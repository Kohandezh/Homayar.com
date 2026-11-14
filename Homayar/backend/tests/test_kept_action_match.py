"""Unit tests of the kept-action match rule (HOM-381, ADR 0054, K2 and K3).

``services/kept_action_match.py`` is pure: no database, clock or network.
These tests pin the rule itself, in Persian like the texts it will see.
"""

from __future__ import annotations

import difflib
from datetime import UTC, datetime, timedelta

from backend.services.analysis_dedupe import _normalizer
from backend.services.kept_action_match import (
    KEPT_ACTION_MATCH_RATIO,
    KeptAction,
    NewAction,
    match_kept_actions,
)

ORIGINAL = "پیگیری قرارداد انجام شود"
CORRECTED = "پیگیری فاکتور انجام شود"  # one word changed: ratio >= 0.8
FAR = "گزارش ماهانه بودجه را برای مدیر ارسال کن"  # a different subject: ratio < 0.8

_BASE = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _kept(
    action_id: str,
    description: str,
    *,
    kind: str | None = "commitment",
    created_at: datetime | None = None,
) -> KeptAction:
    return KeptAction(
        id=action_id,
        description=description,
        kind=kind,
        created_at=created_at or _BASE,
    )


def _new(description: str, *, kind: str | None = "commitment") -> NewAction:
    return NewAction(kind=kind, description=description)


def test_the_threshold_is_the_adr_value():
    assert KEPT_ACTION_MATCH_RATIO == 0.8


def test_a_close_text_of_the_same_kind_matches():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {CORRECTED}")]
    assert match_kept_actions(kept, new, language="fa") == {0: 0}


def test_a_kept_action_of_a_different_kind_never_matches():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {CORRECTED}", kind="next_step")]
    assert match_kept_actions(kept, new, language="fa") == {}


def test_a_kept_action_with_a_null_kind_never_matches():
    # rows from before HOM-133 have no kind
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}", kind=None)]
    new = [_new(f"Call back: {ORIGINAL}")]
    assert match_kept_actions(kept, new, language="fa") == {}


def test_a_new_action_with_a_null_kind_never_matches():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {ORIGINAL}", kind=None)]
    assert match_kept_actions(kept, new, language="fa") == {}


def test_a_far_text_never_matches():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {FAR}")]
    assert match_kept_actions(kept, new, language="fa") == {}


def test_text_that_normalizes_to_nothing_never_matches():
    kept = [_kept("kept-1", "...")]
    new = [_new(f"Call back: {ORIGINAL}")]
    assert match_kept_actions(kept, new, language="fa") == {}
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new("!!! ؟")]
    assert match_kept_actions(kept, new, language="fa") == {}


def test_the_texts_compare_after_normalization():
    # spacing and Arabic/Yeh variants fold away before the ratio
    loose = f"Call  back:  {ORIGINAL}"
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(loose)]
    assert match_kept_actions(kept, new, language="fa") == {0: 0}


def test_a_language_without_a_normalizer_falls_back_to_persian():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {CORRECTED}")]
    assert match_kept_actions(kept, new, language="xx-not-a-language") == {0: 0}
    assert match_kept_actions(kept, new, language=None) == {0: 0}


def test_each_kept_action_takes_at_most_one_new_action():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {ORIGINAL}"), _new(f"Call back: {CORRECTED}")]
    # the exact copy is the better match; the close one stays unmatched
    assert match_kept_actions(kept, new, language="fa") == {0: 0}


def test_each_new_action_goes_to_at_most_one_kept_action():
    first = _kept("kept-1", f"Call back: {ORIGINAL}", created_at=_BASE)
    second = _kept("kept-2", f"Call back: {ORIGINAL}", created_at=_BASE + timedelta(minutes=1))
    new = [_new(f"Call back: {CORRECTED}")]
    # equal ratios: the earlier kept action (created_at) wins
    assert match_kept_actions([first, second], new, language="fa") == {0: 0}
    assert match_kept_actions([second, first], new, language="fa") == {0: 1}


def test_an_equal_ratio_tie_breaks_by_kept_id_when_created_at_is_equal():
    first = _kept("kept-1", f"Call back: {ORIGINAL}", created_at=_BASE)
    second = _kept("kept-2", f"Call back: {ORIGINAL}", created_at=_BASE)
    new = [_new(f"Call back: {CORRECTED}")]
    assert match_kept_actions([first, second], new, language="fa") == {0: 0}


def test_an_equal_ratio_tie_breaks_by_the_new_action_index():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {CORRECTED}"), _new(f"Call back: {CORRECTED}")]
    assert match_kept_actions(kept, new, language="fa") == {0: 0}


def test_two_kept_and_two_new_actions_pair_with_their_best_match():
    kept = [
        _kept("kept-0", f"Call back 0: {ORIGINAL}", created_at=_BASE),
        _kept("kept-1", f"Call back 1: {ORIGINAL}", created_at=_BASE + timedelta(minutes=1)),
    ]
    # every cross pair also passes K2; the same-index pair is the better one
    new = [_new(f"Call back 0: {CORRECTED}"), _new(f"Call back 1: {CORRECTED}")]
    assert match_kept_actions(kept, new, language="fa") == {0: 0, 1: 1}


def test_a_non_matching_new_action_is_not_paired():
    kept = [_kept("kept-1", f"Call back: {ORIGINAL}")]
    new = [_new(f"Call back: {CORRECTED}"), _new(f"Call back: {FAR}")]
    assert match_kept_actions(kept, new, language="fa") == {0: 0}


def test_no_kept_actions_and_no_new_actions_match_nothing():
    assert match_kept_actions([], [], language="fa") == {}
    assert match_kept_actions([], [_new(f"Call back: {ORIGINAL}")], language="fa") == {}
    assert match_kept_actions([_kept("kept-1", f"Call back: {ORIGINAL}")], [], language="fa") == {}


# ``ratio()`` is not symmetric, so K2 fixes the order: the kept text is the
# first argument.  These two pairs fall on different sides of the threshold
# when the order is swapped, and each test asserts that, so it cannot pass by
# accident.
_ORDER_SHORT = "پیگیری فاکتور قرارداد مشتری انجام شود"
_ORDER_LONG = "پیگیری تا هفته آینده قرارداد مشتری انجام شود"


def _both_orders(first: str, second: str) -> tuple[float, float]:
    """The K2 ratio in both argument orders, on the normalized texts (the
    texts of the two tests are chosen so the two orders really differ)."""
    normalize = _normalizer("fa")
    kept_first = difflib.SequenceMatcher(
        None, normalize(first), normalize(second), autojunk=False
    ).ratio()
    new_first = difflib.SequenceMatcher(
        None, normalize(second), normalize(first), autojunk=False
    ).ratio()
    assert kept_first != new_first
    return kept_first, new_first


def test_a_pair_that_passes_only_with_the_kept_text_first_merges():
    kept_first, new_first = _both_orders(_ORDER_SHORT, _ORDER_LONG)
    assert kept_first >= KEPT_ACTION_MATCH_RATIO > new_first
    kept = [_kept("kept-1", _ORDER_SHORT)]
    new = [_new(_ORDER_LONG)]
    assert match_kept_actions(kept, new, language="fa") == {0: 0}


def test_a_pair_that_fails_with_the_kept_text_first_does_not_merge():
    kept_first, new_first = _both_orders(_ORDER_LONG, _ORDER_SHORT)
    assert new_first >= KEPT_ACTION_MATCH_RATIO > kept_first
    kept = [_kept("kept-1", _ORDER_LONG)]
    new = [_new(_ORDER_SHORT)]
    assert match_kept_actions(kept, new, language="fa") == {}
