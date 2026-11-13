"""Agent appeals of review results and supervisor dispositions (HOM-76, ADR 0057).

Agent side (``review:appeal``, every role, never an API key):

* ``GET /my-reviews``: the caller's completed, human-scored reviews, newest first.
* ``GET /my-reviews/{item_id}``: one of them with each criterion; no reviewer user id.
* ``POST /my-reviews/{item_id}/appeal``: file the one appeal of the item (201).  CSRF.
* ``GET /my-appeals``: the caller's own appeals, newest first.
* ``POST /review-appeals/{appeal_id}/withdraw``: the appellant only.  CSRF.

Supervisor side (HOM-73 permissions):

* ``GET /review-appeals`` (``review:read``): the visible appeals, ``created_at`` then
  ``id``; filters ``status`` (repeatable) and ``assignee_user_id``.
* ``POST /review-appeals/{appeal_id}/assign`` and ``/decide`` (``review:manage``, CSRF).

Both sides (``review:appeal``):

* ``GET /review-appeals/{appeal_id}``: the supervisor view, or the appellant view.
* ``GET /review-appeals/{appeal_id}/history``: the audit events, oldest first.

An appeal the caller may not see is ``404 review_appeal_not_found``; the record rules are
in ``backend.services.review_appeal_service``.  Each change writes one audit event with
target ``review_appeal``; a refusal and a no-op write none.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.schemas.review_appeals import (
    AppealAssignRequest,
    AppealDecideRequest,
    AppealFileRequest,
    AppealHistoryResponse,
    AppealListResponse,
    AppealView,
    AppealWriteRequest,
    MyReviewDetail,
    MyReviewListResponse,
)
from backend.security.authorization import role_has
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.security.modules import require_module
from backend.services.audit_service import record_audit_event
from backend.services.review_appeal_service import (
    AppealCaller,
    Outcome,
    ReviewQueueError,
    appeal_view,
    assign_appeal,
    decide_appeal,
    file_appeal,
    get_appeal,
    get_my_review,
    history,
    list_appeals,
    list_my_appeals,
    list_my_reviews,
    parse_statuses,
    withdraw_appeal,
)
from backend.services.review_queue_service import reviewer

router = APIRouter(tags=["review-appeals"], dependencies=[Depends(require_module("quality"))])

require_appeal = require_permission(Permission.REVIEW_APPEAL)
require_appeal_write = require_write_permission(Permission.REVIEW_APPEAL)
require_review_read = require_permission(Permission.REVIEW_READ)
require_review_manage = require_write_permission(Permission.REVIEW_MANAGE)


def _now() -> datetime:
    """One clock per request (tests replace it to reach the window's edges)."""
    return datetime.now(UTC)


def _refused(error: ReviewQueueError) -> HTTPException:
    detail: dict = {"code": error.code, "message": str(error)}
    if error.details is not None:
        detail["details"] = error.details
    return HTTPException(status_code=error.status_code, detail=detail)


def _caller(db: Session, context: WorkspaceContext, now: datetime) -> AppealCaller:
    role = context.membership.role
    return AppealCaller(
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
    db: Session, request: Request, context: WorkspaceContext, settings: Settings, outcome: Outcome
) -> AppealView:
    """One audit event and one commit for a change; nothing for a no-op."""
    if outcome.event is not None:
        record_audit_event(
            db,
            event_type=outcome.event,
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type="review_appeal",
            target_id=outcome.appeal.id,
            request_id=getattr(request.state, "request_id", None),
            details=outcome.details,
            settings=settings,
        )
        db.commit()
    return AppealView.model_validate(appeal_view(db, outcome.appeal, outcome.meeting, outcome.view))


# --- the agent side -----------------------------------------------------------------------


@router.get("/my-reviews", response_model=MyReviewListResponse)
def list_my_review_items(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_appeal),
    db: Session = Depends(get_db),
) -> MyReviewListResponse:
    now = _now()
    try:
        page = list_my_reviews(db, _caller(db, context, now), limit=limit, cursor=cursor)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return MyReviewListResponse.model_validate(
        {"items": page.items, "next_cursor": page.next_cursor}
    )


@router.get("/my-reviews/{item_id}", response_model=MyReviewDetail)
def read_my_review_item(
    item_id: str,
    context: WorkspaceContext = Depends(require_appeal),
    db: Session = Depends(get_db),
) -> MyReviewDetail:
    now = _now()
    try:
        data = get_my_review(db, _caller(db, context, now), item_id=item_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return MyReviewDetail.model_validate(data)


@router.post("/my-reviews/{item_id}/appeal", response_model=AppealView, status_code=201)
def file_review_appeal(
    item_id: str,
    payload: AppealFileRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_appeal_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AppealView:
    now = _now()
    try:
        outcome = file_appeal(
            db, _caller(db, context, now), item_id=item_id, body=payload.model_dump(), now=now
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome)


@router.get("/my-appeals", response_model=AppealListResponse)
def list_own_appeals(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_appeal),
    db: Session = Depends(get_db),
) -> AppealListResponse:
    now = _now()
    try:
        rows, next_cursor = list_my_appeals(
            db, _caller(db, context, now), limit=limit, cursor=cursor
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return AppealListResponse(
        items=[
            AppealView.model_validate(appeal_view(db, appeal, meeting, "appellant"))
            for appeal, meeting in rows
        ],
        next_cursor=next_cursor,
    )


# --- the supervisor side ------------------------------------------------------------------


@router.get("/review-appeals", response_model=AppealListResponse)
def list_review_appeals(
    status: list[str] | None = Query(default=None),
    assignee_user_id: str | None = Query(default=None, min_length=1, max_length=36),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> AppealListResponse:
    """A caller without review authority gets an empty list, not a 403."""
    now = _now()
    try:
        rows, next_cursor = list_appeals(
            db,
            _caller(db, context, now),
            statuses=parse_statuses(status),
            assignee_user_id=assignee_user_id,
            limit=limit,
            cursor=cursor,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return AppealListResponse(
        items=[
            AppealView.model_validate(appeal_view(db, appeal, meeting, "supervisor"))
            for appeal, meeting in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/review-appeals/{appeal_id}", response_model=AppealView)
def read_review_appeal(
    appeal_id: str,
    context: WorkspaceContext = Depends(require_appeal),
    db: Session = Depends(get_db),
) -> AppealView:
    now = _now()
    try:
        appeal, meeting, view = get_appeal(db, _caller(db, context, now), appeal_id=appeal_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return AppealView.model_validate(appeal_view(db, appeal, meeting, view))


@router.get("/review-appeals/{appeal_id}/history", response_model=AppealHistoryResponse)
def read_review_appeal_history(
    appeal_id: str,
    context: WorkspaceContext = Depends(require_appeal),
    db: Session = Depends(get_db),
) -> AppealHistoryResponse:
    now = _now()
    try:
        entries = history(db, _caller(db, context, now), appeal_id=appeal_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return AppealHistoryResponse.model_validate({"items": entries, "next_cursor": None})


@router.post("/review-appeals/{appeal_id}/withdraw", response_model=AppealView)
def withdraw_review_appeal(
    appeal_id: str,
    payload: AppealWriteRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_appeal_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AppealView:
    now = _now()
    try:
        outcome = withdraw_appeal(
            db,
            _caller(db, context, now),
            appeal_id=appeal_id,
            expected_revision=payload.expected_revision,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome)


@router.post("/review-appeals/{appeal_id}/assign", response_model=AppealView)
def assign_review_appeal(
    appeal_id: str,
    payload: AppealAssignRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AppealView:
    """The same assignee again changes nothing and writes no audit event."""
    now = _now()
    try:
        outcome = assign_appeal(
            db,
            _caller(db, context, now),
            appeal_id=appeal_id,
            expected_revision=payload.expected_revision,
            assignee_user_id=payload.assignee_user_id,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome)


@router.post("/review-appeals/{appeal_id}/decide", response_model=AppealView)
def decide_review_appeal(
    appeal_id: str,
    payload: AppealDecideRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AppealView:
    now = _now()
    try:
        outcome = decide_appeal(
            db,
            _caller(db, context, now),
            appeal_id=appeal_id,
            expected_revision=payload.expected_revision,
            body=payload.model_dump(),
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, outcome)
