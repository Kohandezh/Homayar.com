from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LocationKindLiteral = Literal["branch", "dealer", "service_center"]
HoursRange = list[str]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _LocationFields(StrictRequest):
    province: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=120)
    street: str | None = Field(default=None, max_length=300)
    postal_code: str | None = Field(default=None, max_length=16)
    address_text: str | None = Field(default=None, max_length=2000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    phone: str | None = Field(default=None, max_length=32)
    service_tags: list[str] | None = Field(default=None, max_length=50)
    opening_hours: dict[str, list[HoursRange]] | None = None


class LocationCreateRequest(_LocationFields):
    kind: LocationKindLiteral = "branch"
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    is_active: bool = True


class LocationUpdateRequest(_LocationFields):
    kind: LocationKindLiteral | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    is_active: bool | None = Field(default=None, strict=True)


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    kind: str
    name: str
    code: str
    province: str | None
    city: str | None
    district: str | None
    street: str | None
    postal_code: str | None
    address_text: str | None
    latitude: float | None
    longitude: float | None
    phone: str | None
    is_active: bool
    service_tags: list[str]
    opening_hours: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class LocationWriteResponse(LocationResponse):
    warnings: list[str] = []


class LocationListResponse(BaseModel):
    items: list[LocationResponse]
    total: int


class ResolveRequest(StrictRequest):
    text: str = Field(min_length=1, max_length=4000)


class ClarificationOption(BaseModel):
    key: str
    label_fa: str
    label_en: str
    kind: str
    province_key: str | None = None


class Clarification(BaseModel):
    slot: str
    options: list[ClarificationOption]
    question_fa: str


class CandidateResponse(BaseModel):
    type: str
    text: str
    start: int
    end: int
    confidence: float
    ambiguous: bool
    places: list[dict[str, Any]]
    value: dict[str, Any]
    note: str | None = None


class ResolvedPoint(BaseModel):
    latitude: float
    longitude: float
    precision: str
    confidence: float
    city_key: str | None = None
    province_key: str | None = None
    source: str


class ResolveResponse(BaseModel):
    status: Literal["resolved", "ambiguous", "unresolved"]
    candidates: list[CandidateResponse]
    resolved: ResolvedPoint | None
    clarification: Clarification | None
    postal_code: str | None
    landmarks: list[str]


class NearestRequest(StrictRequest):
    text: str | None = Field(default=None, max_length=4000)
    latitude: float | None = None
    longitude: float | None = None
    kinds: list[LocationKindLiteral] | None = None
    service_tag: str | None = Field(default=None, max_length=64)
    include_inactive: bool = False
    open_now: bool = False
    reference_time: datetime | None = None
    prefer_same_city: bool = False
    max_distance_km: float | None = Field(default=None, gt=0, le=5000)
    limit: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def _needs_input(self) -> NearestRequest:
        if not self.text and self.latitude is None and self.longitude is None:
            raise ValueError("Provide text or latitude/longitude")
        return self


class NearestItem(BaseModel):
    location: LocationResponse
    distance_km: float
    reason: str
    open_now: bool | None
    same_city: bool


class NearestResponse(BaseModel):
    status: Literal["ok", "needs_clarification", "no_match"]
    query: ResolvedPoint | None
    confidence: float
    results: list[NearestItem]
    clarification: Clarification | None
    notes: list[str]
    extraction: ResolveResponse | None = None
