"""HOM-53 part 2: the ``analysis_extractions`` table and its one migration.

The migration creates the table (tenant-only, forced row-level security) and lets
``evidence_spans`` hold ``extraction`` citations; its downgrade removes those spans,
restores the old check and drops the table.  A row is immutable through the ORM,
goes with its meeting and its run, is removed by the retention purge, is counted
by the backup reconciliation and is found by the subject export.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text, true
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.backup.reconcile import CONTENT_TABLES as RECONCILED_TABLES
from backend.config import Settings
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APPEND_ONLY_TABLES,
    SHARED_CONTENT_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    AnalysisExtraction,
    AnalysisRun,
    EvidenceSpan,
    Meeting,
    MeetingStatus,
    ShareAudience,
    ShareLink,
    StageState,
    TranscriptImmutableError,
    TranscriptRevision,
    TranscriptSource,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.privacy.subject_export import build_subject_export, parse_subject_identifier
from backend.services.conversation_purge_service import _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_analysis_extraction_stage import (
    MIXED,
    _extraction,
    _fixed,
    _process,
    _rows,
    _stated,
    _value,
)
from backend.tests.test_privacy_routing import NID_FA, SEGMENTS, _SegmentedASR

NEW_REVISION = "c7e2a9d4f1b6"
BASE_REVISION = "e6c3a9f1d4b8"
HEAD_REVISION = "6d4ec2d2e498"  # HOM-58: conversation columns on meetings, after HOM-37
TABLE = "analysis_extractions"


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _span_types(meeting_id: str) -> dict[str, int]:
    with inspection_session_factory()() as db:
        rows = db.execute(
            text(
                "SELECT finding_type, count(*) FROM evidence_spans WHERE meeting_id = :m "
                "GROUP BY finding_type"
            ),
            {"m": meeting_id},
        ).all()
    return dict(rows)


def _copy_span(meeting_id: str, finding_type: str) -> None:
    """Insert a copy of one of the meeting's spans with ``finding_type`` (as the DBA)."""
    with inspection_session_factory()() as db:
        db.execute(
            text(
                "INSERT INTO evidence_spans (id, workspace_id, meeting_id, analysis_run_id, "
                "transcript_revision_id, segment_id, speaker_key, finding_type, finding_id, "
                "finding_index, text_start, text_end, start_ms, end_ms, confidence, "
                "evidence_type, created_at) "
                "SELECT md5(random()::text), workspace_id, meeting_id, analysis_run_id, "
                "transcript_revision_id, segment_id, speaker_key, :type, NULL, 0, text_start, "
                "text_end, start_ms, end_ms, NULL, evidence_type, now() "
                "FROM evidence_spans WHERE meeting_id = :m LIMIT 1"
            ),
            {"m": meeting_id, "type": finding_type},
        )
        db.commit()


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _rls_flags() -> tuple[bool, bool] | None:
    with inspection_session_factory()() as db:
        row = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t AND relkind = 'r'"
            ),
            {"t": TABLE},
        ).first()
    return (row[0], row[1]) if row is not None else None


# --- item 1: the migration round trip with a row present -----------------------------


@pytest.mark.postgres
def test_the_migration_round_trips_with_a_row_present(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="mig"
    )
    assert len(_rows(meeting_id)) == 1
    before = _span_types(meeting_id)
    assert before["extraction"] == 6 and before["summary"] == 2

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _scalar("SELECT to_regclass('public.analysis_extractions')") is None
        # The extraction spans are gone; every other span stays.
        after = _span_types(meeting_id)
        assert "extraction" not in after
        assert after == {key: count for key, count in before.items() if key != "extraction"}
        # The old check is back: an extraction span is refused, a summary span is not.
        with pytest.raises(IntegrityError, match="ck_evidence_finding"):
            _copy_span(meeting_id, "extraction")
        _copy_span(meeting_id, "summary")

        command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert _scalar("SELECT to_regclass('public.analysis_extractions')") is not None
        assert _rls_flags() == (True, True)
        _copy_span(meeting_id, "extraction")
        assert _span_types(meeting_id)["extraction"] == 1
    finally:
        reset_settings_cache()


# --- item 3: row-level security ------------------------------------------------------


def test_the_table_is_tenant_only_and_not_append_only():
    assert TABLE in WORKSPACE_TABLES
    assert TABLE not in SHARED_CONTENT_TABLES  # never through a share token
    assert TABLE not in APPEND_ONLY_TABLES  # the purge must delete rows


@pytest.mark.postgres
def test_row_level_security_is_enabled_and_forced(client):
    _requires_postgres()
    assert _rls_flags() == (True, True)
    with inspection_session_factory()() as db:
        policies = db.execute(
            text("SELECT qual, with_check FROM pg_policies WHERE tablename = :t"), {"t": TABLE}
        ).all()
    assert len(policies) == 1
    for expression in policies[0]:
        assert "homayar.workspace_id" in expression and "share" not in expression


def _second_run(meeting_id: str) -> str:
    """A completed, non-current run of the meeting with no extraction (as the DBA)."""
    with inspection_session_factory()() as db:
        current = db.scalar(
            select(AnalysisRun).where(AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current)
        )
        run = AnalysisRun(
            workspace_id=current.workspace_id,
            meeting_id=meeting_id,
            transcript_revision_id=current.transcript_revision_id,
            state=StageState.COMPLETED,
            is_current=False,
        )
        db.add(run)
        db.commit()
        return run.id


def _copy_of(
    row: AnalysisExtraction, run_id: str, meeting_id: str | None = None
) -> AnalysisExtraction:
    return AnalysisExtraction(
        workspace_id=row.workspace_id,
        meeting_id=meeting_id or row.meeting_id,
        analysis_run_id=run_id,
        taxonomy_id=row.taxonomy_id,
        taxonomy_version=row.taxonomy_version,
        taxonomy_digest=row.taxonomy_digest,
        validator_version=row.validator_version,
        policy_version=row.policy_version,
        policy_digest=row.policy_digest,
        valid=row.valid,
        needs_review=row.needs_review,
        review_count=row.review_count,
        result=row.result,
    )


@pytest.mark.postgres
def test_a_second_workspace_cannot_read_or_insert_the_rows(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    one, first = _process(client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="rls-1")
    two, second = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="rls-2"
    )
    (row_one,) = _rows(first)
    (row_two,) = _rows(second)
    extra_run = _second_run(first)
    factory = get_session_factory()

    with factory() as db:
        bind_tenant_context(db, workspace_id=two["workspace_id"])
        assert [row.id for row in db.scalars(select(AnalysisExtraction))] == [row_two.id]
        assert db.get(AnalysisExtraction, row_one.id) is None
        db.add(_copy_of(row_one, extra_run))
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()

    # Allow-control: the owner reads its own row, and the same insert passes.
    with factory() as db:
        bind_tenant_context(db, workspace_id=one["workspace_id"])
        assert [row.id for row in db.scalars(select(AnalysisExtraction))] == [row_one.id]
        db.add(_copy_of(row_one, extra_run))
        db.commit()
    assert len(_rows(first)) == 2


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="share"
    )
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                created_by_user_id=tenant["user_id"],
                token_hash="ab" * 32,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash="ab" * 32)
        # Control: the shared meeting's evidence is readable through the token.
        assert db.scalars(select(EvidenceSpan.id)).all()
        assert db.scalars(select(AnalysisExtraction.id)).all() == []
        db.rollback()


# --- item 4: immutability --------------------------------------------------------------


def _changed(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, datetime):
        return value + timedelta(seconds=1)
    if isinstance(value, dict):
        return {**value, "valid": not value.get("valid")}
    return f"{value}-changed"


def test_an_orm_update_of_any_column_raises(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="immutable"
    )
    (row,) = _rows(meeting_id)
    columns = [attribute.key for attribute in inspect(AnalysisExtraction).column_attrs]
    assert len(columns) == 15
    factory = get_session_factory()
    for column in columns:
        with factory() as db:
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            stored = db.get(AnalysisExtraction, row.id)
            setattr(stored, column, _changed(getattr(stored, column)))
            with pytest.raises(TranscriptImmutableError, match=column):
                db.flush()
            db.rollback()
    (unchanged,) = _rows(meeting_id)
    assert {c: getattr(unchanged, c) for c in columns} == {c: getattr(row, c) for c in columns}

    # Allow-control: insert and delete work.
    extra_run = _second_run(meeting_id)
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        db.add(_copy_of(row, extra_run))
        db.commit()
    assert len(_rows(meeting_id)) == 2
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        db.delete(db.get(AnalysisExtraction, row.id))
        db.commit()
    assert [kept.analysis_run_id for kept in _rows(meeting_id)] == [extra_run]


# --- item 5: cascade, purge, reconciliation, subject export ----------------------------


def _bare_meeting_with_a_row(tenant: dict, template: AnalysisExtraction) -> str:
    """A meeting with one revision, one run and one row, and nothing else that
    refers to it (a processed meeting also has usage rows), as the DBA."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="cascade",
            status=MeetingStatus.COMPLETED,
            duration_seconds=5,
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="fa",
            segment_count=0,
            is_current=True,
        )
        db.add(revision)
        db.flush()
        run = AnalysisRun(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting.id,
            transcript_revision_id=revision.id,
            state=StageState.COMPLETED,
            is_current=True,
        )
        db.add(run)
        db.flush()
        db.add(_copy_of(template, run.id, meeting.id))
        db.commit()
        return meeting.id


@pytest.mark.postgres
def test_rows_go_with_their_run_and_their_meeting(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    tenant, by_run = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="c-1"
    )
    _, control = _process(client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="c-2")
    (row,) = _rows(by_run)
    by_meeting = _bare_meeting_with_a_row(tenant, row)
    assert len(_rows(by_meeting)) == 1

    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM analysis_runs WHERE id = :r"), {"r": row.analysis_run_id})
        db.commit()
    assert _rows(by_run) == []
    assert len(_rows(by_meeting)) == len(_rows(control)) == 1

    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM meetings WHERE id = :m"), {"m": by_meeting})
        db.commit()
    assert _rows(by_meeting) == []
    assert len(_rows(control)) == 1  # the other meeting's row stays


def test_the_retention_purge_removes_the_rows(client, email_outbox, monkeypatch, settings):
    tenant, purged = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="purge"
    )
    _, kept = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="purge-control"
    )
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        meeting = db.get(Meeting, purged)
        assert _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting) > 0
        db.commit()
    assert _rows(purged) == []
    assert len(_rows(kept)) == 1


def test_the_backup_reconciliation_counts_the_table():
    assert TABLE in RECONCILED_TABLES


EMAIL = "ali.rezaei@example.com"
SPOKEN_PHONE = "۰۹۱۲۳۴۵۶۷۸۹"  # as SEGMENTS[1] says it
SUBJECT = _extraction(
    entity=_stated(
        _value("national_id", [0], 0.9, text=NID_FA),
        _value("email", [1], 0.2, text=EMAIL),  # withheld, still stored
        _value("phone", [1], 0.9, text=SPOKEN_PHONE),
    )
)


def _subject_matches(workspace_id: str, identifier: str) -> list[dict]:
    with inspection_session_factory()() as db:
        document, _audit = build_subject_export(
            db,
            workspace_id=workspace_id,
            identifiers=[parse_subject_identifier(identifier)],
            meeting_clause=true(),
            now=datetime.now(UTC),
        )
    return document["matches"]


def test_the_subject_export_returns_matching_entity_values(
    client, email_outbox, monkeypatch, settings
):
    assert EMAIL in SEGMENTS[1] and SPOKEN_PHONE in SEGMENTS[1]
    tenant, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(SUBJECT),
        label="subject",
        asr=_SegmentedASR(),
    )
    (row,) = _rows(meeting_id)
    entity = row.result["fields"]["entity"]
    assert [v["label"] for v in entity["values"]] == ["national_id", "phone"]
    assert [v["label"] for v in entity["withheld"]] == ["email"]
    run_id = row.analysis_run_id

    (match,) = _subject_matches(tenant["workspace_id"], "09123456789")
    assert match["extraction"] == [
        {"analysis_run_id": run_id, "label": "phone", "text": SPOKEN_PHONE}
    ]
    (match,) = _subject_matches(tenant["workspace_id"], EMAIL)
    assert match["extraction"] == [{"analysis_run_id": run_id, "label": "email", "text": EMAIL}]
    assert _subject_matches(tenant["workspace_id"], "09121112233") == []


def test_the_subject_export_has_no_extraction_key_without_a_hit(
    client, email_outbox, monkeypatch, settings
):
    tenant, _ = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(None),
        label="subject-legacy",
        asr=_SegmentedASR(),
    )
    (match,) = _subject_matches(tenant["workspace_id"], "09123456789")
    assert match["revisions"]  # the transcript matches
    assert "extraction" not in match


# --- item 6: the evidence finding check --------------------------------------------------


@pytest.mark.postgres
def test_evidence_spans_accept_extraction_and_refuse_an_unknown_finding(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(_extraction()), label="finding"
    )
    before = _span_types(meeting_id)
    _copy_span(meeting_id, "extraction")
    assert _span_types(meeting_id)["extraction"] == before.get("extraction", 0) + 1
    with pytest.raises(IntegrityError, match="ck_evidence_finding"):
        _copy_span(meeting_id, "bogus")
    assert "bogus" not in _span_types(meeting_id)
