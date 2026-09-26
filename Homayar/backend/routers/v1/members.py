from __future__ import annotations

from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import WorkspaceInvitation, WorkspaceRole
from backend.schemas.members import (
    AcceptInvitationRequest,
    ChangeMemberRoleRequest,
    InvitationAcceptedResponse,
    InvitationListResponse,
    InvitationResponse,
    InviteMemberRequest,
    MemberListResponse,
    MemberResponse,
)
from backend.security.context import (
    Actor,
    Permission,
    WorkspaceContext,
    require_csrf,
    require_permission,
    require_write_permission,
)
from backend.services.audit_service import record_audit_event
from backend.services.member_service import (
    InvitationExpiredError,
    InvitationIdentityMismatchError,
    InvitationNotFoundError,
    InvitationRevokedError,
    MemberNotFoundError,
    MemberOperationForbiddenError,
    MemberRecord,
    MemberServiceError,
    accept_invitation,
    change_member_role,
    create_invitation,
    invitation_status,
    list_workspace_invitations,
    list_workspace_members,
    remove_member,
    revoke_invitation,
    send_invitation_email,
)

router = APIRouter(prefix="/members", tags=["members"])


def _member_response(record: MemberRecord) -> MemberResponse:
    membership = record.membership
    user = record.user
    return MemberResponse(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        phone_e164=user.phone_e164,
        display_name=user.display_name,
        role=membership.role.value,
        joined_at=membership.created_at,
        valid_from=membership.valid_from,
        valid_until=membership.valid_until,
    )


def _invitation_response(invitation: WorkspaceInvitation) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        workspace_id=invitation.workspace_id,
        email=invitation.email,
        role=invitation.role.value,
        invited_by_user_id=invitation.invited_by_user_id,
        status=invitation_status(invitation),
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        revoked_at=invitation.revoked_at,
        created_at=invitation.created_at,
    )


def _raise_service_error(exc: MemberServiceError) -> Never:
    if isinstance(exc, (MemberNotFoundError, InvitationNotFoundError)):
        status_code = 404
    elif isinstance(exc, (InvitationExpiredError, InvitationRevokedError)):
        status_code = 410
    elif isinstance(exc, (MemberOperationForbiddenError, InvitationIdentityMismatchError)):
        status_code = 403
    else:
        status_code = 409
    raise HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.public_message},
    ) from exc


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("", response_model=MemberListResponse)
def list_members(
    context: WorkspaceContext = Depends(require_permission(Permission.MEMBER_READ)),
    db: Session = Depends(get_db),
) -> MemberListResponse:
    items = [
        _member_response(record) for record in list_workspace_members(db, context.workspace.id)
    ]
    return MemberListResponse(items=items, total=len(items))


@router.get("/invitations", response_model=InvitationListResponse)
def list_invitations(
    context: WorkspaceContext = Depends(require_permission(Permission.MEMBER_MANAGE)),
    db: Session = Depends(get_db),
) -> InvitationListResponse:
    items = [
        _invitation_response(invitation)
        for invitation in list_workspace_invitations(db, context.workspace.id)
    ]
    return InvitationListResponse(items=items, total=len(items))


@router.post("/invitations", response_model=InvitationResponse, status_code=201)
async def invite_member(
    payload: InviteMemberRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEMBER_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvitationResponse:
    try:
        issued = create_invitation(
            db,
            workspace_id=context.workspace.id,
            invited_by_user_id=context.actor.user.id,
            actor_role=context.membership.role,
            email=str(payload.email),
            role=WorkspaceRole(payload.role),
            expires_in_days=payload.expires_in_days,
            settings=settings,
        )
    except MemberServiceError as exc:
        _raise_service_error(exc)

    record_audit_event(
        db,
        event_type="member.invitation.created",
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace_invitation",
        target_id=issued.invitation.id,
        request_id=_request_id(request),
        details={"role": issued.invitation.role.value},
        settings=settings,
    )
    db.commit()
    db.refresh(issued.invitation)

    delivered = False
    try:
        delivered = await send_invitation_email(
            email=issued.invitation.email,
            raw_token=issued.raw_token,
            workspace_name=context.workspace.name,
            settings=settings,
        )
    except Exception:
        delivered = False
    if not delivered:
        revoke_invitation(
            db,
            workspace_id=context.workspace.id,
            invitation_id=issued.invitation.id,
        )
        record_audit_event(
            db,
            event_type="member.invitation.delivery_failed",
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type="workspace_invitation",
            target_id=issued.invitation.id,
            outcome="failure",
            request_id=_request_id(request),
            settings=settings,
        )
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "invitation_delivery_failed",
                "message": "The invitation could not be delivered",
            },
        )

    return _invitation_response(issued.invitation)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=204,
    response_class=Response,
)
def revoke_member_invitation(
    invitation_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEMBER_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        invitation = revoke_invitation(
            db,
            workspace_id=context.workspace.id,
            invitation_id=invitation_id,
        )
    except MemberServiceError as exc:
        _raise_service_error(exc)
    record_audit_event(
        db,
        event_type="member.invitation.revoked",
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace_invitation",
        target_id=invitation.id,
        request_id=_request_id(request),
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)


@router.post("/invitations/accept", response_model=InvitationAcceptedResponse)
def accept_member_invitation(
    payload: AcceptInvitationRequest,
    request: Request,
    actor: Actor = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InvitationAcceptedResponse:
    try:
        accepted = accept_invitation(
            db,
            raw_token=payload.token,
            user=actor.user,
            settings=settings,
        )
    except MemberServiceError as exc:
        _raise_service_error(exc)

    record_audit_event(
        db,
        event_type="member.invitation.accepted",
        actor_user_id=actor.user.id,
        workspace_id=accepted.workspace.id,
        target_type="workspace_invitation",
        target_id=accepted.invitation.id,
        request_id=_request_id(request),
        details={"role": accepted.membership.role.value},
        settings=settings,
    )
    db.commit()
    db.refresh(accepted.membership)
    return InvitationAcceptedResponse(
        workspace_id=accepted.workspace.id,
        workspace_name=accepted.workspace.name,
        membership=_member_response(
            MemberRecord(membership=accepted.membership, user=accepted.user)
        ),
    )


@router.patch("/{membership_id}", response_model=MemberResponse)
def update_member_role(
    membership_id: str,
    payload: ChangeMemberRoleRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEMBER_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MemberResponse:
    try:
        record, previous_role = change_member_role(
            db,
            workspace_id=context.workspace.id,
            membership_id=membership_id,
            actor_user_id=context.actor.user.id,
            actor_role=context.membership.role,
            new_role=WorkspaceRole(payload.role),
        )
    except MemberServiceError as exc:
        _raise_service_error(exc)
    record_audit_event(
        db,
        event_type="member.role_changed",
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="membership",
        target_id=record.membership.id,
        request_id=_request_id(request),
        details={"previous_role": previous_role.value, "new_role": record.membership.role.value},
        settings=settings,
    )
    db.commit()
    db.refresh(record.membership)
    return _member_response(record)


@router.delete(
    "/{membership_id}",
    status_code=204,
    response_class=Response,
)
def delete_member(
    membership_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEMBER_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        membership = remove_member(
            db,
            workspace_id=context.workspace.id,
            membership_id=membership_id,
            actor_user_id=context.actor.user.id,
            actor_role=context.membership.role,
        )
    except MemberServiceError as exc:
        _raise_service_error(exc)
    record_audit_event(
        db,
        event_type="member.removed",
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="membership",
        target_id=membership.id,
        request_id=_request_id(request),
        details={"previous_role": membership.role.value},
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)
