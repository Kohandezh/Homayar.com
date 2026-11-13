"""Cross-chunk speaker reconciliation (HOM-46, ``backend.speech.speaker_reconcile``).

Pure tests: no database, no ffmpeg, no provider, no audio. Every deny-case in
SPEC section 3.3 runs next to its allow-control, and each control proves the
rule caused the outcome.
"""

from __future__ import annotations

import json
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
    AmbiguousMatch,
    ChunkDiarization,
    ClusterEvidence,
    ClusterRef,
    CountDecision,
    CountSource,
    LinkEvidence,
    ReconcileSettings,
    SpeakerCountHint,
    SpeakerLink,
    reconcile_chunk_speakers,
)

# Two chunks with 2 s of shared audio: cut at 8 s, chunk audio [0, 10) and [8, 18).
# Shared region in original time: [8, 10); in chunk 1 local time: [0, 2).
SPAN0 = ASRChunkSpan(
    index=0, start_seconds=0.0, end_seconds=10.0, owned_start_seconds=0.0, owned_end_seconds=8.0
)
SPAN1 = ASRChunkSpan(
    index=1, start_seconds=8.0, end_seconds=18.0, owned_start_seconds=8.0, owned_end_seconds=18.0
)
SPAN2 = ASRChunkSpan(
    index=2, start_seconds=16.0, end_seconds=26.0, owned_start_seconds=18.0, owned_end_seconds=26.0
)


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


def _chunk(
    span: ASRChunkSpan,
    result: DiarizationResult,
    clusters: dict[str, ClusterEvidence] | None = None,
) -> ChunkDiarization:
    return ChunkDiarization(span=span, result=result, clusters=clusters or {})


def _ev(cosine_to_reference: float, model: str = "emb/1") -> ClusterEvidence:
    """A unit 2D vector at the angle whose cosine to (1, 0) is the given value."""
    angle = math.acos(max(-1.0, min(1.0, cosine_to_reference)))
    return ClusterEvidence(embedding=(math.cos(angle), math.sin(angle)), embedding_model=model)


def _ev_angle(theta: float, model: str = "emb/1") -> ClusterEvidence:
    return ClusterEvidence(embedding=(math.cos(theta), math.sin(theta)), embedding_model=model)


def _ref(chunk_index: int, label: str) -> ClusterRef:
    return ClusterRef(chunk_index=chunk_index, label=label)


def _entry(recon, cluster: ClusterRef, reason: AmbiguityReason) -> AmbiguousMatch | None:
    for match in recon.ambiguous:
        if match.cluster == cluster and match.reason == reason:
            return match
    return None


def _segments(recon) -> list[tuple[float, float, str]]:
    return [
        (segment.start_seconds, segment.end_seconds, segment.speaker_label)
        for segment in recon.result.segments
    ]


# --- R1: settings, hint and input validation --------------------------------------------


def test_default_settings_and_descriptor():
    settings = ReconcileSettings()
    assert (
        settings.min_overlap_seconds,
        settings.overlap_match,
        settings.embedding_match,
        settings.embedding_reject,
        settings.margin,
        settings.count_support,
    ) == (0.5, 0.6, 0.75, 0.4, 0.1, 0.6)
    descriptor = settings.descriptor()
    assert descriptor["version"] == RECONCILE_VERSION == "speaker-reconcile/1"
    assert descriptor == {
        "version": "speaker-reconcile/1",
        "min_overlap_seconds": 0.5,
        "overlap_match": 0.6,
        "embedding_match": 0.75,
        "embedding_reject": 0.4,
        "margin": 0.1,
        "count_support": 0.6,
    }
    # JSON-safe and canonical: an int and the same float give one descriptor.
    assert json.loads(json.dumps(descriptor)) == descriptor
    assert ReconcileSettings(min_overlap_seconds=1).descriptor() == (
        ReconcileSettings(min_overlap_seconds=1.0).descriptor()
    )
    assert ReconcileSettings(overlap_match=0.61).descriptor() != descriptor


@pytest.mark.parametrize(
    "values",
    [
        {"embedding_reject": -0.1},
        {"embedding_reject": 0.75},  # not under embedding_match
        {"embedding_match": 0.4},  # not over embedding_reject
        {"embedding_match": 1.01},
        {"overlap_match": 0},
        {"overlap_match": 1.01},
        {"margin": -0.1},
        {"margin": 1.0},
        {"count_support": 0},
        {"count_support": 1.01},
        {"min_overlap_seconds": -1},
        {"margin": float("nan")},
        {"overlap_match": float("inf")},
        {"count_support": True},  # a bool is not a number here
        {"margin": "0.1"},
    ],
)
def test_settings_refuse_invalid_combinations(values):
    with pytest.raises(ValueError):
        ReconcileSettings(**values)


def test_settings_accept_the_edges():
    ReconcileSettings(
        embedding_reject=0.0,
        embedding_match=1.0,
        overlap_match=0.001,
        margin=0.0,
        count_support=1.0,
        min_overlap_seconds=0.0,
    )


@pytest.mark.parametrize("count", [0, -1, True, 2.0])
def test_hint_refuses_bad_counts(count):
    with pytest.raises(ValueError):
        SpeakerCountHint(count=count, source=CountSource.OPERATOR)


def test_hint_accepts_one():
    assert SpeakerCountHint(count=1, source=CountSource.CHANNEL_LAYOUT).count == 1


def test_empty_chunks_are_refused():
    with pytest.raises(ValueError):
        reconcile_chunk_speakers([])


def test_chunk_indexes_must_match_their_position():
    chunks = [
        _chunk(SPAN1, _result(_seg("A", 1.0, 2.0))),
        _chunk(SPAN0, _result(_seg("B", 1.0, 2.0))),
    ]
    with pytest.raises(ValueError):
        reconcile_chunk_speakers(chunks)


def test_owned_ranges_must_be_contiguous():
    gap = ASRChunkSpan(
        index=1,
        start_seconds=8.0,
        end_seconds=18.0,
        owned_start_seconds=8.1,
        owned_end_seconds=18.0,
    )
    with pytest.raises(ValueError):
        reconcile_chunk_speakers([_chunk(SPAN0, _result()), _chunk(gap, _result())])


def test_owned_ranges_contiguous_within_tolerance():
    near = ASRChunkSpan(
        index=1,
        start_seconds=8.0,
        end_seconds=18.0,
        owned_start_seconds=8.0 + 5e-7,
        owned_end_seconds=18.0,
    )
    reconcile_chunk_speakers(
        [_chunk(SPAN0, _result(_seg("A", 1.0, 2.0))), _chunk(near, _result(_seg("B", 1.0, 2.0)))]
    )


# --- R2: original time, clipping, sorting ------------------------------------------------


def test_segments_map_to_original_time_and_clip_to_the_owned_range():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 7.5, 8.4))),  # -> [7.5, 8.0), no co-speech with B
        _chunk(SPAN1, _result(_seg("B", 0.0, 0.5))),  # -> [8.0, 8.5)
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert _segments(recon) == [(7.5, 8.0, "speaker_1"), (8.0, 8.5, "speaker_2")]


def test_a_segment_empty_after_clipping_is_dropped():
    chunks = [
        _chunk(
            SPAN0,
            _result(
                _seg("A", 8.0, 9.5),  # clipped to [8, 8): empty
                _seg("A", 9.0, 9.7),  # fully outside the owned range
            ),
        ),
        _chunk(SPAN1, _result(_seg("B", 2.0, 3.0))),  # -> [10.0, 11.0)
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert _segments(recon) == [(10.0, 11.0, "speaker_1")]
    # The clipped-away cluster is still known and still labeled, after B.
    assert recon.labels == {_ref(1, "B"): "speaker_1", _ref(0, "A"): "speaker_2"}


def test_overlapping_speakers_inside_one_chunk_are_both_kept():
    chunks = [_chunk(SPAN0, _result(_seg("A", 1.0, 3.0), _seg("B", 2.0, 2.5)))]
    recon = reconcile_chunk_speakers(chunks)
    assert _segments(recon) == [(1.0, 3.0, "speaker_1"), (2.0, 2.5, "speaker_2")]


def test_confidence_is_carried_unchanged():
    chunks = [_chunk(SPAN0, _result(_seg("A", 1.0, 2.0, confidence=0.42), _seg("B", 3.0, 4.0)))]
    recon = reconcile_chunk_speakers(chunks)
    assert [segment.confidence for segment in recon.result.segments] == [0.42, None]


def test_output_segments_are_sorted_by_start_end_and_label():
    chunks = [
        _chunk(
            SPAN0,
            _result(
                _seg("B", 3.0, 3.5),
                _seg("B", 1.0, 2.0),
                _seg("A", 1.0, 1.5),
                _seg("D", 1.0, 1.5),
            ),
        )
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert _segments(recon) == [
        (1.0, 1.5, "speaker_1"),  # A
        (1.0, 1.5, "speaker_3"),  # D: same start and end, order by label
        (1.0, 2.0, "speaker_2"),  # B
        (3.0, 3.5, "speaker_2"),
    ]


# --- R3: the unknown label ----------------------------------------------------------------


def test_unknown_segments_keep_their_label_and_are_never_matched():
    chunks = [
        _chunk(
            SPAN0,
            _result(
                _seg(UNKNOWN_SPEAKER_LABEL, 8.0, 9.0),
                _seg("A", 1.0, 2.0),
                _seg(UNKNOWN_SPEAKER_LABEL, 2.0, 3.0),
            ),
            {"A": _ev(1.0)},
        ),
        _chunk(
            SPAN1,
            _result(_seg(UNKNOWN_SPEAKER_LABEL, 0.0, 1.0), _seg("B", 1.0, 2.0)),
            {"B": _ev(1.0)},
        ),
    ]
    recon = reconcile_chunk_speakers(chunks)
    # A and B link; the two unknown segments never join anything.
    assert recon.links == (SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.EMBEDDING, 1.0),)
    assert set(recon.labels) == {_ref(0, "A"), _ref(1, "B")}
    assert _segments(recon) == [
        (1.0, 2.0, "speaker_1"),
        (2.0, 3.0, UNKNOWN_SPEAKER_LABEL),
        (8.0, 9.0, UNKNOWN_SPEAKER_LABEL),
        (9.0, 10.0, "speaker_1"),
    ]


def test_unknown_speech_is_not_counted_as_a_speaker():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0), _seg(UNKNOWN_SPEAKER_LABEL, 3.0, 4.0))),
        _chunk(SPAN1, _result(_seg("B", 1.0, 2.0))),
    ]
    # Two known groups meet the hint of 2 exactly. If the unknown speech were
    # a third speaker, the decision would fall to the merge branch instead.
    recon = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(2, CountSource.OPERATOR))
    assert recon.count_decision == CountDecision.ALREADY_MET
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2"}


# --- R4: unavailable chunks ----------------------------------------------------------------


def test_every_chunk_unavailable_is_an_unavailable_result():
    chunks = [
        _chunk(SPAN0, _unavailable(provider="p0")),
        _chunk(SPAN1, _unavailable(provider="p1", mock=True)),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.result.status == DiarizationStatus.UNAVAILABLE
    assert recon.result.segments == []
    assert recon.result.issue is not None
    assert recon.result.issue.code == "diarization_unavailable"
    assert recon.result.provider == "p0"  # the first chunk's, none is available
    assert recon.result.is_mock is True
    assert recon.labels == {}
    assert recon.links == ()
    assert recon.ambiguous == ()
    assert recon.count_decision == CountDecision.NO_HINT


def test_some_chunks_unavailable_is_a_partial_available_result():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0), _seg("A", 7.5, 9.0), provider="p0")),
        _chunk(SPAN1, _unavailable(provider="p1", mock=True)),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.result.status == DiarizationStatus.AVAILABLE
    assert recon.result.issue is not None
    assert recon.result.issue.code == "diarization_partial"
    assert recon.result.issue.retryable is False
    assert recon.result.provider == "p0"  # the first available chunk's
    assert recon.result.is_mock is True
    assert _segments(recon) == [(1.0, 2.0, "speaker_1"), (7.5, 8.0, "speaker_1")]


def test_nothing_is_invented_across_an_unavailable_chunk():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0)), {"A": _ev(1.0)}),
        _chunk(SPAN1, _unavailable()),
        _chunk(SPAN2, _result(_seg("C", 2.0, 3.0)), {"C": _ev(1.0)}),
    ]
    recon = reconcile_chunk_speakers(chunks)
    # No output segment inside the unavailable chunk's owned range [8, 18).
    assert all(not (8.0 <= start < 18.0) for start, _end, _label in _segments(recon))
    assert _segments(recon) == [(1.0, 2.0, "speaker_1"), (18.0, 19.0, "speaker_1")]
    assert recon.links == (SpeakerLink(_ref(0, "A"), _ref(2, "C"), LinkEvidence.EMBEDDING, 1.0),)
    assert recon.result.issue is not None
    assert recon.result.issue.code == "diarization_partial"


# --- R5: overlap evidence --------------------------------------------------------------------


def test_agreement_is_co_speech_over_the_smaller_side():
    # A speaks 1.2 s inside the region, B 1.6 s, together 0.8 s.
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 1.5), _seg("A", 8.0, 9.2))),
        _chunk(SPAN1, _result(_seg("B", 0.4, 2.0))),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.OVERLAP, pytest.approx(0.8 / 1.2)),
    )


def test_low_agreement_links_nothing_and_records_nothing():
    # Co-speech 0.6 s over the smaller side 1.2 s = agreement 0.5, under the 0.6 bar.
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 1.5), _seg("A", 8.0, 9.2))),
        _chunk(SPAN1, _result(_seg("B", 0.6, 2.0))),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == ()
    assert recon.ambiguous == ()
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2"}


def test_co_speech_under_min_overlap_seconds_is_no_evidence():
    def run(a_end: float, b_end: float):
        chunks = [
            _chunk(SPAN0, _result(_seg("A", 8.0, a_end))),
            _chunk(SPAN1, _result(_seg("B", 0.0, b_end))),
        ]
        return reconcile_chunk_speakers(chunks)

    deny = run(8.4, 0.4)  # 0.4 s of co-speech, under the 0.5 s minimum
    assert deny.links == ()
    assert deny.ambiguous == ()
    allow = run(8.5, 0.5)  # exactly 0.5 s, full agreement
    assert allow.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.OVERLAP, pytest.approx(1.0)),
    )


def test_a_clusters_own_overlapping_segments_are_not_counted_twice():
    # A's two segments cover 1.5 s once (not 2.5 s); B covers 1.0 s of it.
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 8.0, 9.0), _seg("A", 8.5, 9.5))),
        _chunk(SPAN1, _result(_seg("B", 0.0, 1.0))),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.OVERLAP, pytest.approx(1.0)),
    )


# --- R6: embedding evidence ------------------------------------------------------------------


def _embedding_pair(left: ClusterEvidence | None, right: ClusterEvidence | None):
    return [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0)), {"A": left} if left else {}),
        _chunk(SPAN1, _result()),  # a middle chunk with no speech of its own
        _chunk(SPAN2, _result(_seg("C", 1.0, 2.0)), {"C": right} if right else {}),
    ]


@pytest.mark.parametrize(
    "left,right",
    [
        (_ev(1.0, model="emb/1"), _ev(1.0, model="emb/2")),  # different model
        (ClusterEvidence((1.0, 0.0), "emb/1"), ClusterEvidence((1.0, 0.0, 0.0), "emb/1")),
        (ClusterEvidence((), "emb/1"), ClusterEvidence((), "emb/1")),  # empty
        (ClusterEvidence((0.0, 0.0), "emb/1"), _ev(1.0)),  # zero norm
        (ClusterEvidence((float("nan"), 0.0), "emb/1"), _ev(1.0)),
        (ClusterEvidence((float("inf"), 0.0), "emb/1"), _ev(1.0)),
        (_ev(1.0), None),  # one side has no evidence
    ],
)
def test_embedding_evidence_needs_compatible_vectors(left, right):
    recon = reconcile_chunk_speakers(_embedding_pair(left, right))
    assert recon.links == ()
    assert recon.ambiguous == ()
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2"}


def test_the_same_vector_in_different_chunks_links_as_embedding():
    recon = reconcile_chunk_speakers(_embedding_pair(_ev(1.0), _ev(1.0)))
    assert recon.links == (
        SpeakerLink(_ref(0, "A"), _ref(2, "C"), LinkEvidence.EMBEDDING, pytest.approx(1.0)),
    )
    assert set(recon.labels.values()) == {"speaker_1"}


def test_a_cosine_between_reject_and_match_is_not_a_candidate():
    recon = reconcile_chunk_speakers(_embedding_pair(_ev(1.0), _ev(0.5)))
    assert recon.links == ()
    assert recon.ambiguous == ()  # not a candidate, so nothing is recorded


# --- R7: pair score --------------------------------------------------------------------------


def test_overlap_and_embedding_together_link_as_both_with_the_min_score():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 8.0, 9.0)), {"A": _ev(1.0)}),
        _chunk(SPAN1, _result(_seg("B", 0.0, 1.0)), {"B": _ev(0.8)}),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.BOTH, pytest.approx(0.8)),
    )


def test_conflicting_evidence_never_links_and_is_recorded_for_both_sides():
    def run(with_embedding: bool):
        clusters = {"B": _ev(0.2)} if with_embedding else {}
        return reconcile_chunk_speakers(
            [
                _chunk(SPAN0, _result(_seg("A", 8.0, 9.0)), {"A": _ev(1.0)}),
                _chunk(SPAN1, _result(_seg("B", 0.0, 1.0)), clusters),
            ]
        )

    deny = run(True)  # overlap says same (1.0), embedding says different (0.2 < 0.40)
    assert deny.links == ()
    assert _entry(deny, _ref(0, "A"), AmbiguityReason.CONFLICTING_EVIDENCE) == AmbiguousMatch(
        _ref(0, "A"), (_ref(1, "B"),), AmbiguityReason.CONFLICTING_EVIDENCE
    )
    assert _entry(deny, _ref(1, "B"), AmbiguityReason.CONFLICTING_EVIDENCE) == AmbiguousMatch(
        _ref(1, "B"), (_ref(0, "A"),), AmbiguityReason.CONFLICTING_EVIDENCE
    )
    # Control: the same overlap without embeddings links.
    allow = run(False)
    assert allow.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.OVERLAP, pytest.approx(1.0)),
    )


# --- R8: matching ---------------------------------------------------------------------------


def test_mutual_best_candidates_link_and_share_a_global_label():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0)), {"A": _ev(1.0)}),
        _chunk(SPAN1, _result(_seg("B", 1.0, 2.0)), {"B": _ev(0.9)}),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.EMBEDDING, pytest.approx(0.9)),
    )
    assert recon.labels == {_ref(0, "A"): "speaker_1", _ref(1, "B"): "speaker_1"}
    assert _segments(recon) == [(1.0, 2.0, "speaker_1"), (9.0, 10.0, "speaker_1")]


def test_a_runner_up_within_the_margin_blocks_the_link_until_removed():
    def run(with_runner_up: bool):
        clusters = {"B": _ev(0.85), "C": _ev(0.78)} if with_runner_up else {"B": _ev(0.85)}
        return reconcile_chunk_speakers(
            [
                _chunk(SPAN0, _result(_seg("A", 1.0, 2.0)), {"A": _ev(1.0)}),
                _chunk(SPAN1, _result(_seg("B", 1.0, 2.0), _seg("C", 3.0, 4.0)), clusters),
            ]
        )

    deny = run(True)  # 0.85 beats 0.78 by 0.07, under the 0.10 margin
    assert deny.links == ()
    assert _entry(deny, _ref(0, "A"), AmbiguityReason.NO_MARGIN) == AmbiguousMatch(
        _ref(0, "A"), (_ref(1, "B"), _ref(1, "C")), AmbiguityReason.NO_MARGIN
    )
    assert _entry(deny, _ref(1, "B"), AmbiguityReason.NO_MARGIN) == AmbiguousMatch(
        _ref(1, "B"), (_ref(0, "A"),), AmbiguityReason.NO_MARGIN
    )
    # Control: the same pair links once the runner-up stops being a candidate.
    allow = run(False)
    assert allow.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.EMBEDDING, pytest.approx(0.85)),
    )


def test_a_best_that_is_not_mutual_is_recorded_not_mutual():
    def run(with_rival: bool):
        first = (
            (_seg("A", 1.0, 2.0), _seg("A2", 5.0, 6.0)) if with_rival else (_seg("A", 1.0, 2.0),)
        )
        clusters = (
            {"A": _ev_angle(math.acos(0.8)), "A2": _ev_angle(-math.acos(0.96))}
            if with_rival
            else {"A": _ev_angle(math.acos(0.8))}
        )
        return reconcile_chunk_speakers(
            [
                _chunk(SPAN0, _result(*first), clusters),
                _chunk(SPAN1, _result(_seg("B", 1.0, 2.0)), {"B": _ev(1.0)}),
            ]
        )

    deny = run(True)  # B's best is A2 (0.96), so A's best (0.8) is not mutual
    assert deny.links == (
        SpeakerLink(_ref(0, "A2"), _ref(1, "B"), LinkEvidence.EMBEDDING, pytest.approx(0.96)),
    )
    assert _entry(deny, _ref(0, "A"), AmbiguityReason.NOT_MUTUAL) == AmbiguousMatch(
        _ref(0, "A"), (_ref(1, "B"),), AmbiguityReason.NOT_MUTUAL
    )
    # Control: without the rival, A and B link.
    allow = run(False)
    assert allow.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.EMBEDDING, pytest.approx(0.8)),
    )


def test_a_runner_up_exactly_margin_away_still_links():
    # 0.9 - 0.8 is 0.09999999999999998 in floats, but the rule says "at least
    # margin", so the pair links.
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0)), {"A": _ev(1.0)}),
        _chunk(
            SPAN1,
            _result(_seg("B", 1.0, 2.0), _seg("C", 3.0, 4.0)),
            {"B": _ev(0.9), "C": _ev(0.8)},
        ),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.EMBEDDING, pytest.approx(0.9)),
    )
    assert _entry(recon, _ref(1, "C"), AmbiguityReason.NOT_MUTUAL) is not None


def test_a_link_that_would_join_two_clusters_of_one_chunk_is_skipped():
    # Angles from B1 at 0: A0a +acos(0.95), A0b -acos(0.5), C2 -acos(0.9).
    # Accepted: (A0a, B1) 0.95, (B1, C2) 0.9, (A0b, C2) ~0.83. Joining the first
    # two makes the third a cannot-link: it would put A0a and A0b in one group.
    chunks = [
        _chunk(
            SPAN0,
            _result(_seg("A0a", 1.0, 2.0), _seg("A0b", 3.0, 4.0)),
            {"A0a": _ev_angle(math.acos(0.95)), "A0b": _ev_angle(-math.acos(0.5))},
        ),
        _chunk(SPAN1, _result(_seg("B1", 1.0, 2.0)), {"B1": _ev(1.0)}),
        _chunk(SPAN2, _result(_seg("C2", 1.0, 2.0)), {"C2": _ev_angle(-math.acos(0.9))}),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == (
        SpeakerLink(_ref(0, "A0a"), _ref(1, "B1"), LinkEvidence.EMBEDDING, pytest.approx(0.95)),
        SpeakerLink(_ref(1, "B1"), _ref(2, "C2"), LinkEvidence.EMBEDDING, pytest.approx(0.9)),
    )
    assert _entry(recon, _ref(0, "A0b"), AmbiguityReason.CANNOT_LINK) == AmbiguousMatch(
        _ref(0, "A0b"), (_ref(2, "C2"),), AmbiguityReason.CANNOT_LINK
    )
    assert _entry(recon, _ref(2, "C2"), AmbiguityReason.CANNOT_LINK) == AmbiguousMatch(
        _ref(2, "C2"), (_ref(0, "A0b"),), AmbiguityReason.CANNOT_LINK
    )
    # The cannot-link kept the two clusters of chunk 0 apart.
    assert recon.labels[_ref(0, "A0a")] != recon.labels[_ref(0, "A0b")]
    assert (
        recon.labels[_ref(0, "A0a")] == recon.labels[_ref(1, "B1")] == recon.labels[_ref(2, "C2")]
    )
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2"}


# --- R9: count hint -----------------------------------------------------------------------


def _three_unlinked_groups():
    # A1 and B1 have cosine 0.7: support for a count merge, but under the 0.75
    # embedding bar, so the matching phase makes no link. A2 has no evidence.
    return [
        _chunk(SPAN0, _result(_seg("A1", 1.0, 2.0), _seg("A2", 3.0, 4.0)), {"A1": _ev(1.0)}),
        _chunk(SPAN1, _result(_seg("B1", 1.0, 2.0)), {"B1": _ev(0.7)}),
    ]


def test_no_hint_changes_nothing():
    recon = reconcile_chunk_speakers(_three_unlinked_groups())
    assert recon.count_decision == CountDecision.NO_HINT
    assert recon.links == ()
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2", "speaker_3"}


def test_a_provider_estimate_hint_is_never_applied():
    recon = reconcile_chunk_speakers(
        _three_unlinked_groups(),
        count_hint=SpeakerCountHint(2, CountSource.PROVIDER_ESTIMATE),
    )
    assert recon.count_decision == CountDecision.UNTRUSTED_SOURCE
    assert recon.links == ()
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2", "speaker_3"}


def test_an_operator_hint_with_a_supporting_pair_merges_to_the_hint():
    recon = reconcile_chunk_speakers(
        _three_unlinked_groups(),
        count_hint=SpeakerCountHint(2, CountSource.OPERATOR),
    )
    assert recon.count_decision == CountDecision.APPLIED
    assert recon.links == (
        SpeakerLink(_ref(0, "A1"), _ref(1, "B1"), LinkEvidence.COUNT_HINT, pytest.approx(0.7)),
    )
    assert recon.labels[_ref(0, "A1")] == recon.labels[_ref(1, "B1")]
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2"}


def test_a_trusted_hint_already_met_changes_nothing():
    recon = reconcile_chunk_speakers(
        _three_unlinked_groups(),
        count_hint=SpeakerCountHint(3, CountSource.CHANNEL_LAYOUT),
    )
    assert recon.count_decision == CountDecision.ALREADY_MET
    assert recon.links == ()
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2", "speaker_3"}


def test_a_trusted_hint_above_the_group_count_splits_nothing():
    recon = reconcile_chunk_speakers(
        _three_unlinked_groups(),
        count_hint=SpeakerCountHint(4, CountSource.OPERATOR),
    )
    assert recon.count_decision == CountDecision.FEWER_THAN_HINT
    assert recon.links == ()
    assert set(recon.labels.values()) == {"speaker_1", "speaker_2", "speaker_3"}


def test_an_unreachable_hint_keeps_none_of_the_merges():
    chunks = _three_unlinked_groups() + [_chunk(SPAN2, _result(_seg("C2", 1.0, 2.0)))]
    deny = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(2, CountSource.OPERATOR))
    # Four groups, one supporting pair: the merge reaches 3, not 2, so nothing is kept.
    assert deny.count_decision == CountDecision.NOT_SUPPORTED
    assert deny.links == ()
    assert set(deny.labels.values()) == {"speaker_1", "speaker_2", "speaker_3", "speaker_4"}
    assert deny.labels[_ref(0, "A1")] != deny.labels[_ref(1, "B1")]
    # Control: a hint the pairs can reach is applied.
    allow = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(3, CountSource.OPERATOR))
    assert allow.count_decision == CountDecision.APPLIED
    assert allow.links == (
        SpeakerLink(_ref(0, "A1"), _ref(1, "B1"), LinkEvidence.COUNT_HINT, pytest.approx(0.7)),
    )


def test_conflicting_pairs_are_excluded_from_count_merges():
    # A overlaps B at 0.95, C at 0.88 and D at 0.86, all over overlap_match 0.85.
    # In the deny run B conflicts, so A's candidates are C and D and the margin
    # (0.02) blocks the link; in the control run B is a candidate too and the
    # margin (0.07) still blocks it. So no matching link happens either way, and
    # the count merge picks its pair by support alone: with B's cosine at 0.2
    # the pair (A, B) is excluded although its support 0.95 is the best; with
    # cosine 0.5 it does not conflict and wins.
    settings = ReconcileSettings(overlap_match=0.85)

    def run(b_cosine: float):
        chunks = [
            _chunk(SPAN0, _result(_seg("A", 1.0, 2.0), _seg("A", 8.0, 9.0)), {"A": _ev(1.0)}),
            _chunk(
                SPAN1,
                _result(
                    _seg("B", 0.0, 0.95),
                    _seg("B", 1.5, 2.0),
                    _seg("C", 0.12, 1.12),
                    _seg("D", 0.14, 1.14),
                ),
                {"B": _ev(b_cosine)},
            ),
        ]
        return reconcile_chunk_speakers(
            chunks, settings=settings, count_hint=SpeakerCountHint(3, CountSource.OPERATOR)
        )

    deny = run(0.2)
    assert deny.count_decision == CountDecision.APPLIED
    assert deny.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "C"), LinkEvidence.COUNT_HINT, pytest.approx(0.88)),
    )
    assert deny.labels[_ref(0, "A")] != deny.labels[_ref(1, "B")]
    assert _entry(deny, _ref(0, "A"), AmbiguityReason.CONFLICTING_EVIDENCE) is not None
    # Control: the same layout with a non-conflicting cosine merges the best pair.
    allow = run(0.5)
    assert allow.count_decision == CountDecision.APPLIED
    assert allow.links == (
        SpeakerLink(_ref(0, "A"), _ref(1, "B"), LinkEvidence.COUNT_HINT, pytest.approx(0.95)),
    )
    assert allow.labels[_ref(0, "A")] == allow.labels[_ref(1, "B")]


def test_cannot_link_holds_for_count_merges():
    theta_a0b = -math.acos(0.8)
    chunks = [
        _chunk(
            SPAN0,
            _result(_seg("A0a", 1.0, 2.0), _seg("A0b", 3.0, 4.0)),
            {"A0a": _ev_angle(math.acos(0.95)), "A0b": _ev_angle(theta_a0b)},
        ),
        _chunk(SPAN1, _result(_seg("B1", 1.0, 2.0)), {"B1": _ev(1.0)}),
        _chunk(SPAN2, _result(_seg("C2", 1.0, 2.0)), {"C2": _ev_angle(theta_a0b - math.acos(0.7))}),
    ]
    recon = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(2, CountSource.OPERATOR))
    # (A0a, B1) linked in matching; (A0b, B1) has support 0.8 but would join two
    # clusters of chunk 0, so the merge goes to C2 (support 0.7) instead.
    assert recon.count_decision == CountDecision.APPLIED
    assert recon.links == (
        SpeakerLink(_ref(0, "A0a"), _ref(1, "B1"), LinkEvidence.EMBEDDING, pytest.approx(0.95)),
        SpeakerLink(_ref(0, "A0b"), _ref(2, "C2"), LinkEvidence.COUNT_HINT, pytest.approx(0.7)),
    )
    assert _entry(recon, _ref(0, "A0b"), AmbiguityReason.CANNOT_LINK) == AmbiguousMatch(
        _ref(0, "A0b"), (_ref(1, "B1"),), AmbiguityReason.CANNOT_LINK
    )
    assert _entry(recon, _ref(1, "B1"), AmbiguityReason.CANNOT_LINK) == AmbiguousMatch(
        _ref(1, "B1"), (_ref(0, "A0b"),), AmbiguityReason.CANNOT_LINK
    )
    assert recon.labels[_ref(0, "A0a")] != recon.labels[_ref(0, "A0b")]
    assert recon.labels[_ref(0, "A0b")] == recon.labels[_ref(2, "C2")]


def test_a_failed_hint_changes_nothing_not_even_ambiguous_records():
    # a1 and a2 in chunk 0, b in chunk 1, cosines 0.70 and 0.65 to b, no overlap
    # evidence. A hint of 1 cannot merge the two clusters of chunk 0, so the
    # phase fails; the cannot-link records it made on the way must roll back too.
    chunks = [
        _chunk(
            SPAN0,
            _result(_seg("a1", 1.0, 2.0), _seg("a2", 3.0, 4.0)),
            {"a1": _ev(0.70), "a2": _ev(0.65)},
        ),
        _chunk(SPAN1, _result(_seg("b", 1.0, 2.0)), {"b": _ev(1.0)}),
    ]
    no_hint = reconcile_chunk_speakers(chunks)
    deny = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(1, CountSource.OPERATOR))
    assert deny.count_decision == CountDecision.NOT_SUPPORTED
    assert deny.ambiguous == no_hint.ambiguous == ()
    assert deny.links == no_hint.links == ()
    assert deny.labels == no_hint.labels
    # Control: a hint of 2 the support can reach is applied.
    allow = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(2, CountSource.OPERATOR))
    assert allow.count_decision == CountDecision.APPLIED
    assert allow.links == (
        SpeakerLink(_ref(0, "a1"), _ref(1, "b"), LinkEvidence.COUNT_HINT, pytest.approx(0.70)),
    )


# --- R10: global labels ---------------------------------------------------------------------


def test_labels_follow_the_earliest_output_segment_not_the_name():
    chunks = [_chunk(SPAN0, _result(_seg("B", 1.0, 2.0), _seg("A", 3.0, 4.0)))]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.labels == {_ref(0, "B"): "speaker_1", _ref(0, "A"): "speaker_2"}


def test_a_start_tie_breaks_by_the_smallest_cluster_ref():
    chunks = [_chunk(SPAN0, _result(_seg("B", 2.0, 2.5), _seg("A", 2.0, 3.0)))]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.labels == {_ref(0, "A"): "speaker_1", _ref(0, "B"): "speaker_2"}


def test_groups_without_output_segments_come_last():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0), _seg("Z", 9.0, 9.5))),  # Z is clipped away
        _chunk(SPAN1, _result(_seg("B", 2.0, 3.0))),  # -> [10.0, 11.0), outside the region
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert _segments(recon) == [(1.0, 2.0, "speaker_1"), (10.0, 11.0, "speaker_2")]
    assert recon.labels == {
        _ref(0, "A"): "speaker_1",
        _ref(1, "B"): "speaker_2",
        _ref(0, "Z"): "speaker_3",
    }


# --- R11: result fields ----------------------------------------------------------------------


def test_provider_comes_from_the_first_available_chunk():
    chunks = [
        _chunk(SPAN0, _result(_seg("A", 1.0, 2.0), provider="p0")),
        _chunk(SPAN1, _result(_seg("B", 1.0, 2.0), provider="p1", mock=True)),
    ]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.result.provider == "p0"
    assert recon.result.issue is None
    assert recon.result.is_mock is True


# --- R12: purity and determinism --------------------------------------------------------------


def test_a_single_chunk_renames_labels_and_adds_no_links():
    chunks = [_chunk(SPAN0, _result(_seg("B", 1.0, 2.0), _seg("A", 3.0, 4.0)))]
    recon = reconcile_chunk_speakers(chunks)
    assert recon.links == ()
    assert recon.ambiguous == ()
    assert recon.labels == {_ref(0, "B"): "speaker_1", _ref(0, "A"): "speaker_2"}
    assert _segments(recon) == [(1.0, 2.0, "speaker_1"), (3.0, 4.0, "speaker_2")]


def test_reordering_segments_and_clusters_changes_nothing():
    def build(reverse: bool) -> list[ChunkDiarization]:
        first_segments = [_seg("A", 1.0, 2.0), _seg("A", 8.0, 9.2), _seg("A2", 5.0, 6.0)]
        second_segments = [_seg("B", 0.4, 2.0), _seg("C", 3.0, 4.0)]
        first_clusters = {"A": _ev(1.0), "A2": _ev(-0.5)}  # A2 stays a non-candidate
        second_clusters = {"B": _ev(0.85), "C": _ev(0.78)}
        if reverse:
            first_segments.reverse()
            second_segments.reverse()
            # Same mapping, built in the opposite key order.
            first_clusters = dict(reversed(list(first_clusters.items())))
            second_clusters = dict(reversed(list(second_clusters.items())))
        return [
            _chunk(SPAN0, _result(*first_segments), first_clusters),
            _chunk(SPAN1, _result(*second_segments), second_clusters),
        ]

    assert reconcile_chunk_speakers(build(False)) == reconcile_chunk_speakers(build(True))


def test_the_same_input_gives_the_same_output():
    chunks = _three_unlinked_groups()
    first = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(2, CountSource.OPERATOR))
    second = reconcile_chunk_speakers(chunks, count_hint=SpeakerCountHint(2, CountSource.OPERATOR))
    assert first == second
    assert first.version == RECONCILE_VERSION
