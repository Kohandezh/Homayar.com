from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.database.saas_models import (
    DuplicateMediaPolicy,
    JobState,
    RecordScope,
    RetentionMode,
    ShareAudience,
)
from backend.schemas.meetings import JobResponse
from backend.services.admin_shares_service import ShareStatus


class AuditEventResponse(BaseModel):
    id: str
    actor_user_id: str | None
    event_type: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    details: dict[str, Any]
    occurred_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    next_cursor: str | None = None


class AdminShareResponse(BaseModel):
    """One share link in the administrator's list (HOM-192).

    Metadata only: never the token, its hash, a share URL or recipient addresses.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str
    meeting_title: str
    audience: ShareAudience
    status: ShareStatus
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime | None
    access_count: int
    last_accessed_at: datetime | None
    revoked_at: datetime | None


class AdminShareListResponse(BaseModel):
    items: list[AdminShareResponse]
    next_cursor: str | None = None


class JobReplayResponse(BaseModel):
    """Outcome of an operator replay of a dead-lettered job (WP06-T05)."""

    job: JobResponse
    replay: int
    max_replays: int
    dispatch: str  # publisher outcome: published | deferred | failed | busy | ...


class AdminJobResponse(BaseModel):
    """One processing job in the administrator's list (HOM-183).

    Metadata only: no transcript, recording, lease or reservation data.
    """

    id: str
    meeting_id: str
    meeting_title: str
    state: JobState
    stage: str
    attempt_count: int
    max_attempts: int
    failure_code: str | None
    failure_class: str | None
    failure_message: str | None  # audit string rule: secrets redacted, cut to 500
    replay_count: int
    max_replays: int
    replayable: bool
    created_at: datetime
    updated_at: datetime


class AdminJobListResponse(BaseModel):
    items: list[AdminJobResponse]
    next_cursor: str | None = None


class AdminJobStateCounts(BaseModel):
    queued: int
    running: int
    completed: int
    failed: int
    cancelled: int
    dead_letter: int


class AdminJobSummaryResponse(BaseModel):
    counts: AdminJobStateCounts
    total: int


class WorkspaceSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    retention_days: int
    retention_mode: RetentionMode
    delete_audio_after_processing: bool
    record_scope: RecordScope
    public_share_links_enabled: bool
    organization_share_links_enabled: bool
    duplicate_media_policy: DuplicateMediaPolicy
    pii_redaction_enabled: bool
    pii_redaction_classes: list[str]
    updated_at: datetime


class WorkspaceSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=180)
    retention_days: int | None = Field(default=None, ge=0, le=3650, strict=True)
    retention_mode: RetentionMode | None = None
    delete_audio_after_processing: bool | None = Field(default=None, strict=True)
    record_scope: RecordScope | None = None
    public_share_links_enabled: bool | None = Field(default=None, strict=True)
    organization_share_links_enabled: bool | None = Field(default=None, strict=True)
    duplicate_media_policy: DuplicateMediaPolicy | None = None
    pii_redaction_enabled: bool | None = Field(default=None, strict=True)
    pii_redaction_classes: list[str] | None = Field(default=None, min_length=1, max_length=16)

    @model_validator(mode="before")
    @classmethod
    def require_non_empty_values(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not value:
            raise ValueError("At least one workspace setting is required")
        if any(field_value is None for field_value in value.values()):
            raise ValueError("Workspace settings cannot be null")
        return value
