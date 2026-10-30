"""Intent classification scores with abstention (HOM-69).

``gold`` None means the right behaviour is to abstain (a contradictory fixture).
``predicted`` None means the system abstained.  An abstention on a gold label is wrong.
A predicted label outside the taxonomy is counted, and is a false negative for the gold
class only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.evaluation.common import MetricValue, metrics_dict, ratio


@dataclass(frozen=True)
class LabeledItem:
    gold: str | None  # None = the correct behaviour is to abstain
    predicted: str | None  # None = the system abstained
    item_id: str = ""
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassStats:
    label: str
    tp: int
    fp: int
    fn: int
    support: int  # tp + fn
    precision: float | None  # tp / (tp + fp)
    recall: float | None  # tp / (tp + fn)
    f1: float | None  # 2tp / (2tp + fp + fn)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def class_stats(label: str, tp: int, fp: int, fn: int) -> ClassStats:
    def div(a: int, b: int) -> float | None:
        return a / b if b else None

    return ClassStats(label, tp, fp, fn, tp + fn, div(tp, tp + fp), div(tp, tp + fn),
                      div(2 * tp, 2 * tp + fp + fn))  # fmt: skip


@dataclass(frozen=True)
class ClassificationReport:
    metrics: dict[str, MetricValue]  # "macro_f1", "accuracy", "abstention_rate", ...
    per_class: dict[str, ClassStats]  # in labels order
    n_out_of_taxonomy: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": metrics_dict(self.metrics),
            "per_class": {k: s.as_dict() for k, s in self.per_class.items()},
            "n_out_of_taxonomy": self.n_out_of_taxonomy,
        }


def evaluate_classification(
    items: Iterable[LabeledItem], *, labels: Sequence[str] | None = None
) -> ClassificationReport:
    items = list(items)
    if labels is None:
        seen = {x for it in items for x in (it.gold, it.predicted) if x is not None}
        taxonomy = sorted(seen)
    else:
        taxonomy = list(dict.fromkeys(labels))
        known = set(taxonomy)
        for it in items:
            if it.gold is not None and it.gold not in known:
                raise ValueError(f"gold label {it.gold!r} is not in labels")
    known = set(taxonomy)

    per_class: dict[str, ClassStats] = {}
    for c in taxonomy:
        tp = sum(1 for it in items if it.gold == c and it.predicted == c)
        fp = sum(1 for it in items if it.predicted == c and it.gold != c)
        fn = sum(1 for it in items if it.gold == c and it.predicted != c)
        per_class[c] = class_stats(c, tp, fp, fn)
    out_of_taxonomy = sum(1 for it in items if it.predicted is not None
                          and it.predicted not in known)  # fmt: skip

    f1s = [s.f1 for s in per_class.values() if s.f1 is not None]
    gold_items = [it for it in items if it.gold is not None]
    abstain_fixtures = [it for it in items if it.gold is None]
    abstained = sum(1 for it in items if it.predicted is None)
    correct = sum(1 for it in gold_items if it.predicted == it.gold)
    correct_abstain = sum(1 for it in abstain_fixtures if it.predicted is None)
    metrics = {
        "macro_f1": ratio("macro_f1", sum(f1s), len(f1s), n_items=len(f1s),
                          n_excluded=len(taxonomy) - len(f1s), reason="no_labels"),
        "accuracy": ratio("accuracy", correct, len(gold_items), n_items=len(gold_items),
                          n_excluded=len(abstain_fixtures),
                          n_abstained=sum(1 for it in gold_items if it.predicted is None),
                          reason="no_gold_labels"),
        "abstention_rate": ratio("abstention_rate", abstained, len(items), n_items=len(items),
                                 n_abstained=abstained, reason="no_items"),
        "correct_abstention_rate": ratio(
            "correct_abstention_rate", correct_abstain, len(abstain_fixtures),
            n_items=len(abstain_fixtures), n_abstained=correct_abstain,
            reason="no_abstention_fixtures"),
    }  # fmt: skip
    return ClassificationReport(metrics, per_class, out_of_taxonomy)
