"""Frozen BASE_SHA reference copies of every Persian/Arabic normaliser (WP22-T02, HOM-120).

Provenance: base commit ``7112407`` (BASE_SHA of team wp22t02-t1).  Every ``_NAME_a_b``
string below (after its opening newline) is a verbatim copy of lines ``a``-``b`` of the named
base file under ``Homayar/backend/`` (``git show 7112407:Homayar/backend/<file> | sed -n
'a,bp'``); its sha256 is recorded in ``_DIGESTS`` and checked at import.  Source files of the
migrated copies:

- ``backend/privacy/text_pii.py:53,60-62`` (``_DIGITS``, ``normalize_digits``)
- ``backend/speech/text_normalize.py:19-25,28-227`` (all of it except the text_pii import at :26)
- ``backend/locations/textnorm.py:10-12,16-83`` (all of it except the text_pii import at :14)
- ``backend/voicemail/classify.py:16-22,28-60,313-315,348-349`` (imports, the normalisation
  section, ``MIN_WORD_CHARS``/``_WORD_CHARS`` and ``is_empty_transcript``; not the models import)
- ``backend/security/normalization.py:1-4,8-9,20-33`` (``normalize_iranian_mobile``)
- ``backend/voicemail/ingest.py:91-106`` (``normalize_caller_number``)
- ``backend/routers/v1/auth.py:616-618`` (the inline OTP digit translation, wrapped in a call)

Each copy is executed in its own namespace (the copies share names such as ``_CHAR_MAP``,
``_PUNCT`` and ``normalize_text``).  The imports the base files made of other backend modules
are replaced by injecting the matching frozen copy: speech and locations get this module's
text_pii ``normalize_digits``, ingest gets this module's ``normalize_iranian_mobile``.  Nothing
here imports production code, so the references stay at BASE_SHA whatever the owner becomes.

Underscore module: not collected by pytest.  Used by ``test_text_normalization_golden.py``.
"""

from __future__ import annotations
import __future__

import hashlib
import linecache
import sys
import types
from types import SimpleNamespace

BASE_SHA = "7112407"

# backend/privacy/text_pii.py:53-53 @ 7112407
_TEXT_PII_53 = r"""
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
"""

# backend/privacy/text_pii.py:60-62 @ 7112407
_TEXT_PII_60_62 = r'''
def normalize_digits(text: str) -> str:
    """Persian/Arabic-Indic digits to ASCII; one character each, offsets kept."""
    return text.translate(_DIGITS)
'''

# backend/speech/text_normalize.py:19-25 @ 7112407
_SPEECH_19_25 = r"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass

"""

# backend/speech/text_normalize.py:28-227 @ 7112407
_SPEECH_28_227 = r'''
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
'''

# backend/locations/textnorm.py:10-12 @ 7112407
_LOCATIONS_10_12 = r"""
from __future__ import annotations

from dataclasses import dataclass
"""

# backend/locations/textnorm.py:16-83 @ 7112407
_LOCATIONS_16_83 = r'''
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
'''

# backend/voicemail/classify.py:16-22 @ 7112407
_VOICEMAIL_16_22 = r"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol
"""

# backend/voicemail/classify.py:28-60 @ 7112407
_VOICEMAIL_28_60 = r'''
# --- normalisation -------------------------------------------------------------

_CHAR_MAP = str.maketrans(
    {
        "ي": "ی",  # Arabic yeh -> Persian yeh
        "ى": "ی",  # alef maksura -> Persian yeh
        "ك": "ک",  # Arabic kaf -> Persian keheh
        "ة": "ه",  # teh marbuta -> heh
        "ۀ": "ه",  # heh with yeh above -> heh
        "أ": "ا",  # alef with hamza above -> alef
        "إ": "ا",  # alef with hamza below -> alef
        "ٱ": "ا",  # alef wasla -> alef
        "ؤ": "و",  # waw with hamza -> waw
        "‌": " ",  # ZWNJ -> space
        "‍": "",  # ZWJ
        "‏": "",  # RLM
        "‎": "",  # LRM
        "ـ": "",  # tatweel
        **{chr(0x06F0 + i): str(i) for i in range(10)},  # Persian digits
        **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic digits
    }
)
_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭ]")
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_text(value: str) -> str:
    """Casefolded, Arabic-variant-free, ZWNJ-as-space, punctuation-free text."""
    text = unicodedata.normalize("NFKC", value or "").translate(_CHAR_MAP)
    text = _DIACRITICS.sub("", text).casefold()
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()
'''

# backend/voicemail/classify.py:313-315 @ 7112407
_VOICEMAIL_313_315 = r"""
# Fewer word characters than this is "no speech" (e.g. "uh", ".", "[noise]").
MIN_WORD_CHARS = 3
_WORD_CHARS = re.compile(r"\w", re.UNICODE)
"""

# backend/voicemail/classify.py:348-349 @ 7112407
_VOICEMAIL_348_349 = r"""
def is_empty_transcript(text: str) -> bool:
    return len(_WORD_CHARS.findall(normalize_text(text))) < MIN_WORD_CHARS
"""

# backend/security/normalization.py:1-4 @ 7112407
_SECURITY_1_4 = r"""
from __future__ import annotations

import re
import unicodedata
"""

# backend/security/normalization.py:8-9 @ 7112407
_SECURITY_8_9 = r"""
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
"""

# backend/security/normalization.py:20-33 @ 7112407
_SECURITY_20_33 = r"""
def normalize_iranian_mobile(value: str) -> str:
    raw = unicodedata.normalize("NFKC", value).translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)
    digits = re.sub(r"[^0-9+]", "", raw)
    if digits.startswith("0098"):
        digits = "+" + digits[2:]
    elif digits.startswith("98") and not digits.startswith("+"):
        digits = "+" + digits
    elif digits.startswith("09"):
        digits = "+98" + digits[1:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "+98" + digits
    if not re.fullmatch(r"\+989\d{9}", digits):
        raise ValueError("Invalid Iranian mobile number")
    return digits
"""

# backend/voicemail/ingest.py:91-106 @ 7112407
_INGEST_91_106 = r'''
def normalize_caller_number(value: str | None) -> tuple[str | None, bool]:
    """``(+989xxxxxxxxx, True)`` for an Iranian mobile; else digits-only, ``False``."""
    if value is None or not value.strip():
        return None, False
    try:
        return normalize_iranian_mobile(value), True
    except ValueError:
        # Landlines, international and extensions: keep a comparable,
        # digits-only form (leading "+" kept) so repeat-caller detection works.
        raw = value.strip().translate(
            {**{0x06F0 + i: str(i) for i in range(10)}, **{0x0660 + i: str(i) for i in range(10)}}
        )
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None, False
        return (("+" if raw.startswith("+") else "") + digits)[:32], False
'''

# backend/routers/v1/auth.py:616-618 @ 7112407
_AUTH_616_618 = r"""
            code=payload.code.translate(
                str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
            ),
"""


_DIGESTS = {
    "_TEXT_PII_53": "4d6589c22a23a7320e17bb453edd2ffa46abd64c895c89fb2ebc43cf49d1511d",
    "_TEXT_PII_60_62": "098557fbb05d57ae9ebbede0fb4e58788f748acff6da6c613b360252d7a896b3",
    "_SPEECH_19_25": "e614e349be2bb4444cd341746dc3a9341203442210997879e5561eab86e0e8c3",
    "_SPEECH_28_227": "526230f42f4a7619d1bcc7ae965e9a7a3731e10ce3be6a99745ac619a599227f",
    "_LOCATIONS_10_12": "5c85b2ffc87021aa936e8846d04916cb7c28778bd85cb01db04f2f8740d14c0a",
    "_LOCATIONS_16_83": "c8dca834aaa3ce144b71abb337f70d3231dd3583b261b797b799e1b7b66ad5b9",
    "_VOICEMAIL_16_22": "9d4ee474b5469da922016ca18f0b77c8e5364d861a6c5628f6e14a4a05d08d6d",
    "_VOICEMAIL_28_60": "28613105c75707bcaa8e918af98dd10ab75d8ebc6d4d92a33bc76eb57262862c",
    "_VOICEMAIL_313_315": "d3d928605ca1ecc9f2d7051bab028c0543c27f139e2f14dac7387dd41e142f67",
    "_VOICEMAIL_348_349": "5381f18499f07350c478e76a03cb6280a14dd68254b291e3f0c5b84f40f8c069",
    "_SECURITY_1_4": "d191398843dbe1eefda714bb5fb2f2a12cc4f940fe783de0307862ad927b26cc",
    "_SECURITY_8_9": "960407def7c27aeff0c1309f52e5d5a2211d1d883073ec800efa36d916286017",
    "_SECURITY_20_33": "0f4b60a89bb40014e68484740b07e2315bf9f2e59a6f07f1d6270d0e6eca9467",
    "_INGEST_91_106": "a67ed43619a0ed3247951815bbbd5c4a19d2af558d5450bad2234653895ea3eb",
    "_AUTH_616_618": "16f971352e2f7ac81c994d10f72919f9ba5b00402705b8d3faf16b8bc04f2457",
}


def _checked(name: str) -> str:
    source = globals()[name].removeprefix("\n")  # each literal opens with one newline
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest != _DIGESTS[name]:
        raise RuntimeError(f"frozen reference {name} was edited: sha256 {digest}")
    return source


def _load(part: str, snippets: tuple[str, ...], **inject: object) -> types.ModuleType:
    """Execute verbatim base snippets, in order, in one fresh namespace."""
    module = types.ModuleType(f"{__name__}.{part}")
    sys.modules[module.__name__] = module  # dataclasses resolve annotations through it
    module.__dict__.update(inject)
    for name in snippets:
        source = _checked(name)
        filename = f"<{BASE_SHA}:{name}>"
        linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
        code = compile(
            source,
            filename,
            "exec",
            flags=__future__.annotations.compiler_flag,
            dont_inherit=True,
        )
        exec(code, module.__dict__)  # noqa: S102 - verbatim frozen base source, see header
    return module


text_pii = _load("text_pii", ("_TEXT_PII_53", "_TEXT_PII_60_62"))
speech = _load(
    "speech",
    ("_SPEECH_19_25", "_SPEECH_28_227"),
    normalize_digits=text_pii.normalize_digits,
)
locations = _load(
    "locations",
    ("_LOCATIONS_10_12", "_LOCATIONS_16_83"),
    normalize_digits=text_pii.normalize_digits,
)
voicemail = _load(
    "voicemail",
    ("_VOICEMAIL_16_22", "_VOICEMAIL_28_60", "_VOICEMAIL_313_315", "_VOICEMAIL_348_349"),
)
security = _load("security", ("_SECURITY_1_4", "_SECURITY_8_9", "_SECURITY_20_33"))
ingest = _load(
    "ingest",
    ("_INGEST_91_106",),
    normalize_iranian_mobile=security.normalize_iranian_mobile,
)

# routers/v1/auth.py:616-618 is a keyword argument inside the verify_otp(...) call; the copy
# evaluates exactly those three lines as the argument list of dict(...) with ``payload`` bound.
_AUTH_OTP_CALL = eval(  # noqa: S307 - frozen base expression, see the module docstring
    compile(
        "lambda payload: dict(\n" + _checked("_AUTH_616_618") + ')["code"]',
        f"<{BASE_SHA}:_AUTH_616_618>",
        "eval",
        dont_inherit=True,
    )
)


def otp_code_digits(code: str) -> str:
    """The base OTP ``code`` translation (routers/v1/auth.py:616-618) applied to ``code``."""
    return _AUTH_OTP_CALL(SimpleNamespace(code=code))
