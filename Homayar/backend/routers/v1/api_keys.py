"""Workspace API keys (WP03-T07, ADR 0013).

Management is session-only in effect: the routes require ``api_key:manage``,
which is never grantable to a key, so a key-authenticated caller is refused
with ``403 api_key_scope_denied``. Raw keys appear in the creation response
only; listings carry the public prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.schemas.api_keys import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyListResponse,
    ApiKeyResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.api_key_service import (
    create_api_key,
    get_api_key,
    list_api_keys,
    revoke_api_key,
)
from backend.services.audit_service import record_audit_event

router = APIRouter(prefix="/api-keys", tags=["api keys"])


@router.get("", response_model=ApiKeyListResponse)
def list_workspace_api_keys(
    context: WorkspaceContext = Depends(require_permission(Permission.API_KEY_MANAGE)),
    db: Session = Depends(get_db),
) -> ApiKeyListResponse:
    keys = list_api_keys(db, workspace_id=context.workspace.id)
    return ApiKeyListResponse(items=[ApiKeyResponse.model_validate(key) for key in keys])


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
def create_workspace_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.API_KEY_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiKeyCreatedResponse:
    key, raw_key = create_api_key(
        db,
        workspace=context.workspace,
        membership=context.membership,
        name=payload.name,
        scopes=payload.scopes,
        expires_in_days=payload.expires_in_days,
        settings=settings,
    )
    record_audit_event(
        db,
        event_type="api_key.created",
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="api_key",
        target_id=key.id,
        request_id=getattr(request.state, "request_id", None),
        # Public identifiers only: never the raw key or its digest.
        details={
            "name": key.name,
            "credential_prefix": key.key_prefix,
            "scopes": ",".join(key.scopes),
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        },
        settings=settings,
    )
    db.commit()
    db.refresh(key)
    return ApiKeyCreatedResponse(**ApiKeyResponse.model_validate(key).model_dump(), key=raw_key)


@router.delete("/{key_id}", status_code=204, response_class=Response)
def revoke_workspace_api_key(
    key_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.API_KEY_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    key = get_api_key(db, workspace_id=context.workspace.id, key_id=key_id)
    if revoke_api_key(db, key=key, now=datetime.now(UTC)):
        record_audit_event(
            db,
            event_type="api_key.revoked",
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type="api_key",
            target_id=key.id,
            request_id=getattr(request.state, "request_id", None),
            details={"name": key.name, "credential_prefix": key.key_prefix},
            settings=settings,
        )
        db.commit()
    return Response(status_code=204)
