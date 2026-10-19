"""Workspace administrator's view of share links (HOM-192).

``list_workspace_shares`` pages every share link of a workspace newest first
(``created_at`` then ``id``, both descending) with an opaque cursor, and reads
each page with one query: the share joined to its meeting. The query filters
on the caller's workspace itself (row-level security is extra defence) and
leaves out shares of soft-deleted meetings, which public access refuses too.
It selects only the listed columns: the token hash and the recipients are
never read.

A share's status follows the access check of
``backend.services.share_service.resolve_share_grant``: revoked wins, then
expired (``expires_at <= now``), else active. One SQL expression computes it
on the database clock, for both the filter and the listed value, so a link
always shows the status it was filtered by.
"""

from __future__ import annotations

import base64
import binascii
import enum
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, case, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import Meeting, ShareAudience, ShareLink
from backend.jobs.leases import as_utc, database_now


class ShareStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvalidShareCursor(ValueError):
    pass


@dataclass(frozen=True)
class AdminShareRow:
    id: str
    meeting_id: str
    meeting_title: str
    audience: ShareAudience
    status: ShareStatus
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime | None
    access_count: int
    last_accessed_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class AdminSharePage:
    rows: list[AdminShareRow]
    next_cursor: str | None


def _encode_share_cursor(row: AdminShareRow) -> str:
    created_at = as_utc(row.created_at)
    assert created_at is not None
    payload = {"v": 1, "share_created_at": created_at.isoformat(), "share_id": row.id}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_share_cursor(cursor: str) -> tuple[datetime, str]:
    # Its own keys: a jobs ("created_at") or audit-events ("occurred_at")
    # cursor is refused here.
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "share_created_at", "share_id"}:
            raise ValueError
        if (
            # JSON true equals 1 in Python: the type is checked too
            type(payload["v"]) is not int
            or payload["v"] != 1
            or not isinstance(payload["share_created_at"], str)
            or not isinstance(payload["share_id"], str)
        ):
            raise ValueError
        share_id = str(uuid.UUID(payload["share_id"]))
        created_at = datetime.fromisoformat(payload["share_created_at"])
        if created_at.tzinfo is None:
            raise ValueError
        # Stored times are UTC; SQLite compares them as text, so another offset
        # for the same instant must not change the page.
        created_at = created_at.astimezone(UTC)
    except (
        ValueError,
        TypeError,
        OverflowError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise InvalidShareCursor("Invalid share pagination cursor") from exc
    return created_at, share_id


def _status_expression(now: datetime) -> ColumnElement[str]:
    return case(
        (ShareLink.revoked_at.is_not(None), ShareStatus.REVOKED.value),
        (
            and_(ShareLink.expires_at.is_not(None), ShareLink.expires_at <= now),
            ShareStatus.EXPIRED.value,
        ),
        else_=ShareStatus.ACTIVE.value,
    )


def list_workspace_shares(
    db: Session,
    *,
    workspace_id: str,
    limit: int,
    cursor: str | None = None,
    status: ShareStatus | None = None,
    audience: ShareAudience | None = None,
) -> AdminSharePage:
    status_expression = _status_expression(database_now(db))
    query = (
        select(
            ShareLink.id,
            ShareLink.meeting_id,
            Meeting.title.label("meeting_title"),
            ShareLink.audience,
            status_expression.label("status"),
            ShareLink.created_by_user_id,
            ShareLink.created_at,
            ShareLink.expires_at,
            ShareLink.access_count,
            ShareLink.last_accessed_at,
            ShareLink.revoked_at,
        )
        .select_from(ShareLink)
        .join(
            Meeting,
            and_(Meeting.id == ShareLink.meeting_id, Meeting.workspace_id == workspace_id),
        )
        .where(ShareLink.workspace_id == workspace_id, Meeting.deleted_at.is_(None))
    )
    if status is not None:
        query = query.where(status_expression == status.value)
    if audience is not None:
        query = query.where(ShareLink.audience == audience)
    if cursor is not None:
        cursor_time, cursor_id = _decode_share_cursor(cursor)
        query = query.where(
            or_(
                ShareLink.created_at < cursor_time,
                and_(ShareLink.created_at == cursor_time, ShareLink.id < cursor_id),
            )
        )

    rows = db.execute(
        query.order_by(ShareLink.created_at.desc(), ShareLink.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page_rows = [
        AdminShareRow(**{**row._asdict(), "status": ShareStatus(row.status)})
        for row in rows[:limit]
    ]
    return AdminSharePage(
        rows=page_rows,
        next_cursor=_encode_share_cursor(page_rows[-1]) if has_more else None,
    )
