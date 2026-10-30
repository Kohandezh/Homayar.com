"""DER/JER, speaker mapping and speaker counts (HOM-69).

Every expected number below is worked out by hand from the SPEC definitions.
"""

from __future__ import annotations

import json

import pytest

from backend.evaluation import (
    DiarizationItem,
    SpeakerTurn,
    evaluate_diarization,
    score_diarization,
)
from backend.evaluation.assignment import max_weight_assignment, min_cost_assignment


def T(speaker: str, start: int, end: int) -> SpeakerTurn:
    return SpeakerTurn(speaker, start, end)


def item(ref: list[SpeakerTurn], hyp: list[SpeakerTurn], **kw: object) -> DiarizationItem:
    return DiarizationItem("rec", ref, hyp, **kw)  # type: ignore[arg-type]


def test_perfect_match() -> None:
    turns = [T("A", 0, 1000), T("B", 1000, 2000)]
    s = score_diarization(item(turns, turns))
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (0, 0, 0, 2000)
    assert s.der == 0.0
    assert s.jer == 0.0
    assert dict(s.mapping) == {"A": "A", "B": "B"}
    assert s.reason is None


def test_label_permutation_is_still_perfect() -> None:
    ref = [T("A", 0, 1000), T("B", 1000, 2000)]
    hyp = [T("spk1", 0, 1000), T("spk0", 1000, 2000)]
    s = score_diarization(item(ref, hyp))
    assert s.der == 0.0
    assert s.jer == 0.0
    assert dict(s.mapping) == {"A": "spk1", "B": "spk0"}


def test_pure_miss() -> None:
    ref = [T("A", 0, 1000), T("B", 1000, 2000)]
    hyp = [T("x", 0, 1000)]
    s = score_diarization(item(ref, hyp))
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (1000, 0, 0, 2000)
    assert s.der == pytest.approx(0.5)
    # A: Jaccard error 0; B unmapped: 1.0.
    assert s.jer == pytest.approx(0.5)
    assert (s.n_reference_speakers, s.n_hypothesis_speakers, s.speaker_count_error) == (2, 1, -1)


def test_pure_false_alarm() -> None:
    s = score_diarization(item([T("A", 0, 1000)], [T("x", 0, 2000)]))
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (0, 1000, 0, 1000)
    assert s.der == pytest.approx(1.0)
    # R=1000, H=2000, I=1000: 1 - 1000/2000.
    assert s.jer == pytest.approx(0.5)


def test_confusion() -> None:
    ref = [T("A", 0, 3000), T("B", 3000, 4000)]
    hyp = [T("x", 0, 4000)]
    s = score_diarization(item(ref, hyp))
    assert dict(s.mapping) == {"A": "x"}
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (0, 0, 1000, 4000)
    assert s.der == pytest.approx(0.25)
    # A: 1 - 3000/4000 = 0.25; B unmapped: 1.0.
    assert s.jer == pytest.approx(0.625)


def test_optimal_mapping_beats_greedy() -> None:
    # Co-occurrence A-x 1000, A-y 900, B-x 900, B-y 0.
    # Greedy takes A-x (1000); the optimum is A-y + B-x (1800).
    ref = [T("A", 0, 1900), T("B", 1900, 2800)]
    hyp = [T("x", 0, 1000), T("y", 1000, 1900), T("x", 1900, 2800)]
    s = score_diarization(item(ref, hyp))
    assert dict(s.mapping) == {"A": "y", "B": "x"}
    assert s.confusion_ms == 1000
    assert s.der == pytest.approx(1000 / 2800)
    # Jaccard errors: A-x 9/14, A-y 10/19, B-x 10/19, B-y 1. Best total: A-y + B-x.
    assert s.jer == pytest.approx(10 / 19)


def test_overlapped_reference_speech() -> None:
    ref = [T("A", 0, 2000), T("B", 1000, 3000)]
    hyp = [T("x", 0, 2000), T("y", 2000, 3000)]
    s = score_diarization(item(ref, hyp))
    assert dict(s.mapping) == {"A": "x", "B": "y"}
    # Overlap counts once per speaker: 1000 + 2*1000 + 1000.
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (1000, 0, 0, 4000)
    assert s.der == pytest.approx(0.25)
    # A: 1 - 2000/2000 = 0; B: R=2000, H=1000, I=1000: 0.5.
    assert s.jer == pytest.approx(0.25)


def test_skip_overlap_removes_overlapped_time() -> None:
    ref = [T("A", 0, 2000), T("B", 1000, 3000)]
    hyp = [T("x", 0, 2000), T("y", 2000, 3000)]
    s = score_diarization(item(ref, hyp), skip_overlap=True)
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (0, 0, 0, 2000)
    assert s.der == 0.0
    assert s.jer == 0.0
    # Speaker counts are taken before overlap removal.
    assert (s.n_reference_speakers, s.n_hypothesis_speakers) == (2, 2)


def test_collar_removes_time_around_reference_boundaries() -> None:
    ref = [T("A", 0, 1000)]
    hyp = [T("x", 100, 1100)]
    s0 = score_diarization(item(ref, hyp))
    assert (s0.missed_ms, s0.false_alarm_ms, s0.reference_ms) == (100, 100, 1000)
    assert s0.der == pytest.approx(0.2)

    s100 = score_diarization(item(ref, hyp), collar_ms=100)
    # Removed: [0, 100) (clipped at 0) and [900, 1100). Left: [100, 900).
    assert (s100.missed_ms, s100.false_alarm_ms, s100.reference_ms) == (0, 0, 800)
    assert s100.der == 0.0
    assert s100.jer == 0.0

    s50 = score_diarization(item(ref, hyp), collar_ms=50)
    # Left: [50, 950) and [1050, 1100).
    assert (s50.missed_ms, s50.false_alarm_ms, s50.reference_ms) == (50, 50, 900)
    assert s50.der == pytest.approx(100 / 900)
    # R=900, H=900, I=850: 1 - 850/950.
    assert s50.jer == pytest.approx(2 / 19)


def test_same_speaker_turns_are_merged_before_the_collar() -> None:
    ref = [T("A", 0, 500), T("A", 500, 1000)]
    hyp = [T("x", 0, 600), T("x", 400, 1000)]
    s = score_diarization(item(ref, hyp), collar_ms=100)
    # One merged turn [0, 1000): no collar at 500.
    assert s.reference_ms == 800
    assert s.der == 0.0
    assert s.n_reference_speakers == 1


def test_scored_region_ignores_speech_outside() -> None:
    ref = [T("A", 0, 1000), T("B", 1000, 2000)]
    hyp = [T("x", 0, 1000), T("y", 1000, 1500)]
    whole = score_diarization(item(ref, hyp))
    assert (whole.missed_ms, whole.reference_ms) == (500, 2000)
    assert whole.der == pytest.approx(0.25)

    first = score_diarization(item(ref, hyp, scored_region=(0, 1000)))
    assert (first.missed_ms, first.reference_ms) == (0, 1000)
    assert first.der == 0.0
    assert (first.n_reference_speakers, first.n_hypothesis_speakers) == (1, 1)

    middle = score_diarization(item(ref, hyp, scored_region=(500, 1500)))
    assert middle.reference_ms == 1000
    assert middle.der == 0.0
    assert middle.n_reference_speakers == 2


def test_more_hypothesis_speakers_than_reference() -> None:
    ref = [T("A", 0, 2000)]
    hyp = [T("x", 0, 1500), T("y", 1500, 2000)]
    s = score_diarization(item(ref, hyp))
    assert dict(s.mapping) == {"A": "x"}
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms) == (0, 0, 500)
    assert s.der == pytest.approx(0.25)
    assert s.jer == pytest.approx(0.25)
    assert s.speaker_count_error == 1


def test_fewer_hypothesis_speakers_than_reference() -> None:
    ref = [T("A", 0, 1000), T("B", 1000, 2000), T("C", 2000, 3000)]
    hyp = [T("x", 0, 2000), T("y", 2000, 3000)]
    s = score_diarization(item(ref, hyp))
    assert s.mapping["C"] == "y"
    assert len(s.mapping) == 2
    assert s.confusion_ms == 1000
    assert s.der == pytest.approx(1 / 3)
    # C: 0; the mapped one of A/B: 0.5; the other: 1.0.
    assert s.jer == pytest.approx(0.5)
    assert s.speaker_count_error == -1


def test_empty_hypothesis() -> None:
    s = score_diarization(item([T("A", 0, 1000)], []))
    assert (s.missed_ms, s.reference_ms) == (1000, 1000)
    assert s.der == 1.0
    assert s.jer == 1.0
    assert dict(s.mapping) == {}
    assert s.n_hypothesis_speakers == 0


def test_empty_reference() -> None:
    s = score_diarization(item([], [T("x", 0, 500)]))
    assert s.false_alarm_ms == 500
    assert s.reference_ms == 0
    assert s.der is None
    assert s.jer is None
    assert s.reason == "no_reference_speech"


def test_both_empty() -> None:
    s = score_diarization(item([], []))
    assert (s.missed_ms, s.false_alarm_ms, s.confusion_ms, s.reference_ms) == (0, 0, 0, 0)
    assert s.der is None
    assert s.reason == "no_reference_speech"


def test_corpus_der_is_weighted_by_reference_time() -> None:
    perfect = DiarizationItem("r1", [T("A", 0, 1000)], [T("x", 0, 1000)])
    missed = DiarizationItem("r2", [T("A", 0, 3000)], [])
    no_ref = DiarizationItem("r3", [], [T("x", 0, 500)])
    report = evaluate_diarization([perfect, missed, no_ref])
    der = report.metrics["der"]
    # (0 + 3000 + 500) / (1000 + 3000 + 0); a mean of DERs would differ.
    assert (der.numerator, der.denominator) == (3500, 4000)
    assert der.value == pytest.approx(0.875)
    assert der.n_items == 3
    assert (report.missed_ms, report.false_alarm_ms, report.reference_ms) == (3000, 500, 4000)
    jer = report.metrics["jer"]
    assert (jer.numerator, jer.denominator, jer.n_items, jer.n_excluded) == (1.0, 2, 2, 1)
    assert jer.value == pytest.approx(0.5)
    exact = report.metrics["speaker_count_exact"]
    # r1: 1 vs 1 exact; r2: 1 vs 0; r3: 0 vs 1.
    assert (exact.numerator, exact.denominator) == (1, 3)
    assert [s.recording_id for s in report.scores] == ["r1", "r2", "r3"]


def test_corpus_with_no_reference_speech() -> None:
    report = evaluate_diarization([DiarizationItem("r", [], [T("x", 0, 10)])])
    assert report.metrics["der"].value is None
    assert report.metrics["der"].reason == "no_reference_speech"
    assert report.metrics["jer"].value is None
    assert report.metrics["jer"].n_excluded == 1
    assert report.false_alarm_ms == 10


def test_report_as_dict_is_json_safe() -> None:
    report = evaluate_diarization([DiarizationItem("r", [T("A", 0, 10)], [T("x", 0, 10)])])
    d = report.as_dict()
    json.dumps(d)
    assert d["scores"][0]["mapping"] == {"A": "x"}


@pytest.mark.parametrize(
    "turn",
    [
        T("A", 5, 5),
        T("A", 10, 5),
        T("A", -1, 5),
        SpeakerTurn("A", 0.0, 5),
        SpeakerTurn("A", 0, True),
    ],  # type: ignore[arg-type]
)
def test_invalid_turn_is_refused(turn: SpeakerTurn) -> None:
    with pytest.raises(ValueError):
        score_diarization(item([turn], []))
    with pytest.raises(ValueError):
        score_diarization(item([], [turn]))


def test_one_ms_turn_is_allowed() -> None:
    s = score_diarization(item([T("A", 0, 1)], [T("x", 0, 1)]))
    assert s.reference_ms == 1
    assert s.der == 0.0


def test_negative_collar_is_refused_and_zero_is_allowed() -> None:
    it = item([T("A", 0, 10)], [T("x", 0, 10)])
    with pytest.raises(ValueError):
        score_diarization(it, collar_ms=-1)
    with pytest.raises(ValueError):
        evaluate_diarization([it], collar_ms=-1)
    assert score_diarization(it, collar_ms=0).der == 0.0


@pytest.mark.parametrize("region", [(10, 10), (10, 5), (-1, 5)])
def test_invalid_scored_region_is_refused(region: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        score_diarization(item([T("A", 0, 10)], [], scored_region=region))


def test_assignment_helpers() -> None:
    assert max_weight_assignment([[10, 9], [9, 0]]) == {0: 1, 1: 0}
    assert min_cost_assignment([[4, 1, 3], [2, 0, 5], [3, 2, 2]]) == {0: 1, 1: 0, 2: 2}
    # Rectangular, both ways.
    assert max_weight_assignment([[1, 5, 2]]) == {0: 1}
    assert max_weight_assignment([[1], [5], [2]]) == {1: 0}
    assert min_cost_assignment([]) == {}
    assert min_cost_assignment([[]]) == {}


def test_der_too_large_for_a_float() -> None:
    # Turn 4: 10**400 ms of false alarm over 1 ms of reference speech.
    it = item([T("A", 0, 1)], [T("x", 0, 10**400)])
    s = score_diarization(it)
    assert s.der is None
    assert s.reason == "non_finite_result"
    assert s.false_alarm_ms == 10**400 - 1
    assert s.jer == pytest.approx(1.0)
    report = evaluate_diarization([it])
    assert report.metrics["der"].value is None
    assert report.metrics["der"].reason == "non_finite_result"
