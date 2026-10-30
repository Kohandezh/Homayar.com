"""Version, the metric value type and per-slice evaluation (HOM-69).

Every metric in this package is a :class:`MetricValue`.  When a metric cannot be
measured, ``value`` is None and ``reason`` says why.  It is never 0 as a stand-in.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

EVALUATION_VERSION = "evaluation/1"
NON_FINITE_RESULT = "non_finite_result"  # finite inputs that overflowed in float math

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class MetricValue:
    """One metric: ``value == numerator / denominator``, or None with a ``reason``."""

    name: str
    value: float | None
    numerator: float | None
    denominator: float | None
    n_items: int  # units that were scored
    n_excluded: int = 0  # input units not scorable
    n_abstained: int = 0  # units where the system abstained
    reason: str | None = None  # set when value is None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ratio(
    name: str,
    numerator: float,
    denominator: float,
    *,
    n_items: int,
    reason: str,
    n_excluded: int = 0,
    n_abstained: int = 0,
) -> MetricValue:
    """``numerator / denominator``, or None with ``reason`` when the denominator is 0.

    Finite inputs can still overflow in float arithmetic.  A numerator, denominator or
    value that is not finite, or an int too large for a float, gives None for all three,
    with ``non_finite_result``.
    """
    try:
        if math.isfinite(numerator) and math.isfinite(denominator):
            if not denominator:
                return MetricValue(name, None, numerator, denominator, n_items, n_excluded,
                                   n_abstained, reason)  # fmt: skip
            value = numerator / denominator
            if math.isfinite(value):
                return MetricValue(name, value, numerator, denominator, n_items, n_excluded,
                                   n_abstained)  # fmt: skip
    except OverflowError:  # an int too large for a float
        pass
    return MetricValue(name, None, None, None, n_items, n_excluded, n_abstained,
                       NON_FINITE_RESULT)  # fmt: skip


def metrics_dict(metrics: dict[str, MetricValue]) -> dict[str, Any]:
    return {k: m.as_dict() for k, m in metrics.items()}


def evaluate_by_slice(
    items: Iterable[T],
    evaluate: Callable[[list[T]], R],
    dimensions: Sequence[str],
) -> dict[str, R]:
    """``evaluate`` on all items (key ``all``) and on each ``dim=value`` slice.

    An item without a dimension in its ``slices`` is only in ``all``.  Items keep their
    input order inside each slice.  Slice keys after ``all`` are sorted.
    """
    all_items = list(items)
    groups: dict[str, list[T]] = {}
    for it in all_items:
        slices = getattr(it, "slices", None) or {}
        for dim in dimensions:
            if dim in slices:
                groups.setdefault(f"{dim}={slices[dim]}", []).append(it)
    out = {"all": evaluate(all_items)}
    for key in sorted(groups):
        out[key] = evaluate(groups[key])
    return out
