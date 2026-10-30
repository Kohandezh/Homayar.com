"""Authorization policy: roles, permissions, grant hierarchy and record scope.

This module is the single source of truth for *who may do what*.  The human
readable matrix lives in ``docs/security/authorization-matrix.md`` and a test
(``backend/tests/test_authorization_matrix.py``) fails whenever the document and
this module disagree or when a route is mounted without an explicit policy.

Scopes, from widest to narrowest:

* ``platform`` – ``User.system_role == "operator"``.  It never grants anything
  alone.  The voice node routes (register, heartbeat, drain, recover) require
  it together with ``workspace:manage``, from a signed-in session and never an
  API key (``require_platform_operator``).  ``GET /voice/capacity`` adds
  deployment data for such a caller.  Every other route ignores it (deny by
  default).
* ``organization`` – an ``OrganizationMembership`` that is active, unrevoked
  and inside its validity window (WP03-T03, ADR 0012).  Its role maps to a
  fixed set of *organization* permissions (``ORGANIZATION_ROLE_PERMISSIONS``):
  administration of memberships and workspaces, delegated installation of
  workspace roles, and scoped aggregates/metadata for analysts and
  executives.  It never confers workspace content access by itself.
* ``workspace`` – a ``Membership`` that is active *and* inside its validity
  window (``valid_from`` ≤ now < ``valid_until``).  The membership role maps to
  a fixed set of permissions (``ROLE_PERMISSIONS``); nothing else grants them.
* ``record`` – inside a workspace whose ``record_scope`` is ``team``, members
  and viewers only see meetings of the teams they supervise or the meetings
  attributed to them (agent or uploader).  Owners and admins are unrestricted.
* ``service`` – background workers act on a persisted job and are confined to
  ``job.workspace_id``; they never resolve a user context.
* ``api key`` – a workspace-owned key bound to the user who created it
  (WP03-T07, ADR 0013).  It acts as that user in that one workspace with
  ``scopes ∩ API_KEY_SCOPES ∩ current membership permissions``; it never
  reaches organization, session or key-management routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import ColumnElement, and_, or_, select, true
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Meeting,
    Membership,
    OrganizationMembership,
    OrganizationRole,
    OrganizationScopeGrant,
    RecordScope,
    Team,
    TeamAssignment,
    TeamRole,
    Workspace,
    WorkspaceRole,
)


class Permission(str, Enum):
    MEETING_READ = "meeting:read"
    MEETING_CREATE = "meeting:create"
    MEETING_UPDATE = "meeting:update"
    MEETING_DELETE = "meeting:delete"
    SHARE_MANAGE = "share:manage"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    TEAM_READ = "team:read"
    TEAM_MANAGE = "team:manage"
    WORKSPACE_MANAGE = "workspace:manage"
    BILLING_READ = "billing:read"
    BILLING_MANAGE = "billing:manage"
    AUDIT_READ = "audit:read"
    API_KEY_MANAGE = "api_key:manage"
    # Compliance action, separate from ordinary deletion preferences (WP07-T02):
    # owner only, never carried by an API key.
    LEGAL_HOLD_MANAGE = "legal_hold:manage"
    # Text privacy (WP07-T06, ADR 0024): read original transcripts under an
    # enforced redaction policy, reveal a redacted value and change the policy.
    # Owner only, never carried by an API key.
    PII_ORIGINAL_ACCESS = "pii:original_access"
    # AI processing policy (WP08-T02, ADR 0027): where the workspace's audio
    # and text may be processed.  Owner only, never carried by an API key.
    AI_POLICY_MANAGE = "ai_policy:manage"
    # QA scorecards (HOM-54): every role reads the published versions; owners
    # and admins publish a new version.  Never carried by an API key.
    SCORECARD_READ = "scorecard:read"
    SCORECARD_MANAGE = "scorecard:manage"
    # Outbound webhooks (HOM-89, ADR 0049): add, change and rotate endpoints.
    # Owner and admin, never carried by an API key: a key that could add an
    # endpoint could copy every future event out of the workspace.
    WEBHOOK_MANAGE = "webhook:manage"


ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.OWNER: frozenset(Permission),
    WorkspaceRole.ADMIN: frozenset(
        {
            Permission.MEETING_READ,
            Permission.MEETING_CREATE,
            Permission.MEETING_UPDATE,
            Permission.MEETING_DELETE,
            Permission.SHARE_MANAGE,
            Permission.MEMBER_READ,
            Permission.MEMBER_MANAGE,
            Permission.TEAM_READ,
            Permission.TEAM_MANAGE,
            Permission.WORKSPACE_MANAGE,
            Permission.BILLING_READ,
            Permission.AUDIT_READ,
            Permission.API_KEY_MANAGE,
            Permission.SCORECARD_READ,
            Permission.SCORECARD_MANAGE,
            Permission.WEBHOOK_MANAGE,
        }
    ),
    WorkspaceRole.MEMBER: frozenset(
        {
            Permission.MEETING_READ,
            Permission.MEETING_CREATE,
            Permission.MEETING_UPDATE,
            Permission.SHARE_MANAGE,
            Permission.MEMBER_READ,
            Permission.TEAM_READ,
            Permission.SCORECARD_READ,
        }
    ),
    WorkspaceRole.VIEWER: frozenset(
        {
            Permission.MEETING_READ,
            Permission.MEMBER_READ,
            Permission.TEAM_READ,
            Permission.SCORECARD_READ,
        }
    ),
}

# Which workspace roles a caller may grant (invite, promote, demote) or manage
# (change, remove).  Nobody grants OWNER through the API; admins never touch
# other admins.  Deny by default: roles missing here grant nothing.
GRANTABLE_ROLES: dict[WorkspaceRole, frozenset[WorkspaceRole]] = {
    WorkspaceRole.OWNER: frozenset(
        {WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER}
    ),
    WorkspaceRole.ADMIN: frozenset({WorkspaceRole.MEMBER, WorkspaceRole.VIEWER}),
    WorkspaceRole.MEMBER: frozenset(),
    WorkspaceRole.VIEWER: frozenset(),
}

# Workspace roles that are never subject to record scoping.
UNRESTRICTED_ROLES: frozenset[WorkspaceRole] = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})

# Permissions an API key may carry (WP03-T07, ADR 0013).  A key acts as the
# user it is bound to with the intersection of these scopes, the scopes granted
# at creation and that user's *current* membership permissions.  Administration
# (members, teams, workspace settings, billing management, shares, deletion,
# key management) stays session-only: keys cannot mint or widen keys, and the
# conservative interim rule keeps every unlisted permission away from
# automation until a task decides otherwise.
API_KEY_SCOPES: frozenset[Permission] = frozenset(
    {
        Permission.MEETING_READ,
        Permission.MEETING_CREATE,
        Permission.MEETING_UPDATE,
        Permission.MEMBER_READ,
        Permission.TEAM_READ,
        Permission.BILLING_READ,
        Permission.AUDIT_READ,
    }
)


def role_has(role: WorkspaceRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def role_permission_values(role: WorkspaceRole) -> list[str]:
    """The permission strings of ``role``, sorted, as the session lists them (HOM-184)."""
    return sorted(permission.value for permission in ROLE_PERMISSIONS.get(role, frozenset()))


def can_grant(actor_role: WorkspaceRole, target_role: WorkspaceRole) -> bool:
    return target_role in GRANTABLE_ROLES.get(actor_role, frozenset())


def can_manage_member(
    *, actor_user_id: str, actor_role: WorkspaceRole, target: Membership
) -> bool:
    """A caller manages a membership only if it could have granted its role.

    Self-management and owner memberships are always refused so a workspace
    cannot lose its last owner or an admin cannot lock out the owner.
    """
    if target.user_id == actor_user_id:
        return False
    return can_grant(actor_role, target.role)


def membership_effective_clause(now: datetime) -> ColumnElement[bool]:
    """SQL predicate: membership is active and inside its validity window."""
    return and_(
        Membership.is_active.is_(True),
        Membership.valid_from <= now,
        or_(Membership.valid_until.is_(None), Membership.valid_until > now),
    )


def selectable_workspace_clause(now: datetime) -> ColumnElement[bool]:
    """SQL predicate over ``Workspace`` joined with ``Membership``: the workspace
    a request may select right now.  ``require_workspace`` authorizes with it and
    the session's workspace list (``/auth/me``, login, refresh, OTP) is built
    from it, so the console is never offered a workspace every request refuses.
    """
    return and_(membership_effective_clause(now), Workspace.status == "active")


def assignment_effective_clause(now: datetime) -> ColumnElement[bool]:
    return and_(
        TeamAssignment.revoked_at.is_(None),
        TeamAssignment.valid_from <= now,
        or_(TeamAssignment.valid_until.is_(None), TeamAssignment.valid_until > now),
    )


def effective_assignments(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    now: datetime | None = None,
) -> list[TeamAssignment]:
    """Assignments that currently confer access, joined to active teams only."""
    now = now or datetime.now(UTC)
    return list(
        db.scalars(
            select(TeamAssignment)
            .join(Team, Team.id == TeamAssignment.team_id)
            .where(
                TeamAssignment.workspace_id == workspace_id,
                TeamAssignment.user_id == user_id,
                Team.workspace_id == workspace_id,
                Team.is_active.is_(True),
                assignment_effective_clause(now),
            )
            .order_by(TeamAssignment.created_at.asc(), TeamAssignment.id.asc())
        )
    )


def supervised_team_ids(
    db: Session, *, workspace_id: str, user_id: str, now: datetime | None = None
) -> frozenset[str]:
    """Active teams where the user holds an effective ``supervisor`` assignment, whatever
    the workspace's record scope (HOM-82 team comparisons)."""
    return frozenset(
        assignment.team_id
        for assignment in effective_assignments(
            db, workspace_id=workspace_id, user_id=user_id, now=now
        )
        if assignment.role == TeamRole.SUPERVISOR
    )


@dataclass(frozen=True)
class RecordAccess:
    """Resolved record-level visibility for one actor inside one workspace."""

    user_id: str
    unrestricted: bool
    supervised_team_ids: frozenset[str]
    agent_team_ids: frozenset[str]

    @property
    def restricted(self) -> bool:
        return not self.unrestricted

    def meeting_clause(self) -> ColumnElement[bool]:
        """Predicate limiting a ``Meeting`` query to what this actor may see."""
        if self.unrestricted:
            return true()
        criteria: list[ColumnElement[bool]] = [
            Meeting.agent_user_id == self.user_id,
            Meeting.created_by_user_id == self.user_id,
        ]
        if self.supervised_team_ids:
            criteria.append(Meeting.team_id.in_(sorted(self.supervised_team_ids)))
        return or_(*criteria)

    def may_assign_team(self, team_id: str | None) -> bool:
        """May this actor attribute a record to ``team_id``?"""
        if self.unrestricted or team_id is None:
            return True
        return team_id in self.supervised_team_ids


def resolve_record_access(
    db: Session,
    *,
    workspace: Workspace,
    membership: Membership,
    now: datetime | None = None,
) -> RecordAccess:
    user_id = membership.user_id
    if membership.role in UNRESTRICTED_ROLES or workspace.record_scope == RecordScope.WORKSPACE:
        return RecordAccess(
            user_id=user_id,
            unrestricted=True,
            supervised_team_ids=frozenset(),
            agent_team_ids=frozenset(),
        )
    assignments = effective_assignments(
        db, workspace_id=workspace.id, user_id=user_id, now=now
    )
    supervised = frozenset(a.team_id for a in assignments if a.role == TeamRole.SUPERVISOR)
    agent = frozenset(a.team_id for a in assignments if a.role == TeamRole.AGENT)
    return RecordAccess(
        user_id=user_id,
        unrestricted=False,
        supervised_team_ids=supervised,
        agent_team_ids=agent,
    )



# --- organization scope (WP03-T03, ADR 0012) --------------------------------------


class OrganizationPermission(str, Enum):
    ORGANIZATION_READ = "organization:read"
    MEMBER_READ = "organization:member:read"
    MEMBER_MANAGE = "organization:member:manage"
    WORKSPACE_READ = "organization:workspace:read"
    WORKSPACE_CREATE = "organization:workspace:create"
    WORKSPACE_ADMINISTER = "organization:workspace:administer"
    ANALYTICS_READ = "organization:analytics:read"
    RECORDS_READ = "organization:records:read"


_ORGANIZATION_ADMINISTRATION: frozenset[OrganizationPermission] = frozenset(OrganizationPermission)

ORGANIZATION_ROLE_PERMISSIONS: dict[OrganizationRole, frozenset[OrganizationPermission]] = {
    OrganizationRole.OWNER: _ORGANIZATION_ADMINISTRATION,
    OrganizationRole.ADMIN: _ORGANIZATION_ADMINISTRATION,
    OrganizationRole.ANALYST: frozenset(
        {
            OrganizationPermission.ORGANIZATION_READ,
            OrganizationPermission.WORKSPACE_READ,
            OrganizationPermission.ANALYTICS_READ,
            OrganizationPermission.RECORDS_READ,
        }
    ),
    OrganizationRole.EXECUTIVE: frozenset(
        {
            OrganizationPermission.ORGANIZATION_READ,
            OrganizationPermission.WORKSPACE_READ,
            OrganizationPermission.ANALYTICS_READ,
        }
    ),
}

# Which organization roles a caller may grant or manage.  Nobody grants OWNER
# through the API; admins never touch other admins.  Deny by default.
ORGANIZATION_GRANTABLE_ROLES: dict[OrganizationRole, frozenset[OrganizationRole]] = {
    OrganizationRole.OWNER: frozenset(
        {OrganizationRole.ADMIN, OrganizationRole.ANALYST, OrganizationRole.EXECUTIVE}
    ),
    OrganizationRole.ADMIN: frozenset({OrganizationRole.ANALYST, OrganizationRole.EXECUTIVE}),
    OrganizationRole.ANALYST: frozenset(),
    OrganizationRole.EXECUTIVE: frozenset(),
}

# Delegated administration: which *workspace* roles an organization role may
# install in (or withdraw from) a workspace of the organization.  The
# workspace owner role is never delegated.
DELEGATED_WORKSPACE_ROLES: dict[OrganizationRole, frozenset[WorkspaceRole]] = {
    OrganizationRole.OWNER: frozenset(
        {WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER}
    ),
    OrganizationRole.ADMIN: frozenset(
        {WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER}
    ),
    OrganizationRole.ANALYST: frozenset(),
    OrganizationRole.EXECUTIVE: frozenset(),
}

# Roles whose organization-level view is limited to explicit scope grants.
SCOPED_ORGANIZATION_ROLES: frozenset[OrganizationRole] = frozenset(
    {OrganizationRole.ANALYST, OrganizationRole.EXECUTIVE}
)


def organization_role_has(role: OrganizationRole, permission: OrganizationPermission) -> bool:
    return permission in ORGANIZATION_ROLE_PERMISSIONS.get(role, frozenset())


def can_grant_organization_role(
    actor_role: OrganizationRole, target_role: OrganizationRole
) -> bool:
    return target_role in ORGANIZATION_GRANTABLE_ROLES.get(actor_role, frozenset())


def can_manage_organization_member(
    *, actor_user_id: str, actor_role: OrganizationRole, target: OrganizationMembership
) -> bool:
    """Manage (change, revoke) only what the actor could have granted; never
    oneself and never an owner."""
    if target.user_id == actor_user_id or target.role == OrganizationRole.OWNER:
        return False
    return can_grant_organization_role(actor_role, target.role)


def can_delegate_workspace_role(actor_role: OrganizationRole, role: WorkspaceRole) -> bool:
    return role in DELEGATED_WORKSPACE_ROLES.get(actor_role, frozenset())


def organization_membership_effective_clause(now: datetime) -> ColumnElement[bool]:
    """SQL predicate: organization membership is active, unrevoked and in its window."""
    return and_(
        OrganizationMembership.is_active.is_(True),
        OrganizationMembership.revoked_at.is_(None),
        OrganizationMembership.valid_from <= now,
        or_(
            OrganizationMembership.valid_until.is_(None),
            OrganizationMembership.valid_until > now,
        ),
    )


@dataclass(frozen=True)
class OrganizationAccess:
    """Resolved organization-level visibility for one actor.

    ``scopes`` maps workspace id -> team ids (``None`` = the whole workspace)
    for scoped roles; unrestricted roles (owner/admin) see every workspace
    of the organization.  Neither grants workspace *content* access.
    """

    user_id: str
    role: OrganizationRole
    unrestricted: bool
    scopes: dict[str, frozenset[str] | None]

    def may_see_workspace(self, workspace_id: str) -> bool:
        return self.unrestricted or workspace_id in self.scopes

    def team_filter(self, workspace_id: str) -> frozenset[str] | None:
        """Team restriction inside ``workspace_id`` (``None`` = whole workspace)."""
        if self.unrestricted:
            return None
        return self.scopes.get(workspace_id)


def resolve_organization_access(
    db: Session, *, membership: OrganizationMembership, now: datetime | None = None
) -> OrganizationAccess:
    now = now or datetime.now(UTC)
    if membership.role not in SCOPED_ORGANIZATION_ROLES:
        return OrganizationAccess(
            user_id=membership.user_id, role=membership.role, unrestricted=True, scopes={}
        )
    # Under the organization context (no tenant bound) team rows are not
    # visible, so team activity is re-checked when a workspace is bound for
    # aggregation (``organization_service.scoped_workspaces``).
    grants = db.execute(
        select(OrganizationScopeGrant.workspace_id, OrganizationScopeGrant.team_id)
        .join(Workspace, Workspace.id == OrganizationScopeGrant.workspace_id)
        .where(
            OrganizationScopeGrant.membership_id == membership.id,
            OrganizationScopeGrant.organization_id == membership.organization_id,
            OrganizationScopeGrant.revoked_at.is_(None),
            Workspace.organization_id == membership.organization_id,
            Workspace.status == "active",
        )
    ).all()
    scopes: dict[str, frozenset[str] | None] = {}
    for workspace_id, team_id in grants:
        if team_id is None:
            scopes[workspace_id] = None
            continue
        if workspace_id in scopes and scopes[workspace_id] is None:
            continue
        scopes[workspace_id] = (scopes.get(workspace_id) or frozenset()) | {team_id}
    return OrganizationAccess(
        user_id=membership.user_id, role=membership.role, unrestricted=False, scopes=scopes
    )
