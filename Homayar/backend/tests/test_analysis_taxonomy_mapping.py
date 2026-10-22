"""Voicemail and speech ids map onto the conversation taxonomy (HOM-52, D8).

Voicemail and speech code are not changed: this test reads their tables and fails when
one of them gains an id that is neither a taxonomy id nor in ``EXTERNAL_ID_MAP``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.analysis_taxonomy import (
    ABSTAIN_VALUES,
    DIMENSIONS,
    EXTERNAL_ID_MAP,
    FieldStatus,
    load_taxonomy,
)
from backend.speech.entities import _PII_MAP
from backend.voicemail.classify import CATEGORY_INTENT, TOPIC_RULES

BACKEND = Path(__file__).resolve().parent.parent
TAXONOMY = load_taxonomy("conversation", 1)
# The only backend modules that use the package (HOM-53): the provider asks for the
# extraction, the analysis stage judges it.  Any other user fails the test below.
PACKAGE_USERS = frozenset(
    {
        Path("providers/openai_compatible.py"),
        Path("services/analysis_extraction.py"),
    }
)

SPEECH_ENTITY_TYPES = frozenset(
    {
        "phone",
        "national_id",
        "email",
        "amount",
        "date",
        "date_range",
        "time",
        "reference_id",
        "person_name_slot",
    }
)
SOURCE_DIMENSION = {
    "voicemail.intent": "intent",
    "voicemail.topic": "topic",
    "speech.entity": "entity",
}


def _ids(dimension: str) -> set[str]:
    return {label.id for label in TAXONOMY.dimension(dimension).labels}


def _resolves(source: str, external_id: str) -> bool:
    dimension = SOURCE_DIMENSION[source]
    if external_id in _ids(dimension):
        return True
    target = EXTERNAL_ID_MAP.get((source, external_id))
    if target is None:
        return False
    return target in _ids(dimension) or target in {
        status.value for status in TAXONOMY.dimension(dimension).abstain_values
    }


def _speech_types_in_source() -> set[str]:
    entities = (BACKEND / "speech" / "entities.py").read_text(encoding="utf-8")
    found = {etype for etype, _ in _PII_MAP.values()}
    for node in ast.walk(ast.parse(entities)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Entity"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found.add(node.args[0].value)
    # The temporal kinds and the full list are only written as comments.
    dates = (BACKEND / "speech" / "dates.py").read_text(encoding="utf-8")
    kind_comment = re.search(r"kind: str\s+#\s*(.+)", dates)
    assert kind_comment is not None
    found |= set(re.findall(r'"([a-z_]+)"', kind_comment.group(1)))
    return found


def test_speech_entity_types_are_pinned_from_the_source() -> None:
    entities = (BACKEND / "speech" / "entities.py").read_text(encoding="utf-8")
    comment = re.search(r"#\s*([a-z_]+(?:\|[a-z_]+)+)\n\s+type: str", entities)
    assert comment is not None
    assert set(comment.group(1).split("|")) == SPEECH_ENTITY_TYPES
    assert _speech_types_in_source() <= SPEECH_ENTITY_TYPES


def test_every_voicemail_intent_is_a_taxonomy_intent_or_mapped() -> None:
    for intent in CATEGORY_INTENT.values():
        assert _resolves("voicemail.intent", intent), intent


def test_voicemail_unknown_maps_to_an_abstain_value() -> None:
    assert "unknown" in CATEGORY_INTENT.values()
    assert "unknown" not in _ids("intent")
    target = EXTERNAL_ID_MAP[("voicemail.intent", "unknown")]
    assert target == FieldStatus.INSUFFICIENT_EVIDENCE.value
    assert FieldStatus(target) in ABSTAIN_VALUES


def test_every_voicemail_topic_is_a_taxonomy_topic_or_mapped() -> None:
    for topic in TOPIC_RULES:
        assert _resolves("voicemail.topic", topic), topic


def test_every_speech_entity_type_is_a_taxonomy_entity_or_mapped() -> None:
    for etype in SPEECH_ENTITY_TYPES | _speech_types_in_source():
        assert _resolves("speech.entity", etype), etype
    assert EXTERNAL_ID_MAP[("speech.entity", "person_name_slot")] == "person_name"


def test_every_map_entry_has_a_known_source_and_a_real_target() -> None:
    for source, external_id in EXTERNAL_ID_MAP:
        assert source in SOURCE_DIMENSION, source
        assert SOURCE_DIMENSION[source] in DIMENSIONS
        assert external_id not in _ids(SOURCE_DIMENSION[source]), "same ids are not listed"
        assert _resolves(source, external_id)


def test_no_existing_module_imports_the_package() -> None:
    package = BACKEND / "analysis_taxonomy"
    tests = BACKEND / "tests"
    users = [
        path.relative_to(BACKEND)
        for path in BACKEND.rglob("*.py")
        if package not in path.parents
        and tests not in path.parents
        and ".venv" not in path.parts
        and "analysis_taxonomy" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert sorted(users) == sorted(PACKAGE_USERS)
