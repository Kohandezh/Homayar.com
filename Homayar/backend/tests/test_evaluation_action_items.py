"""Action item evaluator (HOM-133, ADR 0058)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from backend.evaluation import ActionItemCase, evaluate_action_items

D1 = date(2026, 11, 13)
D2 = date(2026, 11, 14)


def _case(**fields) -> ActionItemCase:
    base = {
        "gold_keep": True,
        "gold_owner": None,
        "gold_due": None,
        "gold_kind": None,
        "predicted_keep": True,
        "predicted_owner": None,
        "predicted_due": None,
        "predicted_kind": None,
    }
    base.update(fields)
    return ActionItemCase(**base)


METRICS = (
    "owner_precision",
    "due_precision",
    "owner_unset_rate",
    "due_unset_rate",
    "kind_accuracy",
    "keep_precision",
)


def test_empty_input_gives_none_with_reasons() -> None:
    report = evaluate_action_items([])
    assert set(report.metrics) == set(METRICS)
    for name in METRICS:
        metric = report.metrics[name]
        assert metric.value is None, name
        assert metric.reason, name
    json.dumps(report.as_dict())


def test_owner_precision_compares_normalized_names() -> None:
    report = evaluate_action_items(
        [
            _case(item_id="a", gold_owner="علی", predicted_owner="علي"),  # Arabic yeh
            _case(item_id="b", gold_owner="علی", predicted_owner="مریم"),
            _case(item_id="c", gold_owner=None, predicted_owner="رضا"),
            _case(item_id="d", gold_owner="سارا", predicted_owner=None),
        ]
    )
    owner = report.metrics["owner_precision"]
    assert (owner.numerator, owner.denominator, owner.value) == (1, 3, pytest.approx(1 / 3))
    assert owner.n_abstained == 1
    assert report.wrong_owner_ids == ("b", "c")


def test_due_precision_and_unset_rate() -> None:
    report = evaluate_action_items(
        [
            _case(item_id="a", gold_due=D1, predicted_due=D1),
            _case(item_id="b", gold_due=D1, predicted_due=D2),
            _case(item_id="c", gold_due=None, predicted_due=None),
            _case(item_id="d", gold_due=None, predicted_due=D1),
        ]
    )
    due = report.metrics["due_precision"]
    assert (due.numerator, due.denominator) == (1, 3)
    unset = report.metrics["due_unset_rate"]
    assert (unset.numerator, unset.denominator, unset.value) == (1, 2, 0.5)
    assert report.wrong_due_ids == ("b", "d")


def test_owner_unset_rate() -> None:
    report = evaluate_action_items(
        [
            _case(gold_owner=None, predicted_owner=None),
            _case(gold_owner=None, predicted_owner=None),
            _case(gold_owner="علی", predicted_owner=None),
        ]
    )
    unset = report.metrics["owner_unset_rate"]
    assert (unset.numerator, unset.denominator, unset.value) == (2, 2, 1.0)
    assert report.metrics["owner_precision"].value is None
    assert report.metrics["owner_precision"].reason == "no_predicted_owners"


def test_owner_and_due_metrics_use_only_items_both_sides_keep() -> None:
    report = evaluate_action_items(
        [
            _case(gold_keep=False, predicted_keep=True, predicted_owner="x", predicted_due=D1),
            _case(gold_keep=True, predicted_keep=False, gold_owner=None, gold_due=None),
            _case(gold_owner="علی", predicted_owner="علی", gold_due=D1, predicted_due=D1),
        ]
    )
    assert report.metrics["owner_precision"].value == 1.0
    assert report.metrics["owner_precision"].n_excluded == 2
    assert report.metrics["due_precision"].value == 1.0
    assert report.metrics["owner_unset_rate"].reason == "no_unstated_owners"
    assert report.metrics["due_unset_rate"].reason == "no_unstated_due_dates"


def test_kind_accuracy() -> None:
    report = evaluate_action_items(
        [
            _case(gold_kind="commitment", predicted_kind="commitment"),
            _case(gold_kind="commitment", predicted_kind="next_step"),
            _case(gold_kind=None, predicted_kind="next_step"),
        ]
    )
    kind = report.metrics["kind_accuracy"]
    assert (kind.numerator, kind.denominator, kind.value) == (1, 2, 0.5)
    assert evaluate_action_items([_case()]).metrics["kind_accuracy"].reason == "no_gold_kinds"


def test_keep_precision_over_all_items() -> None:
    report = evaluate_action_items(
        [
            _case(gold_keep=True, predicted_keep=True),
            _case(gold_keep=False, predicted_keep=True),
            _case(gold_keep=False, predicted_keep=False),
            _case(gold_keep=True, predicted_keep=False),
        ]
    )
    keep = report.metrics["keep_precision"]
    assert (keep.numerator, keep.denominator, keep.value) == (1, 2, 0.5)
    none_kept = evaluate_action_items([_case(predicted_keep=False)])
    assert none_kept.metrics["keep_precision"].reason == "no_kept_items"


def test_unknown_language_does_not_raise() -> None:
    report = evaluate_action_items([_case(language="xx", gold_owner="Ali", predicted_owner="ali")])
    assert report.metrics["owner_precision"].value == 1.0
