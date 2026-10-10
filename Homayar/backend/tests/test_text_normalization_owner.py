"""Blind acceptance oracle for HOM-120 (WP22-T02), written from the SPEC at base 7112407.

``TestGolden*`` classes freeze behaviour captured at the base commit and must pass there
and after the refactor.  ``TestOwnerStructure`` and ``TestDeliverables`` describe the
single-owner refactor itself and fail at the base commit by design.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import hashlib
import inspect
import io
import json
import re
import subprocess
import sys
import time
import tokenize
import unicodedata
from functools import cache
from pathlib import Path
from types import MappingProxyType

import pytest

BASE_SHA = "7112407"
BASE_UNICODE = "15.0.0"
TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
HOMAYAR = BACKEND.parent
OWNER_PATH = BACKEND / "speech" / "text_normalize.py"

_REF_ZWNJ = "\u200c"
_REF_DIGITS = str.maketrans(
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
    "01234567890123456789",
)
_REF_ARABIC_TO_PERSIAN = str.maketrans(
    {
        "\u064a": "ی",
        "\u0649": "ی",
        "\u0643": "ک",
        "\u0629": "ه",
        "\u06c0": "ه",
        "\u0623": "ا",
        "\u0625": "ا",
        "\u0671": "ا",
        "\u0624": "و",
        "\u0672": "ا",
    }
)
_REF_DIACRITICS_RE = re.compile("[\u064b-\u065f\u0670\u0640]")
_REF_PUNCT = str.maketrans(
    {"?": "\u061f", ";": "\u061b", ",": "\u060c", "\u00ab": '"', "\u00bb": '"'}
)
_REF_SPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_REF_ZW_RUN_RE = re.compile(f"[{_REF_ZWNJ}\u200d]+")
_REF_SPACE_AROUND_ZWNJ_RE = re.compile(f" *{_REF_ZWNJ} *")
_REF_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.\u060c\u061b\u061f!:)])")
_REF_MI_PREFIX_RE = re.compile(r"(?<![\w\u0600-\u06ff])(ن?می)[ ]+(?=[\u0600-\u06ff]{2,})")
_REF_HA_SUFFIX_RE = re.compile(
    r"(?<=[\u0600-\u06ff]{2}) +(ها(?:ی|یی|یم|یت"
    r"|یش|یمان|یتان|یشان)?)"
    r"(?![\u0600-\u06ff])"
)
_REF_AZ_LATIN = str.maketrans(
    {"\u00e4": "\u0259", "\u00c4": "\u018f", "\u01dd": "\u0259", "\u018e": "\u018f"}
)
_REF_COLLOQUIAL_FA: dict[str, str] = {
    "میخوام": "می\u200cخواهم",
    "می\u200cخوام": "می\u200cخواهم",
    "میخوای": "می\u200cخواهی",
    "می\u200cخوای": "می\u200cخواهی",
    "میخواد": "می\u200cخواهد",
    "می\u200cخواد": "می\u200cخواهد",
    "نمیخوام": "نمی\u200cخواهم",
    "نمی\u200cخوام": "نمی\u200cخواهم",
    "میتونم": "می\u200cتوانم",
    "می\u200cتونم": "می\u200cتوانم",
    "میتونید": "می\u200cتوانید",
    "می\u200cتونید": "می\u200cتوانید",
    "نمیتونم": "نمی\u200cتوانم",
    "نمی\u200cتونم": "نمی\u200cتوانم",
    "میشه": "می\u200cشود",
    "می\u200cشه": "می\u200cشود",
    "نمیشه": "نمی\u200cشود",
    "نمی\u200cشه": "نمی\u200cشود",
    "میگم": "می\u200cگویم",
    "می\u200cگم": "می\u200cگویم",
    "میدم": "می\u200cدهم",
    "می\u200cدم": "می\u200cدهم",
    "بدین": "بدهید",
    "بگین": "بگویید",
    "دارین": "دارید",
    "هستین": "هستید",
    "کنین": "کنید",
    "اینجوری": "این\u200cجوری",
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


def _ref_normalize_digits(text: str) -> str:
    return text.translate(_REF_DIGITS)


def _ref_unify_arabic_letters(text: str) -> str:
    return text.translate(_REF_ARABIC_TO_PERSIAN)


def _ref_strip_diacritics(text: str) -> str:
    return _REF_DIACRITICS_RE.sub("", text)


def _ref_normalize_whitespace(text: str) -> str:
    text = _REF_SPACE_RE.sub(" ", text.replace("\u200b", " "))
    text = re.sub(r"[\r\n]+", " ", text)
    return re.sub(" {2,}", " ", text).strip()


def _ref_normalize_zwnj(text: str) -> str:
    text = _REF_ZW_RUN_RE.sub(_REF_ZWNJ, text)
    text = _REF_SPACE_AROUND_ZWNJ_RE.sub(_REF_ZWNJ, text)
    text = _REF_MI_PREFIX_RE.sub(lambda m: m.group(1) + _REF_ZWNJ, text)
    text = _REF_HA_SUFFIX_RE.sub(lambda m: _REF_ZWNJ + m.group(1), text)
    return re.sub(f"(^|[ ]){_REF_ZWNJ}+|{_REF_ZWNJ}+($|[ ])", r"\1\2", text)


def _ref_normalize_punctuation_fa(text: str) -> str:
    text = text.translate(_REF_PUNCT)
    return _REF_SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def _ref_apply_token_map(text: str, table) -> str:
    if not table:
        return text
    return " ".join(table.get(tok, tok) for tok in text.split(" "))


def _ref_normalize_fa(text: str, *, colloquial=None, punctuation: bool = True) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _ref_unify_arabic_letters(text)
    text = _ref_strip_diacritics(text)
    text = _ref_normalize_digits(text)
    text = _ref_normalize_whitespace(text)
    if colloquial:
        text = _ref_apply_token_map(text, colloquial)
    text = _ref_normalize_zwnj(text)
    if punctuation:
        text = _ref_normalize_punctuation_fa(text)
    return text


def _ref_normalize_en(text: str, **_: object) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ref_normalize_digits(text)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return _ref_normalize_whitespace(text)


def _ref_normalize_az(text: str, **_: object) -> str:
    text = unicodedata.normalize("NFC", text).translate(_REF_AZ_LATIN)
    text = _ref_unify_arabic_letters(text)
    text = _ref_strip_diacritics(text)
    text = _ref_normalize_digits(text)
    return _ref_normalize_zwnj(_ref_normalize_whitespace(text))


_REF_REGISTRY = {"fa": _ref_normalize_fa, "en": _ref_normalize_en, "az": _ref_normalize_az}


def _ref_normalize_for_matching(text: str, language: str = "fa") -> str:
    text = _REF_REGISTRY[language](text)
    text = text.replace(_REF_ZWNJ, "")
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return _ref_normalize_whitespace(text)


_REF_LOCATIONS_MAP = {
    "\u064a": "ی",
    "\u0649": "ی",
    "\u0643": "ک",
    "\u0629": "ه",
    "\u0623": "ا",
    "\u0625": "ا",
    "\u0671": "ا",
    "\u200c": " ",
    "\u200d": "",
    "\u200f": "",
    "\u200e": "",
    "\u0640": "",
    "\u066b": ".",
    "\u066c": ",",
    "\u060c": ",",
    "\u00a0": " ",
}
_REF_LOCATIONS_DROP = {chr(c) for c in range(0x064B, 0x0660)} | {"\u0670"}


def _ref_char_stream(text: str) -> tuple[str, tuple[int, ...], str]:
    digits = _ref_normalize_digits(text)
    out: list[str] = []
    offsets: list[int] = []
    prev_space = False
    for index, char in enumerate(digits):
        if char in _REF_LOCATIONS_DROP:
            continue
        mapped = _REF_LOCATIONS_MAP.get(char, char)
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
    return "".join(out), tuple(offsets), text


def _ref_span(offsets: tuple[int, ...], original: str, start: int, end: int) -> tuple[int, int]:
    if start >= len(offsets):
        return len(original), len(original)
    o_start = offsets[start]
    o_end = offsets[end - 1] + 1 if end > start else o_start
    return o_start, o_end


def _ref_locations_text(text: str) -> str:
    return _ref_char_stream(text)[0].strip()


def _ref_is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


_REF_VOICEMAIL_MAP = str.maketrans(
    {
        "\u064a": "ی",
        "\u0649": "ی",
        "\u0643": "ک",
        "\u0629": "ه",
        "\u06c0": "ه",
        "\u0623": "ا",
        "\u0625": "ا",
        "\u0671": "ا",
        "\u0624": "و",
        "\u200c": " ",
        "\u200d": "",
        "\u200f": "",
        "\u200e": "",
        "\u0640": "",
        **{chr(0x06F0 + i): str(i) for i in range(10)},
        **{chr(0x0660 + i): str(i) for i in range(10)},
    }
)
_REF_VOICEMAIL_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u06d6-\u06ed]")
_REF_VOICEMAIL_SPACE = re.compile(r"\s+")
_REF_VOICEMAIL_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_REF_WORD_CHARS = re.compile(r"\w", re.UNICODE)


def _ref_voicemail_text(value) -> str:
    text = unicodedata.normalize("NFKC", value or "").translate(_REF_VOICEMAIL_MAP)
    text = _REF_VOICEMAIL_DIACRITICS.sub("", text).casefold()
    text = _REF_VOICEMAIL_PUNCT.sub(" ", text)
    return _REF_VOICEMAIL_SPACE.sub(" ", text).strip()


def _ref_is_empty_transcript(text: str) -> bool:
    return len(_REF_WORD_CHARS.findall(_ref_voicemail_text(text))) < 3


_REF_PERSIAN_DIGITS = str.maketrans(
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "0123456789"
)
_REF_ARABIC_DIGITS = str.maketrans(
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789"
)


def _ref_normalize_iranian_mobile(value: str) -> str:
    raw = (
        unicodedata.normalize("NFKC", value)
        .translate(_REF_PERSIAN_DIGITS)
        .translate(_REF_ARABIC_DIGITS)
    )
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


def _ref_normalize_caller_number(value):
    if value is None or not value.strip():
        return None, False
    try:
        return _ref_normalize_iranian_mobile(value), True
    except ValueError:
        # HOM-165: fullwidth digits joined the canonical set (base had only two runs).
        raw = value.strip().translate(
            {
                **{0x06F0 + i: str(i) for i in range(10)},
                **{0x0660 + i: str(i) for i in range(10)},
                **{0xFF10 + i: str(i) for i in range(10)},
            }
        )
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None, False
        return (("+" if raw.startswith("+") else "") + digits)[:32], False


def _ref_stream(text: str):
    out, offsets, original = _ref_char_stream(text)
    size = len(out)
    spans = tuple(
        _ref_span(offsets, original, i, j) for i in range(size + 1) for j in range(i, size + 1)
    )
    return out, offsets, original, spans


def _current_stream(text: str):
    from backend.locations.textnorm import NormalizedText, normalize_char_stream

    result = normalize_char_stream(text)
    assert isinstance(result, NormalizedText)
    size = len(result.text)
    spans = tuple(result.span(i, j) for i in range(size + 1) for j in range(i, size + 1))
    return result.text, tuple(result.offsets), result.original, spans


REFERENCES = {
    "text_pii.normalize_digits": _ref_normalize_digits,
    "owner.normalize_digits": _ref_normalize_digits,
    "unify_arabic_letters": _ref_unify_arabic_letters,
    "strip_diacritics": _ref_strip_diacritics,
    "normalize_whitespace": _ref_normalize_whitespace,
    "normalize_zwnj": _ref_normalize_zwnj,
    "normalize_punctuation_fa": _ref_normalize_punctuation_fa,
    "apply_token_map[colloquial]": lambda s: _ref_apply_token_map(s, _REF_COLLOQUIAL_FA),
    "normalize_fa": _ref_normalize_fa,
    "normalize_fa[colloquial]": lambda s: _ref_normalize_fa(s, colloquial=_REF_COLLOQUIAL_FA),
    "normalize_fa[punctuation=False]": lambda s: _ref_normalize_fa(s, punctuation=False),
    "normalize_en": _ref_normalize_en,
    "normalize_az": _ref_normalize_az,
    "normalize_for_matching[fa]": lambda s: _ref_normalize_for_matching(s, "fa"),
    "normalize_for_matching[en]": lambda s: _ref_normalize_for_matching(s, "en"),
    "normalize_for_matching[az]": lambda s: _ref_normalize_for_matching(s, "az"),
    "locations.normalize_char_stream": _ref_stream,
    "locations.normalize_text": _ref_locations_text,
    "locations.is_word_char": _ref_is_word_char,
    "voicemail.normalize_text": _ref_voicemail_text,
    "voicemail.is_empty_transcript": _ref_is_empty_transcript,
    "security.normalize_iranian_mobile": _ref_normalize_iranian_mobile,
    "ingest.normalize_caller_number": _ref_normalize_caller_number,
}


def _current(name: str):
    from backend.locations import textnorm
    from backend.privacy import text_pii
    from backend.security import normalization
    from backend.speech import text_normalize as owner
    from backend.voicemail import classify, ingest

    table = {
        "text_pii.normalize_digits": text_pii.normalize_digits,
        "owner.normalize_digits": owner.normalize_digits,
        "unify_arabic_letters": owner.unify_arabic_letters,
        "strip_diacritics": owner.strip_diacritics,
        "normalize_whitespace": owner.normalize_whitespace,
        "normalize_zwnj": owner.normalize_zwnj,
        "normalize_punctuation_fa": owner.normalize_punctuation_fa,
        "apply_token_map[colloquial]": lambda s: owner.apply_token_map(
            s, owner.DEFAULT_COLLOQUIAL_FA
        ),
        "normalize_fa": owner.normalize_fa,
        "normalize_fa[colloquial]": lambda s: owner.normalize_fa(
            s, colloquial=owner.DEFAULT_COLLOQUIAL_FA
        ),
        "normalize_fa[punctuation=False]": lambda s: owner.normalize_fa(s, punctuation=False),
        "normalize_en": owner.normalize_en,
        "normalize_az": owner.normalize_az,
        "normalize_for_matching[fa]": lambda s: owner.normalize_for_matching(s, "fa"),
        "normalize_for_matching[en]": lambda s: owner.normalize_for_matching(s, "en"),
        "normalize_for_matching[az]": lambda s: owner.normalize_for_matching(s, "az"),
        "locations.normalize_char_stream": _current_stream,
        "locations.normalize_text": textnorm.normalize_text,
        "locations.is_word_char": textnorm.is_word_char,
        "voicemail.normalize_text": classify.normalize_text,
        "voicemail.is_empty_transcript": classify.is_empty_transcript,
        "security.normalize_iranian_mobile": normalization.normalize_iranian_mobile,
        "ingest.normalize_caller_number": ingest.normalize_caller_number,
    }
    return table[name]


SWEEP_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("", ""),
    ("ب", "ب"),
    ("\u06f1", "2"),
    ("\u064a", ""),
    ("", "\u0654"),
    ("", "\u0653"),
    ("می ", "روم"),
    ("کتاب ", "ها"),
)


@cache
def _sweep_strings(template: int) -> tuple[str, ...]:
    prefix, suffix = SWEEP_TEMPLATES[template]
    return tuple(
        prefix + chr(code) + suffix for code in range(0x10000) if not 0xD800 <= code <= 0xDFFF
    )


def _capture(func, text):
    try:
        return func(text)
    except Exception as exc:
        return ("raised", type(exc).__name__, str(exc))


def _digest(outputs) -> str:
    digest = hashlib.sha256()
    for item in outputs:
        digest.update(repr(item).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


SWEEP_DIGESTS: dict[tuple[str, int], str] = {
    (
        "text_pii.normalize_digits",
        0,
    ): "51903acecafd49de6401f1496e8617ab3ccfb93d686752ea85ddb2afca676fc0",
    (
        "text_pii.normalize_digits",
        1,
    ): "a62b2029bc1a0dceded0a10399b10cb9d2ea18a41f0aead894af195ba6899c3b",
    (
        "text_pii.normalize_digits",
        2,
    ): "e89fce809bddfc6df40223ae10e00a7962799bb7f598c42925c85db3a9c8fc58",
    (
        "text_pii.normalize_digits",
        3,
    ): "04e361d20223a1cd2dae2bc4e64347ba2a91b4f096bd03c80a88bde539a640d4",
    (
        "text_pii.normalize_digits",
        4,
    ): "754a91a754358354e883a2dbe4927c4dbad91c5bdad53fe894b23f361f874d85",
    (
        "text_pii.normalize_digits",
        5,
    ): "38243728de474c370c11282958b437fb84876d63119262c843294c98df5086b3",
    (
        "text_pii.normalize_digits",
        6,
    ): "a350eb2929a31c208f12a2ae1a8df1e1e73b3974382fd9c5ffc3f109421da2b4",
    (
        "text_pii.normalize_digits",
        7,
    ): "e0e7859bd9eac1a004893b5d6957022a3fe633651d47a3d75da3710968769c0e",
    (
        "owner.normalize_digits",
        0,
    ): "51903acecafd49de6401f1496e8617ab3ccfb93d686752ea85ddb2afca676fc0",
    (
        "owner.normalize_digits",
        1,
    ): "a62b2029bc1a0dceded0a10399b10cb9d2ea18a41f0aead894af195ba6899c3b",
    (
        "owner.normalize_digits",
        2,
    ): "e89fce809bddfc6df40223ae10e00a7962799bb7f598c42925c85db3a9c8fc58",
    (
        "owner.normalize_digits",
        3,
    ): "04e361d20223a1cd2dae2bc4e64347ba2a91b4f096bd03c80a88bde539a640d4",
    (
        "owner.normalize_digits",
        4,
    ): "754a91a754358354e883a2dbe4927c4dbad91c5bdad53fe894b23f361f874d85",
    (
        "owner.normalize_digits",
        5,
    ): "38243728de474c370c11282958b437fb84876d63119262c843294c98df5086b3",
    (
        "owner.normalize_digits",
        6,
    ): "a350eb2929a31c208f12a2ae1a8df1e1e73b3974382fd9c5ffc3f109421da2b4",
    (
        "owner.normalize_digits",
        7,
    ): "e0e7859bd9eac1a004893b5d6957022a3fe633651d47a3d75da3710968769c0e",
    ("unify_arabic_letters", 0): "03387803f6c8486022b1d4a9f029583b4a93db49dcc510f961bdee97ff7e7b27",
    ("unify_arabic_letters", 1): "49f54bb4c0cce5cdb60abbdc30a05888d6e17308c69d4138eb89904696a7b7b6",
    ("unify_arabic_letters", 2): "0a1443c57e456422ac95525e59399a2bb47489b82c0de195ba244c677ea284db",
    ("unify_arabic_letters", 3): "96b15b2204cc8fd4e89214bf371ef7d021247c4b617a810ed2e26b2e097c35ec",
    ("unify_arabic_letters", 4): "4c3b3b43ad171225fc79873f2f573cd0536b0df3730fbb39724c3ac16ea28b63",
    ("unify_arabic_letters", 5): "c90213656ebf8f72c2ce0278dc080f7c251050cad23043e5650966a3fc272929",
    ("unify_arabic_letters", 6): "1a413eee4e3f60dadc3a23f2e7165b330e724ac5d5865ca426a914edae7925c1",
    ("unify_arabic_letters", 7): "6f201f8a31f20dbe4845792e0616b3504d57041a66f6ba383f9f231777cd88e7",
    ("strip_diacritics", 0): "47f7856c74cd3c9f9ebab2a9e3a0257ae1c7e2dbe220300e29ffee083540efe4",
    ("strip_diacritics", 1): "fee33b1bd47dd94a24cd6b5f070a68e251fcf376c5bdd83e1c7fb74cd4ca7220",
    ("strip_diacritics", 2): "ca5d270ca4b7ca6d04d0cc1722bbbf3ba068605d0dc6918f0d0a561a19c4d8c2",
    ("strip_diacritics", 3): "322aa4c0349929e404db4b58ba0f56717d2184500dfbef971cc832900c5d99ac",
    ("strip_diacritics", 4): "47f7856c74cd3c9f9ebab2a9e3a0257ae1c7e2dbe220300e29ffee083540efe4",
    ("strip_diacritics", 5): "47f7856c74cd3c9f9ebab2a9e3a0257ae1c7e2dbe220300e29ffee083540efe4",
    ("strip_diacritics", 6): "704a1931ffc09b2e573cc56929390e107eaa0e5e1a5b78451739213ca7031167",
    ("strip_diacritics", 7): "5366296c432d20090f8e95e5a43184afd9f0f3b487a1a5ea8a732f7f5a928f9c",
    ("normalize_whitespace", 0): "61e7c3fe3b24891cc650d111d302dd9bc92513e1c2c9b75d9a500c2254d99852",
    ("normalize_whitespace", 1): "0453307f3cf7dd1628067083b428cf23c9743b0bc6d77a0d8a7e4b80eceebda0",
    ("normalize_whitespace", 2): "27a0a7a296e14ebf38022dda49947dc77d2e4b20c3e3085633e8b341ba1c0df4",
    ("normalize_whitespace", 3): "5a2894173f26f5f66fe3d5e922d6f2bd925d0f61258ad16c8185c1e2e91d387b",
    ("normalize_whitespace", 4): "fba7753552ba919ffcd6c12e52e78cf0c5e9461c982bd5371b997cd1328ede4f",
    ("normalize_whitespace", 5): "027677acccd07cf51b761d13df5ac1f52fb0b657c4788cbc84cf13181c7175e7",
    ("normalize_whitespace", 6): "29c5b12893417ef579401c9ed2abf62646d48d4b1bb4895efcdb19897858c6f0",
    ("normalize_whitespace", 7): "ec8fd32795b9e1d87b252816a7c01b42d90c5c0bf76159ccbaba9343f2653128",
    ("normalize_zwnj", 0): "b96ba2dbdf0333e39f707e0949d343fc330b27b7c2a67adadc425ded8d3e1144",
    ("normalize_zwnj", 1): "722d7754e7403af488998faf9bba571c99a1a1bff06d5fb6b1917e73c2e6823d",
    ("normalize_zwnj", 2): "f3b85553513795ecf8275fc69ece47bfd46be792adc93b153a510f38f3d36877",
    ("normalize_zwnj", 3): "81aabb03c4071b6c56ffb79be4279b2301efdad50bfc04c6a913c4cd1e693a90",
    ("normalize_zwnj", 4): "1c2cdd642e56fa1d15746977562b12b780f813a2296199bbe4e6be17df8dbe36",
    ("normalize_zwnj", 5): "4fc421eeb9ccd028e1aa1c8b22b05c7a31d8f9d126fab79b024c126aa218bc92",
    ("normalize_zwnj", 6): "3871377e7b251f70a83085d92af60862d17c58fbab57ae0d9222b86e9cb49f9e",
    ("normalize_zwnj", 7): "36c18e0a32d13349be20d6bcba37afb43466f3b11b3f0b39a03fb86be8367a49",
    (
        "normalize_punctuation_fa",
        0,
    ): "b6a578aee6bd7001dc3d32bfe7e178d669234f6bf41e7ab2fdebf43141b517d7",
    (
        "normalize_punctuation_fa",
        1,
    ): "956e449a8b3ae6334eb2af588f6a1265336dc3cbe5e399a6489d277303101658",
    (
        "normalize_punctuation_fa",
        2,
    ): "38a1fd22a877861bd757567b0b07cc40476dcce0d70ae2a3d80cd24413234d4a",
    (
        "normalize_punctuation_fa",
        3,
    ): "4cc7691235460b0bbf77b43f23e0311422aeb6c23475f7ab0f20557e6ad2ba38",
    (
        "normalize_punctuation_fa",
        4,
    ): "2f6cf0cb2f0ae9353e2c61c0038c6a9dfcea548a55530bb865d8edfdde2b8e38",
    (
        "normalize_punctuation_fa",
        5,
    ): "a5cc54bce43a25346bb54c6459d8232d273d5934a6fa029be1aaad3fcab962c9",
    (
        "normalize_punctuation_fa",
        6,
    ): "6379990ad29c5f40b228e0f87560bcfc04c965855acb60c4c88cdb7321b0f6f4",
    (
        "normalize_punctuation_fa",
        7,
    ): "9a2d3d047f2c689942b9fc690ffd0c23d6a35b595db30b5e286cfe47866f2e68",
    (
        "apply_token_map[colloquial]",
        0,
    ): "a2c2ad3b8b07491389e4558a84568ed1517debdea0988d137a82b675612b761a",
    (
        "apply_token_map[colloquial]",
        1,
    ): "8abaac6ae803db9ef01a0d9235f4aaed38021a4ae00599c366a57c54b9fc5b54",
    (
        "apply_token_map[colloquial]",
        2,
    ): "2098f4579c601ae517b1ba08f331d23a6d09add3c2e2c708cf4e59ad180d0c82",
    (
        "apply_token_map[colloquial]",
        3,
    ): "734b9b71409e153b7f9d38e34061ac330b1957912ff2cd5a4aacef0e6a4de7fb",
    (
        "apply_token_map[colloquial]",
        4,
    ): "a346c90f6547eb6ccd1df13f61e7470c86bc9c4cf602ec497eb34a08a761920f",
    (
        "apply_token_map[colloquial]",
        5,
    ): "d9b28d3d48fe2a3d106d0f5f0ee3aac998958ef4d49aa4cb3b522c6ee53a837f",
    (
        "apply_token_map[colloquial]",
        6,
    ): "9e0b04a0014d230a4e26d5f9934f19d9d0204b6b13bfa58d51b767b28ed58551",
    (
        "apply_token_map[colloquial]",
        7,
    ): "a6d0df1b6cb97b22300406e7b8c6ef836b2e783dcf3ab00b8f170283670d48d2",
    ("normalize_fa", 0): "bc0baaaaee5b3b3222bca635152b4b283c220cc8d4827325bf1f4ed988faf682",
    ("normalize_fa", 1): "b338cb8e625405f7325c0280f4b15b1bc5f0851dbd8597fcdba86e1ffc92cee3",
    ("normalize_fa", 2): "944c3ce3d8a53d0ed517285b500dceeb2b21a914747b8a5f064825dc393fcdaa",
    ("normalize_fa", 3): "610ee2da1200ecaad5fa9180cafe866010760e995908e2f05a58d2233643fa99",
    ("normalize_fa", 4): "9c955d84c9c08175156ae3291d42b2a09beef0961f231dbba888bf4dc1279113",
    ("normalize_fa", 5): "7d276a274ff4f5846f0733aa1efeab0591b95ac54a4dd6344a2715f2a0d0f579",
    ("normalize_fa", 6): "211a21d5222433c42746512ad88643a387cf8ac22d39322b37aab61346b4f2be",
    ("normalize_fa", 7): "8c1ae64c4daf86bbb867221cf730144342697fab45bb7926dd37a55ef90d49ec",
    (
        "normalize_fa[colloquial]",
        0,
    ): "bc0baaaaee5b3b3222bca635152b4b283c220cc8d4827325bf1f4ed988faf682",
    (
        "normalize_fa[colloquial]",
        1,
    ): "b338cb8e625405f7325c0280f4b15b1bc5f0851dbd8597fcdba86e1ffc92cee3",
    (
        "normalize_fa[colloquial]",
        2,
    ): "944c3ce3d8a53d0ed517285b500dceeb2b21a914747b8a5f064825dc393fcdaa",
    (
        "normalize_fa[colloquial]",
        3,
    ): "000c0984252f93466012dfb5f18f5dd2d68e60e0803951af18f54d861e1b8ac7",
    (
        "normalize_fa[colloquial]",
        4,
    ): "9c955d84c9c08175156ae3291d42b2a09beef0961f231dbba888bf4dc1279113",
    (
        "normalize_fa[colloquial]",
        5,
    ): "7d276a274ff4f5846f0733aa1efeab0591b95ac54a4dd6344a2715f2a0d0f579",
    (
        "normalize_fa[colloquial]",
        6,
    ): "211a21d5222433c42746512ad88643a387cf8ac22d39322b37aab61346b4f2be",
    (
        "normalize_fa[colloquial]",
        7,
    ): "8c1ae64c4daf86bbb867221cf730144342697fab45bb7926dd37a55ef90d49ec",
    (
        "normalize_fa[punctuation=False]",
        0,
    ): "bb943b83fbf0fe407ddb726ea424f89a045287470b4aeebc747399cb03863c53",
    (
        "normalize_fa[punctuation=False]",
        1,
    ): "c03c242474f5e9c09a8d556f6caf9596f188512aa6f1b5923e233c86d17a19ce",
    (
        "normalize_fa[punctuation=False]",
        2,
    ): "a3fde86f12d3eedf1a8c66792bb2d7506ab89dd3b0f0c35d3701336af7057b01",
    (
        "normalize_fa[punctuation=False]",
        3,
    ): "1eb62420a99387d2dd116839dd17d841da2dd871807cef5f7aff469a5eea23c1",
    (
        "normalize_fa[punctuation=False]",
        4,
    ): "1741977610e37236e7f5d0680bcb17e425b4f21440cb42454ac96ddf7ee10664",
    (
        "normalize_fa[punctuation=False]",
        5,
    ): "56a91b5d52464ce162e73fa7f79279e479c3dc61e29cd8b103d0edda950c28fd",
    (
        "normalize_fa[punctuation=False]",
        6,
    ): "05a02ba5ea2879157953b714c39b0e4f2ea7e67cbf691f6b735280b8b3ccd761",
    (
        "normalize_fa[punctuation=False]",
        7,
    ): "0b5d08f7c9063002dd65085f1c43e589e1ea8a1856bfbac99b19603d7e070ebf",
    ("normalize_en", 0): "a2b3938cf4ef3d8cfcdae2e4884af03a8887061598be34e57ecd00a07e5ab5d7",
    ("normalize_en", 1): "571c6f0ed3ef4fd6c65f6081e316c29a305c91124b9c62033a24ca87963b9b67",
    ("normalize_en", 2): "2bc02a53266c6e214814daf4b4e7db1888466ae2cb53bc674042e518f130f274",
    ("normalize_en", 3): "999dba6f6d2c8066d6f4709e8ba1b49904c211457b61071f3b0fa71fd38914cb",
    ("normalize_en", 4): "1aafe7b5aa9c15861b57e856a3a79edc05cee21b1a7d64fc9a5c0da8141a5f32",
    ("normalize_en", 5): "ec16947a77c835dbc3e488f0cb88d40e7b9c69ab3f305b14e8cbb3fc76b4b12c",
    ("normalize_en", 6): "031c0c0dd799ba4d93b7af13305be0171d110915859312235f00d05edf33acb9",
    ("normalize_en", 7): "cf150d09764f9491539ac283581a2a86b814a1bbfdaa365b69ffb6f5ce2a17e9",
    ("normalize_az", 0): "f32eaecada36a9452521a3cfeee78e4b48a067a048da0b2fdf12378e670d4a9f",
    ("normalize_az", 1): "18a31e02dad7ec83b98ea4737e6b52222e7696cdcbd2b74a8fd4b7858e4c8233",
    ("normalize_az", 2): "db753c93d8f5cdd94b3d099773f6b8d2fa184251a2a666f2bbad49a38b9419d5",
    ("normalize_az", 3): "b3c4bdcb205578865056c526fba3c8bf5bd8f168ec53e733ddcc180caac9fc23",
    ("normalize_az", 4): "7ef877bb7869278991ac88004988540a0122689b3eb6dac87fe94d231f1e554c",
    ("normalize_az", 5): "0daac00213985087a2efba2a0340f675261f643909b1cc07a90b3e219169e2f6",
    ("normalize_az", 6): "987582669e14057899a495e17300eb57ba7f685df58c1918d16da8234b947694",
    ("normalize_az", 7): "3046c0404e102f0927f7589875363ddf07749cef647a2e86c7fcf52a0edbfe10",
    (
        "normalize_for_matching[fa]",
        0,
    ): "57f856e54fe1dce171e7635366915d817e89b4327225a505128573b62c2bd3bb",
    (
        "normalize_for_matching[fa]",
        1,
    ): "2ec7b1859300b0059d6cbd035a72bd014f52646e633b7b6049927e074c893507",
    (
        "normalize_for_matching[fa]",
        2,
    ): "fe8234f1afdbedac91c32c70d7a52867f7bd01feed84f7bea5cacb1665e90003",
    (
        "normalize_for_matching[fa]",
        3,
    ): "9cad9d053e6ef42c3b01bf72a436c6adb25550a96e629d79fee499e7b2726bd5",
    (
        "normalize_for_matching[fa]",
        4,
    ): "75ff7aa5713cfd5ebc41f00027442abe0286693e6d6230a2be8ffbbe2c14c346",
    (
        "normalize_for_matching[fa]",
        5,
    ): "4d6fd6af94c8057c1c95df00ef1e870d59792f7167b3a7d0a11292a585f794e5",
    (
        "normalize_for_matching[fa]",
        6,
    ): "bdd503795b966743bd1b05ae442611e8d717045e4dec5c348fb5750b0a12c431",
    (
        "normalize_for_matching[fa]",
        7,
    ): "6503fe15eec6498aaf74b18a3e74667fd03a418119d9063c347ac449fab5aaaf",
    (
        "normalize_for_matching[en]",
        0,
    ): "c662405874ae4fd727984e621131c38e594c526f11fc64cbd9fa381926cc90c7",
    (
        "normalize_for_matching[en]",
        1,
    ): "4c88e07953e941b8ad927bf737110572ac79d58e4dc42dcb98d2ebd17b8420e7",
    (
        "normalize_for_matching[en]",
        2,
    ): "024094b5605e9662ad4829b24351f13e1e84e4584e39add05b481acc59d551df",
    (
        "normalize_for_matching[en]",
        3,
    ): "3b8bfbe66c28de17d934dfaeac7f86166d384795022e745706213b1672224ec5",
    (
        "normalize_for_matching[en]",
        4,
    ): "e3fe82566522f645c7579803276e1aa80cadbcb04d62ad2936708de8cf17066f",
    (
        "normalize_for_matching[en]",
        5,
    ): "5e4b5e198ea0d8f4808cc20dd6d67808036c64436640568578232044971fbe60",
    (
        "normalize_for_matching[en]",
        6,
    ): "74922529944ce13205ef38f86a77ee1903147326e02db6c947560a015e31bc42",
    (
        "normalize_for_matching[en]",
        7,
    ): "352612992c7d8c081a2a7e9660c90ba58bdf901947c5fbd48daca41f250ae7df",
    (
        "normalize_for_matching[az]",
        0,
    ): "89f11499032edf6ab1310a3449ec875d15e57fdd4df2736f64cbd9e65242a5d6",
    (
        "normalize_for_matching[az]",
        1,
    ): "cf55f4d1991e50778e5a0be8b1f51bf9422d1291cd91b442a49c65fe990c2930",
    (
        "normalize_for_matching[az]",
        2,
    ): "706d80629e3da83592a58b36714ec9779904e885c38bd9bfa2f8e790614e66b0",
    (
        "normalize_for_matching[az]",
        3,
    ): "f88c741bf99aea55af0b1ec8ff981d628afc510986cc6628d87161e5ecff2ee6",
    (
        "normalize_for_matching[az]",
        4,
    ): "60fc77f6ec04f09836ed43db71c23e0a6fd72f9b302bc2e2aa7a9036784f2a84",
    (
        "normalize_for_matching[az]",
        5,
    ): "cbb5ae64a4ee51bbfe4472506d96fc549455f468b86cb4acd3d2164fada63fbd",
    (
        "normalize_for_matching[az]",
        6,
    ): "4cddb949176af6941a54e53f5ee8434ef7c96b2226a9a5b1a71d28e93ec9943f",
    (
        "normalize_for_matching[az]",
        7,
    ): "df83472173d7d7d1e53f9060baabf0d9bcb34a894087f95a7bee883aca39a395",
    (
        "locations.normalize_char_stream",
        0,
    ): "2368fa82f1438459b78dc8bbfd9ee2e2f5e6a7134265727a904a118f48196c47",
    (
        "locations.normalize_char_stream",
        1,
    ): "5ac01e6b550ae18f0129225291d9f1fc052425d6ef50b4813436f87461209e7d",
    (
        "locations.normalize_char_stream",
        2,
    ): "aada417a92fbffb79e0e4d62e0fc165e3b6f38131aa3b2bb49a86fc138c5d20e",
    (
        "locations.normalize_char_stream",
        3,
    ): "8900e9ea084e8ae86c4e1d372a6afb8aec9a17ed7262818aa7e10ed7461d9324",
    (
        "locations.normalize_char_stream",
        4,
    ): "d8c0c9b35f6eb982808fb5f1bbff7deb56b32152d73b6ad75f3aef0ee99a2452",
    (
        "locations.normalize_char_stream",
        5,
    ): "239d747ff0f828c79f3b0e813e72c454d7b483837f4fb54350755cf801ad6de9",
    (
        "locations.normalize_char_stream",
        6,
    ): "91d2d74e30787237c51730b2634744f770775bad95edac3f3369c0c28bd4f124",
    (
        "locations.normalize_char_stream",
        7,
    ): "0a6db916dfe6f7c15fa6d555d0aa46ed319284241534d83c919feb611e5ae6f0",
    (
        "locations.normalize_text",
        0,
    ): "3a48b82dd7a8080825fbb886315e2efc4d2dd49908f0efb605dbd40041b4c090",
    (
        "locations.normalize_text",
        1,
    ): "3ef9b86a6a944eac48c765477194b19867a3797ec77dd91124ad8906468a2ce1",
    (
        "locations.normalize_text",
        2,
    ): "80bdbc86feb343a78d3f6389e57c266c28c6d73b9bb4e6e0bd5f31f62dd32439",
    (
        "locations.normalize_text",
        3,
    ): "23bfa64e38a4804a72e01dad8a045016ab3ceb86aa63b2787eb2f3263f6be301",
    (
        "locations.normalize_text",
        4,
    ): "3a48b82dd7a8080825fbb886315e2efc4d2dd49908f0efb605dbd40041b4c090",
    (
        "locations.normalize_text",
        5,
    ): "3a48b82dd7a8080825fbb886315e2efc4d2dd49908f0efb605dbd40041b4c090",
    (
        "locations.normalize_text",
        6,
    ): "18891ad97a4d9969ac035951a6621439971c99e8a3a92a7937e57d70c596c8d1",
    (
        "locations.normalize_text",
        7,
    ): "1404c1a4f9d86d69c211961b0e2562946b580af420d6462e9c5065290446c14f",
    (
        "locations.is_word_char",
        0,
    ): "ad96210ffef7103bedba27faa12cf16cb09c489546eab3ef26d811fe5a2d8c55",
    (
        "locations.is_word_char",
        1,
    ): "3411c762074fad752830ca3683274895c952c20585d12925996043b3e1f271db",
    (
        "locations.is_word_char",
        2,
    ): "3411c762074fad752830ca3683274895c952c20585d12925996043b3e1f271db",
    (
        "locations.is_word_char",
        3,
    ): "3411c762074fad752830ca3683274895c952c20585d12925996043b3e1f271db",
    (
        "locations.is_word_char",
        4,
    ): "baa072d733cc2a93c6452b4c8f639ee0fcc6f602bd0d553281e195897de5083e",
    (
        "locations.is_word_char",
        5,
    ): "baa072d733cc2a93c6452b4c8f639ee0fcc6f602bd0d553281e195897de5083e",
    (
        "locations.is_word_char",
        6,
    ): "baa072d733cc2a93c6452b4c8f639ee0fcc6f602bd0d553281e195897de5083e",
    (
        "locations.is_word_char",
        7,
    ): "baa072d733cc2a93c6452b4c8f639ee0fcc6f602bd0d553281e195897de5083e",
    (
        "voicemail.normalize_text",
        0,
    ): "cce06a342155ff5112b301a8cda0653251270d872d4a88aac041fdfea8c1849e",
    (
        "voicemail.normalize_text",
        1,
    ): "9829fda75d86d2f5f2abb983710e32ef76d738e7f66b80b6a8f3290438a312fa",
    (
        "voicemail.normalize_text",
        2,
    ): "2327e8a10aab127a3eb9dc05d6be632573d271230e5c4977759b00b30c3f4b77",
    (
        "voicemail.normalize_text",
        3,
    ): "f163cefbca36700248957b501c8255852e3d502240e631a18a0ecff073a19006",
    (
        "voicemail.normalize_text",
        4,
    ): "89dde43dbafaa98112bf2fc13ffa5bc400912ab53fd7d3d95823a72707144f45",
    (
        "voicemail.normalize_text",
        5,
    ): "88edaedc6e3cac0a8432f05a7c99ef1677a1960b9b26e04d2916b6f4eee24e0d",
    (
        "voicemail.normalize_text",
        6,
    ): "0333eadb62f863f76e0fb421bcc57c89dc8064a03f66cd7485ad76cb322494af",
    (
        "voicemail.normalize_text",
        7,
    ): "5bb03f6197eb3a6178f706574c903543ee689d301ac881a314af288cbaa23c57",
    (
        "voicemail.is_empty_transcript",
        0,
    ): "b518637c1a1a2cf2882a92a4a314e83164fc43735b65c2907eaab82f84508ec7",
    (
        "voicemail.is_empty_transcript",
        1,
    ): "93e25f48ddbcee9f81180ed0c67d695bc71fc717951212612c5a2b36f47b5ebb",
    (
        "voicemail.is_empty_transcript",
        2,
    ): "93e25f48ddbcee9f81180ed0c67d695bc71fc717951212612c5a2b36f47b5ebb",
    (
        "voicemail.is_empty_transcript",
        3,
    ): "af2f1c4f959e7508841586556717ddf5e28c4927e8a514f45a9471203c688cc5",
    (
        "voicemail.is_empty_transcript",
        4,
    ): "b518637c1a1a2cf2882a92a4a314e83164fc43735b65c2907eaab82f84508ec7",
    (
        "voicemail.is_empty_transcript",
        5,
    ): "b518637c1a1a2cf2882a92a4a314e83164fc43735b65c2907eaab82f84508ec7",
    (
        "voicemail.is_empty_transcript",
        6,
    ): "baa072d733cc2a93c6452b4c8f639ee0fcc6f602bd0d553281e195897de5083e",
    (
        "voicemail.is_empty_transcript",
        7,
    ): "baa072d733cc2a93c6452b4c8f639ee0fcc6f602bd0d553281e195897de5083e",
    (
        "security.normalize_iranian_mobile",
        0,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        1,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        2,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        3,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        4,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        5,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        6,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    (
        "security.normalize_iranian_mobile",
        7,
    ): "bf18f349e2d8737de5789bcffee773e25175137d4dca454ddb6e0b67901c2f24",
    # HOM-165: caller-number digests re-captured after fullwidth digits joined the
    # canonical set; the reference above folds them too.
    (
        "ingest.normalize_caller_number",
        0,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
    (
        "ingest.normalize_caller_number",
        1,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
    (
        "ingest.normalize_caller_number",
        2,
    ): "437e50e22d0305db48ce7f371c4b8cf43775da3729fc41991f6acb70aa18a54b",
    (
        "ingest.normalize_caller_number",
        3,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
    (
        "ingest.normalize_caller_number",
        4,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
    (
        "ingest.normalize_caller_number",
        5,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
    (
        "ingest.normalize_caller_number",
        6,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
    (
        "ingest.normalize_caller_number",
        7,
    ): "5474580c03ae994ce0f888cfb096151d32b2f4b4cacaeeb5bc7c703992585e5d",
}


class TestGoldenSweep:
    @pytest.mark.parametrize(
        ("name", "template"),
        [(name, template) for name in REFERENCES for template in range(len(SWEEP_TEMPLATES))],
    )
    def test_every_bmp_code_point_matches_base(self, name, template):
        func = _current(name)
        strings = _sweep_strings(template)
        outputs = [_capture(func, text) for text in strings]
        digest = _digest(outputs)
        on_base_unicode = unicodedata.unidata_version == BASE_UNICODE
        if on_base_unicode and digest == SWEEP_DIGESTS[(name, template)]:
            return
        reference = REFERENCES[name]
        mismatches = []
        for text, got in zip(strings, outputs, strict=True):
            want = _capture(reference, text)
            if got != want:
                mismatches.append((text, got, want))
                if len(mismatches) == 5:
                    break
        assert not mismatches, f"{name} template {template} differs from base: {mismatches!r}"
        assert not on_base_unicode, f"{name} template {template}: digest {digest} is not base"

    @pytest.mark.parametrize(
        "name", ["text_pii.normalize_digits", "owner.normalize_digits", "unify_arabic_letters"]
    )
    def test_digit_and_letter_folding_never_change_length(self, name):
        func = _current(name)
        for template in range(len(SWEEP_TEMPLATES)):
            changed = [text for text in _sweep_strings(template) if len(func(text)) != len(text)]
            assert not changed, (name, template, changed[:5])

    def test_reference_copies_reproduce_the_base_digests(self):
        if unicodedata.unidata_version != BASE_UNICODE:
            pytest.skip(f"digests were captured on Unicode {BASE_UNICODE}")
        strings = _sweep_strings(0)
        drift = [
            name
            for name, reference in REFERENCES.items()
            if _digest(_capture(reference, text) for text in strings) != SWEEP_DIGESTS[(name, 0)]
        ]
        assert drift == []


PII_SAMPLES: tuple[str, ...] = (
    "کد ملی 0012345679",
    "کد ملی \u06f0\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f9",
    "کد ملی \u0660\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0669",
    "کد ملی 001-234567-9",
    "موبایل \u06f0\u06f9\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "موبایل \u06f0\u06f9\u06f1\u06f2 \u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9",
    "mobile +98 912 345 6789.",
    "mobile +989123456789",
    "mobile 0098-912-345-6789",
    "تلفن \u06f0\u06f2\u06f1-\u06f8\u06f8\u06f7\u06f7\u06f6\u06f6\u06f5\u06f5",
    "تلفن (\u06f0\u06f2\u06f1) \u06f8\u06f8\u06f7\u06f7 \u06f6\u06f6\u06f5\u06f5",
    "call +1 202 555 0143 today",
    "card 6037991123456786",
    (
        "کارت \u06f6\u06f0\u06f3\u06f7 \u06f9\u06f9"
        "\u06f1\u06f1 \u06f2\u06f3\u06f4\u06f5 \u06f6"
        "\u06f7\u06f8\u06f6"
    ),
    "شبا IR630190000000101234567890",
    "شبا IR63 0190 0000 0010 1234 5678 90",
    "iban ir630190000000101234567890",
    "mail Ali.Rezaei+crm@Example.co.ir now",
    "ایمیل: sara_k@mail.example.com",
    "کد پستی \u06f1\u06f4\u06f3\u06f5\u06f6\u06f7\u06f8\u06f9\u06f1\u06f2 است",
    "postal code: 14356-78912",
    "کدپستی 0012345679",
    "مبلغ \u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9\u06f0 ریال پرداخت شد",
    "تاریخ \u06f1\u06f4\u06f0\u06f3/\u06f0\u06f5/\u06f1\u06f2 ساعت \u06f1\u06f0:\u06f3\u06f0",
    "سفارش INV1234567890 ثبت شد",
    "number 1234567890123456 is not a card",
    "0000000000 and 1111111111",
    "version 2.4.1 build 20260923",
    "قیمت \u06f4\u06f5,\u06f0\u06f0\u06f0,\u06f0\u06f0\u06f0 تومان",
    "the IBAN IR000190000000101234567890 is mistyped",
    "user@localhost is not a routable address",
    "\u06f9\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "x0012345679y",
    "00123456",
    "واتساپ +\u06f9\u06f8\u06f9\u06f1\u06f9\u06f8\u06f7\u06f6\u06f5\u06f4\u06f3\u06f2\u06f1",
    (
        "کد پستی را بعدا می\u200cگویم\u061b فعلا مبلغ"
        " \u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7"
        "\u06f8\u06f9\u06f0 ریال"
    ),
    (
        "سلام\u060c من علی هستم. کد ملی \u06f0\u06f0"
        "\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7"
        "\u06f9 و موبایل \u06f0\u06f9\u06f1\u06f2 "
        "\u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9."
        " ایمیل ali.rezaei@example.com است و دوباره "
        "\u06f0\u06f9\u06f1\u06f2\u06f3\u06f4\u06f5"
        "\u06f6\u06f7\u06f8\u06f9 را تکرار می\u200cکن"
        "م."
    ),
    (
        "شماره \u06f0\u06f9\u06f1\u06f2\u06f3\u06f4"
        "\u06f5\u06f6\u06f7\u06f8\u06f9 و 09123456789"
        " و 09351234567"
    ),
    "باز هم 09351234567 و 0012345679",
    "email a@b.io and national id 0012345679",
    "سلام\u060c کد ملی من \u06f0\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f9 هست",
    "کد ملیم 2746593815 \u0640ه",
    "my national id is 2746593815",
    "با شماره \u06f0\u06f9\u06f1\u06f2\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7 تماس بگیرید",
    "شماره همراه \u06f0\u06f9\u06f3\u06f5-\u06f1\u06f2\u06f3-\u06f4\u06f5\u06f6\u06f7",
    "واتساپ +\u06f9\u06f8\u06f9\u06f1\u06f9\u06f8\u06f7\u06f6\u06f5\u06f4\u06f3\u06f2",
    "office line 021 2233 4455",
    "تلفن شعبه اصفهان \u06f0\u06f3\u06f1\u06f3\u06f6\u06f6\u06f5\u06f4\u06f4\u06f3\u06f2",
    "reach me on +44 20 7946 0958",
    (
        "شماره کارت \u06f6\u06f0\u06f3\u06f7\u06f9"
        "\u06f9\u06f1\u06f1\u06f2\u06f3\u06f4\u06f5"
        "\u06f6\u06f7\u06f8\u06f6 رو یادداشت کن"
    ),
    "card number 6274-1234-5678-9011",
    "شبای حساب IR630190000000101234567890 است",
    "ایمیل من reza.m@example.org است",
    "send it to billing@shop.example.ir please",
    "کد پستی منزل \u06f8\u06f1\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9\u06f0 هست",
    (
        "اطلاعات: موبایل \u06f0\u06f9\u06f1\u06f2"
        "\u06f3\u06f3\u06f3\u06f4\u06f4\u06f5\u06f5 ا"
        "یمیل n.a@x.com کد ملی 0012345679"
    ),
    (
        "جلسه ساعت \u06f1\u06f4:\u06f0\u06f0 روز "
        "\u06f1\u06f4\u06f0\u06f3/\u06f0\u06f7/\u06f0"
        "\u06f1 برگزار شد"
    ),
    (
        "فاکتور شماره \u06f9\u06f8\u06f7\u06f6\u06f5 "
        "به مبلغ \u06f1\u06f2\u06f0\u06f0\u06f0\u06f0"
        "\u06f0\u06f0\u06f0 ریال"
    ),
    "کد رهگیری \u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9\u06f0\u06f1\u06f2 صادر شد",
    "we shipped 1500 units in Q3 2026",
    "قرارداد \u06f1\u06f4\u06f0\u06f2-\u06f1\u06f1\u06f2\u06f3 تمدید شد",
    "the ticket is SUP-2026-0042",
)
VOICEMAIL_SAMPLES: tuple[str, ...] = (
    "ش\u0643ا\u064aت  دارم",
    "شکایت دارم",
    "کار نمی\u200cکند",
    "\u06f0\u06f9\u06f1\u06f2",
    "فورا\u064b!",
    "URGENT, Please",
    "سلام\u060c از خدمات شما خیلی ناراضی هستم و شکایت دارم",
    "سلام\u060c قیمت پکیج سازمانی چنده\u061f می\u200cخواستم خرید کنم",
    "اینترنت ما از صبح قطع است و کار نمی\u200cکند",
    "می\u200cخواستم برای هفته بعد نوبت رزرو کنم",
    "لطفا\u064b با این شماره تماس بگیرید",
    "فاکتور ماه قبل دوبار از کارت کسر شده",
    "تبریک! شما در قرعه کشی برنده شدید",
    "Hi, I am very unhappy, this is unacceptable, I want to file a complaint",
    "Hello, how much is the premium plan? I'd like a quote",
    "Please call me back when you get this",
    "I was charged twice on my last invoice",
    "I'd like to reschedule my appointment",
    "Just wanted to say hello to the team",
    "من ش\u0643ا\u064aت دارم و ناراض\u064a هستم",
    "I was charged on the invoice and want a refund for the delivery",
    "",
    "   ",
    "\u2026",
    "ا\u0650",
    "uh",
    "?!",
    "این فوری است! از خدمات شما ناراضی هستم و می\u200cخواهم لغو قرارداد کنم",
    "Please call me back",
    "urgent, please call me back asap",
    "urgent please call me back asap",
    "hello team",
    "urgent! you have won a free gift, press 1",
    "سلام\u060c فاکتور من اشتباه است\u060c لطفا تماس بگیرید",
    "برای جایزه عدد \u06f1 را بزنید",
    "برای جایزه عدد 1 را بزنید",
    "برای جایزه عدد \u0661 را بزنید",
    "کار نمیکند و وصل نمی\u200cشه\u060c خیلی ضروری",
    "صورت\u200cحساب این ماه \u06f2 بار کسر شده",
    "\ufeb7\ufedc\ufe8e\ufef3\ufe96 دارم",
)
LOCATION_SAMPLES: tuple[str, ...] = (
    "سا\u0643ن \u0643رج\u060c پلا\u0643 \u06f4\u06f5 و خرم\u200c\u0622باد",
    "ته\u0640\u0640\u0640ران",
    "ش\u0650یراز",
    "سلام\u060c از اصفهان زنگ می\u200cزنم",
    "من سا\u0643ن تهران هستم",
    "شهر کاشان\u060c استان اصفهان",
    "در ش\u064aراز نمایندگی دارید\u061f",
    "خرم\u200c\u0622باد هستم",
    "خرم\u0622باد هستم",
    "از بندر عباس تماس می\u200cگیرم",
    "بندرعباس",
    "I am calling from Mashhad",
    "customer in tabriz",
    "نور\u0622باد فارس",
    "نور\u0622باد لرستان",
    "\u0622ستانه اشرفیه هستم",
    "میدان ونک تهران",
    "سا\u0643ن \u0643رج هستم",
    "استان فارس هستم",
    "اصفهان",
    "از نور\u0622باد تماس می\u200cگیرم",
    "از \u0622ذربایجان هستم",
    "خراسان",
    "\u0622ستانه",
    "تهران یا کرج\u060c فرقی نمی\u200cکند",
    "خیابان شیراز\u060c تهران",
    (
        "سا\u0643ن تهران\u060c منطقه \u06f3\u060c خ"
        "\u064aابان ولیعصر\u060c کوچه \u06f1\u06f2"
        "\u060c پلاک \u06f4\u06f5\u060c کد پستی "
        "\u06f1\u06f4\u06f3\u06f5\u06f6-\u06f7\u06f8"
        "\u06f9\u06f1\u06f3"
    ),
    "میدان \u0622زادی",
    "بلوار کشاورز",
    "بزرگراه همت",
    "near Valiasr St",
    "موقعیتم \u06f3\u06f5\u066b\u06f7\u06f0\u06f1, \u06f5\u06f1\u066b\u06f4\u06f0\u06f1 است",
    "95.1234, 51.4000",
    "کد پستی \u06f0\u06f0\u06f0\u06f0\u06f0\u06f0\u06f0\u06f0\u06f0\u06f0",
    "سلام وقت بخیر",
    "نور\u0622باد",
    "سلام",
    "از کرج",
    "استان اصفهان",
    "خ\u064aابان  ول\u064a\u200cعصر\u060c پلا\u0643 \u06f1\u06f2",
    "موقعیتم \u06f3\u06f5\u066b\u06f7\u06f0\u06f1\u060c \u06f5\u06f1\u066b\u06f4\u06f0\u06f1 است",
    "خان\u06c0 ما در مشهد است",
    "م\u0624سسه در قم",
    "کد پستی \u06f1\u06f4\u06f3\u06f5\u06f6\u06f7\u06f8\u06f9\u06f1\u06f3 در اصفهان",
)

_PERSIAN_FROM_ASCII = str.maketrans(
    "0123456789", "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
)
_ARABIC_INDIC_FROM_ANY = str.maketrans(
    "0123456789\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669" * 2,
)
_FULLWIDTH_FROM_ASCII = str.maketrans(
    "0123456789", "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19"
)
_ARABIC_LETTERS_FROM_PERSIAN = str.maketrans({"\u06cc": "\u064a", "\u06a9": "\u0643"})

VARIANTS = {
    "as_is": lambda text: text,
    "persian_digits": lambda text: text.translate(_PERSIAN_FROM_ASCII),
    "arabic_indic_digits": lambda text: text.translate(_ARABIC_INDIC_FROM_ANY),
    "fullwidth_digits": lambda text: text.translate(_FULLWIDTH_FROM_ASCII),
    "arabic_letters": lambda text: text.translate(_ARABIC_LETTERS_FROM_PERSIAN),
    "zwnj": lambda text: text.replace(" ", "\u200c"),
    "diacritics": lambda text: re.sub("([\u0631\u062a\u0633])", "\\1\u064e", text),
    "tatweel": lambda text: re.sub(
        "([\u0628\u062a\u0633\u0634\u0647])(?=[\u0627-\u06cc])", "\\1\u0640", text
    ),
    "marks": lambda text: "\u200f"
    + text.replace("\u0627", "\u0627\u0670").replace("\u0645", "\u0645\u06e1")
    + "\u200e",
}
PII_VARIANTS = (
    "as_is",
    "persian_digits",
    "arabic_indic_digits",
    "fullwidth_digits",
    "arabic_letters",
    "zwnj",
    "marks",
)
VOICEMAIL_VARIANTS = (
    "as_is",
    "arabic_letters",
    "zwnj",
    "diacritics",
    "tatweel",
    "marks",
    "persian_digits",
    "arabic_indic_digits",
    "fullwidth_digits",
)
LOCATION_VARIANTS = (
    "as_is",
    "arabic_letters",
    "zwnj",
    "diacritics",
    "tatweel",
    "marks",
    "persian_digits",
    "arabic_indic_digits",
)


def _plain(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _plain(value.as_dict())
    if isinstance(value, dict):
        return {str(_plain(key)): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, enum.Enum):
        return value.value
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return repr(value)


def _canonical_digest(value) -> str:
    text = json.dumps(_plain(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:20]


def _pii_observation(text: str):
    from backend.privacy.text_pii import detect, redact

    spans = detect(text)
    redacted, replacements = redact(text)
    assert [(r.start, r.end, r.pii_class) for r in replacements] == [
        (s.start, s.end, s.pii_class) for s in spans
    ]
    assert all(r.value == text[r.start : r.end] for r in replacements)
    if not spans:
        assert redacted == text
        return None
    rows = tuple(
        (s.start, s.end, s.pii_class.value, s.value, r.placeholder)
        for s, r in zip(spans, replacements, strict=True)
    )
    return rows, redacted


def _voicemail_observation(text: str):
    from backend.voicemail.classify import (
        ClassificationInput,
        PriorityContext,
        RuleClassifier,
        prioritize,
    )

    result = RuleClassifier().classify(ClassificationInput(text=text))
    default = prioritize(text, result, PriorityContext())
    loud = prioritize(text, result, PriorityContext(recent_calls_from_caller=2, caller_is_vip=True))
    digest = _canonical_digest([result, default, loud])
    return result.category.value, result.confidence, default.score, loud.score, digest


def _location_observation(text: str):
    from backend.locations.extract import extract_locations

    result = extract_locations(text)
    payload = {
        "candidates": [candidate.as_dict() for candidate in result.candidates],
        "status": result.status,
        "resolved": result.resolved.as_dict() if result.resolved else None,
        "clarification": result.clarification,
        "postal_code": result.postal_code,
        "landmarks": result.landmarks,
    }
    spans = tuple((c.type, c.start, c.end) for c in result.candidates)
    return result.status, spans, _canonical_digest(payload)


PII_EXPECTED: dict = {
    ("as_is", 0): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 1): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 2): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 3): (
        ((7, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 4): (((7, 18, "phone", "09123456789", "[PHONE_1]"),), "موبایل [PHONE_1]"),
    ("as_is", 5): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "موبایل [PHONE_1]"),
    ("as_is", 6): (((7, 23, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]."),
    ("as_is", 7): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]"),
    ("as_is", 8): (((7, 24, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]"),
    ("as_is", 9): (((5, 17, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("as_is", 10): (((5, 20, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("as_is", 11): (((5, 20, "phone", "0012025550143", "[PHONE_1]"),), "call [PHONE_1] today"),
    ("as_is", 12): (
        ((5, 21, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "card [BANK_CARD_1]",
    ),
    ("as_is", 13): (
        ((5, 24, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "کارت [BANK_CARD_1]",
    ),
    ("as_is", 14): (((4, 30, "iban", "IR630190000000101234567890", "[IBAN_1]"),), "شبا [IBAN_1]"),
    ("as_is", 15): (((4, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),), "شبا [IBAN_1]"),
    ("as_is", 16): (((5, 31, "iban", "IR630190000000101234567890", "[IBAN_1]"),), "iban [IBAN_1]"),
    ("as_is", 17): (
        ((5, 33, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "mail [EMAIL_1] now",
    ),
    ("as_is", 18): (
        ((7, 30, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "ایمیل: [EMAIL_1]",
    ),
    ("as_is", 19): (
        ((8, 18, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "کد پستی [IR_POSTAL_CODE_1] است",
    ),
    ("as_is", 20): (
        ((13, 24, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "postal code: [IR_POSTAL_CODE_1]",
    ),
    ("as_is", 21): (
        ((7, 17, "ir_postal_code", "0012345679", "[IR_POSTAL_CODE_1]"),),
        "کدپستی [IR_POSTAL_CODE_1]",
    ),
    ("as_is", 22): None,
    ("as_is", 23): None,
    ("as_is", 24): None,
    ("as_is", 25): None,
    ("as_is", 26): None,
    ("as_is", 27): None,
    ("as_is", 28): None,
    ("as_is", 29): None,
    ("as_is", 30): None,
    ("as_is", 31): None,
    ("as_is", 32): None,
    ("as_is", 33): None,
    ("as_is", 34): None,
    ("as_is", 35): None,
    ("as_is", 36): (
        (
            (26, 36, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (46, 59, "phone", "09123456789", "[PHONE_1]"),
            (67, 89, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (103, 114, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "سلام\u060c من علی هستم. کد ملی [IR_NATIONAL_"
            "ID_1] و موبایل [PHONE_1]. ایمیل [EMAIL_1] اس"
            "ت و دوباره [PHONE_1] را تکرار می\u200cکنم."
        ),
    ),
    ("as_is", 37): (
        (
            (6, 17, "phone", "09123456789", "[PHONE_1]"),
            (20, 31, "phone", "09123456789", "[PHONE_1]"),
            (34, 45, "phone", "09351234567", "[PHONE_2]"),
        ),
        "شماره [PHONE_1] و [PHONE_1] و [PHONE_2]",
    ),
    ("as_is", 38): (
        (
            (7, 18, "phone", "09351234567", "[PHONE_1]"),
            (21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "باز هم [PHONE_1] و [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 39): (
        (
            (6, 12, "email", "a@b.io", "[EMAIL_1]"),
            (29, 39, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "email [EMAIL_1] and national id [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 40): (
        ((16, 26, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "سلام\u060c کد ملی من [IR_NATIONAL_ID_1] هست",
    ),
    ("as_is", 41): (
        ((8, 18, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "کد ملیم [IR_NATIONAL_ID_1] \u0640ه",
    ),
    ("as_is", 42): (
        ((18, 28, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "my national id is [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 43): (
        ((9, 20, "phone", "09121234567", "[PHONE_1]"),),
        "با شماره [PHONE_1] تماس بگیرید",
    ),
    ("as_is", 44): (((12, 25, "phone", "09351234567", "[PHONE_1]"),), "شماره همراه [PHONE_1]"),
    ("as_is", 45): (((7, 20, "phone", "09198765432", "[PHONE_1]"),), "واتساپ [PHONE_1]"),
    ("as_is", 46): (((12, 25, "phone", "02122334455", "[PHONE_1]"),), "office line [PHONE_1]"),
    ("as_is", 47): (((17, 28, "phone", "03136654432", "[PHONE_1]"),), "تلفن شعبه اصفهان [PHONE_1]"),
    ("as_is", 48): (((12, 28, "phone", "00442079460958", "[PHONE_1]"),), "reach me on [PHONE_1]"),
    ("as_is", 49): (
        ((11, 27, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "شماره کارت [BANK_CARD_1] رو یادداشت کن",
    ),
    ("as_is", 50): (
        ((12, 31, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "card number [BANK_CARD_1]",
    ),
    ("as_is", 51): (
        ((10, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبای حساب [IBAN_1] است",
    ),
    ("as_is", 52): (
        ((9, 27, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "ایمیل من [EMAIL_1] است",
    ),
    ("as_is", 53): (
        ((11, 34, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "send it to [EMAIL_1] please",
    ),
    ("as_is", 54): (
        ((13, 23, "ir_postal_code", "8134567890", "[IR_POSTAL_CODE_1]"),),
        "کد پستی منزل [IR_POSTAL_CODE_1] هست",
    ),
    ("as_is", 55): (
        (
            (16, 27, "phone", "09123334455", "[PHONE_1]"),
            (34, 43, "email", "n.a@x.com", "[EMAIL_1]"),
            (51, 61, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "اطلاعات: موبایل [PHONE_1] ایمیل [EMAIL_1] کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("as_is", 56): None,
    ("as_is", 57): None,
    ("as_is", 58): None,
    ("as_is", 59): None,
    ("as_is", 60): None,
    ("as_is", 61): None,
    ("persian_digits", 0): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 1): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 2): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 3): (
        ((7, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 4): (((7, 18, "phone", "09123456789", "[PHONE_1]"),), "موبایل [PHONE_1]"),
    ("persian_digits", 5): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "موبایل [PHONE_1]"),
    ("persian_digits", 6): (((7, 23, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]."),
    ("persian_digits", 7): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]"),
    ("persian_digits", 8): (((7, 24, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]"),
    ("persian_digits", 9): (((5, 17, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("persian_digits", 10): (((5, 20, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("persian_digits", 11): (
        ((5, 20, "phone", "0012025550143", "[PHONE_1]"),),
        "call [PHONE_1] today",
    ),
    ("persian_digits", 12): (
        ((5, 21, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "card [BANK_CARD_1]",
    ),
    ("persian_digits", 13): (
        ((5, 24, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "کارت [BANK_CARD_1]",
    ),
    ("persian_digits", 14): (
        ((4, 30, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("persian_digits", 15): (
        ((4, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("persian_digits", 16): (
        ((5, 31, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "iban [IBAN_1]",
    ),
    ("persian_digits", 17): (
        ((5, 33, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "mail [EMAIL_1] now",
    ),
    ("persian_digits", 18): (
        ((7, 30, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "ایمیل: [EMAIL_1]",
    ),
    ("persian_digits", 19): (
        ((8, 18, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "کد پستی [IR_POSTAL_CODE_1] است",
    ),
    ("persian_digits", 20): (
        ((13, 24, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "postal code: [IR_POSTAL_CODE_1]",
    ),
    ("persian_digits", 21): (
        ((7, 17, "ir_postal_code", "0012345679", "[IR_POSTAL_CODE_1]"),),
        "کدپستی [IR_POSTAL_CODE_1]",
    ),
    ("persian_digits", 22): None,
    ("persian_digits", 23): None,
    ("persian_digits", 24): None,
    ("persian_digits", 25): None,
    ("persian_digits", 26): None,
    ("persian_digits", 27): None,
    ("persian_digits", 28): None,
    ("persian_digits", 29): None,
    ("persian_digits", 30): None,
    ("persian_digits", 31): None,
    ("persian_digits", 32): None,
    ("persian_digits", 33): None,
    ("persian_digits", 34): None,
    ("persian_digits", 35): None,
    ("persian_digits", 36): (
        (
            (26, 36, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (46, 59, "phone", "09123456789", "[PHONE_1]"),
            (67, 89, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (103, 114, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "سلام\u060c من علی هستم. کد ملی [IR_NATIONAL_"
            "ID_1] و موبایل [PHONE_1]. ایمیل [EMAIL_1] اس"
            "ت و دوباره [PHONE_1] را تکرار می\u200cکنم."
        ),
    ),
    ("persian_digits", 37): (
        (
            (6, 17, "phone", "09123456789", "[PHONE_1]"),
            (20, 31, "phone", "09123456789", "[PHONE_1]"),
            (34, 45, "phone", "09351234567", "[PHONE_2]"),
        ),
        "شماره [PHONE_1] و [PHONE_1] و [PHONE_2]",
    ),
    ("persian_digits", 38): (
        (
            (7, 18, "phone", "09351234567", "[PHONE_1]"),
            (21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "باز هم [PHONE_1] و [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 39): (
        (
            (6, 12, "email", "a@b.io", "[EMAIL_1]"),
            (29, 39, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "email [EMAIL_1] and national id [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 40): (
        ((16, 26, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "سلام\u060c کد ملی من [IR_NATIONAL_ID_1] هست",
    ),
    ("persian_digits", 41): (
        ((8, 18, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "کد ملیم [IR_NATIONAL_ID_1] \u0640ه",
    ),
    ("persian_digits", 42): (
        ((18, 28, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "my national id is [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 43): (
        ((9, 20, "phone", "09121234567", "[PHONE_1]"),),
        "با شماره [PHONE_1] تماس بگیرید",
    ),
    ("persian_digits", 44): (
        ((12, 25, "phone", "09351234567", "[PHONE_1]"),),
        "شماره همراه [PHONE_1]",
    ),
    ("persian_digits", 45): (((7, 20, "phone", "09198765432", "[PHONE_1]"),), "واتساپ [PHONE_1]"),
    ("persian_digits", 46): (
        ((12, 25, "phone", "02122334455", "[PHONE_1]"),),
        "office line [PHONE_1]",
    ),
    ("persian_digits", 47): (
        ((17, 28, "phone", "03136654432", "[PHONE_1]"),),
        "تلفن شعبه اصفهان [PHONE_1]",
    ),
    ("persian_digits", 48): (
        ((12, 28, "phone", "00442079460958", "[PHONE_1]"),),
        "reach me on [PHONE_1]",
    ),
    ("persian_digits", 49): (
        ((11, 27, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "شماره کارت [BANK_CARD_1] رو یادداشت کن",
    ),
    ("persian_digits", 50): (
        ((12, 31, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "card number [BANK_CARD_1]",
    ),
    ("persian_digits", 51): (
        ((10, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبای حساب [IBAN_1] است",
    ),
    ("persian_digits", 52): (
        ((9, 27, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "ایمیل من [EMAIL_1] است",
    ),
    ("persian_digits", 53): (
        ((11, 34, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "send it to [EMAIL_1] please",
    ),
    ("persian_digits", 54): (
        ((13, 23, "ir_postal_code", "8134567890", "[IR_POSTAL_CODE_1]"),),
        "کد پستی منزل [IR_POSTAL_CODE_1] هست",
    ),
    ("persian_digits", 55): (
        (
            (16, 27, "phone", "09123334455", "[PHONE_1]"),
            (34, 43, "email", "n.a@x.com", "[EMAIL_1]"),
            (51, 61, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "اطلاعات: موبایل [PHONE_1] ایمیل [EMAIL_1] کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("persian_digits", 56): None,
    ("persian_digits", 57): None,
    ("persian_digits", 58): None,
    ("persian_digits", 59): None,
    ("persian_digits", 60): None,
    ("persian_digits", 61): None,
    ("arabic_indic_digits", 0): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 1): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 2): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 3): (
        ((7, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 4): (
        ((7, 18, "phone", "09123456789", "[PHONE_1]"),),
        "موبایل [PHONE_1]",
    ),
    ("arabic_indic_digits", 5): (
        ((7, 20, "phone", "09123456789", "[PHONE_1]"),),
        "موبایل [PHONE_1]",
    ),
    ("arabic_indic_digits", 6): (
        ((7, 23, "phone", "09123456789", "[PHONE_1]"),),
        "mobile [PHONE_1].",
    ),
    ("arabic_indic_digits", 7): (
        ((7, 20, "phone", "09123456789", "[PHONE_1]"),),
        "mobile [PHONE_1]",
    ),
    ("arabic_indic_digits", 8): (
        ((7, 24, "phone", "09123456789", "[PHONE_1]"),),
        "mobile [PHONE_1]",
    ),
    ("arabic_indic_digits", 9): (((5, 17, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("arabic_indic_digits", 10): (
        ((5, 20, "phone", "02188776655", "[PHONE_1]"),),
        "تلفن [PHONE_1]",
    ),
    ("arabic_indic_digits", 11): (
        ((5, 20, "phone", "0012025550143", "[PHONE_1]"),),
        "call [PHONE_1] today",
    ),
    ("arabic_indic_digits", 12): (
        ((5, 21, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "card [BANK_CARD_1]",
    ),
    ("arabic_indic_digits", 13): (
        ((5, 24, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "کارت [BANK_CARD_1]",
    ),
    ("arabic_indic_digits", 14): (
        ((4, 30, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("arabic_indic_digits", 15): (
        ((4, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("arabic_indic_digits", 16): (
        ((5, 31, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "iban [IBAN_1]",
    ),
    ("arabic_indic_digits", 17): (
        ((5, 33, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "mail [EMAIL_1] now",
    ),
    ("arabic_indic_digits", 18): (
        ((7, 30, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "ایمیل: [EMAIL_1]",
    ),
    ("arabic_indic_digits", 19): (
        ((8, 18, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "کد پستی [IR_POSTAL_CODE_1] است",
    ),
    ("arabic_indic_digits", 20): (
        ((13, 24, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "postal code: [IR_POSTAL_CODE_1]",
    ),
    ("arabic_indic_digits", 21): (
        ((7, 17, "ir_postal_code", "0012345679", "[IR_POSTAL_CODE_1]"),),
        "کدپستی [IR_POSTAL_CODE_1]",
    ),
    ("arabic_indic_digits", 22): None,
    ("arabic_indic_digits", 23): None,
    ("arabic_indic_digits", 24): None,
    ("arabic_indic_digits", 25): None,
    ("arabic_indic_digits", 26): None,
    ("arabic_indic_digits", 27): None,
    ("arabic_indic_digits", 28): None,
    ("arabic_indic_digits", 29): None,
    ("arabic_indic_digits", 30): None,
    ("arabic_indic_digits", 31): None,
    ("arabic_indic_digits", 32): None,
    ("arabic_indic_digits", 33): None,
    ("arabic_indic_digits", 34): None,
    ("arabic_indic_digits", 35): None,
    ("arabic_indic_digits", 36): (
        (
            (26, 36, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (46, 59, "phone", "09123456789", "[PHONE_1]"),
            (67, 89, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (103, 114, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "سلام\u060c من علی هستم. کد ملی [IR_NATIONAL_"
            "ID_1] و موبایل [PHONE_1]. ایمیل [EMAIL_1] اس"
            "ت و دوباره [PHONE_1] را تکرار می\u200cکنم."
        ),
    ),
    ("arabic_indic_digits", 37): (
        (
            (6, 17, "phone", "09123456789", "[PHONE_1]"),
            (20, 31, "phone", "09123456789", "[PHONE_1]"),
            (34, 45, "phone", "09351234567", "[PHONE_2]"),
        ),
        "شماره [PHONE_1] و [PHONE_1] و [PHONE_2]",
    ),
    ("arabic_indic_digits", 38): (
        (
            (7, 18, "phone", "09351234567", "[PHONE_1]"),
            (21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "باز هم [PHONE_1] و [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 39): (
        (
            (6, 12, "email", "a@b.io", "[EMAIL_1]"),
            (29, 39, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "email [EMAIL_1] and national id [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 40): (
        ((16, 26, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "سلام\u060c کد ملی من [IR_NATIONAL_ID_1] هست",
    ),
    ("arabic_indic_digits", 41): (
        ((8, 18, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "کد ملیم [IR_NATIONAL_ID_1] \u0640ه",
    ),
    ("arabic_indic_digits", 42): (
        ((18, 28, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "my national id is [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 43): (
        ((9, 20, "phone", "09121234567", "[PHONE_1]"),),
        "با شماره [PHONE_1] تماس بگیرید",
    ),
    ("arabic_indic_digits", 44): (
        ((12, 25, "phone", "09351234567", "[PHONE_1]"),),
        "شماره همراه [PHONE_1]",
    ),
    ("arabic_indic_digits", 45): (
        ((7, 20, "phone", "09198765432", "[PHONE_1]"),),
        "واتساپ [PHONE_1]",
    ),
    ("arabic_indic_digits", 46): (
        ((12, 25, "phone", "02122334455", "[PHONE_1]"),),
        "office line [PHONE_1]",
    ),
    ("arabic_indic_digits", 47): (
        ((17, 28, "phone", "03136654432", "[PHONE_1]"),),
        "تلفن شعبه اصفهان [PHONE_1]",
    ),
    ("arabic_indic_digits", 48): (
        ((12, 28, "phone", "00442079460958", "[PHONE_1]"),),
        "reach me on [PHONE_1]",
    ),
    ("arabic_indic_digits", 49): (
        ((11, 27, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "شماره کارت [BANK_CARD_1] رو یادداشت کن",
    ),
    ("arabic_indic_digits", 50): (
        ((12, 31, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "card number [BANK_CARD_1]",
    ),
    ("arabic_indic_digits", 51): (
        ((10, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبای حساب [IBAN_1] است",
    ),
    ("arabic_indic_digits", 52): (
        ((9, 27, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "ایمیل من [EMAIL_1] است",
    ),
    ("arabic_indic_digits", 53): (
        ((11, 34, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "send it to [EMAIL_1] please",
    ),
    ("arabic_indic_digits", 54): (
        ((13, 23, "ir_postal_code", "8134567890", "[IR_POSTAL_CODE_1]"),),
        "کد پستی منزل [IR_POSTAL_CODE_1] هست",
    ),
    ("arabic_indic_digits", 55): (
        (
            (16, 27, "phone", "09123334455", "[PHONE_1]"),
            (34, 43, "email", "n.a@x.com", "[EMAIL_1]"),
            (51, 61, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "اطلاعات: موبایل [PHONE_1] ایمیل [EMAIL_1] کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("arabic_indic_digits", 56): None,
    ("arabic_indic_digits", 57): None,
    ("arabic_indic_digits", 58): None,
    ("arabic_indic_digits", 59): None,
    ("arabic_indic_digits", 60): None,
    ("arabic_indic_digits", 61): None,
    ("fullwidth_digits", 0): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 1): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 2): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 3): (
        ((7, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 4): (((7, 18, "phone", "09123456789", "[PHONE_1]"),), "موبایل [PHONE_1]"),
    ("fullwidth_digits", 5): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "موبایل [PHONE_1]"),
    ("fullwidth_digits", 6): (
        ((7, 23, "phone", "09123456789", "[PHONE_1]"),),
        "mobile [PHONE_1].",
    ),
    ("fullwidth_digits", 7): (
        ((7, 20, "phone", "09123456789", "[PHONE_1]"),),
        "mobile [PHONE_1]",
    ),
    ("fullwidth_digits", 8): (
        ((7, 24, "phone", "09123456789", "[PHONE_1]"),),
        "mobile [PHONE_1]",
    ),
    ("fullwidth_digits", 9): (((5, 17, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("fullwidth_digits", 10): (((5, 20, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("fullwidth_digits", 11): (
        ((5, 20, "phone", "0012025550143", "[PHONE_1]"),),
        "call [PHONE_1] today",
    ),
    ("fullwidth_digits", 12): (
        ((5, 21, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "card [BANK_CARD_1]",
    ),
    ("fullwidth_digits", 13): (
        ((5, 24, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "کارت [BANK_CARD_1]",
    ),
    ("fullwidth_digits", 14): (
        ((4, 30, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("fullwidth_digits", 15): (
        ((4, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("fullwidth_digits", 16): (
        ((5, 31, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "iban [IBAN_1]",
    ),
    ("fullwidth_digits", 17): (
        ((5, 33, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "mail [EMAIL_1] now",
    ),
    ("fullwidth_digits", 18): (
        ((7, 30, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "ایمیل: [EMAIL_1]",
    ),
    ("fullwidth_digits", 19): (
        ((8, 18, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "کد پستی [IR_POSTAL_CODE_1] است",
    ),
    ("fullwidth_digits", 20): (
        ((13, 24, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "postal code: [IR_POSTAL_CODE_1]",
    ),
    ("fullwidth_digits", 21): (
        ((7, 17, "ir_postal_code", "0012345679", "[IR_POSTAL_CODE_1]"),),
        "کدپستی [IR_POSTAL_CODE_1]",
    ),
    ("fullwidth_digits", 22): None,
    ("fullwidth_digits", 23): None,
    ("fullwidth_digits", 24): None,
    ("fullwidth_digits", 25): None,
    ("fullwidth_digits", 26): None,
    ("fullwidth_digits", 27): None,
    ("fullwidth_digits", 28): None,
    ("fullwidth_digits", 29): None,
    ("fullwidth_digits", 30): None,
    ("fullwidth_digits", 31): None,
    ("fullwidth_digits", 32): None,
    ("fullwidth_digits", 33): None,
    ("fullwidth_digits", 34): None,
    ("fullwidth_digits", 35): None,
    ("fullwidth_digits", 36): (
        (
            (26, 36, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (46, 59, "phone", "09123456789", "[PHONE_1]"),
            (67, 89, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (103, 114, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "سلام\u060c من علی هستم. کد ملی [IR_NATIONAL_"
            "ID_1] و موبایل [PHONE_1]. ایمیل [EMAIL_1] اس"
            "ت و دوباره [PHONE_1] را تکرار می\u200cکنم."
        ),
    ),
    ("fullwidth_digits", 37): (
        (
            (6, 17, "phone", "09123456789", "[PHONE_1]"),
            (20, 31, "phone", "09123456789", "[PHONE_1]"),
            (34, 45, "phone", "09351234567", "[PHONE_2]"),
        ),
        "شماره [PHONE_1] و [PHONE_1] و [PHONE_2]",
    ),
    ("fullwidth_digits", 38): (
        (
            (7, 18, "phone", "09351234567", "[PHONE_1]"),
            (21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "باز هم [PHONE_1] و [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 39): (
        (
            (6, 12, "email", "a@b.io", "[EMAIL_1]"),
            (29, 39, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "email [EMAIL_1] and national id [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 40): (
        ((16, 26, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "سلام\u060c کد ملی من [IR_NATIONAL_ID_1] هست",
    ),
    ("fullwidth_digits", 41): (
        ((8, 18, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "کد ملیم [IR_NATIONAL_ID_1] ـه",
    ),
    ("fullwidth_digits", 42): (
        ((18, 28, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "my national id is [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 43): (
        ((9, 20, "phone", "09121234567", "[PHONE_1]"),),
        "با شماره [PHONE_1] تماس بگیرید",
    ),
    ("fullwidth_digits", 44): (
        ((12, 25, "phone", "09351234567", "[PHONE_1]"),),
        "شماره همراه [PHONE_1]",
    ),
    ("fullwidth_digits", 45): (((7, 20, "phone", "09198765432", "[PHONE_1]"),), "واتساپ [PHONE_1]"),
    ("fullwidth_digits", 46): (
        ((12, 25, "phone", "02122334455", "[PHONE_1]"),),
        "office line [PHONE_1]",
    ),
    ("fullwidth_digits", 47): (
        ((17, 28, "phone", "03136654432", "[PHONE_1]"),),
        "تلفن شعبه اصفهان [PHONE_1]",
    ),
    ("fullwidth_digits", 48): (
        ((12, 28, "phone", "00442079460958", "[PHONE_1]"),),
        "reach me on [PHONE_1]",
    ),
    ("fullwidth_digits", 49): (
        ((11, 27, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "شماره کارت [BANK_CARD_1] رو یادداشت کن",
    ),
    ("fullwidth_digits", 50): (
        ((12, 31, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "card number [BANK_CARD_1]",
    ),
    ("fullwidth_digits", 51): (
        ((10, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبای حساب [IBAN_1] است",
    ),
    ("fullwidth_digits", 52): (
        ((9, 27, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "ایمیل من [EMAIL_1] است",
    ),
    ("fullwidth_digits", 53): (
        ((11, 34, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "send it to [EMAIL_1] please",
    ),
    ("fullwidth_digits", 54): (
        ((13, 23, "ir_postal_code", "8134567890", "[IR_POSTAL_CODE_1]"),),
        "کد پستی منزل [IR_POSTAL_CODE_1] هست",
    ),
    ("fullwidth_digits", 55): (
        (
            (16, 27, "phone", "09123334455", "[PHONE_1]"),
            (34, 43, "email", "n.a@x.com", "[EMAIL_1]"),
            (51, 61, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "اطلاعات: موبایل [PHONE_1] ایمیل [EMAIL_1] کد ملی [IR_NATIONAL_ID_1]",
    ),
    ("fullwidth_digits", 56): None,
    ("fullwidth_digits", 57): None,
    ("fullwidth_digits", 58): None,
    ("fullwidth_digits", 59): None,
    ("fullwidth_digits", 60): None,
    ("fullwidth_digits", 61): None,
    ("arabic_letters", 0): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u0643د مل\u064a [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 1): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u0643د مل\u064a [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 2): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u0643د مل\u064a [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 3): (
        ((7, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u0643د مل\u064a [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 4): (
        ((7, 18, "phone", "09123456789", "[PHONE_1]"),),
        "موبا\u064aل [PHONE_1]",
    ),
    ("arabic_letters", 5): (
        ((7, 20, "phone", "09123456789", "[PHONE_1]"),),
        "موبا\u064aل [PHONE_1]",
    ),
    ("arabic_letters", 6): (((7, 23, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]."),
    ("arabic_letters", 7): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]"),
    ("arabic_letters", 8): (((7, 24, "phone", "09123456789", "[PHONE_1]"),), "mobile [PHONE_1]"),
    ("arabic_letters", 9): (((5, 17, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("arabic_letters", 10): (((5, 20, "phone", "02188776655", "[PHONE_1]"),), "تلفن [PHONE_1]"),
    ("arabic_letters", 11): (
        ((5, 20, "phone", "0012025550143", "[PHONE_1]"),),
        "call [PHONE_1] today",
    ),
    ("arabic_letters", 12): (
        ((5, 21, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "card [BANK_CARD_1]",
    ),
    ("arabic_letters", 13): (
        ((5, 24, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "\u0643ارت [BANK_CARD_1]",
    ),
    ("arabic_letters", 14): (
        ((4, 30, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("arabic_letters", 15): (
        ((4, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا [IBAN_1]",
    ),
    ("arabic_letters", 16): (
        ((5, 31, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "iban [IBAN_1]",
    ),
    ("arabic_letters", 17): (
        ((5, 33, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "mail [EMAIL_1] now",
    ),
    ("arabic_letters", 18): (
        ((7, 30, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "ا\u064aم\u064aل: [EMAIL_1]",
    ),
    # HOM-164 (text-pii/2): the Arabic-letter postal keyword now names the number.
    ("arabic_letters", 19): (
        ((8, 18, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "\u0643د پست\u064a [IR_POSTAL_CODE_1] است",
    ),
    ("arabic_letters", 20): (
        ((13, 24, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "postal code: [IR_POSTAL_CODE_1]",
    ),
    ("arabic_letters", 21): (
        ((7, 17, "ir_postal_code", "0012345679", "[IR_POSTAL_CODE_1]"),),
        "\u0643دپست\u064a [IR_POSTAL_CODE_1]",
    ),
    ("arabic_letters", 22): None,
    ("arabic_letters", 23): None,
    ("arabic_letters", 24): None,
    ("arabic_letters", 25): None,
    ("arabic_letters", 26): None,
    ("arabic_letters", 27): None,
    ("arabic_letters", 28): None,
    ("arabic_letters", 29): None,
    ("arabic_letters", 30): None,
    ("arabic_letters", 31): None,
    ("arabic_letters", 32): None,
    ("arabic_letters", 33): None,
    ("arabic_letters", 34): None,
    ("arabic_letters", 35): None,
    ("arabic_letters", 36): (
        (
            (26, 36, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (46, 59, "phone", "09123456789", "[PHONE_1]"),
            (67, 89, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (103, 114, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "سلام\u060c من عل\u064a هستم. \u0643د مل"
            "\u064a [IR_NATIONAL_ID_1] و موبا\u064aل [PHO"
            "NE_1]. ا\u064aم\u064aل [EMAIL_1] است و دوبار"
            "ه [PHONE_1] را ت\u0643رار م\u064a\u200c"
            "\u0643نم."
        ),
    ),
    ("arabic_letters", 37): (
        (
            (6, 17, "phone", "09123456789", "[PHONE_1]"),
            (20, 31, "phone", "09123456789", "[PHONE_1]"),
            (34, 45, "phone", "09351234567", "[PHONE_2]"),
        ),
        "شماره [PHONE_1] و [PHONE_1] و [PHONE_2]",
    ),
    ("arabic_letters", 38): (
        (
            (7, 18, "phone", "09351234567", "[PHONE_1]"),
            (21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "باز هم [PHONE_1] و [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 39): (
        (
            (6, 12, "email", "a@b.io", "[EMAIL_1]"),
            (29, 39, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "email [EMAIL_1] and national id [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 40): (
        ((16, 26, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "سلام\u060c \u0643د مل\u064a من [IR_NATIONAL_ID_1] هست",
    ),
    ("arabic_letters", 41): (
        ((8, 18, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "\u0643د مل\u064aم [IR_NATIONAL_ID_1] \u0640ه",
    ),
    ("arabic_letters", 42): (
        ((18, 28, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "my national id is [IR_NATIONAL_ID_1]",
    ),
    ("arabic_letters", 43): (
        ((9, 20, "phone", "09121234567", "[PHONE_1]"),),
        "با شماره [PHONE_1] تماس بگ\u064aر\u064aد",
    ),
    ("arabic_letters", 44): (
        ((12, 25, "phone", "09351234567", "[PHONE_1]"),),
        "شماره همراه [PHONE_1]",
    ),
    ("arabic_letters", 45): (((7, 20, "phone", "09198765432", "[PHONE_1]"),), "واتساپ [PHONE_1]"),
    ("arabic_letters", 46): (
        ((12, 25, "phone", "02122334455", "[PHONE_1]"),),
        "office line [PHONE_1]",
    ),
    ("arabic_letters", 47): (
        ((17, 28, "phone", "03136654432", "[PHONE_1]"),),
        "تلفن شعبه اصفهان [PHONE_1]",
    ),
    ("arabic_letters", 48): (
        ((12, 28, "phone", "00442079460958", "[PHONE_1]"),),
        "reach me on [PHONE_1]",
    ),
    ("arabic_letters", 49): (
        ((11, 27, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "شماره \u0643ارت [BANK_CARD_1] رو \u064aادداشت \u0643ن",
    ),
    ("arabic_letters", 50): (
        ((12, 31, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "card number [BANK_CARD_1]",
    ),
    ("arabic_letters", 51): (
        ((10, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا\u064a حساب [IBAN_1] است",
    ),
    ("arabic_letters", 52): (
        ((9, 27, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "ا\u064aم\u064aل من [EMAIL_1] است",
    ),
    ("arabic_letters", 53): (
        ((11, 34, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "send it to [EMAIL_1] please",
    ),
    ("arabic_letters", 54): (
        ((13, 23, "ir_postal_code", "8134567890", "[IR_POSTAL_CODE_1]"),),
        "\u0643د پست\u064a منزل [IR_POSTAL_CODE_1] هست",
    ),
    ("arabic_letters", 55): (
        (
            (16, 27, "phone", "09123334455", "[PHONE_1]"),
            (34, 43, "email", "n.a@x.com", "[EMAIL_1]"),
            (51, 61, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        (
            "اطلاعات: موبا\u064aل [PHONE_1] ا\u064aم"
            "\u064aل [EMAIL_1] \u0643د مل\u064a [IR_NATIO"
            "NAL_ID_1]"
        ),
    ),
    ("arabic_letters", 56): None,
    ("arabic_letters", 57): None,
    ("arabic_letters", 58): None,
    ("arabic_letters", 59): None,
    ("arabic_letters", 60): None,
    ("arabic_letters", 61): None,
    ("zwnj", 0): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد\u200cملی\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 1): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد\u200cملی\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 2): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد\u200cملی\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 3): (
        ((7, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کد\u200cملی\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 4): (((7, 18, "phone", "09123456789", "[PHONE_1]"),), "موبایل\u200c[PHONE_1]"),
    ("zwnj", 5): None,
    ("zwnj", 6): None,
    ("zwnj", 7): (((7, 20, "phone", "09123456789", "[PHONE_1]"),), "mobile\u200c[PHONE_1]"),
    ("zwnj", 8): (((7, 24, "phone", "09123456789", "[PHONE_1]"),), "mobile\u200c[PHONE_1]"),
    ("zwnj", 9): (((5, 17, "phone", "02188776655", "[PHONE_1]"),), "تلفن\u200c[PHONE_1]"),
    ("zwnj", 10): None,
    ("zwnj", 11): None,
    ("zwnj", 12): (
        ((5, 21, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "card\u200c[BANK_CARD_1]",
    ),
    ("zwnj", 13): None,
    ("zwnj", 14): (
        ((4, 30, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبا\u200c[IBAN_1]",
    ),
    ("zwnj", 15): None,
    ("zwnj", 16): (
        ((5, 31, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "iban\u200c[IBAN_1]",
    ),
    ("zwnj", 17): (
        ((5, 33, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "mail\u200c[EMAIL_1]\u200cnow",
    ),
    ("zwnj", 18): (
        ((7, 30, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "ایمیل:\u200c[EMAIL_1]",
    ),
    ("zwnj", 19): None,
    ("zwnj", 20): None,
    ("zwnj", 21): (
        ((7, 17, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "کدپستی\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 22): None,
    ("zwnj", 23): None,
    ("zwnj", 24): None,
    ("zwnj", 25): None,
    ("zwnj", 26): None,
    ("zwnj", 27): None,
    ("zwnj", 28): None,
    ("zwnj", 29): None,
    ("zwnj", 30): None,
    ("zwnj", 31): None,
    ("zwnj", 32): None,
    ("zwnj", 33): None,
    ("zwnj", 34): None,
    ("zwnj", 35): None,
    ("zwnj", 36): (
        (
            (26, 36, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (67, 89, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (103, 114, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "سلام\u060c\u200cمن\u200cعلی\u200cهستم.\u200c"
            "کد\u200cملی\u200c[IR_NATIONAL_ID_1]\u200cو"
            "\u200cموبایل\u200c\u06f0\u06f9\u06f1\u06f2"
            "\u200c\u06f3\u06f4\u06f5\u200c\u06f6\u06f7"
            "\u06f8\u06f9.\u200cایمیل\u200c[EMAIL_1]"
            "\u200cاست\u200cو\u200cدوباره\u200c[PHONE_1]"
            "\u200cرا\u200cتکرار\u200cمی\u200cکنم."
        ),
    ),
    ("zwnj", 37): (
        (
            (6, 17, "phone", "09123456789", "[PHONE_1]"),
            (20, 31, "phone", "09123456789", "[PHONE_1]"),
            (34, 45, "phone", "09351234567", "[PHONE_2]"),
        ),
        "شماره\u200c[PHONE_1]\u200cو\u200c[PHONE_1]\u200cو\u200c[PHONE_2]",
    ),
    ("zwnj", 38): (
        (
            (7, 18, "phone", "09351234567", "[PHONE_1]"),
            (21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "باز\u200cهم\u200c[PHONE_1]\u200cو\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 39): (
        (
            (6, 12, "email", "a@b.io", "[EMAIL_1]"),
            (29, 39, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "email\u200c[EMAIL_1]\u200cand\u200cnational\u200cid\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 40): (
        ((16, 26, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "سلام\u060c\u200cکد\u200cملی\u200cمن\u200c[IR_NATIONAL_ID_1]\u200cهست",
    ),
    ("zwnj", 41): (
        ((8, 18, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "کد\u200cملیم\u200c[IR_NATIONAL_ID_1]\u200c\u0640ه",
    ),
    ("zwnj", 42): (
        ((18, 28, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "my\u200cnational\u200cid\u200cis\u200c[IR_NATIONAL_ID_1]",
    ),
    ("zwnj", 43): (
        ((9, 20, "phone", "09121234567", "[PHONE_1]"),),
        "با\u200cشماره\u200c[PHONE_1]\u200cتماس\u200cبگیرید",
    ),
    ("zwnj", 44): (
        ((12, 25, "phone", "09351234567", "[PHONE_1]"),),
        "شماره\u200cهمراه\u200c[PHONE_1]",
    ),
    ("zwnj", 45): (((7, 20, "phone", "09198765432", "[PHONE_1]"),), "واتساپ\u200c[PHONE_1]"),
    ("zwnj", 46): None,
    ("zwnj", 47): (
        ((17, 28, "phone", "03136654432", "[PHONE_1]"),),
        "تلفن\u200cشعبه\u200cاصفهان\u200c[PHONE_1]",
    ),
    ("zwnj", 48): None,
    ("zwnj", 49): (
        ((11, 27, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "شماره\u200cکارت\u200c[BANK_CARD_1]\u200cرو\u200cیادداشت\u200cکن",
    ),
    ("zwnj", 50): (
        ((12, 31, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "card\u200cnumber\u200c[BANK_CARD_1]",
    ),
    ("zwnj", 51): (
        ((10, 36, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "شبای\u200cحساب\u200c[IBAN_1]\u200cاست",
    ),
    ("zwnj", 52): (
        ((9, 27, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "ایمیل\u200cمن\u200c[EMAIL_1]\u200cاست",
    ),
    ("zwnj", 53): (
        ((11, 34, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "send\u200cit\u200cto\u200c[EMAIL_1]\u200cplease",
    ),
    ("zwnj", 54): None,
    ("zwnj", 55): (
        (
            (16, 27, "phone", "09123334455", "[PHONE_1]"),
            (34, 43, "email", "n.a@x.com", "[EMAIL_1]"),
            (51, 61, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        (
            "اطلاعات:\u200cموبایل\u200c[PHONE_1]\u200cایم"
            "یل\u200c[EMAIL_1]\u200cکد\u200cملی\u200c[IR_"
            "NATIONAL_ID_1]"
        ),
    ),
    ("zwnj", 56): None,
    ("zwnj", 57): None,
    ("zwnj", 58): None,
    ("zwnj", 59): None,
    ("zwnj", 60): None,
    ("zwnj", 61): None,
    ("marks", 0): (
        ((9, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u200fکد م\u06e1لی [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 1): (
        ((9, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u200fکد م\u06e1لی [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 2): (
        ((9, 19, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u200fکد م\u06e1لی [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 3): (
        ((9, 21, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u200fکد م\u06e1لی [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 4): (
        ((10, 21, "phone", "09123456789", "[PHONE_1]"),),
        "\u200fم\u06e1وبا\u0670یل [PHONE_1]\u200e",
    ),
    ("marks", 5): (
        ((10, 23, "phone", "09123456789", "[PHONE_1]"),),
        "\u200fم\u06e1وبا\u0670یل [PHONE_1]\u200e",
    ),
    ("marks", 6): (
        ((8, 24, "phone", "09123456789", "[PHONE_1]"),),
        "\u200fmobile [PHONE_1].\u200e",
    ),
    ("marks", 7): (((8, 21, "phone", "09123456789", "[PHONE_1]"),), "\u200fmobile [PHONE_1]\u200e"),
    ("marks", 8): (((8, 25, "phone", "09123456789", "[PHONE_1]"),), "\u200fmobile [PHONE_1]\u200e"),
    ("marks", 9): (((6, 18, "phone", "02188776655", "[PHONE_1]"),), "\u200fتلفن [PHONE_1]\u200e"),
    ("marks", 10): (((6, 21, "phone", "02188776655", "[PHONE_1]"),), "\u200fتلفن [PHONE_1]\u200e"),
    ("marks", 11): (
        ((6, 21, "phone", "0012025550143", "[PHONE_1]"),),
        "\u200fcall [PHONE_1] today\u200e",
    ),
    ("marks", 12): (
        ((6, 22, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "\u200fcard [BANK_CARD_1]\u200e",
    ),
    ("marks", 13): (
        ((7, 26, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "\u200fکا\u0670رت [BANK_CARD_1]\u200e",
    ),
    ("marks", 14): (
        ((6, 32, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "\u200fشبا\u0670 [IBAN_1]\u200e",
    ),
    ("marks", 15): (
        ((6, 38, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "\u200fشبا\u0670 [IBAN_1]\u200e",
    ),
    ("marks", 16): (
        ((6, 32, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "\u200fiban [IBAN_1]\u200e",
    ),
    ("marks", 17): (
        ((6, 34, "email", "ali.rezaei+crm@example.co.ir", "[EMAIL_1]"),),
        "\u200fmail [EMAIL_1] now\u200e",
    ),
    ("marks", 18): (
        ((10, 33, "email", "sara_k@mail.example.com", "[EMAIL_1]"),),
        "\u200fا\u0670یم\u06e1یل: [EMAIL_1]\u200e",
    ),
    ("marks", 19): (
        ((9, 19, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "\u200fکد پستی [IR_POSTAL_CODE_1] ا\u0670ست\u200e",
    ),
    ("marks", 20): (
        ((14, 25, "ir_postal_code", "1435678912", "[IR_POSTAL_CODE_1]"),),
        "\u200fpostal code: [IR_POSTAL_CODE_1]\u200e",
    ),
    ("marks", 21): (
        ((8, 18, "ir_postal_code", "0012345679", "[IR_POSTAL_CODE_1]"),),
        "\u200fکدپستی [IR_POSTAL_CODE_1]\u200e",
    ),
    ("marks", 22): None,
    ("marks", 23): None,
    ("marks", 24): None,
    ("marks", 25): None,
    ("marks", 26): None,
    ("marks", 27): None,
    ("marks", 28): None,
    ("marks", 29): None,
    ("marks", 30): None,
    ("marks", 31): None,
    ("marks", 32): None,
    ("marks", 33): None,
    ("marks", 34): None,
    ("marks", 35): None,
    ("marks", 36): (
        (
            (32, 42, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
            (54, 67, "phone", "09123456789", "[PHONE_1]"),
            (77, 99, "email", "ali.rezaei@example.com", "[EMAIL_1]"),
            (115, 126, "phone", "09123456789", "[PHONE_1]"),
        ),
        (
            "\u200fسلا\u0670م\u06e1\u060c م\u06e1ن علی هس"
            "تم\u06e1. کد م\u06e1لی [IR_NATIONAL_ID_1] و "
            "م\u06e1وبا\u0670یل [PHONE_1]. ا\u0670یم"
            "\u06e1یل [EMAIL_1] ا\u0670ست و دوبا\u0670ره "
            "[PHONE_1] را\u0670 تکرا\u0670ر م\u06e1ی"
            "\u200cکنم\u06e1.\u200e"
        ),
    ),
    ("marks", 37): (
        (
            (9, 20, "phone", "09123456789", "[PHONE_1]"),
            (23, 34, "phone", "09123456789", "[PHONE_1]"),
            (37, 48, "phone", "09351234567", "[PHONE_2]"),
        ),
        "\u200fشم\u06e1ا\u0670ره [PHONE_1] و [PHONE_1] و [PHONE_2]\u200e",
    ),
    ("marks", 38): (
        (
            (10, 21, "phone", "09351234567", "[PHONE_1]"),
            (24, 34, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "\u200fبا\u0670ز هم\u06e1 [PHONE_1] و [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 39): (
        (
            (7, 13, "email", "a@b.io", "[EMAIL_1]"),
            (30, 40, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        "\u200femail [EMAIL_1] and national id [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 40): (
        ((21, 31, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),),
        "\u200fسلا\u0670م\u06e1\u060c کد م\u06e1لی م\u06e1ن [IR_NATIONAL_ID_1] هست\u200e",
    ),
    ("marks", 41): (
        ((11, 21, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "\u200fکد م\u06e1لیم\u06e1 [IR_NATIONAL_ID_1] \u0640ه\u200e",
    ),
    ("marks", 42): (
        ((19, 29, "ir_national_id", "2746593815", "[IR_NATIONAL_ID_1]"),),
        "\u200fmy national id is [IR_NATIONAL_ID_1]\u200e",
    ),
    ("marks", 43): (
        ((13, 24, "phone", "09121234567", "[PHONE_1]"),),
        "\u200fبا\u0670 شم\u06e1ا\u0670ره [PHONE_1] تم\u06e1ا\u0670س بگیرید\u200e",
    ),
    ("marks", 44): (
        ((17, 30, "phone", "09351234567", "[PHONE_1]"),),
        "\u200fشم\u06e1ا\u0670ره هم\u06e1را\u0670ه [PHONE_1]\u200e",
    ),
    ("marks", 45): (
        ((10, 23, "phone", "09198765432", "[PHONE_1]"),),
        "\u200fوا\u0670تسا\u0670پ [PHONE_1]\u200e",
    ),
    ("marks", 46): (
        ((13, 26, "phone", "02122334455", "[PHONE_1]"),),
        "\u200foffice line [PHONE_1]\u200e",
    ),
    ("marks", 47): (
        ((20, 31, "phone", "03136654432", "[PHONE_1]"),),
        "\u200fتلفن شعبه ا\u0670صفها\u0670ن [PHONE_1]\u200e",
    ),
    ("marks", 48): (
        ((13, 29, "phone", "00442079460958", "[PHONE_1]"),),
        "\u200freach me on [PHONE_1]\u200e",
    ),
    ("marks", 49): (
        ((15, 31, "bank_card", "6037991123456786", "[BANK_CARD_1]"),),
        "\u200fشم\u06e1ا\u0670ره کا\u0670رت [BANK_CARD_1] رو یا\u0670ددا\u0670شت کن\u200e",
    ),
    ("marks", 50): (
        ((13, 32, "bank_card", "6274123456789011", "[BANK_CARD_1]"),),
        "\u200fcard number [BANK_CARD_1]\u200e",
    ),
    ("marks", 51): (
        ((13, 39, "iban", "IR630190000000101234567890", "[IBAN_1]"),),
        "\u200fشبا\u0670ی حسا\u0670ب [IBAN_1] ا\u0670ست\u200e",
    ),
    ("marks", 52): (
        ((13, 31, "email", "reza.m@example.org", "[EMAIL_1]"),),
        "\u200fا\u0670یم\u06e1یل م\u06e1ن [EMAIL_1] ا\u0670ست\u200e",
    ),
    ("marks", 53): (
        ((12, 35, "email", "billing@shop.example.ir", "[EMAIL_1]"),),
        "\u200fsend it to [EMAIL_1] please\u200e",
    ),
    ("marks", 54): (
        ((15, 25, "ir_postal_code", "8134567890", "[IR_POSTAL_CODE_1]"),),
        "\u200fکد پستی م\u06e1نزل [IR_POSTAL_CODE_1] هست\u200e",
    ),
    ("marks", 55): (
        (
            (22, 33, "phone", "09123334455", "[PHONE_1]"),
            (42, 51, "email", "n.a@x.com", "[EMAIL_1]"),
            (60, 70, "ir_national_id", "0012345679", "[IR_NATIONAL_ID_1]"),
        ),
        (
            "\u200fا\u0670طلا\u0670عا\u0670ت: م\u06e1وبا"
            "\u0670یل [PHONE_1] ا\u0670یم\u06e1یل [EMAIL_"
            "1] کد م\u06e1لی [IR_NATIONAL_ID_1]\u200e"
        ),
    ),
    ("marks", 56): None,
    ("marks", 57): None,
    ("marks", 58): None,
    ("marks", 59): None,
    ("marks", 60): None,
    ("marks", 61): None,
}
VOICEMAIL_EXPECTED: dict = {
    ("as_is", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("as_is", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("as_is", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("as_is", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("as_is", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("as_is", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("as_is", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("as_is", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("as_is", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("as_is", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("as_is", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("as_is", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("as_is", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("as_is", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("as_is", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("as_is", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("as_is", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("as_is", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("as_is", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("as_is", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("as_is", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("as_is", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("as_is", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("as_is", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("as_is", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("as_is", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("as_is", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("as_is", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("as_is", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("as_is", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("as_is", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("as_is", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("as_is", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("as_is", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("as_is", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("as_is", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("as_is", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("as_is", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("as_is", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("as_is", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("arabic_letters", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("arabic_letters", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("arabic_letters", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("arabic_letters", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("arabic_letters", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("arabic_letters", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("arabic_letters", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("arabic_letters", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("arabic_letters", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("arabic_letters", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("arabic_letters", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("arabic_letters", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("arabic_letters", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("arabic_letters", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("arabic_letters", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("arabic_letters", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("arabic_letters", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("arabic_letters", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("arabic_letters", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("arabic_letters", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("arabic_letters", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("arabic_letters", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_letters", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_letters", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_letters", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_letters", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_letters", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_letters", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("arabic_letters", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("arabic_letters", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("arabic_letters", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("arabic_letters", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("arabic_letters", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("arabic_letters", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("arabic_letters", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("arabic_letters", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("arabic_letters", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("arabic_letters", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("arabic_letters", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("arabic_letters", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("zwnj", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("zwnj", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("zwnj", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("zwnj", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("zwnj", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("zwnj", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("zwnj", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("zwnj", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("zwnj", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("zwnj", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("zwnj", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("zwnj", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("zwnj", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("zwnj", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("zwnj", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("zwnj", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("zwnj", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("zwnj", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("zwnj", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("zwnj", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("zwnj", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("zwnj", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("zwnj", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("zwnj", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("zwnj", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("zwnj", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("zwnj", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("zwnj", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("zwnj", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("zwnj", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("zwnj", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("zwnj", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("zwnj", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("zwnj", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("zwnj", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("zwnj", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("zwnj", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("zwnj", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("zwnj", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("zwnj", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("diacritics", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("diacritics", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("diacritics", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("diacritics", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("diacritics", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("diacritics", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("diacritics", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("diacritics", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("diacritics", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("diacritics", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("diacritics", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("diacritics", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("diacritics", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("diacritics", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("diacritics", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("diacritics", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("diacritics", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("diacritics", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("diacritics", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("diacritics", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("diacritics", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("diacritics", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("diacritics", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("diacritics", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("diacritics", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("diacritics", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("diacritics", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("diacritics", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("diacritics", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("diacritics", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("diacritics", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("diacritics", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("diacritics", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("diacritics", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("diacritics", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("diacritics", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("diacritics", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("diacritics", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("diacritics", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("diacritics", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("tatweel", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("tatweel", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("tatweel", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("tatweel", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("tatweel", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("tatweel", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("tatweel", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("tatweel", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("tatweel", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("tatweel", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("tatweel", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("tatweel", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("tatweel", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("tatweel", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("tatweel", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("tatweel", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("tatweel", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("tatweel", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("tatweel", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("tatweel", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("tatweel", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("tatweel", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("tatweel", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("tatweel", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("tatweel", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("tatweel", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("tatweel", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("tatweel", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("tatweel", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("tatweel", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("tatweel", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("tatweel", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("tatweel", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("tatweel", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("tatweel", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("tatweel", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("tatweel", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("tatweel", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("tatweel", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("tatweel", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("marks", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("marks", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("marks", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("marks", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("marks", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("marks", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("marks", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("marks", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("marks", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("marks", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("marks", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("marks", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("marks", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("marks", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("marks", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("marks", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("marks", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("marks", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("marks", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("marks", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("marks", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("marks", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("marks", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("marks", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("marks", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("marks", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("marks", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("marks", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("marks", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("marks", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("marks", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("marks", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("marks", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("marks", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("marks", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("marks", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("marks", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("marks", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("marks", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("marks", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("persian_digits", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("persian_digits", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("persian_digits", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("persian_digits", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("persian_digits", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("persian_digits", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("persian_digits", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("persian_digits", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("persian_digits", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("persian_digits", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("persian_digits", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("persian_digits", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("persian_digits", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("persian_digits", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("persian_digits", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("persian_digits", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("persian_digits", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("persian_digits", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("persian_digits", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("persian_digits", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("persian_digits", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("persian_digits", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("persian_digits", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("persian_digits", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("persian_digits", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("persian_digits", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("persian_digits", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("persian_digits", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("persian_digits", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("persian_digits", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("persian_digits", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("persian_digits", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("persian_digits", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("persian_digits", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("persian_digits", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("persian_digits", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("persian_digits", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("persian_digits", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("persian_digits", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("persian_digits", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("arabic_indic_digits", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("arabic_indic_digits", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("arabic_indic_digits", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("arabic_indic_digits", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("arabic_indic_digits", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("arabic_indic_digits", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("arabic_indic_digits", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("arabic_indic_digits", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("arabic_indic_digits", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("arabic_indic_digits", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("arabic_indic_digits", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("arabic_indic_digits", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("arabic_indic_digits", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("arabic_indic_digits", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("arabic_indic_digits", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("arabic_indic_digits", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("arabic_indic_digits", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("arabic_indic_digits", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("arabic_indic_digits", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("arabic_indic_digits", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("arabic_indic_digits", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("arabic_indic_digits", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_indic_digits", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_indic_digits", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_indic_digits", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_indic_digits", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_indic_digits", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("arabic_indic_digits", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("arabic_indic_digits", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("arabic_indic_digits", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("arabic_indic_digits", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("arabic_indic_digits", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("arabic_indic_digits", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("arabic_indic_digits", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("arabic_indic_digits", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("arabic_indic_digits", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("arabic_indic_digits", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("arabic_indic_digits", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("arabic_indic_digits", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("arabic_indic_digits", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("fullwidth_digits", 0): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("fullwidth_digits", 1): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
    ("fullwidth_digits", 2): ("support", 1.0, 25, 75, "db53f8e816116d3deaea"),
    ("fullwidth_digits", 3): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("fullwidth_digits", 4): ("other", 0.0, 45, 95, "83d7acc9e73909415488"),
    ("fullwidth_digits", 5): ("other", 0.0, 45, 95, "15781c5413f0e7815957"),
    ("fullwidth_digits", 6): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("fullwidth_digits", 7): ("sales_inquiry", 1.0, 20, 70, "d25b43b0094f22444b24"),
    ("fullwidth_digits", 8): ("support", 1.0, 25, 75, "47c6948b25da0eac299f"),
    ("fullwidth_digits", 9): ("appointment", 1.0, 15, 65, "4362fdce56c8faedda3d"),
    ("fullwidth_digits", 10): ("callback_request", 1.0, 20, 70, "c089c38c62be3250de73"),
    ("fullwidth_digits", 11): ("billing", 1.0, 25, 75, "b74225b95b6083fa4813"),
    ("fullwidth_digits", 12): ("spam", 1.0, 0, 0, "f70680c24c1dfaf6b83e"),
    ("fullwidth_digits", 13): ("complaint", 1.0, 45, 95, "bdb20ae6d68aad2e3df4"),
    ("fullwidth_digits", 14): ("sales_inquiry", 1.0, 20, 70, "79fa70924c10221d12ca"),
    ("fullwidth_digits", 15): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("fullwidth_digits", 16): ("billing", 1.0, 25, 75, "68efa642cd561d092a5d"),
    ("fullwidth_digits", 17): ("appointment", 1.0, 15, 65, "cc560ee37f9750edfe0d"),
    ("fullwidth_digits", 18): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("fullwidth_digits", 19): ("complaint", 1.0, 45, 95, "5c5d41c64cfbbc1c9717"),
    ("fullwidth_digits", 20): ("billing", 0.667, 25, 75, "d6aaff60d068c916b27c"),
    ("fullwidth_digits", 21): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("fullwidth_digits", 22): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("fullwidth_digits", 23): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("fullwidth_digits", 24): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("fullwidth_digits", 25): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("fullwidth_digits", 26): ("empty", None, 0, 0, "491164777a442101a47c"),
    ("fullwidth_digits", 27): ("complaint", 1.0, 100, 100, "832939c2433686901597"),
    ("fullwidth_digits", 28): ("callback_request", 1.0, 20, 70, "aa1780e09803f2815b98"),
    ("fullwidth_digits", 29): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("fullwidth_digits", 30): ("callback_request", 1.0, 55, 100, "778390bc06fc9ad985d9"),
    ("fullwidth_digits", 31): ("other", 0.0, 10, 60, "81a4eedb7ceb521f9c40"),
    ("fullwidth_digits", 32): ("spam", 1.0, 0, 0, "71ff26f9a4d865052d5c"),
    ("fullwidth_digits", 33): ("billing", 0.5, 25, 75, "f437e2a29794125134e4"),
    ("fullwidth_digits", 34): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("fullwidth_digits", 35): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("fullwidth_digits", 36): ("spam", 1.0, 0, 0, "a5a3c95a03955fb16f3a"),
    ("fullwidth_digits", 37): ("support", 1.0, 60, 100, "30bae9fefd246085d930"),
    ("fullwidth_digits", 38): ("billing", 1.0, 25, 75, "84b8099ee500197c8808"),
    ("fullwidth_digits", 39): ("complaint", 1.0, 45, 95, "3a9c4b27e195cc865170"),
}
LOCATION_EXPECTED: dict = {
    ("as_is", 0): (
        "ambiguous",
        (("city", 5, 8), ("plate", 10, 17), ("city", 20, 28)),
        "0c7defd452071f796432",
    ),
    ("as_is", 1): ("resolved", (("city", 0, 8),), "7f71e4f4dc87d529236e"),
    ("as_is", 2): ("resolved", (("city", 0, 6),), "3735f6885c9e6a72fd5e"),
    ("as_is", 3): ("resolved", (("city", 9, 15),), "ba78850486051557e3df"),
    ("as_is", 4): ("resolved", (("city", 8, 13),), "66ba5aa95fdf0b31327f"),
    ("as_is", 5): ("resolved", (("city", 0, 9), ("province", 11, 23)), "4ac5ae4a20d0c3927257"),
    ("as_is", 6): ("resolved", (("city", 3, 8),), "a8a95772c3f3361733f8"),
    ("as_is", 7): ("resolved", (("city", 0, 8),), "725aad9eb3b29a9b840b"),
    ("as_is", 8): ("resolved", (("city", 0, 7),), "ddb7292270f997d8f902"),
    ("as_is", 9): ("resolved", (("city", 3, 12),), "2977b79d2c6a82cd8a59"),
    ("as_is", 10): ("resolved", (("city", 0, 8),), "db89d0506d9f650ffe6a"),
    ("as_is", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("as_is", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("as_is", 13): ("resolved", (("city", 0, 7), ("province", 8, 12)), "b8bc7a216ba4df60077a"),
    ("as_is", 14): ("resolved", (("city", 0, 7), ("province", 8, 14)), "3065bd57248114ac87b7"),
    ("as_is", 15): ("resolved", (("city", 0, 13),), "21c4c7f7e3dc98214119"),
    ("as_is", 16): ("resolved", (("landmark", 0, 9), ("city", 10, 15)), "c03f7470a41c26c69ad7"),
    ("as_is", 17): ("resolved", (("city", 5, 8),), "cb9ac52c7c55d9185e54"),
    ("as_is", 18): ("resolved", (("province", 0, 10),), "cd662954ecfe3e38bfe3"),
    ("as_is", 19): ("resolved", (("city", 0, 6),), "872becb262b528730494"),
    ("as_is", 20): ("ambiguous", (("city", 3, 10),), "49a97b1d24f6a9f934a7"),
    ("as_is", 21): ("ambiguous", (("region", 3, 12),), "6dc9d617f7584a37580f"),
    ("as_is", 22): ("ambiguous", (("region", 0, 6),), "157508ff0524be1a5762"),
    ("as_is", 23): ("ambiguous", (("city", 0, 6),), "9d8de8e715e7a0802e04"),
    ("as_is", 24): ("ambiguous", (("city", 0, 5), ("city", 9, 12)), "44531f08dcb1be39fe0f"),
    ("as_is", 25): ("resolved", (("landmark", 0, 12), ("city", 14, 19)), "2df645ae4b0d2301bf93"),
    ("as_is", 26): (
        "resolved",
        (
            ("city", 5, 10),
            ("district", 12, 19),
            ("landmark", 21, 34),
            ("landmark", 36, 43),
            ("plate", 45, 52),
            ("postal_code", 62, 73),
        ),
        "98f1af0a1f18a3d587ea",
    ),
    ("as_is", 27): ("unresolved", (("landmark", 0, 11),), "bd456d7f8637aa1103ea"),
    ("as_is", 28): ("unresolved", (("landmark", 0, 12),), "b34b8d321014c80c6387"),
    ("as_is", 29): ("unresolved", (("landmark", 0, 11),), "b4b0766f0409caeda1fd"),
    ("as_is", 30): ("unresolved", (("landmark", 5, 15),), "e62ec351c64060c6d7a2"),
    ("as_is", 31): ("resolved", (("coordinates", 8, 22),), "30365503698e76fddbd9"),
    ("as_is", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("as_is", 33): ("unresolved", (("postal_code", 8, 18),), "70bd699ba819d5d65766"),
    ("as_is", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("as_is", 35): ("ambiguous", (("city", 0, 7),), "28f5f5d52d5b6f5e5fbe"),
    ("as_is", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("as_is", 37): ("resolved", (("city", 3, 6),), "48b6f4b96138371382d4"),
    ("as_is", 38): ("resolved", (("province", 0, 12),), "84bbd65ff6e1b1983190"),
    ("as_is", 39): ("unresolved", (("landmark", 0, 15), ("plate", 17, 24)), "01aa92098f3cce3673f9"),
    ("as_is", 40): ("resolved", (("coordinates", 8, 22),), "22df4eaf63c075f624ff"),
    ("as_is", 41): ("resolved", (("city", 11, 15),), "266dffed6b11808b2e2c"),
    ("as_is", 42): ("resolved", (("city", 9, 11),), "d2e25b8fe82d417d3fd8"),
    ("as_is", 43): ("resolved", (("postal_code", 8, 18), ("city", 22, 28)), "666375fdb3adc02f98d4"),
    ("arabic_letters", 0): (
        "ambiguous",
        (("city", 5, 8), ("plate", 10, 17), ("city", 20, 28)),
        "0c7defd452071f796432",
    ),
    ("arabic_letters", 1): ("resolved", (("city", 0, 8),), "7f71e4f4dc87d529236e"),
    ("arabic_letters", 2): ("resolved", (("city", 0, 6),), "c0a152a395490fb8a160"),
    ("arabic_letters", 3): ("resolved", (("city", 9, 15),), "ba78850486051557e3df"),
    ("arabic_letters", 4): ("resolved", (("city", 8, 13),), "66ba5aa95fdf0b31327f"),
    ("arabic_letters", 5): (
        "resolved",
        (("city", 0, 9), ("province", 11, 23)),
        "5e23c6e25a59e9953a76",
    ),
    ("arabic_letters", 6): ("resolved", (("city", 3, 8),), "a8a95772c3f3361733f8"),
    ("arabic_letters", 7): ("resolved", (("city", 0, 8),), "725aad9eb3b29a9b840b"),
    ("arabic_letters", 8): ("resolved", (("city", 0, 7),), "ddb7292270f997d8f902"),
    ("arabic_letters", 9): ("resolved", (("city", 3, 12),), "2977b79d2c6a82cd8a59"),
    ("arabic_letters", 10): ("resolved", (("city", 0, 8),), "db89d0506d9f650ffe6a"),
    ("arabic_letters", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("arabic_letters", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("arabic_letters", 13): (
        "resolved",
        (("city", 0, 7), ("province", 8, 12)),
        "b8bc7a216ba4df60077a",
    ),
    ("arabic_letters", 14): (
        "resolved",
        (("city", 0, 7), ("province", 8, 14)),
        "3065bd57248114ac87b7",
    ),
    ("arabic_letters", 15): ("resolved", (("city", 0, 13),), "f372f3477c34d2f19dad"),
    ("arabic_letters", 16): (
        "resolved",
        (("landmark", 0, 9), ("city", 10, 15)),
        "f13400e4f0a8655c5629",
    ),
    ("arabic_letters", 17): ("resolved", (("city", 5, 8),), "cb9ac52c7c55d9185e54"),
    ("arabic_letters", 18): ("resolved", (("province", 0, 10),), "cd662954ecfe3e38bfe3"),
    ("arabic_letters", 19): ("resolved", (("city", 0, 6),), "872becb262b528730494"),
    ("arabic_letters", 20): ("ambiguous", (("city", 3, 10),), "49a97b1d24f6a9f934a7"),
    ("arabic_letters", 21): ("ambiguous", (("region", 3, 12),), "231091114180f244febd"),
    ("arabic_letters", 22): ("ambiguous", (("region", 0, 6),), "157508ff0524be1a5762"),
    ("arabic_letters", 23): ("ambiguous", (("city", 0, 6),), "9d8de8e715e7a0802e04"),
    ("arabic_letters", 24): (
        "ambiguous",
        (("city", 0, 5), ("city", 9, 12)),
        "05c4b3ed1dbb8e24a4eb",
    ),
    ("arabic_letters", 25): (
        "resolved",
        (("landmark", 0, 12), ("city", 14, 19)),
        "d2637e2c7f4027e3d17b",
    ),
    ("arabic_letters", 26): (
        "resolved",
        (
            ("city", 5, 10),
            ("district", 12, 19),
            ("landmark", 21, 34),
            ("landmark", 36, 43),
            ("plate", 45, 52),
            ("postal_code", 62, 73),
        ),
        "037286412346a954101d",
    ),
    ("arabic_letters", 27): ("unresolved", (("landmark", 0, 11),), "2fe4f629882f560cbd1e"),
    ("arabic_letters", 28): ("unresolved", (("landmark", 0, 12),), "17e2d03818b831e8c5e8"),
    ("arabic_letters", 29): ("unresolved", (("landmark", 0, 11),), "b4b0766f0409caeda1fd"),
    ("arabic_letters", 30): ("unresolved", (("landmark", 5, 15),), "e62ec351c64060c6d7a2"),
    ("arabic_letters", 31): ("resolved", (("coordinates", 8, 22),), "30365503698e76fddbd9"),
    ("arabic_letters", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("arabic_letters", 33): ("unresolved", (("postal_code", 8, 18),), "70bd699ba819d5d65766"),
    ("arabic_letters", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("arabic_letters", 35): ("ambiguous", (("city", 0, 7),), "28f5f5d52d5b6f5e5fbe"),
    ("arabic_letters", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("arabic_letters", 37): ("resolved", (("city", 3, 6),), "149dbf7ac26b2625fb38"),
    ("arabic_letters", 38): ("resolved", (("province", 0, 12),), "84bbd65ff6e1b1983190"),
    ("arabic_letters", 39): (
        "unresolved",
        (("landmark", 0, 15), ("plate", 17, 24)),
        "01aa92098f3cce3673f9",
    ),
    ("arabic_letters", 40): ("resolved", (("coordinates", 8, 22),), "22df4eaf63c075f624ff"),
    ("arabic_letters", 41): ("resolved", (("city", 11, 15),), "266dffed6b11808b2e2c"),
    ("arabic_letters", 42): ("resolved", (("city", 9, 11),), "d2e25b8fe82d417d3fd8"),
    ("arabic_letters", 43): (
        "resolved",
        (("postal_code", 8, 18), ("city", 22, 28)),
        "666375fdb3adc02f98d4",
    ),
    ("zwnj", 0): (
        "ambiguous",
        (("city", 5, 8), ("plate", 10, 17), ("city", 20, 28)),
        "c9e2892f518e5afb40c1",
    ),
    ("zwnj", 1): ("resolved", (("city", 0, 8),), "7f71e4f4dc87d529236e"),
    ("zwnj", 2): ("resolved", (("city", 0, 6),), "3735f6885c9e6a72fd5e"),
    ("zwnj", 3): ("resolved", (("city", 9, 15),), "ba78850486051557e3df"),
    ("zwnj", 4): ("resolved", (("city", 8, 13),), "66ba5aa95fdf0b31327f"),
    ("zwnj", 5): ("resolved", (("city", 0, 9), ("province", 11, 23)), "c68eb19bafb4d7a5c744"),
    ("zwnj", 6): ("resolved", (("city", 3, 8),), "a8a95772c3f3361733f8"),
    ("zwnj", 7): ("resolved", (("city", 0, 8),), "725aad9eb3b29a9b840b"),
    ("zwnj", 8): ("resolved", (("city", 0, 7),), "ddb7292270f997d8f902"),
    ("zwnj", 9): ("resolved", (("city", 3, 12),), "7d3e7f6383a11d98ec06"),
    ("zwnj", 10): ("resolved", (("city", 0, 8),), "db89d0506d9f650ffe6a"),
    ("zwnj", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("zwnj", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("zwnj", 13): ("resolved", (("city", 0, 7), ("province", 8, 12)), "b8bc7a216ba4df60077a"),
    ("zwnj", 14): ("resolved", (("city", 0, 7), ("province", 8, 14)), "3065bd57248114ac87b7"),
    ("zwnj", 15): ("resolved", (("city", 0, 13),), "cac8217f83fddb914592"),
    ("zwnj", 16): ("resolved", (("landmark", 0, 9), ("city", 10, 15)), "a52fff4dc9ef84a3bcca"),
    ("zwnj", 17): ("resolved", (("city", 5, 8),), "cb9ac52c7c55d9185e54"),
    ("zwnj", 18): ("resolved", (("province", 0, 10),), "75f6d0b3f005c9b21065"),
    ("zwnj", 19): ("resolved", (("city", 0, 6),), "872becb262b528730494"),
    ("zwnj", 20): ("ambiguous", (("city", 3, 10),), "49a97b1d24f6a9f934a7"),
    ("zwnj", 21): ("ambiguous", (("region", 3, 12),), "6dc9d617f7584a37580f"),
    ("zwnj", 22): ("ambiguous", (("region", 0, 6),), "157508ff0524be1a5762"),
    ("zwnj", 23): ("ambiguous", (("city", 0, 6),), "9d8de8e715e7a0802e04"),
    ("zwnj", 24): ("ambiguous", (("city", 0, 5), ("city", 9, 12)), "44531f08dcb1be39fe0f"),
    ("zwnj", 25): ("resolved", (("landmark", 0, 12), ("city", 14, 19)), "f44c19b28f1353562a8e"),
    ("zwnj", 26): (
        "resolved",
        (
            ("city", 5, 10),
            ("district", 12, 19),
            ("landmark", 21, 34),
            ("landmark", 36, 43),
            ("plate", 45, 52),
            ("postal_code", 62, 73),
        ),
        "33c8663f9391eccd0d07",
    ),
    ("zwnj", 27): ("unresolved", (("landmark", 0, 11),), "487fbe0c04129dceaf2a"),
    ("zwnj", 28): ("unresolved", (("landmark", 0, 12),), "29b921e5b840a515ef9c"),
    ("zwnj", 29): ("unresolved", (("landmark", 0, 11),), "c1dd36c5e2eaa9a02c33"),
    ("zwnj", 30): ("unresolved", (("landmark", 5, 15),), "4e0d44f36e534bd26272"),
    ("zwnj", 31): ("resolved", (("coordinates", 8, 22),), "a6fa1563e258d7770a9a"),
    ("zwnj", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("zwnj", 33): ("unresolved", (("postal_code", 8, 18),), "70bd699ba819d5d65766"),
    ("zwnj", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("zwnj", 35): ("ambiguous", (("city", 0, 7),), "28f5f5d52d5b6f5e5fbe"),
    ("zwnj", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("zwnj", 37): ("resolved", (("city", 3, 6),), "48b6f4b96138371382d4"),
    ("zwnj", 38): ("resolved", (("province", 0, 12),), "d7ed14eea42c14325d10"),
    ("zwnj", 39): ("unresolved", (("landmark", 0, 15), ("plate", 17, 24)), "01fd44caeab824f0ae9f"),
    ("zwnj", 40): ("resolved", (("coordinates", 8, 22),), "cdc717752d1848c6efd7"),
    ("zwnj", 41): ("resolved", (("city", 11, 15),), "266dffed6b11808b2e2c"),
    ("zwnj", 42): ("resolved", (("city", 9, 11),), "d2e25b8fe82d417d3fd8"),
    ("zwnj", 43): ("resolved", (("postal_code", 8, 18), ("city", 22, 28)), "666375fdb3adc02f98d4"),
    ("diacritics", 0): (
        "ambiguous",
        (("city", 6, 10), ("plate", 12, 19), ("city", 22, 31)),
        "2f54c5f404c1a24cdb3f",
    ),
    ("diacritics", 1): ("resolved", (("city", 0, 10),), "f84b239212761a81da6a"),
    ("diacritics", 2): ("resolved", (("city", 0, 7),), "21cba799f255fa0ab751"),
    ("diacritics", 3): ("resolved", (("city", 10, 16),), "a34ec101573a091a6b49"),
    ("diacritics", 4): ("resolved", (("city", 9, 16),), "1496085943567e49b71e"),
    ("diacritics", 5): (
        "resolved",
        (("city", 0, 10), ("province", 12, 26)),
        "59cfd725248ae33d86f7",
    ),
    ("diacritics", 6): ("resolved", (("city", 4, 10),), "18db99e45bbdf9ddae2b"),
    ("diacritics", 7): ("resolved", (("city", 0, 9),), "772d97904fa8ef475756"),
    ("diacritics", 8): ("resolved", (("city", 0, 8),), "4afee9234f6e57c382bd"),
    ("diacritics", 9): ("resolved", (("city", 3, 13),), "0541beade708f5dac479"),
    ("diacritics", 10): ("resolved", (("city", 0, 9),), "30cd34c3d9520ed76142"),
    ("diacritics", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("diacritics", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("diacritics", 13): ("resolved", (("city", 0, 8), ("province", 9, 14)), "15b505c417082c270abb"),
    ("diacritics", 14): ("resolved", (("city", 0, 8), ("province", 9, 18)), "ef4783368689f8cb1840"),
    ("diacritics", 15): ("resolved", (("city", 0, 16),), "0e5e1c4df4f75c4ceb17"),
    ("diacritics", 16): (
        "resolved",
        (("landmark", 0, 9), ("city", 10, 17)),
        "9cba073a14caa5a156e5",
    ),
    ("diacritics", 17): ("resolved", (("city", 6, 10),), "497188e90cbe9f5fbdda"),
    ("diacritics", 18): ("resolved", (("province", 0, 13),), "b957f754a168589b9d80"),
    ("diacritics", 19): ("resolved", (("city", 0, 6),), "872becb262b528730494"),
    ("diacritics", 20): ("ambiguous", (("city", 3, 11),), "654203bb77f2e43951d8"),
    ("diacritics", 21): ("ambiguous", (("region", 3, 13),), "6dc8a6a27f3c754f2efb"),
    ("diacritics", 22): ("ambiguous", (("region", 0, 8),), "26062f9dbc8047e7fce4"),
    ("diacritics", 23): ("ambiguous", (("city", 0, 8),), "e2fe6e183dfd38528f87"),
    ("diacritics", 24): ("ambiguous", (("city", 0, 7), ("city", 11, 15)), "9d3c9f7bb84c7472390f"),
    ("diacritics", 25): (
        "resolved",
        (("landmark", 0, 13), ("city", 15, 22)),
        "a878622620bdac040707",
    ),
    ("diacritics", 26): (
        "resolved",
        (
            ("city", 6, 13),
            ("district", 15, 22),
            ("landmark", 24, 37),
            ("landmark", 40, 47),
            ("plate", 49, 56),
            ("postal_code", 68, 79),
        ),
        "7449dc092bf35f7ece6d",
    ),
    ("diacritics", 27): ("unresolved", (("landmark", 0, 11),), "bd456d7f8637aa1103ea"),
    ("diacritics", 28): ("unresolved", (("landmark", 0, 14),), "5fa2ad9ecf5916d2f8bc"),
    ("diacritics", 29): ("unresolved", (("landmark", 0, 13),), "e5851a78bccd9efa559a"),
    ("diacritics", 30): ("unresolved", (("landmark", 5, 15),), "e62ec351c64060c6d7a2"),
    ("diacritics", 31): ("resolved", (("coordinates", 9, 23),), "55f1276eb6fb16643fc3"),
    ("diacritics", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("diacritics", 33): ("unresolved", (("postal_code", 10, 20),), "985d848768dd52c7c1b3"),
    ("diacritics", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("diacritics", 35): ("ambiguous", (("city", 0, 8),), "3ab3d6134cba59470957"),
    ("diacritics", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("diacritics", 37): ("resolved", (("city", 3, 7),), "eaa67500fcfcfb14ca57"),
    ("diacritics", 38): ("resolved", (("province", 0, 14),), "67ee9e505e00e31a092d"),
    ("diacritics", 39): (
        "unresolved",
        (("landmark", 0, 15), ("plate", 18, 25)),
        "58cba73ca48424a93f03",
    ),
    ("diacritics", 40): ("resolved", (("coordinates", 9, 23),), "c31d1f5e08bfd8f7acb5"),
    ("diacritics", 41): ("resolved", (("city", 12, 16),), "e141ec5029c57b732071"),
    ("diacritics", 42): ("resolved", (("city", 12, 14),), "8121583b366ef99235dd"),
    ("diacritics", 43): (
        "resolved",
        (("postal_code", 10, 20), ("city", 25, 31)),
        "1c16afdf99add8698da4",
    ),
    ("tatweel", 0): (
        "ambiguous",
        (("city", 6, 9), ("plate", 11, 18), ("city", 21, 30)),
        "6f137b29a202daa23f08",
    ),
    ("tatweel", 1): ("resolved", (("city", 0, 10),), "20fb72ff881b05345a2c"),
    ("tatweel", 2): ("resolved", (("city", 0, 7),), "67aaa23e5c8a449dcb53"),
    ("tatweel", 3): ("resolved", (("city", 10, 17),), "63da08e4a57a8909aa14"),
    ("tatweel", 4): ("resolved", (("city", 9, 16),), "b2423d228c0d2824ba5d"),
    ("tatweel", 5): ("resolved", (("city", 0, 12), ("province", 14, 29)), "d4373d18e12a468c5f24"),
    ("tatweel", 6): ("resolved", (("city", 3, 9),), "6d8f731054ea3e292395"),
    ("tatweel", 7): ("resolved", (("city", 0, 9),), "401287fe642a8726319f"),
    ("tatweel", 8): ("resolved", (("city", 0, 8),), "d87a7c47de756304849c"),
    ("tatweel", 9): ("resolved", (("city", 3, 14),), "29640882b7864c6e364b"),
    ("tatweel", 10): ("resolved", (("city", 0, 10),), "d13c4cef545387a31a71"),
    ("tatweel", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("tatweel", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("tatweel", 13): ("resolved", (("city", 0, 8), ("province", 9, 13)), "8fe132e0641feaf1ab92"),
    ("tatweel", 14): ("resolved", (("city", 0, 8), ("province", 9, 17)), "52e19762cc59faf853bf"),
    ("tatweel", 15): ("resolved", (("city", 0, 16),), "90c868edd563c0ca5cec"),
    ("tatweel", 16): ("resolved", (("landmark", 0, 9), ("city", 10, 17)), "cccd5a71a8f335eb8862"),
    ("tatweel", 17): ("resolved", (("city", 6, 9),), "29e53361abd0f5c39092"),
    ("tatweel", 18): ("resolved", (("province", 0, 12),), "7dc4c1821c5d758bf7c6"),
    ("tatweel", 19): ("resolved", (("city", 0, 7),), "31a080d5b851acdd4120"),
    ("tatweel", 20): ("ambiguous", (("city", 3, 11),), "f756249be00c3321f73c"),
    ("tatweel", 21): ("ambiguous", (("region", 3, 13),), "470e6186d563201aa2a5"),
    ("tatweel", 22): ("ambiguous", (("region", 0, 7),), "848067f7ed5640fe360c"),
    ("tatweel", 23): ("ambiguous", (("city", 0, 8),), "b623f3b89be36d02f0cf"),
    ("tatweel", 24): ("ambiguous", (("city", 0, 7), ("city", 11, 14)), "684d5277a300e34cb986"),
    ("tatweel", 25): ("resolved", (("landmark", 0, 14), ("city", 16, 23)), "cdacb68744cec1ac8dac"),
    ("tatweel", 26): (
        "resolved",
        (
            ("city", 6, 13),
            ("district", 15, 22),
            ("landmark", 24, 38),
            ("landmark", 40, 47),
            ("plate", 49, 56),
            ("postal_code", 68, 79),
        ),
        "70b24622825767a431b9",
    ),
    ("tatweel", 27): ("unresolved", (("landmark", 0, 11),), "bd456d7f8637aa1103ea"),
    ("tatweel", 28): ("unresolved", (("landmark", 0, 14),), "f80d6b1f4b445cd0e89c"),
    ("tatweel", 29): ("unresolved", (("landmark", 0, 13),), "06b5e59c12201822fa20"),
    ("tatweel", 30): ("unresolved", (("landmark", 5, 15),), "e62ec351c64060c6d7a2"),
    ("tatweel", 31): ("resolved", (("coordinates", 9, 23),), "55f1276eb6fb16643fc3"),
    ("tatweel", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("tatweel", 33): ("unresolved", (("postal_code", 10, 20),), "985d848768dd52c7c1b3"),
    ("tatweel", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("tatweel", 35): ("ambiguous", (("city", 0, 8),), "5efe1029b8c20e5766cc"),
    ("tatweel", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("tatweel", 37): ("resolved", (("city", 3, 6),), "48b6f4b96138371382d4"),
    ("tatweel", 38): ("resolved", (("province", 0, 15),), "638d995961692dcbec12"),
    ("tatweel", 39): (
        "unresolved",
        (("landmark", 0, 16), ("plate", 18, 25)),
        "45344e51771e3dcb2621",
    ),
    ("tatweel", 40): ("resolved", (("coordinates", 9, 23),), "c31d1f5e08bfd8f7acb5"),
    ("tatweel", 41): ("resolved", (("city", 11, 17),), "2ed1501bee6b40bd26c4"),
    ("tatweel", 42): ("resolved", (("city", 11, 13),), "e5e4ae3037f1ddfe7f45"),
    ("tatweel", 43): (
        "resolved",
        (("postal_code", 10, 20), ("city", 24, 31)),
        "17653152858166da9b9a",
    ),
    ("marks", 0): ("resolved", (("city", 7, 10), ("plate", 12, 20)), "4259b75f531405d0bead"),
    ("marks", 1): ("resolved", (("city", 1, 10),), "741dfa1fda80bf43c116"),
    ("marks", 2): ("resolved", (("city", 1, 8),), "16efdc29cf321a93805a"),
    ("marks", 3): ("resolved", (("city", 13, 21),), "4bd86bde1be0d233104b"),
    ("marks", 4): ("resolved", (("city", 11, 17),), "cbca85f1298dbab097a9"),
    ("marks", 5): ("resolved", (("city", 1, 12), ("province", 14, 30)), "02daeb50604d854de0ed"),
    ("marks", 6): ("resolved", (("city", 4, 10),), "4248ed02bb4d3bdc3f24"),
    ("marks", 7): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 8): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 9): ("resolved", (("city", 5, 15),), "58671cdc3fc3cd7449cf"),
    ("marks", 10): ("resolved", (("city", 1, 10),), "bbc1ba47124d608aa457"),
    ("marks", 11): ("resolved", (("city", 19, 26),), "57444faab38319362666"),
    ("marks", 12): ("resolved", (("city", 13, 19),), "e413b8820e43e7b6ca2d"),
    ("marks", 13): ("resolved", (("city", 1, 9), ("province", 10, 15)), "1976d338d5941321c428"),
    ("marks", 14): ("resolved", (("city", 1, 9), ("province", 10, 17)), "aaaf52ad92f1eb63e783"),
    ("marks", 15): ("resolved", (("city", 1, 16),), "faaeaaba6a167c657019"),
    ("marks", 16): ("resolved", (("city", 13, 19),), "e9e026c1201fd6a2464c"),
    ("marks", 17): ("resolved", (("city", 7, 10),), "58dac17810dd81040acc"),
    ("marks", 18): ("resolved", (("province", 1, 14),), "2595685e016938a113ff"),
    ("marks", 19): ("resolved", (("city", 1, 9),), "ce098e65e3ddd3f90e63"),
    ("marks", 20): ("ambiguous", (("city", 5, 13),), "c37ffedb95036c37a960"),
    ("marks", 21): ("ambiguous", (("region", 5, 16),), "c30ff8d5d439b9d9b475"),
    ("marks", 22): ("ambiguous", (("region", 1, 9),), "6559212e74b4be2c7c52"),
    ("marks", 23): ("ambiguous", (("city", 1, 8),), "9f50dc85691e3d73b592"),
    ("marks", 24): ("ambiguous", (("city", 1, 7), ("city", 12, 15)), "45408748d4cdab70dc31"),
    ("marks", 25): ("resolved", (("landmark", 1, 16), ("city", 18, 24)), "f791c7010d658974ab24"),
    ("marks", 26): (
        "resolved",
        (
            ("city", 7, 13),
            ("landmark", 25, 40),
            ("landmark", 42, 49),
            ("plate", 51, 59),
            ("postal_code", 69, 80),
        ),
        "87efcccac7834edf9f75",
    ),
    ("marks", 27): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 28): ("unresolved", (("landmark", 1, 15),), "b377e8d7a0a6b90d0f2d"),
    ("marks", 29): ("unresolved", (("landmark", 1, 14),), "b73d7fe3e5aea6650265"),
    ("marks", 30): ("unresolved", (("landmark", 6, 16),), "5d6a76e907dfbc991fe3"),
    ("marks", 31): ("resolved", (("coordinates", 11, 25),), "b92aee5326bf9cd83fcd"),
    ("marks", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 33): ("unresolved", (("postal_code", 9, 19),), "2f0db1da7901467f7ada"),
    ("marks", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 35): ("ambiguous", (("city", 1, 9),), "d200c13c1576e2adea0c"),
    ("marks", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 37): ("resolved", (("city", 5, 8),), "a7cffe8395cebb17c6aa"),
    ("marks", 38): ("resolved", (("province", 1, 17),), "730392718c1b6ecc87f6"),
    ("marks", 39): ("unresolved", (("landmark", 1, 18), ("plate", 20, 28)), "eeccd987cc6937937949"),
    ("marks", 40): ("resolved", (("coordinates", 11, 25),), "3ca9b17d09f4ab667153"),
    ("marks", 41): ("unresolved", (), "f30951b79b4abe99be79"),
    ("marks", 42): ("resolved", (("city", 11, 13),), "e5e4ae3037f1ddfe7f45"),
    ("marks", 43): ("resolved", (("postal_code", 9, 19), ("city", 23, 31)), "aecfe5bbbca338b78d74"),
    ("persian_digits", 0): (
        "ambiguous",
        (("city", 5, 8), ("plate", 10, 17), ("city", 20, 28)),
        "0c7defd452071f796432",
    ),
    ("persian_digits", 1): ("resolved", (("city", 0, 8),), "7f71e4f4dc87d529236e"),
    ("persian_digits", 2): ("resolved", (("city", 0, 6),), "3735f6885c9e6a72fd5e"),
    ("persian_digits", 3): ("resolved", (("city", 9, 15),), "ba78850486051557e3df"),
    ("persian_digits", 4): ("resolved", (("city", 8, 13),), "66ba5aa95fdf0b31327f"),
    ("persian_digits", 5): (
        "resolved",
        (("city", 0, 9), ("province", 11, 23)),
        "4ac5ae4a20d0c3927257",
    ),
    ("persian_digits", 6): ("resolved", (("city", 3, 8),), "a8a95772c3f3361733f8"),
    ("persian_digits", 7): ("resolved", (("city", 0, 8),), "725aad9eb3b29a9b840b"),
    ("persian_digits", 8): ("resolved", (("city", 0, 7),), "ddb7292270f997d8f902"),
    ("persian_digits", 9): ("resolved", (("city", 3, 12),), "2977b79d2c6a82cd8a59"),
    ("persian_digits", 10): ("resolved", (("city", 0, 8),), "db89d0506d9f650ffe6a"),
    ("persian_digits", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("persian_digits", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("persian_digits", 13): (
        "resolved",
        (("city", 0, 7), ("province", 8, 12)),
        "b8bc7a216ba4df60077a",
    ),
    ("persian_digits", 14): (
        "resolved",
        (("city", 0, 7), ("province", 8, 14)),
        "3065bd57248114ac87b7",
    ),
    ("persian_digits", 15): ("resolved", (("city", 0, 13),), "21c4c7f7e3dc98214119"),
    ("persian_digits", 16): (
        "resolved",
        (("landmark", 0, 9), ("city", 10, 15)),
        "c03f7470a41c26c69ad7",
    ),
    ("persian_digits", 17): ("resolved", (("city", 5, 8),), "cb9ac52c7c55d9185e54"),
    ("persian_digits", 18): ("resolved", (("province", 0, 10),), "cd662954ecfe3e38bfe3"),
    ("persian_digits", 19): ("resolved", (("city", 0, 6),), "872becb262b528730494"),
    ("persian_digits", 20): ("ambiguous", (("city", 3, 10),), "49a97b1d24f6a9f934a7"),
    ("persian_digits", 21): ("ambiguous", (("region", 3, 12),), "6dc9d617f7584a37580f"),
    ("persian_digits", 22): ("ambiguous", (("region", 0, 6),), "157508ff0524be1a5762"),
    ("persian_digits", 23): ("ambiguous", (("city", 0, 6),), "9d8de8e715e7a0802e04"),
    ("persian_digits", 24): (
        "ambiguous",
        (("city", 0, 5), ("city", 9, 12)),
        "44531f08dcb1be39fe0f",
    ),
    ("persian_digits", 25): (
        "resolved",
        (("landmark", 0, 12), ("city", 14, 19)),
        "2df645ae4b0d2301bf93",
    ),
    ("persian_digits", 26): (
        "resolved",
        (
            ("city", 5, 10),
            ("district", 12, 19),
            ("landmark", 21, 34),
            ("landmark", 36, 43),
            ("plate", 45, 52),
            ("postal_code", 62, 73),
        ),
        "98f1af0a1f18a3d587ea",
    ),
    ("persian_digits", 27): ("unresolved", (("landmark", 0, 11),), "bd456d7f8637aa1103ea"),
    ("persian_digits", 28): ("unresolved", (("landmark", 0, 12),), "b34b8d321014c80c6387"),
    ("persian_digits", 29): ("unresolved", (("landmark", 0, 11),), "b4b0766f0409caeda1fd"),
    ("persian_digits", 30): ("unresolved", (("landmark", 5, 15),), "e62ec351c64060c6d7a2"),
    ("persian_digits", 31): ("resolved", (("coordinates", 8, 22),), "30365503698e76fddbd9"),
    ("persian_digits", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("persian_digits", 33): ("unresolved", (("postal_code", 8, 18),), "70bd699ba819d5d65766"),
    ("persian_digits", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("persian_digits", 35): ("ambiguous", (("city", 0, 7),), "28f5f5d52d5b6f5e5fbe"),
    ("persian_digits", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("persian_digits", 37): ("resolved", (("city", 3, 6),), "48b6f4b96138371382d4"),
    ("persian_digits", 38): ("resolved", (("province", 0, 12),), "84bbd65ff6e1b1983190"),
    ("persian_digits", 39): (
        "unresolved",
        (("landmark", 0, 15), ("plate", 17, 24)),
        "01aa92098f3cce3673f9",
    ),
    ("persian_digits", 40): ("resolved", (("coordinates", 8, 22),), "22df4eaf63c075f624ff"),
    ("persian_digits", 41): ("resolved", (("city", 11, 15),), "266dffed6b11808b2e2c"),
    ("persian_digits", 42): ("resolved", (("city", 9, 11),), "d2e25b8fe82d417d3fd8"),
    ("persian_digits", 43): (
        "resolved",
        (("postal_code", 8, 18), ("city", 22, 28)),
        "666375fdb3adc02f98d4",
    ),
    ("arabic_indic_digits", 0): (
        "ambiguous",
        (("city", 5, 8), ("plate", 10, 17), ("city", 20, 28)),
        "b221286fa7ee597bbc92",
    ),
    ("arabic_indic_digits", 1): ("resolved", (("city", 0, 8),), "7f71e4f4dc87d529236e"),
    ("arabic_indic_digits", 2): ("resolved", (("city", 0, 6),), "3735f6885c9e6a72fd5e"),
    ("arabic_indic_digits", 3): ("resolved", (("city", 9, 15),), "ba78850486051557e3df"),
    ("arabic_indic_digits", 4): ("resolved", (("city", 8, 13),), "66ba5aa95fdf0b31327f"),
    ("arabic_indic_digits", 5): (
        "resolved",
        (("city", 0, 9), ("province", 11, 23)),
        "4ac5ae4a20d0c3927257",
    ),
    ("arabic_indic_digits", 6): ("resolved", (("city", 3, 8),), "a8a95772c3f3361733f8"),
    ("arabic_indic_digits", 7): ("resolved", (("city", 0, 8),), "725aad9eb3b29a9b840b"),
    ("arabic_indic_digits", 8): ("resolved", (("city", 0, 7),), "ddb7292270f997d8f902"),
    ("arabic_indic_digits", 9): ("resolved", (("city", 3, 12),), "2977b79d2c6a82cd8a59"),
    ("arabic_indic_digits", 10): ("resolved", (("city", 0, 8),), "db89d0506d9f650ffe6a"),
    ("arabic_indic_digits", 11): ("resolved", (("city", 18, 25),), "ad00dee7c51d46884dcd"),
    ("arabic_indic_digits", 12): ("resolved", (("city", 12, 18),), "e1433528a2fc545fa8a5"),
    ("arabic_indic_digits", 13): (
        "resolved",
        (("city", 0, 7), ("province", 8, 12)),
        "b8bc7a216ba4df60077a",
    ),
    ("arabic_indic_digits", 14): (
        "resolved",
        (("city", 0, 7), ("province", 8, 14)),
        "3065bd57248114ac87b7",
    ),
    ("arabic_indic_digits", 15): ("resolved", (("city", 0, 13),), "21c4c7f7e3dc98214119"),
    ("arabic_indic_digits", 16): (
        "resolved",
        (("landmark", 0, 9), ("city", 10, 15)),
        "c03f7470a41c26c69ad7",
    ),
    ("arabic_indic_digits", 17): ("resolved", (("city", 5, 8),), "cb9ac52c7c55d9185e54"),
    ("arabic_indic_digits", 18): ("resolved", (("province", 0, 10),), "cd662954ecfe3e38bfe3"),
    ("arabic_indic_digits", 19): ("resolved", (("city", 0, 6),), "872becb262b528730494"),
    ("arabic_indic_digits", 20): ("ambiguous", (("city", 3, 10),), "49a97b1d24f6a9f934a7"),
    ("arabic_indic_digits", 21): ("ambiguous", (("region", 3, 12),), "6dc9d617f7584a37580f"),
    ("arabic_indic_digits", 22): ("ambiguous", (("region", 0, 6),), "157508ff0524be1a5762"),
    ("arabic_indic_digits", 23): ("ambiguous", (("city", 0, 6),), "9d8de8e715e7a0802e04"),
    ("arabic_indic_digits", 24): (
        "ambiguous",
        (("city", 0, 5), ("city", 9, 12)),
        "44531f08dcb1be39fe0f",
    ),
    ("arabic_indic_digits", 25): (
        "resolved",
        (("landmark", 0, 12), ("city", 14, 19)),
        "2df645ae4b0d2301bf93",
    ),
    ("arabic_indic_digits", 26): (
        "resolved",
        (
            ("city", 5, 10),
            ("district", 12, 19),
            ("landmark", 21, 34),
            ("landmark", 36, 43),
            ("plate", 45, 52),
            ("postal_code", 62, 73),
        ),
        "8449449e874f1a2c358f",
    ),
    ("arabic_indic_digits", 27): ("unresolved", (("landmark", 0, 11),), "bd456d7f8637aa1103ea"),
    ("arabic_indic_digits", 28): ("unresolved", (("landmark", 0, 12),), "b34b8d321014c80c6387"),
    ("arabic_indic_digits", 29): ("unresolved", (("landmark", 0, 11),), "b4b0766f0409caeda1fd"),
    ("arabic_indic_digits", 30): ("unresolved", (("landmark", 5, 15),), "e62ec351c64060c6d7a2"),
    ("arabic_indic_digits", 31): ("resolved", (("coordinates", 8, 22),), "a8e25cac5158986758ba"),
    ("arabic_indic_digits", 32): ("unresolved", (), "f30951b79b4abe99be79"),
    ("arabic_indic_digits", 33): ("unresolved", (("postal_code", 8, 18),), "dab7f07b7bf25d94ceb8"),
    ("arabic_indic_digits", 34): ("unresolved", (), "f30951b79b4abe99be79"),
    ("arabic_indic_digits", 35): ("ambiguous", (("city", 0, 7),), "28f5f5d52d5b6f5e5fbe"),
    ("arabic_indic_digits", 36): ("unresolved", (), "f30951b79b4abe99be79"),
    ("arabic_indic_digits", 37): ("resolved", (("city", 3, 6),), "48b6f4b96138371382d4"),
    ("arabic_indic_digits", 38): ("resolved", (("province", 0, 12),), "84bbd65ff6e1b1983190"),
    ("arabic_indic_digits", 39): (
        "unresolved",
        (("landmark", 0, 15), ("plate", 17, 24)),
        "feec515b78763607ce1b",
    ),
    ("arabic_indic_digits", 40): ("resolved", (("coordinates", 8, 22),), "2c2db548e414d93fd0ca"),
    ("arabic_indic_digits", 41): ("resolved", (("city", 11, 15),), "266dffed6b11808b2e2c"),
    ("arabic_indic_digits", 42): ("resolved", (("city", 9, 11),), "d2e25b8fe82d417d3fd8"),
    ("arabic_indic_digits", 43): (
        "resolved",
        (("postal_code", 8, 18), ("city", 22, 28)),
        "8bff9be8d51a550efe7c",
    ),
}

ENTITY_SAMPLES: tuple[str, ...] = (
    "شماره من \u06f0\u06f9\u06f1\u06f2 \u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9 است",
    (
        "کد ملی \u06f0\u06f0\u06f1\u06f2\u06f3\u06f4"
        "\u06f5\u06f6\u06f7\u06f9 و ایمیلم Ali.R@Exam"
        "ple.com هست"
    ),
    (
        "\u06f2\u066c\u06f5\u06f0\u06f0\u066c\u06f0"
        "\u06f0\u06f0 ریال تا \u06f1\u06f5 مهر \u06f1"
        "\u06f4\u06f0\u06f5 واریز شود"
    ),
    "پس\u200cفردا ساعت پنج و نیم عصر با شماره\u200cام 09123456789 تماس بگیرید",
    "سه میلیون و دویست هزار تومن برای جلسه \u06f1\u06f4\u06f0\u06f5/\u06f0\u06f7/\u06f0\u06f8",
)
ENTITY_VARIANTS = (
    "as_is",
    "arabic_letters",
    "persian_digits",
    "arabic_indic_digits",
    "zwnj",
    "marks",
)
ADDRESS_SAMPLES: tuple[dict[str, str], ...] = (
    {
        "city": "\u0643اشان",
        "province": "اصفهان",
        "postal_code": "\u06f1\u06f4\u06f3\u06f5\u06f6-\u06f7\u06f8\u06f9\u06f1\u06f3",
    },
    {"city": "خان\u06c0", "street": "خ\u064aابان  ول\u064a\u200cعصر\u060c پلا\u0643 \u06f1\u06f2"},
    {
        "city": "ته\u0640\u0640\u0640ران",
        "district": "منطقه \u06f3",
        "address_text": " خ\u064aابان ",
    },
    {"city": "م\u0624سسه", "province": "\u200fفارس\u200e"},
    {
        "city": "Tehran",
        "postal_code": "\uff11\uff14\uff13\uff15\uff16\uff17\uff18\uff19\uff11\uff13",
    },
    {"city": "ا\u0653باده", "province": "\u064a\u0654"},
)


def _entity_observation(text: str):
    from datetime import datetime

    from backend.speech.dates import TEHRAN
    from backend.speech.entities import extract_entities

    entities = extract_entities(text, datetime(2026, 9, 27, 10, 0, tzinfo=TEHRAN))
    return tuple((e.type, e.start, e.end) for e in entities), _canonical_digest(entities)


def _address_observation(fields: dict[str, str]) -> dict:
    from backend.locations.normalize import normalize_address

    return _plain(normalize_address(**fields))


ENTITY_EXPECTED: dict = {
    ("as_is", 0): ((("phone", 9, 22),), "fc5285ab10077c8084b3"),
    ("as_is", 1): ((("national_id", 7, 17), ("email", 27, 44)), "345d86940bd7be6b4f9c"),
    ("as_is", 2): ((("amount", 0, 14), ("date", 18, 29)), "c8ffd9c74fe9ac9f83e6"),
    ("as_is", 3): ((("date", 0, 7), ("time", 8, 26), ("phone", 39, 50)), "f7c8eba4b229a0b3a51b"),
    ("as_is", 4): ((("amount", 0, 27), ("date", 38, 48)), "8ed3ee6d8648cfbc6cfe"),
    ("arabic_letters", 0): ((("phone", 9, 22),), "fc5285ab10077c8084b3"),
    ("arabic_letters", 1): ((("national_id", 7, 17), ("email", 27, 44)), "345d86940bd7be6b4f9c"),
    ("arabic_letters", 2): ((("amount", 0, 14), ("date", 18, 29)), "5df525eda0e01693d2f3"),
    ("arabic_letters", 3): (
        (("date", 0, 7), ("time", 8, 26), ("phone", 39, 50)),
        "da8439be006832e20f9a",
    ),
    ("arabic_letters", 4): ((("amount", 0, 27), ("date", 38, 48)), "bcb208178068687d328b"),
    ("persian_digits", 0): ((("phone", 9, 22),), "fc5285ab10077c8084b3"),
    ("persian_digits", 1): ((("national_id", 7, 17), ("email", 27, 44)), "345d86940bd7be6b4f9c"),
    ("persian_digits", 2): ((("amount", 0, 14), ("date", 18, 29)), "c8ffd9c74fe9ac9f83e6"),
    ("persian_digits", 3): (
        (("date", 0, 7), ("time", 8, 26), ("phone", 39, 50)),
        "2d78d3ec6cf604b8dda7",
    ),
    ("persian_digits", 4): ((("amount", 0, 27), ("date", 38, 48)), "8ed3ee6d8648cfbc6cfe"),
    ("arabic_indic_digits", 0): ((("phone", 9, 22),), "a79554a0815e69f7b940"),
    ("arabic_indic_digits", 1): (
        (("national_id", 7, 17), ("email", 27, 44)),
        "8a4aecdcd8c76202676f",
    ),
    ("arabic_indic_digits", 2): ((("amount", 0, 14), ("date", 18, 29)), "8f5e2dab601615c7c08b"),
    ("arabic_indic_digits", 3): (
        (("date", 0, 7), ("time", 8, 26), ("phone", 39, 50)),
        "262c12b48853b2c8c56e",
    ),
    ("arabic_indic_digits", 4): ((("amount", 0, 27), ("date", 38, 48)), "beabc89ea27b0f92a5ed"),
    ("zwnj", 0): ((), "4f53cda18c2baa0c0354"),
    ("zwnj", 1): ((("national_id", 7, 17), ("email", 27, 44)), "345d86940bd7be6b4f9c"),
    ("zwnj", 2): ((("amount", 0, 14), ("date", 18, 29)), "e7b7c9c13ecf424c3166"),
    ("zwnj", 3): ((("date", 0, 7), ("time", 8, 26), ("phone", 39, 50)), "a40184f3db1064dcb6b0"),
    ("zwnj", 4): ((("amount", 0, 27), ("date", 38, 48)), "9f4ef878691411844b82"),
    ("marks", 0): ((("phone", 13, 26),), "81e073c2374524f24b4e"),
    ("marks", 1): ((("national_id", 9, 19), ("email", 32, 49)), "8916971501c023795af3"),
    ("marks", 2): ((), "4f53cda18c2baa0c0354"),
    ("marks", 3): ((("phone", 48, 59),), "4c015dd60dbb8817c816"),
    ("marks", 4): ((("date", 43, 53),), "064c7e2ed12be9f6bd99"),
}
ADDRESS_EXPECTED: tuple[dict, ...] = (
    {
        "address": {
            "province": "اصفهان",
            "city": "کاشان",
            "district": None,
            "street": None,
            "postal_code": "1435678913",
            "address_text": None,
            "latitude": None,
            "longitude": None,
            "province_key": "isfahan_p",
            "city_key": "kashan",
        },
        "errors": [],
        "warnings": [],
    },
    {
        "address": {
            "province": None,
            "city": "خان\u06c0",
            "district": None,
            "street": "خیابان ولی عصر, پلاک 12",
            "postal_code": None,
            "address_text": None,
            "latitude": None,
            "longitude": None,
            "province_key": None,
            "city_key": None,
        },
        "errors": [],
        "warnings": ["city_not_in_gazetteer"],
    },
    {
        "address": {
            "province": None,
            "city": "تهران",
            "district": "منطقه 3",
            "street": None,
            "postal_code": None,
            "address_text": "خ\u064aابان",
            "latitude": None,
            "longitude": None,
            "province_key": None,
            "city_key": "tehran",
        },
        "errors": [],
        "warnings": [],
    },
    {
        "address": {
            "province": "فارس",
            "city": "م\u0624سسه",
            "district": None,
            "street": None,
            "postal_code": None,
            "address_text": None,
            "latitude": None,
            "longitude": None,
            "province_key": "fars",
            "city_key": None,
        },
        "errors": [],
        "warnings": ["city_not_in_gazetteer"],
    },
    {
        "address": {
            "province": None,
            "city": "tehran",
            "district": None,
            "street": None,
            "postal_code": "\uff11\uff14\uff13\uff15\uff16\uff17\uff18\uff19\uff11\uff13",
            "address_text": None,
            "latitude": None,
            "longitude": None,
            "province_key": None,
            "city_key": "tehran",
        },
        "errors": [],
        "warnings": [],
    },
    {
        "address": {
            "province": "ی",
            "city": "اباده",
            "district": None,
            "street": None,
            "postal_code": None,
            "address_text": None,
            "latitude": None,
            "longitude": None,
            "province_key": None,
            "city_key": None,
        },
        "errors": [],
        "warnings": ["city_not_in_gazetteer", "province_not_in_gazetteer"],
    },
)

DIVERGENCE_TABLE = (
    ("خان\u06c0", "خانه", "خان\u06c0", (0, 1, 2, 3), "خانه"),
    ("م\u0624سسه", "موسسه", "م\u0624سسه", (0, 1, 2, 3, 4), "موسسه"),
    ("\u0672", "ا", "\u0672", (0,), "\u0672"),
    ("ا\u0653ب", "\u0622ب", "اب", (0, 2), "\u0622ب"),
    ("\u064a\u0654", "\u0626", "ی", (0,), "\u0626"),
    ("\ufef1\ufeee", "\ufef1\ufeee", "\ufef1\ufeee", (0, 1), "یو"),
    ("می\u200cروم", "می\u200cروم", "می روم", (0, 1, 2, 3, 4, 5), "می روم"),
    ("\u200fسلام\u200e", "\u200fسلام\u200e", "سلام", (1, 2, 3, 4), "سلام"),
    (
        "\u06f3\u06f5\u066b\u06f7\u060c \u06f5\u06f1\u066c\u06f4",
        "35\u066b7\u060c 51\u066c4",
        "35.7, 51,4",
        tuple(range(10)),
        "35 7 51 4",
    ),
    ("\uff11\uff12\uff13", "\uff11\uff12\uff13", "\uff11\uff12\uff13", (0, 1, 2), "123"),
    ("سلام\u06ddا", "سلام\u06ddا", "سلام\u06ddا", (0, 1, 2, 3, 4, 5), "سلاما"),
    ("ته\u0640\u0640\u0640ران", "تهران", "تهران", (0, 1, 5, 6, 7), "تهران"),
    ("ABC Tehran", "ABC Tehran", "abc tehran", tuple(range(10)), "abc tehran"),
)
ADDRESS_LINE = "خ\u064aابان  ول\u064a\u200cعصر\u060c پلا\u0643 \u06f1\u06f2"
PERSIAN_PHONE_TEXT = (
    "شماره من \u06f0\u06f9\u06f1\u06f2 \u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9 است"
)
POSTAL_TEXT = "کد پستی \u06f1\u06f4\u06f3\u06f5\u06f6\u06f7\u06f8\u06f9\u06f1\u06f2 است"
ARABIC_POSTAL_TEXT = (
    "\u0643د پست\u064a \u06f1\u06f4\u06f3\u06f5\u06f6\u06f7\u06f8\u06f9\u06f1\u06f2 است"
)
ZWNJ_POSTAL_TEXT = "کد\u200cپستی \u06f1\u06f4\u06f3\u06f5\u06f6\u06f7\u06f8\u06f9\u06f1\u06f2 است"
FULLWIDTH_MOBILE_TEXT = "موبایل \uff10\uff19\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19"
FULLWIDTH_NID = "\uff11\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff11"
MOBILE_SPELLINGS = (
    "\u06f0\u06f9\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "\u0660\u0669\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
    "\uff10\uff19\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
    "+\u06f9\u06f8 \u06f9\u06f1\u06f2 \u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9",
    "\u06f0\u06f9\u06f1\u06f2\u0663\u0664\u0665\u06f6\u06f7\u06f8\u06f9",
    "\u06f0\u06f0\u06f9\u06f8 912 \u0663\u0664\u0665 \u06f6\u06f7\u06f8\u06f9",
)
SHORT_MOBILE_SPELLINGS = (
    "\u06f0\u06f9\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8",
    "\u0660\u0669\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668",
    "\uff10\uff19\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18",
)
CALLER_NUMBERS = (
    ("\u06f0\u06f2\u06f1-\u06f8\u06f8\u06f7\u06f7\u06f6\u06f6\u06f5\u06f5", ("02188776655", False)),
    ("\u0660\u0669\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", ("+989123456789", True)),
    (
        "+\u06f4\u06f4 \u06f2\u06f0 \u06f7\u06f9\u06f4\u06f6 \u06f0\u06f0\u06f0\u06f0",
        ("+442079460000", False),
    ),
    ("ext \u06f1\u06f2\u06f3", ("123", False)),
    ("", (None, False)),
    (None, (None, False)),
    ("   ", (None, False)),
    ("no digits", (None, False)),
    ("\u0660\u0662\u0661-\u0668\u0668\u0667\u0667\u0666\u0666\u0665\u0665", ("02188776655", False)),
    # HOM-165: fullwidth landlines are stored as ASCII (base kept them fullwidth).
    ("\uff10\uff12\uff11\uff18\uff18\uff17\uff17\uff16\uff16\uff15\uff15", ("02188776655", False)),
    # Devanagari is not part of the canonical set and stays as it is.
    (
        "\u0966\u0968\u0967\u096e\u096e\u096d\u096d\u096c\u096c\u096b\u096b",
        ("\u0966\u0968\u0967\u096e\u096e\u096d\u096d\u096c\u096c\u096b\u096b", False),
    ),
)
ALL_CLASSES_DIGEST = "959fd446dfa18828da8543a05eefb4e4be20bb0cbbcbf84b92f45b58b3ed35ad"
EMAIL_ONLY_DIGEST = "39f06a1d558b0deec7a12df2d3abaf5688443ae31aa770d3e78d333058cdd1d8"
# HOM-164 bumped the detector to text-pii/2; the base digests above stay the
# text-pii/1 values (stored versions keep them), these are the current ones.
# HOM-165 bumped it again to text-pii/3 (fullwidth digits fold to ASCII).
CURRENT_DETECTOR_VERSION = "text-pii/3"
CURRENT_ALL_CLASSES_DIGEST = "6af861dce4c1d53f1436c59aa720d5227f659829e578d65db0c843a48ab2b967"
CURRENT_EMAIL_ONLY_DIGEST = "2620e3be0957f7ef351869a4b9e87fff21333d61524a2bc4918e1e99eb9214f5"


class TestGoldenDivergenceTable:
    @pytest.mark.parametrize(
        ("raw", "speech", "locations_text", "locations_offsets", "voicemail"), DIVERGENCE_TABLE
    )
    def test_each_profile_keeps_its_base_output(
        self, raw, speech, locations_text, locations_offsets, voicemail
    ):
        from backend.locations.textnorm import normalize_char_stream
        from backend.speech.text_normalize import normalize_fa
        from backend.voicemail.classify import normalize_text

        stream = normalize_char_stream(raw)
        assert normalize_fa(raw) == speech
        assert (stream.text, tuple(stream.offsets), stream.original) == (
            locations_text,
            locations_offsets,
            raw,
        )
        assert normalize_text(raw) == voicemail

    def test_address_line_spans_map_back_onto_the_original(self):
        from backend.locations.textnorm import normalize_char_stream

        stream = normalize_char_stream(ADDRESS_LINE)
        assert stream.text == "خیابان ولی عصر, پلاک 12"
        assert stream.span(7, 14) == (8, 15)
        assert ADDRESS_LINE[8:15] == "ول\u064a\u200cعصر"
        assert stream.span(len(stream.text), len(stream.text)) == (
            len(ADDRESS_LINE),
            len(ADDRESS_LINE),
        )


class TestGoldenValues:
    def test_digit_folding_values_listed_under_the_table(self):
        from backend.privacy import text_pii
        from backend.speech import text_normalize

        for fold in (text_pii.normalize_digits, text_normalize.normalize_digits):
            assert fold("\u06f3\u06f5\u066b\u06f7\u060c \u06f5\u06f1\u066c\u06f4") == (
                "35\u066b7\u060c 51\u066c4"
            )
            assert fold("\uff11\uff12\uff13") == "\uff11\uff12\uff13"

    @pytest.mark.parametrize("raw", MOBILE_SPELLINGS)
    def test_eleven_digit_mobile_spellings_normalize(self, raw):
        from backend.security.normalization import normalize_iranian_mobile

        assert normalize_iranian_mobile(raw) == "+989123456789"

    @pytest.mark.parametrize("raw", SHORT_MOBILE_SPELLINGS)
    def test_ten_digit_mobile_spellings_are_refused(self, raw):
        from backend.security.normalization import normalize_iranian_mobile

        with pytest.raises(ValueError, match="^Invalid Iranian mobile number$"):
            normalize_iranian_mobile(raw)

    def test_persian_digit_phone_is_detected_and_redacted_in_original_offsets(self):
        from backend.privacy.text_pii import PiiClass, detect, redact

        spans = detect(PERSIAN_PHONE_TEXT)
        assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
            (9, 22, PiiClass.PHONE, "09123456789")
        ]
        redacted, replacements = redact(PERSIAN_PHONE_TEXT)
        assert redacted == "شماره من [PHONE_1] است"
        assert [r.value for r in replacements] == [
            "\u06f0\u06f9\u06f1\u06f2 \u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9"
        ]

    def test_postal_code_with_persian_keyword_is_detected(self):
        from backend.privacy.text_pii import PiiClass, detect

        spans = detect(POSTAL_TEXT)
        assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
            (8, 18, PiiClass.IR_POSTAL_CODE, "1435678912")
        ]

    def test_devanagari_digits_stay_undetected(self):
        # HOM-165: Devanagari is outside the canonical digit set.
        from backend.privacy.text_pii import detect

        devanagari = FULLWIDTH_MOBILE_TEXT.translate({0xFF10 + i: 0x0966 + i for i in range(10)})
        assert detect(devanagari) == []

    @pytest.mark.parametrize("text", [ARABIC_POSTAL_TEXT, ZWNJ_POSTAL_TEXT])
    def test_postal_keyword_blind_spots_are_closed(self, text):
        # HOM-164: these were base blind spots; text-pii/2 detects them.
        from backend.privacy.text_pii import PiiClass, detect

        spans = detect(text)
        assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
            (8, 18, PiiClass.IR_POSTAL_CODE, "1435678912")
        ]

    def test_fullwidth_digits_are_detected_with_ascii_values(self):
        # HOM-165: base kept the fullwidth value and missed the fullwidth mobile.
        from backend.privacy.text_pii import PiiClass, detect

        spans = detect("کد " + FULLWIDTH_NID + " تمام")
        assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
            (3, 13, PiiClass.IR_NATIONAL_ID, "1000000001")
        ]
        spans = detect(FULLWIDTH_MOBILE_TEXT)
        assert [(s.start, s.end, s.pii_class, s.value) for s in spans] == [
            (7, 18, PiiClass.PHONE, "09123456789")
        ]

    def test_versions_and_policy_digests_are_unchanged(self):
        from backend.privacy.redaction_service import policy_digest
        from backend.privacy.text_pii import DETECTOR_VERSION, PiiClass
        from backend.voicemail.classify import RULES_VERSION, RuleClassifier

        assert DETECTOR_VERSION == CURRENT_DETECTOR_VERSION
        assert RULES_VERSION == "vm-rules-2026.09.1"
        assert RuleClassifier().version == RULES_VERSION
        base = {"detector_version": "text-pii/1"}
        assert policy_digest(list(PiiClass), **base) == ALL_CLASSES_DIGEST
        assert policy_digest([PiiClass.EMAIL], **base) == EMAIL_ONLY_DIGEST
        assert policy_digest(list(PiiClass)) == CURRENT_ALL_CLASSES_DIGEST
        assert policy_digest([PiiClass.EMAIL]) == CURRENT_EMAIL_ONLY_DIGEST

    def test_voicemail_normalizer_accepts_none(self):
        from backend.voicemail.classify import normalize_text

        assert normalize_text(None) == ""


class TestGoldenCallerNumbers:
    @pytest.mark.parametrize(("raw", "expected"), CALLER_NUMBERS)
    def test_caller_numbers_keep_their_base_form(self, raw, expected):
        from backend.voicemail.ingest import normalize_caller_number

        assert normalize_caller_number(raw) == expected


class TestGoldenDenyAllow:
    def test_locations_profile_does_not_gain_extra_folding_or_unicode_normalization(self):
        from backend.locations.textnorm import normalize_char_stream, normalize_text

        assert normalize_text("خان\u06c0") == "خان\u06c0"
        assert normalize_text("م\u0624سسه") == "م\u0624سسه"
        assert normalize_text("\u0672") == "\u0672"
        stream = normalize_char_stream("ا\u0653ب")
        assert (stream.text, tuple(stream.offsets)) == ("اب", (0, 2))
        assert normalize_text("\uff11\uff12\uff13") == "\uff11\uff12\uff13"

    def test_locations_profile_still_folds_what_it_folds_today(self):
        from backend.locations.textnorm import normalize_text
        from backend.voicemail.classify import normalize_text as voicemail_text

        assert normalize_text("عل\u064a \u0643تاب") == "علی کتاب"
        assert voicemail_text("خان\u06c0") == "خانه"

    def test_persisted_address_fields_keep_the_locations_profile(self):
        from backend.locations.normalize import normalize_address

        kept = normalize_address(city="خان\u06c0", street="م\u0624سسه")
        assert (kept.address.city, kept.address.street) == ("خان\u06c0", "م\u0624سسه")
        folded = normalize_address(province="اصفهان", city="\u0643اشان")
        assert folded.address.city == "کاشان"
        assert folded.address.city_key == "kashan"

    def test_detector_gains_only_postal_keyword_and_fullwidth_handling(self):
        from backend.privacy.text_pii import detect

        # HOM-164: the postal keyword is matched letter-folded and ZWNJ-tolerant.
        assert [(s.start, s.end) for s in detect(ARABIC_POSTAL_TEXT)] == [(8, 18)]
        assert [(s.start, s.end) for s in detect(ZWNJ_POSTAL_TEXT)] == [(8, 18)]
        # HOM-165: fullwidth digits fold to ASCII.
        assert [s.value for s in detect(FULLWIDTH_MOBILE_TEXT)] == ["09123456789"]
        spans = detect("کد " + FULLWIDTH_NID + " تمام")
        assert [s.value for s in spans] == ["1000000001"]

    def test_detector_still_finds_persian_spellings_in_original_offsets(self):
        from backend.privacy.text_pii import detect

        assert [(s.start, s.end) for s in detect(POSTAL_TEXT)] == [(8, 18)]
        assert [(s.start, s.end) for s in detect(PERSIAN_PHONE_TEXT)] == [(9, 22)]

    def test_digit_folding_covers_exactly_twenty_code_points(self):
        from backend.privacy import text_pii
        from backend.speech import text_normalize

        expected = {chr(0x06F0 + i): str(i) for i in range(10)}
        expected.update({chr(0x0660 + i): str(i) for i in range(10)})
        for fold in (text_pii.normalize_digits, text_normalize.normalize_digits):
            folded = {
                chr(code): fold(chr(code))
                for code in range(0x10000)
                if not 0xD800 <= code <= 0xDFFF and fold(chr(code)) != chr(code)
            }
            assert folded == expected
            assert fold("\uff11\uff12\uff13") == "\uff11\uff12\uff13"
            assert fold("\u0967\u0968\U0001d7cf") == "\u0967\u0968\U0001d7cf"

    def test_digit_folding_still_maps_persian_and_arabic_indic(self):
        from backend.privacy import text_pii
        from backend.speech import text_normalize

        for fold in (text_pii.normalize_digits, text_normalize.normalize_digits):
            assert fold("\u06f1\u06f2\u06f3 و \u0664\u0665\u0666") == "123 و 456"

    def test_voicemail_keeps_nfkc_and_quranic_marks(self):
        from backend.voicemail.classify import normalize_text

        assert normalize_text("\ufef1\ufeee") == "یو"
        assert normalize_text("سلام\u06ddا") == "سلاما"
        assert normalize_text("\uff11\uff12\uff13") == "123"

    def test_speech_leaves_presentation_forms_and_quranic_marks_alone(self):
        from backend.speech.text_normalize import normalize_fa

        assert normalize_fa("\ufef1\ufeee") == "\ufef1\ufeee"
        assert normalize_fa("سلام\u06ddا") == "سلام\u06ddا"

    def test_speech_composes_before_unifying_letters(self):
        from backend.speech.text_normalize import normalize_az, normalize_fa

        assert normalize_fa("\u064a\u0654") == "\u0626"
        assert normalize_fa("\u0626") == "\u0626"
        assert normalize_az("\u064a\u0654") == "\u0626"

    def test_locations_folds_the_decomposed_hamza_spelling(self):
        from backend.locations.textnorm import normalize_text

        assert normalize_text("\u064a\u0654") == "ی"
        assert normalize_text("\u0626") == "\u0626"

    def test_speech_keeps_zwnj_and_directional_marks(self):
        from backend.speech.text_normalize import normalize_fa

        assert normalize_fa("می\u200cروم") == "می\u200cروم"
        assert normalize_fa("\u200fسلام\u200e") == "\u200fسلام\u200e"

    def test_locations_and_voicemail_turn_zwnj_into_space_and_drop_marks(self):
        from backend.locations.textnorm import normalize_text
        from backend.voicemail.classify import normalize_text as voicemail_text

        for fold in (normalize_text, voicemail_text):
            assert fold("می\u200cروم") == "می روم"
            assert fold("\u200fسلام\u200e") == "سلام"

    def test_letter_unification_is_one_to_one(self):
        from backend.speech.text_normalize import unify_arabic_letters

        assert unify_arabic_letters("عل\u064a") == "علی"
        assert len(unify_arabic_letters("عل\u064a")) == 3
        assert unify_arabic_letters("\u06c0\u0624\u0672") == "هوا"

    def test_speech_date_shadow_offsets_still_line_up(self):
        from datetime import datetime

        from backend.speech.dates import TEHRAN, extract_temporal

        text = "\u200fجلسه \u06f1\u06f5 مهر \u06f1\u06f4\u06f0\u06f5 ساعت \u06f1\u06f0 صبح"
        matches = extract_temporal(text, datetime(2026, 9, 27, 10, 0, tzinfo=TEHRAN))
        assert matches
        assert all(text[m.start : m.end] == m.raw for m in matches)


def _corpus_drift(samples, variants, expected, observe):
    drift = []
    for index, sample in enumerate(samples):
        text = VARIANTS[variants](sample)
        got = observe(text)
        want = expected[(variants, index)]
        if got != want:
            drift.append((index, text, got, want))
    return drift


class TestGoldenCorpus:
    @pytest.mark.parametrize("variant", PII_VARIANTS)
    def test_pii_detection_and_redaction_match_base(self, variant):
        assert _corpus_drift(PII_SAMPLES, variant, PII_EXPECTED, _pii_observation) == []

    @pytest.mark.parametrize("variant", VOICEMAIL_VARIANTS)
    def test_voicemail_classification_and_priority_match_base(self, variant):
        drift = _corpus_drift(
            VOICEMAIL_SAMPLES, variant, VOICEMAIL_EXPECTED, _voicemail_observation
        )
        assert drift == []

    @pytest.mark.parametrize("variant", LOCATION_VARIANTS)
    def test_location_extraction_matches_base(self, variant):
        drift = _corpus_drift(LOCATION_SAMPLES, variant, LOCATION_EXPECTED, _location_observation)
        assert drift == []

    @pytest.mark.parametrize("variant", ENTITY_VARIANTS)
    def test_speech_entities_match_base(self, variant):
        assert _corpus_drift(ENTITY_SAMPLES, variant, ENTITY_EXPECTED, _entity_observation) == []

    def test_persisted_address_fields_match_base(self):
        got = tuple(_address_observation(fields) for fields in ADDRESS_SAMPLES)
        assert got == ADDRESS_EXPECTED

    def test_voicemail_search_and_classifier_version_stay_stable(self):
        from backend.voicemail.classify import RULES_VERSION, RuleClassifier

        classifier = RuleClassifier()
        assert (classifier.kind, classifier.version, classifier.model_id) == (
            "rules",
            RULES_VERSION,
            None,
        )


EVIDENCE_TRANSCRIPT = (
    "  \u200fسلام\u060c من عل\u064a هستم از شر\u0643ت ر\u0624یا\u0640\u0640ی "
    "خان\u06c0 مدرس\u0629. کد پستی \u06f1\u06f4\u06f3\u06f5\u06f6\u06f7\u06f8\u06f9\u06f1\u06f2 "
    "و موبایل \u06f0\u06f9\u06f1\u06f2 \u06f3\u06f4\u06f5 \u06f6\u06f7\u06f8\u06f9 است\u061b "
    "کد ملی \u0660\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0669 را "
    "م\u064eی\u200cگویم.\n"
)


def _keyed(settings):
    import base64
    import os

    key = base64.b64encode(os.urandom(32)).decode()
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{key}", "pii_mapping_active_key_id": "k1"}
    )


class TestGoldenEvidence:
    def test_stored_transcript_is_the_supplied_text_code_point_for_code_point(
        self, client, email_outbox, monkeypatch, settings
    ):
        from datetime import UTC, datetime

        from sqlalchemy import select

        from backend.database.saas_models import PiiMapping, TranscriptSegment
        from backend.privacy.mapping_crypto import MappingCipher
        from backend.privacy.redaction_service import redact_revision, redacted_segments
        from backend.privacy.text_pii import PiiClass, detect
        from backend.services.transcript_service import current_revision
        from backend.tests import test_text_pii
        from backend.tests._database import inspection_session_factory

        keyed = _keyed(settings)
        supplied = EVIDENCE_TRANSCRIPT.strip()
        for required in (
            "\u064a",
            "\u0643",
            "\u0629",
            "\u06c0",
            "\u0624",
            "\u06f1",
            "\u0661",
            "\u200c",
            "\u064e",
            "\u0640",
            "\u200f",
        ):
            assert required in supplied
        monkeypatch.setattr(test_text_pii, "PII_TRANSCRIPT", EVIDENCE_TRANSCRIPT)
        tenant, meeting_id = test_text_pii._processed_with_pii(
            client, email_outbox, monkeypatch, keyed, "oracle-evidence"
        )
        with inspection_session_factory()() as db:
            revision = current_revision(
                db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id
            )
            segments = list(
                db.scalars(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.revision_id == revision.id)
                    .order_by(TranscriptSegment.sequence.asc())
                )
            )
            stored_before = [segment.text for segment in segments]
            assert stored_before == [supplied]
            assert [ord(ch) for ch in stored_before[0]] == [ord(ch) for ch in supplied]
            for variant, folded in (
                ("\u064a", "ی"),
                ("\u0643", "ک"),
                ("\u0629", "ه"),
                ("\u06c0", "ه"),
                ("\u0624", "و"),
            ):
                assert stored_before[0].count(variant) == supplied.count(variant)
                assert stored_before[0].count(folded) == supplied.count(folded)
            redaction = redact_revision(
                db,
                revision=revision,
                classes=list(PiiClass),
                settings=keyed,
                now=datetime.now(UTC),
            )
            db.commit()
            assert redaction.entity_count == len(detect(supplied)) == 3
            by_id = {segment.id: segment for segment in segments}
            mappings = list(
                db.scalars(select(PiiMapping).where(PiiMapping.redaction_id == redaction.id))
            )
            assert len(mappings) == 3
            cipher = MappingCipher.from_settings(keyed)
            for mapping in mappings:
                original = cipher.decrypt(
                    key_id=mapping.key_id,
                    nonce=mapping.nonce,
                    ciphertext=mapping.ciphertext,
                    workspace_id=mapping.workspace_id,
                    redaction_id=redaction.id,
                    placeholder=mapping.placeholder,
                )
                text = by_id[mapping.segment_id].text
                assert original == text[mapping.start_offset : mapping.end_offset]
            redacted = " ".join(row.text for row in redacted_segments(db, redaction=redaction))
            assert "[PHONE_1]" in redacted and "[IR_POSTAL_CODE_1]" in redacted
            assert "[IR_NATIONAL_ID_1]" in redacted
            assert "\u06f0\u06f9\u06f1\u06f2" not in redacted
            reread = [
                row.text
                for row in db.scalars(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.revision_id == revision.id)
                    .order_by(TranscriptSegment.sequence.asc())
                )
            ]
            assert reread == [supplied]


def _otp_challenge(client, sms_outbox, phone: str) -> tuple[str, str]:
    requested = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 202, requested.text
    return requested.json()["challenge_id"], sms_outbox[-1]["code"]


def _otp_verify(client, challenge_id: str, code: str):
    return client.post("/api/v1/auth/otp/verify", json={"challenge_id": challenge_id, "code": code})


_MIXED_SCRIPTS = (
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
    "0123456789",
)


def _mixed(code: str) -> str:
    return "".join(_MIXED_SCRIPTS[index % 3][int(ch)] for index, ch in enumerate(code))


class TestGoldenOtp:
    @pytest.mark.parametrize(
        ("phone", "spell"),
        [
            ("09120000301", lambda code: code.translate(_PERSIAN_FROM_ASCII)),
            ("09120000302", lambda code: code.translate(_ARABIC_INDIC_FROM_ANY)),
            ("09120000303", _mixed),
        ],
        ids=["persian", "arabic_indic", "mixed"],
    )
    def test_sms_code_typed_in_native_digits_signs_in(self, client, sms_outbox, phone, spell):
        challenge_id, code = _otp_challenge(client, sms_outbox, phone)
        verified = _otp_verify(client, challenge_id, spell(code))
        assert verified.status_code == 200, verified.text
        assert verified.json()["user"]["phone_e164"] == "+98" + phone[1:]
        assert verified.json()["user"]["phone_verified"] is True
        assert client.get("/api/v1/auth/me").status_code == 200

    def test_wrong_persian_digit_code_is_refused_and_the_right_one_still_works(
        self, client, sms_outbox
    ):
        challenge_id, code = _otp_challenge(client, sms_outbox, "09120000304")
        wrong = f"{(int(code) + 1) % 1_000_000:06d}".translate(_PERSIAN_FROM_ASCII)
        refused = _otp_verify(client, challenge_id, wrong)
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "invalid_otp"
        assert client.get("/api/v1/auth/me").status_code == 401
        accepted = _otp_verify(client, challenge_id, code.translate(_PERSIAN_FROM_ASCII))
        assert accepted.status_code == 200, accepted.text

    def test_other_digit_scripts_stay_outside_the_accepted_alphabet(self, client, sms_outbox):
        challenge_id, code = _otp_challenge(client, sms_outbox, "09120000305")
        for script in (
            "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
            "\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f",
        ):
            spelled = code.translate(str.maketrans("0123456789", script))
            refused = _otp_verify(client, challenge_id, spelled)
            assert refused.status_code == 422, refused.text
            assert refused.json()["error"]["code"] == "validation_error"
        assert client.get("/api/v1/auth/me").status_code == 401
        accepted = _otp_verify(client, challenge_id, code.translate(_PERSIAN_FROM_ASCII))
        assert accepted.status_code == 200, accepted.text


def _spawn(argv: list[str]) -> subprocess.Popen:
    import os

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.Popen(  # noqa: S603 - fixed argv, test-owned processes
        argv, cwd=HOMAYAR, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _run(argv: list[str], timeout: float = 300) -> subprocess.CompletedProcess:
    process = _spawn(argv)
    stdout, stderr = process.communicate(timeout=timeout)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _lines(path: Path) -> tuple[list[str], ast.Module]:
    source = path.read_text(encoding="utf-8")
    return source.splitlines(), ast.parse(source)


def _segment(lines: list[str], node: ast.AST, *, decorators: bool = True) -> str:
    start = node.lineno
    if decorators and getattr(node, "decorator_list", None):
        start = min(decorator.lineno for decorator in node.decorator_list)
    return "\n".join(lines[start - 1 : node.end_lineno])


def _label(node: ast.stmt) -> str:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    if isinstance(node, ast.Assign):
        return ",".join(ast.unparse(target) for target in node.targets)
    if isinstance(node, ast.AnnAssign):
        return ast.unparse(node.target)
    return type(node).__name__


def _fingerprint(relative: str, *, skip: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    lines, tree = _lines(BACKEND / relative)
    return [
        (_label(node), _sha(_segment(lines, node)))
        for node in tree.body
        if not isinstance(node, ast.Import | ast.ImportFrom) and _label(node) not in skip
    ]


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def _masked(lines: list[str], outer: ast.AST, regions: list[tuple[ast.AST, ast.AST]], mark: str):
    text = lines[outer.lineno - 1 : outer.end_lineno]
    base = outer.lineno
    for first, last in sorted(regions, key=lambda pair: (pair[0].lineno, pair[0].col_offset))[::-1]:
        top, bottom = first.lineno - base, last.end_lineno - base
        head = text[top][: first.col_offset]
        tail = text[bottom][last.end_col_offset :]
        text[top : bottom + 1] = [head + mark + tail]
    return "\n".join(text)


def _otp_verify_masked() -> str:
    lines, tree = _lines(BACKEND / "routers" / "v1" / "auth.py")
    function = _function(tree, "otp_verify")
    codes = [
        keyword.value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "verify_otp"
        for keyword in node.keywords
        if keyword.arg == "code"
    ]
    assert len(codes) == 1
    return _sha(_masked(lines, function, [(codes[0], codes[0])], "<CODE>"))


def _caller_number_masked() -> str:
    lines, tree = _lines(BACKEND / "voicemail" / "ingest.py")
    function = _function(tree, "normalize_caller_number")
    handler = next(node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler))
    cut = next(
        index
        for index, node in enumerate(handler.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "digits" for t in node.targets)
    )
    assert cut > 0
    return _sha(_masked(lines, function, [(handler.body[0], handler.body[cut - 1])], "<RAW>"))


def _mobile_masked() -> str:
    lines, tree = _lines(BACKEND / "security" / "normalization.py")
    function = _function(tree, "normalize_iranian_mobile")
    first = function.body[0]
    assert isinstance(first, ast.Assign)
    assert [ast.unparse(target) for target in first.targets] == ["raw"]
    return _sha(_masked(lines, function, [(first, first)], "<RAW>"))


BASE_FINGERPRINTS: dict = {
    "privacy/text_pii.py": [
        ("Expr", "9fa43d25c4fbaed236304c90"),  # HOM-164/165 docstring
        ("DETECTOR_VERSION", "d3aac593bcb04aecd6309523"),  # HOM-165 text-pii/3
        ("PiiClass", "2208d70f589c46100ffb7c83"),
        ("SUPPORTED_CLASSES", "12bdf0f16f6092f467ef3258"),
        ("_PRIORITY", "3266f57d52c974fe017bac21"),
        ("_SEP", "1c5926f4301140eb5200e211"),
        ("_NOT_ALNUM_BEFORE", "154ed5030fd1756288b6016b"),
        ("_NOT_ALNUM_AFTER", "20ed2907dd84cc34cdd5852b"),
        ("_digits", "852e2c9778b7439134f34787"),  # HOM-165 ASCII digits only
        ("_EMAIL_RE", "a29670cfebee09bc74e67aee"),
        ("_IR_MOBILE_RE", "653f8777c50f54cf2bf3b463"),
        ("_IR_LANDLINE_RE", "93c36ac31a1ad77f625031ac"),  # HOM-165
        ("_INTL_PHONE_RE", "c443fa695f2605ce94871c1f"),  # HOM-165
        ("_TEN_DIGITS_RE", "f12c90097867adca76912b90"),
        ("_CARD_RE", "504886bc0ce5dcc69329dbd7"),
        ("_IBAN_RE", "a948ca753b466b8dbef81dd7"),  # HOM-165
        ("_POSTAL_KEYWORD_RE", "3d3fd7541a6fe30549048ec8"),  # HOM-164
        ("_POSTAL_WINDOW", "74a038fb8d85fb4f9693c87a"),
        ("PiiSpan", "8687935185aa5ebaa3e16763"),
        ("_only_digits", "d8d3ac4b39284bb71083c7de"),  # HOM-165
        ("valid_national_id", "a2a44ce0ff124921f63547b1"),
        ("valid_luhn", "8b40e7530c9816227f216679"),
        ("valid_iban", "96143c494e81f9c5b0b851ef"),
        ("_canonical_phone", "406be280c94ea1e049f0f165"),
        ("detect", "50d0da6041d4c1ae23aeace5"),  # HOM-164 keyword, HOM-165 digits
        ("Replacement", "60da30a0d0afa1499c894471"),
        ("Pseudonymizer", "b5abda68aef66e9336da0554"),
        ("redact", "d9707542ae7464ec191d9235"),
        ("parse_classes", "d28eab398d417ec0aa46896d"),
    ],
    "voicemail/classify.py:tail": [
        ("_phrase_pattern", "e73b595bdcc227fa14e2ea3d"),
        ("_matches", "25d927368032b618fab160ef"),
        ("CATEGORY_RULES", "c81ce74fe64f1c383a77076d"),
        ("_CATEGORY_ORDER", "18b699872a64112f40a3c962"),
        ("CATEGORY_INTENT", "6934162d185ab363e9d69688"),
        ("TOPIC_RULES", "628233c25939123f2cf81165"),
        ("URGENCY_TERMS", "3c9879fca1fa4a06c7df66ef"),
        ("NEGATIVE_TERMS", "77c2fa441c4856a073690a4d"),
        ("CHURN_TERMS", "373c7471a67aaa8315143d77"),
        ("_CATEGORY_PATTERNS", "9efc52ff74e3d75eb7cdbfc2"),
        ("_TOPIC_PATTERNS", "b66abbb44a78b0a29d3e822d"),
        ("_URGENCY", "cb67d644a2c27fd33737b224"),
        ("_NEGATIVE", "fc75676cf4c02e25820bdbd4"),
        ("_CHURN", "82c5bc4e0c615ce6954faff3"),
        ("MIN_WORD_CHARS", "c034caa87bad83b118a449f4"),
        ("_WORD_CHARS", "c2c4562f615cd8d14fb76c09"),
        ("ClassificationInput", "cb052a988dc498a06dfd2155"),
        ("ClassificationResult", "8375680ff1a0a7c94a705984"),
        ("VoicemailClassifier", "ccd915f2e704a2c85f8adc97"),
        ("is_empty_transcript", "983ceb315899117e0ffebb65"),
        ("RuleClassifier", "0435d9c70f9195ee916f74d7"),
        ("CATEGORY_BASE_SCORE", "19636b699102909a4110380e"),
        ("URGENCY_WEIGHT", "233979836bb1951f6e0b6097"),
        ("NEGATIVE_WEIGHT", "686c52d4fd91c8932df9c8b0"),
        ("CHURN_WEIGHT", "f920af9194741c05115ec77f"),
        ("REPEAT_WEIGHT", "e502bb0e5c994bc8f3512bbb"),
        ("REPEAT_MANY_WEIGHT", "0a36c3e339bfdcefe11fe5bd"),
        ("VIP_WEIGHT", "5c67a6bc700b846e88b3578b"),
        ("REPEAT_WINDOW_HOURS", "266b00652d9044cbbb0df91c"),
        ("PRIORITY_THRESHOLDS", "519ebc227e1e119015c0af07"),
        ("PriorityContext", "05d2bf0d3311c1e46dbf3b8b"),
        ("PriorityResult", "ae6745e1e0e9456358b5b795"),
        ("prioritize", "5474f2192ba59073ff34d7c7"),
    ],
    "voicemail/classify.py:RULES_VERSION": "c5968fe6920f210e53f5f7bd",
    "security/normalization.py": [
        ("normalize_email", "cae7cb9145175775e8ffef63"),
        ("normalize_workspace_slug", "af51bfb3d2b456c9e5c09648"),
    ],
    "routers/v1/auth.py": [
        ("router", "97dfb999c3f5737e563a0561"),
        ("_client_ip", "ecee4091cfd3a155f3c18a9c"),
        ("_rate_limit", "10b23026482e1e78e639ff83"),
        ("_set_session_cookies", "b03346792fcaafba45db6581"),
        ("_clear_session_cookies", "b5383f371c1f680296c08887"),
        ("_user_response", "97731dfb70b5c932c1947fde"),
        ("_session_response", "9e0b2633c73819196522c1a6"),
        ("EmailDeliveryError", "51a915476ca1c54bee35bf00"),
        ("_send_verification_message", "04f23206f6e498a157b3c102"),
        ("_send_reset_message", "82b22e5b5657ab6553344988"),
        ("register", "18bc6b38b775adadba09cbf8"),
        ("available_auth_methods", "e4fdb700a3c39287feac91d2"),
        ("auth_methods", "02dba6ec316b12a85a623ae2"),
        ("login", "1b66a248fd426066282336f6"),
        ("refresh", "e238fe572e07ea712618f3eb"),
        ("logout", "3251f225a6cd7d3183e7bf7f"),
        ("me", "10962d9a986e2417d4e9ff10"),
        ("verify_email_endpoint", "b751a03b12a0b36d4f84826a"),
        ("VERIFICATION_RESEND_COOLDOWN_SECONDS", "725a6c4e41978242759d9fd0"),
        ("VERIFICATION_RESEND_PER_HOUR", "1822b297d1d7defaa77af846"),
        ("resend_verification_email", "4652902c2995296f6d6aa5b0"),
        ("forgot_password", "2e9c20c4a8a294db6bb966f0"),
        ("reset_password_endpoint", "ec263f62f0d4c02df9a9af4d"),
        ("_send_asanak_code", "bc6d3d402f0c81a0128a4d0f"),
        ("otp_request", "3e58dd35bae417845a9941eb"),
        ("google_start", "d14e104dc36f6d3223ce3473"),
        ("google_link_start", "82b1718e7e2f3e3ec4ba9b96"),
        ("google_callback", "1bb4860adb5475aeec5d4745"),
    ],
    "voicemail/ingest.py": [
        ("Expr", "46f10c1143fabcc0d13523a7"),
        ("EVENT_PREFIX", "502e7a2609148446e865da7d"),
        ("EXTERNAL_ID_PATTERN", "d057945996de4a5f5873430c"),
        ("VoicemailImportError", "1d0049f55b766d6ba3fcecad"),
        ("VoicemailEnvelope", "48e69e596ad3ee8110928616"),
        ("VoicemailImportResult", "079c8a7869535bf98e75ce8e"),
        ("stage_bytes", "9bcd9486cc1d4f83e25f8fde"),
        ("voicemail_for_external_id", "893cc9de90050d76af471e50"),
        ("_replay", "bbcd801c39802fbe0f681504"),
        ("import_voicemail", "8670484ea196aa3bb189d0f4"),
    ],
    "locations/textnorm.py": {
        "NormalizedText": "a23569002673f9a66934443e",
        "normalize_text": "6998062558ab04d86e3d8170",
        "is_word_char": "4231a3059d72819fe9ad268d",
    },
}
BASE_MASKED: dict[str, str] = {
    "security/normalization.py:normalize_iranian_mobile": "9d1c4b862e8e51f94b6894cf",
    "routers/v1/auth.py:otp_verify": "15d9f6646eba236af6fa751c",
    "voicemail/ingest.py:normalize_caller_number": "7ee4cb76bc47d81b36437e4d",
}
FROZEN_FILES: dict[str, str] = {
    "backend/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "backend/speech/__init__.py": (
        "fb06661f9c10622d82dc0df5b0761afa5973485755ab135687dc97443ba67ea7"
    ),
    "backend/privacy/__init__.py": (
        "7d6cda53fc9005b63ac7d16be2771f3c8ca68a7423193f10ffa8e57d7202e863"
    ),
    "backend/security/__init__.py": (
        "0b01fdf830f04b00192c0ee41546db2961fc10a6603c6367da81a9e6cfaac353"
    ),
    "backend/locations/__init__.py": (
        "4a0168ee64d27edce31d2ed1575e16e1b68fcc5f1a61d932eef513eabd8ebe0d"
    ),
    "backend/voicemail/__init__.py": (
        "3430ad4b253381c0cf56b22b0715e6d90a58fde92bb9c951d33c60649bc2d670"
    ),
    "backend/speech/dates.py": "bc88d518b1220fdb979623952e7dde521ac5fbd0b2a528b2d2af3fd5ffbcd60a",
    "backend/speech/entities.py": (
        "0839768afa68a411174e2e3085f7c38ce234d56e324fe5146fc56a3eeff52d8a"
    ),
    "backend/speech/benchmark.py": (
        "1c293a2489efdd89e065eda550dbfa893f9c31c91db8ab91b6b86bfae18ce1fb"
    ),
    "backend/speech/languages.py": (
        "980b8e389799d3f1cc0479bab4110dffea42c57384f5f4d45b560b94add6de17"
    ),
    "backend/speech/audio_profile.py": (
        "d9ad6ecbdb92c40e494a889cce68fc87cffb6fff8068c1ffcadf32e8a34bb820"
    ),
    "backend/locations/extract.py": (
        "8935540a57f1353a664471ae3b22681dc1921cfca4ad492f6a990a50c99d956d"
    ),
    "backend/locations/gazetteer.py": (
        "b39edf6b752b34da6bdb7f7efe500fcdb2567903fca0494ccd6ab4334c6fac8d"
    ),
    "backend/locations/normalize.py": (
        "88d6b65b91d4bedc9f48d69822d28c824762957a9594fe82757446f79dcb4800"
    ),
    "backend/locations/routing.py": (
        "d00cab7249c5f76f69b155ee8a5d99d30cf3eb6418a70997c17efd3bed6c74b0"
    ),
    "backend/locations/service.py": (
        "cb0d62cd98a2f59c350df988a9f4b712ff40019f5cea81c5ab3d9632ab5d1773"
    ),
    "backend/locations/validate.py": (
        "d5a89d30cc9cc00e2c52af3a4ce6cb7c3054eea983ab6113f2555fda6872e3c5"
    ),
    "backend/voicemail/pipeline.py": (
        "5397c5ec3e9a428d6e047d93615e92dd4dbb4c0861c2ce26c0b6f53a09bce62d"
    ),
    "backend/voicemail/search.py": (
        "f719e23ba9c6124ca8fdf20efa3e3863f9edee1fa3f43b565b72ec23d1b0969a"
    ),
    "backend/voicemail/models.py": (
        "45f50f49434fa6c8f8e80bf97ae33a17b6861b9db1434f2beb84636f4bf04760"
    ),
    "backend/privacy/redaction_service.py": (
        "6facb81af9244730d104e6e12a5bd3491141245dab66f144c38c14dd1b25053a"
    ),
    "backend/privacy/subject_export.py": (
        "c8bc61ffe5031bad00de4c032f442fe7e414d42557a95f3d9a791e33e47ff8c4"
    ),
    "backend/privacy/transcript_view.py": (
        "dff81b928b20306ade4325b794ef29e33d538486bc20f67d8183e536d303e261"
    ),
    "backend/privacy/mapping_crypto.py": (
        "f3bea7cd866f961b03c1e342c540c2a38ccd1402cdba76fea1f4780075e9efa3"
    ),
    "backend/schemas/auth.py": "d6ec760646b22f89bd112b0df07f4d51cee6c9ac51d10d9daa67ae95b450050d",
    "backend/services/otp_service.py": (
        "0bc30e19ec5449d3729d65033812fbf00c0dd786a915b97c9bc89251a1112214"
    ),
}
BASE_SIGNATURES: dict[str, str] = {
    "backend.speech.text_normalize:unify_arabic_letters": "(text: str) -> str",
    "backend.speech.text_normalize:strip_diacritics": "(text: str) -> str",
    "backend.speech.text_normalize:normalize_whitespace": "(text: str) -> str",
    "backend.speech.text_normalize:normalize_zwnj": "(text: str) -> str",
    "backend.speech.text_normalize:normalize_punctuation_fa": "(text: str) -> str",
    ("backend.speech.text_normalize:apply_token_map"): (
        "(text: str, table: collections.abc.Mapping[str, str]) -> str"
    ),
    "backend.speech.text_normalize:normalize_fa": (
        "(text: str, *, colloquial: collections.abc.M"
        "apping[str, str] | None = None, punctuation:"
        " bool = True) -> str"
    ),
    "backend.speech.text_normalize:normalize_en": "(text: str, **_: object) -> str",
    "backend.speech.text_normalize:normalize_az": "(text: str, **_: object) -> str",
    (
        "backend.speech.text_normalize:normalize_for_matching"
    ): "(text: str, language: str = 'fa') -> str",
    ("backend.speech.text_normalize:register_normalizer"): (
        "(language: str, func: collections.abc.Callable[..., str], description: str = '') -> None"
    ),
    "backend.speech.text_normalize:get_normalizer": (
        "(language: str) -> collections.abc.Callable[..., str]"
    ),
    "backend.speech.text_normalize:registered_languages": "() -> tuple[str, ...]",
    "backend.speech.text_normalize:normalize": (
        "(text: str, language: str = 'fa', **options: object) -> str"
    ),
    "backend.speech.text_normalize:normalize_digits": "(text: str) -> str",
    ("backend.locations.textnorm:normalize_char_stream"): (
        "(text: str) -> backend.locations.textnorm.NormalizedText"
    ),
    "backend.locations.textnorm:normalize_text": "(text: str) -> str",
    "backend.locations.textnorm:is_word_char": "(char: str) -> bool",
    ("backend.locations.textnorm:NormalizedText.span"): (
        "(self, start: int, end: int) -> tuple[int, int]"
    ),
    "backend.voicemail.classify:normalize_text": "(value: str) -> str",
    "backend.voicemail.classify:is_empty_transcript": "(text: str) -> bool",
    "backend.privacy.text_pii:normalize_digits": "(text: str) -> str",
    "backend.privacy.text_pii:detect": (
        "(text: str, classes: collections.abc.Iterabl"
        "e[backend.privacy.text_pii.PiiClass] = [<Pii"
        "Class.BANK_CARD: 'bank_card'>, <PiiClass.EMA"
        "IL: 'email'>, <PiiClass.IBAN: 'iban'>, <PiiC"
        "lass.IR_NATIONAL_ID: 'ir_national_id'>, <Pii"
        "Class.IR_POSTAL_CODE: 'ir_postal_code'>, <Pi"
        "iClass.PHONE: 'phone'>]) -> list[backend.pri"
        "vacy.text_pii.PiiSpan]"
    ),
    "backend.privacy.text_pii:redact": (
        "(text: str, classes: collections.abc.Iterabl"
        "e[backend.privacy.text_pii.PiiClass] = [<Pii"
        "Class.BANK_CARD: 'bank_card'>, <PiiClass.EMA"
        "IL: 'email'>, <PiiClass.IBAN: 'iban'>, <PiiC"
        "lass.IR_NATIONAL_ID: 'ir_national_id'>, <Pii"
        "Class.IR_POSTAL_CODE: 'ir_postal_code'>, <Pi"
        "iClass.PHONE: 'phone'>], *, pseudonymizer: b"
        "ackend.privacy.text_pii.Pseudonymizer | None"
        " = None) -> tuple[str, list[backend.privacy."
        "text_pii.Replacement]]"
    ),
    "backend.privacy.text_pii:parse_classes": (
        "(values: collections.abc.Iterable[str]) -> list[backend.privacy.text_pii.PiiClass]"
    ),
    "backend.privacy.text_pii:valid_national_id": "(digits: str) -> bool",
    "backend.privacy.text_pii:valid_luhn": "(digits: str) -> bool",
    "backend.privacy.text_pii:valid_iban": "(compact: str) -> bool",
    "backend.privacy.text_pii:Pseudonymizer.token": (
        "(self, pii_class: backend.privacy.text_pii.PiiClass, value: str) -> str"
    ),
    "backend.security.normalization:normalize_email": "(value: str) -> str",
    "backend.security.normalization:normalize_iranian_mobile": "(value: str) -> str",
    "backend.security.normalization:normalize_workspace_slug": "(value: str) -> str",
    ("backend.voicemail.ingest:normalize_caller_number"): (
        "(value: str | None) -> tuple[str | None, bool]"
    ),
}
BASE_DATACLASS_FIELDS: dict[str, tuple[tuple[str, ...], bool]] = {
    "backend.speech.text_normalize:NormalizerEntry": (("language", "func", "description"), True),
    "backend.locations.textnorm:NormalizedText": (("text", "offsets", "original"), True),
    "backend.privacy.text_pii:PiiSpan": (("start", "end", "pii_class", "value"), True),
    "backend.privacy.text_pii:Replacement": (
        ("start", "end", "pii_class", "placeholder", "value"),
        True,
    ),
}
BASE_NODE_IDS: tuple[str, ...] = (
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0639\\"
        "u0644\\u064a-\\u0639\\u0644\\u06cc]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0643\\"
        "u062a\\u0627\\u0628-\\u06a9\\u062a\\u0627\\u"
        "0628]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0645\\"
        "u062f\\u0631\\u0633\\u0629-\\u0645\\u062f\\u"
        "0631\\u0633\\u0647]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0623\\"
        "u062d\\u0645\\u062f-\\u0627\\u062d\\u0645\\u"
        "062f]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0625\\"
        "u064a\\u0631\\u0627\\u0646-\\u0627\\u06cc\\u"
        "0631\\u0627\\u0646]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0645\\"
        "u0624\\u0633\\u0633\\u0647-\\u0645\\u0648\\u"
        "0633\\u0633\\u0647]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0645\\"
        "u0633\\u0626\\u0644\\u0647-\\u0645\\u0633\\u"
        "0626\\u0644\\u0647]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0633\\"
        "u0640\\u0644\\u0627\\u0645-\\u0633\\u0644\\u"
        "0627\\u0645]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u06a9\\"
        "u0650\\u062a\\u0627\\u0628\\u0652-\\u06a9\\u"
        "062a\\u0627\\u0628]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u0645\\"
        "u064f\\u062d\\u064e\\u0645\\u064e\\u0651\\u0"
        "62f-\\u0645\\u062d\\u0645\\u062f]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[\\u06f1\\"
        "u06f2\\u06f3 \\u0648 \\u0664\\u0665\\u0666-1"
        "23 \\u0648 456]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_normalize_fa_letters_digits_space[  \\u0633"
        "\\u0644\\u0627\\u0645   \\u062f\\u0646\\u06c"
        "c\\u0627 \\n \\u062e\\u0648\\u0628\\u06cc -"
        "\\u0633\\u0644\\u0627\\u0645 \\u062f\\u0646"
        "\\u06cc\\u0627 \\u062e\\u0648\\u0628\\u06cc]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u0645\\u06cc \\u062e\\u0648\\u0627\\"
        "u0647\\u0645-\\u0645\\u06cc\\u200c\\u062e\\u"
        "0648\\u0627\\u0647\\u0645]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u0646\\u0645\\u06cc \\u062f\\u0627\\"
        "u0646\\u0645-\\u0646\\u0645\\u06cc\\u200c\\u"
        "062f\\u0627\\u0646\\u0645]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u0645\\u06cc\\u0631\\u0648\\u0645-\\"
        "u0645\\u06cc\\u0631\\u0648\\u0645]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u06a9\\u062a\\u0627\\u0628 \\u0647\\"
        "u0627-\\u06a9\\u062a\\u0627\\u0628\\u200c\\u"
        "0647\\u0627]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u06a9\\u062a\\u0627\\u0628 \\u0647\\"
        "u0627\\u06cc \\u0645\\u0646-\\u06a9\\u062a\\"
        "u0627\\u0628\\u200c\\u0647\\u0627\\u06cc \\u"
        "0645\\u0646]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u0645\\u06cc\\u200c\\u200c\\u0631\\u"
        "0648\\u0645-\\u0645\\u06cc\\u200c\\u0631\\u0"
        "648\\u0645]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u0645\\u06cc \\u200c \\u0631\\u0648"
        "\\u0645-\\u0645\\u06cc\\u200c\\u0631\\u0648"
        "\\u0645]"
    ),
    (
        "backend/tests/test_speech_normalize.py::test"
        "_zwnj[\\u200c\\u0633\\u0644\\u0627\\u0645\\u"
        "200c-\\u0633\\u0644\\u0627\\u0645]"
    ),
    "backend/tests/test_speech_normalize.py::test_zwnj_does_not_touch_standalone_mi_word",
    "backend/tests/test_speech_normalize.py::test_punctuation_fa",
    "backend/tests/test_speech_normalize.py::test_colloquial_is_opt_in",
    "backend/tests/test_speech_normalize.py::test_colloquial_custom_table",
    "backend/tests/test_speech_normalize.py::test_colloquial_arabic_letters_resolved_first",
    "backend/tests/test_speech_normalize.py::test_english_and_azerbaijani",
    "backend/tests/test_speech_normalize.py::test_registry",
    "backend/tests/test_speech_normalize.py::test_normalize_for_matching_collapses_variants",
    "backend/tests/test_speech_normalize.py::test_idempotent",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_normalizer_maps_arabic_letters_zwnj_and_di"
        "gits_keeping_offsets"
    ),
    "backend/tests/test_locations_extract.py::test_normalizer_drops_diacritics_and_tatweel",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0633\\u0644\\u062"
        "7\\u0645\\u060c \\u0627\\u0632 \\u0627\\u063"
        "5\\u0641\\u0647\\u0627\\u0646 \\u0632\\u0646"
        "\\u06af \\u0645\\u06cc\\u200c\\u0632\\u0646"
        "\\u0645-isfahan]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0645\\u0646 \\u06"
        "33\\u0627\\u0643\\u0646 \\u062a\\u0647\\u063"
        "1\\u0627\\u0646 \\u0647\\u0633\\u062a\\u0645"
        "-tehran]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0634\\u0647\\u063"
        "1 \\u06a9\\u0627\\u0634\\u0627\\u0646\\u060c"
        " \\u0627\\u0633\\u062a\\u0627\\u0646 \\u0627"
        "\\u0635\\u0641\\u0647\\u0627\\u0646-kashan]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u062f\\u0631 \\u06"
        "34\\u064a\\u0631\\u0627\\u0632 \\u0646\\u064"
        "5\\u0627\\u06cc\\u0646\\u062f\\u06af\\u06cc "
        "\\u062f\\u0627\\u0631\\u06cc\\u062f\\u061f-s"
        "hiraz]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u062e\\u0631\\u064"
        "5\\u200c\\u0622\\u0628\\u0627\\u062f \\u0647"
        "\\u0633\\u062a\\u0645-khorramabad]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u062e\\u0631\\u064"
        "5\\u0622\\u0628\\u0627\\u062f \\u0647\\u0633"
        "\\u062a\\u0645-khorramabad]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0627\\u0632 \\u06"
        "28\\u0646\\u062f\\u0631 \\u0639\\u0628\\u062"
        "7\\u0633 \\u062a\\u0645\\u0627\\u0633 \\u064"
        "5\\u06cc\\u200c\\u06af\\u06cc\\u0631\\u0645-"
        "bandar_abbas]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0628\\u0646\\u062"
        "f\\u0631\\u0639\\u0628\\u0627\\u0633-bandar_"
        "abbas]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[I am calling from Ma"
        "shhad-mashhad]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[customer in tabriz-t"
        "abriz]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0646\\u0648\\u063"
        "1\\u0622\\u0628\\u0627\\u062f \\u0641\\u0627"
        "\\u0631\\u0633-nurabad_mamasani]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0646\\u0648\\u063"
        "1\\u0622\\u0628\\u0627\\u062f \\u0644\\u0631"
        "\\u0633\\u062a\\u0627\\u0646-nurabad_delfan]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0622\\u0633\\u062"
        "a\\u0627\\u0646\\u0647 \\u0627\\u0634\\u0631"
        "\\u0641\\u06cc\\u0647 \\u0647\\u0633\\u062a"
        "\\u0645-astaneh_ashrafiyeh]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_city_mentions_resolve[\\u0645\\u06cc\\u062"
        "f\\u0627\\u0646 \\u0648\\u0646\\u06a9 \\u062"
        "a\\u0647\\u0631\\u0627\\u0646-tehran]"
    ),
    "backend/tests/test_locations_extract.py::test_candidates_carry_original_spans",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_province_prefix_restricts_to_province_and_"
        "falls_back_to_centroid"
    ),
    "backend/tests/test_locations_extract.py::test_capital_name_is_one_intent_not_ambiguity",
    "backend/tests/test_locations_extract.py::test_ambiguous_city_name_is_never_silently_resolved",
    "backend/tests/test_locations_extract.py::test_ambiguous_region_name",
    "backend/tests/test_locations_extract.py::test_astaneh_alone_is_ambiguous_between_provinces",
    "backend/tests/test_locations_extract.py::test_two_different_cities_need_clarification",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_street_named_after_a_city_is_a_landmark_no"
        "t_the_city"
    ),
    "backend/tests/test_locations_extract.py::test_full_persian_address_components",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_landmarks_are_unresolved_text[\\u0645\\u06"
        "cc\\u062f\\u0627\\u0646 \\u0622\\u0632\\u062"
        "7\\u062f\\u06cc-square-\\u0622\\u0632\\u0627"
        "\\u062f\\u06cc]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_landmarks_are_unresolved_text[\\u0628\\u06"
        "44\\u0648\\u0627\\u0631 \\u06a9\\u0634\\u062"
        "7\\u0648\\u0631\\u0632-boulevard-\\u06a9\\u0"
        "634\\u0627\\u0648\\u0631\\u0632]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_landmarks_are_unresolved_text[\\u0628\\u06"
        "32\\u0631\\u06af\\u0631\\u0627\\u0647 \\u064"
        "7\\u0645\\u062a-highway-\\u0647\\u0645\\u062"
        "a]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_landmarks_are_unresolved_text[near Valiasr"
        " St-street-valiasr]"
    ),
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_explicit_coordinates_with_persian_decimal_"
        "separator"
    ),
    "backend/tests/test_locations_extract.py::test_out_of_range_coordinates_are_ignored",
    "backend/tests/test_locations_extract.py::test_invalid_postal_code_is_flagged",
    "backend/tests/test_locations_extract.py::test_nothing_found_asks_for_city",
    "backend/tests/test_locations_extract.py::test_gazetteer_has_all_31_provinces_and_aliases",
    "backend/tests/test_locations_extract.py::test_postal_code_validation[1435678913-True]",
    "backend/tests/test_locations_extract.py::test_postal_code_validation[0435678913-False]",
    "backend/tests/test_locations_extract.py::test_postal_code_validation[1425678913-False]",
    "backend/tests/test_locations_extract.py::test_postal_code_validation[111111111-False]",
    "backend/tests/test_locations_extract.py::test_postal_code_validation[1111111111-False]",
    "backend/tests/test_locations_extract.py::test_postal_code_validation[14356a8913-False]",
    "backend/tests/test_locations_extract.py::test_normalize_address_errors_and_warnings",
    "backend/tests/test_locations_extract.py::test_haversine_tehran_isfahan_is_about_340_km",
    "backend/tests/test_locations_extract.py::test_bounding_box_contains_radius",
    "backend/tests/test_locations_extract.py::test_open_now_uses_tehran_time",
    "backend/tests/test_locations_extract.py::test_opening_hours_validation",
    "backend/tests/test_locations_extract.py::test_rank_by_distance_and_skip_inactive_and_unplaced",
    "backend/tests/test_locations_extract.py::test_rank_filters_kind_tag_and_open_now",
    "backend/tests/test_locations_extract.py::test_prefer_same_city_beats_a_closer_other_city",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_route_needs_clarification_for_ambiguous_an"
        "d_unresolved_text"
    ),
    "backend/tests/test_locations_extract.py::test_route_city_text_uses_bbox_and_returns_ranking",
    (
        "backend/tests/test_locations_extract.py::tes"
        "t_route_province_only_is_low_confidence_with"
        "_city_question"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_normalizer_tolerates_arabic_letters_zwnj_"
        "digits_and_diacritics"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "33\\u0644\\u0627\\u0645\\u060c \\u0627\\u063"
        "2 \\u062e\\u062f\\u0645\\u0627\\u062a \\u063"
        "4\\u0645\\u0627 \\u062e\\u06cc\\u0644\\u06cc"
        " \\u0646\\u0627\\u0631\\u0627\\u0636\\u06cc "
        "\\u0647\\u0633\\u062a\\u0645 \\u0648 \\u0634"
        "\\u06a9\\u0627\\u06cc\\u062a \\u062f\\u0627"
        "\\u0631\\u0645-complaint]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "33\\u0644\\u0627\\u0645\\u060c \\u0642\\u06c"
        "c\\u0645\\u062a \\u067e\\u06a9\\u06cc\\u062c"
        " \\u0633\\u0627\\u0632\\u0645\\u0627\\u0646"
        "\\u06cc \\u0686\\u0646\\u062f\\u0647\\u061f "
        "\\u0645\\u06cc\\u200c\\u062e\\u0648\\u0627\\"
        "u0633\\u062a\\u0645 \\u062e\\u0631\\u06cc\\u"
        "062f \\u06a9\\u0646\\u0645-sales_inquiry]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "27\\u06cc\\u0646\\u062a\\u0631\\u0646\\u062a"
        " \\u0645\\u0627 \\u0627\\u0632 \\u0635\\u062"
        "8\\u062d \\u0642\\u0637\\u0639 \\u0627\\u063"
        "3\\u062a \\u0648 \\u06a9\\u0627\\u0631 \\u06"
        "46\\u0645\\u06cc\\u200c\\u06a9\\u0646\\u062f"
        "-support]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "45\\u06cc\\u200c\\u062e\\u0648\\u0627\\u0633"
        "\\u062a\\u0645 \\u0628\\u0631\\u0627\\u06cc "
        "\\u0647\\u0641\\u062a\\u0647 \\u0628\\u0639"
        "\\u062f \\u0646\\u0648\\u0628\\u062a \\u0631"
        "\\u0632\\u0631\\u0648 \\u06a9\\u0646\\u0645-"
        "appointment]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "44\\u0637\\u0641\\u0627\\u064b \\u0628\\u062"
        "7 \\u0627\\u06cc\\u0646 \\u0634\\u0645\\u062"
        "7\\u0631\\u0647 \\u062a\\u0645\\u0627\\u0633"
        " \\u0628\\u06af\\u06cc\\u0631\\u06cc\\u062f-"
        "callback_request]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "41\\u0627\\u06a9\\u062a\\u0648\\u0631 \\u064"
        "5\\u0627\\u0647 \\u0642\\u0628\\u0644 \\u062"
        "f\\u0648\\u0628\\u0627\\u0631 \\u0627\\u0632"
        " \\u06a9\\u0627\\u0631\\u062a \\u06a9\\u0633"
        "\\u0631 \\u0634\\u062f\\u0647-billing]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[\\u06"
        "2a\\u0628\\u0631\\u06cc\\u06a9! \\u0634\\u06"
        "45\\u0627 \\u062f\\u0631 \\u0642\\u0631\\u06"
        "39\\u0647 \\u06a9\\u0634\\u06cc \\u0628\\u06"
        "31\\u0646\\u062f\\u0647 \\u0634\\u062f\\u06c"
        "c\\u062f-spam]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[Hi, I"
        " am very unhappy, this is unacceptable, I wa"
        "nt to file a complaint-complaint]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[Hello"
        ", how much is the premium plan? I'd like a q"
        "uote-sales_inquiry]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[Pleas"
        "e call me back when you get this-callback_re"
        "quest]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[I was"
        " charged twice on my last invoice-billing]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[I'd l"
        "ike to reschedule my appointment-appointment"
        "]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_rule_categories_persian_and_english[Just "
        "wanted to say hello to the team-other]"
    ),
    "backend/tests/test_voicemail_classify.py::test_arabic_letter_variants_classify_like_persian",
    "backend/tests/test_voicemail_classify.py::test_topics_are_detected",
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_empty_transcript_needs_review_and_is_neve"
        "r_fabricated[]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_empty_transcript_needs_review_and_is_neve"
        "r_fabricated[   ]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_empty_transcript_needs_review_and_is_neve"
        "r_fabricated[\\u2026]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_empty_transcript_needs_review_and_is_neve"
        "r_fabricated[\\u0627\\u0650]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_empty_transcript_needs_review_and_is_neve"
        "r_fabricated[uh]"
    ),
    (
        "backend/tests/test_voicemail_classify.py::te"
        "st_empty_transcript_needs_review_and_is_neve"
        "r_fabricated[?!]"
    ),
    "backend/tests/test_voicemail_classify.py::test_priority_reasons_are_explained",
    "backend/tests/test_voicemail_classify.py::test_priority_levels_and_repeat_caller_weight",
    "backend/tests/test_voicemail_classify.py::test_spam_is_low_priority_even_with_urgency",
    "backend/tests/test_voicemail_classify.py::test_classifier_provenance_is_versioned_rules",
    "backend/tests/test_voicemail_classify.py::test_classification_is_deterministic",
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u062f \\u0645\\u0644\\u06cc 0012345679-expec"
        "ted0]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u062f \\u0645\\u0644\\u06cc \\u06f0\\u06f0\\"
        "u06f1\\u06f2\\u06f3\\u06f4\\u06f5\\u06f6\\u0"
        "6f7\\u06f9-expected1]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u062f \\u0645\\u0644\\u06cc \\u0660\\u0660\\"
        "u0661\\u0662\\u0663\\u0664\\u0665\\u0666\\u0"
        "667\\u0669-expected2]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u062f \\u0645\\u0644\\u06cc 001-234567-9-exp"
        "ected3]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u0645\\"
        "u0648\\u0628\\u0627\\u06cc\\u0644 \\u06f0\\u"
        "06f9\\u06f1\\u06f2\\u06f3\\u06f4\\u06f5\\u06"
        "f6\\u06f7\\u06f8\\u06f9-expected4]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u0645\\"
        "u0648\\u0628\\u0627\\u06cc\\u0644 \\u06f0\\u"
        "06f9\\u06f1\\u06f2 \\u06f3\\u06f4\\u06f5 \\u"
        "06f6\\u06f7\\u06f8\\u06f9-expected5]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[mobile +9"
        "8 912 345 6789.-expected6]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[mobile +9"
        "89123456789-expected7]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[mobile 00"
        "98-912-345-6789-expected8]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u062a\\"
        "u0644\\u0641\\u0646 \\u06f0\\u06f2\\u06f1-\\"
        "u06f8\\u06f8\\u06f7\\u06f7\\u06f6\\u06f6\\u0"
        "6f5\\u06f5-expected9]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u062a\\"
        "u0644\\u0641\\u0646 (\\u06f0\\u06f2\\u06f1) "
        "\\u06f8\\u06f8\\u06f7\\u06f7 \\u06f6\\u06f6"
        "\\u06f5\\u06f5-expected10]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[call +1 2"
        "02 555 0143 today-expected11]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[card 6037"
        "991123456786-expected12]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u0627\\u0631\\u062a \\u06f6\\u06f0\\u06f3\\u"
        "06f7 \\u06f9\\u06f9\\u06f1\\u06f1 \\u06f2\\u"
        "06f3\\u06f4\\u06f5 \\u06f6\\u06f7\\u06f8\\u0"
        "6f6-expected13]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u0634\\"
        "u0628\\u0627 IR630190000000101234567890-expe"
        "cted14]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u0634\\"
        "u0628\\u0627 IR63 0190 0000 0010 1234 5678 9"
        "0-expected15]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[iban ir63"
        "0190000000101234567890-expected16]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[mail Ali."
        "Rezaei+crm@Example.co.ir now-expected17]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u0627\\"
        "u06cc\\u0645\\u06cc\\u0644: sara_k@mail.exam"
        "ple.com-expected18]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u062f \\u067e\\u0633\\u062a\\u06cc \\u06f1\\"
        "u06f4\\u06f3\\u06f5\\u06f6\\u06f7\\u06f8\\u0"
        "6f9\\u06f1\\u06f2 \\u0627\\u0633\\u062a-expe"
        "cted19]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[postal co"
        "de: 14356-78912-expected20]"
    ),
    (
        "backend/tests/test_text_pii.py::test_support"
        "ed_classes_and_formatting_variants[\\u06a9\\"
        "u062f\\u067e\\u0633\\u062a\\u06cc 0012345679"
        "-expected21]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u0645\\u0628\\u064"
        "4\\u063a \\u06f1\\u06f2\\u06f3\\u06f4\\u06f5"
        "\\u06f6\\u06f7\\u06f8\\u06f9\\u06f0 \\u0631"
        "\\u06cc\\u0627\\u0644 \\u067e\\u0631\\u062f"
        "\\u0627\\u062e\\u062a \\u0634\\u062f]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u062a\\u0627\\u063"
        "1\\u06cc\\u062e \\u06f1\\u06f4\\u06f0\\u06f3"
        "/\\u06f0\\u06f5/\\u06f1\\u06f2 \\u0633\\u062"
        "7\\u0639\\u062a \\u06f1\\u06f0:\\u06f3\\u06f"
        "0]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u0633\\u0641\\u062"
        "7\\u0631\\u0634 INV1234567890 \\u062b\\u0628"
        "\\u062a \\u0634\\u062f]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[number 1234567890123"
        "456 is not a card]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[0000000000 and 11111"
        "11111]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[version 2.4.1 build "
        "20260923]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u0642\\u06cc\\u064"
        "5\\u062a \\u06f4\\u06f5,\\u06f0\\u06f0\\u06f"
        "0,\\u06f0\\u06f0\\u06f0 \\u062a\\u0648\\u064"
        "5\\u0627\\u0646]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[the IBAN IR000190000"
        "000101234567890 is mistyped]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[user@localhost is no"
        "t a routable address]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u06f9\\u06f1\\u06f"
        "2\\u06f3\\u06f4\\u06f5\\u06f6\\u06f7\\u06f8"
        "\\u06f9]"
    ),
    "backend/tests/test_text_pii.py::test_hard_negatives_are_not_flagged[x0012345679y]",
    "backend/tests/test_text_pii.py::test_hard_negatives_are_not_flagged[00123456]",
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u0648\\u0627\\u062"
        "a\\u0633\\u0627\\u067e +\\u06f9\\u06f8\\u06f"
        "9\\u06f1\\u06f9\\u06f8\\u06f7\\u06f6\\u06f5"
        "\\u06f4\\u06f3\\u06f2\\u06f1]"
    ),
    (
        "backend/tests/test_text_pii.py::test_hard_ne"
        "gatives_are_not_flagged[\\u06a9\\u062f \\u06"
        "7e\\u0633\\u062a\\u06cc \\u0631\\u0627 \\u06"
        "28\\u0639\\u062f\\u0627 \\u0645\\u06cc\\u200"
        "c\\u06af\\u0648\\u06cc\\u0645\\u061b \\u0641"
        "\\u0639\\u0644\\u0627 \\u0645\\u0628\\u0644"
        "\\u063a \\u06f1\\u06f2\\u06f3\\u06f4\\u06f5"
        "\\u06f6\\u06f7\\u06f8\\u06f9\\u06f0 \\u0631"
        "\\u06cc\\u0627\\u0644]"
    ),
    (
        "backend/tests/test_text_pii.py::test_configu"
        "red_classes_limit_detection_and_unknown_clas"
        "ses_are_refused"
    ),
    (
        "backend/tests/test_text_pii.py::test_placeho"
        "lders_are_stable_per_value_and_offsets_refer"
        "_to_the_original"
    ),
    "backend/tests/test_text_pii.py::test_synthetic_annotated_set_meets_the_engineering_gate",
    (
        "backend/tests/test_text_pii.py::test_mapping"
        "_cipher_binds_tenant_redaction_and_placehold"
        "er_and_rotates"
    ),
    "backend/tests/test_text_pii.py::test_malformed_keyrings_are_refused[k1]",
    "backend/tests/test_text_pii.py::test_malformed_keyrings_are_refused[k1:not-base64!]",
    "backend/tests/test_text_pii.py::test_malformed_keyrings_are_refused[k1:c2hvcnQ=]",
    "backend/tests/test_text_pii.py::test_malformed_keyrings_are_refused[:abc]",
    "backend/tests/test_text_pii.py::test_settings_require_the_active_key_to_exist",
    (
        "backend/tests/test_text_pii.py::test_redacte"
        "d_version_keeps_originals_only_as_ciphertext"
        "_and_reveal_is_audited"
    ),
    "backend/tests/test_text_pii.py::test_without_a_key_nothing_original_is_retained",
    "backend/tests/test_text_pii.py::test_the_purge_removes_redacted_versions_and_mappings",
    "backend/tests/test_text_pii.py::test_workspace_policy_is_explicit_and_validated",
    "backend/tests/test_text_pii.py::test_member_cannot_change_the_privacy_policy",
    "backend/tests/test_text_pii.py::test_rls_binds_redactions_and_mappings_to_the_tenant",
)
BASE_TEST_STATEMENTS: dict[str, frozenset[str]] = {
    "tests/test_speech_normalize.py": frozenset(
        {
            "19ecd87cc5043f0b388ecfd0",
            "205cade0ef8b4d22464b0518",
            "4d005d3a99ad3b164a6ec7a8",
            "51aa9b953718f3d5f977a572",
            "586c05674bed51156b077c30",
            "6161a8d5500791088314c333",
            "8a53b0b3b05300cd089055f3",
            "90cd387c29b820192d34f755",
            "90febcef2099266ba36e9d98",
            "9c5f04c7829023ab4ab1a573",
            "9ecd4f1e82c4a9bbaec07ab1",
            "eb0c14f4aeec75593375594b",
        }
    ),
    "tests/test_locations_extract.py": frozenset(
        {
            "04b87f73e7cbbc9db2aba65d",
            "06e9b9f5ec95025aee043960",
            "07cf96d3d9006f125dc6ea5a",
            "0aef52e781a2932985cab76d",
            "155d2f36f1a6d898bf7d4800",
            "1f016dc3cc09e2b6bfd14e45",
            "2f71ac60740973a0beb2cb34",
            "3086d90f4be20f0102bdcede",
            "309cf06824b314f5d6d0137c",
            "3106515e8ecd79ac265ac90d",
            "38aec1e165bdd7aac23cf5c6",
            "3dd8fee8fea6ddc983063f5a",
            "5198d89f22b734c38be14a15",
            "51fdf1463f76e00e8be54196",
            "5dae2fefa1a7f969e83a5664",
            "6dfc71e5c2500a8e16762937",
            "6e3625590490940f96cfd79b",
            "7c5653cabd09d48545a7ef35",
            "7d3cf89aa9dba8f301400cfc",
            "816800869e2dbe578dd31b86",
            "8ae9d5f4f08e23692e0086f8",
            "8b97dd81d4efdfa2137bf8ab",
            "91d01530da0dfed1fc092754",
            "973926fab51a550bcec351ea",
            "980be2cc0af3784a1e606cde",
            "a219bd60241da694e54b5c50",
            "a8cb0e6a4c66ab438a04dc3c",
            "ae1f7087bfeee57c3884c343",
            "b5fa0d47194f59c10492ab90",
            "e4b711d7ebdb0c1736e16cd9",
            "e6251e119f9608ce02e1f697",
            "e8a3dbe984f05b809e9f7259",
            "fde75cee04314ad28d8e93da",
            "ff63d5fbab261a4264050cd8",
            "fff7fdd4df33788cce197574",
        }
    ),
    "tests/test_voicemail_classify.py": frozenset(
        {
            "112b4e58ac9a3bc247c9e659",
            "17771a07459471cfbcb16d82",
            "1aa6225f4420360df6f3a82e",
            "3016da4d14f1d00f3df826ba",
            "35e9200075e4cbe078758966",
            "424bf35029c323259349200b",
            "4db9718232c3ebe8b4a1b203",
            "588a683acf307de807b86e61",
            "73a1326a8b8fffc64c18635a",
            "75ac7b0f421ce8a952bb080f",
            "d9544a2d982c547f18395ce2",
            "f7fa809fbe18445017cbd68b",
        }
    ),
    "tests/test_text_pii.py": frozenset(
        {
            "01f8f522f4b2d2fc09e89a0c",
            "11cf128e0bcedac5696c3aed",
            "1c4194fb3d9bd696c1e3f737",
            "265733c826b6cff9ecd2080f",
            "322777d6a10301e8d185f8d4",
            "3569504167dd304b571785bf",
            "4ba881c53b6a87fb8593c42e",
            "5fe46e0a60e15032479afa6b",
            "644c09bed664e6ac5294d001",
            "734fc942a766b790965ae30b",
            "76aea9eff07483af9f13d2fe",
            "7b9a1072ec7d106a68903238",
            "7e07981df0cb1c5be0534604",
            "7fba31534f5b605f0ab0f426",
            "804eb93d98b0b87afc34802c",
            "84ab43c78ace1f8560343f9a",
            "9c067764c0f04aca3b556ba6",
            "a6ebdde4550a6f940a510678",
            "a81dea28385b44ffe0acbf2d",
            "bbec63f6d539e6000425d2b1",
            "c5ec640832d8ac430e973080",
            "d5f5c0bce78f56e15057cef5",
            "d7896b4a8f3801c28dae973b",
            "e5a887c2310539f3d59ff3ab",
            "e80adb59442d653dd576131e",
            "ed4f83ca5a0baec36600bd93",
            "efe091a0a53942c740536e1b",
            "f4dd16ba2b7eb794e600f027",
            "fac0193e023ba7fca541ed7f",
            "fbe99e9e6dce5e932bee2b46",
            "fc060cdcf39b786fff0a161a",
            "ff42f074142914515dccc473",
        }
    ),
}
BASE_FORMAT_DRIFT: dict[str, frozenset[str]] = {
    "security/normalization.py": frozenset(
        {
            "",
        }
    ),
    "routers/v1/auth.py": frozenset(
        {
            '            db, intent="link", link_user_id=actor.user.id, settings=settings',
            "        )",
            "        url = start_google_oauth(",
            (
                '    "/verify-email/resend", response_model=V'
                "erificationResendResponse, status_code=202"
            ),
            "    db: Session, user: User, access_expires_in: int",
            "    user: User, token: str, settings: Settings",
            ")",
            ") -> None:",
            ") -> SessionResponse:",
            "@router.post(",
            "async def _send_verification_message(",
            "def _session_response(",
        }
    ),
}
BASE_TEST_PII_FUNCTIONS: frozenset[str] = frozenset(
    {
        "_found",
        "_key",
        "_processed_with_pii",
        "card",
        "iban",
        "keyed_settings",
        "national_id",
        "test_configured_classes_limit_detection_and_unknown_classes_are_refused",
        "test_hard_negatives_are_not_flagged",
        "test_malformed_keyrings_are_refused",
        "test_mapping_cipher_binds_tenant_redaction_and_placeholder_and_rotates",
        "test_member_cannot_change_the_privacy_policy",
        "test_placeholders_are_stable_per_value_and_offsets_refer_to_the_original",
        "test_redacted_version_keeps_originals_only_as_ciphertext_and_reveal_is_audited",
        "test_rls_binds_redactions_and_mappings_to_the_tenant",
        "test_settings_require_the_active_key_to_exist",
        "test_supported_classes_and_formatting_variants",
        "test_synthetic_annotated_set_meets_the_engineering_gate",
        "test_the_purge_removes_redacted_versions_and_mappings",
        "test_without_a_key_nothing_original_is_retained",
        "test_workspace_policy_is_explicit_and_validated",
    }
)


def _classify_tail() -> list[tuple[str, str]]:
    full = _fingerprint("voicemail/classify.py")
    start = [label for label, _ in full].index("_phrase_pattern")
    return full[start:]


def _statement_hash(relative: str, label: str) -> str:
    return dict(_fingerprint(relative))[label]


class TestGoldenFrozenSource:
    def test_detector_logic_is_byte_identical(self):
        current = _fingerprint(
            "privacy/text_pii.py", skip=frozenset({"_DIGITS", "normalize_digits"})
        )
        assert current == BASE_FINGERPRINTS["privacy/text_pii.py"]

    def test_voicemail_rules_classifier_and_priority_are_byte_identical(self):
        assert _classify_tail() == BASE_FINGERPRINTS["voicemail/classify.py:tail"]
        assert (
            _statement_hash("voicemail/classify.py", "RULES_VERSION")
            == BASE_FINGERPRINTS["voicemail/classify.py:RULES_VERSION"]
        )

    def test_security_normalization_changes_only_the_digit_step(self):
        current = _fingerprint(
            "security/normalization.py",
            skip=frozenset({"_PERSIAN_DIGITS", "_ARABIC_DIGITS", "normalize_iranian_mobile"}),
        )
        assert current == BASE_FINGERPRINTS["security/normalization.py"]
        assert _mobile_masked() == BASE_MASKED["security/normalization.py:normalize_iranian_mobile"]

    def test_locations_offset_contract_is_byte_identical(self):
        current = dict(_fingerprint("locations/textnorm.py"))
        for label in ("NormalizedText", "normalize_text", "is_word_char"):
            assert current[label] == BASE_FINGERPRINTS["locations/textnorm.py"][label], label

    def test_granted_auth_change_is_limited_to_the_otp_digit_translation(self):
        current = _fingerprint("routers/v1/auth.py", skip=frozenset({"otp_verify"}))
        assert current == BASE_FINGERPRINTS["routers/v1/auth.py"]
        assert _otp_verify_masked() == BASE_MASKED["routers/v1/auth.py:otp_verify"]

    def test_granted_ingest_change_is_limited_to_the_caller_digit_translation(self):
        current = _fingerprint("voicemail/ingest.py", skip=frozenset({"normalize_caller_number"}))
        assert current == BASE_FINGERPRINTS["voicemail/ingest.py"]
        assert _caller_number_masked() == BASE_MASKED["voicemail/ingest.py:normalize_caller_number"]

    @pytest.mark.parametrize("relative", sorted(FROZEN_FILES))
    def test_files_outside_the_unit_are_unchanged(self, relative):
        content = (HOMAYAR / relative).read_bytes()
        assert hashlib.sha256(content).hexdigest() == FROZEN_FILES[relative]


PUBLIC_API = {
    "backend.speech.text_normalize": (
        "unify_arabic_letters",
        "strip_diacritics",
        "normalize_whitespace",
        "normalize_zwnj",
        "normalize_punctuation_fa",
        "apply_token_map",
        "normalize_fa",
        "normalize_en",
        "normalize_az",
        "normalize_for_matching",
        "register_normalizer",
        "get_normalizer",
        "registered_languages",
        "normalize",
        "normalize_digits",
    ),
    "backend.locations.textnorm": (
        "normalize_char_stream",
        "normalize_text",
        "is_word_char",
        "NormalizedText.span",
    ),
    "backend.voicemail.classify": ("normalize_text", "is_empty_transcript"),
    "backend.privacy.text_pii": (
        "normalize_digits",
        "detect",
        "redact",
        "parse_classes",
        "valid_national_id",
        "valid_luhn",
        "valid_iban",
        "Pseudonymizer.token",
    ),
    "backend.security.normalization": (
        "normalize_email",
        "normalize_iranian_mobile",
        "normalize_workspace_slug",
    ),
    "backend.voicemail.ingest": ("normalize_caller_number",),
}
PUBLIC_DATACLASSES = (
    "backend.speech.text_normalize:NormalizerEntry",
    "backend.locations.textnorm:NormalizedText",
    "backend.privacy.text_pii:PiiSpan",
    "backend.privacy.text_pii:Replacement",
)


def _resolve(module_name: str, dotted: str):
    import importlib

    target = importlib.import_module(module_name)
    for part in dotted.split("."):
        target = getattr(target, part)
    return target


def _signature(obj) -> str:
    try:
        signature = inspect.signature(obj, eval_str=True)
    except NameError:
        signature = inspect.signature(obj)
    parameters = [
        parameter.replace(default=sorted(parameter.default, key=repr))
        if isinstance(parameter.default, set | frozenset)
        else parameter
        for parameter in signature.parameters.values()
    ]
    return str(signature.replace(parameters=parameters))


class TestGoldenPublicApi:
    @pytest.mark.parametrize(
        "qualified",
        [f"{module}:{name}" for module, names in PUBLIC_API.items() for name in names],
    )
    def test_public_callables_keep_their_signatures(self, qualified):
        module_name, dotted = qualified.split(":")
        assert _signature(_resolve(module_name, dotted)) == BASE_SIGNATURES[qualified]

    @pytest.mark.parametrize("qualified", PUBLIC_DATACLASSES)
    def test_public_dataclasses_keep_their_fields(self, qualified):
        module_name, dotted = qualified.split(":")
        cls = _resolve(module_name, dotted)
        fields = tuple(field.name for field in dataclasses.fields(cls))
        frozen = cls.__dataclass_params__.frozen
        assert (fields, frozen) == BASE_DATACLASS_FIELDS[qualified]

    def test_public_constants_and_registry_keep_their_values(self):
        from backend.privacy import text_pii
        from backend.speech import text_normalize

        assert text_normalize.ZWNJ == "\u200c"
        assert type(text_normalize.DEFAULT_COLLOQUIAL_FA) is dict
        assert text_normalize.DEFAULT_COLLOQUIAL_FA == _REF_COLLOQUIAL_FA
        assert list(text_normalize.DEFAULT_COLLOQUIAL_FA) == list(_REF_COLLOQUIAL_FA)
        assert {"fa", "en", "az"} <= set(text_normalize.registered_languages())
        assert text_normalize.get_normalizer("fa-IR") is text_normalize.normalize_fa
        assert text_normalize.get_normalizer("AZ_Latn") is text_normalize.normalize_az
        assert text_normalize.get_normalizer("en") is text_normalize.normalize_en
        assert text_pii.DETECTOR_VERSION == CURRENT_DETECTOR_VERSION
        pseudonymizer = text_pii.Pseudonymizer()
        assert pseudonymizer.token(text_pii.PiiClass.PHONE, "1") == "[PHONE_1]"
        assert (pseudonymizer.tokens, pseudonymizer.counters) == (
            {(text_pii.PiiClass.PHONE, "1"): "[PHONE_1]"},
            {text_pii.PiiClass.PHONE: 1},
        )


IMPORT_FIRST = (
    "backend.speech.text_normalize",
    "backend.privacy.text_pii",
    "backend.security.normalization",
    "backend.locations.textnorm",
    "backend.locations.extract",
    "backend.voicemail.classify",
    "backend.voicemail.ingest",
    "backend.speech.dates",
    "backend.speech.entities",
    "backend.speech.languages",
    "backend.speech.benchmark",
    "backend.routers.v1.auth",
    "backend.main",
)


class TestGoldenImportSafety:
    def test_each_module_imports_first_in_a_fresh_interpreter(self):
        processes = {
            name: _spawn([sys.executable, "-c", f"import {name}"]) for name in IMPORT_FIRST
        }
        failures = {}
        for name, process in processes.items():
            _, stderr = process.communicate(timeout=300)
            if process.returncode != 0:
                failures[name] = stderr[-2000:]
        assert failures == {}


ADDITIVE_TEST_FILES = (
    "tests/test_speech_normalize.py",
    "tests/test_locations_extract.py",
    "tests/test_voicemail_classify.py",
    "tests/test_text_pii.py",
)


def _test_statements(relative: str) -> set[str]:
    lines, tree = _lines(BACKEND / relative)
    return {
        _sha(_segment(lines, node, decorators=False))
        for node in tree.body
        if not isinstance(node, ast.Import | ast.ImportFrom)
    }


def _format_drift(relative: str) -> set[str]:
    result = _run([sys.executable, "-m", "ruff", "format", "--diff", f"backend/{relative}"])
    assert result.returncode in (0, 1), result.stderr[-2000:]
    return {
        line[1:]
        for line in result.stdout.splitlines()
        if line.startswith("-") and not line.startswith("---")
    }


OWNED_PRODUCTION = (
    "speech/text_normalize.py",
    "locations/textnorm.py",
    "voicemail/classify.py",
    "privacy/text_pii.py",
    "security/normalization.py",
    "routers/v1/auth.py",
    "voicemail/ingest.py",
)
NEW_TEST_FILES = (
    "tests/test_text_normalization_golden.py",
    "tests/test_text_normalization_owner.py",
    "tests/_text_normalization_reference.py",
    "tests/test_text_normalization_evidence.py",
)


class TestGoldenNoRegression:
    def test_every_base_test_case_is_still_collected(self):
        result = _run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
            + [f"backend/{relative}" for relative in ADDITIVE_TEST_FILES]
        )
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
        collected = {line.strip() for line in result.stdout.splitlines() if "::" in line}
        missing = sorted(set(BASE_NODE_IDS) - collected)
        assert missing == []

    @pytest.mark.parametrize("relative", ADDITIVE_TEST_FILES)
    def test_base_test_code_is_kept_verbatim(self, relative):
        missing = BASE_TEST_STATEMENTS[relative] - _test_statements(relative)
        assert missing == set()

    def test_privacy_routing_constants_stay_importable(self):
        from backend.tests.test_text_pii import FA, NID

        assert NID == "0012345679"
        assert "0123".translate(FA) == "\u06f0\u06f1\u06f2\u06f3"

    def test_changed_files_are_lint_clean_and_add_no_format_drift(self):
        present = [
            f"backend/{relative}"
            for relative in OWNED_PRODUCTION + ADDITIVE_TEST_FILES + NEW_TEST_FILES
            if (BACKEND / relative).exists()
        ]
        lint = _run([sys.executable, "-m", "ruff", "check", *present])
        assert lint.returncode == 0, lint.stdout[-3000:]
        drift = {
            relative: sorted(_format_drift(relative) - BASE_FORMAT_DRIFT.get(relative, frozenset()))
            for relative in OWNED_PRODUCTION + ADDITIVE_TEST_FILES + NEW_TEST_FILES
            if (BACKEND / relative).exists()
        }
        assert {relative: lines for relative, lines in drift.items() if lines} == {}


K_CODE_POINTS = frozenset(
    [
        *range(0x0660, 0x066A),
        *range(0x06F0, 0x06FA),
        0x0623,
        0x0624,
        0x0625,
        0x0629,
        0x0643,
        0x0649,
        0x064A,
        0x06C0,
        0x0671,
        0x0672,
        0x0640,
        *range(0x064B, 0x0660),
        0x0670,
        *range(0x06D6, 0x06EE),
        *range(0x200C, 0x2010),
        0x060C,
        0x066B,
        0x066C,
    ]
)
TABLE_INTS = frozenset({0x064B, 0x0660, 0x06F0})
RULE_DATA = frozenset(
    {
        "CATEGORY_RULES",
        "TOPIC_RULES",
        "URGENCY_TERMS",
        "NEGATIVE_TERMS",
        "CHURN_TERMS",
        "CATEGORY_INTENT",
    }
)
MIGRATED_MODULES = (
    "locations/textnorm.py",
    "voicemail/classify.py",
    "privacy/text_pii.py",
    "security/normalization.py",
    "routers/v1/auth.py",
    "voicemail/ingest.py",
)
RATCHET_ALLOWLIST: frozenset[str] = frozenset()


def _is_maketrans(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "maketrans") or (
        isinstance(func, ast.Name) and func.id == "maketrans"
    )


def _is_table_int(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value in TABLE_INTS


def _inside_table_literal(node: ast.AST, parents: dict) -> bool:
    current = parents.get(node)
    while current is not None and not isinstance(current, ast.stmt):
        if isinstance(current, ast.Dict | ast.Set | ast.DictComp | ast.SetComp):
            return True
        current = parents.get(current)
    return False


def _table_hits(relative: str) -> list[str]:
    return _table_hits_in((BACKEND / relative).read_text(encoding="utf-8"), relative)


def _table_hits_in(source: str, relative: str) -> list[str]:
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    exempt: set[ast.AST] = set()
    for statement in tree.body:
        targets = []
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        if {t.id for t in targets if isinstance(t, ast.Name)} & RULE_DATA:
            exempt.update(ast.walk(statement))
    patterns: set[ast.AST] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
        ):
            roots = node.args[:1] + [kw.value for kw in node.keywords if kw.arg == "pattern"]
            for root in roots:
                patterns.update(ast.walk(root))
    hits = []
    for node in ast.walk(tree):
        where = f"{relative}:{getattr(node, 'lineno', 0)}"
        if _is_maketrans(node):
            hits.append(f"{where}: maketrans call")
        elif _is_table_int(node):
            hits.append(f"{where}: int constant {node.value:#06x}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in exempt
            and any(ord(ch) in K_CODE_POINTS for ch in node.value)
        ):
            if node in patterns:
                hits.append(f"{where}: table code point in a re pattern")
            elif _inside_table_literal(node, parents):
                hits.append(f"{where}: table code point in a dict/set literal")
    return sorted(set(hits))


def _ratchet_hits() -> list[str]:
    hits = []
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        if relative.startswith("tests/") or relative == "speech/text_normalize.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _is_maketrans(node):
                hits.append(f"{relative}:{node.lineno}: maketrans call")
            elif _is_table_int(node):
                hits.append(f"{relative}:{node.lineno}: int constant {node.value:#06x}")
    return [hit for hit in hits if hit.split(": ")[0] not in RATCHET_ALLOWLIST]


def _module_level(tree: ast.Module):
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def _is_table(value) -> bool:
    from collections.abc import Mapping, Sequence
    from collections.abc import Set as AbstractSet

    if inspect.ismodule(value) or inspect.isclass(value) or callable(value):
        return False
    if type(value).__module__ == "__future__":
        return False
    return isinstance(value, Mapping | AbstractSet | Sequence | re.Pattern)


def _deeply_immutable(value) -> bool:
    if isinstance(value, str | re.Pattern):
        return True
    if isinstance(value, MappingProxyType):
        return all(_hashable(key) and _hashable(item) for key, item in value.items())
    if isinstance(value, frozenset | tuple):
        return _hashable(value)
    return False


def _hashable(value) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


CONSUMER_MODULES = (
    "backend.privacy.text_pii",
    "backend.security.normalization",
    "backend.locations.textnorm",
    "backend.voicemail.classify",
    "backend.voicemail.ingest",
    "backend.routers.v1.auth",
)


def _exported_tables() -> dict[str, object]:
    import importlib

    owner = importlib.import_module("backend.speech.text_normalize")
    exported = {
        name: value
        for name, value in vars(owner).items()
        if not name.startswith("_") and name != "DEFAULT_COLLOQUIAL_FA" and _is_table(value)
    }
    owned = {
        id(value): name
        for name, value in vars(owner).items()
        if not name.startswith("__")
        and name != "DEFAULT_COLLOQUIAL_FA"
        and _is_table(value)
        and not isinstance(value, str)
    }
    for module_name in CONSUMER_MODULES:
        for name, value in vars(importlib.import_module(module_name)).items():
            if not name.startswith("__") and id(value) in owned and _is_table(value):
                exported[owned[id(value)]] = value
    return exported


def _owner_documentation() -> str:
    source = OWNER_PATH.read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    comments = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    return docstring + "\n" + "\n".join(comments)


LEAF_PROBE = (
    "import json, sys\n"
    "import backend.speech.text_normalize as owner\n"
    "print(json.dumps([owner.normalize_digits.__module__, sorted(m for m in sys.modules "
    "if m == 'backend' or m.startswith('backend.'))]))\n"
)
CONSUMER_PROBE = (
    "import importlib, json, sys\n"
    "importlib.import_module(sys.argv[1])\n"
    "print(json.dumps('backend.speech.text_normalize' in sys.modules))\n"
)


class TestOwnerStructure:
    def test_normalize_digits_is_defined_by_the_owner_and_reexported(self):
        from backend.privacy import text_pii
        from backend.speech import text_normalize

        assert text_normalize.normalize_digits.__module__ == "backend.speech.text_normalize"
        assert text_pii.normalize_digits is text_normalize.normalize_digits

    def test_owner_is_a_leaf_in_a_fresh_interpreter(self):
        result = _run([sys.executable, "-c", LEAF_PROBE])
        assert result.returncode == 0, result.stderr[-2000:]
        module, loaded = json.loads(result.stdout.strip().splitlines()[-1])
        assert module == "backend.speech.text_normalize"
        assert loaded == ["backend", "backend.speech", "backend.speech.text_normalize"]

    def test_owner_has_no_module_level_backend_import(self):
        offenders = []
        for node in _module_level(ast.parse(OWNER_PATH.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (
                node.level > 0 or (node.module or "").split(".")[0] == "backend"
            ):
                offenders.append(node.lineno)
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "backend" for alias in node.names
            ):
                offenders.append(node.lineno)
        assert offenders == []

    @pytest.mark.parametrize(
        "module",
        [
            "backend.privacy.text_pii",
            "backend.security.normalization",
            "backend.locations.textnorm",
            "backend.voicemail.classify",
        ],
    )
    def test_migrated_consumers_load_the_owner(self, module):
        result = _run([sys.executable, "-c", CONSUMER_PROBE, module])
        assert result.returncode == 0, result.stderr[-2000:]
        assert json.loads(result.stdout.strip().splitlines()[-1]) is True

    @pytest.mark.parametrize("relative", MIGRATED_MODULES)
    def test_migrated_modules_define_no_tables_of_their_own(self, relative):
        assert _table_hits(relative) == []

    def test_voicemail_rule_data_is_not_mistaken_for_a_table(self):
        tree = ast.parse((BACKEND / "voicemail" / "classify.py").read_text(encoding="utf-8"))
        rules = next(node for node in tree.body if _label(node) == "CATEGORY_RULES")
        assert "\u06f1" in ast.unparse(rules)
        lines = {int(hit.split(":")[1]) for hit in _table_hits("voicemail/classify.py")}
        assert not any(rules.lineno <= line <= rules.end_lineno for line in lines)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ('T = {"\u064a": "x"}\n', True),
            ('T = str.maketrans("a", "b")\n', True),
            ("T = {chr(0x06F0 + i): str(i) for i in range(10)}\n", True),
            ('P = re.compile("[\u064b-\u065f]")\n', True),
            ('D = {c for c in "\u200c\u200d"}\n', True),
            ('"""Docstring naming \u064a and \u06f1."""\n', False),
            ('CATEGORY_RULES = {"spam": ("x \u06f1",)}\n', False),
            ('X = re.sub("a", "\u200c", text)\n', False),
            ('T = MappingProxyType({**CORE, ZWNJ: " ", TATWEEL: ""})\n', False),
            ('P = re.compile(f"[{HARAKAT}{QURANIC}]")\n', False),
        ],
    )
    def test_table_scanner_flags_tables_and_spares_docs_and_rules(self, source, flagged):
        assert bool(_table_hits_in(source, "probe.py")) is flagged

    def test_no_eighth_copy_anywhere_in_the_backend(self):
        assert _ratchet_hits() == []

    def test_exported_tables_are_immutable(self):
        exported = _exported_tables()
        mutable = {
            name: type(value).__name__
            for name, value in exported.items()
            if not _deeply_immutable(value)
        }
        assert mutable == {}
        for name, value in exported.items():
            if isinstance(value, MappingProxyType):
                with pytest.raises(TypeError):
                    value["\u0000"] = name

    def test_colloquial_table_keeps_its_mutable_type_and_still_works(self):
        from backend.speech.text_normalize import DEFAULT_COLLOQUIAL_FA, normalize_fa

        assert type(DEFAULT_COLLOQUIAL_FA) is dict
        assert normalize_fa("یه سوال", colloquial=DEFAULT_COLLOQUIAL_FA) == ("یک سوال")
        merged = MappingProxyType({**DEFAULT_COLLOQUIAL_FA, "x": "y"})
        assert merged["x"] == "y"

    def test_owner_documents_tables_profiles_and_divergences(self):
        documentation = _owner_documentation()
        lowered = documentation.casefold()
        undocumented = sorted(name for name in _exported_tables() if name not in documentation)
        assert undocumented == []
        for profile in ("location", "voicemail", "pii", "matching"):
            assert profile in lowered, profile
        assert "otp" in lowered or "mobile" in lowered
        assert "nfkc" in lowered
        assert "diverg" in lowered or "differen" in lowered

    def test_stale_ownership_notes_are_resolved(self):
        owner = OWNER_PATH.read_text(encoding="utf-8")
        assert not re.search(
            r"reusing\s+:func:`backend\.privacy\.text_pii\.normalize_digits`", owner
        )
        textnorm = (BACKEND / "locations" / "textnorm.py").read_text(encoding="utf-8")
        assert "TODO(consolidate)" not in textnorm


GOLDEN_MODULE = TESTS / "test_text_normalization_golden.py"
REFERENCE_MODULE = TESTS / "_text_normalization_reference.py"
EVIDENCE_MODULE = TESTS / "test_text_normalization_evidence.py"
DB_FIXTURES = frozenset(
    {"client", "settings", "email_outbox", "sms_outbox", "keyed_settings", "probe_role"}
)


def _base_names(module: str) -> set[str] | None:
    relative = module.replace(".", "/")
    for candidate in (f"Homayar/{relative}.py", f"Homayar/{relative}/__init__.py"):
        shown = _run(["git", "-C", str(HOMAYAR.parent), "show", f"{BASE_SHA}:{candidate}"])
        if shown.returncode == 0:
            tree = ast.parse(shown.stdout)
            names = {
                node.name
                for node in tree.body
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            }
            for node in _module_level(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, ast.alias):
                    names.add((node.asname or node.name).split(".")[0])
            return names
    return None


GOLDEN_BUDGET_SECONDS = 30.0
GOLDEN_BUDGET_VARIABLE = "HOMAYAR_GOLDEN_BUDGET_SECONDS"
# ``_run`` stops waiting for the timed run after 300 s, so a larger bound could never apply.
GOLDEN_BUDGET_CEILING_SECONDS = 300.0


def _golden_budget_seconds(environ) -> float:
    """The golden module's wall-clock budget (HOM-172).

    The SPEC budget is 30 s and stays the default everywhere, the owner's host included.
    Owner decision of 2026-10-10: on GitHub CI only, ``HOMAYAR_GOLDEN_BUDGET_SECONDS``
    (set by the backend job in ``.github/workflows/ci.yml``, see ``docs/operations/ci.md``)
    replaces it, because shared runners took 37-77 s for the same module.  The variable
    counts only with ``CI=true``; any value that is not a number of seconds from 30 to 300
    fails the gate instead of being ignored.
    """
    raw = environ.get(GOLDEN_BUDGET_VARIABLE)
    if raw is None:
        return GOLDEN_BUDGET_SECONDS
    if environ.get("CI") != "true":
        pytest.fail(f"{GOLDEN_BUDGET_VARIABLE} is a CI-only bound (HOM-172); unset it outside CI")
    try:
        budget = float(raw)
    except ValueError:
        budget = None
    if budget is None or not GOLDEN_BUDGET_SECONDS <= budget <= GOLDEN_BUDGET_CEILING_SECONDS:
        pytest.fail(f"{GOLDEN_BUDGET_VARIABLE}={raw!r} must be from 30 to 300 seconds (HOM-172)")
    return budget


class TestDeliverables:
    def test_golden_module_uses_pure_functions_and_base_names_only(self):
        assert GOLDEN_MODULE.exists()
        tree = ast.parse(GOLDEN_MODULE.read_text(encoding="utf-8"))
        problems = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                used = {arg.arg for arg in node.args.args} & DB_FIXTURES
                if used:
                    problems.append(f"{node.name} uses {sorted(used)}")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {
                "sqlalchemy",
                "fastapi",
            }:
                problems.append(f"imports {node.module}")
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend"):
                if node.module == "backend.tests._text_normalization_reference":
                    continue
                if node.module.startswith(("backend.database", "backend.tests._database")):
                    problems.append(f"imports {node.module}")
                names = _base_names(node.module)
                if names is None:
                    problems.append(f"{node.module} does not exist at {BASE_SHA}")
                    continue
                missing = [alias.name for alias in node.names if alias.name not in names]
                if missing:
                    problems.append(f"{node.module} lacks {missing} at {BASE_SHA}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("backend") and _base_names(alias.name) is None:
                        problems.append(f"{alias.name} does not exist at {BASE_SHA}")
            if isinstance(node, ast.Attribute) and node.attr == "postgres":
                problems.append("marked postgres")
        assert problems == []

    def test_frozen_references_name_the_base_commit_and_source_lines(self):
        assert GOLDEN_MODULE.exists()
        text = GOLDEN_MODULE.read_text(encoding="utf-8")
        if REFERENCE_MODULE.exists():
            text += REFERENCE_MODULE.read_text(encoding="utf-8")
        assert BASE_SHA in text
        for source in ("text_normalize.py", "textnorm.py", "classify.py", "normalization.py"):
            assert source in text, source
        assert re.search(r"\.py:\d+", text)

    def test_golden_module_is_green_fast_and_independent_of_the_registry(self):
        """The module passes alone within its budget, and again after the registry tests.

        The budget is the SPEC's 30 s; only GitHub CI may raise it, through the documented
        ``HOMAYAR_GOLDEN_BUDGET_SECONDS`` bound (HOM-172, see ``_golden_budget_seconds``).
        """
        import os

        assert GOLDEN_MODULE.exists()
        budget = _golden_budget_seconds(os.environ)
        golden = f"backend/tests/{GOLDEN_MODULE.name}"
        started = time.monotonic()
        alone = _run([sys.executable, "-m", "pytest", golden, "-q", "-p", "no:cacheprovider"])
        wall = time.monotonic() - started
        assert alone.returncode == 0, alone.stdout[-3000:]
        summary = alone.stdout.strip().splitlines()[-1]
        assert " passed" in summary and "skipped" not in summary and "failed" not in summary
        reported = re.search(r" in ([0-9.]+)s", summary)
        elapsed = float(reported.group(1)) if reported else wall
        assert elapsed < budget, f"golden module took {elapsed:.2f} s; budget {budget:g} s"
        polluted = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/test_speech_normalize.py",
                golden,
                "-q",
                "-p",
                "no:cacheprovider",
            ]
        )
        assert polluted.returncode == 0, polluted.stdout[-3000:]

    @pytest.mark.parametrize(
        ("environ", "expected"),
        [
            ({}, 30.0),
            ({"CI": "true"}, 30.0),
            ({"CI": "true", GOLDEN_BUDGET_VARIABLE: "30"}, 30.0),
            ({"CI": "true", GOLDEN_BUDGET_VARIABLE: "120"}, 120.0),
            ({"CI": "true", GOLDEN_BUDGET_VARIABLE: "300"}, 300.0),
        ],
    )
    def test_golden_budget_is_30_s_unless_ci_sets_a_documented_bound(self, environ, expected):
        assert _golden_budget_seconds(environ) == expected

    @pytest.mark.parametrize(
        "environ",
        [
            {GOLDEN_BUDGET_VARIABLE: "120"},
            {"CI": "false", GOLDEN_BUDGET_VARIABLE: "120"},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: ""},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: "120s"},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: "29.9"},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: "-120"},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: "300.5"},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: "nan"},
            {"CI": "true", GOLDEN_BUDGET_VARIABLE: "inf"},
        ],
    )
    def test_golden_budget_refuses_a_bound_outside_ci_or_its_range(self, environ):
        with pytest.raises(pytest.fail.Exception, match=GOLDEN_BUDGET_VARIABLE):
            _golden_budget_seconds(environ)

    def test_raw_evidence_test_reuses_the_upload_pipeline(self):
        candidates = []
        if EVIDENCE_MODULE.exists():
            candidates.append(EVIDENCE_MODULE.read_text(encoding="utf-8"))
        lines, tree = _lines(TESTS / "test_text_pii.py")
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name not in BASE_TEST_PII_FUNCTIONS:
                candidates.append(_segment(lines, node))
        assert any(
            "_processed_with_pii" in text and "redact_revision" in text for text in candidates
        )

    @pytest.mark.parametrize("module", ["backend.routers.v1.auth", "backend.voicemail.ingest"])
    def test_granted_sites_use_the_owner_digit_folding(self, module):
        import importlib

        owner = importlib.import_module("backend.speech.text_normalize")
        namespace = vars(importlib.import_module(module)).values()
        folds = (owner.normalize_digits, owner.normalize_digits_extended)
        assert any(value in folds or value is owner for value in namespace)
