"""The privileged MFA and break-glass migration (HOM-129, ADR 0076).

Revision ``d3d1c7aa3cd8`` after ``a3c7e19d4b62``: three user-scoped tables
(``user_mfa_totp``, ``mfa_recovery_codes``, ``break_glass_windows``), the column
``users.is_break_glass`` and three ``login_sessions`` columns.  The migrated schema
must equal the ORM on SQLite and PostgreSQL, and the downgrade must remove
everything the upgrade added and nothing else.
"""

from __future__ import annotations

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from backend.database.model_registry import Base
from backend.database.rls import APP_ROLE, UNPROTECTED_TABLES
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_action_proposals_migration import remove_action_proposals

NEW_REVISION = "d3d1c7aa3cd8"
BASE_REVISION = "a3c7e19d4b62"  # HOM-93: retrieval index
HEAD_REVISION = "fc9730c5f039"  # HOM-135: action proposals, after HOM-129
TABLES = ("user_mfa_totp", "mfa_recovery_codes", "break_glass_windows")
CHANGED_TABLES = ("users", "login_sessions")
SESSION_COLUMNS = {"mfa_verified_at", "mfa_method", "break_glass_window_id"}


def _migration_module():
    return importlib.import_module(
        f"backend.migrations.versions.{NEW_REVISION}_privileged_mfa_and_break_glass"
    )


def remove_privileged_mfa_schema(connection) -> None:
    """Take a ``create_all`` schema back to before HOM-129.

    Older migration tests build the full ORM, drop the newer schema and stamp an
    older revision.  This runs the revision's own downgrade, so the tables and the
    columns go exactly as the migration removes them (``login_sessions`` is rebuilt
    in batch mode on SQLite).
    """
    with Operations.context(MigrationContext.configure(connection)):
        _migration_module().downgrade()


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    assert module.__doc__.splitlines()[0] == "Privileged MFA and break-glass (HOM-129, WP23)"
    assert "Downgrade" in module.__doc__


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION


def test_the_new_tables_are_user_scoped_auth_tables_without_a_tenant_policy():
    for table in TABLES:
        assert table in UNPROTECTED_TABLES, table
        assert table in Base.metadata.tables, table


# --- SQLite ------------------------------------------------------------------------------


def _sqlite_shape(connection, table: str) -> dict:
    inspector = inspect(connection)
    return {
        "columns": {
            column["name"]: (str(column["type"]), column["nullable"], column["default"])
            for column in inspector.get_columns(table)
        },
        "foreign_keys": sorted(
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk["options"].get("ondelete"),
            )
            for fk in inspector.get_foreign_keys(table)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        ),
        "unique": sorted(
            tuple(unique["column_names"]) for unique in inspector.get_unique_constraints(table)
        ),
        "primary_key": tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
    }


def _all_sqlite_shapes(connection) -> dict:
    return {
        name: _sqlite_shape(connection, name)
        for name in inspect(connection).get_table_names()
        if name != "alembic_version"
    }


def test_on_sqlite_the_migration_builds_the_orm_schema_and_the_downgrade_removes_it():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {name: _sqlite_shape(connection, name) for name in TABLES + CHANGED_TABLES}

            remove_privileged_mfa_schema(connection)
            before = _all_sqlite_shapes(connection)
            assert not set(TABLES) & set(before)
            assert "is_break_glass" not in before["users"]["columns"]
            assert not SESSION_COLUMNS & set(before["login_sessions"]["columns"])

            with Operations.context(MigrationContext.configure(connection)):
                _migration_module().upgrade()
            after = _all_sqlite_shapes(connection)
            for name in TABLES + CHANGED_TABLES:
                assert after[name] == orm[name], name
            untouched = set(before) - set(CHANGED_TABLES)
            assert {name: after[name] for name in untouched} == {
                name: before[name] for name in untouched
            }

            remove_privileged_mfa_schema(connection)
            assert _all_sqlite_shapes(connection) == before
    finally:
        engine.dispose()


SESSION_ROWS = [
    ("s1", None, "refresh-1"),
    ("s2", "s1", "refresh-2"),
]


def _session_rows(connection) -> list[tuple]:
    return list(
        connection.execute(
            text("SELECT id, rotated_from_id, refresh_token_hash FROM login_sessions ORDER BY id")
        ).all()
    )


def test_on_sqlite_the_session_rebuild_keeps_every_row():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            remove_privileged_mfa_schema(connection)
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, is_active, system_role, locale, "
                    "session_version, created_at, updated_at) VALUES ('u1', 'u1@example.com', "
                    "'U', 1, 'user', 'fa', 1, '2026-11-13', '2026-11-13')"
                )
            )
            for session_id, rotated_from, refresh in SESSION_ROWS:
                connection.execute(
                    text(
                        "INSERT INTO login_sessions (id, user_id, refresh_token_hash, "
                        "csrf_token_hash, expires_at, last_seen_at, rotated_from_id, created_at, "
                        "updated_at) VALUES (:id, 'u1', :refresh, 'csrf', '2026-12-13', "
                        "'2026-11-13', :rotated, '2026-11-13', '2026-11-13')"
                    ),
                    {"id": session_id, "refresh": refresh, "rotated": rotated_from},
                )

            with Operations.context(MigrationContext.configure(connection)):
                _migration_module().upgrade()
            assert _session_rows(connection) == SESSION_ROWS
            verified = connection.execute(
                text("SELECT count(*) FROM login_sessions WHERE mfa_verified_at IS NULL")
            ).scalar()
            assert verified == len(SESSION_ROWS)
            flag = connection.execute(text("SELECT is_break_glass FROM users")).scalar()
            assert flag == 0

            remove_privileged_mfa_schema(connection)
            assert _session_rows(connection) == SESSION_ROWS
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


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()


def test_on_sqlite_alembic_upgrades_checks_clean_downgrades_and_upgrades_again(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    url = f"sqlite:///{tmp_path / 'privileged-mfa.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            remove_privileged_mfa_schema(connection)
            # HOM-135 adds the action proposal tables; take them out too.
            remove_action_proposals(connection)
            before = _all_sqlite_shapes(connection)

        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        assert _version(engine) == HEAD_REVISION
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")

        command.downgrade(config, NEW_REVISION)
        command.downgrade(config, "-1")
        assert _version(engine) == BASE_REVISION
        with engine.connect() as connection:
            assert _all_sqlite_shapes(connection) == before

        command.upgrade(config, "head")
        assert _version(engine) == HEAD_REVISION
    finally:
        engine.dispose()
        reset_settings_cache()


# --- PostgreSQL --------------------------------------------------------------------------


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _pg_shape(table: str) -> dict:
    """Columns, constraints, indexes and application grants as the catalog shows them."""
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
            text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": table}
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
        "policies": sorted(row[0] for row in policies),
        "grants": sorted(row[0] for row in grants),
    }


@pytest.mark.postgres
def test_on_postgres_the_migration_round_trips_to_the_orm_schema(client, monkeypatch, settings):
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")
    from alembic import command

    from backend.config import reset_settings_cache

    orm = {name: _pg_shape(name) for name in TABLES + CHANGED_TABLES}
    for name in TABLES:
        assert orm[name]["policies"] == []
        assert orm[name]["grants"] == ["DELETE", "INSERT", "SELECT", "UPDATE"]

    config = _alembic_at(monkeypatch, settings.database_url)
    try:
        command.stamp(config, "head")
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for name in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{name}"), name
        assert "is_break_glass" not in _pg_shape("users")["columns"]
        sessions = _pg_shape("login_sessions")
        assert not SESSION_COLUMNS & set(sessions["columns"])
        assert not any("break_glass" in name for name in sessions["constraints"])

        command.upgrade(config, "head")
        assert _scalar("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert {name: _pg_shape(name) for name in TABLES + CHANGED_TABLES} == orm
    finally:
        reset_settings_cache()
