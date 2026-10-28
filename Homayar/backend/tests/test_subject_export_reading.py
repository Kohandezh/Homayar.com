"""HOM-298: whether a text matches a subject's identifier does not depend on the
other classes of the export.

The export reads each class on its own (HOM-291).  A mobile is also found when the
reading of phones saw an identifier on those digits but read it as another value, and
another class, read on its own, explains the cut (the re-cut rule of
``subject_export``).  Values that only a chance reading of several classes gives must
not match: each deny case below is such a value, next to its closest allow case.
Landlines, IDs and cards are not read this way.  Synthetic values only.
"""

from __future__ import annotations

import itertools
import re

import pytest

from backend.privacy import subject_export, text_pii
from backend.privacy.subject_export import SUBJECT_CLASSES, SubjectIdentifier, _Matcher
from backend.privacy.text_pii import PiiClass, detect, valid_luhn
from backend.tests._cost import lines_run

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
SCRIPTS = {"latin": str.maketrans("", ""), "persian": FA, "arabic": AR}
PHONE, ID, CARD = PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.BANK_CARD


def national_id(prefix9: str) -> str:
    total = sum(int(prefix9[i]) * (10 - i) for i in range(9)) % 11
    return prefix9 + str(total if total < 2 else 11 - total)


def card(prefix15: str) -> str:
    return next(prefix15 + d for d in "0123456789" if valid_luhn(prefix15 + d))


# One valid identifier per class, written in no text of this file: the other
# classes of an export.
SENTINEL = {
    PiiClass.EMAIL: "sentinel298@example.org",
    PHONE: "09990001112",
    ID: national_id("987654329"),
    CARD: card("603799999999999"),
    PiiClass.IBAN: "IR062960000000100324200001",
}
COMBOS = [
    frozenset(combo)
    for size in range(1, 6)
    for combo in itertools.combinations(sorted(SUBJECT_CLASSES, key=lambda c: c.value), size)
]


def found(text: str, pii_class: PiiClass, value: str, combo: frozenset[PiiClass]) -> bool:
    identifiers = [SubjectIdentifier(pii_class, value)] + [
        SubjectIdentifier(other, SENTINEL[other]) for other in combo if other is not pii_class
    ]
    return pii_class.value in _Matcher(identifiers).classes(text)


def exports(text: str, pii_class: PiiClass, value: str) -> dict[frozenset[PiiClass], bool]:
    """found() for every export combination that holds the class."""
    return {combo: found(text, pii_class, value, combo) for combo in COMBOS if pii_class in combo}


def combined_only(text: str, pii_class: PiiClass, value: str) -> bool:
    """A reading of several classes gives the value and the class alone does not."""
    alone = {span.value for span in detect(text, {pii_class})}
    return value not in alone and any(
        value in {span.value for span in detect(text, combo) if span.pii_class is pii_class}
        for combo in COMBOS
        if len(combo) > 1 and pii_class in combo
    )


# --- SPEC section 3: the two required examples, in all three digit scripts ----------


TICKET = "6219 6997 6901 0086 0931 397 0789"
HOM291 = "0012345679 0912 345 6789"


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_ticket_phone_is_found_in_every_export_that_holds_phones(script):
    text = TICKET.translate(SCRIPTS[script])
    assert combined_only(text, PHONE, "09313970789")
    assert all(exports(text, PHONE, "09313970789").values())
    assert all(exports(text, CARD, "6219699769010086").values())


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_hom291_phone_and_id_are_found_in_every_export(script):
    text = HOM291.translate(SCRIPTS[script])
    assert all(exports(text, PHONE, "09123456789").values())
    assert all(exports(text, ID, "0012345679").values())


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_international_reading_of_the_class_alone_still_matches(script):
    # The re-cut rule adds values; it takes none away (X2).
    text = TICKET.translate(SCRIPTS[script])
    assert all(exports(text, PHONE, "008609313970789").values())
    text = HOM291.translate(SCRIPTS[script])
    assert all(exports(text, PHONE, "00123456790912345").values())


# --- allow cases: a phone the reading of phones alone cut wrongly -----------------------


ALLOW = {
    # An ID, then a mobile: phones alone read "0069411344 0939 190" as one 00 number.
    "id_then_mobile": ("please 0069411344 0939 190 6234 items order", "09391906234"),
    # A card in four groups, then a mobile with one separator.
    "card_then_mobile": ("6393–4694–0674–0125–0938 390 1788", "09383901788"),
    # A card whose last group starts with 00, then a mobile and a price.
    "card_00_then_mobile": ("5892 1023 7867 0040-0990–869–3888 447,124,000", "09908693888"),
    # Persian digits, an ID and a mobile joined by one space.
    "persian_id_then_mobile": ("۰۰۲۶۵۱۵۷۸۴ ۰۹۱۳-۵۰۱-۶۰۳۵", "09135016035"),
}


@pytest.mark.parametrize("case", ALLOW)
def test_a_phone_that_its_own_reading_cut_wrongly_is_found_in_every_export(case):
    text, value = ALLOW[case]
    assert combined_only(text, PHONE, value)
    assert all(exports(text, PHONE, value).values())


# --- deny cases: chance readings of several classes do not match ---------------------
# Each value is read by some detect(T, K) with several classes and not by its class
# alone; the closest allow case is named.


DENY = {
    # A landline cut from the last groups of a card (closest: card_then_mobile).
    "landline_from_a_card": ("055130–90408999 6037–7070–2540–0809", PHONE, "07025400809"),
    # A landline and an ID cut from a mobile glued to other digits (closest:
    # id_then_mobile).
    "landline_from_a_glued_mobile": ("code 0915-509-42302804508382 6 was me", PHONE, "02804508382"),
    "id_from_a_glued_mobile": ("code 0915-509-42302804508382 6 was me", ID, "0915509423"),
    # An ID across groups of a card and a date (closest: the HOM-291 ID).
    "id_across_groups": ("٥٠٢٢ ٢٩٩٩ ٠٢٩٢ ٠٠٣٣٠٧٤–٣٤٧٤–٤٩٧٧ ١٤٠٢/٠١/٣٠", ID, "0292003307"),
    # Landlines are not re-cut: a real landline after an ID has the same written shape
    # as random digits after an ID, and a chance landline is far more common than a
    # chance mobile (closest: persian_id_then_mobile).
    "id_then_landline": ("نام ٠٠٦١٧٥٩٦٠٠-٠٢٦ ٨٣٥٤ ٤٧٣٤ ٢٧٠ هزار شماره", PHONE, "02683544734"),
    # A landline that is the glued tail of digits after a mobile (closest:
    # id_then_landline).
    "glued_landline_twin": ("۰۹۳۸ ۸۶۹ ۵۷۴۰۲۹۷۸۱۴۶۱۸۴ ۵۸۵ thanks", PHONE, "02978146184"),
    "glued_id_twin": ("۰۹۳۸ ۸۶۹ ۵۷۴۰۲۹۷۸۱۴۶۱۸۴ ۵۸۵ thanks", ID, "0938869574"),
    # A 0098 number whose 14 digits pass Luhn: read alone it is a card (closest:
    # id_then_mobile).
    "0098_number_that_is_a_card": ("شماره ۰۰۵۴۵۸۹۶۰۶۰۰۹۸–۹۳۰–۲۵۲–۳۹۱۹", PHONE, "09302523919"),
    # Four four-digit prices that pass Luhn, after an ID: cards are not re-read
    # (closest: the same text without the last price, card_beside_an_id below).
    "card_in_a_price_list": ("6400417648 7230 5740 9972 7571 7812 or", CARD, "7230574099727571"),
}


@pytest.mark.parametrize("case", DENY)
def test_a_chance_reading_of_several_classes_matches_no_export(case):
    text, pii_class, value = DENY[case]
    assert combined_only(text, pii_class, value)  # the combined call would match it
    assert not any(exports(text, pii_class, value).values())


def test_the_card_beside_an_id_is_still_found_by_the_card_reading():
    # The allow case of card_in_a_price_list: the card reading alone finds it (main).
    text = "6400417648 7230 5740 9972 7571 or"
    assert all(exports(text, CARD, "7230574099727571").values())


def test_a_real_card_in_the_shape_of_a_price_list_is_not_recovered():
    # Pinned limit: an ID, a card and a date have the shape of card_in_a_price_list,
    # so cards are not read by the re-cut rule.  The export for cards misses it, as
    # on main.
    text = "9218057758 6276–4887–2587–4631 1402/01/02"
    assert combined_only(text, CARD, "6276488725874631")
    assert not any(exports(text, CARD, "6276488725874631").values())


# --- one deny case per guard of the rule ---------------------------------------------
# Each deny case is rejected by that guard alone: removing the guard (in _recut and in
# _may_recut) makes the export find it.  Each allow case is the closest text the rule
# accepts.

GUARDS = {
    # (a) a written identifier cuts into the value: the chance card 6765 7300 9489 2308
    # takes the mobile's last group.
    "overlap": (
        "0021315841-0995 833 6765 7300\xa09489 2308",
        "0021315841-0995 833 6765",
        "09958336765",
    ),
    # (c) a 0098 number whose 14 digits pass Luhn: written alone it is a card.
    "0098_luhn": (
        "0064444112 0098\xa0907\xa0543\xa00979",
        "0064444112 0098\xa0907\xa0543\xa00978",
        None,
    ),
    # (d) the value is not in a usual written form of a mobile (3-3-5 groups).
    "written_form": ("0054163250 099 386 18188", "0054163250 0993 861 8188", "09938618188"),
    # (e) the cutter is not in a usual written form: an ID in 5+5 groups (read by the
    # ID class alone) takes the 00 group that phones alone read over the mobile.
    "cutter_written": ("22104 00120–0994 751 6810", "0012345679–0994 751 6810", "09947516810"),
    # (f) mobiles only: an ID, then a landline; the allow case is an ID, then a mobile.
    "mobile_only": (
        "نام ٠٠٦١٧٥٩٦٠٠-٠٢٦ ٨٣٥٤ ٤٧٣٤ ٢٧٠ هزار شماره",
        "۰۰۲۶۵۱۵۷۸۴ ۰۹۱۳-۵۰۱-۶۰۳۵",
        None,
    ),
}
GUARD_VALUES = {
    "0098_luhn": ("09075430979", "09075430978"),
    "mobile_only": ("02683544734", "09135016035"),
}


def _guard_case(name: str) -> tuple[str, str, str, str]:
    deny, allow, value = GUARDS[name]
    deny_value, allow_value = GUARD_VALUES.get(name, (value, value))
    return deny, deny_value, allow, allow_value


@pytest.mark.parametrize("name", GUARDS)
def test_each_guard_rejects_its_deny_case(name):
    deny, value, _, _ = _guard_case(name)
    assert combined_only(deny, PHONE, value)
    assert not any(exports(deny, PHONE, value).values())


@pytest.mark.parametrize("name", GUARDS)
def test_the_closest_allow_case_of_each_guard_is_found(name):
    _, _, allow, value = _guard_case(name)
    assert combined_only(allow, PHONE, value)
    assert all(exports(allow, PHONE, value).values())


# --- A1: one answer per value, whatever the other classes ----------------------------


A1_TEXTS = [
    TICKET,
    *(text for deny, allow, _ in GUARDS.values() for text in (deny, allow)),
    TICKET.translate(FA),
    HOM291.translate(AR),
    *(text for text, _ in ALLOW.values()),
    *(text for text, _, _ in DENY.values()),
    "0012345679 0912 345 6789 6037991199500004 IR062960000000100324200001",
    "کد ملی ۰۰۱۲۳۴۵۶۷۹۰۹۱۲۳۴۵۶۷۸۹ ۲ بار",
    "0039626921 0090 453 3473 896 2",
]


@pytest.mark.parametrize("text", A1_TEXTS, ids=range(len(A1_TEXTS)))
def test_every_value_matches_the_same_way_in_every_export(text):
    values = {
        (span.pii_class, span.value)
        for combo in COMBOS
        for span in detect(text, combo)
        if span.pii_class in SUBJECT_CLASSES
    }
    assert values
    for pii_class, value in sorted(values):
        answers = set(exports(text, pii_class, value).values())
        assert len(answers) == 1, (pii_class, value)


def test_a_value_matches_the_same_way_whatever_the_other_values_of_its_class():
    text = TICKET
    alone = _Matcher([SubjectIdentifier(PHONE, "09313970789")]).classes(text)
    with_others = _Matcher(
        [
            SubjectIdentifier(PHONE, "09313970789"),
            SubjectIdentifier(PHONE, "008609313970789"),
            SubjectIdentifier(PHONE, SENTINEL[PHONE]),
        ]
    ).classes(text)
    assert alone == with_others == ["phone"]


# --- cost: the re-cut rule reads more only where it can match -------------------------


def _count_readings(monkeypatch) -> list[frozenset[PiiClass]]:
    calls: list[frozenset[PiiClass]] = []
    real = subject_export._detect_in

    def counted(shared, classes):
        calls.append(classes)
        return real(shared, classes)

    monkeypatch.setattr(subject_export, "_detect_in", counted)
    return calls


def test_no_other_reading_when_no_wanted_value_can_be_re_cut(monkeypatch):
    calls = _count_readings(monkeypatch)
    identifiers = [SubjectIdentifier(c, SENTINEL[c]) for c in SUBJECT_CLASSES]
    for text in A1_TEXTS:
        _Matcher(identifiers).classes(text)
    # One reading per class and text: the sentinels are written in no text.
    assert len(calls) == len(SUBJECT_CLASSES) * len(A1_TEXTS)
    assert all(len(classes) == 1 for classes in calls)


def test_the_re_cut_rule_reads_more_only_for_a_value_its_class_covered(monkeypatch):
    calls = _count_readings(monkeypatch)
    assert _Matcher([SubjectIdentifier(PHONE, "09313970789")]).classes(TICKET) == ["phone"]
    # Phones, then phones with cards, then cards alone to check the cut: found.
    assert calls == [frozenset({PHONE}), frozenset({PHONE, CARD}), frozenset({CARD})]
    calls.clear()
    # A phone the phone reading did not cover: no other reading.
    assert _Matcher([SubjectIdentifier(PHONE, "09123456789")]).classes(TICKET) == []
    assert calls == [frozenset({PHONE})]


def test_each_class_set_is_read_once_per_text(monkeypatch):
    calls = _count_readings(monkeypatch)
    identifiers = [SubjectIdentifier(PHONE, "09391906234"), SubjectIdentifier(ID, "0069411344")]
    text = ALLOW["id_then_mobile"][0]
    assert _Matcher(identifiers).classes(text) == ["ir_national_id", "phone"]
    assert len(calls) == len(set(calls))


# --- what the re-cut rule may return --------------------------------------------------


@pytest.mark.parametrize("case", ALLOW)
def test_a_re_cut_value_is_a_value_of_some_detect_reading(case):
    text, value = ALLOW[case]
    readings = {
        span.value for combo in COMBOS for span in detect(text, combo) if span.pii_class is PHONE
    }
    assert value in readings


def test_an_international_number_or_a_card_is_never_re_cut():
    assert subject_export._spellings(PHONE, "008609313970789") == []
    assert subject_export._spellings(CARD, "6219699769010086") == []
    assert subject_export._spellings(PHONE, "0") == []
    assert subject_export._spellings(ID, "0") == []


def test_the_run_separators_are_the_ones_of_text_pii():
    chars = {chr(code) for code in range(0x3000) if re.fullmatch(text_pii._SEP, chr(code))}
    assert chars == set(subject_export._SEPARATORS)


# --- cost: a repeated denied value costs linear work (HOM-307 style) -------------------
# Lines of text_pii and subject_export one call runs; the count is the same on every
# machine.  A value the rule denies is tried at each place it is written, so each try
# must cost about its own neighbourhood, not the whole text.  The export may grow no
# faster than detect for phones alone on the same text, the work main does (that one
# grows a little faster than linear on its own, in text_pii's selection step).

COST_MODULES = (text_pii.__name__, subject_export.__name__)
REPEATED_DENIALS = {
    # rejected inside the rule: the cutter is not written (an ID in 5+5 groups)
    "cutter_written": ("22104 00120–0994 751 6810 و ", "09947516810"),
    # rejected by the first test: a written chance card cuts into the mobile
    "overlap": ("0021315841-0995 833 6765 7300\xa09489 2308 و ", "09958336765"),
}


@pytest.mark.parametrize("case", REPEATED_DENIALS)
def test_a_repeated_denied_value_costs_linear_work(case):
    unit, value = REPEATED_DENIALS[case]
    matcher = _Matcher([SubjectIdentifier(PHONE, value)])
    small, found = lines_run(COST_MODULES, matcher.classes, unit * 100)
    assert found == []
    alone_small, _ = lines_run(COST_MODULES, detect, unit * 100, {PHONE})
    alone_large, _ = lines_run(COST_MODULES, detect, unit * 400, {PHONE})
    growth = alone_large / alone_small  # about 4 to 5.3 for 4x the text
    large, _ = lines_run(COST_MODULES, matcher.classes, unit * 400, stop_at=int(5 * growth * small))
    assert large < 1.1 * growth * small, (small, large, growth)
