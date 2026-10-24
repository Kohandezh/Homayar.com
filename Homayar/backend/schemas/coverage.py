"""Coverage counters and review sample draws (HOM-72, WP14-T01), the scorecard and
missing-data blocks every aggregate carries (HOM-81, WP15-T03), and the population coverage
of the voicemail aggregates (HOM-299)."""

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


MissingDataIndicator = Literal[
    "ingestion_incomplete",
    "processing_incomplete",
    "evaluation_incomplete",
    "review_incomplete",
    "ingestion_unavailable",
    "processing_unavailable",
    "evaluation_unavailable",
    "review_unavailable",
    "scorecard_unavailable",
    "empty_population",
    "missing_values",
    "pending_review",
    "insufficient_sample",
    "source_unavailable",
]


class AggregateScorecard(BaseModel):
    """The scorecard state of an aggregate.  Scorecards (HOM-54) do not exist yet, so it
    is always ``not_available``; HOM-54 adds ``available`` with a version and digest."""

    status: Literal["not_available"]
    version: str | None
    digest: str | None
    reason: Literal["scorecard_results_unavailable"] | None


class AggregateMissingData(BaseModel):
    """Sorted, distinct codes, each a pure function of the same response
    (``services/aggregate_metadata.py``)."""

    indicators: list[MissingDataIndicator]


class PopulationCoverageWindow(BaseModel):
    """``from <= <basis column> < to`` of a population that is not the ledger (HOM-299)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    basis: Literal["voicemail_received_at"]


class PopulationCoverage(BaseModel):
    """The ``coverage/1`` processing and evaluation counters over a population of rows
    that is not the ledger (HOM-299, ADR 0041).  ``window.basis`` names the column the
    window filters; ``basis`` names the population: the voicemail rows of
    ``GET /voicemails/analytics``."""

    version: Literal["coverage/1"]
    window: PopulationCoverageWindow
    basis: Literal["voicemail_population"]
    processing: CoverageCounter
    evaluation: CoverageCounter


class CoverageResponse(BaseModel):
    coverage_version: str
    window: CoverageWindowResponse
    counters: CoverageCounters
    scorecard: AggregateScorecard
    missing_data: AggregateMissingData


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
