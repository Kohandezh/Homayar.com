"""The vector store adapter of the retrieval index (HOM-93, ADR 0068 D3).

``postgres:17-alpine`` has no pgvector.  So :class:`TableVectorStore` loads
the candidates of one workspace and one ``embedding_version`` through the live
joins of :func:`backend.retrieval.lexical.live_entries` and scores them in
Python with cosine similarity.  The scan is exact, so its cost is bounded by
``candidate_limit``.  A pgvector adapter can replace this one later without
changing callers, because callers depend on :class:`VectorStore` only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import RetrievalEmbedding
from backend.retrieval.embeddings import finite_values, unpack_vector
from backend.retrieval.lexical import ScoredEntry, live_entries
from backend.security.authorization import RecordAccess

__all__ = ["MAX_CANDIDATES", "MAX_K", "ScoredEntry", "TableVectorStore", "VectorStore"]

MAX_K = 100
MAX_CANDIDATES = 5000


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0  # a zero vector has no direction
    return dot / (norm_left * norm_right)


class VectorStore(Protocol):
    """The adapter callers depend on.  The first implementation is the table."""

    def nearest(
        self,
        db: Session,
        *,
        access: RecordAccess,
        workspace_id: str,
        embedding_version: str,
        query: Sequence[float],
        k: int = 20,
        candidate_limit: int = 2000,
        include_history: bool = False,
    ) -> list[ScoredEntry]:
        """The ``k`` entries nearest to ``query`` that the caller may see now."""
        ...


class TableVectorStore:
    """Exact cosine scan over the stored vectors of one workspace and version."""

    def nearest(
        self,
        db: Session,
        *,
        access: RecordAccess,
        workspace_id: str,
        embedding_version: str,
        query: Sequence[float],
        k: int = 20,
        candidate_limit: int = 2000,
        include_history: bool = False,
    ) -> list[ScoredEntry]:
        """The ``k`` nearest entries, score descending, then entry id.

        ``k`` must be 1..100 and ``candidate_limit`` 1..5000.  At most
        ``candidate_limit`` vectors are loaded.  A query that is empty, not
        finite, or of another length than the version's vectors is a
        ValueError.
        """
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be 1..{MAX_K}")
        if not 1 <= candidate_limit <= MAX_CANDIDATES:
            raise ValueError(f"candidate_limit must be 1..{MAX_CANDIDATES}")
        query_values = finite_values(query)
        dimensions = db.scalar(
            select(RetrievalEmbedding.dimensions)
            .where(
                RetrievalEmbedding.workspace_id == workspace_id,
                RetrievalEmbedding.embedding_version == embedding_version,
            )
            .limit(1)
        )
        if dimensions is None:
            return []  # no vector of this version in this workspace
        if len(query_values) != dimensions:
            raise ValueError(
                f"the query holds {len(query_values)} values, the version holds {dimensions}"
            )
        candidates = live_entries(
            db, access=access, workspace_id=workspace_id, include_history=include_history
        )
        if candidates is None:
            return []
        live = candidates.subquery()
        rows = db.execute(
            select(
                RetrievalEmbedding.entry_id,
                RetrievalEmbedding.vector,
                live.c.meeting_id,
                live.c.is_current,
            )
            .join(live, live.c.id == RetrievalEmbedding.entry_id)
            .where(
                RetrievalEmbedding.workspace_id == workspace_id,
                RetrievalEmbedding.embedding_version == embedding_version,
            )
            .order_by(RetrievalEmbedding.entry_id)
            .limit(candidate_limit)
        ).all()
        scored = sorted(
            ((_cosine(query_values, unpack_vector(row.vector, dimensions)), row) for row in rows),
            key=lambda pair: (-pair[0], pair[1].entry_id),
        )
        return [
            ScoredEntry(
                entry_id=row.entry_id,
                meeting_id=row.meeting_id,
                score=score,
                historical=not row.is_current,
            )
            for score, row in scored[:k]
        ]
