from __future__ import annotations

import ast
import dataclasses
import importlib
import itertools
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.providers.schemas import UNKNOWN_SPEAKER_LABEL

BACKEND = Path(__file__).resolve().parent.parent
PACKAGE_DIR = BACKEND / "speech_metrics"
REPO_ROOT = BACKEND.parent

REASON_CODES = [
    "duration_unknown",
    "zero_length_call",
    "no_speech",
    "diarization_unavailable",
    "too_much_unattributed_speech",
    "word_counts_missing",
    "too_little_speech",
    "no_attributed_speech",
]

CALL_METRICS = [
    "call_duration_ms",
    "speech_ms",
    "silence_ms",
    "speech_ratio",
    "silence_ratio",
    "attributed_speech_ms",
    "unattributed_speech_ms",
    "overlap_ms",
    "overlap_ratio_of_speech",
    "speaking_rate_wpm",
]

SPEAKER_METRICS = ["talk_ms", "talk_share", "talk_ratio_of_call", "speaking_rate_wpm"]

UNITS = {
    "call_duration_ms": "ms",
    "speech_ms": "ms",
    "silence_ms": "ms",
    "speech_ratio": "ratio",
    "silence_ratio": "ratio",
    "attributed_speech_ms": "ms",
    "unattributed_speech_ms": "ms",
    "overlap_ms": "ms",
    "overlap_ratio_of_speech": "ratio",
    "speaking_rate_wpm": "words_per_minute",
}

SPEAKER_UNITS = {
    "talk_ms": "ms",
    "talk_share": "ratio",
    "talk_ratio_of_call": "ratio",
    "speaking_rate_wpm": "words_per_minute",
}

FORBIDDEN_IMPORTS = [
    "backend.providers",
    "backend.database",
    "backend.routers",
    "sqlalchemy",
    "fastapi",
    "httpx",
]


def _sm():
    return importlib.import_module("backend.speech_metrics")


def _iv(start, end, speaker, words=None):
    return _sm().SpeechInterval(start_ms=start, end_ms=end, speaker_key=speaker, word_count=words)


def _run(intervals, duration, *, diarization=True, speakers=None):
    return _sm().compute_speech_metrics(
        intervals,
        call_duration_ms=duration,
        diarization_available=diarization,
        speakers=speakers,
    )


def _ok(m, value, unit, numerator=None, denominator=None):
    assert m.status == "computed"
    assert m.reason is None
    assert m.unit == unit
    assert m.value == value
    if unit == "ms":
        assert type(m.value) is int
        assert m.numerator == value
        assert m.denominator is None
    else:
        assert type(m.value) is float
    if numerator is not None:
        assert m.numerator == numerator
    if denominator is not None:
        assert m.denominator == denominator


def _off(m, reason, unit):
    assert m.status == "not_computable"
    assert m.value is None
    assert m.unit == unit
    if isinstance(reason, set):
        assert m.reason in reason
    else:
        assert m.reason == reason
    assert m.numerator is None
    assert m.denominator is None


def _reconcile(r, *, multi_way_overlap=False):
    d, speech, silence = r.call_duration_ms, r.speech_ms, r.silence_ms
    assert speech.status == "computed"
    if d.status == "computed" and silence.status == "computed":
        assert speech.value + silence.value == d.value
    attributed, unattributed, overlap = (
        r.attributed_speech_ms,
        r.unattributed_speech_ms,
        r.overlap_ms,
    )
    assert attributed.status == "computed"
    assert unattributed.status == "computed"
    assert overlap.status == "computed"
    assert attributed.value + unattributed.value == speech.value
    assert overlap.value <= attributed.value
    talk = [s.talk_ms.value for s in r.speakers.values()]
    assert all(s.talk_ms.status == "computed" for s in r.speakers.values())
    assert max(talk) <= attributed.value <= sum(talk)
    assert attributed.value <= sum(talk) - overlap.value
    if multi_way_overlap:
        assert sum(talk) - overlap.value > attributed.value
    else:
        assert sum(talk) - overlap.value == attributed.value
    if len(talk) == 2:
        assert sum(talk) - overlap.value == attributed.value


def _json_safe(value):
    if value is None or type(value) in (str, int, float):
        return True
    if isinstance(value, list):
        return all(_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(type(k) is str and _json_safe(v) for k, v in value.items())
    return False


def test_version_and_error_type():
    sm = _sm()
    assert sm.METRICS_VERSION == "speech-metrics/1"
    assert issubclass(sm.SpeechMetricsError, ValueError)
    r = _run([], 1000)
    assert r.version == "speech-metrics/1"


def test_speech_interval_is_a_frozen_dataclass():
    iv = _iv(0, 10, "A", 1)
    assert dataclasses.is_dataclass(iv)
    with pytest.raises(dataclasses.FrozenInstanceError):
        iv.start_ms = 5
    assert iv == _iv(0, 10, "A", 1)
    assert _sm().SpeechInterval(0, 10, "A").word_count is None


def test_no_intervals():
    r = _run([], 60000)
    _ok(r.call_duration_ms, 60000, "ms")
    _ok(r.speech_ms, 0, "ms")
    _ok(r.silence_ms, 60000, "ms")
    _ok(r.speech_ratio, 0.0, "ratio", 0, 60000)
    _ok(r.silence_ratio, 1.0, "ratio", 60000, 60000)
    _ok(r.attributed_speech_ms, 0, "ms")
    _ok(r.unattributed_speech_ms, 0, "ms")
    _ok(r.overlap_ms, 0, "ms")
    _off(r.overlap_ratio_of_speech, "no_speech", "ratio")
    _off(r.speaking_rate_wpm, "no_speech", "words_per_minute")
    assert r.speakers == {}
    assert type(r.input_summary) is dict
    assert r.input_summary == {
        "interval_count": 0,
        "ignored_zero_length_count": 0,
        "clipped_interval_count": 0,
        "unknown_interval_count": 0,
    }


def test_no_speech_with_declared_speaker():
    r = _run([], 60000, speakers=["A"])
    assert list(r.speakers) == ["A"]
    a = r.speakers["A"]
    _ok(a.talk_ms, 0, "ms")
    _off(a.talk_share, "no_speech", "ratio")
    _ok(a.talk_ratio_of_call, 0.0, "ratio", 0, 60000)
    _off(a.speaking_rate_wpm, "no_speech", "words_per_minute")
    _ok(r.overlap_ms, 0, "ms")
    _ok(r.attributed_speech_ms, 0, "ms")
    _ok(r.unattributed_speech_ms, 0, "ms")
    _ok(r.silence_ms, 60000, "ms")
    _ok(r.silence_ratio, 1.0, "ratio", 60000, 60000)
    _off(r.overlap_ratio_of_speech, "no_speech", "ratio")
    _off(r.speaking_rate_wpm, "no_speech", "words_per_minute")


def test_no_speech_with_unknown_duration_and_declared_speaker():
    r = _run([], None, speakers=["A"])
    _off(r.speakers["A"].talk_ratio_of_call, "duration_unknown", "ratio")
    _off(r.speakers["A"].talk_share, "no_speech", "ratio")
    _ok(r.speakers["A"].talk_ms, 0, "ms")


def test_no_intervals_accepts_a_generator_and_empty_iterables():
    r = _run(iter([]), 1000, speakers=iter([]))
    _ok(r.speech_ms, 0, "ms")
    assert r.speakers == {}


def test_one_speaker():
    r = _run([_iv(10000, 40000, "A", 50), _iv(50000, 80000, "A", 40)], 120000)
    _ok(r.call_duration_ms, 120000, "ms")
    _ok(r.speech_ms, 60000, "ms")
    _ok(r.silence_ms, 60000, "ms")
    _ok(r.speech_ratio, 0.5, "ratio", 60000, 120000)
    _ok(r.silence_ratio, 0.5, "ratio", 60000, 120000)
    _ok(r.attributed_speech_ms, 60000, "ms")
    _ok(r.unattributed_speech_ms, 0, "ms")
    _ok(r.overlap_ms, 0, "ms")
    _ok(r.overlap_ratio_of_speech, 0.0, "ratio", 0, 60000)
    _ok(r.speaking_rate_wpm, 90.0, "words_per_minute")
    assert list(r.speakers) == ["A"]
    a = r.speakers["A"]
    _ok(a.talk_ms, 60000, "ms")
    _ok(a.talk_share, 1.0, "ratio", 60000, 60000)
    _ok(a.talk_ratio_of_call, 0.5, "ratio", 60000, 120000)
    _ok(a.speaking_rate_wpm, 90.0, "words_per_minute")
    assert r.input_summary == {
        "interval_count": 2,
        "ignored_zero_length_count": 0,
        "clipped_interval_count": 0,
        "unknown_interval_count": 0,
    }


def _two_speaker_intervals():
    return [
        _iv(0, 30000, "A", 60),
        _iv(50000, 70000, "A", 30),
        _iv(20000, 45000, "B", 40),
    ]


def test_two_speakers_with_overlap():
    r = _run(_two_speaker_intervals(), 100000)
    _ok(r.speech_ms, 65000, "ms")
    _ok(r.silence_ms, 35000, "ms")
    _ok(r.speech_ratio, 0.65, "ratio", 65000, 100000)
    _ok(r.silence_ratio, 0.35, "ratio", 35000, 100000)
    _ok(r.attributed_speech_ms, 65000, "ms")
    _ok(r.unattributed_speech_ms, 0, "ms")
    _ok(r.overlap_ms, 10000, "ms")
    _ok(r.overlap_ratio_of_speech, 0.1538, "ratio", 10000, 65000)
    _ok(r.speaking_rate_wpm, 120.0, "words_per_minute", 130, 65000)
    assert list(r.speakers) == ["A", "B"]
    a, b = r.speakers["A"], r.speakers["B"]
    _ok(a.talk_ms, 50000, "ms")
    _ok(b.talk_ms, 25000, "ms")
    _ok(a.talk_share, 0.6667, "ratio", 50000, 75000)
    _ok(b.talk_share, 0.3333, "ratio", 25000, 75000)
    _ok(a.talk_ratio_of_call, 0.5, "ratio", 50000, 100000)
    _ok(b.talk_ratio_of_call, 0.25, "ratio", 25000, 100000)
    _ok(a.speaking_rate_wpm, 108.0, "words_per_minute", 90, 50000)
    _ok(b.speaking_rate_wpm, 96.0, "words_per_minute", 40, 25000)
    _reconcile(r)


def test_three_speakers_all_overlapping_at_once():
    r = _run(
        [_iv(0, 30000, "A", 50), _iv(10000, 40000, "B", 50), _iv(20000, 50000, "C", 50)],
        60000,
    )
    _ok(r.speech_ms, 50000, "ms")
    _ok(r.silence_ms, 10000, "ms")
    _ok(r.speech_ratio, 0.8333, "ratio", 50000, 60000)
    _ok(r.silence_ratio, 0.1667, "ratio", 10000, 60000)
    _ok(r.attributed_speech_ms, 50000, "ms")
    _ok(r.unattributed_speech_ms, 0, "ms")
    _ok(r.overlap_ms, 30000, "ms")
    _ok(r.overlap_ratio_of_speech, 0.6, "ratio", 30000, 50000)
    _ok(r.speaking_rate_wpm, 180.0, "words_per_minute")
    assert list(r.speakers) == ["A", "B", "C"]
    for key in ("A", "B", "C"):
        s = r.speakers[key]
        _ok(s.talk_ms, 30000, "ms")
        _ok(s.talk_share, 0.3333, "ratio", 30000, 90000)
        _ok(s.talk_ratio_of_call, 0.5, "ratio", 30000, 60000)
        _ok(s.speaking_rate_wpm, 100.0, "words_per_minute")
    assert sum(s.talk_share.value for s in r.speakers.values()) != 1.0
    _reconcile(r, multi_way_overlap=True)


def test_same_speaker_self_overlap_and_touching_are_not_overlap():
    r = _run(
        [
            _iv(0, 20000, "A", 30),
            _iv(10000, 30000, "A", 30),
            _iv(30000, 35000, "A", 5),
            _iv(35000, 45000, "B", 10),
        ],
        60000,
    )
    _ok(r.speech_ms, 45000, "ms")
    _ok(r.silence_ms, 15000, "ms")
    _ok(r.speech_ratio, 0.75, "ratio", 45000, 60000)
    _ok(r.attributed_speech_ms, 45000, "ms")
    _ok(r.overlap_ms, 0, "ms")
    _ok(r.overlap_ratio_of_speech, 0.0, "ratio", 0, 45000)
    _ok(r.speaking_rate_wpm, 100.0, "words_per_minute")
    a, b = r.speakers["A"], r.speakers["B"]
    _ok(a.talk_ms, 35000, "ms")
    _ok(b.talk_ms, 10000, "ms")
    _ok(a.talk_share, 0.7778, "ratio", 35000, 45000)
    _ok(b.talk_share, 0.2222, "ratio", 10000, 45000)
    _ok(a.talk_ratio_of_call, 0.5833, "ratio", 35000, 60000)
    _ok(b.talk_ratio_of_call, 0.1667, "ratio", 10000, 60000)
    _ok(a.speaking_rate_wpm, 111.4, "words_per_minute")
    _ok(b.speaking_rate_wpm, 60.0, "words_per_minute")
    _reconcile(r)


def test_unknown_speech_under_known_speech_is_not_unattributed_or_overlap():
    r = _run(
        [
            _iv(0, 30000, "A", 30),
            _iv(20000, 40000, UNKNOWN_SPEAKER_LABEL, 20),
            _iv(40000, 60000, "B", 20),
        ],
        60000,
    )
    _ok(r.speech_ms, 60000, "ms")
    _ok(r.silence_ms, 0, "ms")
    _ok(r.speech_ratio, 1.0, "ratio", 60000, 60000)
    _ok(r.silence_ratio, 0.0, "ratio", 0, 60000)
    _ok(r.attributed_speech_ms, 50000, "ms")
    _ok(r.unattributed_speech_ms, 10000, "ms")
    _ok(r.overlap_ms, 0, "ms")
    _ok(r.overlap_ratio_of_speech, 0.0, "ratio", 0, 60000)
    _ok(r.speaking_rate_wpm, 70.0, "words_per_minute")
    assert list(r.speakers) == ["A", "B"]
    _ok(r.speakers["A"].talk_share, 0.6, "ratio", 30000, 50000)
    _ok(r.speakers["B"].talk_share, 0.4, "ratio", 20000, 50000)
    assert r.input_summary["unknown_interval_count"] == 1
    _reconcile(r)


@pytest.mark.parametrize(
    ("unknown_start", "computed"),
    [(80000, True), (79999, False), (70000, False), (90000, True)],
)
def test_unattributed_guard_boundary(unknown_start, computed):
    r = _run(
        [
            _iv(0, 40000, "A"),
            _iv(40000, unknown_start, "B"),
            _iv(unknown_start, 100000, None),
        ],
        100000,
    )
    b_ms = unknown_start - 40000
    _ok(r.speech_ms, 100000, "ms")
    _ok(r.attributed_speech_ms, unknown_start, "ms")
    _ok(r.unattributed_speech_ms, 100000 - unknown_start, "ms")
    _ok(r.speakers["A"].talk_ms, 40000, "ms")
    _ok(r.speakers["B"].talk_ms, b_ms, "ms")
    _ok(r.speakers["A"].talk_ratio_of_call, 0.4, "ratio", 40000, 100000)
    if computed:
        expected = {80000: (0.5, 0.5), 90000: (0.4444, 0.5556)}[unknown_start]
        _ok(r.speakers["A"].talk_share, expected[0], "ratio", 40000, unknown_start)
        _ok(r.speakers["B"].talk_share, expected[1], "ratio", b_ms, unknown_start)
    else:
        _off(r.speakers["A"].talk_share, "too_much_unattributed_speech", "ratio")
        _off(r.speakers["B"].talk_share, "too_much_unattributed_speech", "ratio")
    _reconcile(r)


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "   ",
        "unknown",
        "Unknown",
        " UNKNOWN ",
        "\tunknown\n",
        "speaker_unknown",
        "SPEAKER_UNKNOWN",
        "  Speaker_Unknown\t",
        "ſpeaker_unknown",
        UNKNOWN_SPEAKER_LABEL,
    ],
)
def test_unknown_speaker_spellings(key):
    r = _run([_iv(0, 10000, "A", 10), _iv(10000, 12000, key, 2)], 20000)
    assert list(r.speakers) == ["A"]
    assert r.input_summary["unknown_interval_count"] == 1
    _ok(r.attributed_speech_ms, 10000, "ms")
    _ok(r.unattributed_speech_ms, 2000, "ms")
    _ok(r.speech_ms, 12000, "ms")


def test_pipeline_unknown_label_is_unknown():
    r = _run([_iv(0, 10000, UNKNOWN_SPEAKER_LABEL, 5)], 20000, speakers=[UNKNOWN_SPEAKER_LABEL])
    assert r.speakers == {}
    assert r.input_summary["unknown_interval_count"] == 1
    _ok(r.attributed_speech_ms, 0, "ms")
    _ok(r.unattributed_speech_ms, 10000, "ms")


@pytest.mark.parametrize(
    ("key", "normalized"),
    [
        ("speaker_unknownx", "speaker_unknownx"),
        ("Unknown Caller", "Unknown Caller"),
        ("unknown_1", "unknown_1"),
        ("xunknown", "xunknown"),
        ("  speaker_2 ", "speaker_2"),
        ("B", "B"),
    ],
)
def test_known_speaker_controls(key, normalized):
    r = _run([_iv(0, 10000, "A", 10), _iv(10000, 12000, key, 2)], 20000)
    assert list(r.speakers) == sorted(["A", normalized])
    assert r.input_summary["unknown_interval_count"] == 0
    _ok(r.attributed_speech_ms, 12000, "ms")
    _ok(r.unattributed_speech_ms, 0, "ms")
    _ok(r.speakers[normalized].talk_ms, 2000, "ms")


def test_known_keys_strip_but_keep_case():
    r = _run(
        [_iv(0, 10000, "A", 10), _iv(5000, 15000, " A ", 10), _iv(20000, 30000, "a", 10)],
        40000,
    )
    assert list(r.speakers) == ["A", "a"]
    _ok(r.speakers["A"].talk_ms, 15000, "ms")
    _ok(r.speakers["a"].talk_ms, 10000, "ms")
    _ok(r.overlap_ms, 0, "ms")
    _ok(r.attributed_speech_ms, 25000, "ms")
    _ok(r.speakers["A"].speaking_rate_wpm, 80.0, "words_per_minute")
    _ok(r.speakers["a"].speaking_rate_wpm, 60.0, "words_per_minute")
    _reconcile(r)


def test_declared_silent_speaker():
    r = _run(
        [_iv(0, 30000, "A", 30)],
        60000,
        speakers=[" B ", "unknown", "", "  ", "Speaker_Unknown", "A"],
    )
    assert list(r.speakers) == ["A", "B"]
    a, b = r.speakers["A"], r.speakers["B"]
    _ok(b.talk_ms, 0, "ms")
    _ok(b.talk_share, 0.0, "ratio", 0, 30000)
    _ok(b.talk_ratio_of_call, 0.0, "ratio", 0, 60000)
    _off(b.speaking_rate_wpm, "too_little_speech", "words_per_minute")
    _ok(a.talk_share, 1.0, "ratio", 30000, 30000)
    _ok(a.speaking_rate_wpm, 60.0, "words_per_minute")
    _reconcile(r)


def test_declared_speaker_with_only_unknown_speech_has_no_attributed_speech():
    r = _run([_iv(0, 10000, "unknown", 10)], 20000, speakers=["A"])
    assert list(r.speakers) == ["A"]
    _ok(r.speakers["A"].talk_ms, 0, "ms")
    _off(r.speakers["A"].talk_share, "no_attributed_speech", "ratio")
    _ok(r.speakers["A"].talk_ratio_of_call, 0.0, "ratio", 0, 20000)
    _ok(r.attributed_speech_ms, 0, "ms")
    _ok(r.unattributed_speech_ms, 10000, "ms")


def test_diarization_off():
    r = _run(
        [_iv(0, 30000, "A", 30), _iv(20000, 40000, "B", 30)],
        60000,
        diarization=False,
        speakers=["C", "A"],
    )
    assert r.speakers == {}
    _ok(r.speech_ms, 40000, "ms")
    _ok(r.silence_ms, 20000, "ms")
    _ok(r.speech_ratio, 0.6667, "ratio", 40000, 60000)
    _ok(r.silence_ratio, 0.3333, "ratio", 20000, 60000)
    _ok(r.speaking_rate_wpm, 90.0, "words_per_minute", 60, 40000)
    _off(r.attributed_speech_ms, "diarization_unavailable", "ms")
    _off(r.unattributed_speech_ms, "diarization_unavailable", "ms")
    _off(r.overlap_ms, "diarization_unavailable", "ms")
    _off(r.overlap_ratio_of_speech, "diarization_unavailable", "ratio")
    assert r.input_summary["unknown_interval_count"] == 2


def test_diarization_off_precedence_with_unknown_duration_and_no_speech():
    r = _run([], None, diarization=False, speakers=["A"])
    assert r.speakers == {}
    _ok(r.speech_ms, 0, "ms")
    _off(r.call_duration_ms, "duration_unknown", "ms")
    _off(r.silence_ms, "duration_unknown", "ms")
    _off(r.speech_ratio, "duration_unknown", "ratio")
    _off(r.silence_ratio, "duration_unknown", "ratio")
    _off(r.overlap_ratio_of_speech, "diarization_unavailable", "ratio")
    _off(r.overlap_ms, "diarization_unavailable", "ms")
    _off(r.attributed_speech_ms, "diarization_unavailable", "ms")


def test_clipping_past_duration_and_interval_wholly_past():
    r = _run(
        [_iv(0, 10000, "B", 10), _iv(50000, 70000, "A", 20), _iv(60000, 65000, "A", None)],
        60000,
    )
    assert r.input_summary["clipped_interval_count"] == 2
    assert r.input_summary["interval_count"] == 3
    assert r.input_summary["ignored_zero_length_count"] == 0
    _ok(r.speech_ms, 20000, "ms")
    _ok(r.silence_ms, 40000, "ms")
    _ok(r.speech_ratio, 0.3333, "ratio", 20000, 60000)
    _ok(r.speakers["A"].talk_ms, 10000, "ms")
    _ok(r.speakers["A"].speaking_rate_wpm, 120.0, "words_per_minute", 20, 10000)
    _ok(r.speakers["B"].speaking_rate_wpm, 60.0, "words_per_minute", 10, 10000)
    _ok(r.speaking_rate_wpm, 90.0, "words_per_minute", 30, 20000)
    _reconcile(r)


@pytest.mark.parametrize(
    ("end", "clipped", "speech"),
    [(60000, 0, 10000), (59999, 0, 9999), (60001, 1, 10000)],
)
def test_clipping_boundary(end, clipped, speech):
    r = _run([_iv(50000, end, "A")], 60000)
    assert r.input_summary["clipped_interval_count"] == clipped
    _ok(r.speech_ms, speech, "ms")
    _ok(r.silence_ms, 60000 - speech, "ms")


def test_no_clipping_when_duration_unknown():
    r = _run([_iv(50000, 70000, "A")], None)
    assert r.input_summary["clipped_interval_count"] == 0
    _ok(r.speech_ms, 20000, "ms")
    _ok(r.speakers["A"].talk_ms, 20000, "ms")


def test_zero_length_call():
    r = _run([], 0, speakers=["A"])
    _off(r.call_duration_ms, "zero_length_call", "ms")
    _off(r.silence_ms, "zero_length_call", "ms")
    _off(r.speech_ratio, "zero_length_call", "ratio")
    _off(r.silence_ratio, "zero_length_call", "ratio")
    _off(r.speakers["A"].talk_ratio_of_call, "zero_length_call", "ratio")
    _ok(r.speech_ms, 0, "ms")
    _ok(r.speakers["A"].talk_ms, 0, "ms")


def test_zero_length_call_with_intervals_keeps_duration_metrics_not_computable():
    r = _run([_iv(0, 1000, "A", 1)], 0)
    _off(r.call_duration_ms, "zero_length_call", "ms")
    _off(r.silence_ms, "zero_length_call", "ms")
    _off(r.speech_ratio, "zero_length_call", "ratio")
    _off(r.silence_ratio, "zero_length_call", "ratio")
    _off(r.speakers["A"].talk_ratio_of_call, "zero_length_call", "ratio")


def test_unknown_duration():
    r = _run([_iv(0, 30000, "A", 30), _iv(30000, 60000, "B", 30)], None)
    _off(r.call_duration_ms, "duration_unknown", "ms")
    _off(r.silence_ms, "duration_unknown", "ms")
    _off(r.speech_ratio, "duration_unknown", "ratio")
    _off(r.silence_ratio, "duration_unknown", "ratio")
    _ok(r.speech_ms, 60000, "ms")
    _ok(r.overlap_ms, 0, "ms")
    _ok(r.overlap_ratio_of_speech, 0.0, "ratio", 0, 60000)
    _ok(r.speaking_rate_wpm, 60.0, "words_per_minute")
    for key in ("A", "B"):
        s = r.speakers[key]
        _off(s.talk_ratio_of_call, "duration_unknown", "ratio")
        _ok(s.talk_share, 0.5, "ratio", 30000, 60000)
        _ok(s.speaking_rate_wpm, 60.0, "words_per_minute")
    _reconcile(r)


def test_missing_word_counts():
    r = _run(
        [
            _iv(0, 10000, "A", None),
            _iv(10000, 20000, "B", 10),
            _iv(30000, 30000, "B", None),
            _iv(20000, 21000, "C", None),
        ],
        40000,
    )
    assert r.input_summary["ignored_zero_length_count"] == 1
    _off(r.speaking_rate_wpm, "word_counts_missing", "words_per_minute")
    _off(r.speakers["A"].speaking_rate_wpm, "word_counts_missing", "words_per_minute")
    _ok(r.speakers["B"].speaking_rate_wpm, 60.0, "words_per_minute")
    _off(r.speakers["C"].speaking_rate_wpm, "word_counts_missing", "words_per_minute")
    _ok(r.speakers["B"].talk_ms, 10000, "ms")


def test_word_counts_from_ignored_zero_length_interval_do_not_count():
    r = _run([_iv(0, 6000, "A", 6), _iv(3000, 3000, "A", 1000)], None)
    _ok(r.speaking_rate_wpm, 60.0, "words_per_minute", 6, 6000)
    _ok(r.speakers["A"].speaking_rate_wpm, 60.0, "words_per_minute", 6, 6000)
    assert r.input_summary["ignored_zero_length_count"] == 1


def test_overlapping_words_are_summed_not_deduplicated():
    r = _run([_iv(0, 10000, "A", 10), _iv(0, 10000, "B", 10)], None)
    _ok(r.speech_ms, 10000, "ms")
    _ok(r.speaking_rate_wpm, 120.0, "words_per_minute", 20, 10000)
    _ok(r.overlap_ms, 10000, "ms")
    _ok(r.overlap_ratio_of_speech, 1.0, "ratio", 10000, 10000)
    _reconcile(r)


@pytest.mark.parametrize(
    ("end", "words", "expected"),
    [(4999, 10, None), (5000, 10, 120.0), (6000, 0, 0.0)],
)
def test_rate_threshold(end, words, expected):
    r = _run([_iv(0, end, "A", words)], None)
    for m in (r.speaking_rate_wpm, r.speakers["A"].speaking_rate_wpm):
        if expected is None:
            _off(m, "too_little_speech", "words_per_minute")
        else:
            _ok(m, expected, "words_per_minute", words, end)


@pytest.mark.parametrize(
    ("speech", "speech_ratio", "silence_ratio"),
    [
        (1, 0.0, 1.0),
        (3, 0.0002, 0.9998),
        (5, 0.0002, 0.9998),
        (7, 0.0004, 0.9996),
    ],
)
def test_ratio_rounding_half_even(speech, speech_ratio, silence_ratio):
    r = _run([_iv(0, speech, "A")], 20000)
    _ok(r.speech_ratio, speech_ratio, "ratio", speech, 20000)
    _ok(r.silence_ratio, silence_ratio, "ratio", 20000 - speech, 20000)


@pytest.mark.parametrize(("words", "expected"), [(41, 10.2), (43, 10.8), (42, 10.5)])
def test_rate_rounding_half_even(words, expected):
    r = _run([_iv(0, 240000, "A", words)], None)
    _ok(r.speaking_rate_wpm, expected, "words_per_minute", words, 240000)
    _ok(r.speakers["A"].speaking_rate_wpm, expected, "words_per_minute", words, 240000)


def _mixed_intervals():
    return [
        _iv(0, 30000, "A", 60),
        _iv(50000, 70000, " A ", 30),
        _iv(20000, 45000, "B", 40),
        _iv(44000, 52000, "speaker_unknown", 5),
        _iv(90000, 130000, "C", 12),
        _iv(140000, 150000, "C", 3),
        _iv(60000, 60000, "B", 7),
        _iv(65000, 80000, "B", 20),
    ]


def test_input_order_does_not_change_as_dict():
    base = _mixed_intervals()
    expected = json.dumps(_run(base, 120000, speakers=["D", "A"]).as_dict(), sort_keys=True)
    for order in itertools.islice(itertools.permutations(base), 0, None, 997):
        got = _run(list(order), 120000, speakers=["A", "D"]).as_dict()
        assert json.dumps(got, sort_keys=True) == expected
    reversed_run = _run(iter(reversed(base)), 120000, speakers=iter(["D", "A"]))
    assert json.dumps(reversed_run.as_dict(), sort_keys=True) == expected


def test_mixed_fixture_values_and_identities():
    r = _run(_mixed_intervals(), 120000, speakers=["D", "A"])
    assert list(r.speakers) == ["A", "B", "C", "D"]
    assert r.input_summary["clipped_interval_count"] == 2
    assert r.input_summary["ignored_zero_length_count"] == 1
    assert r.input_summary["unknown_interval_count"] == 1
    assert r.input_summary["interval_count"] == 8
    _ok(r.speech_ms, 110000, "ms")
    _ok(r.silence_ms, 10000, "ms")
    _ok(r.attributed_speech_ms, 105000, "ms")
    _ok(r.unattributed_speech_ms, 5000, "ms")
    _ok(r.overlap_ms, 15000, "ms")
    _ok(r.overlap_ratio_of_speech, 0.1364, "ratio", 15000, 110000)
    _ok(r.speakers["A"].talk_ms, 50000, "ms")
    _ok(r.speakers["B"].talk_ms, 40000, "ms")
    _ok(r.speakers["C"].talk_ms, 30000, "ms")
    _ok(r.speakers["D"].talk_ms, 0, "ms")
    _ok(r.speakers["A"].talk_share, 0.4167, "ratio", 50000, 120000)
    _ok(r.speakers["B"].talk_share, 0.3333, "ratio", 40000, 120000)
    _ok(r.speakers["C"].talk_share, 0.25, "ratio", 30000, 120000)
    _ok(r.speakers["D"].talk_share, 0.0, "ratio", 0, 120000)
    _ok(r.speakers["C"].speaking_rate_wpm, 24.0, "words_per_minute", 12, 30000)
    _ok(r.speakers["B"].speaking_rate_wpm, 90.0, "words_per_minute", 60, 40000)
    _ok(r.speakers["A"].speaking_rate_wpm, 108.0, "words_per_minute", 90, 50000)
    _ok(r.speaking_rate_wpm, 91.1, "words_per_minute", 167, 110000)
    _reconcile(r)


def test_same_input_twice_gives_the_same_result():
    a = _run(_mixed_intervals(), 120000, speakers=["D"]).as_dict()
    b = _run(_mixed_intervals(), 120000, speakers=["D"]).as_dict()
    assert a == b


def test_as_dict_is_json_safe_and_uses_the_same_names():
    r = _run(_two_speaker_intervals(), 100000)
    d = r.as_dict()
    assert type(d) is dict
    assert _json_safe(d)
    json.dumps(d)
    assert d["version"] == "speech-metrics/1"
    for name in CALL_METRICS:
        m = d[name]
        assert m["status"] == "computed"
        assert m["unit"] == UNITS[name]
        assert m["value"] == getattr(r, name).value
        assert set(m) >= {"status", "value", "unit", "reason", "numerator", "denominator"}
    assert list(d["speakers"]) == ["A", "B"]
    for key in ("A", "B"):
        for name in SPEAKER_METRICS:
            assert d["speakers"][key][name]["value"] == getattr(r.speakers[key], name).value
    assert d["input_summary"] == r.input_summary
    off = _run([], None, diarization=False).as_dict()
    assert off["call_duration_ms"]["value"] is None
    assert off["call_duration_ms"]["reason"] == "duration_unknown"
    assert off["speakers"] == {}
    assert _json_safe(off)


def test_units_on_every_measurement():
    r = _run(_two_speaker_intervals(), 100000)
    for name, unit in UNITS.items():
        assert getattr(r, name).unit == unit
    for s in r.speakers.values():
        for name, unit in SPEAKER_UNITS.items():
            assert getattr(s, name).unit == unit
    off = _run([], None, diarization=False)
    for name, unit in UNITS.items():
        assert getattr(off, name).unit == unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("   \t\n", 0),
        ("hello world", 2),
        ("hello - world ...", 2),
        ("- ... —", 0),
        ("می‌خواهم بروم", 2),
        ("می‌خواهم", 1),
        ("‌", 0),
        ("a b", 2),
        ("a　b", 2),
        ("۱۲۳", 1),
        ("it's  ok!", 2),
        ("سلام\nدنیا", 2),
    ],
)
def test_count_words(text, expected):
    assert _sm().count_words(text) == expected


def test_intervals_from_segments():
    sm = _sm()
    segments = [
        SimpleNamespace(start_ms=0, end_ms=1000, speaker_key="A", text="سلام دنیا"),
        SimpleNamespace(start_ms=1000, end_ms=2000, speaker_key="B", text=None),
        SimpleNamespace(start_ms=2000, end_ms=3000, speaker_key="A", text=""),
        SimpleNamespace(start_ms=3000, end_ms=4000, speaker_key="A", text="a - b"),
    ]
    out = sm.intervals_from_segments(iter(segments))
    assert isinstance(out, list)
    assert all(isinstance(iv, sm.SpeechInterval) for iv in out)
    assert [(iv.start_ms, iv.end_ms, iv.word_count) for iv in out] == [
        (0, 1000, 2),
        (1000, 2000, None),
        (2000, 3000, 0),
        (3000, 4000, 2),
    ]
    assert out[0].speaker_key == "A"
    r = _run(out, 4000)
    _ok(r.speech_ms, 4000, "ms")
    _off(r.speaking_rate_wpm, "word_counts_missing", "words_per_minute")


@pytest.mark.parametrize(
    ("start", "end", "words"),
    [
        (-1, 10, None),
        (10, 9, None),
        (1.5, 10, None),
        (True, 10, None),
        (0, 10.0, None),
        (0, True, None),
        ("0", 10, None),
        (None, 10, None),
        (0, 10, -1),
        (0, 10, 1.5),
    ],
)
def test_invalid_interval_raises(start, end, words):
    sm = _sm()
    bad = _iv(start, end, "A", words)
    with pytest.raises(sm.SpeechMetricsError) as excinfo:
        _run([_iv(0, 10, "A", 1), bad], 1000)
    assert excinfo.value.code == "invalid_interval"
    assert isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize(
    ("start", "end", "words"),
    [(0, 10, None), (10, 10, None), (1, 10, None), (0, 10, 0), (0, 1, 1)],
)
def test_valid_interval_controls(start, end, words):
    r = _run([_iv(start, end, "A", words)], 1000)
    _ok(r.speech_ms, end - start, "ms")
    assert r.input_summary["ignored_zero_length_count"] == (1 if end == start else 0)


@pytest.mark.parametrize("key", [5, b"A", ["A"], 1.0, True])
def test_non_str_speaker_key_raises(key):
    sm = _sm()
    bad = _iv(0, 10, key)
    with pytest.raises(sm.SpeechMetricsError) as excinfo:
        _run([bad], 1000)
    assert excinfo.value.code == "invalid_interval"


@pytest.mark.parametrize("key", [None, "A", "", "5"])
def test_str_or_none_speaker_key_controls(key):
    r = _run([_iv(0, 10, key)], 1000)
    _ok(r.speech_ms, 10, "ms")


@pytest.mark.parametrize("speakers", ["A", "AB", ["A", 1], ["A", None], [b"A"], (None,)])
def test_invalid_speakers_raises(speakers):
    sm = _sm()
    with pytest.raises(sm.SpeechMetricsError) as excinfo:
        _run([_iv(0, 10, "A")], 1000, speakers=speakers)
    assert excinfo.value.code == "invalid_speakers"


@pytest.mark.parametrize(
    ("speakers", "keys"),
    [
        (None, ["A"]),
        ([], ["A"]),
        (["A"], ["A"]),
        (("B",), ["A", "B"]),
        ({"B"}, ["A", "B"]),
        (frozenset({"B", " "}), ["A", "B"]),
        (["B", "unknown", ""], ["A", "B"]),
    ],
)
def test_valid_speakers_controls(speakers, keys):
    r = _run([_iv(0, 10, "A")], 1000, speakers=speakers)
    assert list(r.speakers) == keys


def test_valid_speakers_generator_control():
    r = _run([_iv(0, 10, "A")], 1000, speakers=(k for k in ["C", "B"]))
    assert list(r.speakers) == ["A", "B", "C"]


def test_bad_values_do_not_raise_at_construction():
    sm = _sm()
    sm.SpeechInterval(-1, -5, 7, -1)
    sm.SpeechInterval(1.5, True, None, 1.5)


def test_known_key_only_on_dropped_intervals_does_not_join():
    r = _run(
        [
            _iv(0, 10000, "A", 10),
            _iv(5000, 5000, "X", 1),
            _iv(60000, 70000, "Y", 1),
            _iv(60000, 61000, "W", None),
        ],
        60000,
        speakers=["Z", "X"],
    )
    assert list(r.speakers) == ["A", "X", "Z"]
    _ok(r.speakers["X"].talk_ms, 0, "ms")
    _ok(r.speakers["Z"].talk_ms, 0, "ms")
    assert r.input_summary == {
        "interval_count": 4,
        "ignored_zero_length_count": 1,
        "clipped_interval_count": 2,
        "unknown_interval_count": 0,
    }
    _ok(r.speaking_rate_wpm, 60.0, "words_per_minute", 10, 10000)


def test_kept_key_after_clipping_joins():
    r = _run([_iv(59000, 70000, "Y", 1)], 60000)
    assert list(r.speakers) == ["Y"]
    _ok(r.speakers["Y"].talk_ms, 1000, "ms")


def test_unknown_interval_count_counts_kept_intervals_only():
    r = _run(
        [
            _iv(0, 10000, "A"),
            _iv(10000, 12000, "unknown"),
            _iv(3000, 3000, None),
            _iv(60000, 65000, "speaker_unknown"),
            _iv(59000, 61000, ""),
        ],
        60000,
    )
    assert r.input_summary == {
        "interval_count": 5,
        "ignored_zero_length_count": 1,
        "clipped_interval_count": 2,
        "unknown_interval_count": 2,
    }
    _ok(r.unattributed_speech_ms, 3000, "ms")


def test_unknown_interval_count_with_diarization_off_counts_every_kept_interval():
    r = _run(
        [
            _iv(0, 10000, "A"),
            _iv(10000, 12000, "B"),
            _iv(3000, 3000, "A"),
            _iv(60000, 65000, "B"),
            _iv(59000, 61000, "C"),
        ],
        60000,
        diarization=False,
    )
    assert r.input_summary == {
        "interval_count": 5,
        "ignored_zero_length_count": 1,
        "clipped_interval_count": 2,
        "unknown_interval_count": 3,
    }


def test_invalid_duration_raises():
    sm = _sm()
    with pytest.raises(sm.SpeechMetricsError) as excinfo:
        _run([_iv(0, 10, "A")], -1)
    assert excinfo.value.code == "invalid_duration"


@pytest.mark.parametrize("duration", [0, 1, None])
def test_valid_duration_controls(duration):
    r = _run([], duration)
    _ok(r.speech_ms, 0, "ms")


def test_not_computable_is_never_an_exception():
    r = _run([_iv(0, 1000, "A", None)], None, diarization=False)
    _off(r.call_duration_ms, "duration_unknown", "ms")
    _off(r.overlap_ms, "diarization_unavailable", "ms")
    _off(r.speaking_rate_wpm, "word_counts_missing", "words_per_minute")


def test_readme_states_reasons_and_definitions():
    readme = PACKAGE_DIR / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    for code in REASON_CODES:
        assert code in text
    for name in CALL_METRICS + SPEAKER_METRICS:
        assert name in text
    assert "speech-metrics/1" in text
    assert "0.2" in text
    assert "5000" in text
    assert "—" not in text


def test_package_imports_nothing_forbidden_at_runtime():
    code = (
        "import sys, json\nimport backend.speech_metrics\nprint(json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    bad = [m for m in loaded for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")]
    assert bad == []


def test_package_source_imports_only_stdlib():
    files = sorted(PACKAGE_DIR.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                ok = top in sys.stdlib_module_names or name.startswith("backend.speech_metrics")
                assert ok, f"{path.name} imports {name}"
