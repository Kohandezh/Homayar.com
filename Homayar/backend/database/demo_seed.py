"""Demo workspace seed (HOM-175): believable SaaS data for sales demos.

``python -m backend.database.demo_seed`` (``make demo-seed``) fills a fresh
database with one organization, one workspace on a paid plan, one account per
workspace role, four teams, 270 meetings in every status the product console
shows, a dead-lettered job, usage, API keys, share links and audit history.
Nothing comes from real people or real audio: accounts are on the reserved
``example.com`` domain, recordings are a few seconds of generated near-silence
(unique per meeting, so no demo call is a media duplicate of another), and
transcripts come from scripted in-process providers.

Every row is written through the existing services (or, where no service
exists, the same model writes the bootstrap code uses) on the application
engine, so row-level security and model invariants apply exactly as for a
request. Meetings are processed by the real job processor.

Every meeting enters through the ingestion ledger (HOM-326), as an upload
through the console does: ``accept_ingestion`` writes an accepted ledger row
and creates the meeting and its job in one transaction. The trend views and
the organization overview count meetings by the ledger's ``received_at`` in
closed weeks, so each row is dated in one of the 4 closed weeks before the
current Asia/Tehran week (Saturday 00:00), from the meeting's index alone.
The rows age: a week after the seed the oldest week leaves the 4-week window,
so a shared demo database needs a fresh seed to show every meeting there.

The seed holds two plans, both through the same ledger and processor:

- **The console plan** (30 meetings, HOM-175/HOM-326): every status the
  console shows, scripted mock providers marked ``is_mock``. Mock transcripts
  and mock analyses are excluded from the trend counts by design (a mock
  states nothing it can know), so these meetings populate the meetings,
  report, transcript and evidence pages without touching the trends.
- **The call plan** (240 meetings, HOM-365): scripted customer calls, 30 per
  closed week for ``Call Center`` and for ``Customer Care``. Their providers
  are the same scripts but not marked mock: the scripted intelligence returns
  a real taxonomy extraction (HOM-53) whose labels cite turns of the call's
  script, so the real analysis stage judges, stores and counts them. The
  owner ruled on 2026-11-09 that these 240 calls are stored as non-mock
  analysis, because the trend views never count a mock analysis and the
  product rule is not changed for a demo. For these 240 calls only:
  the call report has no mock notice and can be complete; the export has no
  "Mock output: yes" line; coverage counts them as evaluated; the console
  shows no mock badge; the processing audit records ``mock: false``. What
  still marks them as demo data: the provider names ``demo_seed_call_*``,
  the ``example.com`` accounts, and the generated near-silence recordings.
  The call plan gives the default ``/trends`` query (4 closed Tehran weeks) at
  least 30 assessable conversations per team per week: real percent values
  and trend lines, with enough outcome abstentions that the outcome rows
  still show "Too few calls" honestly. The two call teams share the five demo
  agents; the weekly intent mix moves so the lines move. A database seeded
  by an older seed (fewer or different meetings) must be reset and seeded
  again. The 30 console meetings stay mock.

The trend views only release a cohort with 0 or at least 5 distinct agents.
Five extra agents (``agent1`` to ``agent5``) make that possible. They are
workspace members with no password, so nobody can sign in as them, and they
are not printed as logins. They are the agents of ``Call Center`` and
``Customer Care``. ``Sales`` and ``Support`` keep their one agent each
(member and viewer) and are withheld as small groups. Three console-plan
meetings with no team let the rest of the workspace pass the same weekly
test. ``MEETING_ATTRIBUTION`` holds the team, agent and week of each console
meeting; ``TREND_CALL_BLOCKS`` holds the call plan.

The seed is idempotent: each object has a fixed natural key and is skipped
when it already exists, and audit events are only written together with the
object they describe, so a second run writes nothing. A database seeded by an
older seed, whose demo meetings have no ledger row, is refused before any
write to its workspace: skipping those meetings would leave them out of every
trend view. It must be reset and seeded again.

It refuses every environment except development and test before it opens a
database connection.

The dating instant (HOM-365): ``seed_demo_workspace(settings, now=...)``
takes a timezone-aware datetime used only to choose the anchor week
(``current_week_start(now)``) every ledger row is dated from; ``None`` means
``datetime.now(UTC)``, read once per run, so the CLI and ``make demo-seed``
stay relative to today. Everything else keeps the real clocks: ingestion
acceptance, job deadlines, processing, audit and logins. A naive ``now``
raises ``ValueError`` before any database write.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.config import Settings
    from backend.database.saas_models import Meeting, User, Workspace

ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})

DEMO_ORGANIZATION_SLUG = "homayar-demo"
DEMO_WORKSPACE_SLUG = "default"
DEMO_WORKSPACE_NAME = "شرکت نمونه همایار"
DEMO_PLAN_KEY = "business"
DEMO_EMAIL_DOMAIN = "demo.example.com"
# Readable and low-entropy on purpose (secret scanners, sales demos).
DEMO_PASSWORD = "Homayar-Demo-1405"  # noqa: S105 - public demo credential
DEMO_ROLES = ("owner", "admin", "member", "viewer")
DEMO_DISPLAY_NAMES = {
    "owner": "سارا احمدی",
    "admin": "Reza Karimi",
    "member": "مریم حسینی",
    "viewer": "Ali Rahimi",
}
# Fictional agents (HOM-326): workspace members without a password, not logins.
DEMO_AGENTS: tuple[tuple[str, str], ...] = (
    ("agent1", "نگار موسوی"),
    ("agent2", "Kian Tehrani"),
    ("agent3", "پرهام صادقی"),
    ("agent4", "Leila Nouri"),
    ("agent5", "آرش کاظمی"),
)
AUDIO_SECONDS_LIMIT = 36_000
CONCURRENT_JOBS_LIMIT = 10
DEMO_RETENTION_DAYS = 180
SAMPLE_RATE_HZ = 8_000
WORKER_ID = "demo-seed"
API_KEY_NAME = "CRM integration (demo)"
API_KEY_SCOPES = ("meeting:read", "meeting:create")
SCRIPTED_PROVIDER_VERSION = "demo-seed-v1"
CALL_PROVIDER_VERSION = "demo-seed-calls-v1"


class DemoSeedRefused(RuntimeError):
    """The seed does not run in this environment, or on this database."""


@dataclass(frozen=True)
class DemoAccount:
    role: str
    email: str
    password: str
    display_name: str


@dataclass(frozen=True)
class DemoSeedResult:
    workspace_id: str
    organization_id: str
    workspace_name: str
    accounts: Mapping[str, DemoAccount]
    meeting_count: int
    console_url: str | None


# --- scripted content ------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


@dataclass(frozen=True)
class Script:
    language: str
    turns: tuple[Turn, ...]
    summary: str
    actions: tuple[tuple[str, str, int], ...]  # description, owner, source turn
    decision: tuple[str, int]  # description, source turn


SCRIPTS: tuple[Script, ...] = (
    Script(
        language="fa",
        turns=(
            Turn("SPEAKER_00", "سلام، برای پیگیری سفارش شمارهٔ دوازده تماس گرفتم."),
            Turn("SPEAKER_01", "سلام، سفارش شما فردا ارسال می‌شود."),
            Turn("SPEAKER_00", "لطفاً کد رهگیری را هم پیامک کنید."),
            Turn("SPEAKER_01", "حتماً، تا پایان امروز ارسال می‌کنم."),
        ),
        summary="مشتری وضعیت سفارش را پرسید؛ ارسال فردا انجام می‌شود.",
        actions=(("ارسال کد رهگیری با پیامک", "پشتیبانی", 3),),
        decision=("ارسال سفارش فردا", 1),
    ),
    Script(
        language="en",
        turns=(
            Turn("SPEAKER_00", "Thanks for joining the quarterly pricing review."),
            Turn("SPEAKER_01", "We propose a ten percent discount for annual plans."),
            Turn("SPEAKER_00", "Finance must approve it before Friday."),
            Turn("SPEAKER_01", "I will send the proposal to finance today."),
        ),
        summary="The team reviewed annual pricing and a ten percent discount.",
        actions=(("Send the discount proposal to finance", "Sales", 3),),
        decision=("Offer a ten percent annual discount", 1),
    ),
    Script(
        language="fa",
        turns=(
            Turn("SPEAKER_00", "جلسهٔ هفتگی تیم فروش را شروع کنیم."),
            Turn("SPEAKER_01", "این هفته هشت قرارداد جدید بستیم."),
            Turn("SPEAKER_00", "عالی است. گزارش را برای مدیریت بفرستید."),
            Turn("SPEAKER_01", "گزارش را پنجشنبه آماده می‌کنم."),
        ),
        summary="تیم فروش هشت قرارداد جدید بست و گزارش هفتگی آماده می‌شود.",
        actions=(("آماده‌سازی گزارش هفتگی فروش", "فروش", 3),),
        decision=("ارسال گزارش به مدیریت", 2),
    ),
    Script(
        language="en",
        turns=(
            Turn("SPEAKER_00", "Hello, I cannot log in to the customer portal."),
            Turn("SPEAKER_01", "I reset your password; please check your email."),
            Turn("SPEAKER_00", "It works now, thank you."),
            Turn("SPEAKER_01", "Great, I will close the ticket."),
        ),
        summary="A customer could not log in; support reset the password.",
        actions=(("Close the login support ticket", "Support", 3),),
        decision=("Reset the customer password", 1),
    ),
    Script(
        language="fa",
        turns=(
            Turn("SPEAKER_00", "مشتری از تأخیر در نصب دستگاه ناراضی است."),
            Turn("SPEAKER_01", "تکنسین فردا صبح به محل اعزام می‌شود."),
            Turn("SPEAKER_00", "یک تخفیف جبرانی هم پیشنهاد بدهیم."),
            Turn("SPEAKER_01", "موافقم، ده درصد تخفیف ثبت می‌کنم."),
        ),
        summary="برای جبران تأخیر نصب، تکنسین اعزام و تخفیف ثبت می‌شود.",
        actions=(("اعزام تکنسین برای نصب", "خدمات فنی", 1), ("ثبت تخفیف ده درصدی", "مالی", 3)),
        decision=("پیشنهاد تخفیف جبرانی", 2),
    ),
    Script(
        language="en",
        turns=(
            Turn("SPEAKER_00", "Let us plan the onboarding for the new branch."),
            Turn("SPEAKER_01", "Training starts next Monday for twelve agents."),
            Turn("SPEAKER_00", "Please share the training schedule with HR."),
            Turn("SPEAKER_01", "Sure, I will share it this afternoon."),
        ),
        summary="Onboarding for twelve agents in the new branch starts Monday.",
        actions=(("Share the training schedule with HR", "Operations", 3),),
        decision=("Start training next Monday", 1),
    ),
)

TITLES: tuple[str, ...] = (
    "پیگیری سفارش مشتری",
    "Quarterly pricing review",
    "جلسهٔ هفتگی تیم فروش",
    "Customer portal login issue",
    "شکایت از تأخیر نصب",
    "New branch onboarding",
)

# --- scripted customer calls (HOM-365) -------------------------------------------------


@dataclass(frozen=True)
class CallScript:
    """A scripted customer call whose intelligence returns a real taxonomy
    extraction: the labels (intent, topics, outcome or an outcome abstention)
    cite turns of this script, so the real analysis stage accepts them."""

    key: str
    language: str
    title: str
    turns: tuple[Turn, ...]
    summary: str
    action: tuple[str, str, int]  # description, owner, source turn
    decision: tuple[str, int]  # description, source turn
    intent: str  # taxonomy intent label id
    intent_turn: int
    topics: tuple[tuple[str, int], ...]  # topic label id, source turn
    outcome: str | None  # taxonomy outcome label id; None abstains (unclear ending)
    outcome_turn: int


CALL_SCRIPTS: dict[str, CallScript] = {
    call.key: call
    for call in (
        CallScript(
            key="support_connectivity",
            language="en",
            title="Internet keeps dropping",
            turns=(
                Turn("SPEAKER_00", "Hello, my home internet keeps dropping every evening."),
                Turn("SPEAKER_01", "I am sorry. I see line errors on your port since Monday."),
                Turn("SPEAKER_00", "Can someone check the line, please?"),
                Turn("SPEAKER_01", "I will schedule a technician visit for Thursday morning."),
            ),
            summary="The customer reported an unstable line; a technician visit was scheduled.",
            action=("Schedule the technician visit", "Network Team", 3),
            decision=("Technician visit on Thursday", 3),
            intent="request_support",
            intent_turn=0,
            topics=(("connectivity", 0),),
            outcome="follow_up_scheduled",
            outcome_turn=3,
        ),
        CallScript(
            key="support_connectivity_unresolved",
            language="fa",
            title="کندی اینترنت",
            turns=(
                Turn("SPEAKER_00", "سلام، اینترنت ما چند روز است خیلی کند است."),
                Turn("SPEAKER_01", "بررسی کردم؛ از سمت خط مشکلی نمی‌بینم."),
                Turn("SPEAKER_00", "پس چه کاری باید بکنیم؟"),
                Turn("SPEAKER_01", "فعلاً کاری از دست ما برنمی‌آید؛ اگر ادامه داشت خبر بدهید."),
            ),
            summary="مشتری از کندی اینترنت گفت؛ بررسی نتیجه‌ای نداشت و برای پیگیری، تیکت ثبت شد.",
            action=("ثبت تیکت پیگیری کندی", "پشتیبانی", 3),
            decision=("بدون اقدام مشخص", 3),
            intent="request_support",
            intent_turn=0,
            topics=(("connectivity", 0),),
            outcome="unresolved",
            outcome_turn=3,
        ),
        CallScript(
            key="support_technical",
            language="fa",
            title="خرابی مودم",
            turns=(
                Turn("SPEAKER_00", "سلام، مودم من روشن نمی‌شود."),
                Turn("SPEAKER_01", "چند ثانیه دکمهٔ ریست را نگه دارید."),
                Turn("SPEAKER_00", "امتحان کردم، بالا نمی‌آید."),
                Turn("SPEAKER_01", "پس به واحد فنی ارجاع می‌دهم تا تماس بگیرند."),
            ),
            summary="مودم مشتری روشن نمی‌شد؛ موضوع به واحد فنی ارجاع شد.",
            action=("ارجاع به واحد فنی", "واحد فنی", 3),
            decision=("ارجاع به واحد فنی", 3),
            intent="request_support",
            intent_turn=0,
            topics=(("technical_fault", 1),),
            outcome="escalated",
            outcome_turn=3,
        ),
        CallScript(
            key="support_account",
            language="en",
            title="Cannot sign in to the portal",
            turns=(
                Turn("SPEAKER_00", "Hi, I cannot sign in to the customer portal."),
                Turn("SPEAKER_01", "I have reset your password; please check your email."),
                Turn("SPEAKER_00", "It works now, thank you."),
                Turn("SPEAKER_01", "You are welcome. Enjoy your day."),
            ),
            summary="A locked-out customer signed in again after a password reset.",
            action=("Close the sign-in ticket", "Support", 3),
            decision=("Reset the password", 1),
            intent="request_support",
            intent_turn=0,
            topics=(("account_access", 0),),
            outcome="resolved",
            outcome_turn=2,
        ),
        CallScript(
            key="support_abstain",
            language="fa",
            title="قطعی پراکنده خط",
            turns=(
                Turn("SPEAKER_00", "سلام، خط تلفن ما گاهی قطع می‌شود."),
                Turn("SPEAKER_01", "چند بار اتفاق افتاده؟"),
                Turn("SPEAKER_00", "نمی‌دانم، همسایه‌ها هم گفته‌اند شاید."),
                Turn("SPEAKER_01", "ثبت می‌کنم؛ اگر نمونهٔ مشخصی داشتید زنگ بزنید."),
            ),
            summary="گزارش مبهم از قطعی پراکنده خط؛ شواهد کافی برای جمع‌بندی نبود.",
            action=("ثبت گزارش قطعی پراکنده", "پشتیبانی", 3),
            decision=("ثبت گزارش بدون جمع‌بندی", 3),
            intent="request_support",
            intent_turn=0,
            topics=(("connectivity", 0),),
            outcome=None,
            outcome_turn=3,
        ),
        CallScript(
            key="billing_invoice",
            language="fa",
            title="مبلغ فاکتور اشتباه",
            turns=(
                Turn("SPEAKER_00", "سلام، مبلغ فاکتور این ماه اشتباه است."),
                Turn("SPEAKER_01", "بررسی می‌کنم؛ بله، یک ردیف تکراری دیده می‌شود."),
                Turn("SPEAKER_00", "لطفاً اصلاح کنید."),
                Turn("SPEAKER_01", "فاکتور اصلاحی تا فردا صبح برایتان ارسال می‌شود."),
            ),
            summary="فاکتور مشتری ردیف تکراری داشت؛ فاکتور اصلاحی ارسال می‌شود.",
            action=("ارسال فاکتور اصلاحی", "مالی", 3),
            decision=("اصلاح فاکتور", 1),
            intent="resolve_billing",
            intent_turn=0,
            topics=(("invoice", 0),),
            outcome="follow_up_scheduled",
            outcome_turn=3,
        ),
        CallScript(
            key="billing_payment",
            language="en",
            title="Payment failed twice",
            turns=(
                Turn("SPEAKER_00", "Hello, my monthly payment failed twice."),
                Turn("SPEAKER_01", "Your bank declined it; let me retry it now."),
                Turn("SPEAKER_00", "Please do."),
                Turn("SPEAKER_01", "It went through. You are all set."),
            ),
            summary="A declined payment succeeded on retry during the call.",
            action=("Confirm the payment", "Billing", 3),
            decision=("Retry the payment", 1),
            intent="resolve_billing",
            intent_turn=0,
            topics=(("payment", 0),),
            outcome="resolved",
            outcome_turn=3,
        ),
        CallScript(
            key="billing_unresolved",
            language="en",
            title="Charged twice this month",
            turns=(
                Turn("SPEAKER_00", "Hi, I was charged twice for this month."),
                Turn("SPEAKER_01", "I see both charges; I cannot reverse them from here."),
                Turn("SPEAKER_00", "So what happens now?"),
                Turn("SPEAKER_01", "Our finance team has to look at it; I cannot promise a date."),
            ),
            summary="A double charge was confirmed but no fix was agreed on the call.",
            action=("Open a finance case", "Finance", 3),
            decision=("Hand the double charge to finance", 3),
            intent="resolve_billing",
            intent_turn=0,
            topics=(("payment", 0),),
            outcome="unresolved",
            outcome_turn=3,
        ),
        CallScript(
            key="billing_abstain",
            language="fa",
            title="سؤال دربارهٔ صورتحساب",
            turns=(
                Turn("SPEAKER_00", "سلام، دربارهٔ صورتحساب ماه گذشته سؤالی دارم."),
                Turn("SPEAKER_01", "بفرمایید؛ فاکتور را باز کردم."),
                Turn("SPEAKER_00", "این ردیف آخر برای چیست؟ خط بد قطع شد..."),
                Turn("SPEAKER_01", "هلو؟ شنیدید؟ بعداً دوباره تماس بگیرید."),
            ),
            summary="سؤال مشتری دربارهٔ ردیف فاکتور ناتمام ماند؛ خط قطع شد.",
            action=("ثبت تماس مجدد", "مالی", 3),
            decision=("تماس قطع شد", 3),
            intent="resolve_billing",
            intent_turn=0,
            topics=(("invoice", 0),),
            outcome=None,
            outcome_turn=3,
        ),
        CallScript(
            key="complaint_refund",
            language="fa",
            title="شکایت از بازگشت وجه",
            turns=(
                Turn("SPEAKER_00", "سلام، دو هفته است پول سفارشم برگشته نشده و خیلی عصبانی‌ام."),
                Turn("SPEAKER_01", "حق دارید؛ درخواست بازگشت وجه را ثبت می‌کنم."),
                Turn("SPEAKER_00", "کِی برمی‌گردد؟"),
                Turn("SPEAKER_01", "به سرپرست هم ارجاع می‌دهم که پیگیری سریع‌تر شود."),
            ),
            summary="مشتری از تأخیر بازگشت وجه شکایت کرد؛ موضوع به سرپرست ارجاع شد.",
            action=("ارجاع شکایت به سرپرست", "سرپرست", 3),
            decision=("ثبت و ارجاع درخواست بازگشت وجه", 1),
            intent="report_dissatisfaction",
            intent_turn=0,
            topics=(("refund", 0),),
            outcome="escalated",
            outcome_turn=3,
        ),
        CallScript(
            key="complaint_delivery",
            language="en",
            title="Package a week late",
            turns=(
                Turn("SPEAKER_00", "Hello, I want to complain: my package is a week late."),
                Turn("SPEAKER_01", "I am sorry about that; I see it left the hub yesterday."),
                Turn("SPEAKER_00", "I needed it last week."),
                Turn("SPEAKER_01", "I understand; I can only ask the courier to prioritize it."),
            ),
            summary="A late delivery complaint; no new delivery date could be promised.",
            action=("Flag the shipment as priority", "Logistics", 3),
            decision=("Prioritize the late shipment", 3),
            intent="report_dissatisfaction",
            intent_turn=0,
            topics=(("delivery", 0),),
            outcome="unresolved",
            outcome_turn=3,
        ),
        CallScript(
            key="complaint_refund_abstain",
            language="fa",
            title="شکایت از تأخیر بازپرداخت",
            turns=(
                Turn("SPEAKER_00", "سلام، گفته بودید سه روزه وجه برگردد؛ نشده."),
                Turn("SPEAKER_01", "یک لحظه بررسی می‌کنم... سیستم کند است."),
                Turn("SPEAKER_00", "خب، منتظرم."),
                Turn("SPEAKER_01", "هنوز جوابی ندارم؛ تا آخر روز خبر می‌شوم شاید."),
            ),
            summary="شکایت از تأخیر بازپرداخت؛ پایان تماس روشن نبود.",
            action=("پیگیری وضعیت بازپرداخت", "مالی", 3),
            decision=("پایان نامشخص", 3),
            intent="report_dissatisfaction",
            intent_turn=0,
            topics=(("refund", 0),),
            outcome=None,
            outcome_turn=3,
        ),
        CallScript(
            key="sales_pricing",
            language="en",
            title="Asking about plan prices",
            turns=(
                Turn("SPEAKER_00", "Hello, I am comparing plans for my small business."),
                Turn("SPEAKER_01", "Happy to help; the business plan fits five people."),
                Turn("SPEAKER_00", "What does it cost per month?"),
                Turn("SPEAKER_01", "I will email you the full quote today."),
            ),
            summary="A prospect asked for plan pricing; a quote was promised.",
            action=("Email the pricing quote", "Sales", 3),
            decision=("Send a business-plan quote", 3),
            intent="purchase_inquiry",
            intent_turn=0,
            topics=(("pricing", 1),),
            outcome="follow_up_scheduled",
            outcome_turn=3,
        ),
        CallScript(
            key="sales_pricing_resolved",
            language="fa",
            title="استعلام قیمت اشتراک",
            turns=(
                Turn("SPEAKER_00", "سلام، قیمت اشتراک سالانه چقدر است؟"),
                Turn("SPEAKER_01", "سالانه دو میلیون و چهارصد هزار تومان است."),
                Turn("SPEAKER_00", "تخفیفی هم دارد؟"),
                Turn("SPEAKER_01", "بله، پرداخت یکجا ده درصد تخفیف دارد."),
                Turn("SPEAKER_00", "ممنون، پاسخم را کامل گرفتم."),
            ),
            summary="مشتری قیمت سالانه و تخفیف آن را پرسید و پاسخ کامل گرفت.",
            action=("ثبت فرصت فروش", "فروش", 4),
            decision=("اطلاع از قیمت سالانه", 1),
            intent="purchase_inquiry",
            intent_turn=0,
            topics=(("pricing", 0),),
            outcome="resolved",
            outcome_turn=4,
        ),
    )
}

# The weekly intent mix of one call team: (script key, how many calls). Sums
# to 30 a week; the mix moves week by week so the workspace trend lines move
# (AC3). Every call states its intent and topics; the ``*_abstain`` scripts
# leave the outcome unstated, so the outcome rows of each team-week sit under
# the sample minimum of 30 (AC4) while the intent rows keep the full 30 (AC1).
_WEEK_MIXES: dict[int, tuple[tuple[str, int], ...]] = {
    1: (
        ("support_connectivity", 3),
        ("support_connectivity_unresolved", 2),
        ("support_technical", 2),
        ("support_account", 2),
        ("support_abstain", 3),
        ("billing_invoice", 2),
        ("billing_payment", 2),
        ("billing_unresolved", 2),
        ("billing_abstain", 2),
        ("complaint_refund", 2),
        ("complaint_delivery", 2),
        ("complaint_refund_abstain", 2),
        ("sales_pricing", 2),
        ("sales_pricing_resolved", 2),
    ),
    2: (
        ("support_connectivity", 2),
        ("support_connectivity_unresolved", 2),
        ("support_technical", 1),
        ("support_account", 2),
        ("support_abstain", 2),
        ("billing_invoice", 3),
        ("billing_payment", 2),
        ("billing_unresolved", 3),
        ("billing_abstain", 3),
        ("complaint_refund", 3),
        ("complaint_delivery", 2),
        ("complaint_refund_abstain", 2),
        ("sales_pricing", 2),
        ("sales_pricing_resolved", 1),
    ),
    3: (
        ("support_connectivity", 3),
        ("support_connectivity_unresolved", 3),
        ("support_technical", 2),
        ("support_account", 2),
        ("support_abstain", 3),
        ("billing_invoice", 2),
        ("billing_payment", 1),
        ("billing_unresolved", 2),
        ("billing_abstain", 2),
        ("complaint_refund", 2),
        ("complaint_delivery", 2),
        ("complaint_refund_abstain", 1),
        ("sales_pricing", 3),
        ("sales_pricing_resolved", 2),
    ),
    4: (
        ("support_connectivity", 3),
        ("support_connectivity_unresolved", 2),
        ("support_technical", 2),
        ("support_account", 1),
        ("support_abstain", 2),
        ("billing_invoice", 2),
        ("billing_payment", 2),
        ("billing_unresolved", 2),
        ("billing_abstain", 3),
        ("complaint_refund", 3),
        ("complaint_delivery", 3),
        ("complaint_refund_abstain", 2),
        ("sales_pricing", 2),
        ("sales_pricing_resolved", 1),
    ),
}

CALL_CENTER = "Call Center"
CUSTOMER_CARE = "Customer Care"

# (team name, closed week before the anchor, script key, call count). The
# call plan of HOM-365: 30 assessable calls per team per closed week. Tests
# that do not read the trends shrink this to ``()`` (AC8 runtime budget).
TREND_CALL_BLOCKS: tuple[tuple[str, int, str, int], ...] = tuple(
    (team, week, key, count)
    for team in (CALL_CENTER, CUSTOMER_CARE)
    for week in (1, 2, 3, 4)
    for key, count in _WEEK_MIXES[week]
)

# (status, how many). 30 meetings in total: the console plan of HOM-175.
MEETING_PLAN: tuple[tuple[str, int], ...] = (
    ("completed", 20),
    ("partial", 3),
    ("failed", 2),
    ("dead_letter", 1),
    ("processing", 2),
    ("queued", 2),
)

_AGENT_KEYS = tuple(key for key, _name in DEMO_AGENTS)

# (team name, agent key, weeks before the current week) per console-plan
# meeting, in console-plan order. Week 1 is the most recent closed week. Only
# completed and partial meetings enter the trend population; each closed week
# then has 0 or at least 5 distinct agents, for the workspace and for the
# rest outside ``Call Center``. The others carry no team and no agent, so a
# later replay or a finished job keeps every week passing.
MEETING_ATTRIBUTION: tuple[tuple[str | None, str | None, int], ...] = (
    *((CALL_CENTER, agent, 1) for agent in _AGENT_KEYS),  # 0-4 completed
    *((CALL_CENTER, agent, 2) for agent in _AGENT_KEYS),  # 5-9 completed
    ("Sales", "member", 2),  # 10 completed
    ("Support", "viewer", 2),  # 11 completed
    (None, "agent1", 2),  # 12-14 completed: the rest of week 2 reaches 5 agents
    (None, "agent2", 2),
    (None, "agent3", 2),
    *((CALL_CENTER, agent, 3) for agent in _AGENT_KEYS),  # 15-19 completed
    (CALL_CENTER, "agent1", 1),  # 20-22 partial
    (CALL_CENTER, "agent2", 2),
    (CALL_CENTER, "agent3", 3),
    (None, None, 4),  # 23-24 failed, 25 dead letter
    (None, None, 4),
    (None, None, 4),
    (None, None, 1),  # 26-27 processing, 28-29 queued
    (None, None, 1),
    (None, None, 1),
    (None, None, 1),
)


def _trend_meetings() -> tuple[tuple[str, str, int, CallScript], ...]:
    """(team, agent key, closed week, call script) per scripted customer call,
    in seed order. The five demo agents rotate inside each team-week, so every
    team-week holds at least 5 distinct agents and is released as a rate
    cohort (HOM-82)."""
    slot_of: dict[tuple[str, int], int] = {}
    meetings: list[tuple[str, str, int, CallScript]] = []
    for team, week, key, count in TREND_CALL_BLOCKS:
        call = CALL_SCRIPTS[key]
        for _ in range(count):
            slot = slot_of.get((team, week), 0)
            slot_of[(team, week)] = slot + 1
            meetings.append((team, _AGENT_KEYS[slot % len(_AGENT_KEYS)], week, call))
    return tuple(meetings)


def _attribution(index: int) -> tuple[str | None, str | None, int]:
    """(team, agent key, closed week) of meeting ``index`` of the whole plan."""
    trend = _trend_meetings()
    if index < len(trend):
        team, agent, week, _call = trend[index]
        return team, agent, week
    return MEETING_ATTRIBUTION[index - len(trend)]


def _meeting_specs() -> list[tuple[int, str, Script | CallScript, str]]:
    """Every meeting of the seed, in seed order: the scripted customer calls
    first (HOM-365), then the console plan of HOM-175 at the tail, so the
    console's newest completed meeting is still a scripted mock one."""
    specs: list[tuple[int, str, Script | CallScript, str]] = []
    index = 0
    for _team, _agent, _week, call in _trend_meetings():
        title = f"{call.title} #{index + 1}"
        specs.append((index, "completed", call, title))
        index += 1
    for outcome, count in MEETING_PLAN:
        for _ in range(count):
            script = SCRIPTS[index % len(SCRIPTS)]
            title = f"{TITLES[index % len(TITLES)]} #{index + 1}"
            specs.append((index, outcome, script, title))
            index += 1
    return specs


def _idempotency_key(index: int) -> str:
    return f"demo-seed-meeting-{index + 1:03d}"


# The seed's key format before HOM-365 (two digits, 30 meetings). A workspace
# that still holds one of these was seeded by an older seed and is refused:
# this seed's plan changed, so it can neither adopt nor skip those meetings.
LEGACY_IDEMPOTENCY_KEYS: tuple[str, ...] = tuple(
    f"demo-seed-meeting-{number:02d}" for number in range(1, 31)
)


def _duration_seconds(index: int) -> int:
    if index < len(_trend_meetings()):
        return 10 + (index * 3) % 8
    return 20 + (index * 7) % 40


def _received_at(index: int, week_start: datetime) -> datetime:
    """The ledger ``received_at`` of meeting ``index``: inside its planned closed week
    before ``week_start`` (Saturday 00:00 Asia/Tehran), Saturday to Thursday, 08:00 to
    16:00 Tehran time."""
    _team, _agent, weeks_back = _attribution(index)
    return week_start - timedelta(weeks=weeks_back) + timedelta(days=index % 6, hours=8 + index % 9)


def _silent_wav(seconds: int, *, marker: int | None = None) -> bytes:
    """Generated placeholder audio: silence, except that ``marker`` (the
    meeting index) is written into the first frame. Without the marker every
    recording of the same length would be byte-identical, the ingestion layer
    would link them as media duplicates (case C, policy ``allow``) and every
    linked copy would be excluded from the trend population."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE_HZ)
        frames = bytearray(b"\x00\x00" * (SAMPLE_RATE_HZ * seconds))
        if marker is not None:
            frames[0:2] = (marker % 32_767).to_bytes(2, "little")
        writer.writeframes(bytes(frames))
    return buffer.getvalue()


# --- scripted providers -------------------------------------------------------


def _scripted_bundle(script: Script, *, duration_seconds: int, outcome: str):
    """In-process providers that return ``script`` (no network, no models).

    ``partial`` leaves diarization unavailable (speakers unknown); ``failed``
    makes speech recognition refuse the recording permanently.
    """
    from backend.providers.base import (
        ASRProvider,
        DiarizationProvider,
        IntelligenceProvider,
        ProviderError,
        ProviderIdentity,
        text_digest,
    )
    from backend.providers.factory import ProviderBundle
    from backend.providers.schemas import (
        ActionItem as ProviderActionItem,
    )
    from backend.providers.schemas import (
        ASRRequest,
        ASRResult,
        ASRSegment,
        DiarizationRequest,
        DiarizationResult,
        DiarizationSegment,
        DiarizationStatus,
        IntelligenceInsight,
        IntelligenceRequest,
        IntelligenceResult,
        ProviderIssue,
    )

    step = duration_seconds / len(script.turns)
    times = [(round(i * step, 2), round((i + 1) * step, 2)) for i in range(len(script.turns))]
    script_digest = text_digest("\n".join(turn.text for turn in script.turns))

    class ScriptedASR(ASRProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_asr",
                model=SCRIPTED_PROVIDER_VERSION,
                params={"script_sha256": script_digest, "outcome": outcome},
            )

        async def transcribe(self, request: ASRRequest) -> ASRResult:
            if outcome == "failed":
                raise ProviderError(
                    "asr_input_rejected",
                    "Speech recognition could not read this recording",
                    retryable=False,
                )
            return ASRResult(
                provider="demo_seed_asr",
                model=SCRIPTED_PROVIDER_VERSION,
                language=script.language,
                text=" ".join(turn.text for turn in script.turns),
                segments=[
                    ASRSegment(
                        index=i,
                        text=turn.text,
                        start_seconds=start,
                        end_seconds=end,
                        confidence=0.9,
                    )
                    for i, (turn, (start, end)) in enumerate(zip(script.turns, times, strict=True))
                ],
                duration_seconds=duration_seconds,
                is_mock=True,
            )

    class ScriptedDiarization(DiarizationProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_diarization",
                model=SCRIPTED_PROVIDER_VERSION,
                params={"available": outcome != "partial"},
            )

        async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
            if outcome == "partial":
                return DiarizationResult(
                    provider="demo_seed_diarization",
                    status=DiarizationStatus.UNAVAILABLE,
                    issue=ProviderIssue(
                        code="diarization_unavailable",
                        message="Speaker separation was not available for this recording",
                        retryable=False,
                    ),
                    is_mock=True,
                )
            return DiarizationResult(
                provider="demo_seed_diarization",
                status=DiarizationStatus.AVAILABLE,
                segments=[
                    DiarizationSegment(
                        speaker_label=turn.speaker, start_seconds=start, end_seconds=end
                    )
                    for turn, (start, end) in zip(script.turns, times, strict=True)
                ],
                is_mock=True,
            )

    class ScriptedIntelligence(IntelligenceProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_intelligence",
                model=SCRIPTED_PROVIDER_VERSION,
                params={"script_sha256": script_digest},
            )

        async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
            decision, decision_turn = script.decision
            return IntelligenceResult(
                provider="demo_seed_intelligence",
                model=SCRIPTED_PROVIDER_VERSION,
                summary=script.summary,
                summary_source_turn_indexes=list(range(len(request.turns))),
                action_items=[
                    ProviderActionItem(
                        description=description, owner=owner, source_turn_indexes=[turn]
                    )
                    for description, owner, turn in script.actions
                ],
                insights=[
                    IntelligenceInsight(
                        category="decision",
                        description=decision,
                        confidence=0.85,
                        source_turn_indexes=[decision_turn],
                    )
                ],
                is_mock=True,
            )

    return ProviderBundle(
        asr=ScriptedASR(),
        diarization=ScriptedDiarization(),
        intelligence=ScriptedIntelligence(),
    )


def _call_extraction(call: CallScript) -> dict:
    """The taxonomy extraction of ``call`` (the shape of ``providers/mocks.py``,
    HOM-53): intent and topics stated with cited turns and a confidence above
    the explicit threshold, the outcome stated or abstained
    (``insufficient_evidence``) when the script ends unclearly."""

    def stated(label: str, turn: int) -> dict:
        return {
            "label": label,
            "basis": "explicit",
            "source_turn_indexes": [turn],
            "confidence": 0.9,
        }

    outcome = (
        {"status": "stated", "values": [stated(call.outcome, call.outcome_turn)]}
        if call.outcome is not None
        else {"status": "insufficient_evidence", "values": []}
    )
    return {
        "intent": {"status": "stated", "values": [stated(call.intent, call.intent_turn)]},
        "topic": {
            "status": "stated",
            "values": [stated(label, turn) for label, turn in call.topics],
        },
        "entity": {"status": "not_stated", "values": []},
        "outcome": outcome,
    }


def _call_bundle(call: CallScript, *, duration_seconds: int):
    """In-process providers for one scripted customer call (HOM-365): the same
    script machinery as ``_scripted_bundle``, but nothing is marked ``is_mock``
    and the intelligence returns the call's taxonomy extraction, so the real
    analysis stage judges, stores and counts the call in the trend views."""
    from backend.providers.base import (
        ASRProvider,
        DiarizationProvider,
        IntelligenceProvider,
        ProviderIdentity,
        text_digest,
    )
    from backend.providers.factory import ProviderBundle
    from backend.providers.schemas import (
        ActionItem as ProviderActionItem,
    )
    from backend.providers.schemas import (
        ASRRequest,
        ASRResult,
        ASRSegment,
        DiarizationRequest,
        DiarizationResult,
        DiarizationSegment,
        DiarizationStatus,
        IntelligenceInsight,
        IntelligenceRequest,
        IntelligenceResult,
    )

    step = duration_seconds / len(call.turns)
    times = [(round(i * step, 2), round((i + 1) * step, 2)) for i in range(len(call.turns))]
    script_digest = text_digest("\n".join(turn.text for turn in call.turns))

    class CallASR(ASRProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_call_asr",
                model=CALL_PROVIDER_VERSION,
                params={"script_sha256": script_digest},
            )

        async def transcribe(self, request: ASRRequest) -> ASRResult:
            return ASRResult(
                provider="demo_seed_call_asr",
                model=CALL_PROVIDER_VERSION,
                language=call.language,
                text=" ".join(turn.text for turn in call.turns),
                segments=[
                    ASRSegment(
                        index=i,
                        text=turn.text,
                        start_seconds=start,
                        end_seconds=end,
                        confidence=0.9,
                    )
                    for i, (turn, (start, end)) in enumerate(zip(call.turns, times, strict=True))
                ],
                duration_seconds=duration_seconds,
                is_mock=False,
            )

    class CallDiarization(DiarizationProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_call_diarization",
                model=CALL_PROVIDER_VERSION,
                params={"available": True},
            )

        async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
            return DiarizationResult(
                provider="demo_seed_call_diarization",
                status=DiarizationStatus.AVAILABLE,
                segments=[
                    DiarizationSegment(
                        speaker_label=turn.speaker, start_seconds=start, end_seconds=end
                    )
                    for turn, (start, end) in zip(call.turns, times, strict=True)
                ],
                is_mock=False,
            )

    class CallIntelligence(IntelligenceProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_call_intelligence",
                model=CALL_PROVIDER_VERSION,
                params={"script_sha256": script_digest},
            )

        async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
            decision, decision_turn = call.decision
            description, owner, action_turn = call.action
            return IntelligenceResult(
                provider="demo_seed_call_intelligence",
                model=CALL_PROVIDER_VERSION,
                summary=call.summary,
                summary_source_turn_indexes=list(range(len(request.turns))),
                action_items=[
                    ProviderActionItem(
                        description=description, owner=owner, source_turn_indexes=[action_turn]
                    )
                ],
                insights=[
                    IntelligenceInsight(
                        category="decision",
                        description=decision,
                        confidence=0.85,
                        source_turn_indexes=[decision_turn],
                    )
                ],
                extraction=_call_extraction(call),
                is_mock=False,
            )

    return ProviderBundle(
        asr=CallASR(),
        diarization=CallDiarization(),
        intelligence=CallIntelligence(),
    )


# --- environment and database --------------------------------------------------


def _refuse_unless_allowed(environment: str) -> None:
    if environment not in ALLOWED_ENVIRONMENTS:
        raise DemoSeedRefused(
            f"The demo seed refuses to run in the {environment!r} environment; "
            "it only runs in development or test."
        )


def _ensure_database(settings: Settings) -> None:
    """Reuse the configured engine when it already points at this database
    (the app is running, as in tests); otherwise configure one."""
    from sqlalchemy.engine import make_url

    from backend.database import relational

    engine = relational._engine
    target = make_url(settings.database_url)
    if engine is None or engine.url.render_as_string(hide_password=False) != (
        target.render_as_string(hide_password=False)
    ):
        relational.configure_database(settings)


def _session(*, user_id: str | None = None, workspace: Workspace | None = None) -> Session:
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context, bind_user_context

    db = get_session_factory()()
    if user_id is not None:
        bind_user_context(db, user_id)
    if workspace is not None:
        bind_tenant_context(
            db, workspace_id=workspace.id, organization_id=workspace.organization_id
        )
    return db


# --- steps ---------------------------------------------------------------------


def _accounts() -> dict[str, DemoAccount]:
    return {
        role: DemoAccount(
            role=role,
            email=f"{role}@{DEMO_EMAIL_DOMAIN}",
            password=DEMO_PASSWORD,
            display_name=DEMO_DISPLAY_NAMES[role],
        )
        for role in DEMO_ROLES
    }


def _ensure_users(accounts: Mapping[str, DemoAccount]) -> dict[str, User]:
    """Verified, active password accounts without personal workspaces
    (the pattern of ``ensure_development_demo_account``)."""
    from sqlalchemy import select

    from backend.database.saas_models import AuthIdentity, IdentityProvider, User, utcnow
    from backend.security.tokens import hash_password

    users: dict[str, User] = {}
    with _session() as db:
        for role, account in accounts.items():
            user = db.scalar(select(User).where(User.email == account.email))
            if user is None:
                user = User(
                    email=account.email,
                    display_name=account.display_name,
                    password_hash=hash_password(account.password),
                    locale="fa",
                    email_verified_at=datetime.now(UTC),
                    is_active=True,
                )
                db.add(user)
                db.flush()
                db.add(
                    AuthIdentity(
                        user_id=user.id,
                        provider=IdentityProvider.PASSWORD,
                        subject=account.email,
                        verified_at=utcnow(),
                        profile={"demo_seed": True},
                    )
                )
            users[role] = user
        db.commit()
    return users


def _ensure_agents() -> dict[str, User]:
    """The demo agents: verified and active, but with no password and no sign-in
    identity, so they never log in."""
    from sqlalchemy import select

    from backend.database.saas_models import User

    agents: dict[str, User] = {}
    with _session() as db:
        for key, display_name in DEMO_AGENTS:
            email = f"{key}@{DEMO_EMAIL_DOMAIN}"
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    display_name=display_name,
                    password_hash=None,
                    locale="fa",
                    email_verified_at=datetime.now(UTC),
                    is_active=True,
                )
                db.add(user)
                db.flush()
            agents[key] = user
        db.commit()
    return agents


def _find_workspace(owner: User) -> Workspace | None:
    from backend.services.tenancy_service import list_user_workspaces

    with _session(user_id=owner.id) as db:
        for workspace, membership in list_user_workspaces(db, owner.id):
            if (
                workspace.organization.slug == DEMO_ORGANIZATION_SLUG
                and membership.role.value == "owner"
            ):
                return workspace
    return None


def _refuse_outdated_workspace(owner: User) -> None:
    """Refuse a demo workspace an older seed wrote, before anything is added
    to it. Two causes, one message each:

    - before HOM-326: a demo meeting has no ingestion ledger row, so the trend
      views would not count it;
    - before HOM-365: the demo meetings carry the old 2-digit idempotency
      keys. This seed's plan changed, so it can neither adopt those meetings
      nor skip them (they would stay out of every trend view).
    """
    from backend.services.ingestion_service import ingestion_for_meeting
    from backend.services.meeting_service import find_job_by_idempotency

    workspace = _find_workspace(owner)
    if workspace is None:
        return
    without_ledger = 0
    legacy = 0
    with _session(user_id=owner.id, workspace=workspace) as db:

        def has_ledger(job) -> bool:
            return (
                ingestion_for_meeting(db, workspace_id=workspace.id, meeting_id=job.meeting_id)
                is not None
            )

        for index, _outcome, _script, _title in _meeting_specs():
            job = find_job_by_idempotency(
                db, workspace_id=workspace.id, idempotency_key=_idempotency_key(index)
            )
            if job is not None and not has_ledger(job):
                without_ledger += 1
        for key in LEGACY_IDEMPOTENCY_KEYS:
            job = find_job_by_idempotency(db, workspace_id=workspace.id, idempotency_key=key)
            if job is None:
                continue
            if has_ledger(job):
                legacy += 1
            else:
                without_ledger += 1
    if without_ledger:
        raise DemoSeedRefused(
            f"This database was seeded by an older demo seed: {without_ledger} demo "
            "meetings have no ingestion ledger row, so the trend views would not count "
            "them. The seed does not repair it. Reset the database (drop it, apply the "
            "migrations), then run the demo seed again."
        )
    if legacy:
        raise DemoSeedRefused(
            f"This database was seeded by an older demo seed: {legacy} demo meetings "
            "carry the old seed's idempotency keys, and this seed's meeting plan "
            "changed, so it can neither adopt them nor skip them (they would stay out "
            "of every trend view). Reset the database (drop it, apply the migrations), "
            "then run the demo seed again."
        )


def _demo_retention_days(settings: Settings) -> int:
    """A retention period that differs from the default the workspace starts with."""
    if settings.retention_days_default == DEMO_RETENTION_DAYS:
        return 365
    return DEMO_RETENTION_DAYS


def _ensure_workspace(owner: User, settings: Settings) -> Workspace:
    """The demo organization and workspace on the paid plan (the pattern of
    ``create_personal_workspace``, with a fixed slug and no free trial)."""
    from backend.database.saas_models import (
        Entitlement,
        Membership,
        Organization,
        Plan,
        PlanVersion,
        Subscription,
        SubscriptionStatus,
        Workspace,
        WorkspaceRole,
        new_id,
    )
    from backend.database.tenant_context import bind_tenant_context
    from backend.services.admin_service import update_workspace_settings
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event
    from backend.services.organization_service import bootstrap_owner_membership
    from backend.services.tenancy_service import ensure_plan_catalog

    existing = _find_workspace(owner)
    if existing is not None:
        return existing

    now = datetime.now(UTC)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_end = (period_start + timedelta(days=32)).replace(day=1)
    with _session(user_id=owner.id) as db:
        ensure_plan_catalog(db, settings)
        organization = Organization(
            id=new_id(), name=DEMO_WORKSPACE_NAME, slug=DEMO_ORGANIZATION_SLUG, status="active"
        )
        workspace = Workspace(
            id=new_id(),
            organization=organization,
            name=DEMO_WORKSPACE_NAME,
            slug=DEMO_WORKSPACE_SLUG,
            status="active",
            retention_days=settings.retention_days_default,
        )
        # Ids are known before the first write, so row-level security WITH
        # CHECK sees the new tenant.
        bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
        db.add(organization)
        db.flush()
        db.add(Membership(workspace=workspace, user=owner, role=WorkspaceRole.OWNER))
        db.flush()
        bootstrap_owner_membership(db, organization=organization, user=owner)
        # Pinned to the plan's current published version, as a grant is
        # (``tenancy_service._grant_demo``); no plan version is written here.
        plan = db.get(Plan, DEMO_PLAN_KEY)
        version = db.get(PlanVersion, (plan.key, plan.current_version))
        if version is None:
            raise RuntimeError(f"The {DEMO_PLAN_KEY!r} plan has no current published version")
        db.add(
            Subscription(
                workspace_id=workspace.id,
                plan_key=plan.key,
                plan_version=version.version,
                status=SubscriptionStatus.ACTIVE,
                provider="internal",
                starts_at=period_start,
            )
        )
        terms = version.entitlements_json if isinstance(version.entitlements_json, dict) else {}
        for metric, demo_limit in (
            ("audio_seconds", AUDIO_SECONDS_LIMIT),
            ("concurrent_jobs", CONCURRENT_JOBS_LIMIT),
        ):
            # The version's own term when it defines one; the catalog's paid
            # plans define none, so the demo allowance fills the gap.
            limit = terms.get(metric, demo_limit)
            db.add(
                Entitlement(
                    workspace_id=workspace.id,
                    metric=metric,
                    source=DEMO_PLAN_KEY,
                    limit_value=limit,
                    used_value=0,
                    reserved_value=0,
                    period_start=period_start,
                    period_end=period_end,
                )
            )
        db.flush()
        _, updated_fields = update_workspace_settings(
            db,
            workspace_id=workspace.id,
            # A real, neutral change (a longer retention than the default),
            # recorded once with the workspace it belongs to.
            changes={"retention_days": _demo_retention_days(settings)},
        )
        record_audit_event(
            db,
            event_type=AuditEventType.WORKSPACE_SETTINGS_UPDATED,
            actor_user_id=owner.id,
            workspace_id=workspace.id,
            target_type="workspace",
            target_id=workspace.id,
            details={"fields": ",".join(updated_fields)},
            settings=settings,
        )
        db.commit()
    found = _find_workspace(owner)
    assert found is not None
    return found


def _ensure_members(
    workspace: Workspace,
    users: Mapping[str, User],
    agents: Mapping[str, User],
    settings: Settings,
) -> None:
    """Admin, member, viewer and the agents (as members) join through the real
    invitation flow.

    The invitation is created and accepted in one transaction: a run that
    stops in between leaves no pending invitation (whose raw token would be
    lost) behind, so the next run starts from the same state as a clean one.
    """
    from sqlalchemy import select

    from backend.database.saas_models import Membership, WorkspaceRole
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event
    from backend.services.member_service import accept_invitation, create_invitation

    owner = users["owner"]
    invitees = [(users[role], role) for role in ("admin", "member", "viewer")]
    invitees += [(agent, "member") for agent in agents.values()]
    for user, role in invitees:
        with _session(user_id=owner.id, workspace=workspace) as db:
            joined = db.scalar(
                select(Membership.id).where(
                    Membership.workspace_id == workspace.id, Membership.user_id == user.id
                )
            )
            if joined is not None:
                continue
            issued = create_invitation(
                db,
                workspace_id=workspace.id,
                invited_by_user_id=owner.id,
                actor_role=WorkspaceRole.OWNER,
                email=user.email,
                role=WorkspaceRole(role),
                expires_in_days=7,
                settings=settings,
            )
            record_audit_event(
                db,
                event_type=AuditEventType.MEMBER_INVITATION_CREATED,
                actor_user_id=owner.id,
                workspace_id=workspace.id,
                target_type="workspace_invitation",
                target_id=issued.invitation.id,
                details={"role": issued.invitation.role.value},
                settings=settings,
            )
            # From here the session acts as the invitee, as the accept route does.
            accepted = accept_invitation(
                db, raw_token=issued.raw_token, user=db.merge(user), settings=settings
            )
            record_audit_event(
                db,
                event_type=AuditEventType.MEMBER_INVITATION_ACCEPTED,
                actor_user_id=user.id,
                workspace_id=accepted.workspace.id,
                target_type="workspace_invitation",
                target_id=accepted.invitation.id,
                details={"role": accepted.membership.role.value},
                settings=settings,
            )
            db.commit()


TEAMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # team name, supervisor, agents (a login role or an agent key). The first
    # three are the console plan (HOM-175/HOM-326); ``Customer Care`` and its
    # meetings are the call plan of HOM-365. The two call teams share the five
    # demo agents, so both are released rate cohorts in the trend views.
    ("Sales", "admin", ("member",)),
    ("Support", "owner", ("viewer",)),
    (CALL_CENTER, "admin", _AGENT_KEYS),
    (CUSTOMER_CARE, "admin", _AGENT_KEYS),
)


def _ensure_teams(workspace: Workspace, users: Mapping[str, User], settings: Settings) -> None:
    from sqlalchemy import select

    from backend.database.saas_models import Team, TeamAssignment, TeamRole
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event
    from backend.services.team_service import create_assignment, create_team, slugify

    owner = users["owner"]
    with _session(user_id=owner.id, workspace=workspace) as db:
        for name, supervisor, agent_keys in TEAMS:
            team = db.scalar(
                select(Team).where(Team.workspace_id == workspace.id, Team.slug == slugify(name))
            )
            if team is None:
                team = create_team(db, workspace_id=workspace.id, name=name)
                record_audit_event(
                    db,
                    event_type=AuditEventType.TEAM_CREATED,
                    actor_user_id=owner.id,
                    workspace_id=workspace.id,
                    target_type="team",
                    target_id=team.id,
                    settings=settings,
                )
            for user_key, team_role in (
                (supervisor, TeamRole.SUPERVISOR),
                *((agent, TeamRole.AGENT) for agent in agent_keys),
            ):
                user = users[user_key]
                assigned = db.scalar(
                    select(TeamAssignment.id).where(
                        TeamAssignment.team_id == team.id,
                        TeamAssignment.user_id == user.id,
                        TeamAssignment.role == team_role,
                    )
                )
                if assigned is not None:
                    continue
                record = create_assignment(
                    db,
                    workspace_id=workspace.id,
                    team_id=team.id,
                    user_id=user.id,
                    role=team_role,
                    assigned_by_user_id=owner.id,
                    valid_from=None,
                    valid_until=None,
                )
                record_audit_event(
                    db,
                    event_type=AuditEventType.TEAM_ASSIGNMENT_CREATED,
                    actor_user_id=owner.id,
                    workspace_id=workspace.id,
                    target_type="team_assignment",
                    target_id=record.assignment.id,
                    details={
                        "team_id": team.id,
                        "user_id": user.id,
                        "role": record.assignment.role.value,
                    },
                    settings=settings,
                )
        db.commit()


def _ensure_api_key(workspace: Workspace, owner: User, settings: Settings) -> None:
    from sqlalchemy import select

    from backend.database.saas_models import ApiKey, Membership
    from backend.services.api_key_service import create_api_key
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event

    with _session(user_id=owner.id, workspace=workspace) as db:
        exists = db.scalar(
            select(ApiKey.id).where(
                ApiKey.workspace_id == workspace.id, ApiKey.name == API_KEY_NAME
            )
        )
        if exists is not None:
            return
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace.id, Membership.user_id == owner.id
            )
        )
        # The raw key is never printed or stored: the demo shows the key list.
        key, _raw_key = create_api_key(
            db,
            workspace=db.merge(workspace),
            membership=membership,
            name=API_KEY_NAME,
            scopes=API_KEY_SCOPES,
            expires_in_days=min(90, settings.api_key_max_ttl_days),
            settings=settings,
        )
        record_audit_event(
            db,
            event_type=AuditEventType.API_KEY_CREATED,
            actor_user_id=owner.id,
            workspace_id=workspace.id,
            target_type="api_key",
            target_id=key.id,
            details={
                "name": key.name,
                "credential_prefix": key.key_prefix,
                "scopes": ",".join(key.scopes),
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            },
            settings=settings,
        )
        db.commit()


def _upload(
    workspace: Workspace,
    owner: User,
    *,
    index: int,
    title: str,
    script: Script | CallScript,
    idempotency_key: str,
    team_id: str | None,
    agent_user_id: str | None,
    received_at: datetime,
    settings: Settings,
) -> str | None:
    """Store the placeholder recording, then accept it into the ingestion ledger,
    which creates the meeting (with its team and agent) and its job, as the
    upload route does.

    Returns the id of a job that has not had its first attempt yet: the new
    one, or one an earlier run created but stopped before driving it to its
    planned outcome. Returns ``None`` when the meeting was seeded completely.
    """
    from backend.database.saas_models import IngestionRecord, IngestionSourceType, JobState
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event
    from backend.services.ingestion_service import (
        PROVIDER_CONSOLE,
        IngestionDecision,
        IngestionIdentity,
        IngestionPayload,
        accept_ingestion,
    )
    from backend.services.meeting_service import create_upload_job, find_job_by_idempotency
    from backend.storage.factory import create_object_storage

    with _session(user_id=owner.id, workspace=workspace) as db:
        existing = find_job_by_idempotency(
            db, workspace_id=workspace.id, idempotency_key=idempotency_key
        )
        if existing is not None:
            untouched = existing.state == JobState.QUEUED and existing.attempt_count == 0
            return existing.id if untouched else None
        duration = _duration_seconds(index)
        audio = _silent_wav(duration, marker=index)
        filename = f"demo-meeting-{index + 1:03d}.wav"
        stored = create_object_storage(settings).put_stream(
            workspace.id,
            io.BytesIO(audio),
            content_type="audio/wav",
            original_filename=filename,
            expected_sha256=hashlib.sha256(audio).hexdigest(),
            max_bytes=len(audio),
        )
        # The seed's idempotency key is the event id of a console upload.
        identity = IngestionIdentity(
            source_type=IngestionSourceType.FILE_UPLOAD,
            source_provider=PROVIDER_CONSOLE,
            external_event_id=idempotency_key,
        )
        payload = IngestionPayload(
            media_sha256=stored.sha256,
            media_size_bytes=stored.size_bytes,
            original_filename=filename,
            title=title,
            language=script.language,
            declared_content_type="audio/wav",
        )

        def _create(record: IngestionRecord):
            created = create_upload_job(
                db,
                workspace_id=workspace.id,
                user_id=owner.id,
                title=title,
                language=script.language,
                storage_key=stored.key,
                original_filename=filename,
                media_type="audio/wav",
                container_format="wav",
                size_bytes=stored.size_bytes,
                checksum_sha256=stored.sha256,
                duration_seconds=duration,
                channel_count=1,
                sample_rate_hz=SAMPLE_RATE_HZ,
                idempotency_key=idempotency_key,
                retention_days=workspace.retention_days,
                settings=settings,
                ingestion_id=record.id,
                producer="demo_seed",
                source_type=identity.source_type.value,
            )
            # Attributed as it is created, so no later update is needed.
            created.meeting.team_id = team_id
            created.meeting.agent_user_id = agent_user_id
            return created

        outcome = accept_ingestion(
            db,
            workspace=db.merge(workspace),
            identity=identity,
            payload=payload,
            received_at=received_at,
            now=datetime.now(UTC),
            actor_user_id=owner.id,
            api_key_id=None,
            request_id=None,
            settings=settings,
            create=_create,
        )
        if outcome.decision is not IngestionDecision.ACCEPTED:
            raise RuntimeError(f"Demo meeting {title!r} was not accepted: {outcome.decision.value}")
        created = outcome.created
        record_audit_event(
            db,
            event_type=AuditEventType.MEETING_UPLOADED,
            actor_user_id=owner.id,
            workspace_id=workspace.id,
            target_type="meeting",
            target_id=created.meeting.id,
            details={
                "size_bytes": stored.size_bytes,
                "duration_seconds": created.meeting.duration_seconds,
                "container": "wav",
                "ingestion_id": outcome.record.id,
            },
            settings=settings,
        )
        db.commit()
        return created.job.id


def _dead_letter(workspace: Workspace, job_id: str, settings: Settings) -> None:
    """A dead-lettered job, so the console offers a replay. It is parked
    directly (the processor only dead-letters after its retry budget), and
    the audit says so."""
    from backend.database.saas_models import JobState, Meeting, ProcessingJob
    from backend.jobs.failures import FailureClass, park_job
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event

    with _session(workspace=workspace) as db:
        job = db.get(ProcessingJob, job_id, with_for_update=True)
        meeting = db.get(Meeting, job.meeting_id)
        park_job(
            db,
            job,
            meeting,
            state=JobState.DEAD_LETTER,
            code="asr_unavailable",
            message="Speech recognition was unavailable; parked for replay (demo data)",
            failure_class=FailureClass.TRANSIENT,
            now=datetime.now(UTC),
        )
        record_audit_event(
            db,
            event_type=AuditEventType.MEETING_PROCESSING_FAILED,
            workspace_id=workspace.id,
            target_type="meeting",
            target_id=job.meeting_id,
            outcome="failure",
            details={
                "cause": "asr_unavailable",
                "failure_class": FailureClass.TRANSIENT.value,
                "retryable": True,
                "attempt": job.attempt_count,
                "terminal_reason": "demo_seed",
            },
            settings=settings,
        )
        db.commit()


def _start_processing(workspace: Workspace, job_id: str, settings: Settings) -> None:
    """A job a worker is running right now: the lease and status the
    processor's claim writes. A running worker may pick it up again after
    the lease expires."""
    from backend.database.saas_models import Meeting, MeetingStatus, ProcessingJob
    from backend.jobs.leases import LeasePolicy, database_now, grant_lease

    with _session(workspace=workspace) as db:
        job = db.get(ProcessingJob, job_id, with_for_update=True)
        grant_lease(
            job,
            worker_id=WORKER_ID,
            policy=LeasePolicy.from_settings(settings),
            now=database_now(db),
        )
        meeting = db.get(Meeting, job.meeting_id)
        meeting.status = MeetingStatus.PROCESSING
        db.commit()


def _team_ids(workspace: Workspace, owner: User) -> dict[str, str]:
    from sqlalchemy import select

    from backend.database.saas_models import Team

    with _session(user_id=owner.id, workspace=workspace) as db:
        rows = db.execute(select(Team.name, Team.id).where(Team.workspace_id == workspace.id))
        return {name: team_id for name, team_id in rows}


def _ensure_meetings(
    workspace: Workspace,
    people: Mapping[str, User],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    from backend.jobs.processor import process_job
    from backend.services.role_trend_service import current_week_start

    owner = people["owner"]
    teams = _team_ids(workspace, owner)
    # Read once, so one run dates every row against the same week. ``now``
    # only picks this anchor (HOM-365); every other clock stays real.
    week_start = current_week_start(now if now is not None else datetime.now(UTC))
    for index, outcome, script, title in _meeting_specs():
        team, agent, _weeks_back = _attribution(index)
        job_id = _upload(
            workspace,
            owner,
            index=index,
            title=title,
            script=script,
            idempotency_key=_idempotency_key(index),
            team_id=teams[team] if team is not None else None,
            agent_user_id=people[agent].id if agent is not None else None,
            received_at=_received_at(index, week_start),
            settings=settings,
        )
        # A job found still queued is driven on now: an earlier run stopped
        # before it reached its planned outcome.
        if job_id is None or outcome == "queued":
            continue
        if outcome == "dead_letter":
            _dead_letter(workspace, job_id, settings)
        elif outcome == "processing":
            _start_processing(workspace, job_id, settings)
        else:
            bundle = (
                _call_bundle(script, duration_seconds=_duration_seconds(index))
                if isinstance(script, CallScript)
                else _scripted_bundle(
                    script, duration_seconds=_duration_seconds(index), outcome=outcome
                )
            )
            result = process_job(
                job_id,
                worker_id=WORKER_ID,
                settings=settings,
                providers=bundle,
            )
            if result["status"] != outcome:
                raise RuntimeError(
                    f"Demo meeting {title!r} ended as {result['status']!r}, expected {outcome!r}"
                )


def _ensure_shares(workspace: Workspace, users: Mapping[str, User], settings: Settings) -> None:
    from sqlalchemy import select

    from backend.database.saas_models import Meeting, MeetingStatus, ShareAudience, ShareLink
    from backend.security.tokens import random_token
    from backend.services.audit_event_types import AuditEventType
    from backend.services.audit_service import record_audit_event
    from backend.services.share_service import ShareSpec, issue_share

    owner = users["owner"]
    specs = (
        ShareSpec(
            audience=ShareAudience.WORKSPACE,
            recipients=(),
            allow_download=False,
            allow_export=True,
            expires_in_hours=None,
        ),
        ShareSpec(
            audience=ShareAudience.RECIPIENTS,
            recipients=(f"partner@{DEMO_EMAIL_DOMAIN}",),
            allow_download=False,
            allow_export=False,
            expires_in_hours=24 * 30,
        ),
    )
    with _session(user_id=owner.id, workspace=workspace) as db:
        meetings: Sequence[Meeting] = list(
            db.scalars(
                select(Meeting)
                .where(
                    Meeting.workspace_id == workspace.id,
                    Meeting.status == MeetingStatus.COMPLETED,
                )
                .order_by(Meeting.created_at.asc(), Meeting.id.asc())
                .limit(len(specs))
            )
        )
        current = db.merge(workspace)
        for meeting, spec in zip(meetings, specs, strict=False):
            shared = db.scalar(
                select(ShareLink.id).where(
                    ShareLink.meeting_id == meeting.id, ShareLink.audience == spec.audience
                )
            )
            if shared is not None:
                continue
            # The raw token is not kept: the demo shows the share list only.
            share = issue_share(
                db,
                workspace=current,
                meeting=meeting,
                created_by_user_id=owner.id,
                spec=spec,
                raw_token=random_token(32),
                settings=settings,
            )
            record_audit_event(
                db,
                event_type=AuditEventType.MEETING_SHARE_CREATED,
                actor_user_id=owner.id,
                workspace_id=workspace.id,
                target_type="share_link",
                target_id=share.id,
                details={
                    "meeting_id": meeting.id,
                    "audience": share.audience.value,
                    "allow_download": share.allow_download,
                    "allow_export": share.allow_export,
                    "recipient_count": spec.recipient_count,
                    "has_expiration": share.expires_at is not None,
                },
                settings=settings,
            )
        db.commit()


def _meeting_count(workspace: Workspace) -> int:
    from sqlalchemy import func, select

    from backend.database.saas_models import Meeting

    with _session(workspace=workspace) as db:
        return db.scalar(
            select(func.count()).select_from(Meeting).where(Meeting.workspace_id == workspace.id)
        )


# --- public interface ----------------------------------------------------------


def seed_demo_workspace(settings: Settings, *, now: datetime | None = None) -> DemoSeedResult:
    """Create (or find) the demo workspace and return its logins.

    ``now`` (HOM-365) is a timezone-aware datetime used only to choose the
    anchor week every ledger row is dated from; ``None`` reads the real clock
    once per run, as the CLI does. A naive ``now`` is refused before any
    engine, connection or database write.
    """
    # Before any engine, connection or import of the database layer.
    _refuse_unless_allowed(settings.environment)
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError(
            "now must be a timezone-aware datetime: it only chooses the anchor week "
            "the demo ledger rows are dated from"
        )
    _ensure_database(settings)

    accounts = _accounts()
    users = _ensure_users(accounts)
    # Before the agents and any write to the workspace.
    _refuse_outdated_workspace(users["owner"])
    agents = _ensure_agents()
    people = {**users, **agents}
    workspace = _ensure_workspace(users["owner"], settings)
    _ensure_members(workspace, users, agents, settings)
    _ensure_teams(workspace, people, settings)
    _ensure_api_key(workspace, users["owner"], settings)
    _ensure_meetings(workspace, people, settings, now=now)
    _ensure_shares(workspace, users, settings)
    return DemoSeedResult(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        workspace_name=workspace.name,
        accounts=accounts,
        meeting_count=_meeting_count(workspace),
        console_url=settings.frontend_url or None,
    )


def _print_result(result: DemoSeedResult) -> None:
    print(f"Demo workspace: {result.workspace_name} ({result.workspace_id})")
    print(f"Meetings: {result.meeting_count}")
    if result.console_url:
        print(f"Console: {result.console_url}")
    print("Logins (all roles share one password):")
    for role, account in result.accounts.items():
        print(f"  {role:<7} {account.email}  {account.password}")


def _raw_environments() -> list[str]:
    """Every spelling of the environment variable (settings read it in any
    case), normalized; the default when none is set."""
    values = [value for name, value in os.environ.items() if name.casefold() == "environment"]
    return [value.strip().lower() for value in values] or ["development"]


def main(argv: list[str] | None = None) -> int:
    # Decide from the raw variables first: a production environment is
    # refused even when its settings would not validate here.
    try:
        for environment in _raw_environments():
            _refuse_unless_allowed(environment)
        from pydantic import ValidationError

        from backend.config import Settings

        try:
            settings = Settings()
        except ValidationError as exc:
            problems = "; ".join(error["msg"] for error in exc.errors())
            print(f"The demo seed cannot read its settings: {problems}", file=sys.stderr)
            return 1
        # Settings may also come from the .env file; check what they resolved to.
        result = seed_demo_workspace(settings)
    except DemoSeedRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
