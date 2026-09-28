"""Tenant-scoped voicemail listing, search and analytics.

Every query is bound to one workspace, to live (not deleted) meetings and to
the caller's record scope (``RecordAccess.meeting_clause``), so a team-scoped
member sees exactly the voicemails whose conversations they may open.  Text
search runs over the *current* transcript revision's segments (the shared
``transcript_segments`` table - no second index).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import Meeting, TranscriptRevision, TranscriptSegment
from backend.security.authorization import RecordAccess
from backend.voicemail.classify import normalize_text
from backend.voicemail.models import VoicemailMessage


@dataclass(frozen=True)
class VoicemailFilters:
    status: str | None = None
    category: str | None = None
    priority: str | None = None
    follow_up_state: str | None = None
    assigned_user_id: str | None = None
    caller_number: str | None = None
    received_from: datetime | None = None
    received_to: datetime | None = None
    q: str | None = None
    before: datetime | None = None
    limit: int = 50


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _text_clause(q: str):
    variants = {q.strip(), normalize_text(q)} - {""}
    conditions = [
        func.lower(TranscriptSegment.text).like(f"%{_escape_like(v.lower())}%", escape="\\")
        for v in variants
    ]
    return exists(
        select(TranscriptSegment.id)
        .join(TranscriptRevision, TranscriptRevision.id == TranscriptSegment.revision_id)
        .where(
            TranscriptSegment.meeting_id == VoicemailMessage.meeting_id,
            TranscriptSegment.workspace_id == VoicemailMessage.workspace_id,
            TranscriptRevision.is_current.is_(True),
            or_(*conditions),
        )
    )


def _scoped(workspace_id: str, access: RecordAccess):
    return (
        select(VoicemailMessage)
        .join(Meeting, Meeting.id == VoicemailMessage.meeting_id)
        .where(
            VoicemailMessage.workspace_id == workspace_id,
            Meeting.workspace_id == workspace_id,
            Meeting.deleted_at.is_(None),
            access.meeting_clause(),
        )
    )


def list_voicemails(
    db: Session, *, workspace_id: str, access: RecordAccess, filters: VoicemailFilters
) -> list[VoicemailMessage]:
    query = _scoped(workspace_id, access)
    for column, value in (
        (VoicemailMessage.status, filters.status),
        (VoicemailMessage.category, filters.category),
        (VoicemailMessage.priority, filters.priority),
        (VoicemailMessage.follow_up_state, filters.follow_up_state),
        (VoicemailMessage.assigned_user_id, filters.assigned_user_id),
        (VoicemailMessage.caller_number, filters.caller_number),
    ):
        if value is not None:
            query = query.where(column == value)
    if filters.received_from is not None:
        query = query.where(VoicemailMessage.received_at >= filters.received_from)
    if filters.received_to is not None:
        query = query.where(VoicemailMessage.received_at < filters.received_to)
    if filters.before is not None:
        query = query.where(VoicemailMessage.received_at < filters.before)
    if filters.q and filters.q.strip():
        query = query.where(_text_clause(filters.q))
    query = query.order_by(VoicemailMessage.received_at.desc(), VoicemailMessage.id.desc())
    return list(db.scalars(query.limit(filters.limit)))


def get_voicemail(
    db: Session, *, workspace_id: str, access: RecordAccess, voicemail_id: str
) -> VoicemailMessage | None:
    return db.scalar(_scoped(workspace_id, access).where(VoicemailMessage.id == voicemail_id))


def transcript_excerpt(
    db: Session, voicemail: VoicemailMessage, *, max_chars: int = 600
) -> tuple[str | None, str | None]:
    """``(excerpt, revision_id)`` of the current transcript, or ``(None, None)``."""
    revision = db.scalar(
        select(TranscriptRevision).where(
            TranscriptRevision.meeting_id == voicemail.meeting_id,
            TranscriptRevision.workspace_id == voicemail.workspace_id,
            TranscriptRevision.is_current.is_(True),
        )
    )
    if revision is None:
        return None, None
    texts = db.scalars(
        select(TranscriptSegment.text)
        .where(TranscriptSegment.revision_id == revision.id)
        .order_by(TranscriptSegment.sequence)
    )
    text = " ".join(texts).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text, revision.id


@dataclass
class VoicemailAnalytics:
    received_from: datetime
    received_to: datetime
    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_priority: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    by_follow_up_state: dict[str, int] = field(default_factory=dict)
    by_day: list[tuple[date, int]] = field(default_factory=list)
    followed_up: int = 0
    median_follow_up_seconds: float | None = None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def voicemail_analytics(
    db: Session,
    *,
    workspace_id: str,
    access: RecordAccess,
    received_from: datetime,
    received_to: datetime,
) -> VoicemailAnalytics:
    """Counts by category/priority/status/follow-up/day and median time to follow-up.

    Time to follow-up is ``follow_up_done_at - received_at`` over the
    voicemails of the window whose follow-up is done; ``None`` without any.
    """
    rows = db.execute(
        _scoped(workspace_id, access)
        .with_only_columns(
            VoicemailMessage.category,
            VoicemailMessage.priority,
            VoicemailMessage.status,
            VoicemailMessage.follow_up_state,
            VoicemailMessage.received_at,
            VoicemailMessage.follow_up_done_at,
        )
        .where(
            and_(
                VoicemailMessage.received_at >= received_from,
                VoicemailMessage.received_at < received_to,
            )
        )
    ).all()
    result = VoicemailAnalytics(received_from=received_from, received_to=received_to)
    days: dict[date, int] = {}
    durations: list[float] = []
    for category, priority, status, follow_up, received_at, done_at in rows:
        result.total += 1
        key = category or "unclassified"
        result.by_category[key] = result.by_category.get(key, 0) + 1
        key = priority or "unprioritized"
        result.by_priority[key] = result.by_priority.get(key, 0) + 1
        result.by_status[status] = result.by_status.get(status, 0) + 1
        result.by_follow_up_state[follow_up] = result.by_follow_up_state.get(follow_up, 0) + 1
        day = _as_utc(received_at).astimezone(UTC).date()
        days[day] = days.get(day, 0) + 1
        if done_at is not None:
            durations.append(max(0.0, (_as_utc(done_at) - _as_utc(received_at)).total_seconds()))
    result.by_day = sorted(days.items())
    result.followed_up = len(durations)
    result.median_follow_up_seconds = statistics.median(durations) if durations else None
    return result


def default_window(now: datetime | None = None, days: int = 30) -> tuple[datetime, datetime]:
    end = now or datetime.now(UTC)
    return end - timedelta(days=days), end
