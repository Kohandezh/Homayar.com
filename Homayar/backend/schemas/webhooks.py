from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The URL policy (webhook_url_refusal) gives the real limit of 1000 characters
# with its own error code; this bound only stops absurd input early.
_URL_INPUT_LIMIT = 4096
MAX_OVERLAP_SECONDS = 7 * 24 * 3600


class WebhookEndpointCreateRequest(BaseModel):
    """Add an endpoint (HOM-89, ADR 0050). Its first signing key is made with it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=_URL_INPUT_LIMIT)
    event_types: list[str] = Field(max_length=50)
    description: str | None = Field(default=None, max_length=200)


class WebhookEndpointUpdateRequest(BaseModel):
    """Change any of these fields. Deactivate with ``is_active: false``; there is no delete."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str | None = Field(default=None, min_length=1, max_length=_URL_INPUT_LIMIT)
    event_types: list[str] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    is_active: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def _only_description_may_be_null(self) -> WebhookEndpointUpdateRequest:
        for name in ("url", "event_types", "is_active"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class WebhookSigningKeyRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # How long the previous key keeps signing next to the new one.
    overlap_seconds: int = Field(default=86400, ge=0, le=MAX_OVERLAP_SECONDS, strict=True)


class WebhookSigningKeyView(BaseModel):
    """A key as listed: its public id and lifetime, never the secret."""

    key_id: str
    created_at: datetime
    expires_at: datetime | None
    state: Literal["active", "retiring", "expired"]


class WebhookEndpointView(BaseModel):
    id: str
    url: str
    description: str | None
    event_types: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    signing_keys: list[WebhookSigningKeyView]


class WebhookEndpointListResponse(BaseModel):
    items: list[WebhookEndpointView]


class WebhookEndpointCreatedResponse(BaseModel):
    """The only answer, with the rotation answer, that carries a signing secret."""

    endpoint: WebhookEndpointView
    key_id: str
    signing_secret: str


class WebhookSigningKeyRotatedResponse(BaseModel):
    key_id: str
    signing_secret: str
    previous_key_id: str | None
    previous_key_expires_at: datetime | None


class WebhookTestQueuedResponse(BaseModel):
    delivery_id: str
    event_id: str


# --- delivery diagnostics and redelivery (HOM-90) -------------------------------------

WebhookDeliveryStateName = Literal["pending", "sending", "delivered", "failed", "dead_letter"]


class WebhookDeliveryView(BaseModel):
    """One delivery as an admin sees it: ids, state and short codes only.

    Never the payload, its digest, the endpoint URL, a secret, headers or a
    signature.
    """

    id: str
    endpoint_id: str
    event_id: str
    event_type: str
    state: WebhookDeliveryStateName
    attempt_count: int
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryListResponse(BaseModel):
    items: list[WebhookDeliveryView]
    next_cursor: str | None


class WebhookDeliveryAttemptView(BaseModel):
    """One HTTP request of a delivery. The response body is never stored."""

    attempt_number: int
    started_at: datetime
    duration_ms: int
    outcome: Literal["delivered", "failed"]
    status_code: int | None
    error_code: str | None
    signing_key_ids: list[str]
    request_timestamp: int


class WebhookDeliveryDetailView(WebhookDeliveryView):
    # The latest attempts, oldest of them first; ``attempt_total`` counts them all.
    attempts: list[WebhookDeliveryAttemptView]
    attempt_total: int


class WebhookDeliveryStateCounts(BaseModel):
    pending: int
    sending: int
    delivered: int
    failed: int
    dead_letter: int


class WebhookEndpointDeliverySummary(BaseModel):
    endpoint_id: str
    counts: WebhookDeliveryStateCounts
    last_delivered_at: datetime | None
    last_failure_at: datetime | None
    last_failure_error_code: str | None


class WebhookRedeliveryQueuedResponse(BaseModel):
    delivery_id: str
    event_id: str
    state: Literal["pending"]


class WebhookDeadLettersRedeliverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only rows created at or after this time; null or absent: no filter.
    since: datetime | None = None


class WebhookDeadLettersRedeliveredResponse(BaseModel):
    endpoint_id: str
    redelivered: int
    # True when more matching rows are left after the batch limit.
    remaining: bool
