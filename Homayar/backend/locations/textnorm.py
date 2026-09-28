"""Minimal local Persian/English normalizer with an offset map.

TODO(consolidate): a concurrent package (``backend/speech``) is building a
full Persian normalizer.  This module is intentionally small and local so the
locations package has no dependency on it; fold it into the shared normalizer
once that lands, keeping the offset-map contract (normalized index -> original
index) that span reporting relies on.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.privacy.text_pii import normalize_digits

_CHAR_MAP = {
    "ي": "ی",  # Arabic yeh -> Persian yeh
    "ى": "ی",  # alef maksura -> Persian yeh
    "ك": "ک",  # Arabic kaf -> Persian keheh
    "ة": "ه",  # teh marbuta -> heh
    "أ": "ا",
    "إ": "ا",
    "ٱ": "ا",
    "‌": " ",  # ZWNJ -> space
    "‍": "",
    "‏": "",
    "‎": "",
    "ـ": "",  # tatweel
    "٫": ".",  # Arabic decimal separator
    "٬": ",",
    "،": ",",  # Arabic comma
    " ": " ",
}
# Harakat / tanwin / shadda / sukun: dropped.
_DROP = {chr(c) for c in range(0x064B, 0x0660)} | {"ٰ"}


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
