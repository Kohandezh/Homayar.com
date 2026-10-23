"""Trend definition file: loader, deny codes, digest and version pins (HOM-79, D1 and D6)."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.analysis_taxonomy import POLICY_V1, VALIDATOR_VERSION, load_taxonomy, policy_digest
from backend.issue_trends import (
    DEFINITION_SCHEMA,
    RESERVED_IDS,
    SAMPLE_POLICY_V1,
    SAMPLE_POLICY_VERSION,
    TRENDS_VERSION,
    DefinitionErrorCode,
    FamilySource,
    IssueStatus,
    SourceReason,
    SourceState,
    TrendDefinition,
    TrendDefinitionError,
    definition_digest,
    load_definition,
    load_definition_file,
    released_versions,
    sample_policy_digest,
    service_area_ids,
)

BACKEND = Path(__file__).resolve().parent.parent
PACKAGE = BACKEND / "issue_trends"
DEFINITIONS = PACKAGE / "definitions"
RELEASED = DEFINITIONS / "trends.v1.json"

# A released file never changes: a change is a new version file with its own pin.
RELEASED_DEFINITION_DIGESTS = {
    ("trends", 1): "1d7ef96eec5fff028481b1ecea297d7ccce64be2e16985c87ec58a8ce97a17de",
}
PINNED_TRENDS_VERSION = "issue-trends/1"
PINNED_SAMPLE_POLICY_VERSION = "trend-sample-policy/1"
PINNED_SAMPLE_POLICY_DIGEST = "e0a00f274f7bdac50774b294df188487f2e67077c99de4de261a5563031e13a9"
PINNED_SCHEMA = "homayar.issue-trends/1"
TAXONOMY_DIGEST = "52748000de889391c1f3eba57f286ccc8cde0919554ced27d1e4f8bd51bd7e9e"

V1_ISSUES = {
    "call_reason": [
        "intent_request_support",
        "intent_report_dissatisfaction",
        "intent_purchase_inquiry",
        "intent_resolve_billing",
        "intent_schedule_appointment",
        "intent_request_callback",
        "intent_cancel_service",
        "intent_unsolicited",
    ],
    "unresolved_outcome": [
        "outcome_unresolved",
        "outcome_escalated",
        "outcome_follow_up_scheduled",
    ],
    "service_complaint": [
        f"complaint_{topic}"
        for topic in (
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
        )
    ],
    "qa": [],
}


def _document() -> dict:
    return json.loads(RELEASED.read_text(encoding="utf-8"))


def _write(folder: Path, document: object, name: str = "trends.v1.json") -> Path:
    path = folder / name
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _refused(tmp_path: Path, document: object) -> DefinitionErrorCode:
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition_file(_write(tmp_path, document))
    return caught.value.code


def _issue(document: dict, issue_id: str) -> dict:
    return next(issue for issue in document["issues"] if issue["id"] == issue_id)


# --- pins ----------------------------------------------------------------------------------


def test_version_constants_are_pinned() -> None:
    assert TRENDS_VERSION == PINNED_TRENDS_VERSION
    assert SAMPLE_POLICY_VERSION == PINNED_SAMPLE_POLICY_VERSION
    assert SAMPLE_POLICY_V1.version == PINNED_SAMPLE_POLICY_VERSION
    assert DEFINITION_SCHEMA == PINNED_SCHEMA
    assert sample_policy_digest(SAMPLE_POLICY_V1) == PINNED_SAMPLE_POLICY_DIGEST


def test_released_files_match_their_pinned_digests() -> None:
    found = {}
    for path in sorted(DEFINITIONS.glob("*.json")):
        definition = load_definition_file(path)
        found[(definition.definition_id, definition.version)] = definition.digest
    assert found == RELEASED_DEFINITION_DIGESTS


def test_the_cached_digest_equals_the_recomputed_one(tmp_path: Path) -> None:
    for definition in (
        load_definition("trends", 1),
        load_definition_file(RELEASED),
        load_definition_file(_write(tmp_path, {**_document(), "notes": "other"})),
    ):
        assert definition.digest == definition_digest(definition)
        assert definition.digest == definition_digest(
            TrendDefinition.model_validate(definition.model_dump(mode="json", by_alias=True))
        )


def test_judging_and_tallying_never_recompute_the_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.issue_trends.definition as module
    from backend.issue_trends import ConversationFacts, judge_conversation, tally

    definition = load_definition("trends", 1)
    calls: list[object] = []
    real = module.definition_digest

    def counting(value: TrendDefinition) -> str:
        calls.append(value)
        return real(value)

    monkeypatch.setattr(module, "definition_digest", counting)
    judgments = [
        judge_conversation(
            ConversationFacts(
                meeting_id=f"m{index}",
                ingestion_id=f"i{index}",
                media_group_id=f"g{index}",
                is_counted_copy=True,
                current_revision_id="r",
                transcript_is_mock=False,
                analysis_run_id=None,
                analysis_state=None,
                analysis_revision_id=None,
                analysis_is_mock=False,
                extraction_run_id=None,
                extraction=None,
            ),
            definition,
        )
        for index in range(200)
    ]
    assert tally(judgments, definition).included == 200
    assert calls == []


def test_load_definition_pins_version_and_digest() -> None:
    definition = load_definition("trends", 1)
    assert definition.digest == definition_digest(definition)
    assert definition.digest == RELEASED_DEFINITION_DIGESTS[("trends", 1)]
    again = load_definition("trends", 1, expected_digest=definition.digest)
    assert again == definition
    assert released_versions() == (1,)
    assert released_versions("trends") == (1,)


def test_the_file_pins_the_taxonomy_policy_and_validator() -> None:
    definition = load_definition("trends", 1)
    assert definition.taxonomy.taxonomy_id == "conversation"
    assert definition.taxonomy.taxonomy_version == 1
    assert definition.taxonomy.taxonomy_digest == TAXONOMY_DIGEST
    assert definition.taxonomy.taxonomy_digest == load_taxonomy("conversation", 1).digest
    assert [(p.policy_version, p.policy_digest) for p in definition.extraction_policies] == [
        ("extraction-policy/1", policy_digest(POLICY_V1))
    ]
    assert definition.validator_versions == (VALIDATOR_VERSION,)


# --- version 1 content ---------------------------------------------------------------------


def test_v1_families_and_issues() -> None:
    definition = load_definition("trends", 1)
    assert [family.id for family in definition.families] == list(V1_ISSUES)
    for family_id, issue_ids in V1_ISSUES.items():
        assert [issue.id for issue in definition.active_issues(family_id)] == issue_ids
    assert [issue.id for issue in definition.active_issues()] == [
        issue_id for ids in V1_ISSUES.values() for issue_id in ids
    ]
    assert definition.retired_issue_ids == ()
    qa = definition.family("qa")
    assert qa is not None
    assert qa.source is FamilySource.SCORECARD
    assert qa.source_state is SourceState.UNAVAILABLE
    assert qa.source_reason is SourceReason.SCORECARD_RESULTS_UNAVAILABLE
    for family_id in ("call_reason", "unresolved_outcome", "service_complaint"):
        family = definition.family(family_id)
        assert family is not None
        assert family.source is FamilySource.CONVERSATION_TAXONOMY
        assert family.source_state is SourceState.AVAILABLE
        assert family.source_reason is None
    assert definition.family("nope") is None
    assert definition.issue("nope") is None


def test_v1_references_are_active_labels_of_the_pinned_taxonomy() -> None:
    definition = load_definition("trends", 1)
    taxonomy = load_taxonomy("conversation", 1, expected_digest=TAXONOMY_DIGEST)
    for issue in definition.issues:
        assert issue.status is IssueStatus.ACTIVE
        for reference in issue.match:
            label = taxonomy.label(reference.dimension, reference.label)
            assert label is not None and label.status.value == "active"
    # No parallel label: every issue's references are taxonomy labels, never new ids.
    intent = {label.id for label in taxonomy.dimension("intent").labels}
    assert {i.match[0].label for i in definition.active_issues("call_reason")} == intent
    complaint = definition.issue("complaint_refund")
    assert complaint is not None
    assert [(r.dimension, r.label) for r in complaint.match] == [
        ("intent", "report_dissatisfaction"),
        ("topic", "refund"),
    ]
    resolved = [i for i in definition.issues if any(r.label == "resolved" for r in i.match)]
    assert resolved == []


def test_service_area_ids_are_the_active_topics() -> None:
    definition = load_definition("trends", 1)
    topics = tuple(label.id for label in load_taxonomy("conversation", 1).dimension("topic").labels)
    assert service_area_ids(definition) == topics


# --- load_definition refusals --------------------------------------------------------------


@pytest.mark.parametrize(
    ("definition_id", "version"),
    [
        ("trends", 2),
        ("trends", 0),
        ("trends", -1),
        ("trends", True),
        ("Trends", 1),
        ("x", 1),
        ("../trends", 1),
        (None, 1),
        ("trends", "1"),
    ],
)
def test_unknown_definitions_are_refused(definition_id: object, version: object) -> None:
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition(definition_id, version)  # type: ignore[arg-type]
    assert caught.value.code is DefinitionErrorCode.UNKNOWN


def test_released_versions_of_a_bad_id_is_empty() -> None:
    assert released_versions("nope") == ()
    assert released_versions("../x") == ()
    assert released_versions(None) == ()  # type: ignore[arg-type]


def test_expected_digest_mismatch_is_refused() -> None:
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition("trends", 1, expected_digest="0" * 64)
    assert caught.value.code is DefinitionErrorCode.DIGEST_MISMATCH


# --- load_definition_file: allow control ---------------------------------------------------


def test_the_released_document_loads_from_any_folder(tmp_path: Path) -> None:
    definition = load_definition_file(_write(tmp_path, _document()))
    assert definition.digest == RELEASED_DEFINITION_DIGESTS[("trends", 1)]


def test_digest_ignores_formatting_but_not_meaning(tmp_path: Path) -> None:
    document = _document()
    compact = tmp_path / "compact"
    compact.mkdir()
    path = compact / "trends.v1.json"
    path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    assert load_definition_file(path).digest == RELEASED_DEFINITION_DIGESTS[("trends", 1)]
    changed = copy.deepcopy(document)
    changed["notes"] = "another note"
    assert (
        load_definition_file(_write(tmp_path, changed)).digest
        != (RELEASED_DEFINITION_DIGESTS[("trends", 1)])
    )


def test_a_file_name_without_a_version_is_accepted(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(), name="candidate.json")
    assert load_definition_file(path).version == 1


# --- load_definition_file: deny cases, one code each ---------------------------------------


def test_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition_file(tmp_path / "missing.v1.json")
    assert caught.value.code is DefinitionErrorCode.UNREADABLE
    bad = tmp_path / "trends.v1.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition_file(bad)
    assert caught.value.code is DefinitionErrorCode.UNREADABLE
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition_file(bad)
    assert caught.value.code is DefinitionErrorCode.UNREADABLE


def test_duplicate_json_key_is_refused(tmp_path: Path) -> None:
    text = RELEASED.read_text(encoding="utf-8").replace(
        '"version": 1,', '"version": 1,\n  "version": 1,', 1
    )
    path = tmp_path / "trends.v1.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TrendDefinitionError) as caught:
        load_definition_file(path)
    assert caught.value.code is DefinitionErrorCode.DUPLICATE_KEY


@pytest.mark.parametrize(
    "where",
    ["top", "taxonomy", "policy", "family", "issue", "reference"],
)
def test_unknown_keys_are_refused_at_every_level(tmp_path: Path, where: str) -> None:
    document = _document()
    target = {
        "top": document,
        "taxonomy": document["taxonomy"],
        "policy": document["extraction_policies"][0],
        "family": document["families"][0],
        "issue": document["issues"][0],
        "reference": document["issues"][0]["match"][0],
    }[where]
    target["estimated_nps"] = 1
    assert _refused(tmp_path, document) is DefinitionErrorCode.UNKNOWN_KEY


def test_unknown_key_wins_over_a_wrong_schema(tmp_path: Path) -> None:
    document = _document()
    document["schema"] = "homayar.other/1"
    document["extra"] = True
    assert _refused(tmp_path, document) is DefinitionErrorCode.UNKNOWN_KEY


@pytest.mark.parametrize(
    "bad_id", ["Intent_x", "x", "1abc", "has-dash", "a" * 65, "counted", "missing", "none"]
)
def test_bad_issue_ids_are_refused(tmp_path: Path, bad_id: str) -> None:
    document = _document()
    document["issues"][0]["id"] = bad_id
    assert _refused(tmp_path, document) is DefinitionErrorCode.BAD_ID


def test_bad_family_and_retired_ids_are_refused(tmp_path: Path) -> None:
    document = _document()
    document["families"][3]["id"] = "QA"
    assert _refused(tmp_path, document) is DefinitionErrorCode.BAD_ID
    document = _document()
    document["retired_issue_ids"] = ["Bad-Id"]
    assert _refused(tmp_path, document) is DefinitionErrorCode.BAD_ID


def test_every_reserved_word_is_a_bad_id(tmp_path: Path) -> None:
    for word in sorted(RESERVED_IDS):
        document = _document()
        document["issues"][0]["id"] = word
        assert _refused(tmp_path, document) is DefinitionErrorCode.BAD_ID, word


def _mutations() -> dict[str, object]:
    def wrong_schema(d: dict) -> None:
        d["schema"] = "homayar.issue-trends/2"

    def missing_key(d: dict) -> None:
        del d["retired_issue_ids"]

    def missing_family_key(d: dict) -> None:
        del d["families"][0]["source_state"]

    def version_zero(d: dict) -> None:
        d["version"] = 0

    def version_bool(d: dict) -> None:
        d["version"] = True

    def version_string(d: dict) -> None:
        d["version"] = "1"

    def bad_definition_id(d: dict) -> None:
        d["definition_id"] = "Trends"

    def wrong_file_name_version(d: dict) -> None:
        d["version"] = 2

    def empty_name(d: dict) -> None:
        d["issues"][0]["name_en"] = ""

    def long_name(d: dict) -> None:
        d["issues"][0]["name_fa"] = "ش" * 81

    def two_line_definition(d: dict) -> None:
        d["families"][0]["definition"] = "line one\nline two"

    def bad_source(d: dict) -> None:
        d["families"][0]["source"] = "crm"

    def bad_status(d: dict) -> None:
        d["issues"][0]["status"] = "retired"

    def available_family_with_reason(d: dict) -> None:
        d["families"][0]["source_reason"] = "scorecard_results_unavailable"

    def unavailable_family_without_reason(d: dict) -> None:
        d["families"][3]["source_reason"] = None

    def unavailable_family_with_issue(d: dict) -> None:
        issue = copy.deepcopy(d["issues"][0])
        issue.update(id="qa_greeting", family="qa", source="scorecard")
        d["issues"].append(issue)

    def available_family_without_issue(d: dict) -> None:
        d["issues"] = [i for i in d["issues"] if i["family"] != "unresolved_outcome"]

    def available_family_only_deprecated(d: dict) -> None:
        for issue in d["issues"]:
            if issue["family"] == "unresolved_outcome":
                issue["status"] = "deprecated"

    def unknown_family(d: dict) -> None:
        d["issues"][0]["family"] = "nope"

    def wrong_issue_source(d: dict) -> None:
        d["issues"][0]["source"] = "scorecard"

    def no_reference(d: dict) -> None:
        d["issues"][0]["match"] = []

    def too_many_references(d: dict) -> None:
        d["issues"][0]["match"] = [
            {"dimension": "intent", "label": "request_support"},
            {"dimension": "topic", "label": "refund"},
            {"dimension": "topic", "label": "invoice"},
            {"dimension": "topic", "label": "payment"},
            {"dimension": "outcome", "label": "unresolved"},
        ]

    def entity_reference(d: dict) -> None:
        d["issues"][0]["match"] = [{"dimension": "entity", "label": "phone"}]

    def two_single_valued_references(d: dict) -> None:
        d["issues"][0]["match"] = [
            {"dimension": "intent", "label": "request_support"},
            {"dimension": "intent", "label": "purchase_inquiry"},
        ]

    def same_reference_twice(d: dict) -> None:
        d["issues"][-1]["match"] = [
            {"dimension": "intent", "label": "report_dissatisfaction"},
            {"dimension": "topic", "label": "technical_fault"},
            {"dimension": "topic", "label": "technical_fault"},
        ]

    def same_reference_set(d: dict) -> None:
        d["issues"][1]["match"] = copy.deepcopy(d["issues"][0]["match"])

    def bad_taxonomy_digest_format(d: dict) -> None:
        d["taxonomy"]["taxonomy_digest"] = "short"

    def no_policies(d: dict) -> None:
        d["extraction_policies"] = []

    def no_validators(d: dict) -> None:
        d["validator_versions"] = []

    return {name: fn for name, fn in locals().items() if callable(fn)}


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_rule_defects_are_definition_invalid(tmp_path: Path, name: str) -> None:
    document = _document()
    _mutations()[name](document)  # type: ignore[operator]
    assert _refused(tmp_path, document) is DefinitionErrorCode.INVALID


def test_a_document_that_is_not_an_object_is_invalid(tmp_path: Path) -> None:
    assert _refused(tmp_path, [1, 2]) is DefinitionErrorCode.INVALID
    assert _refused(tmp_path, "trends") is DefinitionErrorCode.INVALID


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    document = _document()
    document["issues"][1]["id"] = document["issues"][0]["id"]
    document["issues"][1]["match"] = [{"dimension": "intent", "label": "report_dissatisfaction"}]
    document["issues"][0]["match"] = [{"dimension": "intent", "label": "request_support"}]
    assert _refused(tmp_path, document) is DefinitionErrorCode.DUPLICATE_ID
    document = _document()
    document["issues"][0]["id"] = "call_reason"  # families and issues share one id space
    assert _refused(tmp_path, document) is DefinitionErrorCode.DUPLICATE_ID
    document = _document()
    document["retired_issue_ids"] = ["intent_unsolicited"]
    assert _refused(tmp_path, document) is DefinitionErrorCode.DUPLICATE_ID
    document = _document()
    family = copy.deepcopy(document["families"][3])
    document["families"].append(family)
    assert _refused(tmp_path, document) is DefinitionErrorCode.DUPLICATE_ID


def test_a_retired_issue_id_that_is_not_used_is_accepted(tmp_path: Path) -> None:
    document = _document()
    document["retired_issue_ids"] = ["intent_old_reason"]
    assert load_definition_file(_write(tmp_path, document)).retired_issue_ids == (
        "intent_old_reason",
    )


def test_a_deprecated_issue_is_kept_but_not_active(tmp_path: Path) -> None:
    document = _document()
    _issue(document, "intent_unsolicited")["status"] = "deprecated"
    definition = load_definition_file(_write(tmp_path, document))
    assert definition.issue("intent_unsolicited") is not None
    assert "intent_unsolicited" not in [i.id for i in definition.active_issues()]
    assert "intent_unsolicited" not in [i.id for i in definition.active_issues("call_reason")]


def test_taxonomy_references(tmp_path: Path) -> None:
    def code_for(mutate) -> DefinitionErrorCode:
        document = _document()
        mutate(document)
        return _refused(tmp_path, document)

    def taxonomy_version(d: dict) -> None:
        d["taxonomy"]["taxonomy_version"] = 9

    def taxonomy_id(d: dict) -> None:
        d["taxonomy"]["taxonomy_id"] = "other"

    def taxonomy_digest(d: dict) -> None:
        d["taxonomy"]["taxonomy_digest"] = "0" * 64

    def policy_version(d: dict) -> None:
        d["extraction_policies"][0]["policy_version"] = "extraction-policy/2"

    def policy_digest_(d: dict) -> None:
        d["extraction_policies"][0]["policy_digest"] = "0" * 64

    def validator(d: dict) -> None:
        d["validator_versions"] = ["analysis-taxonomy-validator/2"]

    def unknown_label(d: dict) -> None:
        _issue(d, "intent_request_support")["match"][0]["label"] = "product_defect"

    def wrong_dimension(d: dict) -> None:
        _issue(d, "intent_request_support")["match"][0] = {
            "dimension": "topic",
            "label": "resolved",
        }

    def wrong_dimension_intent(d: dict) -> None:
        _issue(d, "intent_request_support")["match"][0]["label"] = "refund"

    assert code_for(taxonomy_version) is DefinitionErrorCode.TAXONOMY_UNKNOWN
    assert code_for(taxonomy_id) is DefinitionErrorCode.TAXONOMY_UNKNOWN
    assert code_for(taxonomy_digest) is DefinitionErrorCode.TAXONOMY_DIGEST_MISMATCH
    assert code_for(policy_version) is DefinitionErrorCode.VERSION_MISMATCH
    assert code_for(policy_digest_) is DefinitionErrorCode.VERSION_MISMATCH
    assert code_for(validator) is DefinitionErrorCode.VERSION_MISMATCH
    assert code_for(unknown_label) is DefinitionErrorCode.UNKNOWN_LABEL
    assert code_for(wrong_dimension) is DefinitionErrorCode.WRONG_DIMENSION
    assert code_for(wrong_dimension_intent) is DefinitionErrorCode.WRONG_DIMENSION


def _patched_taxonomy(monkeypatch: pytest.MonkeyPatch, mutate) -> None:
    """Load the definition against a changed copy of the taxonomy (deprecated or retired
    labels do not exist in the released taxonomy v1)."""
    import backend.analysis_taxonomy as module

    real = load_taxonomy("conversation", 1)
    data = real.model_dump(mode="json", by_alias=True)
    mutate(data)
    changed = type(real).model_validate(data)

    def fake(taxonomy_id: str, version: int, *, expected_digest: str | None = None):
        return changed

    monkeypatch.setattr(module, "load_taxonomy", fake)


def test_deprecated_label_reference_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deprecate(data: dict) -> None:
        for label in data["dimensions"]["intent"]["labels"]:
            if label["id"] == "unsolicited":
                label["status"] = "deprecated"

    _patched_taxonomy(monkeypatch, deprecate)
    assert _refused(tmp_path, _document()) is DefinitionErrorCode.INACTIVE_LABEL


def test_retired_label_reference_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def retire(data: dict) -> None:
        intent = data["dimensions"]["intent"]
        intent["labels"] = [label for label in intent["labels"] if label["id"] != "unsolicited"]
        intent["retired_label_ids"] = ["unsolicited"]

    _patched_taxonomy(monkeypatch, retire)
    assert _refused(tmp_path, _document()) is DefinitionErrorCode.RETIRED_LABEL


def test_the_taxonomy_is_looked_up_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C5: the loader calls backend.analysis_taxonomy.load_taxonomy when it runs, so pointing
    the taxonomy package at another definitions folder changes what it checks against."""
    import backend.analysis_taxonomy.models as models

    folder = tmp_path / "taxonomy"
    folder.mkdir()
    data = json.loads((models.DEFINITIONS / "conversation.v1.json").read_text(encoding="utf-8"))
    for label in data["dimensions"]["topic"]["labels"]:
        if label["id"] == "refund":
            label["status"] = "deprecated"
    (folder / "conversation.v1.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(models, "DEFINITIONS", folder)
    models._load_released.cache_clear()
    try:
        document = _document()
        document["taxonomy"]["taxonomy_digest"] = models.load_taxonomy("conversation", 1).digest
        assert _refused(tmp_path, document) is DefinitionErrorCode.INACTIVE_LABEL
    finally:
        models._load_released.cache_clear()


def test_a_later_version_file_loads_when_its_content_matches_its_name(tmp_path: Path) -> None:
    """C6: the file name must match the content; no released version is required."""
    document = _document()
    document["version"] = 2
    definition = load_definition_file(_write(tmp_path, document, name="trends.v2.json"))
    assert definition.version == 2
    assert _refused(tmp_path, document) is DefinitionErrorCode.INVALID  # named trends.v1.json


def test_loading_never_changes_the_released_file() -> None:
    before = RELEASED.read_bytes()
    load_definition("trends", 1)
    load_definition_file(RELEASED)
    assert RELEASED.read_bytes() == before


def test_a_definition_is_immutable() -> None:
    definition = load_definition("trends", 1)
    with pytest.raises(ValidationError):
        definition.version = 2  # type: ignore[misc]
    assert isinstance(definition, TrendDefinition)


# --- purity ---------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = (
    "sqlalchemy",
    "backend.database",
    "backend.services",
    "backend.routers",
    "backend.providers",
    "httpx",
    "requests",
    "socket",
    "time",
    "random",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            # ``from backend import analysis_taxonomy`` imports the package too.
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_import_check_sees_a_package_imported_from_its_parent(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text("from backend import analysis_taxonomy, services\n", encoding="utf-8")
    assert {"backend.analysis_taxonomy", "backend.services"} <= _imports(path)


def test_the_package_is_pure() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        for name in _imports(path):
            assert not any(
                name == bad or name.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS
            ), f"{path.name} imports {name}"


def test_only_definition_imports_the_taxonomy_package() -> None:
    users = sorted(
        path.name
        for path in PACKAGE.glob("*.py")
        if any(name.startswith("backend.analysis_taxonomy") for name in _imports(path))
    )
    assert users == ["definition.py"]
