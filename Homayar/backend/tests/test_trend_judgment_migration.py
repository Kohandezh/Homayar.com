"""HOM-290 WU1: the ``trend_extraction_judgments`` table and its one migration.

One row per analysis run and rules version (the trend definition's digest and
``JUDGMENT_VERSION``): the key of the run's judged extraction content, so the trend
routes can count in SQL.  The row holds ids, versions and a digest only, no personal
data.  The migration creates the table (tenant-only, forced row-level security) and
its downgrade drops it.  A row is immutable through the ORM, is unique per run and
rules version, and goes with its extraction, its run and its workspace.

The rows here use their own ``judgment_version`` (``VERSION``), so they never meet a
row the analysis writes for the real ``JUDGMENT_VERSION``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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
    TrendExtractionJudgment,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.services.conversation_purge_service import _remove_content
from backend.services.issue_trend_service import trend_definition
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_analysis_extraction_migration import (
    _alembic,
    _changed,
    _copy_of,
    _second_run,
)
from backend.tests.test_analysis_extraction_stage import MIXED, _fixed, _process, _rows
from backend.tests.test_ingestion_ledger import _tenant

NEW_REVISION = "f2c6a8e4b1d7"
BASE_REVISION = "a7c3e9f5b2d1"  # HOM-37: media_objects.channel_inspection
HEAD_REVISION = NEW_REVISION
TABLE = "trend_extraction_judgments"
VERSION = "judgment/schema-test"
KEY = "c0" * 32  # a content key: 64 hex (WU2 computes the real one)

# The section 3 contract: column -> (data type, length), every column NOT NULL.
COLUMNS = {
    "id": ("character varying", 36),
    "workspace_id": ("character varying", 36),
    "extraction_id": ("character varying", 36),
    "analysis_run_id": ("character varying", 36),
    "definition_id": ("character varying", 64),
    "definition_version": ("integer", None),
    "definition_digest": ("character varying", 64),
    "judgment_version": ("character varying", 64),
    "content_key": ("character varying", 64),
    "created_at": ("timestamp with time zone", None),
}
CONSTRAINTS = {
    "PRIMARY KEY (id)",
    "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE",
    "FOREIGN KEY (extraction_id) REFERENCES analysis_extractions(id) ON DELETE CASCADE",
    "FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE",
    "UNIQUE (analysis_run_id, definition_digest, judgment_version)",
    "CHECK ((definition_version >= 1))",
}
NAMED_CONSTRAINTS = {
    "uq_trend_judgment_run": "UNIQUE (analysis_run_id, definition_digest, judgment_version)",
    "ck_trend_judgment_definition_version": "CHECK ((definition_version >= 1))",
}
# index -> its key columns; no other index (a later one needs another migration).
INDEXES = {
    f"{TABLE}_pkey": "(id)",
    "uq_trend_judgment_run": "(analysis_run_id, definition_digest, judgment_version)",
    f"ix_{TABLE}_workspace_id": "(workspace_id)",
    f"ix_{TABLE}_extraction_id": "(extraction_id)",
    # A tenant's rows of one rules version as one index-only range (the WU1 prototype).
    "ix_trend_judgment_current": (
        "(workspace_id, definition_digest, judgment_version, analysis_run_id) INCLUDE (content_key)"
    ),
}


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


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


def _assert_contract() -> None:
    """The live table is the section 3 schema exactly, with forced row-level security."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": TABLE},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": f"public.{TABLE}"},
        ).all()
        indexes = db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
            {"t": TABLE},
        ).all()
    assert {name: (kind, length) for name, kind, length, _n, _d in columns} == COLUMNS
    assert {name for name, _k, _l, nullable, _d in columns if nullable != "NO"} == set()
    defaults = {name: default for name, _k, _l, _n, default in columns if default is not None}
    assert defaults == {"created_at": "now()"}
    assert {definition for _name, definition in constraints} == CONSTRAINTS
    named = {name: definition for name, definition in constraints if name in NAMED_CONSTRAINTS}
    assert named == NAMED_CONSTRAINTS
    assert {name: definition.split(" USING btree ")[1] for name, definition in indexes} == INDEXES
    unique = {name for name, definition in indexes if definition.startswith("CREATE UNIQUE")}
    assert unique == {f"{TABLE}_pkey", "uq_trend_judgment_run"}
    assert _rls_flags() == (True, True)


def _judgment(extraction: AnalysisExtraction, **overrides) -> TrendExtractionJudgment:
    """A judgment row of ``extraction`` for the current trend definition and ``VERSION``."""
    definition = trend_definition()
    values = {
        "workspace_id": extraction.workspace_id,
        "extraction_id": extraction.id,
        "analysis_run_id": extraction.analysis_run_id,
        "definition_id": definition.definition_id,
        "definition_version": definition.version,
        "definition_digest": definition.digest,
        "judgment_version": VERSION,
        "content_key": KEY,
    }
    return TrendExtractionJudgment(**{**values, **overrides})


def _add(workspace_id: str, row: TrendExtractionJudgment) -> str:
    """Insert ``row`` as the tenant ``workspace_id`` (the application role)."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        db.add(row)
        db.flush()
        row_id = row.id
        db.commit()
    return row_id


def _ids(**where: str) -> set[str]:
    """The ids of the rows (as the DBA) whose columns equal ``where``."""
    with inspection_session_factory()() as db:
        statement = select(TrendExtractionJudgment.id)
        for column, value in where.items():
            statement = statement.where(getattr(TrendExtractionJudgment, column) == value)
        return set(db.scalars(statement))


def _stored(client, email_outbox, monkeypatch, settings, label: str):
    """A processed meeting, its extraction row and one judgment row of it (as the tenant)."""
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label=f"judgment-{label}"
    )
    (extraction,) = _rows(meeting_id)
    return tenant, extraction, _add(tenant["workspace_id"], _judgment(extraction))


# --- the schema and the migration -------------------------------------------------------


@pytest.mark.postgres
def test_the_table_is_the_contract(client):
    _requires_postgres()
    _assert_contract()
    columns = [attribute.key for attribute in inspect(TrendExtractionJudgment).column_attrs]
    assert columns == list(COLUMNS)  # ids, versions and a digest: no personal data


@pytest.mark.postgres
def test_the_migration_round_trips_with_a_row_present(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION
    tenant, extraction, row_id = _stored(client, email_outbox, monkeypatch, settings, "mig")
    assert _ids() == {row_id}

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _scalar(f"SELECT to_regclass('public.{TABLE}')") is None
        # Only the judgments are lost: the extraction row and its run stay.
        assert [row.id for row in _rows(extraction.meeting_id)] == [extraction.id]

        command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == HEAD_REVISION
        _assert_contract()
        assert _ids() == set()
        # The table takes rows again, through the application role.
        again = _add(tenant["workspace_id"], _judgment(extraction))
        assert _ids(extraction_id=extraction.id) == {again}
    finally:
        reset_settings_cache()


# --- row-level security ------------------------------------------------------------------


def test_the_table_is_tenant_only_and_not_append_only():
    assert TABLE in WORKSPACE_TABLES
    assert TABLE not in SHARED_CONTENT_TABLES  # never through a share token
    assert TABLE not in APPEND_ONLY_TABLES  # cascades and the purge delete rows


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


@pytest.mark.postgres
def test_a_second_workspace_cannot_read_or_insert_the_rows(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    one, extraction_one, row_one = _stored(client, email_outbox, monkeypatch, settings, "rls-1")
    two, _extraction_two, row_two = _stored(client, email_outbox, monkeypatch, settings, "rls-2")
    # Another version of the same run: only row-level security can refuse it (its
    # parents exist and the unique key is free).
    foreign = _judgment(extraction_one, judgment_version=f"{VERSION}-2")
    factory = get_session_factory()

    with factory() as db:
        bind_tenant_context(db, workspace_id=two["workspace_id"])
        seen = {row.id: row.workspace_id for row in db.scalars(select(TrendExtractionJudgment))}
        assert row_two in seen and row_one not in seen
        assert set(seen.values()) == {two["workspace_id"]}
        assert db.get(TrendExtractionJudgment, row_one) is None
        db.add(foreign)
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()
    assert _ids(extraction_id=extraction_one.id) == {row_one}

    # Allow-control: the owner reads its own row, and the same insert passes.
    with factory() as db:
        bind_tenant_context(db, workspace_id=one["workspace_id"])
        seen = {row.id: row.workspace_id for row in db.scalars(select(TrendExtractionJudgment))}
        assert row_one in seen and row_two not in seen
    inserted = _add(one["workspace_id"], _judgment(extraction_one, judgment_version=f"{VERSION}-2"))
    assert _ids(extraction_id=extraction_one.id) == {row_one, inserted}


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    tenant, extraction, row_id = _stored(client, email_outbox, monkeypatch, settings, "share")
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=tenant["workspace_id"],
                meeting_id=extraction.meeting_id,
                created_by_user_id=tenant["user_id"],
                token_hash="cd" * 32,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash="cd" * 32)
        # Control: the shared meeting's evidence is readable through the token.
        assert db.scalars(select(EvidenceSpan.id)).all()
        assert db.scalars(select(TrendExtractionJudgment.id)).all() == []
        assert db.get(TrendExtractionJudgment, row_id) is None
        db.rollback()


# --- immutability, the unique key and the check -----------------------------------------


def test_an_orm_update_of_any_column_raises(client, email_outbox, monkeypatch, settings):
    tenant, extraction, row_id = _stored(client, email_outbox, monkeypatch, settings, "immutable")
    columns = [attribute.key for attribute in inspect(TrendExtractionJudgment).column_attrs]
    assert len(columns) == 10
    factory = get_session_factory()
    with inspection_session_factory()() as db:
        before = {c: getattr(db.get(TrendExtractionJudgment, row_id), c) for c in columns}
    for column in columns:
        with factory() as db:
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            stored = db.get(TrendExtractionJudgment, row_id)
            setattr(stored, column, _changed(getattr(stored, column)))
            with pytest.raises(TranscriptImmutableError, match=column):
                db.flush()
            db.rollback()
    with inspection_session_factory()() as db:
        after = {c: getattr(db.get(TrendExtractionJudgment, row_id), c) for c in columns}
    assert after == before

    # Allow-control: a new row (a new rules version) is an insert, and a delete works.
    newer = _add(tenant["workspace_id"], _judgment(extraction, judgment_version=f"{VERSION}-2"))
    assert _ids(extraction_id=extraction.id) == {row_id, newer}
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        db.delete(db.get(TrendExtractionJudgment, row_id))
        db.commit()
    assert _ids(extraction_id=extraction.id) == {newer}


@pytest.mark.postgres
def test_one_row_per_run_and_rules_version(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    tenant, extraction, row_id = _stored(client, email_outbox, monkeypatch, settings, "unique")
    workspace_id = tenant["workspace_id"]
    with pytest.raises(IntegrityError, match="uq_trend_judgment_run"):
        _add(workspace_id, _judgment(extraction, content_key="d1" * 32))
    assert _ids(analysis_run_id=extraction.analysis_run_id) == {row_id}

    # Allow-control: the same run with another judgment version, or another definition.
    other_version = _add(workspace_id, _judgment(extraction, judgment_version=f"{VERSION}-2"))
    other_digest = _add(
        workspace_id, _judgment(extraction, definition_digest="e2" * 32, definition_version=2)
    )
    assert _ids(analysis_run_id=extraction.analysis_run_id) == {
        row_id,
        other_version,
        other_digest,
    }


@pytest.mark.postgres
def test_the_definition_version_is_at_least_one(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    tenant, extraction, row_id = _stored(client, email_outbox, monkeypatch, settings, "check")
    with pytest.raises(IntegrityError, match="ck_trend_judgment_definition_version"):
        _add(
            tenant["workspace_id"],
            _judgment(extraction, definition_version=0, judgment_version=f"{VERSION}-0"),
        )
    # Allow-control: version 1 (the first version a definition can have) is accepted.
    first = _add(
        tenant["workspace_id"],
        _judgment(extraction, definition_version=1, judgment_version=f"{VERSION}-1"),
    )
    assert _ids(extraction_id=extraction.id) == {row_id, first}


# --- cascades and the retention purge -----------------------------------------------------


def _delete(sql: str, **params: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(text(sql), params)
        db.commit()


def _bare_extraction(tenant: dict, template: AnalysisExtraction) -> AnalysisExtraction:
    """A meeting of ``tenant`` with one revision, one run and a copy of ``template`` in
    ``tenant``'s workspace, and nothing else that refers to it (a processed meeting also
    has usage rows), as the DBA."""
    workspace_id = tenant["workspace_id"]
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=tenant["user_id"],
            title="cascade",
            status=MeetingStatus.COMPLETED,
            duration_seconds=5,
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=workspace_id,
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
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            transcript_revision_id=revision.id,
            state=StageState.COMPLETED,
            is_current=True,
        )
        db.add(run)
        db.flush()
        extraction = _copy_of(template, run.id, meeting.id)
        extraction.workspace_id = workspace_id
        db.add(extraction)
        db.commit()
        meeting_id = meeting.id
    (row,) = _rows(meeting_id)
    return row


@pytest.mark.postgres
def test_rows_go_with_their_extraction_their_run_and_their_workspace(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    tenant, extraction, by_extraction = _stored(client, email_outbox, monkeypatch, settings, "c-1")
    _, control_extraction, control = _stored(client, email_outbox, monkeypatch, settings, "c-2")

    # The run alone: a row of another run of the meeting (the extraction stays).
    other_run = _second_run(extraction.meeting_id)
    by_run = _add(
        tenant["workspace_id"],
        _judgment(extraction, analysis_run_id=other_run, judgment_version=f"{VERSION}-run"),
    )
    _delete("DELETE FROM analysis_runs WHERE id = :r", r=other_run)
    assert _ids(extraction_id=extraction.id) == {by_extraction}
    assert by_run not in _ids()

    # The extraction alone: its run stays.
    _delete("DELETE FROM analysis_extractions WHERE id = :e", e=extraction.id)
    assert _ids(extraction_id=extraction.id) == set()
    assert _scalar("SELECT count(*) FROM analysis_runs WHERE id = :r", r=extraction.analysis_run_id)

    # The workspace: a bare tenant with one meeting, run, extraction and judgment.
    bare = _tenant(client, email_outbox, "judgment-c-3")
    bare_extraction = _bare_extraction(bare, control_extraction)
    by_workspace = _add(bare["workspace_id"], _judgment(bare_extraction))
    _delete("DELETE FROM workspaces WHERE id = :w", w=bare["workspace_id"])
    assert by_workspace not in _ids()

    assert _ids(extraction_id=control_extraction.id) == {control}  # the other meeting's row


@pytest.mark.postgres
def test_the_retention_purge_removes_the_rows(client, email_outbox, monkeypatch, settings):
    _requires_postgres()
    tenant, extraction, purged = _stored(client, email_outbox, monkeypatch, settings, "purge")
    _, control_extraction, kept = _stored(client, email_outbox, monkeypatch, settings, "purge-k")
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        meeting = db.get(Meeting, extraction.meeting_id)
        assert _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting) > 0
        db.commit()
    assert purged not in _ids()
    assert _ids(extraction_id=control_extraction.id) == {kept}
