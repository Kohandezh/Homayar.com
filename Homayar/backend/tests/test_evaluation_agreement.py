"""Cohen's kappa (plain and weighted) and score MAE (HOM-69)."""

from __future__ import annotations

import pytest

from backend.evaluation import RatingPair, ScorePair, cohen_kappa, kappa_by_criterion, score_mae


def pairs(*rows: tuple[str | None, str | None, int]) -> list[RatingPair]:
    out: list[RatingPair] = []
    for a, b, n in rows:
        out += [RatingPair(a, b)] * n
    return out


# Textbook 2x2 example: yes/yes 20, yes/no 5, no/yes 10, no/no 15.
# p_o = 0.7, p_e = 0.5 * 0.6 + 0.5 * 0.4 = 0.5, kappa = 0.4.
TEXTBOOK = pairs(("yes", "yes", 20), ("yes", "no", 5), ("no", "yes", 10), ("no", "no", 15))

# Three ordered categories. Rater A is uniform; rater B is 1/6, 3/6, 2/6.
ORDERED = ["low", "mid", "high"]
THREE = [
    RatingPair("low", "low"),
    RatingPair("low", "mid"),
    RatingPair("mid", "mid"),
    RatingPair("mid", "high"),
    RatingPair("high", "high"),
    RatingPair("high", "mid"),
]


def test_textbook_unweighted_kappa() -> None:
    k = cohen_kappa(TEXTBOOK)
    assert k.name == "kappa"
    assert k.value == pytest.approx(0.4)
    assert k.numerator == pytest.approx(0.2)
    assert k.denominator == pytest.approx(0.5)
    assert k.n_items == 50
    assert cohen_kappa(TEXTBOOK, categories=["yes", "no"]).value == pytest.approx(0.4)


def test_weighted_kappa_on_three_categories() -> None:
    # Unweighted: (2/3 - 1/2) / (2/3).
    assert cohen_kappa(THREE, categories=ORDERED).value == pytest.approx(0.25)
    # Linear: E = 5/12, O = 3/12.
    lin = cohen_kappa(THREE, categories=ORDERED, weights="linear")
    assert lin.denominator == pytest.approx(5 / 12)
    assert lin.numerator == pytest.approx(2 / 12)
    assert lin.value == pytest.approx(0.4)
    # Quadratic: E = 7/24, O = 3/24.
    quad = cohen_kappa(THREE, categories=ORDERED, weights="quadratic")
    assert quad.denominator == pytest.approx(7 / 24)
    assert quad.value == pytest.approx(4 / 7)


def test_pairs_with_a_missing_rating_are_excluded() -> None:
    k = cohen_kappa([*TEXTBOOK, RatingPair(None, "yes"), RatingPair("no", None)],
                    categories=["no", "yes"])  # fmt: skip
    assert k.value == pytest.approx(0.4)
    assert k.n_items == 50
    assert k.n_excluded == 2


def test_no_pairs() -> None:
    for given in ([], [RatingPair(None, None)]):
        k = cohen_kappa(given)
        assert k.value is None
        assert k.reason == "no_pairs"


def test_no_expected_disagreement() -> None:
    same = [RatingPair("yes", "yes")] * 3
    for k in (cohen_kappa(same), cohen_kappa(same, categories=["yes", "no"])):
        assert k.value is None
        assert k.reason == "no_expected_disagreement"
        assert k.n_items == 3


def test_kappa_by_criterion_is_sorted() -> None:
    rows = [RatingPair(p.rating_a, p.rating_b, criterion="clarity") for p in TEXTBOOK]
    rows += [RatingPair("yes", "no", criterion="accuracy"),
             RatingPair("no", "yes", criterion="accuracy")]  # fmt: skip
    out = kappa_by_criterion(rows)
    assert list(out) == ["accuracy", "clarity"]
    assert out["clarity"].value == pytest.approx(0.4)
    # Two full disagreements: p_o = 0, p_e = 0.5, kappa = -1.
    assert out["accuracy"].value == pytest.approx(-1.0)


def test_weights_need_categories() -> None:
    for w in ("linear", "quadratic"):
        with pytest.raises(ValueError):
            cohen_kappa(THREE, weights=w)
    assert cohen_kappa(THREE, weights="none").value == pytest.approx(0.25)


def test_unknown_weights_are_refused() -> None:
    with pytest.raises(ValueError):
        cohen_kappa(THREE, categories=ORDERED, weights="cubic")
    assert cohen_kappa(THREE, categories=ORDERED, weights="quadratic").value is not None


def test_rating_outside_categories_is_refused() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([RatingPair("low", "extreme")], categories=ORDERED)
    k = cohen_kappa([RatingPair("low", None)], categories=ORDERED)
    assert k.n_excluded == 1


def test_score_mae() -> None:
    m = score_mae([ScorePair(3, 4), ScorePair(5, 5), ScorePair(2, 4.5)])
    assert m.name == "score_mae"
    assert m.numerator == pytest.approx(3.5)
    assert m.denominator == 3
    assert m.value == pytest.approx(3.5 / 3)
    assert m.n_items == 3


def test_score_mae_empty() -> None:
    m = score_mae([])
    assert m.value is None
    assert m.reason == "no_pairs"


def test_kappa_by_criterion_checks_arguments_on_empty_input() -> None:
    # Turn 2, finding 4: the checks ran only inside each criterion group.
    with pytest.raises(ValueError):
        kappa_by_criterion([], weights="cubic")
    with pytest.raises(ValueError):
        kappa_by_criterion([], weights="linear")
    assert kappa_by_criterion([], categories=ORDERED, weights="linear") == {}


def test_score_mae_overflow_is_not_finite() -> None:
    # Turn 3 (SPEC 14): finite inputs whose sum overflows to inf.
    m = score_mae([ScorePair(1e308, -0.5)] * 2)
    assert (m.value, m.numerator, m.denominator) == (None, None, None)
    assert m.reason == "non_finite_result"
    assert m.n_items == 2


def test_score_mae_large_but_finite_is_kept() -> None:
    m = score_mae([ScorePair(1e308, -0.5)])
    assert m.value == pytest.approx(1e308)
    assert m.reason is None


def test_score_mae_int_too_large_for_a_float() -> None:
    # Turn 4: JSON allows ints of any size.
    m = score_mae([ScorePair(10**400, 0)])
    assert m.value is None
    assert m.reason == "non_finite_result"


def test_score_mae_float_and_huge_int() -> None:
    # Turn 4: 1.5 - 10**400 raises OverflowError before any division.
    m = score_mae([ScorePair(1.5, 10**400), ScorePair(1, 2)])
    assert (m.value, m.numerator, m.denominator) == (None, None, None)
    assert m.reason == "non_finite_result"
    assert m.n_items == 2
