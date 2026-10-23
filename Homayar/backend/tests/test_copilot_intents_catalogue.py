"""Copilot intent catalogue: loader, deny codes, digest pins, purity (HOM-92)."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from backend.copilot_intents import (
    CATALOGUE_SCHEMA,
    CATALOGUE_VERSION,
    COMPONENTS,
    COVERAGE_VERSION,
    RELEASED_CATALOGUE_DIGESTS,
    CatalogueError,
    CatalogueErrorCode,
    Gate,
    IntentCatalogue,
    IntentDefinition,
    ParamName,
    RefusalReason,
    Route,
    ScopeRule,
    Target,
    catalogue_digest,
    load_catalogue,
    load_catalogue_file,
)
from backend.issue_trends import FilterKey, load_definition

BACKEND = Path(__file__).resolve().parent.parent
PACKAGE = BACKEND / "copilot_intents"
DEFINITIONS = PACKAGE / "definitions"
RELEASED = DEFINITIONS / "intents.v1.json"
README = PACKAGE / "README.md"

# A released file never changes: a change is a new version file with its own pin.
PINNED_DIGESTS = {
    ("intents", 1): "2cd7d4d41a4afb7471aa31d2c95996006404ba1a351914efd2b8e31f817c367e",
}
PINNED_CATALOGUE_VERSION = "copilot-intents/1"
PINNED_SCHEMA = "homayar.copilot-intents/1"

REQUIRED_ROUTES = {
    "issue_count": Route.SQL_AGGREGATE,
    "issue_rate": Route.SQL_AGGREGATE,
    "issue_trend": Route.SQL_AGGREGATE,
    "cohort_comparison": Route.SQL_AGGREGATE,
    "coverage_status": Route.SQL_AGGREGATE,
    "evidence_examples": Route.EVIDENCE_RETRIEVAL,
    "conversation_lookup": Route.EVIDENCE_RETRIEVAL,
    "out_of_scope": Route.REFUSE,
}
V1_REFUSALS = {
    "out_of_scope": RefusalReason.OUT_OF_SCOPE,
    "causal_question": RefusalReason.CAUSAL_NOT_SUPPORTED,
    "agent_ranking": RefusalReason.INDIVIDUAL_RANKING_NOT_SUPPORTED,
    "forecast": RefusalReason.FORECAST_NOT_SUPPORTED,
}


def _document() -> dict:
    return json.loads(RELEASED.read_text(encoding="utf-8"))


def _write(folder: Path, document: object, name: str = "intents.v2.json") -> Path:
    """Write ``document`` under an unreleased name by default, so only the rule under test
    can refuse it (a released name also checks the pinned digest)."""
    if (
        isinstance(document, dict)
        and name == "intents.v2.json"
        and type(document.get("version")) is int
        and document["version"] == 1
    ):
        document = {**document, "version": 2}
    path = folder / name
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _refused(tmp_path: Path, document: object) -> CatalogueErrorCode:
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(_write(tmp_path, document))
    return caught.value.code


def _intent(document: dict, intent_id: str) -> dict:
    return next(intent for intent in document["intents"] if intent["id"] == intent_id)


# --- pins ----------------------------------------------------------------------------------


def test_version_constants_are_pinned() -> None:
    assert CATALOGUE_VERSION == PINNED_CATALOGUE_VERSION
    assert CATALOGUE_SCHEMA == PINNED_SCHEMA
    assert COMPONENTS == ("HOM-93", "HOM-94", "HOM-95", "HOM-96", "HOM-97")


def test_released_files_match_their_pinned_digests() -> None:
    found = {}
    for path in sorted(DEFINITIONS.glob("*.json")):
        catalogue = load_catalogue_file(path)
        found[(catalogue.definition_id, catalogue.definition_version)] = catalogue.digest
    assert found == PINNED_DIGESTS
    assert dict(RELEASED_CATALOGUE_DIGESTS) == PINNED_DIGESTS


def test_coverage_version_matches_the_coverage_service() -> None:
    from backend.services.coverage_service import COVERAGE_VERSION as SERVICE_VERSION

    assert COVERAGE_VERSION == SERVICE_VERSION


def test_the_trends_pin_is_the_released_trends_definition() -> None:
    catalogue = load_catalogue()
    released = load_definition("trends", 1)
    assert catalogue.trends == released
    assert _document()["trends"]["digest"] == released.digest


# --- loading -------------------------------------------------------------------------------


def test_load_catalogue_defaults_and_shape() -> None:
    catalogue = load_catalogue()
    assert isinstance(catalogue, IntentCatalogue)
    assert catalogue.version == CATALOGUE_VERSION
    assert catalogue.definition_id == "intents"
    assert catalogue.definition_version == 1
    assert len(catalogue.digest) == 64
    assert int(catalogue.digest, 16) >= 0
    assert catalogue == load_catalogue("intents", 1)


def test_digest_is_stable_across_loads_and_equals_the_canonical_digest() -> None:
    first, second = load_catalogue(), load_catalogue()
    assert first.digest == second.digest
    assert first.digest == catalogue_digest(_document())
    assert first.digest == PINNED_DIGESTS[("intents", 1)]


def test_expected_digest_is_checked() -> None:
    digest = load_catalogue().digest
    assert load_catalogue(expected_digest=digest).digest == digest
    with pytest.raises(CatalogueError) as caught:
        load_catalogue(expected_digest="0" * 64)
    assert caught.value.code is CatalogueErrorCode.DIGEST_MISMATCH


def test_intents_are_sorted_and_get_works() -> None:
    catalogue = load_catalogue()
    ids = [intent.id for intent in catalogue.intents]
    assert ids == sorted(ids)
    assert isinstance(catalogue.intents, tuple)
    for intent in catalogue.intents:
        assert catalogue.get(intent.id) is intent
    assert catalogue.get("nope") is None
    assert catalogue.get("") is None
    assert catalogue.get(7) is None  # type: ignore[arg-type]


def test_required_intents_have_their_routes() -> None:
    catalogue = load_catalogue()
    for intent_id, route in REQUIRED_ROUTES.items():
        intent = catalogue.get(intent_id)
        assert intent is not None, intent_id
        assert intent.route is route


def test_every_intent_has_a_route_components_and_a_one_line_definition() -> None:
    for intent in load_catalogue().intents:
        assert isinstance(intent, IntentDefinition)
        assert isinstance(intent.route, Route)
        assert intent.route is not Route.ABSTAIN
        assert intent.components
        assert set(intent.components) <= set(COMPONENTS)
        assert list(intent.components) == sorted(set(intent.components))
        assert intent.definition and "\n" not in intent.definition


def test_every_refuse_intent_carries_a_refusal_reason() -> None:
    catalogue = load_catalogue()
    refusals = {i.id: i.reason for i in catalogue.intents if i.route is Route.REFUSE}
    assert refusals == V1_REFUSALS
    for intent in catalogue.intents:
        if intent.route is Route.REFUSE:
            assert isinstance(intent.reason, RefusalReason)
            assert intent.gate is Gate.NONE
            assert intent.filter_keys == () and intent.params == ()
        else:
            assert intent.reason is None


def test_v1_gates_targets_and_params() -> None:
    catalogue = load_catalogue()
    expected = {
        "issue_count": (Gate.COUNT, Target.SELECTOR, ()),
        "issue_rate": (Gate.CELL, Target.SELECTOR, ()),
        "issue_trend": (Gate.CELL, Target.SELECTOR, (ParamName.GRAIN,)),
        "cohort_comparison": (
            Gate.COMPARISON,
            Target.ONE_ROW,
            (ParamName.COMPARE_BY, ParamName.COMPARE_VALUES),
        ),
        "coverage_status": (Gate.NONE, Target.NONE, ()),
        "evidence_examples": (Gate.EVIDENCE, Target.SELECTOR, (ParamName.LIMIT,)),
        "conversation_lookup": (
            Gate.EVIDENCE,
            Target.NONE,
            (ParamName.CONVERSATION_ID, ParamName.QUERY),
        ),
    }
    for intent_id, (gate, target, params) in expected.items():
        intent = catalogue.get(intent_id)
        assert intent is not None
        assert (intent.gate, intent.target, intent.params) == (gate, target, params), intent_id
    every_filter = tuple(sorted(key.value for key in FilterKey))
    for intent_id in ("issue_count", "issue_rate", "issue_trend", "cohort_comparison"):
        assert catalogue.get(intent_id).filter_keys == every_filter
    assert catalogue.get("evidence_examples").filter_keys == every_filter
    assert catalogue.get("coverage_status").filter_keys == ("from", "to")
    assert catalogue.get("conversation_lookup").filter_keys == ()


def test_only_coverage_status_needs_a_whole_workspace_caller() -> None:
    catalogue = load_catalogue()
    rules = {intent.id: intent.scope_rule for intent in catalogue.intents}
    assert rules.pop("coverage_status") is ScopeRule.WHOLE_WORKSPACE
    assert set(rules.values()) == {ScopeRule.CALLER_SCOPE}


def test_the_catalogue_and_its_intents_are_frozen() -> None:
    catalogue = load_catalogue()
    with pytest.raises(AttributeError):
        catalogue.digest = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        catalogue.intents[0].route = Route.REFUSE  # type: ignore[misc]


# --- the loader refuses --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("definition_id", "version"),
    [
        ("nope", 1),
        ("intents", 2),
        ("intents", 0),
        ("intents", -1),
        ("intents", True),
        ("Intents", 1),
        ("../intents", 1),
        (None, 1),
        ("intents", "1"),
    ],
)
def test_unknown_definition_is_refused(definition_id: object, version: object) -> None:
    with pytest.raises(CatalogueError) as caught:
        load_catalogue(definition_id, version)  # type: ignore[arg-type]
    assert caught.value.code is CatalogueErrorCode.UNKNOWN


def test_a_tampered_released_file_is_refused(tmp_path: Path) -> None:
    document = _document()
    _intent(document, "issue_count")["definition"] = "How many conversations, roughly."
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(_write(tmp_path, document, name="intents.v1.json"))
    assert caught.value.code is CatalogueErrorCode.DIGEST_MISMATCH


def test_load_catalogue_refuses_a_tampered_file_in_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.copilot_intents.catalogue as module

    document = _document()
    _intent(document, "out_of_scope")["route"] = "sql_aggregate"
    _intent(document, "out_of_scope")["reason"] = None
    _intent(document, "out_of_scope")["gate"] = "none"
    _write(tmp_path, document, name="intents.v1.json")
    monkeypatch.setattr(module, "DEFINITIONS", tmp_path)
    with pytest.raises(CatalogueError) as caught:
        load_catalogue()
    assert caught.value.code is CatalogueErrorCode.DIGEST_MISMATCH


def test_a_released_version_without_a_pin_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.copilot_intents.catalogue as module

    _write(tmp_path, _document())  # intents.v2.json, a valid file with no pin
    monkeypatch.setattr(module, "DEFINITIONS", tmp_path)
    with pytest.raises(CatalogueError) as caught:
        load_catalogue("intents", 2)
    assert caught.value.code is CatalogueErrorCode.UNKNOWN


def test_formatting_does_not_change_the_digest(tmp_path: Path) -> None:
    path = tmp_path / "intents.v1.json"
    path.write_text(json.dumps(_document(), separators=(",", ":")), encoding="utf-8")
    assert load_catalogue_file(path).digest == PINNED_DIGESTS[("intents", 1)]


def test_an_unreleased_version_loads_when_its_content_matches_its_name(tmp_path: Path) -> None:
    catalogue = load_catalogue_file(_write(tmp_path, _document()))
    assert catalogue.definition_version == 2
    assert catalogue.digest != PINNED_DIGESTS[("intents", 1)]


def test_unreadable_files_are_refused(tmp_path: Path) -> None:
    missing = tmp_path / "intents.v2.json"
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(missing)
    assert caught.value.code is CatalogueErrorCode.UNREADABLE
    missing.write_bytes(b"\xff\xfe")
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(missing)
    assert caught.value.code is CatalogueErrorCode.UNREADABLE
    missing.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(missing)
    assert caught.value.code is CatalogueErrorCode.UNREADABLE


def test_a_duplicate_key_is_refused(tmp_path: Path) -> None:
    text = RELEASED.read_text(encoding="utf-8").replace(
        '"coverage_version": "coverage/1",',
        '"coverage_version": "coverage/1",\n  "coverage_version": "coverage/1",',
    )
    path = tmp_path / "intents.v1.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(path)
    assert caught.value.code is CatalogueErrorCode.DUPLICATE_KEY


def test_not_an_object_is_invalid(tmp_path: Path) -> None:
    assert _refused(tmp_path, [1, 2]) is CatalogueErrorCode.INVALID


@pytest.mark.parametrize("where", ["top", "trends", "sample_policy", "intent"])
def test_an_unknown_key_is_refused(tmp_path: Path, where: str) -> None:
    document = _document()
    target = {
        "top": document,
        "trends": document["trends"],
        "sample_policy": document["sample_policy"],
        "intent": document["intents"][0],
    }[where]
    target["extra"] = 1
    assert _refused(tmp_path, document) is CatalogueErrorCode.UNKNOWN_KEY


@pytest.mark.parametrize("bad_id", ["Issue_count", "1count", "a", "issue-count", "x" * 65, ""])
def test_a_bad_intent_id_is_refused(tmp_path: Path, bad_id: str) -> None:
    document = _document()
    document["intents"][0]["id"] = bad_id
    assert _refused(tmp_path, document) is CatalogueErrorCode.BAD_ID


def test_a_duplicate_intent_id_is_refused(tmp_path: Path) -> None:
    document = _document()
    document["intents"].insert(1, copy.deepcopy(document["intents"][0]))
    assert _refused(tmp_path, document) is CatalogueErrorCode.DUPLICATE_ID


def _broken(change) -> dict:
    document = _document()
    change(document)
    return document


def _set(intent_id: str, **fields: object):
    def change(document: dict) -> None:
        _intent(document, intent_id).update(fields)

    return change


INVALID_CHANGES = {
    "schema": lambda d: d.update(schema="homayar.copilot-intents/2"),
    "definition_id": lambda d: d.update(definition_id="other"),
    "version_bool": lambda d: d.update(version=True),
    "version_string": lambda d: d.update(version="2"),
    "notes_number": lambda d: d.update(notes=3),
    "missing_key": lambda d: d.pop("coverage_version"),
    "intents_empty": lambda d: d.update(intents=[]),
    "intents_not_list": lambda d: d.update(intents={}),
    "unsorted": lambda d: d["intents"].reverse(),
    "intent_missing_key": lambda d: d["intents"][0].pop("gate"),
    "route_abstain": _set("issue_count", route="abstain"),
    "route_unknown": _set("issue_count", route="model"),
    "refuse_without_reason": _set("out_of_scope", reason=None),
    "refuse_with_abstain_reason": _set("out_of_scope", reason="no_evidence"),
    "answer_with_reason": _set("issue_count", reason="out_of_scope"),
    "refuse_with_gate": _set("out_of_scope", gate="cell"),
    "refuse_with_filter": _set("out_of_scope", filter_keys=["from"]),
    "refuse_with_param": _set("out_of_scope", params=["limit"]),
    "refuse_with_target": _set("out_of_scope", target="selector"),
    "evidence_with_cell_gate": _set("evidence_examples", gate="cell"),
    "aggregate_with_evidence_gate": _set("issue_count", gate="evidence"),
    "gate_unknown": _set("issue_count", gate="maybe"),
    "evidence_with_count_gate": _set("evidence_examples", gate="count"),
    "comparison_gate_without_cohorts": _set("issue_rate", gate="comparison"),
    "cohorts_without_comparison_gate": _set("cohort_comparison", gate="cell"),
    "scope_rule_unknown": _set("issue_count", scope_rule="team"),
    "scope_rule_missing": lambda d: _intent(d, "issue_count").pop("scope_rule"),
    "refuse_whole_workspace": _set("forecast", scope_rule="whole_workspace"),
    "evidence_whole_workspace": _set("evidence_examples", scope_rule="whole_workspace"),
    "no_components": _set("issue_count", components=[]),
    "unknown_component": _set("issue_count", components=["HOM-80"]),
    "component_twice": _set("issue_count", components=["HOM-96", "HOM-96"]),
    "components_unsorted": _set("issue_count", components=["HOM-97", "HOM-96"]),
    "definition_two_lines": _set("issue_count", definition="one\ntwo"),
    "definition_empty": _set("issue_count", definition=""),
    "definition_too_long": _set("issue_count", definition="x" * 501),
    "unknown_filter_key": _set("issue_count", filter_keys=["from", "product"]),
    "filter_keys_unsorted": _set("coverage_status", filter_keys=["to", "from"]),
    "filter_key_twice": _set("coverage_status", filter_keys=["from", "from"]),
    "unknown_param": _set("issue_trend", params=["timezone"]),
    "param_twice": _set("issue_trend", params=["grain", "grain"]),
    "param_of_another_intent": _set("issue_rate", params=["conversation_id"]),
    "target_unknown": _set("issue_count", target="many"),
    "selector_target_without_selectors": _set("issue_count", filter_keys=["from", "team_id", "to"]),
    "trends_version_bool": lambda d: d["trends"].update(version=True),
    "trends_digest_short": lambda d: d["trends"].update(digest="abc"),
    "sample_digest_short": lambda d: d["sample_policy"].update(digest="abc"),
}


@pytest.mark.parametrize("name", sorted(INVALID_CHANGES))
def test_broken_rules_are_invalid(tmp_path: Path, name: str) -> None:
    assert _refused(tmp_path, _broken(INVALID_CHANGES[name])) is CatalogueErrorCode.INVALID


def test_content_that_does_not_match_the_file_name_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "intents.v3.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    with pytest.raises(CatalogueError) as caught:
        load_catalogue_file(path)
    assert caught.value.code is CatalogueErrorCode.INVALID


REFERENCE_CHANGES = {
    "trends_digest": lambda d: d["trends"].update(digest="0" * 64),
    "trends_version": lambda d: d["trends"].update(version=9),
    "trends_id": lambda d: d["trends"].update(definition_id="other"),
    "sample_version": lambda d: d["sample_policy"].update(version="trend-sample-policy/2"),
    "sample_digest": lambda d: d["sample_policy"].update(digest="0" * 64),
    "coverage_version": lambda d: d.update(coverage_version="coverage/2"),
}


@pytest.mark.parametrize("name", sorted(REFERENCE_CHANGES))
def test_unknown_references_are_refused(tmp_path: Path, name: str) -> None:
    code = _refused(tmp_path, _broken(REFERENCE_CHANGES[name]))
    assert code is CatalogueErrorCode.REFERENCE_MISMATCH


def test_one_defect_gives_the_first_code_in_order(tmp_path: Path) -> None:
    document = _document()
    document["extra"] = 1  # unknown key
    document["intents"][0]["id"] = "Bad"  # bad id, checked later
    document["trends"]["digest"] = "0" * 64  # reference, checked later still
    assert _refused(tmp_path, document) is CatalogueErrorCode.UNKNOWN_KEY


def test_the_loader_does_not_change_the_file(tmp_path: Path) -> None:
    before = RELEASED.read_bytes()
    load_catalogue()
    assert RELEASED.read_bytes() == before


# --- purity --------------------------------------------------------------------------------

ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "json",
    "pathlib",
    "re",
    "types",
    "typing",
    "unicodedata",
    "backend",
}
ALLOWED_BACKEND_MODULES = {"backend.issue_trends", "backend.copilot_intents"}


def _python_files() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def test_the_package_imports_only_the_standard_library_and_issue_trends() -> None:
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path.name}: use absolute imports"
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in ALLOWED_IMPORT_ROOTS, f"{path.name}: {name}"
                if name.startswith("backend"):
                    assert any(
                        name == allowed or name.startswith(allowed + ".")
                        for allowed in ALLOWED_BACKEND_MODULES
                    ), f"{path.name}: {name}"


def test_the_package_never_restates_the_sample_minimums() -> None:
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        numbers = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and type(node.value) is int
        }
        assert not numbers & {30, 50}, path.name
    text = RELEASED.read_text(encoding="utf-8")
    assert "min_denominator" not in text


def test_the_package_reads_no_clock_and_no_network() -> None:
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for word in ("datetime.now", "time.time", "utcnow", "socket", "requests", "httpx"):
            assert word not in source, f"{path.name}: {word}"


EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)


# --- the README ----------------------------------------------------------------------------


def test_the_readme_lists_every_intent_and_has_no_em_dash() -> None:
    text = README.read_text(encoding="utf-8")
    assert EM_DASH not in text and EN_DASH not in text and " -- " not in text
    for intent in load_catalogue().intents:
        assert f"| `{intent.id}` |" in text, intent.id
    for reason in RefusalReason:
        assert f"`{reason.value}`" in text, reason.value
    for component in COMPONENTS:
        assert component in text


def test_python_sources_have_no_em_dash() -> None:
    for path in [*_python_files(), *sorted((BACKEND / "tests").glob("test_copilot_intents*.py"))]:
        text = path.read_text(encoding="utf-8")
        assert EM_DASH not in text and EN_DASH not in text, path.name
