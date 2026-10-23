"""Minimum sample thresholds and the trend cell (HOM-79, D4).

A cell uses the counter words of ``coverage/1``: ``status``, ``numerator``,
``denominator``, ``ratio`` (4 places, ``null`` unless computed) and ``reason``, with one
new status for too few samples.  A ratio is the exact fraction of two integers, rounded
half up (away from zero) to ``RATIO_PLACES``; floats are never divided or subtracted.
The thresholds live in a versioned policy with a pinned digest, like the extraction
``POLICY_V1``.  Pure: no database, no clock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction

from backend.issue_trends.definition import SourceReason

SAMPLE_POLICY_VERSION = "trend-sample-policy/1"

# ``ratio`` and ``difference``: Fraction(n, d) quantized with Decimal ROUND_HALF_UP.
RATIO_PLACES = 4

# Tells HOM-83 how to label two trends shown together (README "Correlation is not cause").
ASSOCIATION_NOTICE = "correlation_not_cause"


class CellStatus(str, Enum):
    COMPUTED = "computed"
    NOT_COMPUTABLE = "not_computable"
    INSUFFICIENT_SAMPLE = "insufficient_sample"  # new: a denominator under the minimum


class CellReason(str, Enum):
    EMPTY_DENOMINATOR = "empty_denominator"  # the coverage word
    BELOW_MINIMUM_SAMPLE = "below_minimum_sample"
    SCORECARD_RESULTS_UNAVAILABLE = SourceReason.SCORECARD_RESULTS_UNAVAILABLE.value


class CellKind(str, Enum):
    RATE = "rate"  # one cohort: the workspace, a team, a language, a source
    AGENT = "agent"  # one agent's own conversations (self-view or a manager's view)


@dataclass(frozen=True)
class SamplePolicy:
    version: str
    min_denominator_rate: int  # a rate cell
    min_denominator_agent: int  # an agent cell
    min_denominator_comparison: int  # each side of a comparison, on top of its kind
    hide_agent_counts_below_minimum: bool  # agent cell: numerator and pending hidden


# A new number is a new version.  The reasons are in the README.
SAMPLE_POLICY_V1 = SamplePolicy(
    version=SAMPLE_POLICY_VERSION,
    min_denominator_rate=30,
    min_denominator_agent=30,
    min_denominator_comparison=50,
    hide_agent_counts_below_minimum=True,
)


def sample_policy_digest(policy: SamplePolicy) -> str:
    """sha256 of the canonical JSON of the five policy fields (sorted keys, no spaces,
    UTF-8), like ``policy_digest``."""
    canonical = json.dumps(
        asdict(policy), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_QUANTUM = Decimal(1).scaleb(-RATIO_PLACES)


def _rounded(value: Fraction) -> float:
    """``value`` rounded half up (away from zero) to ``RATIO_PLACES``: the result of
    ``Decimal`` ``ROUND_HALF_UP`` on the exact value.  It is computed with integers, so
    no float is divided and no decimal precision limit can round twice."""
    scaled = value * 10**RATIO_PLACES
    whole = (abs(scaled.numerator) * 2 + scaled.denominator) // (2 * scaled.denominator)
    signed = -whole if scaled < 0 else whole
    return float(Decimal(signed) * _QUANTUM)


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_minimum(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an integer of at least 1")
    return value


def _check_policy(policy: SamplePolicy) -> None:
    if not isinstance(policy.version, str) or not policy.version.strip():
        raise ValueError("a sample policy needs a version")
    for name in ("min_denominator_rate", "min_denominator_agent", "min_denominator_comparison"):
        _check_minimum(getattr(policy, name), name)
    if not isinstance(policy.hide_agent_counts_below_minimum, bool):
        raise ValueError("hide_agent_counts_below_minimum must be a bool")


def _kind_minimum(kind: CellKind, policy: SamplePolicy) -> int:
    if kind is CellKind.AGENT:
        return policy.min_denominator_agent
    return policy.min_denominator_rate


@dataclass(frozen=True)
class Cell:
    status: CellStatus
    numerator: int | None  # None: source unavailable, or hidden (agent, below minimum)
    denominator: int
    # float of Fraction(numerator, denominator) rounded half up to 4 places, only when
    # computed.  1/32 = 0.03125 gives 0.0313.
    ratio: float | None
    reason: CellReason | None  # None exactly when computed
    minimum_sample: int  # the minimum this cell was held to

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "ratio": self.ratio,
            "reason": self.reason.value if self.reason is not None else None,
            "minimum_sample": self.minimum_sample,
        }


def sample_cell(
    numerator: int,
    denominator: int,
    *,
    kind: CellKind = CellKind.RATE,
    policy: SamplePolicy = SAMPLE_POLICY_V1,
    minimum: int | None = None,
) -> Cell:
    """One cell.  ``minimum`` overrides the kind's minimum (``compare_cells`` uses it);
    it follows the policy rule: an int >= 1, a bool refused.

    - ``denominator == 0``: ``not_computable``, ``empty_denominator``, numerator 0.
    - ``denominator < minimum``: ``insufficient_sample``, ``below_minimum_sample``,
      ratio ``null``; the numerator stays, except an agent cell hides it under
      ``hide_agent_counts_below_minimum``.
    - else ``computed`` (the boundary ``denominator == minimum`` is computed).

    Raises ``ValueError`` for a bool or a non-int, a negative number,
    ``numerator > denominator``, a bad ``minimum``, or a bad policy (empty version, a
    minimum that is a bool, not an int or < 1, a flag that is not a bool).
    """
    _check_policy(policy)
    kind = CellKind(kind)
    if not _is_count(numerator) or not _is_count(denominator):
        raise ValueError("numerator and denominator are integers of at least 0")
    if numerator > denominator:
        raise ValueError("the numerator cannot be larger than the denominator")
    floor = _kind_minimum(kind, policy) if minimum is None else _check_minimum(minimum, "minimum")
    if denominator == 0:
        return Cell(CellStatus.NOT_COMPUTABLE, 0, 0, None, CellReason.EMPTY_DENOMINATOR, floor)
    if denominator < floor:
        hidden = kind is CellKind.AGENT and policy.hide_agent_counts_below_minimum
        return Cell(
            CellStatus.INSUFFICIENT_SAMPLE,
            None if hidden else numerator,
            denominator,
            None,
            CellReason.BELOW_MINIMUM_SAMPLE,
            floor,
        )
    return Cell(
        CellStatus.COMPUTED,
        numerator,
        denominator,
        _rounded(Fraction(numerator, denominator)),
        None,
        floor,
    )


def unavailable_cell(denominator: int, reason: CellReason, *, minimum: int) -> Cell:
    """A cell whose source does not exist yet: ``not_computable`` with ``reason``,
    numerator ``null``.  The source reason wins over ``empty_denominator``, as the
    coverage review counter does.  Raises ``ValueError`` for a negative denominator or
    a reason that is not a source reason."""
    if reason is not CellReason.SCORECARD_RESULTS_UNAVAILABLE:
        raise ValueError(f"{reason!r} is not a source reason")
    if not _is_count(denominator):
        raise ValueError("the denominator is an integer of at least 0")
    floor = _check_minimum(minimum, "minimum")
    return Cell(CellStatus.NOT_COMPUTABLE, None, denominator, None, reason, floor)


@dataclass(frozen=True)
class Comparison:
    status: CellStatus  # computed only when both sides are computed
    reason: CellReason | None  # the reason of the side that decided; None when computed
    left: Cell
    right: Cell
    # Fraction(left) - Fraction(right), exact, then rounded half up (away from zero) to
    # 4 places, only when computed.  A difference, never a cause.
    difference: float | None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason": self.reason.value if self.reason is not None else None,
            "left": self.left.to_json(),
            "right": self.right.to_json(),
            "difference": self.difference,
        }


def compare_cells(
    left: tuple[int, int],
    right: tuple[int, int],
    *,
    left_kind: CellKind = CellKind.RATE,
    right_kind: CellKind = CellKind.RATE,
    policy: SamplePolicy = SAMPLE_POLICY_V1,
) -> Comparison:
    """Two cohorts, each ``(numerator, denominator)``.  Each side is held to
    ``max(its kind's minimum, min_denominator_comparison)``.  Status: ``not_computable``
    if a side is (left first), else ``insufficient_sample`` if a side is (left first),
    else ``computed``."""
    _check_policy(policy)
    left_kind, right_kind = CellKind(left_kind), CellKind(right_kind)
    cells = []
    for (numerator, denominator), kind in ((left, left_kind), (right, right_kind)):
        floor = max(_kind_minimum(kind, policy), policy.min_denominator_comparison)
        cells.append(sample_cell(numerator, denominator, kind=kind, policy=policy, minimum=floor))
    left_cell, right_cell = cells
    for status in (CellStatus.NOT_COMPUTABLE, CellStatus.INSUFFICIENT_SAMPLE):
        for cell in cells:
            if cell.status is status:
                return Comparison(status, cell.reason, left_cell, right_cell, None)
    difference = Fraction(left[0], left[1]) - Fraction(right[0], right[1])
    return Comparison(CellStatus.COMPUTED, None, left_cell, right_cell, _rounded(difference))
