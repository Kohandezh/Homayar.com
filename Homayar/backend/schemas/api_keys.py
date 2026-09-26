from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateRequest(BaseModel):
    """Issue a workspace API key bound to the calling user (WP03-T07, ADR 0013)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    scopes: list[str] = Field(min_length=1, max_length=16)
    expires_in_days: int = Field(default=90, ge=1, le=3650, strict=True)


class ApiKeyResponse(BaseModel):
    """A key as listed: the public prefix, never the secret or its digest."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_by_user_id: str
    created_at: datetime


class ApiKeyCreatedResponse(ApiKeyResponse):
    """The creation response carries the raw key exactly once."""

    key: str


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]
