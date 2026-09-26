from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.jobs.outbox import dispatch_job
from backend.jobs.replay import ReplayRefused, replay_dead_letter_job
from backend.schemas.admin import (
    AuditEventListResponse,
    AuditEventResponse,
    JobReplayResponse,
    WorkspaceSettingsResponse,
    WorkspaceSettingsUpdateRequest,
)
from backend.schemas.meetings import JobResponse
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.admin_service import (
    InvalidAuditCursor,
    InvalidAuditRange,
    InvalidWorkspaceSettings,
    WorkspaceNotFoundError,
    get_workspace_settings,
    list_workspace_audit_events,
    sanitize_audit_details,
    update_workspace_settings,
)
from backend.services.audit_service import record_audit_event

router = APIRouter(prefix="/admin", tags=["admin"])

# Administrative reads/writes are governed by explicit permissions that only
# OWNER/ADMIN hold (see backend/security/authorization.py); no extra role
# checks are layered on top so the matrix document stays the single policy.
require_audit_admin_context = require_permission(Permission.AUDIT_READ)
require_workspace_admin_context = require_permission(Permission.WORKSPACE_MANAGE)
require_workspace_admin_write_context = require_write_permission(Permission.WORKSPACE_MANAGE)

PRIVACY_POLICY_FIELDS = frozenset({"pii_redaction_enabled", "pii_redaction_classes"})


def _workspace_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "workspace_not_found", "message": "Workspace not found"},
    )


@router.get("/audit-events", response_model=AuditEventListResponse)
def list_audit_events(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    event_type: str | None = Query(default=None, min_length=1, max_length=100),
    outcome: str | None = Query(default=None, min_length=1, max_length=20),
    actor_user_id: str | None = Query(default=None, min_length=1, max_length=36),
    target_type: str | None = Query(default=None, min_length=1, max_length=60),
    target_id: str | None = Query(default=None, min_length=1, max_length=100),
    occurred_after: datetime | None = Query(default=None),
    occurred_before: datetime | None = Query(default=None),
    context: WorkspaceContext = Depends(require_audit_admin_context),
    db: Session = Depends(get_db),
) -> AuditEventListResponse:
    try:
        page = list_workspace_audit_events(
            db,
            workspace_id=context.workspace.id,
            limit=limit,
            cursor=cursor,
            event_type=event_type,
            outcome=outcome,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
        )
    except InvalidAuditCursor as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_cursor", "message": str(exc)},
        ) from exc
    except InvalidAuditRange as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_audit_range", "message": str(exc)},
        ) from exc

    return AuditEventListResponse(
        items=[
            AuditEventResponse(
                id=event.id,
                actor_user_id=event.actor_user_id,
                event_type=event.event_type,
                target_type=event.target_type,
                target_id=event.target_id,
                outcome=event.outcome,
                request_id=event.request_id,
                details=sanitize_audit_details(event.details),
                occurred_at=event.occurred_at,
            )
            for event in page.events
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/workspace-settings", response_model=WorkspaceSettingsResponse)
def read_workspace_settings(
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
) -> WorkspaceSettingsResponse:
    try:
        workspace = get_workspace_settings(db, workspace_id=context.workspace.id)
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    return WorkspaceSettingsResponse.model_validate(workspace)


@router.patch("/workspace-settings", response_model=WorkspaceSettingsResponse)
def patch_workspace_settings(
    payload: WorkspaceSettingsUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WorkspaceSettingsResponse:
    changes = payload.model_dump(exclude_unset=True)
    if PRIVACY_POLICY_FIELDS & changes.keys() and not context.has(Permission.PII_ORIGINAL_ACCESS):
        # WP07-T06: whoever may switch redaction off could read originals, so
        # the policy needs the same right as original access (owner).
        raise HTTPException(
            status_code=403,
            detail={
                "code": "permission_denied",
                "message": "Changing the redaction policy needs pii:original_access",
            },
        )
    try:
        workspace, updated_fields = update_workspace_settings(
            db,
            workspace_id=context.workspace.id,
            changes=changes,
        )
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    except InvalidWorkspaceSettings as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_workspace_settings", "message": str(exc)},
        ) from exc

    record_audit_event(
        db,
        event_type="workspace.settings.updated",
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace",
        target_id=context.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        details={"fields": ",".join(updated_fields)},
        settings=settings,
    )
    db.commit()
    db.refresh(workspace)
    return WorkspaceSettingsResponse.model_validate(workspace)


@router.post("/jobs/{job_id}/replay", response_model=JobReplayResponse, status_code=202)
def replay_job(
    job_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobReplayResponse:
    """Requeue a dead-lettered job (WP06-T05): authorized, audited, bounded.

    Refusals leave the job untouched: ``409 job_not_replayable`` (not
    dead-lettered), ``409 replay_limit_reached``, ``409 meeting_deleted`` /
    ``retention_expired`` / ``recording_missing`` (nothing is resurrected),
    ``429 audio_quota_exceeded`` / ``concurrent_job_limit_reached`` (current
    entitlement does not cover the replay).
    """
    try:
        result = replay_dead_letter_job(
            db,
            job_id=job_id,
            workspace_id=context.workspace.id,
            actor_user_id=context.actor.user.id,
            settings=settings,
            request_id=getattr(request.state, "request_id", None),
        )
    except ReplayRefused as exc:
        db.rollback()
        detail: dict[str, object] = {"code": exc.code, "message": exc.message}
        if exc.details:
            detail["details"] = exc.details
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    db.commit()
    # Request-time publication through the shared publisher; the outbox
    # sweeper republishes the re-armed intent if this attempt fails.
    outcome = dispatch_job(result.job.id, settings)
    db.refresh(result.job)
    return JobReplayResponse(
        job=JobResponse.model_validate(result.job),
        replay=result.replay,
        max_replays=settings.job_max_replays,
        dispatch=outcome.state,
    )
