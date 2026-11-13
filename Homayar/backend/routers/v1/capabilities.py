"""The capability endpoints of HOM-229 (ADR 0065).

``GET /api/v1/capabilities`` is what the console reads: every registry module
with its effective state for the caller's workspace; it is never refused by a
module guard.  ``PUT /api/v1/admin/capabilities/{module_key}`` flips the
workspace admin switch stored in ``workspaces.settings_json`` under
``"modules"``: disabling only refuses calls, it never deletes data, and the
write is allowed whatever the license or install state.  Session-only
administration: ``workspace:manage`` is never an API-key scope.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import Workspace
from backend.schemas.capabilities import (
    CapabilityListResponse,
    CapabilityModuleResponse,
    CapabilityUpdateRequest,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_workspace,
    require_write_permission,
)
from backend.security.modules import (
    ModuleEvaluation,
    evaluate_all_modules,
    evaluate_module,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.capability_registry import (
    CAPABILITY_REGISTRY_VERSION,
    MODULES_SETTINGS_KEY,
    lookup_module,
)

router = APIRouter(tags=["capabilities"])


def _module_response(evaluation: ModuleEvaluation) -> CapabilityModuleResponse:
    return CapabilityModuleResponse(
        key=evaluation.key,
        name=evaluation.name,
        description=evaluation.description,
        enabled=evaluation.enabled,
        reason=evaluation.reason,
        install_enabled=evaluation.install_enabled,
        licensed=evaluation.licensed,
        admin_enabled=evaluation.admin_enabled,
    )


@router.get("/capabilities", response_model=CapabilityListResponse)
def list_capabilities(
    context: WorkspaceContext = Depends(require_workspace),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CapabilityListResponse:
    evaluations = evaluate_all_modules(db, settings=settings, workspace=context.workspace)
    return CapabilityListResponse(
        registry_version=CAPABILITY_REGISTRY_VERSION,
        workspace_id=context.workspace.id,
        modules=[_module_response(evaluation) for evaluation in evaluations],
    )


def _locked_workspace(db: Session, *, workspace_id: str) -> Workspace:
    """The caller's workspace row, locked so concurrent module writes serialise."""
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.status == "active")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "workspace_not_found", "message": "Workspace not found"},
        )
    return workspace


class UnreadableModuleSettings(ValueError):
    """The stored settings cannot be read as the module switch (ADR 0065 D2).

    The write refuses instead of replacing them, which would silently re-enable
    the other five modules.
    """

    code = "module_settings_unreadable"


def _readable_module_entries(settings_json: object) -> dict:
    """The stored ``"modules"`` object as a fresh dict; raises when unreadable.

    Like ``readable_entries`` in ``media/channel_roles.py``: ``settings_json``
    not an object, or a present ``"modules"`` key that is not an object, is
    unreadable.  No settings at all, or no ``"modules"`` key, reads as empty.
    """
    if settings_json is None:
        return {}
    if not isinstance(settings_json, Mapping):
        raise UnreadableModuleSettings("Workspace settings are not an object")
    if MODULES_SETTINGS_KEY not in settings_json:
        return {}
    stored = settings_json[MODULES_SETTINGS_KEY]
    if not isinstance(stored, dict):
        raise UnreadableModuleSettings("Stored module settings are not an object")
    return copy.deepcopy(stored)


def _write_module_entry(workspace: Workspace, key: str, entry: dict) -> None:
    modules = _readable_module_entries(workspace.settings_json)
    settings_json = copy.deepcopy(dict(workspace.settings_json or {}))
    modules[key] = entry
    settings_json[MODULES_SETTINGS_KEY] = modules
    workspace.settings_json = settings_json
    flag_modified(workspace, "settings_json")


@router.put("/admin/capabilities/{module_key}", response_model=CapabilityModuleResponse)
def set_module_enabled(
    module_key: str,
    payload: CapabilityUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CapabilityModuleResponse:
    module = lookup_module(module_key)
    if module is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "module_not_found",
                "message": "Module not found",
                "details": {"module": module_key},
            },
        )
    workspace = _locked_workspace(db, workspace_id=context.workspace.id)
    try:
        _write_module_entry(
            workspace,
            module.key,
            {
                "enabled": payload.enabled,
                "changed_at": datetime.now(UTC).isoformat(),
                "changed_by": context.actor.user.id,
            },
        )
    except UnreadableModuleSettings as exc:
        # Unreadable stored settings are a conflict with the stored state (409);
        # nothing changes and no audit event is written.
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    record_audit_event(
        db,
        event_type=(
            AuditEventType.WORKSPACE_MODULE_ENABLED
            if payload.enabled
            else AuditEventType.WORKSPACE_MODULE_DISABLED
        ),
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace",
        target_id=context.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        details={"module": module.key, "enabled": payload.enabled},
        settings=settings,
    )
    db.commit()
    db.refresh(workspace)
    evaluation = evaluate_module(db, settings=settings, workspace=workspace, module=module)
    return _module_response(evaluation)
