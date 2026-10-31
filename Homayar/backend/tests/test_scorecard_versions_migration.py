"""HOM-54 PR 1: the ``scorecard_versions`` table and its one migration.

Every row is one published version of one scorecard of one workspace.  A change is a
new version; a stored row never changes.  The tests check:

* the ORM declares the columns, the named constraints and the index, and
  ``database/rls.py`` lists the table as a workspace table and as append-only;
* the ORM refuses to update or delete a stored version;
* on PostgreSQL, the ORM schema (``create_all``) and the migrated schema are the same;
* upgrade from ``f2c6a8e4b1d7`` creates the table, its constraints, its policy and its
  grants; downgrade removes them and leaves every other table as it was;
* tenant isolation: a session bound to workspace B sees no row of workspace A, and an
  insert for workspace B while bound to A fails;
* append-only: the application role cannot UPDATE or DELETE a row, while INSERT and
  SELECT in its own workspace work;
* the database refuses a duplicate ``(workspace_id, scorecard_key, version)``, a version
  below 1 and a key that breaks ``^[a-z][a-z0-9_]{1,63}$``, and the closest allow case
  passes.
"""

from __future__ import annotations

import importlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.config import Settings
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    POLICIES,
    POLICY_NAME,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
    apply_row_level_security,
)
from backend.database.saas_models import (
    ScorecardVersion,
    ScorecardVersionImmutableError,
    new_id,
    utcnow,
)
from backend.database.tenant_context import bind_tenant_context
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests._migration_locks import lock_tables_against_analyze
from backend.tests.conftest import login_user, register_user

NEW_REVISION = "47676f445509"
BASE_REVISION = "f2c6a8e4b1d7"  # HOM-290: trend_extraction_judgments
HEAD_REVISION = "484a6df74abf"  # HOM-73: review items, after HOM-56
TABLE = "scorecard_versions"
NAMED_CONSTRAINTS = (
    "uq_scorecard_version",
    "ck_scorecard_version_positive",
    "ck_scorecard_key_format",
)
INDEX = "ix_scorecard_versions_workspace_id"
DOCUMENT = {"name": "Inbound sales", "pass_threshold": 80, "criteria": []}
INSERT = text(
    "INSERT INTO scorecard_versions (id, workspace_id, scorecard_key, version, "
    "scorecards_version, document, digest, reason, created_by_user_id, created_at) "
    "VALUES (:id, :workspace_id, :scorecard_key, :version, :scorecards_version, "
    "CAST(:document AS JSON), :digest, :reason, :created_by_user_id, :created_at)"
)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"scorecard-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _alembic(monkeypatch, settings: Settings):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _row(tenant: dict, **extra: object) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "workspace_id": tenant["workspace_id"],
        "scorecard_key": "inbound_sales",
        "version": 1,
        "scorecards_version": "scorecards/1",
        "document": json.dumps(DOCUMENT),
        "digest": "0" * 64,
        "reason": "first publish",
        "created_by_user_id": tenant["user_id"],
        "created_at": datetime.now(UTC),
        **extra,
    }


def _insert_as_dba(row: dict[str, object]) -> None:
    with inspection_session_factory()() as db:
        db.execute(INSERT, row)
        db.commit()


def _rows() -> list[tuple]:
    with inspection_session_factory()() as db:
        return db.execute(
            text(
                "SELECT workspace_id, scorecard_key, version, reason FROM scorecard_versions "
                "ORDER BY workspace_id, scorecard_key, version"
            )
        ).all()


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


def _other_tables(connection: Connection | None = None) -> dict:
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


EXPECTED_COLUMNS = {
    "id": ("character varying", 36, "NO", None),
    "workspace_id": ("character varying", 36, "NO", None),
    "scorecard_key": ("character varying", 64, "NO", None),
    "version": ("integer", None, "NO", None),
    "scorecards_version": ("character varying", 40, "NO", None),
    "document": ("json", None, "NO", None),
    "digest": ("character varying", 64, "NO", None),
    "reason": ("character varying", 500, "NO", None),
    "created_by_user_id": ("character varying", 36, "YES", None),
    "created_at": ("timestamp with time zone", None, "NO", None),
}
EXPECTED_CONSTRAINTS = {
    "scorecard_versions_pkey": "PRIMARY KEY (id)",
    "scorecard_versions_workspace_id_fkey": (
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE"
    ),
    "scorecard_versions_created_by_user_id_fkey": (
        "FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL"
    ),
    "uq_scorecard_version": "UNIQUE (workspace_id, scorecard_key, version)",
    "ck_scorecard_version_positive": "CHECK ((version >= 1))",
}
EXPECTED_INDEXES = {
    "scorecard_versions_pkey": (
        "CREATE UNIQUE INDEX scorecard_versions_pkey ON public.scorecard_versions USING btree (id)"
    ),
    "uq_scorecard_version": (
        "CREATE UNIQUE INDEX uq_scorecard_version ON public.scorecard_versions "
        "USING btree (workspace_id, scorecard_key, version)"
    ),
    INDEX: f"CREATE INDEX {INDEX} ON public.scorecard_versions USING btree (workspace_id)",
}


def _assert_head_shape() -> dict:
    shape = _shape()
    assert shape["columns"] == EXPECTED_COLUMNS
    constraints = dict(shape["constraints"])
    assert set(constraints) == set(EXPECTED_CONSTRAINTS) | {"ck_scorecard_key_format"}
    constraints.pop("ck_scorecard_key_format")  # its behaviour is pinned by _assert_rules
    assert constraints == EXPECTED_CONSTRAINTS
    assert shape["indexes"] == EXPECTED_INDEXES
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    assert shape["grants"] == ["INSERT", "SELECT"]  # append-only for the application role
    return shape


# Deny cases (refused by the named constraint, or by NOT NULL and the column length) and
# their closest allow cases (SPEC section 3).
DENIED_KEYS = [
    "Inbound_sales",  # upper case
    "1nbound",  # starts with a digit
    "_inbound",  # starts with an underscore
    "a",  # one character: the pattern needs at least two
    "",
    "in-bound",
    "in bound",
    "inbound\n",  # Python's "$" would let this pass
    "ïnbound",
    "inbounD",
]
ALLOWED_KEYS = ["ab", "a1", "a_", "q9_x", "a" + "b" * 63]
REQUIRED_COLUMNS = (
    "workspace_id",
    "scorecard_key",
    "version",
    "scorecards_version",
    "document",
    "digest",
    "reason",
    "created_at",
)


def _assert_rules(first: dict, second: dict, label: str) -> None:
    _insert_as_dba(_row(first, reason=f"{label} v1"))

    # A duplicate (workspace, key, version) fails; the next version and another
    # workspace work.
    with pytest.raises(IntegrityError, match="uq_scorecard_version"):
        _insert_as_dba(_row(first, reason=f"{label} duplicate"))
    _insert_as_dba(_row(first, version=2, reason=f"{label} v2"))
    _insert_as_dba(_row(second, reason=f"{label} other workspace"))
    _insert_as_dba(_row(first, scorecard_key="outbound_sales", reason=f"{label} other key"))

    # version >= 1.
    for version in (0, -1):
        with pytest.raises(IntegrityError, match="ck_scorecard_version_positive"):
            _insert_as_dba(_row(first, scorecard_key="version_probe", version=version))
    _insert_as_dba(_row(first, scorecard_key="version_probe", version=1))

    # The key pattern.
    for key in DENIED_KEYS:
        with pytest.raises(IntegrityError, match="ck_scorecard_key_format"):
            _insert_as_dba(_row(first, scorecard_key=key))
    with pytest.raises(DBAPIError, match=r"character varying\(64\)"):
        _insert_as_dba(_row(first, scorecard_key="a" + "b" * 64))
    for key in ALLOWED_KEYS:
        _insert_as_dba(_row(first, scorecard_key=key))

    # NOT NULL columns; created_by_user_id may be null.
    for column in REQUIRED_COLUMNS:
        with pytest.raises(IntegrityError, match=f'null value in column "{column}"'):
            _insert_as_dba(_row(first, **{"scorecard_key": "null_probe", column: None}))
    _insert_as_dba(_row(first, scorecard_key="null_probe", created_by_user_id=None))

    stored = {(row[0], row[1], row[2]) for row in _rows()}
    expected = {
        (first["workspace_id"], "inbound_sales", 1),
        (first["workspace_id"], "inbound_sales", 2),
        (second["workspace_id"], "inbound_sales", 1),
        (first["workspace_id"], "outbound_sales", 1),
        (first["workspace_id"], "version_probe", 1),
        (first["workspace_id"], "null_probe", 1),
        *((first["workspace_id"], key, 1) for key in ALLOWED_KEYS),
    }
    assert stored == expected


# --- the ORM and the RLS lists ----------------------------------------------------------


def test_the_orm_declares_the_table():
    table = ScorecardVersion.__table__
    assert table.name == TABLE
    lengths = {
        "id": 36,
        "workspace_id": 36,
        "scorecard_key": 64,
        "scorecards_version": 40,
        "digest": 64,
        "reason": 500,
        "created_by_user_id": 36,
    }
    for name, length in lengths.items():
        assert table.c[name].type.length == length, name
    assert [column.name for column in table.primary_key] == ["id"]
    # SQLAlchemy wraps a callable default and keeps its name.
    assert table.c.id.default.arg.__name__ == new_id.__name__
    assert table.c.created_at.default.arg.__name__ == utcnow.__name__
    assert table.c.created_at.type.timezone
    nullable = {column.name for column in table.columns if column.nullable}
    assert nullable == {"created_by_user_id"}
    foreign_keys = {
        key.parent.name: (key.target_fullname, key.ondelete) for key in table.foreign_keys
    }
    assert foreign_keys == {
        "workspace_id": ("workspaces.id", "CASCADE"),
        "created_by_user_id": ("users.id", "SET NULL"),
    }
    named = {constraint.name: constraint for constraint in table.constraints}
    assert set(NAMED_CONSTRAINTS) <= set(named)
    assert [c.name for c in named["uq_scorecard_version"].columns] == [
        "workspace_id",
        "scorecard_key",
        "version",
    ]
    indexes = {index.name: index for index in table.indexes}
    assert set(indexes) == {INDEX}
    assert [c.name for c in indexes[INDEX].columns] == ["workspace_id"]
    assert not indexes[INDEX].unique
    assert "append-only" in ScorecardVersion.__doc__.lower()
    assert "HOM-54" in ScorecardVersion.__doc__


def test_the_table_is_an_append_only_workspace_table():
    assert TABLE in WORKSPACE_TABLES
    assert TABLE in APPEND_ONLY_TABLES
    assert TABLE not in SHARED_CONTENT_TABLES  # never through a share token
    assert TABLE not in UNPROTECTED_TABLES
    assert POLICIES[TABLE] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    # The same treatment as the AI policy versions (ADR 0027).
    assert POLICIES[TABLE] == POLICIES["ai_policy_versions"]


def test_the_orm_refuses_to_change_a_stored_version(client, email_outbox):
    tenant = _tenant(client, email_outbox, "orm")
    factory = inspection_session_factory()
    with factory() as db:
        row = ScorecardVersion(
            workspace_id=tenant["workspace_id"],
            scorecard_key="inbound_sales",
            version=1,
            scorecards_version="scorecards/1",
            document=DOCUMENT,
            digest="0" * 64,
            reason="first publish",
            created_by_user_id=tenant["user_id"],
        )
        db.add(row)
        db.commit()
        row_id = row.id
        assert len(row_id) == 36 and row.created_at is not None

    with factory() as db:
        stored = db.get(ScorecardVersion, row_id)
        stored.reason = "rewritten"
        with pytest.raises(ScorecardVersionImmutableError, match="reason"):
            db.flush()
        db.rollback()
        db.delete(db.get(ScorecardVersion, row_id))
        with pytest.raises(ScorecardVersionImmutableError):
            db.flush()
        db.rollback()

    # Allow-control: the next version is a new row, and nothing changed.
    with factory() as db:
        db.add(
            ScorecardVersion(
                workspace_id=tenant["workspace_id"],
                scorecard_key="inbound_sales",
                version=2,
                scorecards_version="scorecards/1",
                document=DOCUMENT,
                digest="1" * 64,
                reason="second publish",
            )
        )
        db.commit()
    assert [(row[2], row[3]) for row in _rows()] == [(1, "first publish"), (2, "second publish")]


# --- PostgreSQL: the ORM schema (create_all) ---------------------------------------------


@pytest.mark.postgres
def test_the_orm_schema_has_the_table_and_refuses_the_deny_cases(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "rules-a")
    second = _tenant(client, email_outbox, "rules-b")
    _assert_head_shape()
    _assert_rules(first, second, "orm")


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _tenant(client, email_outbox, "tenant-a")
    second = _tenant(client, email_outbox, "tenant-b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.execute(INSERT, _row(first, reason="a v1"))
        db.commit()

    # Bound to B: A's row is invisible, B writes its own version 1 of the same key.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        assert db.scalar(text("SELECT count(*) FROM scorecard_versions")) == 0
        assert (
            db.scalar(
                text("SELECT count(*) FROM scorecard_versions WHERE workspace_id = :w"),
                {"w": first["workspace_id"]},
            )
            == 0
        )
        db.execute(INSERT, _row(second, reason="b v1"))
        db.commit()

    # Bound to A: only A's row; an insert for B fails.
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        visible = db.execute(text("SELECT workspace_id, reason FROM scorecard_versions")).all()
        assert [tuple(row) for row in visible] == [(first["workspace_id"], "a v1")]
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(INSERT, _row(second, version=2, reason="written by a"))
        db.rollback()

    # No workspace bound: nothing.
    with factory() as db:
        assert db.scalar(text("SELECT count(*) FROM scorecard_versions")) == 0

    assert sorted((row[0], row[3]) for row in _rows()) == sorted(
        [(first["workspace_id"], "a v1"), (second["workspace_id"], "b v1")]
    )


@pytest.mark.postgres
def test_the_app_role_reads_and_inserts_but_never_updates_or_deletes(client, email_outbox):
    _requires_postgres()
    tenant = _tenant(client, email_outbox, "append-only")
    privileges = {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=TABLE, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    assert privileges == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        db.execute(INSERT, _row(tenant, reason="v1"))
        db.commit()

    # The workspace is bound, so the row is visible: the refusal is the missing grant.
    for statement in (
        "UPDATE scorecard_versions SET reason = 'rewritten'",
        "DELETE FROM scorecard_versions",
    ):
        with factory() as db:
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            assert db.scalar(text("SELECT count(*) FROM scorecard_versions")) == 1
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()

    # Allow-control: read and append the next version in its own workspace.
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        assert db.scalar(text("SELECT reason FROM scorecard_versions")) == "v1"
        db.execute(INSERT, _row(tenant, version=2, reason="v2"))
        db.commit()
    assert [(row[2], row[3]) for row in _rows()] == [(1, "v1"), (2, "v2")]


# --- PostgreSQL: the migration -----------------------------------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_scorecard_versions")


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_table(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION
    first = _tenant(client, email_outbox, "migration-a")
    second = _tenant(client, email_outbox, "migration-b")
    orm_shape = _assert_head_shape()
    workspaces = _scalar("SELECT count(*) FROM workspaces")
    _insert_as_dba(_row(first, reason="before the downgrade"))

    config = _alembic(monkeypatch, settings)
    try:
        command.stamp(config, "head")
        command.downgrade(config, NEW_REVISION)
        # The re-upgrade applies the live RLS registry (HOM-89), so the baseline must too.
        with inspection_session_factory()() as db:
            lock_tables_against_analyze(db.connection())  # HOM-352
            apply_row_level_security(db.connection())
            db.commit()
        others = _other_tables()  # after the stamp, which adds alembic_version
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{TABLE}")
        assert _shape() == {
            "columns": {},
            "constraints": {},
            "indexes": {},
            "policies": {},
            "rls": [],
            "grants": [],
        }
        assert _other_tables() == others  # every other table as it was
        assert _scalar("SELECT count(*) FROM workspaces") == workspaces

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert _shape() == orm_shape  # the migrated schema equals the ORM schema
        assert _other_tables() == others
        assert _rows() == []  # the downgrade dropped the rows with the table

        # The migrated schema refuses the same deny cases.
        _assert_rules(first, second, "migrated")
        assert _rows()

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == others
        command.upgrade(config, NEW_REVISION)
        assert _shape() == orm_shape
        assert _other_tables() == others
        assert _rows() == []
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()
