"""HOM-75 PR 1: blinded calibration batches and their one migration.

Five tables: ``calibration_batches`` (the exercise), ``calibration_examples``
(one machine result of one conversation in the batch), ``calibration_participants``
(one reviewer in the batch), ``calibration_answers`` (a participant's outcome on one
reviewable criterion of one example) and ``calibration_adjudications`` (the correct
outcome of one criterion, recorded by a review admin who is not a participant).
Nothing writes the tables yet; PR 2 adds the service, the routes and the report.
The tests check:

* the ORM declares the columns, the named constraints, the unique constraints and the
  indexes of the design, the frozen lists (the outcomes equal the engine's
  ``REVIEWER_OUTCOMES`` and HOM-74's reviewer outcomes) and the check strings, which
  use no regex and never name a user column;
* ``database/rls.py`` lists all five tables as workspace tables, only
  ``calibration_examples`` in the no-update list, none in the append-only or
  no-delete lists;
* every deny case of the SPEC is refused by its named constraint, and the closest
  allow case passes (SQLite and PostgreSQL): the status list, the title rules, the
  per-status timestamp rules, the revision, the position, the outcome lists, the
  criterion key, the note length, one conversation once per batch, one position once,
  one participant once, one answer per participant, example and criterion, one
  adjudication per example and criterion, and the composite foreign keys that keep an
  example, an answer and an adjudication inside their batch, workspace, result and
  criterion;
* cascades: deleting a batch, a conversation, a scorecard result or a workspace
  removes the rows it owns; deleting a user clears every user column and keeps the
  rows;
* on SQLite, the migration builds the same five tables as the ORM, and its downgrade
  removes them and nothing else;
* on PostgreSQL: the ORM schema (``create_all``) and the migrated schema are the same;
  downgrade from the head removes the five tables and leaves every other table as it
  was; upgrade recreates them; tenant isolation (also on write); the application role
  may SELECT, INSERT, UPDATE and DELETE four of the tables and has no UPDATE on
  ``calibration_examples``;
* the conversation purge deletes the answers, the adjudications and the examples of
  the purged conversation only, before the review decisions and the scorecard results
  whose cascades would remove them uncounted, counts them, keeps the batch and the
  participant rows, and a legal hold keeps them; the backup reconciliation counts the
  three content tables; the code has one migration head, this one; ADR 0056 records
  the design.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, inspect, text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

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
    CALIBRATION_BATCH_STATUSES,
    CALIBRATION_MAX_EXAMPLES,
    CALIBRATION_MAX_PARTICIPANTS,
    CALIBRATION_OUTCOMES,
    CALIBRATION_TITLE_MAX_LENGTH,
    CRITERION_KEY_CHECK,
    REVIEW_NOTE_MAX_LENGTH,
    REVIEW_REVIEWER_OUTCOMES,
    CalibrationAdjudication,
    CalibrationAnswer,
    CalibrationBatch,
    CalibrationExample,
    CalibrationParticipant,
    CriterionResultRecord,
    Meeting,
    PurgeState,
    ReviewCriterionDecision,
    ReviewItem,
    ReviewResultRecord,
    ScorecardResultRecord,
    User,
    Workspace,
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
from backend.tests.test_review_decisions_migration import _decision, _human, _item_values
from backend.tests.test_scorecard_results_migration import (
    _add,
    _criterion,
    _delete_conversation,
    _parents,
    _result,
)

NEW_REVISION = "02f616cbb655"
HEAD_REVISION = "a3c7e19d4b62"  # HOM-93: retrieval index, after HOM-128
BASE_REVISION = "a651110b03c3"  # HOM-74: review decisions
BATCHES = "calibration_batches"
EXAMPLES = "calibration_examples"
PARTICIPANTS = "calibration_participants"
ANSWERS = "calibration_answers"
ADJUDICATIONS = "calibration_adjudications"
TABLES = (BATCHES, EXAMPLES, PARTICIPANTS, ANSWERS, ADJUDICATIONS)
DECISIONS = "review_criterion_decisions"
ITEMS = "review_items"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
# The check strings of the SPEC, character for character.
CHECKS = {
    "ck_calibration_batches_status": "status IN ('open','closed','cancelled')",
    "ck_calibration_batches_revision": "revision >= 1",
    "ck_calibration_batches_title": "length(trim(title)) > 0 AND length(title) <= 200",
    "ck_calibration_batches_open_fields": (
        "status <> 'open' OR (closed_at IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_calibration_batches_closed_fields": (
        "status <> 'closed' OR (closed_at IS NOT NULL AND cancelled_at IS NULL)"
    ),
    "ck_calibration_batches_cancelled_fields": (
        "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND closed_at IS NULL)"
    ),
}
EXAMPLE_CHECKS = {
    "ck_calibration_examples_position": "position >= 0 AND position < 50",
}
ANSWER_CHECKS = {
    "ck_calibration_answers_outcome": "outcome IN ('passed','failed','not_applicable')",
    "ck_calibration_answers_criterion_key": CRITERION_KEY_CHECK,
}
ADJUDICATION_CHECKS = {
    "ck_calibration_adjudications_outcome": "outcome IN ('passed','failed','not_applicable')",
    "ck_calibration_adjudications_criterion_key": CRITERION_KEY_CHECK,
    "ck_calibration_adjudications_note_length": "note IS NULL OR length(note) <= 2000",
}
# SQLite names the columns of a broken unique constraint, not the constraint.
UNIQUE_COLUMNS = {
    "uq_calibration_examples_meeting": (EXAMPLES, ("batch_id", "meeting_id")),
    "uq_calibration_examples_position": (EXAMPLES, ("batch_id", "position")),
    "uq_calibration_participants_user": (PARTICIPANTS, ("batch_id", "user_id")),
    "uq_calibration_answers_criterion": (ANSWERS, ("participant_id", "example_id", "criterion_id")),
    "uq_calibration_adjudications_criterion": (ADJUDICATIONS, ("example_id", "criterion_id")),
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
    credentials = register_user(client, email_outbox, email=f"calibration-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _conversation(tenant: dict) -> dict:
    """A new conversation of the tenant, without a transcript or a score."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title="Calibrated call",
        )
        db.add(meeting)
        db.commit()
        return {**tenant, "meeting_id": meeting.id}


def _scored(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A scored conversation with one passed criterion, the smallest an example needs.

    With ``meeting_id`` the conversation keeps its current transcript revision.
    """
    parents = _parents(tenant, key=key, meeting_id=meeting_id)
    result_id = _add(ScorecardResultRecord, _result(parents))
    criterion = _criterion(parents, result_id, 0)
    _add(CriterionResultRecord, criterion)
    return {**parents, "result_id": result_id, "criterion_id": criterion["criterion_id"]}


def _batch(setup: dict, **extra: object) -> dict[str, object]:
    """A valid open batch on the setup's workspace."""
    return {
        "workspace_id": setup["workspace_id"],
        "title": "Weekly calibration",
        "status": "open",
        "created_by_user_id": setup["user_id"],
        "revision": 1,
        **extra,
    }


def _example(setup: dict, **extra: object) -> dict[str, object]:
    """A valid example: the setup's result at position 0 of the setup's batch."""
    return {
        "workspace_id": setup["workspace_id"],
        "batch_id": setup["batch_id"],
        "meeting_id": setup["meeting_id"],
        "scorecard_result_id": setup["result_id"],
        "position": 0,
        **extra,
    }


def _participant(setup: dict, **extra: object) -> dict[str, object]:
    """A valid participant: the setup's user in the setup's batch."""
    return {
        "workspace_id": setup["workspace_id"],
        "batch_id": setup["batch_id"],
        "user_id": setup["user_id"],
        **extra,
    }


def _answer(setup: dict, **extra: object) -> dict[str, object]:
    """A valid answer: the setup's user passed the setup's criterion."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "batch_id": setup["batch_id"],
        "example_id": setup["example_id"],
        "participant_id": setup["participant_id"],
        "scorecard_result_id": setup["result_id"],
        "criterion_id": setup["criterion_id"],
        "outcome": "passed",
        **extra,
    }


def _adjudication(setup: dict, **extra: object) -> dict[str, object]:
    """A valid adjudication: the setup's criterion passed, no note."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "batch_id": setup["batch_id"],
        "example_id": setup["example_id"],
        "scorecard_result_id": setup["result_id"],
        "criterion_id": setup["criterion_id"],
        "outcome": "passed",
        "note": None,
        "decided_by_user_id": setup["user_id"],
        **extra,
    }


def _batched(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A scored conversation with a stored batch, one example and one participant."""
    setup = _scored(tenant, key, meeting_id=meeting_id)
    batch_id = _add(CalibrationBatch, _batch(setup))
    example_id = _add(CalibrationExample, _example({**setup, "batch_id": batch_id}))
    participant_id = _add(CalibrationParticipant, _participant({**setup, "batch_id": batch_id}))
    return {
        **setup,
        "batch_id": batch_id,
        "example_id": example_id,
        "participant_id": participant_id,
    }


def _opened(tenant: dict, key: str) -> dict:
    """A scored conversation with a stored batch and no example or participant yet."""
    setup = _scored(tenant, key)
    setup["batch_id"] = _add(CalibrationBatch, _batch(setup))
    return setup


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        # SQLite does not name a broken foreign key.  Each deny names rows that exist,
        # so only the composite key can refuse it.
        return "FOREIGN KEY constraint failed"
    if constraint in UNIQUE_COLUMNS and not _on_postgres():
        table, columns = UNIQUE_COLUMNS[constraint]
        return re.escape(", ".join(f"{table}.{column}" for column in columns))
    return rf"\b{constraint}\b"


def _refused(
    model, values: dict[str, object], constraint: str, *also: str, overflow: bool = False
) -> None:
    """The insert fails on ``constraint``.

    SQLite reports the first broken check in table order, PostgreSQL the first in name
    order.  A deny that must break more than one rule lists the others in ``also``:
    SQLite must still name ``constraint``, PostgreSQL may name any of them.  With
    ``overflow`` the deny also trips the column length on PostgreSQL (a varchar never
    delivers a 201st character to the check), so the refusal there is the truncation.
    """
    names = (constraint, *also) if _on_postgres() else (constraint,)
    if overflow and _on_postgres():
        names = (*names, "too long")
    exception = (IntegrityError, DataError) if overflow else IntegrityError
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(exception, match="|".join(_names(name) for name in names)):
            db.flush()
        db.rollback()


def _ids(table: str, **where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {table} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


# --- the deny and allow cases (SPEC "Acceptance criteria" AC2 and AC4) ---------------------


def _batch_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored valid batch of its own (one row per workspace is not limited)."""
    return _add(CalibrationBatch, _batch(_scored(tenant, key), **extra))


def _assert_title_rules(tenant: dict) -> None:
    setup = _scored(tenant, "title")
    for value in ("", "   "):
        _refused(CalibrationBatch, _batch(setup, title=value), "ck_calibration_batches_title")
    # On PostgreSQL the varchar(200) refuses the 201st character before the check.
    _refused(
        CalibrationBatch,
        _batch(setup, title="x" * 201),
        "ck_calibration_batches_title",
        overflow=True,
    )
    _batch_on(tenant, "title_one", title="x")
    _batch_on(tenant, "title_200", title="x" * 200)


def _assert_status_list_rules(tenant: dict) -> None:
    setup = _scored(tenant, "status_list")
    for value in ("paused", "Open", "open ", ""):
        _refused(CalibrationBatch, _batch(setup, status=value), "ck_calibration_batches_status")


def _assert_open_fields_rules(tenant: dict) -> None:
    setup = _scored(tenant, "open_fields")
    _refused(CalibrationBatch, _batch(setup, closed_at=NOW), "ck_calibration_batches_open_fields")
    _refused(
        CalibrationBatch, _batch(setup, cancelled_at=NOW), "ck_calibration_batches_open_fields"
    )
    _batch_on(tenant, "open_fields_valid")


def _assert_closed_fields_rules(tenant: dict) -> None:
    setup = _scored(tenant, "closed_fields")
    _refused(
        CalibrationBatch, _batch(setup, status="closed"), "ck_calibration_batches_closed_fields"
    )
    _refused(
        CalibrationBatch,
        _batch(setup, status="closed", cancelled_at=NOW),
        "ck_calibration_batches_closed_fields",
    )
    _batch_on(tenant, "closed_fields_valid", status="closed", closed_at=NOW)


def _assert_cancelled_fields_rules(tenant: dict) -> None:
    setup = _scored(tenant, "cancelled_fields")
    _refused(
        CalibrationBatch,
        _batch(setup, status="cancelled"),
        "ck_calibration_batches_cancelled_fields",
    )
    _refused(
        CalibrationBatch,
        _batch(setup, status="cancelled", closed_at=NOW),
        "ck_calibration_batches_cancelled_fields",
    )
    _batch_on(tenant, "cancelled_fields_valid", status="cancelled", cancelled_at=NOW)


def _assert_revision_rules(tenant: dict) -> None:
    setup = _scored(tenant, "revision")
    _refused(CalibrationBatch, _batch(setup, revision=0), "ck_calibration_batches_revision")
    _batch_on(tenant, "revision_one", revision=1)
    _batch_on(tenant, "revision_two", revision=2)


BATCH_RULES = (
    _assert_title_rules,
    _assert_status_list_rules,
    _assert_open_fields_rules,
    _assert_closed_fields_rules,
    _assert_cancelled_fields_rules,
    _assert_revision_rules,
)


def _example_on(tenant: dict, key: str, **extra: object) -> str:
    """A stored example of a fresh batch of its own (position 0 free)."""
    return _add(CalibrationExample, _example(_opened(tenant, key), **extra))


def _assert_position_rules(tenant: dict) -> None:
    for value, label in ((50, "fifty"), (-1, "negative")):
        setup = _batched(tenant, f"position_{label}")
        _refused(
            CalibrationExample, _example(setup, position=value), "ck_calibration_examples_position"
        )
    _example_on(tenant, "position_zero", position=0)
    _example_on(tenant, "position_49", position=49)


def _assert_outcome_rules(tenant: dict) -> None:
    setup = _batched(tenant, "outcome")
    # Neither the machine's word for missing evidence nor a free word is an outcome.
    for value in ("insufficient_evidence", "reviewer", ""):
        _refused(CalibrationAnswer, _answer(setup, outcome=value), "ck_calibration_answers_outcome")
        _refused(
            CalibrationAdjudication,
            _adjudication(setup, outcome=value),
            "ck_calibration_adjudications_outcome",
        )
    for value in ("passed", "failed", "not_applicable"):
        answer_setup = _batched(tenant, f"outcome_answer_{value}")
        _add(CalibrationAnswer, _answer(answer_setup, outcome=value))
        adjudication_setup = _batched(tenant, f"outcome_adjudication_{value}")
        _add(CalibrationAdjudication, _adjudication(adjudication_setup, outcome=value))


def _assert_criterion_key_rules(tenant: dict) -> None:
    setup = _batched(tenant, "criterion_key")
    for value in ("C0", "c", "c-0", "0c", ""):
        _refused(
            CalibrationAnswer,
            _answer(setup, criterion_id=value),
            "ck_calibration_answers_criterion_key",
        )
        _refused(
            CalibrationAdjudication,
            _adjudication(setup, criterion_id=value),
            "ck_calibration_adjudications_criterion_key",
        )


def _assert_note_rules(tenant: dict) -> None:
    setup = _batched(tenant, "note")
    _refused(
        CalibrationAdjudication,
        _adjudication(setup, note="x" * 2001),
        "ck_calibration_adjudications_note_length",
    )
    noted = _batched(tenant, "note_2000")
    _add(CalibrationAdjudication, _adjudication(noted, note="x" * 2000))
    plain = _batched(tenant, "note_none")
    _add(CalibrationAdjudication, _adjudication(plain, note=None))


def _assert_one_conversation_once_per_batch(tenant: dict) -> None:
    setup = _batched(tenant, "one_meeting")
    # A second result of the same conversation: a valid result, but the conversation
    # is already an example of the batch (position 1 keeps the position rule out).
    second_result = _add(ScorecardResultRecord, _result(setup))
    _refused(
        CalibrationExample,
        _example(setup, scorecard_result_id=second_result, position=1),
        "uq_calibration_examples_meeting",
    )
    # The same conversation in another batch is fine, at position 0.
    other = _opened(tenant, "one_meeting_other")
    _add(
        CalibrationExample,
        _example(other, meeting_id=setup["meeting_id"], scorecard_result_id=second_result),
    )


def _assert_one_position_once_per_batch(tenant: dict) -> None:
    setup = _batched(tenant, "one_position")
    # Another conversation of the workspace at the same position of the same batch
    # (its meeting is new to the batch, so only the position rule can refuse it).
    elsewhere = _scored(tenant, "one_position_elsewhere")
    _refused(
        CalibrationExample,
        _example(
            setup, meeting_id=elsewhere["meeting_id"], scorecard_result_id=elsewhere["result_id"]
        ),
        "uq_calibration_examples_position",
    )
    # The same position in another batch is fine.
    other = _opened(tenant, "one_position_other")
    _add(
        CalibrationExample,
        _example(
            other, meeting_id=elsewhere["meeting_id"], scorecard_result_id=elsewhere["result_id"]
        ),
    )


def _assert_one_participant_once_per_batch(tenant: dict) -> None:
    setup = _batched(tenant, "one_participant")
    with inspection_session_factory()() as db:
        colleague = User(email="calibration-colleague@example.com", display_name="Colleague")
        db.add(colleague)
        db.commit()
        colleague_id = colleague.id
    # The same user twice in one batch is refused; another user in the same batch is
    # fine.
    _refused(CalibrationParticipant, _participant(setup), "uq_calibration_participants_user")
    _add(CalibrationParticipant, _participant(setup, user_id=colleague_id))
    # The same user in another batch is fine.
    other = _opened(tenant, "one_participant_other")
    _add(CalibrationParticipant, _participant(other))


def _assert_one_answer_per_participant_example_criterion(tenant: dict) -> None:
    setup = _batched(tenant, "one_answer")
    _add(CalibrationAnswer, _answer(setup))
    _refused(
        CalibrationAnswer, _answer(setup, outcome="failed"), "uq_calibration_answers_criterion"
    )
    # Another criterion of the same example, and the same trio in another batch.
    second = _criterion(setup, setup["result_id"], 1)
    _add(CriterionResultRecord, second)
    _add(CalibrationAnswer, _answer(setup, criterion_id=second["criterion_id"]))
    other = _batched(tenant, "one_answer_other")
    _add(CalibrationAnswer, _answer(other))


def _assert_one_adjudication_per_example_criterion(tenant: dict) -> None:
    setup = _batched(tenant, "one_adjudication")
    _add(CalibrationAdjudication, _adjudication(setup))
    _refused(
        CalibrationAdjudication,
        _adjudication(setup, outcome="failed"),
        "uq_calibration_adjudications_criterion",
    )
    second = _criterion(setup, setup["result_id"], 1)
    _add(CriterionResultRecord, second)
    _add(CalibrationAdjudication, _adjudication(setup, criterion_id=second["criterion_id"]))
    other = _batched(tenant, "one_adjudication_other")
    _add(CalibrationAdjudication, _adjudication(other))


EXAMPLE_RULES = (_assert_position_rules,)
CONTENT_RULES = (
    _assert_outcome_rules,
    _assert_criterion_key_rules,
    _assert_note_rules,
)
UNIQUE_RULES = (
    _assert_one_conversation_once_per_batch,
    _assert_one_position_once_per_batch,
    _assert_one_participant_once_per_batch,
    _assert_one_answer_per_participant_example_criterion,
    _assert_one_adjudication_per_example_criterion,
)
RULES = (*BATCH_RULES, *EXAMPLE_RULES, *CONTENT_RULES, *UNIQUE_RULES)


@pytest.mark.parametrize(
    "rules",
    RULES,
    ids=lambda rules: rules.__name__.removeprefix("_assert_"),
)
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


# --- the composite keys (SPEC acceptance criterion AC3) ------------------------------------


def test_an_example_names_only_its_batch_and_its_results_scope(client, email_outbox):
    tenant = _tenant(client, email_outbox, "scope-a")
    other = _tenant(client, email_outbox, "scope-b")
    setup = _batched(tenant, "scope")
    wrong_meeting = _conversation(tenant)["meeting_id"]
    # A batch of another workspace under this workspace's example (position 1 is free
    # in both batches, so only the batch key can refuse it).
    elsewhere = _batched(other, "scope_elsewhere")
    _refused(
        CalibrationExample,
        _example(setup, batch_id=elsewhere["batch_id"], position=1),
        "fk_calibration_examples_batch",
    )
    # A result of another workspace under a new conversation of this workspace's
    # batch: the result's scope names its own workspace.
    _refused(
        CalibrationExample,
        _example(
            setup, meeting_id=wrong_meeting, scorecard_result_id=elsewhere["result_id"], position=1
        ),
        "fk_calibration_examples_scorecard_result",
    )
    # A wrong meeting: the result's scope names its own conversation.
    _refused(
        CalibrationExample,
        _example(setup, meeting_id=wrong_meeting, position=1),
        "fk_calibration_examples_scorecard_result",
    )
    # Allow-controls: another conversation of the workspace at the next position of
    # the same batch, and a fresh batch's own example.
    second_conversation = _scored(tenant, "scope_second")
    _add(
        CalibrationExample,
        _example(
            setup,
            meeting_id=second_conversation["meeting_id"],
            scorecard_result_id=second_conversation["result_id"],
            position=1,
        ),
    )
    fresh = _opened(tenant, "scope_fresh")
    _add(CalibrationExample, _example(fresh))


def test_an_answer_names_only_its_batchs_example_participant_result_and_criterion(
    client,
    email_outbox,
):
    tenant = _tenant(client, email_outbox, "answer-scope")
    setup = _batched(tenant, "answer_scope")
    # An example and a participant of another batch of the same workspace: valid rows,
    # not this batch's.
    other = _batched(tenant, "answer_scope_other")
    _refused(
        CalibrationAnswer,
        _answer(
            setup,
            example_id=other["example_id"],
            scorecard_result_id=other["result_id"],
            criterion_id=other["criterion_id"],
        ),
        "fk_calibration_answers_example",
    )
    _refused(
        CalibrationAnswer,
        _answer(setup, participant_id=other["participant_id"]),
        "fk_calibration_answers_participant",
    )
    # A criterion that is not one of the example's result's.
    _refused(
        CalibrationAnswer,
        _answer(setup, criterion_id="cx"),
        "fk_calibration_answers_criterion",
    )
    # A result other than its example's: a second result of the same conversation,
    # with the same criterion id, so only the example key can refuse it.
    second_result = _add(ScorecardResultRecord, _result(setup))
    _add(CriterionResultRecord, _criterion(setup, second_result, 0))
    _refused(
        CalibrationAnswer,
        _answer(setup, scorecard_result_id=second_result),
        "fk_calibration_answers_example",
    )
    # Allow-controls: its own example, participant, result and criterion.
    _add(CalibrationAnswer, _answer(setup))


def test_an_adjudication_names_only_its_batchs_example_result_and_criterion(client, email_outbox):
    tenant = _tenant(client, email_outbox, "adjudication-scope")
    setup = _batched(tenant, "adjudication_scope")
    other = _batched(tenant, "adjudication_scope_other")
    _refused(
        CalibrationAdjudication,
        _adjudication(
            setup,
            example_id=other["example_id"],
            scorecard_result_id=other["result_id"],
            criterion_id=other["criterion_id"],
        ),
        "fk_calibration_adjudications_example",
    )
    _refused(
        CalibrationAdjudication,
        _adjudication(setup, criterion_id="cx"),
        "fk_calibration_adjudications_criterion",
    )
    second_result = _add(ScorecardResultRecord, _result(setup))
    _add(CriterionResultRecord, _criterion(setup, second_result, 0))
    _refused(
        CalibrationAdjudication,
        _adjudication(setup, scorecard_result_id=second_result),
        "fk_calibration_adjudications_example",
    )
    _add(CalibrationAdjudication, _adjudication(setup))


# --- the ORM, the frozen lists and the RLS lists --------------------------------------------

BATCH_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "title": 200,
    "status": 16,
    "created_by_user_id": 36,
    "closed_by_user_id": 36,
    "cancelled_by_user_id": 36,
}
BATCH_NULLABLE = {
    "created_by_user_id",
    "closed_by_user_id",
    "cancelled_by_user_id",
    "closed_at",
    "cancelled_at",
}
BATCH_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("created_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("closed_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("cancelled_by_user_id",), ("users.id",), "SET NULL"),
}
BATCH_INDEXES = {
    "ix_calibration_batches_workspace_id": (["workspace_id"], False),
    "uq_calibration_batches_scope": (["id", "workspace_id"], True),
    "ix_calibration_batches_list": (["workspace_id", "created_at", "id"], False),
}


def test_the_orm_declares_the_batches_table():
    table = CalibrationBatch.__table__
    assert table.name == BATCHES
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "title",
        "status",
        "created_by_user_id",
        "closed_by_user_id",
        "cancelled_by_user_id",
        "created_at",
        "updated_at",
        "closed_at",
        "cancelled_at",
        "revision",
    ]
    for name, length in BATCH_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at", "closed_at", "cancelled_at"):
        assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == BATCH_NULLABLE
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
    } == BATCH_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == CHECKS
    assert [name for name in named if name.startswith("uq_")] == []
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == BATCH_INDEXES
    assert "HOM-75" in CalibrationBatch.__doc__
    assert "compare-and-set" in CalibrationBatch.__doc__


EXAMPLE_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "batch_id": 36,
    "meeting_id": 36,
    "scorecard_result_id": 36,
}
EXAMPLE_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        "fk_calibration_examples_batch",
        ("batch_id", "workspace_id"),
        ("calibration_batches.id", "calibration_batches.workspace_id"),
        "CASCADE",
    ),
    (
        "fk_calibration_examples_scorecard_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
}
EXAMPLE_INDEXES = {
    "ix_calibration_examples_scorecard_result_id": (["scorecard_result_id"], False),
    "uq_calibration_examples_scope": (["id", "batch_id", "scorecard_result_id"], True),
    "ix_calibration_examples_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_examples_table():
    table = CalibrationExample.__table__
    assert table.name == EXAMPLES
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "batch_id",
        "meeting_id",
        "scorecard_result_id",
        "position",
        "created_at",
    ]
    for name, length in EXAMPLE_LENGTHS.items():
        assert table.c[name].type.length == length, name
    assert table.c.created_at.type.timezone
    assert table.c.position.type.__class__.__name__ == "Integer"
    assert {column.name for column in table.columns if column.nullable} == set()
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
    } == EXAMPLE_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == (
        EXAMPLE_CHECKS
    )
    assert sorted(name for name in named if name.startswith("uq_")) == [
        "uq_calibration_examples_meeting",
        "uq_calibration_examples_position",
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == EXAMPLE_INDEXES
    # No user column: the table is immutable (no UPDATE grant), so a SET NULL action
    # on a user column would need an update.
    assert [name for name in table.c.keys() if "user" in name] == []
    assert "HOM-75" in CalibrationExample.__doc__
    assert "immutable" in CalibrationExample.__doc__.lower()


PARTICIPANT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "batch_id": 36,
    "user_id": 36,
}
PARTICIPANT_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("user_id",), ("users.id",), "SET NULL"),
    (
        "fk_calibration_participants_batch",
        ("batch_id", "workspace_id"),
        ("calibration_batches.id", "calibration_batches.workspace_id"),
        "CASCADE",
    ),
}
PARTICIPANT_INDEXES = {
    "ix_calibration_participants_user_id": (["user_id"], False),
    "uq_calibration_participants_scope": (["id", "batch_id"], True),
}


def test_the_orm_declares_the_participants_table():
    table = CalibrationParticipant.__table__
    assert table.name == PARTICIPANTS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "batch_id",
        "user_id",
        "created_at",
    ]
    for name, length in PARTICIPANT_LENGTHS.items():
        assert table.c[name].type.length == length, name
    assert table.c.created_at.type.timezone
    assert {column.name for column in table.columns if column.nullable} == {"user_id"}
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
    } == PARTICIPANT_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == {}
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_calibration_participants_user"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == PARTICIPANT_INDEXES
    assert "HOM-75" in CalibrationParticipant.__doc__


CONTENT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "batch_id": 36,
    "example_id": 36,
    "scorecard_result_id": 36,
    "criterion_id": 64,
    "outcome": 24,
}
ANSWER_EXTRA_LENGTHS = {"participant_id": 36}
ANSWER_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        "fk_calibration_answers_example",
        ("example_id", "batch_id", "scorecard_result_id"),
        (
            "calibration_examples.id",
            "calibration_examples.batch_id",
            "calibration_examples.scorecard_result_id",
        ),
        "CASCADE",
    ),
    (
        "fk_calibration_answers_participant",
        ("participant_id", "batch_id"),
        ("calibration_participants.id", "calibration_participants.batch_id"),
        "CASCADE",
    ),
    (
        "fk_calibration_answers_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
    (
        "fk_calibration_answers_criterion",
        ("scorecard_result_id", "criterion_id"),
        ("scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"),
        "CASCADE",
    ),
}
ANSWER_INDEXES = {
    "ix_calibration_answers_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_calibration_answers_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_answers_table():
    table = CalibrationAnswer.__table__
    assert table.name == ANSWERS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "batch_id",
        "example_id",
        "participant_id",
        "scorecard_result_id",
        "criterion_id",
        "outcome",
        "created_at",
        "updated_at",
    ]
    for name, length in {**CONTENT_LENGTHS, **ANSWER_EXTRA_LENGTHS}.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == set()
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
    } == ANSWER_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == (
        ANSWER_CHECKS
    )
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_calibration_answers_criterion"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == ANSWER_INDEXES
    assert "HOM-75" in CalibrationAnswer.__doc__
    assert "purge" in CalibrationAnswer.__doc__


ADJUDICATION_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("decided_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_calibration_adjudications_example",
        ("example_id", "batch_id", "scorecard_result_id"),
        (
            "calibration_examples.id",
            "calibration_examples.batch_id",
            "calibration_examples.scorecard_result_id",
        ),
        "CASCADE",
    ),
    (
        "fk_calibration_adjudications_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
    (
        "fk_calibration_adjudications_criterion",
        ("scorecard_result_id", "criterion_id"),
        ("scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"),
        "CASCADE",
    ),
}
ADJUDICATION_INDEXES = {
    "ix_calibration_adjudications_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_calibration_adjudications_decided_by_user_id": (["decided_by_user_id"], False),
    "ix_calibration_adjudications_workspace_meeting": (["workspace_id", "meeting_id"], False),
}


def test_the_orm_declares_the_adjudications_table():
    table = CalibrationAdjudication.__table__
    assert table.name == ADJUDICATIONS
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "batch_id",
        "example_id",
        "scorecard_result_id",
        "criterion_id",
        "outcome",
        "note",
        "decided_by_user_id",
        "created_at",
        "updated_at",
    ]
    for name, length in CONTENT_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert table.c.note.type.__class__.__name__ == "Text"
    assert {column.name for column in table.columns if column.nullable} == {
        "note",
        "decided_by_user_id",
    }
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
    } == ADJUDICATION_FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == (
        ADJUDICATION_CHECKS
    )
    assert [name for name in named if name.startswith("uq_")] == [
        "uq_calibration_adjudications_criterion"
    ]
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == ADJUDICATION_INDEXES
    assert "HOM-75" in CalibrationAdjudication.__doc__
    assert "purge" in CalibrationAdjudication.__doc__


def test_the_frozen_lists_and_the_check_strings():
    assert CALIBRATION_BATCH_STATUSES == ("open", "closed", "cancelled")
    assert CALIBRATION_TITLE_MAX_LENGTH == 200
    assert CALIBRATION_MAX_EXAMPLES == 50
    assert CALIBRATION_MAX_PARTICIPANTS == 20
    # The outcomes a reviewer may give are exactly the engine's and HOM-74's words.
    assert CALIBRATION_OUTCOMES == REVIEW_REVIEWER_OUTCOMES == ENGINE_REVIEWER_OUTCOMES
    assert {
        "ck_calibration_batches_status": "status IN ('open','closed','cancelled')",
        "ck_calibration_batches_revision": "revision >= 1",
        "ck_calibration_batches_title": ("length(trim(title)) > 0 AND length(title) <= 200"),
        "ck_calibration_batches_open_fields": (
            "status <> 'open' OR (closed_at IS NULL AND cancelled_at IS NULL)"
        ),
        "ck_calibration_batches_closed_fields": (
            "status <> 'closed' OR (closed_at IS NOT NULL AND cancelled_at IS NULL)"
        ),
        "ck_calibration_batches_cancelled_fields": (
            "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND closed_at IS NULL)"
        ),
    } == CHECKS
    assert ANSWER_CHECKS["ck_calibration_answers_outcome"] == (
        "outcome IN ('passed','failed','not_applicable')"
    )
    assert ADJUDICATION_CHECKS["ck_calibration_adjudications_note_length"] == (
        f"note IS NULL OR length(note) <= {REVIEW_NOTE_MAX_LENGTH}"
    )
    for check in (
        *CHECKS.values(),
        *EXAMPLE_CHECKS.values(),
        *ANSWER_CHECKS.values(),
        *ADJUDICATION_CHECKS.values(),
    ):
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        # No user column: those are SET NULL on a user's removal; a check on one would
        # make the removal fail.
        for column in (
            "created_by_user_id",
            "closed_by_user_id",
            "cancelled_by_user_id",
            "user_id",
            "decided_by_user_id",
        ):
            assert column not in check, column


def test_every_table_is_a_workspace_table_and_only_examples_refuse_updates():
    for table in TABLES:
        assert table in WORKSPACE_TABLES
        assert table not in APPEND_ONLY_TABLES
        assert table not in NO_DELETE_TABLES
        assert table not in SHARED_CONTENT_TABLES  # never through a share token
        assert table not in UNPROTECTED_TABLES
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    # The example list is fixed at creation; everything else is written by the service
    # of PR 2, and the purge deletes all five as the application role.
    assert EXAMPLES in NO_UPDATE_TABLES
    for table in (BATCHES, PARTICIPANTS, ANSWERS, ADJUDICATIONS):
        assert table not in NO_UPDATE_TABLES
    assert NO_UPDATE_TABLES == (
        "scorecard_results",
        "scorecard_criterion_results",
        "review_results",
        EXAMPLES,
        # HOM-76: a stored score after an appeal never changes.
        "review_appeal_results",
        # HOM-93: a vector is written once; a new model writes a new row.
        "retrieval_embeddings",
    )


# --- cascades and user removal (SPEC acceptance criterion AC6) ------------------------------


def _calibrated(tenant: dict, key: str) -> dict:
    """A stored batch with its example, participant, answer and adjudication."""
    setup = _batched(tenant, key)
    answer_id = _add(CalibrationAnswer, _answer(setup))
    adjudication_id = _add(CalibrationAdjudication, _adjudication(setup))
    return {**setup, "answer_id": answer_id, "adjudication_id": adjudication_id}


def test_deleting_a_batch_a_conversation_a_result_or_a_workspace_removes_the_rows(
    client, email_outbox
):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    gone = _calibrated(second, "cascade_gone")
    by_result = _calibrated(second, "cascade_result")
    elsewhere = _calibrated(first, "cascade_elsewhere")

    # Removals by the owner (DBA here): the FK actions run as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(CalibrationBatch).where(CalibrationBatch.id == gone["batch_id"]))
        db.execute(delete(Meeting).where(Meeting.id == by_result["meeting_id"]))
        db.execute(
            delete(ScorecardResultRecord).where(ScorecardResultRecord.id == elsewhere["result_id"])
        )
        db.commit()
    # The batch cascade took gone's rows, the meeting and the result cascades took the
    # content rows of their conversations, examples first.
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert _ids(table) == set(), table
    assert _ids(PARTICIPANTS) == {by_result["participant_id"], elsewhere["participant_id"]}
    assert _ids(BATCHES) == {by_result["batch_id"], elsewhere["batch_id"]}

    _calibrated(second, "cascade_kept")
    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == second["workspace_id"]))
        db.commit()
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert _ids(table) == set(), table
    assert _ids(PARTICIPANTS) == {elsewhere["participant_id"]}
    assert _ids(BATCHES) == {elsewhere["batch_id"]}


def test_deleting_a_user_clears_every_user_column_and_keeps_the_rows(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user_removal")
    with inspection_session_factory()() as db:
        user = User(email="calibration-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    open_batch = _add(
        CalibrationBatch, _batch(_scored(tenant, "user_removal_open"), created_by_user_id=user_id)
    )
    closed_batch = _add(
        CalibrationBatch,
        _batch(
            _scored(tenant, "user_removal_closed"),
            status="closed",
            closed_at=NOW,
            closed_by_user_id=user_id,
        ),
    )
    cancelled_batch = _add(
        CalibrationBatch,
        _batch(
            _scored(tenant, "user_removal_cancelled"),
            status="cancelled",
            cancelled_at=NOW,
            cancelled_by_user_id=user_id,
        ),
    )
    setup = _batched(tenant, "user_removal_rows")
    participant_id = _add(CalibrationParticipant, _participant(setup, user_id=user_id))
    adjudication_id = _add(
        CalibrationAdjudication, _adjudication(setup, decided_by_user_id=user_id)
    )

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        for batch_id, column in (
            (open_batch, "created_by_user_id"),
            (closed_batch, "closed_by_user_id"),
            (cancelled_batch, "cancelled_by_user_id"),
        ):
            row = db.get(CalibrationBatch, batch_id)
            assert getattr(row, column) is None, column
        # Everything else stays, the state rules included.
        assert (db.get(CalibrationBatch, closed_batch).status) == "closed"
        assert db.get(CalibrationBatch, cancelled_batch).status == "cancelled"
        assert db.get(CalibrationParticipant, participant_id).user_id is None
        assert db.get(CalibrationAdjudication, adjudication_id).decided_by_user_id is None


# --- the migration (SQLite: the same tables as the ORM) -------------------------------------


def _migration_module():
    return importlib.import_module(
        f"backend.migrations.versions.{NEW_REVISION}_calibration_batches"
    )


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == "Blinded calibration batches, schema only (HOM-75, WP14-T04)"
    for words in ("Why", "No data changes", "Downgrade"):
        assert words in doc, words
    # Frozen copies of the model's check strings.
    assert module.CHECKS == CHECKS
    assert module.EXAMPLE_CHECKS == EXAMPLE_CHECKS
    assert module.ANSWER_CHECKS == ANSWER_CHECKS
    assert module.ADJUDICATION_CHECKS == ADJUDICATION_CHECKS


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    # HOM-76 moved the head past this revision; this one stays the only head's parent.
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
            assert sorted(name for name, _ in orm[BATCHES]["checks"]) == sorted(CHECKS)
            assert sorted(name for name, _ in orm[EXAMPLES]["checks"]) == sorted(EXAMPLE_CHECKS)
            assert sorted(name for name, _ in orm[ANSWERS]["checks"]) == sorted(ANSWER_CHECKS)
            assert sorted(name for name, _ in orm[ADJUDICATIONS]["checks"]) == sorted(
                ADJUDICATION_CHECKS
            )
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


# --- PostgreSQL: the schema, the migration, isolation and grants ----------------------------


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
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname NOT IN "
        "(:a, :b, :c, :d, :e)"
    ),
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b, :c, :d, :e)"
    ),
    "constraints": (
        "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
        "AND c.conrelid::regclass::text NOT IN (:a, :b, :c, :d, :e)"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename NOT IN (:a, :b, :c, :d, :e)"
    ),
    "policies": (
        "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' "
        "AND tablename NOT IN (:a, :b, :c, :d, :e)"
    ),
    "grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b, :c, :d, :e)"
    ),
}


def _other_tables() -> dict:
    """Every other table of the schema, so a test can prove it did not change."""
    excluded = dict(zip("abcde", TABLES, strict=True))
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return ("timestamp with time zone", None, nullable, None)


EXPECTED_BATCH_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "title": _varchar(200),
    "status": _varchar(16),
    "created_by_user_id": _varchar(36, "YES"),
    "closed_by_user_id": _varchar(36, "YES"),
    "cancelled_by_user_id": _varchar(36, "YES"),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
    "closed_at": _timestamp("YES"),
    "cancelled_at": _timestamp("YES"),
    "revision": ("integer", None, "NO", None),
}

EXPECTED_EXAMPLE_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "batch_id": _varchar(36),
    "meeting_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "position": ("integer", None, "NO", None),
    "created_at": _timestamp(),
}

EXPECTED_PARTICIPANT_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "batch_id": _varchar(36),
    "user_id": _varchar(36, "YES"),
    "created_at": _timestamp(),
}

EXPECTED_ANSWER_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "batch_id": _varchar(36),
    "example_id": _varchar(36),
    "participant_id": _varchar(36),
    "scorecard_result_id": _varchar(36),
    "criterion_id": _varchar(64),
    "outcome": _varchar(24),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
}

EXPECTED_ADJUDICATION_COLUMNS = {
    **{
        name: column for name, column in EXPECTED_ANSWER_COLUMNS.items() if name != "participant_id"
    },
    "note": ("text", None, "YES", None),
    "decided_by_user_id": _varchar(36, "YES"),
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
EXAMPLE_KEY = (
    "FOREIGN KEY (example_id, batch_id, scorecard_result_id) REFERENCES "
    "calibration_examples(id, batch_id, scorecard_result_id) ON DELETE CASCADE"
)
BATCH_KEY = (
    "FOREIGN KEY (batch_id, workspace_id) REFERENCES "
    "calibration_batches(id, workspace_id) ON DELETE CASCADE"
)
PARTICIPANT_KEY = (
    "FOREIGN KEY (participant_id, batch_id) REFERENCES "
    "calibration_participants(id, batch_id) ON DELETE CASCADE"
)
CRITERION_KEY = (
    "FOREIGN KEY (scorecard_result_id, criterion_id) REFERENCES "
    "scorecard_criterion_results(result_id, criterion_id) ON DELETE CASCADE"
)

EXPECTED_BATCH_CONSTRAINTS = dict(
    [
        (f"{BATCHES}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(BATCHES, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(BATCHES, "created_by_user_id", "users", "SET NULL"),
        _foreign_key(BATCHES, "closed_by_user_id", "users", "SET NULL"),
        _foreign_key(BATCHES, "cancelled_by_user_id", "users", "SET NULL"),
    ]
)

EXPECTED_EXAMPLE_CONSTRAINTS = dict(
    [
        (f"{EXAMPLES}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(EXAMPLES, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(EXAMPLES, "meeting_id", "meetings", "CASCADE"),
        ("fk_calibration_examples_batch", BATCH_KEY),
        ("fk_calibration_examples_scorecard_result", RESULT_SCOPE),
        ("uq_calibration_examples_meeting", "UNIQUE (batch_id, meeting_id)"),
        # ``position`` is reserved in PostgreSQL, so the catalog quotes it.
        ("uq_calibration_examples_position", 'UNIQUE (batch_id, "position")'),
    ]
)

EXPECTED_PARTICIPANT_CONSTRAINTS = dict(
    [
        (f"{PARTICIPANTS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(PARTICIPANTS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(PARTICIPANTS, "user_id", "users", "SET NULL"),
        ("fk_calibration_participants_batch", BATCH_KEY),
        ("uq_calibration_participants_user", "UNIQUE (batch_id, user_id)"),
    ]
)

EXPECTED_ANSWER_CONSTRAINTS = dict(
    [
        (f"{ANSWERS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(ANSWERS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(ANSWERS, "meeting_id", "meetings", "CASCADE"),
        ("fk_calibration_answers_example", EXAMPLE_KEY),
        ("fk_calibration_answers_participant", PARTICIPANT_KEY),
        ("fk_calibration_answers_result", RESULT_SCOPE),
        ("fk_calibration_answers_criterion", CRITERION_KEY),
        ("uq_calibration_answers_criterion", "UNIQUE (participant_id, example_id, criterion_id)"),
    ]
)

EXPECTED_ADJUDICATION_CONSTRAINTS = dict(
    [
        (f"{ADJUDICATIONS}_pkey", "PRIMARY KEY (id)"),
        _foreign_key(ADJUDICATIONS, "workspace_id", "workspaces", "CASCADE"),
        _foreign_key(ADJUDICATIONS, "meeting_id", "meetings", "CASCADE"),
        _foreign_key(ADJUDICATIONS, "decided_by_user_id", "users", "SET NULL"),
        ("fk_calibration_adjudications_example", EXAMPLE_KEY),
        ("fk_calibration_adjudications_result", RESULT_SCOPE),
        ("fk_calibration_adjudications_criterion", CRITERION_KEY),
        ("uq_calibration_adjudications_criterion", "UNIQUE (example_id, criterion_id)"),
    ]
)


def _index(name: str, table: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_TABLES = {
    BATCHES: {
        "columns": EXPECTED_BATCH_COLUMNS,
        "constraints": EXPECTED_BATCH_CONSTRAINTS,
        "checks": CHECKS,
        "indexes": dict(
            [
                _index(f"{BATCHES}_pkey", BATCHES, "id", unique=True),
                _index("ix_calibration_batches_workspace_id", BATCHES, "workspace_id"),
                _index("uq_calibration_batches_scope", BATCHES, "id, workspace_id", unique=True),
                _index("ix_calibration_batches_list", BATCHES, "workspace_id, created_at, id"),
            ]
        ),
    },
    EXAMPLES: {
        "columns": EXPECTED_EXAMPLE_COLUMNS,
        "constraints": EXPECTED_EXAMPLE_CONSTRAINTS,
        "checks": EXAMPLE_CHECKS,
        "indexes": dict(
            [
                _index(f"{EXAMPLES}_pkey", EXAMPLES, "id", unique=True),
                _index(
                    "ix_calibration_examples_scorecard_result_id", EXAMPLES, "scorecard_result_id"
                ),
                _index(
                    "uq_calibration_examples_scope",
                    EXAMPLES,
                    "id, batch_id, scorecard_result_id",
                    unique=True,
                ),
                # The backing indexes of the two unique constraints (``position`` is
                # reserved in PostgreSQL, so the catalog quotes it).
                _index(
                    "uq_calibration_examples_meeting", EXAMPLES, "batch_id, meeting_id", unique=True
                ),
                _index(
                    "uq_calibration_examples_position",
                    EXAMPLES,
                    'batch_id, "position"',
                    unique=True,
                ),
                _index(
                    "ix_calibration_examples_workspace_meeting",
                    EXAMPLES,
                    "workspace_id, meeting_id",
                ),
            ]
        ),
    },
    PARTICIPANTS: {
        "columns": EXPECTED_PARTICIPANT_COLUMNS,
        "constraints": EXPECTED_PARTICIPANT_CONSTRAINTS,
        "checks": {},
        "indexes": dict(
            [
                _index(f"{PARTICIPANTS}_pkey", PARTICIPANTS, "id", unique=True),
                _index("ix_calibration_participants_user_id", PARTICIPANTS, "user_id"),
                _index(
                    "uq_calibration_participants_scope", PARTICIPANTS, "id, batch_id", unique=True
                ),
                _index(
                    "uq_calibration_participants_user",
                    PARTICIPANTS,
                    "batch_id, user_id",
                    unique=True,
                ),
            ]
        ),
    },
    ANSWERS: {
        "columns": EXPECTED_ANSWER_COLUMNS,
        "constraints": EXPECTED_ANSWER_CONSTRAINTS,
        "checks": ANSWER_CHECKS,
        "indexes": dict(
            [
                _index(f"{ANSWERS}_pkey", ANSWERS, "id", unique=True),
                _index(
                    "ix_calibration_answers_scorecard_result_id", ANSWERS, "scorecard_result_id"
                ),
                _index(
                    "ix_calibration_answers_workspace_meeting", ANSWERS, "workspace_id, meeting_id"
                ),
                _index(
                    "uq_calibration_answers_criterion",
                    ANSWERS,
                    "participant_id, example_id, criterion_id",
                    unique=True,
                ),
            ]
        ),
    },
    ADJUDICATIONS: {
        "columns": EXPECTED_ADJUDICATION_COLUMNS,
        "constraints": EXPECTED_ADJUDICATION_CONSTRAINTS,
        "checks": ADJUDICATION_CHECKS,
        "indexes": dict(
            [
                _index(f"{ADJUDICATIONS}_pkey", ADJUDICATIONS, "id", unique=True),
                _index(
                    "ix_calibration_adjudications_scorecard_result_id",
                    ADJUDICATIONS,
                    "scorecard_result_id",
                ),
                _index(
                    "ix_calibration_adjudications_decided_by_user_id",
                    ADJUDICATIONS,
                    "decided_by_user_id",
                ),
                _index(
                    "ix_calibration_adjudications_workspace_meeting",
                    ADJUDICATIONS,
                    "workspace_id, meeting_id",
                ),
                _index(
                    "uq_calibration_adjudications_criterion",
                    ADJUDICATIONS,
                    "example_id, criterion_id",
                    unique=True,
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
    if table == EXAMPLES:
        # A batch's example list is fixed at creation; the purge deletes the rows.
        assert shape["grants"] == ["DELETE", "INSERT", "SELECT"]
    else:
        # The service of PR 2 upserts rows; the purge deletes them.
        assert shape["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"]
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_five_tables(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_five_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    _calibrated(tenant, "migration")
    results = _scalar("SELECT count(*) FROM scorecard_results")
    meetings = _scalar("SELECT count(*) FROM meetings")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()  # the head's own tables included
        command.downgrade(config, BASE_REVISION)
        others = _other_tables()  # the five tables are gone with the downgrade
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}")
            assert _shape(table) == EMPTY_SHAPE
        assert _other_tables() == others  # every other table as it was
        # Only the calibration rows are lost: the conversations and their results stay.
        assert _scalar("SELECT count(*) FROM scorecard_results") == results
        assert _scalar("SELECT count(*) FROM meetings") == meetings

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == others

        # The migrated schema refuses the same deny cases.
        for rules in RULES:
            rules(tenant)
        migrated = _calibrated(tenant, "migration_migrated")
        assert migrated["answer_id"] in _ids(ANSWERS)

        # Down and up again with rows present, then on to the head.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        command.upgrade(config, "head")
        # Back at the head, with its HOM-76 appeal tables again.
        assert _other_tables() == head_others
    finally:
        reset_settings_cache()


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _batched(_tenant(client, email_outbox, "tenant_a"), "tenant_a")
    second = _batched(_tenant(client, email_outbox, "tenant_b"), "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.add(CalibrationAnswer(**_answer(first)))
        db.add(CalibrationAdjudication(**_adjudication(first)))
        db.commit()

    # Bound to B: none of A's rows in any of the five tables; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert (
                db.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                    {"w": first["workspace_id"]},
                )
                == 0
            )
        db.add(CalibrationAnswer(**_answer(second)))
        db.commit()

    # Bound to A: only A's rows; an insert with B's ids fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]]
    for model, values in (
        (CalibrationBatch, _batch(second)),
        (CalibrationParticipant, _participant(second)),
        (CalibrationAnswer, _answer(second)),
        (CalibrationAdjudication, _adjudication(second)),
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
def test_the_app_role_updates_four_tables_and_never_the_examples(client, email_outbox):
    _requires_postgres()
    first = _batched(_tenant(client, email_outbox, "grants_a"), "grants_a")
    second = _batched(_tenant(client, email_outbox, "grants_b"), "grants_b")
    full = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    frozen = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    for table in (BATCHES, PARTICIPANTS, ANSWERS, ADJUDICATIONS):
        assert _privileges(table) == full, table
    assert _privileges(EXAMPLES) == frozen

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.add(CalibrationAnswer(**_answer(first)))
        db.commit()

    # Bound to B: A's rows are invisible, so an update or a delete aimed at them
    # changes none of them (B's own rows stay untouched too).
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for statement in (
            f"UPDATE {BATCHES} SET title = 'x' WHERE workspace_id = :w",  # noqa: S608
            f"UPDATE {ANSWERS} SET outcome = 'failed' WHERE workspace_id = :w",  # noqa: S608
            f"DELETE FROM {EXAMPLES} WHERE workspace_id = :w",  # noqa: S608
        ):
            assert db.execute(text(statement), {"w": first["workspace_id"]}).rowcount == 0
        db.commit()
    assert len(_ids(BATCHES)) == 2
    assert len(_ids(ANSWERS)) == 1

    # Bound to A: the upserts of PR 2, no UPDATE of the example list, and still none
    # after the policies and the grants are applied again.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {BATCHES} SET status = 'closed', closed_at = :now, "  # noqa: S608
                "revision = revision + 1 WHERE workspace_id = :w"
            ),
            {"now": NOW, "w": first["workspace_id"]},
        )
        assert updated.rowcount == 1
        assert db.execute(text(f"UPDATE {ANSWERS} SET outcome = 'failed'")).rowcount == 1  # noqa: S608
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(f"UPDATE {EXAMPLES} SET position = 1"))  # noqa: S608
        db.rollback()
        db.commit()
    _apply_as_owner()  # idempotent: no revoke for these tables
    for table in (BATCHES, PARTICIPANTS, ANSWERS, ADJUDICATIONS):
        assert _privileges(table) == full, table
    assert _privileges(EXAMPLES) == frozen

    # Allow-control, bound to A: the purge's own statements delete the conversation's
    # calibration rows; the other conversation's rows, the batches and the
    # participants are not touched.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        meeting = db.get(Meeting, first["meeting_id"])
        _remove_content(db, workspace_id=first["workspace_id"], meeting=meeting)
        db.commit()
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert _ids(table, meeting_id=first["meeting_id"]) == set()
    # The other conversation keeps its example; batches and participants stay.
    assert len(_ids(EXAMPLES, meeting_id=second["meeting_id"])) == 1
    assert len(_ids(BATCHES)) == 2
    assert len(_ids(PARTICIPANTS)) == 2


# --- the conversation purge and the backup reconciliation (SPEC criteria AC7 and AC8) --------


def _reviewed_calibration(tenant: dict, key: str, meeting_id: str | None = None) -> dict:
    """A scored conversation with a review item, its HOM-74 rows and a calibration."""
    setup = _batched(tenant, key, meeting_id=meeting_id)
    item_id = _add(ReviewItem, _item_values(setup, scorecard_result_id=setup["result_id"]))
    setup = {**setup, "item_id": item_id}
    _add(ReviewCriterionDecision, _decision(setup))
    _add(ReviewResultRecord, _human(setup))
    _add(CalibrationAnswer, _answer(setup))
    _add(CalibrationAdjudication, _adjudication(setup))
    return setup


def test_the_purge_deletes_the_calibration_rows_before_the_review_rows_and_counts_them(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "purge-order")
    setup = _reviewed_calibration(tenant, "ordered")
    kept = _reviewed_calibration(tenant, "kept")
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
    # The calibration rows go first: the examples' result cascade and the answers' and
    # the adjudications' example cascade would otherwise remove them uncounted.
    for table in (ANSWERS, ADJUDICATIONS, EXAMPLES):
        assert deletes.index(table) < deletes.index(DECISIONS), table
    assert deletes.index(DECISIONS) < deletes.index(ITEMS)
    assert deletes.index(ITEMS) < deletes.index("scorecard_results")
    # One answer, one adjudication, one example, one decision, one human result, one
    # item, one result, one criterion row and the transcript revision.
    assert removed == 9
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert _ids(table, meeting_id=kept["meeting_id"]), table
    # Batches and participants are not conversation content and stay.
    assert len(_ids(BATCHES)) == 2
    assert len(_ids(PARTICIPANTS)) == 2


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "calibration-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"calibration-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    for meeting_id, key in ((queued, "calibration_purged"), (kept, "calibration_kept")):
        _reviewed_calibration(tenant, key, meeting_id=meeting_id)
    assert len(_ids(ANSWERS, meeting_id=queued)) == 1
    assert len(_ids(ANSWERS, meeting_id=kept)) == 1

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert _ids(table, meeting_id=queued) == set(), table
    # Another conversation of the workspace keeps its calibration rows.
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert len(_ids(table, meeting_id=kept)) == 1, table
    # The batch and the participant rows are not conversation content and stay.
    assert len(_ids(BATCHES)) == 2
    assert len(_ids(PARTICIPANTS)) == 2
    # The purge deletes the answer, the adjudication, the example, the item, the
    # decision, the human result, the result and its criterion row itself (they are
    # counted), before the result's cascade could.
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 8


def test_a_held_conversation_keeps_its_calibration_rows_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "calibration-hold")
    meeting_id = _processed(client, tenant, settings, key="calibration-hold-0001")
    _reviewed_calibration(tenant, "calibration_held", meeting_id=meeting_id)
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
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert len(_ids(table, meeting_id=meeting_id)) == 1, table
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    assert len(_ids(EXAMPLES, meeting_id=meeting_id)) == 1

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
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert _ids(table, meeting_id=meeting_id) == set(), table


def test_the_backup_reconciliation_counts_the_three_content_tables():
    for table in (EXAMPLES, ANSWERS, ADJUDICATIONS):
        assert table in RECONCILED_TABLES
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns
    # Batches and participants have no conversation of their own: the reconciliation
    # counts content rows by meeting, so they are not content tables.
    assert BATCHES not in RECONCILED_TABLES
    assert PARTICIPANTS not in RECONCILED_TABLES


# --- the ADR (SPEC acceptance criterion AC10) ------------------------------------------------


def _adr() -> str:
    from backend.observability.release import PROJECT_ROOT

    path = (
        PROJECT_ROOT.parent
        / "docs"
        / "architecture"
        / "decisions"
        / "0056-blinded-calibration-and-agreement.md"
    )
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0056: ")
    assert "Proposed (HOM-75, WP14-T04, F46)" in content
    assert "ADR 0051" in content and "ADR 0053" in content
    for heading in ("Context", "Decision", "Consequences", "Not built"):
        assert re.search(rf"^## {heading}\s*$", content, re.MULTILINE), heading
    for section in ("D1", "D2", "D3", "D4", "D5", "D6"):
        assert re.search(rf"^### {section}\.", content, re.MULTILINE), section


def test_the_adr_uses_no_em_dash_and_no_double_hyphen_dash():
    content = _adr()
    assert "—" not in content
    assert not re.search(r"(^|\s)--(\s|$)", content, re.MULTILINE)


# What the SPEC says the ADR records; any one spelling of a topic is enough.
ADR_TOPICS = {
    "the_tables": (BATCHES, EXAMPLES, PARTICIPANTS, ANSWERS, ADJUDICATIONS),
    "blinding": ("blinding", "Blinded"),
    "write_rules": ("review:manage", "review:read"),
    "route": ("/api/v1/calibration-batches",),
    "admin": ("calibration_admin_required",),
    "participant": ("calibration_not_participant",),
    "revision": ("expected_revision", "calibration_batch_revision_conflict"),
    "close": ("calibration_adjudication_incomplete",),
    "report": ("agreement_bp", "kappa_e4", "confusion"),
    "audit": ("calibration_answer.recorded",),
    "purge": ("purge",),
    "backup": ("reconcil",),
    "not_built": ("HOM-249",),
    "fractions": ("fractions.Fraction",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings
