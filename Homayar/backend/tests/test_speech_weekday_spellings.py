"""HOM-351: Persian weekday names written joined, with ZWNJ, or with a space.

Real calls write "پنج‌شنبه" (ZWNJ) or "پنج شنبه" (space) as often as the joined
"پنجشنبه". Before the fix the ZWNJ and space spellings were read as a number
plus "شنبه" (Saturday).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from backend.speech.dates import (
    TEHRAN,
    WEEKDAY_NAMES_FA,
    WEEKDAYS_FA,
    extract_temporal,
)

ZWNJ = "‌"

# (number word, weekday() value, a reference spelling that worked before the fix)
SPLIT_WEEKDAYS = [
    ("یک", 6, "یکشنبه"),
    ("دو", 0, "دوشنبه"),
    ("سه", 1, "سه شنبه"),
    ("چهار", 2, "چهارشنبه"),
    ("پنج", 3, "پنجشنبه"),
]
MODIFIERS = ["", " آینده", " بعد", " دیگه", " بعدی", " قبل", " پیش"]
# 2026-11-14 is a Saturday; the next 7 days cover every reference weekday.
REFERENCE_DAYS = [date(2026, 11, 14) + timedelta(days=k) for k in range(7)]


def _ref(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=TEHRAN)


def _spellings(num: str) -> list[str]:
    return [num + "شنبه", num + ZWNJ + "شنبه", num + " شنبه"]


def _only(text: str, ref: datetime):
    matches = [m for m in extract_temporal(text, ref) if m.kind == "date"]
    assert len(matches) == 1, (text, matches)
    return matches[0]


@pytest.mark.parametrize(("num", "weekday", "reference"), SPLIT_WEEKDAYS)
@pytest.mark.parametrize("modifier", MODIFIERS)
@pytest.mark.parametrize("today", REFERENCE_DAYS, ids=lambda d: d.isoformat())
def test_every_spelling_matches_the_reference_spelling(
    num: str, weekday: int, reference: str, modifier: str, today: date
) -> None:
    ref = _ref(today)
    expected = _only(reference + modifier, ref)
    for spelling in _spellings(num):
        text = spelling + modifier
        got = _only(text, ref)
        assert got.candidates == expected.candidates, text
        assert got.ambiguous == expected.ambiguous, text
        assert got.rule == expected.rule, text
        assert got.confidence == expected.confidence, text
        assert (got.start, got.end, got.raw) == (0, len(text), text), text
        assert all(c.weekday() == weekday for c in got.candidates), text


@pytest.mark.parametrize(("num", "weekday", "reference"), SPLIT_WEEKDAYS)
def test_offsets_point_into_the_original_sentence(num: str, weekday: int, reference: str) -> None:
    ref = _ref(date(2026, 11, 14))
    for spelling in _spellings(num):
        phrase = spelling + " آینده"
        text = f"گزارش را {phrase} تحویل می‌دهم"
        got = _only(text, ref)
        assert text[got.start : got.end] == phrase
        assert got.raw == phrase
        assert all(c.weekday() == weekday for c in got.candidates)


def test_zwnj_thursday_next_is_thursday_not_saturday() -> None:
    """The HOM-351 example: reference Saturday 2026-11-14 in Tehran."""
    ref = _ref(date(2026, 11, 14))
    joined = _only("پنجشنبه آینده", ref)
    for text in ("پنج‌شنبه آینده", "پنج شنبه آینده"):
        got = _only(text, ref)
        assert date(2026, 11, 21) not in got.candidates
        assert got.candidates == joined.candidates
        assert got.candidates[0] == date(2026, 11, 19)


@pytest.mark.parametrize("text", ["شنبه", "جمعه", "سشنبه", "سه‌شنبه", "سه شنبه"])
@pytest.mark.parametrize("modifier", ["", " آینده", " قبل"])
def test_one_word_names_and_colloquial_tuesday_still_work(text: str, modifier: str) -> None:
    ref = _ref(date(2026, 11, 16))
    got = _only(text + modifier, ref)
    want = {"شنبه": 5, "جمعه": 4}.get(text, 1)
    assert all(c.weekday() == want for c in got.candidates)
    assert (got.start, got.end) == (0, len(text + modifier))


@pytest.mark.parametrize(
    ("text", "weekday"),
    [
        ("يکشنبه", 6),
        ("يک‌شنبه", 6),
        ("يك شنبه", 6),
        ("پنج‌شنبه", 3),
        ("چهار شنبه", 2),
    ],
)
def test_arabic_letter_variants(text: str, weekday: int) -> None:
    got = _only(text, _ref(date(2026, 11, 14)))
    assert all(c.weekday() == weekday for c in got.candidates)
    assert (got.start, got.end) == (0, len(text))


def test_saturday_next_is_still_saturday() -> None:
    got = _only("شنبه آینده", _ref(date(2026, 11, 16)))
    assert got.candidates == (date(2026, 11, 21),)


@pytest.mark.parametrize(
    ("text", "days"),
    [("پنج روز دیگه", 5), ("سه هفته دیگه", 21), ("دو روز پیش", -2), ("یک هفته بعد", 7)],
)
def test_number_with_other_words_keeps_meaning(text: str, days: int) -> None:
    today = date(2026, 11, 14)
    got = _only(text, _ref(today))
    assert got.rule == "relative_n_days"
    assert got.candidates == (today + timedelta(days=days),)


@pytest.mark.parametrize("num", ["شش", "ده", "هفت", "بیست"])
def test_other_numbers_before_saturday_are_not_weekdays(num: str) -> None:
    """Only یک/دو/سه/چهار/پنج form a weekday name with "شنبه"."""
    ref = _ref(date(2026, 11, 16))
    for sep in (" ", ZWNJ):
        text = num + sep + "شنبه"
        got = _only(text, ref)
        assert got.start == len(num) + 1
        assert got.raw == "شنبه"
        assert all(c.weekday() == 5 for c in got.candidates)


def test_split_needs_only_whitespace_between_the_words() -> None:
    """A comma between "پنج" and "شنبه" is two words, as before."""
    got = _only("پنج، شنبه", _ref(date(2026, 11, 16)))
    assert got.raw == "شنبه"


def test_public_weekday_tables_are_unchanged() -> None:
    assert WEEKDAYS_FA == {
        "شنبه": 5,
        "یکشنبه": 6,
        "دوشنبه": 0,
        "سه شنبه": 1,
        "سشنبه": 1,
        "چهارشنبه": 2,
        "پنجشنبه": 3,
        "جمعه": 4,
    }
    assert WEEKDAY_NAMES_FA == {
        5: "شنبه",
        6: "یکشنبه",
        0: "دوشنبه",
        1: "سه شنبه",
        2: "چهارشنبه",
        3: "پنجشنبه",
        4: "جمعه",
    }


def test_joined_tuesday_spelling() -> None:
    """T4-C1: "سهشنبه" (no space, no ZWNJ) is Tuesday too."""
    ref = _ref(date(2026, 11, 14))
    assert _only("سهشنبه آینده", ref).candidates == _only("سه‌شنبه آینده", ref).candidates


@pytest.mark.parametrize("text", ["پنج  شنبه", "دو  شنبه", "پنج\tشنبه", "پنج\nشنبه"])
def test_other_gaps_keep_todays_reading(text: str) -> None:
    """T4-C2: only one space or one ZWNJ joins the words; a double space does not."""
    got = _only(text, _ref(date(2026, 11, 16)))
    assert got.raw == "شنبه"
    assert all(c.weekday() == 5 for c in got.candidates)


@pytest.mark.parametrize("text", ["5 شنبه", "۵ شنبه", "۵‌شنبه"])
def test_digit_heads_keep_todays_reading(text: str) -> None:
    """T4-C2: a digit before "شنبه" is not a weekday name."""
    got = _only(text, _ref(date(2026, 11, 16)))
    assert got.raw == "شنبه"
    assert all(c.weekday() == 5 for c in got.candidates)


def test_glued_digit_form_is_not_a_weekday() -> None:
    """T4-C2: "۵شنبه" is one token and gives no date, as before."""
    assert [
        m for m in extract_temporal("۵شنبه", _ref(date(2026, 11, 16))) if m.kind == "date"
    ] == []


# T4-C4: with a SPACE, a head word after "و" + number word (compound number) or after
# "ساعت" (a time) is not joined with "شنبه". A ZWNJ pair always joins.
COMPOUND_SPACE = [
    ("بیست و پنج شنبه", 25),
    ("سی و یک شنبه", 31),
    ("بیست و دو شنبه", 22),
    ("بیست و چهار شنبه", 24),
    ("بیست و سه شنبه", 23),
]


@pytest.mark.parametrize(("text", "number"), COMPOUND_SPACE)
@pytest.mark.parametrize("today", REFERENCE_DAYS, ids=lambda d: d.isoformat())
def test_space_after_compound_number_keeps_saturday(text: str, number: int, today: date) -> None:
    got = _only(text, _ref(today))
    assert got.raw == "شنبه"
    assert got.start == len(text) - len("شنبه")
    assert all(c.weekday() == 5 for c in got.candidates)


@pytest.mark.parametrize(
    ("text", "weekday"),
    [
        ("بیست و پنج‌شنبه", 3),
        ("سی و یک‌شنبه", 6),
        ("بیست و سه‌شنبه", 1),
        ("ساعت پنج‌شنبه", 3),
        ("ساعت دو‌شنبه", 0),
    ],
)
def test_zwnj_pair_always_joins(text: str, weekday: int) -> None:
    got = _only(text, _ref(date(2026, 11, 18)))
    assert got.raw == text.split(" ")[-1]
    assert got.end == len(text)
    assert all(c.weekday() == weekday for c in got.candidates)


@pytest.mark.parametrize("head", ["یک", "دو", "چهار", "پنج"])
def test_space_after_saat_keeps_time_and_saturday(head: str) -> None:
    text = f"ساعت {head} شنبه"
    ms = extract_temporal(text, _ref(date(2026, 11, 18)))
    assert [m.kind for m in ms] == ["time", "date"]
    assert ms[0].raw == f"ساعت {head}"
    assert ms[1].raw == "شنبه"
    assert all(c.weekday() == 5 for c in ms[1].candidates)


def test_space_after_saat_three_is_still_tuesday() -> None:
    """Base already read "ساعت سه شنبه" as Tuesday (old "سه" + "شنبه" rule)."""
    got = _only("ساعت سه شنبه", _ref(date(2026, 11, 18)))
    assert got.raw == "سه شنبه"
    assert all(c.weekday() == 1 for c in got.candidates)


@pytest.mark.parametrize(
    "text",
    ["و پنج شنبه", "جلسه و پنج شنبه", "فردا و پنج شنبه"],
)
def test_space_after_and_without_number_word_still_joins(text: str) -> None:
    """Only "و" after a number WORD blocks the join."""
    ms = [m for m in extract_temporal(text, _ref(date(2026, 11, 18))) if m.raw == "پنج شنبه"]
    assert len(ms) == 1
    assert all(c.weekday() == 3 for c in ms[0].candidates)
