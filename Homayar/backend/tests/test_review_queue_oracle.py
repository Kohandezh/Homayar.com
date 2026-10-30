"""HOM-73 unit 2 oracle: the supervisor review queue, through HTTP.

Written from SPEC.md (criteria 1 to 25) and DESIGN.md (sections 3 to 7) before the
implementation was visible.  Rows are seeded in the database only where no route
exists (meeting team and agent, ``deleted_at``, a past due time, a lapsed membership,
scorecard results).  The tests check:

* the two permissions, the route policies, API keys and CSRF;
* the record rules: who sees an item (404 otherwise), and who may be its assignee;
* manual create, the list with its filters and pages, and every item action;
* the revision compare-and-set, also under concurrency (PostgreSQL);
* ``from-sample`` and the audit events of every action.
"""

from __future__ import annotations

import base64
import json
import threading
import types
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from backend.database.saas_models import AuditEvent, Meeting, Membership, ReviewItem, Workspace
from backend.services.rate_limit_service import RateLimiter
from backend.tests import test_authorization_matrix as matrix
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import (
    _cookies,
    _doc_block,
    _insert_meeting,
    _Persona,
    _workspace_with_roles,
)
from backend.tests.test_coverage import WINDOW, seed_call
from backend.tests.test_review_sampling_api import DETAIL_KEYS, SAMPLES
from backend.tests.test_scorecard_results_migration import _add, _parents, _result

ITEMS = "/api/v1/review-items"
FROM_SAMPLE = f"{ITEMS}/from-sample"
ACTIONS = ("assign", "due-time", "start", "complete", "cancel")
STATUSES = ("open", "in_review", "completed", "cancelled")
CANCEL_REASONS = ("duplicate", "out_of_scope", "meeting_unusable", "other")
FIELDS = {
    "id",
    "meeting_id",
    "team_id",
    "scorecard_result_id",
    "source",
    "source_details",
    "status",
    "assignee_user_id",
    "assigned_by_user_id",
    "assigned_at",
    "due_at",
    "is_overdue",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "cancelled_at",
    "cancel_reason",
    "revision",
}
TIMES = (
    "assigned_at",
    "due_at",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "cancelled_at",
)
REVIEW_EVENTS = ("review_item.", "review_sample.enqueued")
ROLES = {
    "admin": "admin",
    "sup": "member",
    "sup2": "member",
    "sup_b": "member",
    "sup_agent": "member",
    "agent": "member",
    "revoked": "member",
    "expired": "member",
    "future": "member",
    "gone": "member",
    "lapsed": "member",
    "lone": "member",
    "viewer": "viewer",
}
# name -> (team, team role); "sup_agent" is also the agent of the world's meeting.
ASSIGNMENTS = {
    "sup": ("a", "supervisor"),
    "sup2": ("a", "supervisor"),
    "sup_b": ("b", "supervisor"),
    "sup_agent": ("a", "supervisor"),
    "agent": ("a", "agent"),
    "revoked": ("a", "supervisor"),
    "expired": ("a", "supervisor"),
    "future": ("a", "supervisor"),
    "gone": ("a", "supervisor"),
    "lapsed": ("a", "supervisor"),
    "viewer": ("a", "supervisor"),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _due(**delta: float) -> str:
    return (_now() + timedelta(**delta)).isoformat()


def _utc(value: str) -> datetime:
    """A response or audit datetime: ISO 8601 with a zero UTC offset."""
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() == timedelta(0), value
    return parsed


def _code(response) -> str:
    return response.json()["error"]["code"]


def _refused(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert _code(response) == code, response.text


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _check_item(item: dict) -> dict:
    assert set(item) == FIELDS, sorted(set(item) ^ FIELDS)
    for name in TIMES:
        if item[name] is not None:
            _utc(item[name])
    return item


def _set_meeting(meeting_id: str, **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(**values))
        db.commit()


def _set_due(item_id: str, when: datetime) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ReviewItem).where(ReviewItem.id == item_id).values(due_at=when))
        db.commit()


def _stored_items(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.workspace_id == workspace_id)
        )


def _events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
        )
        return [row for row in rows if row.event_type.startswith(REVIEW_EVENTS)]


def _seen(workspace_id: str) -> set[str]:
    return {event.id for event in _events(workspace_id)}


def _since(workspace_id: str, seen: set[str]) -> list[AuditEvent]:
    return [event for event in _events(workspace_id) if event.id not in seen]


def _normal(details: dict) -> dict:
    """Audit details with every ``*_at`` string read as a datetime."""
    return {
        key: _utc(value) if key.endswith("_at") and isinstance(value, str) else value
        for key, value in details.items()
    }


def _assert_event(event: AuditEvent, event_type: str, *, actor: str, target: str, details: dict):
    target_type = "workspace" if event_type == "review_sample.enqueued" else "review_item"
    assert (event.event_type, event.target_type, event.target_id) == (
        event_type,
        target_type,
        target,
    )
    assert event.actor_user_id == actor
    assert event.outcome == "success"
    assert _normal(event.details) == _normal(details)


def _signup(client, email_outbox, **kwargs) -> dict:
    RateLimiter.reset_memory()  # five sign-ups an hour per address; a world needs up to fourteen
    return register_user(client, email_outbox, **kwargs)


class _World:
    """One workspace: an owner, the named people, teams A and B, and a team A meeting."""

    def __init__(self, client, email_outbox, monkeypatch, *names: str, record_scope: str = ""):
        self.client = client
        monkeypatch.setattr(matrix, "register_user", _signup)
        self.owner, self.people, self.memberships = _workspace_with_roles(
            client, email_outbox, monkeypatch, roles={name: ROLES[name] for name in names}
        )
        self.workspace_id = self.owner.workspace_id
        if record_scope:
            scoped = self.owner.patch(
                "/api/v1/admin/workspace-settings", json={"record_scope": record_scope}
            )
            assert scoped.status_code == 200, scoped.text
        self.teams = {key: self._team(f"Team {key.upper()}") for key in ("a", "b")}
        for name in names:
            if name in ASSIGNMENTS:
                self._assign(name)
        if "gone" in names:
            removed = self.owner.delete(f"/api/v1/members/{self.memberships['gone']}")
            assert removed.status_code == 204, removed.text
        if "lapsed" in names:
            with inspection_session_factory()() as db:
                membership = db.get(Membership, self.memberships["lapsed"])
                membership.valid_until = _now() - timedelta(seconds=1)
                db.commit()
        self.count = 0
        self.meeting = self.new_meeting(agent="sup_agent" if "sup_agent" in names else None)

    def _team(self, name: str) -> str:
        created = self.owner.post("/api/v1/teams", json={"name": name})
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def _assign(self, name: str) -> None:
        team, role = ASSIGNMENTS[name]
        body = {"user_id": self.user_id(name), "role": role}
        if name == "expired":
            body["valid_from"] = _due(hours=-2)
            body["valid_until"] = _due(hours=-1)
        if name == "future":
            body["valid_from"] = _due(days=1)
        assigned = self.owner.post(f"/api/v1/teams/{self.teams[team]}/assignments", json=body)
        assert assigned.status_code == 201, assigned.text
        if name == "revoked":
            path = f"/api/v1/teams/{self.teams[team]}/assignments/{assigned.json()['id']}"
            assert self.owner.delete(path).status_code == 204

    def persona(self, name: str) -> _Persona:
        return self.owner if name == "owner" else self.people[name]

    def user_id(self, name: str | None) -> str | None:
        return None if name is None else self.persona(name).user_id

    def new_meeting(self, *, team: str | None = "a", agent: str | None = None) -> str:
        self.count += 1
        return _insert_meeting(
            self.workspace_id,
            created_by=self.owner.user_id,
            title=f"Reviewed call {self.count}",
            team_id=self.teams[team] if team else None,
            agent_user_id=self.user_id(agent),
        )

    def create(self, meeting_id: str | None = None, *, by: str = "owner", **body) -> dict:
        payload = {"meeting_id": meeting_id or self.new_meeting(), "due_at": _due(days=1), **body}
        created = self.persona(by).post(ITEMS, json=payload)
        assert created.status_code == 201, created.text
        return _check_item(created.json())

    def get(self, item: dict, *, by: str = "owner") -> dict:
        found = self.persona(by).get(f"{ITEMS}/{item['id']}")
        assert found.status_code == 200, found.text
        return _check_item(found.json())

    def act(
        self, item: dict, action: str, *, by: str = "owner", revision: int | None = None, **body
    ):
        expected = item["revision"] if revision is None else revision
        return self.persona(by).post(
            f"{ITEMS}/{item['id']}/{action}", json={"expected_revision": expected, **body}
        )

    def done(self, item: dict, action: str, *, by: str = "owner", **body) -> dict:
        response = self.act(item, action, by=by, **body)
        assert response.status_code == 200, response.text
        changed = _check_item(response.json())
        assert changed["revision"] == item["revision"] + 1
        return changed

    def body(self, action: str) -> dict:
        """A valid body for ``action`` besides the revision."""
        return {
            "assign": {"assignee_user_id": self.user_id("sup") if "sup" in self.people else None},
            "due-time": {"due_at": _due(days=2)},
            "cancel": {"reason": "duplicate"},
        }.get(action, {})

    def listed(self, *, by: str = "owner", params=None) -> list[str]:
        response = self.persona(by).get(ITEMS, params=params or [])
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "next_cursor"}
        assert body["next_cursor"] is None
        return [_check_item(item)["id"] for item in body["items"]]

    def in_review(self, assignee: str = "sup") -> dict:
        item = self.create()
        item = self.done(item, "assign", assignee_user_id=self.user_id(assignee))
        return self.done(item, "start", by=assignee)


def _assert_visible(world: _World, item: dict, name: str) -> None:
    assert item["id"] in world.listed(by=name), name
    assert world.get(item, by=name)["id"] == item["id"]


def _assert_hidden(world: _World, item: dict, name: str) -> None:
    """Absent from the list and 404 on get and on every action, with valid bodies."""
    persona = world.persona(name)
    assert item["id"] not in world.listed(by=name), name
    _refused(persona.get(f"{ITEMS}/{item['id']}"), 404, "review_item_not_found")
    for action in ACTIONS:
        _refused(
            world.act(item, action, by=name, **world.body(action)), 404, "review_item_not_found"
        )


# --- permissions, route policy, API keys and CSRF (criteria 1 to 3) -----------------------------


def _routes(world: _World, item: dict) -> list[tuple[str, str, dict | None]]:
    path = f"{ITEMS}/{item['id']}"
    return [
        ("GET", ITEMS, None),
        ("GET", path, None),
        ("POST", ITEMS, {"meeting_id": world.new_meeting(), "due_at": _due(days=1)}),
        (
            "POST",
            FROM_SAMPLE,
            {**WINDOW, "seed": 1, "config": {"random_size": 1}, "due_at": _due(days=1)},
        ),
        *(
            (
                "POST",
                f"{path}/{action}",
                {"expected_revision": item["revision"], **world.body(action)},
            )
            for action in ACTIONS
        ),
    ]


def _permissions(persona: _Persona) -> set[str]:
    me = persona.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    [workspace] = [w for w in me.json()["workspaces"] if w["id"] == persona.workspace_id]
    return set(workspace["permissions"])


def test_viewers_api_keys_and_missing_csrf_are_refused_on_every_review_route(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "viewer")
    for name in ("owner", "admin", "sup"):
        assert {"review:read", "review:manage"} <= _permissions(world.persona(name)), name
    assert {"review:read", "review:manage"} & _permissions(world.persona("viewer")) == set()

    item = world.create(world.meeting)
    seen = _seen(world.workspace_id)
    stored = _stored_items(world.workspace_id)

    viewer = world.persona("viewer")
    for method, path, body in _routes(world, item):
        denied = client.request(
            method, path, json=body, cookies=viewer.cookies, headers=viewer.headers
        )
        _refused(denied, 403, "permission_denied")

    for scopes in (["review:read"], ["review:manage"]):
        invalid = _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes)
        _refused(invalid, 422, "invalid_scopes")
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text
    key = _bearer(minted.json()["key"], **{"X-Workspace-ID": world.workspace_id})
    for method, path, body in _routes(world, item):
        _refused(client.request(method, path, json=body, headers=key), 403, "api_key_scope_denied")

    no_csrf = {"X-Workspace-ID": world.workspace_id}
    for method, path, body in _routes(world, item):
        sent = client.request(method, path, json=body, cookies=world.owner.cookies, headers=no_csrf)
        if method == "POST":
            _refused(sent, 403, "csrf_failed")
        else:
            assert sent.status_code == 200, (path, sent.text)

    assert world.get(item) == item
    assert _stored_items(world.workspace_id) == stored
    assert _since(world.workspace_id, seen) == []
    # Allow-controls: the same write with the CSRF header, and a member's write.
    world.done(item, "due-time", due_at=_due(days=3))
    world.create(by="sup")


# The route policies of DESIGN section 5, as test_authorization_matrix.py reads them.
DESIGNED_ROUTES = {
    ("GET", ITEMS): "review:read",
    ("GET", f"{ITEMS}/{{item_id}}"): "review:read",
    ("POST", ITEMS): "review:manage +csrf",
    ("POST", FROM_SAMPLE): "workspace:manage +csrf",
    **{("POST", f"{ITEMS}/{{item_id}}/{action}"): "review:manage +csrf" for action in ACTIONS},
}


def test_the_matrix_documents_the_designed_permissions_and_route_policies():
    documented = {(row[0], row[1]): row[2] for row in _doc_block("routes")}
    assert {route: documented.get(route) for route in DESIGNED_ROUTES} == DESIGNED_ROUTES
    marks = {row[0]: row[1:] for row in _doc_block("permissions")}
    for permission in ("review:read", "review:manage"):
        assert permission in marks, permission
        owner, admin, member, viewer = marks[permission]
        assert (owner, admin, member) == ("✓", "✓", "✓") and viewer != "✓", permission


def test_only_owners_and_admins_enqueue_a_sample(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup")
    body = {**WINDOW, "seed": 1, "config": {"random_size": 1}}
    seen = _seen(world.workspace_id)
    _refused(
        world.persona("sup").post(FROM_SAMPLE, json={**body, "due_at": _due(days=1)}),
        403,
        "permission_denied",
    )
    assert _since(world.workspace_id, seen) == []
    for name in ("admin", "owner"):
        allowed = world.persona(name).post(FROM_SAMPLE, json={**body, "due_at": _due(days=1)})
        assert allowed.status_code == 201, (name, allowed.text)


# --- record-level authorization (criteria 4 to 10) ---------------------------------------------

HIDDEN = ("sup_b", "agent", "revoked", "expired", "future", "sup_agent", "lone")


@pytest.mark.parametrize("record_scope", ["workspace", "team"])
def test_only_admins_and_the_teams_current_supervisors_see_an_item(
    client, email_outbox, monkeypatch, record_scope
):
    world = _World(
        client, email_outbox, monkeypatch, "admin", "sup", *HIDDEN, record_scope=record_scope
    )
    item = world.create(world.meeting)
    for name in ("owner", "admin", "sup"):
        _assert_visible(world, item, name)
    for name in HIDDEN:
        _assert_hidden(world, item, name)
    assert world.get(item) == item

    # A supervisor's create: allowed on the team's meeting, 404 on one they are the agent of.
    world.create(world.new_meeting(), by="sup")
    own = world.new_meeting(agent="sup_agent")
    _refused(
        world.persona("sup_agent").post(ITEMS, json={"meeting_id": own, "due_at": _due(days=1)}),
        404,
        "meeting_not_found",
    )

    # An inactive team confers nothing.
    deactivated = world.owner.patch(f"/api/v1/teams/{world.teams['a']}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text
    _assert_hidden(world, item, "sup")
    _assert_visible(world, item, "admin")


def test_workspace_record_scope_gives_a_member_no_review_authority(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "lone", record_scope="workspace")
    item = world.create(world.meeting)
    lone = world.persona("lone")
    assert lone.get(f"/api/v1/meetings/{world.meeting}").status_code == 200
    _assert_hidden(world, item, "lone")
    _refused(
        lone.post(ITEMS, json={"meeting_id": world.new_meeting(), "due_at": _due(days=1)}),
        404,
        "meeting_not_found",
    )

    # Allow-control: the same member as the team's supervisor.
    assigned = world.owner.post(
        f"/api/v1/teams/{world.teams['a']}/assignments",
        json={"user_id": lone.user_id, "role": "supervisor"},
    )
    assert assigned.status_code == 201, assigned.text
    _assert_visible(world, item, "lone")


def test_an_item_of_another_workspace_is_404_even_for_an_owner(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    theirs = world.create(world.meeting)
    credentials = _signup(client, email_outbox, email="review-other@example.com", name="Other")
    session = login_user(client, credentials)
    other = _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))

    assert theirs["id"] not in [i["id"] for i in other.get(ITEMS).json()["items"]]
    _refused(other.get(f"{ITEMS}/{theirs['id']}"), 404, "review_item_not_found")
    for action in ACTIONS:
        body = {"expected_revision": theirs["revision"], **world.body(action)}
        _refused(
            other.post(f"{ITEMS}/{theirs['id']}/{action}", json=body), 404, "review_item_not_found"
        )
    _refused(
        other.post(ITEMS, json={"meeting_id": world.meeting, "due_at": _due(days=1)}),
        404,
        "meeting_not_found",
    )
    assert world.get(theirs) == theirs

    own = _insert_meeting(other.workspace_id, created_by=other.user_id, title="Their own call")
    created = other.post(ITEMS, json={"meeting_id": own, "due_at": _due(days=1)})
    assert created.status_code == 201, created.text
    assert other.get(f"{ITEMS}/{created.json()['id']}").status_code == 200


def test_a_meeting_without_a_team_is_for_admins_only(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup_b")
    teamless = world.new_meeting(team=None)
    item = world.create(teamless)
    assert item["team_id"] is None
    for name in ("owner", "admin"):
        _assert_visible(world, item, name)
    for name in ("sup", "sup_b"):
        _assert_hidden(world, item, name)
    _refused(
        world.persona("sup").post(
            ITEMS, json={"meeting_id": world.new_meeting(team=None), "due_at": _due(days=1)}
        ),
        404,
        "meeting_not_found",
    )
    _refused(
        world.act(item, "assign", assignee_user_id=world.user_id("sup")),
        422,
        "review_assignee_ineligible",
    )
    assigned = world.done(item, "assign", by="admin", assignee_user_id=world.user_id("admin"))
    assert assigned["assignee_user_id"] == world.user_id("admin")


def test_a_deleted_meeting_hides_its_items_and_refuses_a_create(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup")
    item = world.create(world.meeting)
    kept = world.create()
    _assert_visible(world, item, "admin")

    _set_meeting(world.meeting, deleted_at=_now())
    for name in ("owner", "admin", "sup"):
        _assert_hidden(world, item, name)
        _assert_visible(world, kept, name)
    deleted = world.new_meeting()
    _set_meeting(deleted, deleted_at=_now())
    _refused(
        world.owner.post(ITEMS, json={"meeting_id": deleted, "due_at": _due(days=1)}),
        404,
        "meeting_not_found",
    )
    for missing in (str(uuid.uuid4()),):
        _refused(
            world.owner.post(ITEMS, json={"meeting_id": missing, "due_at": _due(days=1)}),
            404,
            "meeting_not_found",
        )


def test_authority_follows_the_meetings_current_team(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup_b")
    item = world.create(world.meeting)
    assert item["team_id"] == world.teams["a"]
    _assert_visible(world, item, "sup")
    _assert_hidden(world, item, "sup_b")

    _set_meeting(world.meeting, team_id=world.teams["b"])
    _assert_hidden(world, item, "sup")
    _assert_visible(world, item, "sup_b")
    assert world.get(item, by="sup_b")["team_id"] == world.teams["b"]


def test_only_an_eligible_person_becomes_the_assignee(client, email_outbox, monkeypatch):
    world = _World(
        client,
        email_outbox,
        monkeypatch,
        "admin",
        "sup",
        "sup_agent",
        "agent",
        "sup_b",
        "lone",
        "viewer",
        "gone",
        "lapsed",
        "revoked",
        "expired",
        "future",
    )
    credentials = _signup(client, email_outbox, email="review-outsider@example.com", name="Out")
    outsider = login_user(client, credentials)["user"]["id"]
    item = world.create(world.meeting)
    seen = _seen(world.workspace_id)

    _refused(
        world.act(item, "assign", assignee_user_id=world.user_id("sup_agent")),
        422,
        "review_assignee_is_agent",
    )
    for name in (
        "viewer",
        "gone",
        "lapsed",
        "lone",
        "sup_b",
        "agent",
        "revoked",
        "expired",
        "future",
    ):
        _refused(
            world.act(item, "assign", assignee_user_id=world.user_id(name)),
            422,
            "review_assignee_ineligible",
        )
    for stranger in (outsider, str(uuid.uuid4())):
        _refused(
            world.act(item, "assign", assignee_user_id=stranger), 422, "review_assignee_ineligible"
        )
    assert world.get(item) == item
    assert _since(world.workspace_id, seen) == []

    # The agent rule is checked first, and it binds admins and viewers too.
    for agent in ("admin", "viewer"):
        theirs = world.create(world.new_meeting(agent=agent))
        _refused(
            world.act(theirs, "assign", assignee_user_id=world.user_id(agent)),
            422,
            "review_assignee_is_agent",
        )
    payload = {"meeting_id": world.new_meeting(agent="sup"), "due_at": _due(days=1)}
    _refused(
        world.owner.post(ITEMS, json={**payload, "assignee_user_id": world.user_id("sup")}),
        422,
        "review_assignee_is_agent",
    )
    _refused(
        world.owner.post(ITEMS, json={**payload, "assignee_user_id": world.user_id("viewer")}),
        422,
        "review_assignee_ineligible",
    )

    for name in ("admin", "owner", "sup"):
        item = world.done(item, "assign", assignee_user_id=world.user_id(name))
        assert item["assignee_user_id"] == world.user_id(name)
    created = world.create(assignee_user_id=world.user_id("sup"))
    assert created["assignee_user_id"] == world.user_id("sup")


# --- manual create (criteria 11 to 14) ----------------------------------------------------------


def test_a_manual_create_returns_the_designed_item(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    due = _due(days=1)
    seen = _seen(world.workspace_id)
    created = world.owner.post(ITEMS, json={"meeting_id": world.meeting, "due_at": due})
    assert created.status_code == 201, created.text
    item = _check_item(created.json())
    assert {key: item[key] for key in FIELDS - {"id", "created_at", "updated_at"}} == {
        "meeting_id": world.meeting,
        "team_id": world.teams["a"],
        "scorecard_result_id": None,
        "source": "manual",
        "source_details": {},
        "status": "open",
        "assignee_user_id": None,
        "assigned_by_user_id": None,
        "assigned_at": None,
        "due_at": item["due_at"],
        "is_overdue": False,
        "created_by_user_id": world.owner.user_id,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "revision": 1,
    }
    assert _utc(item["due_at"]) == datetime.fromisoformat(due)
    assert _utc(item["created_at"]) == _utc(item["updated_at"])
    assert abs(_utc(item["created_at"]) - _now()) < timedelta(minutes=1)
    assert world.get(item) == item
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.created",
        actor=world.owner.user_id,
        target=item["id"],
        details={
            "meeting_id": world.meeting,
            "source": "manual",
            "due_at": due,
            "assignee_user_id": None,
        },
    )

    by_sup = world.create(by="sup", assignee_user_id=world.user_id("sup"))
    assert by_sup["created_by_user_id"] == world.user_id("sup")
    assert by_sup["assignee_user_id"] == by_sup["assigned_by_user_id"] == world.user_id("sup")
    assert _utc(by_sup["assigned_at"]) == _utc(by_sup["created_at"])

    for extra in ({"workspace_id": world.workspace_id}, {"status": "in_review"}, {"revision": 3}):
        sent = world.owner.post(
            ITEMS, json={"meeting_id": world.new_meeting(), "due_at": _due(days=1), **extra}
        )
        assert sent.status_code == 422, (extra, sent.text)
    for missing in ("meeting_id", "due_at"):
        body = {"meeting_id": world.new_meeting(), "due_at": _due(days=1)}
        del body[missing]
        assert world.owner.post(ITEMS, json=body).status_code == 422, missing


def test_a_meeting_has_at_most_one_active_item(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    first = world.create(world.meeting)
    payload = {"meeting_id": world.meeting, "due_at": _due(days=1)}
    seen = _seen(world.workspace_id)
    duplicate = world.owner.post(ITEMS, json=payload)
    _refused(duplicate, 409, "review_item_exists")
    assert duplicate.json()["error"]["details"] == {"review_item_id": first["id"]}
    started = world.done(
        world.done(first, "assign", assignee_user_id=world.user_id("sup")), "start", by="sup"
    )
    _refused(world.owner.post(ITEMS, json=payload), 409, "review_item_exists")
    assert len(_since(world.workspace_id, seen)) == 2

    world.done(started, "complete", by="sup")
    second = world.owner.post(ITEMS, json=payload)
    assert second.status_code == 201, second.text
    world.done(_check_item(second.json()), "cancel", reason="duplicate")
    third = world.owner.post(ITEMS, json=payload)
    assert third.status_code == 201, third.text
    assert world.listed(params=[("meeting_id", world.meeting)]) == [third.json()["id"]]


def test_the_scorecard_result_must_be_the_meetings_own(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    tenant = {"user_id": world.owner.user_id, "workspace_id": world.workspace_id}
    mine, other = _parents(tenant, key="mine"), _parents(tenant, key="other")
    mine_result = _scorecard(mine)
    other_result = _scorecard(other)
    credentials = _signup(client, email_outbox, email="review-results@example.com", name="R")
    foreign = login_user(client, credentials)
    foreign_result = _scorecard(
        _parents(
            {"user_id": foreign["user"]["id"], "workspace_id": foreign["workspaces"][0]["id"]},
            key="foreign",
        )
    )
    seen = _seen(world.workspace_id)
    for result_id in (other_result, foreign_result, str(uuid.uuid4())):
        refused = world.owner.post(
            ITEMS,
            json={
                "meeting_id": mine["meeting_id"],
                "due_at": _due(days=1),
                "scorecard_result_id": result_id,
            },
        )
        _refused(refused, 422, "review_scorecard_result_invalid")
    assert _since(world.workspace_id, seen) == []
    item = world.create(mine["meeting_id"], scorecard_result_id=mine_result)
    assert item["scorecard_result_id"] == mine_result
    without = world.create(other["meeting_id"], scorecard_result_id=None)
    assert without["scorecard_result_id"] is None


def _scorecard(parents: dict) -> str:
    from backend.database.saas_models import ScorecardResultRecord

    return _add(ScorecardResultRecord, _result(parents))


def _naive(**delta: float) -> str:
    return (_now() + timedelta(**delta)).replace(tzinfo=None).isoformat()


def _refused_due_times() -> list[str]:
    """Naive, now, the past, and more than 90 days ahead."""
    return [
        _naive(days=1),
        _now().isoformat(),
        _due(hours=-1),
        _due(days=90, hours=1),
        _due(days=91),
    ]


def test_the_due_time_rule_on_create_and_on_the_due_time_action(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    item = world.create(world.meeting)
    seen = _seen(world.workspace_id)
    stored = _stored_items(world.workspace_id)
    for due in _refused_due_times():
        _refused(
            world.owner.post(ITEMS, json={"meeting_id": world.new_meeting(), "due_at": due}),
            422,
            "review_due_at_invalid",
        )
        _refused(world.act(item, "due-time", due_at=due), 422, "review_due_at_invalid")
    assert _stored_items(world.workspace_id) == stored
    assert world.get(item) == item
    assert _since(world.workspace_id, seen) == []

    for due in (
        _due(minutes=1, seconds=5),
        _due(days=90),
        (_now() + timedelta(days=2)).isoformat().replace("+00:00", "Z"),
    ):
        created = world.owner.post(ITEMS, json={"meeting_id": world.new_meeting(), "due_at": due})
        assert created.status_code == 201, (due, created.text)
        assert _utc(created.json()["due_at"]) == datetime.fromisoformat(due)
        item = world.done(item, "due-time", due_at=due)
        assert _utc(item["due_at"]) == datetime.fromisoformat(due)

    tehran = timezone(timedelta(hours=3, minutes=30))
    local = (_now() + timedelta(days=3)).astimezone(tehran).isoformat()
    created = world.create(due_at=local)
    assert _utc(created["due_at"]) == datetime.fromisoformat(local)
    item = world.done(item, "due-time", due_at=local)
    assert _utc(item["due_at"]) == datetime.fromisoformat(local)


# --- the list (criteria 15 and 16) --------------------------------------------------------------


def test_the_list_filters(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    open_item = world.create()
    mine = world.done(world.create(), "assign", assignee_user_id=world.user_id("sup"))
    theirs = world.done(world.create(), "assign", assignee_user_id=world.user_id("sup2"))
    reviewing = world.in_review("sup")
    finished = world.done(world.in_review("sup"), "complete", by="sup")
    dropped = world.done(world.create(), "cancel", reason="other")
    late = world.create()
    _set_due(late["id"], _now() - timedelta(hours=1))
    late_done = world.done(world.in_review("sup"), "complete", by="sup")
    _set_due(late_done["id"], _now() - timedelta(hours=1))
    active = {open_item["id"], mine["id"], theirs["id"], reviewing["id"], late["id"]}

    def ids(by: str = "owner", **params) -> set[str]:
        return set(world.listed(by=by, params=[(k, v) for k, vs in params.items() for v in vs]))

    assert ids() == active
    assert ids(status=["completed"]) == {finished["id"], late_done["id"]}
    assert ids(status=["completed", "cancelled"]) == {
        finished["id"],
        late_done["id"],
        dropped["id"],
    }
    assert ids(status=list(STATUSES)) == active | {finished["id"], late_done["id"], dropped["id"]}
    assert ids(status=["in_review"]) == {reviewing["id"]}
    assert ids(by="sup", assignee=["me"]) == {mine["id"], reviewing["id"]}
    assert ids(by="sup2", assignee=["me"]) == {theirs["id"]}
    assert ids(assignee=["me"]) == set()
    assert ids(assignee=["unassigned"]) == {open_item["id"], late["id"]}
    assert ids(assignee=[world.user_id("sup2")]) == {theirs["id"]}
    assert ids(overdue=["true"]) == {late["id"]}
    assert ids(overdue=["false"]) == active - {late["id"]}
    assert ids(overdue=["false"], status=["completed"]) == {finished["id"], late_done["id"]}
    assert ids(meeting_id=[mine["meeting_id"]]) == {mine["id"]}
    assert ids(meeting_id=[str(uuid.uuid4())]) == set()

    bad = {
        "status": ["done", "Open", "open,completed", "", "IN_REVIEW"],
        "assignee": ["ME", "nobody", "Unassigned", world.user_id("sup").upper(), "123"],
        "meeting_id": ["not-a-uuid", mine["meeting_id"].upper(), ""],
    }
    for name, values in bad.items():
        for value in values:
            refused = world.owner.get(ITEMS, params={name: value})
            _refused(refused, 422, "review_filter_invalid")
    for limit in ("0", "101", "x"):
        assert world.owner.get(ITEMS, params={"limit": limit}).status_code == 422, limit
    assert world.owner.get(ITEMS, params={"overdue": "maybe"}).status_code == 422


def test_the_list_is_ordered_and_pages_return_every_visible_item_once(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup", "lone")
    base = _now() + timedelta(days=1)
    dues = [
        base + timedelta(hours=3),
        base + timedelta(hours=1),
        base + timedelta(hours=2),
        base,
        base,
    ]
    items = [world.create(due_at=due.isoformat()) for due in dues]
    hidden = world.create(world.new_meeting(team="b"))
    expected = [i["id"] for i in sorted(items, key=lambda i: (_utc(i["due_at"]), i["id"]))]
    assert world.listed(by="sup") == expected
    assert world.listed() == sorted(
        [*expected, hidden["id"]], key=lambda i: (_utc(world.get({"id": i})["due_at"]), i)
    )
    assert world.listed(by="lone") == []

    pages, cursor = [], None
    for _ in range(10):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = world.persona("sup").get(ITEMS, params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body["items"]) <= 2
        pages.append([item["id"] for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert [len(page) for page in pages] == [2, 2, 1]
    assert [item for page in pages for item in page] == expected
    one = world.persona("sup").get(ITEMS, params={"limit": 100})
    assert [i["id"] for i in one.json()["items"]] == expected

    def encoded(payload: object) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    for bad in (
        "!!!",
        "not base64",
        encoded([]),
        encoded({"v": 2, "due_at": dues[0].isoformat(), "id": items[0]["id"]}),
        encoded({"v": 1, "due_at": "yesterday", "id": items[0]["id"]}),
        encoded({"v": 1, "id": items[0]["id"]}),
        encoded({"v": 1, "created_at": dues[0].isoformat(), "id": items[0]["id"]}),
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
    ):
        _refused(world.persona("sup").get(ITEMS, params={"cursor": bad}), 400, "invalid_cursor")


def test_is_overdue_is_true_only_for_an_active_item_past_its_due_time(
    client, email_outbox, monkeypatch
):
    world = _World(client, email_outbox, monkeypatch, "sup")
    future = world.create()
    late_open = world.create()
    late_review = world.in_review("sup")
    late_done = world.done(world.in_review("sup"), "complete", by="sup")
    late_cancelled = world.done(world.create(), "cancel", reason="other")
    for item in (late_open, late_review, late_done, late_cancelled):
        _set_due(item["id"], _now() - timedelta(minutes=5))
    assert world.get(future)["is_overdue"] is False
    assert world.get(late_open)["is_overdue"] is True
    assert world.get(late_review)["is_overdue"] is True
    assert world.get(late_done)["is_overdue"] is False
    assert world.get(late_cancelled)["is_overdue"] is False
    listed = {i["id"]: i["is_overdue"] for i in world.owner.get(ITEMS).json()["items"]}
    assert listed == {future["id"]: False, late_open["id"]: True, late_review["id"]: True}


# --- item actions (criteria 17 to 19) -----------------------------------------------------------


def test_assign_reassign_and_unassign(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    item = world.create(world.meeting)
    sup, sup2, owner = world.user_id("sup"), world.user_id("sup2"), world.owner.user_id
    seen = _seen(world.workspace_id)

    item = world.done(item, "assign", assignee_user_id=sup)
    assert (item["assignee_user_id"], item["assigned_by_user_id"], item["status"]) == (
        sup,
        owner,
        "open",
    )
    assert _utc(item["assigned_at"]) == _utc(item["updated_at"])
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.assigned",
        actor=owner,
        target=item["id"],
        details={"previous_assignee_user_id": None, "assignee_user_id": sup, "revision": 2},
    )

    seen = _seen(world.workspace_id)
    same = world.act(item, "assign", by="admin", assignee_user_id=sup)
    assert same.status_code == 200, same.text
    assert same.json() == item
    assert _since(world.workspace_id, seen) == []

    started = world.done(item, "start", by="sup")
    assert started["status"] == "in_review" and started["started_at"] is not None
    seen = _seen(world.workspace_id)
    moved = world.done(started, "assign", by="sup", assignee_user_id=sup2)
    assert (moved["status"], moved["started_at"]) == ("open", None)
    assert (moved["assignee_user_id"], moved["assigned_by_user_id"]) == (sup2, sup)
    assert _utc(moved["assigned_at"]) == _utc(moved["updated_at"])
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.assigned",
        actor=sup,
        target=item["id"],
        details={
            "previous_assignee_user_id": sup,
            "assignee_user_id": sup2,
            "revision": moved["revision"],
        },
    )

    reviewing = world.done(moved, "start", by="sup2")
    seen = _seen(world.workspace_id)
    cleared = world.done(reviewing, "assign", assignee_user_id=None)
    assert {
        key: cleared[key]
        for key in (
            "assignee_user_id",
            "assigned_by_user_id",
            "assigned_at",
            "status",
            "started_at",
        )
    } == {
        "assignee_user_id": None,
        "assigned_by_user_id": None,
        "assigned_at": None,
        "status": "open",
        "started_at": None,
    }
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.assigned",
        actor=owner,
        target=item["id"],
        details={
            "previous_assignee_user_id": sup2,
            "assignee_user_id": None,
            "revision": cleared["revision"],
        },
    )
    seen = _seen(world.workspace_id)
    again = world.act(cleared, "assign", assignee_user_id=None)
    assert again.status_code == 200 and again.json() == cleared
    assert _since(world.workspace_id, seen) == []
    assert world.act(cleared, "assign").status_code == 422  # the key is required


def test_only_the_assignee_starts_and_completes_in_order(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin", "sup", "sup2")
    unassigned = world.create()
    _refused(world.act(unassigned, "start", by="admin"), 403, "review_not_assignee")
    item = world.done(world.create(world.meeting), "assign", assignee_user_id=world.user_id("sup"))
    seen = _seen(world.workspace_id)
    for name in ("owner", "admin", "sup2"):
        _refused(world.act(item, "start", by=name), 403, "review_not_assignee")
        _refused(world.act(item, "complete", by=name), 403, "review_not_assignee")
    _refused(world.act(item, "complete", by="sup"), 409, "review_item_state_invalid")
    assert world.get(item) == item
    assert _since(world.workspace_id, seen) == []

    started = world.done(item, "start", by="sup")
    assert started["status"] == "in_review"
    assert _utc(started["started_at"]) == _utc(started["updated_at"])
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.started",
        actor=world.user_id("sup"),
        target=item["id"],
        details={"revision": 3},
    )
    _refused(world.act(started, "start", by="sup"), 409, "review_item_state_invalid")
    _refused(world.act(started, "start", by="admin"), 403, "review_not_assignee")
    _refused(world.act(started, "complete", by="admin"), 403, "review_not_assignee")

    seen = _seen(world.workspace_id)
    completed = world.done(started, "complete", by="sup")
    assert completed["status"] == "completed"
    assert completed["started_at"] == started["started_at"]
    assert _utc(completed["completed_at"]) == _utc(completed["updated_at"])
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.completed",
        actor=world.user_id("sup"),
        target=item["id"],
        details={"revision": 4},
    )

    seen = _seen(world.workspace_id)
    for name in ("sup", "admin"):
        for action in ACTIONS:
            _refused(
                world.act(completed, action, by=name, **world.body(action)),
                409,
                "review_item_closed",
            )
    assert world.get(completed) == completed
    assert _since(world.workspace_id, seen) == []

    by_admin = world.done(world.create(), "assign", assignee_user_id=world.user_id("admin"))
    world.done(world.done(by_admin, "start", by="admin"), "complete", by="admin")


def test_cancel_needs_a_listed_reason_and_closes_the_item(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    item = world.create(world.meeting)
    seen = _seen(world.workspace_id)
    for reason in ("spam", "Duplicate", "out-of-scope", "out of scope", "", None):
        refused = world.act(item, "cancel", by="sup", reason=reason)
        assert refused.status_code == 422, (reason, refused.text)
    assert world.act(item, "cancel", by="sup").status_code == 422
    assert world.get(item) == item
    assert _since(world.workspace_id, seen) == []

    cancelled = world.done(item, "cancel", by="sup", reason="out_of_scope")
    assert (cancelled["status"], cancelled["cancel_reason"], cancelled["started_at"]) == (
        "cancelled",
        "out_of_scope",
        None,
    )
    assert _utc(cancelled["cancelled_at"]) == _utc(cancelled["updated_at"])
    [event] = _since(world.workspace_id, seen)
    _assert_event(
        event,
        "review_item.cancelled",
        actor=world.user_id("sup"),
        target=item["id"],
        details={"reason": "out_of_scope", "revision": 2},
    )
    seen = _seen(world.workspace_id)
    for action in ACTIONS:
        _refused(
            world.act(cancelled, action, by="sup", **world.body(action)), 409, "review_item_closed"
        )
    assert _since(world.workspace_id, seen) == []

    reviewing = world.in_review("sup")
    stopped = world.done(reviewing, "cancel", reason="meeting_unusable")
    assert stopped["started_at"] == reviewing["started_at"] and stopped["completed_at"] is None
    for reason in ("duplicate", "other"):
        assert world.done(world.create(), "cancel", reason=reason)["cancel_reason"] == reason


# --- stale writes and concurrency (criteria 20 and 21) ------------------------------------------


def test_a_stale_revision_changes_nothing_on_every_action(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "lone")
    ready = {
        "assign": world.create,
        "due-time": world.create,
        "start": lambda: world.done(
            world.create(), "assign", assignee_user_id=world.user_id("sup")
        ),
        "complete": lambda: world.in_review("sup"),
        "cancel": world.create,
    }
    actor = {"start": "sup", "complete": "sup"}
    for action, make in ready.items():
        item = make()
        current = item["revision"]
        seen = _seen(world.workspace_id)
        for wrong in {current + 1, current + 7, max(current - 1, 1)} - {current}:
            stale = world.act(
                item, action, by=actor.get(action, "owner"), revision=wrong, **world.body(action)
            )
            _refused(stale, 409, "review_item_revision_conflict")
            assert stale.json()["error"]["details"] == {"current_revision": current}, action
        assert world.get(item) == item
        assert _since(world.workspace_id, seen) == []
        hidden = world.act(item, action, by="lone", revision=current + 1, **world.body(action))
        _refused(hidden, 404, "review_item_not_found")
        changed = world.done(item, action, by=actor.get(action, "owner"), **world.body(action))
        assert changed["revision"] == current + 1
        assert len(_since(world.workspace_id, seen)) == 1

    closed = world.done(world.in_review("sup"), "complete", by="sup")
    stale = world.act(closed, "cancel", revision=closed["revision"] + 1, reason="other")
    _refused(stale, 409, "review_item_revision_conflict")


def test_the_expected_revision_is_a_strict_positive_integer(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup")
    item = world.done(world.create(), "assign", assignee_user_id=world.user_id("sup"))
    assert item["revision"] == 2
    for value in (True, "2", 2.0, 0, -1, None):
        sent = world.persona("sup").post(
            f"{ITEMS}/{item['id']}/start", json={"expected_revision": value}
        )
        assert sent.status_code == 422, (value, sent.text)
    assert world.persona("sup").post(f"{ITEMS}/{item['id']}/start", json={}).status_code == 422
    extra = world.act(item, "start", by="sup", status="completed")
    assert extra.status_code == 422, extra.text
    assert world.get(item) == item
    world.done(item, "start", by="sup")


def _race(client, sends: list[tuple[_Persona, str, dict]]) -> list:
    barrier = threading.Barrier(len(sends))
    results: list = [None] * len(sends)

    def send(index: int, persona: _Persona, path: str, body: dict) -> None:
        try:
            own = TestClient(client.app, base_url=str(client.base_url))
            barrier.wait(timeout=15)
            results[index] = own.post(
                path, json=body, cookies=persona.cookies, headers=persona.headers
            )
        except BaseException as exc:  # surfaced by the assertion below
            results[index] = exc

    threads = [threading.Thread(target=send, args=(i, *s)) for i, s in enumerate(sends)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a request hung"
    assert all(not isinstance(result, BaseException) for result in results), results
    return results


@pytest.mark.postgres
def test_two_concurrent_writes_with_one_revision_never_both_succeed(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "admin", "sup")
    for _ in range(3):
        item = world.create()
        seen = _seen(world.workspace_id)
        path = f"{ITEMS}/{item['id']}/due-time"
        results = _race(
            client,
            [
                (world.owner, path, {"expected_revision": 1, "due_at": _due(days=2)}),
                (world.persona("admin"), path, {"expected_revision": 1, "due_at": _due(days=3)}),
            ],
        )
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        [conflict] = [r for r in results if r.status_code == 409]
        assert _code(conflict) == "review_item_revision_conflict"
        assert world.get(item)["revision"] == 2
        assert len(_since(world.workspace_id, seen)) == 1


@pytest.mark.postgres
def test_two_concurrent_creates_for_one_meeting_give_one_201_and_one_409(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _World(client, email_outbox, monkeypatch, "admin")
    for _ in range(3):
        meeting = world.new_meeting()
        body = {"meeting_id": meeting, "due_at": _due(days=1)}
        results = _race(client, [(world.owner, ITEMS, body), (world.persona("admin"), ITEMS, body)])
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        [created] = [r for r in results if r.status_code == 201]
        [refused] = [r for r in results if r.status_code == 409]
        assert _code(refused) == "review_item_exists"
        assert refused.json()["error"]["details"] == {"review_item_id": created.json()["id"]}
        assert world.listed(params=[("meeting_id", meeting)]) == [created.json()["id"]]


# --- from-sample (criteria 14, 22 and 24) -------------------------------------------------------


def _sampled(world: _World, count: int) -> list[str]:
    with inspection_session_factory()() as db:
        organization_id = db.scalar(
            select(Workspace.organization_id).where(Workspace.id == world.workspace_id)
        )
    tenant = types.SimpleNamespace(
        workspace_id=world.workspace_id,
        owner_id=world.owner.user_id,
        organization_id=organization_id,
    )
    return [seed_call(tenant) for _ in range(count)]


def test_from_sample_queues_each_selection_in_draw_order(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin")
    meetings = _sampled(world, 5)
    taken = world.create(meetings[0])
    world.done(world.create(meetings[1]), "cancel", reason="other")
    due = _due(days=2)
    body = {**WINDOW, "seed": 7, "config": {"random_size": 10}, "due_at": due}
    seen = _seen(world.workspace_id)

    response = world.persona("admin").post(FROM_SAMPLE, json=body)
    assert response.status_code == 201, response.text
    result = response.json()
    assert set(result) == {"coverage_version", "window", "draw", "created", "skipped_meeting_ids"}
    draw = result["draw"]
    order = [selection["item_id"] for selection in draw["selections"]]
    assert set(order) == set(meetings)
    assert result["skipped_meeting_ids"] == [taken["meeting_id"]]
    created = [_check_item(item) for item in result["created"]]
    assert [item["meeting_id"] for item in created] == [
        m for m in order if m != taken["meeting_id"]
    ]
    reasons = {selection["item_id"]: selection["reasons"] for selection in draw["selections"]}
    admin = world.user_id("admin")
    for item in created:
        assert {
            key: item[key]
            for key in (
                "source",
                "status",
                "assignee_user_id",
                "assigned_by_user_id",
                "assigned_at",
                "created_by_user_id",
                "revision",
            )
        } == {
            "source": "sample",
            "status": "open",
            "assignee_user_id": None,
            "assigned_by_user_id": None,
            "assigned_at": None,
            "created_by_user_id": admin,
            "revision": 1,
        }
        assert _utc(item["due_at"]) == datetime.fromisoformat(due)
        assert item["source_details"] == {
            "sampler_version": draw["sampler_version"],
            "seed": 7,
            "config_digest": draw["config_digest"],
            "selection_digest": draw["selection_digest"],
            "reasons": reasons[item["meeting_id"]],
        }
        assert world.get(item) == item

    [event] = _since(world.workspace_id, seen)
    audit = {
        "seed": 7,
        "sampler_version": draw["sampler_version"],
        "coverage_version": result["coverage_version"],
        "window_from_at": WINDOW["from"],
        "window_to_at": WINDOW["to"],
        "config": draw["config"],
        "config_digest": draw["config_digest"],
        "population_count": draw["population_count"],
        "population_digest": draw["population_digest"],
        "selection_digest": draw["selection_digest"],
        "selected_count": len(order),
    }
    assert set(audit) == DETAIL_KEYS
    _assert_event(
        event,
        "review_sample.enqueued",
        actor=admin,
        target=world.workspace_id,
        details={**audit, "created_count": len(created), "skipped_count": 1, "due_at": due},
    )

    # The same seed and config on the unchanged population draw the same selections.
    drawn = world.owner.post(
        SAMPLES, json={key: body[key] for key in ("from", "to", "seed", "config")}
    )
    assert drawn.status_code == 200, drawn.text
    assert drawn.json()["draw"] == draw
    again = world.owner.post(FROM_SAMPLE, json=body)
    assert again.status_code == 201, again.text
    assert again.json()["draw"] == draw
    assert again.json()["created"] == []
    assert set(again.json()["skipped_meeting_ids"]) == set(meetings)


def test_from_sample_refusals_create_nothing_and_record_nothing(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin")
    _sampled(world, 2)
    stored = _stored_items(world.workspace_id)
    seen = _seen(world.workspace_id)
    good = {**WINDOW, "seed": 3, "config": {"random_size": 2}, "due_at": _due(days=1)}
    cases = [
        ({"config": {"random_size": 501}}, "sampling_config_invalid"),
        ({"config": {}}, "sampling_config_empty"),
        ({"seed": -1}, "seed_invalid"),
        ({"seed": True}, "seed_invalid"),
        ({"from": "2026-03-01T09:00:00"}, "coverage_window_invalid"),
        ({"from": WINDOW["to"], "to": WINDOW["from"]}, "coverage_window_invalid"),
        *(({"due_at": due}, "review_due_at_invalid") for due in _refused_due_times()),
    ]
    for change, code in cases:
        _refused(world.owner.post(FROM_SAMPLE, json={**good, **change}), 422, code)
    for shape in ({"extra": True}, {"due_at": None}):
        assert world.owner.post(FROM_SAMPLE, json={**good, **shape}).status_code == 422, shape
    without_due = {key: value for key, value in good.items() if key != "due_at"}
    assert world.owner.post(FROM_SAMPLE, json=without_due).status_code == 422
    assert _stored_items(world.workspace_id) == stored
    assert _since(world.workspace_id, seen) == []

    for due in (_due(minutes=1, seconds=5), _due(days=90)):
        allowed = world.owner.post(FROM_SAMPLE, json={**good, "due_at": due})
        assert allowed.status_code == 201, allowed.text
    assert _stored_items(world.workspace_id) == stored + 2


def test_an_empty_selection_is_a_201_with_empty_lists(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin")
    seen = _seen(world.workspace_id)
    response = world.owner.post(
        FROM_SAMPLE,
        json={**WINDOW, "seed": 5, "config": {"random_size": 3}, "due_at": _due(days=1)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["created"], body["skipped_meeting_ids"], body["draw"]["selections"]) == (
        [],
        [],
        [],
    )
    [event] = _since(world.workspace_id, seen)
    assert event.event_type == "review_sample.enqueued"
    assert (event.details["created_count"], event.details["skipped_count"]) == (0, 0)


def test_admin_review_samples_still_only_records_a_draw(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "admin")
    _sampled(world, 3)
    drawn = world.owner.post(SAMPLES, json={**WINDOW, "seed": 9, "config": {"random_size": 2}})
    assert drawn.status_code == 200, drawn.text
    assert set(drawn.json()) == {"coverage_version", "window", "draw"}
    assert _stored_items(world.workspace_id) == 0
    with inspection_session_factory()() as db:
        types_ = [
            event.event_type
            for event in db.scalars(
                select(AuditEvent).where(AuditEvent.workspace_id == world.workspace_id)
            )
            if event.event_type.startswith("review_")
        ]
    assert types_ == ["review_sample.drawn"]


# --- the audit trail of one item (criterion 23) -------------------------------------------------


def test_each_action_records_exactly_one_designed_event(client, email_outbox, monkeypatch):
    world = _World(client, email_outbox, monkeypatch, "sup", "sup2")
    sup, owner = world.user_id("sup"), world.owner.user_id
    due = _due(days=1)
    seen = _seen(world.workspace_id)
    created = world.owner.post(
        ITEMS, json={"meeting_id": world.meeting, "due_at": due, "assignee_user_id": sup}
    )
    assert created.status_code == 201, created.text
    item = _check_item(created.json())
    later = _due(days=5)
    steps = [
        (
            "review_item.created",
            owner,
            {
                "meeting_id": world.meeting,
                "source": "manual",
                "due_at": due,
                "assignee_user_id": sup,
            },
        ),
    ]
    moved = world.done(item, "due-time", due_at=later)
    steps.append(
        ("review_item.due_changed", owner, {"previous_due_at": due, "due_at": later, "revision": 2})
    )
    started = world.done(moved, "start", by="sup")
    steps.append(("review_item.started", sup, {"revision": 3}))
    world.done(started, "complete", by="sup")
    steps.append(("review_item.completed", sup, {"revision": 4}))

    events = _since(world.workspace_id, seen)
    assert len(events) == len(steps)
    for event, (event_type, actor, details) in zip(events, steps, strict=True):
        _assert_event(event, event_type, actor=actor, target=item["id"], details=details)
        for value in event.details.values():
            assert value is None or isinstance(value, int | str), event.details

    refused = _seen(world.workspace_id)
    naive = {"meeting_id": world.new_meeting(), "due_at": _naive(days=1)}
    _refused(world.owner.post(ITEMS, json=naive), 422, "review_due_at_invalid")
    _refused(world.act(started, "start", by="sup2"), 409, "review_item_revision_conflict")
    assert _since(world.workspace_id, refused) == []


# --- the unit 2 documents (DESIGN section 7) ----------------------------------------------------


def test_the_sampling_readme_points_at_the_enqueue_route():
    readme = Path(__file__).resolve().parents[1] / "review_sampling" / "README.md"
    text = " ".join(readme.read_text(encoding="utf-8").split())
    assert "POST /api/v1/review-items/from-sample" in text
    assert "Storing the selected items as review work is HOM-73" not in text
