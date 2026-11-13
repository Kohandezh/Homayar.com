"""HOM-133: the nullable ``action_items.kind`` column and its one migration (ADR 0058).

Upgrade adds the column and ``ck_action_kind``; existing rows read NULL; the check
refuses any other value and takes ``commitment``, ``next_step`` and NULL.  Downgrade
removes both and keeps the rows.  On SQLite here; on PostgreSQL in the
``postgres``-marked test.
"""

from __future__ import annotations

import importlib
import io
import uuid
from datetime import UTC, datetime

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.database.model_registry import Base
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_coaching_tasks_migration import remove_coaching_tasks
from backend.tests.test_federation import remove_federation_group_mappings
from backend.tests.test_retrieval_index_migration import remove_retrieval_index_tables

NEW_REVISION = "c4a8e2f7d913"
BASE_REVISION = "0c51985fcf36"  # HOM-76: review appeals
HEAD_REVISION = "a3c7e19d4b62"  # HOM-93: retrieval index, after HOM-128
TABLE = "action_items"
CHECK = "ck_action_kind"
INSERT_WITHOUT_KIND = (
    "INSERT INTO action_items (id, workspace_id, meeting_id, description, priority, status, "
    "created_by, evidence_status, created_at, updated_at) VALUES (:id, :w, :m, 'd', 'medium', "
    "'open', 'ai', 'insufficient', :now, :now)"
)
INSERT_WITH_KIND = (
    "INSERT INTO action_items (id, workspace_id, meeting_id, description, priority, status, "
    "created_by, evidence_status, created_at, updated_at, kind) VALUES (:id, :w, :m, 'd', "
    "'medium', 'open', 'ai', 'insufficient', :now, :now, :kind)"
)


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_action_item_kind")


def remove_action_item_kind(connection) -> None:
    """Take a ``create_all`` SQLite schema back to before HOM-133.

    Older migration tests build the full ORM, drop the newer tables and stamp an
    older revision.  A column cannot be dropped like a table, so run this
    revision's own downgrade: on a ``create_all`` schema it rebuilds the table once
    in batch mode.  The upgrade and a later downgrade then add and drop the column
    in place and leave the table's SQL text as that rebuild wrote it.
    """
    with Operations.context(MigrationContext.configure(connection)):
        _migration_module().downgrade()


def _columns(connection) -> dict[str, tuple[str, bool]]:
    return {
        column["name"]: (str(column["type"]), column["nullable"])
        for column in inspect(connection).get_columns(TABLE)
    }


def _checks(connection) -> dict[str, str]:
    return {
        check["name"]: check["sqltext"]
        for check in inspect(connection).get_check_constraints(TABLE)
    }


def _seed(connection) -> tuple[str, str]:
    """Ids for raw ``action_items`` rows; foreign keys are off, so no parent rows."""
    connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
    return str(uuid.uuid4()), str(uuid.uuid4())


def _insert_action(connection, workspace_id: str, meeting_id: str, *, kind=None, with_kind=True):
    action_id = str(uuid.uuid4())
    now = datetime(2026, 11, 12, tzinfo=UTC).isoformat()
    params = {"id": action_id, "w": workspace_id, "m": meeting_id, "now": now, "kind": kind}
    connection.execute(text(INSERT_WITH_KIND if with_kind else INSERT_WITHOUT_KIND), params)
    return action_id


# --- the revision ---------------------------------------------------------------------


def test_the_migration_follows_the_hom76_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__ or ""
    assert "HOM-133" in doc.splitlines()[0], doc
    for words in ("Why", "No data changes", "Downgrade"):
        assert words in doc, words


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    # HOM-77 moved the head past this revision; this one stays the head's parent.
    assert code_migration_head() == HEAD_REVISION


def test_alembic_heads_prints_exactly_one_head():
    from alembic import command
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    shown = io.StringIO()
    command.heads(Config(str(PROJECT_ROOT / "alembic.ini"), stdout=shown))
    lines = [line for line in shown.getvalue().splitlines() if line.strip()]
    assert lines == [f"{HEAD_REVISION} (head)"], shown.getvalue()


def test_the_orm_column_is_nullable_with_the_check():
    column = Base.metadata.tables[TABLE].c.kind
    assert column.nullable is True
    assert column.type.length == 16
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables[TABLE].constraints
        if constraint.name == CHECK
    }
    assert checks == {CHECK: "kind IS NULL OR kind IN ('commitment', 'next_step')"}


# --- SQLite ---------------------------------------------------------------------------


def test_on_sqlite_upgrade_adds_the_column_and_downgrade_removes_it():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm_columns = _columns(connection)
            orm_checks = _checks(connection)
            remove_action_item_kind(connection)
            before_columns = _columns(connection)
            assert "kind" not in before_columns
            assert CHECK not in _checks(connection)

            workspace_id, meeting_id = _seed(connection)
            existing = _insert_action(connection, workspace_id, meeting_id, with_kind=False)

            with Operations.context(MigrationContext.configure(connection)):
                _migration_module().upgrade()
            assert _columns(connection) == orm_columns
            assert _checks(connection) == orm_checks
            assert orm_columns["kind"] == ("VARCHAR(16)", True)
            # The existing row reads NULL: no backfill.
            kind = connection.execute(
                text("SELECT kind FROM action_items WHERE id = :id"), {"id": existing}
            ).scalar_one()
            assert kind is None

            # Allow side: both labels and NULL.
            for value in ("commitment", "next_step", None):
                _insert_action(connection, workspace_id, meeting_id, kind=value)
            # Deny side: any other value.
            for value in ("other", "Commitment", ""):
                savepoint = connection.begin_nested()
                with pytest.raises(IntegrityError):
                    _insert_action(connection, workspace_id, meeting_id, kind=value)
                savepoint.rollback()

            with Operations.context(MigrationContext.configure(connection)):
                _migration_module().downgrade()
            assert _columns(connection) == before_columns
            assert CHECK not in _checks(connection)
            # The rows stay; only the kind is gone.
            count = connection.execute(text("SELECT count(*) FROM action_items")).scalar_one()
            assert count == 4
    finally:
        engine.dispose()


def test_on_sqlite_alembic_upgrades_to_the_head_checks_clean_and_downgrades_one_step(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.config import Config
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    url = f"sqlite:///{tmp_path / 'action-kind.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            # HOM-128 adds the federation mapping table and two columns, HOM-77
            # ``coaching_tasks``, on top; take them out too.
            remove_federation_group_mappings(connection)
            remove_coaching_tasks(connection)
            remove_action_item_kind(connection)
            remove_retrieval_index_tables(connection)

        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("ENVIRONMENT", "test")
        reset_settings_cache()
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == HEAD_REVISION
            assert _columns(connection)["kind"] == ("VARCHAR(16)", True)
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")

        # Down past HOM-77 to this revision, then one more step.
        command.downgrade(config, NEW_REVISION)
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == BASE_REVISION
            assert "kind" not in _columns(connection)
    finally:
        engine.dispose()
        reset_settings_cache()


# --- PostgreSQL -----------------------------------------------------------------------


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _pg_scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _pg_column() -> tuple | None:
    with inspection_session_factory()() as db:
        row = db.execute(
            text(
                "SELECT data_type, character_maximum_length, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = 'kind'"
            ),
            {"t": TABLE},
        ).first()
    return tuple(row) if row is not None else None


def _pg_check() -> str | None:
    return _pg_scalar(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = :c AND conrelid = 'public.action_items'::regclass",
        c=CHECK,
    )


def _pg_insert(meeting: tuple[str, str], kind, *, with_kind: bool = True) -> str:
    workspace_id, meeting_id = meeting
    action_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    params = {"id": action_id, "w": workspace_id, "m": meeting_id, "now": now, "kind": kind}
    with inspection_session_factory()() as db:
        db.execute(text(INSERT_WITH_KIND if with_kind else INSERT_WITHOUT_KIND), params)
        db.commit()
    return action_id


def _pg_meeting(client, email_outbox) -> tuple[str, str]:
    from sqlalchemy import select

    from backend.database.saas_models import Meeting, MeetingStatus, Membership
    from backend.tests.test_uploads_api import _authenticated_workspace

    _, workspace_id = _authenticated_workspace(client, email_outbox)
    with inspection_session_factory()() as db:
        user_id = db.scalar(
            select(Membership.user_id).where(Membership.workspace_id == workspace_id)
        )
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            title="HOM-133 migration",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
        )
        db.add(meeting)
        db.commit()
        return workspace_id, meeting.id


@pytest.mark.postgres
def test_on_postgres_the_migration_round_trips_with_rows_present(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT, code_migration_head

    assert code_migration_head() == HEAD_REVISION
    assert _pg_column() == ("character varying", 16, "YES")
    assert _pg_check() is not None
    meeting = _pg_meeting(client, email_outbox)

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    try:
        command.stamp(config, "head")
        command.downgrade(config, BASE_REVISION)
        assert _pg_scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _pg_column() is None and _pg_check() is None
        existing = _pg_insert(meeting, None, with_kind=False)

        command.upgrade(config, "head")
        assert _pg_scalar("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert _pg_column() == ("character varying", 16, "YES")
        assert _pg_scalar("SELECT kind FROM action_items WHERE id = :id", id=existing) is None

        for value in ("commitment", "next_step", None):
            _pg_insert(meeting, value)
        for value in ("other", "Commitment", ""):
            with pytest.raises((IntegrityError, DBAPIError)):
                _pg_insert(meeting, value)
    finally:
        reset_settings_cache()
