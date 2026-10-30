"""Intent classification and entity extraction scores (HOM-69)."""

from __future__ import annotations

import json

import pytest

from backend.evaluation import (
    EntityItem,
    EntityMention,
    LabeledItem,
    evaluate_classification,
    evaluate_entities,
)

ITEMS = [
    LabeledItem("a", "a"),  # tp a
    LabeledItem("a", "b"),  # fn a, fp b
    LabeledItem("b", "b"),  # tp b
    LabeledItem("a", None),  # fn a, abstained
    LabeledItem(None, None),  # correct abstention
    LabeledItem(None, "a"),  # fp a
    LabeledItem("b", "z"),  # fn b, out of taxonomy
]


def test_per_class_stats_with_given_labels() -> None:
    report = evaluate_classification(ITEMS, labels=["a", "b", "c"])
    assert list(report.per_class) == ["a", "b", "c"]
    a, b, c = report.per_class["a"], report.per_class["b"], report.per_class["c"]
    assert (a.tp, a.fp, a.fn, a.support) == (1, 1, 2, 3)
    assert a.precision == pytest.approx(0.5)
    assert a.recall == pytest.approx(1 / 3)
    assert a.f1 == pytest.approx(2 / 5)
    assert (b.tp, b.fp, b.fn, b.support) == (1, 1, 1, 2)
    assert (b.precision, b.recall, b.f1) == (0.5, 0.5, 0.5)
    # A class with no support and no predictions has nothing defined.
    assert (c.tp, c.fp, c.fn, c.support) == (0, 0, 0, 0)
    assert (c.precision, c.recall, c.f1) == (None, None, None)
    assert report.n_out_of_taxonomy == 1


def test_summary_metrics() -> None:
    m = evaluate_classification(ITEMS, labels=["a", "b", "c"]).metrics
    macro = m["macro_f1"]
    assert macro.value == pytest.approx(0.45)
    assert macro.numerator == pytest.approx(0.9)
    assert macro.denominator == 2
    assert macro.n_excluded == 1
    acc = m["accuracy"]
    assert (acc.numerator, acc.denominator) == (2, 5)
    assert acc.value == pytest.approx(0.4)
    abst = m["abstention_rate"]
    assert (abst.numerator, abst.denominator, abst.n_abstained) == (2, 7, 2)
    assert abst.value == pytest.approx(2 / 7)
    correct = m["correct_abstention_rate"]
    assert (correct.numerator, correct.denominator) == (1, 2)
    assert correct.value == pytest.approx(0.5)


def test_labels_default_to_sorted_union() -> None:
    report = evaluate_classification(ITEMS)
    assert list(report.per_class) == ["a", "b", "z"]
    z = report.per_class["z"]
    assert (z.tp, z.fp, z.fn) == (0, 1, 0)
    assert z.precision == 0.0
    assert z.recall is None
    assert z.f1 == 0.0
    assert report.n_out_of_taxonomy == 0


def test_gold_outside_labels_is_refused() -> None:
    with pytest.raises(ValueError):
        evaluate_classification([LabeledItem("q", "a")], labels=["a"])


def test_predicted_outside_labels_is_counted() -> None:
    report = evaluate_classification([LabeledItem("a", "q")], labels=["a"])
    assert report.n_out_of_taxonomy == 1
    assert report.per_class["a"].fn == 1
    assert report.metrics["accuracy"].value == 0.0


def test_no_abstention_fixtures() -> None:
    m = evaluate_classification([LabeledItem("a", "a")]).metrics
    assert m["correct_abstention_rate"].value is None
    assert m["correct_abstention_rate"].reason == "no_abstention_fixtures"
    assert m["abstention_rate"].value == 0.0


def test_only_abstention_fixtures() -> None:
    m = evaluate_classification([LabeledItem(None, None)]).metrics
    assert m["accuracy"].value is None
    assert m["accuracy"].reason == "no_gold_labels"
    assert m["macro_f1"].value is None
    assert m["macro_f1"].reason == "no_labels"
    assert m["correct_abstention_rate"].value == 1.0


def test_empty_classification() -> None:
    report = evaluate_classification([])
    for m in report.metrics.values():
        assert m.value is None
        assert m.reason
    assert report.per_class == {}
    json.dumps(report.as_dict())


def test_classification_as_dict() -> None:
    d = evaluate_classification(ITEMS, labels=["a", "b", "c"]).as_dict()
    json.dumps(d)
    assert d["per_class"]["c"]["f1"] is None
    assert d["n_out_of_taxonomy"] == 1


# Entities


def M(t: str, v: str, start: int | None = None, end: int | None = None) -> EntityMention:
    return EntityMention(t, v, start, end)


ENTITY_ITEM = EntityItem(
    gold=[M("PER", "Ali"), M("PER", "Ali"), M("LOC", "Tehran")],
    predicted=[M("PER", "Ali"), M("LOC", "tehran"), M("ORG", "X")],
)


def test_entity_value_match() -> None:
    report = evaluate_entities([ENTITY_ITEM])
    assert list(report.per_type) == ["LOC", "ORG", "PER"]
    per = report.per_type["PER"]
    assert (per.tp, per.fp, per.fn) == (1, 0, 1)
    assert per.recall == pytest.approx(0.5)
    loc = report.per_type["LOC"]
    assert (loc.tp, loc.fp, loc.fn) == (0, 1, 1)
    org = report.per_type["ORG"]
    assert (org.tp, org.fp, org.fn, org.support) == (0, 1, 0, 0)
    m = report.metrics
    assert (m["precision"].numerator, m["precision"].denominator) == (1, 3)
    assert (m["recall"].numerator, m["recall"].denominator) == (1, 3)
    assert (m["f1"].numerator, m["f1"].denominator) == (2, 6)
    assert m["f1"].value == pytest.approx(1 / 3)
    assert m["f1"].n_items == 1


def test_entity_normalize_callable() -> None:
    report = evaluate_entities([ENTITY_ITEM], normalize=str.casefold)
    assert report.per_type["LOC"].tp == 1
    assert report.metrics["precision"].value == pytest.approx(2 / 3)
    assert report.metrics["recall"].value == pytest.approx(2 / 3)
    assert report.metrics["f1"].value == pytest.approx(2 / 3)


def test_entity_duplicates_are_a_multiset() -> None:
    it = EntityItem([M("PER", "Ali")], [M("PER", "Ali"), M("PER", "Ali"), M("PER", "Ali")])
    per = evaluate_entities([it]).per_type["PER"]
    assert (per.tp, per.fp, per.fn) == (1, 2, 0)


def test_entities_do_not_match_across_items() -> None:
    items = [EntityItem([M("PER", "Ali")], []), EntityItem([], [M("PER", "Ali")])]
    per = evaluate_entities(items).per_type["PER"]
    assert (per.tp, per.fp, per.fn) == (0, 1, 1)


def test_entity_span_match_ignores_values() -> None:
    it = EntityItem(
        gold=[M("PER", "Ali", 0, 3), M("LOC", "Tehran", 5, 11)],
        predicted=[M("PER", "Alı", 0, 3), M("LOC", "Tehran", 5, 10)],
    )
    report = evaluate_entities([it], match="span")
    assert report.per_type["PER"].tp == 1
    loc = report.per_type["LOC"]
    assert (loc.tp, loc.fp, loc.fn) == (0, 1, 1)
    assert report.metrics["f1"].value == pytest.approx(0.5)


def test_entity_span_match_refuses_missing_offsets() -> None:
    it = EntityItem([M("PER", "Ali", None, 3)], [])
    with pytest.raises(ValueError):
        evaluate_entities([it], match="span")
    # The same mention is fine with value matching.
    assert evaluate_entities([it], match="value").per_type["PER"].fn == 1


def test_entity_unknown_match_is_refused() -> None:
    with pytest.raises(ValueError):
        evaluate_entities([], match="fuzzy")


def test_entities_empty() -> None:
    for items in ([], [EntityItem([], [])]):
        report = evaluate_entities(items)
        for m in report.metrics.values():
            assert m.value is None
            assert m.reason == "no_entities"
        json.dumps(report.as_dict())
