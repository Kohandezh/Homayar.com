"""Workspace AI processing policy (WP08-T02, ADR 0027).

* ``GET /admin/ai-policy`` — the policy the workspace runs under now
  (version 0 = the built-in default).  ``workspace:manage``.
* ``PUT /admin/ai-policy`` — append a new version.  ``ai_policy:manage``
  (owner; never an API key) with CSRF, a reason and the version the caller
  read (``409 policy_version_conflict`` otherwise).  Audited.
* ``GET /admin/ai-policy/versions`` — history, newest first, plus presets.
* ``GET /admin/jobs/{job_id}/policy-snapshots`` — what each processing
  attempt of a job ran under.  ``workspace:manage``.

A stored version never changes; a running attempt keeps the snapshot it
started with, and every later attempt (retry, replay) reads the current one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import ProcessingJob
from backend.providers.policy import POLICY_SCHEMA, PRESETS
from backend.schemas.ai_policy import (
    AiPolicyResponse,
    AiPolicyUpdateRequest,
    AiPolicyUpdateResponse,
    AiPolicyVersionListResponse,
    RunPolicySnapshotListResponse,
    RunPolicySnapshotResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.ai_policy_service import (
    EffectivePolicy,
    PolicyVersionConflict,
    changed_stages,
    current_policy,
    list_policy_versions,
    list_run_snapshots,
    set_policy,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event

router = APIRouter(prefix="/admin", tags=["admin"])


def _response(effective: EffectivePolicy) -> AiPolicyResponse:
    return AiPolicyResponse(
        version=effective.version,
        source=effective.source,
        policy_schema=POLICY_SCHEMA,
        digest=effective.digest,
        policy=effective.policy,
        reason=effective.reason,
        created_by_user_id=effective.created_by_user_id,
        created_at=effective.created_at,
    )


@router.get("/ai-policy", response_model=AiPolicyResponse)
def read_ai_policy(
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> AiPolicyResponse:
    return _response(current_policy(db, workspace_id=context.workspace.id))


@router.put("/ai-policy", response_model=AiPolicyUpdateResponse)
def update_ai_policy(
    payload: AiPolicyUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.AI_POLICY_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AiPolicyUpdateResponse:
    before = current_policy(db, workspace_id=context.workspace.id)
    try:
        current, changed = set_policy(
            db,
            workspace_id=context.workspace.id,
            policy=payload.policy,
            expected_version=payload.expected_version,
            actor_user_id=context.actor.user.id,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
    except PolicyVersionConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "policy_version_conflict",
                "message": "The AI policy changed since it was read; reload and retry",
                "details": {"current_version": exc.current_version},
            },
        ) from exc
    if changed:
        record_audit_event(
            db,
            event_type=AuditEventType.AI_POLICY_UPDATED,
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type="ai_policy",
            target_id=str(current.version),
            request_id=getattr(request.state, "request_id", None),
            details={
                "version": current.version,
                "previous_version": before.version,
                "digest": current.digest,
                "stages": ",".join(changed_stages(before.policy, current.policy)),
                "reason": payload.reason,
            },
            settings=settings,
        )
    db.commit()
    return AiPolicyUpdateResponse(changed=changed, current=_response(current))


@router.get("/ai-policy/versions", response_model=AiPolicyVersionListResponse)
def list_ai_policy_versions(
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> AiPolicyVersionListResponse:
    versions = list_policy_versions(db, workspace_id=context.workspace.id)
    return AiPolicyVersionListResponse(
        current_version=versions[0].version if versions else 0,
        items=[_response(item) for item in versions],
        presets=dict(PRESETS),
    )


@router.get("/jobs/{job_id}/policy-snapshots", response_model=RunPolicySnapshotListResponse)
def list_job_policy_snapshots(
    job_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> RunPolicySnapshotListResponse:
    job = db.scalar(
        select(ProcessingJob.id).where(
            ProcessingJob.id == job_id, ProcessingJob.workspace_id == context.workspace.id
        )
    )
    if job is None:
        raise HTTPException(
            status_code=404, detail={"code": "job_not_found", "message": "Job not found"}
        )
    snapshots = list_run_snapshots(db, workspace_id=context.workspace.id, job_id=job_id)
    return RunPolicySnapshotListResponse(
        job_id=job_id,
        items=[RunPolicySnapshotResponse.model_validate(item) for item in snapshots],
    )
