from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TeamRoleLiteral = Literal["supervisor", "agent"]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TeamCreateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=180)


class TeamUpdateRequest(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    is_active: bool | None = Field(default=None, strict=True)


class AssignmentCreateRequest(StrictRequest):
    user_id: str = Field(min_length=1, max_length=36)
    role: TeamRoleLiteral
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TeamListResponse(BaseModel):
    items: list[TeamResponse]
    total: int


class AssignmentResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    display_name: str
    email: str | None
    role: str
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None
    is_effective: bool
    created_at: datetime


class TeamDetailResponse(TeamResponse):
    assignments: list[AssignmentResponse]
