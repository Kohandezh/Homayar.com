"""WP08-T04: identical typed contract tests for local and API adapters.

Every adapter of a port runs the same checks: it returns the port's typed
result (or a typed ``ProviderError``), names itself in results exactly as its
declared identity does (so a manifest that matches the identity describes the
result), declares an identity that is deterministic and credential-free,
reports usage as a typed value or honestly not at all, and never turns a
malformed provider output into a result.  The Padyar AI Gateway has no
supplied API contract; its slot is an explicit skip, and configuring it is a
typed unconfigured state.  No test reaches a network: API adapters run on an
in-memory transport and the local Whisper adapter on an injected model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.database.saas_models import ProviderUsage
from backend.jobs.model_preflight import run_preflight
from backend.jobs.processor import process_job
from backend.providers import egress, padyar
from backend.providers.base import DiarizationProvider, ProviderError, ProviderIdentity
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.factory import (
    ProviderBundle,
    create_asr_provider,
    create_diarization_provider,
    create_intelligence_provider,
)
from backend.providers.mocks import (
    MockASRProvider,
    MockDiarizationProvider,
    MockIntelligenceProvider,
)
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    DiarizationRequest,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    IntelligenceRequest,
    IntelligenceResult,
    ProviderUsageReport,
    TranscriptTurn,
)
from backend.providers.whisper import LocalWhisperProvider
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_provider_routing import _manifest, _settings_with

API_KEY = "contract-test-key-never-sent-anywhere"
VALID_OUTPUT = {
    "summary": "The customer asked for a callback.",
    "summary_source_turn_indexes": [0],
    "action_items": [
        {
            "description": "Call the customer back",
            "owner": None,
            "due_date": None,
            "priority": "high",
            "source_turn_indexes": [0],
        }
    ],
    "insights": [
        {
            "category": "request",
            "description": "Callback requested",
            "confidence": 0.8,
            "source_turn_indexes": [0],
        }
    ],
    "summary_fields": {
        "purpose": {"status": "not_stated", "text": None, "basis": None, "source_turn_indexes": []},
        "resolution": {
            "status": "not_stated",
            "text": None,
            "basis": None,
            "source_turn_indexes": [],
        },
        "next_steps": {
            "status": "not_stated",
            "text": None,
            "basis": None,
            "source_turn_indexes": [],
        },
    },
    "decisions": [],
    "key_moments": [],
}
REQUEST = IntelligenceRequest(
    transcript_text="Please call me back tomorrow.",
    turns=[TranscriptTurn(text="Please call me back tomorrow.", speaker_label="speaker_1")],
    language="en",
    call_id="call-1",
)


def _completion(content: str, *, usage: object = None, status: int = 200) -> httpx.Response:
    envelope: dict = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        envelope["usage"] = usage
    return httpx.Response(status, json=envelope)


def _api_adapter(respond) -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(
        base_url="https://llm.example.test/v1",
        model="contract-llm-2026-09",
        api_key=API_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )


def _api_ok(request: httpx.Request) -> httpx.Response:
    return _completion(
        json.dumps(VALID_OUTPUT), usage={"prompt_tokens": 120, "completion_tokens": 45}
    )


@pytest.fixture(autouse=True)
def _approved_route():
    """Adapter-level tests stand in for the processor's routing decision: the
    API adapter may reach exactly its own endpoint (WP08-T06)."""
    with egress.permit("https://llm.example.test/v1/chat/completions"):
        yield


PADYAR = pytest.param(
    None,
    id="padyar_gateway",
    marks=pytest.mark.skip(reason="Padyar AI Gateway API contract not supplied (see padyar.py)"),
)


@pytest.fixture(
    params=[
        pytest.param(lambda: MockIntelligenceProvider(allow_mock=True), id="local_deterministic"),
        pytest.param(lambda: _api_adapter(_api_ok), id="openai_compatible"),
        PADYAR,
    ]
)
def intelligence_adapter(request):
    return request.param()


class _FakeWhisper:
    def __init__(self, segments=None) -> None:
        self.segments = segments or [
            SimpleNamespace(start=0.0, end=1.2, text=" Please call me "),
            SimpleNamespace(start=1.2, end=2.5, text="back tomorrow."),
        ]

    def transcribe(self, path: str, **kwargs):
        return iter(self.segments), SimpleNamespace(language="en", duration=2.5)


def _whisper(model=None) -> LocalWhisperProvider:
    return LocalWhisperProvider(
        model_name="contract-whisper",
        device="cpu",
        compute_type="int8",
        model=model or _FakeWhisper(),
    )


@pytest.fixture(
    params=[
        pytest.param(lambda: _whisper(), id="local_whisper"),
        pytest.param(
            lambda: MockASRProvider(allow_mock=True, transcript="Please call me back tomorrow."),
            id="local_deterministic",
        ),
        PADYAR,
    ]
)
def asr_adapter(request):
    return request.param()


@pytest.fixture(
    params=[
        pytest.param(lambda: UnavailableDiarizationProvider(), id="unavailable"),
        pytest.param(lambda: MockDiarizationProvider(allow_mock=True), id="local_deterministic"),
        pytest.param(lambda: _SpeakersFound(), id="available_double"),
        PADYAR,
    ]
)
def diarization_adapter(request):
    return request.param()


class _SpeakersFound(DiarizationProvider):
    """A diarization double that finds speakers and names itself consistently."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="contract_diarization", model="two-speakers")

    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        return DiarizationResult(
            provider="contract_diarization",
            status=DiarizationStatus.AVAILABLE,
            segments=[
                DiarizationSegment(speaker_label="speaker_1", start_seconds=0, end_seconds=1.2),
                DiarizationSegment(speaker_label="speaker_2", start_seconds=1.2, end_seconds=2.5),
            ],
            is_mock=True,
        )


def _audio(tmp_path: Path) -> str:
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF-contract-test-audio")
    return str(path)


def _identity_is_clean(adapter) -> None:
    first, second = adapter.identity(), adapter.identity()
    assert first == second  # deterministic: a checkpoint/manifest key
    blob = json.dumps(first.as_manifest())
    assert API_KEY not in blob and "Bearer" not in blob


# --- identical contract per port ------------------------------------------------------


def test_intelligence_contract(intelligence_adapter):
    result = asyncio.run(intelligence_adapter.analyze(REQUEST))
    assert isinstance(result, IntelligenceResult)
    assert IntelligenceResult.model_validate(result.model_dump()) == result
    identity = intelligence_adapter.identity()
    assert (result.provider, result.model) == (identity.provider, identity.model)
    assert result.usage is None or isinstance(result.usage, ProviderUsageReport)
    _identity_is_clean(intelligence_adapter)


def test_asr_contract(asr_adapter, tmp_path):
    result = asyncio.run(
        asr_adapter.transcribe(ASRRequest(audio_path=_audio(tmp_path), language="en"))
    )
    assert isinstance(result, ASRResult)
    assert ASRResult.model_validate(result.model_dump()) == result
    identity = asr_adapter.identity()
    assert (result.provider, result.model) == (identity.provider, identity.model)
    assert [segment.index for segment in result.segments] == list(range(len(result.segments)))
    for earlier, later in zip(result.segments, result.segments[1:], strict=False):
        assert earlier.start_seconds <= later.start_seconds
    assert result.language not in {"", "auto"}
    _identity_is_clean(asr_adapter)


def test_diarization_contract(diarization_adapter, tmp_path):
    result = asyncio.run(
        diarization_adapter.diarize(DiarizationRequest(audio_path=_audio(tmp_path)))
    )
    assert isinstance(result, DiarizationResult)
    assert DiarizationResult.model_validate(result.model_dump()) == result
    if result.status is DiarizationStatus.UNAVAILABLE:
        assert result.issue is not None  # a refusal always says why
    else:
        assert result.segments  # "available" never means "no speakers"
    assert result.provider == diarization_adapter.identity().provider
    _identity_is_clean(diarization_adapter)


# --- schema rejection -----------------------------------------------------------------


def _with(**changes) -> dict:
    return {**VALID_OUTPUT, **changes}


MALFORMED_OUTPUTS = [
    pytest.param("not json", id="not-json"),
    pytest.param("", id="empty"),
    pytest.param(json.dumps([VALID_OUTPUT]), id="array"),
    pytest.param(json.dumps({**VALID_OUTPUT, "extra": 1}), id="extra-key"),
    pytest.param(
        json.dumps({k: v for k, v in VALID_OUTPUT.items() if k != "insights"}), id="missing-key"
    ),
    pytest.param(json.dumps(_with(summary=5)), id="summary-not-text"),
    pytest.param(json.dumps(_with(summary_source_turn_indexes=[-1])), id="negative-index"),
    pytest.param(
        json.dumps(_with(action_items=[{**VALID_OUTPUT["action_items"][0], "priority": "urgent"}])),
        id="unknown-priority",
    ),
    pytest.param(
        json.dumps(_with(action_items=[{**VALID_OUTPUT["action_items"][0], "description": ""}])),
        id="empty-action",
    ),
    pytest.param(
        json.dumps(
            _with(action_items=[{**VALID_OUTPUT["action_items"][0], "due_date": "tomorrow"}])
        ),
        id="invalid-date",
    ),
    pytest.param(
        json.dumps(_with(insights=[{**VALID_OUTPUT["insights"][0], "confidence": 1.7}])),
        id="confidence-above-one",
    ),
    pytest.param(
        json.dumps(_with(action_items=[{**VALID_OUTPUT["action_items"][0], "surprise": True}])),
        id="nested-extra-key",
    ),
]


@pytest.mark.parametrize("content", MALFORMED_OUTPUTS)
def test_an_api_adapter_never_turns_malformed_output_into_a_result(content):
    adapter = _api_adapter(lambda request: _completion(content))
    with pytest.raises(ProviderError) as refused:
        asyncio.run(adapter.analyze(REQUEST))
    assert refused.value.code == "intelligence_invalid_response"
    assert refused.value.retryable is False  # a schema violation is not transient


@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param({}, id="no-choices"),
        pytest.param({"choices": []}, id="empty-choices"),
        pytest.param({"choices": [{"message": {"content": None}}]}, id="null-content"),
        pytest.param({"choices": [{"message": {}}]}, id="no-content"),
    ],
)
def test_an_api_adapter_rejects_malformed_envelopes(envelope):
    adapter = _api_adapter(lambda request: httpx.Response(200, json=envelope))
    with pytest.raises(ProviderError) as refused:
        asyncio.run(adapter.analyze(REQUEST))
    assert refused.value.code == "intelligence_invalid_response" and not refused.value.retryable


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(_with(provider="", model="m"), id="no-provider"),
        pytest.param(_with(provider="p", model="m", summary_source_turn_indexes=[-2]), id="index"),
        pytest.param(
            _with(provider="p", model="m", usage={"input_tokens": -1}), id="usage-negative"
        ),
        pytest.param(_with(provider="p", model="m", usage={"tokens": 3}), id="usage-unknown-key"),
    ],
)
def test_the_shared_result_schema_rejects_what_any_adapter_could_return(document):
    with pytest.raises(ValidationError):
        IntelligenceResult.model_validate(document)


def test_local_whisper_rejects_an_impossible_segment_as_a_typed_failure(tmp_path):
    broken = _FakeWhisper([SimpleNamespace(start=3.0, end=1.0, text="backwards")])
    with pytest.raises(ProviderError) as refused:
        asyncio.run(_whisper(broken).transcribe(ASRRequest(audio_path=_audio(tmp_path))))
    assert refused.value.code == "asr_failed" and refused.value.retryable is False
    with pytest.raises(ProviderError) as missing:
        asyncio.run(_whisper().transcribe(ASRRequest(audio_path=str(tmp_path / "none.wav"))))
    assert missing.value.code == "audio_not_found"


# --- typed failures of an API adapter -------------------------------------------------


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (422, False),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
    ],
)
def test_api_failures_are_typed_and_never_echo_secrets(status, retryable):
    adapter = _api_adapter(
        lambda request: httpx.Response(status, json={"error": {"message": f"bad key {API_KEY}"}})
    )
    with pytest.raises(ProviderError) as failed:
        asyncio.run(adapter.analyze(REQUEST))
    assert failed.value.retryable is retryable
    assert API_KEY not in failed.value.public_message
    assert "llm.example.test" not in failed.value.public_message


def test_the_api_adapter_requests_strict_structured_output_without_leaking_the_key():
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _api_ok(request)

    asyncio.run(_api_adapter(respond).analyze(REQUEST))
    body = json.loads(seen[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["temperature"] == 0 and body["model"] == "contract-llm-2026-09"
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"  # header only
    assert API_KEY not in str(seen[0].url) and API_KEY not in seen[0].content.decode()


# --- usage reporting --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 120, "completion_tokens": 45}, (120, 45)),
        ({"prompt_tokens": 120}, (120, None)),
        ({"prompt_tokens": -3, "completion_tokens": "many"}, None),
        ({"prompt_tokens": True, "completion_tokens": False}, None),
        ("n/a", None),
        (None, None),
    ],
)
def test_usage_is_reported_as_given_or_honestly_absent(usage, expected):
    adapter = _api_adapter(lambda request: _completion(json.dumps(VALID_OUTPUT), usage=usage))
    result = asyncio.run(adapter.analyze(REQUEST))
    if expected is None:
        assert result.usage is None
    else:
        assert (result.usage.input_tokens, result.usage.output_tokens) == expected
    # The deterministic local adapter counts nothing and says so.
    assert asyncio.run(MockIntelligenceProvider(allow_mock=True).analyze(REQUEST)).usage is None


def test_reported_usage_reaches_the_usage_ledger_and_absent_usage_stays_null(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "contract-usage")
    adapter = _api_adapter(_api_ok)
    private = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", adapter.identity(), kind="private", region="customer-dc")],
    )
    providers = ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript="Please call me back tomorrow."),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=adapter,
    )
    accepted = _upload(client, tenant, key="contract-usage-1", language="en")
    job_id = accepted.json()["job"]["id"]
    result = process_job(job_id, worker_id="w", settings=private, providers=providers)
    assert result["status"] == "completed", result
    with inspection_session_factory()() as db:
        rows = {
            row.stage: row
            for row in db.scalars(select(ProviderUsage).where(ProviderUsage.job_id == job_id))
        }
    assert (rows["intelligence"].input_tokens, rows["intelligence"].output_tokens) == (120, 45)
    assert rows["asr"].input_tokens is None and rows["asr"].output_tokens is None


# --- Padyar AI Gateway: explicit unconfigured state --------------------------------------


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (create_intelligence_provider, "intelligence_provider"),
        (create_asr_provider, "asr_provider"),
        (create_diarization_provider, "diarization_provider"),
    ],
)
@pytest.mark.parametrize("name", sorted(padyar.NAMES))
def test_selecting_padyar_is_a_typed_unconfigured_state(settings, factory, field, name):
    with pytest.raises(ProviderError) as unavailable:
        factory(settings.model_copy(update={field: name}))
    assert unavailable.value.code == "provider_contract_unavailable"
    assert unavailable.value.retryable is False
    assert "Padyar AI Gateway" in unavailable.value.public_message


def test_the_preflight_reports_padyar_without_calling_anything(settings):
    code, report = run_preflight(settings.model_copy(update={"intelligence_provider": "padyar"}))
    assert code == 2 and report == {"ready": False, "error": "provider_contract_unavailable"}
    assert len(padyar.MISSING_INPUTS) >= 5
