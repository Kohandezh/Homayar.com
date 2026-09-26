from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.config import Settings, get_settings
from backend.database.relational import (
    configure_database,
    create_schema_for_test_or_development,
    reset_database_state,
)
from backend.database.saas_models import AuditEvent, Membership, User, WorkspaceInvitation
from backend.routers.v1 import auth as auth_router
from backend.routers.v1 import members as members_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.rate_limit_service import RateLimiter
from backend.services.tenancy_service import ensure_plan_catalog
from backend.tests._database import (
    TEST_APP_ROLE,
    inspection_session_factory,
    test_database_url,
)
from backend.tests._transport import APP_REDIS_URL


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        deployment_mode="cloud",
        database_url=test_database_url(tmp_path, name="members"),
        database_app_role=TEST_APP_ROLE,
        redis_url=APP_REDIS_URL,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        auto_create_schema=True,
        tasks_eager=True,
        secure_cookies=False,
        enable_free_demo=True,
        demo_quota_seconds=1800,
        enable_phone_auth=True,
        asanak_api_key="test-only-key",
        asanak_sender="TEST",
        enable_google_auth=False,
        trusted_hosts="testserver,localhost,127.0.0.1",
        frontend_url="http://testserver/app",
        public_url="http://testserver",
        _env_file=None,
    )


@pytest.fixture
def email_outbox() -> list[dict[str, str]]:
    return []


@pytest.fixture
def client(
    settings: Settings,
    email_outbox: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    async def capture_verification(user, token, _settings):
        email_outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    async def capture_reset(user, token, _settings):
        email_outbox.append({"kind": "reset", "user_id": user.id, "token": token})

    monkeypatch.setattr(auth_router, "_send_verification_message", capture_verification)
    monkeypatch.setattr(auth_router, "_send_reset_message", capture_reset)
    RateLimiter.reset_memory()
    configure_database(settings)
    create_schema_for_test_or_development(settings)
    factory = inspection_session_factory()
    with factory() as db:
        ensure_plan_catalog(db, settings)
        db.commit()

    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(auth_router.router, prefix=settings.api_prefix)
    app.include_router(members_router.router, prefix=settings.api_prefix)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": str(detail.get("code", "http_error")),
                    "message": str(detail.get("message", exc.detail)),
                    "request_id": None,
                }
            },
        )

    with TestClient(app) as test_client:
        yield test_client
    reset_database_state()


def register_user(
    client: TestClient,
    email_outbox: list[dict[str, str]],
    *,
    email: str,
    name: str,
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "SecurePass123",
            "display_name": name,
            "locale": "fa",
        },
    )
    assert response.status_code == 202, response.text
    verified = client.post(
        "/api/v1/auth/verify-email",
        json={"token": email_outbox[-1]["token"]},
    )
    assert verified.status_code == 200, verified.text
    return {"email": email, "password": "SecurePass123"}


def login_user(client: TestClient, credentials: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": credentials["email"], "password": credentials["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _mount_router(client, settings) -> None:
    assert any(route.path == "/api/v1/members" for route in client.app.routes)


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _register_login(client, email_outbox, *, email: str, name: str):
    credentials = register_user(
        client,
        email_outbox,
        email=email,
        name=name,
    )
    session = login_user(client, credentials)
    return session, _cookies(client)


def _write_headers(workspace_id: str, cookies: dict[str, str]) -> dict[str, str]:
    return {
        "X-Workspace-ID": workspace_id,
        "X-CSRF-Token": cookies[CSRF_COOKIE],
    }


def test_invitation_acceptance_is_scoped_hashed_and_audited(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    _mount_router(client, settings)
    owner_session, owner_cookies = _register_login(
        client,
        email_outbox,
        email="members-owner@example.com",
        name="Members Owner",
    )
    invitee_session, invitee_cookies = _register_login(
        client,
        email_outbox,
        email="members-invitee@example.com",
        name="Invited Member",
    )
    owner_workspace_id = owner_session["workspaces"][0]["id"]
    invitee_workspace_id = invitee_session["workspaces"][0]["id"]
    delivered: dict[str, str] = {}

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        assert workspace_name == "Members Owner"
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)

    created = client.post(
        "/api/v1/members/invitations",
        json={"email": "MEMBERS-INVITEE@example.com", "role": "member"},
        cookies=owner_cookies,
        headers=_write_headers(owner_workspace_id, owner_cookies),
    )
    assert created.status_code == 201, created.text
    assert created.json()["email"] == "members-invitee@example.com"
    assert created.json()["status"] == "pending"
    raw_token = delivered["members-invitee@example.com"]
    assert raw_token not in created.text
    invitation_id = created.json()["id"]

    listed = client.get(
        "/api/v1/members/invitations",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert "token" not in listed.text.casefold()

    factory = inspection_session_factory()
    with factory() as db:
        invitation = db.get(WorkspaceInvitation, invitation_id)
        assert invitation is not None
        assert invitation.token_hash != raw_token
        assert raw_token not in invitation.token_hash

    cross_tenant_revoke = client.delete(
        f"/api/v1/members/invitations/{invitation_id}",
        cookies=invitee_cookies,
        headers=_write_headers(invitee_workspace_id, invitee_cookies),
    )
    assert cross_tenant_revoke.status_code == 404

    accepted = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": raw_token},
        cookies=invitee_cookies,
        headers={"X-CSRF-Token": invitee_cookies[CSRF_COOKIE]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["workspace_id"] == owner_workspace_id
    assert accepted.json()["membership"]["role"] == "member"
    assert raw_token not in accepted.text

    replay = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": raw_token},
        cookies=invitee_cookies,
        headers={"X-CSRF-Token": invitee_cookies[CSRF_COOKIE]},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "invitation_already_accepted"

    members = client.get(
        "/api/v1/members",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert members.status_code == 200
    assert members.json()["total"] == 2

    with factory() as db:
        audits = list(
            db.scalars(select(AuditEvent).where(AuditEvent.workspace_id == owner_workspace_id))
        )
        event_types = {event.event_type for event in audits}
        assert "member.invitation.created" in event_types
        assert "member.invitation.accepted" in event_types
        assert all(raw_token not in str(event.details) for event in audits)


def test_member_roles_removal_csrf_and_hierarchy(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    _mount_router(client, settings)
    owner_session, owner_cookies = _register_login(
        client,
        email_outbox,
        email="role-owner@example.com",
        name="Role Owner",
    )
    member_session, member_cookies = _register_login(
        client,
        email_outbox,
        email="role-member@example.com",
        name="Role Member",
    )
    owner_workspace_id = owner_session["workspaces"][0]["id"]
    member_workspace_id = member_session["workspaces"][0]["id"]
    delivered: dict[str, str] = {}

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)
    invitation = client.post(
        "/api/v1/members/invitations",
        json={"email": "role-member@example.com", "role": "member"},
        cookies=owner_cookies,
        headers=_write_headers(owner_workspace_id, owner_cookies),
    )
    assert invitation.status_code == 201
    accepted = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": delivered["role-member@example.com"]},
        cookies=member_cookies,
        headers={"X-CSRF-Token": member_cookies[CSRF_COOKIE]},
    )
    assert accepted.status_code == 200
    member_id = accepted.json()["membership"]["id"]

    listed = client.get(
        "/api/v1/members",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    ).json()["items"]
    owner_id = next(item["id"] for item in listed if item["role"] == "owner")

    no_csrf = client.patch(
        f"/api/v1/members/{member_id}",
        json={"role": "admin"},
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert no_csrf.status_code == 403

    promoted = client.patch(
        f"/api/v1/members/{member_id}",
        json={"role": "admin"},
        cookies=owner_cookies,
        headers=_write_headers(owner_workspace_id, owner_cookies),
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    admin_cannot_change_owner = client.patch(
        f"/api/v1/members/{owner_id}",
        json={"role": "member"},
        cookies=member_cookies,
        headers=_write_headers(owner_workspace_id, member_cookies),
    )
    assert admin_cannot_change_owner.status_code == 403

    admin_cannot_invite_admin = client.post(
        "/api/v1/members/invitations",
        json={"email": "another-admin@example.com", "role": "admin"},
        cookies=member_cookies,
        headers=_write_headers(owner_workspace_id, member_cookies),
    )
    assert admin_cannot_invite_admin.status_code == 403

    cross_tenant_member = client.patch(
        f"/api/v1/members/{member_id}",
        json={"role": "viewer"},
        cookies=member_cookies,
        headers=_write_headers(member_workspace_id, member_cookies),
    )
    assert cross_tenant_member.status_code == 404

    removed = client.delete(
        f"/api/v1/members/{member_id}",
        cookies=owner_cookies,
        headers=_write_headers(owner_workspace_id, owner_cookies),
    )
    assert removed.status_code == 204
    removed_again = client.delete(
        f"/api/v1/members/{member_id}",
        cookies=owner_cookies,
        headers=_write_headers(owner_workspace_id, owner_cookies),
    )
    assert removed_again.status_code == 404

    former_admin_access = client.get(
        "/api/v1/members",
        cookies=member_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert former_admin_access.status_code == 403


def test_revoke_expiration_and_identity_mismatch_are_safe(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    _mount_router(client, settings)
    owner_session, owner_cookies = _register_login(
        client,
        email_outbox,
        email="invite-owner@example.com",
        name="Invite Owner",
    )
    target_session, target_cookies = _register_login(
        client,
        email_outbox,
        email="invite-target@example.com",
        name="Invite Target",
    )
    _, wrong_cookies = _register_login(
        client,
        email_outbox,
        email="invite-wrong@example.com",
        name="Wrong Target",
    )
    workspace_id = owner_session["workspaces"][0]["id"]
    delivered: list[str] = []

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered.append(raw_token)
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)

    first = client.post(
        "/api/v1/members/invitations",
        json={"email": "invite-target@example.com", "role": "viewer"},
        cookies=owner_cookies,
        headers=_write_headers(workspace_id, owner_cookies),
    )
    assert first.status_code == 201
    first_id = first.json()["id"]
    first_token = delivered[-1]

    mismatch = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": first_token},
        cookies=wrong_cookies,
        headers={"X-CSRF-Token": wrong_cookies[CSRF_COOKIE]},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "invitation_identity_mismatch"

    without_csrf = client.delete(
        f"/api/v1/members/invitations/{first_id}",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert without_csrf.status_code == 403
    revoked = client.delete(
        f"/api/v1/members/invitations/{first_id}",
        cookies=owner_cookies,
        headers=_write_headers(workspace_id, owner_cookies),
    )
    assert revoked.status_code == 204
    revoke_replay = client.delete(
        f"/api/v1/members/invitations/{first_id}",
        cookies=owner_cookies,
        headers=_write_headers(workspace_id, owner_cookies),
    )
    assert revoke_replay.status_code == 204

    revoked_accept = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": first_token},
        cookies=target_cookies,
        headers={"X-CSRF-Token": target_cookies[CSRF_COOKIE]},
    )
    assert revoked_accept.status_code == 410
    assert revoked_accept.json()["error"]["code"] == "invitation_revoked"

    expired = client.post(
        "/api/v1/members/invitations",
        json={"email": "invite-target@example.com", "role": "viewer"},
        cookies=owner_cookies,
        headers=_write_headers(workspace_id, owner_cookies),
    )
    assert expired.status_code == 201
    expired_id = expired.json()["id"]
    expired_token = delivered[-1]
    factory = inspection_session_factory()
    with factory() as db:
        invitation = db.get(WorkspaceInvitation, expired_id)
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    expired_accept = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": expired_token},
        cookies=target_cookies,
        headers={"X-CSRF-Token": target_cookies[CSRF_COOKIE]},
    )
    assert expired_accept.status_code == 410
    assert expired_accept.json()["error"]["code"] == "invitation_expired"

    with factory() as db:
        target = db.scalar(select(User).where(User.email == "invite-target@example.com"))
        assert target is not None
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == target.id,
            )
        )
        assert membership is None
