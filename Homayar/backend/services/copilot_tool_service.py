"""Read-only, allowlisted analytical tools of the Copilot (HOM-96, WP17-T05): ``run_tool``.

A model may name a tool and its params.  Both are untrusted.  ``run_tool`` runs only a tool
of ``copilot_tools.TOOLS``, with fixed, parameterised SQLAlchemy queries (never SQL text
from a param), inside the caller's workspace and record scope (``meeting_clause``, and RLS
on PostgreSQL), under row and time limits and a statement timeout.  Every number is exact
and comes with the record ids behind it (citations).  A question it cannot answer exactly
abstains.

The check order is in ``copilot_tools/README.md`` ("The check order").  The service reads
no clock except ``time.monotonic()`` for the time budget.  It runs only ``SELECT``
statements and the savepoint commands: it never writes, never flushes and never commits.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from fractions import Fraction
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.copilot_intents import (
    CallerScope,
    IntentCatalogue,
    IntentRequest,
    Route,
    RoutingDecision,
    gate_aggregate,
    load_catalogue,
    route_request,
)
from backend.copilot_tools.params import (
    ActionItemsDueParams,
    ConversationCountParams,
    IssueAggregateParams,
    ParamError,
    ScorecardSummaryParams,
    TopIssuesParams,
    Window,
    check_params,
)
from backend.copilot_tools.registry import DEFAULT_LIMITS, TOOLS, ToolLimits, check_limits
from backend.copilot_tools.results import (
    AbstainReason,
    Fact,
    ParamDefect,
    ToolResult,
    round_half_even_2,
)
from backend.database.saas_models import (
    ActionItem,
    ActionStatus,
    Meeting,
    ScorecardResultRecord,
)
from backend.database.tenant_context import current_context
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    SCOPE_KEYS,
    UNSUPPORTED_KEYS,
    Cell,
    CellKind,
    CellReason,
    CellStatus,
    IssueState,
    trend_provenance,
)
from backend.issue_trends.samples import MIN_COHORT_AGENTS
from backend.security.context import WorkspaceContext
from backend.services.issue_trend_service import (
    CELL_WITHHELD,
    TREND_DEFAULT_TIMEZONE,
    JudgedConversation,
    SeriesPoint,
    TrendSeriesError,
    apply_masks,
    cohort_parts,
    disclosure_block,
    issue_cohort,
    point_finder,
    series_points,
    trend_definition,
)
from backend.services.trend_link_service import LinkRow, row_state

# The intents ``issue_aggregate`` answers.  Every other answered intent abstains.
ISSUE_INTENTS = ("issue_count", "issue_rate", "issue_trend")
TIME_GRAINS = ("day", "week", "month")
CANCELLED_SQLSTATE = "57014"  # PostgreSQL query_canceled (statement_timeout)

_READ_TIMEOUT = text("SELECT current_setting('statement_timeout')")
_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :value, true)")

_Runner = Callable[[Session, WorkspaceContext, Any, datetime, ToolLimits], ToolResult]


class _Abstain(Exception):
    """Inside a runner: give up with this reason (no number)."""

    def __init__(self, reason: AbstainReason, detail: str | None = None, key: str | None = None):
        super().__init__(reason.value)
        self.reason = reason
        self.detail = detail
        self.key = key


# A4.4: the public name of the abstention above, so a caller outside this
# module (the Copilot evidence tool) never imports the private class.
ToolAbstention = _Abstain


def _aware(now: object) -> bool:
    return isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None


def _utc(value: datetime) -> datetime:
    """SQLite gives back a naive datetime; it is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _zone() -> ZoneInfo:
    return ZoneInfo(TREND_DEFAULT_TIMEZONE)


def _points(window: Window, grain: str) -> list[SeriesPoint]:
    """The series points of ``window`` (``[]`` for ``none``).  A window that cannot be shown
    in the series timezone (near year 1 or 9999) is a window defect."""
    assert window.start is not None
    try:
        return series_points(window.start, window.end, grain, _zone())
    except TrendSeriesError as exc:
        raise _Abstain(AbstainReason.PARAMS_INVALID, "filter_window_invalid", None) from exc


def _series(grain: str) -> dict[str, str] | None:
    return {"grain": grain, "timezone": TREND_DEFAULT_TIMEZONE} if grain in TIME_GRAINS else None


def _point_group(index: int, point: SeriesPoint) -> dict[str, Any]:
    zone = _zone()
    return {
        "point": index,
        "from": point.start.astimezone(zone).isoformat(),
        "to": point.end.astimezone(zone).isoformat(),
        "partial": point.partial,
    }


def _null_first(value: str | None) -> tuple[bool, str]:
    return (value is not None, value or "")


def _bounded(db: Session, statement: Any, limits: ToolLimits) -> list[Any]:
    """The rows of ``statement`` (ordered by id, ``LIMIT max_rows + 1``).  More than
    ``max_rows`` rows abstain: never a partial number."""
    rows = db.execute(statement.limit(limits.max_rows + 1)).all()
    if len(rows) > limits.max_rows:
        raise _Abstain(AbstainReason.ROW_LIMIT_EXCEEDED)
    return rows


def _grouped(
    name: str,
    items: Iterable[tuple[str | None, str]],
    group_key: str,
    limits: ToolLimits,
) -> list[Fact]:
    """One fact per present group value, ascending with ``null`` first."""
    groups: dict[str | None, list[str]] = defaultdict(list)
    for value, record_id in items:
        groups[value].append(record_id)
    return [
        Fact.cited(
            name,
            value=len(groups[value]),
            ids=groups[value],
            max_citations=limits.max_citations,
            group={group_key: value},
        )
        for value in sorted(groups, key=_null_first)
    ]


def _meeting_scope(context: WorkspaceContext) -> list[ColumnElement[bool]]:
    """The common rules of every tool: the caller's workspace, live meetings and the
    caller's record scope.  Added by the service, never by a param."""
    return [
        Meeting.workspace_id == context.workspace.id,
        Meeting.deleted_at.is_(None),
        context.record_access.meeting_clause(),
    ]


# --- conversation_count ---------------------------------------------------------------------


def _conversation_count(
    db: Session,
    context: WorkspaceContext,
    params: ConversationCountParams,
    now: datetime,
    limits: ToolLimits,
) -> ToolResult:
    window = params.window
    grain = params.group_by
    points = _points(window, grain) if grain in TIME_GRAINS else []
    clauses = [
        *_meeting_scope(context),
        Meeting.created_at >= window.start,
        Meeting.created_at < window.end,
    ]
    if params.team_ids:
        clauses.append(Meeting.team_id.in_(params.team_ids))
    if params.agent_user_ids:
        clauses.append(Meeting.agent_user_id.in_(params.agent_user_ids))
    rows = _bounded(
        db,
        select(Meeting.id, Meeting.status, Meeting.team_id, Meeting.created_at)
        .where(*clauses)
        .order_by(Meeting.id),
        limits,
    )
    ids = [row.id for row in rows]
    facts = [
        Fact.cited("conversations", value=len(ids), ids=ids, max_citations=limits.max_citations)
    ]
    if grain == "status":
        facts += _grouped(
            "conversations", ((row.status.value, row.id) for row in rows), "status", limits
        )
    elif grain == "team_id":
        facts += _grouped(
            "conversations", ((row.team_id, row.id) for row in rows), "team_id", limits
        )
    elif points:
        index = point_finder(points)
        per_point: list[list[str]] = [[] for _ in points]
        for row in rows:
            per_point[index(_utc(row.created_at))].append(row.id)
        facts += [
            Fact.cited(
                "conversations",
                value=len(members),
                ids=members,
                max_citations=limits.max_citations,
                group=_point_group(position, point),
            )
            for position, (point, members) in enumerate(zip(points, per_point, strict=True))
        ]
    return ToolResult.answered(
        "conversation_count",
        filters={
            "window": window.to_json(),
            "team_id": list(params.team_ids),
            "agent_user_id": list(params.agent_user_ids),
            "group_by": grain,
            "series": _series(grain),
        },
        facts=facts,
        restricted=context.record_access.restricted,
        limits=limits,
    )


# --- action_items_due -----------------------------------------------------------------------


def _action_items_due(
    db: Session,
    context: WorkspaceContext,
    params: ActionItemsDueParams,
    now: datetime,
    limits: ToolLimits,
) -> ToolResult:
    window = params.window
    clauses = [
        *_meeting_scope(context),
        ActionItem.workspace_id == context.workspace.id,
        ActionItem.due_at.is_not(None),
        ActionItem.due_at < window.end,
        ActionItem.status.in_([ActionStatus(value) for value in params.statuses]),
    ]
    if window.start is not None:
        clauses.append(ActionItem.due_at >= window.start)
    if params.team_ids:
        clauses.append(Meeting.team_id.in_(params.team_ids))
    if params.assignee_user_ids:
        clauses.append(ActionItem.assignee_user_id.in_(params.assignee_user_ids))
    rows = _bounded(
        db,
        select(
            ActionItem.id,
            ActionItem.status,
            ActionItem.priority,
            ActionItem.due_at,
            Meeting.team_id,
        )
        .select_from(ActionItem)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(*clauses)
        .order_by(ActionItem.id),
        limits,
    )
    now_utc = now.astimezone(UTC)
    ids = [row.id for row in rows]
    overdue = [row.id for row in rows if _utc(row.due_at) < now_utc]
    facts = [
        Fact.cited("action_items_due", value=len(ids), ids=ids, max_citations=limits.max_citations),
        Fact.cited(
            "action_items_overdue",
            value=len(overdue),
            ids=overdue,
            max_citations=limits.max_citations,
        ),
    ]
    if params.group_by == "status":
        facts += _grouped(
            "action_items_due", ((row.status.value, row.id) for row in rows), "status", limits
        )
    elif params.group_by == "priority":
        facts += _grouped(
            "action_items_due", ((row.priority, row.id) for row in rows), "priority", limits
        )
    elif params.group_by == "team_id":
        facts += _grouped(
            "action_items_due", ((row.team_id, row.id) for row in rows), "team_id", limits
        )
    return ToolResult.answered(
        "action_items_due",
        filters={
            "window": window.to_json(),
            "team_id": list(params.team_ids),
            "assignee_user_id": list(params.assignee_user_ids),
            "status": list(params.statuses),
            "group_by": params.group_by,
        },
        facts=facts,
        restricted=context.record_access.restricted,
        limits=limits,
    )


# --- scorecard_summary ----------------------------------------------------------------------


def _scorecard_minimum(params: ScorecardSummaryParams, agents: set[str]) -> int:
    """The ``SAMPLE_POLICY_V1`` minimum: the agent minimum for an agent filter or 1 to 4
    distinct agents (a small group), else the rate minimum."""
    if params.agent_user_ids or 1 <= len(agents) < MIN_COHORT_AGENTS:
        return SAMPLE_POLICY_V1.min_denominator_agent
    return SAMPLE_POLICY_V1.min_denominator_rate


def _scorecard_summary(
    db: Session,
    context: WorkspaceContext,
    params: ScorecardSummaryParams,
    now: datetime,
    limits: ToolLimits,
) -> ToolResult:
    window = params.window
    result = ScorecardResultRecord
    clauses = [
        *_meeting_scope(context),
        result.workspace_id == context.workspace.id,
        result.scorecard_version_id == params.scorecard_version_id,
        Meeting.created_at >= window.start,
        Meeting.created_at < window.end,
    ]
    if params.team_ids:
        clauses.append(Meeting.team_id.in_(params.team_ids))
    if params.agent_user_ids:
        clauses.append(Meeting.agent_user_id.in_(params.agent_user_ids))
    rows = _bounded(
        db,
        select(
            result.id,
            result.meeting_id,
            result.outcome,
            result.applicable_weight,
            result.earned_weight,
            result.created_at,
            Meeting.agent_user_id,
        )
        .select_from(result)
        .join(Meeting, Meeting.id == result.meeting_id)
        .where(*clauses)
        .order_by(result.id),
        limits,
    )
    newest: dict[str, Any] = {}
    for row in rows:  # one result per meeting: the newest by (created_at, id)
        held = newest.get(row.meeting_id)
        if held is None or (_utc(row.created_at), row.id) > (_utc(held.created_at), held.id):
            newest[row.meeting_id] = row
    chosen = sorted(newest.values(), key=lambda row: row.id)
    agents = {row.agent_user_id for row in chosen if row.agent_user_id is not None}
    if len(chosen) < _scorecard_minimum(params, agents):
        raise _Abstain(AbstainReason.BELOW_MINIMUM_SAMPLE)
    scored = [row for row in chosen if row.applicable_weight > 0]
    if not scored:
        raise _Abstain(AbstainReason.NOT_COMPUTABLE)
    mean = sum(
        (Fraction(row.earned_weight * 100, row.applicable_weight) for row in scored), Fraction(0)
    ) / len(scored)
    facts = [
        Fact.cited(
            "scorecard_results",
            value=len(chosen),
            ids=[row.id for row in chosen],
            max_citations=limits.max_citations,
        ),
        *_grouped(
            "scorecard_results", ((row.outcome, row.id) for row in chosen), "outcome", limits
        ),
        Fact.cited(
            "mean_score_percent",
            value=round_half_even_2(mean),
            ids=[row.id for row in scored],
            max_citations=limits.max_citations,
            parts=(mean.numerator, mean.denominator),
        ),
    ]
    return ToolResult.answered(
        "scorecard_summary",
        filters={
            "scorecard_version_id": params.scorecard_version_id,
            "window": window.to_json(),
            "team_id": list(params.team_ids),
            "agent_user_id": list(params.agent_user_ids),
        },
        facts=facts,
        restricted=context.record_access.restricted,
        limits=limits,
    )


# --- the issue tools (HOM-92 intents) -------------------------------------------------------


@cache
def _catalogue() -> IntentCatalogue:
    """The HOM-92 catalogue, version 1.  Loaded once per process (it is immutable and
    pinned by its digest)."""
    return load_catalogue()


def caller_scope(context: WorkspaceContext) -> CallerScope:
    """The HOM-92 ``CallerScope`` of a caller: the whole workspace when unrestricted, else
    the supervised teams.  Agents are never narrowed here; the SQL always ANDs
    ``meeting_clause()``, so the caller's own calls stay visible and nothing outside is
    counted."""
    access = context.record_access
    return CallerScope(
        workspace_id=context.workspace.id,
        team_ids=None if access.unrestricted else frozenset(access.supervised_team_ids),
        agent_user_ids=None,
    )


def _route(context: WorkspaceContext, intent: str, request: Mapping[str, object], now: datetime):
    decision = route_request(
        IntentRequest(intent, request), _catalogue(), scope=caller_scope(context), now=now
    )
    if decision.route is Route.REFUSE:
        assert decision.reason is not None
        raise _Abstain(AbstainReason(decision.reason.value), decision.detail, decision.key)
    return decision


def _row_name(row: Mapping[str, Any]) -> str:
    return f"issue:{row['issue']}" if row["level"] == "issue" else f"family:{row['family']}"


def _link_row(row: Mapping[str, Any]) -> LinkRow:
    return LinkRow(row["level"], row["family"], row["issue"])


def _cell(data: Mapping[str, Any]) -> Cell:
    """The ``issue_trends`` cell of a cell JSON, with the same field values (no rounding
    change: the JSON of the rebuilt cell must be equal)."""
    reason = data["reason"]
    cell = Cell(
        status=CellStatus(data["status"]),
        numerator=data["numerator"],
        denominator=data["denominator"],
        ratio=data["ratio"],
        reason=None if reason is None else CellReason(reason),
        minimum_sample=data["minimum_sample"],
    )
    if cell.to_json() != dict(data):
        raise RuntimeError("a trend cell changed when it was rebuilt")
    return cell


def _gate(decision: RoutingDecision, cells: Sequence[Mapping[str, Any]]) -> None:
    gated = gate_aggregate(decision, [_cell(cell) for cell in cells])
    if gated.route is Route.ABSTAIN:
        assert gated.reason is not None
        raise _Abstain(AbstainReason(gated.reason.value), gated.detail, None)


def _withheld(rows: Iterable[Mapping[str, Any]]) -> None:
    if any(row["cell"]["status"] == CELL_WITHHELD for row in rows):
        raise _Abstain(AbstainReason.WITHHELD)


class _Counted:
    """The included conversations whose state for a row is ``counted`` (HOM-83 rule of
    ``trend_links``), for the window and per point, as meeting ids."""

    def __init__(
        self, conversations: Sequence[JudgedConversation], points: Sequence[SeriesPoint]
    ) -> None:
        self._included = [c for c in conversations if c.included]
        index = point_finder(points) if points else None
        self._point_of = [index(c.received_at) if index else None for c in self._included]
        self._points = len(points)
        self._cache: dict[tuple[str, str, str | None], tuple[list[str], list[list[str]]]] = {}

    def of(self, row: Mapping[str, Any]) -> tuple[list[str], list[list[str]]]:
        key = (row["level"], row["family"], row["issue"])
        if key not in self._cache:
            link = _link_row(row)
            window: list[str] = []
            points: list[list[str]] = [[] for _ in range(self._points)]
            for conversation, point in zip(self._included, self._point_of, strict=True):
                if row_state(conversation.judgment, link) is IssueState.COUNTED:
                    window.append(conversation.judgment.meeting_id)
                    if point is not None:
                        points[point].append(conversation.judgment.meeting_id)
            self._cache[key] = (sorted(window), [sorted(ids) for ids in points])
        return self._cache[key]


def _issue_fact(
    name: str,
    cell: Mapping[str, Any],
    ids: list[str],
    group: dict[str, Any],
    limits: ToolLimits,
    *,
    ratio: bool,
) -> Fact:
    """The fact of one trend cell.  Citations only for a ``computed`` cell (HOM-83 A3); any
    other cell withholds the list and keeps its total."""
    numerator = cell["numerator"]
    value = cell["ratio"] if ratio else numerator
    parts = (numerator, cell["denominator"]) if ratio else None
    if cell["status"] != CellStatus.COMPUTED.value:
        return Fact.withheld(name, value=value, total=numerator, group=group, parts=parts)
    if len(ids) != numerator:  # one load: never true
        raise RuntimeError("the supporting conversations do not match the numerator")
    return Fact.cited(
        name, value=value, ids=ids, max_citations=limits.max_citations, group=group, parts=parts
    )


class _IssueAnswer:
    """One cohort of ``GET /trends/issues`` for a routed decision: the cells, after the
    HOM-82 masks, and the counted conversations behind them."""

    def __init__(
        self,
        db: Session,
        context: WorkspaceContext,
        decision: RoutingDecision,
        grain: str,
        limits: ToolLimits,
    ) -> None:
        assert decision.filters is not None
        filters = decision.filters
        window = Window(filters.window.start, filters.window.end, "")
        self.points = _points(window, grain) if grain in TIME_GRAINS else []
        cohort = issue_cohort(
            db,
            workspace_id=context.workspace.id,
            access=context.record_access,
            filters=filters,
            points=self.points,
            organization_id=context.workspace.organization_id,
        )
        # SPEC A2: the stricter kind of the data and the routing decision.
        agent = cohort.kind is CellKind.AGENT or decision.cell_kind is CellKind.AGENT
        self.kind = CellKind.AGENT if agent else CellKind.RATE
        population, rows, series = cohort_parts(
            cohort.conversations, trend_definition(), filters, self.kind, self.points, _zone()
        )
        loaded = population["included"] + population["excluded"].get("duplicate_media", 0)
        if loaded > limits.max_rows:
            raise _Abstain(AbstainReason.ROW_LIMIT_EXCEEDED)
        self.rows, self.series = apply_masks(rows, series, cohort.masked)
        self.masked = cohort.masked
        self.counted = _Counted(cohort.conversations, self.points)
        self.provenance = {
            "catalogue_version": decision.catalogue_version,
            "catalogue_digest": decision.catalogue_digest,
            **trend_provenance(trend_definition(), SAMPLE_POLICY_V1),
            "disclosure": disclosure_block(self.kind, self.masked),
        }


def counted_conversation_ids(
    db: Session, *, context: WorkspaceContext, decision: RoutingDecision, limits: ToolLimits
) -> frozenset[str]:
    """The conversations an ``evidence_examples`` decision narrows to (ADR 0084 D4).

    The union, over the decision's rows, of the window ids ``_IssueAnswer.counted``
    gives, before any ``max_citations`` cut.  The decision is the evidence decision
    itself, no second routing: the counted ids and the masks do not depend on the
    decision's cell kind, so an ``issue_count`` decision with the same filters
    gives the same cohort (an ``issue_count`` decision does have a ``cell_kind``:
    ``count`` is a cell gate, and no gate is read here).  The checks are the
    ones of ``issue_aggregate``: a population over ``limits.max_rows`` and a
    withheld row raise ``ToolAbstention`` (``row_limit_exceeded``, ``withheld``);
    no gate runs here.
    """
    answer = _IssueAnswer(db, context, decision, "none", limits)
    _withheld(answer.rows)
    return frozenset(
        meeting_id for row in answer.rows for meeting_id in answer.counted.of(row)[0]
    )


def _issue_aggregate(
    db: Session,
    context: WorkspaceContext,
    params: IssueAggregateParams,
    now: datetime,
    limits: ToolLimits,
) -> ToolResult:
    decision = _route(context, params.intent, params.params, now)
    if decision.intent_id not in ISSUE_INTENTS:
        raise _Abstain(AbstainReason.INTENT_NOT_SUPPORTED)
    trend = decision.intent_id == "issue_trend"
    assert decision.filters is not None
    grain = "none"
    if trend:
        assert decision.arguments is not None and decision.arguments.grain is not None
        grain = decision.arguments.grain.value
    answer = _IssueAnswer(db, context, decision, grain, limits)
    _withheld(answer.rows)
    for point in answer.series:
        _withheld(point["rows"])
    if trend:
        _gate(
            decision, [p["rows"][i]["cell"] for i in range(len(answer.rows)) for p in answer.series]
        )
    else:
        _gate(decision, [row["cell"] for row in answer.rows])

    facts: list[Fact] = []
    for position, row in enumerate(answer.rows):
        window_ids, point_ids = answer.counted.of(row)
        group = {"row": _row_name(row)}
        if decision.intent_id == "issue_count":
            facts.append(
                _issue_fact("issue_count", row["cell"], window_ids, group, limits, ratio=False)
            )
        elif decision.intent_id == "issue_rate":
            facts.append(
                _issue_fact("issue_rate", row["cell"], window_ids, group, limits, ratio=True)
            )
        else:
            for index, point in enumerate(answer.points):
                cell = answer.series[index]["rows"][position]["cell"]
                facts.append(
                    _issue_fact(
                        "issue_rate",
                        cell,
                        point_ids[index],
                        {**group, **_point_group(index, point)},
                        limits,
                        ratio=True,
                    )
                )
    return ToolResult.answered(
        "issue_aggregate",
        filters={
            **decision.filters.to_json(),
            "intent": decision.intent_id,
            "series": _series(grain),
        },
        facts=facts,
        restricted=context.record_access.restricted,
        limits=limits,
        provenance=answer.provenance,
    )


def _top_issues(
    db: Session,
    context: WorkspaceContext,
    params: TopIssuesParams,
    now: datetime,
    limits: ToolLimits,
) -> ToolResult:
    decision = _route(context, "issue_count", {"family": params.family, **params.filters}, now)
    assert decision.filters is not None
    answer = _IssueAnswer(db, context, decision, "none", limits)
    issues = [row for row in answer.rows if row["level"] == "issue"]
    if not issues:  # SPEC A1: a family without issue rows (qa) gates its family row
        family = [row for row in answer.rows if row["level"] == "family"]
        _withheld(family)
        _gate(decision, [row["cell"] for row in family])
        raise RuntimeError("a family without issue rows passed the count gate")
    _withheld(issues)  # SPEC A7: before ranking and before the limit cut
    _gate(decision, [row["cell"] for row in issues])
    ranked = sorted(issues, key=lambda row: (-row["cell"]["numerator"], row["issue"]))
    facts = []
    for rank, row in enumerate(ranked[: params.limit], start=1):
        window_ids, _points_ids = answer.counted.of(row)
        facts.append(
            _issue_fact(
                "issue_count",
                row["cell"],
                window_ids,
                {"rank": rank, "row": _row_name(row)},
                limits,
                ratio=False,
            )
        )
    return ToolResult.answered(
        "top_issues",
        filters={**decision.filters.to_json(), "family": params.family, "limit": params.limit},
        facts=facts,
        restricted=context.record_access.restricted,
        limits=limits,
        provenance=answer.provenance,
    )


_RUNNERS: dict[str, _Runner] = {
    "action_items_due": _action_items_due,
    "conversation_count": _conversation_count,
    "issue_aggregate": _issue_aggregate,
    "scorecard_summary": _scorecard_summary,
    "top_issues": _top_issues,
}


# --- limits ---------------------------------------------------------------------------------


def _cancelled(exc: DBAPIError) -> bool:
    original = exc.orig
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return code == CANCELLED_SQLSTATE


def _limited(
    db: Session, limits: ToolLimits, run: Callable[[], ToolResult]
) -> ToolResult | AbstainReason:
    """``run()`` inside a SAVEPOINT on the session's connection (no flush: SPEC A12), with
    ``statement_timeout`` set on PostgreSQL and set back on every exit path.  A statement
    cancelled by the timeout rolls the savepoint back and gives ``time_limit_exceeded``; the
    caller's session stays usable."""
    connection = db.connection()
    postgres = connection.dialect.name == "postgresql"
    savepoint = connection.begin_nested()
    before: str | None = None

    def restore() -> None:
        if before is not None:
            connection.execute(_SET_TIMEOUT, {"value": before})

    try:
        if postgres:
            before = connection.execute(_READ_TIMEOUT).scalar_one()
            connection.execute(_SET_TIMEOUT, {"value": f"{limits.statement_timeout_ms}ms"})
        with db.no_autoflush:
            result = run()
    except DBAPIError as exc:
        savepoint.rollback()
        restore()
        if postgres and _cancelled(exc):
            return AbstainReason.TIME_LIMIT_EXCEEDED
        raise
    except BaseException:
        savepoint.rollback()
        restore()
        raise
    restore()
    savepoint.commit()
    return result


# --- the entry point ------------------------------------------------------------------------


def _first_key(params: Mapping[str, object], words: frozenset[str]) -> str | None:
    return next((key for key in sorted(params) if key in words), None)


def run_tool(
    db: Session,
    *,
    context: WorkspaceContext,
    name: object,
    params: object,
    now: datetime,
    limits: ToolLimits = DEFAULT_LIMITS,
) -> ToolResult:
    """Run the allowlisted tool ``name`` with ``params`` for ``context``.

    ``name`` and ``params`` are untrusted (a model wrote them).  ``context`` comes from
    authorization and ``now`` from the server clock.  The checks run in this order and the
    first defect decides (README "The check order"):

    1. ``now`` without a timezone, a wrong ``context`` or bad ``limits``: ``ValueError``;
    2. a session not bound to the caller's workspace: ``ValueError`` (no query runs);
    3. an unknown tool: ``tool_unknown`` (the name is never echoed);
    4. a missing permission: ``permission_denied``;
    5. params that are not a mapping with text keys: ``params_invalid``;
    6. a scope key: ``scope_not_allowed``;
    7. an unsupported word: ``unsupported_filter``;
    8. the tool's own param check: ``params_invalid`` (or its specific reason);
    9. the run, under the limits: ``answered``, or an abstention.
    """
    if not _aware(now):
        raise ValueError("now must be a datetime with a timezone")
    if not isinstance(context, WorkspaceContext):
        raise ValueError("context must be a WorkspaceContext")
    limits = check_limits(limits)
    if current_context(db).workspace_id != context.workspace.id:
        raise ValueError("the session is not bound to the caller's workspace")
    restricted = context.record_access.restricted

    def abstain(
        tool: str | None, reason: AbstainReason, detail: str | None = None, key: str | None = None
    ) -> ToolResult:
        return ToolResult.abstain(
            tool, reason, restricted=restricted, limits=limits, detail=detail, key=key
        )

    if not isinstance(name, str) or name not in TOOLS:
        return abstain(None, AbstainReason.TOOL_UNKNOWN)
    spec = TOOLS[name]
    if not all(context.has(permission) for permission in spec.required_permissions):
        return abstain(name, AbstainReason.PERMISSION_DENIED)
    if not isinstance(params, Mapping) or not all(isinstance(key, str) for key in params):
        return abstain(name, AbstainReason.PARAMS_INVALID, ParamDefect.PARAMS_NOT_MAPPING.value)
    scope_key = _first_key(params, SCOPE_KEYS)
    if scope_key is not None:
        return abstain(name, AbstainReason.SCOPE_NOT_ALLOWED, key=scope_key)
    unsupported = _first_key(params, UNSUPPORTED_KEYS)
    if unsupported is not None:
        return abstain(name, AbstainReason.UNSUPPORTED_FILTER, key=unsupported)
    try:
        checked = check_params(name, params, now=now)
    except ParamError as exc:
        return abstain(name, exc.reason, exc.detail, exc.key)

    runner = _RUNNERS[name]

    def run() -> ToolResult:
        try:
            return runner(db, context, checked, now, limits)
        except _Abstain as exc:
            return abstain(name, exc.reason, exc.detail, exc.key)

    started = time.monotonic()
    outcome = _limited(db, limits, run)
    elapsed_ms = (time.monotonic() - started) * 1000
    if isinstance(outcome, AbstainReason):
        return abstain(name, outcome)
    if elapsed_ms > limits.time_budget_ms:
        return abstain(name, AbstainReason.TIME_LIMIT_EXCEEDED)
    return outcome
