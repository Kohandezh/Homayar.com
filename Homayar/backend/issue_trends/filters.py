"""The closed list of trend filters and their pure validator (HOM-79, D3).

Two kinds of filter.  A population filter narrows the conversations that are counted
(window, team, agent, language, source type).  A selector chooses which rows are
returned (family, issue, service area) and never changes a row's numbers.

Scope (workspace, role, team membership) is not a filter.  HOM-80 and HOM-82 apply it
from the caller's authorization and always combine it with the filters by AND, so a
filter can only narrow what the caller may already see.

A meeting with no team (or no agent) appears only in answers without a ``team_id`` (or
``agent_user_id``) filter.  So per-team rows need not add up to the workspace total.
Version 1 has no "no team" value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from backend.issue_trends.definition import SourceState, TrendDefinition, service_area_ids

# The window rules of ``coverage/1`` (``services/coverage_service.py`` ``parse_window``).
# A test holds these equal to the coverage constants.
WINDOW_BASIS = "ingestion_received_at"
DEFAULT_WINDOW = timedelta(days=30)
MAX_WINDOW = timedelta(days=366)

MAX_FILTER_VALUES = 50  # values per list filter, after duplicates collapse

# ``IngestionSourceType`` values (``database/saas_models.py``).  A test holds them equal.
SOURCE_TYPES = (
    "file_upload",
    "microphone",
    "system_audio",
    "mic_plus_system_audio",
    "sip_voip",
    "meeting_bot",
    "external_recording_url",
    "offline_folder_watcher",
    "api",
    "voicemail",
)

# Canonical lowercase UUID text, the form of ``new_id()``.
ID_VALUE_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
# The upload rule (``routers/v1/meetings.py``: 2 to 12 of ``[A-Za-z-]``), lowercased.
LANGUAGE_PATTERN = r"^[a-z-]{2,12}$"


class FilterKey(str, Enum):
    # population filters
    FROM = "from"  # ISO 8601 with a timezone: received_at >= from
    TO = "to"  # ISO 8601 with a timezone: received_at < to
    TEAM_ID = "team_id"  # Meeting.team_id IN (...)
    AGENT_USER_ID = "agent_user_id"  # Meeting.agent_user_id IN (...)
    LANGUAGE = "language"  # Meeting.language IN (...)
    SOURCE_TYPE = "source_type"  # IngestionRecord.source_type IN (...)
    # selectors
    FAMILY = "family"  # family ids of the definition
    ISSUE = "issue"  # active issue ids of the definition
    SERVICE_AREA = "service_area"  # ``service_area_ids(definition)``


POPULATION_KEYS = (
    FilterKey.FROM,
    FilterKey.TO,
    FilterKey.TEAM_ID,
    FilterKey.AGENT_USER_ID,
    FilterKey.LANGUAGE,
    FilterKey.SOURCE_TYPE,
)
SELECTOR_KEYS = (FilterKey.FAMILY, FilterKey.ISSUE, FilterKey.SERVICE_AREA)

# Known words that version 1 does not support.  Refused with ``filter_unsupported``.
UNSUPPORTED_KEYS = frozenset(
    {
        "product",
        "product_id",
        "service",
        "customer_id",
        "phone",
        "entity",
        "repeat_contact",
        "scorecard",
        "sentiment",
    }
)
# Scope words.  Refused with ``filter_scope_not_allowed``: scope comes from the caller.
SCOPE_KEYS = frozenset({"workspace_id", "organization_id", "role", "scope"})


class FilterErrorCode(str, Enum):
    """Every code starts with ``filter_``, so a client tells filter errors apart by the
    prefix.  The window code keeps the coverage rules but not the coverage word."""

    SCOPE_NOT_ALLOWED = "filter_scope_not_allowed"
    UNSUPPORTED = "filter_unsupported"
    UNKNOWN_KEY = "filter_unknown_key"
    BAD_TYPE = "filter_bad_type"
    BAD_VALUE = "filter_bad_value"
    WINDOW_INVALID = "filter_window_invalid"


class TrendFilterError(ValueError):
    def __init__(self, code: FilterErrorCode, key: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.key = key  # the raw key at fault; None only for a window check on both ends


@dataclass(frozen=True)
class TrendWindow:
    """``start <= received_at < end``, both in UTC."""

    start: datetime
    end: datetime

    def to_json(self) -> dict[str, str]:
        """``{"from": iso, "to": iso, "basis": "ingestion_received_at"}``; the instants
        are ``datetime.isoformat()`` in UTC."""
        return {
            "from": self.start.astimezone(UTC).isoformat(),
            "to": self.end.astimezone(UTC).isoformat(),
            "basis": WINDOW_BASIS,
        }


@dataclass(frozen=True)
class TrendFilters:
    """The normalized filters.  Every list is sorted and has no duplicate; an empty
    tuple means "no restriction"."""

    window: TrendWindow
    team_ids: tuple[str, ...]
    agent_user_ids: tuple[str, ...]
    languages: tuple[str, ...]
    source_types: tuple[str, ...]
    families: tuple[str, ...]
    issues: tuple[str, ...]
    service_areas: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        """README "The normalized filter object": the window plus one list per key."""
        return {
            "window": self.window.to_json(),
            FilterKey.TEAM_ID.value: list(self.team_ids),
            FilterKey.AGENT_USER_ID.value: list(self.agent_user_ids),
            FilterKey.LANGUAGE.value: list(self.languages),
            FilterKey.SOURCE_TYPE.value: list(self.source_types),
            FilterKey.FAMILY.value: list(self.families),
            FilterKey.ISSUE.value: list(self.issues),
            FilterKey.SERVICE_AREA.value: list(self.service_areas),
        }


RawFilterValue = str | Sequence[str]

_ID_VALUE = re.compile(ID_VALUE_PATTERN)
_LANGUAGE = re.compile(LANGUAGE_PATTERN)
_KNOWN_KEYS = frozenset(key.value for key in FilterKey)
_WINDOW_KEYS = frozenset({FilterKey.FROM.value, FilterKey.TO.value})


def _fail(code: FilterErrorCode, key: str | None, message: str) -> TrendFilterError:
    return TrendFilterError(code, key, message)


def _well_typed(key: str, value: object) -> bool:
    if key in _WINDOW_KEYS:
        return isinstance(value, str)
    if isinstance(value, str):
        return True
    return isinstance(value, list | tuple) and all(isinstance(item, str) for item in value)


def _allowed(key: str, definition: TrendDefinition) -> Callable[[str], bool]:
    """A predicate on one (normalized) value of a list filter."""
    if key in (FilterKey.TEAM_ID.value, FilterKey.AGENT_USER_ID.value):
        return lambda value: bool(_ID_VALUE.fullmatch(value))
    if key == FilterKey.LANGUAGE.value:
        return lambda value: bool(_LANGUAGE.fullmatch(value))
    if key == FilterKey.SOURCE_TYPE.value:
        return SOURCE_TYPES.__contains__
    if key == FilterKey.FAMILY.value:
        return {family.id for family in definition.families}.__contains__
    if key == FilterKey.ISSUE.value:
        return {issue.id for issue in definition.active_issues()}.__contains__
    return set(service_area_ids(definition)).__contains__


def _list_values(
    key: str, value: str | Sequence[str], definition: TrendDefinition
) -> tuple[str, ...]:
    items = [value] if isinstance(value, str) else list(value)
    if not items:
        raise _fail(FilterErrorCode.BAD_VALUE, key, f"{key} has no value")
    if key == FilterKey.LANGUAGE.value:
        items = [item.lower() for item in items]
    allowed = _allowed(key, definition)
    for item in items:
        if not item or not allowed(item):
            raise _fail(FilterErrorCode.BAD_VALUE, key, f"{key} does not allow {item!r}")
    distinct = tuple(sorted(set(items)))
    if len(distinct) > MAX_FILTER_VALUES:
        raise _fail(
            FilterErrorCode.BAD_VALUE, key, f"{key} allows at most {MAX_FILTER_VALUES} values"
        )
    return distinct


def _instant(value: str, key: str) -> datetime:
    """The coverage ``_instant`` rule, with this package's error."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise _fail(
            FilterErrorCode.WINDOW_INVALID, key, f"{key} is not an ISO 8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail(FilterErrorCode.WINDOW_INVALID, key, f"{key} needs a timezone")
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:
        raise _fail(
            FilterErrorCode.WINDOW_INVALID, key, f"{key} is outside the supported date range"
        ) from exc


def _window(raw_from: str | None, raw_to: str | None, now: datetime) -> TrendWindow:
    """The window rules of ``coverage_service.parse_window``."""
    end = _instant(raw_to, FilterKey.TO.value) if raw_to is not None else now
    if raw_from is not None:
        start = _instant(raw_from, FilterKey.FROM.value)
    else:
        try:
            start = end - DEFAULT_WINDOW
        except OverflowError as exc:
            raise _fail(
                FilterErrorCode.WINDOW_INVALID,
                FilterKey.TO.value,
                "to is too early for the default 30-day window",
            ) from exc
    if start >= end:
        raise _fail(FilterErrorCode.WINDOW_INVALID, None, "from must be before to")
    if end - start > MAX_WINDOW:
        raise _fail(FilterErrorCode.WINDOW_INVALID, None, "the window may span at most 366 days")
    return TrendWindow(start=start, end=end)


def validate_filters(
    raw: Mapping[str, object], definition: TrendDefinition, *, now: datetime
) -> TrendFilters:
    """Check ``raw`` (decoded query parameters) and return the normalized filters.

    ``now`` is the caller's clock: it is the default ``to``.  It is checked first, always:
    a ``now`` without a timezone raises ``ValueError`` (a programming error, not user
    input), even when ``to`` is given.

    Raises ``TrendFilterError`` with the first defect in this order:

    1. ``filter_scope_not_allowed`` for any scope key, then ``filter_unsupported`` for any
       unsupported key, then ``filter_unknown_key`` for any other unknown key (each class
       over its keys in sorted order: the more serious refusal wins);
    2. ``filter_bad_type``, over the keys in sorted order;
    3. ``filter_bad_value``, over the keys in sorted order;
    4. ``filter_window_invalid``.

    ``{"aaa": "x", "workspace_id": "y"}`` gives ``filter_scope_not_allowed`` with key
    ``workspace_id``.  The rules per key are in the README ("Supported filters").
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    now = now.astimezone(UTC)
    keys = sorted(str(key) for key in raw)
    values = {str(key): value for key, value in raw.items()}
    for code, words in (
        (FilterErrorCode.SCOPE_NOT_ALLOWED, SCOPE_KEYS),
        (FilterErrorCode.UNSUPPORTED, UNSUPPORTED_KEYS),
    ):
        for key in keys:
            if key in words:
                raise _fail(code, key, f"{key} is not a trend filter")
    for key in keys:
        if key not in _KNOWN_KEYS:
            raise _fail(FilterErrorCode.UNKNOWN_KEY, key, f"{key} is not a trend filter")
    for key in keys:
        if not _well_typed(key, values[key]):
            raise _fail(FilterErrorCode.BAD_TYPE, key, f"{key} has the wrong type")
    normalized: dict[str, tuple[str, ...]] = {}
    for key in keys:
        if key in _WINDOW_KEYS:
            if not values[key]:
                raise _fail(FilterErrorCode.BAD_VALUE, key, f"{key} is empty")
            continue
        normalized[key] = _list_values(key, values[key], definition)  # type: ignore[arg-type]
    raw_from, raw_to = values.get(FilterKey.FROM.value), values.get(FilterKey.TO.value)
    window = _window(
        raw_from if isinstance(raw_from, str) else None,
        raw_to if isinstance(raw_to, str) else None,
        now,
    )
    return TrendFilters(
        window=window,
        team_ids=normalized.get(FilterKey.TEAM_ID.value, ()),
        agent_user_ids=normalized.get(FilterKey.AGENT_USER_ID.value, ()),
        languages=normalized.get(FilterKey.LANGUAGE.value, ()),
        source_types=normalized.get(FilterKey.SOURCE_TYPE.value, ()),
        families=normalized.get(FilterKey.FAMILY.value, ()),
        issues=normalized.get(FilterKey.ISSUE.value, ()),
        service_areas=normalized.get(FilterKey.SERVICE_AREA.value, ()),
    )


def selected_issue_ids(filters: TrendFilters, definition: TrendDefinition) -> tuple[str, ...]:
    """The active issues of available families that match every given selector, in
    file order.  ``family``: the issue's family is listed.  ``issue``: the issue is
    listed.  ``service_area``: one of the issue's references is a listed topic."""
    available = {
        family.id for family in definition.families if family.source_state is SourceState.AVAILABLE
    }
    selected = []
    for issue in definition.active_issues():
        if issue.family not in available:
            continue
        if filters.families and issue.family not in filters.families:
            continue
        if filters.issues and issue.id not in filters.issues:
            continue
        if filters.service_areas and not any(
            reference.dimension == "topic" and reference.label in filters.service_areas
            for reference in issue.match
        ):
            continue
        selected.append(issue.id)
    return tuple(selected)


def selected_family_ids(filters: TrendFilters, definition: TrendDefinition) -> tuple[str, ...]:
    """The family rows to return, in file order: none when ``issue`` or
    ``service_area`` is given, else every family (or the listed ones)."""
    if filters.issues or filters.service_areas:
        return ()
    return tuple(
        family.id
        for family in definition.families
        if not filters.families or family.id in filters.families
    )
