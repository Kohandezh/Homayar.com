"""Issue-trend aggregates (HOM-80, WP15-T02): the answer of ``GET /trends/issues``.

The shapes are the HOM-79 ones (``backend/issue_trends/README.md``, "A trend answer"):
``trend_provenance``, ``TrendWindow.to_json()``, ``TrendFilters.to_json()`` and
``TrendRow.to_json()``.  Instants are the strings those functions write, never
re-serialized: the window in UTC (``+00:00``), the points in the series timezone.

HOM-82 adds the role views (team comparison, self-view, executive aggregates, analyst
drill-down), the ``disclosure`` block of every cohort and the ``withheld`` cell.

HOM-83 adds the notice ``correlation_not_cause`` to every trend answer, and the answer of
``GET /trends/conversations``: the conversations behind one cell, as ids only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.coverage import AggregateMissingData, AggregateScorecard, CoverageCounter


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
    """``withheld`` (HOM-82): the counts are held back so that no hidden count of a small
    group can be rebuilt; the denominator stays."""

    status: Literal["computed", "not_computable", "insufficient_sample", "withheld"]
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


class TrendCoverage(BaseModel):
    """The ``coverage/1`` processing and evaluation counters of the trend window (HOM-81).

    ``workspace``: the counters of ``GET /admin/coverage``.  ``trend_population``: the same
    rules over the ledger rows whose meeting passes the caller's scope and the population
    filters."""

    version: Literal["coverage/1"]
    window: TrendWindowResponse
    basis: Literal["workspace", "trend_population"]
    processing: CoverageCounter
    evaluation: CoverageCounter


class TrendDisclosurePolicy(BaseModel):
    """The disclosure rules of the answer (HOM-82): a small group has fewer than
    ``min_cohort_agents`` distinct agents."""

    version: str
    min_cohort_agents: int


class TrendGrid(BaseModel):
    """The fixed weekly grid of the organization views."""

    unit: Literal["week"]
    timezone: str
    week_starts_on: Literal["saturday"]
    closed_weeks_only: bool


class OrganizationDisclosurePolicy(TrendDisclosurePolicy):
    grid: TrendGrid


class TrendDisclosure(BaseModel):
    """What HOM-82 held back in one cohort.  ``released``: nothing; ``masked``: the
    ``masked`` layers are withheld (workspace views); ``withheld``: no count at all
    (organization views)."""

    status: Literal["released", "masked", "withheld"]
    reason: Literal["small_group", "complementary_disclosure"] | None
    kind: Literal["rate", "agent"] | None
    masked: list[Literal["points", "window"]]


class TrendProvenance(BaseModel):
    trends_version: str
    definition: TrendDefinitionRef
    taxonomy: TrendTaxonomyRef
    extraction_policies: list[TrendVersionRef]
    validator_versions: list[str]
    sample_policy: TrendVersionRef


class IssueTrendResponse(TrendProvenance):
    disclosure_policy: TrendDisclosurePolicy
    notices: list[str]  # HOM-83: ``correlation_not_cause``
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    scope: TrendScope
    series: TrendSeries
    population: TrendPopulation
    rows: list[TrendRowResponse]
    points: list[TrendPoint]
    coverage: TrendCoverage
    scorecard: AggregateScorecard
    missing_data: AggregateMissingData
    disclosure: TrendDisclosure


# --- HOM-82 role views ----------------------------------------------------------------------


class TrendCohortBlock(BaseModel):
    """One cohort of a workspace view, with the HOM-81 blocks over its own population."""

    population: TrendPopulation
    rows: list[TrendRowResponse]
    points: list[TrendPoint]
    coverage: TrendCoverage
    scorecard: AggregateScorecard
    missing_data: AggregateMissingData
    disclosure: TrendDisclosure


class TrendTeamRef(BaseModel):
    id: str
    name: str


class TrendMix(BaseModel):
    """Included conversations per language and per source type (F61)."""

    language: dict[str, int]
    source_type: dict[str, int]


class TeamTrendBlock(TrendCohortBlock):
    team: TrendTeamRef
    mix: TrendMix


class TeamComparison(BaseModel):
    """``compare_cells`` of two teams for one window row: left minus right, in the fixed
    team order.  A difference, never a rank or a cause."""

    left_team_id: str
    right_team_id: str
    level: Literal["family", "issue"]
    family: str
    issue: str | None
    status: Literal["computed", "not_computable", "insufficient_sample", "withheld"]
    reason: str | None
    difference: float | None
    minimum_sample: int


class TeamComparisonResponse(TrendProvenance):
    disclosure_policy: TrendDisclosurePolicy
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    scope: TrendScope
    series: TrendSeries
    notices: list[str]
    teams: list[TeamTrendBlock]
    comparisons: list[TeamComparison]


class TrendSubject(BaseModel):
    agent_user_id: str


class SelfTrendResponse(TrendProvenance):
    disclosure_policy: TrendDisclosurePolicy
    notices: list[str]  # HOM-83: ``correlation_not_cause``
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    subject: TrendSubject
    series: TrendSeries
    population: TrendPopulation
    rows: list[TrendRowResponse]
    points: list[TrendPoint]
    coverage: TrendCoverage
    scorecard: AggregateScorecard
    missing_data: AggregateMissingData
    disclosure: TrendDisclosure


class OrganizationTrendBlock(BaseModel):
    """One cohort of an organization view.  A withheld cohort has no count at all:
    ``population``, ``coverage`` and ``missing_data`` are null, ``rows`` and ``points``
    empty."""

    population: TrendPopulation | None
    rows: list[TrendRowResponse]
    points: list[TrendPoint]
    coverage: TrendCoverage | None
    scorecard: AggregateScorecard
    missing_data: AggregateMissingData | None
    disclosure: TrendDisclosure


class OrganizationWorkspaceTrend(OrganizationTrendBlock):
    workspace_id: str
    workspace_name: str
    team_ids: list[str] | None


class OrganizationTeamTrend(OrganizationTrendBlock):
    team: TrendTeamRef


class OrganizationTrendResponse(TrendProvenance):
    organization_id: str
    disclosure_policy: OrganizationDisclosurePolicy
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    series: TrendSeries
    notices: list[str]  # HOM-83: ``correlation_not_cause``
    workspaces: list[OrganizationWorkspaceTrend]


class OrganizationDrilldownResponse(TrendProvenance):
    organization_id: str
    workspace_id: str
    workspace_name: str
    team_ids: list[str] | None
    disclosure_policy: OrganizationDisclosurePolicy
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    series: TrendSeries
    notices: list[str]
    workspace: OrganizationTrendBlock
    teams: list[OrganizationTeamTrend]


# --- HOM-83 supporting conversations ----------------------------------------------------------


class TrendLinkRow(BaseModel):
    """The row of the cell: a family row (``issue`` null) or an issue row."""

    level: Literal["family", "issue"]
    family: str
    issue: str | None


class TrendLinkPoint(BaseModel):
    """The point of the cell, by its index in the view's ``points``."""

    model_config = ConfigDict(populate_by_name=True)

    index: int
    from_: str = Field(alias="from")
    to: str
    partial: bool


class TrendLinkPage(BaseModel):
    """``total`` is the cell's numerator, a number the view shows already."""

    offset: int
    limit: int
    total: int


class TrendLinkConversation(BaseModel):
    """One conversation of the numerator, as ids only.  ``evidence_ids`` are the extraction
    evidence spans of the judged run that support the row; the list can be empty (no valid
    turn was cited) and that does not mean "not counted"."""

    meeting_id: str
    state: Literal["counted"]
    analysis_run_id: str
    evidence_ids: list[str]


class TrendLinkResponse(TrendProvenance):
    """The conversations behind one ``computed`` cell of a workspace trend view.  They are
    the conversations where the issue was counted, not a cause of the trend."""

    disclosure_policy: TrendDisclosurePolicy
    notices: list[str]
    trend_view: Literal["issues", "teams", "me"]
    window: TrendWindowResponse
    filters: TrendFiltersResponse
    scope: TrendScope
    series: TrendSeries
    team: TrendTeamRef | None
    row: TrendLinkRow
    point: TrendLinkPoint | None
    cell: TrendCell
    disclosure: TrendDisclosure
    page: TrendLinkPage
    conversations: list[TrendLinkConversation]
