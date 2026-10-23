from __future__ import annotations

import base64
import json
import os

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import (
    ActionItem,
    AuditEvent,
    TranscriptRevision,
    TranscriptSegment,
)
from backend.jobs.processor import process_job
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRResult, ASRSegment
from backend.services.transcript_service import current_revision
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_privacy_routing import SEGMENTS, _no_raw

PHONE = "۰۹۱۲ ۳۴۵ ۶۷۸۹"
PHONE_JOINED = "۰۹۱۲۳۴۵۶۷۸۹"
EMAIL = "ali.rezaei@example.com"
GLUED = "شماره۰۹۱۲۳۴۵۶۷۸۹ را یادداشت کنید."
PLAIN = "داخلی ۶۷۸۹ را بگیرید."
STORED = [*SEGMENTS, GLUED, PLAIN]
SHOWN = [
    "سلام، من علی هستم. کد ملی [IR_NATIONAL_ID_1] و موبایل [PHONE_1] است.",
    "ایمیل [EMAIL_1] را بنویسید و دوباره [PHONE_1] را تکرار می‌کنم.",
    "شماره[PHONE_1] را یادداشت کنید.",
    "داخلی ۶۷۸۹ را بگیرید.",
]
LITERAL_STORED = [
    "ایمیل [EMAIL_9] را بعدا بفرستید.",
    f"کد [PHONE_1] همان {PHONE_JOINED} است.",
    f"ایمیل {EMAIL} را بنویسید.",
    "یادداشت [EMAIL_1] قدیمی است.",
    "کد x[IBAN_7]2 ثبت شد.",
    f"شماره {PHONE_JOINED} یعنی {PHONE} است.",
    f"شماره +98 912 345 6789 یا {PHONE_JOINED} است.",
]
LITERAL_SHOWN = [
    "ایمیل [EMAIL_9] را بعدا بفرستید.",
    "کد [PHONE_1] همان [PHONE_1] است.",
    "ایمیل [EMAIL_1] را بنویسید.",
    "یادداشت [EMAIL_1] قدیمی است.",
    "کد x[IBAN_7]2 ثبت شد.",
    "شماره [PHONE_1] یعنی [PHONE_1] است.",
    "شماره [PHONE_1] یا [PHONE_1] است.",
]
CREATED = "transcript.revision.created"
RECOMPUTE = "meeting.recompute.requested"
AUDIT_KEYS = {
    "meeting_id",
    "revision_number",
    "parent_revision_id",
    "source",
    "reason",
    "correction_reason",
    "segments_changed",
    "segment_count",
}
SILENT_MARKERS = (
    "[",
    "]",
    "PHONE",
    "EMAIL",
    "NATIONAL",
    "IBAN",
    "NAME",
    "۶۷۸۹",
    "6789",
    "0912",
    "۰۹۱۲",
    "ali.rezaei",
    "example.com",
)


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
            duration_seconds=20,
            is_mock=True,
        )


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _meeting(client, email_outbox, monkeypatch, settings, label, *, redacted=True, segments=STORED):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    if redacted:
        enabled = client.patch(
            "/api/v1/admin/workspace-settings",
            json={"pii_redaction_enabled": True},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert enabled.status_code == 200, enabled.text
    accepted = _upload(client, tenant, key=f"{label}-0001")
    assert accepted.status_code == 202, accepted.text
    providers = ProviderBundle(
        asr=_ASR(segments),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(
        accepted.json()["job"]["id"], worker_id=label, settings=settings, providers=providers
    )
    assert result["status"] == "completed", result
    return tenant, accepted.json()["meeting"]["id"]


def _transcript(client, tenant, meeting_id) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _shown(client, tenant, meeting_id, shown=SHOWN) -> dict:
    body = _transcript(client, tenant, meeting_id)
    assert body["view"] == "redacted"
    assert [segment["text"] for segment in body["segments"]] == shown
    return body


def _correct(client, tenant, meeting_id, base_revision_id, edits):
    return client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        json={"base_revision_id": base_revision_id, "reason": "fix the wording", "edits": edits},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )


def _edit(body: dict, index: int, old: str, new: str, shown=SHOWN) -> dict:
    text = shown[index]
    assert old in text
    return {"segment_id": body["segments"][index]["id"], "text": text.replace(old, new, 1)}


def _state(tenant, meeting_id) -> tuple:
    workspace_id = tenant["workspace_id"]
    with inspection_session_factory()() as db:
        current = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
        revisions = db.scalar(
            select(func.count(TranscriptRevision.id)).where(
                TranscriptRevision.meeting_id == meeting_id
            )
        )
        counts = {
            event: db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.workspace_id == workspace_id, AuditEvent.event_type == event
                )
            )
            for event in (CREATED, RECOMPUTE)
        }
    return current.id, revisions, counts[CREATED], counts[RECOMPUTE]


def _stored(tenant, meeting_id) -> list[str]:
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        return list(
            db.scalars(
                select(TranscriptSegment.text)
                .where(TranscriptSegment.revision_id == revision.id)
                .order_by(TranscriptSegment.sequence)
            )
        )


def _assert_refused(client, tenant, meeting_id, before, response, stored=STORED, shown=SHOWN):
    after = _transcript(client, tenant, meeting_id)
    leaked = [segment["text"] for segment in after["segments"]]
    assert _no_raw(_dump(after)), leaked
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_placeholder"
    assert not [marker for marker in SILENT_MARKERS if marker in error["message"]], error
    assert _state(tenant, meeting_id) == before
    assert _stored(tenant, meeting_id) == stored
    assert leaked == shown


def _assert_accepted(client, tenant, meeting_id, before, response, expected, stored=STORED):
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["view"] == "redacted" and body["revision"]["parent_revision_id"] == before[0]
    assert _no_raw(_dump(body))
    assert _stored(tenant, meeting_id) == expected
    current, revisions, created, _ = _state(tenant, meeting_id)
    assert current == body["revision"]["id"] and current != before[0]
    assert (revisions, created) == (before[1] + 1, before[2] + 1)
    with inspection_session_factory()() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == tenant["workspace_id"],
                AuditEvent.event_type == CREATED,
                AuditEvent.target_id == current,
            )
        )
    assert audit is not None and set(audit.details) == AUDIT_KEYS
    assert audit.details["segments_changed"] == sum(
        1 for old, new in zip(stored, expected, strict=True) if old != new
    )
    after = _transcript(client, tenant, meeting_id)
    assert after["revision"]["id"] == current
    assert _no_raw(_dump(after)), [segment["text"] for segment in after["segments"]]


DENY = [
    pytest.param(0, "[PHONE_1]", "[PHONE_1]2", id="phone-then-digit"),
    pytest.param(0, "[PHONE_1]", "[PHONE_1]۲", id="phone-then-persian-digit"),
    pytest.param(0, "[PHONE_1]", "[PHONE_1]٢", id="phone-then-arabic-digit"),
    pytest.param(0, "[PHONE_1]", "2[PHONE_1]", id="digit-then-phone"),
    pytest.param(1, "[PHONE_1]", "[PHONE_1]2", id="joined-phone-then-digit"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1]2", id="email-then-digit"),
    pytest.param(1, "[EMAIL_1]", "x[EMAIL_1]", id="letter-then-email"),
    pytest.param(1, "[EMAIL_1]", "a.[EMAIL_1]", id="dot-then-email"),
    pytest.param(1, "[EMAIL_1]", "a+[EMAIL_1]", id="plus-then-email"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1].ir", id="email-then-label"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1]x", id="email-then-letter"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1]X", id="email-then-capital-letter"),
    pytest.param(
        1, "[EMAIL_1] را بنویسید و دوباره [PHONE_1]", "[EMAIL_1][PHONE_1]", id="email-then-phone"
    ),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1]_x", id="email-then-underscore"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1]-x", id="email-then-hyphen"),
    pytest.param(3, "بگیرید.", "بگیرید و [PHONE_1]2 را بنویسید.", id="added-phone-then-digit"),
    pytest.param(3, "بگیرید.", "بگیرید و به [EMAIL_9] بنویسید.", id="unknown-email-added"),
    pytest.param(0, "[PHONE_1]", "[PHONE_9]", id="unknown-phone-replaces-kept"),
    pytest.param(3, "بگیرید.", "بگیرید و [IBAN_1] را بفرستید.", id="unknown-class"),
    pytest.param(3, "بگیرید.", "بگیرید و [IR_NATIONAL_ID_2] را بفرستید.", id="unknown-number"),
    pytest.param(3, "بگیرید.", "بگیرید و [NAME_1] را بفرستید.", id="undetected-class"),
]

ALLOW = [
    pytest.param(0, "[PHONE_1]", "[PHONE_1] 2", 0, PHONE, PHONE + " 2", id="phone-space-digit"),
    pytest.param(
        0, "[PHONE_1]", "[PHONE_1] ۲", 0, PHONE, PHONE + " ۲", id="phone-space-persian-digit"
    ),
    pytest.param(
        0, "[PHONE_1]", "[PHONE_1] ٢", 0, PHONE, PHONE + " ٢", id="phone-space-arabic-digit"
    ),
    pytest.param(0, "[PHONE_1]", "2 [PHONE_1]", 0, PHONE, "2 " + PHONE, id="digit-space-phone"),
    pytest.param(
        1,
        "[PHONE_1]",
        "[PHONE_1] 2",
        1,
        PHONE_JOINED,
        PHONE_JOINED + " 2",
        id="joined-phone-space-digit",
    ),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1] 2", 1, EMAIL, EMAIL + " 2", id="email-space-digit"),
    pytest.param(1, "[EMAIL_1]", "x [EMAIL_1]", 1, EMAIL, "x " + EMAIL, id="letter-space-email"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1] x", 1, EMAIL, EMAIL + " x", id="email-space-letter"),
    pytest.param(1, "[EMAIL_1]", "a. [EMAIL_1]", 1, EMAIL, "a. " + EMAIL, id="dot-space-email"),
    pytest.param(1, "[EMAIL_1]", "@[EMAIL_1]", 1, EMAIL, "@" + EMAIL, id="at-then-email"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1] .ir", 1, EMAIL, EMAIL + " .ir", id="email-space-label"),
    pytest.param(0, "[PHONE_1]", "+[PHONE_1]", 0, PHONE, "+" + PHONE, id="plus-then-phone"),
    pytest.param(
        1,
        "[EMAIL_1] را بنویسید و دوباره [PHONE_1]",
        "[EMAIL_1] [PHONE_1]",
        1,
        f"{EMAIL} را بنویسید و دوباره {PHONE_JOINED}",
        f"{EMAIL} {PHONE_JOINED}",
        id="email-space-phone",
    ),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1]. x", 1, EMAIL, EMAIL + ". x", id="email-period-space"),
    pytest.param(1, "[EMAIL_1]", "[EMAIL_1] -x", 1, EMAIL, EMAIL + " -x", id="email-space-hyphen"),
    pytest.param(
        3,
        "بگیرید.",
        "بگیرید و به [EMAIL_1] بنویسید.",
        3,
        "بگیرید.",
        f"بگیرید و به {EMAIL} بنویسید.",
        id="known-email-added",
    ),
    pytest.param(
        3,
        "بگیرید.",
        "بگیرید و به [email_9] بنویسید.",
        3,
        "بگیرید.",
        "بگیرید و به [email_9] بنویسید.",
        id="lowercase-is-not-a-placeholder",
    ),
    pytest.param(
        2,
        "را یادداشت",
        "را فوری یادداشت",
        2,
        "را یادداشت",
        "را فوری یادداشت",
        id="kept-next-to-persian-letter",
    ),
    pytest.param(
        0, "است.", "است (تأیید شد).", 0, "است.", "است (تأیید شد).", id="kept-in-edited-segment"
    ),
    pytest.param(
        1,
        "تکرار می‌کنم.",
        "تکرار می‌کنم و [PHONE_1] را نوشتم.",
        1,
        "تکرار می‌کنم.",
        f"تکرار می‌کنم و {PHONE_JOINED} را نوشتم.",
        id="added-in-same-segment",
    ),
]

LITERAL_DENY = [
    pytest.param(0, "بفرستید.", "بفرستید و [EMAIL_9] را هم.", id="literal-unknown-added-again"),
    pytest.param(1, "است.", "است (تأیید شد).", id="ambiguous-token-kept"),
    pytest.param(1, "کد [PHONE_1] همان", "کد همان", id="ambiguous-token-one-removed"),
]

LITERAL_ALLOW = [
    pytest.param(0, "بعدا", "فردا", 0, "بعدا", "فردا", id="literal-unknown-kept"),
    pytest.param(3, "قدیمی", "جدید", 3, "قدیمی", "جدید", id="literal-known-kept"),
    pytest.param(4, "ثبت شد", "ثبت نشد", 4, "ثبت شد", "ثبت نشد", id="literal-glued-kept"),
    pytest.param(
        1,
        "کد [PHONE_1] همان [PHONE_1] است.",
        "کد همان است.",
        1,
        f"کد [PHONE_1] همان {PHONE_JOINED} است.",
        "کد همان است.",
        id="ambiguous-tokens-removed",
    ),
    pytest.param(
        2,
        "را بنویسید.",
        "را بنویسید و [PHONE_1] را بگیرید.",
        2,
        "را بنویسید.",
        f"را بنویسید و {PHONE_JOINED} را بگیرید.",
        id="ambiguous-token-added-in-other-segment",
    ),
    pytest.param(5, "است.", "درست است.", 5, "است.", "درست است.", id="two-spellings-other-word"),
    pytest.param(
        5,
        "شماره [PHONE_1] یعنی",
        "شماره یعنی",
        5,
        f"شماره {PHONE_JOINED} یعنی",
        "شماره یعنی",
        id="two-spellings-first-deleted",
    ),
    pytest.param(
        5,
        "یعنی [PHONE_1] است.",
        "یعنی است.",
        5,
        f"یعنی {PHONE} است.",
        "یعنی است.",
        id="two-spellings-second-deleted",
    ),
    pytest.param(6, "است.", "درست است.", 6, "است.", "درست است.", id="intl-and-local-other-word"),
]


@pytest.mark.parametrize(("index", "old", "new"), DENY)
def test_a_glued_or_unknown_placeholder_is_refused_and_nothing_is_written(
    client, email_outbox, monkeypatch, settings, index, old, new
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, f"o288-d{index}")
    body = _shown(client, tenant, meeting_id)
    before = _state(tenant, meeting_id)
    response = _correct(
        client, tenant, meeting_id, body["revision"]["id"], [_edit(body, index, old, new)]
    )
    _assert_refused(client, tenant, meeting_id, before, response)


@pytest.mark.parametrize(("index", "old", "new", "at", "stored_old", "stored_new"), ALLOW)
def test_the_closest_spelling_is_accepted_and_stores_the_exact_text(
    client, email_outbox, monkeypatch, settings, index, old, new, at, stored_old, stored_new
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, f"o288-a{index}")
    body = _shown(client, tenant, meeting_id)
    before = _state(tenant, meeting_id)
    response = _correct(
        client, tenant, meeting_id, body["revision"]["id"], [_edit(body, index, old, new)]
    )
    expected = list(STORED)
    assert stored_old in expected[at]
    expected[at] = expected[at].replace(stored_old, stored_new, 1)
    _assert_accepted(client, tenant, meeting_id, before, response, expected)


@pytest.mark.parametrize(("index", "old", "new"), LITERAL_DENY)
def test_an_ambiguous_or_added_literal_token_is_refused(
    client, email_outbox, monkeypatch, settings, index, old, new
):
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, f"o288-ld{index}", segments=LITERAL_STORED
    )
    body = _shown(client, tenant, meeting_id, LITERAL_SHOWN)
    before = _state(tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, index, old, new, LITERAL_SHOWN)],
    )
    _assert_refused(client, tenant, meeting_id, before, response, LITERAL_STORED, LITERAL_SHOWN)


@pytest.mark.parametrize(("index", "old", "new", "at", "stored_old", "stored_new"), LITERAL_ALLOW)
def test_a_literal_token_kept_unchanged_stays_literal(
    client, email_outbox, monkeypatch, settings, index, old, new, at, stored_old, stored_new
):
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, f"o288-la{index}", segments=LITERAL_STORED
    )
    body = _shown(client, tenant, meeting_id, LITERAL_SHOWN)
    before = _state(tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, index, old, new, LITERAL_SHOWN)],
    )
    expected = list(LITERAL_STORED)
    assert stored_old in expected[at]
    expected[at] = expected[at].replace(stored_old, stored_new, 1)
    _assert_accepted(client, tenant, meeting_id, before, response, expected, LITERAL_STORED)


def test_one_bad_edit_refuses_the_whole_request(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-mixed")
    body = _shown(client, tenant, meeting_id)
    before = _state(tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [
            _edit(body, 3, "بگیرید.", "فردا بگیرید."),
            _edit(body, 0, "[PHONE_1]", "[PHONE_1]2"),
        ],
    )
    _assert_refused(client, tenant, meeting_id, before, response)


def test_the_good_edit_alone_is_accepted(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-good")
    body = _shown(client, tenant, meeting_id)
    before = _state(tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, 3, "بگیرید.", "فردا بگیرید.")],
    )
    expected = [*STORED[:3], "داخلی ۶۷۸۹ را فردا بگیرید."]
    _assert_accepted(client, tenant, meeting_id, before, response, expected)


def test_a_placeholder_added_over_a_shown_fragment_gets_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-frag")
    body = _shown(client, tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, 3, "۶۷۸۹", "[PHONE_1]")],
    )
    assert response.status_code == 201, response.text
    stored = _stored(tenant, meeting_id)
    assert stored[:3] == STORED[:3]
    assert stored[3] in {f"داخلی {PHONE} را بگیرید.", f"داخلی {PHONE_JOINED} را بگیرید."}
    after = _transcript(client, tenant, meeting_id)
    assert after["segments"][3]["text"] == "داخلی [PHONE_1] را بگیرید."
    assert _no_raw(_dump(after))


def test_a_placeholder_added_next_to_a_persian_letter_is_accepted(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-fa")
    body = _shown(client, tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, 3, "بگیرید.", "بگیرید و شماره[PHONE_1] را بنویسید.")],
    )
    assert response.status_code == 201, response.text
    stored = _stored(tenant, meeting_id)
    assert stored[:3] == STORED[:3]
    assert stored[3] in {
        f"داخلی ۶۷۸۹ را بگیرید و شماره{PHONE} را بنویسید.",
        f"داخلی ۶۷۸۹ را بگیرید و شماره{PHONE_JOINED} را بنویسید.",
    }
    after = _transcript(client, tenant, meeting_id)
    assert after["segments"][3]["text"] == "داخلی ۶۷۸۹ را بگیرید و شماره[PHONE_1] را بنویسید."
    assert _no_raw(_dump(after))


def test_a_placeholder_known_only_from_a_free_text_is_restored(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-free")
    created = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        json={"description": "با ۰۹۳۵۱۱۱۲۲۳۳ تماس بگیرید", "priority": "high"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert created.status_code == 201, created.text
    shown_action = created.json()["description"]
    placeholder = shown_action.removeprefix("با ").removesuffix(" تماس بگیرید")
    assert placeholder.startswith("[PHONE_") and placeholder != "[PHONE_1]", shown_action
    body = _shown(client, tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, 3, "بگیرید.", f"بگیرید و {placeholder} را هم بگیرید.")],
    )
    assert response.status_code == 201, response.text
    assert _stored(tenant, meeting_id) == [
        *STORED[:3],
        "داخلی ۶۷۸۹ را بگیرید و ۰۹۳۵۱۱۱۲۲۳۳ را هم بگیرید.",
    ]
    after = _transcript(client, tenant, meeting_id)
    assert _no_raw(_dump(after)) and "۰۹۳۵" not in _dump(after)


@pytest.mark.parametrize("new", ["[PHONE_1]2", "[PHONE_9]"], ids=["glued", "unknown"])
def test_a_stale_base_stays_a_conflict(client, email_outbox, monkeypatch, settings, new):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-stale")
    body = _shown(client, tenant, meeting_id)
    first = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, 3, "بگیرید.", "فردا بگیرید.")],
    )
    assert first.status_code == 201, first.text
    before = _state(tenant, meeting_id)
    stale = _correct(
        client, tenant, meeting_id, body["revision"]["id"], [_edit(body, 0, "[PHONE_1]", new)]
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "revision_stale"
    assert _state(tenant, meeting_id) == before


@pytest.mark.parametrize("new", ["[PHONE_1]2", "[EMAIL_9]"], ids=["glued", "unknown"])
def test_the_original_view_stores_placeholder_text_literally(
    client, email_outbox, monkeypatch, settings, new
):
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, "o288-orig", redacted=False
    )
    body = _transcript(client, tenant, meeting_id)
    assert body["view"] == "original"
    assert [segment["text"] for segment in body["segments"]] == STORED
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [{"segment_id": body["segments"][0]["id"], "text": STORED[0].replace(PHONE, new)}],
    )
    assert response.status_code == 201, response.text
    assert _stored(tenant, meeting_id) == [STORED[0].replace(PHONE, new), *STORED[1:]]


@pytest.mark.parametrize(
    "code", ["invalid_segment", "invalid_speaker", "duplicate_edit", "invalid_text"]
)
def test_other_refusals_keep_their_code_after_a_refused_placeholder(
    client, email_outbox, monkeypatch, settings, code
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-order")
    body = _shown(client, tenant, meeting_id)
    second = {
        "invalid_segment": {"segment_id": "0" * 36, "text": "متن دیگر"},
        "invalid_speaker": {"segment_id": body["segments"][3]["id"], "speaker_key": "speaker_9"},
        "duplicate_edit": _edit(body, 0, "است.", "است (تأیید شد)."),
        "invalid_text": {"segment_id": body["segments"][3]["id"], "text": "   "},
    }[code]
    before = _state(tenant, meeting_id)
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [_edit(body, 0, "[PHONE_1]", "[PHONE_1]2"), second],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert _state(tenant, meeting_id) == before
    assert _stored(tenant, meeting_id) == STORED


def test_a_speaker_only_edit_is_unchanged(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-speaker")
    body = _shown(client, tenant, meeting_id)
    segment = body["segments"][0]
    assert segment["speaker_key"] != "speaker_unknown"
    response = _correct(
        client,
        tenant,
        meeting_id,
        body["revision"]["id"],
        [{"segment_id": segment["id"], "speaker_key": "speaker_unknown"}],
    )
    assert response.status_code == 201, response.text
    assert response.json()["segments"][0]["speaker_key"] == "speaker_unknown"
    assert response.json()["segments"][0]["text"] == SHOWN[0]
    assert _stored(tenant, meeting_id) == STORED


def test_the_action_write_back_is_unchanged(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, "o288-action")
    base = f"/api/v1/meetings/{meeting_id}/actions"
    created = client.post(
        base,
        json={"description": "تماس با [EMAIL_1]2", "priority": "high"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, action_id).description == f"تماس با {EMAIL}2"
    shown = created.json()["description"]
    assert _no_raw(shown), shown
    edited = client.patch(
        f"{base}/{action_id}",
        json={"description": shown + " فوری"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert edited.status_code == 200, edited.text
    assert _no_raw(_dump(edited.json()))
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, action_id).description == f"تماس با {EMAIL}2 فوری"
