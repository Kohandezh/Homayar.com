"""HOM-39: the ASR language policy and how the Whisper adapter applies it.

The policy is pure. The adapter runs on fake models, so faster-whisper is not
needed. Auto mode reads the language that faster-whisper detects inside the
first ``transcribe`` call, keeps it only when the policy allows it, and else
forces the policy default in a second call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.jobs import checkpoints as ckpt
from backend.jobs.processor import process_job
from backend.jobs.states import StageName
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRRequest, ASRResult
from backend.providers.whisper import RESCUE_VAD_PARAMETERS, LocalWhisperProvider
from backend.speech.language_policy import (
    POLICY_VERSION,
    LanguageDecision,
    LanguagePolicy,
    LanguageSource,
    decide,
    needs_detection,
    normalize_language,
)
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _queued_upload

POLICY = LanguagePolicy()


# --- pure policy ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fa-IR", "fa"),
        (" EN ", "en"),
        ("fa_IR", "fa"),
        ("FA", "fa"),
        ("yue", "yue"),
        (None, None),
        ("", None),
        ("   ", None),
        ("auto", None),
        ("AUTO", None),
    ],
)
def test_normalize_language(value: str | None, expected: str | None) -> None:
    assert normalize_language(value) == expected
    assert needs_detection(value) is (expected is None)


def test_policy_defaults_and_descriptor() -> None:
    assert POLICY.allowed == ("fa", "en")
    assert POLICY.default == "fa"
    assert POLICY.min_probability == 0.5
    assert POLICY.descriptor() == {
        "version": POLICY_VERSION,
        "allowed": ["fa", "en"],
        "default": "fa",
        "min_probability": 0.5,
    }
    assert POLICY_VERSION == "asr-language/1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"allowed": ()},
        {"allowed": ("fa-IR",)},
        {"allowed": ("FA",)},
        {"allowed": ("fa", "auto")},
        {"default": "de"},
        {"default": "auto"},
        {"min_probability": 1.5},
        {"min_probability": -0.1},
    ],
)
def test_policy_rejects_invalid_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        LanguagePolicy(**kwargs)


@pytest.mark.parametrize("bound", [0.0, 1.0])
def test_policy_accepts_probability_bounds(bound: float) -> None:
    assert LanguagePolicy(min_probability=bound).min_probability == bound


def test_policy_from_settings_normalizes() -> None:
    settings = SimpleNamespace(
        asr_allowed_languages=" FA-IR , en_US ,, ",
        asr_default_language="En",
        asr_language_min_probability=0.7,
    )
    policy = LanguagePolicy.from_settings(settings)
    assert policy == LanguagePolicy(allowed=("fa", "en"), default="en", min_probability=0.7)


def test_explicit_request_is_honoured_and_ignores_probabilities() -> None:
    decision = decide("fa-IR", POLICY, [("en", 0.99)])
    assert decision == LanguageDecision(
        language="fa", source=LanguageSource.REQUESTED, requested="fa"
    )
    # Not in the allowed list: the operator chose it, so it is kept.
    assert decide("de", POLICY).language == "de"
    assert decide("de", POLICY).source is LanguageSource.REQUESTED


def test_auto_request_needs_probabilities() -> None:
    with pytest.raises(ValueError):
        decide(None, POLICY)
    with pytest.raises(ValueError):
        decide("auto", POLICY, None)


def test_auto_request_without_speech_falls_back() -> None:
    assert decide("auto", POLICY, []) == LanguageDecision(
        language="fa",
        source=LanguageSource.FALLBACK,
        requested=None,
        detected=None,
        probability=None,
        reason="no_speech",
    )


@pytest.mark.parametrize(
    ("probabilities", "language", "source", "detected", "probability", "reason"),
    [
        ([("en", 0.9), ("fa", 0.05)], "en", LanguageSource.DETECTED, "en", 0.9, None),
        ([("ar", 0.6), ("fa", 0.3)], "fa", LanguageSource.FALLBACK, "ar", 0.6, "low_confidence"),
        ([("de", 0.92), ("en", 0.03)], "fa", LanguageSource.FALLBACK, "de", 0.92, "low_confidence"),
        ([("fa", 0.5)], "fa", LanguageSource.DETECTED, "fa", 0.5, None),
        ([("fa", 0.49)], "fa", LanguageSource.FALLBACK, "fa", 0.49, "low_confidence"),
        ([("en", 0.9)], "en", LanguageSource.DETECTED, "en", 0.9, None),
        # Not sorted, codes not normalized: still the overall top entry.
        ([("ur", 0.2), ("EN", 0.7), ("fa", 0.1)], "en", LanguageSource.DETECTED, "en", 0.7, None),
        # The allowed language under the threshold falls back even if it is second.
        ([("ar", 0.45), ("en", 0.4)], "fa", LanguageSource.FALLBACK, "ar", 0.45, "low_confidence"),
    ],
)
def test_auto_request_rules(
    probabilities: list[tuple[str, float]],
    language: str,
    source: LanguageSource,
    detected: str,
    probability: float,
    reason: str | None,
) -> None:
    decision = decide(None, POLICY, probabilities)
    assert decision.language == language
    assert decision.source is source
    assert decision.requested is None
    assert decision.detected == detected
    assert decision.probability == pytest.approx(probability)
    assert decision.reason == reason


def test_auto_tie_prefers_the_earlier_allowed_language() -> None:
    policy = LanguagePolicy(allowed=("en", "fa"), default="en", min_probability=0.4)
    assert decide(None, policy, [("fa", 0.45), ("en", 0.45)]).language == "en"
    assert decide(None, POLICY, [("en", 0.45), ("fa", 0.45)]).language == "fa"


def test_language_source_values() -> None:
    assert [s.value for s in LanguageSource] == ["requested", "detected", "fallback"]


# --- Whisper adapter ------------------------------------------------------------


class _Model:
    """Fake faster-whisper model. ``info`` is what auto mode would detect."""

    def __init__(
        self,
        info: Any,
        *,
        text_by_language: dict[str, str] | None = None,
        explicit_extra: dict[str, Any] | None = None,
    ) -> None:
        self.info = info
        self.explicit_extra = explicit_extra or {}
        self.text_by_language = text_by_language or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.consumed: list[str | None] = []

    def transcribe(self, path: str, **kwargs: Any):
        self.calls.append((path, kwargs))
        language = kwargs.get("language")
        label = language or "first"

        def segments():
            self.consumed.append(label)
            text = self.text_by_language.get(language or "first", f"text-{label}")
            yield SimpleNamespace(start=0.0, end=1.0, text=text)

        if language is None:
            return segments(), self.info
        return segments(), SimpleNamespace(language=language, duration=1.5, **self.explicit_extra)


def _info(language: str | None, probability: float | None, probs=None, **extra: Any):
    return SimpleNamespace(
        language=language,
        language_probability=probability,
        all_language_probs=probs,
        duration=1.5,
        **extra,
    )


def _audio(tmp_path: Path) -> str:
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF....WAVE")
    return str(path)


def _provider(model: Any, **kwargs: Any) -> LocalWhisperProvider:
    return LocalWhisperProvider(
        model_name="base", device="cpu", compute_type="int8", model=model, **kwargs
    )


def _run(provider: LocalWhisperProvider, audio: str, language: str | None) -> ASRResult:
    return asyncio.run(provider.transcribe(ASRRequest(audio_path=audio, language=language)))


def test_explicit_language_is_normalized_before_the_model(tmp_path: Path) -> None:
    model = _Model(_info("en", 0.9))
    result = _run(_provider(model), _audio(tmp_path), "fa-IR")

    assert model.calls == [
        (model.calls[0][0], {"beam_size": 5, "vad_filter": True, "language": "fa"})
    ]
    assert result.language == "fa"
    assert result.text == "text-fa"
    decision = result.language_decision
    assert decision is not None
    assert decision.source == "requested"
    assert decision.requested == "fa"
    assert decision.detected is None and decision.probability is None
    assert decision.policy_version == POLICY_VERSION


def test_auto_language_agreeing_with_detection_uses_one_call(tmp_path: Path) -> None:
    model = _Model(_info("en", 0.9, [("en", 0.9), ("fa", 0.05)]))
    result = _run(_provider(model), _audio(tmp_path), "auto")

    assert len(model.calls) == 1
    assert "language" not in model.calls[0][1]
    assert model.consumed == ["first"]
    assert result.language == "en"
    assert result.text == "text-first"
    assert result.language_decision is not None
    assert result.language_decision.source == "detected"
    assert result.language_decision.detected == "en"
    assert result.language_decision.probability == pytest.approx(0.9)


def test_auto_language_outside_policy_reruns_with_the_default(tmp_path: Path) -> None:
    model = _Model(_info("ar", 0.6, [("ar", 0.6), ("fa", 0.3)]))
    result = _run(_provider(model), _audio(tmp_path), None)

    assert [kwargs.get("language") for _, kwargs in model.calls] == [None, "fa"]
    # The first generator is never consumed: its Arabic text is not returned.
    assert model.consumed == ["fa"]
    assert result.text == "text-fa"
    assert result.language == "fa"
    decision = result.language_decision
    assert decision is not None
    assert (decision.source, decision.detected, decision.reason) == (
        "fallback",
        "ar",
        "low_confidence",
    )
    assert decision.probability == pytest.approx(0.6)


def test_auto_language_uses_info_language_when_no_probability_list(tmp_path: Path) -> None:
    model = _Model(_info("en", 0.8, None))
    result = _run(_provider(model), _audio(tmp_path), "auto")
    assert len(model.calls) == 1
    assert result.language == "en"
    assert result.language_decision is not None
    assert result.language_decision.source == "detected"


def test_auto_language_without_any_detection_falls_back(tmp_path: Path) -> None:
    model = _Model(SimpleNamespace(language=None, duration=0.0))
    result = _run(_provider(model), _audio(tmp_path), "auto")
    assert [kwargs.get("language") for _, kwargs in model.calls] == [None, "fa"]
    assert result.language == "fa"
    assert result.language_decision is not None
    assert result.language_decision.reason == "no_speech"


def test_custom_policy_is_used(tmp_path: Path) -> None:
    policy = LanguagePolicy(allowed=("en",), default="en", min_probability=0.95)
    model = _Model(_info("en", 0.9, [("en", 0.9)]))
    provider = _provider(model, language_policy=policy)
    assert provider.language_policy is policy
    result = _run(provider, _audio(tmp_path), None)
    # Under the threshold, so a fallback. The fallback equals the detected
    # language, so the first generator is used and there is no second call.
    assert [kwargs.get("language") for _, kwargs in model.calls] == [None]
    assert result.text == "text-first"
    assert result.language_decision is not None
    assert result.language_decision.source == "fallback"
    assert result.language_decision.reason == "low_confidence"


def test_default_policy_and_identity_is_unchanged() -> None:
    provider = _provider(_Model(_info("fa", 1.0)))
    assert provider.language_policy == LanguagePolicy()
    assert provider.identity().as_manifest() == {
        "provider": "local_whisper",
        "model": "base",
        "params": {"compute_type": "int8"},
    }


def test_decide_language_does_not_consume_segments(tmp_path: Path) -> None:
    model = _Model(_info("ar", 0.7, [("ar", 0.7), ("fa", 0.2)]))
    provider = _provider(model)
    decision = provider.decide_language(_audio(tmp_path), None)
    assert isinstance(decision, LanguageDecision)
    assert decision.source is LanguageSource.FALLBACK
    assert decision.language == "fa"
    assert len(model.calls) == 1 and model.consumed == []

    explicit = provider.decide_language(_audio(tmp_path), "EN")
    assert explicit.source is LanguageSource.REQUESTED and explicit.language == "en"
    assert len(model.calls) == 1  # no model call for an explicit request


# --- SPEC 13: no speech after VAD ------------------------------------------------

# What faster-whisper 1.2.0 returned for 5 s of silence (turn-2 check): VAD kept
# nothing, but detection still gave a full list with "en" on top.
_SILENT_PROBS = [("en", 0.566), ("nn", 0.127), ("ja", 0.039)]


def test_auto_request_with_no_audio_after_vad_is_no_speech(tmp_path: Path) -> None:
    model = _Model(_info("en", 0.566, _SILENT_PROBS, duration_after_vad=0.0))
    result = _run(_provider(model), _audio(tmp_path), "auto")

    # One rescue call (HOM-40), which keeps nothing too; no decode with the default.
    assert len(model.calls) == 2
    assert model.calls[1][1]["vad_parameters"] == RESCUE_VAD_PARAMETERS
    assert model.consumed == []  # no generator is read
    assert result.text == ""
    assert result.segments == []
    assert result.language == "fa"
    assert result.duration_seconds == 1.5  # from info.duration
    decision = result.language_decision
    assert decision is not None
    assert (decision.source, decision.reason) == ("fallback", "no_speech")
    assert decision.detected is None and decision.probability is None


@pytest.mark.parametrize("kept", [None, "0", 2.0, "missing"])
def test_auto_request_with_audio_after_vad_is_unchanged(tmp_path: Path, kept: Any) -> None:
    extra = {} if kept == "missing" else {"duration_after_vad": kept}
    model = _Model(_info("en", 0.9, [("en", 0.9), ("fa", 0.05)], **extra))
    result = _run(_provider(model), _audio(tmp_path), None)

    assert len(model.calls) == 1
    assert model.consumed == ["first"]
    assert result.text == "text-first"
    assert result.language == "en"
    assert result.language_decision is not None
    assert result.language_decision.source == "detected"


def test_explicit_request_with_no_audio_after_vad_is_no_speech(tmp_path: Path) -> None:
    # HOM-40: an explicit request now gets the rescue call too. Here it keeps
    # nothing as well, so the result is empty and no generator is read.
    model = _Model(_info("en", 0.9), explicit_extra={"duration_after_vad": 0.0})
    result = _run(_provider(model), _audio(tmp_path), "fa")

    assert [kwargs.get("language") for _, kwargs in model.calls] == ["fa", "fa"]
    assert model.calls[1][1]["vad_parameters"] == RESCUE_VAD_PARAMETERS
    assert model.consumed == []
    assert result.text == ""
    assert result.language_decision is not None
    assert result.language_decision.source == "requested"


def test_decide_language_with_no_audio_after_vad_is_no_speech(tmp_path: Path) -> None:
    model = _Model(_info("en", 0.566, _SILENT_PROBS, duration_after_vad=0))
    decision = _provider(model).decide_language(_audio(tmp_path), None)
    assert decision == LanguageDecision(
        language="fa", source=LanguageSource.FALLBACK, requested=None, reason="no_speech"
    )
    assert len(model.calls) == 2 and model.consumed == []  # plus the rescue call (HOM-40)


def test_decide_language_maps_errors(tmp_path: Path) -> None:
    provider = _provider(_Model(_info("fa", 1.0)))
    with pytest.raises(ProviderError) as missing:
        provider.decide_language(str(tmp_path / "absent.wav"), None)
    assert missing.value.code == "audio_not_found"

    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(ProviderError) as exc:
        provider.decide_language(str(empty), None)
    assert exc.value.code == "audio_empty"

    class Broken:
        def transcribe(self, path: str, **kwargs: Any):
            raise RuntimeError("boom")

    with pytest.raises(ProviderError) as failed:
        _provider(Broken()).decide_language(_audio(tmp_path), None)
    assert failed.value.code == "asr_failed"


def test_language_policy_identity() -> None:
    provider = _provider(_Model(_info("fa", 1.0)))
    assert provider.language_policy_identity("fa") is None
    assert provider.language_policy_identity("fa-IR") is None
    assert provider.language_policy_identity("auto") == LanguagePolicy().descriptor()
    assert provider.language_policy_identity(None) == LanguagePolicy().descriptor()
    # The base port default is None (no policy).
    assert MockASRProvider(allow_mock=True).language_policy_identity("auto") is None


def test_old_asr_result_without_decision_still_loads() -> None:
    old = {
        "provider": "local_whisper",
        "model": "base",
        "language": "fa",
        "text": "سلام",
        "segments": [],
        "duration_seconds": 1.0,
        "is_mock": False,
    }
    assert ASRResult.model_validate(old).language_decision is None


# --- processor checkpoint identity ----------------------------------------------


class _PolicyASR(ASRProvider):
    """Mock ASR that declares a language policy for every language."""

    def __init__(self, descriptor: dict[str, Any] | None) -> None:
        self._inner = MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود")
        self._descriptor = descriptor
        self.seen: list[str | None] = []

    def identity(self):
        return self._inner.identity()

    def language_policy_identity(self, language: str | None) -> dict | None:
        self.seen.append(language)
        return self._descriptor

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return await self._inner.transcribe(request)


def _asr_requests(monkeypatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    original = ckpt.stage_identity

    def spy(context, stage, *, provider, request, upstream=None):
        if stage is StageName.ASR:
            captured.append(dict(request))
        return original(context, stage, provider=provider, request=request, upstream=upstream)

    monkeypatch.setattr(ckpt, "stage_identity", spy)
    return captured


@pytest.mark.parametrize(
    ("descriptor", "expected_extra"),
    [
        (None, {}),
        (LanguagePolicy().descriptor(), {"language_policy": LanguagePolicy().descriptor()}),
    ],
)
def test_processor_adds_the_policy_only_when_the_provider_declares_it(
    client, email_outbox, settings, monkeypatch, descriptor, expected_extra
) -> None:
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    captured = _asr_requests(monkeypatch)
    asr = _PolicyASR(descriptor)
    providers = ProviderBundle(
        asr=asr,
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(job_id, worker_id="hom39-worker", settings=settings, providers=providers)
    assert result["status"] in {"completed", "partial"}
    assert asr.seen == ["fa"]
    assert captured == [{"language": "fa", "beam_size": 5, "vad_filter": True, **expected_extra}]


def test_processor_identity_for_a_provider_without_the_hook(
    client, email_outbox, settings, monkeypatch
) -> None:
    class Plain:
        """Fake that does not subclass ASRProvider (no hook at all)."""

        def __init__(self) -> None:
            self._inner = MockASRProvider(allow_mock=True, transcript="پیگیری قرارداد انجام شود")

        def identity(self):
            return self._inner.identity()

        async def transcribe(self, request: ASRRequest) -> ASRResult:
            return await self._inner.transcribe(request)

    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    captured = _asr_requests(monkeypatch)
    providers = ProviderBundle(
        asr=Plain(),  # type: ignore[arg-type]
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(job_id, worker_id="hom39-plain", settings=settings, providers=providers)
    assert result["status"] in {"completed", "partial"}
    assert captured == [{"language": "fa", "beam_size": 5, "vad_filter": True}]
