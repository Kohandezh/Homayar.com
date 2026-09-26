"""Organization membership, delegated administration and scoped analytics (WP03-T03).

Rules (ADR 0012, ``docs/security/authorization-matrix.md`` §1a–§4a):

* An organization role never confers workspace **content** access.  Meetings,
  transcripts, exports and shares stay behind an effective *workspace*
  membership and the workspace's record scope.
* Owners/admins administer the organization: memberships, workspaces, and
  *delegated administration* — installing an organization member (themselves
  included) in a workspace of the organization with an explicit, audited
  workspace role.  That row is an ordinary ``Membership``; every existing
  workspace policy and RLS rule applies to it unchanged.
* Analysts and executives see only what an owner/admin **granted** them:
  a workspace, optionally narrowed to one team.  Executives get aggregates,
  analysts additionally record metadata.  Neither sees transcript text.
* Every check reads current server-side state; a revoked or expired
  organization membership loses authority on the next request.
* Nothing here creates trial entitlements, moves wallets or usage, or merges
  tenants: a workspace created inside an organization starts with no
  entitlement at all.

Every function that touches workspace-scoped tables binds the tenant context
to that one workspace first (row-level security) and only after the caller's
organization authority over it was established.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    ActionItem,
    Meeting,
    Membership,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    OrganizationScopeGrant,
    Team,
    User,
    Workspace,
    WorkspaceRole,
    new_id,
)
from backend.database.tenant_context import bind_tenant_context
from backend.security.authorization import (
    SCOPED_ORGANIZATION_ROLES,
    OrganizationAccess,
    can_delegate_workspace_role,
    can_grant_organization_role,
    can_manage_organization_member,
    membership_effective_clause,
    organization_membership_effective_clause,
)
from backend.security.normalization import normalize_workspace_slug

RECORDS_LIMIT = 200


class OrganizationServiceError(Exception):
    status_code = 400
    code = "organization_error"
    public_message = "Organization request could not be completed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        if message:
            self.public_message = message


class OrganizationMemberNotFoundError(OrganizationServiceError):
    status_code = 404
    code = "organization_member_not_found"
    public_message = "Organization member not found"


class OrganizationMemberConflictError(OrganizationServiceError):
    status_code = 409
    code = "organization_member_exists"
    public_message = "The user already holds an effective organization role"


class OrganizationGrantDeniedError(OrganizationServiceError):
    status_code = 403
    code = "organization_grant_denied"
    public_message = "You may not grant or manage this organization role"


class OrganizationUserNotFoundError(OrganizationServiceError):
    status_code = 422
    code = "invalid_user"
    public_message = "No active account with a verified email matches this address"


class OrganizationWorkspaceNotFoundError(OrganizationServiceError):
    status_code = 404
    code = "workspace_not_found"
    public_message = "Workspace not found in this organization"


class DelegationDeniedError(OrganizationServiceError):
    status_code = 403
    code = "delegation_denied"
    public_message = "You may not install this workspace role"


class ScopeGrantConflictError(OrganizationServiceError):
    status_code = 409
    code = "scope_grant_exists"
    public_message = "An identical scope grant is already active"


class ScopeGrantNotFoundError(OrganizationServiceError):
    status_code = 404
    code = "scope_grant_not_found"
    public_message = "Scope grant not found"


class ScopeTargetInvalidError(OrganizationServiceError):
    status_code = 422
    code = "invalid_scope_target"
    public_message = "Scope grants apply to analysts and executives of this organization"


class InvalidTeamError(OrganizationServiceError):
    status_code = 422
    code = "invalid_team"
    public_message = "The team does not belong to this workspace or is inactive"


class InvalidWindowError(OrganizationServiceError):
    status_code = 422
    code = "invalid_validity_window"
    public_message = "valid_until must be later than valid_from"


class WorkspaceMemberNotFoundError(OrganizationServiceError):
    status_code = 404
    code = "member_not_found"
    public_message = "Workspace member not found"


@dataclass(frozen=True)
class OrganizationMemberRecord:
    membership: OrganizationMembership
    user: User


@dataclass(frozen=True)
class WorkspaceAggregate:
    workspace: Workspace
    team_ids: frozenset[str] | None
    meetings_total: int
    meetings_by_status: dict[str, int]
    audio_seconds_total: int
    action_items_total: int


@dataclass(frozen=True)
class ScopedRecord:
    workspace_id: str
    meeting: Meeting


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_effective(membership: OrganizationMembership, *, now: datetime) -> bool:
    return (
        membership.is_active
        and membership.revoked_at is None
        and _as_utc(membership.valid_from) <= now
        and (membership.valid_until is None or _as_utc(membership.valid_until) > now)
    )


def _validate_window(valid_from: datetime, valid_until: datetime | None) -> None:
    if valid_until is not None and _as_utc(valid_until) <= _as_utc(valid_from):
        raise InvalidWindowError()


# --- memberships ------------------------------------------------------------------


def list_user_organizations(
    db: Session, *, user_id: str, now: datetime | None = None
) -> list[tuple[Organization, OrganizationMembership]]:
    """Organizations where the user holds an effective membership."""
    now = now or datetime.now(UTC)
    rows = db.execute(
        select(Organization, OrganizationMembership)
        .join(
            OrganizationMembership,
            OrganizationMembership.organization_id == Organization.id,
        )
        .where(
            OrganizationMembership.user_id == user_id,
            organization_membership_effective_clause(now),
            Organization.status == "active",
        )
        .order_by(OrganizationMembership.created_at.asc(), Organization.id.asc())
    ).all()
    return [(organization, membership) for organization, membership in rows]


def list_members(db: Session, *, organization_id: str) -> list[OrganizationMemberRecord]:
    rows = db.execute(
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == organization_id)
        .order_by(OrganizationMembership.created_at.asc(), OrganizationMembership.id.asc())
    ).all()
    return [OrganizationMemberRecord(membership=m, user=u) for m, u in rows]


def get_member(
    db: Session, *, organization_id: str, membership_id: str
) -> OrganizationMemberRecord:
    row = db.execute(
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.id == membership_id,
        )
    ).first()
    if row is None:
        raise OrganizationMemberNotFoundError()
    membership, user = row
    return OrganizationMemberRecord(membership=membership, user=user)


def bootstrap_owner_membership(
    db: Session, *, organization: Organization, user: User, now: datetime | None = None
) -> OrganizationMembership:
    """The creator of an organization is its first owner (tenancy bootstrap)."""
    now = now or datetime.now(UTC)
    membership = OrganizationMembership(
        id=new_id(),
        organization_id=organization.id,
        user_id=user.id,
        role=OrganizationRole.OWNER,
        is_active=True,
        valid_from=now,
        granted_by_user_id=user.id,
    )
    db.add(membership)
    db.flush()
    return membership


def grant_membership(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    email: str,
    role: OrganizationRole,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    now: datetime | None = None,
) -> OrganizationMemberRecord:
    """Grant ``role`` to an existing account identified by its verified email.

    No invitation flow: organization roles are administrative and go to
    people who already hold an account; workspace invitations remain the way
    to bring a new person in.  A revoked or expired row is re-granted in place.
    """
    now = now or datetime.now(UTC)
    if not can_grant_organization_role(actor.role, role):
        raise OrganizationGrantDeniedError()
    valid_from = _as_utc(valid_from) if valid_from is not None else now
    _validate_window(valid_from, valid_until)
    normalized = email.strip().casefold()
    user = db.scalar(
        select(User).where(
            func.lower(User.email) == normalized,
            User.is_active.is_(True),
            User.email_verified_at.is_not(None),
        )
    )
    if user is None:
        raise OrganizationUserNotFoundError()
    existing = db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing is not None:
        if is_effective(existing, now=now) or existing.role == OrganizationRole.OWNER:
            raise OrganizationMemberConflictError()
        existing.role = role
        existing.is_active = True
        existing.revoked_at = None
        existing.valid_from = valid_from
        existing.valid_until = valid_until
        existing.granted_by_user_id = actor.user_id
        db.flush()
        return OrganizationMemberRecord(membership=existing, user=user)
    membership = OrganizationMembership(
        id=new_id(),
        organization_id=organization.id,
        user_id=user.id,
        role=role,
        is_active=True,
        valid_from=valid_from,
        valid_until=valid_until,
        granted_by_user_id=actor.user_id,
    )
    db.add(membership)
    db.flush()
    return OrganizationMemberRecord(membership=membership, user=user)


def _target_for_management(
    db: Session, *, organization_id: str, actor: OrganizationMembership, membership_id: str
) -> OrganizationMemberRecord:
    record = get_member(db, organization_id=organization_id, membership_id=membership_id)
    if not can_manage_organization_member(
        actor_user_id=actor.user_id, actor_role=actor.role, target=record.membership
    ):
        raise OrganizationGrantDeniedError()
    return record


def change_membership(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    membership_id: str,
    role: OrganizationRole | None = None,
    valid_until: datetime | None = None,
    clear_valid_until: bool = False,
) -> OrganizationMemberRecord:
    record = _target_for_management(
        db, organization_id=organization.id, actor=actor, membership_id=membership_id
    )
    target = record.membership
    if role is not None:
        if not can_grant_organization_role(actor.role, role):
            raise OrganizationGrantDeniedError()
        target.role = role
        if role not in SCOPED_ORGANIZATION_ROLES:
            _revoke_scope_grants(db, membership=target, now=datetime.now(UTC))
    if clear_valid_until:
        target.valid_until = None
    elif valid_until is not None:
        _validate_window(target.valid_from, valid_until)
        target.valid_until = valid_until
    db.flush()
    return record


def revoke_membership(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    membership_id: str,
    now: datetime | None = None,
) -> OrganizationMemberRecord:
    now = now or datetime.now(UTC)
    record = _target_for_management(
        db, organization_id=organization.id, actor=actor, membership_id=membership_id
    )
    target = record.membership
    target.is_active = False
    target.revoked_at = now
    _revoke_scope_grants(db, membership=target, now=now)
    db.flush()
    return record


# --- workspaces -------------------------------------------------------------------


def list_organization_workspaces(
    db: Session, *, organization: Organization, access: OrganizationAccess
) -> list[Workspace]:
    """Workspaces of the organization the caller may know about.

    Owners/admins: all.  Analysts/executives: only the workspaces of their
    active scope grants.  (Row-level security enforces the same boundary.)
    """
    query = select(Workspace).where(Workspace.organization_id == organization.id)
    if not access.unrestricted:
        visible = sorted(access.scopes)
        if not visible:
            return []
        query = query.where(Workspace.id.in_(visible))
    return list(db.scalars(query.order_by(Workspace.created_at.asc(), Workspace.id.asc())))


def get_organization_workspace(
    db: Session, *, organization: Organization, workspace_id: str
) -> Workspace:
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == organization.id,
        )
    )
    if workspace is None:
        raise OrganizationWorkspaceNotFoundError()
    return workspace


def workspace_roles_for_user(
    db: Session, *, workspace_ids: Iterable[str], user_id: str, now: datetime
) -> dict[str, WorkspaceRole]:
    ids = sorted(set(workspace_ids))
    if not ids:
        return {}
    rows = db.execute(
        select(Membership.workspace_id, Membership.role).where(
            Membership.workspace_id.in_(ids),
            Membership.user_id == user_id,
            membership_effective_clause(now),
        )
    ).all()
    return {workspace_id: role for workspace_id, role in rows}


def _unique_workspace_slug(db: Session, *, organization_id: str, name: str) -> str:
    base = normalize_workspace_slug(name)
    taken = set(
        db.scalars(
            select(Workspace.slug).where(
                Workspace.organization_id == organization_id,
                Workspace.slug.like(f"{base}%"),
            )
        )
    )
    if base not in taken:
        return base
    for index in range(2, 1000):
        candidate = f"{base[:76]}-{index}"
        if candidate not in taken:
            return candidate
    return f"{base[:72]}-{new_id()[:7]}"


def create_organization_workspace(
    db: Session,
    *,
    organization: Organization,
    owner: User,
    name: str,
    settings: Settings | None = None,
) -> Workspace:
    """A new workspace inside the organization; the creator is its owner.

    Unlike the personal workspace created at registration this grants **no**
    demo subscription or entitlement: organization billing allocates quota to
    workspaces explicitly (blueprint §12).  Until such an allocation exists the
    workspace cannot accept uploads.  Record scope starts as ``workspace``.
    """
    settings = settings or get_settings()
    workspace_name = name.strip()[:180]
    slug = _unique_workspace_slug(db, organization_id=organization.id, name=workspace_name)
    workspace = Workspace(
        id=new_id(),
        organization_id=organization.id,
        name=workspace_name,
        slug=slug,
        status="active",
        retention_days=settings.retention_days_default,
    )
    # The new tenant's id is generated here, so the transaction can be scoped
    # to it before the first row is written (row-level security WITH CHECK).
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=owner.id, role=WorkspaceRole.OWNER))
    db.flush()
    return workspace


def _effective_organization_member(
    db: Session, *, organization_id: str, user_id: str, now: datetime
) -> OrganizationMembership | None:
    return db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
            organization_membership_effective_clause(now),
        )
    )


def install_workspace_member(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    workspace_id: str,
    user_id: str,
    role: WorkspaceRole,
    now: datetime | None = None,
) -> Membership:
    """Delegated administration: give an organization member a workspace role.

    The target must hold an effective membership of *this* organization (the
    actor included); the role must be one the actor may delegate; an existing
    ``owner`` membership is never touched.  The resulting row is an ordinary
    workspace membership — visible, manageable and revocable through the
    workspace member routes and subject to record scope like any other.
    """
    now = now or datetime.now(UTC)
    if not can_delegate_workspace_role(actor.role, role):
        raise DelegationDeniedError()
    workspace = get_organization_workspace(db, organization=organization, workspace_id=workspace_id)
    if (
        _effective_organization_member(
            db, organization_id=organization.id, user_id=user_id, now=now
        )
        is None
    ):
        raise OrganizationMemberNotFoundError()
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
    existing = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id, Membership.user_id == user_id
        )
    )
    if existing is not None:
        if existing.role == WorkspaceRole.OWNER:
            raise DelegationDeniedError("The workspace owner cannot be changed this way")
        existing.role = role
        existing.is_active = True
        existing.valid_from = now
        existing.valid_until = None
        db.flush()
        return existing
    membership = Membership(
        workspace_id=workspace.id,
        user_id=user_id,
        role=role,
        is_active=True,
        valid_from=now,
    )
    db.add(membership)
    db.flush()
    return membership


def remove_workspace_member(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    workspace_id: str,
    membership_id: str,
) -> Membership:
    """Withdraw a delegated workspace role (never the owner's)."""
    workspace = get_organization_workspace(db, organization=organization, workspace_id=workspace_id)
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
    membership = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id, Membership.id == membership_id
        )
    )
    if membership is None:
        raise WorkspaceMemberNotFoundError()
    if membership.role == WorkspaceRole.OWNER or not can_delegate_workspace_role(
        actor.role, membership.role
    ):
        raise DelegationDeniedError()
    membership.is_active = False
    db.flush()
    return membership


# --- scope grants -----------------------------------------------------------------


def list_scope_grants(db: Session, *, organization_id: str) -> list[OrganizationScopeGrant]:
    return list(
        db.scalars(
            select(OrganizationScopeGrant)
            .where(OrganizationScopeGrant.organization_id == organization_id)
            .order_by(OrganizationScopeGrant.created_at.asc(), OrganizationScopeGrant.id.asc())
        )
    )


def grant_scope(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    membership_id: str,
    workspace_id: str,
    team_id: str | None,
    now: datetime | None = None,
) -> OrganizationScopeGrant:
    """Authorize an analyst/executive for one workspace (optionally one team)."""
    now = now or datetime.now(UTC)
    record = get_member(db, organization_id=organization.id, membership_id=membership_id)
    target = record.membership
    if target.role not in SCOPED_ORGANIZATION_ROLES or not is_effective(target, now=now):
        raise ScopeTargetInvalidError()
    if not can_grant_organization_role(actor.role, target.role):
        raise OrganizationGrantDeniedError()
    workspace = get_organization_workspace(db, organization=organization, workspace_id=workspace_id)
    if team_id is not None:
        bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
        team = db.scalar(
            select(Team).where(
                Team.id == team_id, Team.workspace_id == workspace.id, Team.is_active.is_(True)
            )
        )
        if team is None:
            raise InvalidTeamError()
    duplicate = db.scalar(
        select(OrganizationScopeGrant.id).where(
            OrganizationScopeGrant.membership_id == target.id,
            OrganizationScopeGrant.workspace_id == workspace.id,
            OrganizationScopeGrant.team_id.is_(None)
            if team_id is None
            else OrganizationScopeGrant.team_id == team_id,
            OrganizationScopeGrant.revoked_at.is_(None),
        )
    )
    if duplicate is not None:
        raise ScopeGrantConflictError()
    grant = OrganizationScopeGrant(
        id=new_id(),
        organization_id=organization.id,
        membership_id=target.id,
        workspace_id=workspace.id,
        team_id=team_id,
        granted_by_user_id=actor.user_id,
    )
    db.add(grant)
    db.flush()
    return grant


def revoke_scope(
    db: Session,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    grant_id: str,
    now: datetime | None = None,
) -> OrganizationScopeGrant:
    now = now or datetime.now(UTC)
    grant = db.scalar(
        select(OrganizationScopeGrant).where(
            OrganizationScopeGrant.organization_id == organization.id,
            OrganizationScopeGrant.id == grant_id,
        )
    )
    if grant is None:
        raise ScopeGrantNotFoundError()
    target = db.get(OrganizationMembership, grant.membership_id)
    if target is not None and not can_grant_organization_role(actor.role, target.role):
        raise OrganizationGrantDeniedError()
    if grant.revoked_at is None:
        grant.revoked_at = now
        db.flush()
    return grant


def _revoke_scope_grants(db: Session, *, membership: OrganizationMembership, now: datetime) -> None:
    for grant in db.scalars(
        select(OrganizationScopeGrant).where(
            OrganizationScopeGrant.membership_id == membership.id,
            OrganizationScopeGrant.revoked_at.is_(None),
        )
    ):
        grant.revoked_at = now


# --- scoped analytics ---------------------------------------------------------------


def _scoped_workspaces(
    db: Session, *, organization: Organization, access: OrganizationAccess
) -> list[tuple[Workspace, frozenset[str] | None]]:
    """Authorized workspaces with their team filter; inactive teams drop out.

    Binding the workspace as the tenant here is what makes its team rows
    visible; a team-restricted scope whose teams are all inactive yields no
    visible scope at all.
    """
    scoped: list[tuple[Workspace, frozenset[str] | None]] = []
    for workspace in list_organization_workspaces(db, organization=organization, access=access):
        team_ids = access.team_filter(workspace.id)
        if team_ids is not None:
            bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
            active = frozenset(
                db.scalars(
                    select(Team.id).where(
                        Team.workspace_id == workspace.id,
                        Team.id.in_(sorted(team_ids)),
                        Team.is_active.is_(True),
                    )
                )
            )
            if not active:
                continue
            team_ids = active
        scoped.append((workspace, team_ids))
    return scoped


def workspace_aggregates(
    db: Session,
    *,
    organization: Organization,
    access: OrganizationAccess,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[WorkspaceAggregate]:
    """Counts and durations per authorized workspace (and team filter): no content.

    Each workspace is bound as the tenant in turn, so the queries run under
    that workspace's row-level security; a workspace outside the caller's
    scope is never bound.
    """
    results: list[WorkspaceAggregate] = []
    for workspace, team_ids in _scoped_workspaces(db, organization=organization, access=access):
        bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
        conditions = [Meeting.workspace_id == workspace.id, Meeting.deleted_at.is_(None)]
        if team_ids is not None:
            conditions.append(Meeting.team_id.in_(sorted(team_ids)))
        if since is not None:
            conditions.append(Meeting.created_at >= since)
        if until is not None:
            conditions.append(Meeting.created_at < until)
        rows = db.execute(
            select(Meeting.status, func.count(Meeting.id), func.sum(Meeting.duration_seconds))
            .where(*conditions)
            .group_by(Meeting.status)
        ).all()
        by_status = {status.value: int(count) for status, count, _ in rows}
        audio_seconds = int(sum(int(seconds or 0) for _, _, seconds in rows))
        actions = db.scalar(
            select(func.count(ActionItem.id))
            .join(Meeting, Meeting.id == ActionItem.meeting_id)
            .where(ActionItem.workspace_id == workspace.id, *conditions)
        )
        results.append(
            WorkspaceAggregate(
                workspace=workspace,
                team_ids=team_ids,
                meetings_total=sum(by_status.values()),
                meetings_by_status=by_status,
                audio_seconds_total=audio_seconds,
                action_items_total=int(actions or 0),
            )
        )
    return results


def scoped_records(
    db: Session,
    *,
    organization: Organization,
    access: OrganizationAccess,
    limit: int = RECORDS_LIMIT,
) -> tuple[list[ScopedRecord], bool]:
    """Meeting metadata (never transcript text) inside the caller's scope."""
    records: list[ScopedRecord] = []
    truncated = False
    for workspace, team_ids in _scoped_workspaces(db, organization=organization, access=access):
        bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
        query = select(Meeting).where(
            Meeting.workspace_id == workspace.id, Meeting.deleted_at.is_(None)
        )
        if team_ids is not None:
            query = query.where(Meeting.team_id.in_(sorted(team_ids)))
        remaining = limit - len(records)
        if remaining <= 0:
            truncated = True
            break
        meetings = list(
            db.scalars(
                query.order_by(Meeting.created_at.desc(), Meeting.id.asc()).limit(remaining + 1)
            )
        )
        if len(meetings) > remaining:
            truncated = True
            meetings = meetings[:remaining]
        records.extend(ScopedRecord(workspace_id=workspace.id, meeting=m) for m in meetings)
    return records, truncated
