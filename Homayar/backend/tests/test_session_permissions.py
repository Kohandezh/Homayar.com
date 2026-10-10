"""HOM-184: the session payload says what the signed-in user may do.

``user.system_role`` is the stored ``User.system_role``. Each listed workspace
carries ``permissions``: the sorted string values of ``ROLE_PERMISSIONS`` for
that membership role. Both are read from the database on every call, so a
changed role shows on the next request with the same session cookie. They only
drive what the console shows; the API still enforces every permission.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.database.saas_models import Membership, User, WorkspaceRole
from backend.security.authorization import ROLE_PERMISSIONS, Permission
from backend.security.context import CSRF_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _workspace_with_roles

ALL_PERMISSION_VALUES = frozenset(permission.value for permission in Permission)


def _expected(role: WorkspaceRole) -> list[str]:
    return sorted(permission.value for permission in ROLE_PERMISSIONS[role])


def _me(client, cookies: dict[str, str] | None = None) -> dict:
    response = client.get("/api/v1/auth/me", cookies=cookies)
    assert response.status_code == 200, response.text
    return response.json()


def _workspace(session: dict, workspace_id: str) -> dict:
    return next(item for item in session["workspaces"] if item["id"] == workspace_id)


def _assert_permissions_match_roles(session: dict) -> None:
    assert session["workspaces"]
    for workspace in session["workspaces"]:
        permissions = workspace["permissions"]
        assert permissions == _expected(WorkspaceRole(workspace["role"])), workspace
        assert permissions == sorted(set(permissions))
        assert set(permissions) <= ALL_PERMISSION_VALUES
        assert not any(value.startswith("organization:") for value in permissions)


def _set_role(workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    with inspection_session_factory()() as db:
        membership = db.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        ).scalar_one()
        membership.role = role
        db.commit()


def _set_system_role(user_id: str, system_role: str) -> None:
    with inspection_session_factory()() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.system_role = system_role
        db.commit()


def test_role_permission_lists_are_the_authorization_map():
    owner = _expected(WorkspaceRole.OWNER)
    assert owner == sorted(ALL_PERMISSION_VALUES) and len(owner) == 17
    assert len(_expected(WorkspaceRole.ADMIN)) == 13
    assert len(_expected(WorkspaceRole.MEMBER)) == 6
    assert _expected(WorkspaceRole.VIEWER) == ["meeting:read", "member:read", "team:read"]


def test_login_refresh_and_me_carry_system_role_and_owner_permissions(client, email_outbox):
    credentials = register_user(client, email_outbox, email="hom184-owner@example.com")
    login = login_user(client, credentials)
    workspace_id = login["workspaces"][0]["id"]

    refreshed = client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}
    )
    assert refreshed.status_code == 200, refreshed.text

    for session in (login, refreshed.json(), _me(client)):
        assert session["user"]["system_role"] == "user"
        workspace = _workspace(session, workspace_id)
        assert workspace["role"] == "owner"
        assert workspace["permissions"] == sorted(ALL_PERMISSION_VALUES)
        for granted in (
            "legal_hold:manage",
            "pii:original_access",
            "ai_policy:manage",
            "billing:manage",
        ):
            assert granted in workspace["permissions"]
        _assert_permissions_match_roles(session)


def test_each_membership_role_lists_exactly_its_permissions(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    shared = owner.workspace_id
    assert _workspace(_me(client, owner.cookies), shared)["permissions"] == _expected(
        WorkspaceRole.OWNER
    )
    for name, persona in people.items():
        session = _me(client, persona.cookies)
        workspace = _workspace(session, shared)
        assert workspace["role"] == name
        assert workspace["permissions"] == _expected(WorkspaceRole(name))
        # The personal workspace keeps its own (owner) list: permissions are per workspace.
        assert len(session["workspaces"]) == 2
        _assert_permissions_match_roles(session)

    viewer = _workspace(_me(client, people["viewer"].cookies), shared)["permissions"]
    assert viewer == ["meeting:read", "member:read", "team:read"]
    admin = _workspace(_me(client, people["admin"].cookies), shared)["permissions"]
    for withheld in ("legal_hold:manage", "pii:original_access", "ai_policy:manage"):
        assert withheld not in admin
    assert "billing:manage" not in admin


def test_role_and_system_role_changes_show_on_the_next_me(client, email_outbox):
    credentials = register_user(client, email_outbox, email="hom184-fresh@example.com")
    login = login_user(client, credentials)
    user_id = login["user"]["id"]
    workspace_id = login["workspaces"][0]["id"]

    for role in (
        WorkspaceRole.VIEWER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
        WorkspaceRole.OWNER,
    ):
        _set_role(workspace_id, user_id, role)
        workspace = _workspace(_me(client), workspace_id)
        assert workspace["role"] == role.value
        assert workspace["permissions"] == _expected(role)

    _set_system_role(user_id, "operator")
    assert _me(client)["user"]["system_role"] == "operator"
    _set_system_role(user_id, "user")
    assert _me(client)["user"]["system_role"] == "user"


def test_permissions_follow_the_authorization_map_at_request_time(
    client, email_outbox, monkeypatch
):
    credentials = register_user(client, email_outbox, email="hom184-map@example.com")
    workspace_id = login_user(client, credentials)["workspaces"][0]["id"]
    assert _workspace(_me(client), workspace_id)["permissions"] == sorted(ALL_PERMISSION_VALUES)

    with monkeypatch.context() as patch:
        patch.setitem(
            ROLE_PERMISSIONS,
            WorkspaceRole.OWNER,
            frozenset({Permission.TEAM_READ, Permission.AUDIT_READ}),
        )
        workspace = _workspace(_me(client), workspace_id)
        assert workspace["role"] == "owner"
        assert workspace["permissions"] == ["audit:read", "team:read"]

    assert _workspace(_me(client), workspace_id)["permissions"] == sorted(ALL_PERMISSION_VALUES)


def test_otp_session_carries_system_role_and_permissions(client, sms_outbox):
    requested = client.post("/api/v1/auth/otp/request", json={"phone": "09121840184"})
    assert requested.status_code == 202, requested.text
    verified = client.post(
        "/api/v1/auth/otp/verify",
        json={"challenge_id": requested.json()["challenge_id"], "code": sms_outbox[-1]["code"]},
    )
    assert verified.status_code == 200, verified.text
    session = verified.json()
    assert session["user"]["system_role"] == "user"
    _assert_permissions_match_roles(session)


def test_unselectable_workspace_is_not_listed(client, email_outbox):
    credentials = register_user(client, email_outbox, email="hom184-expired@example.com")
    login = login_user(client, credentials)
    workspace_id = login["workspaces"][0]["id"]
    with inspection_session_factory()() as db:
        membership = db.execute(
            select(Membership).where(Membership.workspace_id == workspace_id)
        ).scalar_one()
        membership.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert workspace_id not in {item["id"] for item in _me(client)["workspaces"]}


def test_clients_cannot_send_system_role_or_permissions(client, email_outbox):
    credentials = register_user(client, email_outbox, email="hom184-strict@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={
            "identifier": credentials["email"],
            "password": credentials["password"],
            "system_role": "operator",
            "permissions": ["billing:manage"],
        },
    )
    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["error"]["details"]}
    assert {"body.system_role", "body.permissions"} <= fields


def test_openapi_schema_declares_the_new_fields(client):
    schemas = client.app.openapi()["components"]["schemas"]
    system_role = schemas["UserResponse"]["properties"]["system_role"]
    assert system_role["enum"] == ["user", "operator"]
    assert "system_role" in schemas["UserResponse"]["required"]
    permissions = schemas["WorkspaceResponse"]["properties"]["permissions"]
    assert permissions["type"] == "array" and permissions["items"] == {"type": "string"}
    assert "permissions" in schemas["WorkspaceResponse"]["required"]
