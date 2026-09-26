"""AI processing policy API (WP08-T02, ADR 0027)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.providers.policy import AiPolicy


class AiPolicyResponse(BaseModel):
    version: int  # 0 = the built-in default (never stored)
    source: Literal["default", "workspace"]
    policy_schema: str
    digest: str
    policy: AiPolicy
    reason: str | None = None
    created_by_user_id: str | None = None
    created_at: datetime | None = None


class AiPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    policy: AiPolicy
    # The version the caller read; a different current version is a 409.
    expected_version: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=3, max_length=500)


class AiPolicyUpdateResponse(BaseModel):
    changed: bool
    current: AiPolicyResponse


class AiPolicyVersionListResponse(BaseModel):
    current_version: int
    items: list[AiPolicyResponse]
    presets: dict[str, AiPolicy]


class RunPolicySnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt: int
    policy_version: int
    policy_digest: str
    policy_document: dict[str, Any]
    text_privacy_digest: str | None
    strict_manifests: bool
    stages: list[dict[str, Any]]
    created_at: datetime


class RunPolicySnapshotListResponse(BaseModel):
    job_id: str
    items: list[RunPolicySnapshotResponse]
