"""The strict param checks of every analytical tool (HOM-96, SPEC A3).

Pure: no database, no network.  ``now`` is the caller's clock, used only for the default
window of the trend filters.  Every check raises ``ParamError`` with the first defect, in
this order:

1. ``param_unknown``: the first key, in sorted order, that the tool does not accept;
2. ``validate_filters`` on the tool's trend filter keys that are present: its code is the
   ``detail`` and its key the ``key``;
3. the tool's own params, in sorted name order: ``param_missing``, then
   ``param_bad_type``, then ``param_bad_value`` or a specific reason
   (``individual_ranking_not_supported`` for a group of people).  A rule over two params
   (``due_after`` before ``due_before``) runs after every single param passed.

The checks never echo a value: ``key`` is a param key or ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

from backend.copilot_tools.results import AbstainReason, ParamDefect
from backend.issue_trends import (
    ID_VALUE_PATTERN,
    MAX_FILTER_VALUES,
    MAX_WINDOW,
    TrendDefinition,
    TrendFilterError,
    TrendFilters,
    load_definition,
    validate_filters,
)

TREND_DEFINITION_ID = "trends"
TREND_DEFINITION_VERSION = 1

BASIS_MEETING_CREATED_AT = "meeting_created_at"
BASIS_ACTION_ITEM_DUE_AT = "action_item_due_at"

CONVERSATION_GROUPS = ("none", "status", "team_id", "day", "week", "month")
ACTION_ITEM_GROUPS = ("none", "status", "priority", "team_id")
ACTION_ITEM_STATUSES = ("in_progress", "open")
# Grouping by a person ranks people (HOM-92 rule): refused with its own reason.
CONVERSATION_PEOPLE_GROUPS = ("agent_user_id",)
ACTION_ITEM_PEOPLE_GROUPS = ("agent_user_id", "assignee_user_id")
TOP_ISSUES_DEFAULT_LIMIT = 5
TOP_ISSUES_MAX_LIMIT = 10

_ID = re.compile(ID_VALUE_PATTERN)

# The keys each tool accepts, and which of them are issues_trends filters (checked by
# validate_filters).  The registry's schema properties equal ACCEPTED_KEYS (a test checks).
FILTER_KEYS: dict[str, tuple[str, ...]] = {
    "action_items_due": ("team_id",),
    "conversation_count": ("agent_user_id", "from", "team_id", "to"),
    "issue_aggregate": (),
    "scorecard_summary": ("agent_user_id", "from", "team_id", "to"),
    "top_issues": ("agent_user_id", "from", "language", "source_type", "team_id", "to"),
}
OWN_KEYS: dict[str, tuple[str, ...]] = {
    "action_items_due": ("assignee_user_id", "due_after", "due_before", "group_by", "status"),
    "conversation_count": ("group_by",),
    "issue_aggregate": ("intent", "params"),
    "scorecard_summary": ("scorecard_version_id",),
    "top_issues": ("family", "limit"),
}
ACCEPTED_KEYS: dict[str, frozenset[str]] = {
    name: frozenset(FILTER_KEYS[name]) | frozenset(OWN_KEYS[name]) for name in FILTER_KEYS
}


class ParamError(Exception):
    """The first defect of a tool's params."""

    def __init__(self, reason: AbstainReason, detail: str | None, key: str | None) -> None:
        super().__init__(f"{reason.value}: {detail} ({key})")
        self.reason = reason
        self.detail = detail
        self.key = key


def _invalid(defect: ParamDefect | str, key: str | None) -> ParamError:
    detail = defect.value if isinstance(defect, ParamDefect) else defect
    return ParamError(AbstainReason.PARAMS_INVALID, detail, key)


@cache
def trend_definition() -> TrendDefinition:
    return load_definition(TREND_DEFINITION_ID, TREND_DEFINITION_VERSION)


# --- the checked params ---------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """``start <= value < end`` in UTC.  ``start`` is ``None`` when there is no lower end
    (``action_items_due`` without ``due_after``)."""

    start: datetime | None
    end: datetime
    basis: str

    def to_json(self) -> dict[str, str | None]:
        return {
            "from": None if self.start is None else self.start.astimezone(UTC).isoformat(),
            "to": self.end.astimezone(UTC).isoformat(),
            "basis": self.basis,
        }


@dataclass(frozen=True)
class ConversationCountParams:
    window: Window
    team_ids: tuple[str, ...]
    agent_user_ids: tuple[str, ...]
    group_by: str


@dataclass(frozen=True)
class ActionItemsDueParams:
    window: Window  # start: due_after or None; end: due_before
    team_ids: tuple[str, ...]
    assignee_user_ids: tuple[str, ...]
    statuses: tuple[str, ...]
    group_by: str


@dataclass(frozen=True)
class ScorecardSummaryParams:
    scorecard_version_id: str
    window: Window
    team_ids: tuple[str, ...]
    agent_user_ids: tuple[str, ...]


@dataclass(frozen=True)
class IssueAggregateParams:
    intent: str
    params: Mapping[str, object]  # the HOM-92 request params, checked by route_request


@dataclass(frozen=True)
class TopIssuesParams:
    family: str
    limit: int
    filters: Mapping[str, object]  # the trend filter params given, already valid


# --- shared checks --------------------------------------------------------------------------


def _unknown(params: Mapping[str, object], tool: str) -> None:
    accepted = ACCEPTED_KEYS[tool]
    for key in sorted(params):
        if key not in accepted:
            raise _invalid(ParamDefect.PARAM_UNKNOWN, key)


def _filters(params: Mapping[str, object], tool: str, now: datetime) -> TrendFilters:
    raw = {key: params[key] for key in FILTER_KEYS[tool] if key in params}
    try:
        return validate_filters(raw, trend_definition(), now=now)
    except TrendFilterError as exc:
        raise _invalid(exc.code.value, exc.key) from exc


def _window(filters: TrendFilters, basis: str) -> Window:
    return Window(start=filters.window.start, end=filters.window.end, basis=basis)


def _group_by(
    params: Mapping[str, object], allowed: tuple[str, ...], people: tuple[str, ...]
) -> str:
    if "group_by" not in params:
        return "none"
    value = params["group_by"]
    if not isinstance(value, str):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, "group_by")
    if value in people:
        raise ParamError(AbstainReason.INDIVIDUAL_RANKING_NOT_SUPPORTED, None, "group_by")
    if value not in allowed:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, "group_by")
    return value


def _string_list(value: object, key: str) -> list[str]:
    """A string or a list of strings (``param_bad_type``); a non-empty list
    (``param_bad_value``)."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, key)
    if not value:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, key)
    return list(value)


def _ids(value: object, key: str) -> tuple[str, ...]:
    """One canonical lowercase UUID or a non-empty list of at most 50 of them, deduplicated
    and sorted (SPEC A5)."""
    values = sorted(set(_string_list(value, key)))
    if len(values) > MAX_FILTER_VALUES or not all(_ID.fullmatch(item) for item in values):
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, key)
    return tuple(values)


def _instant(value: object, key: str) -> datetime:
    """ISO 8601 with a timezone, as UTC: the ``issue_trends`` window rule."""
    if not isinstance(value, str):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, key)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, key) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, key)
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, key) from exc


def _require_mapping(params: object) -> Mapping[str, object]:
    if not isinstance(params, Mapping) or not all(isinstance(key, str) for key in params):
        raise _invalid(ParamDefect.PARAMS_NOT_MAPPING, None)
    return params


# --- one check per tool ---------------------------------------------------------------------


def _conversation_count(params: Mapping[str, object], now: datetime) -> ConversationCountParams:
    tool = "conversation_count"
    _unknown(params, tool)
    filters = _filters(params, tool, now)
    group_by = _group_by(params, CONVERSATION_GROUPS, CONVERSATION_PEOPLE_GROUPS)
    return ConversationCountParams(
        window=_window(filters, BASIS_MEETING_CREATED_AT),
        team_ids=filters.team_ids,
        agent_user_ids=filters.agent_user_ids,
        group_by=group_by,
    )


def _action_items_due(params: Mapping[str, object], now: datetime) -> ActionItemsDueParams:
    tool = "action_items_due"
    _unknown(params, tool)
    filters = _filters(params, tool, now)
    # own params in sorted name order
    assignees = (
        _ids(params["assignee_user_id"], "assignee_user_id") if "assignee_user_id" in params else ()
    )
    due_after = _instant(params["due_after"], "due_after") if "due_after" in params else None
    if "due_before" not in params:
        raise _invalid(ParamDefect.PARAM_MISSING, "due_before")
    due_before = _instant(params["due_before"], "due_before")
    group_by = _group_by(params, ACTION_ITEM_GROUPS, ACTION_ITEM_PEOPLE_GROUPS)
    statuses: tuple[str, ...] = ACTION_ITEM_STATUSES
    if "status" in params:
        values = sorted(set(_string_list(params["status"], "status")))
        if not all(value in ACTION_ITEM_STATUSES for value in values):
            raise _invalid(ParamDefect.PARAM_BAD_VALUE, "status")
        statuses = tuple(values)
    # the rule over two params (SPEC A4)
    if due_after is not None and (due_after >= due_before or due_before - due_after > MAX_WINDOW):
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, "due_after")
    return ActionItemsDueParams(
        window=Window(start=due_after, end=due_before, basis=BASIS_ACTION_ITEM_DUE_AT),
        team_ids=filters.team_ids,
        assignee_user_ids=assignees,
        statuses=statuses,
        group_by=group_by,
    )


def _scorecard_summary(params: Mapping[str, object], now: datetime) -> ScorecardSummaryParams:
    tool = "scorecard_summary"
    _unknown(params, tool)
    filters = _filters(params, tool, now)
    if "scorecard_version_id" not in params:
        raise _invalid(ParamDefect.PARAM_MISSING, "scorecard_version_id")
    version_id = params["scorecard_version_id"]
    if not isinstance(version_id, str):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, "scorecard_version_id")
    if not _ID.fullmatch(version_id):
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, "scorecard_version_id")
    return ScorecardSummaryParams(
        scorecard_version_id=version_id,
        window=_window(filters, BASIS_MEETING_CREATED_AT),
        team_ids=filters.team_ids,
        agent_user_ids=filters.agent_user_ids,
    )


def _issue_aggregate(params: Mapping[str, object], now: datetime) -> IssueAggregateParams:
    tool = "issue_aggregate"
    _unknown(params, tool)
    if "intent" not in params:
        raise _invalid(ParamDefect.PARAM_MISSING, "intent")
    intent = params["intent"]
    if not isinstance(intent, str):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, "intent")
    if "params" not in params:
        raise _invalid(ParamDefect.PARAM_MISSING, "params")
    request = params["params"]
    if not isinstance(request, Mapping):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, "params")
    return IssueAggregateParams(intent=intent, params=dict(request))


def _top_issues(params: Mapping[str, object], now: datetime) -> TopIssuesParams:
    tool = "top_issues"
    _unknown(params, tool)
    _filters(params, tool, now)
    if "family" not in params:
        raise _invalid(ParamDefect.PARAM_MISSING, "family")
    family = params["family"]
    if not isinstance(family, str):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, "family")
    if family not in {item.id for item in trend_definition().families}:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, "family")
    limit = params.get("limit", TOP_ISSUES_DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise _invalid(ParamDefect.PARAM_BAD_TYPE, "limit")
    if not 1 <= limit <= TOP_ISSUES_MAX_LIMIT:
        raise _invalid(ParamDefect.PARAM_BAD_VALUE, "limit")
    given = {key: params[key] for key in FILTER_KEYS[tool] if key in params}
    return TopIssuesParams(family=family, limit=limit, filters=given)


_CHECKS: dict[str, Any] = {
    "action_items_due": _action_items_due,
    "conversation_count": _conversation_count,
    "issue_aggregate": _issue_aggregate,
    "scorecard_summary": _scorecard_summary,
    "top_issues": _top_issues,
}


def check_params(tool: str, params: object, *, now: datetime) -> Any:
    """The checked params of ``tool`` (one of the dataclasses above), or ``ParamError``.

    ``params_not_mapping`` when ``params`` is not a mapping with text keys.  ``ValueError``
    for an unknown tool name or a ``now`` without a timezone (programming errors).  It does
    not check scope keys or unsupported words: ``run_tool`` does that before (steps 6 and
    7)."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    if tool not in _CHECKS:
        raise ValueError(f"unknown tool {tool!r}")
    return _CHECKS[tool](_require_mapping(params), now)
