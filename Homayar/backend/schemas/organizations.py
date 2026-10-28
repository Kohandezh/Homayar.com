"""Organization-level API contracts (WP03-T03).

Tenant identifiers never travel in request bodies: the organization comes from
the path, the caller's authority from server-side membership state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.schemas.coverage import AggregateMissingData, AggregateScorecard
from backend.schemas.trends import (
    OrganizationDisclosurePolicy,
    TrendCoverage,
    TrendDisclosure,
    TrendWindowResponse,
)

OrganizationRoleLiteral = Literal["owner", "admin", "analyst", "executive"]
GrantableOrganizationRoleLiteral = Literal["admin", "analyst", "executive"]
DelegatedWorkspaceRoleLiteral = Literal["admin", "member", "viewer"]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- requests -----------------------------------------------------------------


class OrganizationMemberGrantRequest(StrictRequest):
    """Grant an organization role to an existing, verified account."""

    email: EmailStr
    role: GrantableOrganizationRoleLiteral
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class OrganizationMemberUpdateRequest(StrictRequest):
    role: GrantableOrganizationRoleLiteral | None = None
    valid_until: datetime | None = None
    clear_valid_until: bool = Field(default=False, strict=True)


class OrganizationWorkspaceCreateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=180)


class WorkspaceMemberInstallRequest(StrictRequest):
    """Delegated administration: install an organization member in a workspace."""

    user_id: str = Field(min_length=1, max_length=36)
    role: DelegatedWorkspaceRoleLiteral


class ScopeGrantRequest(StrictRequest):
    membership_id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(min_length=1, max_length=36)
    team_id: str | None = Field(default=None, min_length=1, max_length=36)


# --- responses ------------------------------------------------------------------


class OrganizationSummaryResponse(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    role: str
    valid_until: datetime | None


class OrganizationListResponse(BaseModel):
    items: list[OrganizationSummaryResponse]
    total: int


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    role: str
    unrestricted: bool
    workspace_count: int
    created_at: datetime


class OrganizationMemberResponse(BaseModel):
    id: str
    organization_id: str
    user_id: str
    email: str | None
    display_name: str
    role: str
    is_active: bool
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None
    is_effective: bool
    granted_by_user_id: str | None
    created_at: datetime


class OrganizationMemberListResponse(BaseModel):
    items: list[OrganizationMemberResponse]
    total: int


class OrganizationWorkspaceResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    slug: str
    status: str
    record_scope: str
    my_workspace_role: str | None
    scoped_team_ids: list[str] | None
    created_at: datetime


class OrganizationWorkspaceListResponse(BaseModel):
    items: list[OrganizationWorkspaceResponse]
    total: int


class InstalledWorkspaceMemberResponse(BaseModel):
    membership_id: str
    workspace_id: str
    user_id: str
    role: str
    is_active: bool


class ScopeGrantResponse(BaseModel):
    id: str
    organization_id: str
    membership_id: str
    user_id: str
    workspace_id: str
    team_id: str | None
    granted_by_user_id: str
    revoked_at: datetime | None
    created_at: datetime


class ScopeGrantListResponse(BaseModel):
    items: list[ScopeGrantResponse]
    total: int


class WorkspaceAggregateResponse(BaseModel):
    workspace_id: str
    workspace_name: str
    team_ids: list[str] | None
    # HOM-320 (ADR 0041 R1, owner ruling B'): the live meetings of the item's HOM-82
    # population, received (ledger ``received_at``) in ``coverage_window``; null when
    # ``disclosure`` is withheld.
    meetings_total: int | None
    meetings_by_status: dict[str, int] | None
    audio_seconds_total: int | None
    action_items_total: int | None
    # HOM-299 (ADR 0041, L2): the executive view's block for the same workspace, grant and
    # window.  A withheld item has no count: ``coverage``, ``missing_data`` and the four
    # fields above are null.
    coverage: TrendCoverage | None
    scorecard: AggregateScorecard
    missing_data: AggregateMissingData | None
    disclosure: TrendDisclosure


class AggregatesResponse(BaseModel):
    organization_id: str
    # the request, echoed; HOM-320: they only choose ``coverage_window``, which every count uses
    since: datetime | None
    until: datetime | None
    generated_at: datetime
    items: list[WorkspaceAggregateResponse]
    # HOM-299: the disclosure rules and the closed-week window ``W`` of every item's metadata
    disclosure_policy: OrganizationDisclosurePolicy
    coverage_window: TrendWindowResponse


class ScopedRecordResponse(BaseModel):
    workspace_id: str
    meeting_id: str
    team_id: str | None
    title: str
    status: str
    language: str
    duration_seconds: int
    created_at: datetime


class ScopedRecordListResponse(BaseModel):
    items: list[ScopedRecordResponse]
    total: int
    truncated: bool
