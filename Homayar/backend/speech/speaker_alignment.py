"""Align ASR words or turns to diarization speaker intervals (HOM-47, ADR 0072).

A transcript turn's speaker must never be invented. The aligner maps every
ASR span onto the original recording timeline, then measures it against the
diarization speaker intervals: a span one speaker clearly dominates is
assigned to that speaker; a span that straddles speakers or falls inside
overlapping speech stays ambiguous; a span the diarization does not cover
stays unknown. Every rule that fires is reported as a reason, and every
output span lies inside the recording bounds.

Everything here is pure: no I/O, no database, no FastAPI, no randomness, no
clock. Integers and exact :class:`~fractions.Fraction` values are used
inside; floats appear only at the edges (input times and output fields).
Reordered diarization segments change nothing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from fractions import Fraction
from typing import Any, Final

from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRChunkSpan,
    ASRSegment,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
)
from backend.speech.chunking import ChunkPlan

ALIGNMENT_VERSION: Final = "speaker-alignment/1"
_WORD_TOLERANCE: Final = 1e-6  # how far a word may reach past its unit's bounds


def _number(value: object, name: str) -> int | float:
    """A finite non-bool number, or ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


# --- time mapping ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeBase:
    """How a caller's local times map to original seconds.

    Local second ``t`` sits at ``offset_samples / sample_rate + t`` on the
    original timeline; local sample ``n`` at ``(offset_samples + n) /
    sample_rate``.
    """

    offset_samples: int = 0
    sample_rate: int = 16000

    def __post_init__(self) -> None:
        for name in ("offset_samples", "sample_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an int")
        if self.offset_samples < 0:
            raise ValueError("offset_samples must be at least 0")
        if self.sample_rate < 1:
            raise ValueError("sample_rate must be at least 1")

    @classmethod
    def from_chunk_plan(cls, plan: ChunkPlan) -> TimeBase:
        """The time base of one ASR chunk (ADR 0046): local 0 is ``start_sample``."""
        return cls(offset_samples=plan.start_sample, sample_rate=plan.sample_rate)

    @classmethod
    def from_span(cls, span: ASRChunkSpan, sample_rate: int) -> TimeBase:
        """The time base of a chunk known only by its original-time span."""
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate < 1:
            raise ValueError("sample_rate must be a positive int")
        return cls(offset_samples=round(span.start_seconds * sample_rate), sample_rate=sample_rate)

    def seconds_to_original(self, local_seconds: float) -> Fraction:
        return Fraction(self.offset_samples, self.sample_rate) + Fraction(local_seconds)

    def sample_to_original(self, local_sample: int) -> Fraction:
        if isinstance(local_sample, bool) or not isinstance(local_sample, int):
            raise ValueError("local_sample must be an int")
        return Fraction(self.offset_samples + local_sample, self.sample_rate)


def original_sample(seconds: Fraction, original_sample_rate: int) -> int:
    """The sample index at the recording's own rate that names the same instant.

    A 16 kHz analysis time and a 44.1 kHz or 8 kHz original index can name the
    same point of the recording: this is the mapping back through resampling.
    Rounding is half-even.
    """
    if isinstance(original_sample_rate, bool) or not isinstance(original_sample_rate, int):
        raise ValueError("original_sample_rate must be an int")
    if original_sample_rate < 1:
        raise ValueError("original_sample_rate must be at least 1")
    if not isinstance(seconds, Fraction):
        seconds = Fraction(seconds)
    return round(seconds * original_sample_rate)


def map_diarization(result: DiarizationResult, time_base: TimeBase) -> DiarizationResult:
    """Shift chunk-local diarization onto the original timeline.

    Every segment moves by the time base; status, provider, issue and
    ``is_mock`` are kept unchanged.
    """
    if not isinstance(result, DiarizationResult):
        raise ValueError("result must be a DiarizationResult")
    if not isinstance(time_base, TimeBase):
        raise ValueError("time_base must be a TimeBase")
    offset = Fraction(time_base.offset_samples, time_base.sample_rate)
    segments = [
        DiarizationSegment(
            speaker_label=segment.speaker_label,
            start_seconds=float(Fraction(segment.start_seconds) + offset),
            end_seconds=float(Fraction(segment.end_seconds) + offset),
            confidence=segment.confidence,
        )
        for segment in result.segments
    ]
    return DiarizationResult(
        provider=result.provider,
        status=result.status,
        segments=segments,
        issue=result.issue,
        is_mock=result.is_mock,
    )


# --- inputs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class AsrWord:
    """One ASR word in its unit's local time."""

    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        start = _number(self.start_seconds, "start_seconds")
        end = _number(self.end_seconds, "end_seconds")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")
        if start < 0 or end < start:
            raise ValueError("word times must satisfy end >= start >= 0")


@dataclass(frozen=True)
class AsrUnit:
    """One ASR segment: a whole-unit span, or its words when it has them."""

    start_seconds: float
    end_seconds: float
    text: str
    words: tuple[AsrWord, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        start = _number(self.start_seconds, "start_seconds")
        end = _number(self.end_seconds, "end_seconds")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")
        if start < 0 or end < start:
            raise ValueError("unit times must satisfy end >= start >= 0")
        if self.confidence is not None:
            _number(self.confidence, "confidence")
        if not isinstance(self.words, tuple):
            raise ValueError("words must be a tuple of AsrWord")
        previous: Fraction | None = None
        for word in self.words:
            if not isinstance(word, AsrWord):
                raise ValueError("words must be AsrWord instances")
            if (
                word.start_seconds < start - _WORD_TOLERANCE
                or word.end_seconds > end + _WORD_TOLERANCE
            ):
                raise ValueError("every word must lie inside its unit's range")
            word_start = Fraction(word.start_seconds)
            if previous is not None and word_start < previous:
                raise ValueError("words must be sorted by start")
            previous = word_start

    @classmethod
    def from_segment(cls, segment: ASRSegment) -> AsrUnit:
        return cls(
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            text=segment.text,
            confidence=segment.confidence,
        )


# --- settings and output ----------------------------------------------------------------------


@dataclass(frozen=True)
class AlignSettings:
    min_coverage: float = 0.5  # of a span's duration that speakers must cover, else no_speaker
    dominant_share: float = 0.8  # of the covered time the top speaker must hold, else straddles
    max_overlap_share: float = 0.2  # of the span's duration in overlapping speech, else overlap
    bounds_tolerance_seconds: float = 0.05  # how far past a bound is still silent

    def __post_init__(self) -> None:
        for name in (
            "min_coverage",
            "dominant_share",
            "max_overlap_share",
            "bounds_tolerance_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 < self.min_coverage <= 1:
            raise ValueError("min_coverage must be greater than 0 and at most 1")
        if not 0.5 < self.dominant_share <= 1:
            raise ValueError("dominant_share must be greater than 0.5 and at most 1")
        if not 0 < self.max_overlap_share <= 1:
            raise ValueError("max_overlap_share must be greater than 0 and at most 1")
        if not self.bounds_tolerance_seconds >= 0:
            raise ValueError("bounds_tolerance_seconds must be at least 0")

    def descriptor(self) -> dict[str, Any]:
        """The canonical, JSON-safe identity of this alignment (checkpoint input)."""
        return {
            "version": ALIGNMENT_VERSION,
            "min_coverage": float(self.min_coverage),
            "dominant_share": float(self.dominant_share),
            "max_overlap_share": float(self.max_overlap_share),
            "bounds_tolerance_seconds": float(self.bounds_tolerance_seconds),
        }


class AlignmentStatus(str, Enum):
    ASSIGNED = "assigned"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class AlignmentReason(str, Enum):
    OUTSIDE_RECORDING = "outside_recording"
    CLAMPED_TO_RECORDING = "clamped_to_recording"
    DIARIZATION_UNAVAILABLE = "diarization_unavailable"
    NO_DURATION = "no_duration"
    OVERLAP = "overlap"
    NO_SPEAKER = "no_speaker"
    STRADDLES_SPEAKERS = "straddles_speakers"
    UNKNOWN_SPEAKER = "unknown_speaker"


@dataclass(frozen=True)
class AlignedTurn:
    unit_index: int  # position of the source unit in the input
    start_seconds: float  # original time, inside the recording bounds
    end_seconds: float
    start_ms: int  # floor for start, ceil for end, clamped to ceil(duration * 1000)
    end_ms: int
    start_sample: int | None  # original-rate index, when a rate was given
    end_sample: int | None
    text: str
    speaker_label: str  # UNKNOWN_SPEAKER_LABEL unless assigned
    status: AlignmentStatus
    reasons: tuple[AlignmentReason, ...]  # sorted, empty only when assigned with no bounds note
    candidates: tuple[tuple[str, float], ...]  # label -> share of covered time
    confidence: float | None  # the top label's share for assigned, else None
    confidence_basis: str = "coverage_share_uncalibrated"


@dataclass(frozen=True)
class SpeakerAlignment:
    turns: tuple[AlignedTurn, ...]
    version: str = ALIGNMENT_VERSION
    settings: dict[str, Any] = field(default_factory=dict)
    assigned_count: int = 0
    ambiguous_count: int = 0
    unknown_count: int = 0


# --- exact range helpers -----------------------------------------------------------------------


def _merge_ranges(
    ranges: Sequence[tuple[Fraction, Fraction]],
) -> list[tuple[Fraction, Fraction]]:
    """Sorted, non-overlapping copy of ``ranges`` (their union as half-open)."""
    merged: list[list[Fraction]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _clip_ranges(
    ranges: Sequence[tuple[Fraction, Fraction]], start: Fraction, end: Fraction
) -> list[tuple[Fraction, Fraction]]:
    """``ranges`` cut down to ``[start, end)``; empty pieces are dropped."""
    return [
        (max(low, start), min(high, end))
        for low, high in ranges
        if max(low, start) < min(high, end)
    ]


def _total(ranges: Sequence[tuple[Fraction, Fraction]]) -> Fraction:
    return sum((high - low for low, high in ranges), Fraction(0))


def _overlap_seconds(
    per_label: dict[str, list[tuple[Fraction, Fraction]]],
) -> Fraction:
    """Seconds where two or more different labels are active.

    The same label twice is not overlap: per-label ranges are already merged,
    so counting the labels that cover each elementary interval counts
    distinct labels.
    """
    points = sorted(
        {point for ranges in per_label.values() for low, high in ranges for point in (low, high)}
    )
    total = Fraction(0)
    for left, right in zip(points, points[1:], strict=False):
        active = sum(
            1
            for ranges in per_label.values()
            if any(low <= left and high >= right for low, high in ranges)
        )
        if active >= 2:
            total += right - left
    return total


# --- the alignment ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    """One span to align, already in original seconds."""

    start: Fraction  # unclamped
    end: Fraction
    text: str


@dataclass(frozen=True)
class _Decision:
    status: AlignmentStatus
    speaker_label: str
    reasons: tuple[AlignmentReason, ...]
    candidates: tuple[tuple[str, float], ...]
    confidence: float | None
    clamped_start: Fraction
    clamped_end: Fraction
    # Per-label time and covered time inside the span, so a turn joined from
    # several spans can sum them and recompute its own shares (SPEC 11).
    label_times: dict[str, Fraction] = field(default_factory=dict)
    covered: Fraction = Fraction(0)


def _decide_span(
    span: _Span,
    bounds: Fraction,
    tolerance: Fraction,
    has_speakers: bool,
    per_label: dict[str, list[tuple[Fraction, Fraction]]],
    settings: AlignSettings,
) -> _Decision:
    fired: list[AlignmentReason] = []

    start = max(span.start, Fraction(0))
    end = min(span.end, bounds)
    if end <= start:
        # No part inside the open range (0, bounds): the part past the far
        # bound decides (end - duration past the end, 0 - start before 0).
        if span.end <= 0:
            nearest, outside = Fraction(0), -span.start
        elif span.start >= bounds:
            nearest, outside = bounds, span.end - bounds
        else:
            # A zero-length span strictly inside reaches past no bound.
            nearest, outside = start, Fraction(0)
        if outside > tolerance:
            return _Decision(
                status=AlignmentStatus.UNKNOWN,
                speaker_label=UNKNOWN_SPEAKER_LABEL,
                reasons=(AlignmentReason.OUTSIDE_RECORDING,),
                candidates=(),
                confidence=None,
                clamped_start=nearest,
                clamped_end=nearest,
            )
        start = end = nearest  # within the tolerance: clamp silently
    else:
        past_start = -span.start if span.start < 0 else Fraction(0)
        past_end = span.end - bounds if span.end > bounds else Fraction(0)
        if past_start > tolerance or past_end > tolerance:
            fired.append(AlignmentReason.CLAMPED_TO_RECORDING)

    candidates: tuple[tuple[str, float], ...] = ()
    label_times: dict[str, Fraction] = {}
    covered = Fraction(0)

    def _denied(status: AlignmentStatus, reason: AlignmentReason) -> _Decision:
        return _Decision(
            status=status,
            speaker_label=UNKNOWN_SPEAKER_LABEL,
            reasons=_sorted_reasons([*fired, reason]),
            candidates=candidates,
            confidence=None,
            clamped_start=start,
            clamped_end=end,
            label_times=label_times,
            covered=covered,
        )

    if not has_speakers:
        return _denied(AlignmentStatus.UNKNOWN, AlignmentReason.DIARIZATION_UNAVAILABLE)
    if end == start:
        return _denied(AlignmentStatus.UNKNOWN, AlignmentReason.NO_DURATION)

    duration = end - start
    clipped = {
        label: ranges
        for label, ranges in (
            (label, _clip_ranges(ranges, start, end)) for label, ranges in per_label.items()
        )
        if ranges
    }
    covered = _total(_merge_ranges([r for ranges in clipped.values() for r in ranges]))
    label_times = {label: _total(ranges) for label, ranges in clipped.items()}
    if covered > 0:
        candidates = tuple(
            (label, float(time / covered))
            for label, time in sorted(label_times.items(), key=lambda item: (-item[1], item[0]))
        )

    if _overlap_seconds(clipped) / duration >= Fraction(settings.max_overlap_share):
        return _denied(AlignmentStatus.AMBIGUOUS, AlignmentReason.OVERLAP)
    if covered / duration < Fraction(settings.min_coverage):
        return _denied(AlignmentStatus.UNKNOWN, AlignmentReason.NO_SPEAKER)
    top_label, top_time = max(label_times.items(), key=lambda item: (item[1], item[0]))
    if top_time / covered < Fraction(settings.dominant_share):
        return _denied(AlignmentStatus.AMBIGUOUS, AlignmentReason.STRADDLES_SPEAKERS)
    if top_label == UNKNOWN_SPEAKER_LABEL:
        return _denied(AlignmentStatus.UNKNOWN, AlignmentReason.UNKNOWN_SPEAKER)
    return _Decision(
        AlignmentStatus.ASSIGNED,
        top_label,
        _sorted_reasons(fired),
        candidates,
        float(top_time / covered),
        start,
        end,
        label_times,
        covered,
    )


def _sorted_reasons(fired: Sequence[AlignmentReason]) -> tuple[AlignmentReason, ...]:
    return tuple(sorted(fired, key=lambda reason: reason.value))


def _join_decisions(group: list[tuple[_Span, _Decision]]) -> _Decision:
    """One turn's decision from its words: label times and covered time summed
    over the words, then the same share formula a single span uses."""
    joined = group[0][1]
    label_times: dict[str, Fraction] = {}
    covered = Fraction(0)
    for _span, decision in group:
        covered += decision.covered
        for label, time in decision.label_times.items():
            label_times[label] = label_times.get(label, Fraction(0)) + time
    candidates: tuple[tuple[str, float], ...] = ()
    confidence: float | None = None
    if covered > 0:
        candidates = tuple(
            (label, float(time / covered))
            for label, time in sorted(label_times.items(), key=lambda item: (-item[1], item[0]))
        )
        if joined.status is AlignmentStatus.ASSIGNED:
            confidence = float(label_times[joined.speaker_label] / covered)
    return replace(
        joined,
        candidates=candidates,
        confidence=confidence,
        clamped_end=group[-1][1].clamped_end,
    )


def align_speakers(
    units: Sequence[AsrUnit],
    diarization: DiarizationResult | None,
    *,
    recording_duration_seconds: float,
    time_base: TimeBase | None = None,
    original_sample_rate: int | None = None,
    settings: AlignSettings | None = None,
) -> SpeakerAlignment:
    """Align every unit (or word) to the diarization, on the original timeline.

    ``time_base`` maps local unit times to original seconds (``None`` means
    the units are already in original time). Speaker intervals come as a
    ``DiarizationResult`` in original time; chunk-local diarization is mapped
    first with :func:`map_diarization`.
    """
    bounds = _number(recording_duration_seconds, "recording_duration_seconds")
    if bounds <= 0:
        raise ValueError("recording_duration_seconds must be greater than 0")
    bounds = Fraction(bounds)
    if time_base is None:
        time_base = TimeBase()
    elif not isinstance(time_base, TimeBase):
        raise ValueError("time_base must be a TimeBase")
    if original_sample_rate is not None and (
        isinstance(original_sample_rate, bool)
        or not isinstance(original_sample_rate, int)
        or original_sample_rate < 1
    ):
        raise ValueError("original_sample_rate must be a positive int")
    settings = AlignSettings() if settings is None else settings
    if not isinstance(settings, AlignSettings):
        raise ValueError("settings must be AlignSettings")
    checked: list[AsrUnit] = []
    for unit in units:
        if not isinstance(unit, AsrUnit):
            raise ValueError("units must be AsrUnit instances")
        checked.append(unit)

    tolerance = Fraction(settings.bounds_tolerance_seconds)
    has_speakers = diarization is not None and diarization.status == DiarizationStatus.AVAILABLE
    per_label: dict[str, list[tuple[Fraction, Fraction]]] = {}
    if has_speakers and diarization is not None:
        for segment in diarization.segments:
            low = max(Fraction(segment.start_seconds), Fraction(0))
            high = min(Fraction(segment.end_seconds), bounds)
            if high <= low:
                continue  # nothing of this segment is inside the recording
            per_label.setdefault(segment.speaker_label, []).append((low, high))
        per_label = {label: _merge_ranges(ranges) for label, ranges in per_label.items()}

    ceiling_ms = math.ceil(bounds * 1000)
    entries: list[tuple[Fraction, Fraction, int, AlignedTurn]] = []
    counts = {AlignmentStatus.ASSIGNED: 0, AlignmentStatus.AMBIGUOUS: 0, AlignmentStatus.UNKNOWN: 0}

    for index, unit in enumerate(checked):
        if unit.words:
            groups: list[list[tuple[_Span, _Decision]]] = []
            for word in unit.words:
                span = _Span(
                    time_base.seconds_to_original(word.start_seconds),
                    time_base.seconds_to_original(word.end_seconds),
                    word.text,
                )
                decision = _decide_span(span, bounds, tolerance, has_speakers, per_label, settings)
                key = (decision.status, decision.speaker_label, decision.reasons)
                if groups and key == _key_of(groups[-1]):
                    groups[-1].append((span, decision))
                else:
                    groups.append([(span, decision)])
            for group in groups:
                decision = _join_decisions(group)
                entries.append(
                    _turn_entry(
                        index,
                        start=decision.clamped_start,
                        end=decision.clamped_end,
                        text=" ".join(piece.text for piece, _ in group),
                        decision=decision,
                        bounds_ms=ceiling_ms,
                        sample_rate=original_sample_rate,
                    )
                )
        else:
            span = _Span(
                time_base.seconds_to_original(unit.start_seconds),
                time_base.seconds_to_original(unit.end_seconds),
                unit.text,
            )
            decision = _decide_span(span, bounds, tolerance, has_speakers, per_label, settings)
            entries.append(
                _turn_entry(
                    index,
                    start=decision.clamped_start,
                    end=decision.clamped_end,
                    text=unit.text,
                    decision=decision,
                    bounds_ms=ceiling_ms,
                    sample_rate=original_sample_rate,
                )
            )

    entries.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    turns = tuple(entry[3] for entry in entries)
    for turn in turns:
        counts[turn.status] += 1
    return SpeakerAlignment(
        turns=turns,
        version=ALIGNMENT_VERSION,
        settings=settings.descriptor(),
        assigned_count=counts[AlignmentStatus.ASSIGNED],
        ambiguous_count=counts[AlignmentStatus.AMBIGUOUS],
        unknown_count=counts[AlignmentStatus.UNKNOWN],
    )


def _key_of(group: list[tuple[_Span, _Decision]]) -> tuple:
    _span, decision = group[0]
    return (decision.status, decision.speaker_label, decision.reasons)


def _turn_entry(
    unit_index: int,
    *,
    start: Fraction,
    end: Fraction,
    text: str,
    decision: _Decision,
    bounds_ms: int,
    sample_rate: int | None,
) -> tuple[Fraction, Fraction, int, AlignedTurn]:
    start_ms = max(0, min(math.floor(start * 1000), bounds_ms))
    end_ms = max(0, min(math.ceil(end * 1000), bounds_ms))
    start_sample = original_sample(start, sample_rate) if sample_rate is not None else None
    end_sample = original_sample(end, sample_rate) if sample_rate is not None else None
    turn = AlignedTurn(
        unit_index=unit_index,
        start_seconds=float(start),
        end_seconds=float(end),
        start_ms=start_ms,
        end_ms=end_ms,
        start_sample=start_sample,
        end_sample=end_sample,
        text=text,
        speaker_label=decision.speaker_label,
        status=decision.status,
        reasons=decision.reasons,
        candidates=decision.candidates,
        confidence=decision.confidence,
    )
    return (start, end, unit_index, turn)
