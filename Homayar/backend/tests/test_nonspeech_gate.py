"""HOM-348: music and tone segments never become transcript text (ADR 0078).

The detector classifies the audio span of a decoded segment as speech, music
or tone. The Whisper adapter drops music and tone segments and records them as
``ASRNonSpeechSpan``. The gate fails open: any error keeps the ungated result.
The job records a ``non_speech_audio`` reason when only music or tones were
found. Criterion 13 (real faster-whisper) runs in the mission scratch venv,
not here.
"""

from __future__ import annotations

import asyncio
import io
import struct
import subprocess
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.config import Settings
from backend.jobs.asr_chunks import merge_results
from backend.providers.base import ASRProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle, create_asr_provider
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.schemas import (
    ASRNonSpeechSpan,
    ASRRequest,
    ASRResult,
    ASRSegment,
)
from backend.providers.whisper import LocalWhisperProvider
from backend.speech.chunking import ChunkPlan
from backend.tests.test_ingestion_ledger import _headers, _tenant
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider

FIXTURE = (
    Path(__file__).parent / "fixtures" / "audio" / "fleurs_en_us_11705046490861852963_tel8k.wav"
)
RATE = 8000
DTMF_DIGITS = "0123456789*#"
_DTMF_ROWS = (697, 770, 852, 941)
_DTMF_COLUMNS = (1209, 1336, 1477)
_DTMF_LAYOUT = {
    "1": (0, 0),
    "2": (0, 1),
    "3": (0, 2),
    "4": (1, 0),
    "5": (1, 1),
    "6": (1, 2),
    "7": (2, 0),
    "8": (2, 1),
    "9": (2, 2),
    "*": (3, 0),
    "0": (3, 1),
    "#": (3, 2),
}


# --- signal builders (SPEC section 3) --------------------------------------------------


def _mulaw_decode_bytes(data: bytes):
    import numpy as np

    codes = (~np.frombuffer(data, dtype=np.uint8).astype(np.int32)) & 0xFF
    sign = (codes & 0x80) != 0
    exponent = (codes >> 4) & 0x07
    mantissa = codes & 0x0F
    linear = (((mantissa << 3) + 0x84) << exponent) - 0x84
    return np.where(sign, -linear, linear) / 32768.0


def _mulaw_roundtrip(samples):
    import numpy as np

    mu = 255.0
    magnitude = np.log1p(mu * np.clip(np.abs(samples), 0.0, 1.0)) / np.log1p(mu)
    quantized = np.round(magnitude * 127.0) / 127.0
    return np.sign(samples) * np.expm1(quantized * np.log1p(mu)) / mu


def _read_mulaw_wav(path: Path):
    raw = path.read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    position = 12
    fmt = payload = None
    while position + 8 <= len(raw):
        chunk_id = raw[position : position + 4]
        size = struct.unpack("<I", raw[position + 4 : position + 8])[0]
        body = raw[position + 8 : position + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            payload = body
        position += 8 + size + (size & 1)
    assert fmt is not None and payload is not None
    audio_format, channels, _rate, _byte_rate, _block_align, bits = struct.unpack(
        "<HHIIHH", fmt[:16]
    )
    assert (audio_format, channels, bits) == (7, 1, 8)
    return _mulaw_decode_bytes(payload), RATE


def _resample_16k(samples):
    import numpy as np

    t_in = np.arange(samples.size) / RATE
    t_out = np.arange(int(samples.size * 16_000 / RATE)) / 16_000
    return np.interp(t_out, t_in, samples)


def _time(count: int, rate: int):
    import numpy as np

    return np.arange(count) / rate


def _at_rate(samples, rate: int):
    """The 8 kHz signal as itself, or its 16 kHz resample (SPEC section 3)."""
    return samples if rate == RATE else _resample_16k(samples)


def mod_tone(rate: int = RATE):
    import numpy as np

    t = _time(10 * RATE, RATE)
    samples = 0.5 * np.sin(2 * np.pi * 425 * t) * 0.5 * (1 + np.sin(2 * np.pi * 4 * t))
    return _at_rate(samples, rate)


def mod_tone_mulaw(rate: int = RATE):
    return _at_rate(_mulaw_roundtrip(mod_tone(RATE)), rate)


def dial_tone(rate: int = RATE):
    import numpy as np

    t = _time(10 * RATE, RATE)
    return _at_rate(0.5 * np.sin(2 * np.pi * 425 * t), rate)


def ringback(rate: int = RATE):
    t = _time(10 * RATE, RATE)
    return _at_rate(dial_tone(RATE) * ((t % 5.0) < 1.0), rate)


C_MAJOR = (261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25)
_MAJOR_CHORD = (1.0, 1.26, 1.498)
_THREE_HARMONICS = ((1.0, 1.0), (2.0, 0.5), (3.0, 0.25))


def _melody(
    rate: int = RATE,
    *,
    roots: tuple[float, ...] = C_MAJOR,
    chord: tuple[float, ...] = _MAJOR_CHORD,
    harmonics: tuple[tuple[float, float], ...] = _THREE_HARMONICS,
    note_seconds: float = 0.4,
):
    import numpy as np

    note_samples = round(note_seconds * RATE)
    fade = round(0.01 * RATE)
    total = 10 * RATE
    samples = np.zeros(total)
    for note in range(total // note_samples):
        root = roots[note % len(roots)]
        t = _time(note_samples, RATE)
        tone = np.zeros(note_samples)
        for multiplier in chord:
            for harmonic, amplitude in harmonics:
                tone += amplitude * np.sin(2 * np.pi * root * multiplier * harmonic * t)
        tone[:fade] *= np.linspace(0.0, 1.0, fade)
        tone[-fade:] *= np.linspace(1.0, 0.0, fade)
        start = note * note_samples
        samples[start : start + note_samples] = tone
    samples *= 0.3 / np.max(np.abs(samples))
    noise = np.random.default_rng(0).standard_normal(total)
    noise *= 10 ** (-35 / 20) / np.sqrt(np.mean(noise**2))
    return _at_rate(samples + noise, rate)


def melody(rate: int = RATE):
    return _melody(rate)


def melody_quarter_second_notes(rate: int = RATE):
    return _melody(rate, note_seconds=0.25)


def melody_long_notes(rate: int = RATE):
    return _melody(rate, note_seconds=0.8)


def melody_minor_chords(rate: int = RATE):
    return _melody(rate, chord=(1.0, 1.19, 1.498))


def melody_five_harmonics(rate: int = RATE):
    return _melody(rate, harmonics=((1.0, 1.0), (2.0, 0.5), (3.0, 1 / 3), (4.0, 0.25), (5.0, 0.2)))


def melody_octave_lower(rate: int = RATE):
    return _melody(rate, roots=tuple(root / 2.0 for root in C_MAJOR))


def sawtooth_melody(rate: int = RATE):
    """The SPEC melody with harmonics 1-8 at 1/k; harmonics at 4 kHz or above left out."""
    import numpy as np

    note_samples = round(0.4 * RATE)
    fade = round(0.01 * RATE)
    total = 10 * RATE
    samples = np.zeros(total)
    for note in range(total // note_samples):
        root = C_MAJOR[note % len(C_MAJOR)]
        t = _time(note_samples, RATE)
        tone = np.zeros(note_samples)
        for multiplier in _MAJOR_CHORD:
            for harmonic in range(1, 9):
                frequency = root * multiplier * harmonic
                if frequency < 4_000.0:
                    tone += np.sin(2 * np.pi * frequency * t) / harmonic
        tone[:fade] *= np.linspace(0.0, 1.0, fade)
        tone[-fade:] *= np.linspace(1.0, 0.0, fade)
        start = note * note_samples
        samples[start : start + note_samples] = tone
    samples *= 0.3 / np.max(np.abs(samples))
    noise = np.random.default_rng(0).standard_normal(total)
    noise *= 10 ** (-35 / 20) / np.sqrt(np.mean(noise**2))
    return _at_rate(samples + noise, rate)


def _single_notes(
    rate: int,
    harmonics: tuple[tuple[float, float], ...],
    *,
    chord: tuple[float, ...] = (1.0,),
    vibrato: float = 0.0,
):
    """The SPEC melody's notes, timing, fades, scaling and noise, with other partials.

    ``vibrato`` is the peak frequency deviation as a share of each partial, at 5 Hz.
    """
    import numpy as np

    note_samples = round(0.4 * RATE)
    fade = round(0.01 * RATE)
    total = 10 * RATE
    samples = np.zeros(total)
    for note in range(total // note_samples):
        root = C_MAJOR[note % len(C_MAJOR)]
        t = _time(note_samples, RATE)
        tone = np.zeros(note_samples)
        for multiplier in chord:
            for harmonic, amplitude in harmonics:
                frequency = root * multiplier * harmonic
                if frequency < 4_000.0:
                    wobble = (frequency * vibrato / 5.0) * np.sin(2 * np.pi * 5.0 * t)
                    tone += amplitude * np.sin(2 * np.pi * frequency * t + wobble)
        tone[:fade] *= np.linspace(0.0, 1.0, fade)
        tone[-fade:] *= np.linspace(1.0, 0.0, fade)
        start = note * note_samples
        samples[start : start + note_samples] = tone
    samples *= 0.3 / np.max(np.abs(samples))
    noise = np.random.default_rng(0).standard_normal(total)
    noise *= 10 ** (-35 / 20) / np.sqrt(np.mean(noise**2))
    return _at_rate(samples + noise, rate)


def melody_vibrato_chords(rate: int = RATE):
    return _single_notes(rate, _THREE_HARMONICS, chord=_MAJOR_CHORD, vibrato=0.015)


def melody_vibrato_single_notes(rate: int = RATE):
    return _single_notes(rate, _THREE_HARMONICS, vibrato=0.015)


def melody_square_single_notes(rate: int = RATE):
    return _single_notes(rate, tuple((k, 1.0 / k) for k in range(1, 16, 2)))


def melody_sine_single_notes(rate: int = RATE):
    return _single_notes(rate, ((1.0, 1.0),))


def dual_tone(rate: int = RATE):
    """A steady 480 + 620 Hz dual tone, 0.25 each (criterion 2c)."""
    import numpy as np

    t = _time(10 * RATE, RATE)
    samples = 0.25 * np.sin(2 * np.pi * 480 * t) + 0.25 * np.sin(2 * np.pi * 620 * t)
    return _at_rate(samples, rate)


def dtmf(rate: int = RATE):
    import numpy as np

    digit_samples = round(0.1 * RATE)
    samples = np.zeros(10 * RATE)
    for index in range(0, 10 * RATE, 2 * digit_samples):
        digit = DTMF_DIGITS[(index // (2 * digit_samples)) % len(DTMF_DIGITS)]
        row, column = _DTMF_LAYOUT[digit]
        t = _time(digit_samples, RATE)
        samples[index : index + digit_samples] = 0.25 * np.sin(
            2 * np.pi * _DTMF_ROWS[row] * t
        ) + 0.25 * np.sin(2 * np.pi * _DTMF_COLUMNS[column] * t)
    return _at_rate(samples, rate)


def fleurs(rate: int = RATE):
    samples, file_rate = _read_mulaw_wav(FIXTURE)
    assert file_rate == RATE
    return _at_rate(samples, rate)


def _mixed_with(speech, accompaniment, decibels: float):
    import numpy as np

    accompaniment = accompaniment[: speech.size]
    accompaniment = accompaniment * (
        np.sqrt(np.mean(speech**2)) / np.sqrt(np.mean(accompaniment**2)) * 10 ** (-decibels / 20)
    )
    return speech + accompaniment


def fleurs_over_melody(rate: int = RATE):
    return _at_rate(_mixed_with(fleurs(RATE), melody(RATE), 15.0), rate)


def fleurs_over_tone(rate: int = RATE):
    return _at_rate(_mixed_with(fleurs(RATE), dial_tone(RATE), 0.0), rate)


SIGNALS: dict[str, Callable[[int], Any]] = {
    "mod_tone": mod_tone,
    "mod_tone_mulaw": mod_tone_mulaw,
    "melody": melody,
    "melody_quarter_second_notes": melody_quarter_second_notes,
    "melody_long_notes": melody_long_notes,
    "melody_minor_chords": melody_minor_chords,
    "melody_five_harmonics": melody_five_harmonics,
    "melody_octave_lower": melody_octave_lower,
    "dtmf": dtmf,
    "dial_tone": dial_tone,
    "ringback": ringback,
    "fleurs": fleurs,
    "fleurs_over_melody": fleurs_over_melody,
    "fleurs_over_tone": fleurs_over_tone,
}

# (name, start, end, expected class): the spans of SPEC section 3.
TABLE_SPANS = (
    ("mod_tone", 0.0, 10.0, "tone"),
    ("mod_tone_mulaw", 0.0, 10.0, "tone"),
    ("melody", 0.0, 10.0, "music"),
    ("dtmf", 0.0, 10.0, "tone"),
    ("dial_tone", 0.0, 10.0, "tone"),
    ("ringback", 0.0, 6.0, "tone"),
    ("fleurs", 0.0, 7.86, "speech"),
    ("fleurs", 0.0, 4.0, "speech"),
    ("fleurs", 3.5, 7.86, "speech"),
    ("fleurs_over_melody", 0.0, 7.86, "speech"),
    ("fleurs_over_tone", 0.0, 7.86, "speech"),
)

# Other chord melodies (review round 1): all music, whole span.
VARIANT_SPANS = (
    "melody_quarter_second_notes",
    "melody_long_notes",
    "melody_minor_chords",
    "melody_five_harmonics",
    "melody_octave_lower",
)

# The 2 s sub-span check. ringback is 1 s on and 4 s off, so its 2 s window
# covers the first on second plus the start of the silence.
SUB_SPANS = (
    ("mod_tone", 3.0, 5.0, "tone"),
    ("mod_tone_mulaw", 3.0, 5.0, "tone"),
    ("melody", 3.0, 5.0, "music"),
    ("dtmf", 3.0, 5.0, "tone"),
    ("dial_tone", 3.0, 5.0, "tone"),
    ("ringback", 0.0, 2.0, "tone"),
)


# --- criterion 1: the detector ---------------------------------------------------------


@pytest.mark.parametrize("rate", [8_000, 16_000])
@pytest.mark.parametrize(("name", "start", "end", "expected"), TABLE_SPANS)
def test_classify_span_matches_the_signal_table(rate, name, start, end, expected) -> None:
    from backend.speech.nonspeech import classify_span

    assert classify_span(SIGNALS[name](rate), rate, start, end) == expected


@pytest.mark.parametrize("rate", [8_000, 16_000])
@pytest.mark.parametrize("name", VARIANT_SPANS)
def test_other_chord_melodies_are_music(rate, name) -> None:
    from backend.speech.nonspeech import classify_span

    assert classify_span(SIGNALS[name](rate), rate, 0.0, 10.0) == "music"


@pytest.mark.parametrize("rate", [8_000, 16_000])
@pytest.mark.parametrize(("name", "start", "end", "expected"), SUB_SPANS)
def test_non_speech_stays_non_speech_on_two_seconds(rate, name, start, end, expected) -> None:
    from backend.speech.nonspeech import classify_span

    assert classify_span(SIGNALS[name](rate), rate, start, end) == expected


# --- criterion 2: unsure is speech -----------------------------------------------------


def test_unsure_spans_are_speech() -> None:
    import numpy as np

    from backend.speech.nonspeech import classify_span

    samples = mod_tone()
    assert classify_span(samples, RATE, 4.0, 4.0) == "speech"
    assert classify_span(samples, RATE, 5.0, 4.0) == "speech"
    assert classify_span(samples, RATE, 20.0, 30.0) == "speech"
    assert classify_span(samples, RATE, 4.0, 4.2) == "speech"
    assert classify_span(samples, RATE, 1.0, 1.29) == "speech"
    assert classify_span(samples, RATE, 1.0, 2.4) == "speech"
    assert classify_span(np.zeros(10 * RATE), RATE, 0.0, 10.0) == "speech"


# --- criterion 2b: speech is never judged music or tone --------------------------------


@pytest.mark.parametrize("rate", [8_000, 16_000])
@pytest.mark.parametrize("length", [0.5, 1.0, 1.5, 2.0, 3.0])
def test_sliding_windows_over_fleurs_stay_speech(rate, length) -> None:
    from backend.speech.nonspeech import classify_span

    samples = fleurs(rate)
    total = samples.size / rate
    start = 0.0
    while start + length <= total + 1e-9:
        span = classify_span(samples, rate, start, min(start + length, total))
        assert span == "speech", (rate, length, start, span)
        start += 0.1


# --- criterion 2c: speech over music or a dual tone (amendment 5) ----------------------

# (background, speech RMS over the background RMS in dB), all at 16 kHz.
SPEECH_OVER_BACKGROUND = (
    [("melody", decibels) for decibels in (0.0, 3.0, 5.0)]
    + [("sawtooth_melody", decibels) for decibels in (0.0, 3.0, 5.0)]
    + [("dual_tone", decibels) for decibels in (0.0, 3.0, 6.0, 9.0)]
)


def _fleurs_16k_over(background: str, decibels: float):
    background_16k = {"melody": melody, "sawtooth_melody": sawtooth_melody, "dual_tone": dual_tone}
    return _mixed_with(fleurs(16_000), background_16k[background](16_000), decibels)


@pytest.mark.parametrize(("background", "decibels"), SPEECH_OVER_BACKGROUND)
def test_speech_over_music_or_a_dual_tone_stays_speech(background, decibels) -> None:
    from backend.speech.nonspeech import classify_span

    assert classify_span(_fleurs_16k_over(background, decibels), 16_000, 0.0, 7.86) == "speech"


@pytest.mark.parametrize("rate", [8_000, 16_000])
@pytest.mark.parametrize(
    ("builder", "expected"),
    [(sawtooth_melody, "music"), (dual_tone, "tone")],
)
def test_the_criterion_2c_backgrounds_alone_stay_non_speech(rate, builder, expected) -> None:
    from backend.speech.nonspeech import classify_span

    assert classify_span(builder(rate), rate, 0.0, 10.0) == expected
    assert classify_span(builder(rate), rate, 3.0, 5.0) == expected


@pytest.mark.parametrize("rate", [8_000, 16_000])
@pytest.mark.parametrize(
    "builder",
    [
        melody_vibrato_chords,
        melody_vibrato_single_notes,
        melody_square_single_notes,
        melody_sine_single_notes,
    ],
)
def test_vibrato_square_and_sine_melodies_stay_non_speech(rate, builder) -> None:
    from backend.speech.nonspeech import classify_span

    assert classify_span(builder(rate), rate, 0.0, 10.0) in {"music", "tone"}
    assert classify_span(builder(rate), rate, 3.0, 5.0) in {"music", "tone"}


@pytest.mark.parametrize("language", ["en", None])
@pytest.mark.parametrize(("background", "decibels"), SPEECH_OVER_BACKGROUND)
def test_the_adapter_keeps_speech_over_music_or_a_dual_tone(
    tmp_path, language, background, decibels
) -> None:
    loader = _Loader(_fleurs_16k_over(background, decibels))
    result = _run(
        _Segments(0.0, 7.86, "most riders wear riding boots"),
        _audio(tmp_path),
        language,
        audio_loader=loader,
    )

    assert result.text == "most riders wear riding boots"
    assert [segment.index for segment in result.segments] == [0]
    assert result.non_speech == []


def test_other_sample_rates_raise() -> None:
    from backend.speech.nonspeech import classify_span

    with pytest.raises(ValueError):
        classify_span(mod_tone(), 44_100, 0.0, 1.0)


# --- the adapter gate ------------------------------------------------------------------


class _Segments:
    """Fake faster-whisper model whose every call yields one fixed segment."""

    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text

    def transcribe(self, path: str, **kwargs: Any):
        def segments():
            yield SimpleNamespace(start=self.start, end=self.end, text=self.text)

        language = kwargs.get("language")
        info = SimpleNamespace(
            language=language or "en",
            language_probability=0.9 if language is None else None,
            all_language_probs=[("en", 0.9)] if language is None else None,
            duration=10.0,
            duration_after_vad=3.0,
        )
        return segments(), info


class _RescueModel:
    """First call: VAD keeps nothing. Rescue call: one fixed segment."""

    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.rescued = False

    def transcribe(self, path: str, **kwargs: Any):
        rescued = "vad_parameters" in kwargs
        self.rescued = self.rescued or rescued

        def segments():
            if rescued:
                yield SimpleNamespace(start=self.start, end=self.end, text=self.text)

        language = kwargs.get("language")
        info = SimpleNamespace(
            language=language or "en",
            language_probability=None,
            all_language_probs=None,
            duration=10.0,
            duration_after_vad=3.0 if rescued else 0.0,
        )
        return segments(), info


class _TwoSegments:
    def __init__(self, *segments: tuple[float, float, str]) -> None:
        self.segments = segments

    def transcribe(self, path: str, **kwargs: Any):
        def segments():
            for start, end, text in self.segments:
                yield SimpleNamespace(start=start, end=end, text=text)

        language = kwargs.get("language")
        info = SimpleNamespace(
            language=language or "en",
            language_probability=None,
            all_language_probs=None,
            duration=10.0,
            duration_after_vad=9.0,
        )
        return segments(), info


def _provider(
    model: Any, *, nonspeech_gate: bool = True, audio_loader: Any = None
) -> LocalWhisperProvider:
    return LocalWhisperProvider(
        model_name="base",
        device="cpu",
        compute_type="int8",
        model=model,
        nonspeech_gate=nonspeech_gate,
        audio_loader=audio_loader,
    )


def _audio(tmp_path: Path) -> str:
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF....WAVE")
    return str(path)


def _run(
    model: Any,
    audio: str,
    language: str | None,
    *,
    nonspeech_gate: bool = True,
    audio_loader: Any = None,
) -> ASRResult:
    request = ASRRequest(audio_path=audio, language=language, vad_filter=True)
    provider = _provider(model, nonspeech_gate=nonspeech_gate, audio_loader=audio_loader)
    return asyncio.run(provider.transcribe(request))


class _Loader:
    """Audio loader double: returns fixed samples, counts calls, can raise."""

    def __init__(self, samples, error: Exception | None = None) -> None:
        self.samples = samples
        self.error = error
        self.calls: list[str] = []

    def __call__(self, path: str):
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return self.samples


# --- criterion 3: music and tone text is removed ---------------------------------------


@pytest.mark.parametrize("language", ["fa", None])
@pytest.mark.parametrize(
    ("name", "kind"), [("mod_tone", "tone"), ("melody", "music"), ("dtmf", "tone")]
)
def test_a_non_speech_segment_is_removed(tmp_path, language, name, kind) -> None:
    loader = _Loader(SIGNALS[name](16_000))
    result = _run(
        _Segments(0.1, 9.9, f"{name} text"), _audio(tmp_path), language, audio_loader=loader
    )

    assert result.text == ""
    assert result.segments == []
    assert [(s.start_seconds, s.end_seconds, s.kind) for s in result.non_speech] == [
        (0.1, 9.9, kind)
    ]
    assert loader.calls


def test_a_rescued_non_speech_segment_is_removed(tmp_path) -> None:
    loader = _Loader(mod_tone(16_000))
    model = _RescueModel(0.1, 9.9, "music word")
    result = _run(model, _audio(tmp_path), "fa", audio_loader=loader)

    assert model.rescued
    assert result.text == ""
    assert result.segments == []
    assert [(s.start_seconds, s.end_seconds, s.kind) for s in result.non_speech] == [
        (0.1, 9.9, "tone")
    ]


# --- criterion 3b: a span past the end of the audio (amendment 1) ----------------------


def test_a_tone_span_past_the_end_is_removed_with_its_own_times(tmp_path) -> None:
    import numpy as np

    # 5 s of silence, then 5 s of mod_tone; the fake segment runs far past
    # the end of the 10 s audio (seen on base: 5.01-34.99 s).
    audio = np.concatenate([np.zeros(5 * 16_000), mod_tone(16_000)])
    loader = _Loader(audio)
    result = _run(_Segments(5.01, 34.99, "music word"), _audio(tmp_path), "fa", audio_loader=loader)

    assert result.text == ""
    assert result.segments == []
    assert [(s.start_seconds, s.end_seconds, s.kind) for s in result.non_speech] == [
        (5.01, 34.99, "tone")
    ]


def test_a_speech_span_past_the_end_is_kept(tmp_path) -> None:
    loader = _Loader(fleurs(16_000))
    result = _run(_Segments(0.0, 34.99, "kept text"), _audio(tmp_path), "en", audio_loader=loader)

    assert result.text == "kept text"
    assert [segment.index for segment in result.segments] == [0]
    assert result.non_speech == []


def test_a_segment_after_the_audio_ends_is_kept(tmp_path) -> None:
    loader = _Loader(mod_tone(16_000))
    result = _run(_Segments(20.0, 30.0, "kept text"), _audio(tmp_path), "fa", audio_loader=loader)

    assert result.text == "kept text"
    assert result.non_speech == []


def test_a_removed_segment_still_sets_the_duration(tmp_path) -> None:
    # Amendment 2: duration_seconds comes from every decoded segment with
    # text, before the gate removes any, so the gate never changes it.
    import numpy as np

    audio = np.concatenate([np.zeros(5 * 16_000), mod_tone(16_000)])
    model = _Segments(5.01, 34.99, "music word")
    gated = _run(model, _audio(tmp_path), "fa", audio_loader=_Loader(audio))
    ungated = _run(model, _audio(tmp_path), "fa", nonspeech_gate=False)

    assert gated.duration_seconds == ungated.duration_seconds == pytest.approx(34.99)
    assert gated.text == ""
    assert gated.non_speech != []


# --- criterion 4: speech text is unchanged ---------------------------------------------


def test_speech_text_is_unchanged_by_the_gate(tmp_path) -> None:
    model = _Segments(0.5, 7.0, "most riders wear riding boots")
    loader = _Loader(fleurs(16_000))
    on = _run(model, _audio(tmp_path), "en", audio_loader=loader)
    off = _run(model, _audio(tmp_path), "en", nonspeech_gate=False)

    assert on.text == off.text == "most riders wear riding boots"
    assert on.segments == off.segments
    assert on.non_speech == []
    assert off.non_speech == []


# --- criterion 5: a mixed call keeps the speech segment --------------------------------


def test_a_mixed_call_keeps_only_the_speech_segment(tmp_path) -> None:
    import numpy as np

    audio = np.concatenate([fleurs(16_000), mod_tone(16_000)])[: 10 * 16_000]
    loader = _Loader(audio)
    model = _TwoSegments((0.0, 5.0, "hello there"), (5.0, 10.0, "music word"))
    result = _run(model, _audio(tmp_path), "en", audio_loader=loader)

    assert [(s.index, s.text, s.start_seconds, s.end_seconds) for s in result.segments] == [
        (0, "hello there", 0.0, 5.0)
    ]
    assert result.text == "hello there"
    assert [(s.start_seconds, s.end_seconds, s.kind) for s in result.non_speech] == [
        (5.0, 10.0, "tone")
    ]


# --- criterion 6: fail open ------------------------------------------------------------


def test_a_failing_loader_keeps_the_ungated_result(tmp_path, caplog) -> None:
    model = _Segments(0.1, 9.9, "kept text")
    loader = _Loader(None, error=ImportError("no numpy"))
    gated = _run(model, _audio(tmp_path), "en", audio_loader=loader)
    ungated = _run(model, _audio(tmp_path), "en", nonspeech_gate=False)

    assert gated.text == ungated.text == "kept text"
    assert gated.segments == ungated.segments
    assert gated.non_speech == []
    assert loader.calls
    warnings = [r for r in caplog.records if r.getMessage().startswith("ASR_NONSPEECH_GATE_FAILED")]
    assert len(warnings) == 1
    assert "ImportError" in warnings[0].getMessage()


def test_a_missing_default_loader_fails_open(tmp_path, caplog) -> None:
    # This environment has no faster-whisper (like CI), so the default loader
    # itself raises ImportError; the transcript must survive.
    model = _Segments(0.1, 9.9, "kept text")
    result = _run(model, _audio(tmp_path), "en")

    assert result.text == "kept text"
    assert result.non_speech == []
    warnings = [r for r in caplog.records if r.getMessage().startswith("ASR_NONSPEECH_GATE_FAILED")]
    assert len(warnings) == 1


def test_the_loader_is_not_called_without_text_or_without_the_gate(tmp_path) -> None:
    silent = _Segments(0.1, 9.9, "  ")
    loader = _Loader(mod_tone(16_000))
    result = _run(silent, _audio(tmp_path), "en", audio_loader=loader)
    assert result.text == ""
    assert result.non_speech == []
    assert loader.calls == []

    loader.calls.clear()
    result = _run(
        _Segments(0.1, 9.9, "text"),
        _audio(tmp_path),
        "en",
        nonspeech_gate=False,
        audio_loader=loader,
    )
    assert result.text == "text"
    assert loader.calls == []


# --- criterion 7: identities -----------------------------------------------------------


def test_nonspeech_identity() -> None:
    provider = _provider(_Segments(0.1, 9.9, "text"))
    assert provider.nonspeech_identity() == {"version": "nonspeech/1"}
    off = _provider(_Segments(0.1, 9.9, "text"), nonspeech_gate=False)
    assert off.nonspeech_identity() is None


def test_other_identities_are_unchanged() -> None:
    provider = _provider(_Segments(0.1, 9.9, "text"))
    assert provider.identity().as_manifest() == {
        "provider": "local_whisper",
        "model": "base",
        "params": {"compute_type": "int8"},
    }
    assert provider.language_policy_identity("fa") is None
    assert provider.language_policy_identity(None) == {
        "version": "asr-language/1",
        "allowed": ["fa", "en"],
        "default": "fa",
        "min_probability": 0.5,
    }
    assert provider.decode_identity() is None

    class _Plain(ASRProvider):
        async def transcribe(self, request: ASRRequest) -> ASRResult:
            raise NotImplementedError

    assert _Plain().nonspeech_identity() is None


# --- criterion 8 and the job half of criterion 7 ---------------------------------------


class _AnswerASR(ASRProvider):
    """Answers one fixed result, with or without a nonspeech identity."""

    def __init__(self, result: ASRResult, *, nonspeech: dict | None = None) -> None:
        self.result = result
        self._nonspeech = nonspeech

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="answer_fake", model="fake-1")

    def nonspeech_identity(self) -> dict | None:
        return self._nonspeech

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return self.result


class _DuckASR:
    """A test double that does not subclass ``ASRProvider`` (no identity methods)."""

    def __init__(self, result: ASRResult) -> None:
        self.result = result

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="answer_fake", model="fake-1")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return self.result


def _small_wav() -> bytes:
    import numpy as np

    t = _time(2 * RATE, RATE)
    pcm = (0.4 * np.sin(2 * np.pi * 425 * t) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())
    return buffer.getvalue()


@pytest.fixture
def no_dispatch(monkeypatch) -> None:
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)


def _upload(client, tenant: dict, content: bytes, *, key: str):
    response = client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": f"nonspeech-{key}"}),
        cookies=tenant["cookies"],
        data={"title": "Call", "language": "fa"},
        files={"file": ("call.wav", content, "audio/wav")},
    )
    assert response.status_code == 202, response.text
    return response.json()["job"]["id"], response.json()["meeting"]["id"]


def _run_job(job_id: str, settings, asr: ASRProvider) -> dict:
    from backend.jobs.processor import process_job

    return process_job(
        job_id,
        worker_id="test-nonspeech",
        settings=settings,
        providers=ProviderBundle(
            asr=asr,
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=MockIntelligenceProvider(allow_mock=True),
        ),
    )


def _stage_runs(job_id: str) -> list[tuple[str, str, str | None]]:
    from sqlalchemy import select

    from backend.database.saas_models import ProcessingStageRun
    from backend.tests._database import inspection_session_factory

    with inspection_session_factory()() as db:
        query = select(ProcessingStageRun).where(ProcessingStageRun.job_id == job_id)
        rows = list(db.scalars(query))
        return [(row.stage, row.state, row.reason_code) for row in rows]


def _published_request(meeting_id: str) -> dict | None:
    from sqlalchemy import select

    from backend.database.saas_models import CheckpointState, StageCheckpoint
    from backend.tests._database import inspection_session_factory

    with inspection_session_factory()() as db:
        row = db.scalar(
            select(StageCheckpoint)
            .where(
                StageCheckpoint.meeting_id == meeting_id,
                StageCheckpoint.stage == "asr",
                StageCheckpoint.state == CheckpointState.PUBLISHED,
            )
            .limit(1)
        )
        return dict(row.input_manifest["request"]) if row is not None else None


@pytest.mark.ffmpeg
def test_only_music_ends_the_asr_stage_as_non_speech_audio(
    client, email_outbox, settings, no_dispatch
) -> None:
    tenant = _tenant(client, email_outbox, "nonspeech-only")
    job_id, _meeting_id = _upload(client, tenant, _small_wav(), key="only")
    result = ASRResult(
        provider="answer_fake",
        model="fake-1",
        language="fa",
        text="",
        segments=[],
        non_speech=[ASRNonSpeechSpan(start_seconds=0.1, end_seconds=1.9, kind="music")],
        is_mock=True,
    )
    outcome = _run_job(job_id, settings, _AnswerASR(result, nonspeech={"version": "nonspeech/1"}))
    assert outcome["status"] in {"completed", "partial"}
    assert ("asr", "insufficient_evidence", "non_speech_audio") in _stage_runs(job_id)


@pytest.mark.ffmpeg
def test_silent_audio_stays_an_empty_transcript(
    client, email_outbox, settings, no_dispatch
) -> None:
    tenant = _tenant(client, email_outbox, "nonspeech-empty")
    job_id, _meeting_id = _upload(client, tenant, _small_wav(), key="empty")
    result = ASRResult(
        provider="answer_fake", model="fake-1", language="fa", text="", segments=[], is_mock=True
    )
    outcome = _run_job(job_id, settings, _AnswerASR(result))
    assert outcome["status"] in {"completed", "partial"}
    assert ("asr", "insufficient_evidence", "empty_transcript") in _stage_runs(job_id)


@pytest.mark.ffmpeg
def test_the_job_identity_records_the_gate(client, email_outbox, settings, no_dispatch) -> None:
    tenant = _tenant(client, email_outbox, "nonspeech-identity")
    job_id, meeting_id = _upload(client, tenant, _small_wav(), key="identity")
    result = ASRResult(
        provider="answer_fake",
        model="fake-1",
        language="fa",
        text="گفتار",
        segments=[ASRSegment(index=0, text="گفتار", start_seconds=0.1, end_seconds=1.9)],
        is_mock=True,
    )
    outcome = _run_job(job_id, settings, _AnswerASR(result, nonspeech={"version": "nonspeech/1"}))
    assert outcome["status"] == "completed"
    request = _published_request(meeting_id)
    assert request is not None
    assert request["nonspeech"] == {"version": "nonspeech/1"}


@pytest.mark.ffmpeg
def test_a_provider_without_the_method_keeps_the_identity(
    client, email_outbox, settings, no_dispatch
) -> None:
    tenant = _tenant(client, email_outbox, "nonspeech-plain")
    job_id, meeting_id = _upload(client, tenant, _small_wav(), key="plain")
    result = ASRResult(
        provider="answer_fake",
        model="fake-1",
        language="fa",
        text="گفتار",
        segments=[ASRSegment(index=0, text="گفتار", start_seconds=0.1, end_seconds=1.9)],
        is_mock=True,
    )
    outcome = _run_job(job_id, settings, _DuckASR(result))
    assert outcome["status"] == "completed"
    request = _published_request(meeting_id)
    assert request is not None
    assert "nonspeech" not in request


# --- criterion 9: the chunked merge ----------------------------------------------------


def test_merge_results_maps_non_speech_spans_to_original_time() -> None:
    rate = 16_000
    plans = [
        ChunkPlan(0, 0, 10 * rate, 0, 10 * rate, rate),
        ChunkPlan(1, 10 * rate - int(0.5 * rate), 20 * rate, 10 * rate, 20 * rate, rate),
    ]
    first = ASRResult(
        provider="p",
        model="m",
        language="fa",
        text="",
        non_speech=[
            ASRNonSpeechSpan(start_seconds=0.4, end_seconds=0.6, kind="music"),
            ASRNonSpeechSpan(start_seconds=2.0, end_seconds=3.0, kind="tone"),
            # Also clipped: 11 s is past this chunk's 10 s of audio.
            ASRNonSpeechSpan(start_seconds=9.0, end_seconds=11.0, kind="tone"),
        ],
    )
    second = ASRResult(
        provider="p",
        model="m",
        language="fa",
        text="",
        non_speech=[
            ASRNonSpeechSpan(start_seconds=0.4, end_seconds=0.6, kind="tone"),
            # A Whisper end far past the chunk audio (amendment 4): the end is
            # clipped to the chunk length (10.5 s), then shifted and owned.
            ASRNonSpeechSpan(start_seconds=0.4, end_seconds=40.0, kind="music"),
        ],
    )
    merged = merge_results(plans, [first, second], decider=0)

    assert [(s.start_seconds, s.end_seconds, s.kind) for s in merged.non_speech] == [
        (0.4, 0.6, "music"),
        (2.0, 3.0, "tone"),
        (9.0, 10.0, "tone"),
        (9.5 + 0.4, 9.5 + 0.6, "tone"),
        (9.5 + 0.4, 20.0, "music"),
    ]


def test_merge_results_clamps_a_span_start_past_the_chunk_audio() -> None:
    rate = 16_000
    plans = [
        ChunkPlan(0, 0, 10 * rate, 0, 10 * rate, rate),
        ChunkPlan(1, 10 * rate - int(0.5 * rate), 20 * rate, 10 * rate, 20 * rate, rate),
    ]
    first = ASRResult(
        provider="p",
        model="m",
        language="fa",
        text="",
        non_speech=[ASRNonSpeechSpan(start_seconds=12.0, end_seconds=15.0, kind="tone")],
    )
    second = ASRResult(
        provider="p",
        model="m",
        language="fa",
        text="",
        non_speech=[
            ASRNonSpeechSpan(start_seconds=11.0, end_seconds=40.0, kind="music"),
            ASRNonSpeechSpan(start_seconds=2.0, end_seconds=3.0, kind="tone"),
        ],
    )
    merged = merge_results(plans, [first, second], decider=0)

    assert [(s.start_seconds, s.end_seconds, s.kind) for s in merged.non_speech] == [
        (9.5 + 2.0, 9.5 + 3.0, "tone")
    ]


# --- criterion 10: old data loads ------------------------------------------------------


def test_an_asr_result_without_non_speech_still_loads() -> None:
    legacy = {
        "provider": "local_whisper",
        "model": "base",
        "language": "fa",
        "text": "سلام",
        "segments": [{"index": 0, "text": "سلام", "start_seconds": 0.0, "end_seconds": 1.0}],
    }
    result = ASRResult.model_validate(legacy)
    assert result.non_speech == []
    again = ASRResult.model_validate_json(result.model_dump_json(exclude={"non_speech"}))
    assert again.non_speech == []


# --- criterion 11: the setting and the factory -----------------------------------------


def _factory_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "asr_provider": "local_whisper",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_the_setting_defaults_on_and_reaches_the_factory() -> None:
    assert Settings(_env_file=None).asr_nonspeech_gate_enabled is True
    provider = create_asr_provider(_factory_settings())
    assert isinstance(provider, LocalWhisperProvider)
    assert provider.nonspeech_identity() == {"version": "nonspeech/1"}
    disabled = create_asr_provider(_factory_settings(asr_nonspeech_gate_enabled=False))
    assert disabled.nonspeech_identity() is None


# --- criterion 12: import safety -------------------------------------------------------


def test_whisper_imports_without_numpy() -> None:
    code = (
        "import sys; sys.modules['numpy'] = None; "
        "import backend.providers.whisper as w; "
        "print(w.LocalWhisperProvider.__name__)"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, test-only import check
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[2]),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "LocalWhisperProvider"
