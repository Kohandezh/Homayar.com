"""Deterministic routing of a typed Copilot request, and the two answer gates (HOM-92).

``route_request`` decides which route answers a typed request, or why it is refused.
It never runs SQL and never retrieves anything.  After HOM-96 runs the aggregate,
``gate_aggregate`` turns a cell that cannot be shown into an abstention.  After HOM-94
and HOM-95 fetch the authorized evidence, ``gate_evidence`` turns "no span" into an
abstention.  Pure: no database, no network, no clock.  It never changes its inputs.

The rule the routes enforce: every number of an answer comes from an exact SQL
aggregate.  An evidence route never gives a number.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from backend.copilot_intents.catalogue import (
    AbstainReason,
    Gate,
    IntentCatalogue,
    IntentDefinition,
    ParamName,
    RefusalReason,
    Route,
    ScopeRule,
    Target,
)
from backend.issue_trends import (
    ASSOCIATION_NOTICE,
    ID_VALUE_PATTERN,
    SCOPE_KEYS,
    Cell,
    CellKind,
    CellStatus,
    Comparison,
    FilterErrorCode,
    FilterKey,
    TrendFilterError,
    TrendFilters,
    selected_family_ids,
    selected_issue_ids,
    validate_filters,
)


class Grain(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


DEFAULT_GRAIN = Grain.WEEK
# The keys a cohort comparison may split on.  Never agent_user_id: comparing two people
# is a ranking of individuals (refused).
COHORT_KEYS = (FilterKey.LANGUAGE.value, FilterKey.SOURCE_TYPE.value, FilterKey.TEAM_ID.value)
COHORT_SIZE = 2
DEFAULT_EVIDENCE_LIMIT = 5
MAX_EVIDENCE_LIMIT = 10
MAX_QUERY_LENGTH = 200


class RequestDefect(StrEnum):
    """``RoutingDecision.detail`` of a refusal that is not a filter error.  A filter error
    keeps its ``issue_trends`` code (``filter_...``) as the detail."""

    PARAMS_NOT_MAPPING = "params_not_mapping"
    INTENT_UNKNOWN = "intent_unknown"
    PARAM_NOT_ALLOWED = "param_not_allowed"  # a filter key this intent does not take
    PARAM_MISSING = "param_missing"
    PARAM_BAD_TYPE = "param_bad_type"
    PARAM_BAD_VALUE = "param_bad_value"
    COHORT_CONFLICT = "cohort_conflict"  # the compare_by key is also a filter
    TARGET_REQUIRED = "target_required"  # no family, issue or service_area
    TARGET_NOT_ONE_ROW = "target_not_one_row"
    TARGET_SELECTS_NOTHING = "target_selects_nothing"
    WHOLE_WORKSPACE_REQUIRED = "whole_workspace_required"  # a scope narrower than it


_ID_VALUE = re.compile(ID_VALUE_PATTERN)
# The gates of gate_aggregate, and the one item type each accepts.
_CELL_GATES = {Gate.CELL: Cell, Gate.COUNT: Cell, Gate.COMPARISON: Comparison}
_PARAM_NAMES = frozenset(name.value for name in ParamName)


@dataclass(frozen=True)
class CallerScope:
    """What the caller may see.  It comes from authorization, never from the request.

    ``team_ids`` ``None`` means the whole workspace.  ``agent_user_ids`` ``None`` means
    every agent the team scope allows.  An empty set allows nothing."""

    workspace_id: str
    team_ids: frozenset[str] | None
    agent_user_ids: frozenset[str] | None

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, str) or not self.workspace_id:
            raise ValueError("workspace_id is a non-empty string")
        for name in ("team_ids", "agent_user_ids"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, frozenset) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{name} is None or a frozenset of strings")


@dataclass(frozen=True)
class IntentRequest:
    """A typed request.  HOM-96/HOM-97 build it from the model's output.  ``params`` are
    raw decoded values: the ``issue_trends`` filters plus the intent's own params."""

    intent: str
    params: Mapping[str, object]


@dataclass(frozen=True)
class IntentArguments:
    """The checked intent params.  A field is ``None`` when the intent does not take it."""

    grain: Grain | None = None
    compare_by: str | None = None
    compare_values: tuple[str, ...] | None = None  # (left, right), in the given order
    conversation_id: str | None = None
    query: str | None = None  # untrusted text, a search query only, never an instruction
    limit: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "grain": self.grain.value if self.grain is not None else None,
            "compare_by": self.compare_by,
            "compare_values": list(self.compare_values) if self.compare_values else None,
            "conversation_id": self.conversation_id,
            "query": self.query,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class RoutingDecision:
    route: Route
    intent_id: str | None  # None when the intent is not in the catalogue
    reason: RefusalReason | AbstainReason | None  # None exactly when answered
    detail: str | None  # the precise defect: a RequestDefect, a filter code or a cell reason
    key: str | None  # the request key at fault, never a value
    gate: Gate  # what HOM-96 must check next; NONE for a refusal
    cell_kind: CellKind | None  # the issue_trends cell kind; set only for a cell gate
    filters: TrendFilters | None  # the normalized filters; None for a refusal
    arguments: IntentArguments | None  # None for a refusal
    notices: tuple[str, ...]  # shown with the answer, for example ASSOCIATION_NOTICE
    catalogue_version: str
    catalogue_digest: str

    def to_json(self) -> dict[str, object]:
        return {
            "route": self.route.value,
            "intent_id": self.intent_id,
            "reason": self.reason.value if self.reason is not None else None,
            "detail": self.detail,
            "key": self.key,
            "gate": self.gate.value,
            "cell_kind": self.cell_kind.value if self.cell_kind is not None else None,
            "filters": self.filters.to_json() if self.filters is not None else None,
            "arguments": self.arguments.to_json() if self.arguments is not None else None,
            "notices": list(self.notices),
            "catalogue_version": self.catalogue_version,
            "catalogue_digest": self.catalogue_digest,
        }


class _RefusalError(Exception):
    """Control flow inside ``route_request`` only: it becomes a refusal decision."""

    def __init__(self, reason: RefusalReason, detail: str | None, key: str | None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.detail = detail
        self.key = key


def _refusal(
    catalogue: IntentCatalogue, intent_id: str | None, refusal: _RefusalError
) -> RoutingDecision:
    return RoutingDecision(
        route=Route.REFUSE,
        intent_id=intent_id,
        reason=refusal.reason,
        detail=refusal.detail,
        key=refusal.key,
        gate=Gate.NONE,
        cell_kind=None,
        filters=None,
        arguments=None,
        notices=(),
        catalogue_version=catalogue.version,
        catalogue_digest=catalogue.digest,
    )


def _invalid(defect: RequestDefect | str, key: str | None) -> _RefusalError:
    return _RefusalError(RefusalReason.INVALID_REQUEST, str(defect), key)


def _filters(raw: Mapping[str, object], catalogue: IntentCatalogue, now: datetime) -> TrendFilters:
    """``validate_filters`` with its errors mapped to refusals.  Scope keys never reach
    it: they are refused first."""
    try:
        return validate_filters(raw, catalogue.trends, now=now)
    except TrendFilterError as exc:
        if exc.code is FilterErrorCode.UNSUPPORTED:
            raise _RefusalError(RefusalReason.UNSUPPORTED_FILTER, exc.code.value, exc.key) from exc
        if exc.code is FilterErrorCode.SCOPE_NOT_ALLOWED:  # not reachable: checked first
            raise _RefusalError(RefusalReason.SCOPE_NOT_ALLOWED, exc.code.value, exc.key) from exc
        raise _invalid(exc.code.value, exc.key) from exc


def _grain(params: Mapping[str, object]) -> Grain:
    raw = params.get(ParamName.GRAIN.value, DEFAULT_GRAIN.value)
    if not isinstance(raw, str):
        raise _invalid(RequestDefect.PARAM_BAD_TYPE, ParamName.GRAIN.value)
    try:
        return Grain(raw)
    except ValueError as exc:
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, ParamName.GRAIN.value) from exc


def _cohorts(
    params: Mapping[str, object], catalogue: IntentCatalogue, now: datetime
) -> tuple[str, tuple[str, ...]]:
    by_key, values_key = ParamName.COMPARE_BY.value, ParamName.COMPARE_VALUES.value
    if by_key not in params:
        raise _invalid(RequestDefect.PARAM_MISSING, by_key)
    compare_by = params[by_key]
    if not isinstance(compare_by, str):
        raise _invalid(RequestDefect.PARAM_BAD_TYPE, by_key)
    if compare_by == FilterKey.AGENT_USER_ID.value:
        raise _RefusalError(RefusalReason.INDIVIDUAL_RANKING_NOT_SUPPORTED, None, by_key)
    if compare_by not in COHORT_KEYS:
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, by_key)
    if values_key not in params:
        raise _invalid(RequestDefect.PARAM_MISSING, values_key)
    raw = params[values_key]
    if not isinstance(raw, list | tuple) or not all(isinstance(item, str) for item in raw):
        raise _invalid(RequestDefect.PARAM_BAD_TYPE, values_key)
    if len(raw) != COHORT_SIZE:
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, values_key)
    values: list[str] = []
    for item in raw:
        # The filter rules of that key decide what one value may be.
        try:
            one = validate_filters({compare_by: item}, catalogue.trends, now=now)
        except TrendFilterError as exc:
            raise _invalid(RequestDefect.PARAM_BAD_VALUE, values_key) from exc
        values.extend(
            {
                FilterKey.LANGUAGE.value: one.languages,
                FilterKey.SOURCE_TYPE.value: one.source_types,
                FilterKey.TEAM_ID.value: one.team_ids,
            }[compare_by]
        )
    if len(set(values)) != COHORT_SIZE:
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, values_key)
    if compare_by in params:
        raise _invalid(RequestDefect.COHORT_CONFLICT, compare_by)
    return compare_by, tuple(values)


def _conversation_id(params: Mapping[str, object]) -> str:
    key = ParamName.CONVERSATION_ID.value
    if key not in params:
        raise _invalid(RequestDefect.PARAM_MISSING, key)
    raw = params[key]
    if not isinstance(raw, str):
        raise _invalid(RequestDefect.PARAM_BAD_TYPE, key)
    if not _ID_VALUE.fullmatch(raw):
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, key)
    return raw


def _query(params: Mapping[str, object]) -> str | None:
    key = ParamName.QUERY.value
    if key not in params:
        return None
    raw = params[key]
    if not isinstance(raw, str):
        raise _invalid(RequestDefect.PARAM_BAD_TYPE, key)
    text = raw.strip()
    control = any(unicodedata.category(char) == "Cc" for char in text)
    if not text or len(text) > MAX_QUERY_LENGTH or control:
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, key)
    return text


def _limit(params: Mapping[str, object]) -> int:
    key = ParamName.LIMIT.value
    raw = params.get(key, DEFAULT_EVIDENCE_LIMIT)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _invalid(RequestDefect.PARAM_BAD_TYPE, key)
    if not 1 <= raw <= MAX_EVIDENCE_LIMIT:
        raise _invalid(RequestDefect.PARAM_BAD_VALUE, key)
    return raw


def _arguments(
    intent: IntentDefinition,
    params: Mapping[str, object],
    catalogue: IntentCatalogue,
    now: datetime,
) -> IntentArguments:
    """The intent's own params, checked in the sorted order of the catalogue."""
    grain: Grain | None = None
    compare_by: str | None = None
    compare_values: tuple[str, ...] | None = None
    conversation_id: str | None = None
    query: str | None = None
    limit: int | None = None
    for name in intent.params:
        if name is ParamName.GRAIN:
            grain = _grain(params)
        elif name is ParamName.COMPARE_BY:
            compare_by, compare_values = _cohorts(params, catalogue, now)
        elif name is ParamName.CONVERSATION_ID:
            conversation_id = _conversation_id(params)
        elif name is ParamName.QUERY:
            query = _query(params)
        elif name is ParamName.LIMIT:
            limit = _limit(params)
        # COMPARE_VALUES is read with COMPARE_BY.
    return IntentArguments(
        grain=grain,
        compare_by=compare_by,
        compare_values=compare_values,
        conversation_id=conversation_id,
        query=query,
        limit=limit,
    )


def _check_target(
    intent: IntentDefinition, filters: TrendFilters, catalogue: IntentCatalogue
) -> None:
    if intent.target is Target.NONE:
        return
    if intent.target is Target.ONE_ROW:
        if len(filters.families) + len(filters.issues) != 1 or filters.service_areas:
            raise _invalid(RequestDefect.TARGET_NOT_ONE_ROW, None)
    elif not (filters.families or filters.issues or filters.service_areas):
        raise _invalid(RequestDefect.TARGET_REQUIRED, None)
    issues = selected_issue_ids(filters, catalogue.trends)
    if intent.route is Route.EVIDENCE_RETRIEVAL:
        # Evidence comes from issue judgments.  A family without issues has none.
        rows: tuple[str, ...] = issues
    else:
        rows = selected_family_ids(filters, catalogue.trends) + issues
    if not rows:
        raise _invalid(RequestDefect.TARGET_SELECTS_NOTHING, None)


def _narrower_than_workspace(scope: CallerScope) -> bool:
    return scope.team_ids is not None or scope.agent_user_ids is not None


def _check_scope(filters: TrendFilters, arguments: IntentArguments, scope: CallerScope) -> None:
    """Every team and agent the request names must be inside the caller's scope.  The
    check uses only the scope, never whether the id exists, so a real id outside the
    scope and a made-up one give the same refusal."""
    named = [
        (FilterKey.TEAM_ID.value, filters.team_ids, scope.team_ids),
        (FilterKey.AGENT_USER_ID.value, filters.agent_user_ids, scope.agent_user_ids),
    ]
    if arguments.compare_by == FilterKey.TEAM_ID.value and arguments.compare_values:
        named.append((ParamName.COMPARE_VALUES.value, arguments.compare_values, scope.team_ids))
    for key, values, allowed in named:
        if allowed is not None and any(value not in allowed for value in values):
            raise _RefusalError(RefusalReason.OUTSIDE_CALLER_SCOPE, None, key)


def route_request(
    request: IntentRequest,
    catalogue: IntentCatalogue,
    *,
    scope: CallerScope,
    now: datetime,
) -> RoutingDecision:
    """The route of ``request``, or why it is refused.  The checks run in this order and
    the first defect decides (README "Routing order"):

    1. ``now`` without a timezone: ``ValueError`` (a programming error), always first.
    2. ``params`` not a mapping with text keys: ``invalid_request``.
    3. a scope key: ``scope_not_allowed``.  It wins over every other defect.
    4. an unknown intent: ``out_of_scope``.
    5. a refuse intent: its own reason.
    5b. a ``whole_workspace`` intent and a scope with ``team_ids`` or
        ``agent_user_ids`` set: ``outside_caller_scope``, whatever the params.
    6. ``validate_filters`` on the filter keys: ``unsupported_filter``, else
       ``invalid_request`` with the filter code.
    7. a filter key the intent does not take: ``invalid_request``.
    8. the intent's own params: ``invalid_request`` (or ``individual_ranking_not_supported``
       for an agent cohort).
    9. the target rows: ``invalid_request``.
    10. a team or agent outside ``scope``: ``outside_caller_scope``.
    11. else the intent's route, with ``reason`` ``None``.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    if not isinstance(request, IntentRequest):
        raise ValueError("request must be an IntentRequest")
    if not isinstance(catalogue, IntentCatalogue):
        raise ValueError("catalogue must be an IntentCatalogue")
    if not isinstance(scope, CallerScope):
        raise ValueError("scope must be a CallerScope")
    params = request.params
    intent = catalogue.get(request.intent)
    intent_id = intent.id if intent is not None else None
    try:
        if not isinstance(params, Mapping) or not all(isinstance(key, str) for key in params):
            raise _invalid(RequestDefect.PARAMS_NOT_MAPPING, None)
        for key in sorted(params):
            if key in SCOPE_KEYS:
                raise _RefusalError(RefusalReason.SCOPE_NOT_ALLOWED, None, key)
        if intent is None:
            raise _RefusalError(
                RefusalReason.OUT_OF_SCOPE, RequestDefect.INTENT_UNKNOWN.value, None
            )
        if intent.route is Route.REFUSE:
            assert intent.reason is not None  # the loader guarantees it
            raise _RefusalError(intent.reason, None, None)
        if intent.scope_rule is ScopeRule.WHOLE_WORKSPACE and _narrower_than_workspace(scope):
            raise _RefusalError(
                RefusalReason.OUTSIDE_CALLER_SCOPE,
                RequestDefect.WHOLE_WORKSPACE_REQUIRED.value,
                None,
            )
        own = {name.value for name in intent.params}
        filter_part = {key: value for key, value in params.items() if key not in own}
        filters = _filters(filter_part, catalogue, now)
        for key in sorted(filter_part):
            if key not in intent.filter_keys:
                raise _invalid(RequestDefect.PARAM_NOT_ALLOWED, key)
        arguments = _arguments(intent, params, catalogue, now)
        _check_target(intent, filters, catalogue)
        _check_scope(filters, arguments, scope)
    except _RefusalError as refusal:
        return _refusal(catalogue, intent_id, refusal)
    cell_kind = None
    if intent.gate in _CELL_GATES:
        # An agent-scoped caller sees only agent cohorts, filter or not.
        by_agent = bool(filters.agent_user_ids) or scope.agent_user_ids is not None
        cell_kind = CellKind.AGENT if by_agent else CellKind.RATE
    notices = (ASSOCIATION_NOTICE,) if ParamName.COMPARE_BY in intent.params else ()
    return RoutingDecision(
        route=intent.route,
        intent_id=intent.id,
        reason=None,
        detail=None,
        key=None,
        gate=intent.gate,
        cell_kind=cell_kind,
        filters=filters if intent.filter_keys else None,
        arguments=arguments,
        notices=notices,
        catalogue_version=catalogue.version,
        catalogue_digest=catalogue.digest,
    )


def _check_decision(decision: object) -> RoutingDecision:
    if not isinstance(decision, RoutingDecision):
        raise ValueError("decision must be a RoutingDecision")
    return decision


def _cells(value: object) -> tuple[Cell | Comparison, ...]:
    if isinstance(value, Cell | Comparison):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        items = tuple(value)
        if items and all(isinstance(item, Cell | Comparison) for item in items):
            return items
    raise ValueError("expected a Cell, a Comparison, or a non-empty sequence of them")


def gate_aggregate(
    decision: RoutingDecision, cell: Cell | Comparison | Sequence[Cell | Comparison]
) -> RoutingDecision:
    """After HOM-96 ran the SQL and built the ``issue_trends`` cells, check them.

    ``cell`` is one item or a non-empty sequence of items (a time series, or several rows
    of one answer).  The decision's gate says which item type it takes:

    - gate ``comparison`` (``cohort_comparison``): only ``Comparison`` items
      (``compare_cells``).  Its own ``status`` and ``reason`` decide, so each side is held
      to the comparison minimum.  A lone ``Cell`` would skip that minimum.
    - gate ``cell`` and gate ``count``: only ``Cell`` items (``sample_cell``,
      ``build_rows``, ``unavailable_cell``).

    Gates ``cell`` and ``comparison``: any item ``not_computable`` gives ``abstain``,
    ``not_computable`` (it wins); else any item ``insufficient_sample`` gives ``abstain``,
    ``below_minimum_sample``; else the decision, unchanged (the same object).

    Gate ``count`` (``issue_count``, HOM-79 rule: small counts stay visible, F49): any
    item ``not_computable`` gives ``not_computable``; else any item whose ``numerator`` is
    ``None`` (an agent cell under the minimum hides its count) gives
    ``below_minimum_sample``; else the decision, unchanged, even for an
    ``insufficient_sample`` rate cell, because the count is exact.

    ``detail`` is the cell reason of the first item that decided.  A refused or
    abstained decision is returned unchanged.  ``ValueError`` for an unknown item shape,
    an item of the wrong type for the gate, or a decision that is not gated by cells (an
    evidence route, ``coverage_status``).
    """
    decision = _check_decision(decision)
    cells = _cells(cell)
    if decision.route in (Route.REFUSE, Route.ABSTAIN):
        return decision
    if decision.route is not Route.SQL_AGGREGATE or decision.gate not in _CELL_GATES:
        raise ValueError(f"{decision.intent_id} is not gated by cells")
    expected = _CELL_GATES[decision.gate]
    if not all(type(item) is expected for item in cells):
        raise ValueError(f"gate {decision.gate.value} takes only {expected.__name__} items")
    for item in cells:
        if item.status is CellStatus.NOT_COMPUTABLE:
            return _abstain(decision, AbstainReason.NOT_COMPUTABLE, item)
    for item in cells:
        if decision.gate is Gate.COUNT:
            short = isinstance(item, Cell) and item.numerator is None
        else:
            short = item.status is CellStatus.INSUFFICIENT_SAMPLE
        if short:
            return _abstain(decision, AbstainReason.BELOW_MINIMUM_SAMPLE, item)
    return decision


def _abstain(
    decision: RoutingDecision, reason: AbstainReason, item: Cell | Comparison
) -> RoutingDecision:
    detail = item.reason.value if item.reason is not None else None
    return replace(decision, route=Route.ABSTAIN, reason=reason, detail=detail)


def gate_evidence(decision: RoutingDecision, authorized_evidence_count: int) -> RoutingDecision:
    """After HOM-94/HOM-95 fetched the evidence the caller may see, check the count.

    ``0``: ``abstain``, ``no_evidence``.  At least ``1``: the decision, unchanged (the
    same object).  ``ValueError`` for a negative number, a bool or a non-int, or for a
    decision that is not gated by evidence.  A refused or abstained decision is returned
    unchanged.  The count is never shown: it only decides the gate.
    """
    count = authorized_evidence_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("authorized_evidence_count is an integer of at least 0")
    decision = _check_decision(decision)
    if decision.route in (Route.REFUSE, Route.ABSTAIN):
        return decision
    if decision.route is not Route.EVIDENCE_RETRIEVAL or decision.gate is not Gate.EVIDENCE:
        raise ValueError(f"{decision.intent_id} is not gated by evidence")
    if count == 0:
        return replace(decision, route=Route.ABSTAIN, reason=AbstainReason.NO_EVIDENCE)
    return decision
