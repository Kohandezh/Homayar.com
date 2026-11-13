"""HOM-138 (F70): the report PDF draws Persian with Vazirmatn, joined and in RTL order.

The tests read back what the PDF draws: each drawn line, its position, and
its text through the font's ``/ToUnicode`` map.  The expected visual forms
are written out by hand (code points), so the tests do not trust the
shaping and bidi libraries they check.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import zlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import arabic_reshaper
import pytest
from bidi import get_display
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from backend.services import pdf_text
from backend.services.report_export_service import render_report_export
from backend.tests.test_report_export_service import _provenance, _report

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
LEFT_EDGE = 72 + 6  # A4 page margin plus the frame padding of SimpleDocTemplate
RIGHT_EDGE = 595.2755905511812 - 72 - 6

# Visual (left to right) forms, written as code points.
SALAM_VISUAL = "ﻡﻼﺳ"  # meem isolated, lam-alef final, seen initial
AGHAZ_VISUAL = "ﺯﺎﻏﺁ"  # zain isol, alef final, ghain init, alef-madda isol
PAYAN_VISUAL = "ﻥﺎﯾﺎﭘ"  # noon isol, alef, yeh init, alef, peh init
# جلسه با Acme در ۱۴۰۳ برگزار شد, right to left
MIXED_VISUAL = "ﺪﺷ ﺭﺍﺰﮔﺮﺑ ۱۴۰۳ ﺭﺩ Acme ﺎﺑ ﻪﺴﻠﺟ"
# می‌شود: dal isol, waw final, sheen INITIAL (the ZWNJ broke the join), yeh FINAL, meem init
MISHAVAD_VISUAL = "ﺩﻮﺷﯽﻣ"


# --- reading the PDF back ----------------------------------------------------


@dataclass(frozen=True)
class Drawn:
    page: int
    x: float
    y: float
    font: str  # BaseFont without the subset prefix, e.g. "Vazirmatn-Regular"
    size: float
    text: str


def _objects(pdf: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """Every indirect object: its dictionary and its decoded stream (or None)."""
    found: dict[int, tuple[bytes, bytes | None]] = {}
    position = 0
    pattern = re.compile(rb"(?:^|\n)(\d+) 0 obj\b")
    while (match := pattern.search(pdf, position)) is not None:
        start = match.end()
        end = pdf.find(b"endobj", start)
        stream_at = pdf.find(b"stream", start)
        if stream_at == -1 or stream_at > end:
            found[int(match.group(1))] = (pdf[start:end], None)
            position = end
            continue
        header = pdf[start:stream_at]
        length = int(re.search(rb"/Length (\d+)", header).group(1))
        data_start = stream_at + len(b"stream")
        data_start += 2 if pdf[data_start : data_start + 2] == b"\r\n" else 1
        data = pdf[data_start : data_start + length]
        if b"/FlateDecode" in header and b"/ASCII85Decode" not in header:
            data = zlib.decompress(data)
        found[int(match.group(1))] = (header, data)
        position = data_start + length
    return found


def _unescape(literal: bytes) -> bytes:
    named = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
    out = bytearray()
    index = 0
    while index < len(literal):
        char = literal[index : index + 1]
        if char != b"\\":
            out += char
            index += 1
            continue
        octal = re.match(rb"[0-7]{1,3}", literal[index + 1 : index + 4])
        if octal:
            out.append(int(octal.group(0), 8))
            index += 1 + len(octal.group(0))
            continue
        escaped = literal[index + 1 : index + 2]
        out += named.get(escaped, escaped)
        index += 2
    return bytes(out)


def _to_unicode(cmap: bytes) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for code, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            mapping[int(code, 16)] = bytes.fromhex(target.decode()).decode("utf-16-be")
    return mapping


_NUMBER = re.compile(rb"[-+]?(?:\d+\.?\d*|\.\d+)")
_TOKEN = re.compile(
    rb"\((?:\\.|[^\\)])*\)|/[^\s/\[\]()<>]+|[-+]?(?:\d+\.?\d*|\.\d+)|[A-Za-z*']+", re.S
)


def _drawn(pdf: bytes) -> list[Drawn]:
    """Every BT..ET block of every page, as one drawn line."""
    objects = _objects(pdf)
    fonts: dict[str, tuple[str, dict[int, str] | None]] = {}
    for header, _data in objects.values():
        if not re.search(rb"/Type /Font(?![A-Za-z])", header):
            continue
        name = re.search(rb"/Name /(\S+)", header).group(1).decode()
        base = re.search(rb"/BaseFont /(\S+)", header).group(1).decode().split("+")[-1]
        to_unicode = re.search(rb"/ToUnicode (\d+) 0 R", header)
        cmap = _to_unicode(objects[int(to_unicode.group(1))][1]) if to_unicode else None
        fonts[name] = (base, cmap)
    pages = [
        number
        for number, (header, _data) in sorted(objects.items())
        if re.search(rb"/Type /Page\b", header)
    ]
    lines: list[Drawn] = []
    for page_index, number in enumerate(pages):
        contents = re.search(rb"/Contents (\d+) 0 R", objects[number][0]).group(1)
        stream = objects[int(contents)][1]
        operands: list[bytes] = []
        x = y = size = 0.0
        font = ""
        text: list[str] = []
        # the current transformation matrix (a, b, c, d, e, f) and its saved copies
        ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        saved: list[tuple[float, ...]] = []
        for token in _TOKEN.findall(stream):
            if token[:1] in (b"(", b"/") or _NUMBER.fullmatch(token):
                operands.append(token)
                continue
            # an operator: use its operands, then drop them
            if token == b"q":
                saved.append(ctm)
            elif token == b"Q":
                ctm = saved.pop()
            elif token == b"cm":
                a, b, c, d, e, f = (float(value) for value in operands[-6:])
                A, B, C, D, E, F = ctm  # noqa: N806
                ctm = (
                    a * A + b * C,
                    a * B + b * D,
                    c * A + d * C,
                    c * B + d * D,
                    e * A + f * C + E,
                    e * B + f * D + F,
                )
            elif token == b"BT":
                text = []
            elif token == b"Tm":
                local_x, local_y = float(operands[-2]), float(operands[-1])
                a, b, c, d, e, f = ctm
                x, y = a * local_x + c * local_y + e, b * local_x + d * local_y + f
            elif token == b"Tf":
                font, size = operands[-2][1:].decode(), float(operands[-1])
            elif token == b"Tj":
                raw = _unescape(operands[-1][1:-1])
                cmap = fonts[font][1]
                text.append(
                    raw.decode("latin-1") if cmap is None else "".join(cmap[code] for code in raw)
                )
            elif token == b"ET" and text:
                lines.append(Drawn(page_index, x, y, fonts[font][0], size, "".join(text)))
            operands = []
    return lines


@cache
def _font(style: str) -> TTFont:
    font = TTFont(f"HOM138-Test-{style}", str(FONT_DIR / f"Vazirmatn-{style}.ttf"))
    pdfmetrics.registerFont(font)
    return font


def _width(line: Drawn) -> float:
    style = "Bold" if line.font.endswith("Bold") else "Regular"
    return pdfmetrics.stringWidth(line.text, _font(style).fontName, line.size)


def _pdf(**report) -> bytes:
    title = report.pop("title", "R")
    return render_report_export(
        _report(**report), _provenance(format="pdf"), "pdf", title=title
    ).content


def _line(lines: list[Drawn], text: str) -> Drawn:
    matches = [line for line in lines if line.text == text]
    assert len(matches) >= 1, (text, [line.text for line in lines])
    return matches[0]


def _right_aligned(line: Drawn) -> bool:
    return abs(line.x + _width(line) - RIGHT_EDGE) < 0.05


def _left_aligned(line: Drawn) -> bool:
    return abs(line.x - LEFT_EDGE) < 0.05


# --- A1: font and joined forms -------------------------------------------------


def test_persian_pdf_embeds_vazirmatn_and_no_helvetica():
    content = _pdf(title="گزارش جلسه فروش", overview="سلام")
    assert b"/BaseFont /" in content
    assert re.search(rb"/BaseFont /[A-Z]{6}\+Vazirmatn-Regular", content)
    assert re.search(rb"/BaseFont /[A-Z]{6}\+Vazirmatn-Bold", content)
    assert b"Helvetica" not in content
    fonts = {line.font for line in _drawn(content)}
    assert fonts == {"Vazirmatn-Regular", "Vazirmatn-Bold"}


def test_salam_is_drawn_as_joined_forms_in_visual_order():
    lines = _drawn(_pdf(overview="سلام"))
    salam = _line(lines, SALAM_VISUAL)
    assert _right_aligned(salam)
    drawn = "".join(line.text for line in lines)
    # the isolated base letters are never drawn
    assert not set("سلام") & set(drawn)


def test_title_and_headings_use_the_same_path():
    lines = _drawn(_pdf(title="سلام"))
    title = lines[0]
    assert title.text == SALAM_VISUAL
    assert title.font == "Vazirmatn-Bold"
    assert _right_aligned(title)
    provenance = _line(lines, "Provenance")
    assert provenance.font == "Vazirmatn-Bold"
    assert _left_aligned(provenance)


# --- A2: wrapping in logical order ----------------------------------------------


def test_a_long_persian_paragraph_starts_on_the_top_line():
    overview = "آغاز " + "متن آزمایشی گزارش جلسه " * 30 + "پایان"
    lines = _drawn(_pdf(overview=overview))
    first = next(index for index, line in enumerate(lines) if line.text.endswith(AGHAZ_VISUAL))
    last = next(index for index, line in enumerate(lines) if line.text.startswith(PAYAN_VISUAL))
    paragraph = lines[first : last + 1]
    assert len(paragraph) >= 3
    # top to bottom on one page: the first logical word is on the top line
    assert [line.y for line in paragraph] == sorted((line.y for line in paragraph), reverse=True)
    assert all(_right_aligned(line) for line in paragraph)
    # nothing is lost: every word is drawn once
    words = [word for line in paragraph for word in line.text.split(" ")]
    assert len(words) == len(overview.split(" "))


def test_a_word_wider_than_the_frame_breaks_without_losing_characters():
    word = "x" * 300
    lines = _drawn(_pdf(overview=word))
    # the summary paragraph: the first run of lines made only of the word
    start = next(index for index, line in enumerate(lines) if set(line.text) == {"x"})
    end = next(index for index in range(start, len(lines)) if set(lines[index].text) != {"x"})
    pieces = [line.text for line in lines[start:end]]
    assert len(pieces) >= 2
    assert "".join(pieces) == word
    assert all(_width(line) <= RIGHT_EDGE - LEFT_EDGE + 0.05 for line in lines)


def test_every_drawn_line_fits_the_frame():
    overview = "\n".join(
        ["آغاز " + "متن آزمایشی گزارش جلسه " * 30 + "پایان", "Plain " * 120, "x" * 400]
    )
    for line in _drawn(_pdf(overview=overview)):
        assert LEFT_EDGE - 0.05 <= line.x
        assert line.x + _width(line) <= RIGHT_EDGE + 0.05, line.text


# --- A3: mixed direction ------------------------------------------------------


def test_mixed_line_keeps_latin_and_digits_in_reading_order():
    lines = _drawn(_pdf(overview="جلسه با Acme در ۱۴۰۳ برگزار شد"))
    mixed = _line(lines, MIXED_VISUAL)
    assert _right_aligned(mixed)
    assert "Acme" in mixed.text
    assert "۱۴۰۳" in mixed.text


def test_brackets_are_mirrored_in_right_to_left_runs():
    lines = _drawn(_pdf(overview="سلام (دنیا)"))
    # visual: (دنیا) سلام, the bracket glyphs face the right way
    _line(lines, "(ﺎﯿﻧﺩ) " + SALAM_VISUAL)
    # a Persian finding with its English evidence note
    lines = _drawn(_pdf(overview="متن"))
    finding = _line(lines, "(evidence sufficient, supported: True) ﻦﺘﻣ -")
    assert _right_aligned(finding)


def test_harakat_follow_their_base_letter():
    lines = _drawn(_pdf(overview="حتماً"))
    # alef final then its fathatan, then meem, teh, hah
    _line(lines, "ﺎًﻤﺘﺣ")


# --- A4: glyph coverage and ZWNJ ---------------------------------------------


def test_zwnj_breaks_the_join_after_mi():
    lines = _drawn(_pdf(overview="می‌شود"))
    candidates = [line for line in lines if line.text.replace("‌", "") == MISHAVAD_VISUAL]
    assert len(candidates) >= 1
    assert "ﺸ" not in "".join(line.text for line in lines)  # no medial sheen


PERSIAN_SAMPLE = (
    "ی ک گ چ پ ژ یک کی گل چای پیر ژاله آژانس می‌شود خانۀ ۀ جلسهٔ حتماً "
    "۰۱۲۳۴۵۶۷۸۹ ، ؛ ؟ لا الله سلام ‌ ٪ ٫ ٬"
)


def test_every_drawn_persian_glyph_exists_in_the_font():
    lines = _drawn(_pdf(title=PERSIAN_SAMPLE, overview=PERSIAN_SAMPLE))
    for line in lines:
        style = "Bold" if line.font.endswith("Bold") else "Regular"
        cmap = _font(style).face.charToGlyph
        missing = {f"U+{ord(char):04X}" for char in line.text if ord(char) not in cmap}
        assert not missing, (line.text, missing)


@pytest.mark.parametrize(
    "letter",
    list("ءآأؤإئابةتثجحخدذرزسشصضطظعغفقكلمنهوىيپچژکگیۀ"),
)
def test_each_persian_letter_in_every_position_has_a_glyph(letter):
    cmap = _font("Regular").face.charToGlyph
    for text in (letter, f"ب{letter}ب", f"{letter}ب", f"ب{letter}"):
        lines = pdf_text.visual_lines(text, font_name=pdf_text.REGULAR_FONT, size=10, width=400)
        for line in lines:
            missing = [f"U+{ord(char):04X}" for char in line.text if ord(char) not in cmap]
            assert not missing, (text, missing)


def test_bidi_controls_are_applied_but_not_drawn():
    controls = "‎‏؜⁦⁧⁨⁩‪‫‬‭‮"
    text = "⁧Acme⁩ سلام ‏x؜"
    lines = pdf_text.visual_lines(text, font_name=pdf_text.REGULAR_FONT, size=10, width=400)
    assert len(lines) == 1
    assert not set(controls) & set(lines[0].text)
    assert "Acme" in lines[0].text
    assert SALAM_VISUAL in lines[0].text


# --- A5: English unchanged -----------------------------------------------------


def test_an_english_report_is_left_aligned_and_unchanged():
    overview = "The client asked for a new price list (by Friday), see item 3."
    lines = _drawn(_pdf(title="Weekly call", overview=overview, decision_text="Send the list"))
    assert lines[0].text == "Weekly call"
    assert _left_aligned(lines[0])
    summary = _line(lines, overview)
    assert _left_aligned(summary)
    decision = _line(lines, "- Send the list (evidence sufficient, supported: True)")
    assert _left_aligned(decision)
    for line in lines:
        if line.text.isascii():
            assert _left_aligned(line), line.text


# --- A6: the HOM-137 safety guarantees still hold --------------------------------


def test_stored_markup_entities_and_controls_print_as_text(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("HOM138-SECRET-MARKER", encoding="utf-8")
    overview = (
        f'<b>x</b> <a href="file:///etc/passwd">y</a> <img src="{secret}"/> &amp; & \x00\x07 سلام'
    )
    content = _pdf(overview=overview)
    assert b"HOM138-SECRET-MARKER" not in content
    assert b"/URI" not in content
    assert b"/Annots" not in content
    printed = " ".join(line.text for line in _drawn(content))
    flat = "".join(printed.split())
    assert "<b>x</b>" in printed
    assert '<ahref="file:///etc/passwd">y</a>' in flat
    assert f'<imgsrc="{secret}"/>' in flat
    assert "&amp; &" in printed
    assert "&amp;amp;" not in printed
    assert "\x00" not in printed and "\x07" not in printed


def _metadata_title(content: bytes) -> str:
    literal = re.search(rb"/Title \(((?:\\.|[^\\)])*)\)", content)
    if literal is not None:
        raw = _unescape(literal.group(1))
    else:
        raw = bytes.fromhex(re.search(rb"/Title <([0-9A-Fa-f]+)>", content).group(1).decode())
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be")
    return raw.decode("latin-1")


def test_title_metadata_is_the_cleaned_logical_title():
    title = 'گزارش <b>x</b> &amp;\r\n"q"\x00 سلام'
    content = _pdf(title=title)
    assert _metadata_title(content) == 'گزارش <b>x</b> &amp; "q" سلام'
    # the page shows the visual form of the same text
    assert SALAM_VISUAL in _drawn(content)[0].text


def test_the_persian_pdf_is_deterministic():
    first = _pdf(title=PERSIAN_SAMPLE, overview=PERSIAN_SAMPLE)
    second = _pdf(title=PERSIAN_SAMPLE, overview=PERSIAN_SAMPLE)
    assert first == second


# --- direction rule (UAX #9 P2/P3) -------------------------------------------


@pytest.mark.parametrize(
    ("text", "direction"),
    [
        ("سلام Acme", "R"),
        ("Acme سلام", "L"),
        ("۱۴۰۳ سلام", "R"),
        ("- سلام", "R"),
        ("123 - ()", "L"),
        ("", "L"),
        ("⁧Acme⁩ سلام", "R"),
        ("report.summary.overview: سلام", "L"),
    ],
)
def test_paragraph_direction_is_the_first_strong_character(text, direction):
    assert pdf_text.paragraph_direction(text) == direction


# --- the bundled font -------------------------------------------------------


def test_bundled_font_files_match_their_readme():
    readme = (FONT_DIR / "README.md").read_text(encoding="utf-8")
    for name in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf", "OFL.txt"):
        digest = hashlib.sha256((FONT_DIR / name).read_bytes()).hexdigest()
        assert digest in readme, name
    licence = (FONT_DIR / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in licence
    assert "v33.003" in readme


# --- a character the font lacks keeps its value (review turn 1) --------------


@pytest.mark.parametrize(("text", "changed"), [("10⁵", "105"), ("H₂O", "H2O")])
def test_a_character_the_font_lacks_is_never_drawn_as_another_value(text, changed):
    # Vazirmatn has no superscript or subscript digits: they draw as missing
    # glyphs (an honest box), never as the plain digit (a different number)
    regular = pdf_text._glyphs(pdf_text.REGULAR_FONT)
    assert not {ord(char) for char in text} <= set(regular)
    (line,) = pdf_text.visual_lines(text, font_name=pdf_text.REGULAR_FONT, size=10, width=400)
    assert line.text == text
    # reportlab draws a character the font lacks with code 0, the .notdef glyph
    lines = _drawn(_pdf(overview=text))
    _line(lines, "".join(char if ord(char) in regular else "\x00" for char in text))
    assert all(changed not in line.text for line in lines)


def test_only_an_arabic_presentation_form_falls_back_to_its_base_letter():
    regular = pdf_text._glyphs(pdf_text.REGULAR_FONT)
    # isolated heh with yeh above (U+FBA4) is not in the font; its base letter is
    assert pdf_text._with_font_fallback("ﮤ", regular) == "ۀ"
    # outside U+FB50-U+FDFF and U+FE70-U+FEFF nothing is replaced, even when
    # NFKC gives one character the font has (5, 2, 1, A, H)
    for char in ("⁵", "₂", "①", "Ａ", "ℌ"):
        assert ord(char) not in regular
        assert pdf_text._with_font_fallback(char, regular) == char


# --- no-break spaces stay inside the word (review turn 1) --------------------

PRICE = "قیمت ۱۰ 000 تومان"


def test_a_no_break_space_keeps_the_number_groups_in_order():
    # W4: a no-break space between two numbers makes one number, read left to
    # right; as a plain space the groups were drawn reversed (000 ۱۰)
    (line,) = pdf_text.visual_lines(PRICE, font_name=pdf_text.REGULAR_FONT, size=10, width=400)
    assert line.text == "ﻥﺎﻣﻮﺗ ۱۰ 000 ﺖﻤﯿﻗ"
    lines = _drawn(_pdf(overview=PRICE))
    priced = [line for line in lines if "۱" in line.text]  # the summary and its finding
    assert len(priced) == 2
    for line in priced:
        number = "".join(char for char in line.text if char.isdigit())
        assert number == "۱۰000", line.text
    # reportlab draws U+00A0 with the space glyph (code 32)
    assert _right_aligned(_line(lines, "ﻥﺎﻣﻮﺗ ۱۰ 000 ﺖﻤﯿﻗ"))


@pytest.mark.parametrize("space", [" ", " ", " "])
def test_a_no_break_space_is_not_a_line_break(space):
    font = pdf_text.REGULAR_FONT
    joined = f"aaaa{space}bbbb"
    width = pdfmetrics.stringWidth(joined, font, 10) + 1
    lines = pdf_text.visual_lines(f"aaaa {joined}", font_name=font, size=10, width=width)
    assert [line.text for line in lines] == ["aaaa", joined]


def test_other_whitespace_runs_still_become_one_space():
    lines = pdf_text.visual_lines(
        "  a  　  b  c  ",
        font_name=pdf_text.REGULAR_FONT,
        size=10,
        width=400,
    )
    assert [line.text for line in lines] == ["a b c"]


def test_the_collapsed_whitespace_is_every_space_but_the_no_break_ones():
    every = {chr(code) for code in range(0x110000) if chr(code).isspace()}
    assert set(pdf_text._COLLAPSED_WHITESPACE) == every - {" ", " ", " "}


# --- the library internals pdf_text relies on (review turn 1) -----------------


def test_the_reshaper_override_is_active():
    # arabic-reshaper 3.0.0: _ligatures_re is a property of the base class and
    # reshape() reads it; the override caches it on the instance
    base = inspect.getattr_static(arabic_reshaper.ArabicReshaper, "_ligatures_re")
    assert isinstance(base, property)
    reshaper = pdf_text._Reshaper(configuration={"delete_harakat": False})
    assert "_ligatures_re" not in vars(reshaper)
    assert reshaper.reshape("لا") == "ﻻ"  # lam-alef ligature, isolated
    assert "_ligatures_re" in vars(reshaper)  # reshape() went through the override
    assert reshaper._ligatures_re is vars(reshaper)["_ligatures_re"]
    assert type(pdf_text._RESHAPER) is pdf_text._Reshaper


def test_python_bidi_levels_are_read_and_the_rule_path_is_taken():
    # a pin bump that changes the debug text or the order must fail here, not
    # silently fall back to get_display (which does not mirror brackets)
    line = "سلام (abc) دنیا"
    levels = pdf_text._resolved_levels(line, "R")
    assert levels == [1] * 6 + [2] * 3 + [1] * 6
    order = pdf_text._visual_order(pdf_text._line_levels(line, levels, 1))
    assert "".join(line[index] for index in order) == get_display(line, base_dir="R")
    # visual: دنیا reversed, (abc) with mirrored brackets, سلام reversed
    expected = "ایند (abc) مالس"
    assert pdf_text._display(line, "R") == expected
