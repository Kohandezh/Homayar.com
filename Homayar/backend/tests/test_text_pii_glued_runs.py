"""HOM-303: digits of a real identifier stay raw next to another number.

Up to text-pii/5 three shapes left identifier digits raw in a redacted view:

A. a number glued to a national ID or a phone, with no separator (now read
   for a mobile when its group reads in one way only: a plain check digit or
   the landline pattern would read too many other numbers);
B. a card next to a short number in the same run (the card regex reads 13 to
   19 digits from the first digit it meets, so a short number before or after
   a card, or a second card, uses up the digits the card needs);
C. a regex reading that uses up the start of the next number (a national ID
   read as the start of a ``00`` international number).

Synthetic values only.
"""

from __future__ import annotations

import random
import re

import pytest

from backend.privacy.text_pii import PiiClass, detect, redact, valid_luhn

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
NBSP = " "
EN_DASH = "–"

ALL = list(PiiClass)
SPEECH = [PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.EMAIL]


def national_id(prefix9: str) -> str:
    total = sum(int(prefix9[i]) * (10 - i) for i in range(9)) % 11
    return prefix9 + str(total if total < 2 else 11 - total)


def card(prefix15: str) -> str:
    return next(prefix15 + d for d in "0123456789" if valid_luhn(prefix15 + d))


NID = "0012345679"
NID_B = national_id("274659381")  # no "00" start
MOBILE = "09123456789"
LANDLINE = "02188776655"
C1 = "6037991199500004"  # the SPEC cards, both Luhn-valid
C2 = "6219861034529007"
INTL = "0044 20 7946 0958"


def grouped(card_digits: str, sep: str = " ") -> str:
    return sep.join(card_digits[i : i + 4] for i in range(0, 16, 4))


def _script(parts, table):
    return [(text.translate(table) if table else text, ident) for text, ident in parts]


def _raw_identifier_digits(parts, classes=ALL) -> list[int]:
    """Offsets of identifier digits (parts marked True) outside every span."""
    text = "".join(text for text, _ in parts)
    covered = {i for s in detect(text, classes) for i in range(s.start, s.end)}
    raw, cursor = [], 0
    for chunk, ident in parts:
        if ident:
            span = range(cursor, cursor + len(chunk))
            raw += [i for i in span if text[i].isdigit() and i not in covered]
        cursor += len(chunk)
    return raw


SCRIPTS = [("latin", None), ("persian", FA), ("arabic", AR)]


def _params(cases):
    for name, parts, classes in cases:
        for script, table in SCRIPTS:
            yield pytest.param(_script(parts, table), classes, id=f"{name}-{script}")


# --- SPEC section 1: the leader's rows -------------------------------------------

SPEC_ROWS = [
    ("B1", [(grouped(C1), True), (" ", False), (grouped(C2), True), (" 12", False)], ALL),
    ("B2", [("12 ", False), (grouped(C1), True), (" ", False), (grouped(C2), True)], ALL),
    ("B3", [(grouped(C1), True), (" 12 ", False), (grouped(C2), True)], ALL),
    ("B4", [(C1, True), (" ", False), (C2, True), (" 12", False)], ALL),
    ("C1", [(NID, True), (" ", False), (INTL, True)], ALL),
]


@pytest.mark.parametrize(("parts", "classes"), list(_params(SPEC_ROWS)))
def test_spec_rows_leave_no_identifier_digit_raw(parts, classes):
    assert _raw_identifier_digits(parts, classes) == [], "".join(t for t, _ in parts)


# --- case A: a short number glued to a mobile -------------------------------------

GLUED = [
    (f"{label}-{side}-{short}", parts, [PiiClass.PHONE])
    for label, prefix, mobile, shorts in [
        ("mobile", "", MOBILE, ("7", "27", "613")),
        ("mobile-b", "موبایل ", "09351234567", ("7", "33", "613")),
    ]
    for short in shorts
    for side, parts in [
        ("before", [(prefix + short, False), (mobile, True)]),
        ("after", [(prefix, False), (mobile, True), (short, False)]),
    ]
]


@pytest.mark.parametrize(("parts", "classes"), list(_params(GLUED)))
def test_a_mobile_with_a_glued_short_number_is_read(parts, classes):
    for class_list in (classes, ALL, SPEECH):
        assert _raw_identifier_digits(parts, class_list) == [], "".join(t for t, _ in parts)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("709123456789", [(PiiClass.PHONE, MOBILE)]),
        ("091234567897", [(PiiClass.PHONE, MOBILE)]),
        ("61309123456789", [(PiiClass.PHONE, MOBILE)]),
        (f"{NID} 27{MOBILE}", [(PiiClass.IR_NATIONAL_ID, NID), (PiiClass.PHONE, MOBILE)]),
    ],
)
def test_a_glued_mobile_keeps_its_class_and_value(text, expected):
    for written in (text, text.translate(FA)):
        assert [(s.pii_class, s.value) for s in detect(written, SPEECH)] == expected, written


def test_the_glued_phone_keeps_its_own_span_next_to_an_id():
    text = f"{NID} 27{MOBILE}"
    assert [(text[s.start : s.end], s.pii_class) for s in detect(text)] == [
        (NID, PiiClass.IR_NATIONAL_ID),
        (MOBILE, PiiClass.PHONE),
    ]


# Not detected safely (see the text_pii docstring).  A national ID or a
# landline glued to other digits: a check digit holds by chance in about 1 of
# 10 windows, and the landline pattern 0[1-8] + 9 digits in about 8 of 100, so
# random 12-14 digit numbers would go from 0-10% to 34-60% redacted.  An ID
# keyword does not help: the gate corpus has random numbers after it.  A group
# that reads in two ways is a guess (G3: the export would return texts about
# another value).  A mobile may carry at most three glued digits.
@pytest.mark.parametrize(
    "text",
    [
        "12340012345679",  # SPEC row A2
        "1200123456790",  # SPEC row A3
        "کد ملی 12340012345679",
        f"7{NID}",
        f"{NID_B}12",
        f"7{LANDLINE}",
        f"{LANDLINE}1403",
        # 12 + mobile, or the valid ID 2091234567 + 789 (SPEC row A1).
        "1209123456789",
        # Four digits glued to a mobile: +2.2 points at 15 digits, over the limit.
        f"1403{MOBILE}",
        f"{MOBILE}1403",
    ],
)
def test_a_glued_id_landline_two_way_or_long_glue_is_not_read(text):
    for written in (text, text.translate(FA)):
        assert detect(written) == [], written


def test_spec_row_a1_keeps_its_base_output():
    text = f"{NID} 12{MOBILE}"
    for written, expected in (
        (text, "[IR_NATIONAL_ID_1] 1209123456789"),
        (text.translate(FA), "[IR_NATIONAL_ID_1] ۱۲۰۹۱۲۳۴۵۶۷۸۹"),
    ):
        assert redact(written)[0] == expected


# --- case B: a card next to a short number or another card -----------------------

CARD_RUNS = [
    (f"{name}-{sep_name}", parts, [PiiClass.BANK_CARD])
    for sep_name, sep in [("space", " "), ("hyphen", "-"), ("nbsp", NBSP), ("dash", EN_DASH)]
    for name, parts in [
        ("short-after", [(grouped(C1, sep), True), (f"{sep}12", False)]),
        ("short-before", [(f"7{sep}", False), (grouped(C1, sep), True)]),
        ("two-cards-short-after", [(C1, True), (sep, False), (C2, True), (f"{sep}12", False)]),
        (
            "two-cards-short-between",
            [(grouped(C1, sep), True), (f"{sep}5{sep}", False), (grouped(C2, sep), True)],
        ),
    ]
]


@pytest.mark.parametrize(("parts", "classes"), list(_params(CARD_RUNS)))
def test_a_card_next_to_a_short_number_is_read(parts, classes):
    for class_list in (classes, ALL, [PiiClass.PHONE, PiiClass.BANK_CARD]):
        assert _raw_identifier_digits(parts, class_list) == [], "".join(t for t, _ in parts)


@pytest.mark.parametrize(
    "parts",
    [
        [(NID, True), (" 7 ", False), (grouped(C1), True)],
        [(MOBILE, True), (" 12 ", False), (grouped(C1), True)],
        [("+44 20 7946 0958", True), (" ", False), (grouped(C1), True)],
    ],
)
def test_a_card_after_another_identifier_and_a_short_number_is_read(parts):
    for table in (None, FA):
        assert _raw_identifier_digits(_script(parts, table)) == []


@pytest.mark.parametrize(
    "text",
    [f"{grouped(C1)} {grouped(C2)} 12", f"12 {C1} {C2}", f"{grouped(C1)} 12 {grouped(C2)}"],
)
def test_two_cards_and_a_short_number_give_two_cards(text):
    for written in (text, text.translate(FA)):
        spans = detect(written)
        assert [(s.pii_class, s.value) for s in spans] == [
            (PiiClass.BANK_CARD, C1),
            (PiiClass.BANK_CARD, C2),
        ], written


# --- case C: a regex reading uses up the start of the next number ----------------


@pytest.mark.parametrize(
    "text",
    # NID_B does not start with "00": base already reads it right (control).
    [f"{NID} {INTL}", f"{NID}-0044-20-7946-0958", f"{NID_B} {INTL}", f"{NID} {INTL} 12"],
)
def test_an_id_then_an_international_number_is_an_id_and_a_phone(text):
    for written in (text, text.translate(FA)):
        spans = detect(written)
        assert [(s.pii_class, s.value) for s in spans][:2] == [
            (PiiClass.IR_NATIONAL_ID, text[:10]),
            (PiiClass.PHONE, "00442079460958" + ("12" if text.endswith(" 12") else "")),
        ], written
        assert all(
            not c.isdigit() for c in redact(written)[0].replace("_1]", "").replace("_2]", "")
        ), written


def test_the_space_the_base_span_covered_stays_covered():
    # Base read "0012345679 0044 20" as one span, the space included.  The ID
    # grows over that space, so no character base redacted is shown again.
    for written in (f"{NID} {INTL}", f"{NID} {INTL}".translate(FA)):
        assert redact(written)[0] == "[IR_NATIONAL_ID_1][PHONE_1]"


# --- not detected safely, or out of scope: pinned base output --------------------
# See the "NOT detected" paragraph of the text_pii docstring for each reason.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A card in groups of four next to a number of 3 or more digits: the
        # same shape is a price list (1 to 11 more price-list rows of 20,000).
        ("6037 9911 9950 0004 123", "6037 9911 9950 0004 123"),
        ("123 6037 9911 9950 0004", "123 6037 9911 9950 0004"),
        ("1234567 6037 9911 9950 0004", "1234567 6037 9911 9950 0004"),
        ("0912 345 6789 123 6037 9911 9950 0004", "[PHONE_1] 123 6037 9911 9950 0004"),
        # A short number glued to the first or last group of a grouped phone
        # (26-32% more price-list rows).
        ("120912 345 6789", "120912 345 6789"),
        # Two identifiers glued in one group with a glued short number (2.7% to
        # 32% more 21-26 digit codes).
        ("00123456791209123456789", "00123456791209123456789"),
        ("001234567912091234567891", "001234567912091234567891"),
        # A card as one group with only short numbers around it: a random
        # 16-digit number there passes Luhn 1 time in 10 (G3: the export would
        # return texts that hold no card).
        (f"12 {C1} 34", f"12 {C1} 34"),
        (f"{C1} 7", f"{C1} 7"),
        ("call 39 5996690023350377 now", "call 39 5996690023350377 now"),
        # Out of scope: a card with glued digits, and a 00/+ number with a
        # digit glued before it.
        ("60379911995000047", "60379911995000047"),
        ("76037991199500004", "76037991199500004"),
        ("6037 9911 9950 00046219 8610 3452 9007", "6037 [BANK_CARD_1] 8610 3452 9007"),
        ("70044 20 7946 0958", "70044 20 7946 0958"),
        ("7+44 20 7946 0958", "7+44 20 7946 0958"),
    ],
)
def test_shapes_not_detected_keep_their_base_output(text, expected):
    assert redact(text)[0] == expected
    # Digits of the text, not the placeholder numbers (those follow "_").
    persian = re.sub(r"(?<![_0-9])[0-9]+", lambda m: m.group().translate(FA), expected)
    assert redact(text.translate(FA))[0] == persian


@pytest.mark.parametrize(
    "text",
    [
        # 13 digits pass Luhn by chance; a written card has 16 digits.
        "+98 911 156 5207-8091653861170",
        # Four groups of four that hold the whole 00 number 0049 3826 9693.
        "کارت 007–75–61–9411–1653–0049–3826–9693",
    ],
)
def test_a_written_card_has_16_digits_and_holds_no_other_reading(text):
    for written in (text, text.translate(FA)):
        assert detect(written, [PiiClass.BANK_CARD]) == [], written


def test_a_mobile_glued_inside_a_phone_list_is_read():
    text = "+98 911 156 5207-8091653861170"
    assert redact(text)[0] == "[PHONE_1]-8[PHONE_2]0"


def test_a_chance_luhn_hit_inside_another_reading_is_not_a_card():
    # 0034 9582 3347 11 is a 00 number; the 14 digits also pass Luhn.  Base
    # reads no card here, with any class list, and neither does the patch.
    text = "کد ملی 64841 00349582334711 است"
    assert detect(text, [PiiClass.BANK_CARD]) == []
    assert [s.pii_class for s in detect(text)] == [PiiClass.PHONE]


# --- SPEC section 3: deny-cases and allow-controls -------------------------------


@pytest.mark.parametrize(
    "text",
    ["12340012345678", "120000 45000 3500000 98000", "12 6037991199500005 34"],
)
def test_deny_cases_get_no_span(text):
    for written in (text, text.translate(FA)):
        assert detect(written) == [], written


def test_an_international_number_alone_stays_one_phone():
    for written in (INTL, INTL.translate(FA)):
        assert [(s.start, s.end, s.pii_class, s.value) for s in detect(written)] == [
            (0, len(INTL), PiiClass.PHONE, "00442079460958")
        ]


def test_an_id_after_a_separator_is_read():
    for written in ("1234 0012345679", "1234 0012345679".translate(FA)):
        assert [(s.pii_class, s.value) for s in detect(written)] == [(PiiClass.IR_NATIONAL_ID, NID)]


def test_a_phone_inside_a_price_list_is_covered():
    # Base reads the list as two chance cards that hold the phone; any class
    # will do as long as no digit of the phone stays raw.
    parts = [("120000 45000 ", False), (MOBILE, True), (" 3500000 98000", False)]
    for table in (None, FA):
        assert _raw_identifier_digits(_script(parts, table)) == []


# --- review round 1 ------------------------------------------------------------------

C3 = "6104338418140683"
C4 = "6273539475267963"


@pytest.mark.parametrize(
    ("text", "values"),
    [
        # Finding 1: a one-group card cut off from another card by a short number.
        (f"{C1} 7 {C2}", [C1, C2]),
        (f"{C1} 12 {C2}", [C1, C2]),
        (f"{C1} 7 {grouped(C2)}", [C1, C2]),
        (f"{C3} 4–6273 5394 7526 7963", [C3, C4]),
        (f"{C1} 123 {C2}", [C1, C2]),
        (f"{grouped(C1)} 123 {grouped(C2)}", [C1, C2]),
        # Finding 2: a card on one side and three digits on the other.
        (f"{C1} {C2} 123", [C1, C2]),
        (f"123 {C1} {C2}", [C1, C2]),
        (f"{grouped(C1)} {C2} 123", [C1, C2]),
        (f"{C1} {grouped(C2)} 123", [C1, C2]),
    ],
)
def test_a_card_next_to_another_card_past_a_short_number_is_read(text, values):
    for written in (text, text.translate(FA)):
        spans = detect(written)
        assert [(s.pii_class, s.value) for s in spans] == [
            (PiiClass.BANK_CARD, value) for value in values
        ], written


@pytest.mark.parametrize(
    ("parts", "classes"),
    [
        # Finding 3: a chance card of a class that was not requested holds the group.
        (
            [("3998095845-128", False), ("09316593052", True)],
            [PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.EMAIL],
        ),
        ([("01826407857-747", False), ("09380261211", True), ("", False)], SPEECH),
        ([("01826407857-747", False), ("09380261211", True)], [PiiClass.PHONE]),
        ([("09314489417" + NBSP + "61", False), ("09396952460", True)], SPEECH),
    ],
)
def test_a_glued_mobile_is_read_when_only_an_unrequested_class_holds_its_group(parts, classes):
    for table in (None, FA):
        assert _raw_identifier_digits(_script(parts, table), classes) == []


def test_each_written_card_keeps_its_own_span_and_value():
    # Finding 5: the written readings of a run are used together.
    text = "٢٠٨٦٩ 5022-2914-7811-7854–6104-3306-9339-2881 6037–9960–9969–0706"
    assert [(s.pii_class, s.value) for s in detect(text)] == [
        (PiiClass.BANK_CARD, "5022291478117854"),
        (PiiClass.BANK_CARD, "6104330693392881"),
        (PiiClass.BANK_CARD, "6037996099690706"),
    ]


def test_without_ids_a_00_reading_still_takes_the_next_number():
    # Finding 6, pinned: one span over both numbers, valued as the 00 reading
    # that starts at the ID (the value base gives with phones only).
    text = "0039626921 0090 453 3473 896 2"
    spans = detect(text, [PiiClass.PHONE, PiiClass.BANK_CARD])
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (0, len(text), PiiClass.PHONE, "00396269210090453")
    ]


def test_the_digit_patterns_agree_with_the_phone_regexes():
    # Finding 7: the glued-mobile check uses its own digit patterns; they must
    # accept exactly the 11-digit strings the mobile and landline regexes accept.
    from backend.privacy.text_pii import (
        _IR_LANDLINE_RE,
        _IR_MOBILE_RE,
        _LANDLINE_DIGITS_RE,
        _MOBILE_DIGITS_RE,
    )

    rng = random.Random(3037)  # noqa: S311 - deterministic test inputs, not security
    for prefix in range(1000):
        head = f"{prefix:03d}"
        for _ in range(3):
            digits = head + "".join(rng.choice("0123456789") for _ in range(8))
            assert bool(_MOBILE_DIGITS_RE.fullmatch(digits)) == bool(
                _IR_MOBILE_RE.fullmatch(digits) and digits.startswith("0")
            ), digits
            assert bool(_LANDLINE_DIGITS_RE.fullmatch(digits)) == bool(
                _IR_LANDLINE_RE.fullmatch(digits)
            ), digits


def test_every_digit_of_every_written_reading_is_redacted():
    # Finding 8, white box: like the HOM-291 candidate check, for _written.
    from backend.privacy.text_pii import _candidates, _matches, _written
    from backend.speech.text_normalize import normalize_digits_extended, unify_arabic_letters

    rng = random.Random(3038)  # noqa: S311 - deterministic test inputs, not security
    tokens = [
        NID,
        MOBILE,
        LANDLINE,
        C1,
        C2,
        grouped(C1),
        grouped(C2, "-"),
        INTL,
        "+44 20 7946 0958",
    ]
    tokens += ["7", "12", "123", "1403", "98000", "613"]
    separators = ["", " ", "-", NBSP, EN_DASH]
    classes = [
        ALL,
        SPEECH,
        [PiiClass.PHONE],
        [PiiClass.BANK_CARD],
        [PiiClass.PHONE, PiiClass.BANK_CARD],
    ]
    for _ in range(3000):
        text = "".join(
            rng.choice(separators) + rng.choice(tokens) for _ in range(rng.randint(2, 5))
        )
        if rng.random() < 0.3:
            text = text.translate(FA)
        wanted = rng.choice(classes)
        covered = {i for s in detect(text, wanted) for i in range(s.start, s.end)}
        norm = normalize_digits_extended(text)
        found = _matches(norm, unify_arabic_letters(norm))
        runs: list[tuple[int, int]] = []
        _candidates(norm, frozenset(wanted), found, runs=runs)
        for spans in _written(norm, frozenset(wanted), found, runs):
            for span in spans:
                for index in range(span.start, span.end):
                    if norm[index] in "0123456789":
                        assert index in covered, (text, wanted, span)


# --- review round 2: disclosed shapes, pinned -------------------------------------


def test_a_written_card_can_merge_with_a_longer_chance_reading():
    # Pinned: every digit is covered, but the last two cards share one span
    # valued as the 17-digit chance card reading.
    text = "5892 1055 0986 5157".translate(AR) + "–6037 9933 1777 7936-9023–6219 8698 7018 1320"
    spans = detect(text)
    assert [(s.pii_class, s.value) for s in spans] == [
        (PiiClass.BANK_CARD, "5892105509865157"),
        (PiiClass.BANK_CARD, "60379933177779369"),
    ]
    covered = {i for s in spans for i in range(s.start, s.end)}
    assert all(i in covered for i, c in enumerate(text) if c.isdigit())


def test_a_glued_mobile_held_by_a_longer_chance_card_is_not_read_without_cards():
    # Pinned: the chance card 102 1209316593052 holds the glued group; ignoring
    # such a longer reading gave a new false subject-export match (G3).
    for written in ("102 1209316593052", "102 1209316593052".translate(FA)):
        assert detect(written, SPEECH) == [], written
        assert redact(written)[0] == "[BANK_CARD_1]"
