"""Diarization error rate (DER) and Jaccard error rate (JER) (HOM-69).

All time arithmetic is in integer milliseconds.  Turns of one speaker that overlap or touch
are merged first.  The scored region is cut down by the collar around reference turn
boundaries and, when asked, by overlapped reference speech.  The DER mapping maximises the
co-occurrence time; JER uses its own mapping that minimises the total Jaccard error.  Both
are optimal one-to-one assignments (:mod:`backend.evaluation.assignment`).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any

from backend.evaluation.assignment import max_weight_assignment, min_cost_assignment
from backend.evaluation.common import NON_FINITE_RESULT, MetricValue, metrics_dict, ratio

Interval = tuple[int, int]
NO_REFERENCE_SPEECH = "no_reference_speech"


@dataclass(frozen=True)
class SpeakerTurn:
    speaker: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class DiarizationItem:
    recording_id: str
    reference: Sequence[SpeakerTurn]
    hypothesis: Sequence[SpeakerTurn]
    scored_region: tuple[int, int] | None = None  # (start_ms, end_ms); None = whole timeline
    slices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiarizationScore:
    recording_id: str
    reference_ms: int
    missed_ms: int
    false_alarm_ms: int
    confusion_ms: int
    der: float | None
    jer: float | None
    n_reference_speakers: int
    n_hypothesis_speakers: int
    speaker_count_error: int  # n_hypothesis_speakers - n_reference_speakers
    mapping: Mapping[str, str]  # reference speaker -> hypothesis speaker (DER mapping)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mapping"] = dict(self.mapping)
        return d


@dataclass(frozen=True)
class DiarizationReport:
    metrics: dict[str, MetricValue]  # "der", "jer", "speaker_count_exact"
    scores: tuple[DiarizationScore, ...]  # input order
    missed_ms: int
    false_alarm_ms: int
    confusion_ms: int
    reference_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": metrics_dict(self.metrics),
            "scores": [s.as_dict() for s in self.scores],
            "missed_ms": self.missed_ms,
            "false_alarm_ms": self.false_alarm_ms,
            "confusion_ms": self.confusion_ms,
            "reference_ms": self.reference_ms,
        }


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_collar(collar_ms: int) -> None:
    if not _is_int(collar_ms) or collar_ms < 0:
        raise ValueError(f"collar_ms must be an int >= 0, got {collar_ms!r}")


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _by_speaker(turns: Sequence[SpeakerTurn], side: str) -> dict[str, list[Interval]]:
    raw: dict[str, list[Interval]] = {}
    for t in turns:
        if not _is_int(t.start_ms) or not _is_int(t.end_ms):
            raise ValueError(f"{side} turn bounds must be ints: {t!r}")
        if t.start_ms < 0 or t.end_ms <= t.start_ms:
            raise ValueError(f"{side} turn needs 0 <= start_ms < end_ms: {t!r}")
        raw.setdefault(t.speaker, []).append((t.start_ms, t.end_ms))
    return {spk: _merge(iv) for spk, iv in sorted(raw.items())}


def _subtract(region: list[Interval], removed: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in region:
        cur = s
        for rs, re in removed:  # merged and sorted
            if re <= cur or rs >= e:
                continue
            if rs > cur:
                out.append((cur, rs))
            cur = max(cur, re)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def _active(intervals: list[Interval], starts: list[int], t: int) -> bool:
    i = bisect_right(starts, t) - 1
    return i >= 0 and intervals[i][1] > t


def _speech_in(region: list[Interval], intervals: list[Interval]) -> bool:
    return any(s < re and e > rs for s, e in intervals for rs, re in region)


def score_diarization(
    item: DiarizationItem, *, collar_ms: int = 0, skip_overlap: bool = False
) -> DiarizationScore:
    _check_collar(collar_ms)
    ref = _by_speaker(item.reference, "reference")
    hyp = _by_speaker(item.hypothesis, "hypothesis")

    if item.scored_region is not None:
        rs, re = item.scored_region
        if not _is_int(rs) or not _is_int(re) or rs < 0 or re <= rs:
            raise ValueError(f"scored_region needs 0 <= start < end: {item.scored_region!r}")
        region = [(rs, re)]
    else:
        ends = [e for ivs in (*ref.values(), *hyp.values()) for _, e in ivs]
        region = [(0, max(ends))] if ends else []

    n_ref = sum(_speech_in(region, iv) for iv in ref.values())
    n_hyp = sum(_speech_in(region, iv) for iv in hyp.values())

    if collar_ms:
        removed = []
        for ivs in ref.values():
            for s, e in ivs:
                removed.append((max(0, s - collar_ms), s + collar_ms))
                removed.append((max(0, e - collar_ms), e + collar_ms))
        region = _subtract(region, _merge(removed))

    # Elementary segments: split the scored region at every boundary.
    points = {b for iv in region for b in iv}
    for ivs in (*ref.values(), *hyp.values()):
        points.update(b for iv in ivs for b in iv)
    cuts = sorted(points)
    region_starts = [s for s, _ in region]
    ref_starts = {spk: [s for s, _ in ivs] for spk, ivs in ref.items()}
    hyp_starts = {spk: [s for s, _ in ivs] for spk, ivs in hyp.items()}

    segments: list[tuple[int, list[str], list[str]]] = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        if not _active(region, region_starts, a):
            continue
        r_on = [spk for spk, ivs in ref.items() if _active(ivs, ref_starts[spk], a)]
        if skip_overlap and len(r_on) >= 2:
            continue
        h_on = [spk for spk, ivs in hyp.items() if _active(ivs, hyp_starts[spk], a)]
        segments.append((b - a, r_on, h_on))

    ref_dur: dict[str, int] = {}
    hyp_dur: dict[str, int] = {}
    co: dict[tuple[str, str], int] = {}
    for d, r_on, h_on in segments:
        for r in r_on:
            ref_dur[r] = ref_dur.get(r, 0) + d
        for h in h_on:
            hyp_dur[h] = hyp_dur.get(h, 0) + d
        for r in r_on:
            for h in h_on:
                co[(r, h)] = co.get((r, h), 0) + d

    refs, hyps = sorted(ref_dur), sorted(hyp_dur)
    pairs = max_weight_assignment([[co.get((r, h), 0) for h in hyps] for r in refs])
    mapping = {refs[i]: hyps[j] for i, j in pairs.items() if co.get((refs[i], hyps[j]), 0) > 0}

    missed = false_alarm = confusion = reference = 0
    for d, r_on, h_on in segments:
        nr, nh = len(r_on), len(h_on)
        correct = sum(1 for r in r_on if r in mapping and mapping[r] in h_on)
        missed += max(0, nr - nh) * d
        false_alarm += max(0, nh - nr) * d
        confusion += (min(nr, nh) - correct) * d
        reference += nr * d

    der: float | None = None
    jer: float | None = None
    reason: str | None = NO_REFERENCE_SPEECH
    if reference:
        try:
            der = (missed + false_alarm + confusion) / reference
            reason = None
        except OverflowError:  # the error ms over the reference ms is too large for a float
            reason = NON_FINITE_RESULT
    if refs:
        cost = [[1 - Fraction(co.get((r, h), 0), ref_dur[r] + hyp_dur[h] - co.get((r, h), 0))
                 for h in hyps] for r in refs]  # fmt: skip
        jer_pairs = min_cost_assignment(cost)
        total = sum((cost[i][j] for i, j in jer_pairs.items()), Fraction(0))
        total += len(refs) - len(jer_pairs)  # unmapped reference speakers: error 1
        jer = float(total / len(refs))

    return DiarizationScore(
        recording_id=item.recording_id,
        reference_ms=reference,
        missed_ms=missed,
        false_alarm_ms=false_alarm,
        confusion_ms=confusion,
        der=der,
        jer=jer,
        n_reference_speakers=n_ref,
        n_hypothesis_speakers=n_hyp,
        speaker_count_error=n_hyp - n_ref,
        mapping=mapping,
        reason=reason,
    )


def evaluate_diarization(
    items: Iterable[DiarizationItem], *, collar_ms: int = 0, skip_overlap: bool = False
) -> DiarizationReport:
    _check_collar(collar_ms)
    scores = tuple(
        score_diarization(it, collar_ms=collar_ms, skip_overlap=skip_overlap) for it in items
    )
    n = len(scores)
    missed = sum(s.missed_ms for s in scores)
    false_alarm = sum(s.false_alarm_ms for s in scores)
    confusion = sum(s.confusion_ms for s in scores)
    reference = sum(s.reference_ms for s in scores)
    jers = [s.jer for s in scores if s.jer is not None]
    exact = sum(1 for s in scores if s.speaker_count_error == 0)
    metrics = {
        "der": ratio("der", missed + false_alarm + confusion, reference, n_items=n,
                     reason=NO_REFERENCE_SPEECH),
        "jer": ratio("jer", sum(jers), len(jers), n_items=len(jers),
                     n_excluded=n - len(jers), reason=NO_REFERENCE_SPEECH),
        "speaker_count_exact": ratio("speaker_count_exact", exact, n, n_items=n,
                                     reason="no_recordings"),
    }  # fmt: skip
    return DiarizationReport(metrics, scores, missed, false_alarm, confusion, reference)
