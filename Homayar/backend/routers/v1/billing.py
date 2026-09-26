from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database.relational import get_db
from backend.database.saas_models import Entitlement, Plan
from backend.schemas.billing import (
    BillingManagementResponse,
    CurrentSubscriptionResponse,
    PlanCatalogResponse,
    PlanResponse,
    SubscriptionResponse,
    UsageEventListResponse,
    UsageEventResponse,
    UsageOverviewResponse,
    UsageSnapshotResponse,
)
from backend.security.context import Permission, WorkspaceContext, require_permission
from backend.services.billing_service import (
    BillingConfigurationError,
    get_current_billing,
    list_public_plans,
    list_usage_events,
    list_usage_snapshots,
)

router = APIRouter(prefix="/billing", tags=["billing"])


def _plan_response(plan: Plan) -> PlanResponse:
    return PlanResponse(
        key=plan.key,
        name=plan.name,
        is_public=plan.is_public,
        entitlements=dict(plan.entitlements_json or {}),
    )


def _usage_response(entitlement: Entitlement) -> UsageSnapshotResponse:
    remaining = (
        None
        if entitlement.limit_value is None
        else max(
            0,
            entitlement.limit_value - entitlement.used_value - entitlement.reserved_value,
        )
    )
    return UsageSnapshotResponse(
        metric=entitlement.metric,
        source=entitlement.source,
        limit=entitlement.limit_value,
        used=entitlement.used_value,
        reserved=entitlement.reserved_value,
        remaining=remaining,
        period_start=entitlement.period_start,
        period_end=entitlement.period_end,
        updated_at=entitlement.updated_at,
    )


@router.get("/plans", response_model=PlanCatalogResponse)
def get_plan_catalog(
    _context: WorkspaceContext = Depends(require_permission(Permission.BILLING_READ)),
    db: Session = Depends(get_db),
) -> PlanCatalogResponse:
    return PlanCatalogResponse(items=[_plan_response(plan) for plan in list_public_plans(db)])


@router.get("/subscription", response_model=CurrentSubscriptionResponse)
def get_subscription(
    context: WorkspaceContext = Depends(require_permission(Permission.BILLING_READ)),
    db: Session = Depends(get_db),
) -> CurrentSubscriptionResponse:
    try:
        current = get_current_billing(db, workspace_id=context.workspace.id)
    except BillingConfigurationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "billing_configuration_error",
                "message": "Billing configuration is incomplete",
            },
        ) from exc
    return CurrentSubscriptionResponse(
        workspace_id=context.workspace.id,
        plan=_plan_response(current.plan) if current.plan is not None else None,
        subscription=(
            SubscriptionResponse.model_validate(current.subscription)
            if current.subscription is not None
            else None
        ),
    )


@router.get("/usage", response_model=UsageOverviewResponse)
def get_usage(
    context: WorkspaceContext = Depends(require_permission(Permission.BILLING_READ)),
    db: Session = Depends(get_db),
) -> UsageOverviewResponse:
    entitlements = list_usage_snapshots(db, workspace_id=context.workspace.id)
    return UsageOverviewResponse(
        workspace_id=context.workspace.id,
        items=[_usage_response(entitlement) for entitlement in entitlements],
    )


@router.get("/usage/events", response_model=UsageEventListResponse)
def get_usage_events(
    limit: int = Query(default=50, ge=1, le=100),
    metric: str | None = Query(default=None, min_length=1, max_length=60),
    before: datetime | None = Query(default=None),
    before_id: str | None = Query(default=None, min_length=1, max_length=36),
    context: WorkspaceContext = Depends(require_permission(Permission.BILLING_READ)),
    db: Session = Depends(get_db),
) -> UsageEventListResponse:
    if before_id is not None and before is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_usage_cursor",
                "message": "before_id requires before",
            },
        )
    page = list_usage_events(
        db,
        workspace_id=context.workspace.id,
        limit=limit,
        metric=metric,
        before=before,
        before_id=before_id,
    )
    return UsageEventListResponse(
        items=[UsageEventResponse.model_validate(event) for event in page.items],
        next_before=page.next_before,
        next_before_id=page.next_before_id,
    )


@router.get("/management", response_model=BillingManagementResponse)
def get_billing_management(
    context: WorkspaceContext = Depends(require_permission(Permission.BILLING_MANAGE)),
) -> BillingManagementResponse:
    return BillingManagementResponse(workspace_id=context.workspace.id)
