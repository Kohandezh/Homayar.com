"""QA rater agreement: Cohen's kappa (plain, linear, quadratic) and score MAE (HOM-69).

Kappa is written as weighted disagreement: ``(sum(w*E) - sum(w*O)) / sum(w*E)``, where
``O`` is the observed share of each rating pair and ``E`` the share expected from the two
raters' marginals.  With the 0/1 weights this is the usual ``(p_o - p_e) / (1 - p_e)``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from backend.evaluation.common import MetricValue, ratio

WEIGHTS = ("none", "linear", "quadratic")


@dataclass(frozen=True)
class RatingPair:
    rating_a: str | None
    rating_b: str | None
    criterion: str = ""
    item_id: str = ""
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScorePair:
    score: float
    reference: float  # adjudicated score
    item_id: str = ""
    slices: Mapping[str, str] = field(default_factory=dict)


def _weight(i: int, j: int, k: int, weights: str) -> float:
    if k == 1:
        return 0.0
    if weights == "linear":
        return abs(i - j) / (k - 1)
    if weights == "quadratic":
        return ((i - j) / (k - 1)) ** 2
    return 0.0 if i == j else 1.0


def _check_arguments(categories: Sequence[str] | None, weights: str) -> None:
    if weights not in WEIGHTS:
        raise ValueError(f"weights must be one of {WEIGHTS}, got {weights!r}")
    if weights != "none" and categories is None:
        raise ValueError(f"weights={weights!r} needs ordered categories")


def cohen_kappa(
    pairs: Iterable[RatingPair],
    *,
    categories: Sequence[str] | None = None,
    weights: str = "none",
) -> MetricValue:
    _check_arguments(categories, weights)
    pairs = list(pairs)
    included = [(p.rating_a, p.rating_b) for p in pairs
                if p.rating_a is not None and p.rating_b is not None]  # fmt: skip
    excluded = len(pairs) - len(included)
    if categories is None:
        cats = sorted({r for pair in included for r in pair})
    else:
        cats = list(dict.fromkeys(categories))
        known = set(cats)
        for pair in included:
            for r in pair:
                if r not in known:
                    raise ValueError(f"rating {r!r} is not in categories")
    n = len(included)
    if n == 0:
        return MetricValue("kappa", None, None, None, 0, excluded, reason="no_pairs")

    index = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    observed = [[0] * k for _ in range(k)]
    for a, b in included:
        observed[index[a]][index[b]] += 1
    row = [sum(observed[i]) / n for i in range(k)]
    col = [sum(observed[i][j] for i in range(k)) / n for j in range(k)]
    w_e = w_o = 0.0
    for i in range(k):
        for j in range(k):
            w = _weight(i, j, k, weights)
            w_e += w * row[i] * col[j]
            w_o += w * observed[i][j] / n
    return ratio("kappa", w_e - w_o, w_e, n_items=n, n_excluded=excluded,
                 reason="no_expected_disagreement")  # fmt: skip


def kappa_by_criterion(
    pairs: Iterable[RatingPair],
    *,
    categories: Sequence[str] | None = None,
    weights: str = "none",
) -> dict[str, MetricValue]:
    _check_arguments(categories, weights)
    groups: dict[str, list[RatingPair]] = {}
    for p in pairs:
        groups.setdefault(p.criterion, []).append(p)
    return {c: cohen_kappa(groups[c], categories=categories, weights=weights)
            for c in sorted(groups)}  # fmt: skip


def score_mae(pairs: Iterable[ScorePair]) -> MetricValue:
    pairs = list(pairs)
    try:
        total = sum(abs(p.score - p.reference) for p in pairs)
    except OverflowError:  # a float mixed with an int too large for a float
        total = math.inf
    return ratio("score_mae", total, len(pairs), n_items=len(pairs), reason="no_pairs")
