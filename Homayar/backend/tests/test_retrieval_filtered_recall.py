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
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.database.relational import get_session_factory
from backend.database.tenant_context import bind_tenant_context
from backend.retrieval.evidence import search_evidence_lexical, search_evidence_vector
from backend.retrieval.vector_store import TableVectorStore
from backend.tests._retrieval_corpus import (
    LEXICAL_QUERIES,
    NOW,
    QUERY_VECTORS,
    authorized,
    build_corpus,
    lexical_truth,
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
