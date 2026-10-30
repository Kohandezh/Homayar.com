"""QA scorecard API (HOM-54)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ScorecardItem(BaseModel):
    """One stored version, without its reason and definition (the list of keys)."""

    scorecard_key: str
    version: int
    name: str
    digest: str
    scorecards_version: str
    created_at: datetime
    created_by_user_id: str | None = None


class ScorecardVersionItem(ScorecardItem):
    reason: str


class ScorecardVersionResponse(ScorecardVersionItem):
    definition: dict[str, Any]  # the stored document


class ScorecardListResponse(BaseModel):
    items: list[ScorecardItem]  # the latest version of each key, sorted by key


class ScorecardVersionListResponse(BaseModel):
    items: list[ScorecardVersionItem]  # newest first


class ScorecardPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Validated by backend.scorecards, so every problem gets its scorecard code.
    definition: Any
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    # The version the caller read, 0 for a new key; a different current version is a 409.
    expected_version: int = Field(ge=0, strict=True)


class ScorecardValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: Any


class ScorecardValidateResponse(BaseModel):
    valid: Literal[True]
    digest: str
    definition: dict[str, Any]  # with every default filled in
