"""HOM-56 PR 1: the stored scorecard result tables and their one migration.

``scorecard_results`` holds one machine scoring of one conversation against one
published scorecard version.  ``scorecard_criterion_results`` holds one row per
criterion of that result.  A stored result never changes, but the conversation purge
removes it with the rest of the conversation's derived content.  The tests check:

* the ORM declares the columns, the named constraints and the indexes, and
  ``database/rls.py`` lists both tables as workspace tables, not as append-only;
* no column can hold an NPS, a churn value, a probability or a score: the only integer
  columns are the weights, the threshold and the position;
* the ORM refuses to update any column of a stored row, or to delete one, in either
  table, while a flush that changes nothing works;
* every deny case of the SPEC (section 4) is refused by its named constraint, and the
  closest allow case passes (SQLite and PostgreSQL); a criterion row always has its
  result's workspace and meeting (composite foreign key);
* on SQLite, the migration builds the same two tables as the ORM, and its downgrade
  removes them and nothing else;
* on PostgreSQL: the ORM schema (``create_all``) and the migrated schema are the same;
  upgrade from ``e4b8d1f6a3c7`` creates the two tables, their policy and their grants,
  and downgrade removes them and leaves every other table as it was; tenant isolation;
  the application role may SELECT, INSERT and DELETE its own workspace's rows and none
  of another's, and may never UPDATE them, also after the policies are applied again;
  deleting a workspace, a meeting or a scorecard version still removes
  the rows;
* deleting a transcript revision removes the results scored from it;
* the conversation purge removes the rows of the purged conversation only, a legal hold
  keeps them until its release, and the backup reconciliation counts both tables.
"""

from __future__ import annotations

import importlib
import itertools
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Integer, Numeric, create_engine, delete, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.backup.reconcile import CONTENT_TABLES as RECONCILED_TABLES
from backend.config import Settings
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    NO_UPDATE_TABLES,
    POLICIES,
    POLICY_NAME,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    CONVERSATION_DIRECTION_CHECK,
    CONVERSATION_TYPE_CHECK,
    CRITERION_CHECK_OUTCOME_CHECK,
    CRITERION_DECIDED_BY_CHECK,
    CRITERION_KEY_CHECK,
    CRITERION_POSITION_CHECK,
    CRITERION_REASON_CHECK,
    CRITERION_REVIEW_CHECK,
    CRITERION_WEIGHT_CHECK,
    SCORECARD_KEY_CHECK,
    SCORECARD_OUTCOME_CHECK,
    SCORECARD_RESULT_COMPLETE_CHECK,
    SCORECARD_RESULT_DIGESTS_CHECK,
    SCORECARD_RESULT_REASON_CHECK,
    SCORECARD_RESULT_THRESHOLD_CHECK,
    SCORECARD_RESULT_WEIGHTS_CHECK,
    CriterionResultRecord,
    Meeting,
    PurgeState,
    ScorecardResultImmutableError,
    ScorecardResultRecord,
    ScorecardVersion,
    TranscriptRevision,
    TranscriptSource,
    Workspace,
    new_id,
    utcnow,
)
from backend.database.tenant_context import bind_tenant_context
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

NEW_REVISION = "5067ea773c0b"
BASE_REVISION = "e4b8d1f6a3c7"  # HOM-89: webhook delivery ledger
RESULTS = "scorecard_results"
CRITERIA = "scorecard_criterion_results"
TABLES = (RESULTS, CRITERIA)
DIGEST = "d" * 64
DOCUMENT = {"name": "Inbound sales", "pass_threshold": 80, "criteria": []}
# The JSON columns are opaque to the schema; PR 2 stores ``CheckRun.to_json()`` and
# ``ScorecardResult.to_json()`` in them.
CHECK_RUN = {
    "checks_version": "conversation-checks/1",
    "language": "fa",
    "evaluator": None,
    "results": [],
}
RESULT = {
    "scorecards_version": "scorecards/1",
    "checks_version": "conversation-checks/1",
    "definition_digest": DIGEST,
    "outcome": "passed",
    "score": 100.0,
    "criteria": [],
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
    credentials = register_user(client, email_outbox, email=f"scorecard-result-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _parents(tenant: dict, key: str = "inbound_sales", meeting_id: str | None = None) -> dict:
    """A published scorecard version and a conversation with a transcript revision.

    Without ``meeting_id`` a new conversation and its first revision are made; a
    processed conversation keeps its current revision.
    """
    with inspection_session_factory()() as db:
        if meeting_id is None:
            meeting = Meeting(
                workspace_id=tenant["workspace_id"],
                created_by_user_id=tenant["user_id"],
                title="Scored call",
            )
            db.add(meeting)
            db.flush()
            meeting_id = meeting.id
            revision = TranscriptRevision(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                revision_number=1,
                source=TranscriptSource.ASR,
                reason="processing",
                language="fa",
                segment_count=0,
                is_current=True,
            )
            db.add(revision)
            db.flush()
            revision_id = revision.id
        else:
            revision_id = db.scalar(
                select(TranscriptRevision.id).where(
                    TranscriptRevision.meeting_id == meeting_id,
                    TranscriptRevision.is_current.is_(True),
                )
            )
            assert revision_id is not None
        version = ScorecardVersion(
            workspace_id=tenant["workspace_id"],
            scorecard_key=key,
            version=1,
            scorecards_version="scorecards/1",
            document=DOCUMENT,
            digest=DIGEST,
            reason="first publish",
        )
        db.add(version)
        db.commit()
        return {
            **tenant,
            "meeting_id": meeting_id,
            "transcript_revision_id": revision_id,
            "scorecard_version_id": version.id,
        }


def _result(parents: dict, **extra: object) -> dict[str, object]:
    """A valid result row; each call has its own input digest."""
    return {
        "workspace_id": parents["workspace_id"],
        "meeting_id": parents["meeting_id"],
        "scorecard_version_id": parents["scorecard_version_id"],
        "transcript_revision_id": parents["transcript_revision_id"],
        "scorecards_version": "scorecards/1",
        "checks_version": "conversation-checks/1",
        "definition_digest": DIGEST,
        "input_digest": uuid.uuid4().hex * 2,
        "conversation_type": "call",
        "direction": "inbound",
        "outcome": "passed",
        "reason": None,
        "complete": True,
        "pass_threshold": 80,
        "applicable_weight": 100,
        "earned_weight": 100,
        "failed_weight": 0,
        "insufficient_weight": 0,
        "check_run": CHECK_RUN,
        "result": RESULT,
        **extra,
    }


def _criterion(parents: dict, result_id: str, position: int, **extra: object) -> dict[str, object]:
    """A valid criterion row: a passed rule check, with ``c<position>`` as its id."""
    return {
        "workspace_id": parents["workspace_id"],
        "result_id": result_id,
        "meeting_id": parents["meeting_id"],
        "position": position,
        "criterion_id": f"c{position}",
        "weight": 100,
        "auto_fail": False,
        "na_allowed": False,
        "outcome": "passed",
        "reason": None,
        "decided_by": "rule",
        "review_required": False,
        "review_reasons": [],
        "check_outcome": "passed",
        "check_reason": None,
        "evidence": [],
        "candidate_evidence": [],
        **extra,
    }


def _add(model, values: dict[str, object]) -> str:
    """Insert one row through the ORM as the test login role and commit it."""
    with inspection_session_factory()() as db:
        row = model(**values)
        db.add(row)
        db.commit()
        return row.id


# SQLite names the columns of a broken unique constraint, not the constraint.
UNIQUE_COLUMNS = {
    "uq_scorecard_result_input": (RESULTS, ("scorecard_version_id", "meeting_id", "input_digest")),
    "uq_scorecard_criterion_result_position": (CRITERIA, ("result_id", "position")),
    "uq_scorecard_criterion_result_criterion": (CRITERIA, ("result_id", "criterion_id")),
}


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        # SQLite does not name a broken foreign key either.  Each deny names a workspace
        # and a meeting that exist, so only the composite key can refuse it.
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


def _too_long_key(model, values: dict[str, object]) -> None:
    """A 65-character ``criterion_id``: PostgreSQL refuses the length first."""
    if not _on_postgres():
        _refused(model, values, "ck_scorecard_criterion_result_key")
        return
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(DBAPIError, match=r"character varying\(64\)"):
            db.flush()
        db.rollback()


def _refused_null(model, values: dict[str, object], column: str) -> None:
    """The insert fails on the NOT NULL of ``column`` (SQLite and PostgreSQL wording)."""
    table = model.__tablename__
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(
            IntegrityError, match=rf"{table}\.{column}\b|null value in column \"{column}\""
        ):
            db.flush()
        db.rollback()


def _ids(table: str) -> set[str]:
    with inspection_session_factory()() as db:
        return set(db.execute(text(f"SELECT id FROM {table}")).scalars())  # noqa: S608


# --- the deny and allow cases (SPEC section 4) ---------------------------------------------


def _assert_one_result_per_input(parents: dict) -> None:
    first = _result(parents)
    _add(ScorecardResultRecord, first)
    _refused(
        ScorecardResultRecord,
        _result(parents, input_digest=first["input_digest"]),
        "uq_scorecard_result_input",
    )
    _add(ScorecardResultRecord, _result(parents))  # same version and meeting, other input


def _assert_result_outcome_rules(parents: dict) -> None:
    # The four outcomes only.  An unknown outcome also breaks the reason rule.
    _refused(
        ScorecardResultRecord,
        _result(parents, outcome="pass"),
        "ck_scorecard_result_outcome",
        "ck_scorecard_result_reason",
    )
    _add(ScorecardResultRecord, _result(parents, outcome="passed"))

    # Each outcome takes only its own reasons (README "Scorecard outcome").
    reason_cases = [
        (("passed", "below_threshold"), ("failed", "below_threshold")),
        (("failed", None), ("failed", "auto_fail")),
        (("not_applicable", "evidence_missing"), ("not_applicable", "no_applicable_criteria")),
        (
            ("insufficient_evidence", "no_applicable_criteria"),
            ("insufficient_evidence", "evidence_missing"),
        ),
    ]
    for (outcome, reason), (allowed_outcome, allowed_reason) in reason_cases:
        _refused(
            ScorecardResultRecord,
            _result(parents, outcome=outcome, reason=reason),
            "ck_scorecard_result_reason",
        )
        _add(
            ScorecardResultRecord, _result(parents, outcome=allowed_outcome, reason=allowed_reason)
        )


def _assert_result_number_rules(parents: dict) -> None:
    for threshold in (0, 101):
        _refused(
            ScorecardResultRecord,
            _result(parents, pass_threshold=threshold),
            "ck_scorecard_result_threshold",
        )
    for threshold in (1, 100):
        _add(ScorecardResultRecord, _result(parents, pass_threshold=threshold))

    def weights(applicable: int, earned: int, failed: int, insufficient: int) -> dict:
        return {
            "applicable_weight": applicable,
            "earned_weight": earned,
            "failed_weight": failed,
            "insufficient_weight": insufficient,
            "complete": insufficient == 0,
        }

    # The denominator is the sum of the three parts, at most 100, and no part is negative.
    for denied in ((50, 30, 10, 0), (101, 101, 0, 0), (10, -1, 11, 0)):
        _refused(
            ScorecardResultRecord,
            _result(parents, **weights(*denied)),
            "ck_scorecard_result_weights",
        )
    for allowed in ((50, 30, 10, 10), (100, 100, 0, 0), (0, 0, 0, 0), (10, 0, 10, 0)):
        _add(ScorecardResultRecord, _result(parents, **weights(*allowed)))

    # ``complete`` is true only when no weight is insufficient.  A weight-0 criterion can
    # be insufficient, so ``complete`` false with no insufficient weight is allowed.
    split = {"applicable_weight": 100, "earned_weight": 80, "failed_weight": 10}
    _refused(
        ScorecardResultRecord,
        _result(parents, **split, insufficient_weight=10, complete=True),
        "ck_scorecard_result_complete",
    )
    _add(ScorecardResultRecord, _result(parents, **split, insufficient_weight=10, complete=False))
    _add(ScorecardResultRecord, _result(parents, complete=False))


def _assert_result_text_rules(parents: dict) -> None:
    for column in ("definition_digest", "input_digest"):
        _refused(
            ScorecardResultRecord,
            _result(parents, **{column: "a" * 63}),
            "ck_scorecard_result_digests",
        )
        _add(ScorecardResultRecord, _result(parents, **{column: uuid.uuid4().hex * 2}))

    _refused(
        ScorecardResultRecord,
        _result(parents, conversation_type="chat"),
        "ck_scorecard_result_conversation_type",
    )
    _add(ScorecardResultRecord, _result(parents, conversation_type="call"))

    _refused(
        ScorecardResultRecord,
        _result(parents, direction="sideways"),
        "ck_scorecard_result_direction",
    )
    _add(ScorecardResultRecord, _result(parents, direction="inbound"))
    _add(ScorecardResultRecord, _result(parents, direction=None))  # not known

    # Every result names the revision its evidence points into (Addendum A3).
    _refused_null(
        ScorecardResultRecord,
        _result(parents, transcript_revision_id=None),
        "transcript_revision_id",
    )
    _add(
        ScorecardResultRecord,
        _result(parents, transcript_revision_id=parents["transcript_revision_id"]),
    )


def _assert_criterion_unique_rules(parents: dict) -> None:
    first = _add(ScorecardResultRecord, _result(parents))
    second = _add(ScorecardResultRecord, _result(parents))
    _add(CriterionResultRecord, _criterion(parents, first, 0, criterion_id="greeting"))

    _refused(
        CriterionResultRecord,
        _criterion(parents, first, 0, criterion_id="closing"),
        "uq_scorecard_criterion_result_position",
    )
    _add(CriterionResultRecord, _criterion(parents, first, 1, criterion_id="closing"))

    _refused(
        CriterionResultRecord,
        _criterion(parents, first, 2, criterion_id="greeting"),
        "uq_scorecard_criterion_result_criterion",
    )
    _add(CriterionResultRecord, _criterion(parents, second, 0, criterion_id="greeting"))


DENIED_CRITERION_KEYS = ["Greeting", "1greeting", "g", "greet-ing", "", "greet ing", "gré"]
ALLOWED_CRITERION_KEYS = ["greeting", "g1", "fee_explained", "g" * 64]


def _assert_criterion_number_and_key_rules(parents: dict) -> None:
    result_id = _add(ScorecardResultRecord, _result(parents))
    for position in (-1, 100):
        _refused(
            CriterionResultRecord,
            _criterion(parents, result_id, position, criterion_id="probe"),
            "ck_scorecard_criterion_result_position",
        )
    for position in (0, 99):
        _add(CriterionResultRecord, _criterion(parents, result_id, position))

    for weight in (101, -1):
        _refused(
            CriterionResultRecord,
            _criterion(parents, result_id, 10, weight=weight),
            "ck_scorecard_criterion_result_weight",
        )
    for position, weight in ((10, 0), (11, 100)):
        _add(CriterionResultRecord, _criterion(parents, result_id, position, weight=weight))

    # The ``scorecard_key`` rule: ``^[a-z][a-z0-9_]{1,63}$``.
    for key in DENIED_CRITERION_KEYS:
        _refused(
            CriterionResultRecord,
            _criterion(parents, result_id, 20, criterion_id=key),
            "ck_scorecard_criterion_result_key",
        )
    _too_long_key(CriterionResultRecord, _criterion(parents, result_id, 20, criterion_id="g" * 65))
    for position, key in enumerate(ALLOWED_CRITERION_KEYS, start=20):
        _add(CriterionResultRecord, _criterion(parents, result_id, position, criterion_id=key))


def _assert_criterion_outcome_rules(parents: dict) -> None:
    result_id = _add(ScorecardResultRecord, _result(parents))
    slot = itertools.count()

    def row(**extra: object) -> dict[str, object]:
        return _criterion(parents, result_id, next(slot), **extra)

    # The four outcomes only.  The deny and the allow differ in the outcome alone; an
    # unknown outcome also breaks the decided_by and reason rules.
    missing = {"reason": "no_transcript", "decided_by": None, "check_outcome": "not_computable"}
    _refused(
        CriterionResultRecord,
        row(outcome="not_computable", **missing),
        "ck_scorecard_criterion_result_outcome",
        "ck_scorecard_criterion_result_decided_by",
        "ck_scorecard_criterion_result_reason",
    )
    _add(CriterionResultRecord, row(outcome="insufficient_evidence", **missing))

    # Who decided (README "Criterion outcome", rows 1 and 3 to 9; never a reviewer).
    decided_cases = [
        (
            ("insufficient_evidence", "no_transcript", "rule"),
            ("insufficient_evidence", "no_transcript", None),
        ),
        (("not_applicable", "not_triggered", "model"), ("not_applicable", "not_triggered", "rule")),
        (("passed", None, "reviewer"), ("passed", None, "model")),
        (("passed", None, None), ("passed", None, "rule")),
    ]
    fields = ("outcome", "reason", "decided_by")
    for denied, allowed in decided_cases:
        _refused(
            CriterionResultRecord,
            row(**dict(zip(fields, denied, strict=True))),
            "ck_scorecard_criterion_result_decided_by",
        )
        _add(CriterionResultRecord, row(**dict(zip(fields, allowed, strict=True))))

    # A reason only for not_applicable and insufficient_evidence, and always there.
    _refused(
        CriterionResultRecord,
        row(outcome="failed", reason="x"),
        "ck_scorecard_criterion_result_reason",
    )
    _add(CriterionResultRecord, row(outcome="failed", reason=None))
    _refused(
        CriterionResultRecord,
        row(outcome="not_applicable", reason=None),
        "ck_scorecard_criterion_result_reason",
    )
    _add(CriterionResultRecord, row(outcome="not_applicable", reason="not_triggered"))


def _assert_criterion_review_rules(parents: dict) -> None:
    result_id = _add(ScorecardResultRecord, _result(parents))
    slot = itertools.count()

    def row(**extra: object) -> dict[str, object]:
        return _criterion(parents, result_id, next(slot), **extra)

    insufficient = {"outcome": "insufficient_evidence", "decided_by": None}
    # Review is required only through README row 4.
    _refused(
        CriterionResultRecord,
        row(outcome="failed", review_required=True),
        "ck_scorecard_criterion_result_review",
    )
    _add(
        CriterionResultRecord,
        row(
            **insufficient,
            reason="review_required",
            review_required=True,
            review_reasons=["low_confidence"],
        ),
    )
    _refused(
        CriterionResultRecord,
        row(**insufficient, reason="no_transcript", review_required=True),
        "ck_scorecard_criterion_result_review",
    )
    _add(CriterionResultRecord, row(**insufficient, reason="no_transcript", review_required=False))

    _refused(
        CriterionResultRecord,
        row(check_outcome="error"),
        "ck_scorecard_criterion_result_check_outcome",
    )
    _add(
        CriterionResultRecord,
        row(**insufficient, reason="no_transcript", check_outcome="not_computable"),
    )
    _add(CriterionResultRecord, row(**insufficient, reason="direction_unknown", check_outcome=None))


def _assert_criterion_scope_rules(parents: dict, other: dict) -> None:
    """A criterion row has its result's workspace and meeting (Addendum 2, F1).

    ``other`` is a conversation of another workspace.
    """
    key = "fk_scorecard_criterion_result_result"
    result_id = _add(ScorecardResultRecord, _result(parents))
    elsewhere = _parents(parents, key="elsewhere")  # another meeting of the same workspace
    _refused(
        CriterionResultRecord,
        _criterion(parents, result_id, 0, meeting_id=elsewhere["meeting_id"]),
        key,
    )
    _add(CriterionResultRecord, _criterion(parents, result_id, 0, meeting_id=parents["meeting_id"]))

    # Another workspace's result with this workspace's id on the criterion row.
    theirs = _add(ScorecardResultRecord, _result(other))
    _refused(
        CriterionResultRecord,
        _criterion(other, theirs, 0, workspace_id=parents["workspace_id"]),
        key,
    )
    _add(CriterionResultRecord, _criterion(other, theirs, 0, workspace_id=other["workspace_id"]))

    # ``uq_scorecard_result_scope`` is only the target of that key: a second row with the
    # same (id, workspace_id, meeting_id) already breaks the primary key.
    with inspection_session_factory()() as db:
        db.add(ScorecardResultRecord(**_result(parents, id=result_id)))
        with pytest.raises(
            IntegrityError, match=rf"{RESULTS}_pkey|uq_scorecard_result_scope|{RESULTS}\.id\b"
        ):
            db.flush()
        db.rollback()


RULES = (
    _assert_one_result_per_input,
    _assert_result_outcome_rules,
    _assert_result_number_rules,
    _assert_result_text_rules,
    _assert_criterion_unique_rules,
    _assert_criterion_number_and_key_rules,
    _assert_criterion_outcome_rules,
    _assert_criterion_review_rules,
)


@pytest.mark.parametrize("rules", RULES, ids=lambda rules: rules.__name__.removeprefix("_assert_"))
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    parents = _parents(_tenant(client, email_outbox, "rules"))
    rules(parents)


def test_a_criterion_row_has_its_results_workspace_and_meeting(client, email_outbox):
    parents = _parents(_tenant(client, email_outbox, "scope-a"))
    other = _parents(_tenant(client, email_outbox, "scope-b"))
    _assert_criterion_scope_rules(parents, other)


# --- the ORM and the RLS lists ------------------------------------------------------------

RESULT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "scorecard_version_id": 36,
    "transcript_revision_id": 36,
    "scorecards_version": 40,
    "checks_version": 40,
    "definition_digest": 64,
    "input_digest": 64,
    "conversation_type": 20,
    "direction": 12,
    "outcome": 24,
    "reason": 40,
}
CRITERION_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "result_id": 36,
    "meeting_id": 36,
    "criterion_id": 64,
    "outcome": 24,
    "reason": 40,
    "decided_by": 16,
    "check_outcome": 24,
    "check_reason": 40,
}
RESULT_CHECKS = {
    "ck_scorecard_result_outcome": SCORECARD_OUTCOME_CHECK,
    "ck_scorecard_result_reason": SCORECARD_RESULT_REASON_CHECK,
    "ck_scorecard_result_threshold": SCORECARD_RESULT_THRESHOLD_CHECK,
    "ck_scorecard_result_weights": SCORECARD_RESULT_WEIGHTS_CHECK,
    "ck_scorecard_result_complete": SCORECARD_RESULT_COMPLETE_CHECK,
    "ck_scorecard_result_digests": SCORECARD_RESULT_DIGESTS_CHECK,
    "ck_scorecard_result_conversation_type": CONVERSATION_TYPE_CHECK,
    "ck_scorecard_result_direction": CONVERSATION_DIRECTION_CHECK,
}
CRITERION_CHECKS = {
    "ck_scorecard_criterion_result_outcome": SCORECARD_OUTCOME_CHECK,
    "ck_scorecard_criterion_result_position": CRITERION_POSITION_CHECK,
    "ck_scorecard_criterion_result_weight": CRITERION_WEIGHT_CHECK,
    "ck_scorecard_criterion_result_key": CRITERION_KEY_CHECK,
    "ck_scorecard_criterion_result_decided_by": CRITERION_DECIDED_BY_CHECK,
    "ck_scorecard_criterion_result_reason": CRITERION_REASON_CHECK,
    "ck_scorecard_criterion_result_review": CRITERION_REVIEW_CHECK,
    "ck_scorecard_criterion_result_check_outcome": CRITERION_CHECK_OUTCOME_CHECK,
}


def _declared(table) -> dict:
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    return {
        "nullable": {column.name for column in table.columns if column.nullable},
        "foreign_keys": {
            (
                constraint.name,
                tuple(constraint.column_keys),
                tuple(element.target_fullname for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in table.foreign_key_constraints
        },
        "unique": {
            name: [column.name for column in constraint.columns]
            for name, constraint in named.items()
            if name.startswith("uq_")
        },
        "checks": {
            name: str(constraint.sqltext)
            for name, constraint in named.items()
            if name.startswith("ck_")
        },
        "indexes": {
            index.name: ([column.name for column in index.columns], index.unique)
            for index in table.indexes
        },
    }


def _assert_common_columns(table, lengths: dict[str, int]) -> None:
    for name, length in lengths.items():
        assert table.c[name].type.length == length, name
    assert [column.name for column in table.primary_key] == ["id"]
    # SQLAlchemy wraps a callable default and keeps its name.
    assert table.c.id.default.arg.__name__ == new_id.__name__
    assert table.c.created_at.default.arg.__name__ == utcnow.__name__
    assert table.c.created_at.type.timezone
    assert not table.c.created_at.nullable


def test_the_orm_declares_the_result_table():
    table = ScorecardResultRecord.__table__
    assert table.name == RESULTS
    _assert_common_columns(table, RESULT_LENGTHS)
    for name in (
        "pass_threshold",
        "applicable_weight",
        "earned_weight",
        "failed_weight",
        "insufficient_weight",
    ):
        assert isinstance(table.c[name].type, Integer), name
    assert table.c.complete.type.python_type is bool
    for name in ("check_run", "result"):
        assert table.c[name].type.__class__.__name__ == "JSON", name
    declared = _declared(table)
    assert declared["nullable"] == {"direction", "reason"}
    assert declared["foreign_keys"] == {
        (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
        (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
        (None, ("scorecard_version_id",), ("scorecard_versions.id",), "CASCADE"),
        (None, ("transcript_revision_id",), ("transcript_revisions.id",), "CASCADE"),
    }
    assert declared["unique"] == {
        "uq_scorecard_result_input": ["scorecard_version_id", "meeting_id", "input_digest"],
        # The target of the criterion rows' composite foreign key.
        "uq_scorecard_result_scope": ["id", "workspace_id", "meeting_id"],
    }
    assert declared["checks"] == RESULT_CHECKS
    assert declared["indexes"] == {
        "ix_scorecard_results_workspace_id": (["workspace_id"], False),
        "ix_scorecard_results_meeting_id": (["meeting_id"], False),
        "ix_scorecard_results_scorecard_version_id": (["scorecard_version_id"], False),
        "ix_scorecard_results_transcript_revision_id": (["transcript_revision_id"], False),
        "ix_scorecard_result_workspace_meeting": (["workspace_id", "meeting_id"], False),
    }
    assert "immutable" in ScorecardResultRecord.__doc__.lower()
    assert "purge" in ScorecardResultRecord.__doc__
    assert "HOM-56" in ScorecardResultRecord.__doc__


def test_the_orm_declares_the_criterion_table():
    table = CriterionResultRecord.__table__
    assert table.name == CRITERIA
    _assert_common_columns(table, CRITERION_LENGTHS)
    for name in ("position", "weight"):
        assert isinstance(table.c[name].type, Integer), name
    for name in ("auto_fail", "na_allowed", "review_required"):
        assert table.c[name].type.python_type is bool, name
    for name in ("review_reasons", "evidence", "candidate_evidence"):
        assert table.c[name].type.__class__.__name__ == "JSON", name
    declared = _declared(table)
    assert declared["nullable"] == {"reason", "decided_by", "check_outcome", "check_reason"}
    assert declared["foreign_keys"] == {
        (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
        (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
        (
            "fk_scorecard_criterion_result_result",
            ("result_id", "workspace_id", "meeting_id"),
            (
                "scorecard_results.id",
                "scorecard_results.workspace_id",
                "scorecard_results.meeting_id",
            ),
            "CASCADE",
        ),
    }
    assert declared["unique"] == {
        "uq_scorecard_criterion_result_position": ["result_id", "position"],
        "uq_scorecard_criterion_result_criterion": ["result_id", "criterion_id"],
    }
    assert declared["checks"] == CRITERION_CHECKS
    # The position unique constraint also serves the ``result_id`` foreign key.
    assert declared["indexes"] == {
        "ix_scorecard_criterion_results_workspace_id": (["workspace_id"], False),
        "ix_scorecard_criterion_result_workspace_meeting": (["workspace_id", "meeting_id"], False),
    }
    assert "immutable" in CriterionResultRecord.__doc__.lower()
    assert "purge" in CriterionResultRecord.__doc__
    assert "HOM-56" in CriterionResultRecord.__doc__


def test_the_check_strings_use_no_regex():
    assert SCORECARD_OUTCOME_CHECK == (
        "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
    )
    # The scorecard key rule, applied to the criterion id.
    assert CRITERION_KEY_CHECK == SCORECARD_KEY_CHECK.replace("scorecard_key", "criterion_id")
    assert "scorecard_key" not in CRITERION_KEY_CHECK
    for check in (*RESULT_CHECKS.values(), *CRITERION_CHECKS.values()):
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered


INFERRED_WORDS = ("nps", "churn", "probab", "promoter", "sentiment")


def test_no_column_holds_an_inferred_number_or_a_score():
    integers = set()
    for model in (ScorecardResultRecord, CriterionResultRecord):
        table = model.__table__
        names = [column.name for column in table.columns]
        assert [name for name in names if any(w in name.lower() for w in INFERRED_WORDS)] == []
        assert {"score", "score_min", "score_max"} & set(names) == set()
        assert [c.name for c in table.columns if isinstance(c.type, Numeric)] == []
        integers |= {(table.name, c.name) for c in table.columns if isinstance(c.type, Integer)}
    assert integers == {
        (RESULTS, "pass_threshold"),
        (RESULTS, "applicable_weight"),
        (RESULTS, "earned_weight"),
        (RESULTS, "failed_weight"),
        (RESULTS, "insufficient_weight"),
        (CRITERIA, "position"),
        (CRITERIA, "weight"),
    }


def test_both_tables_are_workspace_tables_the_purge_can_delete():
    for table in TABLES:
        assert table in WORKSPACE_TABLES
        # The conversation purge deletes them as the application role (Addendum A2), but
        # the application role never updates them (Addendum 2, F3).
        assert table not in APPEND_ONLY_TABLES
        assert table in NO_UPDATE_TABLES
        assert table not in SHARED_CONTENT_TABLES  # never through a share token
        assert table not in UNPROTECTED_TABLES
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
        # The same policy as the extractions, the other immutable derived content.
        assert POLICIES[table] == POLICIES["analysis_extractions"]
    assert NO_UPDATE_TABLES == (RESULTS, CRITERIA)


def _changed(value: object) -> object:
    """Another value of the same kind; the guard refuses before any SQL runs."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, dict):
        return {**value, "changed": True}
    if isinstance(value, list):
        return [*value, "changed"]
    if isinstance(value, str) or value is None:
        return "changed"
    return value + timedelta(seconds=1)  # created_at


def _stored(client, email_outbox, label: str) -> tuple[dict, dict[type, str]]:
    parents = _parents(_tenant(client, email_outbox, label))
    result_id = _add(ScorecardResultRecord, _result(parents))
    criterion_id = _add(CriterionResultRecord, _criterion(parents, result_id, 0))
    return parents, {ScorecardResultRecord: result_id, CriterionResultRecord: criterion_id}


def _snapshot(model, row_id: str) -> dict[str, object]:
    with inspection_session_factory()() as db:
        row = db.get(model, row_id)
        return {column.key: getattr(row, column.key) for column in model.__table__.columns}


def test_the_orm_refuses_to_change_any_column_of_a_stored_row(client, email_outbox):
    parents, stored = _stored(client, email_outbox, "orm-update")
    factory = inspection_session_factory()
    for model, row_id in stored.items():
        before = _snapshot(model, row_id)
        for column in model.__table__.columns:
            with factory() as db:
                row = db.get(model, row_id)
                setattr(row, column.key, _changed(getattr(row, column.key)))
                with pytest.raises(
                    ScorecardResultImmutableError, match=rf"attempted: {column.key}\)"
                ):
                    db.flush()
                db.rollback()

        # Allow-control: a flush that changes nothing.
        with factory() as db:
            row = db.get(model, row_id)
            row.outcome = row.outcome
            row.reason = row.reason
            db.flush()
            db.commit()
        assert _snapshot(model, row_id) == before

    # Allow-control: a new row of each table is added next to the stored ones.
    result_id = _add(ScorecardResultRecord, _result(parents))
    _add(CriterionResultRecord, _criterion(parents, result_id, 0))
    assert _ids(RESULTS) == {stored[ScorecardResultRecord], result_id}
    assert len(_ids(CRITERIA)) == 2


def test_the_orm_refuses_to_delete_a_stored_row(client, email_outbox):
    _, stored = _stored(client, email_outbox, "orm-delete")
    factory = inspection_session_factory()
    for model, row_id in stored.items():
        with factory() as db:
            db.delete(db.get(model, row_id))
            with pytest.raises(ScorecardResultImmutableError, match="immutable"):
                db.flush()
            db.rollback()
    assert _ids(RESULTS) == {stored[ScorecardResultRecord]}
    assert _ids(CRITERIA) == {stored[CriterionResultRecord]}


# --- the migration (SQLite: the same tables as the ORM) -----------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_scorecard_results")


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    for word in (
        "HOM-56",
        "immutable",
        "purge",
        "score",
        "NPS",
        "churn",
        "probability",
        "Downgrade",
    ):
        assert word in doc, word


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
        # Some other tables have an unnamed unique or check constraint.
        "unique": sorted(
            (unique["name"] or "", tuple(unique["column_names"]))
            for unique in inspector.get_unique_constraints(table)
        ),
        "checks": sorted(
            (check["name"] or "", check["sqltext"])
            for check in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        ),
    }


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

            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert {table: _reflect(connection, table) for table in TABLES} == orm
            assert orm[RESULTS]["checks"] and orm[CRITERIA]["checks"]

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            after = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert after == before
    finally:
        engine.dispose()


# --- PostgreSQL: the schema, the migration, isolation, grants and cascades ------------------


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


def _other_tables() -> dict:
    """Every other table of the schema, so a test can prove it did not change."""
    queries = {
        "tables": (
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname NOT IN (:a, :b)"
        ),
        "columns": (
            "SELECT table_name, column_name, data_type, character_maximum_length, "
            "is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b)"
        ),
        "constraints": (
            "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
            "AND c.conrelid::regclass::text NOT IN (:a, :b)"
        ),
        "indexes": (
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename NOT IN (:a, :b)"
        ),
        "policies": (
            "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' AND tablename NOT IN (:a, :b)"
        ),
        "grants": (
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b)"
        ),
    }
    with inspection_session_factory()() as db:
        return {
            name: sorted(
                tuple(row) for row in db.execute(text(sql), {"a": RESULTS, "b": CRITERIA}).all()
            )
            for name, sql in queries.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


INTEGER = ("integer", None, "NO", None)
BOOLEAN = ("boolean", None, "NO", None)
JSON_COLUMN = ("json", None, "NO", None)
CREATED_AT = ("timestamp with time zone", None, "NO", None)
EXPECTED_COLUMNS = {
    RESULTS: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "meeting_id": _varchar(36),
        "scorecard_version_id": _varchar(36),
        "transcript_revision_id": _varchar(36),
        "scorecards_version": _varchar(40),
        "checks_version": _varchar(40),
        "definition_digest": _varchar(64),
        "input_digest": _varchar(64),
        "conversation_type": _varchar(20),
        "direction": _varchar(12, "YES"),
        "outcome": _varchar(24),
        "reason": _varchar(40, "YES"),
        "complete": BOOLEAN,
        "pass_threshold": INTEGER,
        "applicable_weight": INTEGER,
        "earned_weight": INTEGER,
        "failed_weight": INTEGER,
        "insufficient_weight": INTEGER,
        "check_run": JSON_COLUMN,
        "result": JSON_COLUMN,
        "created_at": CREATED_AT,
    },
    CRITERIA: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "result_id": _varchar(36),
        "meeting_id": _varchar(36),
        "position": INTEGER,
        "criterion_id": _varchar(64),
        "weight": INTEGER,
        "auto_fail": BOOLEAN,
        "na_allowed": BOOLEAN,
        "outcome": _varchar(24),
        "reason": _varchar(40, "YES"),
        "decided_by": _varchar(16, "YES"),
        "review_required": BOOLEAN,
        "review_reasons": JSON_COLUMN,
        "check_outcome": _varchar(24, "YES"),
        "check_reason": _varchar(40, "YES"),
        "evidence": JSON_COLUMN,
        "candidate_evidence": JSON_COLUMN,
        "created_at": CREATED_AT,
    },
}


def _cascade(table: str, column: str, target: str) -> tuple[str, str]:
    return (
        f"{table}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE CASCADE",
    )


EXPECTED_CONSTRAINTS = {
    RESULTS: dict(
        [
            (f"{RESULTS}_pkey", "PRIMARY KEY (id)"),
            _cascade(RESULTS, "workspace_id", "workspaces"),
            _cascade(RESULTS, "meeting_id", "meetings"),
            _cascade(RESULTS, "scorecard_version_id", "scorecard_versions"),
            _cascade(RESULTS, "transcript_revision_id", "transcript_revisions"),
            (
                "uq_scorecard_result_input",
                "UNIQUE (scorecard_version_id, meeting_id, input_digest)",
            ),
            ("uq_scorecard_result_scope", "UNIQUE (id, workspace_id, meeting_id)"),
        ]
    ),
    CRITERIA: dict(
        [
            (f"{CRITERIA}_pkey", "PRIMARY KEY (id)"),
            _cascade(CRITERIA, "workspace_id", "workspaces"),
            (
                "fk_scorecard_criterion_result_result",
                "FOREIGN KEY (result_id, workspace_id, meeting_id) REFERENCES "
                "scorecard_results(id, workspace_id, meeting_id) ON DELETE CASCADE",
            ),
            _cascade(CRITERIA, "meeting_id", "meetings"),
            ("uq_scorecard_criterion_result_position", 'UNIQUE (result_id, "position")'),
            ("uq_scorecard_criterion_result_criterion", "UNIQUE (result_id, criterion_id)"),
        ]
    ),
}
EXPECTED_CHECK_NAMES = {RESULTS: set(RESULT_CHECKS), CRITERIA: set(CRITERION_CHECKS)}


def _index(table: str, name: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_INDEXES = {
    RESULTS: dict(
        [
            _index(RESULTS, f"{RESULTS}_pkey", "id", unique=True),
            _index(
                RESULTS,
                "uq_scorecard_result_input",
                "scorecard_version_id, meeting_id, input_digest",
                unique=True,
            ),
            _index(
                RESULTS, "uq_scorecard_result_scope", "id, workspace_id, meeting_id", unique=True
            ),
            _index(RESULTS, f"ix_{RESULTS}_workspace_id", "workspace_id"),
            _index(RESULTS, f"ix_{RESULTS}_meeting_id", "meeting_id"),
            _index(RESULTS, f"ix_{RESULTS}_scorecard_version_id", "scorecard_version_id"),
            _index(RESULTS, f"ix_{RESULTS}_transcript_revision_id", "transcript_revision_id"),
            _index(RESULTS, "ix_scorecard_result_workspace_meeting", "workspace_id, meeting_id"),
        ]
    ),
    CRITERIA: dict(
        [
            _index(CRITERIA, f"{CRITERIA}_pkey", "id", unique=True),
            _index(
                CRITERIA,
                "uq_scorecard_criterion_result_position",
                'result_id, "position"',
                unique=True,
            ),
            _index(
                CRITERIA,
                "uq_scorecard_criterion_result_criterion",
                "result_id, criterion_id",
                unique=True,
            ),
            _index(CRITERIA, f"ix_{CRITERIA}_workspace_id", "workspace_id"),
            _index(
                CRITERIA,
                "ix_scorecard_criterion_result_workspace_meeting",
                "workspace_id, meeting_id",
            ),
        ]
    ),
}


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    assert shape["columns"] == EXPECTED_COLUMNS[table]
    constraints = dict(shape["constraints"])
    checks = {name for name in constraints if name.startswith("ck_")}
    assert checks == EXPECTED_CHECK_NAMES[table]  # their behaviour is pinned by RULES
    for name in checks:
        constraints.pop(name)
    assert constraints == EXPECTED_CONSTRAINTS[table]
    assert shape["indexes"] == EXPECTED_INDEXES[table]
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    # The workspace-table grants without UPDATE (Addendum 2, F3); DELETE stays for the purge.
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

    parents = _parents(_tenant(client, email_outbox, "migration"))
    other = _parents(_tenant(client, email_outbox, "migration-other"))
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    result_id = _add(ScorecardResultRecord, _result(parents))
    _add(CriterionResultRecord, _criterion(parents, result_id, 0))
    versions = _scalar("SELECT count(*) FROM scorecard_versions")
    meetings = _scalar("SELECT count(*) FROM meetings")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, NEW_REVISION)  # a no-op while this revision is the head
        others = _other_tables()  # after the stamp, which adds alembic_version
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}")
            assert _shape(table) == EMPTY_SHAPE
        assert _other_tables() == others  # every other table as it was
        # Only the results are lost: the scored definition and the conversation stay.
        assert _scalar("SELECT count(*) FROM scorecard_versions") == versions
        assert _scalar("SELECT count(*) FROM meetings") == meetings

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        for table in TABLES:
            assert _shape(table) == orm_shapes[table]  # the migrated schema equals the ORM
            assert _ids(table) == set()  # the downgrade dropped the rows with the tables
        assert _other_tables() == others

        # The migrated schema refuses the same deny cases.
        for rules in RULES:
            rules(parents)
        _assert_criterion_scope_rules(parents, other)
        assert _ids(RESULTS) and _ids(CRITERIA)

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        for table in TABLES:
            assert _shape(table) == orm_shapes[table]
            assert _ids(table) == set()
        assert _other_tables() == others
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()


def _write_pair(db, parents: dict) -> str:
    """Insert one result and one criterion row in the session's own transaction."""
    result = ScorecardResultRecord(**_result(parents))
    db.add(result)
    db.flush()
    db.add(CriterionResultRecord(**_criterion(parents, result.id, 0)))
    db.flush()
    return result.id


def _workspaces(table: str) -> list[str]:
    with inspection_session_factory()() as db:
        return sorted(
            db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
        )


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _parents(_tenant(client, email_outbox, "tenant-a"))
    second = _parents(_tenant(client, email_outbox, "tenant-b"))
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        _write_pair(db, first)
        db.commit()

    # Bound to B: none of A's rows in either table; B writes its own.
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
        second_result = _write_pair(db, second)
        db.commit()

    # Bound to A: only A's rows; an insert for B fails in either table.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]]
    for model, values in (
        (ScorecardResultRecord, _result(second)),
        (CriterionResultRecord, _criterion(second, second_result, 1)),
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

    for table in TABLES:
        assert _workspaces(table) == sorted([first["workspace_id"], second["workspace_id"]])


UPDATES = (
    "UPDATE scorecard_results SET outcome = 'failed', reason = 'auto_fail'",
    "UPDATE scorecard_criterion_results SET outcome = 'failed'",
)


def _privileges(table: str) -> dict[str, bool]:
    return {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


def _assert_no_update(workspace_id: str) -> None:
    """The rows are visible to the bound workspace, so the refusal is the missing grant."""
    for statement in UPDATES:
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            for table in TABLES:
                assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 1  # noqa: S608
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()


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
def test_the_app_role_reads_inserts_and_deletes_its_own_rows_and_never_updates(
    client, email_outbox
):
    """DELETE is the purge path (Addendum A2); UPDATE is revoked (Addendum 2, F3)."""
    _requires_postgres()
    first = _parents(_tenant(client, email_outbox, "delete-a"))
    second = _parents(_tenant(client, email_outbox, "delete-b"))
    expected = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    for table in TABLES:
        assert _privileges(table) == expected

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        _write_pair(db, first)
        db.commit()

    # Bound to B: A's rows are invisible, so a delete removes none of them.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in (CRITERIA, RESULTS):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
            deleted = db.execute(
                text(f"DELETE FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                {"w": first["workspace_id"]},
            )
            assert deleted.rowcount == 0
        db.commit()
    for table in TABLES:
        assert _workspaces(table) == [first["workspace_id"]]

    # Bound to A: no UPDATE of either table, and still none after the policies and the
    # grants are applied again (the call is idempotent).
    _assert_no_update(first["workspace_id"])
    _apply_as_owner()
    for table in TABLES:
        assert _privileges(table) == expected
    _assert_no_update(first["workspace_id"])

    # Allow-control, bound to A: it reads its rows, and the purge's own statements
    # delete them.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 1  # noqa: S608
        meeting = db.get(Meeting, first["meeting_id"])
        _remove_content(db, workspace_id=first["workspace_id"], meeting=meeting)
        db.commit()
    for table in TABLES:
        assert _ids(table) == set()


@pytest.mark.postgres
def test_deleting_a_workspace_meeting_or_version_still_removes_the_rows(client, email_outbox):
    _requires_postgres()
    first = _parents(_tenant(client, email_outbox, "cascade-a"))
    second = _parents(_tenant(client, email_outbox, "cascade-b"))
    by_meeting = _parents(second, key="by_meeting")
    by_version = _parents(second, key="by_version")
    kept = _parents(second, key="kept")
    with inspection_session_factory()() as db:
        for parents in (first, by_meeting, by_version, kept):
            _write_pair(db, parents)
        db.commit()

    def results_of(parents: dict) -> int:
        return _scalar(
            "SELECT count(*) FROM scorecard_results WHERE meeting_id = :m",
            m=parents["meeting_id"],
        )

    def criteria_of(workspace_id: str) -> int:
        return _scalar(
            "SELECT count(*) FROM scorecard_criterion_results WHERE workspace_id = :w",
            w=workspace_id,
        )

    # Removals by the owner (DBA here): the FK action runs as the table owner, whatever
    # the application role may do on the two tables itself.
    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.execute(
            delete(ScorecardVersion).where(
                ScorecardVersion.id == by_version["scorecard_version_id"]
            )
        )
        db.commit()
    assert _workspaces(RESULTS) == [second["workspace_id"]]
    assert criteria_of(first["workspace_id"]) == 0
    assert (results_of(by_meeting), results_of(by_version), results_of(kept)) == (0, 0, 1)
    assert criteria_of(second["workspace_id"]) == 1  # only the kept result's criterion


def test_deleting_a_transcript_revision_removes_the_results_scored_from_it(client, email_outbox):
    """Every result names its revision, so a removed revision takes its results along."""
    tenant = _tenant(client, email_outbox, "revision")
    scored = _parents(tenant, key="scored")
    kept = _parents(tenant, key="kept")
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        _write_pair(db, scored)
        stays = _write_pair(db, kept)
        db.commit()
    assert len(_ids(RESULTS)) == 2 and len(_ids(CRITERIA)) == 2

    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        db.execute(
            delete(TranscriptRevision).where(
                TranscriptRevision.id == scored["transcript_revision_id"]
            )
        )
        db.commit()
    assert _ids(RESULTS) == {stays}
    assert len(_ids(CRITERIA)) == 1


# --- the conversation purge and the backup reconciliation (Addendum A5) ------------------


def _score(tenant: dict, meeting_id: str, key: str) -> None:
    """Store one result and one criterion row for a processed conversation."""
    parents = _parents(tenant, key=key, meeting_id=meeting_id)
    with inspection_session_factory()() as db:
        _write_pair(db, parents)
        db.commit()


def _rows_of(meeting_id: str) -> tuple[int, int]:
    return tuple(
        _scalar(f"SELECT count(*) FROM {table} WHERE meeting_id = :m", m=meeting_id)  # noqa: S608
        for table in TABLES
    )


def _delete_conversation(client, tenant: dict, meeting_id: str) -> None:
    """The deletion route: the tombstone, then the first purge attempt in the request."""
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204, deleted.text


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "scorecard-purge")
    scored, kept, unscored = [
        _processed(client, tenant, settings, key=f"scorecard-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    _score(tenant, scored, "purged")
    _score(tenant, kept, "kept")
    assert _rows_of(scored) == (1, 1)

    for meeting_id in (scored, unscored):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    assert _rows_of(scored) == (0, 0)
    assert _rows_of(kept) == (1, 1)  # another conversation of the workspace keeps its rows
    # The purge deletes the two rows itself (they are counted), not only through the
    # cascade of the transcript revision.
    assert _purge(scored).content_rows_removed == _purge(unscored).content_rows_removed + 2


def test_a_held_conversation_keeps_its_rows_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "scorecard-hold")
    meeting_id = _processed(client, tenant, settings, key="scorecard-hold-0001")
    _score(tenant, meeting_id, "held")
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-7", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    # The deletion of a held conversation: the purge waits and the rows stay.
    _delete_conversation(client, tenant, meeting_id)
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == ERROR_LEGAL_HOLD
    assert _rows_of(meeting_id) == (1, 1)
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    assert _rows_of(meeting_id) == (1, 1)

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
    assert _rows_of(meeting_id) == (0, 0)


def test_the_backup_reconciliation_counts_both_tables():
    assert RESULTS in RECONCILED_TABLES and CRITERIA in RECONCILED_TABLES
    # The reconciliation counts a purged conversation's rows by meeting and workspace.
    for table in TABLES:
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns
