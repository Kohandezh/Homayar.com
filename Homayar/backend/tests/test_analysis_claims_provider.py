"""HOM-61 (WP12-T04): structured, abstaining analysis claims at the provider.

The provider returns summary fields, decisions and key moments. A detail the
call does not state is ``not_stated`` with no text and no evidence; a stated
claim carries text, a basis (explicit or inferred) and its source turns. The
OpenAI-compatible parser rejects any other shape as
``intelligence_invalid_response``. No test reaches a network: the adapter runs
on an in-memory transport.
"""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.analysis_taxonomy import Basis, FieldStatus
from backend.jobs.checkpoints import ARTIFACT_SCHEMAS
from backend.jobs.states import StageName
from backend.providers import egress
from backend.providers.base import ProviderError
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.openai_compatible import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    OpenAICompatibleIntelligenceProvider,
)
from backend.providers.schemas import (
    SUMMARY_FIELD_NAMES,
    ClaimBasis,
    ClaimStatus,
    Decision,
    DecisionDueDate,
    DecisionOwner,
    IntelligenceRequest,
    IntelligenceResult,
    KeyMoment,
    SummaryField,
    SummaryFields,
    TranscriptTurn,
)

ENDPOINT = "https://llm.example.test/v1/chat/completions"
REQUEST = IntelligenceRequest(
    language="fa",
    turns=[
        TranscriptTurn(text="قیمت نهایی را فردا اعلام می‌کنم", speaker_label="speaker_1"),
        TranscriptTurn(text="جلسهٔ بعد سه‌شنبه باشد", speaker_label="speaker_2"),
    ],
)
NOT_STATED = {"status": "not_stated", "text": None, "basis": None, "source_turn_indexes": []}
VALID_OUTPUT = {
    "summary": "Price tomorrow, next meeting on Tuesday.",
    "summary_source_turn_indexes": [0, 1],
    "action_items": [],
    "insights": [],
    "summary_fields": {
        "purpose": {
            "status": "stated",
            "text": "Agree on the final price.",
            "basis": "explicit",
            "source_turn_indexes": [0],
        },
        "resolution": NOT_STATED,
        "next_steps": {
            "status": "stated",
            "text": "Meet again on Tuesday.",
            "basis": "inferred",
            "source_turn_indexes": [1],
        },
    },
    "decisions": [
        {
            "description": "The next meeting is on Tuesday.",
            "basis": "explicit",
            "owner": {"status": "stated", "value": "speaker_2"},
            "due_date": {"status": "not_stated", "value": None},
            "source_turn_indexes": [1],
        }
    ],
    "key_moments": [
        {
            "kind": "commitment",
            "description": "The seller commits to send the price.",
            "source_turn_indexes": [0],
        }
    ],
}


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _analyze(output: object) -> IntelligenceResult:
    content = output if isinstance(output, str) else json.dumps(output)
    adapter = OpenAICompatibleIntelligenceProvider(
        base_url="https://llm.example.test/v1",
        model="claims-llm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _completion(content))),
    )
    with egress.permit(ENDPOINT):
        return asyncio.run(adapter.analyze(REQUEST))


def _changed(path: tuple, value: object) -> dict:
    """VALID_OUTPUT with one value replaced; every other key stays valid."""
    output = copy.deepcopy(VALID_OUTPUT)
    target = output
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return output


# --- parser ----------------------------------------------------------------------


def test_parser_accepts_a_well_formed_structured_payload():
    result = _analyze(VALID_OUTPUT)
    fields = result.summary_fields
    assert fields is not None
    assert fields.purpose.status == ClaimStatus.STATED
    assert fields.purpose.text == "Agree on the final price."
    assert fields.purpose.basis == ClaimBasis.EXPLICIT
    assert fields.resolution.status == ClaimStatus.NOT_STATED
    assert fields.resolution.text is None and fields.resolution.source_turn_indexes == []
    assert fields.next_steps.basis == ClaimBasis.INFERRED
    (decision,) = result.decisions
    assert decision.basis == ClaimBasis.EXPLICIT
    assert decision.owner.status == ClaimStatus.STATED and decision.owner.value == "speaker_2"
    assert decision.due_date.status == ClaimStatus.NOT_STATED and decision.due_date.value is None
    (moment,) = result.key_moments
    assert moment.kind == "commitment" and moment.source_turn_indexes == [0]


def test_parser_accepts_a_stated_claim_without_turns_and_with_out_of_range_turns():
    # Evidence quality is judged later (resolve and cite); the parser only
    # enforces the shape, so the report can show the claim as unsupported.
    output = _changed(("decisions", 0, "source_turn_indexes"), [])
    output["key_moments"][0]["source_turn_indexes"] = [99]
    result = _analyze(output)
    assert result.decisions[0].source_turn_indexes == []
    assert result.key_moments[0].source_turn_indexes == [99]


@pytest.mark.parametrize("key", ["summary_fields", "decisions", "key_moments"])
def test_parser_rejects_a_missing_new_key(key):
    output = {name: value for name, value in VALID_OUTPUT.items() if name != key}
    with pytest.raises(ProviderError) as refused:
        _analyze(output)
    assert refused.value.code == "intelligence_invalid_response"
    assert refused.value.retryable is False


REJECTED = [
    pytest.param(("summary_fields", "purpose", "text"), None, id="stated-field-without-text"),
    pytest.param(("summary_fields", "purpose", "text"), "  ", id="stated-field-blank-text"),
    pytest.param(("summary_fields", "purpose", "basis"), None, id="stated-field-without-basis"),
    pytest.param(
        ("summary_fields", "resolution", "text"), "Guessed.", id="not-stated-field-with-text"
    ),
    pytest.param(
        ("summary_fields", "resolution", "source_turn_indexes"),
        [0],
        id="not-stated-field-with-evidence",
    ),
    pytest.param(
        ("summary_fields", "resolution", "basis"), "inferred", id="not-stated-field-with-basis"
    ),
    pytest.param(("summary_fields", "purpose", "status"), "maybe", id="unknown-status"),
    pytest.param(("decisions", 0, "basis"), "guessed", id="unknown-basis"),
    pytest.param(("decisions", 0, "owner", "value"), None, id="stated-owner-without-value"),
    pytest.param(
        ("decisions", 0, "due_date", "value"), "2026-11-03", id="not-stated-date-with-value"
    ),
    pytest.param(("decisions", 0, "description"), "", id="empty-decision"),
    pytest.param(("key_moments", 0, "kind"), "", id="empty-moment-kind"),
    pytest.param(("key_moments", 0, "source_turn_indexes"), [-1], id="negative-moment-index"),
    pytest.param(("summary_fields", "extra"), NOT_STATED, id="extra-summary-field"),
]


@pytest.mark.parametrize(("path", "value"), REJECTED)
def test_parser_rejects_a_malformed_claim(path, value):
    output = _changed(path, value)
    with pytest.raises(ProviderError) as refused:
        _analyze(output)
    assert refused.value.code == "intelligence_invalid_response"


def test_the_rejection_comes_from_the_field_under_test():
    # The allow-control of every rejection above: the same payload with the
    # field under test valid is accepted.
    assert _analyze(_changed(("summary_fields", "resolution", "text"), None)).summary_fields
    assert _analyze(_changed(("decisions", 0, "owner", "value"), "speaker_1")).decisions


@pytest.mark.parametrize(
    "path",
    [
        ("summary_fields", "resolution", "status"),
        ("decisions", 0, "due_date", "status"),
    ],
)
def test_parser_accepts_only_stated_and_not_stated_of_the_taxonomy(path):
    # The taxonomy (HOM-52) also has insufficient_evidence and contradictory;
    # a HOM-61 claim does not accept them.
    assert _analyze(_changed(path, "not_stated")).summary_fields is not None
    with pytest.raises(ProviderError) as refused:
        _analyze(_changed(path, "insufficient_evidence"))
    assert refused.value.code == "intelligence_invalid_response"


def test_claim_enums_keep_the_values_of_the_hom52_taxonomy():
    # Our own enums until HOM-53 unifies them; this keeps them from drifting.
    assert {status.value for status in ClaimStatus} == {
        FieldStatus.STATED.value,
        FieldStatus.NOT_STATED.value,
    }
    assert {basis.value for basis in ClaimBasis} == {basis.value for basis in Basis}


@pytest.mark.parametrize("status", ["insufficient_evidence", "contradictory"])
def test_claim_models_refuse_the_other_taxonomy_statuses(status):
    assert SummaryField(status="not_stated").status == ClaimStatus.NOT_STATED  # control
    for model in (SummaryField, DecisionOwner, DecisionDueDate):
        with pytest.raises(ValidationError):
            model(status=status)


# --- models ----------------------------------------------------------------------


def test_summary_field_validators():
    assert SummaryField(status="not_stated").text is None
    stated = SummaryField(status="stated", text="x", basis="explicit", source_turn_indexes=[0])
    assert stated.basis == ClaimBasis.EXPLICIT
    for bad in (
        {"status": "stated", "basis": "explicit"},
        {"status": "stated", "text": "x"},
        {"status": "not_stated", "text": "x"},
        {"status": "not_stated", "basis": "inferred"},
        {"status": "not_stated", "source_turn_indexes": [0]},
        {"status": "stated", "text": "x", "basis": "explicit", "source_turn_indexes": [-1]},
    ):
        with pytest.raises(ValidationError):
            SummaryField(**bad)


def test_decision_details_default_to_not_stated():
    decision = Decision(description="d", basis="inferred")
    assert decision.owner == DecisionOwner(status="not_stated")
    assert decision.due_date == DecisionDueDate(status="not_stated")
    assert DecisionDueDate(status="stated", value="2026-11-03").value.isoformat() == "2026-11-03"
    with pytest.raises(ValidationError):
        DecisionOwner(status="not_stated", value="someone")
    with pytest.raises(ValidationError):
        DecisionDueDate(status="stated")
    with pytest.raises(ValidationError):
        Decision(description="d")  # basis is required


def test_decisions_and_key_moments_need_summary_fields():
    base = {"provider": "p", "model": "m", "summary": "s"}
    with pytest.raises(ValidationError):
        IntelligenceResult(**base, decisions=[Decision(description="d", basis="explicit")])
    with pytest.raises(ValidationError):
        IntelligenceResult(**base, key_moments=[KeyMoment(kind="k", description="d")])
    fields = SummaryFields(
        **{name: SummaryField(status="not_stated") for name in SUMMARY_FIELD_NAMES}
    )
    result = IntelligenceResult(
        **base,
        summary_fields=fields,
        decisions=[Decision(description="d", basis="explicit")],
        key_moments=[KeyMoment(kind="k", description="d")],
    )
    assert len(result.decisions) == len(result.key_moments) == 1


def test_a_schema_2_intelligence_artifact_still_validates():
    legacy = {
        "provider": "openai_compatible",
        "model": "m",
        "summary": "s",
        "summary_source_turn_indexes": [0],
        "action_items": [],
        "insights": [],
        "issue": None,
        "is_mock": False,
        "usage": None,
    }
    result = IntelligenceResult.model_validate_json(json.dumps(legacy))
    assert result.summary_fields is None
    assert result.decisions == [] and result.key_moments == []


# --- versions, prompt, mock -------------------------------------------------------


def test_versions_are_bumped():
    assert PROMPT_VERSION == 3
    assert ARTIFACT_SCHEMAS[StageName.INTELLIGENCE] == (IntelligenceResult, 3)


def test_prompt_asks_for_abstention_basis_evidence_and_no_severe_allegations():
    assert "not_stated" in SYSTEM_PROMPT and "never guess" in SYSTEM_PROMPT
    assert "'explicit'" in SYSTEM_PROMPT and "'inferred'" in SYSTEM_PROMPT
    assert "source_turn_indexes" in SYSTEM_PROMPT
    assert "Never allege misconduct" in SYSTEM_PROMPT
    for name in SUMMARY_FIELD_NAMES:
        assert name in SYSTEM_PROMPT


def test_mock_provider_abstains_on_every_structured_claim():
    provider = MockIntelligenceProvider(allow_mock=True)
    result = asyncio.run(provider.analyze(REQUEST))
    assert IntelligenceResult.model_validate(result.model_dump()) == result
    assert result.is_mock is True
    assert result.summary_fields is not None
    for name in SUMMARY_FIELD_NAMES:
        field = getattr(result.summary_fields, name)
        assert field.status == ClaimStatus.NOT_STATED
        assert field.text is None and field.basis is None and field.source_turn_indexes == []
    assert result.decisions == [] and result.key_moments == []
    # Unchanged mock behavior: the transcript is the summary, grounded in every turn.
    assert result.summary_source_turn_indexes == [0, 1]
