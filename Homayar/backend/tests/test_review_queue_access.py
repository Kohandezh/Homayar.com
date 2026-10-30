"""HOM-73 PR 2: who may reach the review queue and which items they see (DESIGN §3, §4).

The permissions open the door: ``review:read`` and ``review:manage`` for owners, admins
and members, never for viewers and never for an API key; ``from-sample`` needs
``workspace:manage``.  The record rules then decide which items a caller sees:

* a review admin (owner or admin) sees every item of the workspace;
* a member sees an item when they supervise the conversation's current team (an
  effective, non-revoked supervisor assignment) and are not its agent;
* the workspace ``record_scope`` grants nothing here;
* an item that is not visible is ``404 review_item_not_found`` on get and on every
  action, and absent from the list, like an item of another workspace;
* an assignee must be an effective owner, admin or member with review authority over
  the conversation, and never its agent (checked first).

The other modules import the queue fixture and the helpers from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from backend.database.saas_models import AuditEvent, Meeting, ReviewItem
from backend.security.authorization import (
    API_KEY_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    WorkspaceRole,
)
from backend.services.rate_limit_service import RateLimiter
from backend.tests import test_authorization_matrix as authorization_matrix
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import (
    _cookies,
    _insert_meeting,
    _Persona,
    _workspace_with_roles,
)
from backend.tests.test_coverage import WINDOW

ITEMS = "/api/v1/review-items"
ACTIONS = ("assign", "due-time", "start", "complete", "cancel")


# --- helpers shared by the review queue tests --------------------------------------------


def code(response) -> str:
    return response.json()["error"]["code"]


def due(delta: timedelta = timedelta(days=2)) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def create(persona: _Persona, meeting_id: str, **body: object):
    return persona.post(ITEMS, json={"meeting_id": meeting_id, "due_at": due(), **body})


def created(persona: _Persona, meeting_id: str, **body: object) -> dict:
    response = create(persona, meeting_id, **body)
    assert response.status_code == 201, response.text
    return response.json()


def act(persona: _Persona, item: dict, action: str, **body: object):
    """One item action with the revision the caller last read (``expected_revision``)."""
    payload = {"expected_revision": item["revision"], **body}
    if action == "assign":
        payload.setdefault("assignee_user_id", None)
    if action == "due-time":
        payload.setdefault("due_at", due(timedelta(days=3)))
    if action == "cancel":
        payload.setdefault("reason", "duplicate")
    return persona.post(f"{ITEMS}/{item['id']}/{action}", json=payload)


def acted(persona: _Persona, item: dict, action: str, **body: object) -> dict:
    response = act(persona, item, action, **body)
    assert response.status_code == 200, response.text
    return response.json()


DATETIME_FIELDS = (
    "assigned_at",
    "due_at",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "cancelled_at",
)


def assert_utc(item: dict) -> None:
    """Every datetime of an item response has a zero UTC offset."""
    for name in DATETIME_FIELDS:
        if item[name] is not None:
            assert datetime.fromisoformat(item[name]).utcoffset() == timedelta(0), (name, item)


def listed(persona: _Persona, **params: object) -> list[dict]:
    response = persona.get(ITEMS, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    for item in body["items"]:
        assert_utc(item)
    return body["items"]


def listed_ids(persona: _Persona, **params: object) -> list[str]:
    return [item["id"] for item in listed(persona, **params)]


def stored(item_id: str) -> ReviewItem:
    with inspection_session_factory()() as db:
        row = db.get(ReviewItem, item_id)
        assert row is not None
        return row


def set_item(item_id: str, **values: object) -> None:
    """Change a stored item behind the API (a past due time, a tie)."""
    with inspection_session_factory()() as db:
        db.execute(update(ReviewItem).where(ReviewItem.id == item_id).values(**values))
        db.commit()


def review_events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type.like("review_item.%")
                    | (AuditEvent.event_type == "review_sample.enqueued"),
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def audit_count() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


def item_count() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(ReviewItem)) or 0


def stranger(client, email_outbox, email: str) -> _Persona:
    """A user with a workspace of their own and no membership in the queue's workspace."""
    credentials = _register_without_limit(client, email_outbox, email=email, name="Stranger")
    session = login_user(client, credentials)
    return _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))


@dataclass
class Queue:
    """One workspace (``record_scope = workspace``) with two teams.

    * ``sup`` supervises team A, ``sup_b`` team B; ``agent`` is an agent of team A;
      ``plain`` and ``lapsed`` are members without assignments; ``viewer`` is a viewer.
    * ``m_a``: team A, ``agent`` is its agent.  ``m_a2``: team A, no agent.
      ``m_b``: team B.  ``m_none``: no team.  ``m_sup_own``: team A, ``sup`` is its agent.
    """

    owner: _Persona
    admin: _Persona
    sup: _Persona
    sup_b: _Persona
    agent: _Persona
    plain: _Persona
    lapsed: _Persona
    viewer: _Persona
    membership_ids: dict[str, str]
    team_a: str
    team_b: str
    m_a: str
    m_a2: str
    m_b: str
    m_none: str
    m_sup_own: str

    @property
    def workspace_id(self) -> str:
        return self.owner.workspace_id

    def meeting(self, *, team_id: str | None = None, agent_user_id: str | None = None) -> str:
        return _insert_meeting(
            self.workspace_id,
            created_by=self.owner.user_id,
            title="Reviewed call",
            team_id=team_id,
            agent_user_id=agent_user_id,
        )

    def assign_team(self, persona: _Persona, team_id: str, role: str, **window: str) -> str:
        response = self.owner.post(
            f"/api/v1/teams/{team_id}/assignments",
            json={"user_id": persona.user_id, "role": role, **window},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]


def _register_without_limit(client, email_outbox, **kwargs):
    RateLimiter.reset_memory()  # five sign-ups an hour per address; the queue needs eight
    return register_user(client, email_outbox, **kwargs)


def build_queue(client, email_outbox, monkeypatch) -> Queue:
    monkeypatch.setattr(authorization_matrix, "register_user", _register_without_limit)
    owner, people, membership_ids = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={
            "admin": "admin",
            "sup": "member",
            "sup_b": "member",
            "agent": "member",
            "plain": "member",
            "lapsed": "member",
            "viewer": "viewer",
        },
    )
    team_a = owner.post("/api/v1/teams", json={"name": "Team A"}).json()["id"]
    team_b = owner.post("/api/v1/teams", json={"name": "Team B"}).json()["id"]
    built = Queue(
        owner=owner,
        membership_ids=membership_ids,
        team_a=team_a,
        team_b=team_b,
        m_a="",
        m_a2="",
        m_b="",
        m_none="",
        m_sup_own="",
        **people,
    )
    built.assign_team(built.sup, team_a, "supervisor")
    built.assign_team(built.sup_b, team_b, "supervisor")
    built.assign_team(built.agent, team_a, "agent")
    built.m_a = built.meeting(team_id=team_a, agent_user_id=built.agent.user_id)
    built.m_a2 = built.meeting(team_id=team_a)
    built.m_b = built.meeting(team_id=team_b)
    built.m_none = built.meeting()
    built.m_sup_own = built.meeting(team_id=team_a, agent_user_id=built.sup.user_id)
    return built


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


def assert_visible(persona: _Persona, item: dict) -> None:
    assert item["id"] in listed_ids(persona, status=["open", "in_review", "completed", "cancelled"])
    response = persona.get(f"{ITEMS}/{item['id']}")
    assert response.status_code == 200, response.text


def assert_hidden(persona: _Persona, item: dict) -> None:
    """Absent from the list and ``404 review_item_not_found`` on get and every action;
    the item does not change."""
    before = stored(item["id"]).revision
    statuses = ["open", "in_review", "completed", "cancelled"]
    assert item["id"] not in listed_ids(persona, status=statuses)
    response = persona.get(f"{ITEMS}/{item['id']}")
    assert response.status_code == 404 and code(response) == "review_item_not_found"
    for action in ACTIONS:
        response = act(persona, item, action)
        assert response.status_code == 404, (action, response.text)
        assert code(response) == "review_item_not_found", action
    assert stored(item["id"]).revision == before


def routes(item_id: str, meeting_id: str) -> list[tuple[str, str, dict | None]]:
    """Every review route, with a body that passes the shape checks."""
    revision = {"expected_revision": 1}
    return [
        ("GET", ITEMS, None),
        ("GET", f"{ITEMS}/{item_id}", None),
        ("POST", ITEMS, {"meeting_id": meeting_id, "due_at": due()}),
        (
            "POST",
            f"{ITEMS}/from-sample",
            {**WINDOW, "config": {"random_size": 1}, "due_at": due()},
        ),
        ("POST", f"{ITEMS}/{item_id}/assign", {**revision, "assignee_user_id": None}),
        ("POST", f"{ITEMS}/{item_id}/due-time", {**revision, "due_at": due()}),
        ("POST", f"{ITEMS}/{item_id}/start", revision),
        ("POST", f"{ITEMS}/{item_id}/complete", revision),
        ("POST", f"{ITEMS}/{item_id}/cancel", {**revision, "reason": "duplicate"}),
    ]


# --- permissions and route policy (criteria 1, 2) -----------------------------------------


def test_the_two_permissions_and_their_roles():
    assert Permission.REVIEW_READ.value == "review:read"
    assert Permission.REVIEW_MANAGE.value == "review:manage"
    for permission in (Permission.REVIEW_READ, Permission.REVIEW_MANAGE):
        holders = {role for role, granted in ROLE_PERMISSIONS.items() if permission in granted}
        assert holders == {WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER}
        assert permission not in API_KEY_SCOPES


def test_a_viewer_gets_403_on_every_review_route(queue):
    item = created(queue.owner, queue.m_a)
    for method, path, body in routes(item["id"], queue.m_a2):
        response = queue.viewer.client.request(
            method, path, json=body, cookies=queue.viewer.cookies, headers=queue.viewer.headers
        )
        assert response.status_code == 403, (method, path, response.text)
        assert code(response) == "permission_denied", (method, path)
    assert stored(item["id"]).revision == 1
    assert item_count() == 1
    # Allow-control: a member with the same requests reaches the queue.
    assert queue.sup.get(ITEMS).status_code == 200
    assert queue.sup.get(f"{ITEMS}/{item['id']}").status_code == 200


def test_an_api_key_gets_403_on_every_review_route(client, queue):
    owner = queue.owner
    item = created(owner, queue.m_a)
    every_scope = sorted(permission.value for permission in API_KEY_SCOPES)
    key = _mint(client, owner.headers, owner.cookies, name="all", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": owner.workspace_id})
    for method, path, body in routes(item["id"], queue.m_a2):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert code(response) == "api_key_scope_denied", (method, path)
    # No key can carry the review permissions.
    for scope in ("review:read", "review:manage"):
        refused = _mint(client, owner.headers, owner.cookies, name=scope, scopes=[scope])
        assert refused.status_code == 422, refused.text
    assert item_count() == 1 and stored(item["id"]).revision == 1


def test_every_write_route_needs_the_csrf_token(queue):
    owner = queue.owner
    item = created(owner, queue.m_a)
    before = audit_count()
    for method, path, body in routes(item["id"], queue.m_a2):
        if method != "POST":
            continue
        response = owner.client.post(
            path, json=body, cookies=owner.cookies, headers={"X-Workspace-ID": owner.workspace_id}
        )
        assert response.status_code == 403, (path, response.text)
        assert code(response) == "csrf_failed", path
    assert audit_count() == before and item_count() == 1
    # Allow-control: the same create with the token.
    assert create(owner, queue.m_a2).status_code == 201


def test_enqueueing_a_sample_needs_workspace_manage(queue):
    body = {**WINDOW, "config": {"random_size": 1}, "due_at": due()}
    refused = queue.sup.post(f"{ITEMS}/from-sample", json=body)
    assert refused.status_code == 403 and code(refused) == "permission_denied"
    for persona in (queue.admin, queue.owner):
        response = persona.post(f"{ITEMS}/from-sample", json=body)
        assert response.status_code == 201, response.text


# --- visibility (criteria 4 to 9) ---------------------------------------------------------


def test_admins_and_the_team_supervisor_see_the_item_others_do_not(queue):
    item = created(queue.owner, queue.m_a2)
    for persona in (queue.owner, queue.admin, queue.sup):
        assert_visible(persona, item)
    # Another team's supervisor, an agent of the same team, a member without assignment.
    for persona in (queue.sup_b, queue.agent, queue.plain):
        assert_hidden(persona, item)


def test_a_supervisor_never_sees_the_review_of_their_own_conversation(queue):
    own = created(queue.owner, queue.m_sup_own)
    assert_hidden(queue.sup, own)
    for persona in (queue.owner, queue.admin):
        assert_visible(persona, own)
    # Allow-control: the same supervisor sees another conversation of the team.
    assert_visible(queue.sup, created(queue.owner, queue.m_a2))


def test_only_an_effective_supervisor_assignment_gives_authority(queue):
    item = created(queue.owner, queue.m_a2)
    now = datetime.now(UTC)
    window = {"valid_until": (now + timedelta(minutes=30)).isoformat()}
    effective = queue.assign_team(queue.lapsed, queue.team_a, "supervisor", **window)
    assert_visible(queue.lapsed, item)  # allow-control first

    revoked = queue.owner.delete(f"/api/v1/teams/{queue.team_a}/assignments/{effective}")
    assert revoked.status_code == 204, revoked.text
    assert_hidden(queue.lapsed, item)

    queue.assign_team(
        queue.lapsed,
        queue.team_a,
        "supervisor",
        valid_from=(now - timedelta(hours=2)).isoformat(),
        valid_until=(now - timedelta(hours=1)).isoformat(),
    )
    assert_hidden(queue.lapsed, item)  # expired

    queue.assign_team(
        queue.lapsed,
        queue.team_a,
        "supervisor",
        valid_from=(now + timedelta(hours=1)).isoformat(),
        valid_until=(now + timedelta(hours=2)).isoformat(),
    )
    assert_hidden(queue.lapsed, item)  # not yet valid

    queue.assign_team(queue.lapsed, queue.team_a, "supervisor", **window)
    assert_visible(queue.lapsed, item)


def test_an_item_of_another_workspace_is_404_even_for_its_owner(client, email_outbox, queue):
    other = stranger(client, email_outbox, "review-other-owner@example.com")
    foreign_meeting = _insert_meeting(
        other.workspace_id, created_by=other.user_id, title="Foreign call"
    )
    foreign = created(other, foreign_meeting)
    assert_hidden(queue.owner, foreign)
    assert_visible(other, foreign)
    # A create on the other workspace's conversation is a missing conversation.
    refused = create(queue.owner, foreign_meeting)
    assert refused.status_code == 404 and code(refused) == "meeting_not_found"


def test_a_conversation_without_a_team_is_for_admins_only(queue):
    item = created(queue.owner, queue.m_none)
    for persona in (queue.owner, queue.admin):
        assert_visible(persona, item)
    for persona in (queue.sup, queue.sup_b):
        assert_hidden(persona, item)
    refused = create(queue.sup, queue.meeting())
    assert refused.status_code == 404 and code(refused) == "meeting_not_found"
    assert create(queue.admin, queue.meeting()).status_code == 201


def test_a_deleted_conversation_hides_its_items_and_refuses_a_create(queue):
    item = created(queue.owner, queue.m_a2)
    assert_visible(queue.owner, item)
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == queue.m_a2).values(deleted_at=datetime.now(UTC))
        )
        db.commit()
    for persona in (queue.owner, queue.sup):
        assert_hidden(persona, item)
    refused = create(queue.owner, queue.m_a2)
    assert refused.status_code == 404 and code(refused) == "meeting_not_found"
    # Allow-control: a live conversation of the same team still takes an item.
    assert create(queue.owner, queue.meeting(team_id=queue.team_a)).status_code == 201


def test_record_scope_workspace_gives_a_member_no_review_authority(queue):
    item = created(queue.owner, queue.m_a2)
    # The flat workspace lets the member read the conversation itself.
    meeting = queue.plain.get(f"/api/v1/meetings/{queue.m_a2}")
    assert meeting.status_code == 200, meeting.text
    assert_hidden(queue.plain, item)
    refused = create(queue.plain, queue.meeting(team_id=queue.team_a))
    assert refused.status_code == 404 and code(refused) == "meeting_not_found"
    # Allow-control: a supervisor assignment, and only that, opens it.
    queue.assign_team(queue.plain, queue.team_a, "supervisor")
    assert_visible(queue.plain, item)


def test_authority_follows_the_conversations_current_team(queue):
    item = created(queue.owner, queue.m_a2)
    assert_visible(queue.sup, item)
    assert_hidden(queue.sup_b, item)
    moved = queue.owner.patch(f"/api/v1/meetings/{queue.m_a2}", json={"team_id": queue.team_b})
    assert moved.status_code == 200, moved.text
    assert_hidden(queue.sup, item)
    assert_visible(queue.sup_b, item)
    response = queue.sup_b.get(f"{ITEMS}/{item['id']}")
    assert response.json()["team_id"] == queue.team_b


# --- assignee eligibility (criterion 10) ------------------------------------------------


def test_the_conversations_agent_is_never_its_assignee(queue):
    item = created(queue.owner, queue.m_a)
    refused = act(queue.owner, item, "assign", assignee_user_id=queue.agent.user_id)
    assert refused.status_code == 422 and code(refused) == "review_assignee_is_agent"
    meeting = queue.meeting(team_id=queue.team_a, agent_user_id=queue.agent.user_id)
    refused = create(queue.owner, meeting, assignee_user_id=queue.agent.user_id)
    assert refused.status_code == 422 and code(refused) == "review_assignee_is_agent"

    # Also an admin who is the agent, and the agent check comes before the other checks:
    # a viewer who is the agent is ``review_assignee_is_agent``, not ``ineligible``.
    for persona in (queue.admin, queue.viewer):
        meeting = queue.meeting(team_id=queue.team_a, agent_user_id=persona.user_id)
        own = created(queue.owner, meeting)
        refused = act(queue.owner, own, "assign", assignee_user_id=persona.user_id)
        assert refused.status_code == 422 and code(refused) == "review_assignee_is_agent"
        assert stored(own["id"]).assignee_user_id is None
    # Allow-control: the same item takes the team's supervisor.
    accepted = acted(queue.owner, item, "assign", assignee_user_id=queue.sup.user_id)
    assert accepted["assignee_user_id"] == queue.sup.user_id


def test_an_assignee_needs_an_effective_membership_and_authority(client, email_outbox, queue):
    item = created(queue.owner, queue.m_a2)
    outsider = stranger(client, email_outbox, "review-outsider@example.com")
    for user_id in (
        queue.viewer.user_id,  # a viewer
        outsider.user_id,  # not a member
        queue.plain.user_id,  # a member without authority
        queue.sup_b.user_id,  # another team's supervisor
        queue.agent.user_id,  # an agent of the team, not the conversation's agent
    ):
        refused = act(queue.owner, item, "assign", assignee_user_id=user_id)
        assert refused.status_code == 422, (user_id, refused.text)
        assert code(refused) == "review_assignee_ineligible", user_id
        refused = create(queue.owner, queue.meeting(team_id=queue.team_a), assignee_user_id=user_id)
        assert refused.status_code == 422 and code(refused) == "review_assignee_ineligible"
    assert stored(item["id"]).revision == 1

    # Allow-controls: an admin, the owner and the team's supervisor.
    for persona in (queue.admin, queue.owner, queue.sup):
        item = acted(queue.owner, item, "assign", assignee_user_id=persona.user_id)
        assert item["assignee_user_id"] == persona.user_id
    assert (
        create(
            queue.owner, queue.meeting(team_id=queue.team_a), assignee_user_id=queue.sup.user_id
        ).status_code
        == 201
    )


def test_a_removed_member_is_no_longer_an_eligible_assignee(queue):
    item = created(queue.owner, queue.m_a2)
    queue.assign_team(queue.lapsed, queue.team_a, "supervisor")
    item = acted(queue.owner, item, "assign", assignee_user_id=queue.lapsed.user_id)  # allowed
    item = acted(queue.owner, item, "assign", assignee_user_id=queue.sup.user_id)

    removed = queue.owner.delete(f"/api/v1/members/{queue.membership_ids['lapsed']}")
    assert removed.status_code == 204, removed.text
    refused = act(queue.owner, item, "assign", assignee_user_id=queue.lapsed.user_id)
    assert refused.status_code == 422 and code(refused) == "review_assignee_ineligible"
    assert stored(item["id"]).assignee_user_id == queue.sup.user_id
