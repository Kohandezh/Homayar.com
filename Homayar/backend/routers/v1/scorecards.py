"""Versioned QA scorecards (HOM-54).

* ``GET /scorecards`` — the latest version of each key.  ``scorecard:read``.
* ``GET /scorecards/{key}/versions`` — every version of a key, newest first.
* ``GET /scorecards/{key}/versions/{version}`` — one version with its definition.
* ``POST /scorecards/{key}/versions`` — publish the next version.  ``scorecard:manage``
  (owner, admin; never an API key) with CSRF, a reason and the version the caller read
  (``409 scorecard_version_conflict`` otherwise).  Audited.
* ``POST /scorecards/validate`` — check a definition and give its digest; stores nothing.

A stored version never changes.  An invalid definition is a 422 whose ``code`` is the
``ScorecardError`` code of ``backend.scorecards``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import ScorecardVersion
from backend.schemas.scorecards import (
    ScorecardItem,
    ScorecardListResponse,
    ScorecardPublishRequest,
    ScorecardValidateRequest,
    ScorecardValidateResponse,
    ScorecardVersionItem,
    ScorecardVersionListResponse,
    ScorecardVersionResponse,
)
from backend.scorecards import ScorecardError, definition_digest, validate_definition
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.scorecard_service import (
    ScorecardVersionConflict,
    check_key,
    get_version,
    latest_versions,
    list_versions,
    publish_version,
)

router = APIRouter(prefix="/scorecards", tags=["scorecards"])


def _invalid(error: ScorecardError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": error.code, "message": str(error)})


def _key(key: str) -> str:
    try:
        return check_key(key)
    except ScorecardError as error:
        raise _invalid(error) from error


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


def _fields(row: ScorecardVersion) -> dict:
    return {
        "scorecard_key": row.scorecard_key,
        "version": row.version,
        "name": row.document["name"],
        "digest": row.digest,
        "scorecards_version": row.scorecards_version,
        "created_at": row.created_at,
        "created_by_user_id": row.created_by_user_id,
    }


def _version(row: ScorecardVersion) -> ScorecardVersionResponse:
    return ScorecardVersionResponse(**_fields(row), reason=row.reason, definition=row.document)


@router.get("", response_model=ScorecardListResponse)
def list_scorecards(
    context: WorkspaceContext = Depends(require_permission(Permission.SCORECARD_READ)),
    db: Session = Depends(get_db),
) -> ScorecardListResponse:
    rows = latest_versions(db, workspace_id=context.workspace.id)
    return ScorecardListResponse(items=[ScorecardItem(**_fields(row)) for row in rows])


@router.post("/validate", response_model=ScorecardValidateResponse)
def validate_scorecard(
    payload: ScorecardValidateRequest,
    context: WorkspaceContext = Depends(require_write_permission(Permission.SCORECARD_MANAGE)),
) -> ScorecardValidateResponse:
    try:
        model = validate_definition(payload.definition)
    except ScorecardError as error:
        raise _invalid(error) from error
    return ScorecardValidateResponse(
        valid=True, digest=definition_digest(model), definition=model.model_dump(mode="json")
    )


@router.get("/{key}/versions", response_model=ScorecardVersionListResponse)
def list_scorecard_versions(
    key: str,
    context: WorkspaceContext = Depends(require_permission(Permission.SCORECARD_READ)),
    db: Session = Depends(get_db),
) -> ScorecardVersionListResponse:
    rows = list_versions(db, workspace_id=context.workspace.id, scorecard_key=_key(key))
    if not rows:
        raise _not_found("scorecard_not_found", "Scorecard not found")
    return ScorecardVersionListResponse(
        items=[ScorecardVersionItem(**_fields(row), reason=row.reason) for row in rows]
    )


@router.get("/{key}/versions/{version}", response_model=ScorecardVersionResponse)
def read_scorecard_version(
    key: str,
    version: int,
    context: WorkspaceContext = Depends(require_permission(Permission.SCORECARD_READ)),
    db: Session = Depends(get_db),
) -> ScorecardVersionResponse:
    row = get_version(
        db, workspace_id=context.workspace.id, scorecard_key=_key(key), version=version
    )
    if row is None:
        raise _not_found("scorecard_version_not_found", "Scorecard version not found")
    return _version(row)


@router.post("/{key}/versions", response_model=ScorecardVersionResponse, status_code=201)
def publish_scorecard_version(
    key: str,
    payload: ScorecardPublishRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.SCORECARD_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ScorecardVersionResponse:
    scorecard_key = _key(key)
    try:
        row = publish_version(
            db,
            workspace_id=context.workspace.id,
            scorecard_key=scorecard_key,
            definition=payload.definition,
            expected_version=payload.expected_version,
            reason=payload.reason,
            actor_user_id=context.actor.user.id,
            now=datetime.now(UTC),
        )
    except ScorecardError as error:
        raise _invalid(error) from error
    except ScorecardVersionConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "scorecard_version_conflict",
                "message": "The scorecard changed since it was read; reload and retry",
                "details": {"current_version": exc.current_version},
            },
        ) from exc
    record_audit_event(
        db,
        event_type=AuditEventType.SCORECARD_PUBLISHED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="scorecard",
        target_id=scorecard_key,
        request_id=getattr(request.state, "request_id", None),
        details={
            "scorecard_key": scorecard_key,
            "version": row.version,
            "digest": row.digest,
            "reason": payload.reason,
        },
        settings=settings,
    )
    db.commit()
    return _version(row)
