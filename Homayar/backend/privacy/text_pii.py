"""Deterministic Persian/English text PII detection and redaction (WP07-T05, ADR 0023).

Supported classes are structured identifiers that can be validated, not
guessed: e-mail addresses, phone numbers (Iranian mobile/landline in every
common spelling, international ``+``/``00`` numbers), Iranian national IDs
(check digit), bank cards (Luhn), IBANs (ISO 13616 mod 97, ``IR`` included) and
Iranian postal codes (only next to a postal-code keyword: ten digits without a
check digit are otherwise indistinguishable from other numbers).

Persian (``۰``–``۹``), Arabic-Indic (``٠``–``٩``) and fullwidth (``０``–``９``)
digits are folded to ASCII, and stored values are ASCII.  All other
(non-canonical) digit scripts, such as Devanagari, Bengali or mathematical
digits, are deliberately not folded and never count as digits.  Spaces, hyphens and
dashes inside a number, ``+98``/``0098``/``98``/``0`` prefixes and parenthesised
area codes are normalised; offsets always refer to the original text.  The
postal-code keyword is matched after the owner's one-to-one Arabic-to-Persian
letter folding (``كد پستي``), with harakat and tatweel ignored (``کدّ پستی``,
``کـد پستی``), and accepts a ZWNJ between its words (``کد‌پستی``); only the
keyword window is folded, so the offsets of a detected number are unaffected.
A national ID written directly next to a phone number, with or without a
separator, is read as two identifiers.  Digits joined by single separators
are read as national IDs, postal codes, Iranian phones and bank cards when
whole written groups of them split exactly into two or more of the requested
classes.  Only a split that holds an ID or a phone may leave out whole groups
at the ends of the run (cards alone must fill it), so a short number before or
after usually stays outside; by chance it can join a neighbour, as in
``1403-001234567909123456789-12``, where the phone and the ``12`` pass Luhn
as one card.  A piece that would cut another reading of its own class is not
used, unless that reading holds a reading of another class (it then reads
across two identifiers, like ``0012345679 0912`` read as one ``00`` number).
This reading also redacts some numbers that are not identifiers, such as a
long code or a price list that happens to split into a card and an ID.
For readings of different classes that overlap where no set of them covers
every letter and digit, the whole overlap is redacted as one span with one
placeholder and the value of its best reading (for example a phone glued to
an e-mail).  Readings of one class that overlap keep their longest-first spans
and values, each grown over the letters and digits up to the next one.
A regex reading can use up digits that a neighbour needs (a card regex reads
13 to 19 digits from the first digit it meets).  So identifiers are also read
in their usual written form, and used only where the readings above leave one
of their digits raw: a Luhn card as one group of 13 to 19 digits, or as four
groups of four digits when each group next to it is missing, at most two
digits long, another such card or part of another reading; a ``00`` number
at any group of a digit run; and a mobile number with at most three digits
glued before or after it in one group (a national ID with at most four, but
only after an ID keyword such as ``کد ملی``), when that group is not inside a
longer reading of any class.  A glued reading may raise the share of random
one-group numbers of 11 to 19 digits that get a span by at most 2 percentage
points; four glued digits on a mobile would already raise it by 2.2.
Where such a reading cuts a span chosen without it, the span before the cut
grows over the separator, so nothing redacted before is shown again.

NOT detected (disclosed residual risk): person and company names, street
addresses, dates of birth, licence plates, passport and account numbers
without a checksum, identifiers spelled out in words (``صفر نهصد و دوازده``),
numbers split across transcript segments, joined digits that do not split
into whole identifiers (21 digits that are not an ID plus a phone, a split
that would cut another reading of the same class, or joined identifiers whose
split needs a class that was not requested, for example a card glued to an ID
with the speech entity classes), and anything a speaker paraphrases.  These
glued or joined shapes are not read because the same rule would redact too
many other numbers (measured on random inputs):
a national ID or a landline with digits glued to it and no ID keyword (a check
digit holds by chance in about 1 of 10 places, the landline pattern in about 8
of 100: random 12 to 14 digit numbers would go from 0-10% to 34-60% redacted);
a number glued to the first or last group of a grouped ID or phone
(``120912 345 6789``: 26-32% more price lists redacted); two identifiers glued
in one group with another number (2.7-32% more 21 to 26 digit codes); and a
card in groups of four next to a number of three or more digits, which is
also the shape of a price list.  Not read either: a card with digits glued to
it, and a ``00`` or ``+`` number with a digit glued before it.
Redaction therefore reduces exposure; it is not anonymisation, and it is text
only — it never masks audio.
"""

from __future__ import annotations

import enum
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from backend.speech.text_normalize import (
    normalize_digits as normalize_digits,  # public name kept for its consumers
)
from backend.speech.text_normalize import (
    normalize_digits_extended,
    strip_diacritics,
    unify_arabic_letters,
)

# text-pii/2 (HOM-164): Arabic-letter and ZWNJ spellings of the postal keyword.
# text-pii/3 (HOM-165): fullwidth digits fold to ASCII; only ASCII counts as a digit.
# text-pii/4 (HOM-171): harakat and tatweel inside the postal keyword are ignored.
# text-pii/5 (HOM-291): an ID next to a phone number is two identifiers, no digit left raw.
# text-pii/6 (HOM-303): a card, a 00 number or a glued mobile is read where a neighbour
# used up its digits.
DETECTOR_VERSION = "text-pii/6"


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

# One optional separator between digits: space, NBSP, hyphen and dashes.
_SEP = r"[  \-‐‑‒–]?"
_NOT_ALNUM_BEFORE = r"(?<![0-9A-Za-z])"
_NOT_ALNUM_AFTER = r"(?![0-9A-Za-z])"


def _digits(n_min: int, n_max: int | None = None) -> str:
    n_max = n_max or n_min
    return rf"[0-9](?:{_SEP}[0-9]){{{n_min - 1},{n_max - 1}}}"


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
    + rf"(?:\(\s?0{_SEP}[1-8][0-9]\s?\)|0{_SEP}[1-8]{_SEP}[0-9]){_SEP}"
    + _digits(8)
    + _NOT_ALNUM_AFTER
)
# International numbers need an explicit + or 00 prefix (8–15 digits).
_INTL_PHONE_RE = re.compile(
    r"(?<![0-9A-Za-z+])(?:\+|00)"
    + _SEP
    + r"[1-9](?:"
    + _SEP
    + r"\(?[0-9]\)?){7,14}"
    + _NOT_ALNUM_AFTER
)
_TEN_DIGITS_RE = re.compile(_NOT_ALNUM_BEFORE + _digits(10) + _NOT_ALNUM_AFTER)
_CARD_RE = re.compile(_NOT_ALNUM_BEFORE + _digits(13, 19) + _NOT_ALNUM_AFTER)
# Digits joined by single separators: an ID written straight before a phone
# number is one run, read as two identifiers when it splits exactly (HOM-291).
_DIGIT_RUN_RE = re.compile(r"[0-9](?:" + _SEP + r"[0-9])*")
_IBAN_RE = re.compile(
    r"(?<![0-9A-Za-z])[A-Za-z]{2}"
    + _SEP
    + r"[0-9]{2}(?:"
    + _SEP
    + r"[A-Za-z0-9]){11,30}(?![0-9A-Za-z])"
)
# The keyword, optionally followed by up to two short words ("کد پستی منزل",
# "postal code of office"), then only separators up to the number.  Matched
# on letter-folded text without harakat and tatweel, so Arabic kaf/yeh and
# marked spellings are covered; a ZWNJ may stand where a space would.
_POSTAL_KEYWORD_RE = re.compile(
    r"(?:کد[\s\u200c]?پستی|postal\s+code|post\s?code|zip(?:\s+code)?)"
    r"(?:\s+[^\s\d]{1,12}){0,2}[\s:：\-]*$",
    re.IGNORECASE,
)
_POSTAL_WINDOW = 48
# The national-ID keyword, in the style of the postal one: only after it is an
# ID with digits glued to it read (HOM-303).
_ID_KEYWORD_RE = re.compile(
    r"(?:کد[\s\u200c]?ملی|شماره[\s\u200c]?ملی|national\s+id)"
    r"(?:\s+[^\s\d]{1,12}){0,2}[\s:：\-]*$",
    re.IGNORECASE,
)
_GLUED_MOBILE = 3  # at most this many digits glued to a mobile
_GLUED_ID = 4  # and to an ID after an ID keyword
_GROUP_RE = re.compile(r"[0-9]+")
_MOBILE_DIGITS_RE = re.compile(r"09[0-49][0-9]{8}")


@dataclass(frozen=True)
class PiiSpan:
    start: int
    end: int
    pii_class: PiiClass
    value: str  # canonical form (digits only / lowercased e-mail), never logged


def _only_digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


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


def _ten_digit_class(folded: str, start: int, digits: str) -> PiiClass | None:
    # Harakat and tatweel leave the keyword window only (HOM-171): the
    # keyword test needs no offsets, and the span stays on ``norm``.
    window = strip_diacritics(folded[max(0, start - _POSTAL_WINDOW) : start])
    if _POSTAL_KEYWORD_RE.search(window):
        # A keyword names the number: a postal code, even when the digits
        # happen to satisfy the national-ID check digit.
        return PiiClass.IR_POSTAL_CODE
    return PiiClass.IR_NATIONAL_ID if valid_national_id(digits) else None


def _split_run(
    norm: str,
    folded: str,
    at: list[int],
    wanted: frozenset[PiiClass],
    matches: list[PiiSpan],
    match_starts: list[int],
    longest: int,
) -> list[tuple[int, int, PiiClass, str]]:
    """Readings of the pieces of one digit run (its digits at ``at``) that lie
    on a split of whole written groups of the run into two or more wanted
    identifiers of 10 to 19 digits each."""
    if len(at) < 20:
        return []  # two identifiers need at least 10 + 10 digits
    count = len(at)
    digits = "".join(norm[k] for k in at)
    # A group is digits written without a separator.  A split starts at the
    # first digit of a group and ends after the last digit of a group.
    group_start = [k == 0 or at[k] != at[k - 1] + 1 for k in range(count)]
    group_end = [k == count or at[k] != at[k - 1] + 1 for k in range(1, count + 1)]
    # The Luhn total of any piece from two prefix sums: a digit is doubled when
    # it stands an odd number of places before the last digit of the piece,
    # so only the parity of its position matters.
    luhn: tuple[list[int], list[int]] = ([0], [0])
    for m, char in enumerate(digits):
        value = int(char)
        twice = 2 * value - 9 if value > 4 else 2 * value
        luhn[0].append(luhn[0][-1] + (twice if m % 2 == 0 else value))
        luhn[1].append(luhn[1][-1] + (twice if m % 2 == 1 else value))

    def near(start: int, end: int) -> list[PiiSpan]:
        # The regex matches that share a character with norm[start:end].
        first = bisect_left(match_starts, start - longest)
        return [m for m in matches[first : bisect_left(match_starts, end)] if m.end > start]

    known: dict[tuple[int, int], list[tuple[PiiClass, str]]] = {}

    def readings(i: int, j: int) -> list[tuple[PiiClass, str]]:
        # The wanted readings of digits[i:j] on their own.
        if (i, j) in known:
            return known[i, j]
        piece = digits[i:j]
        start, end = at[i], at[j - 1] + 1
        found: list[tuple[PiiClass, str]] = []
        if j - i == 10:
            pii_class = _ten_digit_class(folded, start, piece)
            if pii_class is not None:
                found.append((pii_class, piece))
        if piece[0] == "0" or piece[:2] == "98":
            raw = norm[start:end]
            if _IR_MOBILE_RE.fullmatch(raw) or _IR_LANDLINE_RE.fullmatch(raw):
                canonical = _canonical_phone(raw)
                if canonical is not None:
                    found.append((PiiClass.PHONE, canonical))
        sums = luhn[j % 2]
        if j - i >= 13 and (sums[j] - sums[i]) % 10 == 0 and valid_luhn(piece):
            found.append((PiiClass.BANK_CARD, piece))
        # A piece that holds a whole match of another class is a chance
        # reading around a real identifier (a Luhn hit that holds an ID).
        inside = [m for m in near(start, end) if start <= m.start and m.end <= end] if found else []
        known[i, j] = [
            (pii_class, value)
            for pii_class, value in found
            if pii_class in wanted
            and not any(
                m.pii_class is not pii_class and (m.start, m.end) != (start, end) for m in inside
            )
        ]
        return known[i, j]

    def split(
        allowed: Callable[[int, int, PiiClass], bool],
    ) -> list[tuple[int, int, PiiClass, str]]:
        # Forward and backward passes over the pieces; a state is (split
        # touches the run's first or last digit, split holds an ID or a phone
        # piece), and its value the most pieces (-1 none, else up to 2).
        anchor = (PiiClass.IR_NATIONAL_ID, PiiClass.PHONE)
        upto = [[-1] * (count + 1) for _ in range(4)]
        upto[2][0] = 0
        for k in range(1, count):
            if group_start[k]:
                upto[0][k] = 0
        edges: list[tuple[int, int, PiiClass, str]] = []
        for i in range(count):
            live = [state for state in range(4) if upto[state][i] >= 0]
            if not live:
                continue
            for j in range(i + 10, min(i + 19, count) + 1):
                for pii_class, value in readings(i, j):
                    if not allowed(at[i], at[j - 1] + 1, pii_class):
                        continue
                    for state in live:
                        target = state | 1 if pii_class in anchor else state
                        upto[target][j] = max(upto[target][j], min(upto[state][i] + 1, 2))
                    edges.append((i, j, pii_class, value))
        after = [[-1] * (count + 1) for _ in range(4)]
        after[2][count] = 0
        for k in range(1, count):
            if group_end[k - 1]:
                after[0][k] = 0
        pieces: list[tuple[int, int, PiiClass, str]] = []
        for i, j, pii_class, value in reversed(edges):
            held = pii_class in anchor
            if any(
                upto[left][i] + 1 + after[right][j] >= 2
                # Skipping whole groups at an edge of the run needs an ID or
                # a phone piece: a card-only split must span the whole run.
                and (left & right & 2 or left & 1 or right & 1 or held)
                for left in range(4)
                if upto[left][i] >= 0
                for right in range(4)
                if after[right][j] >= 0
            ):
                pieces.append((at[i], at[j - 1] + 1, pii_class, value))
            for right in range(4):
                if after[right][j] >= 0:
                    target = right | 1 if held else right
                    after[target][i] = max(after[target][i], min(after[right][j] + 1, 2))
        return pieces

    # A match that holds a piece or a match of another class reads across two
    # identifiers (``0012345679 0912`` as one international number): a piece
    # may cut it.  Any other match keeps its class: a piece of that class that
    # cuts into it, lies in it or holds it is a chance reading.
    first = split(lambda start, end, pii_class: True)
    inner = sorted(
        [(start, end, pii_class) for start, end, pii_class, _ in first]
        + [(m.start, m.end, m.pii_class) for m in near(at[0], at[-1] + 1)]
    )
    inner_starts = [start for start, _, _ in inner]
    crossing: dict[tuple[int, int, PiiClass], bool] = {}

    def across(match: PiiSpan) -> bool:
        key = (match.start, match.end, match.pii_class)
        if key not in crossing:
            low = bisect_left(inner_starts, match.start)
            crossing[key] = any(
                pii_class is not match.pii_class
                and end <= match.end
                and (start, end) != (match.start, match.end)
                for start, end, pii_class in inner[low : bisect_left(inner_starts, match.end)]
            )
        return crossing[key]

    def allowed(start: int, end: int, pii_class: PiiClass) -> bool:
        return not any(
            m.pii_class is pii_class and (m.start, m.end) != (start, end) and not across(m)
            for m in near(start, end)
        )

    return split(allowed)


def _matches(norm: str, folded: str) -> list[PiiSpan]:
    """Every validated regex reading, of every class."""
    found: list[PiiSpan] = []

    def add(start: int, end: int, pii_class: PiiClass, value: str) -> None:
        found.append(PiiSpan(start, end, pii_class, value))

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
        pii_class = _ten_digit_class(folded, match.start(), digits)
        if pii_class is not None:
            add(match.start(), match.end(), pii_class, digits)
    return found


def _candidates(
    norm: str, wanted: frozenset[PiiClass], found: list[PiiSpan] | None = None
) -> tuple[list[PiiSpan], list[PiiSpan]]:
    """Every validated reading: the matches of each class, then the pieces of
    a digit run whose whole groups split into two or more identifiers."""
    # One-to-one letter folding: same length as ``norm``, so offsets carry over.
    folded = unify_arabic_letters(norm)
    if found is None:
        found = _matches(norm, folded)
    candidates = [span for span in found if span.pii_class in wanted]
    matches = sorted(candidates, key=lambda s: s.start)
    match_starts = [span.start for span in matches]
    longest = max((span.end - span.start for span in matches), default=0)
    pieces: list[PiiSpan] = []
    for run in _DIGIT_RUN_RE.finditer(norm):
        before, after = norm[run.start() - 1 : run.start()], norm[run.end() : run.end() + 1]
        if (before.isascii() and before.isalnum()) or (after.isascii() and after.isalnum()):
            continue  # glued to letters, like every match above
        at = [i for i in range(run.start(), run.end()) if "0" <= norm[i] <= "9"]
        pieces.extend(
            PiiSpan(start, end, pii_class, value)
            for start, end, pii_class, value in _split_run(
                norm, folded, at, wanted, matches, match_starts, longest
            )
        )
    return candidates, pieces


def _written(
    norm: str, folded: str, wanted: frozenset[PiiClass], found: list[PiiSpan]
) -> list[PiiSpan]:
    """Identifiers in their usual written form, which a regex reading of a
    neighbour can use up (HOM-303): a Luhn card as one group of 13 to 19
    digits or as four groups of four, a ``00`` number at any group of a run,
    and a mobile (or an ID after an ID keyword) with digits glued to it."""
    readings: list[PiiSpan] = []
    ordered = sorted(found, key=lambda s: s.start)
    starts = [span.start for span in ordered]
    longest = max((span.end - span.start for span in ordered), default=0)
    for run in _DIGIT_RUN_RE.finditer(norm):
        if run.end() - run.start() < 11:
            continue  # too short for any of these readings
        before, after = norm[run.start() - 1 : run.start()], norm[run.end() : run.end() + 1]
        if (before.isascii() and before.isalnum()) or (after.isascii() and after.isalnum()):
            continue  # glued to letters, like every match
        groups = [(g.start(), g.end()) for g in _GROUP_RE.finditer(norm, run.start(), run.end())]
        # Every reading of any class that shares a character with the run.
        held = [
            (m.start, m.end)
            for m in ordered[
                bisect_left(starts, run.start() - longest) : bisect_left(starts, run.end())
            ]
            if m.end > run.start()
        ]
        phones: list[PiiSpan] = []
        for start, _ in groups[1:]:
            match = _INTL_PHONE_RE.match(norm, start) if norm.startswith("00", start) else None
            canonical = _canonical_phone(match.group()) if match else None
            if match and canonical is not None:
                phones.append(PiiSpan(match.start(), match.end(), PiiClass.PHONE, canonical))
        held += [(span.start, span.end) for span in phones]
        cards = [
            PiiSpan(start, end, PiiClass.BANK_CARD, norm[start:end])
            for start, end in groups
            if 13 <= end - start <= 19 and valid_luhn(norm[start:end])
        ]
        fours = {
            k
            for k in range(len(groups) - 3)
            if all(end - start == 4 for start, end in groups[k : k + 4])
            and valid_luhn(_only_digits(norm[groups[k][0] : groups[k + 3][1]]))
        }
        for k in sorted(fours):
            first, last = groups[k][0], groups[k + 3][1]
            # Each group next to the four is at most two digits, another such
            # card, or part of a reading that does not hold the four.
            if all(
                end - start <= 2
                or other in fours
                or any(s <= start and end <= e and not (s <= first and last <= e) for s, e in held)
                for (start, end), other in [
                    (groups[x], k - 4 if x < k else k + 4)
                    for x in (k - 1, k + 4)
                    if 0 <= x < len(groups)
                ]
            ):
                cards.append(
                    PiiSpan(first, last, PiiClass.BANK_CARD, _only_digits(norm[first:last]))
                )
        held += [(span.start, span.end) for span in cards]
        readings += [span for span in phones + cards if span.pii_class in wanted]
        for start, end in groups:
            # A mobile or a named ID with glued digits, in a group that no
            # longer reading of any class holds.
            size = end - start
            mobile = PiiClass.PHONE in wanted and 11 < size <= 11 + _GLUED_MOBILE
            named = PiiClass.IR_NATIONAL_ID in wanted and 10 < size <= 10 + _GLUED_ID
            if not (mobile or named) or any(
                s <= start and end <= e and (s, e) != (start, end) for s, e in held
            ):
                continue
            digits = norm[start:end]
            if mobile:
                readings += [
                    PiiSpan(start + i, start + i + 11, PiiClass.PHONE, digits[i : i + 11])
                    for i in range(size - 10)
                    if _MOBILE_DIGITS_RE.fullmatch(digits, i, i + 11)
                ]
            if named:
                window = strip_diacritics(folded[max(0, start - _POSTAL_WINDOW) : start])
                if _ID_KEYWORD_RE.search(window):
                    readings += [
                        PiiSpan(
                            start + i, start + i + 10, PiiClass.IR_NATIONAL_ID, digits[i : i + 10]
                        )
                        for i in range(size - 9)
                        if valid_national_id(digits[i : i + 10])
                    ]
    return readings


def _select(norm: str, matches: list[PiiSpan], pieces: list[PiiSpan]) -> list[PiiSpan]:
    """Non-overlapping spans that leave no letter or digit of any candidate raw."""

    def rank(span: PiiSpan) -> tuple[int, int, int]:
        # Longest candidate first, then the better validated class.
        return (-(span.end - span.start), -_PRIORITY[span.pii_class], span.start)

    greedy: list[PiiSpan] = []
    for span in sorted(matches, key=rank):
        if all(span.end <= kept.start or span.start >= kept.end for kept in greedy):
            greedy.append(span)
    greedy.sort(key=lambda s: s.start)

    # Letters and digits carry the identifier; separators and brackets do not.
    marks = [char.isascii() and char.isalnum() for char in norm]
    mark_at = [i for i, mark in enumerate(marks) if mark]
    marks_before = [0]  # marks_before[i]: letters and digits in norm[:i]
    for mark in marks:
        marks_before.append(marks_before[-1] + mark)

    def bare(start: int, end: int) -> bool:
        return start >= end or marks_before[end] == marks_before[start]

    def covers(spans: list[PiiSpan], lo: int, hi: int) -> bool:
        cursor = lo
        for span in spans:
            if not bare(cursor, span.start):
                return False
            cursor = span.end
        return bare(cursor, hi)

    def cover(group: list[PiiSpan], lo: int, hi: int) -> list[PiiSpan] | None:
        # Shortest chain of non-overlapping candidates with no letter or digit
        # before, between or after them, then the best validated classes.  One
        # pass: a candidate links only to candidates that end after the last
        # letter or digit before it, so no subset of the group is enumerated.
        by_end = sorted(range(len(group)), key=lambda k: group[k].end)
        ends = [group[k].end for k in by_end]
        score: list[tuple[int, int] | None] = [None] * len(group)
        back = [-1] * len(group)
        finish = -1
        for k, span in enumerate(group):
            if bare(lo, span.start):
                score[k] = (1, -_PRIORITY[span.pii_class])
            count = marks_before[span.start]
            floor = mark_at[count - 1] + 1 if count else lo
            for e in range(bisect_left(ends, floor), bisect_right(ends, span.start)):
                link = score[by_end[e]]
                if link is not None:
                    option = (link[0] + 1, link[1] - _PRIORITY[span.pii_class])
                    if score[k] is None or option < score[k]:
                        score[k], back[k] = option, by_end[e]
            current = score[k]
            if current is not None and bare(span.end, hi):
                if finish < 0 or current < score[finish]:
                    finish = k
        chain: list[PiiSpan] = []
        while finish >= 0:
            chain.append(group[finish])
            finish = back[finish]
        return chain[::-1] or None

    def widen(kept: list[PiiSpan], lo: int, hi: int) -> list[PiiSpan]:
        # One class only: keep the longest-first spans and their values, each
        # grown over the letters and digits up to the next one.
        starts = [lo] + [span.start for span in kept[1:]]
        ends = [
            max(span.end, mark_at[marks_before[after.start] - 1] + 1)
            for span, after in zip(kept, kept[1:], strict=False)
        ]
        return [
            PiiSpan(start, end, span.pii_class, span.value)
            for start, end, span in zip(starts, ends + [hi], kept, strict=True)
        ]

    def whole(group: list[PiiSpan], lo: int, hi: int) -> list[PiiSpan]:
        # No chain covers every letter and digit: over-redact the whole
        # overlap, with the class and value of its best-ranked candidate.
        best = min(group, key=rank)
        return [PiiSpan(lo, hi, best.pii_class, best.value)]

    unique: dict[tuple[int, int, PiiClass], PiiSpan] = {}
    for span in matches + pieces:
        unique.setdefault((span.start, span.end, span.pii_class), span)
    pool = sorted(unique.values(), key=lambda s: (s.start, s.end, -_PRIORITY[s.pii_class]))
    chosen: list[PiiSpan] = []
    index = taken = 0
    while index < len(pool):
        # A group of candidates linked by overlaps covers one stretch [lo, hi).
        lo, hi = pool[index].start, pool[index].end
        group = [pool[index]]
        index += 1
        while index < len(pool) and pool[index].start < hi:
            hi = max(hi, pool[index].end)
            group.append(pool[index])
            index += 1
        kept: list[PiiSpan] = []
        while taken < len(greedy) and greedy[taken].start < hi:
            kept.append(greedy[taken])
            taken += 1
        if covers(kept, lo, hi):
            chosen.extend(kept)  # the longest-first choice leaves nothing raw
        elif kept and len({span.pii_class for span in group}) == 1:
            chosen.extend(widen(kept, lo, hi))
        else:
            chosen.extend(cover(group, lo, hi) or whole(group, lo, hi))
    return chosen


def _keep(before: list[PiiSpan], spans: list[PiiSpan]) -> list[PiiSpan]:
    """``spans`` grown so that every character inside a span of ``before``
    stays inside a span: where ``spans`` cut one of them, the span before
    the cut grows over the separator."""
    starts = [span.start for span in spans]
    ends = [span.end for span in spans]
    bounds = [[span.start, span.end] for span in spans]
    for old in before:
        # Every letter and digit of ``old`` is in ``spans``, so first < last.
        first, last = bisect_right(ends, old.start), bisect_left(starts, old.end)
        bounds[first][0] = min(bounds[first][0], old.start)
        bounds[last - 1][1] = max(bounds[last - 1][1], old.end)
        for k in range(first, last - 1):
            bounds[k][1] = bounds[k + 1][0]
    return [
        PiiSpan(start, end, span.pii_class, span.value)
        for (start, end), span in zip(bounds, spans, strict=True)
    ]


def detect(text: str, classes: Iterable[PiiClass] = SUPPORTED_CLASSES) -> list[PiiSpan]:
    """Non-overlapping spans of the requested classes, in text order."""
    norm = normalize_digits_extended(text)
    wanted = frozenset(classes)
    folded = unify_arabic_letters(norm)
    found = _matches(norm, folded)
    matches, pieces = _candidates(norm, wanted, found)
    chosen = _select(norm, matches, pieces)
    # A written-form reading is used only where it holds a digit left raw.
    extra = _written(norm, folded, wanted, found)
    if extra:
        covered = bytearray(len(norm))
        for span in chosen:
            covered[span.start : span.end] = b"\x01" * (span.end - span.start)
        extra = [
            span
            for span in extra
            if any(not covered[i] and "0" <= norm[i] <= "9" for i in range(span.start, span.end))
        ]
    if not extra:
        return chosen
    return _keep(chosen, _select(norm, matches, pieces + extra))


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
