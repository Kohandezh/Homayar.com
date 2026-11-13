"""HOM-77 PR 2: the coaching reminder and overdue sweep (design D4, ADR 0062).

``run_coaching_reminders`` runs on a fixed ``now`` here.  For each active task of a call
that is not deleted it sends, once:

* ``coaching_task.due_soon`` to the coachee when ``due_at - 24h <= now < due_at``;
* ``coaching_task.overdue`` to the coachee and the owner when ``now >= due_at``, with one
  ``coaching_task.overdue`` audit event (system actor).

A task already overdue on its first sweep gets only the overdue notice.  The two marks
(``reminder_notified_at``, ``overdue_notified_at``) are set by a compare-and-set, so a
second run, or a run at the same time, sends nothing new.  The marks never change the
task's ``revision`` or ``updated_at``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.config import Settings
from backend.database.saas_models import AuditEvent, Notification
from backend.jobs import coaching_reminders
from backend.jobs.coaching_reminders import run_coaching_reminders
from backend.observability import heartbeats
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_coaching_tasks_api import (
    Queue,
    acknowledged,
    acted,
    closing,
    notified,
    set_meeting,
    set_task,
    stored_task,
    task_events,
    task_on,
)
from backend.tests.test_review_appeals_api import build_appeal_queue

SWEEP_TASK = "backend.jobs.sweep_coaching_reminders"


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


def sweep(queue: Queue, now: datetime) -> dict[str, int]:
    return run_coaching_reminders(now=now, workspace_ids=[queue.workspace_id])


def due_of(task: dict) -> datetime:
    return datetime.fromisoformat(task["due_at"])


def coaching_rows(kind: str) -> int:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(func.count()).select_from(Notification).where(Notification.kind == kind)
            )
            or 0
        )


def overdue_events() -> int:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "coaching_task.overdue")
            )
            or 0
        )


# --- due soon -----------------------------------------------------------------------------


def test_a_task_inside_the_window_gets_one_reminder_and_no_revision_change(queue):
    task = task_on(queue)
    due_at = due_of(task)
    before = stored_task(task["id"])
    # Outside the window: nothing.
    assert sweep(queue, due_at - timedelta(hours=25))["due_soon"] == 0
    assert stored_task(task["id"]).reminder_notified_at is None
    # Inside: one reminder to the coachee, the mark set, no audit event.
    now = due_at - timedelta(hours=23)
    counts = sweep(queue, now)
    assert (counts["due_soon"], counts["overdue"]) == (1, 0)
    assert notified("coaching_task.due_soon", task["id"]) == [queue.agent.user_id]
    after = stored_task(task["id"])
    assert after.reminder_notified_at is not None and after.overdue_notified_at is None
    assert (after.revision, after.updated_at, after.status) == (
        before.revision,
        before.updated_at,
        "assigned",
    )
    assert [event.event_type for event in task_events(task["id"])] == ["coaching_task.created"]
    # A second run in the window sends nothing new.
    assert sweep(queue, due_at - timedelta(hours=22))["due_soon"] == 0
    assert notified("coaching_task.due_soon", task["id"]) == [queue.agent.user_id]


def test_an_acknowledged_task_is_still_reminded(queue):
    task = acknowledged(queue, task_on(queue))
    assert sweep(queue, due_of(task) - timedelta(hours=1))["due_soon"] == 1
    assert notified("coaching_task.due_soon", task["id"]) == [queue.agent.user_id]


# --- overdue ------------------------------------------------------------------------------


def test_an_overdue_task_gets_one_notice_and_one_event(queue):
    task = task_on(queue)
    due_at = due_of(task)
    assert sweep(queue, due_at - timedelta(hours=1))["due_soon"] == 1
    events = overdue_events()
    counts = sweep(queue, due_at + timedelta(minutes=1))
    assert (counts["due_soon"], counts["overdue"]) == (0, 1)
    assert notified("coaching_task.overdue", task["id"]) == sorted(
        [queue.agent.user_id, queue.plain.user_id]
    )
    (event,) = [e for e in task_events(task["id"]) if e.event_type == "coaching_task.overdue"]
    assert event.actor_user_id is None
    assert set(event.details) == {"due_at"}
    assert datetime.fromisoformat(event.details["due_at"]) == due_at
    assert overdue_events() == events + 1
    # Again, and again later: nothing new.
    for later in (timedelta(minutes=2), timedelta(days=3)):
        assert sweep(queue, due_at + later) == {"workspaces": 1, "due_soon": 0, "overdue": 0}
    assert overdue_events() == events + 1
    assert len(notified("coaching_task.overdue", task["id"])) == 2
    assert stored_task(task["id"]).revision == task["revision"]


def test_a_task_overdue_on_its_first_sweep_gets_no_reminder(queue):
    task = task_on(queue)
    counts = sweep(queue, due_of(task) + timedelta(hours=1))
    assert (counts["due_soon"], counts["overdue"]) == (0, 1)
    assert notified("coaching_task.due_soon", task["id"]) == []
    stored = stored_task(task["id"])
    # Both marks are set, so a late reminder never follows the overdue notice.
    assert stored.reminder_notified_at is not None and stored.overdue_notified_at is not None
    assert sweep(queue, due_of(task) + timedelta(hours=2))["due_soon"] == 0


def test_the_owner_is_skipped_when_removed(queue):
    task = task_on(queue)
    set_task(task["id"], owner_user_id=None)
    sweep(queue, due_of(task) + timedelta(minutes=1))
    assert notified("coaching_task.overdue", task["id"]) == [queue.agent.user_id]


def test_closed_cancelled_and_deleted_tasks_get_nothing(queue):
    closed = acted(queue.plain, acknowledged(queue, task_on(queue)), "close", **closing())
    cancelled = acted(queue.plain, task_on(queue), "cancel", cancel_reason="other")
    deleted = task_on(queue)
    set_meeting(deleted["meeting_id"], deleted_at=datetime.now(UTC))
    soon = max(due_of(task) for task in (closed, cancelled, deleted)) + timedelta(days=1)
    rows = {
        kind: coaching_rows(kind) for kind in ("coaching_task.due_soon", "coaching_task.overdue")
    }
    assert sweep(queue, soon) == {"workspaces": 1, "due_soon": 0, "overdue": 0}
    assert {kind: coaching_rows(kind) for kind in rows} == rows
    for task in (closed, cancelled, deleted):
        stored = stored_task(task["id"])
        assert stored.reminder_notified_at is None and stored.overdue_notified_at is None


def test_the_sweep_finds_the_workspace_by_itself(queue):
    task = task_on(queue)
    counts = run_coaching_reminders(now=due_of(task) + timedelta(minutes=1))
    assert counts["overdue"] >= 1 and counts["workspaces"] >= 1
    assert notified("coaching_task.overdue", task["id"]) == sorted(
        [queue.agent.user_id, queue.plain.user_id]
    )


def test_the_sweep_handles_at_most_the_limit_per_workspace(queue, monkeypatch):
    assert coaching_reminders.WORKSPACE_LIMIT == 500  # leader ruling 5
    first = task_on(queue)
    second = task_on(queue)
    monkeypatch.setattr(coaching_reminders, "WORKSPACE_LIMIT", 1)
    now = max(due_of(first), due_of(second)) + timedelta(minutes=1)
    assert sweep(queue, now)["overdue"] == 1
    assert sweep(queue, now)["overdue"] == 1  # the next run takes the rest
    assert sweep(queue, now)["overdue"] == 0


# --- the schedule ---------------------------------------------------------------------------


def test_celery_registers_the_sweep_and_its_beat_entry():
    from backend.celery_app import COACHING_REMINDER_TASK, PROCESSING_QUEUE, celery_app
    from backend.config import get_settings
    from backend.jobs import tasks

    assert COACHING_REMINDER_TASK == SWEEP_TASK
    assert tasks.sweep_coaching_reminders_task.name == SWEEP_TASK
    assert SWEEP_TASK in celery_app.tasks
    entry = celery_app.conf.beat_schedule["coaching-reminders"]
    interval = float(get_settings().coaching_reminder_sweep_interval_seconds)
    assert entry == {
        "task": SWEEP_TASK,
        "schedule": interval,
        "options": {"queue": PROCESSING_QUEUE, "expires": interval},
    }
    assert celery_app.conf.task_routes[SWEEP_TASK] == {"queue": PROCESSING_QUEUE}


def test_the_celery_task_runs_the_sweep_under_its_heartbeat(monkeypatch):
    from backend.jobs import tasks

    monkeypatch.setattr(
        coaching_reminders,
        "run_coaching_reminders",
        lambda: {"workspaces": 2, "due_soon": 1, "overdue": 3},
    )
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    try:
        result = tasks.sweep_coaching_reminders_task.run()
    finally:
        heartbeats.use_heartbeat_store(None)
    assert result == {"workspaces": 2, "due_soon": 1, "overdue": 3}
    record = store.read_all()["coaching_reminders"]
    assert json.loads(record["result"]) == result
    assert "last_success" in record and "last_failure" not in record


def test_the_sweep_is_a_periodic_task_with_its_interval_setting(settings):
    assert heartbeats.PERIODIC_TASKS["coaching_reminders"] == (
        "coaching_reminder_sweep_interval_seconds"
    )
    assert heartbeats.expected_interval_seconds("coaching_reminders", settings) == float(
        settings.coaching_reminder_sweep_interval_seconds
    )
    assert Settings(_env_file=None).coaching_reminder_sweep_interval_seconds == 900
    for value in (0, -1):
        with pytest.raises(ValidationError) as caught:
            Settings(_env_file=None, coaching_reminder_sweep_interval_seconds=value)
        assert "COACHING_REMINDER_SWEEP_INTERVAL_SECONDS" in str(caught.value)
    assert Settings(_env_file=None, coaching_reminder_sweep_interval_seconds=1)


# --- PostgreSQL: two sweeps at the same time ------------------------------------------------


@pytest.mark.postgres
def test_two_sweeps_at_the_same_time_send_each_notice_once(queue):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")
    from backend.tests.test_review_queue_concurrency import _together

    soon = [task_on(queue) for _ in range(3)]  # due in 7 days
    late = [task_on(queue, due_at=(datetime.now(UTC) + timedelta(days=9)).isoformat())]
    # At ``now`` the three are overdue and ``late`` is still outside its window; at
    # ``now_late`` only ``late`` is inside its due-soon window.
    now = max(due_of(task) for task in soon) + timedelta(minutes=1)
    late_due = due_of(late[0])
    assert now < late_due - timedelta(hours=24)
    now_late = late_due - timedelta(hours=1)
    events = overdue_events()
    first, second = _together(lambda: sweep(queue, now), lambda: sweep(queue, now))
    for result in (first, second):
        assert isinstance(result, dict), result
    assert first["overdue"] + second["overdue"] == len(soon)
    first, second = _together(lambda: sweep(queue, now_late), lambda: sweep(queue, now_late))
    assert first["due_soon"] + second["due_soon"] == 1
    for task in soon:
        assert notified("coaching_task.overdue", task["id"]) == sorted(
            [queue.agent.user_id, queue.plain.user_id]
        )
    assert notified("coaching_task.due_soon", late[0]["id"]) == [queue.agent.user_id]
    assert overdue_events() == events + len(soon)
