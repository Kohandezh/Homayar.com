"""The vector store adapter of the retrieval index (HOM-93, ADR 0068 D3;
window and ``score_entries`` HOM-95, ADR 0080).

``postgres:17-alpine`` has no pgvector.  So :class:`TableVectorStore` loads
the candidates of one workspace and one ``embedding_version`` through the live
joins of :func:`backend.retrieval.lexical.live_entries` and scores them in
Python with cosine similarity.  The scan is exact, so its cost is bounded by
``candidate_limit``.  A pgvector adapter can replace this one later without
changing callers, because callers depend on :class:`VectorStore` only.

The candidate window of HOM-94 (ADR 0077 D7) was an arbitrary slice by entry
id.  HOM-95 makes it ranking-aware without an ANN index: the window is the
``candidate_limit`` authorized, filtered vectors of the NEWEST conversations
first (``conversation_at DESC, entry_id ASC``), read in batches, with only the
best ``k`` kept in a heap while scanning, so memory stays bounded by the batch
size plus ``k``.  Lexical candidates that fall outside the window are scored
anyway by :meth:`TableVectorStore.score_entries`, which is what hybrid search
fuses (ADR 0080).  Beyond 5000 authorized vectors only the newest window plus
the lexical candidates are vector-scored: the stated limit, replaceable behind
this adapter by a pgvector/ANN store.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import RetrievalEmbedding
from backend.retrieval.embeddings import finite_values, unpack_vector
from backend.retrieval.filters import SearchFilters, checked_filters, sql_safe_text
from backend.retrieval.lexical import ScoredEntry, live_entries
from backend.security.authorization import RecordAccess

__all__ = [
    "MAX_CANDIDATES",
    "MAX_ENTRY_IDS",
    "MAX_K",
    "ScoredEntry",
    "SCAN_BATCH",
    "TableVectorStore",
    "VectorStore",
]

MAX_K = 100
MAX_CANDIDATES = 5000
MAX_ENTRY_IDS = 100
# The scan reads the window in batches of at most this many rows, so memory is
# bounded by the batch plus the k heap entries, never by ``candidate_limit``.
SCAN_BATCH = 500


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0  # a zero vector has no direction
    return dot / (norm_left * norm_right)


class _Worst:
    """The heap's ordering: the WORST entry compares least, so it sits on top
    of the min-heap (a lower score is worse; on equal scores the larger entry
    id is worse, because the result order is score descending then entry id)."""

    __slots__ = ("entry_id", "historical", "meeting_id", "score")

    def __init__(self, score: float, entry_id: str, meeting_id: str, historical: bool):
        self.score = score
        self.entry_id = entry_id
        self.meeting_id = meeting_id
        self.historical = historical

    def __lt__(self, other: _Worst) -> bool:
        if self.score != other.score:
            return self.score < other.score
        return self.entry_id > other.entry_id


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
        now: datetime | None = None,
        filters: SearchFilters | None = None,
    ) -> list[ScoredEntry]:
        """The ``k`` entries nearest to ``query`` that the caller may see now."""
        ...

    def score_entries(
        self,
        db: Session,
        *,
        access: RecordAccess,
        workspace_id: str,
        embedding_version: str,
        query: Sequence[float],
        entry_ids: Sequence[str],
        include_history: bool = False,
        now: datetime | None = None,
        filters: SearchFilters | None = None,
    ) -> list[ScoredEntry]:
        """The given entry ids scored under the same live joins (HOM-95)."""
        ...


def _checked_ids(entry_ids: object) -> list[str]:
    """The distinct ids of a list or tuple of str, in first-seen order.  A
    set, a str, any other type, a non-str item or more than 100 distinct ids
    is a ``ValueError``; 100 distinct ids with duplicates are accepted."""
    if not isinstance(entry_ids, list | tuple):
        raise ValueError("entry_ids must be a list or tuple")
    asked: list[str] = []
    seen: set[str] = set()
    for item in entry_ids:
        if not isinstance(item, str):
            raise ValueError("entry_ids must hold strings only")
        if item not in seen:
            seen.add(item)
            asked.append(item)
    if len(asked) > MAX_ENTRY_IDS:
        raise ValueError(f"entry_ids holds at most {MAX_ENTRY_IDS} distinct items")
    return asked


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
        now: datetime | None = None,
        filters: SearchFilters | None = None,
    ) -> list[ScoredEntry]:
        """The ``k`` nearest entries, score descending, then entry id.

        ``k`` must be 1..100 and ``candidate_limit`` 1..5000.  At most
        ``candidate_limit`` vectors are scored: the window is the authorized,
        filtered vectors of the newest conversations first (ties by entry id
        ascending), read in batches, keeping only the best ``k`` in a heap.  A
        query that is empty, not finite, or of another length than the
        version's vectors is a ValueError.
        """
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be 1..{MAX_K}")
        if not 1 <= candidate_limit <= MAX_CANDIDATES:
            raise ValueError(f"candidate_limit must be 1..{MAX_CANDIDATES}")
        checked_filters(filters)
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
            db,
            access=access,
            workspace_id=workspace_id,
            include_history=include_history,
            now=now,
            filters=filters,
        )
        if candidates is None:
            return []
        live = candidates.subquery()
        statement = (
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
            # The HOM-95 window (ADR 0080): newest conversations first, ties by
            # entry id ascending, at most candidate_limit vectors scored.
            .order_by(live.c.conversation_at.desc(), RetrievalEmbedding.entry_id.asc())
            .limit(candidate_limit)
        )
        heap: list[_Worst] = []
        stream = db.execute(statement, execution_options={"yield_per": SCAN_BATCH})
        try:
            for row in stream:
                score = _cosine(query_values, unpack_vector(row.vector, dimensions))
                entry = _Worst(score, row.entry_id, row.meeting_id, not row.is_current)
                if len(heap) < k:
                    heapq.heappush(heap, entry)
                elif heap[0] < entry:  # the new entry beats the worst kept one
                    heapq.heapreplace(heap, entry)
        finally:
            stream.close()
        ordered = sorted(heap, key=lambda entry: (-entry.score, entry.entry_id))
        return [
            ScoredEntry(
                entry_id=entry.entry_id,
                meeting_id=entry.meeting_id,
                score=entry.score,
                historical=entry.historical,
            )
            for entry in ordered
        ]

    def score_entries(
        self,
        db: Session,
        *,
        access: RecordAccess,
        workspace_id: str,
        embedding_version: str,
        query: Sequence[float],
        entry_ids: Sequence[str],
        include_history: bool = False,
        now: datetime | None = None,
        filters: SearchFilters | None = None,
    ) -> list[ScoredEntry]:
        """Score exactly the given entry ids, score descending, then entry id.

        The same live joins, filters and query-vector checks as
        :meth:`nearest`, with no window: this is what makes hybrid ranking
        aware, because lexical candidates outside the vector window are still
        scored.  ``entry_ids`` is a list or tuple of at most 100 distinct
        strings.  An id that is not live, not authorized, filtered out, of
        another workspace, or has no vector of this version is silently
        absent: no error, no reason.
        """
        checked_filters(filters)
        asked = _checked_ids(entry_ids)
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
        # An id the database cannot take as a parameter (a NUL character, a
        # lone surrogate) names no entry: it is silently absent, like any
        # unknown id, instead of a driver error.
        queryable = [item for item in asked if sql_safe_text(item)]
        if not queryable:
            return []
        candidates = live_entries(
            db,
            access=access,
            workspace_id=workspace_id,
            include_history=include_history,
            now=now,
            filters=filters,
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
                RetrievalEmbedding.entry_id.in_(queryable),
            )
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
            for score, row in scored
        ]
