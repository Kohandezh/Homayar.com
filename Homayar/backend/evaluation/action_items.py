"""Action item owner, due date and kind against gold labels (HOM-133, ADR 0058).

Each case is one provider item: the gold answer (keep it or not, owner, due date, kind)
and what the system kept.  The owner is compared after ``normalize_for_matching``; the
due date is compared as a ``date``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.evaluation.common import MetricValue, metrics_dict, ratio
from backend.speech.text_normalize import normalize_for_matching


@dataclass(frozen=True)
class ActionItemCase:
    gold_keep: bool
    gold_owner: str | None
    gold_due: date | None
    gold_kind: str | None
    predicted_keep: bool
    predicted_owner: str | None
    predicted_due: date | None
    predicted_kind: str | None
    item_id: str = ""
    language: str = "fa"
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionItemReport:
    # "owner_precision", "due_precision", "owner_unset_rate", "due_unset_rate",
    # "kind_accuracy", "keep_precision"
    metrics: dict[str, MetricValue]
    wrong_owner_ids: tuple[str, ...]  # predicted owner not equal to gold, input order
    wrong_due_ids: tuple[str, ...]  # predicted due date not equal to gold, input order

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": metrics_dict(self.metrics),
            "wrong_owner_ids": list(self.wrong_owner_ids),
            "wrong_due_ids": list(self.wrong_due_ids),
        }


def _norm(text: str, language: str) -> str:
    try:
        return normalize_for_matching(text, language).strip()
    except KeyError:
        return normalize_for_matching(text, "fa").strip()


def _owner_matches(case: ActionItemCase) -> bool:
    if case.predicted_owner is None or case.gold_owner is None:
        return False
    return _norm(case.predicted_owner, case.language) == _norm(case.gold_owner, case.language)


def evaluate_action_items(items: Iterable[ActionItemCase]) -> ActionItemReport:
    items = list(items)
    both = [c for c in items if c.gold_keep and c.predicted_keep]
    excluded = len(items) - len(both)

    owner_given = [c for c in both if c.predicted_owner is not None]
    owner_right = [c for c in owner_given if _owner_matches(c)]
    due_given = [c for c in both if c.predicted_due is not None]
    due_right = [c for c in due_given if c.predicted_due == c.gold_due]
    owner_gold_none = [c for c in both if c.gold_owner is None]
    due_gold_none = [c for c in both if c.gold_due is None]
    kind_gold = [c for c in both if c.gold_kind is not None]
    kind_right = sum(1 for c in kind_gold if c.predicted_kind == c.gold_kind)
    kept = [c for c in items if c.predicted_keep]
    kept_right = sum(1 for c in kept if c.gold_keep)

    metrics = {
        "owner_precision": ratio(
            "owner_precision", len(owner_right), len(owner_given),
            n_items=len(owner_given), n_excluded=excluded,
            n_abstained=len(both) - len(owner_given), reason="no_predicted_owners",
        ),
        "due_precision": ratio(
            "due_precision", len(due_right), len(due_given),
            n_items=len(due_given), n_excluded=excluded,
            n_abstained=len(both) - len(due_given), reason="no_predicted_due_dates",
        ),
        "owner_unset_rate": ratio(
            "owner_unset_rate",
            sum(1 for c in owner_gold_none if c.predicted_owner is None),
            len(owner_gold_none), n_items=len(owner_gold_none), n_excluded=excluded,
            reason="no_unstated_owners",
        ),
        "due_unset_rate": ratio(
            "due_unset_rate",
            sum(1 for c in due_gold_none if c.predicted_due is None),
            len(due_gold_none), n_items=len(due_gold_none), n_excluded=excluded,
            reason="no_unstated_due_dates",
        ),
        "kind_accuracy": ratio(
            "kind_accuracy", kind_right, len(kind_gold), n_items=len(kind_gold),
            n_excluded=len(items) - len(kind_gold), reason="no_gold_kinds",
        ),
        "keep_precision": ratio(
            "keep_precision", kept_right, len(kept), n_items=len(kept),
            n_abstained=len(items) - len(kept), reason="no_kept_items",
        ),
    }  # fmt: skip
    wrong_owner = tuple(c.item_id for c in owner_given if not _owner_matches(c))
    wrong_due = tuple(c.item_id for c in due_given if c.predicted_due != c.gold_due)
    return ActionItemReport(metrics, wrong_owner, wrong_due)
