"""Conversation taxonomy definition: loader, digest pin and version rules (HOM-52, WP11-T02)."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from backend.analysis_taxonomy import (
    ABSTAIN_VALUES,
    DEFAULT_TAXONOMY_ID,
    DIMENSIONS,
    TAXONOMY_SCHEMA,
    VALIDATOR_VERSION,
    Basis,
    Cardinality,
    FieldStatus,
    LabelStatus,
    TaxonomyError,
    TextMatch,
    load_taxonomy,
    load_taxonomy_file,
    released_versions,
    taxonomy_digest,
)

BACKEND = Path(__file__).resolve().parent.parent
DEFINITIONS = BACKEND / "analysis_taxonomy" / "definitions"

# A released file never changes: a change is a new version file with its own pin.
RELEASED_TAXONOMY_DIGESTS = {
    ("conversation", 1): "52748000de889391c1f3eba57f286ccc8cde0919554ced27d1e4f8bd51bd7e9e",
}
PINNED_VALIDATOR_VERSION = "analysis-taxonomy-validator/1"

V1_LABELS = {
    "intent": [
        "request_support",
        "report_dissatisfaction",
        "purchase_inquiry",
        "resolve_billing",
        "schedule_appointment",
        "request_callback",
        "cancel_service",
        "unsolicited",
    ],
    "topic": [
        "pricing",
        "refund",
        "invoice",
        "payment",
        "delivery",
        "account_access",
        "connectivity",
        "contract",
        "installation",
        "scheduling",
        "technical_fault",
    ],
    "entity": [
        "phone",
        "national_id",
        "email",
        "amount",
        "date",
        "date_range",
        "time",
        "reference_id",
        "person_name",
    ],
    "outcome": ["resolved", "escalated", "follow_up_scheduled", "unresolved"],
}
V1_CRITICAL = {
    "cancel_service",
    "unsolicited",
    "phone",
    "national_id",
    "email",
    "amount",
    "reference_id",
    "person_name",
    "resolved",
}
V1_EXPLICIT_ONLY = {
    "cancel_service",
    *V1_LABELS["entity"],
    "resolved",
    "escalated",
    "follow_up_scheduled",
}


def _v1_document() -> dict:
    return json.loads((DEFINITIONS / "conversation.v1.json").read_text(encoding="utf-8"))


def _write(tmp_path: Path, document: dict, name: str = "conversation.v1.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _refused(tmp_path: Path, document: dict, code: str = "taxonomy_invalid") -> TaxonomyError:
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(_write(tmp_path, document))
    assert caught.value.code == code
    return caught.value


# --- the released definition -------------------------------------------------------------


RELEASED_FILE_NAME = re.compile(r"^(?P<id>[a-z][a-z0-9_]{1,63})\.v(?P<version>[1-9][0-9]*)\.json$")


def _released_digests(folder: Path) -> dict[tuple[str, int], str]:
    """Every entry of ``folder`` must be a ``{id}.v{version}.json`` file, so a stray file
    (``conversation.v01.json``, ``notes.txt``) cannot hide from the pins."""
    found = {}
    for path in sorted(folder.iterdir()):
        if path.name == "__pycache__":
            continue
        named = RELEASED_FILE_NAME.fullmatch(path.name)
        assert named is not None and path.is_file(), f"stray entry in definitions: {path.name}"
        taxonomy = load_taxonomy_file(path)
        assert (taxonomy.taxonomy_id, taxonomy.version) == (named["id"], int(named["version"]))
        found[(taxonomy.taxonomy_id, taxonomy.version)] = taxonomy.digest
    return found


def test_released_files_match_their_pinned_digests() -> None:
    assert _released_digests(DEFINITIONS) == RELEASED_TAXONOMY_DIGESTS


@pytest.mark.parametrize(
    "stray", ["conversation.v01.json", "conversation.v0.json", "conversation.json", "notes.txt"]
)
def test_a_stray_file_in_definitions_cannot_hide(tmp_path: Path, stray: str) -> None:
    (tmp_path / "conversation.v1.json").write_text(
        (DEFINITIONS / "conversation.v1.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert _released_digests(tmp_path) == RELEASED_TAXONOMY_DIGESTS
    (tmp_path / stray).write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="stray entry"):
        _released_digests(tmp_path)


@pytest.mark.parametrize("name", ["conversation.v01.json", "conversation.v0.json"])
def test_a_version_with_leading_zeros_or_zero_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(_write(tmp_path, _v1_document(), name))
    assert caught.value.code == "taxonomy_invalid"


def test_released_versions_ignores_names_that_are_not_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.analysis_taxonomy import models

    for name in ("conversation.v1.json", "conversation.v01.json", "conversation.v0.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(models, "DEFINITIONS", tmp_path)
    assert released_versions("conversation") == (1,)


def test_load_taxonomy_returns_the_pinned_version() -> None:
    taxonomy = load_taxonomy("conversation", 1)
    assert taxonomy.schema_id == TAXONOMY_SCHEMA == "homayar.conversation-taxonomy/1"
    assert taxonomy.taxonomy_id == DEFAULT_TAXONOMY_ID == "conversation"
    assert taxonomy.version == 1
    assert (
        taxonomy.digest
        == taxonomy_digest(taxonomy)
        == RELEASED_TAXONOMY_DIGESTS[("conversation", 1)]
    )
    assert released_versions() == (1,)
    assert released_versions("conversation") == (1,)
    assert load_taxonomy("conversation", 1, expected_digest=taxonomy.digest) is taxonomy


def test_validator_version_is_pinned() -> None:
    assert VALIDATOR_VERSION == PINNED_VALIDATOR_VERSION


def test_v1_label_lists() -> None:
    taxonomy = load_taxonomy("conversation", 1)
    for name in DIMENSIONS:
        dimension = taxonomy.dimension(name)
        assert [label.id for label in dimension.labels] == V1_LABELS[name]
        assert dimension.retired_label_ids == ()
        for label in dimension.labels:
            assert label.status is LabelStatus.ACTIVE
            assert label.replaced_by is None
            assert label.min_source_turns == 1
            assert label.critical is (label.id in V1_CRITICAL)
            expected = (
                (Basis.EXPLICIT,)
                if label.id in V1_EXPLICIT_ONLY
                else (Basis.EXPLICIT, Basis.INFERRED)
            )
            assert label.allowed_basis == expected
            assert label.name_en and label.name_fa and label.definition


def test_v1_dimension_rules() -> None:
    taxonomy = load_taxonomy("conversation", 1)
    single = {"intent", "outcome"}
    for name in DIMENSIONS:
        dimension = taxonomy.dimension(name)
        assert dimension.cardinality is (
            Cardinality.SINGLE if name in single else Cardinality.MULTI
        )
        expected = (
            (FieldStatus.INSUFFICIENT_EVIDENCE, FieldStatus.CONTRADICTORY)
            if name in single
            else (FieldStatus.INSUFFICIENT_EVIDENCE,)
        )
        assert dimension.abstain_values == expected
        assert dimension.text_match is (
            TextMatch.NORMALIZED_TOKEN_SPAN if name == "entity" else TextMatch.NONE
        )
    with pytest.raises(KeyError):
        taxonomy.dimension("sentiment")


def test_every_dimension_has_labels_evidence_and_abstain_values() -> None:
    taxonomy = load_taxonomy("conversation", 1)
    label_ids = {label.id for name in DIMENSIONS for label in taxonomy.dimension(name).labels}
    assert ABSTAIN_VALUES == {FieldStatus.INSUFFICIENT_EVIDENCE, FieldStatus.CONTRADICTORY}
    for name in DIMENSIONS:
        dimension = taxonomy.dimension(name)
        assert dimension.labels
        assert dimension.abstain_values
        for label in dimension.labels:
            assert label.allowed_basis and label.min_source_turns >= 1
    for status in FieldStatus:
        assert status.value not in label_ids


def test_label_lookup() -> None:
    taxonomy = load_taxonomy("conversation", 1)
    assert taxonomy.label("topic", "refund").id == "refund"
    assert taxonomy.label("topic", "Refund") is None
    assert taxonomy.label("intent", "refund") is None


def test_definition_has_no_forbidden_fields() -> None:
    forbidden = re.compile(r"nps|churn|sentiment|category", re.IGNORECASE)

    def keys(node: object) -> list[str]:
        if isinstance(node, dict):
            return [k for key, value in node.items() for k in (key, *keys(value))]
        if isinstance(node, list):
            return [k for item in node for k in keys(item)]
        return []

    document = _v1_document()
    assert not [key for key in keys(document) if forbidden.search(key)]
    ids = [label["id"] for dim in document["dimensions"].values() for label in dim["labels"]]
    assert not [label_id for label_id in ids if forbidden.search(label_id)]
    assert "other" not in ids and "unknown" not in ids


# --- digest --------------------------------------------------------------------------------


def test_digest_ignores_formatting_but_not_meaning(tmp_path: Path) -> None:
    document = _v1_document()
    base = load_taxonomy_file(_write(tmp_path, document)).digest
    assert base == RELEASED_TAXONOMY_DIGESTS[("conversation", 1)]

    reordered = json.dumps(dict(reversed(list(document.items()))), indent=4, ensure_ascii=True)
    (tmp_path / "conversation.v1.json").write_text(reordered, encoding="utf-8")
    assert load_taxonomy_file(tmp_path / "conversation.v1.json").digest == base

    edits = [
        lambda d: d["dimensions"]["topic"]["labels"][1].__setitem__("id", "refunds"),
        lambda d: d["dimensions"]["outcome"]["labels"][0].__setitem__("min_source_turns", 2),
        lambda d: d["dimensions"]["intent"]["labels"][0].__setitem__("critical", True),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("definition", "Prices."),
        lambda d: d["dimensions"]["intent"]["labels"][6].__setitem__(
            "allowed_basis", ["explicit", "inferred"]
        ),
    ]
    for edit in edits:
        changed = copy.deepcopy(document)
        edit(changed)
        assert load_taxonomy_file(_write(tmp_path, changed)).digest != base


# --- loader refusals -----------------------------------------------------------------------


def test_unknown_version_is_refused_without_fallback() -> None:
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy("conversation", 99)
    assert caught.value.code == "taxonomy_unknown"
    for bad_id in ("missing", "../providers/manifests/builtin", "Conversation"):
        with pytest.raises(TaxonomyError) as caught:
            load_taxonomy(bad_id, 1)
        assert caught.value.code == "taxonomy_unknown"


def test_expected_digest_mismatch_is_refused() -> None:
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy("conversation", 1, expected_digest="0" * 64)
    assert caught.value.code == "taxonomy_digest_mismatch"


def test_unknown_top_level_key_is_refused(tmp_path: Path) -> None:
    document = _v1_document()
    document["estimated_nps"] = 7
    _refused(tmp_path, document)


def test_unknown_label_key_is_refused(tmp_path: Path) -> None:
    document = _v1_document()
    document["dimensions"]["intent"]["labels"][0]["churn_probability"] = 0.2
    _refused(tmp_path, document)


def test_unknown_dimension_is_refused(tmp_path: Path) -> None:
    document = _v1_document()
    document["dimensions"]["sentiment"] = copy.deepcopy(document["dimensions"]["topic"])
    _refused(tmp_path, document)


def test_duplicate_label_id_in_a_dimension_is_refused(tmp_path: Path) -> None:
    document = _v1_document()
    labels = document["dimensions"]["topic"]["labels"]
    labels.append(copy.deepcopy(labels[0]))
    _refused(tmp_path, document)


def test_the_same_id_in_two_dimensions_is_refused(tmp_path: Path) -> None:
    document = _v1_document()
    label = copy.deepcopy(document["dimensions"]["intent"]["labels"][0])
    document["dimensions"]["topic"]["labels"].append(label)
    _refused(tmp_path, document)


@pytest.mark.parametrize("bad_id", ["Refund", "refund-x", "1refund", "r", "refund ", ""])
def test_bad_id_format_is_refused(tmp_path: Path, bad_id: str) -> None:
    document = _v1_document()
    document["dimensions"]["topic"]["labels"][0]["id"] = bad_id
    _refused(tmp_path, document)


@pytest.mark.parametrize(
    "reserved",
    [
        "stated",
        "not_stated",
        "insufficient_evidence",
        "contradictory",
        "unknown",
        "unavailable",
        "other",
        "none",
        "explicit",
        "inferred",
    ],
)
def test_reserved_word_as_label_id_is_refused(tmp_path: Path, reserved: str) -> None:
    document = _v1_document()
    document["dimensions"]["topic"]["labels"][0]["id"] = reserved
    _refused(tmp_path, document)


@pytest.mark.parametrize(
    "path",
    [
        ("schema",),
        ("taxonomy_id",),
        ("version",),
        ("dimensions",),
        ("dimensions", "outcome"),
        ("dimensions", "intent", "cardinality"),
        ("dimensions", "intent", "abstain_values"),
        ("dimensions", "entity", "text_match"),
        ("dimensions", "topic", "retired_label_ids"),
        ("dimensions", "topic", "labels", 0, "definition"),
        ("dimensions", "topic", "labels", 0, "allowed_basis"),
        ("dimensions", "topic", "labels", 0, "min_source_turns"),
        ("dimensions", "topic", "labels", 0, "critical"),
        ("dimensions", "topic", "labels", 0, "status"),
        ("dimensions", "topic", "labels", 0, "name_fa"),
    ],
)
def test_missing_required_field_is_refused(tmp_path: Path, path: tuple) -> None:
    document = _v1_document()
    node = document
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    _refused(tmp_path, document)


def test_optional_fields_may_be_missing(tmp_path: Path) -> None:
    document = _v1_document()
    document.pop("notes", None)
    document["dimensions"]["topic"]["labels"][0].pop("replaced_by", None)
    load_taxonomy_file(_write(tmp_path, document))


def test_duplicate_json_key_is_refused(tmp_path: Path) -> None:
    text = json.dumps(_v1_document(), ensure_ascii=False)
    text = text.replace('"version": 1,', '"version": 1, "version": 1,', 1)
    path = tmp_path / "conversation.v1.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(path)
    assert caught.value.code == "taxonomy_invalid"


def test_unreadable_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "conversation.v1.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(path)
    assert caught.value.code == "taxonomy_unreadable"
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(tmp_path / "missing.v1.json")
    assert caught.value.code == "taxonomy_unreadable"


def test_wrong_schema_is_refused(tmp_path: Path) -> None:
    document = _v1_document()
    document["schema"] = "homayar.model-manifests/1"
    _refused(tmp_path, document)


def test_content_must_agree_with_the_file_name(tmp_path: Path) -> None:
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(_write(tmp_path, _v1_document(), "conversation.v2.json"))
    assert caught.value.code == "taxonomy_invalid"
    with pytest.raises(TaxonomyError) as caught:
        load_taxonomy_file(_write(tmp_path, _v1_document(), "calls.v1.json"))
    assert caught.value.code == "taxonomy_invalid"


@pytest.mark.parametrize(
    "edit",
    [
        # contradictory only on a single-valued dimension
        lambda d: d["dimensions"]["topic"].__setitem__(
            "abstain_values", ["insufficient_evidence", "contradictory"]
        ),
        # insufficient_evidence is always listed
        lambda d: d["dimensions"]["intent"].__setitem__("abstain_values", ["contradictory"]),
        lambda d: d["dimensions"]["intent"].__setitem__("abstain_values", []),
        lambda d: d["dimensions"]["intent"].__setitem__(
            "abstain_values", ["insufficient_evidence", "insufficient_evidence"]
        ),
        lambda d: d["dimensions"]["intent"].__setitem__(
            "abstain_values", ["insufficient_evidence", "not_stated"]
        ),
        # the token-span text rule belongs to entity, and entity needs it
        lambda d: d["dimensions"]["topic"].__setitem__("text_match", "normalized_token_span"),
        lambda d: d["dimensions"]["entity"].__setitem__("text_match", "none"),
        # an entity is never inferred
        lambda d: d["dimensions"]["entity"]["labels"][0].__setitem__(
            "allowed_basis", ["explicit", "inferred"]
        ),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("allowed_basis", []),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__(
            "allowed_basis", ["explicit", "explicit"]
        ),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("min_source_turns", 0),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("min_source_turns", "1"),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("critical", "false"),
        lambda d: d.__setitem__("version", "1"),
        lambda d: d["dimensions"]["topic"].__setitem__("labels", []),
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("definition", "Two\nlines."),
        # replaced_by only on a deprecated label, pointing at an active id of the dimension
        lambda d: d["dimensions"]["topic"]["labels"][0].__setitem__("replaced_by", "refund"),
        lambda d: d["dimensions"]["topic"]["labels"][0].update(
            status="deprecated", replaced_by="resolved"
        ),
        lambda d: d["dimensions"]["topic"]["labels"][0].update(
            status="deprecated", replaced_by="pricing"
        ),
        # a retired id is never a label
        lambda d: d["dimensions"]["topic"].__setitem__("retired_label_ids", ["pricing"]),
        lambda d: d["dimensions"]["topic"].__setitem__("retired_label_ids", ["Bad-Id"]),
    ],
)
def test_dimension_and_label_rules_are_enforced(tmp_path: Path, edit) -> None:
    document = _v1_document()
    edit(document)
    _refused(tmp_path, document)


def test_a_deprecated_label_with_a_replacement_loads(tmp_path: Path) -> None:
    document = _v1_document()
    document["dimensions"]["topic"]["labels"][0].update(status="deprecated", replaced_by="refund")
    document["dimensions"]["topic"]["retired_label_ids"] = ["old_topic"]
    taxonomy = load_taxonomy_file(_write(tmp_path, document))
    assert taxonomy.label("topic", "pricing").status is LabelStatus.DEPRECATED


# --- version rules across released files ---------------------------------------------------


def test_released_versions_keep_label_meanings() -> None:
    taxonomies = [load_taxonomy("conversation", v) for v in released_versions("conversation")]
    for index, earlier in enumerate(taxonomies):
        for position, later in enumerate(taxonomies[index + 1 :], start=index + 1):
            for name in DIMENSIONS:
                after = {label.id: label for label in later.dimension(name).labels}
                retired = set(later.dimension(name).retired_label_ids)
                for label in earlier.dimension(name).labels:
                    if label.id in after:
                        assert after[label.id].definition == label.definition, label.id
                        continue
                    # removed: retired, and deprecated in a release before the removal
                    assert label.id in retired, label.id
                    assert any(
                        t.label(name, label.id) is not None
                        and t.label(name, label.id).status is LabelStatus.DEPRECATED
                        for t in taxonomies[index:position]
                    ), label.id
                for label_id in earlier.dimension(name).retired_label_ids:
                    assert label_id not in after, label_id
                other_ids = {
                    label.id
                    for other in DIMENSIONS
                    if other != name
                    for label in later.dimension(other).labels
                }
                assert not {label.id for label in earlier.dimension(name).labels} & other_ids
