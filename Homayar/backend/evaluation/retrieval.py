"""Retrieval ranking scores: recall, precision, hit and nDCG at k, and MRR (HOM-69).

Relevance is graded: an int per judged document, above 0 means relevant, unjudged counts
as 0.  Duplicate ids in the ranking keep their first place only, before any cut.  Each
metric is the mean over queries that have at least one relevant document.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.evaluation.common import MetricValue, metrics_dict, ratio


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    relevance: Mapping[str, int]  # doc_id -> graded relevance; > 0 means relevant
    ranked: Sequence[str]  # system output, best first
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalReport:
    metrics: dict[str, MetricValue]  # "mrr" and "recall@k", "precision@k", "hit@k", "ndcg@k"

    def as_dict(self) -> dict[str, Any]:
        return {"metrics": metrics_dict(self.metrics)}


def _dcg(gains: Sequence[int]) -> float:
    """DCG, or inf when a gain is too large for a float (``ldexp`` never builds a big int)."""
    terms = []
    for i, rel in enumerate(gains):
        try:
            gain = math.ldexp(1.0, rel) - 1  # 2 ** rel - 1
        except OverflowError:
            return math.inf
        terms.append(gain / math.log2(i + 2))
    return sum(terms)  # sum() of floats is compensated; a plain += loop rounds differently


def evaluate_retrieval(
    queries: Iterable[RetrievalQuery], *, ks: Sequence[int] = (1, 5, 10)
) -> RetrievalReport:
    ks = list(ks)
    for k in ks:
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise ValueError(f"k must be a positive int, got {k!r}")
    ks = list(dict.fromkeys(ks))  # a repeated k is scored once
    names = ["mrr"] + [f"{m}@{k}" for k in ks for m in ("recall", "precision", "hit", "ndcg")]
    sums = dict.fromkeys(names, 0.0)
    scored = excluded = abstained = 0
    for q in queries:
        for doc, rel in q.relevance.items():
            if rel < 0:
                raise ValueError(f"query {q.query_id!r}: negative relevance for {doc!r}")
        n_relevant = sum(1 for rel in q.relevance.values() if rel > 0)
        if n_relevant == 0:
            excluded += 1
            continue
        scored += 1
        ranked = list(dict.fromkeys(q.ranked))
        if not ranked:
            abstained += 1
        gains = [q.relevance.get(d, 0) for d in ranked]
        ideal = sorted(q.relevance.values(), reverse=True)
        first = next((i for i, g in enumerate(gains) if g > 0), None)
        sums["mrr"] += 0.0 if first is None else 1 / (first + 1)
        for k in ks:
            hits = sum(1 for g in gains[:k] if g > 0)
            sums[f"recall@{k}"] += hits / n_relevant
            sums[f"precision@{k}"] += hits / k
            sums[f"hit@{k}"] += 1.0 if hits else 0.0
            dcg, idcg = _dcg(gains[:k]), _dcg(ideal[:k])
            finite = math.isfinite(dcg) and math.isfinite(idcg)
            sums[f"ndcg@{k}"] += dcg / idcg if finite else math.nan  # nan: non_finite_result
    metrics = {
        name: ratio(name, sums[name], scored, n_items=scored, n_excluded=excluded,
                    n_abstained=abstained, reason="no_relevant_documents")
        for name in names
    }  # fmt: skip
    return RetrievalReport(metrics)
