"""HOM-73 PR 1: the supervisor review queue table and its one migration.

``review_items`` holds one unit of human review work: one conversation (a ``meetings``
row) a supervisor must review, with an optional assignee and a due time.  Nothing writes
the table yet; PR 2 adds the service and the routes.  The tests check:

* the ORM declares the columns, the named constraints, the partial unique index and the
  indexes of the design (section 2), the frozen lists, and the check strings, which use
  no regex and never name a user column;
* ``database/rls.py`` lists the table as a workspace table, and in none of the
  append-only, no-delete and no-update lists;
* every deny case of the SPEC is refused by its named constraint, and the closest allow
  case passes (SQLite and PostgreSQL): status, source, cancel reason, revision, the four
  per-status field rules, one active item per conversation, and the composite foreign
  key to a scorecard result of the same conversation;
* deleting a conversation, a workspace or a scorecard result removes the items, and
  deleting a user sets the three user columns to NULL;
* on SQLite, the migration builds the same table as the ORM, and its downgrade removes
  it and nothing else;
* on PostgreSQL: the ORM schema (``create_all``) and the migrated schema are the same;
  upgrade from ``5067ea773c0b`` creates the table, its policy and its grants, and
  downgrade removes them and leaves every other table as it was; tenant isolation (also
  on write); the application role may SELECT, INSERT, UPDATE and DELETE its own rows and
  none of another workspace's;
* the conversation purge deletes the items of the purged conversation only, before its
  scorecard results, and counts them; a legal hold keeps them until its release; the
  backup reconciliation counts the table; the code has one migration head, this one.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Integer, create_engine, delete, event, inspect, text
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
    REVIEW_CANCEL_REASONS,
    REVIEW_ITEM_ACTIVE_STATUSES,
    REVIEW_ITEM_ACTIVE_WHERE,
    REVIEW_ITEM_CANCEL_REASON_CHECK,
    REVIEW_ITEM_CANCELLED_FIELDS_CHECK,
    REVIEW_ITEM_COMPLETED_FIELDS_CHECK,
    REVIEW_ITEM_IN_REVIEW_FIELDS_CHECK,
    REVIEW_ITEM_OPEN_FIELDS_CHECK,
    REVIEW_ITEM_REVISION_CHECK,
    REVIEW_ITEM_SOURCE_CHECK,
    REVIEW_ITEM_SOURCES,
    REVIEW_ITEM_STATUS_CHECK,
    REVIEW_ITEM_STATUSES,
    Meeting,
    PurgeState,
    ReviewItem,
    ScorecardResultRecord,
    User,
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
from backend.tests.test_scorecard_results_migration import (
    _delete_conversation,
    _parents,
    _result,
)

NEW_REVISION = "484a6df74abf"
BASE_REVISION = "5067ea773c0b"  # HOM-56: scorecard results
TABLE = "review_items"
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
STARTED = NOW + timedelta(hours=1)
FINISHED = NOW + timedelta(hours=2)
# The fields each status needs; every other timestamp and the reason stay NULL.
STATUS_FIELDS: dict[str, dict[str, object]] = {
    "open": {},
    "in_review": {"started_at": STARTED},
    "completed": {"started_at": STARTED, "completed_at": FINISHED},
    "cancelled": {"cancelled_at": FINISHED, "cancel_reason": "duplicate"},
}
# The check strings of DESIGN section 2, character for character.
CHECKS = {
    "ck_review_items_status": "status IN ('open','in_review','completed','cancelled')",
    "ck_review_items_source": "source IN ('manual','sample')",
    "ck_review_items_cancel_reason": (
        "cancel_reason IS NULL OR "
        "cancel_reason IN ('duplicate','out_of_scope','meeting_unusable','other')"
    ),
    "ck_review_items_revision": "revision >= 1",
    "ck_review_items_open_fields": (
        "status <> 'open' OR (started_at IS NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    ),
    "ck_review_items_in_review_fields": (
        "status <> 'in_review' OR (started_at IS NOT NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    ),
    "ck_review_items_completed_fields": (
        "status <> 'completed' OR (started_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    ),
    "ck_review_items_cancelled_fields": (
        "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
        "AND completed_at IS NULL)"
    ),
}
ACTIVE_WHERE = "status IN ('open','in_review')"
USER_COLUMNS = ("assignee_user_id", "assigned_by_user_id", "created_by_user_id")


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"review-item-{label}@example.com")
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


def _scored(tenant: dict, key: str) -> tuple[dict, str]:
    """A new conversation with one stored scorecard result; returns it and the result id."""
    parents = _parents(tenant, key=key)
    return parents, _add(ScorecardResultRecord, _result(parents))


def _item(parents: dict, **extra: object) -> dict[str, object]:
    """A valid ``open`` manual item on the parents' conversation."""
    return {
        "workspace_id": parents["workspace_id"],
        "meeting_id": parents["meeting_id"],
        "source": "manual",
        "source_details": {},
        "status": "open",
        "due_at": NOW + timedelta(days=2),
        "created_by_user_id": parents["user_id"],
        "created_at": NOW,
        "updated_at": NOW,
        "revision": 1,
        **extra,
    }


def _status(state: str, **extra: object) -> dict[str, object]:
    """The fields of an item in ``state``, with ``extra`` on top."""
    return {"status": state, **STATUS_FIELDS[state], **extra}


def _utc(value: datetime) -> datetime:
    """SQLite returns a naive datetime for a ``DateTime(timezone=True)`` column."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _add(model, values: dict[str, object]) -> str:
    """Insert one row through the ORM as the test login role and commit it."""
    with inspection_session_factory()() as db:
        row = model(**values)
        db.add(row)
        db.commit()
        return row.id


def _set(item_id: str, **values: object) -> None:
    with inspection_session_factory()() as db:
        item = db.get(ReviewItem, item_id)
        for name, value in values.items():
            setattr(item, name, value)
        db.commit()


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        # SQLite does not name a broken foreign key.  Each deny names a workspace, a
        # meeting and users that exist, so only the composite key can refuse it.
        return "FOREIGN KEY constraint failed"
    if constraint == "uq_review_items_active_meeting" and not _on_postgres():
        # SQLite names the columns of a broken unique index, not the index.
        return re.escape(f"{TABLE}.workspace_id, {TABLE}.meeting_id")
    return rf"\b{constraint}\b"


def _refused(values: dict[str, object], constraint: str) -> None:
    """The insert fails on ``constraint`` and on nothing else."""
    with inspection_session_factory()() as db:
        db.add(ReviewItem(**values))
        with pytest.raises(IntegrityError, match=_names(constraint)):
            db.flush()
        db.rollback()


def _refused_alone(tenant: dict, constraint: str, **extra: object) -> None:
    """``_refused`` on a new conversation, so no other item of it can interfere."""
    _refused(_item(_conversation(tenant), **extra), constraint)


def _allowed(tenant: dict, **extra: object) -> str:
    """An item on a new conversation is stored; returns its id."""
    return _add(ReviewItem, _item(_conversation(tenant), **extra))


def _ids(**where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {TABLE} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


# --- the deny and allow cases (SPEC "Deny-cases and their allow-controls") ------------------


def _assert_status_rules(tenant: dict) -> None:
    _refused_alone(tenant, "ck_review_items_status", **_status("completed", status="done"))
    _allowed(tenant, **_status("completed"))
    for status in REVIEW_ITEM_STATUSES:
        _allowed(tenant, **_status(status))
    # The ORM defaults: ``open`` and revision 1.
    values = _item(_conversation(tenant))
    del values["status"], values["revision"]
    stored = _add(ReviewItem, values)
    with inspection_session_factory()() as db:
        item = db.get(ReviewItem, stored)
        assert (item.status, item.revision) == ("open", 1)


def _assert_source_rules(tenant: dict) -> None:
    _refused_alone(tenant, "ck_review_items_source", source="auto")
    details = {"sampler_version": "review-sampling/1", "seed": "s1", "reasons": ["random"]}
    _allowed(tenant, source="sample", source_details=details)
    _allowed(tenant, source="manual", source_details={})


def _assert_cancel_reason_rules(tenant: dict) -> None:
    _refused_alone(
        tenant, "ck_review_items_cancel_reason", **_status("cancelled", cancel_reason="spam")
    )
    for reason in REVIEW_CANCEL_REASONS:
        _allowed(tenant, **_status("cancelled", cancel_reason=reason))
    # A reason on an open item breaks the open rule, not the list.
    _refused_alone(tenant, "ck_review_items_open_fields", cancel_reason="duplicate")
    _allowed(tenant, cancel_reason=None)


def _assert_revision_rules(tenant: dict) -> None:
    for revision in (0, -1):
        _refused_alone(tenant, "ck_review_items_revision", revision=revision)
    for revision in (1, 2):
        _allowed(tenant, revision=revision)


def _assert_open_field_rules(tenant: dict) -> None:
    key = "ck_review_items_open_fields"
    for name, value in (
        ("started_at", STARTED),
        ("completed_at", FINISHED),
        ("cancelled_at", FINISHED),
        ("cancel_reason", "other"),
    ):
        _refused_alone(tenant, key, **{name: value})
    _allowed(tenant, **_status("open"))


def _assert_in_review_field_rules(tenant: dict) -> None:
    key = "ck_review_items_in_review_fields"
    _refused_alone(tenant, key, **_status("in_review", started_at=None))
    for name, value in (
        ("completed_at", FINISHED),
        ("cancelled_at", FINISHED),
        ("cancel_reason", "other"),
    ):
        _refused_alone(tenant, key, **_status("in_review", **{name: value}))
    _allowed(tenant, **_status("in_review"))


def _assert_completed_field_rules(tenant: dict) -> None:
    key = "ck_review_items_completed_fields"
    _refused_alone(tenant, key, **_status("completed", completed_at=None))
    _refused_alone(tenant, key, **_status("completed", started_at=None))
    _refused_alone(tenant, key, **_status("completed", cancelled_at=FINISHED))
    _refused_alone(tenant, key, **_status("completed", cancel_reason="other"))
    _allowed(tenant, **_status("completed"))


def _assert_cancelled_field_rules(tenant: dict) -> None:
    key = "ck_review_items_cancelled_fields"
    _refused_alone(tenant, key, **_status("cancelled", cancel_reason=None))
    _refused_alone(tenant, key, **_status("cancelled", cancelled_at=None))
    _refused_alone(tenant, key, **_status("cancelled", completed_at=FINISHED))
    _allowed(tenant, **_status("cancelled"))
    # An item cancelled during its review keeps its start time.
    _allowed(tenant, **_status("cancelled", started_at=STARTED))


def _assert_one_active_item_per_conversation(tenant: dict) -> None:
    key = "uq_review_items_active_meeting"
    conversation = _conversation(tenant)
    first = _add(ReviewItem, _item(conversation))
    _refused(_item(conversation), key)
    _refused(_item(conversation, **_status("in_review")), key)
    # A finished item does not count: it sits next to the active one.
    _add(ReviewItem, _item(conversation, **_status("completed")))
    _add(ReviewItem, _item(conversation, **_status("cancelled")))

    # Once the first is completed, a new item is accepted; once that one is cancelled,
    # another one.
    _set(first, **_status("completed"))
    second = _add(ReviewItem, _item(conversation))
    _set(second, **_status("cancelled"))
    _add(ReviewItem, _item(conversation, **_status("in_review")))
    assert len(_ids(meeting_id=conversation["meeting_id"])) == 5

    # Items of two different conversations never collide.
    other = _conversation(tenant)
    _add(ReviewItem, _item(other))
    _refused(_item(other, **_status("in_review")), key)


RULES = (
    _assert_status_rules,
    _assert_source_rules,
    _assert_cancel_reason_rules,
    _assert_revision_rules,
    _assert_open_field_rules,
    _assert_in_review_field_rules,
    _assert_completed_field_rules,
    _assert_cancelled_field_rules,
    _assert_one_active_item_per_conversation,
)


def _assert_scorecard_result_scope(tenant: dict, other: dict) -> None:
    """An item names only a scorecard result of its own conversation and workspace.

    ``other`` is another workspace.
    """
    key = "fk_review_items_scorecard_result"
    x, result_x = _scored(tenant, "scope_x")
    y, result_y = _scored(tenant, "scope_y")
    _refused(_item(y, scorecard_result_id=result_x), key)  # another conversation's result
    _add(ReviewItem, _item(y, scorecard_result_id=result_y))  # its own result
    _add(ReviewItem, _item(x, scorecard_result_id=None))  # no result: not checked
    _refused(_item(_conversation(tenant), scorecard_result_id=new_id()), key)  # no such result

    z, result_z = _scored(other, "scope_z")
    # Another workspace's result on this workspace's conversation.
    _refused(_item(_conversation(tenant), scorecard_result_id=result_z), key)
    # The other workspace's conversation and result under this workspace's id.
    _refused(
        _item({**z, "workspace_id": tenant["workspace_id"]}, scorecard_result_id=result_z), key
    )
    _add(ReviewItem, _item(z, scorecard_result_id=result_z))


@pytest.mark.parametrize("rules", RULES, ids=lambda rules: rules.__name__.removeprefix("_assert_"))
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


def test_an_item_names_only_a_scorecard_result_of_its_conversation(client, email_outbox):
    _assert_scorecard_result_scope(
        _tenant(client, email_outbox, "scope-a"), _tenant(client, email_outbox, "scope-b")
    )


# --- the ORM, the frozen lists and the RLS lists ------------------------------------------

LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "scorecard_result_id": 36,
    "source": 16,
    "status": 16,
    "assignee_user_id": 36,
    "assigned_by_user_id": 36,
    "created_by_user_id": 36,
    "cancel_reason": 32,
}
TIMESTAMPS = (
    "assigned_at",
    "due_at",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "cancelled_at",
)
NULLABLE = {
    "scorecard_result_id",
    "assignee_user_id",
    "assigned_by_user_id",
    "assigned_at",
    "created_by_user_id",
    "started_at",
    "completed_at",
    "cancelled_at",
    "cancel_reason",
}
FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("assignee_user_id",), ("users.id",), "SET NULL"),
    (None, ("assigned_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("created_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_review_items_scorecard_result",
        ("scorecard_result_id", "workspace_id", "meeting_id"),
        ("scorecard_results.id", "scorecard_results.workspace_id", "scorecard_results.meeting_id"),
        "CASCADE",
    ),
}
INDEXES = {
    "ix_review_items_workspace_id": (["workspace_id"], False),
    "ix_review_items_meeting_id": (["meeting_id"], False),
    "ix_review_items_assignee_user_id": (["assignee_user_id"], False),
    "ix_review_items_scorecard_result_id": (["scorecard_result_id"], False),
    "ix_review_items_queue": (["workspace_id", "status", "due_at", "id"], False),
    "uq_review_items_active_meeting": (["workspace_id", "meeting_id"], True),
}


def test_the_orm_declares_the_table():
    table = ReviewItem.__table__
    assert table.name == TABLE
    assert [column.name for column in table.columns] == [
        "id",
        "workspace_id",
        "meeting_id",
        "scorecard_result_id",
        "source",
        "source_details",
        "status",
        "assignee_user_id",
        "assigned_by_user_id",
        "assigned_at",
        "due_at",
        "created_by_user_id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "cancelled_at",
        "cancel_reason",
        "revision",
    ]
    for name, length in LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in TIMESTAMPS:
        assert table.c[name].type.timezone, name
    assert isinstance(table.c.revision.type, Integer)
    assert table.c.source_details.type.__class__.__name__ == "JSON"
    assert {column.name for column in table.columns if column.nullable} == NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]

    # The ORM defaults (SQLAlchemy wraps a callable default and keeps its name).
    assert table.c.id.default.arg.__name__ == new_id.__name__
    assert table.c.status.default.arg == "open"
    assert table.c.revision.default.arg == 1
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
    } == FOREIGN_KEYS
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    assert {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")} == CHECKS
    assert [name for name in named if name.startswith("uq_")] == []
    assert {
        index.name: ([column.name for column in index.columns], index.unique)
        for index in table.indexes
    } == INDEXES
    (active,) = (index for index in table.indexes if index.name == "uq_review_items_active_meeting")
    for dialect in ("sqlite", "postgresql"):
        assert str(active.dialect_options[dialect]["where"]) == ACTIVE_WHERE, dialect
    assert "HOM-73" in ReviewItem.__doc__
    assert "purge" in ReviewItem.__doc__


def test_the_frozen_lists_and_the_check_strings():
    assert REVIEW_ITEM_SOURCES == ("manual", "sample")
    assert REVIEW_ITEM_STATUSES == ("open", "in_review", "completed", "cancelled")
    assert REVIEW_ITEM_ACTIVE_STATUSES == ("open", "in_review")
    assert REVIEW_CANCEL_REASONS == ("duplicate", "out_of_scope", "meeting_unusable", "other")
    assert {
        "ck_review_items_status": REVIEW_ITEM_STATUS_CHECK,
        "ck_review_items_source": REVIEW_ITEM_SOURCE_CHECK,
        "ck_review_items_cancel_reason": REVIEW_ITEM_CANCEL_REASON_CHECK,
        "ck_review_items_revision": REVIEW_ITEM_REVISION_CHECK,
        "ck_review_items_open_fields": REVIEW_ITEM_OPEN_FIELDS_CHECK,
        "ck_review_items_in_review_fields": REVIEW_ITEM_IN_REVIEW_FIELDS_CHECK,
        "ck_review_items_completed_fields": REVIEW_ITEM_COMPLETED_FIELDS_CHECK,
        "ck_review_items_cancelled_fields": REVIEW_ITEM_CANCELLED_FIELDS_CHECK,
    } == CHECKS
    assert REVIEW_ITEM_ACTIVE_WHERE == ACTIVE_WHERE
    for check in (*CHECKS.values(), ACTIVE_WHERE):
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        # The user columns are SET NULL on a user's removal; a check on them would make
        # that removal fail.
        assert [column for column in USER_COLUMNS if column in check] == []


def test_the_table_is_a_workspace_table_the_service_and_the_purge_can_change():
    assert TABLE in WORKSPACE_TABLES
    # The service updates the rows and the conversation purge deletes them.
    assert TABLE not in APPEND_ONLY_TABLES
    assert TABLE not in NO_DELETE_TABLES
    assert TABLE not in NO_UPDATE_TABLES
    assert TABLE not in SHARED_CONTENT_TABLES  # never through a share token
    assert TABLE not in UNPROTECTED_TABLES
    assert POLICIES[TABLE] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")


# --- cascades and user removal ----------------------------------------------------------


def test_deleting_a_conversation_a_workspace_or_a_result_removes_the_items(client, email_outbox):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    in_first = _conversation(first)
    gone = _conversation(second)
    kept = _conversation(second)
    scored, result_id = _scored(second, "cascade")
    _add(ReviewItem, _item(in_first))
    _add(ReviewItem, _item(gone))
    _add(ReviewItem, _item(gone, **_status("completed")))
    stays = _add(ReviewItem, _item(kept))
    _add(ReviewItem, _item(scored, scorecard_result_id=result_id))
    unscored = _add(ReviewItem, _item(scored, **_status("completed")))

    # Removals by the owner (DBA here): the FK action runs as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(Meeting).where(Meeting.id == gone["meeting_id"]))
        db.execute(delete(ScorecardResultRecord).where(ScorecardResultRecord.id == result_id))
        db.commit()
    assert _ids(meeting_id=gone["meeting_id"]) == set()
    # Only the item that named the removed result goes with it.
    assert _ids(meeting_id=scored["meeting_id"]) == {unscored}
    assert len(_ids(workspace_id=first["workspace_id"])) == 1

    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    assert _ids(workspace_id=first["workspace_id"]) == set()
    assert _ids(workspace_id=second["workspace_id"]) == {stays, unscored}


def test_deleting_a_user_clears_the_user_columns_and_keeps_the_item(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user-removal")
    with inspection_session_factory()() as db:
        user = User(email="review-item-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    named = {column: user_id for column in USER_COLUMNS}
    item_id = _add(
        ReviewItem, _item(_conversation(tenant), **_status("in_review"), assigned_at=NOW, **named)
    )
    kept_id = _add(ReviewItem, _item(_conversation(tenant), assignee_user_id=tenant["user_id"]))

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        item = db.get(ReviewItem, item_id)
        assert [getattr(item, column) for column in USER_COLUMNS] == [None, None, None]
        # Everything else stays, the state rules included.
        assert item.status == "in_review"
        assert (_utc(item.started_at), _utc(item.assigned_at)) == (STARTED, NOW)
        assert db.get(ReviewItem, kept_id).assignee_user_id == tenant["user_id"]


# --- the migration (SQLite: the same table as the ORM) ----------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_review_items")


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    for words in ("schema only (HOM-73, WP14-T02)", "Why", "No data changes", "Downgrade"):
        assert words in doc, words
    # Frozen copies of the model's check strings and of the active-item condition.
    assert module.CHECKS == CHECKS
    assert str(module.ACTIVE_WHERE) == ACTIVE_WHERE


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == NEW_REVISION


def _reflect(connection) -> dict:
    inspector = inspect(connection)
    index_sql = connection.execute(
        text("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = :t"),
        {"t": TABLE},
    ).all()
    return {
        "columns": [
            (column["name"], str(column["type"]), column["nullable"])
            for column in inspector.get_columns(TABLE)
        ],
        "primary_key": inspector.get_pk_constraint(TABLE)["constrained_columns"],
        "foreign_keys": sorted(
            (
                key["name"] or "",
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
                key["options"].get("ondelete"),
            )
            for key in inspector.get_foreign_keys(TABLE)
        ),
        "unique": inspector.get_unique_constraints(TABLE),
        "checks": sorted(
            (check["name"] or "", check["sqltext"])
            for check in inspector.get_check_constraints(TABLE)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(TABLE)
        ),
        # The partial index's WHERE clause is only in the stored SQL.
        "index_sql": sorted((name, sql) for name, sql in index_sql if sql),
    }


def _schema_sql(connection) -> list[tuple]:
    """Every stored table and index definition, the review items' own excluded."""
    return connection.execute(
        text(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE tbl_name <> :t ORDER BY type, name"
        ),
        {"t": TABLE},
    ).all()


def test_on_sqlite_the_migration_builds_the_orm_table_and_drops_only_it():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = _reflect(connection)
            Base.metadata.tables[TABLE].drop(connection)
            before = _schema_sql(connection)

            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert _reflect(connection) == orm
            assert sorted(name for name, _ in orm["checks"]) == sorted(CHECKS)
            (partial,) = [sql for name, sql in orm["index_sql"] if name.startswith("uq_")]
            assert partial.endswith(f"WHERE {ACTIVE_WHERE}")

            assert _schema_sql(connection) == before  # the upgrade changed no other table

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            assert TABLE not in inspect(connection).get_table_names()
            assert _schema_sql(connection) == before
            remaining = connection.execute(
                text("SELECT name FROM sqlite_master WHERE tbl_name = :t OR name LIKE :p"),
                {"t": TABLE, "p": "%review_items%"},
            ).all()
            assert remaining == []
    finally:
        engine.dispose()


# --- PostgreSQL: the schema, the migration, isolation and grants ------------------------


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _shape() -> dict:
    """The table as the catalog shows it: columns, constraints, indexes, policy, grants."""
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
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"), {"t": TABLE}
        ).all()
        policies = db.execute(
            text(
                "SELECT policyname, permissive, roles::text, cmd, qual, with_check "
                "FROM pg_policies WHERE tablename = :t"
            ),
            {"t": TABLE},
        ).all()
        flags = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{TABLE}"},
        ).all()
        grants = db.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t AND grantee = :r"
            ),
            {"t": TABLE, "r": APP_ROLE},
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
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> :t"
        ),
        "columns": (
            "SELECT table_name, column_name, data_type, character_maximum_length, "
            "is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> :t"
        ),
        "constraints": (
            "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
            "AND c.conrelid::regclass::text <> :t"
        ),
        "indexes": (
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename <> :t"
        ),
        "policies": (
            "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' AND tablename <> :t"
        ),
        "grants": (
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND table_name <> :t"
        ),
    }
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), {"t": TABLE}).all())
            for name, sql in queries.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return ("timestamp with time zone", None, nullable, None)


EXPECTED_COLUMNS = {
    "id": _varchar(36),
    "workspace_id": _varchar(36),
    "meeting_id": _varchar(36),
    "scorecard_result_id": _varchar(36, "YES"),
    "source": _varchar(16),
    "source_details": ("json", None, "NO", None),
    "status": _varchar(16),
    "assignee_user_id": _varchar(36, "YES"),
    "assigned_by_user_id": _varchar(36, "YES"),
    "assigned_at": _timestamp("YES"),
    "due_at": _timestamp(),
    "created_by_user_id": _varchar(36, "YES"),
    "created_at": _timestamp(),
    "updated_at": _timestamp(),
    "started_at": _timestamp("YES"),
    "completed_at": _timestamp("YES"),
    "cancelled_at": _timestamp("YES"),
    "cancel_reason": _varchar(32, "YES"),
    "revision": ("integer", None, "NO", None),
}


def _foreign_key(column: str, target: str, action: str) -> tuple[str, str]:
    return (
        f"{TABLE}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}",
    )


EXPECTED_CONSTRAINTS = dict(
    [
        (f"{TABLE}_pkey", "PRIMARY KEY (id)"),
        _foreign_key("workspace_id", "workspaces", "CASCADE"),
        _foreign_key("meeting_id", "meetings", "CASCADE"),
        _foreign_key("assignee_user_id", "users", "SET NULL"),
        _foreign_key("assigned_by_user_id", "users", "SET NULL"),
        _foreign_key("created_by_user_id", "users", "SET NULL"),
        (
            "fk_review_items_scorecard_result",
            "FOREIGN KEY (scorecard_result_id, workspace_id, meeting_id) REFERENCES "
            "scorecard_results(id, workspace_id, meeting_id) ON DELETE CASCADE",
        ),
    ]
)


def _index(name: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{TABLE} USING btree ({columns})"


EXPECTED_INDEXES = dict(
    [
        _index(f"{TABLE}_pkey", "id", unique=True),
        _index(f"ix_{TABLE}_workspace_id", "workspace_id"),
        _index(f"ix_{TABLE}_meeting_id", "meeting_id"),
        _index(f"ix_{TABLE}_assignee_user_id", "assignee_user_id"),
        _index(f"ix_{TABLE}_scorecard_result_id", "scorecard_result_id"),
        _index("ix_review_items_queue", "workspace_id, status, due_at, id"),
    ]
)


def _assert_head_shape() -> dict:
    shape = _shape()
    assert shape["columns"] == EXPECTED_COLUMNS
    constraints = dict(shape["constraints"])
    checks = {name for name in constraints if name.startswith("ck_")}
    assert checks == set(CHECKS)  # their behaviour is pinned by RULES
    for name in checks:
        constraints.pop(name)
    assert constraints == EXPECTED_CONSTRAINTS
    indexes = dict(shape["indexes"])
    # The partial unique index: PostgreSQL rewrites the IN list, so only its parts are pinned.
    partial = indexes.pop("uq_review_items_active_meeting")
    head, where = partial.split(" WHERE ")
    assert (
        head == _index("uq_review_items_active_meeting", "workspace_id, meeting_id", unique=True)[1]
    )
    assert "'open'" in where and "'in_review'" in where
    assert "'completed'" not in where and "'cancelled'" not in where
    assert indexes == EXPECTED_INDEXES
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    # The workspace-table grants: the service updates rows, the purge deletes them.
    assert shape["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"]
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_table(client):
    _requires_postgres()
    _assert_head_shape()


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_table(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    other = _tenant(client, email_outbox, "migration-other")
    orm_shape = _assert_head_shape()
    parents, result_id = _scored(tenant, "migration")
    _add(ReviewItem, _item(parents, scorecard_result_id=result_id))
    results = _scalar("SELECT count(*) FROM scorecard_results")
    meetings = _scalar("SELECT count(*) FROM meetings")

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, NEW_REVISION)  # a no-op while this revision is the head
        others = _other_tables()  # after the stamp, which adds alembic_version
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{TABLE}")
        assert _shape() == EMPTY_SHAPE
        assert _other_tables() == others  # every other table as it was
        # Only the items are lost: the conversations and their results stay.
        assert _scalar("SELECT count(*) FROM scorecard_results") == results
        assert _scalar("SELECT count(*) FROM meetings") == meetings

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert _shape() == orm_shape  # the migrated schema equals the ORM
        assert _ids() == set()  # the downgrade dropped the rows with the table
        assert _other_tables() == others

        # The migrated schema refuses the same deny cases.
        for rules in RULES:
            rules(tenant)
        _assert_scorecard_result_scope(tenant, other)
        assert _ids()

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert _shape() == orm_shape
        assert _ids() == set()
        assert _other_tables() == others
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()


def _write(db, parents: dict, **extra: object) -> str:
    """Insert one item in the session's own transaction."""
    item = ReviewItem(**_item(parents, **extra))
    db.add(item)
    db.flush()
    return item.id


def _workspaces() -> list[str]:
    with inspection_session_factory()() as db:
        return sorted(db.execute(text(f"SELECT workspace_id FROM {TABLE}")).scalars())  # noqa: S608


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _conversation(_tenant(client, email_outbox, "tenant-a"))
    second = _conversation(_tenant(client, email_outbox, "tenant-b"))
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        _write(db, first)
        db.commit()

    # Bound to B: none of A's items; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        assert db.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0  # noqa: S608
        assert (
            db.scalar(
                text(f"SELECT count(*) FROM {TABLE} WHERE workspace_id = :w"),  # noqa: S608
                {"w": first["workspace_id"]},
            )
            == 0
        )
        _write(db, second)
        db.commit()

    # Bound to A: only A's item; an insert with B's workspace id fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        visible = db.execute(text(f"SELECT workspace_id FROM {TABLE}")).scalars()  # noqa: S608
        assert list(visible) == [first["workspace_id"]]
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.add(ReviewItem(**_item(second, **_status("completed"))))
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()
    # Allow-control: the same insert with A's own workspace and conversation.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        _write(db, first, **_status("completed"))
        db.commit()

    # No workspace bound: nothing.
    with factory() as db:
        assert db.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0  # noqa: S608

    assert _workspaces() == sorted([first["workspace_id"]] * 2 + [second["workspace_id"]])


def _privileges() -> dict[str, bool]:
    return {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=TABLE, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


@pytest.mark.postgres
def test_the_app_role_reads_inserts_updates_and_deletes_its_own_rows_only(client, email_outbox):
    _requires_postgres()
    first = _conversation(_tenant(client, email_outbox, "grants-a"))
    second = _conversation(_tenant(client, email_outbox, "grants-b"))
    assert _privileges() == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        item_id = _write(db, first)
        db.commit()

    # Bound to B: A's item is invisible, so an update or a delete changes none of it.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for statement in (
            f"UPDATE {TABLE} SET revision = revision + 1 WHERE workspace_id = :w",  # noqa: S608
            f"DELETE FROM {TABLE} WHERE workspace_id = :w",  # noqa: S608
        ):
            assert db.execute(text(statement), {"w": first["workspace_id"]}).rowcount == 0
        db.commit()
    assert _ids() == {item_id}
    assert _scalar(f"SELECT revision FROM {TABLE}") == 1  # noqa: S608

    # Bound to A: the compare-and-set update of PR 2 and a delete of its own row.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {TABLE} SET status = 'in_review', started_at = :now, "  # noqa: S608
                "revision = revision + 1 WHERE id = :id AND workspace_id = :w AND revision = 1"
            ),
            {"now": STARTED, "id": item_id, "w": first["workspace_id"]},
        )
        assert updated.rowcount == 1
        db.commit()
    assert _scalar(f"SELECT revision FROM {TABLE}") == 2  # noqa: S608
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        deleted = db.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": item_id})  # noqa: S608
        assert deleted.rowcount == 1
        db.commit()
    assert _ids() == set()


# --- the conversation purge and the backup reconciliation --------------------------------


def test_the_purge_deletes_the_items_before_the_scorecard_results_and_counts_them(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "purge-order")
    parents, result_id = _scored(tenant, "ordered")
    _add(ReviewItem, _item(parents, scorecard_result_id=result_id))
    kept = _add(ReviewItem, _item(_conversation(tenant)))
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            meeting = db.get(Meeting, parents["meeting_id"])
            removed = _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting)
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)

    deletes = [s.split()[2] for s in statements if s.lstrip().upper().startswith("DELETE FROM")]
    assert deletes.index(TABLE) < deletes.index("scorecard_results")
    # The item, the scorecard result and the transcript revision.  Had the result gone
    # first, its cascade would have taken the item uncounted.
    assert removed == 3
    assert _ids(meeting_id=parents["meeting_id"]) == set()
    assert _ids() == {kept}  # another conversation of the workspace keeps its item


def _items_of(meeting_id: str) -> int:
    return len(_ids(meeting_id=meeting_id))


def _queue(tenant: dict, meeting_id: str, key: str) -> None:
    """A stored result and two items on a processed conversation: one done, one open,
    both naming the result."""
    parents = _parents(tenant, key=key, meeting_id=meeting_id)
    result_id = _add(ScorecardResultRecord, _result(parents))
    _add(ReviewItem, _item(parents, **_status("completed"), scorecard_result_id=result_id))
    _add(ReviewItem, _item(parents, scorecard_result_id=result_id))


def test_the_conversation_purge_removes_the_items_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "review-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"review-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    _queue(tenant, queued, "purged")
    _queue(tenant, kept, "kept")
    assert (_items_of(queued), _items_of(kept)) == (2, 2)

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    assert _items_of(queued) == 0
    assert _items_of(kept) == 2  # another conversation of the workspace keeps its items
    # The purge deletes the two items itself (they are counted), next to the result.
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 3


def test_a_held_conversation_keeps_its_items_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "review-hold")
    meeting_id = _processed(client, tenant, settings, key="review-hold-0001")
    _queue(tenant, meeting_id, "held")
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-7", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    # The deletion of a held conversation: the purge waits and the items stay.
    _delete_conversation(client, tenant, meeting_id)
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == ERROR_LEGAL_HOLD
    assert _items_of(meeting_id) == 2
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    assert _items_of(meeting_id) == 2

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
    assert _items_of(meeting_id) == 0


def test_the_backup_reconciliation_counts_the_table():
    assert TABLE in RECONCILED_TABLES
    # The reconciliation counts a purged conversation's rows by meeting and workspace.
    columns = Base.metadata.tables[TABLE].c
    assert "meeting_id" in columns and "workspace_id" in columns
