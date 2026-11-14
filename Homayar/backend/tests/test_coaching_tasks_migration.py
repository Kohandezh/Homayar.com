"""HOM-77 PR 1: coaching tasks from accepted findings and their one migration.

One table, ``coaching_tasks``: one task names exactly one finding (one criterion
of one review item that has a human result), with an owner, a due date, the
agent's acknowledgement and the owner's follow-up review.  Nothing writes the
table yet; PR 2 adds the supervisor and agent routes, the audit events, the
notifications and the reminder sweep.  The tests check:

* the ORM declares the columns, the named constraints, the composite foreign
  keys and the indexes of the design, the frozen lists and the check strings,
  which use no regex and never name a user column;
* ``database/rls.py`` lists the table as a workspace table with the full grants,
  in none of the append-only, no-update, no-delete or share lists;
* every deny case of the SPEC is refused by its named constraint, and the closest
  allow case passes (SQLite and PostgreSQL): the status list, the revision, the
  finding source list, the instruction rules, the acknowledgement note rules,
  the follow-up outcome list, the note lengths, the cancel reason list, the
  per-status field rules and the criterion key;
* every composite key refuses a cross-scope row and accepts the in-scope one;
* at most one active task per finding: a closed or cancelled task does not block
  a new one, and another criterion of the same item is free;
* cascades: deleting a review item, a scorecard result, a conversation or a
  workspace removes the task rows; deleting a user clears every user column of
  ``coaching_tasks`` and keeps the row;
* on SQLite, the migration builds the same table as the ORM, and its downgrade
  removes it and nothing else;
* on PostgreSQL: the ORM schema (``create_all``) and the migrated schema are the
  same; downgrade from the head removes the table and leaves every other table
  as it was; upgrade recreates it; tenant isolation (also on write); a
  share-token context sees none of it; the application role may SELECT, INSERT,
  UPDATE and DELETE;
* the conversation purge deletes the coaching tasks of the purged conversation
  only, before the appeal rows, the review decisions, the human results and the
  review items whose cascades would remove them uncounted, counts them, and a
  legal hold keeps them; the backup reconciliation counts the table; the code
  has one migration head, this one; ADR 0062 records the design.
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
    COACHING_CANCEL_REASONS,
    COACHING_FINDING_SOURCES,
    COACHING_FOLLOW_UP_OUTCOMES,
    COACHING_MAX_DUE_DAYS,
    COACHING_REMINDER_LEAD_HOURS,
    COACHING_TASK_ACTIVE_STATUSES,
    COACHING_TASK_ACTIVE_WHERE,
    COACHING_TASK_STATUSES,
    CRITERION_KEY_CHECK,
    REVIEW_NOTE_MAX_LENGTH,
    CoachingTask,
    CriterionResultRecord,
    Meeting,
    PurgeState,
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
from backend.services.conversation_purge_service import ERROR_LEGAL_HOLD, _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_action_proposals_migration import remove_action_proposals
from backend.tests.test_federation import remove_federation_group_mappings
from backend.tests.test_hom129_mfa_migration import remove_privileged_mfa_schema
from backend.tests.test_ingestion_ledger import _headers, _patch_media
from backend.tests.test_ingestion_ledger import _tenant as _ledger_tenant
from backend.tests.test_product_alerts_migration import remove_product_alerts
from backend.tests.test_retention_purge import _media, _processed, _purge, _review, _sweep
from backend.tests.test_retrieval_index_migration import remove_retrieval_index_tables
from backend.tests.test_review_decisions_migration import _decision, _human, _item_values
from backend.tests.test_scorecard_results_migration import (
    _add,
    _criterion,
    _delete_conversation,
    _parents,
    _result,
)

NEW_REVISION = "5f3a91c0d7b2"
BASE_REVISION = "c4a8e2f7d913"  # HOM-133: action item kind
HEAD_REVISION = "1b1144f1d95b"  # HOM-134: product alerts, after HOM-135
TABLE = "coaching_tasks"
TABLES = (TABLE,)
APPEALS = "review_appeals"
HUMAN_RESULTS = "review_results"
ITEMS = "review_items"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
# The check strings of the SPEC, character for character.
CHECKS = {
    "ck_coaching_tasks_status": "status IN ('assigned','acknowledged','closed','cancelled')",
    "ck_coaching_tasks_revision": "revision >= 1",
    "ck_coaching_tasks_finding_source": "finding_source IN ('human_result','appeal_result')",
    "ck_coaching_tasks_instruction": (
        "length(trim(instruction)) > 0 AND length(instruction) <= 2000"
    ),
    "ck_coaching_tasks_acknowledgement_note": (
        "acknowledgement_note IS NULL OR (length(acknowledgement_note) <= 2000 "
        "AND acknowledged_at IS NOT NULL)"
    ),
    "ck_coaching_tasks_follow_up_outcome": (
        "follow_up_outcome IS NULL OR follow_up_outcome IN "
        "('improved','partially_improved','not_improved')"
    ),
    "ck_coaching_tasks_follow_up_note_length": (
        "follow_up_note IS NULL OR length(follow_up_note) <= 2000"
    ),
    "ck_coaching_tasks_cancel_reason": (
        "cancel_reason IS NULL OR cancel_reason IN "
        "('finding_overturned','agent_changed','duplicate','other')"
    ),
    "ck_coaching_tasks_cancel_note_length": ("cancel_note IS NULL OR length(cancel_note) <= 2000"),
    "ck_coaching_tasks_assigned_fields": (
        "status <> 'assigned' OR (acknowledged_at IS NULL AND follow_up_outcome IS NULL "
        "AND follow_up_note IS NULL AND closed_at IS NULL AND cancel_reason IS NULL "
        "AND cancel_note IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_coaching_tasks_acknowledged_fields": (
        "status <> 'acknowledged' OR (acknowledged_at IS NOT NULL AND follow_up_outcome "
        "IS NULL AND follow_up_note IS NULL AND closed_at IS NULL AND cancel_reason "
        "IS NULL AND cancel_note IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_coaching_tasks_closed_fields": (
        "status <> 'closed' OR (acknowledged_at IS NOT NULL AND closed_at IS NOT NULL "
        "AND follow_up_outcome IS NOT NULL AND follow_up_note IS NOT NULL "
        "AND length(trim(follow_up_note)) > 0 AND cancel_reason IS NULL "
        "AND cancel_note IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_coaching_tasks_cancelled_fields": (
        "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
        "AND closed_at IS NULL AND follow_up_outcome IS NULL AND follow_up_note IS NULL)"
    ),
    "ck_coaching_tasks_criterion_key": CRITERION_KEY_CHECK,
}
# The partial unique index's WHERE clause, character for character.
ACTIVE_WHERE = "status IN ('assigned','acknowledged')"
# SQLite names the columns of a broken unique constraint, not the constraint.
UNIQUE_COLUMNS = {
    "uq_coaching_tasks_active_finding": (TABLE, ("review_item_id", "criterion_id")),
}
USER_COLUMNS = (
    "agent_user_id",
    "owner_user_id",
    "created_by_user_id",
    "closed_by_user_id",
    "cancelled_by_user_id",
)


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"coaching-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _conversation(tenant: dict) -> dict:
    """A new conversation of the tenant, without a transcript or a score."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="Coached call",
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

    The smallest setup a coaching task needs: the item names the result, and the
    human result exists.  With ``meeting_id`` the conversation keeps its current
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


def _task(setup: dict, **extra: object) -> dict[str, object]:
    """A valid assigned task on the setup's reviewed item, coached by its creator."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "review_item_id": setup["item_id"],
        "scorecard_result_id": setup["result_id"],
        "criterion_id": setup["criterion_id"],
        "finding_source": "human_result",
        "agent_user_id": setup["user_id"],
        "owner_user_id": setup["user_id"],
        "created_by_user_id": setup["user_id"],
        "instruction": "Practise the opening question.",
        "due_at": NOW + timedelta(days=3),
        "status": "assigned",
        "revision": 1,
        **extra,
    }


def _acknowledged(setup: dict, **extra: object) -> dict[str, object]:
    """A valid acknowledged task: the agent confirmed it; ``extra`` wins."""
    values = _task(setup, status="acknowledged", acknowledged_at=NOW)
    values.update(extra)
    return values


def _closed(setup: dict, **extra: object) -> dict[str, object]:
    """A valid closed task: acknowledged, then reviewed as improved; ``extra`` wins."""
    values = _acknowledged(
        setup,
        status="closed",
        closed_at=NOW + timedelta(minutes=5),
        closed_by_user_id=setup["user_id"],
        follow_up_outcome="improved",
        follow_up_note="Fixed.",
    )
    values.update(extra)
    return values


def _cancelled(setup: dict, **extra: object) -> dict[str, object]:
    """A valid cancelled task: a duplicate; ``extra`` wins."""
    values = _task(
        setup,
        status="cancelled",
        cancel_reason="duplicate",
        cancel_note="Already coached.",
        cancelled_at=NOW + timedelta(minutes=5),
        cancelled_by_user_id=setup["user_id"],
    )
    values.update(extra)
    return values


def _task_on(tenant: dict, key: str, build=_task, **extra: object) -> str:
    """A stored valid task on a fresh reviewed item of its own (one per finding).

    ``build`` is the state to start from (``_task``, ``_acknowledged``, ``_closed`` or
    ``_cancelled``); ``extra`` wins over its values.
    """
    return _add(CoachingTask, build(_reviewed(tenant, key), **extra))


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


# --- the deny and allow cases (SPEC "Deny-cases and their allow-controls") ------------------


def _assert_status_list_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "status_list")
    for value in ("open", "Assigned", "assigned ", ""):
        _refused(CoachingTask, _task(setup, status=value), "ck_coaching_tasks_status")
    _task_on(tenant, "status_assigned", status="assigned")


def _assert_revision_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "revision")
    _refused(CoachingTask, _task(setup, revision=0), "ck_coaching_tasks_revision")
    _task_on(tenant, "revision_one", revision=1)
    _task_on(tenant, "revision_two", revision=2)


def _assert_finding_source_list_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "finding_source_list")
    # A machine word is not a finding source; the two stored words are.
    for value in ("machine", "Human_result", "human ", ""):
        _refused(
            CoachingTask, _task(setup, finding_source=value), "ck_coaching_tasks_finding_source"
        )
    _task_on(tenant, "finding_source_human", finding_source="human_result")
    _task_on(tenant, "finding_source_appeal", finding_source="appeal_result")


def _assert_instruction_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "instruction")
    for value in ("", "   "):
        _refused(CoachingTask, _task(setup, instruction=value), "ck_coaching_tasks_instruction")
    # A Text column delivers every character to the check on both databases.
    _refused(CoachingTask, _task(setup, instruction="x" * 2001), "ck_coaching_tasks_instruction")
    _task_on(tenant, "instruction_one", instruction="x")
    _task_on(tenant, "instruction_2000", instruction="x" * 2000)


def _assert_acknowledgement_note_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "acknowledgement_note")
    # A note without the acknowledgement itself.
    _refused(
        CoachingTask,
        _task(setup, acknowledgement_note="Will do."),
        "ck_coaching_tasks_acknowledgement_note",
    )
    _refused(
        CoachingTask,
        _acknowledged(setup, acknowledgement_note="x" * 2001),
        "ck_coaching_tasks_acknowledgement_note",
    )
    # The same note with the acknowledgement set, up to the length.
    _task_on(
        tenant, "acknowledgement_note_valid", build=_acknowledged, acknowledgement_note="Will do."
    )
    _task_on(
        tenant,
        "acknowledgement_note_2000",
        build=_acknowledged,
        acknowledgement_note="x" * 2000,
    )


def _assert_assigned_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "assigned_fields")
    _refused(CoachingTask, _task(setup, acknowledged_at=NOW), "ck_coaching_tasks_assigned_fields")
    for column, value in (
        ("follow_up_outcome", "improved"),
        ("follow_up_note", "x"),
        ("closed_at", NOW),
        ("cancel_reason", "other"),
        ("cancel_note", "x"),
        ("cancelled_at", NOW),
    ):
        _refused(
            CoachingTask,
            _task(setup, **{column: value}),
            "ck_coaching_tasks_assigned_fields",
        )
    _task_on(tenant, "assigned_fields_valid")


def _assert_acknowledged_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "acknowledged_fields")
    # An acknowledged task needs the acknowledgement itself.
    _refused(
        CoachingTask,
        _task(setup, status="acknowledged"),
        "ck_coaching_tasks_acknowledged_fields",
    )
    # And none of the later fields.
    for column, value in (
        ("follow_up_outcome", "improved"),
        ("follow_up_note", "x"),
        ("closed_at", NOW),
        ("cancel_reason", "other"),
        ("cancel_note", "x"),
        ("cancelled_at", NOW),
    ):
        _refused(
            CoachingTask,
            _acknowledged(setup, **{column: value}),
            "ck_coaching_tasks_acknowledged_fields",
        )
    # Allow: acknowledged with the acknowledgement set.
    _task_on(tenant, "acknowledged_fields_valid", build=_acknowledged)


def _assert_closed_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "closed_fields")
    # A follow-up review needs an acknowledged task, a close time, an outcome and
    # a non-blank note, and no cancel fields.
    _refused(CoachingTask, _closed(setup, acknowledged_at=None), "ck_coaching_tasks_closed_fields")
    _refused(CoachingTask, _closed(setup, closed_at=None), "ck_coaching_tasks_closed_fields")
    _refused(
        CoachingTask, _closed(setup, follow_up_outcome=None), "ck_coaching_tasks_closed_fields"
    )
    _refused(CoachingTask, _closed(setup, follow_up_note=None), "ck_coaching_tasks_closed_fields")
    _refused(CoachingTask, _closed(setup, follow_up_note="   "), "ck_coaching_tasks_closed_fields")
    _refused(CoachingTask, _closed(setup, cancel_reason="other"), "ck_coaching_tasks_closed_fields")
    _refused(CoachingTask, _closed(setup, cancel_note="x"), "ck_coaching_tasks_closed_fields")
    _refused(CoachingTask, _closed(setup, cancelled_at=NOW), "ck_coaching_tasks_closed_fields")
    # Allow: closed with the acknowledgement, outcome ``improved`` and note ``x``, and
    # no cancel fields.
    _task_on(
        tenant,
        "closed_fields_valid",
        build=_closed,
        follow_up_outcome="improved",
        follow_up_note="x",
    )


def _assert_cancelled_fields_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "cancelled_fields")
    _refused(
        CoachingTask, _cancelled(setup, cancel_reason=None), "ck_coaching_tasks_cancelled_fields"
    )
    _refused(
        CoachingTask, _cancelled(setup, cancelled_at=None), "ck_coaching_tasks_cancelled_fields"
    )
    for column, value in (
        ("follow_up_outcome", "improved"),
        ("follow_up_note", "x"),
        ("closed_at", NOW),
    ):
        _refused(
            CoachingTask,
            _cancelled(setup, **{column: value}),
            "ck_coaching_tasks_cancelled_fields",
        )
    # Allow: cancelled with both, and no acknowledgement...
    _task_on(tenant, "cancelled_fields_valid", build=_cancelled)
    # ...and with one (a task can be cancelled after the agent acknowledged it).
    _task_on(
        tenant,
        "cancelled_fields_acknowledged",
        build=_cancelled,
        acknowledged_at=NOW,
        acknowledgement_note="Seen.",
        cancel_reason="agent_changed",
    )


def _assert_follow_up_outcome_list_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "outcome_list")
    for value in ("great", "Improved", "improved ", ""):
        _refused(
            CoachingTask,
            _closed(setup, follow_up_outcome=value),
            "ck_coaching_tasks_follow_up_outcome",
        )
    for value in ("improved", "partially_improved", "not_improved"):
        _task_on(tenant, f"outcome_list_{value}", build=_closed, follow_up_outcome=value)


def _assert_cancel_reason_list_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "cancel_reason_list")
    for value in ("bored", "Duplicate", "duplicate ", ""):
        _refused(
            CoachingTask,
            _cancelled(setup, cancel_reason=value),
            "ck_coaching_tasks_cancel_reason",
        )
    for value in ("finding_overturned", "agent_changed", "duplicate", "other"):
        _task_on(tenant, f"cancel_reason_list_{value}", build=_cancelled, cancel_reason=value)


def _assert_note_length_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "note_lengths")
    _refused(
        CoachingTask,
        _closed(setup, follow_up_note="x" * 2001),
        "ck_coaching_tasks_follow_up_note_length",
    )
    _refused(
        CoachingTask,
        _cancelled(setup, cancel_note="x" * 2001),
        "ck_coaching_tasks_cancel_note_length",
    )
    _refused(
        CoachingTask,
        _acknowledged(setup, acknowledgement_note="x" * 2001),
        "ck_coaching_tasks_acknowledgement_note",
    )
    # The same notes at 2000 characters.
    _task_on(
        tenant,
        "note_length_follow_up",
        build=_closed,
        follow_up_outcome="not_improved",
        follow_up_note="x" * 2000,
    )
    _task_on(tenant, "note_length_cancel", build=_cancelled, cancel_note="x" * 2000)
    _task_on(
        tenant,
        "note_length_acknowledgement",
        build=_acknowledged,
        acknowledgement_note="x" * 2000,
    )


def _assert_criterion_key_rules(tenant: dict) -> None:
    setup = _reviewed(tenant, "criterion_key")
    # A check runs before the foreign keys on both databases, so the key rule names
    # the refusal even though no criterion row has these ids.
    for value in ("C0", "c", "c-0", "0c", ""):
        _refused(CoachingTask, _task(setup, criterion_id=value), "ck_coaching_tasks_criterion_key")
    # Allow: a valid key of the result (``c0``, the setup's own criterion).
    _task_on(tenant, "criterion_key_valid")


RULES = (
    _assert_status_list_rules,
    _assert_revision_rules,
    _assert_finding_source_list_rules,
    _assert_instruction_rules,
    _assert_acknowledgement_note_rules,
    _assert_assigned_fields_rules,
    _assert_acknowledged_fields_rules,
    _assert_closed_fields_rules,
    _assert_cancelled_fields_rules,
    _assert_follow_up_outcome_list_rules,
    _assert_cancel_reason_list_rules,
    _assert_note_length_rules,
    _assert_criterion_key_rules,
)


@pytest.mark.parametrize(
    "rules",
    RULES,
    ids=lambda rules: rules.__name__.removeprefix("_assert_"),
)
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


# --- the composite keys and the one active task per finding ---------------------------------


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


def test_a_task_names_only_its_items_result_the_results_scope_and_a_reviewed_item(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "scope-a")
    other = _tenant(client, email_outbox, "scope-b")
    setup = _reviewed(tenant, "scope")
    # A second result of the same conversation, with the same criterion: a valid
    # result and criterion, but not the item's result.
    second_result = _add(ScorecardResultRecord, _result(setup))
    _add(CriterionResultRecord, _criterion(setup, second_result, 0))
    _refused(
        CoachingTask, _task(setup, scorecard_result_id=second_result), "fk_coaching_tasks_item"
    )
    # Its result's own workspace and meeting, or none.
    elsewhere_meeting = _conversation(tenant)["meeting_id"]
    _refused(CoachingTask, _task(setup, meeting_id=elsewhere_meeting), "fk_coaching_tasks_result")
    _refused(
        CoachingTask, _task(setup, workspace_id=other["workspace_id"]), "fk_coaching_tasks_result"
    )
    # A criterion that is not one of the result's.
    _refused(CoachingTask, _task(setup, criterion_id="cx"), "fk_coaching_tasks_criterion")
    # An item with no human result: only a reviewed item can be coached.
    unreviewed = _scored_in_review(tenant, "scope_unreviewed")
    _refused(CoachingTask, _task(unreviewed), "fk_coaching_tasks_review_result")
    # Allow-control: its own item, result, criterion, workspace and meeting.
    _add(CoachingTask, _task(setup))


def test_at_most_one_active_task_per_finding(client, email_outbox):
    tenant = _tenant(client, email_outbox, "active")
    # A second assigned task for the same item and criterion.
    setup = _reviewed(tenant, "active_assigned")
    _add(CoachingTask, _task(setup))
    _refused(
        CoachingTask, _task(setup, instruction="A second try."), "uq_coaching_tasks_active_finding"
    )
    # An acknowledged task blocks a new one as well.
    acknowledged = _reviewed(tenant, "active_acknowledged")
    _add(CoachingTask, _acknowledged(acknowledged))
    _refused(
        CoachingTask,
        _task(acknowledged, instruction="A second try."),
        "uq_coaching_tasks_active_finding",
    )
    # A closed task does not block a new one.
    closed = _reviewed(tenant, "active_closed")
    _add(CoachingTask, _closed(closed))
    _add(CoachingTask, _task(closed, instruction="Again."))
    # A cancelled task does not block a new one either.
    cancelled = _reviewed(tenant, "active_cancelled")
    _add(CoachingTask, _cancelled(cancelled))
    _add(CoachingTask, _task(cancelled, instruction="Again."))
    # Another criterion of the same item is another finding.
    second = _criterion(setup, setup["result_id"], 1)
    _add(CriterionResultRecord, second)
    _add(CoachingTask, _task(setup, criterion_id=second["criterion_id"], instruction="Opening."))


# --- the ORM, the frozen lists and the RLS lists --------------------------------------------

LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "review_item_id": 36,
    "scorecard_result_id": 36,
    "criterion_id": 64,
    "finding_source": 20,
    "agent_user_id": 36,
    "owner_user_id": 36,
    "created_by_user_id": 36,
    "status": 20,
    "closed_by_user_id": 36,
    "cancel_reason": 30,
    "cancelled_by_user_id": 36,
    "follow_up_outcome": 30,
}
NULLABLE = {
    "agent_user_id",
    "owner_user_id",
    "created_by_user_id",
    "acknowledged_at",
    "acknowledgement_note",
    "follow_up_outcome",
    "follow_up_note",
    "closed_at",
    "closed_by_user_id",
    "cancel_reason",
    "cancel_note",
    "cancelled_at",
    "cancelled_by_user_id",
    "reminder_notified_at",
    "overdue_notified_at",
}
FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("agent_user_id",), ("users.id",), "SET NULL"),
    (None, ("owner_user_id",), ("users.id",), "SET NULL"),
    (None, ("created_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("closed_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("cancelled_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_coaching_tasks_item",
        ("review_item_id", "scorecard_result_id"),
        ("review_items.id", "review_items.scorecard_result_id"),
        "CASCADE",
    ),
    (
        "fk_coaching_tasks_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
    (
        "fk_coaching_tasks_review_result",
        ("review_item_id",),
        ("review_results.review_item_id",),
        "CASCADE",
    ),
    (
        "fk_coaching_tasks_criterion",
        ("scorecard_result_id", "criterion_id"),
        ("scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"),
        "CASCADE",
    ),
}
INDEXES = {
    # ``workspace_id`` is declared like ``review_appeals.workspace_id`` (design D1).
    "ix_coaching_tasks_workspace_id": (["workspace_id"], False),
    "uq_coaching_tasks_active_finding": (["review_item_id", "criterion_id"], True),
    "ix_coaching_tasks_queue": (["workspace_id", "status", "due_at", "id"], False),
    "ix_coaching_tasks_agent": (["workspace_id", "agent_user_id", "created_at", "id"], False),
    "ix_coaching_tasks_due": (["status", "due_at"], False),
    "ix_coaching_tasks_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_coaching_tasks_table():
    table = CoachingTask.__table__
    assert table.name == TABLE
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "review_item_id",
        "scorecard_result_id",
        "criterion_id",
        "finding_source",
        "agent_user_id",
        "owner_user_id",
        "created_by_user_id",
        "instruction",
        "due_at",
        "status",
        "acknowledged_at",
        "acknowledgement_note",
        "follow_up_outcome",
        "follow_up_note",
        "closed_at",
        "closed_by_user_id",
        "cancel_reason",
        "cancel_note",
        "cancelled_at",
        "cancelled_by_user_id",
        "reminder_notified_at",
        "overdue_notified_at",
        "revision",
        "created_at",
        "updated_at",
    ]
    for name, length in LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in (
        "due_at",
        "acknowledged_at",
        "closed_at",
        "cancelled_at",
        "reminder_notified_at",
        "overdue_notified_at",
        "created_at",
        "updated_at",
    ):
        assert table.c[name].type.timezone, name
    for name in ("instruction", "acknowledgement_note", "follow_up_note", "cancel_note"):
        assert table.c[name].type.__class__.__name__ == "Text", name
    assert {column.name for column in table.columns if column.nullable} == NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]

    # The ORM defaults (SQLAlchemy wraps a callable default and keeps its name).
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.status.default.arg == "assigned"
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
    } == FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == CHECKS
    assert [name for name in named if name.startswith("uq_")] == []
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == INDEXES
    # The partial unique index carries the active statuses on both dialects.
    (active,) = (
        index for index in table.indexes if index.name == "uq_coaching_tasks_active_finding"
    )
    for dialect in ("sqlite", "postgresql"):
        assert str(active.dialect_options[dialect]["where"]) == ACTIVE_WHERE, dialect
    assert "HOM-77" in CoachingTask.__doc__
    assert "compare-and-set" in CoachingTask.__doc__


def test_the_frozen_lists_and_the_check_strings():
    assert COACHING_TASK_STATUSES == ("assigned", "acknowledged", "closed", "cancelled")
    assert COACHING_TASK_ACTIVE_STATUSES == ("assigned", "acknowledged")
    assert COACHING_FINDING_SOURCES == ("human_result", "appeal_result")
    assert COACHING_FOLLOW_UP_OUTCOMES == ("improved", "partially_improved", "not_improved")
    assert COACHING_CANCEL_REASONS == (
        "finding_overturned",
        "agent_changed",
        "duplicate",
        "other",
    )
    assert COACHING_MAX_DUE_DAYS == 90
    assert COACHING_REMINDER_LEAD_HOURS == 24
    assert COACHING_TASK_ACTIVE_WHERE == ACTIVE_WHERE
    assert CHECKS["ck_coaching_tasks_status"] == (f"status IN {_list(COACHING_TASK_STATUSES)}")
    assert CHECKS["ck_coaching_tasks_instruction"] == (
        f"length(trim(instruction)) > 0 AND length(instruction) <= {REVIEW_NOTE_MAX_LENGTH}"
    )
    assert CHECKS["ck_coaching_tasks_criterion_key"] == CRITERION_KEY_CHECK
    for check in CHECKS.values():
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        # No user column: those are SET NULL on a user's removal; a check on them would
        # make the removal fail.
        for column in USER_COLUMNS:
            assert column not in check, column


def _list(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


def test_the_table_is_a_workspace_table_with_the_full_grants():
    assert TABLE in WORKSPACE_TABLES
    assert TABLE not in APPEND_ONLY_TABLES
    assert TABLE not in NO_DELETE_TABLES
    assert TABLE not in NO_UPDATE_TABLES  # PR 2 acknowledges, closes and cancels
    assert TABLE not in SHARED_CONTENT_TABLES  # never through a share token
    assert TABLE not in UNPROTECTED_TABLES
    assert POLICIES[TABLE] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")


# --- cascades and user removal --------------------------------------------------------------


def test_deleting_an_item_a_result_a_conversation_or_a_workspace_removes_the_rows(
    client, email_outbox
):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    by_item = _reviewed(second, "cascade_item")
    by_result = _reviewed(second, "cascade_result")
    by_meeting = _reviewed(second, "cascade_meeting")
    elsewhere = _reviewed(first, "cascade_elsewhere")
    task_ids = {
        key: _add(CoachingTask, _task(setup))
        for key, setup in (
            ("item", by_item),
            ("result", by_result),
            ("meeting", by_meeting),
            ("elsewhere", elsewhere),
        )
    }

    # Removals by the owner (DBA here): the FK actions run as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(ReviewItem).where(ReviewItem.id == by_item["item_id"]))
        db.execute(
            delete(ScorecardResultRecord).where(ScorecardResultRecord.id == by_result["result_id"])
        )
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.commit()
    assert _ids(TABLE) == {task_ids["elsewhere"]}

    kept = _reviewed(second, "cascade_kept")
    kept_id = _add(CoachingTask, _task(kept))
    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    assert _ids(TABLE) == {kept_id}


def test_deleting_a_user_clears_every_user_column_and_keeps_the_row(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user_removal")
    with inspection_session_factory()() as db:
        user = User(email="coaching-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    setup = _reviewed(tenant, "user_removal")
    # A closed task names the agent, the owner and the creator; a cancel never
    # happened, but the column is SET NULL like the others, so it is filled too.
    values = _closed(setup)
    values.update({column: user_id for column in USER_COLUMNS})
    task_id = _add(CoachingTask, values)

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        row = db.get(CoachingTask, task_id)
        for column in USER_COLUMNS:
            assert getattr(row, column) is None, column
        # Everything else stays, the state rules included.
        assert (row.status, row.follow_up_outcome, row.revision) == ("closed", "improved", 1)
        assert row.review_item_id == setup["item_id"]


# --- the migration (SPEC acceptance criterion AC1, SQLite) ----------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_coaching_tasks")


def remove_coaching_tasks(connection) -> None:
    """Take a ``create_all`` schema back to before HOM-77.

    Older migration tests build the full ORM, drop the newer tables and stamp an
    older revision.  A table drops like the appeals tables did; this helper just
    keeps the drop in one place with the migration's own name.
    """
    Base.metadata.tables[TABLE].drop(connection)


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == (
        "Coaching tasks from accepted findings, schema only (HOM-77, WP14-T06)"
    )
    for words in ("Why", "No data changes", "Downgrade"):
        assert words in doc, words
    # Frozen copies of the model's check strings.
    assert module.CHECKS == CHECKS
    assert str(module.ACTIVE_WHERE) == ACTIVE_WHERE


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION


def test_alembic_heads_prints_exactly_this_revision():
    import io

    from alembic import command
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    shown = io.StringIO()
    command.heads(Config(str(PROJECT_ROOT / "alembic.ini"), stdout=shown))
    lines = [line for line in shown.getvalue().splitlines() if line.strip()]
    assert lines == [f"{HEAD_REVISION} (head)"], shown.getvalue()


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


def _partial_sql(connection) -> str:
    """The SQL text SQLite keeps for the partial unique index."""
    return connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :n"),
        {"n": "uq_coaching_tasks_active_finding"},
    ).scalar_one()


def test_on_sqlite_the_migration_builds_the_orm_table_and_drops_only_it():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            orm_partial = _partial_sql(connection)
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
            assert sorted(name for name, _ in orm[TABLE]["checks"]) == sorted(CHECKS)
            # The partial unique index keeps the same WHERE clause as the ORM's.
            assert _partial_sql(connection) == orm_partial
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


def test_on_sqlite_the_partial_index_keeps_the_active_statuses():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            rows = connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = :t "
                    "AND sql IS NOT NULL"
                ),
                {"t": TABLE},
            ).all()
            (partial,) = (sql for _, sql in rows if "WHERE" in sql)
            assert partial == _partial_sql(connection)
            assert partial.endswith(f"WHERE {ACTIVE_WHERE}")
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

    url = f"sqlite:///{tmp_path / 'coaching-tasks.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            # HOM-128 adds the federation mapping table and two columns on top;
            # HOM-93 adds the retrieval index tables; take them out too.
            remove_federation_group_mappings(connection)
            remove_retrieval_index_tables(connection)
            # HOM-129 adds the MFA and break-glass schema; take it out too.
            remove_privileged_mfa_schema(connection)
            # HOM-135 adds the action proposal tables; take them out too.
            remove_action_proposals(connection)
            # HOM-134 adds the product alert tables; take them out too.
            remove_product_alerts(connection)
            before_objects = _objects(connection)

        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == HEAD_REVISION
            assert {table: _reflect(connection, table) for table in TABLES} == orm
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")

        # Down past HOM-128 to this revision, then one more step.
        command.downgrade(config, NEW_REVISION)
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == BASE_REVISION
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()
        reset_settings_cache()


# --- PostgreSQL: the schema, the migration, isolation and grants ----------------------------


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
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> :a"
    ),
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name <> :a"
    ),
    "constraints": (
        "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
        "AND c.conrelid::regclass::text <> :a"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename <> :a"
    ),
    "policies": (
        "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename <> :a"
    ),
    "grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name <> :a"
    ),
}


def _other_tables() -> dict:
    """Every other table of the schema, so a test can prove it did not change."""
    excluded = {"a": TABLE}
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


EXPECTED_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "review_item_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "criterion_id": _varchar(64),
    "finding_source": _varchar(20),
    "agent_user_id": _varchar(36, "YES"),
    "owner_user_id": _varchar(36, "YES"),
    "created_by_user_id": _varchar(36, "YES"),
    "instruction": _plain("text"),
    "due_at": _timestamp(),
    "status": _varchar(20),
    "acknowledged_at": _timestamp("YES"),
    "acknowledgement_note": _plain("text", "YES"),
    "follow_up_outcome": _varchar(30, "YES"),
    "follow_up_note": _plain("text", "YES"),
    "closed_at": _timestamp("YES"),
    "closed_by_user_id": _varchar(36, "YES"),
    "cancel_reason": _varchar(30, "YES"),
    "cancel_note": _plain("text", "YES"),
    "cancelled_at": _timestamp("YES"),
    "cancelled_by_user_id": _varchar(36, "YES"),
    "reminder_notified_at": _timestamp("YES"),
    "overdue_notified_at": _timestamp("YES"),
    "revision": _plain("integer"),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
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

EXPECTED_CONSTRAINTS = dict(
    [
        (f"{TABLE}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(TABLE, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(TABLE, "meeting_id", "meetings", "CASCADE"),
        *(_foreign_key(TABLE, column, "users", "SET NULL") for column in USER_COLUMNS),
        (
            "fk_coaching_tasks_item",
            "FOREIGN KEY (review_item_id, scorecard_result_id) REFERENCES "
            "review_items(id, scorecard_result_id) ON DELETE CASCADE",
        ),
        ("fk_coaching_tasks_result", RESULT_SCOPE),
        (
            "fk_coaching_tasks_review_result",
            "FOREIGN KEY (review_item_id) REFERENCES review_results(review_item_id) "
            "ON DELETE CASCADE",
        ),
        (
            "fk_coaching_tasks_criterion",
            "FOREIGN KEY (scorecard_result_id, criterion_id) REFERENCES "
            "scorecard_criterion_results(result_id, criterion_id) ON DELETE CASCADE",
        ),
    ]
)


def _index(name: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{TABLE} USING btree ({columns})"


EXPECTED_INDEXES = dict(
    [
        _index(f"{TABLE}_pkey", "id", unique=True),
        _index("ix_coaching_tasks_workspace_id", "workspace_id"),
        _index("ix_coaching_tasks_queue", "workspace_id, status, due_at, id"),
        _index("ix_coaching_tasks_agent", "workspace_id, agent_user_id, created_at, id"),
        _index("ix_coaching_tasks_due", "status, due_at"),
        _index("ix_coaching_tasks_workspace_meeting", "workspace_id, meeting_id"),
    ]
)


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    assert shape["columns"] == EXPECTED_COLUMNS, table
    constraints = dict(shape["constraints"])
    named_checks = {name for name in constraints if name.startswith("ck_")}
    assert named_checks == set(CHECKS)  # their behaviour is pinned by the RULES above
    for name in named_checks:
        constraints.pop(name)
    assert constraints == EXPECTED_CONSTRAINTS, table
    indexes = dict(shape["indexes"])
    # The partial unique index: PostgreSQL rewrites the IN list, so only its parts are
    # pinned.  Only the active statuses are in it.
    partial = indexes.pop("uq_coaching_tasks_active_finding")
    head, where = partial.split(" WHERE ")
    assert (
        head
        == _index("uq_coaching_tasks_active_finding", "review_item_id, criterion_id", unique=True)[
            1
        ]
    )
    assert "'assigned'" in where and "'acknowledged'" in where
    assert "'closed'" not in where and "'cancelled'" not in where
    assert indexes == EXPECTED_INDEXES, table
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    # The service of PR 2 acknowledges, closes and cancels; the purge deletes.
    assert shape["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"]
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_table(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_table(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    _task_on(tenant, "migration")
    results = _scalar("SELECT count(*) FROM review_results")
    items = _scalar("SELECT count(*) FROM review_items")
    meetings = _scalar("SELECT count(*) FROM meetings")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()
        command.downgrade(config, BASE_REVISION)
        others = _other_tables()  # the table is gone with the downgrade
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}")
            assert _shape(table) == EMPTY_SHAPE
        # Only the coaching tasks are lost: the reviews, the items and the calls stay.
        assert _scalar("SELECT count(*) FROM review_results") == results
        assert _scalar("SELECT count(*) FROM review_items") == items
        assert _scalar("SELECT count(*) FROM meetings") == meetings

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others  # every other table as it was

        # The migrated schema refuses the same deny cases and takes the allow cases.
        for rules in RULES:
            rules(tenant)
        migrated = _task_on(tenant, "migration_migrated")
        assert migrated in _ids(TABLE)

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others
        command.upgrade(config, "head")
        assert _other_tables() == head_others
    finally:
        reset_settings_cache()


def _write_row(db, setup: dict) -> dict:
    """The task row, written in the session's own transaction."""
    task = CoachingTask(**_task(setup))
    db.add(task)
    db.flush()
    return setup | {"task_id": task.id}


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _reviewed(_tenant(client, email_outbox, "tenant_a"), "tenant_a")
    second = _reviewed(_tenant(client, email_outbox, "tenant_b"), "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_row(db, first)
        db.commit()

    # Bound to B: none of A's rows; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        second = _write_row(db, second)
        db.commit()

    # Bound to A: only A's rows; an insert with B's ids fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]]
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.add(CoachingTask(**_task(second)))
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
    setup = _reviewed(_tenant(client, email_outbox, "share"), "share")
    _add(CoachingTask, _task(setup))
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
        assert db.scalars(select(CoachingTask.id)).all() == []
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
def test_the_app_role_may_select_insert_update_and_delete(client, email_outbox):
    _requires_postgres()
    first = _reviewed(_tenant(client, email_outbox, "grants_a"), "grants_a")
    second = _reviewed(_tenant(client, email_outbox, "grants_b"), "grants_b")
    full = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    assert _privileges(TABLE) == full

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_row(db, first)
        db.commit()

    # Bound to B: A's rows are invisible, so an update or a delete changes none of them.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for statement in (
            f"UPDATE {TABLE} SET revision = revision + 1",  # noqa: S608
            f"DELETE FROM {TABLE}",  # noqa: S608
        ):
            assert db.execute(text(statement)).rowcount == 0
        db.commit()
    for table in TABLES:
        assert len(_ids(table)) == 1, table

    # Bound to A: the compare-and-set of PR 2, and still full grants after the
    # policies and the grants are applied again.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {TABLE} SET status = 'acknowledged', acknowledged_at = :now, "  # noqa: S608
                "revision = revision + 1 WHERE id = :id AND workspace_id = :w "
                "AND revision = 1 AND status = 'assigned'"
            ),
            {"now": NOW, "id": first["task_id"], "w": first["workspace_id"]},
        )
        assert updated.rowcount == 1
        db.commit()
    _apply_as_owner()  # idempotent: no revoke for this table
    assert _privileges(TABLE) == full

    # Allow-control, bound to A: the purge's own statements delete the row.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        meeting = db.get(Meeting, first["meeting_id"])
        _remove_content(db, workspace_id=first["workspace_id"], meeting=meeting)
        db.commit()
    for table in TABLES:
        assert _ids(table) == set(), table


# --- the conversation purge and the backup reconciliation -----------------------------------


def _purgeable(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A reviewed conversation with a decision and a coaching task on it."""
    setup = _reviewed(tenant, key, meeting_id=meeting_id)
    _add(ReviewCriterionDecision, _decision(setup))
    task_id = _add(CoachingTask, _task(setup))
    return setup | {"task_id": task_id}


def test_the_purge_deletes_the_tasks_before_the_review_rows_and_counts_them(client, email_outbox):
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
    # The coaching tasks go first: the cascades of the human result, the review item
    # and the scorecard result would otherwise remove them uncounted.
    for table in (APPEALS, "review_appeal_criteria", "review_appeal_results"):
        assert deletes.index(TABLE) < deletes.index(table), table
    for table in ("review_criterion_decisions", HUMAN_RESULTS, ITEMS, "scorecard_results"):
        assert deletes.index(TABLE) < deletes.index(table), table
    # One task, one decision, one human result, one item, one result, one criterion
    # row and the transcript revision.
    assert removed == 7
    for table in TABLES:
        assert _ids(table, meeting_id=setup["meeting_id"]) == set(), table
        assert len(_ids(table, meeting_id=kept["meeting_id"])) == 1, table


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "coaching-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"coaching-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    for meeting_id, key in ((queued, "coaching_purged"), (kept, "coaching_kept")):
        _purgeable(tenant, key, meeting_id=meeting_id)
    for table in TABLES:
        assert len(_ids(table, meeting_id=queued)) == 1, table

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    for table in TABLES:
        assert _ids(table, meeting_id=queued) == set(), table
        # Another conversation of the workspace keeps its tasks.
        assert len(_ids(table, meeting_id=kept)) == 1, table
    # The purge deletes the task, the decision, the human result, the item, the
    # result and its criterion row itself (they are counted), before any cascade
    # could.
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 6


def test_a_held_conversation_keeps_its_tasks_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "coaching-hold")
    meeting_id = _processed(client, tenant, settings, key="coaching-hold-0001")
    _purgeable(tenant, "coaching_held", meeting_id=meeting_id)
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


def test_the_backup_reconciliation_counts_the_table():
    for table in TABLES:
        assert table in RECONCILED_TABLES, table
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns
    # Next to the review rows they belong to.
    assert RECONCILED_TABLES.index(HUMAN_RESULTS) < RECONCILED_TABLES.index(TABLE)


# --- the ADR --------------------------------------------------------------------------------


def _decision_record(name: str) -> str:
    from backend.observability.release import PROJECT_ROOT

    path = PROJECT_ROOT.parent / "docs" / "architecture" / "decisions" / name
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def _adr() -> str:
    return _decision_record("0062-coaching-tasks-from-accepted-findings.md")


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0062: ")
    assert "Proposed (HOM-77, WP14-T06, F47)" in content
    assert "2026-11-13" in content
    for number in ("0051", "0052", "0053", "0054", "0057"):
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
    "the_table": (TABLE,),
    "one_finding": ("exactly one finding",),
    "active_unique": ("uq_coaching_tasks_active_finding",),
    "sources": ("human_result",),
    "outcomes": ("partially_improved",),
    "cancel_reasons": ("finding_overturned",),
    "coachee": ("agent_user_id",),
    "owner": ("owner_user_id",),
    "due_days": ("COACHING_MAX_DUE_DAYS", "90 days"),
    "acknowledgement": ("acknowledged_at",),
    "reminders": ("reminder_notified_at",),
    "overdue": ("overdue_notified_at",),
    "accepted_finding": ("Accepted finding", "accepted finding"),
    "final_outcome": ("final outcome",),
    "independence": ("check_assignee",),
    "permission": ("coaching:respond",),
    "supervisor_permissions": ("review:manage",),
    "routes": ("/api/v1/coaching-tasks",),
    "my_tasks": ("/api/v1/my-coaching-tasks",),
    "revision_cas": ("expected_revision", "coaching_task_revision_conflict"),
    "sweep": ("coaching_reminder_sweep_interval_seconds",),
    "lead_hours": ("COACHING_REMINDER_LEAD_HOURS", "24"),
    "appeal_cancel": ("finding_overturned",),
    "audit": ("coaching_task.created",),
    "audit_closed": ("coaching_task.closed",),
    "audit_ids_only": ("Ids, codes and times only", "ids, codes and times only"),
    "notifications": ("coaching_task.assigned",),
    "agent_view": ("agent view",),
    "rls": ("row-level security",),
    "purge": ("purge",),
    "backup": ("reconcil",),
    "not_built_console": ("HOM-250",),
    "not_built_delivery": ("HOM-134",),
    "not_built_auto": ("No automatic task",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings


def test_the_appeals_adr_keeps_coaching_in_its_not_built():
    content = _decision_record("0057-agent-appeal-and-disposition.md")
    not_built = content.split("## Not built", 1)[1].split("\n## ", 1)[0]
    assert "HOM-77" in not_built
