"""Jalali conversion, spoken numbers and Persian date/time expressions."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from backend.speech.dates import (
    TEHRAN,
    DateRange,
    extract_temporal,
    format_jalali,
    gregorian_to_jalali,
    is_jalali_leap,
    is_valid_jalali,
    jalali_month_length,
    jalali_to_gregorian,
    parse_ordinal,
    parse_spoken_number,
)

KNOWN_PAIRS = [
    ((1403, 12, 30), date(2025, 3, 20)),  # leap-year Esfand 30
    ((1404, 1, 1), date(2025, 3, 21)),
    ((1403, 1, 1), date(2024, 3, 20)),
    ((1399, 12, 30), date(2021, 3, 20)),  # leap
    ((1400, 1, 1), date(2021, 3, 21)),
    ((1402, 1, 1), date(2023, 3, 21)),
    ((1357, 11, 22), date(1979, 2, 11)),
    ((1370, 1, 1), date(1991, 3, 21)),
    ((1379, 10, 11), date(2000, 12, 31)),
    ((1405, 7, 5), date(2026, 9, 27)),
    ((1404, 6, 31), date(2025, 9, 22)),
    ((1404, 7, 1), date(2025, 9, 23)),
    ((1395, 12, 30), date(2017, 3, 20)),  # leap
]


@pytest.mark.parametrize(("jalali", "greg"), KNOWN_PAIRS)
def test_known_pairs(jalali: tuple[int, int, int], greg: date) -> None:
    assert jalali_to_gregorian(*jalali) == greg
    assert gregorian_to_jalali(greg) == jalali


def test_roundtrip_every_day_over_decades() -> None:
    d = date(1980, 1, 1)
    prev = gregorian_to_jalali(d - timedelta(days=1))
    while d < date(2060, 1, 1):
        j = gregorian_to_jalali(d)
        assert jalali_to_gregorian(*j) == d
        assert j != prev
        prev = j
        d += timedelta(days=1)


@pytest.mark.parametrize(
    ("year", "leap"),
    [(1399, True), (1403, True), (1408, True), (1400, False), (1404, False), (1402, False)],
)
def test_leap_years(year: int, leap: bool) -> None:
    assert is_jalali_leap(year) is leap
    assert jalali_month_length(year, 12) == (30 if leap else 29)


def test_invalid_dates() -> None:
    assert not is_valid_jalali(1404, 12, 30)
    assert not is_valid_jalali(1404, 7, 31)
    assert not is_valid_jalali(1404, 13, 1)
    with pytest.raises(ValueError):
        jalali_to_gregorian(1404, 12, 30)


def test_format_jalali() -> None:
    assert format_jalali(date(2025, 3, 20)) == "1403/12/30"
    assert format_jalali(date(2025, 3, 20), month_name=True) == "30 اسفند 1403"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("یک", 1),
        ("یه", 1),
        ("ده", 10),
        ("پونزده", 15),
        ("بیست و پنج", 25),
        ("صد", 100),
        ("یک صد و بیست", 120),
        ("سیصد و شصت و پنج", 365),
        ("هزار", 1000),
        ("دو هزار و پانصد", 2500),
        ("پونصد هزار", 500000),
        ("سه میلیون و دویست هزار", 3200000),
        ("۱۴۰۳", 1403),
        ("صفر", 0),
        ("بيست و پنج", 25),  # Arabic yeh
    ],
)
def test_spoken_numbers(text: str, value: int) -> None:
    assert parse_spoken_number(text) == value


@pytest.mark.parametrize("text", ["سلام", "بیست سی", "", "و پنج"])
def test_not_numbers(text: str) -> None:
    assert parse_spoken_number(text) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("اول", 1),
        ("یکم", 1),
        ("دوم", 2),
        ("سوم", 3),
        ("پنجم", 5),
        ("نهم", 9),
        ("دهم", 10),
        ("پانزدهم", 15),
        ("بیستم", 20),
        ("بیست و یکم", 21),
        ("سی ام", 30),
        ("سی‌ام", 30),
        ("صدم", 100),
    ],
)
def test_ordinals(text: str, value: int) -> None:
    assert parse_ordinal(text) == value


def test_cardinal_is_not_ordinal() -> None:
    assert parse_ordinal("پنج") is None


# Sunday 2026-09-27 10:00 Tehran = 1405/07/05.
REF = datetime(2026, 9, 27, 10, 0, tzinfo=TEHRAN)


def one(text: str, ref: datetime = REF):
    matches = extract_temporal(text, ref)
    assert len(matches) == 1, matches
    return matches[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("امروز", date(2026, 9, 27)),
        ("فردا", date(2026, 9, 28)),
        ("پس‌فردا", date(2026, 9, 29)),
        ("پس فردا", date(2026, 9, 29)),
        ("پسفردا", date(2026, 9, 29)),
        ("دیروز", date(2026, 9, 26)),
        ("پریروز", date(2026, 9, 25)),
        ("سه روز دیگه", date(2026, 9, 30)),
        ("۱۰ روز بعد", date(2026, 10, 7)),
        ("دو روز پیش", date(2026, 9, 25)),
        ("دو هفته دیگر", date(2026, 10, 11)),
        ("شنبه آینده", date(2026, 10, 3)),
        ("دوشنبه قبل", date(2026, 9, 21)),
        ("۱۴۰۳/۱۲/۳۰", date(2025, 3, 20)),
        ("1404-01-01", date(2025, 3, 21)),
        ("2025-03-20", date(2025, 3, 20)),
        ("پانزدهم مهر ۱۴۰۴", date(2025, 10, 7)),
        ("۳۰ اسفند ۱۴۰۳", date(2025, 3, 20)),
        ("یکم فروردین 1405", date(2026, 3, 21)),
    ],
)
def test_resolved_dates(text: str, expected: date) -> None:
    m = one(text)
    assert m.kind == "date"
    assert not m.ambiguous
    assert m.value == expected
    assert m.raw == text
    assert 0 < m.confidence <= 1


def test_invalid_numeric_jalali_not_emitted() -> None:
    assert extract_temporal("۱۴۰۴/۱۲/۳۰", REF) == []


def test_next_week_range() -> None:
    m = one("هفته آینده")
    assert m.kind == "date_range"
    assert m.value == DateRange(date(2026, 10, 3), date(2026, 10, 9))
    assert one("هفته پیش").value == DateRange(date(2026, 9, 19), date(2026, 9, 25))


def test_bare_weekday_is_ambiguous() -> None:
    m = one("چهارشنبه")
    assert m.ambiguous and m.value is None
    assert m.candidates == (date(2026, 9, 30), date(2026, 9, 23))
    assert m.confidence <= 0.5


def test_weekday_next_within_same_week_is_ambiguous() -> None:
    m = one("سه‌شنبه آینده")
    assert m.ambiguous
    assert m.candidates == (date(2026, 9, 29), date(2026, 10, 6))


def test_weekday_same_day() -> None:
    m = one("یکشنبه")  # today is Sunday
    assert m.candidates == (date(2026, 9, 27), date(2026, 10, 4))


def test_day_month_without_year_returns_candidates() -> None:
    m = one("۱۵ مهر")
    assert m.ambiguous
    assert m.candidates[0] == date(2026, 10, 7)  # upcoming first
    assert date(2025, 10, 7) in m.candidates
    assert one("اول مهر").candidates[0] == date(2027, 9, 23)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ساعت ۱۰ صبح", time(10)),
        ("۴ بعدازظهر", time(16)),
        ("۴ بعد از ظهر", time(16)),
        ("ساعت ۶ عصر", time(18)),
        ("ساعت ۹ شب", time(21)),
        ("۲ شب", time(2)),
        ("ساعت ۱۲ ظهر", time(12)),
        ("۲ ظهر", time(14)),
        ("ساعت 18:30", time(18, 30)),
        ("ساعت ده و نیم صبح", time(10, 30)),
        ("ساعت هشت و ربع شب", time(20, 15)),
        ("ساعت ۱۷", time(17)),
        ("ساعت سه و بیست دقیقه بعدازظهر", time(15, 20)),
        ("7:05 صبح", time(7, 5)),
    ],
)
def test_resolved_times(text: str, expected: time) -> None:
    m = one(text)
    assert m.kind == "time"
    assert not m.ambiguous
    assert m.value == expected


def test_time_without_period_is_ambiguous() -> None:
    m = one("ساعت ده و نیم")
    assert m.ambiguous and m.value is None
    assert m.candidates == (time(10, 30), time(22, 30))
    assert one("ساعت ۴").candidates == (time(4), time(16))


def test_bare_number_is_not_a_time() -> None:
    assert extract_temporal("ده و نیم کیلو", REF) == []
    assert extract_temporal("پنج تا بلیط", REF) == []


def test_date_then_time_sentence_offsets() -> None:
    text = "لطفا فردا ساعت ۱۰ صبح با من تماس بگیرید"
    ms = extract_temporal(text, REF)
    assert [m.kind for m in ms] == ["date", "time"]
    for m in ms:
        assert text[m.start : m.end] == m.raw
    assert ms[0].value == date(2026, 9, 28) and ms[1].value == time(10)


def test_date_followed_by_bare_time() -> None:
    ms = extract_temporal("فردا ده و نیم", REF)
    assert [m.kind for m in ms] == ["date", "time"]
    assert ms[1].candidates == (time(10, 30), time(22, 30))


def test_mixed_sentence() -> None:
    text = "جلسه ساعت 4 بعد از ظهر سه‌شنبه آینده است"
    ms = extract_temporal(text, REF)
    assert ms[0].value == time(16)
    assert ms[1].ambiguous


def test_reference_timezone_controls_today() -> None:
    # 2026-09-27 21:00 UTC is already 2026-09-28 in Tehran (+03:30).
    ref = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)
    assert one("امروز", ref).value == date(2026, 9, 28)
    assert extract_temporal("امروز", ref, UTC)[0].value == date(2026, 9, 27)


def test_naive_reference_rejected() -> None:
    with pytest.raises(ValueError):
        extract_temporal("فردا", datetime(2026, 9, 27))


def test_nowruz_leap_boundary_relative() -> None:
    ref = datetime(2025, 3, 19, 12, tzinfo=TEHRAN)  # 1403/12/29
    assert one("فردا", ref).value == jalali_to_gregorian(1403, 12, 30)
    assert one("پس‌فردا", ref).value == jalali_to_gregorian(1404, 1, 1)
