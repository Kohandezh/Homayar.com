"""HOM-290 WU2: the stored trend judgment of each analysis run, written and backfilled.

``content_key`` is the hex SHA-256 of the canonical JSON of a run's ``JudgedContent``.
``write_judgment`` judges a stored extraction with ``SignatureJudge.content`` (the only rules
source) and adds its row for the current definition digest and ``JUDGMENT_VERSION``; the
analysis and the HOM-63 recompute write it in the same transaction as the extraction row, and
a failure to judge never fails the analysis.  ``backfill_judgments`` writes the missing rows of
a workspace's current runs: again it writes nothing, and a row written meanwhile by someone
else is a skip, not an error.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import logging
from contextlib import redirect_stdout
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, true

from backend.database.relational import get_session_factory
from backend.database.saas_models import AnalysisExtraction, TrendExtractionJudgment
from backend.database.tenant_context import bind_tenant_context
from backend.issue_trends import validate_filters
from backend.issue_trends.counting import JUDGMENT_VERSION, SignatureJudge
from backend.issue_trends.definition import TrendDefinition
from backend.jobs import processor, recompute
from backend.services import issue_trend_service
from backend.services import trend_judgment_store as store
from backend.services.issue_trend_service import trend_definition
from backend.tests._database import inspection_session_factory
from backend.tests.test_analysis_extraction_stage import (
    MIXED,
    STEADY,
    _current_run,
    _extraction,
    _fixed,
    _process,
    _recompute_ready,
    _rows,
    _stated,
    _value,
    analysis_provider,  # noqa: F401 - the recompute's provider fixture
)
from backend.tests.test_trend_judgment_cache_judge import _corpus

J = TrendExtractionJudgment


@pytest.fixture
def settings(settings):
    """As in the extraction stage tests: mock providers for the recompute, and no recompute
    on a correction (the recompute test calls it)."""
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
            "recompute_on_correction": False,
        }
    )


def _plain(value: object) -> object:
    """The plain JSON of a dataclass value: enum values as strings, lists in order."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _expected_key(content) -> str:
    document = json.dumps(
        _plain(dataclasses.asdict(content)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(document.encode("ascii")).hexdigest()


def _judgments(**where: str) -> list[TrendExtractionJudgment]:
    with inspection_session_factory()() as db:
        statement = select(J).order_by(J.created_at, J.id)
        for column, value in where.items():
            statement = statement.where(getattr(J, column) == value)
        return list(db.scalars(statement))


def _drop_judgments(workspace_id: str) -> None:
    """Remove the workspace's judgment rows (as the DBA): the state before the backfill."""
    with inspection_session_factory()() as db:
        db.execute(
            text("DELETE FROM trend_extraction_judgments WHERE workspace_id = :w"),
            {"w": workspace_id},
        )
        db.commit()


def _other_definition(definition: TrendDefinition) -> TrendDefinition:
    """The same rules under another digest (only the notes differ)."""
    document = definition.model_dump(mode="json", by_alias=True)
    document["notes"] = f"{document.get('notes') or ''} (HOM-290 test)"
    other = TrendDefinition.model_validate(document)
    assert other.digest != definition.digest
    return other


# --- content_key ------------------------------------------------------------------------------


def test_the_content_key_is_the_sha256_of_the_canonical_json_of_the_content():
    judge = SignatureJudge(trend_definition())
    contents = [judge.content(row) for row in _corpus(count=60)]
    keys = [store.content_key(content) for content in contents]
    for content, key in zip(contents, keys, strict=True):
        assert key == _expected_key(content)
        assert len(key) == 64 and key == key.lower() and int(key, 16) >= 0
    # Equal contents give equal keys, different contents different keys.
    by_key: dict[str, object] = {}
    for content, key in zip(contents, keys, strict=True):
        assert by_key.setdefault(key, content) == content
    assert len(by_key) == len(set(contents)) > 5


def test_the_content_key_keeps_the_issue_order():
    judge = SignatureJudge(trend_definition())
    content = next(c for c in map(judge.content, _corpus(count=60)) if len(set(c.issues)) > 1)
    swapped = dataclasses.replace(content, issues=tuple(reversed(content.issues)))
    assert store.content_key(swapped) != store.content_key(content)


# --- one definition -------------------------------------------------------------------------


class _Stop(Exception):
    """Raised by the stand-in for ``current_judgments``: no statement is needed."""


def test_the_writer_and_the_read_path_use_one_definition(monkeypatch):
    """The digest the writer and the backfill store is the digest the read path filters on:
    both follow ``issue_trend_service.trend_definition``, the one pin.  No database."""
    other = _other_definition(trend_definition())
    monkeypatch.setattr(issue_trend_service, "trend_definition", lambda: other)
    filtered: list[str] = []

    def current_judgments(workspace_id, definition):
        filtered.append(definition.digest)
        raise _Stop

    monkeypatch.setattr(issue_trend_service, "current_judgments", current_judgments)
    window = {"from": "2026-10-01T00:00:00+00:00", "to": "2026-10-08T00:00:00+00:00"}
    filters = validate_filters(window, other, now=datetime(2026, 10, 9, tzinfo=UTC))
    with pytest.raises(_Stop):
        issue_trend_service.judge_population(None, workspace_id="w", scope=true(), filters=filters)
    assert filtered == [other.digest]  # the read path

    extraction = SimpleNamespace(
        id="e", workspace_id="w", analysis_run_id="r", result=_corpus(count=1)[0]
    )
    definition = store._definition(None)  # write_judgment and backfill_judgments
    stored = store._values(extraction, definition, SignatureJudge(definition))
    assert stored["definition_digest"] == other.digest
    assert stored["definition_id"] == other.definition_id


# --- write_judgment -----------------------------------------------------------------------------


def _extraction_of(meeting_id: str) -> AnalysisExtraction:
    (row,) = _rows(meeting_id)
    return row


def test_write_judgment_adds_the_row_of_the_current_rules_once(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="store-write"
    )
    workspace_id = tenant["workspace_id"]
    _drop_judgments(workspace_id)
    extraction = _extraction_of(meeting_id)
    definition = trend_definition()
    factory = get_session_factory()

    with factory() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        row = store.write_judgment(db, extraction=db.get(AnalysisExtraction, extraction.id))
        assert row is not None
        db.commit()
        written = (row.id, row.content_key)
    (stored,) = _judgments(workspace_id=workspace_id)
    assert (stored.id, stored.content_key) == written
    assert (
        stored.extraction_id,
        stored.analysis_run_id,
        stored.definition_id,
        stored.definition_version,
        stored.definition_digest,
        stored.judgment_version,
    ) == (
        extraction.id,
        extraction.analysis_run_id,
        definition.definition_id,
        definition.version,
        definition.digest,
        JUDGMENT_VERSION,
    )
    expected = SignatureJudge(definition).content(extraction.result)
    assert stored.content_key == store.content_key(expected)

    # Again: the row exists, so nothing is added.
    with factory() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        existing = store.write_judgment(db, extraction=db.get(AnalysisExtraction, extraction.id))
        assert existing is None
        db.commit()
    assert [r.id for r in _judgments(workspace_id=workspace_id)] == [stored.id]

    # Allow-control: another definition is another row of the same run.
    other = _other_definition(definition)
    with factory() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        again = store.write_judgment(
            db, extraction=db.get(AnalysisExtraction, extraction.id), definition=other
        )
        assert again is not None
        db.commit()
    digests = sorted(r.definition_digest for r in _judgments(workspace_id=workspace_id))
    assert digests == sorted([definition.digest, other.digest])


# --- the writer on analysis and on recompute ------------------------------------------------------


def test_the_analysis_writes_the_judgment_with_its_extraction(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="store-analysis"
    )
    extraction = _extraction_of(meeting_id)
    (row,) = _judgments(analysis_run_id=extraction.analysis_run_id)
    definition = trend_definition()
    assert (row.workspace_id, row.extraction_id) == (tenant["workspace_id"], extraction.id)
    assert (row.definition_digest, row.judgment_version) == (definition.digest, JUDGMENT_VERSION)
    assert row.content_key == store.content_key(
        SignatureJudge(definition).content(extraction.result)
    )


def test_an_analysis_without_an_extraction_writes_no_judgment(
    client, email_outbox, monkeypatch, settings
):
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(None), label="store-legacy"
    )
    assert _rows(meeting_id) == []
    assert _judgments(analysis_run_id=_current_run(meeting_id).id) == []


def test_a_recompute_writes_the_judgment_of_its_new_run(
    client,
    email_outbox,
    monkeypatch,
    settings,
    analysis_provider,  # noqa: F811
):
    workspace_id, meeting_id, corrected = _recompute_ready(
        client, email_outbox, monkeypatch, settings
    )
    first_run = _current_run(meeting_id).id
    (first,) = _judgments(analysis_run_id=first_run)

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "completed"
    run = _current_run(meeting_id)
    assert run.id != first_run
    (row,) = _judgments(analysis_run_id=run.id)
    (_old, recomputed) = _rows(meeting_id)
    assert row.extraction_id == recomputed.id
    # The same extraction (STEADY), so the same judged content and key; the old row stays.
    assert row.content_key == first.content_key
    assert [r.id for r in _judgments(analysis_run_id=first_run)] == [first.id]
    assert STEADY  # the recompute's provider answers with STEADY


def _judging_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "TREND_JUDGMENT" in r.getMessage()]


def test_a_failure_to_judge_never_fails_the_analysis(
    client, email_outbox, monkeypatch, settings, caplog
):
    caplog.set_level(logging.WARNING)

    def broken(_db, *, extraction, definition=None):
        raise RuntimeError("cancel_service invoice")  # a message that must not be logged

    monkeypatch.setattr(processor, "write_judgment", broken)
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="store-broken"
    )
    extraction = _extraction_of(meeting_id)  # the analysis completed with its row
    assert _judgments(analysis_run_id=extraction.analysis_run_id) == []
    assert _judging_warnings(caplog) == [
        f"TREND_JUDGMENT_SKIPPED run_id={extraction.analysis_run_id} error=RuntimeError"
    ]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "cancel_service" not in logged and "invoice" not in logged


def test_a_refused_judgment_row_is_rolled_back_alone(
    client, email_outbox, monkeypatch, settings, caplog
):
    """The database refuses the row (here its check): only the row is lost, inside its
    savepoint; the extraction, the run and the rest of the analysis commit."""
    caplog.set_level(logging.WARNING)
    real = store.write_judgment

    def refused(db, *, extraction, definition=None):
        row = real(db, extraction=extraction, definition=definition)
        row.definition_version = 0  # ck_trend_judgment_definition_version (a pending insert)
        return row

    monkeypatch.setattr(processor, "write_judgment", refused)
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="store-refused"
    )
    extraction = _extraction_of(meeting_id)
    assert _judgments(analysis_run_id=extraction.analysis_run_id) == []
    assert _judging_warnings(caplog) == [
        f"TREND_JUDGMENT_SKIPPED run_id={extraction.analysis_run_id} error=IntegrityError"
    ]


# --- the backfill -----------------------------------------------------------------------------


OTHER = _extraction(
    intent=_stated(_value("resolve_billing", [0], 0.9)),
    topic=_stated(_value("invoice", [1], 0.8)),
)


def _backfill(workspace_id: str, batch_size: int = 500) -> int:
    with get_session_factory()() as db:
        return store.backfill_judgments(db, workspace_id=workspace_id, batch_size=batch_size)


def test_the_backfill_writes_the_missing_rows_once(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="backfill"
    )
    workspace_id = tenant["workspace_id"]
    written_by_analysis = {(r.analysis_run_id, r.content_key) for r in _judgments()}
    _drop_judgments(workspace_id)
    # A row of an older definition does not count as the current row.
    extraction = _extraction_of(meeting_id)
    with inspection_session_factory()() as db:
        db.add(
            J(
                workspace_id=workspace_id,
                extraction_id=extraction.id,
                analysis_run_id=extraction.analysis_run_id,
                definition_id="trends",
                definition_version=1,
                definition_digest="0" * 64,
                judgment_version=JUDGMENT_VERSION,
                content_key="1" * 64,
            )
        )
        db.commit()

    assert _backfill(workspace_id, batch_size=1) == 1
    current = _judgments(definition_digest=trend_definition().digest)
    assert {(r.analysis_run_id, r.content_key) for r in current} == written_by_analysis
    assert len(_judgments(workspace_id=workspace_id)) == 2  # the old row stays
    # Again: nothing is missing, nothing is written.
    assert _backfill(workspace_id) == 0
    assert len(_judgments(workspace_id=workspace_id)) == 2


def test_the_backfill_pages_through_many_runs_and_only_current_ones(
    client,
    email_outbox,
    monkeypatch,
    settings,
    analysis_provider,  # noqa: F811
):
    workspace_id, meeting_id, corrected = _recompute_ready(
        client, email_outbox, monkeypatch, settings
    )
    assert (
        recompute.recompute_meeting_analysis(
            meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
        )
        == "completed"
    )
    _drop_judgments(workspace_id)
    current = _current_run(meeting_id).id
    # Two extraction rows (the old run and the current one): only the current run is written.
    assert len(_rows(meeting_id)) == 2
    assert _backfill(workspace_id, batch_size=1) == 1
    assert [r.analysis_run_id for r in _judgments(workspace_id=workspace_id)] == [current]


def test_a_row_written_meanwhile_is_a_skip_not_an_error(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="backfill-race"
    )
    workspace_id = tenant["workspace_id"]
    (analysed,) = _judgments(workspace_id=workspace_id)
    _drop_judgments(workspace_id)
    real = store._insert_missing

    def racing(db, rows):
        # Another writer (the analysis, or a second backfill) commits the same row first.
        with inspection_session_factory()() as other:
            other.add(J(**{**rows[0]}))
            other.commit()
        return real(db, rows)

    monkeypatch.setattr(store, "_insert_missing", racing)
    assert _backfill(workspace_id) == 0  # nothing written by this backfill, no error
    (row,) = _judgments(workspace_id=workspace_id)
    assert row.content_key == analysed.content_key


def test_the_backfill_command_prints_counts_only(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="backfill-cli"
    )
    other, other_meeting = _process(
        client, email_outbox, monkeypatch, settings, _fixed(OTHER), label="backfill-cli-2"
    )
    for workspace_id in (tenant["workspace_id"], other["workspace_id"]):
        _drop_judgments(workspace_id)
    factory = get_session_factory()

    out = io.StringIO()
    with redirect_stdout(out):
        assert (
            store.main(["backfill", "--workspace", tenant["workspace_id"]], session_factory=factory)
            == 0
        )
    assert json.loads(out.getvalue()) == {"workspaces": 1, "written": 1}
    assert len(_judgments(workspace_id=other["workspace_id"])) == 0

    out = io.StringIO()
    with redirect_stdout(out):  # every workspace that has a processing job
        assert store.main(["backfill", "--batch-size", "1"], session_factory=factory) == 0
    assert json.loads(out.getvalue()) == {"workspaces": 2, "written": 1}
    printed = out.getvalue()
    assert "cancel_service" not in printed and other["workspace_id"] not in printed
    assert len(_judgments(workspace_id=other["workspace_id"])) == 1
    assert meeting_id and other_meeting


@pytest.mark.parametrize("size", [0, -1])
def test_the_backfill_refuses_an_empty_batch(size):
    with pytest.raises(ValueError, match="batch_size"):
        store.backfill_judgments(object(), workspace_id="w", batch_size=size)  # no statement
