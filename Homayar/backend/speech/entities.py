"""Entity extraction from Persian/English transcript text.

Structured identifiers (phones, national IDs, e-mails) reuse the validated
detectors of :mod:`backend.privacy.text_pii`; dates and times come from
:mod:`backend.speech.dates`.  Added here: amounts with a currency word
(``تومان``/``ریال``/``toman``/``rial``, digits or spoken numbers, ``هزار``/
``میلیون`` multipliers), order/ticket-like reference IDs next to a keyword,
and *person-name hint slots*: the position after ``اسمم``/``من … هستم``/``my
name is`` is reported as a slot with an empty value — a name is never guessed.

Offsets always refer to the original text.  Confidence is a rule prior for
routing/review, not a measured precision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo

from backend.privacy.text_pii import PiiClass, detect
from backend.speech.dates import (
    TEHRAN,
    DateRange,
    _parse_number_at,
    _shadow,
    extract_temporal,
)

EXTRACTOR_VERSION = "speech-entities/1"


@dataclass(frozen=True)
class Entity:
    # phone|national_id|email|amount|date|date_range|time|reference_id|person_name_slot
    type: str
    value: object  # normalised value; None for slots and ambiguous temporal matches
    start: int
    end: int
    raw: str
    confidence: float
    source: str  # detector that produced it
    candidates: tuple = ()
    ambiguous: bool = False


_PII_MAP = {
    PiiClass.PHONE: ("phone", 0.95),
    PiiClass.IR_NATIONAL_ID: ("national_id", 0.9),
    PiiClass.EMAIL: ("email", 0.98),
}

_CURRENCY = {
    "تومان": "IRT", "تومن": "IRT", "تومنی": "IRT", "ریال": "IRR",
    "toman": "IRT", "tomans": "IRT", "rial": "IRR", "rials": "IRR",
}  # fmt: skip
_AMOUNT_SCALE = {"هزار": 1_000, "میلیون": 1_000_000, "میلیارد": 1_000_000_000,
                 "thousand": 1_000, "million": 1_000_000}  # fmt: skip

_AMOUNT_TOKEN_RE = re.compile(r"[^\s\u200c،؛;؟?!()«»\"]+")
_DIGIT_AMOUNT_RE = re.compile(r"(?<![\w/])(\d{1,3}(?:[,٬]\d{3})+|\d+(?:\.\d+)?)(?![\w/])")

_REF_RE = re.compile(
    r"(?:کد\s*(?:پیگیری|سفارش|رهگیری)|شماره\s*(?:سفارش|پیگیری|تیکت|فاکتور|قرارداد)|"
    r"order(?:\s*(?:id|number|no\.?))?|ticket(?:\s*(?:id|number|no\.?))?|tracking\s*(?:code|number)|ref(?:erence)?)"
    r"\s*[:#\-]?\s*(?:(?:ی|ام|من)\s+)?([A-Za-z]{0,4}[-_]?\d[\dA-Za-z\-_]{2,24})",
    re.IGNORECASE,
)

_NAME_SLOT_RES = (
    re.compile(r"(?:اسم|نام)\s*(?:م|من|بنده)\s*(?:هست|است|ه)?\s*"),
    re.compile(r"\bmy name is\s+", re.IGNORECASE),
    re.compile(r"\bthis is\s+(?=\S+\s+(?:speaking|calling))", re.IGNORECASE),
)


def _amounts(text: str, shadow: str) -> list[Entity]:
    toks = []
    for m in _AMOUNT_TOKEN_RE.finditer(shadow):
        word = m.group().rstrip(",.")
        if word:
            toks.append((word, m.start(), m.start() + len(word)))
    words = [t[0] for t in toks]
    out: list[Entity] = []
    for ci, w in enumerate(words):
        cur = _CURRENCY.get(w.casefold())
        if cur is None or ci == 0:
            continue
        # Walk back over a digit amount or a spoken number ending at ci-1.
        j = ci - 1
        scale = 1
        if words[j] in _AMOUNT_SCALE and j > 0 and _DIGIT_AMOUNT_RE.fullmatch(words[j - 1]):
            scale = _AMOUNT_SCALE[words[j]]
            j -= 1
        if _DIGIT_AMOUNT_RE.fullmatch(words[j]):
            raw_num = re.sub(r"[,٬]", "", words[j])
            value = float(raw_num) * scale
            start = toks[j][1]
            conf = 0.9
        else:
            best = None
            for s in range(max(0, j - 12), j + 1):
                parsed = _parse_number_at(words, s)
                if parsed is not None and parsed[1] == ci and not parsed[2]:
                    best = (s, parsed[0])
                    break
            if best is None:
                continue
            start, value, conf = toks[best[0]][1], float(best[1]), 0.8
        amount = int(value) if value == int(value) else value
        out.append(
            Entity(
                "amount",
                {"amount": amount, "currency": cur},
                start,
                toks[ci][2],
                text[start : toks[ci][2]],
                conf,
                "speech.amount",
            )
        )
    return out


def extract_entities(
    text: str,
    reference: datetime,
    tz: tzinfo = TEHRAN,
    *,
    include_name_slots: bool = True,
) -> list[Entity]:
    """Entities in text order.  Overlaps are resolved in favour of the
    validated identifier (phones/IDs/e-mails) over amounts, references and
    temporal matches."""
    shadow = _shadow(text)
    found: list[Entity] = []
    for span in detect(text, _PII_MAP.keys()):
        etype, conf = _PII_MAP[span.pii_class]
        found.append(
            Entity(
                etype,
                span.value,
                span.start,
                span.end,
                text[span.start : span.end],
                conf,
                f"privacy.text_pii:{span.pii_class.value}",
            )
        )

    for m in _REF_RE.finditer(shadow):
        value = m.group(1).upper()
        found.append(
            Entity(
                "reference_id",
                value,
                m.start(1),
                m.end(1),
                text[m.start(1) : m.end(1)],
                0.8,
                "speech.reference_id",
            )
        )

    found.extend(_amounts(text, shadow))

    for tm in extract_temporal(text, reference, tz):
        value: date | DateRange | time | None = tm.value
        found.append(
            Entity(
                tm.kind,
                value,
                tm.start,
                tm.end,
                tm.raw,
                tm.confidence,
                f"speech.dates:{tm.rule}",
                tm.candidates,
                tm.ambiguous,
            )
        )

    if include_name_slots:
        for rx in _NAME_SLOT_RES:
            for m in rx.finditer(text):
                nxt = re.match(r"\S+", text[m.end() :])
                end = m.end() + (nxt.end() if nxt else 0)
                found.append(
                    Entity(
                        "person_name_slot",
                        None,
                        m.end(),
                        end,
                        text[m.end() : end],
                        0.3,
                        "speech.name_slot",
                    )
                )

    priority = {"email": 0, "phone": 1, "national_id": 2, "reference_id": 3, "amount": 4}
    chosen: list[Entity] = []
    for ent in sorted(found, key=lambda e: (priority.get(e.type, 5), -(e.end - e.start), e.start)):
        if ent.type == "person_name_slot" or all(
            ent.end <= k.start or ent.start >= k.end for k in chosen if k.type != "person_name_slot"
        ):
            chosen.append(ent)
    return sorted(chosen, key=lambda e: (e.start, e.end))
