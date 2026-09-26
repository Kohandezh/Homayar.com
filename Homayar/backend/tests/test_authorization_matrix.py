"""WP03-T03: the authorization matrix is explicit, deny-by-default and enforced.

Three kinds of evidence:

1. Document ↔ code: the tables in ``docs/security/authorization-matrix.md``
   marked *machine-checked* must equal what the code enforces.
2. Route coverage: every mounted route declares a policy (permission, session,
   optional session) or is in the documented public list.
3. Behaviour: allowed *and* denied requests per role, team record scope with
   effective-dated assignments, membership validity windows and the common
   escalation paths.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.routing import APIRoute
from sqlalchemy import select

from backend.database.saas_models import (
    Meeting,
    MeetingStatus,
    Membership,
    WorkspaceRole,
)
from backend.routers.v1 import members as members_router
from backend.security import context as ctx
from backend.security.authorization import GRANTABLE_ROLES, ROLE_PERMISSIONS, Permission
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user

MATRIX_DOC = Path(__file__).resolve().parents[2] / "docs" / "security" / "authorization-matrix.md"
ROLE_COLUMNS = (
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
    WorkspaceRole.MEMBER,
    WorkspaceRole.VIEWER,
)


# --- helpers -----------------------------------------------------------------


def _doc_block(name: str) -> list[list[str]]:
    text = MATRIX_DOC.read_text(encoding="utf-8")
    match = re.search(
        rf"<!-- matrix:{name}:start -->(.*?)<!-- matrix:{name}:end -->", text, re.S
    )
    assert match, f"matrix block {name!r} missing from {MATRIX_DOC}"
    rows = []
    for line in match.group(1).strip().splitlines()[2:]:  # skip header + separator
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def _route_policy(route: APIRoute) -> str:
    found: list[str] = []

    def walk(dependant) -> None:
        call = dependant.call
        permission = getattr(call, "homayar_permission", None)
        if permission is not None:
            suffix = " +csrf" if getattr(call, "homayar_write", False) else ""
            found.append(f"{permission.value}{suffix}")
            return
        if call is ctx.require_workspace:
            found.append("workspace")
            return
        if call is ctx.require_csrf:
            found.append("session+csrf")
            return
        if call is ctx.require_actor:
            found.append("session")
            return
        if call is ctx.optional_actor:
            found.append("optional-session")
            return
        for sub in dependant.dependencies:
            walk(sub)

    for sub in route.dependant.dependencies:
        walk(sub)
    if not found:
        return "public"
    assert len(found) == 1, f"{route.path} declares several policies: {found}"
    return found[0]


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _headers(cookies: dict[str, str], workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]}


class _Persona:
    def __init__(self, client, workspace_id: str, session: dict, cookies: dict[str, str]):
        self.client = client
        self.workspace_id = workspace_id
        self.user_id = session["user"]["id"]
        self.cookies = cookies
        self.headers = _headers(cookies, workspace_id)

    def get(self, path: str, **kwargs):
        return self.client.get(path, cookies=self.cookies, headers=self.headers, **kwargs)

    def post(self, path: str, **kwargs):
        return self.client.post(path, cookies=self.cookies, headers=self.headers, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.client.patch(path, cookies=self.cookies, headers=self.headers, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.client.delete(path, cookies=self.cookies, headers=self.headers, **kwargs)


def _workspace_with_roles(
    client, email_outbox, monkeypatch, *, roles: dict[str, str]
) -> tuple[_Persona, dict[str, _Persona], dict[str, str]]:
    """Owner plus one invited user per ``roles`` entry (name → workspace role)."""
    delivered: dict[str, str] = {}

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)

    owner_credentials = register_user(
        client, email_outbox, email="matrix-owner@example.com", name="Matrix Owner"
    )
    owner_session = login_user(client, owner_credentials)
    owner_cookies = _cookies(client)
    workspace_id = owner_session["workspaces"][0]["id"]
    owner = _Persona(client, workspace_id, owner_session, owner_cookies)

    personas: dict[str, _Persona] = {}
    membership_ids: dict[str, str] = {}
    for name, role in roles.items():
        email = f"matrix-{name}@example.com"
        credentials = register_user(client, email_outbox, email=email, name=name)
        session = login_user(client, credentials)
        cookies = _cookies(client)
        invited = owner.post(
            "/api/v1/members/invitations", json={"email": email, "role": role}
        )
        assert invited.status_code == 201, invited.text
        accepted = client.post(
            "/api/v1/members/invitations/accept",
            json={"token": delivered[email]},
            cookies=cookies,
            headers={"X-CSRF-Token": cookies[CSRF_COOKIE]},
        )
        assert accepted.status_code == 200, accepted.text
        membership_ids[name] = accepted.json()["membership"]["id"]
        personas[name] = _Persona(client, workspace_id, session, cookies)
    return owner, personas, membership_ids


def _insert_meeting(
    workspace_id: str,
    *,
    created_by: str,
    title: str,
    team_id: str | None = None,
    agent_user_id: str | None = None,
) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=created_by,
            title=title,
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
            team_id=team_id,
            agent_user_id=agent_user_id,
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _listed_titles(persona: _Persona) -> set[str]:
    response = persona.get("/api/v1/meetings")
    assert response.status_code == 200, response.text
    return {item["title"] for item in response.json()["items"]}


# --- 1. document ↔ code ------------------------------------------------------


def test_permission_matrix_document_matches_code():
    rows = _doc_block("permissions")
    documented = {
        row[0]: {role for role, mark in zip(ROLE_COLUMNS, row[1:], strict=True) if mark == "✓"}
        for row in rows
    }
    expected = {
        permission.value: {role for role in ROLE_COLUMNS if permission in ROLE_PERMISSIONS[role]}
        for permission in Permission
    }
    assert documented == expected

    def _roles(cell: str) -> set[str]:
        return {part.strip() for part in cell.split(",") if part.strip() and part.strip() != "–"}

    grants = {row[0]: _roles(row[1]) for row in _doc_block("grants")}
    expected_grants = {
        role.value: {granted.value for granted in GRANTABLE_ROLES[role]} for role in ROLE_COLUMNS
    }
    assert grants == expected_grants


def test_route_inventory_document_matches_code(client):
    documented = {(row[0], row[1]): row[2] for row in _doc_block("routes")}
    actual: dict[tuple[str, str], str] = {}
    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            actual[(method, route.path)] = _route_policy(route)
    assert actual == documented, (
        "Route inventory drifted from docs/security/authorization-matrix.md §6. "
        "Every route needs an explicit policy entry."
    )
    # Deny-by-default: anything under the API prefix that is not an
    # authentication entry point must resolve a session or a permission.
    for (method, path), policy in actual.items():
        if path.startswith("/api/v1/") and not path.startswith("/api/v1/auth/"):
            assert policy != "public", f"{method} {path} is mounted without a policy"


# --- 2. behaviour per role ---------------------------------------------------


def test_role_matrix_allowed_and_denied(client, email_outbox, monkeypatch):
    owner, people, membership_ids = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer", "extra": "member"},
    )
    admin, member, viewer = people["admin"], people["member"], people["viewer"]
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="Board")

    # viewer: reads allowed, every write and every admin surface denied
    assert viewer.get("/api/v1/meetings").status_code == 200
    assert viewer.get(f"/api/v1/meetings/{meeting_id}").status_code == 200
    assert viewer.get("/api/v1/members").status_code == 200
    assert viewer.get("/api/v1/teams").status_code == 200
    assert viewer.patch(f"/api/v1/meetings/{meeting_id}", json={"title": "x"}).status_code == 403
    assert viewer.post(f"/api/v1/meetings/{meeting_id}/shares", json={}).status_code == 403
    assert viewer.post("/api/v1/teams", json={"name": "Ops"}).status_code == 403
    assert viewer.get("/api/v1/admin/workspace-settings").status_code == 403
    assert viewer.get("/api/v1/admin/audit-events").status_code == 403
    assert viewer.get("/api/v1/billing/usage").status_code == 403
    assert viewer.get("/api/v1/members/invitations").status_code == 403

    # member: operational writes allowed, administration denied
    renamed = member.patch(f"/api/v1/meetings/{meeting_id}", json={"title": "Renamed"})
    assert renamed.status_code == 200
    assert member.post(f"/api/v1/meetings/{meeting_id}/shares", json={}).status_code == 201
    assert member.post("/api/v1/teams", json={"name": "Ops"}).status_code == 403
    assert member.get("/api/v1/members/invitations").status_code == 403
    assert member.post(
        "/api/v1/members/invitations", json={"email": "x@example.com", "role": "admin"}
    ).status_code == 403
    assert member.patch(
        "/api/v1/admin/workspace-settings", json={"record_scope": "team"}
    ).status_code == 403
    assert member.delete(f"/api/v1/meetings/{meeting_id}").status_code == 403
    assert member.get("/api/v1/billing/plans").status_code == 403

    # admin: workspace administration allowed, billing management and admin
    # minting denied
    assert admin.get("/api/v1/admin/workspace-settings").status_code == 200
    assert admin.patch(
        "/api/v1/admin/workspace-settings", json={"retention_days": 30}
    ).status_code == 200
    assert admin.get("/api/v1/admin/audit-events").status_code == 200
    created = admin.post("/api/v1/teams", json={"name": "Ops"})
    assert created.status_code == 201, created.text
    assert admin.get("/api/v1/billing/usage").status_code == 200
    assert admin.get("/api/v1/billing/management").status_code == 403
    assert admin.post(
        "/api/v1/members/invitations", json={"email": "new-admin@example.com", "role": "admin"}
    ).status_code == 403
    assert admin.patch(
        f"/api/v1/members/{membership_ids['member']}", json={"role": "admin"}
    ).status_code == 403
    assert admin.patch(
        f"/api/v1/members/{membership_ids['member']}", json={"role": "viewer"}
    ).status_code == 200

    # owner: may promote to admin; the new admin still cannot manage a peer
    assert owner.get("/api/v1/billing/management").status_code == 200
    promoted = owner.patch(f"/api/v1/members/{membership_ids['extra']}", json={"role": "admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"
    assert admin.patch(
        f"/api/v1/members/{membership_ids['extra']}", json={"role": "member"}
    ).status_code == 403
    assert admin.delete(f"/api/v1/members/{membership_ids['extra']}").status_code == 403
    assert admin.patch(
        f"/api/v1/members/{membership_ids['admin']}", json={"role": "viewer"}
    ).status_code == 403  # self-management refused
    assert owner.patch(
        f"/api/v1/members/{membership_ids['extra']}", json={"role": "member"}
    ).status_code == 200


# --- 3. team record scope with effective-dated assignments ------------------


def test_team_scope_restricts_records_and_attribution(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "sup": "member", "agent": "member", "lone": "member"},
    )
    admin, sup, agent, lone = people["admin"], people["sup"], people["agent"], people["lone"]
    ws = owner.workspace_id

    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200 and switched.json()["record_scope"] == "team"

    team_a = owner.post("/api/v1/teams", json={"name": "Team A"}).json()["id"]
    team_b = owner.post("/api/v1/teams", json={"name": "Team B"}).json()["id"]
    assigned = owner.post(
        f"/api/v1/teams/{team_a}/assignments", json={"user_id": sup.user_id, "role": "supervisor"}
    )
    assert assigned.status_code == 201, assigned.text
    sup_assignment_id = assigned.json()["id"]
    assert assigned.json()["is_effective"] is True
    assert owner.post(
        f"/api/v1/teams/{team_a}/assignments", json={"user_id": agent.user_id, "role": "agent"}
    ).status_code == 201
    # duplicate open assignment is a conflict, not a silent second row
    assert owner.post(
        f"/api/v1/teams/{team_a}/assignments", json={"user_id": agent.user_id, "role": "agent"}
    ).status_code == 409

    m_a = _insert_meeting(
        ws, created_by=owner.user_id, title="A call", team_id=team_a, agent_user_id=agent.user_id
    )
    m_b = _insert_meeting(ws, created_by=owner.user_id, title="B call", team_id=team_b)
    m_own = _insert_meeting(ws, created_by=lone.user_id, title="Lone upload")

    # visibility
    assert _listed_titles(owner) == {"A call", "B call", "Lone upload"}
    assert _listed_titles(admin) == {"A call", "B call", "Lone upload"}
    assert _listed_titles(sup) == {"A call"}
    assert _listed_titles(agent) == {"A call"}
    assert _listed_titles(lone) == {"Lone upload"}
    assert sup.get(f"/api/v1/meetings/{m_a}").status_code == 200
    for path in (
        f"/api/v1/meetings/{m_b}",
        f"/api/v1/meetings/{m_b}/job",
        f"/api/v1/meetings/{m_b}/transcript",
        f"/api/v1/meetings/{m_b}/summary",
        f"/api/v1/meetings/{m_b}/actions",
        f"/api/v1/meetings/{m_b}/export?format=txt",
        f"/api/v1/meetings/{m_b}/shares",
        f"/api/v1/meetings/{m_own}",
    ):
        assert sup.get(path).status_code == 404, path
    assert sup.post(f"/api/v1/meetings/{m_b}/shares", json={}).status_code == 404
    assert sup.post(
        f"/api/v1/meetings/{m_b}/actions", json={"description": "leak"}
    ).status_code == 404
    assert agent.get(f"/api/v1/meetings/{m_own}").status_code == 404
    search = sup.get("/api/v1/search", params={"q": "call"})
    assert search.status_code == 200
    assert {item["title"] for item in search.json()["items"]} == {"A call"}

    # attribution: supervisor cannot move a record to a team they do not
    # supervise, detach it, or attribute it to an agent outside their teams
    assert sup.patch(f"/api/v1/meetings/{m_a}", json={"team_id": team_b}).status_code == 403
    assert sup.patch(f"/api/v1/meetings/{m_a}", json={"team_id": None}).status_code == 403
    outside_agent = sup.patch(f"/api/v1/meetings/{m_a}", json={"agent_user_id": lone.user_id})
    assert outside_agent.status_code == 403
    ok = sup.patch(
        f"/api/v1/meetings/{m_a}", json={"agent_user_id": agent.user_id, "title": "A call"}
    )
    assert ok.status_code == 200 and ok.json()["agent_user_id"] == agent.user_id
    # an agent (no supervised team) cannot re-home their own record
    assert agent.patch(f"/api/v1/meetings/{m_a}", json={"team_id": team_a}).status_code == 403
    # admin may move it anywhere in the workspace
    moved = admin.patch(f"/api/v1/meetings/{m_b}", json={"team_id": team_a})
    assert moved.status_code == 200 and moved.json()["team_id"] == team_a
    assert _listed_titles(sup) == {"A call", "B call"}

    # effective dates: an assignment whose window already closed grants nothing
    expired = owner.post(
        f"/api/v1/teams/{team_b}/assignments",
        json={
            "user_id": lone.user_id,
            "role": "supervisor",
            "valid_from": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "valid_until": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        },
    )
    assert expired.status_code == 201 and expired.json()["is_effective"] is False
    assert _listed_titles(lone) == {"Lone upload"}
    assert owner.post(
        f"/api/v1/teams/{team_b}/assignments",
        json={
            "user_id": lone.user_id,
            "role": "agent",
            "valid_from": datetime.now(UTC).isoformat(),
            "valid_until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    ).status_code == 422

    # revocation takes effect on the next request
    revoke = owner.delete(f"/api/v1/teams/{team_a}/assignments/{sup_assignment_id}")
    assert revoke.status_code == 204
    assert _listed_titles(sup) == set()
    assert sup.get(f"/api/v1/meetings/{m_a}").status_code == 404
    detail = owner.get(f"/api/v1/teams/{team_a}")
    assert detail.status_code == 200
    revoked = next(a for a in detail.json()["assignments"] if a["id"] == sup_assignment_id)
    assert revoked["revoked_at"] is not None and revoked["is_effective"] is False

    # deactivating a team removes the access it conferred
    assert owner.patch(f"/api/v1/teams/{team_a}", json={"is_active": False}).status_code == 200
    assert _listed_titles(agent) == {"A call"}  # still attributed as agent
    assert admin.patch(f"/api/v1/meetings/{m_own}", json={"team_id": team_a}).status_code == 422

    # flat mode restores workspace-wide visibility for the same members
    flat = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "workspace"})
    assert flat.status_code == 200
    assert _listed_titles(lone) == {"A call", "B call", "Lone upload"}


# --- 4. membership validity window ------------------------------------------


def test_membership_validity_window_is_enforced(client, email_outbox, monkeypatch):
    owner, people, membership_ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]
    assert member.get("/api/v1/meetings").status_code == 200
    listed = owner.get("/api/v1/members").json()["items"]
    assert all(item["valid_from"] is not None for item in listed)

    def set_window(valid_from=None, valid_until=None):
        with inspection_session_factory()() as db:
            membership = db.get(Membership, membership_ids["member"])
            if valid_from is not None:
                membership.valid_from = valid_from
            membership.valid_until = valid_until
            db.commit()

    set_window(valid_until=datetime.now(UTC) - timedelta(seconds=1))
    denied = member.get("/api/v1/meetings")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "workspace_access_denied"
    # the same expired membership is not usable through another endpoint either
    assert member.get("/api/v1/members").status_code == 403
    assert member.post("/api/v1/teams", json={"name": "x"}).status_code == 403

    set_window(valid_from=datetime.now(UTC) + timedelta(days=1), valid_until=None)
    assert member.get("/api/v1/meetings").status_code == 403

    set_window(valid_from=datetime.now(UTC) - timedelta(days=1), valid_until=None)
    assert member.get("/api/v1/meetings").status_code == 200


# --- 5. tenant ids in bodies, foreign teams, non-members ---------------------


def test_tenant_id_in_body_and_foreign_team_are_rejected(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="Ours")

    # a second, unrelated workspace with its own team
    outsider_credentials = register_user(
        client, email_outbox, email="matrix-outsider@example.com", name="Outsider"
    )
    outsider_session = login_user(client, outsider_credentials)
    outsider = _Persona(
        client, outsider_session["workspaces"][0]["id"], outsider_session, _cookies(client)
    )
    foreign_team = outsider.post("/api/v1/teams", json={"name": "Foreign"}).json()["id"]

    # tenant identifiers in bodies are rejected outright
    assert owner.patch(
        f"/api/v1/meetings/{meeting_id}",
        json={"title": "x", "workspace_id": outsider.workspace_id},
    ).status_code == 422
    assert owner.patch(
        "/api/v1/admin/workspace-settings", json={"workspace_id": outsider.workspace_id}
    ).status_code == 422
    assert owner.post(
        "/api/v1/teams", json={"name": "T", "workspace_id": outsider.workspace_id}
    ).status_code == 422

    # a team of another workspace cannot be attached, even by the owner
    rejected = owner.patch(f"/api/v1/meetings/{meeting_id}", json={"team_id": foreign_team})
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_team"
    # nor can members of another workspace be assigned or attributed
    own_team = owner.post("/api/v1/teams", json={"name": "Ours"}).json()["id"]
    assert owner.post(
        f"/api/v1/teams/{own_team}/assignments",
        json={"user_id": outsider.user_id, "role": "agent"},
    ).status_code == 422
    assert owner.patch(
        f"/api/v1/meetings/{meeting_id}", json={"agent_user_id": outsider.user_id}
    ).status_code == 422
    # and the outsider cannot reach our team through their own workspace
    assert outsider.get(f"/api/v1/teams/{own_team}").status_code == 404
    assert outsider.post(
        f"/api/v1/teams/{own_team}/assignments",
        json={"user_id": member.user_id, "role": "agent"},
    ).status_code == 404
    with inspection_session_factory()() as db:
        assert db.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id)) is None
