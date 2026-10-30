"""The supervisor review queue (HOM-73, ADR 0051).

* ``GET /review-items``: the visible items, ``due_at`` then ``id``, keyset pages.
  ``review:read``.  Filters: ``status`` (repeatable; default ``open`` and
  ``in_review``), ``assignee`` (``me``, ``unassigned`` or a user id), ``overdue``,
  ``meeting_id``; a bad value is ``422 review_filter_invalid``.
* ``GET /review-items/{item_id}``: one visible item.  ``review:read``.
* ``POST /review-items``: queue a conversation (201).  ``review:manage`` with CSRF.
* ``POST /review-items/from-sample``: draw a seeded review sample (HOM-72) and queue its
  selections (201).  ``workspace:manage`` with CSRF.
* ``POST /review-items/{item_id}/assign``, ``/due-time``, ``/start``, ``/complete``,
  ``/cancel``: ``review:manage`` with CSRF and the revision the caller read.

An item the caller may not see is ``404 review_item_not_found``, never 403.  Every
change is audited; the record rules are in ``backend.services.review_queue_service``.
No API key carries a review permission.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.jobs.leases import as_utc
from backend.review_sampling import SamplingError
from backend.schemas.review_items import (
    ReviewItemAssignRequest,
    ReviewItemCancelRequest,
    ReviewItemCreateRequest,
    ReviewItemDueTimeRequest,
    ReviewItemListResponse,
    ReviewItemResponse,
    ReviewItemWriteRequest,
    ReviewSampleEnqueueRequest,
    ReviewSampleEnqueueResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.coverage_service import COVERAGE_VERSION, CoverageWindowError
from backend.services.review_queue_service import (
    Change,
    ItemView,
    Reviewer,
    ReviewQueueError,
    assign_item,
    cancel_item,
    change_due_time,
    complete_item,
    create_item,
    enqueue_sample,
    get_item,
    is_overdue,
    list_items,
    parse_filters,
    reviewer,
    start_item,
)

router = APIRouter(prefix="/review-items", tags=["review-items"])

require_review_read = require_permission(Permission.REVIEW_READ)
require_review_manage = require_write_permission(Permission.REVIEW_MANAGE)


def _refused(error: ReviewQueueError) -> HTTPException:
    detail: dict = {"code": error.code, "message": str(error)}
    if error.details is not None:
        detail["details"] = error.details
    return HTTPException(status_code=error.status_code, detail=detail)


def _caller(db: Session, context: WorkspaceContext, now: datetime) -> Reviewer:
    return reviewer(
        db,
        workspace_id=context.workspace.id,
        user_id=context.actor.user.id,
        role=context.membership.role,
        now=now,
    )


def _response(view: ItemView, now: datetime) -> ReviewItemResponse:
    item = view.item
    return ReviewItemResponse(
        id=item.id,
        meeting_id=item.meeting_id,
        team_id=view.team_id,
        scorecard_result_id=item.scorecard_result_id,
        source=item.source,
        source_details=item.source_details,
        status=item.status,
        assignee_user_id=item.assignee_user_id,
        assigned_by_user_id=item.assigned_by_user_id,
        assigned_at=as_utc(item.assigned_at),
        due_at=as_utc(item.due_at),
        is_overdue=is_overdue(item, now),
        created_by_user_id=item.created_by_user_id,
        created_at=as_utc(item.created_at),
        updated_at=as_utc(item.updated_at),
        started_at=as_utc(item.started_at),
        completed_at=as_utc(item.completed_at),
        cancelled_at=as_utc(item.cancelled_at),
        cancel_reason=item.cancel_reason,
        revision=item.revision,
    )


def _record(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    *,
    event_type: AuditEventType,
    target_type: str,
    target_id: str,
    details: dict,
) -> None:
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type=target_type,
        target_id=target_id,
        request_id=getattr(request.state, "request_id", None),
        details=details,
        settings=settings,
    )


def _commit(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    change: Change,
    now: datetime,
) -> ReviewItemResponse:
    """One audit event and one commit for a change; nothing for a no-change write."""
    if change.event is not None:
        _record(
            db,
            request,
            context,
            settings,
            event_type=change.event,
            target_type="review_item",
            target_id=change.view.item.id,
            details=change.details,
        )
        db.commit()
    return _response(change.view, now)


@router.get("", response_model=ReviewItemListResponse)
def list_review_items(
    status: list[str] | None = Query(default=None),
    assignee: str | None = Query(default=None),
    overdue: str | None = Query(default=None),
    meeting_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> ReviewItemListResponse:
    """A caller without review authority gets an empty list, not a 403."""
    now = datetime.now(UTC)
    try:
        filters = parse_filters(
            statuses=status, assignee=assignee, overdue=overdue, meeting_id=meeting_id
        )
        page = list_items(
            db, _caller(db, context, now), filters=filters, limit=limit, cursor=cursor, now=now
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return ReviewItemListResponse(
        items=[_response(view, now) for view in page.views], next_cursor=page.next_cursor
    )


@router.post("", response_model=ReviewItemResponse, status_code=201)
def create_review_item(
    payload: ReviewItemCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewItemResponse:
    now = datetime.now(UTC)
    try:
        change = create_item(
            db,
            _caller(db, context, now),
            meeting_id=payload.meeting_id,
            due_at=payload.due_at,
            assignee_user_id=payload.assignee_user_id,
            scorecard_result_id=payload.scorecard_result_id,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change, now)


# Declared before the ``/{item_id}`` routes, so ``from-sample`` is never read as an id.
@router.post("/from-sample", response_model=ReviewSampleEnqueueResponse, status_code=201)
def enqueue_review_sample(
    payload: ReviewSampleEnqueueRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewSampleEnqueueResponse:
    """The draw of ``POST /admin/review-samples`` (same fields, rules and 422 codes), plus
    ``due_at``.  One ``sample`` item per selection, in draw order; a conversation with an
    active item is skipped and listed.  One ``review_sample.enqueued`` audit event; a
    refusal writes no item and no event."""
    now = datetime.now(UTC)
    try:
        result = enqueue_sample(
            db,
            _caller(db, context, now),
            raw_from=payload.from_,
            raw_to=payload.to,
            seed=payload.seed,
            config=payload.config,
            due_at=payload.due_at,
            recompute_max_attempts=settings.recompute_max_attempts,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    except (CoverageWindowError, SamplingError) as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    _record(
        db,
        request,
        context,
        settings,
        event_type=AuditEventType.REVIEW_SAMPLE_ENQUEUED,
        target_type="workspace",
        target_id=context.workspace.id,
        details=result.audit_details,
    )
    db.commit()
    return ReviewSampleEnqueueResponse.model_validate(
        {
            "coverage_version": COVERAGE_VERSION,
            "window": result.sample.window.as_dict(),
            "draw": result.sample.draw.to_json(),
            "created": [_response(view, now) for view in result.created],
            "skipped_meeting_ids": result.skipped_meeting_ids,
        }
    )


@router.get("/{item_id}", response_model=ReviewItemResponse)
def read_review_item(
    item_id: str,
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> ReviewItemResponse:
    now = datetime.now(UTC)
    try:
        view = get_item(db, _caller(db, context, now), item_id=item_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _response(view, now)


@router.post("/{item_id}/assign", response_model=ReviewItemResponse)
def assign_review_item(
    item_id: str,
    payload: ReviewItemAssignRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewItemResponse:
    """A new assignee (or ``null``) sends an ``in_review`` item back to ``open``; the
    same assignee changes nothing and writes no audit event."""
    now = datetime.now(UTC)
    try:
        change = assign_item(
            db,
            _caller(db, context, now),
            item_id=item_id,
            expected_revision=payload.expected_revision,
            assignee_user_id=payload.assignee_user_id,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change, now)


@router.post("/{item_id}/due-time", response_model=ReviewItemResponse)
def change_review_item_due_time(
    item_id: str,
    payload: ReviewItemDueTimeRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewItemResponse:
    now = datetime.now(UTC)
    try:
        change = change_due_time(
            db,
            _caller(db, context, now),
            item_id=item_id,
            expected_revision=payload.expected_revision,
            due_at=payload.due_at,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change, now)


@router.post("/{item_id}/start", response_model=ReviewItemResponse)
def start_review_item(
    item_id: str,
    payload: ReviewItemWriteRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewItemResponse:
    """The assignee only (``403 review_not_assignee``); the item must be ``open``."""
    now = datetime.now(UTC)
    try:
        change = start_item(
            db,
            _caller(db, context, now),
            item_id=item_id,
            expected_revision=payload.expected_revision,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change, now)


@router.post("/{item_id}/complete", response_model=ReviewItemResponse)
def complete_review_item(
    item_id: str,
    payload: ReviewItemWriteRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewItemResponse:
    """The assignee only; the item must be ``in_review``.  Criterion decisions are HOM-74:
    completing only closes the queue entry."""
    now = datetime.now(UTC)
    try:
        change = complete_item(
            db,
            _caller(db, context, now),
            item_id=item_id,
            expected_revision=payload.expected_revision,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change, now)


@router.post("/{item_id}/cancel", response_model=ReviewItemResponse)
def cancel_review_item(
    item_id: str,
    payload: ReviewItemCancelRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewItemResponse:
    now = datetime.now(UTC)
    try:
        change = cancel_item(
            db,
            _caller(db, context, now),
            item_id=item_id,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change, now)
