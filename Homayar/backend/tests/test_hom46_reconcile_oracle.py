"""HOM-46 acceptance oracle for ``backend.speech.speaker_reconcile``, written from the SPEC only.

Pure tests: fake diarization results and fake embeddings. No model, no audio, no database.
"""

from __future__ import annotations

import math

import pytest

from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRChunkSpan,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    ProviderIssue,
)
from backend.speech.speaker_reconcile import (
    RECONCILE_VERSION,
    AmbiguityReason,
    ChunkDiarization,
    ClusterEvidence,
    ClusterRef,
    CountDecision,
    CountSource,
    LinkEvidence,
    ReconcileSettings,
    SpeakerCountHint,
    reconcile_chunk_speakers,
)

R = ClusterRef
UNKNOWN = UNKNOWN_SPEAKER_LABEL


def _span(index: int, start: float, end: float, owned_start: float, owned_end: float):
    return ASRChunkSpan(
        index=index,
        start_seconds=start,
        end_seconds=end,
        owned_start_seconds=owned_start,
        owned_end_seconds=owned_end,
    )


ONE = _span(0, 0.0, 10.0, 0.0, 10.0)
TWO = (_span(0, 0.0, 12.0, 0.0, 10.0), _span(1, 8.0, 20.0, 10.0, 20.0))
THREE = (
    _span(0, 0.0, 12.0, 0.0, 10.0),
    _span(1, 8.0, 22.0, 10.0, 20.0),
    _span(2, 18.0, 30.0, 20.0, 30.0),
)
WIDE = (
    _span(0, 0.0, 26.0, 0.0, 10.0),
    _span(1, 8.0, 22.0, 10.0, 20.0),
    _span(2, 18.0, 30.0, 20.0, 30.0),
)


def _segment(label: str, start: float, end: float, confidence: float | None = None):
    return DiarizationSegment(
        speaker_label=label, start_seconds=start, end_seconds=end, confidence=confidence
    )


def _local(span: ASRChunkSpan, segments) -> list[DiarizationSegment]:
    return [
        _segment(label, start - span.start_seconds, end - span.start_seconds, *rest)
        for label, start, end, *rest in segments
    ]


def _up(span: ASRChunkSpan, *segments, provider: str = "fake-diarizer", mock: bool = False):
    return DiarizationResult(
        provider=provider,
        status=DiarizationStatus.AVAILABLE,
        segments=_local(span, segments),
        is_mock=mock,
    )


def _down(span: ASRChunkSpan, *segments, provider: str = "fake-diarizer", mock: bool = False):
    return DiarizationResult(
        provider=provider,
        status=DiarizationStatus.UNAVAILABLE,
        segments=_local(span, segments),
        issue=ProviderIssue(code="diarization_failed", message="fake failure"),
        is_mock=mock,
    )


def _chunk(span: ASRChunkSpan, result: DiarizationResult, clusters=None) -> ChunkDiarization:
    return ChunkDiarization(span=span, result=result, clusters=dict(clusters or {}))


def _ev(*values: float, model: str = "emb-1") -> ClusterEvidence:
    return ClusterEvidence(embedding=tuple(float(v) for v in values), embedding_model=model)


def _cos(value: float, axis: int = 1, model: str = "emb-1") -> ClusterEvidence:
    vector = [value, 0.0, 0.0]
    vector[axis] = math.sqrt(1.0 - value * value)
    return _ev(*vector, model=model)


def _angle(degrees: float) -> ClusterEvidence:
    radians = math.radians(degrees)
    return _ev(math.cos(radians), math.sin(radians))


REF = _ev(1.0, 0.0, 0.0)


def _run(*chunks: ChunkDiarization, **kwargs):
    return reconcile_chunk_speakers(list(chunks), **kwargs)


def _hint(count: int, source: CountSource = CountSource.OPERATOR) -> SpeakerCountHint:
    return SpeakerCountHint(count=count, source=source)


def _labels(rec) -> dict[ClusterRef, str]:
    return dict(rec.labels)


def _segs(rec) -> list[tuple[float, float, str, float | None]]:
    return [
        (s.start_seconds, s.end_seconds, s.speaker_label, s.confidence) for s in rec.result.segments
    ]


def _amb(rec) -> set[tuple[ClusterRef, AmbiguityReason]]:
    return {(m.cluster, m.reason) for m in rec.ambiguous}


def _only_link(rec):
    assert len(rec.links) == 1
    return rec.links[0]


def _assert_link(link, left, right, evidence, score):
    assert link.left == left
    assert link.right == right
    assert link.evidence == evidence
    assert link.score == pytest.approx(score, abs=1e-9)


def _assert_sorted(rec):
    keys = [(link.left, link.right) for link in rec.links]
    assert keys == sorted(keys)
    assert all(link.left < link.right for link in rec.links)
    amb_keys = [(m.cluster, m.reason.value) for m in rec.ambiguous]
    assert amb_keys == sorted(amb_keys)
    assert all(m.candidates == tuple(sorted(m.candidates)) for m in rec.ambiguous)
    times = [(s.start_seconds, s.end_seconds, s.speaker_label) for s in rec.result.segments]
    assert times == sorted(times)


def test_public_enum_values_and_version():
    assert RECONCILE_VERSION == "speaker-reconcile/1"
    assert {s.value for s in CountSource} == {"channel_layout", "operator", "provider_estimate"}
    assert {e.value for e in LinkEvidence} == {
        "overlap",
        "embedding",
        "overlap_and_embedding",
        "count_hint",
    }
    assert {r.value for r in AmbiguityReason} == {
        "no_margin",
        "not_mutual",
        "conflicting_evidence",
        "cannot_link",
    }
    assert {d.value for d in CountDecision} == {
        "no_hint",
        "untrusted_source",
        "already_met",
        "applied",
        "not_supported",
        "fewer_than_hint",
    }


def test_descriptor_holds_version_and_every_field_as_float():
    assert ReconcileSettings().descriptor() == {
        "version": RECONCILE_VERSION,
        "min_overlap_seconds": 0.5,
        "overlap_match": 0.6,
        "embedding_match": 0.75,
        "embedding_reject": 0.40,
        "margin": 0.10,
        "count_support": 0.60,
    }
    descriptor = ReconcileSettings(
        min_overlap_seconds=1,
        overlap_match=1,
        embedding_match=1,
        embedding_reject=0,
        margin=0,
        count_support=1,
    ).descriptor()
    assert descriptor["version"] == RECONCILE_VERSION
    fields = {k: v for k, v in descriptor.items() if k != "version"}
    assert len(fields) == 6
    assert all(type(v) is float for v in fields.values())
    assert fields["min_overlap_seconds"] == 1.0


def test_empty_chunk_list_is_rejected():
    with pytest.raises(ValueError):
        reconcile_chunk_speakers([])


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: [_chunk(_span(1, 0.0, 10.0, 0.0, 10.0), _up(ONE))], id="starts_at_1"),
        pytest.param(
            lambda: [_chunk(TWO[1], _up(TWO[1])), _chunk(TWO[0], _up(TWO[0]))], id="swapped"
        ),
        pytest.param(
            lambda: [_chunk(TWO[0], _up(TWO[0])), _chunk(TWO[0], _up(TWO[0]))], id="repeated"
        ),
    ],
)
def test_span_index_must_match_position(build):
    with pytest.raises(ValueError):
        reconcile_chunk_speakers(build())


def test_span_index_in_position_is_accepted():
    rec = _run(_chunk(TWO[0], _up(TWO[0], ("a", 1.0, 2.0))), _chunk(TWO[1], _up(TWO[1])))
    assert _labels(rec) == {R(0, "a"): "speaker_1"}


@pytest.mark.parametrize(
    "owned_start",
    [
        pytest.param(10.5, id="gap"),
        pytest.param(9.5, id="overlap"),
        pytest.param(10.00001, id="tiny_gap"),
    ],
)
def test_owned_ranges_must_be_contiguous(owned_start):
    second = _span(1, 8.0, 20.0, owned_start, 20.0)
    with pytest.raises(ValueError):
        _run(_chunk(TWO[0], _up(TWO[0])), _chunk(second, _up(second)))


@pytest.mark.parametrize(
    "owned_start",
    [
        pytest.param(10.0, id="exact"),
        pytest.param(10.0000005, id="within_tolerance_after"),
        pytest.param(9.9999995, id="within_tolerance_before"),
    ],
)
def test_owned_ranges_within_tolerance_are_accepted(owned_start):
    second = _span(1, 8.0, 20.0, owned_start, 20.0)
    rec = _run(_chunk(TWO[0], _up(TWO[0], ("a", 1.0, 2.0))), _chunk(second, _up(second)))
    assert _labels(rec) == {R(0, "a"): "speaker_1"}


BAD_SETTINGS = [
    pytest.param({"min_overlap_seconds": math.nan}, id="min_overlap_nan"),
    pytest.param({"min_overlap_seconds": math.inf}, id="min_overlap_inf"),
    pytest.param({"min_overlap_seconds": -0.1}, id="min_overlap_negative"),
    pytest.param({"min_overlap_seconds": "0.5"}, id="min_overlap_string"),
    pytest.param({"overlap_match": 0.0}, id="overlap_match_zero"),
    pytest.param({"overlap_match": 1.01}, id="overlap_match_above_one"),
    pytest.param({"overlap_match": math.nan}, id="overlap_match_nan"),
    pytest.param({"overlap_match": True}, id="overlap_match_bool"),
    pytest.param({"embedding_match": 1.01}, id="embedding_match_above_one"),
    pytest.param({"embedding_match": math.nan}, id="embedding_match_nan"),
    pytest.param({"embedding_match": 0.4, "embedding_reject": 0.4}, id="reject_equals_match"),
    pytest.param({"embedding_match": 0.5, "embedding_reject": 0.6}, id="reject_above_match"),
    pytest.param({"embedding_reject": -0.01}, id="reject_negative"),
    pytest.param({"embedding_reject": -math.inf}, id="reject_minus_inf"),
    pytest.param({"margin": 1.0}, id="margin_one"),
    pytest.param({"margin": -0.01}, id="margin_negative"),
    pytest.param({"margin": math.nan}, id="margin_nan"),
    pytest.param({"margin": False}, id="margin_bool"),
    pytest.param({"count_support": 0.0}, id="count_support_zero"),
    pytest.param({"count_support": 1.01}, id="count_support_above_one"),
    pytest.param({"count_support": True}, id="count_support_bool"),
    pytest.param({"count_support": math.inf}, id="count_support_inf"),
]


@pytest.mark.parametrize("kwargs", BAD_SETTINGS)
def test_bad_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        _run(_chunk(ONE, _up(ONE, ("a", 1.0, 2.0))), settings=ReconcileSettings(**kwargs))


EDGE_SETTINGS = [
    pytest.param({"embedding_reject": 0.0, "embedding_match": 1.0}, id="reject_zero_match_one"),
    pytest.param({"overlap_match": 1.0}, id="overlap_match_one"),
    pytest.param({"margin": 0.0}, id="margin_zero"),
    pytest.param({"count_support": 1.0}, id="count_support_one"),
    pytest.param({"min_overlap_seconds": 0.0}, id="min_overlap_zero"),
    pytest.param(
        {"min_overlap_seconds": 1, "overlap_match": 1, "margin": 0, "count_support": 1},
        id="int_values",
    ),
]


@pytest.mark.parametrize("kwargs", EDGE_SETTINGS)
def test_edge_settings_are_accepted(kwargs):
    rec = _run(_chunk(ONE, _up(ONE, ("a", 1.0, 2.0))), settings=ReconcileSettings(**kwargs))
    assert _labels(rec) == {R(0, "a"): "speaker_1"}


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(True, id="bool_true"),
    ],
)
def test_bad_count_hint_is_rejected(count):
    with pytest.raises(ValueError):
        _run(_chunk(ONE, _up(ONE, ("a", 1.0, 2.0))), count_hint=_hint(count))


def test_count_hint_of_one_is_accepted():
    rec = _run(_chunk(ONE, _up(ONE, ("a", 1.0, 2.0))), count_hint=_hint(1))
    assert rec.count_decision == CountDecision.ALREADY_MET


def test_single_chunk_renames_labels_without_links():
    rec = _run(_chunk(ONE, _up(ONE, ("spk_b", 0.0, 3.0), ("spk_a", 3.0, 6.0))))
    assert _labels(rec) == {R(0, "spk_b"): "speaker_1", R(0, "spk_a"): "speaker_2"}
    assert rec.links == ()
    assert rec.ambiguous == ()
    assert rec.count_decision == CountDecision.NO_HINT
    assert rec.version == RECONCILE_VERSION
    assert rec.result.status == DiarizationStatus.AVAILABLE
    assert _segs(rec) == [(0.0, 3.0, "speaker_1", None), (3.0, 6.0, "speaker_2", None)]


def test_segment_time_moves_to_original_time_and_clips_to_owned_range():
    first = DiarizationResult(
        provider="fake-diarizer",
        status=DiarizationStatus.AVAILABLE,
        segments=[_segment("x", 9.0, 11.5, 0.8), _segment("x", 1.0, 2.0, 0.9)],
    )
    second = DiarizationResult(
        provider="fake-diarizer",
        status=DiarizationStatus.AVAILABLE,
        segments=[_segment("y", 5.0, 6.0, 0.7), _segment("y", 0.0, 1.5)],
    )
    rec = _run(_chunk(TWO[0], first), _chunk(TWO[1], second))
    assert rec.links == ()
    assert _segs(rec) == [
        (1.0, 2.0, "speaker_1", 0.9),
        (9.0, 10.0, "speaker_1", 0.8),
        (13.0, 14.0, "speaker_2", 0.7),
    ]
    assert _labels(rec) == {R(0, "x"): "speaker_1", R(1, "y"): "speaker_2"}


def test_overlapping_speech_in_one_chunk_is_kept_and_sorted():
    rec = _run(_chunk(ONE, _up(ONE, ("b", 2.0, 4.0), ("c", 1.0, 3.0), ("a", 1.0, 3.0))))
    assert _labels(rec) == {R(0, "a"): "speaker_1", R(0, "c"): "speaker_2", R(0, "b"): "speaker_3"}
    assert _segs(rec) == [
        (1.0, 3.0, "speaker_1", None),
        (1.0, 3.0, "speaker_2", None),
        (2.0, 4.0, "speaker_3", None),
    ]


def test_unknown_label_is_kept_never_matched_never_counted():
    rec = _run(
        _chunk(
            TWO[0],
            _up(TWO[0], (UNKNOWN, 8.0, 11.0), ("a", 1.0, 2.0)),
            {UNKNOWN: REF},
        ),
        _chunk(
            TWO[1],
            _up(TWO[1], (UNKNOWN, 8.0, 11.0), ("b", 13.0, 14.0)),
            {UNKNOWN: REF},
        ),
        count_hint=_hint(2),
    )
    assert rec.links == ()
    assert all(m.cluster.label != UNKNOWN for m in rec.ambiguous)
    assert _labels(rec) == {R(0, "a"): "speaker_1", R(1, "b"): "speaker_2"}
    assert UNKNOWN not in _labels(rec).values()
    assert rec.count_decision == CountDecision.ALREADY_MET
    assert _segs(rec) == [
        (1.0, 2.0, "speaker_1", None),
        (8.0, 10.0, UNKNOWN, None),
        (10.0, 11.0, UNKNOWN, None),
        (13.0, 14.0, "speaker_2", None),
    ]


def test_unknown_label_does_not_count_against_a_hint_of_one():
    rec = _run(_chunk(ONE, _up(ONE, (UNKNOWN, 0.0, 1.0), ("a", 1.0, 2.0))), count_hint=_hint(1))
    assert rec.count_decision == CountDecision.ALREADY_MET
    assert _labels(rec) == {R(0, "a"): "speaker_1"}


def _three_with_middle(middle: ChunkDiarization):
    return (
        _chunk(THREE[0], _up(THREE[0], ("a", 1.0, 2.0))),
        middle,
        _chunk(THREE[2], _up(THREE[2], ("c", 24.0, 26.0))),
    )


def test_unavailable_chunk_gives_no_segment_or_cluster_for_its_time():
    middle = _chunk(THREE[1], _down(THREE[1], (UNKNOWN, 13.0, 15.0)), {"ghost": REF})
    rec = _run(*_three_with_middle(middle))
    assert _segs(rec) == [(1.0, 2.0, "speaker_1", None), (24.0, 26.0, "speaker_2", None)]
    assert not any(s.start_seconds < 20.0 and s.end_seconds > 10.0 for s in rec.result.segments)
    assert set(_labels(rec)) == {R(0, "a"), R(2, "c")}
    assert all(1 not in (link.left.chunk_index, link.right.chunk_index) for link in rec.links)
    assert rec.result.status == DiarizationStatus.AVAILABLE
    assert rec.result.issue is not None
    assert rec.result.issue.code == "diarization_partial"
    assert rec.result.issue.retryable is False


def test_available_chunk_segments_appear_clipped_to_owned_range():
    middle = _chunk(THREE[1], _up(THREE[1], ("m", 13.0, 15.0), ("m", 19.0, 21.5)))
    rec = _run(*_three_with_middle(middle))
    assert _segs(rec) == [
        (1.0, 2.0, "speaker_1", None),
        (13.0, 15.0, "speaker_2", None),
        (19.0, 20.0, "speaker_2", None),
        (24.0, 26.0, "speaker_3", None),
    ]
    assert rec.result.status == DiarizationStatus.AVAILABLE
    assert rec.result.issue is None or rec.result.issue.code != "diarization_partial"


def test_every_chunk_unavailable():
    rec = _run(
        _chunk(TWO[0], _down(TWO[0], (UNKNOWN, 1.0, 2.0), provider="p0")),
        _chunk(TWO[1], _down(TWO[1], provider="p1")),
    )
    assert rec.result.status == DiarizationStatus.UNAVAILABLE
    assert rec.result.segments == []
    assert rec.result.issue is not None
    assert rec.result.issue.code == "diarization_unavailable"
    assert rec.result.provider == "p0"
    assert _labels(rec) == {}
    assert rec.links == ()


@pytest.mark.parametrize("middle_up", [False, True], ids=["middle_unavailable", "middle_silent"])
def test_overlap_evidence_is_never_read_across_a_chunk(middle_up):
    middle = _chunk(WIDE[1], _up(WIDE[1]) if middle_up else _down(WIDE[1]))
    rec = _run(
        _chunk(WIDE[0], _up(WIDE[0], ("a", 24.0, 26.0))),
        middle,
        _chunk(WIDE[2], _up(WIDE[2], ("c", 24.0, 26.0))),
    )
    assert rec.links == ()
    assert _labels(rec)[R(0, "a")] != _labels(rec)[R(2, "c")]


def test_overlap_evidence_between_adjacent_chunks_links():
    spans = (_span(0, 0.0, 26.0, 0.0, 10.0), _span(1, 18.0, 30.0, 10.0, 30.0))
    rec = _run(
        _chunk(spans[0], _up(spans[0], ("a", 24.0, 26.0))),
        _chunk(spans[1], _up(spans[1], ("c", 24.0, 26.0))),
    )
    _assert_link(_only_link(rec), R(0, "a"), R(1, "c"), LinkEvidence.OVERLAP, 1.0)
    assert _labels(rec)[R(0, "a")] == _labels(rec)[R(1, "c")]


@pytest.mark.parametrize(
    ("end", "minimum", "linked"),
    [
        pytest.param(8.4, None, False, id="under_minimum"),
        pytest.param(8.5, None, True, id="at_minimum"),
        pytest.param(8.6, None, True, id="above_minimum"),
        pytest.param(8.4, 0.3, True, id="lowered_minimum"),
    ],
)
def test_co_speech_minimum(end, minimum, linked):
    settings = None if minimum is None else ReconcileSettings(min_overlap_seconds=minimum)
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 8.0, end))),
        _chunk(TWO[1], _up(TWO[1], ("b", 8.0, end))),
        settings=settings,
    )
    if linked:
        _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.OVERLAP, 1.0)
        assert _labels(rec)[R(0, "a")] == _labels(rec)[R(1, "b")]
    else:
        assert rec.links == ()
        assert _labels(rec)[R(0, "a")] != _labels(rec)[R(1, "b")]


def test_agreement_uses_shared_region_and_unclipped_time():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 6.0, 11.0))),
        _chunk(TWO[1], _up(TWO[1], ("b", 9.0, 15.0))),
    )
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.OVERLAP, 2.0 / 3.0)


def test_self_overlap_of_one_cluster_is_merged_before_counting():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 8.0, 10.0), ("a", 9.0, 11.0))),
        _chunk(TWO[1], _up(TWO[1], ("b", 8.0, 9.5))),
    )
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.OVERLAP, 1.0)


@pytest.mark.parametrize(
    ("overlap_match", "linked"),
    [
        pytest.param(None, False, id="default_match"),
        pytest.param(0.5, True, id="match_at_agreement"),
    ],
)
def test_overlap_agreement_must_reach_overlap_match(overlap_match, linked):
    settings = None if overlap_match is None else ReconcileSettings(overlap_match=overlap_match)
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 8.0, 10.0))),
        _chunk(TWO[1], _up(TWO[1], ("b", 9.0, 11.0))),
        settings=settings,
    )
    if linked:
        _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.OVERLAP, 0.5)
    else:
        assert rec.links == ()
        assert rec.ambiguous == ()


def _far_pair(left: ClusterEvidence | None, right: ClusterEvidence | None):
    return _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 1.0, 2.0)), {} if left is None else {"a": left}),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0)), {} if right is None else {"b": right}),
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(REF, _ev(1.0, 0.0, 0.0, model="emb-2"), id="other_model"),
        pytest.param(REF, _ev(1.0, 0.0), id="length_mismatch"),
        pytest.param(_ev(), _ev(), id="empty"),
        pytest.param(_ev(0.0, 0.0, 0.0), REF, id="zero_norm"),
        pytest.param(_ev(1.0, math.nan, 0.0), REF, id="nan"),
        pytest.param(REF, _ev(1.0, math.inf, 0.0), id="inf"),
        pytest.param(None, REF, id="missing_left"),
        pytest.param(REF, None, id="missing_right"),
    ],
)
def test_no_embedding_evidence_gives_no_link(left, right):
    rec = _far_pair(left, right)
    assert rec.links == ()
    assert rec.ambiguous == ()
    assert _labels(rec)[R(0, "a")] != _labels(rec)[R(1, "b")]


def test_same_model_embeddings_in_different_chunks_link():
    rec = _far_pair(REF, _ev(1.0, 0.0, 0.0))
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.EMBEDDING, 1.0)
    assert _labels(rec)[R(0, "a")] == _labels(rec)[R(1, "b")]


def test_embedding_score_is_the_cosine():
    rec = _far_pair(REF, _cos(0.8))
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.EMBEDDING, 0.8)


def test_embedding_under_match_gives_no_link():
    rec = _far_pair(REF, _cos(0.7))
    assert rec.links == ()
    assert rec.ambiguous == ()


def test_identical_embeddings_in_one_chunk_stay_two_speakers():
    rec = _run(_chunk(ONE, _up(ONE, ("a1", 1.0, 2.0), ("a2", 3.0, 4.0)), {"a1": REF, "a2": REF}))
    assert rec.links == ()
    assert _labels(rec) == {R(0, "a1"): "speaker_1", R(0, "a2"): "speaker_2"}


def _co_speaking(right: ClusterEvidence | None, **kwargs):
    return _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 8.0, 11.0)), {"a": REF} if right is not None else {}),
        _chunk(TWO[1], _up(TWO[1], ("b", 8.0, 11.0)), {} if right is None else {"b": right}),
        **kwargs,
    )


def test_overlap_with_rejecting_embedding_is_conflicting_and_not_linked():
    rec = _co_speaking(_cos(0.3))
    assert rec.links == ()
    assert _amb(rec) == {
        (R(0, "a"), AmbiguityReason.CONFLICTING_EVIDENCE),
        (R(1, "b"), AmbiguityReason.CONFLICTING_EVIDENCE),
    }
    by_cluster = {m.cluster: m.candidates for m in rec.ambiguous}
    assert R(1, "b") in by_cluster[R(0, "a")]
    assert R(0, "a") in by_cluster[R(1, "b")]
    assert _labels(rec)[R(0, "a")] != _labels(rec)[R(1, "b")]


@pytest.mark.parametrize(
    ("right", "evidence", "score"),
    [
        pytest.param(None, LinkEvidence.OVERLAP, 1.0, id="no_embeddings"),
        pytest.param(_cos(0.5), LinkEvidence.OVERLAP, 1.0, id="cosine_between_thresholds"),
        pytest.param(_cos(0.8), LinkEvidence.BOTH, 0.8, id="cosine_matches"),
    ],
)
def test_overlap_link_with_or_without_embeddings(right, evidence, score):
    rec = _co_speaking(right)
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), evidence, score)
    assert rec.ambiguous == ()


def test_both_evidence_score_is_the_smaller_one():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 6.0, 11.0)), {"a": REF}),
        _chunk(TWO[1], _up(TWO[1], ("b", 9.0, 15.0)), {"b": _cos(0.9)}),
    )
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.BOTH, 2.0 / 3.0)


def test_weak_overlap_with_matching_embedding_links_by_embedding():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 8.0, 10.0)), {"a": REF}),
        _chunk(TWO[1], _up(TWO[1], ("b", 9.0, 11.0)), {"b": _cos(0.8)}),
    )
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.EMBEDDING, 0.8)


def _margin_case(runner_up: float | None, settings: ReconcileSettings | None = None):
    clusters = {"b1": _cos(0.9, 1)}
    segments = [("b1", 13.0, 14.0)]
    if runner_up is not None:
        clusters["b2"] = _cos(runner_up, 2)
        segments.append(("b2", 15.0, 16.0))
    return _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 1.0, 2.0)), {"a": REF}),
        _chunk(TWO[1], _up(TWO[1], *segments), clusters),
        settings=settings,
    )


def test_runner_up_within_margin_blocks_the_link():
    rec = _margin_case(0.85)
    assert rec.links == ()
    assert _amb(rec) == {
        (R(0, "a"), AmbiguityReason.NO_MARGIN),
        (R(1, "b1"), AmbiguityReason.NO_MARGIN),
        (R(1, "b2"), AmbiguityReason.NOT_MUTUAL),
    }
    assert len(set(_labels(rec).values())) == 3
    _assert_sorted(rec)


def test_same_pair_links_when_the_runner_up_is_removed():
    rec = _margin_case(None)
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b1"), LinkEvidence.EMBEDDING, 0.9)
    assert rec.ambiguous == ()


def test_runner_up_clearly_lower_still_links():
    rec = _margin_case(0.78)
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b1"), LinkEvidence.EMBEDDING, 0.9)
    assert _amb(rec) == {(R(1, "b2"), AmbiguityReason.NOT_MUTUAL)}


def test_smaller_margin_setting_lets_the_close_pair_link():
    rec = _margin_case(0.85, ReconcileSettings(margin=0.04))
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b1"), LinkEvidence.EMBEDDING, 0.9)


def test_best_that_is_not_mutual_is_ambiguous():
    rec = _run(
        _chunk(
            TWO[0],
            _up(TWO[0], ("a1", 1.0, 2.0), ("a2", 3.0, 4.0)),
            {"a1": _cos(0.8, 1), "a2": _cos(0.95, 2)},
        ),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0)), {"b": REF}),
    )
    _assert_link(_only_link(rec), R(0, "a2"), R(1, "b"), LinkEvidence.EMBEDDING, 0.95)
    assert _amb(rec) == {(R(0, "a1"), AmbiguityReason.NOT_MUTUAL)}
    assert _labels(rec)[R(0, "a1")] != _labels(rec)[R(1, "b")]


def _triangle(reverse: bool = False):
    def order(items):
        return list(reversed(items)) if reverse else list(items)

    return (
        _chunk(
            THREE[0],
            _up(THREE[0], *order([("a1", 1.0, 2.0), ("a2", 3.0, 4.0), ("a1", 5.0, 6.0)])),
            dict(order([("a1", _angle(0.0)), ("a2", _angle(90.0))])),
        ),
        _chunk(
            THREE[1],
            _up(THREE[1], *order([("b", 13.0, 15.0), ("b", 16.0, 17.0)])),
            dict(order([("b", _angle(35.0))])),
        ),
        _chunk(
            THREE[2],
            _up(THREE[2], *order([("c", 24.0, 26.0)])),
            dict(order([("c", _angle(60.0))])),
        ),
    )


def test_link_that_joins_two_clusters_of_one_chunk_is_skipped():
    rec = _run(*_triangle())
    assert [(link.left, link.right, link.evidence) for link in rec.links] == [
        (R(0, "a2"), R(2, "c"), LinkEvidence.EMBEDDING),
        (R(1, "b"), R(2, "c"), LinkEvidence.EMBEDDING),
    ]
    assert rec.links[0].score == pytest.approx(math.cos(math.radians(30.0)))
    assert rec.links[1].score == pytest.approx(math.cos(math.radians(25.0)))
    assert _amb(rec) == {
        (R(0, "a1"), AmbiguityReason.CANNOT_LINK),
        (R(1, "b"), AmbiguityReason.CANNOT_LINK),
    }
    by_cluster = {m.cluster: m.candidates for m in rec.ambiguous}
    assert R(1, "b") in by_cluster[R(0, "a1")]
    assert R(0, "a1") in by_cluster[R(1, "b")]
    labels = _labels(rec)
    assert labels[R(0, "a1")] == "speaker_1"
    assert labels[R(0, "a2")] == labels[R(1, "b")] == labels[R(2, "c")] == "speaker_2"
    _assert_sorted(rec)


def _snapshot(rec):
    return (
        rec.result.model_dump(),
        _labels(rec),
        rec.links,
        rec.ambiguous,
        rec.count_decision,
        rec.version,
    )


def test_same_input_gives_same_output():
    assert _snapshot(_run(*_triangle())) == _snapshot(_run(*_triangle()))


def test_order_of_segments_and_clusters_does_not_change_output():
    assert _snapshot(_run(*_triangle(reverse=True))) == _snapshot(_run(*_triangle()))


def _hint_case(**kwargs):
    return _run(
        _chunk(
            TWO[0],
            _up(TWO[0], ("a", 1.0, 2.0), ("x", 3.0, 4.0)),
            {"a": REF, "x": _ev(0.0, 0.0, 1.0)},
        ),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0)), {"b": _cos(0.65, 1)}),
        **kwargs,
    )


def test_no_hint_leaves_groups_as_they_are():
    rec = _hint_case()
    assert rec.count_decision == CountDecision.NO_HINT
    assert rec.links == ()
    assert len(set(_labels(rec).values())) == 3


@pytest.mark.parametrize(
    ("source", "count", "decision", "merged"),
    [
        pytest.param(
            CountSource.PROVIDER_ESTIMATE,
            2,
            CountDecision.UNTRUSTED_SOURCE,
            False,
            id="estimate_fewer",
        ),
        pytest.param(
            CountSource.PROVIDER_ESTIMATE,
            3,
            CountDecision.UNTRUSTED_SOURCE,
            False,
            id="estimate_equal",
        ),
        pytest.param(CountSource.OPERATOR, 2, CountDecision.APPLIED, True, id="operator_merge"),
        pytest.param(
            CountSource.CHANNEL_LAYOUT, 2, CountDecision.APPLIED, True, id="channel_layout_merge"
        ),
        pytest.param(
            CountSource.OPERATOR, 3, CountDecision.ALREADY_MET, False, id="operator_equal"
        ),
        pytest.param(
            CountSource.OPERATOR, 4, CountDecision.FEWER_THAN_HINT, False, id="operator_more"
        ),
        pytest.param(
            CountSource.CHANNEL_LAYOUT,
            5,
            CountDecision.FEWER_THAN_HINT,
            False,
            id="channel_layout_more",
        ),
        pytest.param(
            CountSource.OPERATOR, 1, CountDecision.NOT_SUPPORTED, False, id="operator_unreachable"
        ),
    ],
)
def test_count_hint_decisions(source, count, decision, merged):
    baseline = _hint_case()
    rec = _hint_case(count_hint=_hint(count, source))
    assert rec.count_decision == decision
    labels = _labels(rec)
    if merged:
        _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.COUNT_HINT, 0.65)
        assert labels[R(0, "a")] == labels[R(1, "b")] != labels[R(0, "x")]
        assert len(set(labels.values())) == 2
        assert {s.speaker_label for s in rec.result.segments} == set(labels.values())
    else:
        assert rec.links == ()
        assert labels == _labels(baseline)
        assert _segs(rec) == _segs(baseline)


def test_count_hint_without_a_supporting_pair_is_not_supported():
    rec = _hint_case(count_hint=_hint(2), settings=ReconcileSettings(count_support=0.7))
    assert rec.count_decision == CountDecision.NOT_SUPPORTED
    assert rec.links == ()
    assert len(set(_labels(rec).values())) == 3


def _weak_overlap(right: ClusterEvidence | None, **kwargs):
    return _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 8.0, 10.0)), {"a": REF} if right is not None else {}),
        _chunk(TWO[1], _up(TWO[1], ("b", 9.0, 11.0)), {} if right is None else {"b": right}),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("right", "support", "score"),
    [
        pytest.param(None, 0.45, 0.5, id="overlap_only"),
        pytest.param(_cos(0.45), 0.48, 0.5, id="overlap_larger"),
        pytest.param(_cos(0.62), 0.6, 0.62, id="cosine_larger"),
    ],
)
def test_count_hint_support_is_the_larger_score(right, support, score):
    rec = _weak_overlap(
        right, count_hint=_hint(1), settings=ReconcileSettings(count_support=support)
    )
    assert rec.count_decision == CountDecision.APPLIED
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.COUNT_HINT, score)
    assert _labels(rec)[R(0, "a")] == _labels(rec)[R(1, "b")]


def test_count_hint_overlap_under_support_is_not_supported():
    rec = _weak_overlap(None, count_hint=_hint(1))
    assert rec.count_decision == CountDecision.NOT_SUPPORTED
    assert rec.links == ()


def test_count_hint_never_merges_a_conflicting_pair():
    rec = _co_speaking(_cos(0.3), count_hint=_hint(1))
    assert rec.count_decision == CountDecision.NOT_SUPPORTED
    assert rec.links == ()
    assert (R(0, "a"), AmbiguityReason.CONFLICTING_EVIDENCE) in _amb(rec)
    assert _labels(rec)[R(0, "a")] != _labels(rec)[R(1, "b")]


def test_count_hint_counts_groups_after_regular_links():
    rec = _co_speaking(None, count_hint=_hint(1))
    assert rec.count_decision == CountDecision.ALREADY_MET
    _assert_link(_only_link(rec), R(0, "a"), R(1, "b"), LinkEvidence.OVERLAP, 1.0)


def _cannot_link_hint(count: int):
    return _run(
        _chunk(
            TWO[0],
            _up(TWO[0], ("a1", 1.0, 2.0), ("a2", 3.0, 4.0)),
            {"a1": _cos(0.7, 1), "a2": _cos(0.65, 2)},
        ),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0)), {"b": REF}),
        count_hint=_hint(count),
    )


def test_count_hint_merge_respects_cannot_link_allow():
    rec = _cannot_link_hint(2)
    assert rec.count_decision == CountDecision.APPLIED
    _assert_link(_only_link(rec), R(0, "a1"), R(1, "b"), LinkEvidence.COUNT_HINT, 0.7)
    assert _labels(rec)[R(0, "a1")] != _labels(rec)[R(0, "a2")]


def test_count_hint_merge_respects_cannot_link_deny():
    rec = _cannot_link_hint(1)
    assert rec.count_decision == CountDecision.NOT_SUPPORTED
    assert rec.links == ()
    assert len(set(_labels(rec).values())) == 3


def test_count_hint_never_merges_clusters_of_one_chunk():
    rec = _run(
        _chunk(ONE, _up(ONE, ("a1", 1.0, 2.0), ("a2", 3.0, 4.0)), {"a1": REF, "a2": REF}),
        count_hint=_hint(1),
    )
    assert rec.count_decision == CountDecision.NOT_SUPPORTED
    assert rec.links == ()
    assert _labels(rec) == {R(0, "a1"): "speaker_1", R(0, "a2"): "speaker_2"}


def test_groups_without_output_segments_are_labeled_last():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("edge", 10.5, 11.5), ("a", 1.0, 2.0))),
        _chunk(TWO[1], _up(TWO[1], ("late", 8.0, 8.4), ("b", 13.0, 14.0))),
    )
    assert rec.links == ()
    assert _labels(rec) == {
        R(0, "a"): "speaker_1",
        R(1, "b"): "speaker_2",
        R(0, "edge"): "speaker_3",
        R(1, "late"): "speaker_4",
    }
    assert _segs(rec) == [(1.0, 2.0, "speaker_1", None), (13.0, 14.0, "speaker_2", None)]


def test_cluster_evidence_without_a_segment_is_not_a_speaker():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 1.0, 2.0)), {"a": _ev(0.0, 0.0, 1.0), "ghost": REF}),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0)), {"b": REF}),
    )
    assert set(_labels(rec)) == {R(0, "a"), R(1, "b")}
    assert rec.links == ()
    assert _labels(rec)[R(0, "a")] != _labels(rec)[R(1, "b")]


def test_provider_and_mock_flag_come_from_the_chunks():
    rec = _run(
        _chunk(TWO[0], _down(TWO[0], provider="p0")),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0), provider="p1", mock=True)),
    )
    assert rec.result.provider == "p1"
    assert rec.result.is_mock is True


def test_provider_is_the_first_available_chunk_and_mock_is_false_when_none_is():
    rec = _run(
        _chunk(TWO[0], _up(TWO[0], ("a", 1.0, 2.0), provider="p0")),
        _chunk(TWO[1], _up(TWO[1], ("b", 13.0, 14.0), provider="p1")),
    )
    assert rec.result.provider == "p0"
    assert rec.result.is_mock is False
