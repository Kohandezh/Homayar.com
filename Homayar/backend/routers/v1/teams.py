from __future__ import annotations

from datetime import UTC, datetime
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import TeamRole
from backend.schemas.teams import (
    AssignmentCreateRequest,
    AssignmentResponse,
    TeamCreateRequest,
    TeamDetailResponse,
    TeamListResponse,
    TeamResponse,
    TeamUpdateRequest,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.team_service import (
    AssignmentRecord,
    TeamServiceError,
    create_assignment,
    create_team,
    get_team,
    list_assignments,
    list_teams,
    revoke_assignment,
    update_team,
)

router = APIRouter(prefix="/teams", tags=["teams"])


def _raise(exc: TeamServiceError) -> Never:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.public_message},
    ) from exc


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _assignment_response(record: AssignmentRecord, *, now: datetime) -> AssignmentResponse:
    assignment = record.assignment
    is_effective = (
        assignment.revoked_at is None
        and _as_utc(assignment.valid_from) <= now
        and (assignment.valid_until is None or _as_utc(assignment.valid_until) > now)
    )
    return AssignmentResponse(
        id=assignment.id,
        team_id=assignment.team_id,
        user_id=assignment.user_id,
        display_name=record.user.display_name,
        email=record.user.email,
        role=assignment.role.value,
        valid_from=assignment.valid_from,
        valid_until=assignment.valid_until,
        revoked_at=assignment.revoked_at,
        is_effective=is_effective,
        created_at=assignment.created_at,
    )


@router.get("", response_model=TeamListResponse)
def list_workspace_teams(
    context: WorkspaceContext = Depends(require_permission(Permission.TEAM_READ)),
    db: Session = Depends(get_db),
) -> TeamListResponse:
    teams = list_teams(db, workspace_id=context.workspace.id)
    return TeamListResponse(
        items=[TeamResponse.model_validate(team) for team in teams], total=len(teams)
    )


@router.post("", response_model=TeamResponse, status_code=201)
def create_workspace_team(
    payload: TeamCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.TEAM_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TeamResponse:
    try:
        team = create_team(db, workspace_id=context.workspace.id, name=payload.name)
    except TeamServiceError as exc:
        _raise(exc)
    record_audit_event(
        db,
        event_type=AuditEventType.TEAM_CREATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="team",
        target_id=team.id,
        request_id=_request_id(request),
        settings=settings,
    )
    db.commit()
    db.refresh(team)
    return TeamResponse.model_validate(team)


@router.get("/{team_id}", response_model=TeamDetailResponse)
def read_team(
    team_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.TEAM_READ)),
    db: Session = Depends(get_db),
) -> TeamDetailResponse:
    try:
        team = get_team(db, workspace_id=context.workspace.id, team_id=team_id)
    except TeamServiceError as exc:
        _raise(exc)
    now = datetime.now(UTC)
    records = list_assignments(db, workspace_id=context.workspace.id, team_id=team.id)
    return TeamDetailResponse(
        **TeamResponse.model_validate(team).model_dump(),
        assignments=[_assignment_response(record, now=now) for record in records],
    )


@router.patch("/{team_id}", response_model=TeamResponse)
def patch_team(
    team_id: str,
    payload: TeamUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.TEAM_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TeamResponse:
    if not payload.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_changes", "message": "No updatable field was provided"},
        )
    try:
        team = update_team(
            db,
            workspace_id=context.workspace.id,
            team_id=team_id,
            name=payload.name,
            is_active=payload.is_active,
        )
    except TeamServiceError as exc:
        _raise(exc)
    record_audit_event(
        db,
        event_type=AuditEventType.TEAM_UPDATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="team",
        target_id=team.id,
        request_id=_request_id(request),
        details={"fields": ",".join(sorted(payload.model_fields_set))},
        settings=settings,
    )
    db.commit()
    db.refresh(team)
    return TeamResponse.model_validate(team)


@router.post("/{team_id}/assignments", response_model=AssignmentResponse, status_code=201)
def assign_member(
    team_id: str,
    payload: AssignmentCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.TEAM_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AssignmentResponse:
    now = datetime.now(UTC)
    try:
        record = create_assignment(
            db,
            workspace_id=context.workspace.id,
            team_id=team_id,
            user_id=payload.user_id,
            role=TeamRole(payload.role),
            assigned_by_user_id=context.actor.user.id,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            now=now,
        )
    except TeamServiceError as exc:
        _raise(exc)
    record_audit_event(
        db,
        event_type=AuditEventType.TEAM_ASSIGNMENT_CREATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="team_assignment",
        target_id=record.assignment.id,
        request_id=_request_id(request),
        details={
            "team_id": team_id,
            "user_id": record.user.id,
            "role": record.assignment.role.value,
        },
        settings=settings,
    )
    db.commit()
    db.refresh(record.assignment)
    return _assignment_response(record, now=now)


@router.delete(
    "/{team_id}/assignments/{assignment_id}",
    status_code=204,
    response_class=Response,
)
def revoke_member_assignment(
    team_id: str,
    assignment_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.TEAM_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        assignment = revoke_assignment(
            db,
            workspace_id=context.workspace.id,
            team_id=team_id,
            assignment_id=assignment_id,
        )
    except TeamServiceError as exc:
        _raise(exc)
    record_audit_event(
        db,
        event_type=AuditEventType.TEAM_ASSIGNMENT_REVOKED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="team_assignment",
        target_id=assignment.id,
        request_id=_request_id(request),
        details={"team_id": team_id, "user_id": assignment.user_id},
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)
