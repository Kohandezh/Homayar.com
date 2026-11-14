"""HOM-94 SPEC 3.5 A: the filtered-recall harness over a fixture corpus.

The corpus (``_retrieval_corpus.py``) is deterministic: fixed seed, no
network, no clock.  The ground truth is computed in Python from the fixture's
own metadata, never from the SQL of the code under test.  The harness proves
for every persona:

* the mean recall@10 of ``search_evidence_vector`` at a full
  ``candidate_limit`` is exactly 1.0 on this store and never below 0.90, and
  no returned id is outside the persona's authorized set (leaks == 0);
* a small ``candidate_limit`` keeps leaks at zero (recall may fall: the
  stated limit of the exact scan);
* the post-filter control (an unrestricted top-10, then the Python ACL
  filter) loses recall for the team-1 supervisor and the team-2 agent, so
  the corpus really forces pre-filtering;
* lexical token queries return exactly the authorized truth set.

HOM-95 (SPEC 3.7) adds the hybrid harness: per persona, hybrid recall@10 with
the cluster token plus the cluster's query vector, with a text that matches
nothing (vector-only rescue), under two filter sets against the filtered
Python truth, and the D7 evidence at a 20-vector window.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.database.relational import get_session_factory
from backend.database.tenant_context import bind_tenant_context
from backend.retrieval.evidence import search_evidence_lexical, search_evidence_vector
from backend.retrieval.filters import SearchFilters
from backend.retrieval.hybrid import search_evidence_hybrid
from backend.retrieval.vector_store import TableVectorStore
from backend.tests._retrieval_corpus import (
    LEXICAL_QUERIES,
    NOW,
    QUERIES_PER_CLUSTER,
    QUERY_VECTORS,
    authorized,
    build_corpus,
    hybrid_truth,
    lexical_truth,
    passes_filters,
    persona_context,
    post_filter_baseline,
    ranked_truth,
    recall_fraction,
)

PERSONAS = ("admin", "supervisor1", "agent2", "member", "admin_b")
FULL_CANDIDATE_LIMIT = 1000
SMALL_CANDIDATE_LIMIT = 20


@pytest.fixture
def corpus(client, email_outbox):
    return build_corpus(client, email_outbox)


def _workspace_of(corpus, persona_key: str) -> str:
    persona = corpus.persona(persona_key)
    return corpus.workspace_a if persona.workspace == "A" else corpus.workspace_b


def _authorized_size(corpus, persona_key: str) -> int:
    persona = corpus.persona(persona_key)
    return sum(1 for entry in corpus.entries if authorized(persona, entry))


def _measure_vector(corpus, persona_key: str, *, candidate_limit: int) -> tuple[Decimal, int]:
    """Mean recall@10 and the leak count of the persona over the 24 queries."""
    persona = corpus.persona(persona_key)
    allowed_size = _authorized_size(corpus, persona_key)
    by_id = {entry.entry_id: entry for entry in corpus.entries}
    context = persona_context(corpus, persona_key)
    recalls: list[Decimal] = []
    leaks = 0
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=_workspace_of(corpus, persona_key))
        for query in QUERY_VECTORS:
            outcome = search_evidence_vector(
                db,
                context=context,
                store=TableVectorStore(),
                embedding_version=corpus.embedding_version,
                query_vector=query,
                now=NOW,
                k=10,
                candidate_limit=candidate_limit,
            )
            assert outcome.state == "answered", persona_key
            returned = [hit.entry_id for hit in outcome.hits]
            truth = ranked_truth(corpus, persona, query)
            leaks += sum(1 for entry_id in returned if not authorized(persona, by_id[entry_id]))
            recalls.append(recall_fraction(returned, truth, allowed_size))
    mean = sum(recalls, Decimal(0)) / Decimal(len(recalls))
    return mean, leaks


def test_filtered_recall_at_10_is_exact_and_leak_free(corpus):
    report = [f"persona report (candidate_limit={FULL_CANDIDATE_LIMIT})"]
    for key in PERSONAS:
        mean, leaks = _measure_vector(corpus, key, candidate_limit=FULL_CANDIDATE_LIMIT)
        report.append(f"{key:12s} mean recall@10={mean} leaks={leaks}")
        assert leaks == 0, key
        assert mean >= Decimal("0.90"), key
        assert mean == Decimal(1), key
    print("\n".join(report))


def test_small_candidate_limit_keeps_leaks_at_zero(corpus):
    report = [f"persona report (candidate_limit={SMALL_CANDIDATE_LIMIT})"]
    for key in PERSONAS:
        mean, leaks = _measure_vector(corpus, key, candidate_limit=SMALL_CANDIDATE_LIMIT)
        report.append(f"{key:12s} mean recall@10={mean} leaks={leaks}")
        assert leaks == 0, key
        # Recall may fall (the stated limit); it stays a number in [0, 1].
        assert Decimal(0) <= mean <= Decimal(1), key
    print("\n".join(report))


def test_post_filter_control_loses_recall_for_restricted_personas(corpus):
    """An unrestricted top-10 with a Python filter after it cannot reach 0.90
    for the supervisor or the agent, so pre-filtering inside the SQL is what
    the recall promise rests on."""
    report = ["post-filter control (unrestricted top-10, then the Python filter)"]
    for key in ("supervisor1", "agent2"):
        persona = corpus.persona(key)
        allowed_size = _authorized_size(corpus, key)
        recalls = [
            recall_fraction(
                post_filter_baseline(corpus, persona, query),
                ranked_truth(corpus, persona, query),
                allowed_size,
            )
            for query in QUERY_VECTORS
        ]
        mean = sum(recalls, Decimal(0)) / Decimal(len(recalls))
        report.append(f"{key:12s} mean recall@10={mean}")
        assert mean < Decimal("0.90"), key
    print("\n".join(report))


def test_lexical_returns_exactly_the_authorized_truth_set(corpus):
    report = ["lexical truth-set equality"]
    for key in PERSONAS:
        persona = corpus.persona(key)
        context = persona_context(corpus, key)
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=_workspace_of(corpus, key))
            for query in LEXICAL_QUERIES:
                outcome = search_evidence_lexical(
                    db, context=context, query=query, now=NOW, limit=100
                )
                assert outcome.state == "answered", key
                returned = {hit.entry_id for hit in outcome.hits}
                truth = lexical_truth(corpus, persona, query)
                assert returned == truth, (key, query)
        report.append(f"{key:12s} {len(LEXICAL_QUERIES)} queries, all exact")
    print("\n".join(report))


# --- HOM-95: the hybrid harness (SPEC 3.7) --------------------------------------------------

HYBRID_FILTER_SETS = {
    "types": SearchFilters(conversation_types={"meeting", "voicemail"}),
    "dates+confidence": SearchFilters(
        started_from=NOW - timedelta(days=16),
        started_before=NOW - timedelta(days=3),
        min_confidence=0.7,
    ),
}
D7_CANDIDATE_LIMIT = 20
RESCUE_TEXT = "nomatch"  # matches no entry: the vector stage alone answers


def _cluster_text(query_index: int) -> str:
    return f"tok{query_index // QUERIES_PER_CLUSTER}"


def _measure_hybrid(
    corpus,
    persona_key: str,
    *,
    text_of,
    filters: SearchFilters | None = None,
    candidate_limit: int = 2000,
    queries: list[int] | None = None,
) -> tuple[Decimal, int, int]:
    """Mean recall@10 against the filtered Python truth, the leak count and
    the count of returned entries that fail the filter predicate."""
    persona = corpus.persona(persona_key)
    by_id = {entry.entry_id: entry for entry in corpus.entries}
    filtered_size = sum(
        1
        for entry in corpus.entries
        if authorized(persona, entry) and passes_filters(entry, filters)
    )
    assert filtered_size >= 1, (persona_key, filters)
    context = persona_context(corpus, persona_key)
    recalls: list[Decimal] = []
    leaks = 0
    outside_filter = 0
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=_workspace_of(corpus, persona_key))
        for index in queries if queries is not None else range(len(QUERY_VECTORS)):
            query_vector = QUERY_VECTORS[index]
            outcome = search_evidence_hybrid(
                db,
                context=context,
                query=text_of(index),
                now=NOW,
                store=TableVectorStore(),
                embedding_version=corpus.embedding_version,
                query_vector=query_vector,
                filters=filters,
                k=10,
                candidate_limit=candidate_limit,
                # A clock that never moves: no stage is skipped for time on a
                # slow runner, so the recall measures the full pipeline.
                clock=lambda: 0.0,
            )
            assert outcome.state == "answered", persona_key
            assert outcome.degraded == (), persona_key
            returned = [hit.entry_id for hit in outcome.hits]
            leaks += sum(1 for entry_id in returned if not authorized(persona, by_id[entry_id]))
            outside_filter += sum(
                1 for entry_id in returned if not passes_filters(by_id[entry_id], filters)
            )
            truth = hybrid_truth(corpus, persona, query_vector, filters)
            recalls.append(recall_fraction(returned, truth, filtered_size))
    if not recalls:
        return Decimal(0), leaks, outside_filter
    return sum(recalls, Decimal(0)) / Decimal(len(recalls)), leaks, outside_filter


def test_hybrid_recall_with_text_and_vector_is_leak_free(corpus):
    report = ["hybrid report (text tok<c> + the cluster query vector)"]
    for key in PERSONAS:
        mean, leaks, _outside = _measure_hybrid(corpus, key, text_of=_cluster_text)
        report.append(f"{key:12s} mean recall@10={mean} leaks={leaks}")
        assert leaks == 0, key
        assert mean >= Decimal("0.90"), key
    print("\n".join(report))


def test_hybrid_recall_with_a_vector_only_rescue_text(corpus):
    report = [f"hybrid report (text {RESCUE_TEXT!r} matches nothing: vector stage only)"]
    for key in PERSONAS:
        mean, leaks, _outside = _measure_hybrid(corpus, key, text_of=lambda _index: RESCUE_TEXT)
        report.append(f"{key:12s} mean recall@10={mean} leaks={leaks}")
        assert leaks == 0, key
        assert mean >= Decimal("0.90"), key
    print("\n".join(report))


@pytest.mark.parametrize(
    "filter_set", [pytest.param(name, id=name) for name in sorted(HYBRID_FILTER_SETS)]
)
def test_hybrid_recall_under_filters_against_the_filtered_truth(corpus, filter_set):
    filters = HYBRID_FILTER_SETS[filter_set]
    report = [f"hybrid report (filters {filter_set}: {filters})"]
    for key in PERSONAS:
        persona = corpus.persona(key)
        kept = sum(
            1
            for entry in corpus.entries
            if authorized(persona, entry) and passes_filters(entry, filters)
        )
        allowed = _authorized_size(corpus, key)
        assert 0 < kept < allowed, (key, kept, allowed)  # the filter really narrows
        mean, leaks, outside = _measure_hybrid(corpus, key, text_of=_cluster_text, filters=filters)
        report.append(
            f"{key:12s} kept={kept}/{allowed} mean recall@10={mean} "
            f"leaks={leaks} outside_filter={outside}"
        )
        assert leaks == 0, key
        assert outside == 0, key
        assert mean >= Decimal("0.90"), key
    print("\n".join(report))


def _carried_queries(corpus, persona_key: str) -> list[int]:
    """The queries whose true top 10 all hold the cluster token: the text
    alone carries them, whatever the vector window holds."""
    persona = corpus.persona(persona_key)
    by_id = {entry.entry_id: entry for entry in corpus.entries}
    return [
        index
        for index, query_vector in enumerate(QUERY_VECTORS)
        if all(
            _cluster_text(index) in by_id[entry_id].text.split()
            for entry_id in ranked_truth(corpus, persona, query_vector)
        )
    ]


def test_d7_lexical_candidates_are_vector_scored_outside_the_window(corpus):
    """The HOM-94 D7 follow-up (ADR 0080): at a 20-vector window the lexical
    candidates are still vector-scored by ``score_entries``, so every query the
    text carries keeps its recall.  Queries the text does not carry depend on
    the window (the stated limit): reported, not asserted."""
    report = [f"D7 report (candidate_limit={D7_CANDIDATE_LIMIT}, text tok<c>)"]
    for key in PERSONAS:
        carried = _carried_queries(corpus, key)
        assert carried, key
        hybrid_carried, leaks, _outside = _measure_hybrid(
            corpus,
            key,
            text_of=_cluster_text,
            candidate_limit=D7_CANDIDATE_LIMIT,
            queries=carried,
        )
        hybrid_all, all_leaks, _outside = _measure_hybrid(
            corpus, key, text_of=_cluster_text, candidate_limit=D7_CANDIDATE_LIMIT
        )
        vector_carried = _measure_vector_over(
            corpus, key, candidate_limit=D7_CANDIDATE_LIMIT, queries=carried
        )
        report.append(
            f"{key:12s} carried={len(carried)}/{len(QUERY_VECTORS)} "
            f"hybrid recall@10 (carried)={hybrid_carried} "
            f"vector-only recall@10 (carried)={vector_carried} "
            f"hybrid recall@10 (all 24)={hybrid_all} leaks={leaks + all_leaks}"
        )
        assert leaks == 0 and all_leaks == 0, key
        assert hybrid_carried >= Decimal("0.90"), key
    print("\n".join(report))


def _measure_vector_over(
    corpus, persona_key: str, *, candidate_limit: int, queries: list[int]
) -> Decimal:
    """Vector-only mean recall@10 over the given queries (for the D7 report)."""
    persona = corpus.persona(persona_key)
    allowed_size = _authorized_size(corpus, persona_key)
    context = persona_context(corpus, persona_key)
    recalls: list[Decimal] = []
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=_workspace_of(corpus, persona_key))
        for index in queries:
            outcome = search_evidence_vector(
                db,
                context=context,
                store=TableVectorStore(),
                embedding_version=corpus.embedding_version,
                query_vector=QUERY_VECTORS[index],
                now=NOW,
                k=10,
                candidate_limit=candidate_limit,
            )
            returned = [hit.entry_id for hit in outcome.hits]
            truth = ranked_truth(corpus, persona, QUERY_VECTORS[index])
            recalls.append(recall_fraction(returned, truth, allowed_size))
    return sum(recalls, Decimal(0)) / Decimal(len(recalls))
