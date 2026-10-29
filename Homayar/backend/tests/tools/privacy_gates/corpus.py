"""Deterministic synthetic corpora for the privacy gates (HOM-321).

Self-contained on purpose: this module never imports ``backend.privacy`` or
``backend.speech``, so the ground truth never depends on the code under test.
Every value is synthetic.  Each corpus grows from one seeded
``random.Random`` (never the global ``random``, never the clock, never
``hash()``), so the same seed and row count give byte-identical files in any
process on any machine.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path

CORPUS_NAMES = ("dense", "words", "dsar", "prices")
PRICE_BUCKET_NAMES = ("20-25", "26-35", "36-60")
_PRICE_BUCKETS = {"20-25": (20, 25), "26-35": (26, 35), "36-60": (36, 60)}
EMAIL_DOMAINS = ("example.com", "example.org")
CARD_PREFIXES = ("6037", "6219", "5022", "6104", "6273")
# Separators that text_pii reads between digits: space, NBSP, hyphen, en dash.
SEPARATORS = (" ", "\u00a0", "-", "\u2013")
JOINERS = (" ", " ", " ", " و ", " and ", " با ")
FA_WORDS = (
    "قرار",
    "جلسه",
    "فردا",
    "لطفا",
    "بنویس",
    "یادداشت",
    "زمان",
    "برنامه",
    "لیست",
    "قیمت",
    "محصول",
    "سفارش",
    "مشتری",
    "تاریخ",
    "دقیقه",
    "شروع",
    "پایان",
    "ممنون",
    "بعد",
    "امروز",
)
EN_WORDS = (
    "meeting",
    "tomorrow",
    "please",
    "note",
    "list",
    "price",
    "order",
    "customer",
    "address",
    "date",
    "start",
    "end",
    "thanks",
    "the",
    "and",
    "with",
    "send",
    "later",
)
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
ARABIC_INDIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
# Every identifier class appears in the dense and words corpora in turn.
CLASS_CYCLE = ("ir_national_id", "phone", "bank_card", "iban", "email", "ir_postal_code")
DSAR_CLASSES = ("email", "phone", "ir_national_id", "bank_card", "iban")
DIGITS = "0123456789"
LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _rng_digits(rng: random.Random, size: int) -> str:
    return "".join(rng.choice(DIGITS) for _ in range(size))


# Local validators (the same rules text_pii applies, reimplemented so the
# generator stays independent of the code under test).
def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _national_id_ok(digits: str) -> bool:
    if len(digits) != 10 or len(set(digits)) == 1:
        return False
    total = sum(int(digits[i]) * (10 - i) for i in range(9)) % 11
    check = int(digits[9])
    return check == total if total < 2 else check == 11 - total


def _iban_numeric(chars: str) -> int:
    return int("".join(str(int(char, 36)) for char in chars.upper()))


def _iban_ok(compact: str) -> bool:
    compact = compact.upper()
    if len(compact) != 26 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    return _iban_numeric(compact[4:] + compact[:4]) % 97 == 1


def _phone_ok(digits: str) -> bool:
    """A mobile (09 + one of 012349 + 8 digits) or a landline (0 + 1 to 8 + 9 digits)."""
    if len(digits) != 11 or not digits.isdigit():
        return False
    return (
        (digits.startswith("09") and digits[2] in "012349")
        or digits[0] == "0"
        and digits[1] in "12345678"
    )


def _national_id(rng: random.Random) -> str:
    while True:
        prefix = _rng_digits(rng, 9)
        total = sum(int(prefix[i]) * (10 - i) for i in range(9)) % 11
        value = prefix + str(total if total < 2 else 11 - total)
        if len(set(value)) > 1:
            return value


def _mobile(rng: random.Random) -> str:
    return "09" + rng.choice("012349") + _rng_digits(rng, 8)


def _landline(rng: random.Random) -> str:
    return "0" + rng.choice("12345678") + _rng_digits(rng, 9)


def _bank_card(rng: random.Random) -> str:
    while True:
        body = rng.choice(CARD_PREFIXES) + _rng_digits(rng, 11)
        for check in DIGITS:
            value = body + check
            if _luhn_ok(value) and len(set(value)) > 1:
                return value


def _iban(rng: random.Random) -> str:
    while True:
        bban = _rng_digits(rng, 22)
        check = 98 - _iban_numeric(bban + "IR00") % 97
        if 2 <= check <= 98:
            return f"IR{check:02d}{bban}"


def _email(rng: random.Random) -> str:
    local = "".join(rng.choice(LETTERS) for _ in range(rng.randint(4, 10)))
    return f"{local}@{rng.choice(EMAIL_DOMAINS)}"


def _value(rng: random.Random, item_class: str) -> str:
    if item_class == "email":
        return _email(rng)
    if item_class == "phone":
        return _mobile(rng) if rng.random() < 0.75 else _landline(rng)
    if item_class == "ir_national_id":
        return _national_id(rng)
    if item_class == "bank_card":
        return _bank_card(rng)
    return _iban(rng)


# --- spellings -------------------------------------------------------------------------


def _random_groups(digits: str, rng: random.Random) -> str:
    groups: list[str] = []
    i = 0
    while i < len(digits):
        remaining = len(digits) - i
        if remaining > 5:
            size = rng.randint(2, 5)
        elif remaining >= 2:
            size = rng.randint(2, remaining)
        else:
            size = remaining
        groups.append(digits[i : i + size])
        i += size
    return rng.choice(SEPARATORS).join(groups)


def _fixed_groups(digits: str, sizes: tuple[int, ...], rng: random.Random) -> str:
    groups = []
    i = 0
    for size in sizes:
        groups.append(digits[i : i + size])
        i += size
    return rng.choice(SEPARATORS).join(groups)


def spell(value: str, rng: random.Random, form: tuple[int, ...] | None) -> str:
    """Plain, in random groups of 2 to 5 digits, or in one common written form."""
    roll = rng.random()
    if roll < 0.3:
        return value
    if roll < 0.6 or form is None:
        return _random_groups(value, rng)
    return _fixed_groups(value, form, rng)


def _iban_groups(value: str) -> str:
    return " ".join(value[i : i + 4] for i in range(0, len(value), 4))


def _spelling(rng: random.Random, item_class: str, value: str) -> str:
    """How a value is written into the text (SPEC 3.2).  An e-mail is always
    written as is: ASCII, no separators, no digit script change.  An IBAN is
    compact or in groups of 4 with single spaces; only its digit script may
    change.  Grouping, separators and digit scripts apply to the digit
    identifiers only (phone, national ID, card, postal code)."""
    if item_class == "email":
        return value
    if item_class == "iban":
        return value if rng.random() < 0.5 else _iban_groups(value)
    if item_class == "ir_postal_code":
        return spell(value, rng, (3, 3, 4))
    if item_class == "phone":
        return spell(value, rng, (4, 3, 4) if value.startswith("09") else (3, 4, 4))
    if item_class == "ir_national_id":
        return spell(value, rng, (3, 6, 1))
    return spell(value, rng, (4, 4, 4, 4))  # bank_card


def _script(rng: random.Random):
    roll = rng.random()
    if roll < 0.7:
        return None
    return PERSIAN_DIGITS if roll < 0.9 else ARABIC_INDIC_DIGITS


# --- tokens ----------------------------------------------------------------------------


def _planted(item_class: str, value: str, spelling: str, subject: bool):
    return spelling, [(item_class, 0, len(spelling), subject)]


def _identifier_token(
    rng: random.Random, item_class: str, subject: bool = False, value: str | None = None
):
    if value is None:
        value = _value(rng, item_class)
    if item_class == "ir_postal_code":
        # The planted range is the digits only, not the keyword.
        keyword = rng.choice(("کد پستی", "postal code"))
        value = _rng_digits(rng, 10)
        body = _spelling(rng, "ir_postal_code", value)
        piece = keyword + rng.choice((" ", ": ")) + body
        return piece, [("ir_postal_code", len(piece) - len(body), len(piece), subject)]
    return _planted(item_class, value, _spelling(rng, item_class, value), subject)


def _glued_pair(rng: random.Random):
    # A national ID directly followed by a mobile, both planted.
    piece = _national_id(rng) + _mobile(rng)
    return piece, [
        ("ir_national_id", 0, 10, False),
        ("phone", 10, 21, False),
    ]


def _glued_mobile(rng: random.Random):
    glue = _rng_digits(rng, rng.randint(1, 3))
    piece = glue + _mobile(rng)
    return piece, [("phone", len(glue), len(glue) + 11, False)]


def _noise_run(rng: random.Random):
    return _rng_digits(rng, rng.randint(1, 26)), []


def _noise_00(rng: random.Random):
    return "00" + _rng_digits(rng, rng.randint(6, 14)), []


def _noise_price(rng: random.Random):
    piece = " ".join(_rng_digits(rng, rng.randint(3, 7)) for _ in range(rng.randint(2, 5)))
    return piece, []


# --- row assembly ----------------------------------------------------------------------


def _assemble(
    name: str,
    index: int,
    parts: list,
    joiner: str,
    script,
) -> dict:
    """Join parts into one row.  Offsets are computed on the final text, so
    ``text[start:end] == raw`` holds for every planted item even after the
    digit-script translation (one character for one character)."""
    pieces: list[str] = []
    planted: list[tuple[int, int, str, bool]] = []
    pos = 0
    for k, (piece, items) in enumerate(parts):
        if k:
            pieces.append(joiner)
            pos += len(joiner)
        pieces.append(piece)
        for item_class, start, end, subject in items:
            planted.append((pos + start, pos + end, item_class, subject))
        pos += len(piece)
    text = "".join(pieces)
    if script is not None:
        text = text.translate(script)
    return {
        "id": f"{name}-{index}",
        "text": text,
        "planted": [
            {
                "class": item_class,
                "raw": text[start:end],
                "start": start,
                "end": end,
                "subject": subject,
            }
            for start, end, item_class, subject in sorted(planted)
        ],
        "subject": [],
        "bucket": None,
    }


def _dense_row(rng: random.Random, index: int) -> dict:
    parts = []
    for k in range(rng.randint(3, 8)):
        roll = rng.random()
        if roll < 0.45:
            item_class = CLASS_CYCLE[(index * 3 + k) % len(CLASS_CYCLE)]
            parts.append(_identifier_token(rng, item_class))
        elif roll < 0.55:
            parts.append(_glued_pair(rng))
        elif roll < 0.62:
            parts.append(_glued_mobile(rng))
        elif roll < 0.8:
            parts.append(_noise_run(rng))
        elif roll < 0.9:
            parts.append(_noise_00(rng))
        else:
            parts.append(_noise_price(rng))
    return _assemble("dense", index, parts, rng.choice(JOINERS), _script(rng))


def _words_row(rng: random.Random, index: int) -> dict:
    count = rng.randint(8, 30)
    parts = [(rng.choice(FA_WORDS + EN_WORDS), []) for _ in range(count)]
    for _ in range(rng.choice((0, 1, 1, 2))):
        parts.insert(
            rng.randrange(len(parts) + 1),
            _identifier_token(rng, rng.choice(CLASS_CYCLE)),
        )
    if rng.random() < 0.3:
        parts.insert(rng.randrange(len(parts) + 1), (_rng_digits(rng, rng.randint(3, 12)), []))
    return _assemble("words", index, parts, " ", _script(rng))


def _near_miss(rng: random.Random, item_class: str, value: str) -> str | None:
    """The value with one digit or letter changed, or None when no single
    change keeps it a valid identifier of its class."""
    if item_class == "email":
        local, domain = value.split("@")
        pos = rng.randrange(len(local))
        char = rng.choice([c for c in LETTERS if c != local[pos]])
        return local[:pos] + char + local[pos + 1 :] + "@" + domain
    checks = {
        "ir_national_id": _national_id_ok,
        "bank_card": _luhn_ok,
        "iban": _iban_ok,
        "phone": _phone_ok,
    }
    first = 4 if item_class == "iban" else (2 if item_class == "phone" else 0)
    for _ in range(20):
        pos = rng.randrange(first, len(value))
        char = rng.choice([d for d in DIGITS if d != value[pos]])
        candidate = value[:pos] + char + value[pos + 1 :]
        if checks[item_class](candidate) and candidate != value:
            return candidate
    return None


def _dsar_row(rng: random.Random, index: int) -> dict:
    classes = rng.sample(DSAR_CLASSES, rng.randint(1, 3))
    values = {item_class: _value(rng, item_class) for item_class in classes}
    parts = []
    # Some of the subject's identifiers, planted as subject matches.
    for item_class in classes:
        if rng.random() < 0.55:
            parts.append(_identifier_token(rng, item_class, subject=True, value=values[item_class]))
    # Identifiers of other people.
    for _ in range(rng.randint(0, 2)):
        parts.append(_identifier_token(rng, rng.choice(DSAR_CLASSES)))
    # Near misses of the subject's values, planted only when still valid.
    # They are written with the same per-class spelling rules as any value.
    for item_class in classes:
        if rng.random() < 0.5:
            miss = _near_miss(rng, item_class, values[item_class])
            if miss is not None:
                parts.append(_planted(item_class, miss, _spelling(rng, item_class, miss), False))
    for _ in range(rng.randint(0, 2)):
        parts.append(rng.choice((_noise_run(rng), _noise_00(rng), _noise_price(rng))))
    if not parts:
        parts.append(_noise_run(rng))
    rng.shuffle(parts)
    subject = []
    for item_class in classes:
        value = values[item_class]
        if item_class == "phone" and rng.random() < 0.3:
            subject.append(spell(value, rng, (4, 3, 4) if value.startswith("09") else (3, 4, 4)))
        else:
            subject.append(value)
    row = _assemble("dsar", index, parts, rng.choice(JOINERS), _script(rng))
    row["subject"] = subject
    return row


def _number_with_separators(rng: random.Random, size: int) -> str:
    digits = _rng_digits(rng, size)
    if size >= 5 and rng.random() < 0.35:
        separator = rng.choice((",", "٬"))
        groups: list[str] = []
        while len(digits) > 3:
            groups.insert(0, digits[-3:])
            digits = digits[:-3]
        groups.insert(0, digits)
        return separator.join(groups)
    return digits


def _prices_row(rng: random.Random, index: int) -> dict:
    bucket = PRICE_BUCKET_NAMES[index % len(PRICE_BUCKET_NAMES)]
    low, high = _PRICE_BUCKETS[bucket]
    parts: list[str] = []
    total = 0
    while total < low:
        size = rng.randint(3, min(7, high - total))
        parts.append(_number_with_separators(rng, size))
        total += size
    while total + 3 <= high and rng.random() < 0.5:
        size = rng.randint(3, min(7, high - total))
        parts.append(_number_with_separators(rng, size))
        total += size
    row = _assemble(
        "prices",
        index,
        [(part, []) for part in parts],
        rng.choice((" ", " ", "، ", " و ")),
        _script(rng),
    )
    row["bucket"] = bucket
    return row


ROW_MAKERS: dict[str, Callable[[random.Random, int], dict]] = {
    "dense": _dense_row,
    "words": _words_row,
    "dsar": _dsar_row,
    "prices": _prices_row,
}


def write_corpus(out_dir: Path, seed: int, rows: int) -> Path:
    """Write ``<out_dir>/corpus/<name>.jsonl`` for every corpus."""
    if rows < 1:
        raise ValueError("rows must be at least 1")
    corpus_dir = out_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for name in CORPUS_NAMES:
        # One Random per corpus, seeded from the seed and the corpus name, so
        # corpora stay independent and repeatable.
        rng = random.Random(f"{seed}:{name}")  # noqa: S311 - synthetic test data, not security
        # Every price bucket holds at least one row (SPEC 3.2), so prices may
        # hold 3 rows when rows is 1 or 2.
        count = max(rows, 3) if name == "prices" else rows
        path = corpus_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for index in range(count):
                handle.write(
                    json.dumps(ROW_MAKERS[name](rng, index), ensure_ascii=False, sort_keys=True)
                )
                handle.write("\n")
    return corpus_dir


def read_corpus(corpus_dir: Path) -> dict[str, list[dict]]:
    """Read every corpus file back, keyed by corpus name."""
    rows: dict[str, list[dict]] = {}
    for name in CORPUS_NAMES:
        path = corpus_dir / f"{name}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        rows[name] = [json.loads(line) for line in lines]
    return rows
