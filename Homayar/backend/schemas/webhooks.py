from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The URL policy (webhook_url_refusal) gives the real limit of 1000 characters
# with its own error code; this bound only stops absurd input early.
_URL_INPUT_LIMIT = 4096
MAX_OVERLAP_SECONDS = 7 * 24 * 3600


class WebhookEndpointCreateRequest(BaseModel):
    """Add an endpoint (HOM-89, ADR 0047). Its first signing key is made with it."""

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
