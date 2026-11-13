"""Plain text lines for the report PDF, Persian included (HOM-138, ADR 0063).

reportlab draws the glyphs it is given, in the order it is given them.  It
does not join Arabic-script letters and does not reorder right-to-left text.
So every logical line goes through these steps before it is drawn:

1. whitespace runs become one space (as a reportlab ``Paragraph`` did).  The
   no-break spaces U+00A0, U+2007 and U+202F stay inside their word;
2. ``arabic_reshaper`` picks the joined form of each letter (presentation
   forms, lam-alef ligatures).  ZWNJ breaks a join.  Harakat are kept;
3. an Arabic presentation form the font lacks becomes its base letter.  Any
   other character the font lacks is kept and draws as a missing glyph;
4. the line is wrapped to the frame width in logical order, with the
   Vazirmatn metrics.  Wrapping after reordering would put the last words of
   a Persian sentence on the first line;
5. each wrapped line is reordered alone with the Unicode bidi algorithm
   (``python-bidi`` ``get_display``), in the direction of its logical line
   (the first strong character, UAX #9 P2 and P3).  Two display rules that
   ``get_display`` leaves out are added: a combining mark follows its base
   (L3) and a bracket in a right-to-left run is mirrored (L4);
6. bidi control characters are dropped (they did their work in step 5).

The result is plain text.  ``PlainTextParagraph`` draws it with
``drawString``, never through ``Paragraph`` markup, so a stored ``<b>`` or
``&amp;`` prints as itself.  The fonts are bundled
(``backend/assets/fonts``); nothing is downloaded at runtime.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import arabic_reshaper
from bidi import get_display
from bidi.mirror import MIRRORED
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable

__all__ = [
    "BOLD_FONT",
    "FONT_DIR",
    "REGULAR_FONT",
    "LineStyle",
    "PlainTextParagraph",
    "VisualLine",
    "clean_text",
    "paragraph_direction",
    "register_fonts",
    "visual_lines",
]

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
REGULAR_FONT = "Vazirmatn"
BOLD_FONT = "Vazirmatn-Bold"
_FONT_FILES = {REGULAR_FONT: "Vazirmatn-Regular.ttf", BOLD_FONT: "Vazirmatn-Bold.ttf"}

_registered = False
_register_lock = threading.Lock()


class _Reshaper(arabic_reshaper.ArabicReshaper):
    """``ArabicReshaper`` with its ligature pattern built once.

    arabic-reshaper 3.0.0 means to cache the pattern, but its ``hasattr``
    check uses the unmangled name, so every ``reshape`` call reads about 290
    options again (most of the time of a large Persian PDF).
    """

    @cached_property
    def _ligatures_re(self) -> re.Pattern[str]:
        return super()._ligatures_re


# Harakat are kept: Vazirmatn draws them with zero advance over their base.
_RESHAPER = _Reshaper(configuration={"delete_harakat": False})

# Unicode Bidi_Control characters: they steer the reordering, then are not drawn.
_BIDI_CONTROLS = frozenset("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")
_ISOLATE_OPENERS = frozenset({"LRI", "RLI", "FSI"})
_RTL_CLASSES = frozenset({"R", "AL", "AN", "RLE", "RLO", "RLI", "FSI"})
_REMOVED_BY_X9 = frozenset({"RLE", "LRE", "RLO", "LRO", "PDF", "BN"})
# The whitespace that becomes one plain space: every Python whitespace
# character except the no-break spaces U+00A0, U+2007 and U+202F.  Those stay
# inside their word, so a line never breaks there and ``۱۰<NBSP>000`` stays
# one number for the bidi algorithm (rule W4).
_COLLAPSED_WHITESPACE = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85"  # C0 separators, space, next line
    "        "  # ogham mark, quads, en/em spaces
    "      　"  # thin and other spaces, line/para separators
)
_WHITESPACE_RUN = re.compile(f"[{re.escape(_COLLAPSED_WHITESPACE)}]+")
_LEVELS = re.compile(r"levels: \[(.*?)\],\s*paragraphs:", re.S)
_DIGITS = re.compile(r"\d+")  # inside the levels block only the level numbers are digits


def register_fonts() -> None:
    """Register the bundled Vazirmatn fonts with reportlab, once per process."""
    global _registered
    if _registered:
        return
    with _register_lock:
        if _registered:
            return
        for name, file_name in _FONT_FILES.items():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file_name)))
        _registered = True


def _glyphs(font_name: str) -> dict[int, int]:
    register_fonts()
    return pdfmetrics.getFont(font_name).face.charToGlyph


def clean_text(value: object) -> str:
    """Every C0 control character except ``\\n`` becomes a space (HOM-137)."""
    text = value if isinstance(value, str) else str(value)
    return "".join(char if char == "\n" or ord(char) >= 32 else " " for char in text)


def paragraph_direction(text: str) -> str:
    """``"R"`` when the first strong character is right-to-left, else ``"L"``.

    UAX #9 P2 and P3: characters inside an isolate are skipped; a text with
    no strong character is left-to-right.
    """
    depth = 0
    for char in text:
        kind = unicodedata.bidirectional(char)
        if kind in _ISOLATE_OPENERS:
            depth += 1
        elif kind == "PDI":
            depth = max(depth - 1, 0)
        elif depth == 0 and kind == "L":
            return "L"
        elif depth == 0 and kind in ("R", "AL"):
            return "R"
    return "L"


def _collapse_whitespace(text: str) -> str:
    """Runs of ``_COLLAPSED_WHITESPACE`` become one space; the ends are trimmed."""
    return " ".join(word for word in _WHITESPACE_RUN.split(text) if word)


def _is_arabic_presentation_form(char: str) -> bool:
    code = ord(char)
    return 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF


def _with_font_fallback(text: str, glyphs: dict[int, int]) -> str:
    """An Arabic presentation form the font lacks becomes its base letter.

    The reshaper made that form, and its NFKC base is the same letter.  Any
    other character the font lacks is kept: it draws as a missing glyph.  NFKC
    would change its value (``10⁵`` would print as ``105``).
    """
    out = []
    for char in text:
        if ord(char) not in glyphs and _is_arabic_presentation_form(char):
            base = unicodedata.normalize("NFKC", char)
            if len(base) == 1 and ord(base) in glyphs:
                char = base
        out.append(char)
    return "".join(out)


def _break_word(word: str, *, font_name: str, size: float, width: float) -> list[str]:
    """Characters of a word wider than the frame, in frame-wide pieces.

    A combining mark stays with the character before it.
    """
    pieces: list[str] = []
    current = ""
    current_width = 0.0
    for char in word:
        char_width = pdfmetrics.stringWidth(char, font_name, size)
        is_mark = unicodedata.category(char) in ("Mn", "Me")
        if current and not is_mark and current_width + char_width > width:
            pieces.append(current)
            current, current_width = "", 0.0
        current += char
        current_width += char_width
    pieces.append(current)
    return pieces


def _wrap(words: list[str], *, font_name: str, size: float, width: float) -> list[str]:
    """Greedy wrapping in logical order; a line is never wider than ``width``."""
    space = pdfmetrics.stringWidth(" ", font_name, size)
    lines: list[str] = []
    current: str | None = None
    current_width = 0.0
    for word in words:
        word_width = pdfmetrics.stringWidth(word, font_name, size)
        if current is not None and current_width + space + word_width <= width:
            current += " " + word
            current_width += space + word_width
            continue
        if current is not None:
            lines.append(current)
        if word_width <= width:
            current, current_width = word, word_width
            continue
        pieces = _break_word(word, font_name=font_name, size=size, width=width)
        lines.extend(pieces[:-1])
        current = pieces[-1]
        current_width = pdfmetrics.stringWidth(current, font_name, size)
    lines.append(current or "")
    return lines


def _resolved_levels(line: str, direction: str) -> list[int] | None:
    """The embedding level of each character, as ``python-bidi`` resolves it.

    ``get_display`` with ``debug=True`` returns the Rust ``BidiInfo``; its
    ``levels`` are per UTF-8 byte.  ``None`` when the shape is not the one
    expected (the caller then keeps ``get_display`` as it is).
    """
    match = _LEVELS.search(get_display(line, base_dir=direction, debug=True))
    if match is None:
        return None
    per_byte = [int(level) for level in _DIGITS.findall(match.group(1))]
    levels: list[int] = []
    offset = 0
    for char in line:
        if offset >= len(per_byte):
            return None
        levels.append(per_byte[offset])
        offset += len(char.encode("utf-8"))
    return levels if offset == len(per_byte) else None


def _line_levels(line: str, levels: list[int], paragraph_level: int) -> list[int]:
    """UAX #9 L1, as ``unicode-bidi`` applies it to one line."""
    adjusted = list(levels)
    reset_from: int | None = 0
    previous = paragraph_level
    for index, char in enumerate(line):
        kind = unicodedata.bidirectional(char)
        reset_to: int | None = None
        if kind in ("B", "S"):
            reset_to = index + 1
            if reset_from is None:
                reset_from = index
        elif kind in ("WS", "FSI", "LRI", "RLI", "PDI"):
            if reset_from is None:
                reset_from = index
        elif kind in _REMOVED_BY_X9:
            if reset_from is None:
                reset_from = index
            adjusted[index] = previous
        else:
            reset_from = None
        if reset_from is not None and reset_to is not None:
            adjusted[reset_from:reset_to] = [paragraph_level] * (reset_to - reset_from)
            reset_from = None
        previous = adjusted[index]
    if reset_from is not None:
        adjusted[reset_from:] = [paragraph_level] * (len(line) - reset_from)
    return adjusted


def _visual_order(levels: list[int]) -> list[int]:
    """UAX #9 L2: logical indexes in visual order."""
    order = list(range(len(levels)))
    odd = [level for level in levels if level % 2]
    if not odd:
        return order
    current = list(levels)
    for level in range(max(levels), min(odd) - 1, -1):
        index = 0
        while index < len(order):
            if current[index] < level:
                index += 1
                continue
            end = index
            while end < len(order) and current[end] >= level:
                end += 1
            order[index:end] = order[index:end][::-1]
            current[index:end] = current[index:end][::-1]
            index = end
    return order


def _marks_and_mirrors(line: str, order: list[int], levels: list[int]) -> str:
    """UAX #9 L3 (a mark follows its base) and L4 (mirrored brackets) in RTL runs."""
    out: list[str] = []
    index = 0
    while index < len(order):
        logical = order[index]
        if levels[logical] % 2 and unicodedata.category(line[logical]) in ("Mn", "Me"):
            end = index
            while (
                end < len(order)
                and levels[order[end]] % 2
                and unicodedata.category(line[order[end]]) in ("Mn", "Me")
            ):
                end += 1
            marks = order[index:end]
            if end < len(order):
                out.append(_mirrored(line, order[end], levels))
                end += 1
            out.extend(line[mark] for mark in reversed(marks))
            index = end
            continue
        out.append(_mirrored(line, logical, levels))
        index += 1
    return "".join(out)


def _mirrored(line: str, logical: int, levels: list[int]) -> str:
    char = line[logical]
    return MIRRORED.get(char, char) if levels[logical] % 2 else char


def _display(line: str, direction: str) -> str:
    """One wrapped line in visual (left-to-right drawing) order."""
    display = get_display(line, base_dir=direction)
    needs_rules = any(
        char in MIRRORED or unicodedata.category(char) in ("Mn", "Me") for char in line
    ) and (direction == "R" or any(unicodedata.bidirectional(c) in _RTL_CLASSES for c in line))
    if not needs_rules:
        return display
    resolved = _resolved_levels(line, direction)
    if resolved is None:
        return display
    levels = _line_levels(line, resolved, 1 if direction == "R" else 0)
    order = _visual_order(levels)
    if "".join(line[index] for index in order) != display:
        return display  # our reading of the levels disagrees; keep get_display
    return _marks_and_mirrors(line, order, levels)


def _drawable(text: str, glyphs: dict[int, int]) -> str:
    """Drop bidi controls and other invisible characters the font has no glyph for."""
    return "".join(
        char
        for char in text
        if char not in _BIDI_CONTROLS
        and (ord(char) in glyphs or unicodedata.category(char) not in ("Cc", "Cf"))
    )


@dataclass(frozen=True)
class VisualLine:
    text: str  # in drawing order, left to right
    align: str  # "left" or "right"


def visual_lines(text: str, *, font_name: str, size: float, width: float) -> list[VisualLine]:
    """The drawn lines of one cleaned text, top to bottom.

    Each ``\\n`` starts a logical line.  A right-to-left logical line is
    right-aligned, a left-to-right one left-aligned.
    """
    glyphs = _glyphs(font_name)
    lines: list[VisualLine] = []
    for logical in text.split("\n"):
        direction = paragraph_direction(logical)
        shaped = _with_font_fallback(_RESHAPER.reshape(_collapse_whitespace(logical)), glyphs)
        align = "right" if direction == "R" else "left"
        # only a plain space is a line break; the no-break spaces stay in the word
        for wrapped in _wrap(shaped.split(" "), font_name=font_name, size=size, width=width):
            lines.append(VisualLine(_drawable(_display(wrapped, direction), glyphs), align))
    return lines


@dataclass(frozen=True)
class LineStyle:
    font_name: str
    size: float
    leading: float
    space_before: float = 0.0
    space_after: float = 0.0


class PlainTextParagraph(Flowable):
    """One paragraph of plain text, drawn line by line.

    No markup is parsed: the text is drawn with ``drawString`` /
    ``drawRightString``.  It wraps to the frame width and splits across pages
    between lines.
    """

    def __init__(
        self,
        text: str,
        style: LineStyle,
        *,
        lines: list[VisualLine] | None = None,
        lines_width: float | None = None,
    ) -> None:
        super().__init__()
        self.text = text
        self.style = style
        self.spaceBefore = style.space_before
        self.spaceAfter = style.space_after
        self._lines = lines
        # a piece made by ``split`` keeps its lines: re-wrapping the whole text
        # would draw the head lines again
        self._fixed = lines is not None
        self._layouts: dict[float, list[VisualLine]] = {}
        if lines is not None and lines_width is not None:
            self._layouts[lines_width] = lines

    def _lines_for(self, width: float) -> list[VisualLine]:
        if self._fixed:
            return self._lines or []
        if width not in self._layouts:
            self._layouts[width] = visual_lines(
                self.text, font_name=self.style.font_name, size=self.style.size, width=width
            )
        self._lines = self._layouts[width]
        return self._lines

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: N803
        lines = self._lines_for(availWidth)
        self.width = availWidth
        self.height = len(lines) * self.style.leading
        return self.width, self.height

    def split(self, availWidth: float, availHeight: float) -> list[Flowable]:  # noqa: N803
        lines = self._lines_for(availWidth)
        fit = int((availHeight + 1e-6) // self.style.leading)
        if fit <= 0:
            return []
        if fit >= len(lines):
            return [self]
        head_style = LineStyle(
            self.style.font_name, self.style.size, self.style.leading, self.style.space_before, 0
        )
        tail_style = LineStyle(
            self.style.font_name, self.style.size, self.style.leading, 0, self.style.space_after
        )
        return [
            PlainTextParagraph(self.text, head_style, lines=lines[:fit], lines_width=availWidth),
            PlainTextParagraph(self.text, tail_style, lines=lines[fit:], lines_width=availWidth),
        ]

    def draw(self) -> None:
        style = self.style
        ascent, descent = pdfmetrics.getAscentDescent(style.font_name, style.size)
        # the glyph box (ascent to descent) sits in the middle of each line box
        baseline = (style.leading - (ascent - descent)) / 2 - descent
        canvas = self.canv
        canvas.setFont(style.font_name, style.size)
        for index, line in enumerate(self._lines or []):
            if not line.text:
                continue
            y = self.height - (index + 1) * style.leading + baseline
            if line.align == "right":
                canvas.drawRightString(self.width, y, line.text)
            else:
                canvas.drawString(0, y, line.text)
