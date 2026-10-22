"""Speech, silence, talk, overlap and rate metrics from intervals (HOM-51).

Every fixture is synthetic and its expected values are computed by hand in the comments.
Each deny-case has its closest allow-control.
"""

from __future__ import annotations

import ast
import json
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.speech_metrics import (
    MAX_UNATTRIBUTED_SHARE,
    METRICS_VERSION,
    MIN_RATE_SPEECH_MS,
    REASON_CODES,
    Measurement,
    SpeakerMetrics,
    SpeechInterval,
    SpeechMetrics,
    SpeechMetricsError,
    compute_speech_metrics,
    count_words,
    intervals_from_segments,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "speech_metrics"

ZWNJ = "\u200c"


def iv(start: int, end: int, speaker: str | None = "a", words: int | None = 0) -> SpeechInterval:
    return SpeechInterval(start_ms=start, end_ms=end, speaker_key=speaker, word_count=words)


def compute(intervals, *, duration=None, diarization=True, speakers=None) -> SpeechMetrics:
    return compute_speech_metrics(
        intervals,
        call_duration_ms=duration,
        diarization_available=diarization,
        speakers=speakers,
    )


def computed(measurement: Measurement):
    assert measurement.status == "computed", measurement
    assert measurement.reason is None
    return measurement.value


def not_computable(measurement: Measurement) -> str:
    assert measurement.status == "not_computable", measurement
    assert measurement.value is None
    assert measurement.numerator is None and measurement.denominator is None
    return measurement.reason


def assert_reconciles(result: SpeechMetrics, *, duration: int | None) -> None:
    """Every identity of SPEC 3.3 whose parts are computed."""
    speech = computed(result.speech_ms)
    if result.silence_ms.status == "computed":
        assert speech + result.silence_ms.value == duration
    if result.attributed_speech_ms.status != "computed":
        return
    attributed = result.attributed_speech_ms.value
    assert attributed + computed(result.unattributed_speech_ms) == speech
    overlap = computed(result.overlap_ms)
    assert overlap <= attributed
    talks = [computed(speaker.talk_ms) for speaker in result.speakers.values()]
    if talks:
        assert max(talks) <= attributed <= sum(talks) - overlap <= sum(talks)
        if len(talks) == 2:
            assert attributed == sum(talks) - overlap


# --- package constants ------------------------------------------------------------------


def test_version_and_constants() -> None:
    assert METRICS_VERSION == "speech-metrics/1"
    assert MAX_UNATTRIBUTED_SHARE == 0.2
    assert MIN_RATE_SPEECH_MS == 5000
    assert REASON_CODES == (
        "diarization_unavailable",
        "duration_unknown",
        "zero_length_call",
        "no_speech",
        "no_attributed_speech",
        "too_much_unattributed_speech",
        "word_counts_missing",
        "too_little_speech",
    )
    assert issubclass(SpeechMetricsError, ValueError)


def test_interval_is_frozen() -> None:
    interval = iv(0, 10)
    with pytest.raises(AttributeError):
        interval.start_ms = 5  # type: ignore[misc]
    assert SpeechInterval(1, 2, "a").word_count is None


# --- fixtures from SPEC A1 ----------------------------------------------------------------


def test_no_intervals() -> None:
    result = compute([], duration=60000, speakers=["a"])
    assert result.version == METRICS_VERSION
    assert computed(result.call_duration_ms) == 60000
    assert computed(result.speech_ms) == 0
    assert computed(result.silence_ms) == 60000
    assert computed(result.speech_ratio) == 0.0
    assert computed(result.silence_ratio) == 1.0
    # Revision 3: ms metrics stay a computed 0 with diarization on.
    assert computed(result.attributed_speech_ms) == 0
    assert computed(result.unattributed_speech_ms) == 0
    assert computed(result.overlap_ms) == 0
    assert not_computable(result.overlap_ratio_of_speech) == "no_speech"
    assert not_computable(result.speaking_rate_wpm) == "no_speech"
    a = result.speakers["a"]
    assert computed(a.talk_ms) == 0
    assert not_computable(a.talk_share) == "no_speech"
    assert computed(a.talk_ratio_of_call) == 0.0
    assert not_computable(a.speaking_rate_wpm) == "no_speech"
    assert result.input_summary == {
        "interval_count": 0,
        "ignored_zero_length_count": 0,
        "clipped_interval_count": 0,
        "unknown_interval_count": 0,
    }
    assert_reconciles(result, duration=60000)


def test_no_intervals_without_speakers() -> None:
    result = compute([], duration=60000)
    assert result.speakers == {}
    assert_reconciles(result, duration=60000)


def test_one_speaker() -> None:
    # a speaks [0, 30000) with 50 words in a 60000 ms call.
    result = compute([iv(0, 30000, "a", 50)], duration=60000)
    assert computed(result.speech_ms) == 30000
    assert computed(result.silence_ms) == 30000
    assert computed(result.speech_ratio) == 0.5
    assert computed(result.silence_ratio) == 0.5
    assert computed(result.attributed_speech_ms) == 30000
    assert computed(result.unattributed_speech_ms) == 0
    assert computed(result.overlap_ms) == 0
    assert computed(result.overlap_ratio_of_speech) == 0.0
    # 50 words / (30000 / 60000) min = 100.0
    assert computed(result.speaking_rate_wpm) == 100.0
    a = result.speakers["a"]
    assert computed(a.talk_ms) == 30000
    assert computed(a.talk_share) == 1.0
    assert computed(a.talk_ratio_of_call) == 0.5
    assert computed(a.speaking_rate_wpm) == 100.0
    assert_reconciles(result, duration=60000)


def test_two_speakers_with_overlap() -> None:
    # a [0, 10000) 20 words, b [8000, 20000) 30 words, call 30000 ms.
    result = compute([iv(0, 10000, "a", 20), iv(8000, 20000, "b", 30)], duration=30000)
    assert computed(result.speech_ms) == 20000  # union [0, 20000)
    assert computed(result.silence_ms) == 10000
    assert computed(result.speech_ratio) == 0.6667  # 20000 / 30000
    assert computed(result.silence_ratio) == 0.3333
    assert computed(result.attributed_speech_ms) == 20000
    assert computed(result.unattributed_speech_ms) == 0
    assert computed(result.overlap_ms) == 2000  # [8000, 10000)
    assert computed(result.overlap_ratio_of_speech) == 0.1  # 2000 / 20000
    # 50 words over 20000 ms: 50 * 60000 / 20000 = 150.0
    assert computed(result.speaking_rate_wpm) == 150.0
    a, b = result.speakers["a"], result.speakers["b"]
    assert computed(a.talk_ms) == 10000
    assert computed(b.talk_ms) == 12000
    assert computed(a.talk_share) == 0.4545  # 10000 / 22000
    assert computed(b.talk_share) == 0.5455  # 12000 / 22000
    assert computed(a.talk_ratio_of_call) == 0.3333  # 10000 / 30000
    assert computed(b.talk_ratio_of_call) == 0.4  # 12000 / 30000
    assert computed(a.speaking_rate_wpm) == 120.0  # 20 * 60000 / 10000
    assert computed(b.speaking_rate_wpm) == 150.0  # 30 * 60000 / 12000
    assert_reconciles(result, duration=30000)


def test_three_speakers_overlapping_at_once() -> None:
    # a [0, 6000), b [2000, 8000), c [4000, 10000), 10 words each, call 10000 ms.
    intervals = [iv(0, 6000, "a", 10), iv(2000, 8000, "b", 10), iv(4000, 10000, "c", 10)]
    result = compute(intervals, duration=10000)
    assert computed(result.speech_ms) == 10000
    assert computed(result.silence_ms) == 0
    assert computed(result.speech_ratio) == 1.0
    assert computed(result.silence_ratio) == 0.0
    # 2 or more speakers active in [2000, 8000); all three in [4000, 6000).
    assert computed(result.overlap_ms) == 6000
    assert computed(result.overlap_ratio_of_speech) == 0.6
    assert computed(result.attributed_speech_ms) == 10000
    talks = [computed(s.talk_ms) for s in result.speakers.values()]
    assert talks == [6000, 6000, 6000]
    # The 3-way moment is counted 3 times in the sum and removed once by overlap_ms.
    assert sum(talks) - computed(result.overlap_ms) == 12000
    assert computed(result.attributed_speech_ms) < 12000
    for speaker in result.speakers.values():
        assert computed(speaker.talk_share) == 0.3333  # 6000 / 18000
        assert computed(speaker.talk_ratio_of_call) == 0.6
        assert computed(speaker.speaking_rate_wpm) == 100.0  # 10 * 60000 / 6000
    assert computed(result.speaking_rate_wpm) == 180.0  # 30 * 60000 / 10000
    assert_reconciles(result, duration=10000)


def test_same_speaker_self_overlap_and_touching_merge() -> None:
    # a [0, 5000) and [3000, 8000) overlap, [8000, 10000) touches: one turn [0, 10000).
    intervals = [iv(0, 5000, "a", 5), iv(3000, 8000, "a", 5), iv(8000, 10000, "a", 2)]
    result = compute(intervals, duration=20000)
    assert computed(result.speech_ms) == 10000
    assert computed(result.overlap_ms) == 0
    a = result.speakers["a"]
    assert computed(a.talk_ms) == 10000
    # Words of overlapping intervals are summed: 12 * 60000 / 10000 = 72.0
    assert computed(a.speaking_rate_wpm) == 72.0
    assert computed(result.speaking_rate_wpm) == 72.0
    assert_reconciles(result, duration=20000)


def test_self_overlap_is_not_overlap_with_two_speakers() -> None:
    # a has two overlapping turns; b overlaps one of them for [4000, 6000).
    intervals = [iv(0, 5000, "a"), iv(2000, 6000, "a"), iv(4000, 9000, "b")]
    result = compute(intervals, duration=10000)
    assert computed(result.speakers["a"].talk_ms) == 6000
    assert computed(result.speakers["b"].talk_ms) == 5000
    assert computed(result.overlap_ms) == 2000
    assert computed(result.attributed_speech_ms) == 9000
    assert_reconciles(result, duration=10000)


def test_unknown_speaker_under_the_guard() -> None:
    # unattributed 1000 / 10000 = 0.1 <= 0.2: shares computed over attributed time.
    intervals = [iv(0, 9000, "a"), iv(9000, 10000, "unknown")]
    result = compute(intervals, duration=10000)
    assert computed(result.attributed_speech_ms) == 9000
    assert computed(result.unattributed_speech_ms) == 1000
    assert computed(result.speakers["a"].talk_share) == 1.0
    assert list(result.speakers) == ["a"]
    assert result.input_summary["unknown_interval_count"] == 1
    assert_reconciles(result, duration=10000)


def test_unknown_speaker_exactly_at_the_guard() -> None:
    # unattributed 2000 / 10000 = 0.2, not above the guard: computed.
    intervals = [iv(0, 4000, "a"), iv(4000, 8000, "b"), iv(8000, 10000, "unknown")]
    result = compute(intervals, duration=10000)
    assert computed(result.unattributed_speech_ms) == 2000
    assert computed(result.speakers["a"].talk_share) == 0.5
    assert computed(result.speakers["b"].talk_share) == 0.5
    assert_reconciles(result, duration=10000)


def test_unknown_speaker_over_the_guard() -> None:
    # unattributed 2100 / 10000 = 0.21 > 0.2: every talk_share is withheld.
    intervals = [iv(0, 4000, "a"), iv(4000, 7900, "b"), iv(7900, 10000, " Speaker_Unknown ")]
    result = compute(intervals, duration=10000)
    assert computed(result.unattributed_speech_ms) == 2100
    for speaker in result.speakers.values():
        assert not_computable(speaker.talk_share) == "too_much_unattributed_speech"
    # The other speaker metrics are still computed.
    assert computed(result.speakers["a"].talk_ms) == 4000
    assert computed(result.speakers["a"].talk_ratio_of_call) == 0.4
    assert computed(result.speakers["b"].talk_ratio_of_call) == 0.39
    assert_reconciles(result, duration=10000)


def test_unknown_speech_under_known_speech_is_not_overlap() -> None:
    # a [0, 10000), unknown [5000, 12000): only [10000, 12000) is unattributed.
    result = compute([iv(0, 10000, "a"), iv(5000, 12000, None)], duration=12000)
    assert computed(result.speech_ms) == 12000
    assert computed(result.attributed_speech_ms) == 10000
    assert computed(result.unattributed_speech_ms) == 2000
    assert computed(result.overlap_ms) == 0
    # 2000 / 12000 = 0.1667 <= 0.2
    assert computed(result.speakers["a"].talk_share) == 1.0
    assert_reconciles(result, duration=12000)


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "   ",
        "unknown",
        "UNKNOWN",
        " Unknown\t",
        "speaker_unknown",
        "SPEAKER_UNKNOWN",
        " speaker_unknown ",
    ],
)
def test_unknown_speaker_keys(key) -> None:
    result = compute([iv(0, 1000, key)], duration=1000)
    assert result.speakers == {}
    assert result.input_summary["unknown_interval_count"] == 1
    assert computed(result.attributed_speech_ms) == 0
    assert computed(result.unattributed_speech_ms) == 1000


def test_pipeline_unknown_label_is_unknown() -> None:
    # The pipeline writes this label when no diarization turn covers a segment
    # (backend/jobs/processor.py); the package keeps its own copy of the string.
    from backend.providers.schemas import UNKNOWN_SPEAKER_LABEL

    result = compute([iv(0, 1000, UNKNOWN_SPEAKER_LABEL)], duration=1000)
    assert result.speakers == {}
    assert result.input_summary["unknown_interval_count"] == 1


def test_known_keys_strip_but_keep_case() -> None:
    intervals = [iv(0, 1000, " a "), iv(1000, 2000, "a"), iv(2000, 3000, "A")]
    result = compute(intervals, duration=3000)
    assert list(result.speakers) == ["A", "a"]
    assert computed(result.speakers["a"].talk_ms) == 2000
    assert computed(result.speakers["A"].talk_ms) == 1000


def test_diarization_off() -> None:
    intervals = [iv(0, 6000, "a", 10), iv(4000, 10000, "b", 10)]
    result = compute(intervals, duration=20000, diarization=False, speakers=["c"])
    assert result.speakers == {}
    for name in (
        "attributed_speech_ms",
        "unattributed_speech_ms",
        "overlap_ms",
        "overlap_ratio_of_speech",
    ):
        assert not_computable(getattr(result, name)) == "diarization_unavailable"
    # Metrics that do not need speakers are still computed.
    assert computed(result.speech_ms) == 10000
    assert computed(result.speech_ratio) == 0.5
    assert computed(result.speaking_rate_wpm) == 120.0  # 20 * 60000 / 10000
    assert result.input_summary["unknown_interval_count"] == 2
    assert_reconciles(result, duration=20000)


def test_diarization_off_comes_first_in_precedence() -> None:
    # no speech and no duration also apply, but diarization_unavailable wins.
    result = compute([], duration=None, diarization=False)
    assert not_computable(result.overlap_ratio_of_speech) == "diarization_unavailable"
    # speech_ratio never says diarization_unavailable.
    assert not_computable(result.speech_ratio) == "duration_unknown"


def test_declared_silent_speaker() -> None:
    # a speaks [0, 10000) with 20 words; z is declared and silent.
    result = compute([iv(0, 10000, "a", 20)], duration=20000, speakers=["z", "a"])
    assert list(result.speakers) == ["a", "z"]
    z = result.speakers["z"]
    assert computed(z.talk_ms) == 0
    assert computed(z.talk_share) == 0.0
    assert computed(z.talk_ratio_of_call) == 0.0
    assert not_computable(z.speaking_rate_wpm) == "too_little_speech"
    assert computed(result.speakers["a"].talk_share) == 1.0
    assert_reconciles(result, duration=20000)


def test_declared_speakers_are_normalized() -> None:
    result = compute(
        [iv(0, 1000, "a")],
        duration=1000,
        speakers=[" b ", "unknown", "speaker_unknown", "  ", "a"],
    )
    assert list(result.speakers) == ["a", "b"]


def test_clipping_past_the_end_of_the_call() -> None:
    # Call is 10000 ms. a [8000, 12000) is clipped to [8000, 10000) and keeps its 10 words.
    # b [10000, 15000) is wholly past the end: clipped, then ignored with its words.
    intervals = [iv(0, 5000, "a", 0), iv(8000, 12000, "a", 10), iv(10000, 15000, "b", 5)]
    result = compute(intervals, duration=10000)
    assert result.input_summary == {
        "interval_count": 3,
        "ignored_zero_length_count": 0,
        "clipped_interval_count": 2,
        "unknown_interval_count": 0,
    }
    assert computed(result.speech_ms) == 7000
    assert computed(result.silence_ms) == 3000
    # 10 words * 60000 / 7000 ms = 85.714... -> 85.7
    assert computed(result.speaking_rate_wpm) == 85.7
    # b had no kept interval, so it is not a known speaker.
    assert list(result.speakers) == ["a"]
    assert_reconciles(result, duration=10000)


def test_clipped_unknown_interval_counts_only_when_kept() -> None:
    intervals = [iv(0, 1000, "a"), iv(2000, 3000, "unknown"), iv(5000, 6000, None)]
    result = compute(intervals, duration=4000)
    assert result.input_summary["clipped_interval_count"] == 1
    assert result.input_summary["unknown_interval_count"] == 1


def test_zero_length_interval_is_ignored() -> None:
    intervals = [iv(0, 6000, "a", 6), iv(3000, 3000, "a", 100), iv(4000, 4000, "b", 1)]
    result = compute(intervals, duration=10000)
    assert result.input_summary == {
        "interval_count": 3,
        "ignored_zero_length_count": 2,
        "clipped_interval_count": 0,
        "unknown_interval_count": 0,
    }
    # The ignored 100 words do not count: 6 * 60000 / 6000 = 60.0
    assert computed(result.speaking_rate_wpm) == 60.0
    assert list(result.speakers) == ["a"]


def test_zero_length_past_the_end_is_zero_length_not_clipped() -> None:
    result = compute([iv(15000, 15000, "a")], duration=10000)
    assert result.input_summary["ignored_zero_length_count"] == 1
    assert result.input_summary["clipped_interval_count"] == 0


def test_zero_length_call() -> None:
    result = compute([iv(0, 6000, "a", 12), iv(3000, 9000, "b", 6)], duration=0)
    assert not_computable(result.call_duration_ms) == "zero_length_call"
    for name in ("silence_ms", "speech_ratio", "silence_ratio"):
        assert not_computable(getattr(result, name)) == "zero_length_call"
    # No clipping when the duration is 0.
    assert result.input_summary["clipped_interval_count"] == 0
    assert computed(result.speech_ms) == 9000
    assert computed(result.overlap_ms) == 3000
    for speaker in result.speakers.values():
        assert not_computable(speaker.talk_ratio_of_call) == "zero_length_call"
    assert computed(result.speakers["a"].speaking_rate_wpm) == 120.0  # 12 * 60000 / 6000
    assert_reconciles(result, duration=0)


def test_unknown_duration() -> None:
    result = compute([iv(0, 6000, "a", 12), iv(90000, 96000, "b", 6)], duration=None)
    assert not_computable(result.call_duration_ms) == "duration_unknown"
    for name in ("silence_ms", "speech_ratio", "silence_ratio"):
        assert not_computable(getattr(result, name)) == "duration_unknown"
    # No clipping when the duration is unknown.
    assert computed(result.speech_ms) == 12000
    assert result.input_summary["clipped_interval_count"] == 0
    for speaker in result.speakers.values():
        assert not_computable(speaker.talk_ratio_of_call) == "duration_unknown"
        assert computed(speaker.talk_share) == 0.5
    assert_reconciles(result, duration=None)


def test_missing_word_counts() -> None:
    intervals = [iv(0, 10000, "a", None), iv(10000, 20000, "b", 10), iv(20000, 21000, "c", None)]
    result = compute(intervals, duration=30000)
    assert not_computable(result.speaking_rate_wpm) == "word_counts_missing"
    assert not_computable(result.speakers["a"].speaking_rate_wpm) == "word_counts_missing"
    assert computed(result.speakers["b"].speaking_rate_wpm) == 60.0  # 10 * 60000 / 10000
    # c has both problems; word_counts_missing comes first.
    assert not_computable(result.speakers["c"].speaking_rate_wpm) == "word_counts_missing"


def test_rate_below_and_at_the_minimum_speech_time() -> None:
    below = compute([iv(0, 4999, "a", 10)], duration=10000)
    assert not_computable(below.speaking_rate_wpm) == "too_little_speech"
    assert not_computable(below.speakers["a"].speaking_rate_wpm) == "too_little_speech"
    at = compute([iv(0, 5000, "a", 10)], duration=10000)
    assert computed(at.speaking_rate_wpm) == 120.0  # 10 * 60000 / 5000
    assert computed(at.speakers["a"].speaking_rate_wpm) == 120.0


def test_zero_words_over_enough_time_is_a_computed_zero() -> None:
    result = compute([iv(0, 6000, "a", 0)], duration=10000)
    assert computed(result.speaking_rate_wpm) == 0.0


def test_all_speech_unknown_with_a_declared_speaker() -> None:
    # no_attributed_speech comes before too_much_unattributed_speech.
    result = compute([iv(0, 5000, None)], duration=10000, speakers=["a"])
    assert computed(result.attributed_speech_ms) == 0
    assert computed(result.unattributed_speech_ms) == 5000
    assert not_computable(result.speakers["a"].talk_share) == "no_attributed_speech"


def test_no_speech_with_unknown_duration() -> None:
    result = compute([], duration=None, speakers=["a"])
    a = result.speakers["a"]
    assert not_computable(a.talk_ratio_of_call) == "duration_unknown"
    assert not_computable(a.talk_share) == "no_speech"
    assert not_computable(result.overlap_ratio_of_speech) == "no_speech"


# --- rounding ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("speech_ms", "expected"),
    [
        (1, 0.0),  # 0.00005 ties to the even 0.0000 (half-up would give 0.0001)
        (5, 0.0002),  # 0.00025 ties to the even 0.0002 (half-up would give 0.0003)
        (15, 0.0008),  # 0.00075 ties to the even 0.0008
        (3, 0.0002),  # 0.00015 ties to the even 0.0002
    ],
)
def test_ratio_rounds_half_even(speech_ms: int, expected: float) -> None:
    result = compute([iv(0, speech_ms, "a")], duration=20000)
    ratio = result.speech_ratio
    assert computed(ratio) == expected
    assert type(ratio.value) is float
    assert (ratio.numerator, ratio.denominator) == (speech_ms, 20000)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (3, 11.2),  # 3 * 60000 / 16000 = 11.25 ties to the even 11.2
        (1, 3.8),  # 3.75 ties to the even 3.8
    ],
)
def test_rate_rounds_half_even(words: int, expected: float) -> None:
    result = compute([iv(0, 16000, "a", words)], duration=20000)
    rate = result.speaking_rate_wpm
    assert computed(rate) == expected
    assert type(rate.value) is float
    assert (rate.numerator, rate.denominator) == (words, 16000)


def test_talk_shares_are_rounded_one_by_one() -> None:
    # Three equal speakers: 0.3333 * 3 = 0.9999, not forced to 1.
    intervals = [iv(0, 1000, "a"), iv(1000, 2000, "b"), iv(2000, 3000, "c")]
    result = compute(intervals, duration=3000)
    shares = [computed(s.talk_share) for s in result.speakers.values()]
    assert shares == [0.3333, 0.3333, 0.3333]


def test_numerator_and_denominator_by_unit() -> None:
    result = compute([iv(0, 10000, "a", 20), iv(5000, 15000, "b", 10)], duration=20000)
    speech = result.speech_ms
    assert speech.unit == "ms"
    assert (speech.numerator, speech.denominator) == (15000, None)
    assert type(speech.value) is int
    overlap_ratio = result.overlap_ratio_of_speech
    assert overlap_ratio.unit == "ratio"
    assert (overlap_ratio.numerator, overlap_ratio.denominator) == (5000, 15000)
    share = result.speakers["a"].talk_share
    assert (share.numerator, share.denominator) == (10000, 20000)
    rate = result.speaking_rate_wpm
    assert rate.unit == "words_per_minute"
    assert (rate.numerator, rate.denominator) == (30, 15000)
    assert computed(rate) == 120.0
    duration = result.call_duration_ms
    assert (duration.numerator, duration.denominator) == (20000, None)


# --- determinism and serialization ------------------------------------------------------


def _mixed_fixture() -> list[SpeechInterval]:
    return [
        iv(0, 6000, "a", 10),
        iv(2000, 8000, "b", 11),
        iv(4000, 12000, "c", None),
        iv(3000, 3000, "a", 4),
        iv(9000, 14000, "unknown", 2),
        iv(13000, 22000, "a", 7),
        iv(21000, 30000, " b ", 9),
        iv(25000, 25000, None, 1),
        iv(28000, 41000, "c", 3),
        iv(45000, 50000, "d", 1),
    ]


def test_input_order_does_not_change_the_result() -> None:
    expected = json.dumps(
        compute(_mixed_fixture(), duration=40000, speakers=["z", "a"]).as_dict(),
        sort_keys=True,
    )
    for seed in range(20):
        shuffled = _mixed_fixture()
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - test shuffle, not security
        speakers = ["a", "z"] if seed % 2 else ["z", "a"]
        result = compute(shuffled, duration=40000, speakers=speakers)
        assert json.dumps(result.as_dict(), sort_keys=True) == expected


def test_mixed_fixture_reconciles() -> None:
    result = compute(_mixed_fixture(), duration=40000, speakers=["z", "a"])
    assert result.input_summary == {
        "interval_count": 10,
        "ignored_zero_length_count": 2,
        "clipped_interval_count": 2,  # c [28000, 41000) and d [45000, 50000)
        "unknown_interval_count": 1,
    }
    assert list(result.speakers) == ["a", "b", "c", "z"]
    assert_reconciles(result, duration=40000)


def _json_safe(value) -> bool:
    if value is None or isinstance(value, (str, int, float)):
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def test_as_dict_is_json_safe_with_the_same_names() -> None:
    result = compute(_mixed_fixture(), duration=40000, speakers=["z"])
    data = result.as_dict()
    assert _json_safe(data)
    assert set(data) == {
        "version",
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
        "speakers",
        "input_summary",
    }
    assert data["version"] == METRICS_VERSION
    assert set(data["speech_ms"]) == {
        "status",
        "value",
        "unit",
        "reason",
        "numerator",
        "denominator",
    }
    assert set(data["speakers"]["a"]) == {
        "talk_ms",
        "talk_share",
        "talk_ratio_of_call",
        "speaking_rate_wpm",
    }
    assert list(data["speakers"]) == ["a", "b", "c", "z"]
    assert data["input_summary"] == result.input_summary
    assert json.loads(json.dumps(data)) == data


def test_speaker_metrics_type() -> None:
    result = compute([iv(0, 1000, "a")], duration=1000)
    assert isinstance(result.speakers["a"], SpeakerMetrics)


def test_input_is_not_changed_and_generators_work() -> None:
    intervals = _mixed_fixture()
    before = list(intervals)
    first = compute(intervals, duration=40000)
    assert intervals == before
    second = compute((interval for interval in intervals), duration=40000)
    assert first.as_dict() == second.as_dict()


# --- deny-cases (SPEC 5 and 3.6) and their allow-controls -------------------------------


@pytest.mark.parametrize(
    "interval",
    [
        SpeechInterval(-1, 10, "a"),
        SpeechInterval(10, 9, "a"),
        SpeechInterval(1.5, 10, "a"),  # type: ignore[arg-type]
        SpeechInterval(True, 10, "a"),  # type: ignore[arg-type]
        SpeechInterval(0, 10.0, "a"),  # type: ignore[arg-type]
        SpeechInterval(0, False, "a"),  # type: ignore[arg-type]
        SpeechInterval("0", 10, "a"),  # type: ignore[arg-type]
        SpeechInterval(0, 10, "a", -1),
        SpeechInterval(0, 10, "a", 1.0),  # type: ignore[arg-type]
        SpeechInterval(0, 10, "a", True),  # type: ignore[arg-type]
        SpeechInterval(0, 10, 7),  # type: ignore[arg-type]
        SpeechInterval(0, 10, b"a"),  # type: ignore[arg-type]
    ],
)
def test_invalid_interval_is_refused(interval: SpeechInterval) -> None:
    with pytest.raises(SpeechMetricsError) as error:
        compute([iv(0, 5, "a"), interval], duration=100)
    assert error.value.code == "invalid_interval"


@pytest.mark.parametrize(
    "interval",
    [
        SpeechInterval(0, 10, "a"),
        SpeechInterval(10, 10, "a"),
        SpeechInterval(1, 10, "a"),
        SpeechInterval(0, 10, "a", 0),
        SpeechInterval(0, 10, None),
    ],
)
def test_closest_valid_interval_is_accepted(interval: SpeechInterval) -> None:
    compute([interval], duration=100)


def test_invalid_interval_is_refused_even_with_diarization_off() -> None:
    with pytest.raises(SpeechMetricsError) as error:
        compute([SpeechInterval(0, 10, 7)], duration=100, diarization=False)  # type: ignore[arg-type]
    assert error.value.code == "invalid_interval"


@pytest.mark.parametrize("duration", [-1, 1.5, True, "100"])
def test_invalid_duration_is_refused(duration) -> None:
    with pytest.raises(SpeechMetricsError) as error:
        compute([], duration=duration)
    assert error.value.code == "invalid_duration"


def test_zero_duration_is_accepted() -> None:
    result = compute([], duration=0)
    for name in ("call_duration_ms", "silence_ms", "speech_ratio", "silence_ratio"):
        assert not_computable(getattr(result, name)) == "zero_length_call"


@pytest.mark.parametrize("speakers", ["ab", ["a", 1], ["a", None], [b"a"]])
def test_invalid_speakers_are_refused(speakers) -> None:
    with pytest.raises(SpeechMetricsError) as error:
        compute([], duration=100, speakers=speakers)
    assert error.value.code == "invalid_speakers"


@pytest.mark.parametrize("speakers", [["ab"], ("a", "b"), {"a"}, [], None])
def test_closest_valid_speakers_are_accepted(speakers) -> None:
    compute([], duration=100, speakers=speakers)


def test_speakers_are_checked_even_when_diarization_is_off() -> None:
    # Rule 8 ignores valid speakers with diarization off; a bad argument is still a bug.
    with pytest.raises(SpeechMetricsError) as error:
        compute([], duration=100, diarization=False, speakers=["a", 1])
    assert error.value.code == "invalid_speakers"


# --- count_words ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("   \n\t", 0),
        ("hello world", 2),
        ("سلام دنیا", 2),
        (f"می{ZWNJ}خواهم بروم", 2),  # the half-space keeps one word
        (f"کتاب{ZWNJ}ها", 1),
        ("a - b ...", 2),
        ("- ... \u2014", 0),
        ("۴۸۲۱۳ ۱۲", 2),  # Persian digits are alphanumeric
        ("one\u00a0two\u2003three", 3),  # no-break space and em space are whitespace
        ("  leading and trailing  ", 3),
        ("(x)", 1),
    ],
)
def test_count_words(text: str, expected: int) -> None:
    assert count_words(text) == expected


# --- intervals_from_segments ------------------------------------------------------------


def test_intervals_from_segments_reads_duck_typed_objects() -> None:
    segments = [
        SimpleNamespace(start_ms=0, end_ms=1000, speaker_key="a", text=f"می{ZWNJ}خواهم بروم"),
        SimpleNamespace(start_ms=500, end_ms=900, speaker_key="speaker_unknown", text=None),
        SimpleNamespace(start_ms=1000, end_ms=2000, speaker_key=None, text=""),
    ]
    assert intervals_from_segments(segments) == [
        SpeechInterval(0, 1000, "a", 2),
        SpeechInterval(500, 900, "speaker_unknown", None),
        SpeechInterval(1000, 2000, None, 0),
    ]


def test_intervals_from_transcript_segment_rows() -> None:
    from backend.database.saas_models import TranscriptSegment

    rows = [
        TranscriptSegment(start_ms=0, end_ms=6000, speaker_key="spk_0", text="سلام خوبی؟ بله"),
        TranscriptSegment(start_ms=4000, end_ms=9000, speaker_key="spk_1", text="ممنون"),
        TranscriptSegment(start_ms=9000, end_ms=10000, speaker_key="speaker_unknown", text="باشه"),
    ]
    intervals = intervals_from_segments(rows)
    assert [interval.word_count for interval in intervals] == [3, 1, 1]
    result = compute(intervals, duration=10000)
    assert list(result.speakers) == ["spk_0", "spk_1"]
    assert computed(result.overlap_ms) == 2000
    assert computed(result.unattributed_speech_ms) == 1000
    assert_reconciles(result, duration=10000)


def test_intervals_from_segments_does_not_validate() -> None:
    # Validation is the job of compute_speech_metrics (SPEC 3.6).
    segments = [SimpleNamespace(start_ms=-5, end_ms=1, speaker_key="a", text="x")]
    intervals = intervals_from_segments(segments)
    with pytest.raises(SpeechMetricsError) as error:
        compute(intervals, duration=10)
    assert error.value.code == "invalid_interval"


# --- README and purity ------------------------------------------------------------------


def test_readme_states_every_reason_and_definition() -> None:
    readme = (PACKAGE_DIR / "README.md").read_text(encoding="utf-8")
    for code in (*REASON_CODES, "invalid_interval", "invalid_duration", "invalid_speakers"):
        assert f"`{code}`" in readme, code
    for name in (
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
        "talk_ms",
        "talk_share",
        "talk_ratio_of_call",
        "MAX_UNATTRIBUTED_SHARE",
        "MIN_RATE_SPEECH_MS",
        METRICS_VERSION,
        "ROUND_HALF_EVEN",
    ):
        assert name in readme, name
    assert "\u2014" not in readme  # no em dash


def test_package_source_has_no_em_dash() -> None:
    for path in PACKAGE_DIR.glob("*.py"):
        assert "\u2014" not in path.read_text(encoding="utf-8"), path


def test_package_imports_only_the_standard_library() -> None:
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, path
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                own = name == "backend.speech_metrics" or name.startswith("backend.speech_metrics.")
                assert own or top in sys.stdlib_module_names or top == "__future__", (
                    path,
                    name,
                )


def test_import_does_not_load_forbidden_modules() -> None:
    forbidden = (
        "backend.providers",
        "backend.database",
        "backend.routers",
        "sqlalchemy",
        "fastapi",
        "httpx",
    )
    script = (
        "import sys\n"
        "import backend.speech_metrics\n"
        f"forbidden = {forbidden!r}\n"
        "loaded = sorted(m for m in sys.modules if m in forbidden "
        "or any(m.startswith(f + '.') for f in forbidden))\n"
        "print(','.join(loaded))\n"
    )
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == ""
