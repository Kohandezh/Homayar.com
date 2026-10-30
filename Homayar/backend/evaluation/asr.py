"""Corpus WER and CER (HOM-69).

A thin layer over :mod:`backend.speech.benchmark`: each utterance is scored with
``score_pair`` (same normaliser, same Levenshtein), and the rates are the sums of edits
over the sums of reference lengths.  An utterance whose normalised reference has no words
is not scorable and adds nothing, as in ``benchmark.aggregate``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from backend.evaluation.common import MetricValue, metrics_dict, ratio
from backend.speech.benchmark import ErrorCounts, Utterance, score_pair


@dataclass(frozen=True)
class AsrReport:
    metrics: dict[str, MetricValue]  # "wer", "cer"
    counts: ErrorCounts  # sums over scored utterances

    def as_dict(self) -> dict[str, Any]:
        return {"metrics": metrics_dict(self.metrics), "counts": asdict(self.counts)}


def evaluate_asr(utterances: Iterable[Utterance]) -> AsrReport:
    total = ErrorCounts(0, 0, 0, 0)
    scored = excluded = 0
    for u in utterances:
        c = score_pair(u.reference, u.hypothesis, u.language)
        if c.ref_words == 0:
            excluded += 1
            continue
        scored += 1
        total = total + c
    return AsrReport(
        metrics={
            "wer": ratio(
                "wer",
                total.word_edits,
                total.ref_words,
                n_items=scored,
                n_excluded=excluded,
                reason="no_reference_words",
            ),
            "cer": ratio(
                "cer",
                total.char_edits,
                total.ref_chars,
                n_items=scored,
                n_excluded=excluded,
                reason="no_reference_words",
            ),
        },  # fmt: skip
        counts=total,
    )
