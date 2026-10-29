"""The shared fixtures isolate every test from the one before it (HOM-292).

On PostgreSQL the canonical schema is built once per process.  Between tests that use
``client`` only the rows are reset; a changed schema or cluster-wide state is detected
from the catalogs and rebuilt.  A test with ``settings`` but without ``client`` starts
from the base drop (no tables), as before.

The tests run in file order.  Each check names the writer that must have run before it
in this process and asserts it did, then asserts the clean state.  Each writer first
asserts that its own change is visible to itself.
"""

# ruff: noqa: S608 - the SQL names fixed probe objects and catalog tables only.

from __future__ import annotations

import secrets
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from backend.database.model_registry import Base
from backend.database.relational import create_schema_for_test_or_development, get_engine
from backend.database.rls import APP_ROLE, AUDIT_READER_ROLE, POLICIES, POLICY_NAME
from backend.database.saas_models import Plan, PlanVersion, User
from backend.main import create_app
from backend.services.tenancy_service import PLAN_CATALOG
from backend.tests import _isolation
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
    reset_postgres_schema,
)
from backend.tests.conftest import register_user

SEEDED_TABLES = frozenset({"plans", "plan_versions"})
PROBE_PLAN = "hom292_probe_plan"

_finished: set[str] = set()
_reference: dict[str, object] = {}
_before: dict[str, object] = {}


def _done(name: str) -> None:
    _finished.add(name)


def _ran(name: str) -> None:
    assert name in _finished, f"{name} must run earlier in this process (run the whole module)"


def _scalar(sql: str, **params: object) -> object:
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _admin(*statements: str) -> None:
    with inspection_session_factory()() as db:
        for statement in statements:
            db.execute(text(statement))
        db.commit()


def _catalog_rows() -> tuple[list[tuple], list[tuple]]:
    with inspection_session_factory()() as db:
        plans = sorted(
            (p.key, p.name, p.is_public, p.is_active, p.current_version, p.entitlements_json)
            for p in db.scalars(select(Plan))
        )
        versions = sorted(
            (v.plan_key, v.version, v.name, v.is_public, v.entitlements_json, v.source)
            for v in db.scalars(select(PlanVersion))
        )
    return plans, versions


def _row_counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        names = inspect(db.connection()).get_table_names()
        return {
            name: db.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one() for name in names
        }


def _missing_model_tables() -> list[str]:
    return [
        table.name
        for table in Base.metadata.sorted_tables
        if _scalar("SELECT to_regclass(:name) IS NULL", name=f"public.{table.name}")
    ]


def _rls_flags(table: str) -> tuple[bool, bool]:
    with inspection_session_factory()() as db:
        row = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:name)"
            ),
            {"name": f"public.{table}"},
        ).one()
    return bool(row[0]), bool(row[1])


_SCHEMA_OBJECTS = text(
    """
    WITH ns AS (
      SELECT oid, nspname FROM pg_namespace
       WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
         AND nspname NOT LIKE 'pg\\_temp\\_%' AND nspname NOT LIKE 'pg\\_toast\\_temp\\_%'
    )
    SELECT 'schema', nspname::text FROM ns
    UNION ALL SELECT 'relation ' || c.relkind::text, ns.nspname || '.' || c.relname
      FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
    UNION ALL SELECT 'constraint', k.conrelid::regclass::text || '.' || k.conname
      FROM pg_constraint k JOIN ns ON ns.oid = k.connamespace
    UNION ALL SELECT 'routine', p.oid::regprocedure::text
      FROM pg_proc p JOIN ns ON ns.oid = p.pronamespace
    UNION ALL SELECT 'type ' || t.typtype::text, t.oid::regtype::text
      FROM pg_type t JOIN ns ON ns.oid = t.typnamespace
     WHERE t.typtype <> 'c' OR t.typrelid IN (SELECT oid FROM pg_class WHERE relkind = 'c')
    UNION ALL SELECT 'trigger', t.tgrelid::regclass::text || '.' || t.tgname
      FROM pg_trigger t WHERE NOT t.tgisinternal
    UNION ALL SELECT 'policy', p.polrelid::regclass::text || '.' || p.polname FROM pg_policy p
    UNION ALL SELECT 'collation', c.collnamespace::regnamespace::text || '.' || c.collname
      FROM pg_collation c JOIN ns ON ns.oid = c.collnamespace
    UNION ALL SELECT 'event trigger', evtname FROM pg_event_trigger
    UNION ALL SELECT 'extension', extname FROM pg_extension
    """
)


def _schema_objects(connection) -> frozenset[tuple[str, str]]:
    return frozenset(tuple(row) for row in connection.execute(_SCHEMA_OBJECTS))


@pytest.fixture(scope="module")
def real_build_objects() -> Iterator[frozenset[tuple[str, str]]]:
    """Every object a real build creates on an empty database (a scratch database)."""
    url = configured_database_url()
    if not is_postgres_url(url):
        yield frozenset()
        return
    name = f"hom292_reference_{secrets.token_hex(4)}"
    admin = create_engine(
        make_url(url).set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    scratch = make_url(url).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        create_schema_for_test_or_development(_isolation._schema_settings(scratch))
        engine = _isolation.admin_engine(scratch)
        try:
            with engine.connect() as connection:
                objects = _schema_objects(connection)
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
    yield objects


def _assert_canonical_schema(real_build_objects: frozenset[tuple[str, str]]) -> None:
    """Exactly the real build's objects, with RLS on, and the canonical fingerprint."""
    with inspection_session_factory()() as db:
        objects = _schema_objects(db.connection())
    assert sorted(objects - real_build_objects) == []  # nothing a test left behind
    assert sorted(real_build_objects - objects) == []  # nothing missing
    assert _missing_model_tables() == []
    for table in POLICIES:
        assert _rls_flags(table) == (True, True), table
        assert (
            _scalar(
                "SELECT count(*) FROM pg_policy WHERE polrelid = to_regclass(:t) AND polname = :p",
                t=f"public.{table}",
                p=POLICY_NAME,
            )
            == 1
        ), table
    assert _isolation.current_fingerprint() == _isolation.canonical_fingerprint()


# --- rule 1: rows, with the plain shared fixtures (PostgreSQL and SQLite) ---------------------


def test_rows_reference_is_the_catalog_the_startup_seeds(client):
    plans, versions = _catalog_rows()
    assert {row[0] for row in plans} == {item["key"] for item in PLAN_CATALOG}
    assert {row[0] for row in versions} == {item["key"] for item in PLAN_CATALOG}
    _reference["catalog"] = (plans, versions)
    _done("test_rows_reference_is_the_catalog_the_startup_seeds")


def test_rows_writer_fills_tables_and_edits_the_seeded_catalog(client, email_outbox):
    register_user(client, email_outbox, email="isolation-writer@example.com")
    with inspection_session_factory()() as db:
        db.add(
            Plan(
                key=PROBE_PLAN,
                name=PROBE_PLAN,
                is_public=False,
                is_active=True,
                entitlements_json={},
                display_json={},
                current_version=1,
            )
        )
        db.flush()
        db.add(
            PlanVersion(
                plan_key=PROBE_PLAN,
                version=1,
                name=PROBE_PLAN,
                is_public=False,
                entitlements_json={},
                source="operator",
            )
        )
        db.get(Plan, "professional").name = "Edited by the writer"
        db.commit()
    # allow-control: the writer sees its own rows
    counts = _row_counts()
    assert counts["users"] == 1 and counts["auth_identities"] >= 1
    assert _scalar("SELECT count(*) FROM plans WHERE key = :k", k=PROBE_PLAN) == 1
    assert _scalar("SELECT name FROM plans WHERE key = 'professional'") == "Edited by the writer"
    _done("test_rows_writer_fills_tables_and_edits_the_seeded_catalog")


def test_rows_check_every_table_is_empty_but_the_seeded_catalog(client):
    _ran("test_rows_reference_is_the_catalog_the_startup_seeds")
    _ran("test_rows_writer_fills_tables_and_edits_the_seeded_catalog")
    counts = _row_counts()
    assert {name: n for name, n in counts.items() if n and name not in SEEDED_TABLES} == {}
    assert _catalog_rows() == _reference["catalog"]


def test_rows_check_holds_when_settings_is_set_up_before_client(settings, client):
    # The same clean start when ``settings`` comes first in the argument list.
    _ran("test_rows_writer_fills_tables_and_edits_the_seeded_catalog")
    with inspection_session_factory()() as db:
        assert db.scalars(select(User)).all() == []


# --- rule 2: sequences and identities, and both reset paths (R2) -----------------------------


def test_the_reset_path_follows_the_login_role():
    assert _isolation.reset_strategy(is_superuser=True) == "delete"
    assert _isolation.reset_strategy(is_superuser=False) == "truncate"


@pytest.mark.postgres
def test_the_fixture_uses_the_path_of_its_login_role(client):
    superuser = _scalar("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    assert _isolation.active_reset_strategy() == _isolation.reset_strategy(
        is_superuser=bool(superuser)
    )


@pytest.mark.postgres
@pytest.mark.parametrize("strategy", ["delete", "truncate"])
def test_both_reset_paths_empty_tables_and_restart_identities(client, email_outbox, strategy):
    register_user(client, email_outbox, email=f"reset-{strategy}@example.com")
    _admin(
        "CREATE TABLE hom292_identity (id int GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, note "
        "text)",
        "CREATE SEQUENCE hom292_counter",
        "INSERT INTO hom292_identity (note) VALUES ('a'), ('b'), ('c')",
        "SELECT nextval('hom292_counter'), nextval('hom292_counter')",
    )
    assert _scalar("SELECT max(id) FROM hom292_identity") == 3
    assert _scalar("SELECT count(*) FROM users") == 1
    engine = _isolation.admin_engine()
    try:
        with engine.begin() as connection:
            # The names exactly as the fixture's inventory gives them (schema-qualified).
            inventory = connection.execute(_isolation._INVENTORY).all()
            tables = [name for name, kind in inventory if kind in ("r", "p")]
            sequences = [name for name, kind in inventory if kind == "S"]
            assert set(sequences) == {"public.hom292_counter", "public.hom292_identity_id_seq"}
            _isolation.reset_rows(
                connection, tables=tables, sequences=sequences, strategy=strategy
            )
    finally:
        engine.dispose()
    assert _scalar("SELECT count(*) FROM users") == 0
    assert _scalar("SELECT count(*) FROM hom292_identity") == 0
    _admin("INSERT INTO hom292_identity (note) VALUES ('again')")
    assert _scalar("SELECT id FROM hom292_identity") == 1
    assert _scalar("SELECT nextval('hom292_counter')") == 1
    _done(f"test_both_reset_paths_empty_tables_and_restart_identities[{strategy}]")


@pytest.mark.postgres
def test_identity_probe_objects_are_gone_for_the_next_test(client):
    _ran("test_both_reset_paths_empty_tables_and_restart_identities[truncate]")
    assert _scalar("SELECT to_regclass('public.hom292_identity')") is None
    assert _scalar("SELECT to_regclass('public.hom292_counter')") is None


# --- rule 3: schema drift is detected without a marker ---------------------------------------


def _one_index(table: str) -> str:
    return _scalar(
        "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE i.indrelid = to_regclass(:t) AND NOT EXISTS "
        "(SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid) "
        "AND c.relname NOT LIKE 'hom292%' ORDER BY 1 LIMIT 1",
        t=f"public.{table}",
    )


def _one_constraint(table: str, kind: str) -> str:
    return _scalar(
        "SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(:t) AND contype = :k "
        "ORDER BY 1 LIMIT 1",
        t=f"public.{table}",
        k=kind,
    )


def _policy_using(table: str) -> str | None:
    return _scalar(
        "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
        "WHERE polrelid = to_regclass(:t) AND polname = :p",
        t=f"public.{table}",
        p=POLICY_NAME,
    )


def _owner(table: str) -> str:
    return _scalar(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass(:t)",
        t=f"public.{table}",
    )


def _default_acl() -> str | None:
    return _scalar("SELECT string_agg(defaclacl::text, ';' ORDER BY oid) FROM pg_default_acl")


def _function_exists(name: str) -> bool:
    return bool(
        _scalar(
            "SELECT count(*) FROM pg_proc WHERE proname = :n AND pronamespace = "
            "'public'::regnamespace",
            n=name,
        )
    )


def _user_triggers(table: str) -> int:
    return _scalar(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass(:t) AND NOT tgisinternal",
        t=f"public.{table}",
    )


def _disabled_triggers(table: str) -> int:
    return _scalar(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass(:t) AND tgenabled = 'D'",
        t=f"public.{table}",
    )


def _write_index_dropped() -> None:
    name = _one_index("users")
    _before["index_dropped"] = name
    _admin(f'DROP INDEX "{name}"')
    assert _scalar("SELECT to_regclass(:n)", n=f"public.{name}") is None


def _write_constraint_dropped() -> None:
    name = _one_constraint("memberships", "f")
    _before["constraint_dropped"] = name
    _admin(f'ALTER TABLE memberships DROP CONSTRAINT "{name}"')
    assert _one_constraint("memberships", "f") != name


def _write_policy_altered() -> None:
    _before["policy_altered"] = _policy_using("meetings")
    _admin(f"ALTER POLICY {POLICY_NAME} ON meetings USING (true)")
    assert _policy_using("meetings") == "true"


def _write_owner_changed() -> None:
    _before["owner_changed"] = _owner("meetings")
    _admin(f"ALTER TABLE meetings OWNER TO {APP_ROLE}")
    assert _owner("meetings") == APP_ROLE


def _write_default_privileges() -> None:
    _before["default_privileges"] = _default_acl()
    _admin(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {APP_ROLE}")
    assert _default_acl() != _before["default_privileges"]


def _write_trigger_created() -> None:
    _admin(
        "CREATE FUNCTION hom292_trigger_fn() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RETURN NEW; END $$",
        "CREATE TRIGGER hom292_trigger BEFORE INSERT ON users FOR EACH ROW "
        "EXECUTE FUNCTION hom292_trigger_fn()",
    )
    assert _user_triggers("users") == 1


def _ddl(*statements: str, visible: str):
    def write() -> None:
        _admin(*statements)
        assert _scalar(visible), visible

    return write


DRIFT: dict[str, tuple] = {
    "table_created": (
        _ddl(
            "CREATE TABLE hom292_drift (id int)",
            visible="SELECT to_regclass('public.hom292_drift') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regclass('public.hom292_drift')") is None,
    ),
    "table_dropped": (
        _ddl(
            "DROP TABLE share_recipients CASCADE",
            visible="SELECT to_regclass('public.share_recipients') IS NULL",
        ),
        lambda: _scalar("SELECT to_regclass('public.share_recipients') IS NOT NULL"),
    ),
    "table_altered": (
        _ddl(
            "ALTER TABLE meetings SET (fillfactor = 70)",
            visible="SELECT reloptions IS NOT NULL FROM pg_class WHERE oid = 'meetings'::regclass",
        ),
        lambda: _scalar("SELECT reloptions IS NULL FROM pg_class WHERE oid = 'meetings'::regclass"),
    ),
    "column_added": (
        _ddl(
            "ALTER TABLE users ADD COLUMN hom292_extra int",
            visible="SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' "
            "AND column_name = 'hom292_extra'",
        ),
        lambda: _scalar(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' AND "
            "column_name = 'hom292_extra'"
        )
        == 0,
    ),
    "column_altered": (
        _ddl(
            "ALTER TABLE users ALTER COLUMN display_name SET DEFAULT 'drift'",
            visible="SELECT column_default LIKE '%drift%' FROM information_schema.columns WHERE "
            "table_name = 'users' AND column_name = 'display_name'",
        ),
        lambda: not _scalar(
            "SELECT coalesce(column_default, '') LIKE '%drift%' FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'display_name'"
        ),
    ),
    "column_dropped": (
        _ddl(
            "ALTER TABLE users DROP COLUMN locale",
            visible="SELECT count(*) = 0 FROM information_schema.columns WHERE table_name = "
            "'users' AND column_name = 'locale'",
        ),
        lambda: _scalar(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' AND "
            "column_name = 'locale'"
        )
        == 1,
    ),
    "index_created": (
        _ddl(
            "CREATE INDEX hom292_drift_idx ON users (display_name)",
            visible="SELECT to_regclass('public.hom292_drift_idx') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regclass('public.hom292_drift_idx')") is None,
    ),
    "index_dropped": (
        _write_index_dropped,
        lambda: _scalar(
            "SELECT to_regclass(:n) IS NOT NULL", n=f"public.{_before['index_dropped']}"
        ),
    ),
    "constraint_added": (
        _ddl(
            "ALTER TABLE users ADD CONSTRAINT hom292_check CHECK (length(display_name) < 1000)",
            visible="SELECT count(*) FROM pg_constraint WHERE conname = 'hom292_check'",
        ),
        lambda: _scalar("SELECT count(*) FROM pg_constraint WHERE conname = 'hom292_check'") == 0,
    ),
    "constraint_dropped": (
        _write_constraint_dropped,
        lambda: _scalar(
            "SELECT count(*) FROM pg_constraint WHERE conname = :n AND conrelid = "
            "'memberships'::regclass",
            n=_before["constraint_dropped"],
        )
        == 1,
    ),
    "policy_created": (
        _ddl(
            "CREATE POLICY hom292_leak ON meetings USING (true)",
            visible="SELECT count(*) FROM pg_policy WHERE polname = 'hom292_leak'",
        ),
        lambda: _scalar("SELECT count(*) FROM pg_policy WHERE polname = 'hom292_leak'") == 0,
    ),
    "policy_altered": (
        _write_policy_altered,
        lambda: _policy_using("meetings") == _before["policy_altered"],
    ),
    "policy_dropped": (
        _ddl(
            f"DROP POLICY {POLICY_NAME} ON teams",
            visible="SELECT count(*) = 0 FROM pg_policy WHERE polrelid = 'teams'::regclass",
        ),
        lambda: _scalar("SELECT count(*) FROM pg_policy WHERE polrelid = 'teams'::regclass") == 1,
    ),
    "rls_disabled": (
        _ddl(
            "ALTER TABLE meetings DISABLE ROW LEVEL SECURITY",
            visible="SELECT NOT relrowsecurity FROM pg_class WHERE oid = 'meetings'::regclass",
        ),
        lambda: _rls_flags("meetings") == (True, True),
    ),
    "rls_not_forced": (
        _ddl(
            "ALTER TABLE teams NO FORCE ROW LEVEL SECURITY",
            visible="SELECT NOT relforcerowsecurity FROM pg_class WHERE oid = 'teams'::regclass",
        ),
        lambda: _rls_flags("teams") == (True, True),
    ),
    "rls_enabled_on_an_unprotected_table": (
        _ddl(
            "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
            visible="SELECT relrowsecurity FROM pg_class WHERE oid = 'users'::regclass",
        ),
        lambda: _rls_flags("users") == (False, False),
    ),
    "trigger_created": (
        _write_trigger_created,
        lambda: _user_triggers("users") == 0 and not _function_exists("hom292_trigger_fn"),
    ),
    "trigger_disabled": (
        _ddl(
            "ALTER TABLE memberships DISABLE TRIGGER ALL",
            visible="SELECT count(*) > 0 FROM pg_trigger WHERE tgrelid = 'memberships'::regclass "
            "AND tgenabled = 'D'",
        ),
        lambda: _disabled_triggers("memberships") == 0,
    ),
    "function_created": (
        _ddl(
            "CREATE FUNCTION hom292_fn() RETURNS int LANGUAGE sql AS 'SELECT 1'",
            visible="SELECT count(*) FROM pg_proc WHERE proname = 'hom292_fn'",
        ),
        lambda: not _function_exists("hom292_fn"),
    ),
    "view_created": (
        _ddl(
            "CREATE VIEW hom292_view AS SELECT id FROM users",
            visible="SELECT to_regclass('public.hom292_view') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regclass('public.hom292_view')") is None,
    ),
    "sequence_created": (
        _ddl(
            "CREATE SEQUENCE hom292_seq",
            visible="SELECT to_regclass('public.hom292_seq') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regclass('public.hom292_seq')") is None,
    ),
    "type_created": (
        _ddl(
            "CREATE TYPE hom292_mood AS ENUM ('calm')",
            visible="SELECT to_regtype('public.hom292_mood') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regtype('public.hom292_mood')") is None,
    ),
    "table_grant": (
        _ddl(
            f"GRANT TRUNCATE ON users TO {APP_ROLE}",
            visible=f"SELECT has_table_privilege('{APP_ROLE}', 'users', 'TRUNCATE')",
        ),
        lambda: not _scalar(f"SELECT has_table_privilege('{APP_ROLE}', 'users', 'TRUNCATE')"),
    ),
    "table_revoke": (
        _ddl(
            f"REVOKE SELECT ON meetings FROM {APP_ROLE}",
            visible=f"SELECT NOT has_table_privilege('{APP_ROLE}', 'meetings', 'SELECT')",
        ),
        lambda: _scalar(f"SELECT has_table_privilege('{APP_ROLE}', 'meetings', 'SELECT')"),
    ),
    "append_only_grant": (
        _ddl(
            f"GRANT UPDATE, DELETE ON audit_events TO {APP_ROLE}",
            visible=f"SELECT has_table_privilege('{APP_ROLE}', 'audit_events', 'DELETE')",
        ),
        lambda: not _scalar(f"SELECT has_table_privilege('{APP_ROLE}', 'audit_events', 'UPDATE')")
        and not _scalar(f"SELECT has_table_privilege('{APP_ROLE}', 'audit_events', 'DELETE')"),
    ),
    "schema_grant": (
        _ddl(
            f"GRANT CREATE ON SCHEMA public TO {APP_ROLE}",
            visible=f"SELECT has_schema_privilege('{APP_ROLE}', 'public', 'CREATE')",
        ),
        lambda: not _scalar(f"SELECT has_schema_privilege('{APP_ROLE}', 'public', 'CREATE')"),
    ),
    "default_privileges": (
        _write_default_privileges,
        lambda: _default_acl() == _before["default_privileges"],
    ),
    "owner_changed": (
        _write_owner_changed,
        lambda: _owner("meetings") == _before["owner_changed"],
    ),
    "rows_in_a_table_outside_the_models": (
        _ddl(
            "CREATE TABLE hom292_outside (id int PRIMARY KEY, note text)",
            "INSERT INTO hom292_outside VALUES (1, 'left behind')",
            visible="SELECT count(*) FROM hom292_outside",
        ),
        lambda: _scalar("SELECT to_regclass('public.hom292_outside')") is None,
    ),
    "schema_created": (
        _ddl(
            "CREATE SCHEMA hom292_extra",
            "CREATE TABLE hom292_extra.leak (id int)",
            "INSERT INTO hom292_extra.leak VALUES (1)",
            visible="SELECT count(*) FROM hom292_extra.leak",
        ),
        lambda: _scalar("SELECT to_regnamespace('hom292_extra')") is None,
    ),
    "materialized_view_created": (
        _ddl(
            "CREATE MATERIALIZED VIEW hom292_matview AS SELECT id FROM users",
            visible="SELECT to_regclass('public.hom292_matview') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regclass('public.hom292_matview')") is None,
    ),
    "domain_created": (
        _ddl(
            "CREATE DOMAIN hom292_positive AS int CHECK (VALUE > 0)",
            visible="SELECT to_regtype('public.hom292_positive') IS NOT NULL",
        ),
        lambda: _scalar("SELECT to_regtype('public.hom292_positive')") is None,
    ),
    "procedure_created": (
        _ddl(
            "CREATE PROCEDURE hom292_proc() LANGUAGE sql AS 'SELECT 1'",
            visible="SELECT count(*) FROM pg_proc WHERE proname = 'hom292_proc'",
        ),
        lambda: not _function_exists("hom292_proc"),
    ),
    "collation_created": (
        _ddl(
            'CREATE COLLATION hom292_collation FROM "C"',
            visible="SELECT count(*) FROM pg_collation WHERE collname = 'hom292_collation'",
        ),
        lambda: _scalar("SELECT count(*) FROM pg_collation WHERE collname = 'hom292_collation'")
        == 0,
    ),
    "event_trigger_created": (
        _ddl(
            "CREATE FUNCTION hom292_on_ddl() RETURNS event_trigger LANGUAGE plpgsql AS "
            "$$ BEGIN END $$",
            "CREATE EVENT TRIGGER hom292_on_ddl ON ddl_command_end EXECUTE FUNCTION "
            "hom292_on_ddl()",
            visible="SELECT count(*) FROM pg_event_trigger WHERE evtname = 'hom292_on_ddl'",
        ),
        lambda: _scalar("SELECT count(*) FROM pg_event_trigger") == 0
        and not _function_exists("hom292_on_ddl"),
    ),
}


@pytest.mark.postgres
def test_schema_drift_writer_changes_one_kind_at_a_time(client):
    # Each kind on its own changes the fingerprint: detection needs no marker.
    seen = [_isolation.current_fingerprint()]
    assert seen == [_isolation.canonical_fingerprint()]
    for kind, (write, _restored) in DRIFT.items():
        write()
        now = _isolation.current_fingerprint()
        assert now not in seen, f"the fingerprint does not change for {kind}"
        seen.append(now)
    _done("test_schema_drift_writer_changes_one_kind_at_a_time")


@pytest.mark.postgres
def test_schema_drift_check_the_next_test_has_the_canonical_schema(client, real_build_objects):
    _ran("test_schema_drift_writer_changes_one_kind_at_a_time")
    assert [kind for kind, (_write, restored) in DRIFT.items() if not restored()] == []
    _assert_canonical_schema(real_build_objects)


# --- rule 4: empty-schema and migration tests keep their semantics ---------------------------


@pytest.mark.postgres
def test_a_test_that_drops_the_schema_itself_sees_it_dropped(client, settings):
    reset_postgres_schema(settings.database_url)
    assert len(_missing_model_tables()) == len(Base.metadata.sorted_tables)
    _done("test_a_test_that_drops_the_schema_itself_sees_it_dropped")


@pytest.mark.postgres
def test_the_test_after_a_dropped_schema_gets_the_canonical_one(client, real_build_objects):
    _ran("test_a_test_that_drops_the_schema_itself_sees_it_dropped")
    _assert_canonical_schema(real_build_objects)
    assert {n for n, c in _row_counts().items() if c and n not in SEEDED_TABLES} == set()


@pytest.mark.postgres
def test_a_migration_stamp_is_visible_to_its_own_test(client, settings, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT, code_migration_head

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    try:
        command.stamp(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    finally:
        reset_settings_cache()
    assert _scalar("SELECT version_num FROM alembic_version") == code_migration_head()
    _done("test_a_migration_stamp_is_visible_to_its_own_test")


@pytest.mark.postgres
def test_the_test_after_a_migration_has_no_alembic_version(client, real_build_objects):
    _ran("test_a_migration_stamp_is_visible_to_its_own_test")
    assert _scalar("SELECT to_regclass('public.alembic_version')") is None
    _assert_canonical_schema(real_build_objects)
    _done("test_the_test_after_a_migration_has_no_alembic_version")


@pytest.mark.postgres
def test_settings_without_client_starts_without_tables(settings):
    # R3: the base start state, even right after a test on the canonical schema.
    _ran("test_the_test_after_a_migration_has_no_alembic_version")
    assert len(_missing_model_tables()) == len(Base.metadata.sorted_tables)
    assert _scalar("SELECT to_regclass('public.alembic_version')") is None
    _done("test_settings_without_client_starts_without_tables")


@pytest.mark.postgres
def test_the_client_test_after_it_gets_the_canonical_schema(client, real_build_objects):
    _ran("test_settings_without_client_starts_without_tables")
    _assert_canonical_schema(real_build_objects)


@pytest.mark.postgres
def test_a_direct_schema_call_runs_the_real_function(client, settings, monkeypatch):
    from backend.database import rls

    calls: list[object] = []
    real = rls.apply_row_level_security
    monkeypatch.setattr(
        rls,
        "apply_row_level_security",
        lambda connection, **kw: calls.append(1) or real(connection, **kw),
    )
    create_schema_for_test_or_development(settings)
    assert calls == [1]


@pytest.mark.postgres
def test_the_lifespan_rebuilds_a_schema_changed_after_the_fixture(client, settings):
    _admin("ALTER TABLE meetings DISABLE ROW LEVEL SECURITY")
    assert _rls_flags("meetings") == (False, True)
    with _SecondApp(settings):
        assert _rls_flags("meetings") == (True, True)


@pytest.mark.postgres
def test_the_lifespan_runs_the_real_function_for_another_url(client, settings, monkeypatch):
    from backend.database import rls

    calls: list[object] = []
    real = rls.apply_row_level_security
    monkeypatch.setattr(
        rls,
        "apply_row_level_security",
        lambda connection, **kw: calls.append(1) or real(connection, **kw),
    )
    other = settings.model_copy(
        update={"database_url": settings.database_url + "?application_name=hom292_other"}
    )
    with _SecondApp(other):
        pass
    assert calls == [1]


@pytest.mark.postgres
def test_the_lifespan_skips_the_rebuild_on_the_canonical_schema(client, settings, monkeypatch):
    from backend.database import rls

    calls: list[object] = []
    monkeypatch.setattr(rls, "apply_row_level_security", lambda connection, **kw: calls.append(1))
    with _SecondApp(settings):
        pass
    assert calls == []


@pytest.mark.postgres
def test_the_lifespan_keeps_the_environment_guard_outside_test_and_development(
    client, settings
):
    # On the canonical schema the skip is only for test and development; any other
    # environment reaches the real function, which refuses, as on the base.
    staging = settings.model_copy(update={"environment": "staging"})
    with pytest.raises(RuntimeError, match="forbidden outside test/development"):
        with _SecondApp(staging):
            pass


class _SecondApp:
    """A second app on the given settings, started and stopped inside a test."""

    def __init__(self, settings):
        from fastapi.testclient import TestClient

        self._client = TestClient(create_app(settings))

    def __enter__(self):
        return self._client.__enter__()

    def __exit__(self, *exc):
        return self._client.__exit__(*exc)


# --- rule 5: RLS stays on --------------------------------------------------------------------


@pytest.mark.postgres
def test_the_application_runs_as_the_app_role_under_forced_policies(client, real_build_objects):
    with get_engine().connect() as connection:
        role = connection.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = "
                "current_user"
            )
        ).one()
    assert tuple(role) == (APP_ROLE, False, False)
    _assert_canonical_schema(real_build_objects)


# --- rule 7: cluster-wide state ---------------------------------------------------------------


def _reader_state() -> dict[str, object]:
    with inspection_session_factory()() as db:
        row = db.execute(
            text(
                "SELECT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole "
                "AS privileged, rolcanlogin AS can_login, "
                "(SELECT count(*) FROM pg_auth_members m WHERE m.member = r.oid) AS member_of, "
                "pg_has_role(:app, r.rolname, 'MEMBER') AS app_reaches "
                "FROM pg_roles r WHERE r.rolname = :reader"
            ),
            {"app": APP_ROLE, "reader": AUDIT_READER_ROLE},
        ).one()
        return dict(row._mapping)


def _app_role_state() -> tuple:
    with inspection_session_factory()() as db:
        return tuple(
            db.execute(
                text(
                    "SELECT r.rolcreatedb, r.rolconnlimit, r.rolvaliduntil::text, "
                    "(SELECT count(*) FROM pg_db_role_setting s WHERE s.setrole = r.oid), "
                    "(SELECT count(*) FROM pg_db_role_setting s JOIN pg_database d "
                    " ON d.oid = s.setdatabase WHERE s.setrole = 0 AND d.datname = "
                    "current_database()) "
                    "FROM pg_roles r WHERE r.rolname = :app"
                ),
                {"app": APP_ROLE},
            ).one()
        )


@pytest.mark.postgres
def test_cluster_writer_makes_the_reader_role_reachable(client):
    _admin(
        f"ALTER ROLE {AUDIT_READER_ROLE} LOGIN CREATEDB",
        f"GRANT {AUDIT_READER_ROLE} TO {APP_ROLE}",
    )
    state = _reader_state()
    assert state["privileged"] and state["can_login"] and state["app_reaches"]
    _done("test_cluster_writer_makes_the_reader_role_reachable")


@pytest.mark.postgres
def test_cluster_check_the_reader_role_is_inert_again(client):
    _ran("test_cluster_writer_makes_the_reader_role_reachable")
    assert _reader_state() == {
        "privileged": False,
        "can_login": False,
        "member_of": 0,
        "app_reaches": False,
    }


@pytest.mark.postgres
def test_cluster_writer_changes_the_app_role_and_the_database(client):
    _before["app_role"] = _app_role_state()
    database = _scalar("SELECT current_database()")
    _admin(
        f"ALTER ROLE {APP_ROLE} CREATEDB CONNECTION LIMIT 7",
        f"ALTER ROLE {APP_ROLE} SET statement_timeout = '4321ms'",
        f"ALTER DATABASE \"{database}\" SET lock_timeout = '1234ms'",
    )
    assert _app_role_state() != _before["app_role"]
    _done("test_cluster_writer_changes_the_app_role_and_the_database")


@pytest.mark.postgres
def test_cluster_check_the_app_role_and_the_database_are_restored(client, real_build_objects):
    _ran("test_cluster_writer_changes_the_app_role_and_the_database")
    assert _app_role_state() == _before["app_role"]
    _assert_canonical_schema(real_build_objects)
