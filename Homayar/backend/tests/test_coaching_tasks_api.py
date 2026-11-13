"""HOM-77 PR 2: coaching tasks from accepted findings (design D2 to D6, ADR 0062).

The reviewed calls are real (HOM-74/HOM-76 fixtures): ``sup`` reviews and completes the
call, so the human outcomes are ``greeting``, ``fee_explained`` and ``closing`` passed,
``off_topic`` failed (the accepted finding; it allows not applicable) and ``agenda`` not
applicable by conversation.

Personas (``Queue`` of HOM-73, ``plain`` made a second supervisor of team A by HOM-76):
``sup`` supervises team A and creates the tasks; ``plain`` supervises team A and is the
default owner (the coach); ``agent`` is the call's agent and the coachee; ``sup_b``
supervises team B only; ``lapsed`` is a member without authority; ``viewer`` is a viewer.

Every deny-case runs next to its allow-control, and every refusal writes nothing: the
coaching tasks, the audit log and the notifications keep their counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from backend.database.saas_models import (
    AuditEvent,
    CoachingTask,
    Meeting,
    Notification,
)
from backend.routers.v1 import coaching_tasks as coaching_router
from backend.security.authorization import (
    API_KEY_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    WorkspaceRole,
)
from backend.services import notification_service
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_review_appeals_api import (
    Queue,
    _ids_in,
    assign,
    build_appeal_queue,
    change,
    decide,
    filed,
    reviewed,
    set_agent,
    stored_appeal,
    uphold,
)
from backend.tests.test_review_appeals_api import act as appeal_act
from backend.tests.test_review_decisions_api import _scored_item, code
from backend.tests.test_review_queue_access import stranger

ITEMS = "/api/v1/review-items"
TASKS = "/api/v1/coaching-tasks"
MY_TASKS = "/api/v1/my-coaching-tasks"
INSTRUCTION = "Ask the weather question only after the needs check."
ACK_NOTE = "Understood, I will practise it."
FOLLOW_UP = "Heard it right on two later calls."
CANCEL_NOTE = "Covered by the team session."
TASK_KEYS = {
    "view",
    "id",
    "review_item_id",
    "meeting_id",
    "team_id",
    "scorecard_result_id",
    "criterion_id",
    "finding_source",
    "agent_user_id",
    "owner_user_id",
    "created_by_user_id",
    "instruction",
    "due_at",
    "overdue",
    "status",
    "acknowledged_at",
    "acknowledgement_note",
    "follow_up_outcome",
    "follow_up_note",
    "closed_at",
    "closed_by_user_id",
    "cancel_reason",
    "cancel_note",
    "cancelled_at",
    "cancelled_by_user_id",
    "reminder_notified_at",
    "overdue_notified_at",
    "revision",
    "created_at",
    "updated_at",
}
# Never in the coachee's view (design D3 "Agent view"; leader ruling 4).
AGENT_HIDDEN = (
    "team_id",
    "created_by_user_id",
    "closed_by_user_id",
    "cancelled_by_user_id",
    "cancel_note",
    "reminder_notified_at",
    "overdue_notified_at",
)
TEXTS = {
    "coaching_task.assigned": (
        "Coaching task assigned",
        "A supervisor gave you a coaching task. Open it to see what to practise.",
    ),
    "coaching_task.owner_assigned": (
        "Coaching task to follow",
        "You are the coach of a coaching task. Open it to follow it up.",
    ),
    "coaching_task.acknowledged": (
        "Coaching task acknowledged",
        "The agent acknowledged a coaching task you follow.",
    ),
    "coaching_task.due_soon": (
        "Coaching task due soon",
        "A coaching task of yours is due within 24 hours.",
    ),
    "coaching_task.overdue": ("Coaching task overdue", "A coaching task is past its due date."),
    "coaching_task.closed": (
        "Coaching task closed",
        "Your coach closed a coaching task with a follow-up review.",
    ),
    "coaching_task.cancelled": ("Coaching task cancelled", "A coaching task was cancelled."),
}


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


# --- helpers ------------------------------------------------------------------------------


def due(delta: timedelta = timedelta(days=7)) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def create(persona, item_id: str, owner=None, **overrides: Any):
    payload = {
        "criterion_id": "off_topic",
        "owner_user_id": owner.user_id if owner is not None else None,
        "due_at": due(),
        "instruction": INSTRUCTION,
        **overrides,
    }
    return persona.post(f"{ITEMS}/{item_id}/coaching-tasks", json=payload)


def created(persona, item_id: str, owner, **overrides: Any) -> dict:
    response = create(persona, item_id, owner, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def task_on(queue: Queue, *, owner=None, creator=None, agent=None, **overrides: Any) -> dict:
    """A new assigned task on a fresh reviewed call: ``sup`` creates, ``plain`` coaches."""
    target = reviewed(queue, agent=agent)
    return created(creator or queue.sup, target.id, owner or queue.plain, **overrides)


def act(persona, task: dict, action: str, **body: Any):
    payload = {"expected_revision": task["revision"], **body}
    return persona.post(f"{TASKS}/{task['id']}/{action}", json=payload)


def acted(persona, task: dict, action: str, **body: Any) -> dict:
    response = act(persona, task, action, **body)
    assert response.status_code == 200, response.text
    return response.json()


def acknowledged(queue: Queue, task: dict, **body: Any) -> dict:
    return acted(queue.agent, task, "acknowledge", **body)


def closing(**extra: Any) -> dict:
    return {"follow_up_outcome": "improved", "follow_up_note": FOLLOW_UP, **extra}


def counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model)) or 0
            for model in (CoachingTask, AuditEvent, Notification)
        }


def assert_refused(response, status: int, error: str, before: dict) -> None:
    assert response.status_code == status, response.text
    assert code(response) == error, response.text
    assert counts() == before


def stored_task(task_id: str) -> CoachingTask:
    with inspection_session_factory()() as db:
        row = db.get(CoachingTask, task_id)
        assert row is not None
        return row


def set_task(task_id: str, **values: Any) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(CoachingTask).where(CoachingTask.id == task_id).values(**values))
        db.commit()


def set_meeting(meeting_id: str, **values: Any) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(**values))
        db.commit()


def task_events(task_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.target_type == "coaching_task", AuditEvent.target_id == task_id)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def notified(kind: str, task_id: str) -> list[str]:
    """The users with a ``kind`` row about this task, sorted."""
    with inspection_session_factory()() as db:
        rows = db.scalars(select(Notification).where(Notification.kind == kind)).all()
        return sorted(
            row.user_id
            for row in rows
            if isinstance(row.data, dict) and row.data.get("coaching_task_id") == task_id
        )


def freeze(monkeypatch, at: datetime) -> None:
    monkeypatch.setattr(coaching_router, "_now", lambda: at)


def listed(persona, path: str = TASKS, **params: Any) -> list[str]:
    response = persona.get(path, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    return [item["id"] for item in body["items"]]


def routes(item_id: str, task_id: str) -> list[tuple[str, str, dict | None]]:
    """Every coaching route, with a body that passes the shape checks."""
    revision = {"expected_revision": 1}
    return [
        (
            "POST",
            f"{ITEMS}/{item_id}/coaching-tasks",
            {"criterion_id": "off_topic", "owner_user_id": "x", "due_at": "x", "instruction": "x"},
        ),
        ("GET", TASKS, None),
        ("GET", f"{TASKS}/{task_id}", None),
        ("GET", f"{TASKS}/{task_id}/history", None),
        ("POST", f"{TASKS}/{task_id}/reassign", {**revision, "owner_user_id": "x"}),
        ("POST", f"{TASKS}/{task_id}/acknowledge", revision),
        ("POST", f"{TASKS}/{task_id}/close", {**revision, **closing()}),
        ("POST", f"{TASKS}/{task_id}/cancel", {**revision, "cancel_reason": "other"}),
        ("GET", MY_TASKS, None),
    ]


# --- AC13, AC14: the permission and the route policy -------------------------------------


def test_coaching_respond_belongs_to_every_role_and_never_to_a_key():
    assert Permission.COACHING_RESPOND.value == "coaching:respond"
    holders = {
        role for role, granted in ROLE_PERMISSIONS.items() if Permission.COACHING_RESPOND in granted
    }
    assert holders == set(WorkspaceRole)
    assert Permission.COACHING_RESPOND not in API_KEY_SCOPES


def test_the_session_payload_lists_the_permission_for_every_role(queue):
    for persona in (queue.owner, queue.admin, queue.agent, queue.viewer):
        me = persona.client.get("/api/v1/auth/me", cookies=persona.cookies)
        assert me.status_code == 200, me.text
        (workspace,) = [w for w in me.json()["workspaces"] if w["id"] == queue.workspace_id]
        assert "coaching:respond" in workspace["permissions"]


def test_an_api_key_gets_403_on_every_coaching_route_and_cannot_carry_the_scope(client, queue):
    owner = queue.owner
    task = task_on(queue)
    before = counts()
    every_scope = sorted(permission.value for permission in API_KEY_SCOPES)
    key = _mint(client, owner.headers, owner.cookies, name="all", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": owner.workspace_id})
    for method, path, body in routes(task["review_item_id"], task["id"]):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert code(response) == "api_key_scope_denied", (method, path)
    refused = _mint(client, owner.headers, owner.cookies, name="coach", scopes=["coaching:respond"])
    assert refused.status_code == 422, refused.text
    after = counts()  # minting the key writes its own audit event only
    assert after["coaching_tasks"] == before["coaching_tasks"]
    assert after["notifications"] == before["notifications"]
    assert [event.event_type for event in task_events(task["id"])] == ["coaching_task.created"]


def test_every_coaching_write_needs_the_csrf_token(queue):
    task = task_on(queue)
    before = counts()
    for persona in (queue.agent, queue.owner, queue.plain):
        for method, path, body in routes(task["review_item_id"], task["id"]):
            if method != "POST":
                continue
            response = persona.client.post(
                path,
                json=body,
                cookies=persona.cookies,
                headers={"X-Workspace-ID": persona.workspace_id},
            )
            assert response.status_code == 403, (path, response.text)
            assert code(response) == "csrf_failed", path
    assert counts() == before
    # Allow-control: the same acknowledge with the token.
    assert act(queue.agent, task, "acknowledge").status_code == 200


def test_a_viewer_reaches_the_coachee_routes_but_not_the_supervisor_routes(queue):
    task = task_on(queue, agent=queue.viewer)
    assert listed(queue.viewer, MY_TASKS) == [task["id"]]
    assert queue.viewer.get(f"{TASKS}/{task['id']}").json()["view"] == "agent"
    for method, path, body in routes(task["review_item_id"], task["id"]):
        supervisor_only = (
            path.startswith(ITEMS)
            or path == TASKS
            or path.endswith(("/reassign", "/close", "/cancel"))
        )
        if not supervisor_only:
            continue
        response = queue.viewer.client.request(
            method, path, json=body, cookies=queue.viewer.cookies, headers=queue.viewer.headers
        )
        assert response.status_code == 403, (path, response.text)
        assert code(response) == "permission_denied", path
    # Allow-control: the viewer coachee acknowledges.
    assert act(queue.viewer, task, "acknowledge").status_code == 200


# --- AC1: create --------------------------------------------------------------------------


def test_create_writes_the_task_one_event_and_the_two_notifications(queue):
    target = reviewed(queue)
    before = counts()
    task = created(queue.sup, target.id, queue.plain)
    assert set(task) == TASK_KEYS
    assert task["view"] == "supervisor"
    assert (task["status"], task["revision"], task["finding_source"]) == (
        "assigned",
        1,
        "human_result",
    )
    assert task["agent_user_id"] == queue.agent.user_id  # the call's current agent
    assert task["owner_user_id"] == queue.plain.user_id
    assert task["created_by_user_id"] == queue.sup.user_id
    assert task["review_item_id"] == target.id and task["meeting_id"] == target.meeting_id
    assert task["criterion_id"] == "off_topic" and task["instruction"] == INSTRUCTION
    assert task["team_id"] == queue.team_a and task["overdue"] is False
    assert counts() == {
        "coaching_tasks": before["coaching_tasks"] + 1,
        "audit_events": before["audit_events"] + 1,
        "notifications": before["notifications"] + 2,
    }
    (event,) = task_events(task["id"])
    assert event.event_type == "coaching_task.created"
    assert event.actor_user_id == queue.sup.user_id
    details = dict(event.details)
    assert datetime.fromisoformat(details.pop("due_at")) == datetime.fromisoformat(task["due_at"])
    assert details == {
        "review_item_id": target.id,
        "scorecard_result_id": task["scorecard_result_id"],
        "criterion_id": "off_topic",
        "finding_source": "human_result",
        "agent_user_id": queue.agent.user_id,
        "owner_user_id": queue.plain.user_id,
        "revision": 1,
    }
    assert notified("coaching_task.assigned", task["id"]) == [queue.agent.user_id]
    assert notified("coaching_task.owner_assigned", task["id"]) == [queue.plain.user_id]
    with inspection_session_factory()() as db:
        for row in db.scalars(
            select(Notification).where(Notification.kind.like("coaching_task.%"))
        ):
            assert (row.title, row.body) == TEXTS[row.kind]
            assert row.data == {
                "coaching_task_id": task["id"],
                "review_item_id": target.id,
                "meeting_id": target.meeting_id,
            }


def test_an_owner_who_creates_gets_no_owner_notification(queue):
    task = task_on(queue, creator=queue.plain, owner=queue.plain)
    assert notified("coaching_task.owner_assigned", task["id"]) == []
    assert notified("coaching_task.assigned", task["id"]) == [queue.agent.user_id]


def test_the_instruction_is_stored_stripped(queue):
    task = task_on(queue, instruction="  \tPractise the greeting.\n")
    assert task["instruction"] == "Practise the greeting."
    assert stored_task(task["id"]).instruction == "Practise the greeting."


def test_a_task_after_a_granted_appeal_names_the_appeal_result(queue):
    """The final outcome comes from the appeal result when the item has one."""
    target = reviewed(queue)
    appeal = assign(queue.owner, filed(queue.agent, target.id, ["greeting"]), queue.plain)
    decided = decide(queue.plain, appeal, [change("greeting", "failed")])
    assert decided.status_code == 200, decided.text
    off_topic = created(queue.sup, target.id, queue.plain)
    assert off_topic["finding_source"] == "appeal_result"
    greeting = created(queue.sup, target.id, queue.plain, criterion_id="greeting")
    assert greeting["finding_source"] == "appeal_result"


def test_an_upheld_appeal_leaves_the_human_result_as_the_source(queue):
    target = reviewed(queue)
    appeal = assign(queue.owner, filed(queue.agent, target.id, ["greeting"]), queue.plain)
    assert decide(queue.plain, appeal, [uphold("greeting")]).status_code == 200
    assert created(queue.sup, target.id, queue.plain)["finding_source"] == "human_result"


# --- AC2: the create refusals, in order ---------------------------------------------------


BAD = {
    "criterion_id": "nope",
    "owner_user_id": "nobody",
    "due_at": "yesterday",
    "instruction": "\t\n",
}


def test_create_checks_run_in_the_design_order(queue):
    target = reviewed(queue)
    before = counts()
    # 1. Not visible (another team's supervisor).
    assert_refused(
        queue.sup_b.post(f"{ITEMS}/{target.id}/coaching-tasks", json=BAD),
        404,
        "review_item_not_found",
        before,
    )
    # 2. The creator is the call's current agent (an admin sees every call).
    own = reviewed(queue, agent=queue.admin)
    before = counts()
    assert_refused(
        queue.admin.post(f"{ITEMS}/{own.id}/coaching-tasks", json=BAD),
        422,
        "coaching_actor_is_agent",
        before,
    )
    # 3. Not completed (still in review).
    in_review = _scored_item(queue, agent_user_id=queue.agent.user_id)
    before = counts()
    assert_refused(
        queue.sup.post(f"{ITEMS}/{in_review.item['id']}/coaching-tasks", json=BAD),
        409,
        "review_item_not_coachable",
        before,
    )
    # 4. An open appeal.
    appealed = reviewed(queue)
    appeal = filed(queue.agent, appealed.id, ["greeting"])
    before = counts()
    assert_refused(
        queue.sup.post(f"{ITEMS}/{appealed.id}/coaching-tasks", json=BAD),
        409,
        "review_appeal_open",
        before,
    )
    # 5. No current agent.
    agentless = reviewed(queue)
    set_meeting(agentless.meeting_id, agent_user_id=None)
    before = counts()
    assert_refused(
        queue.sup.post(f"{ITEMS}/{agentless.id}/coaching-tasks", json=BAD),
        409,
        "coaching_no_agent",
        before,
    )
    # 6. Not a criterion of the result.
    assert_refused(
        queue.sup.post(f"{ITEMS}/{target.id}/coaching-tasks", json=BAD),
        404,
        "criterion_not_found",
        before,
    )
    # 7. Its final outcome is not failed.
    assert_refused(
        queue.sup.post(
            f"{ITEMS}/{target.id}/coaching-tasks", json={**BAD, "criterion_id": "greeting"}
        ),
        422,
        "coaching_finding_not_failed",
        before,
    )
    # 8. An active task exists for the finding.
    first = created(queue.sup, target.id, queue.plain)
    before = counts()
    assert_refused(
        queue.sup.post(
            f"{ITEMS}/{target.id}/coaching-tasks", json={**BAD, "criterion_id": "off_topic"}
        ),
        409,
        "coaching_task_exists",
        before,
    )
    # 9. The owner is the call's current agent.
    fresh = reviewed(queue)
    before = counts()
    body = {**BAD, "criterion_id": "off_topic", "owner_user_id": queue.agent.user_id}
    assert_refused(
        queue.sup.post(f"{ITEMS}/{fresh.id}/coaching-tasks", json=body),
        422,
        "coaching_owner_is_agent",
        before,
    )
    # 10. The owner has no review authority over the call.
    body = {**body, "owner_user_id": queue.sup_b.user_id}
    assert_refused(
        queue.sup.post(f"{ITEMS}/{fresh.id}/coaching-tasks", json=body),
        422,
        "review_assignee_ineligible",
        before,
    )
    # 11. A bad due time.
    body = {**body, "owner_user_id": queue.plain.user_id}
    assert_refused(
        queue.sup.post(f"{ITEMS}/{fresh.id}/coaching-tasks", json=body),
        422,
        "coaching_due_invalid",
        before,
    )
    # 12. A blank instruction.
    body = {**body, "due_at": due()}
    assert_refused(
        queue.sup.post(f"{ITEMS}/{fresh.id}/coaching-tasks", json=body),
        422,
        "coaching_instruction_required",
        before,
    )
    # Allow-controls: each of the refused requests, made right.
    assert created(queue.sup, fresh.id, queue.plain)["status"] == "assigned"
    assert created(queue.owner, own.id, queue.plain)["agent_user_id"] == queue.admin.user_id
    assert appeal_act(queue.agent, appeal, "withdraw").status_code == 200
    assert created(queue.sup, appealed.id, queue.plain)["status"] == "assigned"
    set_meeting(agentless.meeting_id, agent_user_id=queue.agent.user_id)
    assert created(queue.sup, agentless.id, queue.plain)["status"] == "assigned"
    assert acted(queue.plain, first, "cancel", cancel_reason="duplicate")["status"] == "cancelled"
    assert created(queue.sup, target.id, queue.plain)["status"] == "assigned"


def test_only_a_completed_item_with_a_human_result_is_coachable(queue):
    in_review = _scored_item(queue, agent_user_id=queue.agent.user_id)
    before = counts()
    assert_refused(
        create(queue.sup, in_review.item["id"], queue.plain),
        409,
        "review_item_not_coachable",
        before,
    )
    assert created(queue.sup, reviewed(queue).id, queue.plain)["status"] == "assigned"


def test_the_criterion_must_be_one_of_the_result(queue):
    target = reviewed(queue)
    before = counts()
    for value in ("nope", "OFF_TOPIC", "", 7, None, ["off_topic"]):
        assert_refused(
            create(queue.sup, target.id, queue.plain, criterion_id=value),
            404,
            "criterion_not_found",
            before,
        )
    assert created(queue.sup, target.id, queue.plain)["criterion_id"] == "off_topic"


def test_only_a_failed_final_outcome_is_a_finding(queue):
    target = reviewed(queue)
    before = counts()
    # passed, the reviewer's override to passed, and not applicable by conversation.
    for criterion_id in ("greeting", "fee_explained", "closing", "agenda"):
        assert_refused(
            create(queue.sup, target.id, queue.plain, criterion_id=criterion_id),
            422,
            "coaching_finding_not_failed",
            before,
        )
    # A criterion an appeal changed to passed is no finding either.
    overturned = reviewed(queue)
    appeal = assign(queue.owner, filed(queue.agent, overturned.id, ["off_topic"]), queue.plain)
    assert decide(queue.plain, appeal, [change("off_topic", "passed")]).status_code == 200
    before = counts()
    assert_refused(
        create(queue.sup, overturned.id, queue.plain),
        422,
        "coaching_finding_not_failed",
        before,
    )
    assert created(queue.sup, target.id, queue.plain)["criterion_id"] == "off_topic"


def test_one_active_task_per_finding_and_a_new_one_after_close_or_cancel(queue):
    target = reviewed(queue)
    first = created(queue.sup, target.id, queue.plain)
    before = counts()
    assert_refused(create(queue.owner, target.id, queue.admin), 409, "coaching_task_exists", before)
    # Still active once acknowledged.
    first = acknowledged(queue, first)
    before = counts()
    assert_refused(create(queue.owner, target.id, queue.admin), 409, "coaching_task_exists", before)
    # Closed: a new task is allowed; then cancelled: another one.
    acted(queue.plain, first, "close", **closing())
    second = created(queue.owner, target.id, queue.admin)
    acted(queue.admin, second, "cancel", cancel_reason="other")
    assert created(queue.sup, target.id, queue.plain)["status"] == "assigned"


def test_the_owner_needs_review_authority_and_is_never_the_agent(queue):
    target = reviewed(queue)
    before = counts()
    assert_refused(
        create(queue.sup, target.id, queue.agent), 422, "coaching_owner_is_agent", before
    )
    for owner in (queue.sup_b, queue.lapsed, queue.viewer):
        assert_refused(
            create(queue.sup, target.id, owner), 422, "review_assignee_ineligible", before
        )
    for value in ("not-a-user", "", 5, None):
        assert_refused(
            create(queue.sup, target.id, owner_user_id=value),
            422,
            "review_assignee_ineligible",
            before,
        )
    # Allow-controls: a supervisor of the team, the creator itself, an admin.
    for owner in (queue.plain, queue.admin):
        task = created(queue.sup, reviewed(queue).id, owner)
        assert task["owner_user_id"] == owner.user_id
    assert created(queue.sup, target.id, queue.sup)["owner_user_id"] == queue.sup.user_id


def test_the_due_time_is_later_than_now_and_at_most_ninety_days_ahead(queue, monkeypatch):
    target = reviewed(queue)
    # A whole second, after every membership and team assignment of the fixture began
    # (``check_assignee`` reads them at ``now``).
    now = (datetime.now(UTC) + timedelta(seconds=2)).replace(microsecond=0)
    freeze(monkeypatch, now)
    before = counts()
    for value in (
        (now - timedelta(minutes=1)).isoformat(),
        now.isoformat(),
        (now + timedelta(days=90, minutes=1)).isoformat(),
        (now + timedelta(days=7)).replace(tzinfo=None).isoformat(),  # no offset
        "2026-13-01T00:00:00Z",
        "1890000000",
        1890000000,
        None,
    ):
        assert_refused(
            create(queue.sup, target.id, queue.plain, due_at=value),
            422,
            "coaching_due_invalid",
            before,
        )
    edge = created(queue.sup, target.id, queue.plain, due_at=(now + timedelta(days=90)).isoformat())
    assert datetime.fromisoformat(edge["due_at"]) == now + timedelta(days=90)
    other = created(
        queue.sup,
        reviewed(queue).id,
        queue.plain,
        due_at=(now + timedelta(minutes=1)).astimezone(UTC).isoformat(),
    )
    assert datetime.fromisoformat(other["due_at"]).utcoffset() == timedelta(0)


def test_the_instruction_is_one_to_two_thousand_characters_of_text(queue):
    target = reviewed(queue)
    before = counts()
    for value in ("", "   ", "\t\n", " \n\t ", "x" * 2001, "\x00x", 5, None, ["x"]):
        assert_refused(
            create(queue.sup, target.id, queue.plain, instruction=value),
            422,
            "coaching_instruction_required",
            before,
        )
    assert created(queue.sup, target.id, queue.plain, instruction="x" * 2000)["instruction"] == (
        "x" * 2000
    )
    assert (
        created(queue.sup, reviewed(queue).id, queue.plain, instruction="x")["instruction"] == "x"
    )


def test_the_create_body_forbids_unknown_keys(queue):
    target = reviewed(queue)
    before = counts()
    response = create(queue.sup, target.id, queue.plain, agent_user_id=queue.sup.user_id)
    assert response.status_code == 422, response.text
    assert counts() == before


# --- AC3: attribution and independence ----------------------------------------------------


def test_the_coachee_and_a_member_agent_never_use_the_supervisor_routes(queue):
    task = task_on(queue)
    before = counts()
    # The coachee (a member of team A, not a supervisor) sees no task in the list and
    # gets 404 on every supervisor write; it never creates.
    assert task["id"] not in listed(queue.agent, status=["assigned", "acknowledged"])
    for action, body in (
        ("reassign", {"owner_user_id": queue.admin.user_id}),
        ("close", closing()),
        ("cancel", {"cancel_reason": "other"}),
    ):
        assert_refused(
            act(queue.agent, task, action, **body), 404, "coaching_task_not_found", before
        )
    assert_refused(
        create(queue.agent, task["review_item_id"], queue.plain),
        404,
        "review_item_not_found",
        before,
    )
    # Allow-control: the coachee's own routes.
    assert listed(queue.agent, MY_TASKS) == [task["id"]]
    assert queue.agent.get(f"{TASKS}/{task['id']}").json()["view"] == "agent"


def test_an_admin_coachee_never_sees_the_task_as_a_supervisor(queue):
    task = task_on(queue, agent=queue.admin, creator=queue.owner)
    before = counts()
    assert task["id"] not in listed(queue.admin)
    detail = queue.admin.get(f"{TASKS}/{task['id']}")
    assert detail.json()["view"] == "agent"
    for action, body in (
        ("reassign", {"owner_user_id": queue.sup.user_id}),
        ("close", closing()),
        ("cancel", {"cancel_reason": "other"}),
    ):
        assert_refused(
            act(queue.admin, task, action, **body), 404, "coaching_task_not_found", before
        )
    # Allow-control: the owner of the workspace sees it as a supervisor.
    assert task["id"] in listed(queue.owner)


def test_an_admin_who_is_the_current_agent_sees_the_task_but_never_acts(queue):
    """Leader ruling 11 (ADR 0051/0057): an owner or admin sees every task, also on their
    own call, but never creates, reassigns, closes or cancels there.  The admin owns the
    task here, so the close reaches the agent check (a non-owner gets 403 first)."""
    task = task_on(queue, owner=queue.admin)
    set_agent(queue.owner, task["meeting_id"], queue.admin)
    task = acknowledged(queue, task)  # the coachee still acknowledges
    before = counts()
    assert task["id"] in listed(queue.admin)
    assert queue.admin.get(f"{TASKS}/{task['id']}").json()["view"] == "supervisor"
    for action, body in (
        ("reassign", {"owner_user_id": queue.sup.user_id}),
        ("close", closing()),
        ("cancel", {"cancel_reason": "agent_changed"}),
    ):
        assert_refused(
            act(queue.admin, task, action, **body), 422, "coaching_actor_is_agent", before
        )
    assert_refused(
        create(queue.admin, task["review_item_id"], queue.plain, criterion_id="greeting"),
        422,
        "coaching_actor_is_agent",
        before,
    )
    # Allow-control: another supervisor cancels with agent_changed.
    cancelled = acted(queue.owner, task, "cancel", cancel_reason="agent_changed")
    assert cancelled["cancel_reason"] == "agent_changed"


def test_after_the_agent_changes_the_task_keeps_its_coachee(queue):
    task = task_on(queue)
    set_agent(queue.owner, task["meeting_id"], queue.sup)
    # The coachee stays; the former agent still sees and acknowledges the task.
    assert stored_task(task["id"]).agent_user_id == queue.agent.user_id
    assert listed(queue.agent, MY_TASKS) == [task["id"]]
    task = acknowledged(queue, task)
    # The new agent (a member supervisor of the team) no longer sees it as a supervisor.
    before = counts()
    assert task["id"] not in listed(queue.sup)
    for action, body in (
        ("reassign", {"owner_user_id": queue.admin.user_id}),
        ("cancel", {"cancel_reason": "agent_changed"}),
    ):
        assert_refused(act(queue.sup, task, action, **body), 404, "coaching_task_not_found", before)
    assert listed(queue.sup, MY_TASKS) == []
    # The owner still closes it.
    assert acted(queue.plain, task, "close", **closing())["status"] == "closed"


# --- AC4: acknowledge ---------------------------------------------------------------------


def test_only_the_coachee_acknowledges_and_only_from_assigned(queue):
    task = task_on(queue)
    before = counts()
    for persona in (queue.plain, queue.owner, queue.sup):
        assert_refused(act(persona, task, "acknowledge"), 403, "coaching_not_agent", before)
    assert_refused(
        act(queue.agent, {**task, "revision": 2}, "acknowledge"),
        409,
        "coaching_task_revision_conflict",
        before,
    )
    done = acknowledged(queue, task, note=ACK_NOTE)
    assert (done["status"], done["revision"]) == ("acknowledged", 2)
    assert done["acknowledgement_note"] == ACK_NOTE and done["acknowledged_at"] is not None
    assert notified("coaching_task.acknowledged", task["id"]) == [queue.plain.user_id]
    assert [event.event_type for event in task_events(task["id"])] == [
        "coaching_task.created",
        "coaching_task.acknowledged",
    ]
    # A second acknowledge: the stale revision first, then the state.
    before = counts()
    assert_refused(
        act(queue.agent, task, "acknowledge"), 409, "coaching_task_revision_conflict", before
    )
    assert_refused(
        act(queue.agent, done, "acknowledge"), 409, "coaching_task_state_invalid", before
    )


def test_the_acknowledgement_note_is_optional_and_at_most_two_thousand(queue):
    task = task_on(queue)
    before = counts()
    for value in ("x" * 2001, 5, ["x"], "\x00"):
        assert_refused(
            act(queue.agent, task, "acknowledge", note=value), 422, "coaching_note_invalid", before
        )
    assert acknowledged(queue, task, note=" " + "x" * 2000 + "\n")["acknowledgement_note"] == (
        "x" * 2000
    )
    # Blank or missing: no note (the note is optional).
    for note in ("\t\n", None):
        other = acknowledged(queue, task_on(queue), note=note)
        assert other["acknowledgement_note"] is None
    assert acknowledged(queue, task_on(queue))["acknowledgement_note"] is None


# --- AC5: close (the follow-up review) ----------------------------------------------------


def test_only_the_owner_closes_and_only_an_acknowledged_task(queue):
    task = task_on(queue)
    before = counts()
    assert_refused(
        act(queue.plain, task, "close", **closing()), 409, "coaching_task_not_acknowledged", before
    )
    task = acknowledged(queue, task)
    before = counts()
    for persona in (queue.sup, queue.admin, queue.owner):
        assert_refused(act(persona, task, "close", **closing()), 403, "coaching_not_owner", before)
    closed = acted(queue.plain, task, "close", **closing())
    assert (closed["status"], closed["revision"]) == ("closed", 3)
    assert closed["follow_up_outcome"] == "improved" and closed["follow_up_note"] == FOLLOW_UP
    assert closed["closed_by_user_id"] == queue.plain.user_id and closed["closed_at"]
    assert notified("coaching_task.closed", task["id"]) == [queue.agent.user_id]
    event = task_events(task["id"])[-1]
    # The closure audit: the outcome, no note.
    assert event.event_type == "coaching_task.closed"
    assert event.actor_user_id == queue.plain.user_id
    assert event.details == {"follow_up_outcome": "improved", "revision": 3}


def test_the_follow_up_needs_an_outcome_of_the_list_and_a_note(queue):
    task = acknowledged(queue, task_on(queue))
    before = counts()
    for body in (
        {"follow_up_note": FOLLOW_UP},
        {"follow_up_outcome": "great", "follow_up_note": FOLLOW_UP},
        {"follow_up_outcome": 1, "follow_up_note": FOLLOW_UP},
        {"follow_up_outcome": "improved"},
        {"follow_up_outcome": "improved", "follow_up_note": "\t\n"},
        {"follow_up_outcome": "improved", "follow_up_note": "   "},
        {"follow_up_outcome": "improved", "follow_up_note": "x" * 2001},
    ):
        assert_refused(
            act(queue.plain, task, "close", **body), 422, "coaching_follow_up_invalid", before
        )
    for outcome in ("partially_improved", "not_improved"):
        other = acknowledged(queue, task_on(queue))
        closed = acted(queue.plain, other, "close", follow_up_outcome=outcome, follow_up_note="x")
        assert closed["follow_up_outcome"] == outcome
    closed = acted(queue.plain, task, "close", **closing(follow_up_note=" " + "y" * 2000 + "\t"))
    assert closed["follow_up_note"] == "y" * 2000


def test_close_checks_run_in_order(queue):
    task = acknowledged(queue, task_on(queue))
    before = counts()
    bad = {"follow_up_outcome": "great"}
    # Visible: another team's supervisor.
    assert_refused(act(queue.sup_b, task, "close", **bad), 404, "coaching_task_not_found", before)
    # Revision before ownership.
    assert_refused(
        act(queue.sup, {**task, "revision": 9}, "close", **bad),
        409,
        "coaching_task_revision_conflict",
        before,
    )
    assert_refused(act(queue.sup, task, "close", **bad), 403, "coaching_not_owner", before)
    # The owner, with a bad body: the body is checked last.
    assert_refused(
        act(queue.plain, task, "close", **bad), 422, "coaching_follow_up_invalid", before
    )
    # Allow-control: the owner's follow-up review.
    assert acted(queue.plain, task, "close", **closing())["status"] == "closed"


def test_a_closed_task_takes_no_further_write(queue):
    task = acknowledged(queue, task_on(queue))
    closed = acted(queue.plain, task, "close", **closing())
    before = counts()
    assert_refused(
        act(queue.agent, closed, "acknowledge"), 409, "coaching_task_state_invalid", before
    )
    for action, body in (
        ("reassign", {"owner_user_id": queue.admin.user_id}),
        ("close", closing()),
        ("cancel", {"cancel_reason": "other"}),
    ):
        assert_refused(
            act(queue.plain, closed, action, **body), 409, "coaching_task_closed", before
        )


# --- AC6: cancel --------------------------------------------------------------------------


def test_the_owner_or_a_workspace_admin_cancels_and_the_others_do_not(queue):
    task = task_on(queue)
    before = counts()
    assert_refused(
        act(queue.sup, task, "cancel", cancel_reason="other"), 403, "coaching_not_owner", before
    )
    # A workspace admin who is not the owner: allowed; the owner is notified too.
    cancelled = acted(
        queue.admin, task, "cancel", cancel_reason="duplicate", cancel_note=CANCEL_NOTE
    )
    assert (cancelled["status"], cancelled["cancel_reason"]) == ("cancelled", "duplicate")
    assert cancelled["cancel_note"] == CANCEL_NOTE
    assert cancelled["cancelled_by_user_id"] == queue.admin.user_id
    assert notified("coaching_task.cancelled", task["id"]) == sorted(
        [queue.agent.user_id, queue.plain.user_id]
    )
    event = task_events(task["id"])[-1]
    assert event.event_type == "coaching_task.cancelled"
    assert event.details == {"cancel_reason": "duplicate", "revision": 2}
    # The owner cancels an acknowledged task: only the coachee is notified.
    other = acknowledged(queue, task_on(queue))
    acted(queue.plain, other, "cancel", cancel_reason="other")
    assert notified("coaching_task.cancelled", other["id"]) == [queue.agent.user_id]
    # The workspace owner cancels too.
    third = task_on(queue)
    assert acted(queue.owner, third, "cancel", cancel_reason="other")["status"] == "cancelled"


def test_the_cancel_reason_is_in_the_list_and_the_note_at_most_two_thousand(queue):
    task = task_on(queue)
    before = counts()
    for body in (
        {},
        {"cancel_reason": "bored"},
        {"cancel_reason": None},
        {"cancel_reason": "other", "cancel_note": "x" * 2001},
        {"cancel_reason": "other", "cancel_note": 5},
    ):
        assert_refused(
            act(queue.plain, task, "cancel", **body), 422, "coaching_cancel_invalid", before
        )
    for reason in ("finding_overturned", "agent_changed", "duplicate", "other"):
        cancelled = acted(queue.plain, task_on(queue), "cancel", cancel_reason=reason)
        assert cancelled["cancel_reason"] == reason and cancelled["cancel_note"] is None
    assert (
        acted(queue.plain, task, "cancel", cancel_reason="other", cancel_note="x" * 2000)[
            "cancel_note"
        ]
        == "x" * 2000
    )


# --- AC7: reassign ------------------------------------------------------------------------


def test_reassign_checks_run_in_order_and_the_same_owner_writes_nothing(queue):
    task = task_on(queue)
    before = counts()
    assert_refused(
        act(queue.sup_b, task, "reassign", owner_user_id=queue.admin.user_id),
        404,
        "coaching_task_not_found",
        before,
    )
    assert_refused(
        act(queue.sup, {**task, "revision": 5}, "reassign", owner_user_id=queue.agent.user_id),
        409,
        "coaching_task_revision_conflict",
        before,
    )
    assert_refused(
        act(queue.sup, task, "reassign", owner_user_id=queue.agent.user_id),
        422,
        "coaching_owner_is_agent",
        before,
    )
    for owner in (queue.sup_b, queue.lapsed, queue.viewer):
        assert_refused(
            act(queue.sup, task, "reassign", owner_user_id=owner.user_id),
            422,
            "review_assignee_ineligible",
            before,
        )
    # The same owner again: no write, no event, no notification.
    same = acted(queue.sup, task, "reassign", owner_user_id=queue.plain.user_id)
    assert same["revision"] == 1
    assert counts() == before
    moved = acted(queue.sup, task, "reassign", owner_user_id=queue.admin.user_id)
    assert (moved["owner_user_id"], moved["revision"]) == (queue.admin.user_id, 2)
    assert notified("coaching_task.owner_assigned", task["id"]) == sorted(
        [queue.plain.user_id, queue.admin.user_id]
    )
    event = task_events(task["id"])[-1]
    assert event.event_type == "coaching_task.reassigned"
    assert event.details == {
        "owner_user_id_before": queue.plain.user_id,
        "owner_user_id_after": queue.admin.user_id,
        "revision": 2,
    }
    # Away and back: still one owner notification per user.
    back = acted(queue.sup, moved, "reassign", owner_user_id=queue.plain.user_id)
    assert notified("coaching_task.owner_assigned", task["id"]) == sorted(
        [queue.plain.user_id, queue.admin.user_id]
    )
    # A coachee can never be the owner, even after the call's agent changed.
    set_agent(queue.owner, task["meeting_id"], queue.sup)
    before = counts()
    assert_refused(
        act(queue.owner, back, "reassign", owner_user_id=queue.agent.user_id),
        422,
        "coaching_owner_is_agent",
        before,
    )


# --- AC8: who sees the task ---------------------------------------------------------------


def test_the_supervisors_with_authority_see_the_task_and_nobody_else(client, email_outbox, queue):
    task = task_on(queue)
    for persona in (queue.owner, queue.admin, queue.sup, queue.plain):
        assert task["id"] in listed(persona), persona.user_id
        view = persona.get(f"{TASKS}/{task['id']}")
        assert view.status_code == 200 and view.json()["view"] == "supervisor"
    for persona in (queue.sup_b, queue.lapsed):
        assert listed(persona) == []
        hidden = persona.get(f"{TASKS}/{task['id']}")
        assert hidden.status_code == 404 and code(hidden) == "coaching_task_not_found"
        assert persona.get(f"{TASKS}/{task['id']}/history").status_code == 404
        assert listed(persona, MY_TASKS) == []
    viewer = queue.viewer.get(TASKS)
    assert viewer.status_code == 403 and code(viewer) == "permission_denied"
    assert queue.viewer.get(f"{TASKS}/{task['id']}").status_code == 404
    outsider = stranger(client, email_outbox, "coaching-stranger@example.com")
    assert listed(outsider, MY_TASKS) == []
    assert outsider.get(f"{TASKS}/{task['id']}").status_code == 404


def test_the_coachee_sees_status_acknowledgement_and_follow_up_but_no_reviewer(queue):
    task = task_on(queue)
    task = acknowledged(queue, task, note=ACK_NOTE)
    task = acted(queue.plain, task, "close", **closing())
    view = queue.agent.get(f"{TASKS}/{task['id']}").json()
    assert set(view) == TASK_KEYS and view["view"] == "agent"
    for key in AGENT_HIDDEN:
        assert view[key] is None, key
    assert (view["status"], view["acknowledgement_note"], view["follow_up_outcome"]) == (
        "closed",
        ACK_NOTE,
        "improved",
    )
    assert view["follow_up_note"] == FOLLOW_UP and view["owner_user_id"] == queue.plain.user_id
    assert view["revision"] == 3 and view["agent_user_id"] == queue.agent.user_id
    # No reviewer of the review item and not the creator, anywhere in the agent payloads.
    payloads = [view, queue.agent.get(MY_TASKS).json()]
    for payload in payloads:
        assert queue.sup.user_id not in _ids_in(payload)
    # The supervisor view has them.
    supervisor = queue.owner.get(f"{TASKS}/{task['id']}").json()
    assert supervisor["created_by_user_id"] == queue.sup.user_id
    assert supervisor["closed_by_user_id"] == queue.plain.user_id
    assert supervisor["team_id"] == queue.team_a


def test_the_cancel_note_stays_hidden_from_the_coachee(queue):
    task = acted(queue.plain, task_on(queue), "cancel", cancel_reason="other", cancel_note="x")
    view = queue.agent.get(f"{TASKS}/{task['id']}").json()
    assert (view["cancel_reason"], view["cancel_note"]) == ("other", None)
    assert view["cancelled_by_user_id"] is None and view["cancelled_at"] is not None


def test_a_deleted_call_hides_its_tasks(queue):
    task = task_on(queue)
    set_meeting(task["meeting_id"], deleted_at=datetime.now(UTC))
    assert listed(queue.owner, status=["assigned"]) == []
    assert queue.agent.get(f"{TASKS}/{task['id']}").status_code == 404
    assert listed(queue.agent, MY_TASKS) == []


# --- the lists ----------------------------------------------------------------------------


def test_the_supervisor_list_shows_every_status_by_default_due_first(queue, monkeypatch):
    late = task_on(queue, due_at=due(timedelta(days=9)))
    soon = task_on(queue, due_at=due(timedelta(days=2)))
    middle = acknowledged(queue, task_on(queue, due_at=due(timedelta(days=5))))
    middle = acted(queue.plain, middle, "close", **closing())
    gone = acted(
        queue.plain, task_on(queue, due_at=due(timedelta(days=7))), "cancel", cancel_reason="other"
    )
    assert listed(queue.owner) == [soon["id"], middle["id"], gone["id"], late["id"]]
    assert listed(queue.owner, status=["closed", "cancelled"]) == [middle["id"], gone["id"]]
    assert listed(queue.owner, status="assigned") == [soon["id"], late["id"]]
    assert listed(queue.owner, owner_user_id=queue.admin.user_id) == []
    assert listed(queue.owner, owner_user_id=queue.plain.user_id)[0] == soon["id"]
    assert listed(queue.owner, agent_user_id=queue.agent.user_id)[0] == soon["id"]
    assert listed(queue.owner, agent_user_id=queue.sup.user_id) == []
    # Overdue: active with now >= due_at.
    set_task(soon["id"], due_at=datetime.now(UTC) - timedelta(hours=1))
    assert listed(queue.owner, overdue="true") == [soon["id"]]
    assert soon["id"] not in listed(queue.owner, overdue="false")
    assert queue.owner.get(f"{TASKS}/{soon['id']}").json()["overdue"] is True
    # Keyset pages.
    first = queue.owner.get(TASKS, params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = queue.owner.get(TASKS, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [item["id"] for item in first["items"] + second["items"]] == listed(queue.owner)
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"status": "open"},
        {"overdue": "maybe"},
        {"owner_user_id": "Not-A-Uuid"},
        {"agent_user_id": "x"},
    ],
)
def test_a_bad_filter_is_refused(queue, params):
    response = queue.owner.get(TASKS, params=params)
    assert response.status_code == 422 and code(response) == "coaching_filter_invalid", (
        response.text
    )


def test_a_bad_cursor_is_refused(queue):
    for path in (TASKS, MY_TASKS):
        response = queue.owner.get(path, params={"cursor": "not-a-cursor"})
        assert response.status_code == 400 and code(response) == "invalid_cursor", path
    bad = queue.agent.get(MY_TASKS, params={"status": "open"})
    assert bad.status_code == 422 and code(bad) == "coaching_filter_invalid"


@pytest.mark.parametrize("limit", [0, 101])
def test_the_list_limit_is_one_to_a_hundred(queue, limit):
    for path in (TASKS, MY_TASKS):
        assert queue.owner.get(path, params={"limit": limit}).status_code == 422, path


def test_my_tasks_are_the_callers_own_newest_first(queue):
    first = task_on(queue)
    second = task_on(queue)
    other = task_on(queue, agent=queue.viewer)
    set_task(first["id"], created_at=datetime.now(UTC) - timedelta(hours=2))
    assert listed(queue.agent, MY_TASKS) == [second["id"], first["id"]]
    assert listed(queue.viewer, MY_TASKS) == [other["id"]]
    acknowledged(queue, second)
    assert listed(queue.agent, MY_TASKS, status="acknowledged") == [second["id"]]
    page = queue.agent.get(MY_TASKS, params={"limit": 1}).json()
    assert [item["id"] for item in page["items"]] == [second["id"]]
    rest = queue.agent.get(MY_TASKS, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert [item["id"] for item in rest["items"]] == [first["id"]] and rest["next_cursor"] is None
    for item in page["items"]:
        assert item["view"] == "agent"


# --- AC9: the history ---------------------------------------------------------------------


def test_the_history_is_oldest_first_and_the_coachee_view_hides_other_people(queue):
    task = task_on(queue)  # sup creates, plain coaches
    task = acted(queue.owner, task, "reassign", owner_user_id=queue.admin.user_id)
    task = acknowledged(queue, task, note=ACK_NOTE)
    task = acted(queue.admin, task, "close", **closing())
    supervisor = queue.owner.get(f"{TASKS}/{task['id']}/history")
    assert supervisor.status_code == 200, supervisor.text
    entries = supervisor.json()["items"]
    assert [entry["event_type"] for entry in entries] == [
        "coaching_task.created",
        "coaching_task.reassigned",
        "coaching_task.acknowledged",
        "coaching_task.closed",
    ]
    assert [(entry["actor_user_id"], entry["actor_role"]) for entry in entries] == [
        (queue.sup.user_id, "reviewer"),
        (queue.owner.user_id, "reviewer"),
        (queue.agent.user_id, "coachee"),
        (queue.admin.user_id, "owner"),
    ]
    assert entries[1]["details"]["owner_user_id_before"] == queue.plain.user_id
    coachee = queue.agent.get(f"{TASKS}/{task['id']}/history").json()["items"]
    assert [(entry["actor_user_id"], entry["actor_role"]) for entry in coachee] == [
        (None, "reviewer"),
        (None, "reviewer"),
        (queue.agent.user_id, "coachee"),
        (queue.admin.user_id, "owner"),
    ]
    # Only the coachee's and the current owner's ids stay in the details.
    assert coachee[0]["details"]["agent_user_id"] == queue.agent.user_id
    assert "owner_user_id" not in coachee[0]["details"]  # plain is no longer the owner
    assert "owner_user_id_before" not in coachee[1]["details"]
    assert coachee[1]["details"]["owner_user_id_after"] == queue.admin.user_id
    allowed = {queue.agent.user_id, queue.admin.user_id}
    for entry in coachee:
        assert not (_ids_in(entry) & {queue.sup.user_id, queue.plain.user_id, queue.owner.user_id})
    for entry in coachee:
        for key, value in entry["details"].items():
            if "user_id" in key:
                assert value in allowed, key
    # No event carries the instruction or a note.
    for event in task_events(task["id"]):
        text = repr(event.details)
        for secret in (INSTRUCTION, ACK_NOTE, FOLLOW_UP):
            assert secret not in text


def test_the_history_follows_the_visibility(queue):
    task = task_on(queue)
    for persona in (queue.sup_b, queue.lapsed, queue.viewer):
        response = persona.get(f"{TASKS}/{task['id']}/history")
        assert response.status_code == 404 and code(response) == "coaching_task_not_found"
    assert queue.agent.get(f"{TASKS}/{task['id']}/history").status_code == 200


# --- AC11: the appeal hook ----------------------------------------------------------------


def _appealed_task(queue: Queue, criteria: list[str], *, owner=None) -> tuple[dict, dict]:
    target = reviewed(queue)
    task = created(queue.sup, target.id, owner or queue.admin)
    appeal = assign(queue.owner, filed(queue.agent, target.id, criteria), queue.plain)
    return task, appeal


@pytest.mark.parametrize("outcome", ["passed", "not_applicable"])
def test_an_appeal_that_overturns_the_finding_cancels_the_task(queue, outcome):
    task, appeal = _appealed_task(queue, ["off_topic"])
    task = acknowledged(queue, task)  # an acknowledged task is active too
    decided = decide(queue.plain, appeal, [change("off_topic", outcome)])
    assert decided.status_code == 200, decided.text
    stored = stored_task(task["id"])
    assert (stored.status, stored.cancel_reason, stored.revision) == (
        "cancelled",
        "finding_overturned",
        3,
    )
    assert stored.cancelled_by_user_id == queue.plain.user_id and stored.cancelled_at
    event = task_events(task["id"])[-1]
    assert event.event_type == "coaching_task.cancelled"
    assert event.actor_user_id == queue.plain.user_id
    assert event.details == {"cancel_reason": "finding_overturned", "revision": 3}
    assert notified("coaching_task.cancelled", task["id"]) == sorted(
        [queue.agent.user_id, queue.admin.user_id]
    )


def test_an_overturn_by_the_owner_notifies_only_the_coachee(queue):
    task, appeal = _appealed_task(queue, ["off_topic"], owner=queue.plain)
    assert decide(queue.plain, appeal, [change("off_topic", "passed")]).status_code == 200
    assert stored_task(task["id"]).status == "cancelled"
    assert notified("coaching_task.cancelled", task["id"]) == [queue.agent.user_id]


def test_an_uphold_or_another_criterion_leaves_the_task_alone(queue):
    upheld, appeal = _appealed_task(queue, ["off_topic"])
    assert decide(queue.plain, appeal, [uphold("off_topic")]).status_code == 200
    # Another criterion changed to failed; the coached criterion was not appealed.
    other, second = _appealed_task(queue, ["greeting"])
    assert decide(queue.plain, second, [change("greeting", "failed")]).status_code == 200
    for task in (upheld, other):
        stored = stored_task(task["id"])
        assert (stored.status, stored.revision) == ("assigned", 1), task["id"]
        assert [event.event_type for event in task_events(task["id"])] == ["coaching_task.created"]
        assert notified("coaching_task.cancelled", task["id"]) == []


def test_a_closed_task_is_not_cancelled_by_a_later_appeal(queue):
    target = reviewed(queue)
    task = acknowledged(queue, created(queue.sup, target.id, queue.admin))
    task = acted(queue.admin, task, "close", **closing())
    appeal = assign(queue.owner, filed(queue.agent, target.id, ["off_topic"]), queue.plain)
    assert decide(queue.plain, appeal, [change("off_topic", "passed")]).status_code == 200
    assert stored_task(task["id"]).status == "closed"


def test_a_decider_who_is_the_coachee_cannot_cancel_their_own_task(queue):
    """D2: nobody acts on a task as a supervisor when they are its coachee.  The
    coachee stopped being the call's agent, so HOM-76 alone lets them decide."""
    target = reviewed(queue, agent=queue.admin)  # call C, agent X (an admin)
    task = created(queue.sup, target.id, queue.plain)  # coachee X, owner S
    assert task["agent_user_id"] == queue.admin.user_id
    set_agent(queue.owner, target.meeting_id, queue.agent)  # the agent becomes Y
    appeal = assign(  # Y appeals the coached finding; X stays eligible for HOM-76
        queue.owner, filed(queue.agent, target.id, ["off_topic"]), queue.admin
    )
    before = counts()
    refused = decide(queue.admin, appeal, [change("off_topic", "passed")])
    assert refused.status_code == 422, refused.text
    assert code(refused) == "appeal_decider_is_party", refused.text
    assert counts() == before  # no task change, no audit event, no notification
    stored = stored_appeal(appeal["id"])  # the appeal itself is untouched
    assert (stored.status, stored.revision) == (appeal["status"], appeal["revision"])
    assert stored.disposition is None and stored.decided_by_user_id is None
    assert (stored_task(task["id"]).status, stored_task(task["id"]).revision) == ("assigned", 1)
    # Allow-control: another eligible supervisor decides the same change.
    reassigned = assign(queue.owner, appeal, queue.plain)
    decided = decide(queue.plain, reassigned, [change("off_topic", "passed")])
    assert decided.status_code == 200, decided.text
    cancelled = stored_task(task["id"])
    assert (cancelled.status, cancelled.cancel_reason) == ("cancelled", "finding_overturned")
    assert cancelled.cancelled_by_user_id == queue.plain.user_id


# --- the notification texts ---------------------------------------------------------------


def test_the_notification_texts_are_fixed_and_hold_no_instruction(queue):
    assert {kind: notification_service.TEXTS[kind] for kind in TEXTS} == TEXTS
    for title, body in TEXTS.values():
        assert INSTRUCTION not in title + body
    # The HOM-76 kinds are unchanged.
    assert notification_service.FILED == "review_appeal.filed"
    assert notification_service.MAX_FILED_RECIPIENTS == 50
