"""Coaching tasks from accepted findings (HOM-77, WP14-T06, F47, ADR 0062).

A supervisor turns one **accepted finding** into one task: one criterion of a completed
review item that has a human result, whose **final outcome** is ``failed``.  The final
outcome is the appeal result's outcome when the item has one, else the human result's
(ADR 0057 D4); it is read from the stored rows, never scored again.  The coachee is the
call's agent when the task is created and never changes; the owner is the coach.

Who sees what, for caller ``U`` in workspace ``W``:

* **Supervisor view** (the role holds ``review:read``): the HOM-73 record rule of the
  task's call (``Reviewer.meeting_clause``: an owner or admin sees every call; a member
  sees the calls of the teams they supervise, never their own), minus the tasks where
  ``U`` is the coachee.  An owner or admin who is the call's current agent still sees
  the task here, but never writes it (``422 coaching_actor_is_agent``).
* **Agent view**: the tasks whose coachee is ``U`` (``agent_user_id``), whatever the
  call's current agent is.  It never holds the creator, the closer, the canceller, the
  cancel note or any reviewer of the review item.
* Anything else is ``404 coaching_task_not_found``, like a missing id or an id of another
  workspace.  A deleted call hides its tasks.

Every write reads the task and its meeting again, checks in the ADR 0062 D3 order, and
updates the task with a compare-and-set on ``revision`` and the status, so two
concurrent writes never both land.  A refusal raises before anything is written.  Free
text is stripped with ``str.strip()`` (tabs and newlines too) and stored stripped.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Select, and_, false, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    COACHING_CANCEL_REASONS,
    COACHING_FOLLOW_UP_OUTCOMES,
    COACHING_MAX_DUE_DAYS,
    COACHING_TASK_ACTIVE_STATUSES,
    COACHING_TASK_STATUSES,
    REVIEW_NOTE_MAX_LENGTH,
    AuditEvent,
    CoachingTask,
    CriterionResultRecord,
    Meeting,
    ReviewAppeal,
    ReviewItem,
    new_id,
)
from backend.jobs.leases import as_utc
from backend.services import notification_service
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.review_appeal_service import (
    _appeal_of_item,
    _appeal_result,
    _human,
    human_outcomes,
)
from backend.services.review_queue_service import (
    _DATETIME,
    _NUMBER,
    Reviewer,
    ReviewQueueError,
    _visible_item,
    check_assignee,
)

__all__ = ["ReviewQueueError"]

SUPERVISOR = "supervisor"
AGENT = "agent"
TARGET_TYPE = "coaching_task"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# --- errors -------------------------------------------------------------------------------


def _not_found() -> ReviewQueueError:
    return ReviewQueueError(404, "coaching_task_not_found", "Coaching task not found")


def _revision_conflict(current_revision: int) -> ReviewQueueError:
    return ReviewQueueError(
        409,
        "coaching_task_revision_conflict",
        "The coaching task changed since it was read; reload and retry",
        {"current_revision": current_revision},
    )


def _closed() -> ReviewQueueError:
    return ReviewQueueError(409, "coaching_task_closed", "The coaching task is closed")


def _state_invalid() -> ReviewQueueError:
    return ReviewQueueError(
        409, "coaching_task_state_invalid", "Only an assigned task can be acknowledged"
    )


def _not_acknowledged() -> ReviewQueueError:
    return ReviewQueueError(
        409, "coaching_task_not_acknowledged", "The follow-up review needs an acknowledged task"
    )


def _actor_is_agent() -> ReviewQueueError:
    return ReviewQueueError(
        422, "coaching_actor_is_agent", "Nobody coaches or reviews their own call"
    )


def _owner_is_agent() -> ReviewQueueError:
    return ReviewQueueError(
        422, "coaching_owner_is_agent", "The owner is never the call's agent or the coachee"
    )


def _ineligible() -> ReviewQueueError:
    return ReviewQueueError(
        422,
        "review_assignee_ineligible",
        "The assignee needs review authority over the conversation",
    )


def _task_exists() -> ReviewQueueError:
    return ReviewQueueError(
        409, "coaching_task_exists", "The finding already has an active coaching task"
    )


def _invalid_cursor() -> ReviewQueueError:
    return ReviewQueueError(400, "invalid_cursor", "Invalid coaching task pagination cursor")


def _filter_invalid(message: str) -> ReviewQueueError:
    return ReviewQueueError(422, "coaching_filter_invalid", message)


# --- the caller ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoachingCaller:
    """The caller's review authority (HOM-73) and whether their role reads the queue."""

    reviewer: Reviewer
    supervises: bool  # the role holds ``review:read``

    @property
    def workspace_id(self) -> str:
        return self.reviewer.workspace_id

    @property
    def user_id(self) -> str:
        return self.reviewer.user_id

    @property
    def is_workspace_admin(self) -> bool:
        """A workspace owner or admin (the cancel rule of ADR 0062 D3 route 7)."""
        return self.reviewer.is_admin


# --- text ---------------------------------------------------------------------------------


def _stored_text(value: Any) -> str | None:
    """Text as stored: ``str.strip()``-ed (tabs and newlines too), 1 to 2000 characters,
    no NUL, encodable; else ``None``."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > REVIEW_NOTE_MAX_LENGTH or "\x00" in stripped:
        return None
    try:
        stripped.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return stripped


def _optional_text(value: Any, error: ReviewQueueError) -> str | None:
    """An optional note: missing or blank is no note; otherwise it must be valid text."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise error
    if not value.strip():
        return None
    stored = _stored_text(value)
    if stored is None:
        raise error
    return stored


# --- the finding --------------------------------------------------------------------------


def final_outcomes(db: Session, item: ReviewItem) -> tuple[dict[str, str], str]:
    """Each criterion's final outcome and its source (``appeal_result`` or
    ``human_result``), from the stored rows (HOM-76 helpers; nothing is scored)."""
    appeal = _appeal_of_item(db, item.id)
    appeal_result = _appeal_result(db, appeal.id) if appeal is not None else None
    if appeal_result is not None:
        # The appeal result's ``result`` has the human result's ``criteria`` list.
        return human_outcomes(cast(Any, appeal_result)), "appeal_result"
    human = _human(db, item.id)
    if human is None:  # the caller checked it; this guards a broken row
        return {}, "human_result"
    return human_outcomes(human), "human_result"


def _active_task_id(
    db: Session, *, workspace_id: str, item_id: str, criterion_id: str
) -> str | None:
    return db.scalar(
        select(CoachingTask.id).where(
            CoachingTask.workspace_id == workspace_id,
            CoachingTask.review_item_id == item_id,
            CoachingTask.criterion_id == criterion_id,
            CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
        )
    )


def _criterion_ids(db: Session, *, workspace_id: str, result_id: str) -> set[str]:
    return set(
        db.scalars(
            select(CriterionResultRecord.criterion_id).where(
                CriterionResultRecord.result_id == result_id,
                CriterionResultRecord.workspace_id == workspace_id,
            )
        )
    )


def _due_at(raw: object, *, now: datetime) -> datetime:
    """``due_at`` in UTC, or ``422 coaching_due_invalid``: HOM-73's parsing rules (an
    ISO date-time string with a UTC offset, later than ``now``) with this domain's own
    bound ``COACHING_MAX_DUE_DAYS`` (ADR 0062 D1), not HOM-73's review bound."""
    try:
        if not isinstance(raw, str) or _NUMBER.fullmatch(raw):
            raise ValueError
        due_at = _DATETIME.validate_python(raw)
    except (ValueError, ValidationError):
        raise ReviewQueueError(
            422, "coaching_due_invalid", "due_at must be an ISO 8601 date-time string"
        ) from None
    if due_at.tzinfo is None or due_at.utcoffset() is None:
        message = "due_at must carry a UTC offset"
    elif due_at <= now:
        message = "due_at must be later than now"
    elif due_at > now + timedelta(days=COACHING_MAX_DUE_DAYS):
        message = f"due_at must be at most {COACHING_MAX_DUE_DAYS} days ahead"
    else:
        return due_at.astimezone(UTC)
    raise ReviewQueueError(422, "coaching_due_invalid", message)


# --- visibility ---------------------------------------------------------------------------


def _tasks(caller: CoachingCaller) -> Select[Any]:
    return (
        select(CoachingTask, Meeting)
        .join(
            Meeting,
            and_(
                Meeting.id == CoachingTask.meeting_id,
                Meeting.workspace_id == caller.workspace_id,
            ),
        )
        .where(CoachingTask.workspace_id == caller.workspace_id, Meeting.deleted_at.is_(None))
    )


def _supervisor_clause(caller: CoachingCaller) -> Any:
    if not caller.supervises:
        return false()
    return and_(
        caller.reviewer.meeting_clause(),
        # The coachee never sees the task through the supervisor routes.
        or_(CoachingTask.agent_user_id.is_(None), CoachingTask.agent_user_id != caller.user_id),
    )


def _visible(
    db: Session, caller: CoachingCaller, task_id: str
) -> tuple[CoachingTask, Meeting, str]:
    """The task and the caller's view of it, or ``404 coaching_task_not_found``."""
    base = _tasks(caller).where(CoachingTask.id == task_id)
    row = db.execute(base.where(_supervisor_clause(caller))).first()
    if row is not None:
        return row[0], row[1], SUPERVISOR
    row = db.execute(base.where(CoachingTask.agent_user_id == caller.user_id)).first()
    if row is not None:
        return row[0], row[1], AGENT
    raise _not_found()


def _supervised(db: Session, caller: CoachingCaller, task_id: str) -> tuple[CoachingTask, Meeting]:
    task, meeting, view = _visible(db, caller, task_id)
    if view != SUPERVISOR:
        raise _not_found()
    return task, meeting


def get_task(
    db: Session, caller: CoachingCaller, *, task_id: str
) -> tuple[CoachingTask, Meeting, str]:
    return _visible(db, caller, task_id)


def _is_party(task: CoachingTask, meeting: Meeting, user_id: Any) -> bool:
    """The call's current agent or the task's coachee."""
    return user_id is not None and user_id in (meeting.agent_user_id, task.agent_user_id)


# --- the view -----------------------------------------------------------------------------


def is_overdue(task: CoachingTask, now: datetime) -> bool:
    due_at = as_utc(task.due_at)
    return task.status in COACHING_TASK_ACTIVE_STATUSES and due_at is not None and now >= due_at


def task_view(task: CoachingTask, meeting: Meeting, view: str, *, now: datetime) -> dict[str, Any]:
    """The supervisor view, or the agent view without the creator, the closer, the
    canceller, the cancel note, the team and the sweep marks."""
    supervisor = view == SUPERVISOR
    return {
        "view": view,
        "id": task.id,
        "review_item_id": task.review_item_id,
        "meeting_id": task.meeting_id,
        "team_id": meeting.team_id if supervisor else None,
        "scorecard_result_id": task.scorecard_result_id,
        "criterion_id": task.criterion_id,
        "finding_source": task.finding_source,
        "agent_user_id": task.agent_user_id,
        "owner_user_id": task.owner_user_id,
        "created_by_user_id": task.created_by_user_id if supervisor else None,
        "instruction": task.instruction,
        "due_at": as_utc(task.due_at),
        "overdue": is_overdue(task, now),
        "status": task.status,
        "acknowledged_at": as_utc(task.acknowledged_at),
        "acknowledgement_note": task.acknowledgement_note,
        "follow_up_outcome": task.follow_up_outcome,
        "follow_up_note": task.follow_up_note,
        "closed_at": as_utc(task.closed_at),
        "closed_by_user_id": task.closed_by_user_id if supervisor else None,
        "cancel_reason": task.cancel_reason,
        "cancel_note": task.cancel_note if supervisor else None,
        "cancelled_at": as_utc(task.cancelled_at),
        "cancelled_by_user_id": task.cancelled_by_user_id if supervisor else None,
        "reminder_notified_at": as_utc(task.reminder_notified_at) if supervisor else None,
        "overdue_notified_at": as_utc(task.overdue_notified_at) if supervisor else None,
        "revision": task.revision,
        "created_at": as_utc(task.created_at),
        "updated_at": as_utc(task.updated_at),
    }


# --- keyset cursors -----------------------------------------------------------------------


def _encode_cursor(key: str, when: datetime | None, row_id: str) -> str:
    moment = as_utc(when)
    assert moment is not None
    payload = {"v": 1, key: moment.isoformat(), "id": row_id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str, key: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode("utf-8")
        )
        if not isinstance(payload, dict) or set(payload) != {"v", key, "id"}:
            raise ValueError
        if payload["v"] != 1 or not isinstance(payload[key], str):
            raise ValueError
        row_id = str(uuid.UUID(str(payload["id"])))
        moment = datetime.fromisoformat(payload[key])
        if moment.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise _invalid_cursor() from None
    return moment.astimezone(UTC), row_id


# --- the lists ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskFilters:
    statuses: tuple[str, ...] = COACHING_TASK_STATUSES
    owner_user_id: str | None = None
    agent_user_id: str | None = None
    overdue: bool | None = None


def parse_statuses(statuses: list[str] | None) -> tuple[str, ...]:
    """Every status by default (leader ruling 3: closed tasks show their follow-up)."""
    if not statuses:
        return COACHING_TASK_STATUSES
    if any(status not in COACHING_TASK_STATUSES for status in statuses):
        raise _filter_invalid(f"status must be one of {', '.join(COACHING_TASK_STATUSES)}")
    return tuple(dict.fromkeys(statuses))


def parse_filters(
    *,
    statuses: list[str] | None,
    owner_user_id: str | None,
    agent_user_id: str | None,
    overdue: str | None,
) -> TaskFilters:
    for name, value in (("owner_user_id", owner_user_id), ("agent_user_id", agent_user_id)):
        if value is not None and _UUID.fullmatch(value) is None:
            raise _filter_invalid(f"{name} must be a lowercase UUID")
    parsed_overdue: bool | None = None
    if overdue is not None:
        lowered = overdue.strip().lower()
        if lowered in ("true", "1"):
            parsed_overdue = True
        elif lowered in ("false", "0"):
            parsed_overdue = False
        else:
            raise _filter_invalid("overdue must be true or false")
    return TaskFilters(
        statuses=parse_statuses(statuses),
        owner_user_id=owner_user_id,
        agent_user_id=agent_user_id,
        overdue=parsed_overdue,
    )


def list_tasks(
    db: Session,
    caller: CoachingCaller,
    *,
    filters: TaskFilters,
    limit: int,
    cursor: str | None,
    now: datetime,
) -> tuple[list[tuple[CoachingTask, Meeting]], str | None]:
    """The supervisor-visible tasks, ``due_at`` then ``id`` ascending, one keyset page."""
    query = _tasks(caller).where(
        _supervisor_clause(caller), CoachingTask.status.in_(filters.statuses)
    )
    if filters.owner_user_id is not None:
        query = query.where(CoachingTask.owner_user_id == filters.owner_user_id)
    if filters.agent_user_id is not None:
        query = query.where(CoachingTask.agent_user_id == filters.agent_user_id)
    overdue = and_(
        CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES), CoachingTask.due_at <= now
    )
    if filters.overdue is True:
        query = query.where(overdue)
    elif filters.overdue is False:
        query = query.where(~overdue)
    if cursor is not None:
        moment, task_id = _decode_cursor(cursor, "due_at")
        query = query.where(
            or_(
                CoachingTask.due_at > moment,
                and_(CoachingTask.due_at == moment, CoachingTask.id > task_id),
            )
        )
    rows = db.execute(
        query.order_by(CoachingTask.due_at.asc(), CoachingTask.id.asc()).limit(limit + 1)
    ).all()
    page = [(task, meeting) for task, meeting in rows[:limit]]
    next_cursor = (
        _encode_cursor("due_at", page[-1][0].due_at, page[-1][0].id) if len(rows) > limit else None
    )
    return page, next_cursor


def list_my_tasks(
    db: Session,
    caller: CoachingCaller,
    *,
    statuses: tuple[str, ...],
    limit: int,
    cursor: str | None,
) -> tuple[list[tuple[CoachingTask, Meeting]], str | None]:
    """The caller's tasks as coachee, newest ``created_at`` then ``id`` first."""
    query = _tasks(caller).where(
        CoachingTask.agent_user_id == caller.user_id, CoachingTask.status.in_(statuses)
    )
    if cursor is not None:
        moment, task_id = _decode_cursor(cursor, "created_at")
        query = query.where(
            or_(
                CoachingTask.created_at < moment,
                and_(CoachingTask.created_at == moment, CoachingTask.id < task_id),
            )
        )
    rows = db.execute(
        query.order_by(CoachingTask.created_at.desc(), CoachingTask.id.desc()).limit(limit + 1)
    ).all()
    page = [(task, meeting) for task, meeting in rows[:limit]]
    next_cursor = (
        _encode_cursor("created_at", page[-1][0].created_at, page[-1][0].id)
        if len(rows) > limit
        else None
    )
    return page, next_cursor


# --- the writes ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A write's result: the task and its view; ``event`` is ``None`` for a no-op."""

    task: CoachingTask
    meeting: Meeting
    view: str
    event: AuditEventType | None
    details: dict[str, Any] = field(default_factory=dict)


def _insert(db: Session, task: CoachingTask) -> bool:
    """Add the task in a savepoint; ``False`` when ``uq_coaching_tasks_active_finding``
    refuses it (another create of the same finding won the race)."""
    savepoint = db.begin_nested()
    try:
        db.add(task)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    return True


@dataclass(frozen=True)
class CreateChecks:
    """What a new task from one finding would get, once every create check passed."""

    item: ReviewItem
    meeting: Meeting
    criterion_id: str
    finding_source: str
    owner_user_id: str
    due_at: datetime
    instruction: str


def check_create(
    db: Session, caller: CoachingCaller, *, item_id: str, body: dict[str, Any], now: datetime
) -> CreateChecks:
    """The checks of ADR 0062 D3 route 1, in order, without writing anything.

    ``create_task`` runs them before its insert; an action proposal of type
    ``coaching_task`` (HOM-135, ADR 0082) runs the same checks with the same codes."""
    item, meeting = _visible_item(db, caller.reviewer, item_id)  # 404 review_item_not_found
    if meeting.agent_user_id is not None and caller.user_id == meeting.agent_user_id:
        raise _actor_is_agent()
    human = _human(db, item.id) if item.status == "completed" else None
    if human is None or item.scorecard_result_id is None:
        raise ReviewQueueError(
            409, "review_item_not_coachable", "Only a completed review with a human result"
        )
    appeal = _appeal_of_item(db, item.id)
    if appeal is not None and appeal.status == "open":
        raise ReviewQueueError(409, "review_appeal_open", "The review has an open appeal")
    if meeting.agent_user_id is None:
        raise ReviewQueueError(409, "coaching_no_agent", "The call has no agent to coach")
    criterion_id = body.get("criterion_id")
    known = _criterion_ids(db, workspace_id=caller.workspace_id, result_id=item.scorecard_result_id)
    if not isinstance(criterion_id, str) or criterion_id not in known:
        raise ReviewQueueError(404, "criterion_not_found", "Not a criterion of the reviewed result")
    outcomes, source = final_outcomes(db, item)
    if outcomes.get(criterion_id) != "failed":
        raise ReviewQueueError(
            422, "coaching_finding_not_failed", "Only a criterion whose final outcome is failed"
        )
    if _active_task_id(
        db, workspace_id=caller.workspace_id, item_id=item.id, criterion_id=criterion_id
    ):
        raise _task_exists()
    owner_user_id = body.get("owner_user_id")
    if owner_user_id is not None and owner_user_id == meeting.agent_user_id:
        raise _owner_is_agent()
    if not isinstance(owner_user_id, str) or not owner_user_id:
        raise _ineligible()
    check_assignee(
        db,
        workspace_id=caller.workspace_id,
        meeting=meeting,
        assignee_user_id=owner_user_id,
        now=now,
    )
    due_at = _due_at(body.get("due_at"), now=now)
    instruction = _stored_text(body.get("instruction"))
    if instruction is None:
        raise ReviewQueueError(
            422,
            "coaching_instruction_required",
            f"instruction must be 1 to {REVIEW_NOTE_MAX_LENGTH} characters of text",
        )
    return CreateChecks(
        item=item,
        meeting=meeting,
        criterion_id=criterion_id,
        finding_source=source,
        owner_user_id=owner_user_id,
        due_at=due_at,
        instruction=instruction,
    )


def create_task(
    db: Session, caller: CoachingCaller, *, item_id: str, body: dict[str, Any], now: datetime
) -> Outcome:
    """The checks of ADR 0062 D3 route 1, in order; a refusal writes nothing."""
    checked = check_create(db, caller, item_id=item_id, body=body, now=now)
    item, meeting = checked.item, checked.meeting
    criterion_id, source = checked.criterion_id, checked.finding_source
    owner_user_id, due_at = checked.owner_user_id, checked.due_at

    task = CoachingTask(
        id=new_id(),
        workspace_id=item.workspace_id,
        meeting_id=item.meeting_id,
        review_item_id=item.id,
        scorecard_result_id=item.scorecard_result_id,
        criterion_id=criterion_id,
        finding_source=source,
        agent_user_id=meeting.agent_user_id,
        owner_user_id=owner_user_id,
        created_by_user_id=caller.user_id,
        instruction=checked.instruction,
        due_at=due_at,
        status="assigned",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    if not _insert(db, task):
        raise _task_exists()
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_ASSIGNED,
        user_ids=[task.agent_user_id],
        now=now,
    )
    if owner_user_id != caller.user_id:
        notification_service.notify_coaching_task(
            db,
            task=task,
            kind=notification_service.COACHING_OWNER_ASSIGNED,
            user_ids=[owner_user_id],
            now=now,
        )
    return Outcome(
        task,
        meeting,
        SUPERVISOR,
        AuditEventType.COACHING_TASK_CREATED,
        {
            "review_item_id": item.id,
            "scorecard_result_id": task.scorecard_result_id,
            "criterion_id": criterion_id,
            "finding_source": source,
            "agent_user_id": task.agent_user_id,
            "owner_user_id": owner_user_id,
            "due_at": due_at.isoformat(),
            "revision": task.revision,
        },
    )


def _check_revision(task: CoachingTask, expected_revision: int) -> None:
    if task.revision != expected_revision:
        raise _revision_conflict(task.revision)


def _check_active(task: CoachingTask) -> None:
    if task.status not in COACHING_TASK_ACTIVE_STATUSES:
        raise _closed()


def _compare_and_set(
    db: Session,
    task: CoachingTask,
    *,
    expected_revision: int,
    statuses: tuple[str, ...],
    state_error: ReviewQueueError,
    now: datetime,
    values: dict[str, Any],
) -> None:
    """Write ``values`` only while the task is still at ``expected_revision`` in one of
    ``statuses``; the revision grows by one.  A concurrent writer makes the update change
    no row: a moved revision answers the revision conflict, else ``state_error``."""
    changed = db.execute(
        update(CoachingTask)
        .where(
            CoachingTask.id == task.id,
            CoachingTask.workspace_id == task.workspace_id,
            CoachingTask.revision == expected_revision,
            CoachingTask.status.in_(statuses),
        )
        .values(**values, updated_at=now, revision=CoachingTask.revision + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        current = db.scalar(
            select(CoachingTask.revision).where(
                CoachingTask.id == task.id, CoachingTask.workspace_id == task.workspace_id
            )
        )
        if current is None:
            raise _not_found()
        if current != expected_revision:
            raise _revision_conflict(current)
        raise state_error
    db.refresh(task)


def reassign_task(
    db: Session,
    caller: CoachingCaller,
    *,
    task_id: str,
    expected_revision: int,
    owner_user_id: Any,
    now: datetime,
) -> Outcome:
    """Visible (404), the caller not a party (422), the revision (409), active (409), the
    new owner not a party (422), then HOM-73's ``check_assignee``.  The same owner again
    writes nothing."""
    task, meeting = _supervised(db, caller, task_id)
    if _is_party(task, meeting, caller.user_id):
        raise _actor_is_agent()
    _check_revision(task, expected_revision)
    _check_active(task)
    if _is_party(task, meeting, owner_user_id):
        raise _owner_is_agent()
    if not isinstance(owner_user_id, str) or not owner_user_id:
        raise _ineligible()
    check_assignee(
        db,
        workspace_id=caller.workspace_id,
        meeting=meeting,
        assignee_user_id=owner_user_id,
        now=now,
    )
    previous = task.owner_user_id
    if owner_user_id == previous:
        return Outcome(task, meeting, SUPERVISOR, None)
    _compare_and_set(
        db,
        task,
        expected_revision=expected_revision,
        statuses=COACHING_TASK_ACTIVE_STATUSES,
        state_error=_closed(),
        now=now,
        values={"owner_user_id": owner_user_id},
    )
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_OWNER_ASSIGNED,
        user_ids=[owner_user_id],
        now=now,
    )
    return Outcome(
        task,
        meeting,
        SUPERVISOR,
        AuditEventType.COACHING_TASK_REASSIGNED,
        {
            "owner_user_id_before": previous,
            "owner_user_id_after": owner_user_id,
            "revision": task.revision,
        },
    )


def acknowledge_task(
    db: Session,
    caller: CoachingCaller,
    *,
    task_id: str,
    expected_revision: int,
    note: Any,
    now: datetime,
) -> Outcome:
    """Visible as coachee or supervisor (404), the coachee (403), the revision (409),
    ``assigned`` (409), then the optional note (422)."""
    task, meeting, view = _visible(db, caller, task_id)
    if task.agent_user_id is None or task.agent_user_id != caller.user_id:
        raise ReviewQueueError(403, "coaching_not_agent", "Only the coachee acknowledges")
    _check_revision(task, expected_revision)
    if task.status != "assigned":
        raise _state_invalid()
    stored_note = _optional_text(
        note,
        ReviewQueueError(
            422,
            "coaching_note_invalid",
            f"note must be at most {REVIEW_NOTE_MAX_LENGTH} characters of text",
        ),
    )
    _compare_and_set(
        db,
        task,
        expected_revision=expected_revision,
        statuses=("assigned",),
        state_error=_state_invalid(),
        now=now,
        values={
            "status": "acknowledged",
            "acknowledged_at": now,
            "acknowledgement_note": stored_note,
        },
    )
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_ACKNOWLEDGED,
        user_ids=[task.owner_user_id],
        now=now,
    )
    return Outcome(
        task,
        meeting,
        view,
        AuditEventType.COACHING_TASK_ACKNOWLEDGED,
        {"revision": task.revision},
    )


def close_task(
    db: Session,
    caller: CoachingCaller,
    *,
    task_id: str,
    expected_revision: int,
    body: dict[str, Any],
    now: datetime,
) -> Outcome:
    """The follow-up review.  Visible (404), the revision (409), active (409), the owner
    (403), not a party (422), ``acknowledged`` (409), ``check_assignee`` for the caller,
    then the outcome and the note (422)."""
    task, meeting = _supervised(db, caller, task_id)
    _check_revision(task, expected_revision)
    _check_active(task)
    if task.owner_user_id is None or task.owner_user_id != caller.user_id:
        raise ReviewQueueError(403, "coaching_not_owner", "Only the owner closes the task")
    if _is_party(task, meeting, caller.user_id):
        raise _actor_is_agent()
    if task.status != "acknowledged":
        raise _not_acknowledged()
    check_assignee(
        db,
        workspace_id=caller.workspace_id,
        meeting=meeting,
        assignee_user_id=caller.user_id,
        now=now,
    )
    outcome = body.get("follow_up_outcome")
    note = _stored_text(body.get("follow_up_note"))
    if not isinstance(outcome, str) or outcome not in COACHING_FOLLOW_UP_OUTCOMES or note is None:
        raise ReviewQueueError(
            422,
            "coaching_follow_up_invalid",
            "The follow-up review needs an outcome of the list and a note of 1 to "
            f"{REVIEW_NOTE_MAX_LENGTH} characters",
        )
    _compare_and_set(
        db,
        task,
        expected_revision=expected_revision,
        statuses=("acknowledged",),
        state_error=_closed(),
        now=now,
        values={
            "status": "closed",
            "closed_at": now,
            "closed_by_user_id": caller.user_id,
            "follow_up_outcome": outcome,
            "follow_up_note": note,
        },
    )
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_CLOSED,
        user_ids=[task.agent_user_id],
        now=now,
    )
    return Outcome(
        task,
        meeting,
        SUPERVISOR,
        AuditEventType.COACHING_TASK_CLOSED,
        {"follow_up_outcome": outcome, "revision": task.revision},
    )


def _cancel_recipients(task: CoachingTask, *, cancelled_by: str) -> list[str | None]:
    """The coachee, and the owner when the owner did not cancel (ADR 0062 D6)."""
    recipients: list[str | None] = [task.agent_user_id]
    if task.owner_user_id != cancelled_by:
        recipients.append(task.owner_user_id)
    return recipients


def cancel_task(
    db: Session,
    caller: CoachingCaller,
    *,
    task_id: str,
    expected_revision: int,
    body: dict[str, Any],
    now: datetime,
) -> Outcome:
    """Visible (404), the revision (409), active (409), the owner or a workspace owner or
    admin (403), not a party (422), then the reason and the note (422)."""
    task, meeting = _supervised(db, caller, task_id)
    _check_revision(task, expected_revision)
    _check_active(task)
    if task.owner_user_id != caller.user_id and not caller.is_workspace_admin:
        raise ReviewQueueError(
            403, "coaching_not_owner", "Only the owner or a workspace admin cancels the task"
        )
    if _is_party(task, meeting, caller.user_id):
        raise _actor_is_agent()
    reason = body.get("cancel_reason")
    invalid = ReviewQueueError(
        422,
        "coaching_cancel_invalid",
        "cancel_reason must be one of the list and cancel_note at most "
        f"{REVIEW_NOTE_MAX_LENGTH} characters",
    )
    if not isinstance(reason, str) or reason not in COACHING_CANCEL_REASONS:
        raise invalid
    note = _optional_text(body.get("cancel_note"), invalid)
    _compare_and_set(
        db,
        task,
        expected_revision=expected_revision,
        statuses=COACHING_TASK_ACTIVE_STATUSES,
        state_error=_closed(),
        now=now,
        values={
            "status": "cancelled",
            "cancel_reason": reason,
            "cancel_note": note,
            "cancelled_at": now,
            "cancelled_by_user_id": caller.user_id,
        },
    )
    notification_service.notify_coaching_task(
        db,
        task=task,
        kind=notification_service.COACHING_CANCELLED,
        user_ids=_cancel_recipients(task, cancelled_by=caller.user_id),
        now=now,
    )
    return Outcome(
        task,
        meeting,
        SUPERVISOR,
        AuditEventType.COACHING_TASK_CANCELLED,
        {"cancel_reason": reason, "revision": task.revision},
    )


# --- the appeal hook (ADR 0062 D3 "Appeals and coaching") -------------------------------


def _overturned(changes: dict[str, str | None]) -> list[str]:
    """The criteria the decision changes to an outcome other than ``failed``."""
    return sorted(criterion_id for criterion_id, outcome in changes.items() if outcome != "failed")


def check_decider_not_coachee(
    db: Session, *, appeal: ReviewAppeal, changes: dict[str, str | None], decider_user_id: str
) -> None:
    """The decide route's pre-check (D2: nobody acts on a task as a supervisor when they
    are its coachee).  A decider who is the coachee of an active task that this decision
    would cancel is a party (``422 appeal_decider_is_party``, HOM-76's code) and the
    whole decision is refused before anything is written.  HOM-76's own party check
    cannot catch this: it reads the call's *current* agent, and a former agent is still
    a task's coachee."""
    overturned = _overturned(changes)
    if not overturned:
        return
    clash = db.scalar(
        select(CoachingTask.id).where(
            CoachingTask.workspace_id == appeal.workspace_id,
            CoachingTask.review_item_id == appeal.review_item_id,
            CoachingTask.criterion_id.in_(overturned),
            CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
            CoachingTask.agent_user_id == decider_user_id,
        )
    )
    if clash is not None:
        raise ReviewQueueError(
            422,
            "appeal_decider_is_party",
            "The decider is the coachee of a coaching task this decision would cancel",
        )


def cancel_overturned(
    db: Session,
    *,
    appeal: ReviewAppeal,
    changes: dict[str, str | None],
    decider_user_id: str,
    now: datetime,
) -> list[str]:
    """Cancel each active task of the appeal's item whose criterion the decision changed
    to an outcome other than ``failed`` (``finding_overturned``, by the decider), in the
    decide transaction.  Writes each task's audit event and notifications; returns the
    cancelled task ids."""
    overturned = _overturned(changes)
    if not overturned:
        return []
    tasks = list(
        db.scalars(
            select(CoachingTask)
            .where(
                CoachingTask.workspace_id == appeal.workspace_id,
                CoachingTask.review_item_id == appeal.review_item_id,
                CoachingTask.criterion_id.in_(overturned),
                CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
            )
            .order_by(CoachingTask.criterion_id, CoachingTask.id)
        )
    )
    cancelled: list[str] = []
    for task in tasks:
        # Not a user's write: no expected revision, only "still active".
        changed = db.execute(
            update(CoachingTask)
            .where(
                CoachingTask.id == task.id,
                CoachingTask.workspace_id == task.workspace_id,
                CoachingTask.status.in_(COACHING_TASK_ACTIVE_STATUSES),
            )
            .values(
                status="cancelled",
                cancel_reason="finding_overturned",
                cancelled_at=now,
                cancelled_by_user_id=decider_user_id,
                updated_at=now,
                revision=CoachingTask.revision + 1,
            )
            .execution_options(synchronize_session=False)
        ).rowcount
        if changed != 1:
            continue
        db.refresh(task)
        record_audit_event(
            db,
            event_type=AuditEventType.COACHING_TASK_CANCELLED,
            actor_user_id=decider_user_id,
            workspace_id=task.workspace_id,
            target_type=TARGET_TYPE,
            target_id=task.id,
            details={"cancel_reason": "finding_overturned", "revision": task.revision},
        )
        notification_service.notify_coaching_task(
            db,
            task=task,
            kind=notification_service.COACHING_CANCELLED,
            user_ids=_cancel_recipients(task, cancelled_by=decider_user_id),
            now=now,
        )
        cancelled.append(task.id)
    return cancelled


# --- the history --------------------------------------------------------------------------


def _actor_role(task: CoachingTask, actor_user_id: str | None) -> str:
    if actor_user_id is None:
        return "system"
    if actor_user_id == task.agent_user_id:
        return "coachee"
    if actor_user_id == task.owner_user_id:
        return "owner"
    return "reviewer"


def history(db: Session, caller: CoachingCaller, *, task_id: str) -> list[dict[str, Any]]:
    """The task's audit events, oldest first.  In the coachee's view an actor id is shown
    only for the coachee or the current owner (any other actor is ``reviewer``), and a
    user id stays in the details only when it is the coachee's or the current owner's."""
    task, _, view = _visible(db, caller, task_id)
    events = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == caller.workspace_id,
            AuditEvent.target_type == TARGET_TYPE,
            AuditEvent.target_id == task.id,
        )
        .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
    )
    shown = {task.agent_user_id, task.owner_user_id} - {None}
    entries: list[dict[str, Any]] = []
    for event in events:
        actor = event.actor_user_id
        role = _actor_role(task, actor)
        details = dict(event.details or {})
        if view == AGENT:
            if actor is not None and actor not in shown:
                actor, role = None, "reviewer"
            details = {
                key: value
                for key, value in details.items()
                if "user_id" not in key or value in shown
            }
        entries.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "occurred_at": as_utc(event.occurred_at),
                "actor_user_id": actor,
                "actor_role": role,
                "details": details,
            }
        )
    return entries
