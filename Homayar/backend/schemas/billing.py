from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanResponse(BaseModel):
    key: str
    name: str
    is_public: bool
    entitlements: dict[str, Any]
    price_published: Literal[False] = False
    pricing_mode: Literal["manual_quote"] = "manual_quote"


class PlanCatalogResponse(BaseModel):
    items: list[PlanResponse]


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_key: str
    plan_version: int  # the version of the plan this subscription was granted under
    status: str
    provider: str
    starts_at: datetime
    ends_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CurrentSubscriptionResponse(BaseModel):
    workspace_id: str
    plan: PlanResponse | None
    subscription: SubscriptionResponse | None
    billing_mode: Literal["manual"] = "manual"
    price_published: Literal[False] = False


class UsageSnapshotResponse(BaseModel):
    metric: str
    source: str
    limit: int | None
    used: int
    reserved: int
    remaining: int | None
    period_start: datetime
    period_end: datetime | None
    updated_at: datetime


class UsageOverviewResponse(BaseModel):
    workspace_id: str
    items: list[UsageSnapshotResponse]


class UsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    metric: str
    value: int
    event_type: str  # reserved | committed | released | adjusted (WP06-T06)
    subject_type: str
    subject_id: str
    operation_key: str | None = None
    reason: str | None = None
    occurred_at: datetime


class UsageEventListResponse(BaseModel):
    items: list[UsageEventResponse]
    next_before: datetime | None = None
    next_before_id: str | None = None


class BillingManagementResponse(BaseModel):
    workspace_id: str
    mode: Literal["manual_read_only"] = "manual_read_only"
    write_operations_enabled: Literal[False] = False
    contact_request_supported: Literal[False] = False
    mutation_endpoints: list[str] = Field(default_factory=list)
    message: str = (
        "Billing is managed manually; this API does not persist plan changes or contact requests."
    )
