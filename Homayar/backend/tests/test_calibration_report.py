"""HOM-75 PR 2: the agreement report's math, pure and hand computed (ADR 0056 D4).

``agreement_row`` takes compared pairs as ``(adjudicated, answer)`` tuples.
``report_numbers`` takes the adjudicated pairs of a batch, the participant ids and the
answers, and returns every block of the report.  Every expected number below was
computed by hand with fractions; the comment next to it shows the work.

* ``agreement_bp`` = ``matched * 10000 / compared`` rounded half up.
* ``kappa_e4`` = ``(p_o - p_e) / (1 - p_e) * 10000`` rounded half away from zero, with
  ``p_o = matched / n`` and ``p_e = sum(answers_k * gold_k) / n^2``.
* Both are ``None`` when nothing is compared; ``kappa_e4`` also when ``p_e = 1``.
* A machine ``insufficient_evidence`` counts as compared and never matches.
"""

from __future__ import annotations

from fractions import Fraction

from backend.services.calibration_service import (
    Pair,
    agreement_row,
    report_numbers,
    round_half_away,
    round_half_up,
)

P = "passed"
F = "failed"
NA = "not_applicable"
IE = "insufficient_evidence"


# --- rounding ------------------------------------------------------------------------------


def test_round_half_up():
    assert round_half_up(Fraction(10000, 3)) == 3333  # 3333.33
    assert round_half_up(Fraction(20000, 3)) == 6667  # 6666.67
    assert round_half_up(Fraction(625, 2)) == 313  # 312.5; round() would give 312
    assert round_half_up(Fraction(1, 2)) == 1
    assert round_half_up(Fraction(0)) == 0


def test_round_half_away_from_zero():
    assert round_half_away(Fraction(-10625, 2)) == -5313  # -5312.5; round() gives -5312
    assert round_half_away(Fraction(10625, 2)) == 5313  # 5312.5; round() gives 5312
    assert round_half_away(Fraction(-10623, 2)) == -5312  # -5311.5
    assert round_half_away(Fraction(-10624, 2)) == -5312  # exact
    assert round_half_away(Fraction(-1, 3)) == 0


# --- one row ---------------------------------------------------------------------------------


def test_agreement_bp_rounds_half_up():
    def bp(matched: int, compared: int) -> int | None:
        pairs = [(P, P)] * matched + [(P, F)] * (compared - matched)
        return agreement_row(pairs)["agreement_bp"]

    assert bp(1, 3) == 3333  # 3333.33
    assert bp(2, 3) == 6667  # 6666.67
    assert bp(1, 8) == 1250  # exact
    assert bp(1, 32) == 313  # 312.5 rounds up
    assert bp(0, 4) == 0
    assert bp(4, 4) == 10000


def test_a_hand_computed_row():
    pairs = [(P, P), (F, F), (NA, P), (F, F), (P, F)]
    # n = 5, matched 3: agreement 30000 / 5 = 6000.
    # answers P 2, F 3; golds P 2, F 2, NA 1.
    # p_e = (2*2 + 3*2) / 25 = 10/25; p_o = 15/25; kappa = 5/15 = 1/3 -> 3333.33 -> 3333.
    assert agreement_row(pairs) == {
        "compared": 5,
        "matched": 3,
        "agreement_bp": 6000,
        "kappa_e4": 3333,
        "confusion": {P: {P: 1, F: 1}, F: {F: 2}, NA: {P: 1}},
    }


def test_a_negative_kappa_rounds_half_away_from_zero():
    pairs = [(P, F)] * 4 + [(F, P), (F, NA), (NA, P)]
    # n = 7, matched 0.  answers F 4, P 2, NA 1; golds P 4, F 2, NA 1.
    # p_e = (4*2 + 2*4 + 1*1) / 49 = 17/49; p_o = 0.
    # kappa = (0 - 17/49) / (32/49) = -17/32 = -0.53125 -> -5312.5 -> -5313.
    row = agreement_row(pairs)
    assert (row["compared"], row["matched"], row["agreement_bp"]) == (7, 0, 0)
    assert row["kappa_e4"] == -5313
    assert row["confusion"] == {P: {F: 4}, F: {P: 1, NA: 1}, NA: {P: 1}}


def test_kappa_is_null_when_p_e_is_one_and_both_are_null_when_nothing_is_compared():
    # Every answer and every adjudication is "passed": p_e = (2*2) / 4 = 1.
    row = agreement_row([(P, P), (P, P)])
    assert (row["agreement_bp"], row["kappa_e4"]) == (10000, None)
    empty = agreement_row([])
    assert empty == {
        "compared": 0,
        "matched": 0,
        "agreement_bp": None,
        "kappa_e4": None,
        "confusion": {},
    }


def test_insufficient_evidence_counts_as_compared_and_never_matches():
    pairs = [(P, IE), (F, IE), (P, P)]
    # n = 3, matched 1 -> 3333.  answers IE 2, P 1; golds P 2, F 1.
    # p_e = (1*2) / 9 = 2/9; p_o = 3/9; kappa = (1/9) / (7/9) = 1/7 -> 1428.57 -> 1429.
    assert agreement_row(pairs) == {
        "compared": 3,
        "matched": 1,
        "agreement_bp": 3333,
        "kappa_e4": 1429,
        "confusion": {P: {IE: 1, P: 1}, F: {IE: 1}},
    }


def test_the_math_uses_no_float():
    # 1/3 as a float is 0.333...; the exact fraction must give the exact integer.
    row = agreement_row([(P, P), (F, F), (NA, P), (F, F), (P, F)])
    assert isinstance(row["agreement_bp"], int) and isinstance(row["kappa_e4"], int)


# --- a whole batch: 2 participants, 2 examples ----------------------------------------------

# Example e1 has three reviewable criteria, e2 has two.  Each pair: (gold, machine).
PAIRS = [
    Pair("e1", "c1", P, P),
    Pair("e1", "c2", F, IE),
    Pair("e1", "c3", NA, F),
    Pair("e2", "c1", F, F),
    Pair("e2", "c2", P, P),
]
ANSWERS = {
    # Participant a answers all five.
    ("a", "e1", "c1"): P,
    ("a", "e1", "c2"): F,
    ("a", "e1", "c3"): P,
    ("a", "e2", "c1"): F,
    ("a", "e2", "c2"): F,
    # Participant b answers three; e1/c3 and e2/c1 are missing.
    ("b", "e1", "c1"): F,
    ("b", "e1", "c2"): F,
    ("b", "e2", "c2"): P,
}


def test_a_hand_computed_batch_with_two_participants_and_two_examples():
    numbers = report_numbers(PAIRS, ["a", "b"], ANSWERS)

    # a: (P,P) (F,F) (NA,P) (F,F) (P,F): n 5, matched 3 -> 6000.
    # answers P 2, F 3; golds P 2, F 2, NA 1: p_e = (4 + 6) / 25 = 2/5, p_o = 3/5,
    # kappa = (1/5) / (3/5) = 1/3 -> 3333.
    assert numbers["participants"]["a"] == {
        "expected": 5,
        "compared": 5,
        "matched": 3,
        "missing": 0,
        "agreement_bp": 6000,
        "kappa_e4": 3333,
        "confusion": {P: {P: 1, F: 1}, F: {F: 2}, NA: {P: 1}},
    }
    # b: (P,F) (F,F) (P,P): n 3, matched 2 -> 6666.67 -> 6667.
    # answers F 2, P 1; golds P 2, F 1: p_e = (2*1 + 1*2) / 9 = 4/9, p_o = 6/9,
    # kappa = (2/9) / (5/9) = 2/5 -> 4000.
    assert numbers["participants"]["b"] == {
        "expected": 5,
        "compared": 3,
        "matched": 2,
        "missing": 2,
        "agreement_bp": 6667,
        "kappa_e4": 4000,
        "confusion": {P: {F: 1, P: 1}, F: {F: 1}},
    }
    # Overall pools a and b: expected 5 * 2 = 10, n 8, matched 5 -> 6250.
    # answers P 3, F 5; golds P 4, F 3, NA 1: p_e = (3*4 + 5*3) / 64 = 27/64,
    # p_o = 40/64, kappa = (13/64) / (37/64) = 13/37 = 0.351351 -> 3513.51 -> 3514.
    assert numbers["overall"] == {
        "expected": 10,
        "compared": 8,
        "matched": 5,
        "missing": 2,
        "agreement_bp": 6250,
        "kappa_e4": 3514,
        "confusion": {P: {P: 2, F: 2}, F: {F: 3}, NA: {P: 1}},
    }
    # Per criterion over both participants and both examples, in first-seen order:
    # c1: a (P,P) (F,F), b (P,F) -> 3 compared, 2 matched -> 6667.
    # c2: a (F,F) (P,F), b (F,F) (P,P) -> 4 compared, 3 matched -> 7500.
    # c3: a (NA,P) -> 1 compared, 0 matched -> 0.
    assert numbers["criteria"] == [
        {"criterion_id": "c1", "compared": 3, "matched": 2, "agreement_bp": 6667},
        {"criterion_id": "c2", "compared": 4, "matched": 3, "agreement_bp": 7500},
        {"criterion_id": "c3", "compared": 1, "matched": 0, "agreement_bp": 0},
    ]
    # Machine: (P,P) (F,IE) (NA,F) (F,F) (P,P): n 5, matched 3 -> 6000.
    # machine P 2, IE 1, F 2; golds P 2, F 2, NA 1: p_e = (2*2 + 2*2) / 25 = 8/25,
    # p_o = 15/25, kappa = (7/25) / (17/25) = 7/17 = 0.411765 -> 4117.65 -> 4118.
    assert numbers["machine"] == {
        "expected": 5,
        "compared": 5,
        "matched": 3,
        "missing": 0,
        "agreement_bp": 6000,
        "kappa_e4": 4118,
        "confusion": {P: {P: 2}, F: {IE: 1, F: 1}, NA: {F: 1}},
    }


def test_a_participant_with_no_answer_has_null_numbers_and_a_full_missing_count():
    numbers = report_numbers(PAIRS, ["a", "b", "c"], ANSWERS)
    assert numbers["participants"]["c"] == {
        "expected": 5,
        "compared": 0,
        "matched": 0,
        "missing": 5,
        "agreement_bp": None,
        "kappa_e4": None,
        "confusion": {},
    }
    # c adds 5 to the overall expected and nothing else.
    assert (numbers["overall"]["expected"], numbers["overall"]["compared"]) == (15, 8)


def test_a_criterion_nobody_answered_is_listed_with_nothing_compared():
    numbers = report_numbers(PAIRS, ["b"], ANSWERS)
    assert {row["criterion_id"]: row for row in numbers["criteria"]}["c3"] == {
        "criterion_id": "c3",
        "compared": 0,
        "matched": 0,
        "agreement_bp": None,
    }
