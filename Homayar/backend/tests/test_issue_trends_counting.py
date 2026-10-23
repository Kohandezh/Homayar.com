"""Counting and deduplication of trend issues per conversation (HOM-79, D2)."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from backend.analysis_taxonomy import evaluate_extraction, load_taxonomy
from backend.database.saas_models import StageState
from backend.issue_trends import (
    CONVERSATION_BUCKETS,
    DIMENSION_BUCKETS,
    NO_ANALYSIS_STATES,
    STAGE_STATES,
    TRENDS_VERSION,
    ConversationFacts,
    Exclusion,
    IssueState,
    MediaGroupMember,
    MissingBucket,
    TrendDefinition,
    UnreadableReason,
    counted_copy_id,
    judge_conversation,
    load_definition,
    load_definition_file,
    tally,
)


@cache
def _definition() -> TrendDefinition:
    return load_definition("trends", 1)


RELEASED = (
    Path(__file__).resolve().parent.parent / "issue_trends" / "definitions" / "trends.v1.json"
)
TAXONOMY = load_taxonomy("conversation", 1)
TURNS = ["turn zero", "turn one", "turn two", "turn three"]
AVAILABLE_FAMILIES = ["call_reason", "unresolved_outcome", "service_complaint"]


# --- builders -------------------------------------------------------------------------------


def _field(spec: Any) -> dict[str, Any]:
    """``"not_stated"``, an abstain status, or a list of ``(label, basis, confidence)``."""
    if isinstance(spec, str):
        return {"status": spec, "values": []}
    return {
        "status": "stated",
        "values": [
            {"label": label, "basis": basis, "source_turn_indexes": [0], "confidence": conf}
            for label, basis, conf in spec
        ],
    }


def stored(
    intent: Any = "not_stated", topic: Any = "not_stated", outcome: Any = "not_stated"
) -> dict[str, Any]:
    """A real stored ``PolicyResult.to_json()`` from the pinned taxonomy and policy."""
    extraction = {
        "intent": _field(intent),
        "topic": _field(topic),
        "entity": {"status": "not_stated", "values": []},
        "outcome": _field(outcome),
    }
    return evaluate_extraction(extraction, TAXONOMY, TURNS).to_json()


def ok(label: str) -> list[tuple[str, str, float]]:
    return [(label, "explicit", 0.9)]


_DEFAULT = object()


def facts(extraction: object = _DEFAULT, **changes: Any) -> ConversationFacts:
    values: dict[str, Any] = {
        "meeting_id": "m1",
        "ingestion_id": "i1",
        "media_group_id": "i1",
        "is_counted_copy": True,
        "current_revision_id": "rev1",
        "transcript_is_mock": False,
        "analysis_run_id": "run1",
        "analysis_state": "completed",
        "analysis_revision_id": "rev1",
        "analysis_is_mock": False,
        "extraction_run_id": "run1",
        "extraction": stored() if extraction is _DEFAULT else extraction,
    }
    values.update(changes)
    return ConversationFacts(**values)


def judge(extraction: object = _DEFAULT, **changes: Any):
    return judge_conversation(facts(extraction, **changes), _definition())


def states(judgment) -> dict[str, tuple[IssueState, MissingBucket | None]]:
    return {i.issue_id: (i.state, i.bucket) for i in judgment.issues}


def family_states(judgment) -> dict[str, tuple[IssueState, MissingBucket | None]]:
    return {f.family_id: (f.state, f.bucket) for f in judgment.families}


def bucket_of(judgment) -> MissingBucket | None:
    return judgment.bucket


AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# --- vocabulary -----------------------------------------------------------------------------


def test_stage_states_match_the_database_enum() -> None:
    assert STAGE_STATES == tuple(state.value for state in StageState)
    assert NO_ANALYSIS_STATES == {"skipped", "unavailable", "failed"}


def test_bucket_order() -> None:
    assert [b.value for b in MissingBucket] == [
        "analysis_unavailable",
        "analysis_stale",
        "mock_analysis",
        "extraction_unavailable",
        "extraction_unreadable",
        "version_mismatch",
        "extraction_invalid",
        "abstained_insufficient_evidence",
        "abstained_contradictory",
        "abstained_contradictory_values",
        "abstained_low_confidence",
        "value_withheld",
    ]
    assert CONVERSATION_BUCKETS == tuple(list(MissingBucket)[:7])
    assert DIMENSION_BUCKETS == tuple(list(MissingBucket)[7:11])
    assert [e.value for e in Exclusion] == ["duplicate_media"]


# --- media groups ---------------------------------------------------------------------------


def _member(ingestion_id: str, minutes: int, transcribed: bool) -> MediaGroupMember:
    return MediaGroupMember(ingestion_id, AT + timedelta(minutes=minutes), transcribed)


def test_counted_copy_is_the_earliest_transcribed_member() -> None:
    members = [_member("c", 2, True), _member("a", 0, False), _member("b", 1, True)]
    assert counted_copy_id(members) == "b"
    assert counted_copy_id(reversed(members)) == "b"


def test_earliest_never_transcribed_two_later_copies_only_the_earlier_counts() -> None:
    assert (
        counted_copy_id([_member("a", 0, False), _member("b", 5, True), _member("c", 9, True)])
        == "b"
    )


def test_counted_copy_tie_break_is_the_ingestion_id() -> None:
    assert counted_copy_id([_member("z", 0, True), _member("y", 0, True)]) == "y"


def test_counted_copy_compares_instants_not_offsets() -> None:
    tehran = datetime(2026, 10, 1, 15, 0, tzinfo=UTC).astimezone()  # same instant, any zone
    later_wall_clock = MediaGroupMember("a", tehran, True)
    earlier = MediaGroupMember("b", datetime(2026, 10, 1, 14, 59, tzinfo=UTC), True)
    assert counted_copy_id([later_wall_clock, earlier]) == "b"


def test_group_without_transcribed_member_counts_nothing() -> None:
    assert counted_copy_id([_member("a", 0, False), _member("b", 1, False)]) is None


def test_counted_copy_refusals() -> None:
    with pytest.raises(ValueError):
        counted_copy_id([])
    with pytest.raises(ValueError):
        counted_copy_id([_member("a", 0, True), _member("a", 1, False)])
    with pytest.raises(ValueError):
        counted_copy_id([MediaGroupMember("a", datetime(2026, 10, 1), True)])


# --- the steps per conversation -------------------------------------------------------------


def test_a_member_that_is_not_the_counted_copy_is_excluded() -> None:
    judgment = judge(stored(intent=ok("request_support")), is_counted_copy=False)
    assert judgment.exclusion is Exclusion.DUPLICATE_MEDIA
    assert judgment.bucket is None
    assert judgment.unreadable is None
    assert judgment.dimensions == ()
    assert judgment.issues == ()
    assert judgment.families == ()


def test_exclusion_comes_before_every_bucket() -> None:
    judgment = judge(
        None,
        is_counted_copy=False,
        analysis_run_id=None,
        analysis_state=None,
        analysis_revision_id=None,
        extraction_run_id=None,
    )
    assert judgment.exclusion is Exclusion.DUPLICATE_MEDIA


def _whole(judgment, bucket: MissingBucket) -> None:
    assert judgment.exclusion is None
    assert judgment.bucket is bucket
    assert [(d.dimension, d.bucket) for d in judgment.dimensions] == [
        ("intent", bucket),
        ("topic", bucket),
        ("outcome", bucket),
    ]
    assert [i.issue_id for i in judgment.issues] == [
        issue.id for issue in _definition().active_issues()
    ]
    assert all(i.state is IssueState.MISSING and i.bucket is bucket for i in judgment.issues)
    assert not any(i.absent_pending_review for i in judgment.issues)
    assert [f.family_id for f in judgment.families] == AVAILABLE_FAMILIES
    assert all(f.state is IssueState.MISSING and f.bucket is bucket for f in judgment.families)


def test_no_current_run_is_analysis_unavailable() -> None:
    judgment = judge_conversation(
        ConversationFacts(
            meeting_id="m1",
            ingestion_id="i1",
            media_group_id="i1",
            is_counted_copy=True,
            current_revision_id="rev1",
            transcript_is_mock=False,
            analysis_run_id=None,
            analysis_state=None,
            analysis_revision_id=None,
            analysis_is_mock=False,
            extraction_run_id=None,
            extraction=None,
        ),
        _definition(),
    )
    _whole(judgment, MissingBucket.ANALYSIS_UNAVAILABLE)


@pytest.mark.parametrize("state", ["skipped", "unavailable", "failed"])
def test_a_run_that_analysed_nothing_is_analysis_unavailable(state: str) -> None:
    _whole(
        judge(stored(intent=ok("request_support")), analysis_state=state),
        MissingBucket.ANALYSIS_UNAVAILABLE,
    )


@pytest.mark.parametrize(
    "state", ["queued", "running", "completed", "partial", "insufficient_evidence"]
)
def test_other_run_states_are_read(state: str) -> None:
    judgment = judge(stored(intent=ok("request_support")), analysis_state=state)
    assert judgment.bucket is None
    assert states(judgment)["intent_request_support"] == (IssueState.COUNTED, None)


@pytest.mark.parametrize(
    "state", ["queued", "running", "completed", "partial", "insufficient_evidence"]
)
def test_other_run_states_without_a_row_are_extraction_unavailable(state: str) -> None:
    """C3: only skipped, unavailable and failed analysed nothing."""
    _whole(
        judge(None, analysis_state=state, extraction_run_id=None),
        MissingBucket.EXTRACTION_UNAVAILABLE,
    )


def test_a_stale_run_is_analysis_stale() -> None:
    _whole(judge(analysis_revision_id="rev0"), MissingBucket.ANALYSIS_STALE)


def test_stale_wins_over_mock() -> None:
    _whole(judge(analysis_revision_id="rev0", analysis_is_mock=True), MissingBucket.ANALYSIS_STALE)


def test_unavailable_wins_over_stale() -> None:
    _whole(
        judge(analysis_state="failed", analysis_revision_id="rev0"),
        MissingBucket.ANALYSIS_UNAVAILABLE,
    )


def test_a_mock_transcript_or_run_is_mock_analysis() -> None:
    _whole(judge(transcript_is_mock=True), MissingBucket.MOCK_ANALYSIS)
    _whole(judge(analysis_is_mock=True), MissingBucket.MOCK_ANALYSIS)
    _whole(judge(transcript_is_mock=True, analysis_is_mock=True), MissingBucket.MOCK_ANALYSIS)


def test_mock_wins_over_a_missing_row() -> None:
    _whole(
        judge(None, transcript_is_mock=True, extraction_run_id=None), MissingBucket.MOCK_ANALYSIS
    )


def test_no_row_is_extraction_unavailable() -> None:
    _whole(judge(None, extraction_run_id=None), MissingBucket.EXTRACTION_UNAVAILABLE)


def test_a_valid_false_row_is_extraction_invalid() -> None:
    row = stored(intent=ok("request_support"))
    row = copy.deepcopy(row)
    row["issues"] = [{"code": "unknown_key", "path": "estimated_nps"}]
    row["valid"] = False
    _whole(judge(row), MissingBucket.EXTRACTION_INVALID)


def test_a_real_rejected_field_makes_the_row_invalid() -> None:
    row = stored(intent=[("no_such_label", "explicit", 0.9)])
    assert row["valid"] is False
    assert row["fields"]["intent"]["verdict"] == "rejected"
    _whole(judge(row), MissingBucket.EXTRACTION_INVALID)


# --- version fields and version mismatch ----------------------------------------------------


def _unreadable(row: object, reason: UnreadableReason) -> None:
    judgment = judge(row)
    _whole(judgment, MissingBucket.EXTRACTION_UNREADABLE)
    assert judgment.unreadable is reason


@pytest.mark.parametrize("value", [[1, 2], "row", 3, True])
def test_a_result_that_is_not_an_object_is_unreadable(value: object) -> None:
    _unreadable(value, UnreadableReason.VERSION_FIELDS)


def test_a_stored_json_null_is_unreadable() -> None:
    judgment = judge_conversation(
        ConversationFacts(
            meeting_id="m1",
            ingestion_id="i1",
            media_group_id="i1",
            is_counted_copy=True,
            current_revision_id="rev1",
            transcript_is_mock=False,
            analysis_run_id="run1",
            analysis_state="completed",
            analysis_revision_id="rev1",
            analysis_is_mock=False,
            extraction_run_id="run1",
            extraction=None,
        ),
        _definition(),
    )
    assert judgment.bucket is MissingBucket.EXTRACTION_UNREADABLE
    assert judgment.unreadable is UnreadableReason.VERSION_FIELDS


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("taxonomy_id", None),
        ("taxonomy_version", True),
        ("taxonomy_version", "1"),
        ("taxonomy_version", 1.0),
        ("taxonomy_digest", 5),
        ("policy_version", None),
        ("policy_digest", []),
        ("validator_version", 1),
    ],
)
def test_unreadable_version_fields(key: str, value: object) -> None:
    row = copy.deepcopy(stored())
    row[key] = value
    _unreadable(row, UnreadableReason.VERSION_FIELDS)


@pytest.mark.parametrize(
    "key",
    [
        "taxonomy_id",
        "taxonomy_version",
        "taxonomy_digest",
        "policy_version",
        "policy_digest",
        "validator_version",
    ],
)
def test_a_missing_version_field_is_unreadable(key: str) -> None:
    row = copy.deepcopy(stored())
    del row[key]
    _unreadable(row, UnreadableReason.VERSION_FIELDS)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("taxonomy_id", "other"),
        ("taxonomy_version", 2),
        ("taxonomy_digest", "0" * 64),
        ("policy_version", "extraction-policy/2"),
        ("policy_digest", "0" * 64),
        ("validator_version", "analysis-taxonomy-validator/2"),
    ],
)
def test_version_mismatch(key: str, value: object) -> None:
    row = copy.deepcopy(stored(intent=ok("request_support")))
    row[key] = value
    judgment = judge(row)
    _whole(judgment, MissingBucket.VERSION_MISMATCH)
    assert judgment.unreadable is None


def test_version_mismatch_wins_over_an_unknown_shape() -> None:
    row = copy.deepcopy(stored())
    row["taxonomy_version"] = 2
    row["new_key"] = {"anything": True}
    del row["fields"]
    _whole(judge(row), MissingBucket.VERSION_MISMATCH)


def test_version_mismatch_wins_over_valid_false() -> None:
    row = copy.deepcopy(stored())
    row["policy_version"] = "extraction-policy/2"
    row["valid"] = False
    _whole(judge(row), MissingBucket.VERSION_MISMATCH)


# --- the stored shape -----------------------------------------------------------------------


def _mutated(mutate, base: dict | None = None) -> dict:
    row = copy.deepcopy(base if base is not None else stored(intent=ok("request_support")))
    mutate(row)
    return row


SHAPE_CASES: dict[str, tuple[Any, UnreadableReason]] = {
    "extra top key": (lambda r: r.update(estimated_nps=4), UnreadableReason.UNKNOWN_KEY),
    "missing valid": (lambda r: r.pop("valid"), UnreadableReason.MISSING_KEY),
    "missing fields": (lambda r: r.pop("fields"), UnreadableReason.MISSING_KEY),
    "valid not bool": (lambda r: r.update(valid=1), UnreadableReason.BAD_TYPE),
    "needs_review not bool": (lambda r: r.update(needs_review="no"), UnreadableReason.BAD_TYPE),
    "issues not list": (lambda r: r.update(issues={}), UnreadableReason.BAD_TYPE),
    "fields not object": (lambda r: r.update(fields=[]), UnreadableReason.BAD_TYPE),
    "issue not object": (lambda r: r.update(issues=["x"]), UnreadableReason.BAD_TYPE),
    "issue extra key": (
        lambda r: r.update(issues=[{"code": "a", "path": "b", "message": "c"}]),
        UnreadableReason.UNKNOWN_KEY,
    ),
    "issue missing key": (lambda r: r.update(issues=[{"code": "a"}]), UnreadableReason.MISSING_KEY),
    "issue code not string": (
        lambda r: r.update(issues=[{"code": 1, "path": "b"}]),
        UnreadableReason.BAD_TYPE,
    ),
    "fields missing outcome": (lambda r: r["fields"].pop("outcome"), UnreadableReason.MISSING_KEY),
    "fields extra": (
        lambda r: r["fields"].update(sentiment=copy.deepcopy(r["fields"]["topic"])),
        UnreadableReason.UNKNOWN_KEY,
    ),
    "field not object": (lambda r: r["fields"].update(topic=None), UnreadableReason.BAD_TYPE),
    "field missing key": (
        lambda r: r["fields"]["topic"].pop("withheld"),
        UnreadableReason.MISSING_KEY,
    ),
    "field extra key": (
        lambda r: r["fields"]["topic"].update(note="x"),
        UnreadableReason.UNKNOWN_KEY,
    ),
    "verdict not string": (
        lambda r: r["fields"]["topic"].update(verdict=None),
        UnreadableReason.BAD_TYPE,
    ),
    "verdict word": (
        lambda r: r["fields"]["topic"].update(verdict="maybe"),
        UnreadableReason.BAD_WORD,
    ),
    "status word": (
        lambda r: r["fields"]["topic"].update(status="unknown"),
        UnreadableReason.BAD_WORD,
    ),
    "abstain word": (
        lambda r: r["fields"]["topic"].update(abstain_reason="tired"),
        UnreadableReason.BAD_WORD,
    ),
    "review_required not bool": (
        lambda r: r["fields"]["topic"].update(review_required=0),
        UnreadableReason.BAD_TYPE,
    ),
    "values not list": (
        lambda r: r["fields"]["intent"].update(values={}),
        UnreadableReason.BAD_TYPE,
    ),
    "value not object": (
        lambda r: r["fields"]["intent"].update(values=[1]),
        UnreadableReason.BAD_TYPE,
    ),
    "value missing key": (
        lambda r: r["fields"]["intent"]["values"][0].pop("disposition"),
        UnreadableReason.MISSING_KEY,
    ),
    "value extra key": (
        lambda r: r["fields"]["intent"]["values"][0].update(reason="below_threshold"),
        UnreadableReason.UNKNOWN_KEY,
    ),
    "label not string": (
        lambda r: r["fields"]["intent"]["values"][0].update(label=3),
        UnreadableReason.BAD_TYPE,
    ),
    "basis word": (
        lambda r: r["fields"]["intent"]["values"][0].update(basis="guessed"),
        UnreadableReason.BAD_WORD,
    ),
    "turn index bool": (
        lambda r: r["fields"]["intent"]["values"][0].update(source_turn_indexes=[True]),
        UnreadableReason.BAD_TYPE,
    ),
    "turn indexes not list": (
        lambda r: r["fields"]["intent"]["values"][0].update(source_turn_indexes=0),
        UnreadableReason.BAD_TYPE,
    ),
    "confidence string": (
        lambda r: r["fields"]["intent"]["values"][0].update(confidence="high"),
        UnreadableReason.BAD_TYPE,
    ),
    "confidence bool": (
        lambda r: r["fields"]["intent"]["values"][0].update(confidence=True),
        UnreadableReason.BAD_TYPE,
    ),
    "critical not bool": (
        lambda r: r["fields"]["intent"]["values"][0].update(critical=None),
        UnreadableReason.BAD_TYPE,
    ),
    "text not string": (
        lambda r: r["fields"]["intent"]["values"][0].update(text=5),
        UnreadableReason.BAD_TYPE,
    ),
    "disposition word": (
        lambda r: r["fields"]["intent"]["values"][0].update(disposition="maybe"),
        UnreadableReason.BAD_WORD,
    ),
    "review reason word": (
        lambda r: r["fields"]["intent"]["values"][0].update(review_reason="other"),
        UnreadableReason.BAD_WORD,
    ),
}


@pytest.mark.parametrize("name", sorted(SHAPE_CASES))
def test_shape_defects(name: str) -> None:
    mutate, reason = SHAPE_CASES[name]
    _unreadable(_mutated(mutate), reason)


def _withheld_row() -> dict:
    row = stored(topic=[("refund", "explicit", 0.9), ("invoice", "explicit", 0.1)])
    assert row["fields"]["topic"]["withheld"][0]["reason"] == "below_threshold"
    return row


def test_withheld_shape_defects() -> None:
    base = _withheld_row()
    _unreadable(
        _mutated(lambda r: r["fields"]["topic"]["withheld"][0].update(reason="other"), base),
        UnreadableReason.BAD_WORD,
    )
    _unreadable(
        _mutated(
            lambda r: r["fields"]["topic"]["withheld"][0].update(disposition="auto_accepted"), base
        ),
        UnreadableReason.UNKNOWN_KEY,
    )
    _unreadable(
        _mutated(lambda r: r["fields"]["topic"]["withheld"][0].pop("reason"), base),
        UnreadableReason.MISSING_KEY,
    )
    _unreadable(
        _mutated(lambda r: r["fields"]["topic"].update(withheld=["x"]), base),
        UnreadableReason.BAD_TYPE,
    )


def test_entity_is_checked_for_types_and_words() -> None:
    _unreadable(
        _mutated(lambda r: r["fields"]["entity"].update(verdict="maybe")), UnreadableReason.BAD_WORD
    )
    _unreadable(
        _mutated(lambda r: r["fields"]["entity"].update(values="phone")), UnreadableReason.BAD_TYPE
    )


def test_labels_are_not_checked_against_the_taxonomy() -> None:
    row = _mutated(lambda r: r["fields"]["intent"]["values"][0].update(label="future_label"))
    judgment = judge(row)
    assert judgment.bucket is None
    assert states(judgment)["intent_request_support"] == (IssueState.ABSENT, None)


def test_missing_key_wins_over_unknown_key_in_one_object() -> None:
    def mutate(row: dict) -> None:
        row["extra"] = 1
        del row["needs_review"]

    _unreadable(_mutated(mutate), UnreadableReason.MISSING_KEY)


def test_unknown_key_wins_over_a_bad_type_in_one_object() -> None:
    def mutate(row: dict) -> None:
        row["extra"] = 1
        row["valid"] = "yes"

    _unreadable(_mutated(mutate), UnreadableReason.UNKNOWN_KEY)


def test_bad_type_wins_over_a_bad_word_in_one_object() -> None:
    def mutate(row: dict) -> None:
        row["fields"]["topic"]["verdict"] = "maybe"
        row["fields"]["topic"]["review_required"] = "no"

    _unreadable(_mutated(mutate), UnreadableReason.BAD_TYPE)


def test_walk_top_level_issues_before_fields() -> None:
    # A3: a top-level issue item comes before the fields object in the walk.
    def mutate(row: dict) -> None:
        row["issues"] = [{"code": "a"}]  # missing key, in the issues items
        row["fields"]["intent"]["verdict"] = 7  # bad type, later in the walk

    _unreadable(_mutated(mutate), UnreadableReason.MISSING_KEY)

    def mutate_other(row: dict) -> None:
        row["issues"] = [{"code": 1, "path": "x"}]  # bad type in the issues items
        row["fields"].pop("entity")  # missing key in fields, later

    _unreadable(_mutated(mutate_other), UnreadableReason.BAD_TYPE)


def test_walk_follows_the_field_order() -> None:
    def mutate(row: dict) -> None:
        row["fields"]["intent"]["status"] = "unknown"  # bad word in intent (first)
        row["fields"]["topic"].pop("issues")  # missing key in topic (later)

    _unreadable(_mutated(mutate), UnreadableReason.BAD_WORD)

    def mutate_entity_first(row: dict) -> None:
        row["fields"]["entity"]["extra"] = 1  # unknown key in entity
        row["fields"]["outcome"].pop("verdict")  # missing key in outcome (after entity)

    _unreadable(_mutated(mutate_entity_first), UnreadableReason.UNKNOWN_KEY)


def test_walk_field_object_before_its_items() -> None:
    def mutate(row: dict) -> None:
        row["fields"]["intent"]["values"][0]["extra"] = 1  # unknown key in a value
        row["fields"]["intent"]["verdict"] = "maybe"  # bad word in the field itself

    _unreadable(_mutated(mutate), UnreadableReason.BAD_WORD)


def test_consistency_runs_only_after_the_whole_walk() -> None:
    def mutate(row: dict) -> None:
        row["needs_review"] = True  # inconsistent
        row["fields"]["outcome"]["values"] = [5]  # bad type, later in the walk

    _unreadable(_mutated(mutate), UnreadableReason.BAD_TYPE)


def _review_value(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "basis": "explicit",
        "source_turn_indexes": [0],
        "confidence": 0.9,
        "critical": True,
        "text": None,
        "disposition": "review_required",
        "review_reason": "critical_label",
    }


INCONSISTENT_CASES: dict[str, Any] = {
    "rejected with status": lambda r: r["fields"]["topic"].update(verdict="rejected"),
    "accepted with status null (A1)": lambda r: r["fields"]["topic"].update(status=None),
    "accepted with abstain status": lambda r: r["fields"]["topic"].update(
        status="insufficient_evidence"
    ),
    "accepted with abstain reason": lambda r: r["fields"]["topic"].update(abstain_reason="model"),
    "not_stated with a value": lambda r: r["fields"]["topic"].update(
        values=copy.deepcopy(r["fields"]["intent"]["values"])
    ),
    "stated with no kept value": lambda r: r["fields"]["intent"].update(values=[]),
    "abstained with stated": lambda r: r["fields"]["topic"].update(
        verdict="abstained", abstain_reason="model"
    ),
    "abstained without reason": lambda r: r["fields"]["topic"].update(
        verdict="abstained", status="insufficient_evidence"
    ),
    "abstained with a kept value": lambda r: r["fields"]["intent"].update(
        verdict="abstained", status="insufficient_evidence", abstain_reason="model"
    ),
    "contradictory reason, other status": lambda r: r["fields"]["topic"].update(
        verdict="abstained", status="insufficient_evidence", abstain_reason="contradictory"
    ),
    "low_confidence without withheld": lambda r: r["fields"]["topic"].update(
        verdict="abstained", status="insufficient_evidence", abstain_reason="low_confidence"
    ),
    "review_required flag false with a review value": lambda r: (
        r["fields"]["outcome"].update(
            verdict="accepted",
            status="stated",
            values=[_review_value("resolved")],
            review_required=False,
        ),
        r.update(needs_review=False),
    ),
    "review_required flag without a review value": lambda r: (
        r["fields"]["intent"].update(review_required=True),
        r.update(needs_review=True),
    ),
    "review value without reason": lambda r: (
        r["fields"]["outcome"].update(
            verdict="accepted",
            status="stated",
            values=[{**_review_value("resolved"), "review_reason": None}],
            review_required=True,
        ),
        r.update(needs_review=True),
    ),
    "auto value with a reason": lambda r: r["fields"]["intent"]["values"][0].update(
        review_reason="critical_label"
    ),
    "two intent values (A2)": lambda r: r["fields"]["intent"]["values"].append(
        {**r["fields"]["intent"]["values"][0], "label": "purchase_inquiry"}
    ),
    "needs_review mismatch": lambda r: r.update(needs_review=True),
    "valid true with a top issue": lambda r: r.update(
        issues=[{"code": "unknown_key", "path": "x"}]
    ),
    "valid false without a cause": lambda r: r.update(valid=False),
    "valid true with a rejected field": lambda r: r["fields"]["topic"].update(
        verdict="rejected", status=None
    ),
}


@pytest.mark.parametrize("name", sorted(INCONSISTENT_CASES))
def test_inconsistent_pairs(name: str) -> None:
    _unreadable(_mutated(INCONSISTENT_CASES[name]), UnreadableReason.INCONSISTENT)


def test_a2_one_kept_and_one_withheld_outcome_is_inconsistent() -> None:
    row = stored(outcome=ok("escalated"))
    row["fields"]["outcome"]["withheld"] = [
        {
            "label": "unresolved",
            "basis": "inferred",
            "source_turn_indexes": [0],
            "confidence": 0.1,
            "critical": False,
            "text": None,
            "reason": "below_threshold",
        }
    ]
    _unreadable(row, UnreadableReason.INCONSISTENT)


def test_a2_two_topics_are_fine() -> None:
    assert judge(stored(topic=ok("refund") + ok("invoice"))).bucket is None


def test_model_abstain_with_withheld_is_inconsistent() -> None:
    row = _withheld_row()
    row["fields"]["topic"].update(
        verdict="abstained", status="insufficient_evidence", abstain_reason="model", values=[]
    )
    _unreadable(row, UnreadableReason.INCONSISTENT)


def test_rejected_field_consistent_but_row_valid_false_is_invalid_not_unreadable() -> None:
    row = stored(topic=[("no_such_topic", "explicit", 0.9)])
    assert row["fields"]["topic"]["verdict"] == "rejected" and row["valid"] is False
    assert judge(row).bucket is MissingBucket.EXTRACTION_INVALID


def test_every_released_policy_shape_is_readable() -> None:
    rows = [
        stored(),
        stored(intent=ok("cancel_service"), topic=ok("refund"), outcome=ok("resolved")),
        stored(
            intent="insufficient_evidence", topic="insufficient_evidence", outcome="contradictory"
        ),
        stored(intent=ok("request_support") + ok("purchase_inquiry")),
        stored(intent=[("request_support", "inferred", 0.2)]),
        stored(intent=[("request_support", "explicit", None)]),
        _withheld_row(),
    ]
    for row in rows:
        judgment = judge(row)
        assert judgment.bucket is None and judgment.unreadable is None, row


# --- status mapping -------------------------------------------------------------------------


def test_dimension_buckets_for_every_abstain_reason() -> None:
    cases = {
        "insufficient_evidence": MissingBucket.ABSTAINED_INSUFFICIENT_EVIDENCE,
        "contradictory": MissingBucket.ABSTAINED_CONTRADICTORY,
    }
    for status, bucket in cases.items():
        judgment = judge(stored(intent=status))
        assert judgment.dimensions[0].bucket is bucket
        assert states(judgment)["intent_request_support"] == (IssueState.MISSING, bucket)
    validator = judge(stored(intent=ok("request_support") + ok("purchase_inquiry")))
    assert validator.dimensions[0].bucket is MissingBucket.ABSTAINED_CONTRADICTORY_VALUES
    low = judge(stored(intent=[("request_support", "inferred", 0.2)]))
    assert low.dimensions[0].bucket is MissingBucket.ABSTAINED_LOW_CONFIDENCE
    missing_conf = judge(stored(intent=[("request_support", "explicit", None)]))
    assert missing_conf.dimensions[0].bucket is MissingBucket.ABSTAINED_LOW_CONFIDENCE


def test_abstained_is_never_not_stated() -> None:
    judgment = judge(stored(outcome="insufficient_evidence"))
    for issue_id in ("outcome_unresolved", "outcome_escalated", "outcome_follow_up_scheduled"):
        assert states(judgment)[issue_id][0] is IssueState.MISSING
    not_stated = judge(stored(outcome="not_stated"))
    for issue_id in ("outcome_unresolved", "outcome_escalated", "outcome_follow_up_scheduled"):
        assert states(not_stated)[issue_id] == (IssueState.ABSENT, None)
    assert not_stated.dimensions[2].bucket is None


def test_reference_results() -> None:
    judgment = judge(
        stored(
            intent=ok("cancel_service"),
            topic=[("refund", "explicit", 0.9), ("invoice", "explicit", 0.1)],
            outcome=ok("escalated"),
        )
    )
    s = states(judgment)
    assert s["intent_cancel_service"] == (IssueState.PENDING_REVIEW, None)  # critical label
    assert s["intent_request_support"] == (IssueState.ABSENT, None)
    assert s["outcome_escalated"] == (IssueState.COUNTED, None)
    assert s["outcome_unresolved"] == (IssueState.ABSENT, None)
    # complaints need intent report_dissatisfaction: absent, whatever the topic says
    assert s["complaint_refund"] == (IssueState.ABSENT, None)
    assert s["complaint_invoice"] == (IssueState.ABSENT, None)


def test_withheld_label_is_value_withheld_for_that_issue_only() -> None:
    judgment = judge(
        stored(
            intent=ok("report_dissatisfaction"),
            topic=[("refund", "explicit", 0.9), ("invoice", "explicit", 0.1)],
        )
    )
    s = states(judgment)
    assert s["complaint_refund"] == (IssueState.COUNTED, None)
    assert s["complaint_invoice"] == (IssueState.MISSING, MissingBucket.VALUE_WITHHELD)
    assert s["complaint_pricing"] == (IssueState.ABSENT, None)
    assert judgment.dimensions[1].bucket is None


def test_and_combination_missing_then_pending() -> None:
    complaint_low_topic = judge(
        stored(intent=ok("report_dissatisfaction"), topic=[("refund", "inferred", 0.1)])
    )
    assert states(complaint_low_topic)["complaint_refund"] == (
        IssueState.MISSING,
        MissingBucket.ABSTAINED_LOW_CONFIDENCE,
    )
    # missing wins over pending: intent missing, topic pending (hand-built critical topic)
    row = stored(intent="insufficient_evidence", topic=ok("refund"))
    row["fields"]["topic"]["values"] = [_review_value("refund")]
    row["fields"]["topic"]["review_required"] = True
    row["needs_review"] = True
    assert states(judge(row))["complaint_refund"] == (
        IssueState.MISSING,
        MissingBucket.ABSTAINED_INSUFFICIENT_EVIDENCE,
    )
    # pending when every reference holds and one waits
    row = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"))
    row["fields"]["topic"]["values"] = [_review_value("refund")]
    row["fields"]["topic"]["review_required"] = True
    row["needs_review"] = True
    assert states(judge(row))["complaint_refund"] == (IssueState.PENDING_REVIEW, None)


def test_missing_issue_takes_the_first_missing_reference_bucket() -> None:
    row = stored(intent="contradictory", topic=[("refund", "inferred", 0.1)])
    assert states(judge(row))["complaint_refund"] == (
        IssueState.MISSING,
        MissingBucket.ABSTAINED_CONTRADICTORY,
    )


def test_best_value_wins_when_a_label_is_there_twice() -> None:
    row = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"))
    review = _review_value("refund")
    row["fields"]["topic"]["values"].append(review)
    row["fields"]["topic"]["review_required"] = True
    row["needs_review"] = True
    assert states(judge(row))["complaint_refund"] == (IssueState.COUNTED, None)


# --- families -------------------------------------------------------------------------------


def test_family_is_or_over_its_issues() -> None:
    judgment = judge(
        stored(intent=ok("report_dissatisfaction"), topic=ok("refund") + ok("invoice"))
    )
    f = family_states(judgment)
    assert f["call_reason"] == (IssueState.COUNTED, None)
    assert f["service_complaint"] == (IssueState.COUNTED, None)
    assert f["unresolved_outcome"] == (IssueState.ABSENT, None)


def test_family_pending_and_missing() -> None:
    assert family_states(judge(stored(intent=ok("cancel_service"))))["call_reason"] == (
        IssueState.PENDING_REVIEW,
        None,
    )
    low = judge(stored(intent=ok("report_dissatisfaction"), topic=[("refund", "inferred", 0.1)]))
    assert family_states(low)["service_complaint"] == (
        IssueState.MISSING,
        MissingBucket.ABSTAINED_LOW_CONFIDENCE,
    )
    withheld = judge(
        stored(
            intent=ok("report_dissatisfaction"),
            topic=[("refund", "explicit", 0.9), ("invoice", "explicit", 0.1)],
        )
    )
    assert family_states(withheld)["service_complaint"] == (IssueState.COUNTED, None)


def test_family_missing_takes_the_first_bucket_in_bucket_order(tmp_path: Path) -> None:
    # A family whose issues read two dimensions, with the topic issue first in the file:
    # the family bucket follows MissingBucket order, not the issue order.
    document = json.loads(RELEASED.read_text(encoding="utf-8"))
    extra = copy.deepcopy(document["issues"][0])
    extra.update(id="topic_refund_reason", match=[{"dimension": "topic", "label": "refund"}])
    document["issues"].insert(0, extra)
    path = tmp_path / "trends.v1.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    definition = load_definition_file(path)
    row = stored(
        intent="contradictory", topic=[("invoice", "explicit", 0.9), ("refund", "explicit", 0.1)]
    )
    judgment = judge_conversation(facts(row), definition)
    s = states(judgment)
    assert s["topic_refund_reason"] == (IssueState.MISSING, MissingBucket.VALUE_WITHHELD)
    assert s["intent_request_support"] == (
        IssueState.MISSING,
        MissingBucket.ABSTAINED_CONTRADICTORY,
    )
    assert family_states(judgment)["call_reason"] == (
        IssueState.MISSING,
        MissingBucket.ABSTAINED_CONTRADICTORY,
    )


def test_family_missing_when_no_issue_holds() -> None:
    withheld = judge(
        stored(
            intent=ok("report_dissatisfaction"),
            topic=[("refund", "explicit", 0.1), ("invoice", "explicit", 0.9)],
        )
    )
    s = states(withheld)
    assert s["complaint_refund"] == (IssueState.MISSING, MissingBucket.VALUE_WITHHELD)
    assert s["complaint_invoice"] == (IssueState.COUNTED, None)
    assert family_states(withheld)["service_complaint"] == (IssueState.COUNTED, None)
    only_withheld = judge(
        stored(
            intent=ok("report_dissatisfaction"),
            topic=[("refund", "explicit", 0.1), ("pricing", "explicit", 0.9)],
        )
    )
    assert family_states(only_withheld)["service_complaint"] == (IssueState.COUNTED, None)
    no_kept_complaint = judge(
        stored(
            intent=ok("report_dissatisfaction"),
            topic=[("refund", "explicit", 0.1), ("refund_x", "explicit", 0.9)],
        )
    )
    assert no_kept_complaint.bucket is MissingBucket.EXTRACTION_INVALID  # unknown topic label


# --- absent on an unconfirmed value ---------------------------------------------------------


def _apr(judgment) -> dict[str, bool]:
    return {i.issue_id: i.absent_pending_review for i in judgment.issues}


def test_absent_pending_review_on_a_pending_resolved_outcome() -> None:
    judgment = judge(stored(outcome=ok("resolved")))
    s, apr = states(judgment), _apr(judgment)
    for issue_id in ("outcome_unresolved", "outcome_escalated", "outcome_follow_up_scheduled"):
        assert s[issue_id] == (IssueState.ABSENT, None)
        assert apr[issue_id] is True
    family = {f.family_id: f for f in judgment.families}["unresolved_outcome"]
    assert family.state is IssueState.ABSENT and family.absent_pending_review is True


def test_absent_pending_review_on_a_pending_intent() -> None:
    judgment = judge(stored(intent=ok("cancel_service"), topic=ok("refund")))
    apr = _apr(judgment)
    assert apr["intent_request_support"] is True
    assert apr["complaint_refund"] is True  # rests on the pending cancel_service
    assert apr["intent_cancel_service"] is False  # pending, not absent
    assert apr["complaint_pricing"] is False  # topic pricing is firmly absent too


def test_firm_absence_is_not_pending() -> None:
    judgment = judge(stored(intent=ok("request_support"), outcome=ok("escalated")))
    apr = _apr(judgment)
    assert not any(apr.values())
    assert not any(f.absent_pending_review for f in judgment.families)
    assert not any(_apr(judge(stored())).values())  # not_stated is a firm absence


def test_a_pending_topic_never_makes_another_topic_pending_absent() -> None:
    row = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"))
    row["fields"]["topic"]["values"] = [_review_value("refund")]
    row["fields"]["topic"]["review_required"] = True
    row["needs_review"] = True
    judgment = judge(row)
    assert states(judgment)["complaint_pricing"] == (IssueState.ABSENT, None)
    assert _apr(judgment)["complaint_pricing"] is False


# --- dedup inside one conversation ----------------------------------------------------------


def test_many_turns_count_once() -> None:
    row = stored(intent=ok("resolve_billing"))
    row["fields"]["intent"]["values"][0]["source_turn_indexes"] = [0, 1, 2, 3]
    result = tally([judge(row)], _definition())
    assert result.issues["intent_resolve_billing"].confirmed == 1
    assert result.families["call_reason"].confirmed == 1


def test_two_topics_count_once_per_issue_and_once_for_the_family() -> None:
    judgment = judge(
        stored(intent=ok("report_dissatisfaction"), topic=ok("refund") + ok("invoice"))
    )
    result = tally([judgment], _definition())
    assert result.issues["complaint_refund"].confirmed == 1
    assert result.issues["complaint_invoice"].confirmed == 1
    assert result.families["service_complaint"].confirmed == 1


# --- contract of the facts ------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"extraction_run_id": "run0"},
        {"analysis_run_id": None, "analysis_state": None, "analysis_revision_id": None},
        {"analysis_state": None},
        {"analysis_revision_id": None},
        {
            "analysis_run_id": None,
            "analysis_state": "completed",
            "analysis_revision_id": None,
            "extraction_run_id": None,
            "extraction": None,
        },
        {
            "analysis_run_id": None,
            "analysis_state": None,
            "analysis_revision_id": "rev1",
            "extraction_run_id": None,
            "extraction": None,
        },
        {
            "analysis_run_id": None,
            "analysis_state": None,
            "analysis_revision_id": None,
            "analysis_is_mock": True,
            "extraction_run_id": None,
            "extraction": None,
        },
        {"analysis_state": "COMPLETED"},
        {"analysis_state": "done"},
    ],
)
def test_facts_that_break_their_contract_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        rest = dict(changes)
        extraction = rest.pop("extraction", stored())
        judge_conversation(facts(extraction, **rest), _definition())


def test_an_extraction_without_a_row_is_refused() -> None:
    with pytest.raises(ValueError):
        judge_conversation(facts(stored(), extraction_run_id=None), _definition())


def test_the_judge_never_changes_its_input() -> None:
    row = stored(intent=ok("cancel_service"), topic=ok("refund"), outcome=ok("resolved"))
    before = copy.deepcopy(row)
    judge(row)
    assert row == before


def test_the_judge_is_deterministic() -> None:
    row = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"))
    assert judge(row) == judge(copy.deepcopy(row))


# --- the judgment ---------------------------------------------------------------------------


def test_judgment_provenance_and_lookup() -> None:
    judgment = judge(stored(intent=ok("request_support")), media_group_id="g7")
    assert judgment.meeting_id == "m1"
    assert judgment.media_group_id == "g7"
    assert judgment.trends_version == TRENDS_VERSION
    assert (judgment.definition_id, judgment.definition_version) == ("trends", 1)
    assert judgment.definition_digest == _definition().digest
    assert judgment.issue("intent_request_support").state is IssueState.COUNTED
    assert judgment.family("call_reason").state is IssueState.COUNTED
    with pytest.raises(KeyError):
        judgment.issue("nope")
    with pytest.raises(KeyError):
        judgment.family("qa")  # an unavailable family is never judged


def test_judgment_to_json() -> None:
    judgment = judge(stored(intent=ok("report_dissatisfaction"), topic=ok("refund")))
    data = judgment.to_json()
    assert list(data) == [
        "meeting_id",
        "media_group_id",
        "trends_version",
        "definition",
        "exclusion",
        "bucket",
        "unreadable",
        "dimensions",
        "issues",
        "families",
    ]
    assert data["definition"] == {"id": "trends", "version": 1, "digest": _definition().digest}
    assert data["exclusion"] is None and data["bucket"] is None and data["unreadable"] is None
    assert data["dimensions"] == {
        "intent": {"bucket": None},
        "topic": {"bucket": None},
        "outcome": {"bucket": None},
    }
    assert list(data["issues"]) == [issue.id for issue in _definition().active_issues()]
    assert data["issues"]["complaint_refund"] == {
        "state": "counted",
        "bucket": None,
        "absent_pending_review": False,
    }
    assert list(data["families"]) == AVAILABLE_FAMILIES
    unreadable = judge({"x": 1}).to_json()
    assert unreadable["bucket"] == "extraction_unreadable"
    assert unreadable["unreadable"] == "shape_version_fields"
    assert unreadable["issues"]["outcome_unresolved"] == {
        "state": "missing",
        "bucket": "extraction_unreadable",
        "absent_pending_review": False,
    }
    excluded = judge(is_counted_copy=False).to_json()
    assert excluded["exclusion"] == "duplicate_media"
    assert excluded["dimensions"] == {} and excluded["issues"] == {} and excluded["families"] == {}


# --- tally ----------------------------------------------------------------------------------


def _many() -> list:
    return [
        judge(
            stored(intent=ok("report_dissatisfaction"), topic=ok("refund")),
            meeting_id="a",
            media_group_id="ga",
        ),
        judge(stored(intent=ok("cancel_service")), meeting_id="b", media_group_id="gb"),
        judge(stored(outcome=ok("resolved")), meeting_id="c", media_group_id="gc"),
        judge(stored(intent="insufficient_evidence"), meeting_id="d", media_group_id="gd"),
        judge(None, meeting_id="e", media_group_id="ge", extraction_run_id=None),
        judge(meeting_id="f", media_group_id="ga", is_counted_copy=False),
    ]


def test_tally_counts_distinct_conversations() -> None:
    result = tally(_many(), _definition())
    assert result.trends_version == TRENDS_VERSION
    assert (result.definition_id, result.definition_version) == ("trends", 1)
    assert result.definition_digest == _definition().digest
    assert result.included == 5
    assert result.excluded == {Exclusion.DUPLICATE_MEDIA: 1}
    assert list(result.issues) == [issue.id for issue in _definition().active_issues()]
    assert list(result.families) == AVAILABLE_FAMILIES
    support = result.issues["intent_request_support"]
    assert (support.confirmed, support.pending_review, support.absent) == (0, 0, 3)
    assert support.absent_pending_review == 1  # the pending cancel_service
    assert support.missing[MissingBucket.ABSTAINED_INSUFFICIENT_EVIDENCE] == 1
    assert support.missing[MissingBucket.EXTRACTION_UNAVAILABLE] == 1
    assert support.denominator == 3
    cancel = result.issues["intent_cancel_service"]
    assert (cancel.confirmed, cancel.pending_review) == (0, 1)
    unresolved = result.issues["outcome_unresolved"]
    assert unresolved.absent == 4 and unresolved.absent_pending_review == 1
    family = result.families["call_reason"]
    assert (family.confirmed, family.pending_review, family.absent) == (1, 1, 1)
    complaint = result.families["service_complaint"]
    assert complaint.confirmed == 1


def test_tally_invariant_for_every_issue_and_family() -> None:
    result = tally(_many(), _definition())
    for counts in [*result.issues.values(), *result.families.values()]:
        assert set(counts.missing) == set(MissingBucket)
        assert (
            counts.confirmed + counts.pending_review + counts.absent + sum(counts.missing.values())
            == result.included
        )
        assert counts.denominator == counts.confirmed + counts.pending_review + counts.absent
        assert 0 <= counts.absent_pending_review <= counts.absent


def test_an_empty_tally() -> None:
    result = tally([], _definition())
    assert result.included == 0
    assert result.excluded == {Exclusion.DUPLICATE_MEDIA: 0}
    assert result.issues["complaint_refund"].denominator == 0


def test_tally_refuses_a_meeting_twice() -> None:
    with pytest.raises(ValueError):
        tally(
            [
                judge(meeting_id="a", media_group_id="ga"),
                judge(meeting_id="a", media_group_id="gb"),
            ],
            _definition(),
        )
    with pytest.raises(ValueError):
        tally(
            [
                judge(meeting_id="a", is_counted_copy=False),
                judge(meeting_id="a", is_counted_copy=False),
            ],
            _definition(),
        )


def test_tally_refuses_two_counted_copies_of_one_group() -> None:
    with pytest.raises(ValueError):
        tally(
            [judge(meeting_id="a", media_group_id="g"), judge(meeting_id="b", media_group_id="g")],
            _definition(),
        )


def test_tally_accepts_excluded_members_of_a_group() -> None:
    result = tally(
        [
            judge(meeting_id="a", media_group_id="g"),
            judge(meeting_id="b", media_group_id="g", is_counted_copy=False),
            judge(meeting_id="c", media_group_id="g", is_counted_copy=False),
        ],
        _definition(),
    )
    assert result.included == 1 and result.excluded[Exclusion.DUPLICATE_MEDIA] == 2


def test_tally_refuses_a_judgment_of_another_definition() -> None:
    other = judge()
    for changes in (
        {"definition_digest": "0" * 64},
        {"definition_version": 2},
        {"definition_id": "other"},
    ):
        changed = type(other)(**{**other.__dict__, **changes})
        with pytest.raises(ValueError):
            tally([changed], _definition())
