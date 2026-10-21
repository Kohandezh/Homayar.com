"""Coverage counters and review sample draws (HOM-72, WP14-T01)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CoverageCounter(BaseModel):
    """One counter: a written definition, a numerator over a denominator, and
    mutually exclusive buckets that sum to the denominator."""

    definition: str
    numerator_label: str
    denominator_label: str
    numerator: int | None
    denominator: int
    ratio: float | None
    status: Literal["computed", "not_computable"]
    reason: str | None
    buckets: dict[str, int]


class IngestionCoverageCounter(CoverageCounter):
    rejected_reasons: dict[str, int]


class CoverageCounters(BaseModel):
    ingestion: IngestionCoverageCounter
    processing: CoverageCounter
    evaluation: CoverageCounter
    review: CoverageCounter


class CoverageWindowResponse(BaseModel):
    """``from <= received_at < to`` of the ingestion ledger, in UTC."""

    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    basis: Literal["ingestion_received_at"]


class CoverageResponse(BaseModel):
    coverage_version: str
    window: CoverageWindowResponse
    counters: CoverageCounters


class ReviewSampleRequest(BaseModel):
    """``from``, ``to`` and ``seed`` are optional; ``config`` is required.

    The values are checked by the service, so each error carries its own code
    (``coverage_window_invalid``, ``seed_invalid``, ``sampling_config_invalid``,
    ``sampling_config_empty``) instead of a generic validation error.
    """

    model_config = ConfigDict(extra="forbid")

    from_: Any = Field(default=None, alias="from")
    to: Any = None
    seed: Any = None
    config: Any


class SampleSelection(BaseModel):
    item_id: str
    reasons: list[Literal["risk", "stratified", "random"]]
    stratum: dict[str, str]
    risk_flags: list[str]


class SampleShortfall(BaseModel):
    method: Literal["stratified", "random"]
    stratum: dict[str, str] | None
    requested: int
    selected: int


class SampleDrawResponse(BaseModel):
    sampler_version: str
    seed: int
    config: dict[str, Any]
    config_digest: str
    population_count: int
    scoped_count: int
    population_digest: str
    risk_flagged_count: int
    selections: list[SampleSelection]
    selection_digest: str
    shortfalls: list[SampleShortfall]


class ReviewSampleResponse(BaseModel):
    coverage_version: str
    window: CoverageWindowResponse
    draw: SampleDrawResponse
