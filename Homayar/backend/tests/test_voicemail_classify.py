from __future__ import annotations

import pytest

from backend.voicemail.classify import (
    RULES_VERSION,
    ClassificationInput,
    PriorityContext,
    RuleClassifier,
    normalize_text,
    prioritize,
)
from backend.voicemail.models import VoicemailCategory, VoicemailPriority

CLASSIFIER = RuleClassifier()


def _classify(text: str):
    return CLASSIFIER.classify(ClassificationInput(text=text))


def test_normalizer_tolerates_arabic_letters_zwnj_digits_and_diacritics() -> None:
    assert normalize_text("شكايت  دارم") == normalize_text("شکایت دارم")
    assert normalize_text("کار نمی‌کند") == "کار نمی کند"
    assert normalize_text("۰۹۱۲") == "0912"
    assert normalize_text("فوراً!") == "فورا"
    assert normalize_text("URGENT, Please") == "urgent please"


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("سلام، از خدمات شما خیلی ناراضی هستم و شکایت دارم", VoicemailCategory.COMPLAINT),
        ("سلام، قیمت پکیج سازمانی چنده؟ می‌خواستم خرید کنم", VoicemailCategory.SALES_INQUIRY),
        ("اینترنت ما از صبح قطع است و کار نمی‌کند", VoicemailCategory.SUPPORT),
        ("می‌خواستم برای هفته بعد نوبت رزرو کنم", VoicemailCategory.APPOINTMENT),
        ("لطفاً با این شماره تماس بگیرید", VoicemailCategory.CALLBACK_REQUEST),
        ("فاکتور ماه قبل دوبار از کارت کسر شده", VoicemailCategory.BILLING),
        ("تبریک! شما در قرعه کشی برنده شدید", VoicemailCategory.SPAM),
        (
            "Hi, I am very unhappy, this is unacceptable, I want to file a complaint",
            VoicemailCategory.COMPLAINT,
        ),
        ("Hello, how much is the premium plan? I'd like a quote", VoicemailCategory.SALES_INQUIRY),
        ("Please call me back when you get this", VoicemailCategory.CALLBACK_REQUEST),
        ("I was charged twice on my last invoice", VoicemailCategory.BILLING),
        ("I'd like to reschedule my appointment", VoicemailCategory.APPOINTMENT),
        ("Just wanted to say hello to the team", VoicemailCategory.OTHER),
    ],
)
def test_rule_categories_persian_and_english(text: str, category: VoicemailCategory) -> None:
    result = _classify(text)
    assert result.category is category
    assert result.needs_review is False
    assert result.intent


def test_arabic_letter_variants_classify_like_persian() -> None:
    # Arabic kaf/yeh variants of "شکایت" and "ناراضی"
    assert _classify("من شكايت دارم و ناراضي هستم").category is VoicemailCategory.COMPLAINT


def test_topics_are_detected() -> None:
    result = _classify("I was charged on the invoice and want a refund for the delivery")
    assert {"invoice", "payment", "refund", "delivery"} <= set(result.topics)


@pytest.mark.parametrize("text", ["", "   ", "…", "اِ", "uh", "?!"])
def test_empty_transcript_needs_review_and_is_never_fabricated(text: str) -> None:
    result = _classify(text)
    assert result.category is VoicemailCategory.EMPTY
    assert result.needs_review and result.review_reason == "no_speech"
    assert result.intent is None and result.topics == () and result.confidence is None
    priority = prioritize(
        text, result, PriorityContext(recent_calls_from_caller=5, caller_is_vip=True)
    )
    assert priority.priority is VoicemailPriority.LOW and priority.score == 0


def test_priority_reasons_are_explained() -> None:
    text = "این فوری است! از خدمات شما ناراضی هستم و می‌خواهم لغو قرارداد کنم"
    result = _classify(text)
    priority = prioritize(
        text, result, PriorityContext(recent_calls_from_caller=2, caller_is_vip=True)
    )
    codes = [reason["code"] for reason in priority.reasons]
    assert codes == [
        "category_base",
        "urgency_terms",
        "negative_sentiment",
        "churn_risk",
        "repeat_caller",
        "vip_caller",
    ]
    assert priority.score == 100  # clamped
    assert priority.priority is VoicemailPriority.URGENT
    urgency = next(r for r in priority.reasons if r["code"] == "urgency_terms")
    assert urgency["terms"] == ["فوری"]


def test_priority_levels_and_repeat_caller_weight() -> None:
    text = "Please call me back"
    result = _classify(text)
    assert prioritize(text, result, PriorityContext()).priority is VoicemailPriority.NORMAL
    one = prioritize(text, result, PriorityContext(recent_calls_from_caller=1))
    assert one.score == 35 and one.priority is VoicemailPriority.NORMAL
    urgent = prioritize(
        "urgent, please call me back asap",
        _classify("urgent please call me back asap"),
        PriorityContext(recent_calls_from_caller=1),
    )
    assert urgent.priority is VoicemailPriority.URGENT
    low = prioritize("hello team", _classify("hello team"), PriorityContext())
    assert low.priority is VoicemailPriority.LOW


def test_spam_is_low_priority_even_with_urgency() -> None:
    text = "urgent! you have won a free gift, press 1"
    result = _classify(text)
    assert result.category is VoicemailCategory.SPAM
    assert prioritize(text, result, PriorityContext()).priority is VoicemailPriority.LOW


def test_classifier_provenance_is_versioned_rules() -> None:
    assert CLASSIFIER.kind == "rules"
    assert CLASSIFIER.version == RULES_VERSION
    assert CLASSIFIER.model_id is None


def test_classification_is_deterministic() -> None:
    text = "سلام، فاکتور من اشتباه است، لطفا تماس بگیرید"
    assert _classify(text) == _classify(text)
