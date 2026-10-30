"""Factual support from audit labels, and retrieval ranking scores (HOM-69)."""

from __future__ import annotations

import json
import math

import pytest

from backend.evaluation import (
    AuditedClaim,
    Citation,
    RetrievalQuery,
    SupportLabel,
    citation_is_valid,
    evaluate_factual_support,
    evaluate_retrieval,
)

OK = Citation(0, 1000, "rev1")


@pytest.mark.parametrize(
    ("citation", "duration", "valid"),
    [
        (Citation(0, 1000, "rev1"), 1000, True),
        (Citation(0, 1000, "rev1"), None, True),
        (Citation(0, 10**9, "rev1"), None, True),
        (Citation(0, 1001, "rev1"), 1000, False),
        (Citation(500, 500, "rev1"), None, False),
        (Citation(-1, 500, "rev1"), None, False),
        (Citation(0, 500, "  "), None, False),
        (Citation(0, 500), None, False),
    ],
)
def test_citation_is_valid(citation: Citation, duration: int | None, valid: bool) -> None:
    assert citation_is_valid(citation, duration) is valid


CLAIMS = [
    AuditedClaim("c1", SupportLabel.SUPPORTED, [OK], recording_duration_ms=1000),
    AuditedClaim("c2", SupportLabel.UNSUPPORTED, [], severe=True),
    AuditedClaim("c3", SupportLabel.CONTRADICTED, [OK], severe=True),
    AuditedClaim("c4", SupportLabel.NOT_AUDITED, [OK], severe=True),
    AuditedClaim(
        "c5", SupportLabel.SUPPORTED, [OK, Citation(0, 2000, "rev1")], recording_duration_ms=1000
    ),  # fmt: skip
    AuditedClaim("c6", SupportLabel.UNSUPPORTED, [OK]),
]


def test_factual_support_report() -> None:
    report = evaluate_factual_support(CLAIMS)
    rate = report.metrics["support_rate"]
    assert (rate.numerator, rate.denominator, rate.n_items, rate.n_excluded) == (2, 5, 5, 1)
    assert rate.value == pytest.approx(0.4)
    validity = report.metrics["citation_validity"]
    # c2 has no citation; c5 has one bad citation.
    assert (validity.numerator, validity.denominator, validity.n_items) == (4, 6, 6)
    assert report.invented_severe == 2
    assert report.invented_severe_claim_ids == ("c2", "c3")
    assert report.unsupported_claim_ids == ("c2", "c3", "c6")
    d = report.as_dict()
    json.dumps(d)
    assert d["invented_severe_claim_ids"] == ["c2", "c3"]


def test_factual_support_only_not_audited() -> None:
    report = evaluate_factual_support([AuditedClaim("c", SupportLabel.NOT_AUDITED, [OK])])
    assert report.metrics["support_rate"].value is None
    assert report.metrics["support_rate"].reason == "no_audited_claims"
    assert report.metrics["citation_validity"].value == 1.0


def test_factual_support_empty() -> None:
    report = evaluate_factual_support([])
    assert report.metrics["support_rate"].reason == "no_audited_claims"
    assert report.metrics["citation_validity"].value is None
    assert report.metrics["citation_validity"].reason == "no_claims"
    assert report.invented_severe == 0


# Retrieval

Q1 = RetrievalQuery(
    "q1",
    {"d1": 3, "d2": 2, "d3": 0, "d4": 1},
    ["d3", "d1", "d3", "d5", "d2"],  # deduplicated: d3, d1, d5, d2
)
Q2 = RetrievalQuery("q2", {"a": 1}, ["a"])
Q3 = RetrievalQuery("q3", {"x": 0}, ["x"])  # nothing relevant: excluded
Q4 = RetrievalQuery("q4", {"b": 1}, [])  # abstained: zeros


def test_retrieval_single_query_graded() -> None:
    m = evaluate_retrieval([Q1], ks=(1, 3, 5)).metrics
    assert m["recall@1"].value == 0.0
    assert m["hit@1"].value == 0.0
    assert m["ndcg@1"].value == 0.0
    assert m["recall@3"].value == pytest.approx(1 / 3)
    assert m["precision@3"].value == pytest.approx(1 / 3)
    assert m["hit@3"].value == 1.0
    dcg3 = 7 / math.log2(3)
    idcg3 = 7 + 3 / math.log2(3) + 1 / 2
    assert m["ndcg@3"].value == pytest.approx(dcg3 / idcg3)
    assert m["recall@5"].value == pytest.approx(2 / 3)
    assert m["precision@5"].value == pytest.approx(2 / 5)
    dcg5 = 7 / math.log2(3) + 3 / math.log2(5)
    idcg5 = 7 + 3 / math.log2(3) + 1 / 2
    assert m["ndcg@5"].value == pytest.approx(dcg5 / idcg5)
    assert m["mrr"].value == pytest.approx(0.5)


def test_retrieval_corpus_means() -> None:
    report = evaluate_retrieval([Q1, Q2, Q3, Q4], ks=(1, 3, 5))
    m = report.metrics
    assert set(m) == {"mrr"} | {f"{n}@{k}" for n in ("recall", "precision", "hit", "ndcg")
                                for k in (1, 3, 5)}  # fmt: skip
    mrr = m["mrr"]
    assert (mrr.numerator, mrr.denominator) == (1.5, 3)
    assert mrr.value == pytest.approx(0.5)
    assert (mrr.n_items, mrr.n_excluded, mrr.n_abstained) == (3, 1, 1)
    assert m["recall@1"].value == pytest.approx(1 / 3)
    assert m["precision@5"].value == pytest.approx((2 / 5 + 1 / 5) / 3)
    assert m["precision@3"].value == pytest.approx((1 / 3 + 1 / 3) / 3)
    assert m["hit@5"].value == pytest.approx(2 / 3)
    json.dumps(report.as_dict())


def test_retrieval_default_ks() -> None:
    m = evaluate_retrieval([Q2]).metrics
    assert {"recall@1", "recall@5", "recall@10", "ndcg@10"} <= set(m)
    assert m["precision@10"].value == pytest.approx(0.1)
    assert m["ndcg@10"].value == pytest.approx(1.0)


def test_retrieval_duplicates_are_removed_before_the_cut() -> None:
    q = RetrievalQuery("q", {"a": 1, "b": 1}, ["a", "a", "b"])
    m = evaluate_retrieval([q], ks=(2,)).metrics
    assert m["recall@2"].value == 1.0
    assert m["precision@2"].value == 1.0


def test_retrieval_no_scored_query() -> None:
    for queries in ([], [Q3]):
        report = evaluate_retrieval(queries, ks=(1,))
        for m in report.metrics.values():
            assert m.value is None
            assert m.reason == "no_relevant_documents"


@pytest.mark.parametrize("ks", [(0,), (-1,), (1.5,), (True,)])
def test_retrieval_bad_k_is_refused(ks: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        evaluate_retrieval([Q2], ks=ks)  # type: ignore[arg-type]


def test_retrieval_negative_relevance_is_refused() -> None:
    with pytest.raises(ValueError):
        evaluate_retrieval([RetrievalQuery("q", {"a": -1}, ["a"])], ks=(1,))


def test_retrieval_k_one_and_relevance_zero_are_allowed() -> None:
    q = RetrievalQuery("q", {"a": 0, "b": 1}, ["a", "b"])
    m = evaluate_retrieval([q], ks=(1,)).metrics
    assert m["hit@1"].value == 0.0
    assert m["mrr"].value == pytest.approx(0.5)


def test_retrieval_duplicate_ks_are_removed() -> None:
    # Turn 2, finding 1: ks=(5, 5) once gave recall@5 == 2.0.
    q = RetrievalQuery("q", {"a": 1}, ["a"])
    twice = evaluate_retrieval([q], ks=(5, 5))
    assert twice.as_dict() == evaluate_retrieval([q], ks=(5,)).as_dict()
    assert twice.metrics["recall@5"].value == 1.0
    assert twice.metrics["hit@5"].value == 1.0
    assert list(evaluate_retrieval([q], ks=(5, 1, 5)).metrics) == [
        "mrr", "recall@5", "precision@5", "hit@5", "ndcg@5",
        "recall@1", "precision@1", "hit@1", "ndcg@1",
    ]  # fmt: skip


# Turn 4 (SPEC 14): gains too large for a float.


def test_retrieval_huge_relevance_gives_non_finite_ndcg_only() -> None:
    q = RetrievalQuery("q", {"a": 1100}, ["a"])
    m = evaluate_retrieval([q], ks=(1,)).metrics
    ndcg = m["ndcg@1"]
    assert (ndcg.value, ndcg.numerator, ndcg.denominator) == (None, None, None)
    assert ndcg.reason == "non_finite_result"
    assert m["recall@1"].value == 1.0
    assert m["precision@1"].value == 1.0
    assert m["hit@1"].value == 1.0
    assert m["mrr"].value == 1.0
    json.dumps(evaluate_retrieval([q], ks=(1,)).as_dict(), allow_nan=False)


@pytest.mark.parametrize(
    "query",
    [
        RetrievalQuery("q", {"a": 1100, "b": 1}, ["b"]),  # only the ideal DCG overflows
        RetrievalQuery("q", {"a": 1023, "b": 1023, "c": 1023}, ["a", "b", "c"]),  # sum
        RetrievalQuery("q", {"a": 10**30}, ["a"]),  # must not build 2 ** 10**30
    ],
)
def test_retrieval_ndcg_overflow_cases(query: RetrievalQuery) -> None:
    m = evaluate_retrieval([query], ks=(3,)).metrics
    assert m["ndcg@3"].value is None
    assert m["ndcg@3"].reason == "non_finite_result"
    assert m["mrr"].value is not None


def test_retrieval_large_but_finite_relevance() -> None:
    m = evaluate_retrieval([RetrievalQuery("q", {"a": 1000}, ["a"])], ks=(1,)).metrics
    assert m["ndcg@1"].value == 1.0
