"""Speech, silence, talk, overlap and rate metrics computed from intervals (HOM-51).

A pure function: no database, no network, no model, no clock.  The result does not
depend on the order of the input.  Every value is exact integer arithmetic; ratios and
rates are rounded once, half to even, from the exact fraction.  A metric that cannot be
measured says why instead of reporting 0.  See ``README.md`` for every definition.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from backend.speech_metrics.models import (
    MAX_UNATTRIBUTED_SHARE,
    METRICS_VERSION,
    MIN_RATE_SPEECH_MS,
    REASON_CODES,
    UNKNOWN_SPEAKER_KEYS,
    Measurement,
    SpeakerMetrics,
    SpeechInterval,
    SpeechMetrics,
    SpeechMetricsError,
    Unit,
)
from backend.speech_metrics.words import count_words

MS_PER_MINUTE = 60000

# Exact, so a share of exactly 0.2 is never judged above the guard by float error.
_MAX_UNATTRIBUTED = Fraction(str(MAX_UNATTRIBUTED_SHARE))


@dataclass(frozen=True)
class _Kept:
    """An interval after the input rules: clipped, with its speaker normalized."""

    start_ms: int
    end_ms: int
    speaker: str | None  # None for unknown speech
    word_count: int | None


def intervals_from_segments(segments: Iterable[object]) -> list[SpeechInterval]:
    """One interval per segment, read from ``start_ms``, ``end_ms``, ``speaker_key`` and
    ``text``.  Works on ``TranscriptSegment`` rows or any object with those attributes.
    Nothing is validated here; ``compute_speech_metrics`` does that.
    """
    intervals = []
    for segment in segments:
        row: Any = segment
        text = row.text
        intervals.append(
            SpeechInterval(
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                speaker_key=row.speaker_key,
                word_count=count_words(text) if isinstance(text, str) else None,
            )
        )
    return intervals


def compute_speech_metrics(
    intervals: Iterable[SpeechInterval],
    *,
    call_duration_ms: int | None,
    diarization_available: bool,
    speakers: Iterable[str] | None = None,
) -> SpeechMetrics:
    """Measure one call from its speech intervals.

    ``call_duration_ms`` None means the duration is unknown.  ``speakers`` declares
    speakers that may have said nothing.  Raises ``SpeechMetricsError`` only for invalid
    input; anything that cannot be measured is a not-computable ``Measurement``.
    """
    given = list(intervals)
    for interval in given:
        _check_interval(interval)
    duration = _check_duration(call_duration_ms)
    declared = _declared_speakers(speakers)

    kept, input_summary = _apply_input_rules(given, duration, diarization_available)

    speech = _union_ms((item.start_ms, item.end_ms) for item in kept)
    if duration is None:
        duration_reason: str | None = "duration_unknown"
    elif duration == 0:
        duration_reason = "zero_length_call"
    else:
        duration_reason = None
    diarization_reason = None if diarization_available else "diarization_unavailable"
    no_speech = "no_speech" if speech == 0 else None

    by_speaker: dict[str, list[_Kept]] = {}
    if diarization_available:
        for key in declared:
            by_speaker[key] = []
        for item in kept:
            if item.speaker is not None:
                by_speaker.setdefault(item.speaker, []).append(item)

    attributed = _union_ms(
        (item.start_ms, item.end_ms) for item in kept if item.speaker is not None
    )
    unattributed = speech - attributed
    overlap = _overlap_ms(
        [(item.start_ms, item.end_ms) for item in items] for items in by_speaker.values()
    )
    talk = {
        key: _union_ms((item.start_ms, item.end_ms) for item in items)
        for key, items in by_speaker.items()
    }
    talk_sum = sum(talk.values())
    share_reasons = (
        no_speech,
        "no_attributed_speech" if talk_sum == 0 else None,
        "too_much_unattributed_speech"
        if speech > 0 and Fraction(unattributed, speech) > _MAX_UNATTRIBUTED
        else None,
    )

    speaker_metrics = {
        key: SpeakerMetrics(
            talk_ms=_ms(talk[key]),
            talk_share=_withheld("ratio", *share_reasons) or _ratio(talk[key], talk_sum),
            talk_ratio_of_call=_withheld("ratio", duration_reason) or _ratio(talk[key], duration),
            speaking_rate_wpm=_rate(by_speaker[key], talk[key], no_speech=no_speech),
        )
        for key in sorted(by_speaker)
    }

    return SpeechMetrics(
        version=METRICS_VERSION,
        call_duration_ms=_withheld("ms", duration_reason) or _ms(duration),
        speech_ms=_ms(speech),
        silence_ms=_withheld("ms", duration_reason) or _ms(duration - speech),
        speech_ratio=_withheld("ratio", duration_reason) or _ratio(speech, duration),
        silence_ratio=_withheld("ratio", duration_reason) or _ratio(duration - speech, duration),
        attributed_speech_ms=_withheld("ms", diarization_reason) or _ms(attributed),
        unattributed_speech_ms=_withheld("ms", diarization_reason) or _ms(unattributed),
        overlap_ms=_withheld("ms", diarization_reason) or _ms(overlap),
        overlap_ratio_of_speech=_withheld("ratio", diarization_reason, no_speech)
        or _ratio(overlap, speech),
        speaking_rate_wpm=_rate(kept, speech, no_speech=no_speech),
        speakers=speaker_metrics,
        input_summary=input_summary,
    )


# --- input rules ------------------------------------------------------------------------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_interval(interval: object) -> None:
    if not isinstance(interval, SpeechInterval):
        raise SpeechMetricsError("invalid_interval", "Every interval must be a SpeechInterval")
    start, end = interval.start_ms, interval.end_ms
    if not (_is_int(start) and _is_int(end)) or start < 0 or end < start:
        raise SpeechMetricsError(
            "invalid_interval", "start_ms and end_ms must be ints with 0 <= start_ms <= end_ms"
        )
    if interval.word_count is not None and (
        not _is_int(interval.word_count) or interval.word_count < 0
    ):
        raise SpeechMetricsError("invalid_interval", "word_count must be None or an int >= 0")
    if interval.speaker_key is not None and not isinstance(interval.speaker_key, str):
        raise SpeechMetricsError("invalid_interval", "speaker_key must be None or a str")


def _check_duration(duration: object) -> int | None:
    if duration is None:
        return None
    if not _is_int(duration) or duration < 0:
        raise SpeechMetricsError("invalid_duration", "call_duration_ms must be None or an int >= 0")
    return duration


def _speaker_of(key: str | None) -> str | None:
    """The known speaker for ``key``, or None when the key means an unknown speaker."""
    if key is None:
        return None
    stripped = key.strip()
    return None if stripped.casefold() in UNKNOWN_SPEAKER_KEYS else stripped


def _declared_speakers(speakers: Iterable[str] | None) -> set[str]:
    # Checked even when diarization is off: a bad argument is a caller bug either way.
    if speakers is None:
        return set()
    if isinstance(speakers, str):
        raise SpeechMetricsError("invalid_speakers", "speakers must be a collection, not a str")
    try:
        entries = list(speakers)
    except TypeError:
        raise SpeechMetricsError("invalid_speakers", "speakers must be iterable") from None
    if any(not isinstance(entry, str) for entry in entries):
        raise SpeechMetricsError("invalid_speakers", "every declared speaker must be a str")
    return {key for key in map(_speaker_of, entries) if key is not None}


def _apply_input_rules(
    given: list[SpeechInterval], duration: int | None, diarization_available: bool
) -> tuple[list[_Kept], dict[str, int]]:
    """Drop zero-length intervals, clip to a positive duration, normalize speakers."""
    kept: list[_Kept] = []
    zero_length = clipped = unknown = 0
    for interval in given:
        start, end = interval.start_ms, interval.end_ms
        if end == start:
            zero_length += 1
            continue
        if duration and end > duration:
            # Clipped; one wholly past the end keeps no length and is dropped, words too.
            clipped += 1
            end = duration
            if end <= start:
                continue
        speaker = _speaker_of(interval.speaker_key) if diarization_available else None
        if speaker is None:
            unknown += 1
        kept.append(_Kept(start, end, speaker, interval.word_count))
    return kept, {
        "interval_count": len(given),
        "ignored_zero_length_count": zero_length,
        "clipped_interval_count": clipped,
        "unknown_interval_count": unknown,
    }


# --- interval arithmetic ----------------------------------------------------------------


def _merged(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted, disjoint spans; spans that overlap or touch become one."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _union_ms(spans: Iterable[tuple[int, int]]) -> int:
    return sum(end - start for start, end in _merged(spans))


def _overlap_ms(speaker_spans: Iterable[list[tuple[int, int]]]) -> int:
    """Time where 2 or more distinct speakers are active.

    Each speaker's spans are merged first, so one speaker counts at most once at any moment.
    """
    changes: dict[int, int] = {}
    for spans in speaker_spans:
        for start, end in _merged(spans):
            changes[start] = changes.get(start, 0) + 1
            changes[end] = changes.get(end, 0) - 1
    total = active = 0
    previous: int | None = None
    for point in sorted(changes):
        if previous is not None and active >= 2:
            total += point - previous
        active += changes[point]
        previous = point
    return total


# --- measurements -----------------------------------------------------------------------


def _withheld(unit: Unit, *reasons: str | None) -> Measurement | None:
    """A not-computable measurement for the first applicable reason, or None if none apply."""
    applicable = [reason for reason in reasons if reason is not None]
    if not applicable:
        return None
    return Measurement(
        status="not_computable",
        value=None,
        unit=unit,
        reason=min(applicable, key=REASON_CODES.index),
        numerator=None,
        denominator=None,
    )


def _ms(value: int) -> Measurement:
    return Measurement("computed", value, "ms", None, value, None)


def _ratio(numerator: int, denominator: int) -> Measurement:
    # round() on a Fraction rounds half to even.
    value = float(round(Fraction(numerator, denominator), 4))
    return Measurement("computed", value, "ratio", None, numerator, denominator)


def _rate(items: list[_Kept], time_ms: int, *, no_speech: str | None) -> Measurement:
    """Words per minute of ``items`` over ``time_ms`` of their speech time."""
    words = [item.word_count for item in items]
    withheld = _withheld(
        "words_per_minute",
        no_speech,
        "word_counts_missing" if any(count is None for count in words) else None,
        "too_little_speech" if time_ms < MIN_RATE_SPEECH_MS else None,
    )
    if withheld is not None:
        return withheld
    total = sum(count for count in words if count is not None)
    value = float(round(Fraction(total * MS_PER_MINUTE, time_ms), 1))
    return Measurement("computed", value, "words_per_minute", None, total, time_ms)
