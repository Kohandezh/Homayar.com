"""Entity extraction precision, recall and F1 (HOM-69).

Matching is exact, per item and per type, as a multiset.  ``value`` matching compares
``normalize(value)``; ``span`` matching compares ``(type, start, end)``.  Per-type recall
is the exact-match rate that the critical-entity gate reads.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.evaluation.classification import ClassStats, class_stats
from backend.evaluation.common import MetricValue, metrics_dict, ratio

MATCH_MODES = ("value", "span")


@dataclass(frozen=True)
class EntityMention:
    type: str
    value: str
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class EntityItem:
    gold: Sequence[EntityMention]
    predicted: Sequence[EntityMention]
    item_id: str = ""
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityReport:
    metrics: dict[str, MetricValue]  # "precision", "recall", "f1" (micro)
    per_type: dict[str, ClassStats]  # sorted by type

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": metrics_dict(self.metrics),
            "per_type": {k: s.as_dict() for k, s in self.per_type.items()},
        }


def evaluate_entities(
    items: Iterable[EntityItem],
    *,
    match: str = "value",
    normalize: Callable[[str], str] | None = None,
) -> EntityReport:
    if match not in MATCH_MODES:
        raise ValueError(f"match must be one of {MATCH_MODES}, got {match!r}")
    norm = normalize or (lambda v: v)

    def key(m: EntityMention) -> Hashable:
        if match == "span":
            if m.start is None or m.end is None:
                raise ValueError(f"span matching needs start and end: {m!r}")
            return (m.type, m.start, m.end)
        return (m.type, norm(m.value))

    counts: dict[str, list[int]] = {}  # type -> [tp, fp, fn]
    n_items = 0
    for it in items:
        n_items += 1
        gold = Counter(key(m) for m in it.gold)
        pred = Counter(key(m) for m in it.predicted)
        for k in gold.keys() | pred.keys():
            tp = min(gold[k], pred[k])
            c = counts.setdefault(k[0], [0, 0, 0])  # type: ignore[index]
            c[0] += tp
            c[1] += pred[k] - tp
            c[2] += gold[k] - tp

    per_type = {t: class_stats(t, *counts[t]) for t in sorted(counts)}
    tp = sum(s.tp for s in per_type.values())
    fp = sum(s.fp for s in per_type.values())
    fn = sum(s.fn for s in per_type.values())
    metrics = {
        "precision": ratio("precision", tp, tp + fp, n_items=n_items, reason="no_entities"),
        "recall": ratio("recall", tp, tp + fn, n_items=n_items, reason="no_entities"),
        "f1": ratio("f1", 2 * tp, 2 * tp + fp + fn, n_items=n_items, reason="no_entities"),
    }
    return EntityReport(metrics, per_type)
