"""The local reranker adapter of hybrid search (HOM-95, ADR 0080).

A reranker re-scores a shortlist of already-authorized candidates against the
query, so hybrid search can put the most relevant evidence first without
trusting lexical or cosine scores as relevance.  Only a LOCAL reranker may
implement :class:`Reranker`: no network, no remote API.  This module ships the
protocol only: no model, no adapter, no new dependency. Tests use fakes.

The seam is deliberately narrow.  The reranker receives only candidates that
already passed the authorization and the search filters, and only their entry
id and text (never meetings, callers or vectors), plus the stripped query and
a millisecond budget.  It returns one score per candidate, same order, higher
is better.  A reranker that raises, returns after its budget, returns a wrong
count or a non-finite or non-number score never fails the search: its scores
are discarded and the fused order stands (ADR 0080).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = ["RerankCandidate", "Reranker"]


@dataclass(frozen=True)
class RerankCandidate:
    """One candidate a reranker sees: the entry id and its text, nothing else."""

    entry_id: str
    text: str


class Reranker(Protocol):
    """A local cross-encoder-style reranker behind an adapter."""

    def rerank(
        self, *, query: str, candidates: Sequence[RerankCandidate], timeout_ms: int
    ) -> Sequence[float]:
        """One score per candidate, same order, higher is better."""
        ...
