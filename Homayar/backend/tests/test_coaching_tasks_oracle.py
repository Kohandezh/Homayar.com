"""HOM-77 PR 2 oracle: coaching tasks from accepted findings, through HTTP and the sweep.

Written from SPEC-pr2.md and design D2 to D6 before the implementation was visible.
Every refusal is paired with the closest request that must still succeed, and every
refusal must write nothing: no task change, no audit event and no notification.  Rows
are changed in the database only where no route exists (a call's agent set to nobody, a
due time moved into the reminder window or the past, a removed user).
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from backend.database.saas_models import AuditEvent, CoachingTask, Meeting, Notification
from backend.security.authorization import API_KEY_SCOPES
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import MATRIX_DOC, _doc_block, _Persona
from backend.tests.test_review_appeals_oracle import (
    _assign,
    _body,
    _change,
    _done,
    _filed,
    _outsider,
    _refused,
    _review,
    _uphold,
)
from backend.tests.test_review_decisions_oracle import ACCEPT, _Case, _override
from backend.tests.test_review_queue_oracle import (
    _permissions,
    _race,
    _requires_postgres,
    _set_meeting,
    _World,
)

ITEMS = "/api/v1/review-items"
TASKS = "/api/v1/coaching-tasks"
MY_TASKS = "/api/v1/my-coaching-tasks"
SWEEP_MODULE = "backend.jobs.coaching_reminders"
INSTRUCTION = "Ask for the customer's name before the offer."
ACK_NOTE = "I will practise it this week."
FOLLOW_UP = "The greeting was clear in the next three calls."
CANCEL_NOTE = "Coached twice already."
FAILED = ("greeting", "fee_explained", "callback")
NOT_FAILED = ("closing", "agenda")
OUTCOMES = ("improved", "partially_improved", "not_improved")
REASONS = ("finding_overturned", "agent_changed", "duplicate", "other")
KINDS = (
    "coaching_task.assigned",
    "coaching_task.owner_assigned",
    "coaching_task.acknowledged",
    "coaching_task.due_soon",
    "coaching_task.overdue",
    "coaching_task.closed",
    "coaching_task.cancelled",
)
AGENT_HIDDEN = ("created_by_user_id", "closed_by_user_id", "cancelled_by_user_id")
BLANKS = ("", " ", "   ", "\t\n", " \n\t ", "　")
WRITES = ("reassign", "acknowledge", "close", "cancel")


def _now() -> datetime:
    return datetime.now(UTC)


def _due(**delta: float) -> str:
    return (_now() + timedelta(**delta)).isoformat()


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None, value
    return parsed


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _code(response) -> str:
    return response.json()["error"]["code"]


def _denied(response, statuses: set[int], codes: set[str] | None = None) -> None:
    assert response.status_code in statuses, response.text
    if codes is not None:
        assert _code(response) in codes, response.text


def _world(client, email_outbox, monkeypatch, *names: str) -> _World:
    return _World(client, email_outbox, monkeypatch, *names)


def _create_path(case: _Case) -> str:
    return f"{ITEMS}/{case.id}/coaching-tasks"


def _create(
    world: _World,
    case: _Case,
    criterion: str = "greeting",
    *,
    by: str = "sup",
    owner: str | None = "sup2",
    **extra: object,
):
    body = {
        "criterion_id": criterion,
        "owner_user_id": world.user_id(owner),
        "due_at": _due(days=3),
        "instruction": INSTRUCTION,
        **extra,
    }
    return world.persona(by).post(_create_path(case), json=body)


def _created(world: _World, case: _Case, criterion: str = "greeting", **kwargs) -> dict:
    response = _create(world, case, criterion, **kwargs)
    assert response.status_code == 201, (criterion, response.text)
    task = response.json()
    assert task["id"], task
    return task


def _row(task: dict | str) -> CoachingTask:
    task_id = task if isinstance(task, str) else task["id"]
    with inspection_session_factory()() as db:
        row = db.get(CoachingTask, task_id)
        assert row is not None, task_id
        return row


def _rows(world: _World) -> list[CoachingTask]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(CoachingTask)
                .where(CoachingTask.workspace_id == world.workspace_id)
                .order_by(CoachingTask.id)
            )
        )


def _revision(task: dict) -> int:
    return _row(task).revision


def _set_task(task: dict, **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(CoachingTask).where(CoachingTask.id == task["id"]).values(**values))
        db.commit()


def _act(world: _World, task: dict, action: str, *, by: str, revision: int | None = None, **body):
    expected = _revision(task) if revision is None else revision
    return world.persona(by).post(
        f"{TASKS}/{task['id']}/{action}", json={"expected_revision": expected, **body}
    )


def _ok(world: _World, task: dict, action: str, *, by: str, **body) -> dict:
    before = _revision(task)
    response = _act(world, task, action, by=by, **body)
    assert response.status_code == 200, (action, by, response.text)
    assert _revision(task) == before + 1, action
    return response.json()


def _acknowledge(world: _World, task: dict, *, by: str = "agent", **body) -> dict:
    return _ok(world, task, "acknowledge", by=by, **body)


def _close(
    world: _World, task: dict, *, by: str = "sup2", outcome: str = "improved", note=FOLLOW_UP
) -> dict:
    return _ok(world, task, "close", by=by, follow_up_outcome=outcome, follow_up_note=note)


def _cancel(world: _World, task: dict, *, by: str = "sup2", reason: str = "duplicate", **body):
    return _ok(world, task, "cancel", by=by, cancel_reason=reason, **body)


def _reassign(world: _World, task: dict, owner: str, *, by: str = "sup") -> dict:
    return _ok(world, task, "reassign", by=by, owner_user_id=world.user_id(owner))


def _write_body(world: _World, action: str) -> dict:
    return {
        "reassign": {"owner_user_id": world.user_id("sup")},
        "acknowledge": {},
        "close": {"follow_up_outcome": "improved", "follow_up_note": FOLLOW_UP},
        "cancel": {"cancel_reason": "duplicate"},
    }[action]


def _detail(world: _World, task: dict, by: str):
    return world.persona(by).get(f"{TASKS}/{task['id']}")


def _view(world: _World, task: dict, by: str) -> dict:
    response = _detail(world, task, by)
    assert response.status_code == 200, (by, response.text)
    view = response.json()
    assert view["id"] == task["id"], view
    return view


def _items(response) -> list[dict]:
    assert response.status_code == 200, response.text
    body = response.json()
    if isinstance(body, dict):
        assert "items" in body, body
        return body["items"]
    assert isinstance(body, list), body
    return body


def _page(response) -> tuple[list[dict], str | None]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"items", "next_cursor"}, body
    return body["items"], body["next_cursor"]


def _listed(persona: _Persona, path: str = TASKS, **params) -> list[str]:
    items, cursor = _page(persona.get(path, params=params))
    assert cursor is None, cursor
    return [item["id"] for item in items]


def _mine(world: _World, by: str, **params) -> list[str]:
    return _listed(world.persona(by), MY_TASKS, **params)


def _supervised(world: _World, by: str, **params) -> list[str]:
    return _listed(world.persona(by), TASKS, **params)


def _history(world: _World, task: dict, by: str) -> list[dict]:
    entries = _items(world.persona(by).get(f"{TASKS}/{task['id']}/history"))
    for entry in entries:
        assert {"event_type", "occurred_at", "actor_user_id", "actor_role", "details"} <= set(
            entry
        ), entry
        _time(entry["occurred_at"])
    return entries


def _notes(world: _World, task: dict | None = None, kind: str | None = None) -> list:
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(
                select(Notification)
                .where(Notification.workspace_id == world.workspace_id)
                .order_by(Notification.user_id, Notification.id)
            )
        )
    return [
        row
        for row in rows
        if (kind is None or row.kind == kind)
        and (task is None or (row.data or {}).get("coaching_task_id") == task["id"])
    ]


def _recipients(world: _World, task: dict, kind: str) -> list[str]:
    return sorted(row.user_id for row in _notes(world, task, kind))


def _ids(world: _World, *names: str) -> list[str]:
    return sorted(world.user_id(name) for name in names)


def _events(world: _World, task: dict | None = None) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == world.workspace_id)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )
    return [
        row
        for row in rows
        if row.event_type.startswith("coaching_task.")
        and (task is None or row.target_id == task["id"])
    ]


def _event_types(world: _World, task: dict) -> list[str]:
    return [event.event_type for event in _events(world, task)]


def _state(world: _World) -> str:
    with inspection_session_factory()() as db:
        tasks = [
            {c.key: getattr(row, c.key) for c in CoachingTask.__mapper__.column_attrs}
            for row in db.scalars(
                select(CoachingTask)
                .where(CoachingTask.workspace_id == world.workspace_id)
                .order_by(CoachingTask.id)
            )
        ]
        notes = [
            {c.key: getattr(row, c.key) for c in Notification.__mapper__.column_attrs}
            for row in db.scalars(
                select(Notification)
                .where(Notification.workspace_id == world.workspace_id)
                .order_by(Notification.id)
            )
        ]
        events = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.workspace_id == world.workspace_id)
        )
    return json.dumps({"tasks": tasks, "notes": notes, "events": events}, default=str)


def _dump(model) -> str:
    with inspection_session_factory()() as db:
        rows = list(db.scalars(select(model)))
        return "\n".join(
            json.dumps(
                {c.key: getattr(row, c.key) for c in model.__mapper__.column_attrs},
                default=str,
                ensure_ascii=False,
            )
            for row in rows
        )


def _move(world: _World, case: _Case, name: str | None) -> None:
    moved = world.owner.patch(
        f"/api/v1/meetings/{case.meeting_id}", json={"agent_user_id": world.user_id(name)}
    )
    assert moved.status_code == 200, moved.text


def _agent_of(case: _Case) -> str | None:
    with inspection_session_factory()() as db:
        return db.get(Meeting, case.meeting_id).agent_user_id


def _cannot_act(world: _World, task: dict, name: str, statuses=(403, 404, 422)) -> None:
    before = _state(world)
    for action in ("reassign", "close", "cancel"):
        _denied(_act(world, task, action, by=name, **_write_body(world, action)), set(statuses))
    assert _state(world) == before, name


def _invisible(world: _World, task: dict, name: str) -> None:
    persona = world.persona(name)
    before = _state(world)
    listed = persona.get(TASKS)
    if listed.status_code == 200:
        assert task["id"] not in {item["id"] for item in _page(listed)[0]}, name
    else:
        _refused(listed, 403, "permission_denied")
    _refused(_detail(world, task, name), 404, "coaching_task_not_found")
    _refused(persona.get(f"{TASKS}/{task['id']}/history"), 404, "coaching_task_not_found")
    for action in WRITES:
        _denied(
            _act(world, task, action, by=name, **_write_body(world, action)),
            {403, 404},
            {"coaching_task_not_found", "permission_denied"},
        )
    assert _state(world) == before, name


def _sweep_function():
    module = importlib.import_module(SWEEP_MODULE)
    required = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )
    found = [
        value
        for name, value in sorted(vars(module).items())
        if not name.startswith("_")
        and inspect.isfunction(value)
        and value.__module__ == module.__name__
        and not any(
            parameter.default is inspect.Parameter.empty and parameter.kind in required
            for parameter in inspect.signature(value).parameters.values()
        )
    ]
    if len(found) > 1:
        tasks = Path(importlib.import_module("backend.jobs.tasks").__file__).read_text()
        found = [value for value in found if re.search(rf"import[^\n]*\b{value.__name__}\b", tasks)]
    assert len(found) == 1, [value.__name__ for value in found]
    return found[0]


def _sweep(*worlds: _World) -> object:
    sweep = _sweep_function()
    if "workspace_ids" in inspect.signature(sweep).parameters:
        return sweep(workspace_ids=[world.workspace_id for world in worlds])
    return sweep()


def _setup(client, email_outbox, monkeypatch, *extra: str):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent", *extra)
    case = _review(world)
    return world, case, _created(world, case)


def _routes(world: _World, case: _Case, task: dict) -> list[tuple[str, str, str, dict | None]]:
    path = f"{TASKS}/{task['id']}"
    revision = {"expected_revision": _revision(task)}
    return [
        (
            "sup",
            "POST",
            _create_path(case),
            {
                "criterion_id": "fee_explained",
                "owner_user_id": world.user_id("sup2"),
                "due_at": _due(days=3),
                "instruction": INSTRUCTION,
            },
        ),
        ("sup", "GET", TASKS, None),
        ("sup", "GET", path, None),
        ("sup", "GET", f"{path}/history", None),
        ("sup", "POST", f"{path}/reassign", {**revision, "owner_user_id": world.user_id("sup")}),
        ("agent", "POST", f"{path}/acknowledge", revision),
        (
            "sup2",
            "POST",
            f"{path}/close",
            {**revision, "follow_up_outcome": "improved", "follow_up_note": FOLLOW_UP},
        ),
        ("sup2", "POST", f"{path}/cancel", {**revision, "cancel_reason": "duplicate"}),
        ("agent", "GET", MY_TASKS, None),
        ("agent", "GET", path, None),
    ]


def test_every_role_holds_coaching_respond_and_no_api_key_scope_does(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "viewer")
    for name in ("owner", "admin", "sup", "viewer"):
        assert "coaching:respond" in _permissions(world.persona(name)), name
    assert "coaching:respond" not in {scope.value for scope in API_KEY_SCOPES}
    for scopes in (["coaching:respond"], ["meeting:read", "coaching:respond"]):
        _refused(
            _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes),
            422,
            "invalid_scopes",
        )
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text


def test_an_api_key_with_every_key_scope_is_refused_on_every_coaching_route(
    client, email_outbox, monkeypatch
):
    world, case, task = _setup(client, email_outbox, monkeypatch)
    scopes = sorted(scope.value for scope in API_KEY_SCOPES)
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes)
    assert minted.status_code == 201, minted.text
    key = _bearer(minted.json()["key"], **{"X-Workspace-ID": world.workspace_id})
    before = _state(world)
    for _, method, path, body in _routes(world, case, task):
        _refused(client.request(method, path, json=body, headers=key), 403, "api_key_scope_denied")
    assert _state(world) == before

    assert task["id"] in _supervised(world, "sup")
    _acknowledge(world, task)


def test_every_write_without_the_csrf_token_is_refused_and_reads_still_work(
    client, email_outbox, monkeypatch
):
    world, case, task = _setup(client, email_outbox, monkeypatch)
    before = _state(world)
    for name, method, path, body in _routes(world, case, task):
        persona = world.persona(name)
        sent = client.request(
            method,
            path,
            json=body,
            cookies=persona.cookies,
            headers={"X-Workspace-ID": world.workspace_id},
        )
        if method == "POST":
            _refused(sent, 403, "csrf_failed")
        else:
            assert sent.status_code == 200, (path, sent.text)
    assert _state(world) == before

    _created(world, case, "fee_explained")
    _acknowledge(world, task)


def test_a_viewer_holds_only_the_agent_side(client, email_outbox, monkeypatch):
    world, case, task = _setup(client, email_outbox, monkeypatch, "viewer")
    viewer = world.persona("viewer")
    before = _state(world)
    assert _mine(world, "viewer") == []
    _refused(viewer.get(TASKS), 403, "permission_denied")
    _refused(_create(world, case, "fee_explained", by="viewer"), 403, "permission_denied")
    for action in ("reassign", "close", "cancel"):
        _refused(
            _act(world, task, action, by="viewer", **_write_body(world, action)),
            403,
            "permission_denied",
        )
    _refused(_detail(world, task, "viewer"), 404, "coaching_task_not_found")
    _refused(_act(world, task, "acknowledge", by="viewer"), 404, "coaching_task_not_found")
    assert _state(world) == before

    coached = _review(world, agent="viewer")
    own = _created(world, coached)
    assert _mine(world, "viewer") == [own["id"]]
    assert _view(world, own, "viewer")["status"] == "assigned"
    _acknowledge(world, own, by="viewer")
    assert _row(own).status == "acknowledged"


def _placeholders(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


DESIGNED_ROUTES = {
    ("POST", f"{ITEMS}/{{}}/coaching-tasks"): "review:manage +csrf",
    ("GET", TASKS): "review:read",
    ("POST", f"{TASKS}/{{}}/reassign"): "review:manage +csrf",
    ("POST", f"{TASKS}/{{}}/acknowledge"): "coaching:respond +csrf",
    ("POST", f"{TASKS}/{{}}/close"): "review:manage +csrf",
    ("POST", f"{TASKS}/{{}}/cancel"): "review:manage +csrf",
    ("GET", MY_TASKS): "coaching:respond",
    ("GET", f"{TASKS}/{{}}"): "coaching:respond",
    ("GET", f"{TASKS}/{{}}/history"): "coaching:respond",
}


def test_the_matrix_and_the_session_test_list_coaching_respond_for_every_role():
    marks = {row[0]: row[1:] for row in _doc_block("permissions")}
    assert marks.get("coaching:respond", [])[:4] == ["✓", "✓", "✓", "✓"], marks.get(
        "coaching:respond"
    )
    for permission in ("review:read", "review:manage"):
        owner, admin, member, viewer = marks[permission][:4]
        assert (owner, admin, member) == ("✓", "✓", "✓") and viewer != "✓", permission
    scopes = {row[0]: row[1:] for row in _doc_block("api-key-scopes")}
    assert "coaching:respond" in scopes
    assert scopes["coaching:respond"][0] != "✓"
    routes = {(row[0], _placeholders(row[1])): row[2] for row in _doc_block("routes")}
    assert {route: routes.get(route) for route in DESIGNED_ROUTES} == DESIGNED_ROUTES
    text = MATRIX_DOC.read_text(encoding="utf-8")
    assert "HOM-77" in text
    session_test = Path(__file__).with_name("test_session_permissions.py").read_text()
    assert session_test.count("coaching:respond") >= 1


def _check_note(world: _World, row: Notification, task: dict, case: _Case) -> None:
    assert row.data == {
        "coaching_task_id": task["id"],
        "review_item_id": case.id,
        "meeting_id": case.meeting_id,
    }, row.data
    assert row.title.strip() and row.body.strip(), row.kind
    assert row.read_at is None


def test_a_supervisor_creates_an_assigned_task_from_a_failed_finding(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "agent")
    case = _review(world)
    due = _now() + timedelta(days=3)
    response = _create(world, case, "greeting", due_at=due.isoformat())
    assert response.status_code == 201, response.text
    task = response.json()
    assert (task["status"], task["revision"]) == ("assigned", 1)
    row = _row(task)
    assert (row.status, row.revision, row.finding_source) == ("assigned", 1, "human_result")
    assert (row.review_item_id, row.scorecard_result_id, row.meeting_id, row.criterion_id) == (
        case.id,
        case.result_id,
        case.meeting_id,
        "greeting",
    )
    assert row.agent_user_id == world.user_id("agent")
    assert row.owner_user_id == world.user_id("sup2")
    assert row.created_by_user_id == world.user_id("sup")
    assert row.instruction == INSTRUCTION
    assert abs(_aware(row.due_at) - due) < timedelta(seconds=1)
    for column in ("acknowledged_at", "closed_at", "cancelled_at", "follow_up_outcome"):
        assert getattr(row, column) is None, column

    assert _recipients(world, task, "coaching_task.assigned") == _ids(world, "agent")
    assert _recipients(world, task, "coaching_task.owner_assigned") == _ids(world, "sup2")
    assert sorted(row.kind for row in _notes(world, task)) == [
        "coaching_task.assigned",
        "coaching_task.owner_assigned",
    ]
    for note in _notes(world, task):
        _check_note(world, note, task, case)
        assert INSTRUCTION not in note.title + note.body
    [event] = _events(world, task)
    assert (event.event_type, event.target_type, event.target_id) == (
        "coaching_task.created",
        "coaching_task",
        task["id"],
    )
    assert (event.actor_user_id, event.outcome) == (world.user_id("sup"), "success")
    details = json.dumps(event.details)
    for value in (
        case.id,
        case.result_id,
        "greeting",
        "human_result",
        world.user_id("agent"),
        world.user_id("sup2"),
    ):
        assert value in details, value
    assert INSTRUCTION not in details

    own = _created(world, case, "fee_explained", by="sup2", owner="sup2")
    assert _recipients(world, own, "coaching_task.owner_assigned") == []
    assert _recipients(world, own, "coaching_task.assigned") == _ids(world, "agent")
    by_admin = _created(world, case, "callback", by="admin", owner="owner")
    assert _row(by_admin).owner_user_id == world.owner.user_id
    assert _row(by_admin).created_by_user_id == world.user_id("admin")


def test_the_finding_source_is_the_appeal_result_once_the_item_has_one(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "sup_agent", "agent")
    upheld = _review(world)
    appeal = _assign(world, _filed(world, upheld, ("greeting",)), "sup_agent")
    _done(world, appeal, "decide", by="sup_agent", **_body([_uphold("greeting")]))
    assert _row(_created(world, upheld, "greeting")).finding_source == "human_result"

    changed = _review(world)
    appeal = _assign(world, _filed(world, changed), "sup_agent")
    _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body([_change("greeting", "passed"), _uphold("fee_explained")]),
    )
    before = _state(world)
    _refused(_create(world, changed, "greeting"), 422, "coaching_finding_not_failed")
    assert _state(world) == before
    assert _row(_created(world, changed, "fee_explained")).finding_source == "appeal_result"
    assert _row(_created(world, changed, "callback")).finding_source == "appeal_result"


class _Item:
    def __init__(self, item_id: str):
        self.item = {"id": item_id}

    @property
    def id(self) -> str:
        return self.item["id"]


def test_create_refusals_follow_the_designed_order_and_write_nothing(
    client, email_outbox, monkeypatch
):
    world = _world(
        client,
        email_outbox,
        monkeypatch,
        "admin",
        "sup",
        "sup2",
        "sup_b",
        "sup_agent",
        "agent",
        "lone",
        "viewer",
    )
    outsider = _outsider(client, email_outbox, "coaching-create")
    case = _review(world)
    in_review = _Case(world, agent="agent")
    unscored = _Case(world, agent="agent", scored=False)
    unscored.act("complete", by="sup")
    stopped = world.create(world.new_meeting(agent="agent"))
    stopped = world.done(stopped, "cancel", reason="duplicate")
    admin_call = _review(world, agent="admin")
    appealed = _review(world)
    appeal = _filed(world, appealed, ("greeting",))
    agentless = _review(world)
    _set_meeting(agentless.meeting_id, agent_user_id=None)
    active = _review(world)
    first = _created(world, active, "greeting")
    past = _due(days=-1)
    before = _state(world)

    def refused(target, status: int, code: str, criterion: str = "greeting", **kwargs):
        response = _create(world, target, criterion, **kwargs)
        _refused(response, status, code)
        assert _state(world) == before, code

    for name in ("sup_b", "lone", "agent"):
        refused(case, 404, "review_item_not_found", by=name)
        refused(case, 404, "review_item_not_found", by=name, due_at=past)
    refused(_Item(str(uuid.uuid4())), 404, "review_item_not_found")
    refused(admin_call, 422, "coaching_actor_is_agent", by="admin")
    refused(admin_call, 422, "coaching_actor_is_agent", by="admin", instruction="")
    for target in (in_review, unscored, _Item(stopped["id"])):
        refused(target, 409, "review_item_not_coachable")
        refused(target, 409, "review_item_not_coachable", "no_such_criterion")
    refused(appealed, 409, "review_appeal_open")
    refused(appealed, 409, "review_appeal_open", "closing")
    refused(agentless, 409, "coaching_no_agent")
    refused(agentless, 409, "coaching_no_agent", "no_such_criterion")
    for unknown in ("no_such_criterion", "Greeting", "greeting ", "empathy"):
        refused(case, 404, "criterion_not_found", unknown)
    refused(case, 404, "criterion_not_found", "no_such_criterion", owner="agent")
    for criterion in NOT_FAILED:
        refused(case, 422, "coaching_finding_not_failed", criterion)
        refused(case, 422, "coaching_finding_not_failed", criterion, owner="agent")
    refused(active, 409, "coaching_task_exists")
    refused(active, 409, "coaching_task_exists", owner="agent")
    refused(case, 422, "coaching_owner_is_agent", owner="agent")
    refused(case, 422, "coaching_owner_is_agent", owner="agent", due_at=past)
    for name in ("sup_b", "lone", "viewer"):
        refused(case, 422, "review_assignee_ineligible", owner=name)
        refused(case, 422, "review_assignee_ineligible", owner=name, due_at=past)
    refused(case, 422, "review_assignee_ineligible", owner=None, owner_user_id=outsider.user_id)
    refused(case, 422, "coaching_due_invalid", due_at=past, instruction="")
    refused(case, 422, "coaching_instruction_required", instruction="")

    _created(world, case, "greeting")
    _created(world, admin_call, "greeting", by="owner")
    _done(world, appeal, "withdraw", by="agent")
    _created(world, appealed, "greeting")
    _set_meeting(agentless.meeting_id, agent_user_id=world.user_id("agent"))
    _created(world, agentless, "greeting")
    _cancel(world, first)
    _created(world, active, "greeting")
    _created(world, active, "fee_explained", owner="sup_agent")
    _created(world, case, "fee_explained", owner="admin")


def test_the_due_time_is_aware_after_now_and_at_most_ninety_days_ahead(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case, spare = _review(world), _review(world)
    naive = (_now() + timedelta(days=1)).replace(tzinfo=None).isoformat()
    before = _state(world)
    for due in (
        _due(days=-1),
        _due(seconds=-1),
        _now().isoformat(),
        _due(days=90, minutes=1),
        _due(days=365),
        naive,
    ):
        _refused(_create(world, case, due_at=due), 422, "coaching_due_invalid")
    for due in ("not a time", "", None):
        _denied(_create(world, case, due_at=due), {422})
    assert _state(world) == before

    at_most = _now() + timedelta(days=90)
    task = _created(world, case, "greeting", due_at=at_most.isoformat())
    assert abs(_aware(_row(task).due_at) - at_most) < timedelta(seconds=1)
    tehran = (_now() + timedelta(hours=2)).astimezone(timezone(timedelta(hours=3, minutes=30)))
    task = _created(world, case, "fee_explained", due_at=tehran.isoformat())
    assert abs(_aware(_row(task).due_at) - tehran) < timedelta(seconds=1)
    zulu = (_now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _created(world, case, "callback", due_at=zulu)
    _created(world, spare, "greeting", due_at=_due(minutes=5))


def test_the_instruction_is_not_blank_after_strip_and_at_most_2000_characters(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case, spare = _review(world), _review(world)
    before = _state(world)
    for value in (*BLANKS, "x" * 2001, "ن" * 2001):
        _refused(_create(world, case, instruction=value), 422, "coaching_instruction_required")
    _denied(_create(world, case, instruction=None), {422})
    assert _state(world) == before

    _created(world, case, "greeting", instruction="x")
    _created(world, case, "fee_explained", instruction="x" * 2000)
    _created(world, case, "callback", instruction="ن" * 2000)
    padded = _created(world, spare, "greeting", instruction=" Practise\tthe close.\n ")
    assert _row(padded).instruction == "Practise\tthe close."

    noted = _created(world, spare, "fee_explained")
    stopped = _created(world, spare, "callback")
    extra = [_created(world, more, c) for more in (_review(world), _review(world)) for c in FAILED]
    for task, blank in zip(extra[:3], ("   ", "\t\n", "　"), strict=True):
        _acknowledge(world, task, note=blank)
        row = _row(task)
        assert (row.status, row.acknowledgement_note) == ("acknowledged", None), repr(blank)
    for task, blank in zip(extra[3:], (" ", " \n\t ", "　"), strict=True):
        _cancel(world, task, reason="other", cancel_note=blank)
        row = _row(task)
        assert (row.status, row.cancel_note) == ("cancelled", None), repr(blank)
    _close(world, extra[0], note="\n Better now. ")
    assert _row(extra[0]).follow_up_note == "Better now."
    _acknowledge(world, noted, note="  Will do.\t")
    assert _row(noted).acknowledgement_note == "Will do."
    _cancel(world, stopped, reason="other", cancel_note=" Coached twice.  ")
    assert _row(stopped).cancel_note == "Coached twice."


def test_the_coachee_and_the_current_agent_never_act_through_the_supervisor_routes(
    client, email_outbox, monkeypatch
):
    world = _world(
        client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_agent", "agent", "lone"
    )
    case = _review(world)
    task = _created(world, case, "greeting")
    other = _created(world, case, "fee_explained")
    assert task["id"] not in _supervised(world, "agent")
    view = _view(world, task, "agent")
    for key in AGENT_HIDDEN:
        assert view.get(key) is None, key
    _cannot_act(world, task, "agent")
    before = _state(world)
    _denied(
        _create(world, case, "callback", by="agent"),
        {404, 422},
        {"review_item_not_found", "coaching_actor_is_agent"},
    )
    assert _state(world) == before

    _move(world, case, "sup_agent")
    assert _agent_of(case) == world.user_id("sup_agent")
    assert _row(task).agent_user_id == world.user_id("agent")
    assert set(_mine(world, "agent")) == {task["id"], other["id"]}
    assert _mine(world, "sup_agent") == []
    _view(world, task, "agent")
    _invisible(world, task, "sup_agent")
    before = _state(world)
    _denied(
        _create(world, case, "callback", by="sup_agent"),
        {404, 422},
        {"review_item_not_found", "coaching_actor_is_agent"},
    )
    for name in ("agent", "sup_agent"):
        _refused(
            _act(world, task, "reassign", by="sup", owner_user_id=world.user_id(name)),
            422,
            "coaching_owner_is_agent",
        )
    assert _state(world) == before
    _acknowledge(world, task, by="agent")
    _cancel(world, other, by="sup2", reason="agent_changed")
    assert _row(other).cancel_reason == "agent_changed"

    _reassign(world, task, "admin", by="sup2")
    _move(world, case, "admin")
    assert task["id"] in _supervised(world, "admin")
    assert _view(world, task, "admin")["status"] == "acknowledged"
    before = _state(world)
    for action, body in (
        ("reassign", {"owner_user_id": world.user_id("sup")}),
        ("close", {"follow_up_outcome": "improved", "follow_up_note": FOLLOW_UP}),
        ("cancel", {"cancel_reason": "agent_changed"}),
    ):
        _refused(_act(world, task, action, by="admin", **body), 422, "coaching_actor_is_agent")
    _refused(_create(world, case, "callback", by="admin"), 422, "coaching_actor_is_agent")
    assert _state(world) == before
    _move(world, case, "lone")
    _reassign(world, task, "sup", by="admin")
    _created(world, case, "callback", by="admin")

    coached_admin = _review(world, agent="admin")
    own = _created(world, coached_admin, "greeting")
    assert own["id"] not in _supervised(world, "admin")
    assert _mine(world, "admin") == [own["id"]]
    view = _view(world, own, "admin")
    for key in AGENT_HIDDEN:
        assert view.get(key) is None, key
    _cannot_act(world, own, "admin")


def test_only_the_coachee_acknowledges_an_assigned_task_and_only_once(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_b", "agent")
    outsider = _outsider(client, email_outbox, "coaching-acknowledge")
    case = _review(world)
    task = _created(world, case, "greeting")
    plain = _created(world, case, "fee_explained")
    before = _state(world)
    for name in ("owner", "admin", "sup", "sup2"):
        _refused(_act(world, task, "acknowledge", by=name), 403, "coaching_not_agent")
        _refused(_act(world, task, "acknowledge", by=name, revision=7), 403, "coaching_not_agent")
    _refused(_act(world, task, "acknowledge", by="sup_b"), 404, "coaching_task_not_found")
    _refused(
        outsider.post(f"{TASKS}/{task['id']}/acknowledge", json={"expected_revision": 1}),
        404,
        "coaching_task_not_found",
    )
    _refused(
        world.persona("agent").post(
            f"{TASKS}/{uuid.uuid4()}/acknowledge", json={"expected_revision": 1}
        ),
        404,
        "coaching_task_not_found",
    )
    _refused(
        _act(world, task, "acknowledge", by="agent", revision=2),
        409,
        "coaching_task_revision_conflict",
    )
    for note in ("x" * 2001, "ن" * 2001):
        _refused(
            _act(world, task, "acknowledge", by="agent", note=note), 422, "coaching_note_invalid"
        )
    assert _state(world) == before

    _acknowledge(world, task, note="y" * 2000)
    row = _row(task)
    assert (row.status, row.revision) == ("acknowledged", 2)
    assert row.acknowledged_at is not None
    assert row.acknowledgement_note == "y" * 2000
    assert _recipients(world, task, "coaching_task.acknowledged") == _ids(world, "sup2")
    assert _event_types(world, task) == ["coaching_task.created", "coaching_task.acknowledged"]
    assert _events(world, task)[-1].actor_user_id == world.user_id("agent")
    for note in _notes(world, task, "coaching_task.acknowledged"):
        _check_note(world, note, task, case)

    before = _state(world)
    _refused(_act(world, task, "acknowledge", by="agent"), 409, "coaching_task_state_invalid")
    _refused(
        _act(world, task, "acknowledge", by="agent", revision=1),
        409,
        "coaching_task_revision_conflict",
    )
    assert _state(world) == before
    _acknowledge(world, plain)
    assert _row(plain).acknowledgement_note is None


def test_only_the_owner_closes_an_acknowledged_task_with_an_outcome_and_a_note(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_b", "agent")
    case = _review(world)
    task = _created(world, case, "greeting")
    body = {"follow_up_outcome": "improved", "follow_up_note": FOLLOW_UP}
    before = _state(world)
    _refused(_act(world, task, "close", by="sup2", **body), 409, "coaching_task_not_acknowledged")
    _refused(
        _act(world, task, "close", by="sup2", follow_up_outcome="great", follow_up_note=""),
        409,
        "coaching_task_not_acknowledged",
    )
    _refused(_act(world, task, "close", by="sup", **body), 403, "coaching_not_owner")
    assert _state(world) == before

    _acknowledge(world, task)
    before = _state(world)
    _refused(_act(world, task, "close", by="sup_b", **body), 404, "coaching_task_not_found")
    for name in ("owner", "admin", "sup"):
        _refused(_act(world, task, "close", by=name, **body), 403, "coaching_not_owner")
    _refused(
        _act(world, task, "close", by="sup", revision=1, **body),
        409,
        "coaching_task_revision_conflict",
    )
    _refused(
        _act(world, task, "close", by="sup2", revision=1, **body),
        409,
        "coaching_task_revision_conflict",
    )
    for outcome in ("great", "Improved", "improved ", "", "closed"):
        _refused(
            _act(world, task, "close", by="sup2", follow_up_outcome=outcome, follow_up_note="x"),
            422,
            "coaching_follow_up_invalid",
        )
    for note in (*BLANKS, "x" * 2001):
        _refused(
            _act(
                world, task, "close", by="sup2", follow_up_outcome="improved", follow_up_note=note
            ),
            422,
            "coaching_follow_up_invalid",
        )
    for broken in (
        {"follow_up_outcome": None, "follow_up_note": "x"},
        {"follow_up_outcome": "improved", "follow_up_note": None},
        {"follow_up_outcome": "improved"},
    ):
        _denied(_act(world, task, "close", by="sup2", **broken), {422})
    _denied(_act(world, task, "close", by="agent", **body), {403, 404, 422})
    assert _state(world) == before

    _close(world, task, outcome="partially_improved")
    row = _row(task)
    assert (row.status, row.revision) == ("closed", 3)
    assert (row.follow_up_outcome, row.follow_up_note) == ("partially_improved", FOLLOW_UP)
    assert row.closed_by_user_id == world.user_id("sup2")
    assert row.closed_at is not None and row.cancelled_at is None
    assert _recipients(world, task, "coaching_task.closed") == _ids(world, "agent")
    [closed] = [e for e in _events(world, task) if e.event_type == "coaching_task.closed"]
    assert closed.actor_user_id == world.user_id("sup2")
    assert "partially_improved" in json.dumps(closed.details)
    assert FOLLOW_UP not in json.dumps(closed.details)

    before = _state(world)
    _refused(_act(world, task, "acknowledge", by="agent"), 409, "coaching_task_state_invalid")
    for action, name in (("reassign", "sup"), ("close", "sup2"), ("cancel", "sup2")):
        _refused(
            _act(world, task, action, by=name, **_write_body(world, action)),
            409,
            "coaching_task_closed",
        )
    assert _state(world) == before

    second = _created(world, case, "greeting")
    _acknowledge(world, second)
    _close(world, second, outcome="not_improved", note="x" * 2000)
    third = _created(world, case, "fee_explained")
    _acknowledge(world, third)
    _close(world, third, outcome="improved", note="x")


def test_the_owner_or_a_workspace_owner_or_admin_cancels_an_active_task(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_b", "agent")
    case = _review(world)
    by_admin = _created(world, case, "greeting")
    by_owner = _created(world, case, "fee_explained")
    by_workspace_owner = _created(world, case, "callback")
    reason = {"cancel_reason": "duplicate"}
    before = _state(world)
    _refused(_act(world, by_admin, "cancel", by="sup_b", **reason), 404, "coaching_task_not_found")
    _refused(_act(world, by_admin, "cancel", by="sup", **reason), 403, "coaching_not_owner")
    _refused(
        _act(world, by_admin, "cancel", by="sup", revision=2, **reason),
        409,
        "coaching_task_revision_conflict",
    )
    for value in ("bored", "Duplicate", "duplicate ", "", "closed"):
        _refused(
            _act(world, by_admin, "cancel", by="admin", cancel_reason=value),
            422,
            "coaching_cancel_invalid",
        )
    _denied(_act(world, by_admin, "cancel", by="admin", cancel_reason=None), {422})
    _denied(_act(world, by_admin, "cancel", by="admin"), {422})
    for note in ("x" * 2001, "ن" * 2001):
        _refused(
            _act(world, by_admin, "cancel", by="admin", cancel_note=note, **reason),
            422,
            "coaching_cancel_invalid",
        )
    _denied(_act(world, by_admin, "cancel", by="agent", **reason), {403, 404, 422})
    assert _state(world) == before

    _cancel(world, by_admin, by="admin", cancel_note=CANCEL_NOTE)
    row = _row(by_admin)
    assert (row.status, row.revision, row.cancel_reason) == ("cancelled", 2, "duplicate")
    assert (row.cancel_note, row.cancelled_by_user_id) == (CANCEL_NOTE, world.user_id("admin"))
    assert row.cancelled_at is not None and row.closed_at is None
    assert _recipients(world, by_admin, "coaching_task.cancelled") == _ids(world, "agent", "sup2")
    [cancelled] = [e for e in _events(world, by_admin) if e.event_type == "coaching_task.cancelled"]
    assert cancelled.actor_user_id == world.user_id("admin")
    assert "duplicate" in json.dumps(cancelled.details)
    assert CANCEL_NOTE not in json.dumps(cancelled.details)

    _acknowledge(world, by_owner)
    _cancel(world, by_owner, by="sup2", reason="other", cancel_note="x" * 2000)
    assert _recipients(world, by_owner, "coaching_task.cancelled") == _ids(world, "agent")
    _cancel(world, by_workspace_owner, by="owner", reason="finding_overturned")
    assert _recipients(world, by_workspace_owner, "coaching_task.cancelled") == _ids(
        world, "agent", "sup2"
    )

    before = _state(world)
    _refused(_act(world, by_admin, "acknowledge", by="agent"), 409, "coaching_task_state_invalid")
    for action, name in (("reassign", "sup"), ("close", "sup2"), ("cancel", "admin")):
        _refused(
            _act(world, by_admin, action, by=name, **_write_body(world, action)),
            409,
            "coaching_task_closed",
        )
    assert _state(world) == before
    _created(world, case, "greeting")


def test_reassign_follows_the_designed_order_and_the_same_owner_writes_nothing(
    client, email_outbox, monkeypatch
):
    world = _world(
        client,
        email_outbox,
        monkeypatch,
        "admin",
        "sup",
        "sup2",
        "sup_b",
        "sup_agent",
        "agent",
        "lone",
        "viewer",
    )
    outsider = _outsider(client, email_outbox, "coaching-reassign")
    case = _review(world)
    task = _created(world, case, "greeting")
    done = _created(world, case, "fee_explained")
    _acknowledge(world, done)
    _close(world, done)
    before = _state(world)

    def refused(target: dict, status: int, code: str, owner: str | None, **kwargs) -> None:
        body = {"owner_user_id": world.user_id(owner)} if owner else {}
        response = _act(world, target, "reassign", **{"by": "sup", **kwargs, **body})
        _refused(response, status, code)
        assert _state(world) == before, code

    refused(task, 404, "coaching_task_not_found", "sup_agent", by="sup_b")
    refused(task, 404, "coaching_task_not_found", "agent", by="sup_b", revision=9)
    refused(task, 409, "coaching_task_revision_conflict", "sup_agent", revision=2)
    refused(task, 409, "coaching_task_revision_conflict", "agent", revision=2)
    stale = _act(world, task, "reassign", by="sup", revision=2, owner_user_id=world.owner.user_id)
    assert stale.json()["error"]["details"]["current_revision"] == 1
    refused(done, 409, "coaching_task_closed", "sup_agent")
    refused(done, 409, "coaching_task_closed", "agent")
    refused(task, 422, "coaching_owner_is_agent", "agent")
    for name in ("sup_b", "lone", "viewer"):
        refused(task, 422, "review_assignee_ineligible", name)
    refused(task, 422, "review_assignee_ineligible", None, owner_user_id=outsider.user_id)

    same = _act(world, task, "reassign", by="sup", owner_user_id=world.user_id("sup2"))
    assert same.status_code == 200, same.text
    assert _state(world) == before

    _reassign(world, task, "sup_agent", by="sup")
    row = _row(task)
    assert (row.owner_user_id, row.revision, row.status) == (
        world.user_id("sup_agent"),
        2,
        "assigned",
    )
    assert _recipients(world, task, "coaching_task.owner_assigned") == _ids(
        world, "sup2", "sup_agent"
    )
    [moved] = [e for e in _events(world, task) if e.event_type == "coaching_task.reassigned"]
    assert moved.actor_user_id == world.user_id("sup")
    for name in ("sup2", "sup_agent"):
        assert world.user_id(name) in json.dumps(moved.details), name
    _acknowledge(world, task)
    _reassign(world, task, "admin", by="admin")
    assert _row(task).status == "acknowledged"


def _agent_safe(world: _World, item: dict, *hidden: str) -> None:
    for key in AGENT_HIDDEN:
        assert item.get(key) is None, key
    text = json.dumps(item)
    for name in hidden:
        assert world.user_id(name) not in text, name


def test_each_role_sees_only_the_tasks_its_record_rules_allow(client, email_outbox, monkeypatch):
    world = _world(
        client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_b", "agent", "viewer"
    )
    outsider = _outsider(client, email_outbox, "coaching-visibility")
    case = _review(world)
    task = _created(world, case, "greeting", by="admin")
    _acknowledge(world, task, note=ACK_NOTE)
    _close(world, task)
    active = _created(world, case, "fee_explained", by="admin")
    stopped = _created(world, case, "callback", by="admin")
    _cancel(world, stopped, by="owner")

    for name in ("owner", "admin", "sup", "sup2"):
        assert {task["id"], active["id"], stopped["id"]} <= set(_supervised(world, name)), name
        view = _view(world, task, name)
        assert (view["status"], view["follow_up_outcome"]) == ("closed", "improved"), name
        assert view["owner_user_id"] == world.user_id("sup2")
    for name in ("sup_b", "viewer"):
        _invisible(world, task, name)
        _invisible(world, active, name)
    assert _listed(outsider) == []
    assert _listed(outsider, MY_TASKS) == []
    _refused(outsider.get(f"{TASKS}/{task['id']}"), 404, "coaching_task_not_found")
    _refused(outsider.get(f"{TASKS}/{task['id']}/history"), 404, "coaching_task_not_found")

    assert set(_mine(world, "agent")) == {task["id"], active["id"], stopped["id"]}
    for item in _page(world.persona("agent").get(MY_TASKS))[0]:
        _agent_safe(world, item, "admin", "sup", "owner")
    view = _view(world, task, "agent")
    assert (view["status"], view["follow_up_outcome"]) == ("closed", "improved")
    assert view["acknowledged_at"] is not None
    assert view["owner_user_id"] == world.user_id("sup2")
    assert view["overdue"] is False
    text = json.dumps(view, ensure_ascii=False)
    for value in (ACK_NOTE, FOLLOW_UP, INSTRUCTION, "greeting", case.id, case.meeting_id):
        assert value in text, value
    _agent_safe(world, view, "admin", "sup", "owner")
    view = _view(world, stopped, "agent")
    assert (view["status"], view["cancel_reason"]) == ("cancelled", "duplicate")
    _agent_safe(world, view, "admin", "sup", "owner")
    view = _view(world, active, "agent")
    assert (view["status"], view["finding_source"]) == ("assigned", "human_result")
    _agent_safe(world, view, "admin", "sup", "owner")
    assert _mine(world, "sup2") == []


def test_the_supervisor_list_filters_orders_and_pages(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "sup_agent", "agent")
    first, second = _review(world), _review(world, agent="sup_agent")
    late = _created(world, first, "greeting", owner="sup")
    soon = _created(world, second, "greeting", due_at=_due(days=2))
    later = _created(world, first, "fee_explained", due_at=_due(days=3))
    done = _created(world, first, "callback", due_at=_due(days=5))
    _acknowledge(world, done)
    _close(world, done)
    _set_task(late, due_at=_now() - timedelta(hours=1))
    ordered = [late["id"], soon["id"], later["id"], done["id"]]

    assert _supervised(world, "sup") == ordered
    assert _supervised(world, "sup", status="assigned") == ordered[:3]
    assert _supervised(world, "sup", status="closed") == [done["id"]]
    assert _supervised(world, "sup", owner_user_id=world.user_id("sup")) == [late["id"]]
    assert _supervised(world, "sup", agent_user_id=world.user_id("sup_agent")) == [soon["id"]]
    assert _supervised(world, "sup", overdue="true") == [late["id"]]
    assert _supervised(world, "sup", overdue="false") == ordered[1:]
    assert _view(world, late, "sup")["overdue"] is True
    assert _view(world, later, "sup")["overdue"] is False
    _set_task(done, due_at=_now() - timedelta(hours=1))
    assert _view(world, done, "sup")["overdue"] is False
    assert _supervised(world, "sup", overdue="true") == [late["id"]]
    ordered = [late["id"], done["id"], soon["id"], later["id"]]
    assert _supervised(world, "sup") == ordered

    persona = world.persona("sup")
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        items, cursor = _page(persona.get(TASKS, params=params))
        assert len(items) <= 1
        seen.extend(item["id"] for item in items)
        if cursor is None:
            break
    assert seen == ordered
    for limit in (0, 101, -1):
        assert persona.get(TASKS, params={"limit": limit}).status_code == 422, limit
    assert persona.get(TASKS, params={"limit": 100}).status_code == 200
    for cursor in ("not-a-cursor", "e30", "%%%"):
        _refused(persona.get(TASKS, params={"cursor": cursor}), 400, "invalid_cursor")
    for status in ("open", "Assigned", "assigned ", "done"):
        _refused(persona.get(TASKS, params={"status": status}), 422, "coaching_filter_invalid")
    assert persona.get(TASKS, params={"overdue": "maybe"}).status_code == 422
    for status in ("assigned", "acknowledged", "closed", "cancelled"):
        assert persona.get(TASKS, params={"status": status}).status_code == 200, status


def test_my_coaching_tasks_lists_the_callers_own_tasks_newest_first(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "sup_agent", "agent")
    case, theirs = _review(world), _review(world, agent="sup_agent")
    made = [_created(world, case, criterion) for criterion in FAILED]
    other = _created(world, theirs, "greeting")
    newest_first = [task["id"] for task in reversed(made)]
    assert _mine(world, "agent") == newest_first
    assert _mine(world, "sup_agent") == [other["id"]]
    assert _mine(world, "sup2") == []
    _acknowledge(world, made[1])
    assert _mine(world, "agent", status="acknowledged") == [made[1]["id"]]
    assert _mine(world, "agent", status="assigned") == [made[2]["id"], made[0]["id"]]

    persona = world.persona("agent")
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        items, cursor = _page(persona.get(MY_TASKS, params=params))
        assert len(items) <= 1
        seen.extend(item["id"] for item in items)
        if cursor is None:
            break
    assert seen == newest_first
    _refused(persona.get(MY_TASKS, params={"cursor": "not-a-cursor"}), 400, "invalid_cursor")
    for status in ("open", "Acknowledged", "done"):
        _refused(persona.get(MY_TASKS, params={"status": status}), 422, "coaching_filter_invalid")
    assert _mine(world, "agent", status="closed") == []


def test_the_history_is_oldest_first_and_the_coachee_sees_only_the_coachee_and_the_owner(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "sup_b", "agent")
    case = _review(world)
    task = _created(world, case, "greeting", by="admin")
    _reassign(world, task, "sup", by="sup2")
    _acknowledge(world, task)
    _close(world, task, by="sup")
    kinds = [
        "coaching_task.created",
        "coaching_task.reassigned",
        "coaching_task.acknowledged",
        "coaching_task.closed",
    ]
    actors = [world.user_id(name) for name in ("admin", "sup2", "agent", "sup")]
    for name in ("owner", "admin", "sup", "sup2"):
        entries = _history(world, task, name)
        assert [entry["event_type"] for entry in entries] == kinds, name
        assert [entry["actor_user_id"] for entry in entries] == actors, name
        roles = [entry["actor_role"] for entry in entries]
        assert set(roles) <= {"coachee", "owner", "reviewer", "system"}, roles
        assert (roles[0], roles[2], roles[3]) == ("reviewer", "coachee", "owner"), name
        times = [_time(entry["occurred_at"]) for entry in entries]
        assert times == sorted(times), name

    own = _history(world, task, "agent")
    assert [entry["event_type"] for entry in own] == kinds
    assert [entry["actor_user_id"] for entry in own] == [
        None,
        None,
        world.user_id("agent"),
        world.user_id("sup"),
    ]
    assert [entry["actor_role"] for entry in own] == ["reviewer", "reviewer", "coachee", "owner"]
    details = json.dumps([entry["details"] for entry in own])
    for name in ("admin", "sup2", "owner", "sup_b"):
        assert world.user_id(name) not in details, name
    _refused(
        world.persona("sup_b").get(f"{TASKS}/{task['id']}/history"),
        404,
        "coaching_task_not_found",
    )


def test_no_event_and_no_notification_holds_the_free_text(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "agent")
    case = _review(world)
    tokens = {
        name: f"{name}-{uuid.uuid4().hex}"
        for name in ("instruction", "acknowledgement", "follow_up", "cancel")
    }
    task = _created(world, case, "greeting", instruction=f"Practise {tokens['instruction']}")
    _acknowledge(world, task, note=f"Ok {tokens['acknowledgement']}")
    _close(world, task, note=f"Better {tokens['follow_up']}")
    stopped = _created(world, case, "fee_explained")
    _cancel(world, stopped, by="admin", cancel_note=f"Stop {tokens['cancel']}")
    _reassign(world, _created(world, case, "callback"), "sup", by="sup2")

    events = _events(world)
    assert sorted({event.event_type for event in events}) == sorted(
        {
            "coaching_task.created",
            "coaching_task.reassigned",
            "coaching_task.acknowledged",
            "coaching_task.closed",
            "coaching_task.cancelled",
        }
    )
    assert all(event.target_type == "coaching_task" for event in events)
    audit, inbox = _dump(AuditEvent), _dump(Notification)
    for token in tokens.values():
        assert token not in audit, token
        assert token not in inbox, token
    rows = _notes(world)
    assert {row.kind for row in rows} <= set(KINDS)
    for row in rows:
        assert set(row.data) == {"coaching_task_id", "review_item_id", "meeting_id"}, row.data
    texts: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        texts.setdefault(row.kind, set()).add((row.title, row.body))
    assert all(len(pairs) == 1 for pairs in texts.values()), texts
    keys = [(row.user_id, row.kind, row.data["coaching_task_id"]) for row in rows]
    assert len(keys) == len(set(keys))


def _due_in(task: dict, **delta: float) -> None:
    _set_task(task, due_at=_now() + timedelta(**delta))


def _overdue_events(world: _World, task: dict) -> list[AuditEvent]:
    return [e for e in _events(world, task) if e.event_type == "coaching_task.overdue"]


def test_one_sweep_sends_the_due_soon_reminder_once_inside_the_window(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case, spare = _review(world), _review(world)
    far = _created(world, case, "greeting")
    edge = _created(world, case, "fee_explained")
    inside = _created(world, case, "callback")
    acknowledged = _created(world, spare, "greeting")
    _acknowledge(world, acknowledged)
    _due_in(edge, hours=24, minutes=10)
    _due_in(inside, hours=23)
    _due_in(acknowledged, hours=2)

    _sweep(world)
    for task in (inside, acknowledged):
        assert _recipients(world, task, "coaching_task.due_soon") == _ids(world, "agent")
        assert _row(task).reminder_notified_at is not None
        assert _row(task).overdue_notified_at is None
    for note in _notes(world, inside, "coaching_task.due_soon"):
        _check_note(world, note, inside, case)
    for task in (far, edge):
        assert _recipients(world, task, "coaching_task.due_soon") == []
        assert _row(task).reminder_notified_at is None
    assert _notes(world, kind="coaching_task.overdue") == []
    assert [e for e in _events(world) if e.event_type == "coaching_task.overdue"] == []

    before = _state(world)
    _sweep(world)
    assert _state(world) == before

    _due_in(edge, hours=23, minutes=50)
    _sweep(world)
    for task in (edge, inside, acknowledged):
        assert _recipients(world, task, "coaching_task.due_soon") == _ids(world, "agent")
    assert _recipients(world, far, "coaching_task.due_soon") == []


def test_an_overdue_task_gets_one_notice_for_coachee_and_owner_and_one_event(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case, spare = _review(world), _review(world)
    late = _created(world, case, "greeting")
    warned = _created(world, case, "fee_explained")
    acknowledged = _created(world, case, "callback")
    _acknowledge(world, acknowledged)
    orphan = _created(world, spare, "greeting")
    _due_in(warned, hours=2)
    _sweep(world)
    assert _recipients(world, warned, "coaching_task.due_soon") == _ids(world, "agent")
    assert _recipients(world, late, "coaching_task.due_soon") == []

    for task in (late, warned, acknowledged, orphan):
        _due_in(task, minutes=-30)
    _set_task(orphan, owner_user_id=None)
    _sweep(world)
    for task in (late, warned, acknowledged):
        assert _recipients(world, task, "coaching_task.overdue") == _ids(world, "agent", "sup2")
    assert _recipients(world, orphan, "coaching_task.overdue") == _ids(world, "agent")
    for task in (late, acknowledged, orphan):
        assert _recipients(world, task, "coaching_task.due_soon") == []
        assert _row(task).reminder_notified_at is not None
    assert _recipients(world, warned, "coaching_task.due_soon") == _ids(world, "agent")
    for task in (late, warned, acknowledged, orphan):
        assert _row(task).overdue_notified_at is not None
        [event] = _overdue_events(world, task)
        assert (event.target_type, event.actor_user_id) == ("coaching_task", None)
        assert "due_at" in json.dumps(event.details)
        for note in _notes(world, task, "coaching_task.overdue"):
            assert note.data["coaching_task_id"] == task["id"]
    assert _row(acknowledged).status == "acknowledged"
    assert _row(late).status == "assigned"
    for name, roles in (("sup2", {"system"}), ("agent", {"system", "reviewer"})):
        last = _history(world, late, name)[-1]
        assert last["event_type"] == "coaching_task.overdue", name
        assert last["actor_user_id"] is None and last["actor_role"] in roles, (name, last)

    before = _state(world)
    _sweep(world)
    assert _state(world) == before


def test_closed_and_cancelled_tasks_get_no_reminder_and_no_notice(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case, spare = _review(world), _review(world)
    closed = _created(world, case, "greeting")
    _acknowledge(world, closed)
    _close(world, closed)
    cancelled = _created(world, case, "fee_explained")
    _cancel(world, cancelled)
    closed_soon = _created(world, spare, "greeting")
    _acknowledge(world, closed_soon)
    _close(world, closed_soon)
    cancelled_soon = _created(world, spare, "fee_explained")
    _cancel(world, cancelled_soon)
    active = _created(world, case, "callback")
    active_soon = _created(world, spare, "callback")
    for task in (closed, cancelled, active):
        _due_in(task, hours=-1)
    for task in (closed_soon, cancelled_soon, active_soon):
        _due_in(task, hours=2)

    _sweep(world)
    for task in (closed, cancelled, closed_soon, cancelled_soon):
        for kind in ("coaching_task.due_soon", "coaching_task.overdue"):
            assert _recipients(world, task, kind) == [], kind
        assert _overdue_events(world, task) == []
        assert _row(task).overdue_notified_at is None
    assert _recipients(world, active, "coaching_task.overdue") == _ids(world, "agent", "sup2")
    assert _recipients(world, active_soon, "coaching_task.due_soon") == _ids(world, "agent")


def test_the_sweep_runs_from_beat_on_its_interval_setting(client, email_outbox, monkeypatch):
    from backend.celery_app import celery_app
    from backend.config import Settings, get_settings
    from backend.observability import heartbeats

    assert Settings.model_fields["coaching_reminder_sweep_interval_seconds"].default == 900
    interval = get_settings().coaching_reminder_sweep_interval_seconds
    entries = [
        entry for entry in celery_app.conf.beat_schedule.values() if "coaching" in entry["task"]
    ]
    assert len(entries) == 1, entries
    assert entries[0]["schedule"] == float(interval)
    assert entries[0]["task"] in celery_app.tasks
    assert _sweep_function().__module__ == SWEEP_MODULE
    source = Path(inspect.getfile(importlib.import_module(SWEEP_MODULE))).read_text()
    assert '__name__ == "__main__"' in source

    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    task = _created(world, _review(world))
    _due_in(task, hours=-1)
    heartbeats.use_heartbeat_store(heartbeats.MemoryHeartbeatStore())
    try:
        celery_app.tasks[entries[0]["task"]].run()
    finally:
        heartbeats.use_heartbeat_store(None)
    assert _recipients(world, task, "coaching_task.overdue") == _ids(world, "agent", "sup2")


def _all_failed(world: _World) -> _Case:
    case = _Case(world, assignee="sup", agent="agent")
    case.decided("greeting", _override("failed", note="No greeting was heard."))
    case.decided("closing", _override("failed", note="No goodbye was heard."))
    case.decided("fee_explained", ACCEPT)
    case.decided(
        "callback",
        _override("failed", reason_code="policy_exception", note="No call back was offered."),
    )
    case.act("complete", by="sup")
    return case


def _columns(row: CoachingTask) -> dict:
    return {c.key: getattr(row, c.key) for c in CoachingTask.__mapper__.column_attrs}


def test_an_appeal_that_overturns_a_coached_finding_cancels_its_active_task(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "sup_agent", "agent")
    case = _all_failed(world)
    old = _created(world, case, "greeting")
    _acknowledge(world, old)
    _close(world, old)
    tasks = {
        criterion: _created(world, case, criterion)
        for criterion in ("greeting", "closing", "fee_explained", "callback")
    }
    _acknowledge(world, tasks["closing"])
    kept = {name: _columns(_row(tasks[name])) for name in ("fee_explained", "callback")}
    finished = _columns(_row(old))
    appeal = _assign(
        world, _filed(world, case, ("greeting", "closing", "fee_explained")), "sup_agent"
    )
    seen = {event.id for event in _events(world)}

    _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body(
            [
                _change("greeting", "passed"),
                _change("closing", "not_applicable"),
                _uphold("fee_explained"),
            ]
        ),
    )
    for criterion in ("greeting", "closing"):
        task = tasks[criterion]
        row = _row(task)
        assert (row.status, row.cancel_reason) == ("cancelled", "finding_overturned"), criterion
        assert row.cancelled_by_user_id == world.user_id("sup_agent")
        assert row.cancelled_at is not None and row.closed_at is None
        assert _recipients(world, task, "coaching_task.cancelled") == _ids(world, "agent", "sup2")
        new = [event for event in _events(world, task) if event.id not in seen]
        assert [event.event_type for event in new] == ["coaching_task.cancelled"], criterion
        assert new[0].actor_user_id == world.user_id("sup_agent")
        assert "finding_overturned" in json.dumps(new[0].details)
    for name, columns in kept.items():
        assert _columns(_row(tasks[name])) == columns, name
        assert _recipients(world, tasks[name], "coaching_task.cancelled") == []
        assert [e for e in _events(world, tasks[name]) if e.id not in seen] == []
    assert _columns(_row(old)) == finished

    before = _state(world)
    for criterion in ("greeting", "closing"):
        _refused(_create(world, case, criterion), 422, "coaching_finding_not_failed")
    assert _state(world) == before


def test_an_appeal_that_keeps_or_adds_failed_findings_leaves_the_active_tasks_alone(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "sup_agent", "agent")
    case = _review(world)
    tasks = {criterion: _created(world, case, criterion) for criterion in FAILED}
    _acknowledge(world, tasks["callback"])
    kept = {name: _columns(_row(task)) for name, task in tasks.items()}
    _refused(_create(world, case, "closing"), 422, "coaching_finding_not_failed")
    appeal = _assign(world, _filed(world, case, ("greeting", "closing")), "sup_agent")
    seen = {event.id for event in _events(world)}
    notes = [row.id for row in _notes(world) if row.kind.startswith("coaching_task.")]

    _done(
        world,
        appeal,
        "decide",
        by="sup_agent",
        **_body([_uphold("greeting"), _change("closing", "failed")]),
    )
    for name, columns in kept.items():
        assert _columns(_row(tasks[name])) == columns, name
    assert [event for event in _events(world) if event.id not in seen] == []
    assert [row.id for row in _notes(world) if row.kind.startswith("coaching_task.")] == notes

    added = _created(world, case, "closing")
    assert _row(added).finding_source == "appeal_result"
    assert _row(added).agent_user_id == world.user_id("agent")


def _one_lands(results: list, codes: set[str]) -> int:
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    [lost] = [r for r in results if r.status_code == 409]
    assert _code(lost) in codes, lost.text
    return next(index for index, r in enumerate(results) if r.status_code == 200)


@pytest.mark.postgres
def test_two_concurrent_acknowledges_let_exactly_one_land(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case = _review(world)
    agent = world.persona("agent")
    for criterion in FAILED:
        task = _created(world, case, criterion)
        path = f"{TASKS}/{task['id']}/acknowledge"
        results = _race(
            client,
            [(agent, path, {"expected_revision": 1}), (agent, path, {"expected_revision": 1})],
        )
        _one_lands(results, {"coaching_task_revision_conflict", "coaching_task_state_invalid"})
        assert _row(task).revision == 2
        assert _event_types(world, task) == ["coaching_task.created", "coaching_task.acknowledged"]
        assert _recipients(world, task, "coaching_task.acknowledged") == _ids(world, "sup2")


@pytest.mark.postgres
def test_two_concurrent_closes_let_exactly_one_land(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case = _review(world)
    owner = world.persona("sup2")
    for criterion in FAILED:
        task = _created(world, case, criterion)
        _acknowledge(world, task)
        path = f"{TASKS}/{task['id']}/close"
        body = {"expected_revision": 2, "follow_up_outcome": "improved", "follow_up_note": "x"}
        results = _race(client, [(owner, path, body), (owner, path, body)])
        _one_lands(results, {"coaching_task_revision_conflict", "coaching_task_closed"})
        assert (_row(task).status, _row(task).revision) == ("closed", 3)
        assert _event_types(world, task).count("coaching_task.closed") == 1
        assert _recipients(world, task, "coaching_task.closed") == _ids(world, "agent")


@pytest.mark.postgres
def test_a_close_racing_a_cancel_lets_exactly_one_land(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "agent")
    case = _review(world)
    for criterion in FAILED:
        task = _created(world, case, criterion)
        _acknowledge(world, task)
        base = f"{TASKS}/{task['id']}"
        results = _race(
            client,
            [
                (
                    world.persona("sup2"),
                    f"{base}/close",
                    {
                        "expected_revision": 2,
                        "follow_up_outcome": "improved",
                        "follow_up_note": "x",
                    },
                ),
                (
                    world.persona("admin"),
                    f"{base}/cancel",
                    {"expected_revision": 2, "cancel_reason": "duplicate"},
                ),
            ],
        )
        winner = _one_lands(results, {"coaching_task_revision_conflict", "coaching_task_closed"})
        row = _row(task)
        assert row.status == ("closed", "cancelled")[winner], criterion
        assert row.revision == 3
        terminal = [
            t
            for t in _event_types(world, task)
            if t in ("coaching_task.closed", "coaching_task.cancelled")
        ]
        assert terminal == [("coaching_task.closed", "coaching_task.cancelled")[winner]]


@pytest.mark.postgres
def test_two_concurrent_creates_for_one_finding_give_one_201_and_one_409(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch, "admin", "sup", "sup2", "agent")
    case = _review(world)
    for criterion in FAILED:
        body = {
            "criterion_id": criterion,
            "owner_user_id": world.user_id("sup2"),
            "due_at": _due(days=3),
            "instruction": INSTRUCTION,
        }
        path = _create_path(case)
        results = _race(client, [(world.persona("sup"), path, body), (world.owner, path, body)])
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        [lost] = [r for r in results if r.status_code == 409]
        assert _code(lost) == "coaching_task_exists"
        assert [row.criterion_id for row in _rows(world)].count(criterion) == 1


@pytest.mark.postgres
def test_two_parallel_sweeps_send_each_notice_once(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch, "sup", "sup2", "agent")
    case = _review(world)
    soon = _created(world, case, "greeting")
    late = _created(world, case, "fee_explained")
    _due_in(soon, hours=2)
    _due_in(late, hours=-1)
    barrier = threading.Barrier(2)
    errors: list = []

    def run() -> None:
        try:
            barrier.wait(timeout=15)
            _sweep(world)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert _recipients(world, soon, "coaching_task.due_soon") == _ids(world, "agent")
    assert _recipients(world, late, "coaching_task.overdue") == _ids(world, "agent", "sup2")
    assert _recipients(world, late, "coaching_task.due_soon") == []
    assert len(_overdue_events(world, late)) == 1
