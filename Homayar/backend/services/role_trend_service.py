"""Role-scoped trend views (HOM-82, WP15-T04).

Four views on the HOM-80 population and the HOM-79 rules:

    GET /trends/teams                                   supervisor team comparison
    GET /trends/me                                      agent self-view
    GET /organizations/{id}/trends/issues               executive aggregates
    GET /organizations/{id}/workspaces/{id}/trends/issues  analyst drill-down

Each loads one scope with the two statements of ``issue_trend_service`` (statements P and
G), judges every conversation once and splits cohorts in Python.  The disclosure rules are
the pure functions of ``issue_trend_service`` (section "disclosure"): the kind of a cohort
comes from its agents, team cohorts come from one canonical released set, and an
organization view decides once per workspace and week.  Organization views bind each
workspace as the tenant before they read it (row-level security), like
``organization_service.workspace_aggregates``.  Reads, not audited.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, select, true
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    IngestionRecord,
    IngestionStatus,
    Meeting,
    Membership,
    Organization,
    Team,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    CellKind,
    CellReason,
    SignatureJudge,
    TrendFilterError,
    TrendFilters,
    TrendWindow,
    compare_cells,
    trend_provenance,
    validate_filters,
)
from backend.issue_trends.filters import SCOPE_KEYS, FilterErrorCode
from backend.issue_trends.judgment_cache import JudgmentCache
from backend.issue_trends.samples import ASSOCIATION_NOTICE
from backend.security.authorization import (
    UNRESTRICTED_ROLES,
    OrganizationAccess,
    membership_effective_clause,
    resolve_record_access,
    supervised_team_ids,
)
from backend.security.context import WorkspaceContext
from backend.services.aggregate_metadata import scorecard_block
from backend.services.coverage_service import compute_coverage, compute_coverage_by_team
from backend.services.issue_trend_service import (
    COVERAGE_BASIS_TREND_POPULATION,
    COVERAGE_BASIS_WORKSPACE,
    GRAIN_INVALID,
    LAYER_WINDOW,
    REASON_COMPLEMENTARY,
    REASON_SMALL_GROUP,
    TREND_DEFAULT_GRAIN,
    TREND_DEFAULT_TIMEZONE,
    TREND_WEEK_STARTS_ON,
    CohortDecision,
    JudgedConversation,
    SeriesPoint,
    TeamPart,
    TeamRelease,
    TrendSeriesError,
    WeekStats,
    apply_masks,
    canonical_release,
    cohort_kind,
    cohort_parts,
    cohort_stats,
    complete_block,
    coverage_block,
    coverage_window,
    disclosure_block,
    disclosure_policy,
    filter_clauses,
    judge_population,
    organization_release,
    point_finder,
    scope_meeting_ids,
    series_block,
    series_points,
    team_filter_masks,
    team_parts,
    trend_definition,
    validate_series,
    week_passes,
    week_stats,
    weekly_release,
    withheld_block,
)
from backend.services.organization_service import scoped_workspaces

MAX_COMPARED_TEAMS = 10  # at most 45 pairs per selected row
UNADJUSTED_NOTICE = "unadjusted_comparison"  # a raw difference, not adjusted for the mix
COMPARISON_NOTICES = (ASSOCIATION_NOTICE, UNADJUSTED_NOTICE)
COMPARISON_MINIMUM = max(
    SAMPLE_POLICY_V1.min_denominator_rate, SAMPLE_POLICY_V1.min_denominator_comparison
)

# Query keys a view refuses with ``trend_parameter_not_allowed`` (a key class, checked
# right after the scope keys: the more serious refusal wins, as in HOM-79).
WORKSPACE_VIEW_REFUSED = frozenset({"agent_user_id"})
ORGANIZATION_VIEW_REFUSED = frozenset(
    {"team_id", "agent_user_id", "language", "source_type", "timezone"}
)
PARAMETER_NOT_ALLOWED = "trend_parameter_not_allowed"
WINDOW_NOT_ALIGNED = "trend_window_not_aligned"
TOO_MANY_TEAMS = "trend_too_many_teams"
TEAM_SCOPE_DENIED = "team_scope_denied"
AGENT_SCOPE_DENIED = "agent_scope_denied"

# The organization grid: whole closed weeks from Saturday 00:00 in one fixed timezone.
ORGANIZATION_GRAINS = ("none", "week")
ORGANIZATION_DEFAULT_WEEKS = 4
_GRID_ZONE = ZoneInfo(TREND_DEFAULT_TIMEZONE)
_SATURDAY = 5  # date.weekday()
_WEEK = timedelta(days=7)


class TrendViewError(ValueError):
    """A refusal of a role view: ``status_code``, ``code`` and the query ``key`` at fault."""

    def __init__(self, status_code: int, code: str, key: str | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.key = key


def query_dict(items: Iterable[tuple[str, str]]) -> dict[str, str | list[str]]:
    """Query items as HOM-80 reads them: a key given once is a string, more often a list."""
    raw: dict[str, str | list[str]] = {}
    for key, value in items:
        held = raw.get(key)
        if held is None:
            raw[key] = value
        elif isinstance(held, list):
            held.append(value)
        else:
            raw[key] = [held, value]
    return raw


def check_view_keys(raw: Mapping[str, object], refused: frozenset[str]) -> None:
    """The key classes before ``validate_filters``: a scope key (``filter_scope_not_allowed``)
    first, then a key this view does not take (``trend_parameter_not_allowed``), each class
    over its keys in sorted order."""
    keys = sorted(str(key) for key in raw)
    for key in keys:
        if key in SCOPE_KEYS:
            raise TrendFilterError(
                FilterErrorCode.SCOPE_NOT_ALLOWED, key, f"{key} is not a trend filter"
            )
    for key in keys:
        if key in refused:
            raise TrendViewError(422, PARAMETER_NOT_ALLOWED, key, f"{key} is not allowed here")


def workspace_query(
    raw: Mapping[str, str | list[str]], *, now: datetime
) -> tuple[TrendFilters, str, str]:
    """Filters, grain and timezone of ``/trends/teams`` and ``/trends/me``."""
    check_view_keys(raw, WORKSPACE_VIEW_REFUSED)
    rest = dict(raw)
    grain = rest.pop("grain", TREND_DEFAULT_GRAIN)
    timezone = rest.pop("timezone", TREND_DEFAULT_TIMEZONE)
    filters = validate_filters(rest, trend_definition(), now=now)
    validate_series(grain, timezone)
    assert isinstance(grain, str) and isinstance(timezone, str)  # validate_series checked
    return filters, grain, timezone


# --- the organization grid ------------------------------------------------------------------


def _local_midnight(day: Any) -> datetime | None:
    """Midnight of ``day`` in Asia/Tehran as a UTC instant; ``None`` outside the UTC range
    (near year 1 or 9999), like ``issue_trend_service._midnight``."""
    try:
        return datetime.combine(day, time(), tzinfo=_GRID_ZONE).astimezone(UTC)
    except OverflowError:
        return None


def current_week_start(now: datetime) -> datetime:
    """Saturday 00:00 in Asia/Tehran at or before ``now``, as a UTC instant."""
    local = now.astimezone(_GRID_ZONE)
    start = _local_midnight(local.date() - timedelta(days=(local.weekday() - _SATURDAY) % 7))
    if start is None:  # the server clock, never user input
        raise ValueError("now is outside the supported date range")
    return start


def is_week_boundary(instant: datetime) -> bool:
    """Saturday 00:00 in Asia/Tehran.  An instant that can not be shown in Asia/Tehran (near
    year 9999) is not a boundary, so it is refused as not aligned, never a 500."""
    try:
        local = instant.astimezone(_GRID_ZONE)
    except OverflowError:
        return False
    return local.weekday() == _SATURDAY and _local_midnight(local.date()) == instant


def organization_query(
    raw: Mapping[str, str | list[str]], *, now: datetime
) -> tuple[TrendFilters, str]:
    """Filters and grain of the organization views (design 4.3 step 1).

    Order: the key classes, the grid default for a missing end, ``validate_filters``,
    ``trend_grain_invalid``, then ``trend_window_not_aligned`` (``from`` aligned, ``to``
    aligned, ``to`` at or before the start of the current week).  With only ``to``,
    ``from`` is ``to`` minus 4 weeks, set after the checks of ``to``.
    """
    check_view_keys(raw, ORGANIZATION_VIEW_REFUSED)
    rest = dict(raw)
    grain = rest.pop("grain", TREND_DEFAULT_GRAIN)
    week_start = current_week_start(now)
    given_from, given_to = "from" in rest, "to" in rest
    if not given_to:
        rest["to"] = week_start.isoformat()
        if not given_from:
            rest["from"] = (week_start - ORGANIZATION_DEFAULT_WEEKS * _WEEK).isoformat()
    filters = validate_filters(rest, trend_definition(), now=now)
    if not isinstance(grain, str) or grain not in ORGANIZATION_GRAINS:
        raise TrendSeriesError(GRAIN_INVALID, "grain", "grain must be none or week here")
    window = filters.window
    if given_from and not is_week_boundary(window.start):
        raise TrendViewError(
            422, WINDOW_NOT_ALIGNED, "from", "from must be a Saturday 00:00 in Asia/Tehran"
        )
    if given_to and not is_week_boundary(window.end):
        raise TrendViewError(
            422, WINDOW_NOT_ALIGNED, "to", "to must be a Saturday 00:00 in Asia/Tehran"
        )
    if given_to and window.end > week_start:
        raise TrendViewError(
            422, WINDOW_NOT_ALIGNED, "to", "to must be at or before the start of this week"
        )
    if given_to and not given_from:
        start = window.end - ORGANIZATION_DEFAULT_WEEKS * _WEEK
        filters = replace(filters, window=TrendWindow(start=start, end=window.end))
    return filters, grain


def organization_disclosure_policy() -> dict[str, Any]:
    return {
        **disclosure_policy(),
        "grid": {
            "unit": "week",
            "timezone": TREND_DEFAULT_TIMEZONE,
            "week_starts_on": TREND_WEEK_STARTS_ON,
            "closed_weeks_only": True,
        },
    }


# --- shared parts ---------------------------------------------------------------------------


def cohort_mix(conversations: Iterable[JudgedConversation]) -> dict[str, dict[str, int]]:
    """Included conversations per language and per source type (F61: cohort differences)."""
    languages: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    for conversation in conversations:
        if conversation.included:
            languages[conversation.language] += 1
            sources[conversation.source_type] += 1
    return {
        "language": dict(sorted(languages.items())),
        "source_type": dict(sorted(sources.items())),
    }


def _accepted(clauses: Iterable[Any]) -> list[Any]:
    return [IngestionRecord.status == IngestionStatus.ACCEPTED, *clauses]


def _team_order(teams: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Name case-folded, then id: a fixed order, never a value."""
    return sorted(teams, key=lambda team: (team[1].casefold(), team[0]))


def active_teams(
    db: Session, *, workspace_id: str, team_ids: Iterable[str] | None = None
) -> list[tuple[str, str]]:
    """``(id, name)`` of the active teams of the workspace (only ``team_ids`` when given),
    in the fixed team order."""
    query = select(Team.id, Team.name).where(
        Team.workspace_id == workspace_id, Team.is_active.is_(True)
    )
    if team_ids is not None:
        query = query.where(Team.id.in_(sorted(team_ids)))
    return _team_order((team_id, name) for team_id, name in db.execute(query))


# --- comparisons ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparedTeam:
    team_id: str
    kind: CellKind
    withheld: bool  # the window layer is withheld
    rows: Sequence[Mapping[str, Any]]


def _comparison(
    left: ComparedTeam,
    right: ComparedTeam,
    left_row: Mapping[str, Any],
    right_row: Mapping[str, Any],
) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "left_team_id": left.team_id,
        "right_team_id": right.team_id,
        "level": left_row["level"],
        "family": left_row["family"],
        "issue": left_row["issue"],
        "status": "withheld",
        "reason": None,
        "difference": None,
        "minimum_sample": COMPARISON_MINIMUM,
    }
    left_cell, right_cell = left_row["cell"], right_row["cell"]
    # A small group is never compared, on any row (the qa row too); then a withheld side;
    # then a source that does not exist yet.
    if CellKind.AGENT in (left.kind, right.kind):
        return {**answer, "reason": REASON_SMALL_GROUP}
    if left.withheld or right.withheld or "withheld" in (left_cell["status"], right_cell["status"]):
        return {**answer, "reason": REASON_COMPLEMENTARY}
    unavailable = CellReason.SCORECARD_RESULTS_UNAVAILABLE.value
    if unavailable in (left_cell["reason"], right_cell["reason"]):
        return {**answer, "status": "not_computable", "reason": unavailable}
    compared = compare_cells(
        (left_cell["numerator"], left_cell["denominator"]),
        (right_cell["numerator"], right_cell["denominator"]),
        left_kind=CellKind.RATE,
        right_kind=CellKind.RATE,
        policy=SAMPLE_POLICY_V1,
    )
    return {
        **answer,
        "status": compared.status.value,
        "reason": compared.reason.value if compared.reason is not None else None,
        "difference": compared.difference,
    }


def team_comparisons(teams: Sequence[ComparedTeam]) -> list[dict[str, Any]]:
    """Every unordered pair in the given (fixed) order, one comparison per window row.
    ``left`` is the earlier team; the difference is left minus right, never a rank."""
    comparisons = []
    for index, left in enumerate(teams):
        for right in teams[index + 1 :]:
            for left_row, right_row in zip(left.rows, right.rows, strict=True):
                comparisons.append(_comparison(left, right, left_row, right_row))
    return comparisons


# --- workspace views ------------------------------------------------------------------------


def compares_every_team(context: WorkspaceContext) -> bool:
    """An owner or admin may compare every active team of the workspace."""
    return context.membership.role in UNRESTRICTED_ROLES


def comparable_teams(db: Session, context: WorkspaceContext) -> list[tuple[str, str]]:
    """The teams the caller may compare: every active team for an owner or admin, else the
    active teams where they hold an effective supervisor assignment."""
    teams = active_teams(db, workspace_id=context.workspace.id)
    if compares_every_team(context):
        return teams
    access = context.record_access
    supervised = (
        access.supervised_team_ids
        if access.restricted
        else supervised_team_ids(
            db, workspace_id=context.workspace.id, user_id=context.membership.user_id
        )
    )
    return [team for team in teams if team[0] in supervised]


def compared_teams(
    comparable: Sequence[tuple[str, str]],
    requested: Sequence[str],
    *,
    every_team: bool = False,
) -> list[tuple[str, str]]:
    """The requested teams (default: every comparable team) in the fixed order, or a
    ``TrendViewError``: ``trend_too_many_teams`` first, then ``team_scope_denied``.  An owner
    or admin (``every_team``) of a workspace with no active team gets no team, not a 403:
    there is nothing to compare.  A member or viewer with no comparable team gets the 403."""
    chosen = list(requested) if requested else [team_id for team_id, _ in comparable]
    if len(chosen) > MAX_COMPARED_TEAMS:
        raise TrendViewError(
            422, TOO_MANY_TEAMS, "team_id", f"compare at most {MAX_COMPARED_TEAMS} teams"
        )
    names = dict(comparable)
    if requested and any(team_id not in names for team_id in requested):
        raise TrendViewError(
            403, TEAM_SCOPE_DENIED, "team_id", "You may only compare teams you supervise"
        )
    if not comparable and not every_team:
        raise TrendViewError(
            403, TEAM_SCOPE_DENIED, None, "You may only compare teams you supervise"
        )
    return _team_order((team_id, names[team_id]) for team_id in chosen)


@dataclass(frozen=True)
class TeamScope:
    """The caller's scope loaded once for team cohorts: every conversation judged once,
    every team as a ``TeamPart`` and the canonical released set (``GET /trends/teams``)."""

    judged: Sequence[JudgedConversation]
    parts: Mapping[str, TeamPart]
    release: TeamRelease
    viewer: str
    points: Sequence[SeriesPoint]
    members: Mapping[str | None, Sequence[JudgedConversation]]  # by team id, in load order


def team_scope(
    db: Session,
    *,
    context: WorkspaceContext,
    filters: TrendFilters,
    points: Sequence[SeriesPoint],
    cache: JudgmentCache | None = None,
) -> TeamScope:
    """Statements P and G over the caller's whole scope (no team filter), then the
    canonical released set over every team of it, whatever the compared teams."""
    point_of = point_finder(points) if points else None
    access = context.record_access
    judged = judge_population(
        db,
        workspace_id=context.workspace.id,
        scope=access.meeting_clause(),
        filters=replace(filters, team_ids=()),
        cache=cache,
        organization_id=context.workspace.organization_id,
    )
    parts, rest = team_parts(judged, access.user_id, n_points=len(points), point_of=point_of)
    release = canonical_release(parts, rest, has_points=bool(points))
    return TeamScope(judged, parts, release, access.user_id, points, _by_team(judged))


def _by_team(
    conversations: Iterable[JudgedConversation],
) -> dict[str | None, list[JudgedConversation]]:
    """The conversations of each team id, in their order (HOM-312: one pass, not one per
    team)."""
    members: dict[str | None, list[JudgedConversation]] = defaultdict(list)
    for conversation in conversations:
        members[conversation.team_id].append(conversation)
    return members


def team_cohort(scope: TeamScope, team_id: str) -> CohortDecision:
    """One team block of ``GET /trends/teams``: its members, its kind, and the layers the
    all-or-nothing rule and the canonical set withhold.  The HOM-83 links reuse it."""
    points = scope.points
    point_of = point_finder(points) if points else None
    members = list(scope.members.get(team_id, ()))
    everyone = cohort_stats(members, n_points=len(points), point_of=point_of)
    kind, _gated = cohort_kind(everyone.agents, scope.viewer)
    masked = set(scope.parts[team_id].masked) if team_id in scope.parts else set()
    masked |= team_filter_masks(frozenset({team_id}), scope.release, has_points=bool(points))
    return CohortDecision(members, kind, frozenset(masked))


def team_comparison(
    db: Session,
    *,
    context: WorkspaceContext,
    filters: TrendFilters,
    teams: Sequence[tuple[str, str]],
    grain: str,
    timezone: str,
    recompute_max_attempts: int,
    cache: JudgmentCache | None = None,
) -> dict[str, Any]:
    """``GET /trends/teams``: one block per compared team, released from the canonical set
    of the caller's scope (every team of the scope, not only the compared ones)."""
    zone = validate_series(grain, timezone)
    definition = trend_definition()
    points = series_points(filters.window.start, filters.window.end, grain, zone)
    access = context.record_access
    workspace_id = context.workspace.id
    scope_filters = replace(filters, team_ids=())
    scope = team_scope(db, context=context, filters=filters, points=points, cache=cache)
    _whole, by_team = compute_coverage_by_team(
        db,
        workspace_id=workspace_id,
        window=coverage_window(filters),
        recompute_max_attempts=recompute_max_attempts,
        population=_accepted(filter_clauses(access.meeting_clause(), scope_filters)),
        teams=[team_id for team_id, _ in teams],
        cache=cache,
        organization_id=context.workspace.organization_id,
    )

    blocks, compared = [], []
    for team_id, name in teams:
        decision = team_cohort(scope, team_id)
        population, rows, series_answer = cohort_parts(
            decision.conversations, definition, filters, decision.kind, points, zone
        )
        rows, series_answer = apply_masks(rows, series_answer, decision.masked)
        block = complete_block(
            {
                "team": {"id": team_id, "name": name},
                "population": population,
                "mix": cohort_mix(decision.conversations),
                "rows": rows,
                "points": series_answer,
                "disclosure": disclosure_block(decision.kind, decision.masked),
            },
            coverage_block(
                filters.window, COVERAGE_BASIS_TREND_POPULATION, by_team[team_id].counters
            ),
        )
        blocks.append(block)
        compared.append(ComparedTeam(team_id, decision.kind, LAYER_WINDOW in decision.masked, rows))

    return {
        **trend_provenance(definition, SAMPLE_POLICY_V1),
        "disclosure_policy": disclosure_policy(),
        "window": filters.window.to_json(),
        "filters": filters.to_json(),
        "scope": {"restricted": access.restricted},
        "series": series_block(grain, timezone),
        "notices": list(COMPARISON_NOTICES),
        "teams": blocks,
        "comparisons": team_comparisons(compared),
    }


def _self_clause(context: WorkspaceContext) -> ColumnElement[bool]:
    """The caller's own meetings as agent, inside their record scope."""
    access = context.record_access
    return and_(access.meeting_clause(), Meeting.agent_user_id == access.user_id)


def self_cohort(
    db: Session,
    *,
    context: WorkspaceContext,
    filters: TrendFilters,
    cache: JudgmentCache | None = None,
) -> CohortDecision:
    """The cohort of ``GET /trends/me`` (statements P and G): kind ``agent``, nothing
    withheld beyond the HOM-79 agent cell.  The HOM-83 links reuse it."""
    judged = judge_population(
        db,
        workspace_id=context.workspace.id,
        scope=_self_clause(context),
        filters=filters,
        cache=cache,
        organization_id=context.workspace.organization_id,
    )
    return CohortDecision(judged, CellKind.AGENT, frozenset())


def self_view(
    db: Session,
    *,
    context: WorkspaceContext,
    filters: TrendFilters,
    grain: str,
    timezone: str,
    recompute_max_attempts: int,
    cache: JudgmentCache | None = None,
) -> dict[str, Any]:
    """``GET /trends/me``: the caller's own meetings as agent, kind ``agent``."""
    zone = validate_series(grain, timezone)
    definition = trend_definition()
    points = series_points(filters.window.start, filters.window.end, grain, zone)
    access = context.record_access
    viewer = access.user_id
    workspace_id = context.workspace.id
    decision = self_cohort(db, context=context, filters=filters, cache=cache)
    population, rows, series_answer = cohort_parts(
        decision.conversations, definition, filters, decision.kind, points, zone
    )
    counters = compute_coverage(
        db,
        workspace_id=workspace_id,
        window=coverage_window(filters),
        recompute_max_attempts=recompute_max_attempts,
        population=_accepted(filter_clauses(_self_clause(context), filters)),
        cache=cache,
        organization_id=context.workspace.organization_id,
    ).counters
    return complete_block(
        {
            **trend_provenance(definition, SAMPLE_POLICY_V1),
            "disclosure_policy": disclosure_policy(),
            "window": filters.window.to_json(),
            "filters": filters.to_json(),
            "subject": {"agent_user_id": viewer},
            "series": series_block(grain, timezone),
            "population": population,
            "rows": rows,
            "points": series_answer,
            "disclosure": disclosure_block(decision.kind, decision.masked),
        },
        coverage_block(filters.window, COVERAGE_BASIS_TREND_POPULATION, counters),
    )


# --- organization views ---------------------------------------------------------------------


def _weeks(filters: TrendFilters):
    return series_points(filters.window.start, filters.window.end, "week", _GRID_ZONE)


def organization_weeks(filters: TrendFilters) -> list[SeriesPoint]:
    """The closed weeks of an organization window (HOM-299 reads them for ``/aggregates``)."""
    return _weeks(filters)


def week_parts(
    conversations: Iterable[JudgedConversation], weeks: Sequence[Any]
) -> list[dict[str | None, WeekStats]]:
    """Per week, the stats of every team id with data that week and of the no-team part."""
    index = point_finder(weeks)
    grouped: list[dict[str | None, list[JudgedConversation]]] = [{} for _ in weeks]
    for conversation in conversations:
        if conversation.included:
            grouped[index(conversation.received_at)].setdefault(conversation.team_id, []).append(
                conversation
            )
    return [{team_id: week_stats(members) for team_id, members in week.items()} for week in grouped]


def weekly_stats(
    conversations: Iterable[JudgedConversation], weeks: Sequence[Any]
) -> list[WeekStats]:
    """The stats of the included ``conversations`` in each week."""
    index = point_finder(weeks)
    grouped: list[list[JudgedConversation]] = [[] for _ in weeks]
    for conversation in conversations:
        if conversation.included:
            grouped[index(conversation.received_at)].append(conversation)
    return [week_stats(members) for members in grouped]


def passes_outside_known(
    conversations: Sequence[JudgedConversation],
    known: frozenset[str] | None,
    weeks: Sequence[Any],
) -> bool:
    """A6: a caller who also holds a restricted membership in the workspace knows the
    meetings of its scope (``known``).  The block minus them must pass the week test in
    every week (0 or at least 5 agents, per cohort and per row).  ``None``: no such
    membership, nothing to check."""
    if known is None:
        return True
    outside = [c for c in conversations if c.judgment.meeting_id not in known]
    return all(week_passes(stats) for stats in weekly_stats(outside, weeks))


def known_meetings(
    db: Session,
    *,
    workspace: Workspace,
    user_id: str,
    filters: TrendFilters,
    now: datetime | None = None,
) -> frozenset[str] | None:
    """The meetings of the window an organization caller knows through an effective,
    restricted membership in ``workspace`` (A6): its ``meeting_clause`` (supervised teams, own
    agent meetings, own uploads).  ``None`` with no membership, or an unrestricted one (owner,
    admin, ``record_scope=workspace``), which can read every record already.  At most 3
    statements per workspace: the membership, the assignments, the meetings."""
    now = now or datetime.now(UTC)
    membership = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == user_id,
            membership_effective_clause(now),
        )
    )
    if membership is None:
        return None
    access = resolve_record_access(db, workspace=workspace, membership=membership, now=now)
    if access.unrestricted:
        return None
    return scope_meeting_ids(
        db, workspace_id=workspace.id, scope=access.meeting_clause(), filters=filters
    )


def caller_release(
    judged: Sequence[JudgedConversation],
    parts: Sequence[Mapping[str | None, WeekStats]],
    known: frozenset[str] | None,
    weeks: Sequence[Any],
) -> list[frozenset[str]]:
    """The per-week release set for this caller.  Without ``known`` it is ``Rel_k``.  With
    a restricted membership (A6b) it is ``Rel_k`` and, in the same week, ``Rel_out_k``: the
    same weekly decision over the conversations outside what the caller knows.  So the
    workspace minus the released teams minus the caller's scope is a remainder that passed
    the week test too, not only each block on its own.  Python only, no statement; it does
    not read the grant, so a change of grant gives the same set."""
    rel = weekly_release(parts)
    if known is None:
        return rel
    outside = [c for c in judged if c.judgment.meeting_id not in known]
    rel_out = weekly_release(week_parts(outside, weeks))
    return [inside & out for inside, out in zip(rel, rel_out, strict=True)]


def _decide(
    parts: Sequence[Mapping[str | None, WeekStats]],
    rel: Sequence[frozenset[str]],
    team_ids: frozenset[str] | None,
    members: Sequence[JudgedConversation],
    known: frozenset[str] | None,
    weeks: Sequence[Any],
) -> tuple[bool, str | None]:
    """``organization_release``, then the A6 test of the caller's known meetings."""
    released, reason = organization_release(parts, rel, team_ids)
    if released and not passes_outside_known(members, known, weeks):
        return False, REASON_COMPLEMENTARY
    return released, reason


def _organization_block(
    released: bool,
    reason: str | None,
    members: Sequence[JudgedConversation],
    *,
    filters: TrendFilters,
    grain: str,
    weeks: Sequence[Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    if not released:
        return {
            "population": None,
            "rows": [],
            "points": [],
            "coverage": None,
            "scorecard": scorecard_block(),
            "missing_data": None,
            "disclosure": {**withheld_block(), "reason": reason},
        }
    points = weeks if grain == "week" else []
    population, rows, series_answer = cohort_parts(
        members, trend_definition(), filters, CellKind.RATE, points, _GRID_ZONE
    )
    return complete_block(
        {
            "population": population,
            "rows": rows,
            "points": series_answer,
            "disclosure": disclosure_block(CellKind.RATE, frozenset()),
        },
        coverage,
    )


def _organization_answer(filters: TrendFilters, grain: str) -> dict[str, Any]:
    return {
        **trend_provenance(trend_definition(), SAMPLE_POLICY_V1),
        "disclosure_policy": organization_disclosure_policy(),
        "window": filters.window.to_json(),
        "filters": filters.to_json(),
        "series": series_block(grain, TREND_DEFAULT_TIMEZONE),
    }


def _granted(team_ids: frozenset[str] | None) -> list[Any]:
    return [] if team_ids is None else _accepted([Meeting.team_id.in_(sorted(team_ids))])


def _workspace_population(
    db: Session,
    workspace: Workspace,
    filters: TrendFilters,
    judge: SignatureJudge | None = None,
    cache: JudgmentCache | None = None,
):
    return judge_population(
        db,
        workspace_id=workspace.id,
        scope=true(),
        filters=filters,
        judge=judge,
        cache=cache,
        organization_id=workspace.organization_id,
    )


def organization_workspace_block(
    db: Session,
    *,
    workspace: Workspace,
    team_ids: frozenset[str] | None,
    user_id: str,
    filters: TrendFilters,
    grain: str,
    weeks: Sequence[Any],
    recompute_max_attempts: int,
    judge: SignatureJudge | None = None,
    cache: JudgmentCache | None = None,
) -> dict[str, Any]:
    """The executive view's block of one scoped workspace or team grant, with the workspace
    already bound as the tenant: statements P and G, at most 3 for A6, and 9 coverage
    statements when released.  The executive view and the HOM-299 metadata of
    ``/aggregates`` both run it, so their ``coverage``, ``scorecard``, ``missing_data`` and
    ``disclosure`` are the same (ADR 0041, L2)."""
    judged = _workspace_population(db, workspace, filters, judge, cache)
    known = known_meetings(db, workspace=workspace, user_id=user_id, filters=filters)
    parts = week_parts(judged, weeks)
    members = judged if team_ids is None else [c for c in judged if c.team_id in team_ids]
    rel = caller_release(judged, parts, known, weeks)
    released, reason = _decide(parts, rel, team_ids, members, known, weeks)
    coverage: dict[str, Any] = {}
    if released:
        counters = compute_coverage(
            db,
            workspace_id=workspace.id,
            window=coverage_window(filters),
            recompute_max_attempts=recompute_max_attempts,
            population=_granted(team_ids),
            cache=cache,
            organization_id=workspace.organization_id,
        ).counters
        basis = COVERAGE_BASIS_WORKSPACE if team_ids is None else COVERAGE_BASIS_TREND_POPULATION
        coverage = coverage_block(filters.window, basis, counters)
    return _organization_block(
        released,
        reason,
        members,
        filters=filters,
        grain=grain,
        weeks=weeks,
        coverage=coverage,
    )


def organization_trends(
    db: Session,
    *,
    organization: Organization,
    access: OrganizationAccess,
    filters: TrendFilters,
    grain: str,
    recompute_max_attempts: int,
    cache: JudgmentCache | None = None,
) -> dict[str, Any]:
    """Executive aggregates: one block per scoped workspace (the grant unit).  Each
    workspace is loaded whole, also for a team grant: the release decision is one per
    workspace and week (design 4.3, M1)."""
    weeks = _weeks(filters)
    judge = SignatureJudge(trend_definition())
    items = []
    for workspace, team_ids in scoped_workspaces(db, organization=organization, access=access):
        bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
        items.append(
            {
                "workspace_id": workspace.id,
                "workspace_name": workspace.name,
                "team_ids": sorted(team_ids) if team_ids is not None else None,
                **organization_workspace_block(
                    db,
                    workspace=workspace,
                    team_ids=team_ids,
                    user_id=access.user_id,
                    filters=filters,
                    grain=grain,
                    weeks=weeks,
                    recompute_max_attempts=recompute_max_attempts,
                    judge=judge,
                    cache=cache,
                ),
            }
        )
    return {
        "organization_id": organization.id,
        **_organization_answer(filters, grain),
        "notices": [ASSOCIATION_NOTICE],
        "workspaces": items,
    }


def organization_drilldown(
    db: Session,
    *,
    organization: Organization,
    workspace: Workspace,
    team_ids: frozenset[str] | None,
    user_id: str,
    filters: TrendFilters,
    grain: str,
    recompute_max_attempts: int,
    cache: JudgmentCache | None = None,
) -> dict[str, Any]:
    """Analyst drill-down: the grant unit and one block per active team inside it.  Every
    block is released per week over the window (design 4.3), and tested against what the
    caller knows through a restricted membership in the workspace (A6)."""
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
    weeks = _weeks(filters)
    teams = active_teams(db, workspace_id=workspace.id, team_ids=team_ids)
    judged = _workspace_population(db, workspace, filters, cache=cache)
    known = known_meetings(db, workspace=workspace, user_id=user_id, filters=filters)
    parts = week_parts(judged, weeks)
    rel = caller_release(judged, parts, known, weeks)
    whole, by_team = compute_coverage_by_team(
        db,
        workspace_id=workspace.id,
        window=coverage_window(filters),
        recompute_max_attempts=recompute_max_attempts,
        population=_granted(team_ids),
        teams=[team_id for team_id, _ in teams],
        cache=cache,
        organization_id=organization.id,
    )
    options = {"filters": filters, "grain": grain, "weeks": weeks}
    members = judged if team_ids is None else [c for c in judged if c.team_id in team_ids]
    released, reason = _decide(parts, rel, team_ids, members, known, weeks)
    basis = COVERAGE_BASIS_WORKSPACE if team_ids is None else COVERAGE_BASIS_TREND_POPULATION
    workspace_block = _organization_block(
        released,
        reason,
        members,
        coverage=coverage_block(filters.window, basis, whole.counters),
        **options,
    )
    team_blocks = []
    members_by_team = _by_team(judged)
    for team_id, name in teams:
        team_members = list(members_by_team.get(team_id, ()))
        team_released, team_reason = _decide(
            parts, rel, frozenset({team_id}), team_members, known, weeks
        )
        team_blocks.append(
            {
                "team": {"id": team_id, "name": name},
                **_organization_block(
                    team_released,
                    team_reason,
                    team_members,
                    coverage=coverage_block(
                        filters.window,
                        COVERAGE_BASIS_TREND_POPULATION,
                        by_team[team_id].counters,
                    ),
                    **options,
                ),
            }
        )
    return {
        "organization_id": organization.id,
        "workspace_id": workspace.id,
        "workspace_name": workspace.name,
        "team_ids": sorted(team_ids) if team_ids is not None else None,
        **_organization_answer(filters, grain),
        "notices": list(COMPARISON_NOTICES),
        "workspace": workspace_block,
        "teams": team_blocks,
    }
