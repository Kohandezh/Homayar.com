"""Minimum sample thresholds, cells, rounding and comparisons (HOM-79, D4 and D5)."""

from __future__ import annotations

import dataclasses
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import pytest

from backend.issue_trends import (
    ASSOCIATION_NOTICE,
    RATIO_PLACES,
    SAMPLE_POLICY_V1,
    Cell,
    CellKind,
    CellReason,
    CellStatus,
    SamplePolicy,
    compare_cells,
    sample_cell,
    sample_policy_digest,
    unavailable_cell,
)


def _half_up(value: Fraction) -> float:
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return float(exact.quantize(Decimal(1).scaleb(-RATIO_PLACES), rounding=ROUND_HALF_UP))


def test_policy_v1_numbers() -> None:
    assert SAMPLE_POLICY_V1 == SamplePolicy(
        version="trend-sample-policy/1",
        min_denominator_rate=30,
        min_denominator_agent=30,
        min_denominator_comparison=50,
        hide_agent_counts_below_minimum=True,
    )
    assert RATIO_PLACES == 4
    assert ASSOCIATION_NOTICE == "correlation_not_cause"


def test_policy_digest_changes_with_any_field() -> None:
    base = sample_policy_digest(SAMPLE_POLICY_V1)
    assert len(base) == 64
    for change in (
        {"version": "trend-sample-policy/2"},
        {"min_denominator_rate": 31},
        {"min_denominator_agent": 29},
        {"min_denominator_comparison": 51},
        {"hide_agent_counts_below_minimum": False},
    ):
        assert sample_policy_digest(dataclasses.replace(SAMPLE_POLICY_V1, **change)) != base


# --- one cell -------------------------------------------------------------------------------


def test_empty_denominator_is_never_insufficient_sample() -> None:
    for kind in CellKind:
        assert sample_cell(0, 0, kind=kind) == Cell(
            status=CellStatus.NOT_COMPUTABLE,
            numerator=0,
            denominator=0,
            ratio=None,
            reason=CellReason.EMPTY_DENOMINATOR,
            minimum_sample=30,
        )


def test_below_the_minimum_keeps_rate_counts_visible() -> None:
    assert sample_cell(4, 29) == Cell(
        status=CellStatus.INSUFFICIENT_SAMPLE,
        numerator=4,
        denominator=29,
        ratio=None,
        reason=CellReason.BELOW_MINIMUM_SAMPLE,
        minimum_sample=30,
    )
    assert sample_cell(0, 1).numerator == 0


def test_below_the_minimum_hides_an_agent_numerator() -> None:
    cell = sample_cell(1, 4, kind=CellKind.AGENT)
    assert cell.status is CellStatus.INSUFFICIENT_SAMPLE
    assert cell.numerator is None
    assert cell.denominator == 4
    shown = sample_cell(
        1,
        4,
        kind=CellKind.AGENT,
        policy=dataclasses.replace(SAMPLE_POLICY_V1, hide_agent_counts_below_minimum=False),
    )
    assert shown.numerator == 1


def test_the_boundary_passes() -> None:
    cell = sample_cell(3, 30)
    assert cell == Cell(
        status=CellStatus.COMPUTED,
        numerator=3,
        denominator=30,
        ratio=0.1,
        reason=None,
        minimum_sample=30,
    )
    agent = sample_cell(3, 30, kind=CellKind.AGENT)
    assert agent.status is CellStatus.COMPUTED and agent.numerator == 3


def test_reason_is_null_exactly_when_computed() -> None:
    for numerator, denominator in [(0, 0), (1, 5), (5, 30), (30, 30), (0, 100)]:
        cell = sample_cell(numerator, denominator)
        assert (cell.reason is None) is (cell.status is CellStatus.COMPUTED)
        assert (cell.ratio is None) is (cell.status is not CellStatus.COMPUTED)


def test_minimum_override() -> None:
    assert sample_cell(1, 40, minimum=50).status is CellStatus.INSUFFICIENT_SAMPLE
    assert sample_cell(1, 40, minimum=50).minimum_sample == 50
    assert sample_cell(1, 1, minimum=1).status is CellStatus.COMPUTED
    for bad in (0, -1, True, 1.5, "30"):
        with pytest.raises(ValueError):
            sample_cell(1, 40, minimum=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [
        (-1, 10),
        (1, -1),
        (11, 10),
        (True, 10),
        (1, True),
        (1.0, 10),
        (1, 10.0),
        ("1", 10),
        (None, 1),
    ],
)
def test_bad_counts_are_refused(numerator: object, denominator: object) -> None:
    with pytest.raises(ValueError):
        sample_cell(numerator, denominator)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "policy",
    [
        dataclasses.replace(SAMPLE_POLICY_V1, version=""),
        dataclasses.replace(SAMPLE_POLICY_V1, version="  "),
        dataclasses.replace(SAMPLE_POLICY_V1, min_denominator_rate=0),
        dataclasses.replace(SAMPLE_POLICY_V1, min_denominator_agent=True),
        dataclasses.replace(SAMPLE_POLICY_V1, min_denominator_comparison=2.5),
        dataclasses.replace(SAMPLE_POLICY_V1, hide_agent_counts_below_minimum=1),
    ],
)
def test_bad_policies_are_refused(policy: SamplePolicy) -> None:
    with pytest.raises(ValueError):
        sample_cell(1, 40, policy=policy)
    with pytest.raises(ValueError):
        compare_cells((1, 60), (1, 60), policy=policy)


def test_cell_to_json() -> None:
    assert sample_cell(14, 120).to_json() == {
        "status": "computed",
        "numerator": 14,
        "denominator": 120,
        "ratio": 0.1167,
        "reason": None,
        "minimum_sample": 30,
    }
    assert sample_cell(1, 4, kind=CellKind.AGENT).to_json() == {
        "status": "insufficient_sample",
        "numerator": None,
        "denominator": 4,
        "ratio": None,
        "reason": "below_minimum_sample",
        "minimum_sample": 30,
    }


# --- rounding -------------------------------------------------------------------------------


def test_ratio_rounds_half_up_from_the_exact_fraction() -> None:
    assert sample_cell(1, 32, minimum=1).ratio == 0.0313  # float round gives 0.0312
    assert round(1 / 32, 4) == 0.0312
    assert sample_cell(14, 96).ratio == 0.1458
    assert sample_cell(122, 130).ratio == 0.9385
    assert sample_cell(0, 104).ratio == 0.0
    assert sample_cell(104, 104).ratio == 1.0


def test_ratio_matches_decimal_half_up_for_many_fractions() -> None:
    for denominator in range(1, 400):
        for numerator in range(0, denominator + 1, max(1, denominator // 37)):
            cell = sample_cell(numerator, denominator, minimum=1)
            assert cell.ratio == _half_up(Fraction(numerator, denominator)), (
                numerator,
                denominator,
            )


def test_exact_half_cases() -> None:
    # n / d with exactly five in the fifth place
    for numerator, denominator, expected in [
        (1, 32, 0.0313),
        (3, 32, 0.0938),
        (5, 32, 0.1563),
        (1, 160, 0.0063),
        (1, 800, 0.0013),
    ]:
        assert sample_cell(numerator, denominator, minimum=1).ratio == expected


# --- unavailable source ---------------------------------------------------------------------


def test_unavailable_cell() -> None:
    cell = unavailable_cell(120, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=30)
    assert cell == Cell(
        status=CellStatus.NOT_COMPUTABLE,
        numerator=None,
        denominator=120,
        ratio=None,
        reason=CellReason.SCORECARD_RESULTS_UNAVAILABLE,
        minimum_sample=30,
    )
    empty = unavailable_cell(0, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=30)
    assert empty.reason is CellReason.SCORECARD_RESULTS_UNAVAILABLE  # wins over empty


def test_unavailable_cell_refusals() -> None:
    for reason in (CellReason.EMPTY_DENOMINATOR, CellReason.BELOW_MINIMUM_SAMPLE):
        with pytest.raises(ValueError):
            unavailable_cell(10, reason, minimum=30)
    with pytest.raises(ValueError):
        unavailable_cell(-1, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=30)
    with pytest.raises(ValueError):
        unavailable_cell(True, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=30)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        unavailable_cell(10, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=0)


# --- comparisons ----------------------------------------------------------------------------


def test_both_sides_must_pass() -> None:
    comparison = compare_cells((20, 60), (9, 40))
    assert comparison.status is CellStatus.INSUFFICIENT_SAMPLE
    assert comparison.reason is CellReason.BELOW_MINIMUM_SAMPLE
    assert comparison.difference is None
    assert comparison.left.status is CellStatus.COMPUTED
    assert comparison.left.minimum_sample == 50
    assert comparison.right == Cell(
        status=CellStatus.INSUFFICIENT_SAMPLE,
        numerator=9,
        denominator=40,
        ratio=None,
        reason=CellReason.BELOW_MINIMUM_SAMPLE,
        minimum_sample=50,
    )


def test_a_computed_comparison() -> None:
    comparison = compare_cells((30, 60), (20, 50))
    assert comparison.status is CellStatus.COMPUTED
    assert comparison.reason is None
    assert comparison.difference == 0.1
    assert compare_cells((20, 50), (30, 60)).difference == -0.1


def test_difference_is_exact_and_rounds_half_up_away_from_zero() -> None:
    # 33/64 - 31/64 = 1/32
    assert compare_cells((33, 64), (31, 64)).difference == 0.0313
    assert compare_cells((31, 64), (33, 64)).difference == -0.0313
    # the float difference of the rounded ratios would differ
    for left, right in [((1, 3), (1, 7)), ((2, 3), (1, 3)), ((17, 51), (50, 99))]:
        if left[1] < 50 or right[1] < 50:
            left = (left[0] * 50, left[1] * 50)
            right = (right[0] * 50, right[1] * 50)
        expected = _half_up(Fraction(*left) - Fraction(*right))
        assert compare_cells(left, right).difference == expected


def test_comparison_status_precedence() -> None:
    empty_left = compare_cells((0, 0), (1, 10))
    assert empty_left.status is CellStatus.NOT_COMPUTABLE
    assert empty_left.reason is CellReason.EMPTY_DENOMINATOR
    empty_right = compare_cells((1, 10), (0, 0))
    assert empty_right.status is CellStatus.NOT_COMPUTABLE
    assert empty_right.reason is CellReason.EMPTY_DENOMINATOR
    both_small = compare_cells((1, 10), (1, 20))
    assert both_small.status is CellStatus.INSUFFICIENT_SAMPLE
    assert both_small.left.denominator == 10


def test_comparison_with_agent_sides() -> None:
    agent_policy = dataclasses.replace(SAMPLE_POLICY_V1, min_denominator_agent=80)
    comparison = compare_cells((10, 60), (10, 60), left_kind=CellKind.AGENT, policy=agent_policy)
    assert comparison.left.minimum_sample == 80
    assert comparison.left.status is CellStatus.INSUFFICIENT_SAMPLE
    assert comparison.left.numerator is None
    assert comparison.right.minimum_sample == 50
    assert comparison.status is CellStatus.INSUFFICIENT_SAMPLE


def test_comparison_to_json() -> None:
    assert compare_cells((30, 60), (20, 50)).to_json() == {
        "status": "computed",
        "reason": None,
        "left": sample_cell(30, 60, minimum=50).to_json(),
        "right": sample_cell(20, 50, minimum=50).to_json(),
        "difference": 0.1,
    }


def test_comparison_refuses_bad_counts() -> None:
    with pytest.raises(ValueError):
        compare_cells((5, 4), (1, 60))
    with pytest.raises(ValueError):
        compare_cells((1, 60), (-1, 60))


# --- correlation is not cause ---------------------------------------------------------------


def test_no_cause_word_in_any_status_reason_or_field() -> None:
    import backend.issue_trends as package

    words: set[str] = set()
    for name in package.__all__:
        value = getattr(package, name)
        words.add(name.lower())
        if isinstance(value, type) and hasattr(value, "__members__"):
            words.update(str(member.value).lower() for member in value)  # type: ignore[attr-defined]
        if dataclasses.is_dataclass(value):
            words.update(field.name.lower() for field in dataclasses.fields(value))
    for word in words:
        if word == ASSOCIATION_NOTICE:
            continue
        for bad in ("cause", "causal", "driver", "confirmed_cause", "root_cause"):
            assert bad not in word, word
