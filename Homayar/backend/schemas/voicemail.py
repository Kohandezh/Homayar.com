from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.meetings import JobResponse
from backend.voicemail.models import FollowUpState


class VoicemailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str
    ingestion_id: str | None
    external_message_id: str
    mailbox: str | None
    extension: str | None
    caller_number: str | None
    caller_number_valid: bool
    received_at: datetime
    duration_seconds: int
    status: str
    review_reason: str | None
    category: str | None
    intent: str | None
    topics: list[str]
    priority: str | None
    priority_score: int | None
    priority_reasons: list[dict[str, Any]]
    classification_confidence: float | None
    classifier_kind: str | None
    classifier_version: str | None
    classifier_model_id: str | None
    classified_revision_id: str | None
    classified_at: datetime | None
    follow_up_state: str
    follow_up_action_id: str | None
    follow_up_done_at: datetime | None
    assigned_team_id: str | None
    assigned_user_id: str | None
    created_at: datetime


class VoicemailDetailResponse(VoicemailResponse):
    meeting_status: str
    transcript_excerpt: str | None = None
    transcript_revision_id: str | None = None


class VoicemailImportResponse(BaseModel):
    voicemail: VoicemailResponse
    job: JobResponse | None
    idempotent_replay: bool


class VoicemailListResponse(BaseModel):
    items: list[VoicemailResponse]
    next_before: datetime | None = None


class VoicemailUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    follow_up_state: FollowUpState | None = None
    assigned_user_id: str | None = Field(default=None, max_length=36)
    assigned_team_id: str | None = Field(default=None, max_length=36)


class DayCount(BaseModel):
    day: date
    count: int


class VoicemailAnalyticsResponse(BaseModel):
    received_from: datetime
    received_to: datetime
    total: int
    by_category: dict[str, int]
    by_priority: dict[str, int]
    by_status: dict[str, int]
    by_follow_up_state: dict[str, int]
    by_day: list[DayCount]
    followed_up: int
    median_follow_up_seconds: float | None
