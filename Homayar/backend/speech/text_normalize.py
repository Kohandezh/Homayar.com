"""Deterministic text normalisation for ASR output and NLU (Persian first).

Persian (``fa``): Arabic letter variants to Persian (``ي``→``ی``, ``ك``→``ک``,
``ة``→``ه``, ``أ``/``إ``/``ٱ``→``ا``, ``ؤ``→``و``), diacritics and tatweel
removed, Persian/Arabic-Indic digits to ASCII (reusing
:func:`backend.privacy.text_pii.normalize_digits`), ZWNJ repaired for the
``می``/``نمی`` verb prefixes and the ``ها``/``های`` plural suffix, Persian
punctuation mapped to one canonical form, whitespace collapsed.

Colloquial → formal rewriting is an explicit, *configurable* token table
(:data:`DEFAULT_COLLOQUIAL_FA`).  It is a hook with a handful of common spoken
forms, not a complete colloquial grammar; callers pass their own table.

A small registry maps language tags (``fa``, ``en``, ``az``) to normalisers.
Azerbaijani support is basic: Unicode NFC, Latin ``ə`` variants, Arabic-script
letter unification shared with Persian.  No accuracy is claimed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from backend.privacy.text_pii import normalize_digits

ZWNJ = "‌"

_ARABIC_TO_PERSIAN = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "أ": "ا",
        "إ": "ا",
        "ٱ": "ا",
        "ؤ": "و",
        "ٲ": "ا",
    }
)

# Harakat, tanwin, shadda, sukun, superscript alef, and tatweel.
_DIACRITICS_RE = re.compile("[ً-ٰٟـ]")
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


def normalize_whitespace(text: str) -> str:
    text = _SPACE_RE.sub(" ", text.replace("​", " "))
    text = re.sub(r"[\r\n]+", " ", text)
    return re.sub(" {2,}", " ", text).strip()


def normalize_zwnj(text: str) -> str:
    text = _ZW_RUN_RE.sub(ZWNJ, text)
    text = _SPACE_AROUND_ZWNJ_RE.sub(ZWNJ, text)
    text = _MI_PREFIX_RE.sub(lambda m: m.group(1) + ZWNJ, text)
    text = _HA_SUFFIX_RE.sub(lambda m: ZWNJ + m.group(1), text)
    # A ZWNJ at a word boundary carries no meaning.
    text = re.sub(f"(^|[ ]){ZWNJ}+|{ZWNJ}+($|[ ])", r"\1\2", text)
    return text


def normalize_punctuation_fa(text: str) -> str:
    text = text.translate(_PUNCT)
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def apply_token_map(text: str, table: Mapping[str, str]) -> str:
    """Whole-token replacement (tokens are split on single spaces)."""
    if not table:
        return text
    return " ".join(table.get(tok, tok) for tok in text.split(" "))


def normalize_fa(
    text: str,
    *,
    colloquial: Mapping[str, str] | None = None,
    punctuation: bool = True,
) -> str:
    """Full Persian normalisation.  ``colloquial=None`` disables rewriting;
    pass :data:`DEFAULT_COLLOQUIAL_FA` (or your own table) to enable it."""
    text = unicodedata.normalize("NFC", text)
    text = unify_arabic_letters(text)
    text = strip_diacritics(text)
    text = normalize_digits(text)
    text = normalize_whitespace(text)
    if colloquial:
        # Match both the ZWNJ and non-ZWNJ spellings before ZWNJ repair.
        text = apply_token_map(text, colloquial)
    text = normalize_zwnj(text)
    if punctuation:
        text = normalize_punctuation_fa(text)
    return text


def normalize_en(text: str, **_: object) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = normalize_digits(text)
    text = text.replace("’", "'").replace("‘", "'")
    return normalize_whitespace(text)


_AZ_LATIN = str.maketrans({"ä": "ə", "Ä": "Ə", "ǝ": "ə", "Ǝ": "Ə"})


def normalize_az(text: str, **_: object) -> str:
    """Azerbaijani basics: NFC, schwa variants unified (Latin script),
    Arabic-script letters unified with Persian conventions (Iranian
    Azerbaijani), digits, whitespace.  No orthography conversion."""
    text = unicodedata.normalize("NFC", text).translate(_AZ_LATIN)
    text = unify_arabic_letters(text)
    text = strip_diacritics(text)
    text = normalize_digits(text)
    return normalize_zwnj(normalize_whitespace(text))


def normalize_for_matching(text: str, language: str = "fa") -> str:
    """Aggressive form for WER/CER and keyword matching: normalised, casefolded,
    punctuation removed, ZWNJ treated as a word-internal join (removed)."""
    text = get_normalizer(language)(text)
    text = text.replace(ZWNJ, "")
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return normalize_whitespace(text)


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
