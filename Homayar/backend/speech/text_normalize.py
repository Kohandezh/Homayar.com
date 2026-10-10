"""Deterministic text normalisation for ASR output and NLU (Persian first).

This module is the single owner of Persian/Arabic text normalisation in the
backend: the digit tables, the Arabic letter-variant tables, the diacritic
classes and the invisible-character set are defined here and nowhere else.  It
is a leaf module (no ``backend.*`` import), so every other package can depend
on it without a cycle.

Persian (``fa``): Arabic letter variants to Persian (``ي``→``ی``, ``ك``→``ک``,
``ة``→``ه``, ``أ``/``إ``/``ٱ``→``ا``, ``ؤ``→``و``), diacritics and tatweel
removed, Persian/Arabic-Indic digits to ASCII (:func:`normalize_digits`), ZWNJ
repaired for the ``می``/``نمی`` verb prefixes and the ``ها``/``های`` plural
suffix, Persian punctuation mapped to one canonical form, whitespace collapsed.

Colloquial → formal rewriting is an explicit, *configurable* token table
(:data:`DEFAULT_COLLOQUIAL_FA`).  It is a hook with a handful of common spoken
forms, not a complete colloquial grammar; callers pass their own table.

A small registry maps language tags (``fa``, ``en``, ``az``) to normalisers.
Azerbaijani support is basic: Unicode NFC, Latin ``ə`` variants, Arabic-script
letter unification shared with Persian.  No accuracy is claimed.

Exported tables (all immutable: ``str``, ``frozenset`` or ``MappingProxyType``)
-------------------------------------------------------------------------------
- ``PERSIAN_DIGITS`` (U+06F0-06F9) and ``ARABIC_INDIC_DIGITS`` (U+0660-0669):
  the two native digit runs, in value order.
- ``DIGITS``: those 20 code points to ASCII ``0``-``9``.  Nothing else (no
  fullwidth, no Devanagari).  :func:`normalize_digits` applies it one
  character for one character, so offsets are kept.
- ``FULLWIDTH_DIGITS`` (U+FF10-FF19) and ``DIGITS_EXTENDED``: ``DIGITS`` plus
  the fullwidth run, the canonical digit set (HOM-165: ASCII, Persian,
  Arabic-Indic, fullwidth).  :func:`normalize_digits_extended` applies it one
  to one.  Devanagari (U+0966-096F) and every other digit script are
  deliberately not folded.
- ``LETTER_VARIANTS_BASIC``: ``ي ى``→``ی``, ``ك``→``ک``, ``ة``→``ه``,
  ``أ إ ٱ``→``ا`` (7 entries).
- ``LETTER_VARIANTS_EXTENDED``: ``LETTER_VARIANTS_BASIC`` plus ``ۀ``→``ه`` and
  ``ؤ``→``و`` (9 entries).
- ``LETTER_VARIANTS_SPEECH``: ``LETTER_VARIANTS_EXTENDED`` plus ``ٲ``→``ا``
  (10 entries); :func:`unify_arabic_letters` applies it, one to one.
- ``ZWNJ``, ``ZWJ``, ``LRM``, ``RLM``: the joiner and directional format
  characters; ``TATWEEL`` (U+0640).
- ``HARAKAT``: the set U+064B-065F plus U+0670 (harakat, tanwin, shadda,
  sukun, superscript alef); ``HARAKAT_CLASS`` is the same set as a regex
  character-class body.
- ``QURANIC_MARKS_CLASS``: U+06D6-06ED as a regex character-class body.
- ``ARABIC_NUMBER_PUNCTUATION``: ``٫``→``.``, ``٬``→``,``, ``،``→``,``.

Consumer profiles (each keeps its own step order)
-------------------------------------------------
- Speech ``fa``/``az`` (:func:`normalize_fa`, :func:`normalize_az`): NFC →
  ``LETTER_VARIANTS_SPEECH`` → strip ``HARAKAT`` and ``TATWEEL`` → ``DIGITS`` →
  whitespace → [colloquial] → ZWNJ repair → [punctuation].  ZWNJ is kept as a
  joiner; LRM/RLM are kept.  ``en``: NFKC → ``DIGITS`` → quotes → whitespace.
- Speech matching (:func:`normalize_for_matching`): the language normaliser,
  then ZWNJ removed, casefold, punctuation to space, whitespace.
- Locations stream (``backend.locations.textnorm``): ``DIGITS``, then per
  character: drop ``HARAKAT``; ``LETTER_VARIANTS_BASIC``; ZWNJ and NBSP to
  space; ZWJ, LRM, RLM and ``TATWEEL`` dropped; ``ARABIC_NUMBER_PUNCTUATION``;
  ASCII letters lowercased; whitespace collapsed.  No Unicode normalisation,
  and an offset map back to the original text.  Its output is persisted
  (service location address fields), so it must not change silently.
- Voicemail keywords (``backend.voicemail.classify``): NFKC →
  ``LETTER_VARIANTS_EXTENDED``, ZWNJ to space, ZWJ/LRM/RLM/``TATWEEL``
  dropped, ``DIGITS`` → strip ``HARAKAT_CLASS`` and ``QURANIC_MARKS_CLASS`` →
  casefold → punctuation to space → whitespace collapsed.
- PII digits (``backend.privacy.text_pii``): ``DIGITS_EXTENDED``
  (:func:`normalize_digits_extended`; ``normalize_digits`` is still
  re-exported there) and the postal keyword window letter-folded with
  :func:`unify_arabic_letters` and stripped by :func:`strip_diacritics`
  (HOM-171); offsets refer to the original.  Only ASCII counts as a digit
  after folding, so numbers in every non-canonical script (Devanagari,
  Bengali, mathematical digits) are not detected.
- Mobile/OTP digits (``backend.security.normalization``,
  ``backend.routers.v1.auth``, ``backend.voicemail.ingest``): mobile numbers
  are NFKC then ``DIGITS``; OTP codes are ``DIGITS`` only; non-mobile caller
  numbers are ``DIGITS_EXTENDED`` (HOM-165).  Caller numbers stored before
  HOM-165 with fullwidth digits are left as they are (no data fix); repeat
  caller matching works for rows written since.

Preserved divergences between profiles (intentional, not yet unified; changing
any of them is a behaviour change that needs a detector/rules version bump and,
for locations, a data migration):
- ``ۀ`` and ``ؤ`` fold in speech and voicemail, not in locations; ``ٲ`` folds
  only in speech.
- Unicode normalisation: speech NFC (``ئ`` stays ``ئ``, ``آ`` stays), voicemail
  NFKC (presentation forms and fullwidth digits fold), locations none (``آ``
  loses its maddah, decomposed ``ئ`` becomes ``ی``).  PII has none (it folds
  fullwidth digits through ``DIGITS_EXTENDED`` only).
- ZWNJ: kept by speech, a space in locations and voicemail; LRM/RLM kept by
  speech, dropped by locations and voicemail.
- Quranic marks (U+06D6-06ED) are stripped only by voicemail; tatweel is
  stripped by speech, locations and voicemail, and by PII only inside the
  postal keyword window.
- Arabic number punctuation is mapped only by locations; voicemail turns it
  into spaces; speech keeps it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

# --- shared tables (single definition site) ------------------------------------

PERSIAN_DIGITS = "".join(chr(0x06F0 + i) for i in range(10))
ARABIC_INDIC_DIGITS = "".join(chr(0x0660 + i) for i in range(10))
DIGITS: Mapping[str, str] = MappingProxyType(
    {native: str(value % 10) for value, native in enumerate(PERSIAN_DIGITS + ARABIC_INDIC_DIGITS)}
)

# Canonical digit set (HOM-165): ASCII, Persian, Arabic-Indic and fullwidth.
# Devanagari (U+0966-096F) and every other script are deliberately NOT folded:
# callers that need an ASCII number must treat them as non-digits.
FULLWIDTH_DIGITS = "".join(chr(0xFF10 + i) for i in range(10))
DIGITS_EXTENDED: Mapping[str, str] = MappingProxyType(
    {**DIGITS, **{native: str(value) for value, native in enumerate(FULLWIDTH_DIGITS)}}
)

ZWNJ = "\u200c"
ZWJ = "\u200d"
LRM = "\u200e"
RLM = "\u200f"
TATWEEL = "\u0640"

LETTER_VARIANTS_BASIC: Mapping[str, str] = MappingProxyType(
    {
        "\u064a": "\u06cc",  # Arabic yeh -> Persian yeh
        "\u0649": "\u06cc",  # alef maksura -> Persian yeh
        "\u0643": "\u06a9",  # Arabic kaf -> Persian keheh
        "\u0629": "\u0647",  # teh marbuta -> heh
        "\u0623": "\u0627",  # alef with hamza above -> alef
        "\u0625": "\u0627",  # alef with hamza below -> alef
        "\u0671": "\u0627",  # alef wasla -> alef
    }
)
LETTER_VARIANTS_EXTENDED: Mapping[str, str] = MappingProxyType(
    {
        **LETTER_VARIANTS_BASIC,
        "\u06c0": "\u0647",  # heh with yeh above -> heh
        "\u0624": "\u0648",  # waw with hamza -> waw
    }
)
LETTER_VARIANTS_SPEECH: Mapping[str, str] = MappingProxyType(
    {
        **LETTER_VARIANTS_EXTENDED,
        "\u0672": "\u0627",  # alef with wavy hamza above -> alef
    }
)

# Harakat, tanwin, shadda, sukun and superscript alef.
HARAKAT: frozenset[str] = frozenset(chr(c) for c in range(0x064B, 0x0660)) | {"\u0670"}
HARAKAT_CLASS = "\u064b-\u065f\u0670"
QURANIC_MARKS_CLASS = "\u06d6-\u06ed"

ARABIC_NUMBER_PUNCTUATION: Mapping[str, str] = MappingProxyType(
    {
        "\u066b": ".",  # Arabic decimal separator
        "\u066c": ",",  # Arabic thousands separator
        "\u060c": ",",  # Arabic comma
    }
)

_DIGIT_TABLE = str.maketrans(dict(DIGITS))
_DIGIT_TABLE_EXTENDED = str.maketrans(dict(DIGITS_EXTENDED))
_ARABIC_TO_PERSIAN = str.maketrans(dict(LETTER_VARIANTS_SPEECH))


def translation_table(*mappings: Mapping[str, str]) -> dict[int, str]:
    """A fresh :meth:`str.translate` table merging ``mappings`` (later wins)."""
    merged: dict[str, str] = {}
    for mapping in mappings:
        merged.update(mapping)
    return str.maketrans(merged)


def normalize_digits(text: str) -> str:
    """Persian/Arabic-Indic digits to ASCII; one character each, offsets kept."""
    return text.translate(_DIGIT_TABLE)


def normalize_digits_extended(text: str) -> str:
    """The canonical digit set (Persian, Arabic-Indic, fullwidth) to ASCII.

    One character each, so offsets are kept.  Devanagari and other scripts are
    left as they are; :func:`normalize_digits` keeps its narrower behaviour
    for the consumers that pin it.
    """
    return text.translate(_DIGIT_TABLE_EXTENDED)


# --- speech profile --------------------------------------------------------------

# Harakat, tanwin, shadda, sukun, superscript alef, and tatweel.
_DIACRITICS_RE = re.compile(f"[{HARAKAT_CLASS}{TATWEEL}]")
_PUNCT = str.maketrans({"?": "؟", ";": "؛", ",": "،", "«": '"', "»": '"'})
_SPACE_RE = re.compile(r"[ \t  -​  　]+")
_ZW_RUN_RE = re.compile(f"[{ZWNJ}‍]+")
_SPACE_AROUND_ZWNJ_RE = re.compile(f" *{ZWNJ} *")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.،؛؟!:)])")
# می / نمی verb prefix followed by a space or nothing before a verb stem.
_MI_PREFIX_RE = re.compile(r"(?<![\w؀-ۿ])(ن?می)[ ]+(?=[؀-ۿ]{2,})")
# Plural suffix separated by a space: "کتاب ها" -> "کتاب‌ها".
_HA_SUFFIX_RE = re.compile(r"(?<=[؀-ۿ]{2}) +(ها(?:ی|یی|یم|یت|یش|یمان|یتان|یشان)?)(?![؀-ۿ])")

# Common spoken Tehrani forms → formal written forms.  Deliberately small and
# overridable; callers own completeness.
DEFAULT_COLLOQUIAL_FA: Mapping[str, str] = {
    "میخوام": "می‌خواهم",
    "می‌خوام": "می‌خواهم",
    "میخوای": "می‌خواهی",
    "می‌خوای": "می‌خواهی",
    "میخواد": "می‌خواهد",
    "می‌خواد": "می‌خواهد",
    "نمیخوام": "نمی‌خواهم",
    "نمی‌خوام": "نمی‌خواهم",
    "میتونم": "می‌توانم",
    "می‌تونم": "می‌توانم",
    "میتونید": "می‌توانید",
    "می‌تونید": "می‌توانید",
    "نمیتونم": "نمی‌توانم",
    "نمی‌تونم": "نمی‌توانم",
    "میشه": "می‌شود",
    "می‌شه": "می‌شود",
    "نمیشه": "نمی‌شود",
    "نمی‌شه": "نمی‌شود",
    "میگم": "می‌گویم",
    "می‌گم": "می‌گویم",
    "میدم": "می‌دهم",
    "می‌دم": "می‌دهم",
    "بدین": "بدهید",
    "بگین": "بگویید",
    "دارین": "دارید",
    "هستین": "هستید",
    "کنین": "کنید",
    "اینجوری": "این‌جوری",
    "چجوری": "چطور",
    "چیه": "چیست",
    "کجاست": "کجاست",
    "دیگه": "دیگر",
    "الان": "الان",
    "اگه": "اگر",
    "واسه": "برای",
    "ممنونم": "ممنونم",
    "باشه": "باشد",
    "یه": "یک",
}


def unify_arabic_letters(text: str) -> str:
    return text.translate(_ARABIC_TO_PERSIAN)


def strip_diacritics(text: str) -> str:
    return _DIACRITICS_RE.sub("", text)


# _SPACE_RE plus CR and LF.  Every maximal run of these characters becomes one space,
# exactly as the three passes of the original normalize_whitespace did.
_WHITESPACE_CLASS = r"[ \t\r\n\u00a0\u2000-\u200b\u202f\u205f\u3000]"
_WHITESPACE_RE = re.compile(f"{_WHITESPACE_CLASS}+")
# normalize_for_matching separators: punctuation (neither word nor whitespace) or
# _WHITESPACE_CLASS; one run of them becomes one space.
_MATCH_SEPARATOR_RE = re.compile(rf"(?:[^\w\s]|{_WHITESPACE_CLASS})+")
# A ZWNJ run at a word boundary (start, end or next to a space).
_ZWNJ_AT_BOUNDARY_RE = re.compile(f"(^|[ ]){ZWNJ}+|{ZWNJ}+($|[ ])")
# The keys of _PUNCT, so its translate can be skipped when none is present.
_PUNCT_KEYS_RE = re.compile(f"[{re.escape(''.join(map(chr, _PUNCT)))}]")
# LETTER_VARIANTS_SPEECH, HARAKAT and TATWEEL dropped, and DIGITS in one translate: the
# three are per-character maps with disjoint keys whose values are no other map's keys,
# so their union equals their composition.
_SPEECH_FOLD: dict[int, str | None] = {
    **_ARABIC_TO_PERSIAN,
    **dict.fromkeys(map(ord, HARAKAT | {TATWEEL})),
    **_DIGIT_TABLE,
}


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.replace("​", " ")).strip()


def _repair_zwnj(text: str) -> str:
    """:func:`normalize_zwnj` for a ``str``; each pass runs only when the text holds a
    substring its pattern cannot match without ("\u0645\u06cc " for the verb prefix,
    " \u0647\u0627" for the plural suffix, ZWNJ or ZWJ for the rest)."""
    if ZWNJ in text or ZWJ in text:
        text = _ZW_RUN_RE.sub(ZWNJ, text)
        text = _SPACE_AROUND_ZWNJ_RE.sub(ZWNJ, text)
    if "\u0645\u06cc " in text:
        text = _MI_PREFIX_RE.sub(lambda m: m.group(1) + ZWNJ, text)
    if " \u0647\u0627" in text:
        text = _HA_SUFFIX_RE.sub(lambda m: ZWNJ + m.group(1), text)
    # A ZWNJ at a word boundary carries no meaning.
    if ZWNJ in text:
        text = _ZWNJ_AT_BOUNDARY_RE.sub(r"\1\2", text)
    return text


def normalize_zwnj(text: str) -> str:
    return _repair_zwnj(_ZW_RUN_RE.sub(ZWNJ, text))


def normalize_punctuation_fa(text: str) -> str:
    text = text.translate(_PUNCT)
    if " " not in text:
        return text
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def apply_token_map(text: str, table: Mapping[str, str]) -> str:
    """Whole-token replacement (tokens are split on single spaces)."""
    if not table:
        return text
    return " ".join([table.get(tok, tok) for tok in text.split(" ")])


def normalize_fa(
    text: str,
    *,
    colloquial: Mapping[str, str] | None = None,
    punctuation: bool = True,
) -> str:
    """Full Persian normalisation.  ``colloquial=None`` disables rewriting;
    pass :data:`DEFAULT_COLLOQUIAL_FA` (or your own table) to enable it."""
    text = unicodedata.normalize("NFC", text).translate(_SPEECH_FOLD)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if colloquial:
        # Match both the ZWNJ and non-ZWNJ spellings before ZWNJ repair.
        text = apply_token_map(text, colloquial)
    text = _repair_zwnj(text)
    if punctuation:
        if _PUNCT_KEYS_RE.search(text):
            text = text.translate(_PUNCT)
        if " " in text:
            text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return text


# DIGITS and the two curly single quotes in one translate (disjoint per-character maps).
_EN_FOLD: dict[int, str] = {**_DIGIT_TABLE, **str.maketrans({"’": "'", "‘": "'"})}


def normalize_en(text: str, **_: object) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_EN_FOLD)
    return _WHITESPACE_RE.sub(" ", text).strip()


_AZ_LATIN = str.maketrans({"ä": "ə", "Ä": "Ə", "ǝ": "ə", "Ǝ": "Ə"})
# _AZ_LATIN, then the speech folds of _SPEECH_FOLD, in one translate.
_AZ_FOLD: dict[int, str | None] = {**_SPEECH_FOLD, **_AZ_LATIN}


def normalize_az(text: str, **_: object) -> str:
    """Azerbaijani basics: NFC, schwa variants unified (Latin script),
    Arabic-script letters unified with Persian conventions (Iranian
    Azerbaijani), digits, whitespace.  No orthography conversion."""
    text = unicodedata.normalize("NFC", text).translate(_AZ_FOLD)
    return _repair_zwnj(_WHITESPACE_RE.sub(" ", text).strip())


def normalize_for_matching(text: str, language: str = "fa") -> str:
    """Aggressive form for WER/CER and keyword matching: normalised, casefolded,
    punctuation removed, ZWNJ treated as a word-internal join (removed)."""
    text = get_normalizer(language)(text)
    text = text.replace(ZWNJ, "")
    return _MATCH_SEPARATOR_RE.sub(" ", text.casefold()).strip()


@dataclass(frozen=True)
class NormalizerEntry:
    language: str
    func: Callable[..., str]
    description: str


_REGISTRY: dict[str, NormalizerEntry] = {
    "fa": NormalizerEntry(
        "fa", normalize_fa, "Persian: letters, diacritics, digits, ZWNJ, punctuation"
    ),
    "en": NormalizerEntry("en", normalize_en, "English: NFKC, quotes, digits, whitespace"),
    "az": NormalizerEntry(
        "az", normalize_az, "Azerbaijani basics: Latin schwa, Arabic-script letters"
    ),
}


def register_normalizer(language: str, func: Callable[..., str], description: str = "") -> None:
    _REGISTRY[language.lower()] = NormalizerEntry(language.lower(), func, description)


def get_normalizer(language: str) -> Callable[..., str]:
    """``fa-IR``/``az-Latn`` resolve to their primary subtag.  Unknown -> KeyError."""
    key = language.lower().replace("_", "-")
    if key in _REGISTRY:
        return _REGISTRY[key].func
    primary = key.split("-", 1)[0]
    if primary in _REGISTRY:
        return _REGISTRY[primary].func
    raise KeyError(f"No normalizer registered for language {language!r}")


def registered_languages() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def normalize(text: str, language: str = "fa", **options: object) -> str:
    return get_normalizer(language)(text, **options)
