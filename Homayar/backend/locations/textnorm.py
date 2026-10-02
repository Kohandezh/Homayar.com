"""Persian/English character-stream normalizer with an offset map (locations profile).

The tables come from the single normalization owner,
:mod:`backend.speech.text_normalize`; this module only keeps the locations
profile's own per-character step order and the offset-map contract
(normalized index -> original index) that span reporting relies on.  The
profile deliberately differs from the speech and voicemail ones (no Unicode
normalization, no ``ۀ``/``ؤ``/``ٲ`` folding) because its output is persisted in
service location address fields; see the owner's documentation.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.speech.text_normalize import (
    ARABIC_NUMBER_PUNCTUATION,
    HARAKAT,
    LETTER_VARIANTS_BASIC,
    LRM,
    RLM,
    TATWEEL,
    ZWJ,
    ZWNJ,
    normalize_digits,
)

_CHAR_MAP = {
    **LETTER_VARIANTS_BASIC,
    ZWNJ: " ",
    ZWJ: "",
    RLM: "",
    LRM: "",
    TATWEEL: "",
    **ARABIC_NUMBER_PUNCTUATION,
    "\xa0": " ",
}
# Harakat / tanwin / shadda / sukun / superscript alef: dropped.
_DROP = HARAKAT


@dataclass(frozen=True)
class NormalizedText:
    text: str
    offsets: tuple[int, ...]  # offsets[i] = original index of normalized char i
    original: str

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Map a normalized [start, end) span back onto the original text."""
        if start >= len(self.offsets):
            return len(self.original), len(self.original)
        o_start = self.offsets[start]
        o_end = self.offsets[end - 1] + 1 if end > start else o_start
        return o_start, o_end


def normalize_char_stream(text: str) -> NormalizedText:
    digits = normalize_digits(text)
    out: list[str] = []
    offsets: list[int] = []
    prev_space = False
    for index, char in enumerate(digits):
        if char in _DROP:
            continue
        mapped = _CHAR_MAP.get(char, char)
        if mapped == "":
            continue
        if len(mapped) == 1 and mapped.isascii() and mapped.isalpha():
            mapped = mapped.lower()
        if mapped.isspace():
            if prev_space:
                continue
            mapped = " "
            prev_space = True
        else:
            prev_space = False
        out.append(mapped)
        offsets.append(index)
    return NormalizedText(text="".join(out), offsets=tuple(offsets), original=text)


def normalize_text(text: str) -> str:
    return normalize_char_stream(text).text.strip()


def is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"
