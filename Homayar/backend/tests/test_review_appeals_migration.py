"""HOM-76 PR 1: agent appeals of review results and their one migration.

Three tables: ``review_appeals`` (one appeal of one completed review item),
``review_appeal_criteria`` (one appealed criterion of one appeal, and later its
decision) and ``review_appeal_results`` (the score after a granted or partially
granted appeal, immutable like ``review_results``).  Nothing writes the tables
yet; PR 2 adds the agent routes, the supervisor routes, the audit events and the
notifications.  The tests check:

* the ORM declares the columns, the named constraints, the unique constraints and
  the indexes of the design, the frozen lists and the check strings, which use no
  regex and never name a user column;
* ``database/rls.py`` lists all three tables as workspace tables, only
  ``review_appeal_results`` in the no-update list, none in the append-only or
  no-delete lists;
* every deny case of the SPEC is refused by its named constraint, and the closest
  allow case passes (SQLite and PostgreSQL): the status list, the reason note
  rules, the revision, the disposition list, the disposition note length, the
  per-status field rules, the appealed outcome list, the criterion decision list,
  the decided outcome list, the reason code list, the note length, the undecided,
  uphold and change rules, the criterion key, and the outcome, reason and number
  rules of the appeal result (the same deny and allow pairs as the human result
  of HOM-74);
* every composite key refuses a cross-scope row and accepts the in-scope one;
* one appeal per review item, one criterion row per appeal and criterion, one
  appeal result per appeal;
* the ORM refuses an update and a delete of an appeal result; the appeal and the
  criterion rows allow both;
* cascades: deleting a review item, a scorecard result, a conversation or a
  workspace removes the appeal rows; deleting a user clears every user column of
  ``review_appeals`` and keeps the row;
* on SQLite, the migration builds the same three tables as the ORM, and its
  downgrade removes them and nothing else;
* on PostgreSQL: the ORM schema (``create_all``) and the migrated schema are the
  same; downgrade from the head removes the three tables and leaves every other
  table as it was; upgrade recreates them; tenant isolation (also on write); a
  share-token context sees none of them; the application role may SELECT, INSERT,
  UPDATE and DELETE the appeal and criterion tables and has no UPDATE on
  ``review_appeal_results``;
* the conversation purge deletes the appeal rows of the purged conversation only,
  before the review decisions, the human results and the review items whose
  cascades would remove them uncounted, counts them, and a legal hold keeps them;
  the backup reconciliation counts the three tables; the code has one migration
  head, this one; ADR 0057 records the design.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.backup.reconcile import CONTENT_TABLES as RECONCILED_TABLES
from backend.config import Settings
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    NO_DELETE_TABLES,
    NO_UPDATE_TABLES,
    POLICIES,
    POLICY_NAME,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    CRITERION_KEY_CHECK,
    REVIEW_APPEAL_CRITERION_DECISIONS,
    REVIEW_APPEAL_DISPOSITIONS,
    REVIEW_APPEAL_MAX_CRITERIA,
    REVIEW_APPEAL_STATUSES,
    REVIEW_APPEAL_WINDOW_DAYS,
    REVIEW_NOTE_MAX_LENGTH,
    REVIEW_OVERRIDE_REASONS,
    REVIEW_REVIEWER_OUTCOMES,
    CriterionResultRecord,
    Meeting,
    PurgeState,
    ReviewAppeal,
    ReviewAppealCriterion,
    ReviewAppealResult,
    ReviewAppealResultImmutableError,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultRecord,
    ScorecardResultRecord,
    ShareAudience,
    ShareLink,
    User,
    Workspace,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.scorecards import REVIEWER_OUTCOMES as ENGINE_REVIEWER_OUTCOMES
from backend.services.conversation_purge_service import ERROR_LEGAL_HOLD, _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_ingestion_ledger import _headers, _patch_media
from backend.tests.test_ingestion_ledger import _tenant as _ledger_tenant
from backend.tests.test_retention_purge import _media, _processed, _purge, _review, _sweep
from backend.tests.test_review_decisions_migration import _decision, _human, _item_values
from backend.tests.test_scorecard_results_migration import (
    RESULT as RESULT_JSON,
)
from backend.tests.test_scorecard_results_migration import (
    _add,
    _criterion,
    _delete_conversation,
    _parents,
    _result,
)

NEW_REVISION = "0c51985fcf36"
BASE_REVISION = "02f616cbb655"  # HOM-75: calibration batches
APPEALS = "review_appeals"
CRITERIA = "review_appeal_criteria"
RESULTS = "review_appeal_results"
TABLES = (APPEALS, CRITERIA, RESULTS)
DECISIONS = "review_criterion_decisions"
HUMAN_RESULTS = "review_results"
ITEMS = "review_items"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
MACHINE_OUTCOMES = ("passed", "failed", "not_applicable", "insufficient_evidence")
# The check strings of the SPEC, character for character.
APPEAL_CHECKS = {
    "ck_review_appeals_status": "status IN ('open','decided','withdrawn')",
    "ck_review_appeals_revision": "revision >= 1",
    "ck_review_appeals_reason_note": (
        "length(trim(reason_note)) > 0 AND length(reason_note) <= 2000"
    ),
    "ck_review_appeals_disposition": (
        "disposition IS NULL OR disposition IN ('upheld','partially_granted','granted')"
    ),
    "ck_review_appeals_disposition_note_length": (
        "disposition_note IS NULL OR length(disposition_note) <= 2000"
    ),
    "ck_review_appeals_open_fields": (
        "status <> 'open' OR (disposition IS NULL AND disposition_note IS NULL "
        "AND decided_at IS NULL AND withdrawn_at IS NULL)"
    ),
    "ck_review_appeals_decided_fields": (
        "status <> 'decided' OR (disposition IS NOT NULL AND decided_at IS NOT NULL "
        "AND withdrawn_at IS NULL AND disposition_note IS NOT NULL "
        "AND length(trim(disposition_note)) > 0)"
    ),
    "ck_review_appeals_withdrawn_fields": (
        "status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND decided_at IS NULL "
        "AND disposition IS NULL AND disposition_note IS NULL)"
    ),
}
CRITERION_CHECKS = {
    "ck_review_appeal_criteria_appealed_outcome": (
        "appealed_outcome IN ('passed','failed','not_applicable','insufficient_evidence')"
    ),
    "ck_review_appeal_criteria_decision": "decision IS NULL OR decision IN ('uphold','change')",
    "ck_review_appeal_criteria_decided_outcome": (
        "decided_outcome IS NULL OR decided_outcome IN ('passed','failed','not_applicable')"
    ),
    "ck_review_appeal_criteria_reason_code": (
        "reason_code IS NULL OR reason_code IN "
        "('evidence_missed','evidence_misread','transcript_error',"
        "'context_outside_call','policy_exception','other')"
    ),
    "ck_review_appeal_criteria_note_length": "note IS NULL OR length(note) <= 2000",
    "ck_review_appeal_criteria_undecided": (
        "decision IS NOT NULL OR (decided_outcome IS NULL AND reason_code IS NULL AND note IS NULL)"
    ),
    "ck_review_appeal_criteria_uphold": (
        "decision IS NULL OR decision <> 'uphold' OR "
        "(decided_outcome IS NULL AND reason_code IS NULL)"
    ),
    "ck_review_appeal_criteria_change": (
        "decision IS NULL OR decision <> 'change' OR (decided_outcome IS NOT NULL "
        "AND decided_outcome <> appealed_outcome AND reason_code IS NOT NULL "
        "AND note IS NOT NULL AND length(trim(note)) > 0)"
    ),
    "ck_review_appeal_criteria_criterion_key": CRITERION_KEY_CHECK,
}
# The appeal result reuses the human result's own rules under its own names.
RESULT_CHECKS = {
    "ck_review_appeal_result_outcome": (
        "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
    ),
    "ck_review_appeal_result_reason": (
        "(outcome = 'passed' AND reason IS NULL) "
        "OR (outcome = 'failed' AND reason IS NOT NULL "
        "AND reason IN ('auto_fail', 'below_threshold')) "
        "OR (outcome = 'not_applicable' AND reason IS NOT NULL "
        "AND reason = 'no_applicable_criteria') "
        "OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
        "AND reason = 'evidence_missing')"
    ),
    "ck_review_appeal_result_threshold": "pass_threshold BETWEEN 1 AND 100",
    "ck_review_appeal_result_weights": (
        "earned_weight >= 0 AND failed_weight >= 0 AND insufficient_weight >= 0 "
        "AND applicable_weight = earned_weight + failed_weight + insufficient_weight "
        "AND applicable_weight <= 100"
    ),
    "ck_review_appeal_result_complete": "NOT complete OR insufficient_weight = 0",
}
# SQLite names the columns of a broken unique constraint, not the constraint.
UNIQUE_COLUMNS = {
    "uq_review_appeals_item": (APPEALS, ("review_item_id",)),
    "uq_review_appeal_criteria_criterion": (CRITERIA, ("appeal_id", "criterion_id")),
    "uq_review_appeal_results_appeal": (RESULTS, ("appeal_id",)),
}


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"review-appeal-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _conversation(tenant: dict) -> dict:
    """A new conversation of the tenant, without a transcript or a score."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="Appealed call",
        )
        db.add(meeting)
        db.commit()
        return {**tenant, "meeting_id": meeting.id}


def _completed_item_values(parents: dict, **extra: object) -> dict[str, object]:
    """A valid completed manual item, reviewed by its creator, on the parents' call."""
    return {
        "workspace_id": parents["workspace_id"],
        "meeting_id": parents["meeting_id"],
        "source": "manual",
        "source_details": {},
        "status": "completed",
        "started_at": NOW,
        "completed_at": NOW,
        "due_at": NOW + timedelta(days=2),
        "created_by_user_id": parents["user_id"],
        "assignee_user_id": parents["user_id"],
        "revision": 1,
        **extra,
    }


def _reviewed(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A scored conversation with a completed review item and its human result.

    The smallest setup an appeal needs: the item names the result, and the human
    result exists.  With ``meeting_id`` the conversation keeps its current
    transcript revision.
    """
    parents = _parents(tenant, key=key, meeting_id=meeting_id)
    result_id = _add(ScorecardResultRecord, _result(parents))
    criterion = _criterion(parents, result_id, 0)
    _add(CriterionResultRecord, criterion)
    item_id = _add(ReviewItem, _completed_item_values(parents, scorecard_result_id=result_id))
    setup = {**parents, "result_id": result_id, "criterion_id": criterion["criterion_id"]}
    _add(ReviewResultRecord, _human(setup | {"item_id": item_id}))
    return setup | {"item_id": item_id}


def _appeal(setup: dict, **extra: object) -> dict[str, object]:
    """A valid open appeal by the setup's user on the setup's reviewed item."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "review_item_id": setup["item_id"],
        "scorecard_result_id": setup["result_id"],
        "appellant_user_id": setup["user_id"],
        "original_reviewer_user_id": setup["user_id"],
        "status": "open",
        "reason_note": "The call was better than the score says.",
        "revision": 1,
        **extra,
    }


def _decided(setup: dict, **extra: object) -> dict[str, object]:
    """A valid decided appeal: the decider changed the appealed criterion; ``extra`` wins."""
    values = _appeal(
        setup,
        status="decided",
        disposition="granted",
        disposition_note="Checked the evidence again.",
        decided_by_user_id=setup["user_id"],
        decided_at=NOW,
    )
    values.update(extra)
    return values


def _withdrawn(setup: dict, **extra: object) -> dict[str, object]:
    """A valid withdrawn appeal: only the withdrawal timestamp is set; ``extra`` wins."""
    values = _appeal(setup, status="withdrawn", withdrawn_at=NOW)
    values.update(extra)
    return values


def _criterion_row(setup: dict, **extra: object) -> dict[str, object]:
    """A valid undecided criterion row: the setup's criterion appealed as passed."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "appeal_id": setup["appeal_id"],
        "review_item_id": setup["item_id"],
        "scorecard_result_id": setup["result_id"],
        "criterion_id": setup["criterion_id"],
        "appealed_outcome": "passed",
        "decision": None,
        "decided_outcome": None,
        "reason_code": None,
        "note": None,
        **extra,
    }


def _uphold(setup: dict, **extra: object) -> dict[str, object]:
    """A valid upheld criterion row: the human outcome stays; ``extra`` wins."""
    values = _criterion_row(setup, decision="uphold")
    values.update(extra)
    return values


def _change(setup: dict, **extra: object) -> dict[str, object]:
    """A valid changed criterion row: failed instead of passed, with a reason."""
    values = _criterion_row(
        setup,
        decision="change",
        decided_outcome="failed",
        reason_code="other",
        note="The evidence is there.",
    )
    values.update(extra)
    return values


def _appeal_result(setup: dict, **extra: object) -> dict[str, object]:
    """A valid appeal result: the setup's criterion scored again as failed."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "appeal_id": setup["appeal_id"],
        "review_item_id": setup["item_id"],
        "scorecard_result_id": setup["result_id"],
        "scorecards_version": "scorecards/1",
        "reviewer_outcomes": {setup["criterion_id"]: "failed"},
        "outcome": "failed",
        "reason": "auto_fail",
        "complete": True,
        "pass_threshold": 80,
        "applicable_weight": 100,
        "earned_weight": 0,
        "failed_weight": 100,
        "insufficient_weight": 0,
        "result": RESULT_JSON,
        **extra,
    }


def _appealed(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A reviewed conversation with a stored open appeal on it."""
    setup = _reviewed(tenant, key, meeting_id=meeting_id)
    appeal_id = _add(ReviewAppeal, _appeal(setup))
    return setup | {"appeal_id": appeal_id}


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        # SQLite does not name a broken foreign key.  Each deny names rows that exist,
        # so only the composite key can refuse it.
        return "FOREIGN KEY constraint failed"
    if constraint in UNIQUE_COLUMNS and not _on_postgres():
        table, columns = UNIQUE_COLUMNS[constraint]
        return re.escape(", ".join(f"{table}.{column}" for column in columns))
    return rf"\b{constraint}\b"


def _refused(model, values: dict[str, object], constraint: str, *also: str) -> None:
    """The insert fails on ``constraint``.

    SQLite reports the first broken check in table order, PostgreSQL the first in name
    order.  A deny that must break more than one rule lists the others in ``also``:
    SQLite must still name ``constraint``, PostgreSQL may name any of them.
    """
    names = (constraint, *also) if _on_postgres() else (constraint,)
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(IntegrityError, match="|".join(_names(name) for name in names)):
            db.flush()
        db.rollback()


def _ids(table: str, **where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {table} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


# --- the deny and allow cases (SPEC "Acceptance criteria" AC2 and AC4) ---------------------


def _appeal_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored valid appeal on a fresh reviewed item of its own (one per item)."""
    return _add(ReviewAppeal, _appeal(_reviewed(tenant, key), **extra))


def _assert_status_list_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "status_list")
    for value in ("paused", "Open", "open ", ""):
        _refused(ReviewAppeal, _appeal(setup, status=value), "ck_review_appeals_status")


def _assert_reason_note_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "reason_note")
    for value in ("", "   "):
        _refused(ReviewAppeal, _appeal(setup, reason_note=value), "ck_review_appeals_reason_note")
    # A Text column delivers every character to the check on both databases.
    _refused(ReviewAppeal, _appeal(setup, reason_note="x" * 2001), "ck_review_appeals_reason_note")
    _appeal_on(tenant, "reason_note_one", reason_note="x")
    _appeal_on(tenant, "reason_note_2000", reason_note="x" * 2000)


def _assert_revision_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "revision")
    _refused(ReviewAppeal, _appeal(setup, revision=0), "ck_review_appeals_revision")
    _appeal_on(tenant, "revision_one", revision=1)
    _appeal_on(tenant, "revision_two", revision=2)


def _assert_disposition_list_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "disposition_list")
    # A decided appeal with a disposition word outside the frozen list; every other
    # decided rule holds, so only the list can refuse it.
    for value in ("granted ", "Granted", "partial", ""):
        _refused(ReviewAppeal, _decided(setup, disposition=value), "ck_review_appeals_disposition")
    for value in REVIEW_APPEAL_DISPOSITIONS:
        _appeal_on(
            tenant,
            f"disposition_list_{value}",
            status="decided",
            disposition=value,
            disposition_note="Checked the evidence again.",
            decided_by_user_id=setup["user_id"],
            decided_at=NOW,
        )


def _assert_disposition_note_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "disposition_note")
    _refused(
        ReviewAppeal,
        _decided(setup, disposition_note="x" * 2001),
        "ck_review_appeals_disposition_note_length",
    )
    # A note on an open or a withdrawn appeal breaks the field rule, not the length.
    _refused(ReviewAppeal, _appeal(setup, disposition_note="x"), "ck_review_appeals_open_fields")
    _refused(
        ReviewAppeal,
        _withdrawn(setup, disposition_note="x"),
        "ck_review_appeals_withdrawn_fields",
    )


def _assert_open_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "open_fields")
    _refused(ReviewAppeal, _appeal(setup, decided_at=NOW), "ck_review_appeals_open_fields")
    _refused(ReviewAppeal, _appeal(setup, withdrawn_at=NOW), "ck_review_appeals_open_fields")
    _refused(ReviewAppeal, _appeal(setup, disposition="upheld"), "ck_review_appeals_open_fields")
    _appeal_on(tenant, "open_fields_valid")


def _assert_decided_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "decided_fields")
    _refused(ReviewAppeal, _decided(setup, disposition=None), "ck_review_appeals_decided_fields")
    _refused(ReviewAppeal, _decided(setup, decided_at=None), "ck_review_appeals_decided_fields")
    _refused(ReviewAppeal, _decided(setup, withdrawn_at=NOW), "ck_review_appeals_decided_fields")
    _refused(
        ReviewAppeal, _decided(setup, disposition_note="   "), "ck_review_appeals_decided_fields"
    )
    _refused(
        ReviewAppeal, _decided(setup, disposition_note=None), "ck_review_appeals_decided_fields"
    )
    _appeal_on(
        tenant,
        "decided_fields_valid",
        status="decided",
        disposition="partially_granted",
        disposition_note="One criterion changed.",
        decided_by_user_id=setup["user_id"],
        decided_at=NOW,
    )


def _assert_withdrawn_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "withdrawn_fields")
    _refused(
        ReviewAppeal, _withdrawn(setup, withdrawn_at=None), "ck_review_appeals_withdrawn_fields"
    )
    _refused(ReviewAppeal, _withdrawn(setup, decided_at=NOW), "ck_review_appeals_withdrawn_fields")
    _refused(
        ReviewAppeal, _withdrawn(setup, disposition="upheld"), "ck_review_appeals_withdrawn_fields"
    )
    _refused(
        ReviewAppeal,
        _withdrawn(setup, disposition_note="x"),
        "ck_review_appeals_withdrawn_fields",
    )
    _appeal_on(tenant, "withdrawn_fields_valid", status="withdrawn", withdrawn_at=NOW)


APPEAL_RULES = (
    _assert_status_list_rules,
    _assert_reason_note_rules,
    _assert_revision_rules,
    _assert_disposition_list_rules,
    _assert_disposition_note_rules,
    _assert_open_fields_rules,
    _assert_decided_fields_rules,
    _assert_withdrawn_fields_rules,
)


def _criterion_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored criterion row on a fresh appeal of its own (one per appeal and id)."""
    return _add(ReviewAppealCriterion, _criterion_row(_appealed(tenant, key), **extra))


def _change_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored valid change on a fresh appeal of its own; ``extra`` wins."""
    return _add(ReviewAppealCriterion, _change(_appealed(tenant, key), **extra))


def _assert_appealed_outcome_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "appealed_outcome")
    # A reviewer's word is not a machine outcome; the machine's four words are.
    for value in ("reviewer", "maybe", ""):
        _refused(
            ReviewAppealCriterion,
            _criterion_row(setup, appealed_outcome=value),
            "ck_review_appeal_criteria_appealed_outcome",
        )
    for value in MACHINE_OUTCOMES:
        _criterion_on(tenant, f"appealed_outcome_{value}", appealed_outcome=value)


def _assert_criterion_decision_list_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "decision_list")
    for value in ("reject", "Uphold", "uphold ", ""):
        _refused(
            ReviewAppealCriterion,
            _criterion_row(setup, decision=value),
            "ck_review_appeal_criteria_decision",
        )
    _criterion_on(tenant, "decision_list_uphold", decision="uphold")
    _change_on(tenant, "decision_list_change")


def _assert_decided_outcome_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "decided_outcome")
    # An outcome without a decision.
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, decided_outcome="failed"),
        "ck_review_appeal_criteria_undecided",
    )
    # The reviewer's three words only: the machine's word for missing evidence is
    # refused, and so is every other word.
    for value in ("insufficient_evidence", "reviewer", ""):
        _refused(
            ReviewAppealCriterion,
            _change(setup, decided_outcome=value),
            "ck_review_appeal_criteria_decided_outcome",
        )
    _change_on(tenant, "decided_outcome_failed", decided_outcome="failed")
    _change_on(tenant, "decided_outcome_na", decided_outcome="not_applicable")


def _assert_criterion_reason_code_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "reason_code")
    for value in ("because", "Other", ""):
        _refused(
            ReviewAppealCriterion,
            _change(setup, reason_code=value),
            "ck_review_appeal_criteria_reason_code",
        )
    # A reason code on an uphold, and one on an undecided row.
    _refused(
        ReviewAppealCriterion,
        _uphold(setup, reason_code="other"),
        "ck_review_appeal_criteria_uphold",
    )
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, reason_code="other"),
        "ck_review_appeal_criteria_undecided",
    )
    for value in REVIEW_OVERRIDE_REASONS:
        _change_on(tenant, f"reason_code_{value}", reason_code=value)


def _assert_criterion_note_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "note")
    _refused(ReviewAppealCriterion, _change(setup, note=None), "ck_review_appeal_criteria_change")
    _refused(ReviewAppealCriterion, _change(setup, note="   "), "ck_review_appeal_criteria_change")
    _refused(
        ReviewAppealCriterion,
        _uphold(setup, note="x" * 2001),
        "ck_review_appeal_criteria_note_length",
    )
    _change_on(tenant, "note_valid_change")
    _change_on(tenant, "note_change_2000", note="x" * 2000)
    # A note on an uphold is allowed, up to the same length.
    _criterion_on(tenant, "note_uphold_2000", decision="uphold", note="x" * 2000)


def _assert_undecided_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "undecided")
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, decided_outcome="failed"),
        "ck_review_appeal_criteria_undecided",
    )
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, reason_code="other"),
        "ck_review_appeal_criteria_undecided",
    )
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, note="x"),
        "ck_review_appeal_criteria_undecided",
    )
    _criterion_on(tenant, "undecided_valid")


def _assert_uphold_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "uphold")
    _refused(
        ReviewAppealCriterion,
        _uphold(setup, decided_outcome="failed"),
        "ck_review_appeal_criteria_uphold",
    )
    _refused(
        ReviewAppealCriterion,
        _uphold(setup, reason_code="other"),
        "ck_review_appeal_criteria_uphold",
    )
    # An uphold with a note is allowed; so is a bare one.
    _criterion_on(tenant, "uphold_with_note", decision="uphold", note="Agreed on a second look.")
    _criterion_on(tenant, "uphold_bare", decision="uphold")


def _assert_change_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "change")
    # A change equal to the appealed outcome changes nothing.
    _refused(
        ReviewAppealCriterion,
        _change(setup, decided_outcome="passed"),
        "ck_review_appeal_criteria_change",
    )
    _refused(
        ReviewAppealCriterion, _change(setup, reason_code=None), "ck_review_appeal_criteria_change"
    )
    _refused(ReviewAppealCriterion, _change(setup, note=None), "ck_review_appeal_criteria_change")
    _refused(ReviewAppealCriterion, _change(setup, note="   "), "ck_review_appeal_criteria_change")
    _change_on(tenant, "change_valid")
    # A machine ``insufficient_evidence`` the reviewer did not decide, changed to failed.
    _change_on(tenant, "change_insufficient", appealed_outcome="insufficient_evidence")


def _assert_criterion_key_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "criterion_key")
    for value in ("C0", "c", "c-0", "0c", ""):
        _refused(
            ReviewAppealCriterion,
            _criterion_row(setup, criterion_id=value),
            "ck_review_appeal_criteria_criterion_key",
        )


CRITERION_RULES = (
    _assert_appealed_outcome_rules,
    _assert_criterion_decision_list_rules,
    _assert_decided_outcome_rules,
    _assert_criterion_reason_code_rules,
    _assert_criterion_note_rules,
    _assert_undecided_rules,
    _assert_uphold_rules,
    _assert_change_rules,
    _assert_criterion_key_rules,
)


def _result_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored valid appeal result on a fresh appeal of its own (one per appeal)."""
    return _add(ReviewAppealResult, _appeal_result(_appealed(tenant, key), **extra))


def _assert_result_outcome_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "result_outcome")
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, outcome="maybe"),
        "ck_review_appeal_result_outcome",
        "ck_review_appeal_result_reason",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, outcome="passed"),
        "ck_review_appeal_result_reason",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, outcome="passed", reason="other"),
        "ck_review_appeal_result_reason",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, outcome="not_applicable", reason="auto_fail"),
        "ck_review_appeal_result_reason",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, outcome="insufficient_evidence", reason="auto_fail"),
        "ck_review_appeal_result_reason",
    )
    # Each outcome with its own reason, as the human result's rules demand.
    _result_on(tenant, "result_outcome_failed")
    _result_on(
        tenant,
        "result_outcome_passed",
        outcome="passed",
        reason=None,
        earned_weight=100,
        failed_weight=0,
    )
    _result_on(
        tenant,
        "result_outcome_na",
        outcome="not_applicable",
        reason="no_applicable_criteria",
        applicable_weight=0,
        earned_weight=0,
        failed_weight=0,
    )
    _result_on(
        tenant,
        "result_outcome_insufficient",
        outcome="insufficient_evidence",
        reason="evidence_missing",
        complete=False,
        earned_weight=90,
        failed_weight=0,
        insufficient_weight=10,
    )


def _assert_result_number_rules(tenant: dict) -> None:
    setup = _appealed(tenant, "result_numbers")
    for value in (0, 101):
        _refused(
            ReviewAppealResult,
            _appeal_result(setup, pass_threshold=value),
            "ck_review_appeal_result_threshold",
        )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, earned_weight=-1, failed_weight=101),
        "ck_review_appeal_result_weights",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, failed_weight=101),
        "ck_review_appeal_result_weights",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, applicable_weight=101, failed_weight=101),
        "ck_review_appeal_result_weights",
    )
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, complete=True, insufficient_weight=10, failed_weight=90),
        "ck_review_appeal_result_complete",
    )
    # Each allow row on a fresh appeal (one appeal result per appeal).
    _result_on(tenant, "result_numbers_low", pass_threshold=1)
    _result_on(tenant, "result_numbers_high", pass_threshold=100)
    # Incomplete with no insufficient weight is allowed (a weight-0 criterion).
    _result_on(tenant, "result_numbers_incomplete", complete=False)


RESULT_RULES = (
    _assert_result_outcome_rules,
    _assert_result_number_rules,
)


def _assert_one_appeal_per_item(tenant: dict) -> None:
    setup = _appealed(tenant, "one_appeal")
    _refused(ReviewAppeal, _appeal(setup, reason_note="A second try."), "uq_review_appeals_item")
    # An appeal of another reviewed item is fine.
    _appeal_on(tenant, "one_appeal_other")


def _assert_one_criterion_row_per_appeal_criterion(tenant: dict) -> None:
    setup = _appealed(tenant, "one_criterion")
    _add(ReviewAppealCriterion, _criterion_row(setup))
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, decision="uphold"),
        "uq_review_appeal_criteria_criterion",
    )
    # Another criterion of the same appeal, and the same criterion of another appeal.
    second = _criterion(setup, setup["result_id"], 1)
    _add(CriterionResultRecord, second)
    _add(ReviewAppealCriterion, _criterion_row(setup, criterion_id=second["criterion_id"]))
    _criterion_on(tenant, "one_criterion_other")


def _assert_one_appeal_result_per_appeal(tenant: dict) -> None:
    setup = _appealed(tenant, "one_result")
    _add(ReviewAppealResult, _appeal_result(setup))
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, outcome="passed", reason=None, earned_weight=100, failed_weight=0),
        "uq_review_appeal_results_appeal",
    )
    _result_on(tenant, "one_result_other")


UNIQUE_RULES = (
    _assert_one_appeal_per_item,
    _assert_one_criterion_row_per_appeal_criterion,
    _assert_one_appeal_result_per_appeal,
)
RULES = (*APPEAL_RULES, *CRITERION_RULES, *RESULT_RULES, *UNIQUE_RULES)


@pytest.mark.parametrize(
    "rules",
    RULES,
    ids=lambda rules: rules.__name__.removeprefix("_assert_"),
)
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


# --- the composite keys (SPEC acceptance criterion AC3) ------------------------------------


def _scored_in_review(tenant: dict, key: str) -> dict:
    """A scored conversation with an item still in review and no human result."""
    parents = _parents(tenant, key=key)
    result_id = _add(ScorecardResultRecord, _result(parents))
    criterion = _criterion(parents, result_id, 0)
    _add(CriterionResultRecord, criterion)
    item_id = _add(ReviewItem, _item_values(parents, scorecard_result_id=result_id))
    return {
        **parents,
        "result_id": result_id,
        "criterion_id": criterion["criterion_id"],
        "item_id": item_id,
    }


def test_an_appeal_names_only_its_items_result_the_results_scope_and_a_reviewed_item(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "scope-a")
    other = _tenant(client, email_outbox, "scope-b")
    setup = _reviewed(tenant, "scope")
    # A second result of the same conversation: a valid result, not its item's.
    second_result = _add(ScorecardResultRecord, _result(setup))
    _refused(
        ReviewAppeal, _appeal(setup, scorecard_result_id=second_result), "fk_review_appeals_item"
    )
    # Its result's own workspace and meeting, or none.
    elsewhere_meeting = _conversation(tenant)["meeting_id"]
    _refused(
        ReviewAppeal,
        _appeal(setup, meeting_id=elsewhere_meeting),
        "fk_review_appeals_result",
    )
    _refused(
        ReviewAppeal,
        _appeal(setup, workspace_id=other["workspace_id"]),
        "fk_review_appeals_result",
    )
    # An item with no human result: only a reviewed item can be appealed.
    unreviewed = _scored_in_review(tenant, "scope_unreviewed")
    _refused(
        ReviewAppeal,
        _appeal(unreviewed),
        "fk_review_appeals_review_result",
    )
    # Allow-controls: its own item, result, workspace and meeting.
    _add(ReviewAppeal, _appeal(setup))


def test_a_criterion_row_names_only_its_appeals_item_result_and_criterion(client, email_outbox):
    tenant = _tenant(client, email_outbox, "criterion-scope")
    setup = _appealed(tenant, "criterion_scope")
    other = _appealed(tenant, "criterion_scope_other")
    # Another appeal's item and result under this appeal's id: valid rows, not this
    # appeal's.
    _refused(
        ReviewAppealCriterion,
        _criterion_row(
            setup,
            meeting_id=other["meeting_id"],
            review_item_id=other["item_id"],
            scorecard_result_id=other["result_id"],
            criterion_id=other["criterion_id"],
        ),
        "fk_review_appeal_criteria_appeal",
    )
    # The other appeal's item with this appeal's result.
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, review_item_id=other["item_id"]),
        "fk_review_appeal_criteria_appeal",
    )
    # Its result's own workspace and meeting, or none.
    elsewhere_meeting = _conversation(tenant)["meeting_id"]
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, meeting_id=elsewhere_meeting),
        "fk_review_appeal_criteria_result",
    )
    other_tenant = _tenant(client, email_outbox, "criterion-scope-b")
    elsewhere = _appealed(other_tenant, "criterion_scope_elsewhere")
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, workspace_id=elsewhere["workspace_id"]),
        "fk_review_appeal_criteria_result",
    )
    # A criterion that is not one of the result's.
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, criterion_id="cx"),
        "fk_review_appeal_criteria_criterion",
    )
    # A result other than its appeal's: a second result of the same conversation,
    # with the same criterion id, so only the appeal key can refuse it.
    second_result = _add(ScorecardResultRecord, _result(setup))
    _add(CriterionResultRecord, _criterion(setup, second_result, 0))
    _refused(
        ReviewAppealCriterion,
        _criterion_row(setup, scorecard_result_id=second_result),
        "fk_review_appeal_criteria_appeal",
    )
    # Allow-control: its own appeal, item, result and criterion.
    _add(ReviewAppealCriterion, _criterion_row(setup))


def test_an_appeal_result_names_only_its_appeals_scope_and_result(client, email_outbox):
    tenant = _tenant(client, email_outbox, "result-scope")
    setup = _appealed(tenant, "result_scope")
    other = _appealed(tenant, "result_scope_other")
    # Another appeal's item and result under this appeal's id.
    _refused(
        ReviewAppealResult,
        _appeal_result(
            setup,
            meeting_id=other["meeting_id"],
            review_item_id=other["item_id"],
            scorecard_result_id=other["result_id"],
        ),
        "fk_review_appeal_results_appeal",
    )
    # A result other than its appeal's: a second result of the same conversation.
    second_result = _add(ScorecardResultRecord, _result(setup))
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, scorecard_result_id=second_result),
        "fk_review_appeal_results_appeal",
    )
    # Its result's own workspace and meeting, or none.
    elsewhere_meeting = _conversation(tenant)["meeting_id"]
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, meeting_id=elsewhere_meeting),
        "fk_review_appeal_results_scorecard_result",
    )
    other_tenant = _tenant(client, email_outbox, "result-scope-b")
    elsewhere = _appealed(other_tenant, "result_scope_elsewhere")
    _refused(
        ReviewAppealResult,
        _appeal_result(setup, workspace_id=elsewhere["workspace_id"]),
        "fk_review_appeal_results_scorecard_result",
    )
    # Allow-control: its own appeal, item, result, workspace and meeting.
    _add(ReviewAppealResult, _appeal_result(setup))


# --- the ORM, the frozen lists and the RLS lists --------------------------------------------

APPEAL_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "review_item_id": 36,
    "scorecard_result_id": 36,
    "appellant_user_id": 36,
    "original_reviewer_user_id": 36,
    "status": 16,
    "assignee_user_id": 36,
    "assigned_by_user_id": 36,
    "disposition": 24,
    "decided_by_user_id": 36,
}
APPEAL_NULLABLE = {
    "appellant_user_id",
    "original_reviewer_user_id",
    "assignee_user_id",
    "assigned_by_user_id",
    "assigned_at",
    "disposition",
    "disposition_note",
    "decided_by_user_id",
    "decided_at",
    "withdrawn_at",
}
APPEAL_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("appellant_user_id",), ("users.id",), "SET NULL"),
    (None, ("original_reviewer_user_id",), ("users.id",), "SET NULL"),
    (None, ("assignee_user_id",), ("users.id",), "SET NULL"),
    (None, ("assigned_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("decided_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_review_appeals_item",
        ("review_item_id", "scorecard_result_id"),
        ("review_items.id", "review_items.scorecard_result_id"),
        "CASCADE",
    ),
    (
        "fk_review_appeals_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
    (
        "fk_review_appeals_review_result",
        ("review_item_id",),
        ("review_results.review_item_id",),
        "CASCADE",
    ),
}
APPEAL_INDEXES = {
    "ix_review_appeals_workspace_id": (["workspace_id"], False),
    "ix_review_appeals_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_review_appeals_appellant_user_id": (["appellant_user_id"], False),
    "ix_review_appeals_assignee_user_id": (["assignee_user_id"], False),
    "uq_review_appeals_scope": (["id", "review_item_id", "scorecard_result_id"], True),
    "ix_review_appeals_queue": (["workspace_id", "status", "created_at", "id"], False),
    "ix_review_appeals_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_appeals_table():
    table = ReviewAppeal.__table__
    assert table.name == APPEALS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "review_item_id",
        "scorecard_result_id",
        "appellant_user_id",
        "original_reviewer_user_id",
        "status",
        "reason_note",
        "assignee_user_id",
        "assigned_by_user_id",
        "assigned_at",
        "disposition",
        "disposition_note",
        "decided_by_user_id",
        "decided_at",
        "withdrawn_at",
        "created_at",
        "updated_at",
        "revision",
    ]
    for name, length in APPEAL_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("assigned_at", "decided_at", "withdrawn_at", "created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert table.c.reason_note.type.__class__.__name__ == "Text"
    assert table.c.disposition_note.type.__class__.__name__ == "Text"
    assert {column.name for column in table.columns if column.nullable} == APPEAL_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]

    # The ORM defaults (SQLAlchemy wraps a callable default and keeps its name).
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.status.default.arg == "open"
    assert table.c.revision.default.arg == 1
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)

    assert {
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    } == APPEAL_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == (
        APPEAL_CHECKS
    )
    assert [name for name in named if name.startswith("uq_")] == ["uq_review_appeals_item"]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == APPEAL_INDEXES
    assert "HOM-76" in ReviewAppeal.__doc__
    assert "compare-and-set" in ReviewAppeal.__doc__


CRITERION_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "appeal_id": 36,
    "review_item_id": 36,
    "scorecard_result_id": 36,
    "criterion_id": 64,
    "appealed_outcome": 24,
    "decision": 16,
    "decided_outcome": 24,
    "reason_code": 32,
}
CRITERION_NULLABLE = {"decision", "decided_outcome", "reason_code", "note"}
CRITERION_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        "fk_review_appeal_criteria_appeal",
        ("appeal_id", "review_item_id", "scorecard_result_id"),
        (
            "review_appeals.id",
            "review_appeals.review_item_id",
            "review_appeals.scorecard_result_id",
        ),
        "CASCADE",
    ),
    (
        "fk_review_appeal_criteria_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
    (
        "fk_review_appeal_criteria_criterion",
        ("scorecard_result_id", "criterion_id"),
        ("scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"),
        "CASCADE",
    ),
}
CRITERION_INDEXES = {
    "ix_review_appeal_criteria_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_review_appeal_criteria_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_appeal_criteria_table():
    table = ReviewAppealCriterion.__table__
    assert table.name == CRITERIA
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "appeal_id",
        "review_item_id",
        "scorecard_result_id",
        "criterion_id",
        "appealed_outcome",
        "decision",
        "decided_outcome",
        "reason_code",
        "note",
        "created_at",
        "updated_at",
    ]
    for name, length in CRITERION_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert table.c.note.type.__class__.__name__ == "Text"
    assert {column.name for column in table.columns if column.nullable} == CRITERION_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]

    assert table.c.id.default.arg.__name__ == "new_id"
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)

    assert {
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    } == CRITERION_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == (
        CRITERION_CHECKS
    )
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_review_appeal_criteria_criterion"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == CRITERION_INDEXES
    assert "HOM-76" in ReviewAppealCriterion.__doc__


RESULT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "appeal_id": 36,
    "review_item_id": 36,
    "scorecard_result_id": 36,
    "scorecards_version": 40,
    "outcome": 24,
    "reason": 40,
}
RESULT_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        "fk_review_appeal_results_appeal",
        ("appeal_id", "review_item_id", "scorecard_result_id"),
        (
            "review_appeals.id",
            "review_appeals.review_item_id",
            "review_appeals.scorecard_result_id",
        ),
        "CASCADE",
    ),
    (
        "fk_review_appeal_results_scorecard_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
}
RESULT_INDEXES = {
    "ix_review_appeal_results_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_review_appeal_results_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_appeal_results_table():
    table = ReviewAppealResult.__table__
    assert table.name == RESULTS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "appeal_id",
        "review_item_id",
        "scorecard_result_id",
        "scorecards_version",
        "reviewer_outcomes",
        "outcome",
        "reason",
        "complete",
        "pass_threshold",
        "applicable_weight",
        "earned_weight",
        "failed_weight",
        "insufficient_weight",
        "result",
        "created_at",
    ]
    for name, length in RESULT_LENGTHS.items():
        assert table.c[name].type.length == length, name
    assert table.c.created_at.type.timezone
    assert {column.name for column in table.columns if column.nullable} == {"reason"}
    assert [column.name for column in table.primary_key] == ["id"]

    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.created_at.default.arg.__name__ == "utcnow"
    assert all(column.server_default is None for column in table.columns)

    assert {
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    } == RESULT_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == (
        RESULT_CHECKS
    )
    assert [name for name in named if name.startswith("uq_")] == ["uq_review_appeal_results_appeal"]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == RESULT_INDEXES
    # No user column: the table is immutable (no UPDATE grant), so a SET NULL action
    # on a user column would need an update.
    assert [name for name in table.c.keys() if "user" in name] == []
    assert "HOM-76" in ReviewAppealResult.__doc__
    assert "immutable" in ReviewAppealResult.__doc__.lower()


def test_the_frozen_lists_and_the_check_strings():
    assert REVIEW_APPEAL_STATUSES == ("open", "decided", "withdrawn")
    assert REVIEW_APPEAL_DISPOSITIONS == ("upheld", "partially_granted", "granted")
    assert REVIEW_APPEAL_CRITERION_DECISIONS == ("uphold", "change")
    assert REVIEW_APPEAL_MAX_CRITERIA == 20
    assert REVIEW_APPEAL_WINDOW_DAYS == 14
    assert APPEAL_CHECKS["ck_review_appeals_status"] == ("status IN ('open','decided','withdrawn')")
    assert APPEAL_CHECKS["ck_review_appeals_reason_note"] == (
        f"length(trim(reason_note)) > 0 AND length(reason_note) <= {REVIEW_NOTE_MAX_LENGTH}"
    )
    assert CRITERION_CHECKS["ck_review_appeal_criteria_appealed_outcome"] == (
        "appealed_outcome IN ('passed','failed','not_applicable','insufficient_evidence')"
    )
    # The decided outcome is one of the reviewer's three words, never the machine's
    # word for missing evidence.
    assert CRITERION_CHECKS["ck_review_appeal_criteria_decided_outcome"].endswith(
        "(" + ",".join(f"'{value}'" for value in REVIEW_REVIEWER_OUTCOMES) + ")"
    )
    assert CRITERION_CHECKS["ck_review_appeal_criteria_reason_code"].endswith(
        "(" + ",".join(f"'{value}'" for value in REVIEW_OVERRIDE_REASONS) + ")"
    )
    assert CRITERION_CHECKS["ck_review_appeal_criteria_decision"].endswith(
        "(" + ",".join(f"'{value}'" for value in REVIEW_APPEAL_CRITERION_DECISIONS) + ")"
    )
    assert ENGINE_REVIEWER_OUTCOMES == REVIEW_REVIEWER_OUTCOMES
    assert CRITERION_CHECKS["ck_review_appeal_criteria_note_length"] == (
        f"note IS NULL OR length(note) <= {REVIEW_NOTE_MAX_LENGTH}"
    )
    # The appeal result's checks are the human result's own strings.
    assert RESULT_CHECKS["ck_review_appeal_result_outcome"] == (
        "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
    )
    assert RESULT_CHECKS["ck_review_appeal_result_threshold"] == "pass_threshold BETWEEN 1 AND 100"
    for check in (
        *APPEAL_CHECKS.values(),
        *CRITERION_CHECKS.values(),
        *RESULT_CHECKS.values(),
    ):
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        # No user column: those are SET NULL on a user's removal; a check on one would
        # make the removal fail.
        for column in (
            "appellant_user_id",
            "original_reviewer_user_id",
            "assignee_user_id",
            "assigned_by_user_id",
            "decided_by_user_id",
        ):
            assert column not in check, column


def test_every_table_is_a_workspace_table_and_only_results_refuse_updates():
    for table in TABLES:
        assert table in WORKSPACE_TABLES
        assert table not in APPEND_ONLY_TABLES
        assert table not in NO_DELETE_TABLES
        assert table not in SHARED_CONTENT_TABLES  # never through a share token
        assert table not in UNPROTECTED_TABLES
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    # A stored appeal result never changes, like a stored human result; the service
    # of PR 2 writes the appeals and the criterion rows, and the purge deletes all
    # three as the application role.
    assert RESULTS in NO_UPDATE_TABLES
    for table in (APPEALS, CRITERIA):
        assert table not in NO_UPDATE_TABLES
    assert NO_UPDATE_TABLES == (
        "scorecard_results",
        "scorecard_criterion_results",
        "review_results",
        "calibration_examples",
        RESULTS,
    )


# --- immutability: the appeal result is written once (SPEC acceptance criterion AC6) --------


def _changed(value: object) -> object:
    """Another value of the same kind; the guard refuses before any SQL runs."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, dict):
        return {**value, "changed": True}
    if isinstance(value, str) or value is None:
        return "changed"
    return value + timedelta(seconds=1)  # created_at


def _snapshot(model, row_id: str) -> dict[str, object]:
    with inspection_session_factory()() as db:
        row = db.get(model, row_id)
        return {column.key: getattr(row, column.key) for column in model.__table__.columns}


def _utc(value: datetime) -> datetime:
    """SQLite returns a naive datetime for a ``DateTime(timezone=True)`` column."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def test_the_orm_refuses_to_change_any_column_of_an_appeal_result(client, email_outbox):
    setup = _appealed(_tenant(client, email_outbox, "orm_update"), "orm_update")
    row_id = _add(ReviewAppealResult, _appeal_result(setup))
    before = _snapshot(ReviewAppealResult, row_id)
    factory = inspection_session_factory()
    for column in ReviewAppealResult.__table__.columns:
        with factory() as db:
            row = db.get(ReviewAppealResult, row_id)
            setattr(row, column.key, _changed(getattr(row, column.key)))
            with pytest.raises(
                ReviewAppealResultImmutableError, match=rf"attempted: {column.key}\)"
            ):
                db.flush()
            db.rollback()

    # Allow-control: a flush that changes nothing.
    with factory() as db:
        row = db.get(ReviewAppealResult, row_id)
        row.outcome = row.outcome
        row.result = row.result
        db.flush()
        db.commit()
    assert _snapshot(ReviewAppealResult, row_id) == before


def test_the_orm_refuses_to_delete_an_appeal_result(client, email_outbox):
    setup = _appealed(_tenant(client, email_outbox, "orm_delete"), "orm_delete")
    row_id = _add(ReviewAppealResult, _appeal_result(setup))
    with inspection_session_factory()() as db:
        db.delete(db.get(ReviewAppealResult, row_id))
        with pytest.raises(ReviewAppealResultImmutableError, match="immutable"):
            db.flush()
        db.rollback()
    assert _ids(RESULTS) == {row_id}


def test_an_appeal_and_its_criterion_rows_are_not_immutable(client, email_outbox):
    setup = _appealed(_tenant(client, email_outbox, "orm_change"), "orm_change")
    criterion_id = _add(ReviewAppealCriterion, _criterion_row(setup))
    later = NOW + timedelta(minutes=1)
    with inspection_session_factory()() as db:
        appeal = db.get(ReviewAppeal, setup["appeal_id"])
        appeal.assignee_user_id = setup["user_id"]
        appeal.assigned_by_user_id = setup["user_id"]
        appeal.assigned_at = NOW
        appeal.status = "decided"
        appeal.disposition = "granted"
        appeal.disposition_note = "Checked the evidence again."
        appeal.decided_by_user_id = setup["user_id"]
        appeal.decided_at = later
        appeal.updated_at = later
        appeal.revision = 2
        row = db.get(ReviewAppealCriterion, criterion_id)
        row.decision = "change"
        row.decided_outcome = "failed"
        row.reason_code = "other"
        row.note = "The evidence is there."
        row.updated_at = later
        db.commit()
    with inspection_session_factory()() as db:
        appeal = db.get(ReviewAppeal, setup["appeal_id"])
        assert (appeal.status, appeal.disposition, appeal.revision) == ("decided", "granted", 2)
        assert _utc(appeal.decided_at) == later
        row = db.get(ReviewAppealCriterion, criterion_id)
        assert (row.decision, row.decided_outcome, row.reason_code) == ("change", "failed", "other")
        db.delete(row)
        db.flush()
        db.delete(appeal)
        db.commit()
    assert _ids(CRITERIA) == set()
    assert _ids(APPEALS) == set()


# --- cascades and user removal (SPEC acceptance criterion AC7) ------------------------------


def _full(tenant: dict, key: str) -> dict:
    """A reviewed conversation with an appeal, its criterion row and its appeal result."""
    setup = _appealed(tenant, key)
    criterion_row_id = _add(ReviewAppealCriterion, _change(setup))
    result_id = _add(ReviewAppealResult, _appeal_result(setup))
    return setup | {"criterion_row_id": criterion_row_id, "appeal_result_id": result_id}


def _rows(setup: dict) -> dict[str, set[str]]:
    return {table: _ids(table, meeting_id=setup["meeting_id"]) for table in TABLES}


def test_deleting_an_item_a_result_a_conversation_or_a_workspace_removes_the_rows(
    client, email_outbox
):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    by_item = _full(second, "cascade_item")
    by_result = _full(second, "cascade_result")
    by_meeting = _full(second, "cascade_meeting")
    elsewhere = _full(first, "cascade_elsewhere")
    for setup in (by_item, by_result, by_meeting, elsewhere):
        assert all(len(ids) == 1 for ids in _rows(setup).values()), setup

    # Removals by the owner (DBA here): the FK actions run as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(ReviewItem).where(ReviewItem.id == by_item["item_id"]))
        db.execute(
            delete(ScorecardResultRecord).where(ScorecardResultRecord.id == by_result["result_id"])
        )
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.commit()
    assert _ids(APPEALS) == {elsewhere["appeal_id"]}
    assert _ids(CRITERIA) == {elsewhere["criterion_row_id"]}
    assert _ids(RESULTS) == {elsewhere["appeal_result_id"]}

    kept = _full(second, "cascade_kept")
    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    assert _ids(APPEALS) == {kept["appeal_id"]}
    assert _ids(CRITERIA) == {kept["criterion_row_id"]}
    assert _ids(RESULTS) == {kept["appeal_result_id"]}


USER_COLUMNS = (
    "appellant_user_id",
    "original_reviewer_user_id",
    "assignee_user_id",
    "assigned_by_user_id",
    "decided_by_user_id",
)


def test_deleting_a_user_clears_every_user_column_and_keeps_the_row(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user_removal")
    with inspection_session_factory()() as db:
        user = User(email="review-appeal-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    setup = _reviewed(tenant, "user_removal")
    values = _decided(setup, assigned_at=NOW)
    values.update({column: user_id for column in USER_COLUMNS})
    appeal_id = _add(ReviewAppeal, values)
    setup = setup | {"appeal_id": appeal_id}
    criterion_row_id = _add(ReviewAppealCriterion, _change(setup))
    result_id = _add(ReviewAppealResult, _appeal_result(setup))

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        row = db.get(ReviewAppeal, appeal_id)
        for column in USER_COLUMNS:
            assert getattr(row, column) is None, column
        # Everything else stays, the state rules included.
        assert (row.status, row.disposition, row.revision) == ("decided", "granted", 1)
        assert row.review_item_id == setup["item_id"]
    assert _ids(CRITERIA) == {criterion_row_id}
    assert _ids(RESULTS) == {result_id}


# --- the migration (SPEC acceptance criterion AC1, SQLite) ----------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_review_appeals")


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == (
        "Agent appeals of review results, schema only (HOM-76, WP14-T05)"
    )
    for words in ("Why", "No data changes", "Downgrade"):
        assert words in doc, words
    # Frozen copies of the model's check strings.
    assert module.CHECKS == APPEAL_CHECKS
    assert module.CRITERION_CHECKS == CRITERION_CHECKS
    assert module.RESULT_CHECKS == RESULT_CHECKS


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == NEW_REVISION


def test_alembic_heads_prints_exactly_this_revision():
    import io

    from alembic import command
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    shown = io.StringIO()
    command.heads(Config(str(PROJECT_ROOT / "alembic.ini"), stdout=shown))
    lines = [line for line in shown.getvalue().splitlines() if line.strip()]
    assert lines == [f"{NEW_REVISION} (head)"], shown.getvalue()


def _reflect(connection, table: str) -> dict:
    inspector = inspect(connection)
    return {
        "columns": [
            (column["name"], str(column["type"]), column["nullable"])
            for column in inspector.get_columns(table)
        ],
        "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
        "foreign_keys": sorted(
            (
                key["name"] or "",
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
                key["options"].get("ondelete"),
            )
            for key in inspector.get_foreign_keys(table)
        ),
        "unique": inspector.get_unique_constraints(table),
        "checks": sorted(
            (check["name"] or "", check["sqltext"])
            for check in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        ),
    }


def _objects(connection) -> set[tuple[str, str, str]]:
    """Every table and index of the database, as (type, name, table)."""
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT type, name, tbl_name FROM sqlite_master WHERE tbl_name <> 'alembic_version'"
            )
        ).all()
    }


def _untouched(shapes: dict) -> dict:
    """Every table this migration does not build or change."""
    return {name: shape for name, shape in shapes.items() if name not in TABLES}


def test_on_sqlite_the_migration_builds_the_orm_tables_and_drops_only_them():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            before = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            before_objects = _objects(connection)

            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert {table: _reflect(connection, table) for table in TABLES} == orm
            assert sorted(name for name, _ in orm[APPEALS]["checks"]) == sorted(APPEAL_CHECKS)
            assert sorted(name for name, _ in orm[CRITERIA]["checks"]) == sorted(CRITERION_CHECKS)
            assert sorted(name for name, _ in orm[RESULTS]["checks"]) == sorted(RESULT_CHECKS)
            # No other table changed.
            after = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert _untouched(after) == _untouched(before)

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            final = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert final == before
            assert _objects(connection) == before_objects
            assert not any(name in TABLES for _, name, _ in _objects(connection))
    finally:
        engine.dispose()


def _alembic_at(monkeypatch, url: str):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_on_sqlite_alembic_upgrades_to_the_head_checks_clean_and_downgrades_one_step(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    url = f"sqlite:///{tmp_path / 'review-appeals.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            before_objects = _objects(connection)

        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == NEW_REVISION
            assert {table: _reflect(connection, table) for table in TABLES} == orm
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")

        command.downgrade(config, "-1")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == BASE_REVISION
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()
        reset_settings_cache()


# --- PostgreSQL: the schema, the migration, isolation and grants (AC1, AC5) -----------------


def _alembic(monkeypatch, settings: Settings):
    return _alembic_at(monkeypatch, settings.database_url)


def _shape(table: str) -> dict:
    """The table as the catalog shows it: columns, constraints, indexes, policy, grants."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        indexes = db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"), {"t": table}
        ).all()
        policies = db.execute(
            text(
                "SELECT policyname, permissive, roles::text, cmd, qual, with_check "
                "FROM pg_policies WHERE tablename = :t"
            ),
            {"t": table},
        ).all()
        flags = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        grants = db.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t AND grantee = :r"
            ),
            {"t": table, "r": APP_ROLE},
        ).all()
    return {
        "columns": {name: tuple(rest) for name, *rest in columns},
        "constraints": dict(constraints),
        "indexes": dict(indexes),
        "policies": {name: tuple(rest) for name, *rest in policies},
        "rls": [tuple(row) for row in flags],
        "grants": sorted(row[0] for row in grants),
    }


EMPTY_SHAPE = {
    "columns": {},
    "constraints": {},
    "indexes": {},
    "policies": {},
    "rls": [],
    "grants": [],
}

OTHER_TABLE_QUERIES = {
    "tables": (
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname NOT IN (:a, :b, :c)"
    ),
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b, :c)"
    ),
    "constraints": (
        "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
        "AND c.conrelid::regclass::text NOT IN (:a, :b, :c)"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename NOT IN (:a, :b, :c)"
    ),
    "policies": (
        "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename NOT IN (:a, :b, :c)"
    ),
    "grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b, :c)"
    ),
}


def _other_tables() -> dict:
    """Every other table of the schema, so a test can prove it did not change."""
    excluded = dict(zip("abc", TABLES, strict=True))
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return ("timestamp with time zone", None, nullable, None)


def _plain(kind: str, nullable: str = "NO") -> tuple:
    return (kind, None, nullable, None)


EXPECTED_APPEAL_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "review_item_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "appellant_user_id": _varchar(36, "YES"),
    "original_reviewer_user_id": _varchar(36, "YES"),
    "status": _varchar(16),
    "reason_note": _plain("text"),
    "assignee_user_id": _varchar(36, "YES"),
    "assigned_by_user_id": _varchar(36, "YES"),
    "assigned_at": _timestamp("YES"),
    "disposition": _varchar(24, "YES"),
    "disposition_note": _plain("text", "YES"),
    "decided_by_user_id": _varchar(36, "YES"),
    "decided_at": _timestamp("YES"),
    "withdrawn_at": _timestamp("YES"),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
    "revision": _plain("integer"),
}

EXPECTED_CRITERION_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "appeal_id": _varchar(36),
    "review_item_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "criterion_id": _varchar(64),
    "appealed_outcome": _varchar(24),
    "decision": _varchar(16, "YES"),
    "decided_outcome": _varchar(24, "YES"),
    "reason_code": _varchar(32, "YES"),
    "note": _plain("text", "YES"),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
}

EXPECTED_RESULT_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "appeal_id": _varchar(36),
    "review_item_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "scorecards_version": _varchar(40),
    "reviewer_outcomes": _plain("json"),
    "outcome": _varchar(24),
    "reason": _varchar(40, "YES"),
    "complete": _plain("boolean"),
    "pass_threshold": _plain("integer"),
    "applicable_weight": _plain("integer"),
    "earned_weight": _plain("integer"),
    "failed_weight": _plain("integer"),
    "insufficient_weight": _plain("integer"),
    "result": _plain("json"),
    "created_at": _timestamp(),
}


def _foreign_key(table: str, column: str, target: str, action: str) -> tuple[str, str]:
    return (
        f"{table}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}",
    )


RESULT_SCOPE = (
    "FOREIGN KEY (scorecard_result_id, workspace_id, meeting_id) REFERENCES "
    "scorecard_results(id, workspace_id, meeting_id) ON DELETE CASCADE"
)
APPEAL_SCOPE = (
    "FOREIGN KEY (appeal_id, review_item_id, scorecard_result_id) REFERENCES "
    "review_appeals(id, review_item_id, scorecard_result_id) ON DELETE CASCADE"
)

EXPECTED_APPEAL_CONSTRAINTS = dict(
    [
        (f"{APPEALS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(APPEALS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(APPEALS, "meeting_id", "meetings", "CASCADE"),
        *(_foreign_key(APPEALS, column, "users", "SET NULL") for column in USER_COLUMNS),
        (
            "fk_review_appeals_item",
            "FOREIGN KEY (review_item_id, scorecard_result_id) REFERENCES "
            "review_items(id, scorecard_result_id) ON DELETE CASCADE",
        ),
        ("fk_review_appeals_result", RESULT_SCOPE),
        (
            "fk_review_appeals_review_result",
            "FOREIGN KEY (review_item_id) REFERENCES review_results(review_item_id) "
            "ON DELETE CASCADE",
        ),
        ("uq_review_appeals_item", "UNIQUE (review_item_id)"),
    ]
)

EXPECTED_CRITERION_CONSTRAINTS = dict(
    [
        (f"{CRITERIA}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(CRITERIA, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(CRITERIA, "meeting_id", "meetings", "CASCADE"),
        ("fk_review_appeal_criteria_appeal", APPEAL_SCOPE),
        ("fk_review_appeal_criteria_result", RESULT_SCOPE),
        (
            "fk_review_appeal_criteria_criterion",
            "FOREIGN KEY (scorecard_result_id, criterion_id) REFERENCES "
            "scorecard_criterion_results(result_id, criterion_id) ON DELETE CASCADE",
        ),
        ("uq_review_appeal_criteria_criterion", "UNIQUE (appeal_id, criterion_id)"),
    ]
)

EXPECTED_RESULT_CONSTRAINTS = dict(
    [
        (f"{RESULTS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(RESULTS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(RESULTS, "meeting_id", "meetings", "CASCADE"),
        ("fk_review_appeal_results_appeal", APPEAL_SCOPE),
        ("fk_review_appeal_results_scorecard_result", RESULT_SCOPE),
        ("uq_review_appeal_results_appeal", "UNIQUE (appeal_id)"),
    ]
)


def _index(name: str, table: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_TABLES = {
    APPEALS: {
        "columns": EXPECTED_APPEAL_COLUMNS,
        "constraints": EXPECTED_APPEAL_CONSTRAINTS,
        "checks": APPEAL_CHECKS,
        "indexes": dict(
            [
                _index(f"{APPEALS}_pkey", APPEALS, "id", unique=True),
                _index("uq_review_appeals_item", APPEALS, "review_item_id", unique=True),
                _index(
                    "uq_review_appeals_scope",
                    APPEALS,
                    "id, review_item_id, scorecard_result_id",
                    unique=True,
                ),
                _index("ix_review_appeals_workspace_id", APPEALS, "workspace_id"),
                _index("ix_review_appeals_scorecard_result_id", APPEALS, "scorecard_result_id"),
                _index("ix_review_appeals_appellant_user_id", APPEALS, "appellant_user_id"),
                _index("ix_review_appeals_assignee_user_id", APPEALS, "assignee_user_id"),
                _index("ix_review_appeals_queue", APPEALS, "workspace_id, status, created_at, id"),
                _index("ix_review_appeals_workspace_meeting", APPEALS, "workspace_id, meeting_id"),
            ]
        ),
    },
    CRITERIA: {
        "columns": EXPECTED_CRITERION_COLUMNS,
        "constraints": EXPECTED_CRITERION_CONSTRAINTS,
        "checks": CRITERION_CHECKS,
        "indexes": dict(
            [
                _index(f"{CRITERIA}_pkey", CRITERIA, "id", unique=True),
                _index(
                    "uq_review_appeal_criteria_criterion",
                    CRITERIA,
                    "appeal_id, criterion_id",
                    unique=True,
                ),
                _index(
                    "ix_review_appeal_criteria_scorecard_result_id",
                    CRITERIA,
                    "scorecard_result_id",
                ),
                _index(
                    "ix_review_appeal_criteria_workspace_meeting",
                    CRITERIA,
                    "workspace_id, meeting_id",
                ),
            ]
        ),
    },
    RESULTS: {
        "columns": EXPECTED_RESULT_COLUMNS,
        "constraints": EXPECTED_RESULT_CONSTRAINTS,
        "checks": RESULT_CHECKS,
        "indexes": dict(
            [
                _index(f"{RESULTS}_pkey", RESULTS, "id", unique=True),
                _index("uq_review_appeal_results_appeal", RESULTS, "appeal_id", unique=True),
                _index(
                    "ix_review_appeal_results_scorecard_result_id",
                    RESULTS,
                    "scorecard_result_id",
                ),
                _index(
                    "ix_review_appeal_results_workspace_meeting",
                    RESULTS,
                    "workspace_id, meeting_id",
                ),
            ]
        ),
    },
}


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    expected = EXPECTED_TABLES[table]
    assert shape["columns"] == expected["columns"], table
    constraints = dict(shape["constraints"])
    named_checks = {name for name in constraints if name.startswith("ck_")}
    assert named_checks == set(expected["checks"])  # their behaviour is pinned by the RULES above
    for name in named_checks:
        constraints.pop(name)
    assert constraints == expected["constraints"], table
    assert shape["indexes"] == expected["indexes"], table
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    if table == RESULTS:
        # A stored appeal result never changes; the purge deletes the rows.
        assert shape["grants"] == ["DELETE", "INSERT", "SELECT"]
    else:
        # The service of PR 2 assigns, decides and withdraws; the purge deletes.
        assert shape["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"]
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_three_tables(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_three_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    _full(tenant, "migration")
    results = _scalar("SELECT count(*) FROM review_results")
    items = _scalar("SELECT count(*) FROM review_items")
    meetings = _scalar("SELECT count(*) FROM meetings")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, "-1")
        others = _other_tables()  # the three tables are gone with the downgrade
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}")
            assert _shape(table) == EMPTY_SHAPE
        # Only the appeal rows are lost: the reviews, the items and the calls stay.
        assert _scalar("SELECT count(*) FROM review_results") == results
        assert _scalar("SELECT count(*) FROM review_items") == items
        assert _scalar("SELECT count(*) FROM meetings") == meetings

        command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others  # every other table as it was

        # The migrated schema refuses the same deny cases and takes the allow cases.
        for rules in RULES:
            rules(tenant)
        migrated = _full(tenant, "migration_migrated")
        assert migrated["appeal_result_id"] in _ids(RESULTS)

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others
    finally:
        reset_settings_cache()


def _write_rows(db, setup: dict) -> dict:
    """The three rows of one appeal, written in the session's own transaction."""
    appeal = ReviewAppeal(**_appeal(setup))
    db.add(appeal)
    db.flush()
    setup = setup | {"appeal_id": appeal.id}
    db.add(ReviewAppealCriterion(**_change(setup)))
    db.add(ReviewAppealResult(**_appeal_result(setup)))
    db.flush()
    return setup


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _reviewed(_tenant(client, email_outbox, "tenant_a"), "tenant_a")
    second = _reviewed(_tenant(client, email_outbox, "tenant_b"), "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_rows(db, first)
        db.commit()

    # Bound to B: none of A's rows; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        second = _write_rows(db, second)
        db.commit()

    # Bound to A: only A's rows; an insert with B's ids fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]]
    for model, values in (
        (ReviewAppeal, _appeal(second)),
        (ReviewAppealCriterion, _change(second)),
        (ReviewAppealResult, _appeal_result(second)),
    ):
        with factory() as db:
            bind_tenant_context(db, workspace_id=first["workspace_id"])
            db.add(model(**values))
            with pytest.raises(DBAPIError, match="row-level security"):
                db.flush()
            db.rollback()

    # No workspace bound: nothing.
    with factory() as db:
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox):
    _requires_postgres()
    setup = _full(_tenant(client, email_outbox, "share"), "share")
    token_hash = "cd" * 32
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=setup["workspace_id"],
                meeting_id=setup["meeting_id"],
                created_by_user_id=setup["user_id"],
                token_hash=token_hash,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash=token_hash)
        # Control: the shared conversation itself is readable through the token.
        assert db.scalars(select(Meeting.id)).all() == [setup["meeting_id"]]
        for model in (ReviewAppeal, ReviewAppealCriterion, ReviewAppealResult):
            assert db.scalars(select(model.id)).all() == [], model.__tablename__
        db.rollback()


def _privileges(table: str) -> dict[str, bool]:
    return {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


def _apply_as_owner() -> None:
    from backend.database.rls import apply_row_level_security

    with inspection_session_factory()() as db:
        try:
            apply_row_level_security(db.connection())
        except Exception:
            db.rollback()
            raise
        db.commit()


@pytest.mark.postgres
def test_the_app_role_updates_two_tables_and_never_the_appeal_results(client, email_outbox):
    _requires_postgres()
    first = _reviewed(_tenant(client, email_outbox, "grants_a"), "grants_a")
    second = _reviewed(_tenant(client, email_outbox, "grants_b"), "grants_b")
    full = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    frozen = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    for table in (APPEALS, CRITERIA):
        assert _privileges(table) == full, table
    assert _privileges(RESULTS) == frozen

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_rows(db, first)
        db.commit()

    # Bound to B: A's rows are invisible, so an update or a delete changes none of them.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for statement in (
            f"UPDATE {APPEALS} SET revision = revision + 1",  # noqa: S608
            f"UPDATE {CRITERIA} SET note = 'x'",  # noqa: S608
            f"DELETE FROM {CRITERIA}",  # noqa: S608
            f"DELETE FROM {RESULTS}",  # noqa: S608
            f"DELETE FROM {APPEALS}",  # noqa: S608
        ):
            assert db.execute(text(statement)).rowcount == 0
        db.commit()
    for table in TABLES:
        assert len(_ids(table)) == 1, table

    # Bound to A: the compare-and-set of PR 2 on the appeal, the decision on the
    # criterion row, no UPDATE of the appeal result, and still none after the policies
    # and the grants are applied again.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {APPEALS} SET status = 'withdrawn', withdrawn_at = :now, "  # noqa: S608
                "revision = revision + 1 WHERE id = :id AND workspace_id = :w "
                "AND revision = 1 AND status = 'open'"
            ),
            {"now": NOW, "id": first["appeal_id"], "w": first["workspace_id"]},
        )
        assert updated.rowcount == 1
        assert (
            db.execute(text(f"UPDATE {CRITERIA} SET note = 'Seen again.'")).rowcount  # noqa: S608
            == 1
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(f"UPDATE {RESULTS} SET outcome = 'passed'"))  # noqa: S608
        db.rollback()
        db.commit()
    _apply_as_owner()  # idempotent: no revoke for these tables
    for table in (APPEALS, CRITERIA):
        assert _privileges(table) == full, table
    assert _privileges(RESULTS) == frozen

    # Allow-control, bound to A: the purge's own statements delete all three.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        meeting = db.get(Meeting, first["meeting_id"])
        _remove_content(db, workspace_id=first["workspace_id"], meeting=meeting)
        db.commit()
    for table in TABLES:
        assert _ids(table) == set(), table


# --- the conversation purge and the backup reconciliation (AC8, AC9) ------------------------


def _purgeable(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A reviewed conversation with a decision, an appeal, its criterion row and its
    appeal result."""
    setup = _reviewed(tenant, key, meeting_id=meeting_id)
    _add(ReviewCriterionDecision, _decision(setup))
    appeal_id = _add(ReviewAppeal, _appeal(setup))
    setup = setup | {"appeal_id": appeal_id}
    _add(ReviewAppealCriterion, _change(setup))
    _add(ReviewAppealResult, _appeal_result(setup))
    return setup


def test_the_purge_deletes_the_appeal_rows_before_the_review_rows_and_counts_them(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "purge-order")
    setup = _purgeable(tenant, "ordered")
    kept = _purgeable(tenant, "kept")
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            meeting = db.get(Meeting, setup["meeting_id"])
            removed = _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting)
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)

    deletes = [s.split()[2] for s in statements if s.lstrip().upper().startswith("DELETE FROM")]
    # The appeal rows go first, children before the appeal: the cascades of the human
    # result, the review item and the scorecard result would otherwise remove them
    # uncounted.
    assert deletes.index(RESULTS) < deletes.index(CRITERIA) < deletes.index(APPEALS)
    for table in (DECISIONS, HUMAN_RESULTS, ITEMS, "scorecard_results"):
        assert deletes.index(APPEALS) < deletes.index(table), table
    # One appeal result, one appeal criterion, one appeal, one decision, one human
    # result, one item, one result, one criterion row and the transcript revision.
    assert removed == 9
    for table in TABLES:
        assert _ids(table, meeting_id=setup["meeting_id"]) == set(), table
        assert len(_ids(table, meeting_id=kept["meeting_id"])) == 1, table


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "appeal-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"appeal-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    for meeting_id, key in ((queued, "appeal_purged"), (kept, "appeal_kept")):
        _purgeable(tenant, key, meeting_id=meeting_id)
    for table in TABLES:
        assert len(_ids(table, meeting_id=queued)) == 1, table

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    for table in TABLES:
        assert _ids(table, meeting_id=queued) == set(), table
        # Another conversation of the workspace keeps its appeal rows.
        assert len(_ids(table, meeting_id=kept)) == 1, table
    # The purge deletes the appeal result, the appeal criterion, the appeal, the
    # decision, the human result, the item, the result and its criterion row itself
    # (they are counted), before any cascade could.
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 8


def test_a_held_conversation_keeps_its_appeal_rows_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "appeal-hold")
    meeting_id = _processed(client, tenant, settings, key="appeal-hold-0001")
    _purgeable(tenant, "appeal_held", meeting_id=meeting_id)
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-9", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    # The deletion of a held conversation: the purge waits and the rows stay.
    _delete_conversation(client, tenant, meeting_id)
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == ERROR_LEGAL_HOLD
    for table in TABLES:
        assert len(_ids(table, meeting_id=meeting_id)) == 1, table
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    for table in TABLES:
        assert len(_ids(table, meeting_id=meeting_id)) == 1, table

    # After the release, the next attempt removes them.
    for media in _media(meeting_id):
        released = client.post(
            f"/api/v1/media/{media.id}/legal-hold/release",
            json={"reason": "matter closed"},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert released.status_code == 200, released.text
    final = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(seconds=1))
    assert final["purges_completed"] == 1, final
    for table in TABLES:
        assert _ids(table, meeting_id=meeting_id) == set(), table


def test_the_backup_reconciliation_counts_the_three_tables():
    for table in TABLES:
        assert table in RECONCILED_TABLES, table
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns
    # Next to the review rows they belong to.
    assert RECONCILED_TABLES.index(HUMAN_RESULTS) < RECONCILED_TABLES.index(APPEALS)


# --- the ADR (SPEC acceptance criterion AC11) ------------------------------------------------


def _decision_record(name: str) -> str:
    from backend.observability.release import PROJECT_ROOT

    path = PROJECT_ROOT.parent / "docs" / "architecture" / "decisions" / name
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def _adr() -> str:
    return _decision_record("0057-agent-appeal-and-disposition.md")


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0057: ")
    assert "Proposed (HOM-76, WP14-T05, F46)" in content
    assert "2026-11-12" in content
    for number in ("0051", "0052", "0053", "0054"):
        assert f"ADR {number}" in content, number
    for heading in ("Context", "Decision", "Consequences", "Not built"):
        assert re.search(rf"^## {heading}\s*$", content, re.MULTILINE), heading
    for section in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
        assert re.search(rf"^### {section}\.", content, re.MULTILINE), section


def test_the_adr_uses_no_em_dash_and_no_double_hyphen_dash():
    content = _adr()
    assert "—" not in content
    assert not re.search(r"(^|\s)--(\s|$)", content, re.MULTILINE)


# What the SPEC says the ADR records; any one spelling of a topic is enough.
ADR_TOPICS = {
    "the_tables": (APPEALS,),
    "the_criteria_table": (CRITERIA,),
    "the_results_table": (RESULTS,),
    "window": ("14 days",),
    "one_appeal": ("uq_review_appeals_item",),
    "human_result_key": ("fk_review_appeals_review_result",),
    "original_reviewers": ("original_reviewer_user_id",),
    "decision_rows": ("decided_by_user_id",),
    "check_assignee": ("check_assignee",),
    "permission": ("review:appeal",),
    "not_an_api_key_scope": ("API-key scope", "API key scope"),
    "agent_routes": ("/api/v1/my-reviews",),
    "my_appeals": ("/api/v1/my-appeals",),
    "supervisor_routes": ("/api/v1/review-appeals",),
    "history_route": ("/history",),
    "window_closed": ("appeal_window_closed",),
    "appeal_exists": ("appeal_exists",),
    "party": ("appeal_decider_is_party",),
    "original_reviewer_code": ("appeal_decider_is_original_reviewer",),
    "assign_party": ("appeal_assignee_is_party",),
    "assign_original": ("appeal_assignee_is_original_reviewer",),
    "not_appellant": ("appeal_not_appellant",),
    "revision": ("expected_revision", "review_appeal_revision_conflict"),
    "decisions_incomplete": ("appeal_decisions_incomplete",),
    "change_matches": ("appeal_change_matches",),
    "final_outcome": ("final outcome",),
    "score_human_result": ("score_human_result",),
    "audit": ("review_appeal.filed",),
    "audit_decided": ("review_appeal.decided",),
    "notifications": ("notifications",),
    "api_key_denied": ("api_key_scope_denied",),
    "notification_not_found": ("notification_not_found",),
    "purge": ("purge",),
    "backup": ("reconcil",),
    "not_built_coaching": ("HOM-77",),
    "not_built_console": ("HOM-250",),
    "not_built_delivery": ("HOM-134",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings


# The new line of ADR 0051 and ADR 0053: appeals keep their F46 tag (the HOM-74 tests
# read F46 in ADR 0053).
POINTED_LINE = (
    "- Coaching (F47). Calibration is ADR 0056 (HOM-75) and appeals (F46) are ADR 0057 (HOM-76)."
)
NOT_BUILT_LINES = {
    "0051-supervisor-review-queue.md": (
        "- Appeals and coaching (F46, F47). Calibration is ADR 0056 (HOM-75).",
        POINTED_LINE,
    ),
    "0053-criterion-override-and-human-result.md": (
        "- Appeals and coaching (F46, F47). Calibration is ADR 0056 (HOM-75).",
        POINTED_LINE,
    ),
    "0056-blinded-calibration-and-agreement.md": (
        "- Appeals and coaching remain open (F46 second clause, F47).",
        "- Coaching remains open (F47). Appeals (F46 second clause) are ADR 0057 (HOM-76).",
    ),
}


@pytest.mark.parametrize("name", NOT_BUILT_LINES, ids=lambda name: name[:4])
def test_the_earlier_adrs_point_their_appeal_line_at_adr_0057(name):
    old, new = NOT_BUILT_LINES[name]
    content = _decision_record(name)
    not_built = content.split("## Not built", 1)[1].split("\n## ", 1)[0]
    assert old not in content
    assert new in not_built.splitlines()
