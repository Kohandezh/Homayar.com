"""Federation group mapping routes (HOM-128, ADR 0066).

Organization admins decide by hand which IdP group grants which workspace
role; the reconciler applies the stored rules to every federated identity of
the organization.  Every route resolves the caller's current organization
membership exactly like the other ``/organizations/{organization_id}`` routes;
nothing here is reachable with an API key (the write dependencies are session
+ CSRF, the read one session only).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import FederationGroupMapping, WorkspaceRole, new_id
from backend.schemas.federation import (
    GroupMappingCreateRequest,
    GroupMappingListResponse,
    GroupMappingResponse,
)
from backend.security.authorization import OrganizationPermission, can_delegate_workspace_role
from backend.security.context import (
    OrganizationContext,
    require_organization_permission,
    require_organization_write_permission,
)
from backend.services import organization_service as service
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.federation_service import reapply_organization_mappings

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _raise(exc: service.OrganizationServiceError) -> Never:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.public_message},
    ) from exc


def _mapping_response(mapping: FederationGroupMapping) -> GroupMappingResponse:
    return GroupMappingResponse(
        id=mapping.id,
        organization_id=mapping.organization_id,
        issuer=mapping.issuer,
        group_name=mapping.group_name,
        workspace_id=mapping.workspace_id,
        role=mapping.role.value,
        created_by_user_id=mapping.created_by_user_id,
        created_at=mapping.created_at,
    )


def _audit(
    request: Request,
    db: Session,
    context: OrganizationContext,
    *,
    event_type: AuditEventType,
    target_id: str,
    details: dict,
    settings: Settings,
) -> None:
    # No workspace_id on the row: only the organization is bound here, and the
    # write is flushed while that context holds (RLS).  The workspace id rides
    # in details, like the scope-grant routes.
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=context.actor.user.id,
        target_type="federation_group_mapping",
        target_id=target_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        details={"organization_id": context.organization.id, **details},
        settings=settings,
    )


@router.get("/{organization_id}/federation/group-mappings", response_model=GroupMappingListResponse)
def list_group_mappings(
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.WORKSPACE_ADMINISTER)
    ),
    db: Session = Depends(get_db),
) -> GroupMappingListResponse:
    mappings = db.scalars(
        select(FederationGroupMapping)
        .where(
            FederationGroupMapping.organization_id == context.organization.id,
            FederationGroupMapping.revoked_at.is_(None),
        )
        .order_by(FederationGroupMapping.created_at.asc(), FederationGroupMapping.id.asc())
    ).all()
    return GroupMappingListResponse(items=[_mapping_response(mapping) for mapping in mappings])


@router.post(
    "/{organization_id}/federation/group-mappings",
    response_model=GroupMappingResponse,
    status_code=201,
)
def create_group_mapping(
    payload: GroupMappingCreateRequest,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.WORKSPACE_ADMINISTER)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> GroupMappingResponse:
    role = WorkspaceRole(payload.role)
    if not can_delegate_workspace_role(context.membership.role, role):
        _raise(service.DelegationDeniedError())
    try:
        workspace = service.get_organization_workspace(
            db, organization=context.organization, workspace_id=payload.workspace_id
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    duplicate = db.scalar(
        select(FederationGroupMapping.id).where(
            FederationGroupMapping.organization_id == context.organization.id,
            FederationGroupMapping.issuer == payload.issuer,
            FederationGroupMapping.group_name == payload.group_name,
            FederationGroupMapping.workspace_id == workspace.id,
            FederationGroupMapping.revoked_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "group_mapping_exists",
                "message": "An identical group mapping is already active",
            },
        )
    mapping = FederationGroupMapping(
        id=new_id(),
        organization_id=context.organization.id,
        issuer=payload.issuer,
        group_name=payload.group_name,
        workspace_id=workspace.id,
        role=role,
        created_by_user_id=context.actor.user.id,
    )
    db.add(mapping)
    try:
        db.flush()
    except IntegrityError as exc:
        # Two identical concurrent posts: the partial unique index refused the
        # second one after both passed the SELECT.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "group_mapping_exists",
                "message": "An identical group mapping is already active",
            },
        ) from exc
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.FEDERATION_GROUP_MAPPING_CREATED,
        target_id=mapping.id,
        details={
            "issuer": mapping.issuer,
            "group_name": mapping.group_name,
            "workspace_id": workspace.id,
            "role": mapping.role.value,
        },
        settings=settings,
    )
    db.flush()
    reapply_organization_mappings(
        db,
        organization_id=context.organization.id,
        issuer=payload.issuer,
        now=datetime.now(UTC),
        settings=settings,
    )
    db.commit()
    return _mapping_response(mapping)


@router.delete(
    "/{organization_id}/federation/group-mappings/{mapping_id}",
    status_code=204,
    response_class=Response,
)
def revoke_group_mapping(
    mapping_id: str,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.WORKSPACE_ADMINISTER)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    now = datetime.now(UTC)
    mapping = db.scalar(
        select(FederationGroupMapping).where(
            FederationGroupMapping.id == mapping_id,
            FederationGroupMapping.organization_id == context.organization.id,
            FederationGroupMapping.revoked_at.is_(None),
        )
    )
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "group_mapping_not_found",
                "message": "Group mapping not found",
            },
        )
    mapping.revoked_at = now
    mapping.revoked_by_user_id = context.actor.user.id
    db.flush()
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.FEDERATION_GROUP_MAPPING_REVOKED,
        target_id=mapping.id,
        details={
            "issuer": mapping.issuer,
            "group_name": mapping.group_name,
            "workspace_id": mapping.workspace_id,
            "role": mapping.role.value,
        },
        settings=settings,
    )
    db.flush()
    reapply_organization_mappings(
        db,
        organization_id=context.organization.id,
        issuer=mapping.issuer,
        now=now,
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)
