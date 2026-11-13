"""Federation group mapping contracts (HOM-128, ADR 0066).

The mapping body is strict: unknown fields are refused, and ``issuer`` and
``group_name`` must arrive exactly as the IdP uses them (no silent stripping),
because the mapping compares them byte for byte.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MappingRoleLiteral = Literal["admin", "member", "viewer"]


class _ExactStrictRequest(BaseModel):
    """Strict body whose strings are never trimmed (HOM-128)."""

    model_config = ConfigDict(extra="forbid")


def _refuse_padding(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("must not be empty or padded with whitespace")
    return value


class GroupMappingCreateRequest(_ExactStrictRequest):
    issuer: str = Field(min_length=1, max_length=512)
    group_name: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(min_length=1, max_length=36)
    role: MappingRoleLiteral

    _exact = field_validator("issuer", "group_name")(_refuse_padding)


class GroupMappingResponse(BaseModel):
    id: str
    organization_id: str
    issuer: str
    group_name: str
    workspace_id: str
    role: str
    created_by_user_id: str
    created_at: datetime


class GroupMappingListResponse(BaseModel):
    items: list[GroupMappingResponse]
