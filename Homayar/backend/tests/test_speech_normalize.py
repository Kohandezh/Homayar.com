"""Persian/English/Azerbaijani text normalisation (synthetic strings only)."""

from __future__ import annotations

import pytest

from backend.speech.text_normalize import (
    DEFAULT_COLLOQUIAL_FA,
    ZWNJ,
    get_normalizer,
    normalize,
    normalize_az,
    normalize_en,
    normalize_fa,
    normalize_for_matching,
    normalize_zwnj,
    register_normalizer,
    registered_languages,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("علي", "علی"),
        ("كتاب", "کتاب"),
        ("مدرسة", "مدرسه"),
        ("أحمد", "احمد"),
        ("إيران", "ایران"),
        ("مؤسسه", "موسسه"),
        ("مسئله", "مسئله"),  # yeh-hamza is valid Persian and kept
        ("سـلام", "سلام"),  # tatweel
        ("کِتابْ", "کتاب"),  # diacritics
        ("مُحَمَّد", "محمد"),
        ("۱۲۳ و ٤٥٦", "123 و 456"),
        ("  سلام   دنیا \n خوبی ", "سلام دنیا خوبی"),
    ],
)
def test_normalize_fa_letters_digits_space(raw: str, expected: str) -> None:
    assert normalize_fa(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("می خواهم", f"می{ZWNJ}خواهم"),
        ("نمی دانم", f"نمی{ZWNJ}دانم"),
        ("میروم", "میروم"),  # no space: left alone (not guessed)
        ("کتاب ها", f"کتاب{ZWNJ}ها"),
        ("کتاب های من", f"کتاب{ZWNJ}های من"),
        (f"می{ZWNJ}{ZWNJ}روم", f"می{ZWNJ}روم"),
        (f"می {ZWNJ} روم", f"می{ZWNJ}روم"),
        (f"{ZWNJ}سلام{ZWNJ}", "سلام"),
    ],
)
def test_zwnj(raw: str, expected: str) -> None:
    assert normalize_zwnj(raw) == expected


def test_zwnj_does_not_touch_standalone_mi_word() -> None:
    # "هامون" does not start with the plural suffix token.
    assert normalize_fa("دریاچه هامون") == "دریاچه هامون"


def test_punctuation_fa() -> None:
    assert normalize_fa("سلام , خوبی ?") == "سلام، خوبی؟"
    assert normalize_fa("«سلام»") == '"سلام"'
    assert normalize_fa("سلام ,خوبی", punctuation=False) == "سلام ,خوبی"


def test_colloquial_is_opt_in() -> None:
    assert normalize_fa("من میخوام برم") == "من میخوام برم"
    assert (
        normalize_fa("من میخوام برم", colloquial=DEFAULT_COLLOQUIAL_FA) == f"من می{ZWNJ}خواهم برم"
    )
    assert (
        normalize_fa("میشه کمکم کنین", colloquial=DEFAULT_COLLOQUIAL_FA) == f"می{ZWNJ}شود کمکم کنید"
    )
    assert normalize_fa("یه سوال دارم", colloquial=DEFAULT_COLLOQUIAL_FA) == "یک سوال دارم"


def test_colloquial_custom_table() -> None:
    assert normalize_fa("اینو بده", colloquial={"اینو": "این را"}) == "این را بده"


def test_colloquial_arabic_letters_resolved_first() -> None:
    assert normalize_fa("ميخوام", colloquial=DEFAULT_COLLOQUIAL_FA) == f"می{ZWNJ}خواهم"


def test_english_and_azerbaijani() -> None:
    assert normalize_en("  It’s   ۱۰ o'clock ") == "It's 10 o'clock"
    assert normalize_az("Mən Azərbaycanlıyam") == "Mən Azərbaycanlıyam"
    assert normalize_az("Azärbaycan") == "Azərbaycan"
    assert normalize_az("آذربايجان ديلي") == "آذربایجان دیلی"


def test_registry() -> None:
    assert {"fa", "en", "az"} <= set(registered_languages())
    assert get_normalizer("fa-IR") is normalize_fa
    assert get_normalizer("AZ_Latn") is normalize_az
    with pytest.raises(KeyError):
        get_normalizer("xx")
    register_normalizer("ckb", lambda t, **_: t.upper(), "test only")
    assert normalize("ab", "ckb") == "AB"


def test_normalize_for_matching_collapses_variants() -> None:
    a = normalize_for_matching("كتاب‌هاي من ، ۲ تا")
    b = normalize_for_matching("کتاب ها ی من 2 تا")
    assert a == "کتابهای من 2 تا"
    assert normalize_for_matching("Hello, World!", "en") == "hello world"
    assert b.startswith("کتابها")


def test_idempotent() -> None:
    s = "كتاب هاي من رو مي خوام ۱۲ تا ?"
    once = normalize_fa(s)
    assert normalize_fa(once) == once
