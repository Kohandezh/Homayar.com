"""HOM-302: detect and the subject export do less work, with the same results.

The cost pins count the lines of ``backend.privacy.text_pii`` one call runs
(``_cost.lines_run``): the count is the same on every machine.  No test here
has a time budget (HOM-307, HOM-310).  Synthetic values only.
"""

from __future__ import annotations

import gc
import random
import types

import pytest

from backend.privacy import subject_export, text_pii
from backend.privacy.subject_export import SUBJECT_CLASSES, SubjectIdentifier, _Matcher
from backend.privacy.text_pii import SUPPORTED_CLASSES, PiiClass, detect, redact, valid_luhn
from backend.speech.text_normalize import normalize_digits_extended
from backend.tests._cost import lines_run

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
SEPARATORS = ["", " ", "-", " ", "–"]
EXPORT_CLASSES = [PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.BANK_CARD]
MODULE = ("backend.privacy.text_pii",)
MOBILE_RE, LANDLINE_RE = text_pii._IR_MOBILE_RE, text_pii._IR_LANDLINE_RE


def national_id(prefix9: str) -> str:
    total = sum(int(prefix9[i]) * (10 - i) for i in range(9)) % 11
    return prefix9 + str(total if total < 2 else 11 - total)


def card(prefix15: str) -> str:
    return next(prefix15 + d for d in "0123456789" if valid_luhn(prefix15 + d))


NID = "0012345679"
MOBILE = "09123456789"
LANDLINE = "02188776655"
CARD = card("603799112345678")
IBAN = "IR062960000000100324200001"


def _grouped(digits: str, rng: random.Random) -> str:
    if rng.random() < 0.4:
        return digits
    sep = rng.choice(SEPARATORS[1:])
    out, i = [], 0
    while i < len(digits):
        size = rng.randint(2, 5)
        out.append(digits[i : i + size])
        i += size
    return sep.join(out)


def _random_digits(rng: random.Random, size: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(size))


def _token(rng: random.Random) -> str:
    kind = rng.randrange(9)
    if kind == 0:
        return _grouped(national_id(_random_digits(rng, 9)), rng)
    if kind == 1:
        return _grouped("09" + rng.choice("0123349") + _random_digits(rng, 8), rng)
    if kind == 2:
        return _grouped(card("6037" + _random_digits(rng, 11)), rng)
    if kind == 3:
        return national_id(_random_digits(rng, 9)) + MOBILE  # glued pair
    if kind == 4:
        return _random_digits(rng, rng.randint(1, 3)) + MOBILE  # glued mobile
    if kind == 5:
        return "00" + _random_digits(rng, rng.randint(8, 13))
    if kind == 6:  # a price list
        return " ".join(_random_digits(rng, rng.randint(3, 7)) for _ in range(rng.randint(3, 6)))
    if kind == 7:
        return _random_digits(rng, rng.randint(1, 26))
    return rng.choice(["و", "کد ملی", "and", IBAN + " thanks", "a@b.io"])


def _number_dense(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test inputs, not security
    texts = []
    for _ in range(count):
        parts = [_token(rng) for _ in range(rng.randint(3, 10))]
        text = "".join(rng.choice([" ", " ", "-", ", ", " و "]) + part for part in parts).strip()
        texts.append(text.translate(FA) if rng.random() < 0.3 else text)
    return texts


DENSE = _number_dense(302, 40)


def _lines(fn, *args) -> int:
    return lines_run(MODULE, lambda: [fn(text, *args) for text in DENSE])[0]


# --- cost pins (each fails on main 05f15dd: see the HOM-302 handoff) -----------------


def test_a_three_class_export_costs_about_one_detect():
    # main ran detect once per class, from the start: 2.1x one all-class detect.
    matcher = _Matcher([SubjectIdentifier(c, "0") for c in EXPORT_CLASSES])
    export = _lines(matcher.classes)
    one = _lines(detect)
    assert export < 1.4 * one, (export, one)


def test_a_detect_without_split_classes_does_no_split_work():
    # IBAN and e-mail never split a digit run: main still read every piece of
    # every run as an ID, a phone and a card, then dropped them (0.55x).
    assert _lines(detect, [PiiClass.IBAN]) < 0.2 * _lines(detect), "split work for IBAN"


@pytest.mark.parametrize("pii_class", [PiiClass.PHONE, PiiClass.IBAN, PiiClass.EMAIL])
def test_a_detect_without_cards_checks_only_the_card_regex_matches(pii_class, monkeypatch):
    # The card regex and its Luhn check run for every class set (_matches does
    # not depend on the classes); a piece of a digit run is read as a card only
    # when cards are requested.  main read every piece as a card.
    calls = []

    def counted(digits: str) -> bool:
        calls.append(digits)
        return valid_luhn(digits)

    monkeypatch.setattr(text_pii, "valid_luhn", counted)
    for text in DENSE:
        detect(text, [pii_class])
    regex = sum(len(text_pii._CARD_RE.findall(normalize_digits_extended(t))) for t in DENSE)
    assert len(calls) == regex


# --- the shared path gives the spans of separate calls --------------------------------


SHAPES = [
    f"{NID} 0912 345 6789",
    f"{NID}{MOBILE}",
    f"1403-{NID}{MOBILE}-12",
    "کد ملی ۰۰۱۲۳۴۵۶۷۹۰۹۱۲۳۴۵۶۷۸۹ ۲ بار",
    "0039626921 0090 453 3473 896 2",
    "102 1209316593052",
    f"12 {CARD} 34",
    f"{CARD[:4]} {CARD[4:8]} {CARD[8:12]} {CARD[12:]} 1234 5678",
    f"{IBAN} thanks IR270570028180010653892101 x",
    f"{IBAN} and DE89 3704 0044 0532 0130 00",
    f"کد پستی 1234567890 {NID} {MOBILE}",
    f"{LANDLINE}{NID} 7",
    "6 0 3 7 1 0 9 5 1 1 7 0 8 3 9 7 0 3 175 9899–7–933–3231",
    "09190754360 55561 6–0–3–7–9–3–9–2–4–5–4–3–6–8–9–7 00629428913",
]
CLASS_SETS = [
    SUPPORTED_CLASSES,
    frozenset({PiiClass.PHONE, PiiClass.IR_NATIONAL_ID, PiiClass.EMAIL}),
    SUBJECT_CLASSES,
    *[frozenset({c}) for c in PiiClass],
    frozenset({PiiClass.PHONE, PiiClass.BANK_CARD}),
]


def _spans(spans):
    return [(s.start, s.end, s.pii_class, s.value) for s in spans]


@pytest.mark.parametrize("text", SHAPES + DENSE[:20], ids=range(len(SHAPES) + 20))
def test_the_shared_path_gives_each_class_set_its_own_spans(text):
    alone = [_spans(detect(text, classes)) for classes in CLASS_SETS]
    # One shared preparation, the class sets in two orders: no class set sees
    # work another one did.
    together = [_spans(spans) for spans in text_pii._detect_each(text, CLASS_SETS)]
    backwards = [_spans(spans) for spans in text_pii._detect_each(text, CLASS_SETS[::-1])]
    assert together == alone
    assert backwards[::-1] == alone


def _classes_one_by_one(identifiers: list[SubjectIdentifier], text: str) -> list[str]:
    # HOM-298: the HOM-291 rule (one detect per class, then the union), plus the
    # re-cut rule of subject_export for a mobile, read from separate detect
    # calls of each pair of cutting classes: no shared work and no first test of the
    # digits.
    wanted = {(item.pii_class, item.value) for item in identifiers}
    single = {pii_class: detect(text, {pii_class}) for pii_class in SUBJECT_CLASSES}
    found = {
        (span.pii_class, span.value)
        for spans in single.values()
        for span in spans
        if (span.pii_class, span.value) in wanted
    }
    norm = normalize_digits_extended(text)
    written = subject_export._written_identifiers(norm)
    classes = sorted(subject_export._CUTTING_CLASSES, key=lambda c: c.value)
    for mask in range(1, 1 << len(classes)):
        combo = {c for bit, c in enumerate(classes) if mask >> bit & 1}
        reading = detect(text, combo) if len(combo) == 2 else []
        for k, span in enumerate(reading):
            key = (span.pii_class, span.value)
            if (
                key in wanted
                and key not in found
                and span.pii_class in subject_export._REREAD_CLASSES
                and subject_export._recut(norm, reading, k, single.__getitem__, written)
            ):
                found.add(key)
    return sorted({pii_class.value for pii_class, _ in found})


@pytest.mark.parametrize("text", SHAPES + DENSE[:20], ids=range(len(SHAPES) + 20))
def test_the_export_matcher_reads_each_class_on_its_own(text):
    found = {(s.pii_class, s.value) for c in SUBJECT_CLASSES for s in detect(text, {c})}
    planted = [SubjectIdentifier(c, v) for c, v in sorted(found)]
    decoys = [SubjectIdentifier(c, v) for c, v in [(PiiClass.PHONE, MOBILE), (PiiClass.IBAN, IBAN)]]
    for identifiers in ([*planted, *decoys], planted[:1], planted[-2:], decoys):
        assert _Matcher(identifiers).classes(text) == _classes_one_by_one(identifiers, text)


# --- the exact rules behind the cheaper reading ---------------------------------------


@pytest.mark.parametrize("size", range(10, 20))
def test_a_phone_piece_in_a_digit_run_has_11_12_or_14_digits(size):
    # Inside a digit run there is no + and no bracket, so the phone regexes
    # read only 0 + 10, 98 + 10 or 0098 + 10 digits.
    rng = random.Random(size)  # noqa: S311 - deterministic test inputs, not security
    reads = 0
    for _ in range(400):
        head = rng.choice(["09", "989", "00989", "0" + rng.choice("12345678")])
        digits = (head + rng.choice("0123349") + _random_digits(rng, 19))[:size]
        raw = digits[0] + "".join(rng.choice(SEPARATORS) + d for d in digits[1:])
        read = bool(MOBILE_RE.fullmatch(raw) or LANDLINE_RE.fullmatch(raw))
        assert not read or size in text_pii._PHONE_DIGITS, raw
        reads += read
    assert reads > 0 or size not in text_pii._PHONE_DIGITS


def test_any_phone_reading_in_a_digit_run_has_11_12_or_14_digits():
    # Not only the prefixes the phone regexes know today: any digits that start
    # with 0 or 9, written with the run separators.
    rng = random.Random(3021)  # noqa: S311 - deterministic test inputs, not security
    reads = 0
    for _ in range(20_000):
        digits = rng.choice("09") + _random_digits(rng, rng.randint(9, 18))
        raw = digits[0] + "".join(rng.choice(SEPARATORS) + d for d in digits[1:])
        if MOBILE_RE.fullmatch(raw) or LANDLINE_RE.fullmatch(raw):
            assert len(digits) in text_pii._PHONE_DIGITS, raw
            reads += 1
    assert reads > 0


# --- S1: nothing of a text outlives the call -------------------------------------------


OWN_MODULES = (text_pii.__name__, subject_export.__name__)


def _reachable(value, seen: set[int], depth: int = 0):
    """Every object a module or class attribute keeps alive, as far as a
    cache could hide text: containers, classes of our modules, the object of a
    bound method, and our functions with their closure cells, defaults and
    wrapped functions."""
    if id(value) in seen or depth > 6:
        return
    seen.add(id(value))
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _reachable(key, seen, depth + 1)
            yield from _reachable(item, seen, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _reachable(item, seen, depth + 1)
    elif isinstance(value, type) and value.__module__ in OWN_MODULES:
        yield from _reachable(dict(vars(value)), seen, depth + 1)
    elif isinstance(value, (staticmethod, classmethod)):
        yield from _reachable(value.__func__, seen, depth + 1)
    elif isinstance(value, property):
        yield from _reachable([value.fget, value.fset, value.fdel], seen, depth + 1)
    elif isinstance(value, (types.MethodType, types.BuiltinMethodType)) and not isinstance(
        value.__self__, types.ModuleType
    ):
        yield from _reachable(value.__self__, seen, depth + 1)  # a bound method keeps its object
    elif callable(value) and getattr(value, "__module__", None) in OWN_MODULES:
        cells = [cell.cell_contents for cell in getattr(value, "__closure__", None) or ()]
        defaults = [getattr(value, "__defaults__", None), getattr(value, "__kwdefaults__", None)]
        yield from _reachable(
            [cells, defaults, getattr(value, "__wrapped__", None)], seen, depth + 1
        )


def test_no_module_or_class_attribute_keeps_a_text_or_a_value():
    secret_id, secret_phone = national_id("274659381"), "09357771234"
    text = f"شماره {secret_id} {secret_phone[:4]} {secret_phone[4:7]} {secret_phone[7:]} و"
    spans = detect(text)
    assert {s.value for s in spans} == {secret_id, secret_phone}
    redact(text, SUBJECT_CLASSES)
    matcher = _Matcher([SubjectIdentifier(PiiClass.PHONE, secret_phone)])
    assert matcher.classes(text) == ["phone"]
    del spans, matcher
    gc.collect()
    for module in (text_pii, subject_export):
        kept = list(_reachable(dict(vars(module)), set()))
        # A functools cache (lru_cache, cache) keeps its arguments and results.
        assert not [item for item in kept if hasattr(item, "cache_info")], module.__name__
        for value in [item for item in kept if isinstance(item, str)]:
            assert secret_id not in value and secret_phone not in value and text not in value
