"""WER/CER, per-slice aggregation and a qualification gate.

Error rates are corpus-level (sum of edits / sum of reference lengths), after
the language normaliser in :func:`backend.speech.text_normalize.normalize_for_matching`
so orthographic variants (``ي``/``ی``, ZWNJ, digits) are not counted as errors.
The gate never returns ``PASS`` without enough utterances; a model/language is
only "qualified" for the dataset and slice it was measured on.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from backend.speech.text_normalize import normalize_for_matching


def levenshtein(ref: Sequence[object], hyp: Sequence[object]) -> int:
    if len(ref) < len(hyp):
        ref, hyp = hyp, ref
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class ErrorCounts:
    word_edits: int
    ref_words: int
    char_edits: int
    ref_chars: int

    def __add__(self, other: ErrorCounts) -> ErrorCounts:
        return ErrorCounts(
            self.word_edits + other.word_edits,
            self.ref_words + other.ref_words,
            self.char_edits + other.char_edits,
            self.ref_chars + other.ref_chars,
        )

    @property
    def wer(self) -> float | None:
        return self.word_edits / self.ref_words if self.ref_words else None

    @property
    def cer(self) -> float | None:
        return self.char_edits / self.ref_chars if self.ref_chars else None


def score_pair(reference: str, hypothesis: str, language: str = "fa") -> ErrorCounts:
    ref = normalize_for_matching(reference, language)
    hyp = normalize_for_matching(hypothesis, language)
    ref_w, hyp_w = ref.split(), hyp.split()
    ref_c, hyp_c = ref.replace(" ", ""), hyp.replace(" ", "")
    return ErrorCounts(levenshtein(ref_w, hyp_w), len(ref_w), levenshtein(ref_c, hyp_c), len(ref_c))


def wer(reference: str, hypothesis: str, language: str = "fa") -> float | None:
    return score_pair(reference, hypothesis, language).wer


def cer(reference: str, hypothesis: str, language: str = "fa") -> float | None:
    return score_pair(reference, hypothesis, language).cer


@dataclass(frozen=True)
class Utterance:
    reference: str
    hypothesis: str
    language: str = "fa"
    slices: dict[str, str] = field(
        default_factory=dict
    )  # e.g. {"dialect": "...", "noise": "snr_0_10", "channel": "tel_8k"}


@dataclass(frozen=True)
class SliceResult:
    key: str  # "all" or "dimension=value"
    n_utterances: int
    counts: ErrorCounts


def aggregate(
    utterances: Iterable[Utterance], dimensions: Sequence[str] = ("dialect", "noise", "channel")
) -> dict[str, SliceResult]:
    """Corpus-level totals for ``all`` and each ``dimension=value`` slice.
    Utterances with an empty normalised reference are skipped (not scorable)."""
    buckets: dict[str, tuple[int, ErrorCounts]] = {}

    def add(key: str, c: ErrorCounts) -> None:
        n, acc = buckets.get(key, (0, ErrorCounts(0, 0, 0, 0)))
        buckets[key] = (n + 1, acc + c)

    for u in utterances:
        c = score_pair(u.reference, u.hypothesis, u.language)
        if c.ref_words == 0:
            continue
        add("all", c)
        for dim in dimensions:
            if dim in u.slices:
                add(f"{dim}={u.slices[dim]}", c)
    return {k: SliceResult(k, n, c) for k, (n, c) in sorted(buckets.items())}


def noise_band(snr_db: float | None) -> str:
    if snr_db is None:
        return "snr_unknown"
    if snr_db < 0:
        return "snr_lt_0"
    if snr_db < 10:
        return "snr_0_10"
    if snr_db < 20:
        return "snr_10_20"
    return "snr_ge_20"


class GateVerdict(str, enum.Enum):
    PASS = "PASS"  # noqa: S105 - verdict label, not a secret
    FAIL = "FAIL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class QualificationRecord:
    model_id: str
    dataset_id: str
    language: str
    slice_key: str
    n_utterances: int
    wer: float | None
    cer: float | None
    status: GateVerdict = GateVerdict.INSUFFICIENT_DATA
    notes: str = ""


@dataclass(frozen=True)
class Thresholds:
    max_wer: float | None = None
    max_cer: float | None = None
    min_utterances: int = 200
    min_ref_words: int = 1000

    def __post_init__(self) -> None:
        if self.max_wer is None and self.max_cer is None:
            raise ValueError("at least one of max_wer/max_cer is required")
        if self.min_utterances < 1 or self.min_ref_words < 1:
            raise ValueError("minimums must be positive")


def gate(result: SliceResult | None, thresholds: Thresholds) -> GateVerdict:
    if result is None or result.n_utterances < thresholds.min_utterances:
        return GateVerdict.INSUFFICIENT_DATA
    c = result.counts
    if c.ref_words < thresholds.min_ref_words:
        return GateVerdict.INSUFFICIENT_DATA
    if thresholds.max_wer is not None and (c.wer is None or c.wer > thresholds.max_wer):
        return GateVerdict.FAIL
    if thresholds.max_cer is not None and (c.cer is None or c.cer > thresholds.max_cer):
        return GateVerdict.FAIL
    return GateVerdict.PASS


def qualify(
    model_id: str,
    dataset_id: str,
    language: str,
    results: dict[str, SliceResult],
    thresholds: Thresholds,
    slice_key: str = "all",
) -> QualificationRecord:
    res = results.get(slice_key)
    verdict = gate(res, thresholds)
    return QualificationRecord(
        model_id, dataset_id, language, slice_key,
        res.n_utterances if res else 0,
        res.counts.wer if res else None,
        res.counts.cer if res else None,
        verdict,
    )  # fmt: skip
