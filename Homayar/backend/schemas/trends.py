"""Issue-trend aggregates (HOM-80, WP15-T02): the answer of ``GET /trends/issues``.

The shapes are the HOM-79 ones (``backend/issue_trends/README.md``, "A trend answer"):
``trend_provenance``, ``TrendWindow.to_json()``, ``TrendFilters.to_json()`` and
``TrendRow.to_json()``.  Instants are the strings those functions write, never
re-serialized: the window in UTC (``+00:00``), the points in the series timezone.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrendDefinitionRef(BaseModel):
    id: str
    version: int
    digest: str


class TrendTaxonomyRef(BaseModel):
    id: str
    version: int
    digest: str


class TrendVersionRef(BaseModel):
    """An extraction policy or the sample policy: a version and its digest."""

    version: str
    digest: str


class TrendWindowResponse(BaseModel):
    """``from <= received_at < to`` of the ingestion ledger, ISO 8601 in UTC."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    basis: Literal["ingestion_received_at"]


class TrendFiltersResponse(BaseModel):
    """The normalized filters; an empty list means no restriction."""

    window: TrendWindowResponse
    team_id: list[str]
    agent_user_id: list[str]
    language: list[str]
    source_type: list[str]
    family: list[str]
    issue: list[str]
    service_area: list[str]


class TrendScope(BaseModel):
    """``restricted``: the caller sees only the meetings of their record scope."""

    restricted: bool


class TrendSeries(BaseModel):
    grain: Literal["none", "day", "week", "month"]
    timezone: str
    calendar: Literal["gregorian"]
    week_starts_on: Literal["saturday"]


class TrendExcluded(BaseModel):
    duplicate_media: int


class TrendPopulation(BaseModel):
    """Conversations counted, and the copies of a media group left out."""

    included: int
    excluded: TrendExcluded


class TrendCell(BaseModel):
    status: Literal["computed", "not_computable", "insufficient_sample"]
    numerator: int | None
    denominator: int
    ratio: float | None
    reason: str | None
    minimum_sample: int


class TrendRowResponse(BaseModel):
    level: Literal["family", "issue"]
    family: str
    issue: str | None
    cell: TrendCell
    pending_review: int | None
    absent_pending_review: int | None
    missing: dict[str, int]


class TrendPoint(BaseModel):
    """One point of a series: ``from <= received_at < to``, in the series timezone."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    partial: bool
    population: TrendPopulation
    rows: list[TrendRowResponse]


class IssueTrendResponse(BaseModel):
    trends_version: str
    definition: TrendDefinitionRef
    taxonomy: TrendTaxonomyRef
    extraction_policies: list[TrendVersionRef]
    validator_versions: list[str]
    sample_policy: TrendVersionRef
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    scope: TrendScope
    series: TrendSeries
    population: TrendPopulation
    rows: list[TrendRowResponse]
    points: list[TrendPoint]
