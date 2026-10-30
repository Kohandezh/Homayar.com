"""Corpus WER/CER through backend.speech.benchmark (HOM-69)."""

from __future__ import annotations

import json

import pytest

from backend.evaluation import Utterance, evaluate_asr
from backend.speech.benchmark import ErrorCounts, score_pair


def test_corpus_wer_cer_are_sums_not_means() -> None:
    # "the cat sat" / "the cat sit": 1 of 3 words, 1 of 9 chars.
    # "hello world" / "hello": 1 of 2 words, 5 of 10 chars.
    report = evaluate_asr([
        Utterance("The cat sat", "the cat sit", "en"),
        Utterance("hello world", "hello", "en"),
    ])  # fmt: skip
    wer, cer = report.metrics["wer"], report.metrics["cer"]
    assert (wer.numerator, wer.denominator) == (2, 5)
    assert wer.value == pytest.approx(2 / 5)
    assert (cer.numerator, cer.denominator) == (6, 19)
    assert cer.value == pytest.approx(6 / 19)
    assert wer.n_items == cer.n_items == 2
    assert wer.n_excluded == 0
    assert report.counts == ErrorCounts(2, 5, 6, 19)


def test_matches_the_sum_of_score_pair() -> None:
    pairs = [("سلام دنیا", "سلام"), ("امروز هوا خوب است", "امروز هوا بد است")]
    total = ErrorCounts(0, 0, 0, 0)
    for ref, hyp in pairs:
        total = total + score_pair(ref, hyp, "fa")
    report = evaluate_asr([Utterance(r, h) for r, h in pairs])
    assert report.counts == total
    assert report.metrics["wer"].value == pytest.approx(total.wer)
    assert report.metrics["cer"].value == pytest.approx(total.cer)


def test_persian_orthographic_variants_are_zero_edits() -> None:
    # Arabic yeh vs Persian yeh, Persian digits vs ASCII digits.
    report = evaluate_asr([Utterance("سلام علي ۱۲۳", "سلام علی 123", "fa")])
    assert report.metrics["wer"].value == 0.0
    assert report.metrics["cer"].value == 0.0
    assert report.metrics["wer"].numerator == 0
    assert report.metrics["wer"].denominator == 3


def test_empty_reference_is_excluded_and_adds_nothing() -> None:
    report = evaluate_asr([
        Utterance("hello world", "hello world", "en"),
        Utterance("", "noise words", "en"),
        Utterance(" ... ", "x", "en"),
    ])  # fmt: skip
    wer = report.metrics["wer"]
    assert wer.n_items == 1
    assert wer.n_excluded == 2
    assert (wer.numerator, wer.denominator) == (0, 2)
    assert report.counts == ErrorCounts(0, 2, 0, 10)


def test_all_excluded_gives_none() -> None:
    report = evaluate_asr([Utterance("", "x", "en")])
    for name in ("wer", "cer"):
        m = report.metrics[name]
        assert m.value is None
        assert m.reason == "no_reference_words"
        assert m.n_excluded == 1
        assert m.n_items == 0


def test_empty_input_gives_none() -> None:
    report = evaluate_asr([])
    assert report.metrics["wer"].value is None
    assert report.metrics["wer"].reason == "no_reference_words"
    assert report.metrics["cer"].reason == "no_reference_words"
    assert report.counts == ErrorCounts(0, 0, 0, 0)


def test_as_dict_is_json_safe() -> None:
    d = evaluate_asr([Utterance("a b", "a", "en")]).as_dict()
    json.dumps(d)
    assert d["counts"] == {"word_edits": 1, "ref_words": 2, "char_edits": 1, "ref_chars": 2}
    assert d["metrics"]["wer"]["value"] == pytest.approx(0.5)
