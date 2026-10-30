"""HOM-40: VAD rescue for 8 kHz phone audio (ADR 0044, amendment HOM-40).

When VAD keeps no audio, the Whisper adapter makes one more ``transcribe`` call
with ``RESCUE_VAD_PARAMETERS``. The adapter tests run on fake models. The real
VAD tests need faster-whisper (the ``ai-local`` extra) and skip without it.
"""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.providers.schemas import ASRRequest, ASRResult
from backend.providers.whisper import LocalWhisperProvider
from backend.speech.language_policy import POLICY_VERSION, LanguageDecision, LanguageSource

FIXTURE = (
    Path(__file__).parent / "fixtures" / "audio" / "fleurs_en_us_11705046490861852963_tel8k.wav"
)
_EMPTY = 0.0
_KEPT = 3.0


def _rescue() -> dict[str, Any]:
    from backend.providers.whisper import RESCUE_VAD_PARAMETERS

    return RESCUE_VAD_PARAMETERS


class _Model:
    """Fake faster-whisper model.

    ``first`` and ``rescue`` are the infos a call without and with
    ``vad_parameters`` returns. A call with a language reports that language;
    its ``duration_after_vad`` follows the same first/rescue split.
    """

    def __init__(self, first: Any, rescue: Any) -> None:
        self.first = first
        self.rescue = rescue
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.consumed: list[str] = []

    def transcribe(self, path: str, **kwargs: Any):
        self.calls.append((path, kwargs))
        rescued = "vad_parameters" in kwargs
        base = self.rescue if rescued else self.first
        language = kwargs.get("language")
        label = f"{'rescue' if rescued else 'first'}-{language or 'auto'}"

        def segments():
            self.consumed.append(label)
            yield SimpleNamespace(start=0.5, end=2.0, text=f"text-{label}")

        if language is None:
            return segments(), base
        info = SimpleNamespace(
            language=language, duration=base.duration, duration_after_vad=base.duration_after_vad
        )
        return segments(), info


def _info(kept: float, language: str | None = "en", probs=None) -> Any:
    probability = probs[0][1] if probs else None
    return SimpleNamespace(
        language=language,
        language_probability=probability,
        all_language_probs=probs,
        duration=7.86,
        duration_after_vad=kept,
    )


def _audio(tmp_path: Path) -> str:
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF....WAVE")
    return str(path)


def _provider(model: Any) -> LocalWhisperProvider:
    return LocalWhisperProvider(model_name="base", device="cpu", compute_type="int8", model=model)


def _run(model: Any, audio: str, language: str | None, *, vad_filter: bool = True) -> ASRResult:
    request = ASRRequest(audio_path=audio, language=language, vad_filter=vad_filter)
    return asyncio.run(_provider(model).transcribe(request))


def _kwargs(**extra: Any) -> dict[str, Any]:
    return {"beam_size": 5, "vad_filter": True, **extra}


# --- AC1: the rescue finds speech -------------------------------------------------


def test_explicit_request_uses_the_rescue_call(tmp_path: Path) -> None:
    model = _Model(_info(_EMPTY), _info(_KEPT))
    audio = _audio(tmp_path)
    result = _run(model, audio, "en")

    assert model.calls == [
        (audio, _kwargs(language="en")),
        (audio, _kwargs(language="en", vad_parameters=_rescue())),
    ]
    assert model.consumed == ["rescue-en"]
    assert result.text == "text-rescue-en"
    assert [(s.start_seconds, s.end_seconds) for s in result.segments] == [(0.5, 2.0)]
    assert result.language == "en"
    assert result.language_decision is not None
    assert result.language_decision.source == "requested"


def test_auto_request_decides_on_the_rescue_call(tmp_path: Path) -> None:
    probs = [("en", 0.9), ("fa", 0.05)]
    model = _Model(_info(_EMPTY, "en", [("en", 0.566)]), _info(_KEPT, "en", probs))
    audio = _audio(tmp_path)
    result = _run(model, audio, None)

    assert model.calls == [(audio, _kwargs()), (audio, _kwargs(vad_parameters=_rescue()))]
    assert model.consumed == ["rescue-auto"]
    assert result.text == "text-rescue-auto"
    assert result.language == "en"
    decision = result.language_decision
    assert decision is not None
    assert (decision.source, decision.detected, decision.reason) == ("detected", "en", None)
    assert decision.probability == pytest.approx(0.9)
    assert decision.policy_version == POLICY_VERSION


def test_auto_override_after_rescue_keeps_the_rescue_vad(tmp_path: Path) -> None:
    model = _Model(_info(_EMPTY), _info(_KEPT, "ar", [("ar", 0.6), ("fa", 0.3)]))
    audio = _audio(tmp_path)
    result = _run(model, audio, "auto")

    assert model.calls == [
        (audio, _kwargs()),
        (audio, _kwargs(vad_parameters=_rescue())),
        (audio, _kwargs(vad_parameters=_rescue(), language="fa")),
    ]
    assert model.consumed == ["rescue-fa"]
    assert result.text == "text-rescue-fa"
    assert result.language == "fa"
    decision = result.language_decision
    assert decision is not None
    assert (decision.source, decision.detected, decision.reason) == (
        "fallback",
        "ar",
        "low_confidence",
    )


# --- AC2: the rescue finds nothing ------------------------------------------------


@pytest.mark.parametrize("language", ["en", None])
def test_rescue_that_keeps_nothing_is_no_speech(tmp_path: Path, language: str | None) -> None:
    model = _Model(_info(_EMPTY, "en", [("en", 0.566)]), _info(_EMPTY, "en", [("en", 0.566)]))
    result = _run(model, _audio(tmp_path), language)

    assert len(model.calls) == 2
    assert model.calls[1][1]["vad_parameters"] == _rescue()
    assert model.consumed == []
    assert result.text == ""
    assert result.segments == []
    decision = result.language_decision
    assert decision is not None
    if language is None:
        assert (result.language, decision.source, decision.reason) == (
            "fa",
            "fallback",
            "no_speech",
        )
        assert decision.detected is None and decision.probability is None
    else:
        assert (result.language, decision.source) == ("en", "requested")


# --- AC3: no change when the first call keeps audio --------------------------------


def test_explicit_request_with_audio_has_no_rescue(tmp_path: Path) -> None:
    model = _Model(_info(_KEPT), _info(_KEPT))
    audio = _audio(tmp_path)
    result = _run(model, audio, "fa")

    assert model.calls == [(audio, _kwargs(language="fa"))]
    assert model.consumed == ["first-fa"]
    assert result.text == "text-first-fa"


def test_auto_detected_with_audio_has_no_rescue(tmp_path: Path) -> None:
    model = _Model(_info(_KEPT, "en", [("en", 0.9)]), _info(_KEPT))
    audio = _audio(tmp_path)
    result = _run(model, audio, None)

    assert model.calls == [(audio, _kwargs())]
    assert model.consumed == ["first-auto"]
    assert result.text == "text-first-auto"
    assert result.language_decision is not None
    assert result.language_decision.source == "detected"


def test_auto_overridden_with_audio_has_no_rescue(tmp_path: Path) -> None:
    model = _Model(_info(_KEPT, "ar", [("ar", 0.6), ("fa", 0.3)]), _info(_KEPT))
    audio = _audio(tmp_path)
    result = _run(model, audio, None)

    assert model.calls == [(audio, _kwargs()), (audio, _kwargs(language="fa"))]
    assert model.consumed == ["first-fa"]
    assert result.text == "text-first-fa"
    assert result.language_decision is not None
    assert result.language_decision.reason == "low_confidence"


@pytest.mark.parametrize("language", ["en", None])
def test_no_rescue_without_vad_filter(tmp_path: Path, language: str | None) -> None:
    model = _Model(_info(_EMPTY, "en", [("en", 0.9)]), _info(_KEPT))
    _run(model, _audio(tmp_path), language, vad_filter=False)

    assert len(model.calls) == 1
    assert "vad_parameters" not in model.calls[0][1]


# --- AC4: decide_language agrees with transcribe -----------------------------------


@pytest.mark.parametrize(
    ("rescue", "expected"),
    [
        (
            _info(_KEPT, "en", [("en", 0.9), ("fa", 0.05)]),
            LanguageDecision(
                language="en",
                source=LanguageSource.DETECTED,
                requested=None,
                detected="en",
                probability=0.9,
            ),
        ),
        (
            _info(_EMPTY, "en", [("en", 0.566)]),
            LanguageDecision(
                language="fa", source=LanguageSource.FALLBACK, requested=None, reason="no_speech"
            ),
        ),
    ],
)
def test_decide_language_gets_the_same_rescue(
    tmp_path: Path, rescue: Any, expected: LanguageDecision
) -> None:
    audio = _audio(tmp_path)
    model = _Model(_info(_EMPTY, "en", [("en", 0.566)]), rescue)
    decision = _provider(model).decide_language(audio, None)

    assert decision == expected
    assert model.calls == [(audio, _kwargs()), (audio, _kwargs(vad_parameters=_rescue()))]
    assert model.consumed == []

    result = _run(_Model(_info(_EMPTY, "en", [("en", 0.566)]), rescue), audio, None)
    assert result.language_decision is not None
    assert (result.language, result.language_decision.source) == (
        decision.language,
        decision.source.value,
    )


# --- AC6: identities are unchanged -------------------------------------------------


def test_identities_are_unchanged() -> None:
    provider = _provider(_Model(_info(_KEPT), _info(_KEPT)))
    assert provider.identity().as_manifest() == {
        "provider": "local_whisper",
        "model": "base",
        "params": {"compute_type": "int8"},
    }
    assert provider.language_policy_identity(None) == {
        "version": "asr-language/1",
        "allowed": ["fa", "en"],
        "default": "fa",
        "min_probability": 0.5,
    }
    assert provider.language_policy_identity("fa") is None


# --- AC5: the real Silero VAD ------------------------------------------------------


def _speech_seconds(audio: Any, **options: Any) -> float:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    stamps = get_speech_timestamps(audio, VadOptions(**options))
    return sum(stamp["end"] - stamp["start"] for stamp in stamps) / 16000


def _phone_wav(tmp_path: Path, name: str, samples: Any) -> str:
    import numpy as np

    path = tmp_path / f"{name}.wav"
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(pcm.tobytes())
    return str(path)


def _signals() -> dict[str, Any]:
    import numpy as np

    rate = 8000
    t = np.arange(10 * rate) / rate
    noise = np.random.default_rng(0).standard_normal(t.size)
    noise *= 10 ** (-30 / 20) / np.sqrt(np.mean(noise**2))  # about -30 dBFS RMS
    tone = 0.5 * np.sin(2 * np.pi * 425 * t)  # Iranian dial tone
    return {
        "silence": np.zeros(t.size),
        "white_noise": noise,
        "dial_tone": tone,
        "ringback": tone * ((t % 5.0) < 1.0),  # 1 s on, 4 s off
    }


def test_real_vad_rescues_the_phone_fixture() -> None:
    pytest.importorskip("faster_whisper", reason="needs the ai-local extra (faster-whisper)")
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(FIXTURE))
    assert _speech_seconds(audio) == 0.0  # the defect: default VAD empties real speech
    assert _speech_seconds(audio, **_rescue()) >= 3.0


@pytest.mark.parametrize("name", ["silence", "white_noise", "dial_tone", "ringback"])
def test_real_vad_rescue_keeps_nothing_on_non_speech(tmp_path: Path, name: str) -> None:
    pytest.importorskip("faster_whisper", reason="needs the ai-local extra (faster-whisper)")
    from faster_whisper.audio import decode_audio

    audio = decode_audio(_phone_wav(tmp_path, name, _signals()[name]))
    assert _speech_seconds(audio, **_rescue()) == 0.0
