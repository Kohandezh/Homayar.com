"""HOM-198: ``GET /api/v1/meetings/counts`` counts meetings per status.

The admin overview needs real totals, not a count of the latest 100 rows.
The counts use the same access rules as ``GET /api/v1/meetings``: the
selected workspace, no deleted meetings, the caller's record scope, and
``meeting:read`` for sessions and API keys.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import event

from backend.database.relational import get_engine
from backend.database.saas_models import Meeting, MeetingStatus
from backend.routers.v1 import members as members_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import grant_admin_seats, login_user, register_user

COUNTS = "/api/v1/meetings/counts"


# --- helpers -----------------------------------------------------------------


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


class _Persona:
    def __init__(self, client, workspace_id: str, session: dict, cookies: dict[str, str]):
        self.client = client
        self.workspace_id = workspace_id
        self.session = session
        self.user_id = session["user"]["id"]
        self.cookies = cookies
        self.headers = {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]}

    def in_workspace(self, workspace_id: str) -> _Persona:
        return _Persona(self.client, workspace_id, self.session, self.cookies)

    def get(self, path: str, **kwargs):
        return self.client.get(path, cookies=self.cookies, headers=self.headers, **kwargs)

    def post(self, path: str, **kwargs):
        return self.client.post(path, cookies=self.cookies, headers=self.headers, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.client.patch(path, cookies=self.cookies, headers=self.headers, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.client.delete(path, cookies=self.cookies, headers=self.headers, **kwargs)


def _signup(client, email_outbox, *, email: str) -> _Persona:
    """A new verified user, acting in the workspace created at sign-up."""
    session = login_user(client, register_user(client, email_outbox, email=email))
    return _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))


def _workspace_with_roles(
    client, email_outbox, monkeypatch, *, roles: dict[str, str]
) -> tuple[_Persona, dict[str, _Persona]]:
    """Owner plus one invited user per ``roles`` entry (name → workspace role)."""
    delivered: dict[str, str] = {}

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)

    owner = _signup(client, email_outbox, email="counts-owner@example.com")
    if "admin" in roles.values():
        grant_admin_seats(owner.workspace_id)  # the demo's one seat is the owner's
    personas: dict[str, _Persona] = {}
    for name, role in roles.items():
        email = f"counts-{name}@example.com"
        person = _signup(client, email_outbox, email=email)
        invited = owner.post("/api/v1/members/invitations", json={"email": email, "role": role})
        assert invited.status_code == 201, invited.text
        accepted = client.post(
            "/api/v1/members/invitations/accept",
            json={"token": delivered[email]},
            cookies=person.cookies,
            headers={"X-CSRF-Token": person.cookies[CSRF_COOKIE]},
        )
        assert accepted.status_code == 200, accepted.text
        personas[name] = person.in_workspace(owner.workspace_id)
    return owner, personas


def _insert_meeting(
    workspace_id: str,
    *,
    created_by: str,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    title: str = "Call",
    team_id: str | None = None,
    agent_user_id: str | None = None,
    deleted: bool = False,
) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=created_by,
            title=title,
            status=status,
            duration_seconds=30,
            team_id=team_id,
            agent_user_id=agent_user_id,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _expected(**nonzero: int) -> dict:
    counts = {status.value: 0 for status in MeetingStatus}
    for status, count in nonzero.items():
        assert status in counts, status
        counts[status] = count
    return {"counts": counts, "total": sum(counts.values())}


def _counts(persona: _Persona) -> dict:
    response = persona.get(COUNTS)
    assert response.status_code == 200, response.text
    return response.json()


def _listed_by_status(persona: _Persona) -> dict:
    """What ``GET /api/v1/meetings`` shows this caller, grouped by status."""
    response = persona.get("/api/v1/meetings", params={"limit": 100})
    assert response.status_code == 200, response.text
    grouped: dict[str, int] = {}
    for item in response.json()["items"]:
        grouped[item["status"]] = grouped.get(item["status"], 0) + 1
    return _expected(**grouped)


def _error(response) -> str:
    return response.json()["error"]["code"]


# --- shape and values --------------------------------------------------------


def test_owner_gets_the_exact_count_per_status(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-owner@example.com")
    for status in (
        MeetingStatus.COMPLETED,
        MeetingStatus.COMPLETED,
        MeetingStatus.FAILED,
        MeetingStatus.QUEUED,
        MeetingStatus.TRANSCRIBING,
    ):
        _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=status)

    body = _counts(owner)
    assert body == _expected(completed=2, failed=1, queued=1, transcribing=1)
    assert body["total"] == 5
    # one key per status, in enum order, integer values
    assert list(body["counts"]) == [status.value for status in MeetingStatus]
    assert all(type(value) is int for value in body["counts"].values())


def test_empty_workspace_has_every_status_at_zero(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-empty@example.com")
    body = _counts(owner)
    assert body == _expected()
    assert len(body["counts"]) == 11 and body["total"] == 0


# --- tenancy and deletion ----------------------------------------------------


def test_counts_only_the_selected_workspace(client, email_outbox, monkeypatch):
    owner, people = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"guest": "member"}
    )
    guest = people["guest"]
    guest_home = guest.session["workspaces"][0]["id"]
    assert guest_home != owner.workspace_id
    _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.COMPLETED)
    _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.FAILED)
    for status in (MeetingStatus.COMPLETED,) * 3 + (MeetingStatus.FAILED,) * 2:
        _insert_meeting(guest_home, created_by=guest.user_id, status=status)

    # guest is a member of both; the selected workspace decides what counts
    assert _counts(guest) == _expected(completed=1, failed=1)
    assert _counts(guest.in_workspace(guest_home)) == _expected(completed=3, failed=2)
    assert _counts(owner) == _expected(completed=1, failed=1)


def test_deleted_meetings_are_not_counted(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-deleted@example.com")
    _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.COMPLETED)
    _insert_meeting(
        owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.COMPLETED, deleted=True
    )
    doomed = _insert_meeting(
        owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.FAILED
    )
    assert _counts(owner) == _expected(completed=1, failed=1)

    # deleting through the API drops it out, including as "cancelled"
    assert owner.delete(f"/api/v1/meetings/{doomed}").status_code == 204
    assert _counts(owner) == _expected(completed=1)


# --- record scope and roles --------------------------------------------------


def test_team_record_scope_counts_what_the_list_shows(client, email_outbox, monkeypatch):
    owner, people = _workspace_with_roles(
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
    for user_id, role in ((sup.user_id, "supervisor"), (agent.user_id, "agent")):
        assigned = owner.post(
            f"/api/v1/teams/{team_a}/assignments", json={"user_id": user_id, "role": role}
        )
        assert assigned.status_code == 201, assigned.text

    completed, failed, queued = (
        MeetingStatus.COMPLETED,
        MeetingStatus.FAILED,
        MeetingStatus.QUEUED,
    )
    # team A: one attributed to the agent, one not
    _insert_meeting(
        ws, created_by=owner.user_id, status=completed, team_id=team_a, agent_user_id=agent.user_id
    )
    _insert_meeting(ws, created_by=owner.user_id, status=failed, team_id=team_a)
    # team B, same status as a team A meeting
    _insert_meeting(ws, created_by=owner.user_id, status=completed, team_id=team_b)
    # uploads without a team
    _insert_meeting(ws, created_by=sup.user_id, status=queued)
    _insert_meeting(ws, created_by=agent.user_id, status=queued)
    _insert_meeting(ws, created_by=lone.user_id, status=completed)

    expected = {
        "owner": (owner, _expected(completed=3, failed=1, queued=2)),
        "admin": (admin, _expected(completed=3, failed=1, queued=2)),
        # team A (both) plus own upload; not team B
        "sup": (sup, _expected(completed=1, failed=1, queued=1)),
        # attributed to them plus own upload; not team A's other meeting
        "agent": (agent, _expected(completed=1, queued=1)),
        # own upload only
        "lone": (lone, _expected(completed=1)),
    }
    for name, (persona, counts) in expected.items():
        assert _counts(persona) == counts, name
        assert _listed_by_status(persona) == counts, name


def test_every_workspace_role_may_read_counts(client, email_outbox, monkeypatch):
    owner, people = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.PARTIAL)
    for persona in (owner, people["admin"], people["member"], people["viewer"]):
        assert _counts(persona) == _expected(partial=1)


def test_no_session_is_refused_like_the_list(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-anon@example.com")
    assert _counts(owner) == _expected()

    client.cookies.clear()
    anonymous = client.get(COUNTS)
    listed = client.get("/api/v1/meetings")
    assert listed.status_code == 401
    assert anonymous.status_code == 401, anonymous.text
    assert _error(anonymous) == _error(listed)


# --- API keys ----------------------------------------------------------------


def test_api_key_needs_the_meeting_read_scope(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-keys@example.com")
    stranger = _signup(client, email_outbox, email="counts-stranger@example.com")
    _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.COMPLETED)
    _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=MeetingStatus.FAILED)
    _insert_meeting(
        stranger.workspace_id, created_by=stranger.user_id, status=MeetingStatus.COMPLETED
    )

    def mint(scopes: list[str]) -> str:
        created = owner.post(
            "/api/v1/api-keys", json={"name": "counts", "scopes": scopes, "expires_in_days": 30}
        )
        assert created.status_code == 201, created.text
        return created.json()["key"]

    reader = mint(["meeting:read"])
    other = mint(["team:read"])
    client.cookies.clear()

    allowed = client.get(COUNTS, headers={"Authorization": f"Bearer {reader}"})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == _expected(completed=1, failed=1)

    denied = client.get(COUNTS, headers={"Authorization": f"Bearer {other}"})
    listed = client.get("/api/v1/meetings", headers={"Authorization": f"Bearer {other}"})
    assert listed.status_code == 403 and _error(listed) == "api_key_scope_denied"
    assert denied.status_code == 403, denied.text
    assert _error(denied) == "api_key_scope_denied"


# --- routing and query shape -------------------------------------------------


def test_counts_is_not_read_as_a_meeting_id(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-route@example.com")
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id)

    response = owner.get(COUNTS)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"counts", "total"}

    detail = owner.get(f"/api/v1/meetings/{meeting_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == meeting_id


def test_counts_read_with_one_grouped_query(client, email_outbox):
    owner = _signup(client, email_outbox, email="counts-queries@example.com")
    statuses = (
        MeetingStatus.COMPLETED,
        MeetingStatus.FAILED,
        MeetingStatus.QUEUED,
        MeetingStatus.ANALYZING,
    )

    def meeting_selects() -> list[str]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT") and re.search(
                r"\bmeetings\b", statement
            ):
                seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = owner.get(COUNTS)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return seen

    for status in statuses:
        _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=status)
    few = meeting_selects()
    for status in statuses * 9:
        _insert_meeting(owner.workspace_id, created_by=owner.user_id, status=status)
    many = meeting_selects()

    assert len(few) == len(many) == 1, many
    assert "GROUP BY" in many[0].upper(), many[0]
    assert _counts(owner) == _expected(completed=10, failed=10, queued=10, analyzing=10)
