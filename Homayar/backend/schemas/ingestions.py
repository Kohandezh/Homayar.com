from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IngestionResponse(BaseModel):
    """One ledger row of the canonical ingestion contract (WP05-T01)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    organization_id: str
    source_type: str
    source_provider: str
    external_event_id: str
    external_source_id: str | None = None
    payload_hash: str
    media_hash: str | None = None
    media_size_bytes: int | None = None
    declared_content_type: str | None = None
    original_filename: str | None = None
    status: str
    reason: str | None = None
    received_at: datetime
    created_at: datetime
    created_by_user_id: str | None = None
    api_key_id: str | None = None
    meeting_id: str | None = None
    job_id: str | None = None
    duplicate_of_id: str | None = None
    conflict_count: int = 0
    last_conflict_at: datetime | None = None


class IngestionListResponse(BaseModel):
    items: list[IngestionResponse]
    next_before: datetime | None = None
