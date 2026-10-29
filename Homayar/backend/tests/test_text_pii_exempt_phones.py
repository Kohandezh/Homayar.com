"""HOM-315: a phone written right after a card is read as a phone.

The HOM-298 mission found 32 planted phones that the pre-HOM-291 detector
(``text-pii/4``) read and today's detector reads with no class set (PR #163).  Their
corpus rows are lost, so each case is rebuilt in the known shape: a price whose last
group is ``000``, a Luhn card in four groups of four whose last group starts with
``00``, the phone in a usual written form, and a short number.  On base a ``00``
number that starts in the card takes the phone's digits, and with cards requested one
card span runs from the price over the phone.  Synthetic values only.
"""

from __future__ import annotations

import itertools
import re

import pytest

from backend.privacy.subject_export import SUBJECT_CLASSES, SubjectIdentifier, _Matcher
from backend.privacy.text_pii import PiiClass, detect, valid_luhn

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
SCRIPTS = {"latin": str.maketrans("", ""), "persian": FA, "arabic": AR}
PHONE, ID, CARD = PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.BANK_CARD

# The class lists of the gate tool (HOM-321).
LISTS = {
    "all": frozenset(PiiClass),
    "speech": frozenset({PHONE, ID, PiiClass.EMAIL}),
    "export": frozenset({PiiClass.EMAIL, PHONE, ID, CARD, PiiClass.IBAN}),
    "phone": frozenset({PHONE}),
    "id": frozenset({ID}),
    "card": frozenset({CARD}),
    "postal": frozenset({PiiClass.IR_POSTAL_CODE}),
    "phone+card": frozenset({PHONE, CARD}),
}
PHONE_LISTS = [name for name, classes in LISTS.items() if PHONE in classes]


def national_id(prefix9: str) -> str:
    total = sum(int(prefix9[i]) * (10 - i) for i in range(9)) % 11
    return prefix9 + str(total if total < 2 else 11 - total)


def card(prefix15: str) -> str:
    return next(prefix15 + d for d in "0123456789" if valid_luhn(prefix15 + d))


# One valid identifier per class, written in no text of this file: the other
# classes of an export.
SENTINEL = {
    PiiClass.EMAIL: "sentinel315@example.org",
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


def found(text: str, value: str, combo: frozenset[PiiClass]) -> bool:
    identifiers = [SubjectIdentifier(PHONE, value)] + [
        SubjectIdentifier(other, SENTINEL[other]) for other in combo if other is not PHONE
    ]
    return PHONE.value in _Matcher(identifiers).classes(text)


def exports(text: str, value: str) -> dict[frozenset[PiiClass], bool]:
    """found() for every export combination that holds phones."""
    return {combo: found(text, value, combo) for combo in COMBOS if PHONE in combo}


def phone_values(text: str, classes: frozenset[PiiClass]) -> set[str]:
    return {span.value for span in detect(text, classes) if span.pii_class is PHONE}


def phone_at(latin: str, phone: str) -> list[int]:
    """Where the digits of the planted phone are written."""
    at = [i for i, char in enumerate(latin) if "0" <= char <= "9"]
    k = "".join(latin[i] for i in at).rindex(phone)
    return at[k : k + len(phone)]


# --- the rebuilt cases (SPEC section 4) -----------------------------------------------
# Each value is a phone of the PR #163 list, by corpus and row.  Row 888 is the one
# surviving text; the other rows are rebuilt.  Card prefixes, the 00 group, the price,
# the phone grouping and the separator vary.  K 16315 is a landline.


ROW_888 = "۱۶۴,۴۹۰,۰۰۰ ۶۲۷۸ ۶۱۰۶ ۱۸۴۲ ۰۰۵۱ ۰۹۰۵ ۹۱۸ ۶۱۹۴ ۳۱"  # verbatim, PR #163
CASES = {
    ("K", 888): ("164,490,000 6278 6106 1842 0051 0905 918 6194 31", "09059186194"),
    ("r3fuzz2", 4722): ("164,490,000 6037 9900 0000 0014 0905 841 1311 31", "09058411311"),
    ("r3fuzz2", 4761): (
        "2,350,000\xa05892\xa01000\xa07919\xa00029\xa009123442058\xa07",
        "09123442058",
    ),
    ("K", 1115): ("89,000-6273-5301-5838-0033-0935-7546790-12", "09357546790"),
    ("K", 1176): ("1,200,000–6104–3302–3757–0042–0910–010–16–51–4", "09100101651"),
    ("K", 1914): ("45,000 6221 0603 1676 0051 09372832772 56", "09372832772"),
    ("K", 2184): (
        "12,750,000\xa05022\xa02903\xa09595\xa00066\xa00990\xa01453951\xa09",
        "09901453951",
    ),
    ("K", 2652): ("980,000-6219-8604-7514-0076-0913-596-99-22-23", "09135969922"),
    ("K", 5136): ("164,490,000–6274–1205–5433–0086–0914–716–0739–31", "09147160739"),
    ("K", 5820): ("2,350,000 6393 4606 3352 0091 0901 9029638 7", "09019029638"),
    ("K", 9204): (
        "89,000\xa05859\xa08307\xa01271\xa00012\xa00921\xa0228\xa043\xa049\xa012",
        "09212284349",
    ),
    ("K", 11580): ("1,200,000-6362-1407-9190-0023-0902-811-2130-4", "09028112130"),
    ("K", 13416): ("45,000–5028–0608–7109–0032–09913393660–8", "09913393660"),
    ("K", 14316): ("12,750,000 5047 0609 5028 0048 0901 993 62 01 9", "09019936201"),
    ("K", 15108): (
        "980,000\xa06063\xa07310\xa02947\xa00052\xa00935\xa0477\xa01908\xa023",
        "09354771908",
    ),
    ("K", 15684): ("164,490,000-6274-8811-0866-0068-09222470854-31", "09222470854"),
    ("K", 16315): ("2,350,000–6396–0711–8785–0078–066–7038–4149–7", "06670384149"),
    ("K", 17520): ("89,000 6037 9912 6704 0081 0930 518 1008 12", "09305181008"),
    ("K", 17592): ("1,200,000\xa05892\xa01013\xa04623\xa00093\xa009130184202\xa04", "09130184202"),
    ("K", 19680): ("45,000-6273-5314-2542-0016-0903-4344469-3", "09034344469"),
    ("K", 21300): ("12,750,000–6104–3315–0461–0024–0902–202–36–28–9", "09022023628"),
    ("K", 22524): ("980,000 6221 0615 8380 0034 09909029080 23", "09909029080"),
    ("K", 23100): (
        "164,490,000\xa05022\xa02916\xa06299\xa00041\xa00910\xa07835594\xa031",
        "09107835594",
    ),
    ("K", 23172): ("2,350,000-6219-8617-4218-0051-0915-655-00-89-7", "09156550089"),
    ("K", 23820): ("89,000–6274–1218–2137–0061–0905–021–1665–12", "09050211665"),
    # The card has a second 00 group: phones alone read a 00 number from its third group.
    ("K", 23928): ("1,200,000 6393 4619 0056 0075 0919 1076366 4", "09191076366"),
    ("K", 25512): (
        "45,000\xa05859\xa08319\xa07975\xa00089\xa00910\xa0541\xa060\xa096\xa056",
        "09105416096",
    ),
    ("K", 26160): ("12,750,000-6362-1420-5894-0098-0911-919-5980-9", "09119195980"),
    ("K", 26484): ("980,000–5028–0621–3813–0015–09410219993–23", "09410219993"),
    ("K", 26772): ("164,490,000 5047 0622 1732 0021 0938 249 84 53 6", "09382498453"),
    ("K", 26916): (
        "2,350,000\xa06063\xa07322\xa09651\xa00034\xa00938\xa0078\xa00774\xa07",
        "09380780774",
    ),
    ("K", 27420): ("89,000-6274-8823-7570-0041-09130042310-12", "09130042310"),
}
IDS = [f"{corpus}-{row}" for corpus, row in CASES]
SEPARATOR = re.compile("[ \xa0\\-–]")
MOBILE_FORMS = {(11,), (4, 3, 4), (4, 7), (4, 3, 2, 2)}
LANDLINE_FORMS = {(11,), (3, 4, 4), (3, 8)}


def case_texts():
    for key, (latin, phone) in CASES.items():
        for script, table in SCRIPTS.items():
            yield pytest.param(
                latin.translate(table), latin, phone, id=f"{key[0]}-{key[1]}-{script}"
            )


def test_row_888_is_the_surviving_text():
    assert CASES[("K", 888)][0].translate(FA) == ROW_888


@pytest.mark.parametrize("key", CASES, ids=IDS)
def test_each_case_is_in_the_planted_shape(key):
    latin, phone = CASES[key]
    assert len(set(SEPARATOR.findall(latin))) == 1  # one separator per text
    price, *groups, short = SEPARATOR.split(latin)
    cards, phones = groups[:4], groups[4:]
    assert re.fullmatch("[1-9][0-9]{0,2}(?:,[0-9]{3})*,000", price)
    assert [len(group) for group in cards] == [4, 4, 4, 4]
    assert valid_luhn("".join(cards)) and cards[3].startswith("00")
    assert "".join(phones) == phone
    forms = MOBILE_FORMS if phone.startswith("09") else LANDLINE_FORMS
    assert tuple(len(group) for group in phones) in forms
    assert re.fullmatch("[1-9][0-9]?", short)


# --- AC2: the phone is read, and the export finds it ----------------------------------


@pytest.mark.parametrize(("text", "latin", "phone"), case_texts())
def test_the_phone_is_read_with_the_phone_class_list(text, latin, phone):
    assert phone in phone_values(text, LISTS["phone"])


@pytest.mark.parametrize(("text", "latin", "phone"), case_texts())
def test_the_export_finds_the_phone_in_every_combination(text, latin, phone):
    answers = exports(text, phone)
    assert len(answers) == 16
    assert all(answers.values())


# --- AC3: every class list that holds phones covers the phone -------------------------


@pytest.mark.parametrize(("text", "latin", "phone"), case_texts())
@pytest.mark.parametrize("name", PHONE_LISTS)
def test_the_phone_is_covered(name, text, latin, phone):
    spans = detect(text, LISTS[name])
    assert all(any(s.start <= i < s.end for s in spans) for i in phone_at(latin, phone))


@pytest.mark.parametrize("key", CASES, ids=IDS)
def test_without_the_short_number_the_card_lists_already_read_the_phone(key):
    # Regression guard, not an exempt case: with no short number after the phone,
    # the card and the phone are read on base too.
    latin, phone = CASES[key]
    short = SEPARATOR.split(latin)[-1]
    text = latin[: -len(short) - 1]
    for name in ("all", "export", "phone+card"):
        assert phone in phone_values(text, LISTS[name]), name


# --- AC5: deny cases, each next to its closest allow-control --------------------------

LUHN_888 = "164,490,000 6278 6106 1842 0051 0905 918 6194 31"
MOBILE_888 = "09059186194"


# D1: a 00 number written on its own keeps today's reading.
LONE_00 = {
    # The 00 number of row 888 with no card before it.  Its 17 digits also pass
    # Luhn, so the card lists read it as a card.
    "row_888": ("0051 0905 918 6194 31", "00510905918619431", ("K", 888)),
    "row_4722": ("0014 0905 841 1311 31", "00140905841131131", ("r3fuzz2", 4722)),
}


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("case", LONE_00)
def test_d1_a_00_number_with_no_card_before_it_keeps_its_reading(case, script):
    latin, value, key = LONE_00[case]
    text = latin.translate(SCRIPTS[script])
    assert phone_values(text, LISTS["phone"]) == {value}
    assert not any(exports(text, CASES[key][1]).values())


@pytest.mark.parametrize("case", LONE_00)
def test_d1_allow_after_a_luhn_card_the_export_finds_the_mobile(case):
    latin, phone = CASES[LONE_00[case][2]]
    assert all(exports(latin, phone).values())


# D2: four groups of four that are not a Luhn card (one digit changed).
NOT_LUHN = "164,490,000 6278 6106 1843 0051 0905 918 6194 31"
NOT_LUHN_PHONES = {"00510905918619431"}  # what base reads, in every list that holds phones


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("name", LISTS)
def test_d2_no_new_phone_value_when_the_groups_are_not_a_luhn_card(name, script):
    assert not valid_luhn("6278610618430051")
    text = NOT_LUHN.translate(SCRIPTS[script])
    assert phone_values(text, LISTS[name]) <= NOT_LUHN_PHONES
    assert not any(exports(text, MOBILE_888).values())


def test_d2_allow_the_luhn_card_lets_the_mobile_be_read():
    assert valid_luhn("6278610618420051")
    assert MOBILE_888 in phone_values(LUHN_888, LISTS["phone"])


# D3: the digits after the card are not a usual phone.
NOT_A_PHONE = {
    "0805": ("164,490,000 6278 6106 1842 0051 0805 918 6194 31", {"00510805918619431"}),
    "one_digit_missing": ("164,490,000 6278 6106 1842 0051 0905 918 619 31", {"0051090591861931"}),
    # The mobile's digits in 3-3-5 groups, not a usual written form.
    "not_written": ("164,490,000 6278 6106 1842 0051 090 591 86194 31", {"00510905918619431"}),
}


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("name", LISTS)
@pytest.mark.parametrize("case", NOT_A_PHONE)
def test_d3_no_new_phone_value_when_no_usual_phone_follows_the_card(case, name, script):
    latin, base_values = NOT_A_PHONE[case]
    text = latin.translate(SCRIPTS[script])
    assert phone_values(text, LISTS[name]) <= base_values
    assert not any(exports(text, MOBILE_888).values())


def test_d3_allow_the_planted_phone_is_read():
    assert MOBILE_888 in phone_values(LUHN_888, LISTS["phone"])


# D4: a mobile one digit away from the planted one.
NEAR_MOBILES = ["09059186195", "09059186184", "09159186194", "09059186294"]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("near", NEAR_MOBILES)
def test_d4_the_export_for_a_mobile_one_digit_away_does_not_find_the_text(near, script):
    text = LUHN_888.translate(SCRIPTS[script])
    assert sum(a != b for a, b in zip(near, MOBILE_888, strict=True)) == 1
    assert not any(exports(text, near).values())


@pytest.mark.parametrize("script", SCRIPTS)
def test_d4_allow_the_export_for_the_planted_mobile_finds_the_text(script):
    assert all(exports(LUHN_888.translate(SCRIPTS[script]), MOBILE_888).values())


# D5: the documented 00 reading that starts at an ID stays as it is.
ID_THEN_00 = "0039626921 0090 453 3473 896 2"


@pytest.mark.parametrize("script", SCRIPTS)
def test_d5_a_00_reading_that_starts_at_an_id_keeps_its_span_and_value(script):
    text = ID_THEN_00.translate(SCRIPTS[script])
    spans = detect(text, LISTS["phone"])
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (0, len(text), PHONE, "00396269210090453")
    ]


@pytest.mark.parametrize("script", SCRIPTS)
def test_d5_allow_with_ids_requested_the_id_is_read(script):
    text = ID_THEN_00.translate(SCRIPTS[script])
    for classes in (frozenset({ID}), frozenset({PHONE, ID})):
        assert (ID, "0039626921") in {(s.pii_class, s.value) for s in detect(text, classes)}


# D6 (review, turn 2): after the card, a second phone in a usual written form starts
# inside the first one and ends after it (``0917 087-0441`` and ``087-0441 9135``).  The
# text reads two ways, so it keeps its base reading, and the export still finds the
# landline base read.
TWO_PHONES = "46430 679 0756 0079 5423 6445 0917 087-0441 9135"
TWO_PHONES_SCRIPTS = ("latin", "persian")


@pytest.mark.parametrize("script", TWO_PHONES_SCRIPTS)
def test_d6_two_overlapping_written_phones_keep_the_base_reading(script):
    text = TWO_PHONES.translate(SCRIPTS[script])
    spans = detect(text, LISTS["phone"])
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (15, 34, PHONE, "0079542364450917"),
        (35, 48, PHONE, "08704419135"),
    ]


@pytest.mark.parametrize("script", TWO_PHONES_SCRIPTS)
def test_d6_the_export_still_finds_the_landline_base_read(script):
    assert all(exports(TWO_PHONES.translate(SCRIPTS[script]), "08704419135").values())


# D7 (turn 2): a landline reading that starts in the card's last group also reads across
# the card and the phone (``0309 0938 524`` in ``0018-0309 0938 524 6112``).  It is not
# used either: phones alone read the mobile, and the export for the landline finds
# nothing, as on base.
CARD_LANDLINE = "4976 2550 0018-0309 0938 524 6112–4919 thanks"


@pytest.mark.parametrize("script", TWO_PHONES_SCRIPTS)
def test_d7_a_landline_that_starts_in_the_card_is_not_read(script):
    text = CARD_LANDLINE.translate(SCRIPTS[script])
    spans = detect(text, LISTS["phone"])
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (10, 33, PHONE, "09385246112")
    ]
    assert not any(exports(text, "03090938524").values())


@pytest.mark.parametrize("script", TWO_PHONES_SCRIPTS)
def test_d7_allow_the_export_finds_the_mobile_after_the_card(script):
    assert all(exports(CARD_LANDLINE.translate(SCRIPTS[script]), "09385246112").values())


# D8 (turn 2): a chance reading inside the card (``98`` and ``92 9593 0097``, read as the
# mobile ``09295930097``) must not become a new value when the 00 reading is not used.
# The reading again may add only the phone after the card, so this text keeps its base
# reading, and the export for the chance mobile finds nothing.
CARD_CHANCE_98 = "9892\xa09593-0097-3822 0924\xa0289 77-78"


@pytest.mark.parametrize("script", TWO_PHONES_SCRIPTS)
def test_d8_a_chance_reading_inside_the_card_is_not_a_new_value(script):
    text = CARD_CHANCE_98.translate(SCRIPTS[script])
    spans = detect(text, LISTS["phone"])
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (0, 34, PHONE, "00973822092428977")
    ]
    assert not any(exports(text, "09295930097").values())
