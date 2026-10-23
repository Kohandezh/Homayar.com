"""The planner's output, its routing, and the tools a route may use (HOM-97).

The planner writes a plan: one JSON object ``{"intent": ..., "params": {...}}``.
``parse_plan`` accepts only that exact shape.  ``route_plan`` sends it to the HOM-92
``route_request`` with the scope from authorization, never from the model, so a plan
that carries a scope key or an id outside the scope is refused by the HOM-92 rules.
Code, not a model, chooses the tools from the decision (``allowed_tools``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from backend.copilot_guard.defects import GuardDefect, MalformedJson, check_decision, strict_loads
from backend.copilot_intents import (
    CallerScope,
    Gate,
    IntentCatalogue,
    IntentRequest,
    RefusalReason,
    Route,
    RoutingDecision,
    route_request,
)

MAX_PLAN_LENGTH = 4096
_PLAN_KEYS = frozenset({"intent", "params"})


class ToolName(StrEnum):
    ANALYTICS_SQL = "analytics_sql"  # HOM-96
    EVIDENCE_SEARCH = "evidence_search"  # HOM-95


_TOOLS = {
    Route.SQL_AGGREGATE: frozenset({ToolName.ANALYTICS_SQL}),
    Route.EVIDENCE_RETRIEVAL: frozenset({ToolName.EVIDENCE_SEARCH}),
    Route.REFUSE: frozenset(),
    Route.ABSTAIN: frozenset(),
}


def parse_plan(raw: str) -> IntentRequest | None:
    """The typed request of a plan, or ``None`` when the plan is not exactly a JSON object
    with the keys ``intent`` (a str) and ``params`` (an object).  ``None`` also for a
    non-str, more than 4096 characters, a duplicate key at any level, ``NaN`` or
    ``Infinity``, or any other top-level key (``tool``, ``system``, ``scope``, ...).  It
    does not check the intent or the params: ``route_request`` does."""
    if not isinstance(raw, str) or len(raw) > MAX_PLAN_LENGTH:
        return None
    try:
        document = strict_loads(raw)
    except MalformedJson:
        return None
    if not isinstance(document, dict) or set(document) != _PLAN_KEYS:
        return None
    intent, params = document["intent"], document["params"]
    if not isinstance(intent, str) or not isinstance(params, dict):
        return None
    return IntentRequest(intent=intent, params=params)


def route_plan(
    raw: str, catalogue: IntentCatalogue, *, scope: CallerScope, now: datetime
) -> RoutingDecision:
    """``route_request`` of the parsed plan, unchanged.  A malformed plan refuses with
    ``invalid_request``, detail ``plan_malformed``.  ``now``, ``catalogue`` and ``scope``
    are checked first, as ``route_request`` checks them, so a bad ``now`` is a
    ``ValueError`` even for a malformed plan.  There is no transcript input."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    if not isinstance(catalogue, IntentCatalogue):
        raise ValueError("catalogue must be an IntentCatalogue")
    if not isinstance(scope, CallerScope):
        raise ValueError("scope must be a CallerScope")
    request = parse_plan(raw)
    if request is None:
        return RoutingDecision(
            route=Route.REFUSE,
            intent_id=None,
            reason=RefusalReason.INVALID_REQUEST,
            detail=GuardDefect.PLAN_MALFORMED.value,
            key=None,
            gate=Gate.NONE,
            cell_kind=None,
            filters=None,
            arguments=None,
            notices=(),
            catalogue_version=catalogue.version,
            catalogue_digest=catalogue.digest,
        )
    return route_request(request, catalogue, scope=scope, now=now)


def allowed_tools(decision: RoutingDecision) -> frozenset[ToolName]:
    """The tools code may run for ``decision``, a function of its route only:
    ``sql_aggregate`` the analytics SQL, ``evidence_retrieval`` the evidence search,
    ``refuse`` and ``abstain`` none."""
    return _TOOLS[check_decision(decision).route]
