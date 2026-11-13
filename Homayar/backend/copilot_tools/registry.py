"""The allowlist of analytical Copilot tools (HOM-96): names, permissions, schemas, limits.

Pure: no database, no clock, no network.  ``TOOLS`` is the only list of tools.  A name that
is not a key of ``TOOLS`` is never run.  ``tool_schemas()`` is the shape a model's tool-use
API takes; the model only ever sees these schemas, never SQL.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any

from backend.issue_trends import ID_VALUE_PATTERN, MAX_FILTER_VALUES, SOURCE_TYPES
from backend.security.authorization import Permission

TOOLS_VERSION = "copilot-tools/1"


# --- limits ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolLimits:
    """The limits of one tool run.  Every value is an ``int`` of at least 1 and at most
    its ``LIMIT_CAPS`` value (``check_limits``)."""

    max_rows: int  # population rows a tool may load; one more abstains
    max_citations: int  # record ids listed per fact
    statement_timeout_ms: int  # PostgreSQL statement_timeout during the run
    time_budget_ms: int  # wall time of the whole run (time.monotonic)

    def to_json(self) -> dict[str, int]:
        return {
            "max_rows": self.max_rows,
            "max_citations": self.max_citations,
            "statement_timeout_ms": self.statement_timeout_ms,
            "time_budget_ms": self.time_budget_ms,
        }


DEFAULT_LIMITS = ToolLimits(
    max_rows=5000, max_citations=100, statement_timeout_ms=5000, time_budget_ms=10000
)
LIMIT_CAPS = ToolLimits(
    max_rows=20000, max_citations=500, statement_timeout_ms=15000, time_budget_ms=30000
)


def check_limits(limits: object) -> ToolLimits:
    """``limits`` itself, or ``ValueError`` (a programming error): not a ``ToolLimits``, or a
    value that is not an ``int`` (a ``bool`` is refused), below 1 or above its cap."""
    if not isinstance(limits, ToolLimits):
        raise ValueError("limits must be a ToolLimits")
    for field in fields(ToolLimits):
        value = getattr(limits, field.name)
        cap = getattr(LIMIT_CAPS, field.name)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
            raise ValueError(f"limits.{field.name} must be an int from 1 to {cap}")
    return limits


# --- the tools ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # one English line
    required_permissions: tuple[Permission, ...]  # sorted by value; the caller needs all
    input_schema: dict[str, Any]  # JSON Schema: type object, additionalProperties false


_UUID: dict[str, Any] = {"type": "string", "pattern": ID_VALUE_PATTERN}
_INSTANT: dict[str, Any] = {"type": "string", "format": "date-time"}


def _one_or_many(item: dict[str, Any], description: str) -> dict[str, Any]:
    return {
        "description": description,
        "anyOf": [
            item,
            {"type": "array", "items": item, "minItems": 1, "maxItems": MAX_FILTER_VALUES},
        ],
    }


def _instant(description: str) -> dict[str, Any]:
    return {**_INSTANT, "description": description}


def _choice(values: tuple[str, ...], description: str, default: str | None = None) -> dict:
    schema: dict[str, Any] = {"type": "string", "enum": list(values), "description": description}
    if default is not None:
        schema["default"] = default
    return schema


_FROM = _instant("Window start, inclusive: ISO 8601 with a timezone. Default: 30 days before to.")
_TO = _instant("Window end, exclusive: ISO 8601 with a timezone. Default: now.")
_TEAM = _one_or_many(_UUID, "Only conversations of these teams (team ids).")
_AGENT = _one_or_many(_UUID, "Only conversations of these agents (user ids).")


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


def _spec(
    name: str,
    description: str,
    permissions: tuple[Permission, ...],
    schema: dict[str, Any],
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        required_permissions=tuple(sorted(permissions, key=lambda p: p.value)),
        input_schema=schema,
    )


_SPECS = (
    _spec(
        "action_items_due",
        "Count open action items that are due before a time, with the overdue ones.",
        (Permission.MEETING_READ,),
        _object(
            {
                "due_before": _instant("Count items due before this time (exclusive)."),
                "due_after": _instant(
                    "Count items due at or after this time. At most 366 days before due_before."
                ),
                "team_id": _one_or_many(_UUID, "Only items of conversations of these teams."),
                "assignee_user_id": _one_or_many(_UUID, "Only items assigned to these users."),
                "status": _one_or_many(
                    {"type": "string", "enum": ["in_progress", "open"]},
                    "Item statuses to count. Default: open and in_progress.",
                ),
                "group_by": _choice(
                    ("none", "status", "priority", "team_id"),
                    "Also count per group.",
                    "none",
                ),
            },
            ("due_before",),
        ),
    ),
    _spec(
        "conversation_count",
        "Count conversations in a window, in total and by status, team or period.",
        (Permission.MEETING_READ,),
        _object(
            {
                "from": _FROM,
                "to": _TO,
                "team_id": _TEAM,
                "agent_user_id": _AGENT,
                "group_by": _choice(
                    ("none", "status", "team_id", "day", "week", "month"),
                    "Also count per group; day, week and month cut at Asia/Tehran midnights.",
                    "none",
                ),
            }
        ),
    ),
    _spec(
        "issue_aggregate",
        "Count or rate trend issues (issue_count, issue_rate, issue_trend) with trend filters.",
        (Permission.MEETING_READ,),
        _object(
            {
                "intent": _choice(
                    ("issue_count", "issue_rate", "issue_trend"), "The aggregate to compute."
                ),
                "params": {
                    "type": "object",
                    "description": (
                        "Trend filters (from, to, team_id, agent_user_id, language, "
                        "source_type, family, issue, service_area) and grain for issue_trend."
                    ),
                },
            },
            ("intent", "params"),
        ),
    ),
    _spec(
        "scorecard_summary",
        "Outcomes and mean score of one published scorecard version over a window.",
        (Permission.MEETING_READ, Permission.SCORECARD_READ),
        _object(
            {
                "scorecard_version_id": {**_UUID, "description": "The scorecard version id."},
                "from": _FROM,
                "to": _TO,
                "team_id": _TEAM,
                "agent_user_id": _AGENT,
            },
            ("scorecard_version_id",),
        ),
    ),
    _spec(
        "top_issues",
        "The most frequent issues of one trend family, ranked by conversation count.",
        (Permission.MEETING_READ,),
        _object(
            {
                "family": {"type": "string", "description": "One trend family id."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                    "description": "How many issues to return.",
                },
                "from": _FROM,
                "to": _TO,
                "team_id": _TEAM,
                "agent_user_id": _AGENT,
                "language": _one_or_many(
                    {"type": "string", "pattern": "^[A-Za-z-]{2,12}$"},
                    "Only conversations in these languages.",
                ),
                "source_type": _one_or_many(
                    {"type": "string", "enum": list(SOURCE_TYPES)},
                    "Only conversations from these sources.",
                ),
            },
            ("family",),
        ),
    ),
)

TOOLS: MappingProxyType[str, ToolSpec] = MappingProxyType({spec.name: spec for spec in _SPECS})


def tool_schemas() -> list[dict[str, Any]]:
    """Every tool as ``{"name", "description", "input_schema"}``, sorted by name: the shape
    of a model's tool-use API.  A fresh copy on every call."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": copy.deepcopy(spec.input_schema),
        }
        for spec in sorted(TOOLS.values(), key=lambda spec: spec.name)
    ]


def registry_digest() -> str:
    """sha256 hex of the canonical JSON (sorted keys, no spaces, UTF-8) of
    ``tool_schemas()`` with each tool's ``required_permissions`` values added."""
    document = [
        {
            **schema,
            "required_permissions": [
                permission.value for permission in TOOLS[schema["name"]].required_permissions
            ],
        }
        for schema in tool_schemas()
    ]
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


REGISTRY_DIGEST = registry_digest()
