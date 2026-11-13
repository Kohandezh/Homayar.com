"""HOM-77 PR 2: concurrent coaching writes (PostgreSQL only, SPEC-pr2 AC12).

Acknowledge, close and cancel are a compare-and-set on the task's revision, so of two
writes that read the same revision exactly one lands; the other answers ``409
coaching_task_revision_conflict`` (or the state code when the winner moved the task on).
Two creates of one finding race on ``uq_coaching_tasks_active_finding``: one 201, one
``409 coaching_task_exists``.  The losers write no row, no audit event and no
notification.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import CoachingTask, Notification
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_coaching_tasks_api import (
    Queue,
    acknowledged,
    act,
    closing,
    code,
    create,
    stored_task,
    task_events,
    task_on,
)
from backend.tests.test_review_appeals_api import build_appeal_queue, reviewed

pytestmark = pytest.mark.postgres

STATE_CODES = {
    "coaching_task_revision_conflict",
    "coaching_task_closed",
    "coaching_task_state_invalid",
    "coaching_task_not_acknowledged",
}


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _together(*calls) -> list:
    from backend.tests.test_review_queue_concurrency import _together as together

    return together(*calls)


def _count(model, **where) -> int:
    with inspection_session_factory()() as db:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return db.scalar(query) or 0


def _kinds(task_id: str, kind: str) -> int:
    with inspection_session_factory()() as db:
        rows = db.scalars(select(Notification).where(Notification.kind == kind)).all()
        return sum(
            1
            for row in rows
            if isinstance(row.data, dict) and row.data.get("coaching_task_id") == task_id
        )


def _one_landed(responses: list) -> tuple:
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409], [response.text for response in responses]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) in STATE_CODES, loser.text
    return loser


def test_two_acknowledges_with_the_same_revision_exactly_one_lands(queue):
    _requires_postgres()
    task = task_on(queue)
    responses = _together(
        lambda: act(queue.agent, task, "acknowledge"),
        lambda: act(queue.agent, task, "acknowledge"),
    )
    loser = _one_landed(responses)
    assert code(loser) == "coaching_task_revision_conflict"
    assert loser.json()["error"]["details"] == {"current_revision": 2}
    assert stored_task(task["id"]).revision == 2
    types = [event.event_type for event in task_events(task["id"])]
    assert types.count("coaching_task.acknowledged") == 1
    assert _kinds(task["id"], "coaching_task.acknowledged") == 1


def test_two_closes_with_the_same_revision_exactly_one_lands(queue):
    _requires_postgres()
    task = acknowledged(queue, task_on(queue))
    responses = _together(
        lambda: act(queue.plain, task, "close", **closing()),
        lambda: act(queue.plain, task, "close", **closing(follow_up_outcome="not_improved")),
    )
    _one_landed(responses)
    stored = stored_task(task["id"])
    assert (stored.status, stored.revision) == ("closed", 3)
    types = [event.event_type for event in task_events(task["id"])]
    assert types.count("coaching_task.closed") == 1
    assert _kinds(task["id"], "coaching_task.closed") == 1


def test_a_close_and_a_cancel_never_both_land(queue):
    _requires_postgres()
    task = acknowledged(queue, task_on(queue))
    responses = _together(
        lambda: act(queue.plain, task, "close", **closing()),
        lambda: act(queue.admin, task, "cancel", cancel_reason="other"),
    )
    _one_landed(responses)
    stored = stored_task(task["id"])
    assert stored.status in {"closed", "cancelled"} and stored.revision == 3
    types = [event.event_type for event in task_events(task["id"])]
    finishing = [t for t in types if t in {"coaching_task.closed", "coaching_task.cancelled"}]
    assert len(finishing) == 1
    total = _kinds(task["id"], "coaching_task.closed") + _kinds(
        task["id"], "coaching_task.cancelled"
    )
    assert total == (1 if stored.status == "closed" else 2)  # a cancel by an admin: both


def test_two_creates_of_one_finding_exactly_one_lands(queue):
    _requires_postgres()
    target = reviewed(queue)
    responses = _together(
        lambda: create(queue.sup, target.id, queue.plain),
        lambda: create(queue.owner, target.id, queue.admin),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 409], [response.text for response in responses]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "coaching_task_exists"
    assert _count(CoachingTask, review_item_id=target.id) == 1
    (winner,) = [response.json() for response in responses if response.status_code == 201]
    assert [event.event_type for event in task_events(winner["id"])] == ["coaching_task.created"]
    assert _kinds(winner["id"], "coaching_task.assigned") == 1
    assert _count(Notification, kind="coaching_task.assigned") == 1
