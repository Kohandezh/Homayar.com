"""Confidence, abstention and critical-value review policy of the taxonomy (HOM-53).

Every deny-case is the valid base extraction with one thing changed, so the result comes
from the intended rule.  Each has its closest allow-control.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import replace

import pytest

from backend.analysis_taxonomy import (
    POLICY_V1,
    POLICY_VERSION,
    VALIDATOR_VERSION,
    AbstainReason,
    AcceptedValue,
    Basis,
    Disposition,
    ExtractionPolicy,
    ExtractionValidation,
    FieldStatus,
    FieldValidation,
    ReviewReason,
    Verdict,
    WithheldReason,
    apply_policy,
    evaluate_extraction,
    load_taxonomy,
    policy_digest,
    validate_extraction,
)

TAXONOMY = load_taxonomy("conversation", 1)

TURNS = (
    "سلام، برای فاکتور اشتباه تماس گرفتم و می‌خواهم پولم را پس بدید",
    "لطفا شماره پیگیری را بگویید",
    "کد پیگیری ۴۸۲۱۳ است و شماره من ۰۹۱۲۳۴۵۶۷۸۹",
    "مبلغ برگشت داده شد، مشکل شما حل شد",
    "ممنون، خداحافظ، فردا ساعت ۱۰ تماس بگیرید",
)

# Pinned: a threshold change without a new version must fail here.
POLICY_V1_DIGEST = "309c4abbd4eef8c975ac64aef597ce4fe6ba755ac4f998a35a0f305bc47a62de"


def _value(label: str, turns: list[int], basis: str = "explicit", confidence=0.9) -> dict:
    return {
        "label": label,
        "basis": basis,
        "source_turn_indexes": turns,
        "confidence": confidence,
    }


def _entity(label: str, text: str, turns: list[int], confidence=0.95) -> dict:
    return {**_value(label, turns, confidence=confidence), "text": text}


def base() -> dict:
    """Valid; no value is critical, every confidence passes."""
    return {
        "intent": {"status": "stated", "values": [_value("resolve_billing", [0])]},
        "topic": {
            "status": "stated",
            "values": [_value("refund", [0, 3]), _value("invoice", [0], "inferred", 0.8)],
        },
        "entity": {"status": "stated", "values": [_entity("date", "فردا", [4])]},
        "outcome": {"status": "not_stated", "values": []},
    }


def run(extraction, policy: ExtractionPolicy = POLICY_V1):
    return evaluate_extraction(extraction, TAXONOMY, TURNS, policy)


def field_json(result, dimension: str) -> dict:
    return result.to_json()["fields"][dimension]


def with_intent(label: str, confidence, basis: str = "explicit") -> dict:
    extraction = base()
    extraction["intent"]["values"] = [_value(label, [0], basis, confidence)]
    return extraction


# --- the valid base ----------------------------------------------------------------------


def test_base_is_auto_accepted_and_names_every_version() -> None:
    result = run(base())
    assert result.valid
    assert result.needs_review is False
    data = result.to_json()
    assert data["policy_version"] == POLICY_VERSION == "extraction-policy/1"
    assert data["policy_digest"] == policy_digest(POLICY_V1)
    assert data["taxonomy_id"] == "conversation"
    assert data["taxonomy_version"] == 1
    assert data["taxonomy_digest"] == TAXONOMY.digest
    assert data["validator_version"] == VALIDATOR_VERSION
    assert data["valid"] is True
    assert data["needs_review"] is False
    assert data["issues"] == []
    assert list(data["fields"]) == ["intent", "topic", "entity", "outcome"]
    assert data["fields"]["intent"] == {
        "verdict": "accepted",
        "status": "stated",
        "abstain_reason": None,
        "review_required": False,
        "issues": [],
        "values": [
            {
                "label": "resolve_billing",
                "basis": "explicit",
                "source_turn_indexes": [0],
                "confidence": 0.9,
                "critical": False,
                "text": None,
                "disposition": "auto_accepted",
                "review_reason": None,
            }
        ],
        "withheld": [],
    }
    assert [v["label"] for v in data["fields"]["topic"]["values"]] == ["refund", "invoice"]
    assert data["fields"]["entity"]["values"][0]["text"] == "فردا"
    assert data["fields"]["outcome"] == {
        "verdict": "accepted",
        "status": "not_stated",
        "abstain_reason": None,
        "review_required": False,
        "issues": [],
        "values": [],
        "withheld": [],
    }


def test_field_lookup_returns_the_dimension_and_refuses_others() -> None:
    result = run(base())
    intent = result.field("intent")
    assert intent.verdict is Verdict.ACCEPTED
    assert intent.status is FieldStatus.STATED
    assert intent.abstain_reason is None
    assert intent.review_required is False
    assert [v.label for v in intent.values] == ["resolve_billing"]
    assert intent.values[0].disposition is Disposition.AUTO_ACCEPTED
    with pytest.raises(KeyError):
        result.field("sentiment")


def test_evaluate_extraction_is_apply_policy_of_the_validator() -> None:
    extraction = with_intent("cancel_service", 0.9)
    validation = validate_extraction(extraction, TAXONOMY, TURNS)
    assert run(extraction) == apply_policy(validation)
    assert run(extraction) == apply_policy(validation, POLICY_V1)


def test_to_json_is_plain_json() -> None:
    extraction = with_intent("cancel_service", 0.9)
    extraction["topic"]["values"][1]["confidence"] = 0.1
    extraction["estimated_nps"] = 9
    data = run(extraction).to_json()
    assert json.loads(json.dumps(data)) == data
    assert isinstance(data["fields"]["intent"]["values"][0]["source_turn_indexes"], list)
    assert isinstance(data["fields"]["topic"]["withheld"][0]["source_turn_indexes"], list)


def test_policy_is_pure_and_deterministic() -> None:
    extraction = with_intent("cancel_service", 0.9)
    before = copy.deepcopy(extraction)
    validation = validate_extraction(extraction, TAXONOMY, TURNS)
    validation_before = copy.deepcopy(validation)
    first = apply_policy(validation)
    second = apply_policy(validation)
    assert first == second
    assert first.to_json() == second.to_json()
    assert validation == validation_before
    assert extraction == before


# --- 2.2 per-value thresholds ------------------------------------------------------------


@pytest.mark.parametrize(
    ("basis", "confidence", "kept"),
    [
        ("explicit", 0.49, False),
        ("explicit", 0.5, True),
        ("explicit", 0.6, True),
        ("inferred", 0.69, False),
        ("inferred", 0.7, True),
        ("explicit", 0.0, False),
        ("inferred", 1.0, True),
    ],
)
def test_threshold_depends_on_basis_and_the_boundary_passes(basis, confidence, kept) -> None:
    extraction = with_intent("request_support", confidence, basis)
    intent = field_json(run(extraction), "intent")
    if kept:
        assert intent["verdict"] == "accepted"
        assert intent["values"][0]["confidence"] == confidence
        assert intent["values"][0]["disposition"] == "auto_accepted"
        assert intent["withheld"] == []
    else:
        assert intent["values"] == []
        assert intent["withheld"][0]["reason"] == "below_threshold"


def test_missing_confidence_is_withheld() -> None:
    intent = field_json(run(with_intent("request_support", None)), "intent")
    assert intent["values"] == []
    assert intent["withheld"][0]["reason"] == WithheldReason.MISSING_CONFIDENCE.value
    assert intent["withheld"][0]["confidence"] is None


def test_allow_control_high_confidence_is_kept() -> None:
    intent = field_json(run(with_intent("request_support", 0.95)), "intent")
    assert [v["label"] for v in intent["values"]] == ["request_support"]


def test_withheld_value_has_the_value_keys_and_a_reason() -> None:
    extraction = base()
    extraction["entity"]["values"] = [
        _entity("date", "فردا", [4]),
        _entity("phone", "09123456789", [2], confidence=0.3),
    ]
    entity = field_json(run(extraction), "entity")
    assert entity["withheld"] == [
        {
            "label": "phone",
            "basis": "explicit",
            "source_turn_indexes": [2],
            "confidence": 0.3,
            "critical": True,
            "text": "09123456789",
            "reason": "below_threshold",
        }
    ]


# --- 2.2 critical values -----------------------------------------------------------------


def test_critical_label_goes_to_review_never_auto_accepted() -> None:
    result = run(with_intent("cancel_service", 0.99))
    value = field_json(result, "intent")["values"][0]
    assert value["critical"] is True
    assert value["disposition"] == "review_required"
    assert value["review_reason"] == "critical_label"
    assert result.field("intent").review_required is True
    assert result.field("intent").values[0].review_reason is ReviewReason.CRITICAL_LABEL
    assert result.needs_review is True
    assert result.to_json()["needs_review"] is True


def test_allow_control_non_critical_label_is_auto_accepted() -> None:
    result = run(with_intent("request_support", 0.99))
    value = field_json(result, "intent")["values"][0]
    assert value["disposition"] == "auto_accepted"
    assert value["review_reason"] is None
    assert result.needs_review is False


def test_critical_entity_goes_to_review_and_non_critical_does_not() -> None:
    extraction = base()
    extraction["entity"]["values"] = [
        _entity("phone", "09123456789", [2]),
        _entity("date", "فردا", [4]),
    ]
    result = run(extraction)
    entity = field_json(result, "entity")
    assert [(v["label"], v["disposition"]) for v in entity["values"]] == [
        ("phone", "review_required"),
        ("date", "auto_accepted"),
    ]
    assert entity["review_required"] is True
    assert result.needs_review is True


def test_critical_value_below_threshold_is_withheld_not_routed_to_review() -> None:
    result = run(with_intent("cancel_service", 0.49))
    intent = field_json(result, "intent")
    assert intent["values"] == []
    assert intent["withheld"][0]["critical"] is True
    assert intent["review_required"] is False
    assert result.needs_review is False


def test_allow_control_critical_value_at_threshold_goes_to_review() -> None:
    result = run(with_intent("cancel_service", 0.5))
    assert field_json(result, "intent")["values"][0]["disposition"] == "review_required"
    assert result.needs_review is True


def test_criticality_comes_from_the_validator_flag_not_the_label_name() -> None:
    validation = validate_extraction(with_intent("request_support", 0.9), TAXONOMY, TURNS)
    intent = validation.field("intent")
    flagged = replace(intent, values=(replace(intent.values[0], critical=True),))
    fields = tuple(flagged if f.dimension == "intent" else f for f in validation.fields)
    forged = replace(validation, fields=fields)
    value = apply_policy(forged).field("intent").values[0]
    assert value.label == "request_support"
    assert value.disposition is Disposition.REVIEW_REQUIRED


@pytest.mark.parametrize(("confidence", "kept"), [(math.nan, False), (0.5, True)])
def test_nan_confidence_fails_closed(confidence, kept) -> None:
    # The pydantic shape refuses NaN, so build the validator result by hand.
    value = AcceptedValue("request_support", Basis.EXPLICIT, (0,), confidence, False, None)
    fields = tuple(
        FieldValidation(name, Verdict.ACCEPTED, FieldStatus.STATED, (value,), (), 0)
        if name == "intent"
        else FieldValidation(name, Verdict.ACCEPTED, FieldStatus.NOT_STATED, (), (), 0)
        for name in ("intent", "topic", "entity", "outcome")
    )
    validation = ExtractionValidation(
        "conversation", 1, TAXONOMY.digest, VALIDATOR_VERSION, fields, ()
    )
    intent = apply_policy(validation).field("intent")
    if kept:
        assert intent.verdict is Verdict.ACCEPTED
        assert [v.disposition for v in intent.values] == [Disposition.AUTO_ACCEPTED]
        assert intent.withheld == ()
    else:
        assert intent.verdict is Verdict.ABSTAINED
        assert intent.values == ()
        assert [w.reason for w in intent.withheld] == [WithheldReason.BELOW_THRESHOLD]


# --- 2.3 per-field rules -----------------------------------------------------------------


def test_single_valued_field_with_only_value_withheld_abstains() -> None:
    result = run(with_intent("request_support", 0.2))
    intent = field_json(result, "intent")
    assert intent["verdict"] == "abstained"
    assert intent["status"] == "insufficient_evidence"
    assert intent["abstain_reason"] == "low_confidence"
    assert intent["values"] == []
    assert [w["label"] for w in intent["withheld"]] == ["request_support"]
    assert intent["review_required"] is False
    assert result.field("intent").abstain_reason is AbstainReason.LOW_CONFIDENCE
    assert result.valid is True


def test_allow_control_same_field_at_threshold_is_accepted() -> None:
    intent = field_json(run(with_intent("request_support", 0.5)), "intent")
    assert (intent["verdict"], intent["status"], intent["abstain_reason"]) == (
        "accepted",
        "stated",
        None,
    )


def test_multi_valued_field_with_every_value_withheld_abstains() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["confidence"] = None
    extraction["topic"]["values"][1]["confidence"] = 0.69
    topic = field_json(run(extraction), "topic")
    assert topic["verdict"] == "abstained"
    assert topic["status"] == "insufficient_evidence"
    assert topic["abstain_reason"] == "low_confidence"
    assert topic["values"] == []
    assert [(w["label"], w["reason"]) for w in topic["withheld"]] == [
        ("refund", "missing_confidence"),
        ("invoice", "below_threshold"),
    ]


def test_allow_control_one_of_two_values_kept_keeps_input_order() -> None:
    extraction = base()
    extraction["topic"]["values"] = [
        _value("pricing", [0], confidence=0.1),
        _value("refund", [0, 3]),
        _value("invoice", [0], confidence=None),
        _value("payment", [3], confidence=0.6),
    ]
    topic = field_json(run(extraction), "topic")
    assert (topic["verdict"], topic["status"]) == ("accepted", "stated")
    assert [v["label"] for v in topic["values"]] == ["refund", "payment"]
    assert [w["label"] for w in topic["withheld"]] == ["pricing", "invoice"]


def test_validator_contradictory_intent_abstains_as_contradictory() -> None:
    extraction = base()
    extraction["intent"]["values"] = [
        _value("resolve_billing", [0]),
        _value("cancel_service", [0], confidence=0.99),
    ]
    result = run(extraction)
    intent = field_json(result, "intent")
    assert intent["verdict"] == "abstained"
    assert intent["status"] == "contradictory"
    assert intent["abstain_reason"] == "contradictory"
    assert intent["values"] == [] and intent["withheld"] == []
    assert intent["review_required"] is False
    assert intent["issues"] == [{"code": "contradictory_values", "path": "intent"}]
    assert result.needs_review is False


@pytest.mark.parametrize("status", ["insufficient_evidence", "contradictory"])
def test_model_abstention_keeps_the_input_status(status) -> None:
    extraction = base()
    extraction["intent"] = {"status": status, "values": []}
    intent = field_json(run(extraction), "intent")
    assert intent["verdict"] == "abstained"
    assert intent["status"] == status
    assert intent["abstain_reason"] == "model"
    assert intent["values"] == [] and intent["withheld"] == []
    assert intent["issues"] == []


def test_rejected_field_has_no_status_values_or_review() -> None:
    extraction = base()
    extraction["intent"]["values"] = [_value("cancel_everything", [0], confidence=0.99)]
    result = run(extraction)
    intent = field_json(result, "intent")
    assert intent == {
        "verdict": "rejected",
        "status": None,
        "abstain_reason": None,
        "review_required": False,
        "issues": [{"code": "unsupported_label", "path": "intent.values[0].label"}],
        "values": [],
        "withheld": [],
    }
    assert result.valid is False
    assert result.to_json()["valid"] is False


def test_rejected_critical_field_is_not_routed_to_review() -> None:
    extraction = base()
    extraction["entity"]["values"] = [_entity("phone", "09120000000", [2])]
    result = run(extraction)
    assert field_json(result, "entity")["verdict"] == "rejected"
    assert result.needs_review is False


def test_unknown_top_level_key_makes_it_invalid_and_is_listed() -> None:
    extraction = base()
    extraction["estimated_nps"] = 9
    result = run(extraction)
    assert result.valid is False
    assert result.to_json()["issues"] == [{"code": "unknown_key", "path": "estimated_nps"}]
    assert field_json(result, "intent")["verdict"] == "accepted"


def test_allow_control_without_unknown_key_is_valid() -> None:
    assert run(base()).valid is True


def test_policy_never_changes_validity() -> None:
    for extraction in (base(), with_intent("request_support", 0.1)):
        validation = validate_extraction(extraction, TAXONOMY, TURNS)
        assert apply_policy(validation).valid is validation.valid is True
    extraction = base()
    extraction["outcome"] = {"status": "stated", "values": []}
    validation = validate_extraction(extraction, TAXONOMY, TURNS)
    assert apply_policy(validation).valid is validation.valid is False


def test_single_valued_outcome_follows_the_value_rules() -> None:
    extraction = base()
    extraction["outcome"] = {"status": "stated", "values": [_value("resolved", [3])]}
    assert field_json(run(extraction), "outcome")["values"][0]["disposition"] == ("review_required")
    extraction["outcome"]["values"][0]["confidence"] = 0.4
    outcome = field_json(run(extraction), "outcome")
    assert (outcome["verdict"], outcome["abstain_reason"]) == ("abstained", "low_confidence")


# --- 2.6 versioning ----------------------------------------------------------------------


def test_policy_version_and_digest_are_pinned() -> None:
    assert POLICY_VERSION == "extraction-policy/1"
    assert POLICY_V1 == ExtractionPolicy(
        version="extraction-policy/1", min_confidence_explicit=0.5, min_confidence_inferred=0.7
    )
    assert policy_digest(POLICY_V1) == POLICY_V1_DIGEST


def test_policy_digest_is_sha256_of_the_canonical_fields() -> None:
    canonical = (
        '{"min_confidence_explicit":0.5,"min_confidence_inferred":0.7,'
        '"version":"extraction-policy/1"}'
    )
    assert policy_digest(POLICY_V1) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_any_threshold_change_changes_the_digest() -> None:
    changed = ExtractionPolicy("extraction-policy/1", 0.5, 0.71)
    assert policy_digest(changed) != policy_digest(POLICY_V1)


def test_a_custom_policy_is_applied_and_named_in_the_result() -> None:
    strict = ExtractionPolicy("extraction-policy/test", 0.95, 0.99)
    result = run(with_intent("request_support", 0.9), strict)
    assert field_json(result, "intent")["withheld"][0]["reason"] == "below_threshold"
    assert result.to_json()["policy_version"] == "extraction-policy/test"
    assert result.to_json()["policy_digest"] == policy_digest(strict)


@pytest.mark.parametrize(
    "policy",
    [
        ExtractionPolicy("extraction-policy/1", 1.5, 0.7),
        ExtractionPolicy("extraction-policy/1", 0.5, -0.1),
        ExtractionPolicy("extraction-policy/1", math.nan, 0.7),
        ExtractionPolicy("", 0.5, 0.7),
        ExtractionPolicy("   ", 0.5, 0.7),
    ],
)
def test_a_broken_policy_is_refused(policy) -> None:
    validation = validate_extraction(base(), TAXONOMY, TURNS)
    with pytest.raises(ValueError):
        apply_policy(validation, policy)


@pytest.mark.parametrize("bound", [0.0, 1.0])
def test_allow_control_thresholds_on_the_bounds_work(bound) -> None:
    validation = validate_extraction(base(), TAXONOMY, TURNS)
    assert apply_policy(validation, ExtractionPolicy("extraction-policy/x", bound, bound)).valid
