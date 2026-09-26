from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

AssignableRole = Literal["admin", "member", "viewer"]
InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InviteMemberRequest(StrictRequest):
    email: EmailStr
    role: AssignableRole = "member"
    expires_in_days: int = Field(default=7, ge=1, le=30)


class ChangeMemberRoleRequest(StrictRequest):
    role: AssignableRole


class AcceptInvitationRequest(StrictRequest):
    token: str = Field(min_length=32, max_length=256)


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str | None
    phone_e164: str | None
    display_name: str
    role: str
    joined_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class MemberListResponse(BaseModel):
    items: list[MemberResponse]
    total: int


class InvitationResponse(BaseModel):
    id: str
    workspace_id: str
    email: str
    role: str
    invited_by_user_id: str
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationListResponse(BaseModel):
    items: list[InvitationResponse]
    total: int


class InvitationAcceptedResponse(BaseModel):
    workspace_id: str
    workspace_name: str
    membership: MemberResponse
