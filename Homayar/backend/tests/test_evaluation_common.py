"""Common evaluation types, slicing and the public import surface (HOM-69)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

import backend.evaluation as ev
from backend.evaluation import EVALUATION_VERSION, MetricValue, evaluate_by_slice
from backend.evaluation.common import ratio

PUBLIC_NAMES = [
    "EVALUATION_VERSION", "MetricValue", "evaluate_by_slice",
    "Utterance", "AsrReport", "evaluate_asr",
    "SpeakerTurn", "DiarizationItem", "DiarizationScore", "DiarizationReport",
    "score_diarization", "evaluate_diarization",
    "LabeledItem", "ClassStats", "ClassificationReport", "evaluate_classification",
    "EntityMention", "EntityItem", "EntityReport", "evaluate_entities",
    "RatingPair", "ScorePair", "cohen_kappa", "kappa_by_criterion", "score_mae",
    "SupportLabel", "Citation", "AuditedClaim", "FactualSupportReport",
    "citation_is_valid", "evaluate_factual_support",
    "RetrievalQuery", "RetrievalReport", "evaluate_retrieval",
    "KINDS", "parse_record", "load_jsonl",
]  # fmt: skip


@dataclass(frozen=True)
class _Item:
    value: int
    slices: dict[str, str] = field(default_factory=dict)


def _values(items: list[_Item]) -> list[int]:
    return [i.value for i in items]


def test_every_public_name_imports() -> None:
    for name in PUBLIC_NAMES:
        assert hasattr(ev, name), name
        assert name in ev.__all__, name
    assert EVALUATION_VERSION == "evaluation/1"


def test_utterance_is_the_benchmark_type() -> None:
    from backend.speech.benchmark import Utterance

    assert ev.Utterance is Utterance


def test_metric_value_as_dict_is_json_safe() -> None:
    m = MetricValue("x", 0.5, 1, 2, n_items=2)
    d = m.as_dict()
    assert d == {
        "name": "x", "value": 0.5, "numerator": 1, "denominator": 2,
        "n_items": 2, "n_excluded": 0, "n_abstained": 0, "reason": None,
    }  # fmt: skip
    json.dumps(d)


def test_evaluate_by_slice_groups_in_input_order() -> None:
    items = [
        _Item(1, {"dialect": "tehrani", "noise": "low"}),
        _Item(2, {"dialect": "shirazi"}),
        _Item(3, {}),
        _Item(4, {"dialect": "tehrani", "channel": "tel"}),
    ]
    out = evaluate_by_slice(items, _values, ["dialect", "noise"])
    assert out == {
        "all": [1, 2, 3, 4],
        "dialect=shirazi": [2],
        "dialect=tehrani": [1, 4],
        "noise=low": [1],
    }
    assert next(iter(out)) == "all"


def test_evaluate_by_slice_item_without_dimension_is_only_in_all() -> None:
    out = evaluate_by_slice([_Item(7)], _values, ["dialect"])
    assert out == {"all": [7]}


def test_evaluate_by_slice_empty_input_still_has_all() -> None:
    out = evaluate_by_slice([], _values, ["dialect"])
    assert out == {"all": []}


def test_evaluate_by_slice_accepts_a_generator() -> None:
    out = evaluate_by_slice((i for i in [_Item(1, {"d": "x"})]), _values, ["d"])
    assert out == {"all": [1], "d=x": [1]}


def test_evaluate_by_slice_with_a_real_evaluator() -> None:
    items = [
        ev.LabeledItem("a", "a", slices={"channel": "tel"}),
        ev.LabeledItem("a", "b", slices={"channel": "web"}),
    ]
    out = evaluate_by_slice(items, ev.evaluate_classification, ["channel"])
    assert out["all"].metrics["accuracy"].value == pytest.approx(0.5)
    assert out["channel=tel"].metrics["accuracy"].value == pytest.approx(1.0)
    assert out["channel=web"].metrics["accuracy"].value == pytest.approx(0.0)


def test_every_evaluator_gives_none_with_reason_on_empty_input() -> None:
    reports = [
        ev.evaluate_asr([]),
        ev.evaluate_diarization([]),
        ev.evaluate_classification([]),
        ev.evaluate_entities([]),
        ev.evaluate_factual_support([]),
        ev.evaluate_retrieval([]),
    ]
    metrics: list[MetricValue] = [m for r in reports for m in r.metrics.values()]
    metrics += [ev.cohen_kappa([]), ev.score_mae([])]
    assert metrics
    for m in metrics:
        assert m.value is None, m.name
        assert isinstance(m.reason, str) and m.reason, m.name
    for r in reports:
        json.dumps(r.as_dict())
    assert ev.kappa_by_criterion([]) == {}


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(float("inf"), 1), (1, float("inf")), (float("nan"), 2), (1e308, 1e-10), (float("inf"), 0)],
)
def test_ratio_reports_non_finite_results_as_none(numerator: float, denominator: float) -> None:
    # Turn 3 (SPEC 14): one central rule for every metric.
    m = ratio("x", numerator, denominator, n_items=1, reason="empty")
    assert (m.value, m.numerator, m.denominator) == (None, None, None)
    assert m.reason == "non_finite_result"
    json.dumps(m.as_dict(), allow_nan=False)


def test_ratio_keeps_finite_and_empty_cases() -> None:
    assert ratio("x", 1e308, 1, n_items=1, reason="empty").value == 1e308
    empty = ratio("x", 0, 0, n_items=0, reason="empty")
    assert (empty.value, empty.reason) == (None, "empty")


def test_ratio_int_too_large_for_a_float() -> None:
    # Turn 4: math.isfinite and / raise OverflowError on such ints.
    for numerator, denominator in ((10**400, 1), (1, 10**400), (10**400, 10**401)):
        m = ratio("x", numerator, denominator, n_items=1, reason="empty")
        assert (m.value, m.numerator, m.denominator) == (None, None, None)
        assert m.reason == "non_finite_result"
