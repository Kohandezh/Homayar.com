"""Plain messages for every refusal and abstention (HOM-97).

``explain`` turns a refused or abstained decision into a stable code and one message in
English and one in Persian.  The code and the messages depend on the route and the reason
only.  They never hold the key, the filters, the arguments, the intent or any request
value, so a made-up team id and a real team id outside the scope read the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.copilot_guard.defects import check_decision
from backend.copilot_intents import AbstainReason, RefusalReason, Route, RoutingDecision


@dataclass(frozen=True)
class Explanation:
    code: str  # "<route>.<reason>", for example "refuse.out_of_scope"
    detail: str | None  # decision.detail, unchanged
    message_en: str
    message_fa: str


_REFUSALS: dict[RefusalReason, tuple[str, str]] = {
    RefusalReason.OUT_OF_SCOPE: (
        "The Copilot answers only questions about your call analytics. This question is "
        "outside that, so it gets no answer.",
        "دستیار فقط به پرسش‌هایی درباره تحلیل تماس‌های شما پاسخ می‌دهد. این پرسش بیرون از "
        "این حوزه است، پس پاسخی ندارد.",
    ),
    RefusalReason.OUTSIDE_CALLER_SCOPE: (
        "This question asks about data outside your access. You can ask only about the teams "
        "and agents you are allowed to see, and some answers need access to the whole "
        "workspace.",
        "این پرسش درباره داده‌ای بیرون از دسترسی شماست. فقط می‌توانید درباره تیم‌ها و "
        "کارشناسانی بپرسید که اجازه دیدن آن‌ها را دارید، و برخی پاسخ‌ها به دسترسی کل فضای "
        "کاری نیاز دارند.",
    ),
    RefusalReason.SCOPE_NOT_ALLOWED: (
        "A question cannot choose its own workspace, organization, role or access. The "
        "Copilot always uses your own access.",
        "یک پرسش نمی‌تواند فضای کاری، سازمان، نقش یا دسترسی خودش را انتخاب کند. دستیار "
        "همیشه با دسترسی خود شما کار می‌کند.",
    ),
    RefusalReason.UNSUPPORTED_FILTER: (
        "This question uses a filter the Copilot does not support yet, for example a "
        "product, a service, a customer, a phone number or sentiment.",
        "این پرسش از فیلتری استفاده می‌کند که دستیار هنوز از آن پشتیبانی نمی‌کند، مثلاً "
        "محصول، سرویس، مشتری، شماره تلفن یا احساس مشتری.",
    ),
    RefusalReason.INVALID_REQUEST: (
        "The Copilot could not turn this question into a valid request. Please ask it again "
        "in a simpler way.",
        "دستیار نتوانست این پرسش را به یک درخواست معتبر تبدیل کند. لطفاً پرسش را ساده‌تر "
        "دوباره بپرسید.",
    ),
    RefusalReason.CAUSAL_NOT_SUPPORTED: (
        "The Copilot cannot say why one thing causes another. The data shows only how often "
        "things happen together.",
        "دستیار نمی‌تواند بگوید چرا یک چیز باعث چیز دیگری می‌شود. داده‌ها فقط نشان می‌دهند "
        "که چیزها چند بار با هم رخ می‌دهند.",
    ),
    RefusalReason.INDIVIDUAL_RANKING_NOT_SUPPORTED: (
        "The Copilot does not rank or compare individual agents. You can ask about one "
        "agent's own results, or about teams.",
        "دستیار کارشناسان را به‌صورت فردی رتبه‌بندی یا مقایسه نمی‌کند. می‌توانید درباره "
        "نتایج یک کارشناس یا درباره تیم‌ها بپرسید.",
    ),
    RefusalReason.FORECAST_NOT_SUPPORTED: (
        "The Copilot does not predict future numbers. It answers only with numbers counted "
        "from past calls.",
        "دستیار عددهای آینده را پیش‌بینی نمی‌کند. فقط با عددهایی پاسخ می‌دهد که از "
        "تماس‌های گذشته شمرده شده‌اند.",
    ),
}

_ABSTENTIONS: dict[AbstainReason, tuple[str, str]] = {
    AbstainReason.BELOW_MINIMUM_SAMPLE: (
        "There are too few conversations for a reliable answer, so the Copilot gives no answer.",
        "تعداد گفتگوها برای یک پاسخ قابل اعتماد کافی نیست، پس دستیار پاسخی نمی‌دهد.",
    ),
    AbstainReason.NOT_COMPUTABLE: (
        "This number cannot be computed from the data that exists now, so the Copilot gives "
        "no answer.",
        "این عدد با داده‌هایی که اکنون وجود دارد قابل محاسبه نیست، پس دستیار پاسخی نمی‌دهد.",
    ),
    AbstainReason.NO_EVIDENCE: (
        "No evidence that you can see supports an answer, so the Copilot gives no answer.",
        "هیچ شواهدی در دسترس شما از یک پاسخ پشتیبانی نمی‌کند، پس دستیار پاسخی نمی‌دهد.",
    ),
}


def explain(decision: RoutingDecision) -> Explanation:
    """The code and the messages of a refused or abstained decision.  ``code`` is
    ``"<route>.<reason>"``; ``detail`` is the decision's detail, unchanged.  ``ValueError``
    for an answered decision, and for a reason of the wrong kind for the route."""
    decision = check_decision(decision)
    reason = decision.reason
    if decision.route is Route.REFUSE and isinstance(reason, RefusalReason):
        message_en, message_fa = _REFUSALS[reason]
    elif decision.route is Route.ABSTAIN and isinstance(reason, AbstainReason):
        message_en, message_fa = _ABSTENTIONS[reason]
    else:
        raise ValueError("only a refused or abstained decision with its own reason is explained")
    return Explanation(
        code=f"{decision.route.value}.{reason.value}",
        detail=decision.detail,
        message_en=message_en,
        message_fa=message_fa,
    )
