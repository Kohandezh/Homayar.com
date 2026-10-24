"""HOM-291: a national ID directly next to a phone number is two identifiers.

Up to text-pii/4 the detector kept the longest candidate, so a bank card or an
``00`` international number read across the boundary between the two won, and
the digits of the phone outside that candidate stayed in the redacted text.
Synthetic values only.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from backend.privacy.text_pii import PiiClass, detect, redact, valid_luhn
from backend.speech.text_normalize import normalize_digits_extended
from backend.tests._cost import lines_run

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
NBSP = " "
EN_DASH = "–"


def national_id(prefix9: str) -> str:
    total = sum(int(prefix9[i]) * (10 - i) for i in range(9)) % 11
    return prefix9 + str(total if total < 2 else 11 - total)


def card(prefix15: str) -> str:
    return next(prefix15 + d for d in "0123456789" if valid_luhn(prefix15 + d))


NID = "0012345679"  # the SPEC pair
MOBILE = "09123456789"
NID_B = national_id("005678912")  # 0056789122: a second "00" ID (AC2)
MOBILE_B = "09351234567"
LANDLINE_B = "02188776655"
NID_C = national_id("274659381")  # 2746593815: no "00" start, a card wins on base
CARD = card("603799112345678")
CARD_NID_PREFIX = card(national_id("603799112") + "34567")  # first 10 digits pass the ID check


def _raw_digits(redacted: str) -> str:
    """Digits of any script left after the placeholders are removed."""
    return "".join(c for c in re.sub(r"\[[A-Z_]+_[0-9]+\]", "", redacted) if c.isdigit())


def _shapes(nid: str, phone: str) -> dict[str, tuple[str, str, str, str]]:
    """SPEC section 1 cases 1 to 7 as (prefix, ID, joint, phone) parts."""
    p = phone
    return {
        "case1-keyword": ("کد ملی ", nid, " ", f"{p[:4]} {p[4:7]} {p[7:]}"),
        "case2-spaces": ("", nid, " ", f"{p[:4]} {p[4:7]} {p[7:]}"),
        "case3-hyphens": ("", nid, "-", f"{p[:4]}-{p[4:7]}-{p[7:]}"),
        "case4-space-hyphens": ("", nid, " ", f"{p[:4]}-{p[4:7]}-{p[7:]}"),
        "case5-joined-groups": ("", nid, "", f"{p[:4]} {p[4:7]} {p[7:]}"),
        "case6-two-groups": ("", nid, " ", f"{p[:4]} {p[4:]}"),
        "case7-all-joined": ("", nid, "", p),
    }


def _assert_two_spans(parts, nid_value: str, phone_value: str) -> None:
    prefix, nid, joint, phone = parts
    text = prefix + nid + joint + phone
    start = len(prefix)
    phone_start = start + len(nid) + len(joint)
    spans = detect(text)
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (start, start + len(nid), PiiClass.IR_NATIONAL_ID, nid_value),
        (phone_start, len(text), PiiClass.PHONE, phone_value),
    ], text
    assert [text[s.start : s.end] for s in spans] == [nid, phone]
    redacted, _ = redact(text)
    assert _raw_digits(redacted) == "", redacted


def _cases(pairs):
    for nid, phone in pairs:
        for name, parts in _shapes(nid, phone).items():
            for script, table in (("latin", None), ("persian", FA)):
                translated = tuple(part.translate(table) if table else part for part in parts)
                yield pytest.param(translated, nid, phone, id=f"{nid}-{phone}-{name}-{script}")


# --- AC1 and AC2: cases 1 to 7, both digit scripts, three pairs of values -----


@pytest.mark.parametrize(("parts", "nid", "phone"), list(_cases([(NID, MOBILE)])))
def test_spec_cases_give_the_id_then_the_phone(parts, nid, phone):
    _assert_two_spans(parts, nid, phone)


@pytest.mark.parametrize(
    ("parts", "nid", "phone"),
    list(_cases([(NID_B, MOBILE_B), (NID_B, LANDLINE_B), (NID_C, MOBILE)])),
)
def test_other_ids_mobiles_and_landlines_split_the_same_way(parts, nid, phone):
    _assert_two_spans(parts, nid, phone)


@pytest.mark.parametrize(
    "parts",
    [
        ("", NID_B, " ", "021 8877 6655"),
        ("", NID_B, "-", "021-88776655"),
        ("", NID_B, " ", "021 88776655"),
        ("تلفن ", NID_B.translate(FA), " ", "۰۲۱ ۸۸۷۷ ۶۶۵۵"),
    ],
)
def test_a_landline_in_its_usual_groups_after_an_id(parts):
    _assert_two_spans(parts, NID_B, LANDLINE_B)


# --- other spellings of the same digits ------------------------------------------


@pytest.mark.parametrize(
    ("parts", "phone"),
    [
        (("", NID, NBSP, f"0912{NBSP}345{NBSP}6789"), MOBILE),
        (("", NID, EN_DASH, f"0912{EN_DASH}345{EN_DASH}6789"), MOBILE),
        (("", NID.translate(FA), " ", "0912 345 6789"), MOBILE),
        (("", NID, " ", "0912 345 6789".translate(FA)), MOBILE),
        (("", NID.translate(FA), "", "0912 345 6789".translate(AR)), MOBILE),
        (("", NID, " ", "0098 912 345 6789"), MOBILE),
        (("", NID, "", "0098 912 345 6789"), MOBILE),
        (("", NID, "", "989123456789"), MOBILE),
        (("", NID, " ", "+98 912 345 6789"), MOBILE),
        (("", NID, "", "02188776655"), LANDLINE_B),
    ],
    ids=[
        "nbsp",
        "en-dash",
        "persian-id-latin-phone",
        "latin-id-persian-phone",
        "persian-id-arabic-phone-joined",
        "0098-prefix",
        "0098-prefix-joined",
        "98-prefix-joined",
        "plus98-prefix",
        "landline-joined",
    ],
)
def test_other_separators_scripts_and_prefixes(parts, phone):
    _assert_two_spans(parts, NID, phone)


@pytest.mark.parametrize(
    "text",
    [
        f"{MOBILE}{NID}",
        f"0912 345 6789{NID}",
        f"0912-345-6789 {NID}",
        f"{LANDLINE_B}{NID_B}",
    ],
)
def test_a_phone_first_and_then_the_id(text):
    spans = detect(text)
    assert [s.pii_class for s in spans] == [PiiClass.PHONE, PiiClass.IR_NATIONAL_ID]
    assert [_raw_digits(text[s.start : s.end]) for s in spans] == [
        _raw_digits(text)[:11],
        _raw_digits(text)[11:],
    ]
    assert _raw_digits(redact(text)[0]) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"{NID} 0912 345 6789 {NID_B}",
            [PiiClass.IR_NATIONAL_ID, PiiClass.PHONE, PiiClass.IR_NATIONAL_ID],
        ),
        (
            f"{NID}{MOBILE}{NID_B}",
            [PiiClass.IR_NATIONAL_ID, PiiClass.PHONE, PiiClass.IR_NATIONAL_ID],
        ),
        (
            f"{NID}-{MOBILE_B}-{LANDLINE_B}",
            [PiiClass.IR_NATIONAL_ID, PiiClass.PHONE, PiiClass.PHONE],
        ),
        (f"{NID}{NID_B}", [PiiClass.IR_NATIONAL_ID, PiiClass.IR_NATIONAL_ID]),
    ],
    ids=["id-phone-id", "id-phone-id-joined", "id-mobile-landline", "two-ids-joined"],
)
def test_three_identifiers_in_a_row(text, expected):
    assert [s.pii_class for s in detect(text)] == expected
    assert _raw_digits(redact(text)[0]) == ""


def test_the_cross_boundary_reading_is_still_found_and_loses_the_selection():
    # The card regex and Luhn still accept the joined digits (the change is in
    # the selection, not in a regex that stopped matching).
    text = f"{NID} 0912 345 6789"
    cards = detect(text, [PiiClass.BANK_CARD])
    assert [(s.start, s.end, s.value) for s in cards] == [(0, 19, "00123456790912345")]
    assert [s.pii_class for s in detect(text)] == [PiiClass.IR_NATIONAL_ID, PiiClass.PHONE]


def test_the_selection_covers_every_candidate_digit_on_mixed_inputs():
    # AC3, white box: every digit inside any internal candidate is redacted.
    from backend.privacy.text_pii import _candidates

    rng = random.Random(291)  # noqa: S311 - deterministic test inputs, not security
    tokens = [
        NID,
        NID_B,
        NID_C,
        MOBILE,
        MOBILE_B,
        LANDLINE_B,
        CARD,
        "0098 912 345 6789",
        "+98 912 345 6789",
        "0044 20 7946 0958",
        "1234567890",
        "0012345678",
        "4567",
        "تماس",
        "a@b.io",
    ]
    separators = ["", " ", "-", NBSP, EN_DASH, "  ", ", "]
    for _ in range(3000):
        parts = [rng.choice(tokens) for _ in range(rng.randint(1, 5))]
        text = ""
        for part in parts:
            text += rng.choice(separators) + (part.translate(FA) if rng.random() < 0.3 else part)
        covered = set()
        for span in detect(text):
            covered.update(range(span.start, span.end))
        norm = normalize_digits_extended(text)
        matches, pieces = _candidates(norm, frozenset(PiiClass))
        for candidate in matches + pieces:
            for index in range(candidate.start, candidate.end):
                if norm[index] in "0123456789":
                    assert index in covered, (text, candidate)


def test_no_digit_of_identifiers_joined_in_one_run_stays_raw():
    rng = random.Random(2910)  # noqa: S311 - deterministic test inputs, not security
    tokens = [NID, NID_B, NID_C, MOBILE, MOBILE_B, LANDLINE_B, CARD, "0098 912 345 6789"]
    separators = ["", " ", "-", NBSP, EN_DASH]
    for _ in range(3000):
        parts = [rng.choice(tokens) for _ in range(rng.randint(2, 4))]
        text = parts[0]
        for part in parts[1:]:
            text += rng.choice(separators) + part
        if rng.random() < 0.5:
            text = text.translate(FA)
        assert _raw_digits(redact(text)[0]) == "", text


# --- allow-controls: unchanged from base -------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"{NID} {MOBILE}", [(PiiClass.IR_NATIONAL_ID, NID), (PiiClass.PHONE, MOBILE)]),
        (
            f"{NID} {MOBILE}".translate(FA),
            [(PiiClass.IR_NATIONAL_ID, NID), (PiiClass.PHONE, MOBILE)],
        ),
        ("0044 20 7946 0958", [(PiiClass.PHONE, "00442079460958")]),
        ("+98 912 345 6789", [(PiiClass.PHONE, MOBILE)]),
        ("+98 912 345 6789".translate(FA), [(PiiClass.PHONE, MOBILE)]),
        (f"کد ملی {NID.translate(FA)}", [(PiiClass.IR_NATIONAL_ID, NID)]),
        ("موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹", [(PiiClass.PHONE, MOBILE)]),
        (f"کارت {CARD.translate(FA)}", [(PiiClass.BANK_CARD, CARD)]),
        # 21 joined digits that do not split into an ID and a phone.
        ("123456789012345678901", []),
        ("001234567809123456789", []),  # 0012345678 fails the check digit
        ("۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹۰۱", []),
    ],
)
def test_allow_controls_keep_their_base_result(text, expected):
    assert [(s.pii_class, s.value) for s in detect(text)] == expected


@pytest.mark.parametrize(
    "text",
    [
        CARD,
        f"{CARD[:4]} {CARD[4:8]} {CARD[8:12]} {CARD[12:]}",
        f"{CARD[:4]}-{CARD[4:8]}-{CARD[8:12]}-{CARD[12:]}",
        CARD_NID_PREFIX,
        " ".join(CARD_NID_PREFIX[i : i + 4] for i in range(0, 16, 4)),
        f"{CARD_NID_PREFIX[:10]} {CARD_NID_PREFIX[10:]}",
        f"{CARD[:4]} {CARD[4:8]} {CARD[8:12]} {CARD[12:]}".translate(FA),
    ],
)
def test_a_real_card_stays_one_card(text):
    spans = detect(text)
    assert [(s.start, s.end, s.pii_class) for s in spans] == [(0, len(text), PiiClass.BANK_CARD)]
    assert spans[0].value == _raw_digits(text).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


@pytest.mark.parametrize(
    "text",
    [f"{CARD} {MOBILE}", f"{CARD_NID_PREFIX} {MOBILE}", f"{CARD} 0912 345 6789".translate(FA)],
)
def test_a_card_then_a_space_and_a_phone_is_a_card_and_a_phone(text):
    spans = detect(text)
    assert [(s.pii_class, s.value) for s in spans] == [
        (PiiClass.BANK_CARD, spans[0].value),
        (PiiClass.PHONE, MOBILE),
    ]
    assert spans[0].value in {CARD, CARD_NID_PREFIX}


def test_an_unsplittable_overlap_is_redacted_whole():
    # 0012345678 fails the check digit: no ID, so the "00" international
    # reading and the mobile cannot both be kept.  Over-redaction is the safe
    # direction: no digit of either stays raw.
    text = "0012345678 0912 3456789"
    assert _raw_digits(redact(text)[0]) == ""


# --- AC8: no exponential search -------------------------------------------------------


def _text_pii_lines(text: str) -> tuple[int, str]:
    """Lines of backend.privacy.text_pii code that one redact(text) call runs."""
    count, (redacted, _) = lines_run(("backend.privacy.text_pii",), redact, text)
    return count, redacted


# Lines of text_pii code one redact call runs on the 5000-char inputs, measured on
# the base (Python 3.12.13): id-phone 737,113; ids-joined 321,459; id-phone-joined
# 1,379,797; single-digits 873,539; unsplittable 576,600; cards 534,628.  The
# ceiling is about 9x the largest count, like the old 1.0 s budget.
WORK_CEILING = 12_500_000
# Backstop for work in C the line count cannot see (a regex blow-up), in CPU
# seconds of this thread, like HOM-301 test_system_health.py.
WIDE_BOUND_SECONDS = 10.0


@pytest.mark.parametrize(
    "text",
    [
        (f"{NID} 0912 345 6789 " * 200)[:5000],
        (NID * 500)[:5000],
        (f"{NID}{MOBILE}" * 240)[:5000],
        " ".join(str(i % 10) for i in range(2500))[:5000],
        ("0012345678 0912 3456789 " * 210)[:5000],
        (f"{CARD} " * 300)[:5000],
    ],
    ids=["id-phone", "ids-joined", "id-phone-joined", "single-digits", "unsplittable", "cards"],
)
def test_a_long_segment_full_of_numbers_does_bounded_linear_work(text):
    full_count, redacted = _text_pii_lines(text)
    half_count, _ = _text_pii_lines(text[:2500])
    assert 0 < half_count
    assert full_count < WORK_CEILING
    assert full_count < 3 * half_count  # twice the text costs about twice the work
    started = time.thread_time()  # this thread's CPU time: load on other threads is not counted
    redact(text)
    assert time.thread_time() - started < WIDE_BOUND_SECONDS
    assert redacted


# --- review of turn 1: a short neighbour, class lists, chance card pieces -------


@pytest.mark.parametrize(
    ("text", "neighbour"),
    [
        ("کد ملی 00123456790912 345 6789 2 بار", "2"),
        ("سال 1403 001234567909123456789", "1403"),
        ("کد ملی ۰۰۱۲۳۴۵۶۷۹۰۹۱۲۳۴۵۶۷۸۹ ۲ بار", "۲"),
        ("1403-0012345679-0912-345-6789", "1403"),
        (f"{MOBILE}{NID} 7", "7"),
    ],
)
def test_a_short_number_in_the_same_run_does_not_stop_the_split(text, neighbour):
    spans = detect(text)
    assert sorted((s.pii_class, s.value) for s in spans) == sorted(
        [(PiiClass.IR_NATIONAL_ID, NID), (PiiClass.PHONE, MOBILE)]
    )
    assert _raw_digits(redact(text)[0]) == neighbour


def test_a_short_number_next_to_joined_identifiers_leaves_none_of_them_raw():
    rng = random.Random(2911)  # noqa: S311 - deterministic test inputs, not security
    tokens = [NID, NID_B, NID_C, MOBILE, MOBILE_B, LANDLINE_B, CARD, "0912 345 6789"]
    separators = [" ", "-", NBSP, EN_DASH]
    for _ in range(3000):
        parts = [rng.choice(tokens) for _ in range(rng.randint(2, 3))]
        if all(part == CARD for part in parts):
            continue  # cards only: a split that skips the short number needs an ID or a phone
        body = parts[0] + "".join(rng.choice(["", *separators]) + part for part in parts[1:])
        short = str(rng.randint(0, 9999))
        lead = rng.random() < 0.5
        joint = rng.choice(separators)
        text = f"{short}{joint}{body}" if lead else f"{body}{joint}{short}"
        if rng.random() < 0.5:
            text = text.translate(FA)
        offset = len(short) + len(joint) if lead else 0
        covered = {i for s in detect(text) for i in range(s.start, s.end)}
        raw = [
            i for i in range(offset, offset + len(body)) if text[i].isdigit() and i not in covered
        ]
        assert raw == [], text


PHONE_NEXT_TO_ID = "کد ملی 0939287757 0924 762 1682"


@pytest.mark.parametrize(
    ("classes", "expected"),
    [
        ([PiiClass.PHONE], [(PiiClass.PHONE, "0924 762 1682", "09247621682")]),
        ([PiiClass.IR_NATIONAL_ID], [(PiiClass.IR_NATIONAL_ID, "0939287757", "0939287757")]),
    ],
)
def test_a_class_list_reads_a_run_only_with_its_own_classes(classes, expected):
    # Base output: a split that needs a class the caller did not ask for is
    # not built, so the real phone keeps its value and no false ID appears.
    spans = detect(PHONE_NEXT_TO_ID, classes)
    assert [(s.pii_class, PHONE_NEXT_TO_ID[s.start : s.end], s.value) for s in spans] == expected


def test_the_subject_export_still_finds_a_phone_next_to_an_id():
    from backend.privacy.subject_export import _Matcher, parse_subject_identifier

    matcher = _Matcher([parse_subject_identifier("09247621682")])
    assert matcher.classes(PHONE_NEXT_TO_ID) == ["phone"]


def test_a_chance_card_piece_does_not_swallow_a_real_id():
    # 7110 3407309570 passes Luhn by chance, but it holds the whole ID match
    # 3407309570: the ID keeps its base reading, 7110 may stay raw.
    text = "0990 161 0084 7110 3407309570"
    assert [(s.pii_class, text[s.start : s.end], s.value) for s in detect(text)] == [
        (PiiClass.PHONE, "0990 161 0084", "09901610084"),
        (PiiClass.IR_NATIONAL_ID, "3407309570", "3407309570"),
    ]


def test_a_class_list_with_both_classes_still_splits_a_glued_pair():
    spans = detect(f"{NID}{MOBILE}", [PiiClass.PHONE, PiiClass.IR_NATIONAL_ID])
    assert [(s.pii_class, s.value) for s in spans] == [
        (PiiClass.IR_NATIONAL_ID, NID),
        (PiiClass.PHONE, MOBILE),
    ]


# --- final review: same-class cuts, merged values, card-only edge splits -------

CARD_THEN_ID = "6037 1569 8864 0987 0652128981"


@pytest.mark.parametrize(
    ("text", "pii_class", "expected"),
    [
        # Two chance Luhn pieces cut the real card in two (one lies in it,
        # one runs from it into the ID).
        (CARD_THEN_ID, PiiClass.BANK_CARD, [("6037 1569 8864 0987", "6037156988640987")]),
        # The mobile and the landline 037 1331 9499 are both regex matches: the
        # span covers both (no digit raw) and keeps the mobile's value.
        ("0937 037 1331 9499 3635191116", PiiClass.PHONE, [("0937 037 1331 9499", "09370371331")]),
    ],
)
def test_a_chance_piece_does_not_cut_a_real_match_of_its_class(text, pii_class, expected):
    spans = detect(text, [pii_class])
    assert [(text[s.start : s.end], s.value) for s in spans] == expected
    assert {s.pii_class for s in spans} == {pii_class}


def test_the_subject_export_still_finds_a_card_next_to_an_id():
    from backend.privacy.subject_export import _Matcher, parse_subject_identifier

    matcher = _Matcher([parse_subject_identifier("6037156988640987")])
    assert matcher.classes(CARD_THEN_ID) == ["bank_card"]


def test_the_subject_export_finds_a_phone_before_a_short_number():
    from backend.privacy.subject_export import _Matcher, parse_subject_identifier

    text = "0888251467 0930 033 4354 1646"
    assert "09300334354" in [s.value for s in detect(text, [PiiClass.PHONE])]
    assert _Matcher([parse_subject_identifier("09300334354")]).classes(text) == ["phone"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The "00" reading and the mobile overlap and the ID fails its check
        # digit: one span as wide as both, with the best-ranked value.
        ("0012345678 0912 3456789", (0, 23, PiiClass.PHONE, "00123456780912")),
        ("0912 345 6789-ali@x.io", (0, 22, PiiClass.EMAIL, "6789-ali@x.io")),
    ],
)
def test_an_overlap_with_no_exact_cover_keeps_the_best_ranked_value(text, expected):
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text)] == [expected]
    assert _raw_digits(redact(text)[0]) == ""


def test_a_card_only_split_does_not_skip_groups_of_a_price_list():
    # Base finds nothing here; two chance cards that leave the last price out
    # are not a split (skipping edge groups needs an ID or a phone piece).
    assert detect("قیمت‌ها ۱۲۹۱ ۳۶۱۷ ۹۶۱۰ ۹۶۵۹ ۹۴۶۶ ۷۹۹۵ ۷۸۶۷ ۴۵۴۹ ۵۱۹۳ تومان") == []


def test_two_cards_joined_over_the_whole_run_are_still_split():
    card2 = card("627412345678901")
    assert [(s.pii_class, s.value) for s in detect(CARD + card2)] == [
        (PiiClass.BANK_CARD, CARD),
        (PiiClass.BANK_CARD, card2),
    ]


# --- mission owner, turn 4: one class in an overlap keeps base's values --------

PHONE_LIST = "98 904 032 9520 0098 945 016 8484 1775"


@pytest.mark.parametrize(
    ("text", "values"),
    [
        (PHONE_LIST, {"09040329520", "09450168484"}),
        ("0 9 3 7 037 1331 5 091 2345 6789", {"09370371331", "09123456789"}),
        ("0954 0615007 0990 083 4508 5120", {"09900834508"}),
    ],
)
def test_overlapping_phone_readings_keep_every_value_base_reports(text, values):
    # Mobile, landline and "00" readings overlap; base's longest-first spans
    # keep their values and only grow over the digits base left raw.
    spans = detect(text, [PiiClass.PHONE])
    assert values <= {s.value for s in spans}
    norm = normalize_digits_extended(text)
    matches, pieces = _candidates_of(norm, [PiiClass.PHONE])
    covered = {i for s in spans for i in range(s.start, s.end)}
    for candidate in matches + pieces:
        assert all(i in covered for i in range(candidate.start, candidate.end) if norm[i].isdigit())


def test_the_subject_export_finds_both_phones_of_a_phone_list():
    from backend.privacy.subject_export import _Matcher, parse_subject_identifier

    for phone in ("09040329520", "09450168484"):
        assert _Matcher([parse_subject_identifier(phone)]).classes(PHONE_LIST) == ["phone"]


def _candidates_of(norm, classes):
    from backend.privacy.text_pii import _candidates

    return _candidates(norm, frozenset(classes))


# --- mission owner, turn 5: the subject export reads one class at a time -------


@pytest.mark.parametrize(
    ("text", "identifiers"),
    [
        # A chance card piece cuts the phone when cards are searched too.
        ("6 0 3 7 1 0 9 5 1 1 7 0 8 3 9 7 0 3 175 9899–7–933–3231", ["09979333231", CARD]),
        # Chance ID pieces cut the phone when IDs are searched too.
        ("09190754360 55561 6–0–3–7–9–3–9–2–4–5–4–3–6–8–9–7 00629428913", ["09190754360", NID]),
    ],
)
def test_the_subject_export_finds_a_phone_when_other_classes_are_searched(text, identifiers):
    from backend.privacy.subject_export import _Matcher, parse_subject_identifier

    matcher = _Matcher([parse_subject_identifier(raw) for raw in identifiers])
    assert matcher.classes(text) == ["phone"]
