"""HOM-47 acceptance oracle for ``backend.speech.speaker_alignment``, written from the SPEC only.

Pure tests: fake ASR units and fake diarization results. No model, no audio, no database.
"""

from __future__ import annotations

import importlib
import json
import math
from fractions import Fraction

import pytest

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
from backend.speech.speaker_reconcile import ChunkDiarization, reconcile_chunk_speakers

UNKNOWN = UNKNOWN_SPEAKER_LABEL


def _sa():
    return importlib.import_module("backend.speech.speaker_alignment")


def _v(value):
    return getattr(value, "value", value)


def _status(turn) -> str:
    return _v(turn.status)


def _reasons(turn) -> tuple[str, ...]:
    return tuple(_v(reason) for reason in turn.reasons)


def _labels(turn) -> tuple[str, ...]:
    return tuple(label for label, _share in turn.candidates)


def _seg(label: str, start: float, end: float) -> DiarizationSegment:
    return DiarizationSegment(speaker_label=label, start_seconds=start, end_seconds=end)


def _avail(*segments: DiarizationSegment, provider: str = "fake-diarizer") -> DiarizationResult:
    return DiarizationResult(
        provider=provider, status=DiarizationStatus.AVAILABLE, segments=list(segments)
    )


def _unavailable(*segments: DiarizationSegment) -> DiarizationResult:
    return DiarizationResult(
        provider="fake-diarizer",
        status=DiarizationStatus.UNAVAILABLE,
        segments=list(segments),
        issue=ProviderIssue(code="diarizer_down", message="the diarizer did not run"),
        is_mock=True,
    )


def _unit(start, end, text="hello", words=()):
    return _sa().AsrUnit(start_seconds=start, end_seconds=end, text=text, words=tuple(words))


def _word(start, end, text):
    return _sa().AsrWord(start_seconds=start, end_seconds=end, text=text)


def _check(alignment, duration) -> None:
    limit = math.ceil(duration * 1000)
    keys = []
    for turn in alignment.turns:
        assert isinstance(turn.start_seconds, float) and isinstance(turn.end_seconds, float)
        assert 0 <= turn.start_seconds <= turn.end_seconds <= duration
        assert isinstance(turn.start_ms, int) and isinstance(turn.end_ms, int)
        assert 0 <= turn.start_ms <= turn.end_ms <= limit
        assert _status(turn) in {"assigned", "ambiguous", "unknown"}
        if _status(turn) == "assigned":
            assert turn.speaker_label != UNKNOWN
            assert turn.confidence is not None
        else:
            assert turn.speaker_label == UNKNOWN
            assert turn.confidence is None
            assert turn.reasons
        assert list(_reasons(turn)) == sorted(_reasons(turn))
        assert list(turn.candidates) == sorted(
            turn.candidates, key=lambda item: (-item[1], item[0])
        )
        assert turn.confidence_basis == "coverage_share_uncalibrated"
        keys.append((turn.start_seconds, turn.end_seconds, turn.unit_index))
    assert keys == sorted(keys)


def _align(units, diarization, duration, **kwargs):
    alignment = _sa().align_speakers(
        units, diarization, recording_duration_seconds=duration, **kwargs
    )
    _check(alignment, duration)
    return alignment


def _one(units, diarization, duration=20.0, **kwargs):
    alignment = _align(units, diarization, duration, **kwargs)
    assert len(alignment.turns) == 1
    return alignment.turns[0]


def test_version_constant_and_settings_descriptor():
    sa = _sa()
    assert sa.ALIGNMENT_VERSION == "speaker-alignment/1"
    descriptor = sa.AlignSettings().descriptor()
    assert "speaker-alignment/1" in descriptor.values()
    json.dumps(descriptor)
    assert sa.AlignSettings(min_coverage=0.75).descriptor() != descriptor
    alignment = _align([_unit(1.0, 2.0)], _avail(_seg("A", 0.0, 5.0)), 10.0)
    assert alignment.version == "speaker-alignment/1"
    assert alignment.settings == descriptor


def test_ac1_a_unit_inside_one_speaker_is_assigned_with_full_confidence():
    turn = _one(
        [_unit(2.0, 8.0, text="salam")],
        _avail(_seg("A", 0.0, 10.0), _seg("B", 10.0, 20.0)),
    )
    assert _status(turn) == "assigned"
    assert turn.speaker_label == "A"
    assert turn.confidence == pytest.approx(1.0)
    assert _reasons(turn) == ()
    assert _labels(turn) == ("A",)
    assert turn.candidates[0][1] == pytest.approx(1.0)
    assert turn.text == "salam"
    assert turn.unit_index == 0
    assert (turn.start_seconds, turn.end_seconds) == (2.0, 8.0)
    assert (turn.start_ms, turn.end_ms) == (2000, 8000)
    assert turn.start_sample is None and turn.end_sample is None


@pytest.mark.parametrize(
    ("segments", "settings", "status", "label", "reasons", "shares"),
    [
        pytest.param(
            (("A", 0.0, 6.0), ("B", 6.0, 10.0)),
            {},
            "ambiguous",
            UNKNOWN,
            ("straddles_speakers",),
            (("A", 0.6), ("B", 0.4)),
            id="straddle-60-40-ambiguous",
        ),
        pytest.param(
            (("A", 0.0, 9.0), ("B", 9.0, 10.0)),
            {},
            "assigned",
            "A",
            (),
            (("A", 0.9), ("B", 0.1)),
            id="control-90-10-assigned",
        ),
        pytest.param(
            (("B", 0.0, 4.0), ("A", 4.0, 10.0)),
            {},
            "ambiguous",
            UNKNOWN,
            ("straddles_speakers",),
            (("A", 0.6), ("B", 0.4)),
            id="straddle-40-60-reversed-order",
        ),
        pytest.param(
            (("A", 0.0, 5.0), ("B", 5.0, 10.0)),
            {"dominant_share": 0.51},
            "ambiguous",
            UNKNOWN,
            ("straddles_speakers",),
            (("A", 0.5), ("B", 0.5)),
            id="equal-split-never-assigned",
        ),
        pytest.param(
            (("A", 0.0, 7.5), ("B", 7.5, 10.0)),
            {"dominant_share": 0.75},
            "assigned",
            "A",
            (),
            (("A", 0.75), ("B", 0.25)),
            id="share-exactly-at-dominant-share-assigned",
        ),
        pytest.param(
            (("A", 0.0, 7.1875), ("B", 7.1875, 10.0)),
            {"dominant_share": 0.75},
            "ambiguous",
            UNKNOWN,
            ("straddles_speakers",),
            (("A", 0.71875), ("B", 0.28125)),
            id="share-just-under-dominant-share-ambiguous",
        ),
    ],
)
def test_ac2_straddling_two_speakers(segments, settings, status, label, reasons, shares):
    sa = _sa()
    diarization = _avail(*(_seg(*segment) for segment in segments))
    turn = _one([_unit(0.0, 10.0)], diarization, settings=sa.AlignSettings(**settings))
    assert _status(turn) == status
    assert turn.speaker_label == label
    assert _reasons(turn) == reasons
    assert _labels(turn) == tuple(name for name, _share in shares)
    for (name, share), (got_name, got_share) in zip(shares, turn.candidates, strict=True):
        assert got_name == name
        assert got_share == pytest.approx(share)
    if status == "assigned":
        assert turn.confidence == pytest.approx(shares[0][1])


@pytest.mark.parametrize(
    ("segments", "settings", "status", "label", "reasons"),
    [
        pytest.param(
            (("A", 0.0, 6.5), ("B", 3.5, 10.0)),
            {},
            "ambiguous",
            UNKNOWN,
            ("overlap",),
            id="overlap-30pct-two-labels-ambiguous",
        ),
        pytest.param(
            (("A", 0.0, 6.5), ("A", 3.5, 10.0)),
            {},
            "assigned",
            "A",
            (),
            id="control-same-label-twice-is-not-overlap",
        ),
        pytest.param(
            (("A", 0.0, 10.0), ("B", 9.0, 10.0)),
            {},
            "assigned",
            "A",
            (),
            id="control-overlap-10pct-under-threshold-assigned",
        ),
        pytest.param(
            (("A", 0.0, 10.0), ("B", 7.5, 10.0)),
            {"max_overlap_share": 0.25},
            "ambiguous",
            UNKNOWN,
            ("overlap",),
            id="overlap-exactly-at-threshold-ambiguous",
        ),
        pytest.param(
            (("A", 0.0, 10.0), ("B", 7.75, 10.0)),
            {"max_overlap_share": 0.25},
            "assigned",
            "A",
            (),
            id="control-overlap-just-under-threshold-assigned",
        ),
        pytest.param(
            (("A", 0.0, 3.0), ("B", 0.0, 3.0)),
            {},
            "ambiguous",
            UNKNOWN,
            ("overlap",),
            id="overlap-decides-before-low-coverage",
        ),
    ],
)
def test_ac3_overlapping_speech(segments, settings, status, label, reasons):
    sa = _sa()
    diarization = _avail(*(_seg(*segment) for segment in segments))
    turn = _one([_unit(0.0, 10.0)], diarization, settings=sa.AlignSettings(**settings))
    assert _status(turn) == status
    assert turn.speaker_label == label
    assert _reasons(turn) == reasons
    assert set(_labels(turn)) == {segment[0] for segment in segments}
    if status == "assigned":
        assert turn.confidence == pytest.approx(1.0)


def test_ac3_overlap_turn_names_both_candidates():
    turn = _one([_unit(0.0, 10.0)], _avail(_seg("A", 0.0, 6.5), _seg("B", 3.5, 10.0)))
    assert _labels(turn) == ("A", "B")
    assert turn.candidates[0][1] == pytest.approx(0.65)
    assert turn.candidates[1][1] == pytest.approx(0.65)


@pytest.mark.parametrize(
    ("end", "status", "label", "reasons"),
    [
        pytest.param(3.0, "unknown", UNKNOWN, ("no_speaker",), id="coverage-30pct-unknown"),
        pytest.param(6.0, "assigned", "A", (), id="control-coverage-60pct-assigned"),
        pytest.param(5.0, "assigned", "A", (), id="coverage-exactly-50pct-assigned"),
        pytest.param(4.875, "unknown", UNKNOWN, ("no_speaker",), id="coverage-just-under-50pct"),
    ],
)
def test_ac4_coverage(end, status, label, reasons):
    turn = _one([_unit(0.0, 10.0)], _avail(_seg("A", 0.0, end), _seg("B", 15.0, 20.0)))
    assert _status(turn) == status
    assert turn.speaker_label == label
    assert _reasons(turn) == reasons
    assert _labels(turn) == ("A",)
    if status == "assigned":
        assert turn.confidence == pytest.approx(1.0)


@pytest.mark.parametrize(
    "diarization",
    [
        pytest.param(None, id="none"),
        pytest.param(_unavailable(), id="unavailable-empty"),
        pytest.param(_unavailable(_seg(UNKNOWN, 0.0, 20.0)), id="unavailable-unknown-segments"),
    ],
)
def test_ac5_missing_diarization_leaves_every_turn_unknown(diarization):
    units = [_unit(0.0, 4.0, "one"), _unit(5.0, 9.0, "two"), _unit(10.0, 15.0, "three")]
    alignment = _align(units, diarization, 20.0)
    assert len(alignment.turns) == 3
    for turn in alignment.turns:
        assert _status(turn) == "unknown"
        assert turn.speaker_label == UNKNOWN
        assert _reasons(turn) == ("diarization_unavailable",)
        assert turn.candidates == ()
        assert turn.confidence is None


def test_ac5_control_available_diarization_assigns_the_same_units():
    units = [_unit(0.0, 4.0, "one"), _unit(5.0, 9.0, "two"), _unit(10.0, 15.0, "three")]
    alignment = _align(units, _avail(_seg("A", 0.0, 20.0)), 20.0)
    assert [(_status(turn), turn.speaker_label) for turn in alignment.turns] == [
        ("assigned", "A"),
        ("assigned", "A"),
        ("assigned", "A"),
    ]


@pytest.mark.parametrize(
    ("start", "end", "status", "reasons"),
    [
        pytest.param(0.0, 0.0, "unknown", ("no_duration",), id="zero-length-at-0"),
        pytest.param(3.0, 3.0, "unknown", ("no_duration",), id="zero-length-inside-speaker"),
        pytest.param(3.0, 3.5, "assigned", (), id="control-short-span-assigned"),
    ],
)
def test_zero_length_span_is_unknown(start, end, status, reasons):
    turn = _one([_unit(start, end)], _avail(_seg("A", 0.0, 10.0)))
    assert _status(turn) == status
    assert _reasons(turn) == reasons


def test_ac6_words_split_into_turns_by_speaker():
    words = [
        _word(0.0, 1.0, "w1"),
        _word(1.2, 2.0, "w2"),
        _word(2.2, 3.5, "w3"),
        _word(6.5, 8.0, "w4"),
        _word(8.2, 10.0, "w5"),
    ]
    alignment = _align(
        [_unit(0.0, 10.0, "w1 w2 w3 w4 w5", words)],
        _avail(_seg("A", 0.0, 5.0), _seg("B", 5.0, 10.0)),
        10.0,
    )
    got = [
        (turn.text, _status(turn), turn.speaker_label, turn.start_seconds, turn.end_seconds)
        for turn in alignment.turns
    ]
    assert got == [
        ("w1 w2 w3", "assigned", "A", 0.0, 3.5),
        ("w4 w5", "assigned", "B", 6.5, 10.0),
    ]
    assert [turn.unit_index for turn in alignment.turns] == [0, 0]


def test_ac6_a_word_on_the_boundary_becomes_its_own_ambiguous_turn():
    words = [
        _word(0.0, 1.0, "w1"),
        _word(1.2, 2.0, "w2"),
        _word(2.2, 3.5, "w3"),
        _word(4.0, 6.0, "edge"),
        _word(6.5, 8.0, "w4"),
        _word(8.2, 10.0, "w5"),
    ]
    alignment = _align(
        [_unit(0.0, 10.0, "w1 w2 w3 edge w4 w5", words)],
        _avail(_seg("A", 0.0, 5.0), _seg("B", 5.0, 10.0)),
        10.0,
    )
    got = [
        (
            turn.text,
            _status(turn),
            turn.speaker_label,
            _reasons(turn),
            turn.start_seconds,
            turn.end_seconds,
        )
        for turn in alignment.turns
    ]
    assert got == [
        ("w1 w2 w3", "assigned", "A", (), 0.0, 3.5),
        ("edge", "ambiguous", UNKNOWN, ("straddles_speakers",), 4.0, 6.0),
        ("w4 w5", "assigned", "B", (), 6.5, 10.0),
    ]


def test_words_join_only_with_the_same_reasons():
    sa = _sa()
    split = _align(
        [_unit(0.0, 4.0, "p q", [_word(0.0, 2.0, "p"), _word(2.0, 4.0, "q")])],
        _avail(_seg("A", 0.0, 3.0), _seg("B", 0.0, 2.0), _seg("C", 3.0, 4.0)),
        10.0,
        settings=sa.AlignSettings(),
    )
    assert [(turn.text, _status(turn), _reasons(turn)) for turn in split.turns] == [
        ("p", "ambiguous", ("overlap",)),
        ("q", "ambiguous", ("straddles_speakers",)),
    ]
    joined = _align(
        [_unit(0.0, 4.0, "p q", [_word(0.0, 2.0, "p"), _word(2.0, 4.0, "q")])],
        _avail(_seg("A", 0.0, 1.0), _seg("B", 1.0, 2.0), _seg("A", 2.0, 3.0), _seg("B", 3.0, 4.0)),
        10.0,
    )
    assert [(turn.text, _status(turn), _reasons(turn)) for turn in joined.turns] == [
        ("p q", "ambiguous", ("straddles_speakers",)),
    ]
    assert (joined.turns[0].start_seconds, joined.turns[0].end_seconds) == (0.0, 4.0)


def test_word_turn_uses_word_times_not_unit_times():
    turn = _one(
        [_unit(0.0, 10.0, "a b", [_word(1.0, 2.0, "a"), _word(3.0, 4.0, "b")])],
        _avail(_seg("A", 0.0, 10.0)),
    )
    assert (turn.text, turn.start_seconds, turn.end_seconds) == ("a b", 1.0, 4.0)
    assert _status(turn) == "assigned" and turn.speaker_label == "A"


def test_turns_never_join_across_units_and_keep_input_positions():
    alignment = _align(
        [_unit(5.0, 6.0, "second"), _unit(1.0, 2.0, "first")],
        _avail(_seg("A", 0.0, 10.0)),
        10.0,
    )
    got = [(turn.text, turn.unit_index, turn.speaker_label) for turn in alignment.turns]
    assert got == [("first", 1, "A"), ("second", 0, "A")]


def test_ac7_time_base_maps_a_chunk_unit_to_original_time():
    sa = _sa()
    time_base = sa.TimeBase(offset_samples=9_600_000, sample_rate=16000)
    assert time_base.seconds_to_original(1.0) == Fraction(601)
    assert time_base.sample_to_original(16000) == Fraction(601)
    turn = _one(
        [_unit(1.0, 2.0)],
        _avail(_seg("A", 600.0, 605.0)),
        duration=1200.0,
        time_base=time_base,
    )
    assert (turn.start_seconds, turn.end_seconds) == (601.0, 602.0)
    assert (turn.start_ms, turn.end_ms) == (601000, 602000)
    assert _status(turn) == "assigned" and turn.speaker_label == "A"


def test_ac7_time_base_math_is_exact_and_defaults_to_zero_offset():
    sa = _sa()
    assert sa.TimeBase().offset_samples == 0
    assert sa.TimeBase().sample_rate == 16000
    assert sa.TimeBase(offset_samples=1, sample_rate=3).seconds_to_original(0) == Fraction(1, 3)
    assert sa.TimeBase(offset_samples=1, sample_rate=3).sample_to_original(1) == Fraction(2, 3)
    no_base = _align([_unit(1.0, 2.0)], _avail(_seg("A", 0.0, 5.0)), 10.0)
    zero_base = _align(
        [_unit(1.0, 2.0)], _avail(_seg("A", 0.0, 5.0)), 10.0, time_base=sa.TimeBase()
    )
    assert no_base == zero_base
    assert (no_base.turns[0].start_seconds, no_base.turns[0].end_seconds) == (1.0, 2.0)


def test_ac7_from_chunk_plan_and_from_span_agree():
    sa = _sa()
    plan = ChunkPlan(
        index=1,
        start_sample=9_600_000,
        end_sample=19_232_000,
        owned_start_sample=9_632_000,
        owned_end_sample=19_200_000,
        sample_rate=16000,
    )
    span = ASRChunkSpan(
        index=1,
        start_seconds=600.0,
        end_seconds=1202.0,
        owned_start_seconds=602.0,
        owned_end_seconds=1200.0,
    )
    expected = sa.TimeBase(offset_samples=9_600_000, sample_rate=16000)
    assert sa.TimeBase.from_chunk_plan(plan) == expected
    assert sa.TimeBase.from_span(span, 16000) == expected
    assert sa.TimeBase.from_span(span, 44100) == sa.TimeBase(
        offset_samples=26_460_000, sample_rate=44100
    )


def test_ac7_map_diarization_shifts_chunk_local_speakers():
    sa = _sa()
    time_base = sa.TimeBase(offset_samples=9_600_000, sample_rate=16000)
    local = DiarizationResult(
        provider="chunk-diarizer",
        status=DiarizationStatus.AVAILABLE,
        segments=[_seg("A", 0.5, 2.5), _seg("B", 2.5, 4.0)],
        is_mock=True,
    )
    mapped = sa.map_diarization(local, time_base)
    assert mapped.provider == "chunk-diarizer"
    assert mapped.status == DiarizationStatus.AVAILABLE
    assert mapped.is_mock is True
    assert mapped.issue is None
    assert sorted(
        (segment.speaker_label, segment.start_seconds, segment.end_seconds)
        for segment in mapped.segments
    ) == [("A", 600.5, 602.5), ("B", 602.5, 604.0)]
    shifted = _one([_unit(1.0, 2.0)], mapped, duration=1200.0, time_base=time_base)
    assert _status(shifted) == "assigned" and shifted.speaker_label == "A"
    unshifted = _one([_unit(1.0, 2.0)], local, duration=1200.0, time_base=time_base)
    assert _status(unshifted) == "unknown"
    assert _reasons(unshifted) == ("no_speaker",)


def test_ac7_map_diarization_keeps_an_unavailable_result():
    sa = _sa()
    source = _unavailable()
    mapped = sa.map_diarization(source, sa.TimeBase(offset_samples=16000, sample_rate=16000))
    assert mapped.status == DiarizationStatus.UNAVAILABLE
    assert mapped.issue == source.issue
    assert mapped.provider == source.provider
    assert mapped.is_mock is True


@pytest.mark.parametrize(
    ("seconds", "rate", "expected"),
    [
        pytest.param(Fraction(601), 44100, 26_504_100, id="601s-at-44100"),
        pytest.param(Fraction(601), 8000, 4_808_000, id="601s-at-8000"),
        pytest.param(Fraction(601), 16000, 9_616_000, id="601s-at-16000"),
        pytest.param(Fraction(1, 2), 1, 0, id="half-even-down"),
        pytest.param(Fraction(3, 2), 1, 2, id="half-even-up"),
        pytest.param(Fraction(1, 3), 44100, 14700, id="third-at-44100"),
    ],
)
def test_ac8_original_sample(seconds, rate, expected):
    assert _sa().original_sample(seconds, rate) == expected


@pytest.mark.parametrize(
    ("rate", "start_sample", "end_sample"),
    [
        pytest.param(44100, 26_504_100, 26_548_200, id="44100"),
        pytest.param(8000, 4_808_000, 4_816_000, id="8000"),
    ],
)
def test_ac8_turn_samples_in_the_original_rate(rate, start_sample, end_sample):
    sa = _sa()
    turn = _one(
        [_unit(1.0, 2.0)],
        _avail(_seg("A", 600.0, 605.0)),
        duration=1200.0,
        time_base=sa.TimeBase(offset_samples=9_600_000, sample_rate=16000),
        original_sample_rate=rate,
    )
    assert turn.start_seconds == 601.0
    assert (turn.start_sample, turn.end_sample) == (start_sample, end_sample)


@pytest.mark.parametrize(
    "diarization",
    [
        pytest.param(_avail(_seg("A", 0.0, 10.0)), id="with-speaker"),
        pytest.param(None, id="no-diarization"),
    ],
)
def test_ac9_a_unit_past_the_end_is_outside_recording(diarization):
    alignment = _align([_unit(2.0, 3.0, "inside"), _unit(11.0, 12.0, "after")], diarization, 10.0)
    after = next(turn for turn in alignment.turns if turn.text == "after")
    assert _status(after) == "unknown"
    assert after.speaker_label == UNKNOWN
    assert _reasons(after) == ("outside_recording",)
    assert (after.start_seconds, after.end_seconds) == (10.0, 10.0)
    assert (after.start_ms, after.end_ms) == (10000, 10000)
    inside = next(turn for turn in alignment.turns if turn.text == "inside")
    assert "outside_recording" not in _reasons(inside)


def test_ac9_a_chunk_unit_mapped_past_the_end_is_outside_recording():
    sa = _sa()
    turn = _one(
        [_unit(1.0, 2.0)],
        _avail(_seg("A", 0.0, 100.0)),
        duration=100.0,
        time_base=sa.TimeBase(offset_samples=9_600_000, sample_rate=16000),
    )
    assert _status(turn) == "unknown"
    assert _reasons(turn) == ("outside_recording",)
    assert (turn.start_seconds, turn.end_seconds) == (100.0, 100.0)


@pytest.mark.parametrize(
    ("end", "reasons"),
    [
        pytest.param(10.5, ("clamped_to_recording",), id="half-second-past-end-clamped"),
        pytest.param(10.04, (), id="within-tolerance-silent-clamp"),
        pytest.param(10.0, (), id="control-exactly-at-end"),
    ],
)
def test_ac9_a_unit_reaching_past_the_end_is_clamped_and_keeps_its_speaker(end, reasons):
    turn = _one([_unit(9.0, end)], _avail(_seg("A", 0.0, 10.0)), duration=10.0)
    assert _status(turn) == "assigned"
    assert turn.speaker_label == "A"
    assert _reasons(turn) == reasons
    assert (turn.start_seconds, turn.end_seconds) == (9.0, 10.0)
    assert turn.end_ms == 10000


def test_ac9_tolerance_setting_controls_the_clamp_note():
    sa = _sa()
    turn = _one(
        [_unit(9.0, 10.04)],
        _avail(_seg("A", 0.0, 10.0)),
        duration=10.0,
        settings=sa.AlignSettings(bounds_tolerance_seconds=0.0),
    )
    assert _reasons(turn) == ("clamped_to_recording",)
    assert _status(turn) == "assigned"


def test_ac9_clamp_note_is_kept_next_to_the_deciding_reason():
    turn = _one(
        [_unit(9.0, 11.0)],
        _avail(_seg("A", 0.0, 9.4), _seg("B", 9.4, 10.0)),
        duration=10.0,
    )
    assert _status(turn) == "ambiguous"
    assert _reasons(turn) == ("clamped_to_recording", "straddles_speakers")
    assert (turn.start_seconds, turn.end_seconds) == (9.0, 10.0)


def test_ac9_diarization_past_the_end_is_clamped_like_the_single_speaker_fixture():
    turn = _one([_unit(1.0, 2.0)], _avail(_seg("speaker_1", 0.0, 1e6)), duration=13)
    assert _status(turn) == "assigned"
    assert turn.speaker_label == "speaker_1"
    assert turn.confidence == pytest.approx(1.0)


def test_ac9_milliseconds_floor_the_start_and_ceil_the_end():
    alignment = _align(
        [_unit(0.0015, 1.0001, "a"), _unit(0.0004, 2.4996, "b")],
        _avail(_seg("A", 0.0, 2.5)),
        2.5,
    )
    got = {turn.text: (turn.start_ms, turn.end_ms) for turn in alignment.turns}
    assert got == {"a": (1, 1001), "b": (0, 2500)}


def test_ac9_end_ms_rounds_up_a_fractional_duration_after_a_silent_clamp():
    turn = _one([_unit(0.0, 10.03)], _avail(_seg("A", 0.0, 10.0004)), duration=10.0004)
    assert _reasons(turn) == ()
    assert turn.end_seconds == pytest.approx(10.0004)
    assert turn.end_ms == 10001


@pytest.mark.parametrize(
    ("segments", "status", "label", "reasons"),
    [
        pytest.param(
            ((UNKNOWN, 0.0, 10.0),), "unknown", UNKNOWN, ("unknown_speaker",), id="all-unknown"
        ),
        pytest.param(
            ((UNKNOWN, 0.0, 9.0), ("A", 9.0, 10.0)),
            "unknown",
            UNKNOWN,
            ("unknown_speaker",),
            id="unknown-dominates",
        ),
        pytest.param(
            ((UNKNOWN, 0.0, 1.0), ("A", 1.0, 10.0)),
            "assigned",
            "A",
            (),
            id="control-real-label-dominates",
        ),
    ],
)
def test_ac10_unknown_speaker_intervals(segments, status, label, reasons):
    turn = _one([_unit(0.0, 10.0)], _avail(*(_seg(*segment) for segment in segments)))
    assert _status(turn) == status
    assert turn.speaker_label == label
    assert _reasons(turn) == reasons
    assert UNKNOWN in _labels(turn)


def _two_chunk_reconciliation():
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
    return reconcile_chunk_speakers(
        [
            ChunkDiarization(span=span0, result=_avail(_seg("local_a", 0.0, 10.0))),
            ChunkDiarization(
                span=span1, result=_avail(_seg("local_x", 0.0, 2.0), _seg("local_y", 2.0, 10.0))
            ),
        ]
    )


def test_ac11_fixture_reconciliation_links_the_shared_speaker():
    result = _two_chunk_reconciliation().result
    assert result.status == DiarizationStatus.AVAILABLE
    assert [
        (segment.speaker_label, segment.start_seconds, segment.end_seconds)
        for segment in result.segments
    ] == [("speaker_1", 0.0, 8.0), ("speaker_1", 8.0, 10.0), ("speaker_2", 10.0, 18.0)]


def test_ac11_aligner_accepts_a_reconciliation_result_directly():
    reconciliation = _two_chunk_reconciliation()
    units = [
        _unit(1.0, 7.0, "first chunk"),
        _unit(7.0, 9.5, "across the cut"),
        _unit(9.0, 11.0, "speaker change"),
        _unit(11.0, 17.0, "second chunk"),
    ]
    alignment = _align(units, reconciliation.result, 18.0)
    got = {
        turn.text: (_status(turn), turn.speaker_label, _reasons(turn)) for turn in alignment.turns
    }
    assert got == {
        "first chunk": ("assigned", "speaker_1", ()),
        "across the cut": ("assigned", "speaker_1", ()),
        "speaker change": ("ambiguous", UNKNOWN, ("straddles_speakers",)),
        "second chunk": ("assigned", "speaker_2", ()),
    }


def test_ac12_reordered_diarization_segments_give_an_identical_alignment():
    segments = [
        _seg("A", 0.0, 4.0),
        _seg("B", 4.0, 6.0),
        _seg("A", 3.0, 5.0),
        _seg("C", 8.0, 10.0),
        _seg(UNKNOWN, 10.0, 12.0),
        _seg("B", 12.0, 20.0),
    ]
    order = [4, 2, 5, 0, 3, 1]
    units = [
        _unit(0.0, 3.0, "a"),
        _unit(3.0, 7.0, "b", [_word(3.0, 4.5, "b1"), _word(4.5, 7.0, "b2")]),
        _unit(8.0, 12.0, "c"),
        _unit(12.5, 19.0, "d"),
        _unit(21.0, 22.0, "e"),
    ]
    first = _align(units, _avail(*segments), 20.0, original_sample_rate=8000)
    again = _align(units, _avail(*segments), 20.0, original_sample_rate=8000)
    shuffled = _align(
        units, _avail(*(segments[index] for index in order)), 20.0, original_sample_rate=8000
    )
    assert first == again
    assert first == shuffled
    assert first.turns == shuffled.turns


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"min_coverage": True}, id="min_coverage-bool"),
        pytest.param({"min_coverage": float("nan")}, id="min_coverage-nan"),
        pytest.param({"min_coverage": float("inf")}, id="min_coverage-inf"),
        pytest.param({"min_coverage": 0.0}, id="min_coverage-zero"),
        pytest.param({"min_coverage": 1.01}, id="min_coverage-above-1"),
        pytest.param({"min_coverage": "0.5"}, id="min_coverage-string"),
        pytest.param({"dominant_share": 0.5}, id="dominant_share-half"),
        pytest.param({"dominant_share": 1.01}, id="dominant_share-above-1"),
        pytest.param({"dominant_share": True}, id="dominant_share-bool"),
        pytest.param({"dominant_share": float("nan")}, id="dominant_share-nan"),
        pytest.param({"max_overlap_share": 0.0}, id="max_overlap_share-zero"),
        pytest.param({"max_overlap_share": 1.5}, id="max_overlap_share-above-1"),
        pytest.param({"max_overlap_share": float("inf")}, id="max_overlap_share-inf"),
        pytest.param({"max_overlap_share": False}, id="max_overlap_share-bool"),
        pytest.param({"bounds_tolerance_seconds": -0.01}, id="tolerance-negative"),
        pytest.param({"bounds_tolerance_seconds": float("nan")}, id="tolerance-nan"),
        pytest.param({"bounds_tolerance_seconds": float("inf")}, id="tolerance-inf"),
        pytest.param({"bounds_tolerance_seconds": True}, id="tolerance-bool"),
    ],
)
def test_ac13_settings_reject_bad_values(kwargs):
    sa = _sa()
    with pytest.raises((ValueError, TypeError)):
        sa.AlignSettings(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="defaults"),
        pytest.param({"min_coverage": 1}, id="min_coverage-int-1"),
        pytest.param({"min_coverage": 0.01}, id="min_coverage-small"),
        pytest.param({"dominant_share": 1.0}, id="dominant_share-1"),
        pytest.param({"dominant_share": 0.51}, id="dominant_share-just-over-half"),
        pytest.param({"max_overlap_share": 1.0}, id="max_overlap_share-1"),
        pytest.param({"bounds_tolerance_seconds": 0}, id="tolerance-int-0"),
    ],
)
def test_ac13_settings_accept_edge_values(kwargs):
    sa = _sa()
    settings = sa.AlignSettings(**kwargs)
    assert "speaker-alignment/1" in settings.descriptor().values()


def test_ac13_settings_defaults():
    settings = _sa().AlignSettings()
    assert settings.min_coverage == 0.5
    assert settings.dominant_share == 0.8
    assert settings.max_overlap_share == 0.2
    assert settings.bounds_tolerance_seconds == 0.05


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        pytest.param(2.0, 1.0, "x", id="end-before-start"),
        pytest.param(-0.5, 1.0, "x", id="negative-start"),
        pytest.param(0.0, float("nan"), "x", id="nan-end"),
        pytest.param(0.0, float("inf"), "x", id="inf-end"),
        pytest.param(True, 1.0, "x", id="bool-start"),
        pytest.param(0.0, 1.0, "   ", id="blank-text"),
        pytest.param(0.0, 1.0, "", id="empty-text"),
    ],
)
def test_ac13_units_reject_bad_values(start, end, text):
    sa = _sa()
    with pytest.raises((ValueError, TypeError)):
        sa.AsrUnit(start_seconds=start, end_seconds=end, text=text)
    with pytest.raises((ValueError, TypeError)):
        sa.AsrWord(start_seconds=start, end_seconds=end, text=text)


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        pytest.param(1.0, 1.0, "x", id="zero-length"),
        pytest.param(0, 2, "x", id="int-seconds"),
        pytest.param(0.0, 1.0, " padded ", id="padded-text"),
    ],
)
def test_ac13_units_accept_close_values(start, end, text):
    sa = _sa()
    unit = sa.AsrUnit(start_seconds=start, end_seconds=end, text=text)
    assert unit.words == ()
    assert unit.confidence is None
    sa.AsrWord(start_seconds=start, end_seconds=end, text=text)


@pytest.mark.parametrize(
    "words",
    [
        pytest.param(((0.5, 1.5, "late"),), id="word-past-unit-end"),
        pytest.param(((0.99, 1.0, "early"),), id="word-before-unit-start"),
        pytest.param(((1.0, 1.5, "a"), (2.0, 2.0000011, "b")), id="word-past-end-beyond-tolerance"),
        pytest.param(((1.5, 1.8, "b"), (1.0, 1.4, "a")), id="unsorted-words"),
    ],
)
def test_ac13_units_reject_bad_words(words):
    sa = _sa()
    with pytest.raises((ValueError, TypeError)):
        sa.AsrUnit(
            start_seconds=1.0,
            end_seconds=2.0,
            text="t",
            words=tuple(sa.AsrWord(start_seconds=s, end_seconds=e, text=t) for s, e, t in words),
        )


@pytest.mark.parametrize(
    "words",
    [
        pytest.param(((1.0, 2.0, "all"),), id="word-equals-unit"),
        pytest.param(((1.0, 1.5, "a"), (1.5, 2.0000005, "b")), id="word-within-tolerance"),
        pytest.param(((1.0, 1.4, "a"), (1.0, 1.8, "b")), id="equal-starts-sorted"),
    ],
)
def test_ac13_units_accept_close_words(words):
    sa = _sa()
    unit = sa.AsrUnit(
        start_seconds=1.0,
        end_seconds=2.0,
        text="t",
        words=tuple(sa.AsrWord(start_seconds=s, end_seconds=e, text=t) for s, e, t in words),
    )
    assert len(unit.words) == len(words)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"offset_samples": -1}, id="negative-offset"),
        pytest.param({"offset_samples": True}, id="bool-offset"),
        pytest.param({"offset_samples": 1.5}, id="float-offset"),
        pytest.param({"sample_rate": 0}, id="zero-rate"),
        pytest.param({"sample_rate": True}, id="bool-rate"),
        pytest.param({"sample_rate": 16000.0}, id="float-rate"),
    ],
)
def test_time_base_rejects_bad_values(kwargs):
    sa = _sa()
    with pytest.raises((ValueError, TypeError)):
        sa.TimeBase(**kwargs)


def test_time_base_accepts_edge_values():
    sa = _sa()
    assert sa.TimeBase(offset_samples=0, sample_rate=1).seconds_to_original(2) == Fraction(2)


@pytest.mark.parametrize(
    ("duration", "ok"),
    [
        pytest.param(0.0, False, id="zero"),
        pytest.param(-1.0, False, id="negative"),
        pytest.param(float("nan"), False, id="nan"),
        pytest.param(float("inf"), False, id="inf"),
        pytest.param(0.001, True, id="control-tiny-positive"),
        pytest.param(13, True, id="control-int-like-claim"),
    ],
)
def test_recording_duration_must_be_positive_and_finite(duration, ok):
    sa = _sa()
    if ok:
        sa.align_speakers([], None, recording_duration_seconds=duration)
        return
    with pytest.raises((ValueError, TypeError)):
        sa.align_speakers([], None, recording_duration_seconds=duration)


def test_from_segment_copies_the_asr_segment():
    sa = _sa()
    unit = sa.AsrUnit.from_segment(
        ASRSegment(index=3, text="dorood", start_seconds=1.25, end_seconds=2.5, confidence=0.9)
    )
    assert (unit.start_seconds, unit.end_seconds, unit.text) == (1.25, 2.5, "dorood")
    assert unit.words == ()
    assert unit.confidence == pytest.approx(0.9)


def _asr(*segments: tuple[float, float, str]) -> ASRResult:
    return ASRResult(
        provider="fake-asr",
        model="fake",
        language="fa",
        text=" ".join(text for _start, _end, text in segments),
        segments=[
            ASRSegment(index=index, text=text, start_seconds=start, end_seconds=end)
            for index, (start, end, text) in enumerate(segments)
        ],
    )


@pytest.mark.parametrize(
    ("segment", "label"),
    [
        pytest.param((0.0, 10.0, "straddle"), UNKNOWN, id="straddling-segment-unknown"),
        pytest.param((1.0, 2.0, "contained"), "A", id="control-contained-segment-real-label"),
        pytest.param((7.0, 9.0, "inside-b"), "B", id="control-contained-in-b"),
    ],
)
def test_ac14_processor_transcript_turns(segment, label):
    from backend.jobs import processor

    turns = processor._transcript_turns(
        _asr(segment),
        _avail(_seg("A", 0.0, 6.0), _seg("B", 6.0, 10.0)),
        recording_duration_seconds=10,
    )
    assert len(turns) == 1
    turn = turns[0]
    assert turn.text == segment[2]
    assert turn.speaker_label == label
    assert (turn.start_seconds, turn.end_seconds) == (segment[0], segment[1])


def test_ac14_processor_keeps_the_single_speaker_fixture_result():
    from backend.jobs import processor

    single = _avail(_seg("speaker_1", 0.0, 1e6))
    turns = processor._transcript_turns(
        _asr((0.0, 0.0, "پیگیری قرارداد انجام شود")), single, recording_duration_seconds=13
    )
    assert [(turn.speaker_label, turn.start_seconds, turn.end_seconds) for turn in turns] == [
        (UNKNOWN, 0.0, 0.0)
    ]


def test_ac14_processor_with_unavailable_diarization_stays_unknown():
    from backend.jobs import processor

    turns = processor._transcript_turns(
        _asr((0.0, 4.0, "yek"), (4.0, 8.0, "do")), None, recording_duration_seconds=8
    )
    assert [(turn.text, turn.speaker_label) for turn in turns] == [
        ("yek", UNKNOWN),
        ("do", UNKNOWN),
    ]
