"""Bodies and answers of the CRM mapping API (HOM-88, DESIGN section 5).

Request bodies refuse extra fields.  Maps and the late window are typed loosely here on
purpose: ``backend.services.crm_mapping_service`` checks them and answers with the
contract's own error codes (``invalid_field_map``, ``invalid_outcome_map`` and so on).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.database.saas_models import LATE_RECORD_WINDOW_DEFAULT_SECONDS


class MappingProfileRequest(BaseModel):
    """``PUT /integrations/mapping-profiles/{source_system}``: the whole profile."""

    model_config = ConfigDict(extra="forbid")

    conversation_source_system: Any = None
    field_map: Any
    outcome_map: Any = Field(default_factory=dict)
    agent_map: Any = Field(default_factory=dict)
    late_record_window_seconds: Any = LATE_RECORD_WINDOW_DEFAULT_SECONDS
    is_active: bool = True


class MappingProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_system: str
    conversation_source_system: str
    field_map: dict[str, str]
    outcome_map: dict[str, str]
    agent_map: dict[str, str]
    late_record_window_seconds: int
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class MappingProfileListResponse(BaseModel):
    items: list[MappingProfileResponse]


class InboundRecordRequest(BaseModel):
    """``POST /integrations/{source_system}/records``.  Only the mapped values of
    ``payload`` are stored; the payload itself never is."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]


class InboundRecordResponse(BaseModel):
    id: str
    source_system: str
    event_id: str
    state: str
    meeting_id: str | None = None
    replayed: bool
    received_at: datetime
    expires_at: datetime | None = None
    reconciliation_item_ids: list[str]


class ReconciliationItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_system: str
    inbound_record_id: str
    meeting_id: str | None = None
    link_type: str | None = None
    reason: str
    status: str
    current_value: str | None = None
    candidate_value: str | None = None
    candidate_meeting_ids: list[str]
    resolution: str | None = None
    resolved_by_user_id: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class ReconciliationListResponse(BaseModel):
    items: list[ReconciliationItemResponse]
    next_before: datetime | None = None


class ResolveRequest(BaseModel):
    """``POST /integrations/reconciliation/{item_id}/resolve``.  Which fields a
    resolution needs depends on the item's reason (``invalid_resolution`` otherwise)."""

    model_config = ConfigDict(extra="forbid")

    resolution: str
    meeting_id: str | None = None
    user_id: str | None = None
    outcome: str | None = None


class ConversationExternalLinkResponse(BaseModel):
    """A conversation's current link in one source system.  ``external_id`` is the
    external record's id, or the outcome code for an outcome link."""

    model_config = ConfigDict(from_attributes=True)

    source_system: str
    link_type: str
    external_id: str | None = None
    is_active: bool | None = None
    outcome: str | None = None
    agent_user_id: str | None = None
    profile_version: int
    linked_at: datetime
    linked_by_user_id: str | None = None


class ConversationExternalLinkListResponse(BaseModel):
    items: list[ConversationExternalLinkResponse]
