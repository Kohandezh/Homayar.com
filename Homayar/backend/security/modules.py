"""The module guard of HOM-229: one dependency per capability router.

``require_module(key)`` resolves the request's workspace through its
``workspace`` dependency (by default the existing ``require_workspace``, which
accepts API keys; a session-only router passes its session-only workspace
dependency instead, so every API-key bearer is refused before any key lookup,
in every module state) and refuses with one error when the module is not
enabled for it.  The refusal order is: authentication, membership, module,
then the route's own permission check, so a module refusal never depends on
the caller's role.  The effective state is read fresh on every request: no
cache (ADR 0065).

An enforced offline licence (HOM-109, ADR 0073) narrows the licensed modules
to its own list while it allows processing, and makes every module read-only
(safe methods only) while it is restricted.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import Workspace
from backend.licensing.offline_license import (
    RESTRICTED_MESSAGE,
    SAFE_METHODS,
    LicenseStatus,
    current_license_status,
)
from backend.security.context import WorkspaceContext, require_workspace
from backend.services.billing_service import BillingConfigurationError, get_current_billing
from backend.services.capability_registry import (
    CAPABILITY_MODULES,
    CapabilityModule,
    admin_module_enabled,
    licensed_module_keys,
    lookup_module,
)


@dataclass(frozen=True)
class ModuleEvaluation:
    key: str
    name: str
    description: str
    install_enabled: bool
    licensed: bool
    admin_enabled: bool
    enabled: bool
    reason: str | None


def _licensed_keys(db: Session, *, workspace_id: str) -> frozenset[str] | None:
    """``None`` licenses every module: no current subscription, no plan version,
    or entitlements without a ``"modules"`` key (the HOM-109 gap, ADR 0065).
    A broken billing configuration licenses nothing: fail closed."""
    try:
        billing = get_current_billing(db, workspace_id=workspace_id)
    except BillingConfigurationError:
        return frozenset()
    if billing.version is None:
        return None
    return licensed_module_keys(billing.version.entitlements_json)


def _with_license(
    plan_keys: frozenset[str] | None, license_status: LicenseStatus
) -> frozenset[str] | None:
    """The plan rule intersected with the licence modules while it allows
    processing; a restricted or unenforced licence leaves the plan rule as is."""
    if not license_status.processing_allowed or not license_status.enforced:
        return plan_keys
    licensed = frozenset(license_status.modules)
    return licensed if plan_keys is None else plan_keys & licensed


def _effective_licensed_keys(
    db: Session, *, settings: Settings, workspace_id: str
) -> frozenset[str] | None:
    return _with_license(
        _licensed_keys(db, workspace_id=workspace_id), current_license_status(settings)
    )


def _evaluate(
    module: CapabilityModule,
    *,
    install_disabled: frozenset[str],
    licensed_keys: frozenset[str] | None,
    settings_json: object,
) -> ModuleEvaluation:
    install_enabled = module.key not in install_disabled
    licensed = licensed_keys is None or module.key in licensed_keys
    admin_enabled = admin_module_enabled(settings_json, module.key)
    if not install_enabled:
        reason = "disabled_for_install"
    elif not licensed:
        reason = "not_licensed"
    elif not admin_enabled:
        reason = "disabled_by_admin"
    else:
        reason = None
    return ModuleEvaluation(
        key=module.key,
        name=module.name,
        description=module.description,
        install_enabled=install_enabled,
        licensed=licensed,
        admin_enabled=admin_enabled,
        enabled=reason is None,
        reason=reason,
    )


def evaluate_module(
    db: Session, *, settings: Settings, workspace: Workspace, module: CapabilityModule
) -> ModuleEvaluation:
    return _evaluate(
        module,
        install_disabled=settings.disabled_module_keys,
        licensed_keys=_effective_licensed_keys(db, settings=settings, workspace_id=workspace.id),
        settings_json=workspace.settings_json,
    )


def evaluate_all_modules(
    db: Session, *, settings: Settings, workspace: Workspace
) -> list[ModuleEvaluation]:
    licensed_keys = _effective_licensed_keys(db, settings=settings, workspace_id=workspace.id)
    return [
        _evaluate(
            module,
            install_disabled=settings.disabled_module_keys,
            licensed_keys=licensed_keys,
            settings_json=workspace.settings_json,
        )
        for module in CAPABILITY_MODULES
    ]


def require_module(key: str, workspace=require_workspace):
    """The module guard of one router.  ``workspace`` is the dependency that
    resolves the request's workspace (shared, through FastAPI's dependency
    cache, with the route's own policy dependencies); the session-only trends
    router passes its session-only workspace dependency so the HOM-82 contract
    (401 ``api_key_not_accepted`` before any key lookup) holds in every module
    state (ADR 0065, amendment 2)."""

    module = lookup_module(key)
    if module is None:
        raise ValueError(f"Unknown module key {key!r}")

    async def dependency(
        request: Request,
        context: WorkspaceContext = Depends(workspace),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> None:
        license_status = current_license_status(settings)
        evaluation = _evaluate(
            module,
            install_disabled=settings.disabled_module_keys,
            licensed_keys=_with_license(
                _licensed_keys(db, workspace_id=context.workspace.id), license_status
            ),
            settings_json=context.workspace.settings_json,
        )
        if not evaluation.enabled:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "module_disabled",
                    "message": f"The {module.name} module is turned off",
                    "details": {"module": module.key, "reason": evaluation.reason},
                },
            )
        if license_status.restricted and request.method not in SAFE_METHODS:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "license_restricted",
                    "message": RESTRICTED_MESSAGE,
                    "details": {
                        "state": license_status.state,
                        "reason": license_status.reason,
                        "module": module.key,
                    },
                },
            )

    # Policy metadata read by the route-coverage test (never by the runtime).
    dependency.homayar_module = key  # type: ignore[attr-defined]
    return dependency
