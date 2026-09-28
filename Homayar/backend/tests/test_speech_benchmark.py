"""WER/CER, slice aggregation, qualification gate and language registry."""

from __future__ import annotations

import pytest

from backend.speech.benchmark import (
    ErrorCounts,
    GateVerdict,
    SliceResult,
    Thresholds,
    Utterance,
    aggregate,
    cer,
    gate,
    levenshtein,
    noise_band,
    qualify,
    score_pair,
    wer,
)
from backend.speech.languages import (
    DateSupport,
    QualificationStatus,
    get_language,
    list_languages,
    record_qualification,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_levenshtein() -> None:
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein([], ["a"]) == 1
    assert levenshtein(["a", "b"], ["a", "b"]) == 0


def test_wer_basic_persian() -> None:
    assert wer("من فردا می‌آیم", "من فردا می‌آیم") == 0
    assert wer("من فردا می‌آیم", "من پس فردا می‌آیم") == pytest.approx(1 / 3)
    assert wer("سلام خوبی", "") == 1


def test_orthographic_variants_are_not_errors() -> None:
    assert wer("كتاب‌هاي علي ۲ تا", "کتاب های علی 2 تا") == 0
    assert cer("مي‌خواهم", "میخواهم") == 0


def test_cer() -> None:
    assert cer("سلام", "سلم") == pytest.approx(1 / 4)
    assert cer("", "abc", "en") is None


def test_empty_reference_not_scorable() -> None:
    assert wer("", "abc", "en") is None


def test_aggregate_is_corpus_level_and_sliced() -> None:
    utts = [
        Utterance(
            "من فردا می‌آیم",
            "من فردا می‌آیم",
            slices={"dialect": "fa-IR-x-tehrani", "channel": "tel_8k"},
        ),
        Utterance(
            "ساعت ده صبح",
            "ساعت دو صبح",
            slices={"dialect": "fa-IR-x-mashhadi", "channel": "tel_8k"},
        ),
        Utterance("", "noise", slices={"dialect": "fa-IR-x-tehrani"}),
    ]
    res = aggregate(utts)
    assert res["all"].n_utterances == 2
    assert res["all"].counts.wer == pytest.approx(1 / 6)
    assert res["dialect=fa-IR-x-mashhadi"].counts.wer == pytest.approx(1 / 3)
    assert res["channel=tel_8k"].n_utterances == 2
    assert "noise=" not in " ".join(res)


@pytest.mark.parametrize(
    ("snr", "band"),
    [
        (None, "snr_unknown"),
        (-3, "snr_lt_0"),
        (5, "snr_0_10"),
        (15, "snr_10_20"),
        (30, "snr_ge_20"),
    ],
)
def test_noise_band(snr, band) -> None:
    assert noise_band(snr) == band


def _result(n: int, word_edits: int, ref_words: int) -> SliceResult:
    return SliceResult("all", n, ErrorCounts(word_edits, ref_words, word_edits, ref_words * 4))


def test_gate_never_passes_without_data() -> None:
    t = Thresholds(max_wer=0.2, min_utterances=100, min_ref_words=500)
    assert gate(None, t) is GateVerdict.INSUFFICIENT_DATA
    assert gate(_result(0, 0, 0), t) is GateVerdict.INSUFFICIENT_DATA
    assert gate(_result(99, 0, 5000), t) is GateVerdict.INSUFFICIENT_DATA
    assert gate(_result(100, 0, 499), t) is GateVerdict.INSUFFICIENT_DATA


def test_gate_pass_fail() -> None:
    t = Thresholds(max_wer=0.2, max_cer=0.1, min_utterances=10, min_ref_words=100)
    assert gate(_result(10, 20, 100), t) is GateVerdict.PASS
    assert gate(_result(10, 21, 100), t) is GateVerdict.FAIL
    assert gate(SliceResult("all", 10, ErrorCounts(0, 100, 50, 100)), t) is GateVerdict.FAIL


def test_thresholds_require_a_metric() -> None:
    with pytest.raises(ValueError):
        Thresholds()


def test_qualify_record() -> None:
    utts = [Utterance("یک دو سه", "یک دو سه")] * 3
    rec = qualify(
        "model-x",
        "synthetic-v0",
        "fa",
        aggregate(utts),
        Thresholds(max_wer=0.3, min_utterances=200),
    )
    assert rec.status is GateVerdict.INSUFFICIENT_DATA
    assert rec.n_utterances == 3 and rec.wer == 0
    missing = qualify("m", "d", "fa", {}, Thresholds(max_wer=0.3), slice_key="dialect=fa-AF")
    assert missing.status is GateVerdict.INSUFFICIENT_DATA and missing.wer is None


def test_languages_default_planned() -> None:
    codes = {lang.code for lang in list_languages()}
    assert codes == {"az", "en", "fa"}
    for lang in list_languages():
        assert lang.asr_status is QualificationStatus.PLANNED
        assert lang.evidence == ()
        assert all(d.status is QualificationStatus.PLANNED for d in lang.dialects)
    fa = get_language("fa-IR")
    assert fa.date_support is DateSupport.NATURAL
    assert {"fa-AF", "fa-IR-x-isfahani", "fa-IR-x-mashhadi", "fa-IR-x-tehrani"} <= {
        d.tag for d in fa.dialects
    }
    assert get_language("az").scripts == ("Latn", "Arab")


def test_record_qualification_requires_pass() -> None:
    rec = qualify("m", "d", "fa", {}, Thresholds(max_wer=0.2))
    lang = record_qualification("fa", rec, rec.status)
    assert lang.asr_status is QualificationStatus.IN_EVALUATION
    passing = SliceResult("all", 300, ErrorCounts(100, 2000, 300, 8000))
    t = Thresholds(max_wer=0.2)
    rec2 = qualify("m", "d", "fa", {"all": passing}, t)
    assert rec2.status is GateVerdict.PASS
    lang = record_qualification("fa", rec2, rec2.status)
    assert lang.asr_status is QualificationStatus.QUALIFIED and len(lang.evidence) == 2
    lang = record_qualification("fa", rec2, GateVerdict.FAIL, dialect_tag="fa-AF")
    assert (
        next(d for d in lang.dialects if d.tag == "fa-AF").status
        is QualificationStatus.IN_EVALUATION
    )
    with pytest.raises(KeyError):
        record_qualification("fa", rec2, rec2.status, dialect_tag="fa-XX")


def test_score_pair_counts() -> None:
    c = score_pair("hello world", "hello word", "en")
    assert (c.word_edits, c.ref_words, c.char_edits, c.ref_chars) == (1, 2, 1, 10)
