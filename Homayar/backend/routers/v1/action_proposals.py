"""Approval-bound action proposals (HOM-135, WP24-T03, F60, ADR 0082 D3 to D6).

Every route is session only (an API key gets ``401 api_key_not_accepted`` before any key
lookup, in every module state) and sits behind the ``integrations`` module guard, which
resolves its workspace from the same session-only dependency.  Writes need CSRF.

* ``POST /action-proposals`` (``action:propose``): build a proposal from a source (201).
* ``GET /action-proposals`` (``action:propose``): the visible proposals, oldest first;
  filters ``status`` (repeatable), ``action_type``, ``meeting_id``; ``limit`` and
  ``cursor``.  Rows leave out the payload and the live staleness check.
* ``GET /action-proposals/{proposal_id}`` (``action:propose``): the preview.
* ``POST /action-proposals/{proposal_id}/refresh`` (``action:propose``): rebuild it.
* ``POST /action-proposals/{proposal_id}/approve`` (``action:approve``): approve the
  exact payload, optionally edited, and write its one delivery request (201).
* ``POST /action-proposals/{proposal_id}/reject`` and ``/revoke`` (``action:approve``).

A proposal the caller may not see is ``404 action_proposal_not_found``.  Each change
writes one audit event with target ``action_proposal``; a 409 or 422 refusal of a visible
proposal's refresh, approve, reject or revoke writes the same event type with outcome
``denied`` and ``reason_code``.  Nothing here writes outside Homayar (HOM-136 delivers).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.schemas.action_proposals import (
    ActionProposalApproveRequest,
    ActionProposalCreateRequest,
    ActionProposalDecisionRequest,
    ActionProposalListItem,
    ActionProposalListResponse,
    ActionProposalRefreshRequest,
    ActionProposalView,
)
from backend.security.authorization import role_has
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_session_permission,
    require_session_workspace,
    require_session_write_permission,
)
from backend.security.modules import require_module
from backend.services.action_proposal_service import (
    TARGET_TYPE,
    ActionProposalError,
    Outcome,
    ProposalCaller,
    approve_proposal,
    create_proposal,
    get_proposal,
    list_proposals,
    list_views,
    parse_list,
    proposal_view,
    refresh_proposal,
    reject_proposal,
    revoke_proposal,
)
from backend.services.audit_service import record_audit_event, record_audit_event_best_effort
from backend.services.coaching_service import CoachingCaller
from backend.services.review_queue_service import reviewer

router = APIRouter(
    tags=["action-proposals"],
    dependencies=[Depends(require_module("integrations", workspace=require_session_workspace))],
)

require_propose = require_session_permission(Permission.ACTION_PROPOSE)
require_propose_write = require_session_write_permission(Permission.ACTION_PROPOSE)
require_approve_write = require_session_write_permission(Permission.ACTION_APPROVE)


def _now() -> datetime:
    """One clock per request (tests replace it to reach the time edges)."""
    return datetime.now(UTC)


def _caller(db: Session, context: WorkspaceContext, now: datetime) -> ProposalCaller:
    role = context.membership.role
    return ProposalCaller(
        workspace_id=context.workspace.id,
        user_id=context.actor.user.id,
        record_access=context.record_access,
        coaching=CoachingCaller(
            reviewer=reviewer(
                db,
                workspace_id=context.workspace.id,
                user_id=context.actor.user.id,
                role=role,
                now=now,
            ),
            supervises=role_has(role, Permission.REVIEW_READ),
        ),
    )


def _refused(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    error: ActionProposalError,
) -> HTTPException:
    """Nothing of the refused write stays; a visible proposal's refusal is audited."""
    db.rollback()
    if error.event is not None and error.denied is not None:
        record_audit_event_best_effort(
            db,
            event_type=error.event,
            outcome="denied",
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type=TARGET_TYPE,
            target_id=error.denied["proposal_id"],
            request_id=getattr(request.state, "request_id", None),
            details=error.denied,
            settings=settings,
        )
    detail: dict = {"code": error.code, "message": str(error)}
    if error.details is not None:
        detail["details"] = error.details
    return HTTPException(status_code=error.status_code, detail=detail)


def _commit(
    db: Session,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    outcome: Outcome,
) -> ActionProposalView:
    """The view, one audit event and one commit for a change."""
    view = ActionProposalView.model_validate(proposal_view(db, outcome.proposal))
    record_audit_event(
        db,
        event_type=outcome.event,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type=TARGET_TYPE,
        target_id=outcome.proposal.id,
        request_id=getattr(request.state, "request_id", None),
        details=outcome.details,
        settings=settings,
    )
    db.commit()
    return view


@router.post("/action-proposals", response_model=ActionProposalView, status_code=201)
def create_action_proposal(
    payload: ActionProposalCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_propose_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalView:
    now = _now()
    try:
        outcome = create_proposal(db, _caller(db, context, now), body=payload.model_dump(), now=now)
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return _commit(db, request, context, settings, outcome)


@router.get("/action-proposals", response_model=ActionProposalListResponse)
def list_action_proposals(
    request: Request,
    status: list[str] | None = Query(default=None),
    action_type: str | None = Query(default=None),
    meeting_id: str | None = Query(default=None),
    limit: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    context: WorkspaceContext = Depends(require_propose),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalListResponse:
    now = _now()
    try:
        query = parse_list(
            statuses=status,
            action_type=action_type,
            meeting_id=meeting_id,
            limit=limit,
            cursor=cursor,
        )
        page, next_cursor = list_proposals(db, _caller(db, context, now), query=query)
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return ActionProposalListResponse(
        items=[ActionProposalListItem.model_validate(row) for row in list_views(db, page)],
        next_cursor=next_cursor,
    )


@router.get("/action-proposals/{proposal_id}", response_model=ActionProposalView)
def get_action_proposal(
    proposal_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_propose),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalView:
    now = _now()
    try:
        proposal = get_proposal(db, _caller(db, context, now), proposal_id=proposal_id)
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return ActionProposalView.model_validate(proposal_view(db, proposal))


@router.post("/action-proposals/{proposal_id}/refresh", response_model=ActionProposalView)
def refresh_action_proposal(
    proposal_id: str,
    payload: ActionProposalRefreshRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_propose_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalView:
    now = _now()
    try:
        outcome = refresh_proposal(
            db,
            _caller(db, context, now),
            proposal_id=proposal_id,
            expected_revision=payload.expected_revision,
            now=now,
        )
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return _commit(db, request, context, settings, outcome)


@router.post(
    "/action-proposals/{proposal_id}/approve", response_model=ActionProposalView, status_code=201
)
def approve_action_proposal(
    proposal_id: str,
    payload: ActionProposalApproveRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_approve_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalView:
    now = _now()
    try:
        outcome = approve_proposal(
            db,
            _caller(db, context, now),
            proposal_id=proposal_id,
            expected_revision=payload.expected_revision,
            payload_hash=payload.payload_hash,
            source_transcript_revision_id=payload.source_transcript_revision_id,
            payload=payload.payload,
            now=now,
        )
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return _commit(db, request, context, settings, outcome)


@router.post("/action-proposals/{proposal_id}/reject", response_model=ActionProposalView)
def reject_action_proposal(
    proposal_id: str,
    payload: ActionProposalDecisionRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_approve_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalView:
    now = _now()
    try:
        outcome = reject_proposal(
            db,
            _caller(db, context, now),
            proposal_id=proposal_id,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            now=now,
        )
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return _commit(db, request, context, settings, outcome)


@router.post("/action-proposals/{proposal_id}/revoke", response_model=ActionProposalView)
def revoke_action_proposal(
    proposal_id: str,
    payload: ActionProposalDecisionRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_approve_write),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionProposalView:
    now = _now()
    try:
        outcome = revoke_proposal(
            db,
            _caller(db, context, now),
            proposal_id=proposal_id,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
            now=now,
        )
    except ActionProposalError as error:
        raise _refused(db, request, context, settings, error) from error
    return _commit(db, request, context, settings, outcome)
