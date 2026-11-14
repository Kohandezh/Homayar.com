"""Bounded hybrid search over the retrieval index (HOM-95, ADR 0080).

One entry point fuses the lexical and the vector stage under the HOM-94
authorization (ADR 0077).  Every candidate (the lexical hits, the vector
window, the ``score_entries`` rescues, the rerank texts) comes from
``live_entries`` under the freshly resolved scope, so hybrid never widens
what ``search_evidence_lexical`` / ``search_evidence_vector`` return for the
same caller.  A denied caller is answered before any argument check runs, and
no query touches the index.

Fusion is reciprocal rank fusion with ``k = 60`` and shared ranks for equal
scores (ADR 0080).  There is no score calibration between ``ts_rank`` and
cosine, and on SQLite every lexical score is equal, so ranks, not scores, are
the common measure.  An optional local reranker (:mod:`backend.retrieval.rerank`)
then reorders only the head of the fused list.  A failing, late or malformed
reranker never fails the search.

The time budget bounds the STAGES, not the SQL: the deadline skips the vector
stage or the rerank before they start, and every single statement stays
bounded by the candidate limits (ADR 0080).  Nothing here logs entry text,
query text or vectors.
"""

from __future__ import annotations

import bisect
import math
import numbers
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from backend.database.saas_models import RetrievalEntry
from backend.retrieval.embeddings import finite_values
from backend.retrieval.evidence import (
    _checked_context,
    _checked_query,
    _required_now,
    resolve_scope,
)
from backend.retrieval.filters import SearchFilters, checked_filters
from backend.retrieval.lexical import (
    MAX_QUERY_CHARS,
    MAX_QUERY_TOKENS,
    ScoredEntry,
    lexical_search,
    live_entries,
)
from backend.retrieval.rerank import RerankCandidate, Reranker
from backend.retrieval.vector_store import MAX_CANDIDATES, VectorStore
from backend.security.authorization import RecordAccess
from backend.security.context import WorkspaceContext
from backend.speech.text_normalize import get_normalizer

__all__ = ["HybridHit", "HybridOutcome", "RRF_K", "search_evidence_hybrid"]

RRF_K = 60
# The bounds of SPEC 3.5 step 3 (ADR 0080), inclusive.
K_BOUNDS = (1, 50)
STAGE_BOUNDS = (1, 100)  # lexical_candidates and vector_candidates
WINDOW_BOUNDS = (1, MAX_CANDIDATES)  # candidate_limit
RERANK_BOUNDS = (1, 50)  # rerank_candidates
BUDGET_BOUNDS_MS = (50, 10000)  # time_budget_ms


@dataclass(frozen=True)
class HybridHit:
    """One fused result: ids, scores and ranks only, never text."""

    entry_id: str
    meeting_id: str
    score: float  # the rerank score of a reranked hit, else the fused score
    fused_score: float
    lexical_rank: int | None  # None when the entry is not in the lexical list
    vector_rank: int | None  # None when the entry is not in the vector list
    historical: bool


@dataclass(frozen=True)
class HybridOutcome:
    state: str  # "answered" or "denied"
    hits: tuple[HybridHit, ...]  # empty when denied
    degraded: tuple[str, ...]  # every shortcut taken, in stage order


def _checked_int(value: object, *, name: str, bounds: tuple[int, int]) -> int:
    """A plain int inside its bounds; a bool is not an int here."""
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int of {low}..{high}")
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")
    return value


def _shared_ranks(hits: Sequence[ScoredEntry]) -> dict[str, int]:
    """Rank = 1 + the number of hits with a strictly higher score, so equal
    scores share one rank (on SQLite every lexical score is equal: rank 1)."""
    ascending = sorted(hit.score for hit in hits)
    total = len(ascending)
    return {hit.entry_id: 1 + total - bisect.bisect_right(ascending, hit.score) for hit in hits}


def _rerank_scores(returned: object, count: int) -> list[float] | None:
    """The reranker's output as one finite float per candidate, or None when
    it is not that: not a sequence, a ``str``, a wrong count, a ``bool``, a
    non-real value, a value that does not fit a float (``10**400``) or a
    non-finite one.  Any exception while reading or converting the output
    (a ``__len__`` that raises, an ``OverflowError``) also gives None: a
    failing reranker never fails the search (SPEC 3.5)."""
    try:
        if isinstance(returned, str | bytes) or not isinstance(returned, Sequence):
            return None
        if len(returned) != count:
            return None
        scores: list[float] = []
        for value in returned:
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                return None
            score = float(value)
            if not math.isfinite(score):
                return None
            scores.append(score)
        if len(scores) != count:
            return None
        return scores
    except Exception:
        return None


def _live_texts(
    db: Session,
    *,
    access: RecordAccess,
    workspace_id: str,
    include_history: bool,
    now: datetime,
    filters: SearchFilters | None,
    entry_ids: Sequence[str],
) -> dict[str, str]:
    """The texts of ``entry_ids`` that the live joins and filters still
    return now.  Never a read by id alone, so a reranker only ever sees
    authorized text."""
    candidates = live_entries(
        db,
        access=access,
        workspace_id=workspace_id,
        include_history=include_history,
        now=now,
        filters=filters,
    )
    if candidates is None:
        return {}
    rows = db.execute(
        candidates.add_columns(RetrievalEntry.text).where(RetrievalEntry.id.in_(list(entry_ids)))
    ).all()
    return {row.id: row.text for row in rows}


def search_evidence_hybrid(
    db: Session,
    *,
    context: WorkspaceContext,
    query: str,
    now: datetime,
    store: VectorStore | None = None,
    embedding_version: str | None = None,
    query_vector: Sequence[float] | None = None,
    filters: SearchFilters | None = None,
    k: int = 10,
    lexical_candidates: int = 50,
    vector_candidates: int = 50,
    candidate_limit: int = 2000,
    reranker: Reranker | None = None,
    rerank_candidates: int = 20,
    time_budget_ms: int = 2000,
    include_history: bool = False,
    language: str = "fa",
    clock: Callable[[], float] = time.monotonic,
) -> HybridOutcome:
    """Fused lexical and vector search under a freshly resolved scope.

    Checks run in this order (SPEC 3.5): ``context`` and ``now``, then the
    scope (a denied caller gets ``("denied", (), ())`` with no index query,
    so its argument defects never surface), then every argument check, all
    before any index SQL.  ``store``, ``embedding_version`` and
    ``query_vector`` must be all set or all ``None``; all ``None`` is a
    lexical-only search.  ``degraded`` names every shortcut the time budget
    or a failing reranker forced.
    """
    _checked_context(context)
    moment = _required_now(now)
    scope = resolve_scope(db, context=context, now=moment)
    if scope is None:
        return HybridOutcome("denied", (), ())

    _checked_query(query, language)
    stripped = query.strip()
    if len(stripped) > MAX_QUERY_CHARS:
        raise ValueError(f"a query holds at most {MAX_QUERY_CHARS} characters")
    normalized = get_normalizer(language)(stripped).strip()
    if len(normalized.split()) > MAX_QUERY_TOKENS:
        raise ValueError(f"a query holds at most {MAX_QUERY_TOKENS} words")
    _checked_int(k, name="k", bounds=K_BOUNDS)
    _checked_int(lexical_candidates, name="lexical_candidates", bounds=STAGE_BOUNDS)
    _checked_int(vector_candidates, name="vector_candidates", bounds=STAGE_BOUNDS)
    _checked_int(candidate_limit, name="candidate_limit", bounds=WINDOW_BOUNDS)
    _checked_int(rerank_candidates, name="rerank_candidates", bounds=RERANK_BOUNDS)
    _checked_int(time_budget_ms, name="time_budget_ms", bounds=BUDGET_BOUNDS_MS)
    checked_filters(filters)
    vector_arguments = (store, embedding_version, query_vector)
    with_vectors = all(value is not None for value in vector_arguments)
    if not with_vectors and any(value is not None for value in vector_arguments):
        raise ValueError("store, embedding_version and query_vector must be all set or all None")
    if with_vectors:
        finite_values(query_vector)  # an empty or non-finite vector, before any SQL
    if reranker is not None and not callable(getattr(reranker, "rerank", None)):
        raise ValueError("reranker must be None or have a callable rerank")

    deadline = clock() + time_budget_ms / 1000
    expired = False

    def deadline_passed() -> bool:
        # Once passed, it stays passed, whatever a fake clock says later.
        nonlocal expired
        expired = expired or clock() > deadline
        return expired

    degraded: list[str] = []

    # Step 4: the lexical candidates, narrowed by the same filters.
    lexical_hits = lexical_search(
        db,
        access=scope.access,
        workspace_id=scope.workspace_id,
        query=query,
        limit=lexical_candidates,
        include_history=include_history,
        language=language,
        now=scope.now,
        filters=filters,
    )

    # Step 5: the newest-first window of ``nearest``, plus ``score_entries``
    # for every lexical candidate the window did not return, so the lexical
    # side is always vector-scored (the ADR 0077 D7 fix, ADR 0080).
    vector_hits: list[ScoredEntry] = []
    if with_vectors and deadline_passed():
        degraded.append("vector_skipped_time")
    elif with_vectors:
        nearest_hits = store.nearest(
            db,
            access=scope.access,
            workspace_id=scope.workspace_id,
            embedding_version=embedding_version,
            query=query_vector,
            k=vector_candidates,
            candidate_limit=candidate_limit,
            include_history=include_history,
            now=scope.now,
            filters=filters,
        )
        returned_ids = {hit.entry_id for hit in nearest_hits}
        rescue_ids = [hit.entry_id for hit in lexical_hits if hit.entry_id not in returned_ids]
        rescued = (
            store.score_entries(
                db,
                access=scope.access,
                workspace_id=scope.workspace_id,
                embedding_version=embedding_version,
                query=query_vector,
                entry_ids=rescue_ids,
                include_history=include_history,
                now=scope.now,
                filters=filters,
            )
            if rescue_ids
            else []
        )
        vector_hits = sorted([*nearest_hits, *rescued], key=lambda hit: (-hit.score, hit.entry_id))

    # Step 6: reciprocal rank fusion with shared ranks, ties by entry id.
    lexical_ranks = _shared_ranks(lexical_hits)
    vector_ranks = _shared_ranks(vector_hits)
    by_id = {hit.entry_id: hit for hit in (*lexical_hits, *vector_hits)}
    fused_score: dict[str, float] = {}
    for entry_id in by_id:
        total = 0.0
        if entry_id in lexical_ranks:
            total += 1 / (RRF_K + lexical_ranks[entry_id])
        if entry_id in vector_ranks:
            total += 1 / (RRF_K + vector_ranks[entry_id])
        fused_score[entry_id] = total
    fused_order = sorted(fused_score, key=lambda entry_id: (-fused_score[entry_id], entry_id))

    # Step 7: the reranker, on the head of the fused list only.
    order = fused_order
    rerank_scores: dict[str, float] = {}
    if reranker is not None:
        if deadline_passed():
            degraded.append("rerank_skipped_time")
        elif fused_order:
            head_ids = fused_order[:rerank_candidates]
            texts = _live_texts(
                db,
                access=scope.access,
                workspace_id=scope.workspace_id,
                include_history=include_history,
                now=scope.now,
                filters=filters,
                entry_ids=head_ids,
            )
            # An entry the live joins no longer return is neither given to
            # the reranker nor returned (SPEC revision 1, clarification 5).
            head = [entry_id for entry_id in head_ids if entry_id in texts]
            order = head + fused_order[rerank_candidates:]
            if head:
                candidates = tuple(RerankCandidate(entry_id, texts[entry_id]) for entry_id in head)
                remaining_ms = max(1, int((deadline - clock()) * 1000))
                try:
                    returned = reranker.rerank(
                        query=stripped, candidates=candidates, timeout_ms=remaining_ms
                    )
                except Exception:
                    degraded.append("rerank_failed")
                else:
                    if deadline_passed():
                        degraded.append("rerank_timeout")
                    elif (scores := _rerank_scores(returned, len(head))) is None:
                        degraded.append("rerank_failed")
                    else:
                        rerank_scores = dict(zip(head, scores, strict=True))
                        head.sort(
                            key=lambda entry_id: (
                                -rerank_scores[entry_id],
                                -fused_score[entry_id],
                                entry_id,
                            )
                        )
                        order = head + fused_order[rerank_candidates:]

    # Step 8: the first k hits.
    hits = tuple(
        HybridHit(
            entry_id=entry_id,
            meeting_id=by_id[entry_id].meeting_id,
            score=rerank_scores.get(entry_id, fused_score[entry_id]),
            fused_score=fused_score[entry_id],
            lexical_rank=lexical_ranks.get(entry_id),
            vector_rank=vector_ranks.get(entry_id),
            historical=by_id[entry_id].historical,
        )
        for entry_id in order[:k]
    )
    return HybridOutcome("answered", hits, tuple(degraded))
