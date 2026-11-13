"""In-app notifications (HOM-76, ADR 0057 D6; HOM-77, ADR 0062 D6).

Rows live in the existing ``notifications`` table; it does not change.  Each row has a
fixed English title and body per ``kind`` (the console translates by ``kind``), never a
name, a note, an instruction or transcript text, and ``data`` holds ids only.  At most
one row exists per user, kind and subject (``data["appeal_id"]`` for an appeal,
``data["coaching_task_id"]`` for a coaching task): a second notify for the same three
writes nothing.

The inbox shows the caller's own rows of the current workspace, newest first
(``created_at`` then ``id``), in keyset pages; ``unread_only`` keeps the rows with no
``read_at``.  Marking a row read is idempotent; another user's row, a row of another
workspace and a missing id are all ``404 notification_not_found``.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import CoachingTask, Notification, ReviewAppeal, new_id
from backend.jobs.leases import as_utc
from backend.services.audit_event_types import AuditEventType
from backend.services.review_queue_service import ReviewQueueError

# Each kind is the audit event type of the same change (the stored string).
FILED = AuditEventType.REVIEW_APPEAL_FILED.value
ASSIGNED = AuditEventType.REVIEW_APPEAL_ASSIGNED.value
DECIDED = AuditEventType.REVIEW_APPEAL_DECIDED.value
WITHDRAWN = AuditEventType.REVIEW_APPEAL_WITHDRAWN.value
# The coaching kinds (HOM-77, ADR 0062 D6).  Some have no audit event of their own: the
# notice to the owner and the due-soon reminder are not changes of the task.
COACHING_ASSIGNED = "coaching_task.assigned"
COACHING_OWNER_ASSIGNED = "coaching_task.owner_assigned"
COACHING_ACKNOWLEDGED = AuditEventType.COACHING_TASK_ACKNOWLEDGED.value
COACHING_DUE_SOON = "coaching_task.due_soon"
COACHING_OVERDUE = AuditEventType.COACHING_TASK_OVERDUE.value
COACHING_CLOSED = AuditEventType.COACHING_TASK_CLOSED.value
COACHING_CANCELLED = AuditEventType.COACHING_TASK_CANCELLED.value
# The fixed texts of ADR 0057 D6, by kind: (title, body).
TEXTS: dict[str, tuple[str, str]] = {
    FILED: (
        "Appeal filed",
        "An agent appealed a completed review. Open the appeals queue to assign it.",
    ),
    ASSIGNED: ("Appeal assigned to you", "You were asked to decide an appeal."),
    DECIDED: (
        "Your appeal was decided",
        "A reviewer decided your appeal. Open it to see the result.",
    ),
    WITHDRAWN: ("Appeal withdrawn", "The agent withdrew an appeal assigned to you."),
    COACHING_ASSIGNED: (
        "Coaching task assigned",
        "A supervisor gave you a coaching task. Open it to see what to practise.",
    ),
    COACHING_OWNER_ASSIGNED: (
        "Coaching task to follow",
        "You are the coach of a coaching task. Open it to follow it up.",
    ),
    COACHING_ACKNOWLEDGED: (
        "Coaching task acknowledged",
        "The agent acknowledged a coaching task you follow.",
    ),
    COACHING_DUE_SOON: (
        "Coaching task due soon",
        "A coaching task of yours is due within 24 hours.",
    ),
    COACHING_OVERDUE: ("Coaching task overdue", "A coaching task is past its due date."),
    COACHING_CLOSED: (
        "Coaching task closed",
        "Your coach closed a coaching task with a follow-up review.",
    ),
    COACHING_CANCELLED: ("Coaching task cancelled", "A coaching task was cancelled."),
}
# A filed appeal notifies at most this many possible deciders: the supervisors of the
# call's current team first, then the owners and admins, each group by user id.
MAX_FILED_RECIPIENTS = 50


def _notify(
    db: Session,
    *,
    workspace_id: str,
    subject_key: str,
    subject_id: str,
    since: datetime,
    kind: str,
    data: dict[str, Any],
    user_ids: Iterable[str | None],
    now: datetime,
) -> list[str]:
    """Add one row per user for ``kind`` about the subject ``data[subject_key]``; skip a
    ``None`` user (removed) and a user who already has a row of this kind for this
    subject.  Returns the users who got a new row, in the given order."""
    wanted = [user_id for user_id in dict.fromkeys(user_ids) if user_id is not None]
    if not wanted:
        return []
    # Bounded in SQL to the subject's lifetime: every row about a subject is written at or
    # after ``since`` (its creation), so older rows (earlier subjects) are never read.
    # The ``data`` check below then keeps only this subject's rows.
    existing = db.execute(
        select(Notification.user_id, Notification.data).where(
            Notification.workspace_id == workspace_id,
            Notification.kind == kind,
            Notification.user_id.in_(wanted),
            Notification.created_at >= since,
        )
    ).all()
    already = {
        user_id
        for user_id, row_data in existing
        if isinstance(row_data, dict) and row_data.get(subject_key) == subject_id
    }
    title, body = TEXTS[kind]
    added: list[str] = []
    for user_id in wanted:
        if user_id in already:
            continue
        db.add(
            Notification(
                id=new_id(),
                workspace_id=workspace_id,
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                data=dict(data),
                created_at=now,
                updated_at=now,
            )
        )
        added.append(user_id)
    db.flush()
    return added


def notify_appeal(
    db: Session, *, appeal: ReviewAppeal, kind: str, user_ids: Iterable[str | None], now: datetime
) -> list[str]:
    """Add one row per user for ``kind`` about ``appeal``; skip a ``None`` user (removed)
    and a user who already has a row of this kind for this appeal.  Returns the users
    who got a new row, in the given order."""
    return _notify(
        db,
        workspace_id=appeal.workspace_id,
        subject_key="appeal_id",
        subject_id=appeal.id,
        since=appeal.created_at,
        kind=kind,
        data={
            "appeal_id": appeal.id,
            "review_item_id": appeal.review_item_id,
            "meeting_id": appeal.meeting_id,
        },
        user_ids=user_ids,
        now=now,
    )


def notify_coaching_task(
    db: Session, *, task: CoachingTask, kind: str, user_ids: Iterable[str | None], now: datetime
) -> list[str]:
    """The same rule for a coaching task (HOM-77): at most one row per user, kind and
    ``coaching_task_id``; ``data`` holds the task, its review item and its meeting."""
    return _notify(
        db,
        workspace_id=task.workspace_id,
        subject_key="coaching_task_id",
        subject_id=task.id,
        since=task.created_at,
        kind=kind,
        data={
            "coaching_task_id": task.id,
            "review_item_id": task.review_item_id,
            "meeting_id": task.meeting_id,
        },
        user_ids=user_ids,
        now=now,
    )


# --- the inbox ----------------------------------------------------------------------------


def _not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "notification_not_found", "Notification not found")


def _invalid_cursor() -> ReviewQueueError:
    return ReviewQueueError(400, "invalid_cursor", "Invalid notification pagination cursor")


def _encode_cursor(row: Notification) -> str:
    created = as_utc(row.created_at)
    assert created is not None
    payload = {"v": 1, "created_at": created.isoformat(), "id": row.id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode("utf-8")
        )
        if not isinstance(payload, dict) or set(payload) != {"v", "created_at", "id"}:
            raise ValueError
        if payload["v"] != 1 or not isinstance(payload["created_at"], str):
            raise ValueError
        row_id = str(uuid.UUID(str(payload["id"])))
        created = datetime.fromisoformat(payload["created_at"])
        if created.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise _invalid_cursor() from None
    return created.astimezone(UTC), row_id


@dataclass(frozen=True)
class NotificationPage:
    rows: list[Notification]
    next_cursor: str | None


def list_inbox(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    unread_only: bool,
    limit: int,
    cursor: str | None,
) -> NotificationPage:
    query = select(Notification).where(
        Notification.workspace_id == workspace_id, Notification.user_id == user_id
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    if cursor is not None:
        created, row_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                Notification.created_at < created,
                and_(Notification.created_at == created, Notification.id < row_id),
            )
        )
    found = list(
        db.scalars(
            query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)
        )
    )
    page = found[:limit]
    return NotificationPage(
        rows=page, next_cursor=_encode_cursor(page[-1]) if len(found) > limit else None
    )


def mark_read(
    db: Session, *, workspace_id: str, user_id: str, notification_id: str, now: datetime
) -> tuple[Notification, bool]:
    """The caller's own row, read; ``True`` when this call set ``read_at``."""
    row = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
        )
    )
    if row is None:
        raise _not_found()
    if row.read_at is not None:
        return row, False
    row.read_at = now
    row.updated_at = now
    db.flush()
    return row, True


def view(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "data": row.data,
        "read_at": as_utc(row.read_at),
        "created_at": as_utc(row.created_at),
    }
