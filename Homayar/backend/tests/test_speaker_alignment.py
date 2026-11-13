"""Speaker alignment of ASR units to diarization intervals (HOM-47).

Pure tests of ``backend.speech.speaker_alignment``: no database, no ffmpeg,
no provider, no audio. Every deny-case in the SPEC runs next to its
allow-control, and each control proves the rule caused the outcome. The last
tests exercise the one job wiring point, ``_transcript_turns``.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from backend.jobs.checkpoints import TURNS_VERSION
from backend.jobs.processor import _transcript_turns
from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRChunkSpan,
    ASRResult,
    ASRSegment,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    ProviderIssue,
)
from backend.speech.chunking import ChunkPlan
from backend.speech.speaker_alignment import (
    ALIGNMENT_VERSION,
    AlignmentReason,
    AlignmentStatus,
    AlignSettings,
    AsrUnit,
    AsrWord,
    SpeakerAlignment,
    TimeBase,
    align_speakers,
    map_diarization,
    original_sample,
)
from backend.speech.speaker_reconcile import ChunkDiarization, reconcile_chunk_speakers


def _seg(
    label: str, start: float, end: float, confidence: float | None = None
) -> DiarizationSegment:
    return DiarizationSegment(
        speaker_label=label, start_seconds=start, end_seconds=end, confidence=confidence
    )


def _result(
    *segments: DiarizationSegment, provider: str = "fake-diarizer", mock: bool = False
) -> DiarizationResult:
    return DiarizationResult(
        provider=provider, status=DiarizationStatus.AVAILABLE, segments=list(segments), is_mock=mock
    )


def _unavailable(provider: str = "fake-diarizer", mock: bool = False) -> DiarizationResult:
    return DiarizationResult(
        provider=provider,
        status=DiarizationStatus.UNAVAILABLE,
        segments=[],
        issue=ProviderIssue(code="diarizer_down", message="the diarizer did not run"),
        is_mock=mock,
    )


def _word(start: float, end: float, text: str) -> AsrWord:
    return AsrWord(start_seconds=start, end_seconds=end, text=text)


def _unit(
    start: float,
    end: float,
    text: str = "hello",
    words: tuple[AsrWord, ...] = (),
    confidence: float | None = None,
) -> AsrUnit:
    return AsrUnit(
        start_seconds=start, end_seconds=end, text=text, words=words, confidence=confidence
    )


def _check_invariants(alignment: SpeakerAlignment, duration: float) -> None:
    """Every output span is inside the recording bounds (SPEC acceptance 9)."""
    ceiling_ms = math.ceil(duration * 1000)
    for turn in alignment.turns:
        assert 0 <= turn.start_seconds <= turn.end_seconds <= duration
        assert 0 <= turn.start_ms <= turn.end_ms <= ceiling_ms


# --- assigned, straddles, overlap, coverage -----------------------------------------------


def test_fully_contained_segment_is_assigned_with_confidence_one() -> None:
    alignment = align_speakers(
        [_unit(1.0, 2.0)],
        _result(_seg("speaker_1", 0.0, 10.0)),
        recording_duration_seconds=100.0,
    )
    assert len(alignment.turns) == 1
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.confidence == 1.0
    assert turn.confidence_basis == "coverage_share_uncalibrated"
    assert turn.reasons == ()
    assert turn.candidates == (("speaker_1", 1.0),)
    assert turn.unit_index == 0
    assert (turn.start_seconds, turn.end_seconds) == (1.0, 2.0)
    assert (turn.start_ms, turn.end_ms) == (1000, 2000)
    assert turn.start_sample is None and turn.end_sample is None
    _check_invariants(alignment, 100.0)


def test_straddling_60_40_is_ambiguous() -> None:
    alignment = align_speakers(
        [_unit(10.0, 20.0)],
        _result(_seg("speaker_1", 10.0, 16.0), _seg("speaker_2", 16.0, 20.0)),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.AMBIGUOUS
    assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
    assert AlignmentReason.STRADDLES_SPEAKERS in turn.reasons
    assert turn.confidence is None
    assert turn.candidates == (("speaker_1", 0.6), ("speaker_2", 0.4))
    _check_invariants(alignment, 100.0)


def test_straddling_90_10_is_assigned_control() -> None:
    alignment = align_speakers(
        [_unit(10.0, 20.0)],
        _result(_seg("speaker_1", 10.0, 19.0), _seg("speaker_2", 19.0, 20.0)),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.confidence == 0.9
    _check_invariants(alignment, 100.0)


def test_thirty_percent_overlap_is_ambiguous() -> None:
    alignment = align_speakers(
        [_unit(0.0, 10.0)],
        _result(
            _seg("speaker_1", 0.0, 3.0),
            _seg("speaker_2", 0.0, 3.0),
            _seg("speaker_1", 3.0, 10.0),
        ),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.AMBIGUOUS
    assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
    assert AlignmentReason.OVERLAP in turn.reasons
    _check_invariants(alignment, 100.0)


def test_same_label_overlap_is_not_overlap_control() -> None:
    alignment = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg("speaker_1", 0.0, 6.0), _seg("speaker_1", 5.0, 10.0)),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.confidence == 1.0
    assert turn.reasons == ()
    _check_invariants(alignment, 100.0)


def test_thirty_percent_coverage_is_no_speaker() -> None:
    alignment = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg("speaker_1", 0.0, 3.0)),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.UNKNOWN
    assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
    assert turn.reasons == (AlignmentReason.NO_SPEAKER,)
    assert turn.candidates == (("speaker_1", 1.0),)
    _check_invariants(alignment, 100.0)


def test_sixty_percent_coverage_is_assigned_control() -> None:
    alignment = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg("speaker_1", 0.0, 6.0)),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.confidence == 1.0
    _check_invariants(alignment, 100.0)


def test_share_exactly_at_a_threshold_follows_the_inequality() -> None:
    # overlap exactly 1/2 with max_overlap_share=0.5 (exact in binary): >= fires.
    overlap = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg("speaker_1", 0.0, 5.0), _seg("speaker_2", 0.0, 5.0)),
        recording_duration_seconds=100.0,
        settings=AlignSettings(max_overlap_share=0.5),
    )
    assert overlap.turns[0].status is AlignmentStatus.AMBIGUOUS
    assert AlignmentReason.OVERLAP in overlap.turns[0].reasons

    # coverage exactly 1/2 with min_coverage=0.5: < does not fire.
    coverage = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg("speaker_1", 0.0, 5.0)),
        recording_duration_seconds=100.0,
        settings=AlignSettings(min_coverage=0.5),
    )
    assert coverage.turns[0].status is AlignmentStatus.ASSIGNED

    # dominant share exactly 3/4 with dominant_share=0.75: < does not fire.
    dominant = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg("speaker_1", 0.0, 7.5), _seg("speaker_2", 7.5, 10.0)),
        recording_duration_seconds=100.0,
        settings=AlignSettings(dominant_share=0.75),
    )
    assert dominant.turns[0].status is AlignmentStatus.ASSIGNED
    assert dominant.turns[0].confidence == 0.75


# --- unavailable diarization ---------------------------------------------------------------


@pytest.mark.parametrize(
    "diarization",
    [
        pytest.param(None, id="none"),
        pytest.param(_unavailable(), id="unavailable"),
    ],
)
def test_missing_diarization_leaves_every_turn_unknown(diarization) -> None:
    alignment = align_speakers(
        [_unit(0.0, 2.0), _unit(3.0, 4.0)],
        diarization,
        recording_duration_seconds=10.0,
    )
    assert len(alignment.turns) == 2
    for turn in alignment.turns:
        assert turn.status is AlignmentStatus.UNKNOWN
        assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
        assert turn.reasons == (AlignmentReason.DIARIZATION_UNAVAILABLE,)
        assert turn.candidates == ()
        assert turn.confidence is None
    _check_invariants(alignment, 10.0)


# --- words ---------------------------------------------------------------------------------


def test_words_align_and_join_into_turns_by_speaker() -> None:
    unit = _unit(
        0.0,
        7.0,
        text="a1 a2 a3 mid b1 b2",
        words=(
            _word(0.0, 0.5, "a1"),
            _word(0.5, 1.0, "a2"),
            _word(1.0, 1.5, "a3"),
            _word(4.5, 5.5, "mid"),
            _word(6.0, 6.5, "b1"),
            _word(6.5, 7.0, "b2"),
        ),
    )
    alignment = align_speakers(
        [unit],
        _result(_seg("speaker_1", 0.0, 5.0), _seg("speaker_2", 5.0, 10.0)),
        recording_duration_seconds=100.0,
    )
    assert [(turn.text, turn.status, turn.speaker_label) for turn in alignment.turns] == [
        ("a1 a2 a3", AlignmentStatus.ASSIGNED, "speaker_1"),
        ("mid", AlignmentStatus.AMBIGUOUS, UNKNOWN_SPEAKER_LABEL),
        ("b1 b2", AlignmentStatus.ASSIGNED, "speaker_2"),
    ]
    first, middle, last = alignment.turns
    assert (first.start_seconds, first.end_seconds) == (0.0, 1.5)
    assert (first.start_ms, first.end_ms) == (0, 1500)
    assert first.confidence == 1.0
    assert (middle.start_seconds, middle.end_seconds) == (4.5, 5.5)
    assert AlignmentReason.STRADDLES_SPEAKERS in middle.reasons
    assert (last.start_seconds, last.end_seconds) == (6.0, 7.0)
    assert last.confidence == 1.0
    assert all(turn.unit_index == 0 for turn in alignment.turns)
    _check_invariants(alignment, 100.0)


def test_turns_never_join_across_units() -> None:
    units = [
        _unit(0.0, 1.0, words=(_word(0.0, 1.0, "a1"),)),
        _unit(2.0, 3.0, words=(_word(2.0, 3.0, "a2"),)),
    ]
    alignment = align_speakers(
        units,
        _result(_seg("speaker_1", 0.0, 4.0)),
        recording_duration_seconds=10.0,
    )
    assert [turn.text for turn in alignment.turns] == ["a1", "a2"]
    assert [turn.unit_index for turn in alignment.turns] == [0, 1]


def test_zero_length_span_is_no_duration() -> None:
    # The mock ASR emits one 0..0 segment; it must stay speaker_unknown.
    alignment = align_speakers(
        [_unit(0.0, 0.0)],
        _result(_seg("speaker_1", 0.0, 10.0)),
        recording_duration_seconds=10.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.UNKNOWN
    assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
    assert turn.reasons == (AlignmentReason.NO_DURATION,)
    assert (turn.start_ms, turn.end_ms) == (0, 0)
    _check_invariants(alignment, 10.0)


# --- chunk time mapping ---------------------------------------------------------------------


def test_time_base_maps_local_seconds_to_original() -> None:
    time_base = TimeBase(offset_samples=9_600_000, sample_rate=16_000)
    assert time_base.seconds_to_original(1.0) == Fraction(601)
    assert time_base.seconds_to_original(2.0) == Fraction(602)
    assert time_base.sample_to_original(16_000) == Fraction(601)
    alignment = align_speakers(
        [_unit(1.0, 2.0)],
        _result(_seg("speaker_1", 601.0, 602.0)),
        recording_duration_seconds=700.0,
        time_base=time_base,
    )
    turn = alignment.turns[0]
    assert (turn.start_seconds, turn.end_seconds) == (601.0, 602.0)
    assert (turn.start_ms, turn.end_ms) == (601_000, 602_000)
    assert turn.status is AlignmentStatus.ASSIGNED
    _check_invariants(alignment, 700.0)


def test_from_chunk_plan_and_from_span_agree() -> None:
    plan = ChunkPlan(
        index=3,
        start_sample=9_600_000,
        end_sample=17_600_000,
        owned_start_sample=9_600_000,
        owned_end_sample=17_600_000,
        sample_rate=16_000,
    )
    span = ASRChunkSpan(
        index=3,
        start_seconds=600.0,
        end_seconds=1100.0,
        owned_start_seconds=600.0,
        owned_end_seconds=1100.0,
    )
    assert TimeBase.from_chunk_plan(plan) == TimeBase.from_span(span, 16_000)
    assert TimeBase.from_chunk_plan(plan) == TimeBase(9_600_000, 16_000)
    assert TimeBase() == TimeBase(0, 16_000)


def test_map_diarization_shifts_chunk_local_segments() -> None:
    time_base = TimeBase(offset_samples=9_600_000, sample_rate=16_000)
    local = _result(_seg("speaker_1", 1.0, 2.0), mock=True)
    mapped = map_diarization(local, time_base)
    assert mapped.provider == "fake-diarizer"
    assert mapped.status == DiarizationStatus.AVAILABLE
    assert mapped.is_mock is True
    assert [(seg.speaker_label, seg.start_seconds, seg.end_seconds) for seg in mapped.segments] == [
        ("speaker_1", 601.0, 602.0)
    ]
    unavailable_mapped = map_diarization(_unavailable(), time_base)
    assert unavailable_mapped.status == DiarizationStatus.UNAVAILABLE
    assert unavailable_mapped.issue is not None

    # The shifted unit aligns to the shifted speaker.
    alignment = align_speakers(
        [_unit(1.0, 2.0)],
        mapped,
        recording_duration_seconds=700.0,
        time_base=time_base,
    )
    assert alignment.turns[0].status is AlignmentStatus.ASSIGNED
    assert alignment.turns[0].speaker_label == "speaker_1"


# --- resampling -----------------------------------------------------------------------------


def test_original_sample_resamples_across_rates() -> None:
    assert original_sample(Fraction(601), 44_100) == 26_504_100
    assert original_sample(Fraction(601), 8_000) == 4_808_000
    alignment = align_speakers(
        [_unit(601.0, 602.0)],
        _result(_seg("speaker_1", 600.0, 603.0)),
        recording_duration_seconds=700.0,
        original_sample_rate=44_100,
    )
    turn = alignment.turns[0]
    assert turn.start_sample == 26_504_100
    assert turn.end_sample == 26_548_200


# --- bounds ---------------------------------------------------------------------------------


def test_unit_past_the_end_is_outside_recording() -> None:
    alignment = align_speakers(
        [_unit(12.0, 13.0)],
        _result(_seg("speaker_1", 0.0, 1e6)),
        recording_duration_seconds=10.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.UNKNOWN
    assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
    assert turn.reasons == (AlignmentReason.OUTSIDE_RECORDING,)
    assert (turn.start_seconds, turn.end_seconds) == (10.0, 10.0)
    assert (turn.start_ms, turn.end_ms) == (10_000, 10_000)
    _check_invariants(alignment, 10.0)


def test_unit_reaching_past_the_end_is_clamped_and_keeps_assignment() -> None:
    alignment = align_speakers(
        [_unit(8.0, 10.5)],
        _result(_seg("speaker_1", 0.0, 1e6)),
        recording_duration_seconds=10.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.reasons == (AlignmentReason.CLAMPED_TO_RECORDING,)
    assert (turn.start_seconds, turn.end_seconds) == (8.0, 10.0)
    _check_invariants(alignment, 10.0)


def test_unit_within_tolerance_is_clamped_silently() -> None:
    alignment = align_speakers(
        [_unit(8.0, 10.04)],
        _result(_seg("speaker_1", 0.0, 1e6)),
        recording_duration_seconds=10.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.reasons == ()
    assert (turn.start_seconds, turn.end_seconds) == (8.0, 10.0)
    _check_invariants(alignment, 10.0)


# --- unknown speakers and reconciliation ----------------------------------------------------


def test_unknown_speaker_intervals_leave_the_turn_unknown() -> None:
    alignment = align_speakers(
        [_unit(0.0, 10.0)],
        _result(_seg(UNKNOWN_SPEAKER_LABEL, 0.0, 1e6)),
        recording_duration_seconds=100.0,
    )
    turn = alignment.turns[0]
    assert turn.status is AlignmentStatus.UNKNOWN
    assert turn.speaker_label == UNKNOWN_SPEAKER_LABEL
    assert turn.reasons == (AlignmentReason.UNKNOWN_SPEAKER,)
    _check_invariants(alignment, 100.0)


def test_aligner_accepts_a_reconciliation_result_directly() -> None:
    span0 = ASRChunkSpan(
        index=0, start_seconds=0.0, end_seconds=10.0, owned_start_seconds=0.0, owned_end_seconds=8.0
    )
    span1 = ASRChunkSpan(
        index=1,
        start_seconds=8.0,
        end_seconds=18.0,
        owned_start_seconds=8.0,
        owned_end_seconds=18.0,
    )
    reconciliation = reconcile_chunk_speakers(
        [
            # Both chunks hear the shared audio [8, 10): chunk 0 up to local 10,
            # chunk 1 from local 0. The same speaker joins into one global label.
            ChunkDiarization(span=span0, result=_result(_seg("speaker_1", 0.0, 10.0))),
            ChunkDiarization(span=span1, result=_result(_seg("speaker_1", 0.0, 10.0))),
        ]
    )
    alignment = align_speakers(
        [_unit(3.0, 5.0), _unit(9.0, 11.0)],
        reconciliation.result,
        recording_duration_seconds=20.0,
    )
    assert [turn.speaker_label for turn in alignment.turns] == ["speaker_1", "speaker_1"]
    assert all(turn.status is AlignmentStatus.ASSIGNED for turn in alignment.turns)
    _check_invariants(alignment, 20.0)


# --- determinism and output shape ------------------------------------------------------------


def test_shuffled_diarization_segments_change_nothing() -> None:
    units = [_unit(0.0, 10.0), _unit(10.0, 12.0)]
    ordered = _result(
        _seg("speaker_1", 0.0, 6.0),
        _seg("speaker_2", 6.0, 12.0),
        _seg("speaker_3", 5.0, 7.0),
    )
    shuffled = _result(
        _seg("speaker_3", 5.0, 7.0),
        _seg("speaker_2", 6.0, 12.0),
        _seg("speaker_1", 0.0, 6.0),
    )
    first = align_speakers(units, ordered, recording_duration_seconds=20.0)
    second = align_speakers(units, shuffled, recording_duration_seconds=20.0)
    assert first == second


def test_version_settings_and_counts_are_reported() -> None:
    assert ALIGNMENT_VERSION == "speaker-alignment/1"
    settings = AlignSettings()
    descriptor = settings.descriptor()
    assert descriptor["version"] == "speaker-alignment/1"
    assert descriptor["min_coverage"] == 0.5
    assert descriptor["dominant_share"] == 0.8
    assert descriptor["max_overlap_share"] == 0.2
    assert descriptor["bounds_tolerance_seconds"] == 0.05

    alignment = align_speakers(
        [_unit(0.0, 1.0), _unit(1.0, 3.0), _unit(5.0, 6.0)],
        _result(_seg("speaker_1", 0.0, 2.0), _seg("speaker_2", 2.0, 4.0)),
        recording_duration_seconds=10.0,
        settings=settings,
    )
    assert alignment.version == ALIGNMENT_VERSION
    assert alignment.settings == descriptor
    assert alignment.assigned_count == 1
    assert alignment.ambiguous_count == 1
    assert alignment.unknown_count == 1
    assert len(alignment.turns) == 3
    # Sorted by (start, end, unit index).
    assert [turn.start_seconds for turn in alignment.turns] == [0.0, 1.0, 5.0]


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"min_coverage": True}, id="min-coverage-bool"),
        pytest.param({"min_coverage": float("nan")}, id="min-coverage-nan"),
        pytest.param({"min_coverage": float("inf")}, id="min-coverage-inf"),
        pytest.param({"min_coverage": 0.0}, id="min-coverage-zero"),
        pytest.param({"min_coverage": -0.5}, id="min-coverage-negative"),
        pytest.param({"min_coverage": 1.5}, id="min-coverage-above-one"),
        pytest.param({"dominant_share": 0.5}, id="dominant-share-at-half"),
        pytest.param({"dominant_share": 1.5}, id="dominant-share-above-one"),
        pytest.param({"dominant_share": float("nan")}, id="dominant-share-nan"),
        pytest.param({"max_overlap_share": 0.0}, id="overlap-share-zero"),
        pytest.param({"max_overlap_share": True}, id="overlap-share-bool"),
        pytest.param({"bounds_tolerance_seconds": -0.1}, id="tolerance-negative"),
        pytest.param({"bounds_tolerance_seconds": float("inf")}, id="tolerance-inf"),
    ],
)
def test_settings_reject_bad_values(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        AlignSettings(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"offset_samples": -1}, id="offset-negative"),
        pytest.param({"offset_samples": True}, id="offset-bool"),
        pytest.param({"offset_samples": 1.5}, id="offset-float"),
        pytest.param({"sample_rate": 0}, id="rate-zero"),
        pytest.param({"sample_rate": True}, id="rate-bool"),
    ],
)
def test_time_base_rejects_bad_values(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        TimeBase(**kwargs)


def test_units_and_words_reject_bad_values() -> None:
    with pytest.raises(ValueError):
        AsrWord(start_seconds=2.0, end_seconds=1.0, text="w")
    with pytest.raises(ValueError):
        AsrWord(start_seconds=-1.0, end_seconds=0.0, text="w")
    with pytest.raises(ValueError):
        AsrWord(start_seconds=0.0, end_seconds=1.0, text="  ")
    with pytest.raises(ValueError):
        AsrWord(start_seconds=float("nan"), end_seconds=1.0, text="w")
    with pytest.raises(ValueError):
        AsrWord(start_seconds=True, end_seconds=1.0, text="w")
    with pytest.raises(ValueError):
        AsrUnit(start_seconds=2.0, end_seconds=1.0, text="x")
    with pytest.raises(ValueError):
        AsrUnit(start_seconds=0.0, end_seconds=1.0, text="x", confidence=float("inf"))
    with pytest.raises(ValueError):
        # word outside its unit's range
        AsrUnit(start_seconds=0.0, end_seconds=10.0, text="x", words=(_word(0.0, 11.0, "w"),))
    with pytest.raises(ValueError):
        # words not sorted by start
        AsrUnit(
            start_seconds=0.0,
            end_seconds=10.0,
            text="x",
            words=(_word(5.0, 6.0, "b"), _word(0.0, 1.0, "a")),
        )
    # Containment holds within the 1e-6 tolerance.
    AsrUnit(start_seconds=0.0, end_seconds=10.0, text="x", words=(_word(9.9999995, 10.0, "w"),))


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"recording_duration_seconds": 0.0}, id="duration-zero"),
        pytest.param({"recording_duration_seconds": -5.0}, id="duration-negative"),
        pytest.param({"recording_duration_seconds": float("nan")}, id="duration-nan"),
        pytest.param({"recording_duration_seconds": float("inf")}, id="duration-inf"),
        pytest.param({"recording_duration_seconds": True}, id="duration-bool"),
        pytest.param({"original_sample_rate": 0}, id="rate-zero"),
        pytest.param({"original_sample_rate": True}, id="rate-bool"),
    ],
)
def test_align_speakers_rejects_bad_arguments(kwargs: dict) -> None:
    arguments: dict = {
        "units": [_unit(0.0, 1.0)],
        "diarization": _result(_seg("speaker_1", 0.0, 2.0)),
        "recording_duration_seconds": 10.0,
    }
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        align_speakers(**arguments)


# --- the job wiring point ---------------------------------------------------------------------


def _asr(*segments: ASRSegment) -> ASRResult:
    return ASRResult(
        provider="fake-asr",
        model="v1",
        language="fa",
        text=" ".join(segment.text for segment in segments),
        segments=list(segments),
        duration_seconds=10.0,
    )


def test_transcript_turns_keeps_a_straddling_segment_unknown() -> None:
    asr = _asr(
        ASRSegment(index=0, text="سلام", start_seconds=0.0, end_seconds=3.0),
        ASRSegment(index=1, text="خداحافظ", start_seconds=4.0, end_seconds=8.0),
    )
    diarization = _result(_seg("speaker_1", 0.0, 6.0), _seg("speaker_2", 6.0, 10.0))
    turns = _transcript_turns(
        asr, diarization, recording_duration_seconds=10
    )
    assert [(turn.speaker_label, turn.start_seconds, turn.end_seconds) for turn in turns] == [
        ("speaker_1", 0.0, 3.0),
        (UNKNOWN_SPEAKER_LABEL, 4.0, 8.0),
    ]


def test_transcript_turns_keeps_the_mock_zero_segment_unknown() -> None:
    asr = _asr(ASRSegment(index=0, text="پیگیری قرارداد انجام شود", start_seconds=0, end_seconds=0))
    turns = _transcript_turns(
        asr, _result(_seg("speaker_1", 0, 1e6)), recording_duration_seconds=12
    )
    assert len(turns) == 1
    assert turns[0].speaker_label == UNKNOWN_SPEAKER_LABEL
    assert (turns[0].start_seconds, turns[0].end_seconds) == (0.0, 0.0)


# --- SPEC section 11 (review round 1) ---------------------------------------------------------


def test_transcript_turns_preserve_asr_input_order() -> None:
    # The transcript writer pairs turns[i] with asr.segments[i].confidence, so
    # two segments with the same start must come back in input order.
    asr = _asr(
        ASRSegment(index=0, text="long", start_seconds=5.0, end_seconds=7.0, confidence=0.9),
        ASRSegment(index=1, text="short", start_seconds=5.0, end_seconds=6.0, confidence=0.2),
    )
    turns = _transcript_turns(
        asr, _result(_seg("speaker_1", 0.0, 10.0)), recording_duration_seconds=10
    )
    assert [turn.text for turn in turns] == ["long", "short"]
    assert [(turn.start_seconds, turn.end_seconds) for turn in turns] == [(5.0, 7.0), (5.0, 6.0)]


def test_turns_version_is_two() -> None:
    # The intelligence checkpoint key carries this version: an analysis built
    # from forced speakers must not be reused after HOM-47.
    assert TURNS_VERSION == 2


@pytest.mark.parametrize(
    ("unit_start", "unit_end", "status", "reasons", "span_start", "span_end"),
    [
        pytest.param(
            10.0,
            12.0,
            AlignmentStatus.UNKNOWN,
            (AlignmentReason.OUTSIDE_RECORDING,),
            10.0,
            10.0,
            id="starts-at-end-extends-past",
        ),
        pytest.param(
            10.03,
            12.0,
            AlignmentStatus.UNKNOWN,
            (AlignmentReason.OUTSIDE_RECORDING,),
            10.0,
            10.0,
            id="starts-just-past-end",
        ),
        pytest.param(
            10.1,
            12.0,
            AlignmentStatus.UNKNOWN,
            (AlignmentReason.OUTSIDE_RECORDING,),
            10.0,
            10.0,
            id="starts-clearly-past-end",
        ),
        pytest.param(
            9.0,
            10.5,
            AlignmentStatus.ASSIGNED,
            (AlignmentReason.CLAMPED_TO_RECORDING,),
            9.0,
            10.0,
            id="reaches-past-end-keeps-assignment",
        ),
        pytest.param(
            9.0,
            10.03,
            AlignmentStatus.ASSIGNED,
            (),
            9.0,
            10.0,
            id="within-tolerance-clamps-silently",
        ),
        pytest.param(
            10.0,
            10.0,
            AlignmentStatus.UNKNOWN,
            (AlignmentReason.NO_DURATION,),
            10.0,
            10.0,
            id="zero-length-at-the-bound",
        ),
    ],
)
def test_bounds_rule_at_the_end_bound(
    unit_start: float,
    unit_end: float,
    status: AlignmentStatus,
    reasons: tuple[AlignmentReason, ...],
    span_start: float,
    span_end: float,
) -> None:
    # A span with no part inside (0, duration) is outside_recording when the
    # part past the far bound (end - duration) beats the tolerance; a span
    # with a part inside is clamped (with a reason) only past the tolerance.
    alignment = align_speakers(
        [_unit(unit_start, unit_end)],
        _result(_seg("speaker_1", 0.0, 1e6)),
        recording_duration_seconds=10.0,
    )
    turn = alignment.turns[0]
    assert turn.status is status
    assert turn.reasons == reasons
    assert (turn.start_seconds, turn.end_seconds) == (span_start, span_end)
    _check_invariants(alignment, 10.0)


def test_joined_word_turn_sums_label_times_over_its_words() -> None:
    # Word a [0, 1]: speaker_1 covers it fully (1.0 s of 1.0 s covered).
    # Word b [2, 3]: speaker_1 still covers it fully (1.0 s, it spans [0, 10])
    # and speaker_2 overlaps on [2.9, 3], covered 1.0 s. Both words are
    # assigned speaker_1, so they join. The turn's shares come from the sums
    # over its words: speaker_1 (1.0 + 1.0) / 2.0 = 1.0, speaker_2 0.1 / 2.0,
    # confidence = speaker_1's share = 1.0. The speaker_2 share is derived
    # from the exact float inputs (2.9 is not exact in binary).
    speaker_2_time = Fraction(3.0) - Fraction(2.9)
    covered = Fraction(1.0) + Fraction(1.0)
    unit = _unit(
        0.0,
        3.0,
        text="a b",
        words=(_word(0.0, 1.0, "a"), _word(2.0, 3.0, "b")),
    )
    alignment = align_speakers(
        [unit],
        _result(_seg("speaker_1", 0.0, 10.0), _seg("speaker_2", 2.9, 3.0)),
        recording_duration_seconds=10.0,
    )
    assert len(alignment.turns) == 1
    turn = alignment.turns[0]
    assert turn.text == "a b"
    assert turn.status is AlignmentStatus.ASSIGNED
    assert turn.speaker_label == "speaker_1"
    assert turn.candidates == (
        ("speaker_1", 1.0),
        ("speaker_2", float(speaker_2_time / covered)),
    )
    assert turn.confidence == 1.0
    assert (turn.start_seconds, turn.end_seconds) == (0.0, 3.0)
    _check_invariants(alignment, 10.0)
