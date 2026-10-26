"""WP03-T05: PostgreSQL row-level security under realistic non-bypass roles.

The regular suite already runs every API and worker path as ``homayar_app``
(``SET ROLE`` on checkout).  This module proves the database layer on its own:

* a LOGIN role that is a member of ``homayar_app`` — not a superuser, not
  ``BYPASSRLS``, not the table owner — cannot read or write across tenants,
  sees nothing without a bound context, and cannot re-home rows;
* the context is transaction-scoped: it is gone after COMMIT and ROLLBACK and
  never survives pooled-connection reuse;
* the worker's job-scoped access reads exactly one job and cannot write until
  the tenant is bound;
* the table owner (a non-superuser) is subject to the policies because they are
  FORCED;
* the API works end to end when the application logs in directly as that
  low-privilege role (no ``SET ROLE``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from backend.config import Settings
from backend.database.relational import reset_database_state
from backend.database.rls import (
    APP_ROLE,
    RowLevelSecurityPostureError,
    assert_production_posture,
    describe_current_role,
)
from backend.database.saas_models import (
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    Workspace,
)
from backend.main import create_app
from backend.routers.v1 import members as members_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_job_processor import _queued_upload

pytestmark = pytest.mark.postgres

PROBE_ROLE = "homayar_rls_probe"
OWNER_PROBE_ROLE = "homayar_rls_owner_probe"
PROBE_PASSWORD = "rls-probe-only"  # noqa: S105 - throwaway role on a disposable test database


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _admin_execute(*statements: str) -> None:
    with inspection_session_factory()() as db:
        for statement in statements:
            db.execute(text(statement))
        db.commit()


def _role_url(settings: Settings, role: str) -> str:
    url = make_url(settings.database_url).set(username=role, password=PROBE_PASSWORD)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def probe_role(settings: Settings):
    """A LOGIN member of homayar_app with no superuser/BYPASSRLS/ownership."""
    _admin_execute(
        f"DROP ROLE IF EXISTS {PROBE_ROLE}",
        f"CREATE ROLE {PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}' "
        f"NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE IN ROLE {APP_ROLE}",
    )
    yield _role_url(settings, PROBE_ROLE)
    _admin_execute(f"DROP ROLE IF EXISTS {PROBE_ROLE}")


def _two_tenants(client, email_outbox) -> tuple[dict, dict]:
    tenants = []
    for label in ("one", "two"):
        credentials = register_user(
            client, email_outbox, email=f"rls-{label}@example.com", name=f"RLS {label}"
        )
        session = login_user(client, credentials)
        tenants.append(
            {
                "user_id": session["user"]["id"],
                "workspace_id": session["workspaces"][0]["id"],
                "organization_id": session["workspaces"][0]["organization_id"],
                "cookies": _cookies(client),
            }
        )
    with inspection_session_factory()() as db:
        for tenant in tenants:
            meeting = Meeting(
                workspace_id=tenant["workspace_id"],
                created_by_user_id=tenant["user_id"],
                title=f"meeting of {tenant['workspace_id']}",
                status=MeetingStatus.COMPLETED,
                duration_seconds=5,
            )
            db.add(meeting)
            db.flush()
            tenant["meeting_id"] = meeting.id
            revision = TranscriptRevision(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting.id,
                revision_number=1,
                source=TranscriptSource.ASR,
                reason="processing",
                language="fa",
                segment_count=1,
                is_current=True,
            )
            db.add(revision)
            db.flush()
            db.add(
                TranscriptSegment(
                    workspace_id=tenant["workspace_id"],
                    meeting_id=meeting.id,
                    revision_id=revision.id,
                    sequence=0,
                    speaker_key="S1",
                    start_ms=0,
                    end_ms=1000,
                    text="secret",
                    confidence=0.9,
                )
            )
        db.commit()
    return tenants[0], tenants[1]


def _bind(connection, **values: str) -> None:
    for key, value in values.items():
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": f"homayar.{key}", "value": value},
        )


def _count(connection, table: str) -> int:
    return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


def test_non_bypass_login_role_is_isolated_per_transaction(client, email_outbox, probe_role):
    one, two = _two_tenants(client, email_outbox)
    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            posture = describe_current_role(connection)
            assert posture == {
                "role": PROBE_ROLE,
                "superuser": False,
                "bypassrls": False,
                "rls_enabled": True,
                "rls_forced": True,
                "is_owner": False,
            }

            # No context: nothing is visible and nothing can be written.
            assert _count(connection, "meetings") == 0
            assert _count(connection, "transcript_segments") == 0
            assert _count(connection, "memberships") == 0
            assert _count(connection, "workspaces") == 0
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text(
                        "INSERT INTO meetings (id, workspace_id, created_by_user_id, title, "
                        "language, status, duration_seconds, source, created_at, updated_at) "
                        "VALUES ('probe-1', :ws, :user, 'x', 'fa', 'COMPLETED', 1, 'upload', "
                        "now(), now())"
                    ),
                    {"ws": one["workspace_id"], "user": one["user_id"]},
                )
            connection.rollback()

            # Tenant one: only its own rows, on every table.
            _bind(connection, workspace_id=one["workspace_id"])
            titles = connection.execute(text("SELECT title FROM meetings")).scalars().all()
            assert titles == [f"meeting of {one['workspace_id']}"]
            assert _count(connection, "transcript_segments") == 1
            assert (
                connection.execute(
                    text("SELECT count(*) FROM meetings WHERE id = :m"), {"m": two["meeting_id"]}
                ).scalar_one()
                == 0
            )
            # Cross-tenant writes touch nothing (the rows are invisible)...
            updated = connection.execute(
                text("UPDATE meetings SET title = 'pwned' WHERE workspace_id = :ws"),
                {"ws": two["workspace_id"]},
            )
            assert updated.rowcount == 0
            deleted = connection.execute(
                text("DELETE FROM transcript_segments WHERE workspace_id = :ws"),
                {"ws": two["workspace_id"]},
            )
            assert deleted.rowcount == 0
            connection.commit()

            # ...and inserting into, or re-homing a row to, another tenant is refused.
            _bind(connection, workspace_id=one["workspace_id"])
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text(
                        "INSERT INTO meetings (id, workspace_id, created_by_user_id, title, "
                        "language, status, duration_seconds, source, created_at, updated_at) "
                        "VALUES ('probe-2', :ws, :user, 'x', 'fa', 'COMPLETED', 1, 'upload', "
                        "now(), now())"
                    ),
                    {"ws": two["workspace_id"], "user": one["user_id"]},
                )
            connection.rollback()
            _bind(connection, workspace_id=one["workspace_id"])
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text("UPDATE meetings SET workspace_id = :other WHERE id = :m"),
                    {"other": two["workspace_id"], "m": one["meeting_id"]},
                )
            connection.rollback()

            # Same-tenant writes work.
            _bind(connection, workspace_id=one["workspace_id"])
            ok = connection.execute(
                text("UPDATE meetings SET title = 'renamed' WHERE id = :m"),
                {"m": one["meeting_id"]},
            )
            assert ok.rowcount == 1
            connection.commit()

            # The context died with the transaction: the same connection sees nothing.
            assert (
                connection.execute(
                    text("SELECT current_setting('homayar.workspace_id', true)")
                ).scalar_one()
                == ""
            )
            assert _count(connection, "meetings") == 0
            # ...also after a rollback.
            _bind(connection, workspace_id=one["workspace_id"])
            assert _count(connection, "meetings") == 1
            connection.rollback()
            assert _count(connection, "meetings") == 0
            connection.rollback()

        # Pool reuse: the next checkout gets the same physical connection and no context.
        with engine.connect() as connection:
            _bind(connection, workspace_id=two["workspace_id"])
            assert _count(connection, "meetings") == 1
            # leave the transaction open on purpose; the pool resets it on return
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT current_setting('homayar.workspace_id', true)")
                ).scalar_one()
                == ""
            )
            assert _count(connection, "meetings") == 0
            connection.rollback()

        # Bootstrap path: a user context reveals that user's memberships and
        # workspaces only, and the organizations behind them.
        with engine.connect() as connection:
            _bind(connection, user_id=one["user_id"])

            def ids(sql: str) -> list[str]:
                return connection.execute(text(sql)).scalars().all()

            assert ids("SELECT workspace_id FROM memberships") == [one["workspace_id"]]
            assert ids("SELECT id FROM workspaces") == [one["workspace_id"]]
            assert ids("SELECT id FROM organizations") == [one["organization_id"]]
            assert _count(connection, "meetings") == 0  # user context is not tenant context
            connection.rollback()
    finally:
        engine.dispose()


def test_job_context_reads_one_job_and_cannot_write_without_tenant(
    client, email_outbox, probe_role, monkeypatch
):
    one, _ = _two_tenants(client, email_outbox)
    # A real queued upload (third tenant) so the job carries its reservation.
    job_id, job_workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, job_id).state == JobState.QUEUED

    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            assert _count(connection, "processing_jobs") == 0
            _bind(connection, job_id=job_id)
            visible = connection.execute(text("SELECT id FROM processing_jobs")).scalars().all()
            assert visible == [job_id]
            assert _count(connection, "meetings") == 0
            # A worker that has not bound the tenant may read the job but not write it.
            # (a plain column: RUNNING would also trip the lease invariant, WP06-T04)
            update = text("UPDATE processing_jobs SET stage = 'probe' WHERE id = :j")
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(update, {"j": job_id})
            connection.rollback()
            # After binding the job's own workspace the write succeeds; another
            # tenant's context does not even see the job.
            _bind(connection, job_id=job_id, workspace_id=job_workspace_id)
            assert connection.execute(update, {"j": job_id}).rowcount == 1
            connection.rollback()
            _bind(connection, workspace_id=one["workspace_id"])
            assert _count(connection, "processing_jobs") == 0
            connection.rollback()
    finally:
        engine.dispose()


def test_production_posture_refuses_bypass_roles(client, probe_role):
    del client
    with inspection_session_factory()() as db:
        with pytest.raises(RowLevelSecurityPostureError, match="superuser|BYPASSRLS"):
            assert_production_posture(db.connection())
    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            posture = assert_production_posture(connection)
            assert posture["role"] == PROBE_ROLE and posture["rls_enabled"] is True
    finally:
        engine.dispose()


def test_share_context_exposes_only_the_shared_meeting(client, email_outbox, probe_role):
    """A presented share token is a ceiling of one meeting's content, read-only."""
    one, two = _two_tenants(client, email_outbox)
    owner_headers = {
        "X-Workspace-ID": one["workspace_id"],
        "X-CSRF-Token": one["cookies"][CSRF_COOKIE],
    }
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        json={"public_share_links_enabled": True},
        cookies=one["cookies"],
        headers=owner_headers,
    )
    assert enabled.status_code == 200, enabled.text
    created = client.post(
        f"/api/v1/meetings/{one['meeting_id']}/shares",
        json={"audience": "link", "allow_export": True},
        cookies=one["cookies"],
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    raw_token = created.json()["url"].rsplit("/", 1)[-1]
    from backend.security.tokens import token_digest

    digest = token_digest(f"share:{raw_token}", client.app.state.settings)

    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            _bind(connection, share_token_hash=digest)

            def ids(sql: str) -> list[str]:
                return connection.execute(text(sql)).scalars().all()

            assert ids("SELECT id FROM meetings") == [one["meeting_id"]]
            assert ids("SELECT meeting_id FROM transcript_segments") == [one["meeting_id"]]
            assert ids("SELECT id FROM workspaces") == [one["workspace_id"]]
            assert ids("SELECT user_id FROM memberships") == [one["user_id"]]  # issuer only
            assert _count(connection, "share_links") == 1
            # may record a workspace audit row, never read one (platform-level
            # rows without a workspace stay visible, as for any role)
            assert (
                connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE workspace_id IS NOT NULL")
                ).scalar_one()
                == 0
            )
            assert _count(connection, "processing_jobs") == 0
            assert _count(connection, "usage_events") == 0
            # read-only: the share context cannot write content or re-home it
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text("UPDATE meetings SET title = 'via share' WHERE id = :m"),
                    {"m": one["meeting_id"]},
                )
            connection.rollback()
            _bind(connection, share_token_hash=digest)
            assert (
                connection.execute(
                    text("SELECT count(*) FROM meetings WHERE id = :m"), {"m": two["meeting_id"]}
                ).scalar_one()
                == 0
            )
            connection.rollback()

        revoked = client.delete(
            f"/api/v1/meetings/{one['meeting_id']}/shares/{created.json()['id']}",
            cookies=one["cookies"],
            headers=owner_headers,
        )
        assert revoked.status_code == 204
        with engine.connect() as connection:
            _bind(connection, share_token_hash=digest)
            assert _count(connection, "meetings") == 0
            assert _count(connection, "transcript_segments") == 0
            connection.rollback()
    finally:
        engine.dispose()


def test_forced_policies_bind_a_non_superuser_table_owner(client, email_outbox, settings):
    one, _ = _two_tenants(client, email_outbox)
    _admin_execute(
        f"DROP ROLE IF EXISTS {OWNER_PROBE_ROLE}",
        # Member of homayar_app so it can read the tables the policies consult
        # (share_links); still a plain non-superuser owner of `meetings`.
        f"CREATE ROLE {OWNER_PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}' "
        f"NOSUPERUSER NOBYPASSRLS IN ROLE {APP_ROLE}",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {OWNER_PROBE_ROLE}",
        f"ALTER TABLE meetings OWNER TO {OWNER_PROBE_ROLE}",
    )
    engine = create_engine(_role_url(settings, OWNER_PROBE_ROLE), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            posture = describe_current_role(connection)
            assert posture["is_owner"] is True and posture["rls_forced"] is True
            assert _count(connection, "meetings") == 0  # owner, but FORCE applies
            _bind(connection, workspace_id=one["workspace_id"])
            assert _count(connection, "meetings") == 1
            connection.rollback()
    finally:
        engine.dispose()
        owner = make_url(settings.database_url).username
        _admin_execute(
            f"ALTER TABLE meetings OWNER TO {owner}",
            f"REVOKE ALL ON SCHEMA public FROM {OWNER_PROBE_ROLE}",
            f"DROP ROLE IF EXISTS {OWNER_PROBE_ROLE}",
        )


def test_api_works_when_logged_in_directly_as_the_low_privilege_role(
    client, settings, email_outbox, probe_role, monkeypatch
):
    """Deployment shape: DATABASE_URL logs in as a member of homayar_app, no SET ROLE."""
    del client  # schema and policies were created by the default app; reuse them
    reset_database_state()
    direct = settings.model_copy(
        update={"database_url": probe_role, "database_app_role": "", "auto_create_schema": False}
    )
    delivered: dict[str, str] = {}

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)
    RateLimiter.reset_memory()
    app = create_app(direct)
    with TestClient(app) as api:
        owner_credentials = register_user(api, email_outbox, email="direct-owner@example.com")
        owner_session = login_user(api, owner_credentials)
        owner_cookies = _cookies(api)
        owner_ws = owner_session["workspaces"][0]["id"]
        guest_credentials = register_user(api, email_outbox, email="direct-guest@example.com")
        guest_session = login_user(api, guest_credentials)
        guest_cookies = _cookies(api)
        guest_ws = guest_session["workspaces"][0]["id"]

        owner_headers = {"X-Workspace-ID": owner_ws, "X-CSRF-Token": owner_cookies[CSRF_COOKIE]}
        listed = api.get("/api/v1/meetings", cookies=owner_cookies, headers=owner_headers)
        assert listed.status_code == 200, listed.text
        team = api.post(
            "/api/v1/teams", json={"name": "Direct"}, cookies=owner_cookies, headers=owner_headers
        )
        assert team.status_code == 201, team.text

        invited = api.post(
            "/api/v1/members/invitations",
            json={"email": "direct-guest@example.com", "role": "viewer"},
            cookies=owner_cookies,
            headers=owner_headers,
        )
        assert invited.status_code == 201, invited.text
        accepted = api.post(
            "/api/v1/members/invitations/accept",
            json={"token": delivered["direct-guest@example.com"]},
            cookies=guest_cookies,
            headers={"X-CSRF-Token": guest_cookies[CSRF_COOKIE]},
        )
        assert accepted.status_code == 200, accepted.text
        me = api.get("/api/v1/auth/me", cookies=guest_cookies)
        assert me.status_code == 200
        assert {w["id"] for w in me.json()["workspaces"]} == {owner_ws, guest_ws}

        with inspection_session_factory()() as db:
            meeting = Meeting(
                workspace_id=owner_ws,
                created_by_user_id=owner_session["user"]["id"],
                title="Direct meeting",
                status=MeetingStatus.COMPLETED,
                duration_seconds=5,
            )
            db.add(meeting)
            db.commit()
            meeting_id = meeting.id
            assert db.get(Workspace, owner_ws) is not None

        path = f"/api/v1/meetings/{meeting_id}"
        as_member = api.get(path, cookies=guest_cookies, headers={"X-Workspace-ID": owner_ws})
        assert as_member.status_code == 200, as_member.text
        from_own_ws = api.get(path, cookies=guest_cookies, headers={"X-Workspace-ID": guest_ws})
        assert from_own_ws.status_code == 404

        enabled = api.patch(
            "/api/v1/admin/workspace-settings",
            json={"public_share_links_enabled": True},
            cookies=owner_cookies,
            headers=owner_headers,
        )
        assert enabled.status_code == 200, enabled.text
        share = api.post(
            f"/api/v1/meetings/{meeting_id}/shares",
            json={"expires_in_hours": 1, "require_authentication": False},
            cookies=owner_cookies,
            headers=owner_headers,
        )
        assert share.status_code == 201, share.text
        token = share.json()["url"].rsplit("/", 1)[-1]
        api.cookies.clear()
        public = api.get(f"/api/v1/shares/{token}")
        assert public.status_code == 200, public.text
        assert public.json()["title"] == "Direct meeting"
        assert api.get(f"/api/v1/shares/{'x' * 40}").status_code == 404
    reset_database_state()


def test_channel_role_mappings_stay_inside_their_workspace(client, email_outbox, probe_role):
    """HOM-37: the trusted mapping lives in workspaces.settings_json; under RLS
    another tenant can neither read nor overwrite it (criterion 8)."""
    one, two = _two_tenants(client, email_outbox)
    mapping = {
        "channel_role_mappings": {
            "file_upload:console": {"source_type": "file_upload", "version": 1, "active": True}
        }
    }
    with inspection_session_factory()() as db:
        db.get(Workspace, one["workspace_id"]).settings_json = mapping
        db.commit()

    def settings_of(connection, workspace_id: str) -> list:
        return (
            connection.execute(
                text("SELECT settings_json FROM workspaces WHERE id = :ws"), {"ws": workspace_id}
            )
            .scalars()
            .all()
        )

    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            # Allow-control: tenant one reads its own mapping.
            _bind(connection, workspace_id=one["workspace_id"])
            assert settings_of(connection, one["workspace_id"]) == [mapping]
            connection.rollback()

            # Tenant two sees no row of tenant one and its write changes nothing.
            _bind(connection, workspace_id=two["workspace_id"])
            assert settings_of(connection, one["workspace_id"]) == []
            overwritten = connection.execute(
                text("UPDATE workspaces SET settings_json = CAST(:value AS json) WHERE id = :ws"),
                {"value": '{"channel_role_mappings": {}}', "ws": one["workspace_id"]},
            )
            assert overwritten.rowcount == 0
            connection.commit()
    finally:
        engine.dispose()
    with inspection_session_factory()() as db:
        assert db.get(Workspace, one["workspace_id"]).settings_json == mapping
