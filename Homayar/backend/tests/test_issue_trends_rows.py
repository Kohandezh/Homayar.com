"""Trend rows and provenance for HOM-80 and HOM-81 (HOM-79, D7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from functools import cache

import pytest

from backend.analysis_taxonomy import POLICY_V1, evaluate_extraction, load_taxonomy, policy_digest
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    TRENDS_VERSION,
    CellKind,
    CellReason,
    CellStatus,
    ConversationFacts,
    Exclusion,
    MissingBucket,
    RowLevel,
    TrendDefinition,
    TrendFilters,
    build_rows,
    judge_conversation,
    load_definition,
    sample_policy_digest,
    tally,
    trend_provenance,
    validate_filters,
)


@cache
def _definition() -> TrendDefinition:
    return load_definition("trends", 1)


TAXONOMY = load_taxonomy("conversation", 1)
NOW = datetime(2026, 10, 23, tzinfo=UTC)


def _all() -> TrendFilters:
    return validate_filters({}, _definition(), now=NOW)


def _row(intent: str | None = None, topic: str | None = None, outcome: str | None = None) -> dict:
    def field(label: str | None) -> dict:
        if label is None:
            return {"status": "not_stated", "values": []}
        return {
            "status": "stated",
            "values": [
                {"label": label, "basis": "explicit", "source_turn_indexes": [0], "confidence": 0.9}
            ],
        }

    extraction = {
        "intent": field(intent),
        "topic": field(topic),
        "entity": {"status": "not_stated", "values": []},
        "outcome": field(outcome),
    }
    return evaluate_extraction(extraction, TAXONOMY, ["a", "b"]).to_json()


def _judge(index: int, extraction: dict | None, *, counted: bool = True):
    return judge_conversation(
        ConversationFacts(
            meeting_id=f"m{index}",
            ingestion_id=f"i{index}",
            media_group_id=f"g{index}",
            is_counted_copy=counted,
            current_revision_id="r",
            transcript_is_mock=False,
            analysis_run_id="run",
            analysis_state="completed",
            analysis_revision_id="r",
            analysis_is_mock=False,
            extraction_run_id="run" if extraction is not None else None,
            extraction=extraction,
        ),
        _definition(),
    )


def _tally(
    complaints: int, others: int, pending_cancel: int = 0, missing: int = 0, excluded: int = 0
):
    judgments = []
    index = 0
    for _ in range(complaints):
        judgments.append(_judge(index, _row("report_dissatisfaction", "refund")))
        index += 1
    for _ in range(others):
        judgments.append(_judge(index, _row("request_support", outcome="resolved")))
        index += 1
    for _ in range(pending_cancel):
        judgments.append(_judge(index, _row("cancel_service")))
        index += 1
    for _ in range(missing):
        judgments.append(_judge(index, None))
        index += 1
    for _ in range(excluded):
        judgments.append(_judge(index, _row("request_support"), counted=False))
        index += 1
    return tally(judgments, _definition())


def _by_key(rows) -> dict:
    return {(row.level, row.issue_id or row.family_id): row for row in rows}


def test_rows_order_and_levels() -> None:
    rows = build_rows(_tally(2, 2), _definition(), _all())
    keys = [(row.level, row.family_id, row.issue_id) for row in rows]
    families = [k for k in keys if k[0] is RowLevel.FAMILY]
    issues = [k for k in keys if k[0] is RowLevel.ISSUE]
    assert keys == families + issues
    assert [k[1] for k in families] == [
        "call_reason",
        "unresolved_outcome",
        "service_complaint",
        "qa",
    ]
    assert all(k[2] is None for k in families)
    assert [k[2] for k in issues] == [issue.id for issue in _definition().active_issues()]
    assert all(_definition().issue(k[2]).family == k[1] for k in issues)  # type: ignore[union-attr]


def test_a_computed_row() -> None:
    result = _tally(complaints=12, others=24, pending_cancel=4, missing=3, excluded=2)
    rows = _by_key(build_rows(result, _definition(), _all()))
    complaint = rows[(RowLevel.ISSUE, "complaint_refund")]
    assert complaint.cell.status is CellStatus.COMPUTED
    assert (complaint.cell.numerator, complaint.cell.denominator) == (12, 40)
    assert complaint.cell.ratio == 0.3
    assert complaint.pending_review == 0
    # the pending cancel_service calls state no topic: a firm absence for a complaint
    assert complaint.absent_pending_review == 0
    assert rows[(RowLevel.ISSUE, "intent_purchase_inquiry")].absent_pending_review == 4
    assert complaint.missing[MissingBucket.EXTRACTION_UNAVAILABLE] == 3
    assert set(complaint.missing) == set(MissingBucket)
    assert complaint.cell.denominator + sum(complaint.missing.values()) == result.included
    cancel = rows[(RowLevel.ISSUE, "intent_cancel_service")]
    assert (cancel.cell.numerator, cancel.pending_review) == (0, 4)
    unresolved = rows[(RowLevel.FAMILY, "unresolved_outcome")]
    assert unresolved.absent_pending_review == 24  # every pending resolved outcome
    assert result.excluded == {Exclusion.DUPLICATE_MEDIA: 2}


def test_every_available_row_reconciles_with_the_population() -> None:
    result = _tally(5, 31, 2, 4, 1)
    for row in build_rows(result, _definition(), _all()):
        if row.cell.reason is CellReason.SCORECARD_RESULTS_UNAVAILABLE:
            continue
        assert row.cell.denominator + sum(row.missing.values()) == result.included


def test_the_qa_row_is_source_unavailable() -> None:
    result = _tally(3, 3)
    qa = _by_key(build_rows(result, _definition(), _all()))[(RowLevel.FAMILY, "qa")]
    assert qa.cell.status is CellStatus.NOT_COMPUTABLE
    assert qa.cell.reason is CellReason.SCORECARD_RESULTS_UNAVAILABLE
    assert qa.cell.numerator is None
    assert qa.cell.denominator == result.included
    assert qa.cell.minimum_sample == 30
    assert qa.pending_review is None and qa.absent_pending_review is None
    assert qa.missing == {}
    empty_qa = _by_key(build_rows(_tally(0, 0), _definition(), _all()))[(RowLevel.FAMILY, "qa")]
    assert empty_qa.cell.reason is CellReason.SCORECARD_RESULTS_UNAVAILABLE


def test_small_rate_rows_stay_visible() -> None:
    rows = _by_key(build_rows(_tally(2, 3, 1), _definition(), _all()))
    complaint = rows[(RowLevel.ISSUE, "complaint_refund")]
    assert complaint.cell.status is CellStatus.INSUFFICIENT_SAMPLE
    assert complaint.cell.numerator == 2
    assert complaint.pending_review == 0
    assert complaint.absent_pending_review == 0
    inquiry = rows[(RowLevel.ISSUE, "intent_purchase_inquiry")]
    assert inquiry.cell.status is CellStatus.INSUFFICIENT_SAMPLE
    assert inquiry.absent_pending_review == 1


def test_small_agent_rows_hide_their_counts() -> None:
    rows = _by_key(
        build_rows(_tally(2, 3, 1, missing=1), _definition(), _all(), kind=CellKind.AGENT)
    )
    complaint = rows[(RowLevel.ISSUE, "complaint_refund")]
    assert complaint.cell.status is CellStatus.INSUFFICIENT_SAMPLE
    assert complaint.cell.numerator is None
    assert complaint.cell.denominator == 6
    assert complaint.pending_review is None
    assert complaint.absent_pending_review is None
    assert complaint.missing[MissingBucket.EXTRACTION_UNAVAILABLE] == 1  # stays visible
    big = _by_key(build_rows(_tally(10, 25), _definition(), _all(), kind=CellKind.AGENT))
    assert big[(RowLevel.ISSUE, "complaint_refund")].cell.numerator == 10
    assert big[(RowLevel.ISSUE, "complaint_refund")].pending_review == 0


def test_agent_policy_without_hiding() -> None:
    policy = replace(SAMPLE_POLICY_V1, hide_agent_counts_below_minimum=False)
    rows = _by_key(
        build_rows(_tally(2, 3), _definition(), _all(), kind=CellKind.AGENT, policy=policy)
    )
    assert rows[(RowLevel.ISSUE, "complaint_refund")].cell.numerator == 2
    assert rows[(RowLevel.ISSUE, "complaint_refund")].pending_review == 0


def test_empty_rows() -> None:
    rows = _by_key(build_rows(_tally(0, 0), _definition(), _all()))
    complaint = rows[(RowLevel.ISSUE, "complaint_refund")]
    assert complaint.cell.status is CellStatus.NOT_COMPUTABLE
    assert complaint.cell.reason is CellReason.EMPTY_DENOMINATOR
    assert complaint.cell.numerator == 0
    assert complaint.pending_review == 0


def test_selectors_choose_rows_and_never_change_numbers() -> None:
    result = _tally(12, 24)
    everything = _by_key(build_rows(result, _definition(), _all()))
    only = build_rows(
        result, _definition(), validate_filters({"service_area": "refund"}, _definition(), now=NOW)
    )
    assert [(row.level, row.issue_id) for row in only] == [(RowLevel.ISSUE, "complaint_refund")]
    assert only[0] == everything[(RowLevel.ISSUE, "complaint_refund")]
    families = build_rows(
        result, _definition(), validate_filters({"family": "qa"}, _definition(), now=NOW)
    )
    assert [(row.level, row.family_id) for row in families] == [(RowLevel.FAMILY, "qa")]


def test_row_to_json() -> None:
    result = _tally(12, 24, 4, 3)
    rows = _by_key(build_rows(result, _definition(), _all()))
    data = rows[(RowLevel.ISSUE, "complaint_refund")].to_json()
    assert list(data) == [
        "level",
        "family",
        "issue",
        "cell",
        "pending_review",
        "absent_pending_review",
        "missing",
    ]
    assert data["level"] == "issue" and data["family"] == "service_complaint"
    assert data["issue"] == "complaint_refund"
    assert data["cell"] == rows[(RowLevel.ISSUE, "complaint_refund")].cell.to_json()
    assert list(data["missing"]) == [bucket.value for bucket in MissingBucket]
    assert data["missing"]["extraction_unavailable"] == 3
    qa = rows[(RowLevel.FAMILY, "qa")].to_json()
    assert qa["issue"] is None and qa["missing"] == {} and qa["pending_review"] is None
    assert qa["cell"]["reason"] == "scorecard_results_unavailable"


def test_rows_refuse_a_tally_of_another_definition() -> None:
    result = replace(_tally(1, 1), definition_digest="0" * 64)
    with pytest.raises(ValueError):
        build_rows(result, _definition(), _all())
    with pytest.raises(ValueError):
        build_rows(replace(_tally(1, 1), definition_version=2), _definition(), _all())


def test_provenance() -> None:
    assert trend_provenance(_definition()) == {
        "trends_version": TRENDS_VERSION,
        "definition": {"id": "trends", "version": 1, "digest": _definition().digest},
        "taxonomy": {"id": "conversation", "version": 1, "digest": TAXONOMY.digest},
        "extraction_policies": [
            {"version": "extraction-policy/1", "digest": policy_digest(POLICY_V1)}
        ],
        "validator_versions": ["analysis-taxonomy-validator/1"],
        "sample_policy": {
            "version": "trend-sample-policy/1",
            "digest": sample_policy_digest(SAMPLE_POLICY_V1),
        },
    }
    other = replace(SAMPLE_POLICY_V1, version="trend-sample-policy/x", min_denominator_rate=40)
    assert trend_provenance(_definition(), other)["sample_policy"] == {
        "version": "trend-sample-policy/x",
        "digest": sample_policy_digest(other),
    }
