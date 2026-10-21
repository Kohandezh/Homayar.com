"""Deterministic extraction validator of the conversation taxonomy (HOM-52, WP11-T02).

Every deny-case is the valid base extraction with exactly one thing wrong, so the
rejection comes from the intended rule.  Each has its closest allow-control.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.analysis_taxonomy import (
    VALIDATOR_VERSION,
    Basis,
    Extraction,
    FieldStatus,
    ReasonCode,
    Verdict,
    load_taxonomy,
    load_taxonomy_file,
    validate_extraction,
)

TAXONOMY = load_taxonomy("conversation", 1)
DEFINITIONS = Path(__file__).resolve().parent.parent / "analysis_taxonomy" / "definitions"

TURNS = (
    "سلام، برای فاکتور اشتباه تماس گرفتم و می‌خواهم پولم را پس بدید",
    "لطفا شماره پیگیری را بگویید",
    "کد پیگیری ۴۸۲۱۳ است و شماره من ۰۹۱۲۳۴۵۶۷۸۹",
    "مبلغ برگشت داده شد، مشکل شما حل شد",
    "ممنون، خداحافظ",
)


def _value(label: str, turns: list[int], basis: str = "explicit", confidence=None) -> dict:
    return {
        "label": label,
        "basis": basis,
        "source_turn_indexes": turns,
        "confidence": confidence,
    }


def _entity(label: str, text: str, turns: list[int]) -> dict:
    return {**_value(label, turns, confidence=0.95), "text": text}


def base() -> dict:
    return {
        "intent": {"status": "stated", "values": [_value("resolve_billing", [0], confidence=0.9)]},
        "topic": {
            "status": "stated",
            "values": [_value("refund", [0, 3]), _value("invoice", [0], "inferred", 0.6)],
        },
        "entity": {
            "status": "stated",
            "values": [_entity("reference_id", "48213", [2]), _entity("phone", "09123456789", [2])],
        },
        "outcome": {"status": "stated", "values": [_value("resolved", [3])]},
    }


def run(extraction):
    return validate_extraction(extraction, TAXONOMY, TURNS)


def codes(result, dimension: str) -> list[str]:
    return [issue.code.value for issue in result.field(dimension).issues]


def assert_only(result, dimension: str, code: str, verdict: Verdict = Verdict.REJECTED) -> None:
    """``dimension`` has exactly one issue, ``code``; every other field is accepted."""
    assert result.issues == ()
    field = result.field(dimension)
    assert field.verdict is verdict
    assert codes(result, dimension) == [code]
    assert field.values == ()
    for other in result.fields:
        if other.dimension != dimension:
            assert other.verdict is Verdict.ACCEPTED, (other.dimension, other.issues)
    assert result.valid is (verdict is not Verdict.REJECTED)


def assert_all_accepted(result) -> None:
    assert result.issues == ()
    assert [f.verdict for f in result.fields] == [Verdict.ACCEPTED] * 4, [
        (f.dimension, f.issues) for f in result.fields
    ]
    assert result.valid


# --- the valid base ----------------------------------------------------------------------


def test_base_extraction_is_accepted_and_names_the_taxonomy() -> None:
    result = run(base())
    assert_all_accepted(result)
    assert result.taxonomy_id == "conversation"
    assert result.taxonomy_version == 1
    assert result.taxonomy_digest == TAXONOMY.digest
    assert result.validator_version == VALIDATOR_VERSION
    assert [f.dimension for f in result.fields] == ["intent", "topic", "entity", "outcome"]

    intent = result.field("intent")
    assert intent.status is FieldStatus.STATED
    assert intent.labels == ("resolve_billing",)
    assert intent.abstain_value is None
    assert intent.rejected_turn_indexes == 0
    value = intent.values[0]
    assert (value.basis, value.source_turn_indexes, value.confidence) == (
        Basis.EXPLICIT,
        (0,),
        0.9,
    )
    assert value.critical is False and value.text is None

    assert result.field("topic").labels == ("refund", "invoice")
    entity = result.field("entity")
    assert entity.labels == ("reference_id", "phone")
    assert [v.text for v in entity.values] == ["48213", "09123456789"]
    assert all(v.critical for v in entity.values)
    assert result.field("outcome").values[0].critical is True
    with pytest.raises(KeyError):
        result.field("sentiment")


def test_pydantic_extraction_instance_is_accepted() -> None:
    model = Extraction.model_validate(base())
    assert run(model) == run(base())


def test_validator_is_pure_and_deterministic() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["source_turn_indexes"] = [3, 0, 3, 99]
    before = copy.deepcopy(extraction)
    first = run(extraction)
    second = run(extraction)
    assert extraction == before
    assert first == second


# --- SPEC A5 deny-cases and allow-controls -------------------------------------------------


@pytest.mark.parametrize("label", ["Refund", "refund ", "REFUND", "refunds", "request_support"])
def test_unsupported_label(label: str) -> None:
    extraction = base()
    extraction["topic"]["values"][0]["label"] = label
    result = run(extraction)
    assert_only(result, "topic", "unsupported_label")
    assert result.field("topic").issues[0].path == "topic.values[0].label"


def test_stated_without_a_turn_index() -> None:
    extraction = base()
    extraction["intent"]["values"][0]["source_turn_indexes"] = []
    assert_only(run(extraction), "intent", "too_few_turns")
    extraction["intent"]["values"][0]["source_turn_indexes"] = [0]
    assert_all_accepted(run(extraction))


def test_stated_entity_without_a_turn_index_has_one_code() -> None:
    extraction = base()
    extraction["entity"]["values"][0]["source_turn_indexes"] = []
    assert_only(run(extraction), "entity", "too_few_turns")


def test_too_few_turns_follows_the_label_minimum(tmp_path: Path) -> None:
    document = json.loads((DEFINITIONS / "conversation.v1.json").read_text(encoding="utf-8"))
    document["dimensions"]["outcome"]["labels"][0]["min_source_turns"] = 2
    path = tmp_path / "conversation.v1.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    taxonomy = load_taxonomy_file(path)
    extraction = base()
    extraction["outcome"]["values"][0]["source_turn_indexes"] = [3, 3]
    result = validate_extraction(extraction, taxonomy, TURNS)
    assert codes(result, "outcome") == ["too_few_turns"]
    extraction["outcome"]["values"][0]["source_turn_indexes"] = [3, 4]
    assert validate_extraction(extraction, taxonomy, TURNS).field("outcome").verdict is (
        Verdict.ACCEPTED
    )


@pytest.mark.parametrize("bad", [5, 99, -1])
def test_turn_index_out_of_range(bad: int) -> None:
    extraction = base()
    extraction["intent"]["values"][0]["source_turn_indexes"] = [bad]
    result = run(extraction)
    assert_only(result, "intent", "turn_index_out_of_range")
    assert result.field("intent").rejected_turn_indexes == 1
    assert result.field("intent").issues[0].path == "intent.values[0].source_turn_indexes[0]"
    extraction["intent"]["values"][0]["source_turn_indexes"] = [4]
    assert_all_accepted(run(extraction))


def test_out_of_range_indexes_are_counted_never_repaired() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["source_turn_indexes"] = [0, 7, 7, -2, 3]
    result = run(extraction)
    assert codes(result, "topic") == ["turn_index_out_of_range"] * 2
    assert result.field("topic").rejected_turn_indexes == 2
    assert result.field("topic").values == ()


def test_rejected_turn_indexes_counts_distinct_bad_indexes_of_the_field() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["source_turn_indexes"] = [9]
    extraction["topic"]["values"][1]["source_turn_indexes"] = [9]
    result = run(extraction)
    field = result.field("topic")
    assert field.rejected_turn_indexes == 1
    assert [(i.code.value, i.path) for i in field.issues] == [
        ("turn_index_out_of_range", "topic.values[0].source_turn_indexes[0]"),
        ("turn_index_out_of_range", "topic.values[1].source_turn_indexes[0]"),
    ]


def test_a_bad_index_cited_twice_in_one_value_is_one_issue() -> None:
    extraction = base()
    extraction["intent"]["values"][0]["source_turn_indexes"] = [9, 9]
    result = run(extraction)
    assert_only(result, "intent", "turn_index_out_of_range")
    assert result.field("intent").rejected_turn_indexes == 1


def test_duplicate_turn_indexes_collapse() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["source_turn_indexes"] = [3, 0, 3]
    result = run(extraction)
    assert_all_accepted(result)
    assert result.field("topic").values[0].source_turn_indexes == (3, 0)


@pytest.mark.parametrize("status", ["not_stated", "insufficient_evidence", "contradictory"])
def test_non_stated_status_with_values_is_rejected(status: str) -> None:
    extraction = base()
    extraction["outcome"]["status"] = status
    result = run(extraction)
    assert_only(result, "outcome", "values_not_allowed")
    assert result.field("outcome").status is FieldStatus(status)


def test_not_stated_without_values_is_accepted() -> None:
    extraction = base()
    extraction["outcome"] = {"status": "not_stated", "values": []}
    result = run(extraction)
    assert_all_accepted(result)
    field = result.field("outcome")
    assert field.status is FieldStatus.NOT_STATED
    assert field.values == () and field.labels == () and field.abstain_value is None


@pytest.mark.parametrize("status", ["insufficient_evidence", "contradictory"])
def test_abstain_without_values_is_abstained(status: str) -> None:
    extraction = base()
    extraction["intent"] = {"status": status, "values": []}
    result = run(extraction)
    field = result.field("intent")
    assert field.verdict is Verdict.ABSTAINED
    assert field.status is FieldStatus(status) is field.abstain_value
    assert field.values == () and field.issues == ()
    assert result.valid


def test_entity_insufficient_evidence_is_abstained() -> None:
    extraction = base()
    extraction["entity"] = {"status": "insufficient_evidence", "values": []}
    assert run(extraction).field("entity").verdict is Verdict.ABSTAINED


@pytest.mark.parametrize("dimension", ["topic", "entity"])
def test_contradictory_on_a_multi_valued_dimension(dimension: str) -> None:
    extraction = base()
    extraction[dimension] = {"status": "contradictory", "values": []}
    assert_only(run(extraction), dimension, "status_not_allowed")


def test_stated_without_values() -> None:
    extraction = base()
    extraction["topic"]["values"] = []
    assert_only(run(extraction), "topic", "missing_values")


@pytest.mark.parametrize(
    ("dimension", "index", "label"),
    [("intent", 0, "cancel_service"), ("outcome", 0, "resolved"), ("entity", 0, "reference_id")],
)
def test_basis_the_label_does_not_allow(dimension: str, index: int, label: str) -> None:
    extraction = base()
    extraction[dimension]["values"][index]["label"] = label
    extraction[dimension]["values"][index]["basis"] = "inferred"
    result = run(extraction)
    assert_only(result, dimension, "basis_not_allowed")
    assert result.field(dimension).issues[0].path == f"{dimension}.values[{index}].basis"
    extraction[dimension]["values"][index]["basis"] = "explicit"
    assert_all_accepted(run(extraction))


def test_inferred_unresolved_is_allowed() -> None:
    extraction = base()
    extraction["outcome"]["values"][0] = _value("unresolved", [4], "inferred")
    assert_all_accepted(run(extraction))


def test_entity_text_not_in_the_cited_turn() -> None:
    extraction = base()
    extraction["entity"]["values"][0]["text"] = "4821"
    result = run(extraction)
    assert_only(result, "entity", "entity_text_not_found")
    assert result.field("entity").issues[0].path == "entity.values[0].text"


def test_entity_text_must_be_in_a_cited_turn() -> None:
    extraction = base()
    extraction["entity"]["values"][0]["source_turn_indexes"] = [0]
    assert_only(run(extraction), "entity", "entity_text_not_found")
    extraction["entity"]["values"][0]["source_turn_indexes"] = [0, 2]
    assert_all_accepted(run(extraction))


@pytest.mark.parametrize("text", ["48213", "۴۸۲۱۳", "٤٨٢١٣"])
def test_entity_text_matches_across_digit_scripts(text: str) -> None:
    extraction = base()
    extraction["entity"]["values"][0]["text"] = text
    assert_all_accepted(run(extraction))


def test_entity_text_matches_persian_digits_in_the_turn() -> None:
    turns = ("my number is ۰۹۱۲۳۴۵۶۷۸۹ thanks",) + TURNS[1:]
    extraction = base()
    extraction["entity"]["values"][1]["source_turn_indexes"] = [0]
    assert validate_extraction(extraction, TAXONOMY, turns).field("entity").verdict is (
        Verdict.ACCEPTED
    )


def test_entity_text_matches_arabic_letter_variants() -> None:
    turns = TURNS[:4] + ("اسم من علي كريمي است",)
    extraction = base()
    extraction["entity"]["values"][1] = _entity("person_name", "علی کریمی", [4])
    assert validate_extraction(extraction, TAXONOMY, turns).field("entity").verdict is (
        Verdict.ACCEPTED
    )


@pytest.mark.parametrize("text", ["   ", "...", "0912345678"])
def test_entity_text_that_is_empty_after_folding_or_a_partial_token(text: str) -> None:
    extraction = base()
    extraction["entity"]["values"][1]["text"] = text
    assert_only(run(extraction), "entity", "entity_text_not_found")


def test_single_valued_field_with_two_values_abstains_as_contradictory() -> None:
    extraction = base()
    extraction["intent"]["values"].append(_value("request_support", [0]))
    result = run(extraction)
    assert_only(result, "intent", "contradictory_values", Verdict.ABSTAINED)
    field = result.field("intent")
    assert field.status is FieldStatus.CONTRADICTORY is field.abstain_value
    assert field.labels == ()
    extraction["intent"]["values"].pop()
    assert_all_accepted(run(extraction))


def test_outcome_with_two_values_abstains_as_contradictory() -> None:
    extraction = base()
    extraction["outcome"]["values"].append(_value("unresolved", [4], "inferred"))
    assert_only(run(extraction), "outcome", "contradictory_values", Verdict.ABSTAINED)


def test_duplicate_label_in_a_multi_valued_field() -> None:
    extraction = base()
    extraction["topic"]["values"].append(_value("refund", [3]))
    result = run(extraction)
    assert_only(result, "topic", "duplicate_label")
    assert result.field("topic").issues[0].path == "topic.values[2].label"
    extraction["topic"]["values"].pop()
    assert_all_accepted(run(extraction))


def test_duplicate_label_in_a_single_valued_field() -> None:
    extraction = base()
    extraction["intent"]["values"].append(_value("resolve_billing", [0]))
    assert_only(run(extraction), "intent", "duplicate_label")


def test_duplicate_entity_is_the_same_label_and_the_same_folded_text() -> None:
    extraction = base()
    extraction["entity"]["values"].append(_entity("phone", "۰۹۱۲۳۴۵۶۷۸۹", [2]))
    assert_only(run(extraction), "entity", "duplicate_label")
    extraction["entity"]["values"][-1] = _entity("reference_id", "09123456789", [2])
    assert_all_accepted(run(extraction))


@pytest.mark.parametrize("key", ["estimated_nps", "churn_risk", "sentiment", "category"])
def test_extraction_with_an_unknown_top_level_key(key: str) -> None:
    extraction = base()
    extraction[key] = 9
    result = run(extraction)
    assert [(i.code, i.path) for i in result.issues] == [(ReasonCode.UNKNOWN_KEY, key)]
    assert [f.verdict for f in result.fields] == [Verdict.ACCEPTED] * 4
    assert result.valid is False
    del extraction[key]
    assert_all_accepted(run(extraction))


@pytest.mark.parametrize(
    ("place", "path"),
    [
        (lambda e: e["intent"], "intent.sentiment"),
        (lambda e: e["intent"]["values"][0], "intent.values[0].sentiment"),
        (lambda e: e["entity"]["values"][0], "entity.values[0].sentiment"),
    ],
)
def test_unknown_key_inside_a_field(place, path: str) -> None:
    extraction = base()
    place(extraction)["sentiment"] = 0.4
    result = run(extraction)
    dimension = path.split(".", 1)[0]
    assert_only(result, dimension, "unknown_key")
    assert result.field(dimension).issues[0].path == path
    assert result.field(dimension).status is FieldStatus.STATED


def test_text_on_a_non_entity_value_is_an_unknown_key() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["text"] = "refund"
    assert_only(run(extraction), "topic", "unknown_key")


# --- tier 1 shape errors (pydantic models of extraction.py, M1) ----------------------------

TIER_1_CASES = [
    ("missing dimension", lambda e: e.pop("outcome"), "outcome", "missing_field", "outcome"),
    (
        "missing values",
        lambda e: e["topic"].pop("values"),
        "topic",
        "missing_field",
        "topic.values",
    ),
    (
        "missing confidence",
        lambda e: e["intent"]["values"][0].pop("confidence"),
        "intent",
        "missing_field",
        "intent.values[0].confidence",
    ),
    (
        "missing entity text",
        lambda e: e["entity"]["values"][0].pop("text"),
        "entity",
        "missing_field",
        "entity.values[0].text",
    ),
    (
        "unknown status",
        lambda e: e["intent"].__setitem__("status", "unknown"),
        "intent",
        "invalid_value",
        "intent.status",
    ),
    (
        "unknown basis",
        lambda e: e["topic"]["values"][0].__setitem__("basis", "guess"),
        "topic",
        "invalid_value",
        "topic.values[0].basis",
    ),
    (
        "confidence above one",
        lambda e: e["intent"]["values"][0].__setitem__("confidence", 1.5),
        "intent",
        "invalid_value",
        "intent.values[0].confidence",
    ),
    (
        "confidence as text",
        lambda e: e["intent"]["values"][0].__setitem__("confidence", "0.5"),
        "intent",
        "invalid_value",
        "intent.values[0].confidence",
    ),
    (
        "index as bool",
        lambda e: e["intent"]["values"][0].__setitem__("source_turn_indexes", [True]),
        "intent",
        "invalid_value",
        "intent.values[0].source_turn_indexes[0]",
    ),
    (
        "index as text",
        lambda e: e["intent"]["values"][0].__setitem__("source_turn_indexes", ["0"]),
        "intent",
        "invalid_value",
        "intent.values[0].source_turn_indexes[0]",
    ),
    (
        "index as float",
        lambda e: e["intent"]["values"][0].__setitem__("source_turn_indexes", [0.0]),
        "intent",
        "invalid_value",
        "intent.values[0].source_turn_indexes[0]",
    ),
    (
        "label not a string",
        lambda e: e["topic"]["values"][0].__setitem__("label", 3),
        "topic",
        "invalid_value",
        "topic.values[0].label",
    ),
    (
        "empty entity text",
        lambda e: e["entity"]["values"][0].__setitem__("text", ""),
        "entity",
        "invalid_value",
        "entity.values[0].text",
    ),
    (
        "field not an object",
        lambda e: e.__setitem__("topic", ["refund"]),
        "topic",
        "invalid_value",
        "topic",
    ),
    (
        "too many values",
        lambda e: e["topic"].__setitem__("values", [_value(f"label_{i}", [0]) for i in range(33)]),
        "topic",
        "invalid_value",
        "topic.values",
    ),
    (
        "too many indexes",
        lambda e: e["topic"]["values"][0].__setitem__("source_turn_indexes", [0] * 65),
        "topic",
        "invalid_value",
        "topic.values[0].source_turn_indexes",
    ),
    (
        "unknown key in a value",
        lambda e: e["outcome"]["values"][0].__setitem__("estimated_nps", 8),
        "outcome",
        "unknown_key",
        "outcome.values[0].estimated_nps",
    ),
]


@pytest.mark.parametrize(
    ("edit", "dimension", "code", "path"),
    [case[1:] for case in TIER_1_CASES],
    ids=[case[0] for case in TIER_1_CASES],
)
def test_tier_1_shape_errors(edit, dimension: str, code: str, path: str) -> None:
    extraction = base()
    edit(extraction)
    result = run(extraction)
    assert_only(result, dimension, code)
    assert result.field(dimension).issues[0].path == path


def test_base_passes_the_extraction_model_and_each_tier_1_case_fails_it() -> None:
    Extraction.model_validate(base())
    for name, edit, *_ in TIER_1_CASES:
        extraction = base()
        edit(extraction)
        with pytest.raises(ValidationError):
            Extraction.model_validate(extraction)
        assert run(extraction).valid is False, name


def test_extraction_json_schema_requires_every_key() -> None:
    schema = Extraction.model_json_schema()
    assert set(schema["required"]) == {"intent", "topic", "entity", "outcome"}
    assert schema["additionalProperties"] is False
    models = {name: d for name, d in schema["$defs"].items() if d.get("type") == "object"}
    assert set(models) == {"LabelField", "EntityField", "LabelValue", "EntityValue"}
    for name, model in models.items():
        assert set(model["required"]) == set(model["properties"]), name
        assert model["additionalProperties"] is False, name
    keys = json.dumps(schema)
    for forbidden in ("nps", "churn", "sentiment", "category"):
        assert forbidden not in keys


def test_a_field_with_an_unparseable_status_has_no_status() -> None:
    extraction = base()
    extraction["intent"]["status"] = "unknown"
    assert run(extraction).field("intent").status is None


def test_input_that_is_not_an_object() -> None:
    result = run(["intent"])
    assert [(i.code, i.path) for i in result.issues] == [(ReasonCode.INVALID_VALUE, "")]
    for field in result.fields:
        assert field.verdict is Verdict.REJECTED
        assert codes(result, field.dimension) == ["missing_field"]
    assert result.valid is False


# --- tier order and deprecation -------------------------------------------------------------


def test_only_the_first_failing_tier_is_reported() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["label"] = "Refund"  # tier 2
    extraction["topic"]["values"][1]["source_turn_indexes"] = [99]  # tier 3
    extraction["topic"]["values"].append(_value("invoice", [0]))  # tier 4
    result = run(extraction)
    assert codes(result, "topic") == ["unsupported_label"]
    assert result.field("topic").rejected_turn_indexes == 0


def test_issues_of_one_tier_follow_table_order_then_position() -> None:
    extraction = base()
    extraction["intent"]["values"][0] = _value("cancel_service", [0], "inferred")
    extraction["intent"]["values"].append(_value("Nope", [0]))
    result = run(extraction)
    assert codes(result, "intent") == ["unsupported_label", "basis_not_allowed"]
    assert [i.path for i in result.field("intent").issues] == [
        "intent.values[1].label",
        "intent.values[0].basis",
    ]


def test_each_field_is_judged_on_its_own() -> None:
    extraction = base()
    extraction["topic"]["values"][0]["label"] = "Refund"
    extraction["outcome"]["values"][0]["source_turn_indexes"] = []
    result = run(extraction)
    assert codes(result, "topic") == ["unsupported_label"]
    assert codes(result, "outcome") == ["too_few_turns"]
    assert result.field("intent").verdict is Verdict.ACCEPTED
    assert result.field("entity").verdict is Verdict.ACCEPTED


def test_deprecated_label_is_rejected(tmp_path: Path) -> None:
    document = json.loads((DEFINITIONS / "conversation.v1.json").read_text(encoding="utf-8"))
    document["version"] = 2
    document["dimensions"]["topic"]["labels"][1].update(status="deprecated", replaced_by="payment")
    path = tmp_path / "conversation.v2.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    taxonomy = load_taxonomy_file(path)
    result = validate_extraction(base(), taxonomy, TURNS)
    assert codes(result, "topic") == ["deprecated_label"]
    assert result.taxonomy_version == 2
    assert result.taxonomy_digest == taxonomy.digest != TAXONOMY.digest
