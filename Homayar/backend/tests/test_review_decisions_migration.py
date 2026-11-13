"""HOM-74 PR 1: criterion decisions, the human result and their one migration.

``review_criterion_decisions`` holds one human decision on one criterion of the machine
result under review, inside one review item.  ``review_results`` holds the human score
of one completed review: the machine result scored again by the same engine with the
reviewer outcomes.  Nothing writes the tables yet; PR 2 adds the service and the routes.
The tests check:

* the ORM declares the columns, the named constraints, the unique constraints and the
  indexes of the design, the frozen lists (equal to the engine's ``REVIEWER_OUTCOMES``)
  and the check strings, which use no regex and never name a user column;
* ``database/rls.py`` lists both tables as workspace tables, ``review_results`` in the
  no-update list, neither in the append-only or no-delete lists;
* every deny case of the SPEC is refused by its named constraint, and the closest allow
  case passes (SQLite and PostgreSQL): the decision and outcome lists, the override
  reason and note rules, one decision per item and criterion, the composite foreign
  keys to the item's result, the result's own scope and a criterion of that result, and
  the human result's own outcome, reason, threshold, weights and completeness rules;
* ``review_results`` refuses an ORM update and an ORM delete; a decision row does not
  (the service of PR 2 upserts it);
* deleting a conversation, a workspace, a review item or a scorecard result removes the
  dependent decisions and human results, and deleting a user sets ``decided_by_user_id``
  to NULL and keeps the decision;
* on SQLite, the migration builds the same two tables as the ORM and the
  ``uq_review_items_result`` index on ``review_items``, and its downgrade removes them
  and nothing else;
* on PostgreSQL: the ORM schema (``create_all``) and the migrated schema are the same;
  downgrade from the head removes both tables and ``uq_review_items_result`` and leaves
  every other table as it was; upgrade recreates them; tenant isolation (also on
  write); the application role may SELECT, INSERT, UPDATE and DELETE its own workspace's
  decisions and has no UPDATE on ``review_results``;
* the conversation purge deletes the decisions and the human results of the purged
  conversation only, before its review items and its scorecard results, and counts them;
  a legal hold keeps them; the backup reconciliation counts both tables; the code has
  one migration head, this one; ADR 0053 records the design.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, inspect, text
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
    REVIEW_DECISION_ACCEPT_CHECK,
    REVIEW_DECISION_CHECK,
    REVIEW_DECISION_MACHINE_OUTCOME_CHECK,
    REVIEW_DECISION_NOTE_LENGTH_CHECK,
    REVIEW_DECISION_OVERRIDE_CHECK,
    REVIEW_DECISION_REASON_CODE_CHECK,
    REVIEW_DECISION_REVIEWER_OUTCOME_CHECK,
    REVIEW_DECISIONS,
    REVIEW_NOTE_MAX_LENGTH,
    REVIEW_OVERRIDE_REASONS,
    REVIEW_REVIEWER_OUTCOMES,
    CriterionResultRecord,
    Meeting,
    PurgeState,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultImmutableError,
    ReviewResultRecord,
    ScorecardResultRecord,
    User,
    Workspace,
    utcnow,
)
from backend.database.tenant_context import bind_tenant_context
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

NEW_REVISION = "a651110b03c3"
BASE_REVISION = "484a6df74abf"  # HOM-73: review items
HEAD_REVISION = "d3d1c7aa3cd8"  # HOM-129: privileged MFA and break-glass, after HOM-93
DECISIONS = "review_criterion_decisions"
RESULTS = "review_results"
ITEMS = "review_items"
TABLES = (DECISIONS, RESULTS)
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
MACHINE_OUTCOMES = ("passed", "failed", "not_applicable", "insufficient_evidence")
ITEM_INDEX = "uq_review_items_result"
# The check strings of the SPEC's decisions table, character for character.
CHECKS = {
    "ck_review_criterion_decisions_decision": "decision IN ('accept','override')",
    "ck_review_criterion_decisions_machine_outcome": (
        "machine_outcome IN ('passed','failed','not_applicable','insufficient_evidence')"
    ),
    "ck_review_criterion_decisions_reviewer_outcome": (
        "reviewer_outcome IN ('passed','failed','not_applicable')"
    ),
    "ck_review_criterion_decisions_reason_code": (
        "reason_code IS NULL OR reason_code IN "
        "('evidence_missed','evidence_misread','transcript_error',"
        "'context_outside_call','policy_exception','other')"
    ),
    "ck_review_criterion_decisions_note_length": "note IS NULL OR length(note) <= 2000",
    "ck_review_criterion_decisions_accept": (
        "decision <> 'accept' OR (reviewer_outcome = machine_outcome AND reason_code IS NULL)"
    ),
    "ck_review_criterion_decisions_override": (
        "decision <> 'override' OR (reviewer_outcome <> machine_outcome "
        "AND reason_code IS NOT NULL AND note IS NOT NULL AND length(trim(note)) > 0)"
    ),
    "ck_review_criterion_decisions_criterion_key": CRITERION_KEY_CHECK,
}
# The human result reuses the machine result's own rules under its own constraint names.
RESULT_CHECKS = {
    "ck_review_result_outcome": (
        "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
    ),
    "ck_review_result_reason": (
        "(outcome = 'passed' AND reason IS NULL) "
        "OR (outcome = 'failed' AND reason IS NOT NULL "
        "AND reason IN ('auto_fail', 'below_threshold')) "
        "OR (outcome = 'not_applicable' AND reason IS NOT NULL "
        "AND reason = 'no_applicable_criteria') "
        "OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
        "AND reason = 'evidence_missing')"
    ),
    "ck_review_result_threshold": "pass_threshold BETWEEN 1 AND 100",
    "ck_review_result_weights": (
        "earned_weight >= 0 AND failed_weight >= 0 AND insufficient_weight >= 0 "
        "AND applicable_weight = earned_weight + failed_weight + insufficient_weight "
        "AND applicable_weight <= 100"
    ),
    "ck_review_result_complete": "NOT complete OR insufficient_weight = 0",
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
    credentials = register_user(client, email_outbox, email=f"review-decision-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _conversation(tenant: dict) -> dict:
    """A new conversation of the tenant, without a transcript or a score."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="Reviewed call",
        )
        db.add(meeting)
        db.commit()
        return {**tenant, "meeting_id": meeting.id}


def _item_values(parents: dict, **extra: object) -> dict[str, object]:
    """A valid ``in_review`` manual item on the parents' conversation."""
    return {
        "workspace_id": parents["workspace_id"],
        "meeting_id": parents["meeting_id"],
        "source": "manual",
        "source_details": {},
        "status": "in_review",
        "started_at": NOW,
        "due_at": NOW + timedelta(days=2),
        "created_by_user_id": parents["user_id"],
        "revision": 1,
        **extra,
    }


def _scored(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A scored conversation with one passed criterion and one item in review on it.

    The item names the result, so the setup is the smallest a decision needs.  With
    ``meeting_id`` the conversation keeps its current transcript revision.
    """
    parents = _parents(tenant, key=key, meeting_id=meeting_id)
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


def _decision(setup: dict, **extra: object) -> dict[str, object]:
    """A valid accept of the setup's passed criterion."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "review_item_id": setup["item_id"],
        "scorecard_result_id": setup["result_id"],
        "criterion_id": setup["criterion_id"],
        "decision": "accept",
        "machine_outcome": "passed",
        "reviewer_outcome": "passed",
        "reason_code": None,
        "note": None,
        "evidence": [],
        "decided_by_user_id": setup["user_id"],
        **extra,
    }


def _override(setup: dict, **extra: object) -> dict[str, object]:
    """A valid override of the passed criterion to ``failed``; ``extra`` wins."""
    values = _decision(setup)
    values.update(
        {
            "decision": "override",
            "reviewer_outcome": "failed",
            "reason_code": "other",
            "note": "x",
            **extra,
        }
    )
    return values


def _human(setup: dict, **extra: object) -> dict[str, object]:
    """A valid human result: the setup's machine result scored again, unchanged."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "review_item_id": setup["item_id"],
        "scorecard_result_id": setup["result_id"],
        "scorecards_version": "scorecards/1",
        "reviewer_outcomes": {setup["criterion_id"]: "passed"},
        "outcome": "passed",
        "reason": None,
        "complete": True,
        "pass_threshold": 80,
        "applicable_weight": 100,
        "earned_weight": 100,
        "failed_weight": 0,
        "insufficient_weight": 0,
        "result": RESULT_JSON,
        **extra,
    }


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        # SQLite does not name a broken foreign key.  Each deny names a workspace, a
        # meeting and a result that exist, so only the composite key can refuse it.
        return "FOREIGN KEY constraint failed"
    if constraint == "uq_review_criterion_decisions_criterion" and not _on_postgres():
        # SQLite names the columns of a broken unique constraint, not the constraint.
        return re.escape(f"{DECISIONS}.review_item_id, {DECISIONS}.criterion_id")
    if constraint == "uq_review_results_item" and not _on_postgres():
        return re.escape(f"{RESULTS}.review_item_id")
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


def _utc(value: datetime) -> datetime:
    """SQLite returns a naive datetime for a ``DateTime(timezone=True)`` column."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --- the deny and allow cases (SPEC "Deny-cases and their allow-controls") ------------------


def _decision_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored accept on a fresh setup of its own (one decision per item and criterion)."""
    return _add(ReviewCriterionDecision, _decision(_scored(tenant, key), **extra))


def _override_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored override on a fresh setup of its own (one decision per item and criterion)."""
    return _add(ReviewCriterionDecision, _override(_scored(tenant, key), **extra))


def _assert_decision_list_rules(tenant: dict) -> None:
    setup = _scored(tenant, "decision_list")
    for value in ("reject", "Accept", "accept ", ""):
        _refused(
            ReviewCriterionDecision,
            _decision(setup, decision=value),
            "ck_review_criterion_decisions_decision",
        )
    _decision_on(tenant, "decision_list_accept")
    _override_on(tenant, "decision_list_override")


def _assert_outcome_list_rules(tenant: dict) -> None:
    setup = _scored(tenant, "outcome_list")
    # The reviewer never scores ``insufficient_evidence``: that is the machine's word.
    for value in ("insufficient_evidence", "maybe", ""):
        _refused(
            ReviewCriterionDecision,
            _override(setup, reviewer_outcome=value),
            "ck_review_criterion_decisions_reviewer_outcome",
        )
    for value in ("maybe", ""):
        _refused(
            ReviewCriterionDecision,
            _override(setup, machine_outcome=value),
            "ck_review_criterion_decisions_machine_outcome",
        )
    # The reviewer outcomes the engine accepts, one allow per fresh item.
    _decision_on(tenant, "outcome_list_passed")
    _override_on(tenant, "outcome_list_failed", reviewer_outcome="failed")
    _override_on(tenant, "outcome_list_na", reviewer_outcome="not_applicable")
    # A machine ``insufficient_evidence`` can be overridden, never accepted.
    for value in ("passed", "failed", "not_applicable"):
        _decision_on(
            tenant, f"outcome_list_machine_{value}", machine_outcome=value, reviewer_outcome=value
        )
    _override_on(
        tenant, "outcome_list_machine_insufficient", machine_outcome="insufficient_evidence"
    )


def _assert_reason_code_rules(tenant: dict) -> None:
    setup = _scored(tenant, "reason_code")
    for value in ("because", "Other", ""):
        _refused(
            ReviewCriterionDecision,
            _override(setup, reason_code=value),
            "ck_review_criterion_decisions_reason_code",
        )
    # A reason on an accept breaks the accept rule, not the list.
    _refused(
        ReviewCriterionDecision,
        _decision(setup, reason_code="other"),
        "ck_review_criterion_decisions_accept",
    )
    for value in REVIEW_OVERRIDE_REASONS:
        _override_on(tenant, f"reason_code_{value}", reason_code=value)
    # Allow-control: an accept with a note and no code.
    _decision_on(tenant, "reason_code_note", note="looks right")


def _assert_note_rules(tenant: dict) -> None:
    setup = _scored(tenant, "note")
    _refused(
        ReviewCriterionDecision,
        _override(setup, note=None),
        "ck_review_criterion_decisions_override",
    )
    _refused(
        ReviewCriterionDecision,
        _override(setup, note="   "),
        "ck_review_criterion_decisions_override",
    )
    _refused(
        ReviewCriterionDecision,
        _decision(setup, note="x" * 2001),
        "ck_review_criterion_decisions_note_length",
    )
    _override_on(tenant, "note_valid")
    # A note on an accept is allowed, up to the same length.
    _decision_on(tenant, "note_accept_2000", note="x" * 2000)


def _assert_accept_rules(tenant: dict) -> None:
    setup = _scored(tenant, "accept")
    _refused(
        ReviewCriterionDecision,
        _decision(setup, reviewer_outcome="failed"),
        "ck_review_criterion_decisions_accept",
    )
    # An accept confirms the machine outcome, and ``insufficient_evidence`` cannot be
    # one of the reviewer's words.
    _refused(
        ReviewCriterionDecision,
        _decision(
            setup,
            machine_outcome="insufficient_evidence",
            reviewer_outcome="insufficient_evidence",
        ),
        "ck_review_criterion_decisions_reviewer_outcome",
    )
    # Allow-controls: equal outcomes (a note is fine on an accept), and the override of
    # an insufficient criterion.
    _decision_on(tenant, "accept_equal")
    _decision_on(
        tenant, "accept_failed", machine_outcome="failed", reviewer_outcome="failed", note="ok"
    )
    _override_on(
        tenant,
        "accept_insufficient",
        machine_outcome="insufficient_evidence",
        reviewer_outcome="passed",
    )


def _assert_override_rules(tenant: dict) -> None:
    setup = _scored(tenant, "override")
    _refused(
        ReviewCriterionDecision,
        _decision(
            setup, decision="override", reviewer_outcome="passed", reason_code="other", note="x"
        ),
        "ck_review_criterion_decisions_override",
    )
    _refused(
        ReviewCriterionDecision,
        _override(setup, reason_code=None),
        "ck_review_criterion_decisions_override",
    )
    _override_on(tenant, "override_valid")


def _assert_one_decision_per_criterion(tenant: dict) -> None:
    key = "uq_review_criterion_decisions_criterion"
    setup = _scored(tenant, "one_per_criterion")
    _add(ReviewCriterionDecision, _decision(setup))
    _refused(ReviewCriterionDecision, _override(setup), key)
    # Another criterion of the same result, in the same item.
    second = _criterion(setup, setup["result_id"], 1)
    _add(CriterionResultRecord, second)
    _add(ReviewCriterionDecision, _decision(setup, criterion_id=second["criterion_id"]))
    # The same criterion in another item: another conversation with its own result.
    other = _scored(tenant, "one_per_criterion_other")
    _add(ReviewCriterionDecision, _decision(other))


DECISION_RULES = (
    _assert_decision_list_rules,
    _assert_outcome_list_rules,
    _assert_reason_code_rules,
    _assert_note_rules,
    _assert_accept_rules,
    _assert_override_rules,
    _assert_one_decision_per_criterion,
)


def _human_on(tenant: dict, key: str, **extra: object) -> str:
    """One valid human result on a fresh item of its own (one row per item)."""
    return _add(ReviewResultRecord, _human(_scored(tenant, key), **extra))


def _assert_human_outcome_rules(tenant: dict) -> None:
    setup = _scored(tenant, "human_outcome")
    _refused(
        ReviewResultRecord,
        _human(setup, outcome="maybe"),
        "ck_review_result_outcome",
        "ck_review_result_reason",
    )
    _refused(ReviewResultRecord, _human(setup, outcome="failed"), "ck_review_result_reason")
    _refused(
        ReviewResultRecord,
        _human(setup, outcome="failed", reason="other"),
        "ck_review_result_reason",
    )
    _refused(ReviewResultRecord, _human(setup, reason="auto_fail"), "ck_review_result_reason")
    _refused(
        ReviewResultRecord,
        _human(setup, outcome="not_applicable", reason="auto_fail"),
        "ck_review_result_reason",
    )
    _refused(
        ReviewResultRecord,
        _human(setup, outcome="insufficient_evidence", reason="auto_fail"),
        "ck_review_result_reason",
    )
    # Each outcome with its own reason, as the machine result's rules demand.
    _human_on(tenant, "human_outcome_passed")
    _human_on(
        tenant,
        "human_outcome_failed",
        outcome="failed",
        reason="auto_fail",
        failed_weight=100,
        earned_weight=0,
    )
    _human_on(
        tenant,
        "human_outcome_na",
        outcome="not_applicable",
        reason="no_applicable_criteria",
        applicable_weight=0,
        earned_weight=0,
    )
    _human_on(
        tenant,
        "human_outcome_insufficient",
        outcome="insufficient_evidence",
        reason="evidence_missing",
        complete=False,
        earned_weight=90,
        insufficient_weight=10,
    )


def _assert_human_number_rules(tenant: dict) -> None:
    setup = _scored(tenant, "human_numbers")
    for value in (0, 101):
        _refused(
            ReviewResultRecord,
            _human(setup, pass_threshold=value),
            "ck_review_result_threshold",
        )
    _refused(
        ReviewResultRecord,
        _human(setup, earned_weight=-1, failed_weight=1),
        "ck_review_result_weights",
    )
    _refused(
        ReviewResultRecord,
        _human(setup, failed_weight=1),
        "ck_review_result_weights",
    )
    _refused(
        ReviewResultRecord,
        _human(setup, applicable_weight=101, earned_weight=101),
        "ck_review_result_weights",
    )
    _refused(
        ReviewResultRecord,
        _human(setup, complete=True, insufficient_weight=10, earned_weight=90),
        "ck_review_result_complete",
    )
    # Each allow row on a fresh item (one human result per item).
    _human_on(tenant, "human_numbers_low", pass_threshold=1)
    _human_on(tenant, "human_numbers_high", pass_threshold=100)
    # Incomplete with no insufficient weight is allowed (a weight-0 criterion).
    _human_on(tenant, "human_numbers_incomplete", complete=False)


def _assert_one_human_result_per_item(tenant: dict) -> None:
    setup = _scored(tenant, "one_human_per_item")
    _add(ReviewResultRecord, _human(setup))
    _refused(
        ReviewResultRecord,
        _human(setup, outcome="failed", reason="auto_fail"),
        "uq_review_results_item",
    )
    other = _scored(tenant, "one_human_per_item_other")
    _add(ReviewResultRecord, _human(other))


RESULT_RULES = (
    _assert_human_outcome_rules,
    _assert_human_number_rules,
    _assert_one_human_result_per_item,
)


@pytest.mark.parametrize(
    "rules",
    (*DECISION_RULES, *RESULT_RULES),
    ids=lambda rules: rules.__name__.removeprefix("_assert_"),
)
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


def test_a_decision_names_only_its_items_result_scope_and_criterion(client, email_outbox):
    """The three composite foreign keys of a decision (SPEC keys and indexes).

    Every deny comes before the allows: a deny that also repeats an existing decision
    would trip the unique constraint first on SQLite.
    """
    tenant = _tenant(client, email_outbox, "scope-a")
    other = _tenant(client, email_outbox, "scope-b")
    setup = _scored(tenant, "scope")
    # A second result of the same conversation: a valid result, not its item's.
    second_result = _add(ScorecardResultRecord, _result(setup))
    _refused(
        ReviewCriterionDecision,
        _decision(setup, scorecard_result_id=second_result),
        "fk_review_criterion_decisions_item",
    )

    # Its result's own workspace and meeting, or none.
    elsewhere_meeting = _conversation(tenant)["meeting_id"]
    _refused(
        ReviewCriterionDecision,
        _decision(setup, meeting_id=elsewhere_meeting),
        "fk_review_criterion_decisions_result",
    )
    _refused(
        ReviewCriterionDecision,
        _decision(setup, workspace_id=other["workspace_id"]),
        "fk_review_criterion_decisions_result",
    )
    # Another workspace's result under this workspace's meeting.
    elsewhere = _scored(other, "scope_elsewhere")
    _add(CriterionResultRecord, _criterion(elsewhere, elsewhere["result_id"], 1))
    _refused(
        ReviewCriterionDecision,
        _decision(
            {**setup, "meeting_id": elsewhere["meeting_id"]},
            scorecard_result_id=elsewhere["result_id"],
        ),
        "fk_review_criterion_decisions_result",
        "fk_review_criterion_decisions_item",
    )

    # A criterion that is not one of that result's (``c1`` is only elsewhere's).
    _refused(
        ReviewCriterionDecision,
        _decision(setup, criterion_id="cx"),
        "fk_review_criterion_decisions_criterion",
    )
    _refused(
        ReviewCriterionDecision,
        _decision(setup, criterion_id="c1"),
        "fk_review_criterion_decisions_criterion",
    )

    # An item without a result has no decision: the pair cannot match a NULL.
    bare = _add(ReviewItem, _item_values(setup, status="completed", completed_at=NOW))
    _refused(
        ReviewCriterionDecision,
        _decision(setup, review_item_id=bare),
        "fk_review_criterion_decisions_item",
    )

    # Allow-controls: its item's result, another criterion of that result, another
    # item's own.
    _add(ReviewCriterionDecision, _decision(setup))
    _add(CriterionResultRecord, _criterion(setup, setup["result_id"], 1))
    _add(ReviewCriterionDecision, _decision(setup, criterion_id="c1"))
    _add(ReviewCriterionDecision, _decision(elsewhere))


def test_a_human_result_names_only_its_items_result(client, email_outbox):
    tenant = _tenant(client, email_outbox, "human_scope")
    setup = _scored(tenant, "human_scope")
    other = _scored(tenant, "human_scope_other")
    _refused(
        ReviewResultRecord,
        _human(setup, scorecard_result_id=other["result_id"]),
        "fk_review_results_item",
        "fk_review_results_scorecard_result",
    )
    _refused(
        ReviewResultRecord,
        _human(setup, review_item_id=other["item_id"]),
        "fk_review_results_item",
        "fk_review_results_scorecard_result",
    )
    _add(ReviewResultRecord, _human(setup))


# --- the ORM, the frozen lists and the RLS lists ------------------------------------------

DECISION_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "review_item_id": 36,
    "scorecard_result_id": 36,
    "criterion_id": 64,
    "decision": 16,
    "machine_outcome": 24,
    "reviewer_outcome": 24,
    "reason_code": 32,
    "decided_by_user_id": 36,
}
DECISION_NULLABLE = {"reason_code", "note", "decided_by_user_id"}
DECISION_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("decided_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_review_criterion_decisions_item",
        ("review_item_id", "scorecard_result_id"),
        ("review_items.id", "review_items.scorecard_result_id"),
        "CASCADE",
    ),
    (
        "fk_review_criterion_decisions_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
    (
        "fk_review_criterion_decisions_criterion",
        ("scorecard_result_id", "criterion_id"),
        ("scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"),
        "CASCADE",
    ),
}
DECISION_INDEXES = {
    "ix_review_criterion_decisions_workspace_meeting": (["workspace_id", "meeting_id"], False),
    "ix_review_criterion_decisions_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_review_criterion_decisions_decided_by_user_id": (["decided_by_user_id"], False),
}


def test_the_orm_declares_the_decisions_table():
    table = ReviewCriterionDecision.__table__
    assert table.name == DECISIONS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "review_item_id",
        "scorecard_result_id",
        "criterion_id",
        "decision",
        "machine_outcome",
        "reviewer_outcome",
        "reason_code",
        "note",
        "evidence",
        "decided_by_user_id",
        "created_at",
        "updated_at",
    ]
    for name, length in DECISION_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert table.c.note.type.__class__.__name__ == "Text"
    assert table.c.evidence.type.__class__.__name__ == "JSON"
    assert {column.name for column in table.columns if column.nullable} == DECISION_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]

    # The ORM defaults (SQLAlchemy wraps a callable default and keeps its name).
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.evidence.default.arg.__name__ == list.__name__
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == utcnow.__name__, name
    assert all(column.server_default is None for column in table.columns)

    assert {
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    } == DECISION_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == CHECKS
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_review_criterion_decisions_criterion"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == DECISION_INDEXES
    assert "HOM-74" in ReviewCriterionDecision.__doc__
    assert "purge" in ReviewCriterionDecision.__doc__


RESULT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "review_item_id": 36,
    "scorecard_result_id": 36,
    "scorecards_version": 40,
    "outcome": 24,
    "reason": 40,
}
RESULT_WEIGHTS = (
    "pass_threshold",
    "applicable_weight",
    "earned_weight",
    "failed_weight",
    "insufficient_weight",
)
RESULT_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        "fk_review_results_item",
        ("review_item_id", "scorecard_result_id"),
        ("review_items.id", "review_items.scorecard_result_id"),
        "CASCADE",
    ),
    (
        "fk_review_results_scorecard_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
}
RESULT_INDEXES = {
    "ix_review_results_workspace_meeting": (["workspace_id", "meeting_id"], False),
    "ix_review_results_scorecard_result_id": (["scorecard_result_id"], False),
}


def test_the_orm_declares_the_results_table():
    table = ReviewResultRecord.__table__
    assert table.name == RESULTS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
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
    assert table.c.reviewer_outcomes.type.__class__.__name__ == "JSON"
    assert table.c.result.type.__class__.__name__ == "JSON"
    assert table.c.complete.type.__class__.__name__ == "Boolean"
    for name in RESULT_WEIGHTS:
        assert table.c[name].type.__class__.__name__ == "Integer", name
    assert {column.name for column in table.columns if column.nullable} == {"reason"}
    assert [column.name for column in table.primary_key] == ["id"]

    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.created_at.default.arg.__name__ == utcnow.__name__
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
    # The outcome check is declared first, so SQLite names it when an unknown outcome
    # also breaks the reason rule: the ``outcome="maybe"`` deny above proves the order.
    assert [name for name in named if name.startswith("uq_")] == ["uq_review_results_item"]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == RESULT_INDEXES
    # No user column: the table is immutable (no UPDATE grant), so a SET NULL action on
    # a user column would need an update.
    assert [name for name in table.c.keys() if "user" in name] == []
    assert "HOM-74" in ReviewResultRecord.__doc__
    assert "immutable" in ReviewResultRecord.__doc__.lower()


def test_the_frozen_lists_and_the_check_strings():
    assert REVIEW_DECISIONS == ("accept", "override")
    assert REVIEW_REVIEWER_OUTCOMES == ("passed", "failed", "not_applicable")
    assert REVIEW_OVERRIDE_REASONS == (
        "evidence_missed",
        "evidence_misread",
        "transcript_error",
        "context_outside_call",
        "policy_exception",
        "other",
    )
    assert REVIEW_NOTE_MAX_LENGTH == 2000
    # The engine accepts exactly the reviewer outcomes a decision may store.
    assert REVIEW_REVIEWER_OUTCOMES == ENGINE_REVIEWER_OUTCOMES
    assert {
        "ck_review_criterion_decisions_decision": REVIEW_DECISION_CHECK,
        "ck_review_criterion_decisions_machine_outcome": REVIEW_DECISION_MACHINE_OUTCOME_CHECK,
        "ck_review_criterion_decisions_reviewer_outcome": REVIEW_DECISION_REVIEWER_OUTCOME_CHECK,
        "ck_review_criterion_decisions_reason_code": REVIEW_DECISION_REASON_CODE_CHECK,
        "ck_review_criterion_decisions_note_length": REVIEW_DECISION_NOTE_LENGTH_CHECK,
        "ck_review_criterion_decisions_accept": REVIEW_DECISION_ACCEPT_CHECK,
        "ck_review_criterion_decisions_override": REVIEW_DECISION_OVERRIDE_CHECK,
        "ck_review_criterion_decisions_criterion_key": CRITERION_KEY_CHECK,
    } == CHECKS
    for check in (*CHECKS.values(), *RESULT_CHECKS.values()):
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        # The user column is SET NULL on a user's removal; a check on it would make the
        # removal fail.
        assert "decided_by_user_id" not in check


def test_both_tables_are_workspace_tables_the_purge_can_delete():
    for table in TABLES:
        assert table in WORKSPACE_TABLES
        assert table not in APPEND_ONLY_TABLES
        assert table not in NO_DELETE_TABLES
        assert table not in SHARED_CONTENT_TABLES  # never through a share token
        assert table not in UNPROTECTED_TABLES
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    # The service of PR 2 upserts decisions; a stored human result never changes.
    assert DECISIONS not in NO_UPDATE_TABLES
    assert RESULTS in NO_UPDATE_TABLES
    assert NO_UPDATE_TABLES == (
        "scorecard_results",
        "scorecard_criterion_results",
        RESULTS,
        # HOM-75: a batch's example list is fixed at creation.
        "calibration_examples",
        # HOM-76: a stored score after an appeal never changes.
        "review_appeal_results",
        # HOM-93: a vector is written once; a new model writes a new row.
        "retrieval_embeddings",
    )


# --- immutability: the human result is written once ---------------------------------------


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


def test_the_orm_refuses_to_change_any_column_of_a_human_result(client, email_outbox):
    setup = _scored(_tenant(client, email_outbox, "orm_update"), "orm_update")
    row_id = _add(ReviewResultRecord, _human(setup))
    before = _snapshot(ReviewResultRecord, row_id)
    factory = inspection_session_factory()
    for column in ReviewResultRecord.__table__.columns:
        with factory() as db:
            row = db.get(ReviewResultRecord, row_id)
            setattr(row, column.key, _changed(getattr(row, column.key)))
            with pytest.raises(ReviewResultImmutableError, match=rf"attempted: {column.key}\)"):
                db.flush()
            db.rollback()

    # Allow-control: a flush that changes nothing.
    with factory() as db:
        row = db.get(ReviewResultRecord, row_id)
        row.outcome = row.outcome
        row.result = row.result
        db.flush()
        db.commit()
    assert _snapshot(ReviewResultRecord, row_id) == before


def test_the_orm_refuses_to_delete_a_human_result(client, email_outbox):
    setup = _scored(_tenant(client, email_outbox, "orm_delete"), "orm_delete")
    row_id = _add(ReviewResultRecord, _human(setup))
    with inspection_session_factory()() as db:
        db.delete(db.get(ReviewResultRecord, row_id))
        with pytest.raises(ReviewResultImmutableError, match="immutable"):
            db.flush()
        db.rollback()
    assert _ids(RESULTS) == {row_id}


def test_a_decision_is_not_immutable_the_service_of_pr_2_upserts_it(client, email_outbox):
    setup = _scored(_tenant(client, email_outbox, "orm_change"), "orm_change")
    row_id = _add(ReviewCriterionDecision, _decision(setup))
    with inspection_session_factory()() as db:
        row = db.get(ReviewCriterionDecision, row_id)
        row.decision = "override"
        row.reviewer_outcome = "failed"
        row.reason_code = "other"
        row.note = "changed my mind"
        row.updated_at = NOW + timedelta(minutes=1)
        db.commit()
    with inspection_session_factory()() as db:
        row = db.get(ReviewCriterionDecision, row_id)
        assert (row.decision, row.reviewer_outcome, row.reason_code) == (
            "override",
            "failed",
            "other",
        )
        assert _utc(row.updated_at) == NOW + timedelta(minutes=1)
        db.delete(row)
        db.commit()
    assert _ids(DECISIONS) == set()


# --- cascades and user removal ------------------------------------------------------------


def test_deleting_a_conversation_an_item_a_result_or_a_workspace_removes_the_rows(
    client, email_outbox
):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    gone = _scored(second, "cascade_gone")
    by_item = _scored(second, "cascade_item")
    by_result = _scored(second, "cascade_result")
    elsewhere = _scored(first, "cascade_elsewhere")
    decisions = {}
    humans = {}
    for label, setup in (
        ("gone", gone),
        ("by_item", by_item),
        ("by_result", by_result),
        ("elsewhere", elsewhere),
    ):
        decisions[label] = _add(ReviewCriterionDecision, _decision(setup))
        humans[label] = _add(ReviewResultRecord, _human(setup))

    # Removals by the owner (DBA here): the FK actions run as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(Meeting).where(Meeting.id == gone["meeting_id"]))
        db.execute(delete(ReviewItem).where(ReviewItem.id == by_item["item_id"]))
        db.execute(
            delete(ScorecardResultRecord).where(ScorecardResultRecord.id == by_result["result_id"])
        )
        db.commit()
    assert _ids(DECISIONS) == {decisions["elsewhere"]}
    assert _ids(RESULTS) == {humans["elsewhere"]}

    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    assert _ids(DECISIONS) == set()
    assert _ids(RESULTS) == set()


def test_deleting_a_user_clears_the_decider_and_keeps_the_decision(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user_removal")
    setup = _scored(tenant, "user_removal")
    with inspection_session_factory()() as db:
        user = User(email="review-decision-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    row_id = _add(ReviewCriterionDecision, _decision(setup, decided_by_user_id=user_id))
    human_id = _add(ReviewResultRecord, _human(setup))

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        row = db.get(ReviewCriterionDecision, row_id)
        assert row.decided_by_user_id is None
        # Everything else stays, the state rules included.
        assert (row.decision, row.machine_outcome, row.reviewer_outcome) == (
            "accept",
            "passed",
            "passed",
        )
        assert row.review_item_id == setup["item_id"]
    assert _ids(RESULTS) == {human_id}


# --- the migration (SQLite: the same tables as the ORM) -----------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_review_decisions")


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == (
        "Criterion decisions and the human result, schema only (HOM-74, WP14-T03)"
    )
    for words in ("Why", "No data changes", "Downgrade"):
        assert words in doc, words
    # Frozen copies of the model's check strings.
    assert module.CHECKS == CHECKS
    assert module.RESULT_CHECKS == RESULT_CHECKS


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    # HOM-75 moved the head past this revision; this one stays the only head's parent.
    assert code_migration_head() == HEAD_REVISION


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
    return {name: shape for name, shape in shapes.items() if name not in (ITEMS, *TABLES)}


def test_on_sqlite_the_migration_builds_the_orm_tables_the_item_index_and_drops_only_them():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            (item_index,) = [
                index for index in Base.metadata.tables[ITEMS].indexes if index.name == ITEM_INDEX
            ]
            item_index.drop(connection)
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
            assert sorted(name for name, _ in orm[DECISIONS]["checks"]) == sorted(CHECKS)
            assert sorted(name for name, _ in orm[RESULTS]["checks"]) == sorted(RESULT_CHECKS)
            # The review items index is back, and no other table changed.
            after = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert _untouched(after) == _untouched(before)
            assert (ITEM_INDEX, ("id", "scorecard_result_id"), True) in after[ITEMS]["indexes"]
            assert (ITEM_INDEX, ("id", "scorecard_result_id"), True) not in before[ITEMS]["indexes"]

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            final = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert final == before
            assert _objects(connection) == before_objects
            assert not any(
                name in {DECISIONS, RESULTS, ITEM_INDEX} for _, name, _ in _objects(connection)
            )
    finally:
        engine.dispose()


# --- PostgreSQL: the schema, the migration, isolation and grants --------------------------


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


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
    excluded = {"a": DECISIONS, "b": RESULTS, "c": ITEMS}
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return ("timestamp with time zone", None, nullable, None)


EXPECTED_DECISION_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "review_item_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "criterion_id": _varchar(64),
    "decision": _varchar(16),
    "machine_outcome": _varchar(24),
    "reviewer_outcome": _varchar(24),
    "reason_code": _varchar(32, "YES"),
    "note": ("text", None, "YES", None),
    "evidence": ("json", None, "NO", None),
    "decided_by_user_id": _varchar(36, "YES"),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
}

EXPECTED_RESULT_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "review_item_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "scorecards_version": _varchar(40),
    "reviewer_outcomes": ("json", None, "NO", None),
    "outcome": _varchar(24),
    "reason": _varchar(40, "YES"),
    "complete": ("boolean", None, "NO", None),
    "pass_threshold": ("integer", None, "NO", None),
    "applicable_weight": ("integer", None, "NO", None),
    "earned_weight": ("integer", None, "NO", None),
    "failed_weight": ("integer", None, "NO", None),
    "insufficient_weight": ("integer", None, "NO", None),
    "result": ("json", None, "NO", None),
    "created_at": _timestamp(),
}


def _foreign_key(table: str, column: str, target: str, action: str) -> tuple[str, str]:
    return (
        f"{table}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}",
    )


EXPECTED_DECISION_CONSTRAINTS = dict(
    [
        (f"{DECISIONS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(DECISIONS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(DECISIONS, "meeting_id", "meetings", "CASCADE"),
        _foreign_key(DECISIONS, "decided_by_user_id", "users", "SET NULL"),
        (
            "fk_review_criterion_decisions_item",
            "FOREIGN KEY (review_item_id, scorecard_result_id) REFERENCES "
            "review_items(id, scorecard_result_id) ON DELETE CASCADE",
        ),
        (
            "fk_review_criterion_decisions_result",
            "FOREIGN KEY (scorecard_result_id, workspace_id, meeting_id) REFERENCES "
            "scorecard_results(id, workspace_id, meeting_id) ON DELETE CASCADE",
        ),
        (
            "fk_review_criterion_decisions_criterion",
            "FOREIGN KEY (scorecard_result_id, criterion_id) REFERENCES "
            "scorecard_criterion_results(result_id, criterion_id) ON DELETE CASCADE",
        ),
        ("uq_review_criterion_decisions_criterion", "UNIQUE (review_item_id, criterion_id)"),
    ]
)

EXPECTED_RESULT_CONSTRAINTS = dict(
    [
        (f"{RESULTS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(RESULTS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(RESULTS, "meeting_id", "meetings", "CASCADE"),
        (
            "fk_review_results_item",
            "FOREIGN KEY (review_item_id, scorecard_result_id) REFERENCES "
            "review_items(id, scorecard_result_id) ON DELETE CASCADE",
        ),
        (
            "fk_review_results_scorecard_result",
            "FOREIGN KEY (scorecard_result_id, workspace_id, meeting_id) REFERENCES "
            "scorecard_results(id, workspace_id, meeting_id) ON DELETE CASCADE",
        ),
        ("uq_review_results_item", "UNIQUE (review_item_id)"),
    ]
)


def _index(name: str, table: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_DECISION_INDEXES = dict(
    [
        _index(f"{DECISIONS}_pkey", DECISIONS, "id", unique=True),
        _index(
            "ix_review_criterion_decisions_workspace_meeting",
            DECISIONS,
            "workspace_id, meeting_id",
        ),
        _index(
            "ix_review_criterion_decisions_scorecard_result_id", DECISIONS, "scorecard_result_id"
        ),
        _index("ix_review_criterion_decisions_decided_by_user_id", DECISIONS, "decided_by_user_id"),
        _index(
            "uq_review_criterion_decisions_criterion",
            DECISIONS,
            "review_item_id, criterion_id",
            unique=True,
        ),
    ]
)

EXPECTED_RESULT_INDEXES = dict(
    [
        _index(f"{RESULTS}_pkey", RESULTS, "id", unique=True),
        _index("ix_review_results_workspace_meeting", RESULTS, "workspace_id, meeting_id"),
        _index("ix_review_results_scorecard_result_id", RESULTS, "scorecard_result_id"),
        _index("uq_review_results_item", RESULTS, "review_item_id", unique=True),
    ]
)


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    if table == DECISIONS:
        assert shape["columns"] == EXPECTED_DECISION_COLUMNS
        expected_constraints = EXPECTED_DECISION_CONSTRAINTS
        expected_indexes = EXPECTED_DECISION_INDEXES
        checks = CHECKS
    else:
        assert shape["columns"] == EXPECTED_RESULT_COLUMNS
        expected_constraints = EXPECTED_RESULT_CONSTRAINTS
        expected_indexes = EXPECTED_RESULT_INDEXES
        checks = RESULT_CHECKS
    constraints = dict(shape["constraints"])
    named_checks = {name for name in constraints if name.startswith("ck_")}
    assert named_checks == set(checks)  # their behaviour is pinned by the RULES above
    for name in named_checks:
        constraints.pop(name)
    assert constraints == expected_constraints
    assert shape["indexes"] == expected_indexes
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    if table == DECISIONS:
        # The service of PR 2 upserts decisions, the purge deletes them.
        assert shape["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"]
    else:
        # A stored human result never changes; the purge deletes it.
        assert shape["grants"] == ["DELETE", "INSERT", "SELECT"]
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_two_tables(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_two_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    setup = _scored(tenant, "migration")
    _add(ReviewCriterionDecision, _decision(setup))
    _add(ReviewResultRecord, _human(setup))
    results = _scalar("SELECT count(*) FROM scorecard_results")
    meetings = _scalar("SELECT count(*) FROM meetings")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()  # the head's own tables included
        # Down to this revision first: the HOM-75 head's five calibration tables go
        # with it, so the snapshot below covers only what this migration owns.
        command.downgrade(config, NEW_REVISION)
        others = _other_tables()
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}")
            assert _shape(table) == EMPTY_SHAPE
        # The upgrade's own index on review items went with the two tables.
        assert (
            _scalar("SELECT indexname FROM pg_indexes WHERE indexname = :i", i=ITEM_INDEX) is None
        )
        assert _other_tables() == others  # every other table as it was
        # Only the decisions and the human results are lost: the rest stays.
        assert _scalar("SELECT count(*) FROM scorecard_results") == results
        assert _scalar("SELECT count(*) FROM meetings") == meetings

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others

        # The migrated schema refuses the same deny cases.
        for rules in (*DECISION_RULES, *RESULT_RULES):
            rules(tenant)
        migrated = _scored(tenant, "migration_migrated")
        _add(ReviewCriterionDecision, _decision(migrated))
        _add(ReviewResultRecord, _human(migrated))

        # Down and up again with rows present, then on to the head.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        command.upgrade(config, "head")
        assert (
            _scalar("SELECT indexname FROM pg_indexes WHERE indexname = :i", i=ITEM_INDEX)
            is not None
        )
        # Back at the head, with its HOM-75 tables again.
        assert _other_tables() == head_others
    finally:
        reset_settings_cache()


def _write_rows(db, setup: dict) -> None:
    db.add(ReviewCriterionDecision(**_decision(setup)))
    db.add(ReviewResultRecord(**_human(setup)))
    db.flush()


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _scored(_tenant(client, email_outbox, "tenant_a"), "tenant_a")
    second = _scored(_tenant(client, email_outbox, "tenant_b"), "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        _write_rows(db, first)
        db.commit()

    # Bound to B: none of A's rows; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
            assert (
                db.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                    {"w": first["workspace_id"]},
                )
                == 0
            )
        _write_rows(db, second)
        db.commit()

    # Bound to A: only A's rows; an insert with B's ids fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]]
    for model, values in (
        (ReviewCriterionDecision, _decision(second)),
        (ReviewResultRecord, _human(second)),
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
def test_the_app_role_changes_decisions_and_never_updates_human_results(client, email_outbox):
    _requires_postgres()
    first = _scored(_tenant(client, email_outbox, "grants_a"), "grants_a")
    second = _scored(_tenant(client, email_outbox, "grants_b"), "grants_b")
    decision_grants = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    result_grants = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    assert _privileges(DECISIONS) == decision_grants
    assert _privileges(RESULTS) == result_grants

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        _write_rows(db, first)
        db.commit()

    # Bound to B: A's rows are invisible, so an update or a delete changes none of them.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for statement in (
            f"UPDATE {DECISIONS} SET decision = 'override'",  # noqa: S608
            f"DELETE FROM {DECISIONS}",  # noqa: S608
            f"DELETE FROM {RESULTS}",  # noqa: S608
        ):
            assert db.execute(text(statement)).rowcount == 0
        db.commit()
    assert len(_ids(DECISIONS)) == 1
    assert len(_ids(RESULTS)) == 1

    # Bound to A: the upsert of PR 2 on the decisions, no UPDATE of the human results,
    # and still none after the policies and the grants are applied again.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {DECISIONS} SET decision = 'override', reviewer_outcome = 'failed', "  # noqa: S608
                "reason_code = 'other', note = 'x' WHERE workspace_id = :w"
            ),
            {"w": first["workspace_id"]},
        )
        assert updated.rowcount == 1
        assert db.scalar(text(f"SELECT count(*) FROM {RESULTS}")) == 1  # noqa: S608
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(f"UPDATE {RESULTS} SET outcome = 'failed'"))  # noqa: S608
        db.rollback()
        db.commit()
    _apply_as_owner()  # idempotent: no revoke for these tables
    assert _privileges(DECISIONS) == decision_grants
    assert _privileges(RESULTS) == result_grants

    # Allow-control, bound to A: the purge's own statements delete both.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        meeting = db.get(Meeting, first["meeting_id"])
        _remove_content(db, workspace_id=first["workspace_id"], meeting=meeting)
        db.commit()
    for table in TABLES:
        assert _ids(table) == set()


# --- the conversation purge and the backup reconciliation --------------------------------


def test_the_purge_deletes_both_tables_before_the_items_and_the_results_and_counts_them(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "purge-order")
    setup = _scored(tenant, "ordered")
    _add(ReviewCriterionDecision, _decision(setup))
    _add(ReviewResultRecord, _human(setup))
    kept = _scored(tenant, "kept")
    kept_decision = _add(ReviewCriterionDecision, _decision(kept))
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
    # The decisions and the human results go first: the items' and the results' cascades
    # would otherwise remove them uncounted.
    assert deletes.index(DECISIONS) < deletes.index(ITEMS)
    assert deletes.index(RESULTS) < deletes.index(ITEMS)
    assert deletes.index(ITEMS) < deletes.index("scorecard_results")
    # One decision, one human result, one item, one result, one criterion row and the
    # transcript revision.
    assert removed == 6
    assert _ids(DECISIONS) == {kept_decision}
    assert len(_ids(RESULTS, meeting_id=kept["meeting_id"])) == 0  # kept has no human result


def _decided(tenant: dict, meeting_id: str, key: str) -> None:
    """A stored result, one item on it, a decision and a human result."""
    setup = _scored(tenant, key, meeting_id=meeting_id)
    _add(ReviewCriterionDecision, _decision(setup))
    _add(ReviewResultRecord, _human(setup))


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "decision-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"decision-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    for meeting_id, key in ((queued, "decision_purged"), (kept, "decision_kept")):
        _decided(tenant, meeting_id, key)
    assert len(_ids(DECISIONS)) == 2 and len(_ids(RESULTS)) == 2

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    assert _ids(DECISIONS, meeting_id=queued) == set()
    assert _ids(RESULTS, meeting_id=queued) == set()
    # Another conversation of the workspace keeps its rows.
    assert len(_ids(DECISIONS, meeting_id=kept)) == 1
    assert len(_ids(RESULTS, meeting_id=kept)) == 1
    # The purge deletes the decision, the human result, the item, the result and its
    # criterion row itself (they are counted), before the result's cascade could.
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 5


def test_a_held_conversation_keeps_its_rows_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "decision-hold")
    meeting_id = _processed(client, tenant, settings, key="decision-hold-0001")
    _decided(tenant, meeting_id, "decision_held")
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-8", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    # The deletion of a held conversation: the purge waits and the rows stay.
    _delete_conversation(client, tenant, meeting_id)
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == ERROR_LEGAL_HOLD
    assert len(_ids(DECISIONS, meeting_id=meeting_id)) == 1
    assert len(_ids(RESULTS, meeting_id=meeting_id)) == 1
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    assert len(_ids(DECISIONS, meeting_id=meeting_id)) == 1

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
    assert _ids(DECISIONS, meeting_id=meeting_id) == set()
    assert _ids(RESULTS, meeting_id=meeting_id) == set()


def test_the_backup_reconciliation_counts_both_tables():
    for table in TABLES:
        assert table in RECONCILED_TABLES
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns


# --- the ADR (SPEC criterion 10) -----------------------------------------------------------


def _adr() -> str:
    from backend.observability.release import PROJECT_ROOT

    path = (
        PROJECT_ROOT.parent
        / "docs"
        / "architecture"
        / "decisions"
        / "0053-criterion-override-and-human-result.md"
    )
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0053: ")
    assert "Proposed (HOM-74, WP14-T03, F44)" in content
    assert "ADR 0051" in content and "ADR 0052" in content
    for heading in ("Context", "Decision", "Consequences", "Not built"):
        assert re.search(rf"^## {heading}\s*$", content, re.MULTILINE), heading
    for section in ("D1", "D2", "D3", "D4", "D5", "D6"):
        assert re.search(rf"^### {section}\.", content, re.MULTILINE), section


# What the SPEC says the ADR records; any one spelling of a topic is enough.
ADR_TOPICS = {
    "the_tables": ("review_criterion_decisions", "review_results"),
    "the_lists": ("evidence_missed",),
    "machine_kept": ("machine score", "never changes", "immutable"),
    "item_index": (ITEM_INDEX,),
    "write_rules": ("review:manage", "expected_revision", "review_item_revision_conflict"),
    "route": ("/criteria/{criterion_id}/decision",),
    "completion": ("reviewer_outcomes",),
    "purge": ("purge",),
    "backup": ("reconcil",),
    "audit": ("review_criterion.decided",),
    "not_built": ("F46", "F47"),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings
