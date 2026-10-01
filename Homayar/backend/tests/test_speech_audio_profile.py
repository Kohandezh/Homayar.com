"""Telephone-audio ffmpeg argv contract and PCM16 quality estimation (synthetic signals)."""

from __future__ import annotations

import array
import math
import random
import sys
from pathlib import Path

import pytest

from backend.speech.audio_profile import (
    TELEPHONE_8K,
    AudioProfile,
    build_ffmpeg_argv,
    build_filter_chain,
    estimate_quality,
    run_preprocess,
)

SR = 8000


def pcm(samples: list[int]) -> bytes:
    a = array.array("h", samples)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def tone(seconds: float, amp: float, freq: float = 440.0) -> list[int]:
    return [int(amp * math.sin(2 * math.pi * freq * i / SR)) for i in range(int(seconds * SR))]


def noise(seconds: float, amp: float, seed: int = 7) -> list[int]:
    rnd = random.Random(seed)  # noqa: S311 - deterministic test signal
    return [int(rnd.uniform(-amp, amp)) for _ in range(int(seconds * SR))]


def test_argv_is_fixed_list_no_shell() -> None:
    argv = build_ffmpeg_argv(
        "/usr/bin/ffmpeg", Path("/in/-evil;rm -rf.wav"), Path("/out/x.wav"), TELEPHONE_8K
    )
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
    assert argv[0] == "/usr/bin/ffmpeg"
    assert "file:/in/-evil;rm -rf.wav" in argv  # one element, file: protocol
    assert argv[argv.index("-ar") + 1] == "8000"
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-c:a") + 1] == "pcm_s16le"
    assert "-nostdin" in argv and argv[argv.index("-threads") + 1] == "1"
    assert argv[-1] == "file:/out/x.wav"


def test_filter_chain_options() -> None:
    assert build_filter_chain(AudioProfile(loudness_normalize=False)) == ""
    chain = build_filter_chain(AudioProfile(sample_rate=8000, bandpass=True, denoise=True))
    assert chain == "highpass=f=300,lowpass=f=3400,afftdn=nr=12,loudnorm=I=-16:TP=-1.5:LRA=11"
    argv = build_ffmpeg_argv("ffmpeg", Path("a"), Path("b"), AudioProfile(loudness_normalize=False))
    assert "-af" not in argv


def test_duration_cap() -> None:
    argv = build_ffmpeg_argv("ffmpeg", Path("a"), Path("b"), AudioProfile(max_duration_seconds=90))
    assert argv[argv.index("-t") + 1] == "90"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_rate": 44100},
        {"sample_rate": 8000, "bandpass_high_hz": 5000},
        {"bandpass_low_hz": 3500},
        {"target_lufs": 0.0},
        {"denoise_nr_db": 200.0},
        {"max_duration_seconds": 0},
    ],
)
def test_invalid_profiles(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        AudioProfile(**kwargs)


def test_invalid_executable_and_timeout() -> None:
    with pytest.raises(ValueError):
        build_ffmpeg_argv("ffmpeg\n", Path("a"), Path("b"))
    with pytest.raises(ValueError):
        run_preprocess(["true"], timeout=0)


def test_empty_audio() -> None:
    q = estimate_quality(b"", SR)
    assert q.flags == ("empty",)


def test_clean_speechlike_signal_has_no_flags() -> None:
    # Alternating loud tone and quiet noise: high estimated SNR, little silence.
    sig: list[int] = []
    for k in range(10):
        sig += tone(0.2, 8000)
        sig += noise(0.05, 300, seed=k)
    q = estimate_quality(pcm(sig), SR)
    assert q.flags == ()
    assert q.estimated_snr_db is not None and q.estimated_snr_db > 20
    assert q.clipping_ratio == 0


def test_low_snr_flag() -> None:
    sig = [a + b for a, b in zip(tone(2.0, 3000), noise(2.0, 6000), strict=True)]
    q = estimate_quality(pcm(sig), SR)
    assert "low_snr" in q.flags


def test_clipping_flag() -> None:
    sig = [max(-32768, min(32767, s)) for s in tone(1.0, 60000)]
    q = estimate_quality(pcm(sig), SR)
    assert "clipping" in q.flags and q.clipping_ratio > 0.01


def test_too_quiet_and_mostly_silence() -> None:
    q = estimate_quality(pcm(noise(2.0, 20)), SR)
    assert "too_quiet" in q.flags and "mostly_silence" in q.flags
    assert q.silence_ratio > 0.9


def test_short_audio_snr_unknown() -> None:
    q = estimate_quality(pcm(tone(0.05, 8000)), SR)
    assert "snr_unknown" in q.flags and q.estimated_snr_db is None


def test_odd_byte_count_tolerated() -> None:
    q = estimate_quality(pcm(tone(1.0, 5000)) + b"\x01", SR)
    assert q.duration_seconds == pytest.approx(1.0)
