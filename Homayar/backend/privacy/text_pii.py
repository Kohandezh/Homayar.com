"""Deterministic Persian/English text PII detection and redaction (WP07-T05, ADR 0023).

Supported classes are structured identifiers that can be validated, not
guessed: e-mail addresses, phone numbers (Iranian mobile/landline in every
common spelling, international ``+``/``00`` numbers), Iranian national IDs
(check digit), bank cards (Luhn), IBANs (ISO 13616 mod 97, ``IR`` included) and
Iranian postal codes (only next to a postal-code keyword: ten digits without a
check digit are otherwise indistinguishable from other numbers).

Persian (``۰``–``۹``) and Arabic-Indic (``٠``–``٩``) digits, spaces, hyphens and
dashes inside a number, ``+98``/``0098``/``98``/``0`` prefixes and parenthesised
area codes are normalised; offsets always refer to the original text.

NOT detected (disclosed residual risk): person and company names, street
addresses, dates of birth, licence plates, passport and account numbers
without a checksum, identifiers spelled out in words (``صفر نهصد و دوازده``),
numbers split across transcript segments, and anything a speaker paraphrases.
Redaction therefore reduces exposure; it is not anonymisation, and it is text
only — it never masks audio.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable
from dataclasses import dataclass

DETECTOR_VERSION = "text-pii/1"


class PiiClass(str, enum.Enum):
    EMAIL = "email"
    PHONE = "phone"
    IR_NATIONAL_ID = "ir_national_id"
    BANK_CARD = "bank_card"
    IBAN = "iban"
    IR_POSTAL_CODE = "ir_postal_code"


SUPPORTED_CLASSES: frozenset[PiiClass] = frozenset(PiiClass)

# When candidates overlap, the more specific (better validated) class wins.
_PRIORITY = {
    PiiClass.EMAIL: 6,
    PiiClass.IBAN: 5,
    PiiClass.BANK_CARD: 4,
    PiiClass.PHONE: 3,
    PiiClass.IR_NATIONAL_ID: 2,
    PiiClass.IR_POSTAL_CODE: 1,
}

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
# One optional separator between digits: space, NBSP, hyphen and dashes.
_SEP = r"[  \-‐‑‒–]?"
_NOT_ALNUM_BEFORE = r"(?<![0-9A-Za-z])"
_NOT_ALNUM_AFTER = r"(?![0-9A-Za-z])"


def normalize_digits(text: str) -> str:
    """Persian/Arabic-Indic digits to ASCII; one character each, offsets kept."""
    return text.translate(_DIGITS)


def _digits(n_min: int, n_max: int | None = None) -> str:
    n_max = n_max or n_min
    return rf"\d(?:{_SEP}\d){{{n_min - 1},{n_max - 1}}}"


_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"\.[A-Za-z]{2,24}(?![\w-])"
)
# Iranian mobile: 0 9xx ..., +98 9xx ..., 0098 9xx ..., 98 9xx ...
_IR_MOBILE_RE = re.compile(
    _NOT_ALNUM_BEFORE
    + rf"(?:\+{_SEP}98|0{_SEP}0{_SEP}9{_SEP}8|9{_SEP}8|0){_SEP}\(?9{_SEP}[0-49]\)?{_SEP}"
    + _digits(8)
    + _NOT_ALNUM_AFTER
)
# Iranian landline: 0 + two-digit area code + 8 digits, area code may be in brackets.
_IR_LANDLINE_RE = re.compile(
    _NOT_ALNUM_BEFORE
    + rf"(?:\(\s?0{_SEP}[1-8]\d\s?\)|0{_SEP}[1-8]{_SEP}\d){_SEP}"
    + _digits(8)
    + _NOT_ALNUM_AFTER
)
# International numbers need an explicit + or 00 prefix (8–15 digits).
_INTL_PHONE_RE = re.compile(
    r"(?<![0-9A-Za-z+])(?:\+|00)"
    + _SEP
    + r"[1-9](?:"
    + _SEP
    + r"\(?\d\)?){7,14}"
    + _NOT_ALNUM_AFTER
)
_TEN_DIGITS_RE = re.compile(_NOT_ALNUM_BEFORE + _digits(10) + _NOT_ALNUM_AFTER)
_CARD_RE = re.compile(_NOT_ALNUM_BEFORE + _digits(13, 19) + _NOT_ALNUM_AFTER)
_IBAN_RE = re.compile(
    r"(?<![0-9A-Za-z])[A-Za-z]{2}"
    + _SEP
    + r"\d{2}(?:"
    + _SEP
    + r"[A-Za-z0-9]){11,30}(?![0-9A-Za-z])"
)
# The keyword, optionally followed by up to two short words ("کد پستی منزل",
# "postal code of office"), then only separators up to the number.
_POSTAL_KEYWORD_RE = re.compile(
    r"(?:کد\s?پستی|کدپستی|postal\s+code|post\s?code|zip(?:\s+code)?)"
    r"(?:\s+[^\s\d]{1,12}){0,2}[\s:：\-]*$",
    re.IGNORECASE,
)
_POSTAL_WINDOW = 48


@dataclass(frozen=True)
class PiiSpan:
    start: int
    end: int
    pii_class: PiiClass
    value: str  # canonical form (digits only / lowercased e-mail), never logged


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def valid_national_id(digits: str) -> bool:
    if len(digits) != 10 or len(set(digits)) == 1:
        return False
    check = int(digits[9])
    total = sum(int(digits[i]) * (10 - i) for i in range(9)) % 11
    return check == total if total < 2 else check == 11 - total


def valid_luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def valid_iban(compact: str) -> bool:
    compact = compact.upper()
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    if compact[:2] == "IR" and len(compact) != 26:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(char, 36)) for char in rearranged)
    return int(numeric) % 97 == 1


def _canonical_phone(raw: str) -> str | None:
    digits = _only_digits(raw)
    if raw.lstrip().startswith("+"):
        digits = "00" + digits
    if digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    if digits.startswith("00"):
        # A bare "00" + 8 digits is how many Iranian national IDs start
        # (Tehran): an international number needs at least 9 digits after 00.
        minimum = 10 if raw.lstrip().startswith("+") else 11
        return digits if minimum <= len(digits) <= 17 else None
    return digits if len(digits) == 11 and digits.startswith("0") else None


def detect(text: str, classes: Iterable[PiiClass] = SUPPORTED_CLASSES) -> list[PiiSpan]:
    """Non-overlapping spans of the requested classes, in text order."""
    wanted = frozenset(classes)
    norm = normalize_digits(text)
    candidates: list[PiiSpan] = []

    def add(start: int, end: int, pii_class: PiiClass, value: str) -> None:
        if pii_class in wanted:
            candidates.append(PiiSpan(start, end, pii_class, value))

    for match in _EMAIL_RE.finditer(norm):
        add(match.start(), match.end(), PiiClass.EMAIL, match.group().lower())
    for regex in (_IR_MOBILE_RE, _IR_LANDLINE_RE, _INTL_PHONE_RE):
        for match in regex.finditer(norm):
            canonical = _canonical_phone(match.group())
            if canonical is not None:
                add(match.start(), match.end(), PiiClass.PHONE, canonical)
    for match in _IBAN_RE.finditer(norm):
        compact = re.sub(r"[^0-9A-Za-z]", "", match.group()).upper()
        if valid_iban(compact):
            add(match.start(), match.end(), PiiClass.IBAN, compact)
    for match in _CARD_RE.finditer(norm):
        digits = _only_digits(match.group())
        if valid_luhn(digits):
            add(match.start(), match.end(), PiiClass.BANK_CARD, digits)
    for match in _TEN_DIGITS_RE.finditer(norm):
        digits = _only_digits(match.group())
        if _POSTAL_KEYWORD_RE.search(norm[max(0, match.start() - _POSTAL_WINDOW) : match.start()]):
            # A keyword names the number: a postal code, even when the digits
            # happen to satisfy the national-ID check digit.
            add(match.start(), match.end(), PiiClass.IR_POSTAL_CODE, digits)
        elif valid_national_id(digits):
            add(match.start(), match.end(), PiiClass.IR_NATIONAL_ID, digits)

    def rank(span: PiiSpan) -> tuple[int, int, int]:
        # Longest candidate first, then the better validated class.
        return (-(span.end - span.start), -_PRIORITY[span.pii_class], span.start)

    chosen: list[PiiSpan] = []
    for span in sorted(candidates, key=rank):
        if all(span.end <= kept.start or span.start >= kept.end for kept in chosen):
            chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    pii_class: PiiClass
    placeholder: str
    value: str


@dataclass
class Pseudonymizer:
    """Stable placeholders across the segments of one transcript: the same
    value gets the same ``[CLASS_n]`` token everywhere in that redaction."""

    tokens: dict[tuple[PiiClass, str], str]
    counters: dict[PiiClass, int]

    def __init__(self) -> None:
        self.tokens = {}
        self.counters = {}

    def token(self, pii_class: PiiClass, value: str) -> str:
        key = (pii_class, value)
        if key not in self.tokens:
            self.counters[pii_class] = self.counters.get(pii_class, 0) + 1
            self.tokens[key] = f"[{pii_class.name}_{self.counters[pii_class]}]"
        return self.tokens[key]


def redact(
    text: str,
    classes: Iterable[PiiClass] = SUPPORTED_CLASSES,
    *,
    pseudonymizer: Pseudonymizer | None = None,
) -> tuple[str, list[Replacement]]:
    """Redacted text plus the replacements (with originals: restricted data)."""
    pseudonymizer = pseudonymizer or Pseudonymizer()
    spans = detect(text, classes)
    out: list[str] = []
    replacements: list[Replacement] = []
    cursor = 0
    for span in spans:
        token = pseudonymizer.token(span.pii_class, span.value)
        out.append(text[cursor : span.start])
        out.append(token)
        replacements.append(
            Replacement(span.start, span.end, span.pii_class, token, text[span.start : span.end])
        )
        cursor = span.end
    out.append(text[cursor:])
    return "".join(out), replacements


def parse_classes(values: Iterable[str]) -> list[PiiClass]:
    """Validate a configured class list (unknown names are refused)."""
    result: list[PiiClass] = []
    for value in values:
        try:
            pii_class = PiiClass(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported PII class: {value!r}") from exc
        if pii_class not in result:
            result.append(pii_class)
    return sorted(result, key=lambda c: c.value)
