"""Deterministic, versioned voicemail classification and prioritisation.

``RuleClassifier`` matches Persian and English keyword/phrase rules over the
transcript text after the voicemail keyword normalisation profile (Arabic letter
variants, ZWNJ, diacritics, tatweel, Persian/Arabic digits, case), built from
the tables of :mod:`backend.speech.text_normalize`.  It never invents
content: an empty or speechless transcript is reported as ``empty`` with
``needs_review`` and no intent, topics or follow-up.

The ``VoicemailClassifier`` protocol is the seam for an LLM classifier
(an ``IntelligenceProvider`` adapter) later: whatever implements it reports
its ``kind``/``version``/``model_id`` and the pipeline records them as the
classification's provenance.  Priority scoring stays rule-based and explained
(``PriorityReason`` list) whichever classifier produced the category.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from backend.speech.text_normalize import (
    DIGITS,
    HARAKAT_CLASS,
    LETTER_VARIANTS_EXTENDED,
    LRM,
    QURANIC_MARKS_CLASS,
    RLM,
    TATWEEL,
    ZWJ,
    ZWNJ,
    translation_table,
)
from backend.voicemail.models import VoicemailCategory, VoicemailPriority

RULES_VERSION = "vm-rules-2026.09.1"

# --- normalisation -------------------------------------------------------------

_CHAR_MAP = translation_table(
    {
        **LETTER_VARIANTS_EXTENDED,
        ZWNJ: " ",
        ZWJ: "",
        RLM: "",
        LRM: "",
        TATWEEL: "",
        **DIGITS,
    }
)
_DIACRITICS = re.compile(f"[{HARAKAT_CLASS}{QURANIC_MARKS_CLASS}]")
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_text(value: str) -> str:
    """Casefolded, Arabic-variant-free, ZWNJ-as-space, punctuation-free text."""
    text = unicodedata.normalize("NFKC", value or "").translate(_CHAR_MAP)
    text = _DIACRITICS.sub("", text).casefold()
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    # Word-start boundary only, so Persian/English suffixes still match
    # ("شکایت" matches "شکایتم", "bill" matches "billing").
    alternatives = sorted({normalize_text(p) for p in phrases if normalize_text(p)}, key=len)
    body = "|".join(re.escape(p) for p in reversed(alternatives))
    return re.compile(rf"(?<!\w)(?:{body})")


def _matches(pattern: re.Pattern[str], text: str) -> list[str]:
    return sorted({match.group(0) for match in pattern.finditer(text)})


# --- rules ---------------------------------------------------------------------

CATEGORY_RULES: dict[VoicemailCategory, tuple[str, ...]] = {
    VoicemailCategory.COMPLAINT: (
        "complaint",
        "complain",
        "unacceptable",
        "not happy",
        "unhappy",
        "disappointed",
        "terrible service",
        "worst",
        "poor service",
        "refund",
        "شکایت",
        "ناراضی",
        "اعتراض",
        "افتضاح",
        "بدترین",
        "برخورد بد",
        "پس بدید",
        "مرجوع",
    ),
    VoicemailCategory.SALES_INQUIRY: (
        "price",
        "pricing",
        "quote",
        "buy",
        "purchase",
        "interested in",
        "how much",
        "discount",
        "catalog",
        "order",
        "قیمت",
        "خرید",
        "بخرم",
        "استعلام",
        "پیش فاکتور",
        "تخفیف",
        "چند است",
        "چنده",
        "سفارش",
        "علاقه مند",
    ),
    VoicemailCategory.SUPPORT: (
        "not working",
        "doesn't work",
        "does not work",
        "broken",
        "error",
        "problem",
        "issue",
        "help",
        "can't log in",
        "cannot log in",
        "outage",
        "down",
        "کار نمیکند",
        "کار نمی کند",
        "خراب",
        "مشکل",
        "خطا",
        "ارور",
        "قطع",
        "پشتیبانی",
        "راهنمایی",
        "وصل نمیشه",
        "وصل نمی شه",
    ),
    VoicemailCategory.APPOINTMENT: (
        "appointment",
        "schedule",
        "reschedule",
        "booking",
        "book a",
        "meeting time",
        "visit",
        "نوبت",
        "وقت ملاقات",
        "رزرو",
        "جلسه",
        "زمان ملاقات",
        "وقت بگیرم",
    ),
    VoicemailCategory.CALLBACK_REQUEST: (
        "call me back",
        "call back",
        "callback",
        "return my call",
        "give me a call",
        "please call",
        "تماس بگیرید",
        "با من تماس",
        "باهام تماس",
        "زنگ بزنید",
        "تماس بگیرین",
        "زنگ بزنین",
        "با این شماره تماس",
    ),
    VoicemailCategory.BILLING: (
        "invoice",
        "bill",
        "charged",
        "charge",
        "payment",
        "overcharged",
        "receipt",
        "subscription fee",
        "فاکتور",
        "صورتحساب",
        "صورت حساب",
        "پرداخت",
        "قبض",
        "کسر شده",
        "کم شده",
        "واریز",
        "هزینه",
        "اشتراک",
    ),
    VoicemailCategory.SPAM: (
        "you have won",
        "you've won",
        "congratulations you",
        "free gift",
        "lottery",
        "limited time offer",
        "press 1",
        "برنده شدید",
        "قرعه کشی",
        "جایزه",
        "کد تخفیف ویژه",
        "عدد ۱ را",
        "عدد 1 را",
    ),
}

# When two categories score the same, the earlier one wins.
_CATEGORY_ORDER: tuple[VoicemailCategory, ...] = (
    VoicemailCategory.COMPLAINT,
    VoicemailCategory.BILLING,
    VoicemailCategory.SUPPORT,
    VoicemailCategory.CALLBACK_REQUEST,
    VoicemailCategory.APPOINTMENT,
    VoicemailCategory.SALES_INQUIRY,
    VoicemailCategory.SPAM,
)

CATEGORY_INTENT: dict[VoicemailCategory, str] = {
    VoicemailCategory.COMPLAINT: "report_dissatisfaction",
    VoicemailCategory.SALES_INQUIRY: "purchase_inquiry",
    VoicemailCategory.SUPPORT: "request_support",
    VoicemailCategory.APPOINTMENT: "schedule_appointment",
    VoicemailCategory.CALLBACK_REQUEST: "request_callback",
    VoicemailCategory.BILLING: "resolve_billing",
    VoicemailCategory.SPAM: "unsolicited",
    VoicemailCategory.OTHER: "unknown",
}

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "pricing": ("price", "pricing", "quote", "discount", "قیمت", "تخفیف", "استعلام"),
    "refund": ("refund", "money back", "پس بدید", "برگشت پول", "بازگشت وجه", "مرجوع"),
    "invoice": ("invoice", "bill", "receipt", "فاکتور", "صورتحساب", "صورت حساب", "قبض"),
    "payment": ("payment", "charged", "card", "پرداخت", "کسر شده", "واریز", "کارت"),
    "delivery": ("delivery", "shipping", "shipment", "ارسال", "تحویل", "پست", "مرسوله"),
    "account_access": (
        "log in",
        "login",
        "password",
        "account",
        "ورود",
        "رمز",
        "حساب کاربری",
        "پسورد",
    ),
    "connectivity": ("internet", "network", "outage", "اینترنت", "شبکه", "قطع"),
    "contract": ("contract", "cancel", "subscription", "قرارداد", "لغو", "اشتراک", "تمدید"),
    "installation": ("install", "setup", "نصب", "راه اندازی"),
    "scheduling": ("appointment", "schedule", "نوبت", "رزرو", "وقت ملاقات"),
}

URGENCY_TERMS: tuple[str, ...] = (
    "urgent",
    "urgently",
    "asap",
    "as soon as possible",
    "immediately",
    "emergency",
    "right away",
    "critical",
    "فوری",
    "فورا",
    "اضطراری",
    "هرچه سریعتر",
    "هر چه سریعتر",
    "همین الان",
    "سریعا",
    "خیلی ضروری",
    "ضروری",
)
NEGATIVE_TERMS: tuple[str, ...] = (
    "angry",
    "furious",
    "unacceptable",
    "terrible",
    "awful",
    "lawyer",
    "legal action",
    "sue",
    "worst",
    "fed up",
    "عصبانی",
    "افتضاح",
    "ناراضی",
    "شکایت",
    "وکیل",
    "دادگاه",
    "بدترین",
    "خسته شدم",
    "مسخره",
)
CHURN_TERMS: tuple[str, ...] = (
    "cancel my",
    "cancel the contract",
    "cancel subscription",
    "switch to another",
    "لغو قرارداد",
    "لغو اشتراک",
    "کنسل",
    "قطع همکاری",
)

_CATEGORY_PATTERNS = {cat: _phrase_pattern(p) for cat, p in CATEGORY_RULES.items()}
_TOPIC_PATTERNS = {topic: _phrase_pattern(p) for topic, p in TOPIC_RULES.items()}
_URGENCY = _phrase_pattern(URGENCY_TERMS)
_NEGATIVE = _phrase_pattern(NEGATIVE_TERMS)
_CHURN = _phrase_pattern(CHURN_TERMS)

# Fewer word characters than this is "no speech" (e.g. "uh", ".", "[noise]").
MIN_WORD_CHARS = 3
_WORD_CHARS = re.compile(r"\w", re.UNICODE)


# --- classification --------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationInput:
    text: str
    language: str | None = None


@dataclass(frozen=True)
class ClassificationResult:
    category: VoicemailCategory
    intent: str | None
    topics: tuple[str, ...]
    confidence: float | None
    needs_review: bool
    review_reason: str | None = None
    matched_terms: dict[str, list[str]] = field(default_factory=dict)


class VoicemailClassifier(Protocol):
    """What the pipeline needs from a classifier (rules today, LLM later)."""

    kind: str  # "rules" | "llm"
    version: str
    model_id: str | None

    def classify(self, item: ClassificationInput) -> ClassificationResult: ...


def is_empty_transcript(text: str) -> bool:
    return len(_WORD_CHARS.findall(normalize_text(text))) < MIN_WORD_CHARS


class RuleClassifier:
    kind = "rules"
    version = RULES_VERSION
    model_id: str | None = None

    def classify(self, item: ClassificationInput) -> ClassificationResult:
        if is_empty_transcript(item.text):
            return ClassificationResult(
                category=VoicemailCategory.EMPTY,
                intent=None,
                topics=(),
                confidence=None,
                needs_review=True,
                review_reason="no_speech",
            )
        text = normalize_text(item.text)
        hits = {cat: _matches(pattern, text) for cat, pattern in _CATEGORY_PATTERNS.items()}
        scores = {cat: len(terms) for cat, terms in hits.items()}
        best_score = max(scores.values())
        if best_score == 0:
            category = VoicemailCategory.OTHER
        else:
            category = next(c for c in _CATEGORY_ORDER if scores[c] == best_score)
        topics = tuple(topic for topic, pattern in _TOPIC_PATTERNS.items() if pattern.search(text))
        total = sum(scores.values())
        # A share of matched rules, not a model probability: 0 for "other".
        confidence = round(best_score / total, 3) if total else 0.0
        return ClassificationResult(
            category=category,
            intent=CATEGORY_INTENT[category],
            topics=topics,
            confidence=confidence,
            needs_review=False,
            matched_terms={c.value: t for c, t in hits.items() if t},
        )


# --- prioritisation --------------------------------------------------------------

CATEGORY_BASE_SCORE: dict[VoicemailCategory, int] = {
    VoicemailCategory.COMPLAINT: 30,
    VoicemailCategory.BILLING: 25,
    VoicemailCategory.SUPPORT: 25,
    VoicemailCategory.CALLBACK_REQUEST: 20,
    VoicemailCategory.SALES_INQUIRY: 20,
    VoicemailCategory.APPOINTMENT: 15,
    VoicemailCategory.OTHER: 10,
    VoicemailCategory.SPAM: 0,
    VoicemailCategory.EMPTY: 0,
}
URGENCY_WEIGHT = 35
NEGATIVE_WEIGHT = 15
CHURN_WEIGHT = 20
REPEAT_WEIGHT = 15  # one earlier voicemail from the caller inside the window
REPEAT_MANY_WEIGHT = 25  # two or more
VIP_WEIGHT = 25
REPEAT_WINDOW_HOURS = 72

PRIORITY_THRESHOLDS: tuple[tuple[int, VoicemailPriority], ...] = (
    (70, VoicemailPriority.URGENT),
    (45, VoicemailPriority.HIGH),
    (20, VoicemailPriority.NORMAL),
    (0, VoicemailPriority.LOW),
)


@dataclass(frozen=True)
class PriorityContext:
    recent_calls_from_caller: int = 0  # earlier voicemails inside the window
    caller_is_vip: bool = False


@dataclass(frozen=True)
class PriorityResult:
    priority: VoicemailPriority
    score: int
    reasons: tuple[dict, ...]


def prioritize(
    text: str, classification: ClassificationResult, context: PriorityContext
) -> PriorityResult:
    category = classification.category
    reasons: list[dict] = []
    if category in (VoicemailCategory.SPAM, VoicemailCategory.EMPTY):
        reasons.append({"code": f"category_{category.value}", "weight": 0})
        return PriorityResult(VoicemailPriority.LOW, 0, tuple(reasons))

    normalized = normalize_text(text)
    score = CATEGORY_BASE_SCORE[category]
    reasons.append({"code": "category_base", "weight": score, "category": category.value})

    def _add(code: str, weight: int, terms: list[str] | None = None) -> None:
        nonlocal score
        score += weight
        entry: dict = {"code": code, "weight": weight}
        if terms:
            entry["terms"] = terms
        reasons.append(entry)

    if urgency := _matches(_URGENCY, normalized):
        _add("urgency_terms", URGENCY_WEIGHT, urgency)
    if negative := _matches(_NEGATIVE, normalized):
        _add("negative_sentiment", NEGATIVE_WEIGHT, negative)
    if churn := _matches(_CHURN, normalized):
        _add("churn_risk", CHURN_WEIGHT, churn)
    if context.recent_calls_from_caller >= 2:
        _add("repeat_caller", REPEAT_MANY_WEIGHT)
        reasons[-1]["earlier_calls"] = context.recent_calls_from_caller
    elif context.recent_calls_from_caller == 1:
        _add("repeat_caller", REPEAT_WEIGHT)
        reasons[-1]["earlier_calls"] = 1
    if context.caller_is_vip:
        _add("vip_caller", VIP_WEIGHT)

    score = max(0, min(100, score))
    level = next(p for threshold, p in PRIORITY_THRESHOLDS if score >= threshold)
    return PriorityResult(level, score, tuple(reasons))
