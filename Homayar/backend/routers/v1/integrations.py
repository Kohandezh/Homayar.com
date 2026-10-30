"""CRM mapping profiles, received records and the reconciliation queue (HOM-88, ADR 0048).

Profiles and the queue need ``workspace:manage``, which an API key never carries, so
only people configure a mapping or decide a case.  Records need ``meeting:update`` and
unrestricted record access: a team-scoped member (or a key bound to one) could
otherwise link conversations it cannot see.  Errors use the repo shape: the route
raises ``HTTPException(detail={"code", "message"})`` and the app answers ``error.code``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import ReconciliationReason, ReconciliationStatus
from backend.schemas.integrations import (
    InboundRecordRequest,
    InboundRecordResponse,
    MappingProfileListResponse,
    MappingProfileRequest,
    MappingProfileResponse,
    ReconciliationItemResponse,
    ReconciliationListResponse,
    ResolveRequest,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.crm_mapping_service import (
    CrmMappingError,
    get_item,
    get_profile,
    list_items,
    list_profiles,
    put_profile,
    resolve_item,
    submit_record,
    validate_profile,
)

router = APIRouter(tags=["integrations"])


def _http(error: CrmMappingError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code, detail={"code": error.code, "message": error.message}
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/integrations/mapping-profiles", response_model=MappingProfileListResponse)
def list_mapping_profiles(
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> MappingProfileListResponse:
    profiles = list_profiles(db, workspace_id=context.workspace.id)
    return MappingProfileListResponse(
        items=[MappingProfileResponse.model_validate(profile) for profile in profiles]
    )


@router.get("/integrations/mapping-profiles/{source_system}", response_model=MappingProfileResponse)
def get_mapping_profile(
    source_system: str,
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> MappingProfileResponse:
    profile = get_profile(db, workspace_id=context.workspace.id, source_system=source_system)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "mapping_profile_not_found", "message": "Mapping profile not found"},
        )
    return MappingProfileResponse.model_validate(profile)


@router.put(
    "/integrations/mapping-profiles/{source_system}",
    response_model=MappingProfileResponse,
    responses={201: {"model": MappingProfileResponse, "description": "Profile created"}},
)
def put_mapping_profile(
    source_system: str,
    body: MappingProfileRequest,
    request: Request,
    response: Response,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MappingProfileResponse:
    """Create the profile (201, version 1) or replace it (200).  A replace that changes
    nothing keeps the version and writes no audit event."""
    now = datetime.now(UTC)
    try:
        values = validate_profile(
            db,
            workspace_id=context.workspace.id,
            source_system=source_system,
            conversation_source_system=body.conversation_source_system,
            field_map=body.field_map,
            outcome_map=body.outcome_map,
            agent_map=body.agent_map,
            late_record_window_seconds=body.late_record_window_seconds,
            is_active=body.is_active,
            now=now,
        )
        profile, created = put_profile(
            db,
            workspace=context.workspace,
            source_system=source_system,
            values=values,
            actor_user_id=context.actor.user.id,
            request_id=_request_id(request),
            settings=settings,
            now=now,
        )
    except CrmMappingError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    db.refresh(profile)
    response.status_code = 201 if created else 200
    return MappingProfileResponse.model_validate(profile)


@router.post(
    "/integrations/{source_system}/records",
    response_model=InboundRecordResponse,
    status_code=201,
    responses={200: {"model": InboundRecordResponse, "description": "Replay of a record"}},
)
def post_inbound_record(
    source_system: str,
    body: InboundRecordRequest,
    request: Request,
    response: Response,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InboundRecordResponse:
    """Record and apply one CRM record: 201 new, 200 replay, 409 conflict."""
    if context.record_access.restricted:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "record_scope_restricted",
                "message": "Posting CRM records needs unrestricted record access",
            },
        )
    api_key = context.actor.api_key
    try:
        outcome = submit_record(
            db,
            workspace=context.workspace,
            source_system=source_system,
            event_id=body.event_id,
            payload=body.payload,
            actor_user_id=context.actor.user.id,
            api_key_id=api_key.id if api_key is not None else None,
            request_id=_request_id(request),
            settings=settings,
            now=datetime.now(UTC),
        )
    except CrmMappingError as error:
        db.rollback()
        raise _http(error) from error
    record = outcome.record
    answer = InboundRecordResponse(
        id=record.id,
        source_system=record.source_system,
        event_id=record.event_id,
        state=record.state,
        meeting_id=record.meeting_id,
        replayed=outcome.replayed,
        received_at=record.received_at,
        expires_at=record.expires_at,
        reconciliation_item_ids=list(outcome.item_ids),
    )
    conflict_count = record.conflict_count
    db.commit()  # the conflict counter and its audit row are kept
    if outcome.conflict:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "inbound_record_conflict",
                "message": "This event id was received before with another payload",
                "details": {"inbound_record_id": answer.id, "conflict_count": conflict_count},
            },
        )
    if outcome.replayed:
        response.status_code = 200
    return answer


@router.get("/integrations/reconciliation", response_model=ReconciliationListResponse)
def list_reconciliation_items(
    status: ReconciliationStatus = Query(default=ReconciliationStatus.OPEN),
    source_system: str | None = Query(default=None, min_length=1, max_length=60),
    reason: ReconciliationReason | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = Query(default=None),
    before_id: str | None = Query(default=None, min_length=1, max_length=36),
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> ReconciliationListResponse:
    """Newest first.  Page with ``before`` + ``before_id`` from the last answer: items
    of one record or one sweep run share ``created_at``, the id breaks the tie."""
    if before_id is not None and before is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_cursor", "message": "before_id needs before"},
        )
    items = list_items(
        db,
        workspace_id=context.workspace.id,
        status=status.value,
        source_system=source_system,
        reason=reason.value if reason is not None else None,
        limit=limit,
        before=before,
        before_id=before_id,
    )
    full = len(items) == limit
    return ReconciliationListResponse(
        items=[ReconciliationItemResponse.model_validate(item) for item in items],
        next_before=items[-1].created_at if full else None,
        next_before_id=items[-1].id if full else None,
    )


@router.get("/integrations/reconciliation/{item_id}", response_model=ReconciliationItemResponse)
def get_reconciliation_item(
    item_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> ReconciliationItemResponse:
    try:
        item = get_item(db, workspace_id=context.workspace.id, item_id=item_id)
    except CrmMappingError as error:
        raise _http(error) from error
    return ReconciliationItemResponse.model_validate(item)


@router.post(
    "/integrations/reconciliation/{item_id}/resolve", response_model=ReconciliationItemResponse
)
def resolve_reconciliation_item(
    item_id: str,
    body: ResolveRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReconciliationItemResponse:
    try:
        item = resolve_item(
            db,
            workspace_id=context.workspace.id,
            item_id=item_id,
            resolution=body.resolution,
            meeting_id=body.meeting_id,
            user_id=body.user_id,
            outcome=body.outcome,
            actor_user_id=context.actor.user.id,
            request_id=_request_id(request),
            settings=settings,
            now=datetime.now(UTC),
        )
    except CrmMappingError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    db.refresh(item)
    return ReconciliationItemResponse.model_validate(item)
