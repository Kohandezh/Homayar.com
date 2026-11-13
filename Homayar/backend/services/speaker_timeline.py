"""The speaker timeline of one transcript revision (HOM-49, WP10-T06).

Pure functions over plain values: no database access, no FastAPI imports.
The route in ``routers/v1/insights.py`` loads the rows and hands them over;
unit tests of the builder need no database.

The timeline answers, for one revision, who speaks when (one lane per
speaker), where turns overlap, and which turns need a person to look at them.
Overlap is measured from turn time ranges (``overlap_basis == "turns"``):
raw diarization segments are not stored yet, so HOM-45 will add a measured
basis without breaking this shape.  The response carries no transcript text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.providers.schemas import UNKNOWN_SPEAKER_LABEL
from backend.schemas.insights import (
    TimelineLane,
    TimelineOverlap,
    TimelineTurn,
)

# A speaker key that names no person: the providers' label or the column
# default of ``TranscriptSegment.speaker_key``.
UNKNOWN_SPEAKER_KEYS = frozenset({UNKNOWN_SPEAKER_LABEL, "unknown"})


@dataclass(frozen=True)
class TimelineSegmentData:
    """One turn as the builder sees it: a stored ``TranscriptSegment`` copy."""

    id: str
    sequence: int
    speaker_key: str
    start_ms: int
    end_ms: int
    confidence: float | None = None


@dataclass(frozen=True)
class TimelineSpeakerData:
    """One ``MeetingSpeaker`` row as the builder sees it."""

    speaker_key: str
    display_name: str | None = None
    identity_type: str | None = None
    confidence: float | None = None


class TimelineParts(BaseModel):
    """What ``build_timeline`` returns: the body of the response minus the
    revision and correction info the route assembles from the revision row."""

    lanes: list[TimelineLane] = Field(default_factory=list)
    turns: list[TimelineTurn] = Field(default_factory=list)
    overlaps: list[TimelineOverlap] = Field(default_factory=list)
    overlap_ms: int = 0
    unknown_speech_ms: int = 0


def _is_unknown(speaker_key: str) -> bool:
    return speaker_key in UNKNOWN_SPEAKER_KEYS


def _attribution(
    speaker_key: str,
    sequence: int,
    baseline_speaker_by_sequence: Mapping[int, str],
    baseline_source: str,
    baseline_diarization_status: str | None,
) -> str:
    """Why the revision says this turn belongs to this speaker (SPEC 5.2).

    In order: an unknown speaker has no attribution; a speaker that differs
    from the baseline's turn of the same sequence was changed by a person; a
    backfill baseline names its rows itself; an available diarization named
    the rest; anything else has none.
    """
    if _is_unknown(speaker_key):
        return "none"
    if baseline_speaker_by_sequence.get(sequence) != speaker_key:
        return "person"
    if baseline_source == "backfill":
        return "backfill"
    if baseline_diarization_status == "available":
        return "diarization"
    return "none"


def _overlap_intervals(
    turns: Sequence[TimelineSegmentData],
) -> list[TimelineOverlap]:
    """The maximal ranges where the set of active turns holds two or more
    turns (SPEC 5.4).

    Half-open intervals ``[start_ms, end_ms)``: a zero-length turn takes part
    in no overlap and two turns that only touch do not overlap.  A sweep over
    the start and end points of every positive-length turn closes one entry
    whenever the active set changes, so entries never merge and the list is
    ordered by ``start_ms``, then ``end_ms``.
    """
    positive = [turn for turn in turns if turn.end_ms > turn.start_ms]
    points = sorted({point for turn in positive for point in (turn.start_ms, turn.end_ms)})
    intervals: list[TimelineOverlap] = []
    for start_ms, end_ms in zip(points, points[1:], strict=False):
        if end_ms <= start_ms:
            continue
        active = [turn for turn in positive if turn.start_ms <= start_ms < turn.end_ms]
        if len(active) < 2:
            continue
        keys = {turn.speaker_key for turn in active}
        includes_unknown = any(_is_unknown(turn.speaker_key) for turn in active)
        kind = "cross_speaker" if len(keys) >= 2 or includes_unknown else "same_speaker"
        intervals.append(
            TimelineOverlap(
                index=len(intervals),
                start_ms=start_ms,
                end_ms=end_ms,
                kind=kind,
                speaker_keys=sorted(keys),
                segment_ids=[turn.id for turn in sorted(active, key=lambda t: t.sequence)],
                includes_unknown=includes_unknown,
            )
        )
    return intervals


def build_timeline(
    *,
    segments: Sequence[TimelineSegmentData],
    speakers: Sequence[TimelineSpeakerData],
    baseline_speaker_by_sequence: Mapping[int, str],
    baseline_source: str,
    recording_duration_ms: int | None,
    baseline_diarization_status: str | None = None,
) -> TimelineParts:
    """Lanes, turns, overlap intervals and totals of one revision.

    ``segments`` are the revision's turns ordered any way (the output is
    ordered by ``sequence``); ``speakers`` the meeting's ``MeetingSpeaker``
    rows; ``baseline_speaker_by_sequence`` the baseline revision's
    ``(sequence, speaker_key)`` pairs (the revision's own when it is the
    baseline); ``baseline_source`` and ``baseline_diarization_status`` drive
    the attribution; ``recording_duration_ms`` bounds the recording.
    """
    ordered = sorted(segments, key=lambda turn: turn.sequence)
    overlaps = _overlap_intervals(ordered)

    # every interval each turn takes part in, and whether one is cross_speaker
    overlap_ids: dict[str, list[int]] = {}
    cross_ids: dict[str, list[int]] = {}
    for interval in overlaps:
        for turn_id in interval.segment_ids:
            overlap_ids.setdefault(turn_id, []).append(interval.index)
            if interval.kind == "cross_speaker":
                cross_ids.setdefault(turn_id, []).append(interval.index)

    turns: list[TimelineTurn] = []
    speech_ms: dict[str, int] = {}
    turn_count: dict[str, int] = {}
    unknown_speech_ms = 0
    for turn in ordered:
        reasons: list[str] = []
        if _is_unknown(turn.speaker_key):
            reasons.append("unknown_speaker")
        if cross_ids.get(turn.id):
            reasons.append("overlap")
        if recording_duration_ms is not None and turn.end_ms > recording_duration_ms:
            reasons.append("outside_recording")
        turns.append(
            TimelineTurn(
                segment_id=turn.id,
                sequence=turn.sequence,
                speaker_key=turn.speaker_key,
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
                confidence=turn.confidence,
                attribution=_attribution(
                    turn.speaker_key,
                    turn.sequence,
                    baseline_speaker_by_sequence,
                    baseline_source,
                    baseline_diarization_status,
                ),
                overlap_ids=overlap_ids.get(turn.id, []),
                review_reasons=reasons,
            )
        )
        length = turn.end_ms - turn.start_ms
        speech_ms[turn.speaker_key] = speech_ms.get(turn.speaker_key, 0) + length
        turn_count[turn.speaker_key] = turn_count.get(turn.speaker_key, 0) + 1
        if _is_unknown(turn.speaker_key):
            unknown_speech_ms += length

    # the union of the cross_speaker intervals each lane is in; the intervals
    # of the sweep are disjoint, so the sum is the union's length
    lane_overlap_ms: dict[str, int] = {}
    total_overlap_ms = 0
    for interval in overlaps:
        if interval.kind != "cross_speaker":
            continue
        total_overlap_ms += interval.end_ms - interval.start_ms
        for speaker_key in interval.speaker_keys:
            lane_overlap_ms[speaker_key] = (
                lane_overlap_ms.get(speaker_key, 0) + interval.end_ms - interval.start_ms
            )

    speaker_by_key = {speaker.speaker_key: speaker for speaker in speakers}
    lane_keys = set(speech_ms) | set(speaker_by_key)
    known = sorted(key for key in lane_keys if not _is_unknown(key))
    unknown = sorted(key for key in lane_keys if _is_unknown(key))
    lanes: list[TimelineLane] = []
    for key in known + unknown:
        row = speaker_by_key.get(key)
        if _is_unknown(key):
            kind = "unknown"
        elif row is not None and (
            (row.display_name is not None and row.display_name != "")
            or (row.identity_type is not None and row.identity_type != "unknown")
        ):
            kind = "identified"
        else:
            kind = "anonymous"
        lanes.append(
            TimelineLane(
                speaker_key=key,
                # never fabricate a name: only the stored value
                display_name=row.display_name if row is not None else None,
                kind=kind,
                identity_type=row.identity_type if row is not None else None,
                speaker_confidence=row.confidence if row is not None else None,
                turn_count=turn_count.get(key, 0),
                speech_ms=speech_ms.get(key, 0),
                overlap_ms=lane_overlap_ms.get(key, 0),
            )
        )
    return TimelineParts(
        lanes=lanes,
        turns=turns,
        overlaps=overlaps,
        overlap_ms=total_overlap_ms,
        unknown_speech_ms=unknown_speech_ms,
    )
