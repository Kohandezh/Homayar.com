from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SubjectExportRequest(BaseModel):
    """A data-subject export (WP07-T07): up to ten identifiers and a reason."""

    model_config = ConfigDict(extra="forbid")

    identifiers: list[str] = Field(min_length=1, max_length=10)
    reason: str = Field(min_length=3, max_length=200)

    @field_validator("identifiers")
    @classmethod
    def bounded_identifiers(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 64 for value in values):
            raise ValueError("Identifiers must be 1–64 characters")
        return values

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("A reason is required")
        return value
