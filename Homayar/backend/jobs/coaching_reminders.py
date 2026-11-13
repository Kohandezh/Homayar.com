"""Coaching due-soon reminders and overdue notices (HOM-77, ADR 0062 D4).

Per workspace (discovered like the other sweepers, each processed in its own
tenant-bound session), for each active (``assigned`` or ``acknowledged``) coaching task of
a call that is not deleted:

* **Overdue** (``now >= due_at`` and ``overdue_notified_at IS NULL``): one compare-and-set
  sets ``overdue_notified_at`` and, when still empty, ``reminder_notified_at``.  Only the
  run whose update changed the row notifies the coachee and the owner
  (``coaching_task.overdue``) and writes the audit event ``coaching_task.overdue``
  (system actor).  So a task already overdue on its first sweep gets the overdue notice
  and never a late due-soon reminder.
* **Due soon** (``due_at - 24h <= now < due_at`` and ``reminder_notified_at IS NULL``): the
  same compare-and-set on ``reminder_notified_at``; only the winner notifies the coachee
  (``coaching_task.due_soon``).

Each task commits on its own.  Two sweeps at the same time race on the row: the second
update waits for the first commit, then finds the mark set and changes nothing, so each
notice goes out once.  The marks are not a user's write: they never change ``revision``
or ``updated_at``.  A run handles at most ``WORKSPACE_LIMIT`` tasks per workspace; the
next run takes the rest.

Without Celery beat, run ``python -m backend.jobs.coaching_reminders``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    COACHING_REMINDER_LEAD_HOURS,
    COACHING_TASK_ACTIVE_STATUSES,
    CoachingTask,
    Meeting,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.checkpoints import discover_workspaces
from backend.jobs.leases import as_utc, database_now
from backend.services import notification_service
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger

LEAD = timedelta(hours=COACHING_REMINDER_LEAD_HOURS)
# Leader ruling 5: at most this many tasks per workspace and run.
WORKSPACE_LIMIT = 500
_MARK_TYPE = CoachingTask.__table__.c.reminder_notified_at.type


def _reminder_mark(now: datetime):
    """``COALESCE(reminder_notified_at, :now)``, with ``now`` typed like the column."""
    return func.coalesce(CoachingTask.reminder_notified_at, literal(now, _MARK_TYPE))


def _due_tasks(db: Session, *, workspace_id: str, now: datetime) -> list[tuple[str, datetime]]:
    """The active tasks of live calls with a notice due at ``now``, earliest first."""
    rows = db.execute(
        select(CoachingTask.id, CoachingTask.due_at)
        .join(
            Meeting,
            and_(Meeting.id == CoachingTask.meeting_id, Meeting.workspace_id == workspace_id),
        )
        .where(
            CoachingTask.workspace_id == workspace_id,
            Meeting.deleted_at.is_(None),
            CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
            or_(
                and_(CoachingTask.due_at <= now, CoachingTask.overdue_notified_at.is_(None)),
                and_(
                    CoachingTask.due_at > now,
                    CoachingTask.due_at <= now + LEAD,
                    CoachingTask.reminder_notified_at.is_(None),
                ),
            ),
        )
        .order_by(CoachingTask.due_at.asc(), CoachingTask.id.asc())
        .limit(WORKSPACE_LIMIT)
    ).all()
    return [(task_id, due_at) for task_id, due_at in rows]


def _task(db: Session, task_id: str) -> CoachingTask:
    return db.execute(
        select(CoachingTask)
        .where(CoachingTask.id == task_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _mark_overdue(db: Session, *, workspace_id: str, task_id: str, now: datetime) -> bool:
    changed = db.execute(
        update(CoachingTask)
        .where(
            CoachingTask.id == task_id,
            CoachingTask.workspace_id == workspace_id,
            CoachingTask.overdue_notified_at.is_(None),
            CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
            CoachingTask.due_at <= now,
        )
        .values(
            overdue_notified_at=now,
            # A task overdue on its first sweep never gets a late due-soon reminder.
            reminder_notified_at=_reminder_mark(now),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        return False
    task = _task(db, task_id)
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_OVERDUE,
        user_ids=[task.agent_user_id, task.owner_user_id],
        now=now,
    )
    due_at = as_utc(task.due_at)
    assert due_at is not None
    record_audit_event(
        db,
        event_type=AuditEventType.COACHING_TASK_OVERDUE,
        actor_user_id=None,
        workspace_id=workspace_id,
        target_type="coaching_task",
        target_id=task.id,
        details={"due_at": due_at.isoformat()},
    )
    return True


def _mark_due_soon(db: Session, *, workspace_id: str, task_id: str, now: datetime) -> bool:
    changed = db.execute(
        update(CoachingTask)
        .where(
            CoachingTask.id == task_id,
            CoachingTask.workspace_id == workspace_id,
            CoachingTask.reminder_notified_at.is_(None),
            CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
            CoachingTask.due_at > now,
            CoachingTask.due_at <= now + LEAD,
        )
        .values(reminder_notified_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        return False
    task = _task(db, task_id)
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_DUE_SOON,
        user_ids=[task.agent_user_id],
        now=now,
    )
    return True


def run_coaching_reminders(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    workspace_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Send the due notices of every workspace; returns counts."""
    settings = settings or get_settings()
    counts = {"workspaces": 0, "due_soon": 0, "overdue": 0}
    workspaces = (
        list(workspace_ids)
        if workspace_ids is not None
        else discover_workspaces(
            interval_seconds=settings.coaching_reminder_sweep_interval_seconds, now=now
        )
    )
    for workspace_id in workspaces:
        counts["workspaces"] += 1
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            current = as_utc(now) or database_now(db)
            due = _due_tasks(db, workspace_id=workspace_id, now=current)
            db.rollback()
            for task_id, due_at in due:
                if current >= (as_utc(due_at) or current):
                    if _mark_overdue(db, workspace_id=workspace_id, task_id=task_id, now=current):
                        counts["overdue"] += 1
                elif _mark_due_soon(db, workspace_id=workspace_id, task_id=task_id, now=current):
                    counts["due_soon"] += 1
                db.commit()
    if counts["due_soon"] or counts["overdue"]:
        logger.info("COACHING_REMINDERS %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(run_coaching_reminders(), ensure_ascii=False))
    sys.exit(0)
