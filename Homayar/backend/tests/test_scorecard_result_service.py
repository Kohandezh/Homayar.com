"""HOM-56 PR 2: scoring, storing and verifying results (``services/scorecard_result_service.py``).

The service scores one conversation with the HOM-54 engine from a stored scorecard version
and the meeting's own conversation columns, and stores the result and one row per
criterion.  The caller gives only ids, a real ``CheckRun`` and the clock.  The tests check:

* the stored row is the engine output exactly, the columns copy it, and the denominator
  leaves out a criterion that is not applicable (SPEC AC2, AC3);
* the same input returns the stored row, a new input is a new row, and a concurrent insert
  returns the row that won (AC4);
* every refusal of SPEC section 4 raises its code and writes nothing (AC5);
* ``verify_result`` recomputes a stored row and sees a changed one (AC6), and finds nothing
  after the conversation purge (AC6b);
* no stored key names an NPS, a churn value, a probability, a promoter or a sentiment, and
  a check run given as a dict is refused (AC7);
* the reads, and tenancy through the explicit workspace filter and, on PostgreSQL, through
  row-level security (AC8, AC9).

Check runs are real: ``plan_scorecard``, ``plan_checks`` and ``finish_checks`` with phrase
checks, which need no evaluator.  The sessions are the application's own (``homayar_app``
under row-level security on PostgreSQL), bound to the tenant like a request.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update

from backend.conversation_checks import CheckRun, CheckTurn, finish_checks, plan_checks
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    CriterionResultRecord,
    Meeting,
    PurgeState,
    ScorecardResultRecord,
    ScorecardVersion,
    TranscriptRevision,
    TranscriptSource,
)
from backend.database.tenant_context import bind_tenant_context
from backend.scorecards import (
    SCORECARDS_VERSION,
    ConversationFacts,
    ScorecardError,
    definition_digest,
    plan_scorecard,
    score_conversation,
    validate_definition,
)
from backend.services import scorecard_result_service
from backend.services.scorecard_result_service import (
    ScorecardResultError,
    criterion_rows,
    get_result,
    list_results,
    record_result,
    scoring_input_digest,
    verify_result,
)
from backend.services.scorecard_service import publish_version
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_ingestion_ledger import _headers, _tenant
from backend.tests.test_retention_purge import _purge

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
TURNS = [
    CheckTurn("hello and welcome to the shop", "agent", 0, 3000, 0.9),
    CheckTurn("I want to change my plan", "customer", 3000, 6000, 0.9),
    CheckTurn("thank you, goodbye", "agent", 6000, 9000, 0.9),
]
# The same call, but the agent also explains the fee: another check run.
TURNS_WITH_FEE = [*TURNS, CheckTurn("the cancellation fee is ten", "agent", 9000, 10000, 0.9)]
CRITERION_COLUMNS = (
    "criterion_id",
    "weight",
    "auto_fail",
    "na_allowed",
    "outcome",
    "reason",
    "decided_by",
    "review_required",
    "review_reasons",
    "check_outcome",
    "check_reason",
    "evidence",
    "candidate_evidence",
)
INFERRED_WORDS = ("nps", "churn", "probab", "promoter", "sentiment")


def _criterion(criterion_id: str, weight: int, phrase: str, **extra: Any) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "label": criterion_id.replace("_", " ").capitalize(),
        "weight": weight,
        "check": {
            "check_id": criterion_id,
            "version": 1,
            "kind": "required_phrase",
            "phrases": [phrase],
        },
        **extra,
    }


# On a call: greeting and closing pass, the fee is not explained, and the agenda criterion
# is for meetings only.  So 50 of 75 applicable weight: 66.67 (README, 2 of 3).
DEFINITION = {
    "name": "Inbound sales",
    "pass_threshold": 60,
    "criteria": [
        _criterion("greeting", 25, "hello"),
        _criterion("closing", 25, "goodbye"),
        _criterion("fee_explained", 25, "cancellation fee"),
        _criterion("agenda", 25, "agenda", applies_to={"conversation_types": ["meeting"]}),
    ],
}
# The closing counts on inbound calls only.
DIRECTED = {
    "name": "Inbound only",
    "pass_threshold": 50,
    "criteria": [
        _criterion("greeting", 50, "hello"),
        _criterion("closing", 50, "goodbye", applies_to={"directions": ["inbound"]}),
    ],
}
# The closing counts on calls only.
CALLS_ONLY = {
    "name": "Calls only",
    "pass_threshold": 50,
    "criteria": [
        _criterion("greeting", 50, "hello"),
        _criterion("closing", 50, "goodbye", applies_to={"conversation_types": ["call"]}),
    ],
}


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _run(
    definition: dict[str, Any],
    conversation_type: str = "call",
    direction: str | None = "inbound",
    turns: list[CheckTurn] = TURNS,
) -> CheckRun:
    """A real check run of the criteria that apply to the conversation."""
    checks = plan_scorecard(definition, ConversationFacts(conversation_type, direction))
    return finish_checks(plan_checks(checks, turns, language="en"))


def _session(tenant: dict):
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=tenant["workspace_id"])
    return db


def _conversation(
    tenant: dict, *, conversation_type: str = "call", direction: str | None = "inbound"
) -> dict[str, str]:
    """A meeting of the tenant and its first transcript revision."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="Scored call",
            conversation_type=conversation_type,
            direction=direction,
        )
        db.add(meeting)
        db.flush()
        revision = _revision_row(tenant, meeting.id, 1)
        db.add(revision)
        db.commit()
        return {"meeting_id": meeting.id, "transcript_revision_id": revision.id}


def _revision_row(tenant: dict, meeting_id: str, number: int) -> TranscriptRevision:
    return TranscriptRevision(
        workspace_id=tenant["workspace_id"],
        meeting_id=meeting_id,
        revision_number=number,
        source=TranscriptSource.ASR,
        reason="processing",
        language="en",
        segment_count=len(TURNS),
        is_current=number == 1,
    )


def _revision(tenant: dict, meeting_id: str, number: int) -> str:
    with inspection_session_factory()() as db:
        revision = _revision_row(tenant, meeting_id, number)
        db.add(revision)
        db.commit()
        return revision.id


def _version(tenant: dict, definition: dict[str, Any] = DEFINITION, key: str = "sales") -> str:
    """A version published the normal way."""
    with _session(tenant) as db:
        row = publish_version(
            db,
            workspace_id=tenant["workspace_id"],
            scorecard_key=key,
            definition=definition,
            expected_version=0,
            reason="publish",
            actor_user_id=tenant["user_id"],
            now=NOW,
        )
        db.commit()
        return row.id


def _raw_version(tenant: dict, key: str, **columns: Any) -> str:
    """A version row inserted directly, so it can break the service's own checks."""
    model = validate_definition(DEFINITION)
    values = {
        "workspace_id": tenant["workspace_id"],
        "scorecard_key": key,
        "version": 1,
        "scorecards_version": SCORECARDS_VERSION,
        "document": model.model_dump(mode="json"),
        "digest": definition_digest(model),
        "reason": "inserted directly",
        **columns,
    }
    with inspection_session_factory()() as db:
        row = ScorecardVersion(**values)
        db.add(row)
        db.commit()
        return row.id


def _arguments(tenant: dict, case: dict[str, str], version_id: str, run: Any) -> dict:
    return {
        "workspace_id": tenant["workspace_id"],
        "meeting_id": case["meeting_id"],
        "scorecard_version_id": version_id,
        "check_run": run,
        "transcript_revision_id": case["transcript_revision_id"],
        "now": NOW,
    }


def _keys(value: Any) -> list[str]:
    """Every key of every mapping in ``value``, at any depth."""
    if isinstance(value, dict):
        return [*value, *(key for item in value.values() for key in _keys(item))]
    if isinstance(value, list):
        return [key for item in value for key in _keys(item)]
    return []


def _assert_no_inferred_keys(row: ScorecardResultRecord) -> None:
    """SPEC AC7, for every row the tests store."""
    for document in (row.result, row.check_run):
        keys = [key.lower() for key in _keys(document)]
        assert keys
        assert [key for key in keys if any(word in key for word in INFERRED_WORDS)] == []


def _record(tenant: dict, arguments: dict) -> tuple[str, bool]:
    with _session(tenant) as db:
        row, created = record_result(db, **arguments)
        db.commit()
        _assert_no_inferred_keys(row)
        return row.id, created


def _counts() -> tuple[int, int]:
    with inspection_session_factory()() as db:
        return (
            db.scalar(select(func.count()).select_from(ScorecardResultRecord)),
            db.scalar(select(func.count()).select_from(CriterionResultRecord)),
        )


def _refused(tenant: dict, code: str, arguments: dict) -> None:
    """``record_result`` raises ``code`` and both tables keep their row counts."""
    before = _counts()
    with _session(tenant) as db:
        with pytest.raises(ScorecardResultError) as error:
            record_result(db, **arguments)
        db.rollback()
    assert error.value.code == code
    assert _counts() == before


def _stored(result_id: str) -> tuple[ScorecardResultRecord, list[CriterionResultRecord]]:
    with inspection_session_factory()() as db:
        row = db.get(ScorecardResultRecord, result_id)
        rows = list(
            db.scalars(
                select(CriterionResultRecord)
                .where(CriterionResultRecord.result_id == result_id)
                .order_by(CriterionResultRecord.position)
            )
        )
        return row, rows


def _verify(tenant: dict, result_id: str) -> bool:
    with _session(tenant) as db:
        return verify_result(db, workspace_id=tenant["workspace_id"], result_id=result_id)


def _half_up(part: int, whole: int) -> float:
    exact = Decimal(100 * part) / Decimal(whole)
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --- AC2, AC3: stored exactly, and the denominator ------------------------------------------


def test_a_recorded_result_is_the_engine_output_stored_exactly(client, email_outbox):
    tenant = _tenant(client, email_outbox, "scored-exactly")
    case = _conversation(tenant)
    version_id = _version(tenant)
    run = _run(DEFINITION)
    result_id, created = _record(tenant, _arguments(tenant, case, version_id, run))
    assert created

    row, rows = _stored(result_id)
    with inspection_session_factory()() as db:
        version = db.get(ScorecardVersion, version_id)
        expected = score_conversation(
            version.document, run, ConversationFacts("call", "inbound")
        ).to_json()
        digest = version.digest
    assert row.result == expected
    assert row.check_run == run.to_json()
    assert row.definition_digest == digest == expected["definition_digest"]
    for column in (
        "scorecards_version",
        "checks_version",
        "outcome",
        "reason",
        "complete",
        "pass_threshold",
        "applicable_weight",
        "earned_weight",
        "failed_weight",
        "insufficient_weight",
    ):
        assert getattr(row, column) == expected[column], column
    assert (row.conversation_type, row.direction) == ("call", "inbound")  # the meeting's
    assert (row.workspace_id, row.meeting_id) == (tenant["workspace_id"], case["meeting_id"])
    assert (row.scorecard_version_id, row.transcript_revision_id) == (
        version_id,
        case["transcript_revision_id"],
    )
    assert row.input_digest == scoring_input_digest(
        run, "call", "inbound", case["transcript_revision_id"]
    )

    # One criterion row per criterion, in definition order, each a copy without the label.
    assert [c.position for c in rows] == list(range(len(DEFINITION["criteria"])))
    for stored, criterion in zip(rows, expected["criteria"], strict=True):
        assert {column: getattr(stored, column) for column in CRITERION_COLUMNS} == {
            column: criterion[column] for column in CRITERION_COLUMNS
        }
        assert (stored.workspace_id, stored.meeting_id) == (row.workspace_id, row.meeting_id)
        assert not hasattr(stored, "label")

    # AC3: the agenda criterion is not applicable to a call, so its weight is left out.
    by_id = {c.criterion_id: c for c in rows}
    assert (by_id["agenda"].outcome, by_id["agenda"].reason) == (
        "not_applicable",
        "not_applicable_by_conversation",
    )
    assert [by_id[k].outcome for k in ("greeting", "closing", "fee_explained")] == [
        "passed",
        "passed",
        "failed",
    ]
    assert (row.applicable_weight, row.earned_weight, row.failed_weight) == (75, 50, 25)
    assert row.insufficient_weight == 0 and row.complete
    assert row.result["score"] == _half_up(row.earned_weight, row.applicable_weight) == 66.67
    assert (row.outcome, row.reason) == ("passed", None)  # 5000 >= 60 * 75
    assert _verify(tenant, result_id)


def test_the_input_digest_is_the_canonical_json_of_the_scoring_input():
    run = _run(DEFINITION)
    document = {
        "check_run": run.to_json(),
        "conversation": {"conversation_type": "call", "direction": None},
        "transcript_revision_id": "rev-1",
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert scoring_input_digest(run, "call", None, "rev-1") == expected
    # Each part of the input changes it.
    assert scoring_input_digest(run, "call", "inbound", "rev-1") != expected
    assert scoring_input_digest(run, "call", None, "rev-2") != expected
    assert scoring_input_digest(_run(DEFINITION, turns=TURNS_WITH_FEE), "call", None, "rev-1") != (
        expected
    )


# --- AC4: idempotent ---------------------------------------------------------------------


def test_the_same_input_returns_the_stored_row_and_a_new_input_is_a_new_row(client, email_outbox):
    tenant = _tenant(client, email_outbox, "idempotent")
    case = _conversation(tenant)
    version_id = _version(tenant)
    arguments = _arguments(tenant, case, version_id, _run(DEFINITION))
    first, created = _record(tenant, arguments)
    assert created
    assert _record(tenant, {**arguments, "now": NOW + timedelta(hours=1)}) == (first, False)
    assert _counts() == (1, 4)

    # Allow: another check run, and another revision of the same meeting, are new inputs.
    changed = _run(DEFINITION, turns=TURNS_WITH_FEE)
    second, created = _record(tenant, {**arguments, "check_run": changed})
    assert created and second != first
    revision_id = _revision(tenant, case["meeting_id"], 2)
    third, created = _record(tenant, {**arguments, "transcript_revision_id": revision_id})
    assert created and third not in (first, second)
    assert _counts() == (3, 12)
    assert _stored(second)[0].earned_weight == 75  # the fee is explained in that run


def test_a_concurrent_insert_returns_the_row_that_won(client, email_outbox, monkeypatch):
    tenant = _tenant(client, email_outbox, "race")
    case = _conversation(tenant)
    version_id = _version(tenant)
    arguments = _arguments(tenant, case, version_id, _run(DEFINITION))
    winner, _ = _record(tenant, arguments)

    # This writer looked for the input before the other one committed it.
    real = scorecard_result_service.find_by_input
    calls = []

    def missing_then_real(db, **kwargs):
        calls.append(kwargs)
        return None if len(calls) == 1 else real(db, **kwargs)

    monkeypatch.setattr(scorecard_result_service, "find_by_input", missing_then_real)
    with _session(tenant) as db:
        row, created = record_result(db, **arguments)
        assert (row.id, created) == (winner, False)
        assert len(calls) == 2  # the unique constraint sent it back to the lookup
        # The session is still usable: it reads and writes after the conflict.
        assert get_result(db, workspace_id=tenant["workspace_id"], result_id=winner).id == winner
        other, created = record_result(
            db, **{**arguments, "check_run": _run(DEFINITION, turns=TURNS_WITH_FEE)}
        )
        assert created
        db.commit()
    assert _counts() == (2, 8)


# --- AC5, AC7: refusals write nothing ---------------------------------------------------------


def test_a_check_run_that_is_not_a_check_run_is_refused(client, email_outbox):
    tenant = _tenant(client, email_outbox, "check-run")
    case = _conversation(tenant)
    version_id = _version(tenant)
    run = _run(DEFINITION)
    arguments = _arguments(tenant, case, version_id, run)
    for value in (run.to_json(), {**run.to_json(), "estimated_nps": 9}, None):
        _refused(tenant, "check_run_invalid", {**arguments, "check_run": value})
    assert _record(tenant, arguments)[1]  # allow: the same run as a ``CheckRun``


def test_only_a_valid_version_of_the_workspace_is_used(client, email_outbox):
    tenant = _tenant(client, email_outbox, "version-a")
    other = _tenant(client, email_outbox, "version-b")
    case = _conversation(tenant)
    run = _run(DEFINITION)
    for version_id in ("no-such-version", _version(other)):
        _refused(tenant, "scorecard_version_not_found", _arguments(tenant, case, version_id, run))
    old = _raw_version(tenant, "old_engine", scorecards_version="scorecards/0")
    _refused(tenant, "calculation_version_mismatch", _arguments(tenant, case, old, run))
    changed = _raw_version(tenant, "bad_digest", digest="0" * 64)
    _refused(tenant, "definition_digest_mismatch", _arguments(tenant, case, changed, run))
    # Allow: the workspace's own version, published or inserted with a matching digest.
    assert _record(tenant, _arguments(tenant, case, _version(tenant), run))[1]
    inserted = _raw_version(tenant, "inserted")
    assert _record(tenant, _arguments(tenant, case, inserted, run))[1]


def test_only_a_live_meeting_of_the_workspace_and_its_own_revision_are_used(client, email_outbox):
    tenant = _tenant(client, email_outbox, "meeting-a")
    other = _tenant(client, email_outbox, "meeting-b")
    case = _conversation(tenant)
    elsewhere = _conversation(tenant)  # another meeting of the same workspace
    foreign = _conversation(other)
    version_id = _version(tenant)
    arguments = _arguments(tenant, case, version_id, _run(DEFINITION))

    for meeting_id in ("no-such-meeting", foreign["meeting_id"]):
        _refused(tenant, "meeting_not_found", {**arguments, "meeting_id": meeting_id})
    for revision_id in (elsewhere["transcript_revision_id"], None, 1):
        _refused(
            tenant,
            "transcript_revision_invalid",
            {**arguments, "transcript_revision_id": revision_id},
        )
    assert _record(tenant, arguments)[1]  # allow: the meeting and its own revision


def test_a_tombstoned_meeting_is_refused(client, email_outbox):
    """SPEC Addendum 1, B1: its purge may already have run, so nothing is stored."""
    tenant = _tenant(client, email_outbox, "tombstone")
    case = _conversation(tenant)
    arguments = _arguments(tenant, case, _version(tenant), _run(DEFINITION))
    # Allow: the same meeting before the tombstone.
    assert _record(tenant, arguments)[1]
    assert _counts() == (1, 4)

    with inspection_session_factory()() as db:
        db.get(Meeting, case["meeting_id"]).deleted_at = NOW
        db.commit()
    # The same input and a new one are both refused, and nothing is written.
    _refused(tenant, "meeting_not_found", arguments)
    changed = {**arguments, "check_run": _run(DEFINITION, turns=TURNS_WITH_FEE)}
    _refused(tenant, "meeting_not_found", changed)


def test_engine_errors_propagate_and_write_nothing(client, email_outbox):
    tenant = _tenant(client, email_outbox, "engine")
    case = _conversation(tenant, direction=None)
    version_id = _version(tenant, DIRECTED)
    full = _run(DIRECTED, direction=None)
    before = _counts()

    # A run that misses the result of one criterion that applies.
    short = dataclasses.replace(full, results=full.results[:-1])
    # A run planned for an inbound call, while the meeting's direction is not known: the
    # facts come from the meeting, so the extra closing result does not match the plan.
    planned_inbound = _run(DIRECTED, direction="inbound")
    for run in (short, planned_inbound):
        with _session(tenant) as db:
            with pytest.raises(ScorecardError) as error:
                record_result(db, **_arguments(tenant, case, version_id, run))
            db.rollback()
        assert error.value.code == "check_run_mismatch"
        assert not isinstance(error.value, ScorecardResultError)
    assert _counts() == before
    assert _record(tenant, _arguments(tenant, case, version_id, full))[1]  # allow


# --- the conversation facts come from the meeting ------------------------------------------


def _criterion_row(result_id: str, criterion_id: str) -> CriterionResultRecord:
    return next(row for row in _stored(result_id)[1] if row.criterion_id == criterion_id)


def test_an_unknown_direction_makes_a_directed_criterion_insufficient(client, email_outbox):
    tenant = _tenant(client, email_outbox, "direction")
    version_id = _version(tenant, DIRECTED)
    unknown = _conversation(tenant, direction=None)
    result_id, _ = _record(
        tenant, _arguments(tenant, unknown, version_id, _run(DIRECTED, direction=None))
    )
    closing = _criterion_row(result_id, "closing")
    assert (closing.outcome, closing.reason, closing.decided_by) == (
        "insufficient_evidence",
        "direction_unknown",
        None,
    )
    row = _stored(result_id)[0]
    assert (row.direction, row.complete, row.insufficient_weight) == (None, False, 50)

    # Allow: an inbound meeting, and the closing applies.
    inbound = _conversation(tenant, direction="inbound")
    result_id, _ = _record(tenant, _arguments(tenant, inbound, version_id, _run(DIRECTED)))
    closing = _criterion_row(result_id, "closing")
    assert (closing.outcome, closing.reason, closing.decided_by) == ("passed", None, "rule")


def test_a_voicemail_leaves_a_call_criterion_out_of_the_denominator(client, email_outbox):
    tenant = _tenant(client, email_outbox, "voicemail")
    version_id = _version(tenant, CALLS_ONLY)
    voicemail = _conversation(tenant, conversation_type="voicemail", direction=None)
    result_id, _ = _record(
        tenant,
        _arguments(tenant, voicemail, version_id, _run(CALLS_ONLY, "voicemail", direction=None)),
    )
    closing = _criterion_row(result_id, "closing")
    assert (closing.outcome, closing.reason) == (
        "not_applicable",
        "not_applicable_by_conversation",
    )
    row = _stored(result_id)[0]
    assert (row.conversation_type, row.applicable_weight, row.earned_weight) == (
        "voicemail",
        50,
        50,
    )
    assert row.result["score"] == 100.0

    # Allow: a call, and the closing applies.
    call = _conversation(tenant)
    result_id, _ = _record(tenant, _arguments(tenant, call, version_id, _run(CALLS_ONLY)))
    assert _criterion_row(result_id, "closing").outcome == "passed"
    assert _stored(result_id)[0].applicable_weight == 100


# --- AC6, AC6b: reproducible, and gone after the purge ---------------------------------------


def _recorded(tenant: dict) -> str:
    """One stored result, on its own conversation and its own scorecard key."""
    case = _conversation(tenant)
    version_id = _version(tenant, key=f"sales_{uuid.uuid4().hex[:8]}")
    return _record(tenant, _arguments(tenant, case, version_id, _run(DEFINITION)))[0]


def _tamper(table, *where: Any, **values: Any) -> None:
    """A direct change as the table owner: a Core statement fires no ORM guard."""
    with inspection_session_factory()() as db:
        db.execute(update(table).where(*where).values(**values))
        db.commit()


def test_verify_recomputes_a_stored_row_and_sees_a_change(client, email_outbox):
    tenant = _tenant(client, email_outbox, "verify")
    results = ScorecardResultRecord.__table__
    criteria = CriterionResultRecord.__table__

    untouched = _recorded(tenant)
    assert _verify(tenant, untouched) is True

    changed_result = _recorded(tenant)
    result = _stored(changed_result)[0].result
    _tamper(results, results.c.id == changed_result, result={**result, "score": 99.0})
    assert _verify(tenant, changed_result) is False

    changed_criterion = _recorded(tenant)
    # The fee criterion (position 2) failed; now it says passed.
    _tamper(
        criteria,
        criteria.c.result_id == changed_criterion,
        criteria.c.position == 2,
        outcome="passed",
    )
    assert _verify(tenant, changed_criterion) is False

    old_engine = _recorded(tenant)
    _tamper(results, results.c.id == old_engine, scorecards_version="scorecards/0")
    with _session(tenant) as db:
        with pytest.raises(ScorecardResultError) as error:
            verify_result(db, workspace_id=tenant["workspace_id"], result_id=old_engine)
    assert error.value.code == "calculation_version_mismatch"
    assert _verify(tenant, untouched) is True  # allow: the untouched row still verifies


RESULTS = ScorecardResultRecord.__table__
CRITERIA = CriterionResultRecord.__table__


def _set(**values: Any):
    """Change columns of the result row."""

    def change(tenant: dict, row: ScorecardResultRecord) -> None:
        _tamper(RESULTS, RESULTS.c.id == row.id, **values)

    return change


def _set_criterion(at: int, **values: Any):
    """Change columns of the criterion row at position ``at``."""

    def change(tenant: dict, row: ScorecardResultRecord) -> None:
        where = (CRITERIA.c.result_id == row.id, CRITERIA.c.position == at)
        _tamper(CRITERIA, *where, **values)

    return change


def _set_check_run(edit):
    """Replace the stored check run with ``edit(stored check run)``."""

    def change(tenant: dict, row: ScorecardResultRecord) -> None:
        _tamper(RESULTS, RESULTS.c.id == row.id, check_run=edit(row.check_run))

    return change


def _drop_last_criterion(tenant: dict, row: ScorecardResultRecord) -> None:
    with inspection_session_factory()() as db:
        db.execute(delete(CRITERIA).where(CRITERIA.c.result_id == row.id, CRITERIA.c.position == 3))
        db.commit()


def _swap_first_two_criteria(tenant: dict, row: ScorecardResultRecord) -> None:
    # Through a free position, because (result_id, position) is unique.
    for old, new in ((0, 50), (1, 0), (50, 1)):
        _set_criterion(old, position=new)(tenant, row)


def _other_revision(tenant: dict, row: ScorecardResultRecord) -> None:
    revision_id = _revision(tenant, row.meeting_id, 2)
    _tamper(RESULTS, RESULTS.c.id == row.id, transcript_revision_id=revision_id)


# SPEC Addendum 1, B2.  Each change is a direct SQL statement as the table owner.  The
# result JSON stays as stored unless the case names it.
TAMPERS = {
    # The weights check keeps the sum, so the failed weight moves with the earned weight.
    "earned_weight": _set(earned_weight=25, failed_weight=50),
    "outcome_and_reason": _set(outcome="failed", reason="below_threshold"),
    "complete": _set(complete=False),
    "pass_threshold": _set(pass_threshold=61),
    "checks_version": _set(checks_version="conversation-checks/0"),
    "definition_digest": _set(definition_digest="f" * 64),
    "input_digest": _set(input_digest="e" * 64),
    "direction": _set(direction="outbound"),
    # The agenda criterion applies to a meeting, and the stored run has no result for it:
    # the engine refuses the stored input.
    "conversation_type": _set(conversation_type="meeting"),
    "transcript_revision": _other_revision,
    "criterion_weight": _set_criterion(0, weight=30),
    "criterion_evidence": _set_criterion(0, evidence=[{"turn_index": 9}]),
    "criterion_missing": _drop_last_criterion,
    "criterion_gap": _set_criterion(3, position=7),
    "criterion_order": _swap_first_two_criteria,
    # The engine refuses these stored runs; verify gives False and does not raise.
    "check_run_short": _set_check_run(lambda run: {**run, "results": run["results"][:-1]}),
    "check_run_not_a_run": _set_check_run(lambda run: "not a check run"),
    # A change the engine may accept: the input digest no longer matches.
    "check_run_language": _set_check_run(lambda run: {**run, "language": "fa"}),
}


@pytest.mark.parametrize("change", list(TAMPERS.values()), ids=list(TAMPERS))
def test_verify_is_false_for_any_changed_copy_and_never_raises(client, email_outbox, change):
    tenant = _tenant(client, email_outbox, "verify-copy")
    result_id = _recorded(tenant)
    assert _verify(tenant, result_id) is True  # allow: untouched
    change(tenant, _stored(result_id)[0])
    assert _verify(tenant, result_id) is False


def test_verify_and_get_find_only_the_workspace_rows(client, email_outbox):
    tenant = _tenant(client, email_outbox, "verify-a")
    other = _tenant(client, email_outbox, "verify-b")
    own = _recorded(tenant)
    theirs = _recorded(other)
    for result_id in ("no-such-result", theirs):
        with _session(tenant) as db:
            assert get_result(db, workspace_id=tenant["workspace_id"], result_id=result_id) is None
            with pytest.raises(ScorecardResultError) as error:
                verify_result(db, workspace_id=tenant["workspace_id"], result_id=result_id)
        assert error.value.code == "result_not_found"
    with _session(tenant) as db:
        assert get_result(db, workspace_id=tenant["workspace_id"], result_id=own).id == own
    assert _verify(tenant, own) is True


def test_after_the_conversation_purge_nothing_is_found(client, email_outbox):
    tenant = _tenant(client, email_outbox, "purge")
    case = _conversation(tenant)
    kept = _conversation(tenant)
    version_id = _version(tenant)
    purged_id, _ = _record(tenant, _arguments(tenant, case, version_id, _run(DEFINITION)))
    kept_id, _ = _record(tenant, _arguments(tenant, kept, version_id, _run(DEFINITION)))

    deleted = client.delete(
        f"/api/v1/meetings/{case['meeting_id']}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert deleted.status_code == 204, deleted.text
    assert _purge(case["meeting_id"]).state is PurgeState.COMPLETED

    workspace_id = tenant["workspace_id"]
    with _session(tenant) as db:
        assert get_result(db, workspace_id=workspace_id, result_id=purged_id) is None
        assert list_results(db, workspace_id=workspace_id, meeting_id=case["meeting_id"]) == []
        assert criterion_rows(db, workspace_id=workspace_id, result_id=purged_id) == []
        with pytest.raises(ScorecardResultError) as error:
            verify_result(db, workspace_id=workspace_id, result_id=purged_id)
        assert error.value.code == "result_not_found"
        # Allow: another conversation of the workspace keeps its result.
        assert [
            r.id for r in list_results(db, workspace_id=workspace_id, meeting_id=kept["meeting_id"])
        ] == [kept_id]
    assert _verify(tenant, kept_id) is True


# --- AC8, AC9: reads and tenancy ---------------------------------------------------------------


def test_list_results_is_newest_first_and_criterion_rows_are_by_position(client, email_outbox):
    tenant = _tenant(client, email_outbox, "reads")
    case = _conversation(tenant)
    version_id = _version(tenant)
    arguments = _arguments(tenant, case, version_id, _run(DEFINITION))
    oldest, _ = _record(tenant, arguments)
    later = NOW + timedelta(minutes=1)
    tied = [
        _record(
            tenant,
            {
                **arguments,
                "transcript_revision_id": _revision(tenant, case["meeting_id"], number),
                "now": later,
            },
        )[0]
        for number in (2, 3)
    ]
    workspace_id = tenant["workspace_id"]
    with _session(tenant) as db:
        listed = [
            row.id
            for row in list_results(db, workspace_id=workspace_id, meeting_id=case["meeting_id"])
        ]
        assert listed == [*sorted(tied, reverse=True), oldest]  # created_at desc, then id desc
        assert list_results(db, workspace_id=workspace_id, meeting_id="no-such-meeting") == []
        positions = [
            row.position for row in criterion_rows(db, workspace_id=workspace_id, result_id=oldest)
        ]
        assert positions == [0, 1, 2, 3]
        assert criterion_rows(db, workspace_id=workspace_id, result_id="no-such-result") == []


def _assert_b_sees_nothing_of_a(db, b: dict, a_case: dict, a_id: str) -> None:
    workspace_id = b["workspace_id"]
    assert get_result(db, workspace_id=workspace_id, result_id=a_id) is None
    assert list_results(db, workspace_id=workspace_id, meeting_id=a_case["meeting_id"]) == []
    assert criterion_rows(db, workspace_id=workspace_id, result_id=a_id) == []


def _tenancy_setup(client, email_outbox, label: str):
    a = _tenant(client, email_outbox, f"{label}-a")
    b = _tenant(client, email_outbox, f"{label}-b")
    a_case = _conversation(a)
    a_version = _version(a)
    a_id, _ = _record(a, _arguments(a, a_case, a_version, _run(DEFINITION)))
    b_case = _conversation(b)
    b_version = _version(b)
    return a, b, a_case, a_version, a_id, b_case, b_version


def _assert_b_cannot_use_a(b: dict, a_case: dict, a_version: str, b_case: dict, b_version: str):
    run = _run(DEFINITION)
    _refused(b, "scorecard_version_not_found", _arguments(b, b_case, a_version, run))
    _refused(b, "meeting_not_found", _arguments(b, a_case, b_version, run))


def test_another_workspace_reads_and_uses_nothing_through_the_workspace_filter(
    client, email_outbox
):
    a, b, a_case, a_version, a_id, b_case, b_version = _tenancy_setup(
        client, email_outbox, "filter"
    )
    with _session(b) as db:
        _assert_b_sees_nothing_of_a(db, b, a_case, a_id)
    _assert_b_cannot_use_a(b, a_case, a_version, b_case, b_version)
    assert _record(b, _arguments(b, b_case, b_version, _run(DEFINITION)))[1]  # allow


@pytest.mark.postgres
def test_row_level_security_hides_another_workspace_from_the_service(client, email_outbox):
    _requires_postgres()
    a, b, a_case, a_version, a_id, b_case, b_version = _tenancy_setup(client, email_outbox, "rls")
    with _session(b) as db:
        _assert_b_sees_nothing_of_a(db, b, a_case, a_id)
        # Bound to B, even A's own workspace id finds nothing: the policy hides the rows.
        workspace_id = a["workspace_id"]
        assert get_result(db, workspace_id=workspace_id, result_id=a_id) is None
        assert list_results(db, workspace_id=workspace_id, meeting_id=a_case["meeting_id"]) == []
        assert criterion_rows(db, workspace_id=workspace_id, result_id=a_id) == []
        with pytest.raises(ScorecardResultError) as error:
            verify_result(db, workspace_id=workspace_id, result_id=a_id)
        assert error.value.code == "result_not_found"
    _assert_b_cannot_use_a(b, a_case, a_version, b_case, b_version)
    with _session(a) as db:  # allow: bound to A, the row is there
        assert get_result(db, workspace_id=a["workspace_id"], result_id=a_id).id == a_id
