"""Workspace owner's register of media objects on legal hold (HOM-192).

``list_workspace_legal_holds`` pages a workspace's held objects, the hold
that needs review first first (``legal_hold_review_at`` then ``id``, both
ascending), with an opaque cursor, and reads each page with one query: the
object joined, when there is one, to its meeting.

The query filters on the caller's workspace itself, the meeting join
included (row-level security is extra defence).  Holds of soft-deleted
conversations and of objects without a meeting are listed too: a hold may be
placed on an object of a deleted conversation, and a legal register that
hides some holds is wrong.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import MediaLifecycleState, MediaObject, Meeting
from backend.jobs.leases import as_utc


class InvalidLegalHoldCursor(ValueError):
    pass


class InvalidDueBefore(ValueError):
    pass


@dataclass(frozen=True)
class LegalHoldRow:
    media: MediaObject
    meeting_title: str | None  # None when there is no meeting row


@dataclass(frozen=True)
class LegalHoldPage:
    rows: list[LegalHoldRow]
    next_cursor: str | None


def _encode_hold_cursor(media: MediaObject) -> str:
    review_at = as_utc(media.legal_hold_review_at)
    assert review_at is not None  # ck_media_hold_review: a held row has one
    payload = {"v": 1, "review_at": review_at.isoformat(), "id": media.id}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_hold_cursor(cursor: str) -> tuple[datetime, str]:
    # Its own keys: a jobs ("created_at") or audit-events ("occurred_at")
    # cursor is refused here.
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "review_at", "id"}:
            raise ValueError
        # Exactly the int 1: JSON true and 1.0 also equal 1 in Python.
        if (
            type(payload["v"]) is not int
            or payload["v"] != 1
            or not isinstance(payload["review_at"], str)
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        media_id = str(uuid.UUID(payload["id"]))
        parsed = datetime.fromisoformat(payload["review_at"])
        if parsed.tzinfo is None:
            raise ValueError
        # In the try: a forged offset may leave the datetime range in UTC
        # (9999-12-31T23:59:59-01:00).
        review_at = parsed.astimezone(UTC)
    except (
        ValueError,
        TypeError,
        OverflowError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise InvalidLegalHoldCursor("Invalid legal hold pagination cursor") from exc
    return review_at, media_id


def _utc_due_before(due_before: datetime) -> datetime:
    """A naive value is read as UTC, an aware one is converted to UTC; one
    that leaves the datetime range in UTC (9999-12-31T23:59:59-01:00) is refused."""
    try:
        utc_due_before = as_utc(due_before)
    except OverflowError as exc:
        raise InvalidDueBefore("due_before is outside the supported date range") from exc
    assert utc_due_before is not None
    return utc_due_before


def list_workspace_legal_holds(
    db: Session,
    *,
    workspace_id: str,
    limit: int,
    cursor: str | None = None,
    due_before: datetime | None = None,
) -> LegalHoldPage:
    """``due_before`` keeps holds with ``legal_hold_review_at <= due_before``;
    a value without a time zone is read as UTC.  Raises ``InvalidDueBefore``
    or ``InvalidLegalHoldCursor`` for a value the query cannot use."""
    query = (
        select(MediaObject, Meeting.title)
        .outerjoin(
            Meeting,
            and_(Meeting.id == MediaObject.meeting_id, Meeting.workspace_id == workspace_id),
        )
        .where(
            MediaObject.workspace_id == workspace_id,
            MediaObject.lifecycle_state == MediaLifecycleState.LEGAL_HOLD,
        )
    )
    # UTC before binding: SQLite stores the wall clock without the offset.
    if due_before is not None:
        query = query.where(MediaObject.legal_hold_review_at <= _utc_due_before(due_before))
    if cursor is not None:
        cursor_time, cursor_id = _decode_hold_cursor(cursor)
        query = query.where(
            or_(
                MediaObject.legal_hold_review_at > cursor_time,
                and_(
                    MediaObject.legal_hold_review_at == cursor_time,
                    MediaObject.id > cursor_id,
                ),
            )
        )

    rows = db.execute(
        query.order_by(MediaObject.legal_hold_review_at.asc(), MediaObject.id.asc()).limit(
            limit + 1
        )
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    return LegalHoldPage(
        rows=[LegalHoldRow(media=media, meeting_title=title) for media, title in page_rows],
        next_cursor=_encode_hold_cursor(page_rows[-1][0]) if has_more else None,
    )
