"""Blinded calibration batches (HOM-75, ADR 0056).

* ``GET /calibration-batches``: the visible batches, ``created_at`` then ``id``
  descending, keyset pages.  An admin sees every batch of the workspace, anyone else
  only the batches they are a participant of.  ``review:read``.
* ``GET /calibration-batches/{batch_id}``: one visible batch, blinded per the caller.
  ``review:read``.
* ``GET /calibration-batches/{batch_id}/report``: the agreement report of a closed
  batch, computed on read.  ``review:read``.
* ``POST /calibration-batches``: create a batch (201).  ``review:manage`` with CSRF.
* ``POST /calibration-batches/{batch_id}/examples/{example_id}/criteria/
  {criterion_id}/answer`` and ``.../adjudication``: upsert one outcome while the
  batch is open.  ``review:manage`` with CSRF.
* ``POST /calibration-batches/{batch_id}/close`` and ``.../cancel``: the batch's
  compare-and-set.  ``review:manage`` with CSRF.

A batch the caller may not see is ``404 calibration_batch_not_found``, never 403.
The record rules are in ``backend.services.calibration_service``; the audit details
never hold an outcome, a note or the title.  No API key carries a review permission.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.schemas.calibration import (
    CalibrationAdjudicationRequest,
    CalibrationAnswerRequest,
    CalibrationBatchCreateRequest,
    CalibrationBatchListResponse,
    CalibrationBatchResponse,
    CalibrationBatchWriteRequest,
    CalibrationReportResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.security.modules import require_module
from backend.services.audit_service import record_audit_event
from backend.services.calibration_service import (
    Change,
    adjudicate_criterion,
    answer_criterion,
    batch_report,
    batch_view,
    cancel_batch,
    close_batch,
    create_batch,
    list_batches,
)
from backend.services.review_queue_service import Reviewer, ReviewQueueError, reviewer

router = APIRouter(
    prefix="/calibration-batches",
    tags=["calibration-batches"],
    dependencies=[Depends(require_module("quality"))],
)

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


def _record(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    change: Change,
) -> None:
    record_audit_event(
        db,
        event_type=change.event,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type=change.target_type,
        target_id=change.target_id,
        request_id=getattr(request.state, "request_id", None),
        details=change.details,
        settings=settings,
    )


def _commit(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    change: Change,
) -> CalibrationBatchResponse:
    """One audit event and one commit for a change; nothing for a no-change write."""
    if change.event is not None:
        _record(db, request, context, settings, change)
        db.commit()
    return CalibrationBatchResponse.model_validate(change.view)


@router.get("", response_model=CalibrationBatchListResponse)
def list_calibration_batches(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> CalibrationBatchListResponse:
    """An admin sees every batch of the workspace; anyone else only their own."""
    now = datetime.now(UTC)
    try:
        page = list_batches(db, _caller(db, context, now), limit=limit, cursor=cursor)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CalibrationBatchListResponse.model_validate(page)


@router.post("", response_model=CalibrationBatchResponse, status_code=201)
def create_calibration_batch(
    payload: CalibrationBatchCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalibrationBatchResponse:
    """Create a batch: a fixed list of examples in the given order and a fixed list
    of participants.  One ``calibration_batch.created`` audit event with ids and
    counts only; a refusal writes nothing."""
    now = datetime.now(UTC)
    try:
        change = create_batch(
            db,
            _caller(db, context, now),
            title=payload.title,
            scorecard_result_ids=payload.scorecard_result_ids,
            participant_user_ids=payload.participant_user_ids,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change)


@router.get("/{batch_id}", response_model=CalibrationBatchResponse)
def read_calibration_batch(
    batch_id: str,
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> CalibrationBatchResponse:
    now = datetime.now(UTC)
    try:
        view = batch_view(db, _caller(db, context, now), batch_id=batch_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CalibrationBatchResponse.model_validate(view)


@router.get("/{batch_id}/report", response_model=CalibrationReportResponse)
def read_calibration_batch_report(
    batch_id: str,
    context: WorkspaceContext = Depends(require_review_read),
    db: Session = Depends(get_db),
) -> CalibrationReportResponse:
    """The agreement report of a closed batch.  Admins get everything; a non-admin
    participant gets the overall block, the per-criterion block and only their own
    participant row (no machine row)."""
    now = datetime.now(UTC)
    try:
        report = batch_report(db, _caller(db, context, now), batch_id=batch_id)
    except ReviewQueueError as error:
        raise _refused(error) from error
    return CalibrationReportResponse.model_validate(report)


@router.post(
    "/{batch_id}/examples/{example_id}/criteria/{criterion_id}/answer",
    response_model=CalibrationBatchResponse,
)
def answer_calibration_criterion(
    batch_id: str,
    example_id: str,
    criterion_id: str,
    payload: CalibrationAnswerRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalibrationBatchResponse:
    """Answer one reviewable criterion (a participant, while the batch is open).  An
    identical answer writes nothing.  The audit event carries ids only, never the
    outcome."""
    now = datetime.now(UTC)
    try:
        change = answer_criterion(
            db,
            _caller(db, context, now),
            batch_id=batch_id,
            example_id=example_id,
            criterion_id=criterion_id,
            outcome=payload.outcome,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change)


@router.post(
    "/{batch_id}/examples/{example_id}/criteria/{criterion_id}/adjudication",
    response_model=CalibrationBatchResponse,
)
def adjudicate_calibration_criterion(
    batch_id: str,
    example_id: str,
    criterion_id: str,
    payload: CalibrationAdjudicationRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalibrationBatchResponse:
    """Record the correct outcome of one criterion (a review admin who is not a
    participant, while the batch is open).  An identical adjudication writes nothing.
    The audit event carries ids only, never the outcome or the note."""
    now = datetime.now(UTC)
    try:
        change = adjudicate_criterion(
            db,
            _caller(db, context, now),
            batch_id=batch_id,
            example_id=example_id,
            criterion_id=criterion_id,
            outcome=payload.outcome,
            note=payload.note,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change)


@router.post("/{batch_id}/close", response_model=CalibrationBatchResponse)
def close_calibration_batch(
    batch_id: str,
    payload: CalibrationBatchWriteRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalibrationBatchResponse:
    """Close the batch once every reviewable criterion of every example is
    adjudicated; the revision grows by one.  Only a closed batch reveals answers next
    to adjudications and has a report."""
    now = datetime.now(UTC)
    try:
        change = close_batch(
            db,
            _caller(db, context, now),
            batch_id=batch_id,
            expected_revision=payload.expected_revision,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change)


@router.post("/{batch_id}/cancel", response_model=CalibrationBatchResponse)
def cancel_calibration_batch(
    batch_id: str,
    payload: CalibrationBatchWriteRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_review_manage),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CalibrationBatchResponse:
    """Cancel the batch; a cancelled batch never reveals answers and has no report."""
    now = datetime.now(UTC)
    try:
        change = cancel_batch(
            db,
            _caller(db, context, now),
            batch_id=batch_id,
            expected_revision=payload.expected_revision,
            now=now,
        )
    except ReviewQueueError as error:
        raise _refused(error) from error
    return _commit(db, request, context, settings, change)
