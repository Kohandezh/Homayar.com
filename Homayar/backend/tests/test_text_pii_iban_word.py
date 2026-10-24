"""HOM-308: an IBAN followed by a separator and a Latin word stays raw.

Up to text-pii/6 the IBAN regex reads one optional separator and one letter
or digit at a time, so it also reads the Latin word after the IBAN
(``IR062960000000100324200001 thanks``).  The reading with the word fails
the IBAN check, and no shorter reading was tried: the IBAN digits stayed raw
in a redacted view.  A Persian word is not read by the regex, so it already
worked.  The shorter reading is tried for ``IR`` readings only, with 26
characters (SPEC rules R2 and R4).  A non-IR IBAN before a Latin word stays as
on base: every extra mod-97 test on non-IR codes gave chance matches.

Synthetic values only.
"""

from __future__ import annotations

import pytest

from backend.privacy import text_pii
from backend.privacy.text_pii import PiiClass, detect, redact, valid_iban

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
NBSP = " "

ALL = list(PiiClass)

IR = "IR062960000000100324200001"
DE = "DE89370400440532013000"
GB = "GB82WEST12345698765432"
GB_GROUPED = "GB82 WEST 1234 5698 7654 32"
# The reading with the word is a valid IBAN by chance (SPEC rule R1).
TR = "TR653950408633924145425600"


def iban(country: str, bban: str) -> str:
    return next(
        f"{country}{check:02d}{bban}"
        for check in range(100)
        if valid_iban(f"{country}{check:02d}{bban}")
    )


# Italian IBANs whose last group holds letters only (from the leader's G7 misses).
IT_A = iban("IT", "N7566830378J1B0043F4EQX")
IT_B = iban("IT", "G7722595978DRHNLYAKQMUB")
MU = "MU17BOMM0101101030300200000MUR"
# "ZZ" is no IBAN country: a valid checksum, but no registered length.
ZZ = iban("ZZ", "370400440532013000")
# From the leader's turn-2 gate run (G1): a base IBAN after a cut must keep its span.
FR = iban("FR", "6424712933ZNHX9ZELUXB78")
IR27 = "IR270282861276077624811498"
IR00 = iban("IR", "1234567890123456789000")

SCRIPTS = [("latin", None), ("persian", FA), ("arabic", AR)]


def grouped(value: str, sep: str = " ") -> str:
    return sep.join(value[i : i + 4] for i in range(0, len(value), 4))


def _params(cases):
    for name, *rest in cases:
        for script, table in SCRIPTS:
            yield pytest.param(table, *rest, id=f"{name}-{script}")


def _in(table, text: str) -> str:
    """The digits of ``text`` in one script; letters stay Latin."""
    return text.translate(table) if table else text


def _ibans(text: str, classes=ALL):
    return [span for span in detect(text, classes) if span.pii_class is PiiClass.IBAN]


def _compact(text: str) -> str:
    return "".join(char for char in text if char.isascii() and char.isalnum()).upper()


def _assert_only_the_iban(text: str, start: int, iban_text: str, value: str) -> None:
    end = start + len(iban_text)
    assert text[start:end] == iban_text
    spans = detect(text, ALL)
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (start, end, PiiClass.IBAN, value)
    ]
    redacted, _ = redact(text, ALL)
    assert redacted == text[:start] + "[IBAN_1]" + text[end:]


# AC1 and AC2: a Latin word (or several) after a compact IR IBAN.
WORD_TAILS = [
    ("thanks", " thanks"),
    ("one-letter", " a"),
    ("two-letters", " ok"),
    ("capitalised", " Thanks"),
    ("all-caps", " THANKS"),
    ("several-words", " thanks a lot"),
    ("word-and-dot", " thanks."),
    ("word-then-digit", " thanks 2"),
    ("word-then-digits", " ok 5 times"),
]


@pytest.mark.parametrize(("table", "tail"), list(_params(WORD_TAILS)))
def test_an_ir_iban_before_a_latin_word_is_read_without_the_word(table, tail):
    iban_text = _in(table, IR)
    text = iban_text + tail
    _assert_only_the_iban(text, 0, iban_text, IR)


# AC3: the grouped form, with a space, NBSP or hyphen between the groups.
GROUPED = [
    (f"{name}-{word.strip()}", sep, word)
    for name, sep in (("space", " "), ("nbsp", NBSP), ("hyphen", "-"))
    for word in (" thanks", " ok")
]


@pytest.mark.parametrize(("table", "sep", "tail"), list(_params(GROUPED)))
def test_a_grouped_ir_iban_before_a_latin_word_is_one_iban_span(table, sep, tail):
    iban_text = _in(table, grouped(IR, sep))
    text = iban_text + tail
    # No phone or other span takes the IBAN digits instead.
    _assert_only_the_iban(text, 0, iban_text, IR)


# AC4: a Latin word before and after, and Persian text around it.
SENTENCES = [
    ("latin-words-around", "my iban ", IR, " thanks"),
    ("latin-words-around-grouped", "my iban ", grouped(IR), " thanks"),
    ("persian-around", "شماره شبای من ", IR, " thanks است"),
    ("persian-around-grouped", "شبا ", grouped(IR), " ok ممنون"),
]


@pytest.mark.parametrize(("table", "before", "iban", "after"), list(_params(SENTENCES)))
def test_an_ir_iban_inside_a_sentence_keeps_the_words_outside(table, before, iban, after):
    iban_text = _in(table, iban)
    _assert_only_the_iban(before + iban_text + after, len(before), iban_text, IR)


# AC5: a Persian word already stops the regex (passes on base; must stay green).
PERSIAN_WORD = [
    ("compact", IR),
    ("grouped", grouped(IR)),
]


@pytest.mark.parametrize(("table", "iban"), list(_params(PERSIAN_WORD)))
def test_an_ir_iban_before_a_persian_word_is_still_read(table, iban):
    iban_text = _in(table, iban)
    _assert_only_the_iban(iban_text + " ممنون", 0, iban_text, IR)


# AC6 was withdrawn (SPEC R4): a non-IR IBAN before a Latin word stays as on base.
OTHER_COUNTRIES = [
    ("de", DE, DE),
    ("de-grouped", grouped(DE), DE),
    ("gb", GB, GB),
    ("gb-grouped", GB_GROUPED, GB),
    # The last group holds letters only.
    ("it-a-grouped", grouped(IT_A), IT_A),
    ("it-b-grouped", grouped(IT_B), IT_B),
    ("it-b-hyphen", grouped(IT_B, "-"), IT_B),
    ("mu-grouped", grouped(MU), MU),
]


@pytest.mark.parametrize(("table", "iban", "value"), list(_params(OTHER_COUNTRIES)))
def test_a_non_ir_iban_before_a_latin_word_stays_as_on_base(table, iban, value):
    iban_text = _in(table, iban)
    # Alone it is read in full.
    assert _ibans(iban_text) == [text_pii.PiiSpan(0, len(iban_text), PiiClass.IBAN, value)]
    for tail in (" thanks", " x", " ok! send"):
        word = tail.split("!")[0]
        if valid_iban(value + _compact(word)):
            # Rule R1: by chance the reading with the word is valid (IT_B thanks).
            assert (value, tail) == (IT_B, " thanks")
            end = len(iban_text) + len(word)
            assert _ibans(iban_text + tail) == [
                text_pii.PiiSpan(0, end, PiiClass.IBAN, value + _compact(word))
            ]
            continue
        if (value, tail) == (MU, " thanks"):
            # MU has 30 characters: the regex stops before a 6-letter word, as on base.
            assert _ibans(iban_text + tail) == [
                text_pii.PiiSpan(0, len(iban_text), PiiClass.IBAN, MU)
            ]
            continue
        # Pin (SPEC R4): not read, as on base.
        assert _ibans(iban_text + tail) == []


# AC7: two IBANs joined by a Latin word.
TWO_IBANS = [
    ("ir-then-de", IR, DE),
    # The reading of the first one takes "and GB82": the search goes on after the cut.
    ("ir-grouped-then-gb-grouped", grouped(IR), GB_GROUPED),
    ("ir-then-it-grouped", IR, grouped(IT_A)),
]


@pytest.mark.parametrize(("table", "first", "second"), list(_params(TWO_IBANS)))
def test_two_ibans_joined_by_a_latin_word_are_two_spans(table, first, second):
    first_text, second_text = _in(table, first), _in(table, second)
    text = f"{first_text} and {second_text}"
    start = len(first_text) + len(" and ")
    spans = detect(text, ALL)
    assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
        (0, len(first_text), PiiClass.IBAN, _compact(first)),
        (start, len(text), PiiClass.IBAN, _compact(second)),
    ]
    redacted, _ = redact(text, ALL)
    assert redacted == "[IBAN_1] and [IBAN_2]"


@pytest.mark.parametrize(
    ("table", "first"), list(_params([("de", DE), ("gb-grouped", GB_GROUPED)]))
)
def test_a_non_ir_iban_before_a_word_then_an_ir_iban(table, first):
    # Pin (SPEC R4): the non-IR reading takes "and" and is not cut, as on base.
    first_text, ir_text = _in(table, first), _in(table, IR)
    text = f"{first_text} and {ir_text}"
    start = len(text) - len(ir_text)
    assert _ibans(text) == [text_pii.PiiSpan(start, len(text), PiiClass.IBAN, IR)]


# Deny-cases: each must give no IBAN span, because of the IBAN rules.
DENY = [
    # The checksum fails, with or without the word.
    ("bad-checksum", IR[:-1] + "2", " thanks"),
    # An IR IBAN has 26 characters.
    ("ir-25-characters", IR[:-1], " thanks"),
    # The word glued to the IBAN, with no separator: not read, as on base.
    ("glued-word", IR, "thanks"),
]


@pytest.mark.parametrize(("table", "iban", "tail"), list(_params(DENY)))
def test_no_iban_span_when_the_iban_rules_fail(table, iban, tail):
    if tail.startswith(" "):
        # The deny comes from the IBAN check on the reading without the word.
        assert not valid_iban(iban)
    text = _in(table, iban) + tail
    assert _ibans(text) == []
    assert _ibans(text, [PiiClass.IBAN]) == []


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_letters_inside_a_grouped_iban_do_not_cut_it(table):
    iban_text = _in(table, GB_GROUPED)
    assert _ibans(iban_text) == [text_pii.PiiSpan(0, len(iban_text), PiiClass.IBAN, GB)]
    # Pin (SPEC R4): with a word after it, a non-IR IBAN stays as on base.
    assert _ibans(iban_text + " thanks") == []
    # Pin (SPEC R5): after an IR cut the restart scan reads whole readings only,
    # so it does not cut GB82 WEST ... before "thanks" either.
    text = f"{_in(table, IR)} and {iban_text} thanks"
    assert _ibans(text) == [text_pii.PiiSpan(0, 26, PiiClass.IBAN, IR)]


# Latin text with two capital letters, two digits and digit groups, where no
# reading (the whole one or one cut at a separator) is a valid IBAN.
NO_IBAN_AT_ANY_CUT = [
    ("", "AB12 3456 7890 1234 5678 thanks"),
    ("", "XY99 1111 2222 3333 4444 55 ok then"),
    ("my code ", "QZ07 0000 1234 5678 9012 34 please"),
]


@pytest.mark.parametrize(("before", "code"), NO_IBAN_AT_ANY_CUT)
def test_a_code_that_is_no_iban_at_any_cut_gets_no_iban_span(before, code):
    for end in [len(code)] + [i for i, char in enumerate(code) if char == " "]:
        assert not valid_iban(_compact(code[:end])), code[:end]
    assert _ibans(before + code) == []


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_a_reading_that_is_valid_with_the_word_keeps_its_span(table):
    # Rule R1: the longest reading is kept when it is already a valid IBAN.
    assert valid_iban(TR + "THANKS")
    text = _in(table, TR) + " thanks"
    assert _ibans(text) == [text_pii.PiiSpan(0, len(text), PiiClass.IBAN, TR + "THANKS")]


def test_a_failing_reading_tries_one_cut_only(monkeypatch):
    # Each extra cut is one more chance of a mod-97 hit: one cut per reading.
    calls: list[str] = []

    def counting(compact: str) -> bool:
        calls.append(compact)
        return valid_iban(compact)

    monkeypatch.setattr(text_pii, "valid_iban", counting)
    # The regex reads at most 30 letters and digits after the country code.
    assert [s.value for s in _ibans(IR + " thanks a lot", [PiiClass.IBAN])] == [IR]
    assert calls == [IR + "THANKSA", IR]
    calls.clear()
    # A non-IR reading gets no cut (SPEC R4).
    assert _ibans(DE + " thanks a lot", [PiiClass.IBAN]) == []
    assert calls == [DE + "THANKSALOT"]
    calls.clear()
    # Prose: no cut either.
    assert _ibans("at 10 people came to the party", [PiiClass.IBAN]) == []
    assert calls == ["AT10PEOPLECAMETOTHEPARTY"]
    calls.clear()
    # A code that is not an IBAN country gets no cut.
    assert _ibans(ZZ + " thanks", [PiiClass.IBAN]) == []
    assert calls == [ZZ + "THANKS"]


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_an_unknown_country_code_before_a_word_gets_no_span(table):
    zz_text = _in(table, ZZ)
    # Without a word it is read as on base (valid_iban does not check the country).
    assert _ibans(zz_text) == [text_pii.PiiSpan(0, len(zz_text), PiiClass.IBAN, ZZ)]
    assert _ibans(zz_text + " thanks") == []
    assert _ibans(grouped(zz_text) + " ok") == []


# Random codes from the leader's G3 run: turn 1 read each one as a chance IBAN.
G3_CODES = [
    "LE09 823490 zip 3398953253 tomorrow my",
    "CG02 5637 977 296 1344 338 2779 182 ref no done",
    "IV15-4476 yes 2198834431 and before",
    "5841 it 043 3338 5783 98-946-823-6055 an tomorrow",
]


@pytest.mark.parametrize("text", G3_CODES)
def test_a_random_code_before_a_word_gets_no_iban_span(text):
    assert _ibans(text) == []
    assert _ibans(text, [PiiClass.IBAN]) == []


# SPEC R3: a string of a registered country but another length is never cut.
DE30 = "DE375360254579424931ABCD119436"


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_a_registered_country_with_another_length_is_not_cut(table):
    text = _in(table, DE30)
    # Alone it is read as on base (valid_iban checks the length of IR only).
    assert _ibans(text) == [text_pii.PiiSpan(0, len(text), PiiClass.IBAN, DE30)]
    assert _ibans(text + " ok") == []


# G1 shapes of turn 2: the scan restarted at the cut and ran over an IBAN base read.
BASE_SPAN_KEPT = [
    (
        # SPEC R4: the FR reading is not cut any more; the spans are the base ones.
        "fr-then-id-then-ir",
        f"{FR} to 8055880468 today {grouped(IR27)}",
        [
            (31, 41, PiiClass.IR_NATIONAL_ID, "8055880468"),
            (48, 80, PiiClass.IBAN, IR27),
        ],
    ),
    (
        "ir-then-code-then-ir",
        f"{grouped(IR00)} KQ84 950 771 4734 x and {grouped(IR, '-')}",
        [(0, 32, PiiClass.IBAN, IR00), (57, 89, PiiClass.IBAN, IR)],
    ),
]


@pytest.mark.parametrize(("table", "text", "spans"), list(_params(BASE_SPAN_KEPT)))
def test_a_base_iban_after_a_cut_keeps_its_span(table, text, spans):
    # The national ID and the last IBAN are base spans; IR00 is a new IR cut.
    text = _in(table, text)
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text, ALL)] == spans


# G3 shapes of turn 2: whole readings at the restart point that pass mod 97 by
# chance.  Their country is not registered or their length is not.
CHANCE_AFTER_A_CUT = [
    "x FH56-272-0844-2433-0989-5991-667",
    "LC91-69638 Thanks",
    "VG61-294-0099-2066 iban tomorrow send",
    "QC24-896-2961-4454-966-210-2460 Thanks",
]


@pytest.mark.parametrize("code", CHANCE_AFTER_A_CUT)
def test_no_chance_iban_after_a_cut(code):
    assert _ibans(f"{IR} {code}") == [text_pii.PiiSpan(0, 26, PiiClass.IBAN, IR)]


# G3 texts of turn 3: non-IR readings of a registered length that passed mod 97
# by chance.  Now detect gives exactly what base gives.  Base itself reads four
# of them as chance IBANs (the whole reading, rule R1).
IBAN, ID, PHONE = PiiClass.IBAN, PiiClass.IR_NATIONAL_ID, PiiClass.PHONE
G3_TURN_3 = [
    ("sure SM93 925340 thanks a lot invoice", [(5, 37, IBAN, "SM93925340THANKSALOTINVOICE")]),
    ("ref it ۱۳۸۱۵۸۷۰۶۲ report account to send", [(7, 17, ID, "1381587062")]),
    ("TN۴۶-۲۰۷-۰۴۱-۵۰۸۱-۷۹۳۲-۵۹۲ and thanks", [(9, 22, PHONE, "04150817932")]),
    ("HN51 248 702 4446 3558 Thanks done", [(0, 34, IBAN, "HN5124870244463558THANKSDONE")]),
    ("is 0098 947 287 6213 before sure", [(0, 32, IBAN, "IS00989472876213BEFORESURE")]),
    ("me 0098 925 255 0047 ok card", [(0, 28, IBAN, "ME00989252550047OKCARD")]),
    ("TL249450139625346701946 please", []),
    ("SA54 264 8710 813 777 2870 815 and", []),
]


@pytest.mark.parametrize(("text", "spans"), G3_TURN_3)
def test_a_non_ir_chance_reading_gives_no_new_span(text, spans):
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text, ALL)] == spans


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_a_restart_reading_over_a_base_phone_is_dropped(table):
    # SPEC R5: "me 0098 925 255 0047 ok card" is a whole ME reading (22) that
    # passes mod 97 by chance; it overlaps the phone base read, so it is dropped.
    text = _in(table, f"{IR} me 0098 925 255 0047 ok card")
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text, ALL)] == [
        (0, 26, IBAN, IR),
        (30, 47, PHONE, "09252550047"),
    ]


B16 = "FZ17Q2S19960X09V"  # a valid 16-character reading base finds on its own
ES = iban("ES", "7433" + B16)  # ES is 24: "ESkk 7433" + B16


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_a_restart_reading_over_a_base_iban_is_dropped(table):
    # The IR reading takes "ok ESkk"; the restart reads all of "ESkk 7433
    # FZ17 ..." as one valid ES IBAN, which overlaps the IBAN base reads.
    text = _in(table, f"{IR} ok {ES[:4]} 7433 {grouped(B16)}")
    assert _ibans(text) == [
        text_pii.PiiSpan(0, 26, PiiClass.IBAN, IR),
        text_pii.PiiSpan(40, 59, PiiClass.IBAN, B16),
    ]


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_the_restart_scan_stops_at_the_end_of_the_failed_reading(table):
    # Base reads "is 416 a 566 120 BA28..." as one failing reading, so the BA
    # IBAN stays raw, as on base.  A restart scan that went on past the end of
    # the failed IR reading would meet BA28... at a new start point.
    text = _in(table, f"{IR} ok ok 920 is 416 a 566 120 BA288617613609841855")
    assert _ibans(text) == [text_pii.PiiSpan(0, 26, PiiClass.IBAN, IR)]


# SPEC R6: in the restart scan an IR reading is read like the first one.
@pytest.mark.parametrize(
    ("table", "word"), list(_params([(w, w) for w in ("and", "or", "ok", "x")]))
)
def test_two_grouped_ir_ibans_joined_by_a_word_then_a_word(table, word):
    # The second IR reading takes "thanks": it is cut at 26 characters, and
    # the phones base reads in its digits do not stop it.
    first, second = _in(table, grouped(IR)), _in(table, grouped(IR27))
    text = f"{first} {word} {second} thanks"
    start = len(first) + len(word) + 2
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text, ALL)] == [
        (0, 32, IBAN, IR),
        (start, start + 32, IBAN, IR27),
    ]


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_a_grouped_ir_iban_after_an_ir_iban_and_a_word_covers_its_phone(table):
    # Base reads a phone in the digits of the second IBAN; the IR restart
    # reading is dropped only over a base IBAN, so it takes that phone.
    text = _in(table, f"{IR} and {grouped(IR)}")
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text, ALL)] == [
        (0, 26, IBAN, IR),
        (31, 63, IBAN, IR),
    ]


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_a_grouped_non_ir_iban_holding_a_phone_after_an_ir_iban_is_not_read(table):
    # Disclosed residual (module docstring): a non-IR restart reading is
    # dropped over any base regex reading, here the phone in the DE digits.
    text = _in(table, f"{grouped(IR)} and {grouped(DE)}")
    assert [(s.start, s.end, s.pii_class, s.value) for s in detect(text, ALL)] == [
        (0, 32, IBAN, IR),
        (47, 64, PHONE, "00440532013000"),
    ]


B18 = iban("XY", "12345678901234")  # a valid reading base finds on its own
IR_OVER_B18 = iban("IR", "7433" + B18)  # "IRkk 7433" + B18 is a valid 26-character IR


@pytest.mark.parametrize("table", [t for _, t in SCRIPTS], ids=[n for n, _ in SCRIPTS])
def test_an_ir_restart_reading_over_a_base_iban_is_dropped(table):
    text = _in(table, f"{IR} ok {IR_OVER_B18[:4]} 7433 {grouped(B18)}")
    assert _ibans(text) == [
        text_pii.PiiSpan(0, 26, PiiClass.IBAN, IR),
        text_pii.PiiSpan(40, 62, PiiClass.IBAN, B18),
    ]


@pytest.mark.parametrize(
    ("table", "tail"), list(_params([("12-thanks", " 12 thanks"), ("2", " 2"), ("1-ok", " 1 ok")]))
)
def test_no_cut_before_a_digit_token(table, tail):
    # SPEC R2: the cut needs a separator and then a Latin letter.  Base reads
    # nothing here either.
    assert _ibans(_in(table, IR + tail)) == []


@pytest.mark.parametrize("text", [IR + "thanks", IR + "thanks 2"])
def test_shapes_still_not_read(text):
    # Disclosed residual risk (module docstring): a word glued to the IBAN,
    # with no separator, stays raw, as on base.
    assert _ibans(text) == []
