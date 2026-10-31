from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    Membership,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
)
from backend.database.tenant_context import (
    bind_capability_context,
    bind_tenant_context,
    bind_user_context,
)
from backend.security.authorization import can_grant, can_manage_member
from backend.security.normalization import normalize_email
from backend.security.tokens import random_token, token_digest
from backend.services.seat_service import consumes_seat, lock_seats, seat_available


class MemberServiceError(Exception):
    code = "member_operation_failed"
    public_message = "Member operation failed"


class MemberNotFoundError(MemberServiceError):
    code = "member_not_found"
    public_message = "Member not found"


class InvitationNotFoundError(MemberServiceError):
    code = "invitation_not_found"
    public_message = "Invitation not found"


class MemberConflictError(MemberServiceError):
    code = "member_conflict"
    public_message = "The requested membership conflicts with existing state"


class PendingInvitationError(MemberServiceError):
    code = "invitation_pending"
    public_message = "An active invitation already exists for this email"


class MemberOperationForbiddenError(MemberServiceError):
    code = "member_operation_forbidden"
    public_message = "This member operation is not permitted"


class InvitationIdentityMismatchError(MemberServiceError):
    code = "invitation_identity_mismatch"
    public_message = "The invitation does not belong to the authenticated account"


class InvitationExpiredError(MemberServiceError):
    code = "invitation_expired"
    public_message = "The invitation has expired"


class InvitationRevokedError(MemberServiceError):
    code = "invitation_revoked"
    public_message = "The invitation has been revoked"


class InvitationAcceptedError(MemberServiceError):
    code = "invitation_already_accepted"
    public_message = "The invitation has already been accepted"


class SeatLimitReachedError(MemberServiceError):
    code = "seat_limit_reached"
    public_message = "The workspace plan has no free admin seat"


@dataclass(frozen=True)
class MemberRecord:
    membership: Membership
    user: User


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: WorkspaceInvitation
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class AcceptedInvitation:
    membership: Membership
    user: User
    workspace: Workspace
    invitation: WorkspaceInvitation


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def invitation_status(
    invitation: WorkspaceInvitation,
    *,
    now: datetime | None = None,
) -> str:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    if _as_utc(invitation.expires_at) <= (now or datetime.now(UTC)):
        return "expired"
    return "pending"


def invitation_open_for_email(
    db: Session,
    *,
    raw_token: str,
    email: str,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    """Whether ``raw_token`` names an open invitation addressed to ``email``.

    The closed-registration gate on ``POST /auth/register`` calls this (HOM-336).
    That request has no signed-in user, so the presented token is the only
    capability that makes the invitation row visible under row-level security;
    it is bound for the lookup and cleared again right after.
    """
    now = now or datetime.now(UTC)
    digest = token_digest(f"workspace-invitation:{raw_token}", settings)
    bind_capability_context(db, invitation_token_hash=digest)
    try:
        invitation = db.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == digest)
        )
    finally:
        bind_capability_context(db, invitation_token_hash=None)
    if invitation is None:
        return False
    if invitation.accepted_at is not None:
        return False
    if invitation.revoked_at is not None:
        return False
    if _as_utc(invitation.expires_at) <= now:
        return False
    return invitation.email == normalize_email(email)


def list_workspace_members(db: Session, workspace_id: str) -> list[MemberRecord]:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.is_active.is_(True),
        )
        .order_by(Membership.created_at.asc(), Membership.id.asc())
    )
    return [MemberRecord(membership=membership, user=user) for membership, user in rows]


def list_workspace_invitations(
    db: Session,
    workspace_id: str,
) -> list[WorkspaceInvitation]:
    return list(
        db.scalars(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == workspace_id)
            .order_by(WorkspaceInvitation.created_at.desc(), WorkspaceInvitation.id.desc())
        )
    )


def create_invitation(
    db: Session,
    *,
    workspace_id: str,
    invited_by_user_id: str,
    actor_role: WorkspaceRole,
    email: str,
    role: WorkspaceRole,
    expires_in_days: int,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedInvitation:
    now = now or datetime.now(UTC)
    normalized_email = normalize_email(email)
    # GRANTABLE_ROLES: owner is never grantable, admins cannot mint admins.
    if not can_grant(actor_role, role):
        raise MemberOperationForbiddenError

    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.status == "active")
        .with_for_update()
    )
    if workspace is None:
        raise InvitationNotFoundError

    active_member = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.is_active.is_(True),
            User.email == normalized_email,
        )
    )
    if active_member is not None:
        raise MemberConflictError

    pending = db.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.email == normalized_email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at > now,
        )
    )
    if pending is not None:
        raise PendingInvitationError
    if consumes_seat(role) and not seat_available(
        db, workspace_id=workspace_id, include_pending_invitations=True, now=now
    ):
        raise SeatLimitReachedError

    raw_token = random_token(32)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        invited_by_user_id=invited_by_user_id,
        email=normalized_email,
        role=role,
        token_hash=token_digest(f"workspace-invitation:{raw_token}", settings),
        expires_at=now + timedelta(days=expires_in_days),
    )
    db.add(invitation)
    db.flush()
    return IssuedInvitation(invitation=invitation, raw_token=raw_token)


async def send_invitation_email(
    *,
    email: str,
    raw_token: str,
    workspace_name: str,
    settings: Settings,
) -> bool:
    from backend.services.email_service import send_email

    safe_workspace_name = " ".join(workspace_name.split())[:180] or "Homayar workspace"
    accept_url = f"{settings.frontend_url.rstrip('/')}/invitations/accept?" + urlencode(
        {"token": raw_token}
    )
    body = (
        f"You have been invited to join {safe_workspace_name} in Homayar.\n\n"
        f"Accept the invitation: {accept_url}\n\n"
        "This invitation is personal. Do not forward this message."
    )
    return bool(
        await send_email(
            email,
            f"Invitation to {safe_workspace_name}",
            body,
        )
    )


def _get_member_for_update(
    db: Session,
    *,
    workspace_id: str,
    membership_id: str,
) -> Membership:
    membership = db.scalar(
        select(Membership)
        .where(
            Membership.id == membership_id,
            Membership.workspace_id == workspace_id,
            Membership.is_active.is_(True),
        )
        .with_for_update()
    )
    if membership is None:
        raise MemberNotFoundError
    return membership


def _enforce_member_management_hierarchy(
    *,
    actor_user_id: str,
    actor_role: WorkspaceRole,
    target: Membership,
) -> None:
    if not can_manage_member(actor_user_id=actor_user_id, actor_role=actor_role, target=target):
        raise MemberOperationForbiddenError


def change_member_role(
    db: Session,
    *,
    workspace_id: str,
    membership_id: str,
    actor_user_id: str,
    actor_role: WorkspaceRole,
    new_role: WorkspaceRole,
) -> tuple[MemberRecord, WorkspaceRole]:
    # The caller must be allowed to grant the *new* role as well as to manage
    # the target's *current* role; otherwise an admin could escalate a member.
    if not can_grant(actor_role, new_role):
        raise MemberOperationForbiddenError
    if consumes_seat(new_role):
        lock_seats(db, workspace_id=workspace_id)
    target = _get_member_for_update(
        db,
        workspace_id=workspace_id,
        membership_id=membership_id,
    )
    _enforce_member_management_hierarchy(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target=target,
    )
    if consumes_seat(new_role, target) and not seat_available(
        db, workspace_id=workspace_id, include_pending_invitations=True
    ):
        raise SeatLimitReachedError

    previous_role = target.role
    target.role = new_role
    user = db.get(User, target.user_id)
    if user is None:
        raise MemberNotFoundError
    db.flush()
    return MemberRecord(membership=target, user=user), previous_role


def remove_member(
    db: Session,
    *,
    workspace_id: str,
    membership_id: str,
    actor_user_id: str,
    actor_role: WorkspaceRole,
) -> Membership:
    target = _get_member_for_update(
        db,
        workspace_id=workspace_id,
        membership_id=membership_id,
    )
    _enforce_member_management_hierarchy(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target=target,
    )
    target.is_active = False
    db.flush()
    return target


def revoke_invitation(
    db: Session,
    *,
    workspace_id: str,
    invitation_id: str,
    now: datetime | None = None,
) -> WorkspaceInvitation:
    invitation = db.scalar(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if invitation is None:
        raise InvitationNotFoundError
    if invitation.accepted_at is not None:
        raise InvitationAcceptedError
    if invitation.revoked_at is None:
        invitation.revoked_at = now or datetime.now(UTC)
        db.flush()
    return invitation


def accept_invitation(
    db: Session,
    *,
    raw_token: str,
    user: User,
    settings: Settings,
    now: datetime | None = None,
) -> AcceptedInvitation:
    now = now or datetime.now(UTC)
    digest = token_digest(f"workspace-invitation:{raw_token}", settings)
    # The presented token is the only capability that makes this invitation
    # row visible under row-level security; nothing else in the workspace is.
    bind_user_context(db, user.id)
    bind_capability_context(db, invitation_token_hash=digest)
    invitation = db.scalar(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.token_hash == digest)
        .with_for_update()
    )
    if invitation is None:
        raise InvitationNotFoundError
    if invitation.accepted_at is not None:
        raise InvitationAcceptedError
    if invitation.revoked_at is not None:
        raise InvitationRevokedError
    if _as_utc(invitation.expires_at) <= now:
        raise InvitationExpiredError

    if user.email is None or user.email_verified_at is None:
        raise InvitationIdentityMismatchError
    if normalize_email(user.email) != invitation.email:
        raise InvitationIdentityMismatchError

    # Validated: the caller is the invitee. Scope the rest of the transaction
    # to the inviting workspace so the membership row can be written.
    bind_tenant_context(db, workspace_id=invitation.workspace_id)
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == invitation.workspace_id,
            Workspace.status == "active",
        )
    )
    if workspace is None:
        raise InvitationNotFoundError
    if consumes_seat(invitation.role):
        lock_seats(db, workspace_id=invitation.workspace_id)

    membership = db.scalar(
        select(Membership)
        .where(
            Membership.workspace_id == invitation.workspace_id,
            Membership.user_id == user.id,
        )
        .with_for_update()
    )
    # An owner membership is never changed below, so it takes no new seat.
    owner_kept = membership is not None and membership.role == WorkspaceRole.OWNER
    if (
        not owner_kept
        and consumes_seat(invitation.role, membership, now=now)
        and not seat_available(
            db, workspace_id=invitation.workspace_id, include_pending_invitations=False, now=now
        )
    ):
        raise SeatLimitReachedError
    if membership is None:
        membership = Membership(
            workspace_id=invitation.workspace_id,
            user_id=user.id,
            role=invitation.role,
            is_active=True,
        )
        db.add(membership)
    elif membership.role != WorkspaceRole.OWNER:
        membership.role = invitation.role
        membership.is_active = True

    invitation.accepted_at = now
    db.flush()
    return AcceptedInvitation(
        membership=membership,
        user=user,
        workspace=workspace,
        invitation=invitation,
    )
