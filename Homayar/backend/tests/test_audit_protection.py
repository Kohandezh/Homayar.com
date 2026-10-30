"""Append-only audit events and the sink reader role (HOM-115, ADR 0036, owner option B).

* The ORM refuses to change or delete an ``AuditEvent`` (every database).
* On PostgreSQL the application role holds no UPDATE/DELETE on ``audit_events``,
  so raw SQL from the application cannot rewrite history either; INSERT and
  SELECT still work, and deleting a workspace or a user still nulls the rows'
  reference (``ON DELETE SET NULL`` runs as the table owner).
* ``homayar_audit_reader`` reads every tenant's rows and cannot write; an
  application login cannot ``SET ROLE`` to it; a tenant session still sees only
  its own workspace's (and platform) rows.
* The migration upgrades, downgrades and upgrades again cleanly.
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from backend.config import Settings
from backend.database.rls import APP_ROLE, AUDIT_READER_POLICY, AUDIT_READER_ROLE
from backend.database.saas_models import AuditEvent, User, Workspace
from backend.services.audit_event_types import lookup_audit_event_type
from backend.services.audit_service import AuditImmutableError, record_audit_event
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user

APP_LOGIN = "homayar_audit_app_probe"
READER_LOGIN = "homayar_audit_reader_probe"
HOP_ROLE = "homayar_audit_reader_hop_probe"
HEAD_REVISION = "47676f445509"  # HOM-54: scorecard_versions, after HOM-290
PREVIOUS_REVISION = "d8a2f4c6b1e3"


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL roles and privileges")


def _admin(*statements: str) -> None:
    with inspection_session_factory()() as db:
        for statement in statements:
            db.execute(text(statement))
        db.commit()


def _login_url(settings: Settings, role: str, password: str) -> str:
    url = make_url(settings.database_url).set(username=role, password=password)
    return url.render_as_string(hide_password=False)


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"audit-protect-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _seed(workspace_id: str | None, event_type: str, actor_user_id: str | None = None) -> str:
    """One event of a registered type, with that type's registered target type."""
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context

    registered = lookup_audit_event_type(event_type)
    assert registered is not None, event_type
    with get_session_factory()() as db:
        if workspace_id:
            bind_tenant_context(db, workspace_id=workspace_id)
        event = record_audit_event(
            db,
            event_type=registered,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            target_type=registered.target_type,
            target_id="audit-protection-target" if registered.target_type else None,
        )
        db.commit()
        return event.id


# --- ORM guard (every database) ----------------------------------------------------------


def test_the_orm_refuses_to_change_or_delete_an_audit_event(client, email_outbox):
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context

    tenant = _tenant(client, email_outbox, "orm")
    event_id = _seed(tenant["workspace_id"], "workspace.settings.updated")
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        event = db.get(AuditEvent, event_id)
        event.outcome = "rewritten"
        with pytest.raises(AuditImmutableError, match="update refused"):
            db.flush()
        db.rollback()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        db.delete(db.get(AuditEvent, event_id))
        with pytest.raises(AuditImmutableError, match="delete refused"):
            db.flush()
        db.rollback()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        with pytest.raises(AuditImmutableError, match="bulk"):
            db.execute(update(AuditEvent).values(outcome="rewritten"))
        db.rollback()
        with pytest.raises(AuditImmutableError, match="bulk"):
            db.execute(delete(AuditEvent).where(AuditEvent.id == event_id))
        db.rollback()
    with inspection_session_factory()() as db:
        assert db.get(AuditEvent, event_id).outcome == "success"


def test_inserting_and_reading_audit_events_still_works(client, email_outbox):
    tenant = _tenant(client, email_outbox, "insert")
    event_id = _seed(tenant["workspace_id"], "team.created")
    platform_id = _seed(None, "auth.logout")
    with inspection_session_factory()() as db:
        assert db.get(AuditEvent, event_id) is not None
        assert db.get(AuditEvent, platform_id).workspace_id is None


# --- database privileges (PostgreSQL) ---------------------------------------------------------


@pytest.mark.postgres
def test_the_application_role_cannot_update_or_delete_audit_rows(client, email_outbox):
    _requires_postgres()
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context

    tenant = _tenant(client, email_outbox, "grants")
    event_id = _seed(tenant["workspace_id"], "member.role_changed")
    for statement in (
        "UPDATE audit_events SET outcome = 'rewritten'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        with get_session_factory()() as db:  # application engine: SET ROLE homayar_app
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()
    with inspection_session_factory()() as db:
        privileges = {
            privilege: db.execute(
                text("SELECT has_table_privilege(:role, 'audit_events', :privilege)"),
                {"role": APP_ROLE, "privilege": privilege},
            ).scalar_one()
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
        assert privileges == {
            "SELECT": True,
            "INSERT": True,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
        }
        assert db.get(AuditEvent, event_id).outcome == "success"


@pytest.mark.postgres
def test_deleting_a_workspace_or_a_user_still_nulls_their_audit_rows(client, email_outbox):
    _requires_postgres()
    tenant = _tenant(client, email_outbox, "cascade")
    event_id = _seed(tenant["workspace_id"], "workspace.settings.updated", tenant["user_id"])
    with inspection_session_factory()() as db:
        # Removals by the owner (DBA here): the FK action runs as the table owner,
        # whatever the deleting role may do on audit_events itself.
        db.execute(delete(Workspace).where(Workspace.id == tenant["workspace_id"]))
        db.commit()
        assert db.get(AuditEvent, event_id).workspace_id is None
        db.execute(delete(User).where(User.id == tenant["user_id"]))
        db.commit()
        db.expire_all()
        row = db.get(AuditEvent, event_id)
        assert (row.workspace_id, row.actor_user_id, row.event_type) == (
            None,
            None,
            "workspace.settings.updated",
        )


@pytest.fixture
def audit_logins(settings: Settings):
    """Two non-superuser logins: one in homayar_app, one in homayar_audit_reader."""
    _requires_postgres()
    password = secrets.token_urlsafe(18)  # throwaway roles on a disposable test database
    _admin(
        f"DROP ROLE IF EXISTS {APP_LOGIN}",
        f"DROP ROLE IF EXISTS {READER_LOGIN}",
        f"CREATE ROLE {APP_LOGIN} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS "
        f"NOCREATEDB NOCREATEROLE IN ROLE {APP_ROLE}",
        f"CREATE ROLE {READER_LOGIN} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS "
        f"NOCREATEDB NOCREATEROLE IN ROLE {AUDIT_READER_ROLE}",
    )
    yield {
        "app": _login_url(settings, APP_LOGIN, password),
        "reader": _login_url(settings, READER_LOGIN, password),
    }
    _admin(f"DROP ROLE IF EXISTS {APP_LOGIN}", f"DROP ROLE IF EXISTS {READER_LOGIN}")


@pytest.mark.postgres
def test_the_reader_reads_every_tenant_and_cannot_write(client, email_outbox, audit_logins):
    one = _tenant(client, email_outbox, "reader-one")
    two = _tenant(client, email_outbox, "reader-two")
    ids = {_seed(one["workspace_id"], "team.created"), _seed(two["workspace_id"], "team.created")}
    engine = create_engine(audit_logins["reader"])
    try:
        with engine.connect() as connection:
            connection.execute(text(f"SET ROLE {AUDIT_READER_ROLE}"))
            seen = set(connection.execute(text("SELECT id FROM audit_events")).scalars())
            assert ids <= seen
            for statement in (
                "UPDATE audit_events SET outcome = 'rewritten'",
                "DELETE FROM audit_events",
                "TRUNCATE audit_events",
                "INSERT INTO audit_events (id, event_type, outcome, details, occurred_at) "
                "VALUES ('forged', 'forged.event', 'success', '{}', now())",
                "SELECT count(*) FROM workspaces",
                "SELECT count(*) FROM users",
            ):
                with pytest.raises(DBAPIError, match="permission denied"):
                    with connection.begin_nested():
                        connection.execute(text(statement))
    finally:
        engine.dispose()
    with inspection_session_factory()() as db:
        assert db.get(AuditEvent, "forged") is None
        assert {db.get(AuditEvent, event_id).outcome for event_id in ids} == {"success"}


@pytest.mark.postgres
def test_an_application_login_cannot_become_the_reader(client, email_outbox, audit_logins):
    one = _tenant(client, email_outbox, "app-one")
    two = _tenant(client, email_outbox, "app-two")
    own = _seed(one["workspace_id"], "team.created")
    foreign = _seed(two["workspace_id"], "team.created")
    engine = create_engine(audit_logins["app"])
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="permission denied to set role"):
                with connection.begin_nested():
                    connection.execute(text(f"SET ROLE {AUDIT_READER_ROLE}"))
            connection.execute(text(f"SET ROLE {APP_ROLE}"))
            connection.execute(
                text("SELECT set_config('homayar.workspace_id', :w, false)"),
                {"w": one["workspace_id"]},
            )
            visible = set(connection.execute(text("SELECT id FROM audit_events")).scalars())
            assert own in visible and foreign not in visible
            # a GUC any session can set does not unlock other tenants either
            connection.execute(
                text("SELECT set_config('homayar.service_scope', 'audit_export', false)")
            )
            assert foreign not in set(
                connection.execute(text("SELECT id FROM audit_events")).scalars()
            )
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_reader_role_is_inert(client):
    _requires_postgres()
    with inspection_session_factory()() as db:
        role = db.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": AUDIT_READER_ROLE},
        ).one()
        assert tuple(role) == (False, False, False, False, False)
        assert not db.execute(
            text("SELECT pg_has_role(:app, :reader, 'MEMBER')"),
            {"app": APP_ROLE, "reader": AUDIT_READER_ROLE},
        ).scalar_one()
        tables = set(
            db.execute(
                text(
                    "SELECT table_name FROM information_schema.role_table_grants "
                    "WHERE grantee = :role"
                ),
                {"role": AUDIT_READER_ROLE},
            ).scalars()
        )
        assert tables == {"audit_events"}
        policies = {
            row.policyname: (row.cmd, tuple(row.roles))
            for row in db.execute(
                text(
                    "SELECT policyname, cmd, roles FROM pg_policies "
                    "WHERE tablename = 'audit_events'"
                )
            )
        }
        assert policies == {
            "homayar_tenant_isolation": ("ALL", ("public",)),
            AUDIT_READER_POLICY: ("SELECT", (AUDIT_READER_ROLE,)),
        }


# --- migration round trip ----------------------------------------------------------------------


def _audit_privileges() -> tuple[bool, bool, bool]:
    with inspection_session_factory()() as db:
        return (
            db.execute(
                text("SELECT has_table_privilege(:r, 'audit_events', 'UPDATE')"), {"r": APP_ROLE}
            ).scalar_one(),
            db.execute(
                text("SELECT has_table_privilege(:r, 'audit_events', 'DELETE')"), {"r": APP_ROLE}
            ).scalar_one(),
            bool(
                db.execute(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE tablename = 'audit_events' "
                        "AND policyname = :p"
                    ),
                    {"p": AUDIT_READER_POLICY},
                ).scalar_one()
            ),
        )


@pytest.mark.postgres
def test_migration_downgrade_and_upgrade_round_trip(client, monkeypatch, settings: Settings):
    _requires_postgres()
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT, code_migration_head

    assert code_migration_head() == HEAD_REVISION
    assert _audit_privileges() == (False, False, True)  # create_all + RLS = the head state
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    try:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.stamp(config, "head")
        command.downgrade(config, PREVIOUS_REVISION)
        assert _audit_privileges() == (True, True, False)
        command.upgrade(config, "head")
        assert _audit_privileges() == (False, False, True)
        with inspection_session_factory()() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == HEAD_REVISION
    finally:
        reset_settings_cache()


# --- a pre-existing reader role is made inert, or refused ------------------------------------


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


INERT = {"privileged": False, "can_login": False, "member_of": 0, "app_reaches": False}


def _apply_as_owner() -> None:
    from backend.database.rls import apply_row_level_security

    with inspection_session_factory()() as db:
        try:
            apply_row_level_security(db.connection())
        except Exception:
            db.rollback()
            raise
        db.commit()


@pytest.fixture
def restore_reader():
    """The reader role is cluster-wide: leave it inert whatever the test did to it."""
    yield
    _admin(f"DROP ROLE IF EXISTS {HOP_ROLE}")
    _apply_as_owner()
    assert _reader_state() == INERT


@pytest.mark.postgres
def test_the_migration_makes_a_pre_created_reader_inert(
    client, monkeypatch, settings: Settings, restore_reader
):
    _requires_postgres()
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    try:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.stamp(config, "head")
        command.downgrade(config, PREVIOUS_REVISION)
        # A DBA pre-created the role as a login inside the application role (the role may
        # also survive the downgrade, when another database on the cluster still uses it).
        password = secrets.token_urlsafe(18)  # throwaway; the role ends up NOLOGIN
        with inspection_session_factory()() as db:
            exists = db.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": AUDIT_READER_ROLE}
            ).first()
        if exists:
            _admin(
                f"ALTER ROLE {AUDIT_READER_ROLE} LOGIN PASSWORD '{password}' CREATEDB CREATEROLE",
                f"GRANT {APP_ROLE} TO {AUDIT_READER_ROLE}",
            )
        else:
            _admin(
                f"CREATE ROLE {AUDIT_READER_ROLE} LOGIN PASSWORD '{password}' CREATEDB "
                f"CREATEROLE IN ROLE {APP_ROLE}"
            )
        assert _reader_state() == {
            "privileged": True,
            "can_login": True,
            "member_of": 1,
            "app_reaches": False,
        }
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()
    assert _reader_state() == INERT
    with inspection_session_factory()() as db:
        assert not db.execute(
            text("SELECT pg_has_role(:reader, :app, 'MEMBER')"),
            {"reader": AUDIT_READER_ROLE, "app": APP_ROLE},
        ).scalar_one()


@pytest.mark.postgres
def test_the_application_role_never_reaches_the_reader(client, restore_reader):
    _requires_postgres()
    _admin(f"GRANT {AUDIT_READER_ROLE} TO {APP_ROLE}")
    assert _reader_state()["app_reaches"] is True
    _apply_as_owner()  # a direct membership is revoked
    assert _reader_state() == INERT

    # An indirect path cannot be cut safely here: the upgrade refuses instead.
    _admin(
        f"DROP ROLE IF EXISTS {HOP_ROLE}",
        f"CREATE ROLE {HOP_ROLE} NOLOGIN",
        f"GRANT {AUDIT_READER_ROLE} TO {HOP_ROLE}",
        f"GRANT {HOP_ROLE} TO {APP_ROLE}",
    )
    with pytest.raises(DBAPIError, match="homayar_audit_reader is not inert"):
        _apply_as_owner()
