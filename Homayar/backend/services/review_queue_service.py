"""The supervisor review queue (HOM-73, WP14-T02, ADR 0051).

A review item is one conversation (a ``meetings`` row) that a supervisor must review,
with an optional assignee and a due time.  Every read and write runs in the caller's
tenant-bound session, and every query also names the caller's workspace.

Record rules, for caller ``U`` in workspace ``W`` at ``now``:

* **Review admin**: ``U``'s workspace role is owner or admin.  The workspace
  ``record_scope`` makes nobody a review admin: it widens which conversations a member
  may read, not who may judge them.
* **Review authority over a conversation M**: ``U`` is a review admin, or ``M.team_id``
  is a team ``U`` supervises (``supervised_team_ids``), computed from the
  conversation's *current* team.
* **Visible item**: ``M`` is in ``W`` and not deleted, ``U`` has review authority over
  it, and ``U`` is a review admin or not ``M``'s agent.  Anything else answers ``404
  review_item_not_found``, like a missing id or an id of another workspace.
* **Eligible assignee A**: not ``M``'s agent (checked first, ``422
  review_assignee_is_agent``); an effective owner, admin or member of ``W`` with review
  authority over ``M`` (else ``422 review_assignee_ineligible``).

Every write checks, in order: visible (404), ``expected_revision`` equal to the stored
revision (``409 review_item_revision_conflict``), the item active (``409
review_item_closed``), then the action's own rules.  The update itself is a
compare-and-set on the revision, so two concurrent writes never both succeed.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import ColumnElement, Select, and_, false, or_, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    REVIEW_ITEM_ACTIVE_STATUSES,
    REVIEW_ITEM_STATUSES,
    Meeting,
    Membership,
    ReviewItem,
    ScorecardResultRecord,
    WorkspaceRole,
)
from backend.jobs.leases import as_utc
from backend.security.authorization import membership_effective_clause, supervised_team_ids
from backend.services.audit_event_types import AuditEventType
from backend.services.review_sampling_service import ReviewSample, draw_review_sample

DUE_AT_MAX_AHEAD = timedelta(days=90)
REVIEW_ADMIN_ROLES: frozenset[WorkspaceRole] = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})
ASSIGNEE_ME = "me"
ASSIGNEE_UNASSIGNED = "unassigned"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_BOOLEAN = TypeAdapter(bool)


class ReviewQueueError(Exception):
    """A refusal: the HTTP status, the error code and optional details."""

    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details


def _not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "review_item_not_found", "Review item not found")


def _revision_conflict(current_revision: int) -> ReviewQueueError:
    return ReviewQueueError(
        409,
        "review_item_revision_conflict",
        "The review item changed since it was read; reload and retry",
        {"current_revision": current_revision},
    )


def _exists(item_id: str) -> ReviewQueueError:
    return ReviewQueueError(
        409,
        "review_item_exists",
        "The conversation already has an active review item",
        {"review_item_id": item_id},
    )


@dataclass(frozen=True)
class Reviewer:
    """The caller's review authority in one workspace."""

    workspace_id: str
    user_id: str
    is_admin: bool
    supervised_team_ids: frozenset[str]

    def meeting_clause(self) -> ColumnElement[bool]:
        """The conversations whose items this caller sees (a query that joins ``Meeting``)."""
        if self.is_admin:
            return true()
        if not self.supervised_team_ids:
            return false()
        return and_(
            Meeting.team_id.in_(sorted(self.supervised_team_ids)),
            # A supervisor never sees the review of their own conversation.
            or_(Meeting.agent_user_id.is_(None), Meeting.agent_user_id != self.user_id),
        )


def reviewer(
    db: Session, *, workspace_id: str, user_id: str, role: WorkspaceRole, now: datetime
) -> Reviewer:
    is_admin = role in REVIEW_ADMIN_ROLES
    teams = (
        frozenset()
        if is_admin
        else supervised_team_ids(db, workspace_id=workspace_id, user_id=user_id, now=now)
    )
    return Reviewer(
        workspace_id=workspace_id, user_id=user_id, is_admin=is_admin, supervised_team_ids=teams
    )


@dataclass(frozen=True)
class ItemView:
    """An item with its conversation's current team."""

    item: ReviewItem
    team_id: str | None


@dataclass(frozen=True)
class Change:
    """A write's result; ``event`` is ``None`` when nothing changed (no audit event)."""

    view: ItemView
    event: AuditEventType | None
    details: dict[str, Any] = field(default_factory=dict)


def is_active(item: ReviewItem) -> bool:
    return item.status in REVIEW_ITEM_ACTIVE_STATUSES


def is_overdue(item: ReviewItem, now: datetime) -> bool:
    due_at = as_utc(item.due_at)
    return is_active(item) and due_at is not None and due_at <= now


def check_due_at(raw: object, *, now: datetime) -> datetime:
    """``due_at`` in UTC, or ``422 review_due_at_invalid``: an ISO 8601 string (a JSON
    number or any other value is refused) that carries a UTC offset, later than ``now``
    and at most 90 days after it."""
    try:
        if not isinstance(raw, str):
            raise ValueError
        due_at = datetime.fromisoformat(raw)
    except ValueError:
        raise ReviewQueueError(
            422, "review_due_at_invalid", "due_at must be an ISO 8601 string"
        ) from None
    if due_at.tzinfo is None or due_at.utcoffset() is None:
        message = "due_at must carry a UTC offset"
    elif due_at <= now:
        message = "due_at must be later than now"
    elif due_at > now + DUE_AT_MAX_AHEAD:
        message = "due_at must be at most 90 days ahead"
    else:
        return due_at.astimezone(UTC)
    raise ReviewQueueError(422, "review_due_at_invalid", message)


def active_item_id(db: Session, *, workspace_id: str, meeting_id: str) -> str | None:
    """The conversation's active item, the one ``uq_review_items_active_meeting`` allows."""
    return db.scalar(
        select(ReviewItem.id).where(
            ReviewItem.workspace_id == workspace_id,
            ReviewItem.meeting_id == meeting_id,
            ReviewItem.status.in_(REVIEW_ITEM_ACTIVE_STATUSES),
        )
    )


def _visible(caller: Reviewer, *columns: Any) -> Select[Any]:
    return (
        select(*columns)
        .join(
            Meeting,
            and_(Meeting.id == ReviewItem.meeting_id, Meeting.workspace_id == caller.workspace_id),
        )
        .where(
            ReviewItem.workspace_id == caller.workspace_id,
            Meeting.deleted_at.is_(None),
            caller.meeting_clause(),
        )
    )


def _visible_item(db: Session, caller: Reviewer, item_id: str) -> tuple[ReviewItem, Meeting]:
    row = db.execute(_visible(caller, ReviewItem, Meeting).where(ReviewItem.id == item_id)).first()
    if row is None:
        raise _not_found()
    return row[0], row[1]


def get_item(db: Session, caller: Reviewer, *, item_id: str) -> ItemView:
    item, meeting = _visible_item(db, caller, item_id)
    return ItemView(item, meeting.team_id)


def check_assignee(
    db: Session, *, workspace_id: str, meeting: Meeting, assignee_user_id: str, now: datetime
) -> None:
    if assignee_user_id == meeting.agent_user_id:
        raise ReviewQueueError(
            422,
            "review_assignee_is_agent",
            "Nobody reviews their own conversation",
        )
    role = db.scalar(
        select(Membership.role).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == assignee_user_id,
            membership_effective_clause(now),
        )
    )
    if role in REVIEW_ADMIN_ROLES:
        return
    # A member needs authority over the conversation; a viewer never reviews.
    if (
        role == WorkspaceRole.MEMBER
        and meeting.team_id is not None
        and meeting.team_id
        in supervised_team_ids(db, workspace_id=workspace_id, user_id=assignee_user_id, now=now)
    ):
        return
    raise ReviewQueueError(
        422,
        "review_assignee_ineligible",
        "The assignee needs review authority over the conversation",
    )


def _insert(db: Session, item: ReviewItem) -> bool:
    """Add the item in a savepoint; ``False`` when the active-item index refuses it."""
    savepoint = db.begin_nested()
    try:
        db.add(item)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    return True


def create_item(
    db: Session,
    caller: Reviewer,
    *,
    meeting_id: str,
    due_at: object,
    assignee_user_id: str | None,
    scorecard_result_id: str | None,
    now: datetime,
) -> Change:
    meeting = db.scalar(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.workspace_id == caller.workspace_id,
            Meeting.deleted_at.is_(None),
            caller.meeting_clause(),
        )
    )
    if meeting is None:
        raise ReviewQueueError(404, "meeting_not_found", "Meeting not found")
    due = check_due_at(due_at, now=now)
    if scorecard_result_id is not None and (
        db.scalar(
            select(ScorecardResultRecord.id).where(
                ScorecardResultRecord.id == scorecard_result_id,
                ScorecardResultRecord.workspace_id == caller.workspace_id,
                ScorecardResultRecord.meeting_id == meeting.id,
            )
        )
        is None
    ):
        raise ReviewQueueError(
            422,
            "review_scorecard_result_invalid",
            "The scorecard result is not a result of this conversation",
        )
    if assignee_user_id is not None:
        check_assignee(
            db,
            workspace_id=caller.workspace_id,
            meeting=meeting,
            assignee_user_id=assignee_user_id,
            now=now,
        )
    existing = active_item_id(db, workspace_id=caller.workspace_id, meeting_id=meeting.id)
    if existing is not None:
        raise _exists(existing)
    item = ReviewItem(
        workspace_id=caller.workspace_id,
        meeting_id=meeting.id,
        scorecard_result_id=scorecard_result_id,
        source="manual",
        source_details={},
        status="open",
        assignee_user_id=assignee_user_id,
        assigned_by_user_id=caller.user_id if assignee_user_id is not None else None,
        assigned_at=now if assignee_user_id is not None else None,
        due_at=due,
        created_by_user_id=caller.user_id,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    if not _insert(db, item):
        # A concurrent create committed between the check and the insert.
        existing = active_item_id(db, workspace_id=caller.workspace_id, meeting_id=meeting.id)
        if existing is None:
            raise RuntimeError("review item insert refused without an active item")
        raise _exists(existing)
    return Change(
        ItemView(item, meeting.team_id),
        AuditEventType.REVIEW_ITEM_CREATED,
        {
            "meeting_id": meeting.id,
            "source": item.source,
            "due_at": due.isoformat(),
            "assignee_user_id": assignee_user_id,
        },
    )


# --- the list ------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemFilters:
    statuses: tuple[str, ...] = REVIEW_ITEM_ACTIVE_STATUSES
    assignee: str | None = None  # ``me``, ``unassigned`` or a user id
    overdue: bool | None = None
    meeting_id: str | None = None


def _filter_invalid(message: str) -> ReviewQueueError:
    return ReviewQueueError(422, "review_filter_invalid", message)


def parse_filters(
    *,
    statuses: list[str] | None,
    assignee: str | None,
    overdue: str | None,
    meeting_id: str | None,
) -> ItemFilters:
    if statuses and any(status not in REVIEW_ITEM_STATUSES for status in statuses):
        raise _filter_invalid(f"status must be one of {', '.join(REVIEW_ITEM_STATUSES)}")
    if assignee is not None and assignee not in (ASSIGNEE_ME, ASSIGNEE_UNASSIGNED):
        if _UUID.fullmatch(assignee) is None:
            raise _filter_invalid("assignee must be 'me', 'unassigned' or a lowercase UUID")
    parsed_overdue = None
    if overdue is not None:
        try:
            parsed_overdue = _BOOLEAN.validate_python(overdue)
        except ValidationError:
            raise _filter_invalid("overdue must be true or false") from None
    if meeting_id is not None and _UUID.fullmatch(meeting_id) is None:
        raise _filter_invalid("meeting_id must be a lowercase UUID")
    return ItemFilters(
        statuses=tuple(dict.fromkeys(statuses)) if statuses else REVIEW_ITEM_ACTIVE_STATUSES,
        assignee=assignee,
        overdue=parsed_overdue,
        meeting_id=meeting_id,
    )


def _invalid_cursor() -> ReviewQueueError:
    return ReviewQueueError(400, "invalid_cursor", "Invalid review item pagination cursor")


def _encode_cursor(item: ReviewItem) -> str:
    due_at = as_utc(item.due_at)
    assert due_at is not None
    payload = {"v": 1, "due_at": due_at.isoformat(), "id": item.id}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    # Its own keys: a jobs cursor ("created_at") is refused here.
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "due_at", "id"}:
            raise ValueError
        if (
            payload["v"] != 1
            or not isinstance(payload["due_at"], str)
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        item_id = str(uuid.UUID(payload["id"]))
        due_at = datetime.fromisoformat(payload["due_at"])
        if due_at.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise _invalid_cursor() from None
    return due_at.astimezone(UTC), item_id


@dataclass(frozen=True)
class ItemPage:
    views: list[ItemView]
    next_cursor: str | None


def list_items(
    db: Session,
    caller: Reviewer,
    *,
    filters: ItemFilters,
    limit: int,
    cursor: str | None,
    now: datetime,
) -> ItemPage:
    """The visible items, ``due_at`` then ``id`` ascending, one keyset page."""
    query = _visible(caller, ReviewItem, Meeting.team_id).where(
        ReviewItem.status.in_(filters.statuses)
    )
    if filters.assignee == ASSIGNEE_ME:
        query = query.where(ReviewItem.assignee_user_id == caller.user_id)
    elif filters.assignee == ASSIGNEE_UNASSIGNED:
        query = query.where(ReviewItem.assignee_user_id.is_(None))
    elif filters.assignee is not None:
        query = query.where(ReviewItem.assignee_user_id == filters.assignee)
    overdue = and_(ReviewItem.status.in_(REVIEW_ITEM_ACTIVE_STATUSES), ReviewItem.due_at <= now)
    if filters.overdue is True:
        query = query.where(overdue)
    elif filters.overdue is False:
        query = query.where(~overdue)
    if filters.meeting_id is not None:
        query = query.where(ReviewItem.meeting_id == filters.meeting_id)
    if cursor is not None:
        due_at, item_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                ReviewItem.due_at > due_at,
                and_(ReviewItem.due_at == due_at, ReviewItem.id > item_id),
            )
        )
    rows = db.execute(
        query.order_by(ReviewItem.due_at.asc(), ReviewItem.id.asc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    return ItemPage(
        views=[ItemView(item, team_id) for item, team_id in page],
        next_cursor=_encode_cursor(page[-1][0]) if len(rows) > limit else None,
    )


# --- the actions ---------------------------------------------------------------------


def _writable(
    db: Session, caller: Reviewer, *, item_id: str, expected_revision: int
) -> tuple[ReviewItem, Meeting]:
    """The common checks of every write, in order: visible, the revision, active."""
    item, meeting = _visible_item(db, caller, item_id)
    if item.revision != expected_revision:
        raise _revision_conflict(item.revision)
    if not is_active(item):
        raise ReviewQueueError(409, "review_item_closed", "The review item is closed")
    return item, meeting


def compare_and_set(
    db: Session,
    item: ReviewItem,
    *,
    expected_revision: int,
    now: datetime,
    values: dict[str, Any],
) -> None:
    """Write ``values`` only while the stored revision is still ``expected_revision``;
    the revision grows by one.  A concurrent writer makes the update change no row."""
    changed = db.execute(
        update(ReviewItem)
        .where(
            ReviewItem.id == item.id,
            ReviewItem.workspace_id == item.workspace_id,
            ReviewItem.revision == expected_revision,
        )
        .values(**values, updated_at=now, revision=ReviewItem.revision + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        current = db.scalar(
            select(ReviewItem.revision).where(
                ReviewItem.id == item.id, ReviewItem.workspace_id == item.workspace_id
            )
        )
        if current is None:
            raise _not_found()
        raise _revision_conflict(current)
    db.refresh(item)


def assign_item(
    db: Session,
    caller: Reviewer,
    *,
    item_id: str,
    expected_revision: int,
    assignee_user_id: str | None,
    now: datetime,
) -> Change:
    item, meeting = _writable(db, caller, item_id=item_id, expected_revision=expected_revision)
    if assignee_user_id is not None:
        check_assignee(
            db,
            workspace_id=caller.workspace_id,
            meeting=meeting,
            assignee_user_id=assignee_user_id,
            now=now,
        )
    previous = item.assignee_user_id
    if assignee_user_id == previous:
        return Change(ItemView(item, meeting.team_id), None)
    assigned = assignee_user_id is not None
    values: dict[str, Any] = {
        "assignee_user_id": assignee_user_id,
        "assigned_by_user_id": caller.user_id if assigned else None,
        "assigned_at": now if assigned else None,
    }
    if item.status == "in_review":
        # A new assignee (or none) starts the review again.
        values.update(status="open", started_at=None)
    compare_and_set(db, item, expected_revision=expected_revision, now=now, values=values)
    return Change(
        ItemView(item, meeting.team_id),
        AuditEventType.REVIEW_ITEM_ASSIGNED,
        {
            "previous_assignee_user_id": previous,
            "assignee_user_id": assignee_user_id,
            "revision": item.revision,
        },
    )


def change_due_time(
    db: Session,
    caller: Reviewer,
    *,
    item_id: str,
    expected_revision: int,
    due_at: object,
    now: datetime,
) -> Change:
    item, meeting = _writable(db, caller, item_id=item_id, expected_revision=expected_revision)
    due = check_due_at(due_at, now=now)
    previous = as_utc(item.due_at)
    assert previous is not None
    compare_and_set(db, item, expected_revision=expected_revision, now=now, values={"due_at": due})
    return Change(
        ItemView(item, meeting.team_id),
        AuditEventType.REVIEW_ITEM_DUE_CHANGED,
        {
            "previous_due_at": previous.isoformat(),
            "due_at": due.isoformat(),
            "revision": item.revision,
        },
    )


def _assignee_action(
    db: Session,
    caller: Reviewer,
    *,
    item_id: str,
    expected_revision: int,
    needs_status: str,
    values: dict[str, Any],
    event: AuditEventType,
    now: datetime,
) -> Change:
    item, meeting = _writable(db, caller, item_id=item_id, expected_revision=expected_revision)
    if item.assignee_user_id != caller.user_id:
        raise ReviewQueueError(403, "review_not_assignee", "Only the assignee may do this")
    # The conversation's agent may have changed since the assignment.  This reaches an
    # admin only: a member who is the agent does not see the item.
    if meeting.agent_user_id == caller.user_id:
        raise ReviewQueueError(
            422, "review_assignee_is_agent", "Nobody reviews their own conversation"
        )
    if item.status != needs_status:
        raise ReviewQueueError(
            409, "review_item_state_invalid", f"The review item must be {needs_status}"
        )
    compare_and_set(db, item, expected_revision=expected_revision, now=now, values=values)
    return Change(ItemView(item, meeting.team_id), event, {"revision": item.revision})


def start_item(
    db: Session, caller: Reviewer, *, item_id: str, expected_revision: int, now: datetime
) -> Change:
    return _assignee_action(
        db,
        caller,
        item_id=item_id,
        expected_revision=expected_revision,
        needs_status="open",
        values={"status": "in_review", "started_at": now},
        event=AuditEventType.REVIEW_ITEM_STARTED,
        now=now,
    )


def complete_item(
    db: Session, caller: Reviewer, *, item_id: str, expected_revision: int, now: datetime
) -> Change:
    return _assignee_action(
        db,
        caller,
        item_id=item_id,
        expected_revision=expected_revision,
        needs_status="in_review",
        values={"status": "completed", "completed_at": now},
        event=AuditEventType.REVIEW_ITEM_COMPLETED,
        now=now,
    )


def cancel_item(
    db: Session,
    caller: Reviewer,
    *,
    item_id: str,
    expected_revision: int,
    reason: str,
    now: datetime,
) -> Change:
    item, meeting = _writable(db, caller, item_id=item_id, expected_revision=expected_revision)
    values = {"status": "cancelled", "cancelled_at": now, "cancel_reason": reason}
    compare_and_set(db, item, expected_revision=expected_revision, now=now, values=values)
    return Change(
        ItemView(item, meeting.team_id),
        AuditEventType.REVIEW_ITEM_CANCELLED,
        {"reason": reason, "revision": item.revision},
    )


# --- sample enqueue ------------------------------------------------------------------


@dataclass(frozen=True)
class SampleEnqueue:
    sample: ReviewSample
    created: list[ItemView]
    skipped_meeting_ids: list[str]
    audit_details: dict[str, Any]


def enqueue_sample(
    db: Session,
    caller: Reviewer,
    *,
    raw_from: object,
    raw_to: object,
    seed: object,
    config: object,
    due_at: object,
    recompute_max_attempts: int,
    now: datetime,
) -> SampleEnqueue:
    """Draw a seeded review sample (``draw_review_sample``, unchanged) and create one
    ``sample`` item per selection, in draw order.  A conversation that already has an
    active item is skipped.  Raises ``ReviewQueueError``, ``CoverageWindowError`` or
    ``SamplingError`` before anything is written."""
    due = check_due_at(due_at, now=now)
    sample = draw_review_sample(
        db,
        workspace_id=caller.workspace_id,
        raw_from=raw_from,
        raw_to=raw_to,
        seed=seed,
        config=config,
        recompute_max_attempts=recompute_max_attempts,
    )
    draw = sample.draw
    created: list[ReviewItem] = []
    skipped: list[str] = []
    for selection in draw.selections:
        meeting_id = selection["item_id"]
        if active_item_id(db, workspace_id=caller.workspace_id, meeting_id=meeting_id):
            skipped.append(meeting_id)
            continue
        item = ReviewItem(
            workspace_id=caller.workspace_id,
            meeting_id=meeting_id,
            source="sample",
            source_details={
                "sampler_version": draw.sampler_version,
                "seed": draw.seed,
                "config_digest": draw.config_digest,
                "selection_digest": draw.selection_digest,
                "reasons": list(selection["reasons"]),
            },
            status="open",
            due_at=due,
            created_by_user_id=caller.user_id,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        if _insert(db, item):
            created.append(item)
        else:  # a concurrent create took the conversation
            skipped.append(meeting_id)
    teams = dict(
        db.execute(
            select(Meeting.id, Meeting.team_id).where(
                Meeting.workspace_id == caller.workspace_id,
                Meeting.id.in_([item.meeting_id for item in created]),
            )
        ).all()
    )
    return SampleEnqueue(
        sample=sample,
        created=[ItemView(item, teams.get(item.meeting_id)) for item in created],
        skipped_meeting_ids=skipped,
        audit_details={
            **sample.audit_details(),
            "created_count": len(created),
            "skipped_count": len(skipped),
            "due_at": due.isoformat(),
        },
    )
