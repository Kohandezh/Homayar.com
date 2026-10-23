from __future__ import annotations

import base64
import os
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ActionItem,
    Meeting,
    MeetingSummary,
    TranscriptRedaction,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.processor import process_job
from backend.privacy.text_pii import PiiClass, detect, redact
from backend.privacy.transcript_view import MeetingView, RedactedText, choose_view
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRResult, ASRSegment
from backend.services.transcript_service import current_revision
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_text_pii import NID, NID2, card, iban

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
NID2_FA = NID2.translate(FA)

SEGMENTS = [
    f"سلام، من علی هستم. کد ملی {NID2_FA} و موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹ است.",
    "ایمیل ali.rezaei@example.com و تلفن دفتر ۰۲۱ ۸۸۷۷ ۶۶۵۵ را بنویسید.",
]
REDACTED_SEGMENTS = [
    "سلام، من علی هستم. کد ملی [IR_NATIONAL_ID_1] و موبایل [PHONE_1] است.",
    "ایمیل [EMAIL_1] و تلفن دفتر [PHONE_2] را بنویسید.",
]
NEUTRAL_SEGMENTS = [
    "سلام، جلسه درباره قرارداد سالانه بود.",
    "قرار شد گزارش هفته بعد آماده شود.",
]
OVERLAPPING_SEGMENTS = [
    f"کد ملی {NID.translate(FA)} و موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹ است.",
    "دوباره ۰۹۱۲۳۴۵۶۷۸۹ را تکرار می‌کنم.",
]
ACCOUNT_CARD = card("603769482109753")
ACCOUNT_IBAN = iban("0570028180010653892001")
ACCOUNT_POSTAL = "1965834712"
ACCOUNT_SEGMENTS = [
    "شماره کارت ۶۰۳۷ ۶۹۴۸ ۲۱۰۹ ۷۵۳۶ را برای واریز بنویسید.",
    "شبا IR71 0570 0281 8001 0653 8920 01 و کد پستی ۱۹۶۵۸۳۴۷۱۲ است.",
]

FRAGMENT_MARKERS = ("0912", "۰۹۱۲", "ali.rezaei", "example.com", "mailbox.ir", "۸۸۷۷", "۲۷۴۶")


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


class _ASR(MockASRProvider):
    def __init__(self, segments: list[str]) -> None:
        super().__init__(allow_mock=True, transcript=" ".join(segments), language="fa")
        self._segments = segments

    async def transcribe(self, request):
        return ASRResult(
            provider="mock_asr",
            model="deterministic-v1",
            language="fa",
            text=" ".join(self._segments),
            segments=[
                ASRSegment(index=i, text=text, start_seconds=i * 5, end_seconds=i * 5 + 4)
                for i, text in enumerate(self._segments)
            ],
            duration_seconds=10,
            is_mock=True,
        )


class _CapturingIntelligence(MockIntelligenceProvider):
    def __init__(self) -> None:
        super().__init__(allow_mock=True)
        self.requests = []

    async def analyze(self, request):
        self.requests.append(request)
        return await super().analyze(request)


def _process(client, tenant, settings, label, segments):
    accepted = _upload(client, tenant, key=f"{label}-0001")
    assert accepted.status_code == 202, accepted.text
    intelligence = _CapturingIntelligence()
    providers = ProviderBundle(
        asr=_ASR(segments),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=intelligence,
    )
    result = process_job(
        accepted.json()["job"]["id"], worker_id=label, settings=settings, providers=providers
    )
    assert result["status"] == "completed", result
    return accepted.json()["meeting"]["id"], intelligence


def _set_policy(client, tenant, **payload):
    response = client.patch(
        "/api/v1/admin/workspace-settings",
        json=payload,
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert response.status_code == 200, response.text


def _get(client, tenant, path, **params):
    response = client.get(path, params=params, headers=_headers(tenant), cookies=tenant["cookies"])
    assert response.status_code == 200, (path, response.text)
    return response


def _seed_summary(meeting_id, *, overview, topics=(), key_points=(), decisions=()):
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        summary.overview = overview
        summary.topics = list(topics)
        summary.key_points = list(key_points)
        summary.decisions = list(decisions)
        db.commit()


def _add_action(client, tenant, meeting_id, description, assignee_text=None):
    payload = {"description": description, "priority": "high"}
    if assignee_text is not None:
        payload["assignee_text"] = assignee_text
    created = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        json=payload,
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _stored_action(action_id):
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        return action.description, action.assignee_text


def _render(settings, tenant, meeting_id, values, *, view="redacted"):
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        workspace = db.get(Workspace, tenant["workspace_id"])
        meeting = db.get(Meeting, meeting_id)
        meeting_view = MeetingView(
            db,
            meeting=meeting,
            policy=choose_view(workspace, requested=view, may_read_original=True),
            settings=settings,
            now=datetime.now(UTC),
            persist=False,
        )
        return [meeting_view.text(value) for value in values]


def _leak(label: str) -> str:
    return (
        f"{label}: با ۰۹۱۲ ۳۴۵ یا 0912-345 یا ali.rezaei، سایت example.com، "
        "اتاق ۳۴۵، سال ۱۴۰۳، پسوند com، نام ali"
    )


def _masked(label: str) -> str:
    return (
        f"{label}: با [PHONE_1] یا [PHONE_1] یا [EMAIL_1]، سایت [EMAIL_1]، "
        "اتاق ۳۴۵، سال ۱۴۰۳، پسوند com، نام ali"
    )


LEAK_OVERVIEW = _leak("overview") + " و همکار mailbox.ir و تلفن ۰۹۱۲۳۴۵۶۷۸۹"
MASKED_OVERVIEW = _masked("overview") + " و همکار [EMAIL_2] و تلفن [PHONE_1]"
LEAK_TOPICS = [_leak("topic"), "دفتر ۸۸۷۷ ۶۶۵۵", "گزارش ۱۴۰۳"]
MASKED_TOPICS = [_masked("topic"), "دفتر [PHONE_2]", "گزارش ۱۴۰۳"]
LEAK_KEY_POINTS = [
    {"category": _leak("point"), "description": _leak("detail"), "confidence": 0.8},
    {"category": "risk", "description": "کد ملی ۲۷۴۶۵۹ و شماره ۶۷۸۹", "confidence": 0.7},
]
MASKED_KEY_POINTS = [
    (_masked("point"), _masked("detail")),
    ("risk", "کد ملی [IR_NATIONAL_ID_1] و شماره [PHONE_1]"),
]
LEAK_DECISIONS = [
    {
        "section": "decision",
        "category": _leak("decision"),
        "description": _leak("outcome"),
        "owner": {"status": "stated", "value": _leak("owner")},
        "due_date": {"status": "not_stated", "value": None},
        "basis": "explicit",
        "evidence_status": "insufficient",
    }
]


def _leaky_meeting(client, email_outbox, monkeypatch, settings, label):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    meeting_id, _ = _process(client, tenant, settings, label, SEGMENTS)
    _seed_summary(
        meeting_id,
        overview=LEAK_OVERVIEW,
        topics=LEAK_TOPICS,
        key_points=LEAK_KEY_POINTS,
        decisions=LEAK_DECISIONS,
    )
    actions = [
        _add_action(client, tenant, meeting_id, _leak("task"), _leak("assignee")),
        _add_action(client, tenant, meeting_id, "ارسال برای sara@mailbox.ir"),
        _add_action(client, tenant, meeting_id, "شماره جدید ۰۹۳۵ ۱۱۱ ۲۲۳۳ و دامنه mailbox"),
    ]
    _set_policy(client, tenant, pii_redaction_enabled=True)
    return tenant, meeting_id, actions


EXPECTED_ACTIONS = [
    (_masked("task"), _masked("assignee")),
    ("ارسال برای [EMAIL_2]", None),
    ("شماره جدید [PHONE_3] و دامنه [EMAIL_2]", None),
]


def _assert_masked_summary(body):
    assert body["overview"] == MASKED_OVERVIEW
    assert body["topics"] == MASKED_TOPICS
    assert [(p["category"], p["description"]) for p in body["key_points"]] == MASKED_KEY_POINTS
    (decision,) = body["decisions"]
    assert decision["category"] == _masked("decision")
    assert decision["description"] == _masked("outcome")
    assert decision["owner"] == {"status": "stated", "value": _masked("owner")}


def _visible(text: str) -> list[str]:
    return [marker for marker in FRAGMENT_MARKERS if marker in text]


@pytest.fixture
def known_meeting(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom284-known")
    meeting_id, _ = _process(client, tenant, settings, "hom284-known", SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    _add_action(client, tenant, meeting_id, "پیگیری با sara@mailbox.ir")
    return tenant, meeting_id


DENY_CASES = [
    ("persian-phone-fragment", "با ۰۹۱۲ ۳۴۵ تماس بگیرید", "با [PHONE_1] تماس بگیرید"),
    ("latin-hyphen-fragment", "شماره 0912-345 است", "شماره [PHONE_1] است"),
    ("slash-ends-run", "شماره 0912/345 است", "شماره [PHONE_1]/345 است"),
    ("dot-ends-run", "شماره 0912.345 است", "شماره [PHONE_1].345 است"),
    ("two-spaces-end-run", "شماره 0912  345 است", "شماره [PHONE_1]  345 است"),
    ("nbsp-separator", "شماره 0912 345 است", "شماره [PHONE_1] است"),
    ("en-dash-separator", "شماره 0912–345 است", "شماره [PHONE_1] است"),
    ("prefix-0098", "شماره 0098 912 345 است", "شماره [PHONE_1] است"),
    ("arabic-indic-digits", "شماره ٠٩١٢ ٣٤٥ است", "شماره [PHONE_1] است"),
    ("fullwidth-digits", "شماره ０９１２ ３４５ است", "شماره [PHONE_1] است"),
    ("mixed-scripts-one-run", "شماره ۰۹۱2 345 است", "شماره [PHONE_1] است"),
    ("glued-to-persian-letters", "شماره۰۹۱۲۳۴۵ را بگیر", "شماره[PHONE_1] را بگیر"),
    ("middle-four-digits", "کد ۳۴۵۶ را بزن", "کد [PHONE_1] را بزن"),
    ("last-four-digits", "شماره‌ای که به ۶۷۸۹ ختم می‌شود", "شماره‌ای که به [PHONE_1] ختم می‌شود"),
    ("whole-run-replaced", "کد 77-0912 است", "کد [PHONE_1] است"),
    ("second-phone-fragment", "دفتر ۸۸۷۷ ۶۶۵۵ است", "دفتر [PHONE_2] است"),
    ("national-id-fragment", "کد ملی ۲۷۴۶۵۹ ثبت شد", "کد ملی [IR_NATIONAL_ID_1] ثبت شد"),
    ("email-domain", "سایت example.com است", "سایت [EMAIL_1] است"),
    ("email-domain-upper", "سایت EXAMPLE.COM است", "سایت [EMAIL_1] است"),
    ("email-domain-sentence-dot", "سایت example.com.", "سایت [EMAIL_1]."),
    ("email-local-part", "نشانی ali.rezaei ثبت شد", "نشانی [EMAIL_1] ثبت شد"),
    ("email-local-part-mixed-case", "نشانی Ali.Rezaei ثبت شد", "نشانی [EMAIL_1] ثبت شد"),
    ("email-without-tld", "نشانی ali.rezaei@example ثبت شد", "نشانی [EMAIL_1] ثبت شد"),
    ("email-domain-label", "شرکت example است", "شرکت [EMAIL_1] است"),
    ("free-text-email-domain", "همکار mailbox.ir است", "همکار [EMAIL_2] است"),
    ("substring-without-dot", "نام rezaei است", "نام [EMAIL_1] است"),
    ("local-part-label-upper", "نام REZAEI است", "نام [EMAIL_1] است"),
]

ALLOW_CASES = [
    ("three-digits-inside-value", "اتاق ۳۴۵ آزاد است"),
    ("two-latin-digits", "ساعت 12 جلسه است"),
    ("three-digits-912", "کد 912 است"),
    ("year-without-overlap", "سال ۱۴۰۳ تمام شد"),
    ("date-with-slashes", "تاریخ ۱۴۰۳/۰۵/۱۲ است"),
    ("phone-like-no-overlap", "شماره ۰۹۱۳ ۹۹۹ است"),
    ("tld-alone", "پسوند com است"),
    ("not-a-substring", "فایل meeting.notes است"),
    ("short-local-word", "نام ali است"),
    ("short-local-word-upper", "نام ALI است"),
    ("short-prefix", "بخش rez است"),
    ("substring-not-a-label", "کلمه exam است"),
    ("plain-words", "meeting report"),
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [pytest.param(value, expected, id=name) for name, value, expected in DENY_CASES],
)
def test_redacted_view_masks_a_fragment(known_meeting, settings, value, expected):
    assert detect(value, list(PiiClass)) == []
    tenant, meeting_id = known_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [expected]


@pytest.mark.parametrize("value", [pytest.param(value, id=name) for name, value in ALLOW_CASES])
def test_redacted_view_keeps_a_non_fragment(known_meeting, settings, value):
    tenant, meeting_id = known_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [value]


@pytest.fixture
def account_meeting(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom284-account")
    meeting_id, _ = _process(client, tenant, settings, "hom284-account", ACCOUNT_SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    return tenant, meeting_id


ACCOUNT_DENY_CASES = [
    ("card-first-six", "کارت ۶۰۳۷۶۹ است", "کارت [BANK_CARD_1] است"),
    ("card-last-four", "کارتی که به ۷۵۳۶ ختم می‌شود", "کارتی که به [BANK_CARD_1] ختم می‌شود"),
    ("iban-digits", "شبا 0570 0281 است", "شبا [IBAN_1] است"),
    ("iban-persian-digits", "شبا ۰۵۷۰ ۰۲۸۱ است", "شبا [IBAN_1] است"),
    ("postal-first-five", "کد پستی ۱۹۶۵۸ است", "کد پستی [IR_POSTAL_CODE_1] است"),
]

ACCOUNT_ALLOW_CASES = [
    ("card-last-three", "کارتی که به ۵۳۶ ختم می‌شود"),
    ("card-like-no-overlap", "کارت ۶۰۳۸ ۶۰۳۸ است"),
    ("iban-country-and-check", "شبا IR71 است"),
    ("iban-three-digits", "شبا 057 است"),
    ("postal-three-digits", "کد پستی ۱۹۶ است"),
    ("postal-like-no-overlap", "کد پستی ۱۹۶۴ ۲۲ است"),
]


def test_account_whole_values_keep_their_placeholders(account_meeting, settings):
    tenant, meeting_id = account_meeting
    assert [(span.pii_class, span.value) for span in detect(" ".join(ACCOUNT_SEGMENTS))] == [
        (PiiClass.BANK_CARD, ACCOUNT_CARD),
        (PiiClass.IBAN, ACCOUNT_IBAN),
        (PiiClass.IR_POSTAL_CODE, ACCOUNT_POSTAL),
    ]
    assert _render(settings, tenant, meeting_id, ACCOUNT_SEGMENTS) == [
        "شماره کارت [BANK_CARD_1] را برای واریز بنویسید.",
        "شبا [IBAN_1] و کد پستی [IR_POSTAL_CODE_1] است.",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [pytest.param(value, expected, id=name) for name, value, expected in ACCOUNT_DENY_CASES],
)
def test_redacted_view_masks_an_account_fragment(account_meeting, settings, value, expected):
    assert detect(value, list(PiiClass)) == []
    tenant, meeting_id = account_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [expected]


@pytest.mark.parametrize(
    "value", [pytest.param(value, id=name) for name, value in ACCOUNT_ALLOW_CASES]
)
def test_redacted_view_keeps_an_account_non_fragment(account_meeting, settings, value):
    tenant, meeting_id = account_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [value]


WIDE_DENY_CASES = [
    ("glued-www", "سایت www.example.com است", "سایت [EMAIL_1] است"),
    ("glued-www-upper", "سایت WWW.EXAMPLE.COM است", "سایت [EMAIL_1] است"),
    ("glued-www-sentence-dot", "سایت www.example.com.", "سایت [EMAIL_1]."),
    ("whole-address-digit-glued", "نشانی ali.rezaei@example.com2 است", "نشانی [EMAIL_1] است"),
    ("whole-address-dash-glued", "نشانی ali.rezaei@example.com-x است", "نشانی [EMAIL_1] است"),
    ("local-part-letter-glued", "نشانی ali.rezaeix است", "نشانی [EMAIL_1] است"),
    ("local-part-prefixed", "نشانی mail-ali.rezaei است", "نشانی [EMAIL_1] است"),
    ("local-part-digit-glued", "نشانی ali.rezaei2 است", "نشانی [EMAIL_1] است"),
    ("label-digit-glued", "شرکت example2 است", "شرکت [EMAIL_1] است"),
    ("label-dash-prefixed", "شرکت x-example است", "شرکت [EMAIL_1] است"),
    ("label-next-to-dot", "سایت example.org است", "سایت [EMAIL_1] است"),
]

WIDE_ALLOW_CASES = [
    ("label-letter-suffix", "نمونه examples است"),
    ("label-inside-word", "کلمه counterexample است"),
    ("label-letter-prefix", "کلمه xexample است"),
    ("local-label-letter-suffix", "نام rezaeian است"),
]

DOTTED_ALLOW_CASES = [
    ("unrelated-domain", "سایت google.com است"),
    ("dotted-part-no-whole-label", "سایت e.com است"),
    ("dotted-part-inside-local-part", "کد i.re است"),
]

R6_DENY_CASES = [
    ("placeholder-shaped-digits", "کد [A_0912345] است", "کد [A_[PHONE_1]] است"),
    ("placeholder-shaped-persian-digits", "کد [X_۰۹۱۲۳۴۵] است", "کد [X_[PHONE_1]] است"),
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(value, expected, id=name)
        for name, value, expected in WIDE_DENY_CASES + R6_DENY_CASES
    ],
)
def test_redacted_view_masks_a_glued_or_disguised_fragment(
    known_meeting, settings, value, expected
):
    assert detect(value, list(PiiClass)) == []
    tenant, meeting_id = known_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [expected]


@pytest.mark.parametrize(
    "value", [pytest.param(value, id=name) for name, value in WIDE_ALLOW_CASES]
)
def test_redacted_view_keeps_a_label_without_a_boundary(known_meeting, settings, value):
    tenant, meeting_id = known_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [value]


@pytest.mark.parametrize(
    "value", [pytest.param(value, id=name) for name, value in DOTTED_ALLOW_CASES]
)
def test_redacted_view_keeps_a_dotted_part_without_a_whole_label(known_meeting, settings, value):
    tenant, meeting_id = known_meeting
    assert _render(settings, tenant, meeting_id, [value]) == [value]


def test_known_placeholders_in_free_text_stay_as_they_are(known_meeting, settings):
    tenant, meeting_id = known_meeting
    value = "تماس با [PHONE_1] و [EMAIL_2] و [IR_NATIONAL_ID_1]"
    assert _render(settings, tenant, meeting_id, [value]) == [value]


def test_a_leading_plus_prefix_fragment_shows_no_digit(known_meeting, settings):
    tenant, meeting_id = known_meeting
    value = "تماس با +98 912 345 گرفته شد"
    assert detect(value, list(PiiClass)) == []
    (out,) = _render(settings, tenant, meeting_id, [value])
    assert out in ("تماس با [PHONE_1] گرفته شد", "تماس با +[PHONE_1] گرفته شد")


def test_a_new_whole_email_keeps_whole_value_redaction(known_meeting, settings):
    tenant, meeting_id = known_meeting
    (out,) = _render(settings, tenant, meeting_id, ["نشانی rezaei@example.com است"])
    assert re.fullmatch(r"نشانی \[EMAIL_\d+\] است", out)


def test_whole_values_and_fragments_in_one_text(known_meeting, settings):
    tenant, meeting_id = known_meeting
    value = "تلفن ۰۹۱۲۳۴۵۶۷۸۹ و بخشی 0912-345 و ایمیل ali.rezaei@example.com و example.com"
    assert _render(settings, tenant, meeting_id, [value]) == [
        "تلفن [PHONE_1] و بخشی [PHONE_1] و ایمیل [EMAIL_1] و [EMAIL_1]"
    ]


def test_whole_values_keep_their_placeholders(known_meeting, settings):
    tenant, meeting_id = known_meeting
    values = [
        "تلفن ۰۹۱۲۳۴۵۶۷۸۹ و ali.rezaei@example.com",
        "دفتر ۰۲۱ ۸۸۷۷ ۶۶۵۵",
        f"کد ملی {NID2_FA}",
        "پیگیری با sara@mailbox.ir",
    ]
    assert _render(settings, tenant, meeting_id, values) == [
        "تلفن [PHONE_1] و [EMAIL_1]",
        "دفتر [PHONE_2]",
        "کد ملی [IR_NATIONAL_ID_1]",
        "پیگیری با [EMAIL_2]",
    ]


def test_masking_a_fragment_creates_no_placeholder(known_meeting, settings):
    tenant, meeting_id = known_meeting
    values = ["بخشی 0912-345 و example.com", "شماره جدید ۰۹۳۵ ۱۱۱ ۲۲۳۳", "بخشی ۸۸۷۷ ۶۶۵۵"]
    assert _render(settings, tenant, meeting_id, values) == [
        "بخشی [PHONE_1] و [EMAIL_1]",
        "شماره جدید [PHONE_3]",
        "بخشی [PHONE_2]",
    ]


def test_a_new_whole_value_after_no_fragment_gets_the_same_number(known_meeting, settings):
    tenant, meeting_id = known_meeting
    values = ["اتاق ۳۴۵", "شماره جدید ۰۹۳۵ ۱۱۱ ۲۲۳۳"]
    assert _render(settings, tenant, meeting_id, values) == ["اتاق ۳۴۵", "شماره جدید [PHONE_3]"]


def test_the_same_fragment_renders_the_same_in_two_views(known_meeting, settings):
    tenant, meeting_id = known_meeting
    first = _render(settings, tenant, meeting_id, ["با ۰۹۱۲ ۳۴۵ و example.com"])
    second = _render(settings, tenant, meeting_id, ["با ۰۹۱۲ ۳۴۵ و example.com"])
    assert first == second == ["با [PHONE_1] و [EMAIL_1]"]


def test_the_original_view_returns_free_text_unchanged(known_meeting, settings):
    tenant, meeting_id = known_meeting
    values = [value for _name, value, _expected in DENY_CASES] + [None]
    assert _render(settings, tenant, meeting_id, values, view="original") == values


def test_a_meeting_without_known_values_keeps_free_text(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom284-none")
    meeting_id, _ = _process(client, tenant, settings, "hom284-none", NEUTRAL_SEGMENTS)
    stored = "با ۰۹۱۲ ۳۴۵ یا 0912-345 تماس، سایت example.com، نشانی ali.rezaei، سال ۱۴۰۳"
    _seed_summary(meeting_id, overview=stored, topics=[stored])
    action_id = _add_action(client, tenant, meeting_id, stored, stored)
    _set_policy(client, tenant, pii_redaction_enabled=True)
    values = [value for _name, value, _expected in DENY_CASES]
    assert _render(settings, tenant, meeting_id, values) == values
    base = f"/api/v1/meetings/{meeting_id}"
    summary = _get(client, tenant, f"{base}/summary").json()
    assert summary["view"] == "redacted"
    assert summary["overview"] == stored and summary["topics"] == [stored]
    (action,) = _get(client, tenant, f"{base}/actions").json()
    assert action["id"] == action_id
    assert action["description"] == stored and action["assignee_text"] == stored


def test_summary_endpoint_masks_fragments_in_every_field(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-sum")
    base = f"/api/v1/meetings/{meeting_id}"
    transcript = _get(client, tenant, f"{base}/transcript").json()
    assert [s["text"] for s in transcript["segments"]] == REDACTED_SEGMENTS
    summary = _get(client, tenant, f"{base}/summary").json()
    assert summary["view"] == "redacted"
    _assert_masked_summary(summary)


def test_actions_endpoint_masks_fragments_in_description_and_assignee(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, actions = _leaky_meeting(
        client, email_outbox, monkeypatch, settings, "h-act"
    )
    listed = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/actions").json()
    assert [item["id"] for item in listed] == actions
    assert [(item["description"], item["assignee_text"]) for item in listed] == EXPECTED_ACTIONS


def test_report_endpoint_masks_fragments(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-rep")
    report = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/report").json()
    summary = report["summary"]
    assert summary["overview"] == MASKED_OVERVIEW
    assert summary["topics"] == MASKED_TOPICS
    assert [(p["category"], p["text"]) for p in summary["key_points"]] == MASKED_KEY_POINTS
    (decision,) = report["outcome"]["decisions"]
    assert decision["text"] == _masked("outcome")
    texts = [finding["text"] for finding in report["qa"]["findings"]]
    assert MASKED_OVERVIEW in texts
    assert all(_visible(text) == [] for text in texts), texts


def test_report_decision_owner_is_masked(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-own")
    report = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/report").json()
    (decision,) = report["outcome"]["decisions"]
    assert decision["owner"] == {"status": "stated", "value": _masked("owner")}


def test_export_masks_fragments(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-exp")
    base = f"/api/v1/meetings/{meeting_id}/export"
    exported = _get(client, tenant, base, format="json").json()
    assert exported["summary"] == MASKED_OVERVIEW
    assert exported["topics"] == MASKED_TOPICS
    assert [(a["description"], a["assignee"]) for a in exported["actions"]] == EXPECTED_ACTIONS
    assert exported["privacy"]["view"] == "redacted"
    assert exported["privacy"]["entity_count"] == 4
    txt = _get(client, tenant, base, format="txt").content.decode()
    assert MASKED_OVERVIEW in txt and LEAK_OVERVIEW not in txt


def test_share_page_and_share_export_mask_fragments(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-shr")
    _set_policy(client, tenant, public_share_links_enabled=True)
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        json={"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert share.status_code == 201, share.text
    token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{token}")
    assert shared.status_code == 200, shared.text
    body = shared.json()
    _assert_masked_summary(body["summary"])
    pairs = [(a["description"], a["assignee_text"]) for a in body["actions"]]
    assert pairs == EXPECTED_ACTIONS
    exported = client.get(f"/api/v1/shares/{token}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload["summary"] == MASKED_OVERVIEW
    assert payload["topics"] == MASKED_TOPICS
    assert [(a["description"], a["assignee"]) for a in payload["actions"]] == EXPECTED_ACTIONS


def test_search_is_not_an_oracle_for_fragments(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-src")
    for query in ("0912-345", "example.com", "ali.rezaei", "۸۸۷۷ ۶۶۵۵", "mailbox.ir"):
        found = _get(client, tenant, "/api/v1/search", q=query).json()["items"]
        assert found == [], query


def test_search_still_finds_visible_words_without_fragments(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-sr2")
    found = _get(client, tenant, "/api/v1/search", q="۱۴۰۳").json()["items"]
    assert [item["id"] for item in found] == [meeting_id]
    kinds = {match["kind"] for match in found[0]["matches"]}
    assert {"summary", "action"} <= kinds
    for match in found[0]["matches"]:
        assert _visible(match["snippet"]) == [], match


def test_original_view_endpoints_return_free_text_unchanged(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-org")
    base = f"/api/v1/meetings/{meeting_id}"
    summary = _get(client, tenant, f"{base}/summary", view="original").json()
    assert summary["view"] == "original"
    assert summary["overview"] == LEAK_OVERVIEW and summary["topics"] == LEAK_TOPICS
    assert [(p["category"], p["description"]) for p in summary["key_points"]] == [
        (p["category"], p["description"]) for p in LEAK_KEY_POINTS
    ]
    assert summary["decisions"][0]["owner"]["value"] == _leak("owner")
    listed = _get(client, tenant, f"{base}/actions", view="original").json()
    assert [(a["description"], a["assignee_text"]) for a in listed] == [
        (_leak("task"), _leak("assignee")),
        ("ارسال برای sara@mailbox.ir", None),
        ("شماره جدید ۰۹۳۵ ۱۱۱ ۲۲۳۳ و دامنه mailbox", None),
    ]


def test_fragments_change_no_transcript_span_or_provenance(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _ = _leaky_meeting(client, email_outbox, monkeypatch, settings, "h-n6")
    base = f"/api/v1/meetings/{meeting_id}"
    transcript = _get(client, tenant, f"{base}/transcript").json()
    assert [s["text"] for s in transcript["segments"]] == REDACTED_SEGMENTS
    assert transcript["redaction"]["entity_count"] == 4
    segments = {s["id"]: s["text"] for s in transcript["segments"]}
    evidence = _get(client, tenant, f"{base}/evidence").json()["items"]
    assert evidence
    for item in evidence:
        assert segments[item["segment_id"]][item["text_start"] : item["text_end"]] == item["text"]
    summary = _get(client, tenant, f"{base}/summary").json()
    assert set(summary) == {
        "meeting_id",
        "overview",
        "decisions",
        "key_points",
        "topics",
        "sentiment",
        "provider",
        "model",
        "schema_version",
        "analysis_run_id",
        "analysis_run",
        "evidence_status",
        "created_at",
        "updated_at",
        "view",
        "privacy_enforced",
        "redaction",
    }
    (first, *_rest) = _get(client, tenant, f"{base}/actions").json()
    assert set(first) == {
        "id",
        "meeting_id",
        "description",
        "assignee_text",
        "assignee_user_id",
        "due_at",
        "priority",
        "status",
        "source_segment_id",
        "source_timestamp_ms",
        "confidence",
        "created_by",
        "analysis_run_id",
        "evidence_status",
        "evidence",
        "created_at",
        "updated_at",
        "view",
        "privacy_enforced",
        "redaction",
    }
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        rows = list(
            db.scalars(
                select(TranscriptRedaction).where(TranscriptRedaction.revision_id == revision.id)
            )
        )
    assert [row.entity_count for row in rows] == [4]


def test_the_provider_input_is_unchanged(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-prov")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    _meeting_id, intelligence = _process(client, tenant, settings, "h-prov", SEGMENTS)
    (request,) = intelligence.requests
    assert [turn.text for turn in request.turns] == REDACTED_SEGMENTS


def test_offsets_still_widen_to_whole_placeholders():
    text = "call ۰۹۱۲ ۳۴۵ ۶۷۸۹ now"
    redacted, replacements = redact(text, [PiiClass.PHONE])
    item = RedactedText(redacted, tuple(replacements))
    assert redacted == "call [PHONE_1] now"
    start = text.index("۰۹۱۲")
    assert item.offset(start + 3, end=False) == 5
    assert item.offset(start + 3, end=True) == 5 + len("[PHONE_1]")
    assert item.offset(len(text), end=True) == len(redacted)


def test_an_ambiguous_fragment_gets_one_stable_placeholder(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-amb")
    meeting_id, _ = _process(client, tenant, settings, "h-amb", OVERLAPPING_SEGMENTS)
    _seed_summary(meeting_id, overview="با ۰۹۱۲ ۳۴۵ تماس بگیرید")
    _add_action(client, tenant, meeting_id, "با ۰۹۱۲ ۳۴۵ تماس بگیرید")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    base = f"/api/v1/meetings/{meeting_id}"
    overview = _get(client, tenant, f"{base}/summary").json()["overview"]
    (action,) = _get(client, tenant, f"{base}/actions").json()
    exported = _get(client, tenant, f"{base}/export", format="json").json()
    assert overview in ("با [PHONE_1] تماس بگیرید", "با [IR_NATIONAL_ID_1] تماس بگیرید")
    assert action["description"] == overview
    assert exported["summary"] == overview
    assert exported["actions"][0]["description"] == overview


def test_write_back_keeps_the_fragment_and_whole_values(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-wb")
    meeting_id, _ = _process(client, tenant, settings, "h-wb", SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    fragment_id = _add_action(client, tenant, meeting_id, "با ۰۹۱۲ ۳۴۵ تماس بگیرید", "ali.rezaei")
    whole_id = _add_action(client, tenant, meeting_id, "با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    base = f"/api/v1/meetings/{meeting_id}"
    shown = {item["id"]: item for item in _get(client, tenant, f"{base}/actions").json()}
    assert shown[fragment_id]["description"] == "با [PHONE_1] تماس بگیرید"
    assert shown[fragment_id]["assignee_text"] == "[EMAIL_1]"
    assert shown[whole_id]["description"] == "با [PHONE_1] تماس بگیرید"

    edited = client.patch(
        f"{base}/actions/{fragment_id}",
        json={
            "description": shown[fragment_id]["description"] + " فوری",
            "assignee_text": shown[fragment_id]["assignee_text"] + " (مدیر)",
        },
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "با [PHONE_1] تماس بگیرید فوری"
    assert edited.json()["assignee_text"] == "[EMAIL_1] (مدیر)"
    assert _stored_action(fragment_id) == ("با ۰۹۱۲ ۳۴۵ تماس بگیرید فوری", "ali.rezaei (مدیر)")

    edited = client.patch(
        f"{base}/actions/{whole_id}",
        json={"description": shown[whole_id]["description"] + " فوری"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert edited.status_code == 200, edited.text
    assert _stored_action(whole_id) == ("با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید فوری", None)


def test_write_back_of_a_whole_value_restores_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-wb2")
    meeting_id, _ = _process(client, tenant, settings, "h-wb2", SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    whole_id = _add_action(client, tenant, meeting_id, "با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    base = f"/api/v1/meetings/{meeting_id}"
    (shown,) = _get(client, tenant, f"{base}/actions").json()
    assert shown["description"] == "با [PHONE_1] تماس بگیرید"
    edited = client.patch(
        f"{base}/actions/{whole_id}",
        json={"description": shown["description"] + " فوری"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert edited.status_code == 200, edited.text
    assert _stored_action(whole_id) == ("با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید فوری", None)


def test_action_create_keeps_todays_restore(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-wb3")
    meeting_id, _ = _process(client, tenant, settings, "h-wb3", SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    _add_action(client, tenant, meeting_id, "با ۰۹۱۲ ۳۴۵ تماس بگیرید", "ali.rezaei")
    _add_action(client, tenant, meeting_id, "با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    created = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        json={"description": "پیگیری [PHONE_1] و [EMAIL_1]", "priority": "high"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "پیگیری [PHONE_1] و [EMAIL_1]"
    assert _stored_action(created.json()["id"]) == (
        "پیگیری ۰۹۱۲۳۴۵۶۷۸۹ و ali.rezaei@example.com",
        None,
    )


def _patch_action(client, tenant, meeting_id, action_id, payload):
    edited = client.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{action_id}",
        json=payload,
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert edited.status_code == 200, edited.text
    return edited.json()


def test_ambiguous_placeholders_restore_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-n5a")
    meeting_id, _ = _process(client, tenant, settings, "h-n5a", SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    fragment_first = _add_action(client, tenant, meeting_id, "۰۹۱۲ ۳۴۵ یا ۰۹۱۲۳۴۵۶۷۸۹")
    whole_first = _add_action(client, tenant, meeting_id, "۰۹۱۲۳۴۵۶۷۸۹ یا ۰۹۱۲ ۳۴۵")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    shown = {
        item["id"]: item["description"]
        for item in _get(client, tenant, f"/api/v1/meetings/{meeting_id}/actions").json()
    }
    assert shown == {
        fragment_first: "[PHONE_1] یا [PHONE_1]",
        whole_first: "[PHONE_1] یا [PHONE_1]",
    }
    for action_id in (fragment_first, whole_first):
        edited = _patch_action(client, tenant, meeting_id, action_id, {"description": "[PHONE_1]"})
        assert edited["description"] == "[PHONE_1]"
        assert _stored_action(action_id) == ("۰۹۱۲۳۴۵۶۷۸۹", None)


def test_repeated_placeholders_for_one_text_keep_it(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "h-n5b")
    meeting_id, _ = _process(client, tenant, settings, "h-n5b", SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    action_id = _add_action(client, tenant, meeting_id, "۰۹۱۲ ۳۴۵ و ۰۹۱۲ ۳۴۵")
    _set_policy(client, tenant, pii_redaction_enabled=True)
    (shown,) = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/actions").json()
    assert shown["description"] == "[PHONE_1] و [PHONE_1]"
    edited = _patch_action(
        client, tenant, meeting_id, action_id, {"description": "[PHONE_1] و [PHONE_1] فوری"}
    )
    assert edited["description"] == "[PHONE_1] و [PHONE_1] فوری"
    assert _stored_action(action_id) == ("۰۹۱۲ ۳۴۵ و ۰۹۱۲ ۳۴۵ فوری", None)


_PLACEHOLDER = re.compile(r"\[[A-Z_]+_[0-9]+\]")


def _email_hidden(text: str | None) -> bool:
    visible = _PLACEHOLDER.sub(" ", text or "").lower()
    shown = re.search(r"\[EMAIL_[0-9]+\]", text or "") is not None
    return shown and not any(part in visible for part in ("ali", "rezaei", "example", "@"))


def _member_meeting(
    client,
    email_outbox,
    monkeypatch,
    settings,
    label,
    assignee_text=None,
    description="پیگیری کنید",
):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    tenant = {
        "workspace_id": owner.workspace_id,
        "cookies": owner.cookies,
        "user_id": owner.user_id,
    }
    meeting_id, _ = _process(client, tenant, settings, label, SEGMENTS)
    _seed_summary(meeting_id, overview="جلسه درباره قرارداد بود.")
    action_id = _add_action(client, tenant, meeting_id, description, assignee_text)
    _set_policy(client, tenant, pii_redaction_enabled=True)
    return personas["member"], meeting_id, action_id


N7_CASES = [
    ("patch-assignee-digit", "patch", "assignee_text", "[EMAIL_1]2", None),
    ("patch-assignee-dash", "patch", "assignee_text", "[EMAIL_1]-x", None),
    ("patch-assignee-prefix", "patch", "assignee_text", "x[EMAIL_1]", None),
    ("patch-assignee-on-fragment", "patch", "assignee_text", "[EMAIL_1]x", "ali.rezaei"),
    ("patch-description-digit", "patch", "description", "[EMAIL_1]2", None),
    ("post-description-digit", "post", "description", "[EMAIL_1]2", None),
    ("post-description-dash", "post", "description", "[EMAIL_1]-x", None),
    ("post-description-prefix", "post", "description", "x[EMAIL_1]", None),
    ("post-assignee-dash", "post", "assignee_text", "[EMAIL_1]-x", None),
]


@pytest.mark.parametrize(
    ("method", "field", "sent", "stored_assignee"),
    [pytest.param(*case[1:], id=case[0]) for case in N7_CASES],
)
def test_member_write_back_never_shows_an_email(
    client, email_outbox, monkeypatch, settings, method, field, sent, stored_assignee
):
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n7", stored_assignee
    )
    base = f"/api/v1/meetings/{meeting_id}/actions"
    if stored_assignee is not None:
        (shown,) = member.get(base).json()
        assert shown["assignee_text"] == "[EMAIL_1]"
    if method == "patch":
        response = member.patch(f"{base}/{action_id}", json={field: sent})
        assert response.status_code == 200, response.text
    else:
        response = member.post(base, json={"description": "پیگیری کنید", field: sent})
        assert response.status_code == 201, response.text
        action_id = response.json()["id"]
    assert _email_hidden(response.json()[field]), response.json()[field]
    listed = {item["id"]: item for item in member.get(base).json()}
    assert _email_hidden(listed[action_id][field]), listed[action_id][field]
    exported = member.get(f"/api/v1/meetings/{meeting_id}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    texts = [
        a[key] or "" for a in exported.json()["actions"] for key in ("description", "assignee")
    ]
    assert not any(part in text for text in texts for part in ("ali.rezaei", "example.com"))


def test_member_write_back_of_a_clean_placeholder_still_works(
    client, email_outbox, monkeypatch, settings
):
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n7ok"
    )
    base = f"/api/v1/meetings/{meeting_id}"
    assert member.get(f"{base}/actions", params={"view": "original"}).status_code == 403
    edited = member.patch(f"{base}/actions/{action_id}", json={"assignee_text": "[EMAIL_1] (مدیر)"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["assignee_text"] == "[EMAIL_1] (مدیر)"
    assert _stored_action(action_id) == ("پیگیری کنید", "ali.rezaei@example.com (مدیر)")
    created = member.post(f"{base}/actions", json={"description": "تماس با [PHONE_1]"})
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "تماس با [PHONE_1]"
    assert _stored_action(created.json()["id"]) == ("تماس با ۰۹۱۲ ۳۴۵ ۶۷۸۹", None)


_FIELD_INDEX = {"description": 0, "assignee_text": 1}

GLUED_LABEL_CASES = [
    (
        "label-assignee-letter-after",
        "assignee_text",
        "پیگیری کنید",
        "example",
        "[EMAIL_1]",
        "[EMAIL_1]s",
        "ali.rezaei@example.coms",
    ),
    (
        "label-assignee-letter-before",
        "assignee_text",
        "پیگیری کنید",
        "example",
        "[EMAIL_1]",
        "x[EMAIL_1]",
        "xali.rezaei@example.com",
    ),
    (
        "dash-label-assignee-letter-after",
        "assignee_text",
        "پیگیری کنید",
        "x-example",
        "[EMAIL_1]",
        "[EMAIL_1]s",
        "ali.rezaei@example.coms",
    ),
    (
        "label-description-letter-before",
        "description",
        "شرکت example",
        None,
        "شرکت [EMAIL_1]",
        "شرکت x[EMAIL_1]",
        "شرکت xali.rezaei@example.com",
    ),
    (
        "label-description-letter-after",
        "description",
        "شرکت example",
        None,
        "شرکت [EMAIL_1]",
        "شرکت [EMAIL_1]s",
        "شرکت ali.rezaei@example.coms",
    ),
]


@pytest.mark.parametrize(
    ("field", "description", "assignee", "shown", "sent", "stored"),
    [pytest.param(*case[1:], id=case[0]) for case in GLUED_LABEL_CASES],
)
def test_member_glued_write_back_of_a_label_fragment_stores_the_whole_value(
    client, email_outbox, monkeypatch, settings, field, description, assignee, shown, sent, stored
):
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n7b", assignee, description
    )
    base = f"/api/v1/meetings/{meeting_id}/actions"
    (listed,) = member.get(base).json()
    assert listed[field] == shown
    response = member.patch(f"{base}/{action_id}", json={field: sent})
    assert response.status_code == 200, response.text
    assert _email_hidden(response.json()[field]), response.json()[field]
    (listed,) = member.get(base).json()
    assert _email_hidden(listed[field]), listed[field]
    exported = member.get(f"/api/v1/meetings/{meeting_id}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    for action in exported.json()["actions"]:
        for key in ("description", "assignee"):
            visible = _PLACEHOLDER.sub(" ", action[key] or "").lower()
            assert not any(part in visible for part in ("ali", "rezaei", "example", "@")), action
    assert _stored_action(action_id)[_FIELD_INDEX[field]] == stored


NOT_GLUED_LABEL_CASES = [
    ("persian-comma-after", "شرکت [EMAIL_1]، فوری", "شرکت example، فوری"),
    ("persian-letters-after", "شرکت [EMAIL_1]ها", "شرکت exampleها"),
]


@pytest.mark.parametrize(
    ("sent", "stored"),
    [pytest.param(sent, stored, id=name) for name, sent, stored in NOT_GLUED_LABEL_CASES],
)
def test_member_write_back_next_to_other_characters_keeps_the_label_fragment(
    client, email_outbox, monkeypatch, settings, sent, stored
):
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n7c", None, "شرکت example"
    )
    base = f"/api/v1/meetings/{meeting_id}/actions"
    (listed,) = member.get(base).json()
    assert listed["description"] == "شرکت [EMAIL_1]"
    response = member.patch(f"{base}/{action_id}", json={"description": sent})
    assert response.status_code == 200, response.text
    assert response.json()["description"] == sent
    (listed,) = member.get(base).json()
    assert listed["description"] == sent
    assert _stored_action(action_id) == (stored, None)


def test_a_kept_placeholder_keeps_the_fragment_and_an_added_one_gets_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n5c", None, "با ۰۹۱۲ ۳۴۵ تماس"
    )
    base = f"/api/v1/meetings/{meeting_id}/actions"
    (listed,) = member.get(base).json()
    assert listed["description"] == "با [PHONE_1] تماس"
    sent = "با [PHONE_1] تماس، شماره کامل [PHONE_1]"
    response = member.patch(f"{base}/{action_id}", json={"description": sent})
    assert response.status_code == 200, response.text
    assert response.json()["description"] == sent
    assert _stored_action(action_id) == ("با ۰۹۱۲ ۳۴۵ تماس، شماره کامل ۰۹۱۲ ۳۴۵ ۶۷۸۹", None)


_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩０１２３４５６７８９", "0123456789" * 3)


def _whole_phone(text: str | None, before: str, after: str) -> bool:
    match = re.fullmatch(re.escape(before) + "(.+)" + re.escape(after), text or "")
    return match is not None and re.sub(r"[\s-]", "", match.group(1).translate(_FOLD)) == (
        "09123456789"
    )


def _digits_hidden(text: str | None) -> bool:
    visible = re.sub(r"[\s-]", "", _PLACEHOLDER.sub(" ", text or "").translate(_FOLD))
    return re.search(r"[0-9]{2}", visible) is None


def test_a_placeholder_stored_literally_makes_the_placeholder_ambiguous(
    client, email_outbox, monkeypatch, settings
):
    stored = "با ۰۹۱۲ ۳۴۵ یا [PHONE_1] تماس"
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n5d", None, stored
    )
    assert _stored_action(action_id) == (stored, None)
    base = f"/api/v1/meetings/{meeting_id}/actions"
    (listed,) = member.get(base).json()
    assert listed["description"] == "با [PHONE_1] یا [PHONE_1] تماس"
    sent = "با [PHONE_1] یا"
    response = member.patch(f"{base}/{action_id}", json={"description": sent})
    assert response.status_code == 200, response.text
    assert response.json()["description"] == sent
    (listed,) = member.get(base).json()
    assert listed["description"] == sent
    description, _ = _stored_action(action_id)
    assert _whole_phone(description, "با ", " یا"), description


def test_a_glued_persian_digit_restores_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    member, meeting_id, action_id = _member_meeting(
        client, email_outbox, monkeypatch, settings, "h-n7e", "98 912 تماس"
    )
    base = f"/api/v1/meetings/{meeting_id}/actions"
    (listed,) = member.get(base).json()
    assert listed["assignee_text"] == "[PHONE_1] تماس"
    response = member.patch(f"{base}/{action_id}", json={"assignee_text": "۱[PHONE_1] تماس"})
    assert response.status_code == 200, response.text
    for shown in (response.json(), member.get(base).json()[0]):
        assert "[PHONE_1]" in shown["assignee_text"], shown
        assert _digits_hidden(shown["assignee_text"]), shown
    exported = member.get(f"/api/v1/meetings/{meeting_id}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    for action in exported.json()["actions"]:
        assert all(_digits_hidden(action[key]) for key in ("description", "assignee")), action
    _, assignee = _stored_action(action_id)
    assert _whole_phone(assignee, "۱", " تماس"), assignee
