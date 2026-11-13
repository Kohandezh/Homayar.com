"""The result of one analytical tool run (HOM-96): ``ToolResult`` and its ``Fact`` items.

Pure: no database, no clock, no network.  ``ToolResult.to_json()`` is deterministic: the
same database state and the same inputs give the same JSON, byte for byte, with
``json.dumps(..., sort_keys=True)``.  It holds no execution time, no random value and no
wall clock.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from backend.copilot_tools.registry import TOOLS_VERSION, ToolLimits


class ResultStatus(StrEnum):
    ANSWERED = "answered"
    ABSTAIN = "abstain"


class AbstainReason(StrEnum):
    """Why a tool gives no number.  The last five are HOM-92 ``RefusalReason`` values that
    ``issue_aggregate`` and ``top_issues`` pass through unchanged."""

    TOOL_UNKNOWN = "tool_unknown"
    PERMISSION_DENIED = "permission_denied"
    PARAMS_INVALID = "params_invalid"
    SCOPE_NOT_ALLOWED = "scope_not_allowed"
    UNSUPPORTED_FILTER = "unsupported_filter"
    INDIVIDUAL_RANKING_NOT_SUPPORTED = "individual_ranking_not_supported"
    INTENT_NOT_SUPPORTED = "intent_not_supported"
    ROW_LIMIT_EXCEEDED = "row_limit_exceeded"
    TIME_LIMIT_EXCEEDED = "time_limit_exceeded"
    BELOW_MINIMUM_SAMPLE = "below_minimum_sample"
    NOT_COMPUTABLE = "not_computable"
    WITHHELD = "withheld"
    # HOM-92 routing refusals, passed through
    OUT_OF_SCOPE = "out_of_scope"
    INVALID_REQUEST = "invalid_request"
    OUTSIDE_CALLER_SCOPE = "outside_caller_scope"
    CAUSAL_NOT_SUPPORTED = "causal_not_supported"
    FORECAST_NOT_SUPPORTED = "forecast_not_supported"


class ParamDefect(StrEnum):
    """``detail`` of a ``params_invalid`` abstention.  An ``issue_trends`` filter error keeps
    its own code (``filter_bad_value``, ``filter_window_invalid``, ...)."""

    PARAMS_NOT_MAPPING = "params_not_mapping"
    PARAM_UNKNOWN = "param_unknown"
    PARAM_MISSING = "param_missing"
    PARAM_BAD_TYPE = "param_bad_type"
    PARAM_BAD_VALUE = "param_bad_value"


def round_half_even_2(value: Fraction) -> float:
    """``value`` rounded half-even to 2 decimals, exactly, as a float.  The rounding is done
    on integers, then ``Decimal`` gives the 2-place value: no float arithmetic before it."""
    scaled = value * 100
    whole, rest = divmod(scaled.numerator, scaled.denominator)
    twice = 2 * rest
    if twice > scaled.denominator or (twice == scaled.denominator and whole % 2 == 1):
        whole += 1
    return float(Decimal(whole).scaleb(-2).quantize(Decimal("0.01")))


def _frozen(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    return None if value is None else MappingProxyType(copy.deepcopy(dict(value)))


@dataclass(frozen=True)
class Fact:
    """One number of an answer and the records behind it."""

    name: str
    group: Mapping[str, Any] | None
    value: int | float
    parts: tuple[int, int] | None  # (numerator, denominator) of a ratio or a mean
    citations: tuple[str, ...]  # the first max_citations ids, ascending
    citations_total: int
    citations_complete: bool
    citations_withheld: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "group", _frozen(self.group))
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            raise ValueError("value must be an int or a float")
        if list(self.citations) != sorted(self.citations):
            raise ValueError("citations must be sorted")
        if self.citations_withheld and (self.citations or self.citations_complete):
            raise ValueError("withheld citations are empty and not complete")

    @classmethod
    def cited(
        cls,
        name: str,
        *,
        value: int | float,
        ids: Iterable[str],
        max_citations: int,
        group: Mapping[str, Any] | None = None,
        parts: tuple[int, int] | None = None,
    ) -> Fact:
        """A fact whose supporting records are ``ids`` (each id once)."""
        ordered = sorted(ids)
        if len(set(ordered)) != len(ordered):
            raise ValueError("a record supports a number once")
        return cls(
            name=name,
            group=group,
            value=value,
            parts=parts,
            citations=tuple(ordered[:max_citations]),
            citations_total=len(ordered),
            citations_complete=len(ordered) <= max_citations,
            citations_withheld=False,
        )

    @classmethod
    def withheld(
        cls,
        name: str,
        *,
        value: int | float,
        total: int,
        group: Mapping[str, Any] | None = None,
        parts: tuple[int, int] | None = None,
    ) -> Fact:
        """A fact whose record list must not be shown (a trend cell that is not computed)."""
        return cls(
            name=name,
            group=group,
            value=value,
            parts=parts,
            citations=(),
            citations_total=total,
            citations_complete=False,
            citations_withheld=True,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": None if self.group is None else copy.deepcopy(dict(self.group)),
            "value": self.value,
            "parts": (
                None
                if self.parts is None
                else {"numerator": self.parts[0], "denominator": self.parts[1]}
            ),
            "citations": list(self.citations),
            "citations_total": self.citations_total,
            "citations_complete": self.citations_complete,
            "citations_withheld": self.citations_withheld,
        }


@dataclass(frozen=True)
class ToolResult:
    """``answered`` with facts, or ``abstain`` with a reason.  An abstention has no filters,
    no facts and no provenance, and never holds a param value or an id."""

    tool: str | None  # None only for tool_unknown: the unknown name is never echoed
    status: ResultStatus
    reason: AbstainReason | None
    detail: str | None
    key: str | None  # a param key, never a value
    filters: Mapping[str, Any] | None
    restricted: bool  # RecordAccess.restricted of the caller
    facts: tuple[Fact, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)
    limits: ToolLimits | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", _frozen(self.filters))
        object.__setattr__(self, "provenance", _frozen(self.provenance))
        if not isinstance(self.limits, ToolLimits):
            raise ValueError("limits must be a ToolLimits")
        if self.status is ResultStatus.ANSWERED:
            if self.reason is not None or self.detail is not None or self.key is not None:
                raise ValueError("an answered result has no reason, detail or key")
            if self.filters is None or self.tool is None:
                raise ValueError("an answered result has a tool and filters")
        elif self.status is ResultStatus.ABSTAIN:
            if not isinstance(self.reason, AbstainReason):
                raise ValueError("an abstention has an AbstainReason")
            if self.filters is not None or self.facts or self.provenance:
                raise ValueError("an abstention has no filters, facts or provenance")
        else:
            raise ValueError("status must be a ResultStatus")

    @classmethod
    def abstain(
        cls,
        tool: str | None,
        reason: AbstainReason,
        *,
        restricted: bool,
        limits: ToolLimits,
        detail: str | None = None,
        key: str | None = None,
    ) -> ToolResult:
        return cls(
            tool=tool,
            status=ResultStatus.ABSTAIN,
            reason=reason,
            detail=detail,
            key=key,
            filters=None,
            restricted=restricted,
            facts=(),
            provenance={},
            limits=limits,
        )

    @classmethod
    def answered(
        cls,
        tool: str,
        *,
        filters: Mapping[str, Any],
        facts: Iterable[Fact],
        restricted: bool,
        limits: ToolLimits,
        provenance: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            tool=tool,
            status=ResultStatus.ANSWERED,
            reason=None,
            detail=None,
            key=None,
            filters=filters,
            restricted=restricted,
            facts=tuple(facts),
            provenance=provenance or {},
            limits=limits,
        )

    def to_json(self) -> dict[str, Any]:
        assert self.limits is not None  # __post_init__ checks it
        return {
            "tools_version": TOOLS_VERSION,
            "tool": self.tool,
            "status": self.status.value,
            "reason": self.reason.value if self.reason is not None else None,
            "detail": self.detail,
            "key": self.key,
            "filters": None if self.filters is None else copy.deepcopy(dict(self.filters)),
            "scope": {"restricted": self.restricted},
            "facts": [fact.to_json() for fact in self.facts],
            "provenance": copy.deepcopy(dict(self.provenance)),
            "limits": self.limits.to_json(),
        }
