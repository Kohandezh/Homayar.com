"""Coaching tasks from accepted findings (HOM-77, ADR 0062 D3).

Supervisor side (HOM-73 permissions):

* ``POST /review-items/{item_id}/coaching-tasks`` (``review:manage``, CSRF): create a task
  from a failed finding of a completed review (201).
* ``GET /coaching-tasks`` (``review:read``): the visible tasks, ``due_at`` then ``id``;
  filters ``status`` (repeatable; every status by default), ``owner_user_id``,
  ``agent_user_id`` and ``overdue``.
* ``POST /coaching-tasks/{task_id}/reassign``, ``/close`` and ``/cancel``
  (``review:manage``, CSRF).

Coachee side (``coaching:respond``, every role, never an API key):

* ``GET /my-coaching-tasks``: the caller's tasks as coachee, newest first.
* ``POST /coaching-tasks/{task_id}/acknowledge`` (CSRF): the coachee only.

Both sides (``coaching:respond``):

* ``GET /coaching-tasks/{task_id}``: the supervisor view, or the agent view.
* ``GET /coaching-tasks/{task_id}/history``: the audit events, oldest first.

A task the caller may not see is ``404 coaching_task_not_found``; the record rules are in
``backend.services.coaching_service``.  Each change writes one audit event with target
``coaching_task``; a refusal and a no-op write none.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.schemas.coaching_tasks import (
    CoachingHistoryResponse,
    CoachingTaskAcknowledgeRequest,
    CoachingTaskCancelRequest,
    CoachingTaskCloseRequest,
    CoachingTaskCreateRequest,
    CoachingTaskListResponse,
    CoachingTaskReassignRequest,
    CoachingTaskView,
)
from backend.security.authorization import role_has
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.audit_service import record_audit_event
from backend.services.coaching_service import (
    TARGET_TYPE,
    CoachingCaller,
    Outcome,
    ReviewQueueError,
    acknowledge_task,
    cancel_task,
    close_task,
    create_task,
    get_task,
    history,
    list_my_tasks,
    list_tasks,
    parse_filters,
    parse_statuses,
    reassign_task,
    task_view,
)
from backend.services.review_queue_service import reviewer

router = APIRouter(tags=["coaching-tasks"])

require_respond = require_permission(Permission.COACHING_RESPOND)
require_respond_write = require_write_permission(Permission.COACHING_RESPOND)
require_review_read = require_permission(Permission.REVIEW_READ)
require_review_manage = require_write_permission(Permission.REVIEW_MANAGE)


def _now() -> datetime:
    """One clock per request (tests replace it to reach the due-time edges)."""
    return datetime.now(UTC)


def _refused(error: ReviewQueueError) -> HTTPException:
    detail: dict = {"code": error.code, "message": str(error)}
    if error.details is not None:
        detail["details"] = error.details
    return HTTPException(status_code=error.status_code, detail=detail)


def _caller(db: Session, context: WorkspaceContext, now: datetime) -> CoachingCaller:
    role = context.membership.role
    return CoachingCaller(
        reviewer=reviewer(
            db,
            workspace_id=context.workspace.id,
            user_id=context.actor.user.id,
            role=role,
            now=now,
        ),
        supervises=role_has(role, Permission.REVIEW_READ),
    )


def _commit(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    outcome: Outcome,
    now: datetime,
) -> CoachingTaskView:
    """One audit event and one commit for a change; nothing for a no-op."""
    if outcome.event is not None:
        record_audit_event(
            db,
            event_type=outcome.event,
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type=TARGET_TYPE,
            target_id=outcome.task.id,
            request_id=getattr(request.state, "request_id", None),
            details=outcome.details,
            settings=settings,
        )
        db.commit()
    return CoachingTaskView.model_validate(
        task_view(outcome.task, outcome.meeting, outcome.view, now=now)
    )


# --- the supervisor side ------------------------------------------------------------------


@router.post(
    "/review-items/{item_id}/coaching-tasks", response_model=CoachingTaskView, status_code=201
)
def create_coaching_task(
    item_id: str,
    payload: CoachingTaskCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoachingTaskView:
    now = _now()
    try:
        outcome = create_task(
            db, _caller(db, context, now), item_id=item_id, body=payload.model_dump(), now=now
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome, now)


@router.get("/coaching-tasks", response_model=CoachingTaskListResponse)
def list_coaching_tasks(
    status: list[str] | None = Query(default=None),
    owner_user_id: str | None = Query(default=None, max_length=64),
    agent_user_id: str | None = Query(default=None, max_length=64),
    overdue: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> CoachingTaskListResponse:
    """A caller without review authority gets an empty list, not a 403."""
    now = _now()
    try:
        filters = parse_filters(
            statuses=status,
            owner_user_id=owner_user_id,
            agent_user_id=agent_user_id,
            overdue=overdue,
        )
        rows, next_cursor = list_tasks(
            db, _caller(db, context, now), filters=filters, limit=limit, cursor=cursor, now=now
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CoachingTaskListResponse(
        items=[
            CoachingTaskView.model_validate(task_view(task, meeting, "supervisor", now=now))
            for task, meeting in rows
        ],
        next_cursor=next_cursor,
    )


@router.post("/coaching-tasks/{task_id}/reassign", response_model=CoachingTaskView)
def reassign_coaching_task(
    task_id: str,
    payload: CoachingTaskReassignRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoachingTaskView:
    """The same owner again changes nothing and writes no audit event."""
    now = _now()
    try:
        outcome = reassign_task(
            db,
            _caller(db, context, now),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            owner_user_id=payload.owner_user_id,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome, now)


@router.post("/coaching-tasks/{task_id}/close", response_model=CoachingTaskView)
def close_coaching_task(
    task_id: str,
    payload: CoachingTaskCloseRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoachingTaskView:
    now = _now()
    try:
        outcome = close_task(
            db,
            _caller(db, context, now),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            body=payload.model_dump(),
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome, now)


@router.post("/coaching-tasks/{task_id}/cancel", response_model=CoachingTaskView)
def cancel_coaching_task(
    task_id: str,
    payload: CoachingTaskCancelRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoachingTaskView:
    now = _now()
    try:
        outcome = cancel_task(
            db,
            _caller(db, context, now),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            body=payload.model_dump(),
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome, now)


# --- the coachee side and the shared reads ------------------------------------------------


@router.get("/my-coaching-tasks", response_model=CoachingTaskListResponse)
def list_own_coaching_tasks(
    status: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_respond),
    db: Session = Depends(get_db),
) -> CoachingTaskListResponse:
    now = _now()
    try:
        rows, next_cursor = list_my_tasks(
            db,
            _caller(db, context, now),
            statuses=parse_statuses(status),
            limit=limit,
            cursor=cursor,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CoachingTaskListResponse(
        items=[
            CoachingTaskView.model_validate(task_view(task, meeting, "agent", now=now))
            for task, meeting in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/coaching-tasks/{task_id}", response_model=CoachingTaskView)
def read_coaching_task(
    task_id: str,
    context: WorkspaceContext = Depends(require_respond),
    db: Session = Depends(get_db),
) -> CoachingTaskView:
    now = _now()
    try:
        task, meeting, view = get_task(db, _caller(db, context, now), task_id=task_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CoachingTaskView.model_validate(task_view(task, meeting, view, now=now))


@router.get("/coaching-tasks/{task_id}/history", response_model=CoachingHistoryResponse)
def read_coaching_task_history(
    task_id: str,
    context: WorkspaceContext = Depends(require_respond),
    db: Session = Depends(get_db),
) -> CoachingHistoryResponse:
    now = _now()
    try:
        entries = history(db, _caller(db, context, now), task_id=task_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CoachingHistoryResponse.model_validate({"items": entries, "next_cursor": None})


@router.post("/coaching-tasks/{task_id}/acknowledge", response_model=CoachingTaskView)
def acknowledge_coaching_task(
    task_id: str,
    payload: CoachingTaskAcknowledgeRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_respond_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoachingTaskView:
    now = _now()
    try:
        outcome = acknowledge_task(
            db,
            _caller(db, context, now),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            note=payload.note,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome, now)
