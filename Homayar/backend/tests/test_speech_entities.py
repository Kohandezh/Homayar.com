"""Entity extraction from synthetic Persian/English transcripts."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from backend.speech.dates import TEHRAN
from backend.speech.entities import extract_entities

REF = datetime(2026, 9, 27, 10, 0, tzinfo=TEHRAN)


def by_type(text: str, **kw):
    ents = extract_entities(text, REF, **kw)
    for e in ents:
        if e.type != "person_name_slot":
            assert text[e.start : e.end] == e.raw
    out: dict[str, list] = {}
    for e in ents:
        out.setdefault(e.type, []).append(e)
    return out


@pytest.mark.parametrize(
    "text",
    [
        "شماره من ۰۹۱۲ ۳۴۵ ۶۷۸۹ است",
        "شماره‌ام 09123456789",
        "با +98 912 345 6789 تماس بگیرید",
        "۰۰۹۸۹۱۲۳۴۵۶۷۸۹",
    ],
)
def test_phone(text: str) -> None:
    ents = by_type(text)
    assert [e.value for e in ents["phone"]] == ["09123456789"]
    assert ents["phone"][0].source.startswith("privacy.text_pii")


def test_national_id_valid_only() -> None:
    assert by_type("کد ملی ۰۰۱۲۳۴۵۶۷۹")["national_id"][0].value == "0012345679"
    assert "national_id" not in by_type("کد ملی ۰۰۱۲۳۴۵۶۷۶")


def test_email() -> None:
    assert by_type("ایمیلم Ali.R@Example.com هست")["email"][0].value == "ali.r@example.com"


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("مبلغ پانصد هزار تومان", 500000, "IRT"),
        ("۵۰۰ هزار تومان", 500000, "IRT"),
        ("۲,۵۰۰,۰۰۰ ریال", 2500000, "IRR"),
        ("۲٬۵۰۰٬۰۰۰ ریال", 2500000, "IRR"),
        ("سه میلیون و دویست هزار تومن", 3200000, "IRT"),
        ("بیست و پنج هزار تومان بدهکارم", 25000, "IRT"),
        ("it costs 250 thousand tomans", 250000, "IRT"),
        ("1200 rials", 1200, "IRR"),
    ],
)
def test_amounts(text: str, amount: int, currency: str) -> None:
    ents = by_type(text)["amount"]
    assert len(ents) == 1
    assert ents[0].value == {"amount": amount, "currency": currency}


def test_currency_word_alone_is_not_amount() -> None:
    assert "amount" not in by_type("قیمت به تومان چنده؟")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("کد پیگیری ۴۵۶۷۸۹ رو دارم", "456789"),
        ("شماره سفارش: A-12345", "A-12345"),
        ("order number AB-12345 please", "AB-12345"),
        ("ticket #778812", "778812"),
        ("کد رهگیری 99887766", "99887766"),
    ],
)
def test_reference_ids(text: str, value: str) -> None:
    assert by_type(text)["reference_id"][0].value == value


def test_digits_without_keyword_are_not_reference() -> None:
    assert "reference_id" not in by_type("۴۵۶۷۸۹ تا")


def test_dates_and_times_through_dates_module() -> None:
    ents = by_type("فردا ساعت ۱۰ صبح واریز می‌کنم")
    assert ents["date"][0].value == date(2026, 9, 28)
    assert ents["time"][0].value == time(10)
    assert ents["date"][0].source == "speech.dates:relative_day"


def test_ambiguous_time_has_no_value_but_candidates() -> None:
    e = by_type("ساعت ۴ بیاید")["time"][0]
    assert e.value is None and e.ambiguous
    assert e.candidates == (time(4), time(16))


def test_name_slot_never_guesses() -> None:
    for text in ("سلام، اسمم علی هست", "my name is Sara Karimi", "نام من رضا است"):
        slots = by_type(text)["person_name_slot"]
        assert len(slots) == 1
        assert slots[0].value is None
        assert slots[0].confidence < 0.5


def test_name_slots_can_be_disabled() -> None:
    assert "person_name_slot" not in by_type("اسمم علی هست", include_name_slots=False)


def test_phone_digits_do_not_become_amount_or_time() -> None:
    ents = by_type("ساعت ۱۰ با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید")
    assert set(ents) == {"time", "phone"}


def test_full_call_transcript() -> None:
    text = (
        "سلام، اسمم مریم هست. شماره‌ام ۰۹۳۵۱۱۱۲۲۳۳ است، کد پیگیری ۷۷۱۲۳۴ رو دارم. "
        "می‌خواستم بگم پنجشنبه آینده ساعت ۶ عصر مبلغ دویست هزار تومان رو واریز می‌کنم."
    )
    ents = extract_entities(text, REF)
    types = [e.type for e in ents]
    assert types == ["person_name_slot", "phone", "reference_id", "date", "time", "amount"]
    starts = [e.start for e in ents]
    assert starts == sorted(starts)
    date_ent = ents[3]
    assert date_ent.candidates[0] == date(2026, 10, 1)
    assert ents[4].value == time(18)
    assert ents[5].value == {"amount": 200000, "currency": "IRT"}
