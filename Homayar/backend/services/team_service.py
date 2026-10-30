"""Teams and effective-dated team assignments (WP03-T03).

Teams live inside one workspace.  An assignment gives a workspace member an
operational role (supervisor or agent) in a team for a time window.  Access is
derived from *effective* assignments only (see ``security.authorization``);
revoked or expired rows stay for audit and history.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Membership,
    Team,
    TeamAssignment,
    TeamRole,
    User,
)
from backend.security.authorization import (
    Permission,
    assignment_effective_clause,
    membership_effective_clause,
)
from backend.security.context import WorkspaceContext

_SLUG_STRIP = re.compile(r"[^a-z0-9؀-ۿ]+")


class TeamServiceError(Exception):
    code = "team_error"
    status_code = 409
    public_message = "Team operation failed"


class TeamNotFoundError(TeamServiceError):
    code = "team_not_found"
    status_code = 404
    public_message = "Team not found"


class TeamConflictError(TeamServiceError):
    code = "team_conflict"
    status_code = 409
    public_message = "A team with this name already exists"


class AssignmentNotFoundError(TeamServiceError):
    code = "assignment_not_found"
    status_code = 404
    public_message = "Assignment not found"


class AssignmentConflictError(TeamServiceError):
    code = "assignment_conflict"
    status_code = 409
    public_message = "An overlapping assignment already exists"


class AssigneeNotMemberError(TeamServiceError):
    code = "assignee_not_member"
    status_code = 422
    public_message = "The user is not an effective member of this workspace"


class InvalidWindowError(TeamServiceError):
    code = "invalid_validity_window"
    status_code = 422
    public_message = "valid_until must be after valid_from"


@dataclass(frozen=True)
class AssignmentRecord:
    assignment: TeamAssignment
    user: User


def slugify(name: str) -> str:
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return slug[:100] or "team"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def list_teams(db: Session, *, workspace_id: str) -> list[Team]:
    return list(
        db.scalars(
            select(Team)
            .where(Team.workspace_id == workspace_id)
            .order_by(Team.created_at.asc(), Team.id.asc())
        )
    )


def get_team(db: Session, *, workspace_id: str, team_id: str) -> Team:
    team = db.scalar(select(Team).where(Team.id == team_id, Team.workspace_id == workspace_id))
    if team is None:
        raise TeamNotFoundError
    return team


def create_team(db: Session, *, workspace_id: str, name: str) -> Team:
    clean_name = name.strip()
    slug = slugify(clean_name)
    existing = db.scalar(
        select(Team.id).where(Team.workspace_id == workspace_id, Team.slug == slug)
    )
    if existing is not None:
        raise TeamConflictError
    team = Team(workspace_id=workspace_id, name=clean_name, slug=slug, is_active=True)
    db.add(team)
    db.flush()
    return team


def update_team(
    db: Session,
    *,
    workspace_id: str,
    team_id: str,
    name: str | None,
    is_active: bool | None,
) -> Team:
    team = get_team(db, workspace_id=workspace_id, team_id=team_id)
    if name is not None:
        clean_name = name.strip()
        slug = slugify(clean_name)
        clash = db.scalar(
            select(Team.id).where(
                Team.workspace_id == workspace_id, Team.slug == slug, Team.id != team.id
            )
        )
        if clash is not None:
            raise TeamConflictError
        team.name = clean_name
        team.slug = slug
    if is_active is not None:
        team.is_active = is_active
    db.flush()
    return team


def list_assignments(
    db: Session, *, workspace_id: str, team_id: str
) -> list[AssignmentRecord]:
    rows = db.execute(
        select(TeamAssignment, User)
        .join(User, User.id == TeamAssignment.user_id)
        .where(TeamAssignment.workspace_id == workspace_id, TeamAssignment.team_id == team_id)
        .order_by(TeamAssignment.created_at.asc(), TeamAssignment.id.asc())
    )
    return [AssignmentRecord(assignment=assignment, user=user) for assignment, user in rows]


def _effective_member(db: Session, *, workspace_id: str, user_id: str, now: datetime) -> User:
    row = db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
            membership_effective_clause(now),
            User.is_active.is_(True),
        )
    ).first()
    if row is None:
        raise AssigneeNotMemberError
    return row[0]


def create_assignment(
    db: Session,
    *,
    workspace_id: str,
    team_id: str,
    user_id: str,
    role: TeamRole,
    assigned_by_user_id: str,
    valid_from: datetime | None,
    valid_until: datetime | None,
    now: datetime | None = None,
) -> AssignmentRecord:
    now = now or datetime.now(UTC)
    team = get_team(db, workspace_id=workspace_id, team_id=team_id)
    start = _as_utc(valid_from) if valid_from is not None else now
    end = _as_utc(valid_until) if valid_until is not None else None
    if end is not None and end <= start:
        raise InvalidWindowError
    user = _effective_member(db, workspace_id=workspace_id, user_id=user_id, now=now)

    # Reject an open (not revoked, not ended) row for the same team/user/role
    # whose window overlaps the new one.
    overlapping = db.scalar(
        select(TeamAssignment.id).where(
            TeamAssignment.team_id == team.id,
            TeamAssignment.user_id == user.id,
            TeamAssignment.role == role,
            TeamAssignment.revoked_at.is_(None),
            TeamAssignment.valid_from < (end or datetime.max.replace(tzinfo=UTC)),
            (TeamAssignment.valid_until.is_(None)) | (TeamAssignment.valid_until > start),
        )
    )
    if overlapping is not None:
        raise AssignmentConflictError

    assignment = TeamAssignment(
        workspace_id=workspace_id,
        team_id=team.id,
        user_id=user.id,
        role=role,
        assigned_by_user_id=assigned_by_user_id,
        valid_from=start,
        valid_until=end,
    )
    db.add(assignment)
    db.flush()
    return AssignmentRecord(assignment=assignment, user=user)


def revoke_assignment(
    db: Session,
    *,
    workspace_id: str,
    team_id: str,
    assignment_id: str,
    now: datetime | None = None,
) -> TeamAssignment:
    now = now or datetime.now(UTC)
    assignment = db.scalar(
        select(TeamAssignment)
        .where(
            TeamAssignment.id == assignment_id,
            TeamAssignment.workspace_id == workspace_id,
            TeamAssignment.team_id == team_id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise AssignmentNotFoundError
    if assignment.revoked_at is None:
        assignment.revoked_at = now
        db.flush()
    return assignment


# --- record attribution guards used by the meetings router -----------------


def authorize_attribution_change(
    context: WorkspaceContext,
    *,
    current_agent_user_id: str | None,
    changes: Mapping[str, str | None],
) -> None:
    """Refuse a change of a call's ``team_id`` / ``agent_user_id`` (HOM-360, ADR 0052).

    ``changes`` holds only the fields whose new value differs from the current one, so
    an empty mapping needs no right.  The review queue decides who sees and works a
    review from these two values, so a change needs ``meeting:attribute`` (owner,
    admin; never an API key), and nobody changes the attribution of their own call or
    makes themselves its agent.  The two validators below run after this one.
    """
    if not changes:
        return
    # A key is refused by itself too: until HOM-361 a stored key scope is not
    # intersected with ``API_KEY_SCOPES``, so ``has`` alone would trust a stray scope.
    # Its own message, so the integrator of an owner's key is not told to be an owner.
    if context.actor.api_key is not None:
        message = "An API key cannot change a call's team or agent"
    elif not context.has(Permission.MEETING_ATTRIBUTE):
        message = "Only an owner or admin may change a call's team or agent"
    else:
        message = None
    if message is not None:
        raise HTTPException(
            status_code=403,
            detail={"code": "attribution_change_denied", "message": message},
        )
    caller = context.actor.user.id
    if current_agent_user_id == caller or changes.get("agent_user_id") == caller:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "attribution_own_call",
                "message": "Nobody changes the team or agent of their own call",
            },
        )


def validate_team_attribution(
    db: Session, *, context: WorkspaceContext, team_id: str | None
) -> str | None:
    """Return the team id a meeting may be attributed to, or raise 403/422."""
    access = context.record_access
    if team_id is None:
        if access.restricted:
            # A scoped supervisor may not detach a record from the teams that
            # make it visible to them.
            raise HTTPException(
                status_code=403,
                detail={"code": "team_scope_denied", "message": "Team attribution denied"},
            )
        return None
    team = db.scalar(
        select(Team).where(
            Team.id == team_id,
            Team.workspace_id == context.workspace.id,
            Team.is_active.is_(True),
        )
    )
    if team is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_team", "message": "Team is not an active team here"},
        )
    if not access.may_assign_team(team.id):
        raise HTTPException(
            status_code=403,
            detail={"code": "team_scope_denied", "message": "Team attribution denied"},
        )
    return team.id


def validate_agent_attribution(
    db: Session, *, context: WorkspaceContext, agent_user_id: str | None
) -> str | None:
    if agent_user_id is None:
        return None
    now = datetime.now(UTC)
    try:
        _effective_member(
            db, workspace_id=context.workspace.id, user_id=agent_user_id, now=now
        )
    except AssigneeNotMemberError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_agent", "message": exc.public_message},
        ) from exc
    access = context.record_access
    if access.restricted:
        # A scoped supervisor may only attribute records to agents who hold an
        # effective assignment in one of the teams they supervise.
        allowed = db.scalar(
            select(TeamAssignment.id).where(
                TeamAssignment.workspace_id == context.workspace.id,
                TeamAssignment.user_id == agent_user_id,
                TeamAssignment.team_id.in_(sorted(access.supervised_team_ids) or [""]),
                assignment_effective_clause(now),
            )
        )
        if allowed is None:
            raise HTTPException(
                status_code=403,
                detail={"code": "agent_scope_denied", "message": "Agent attribution denied"},
            )
    return agent_user_id
