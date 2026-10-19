"""WP07-T07: retention under a controllable clock with held records, failed
storage deletion, mixed-language identifiers and the subject-scope export
(ADR 0025).

One workspace lives through a whole retention cycle: conversations that name
the same person in Persian, ASCII (+98) and Arabic-Indic spellings, a legal
hold, a correction, an object store that fails and recovers, and a release.
The subject export is taken at every step; it must follow exactly what normal
access may still see.  Synthetic fixtures only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    AuditEvent,
    MediaLifecycleState,
    PurgeState,
    RetentionMode,
)
from backend.jobs.leases import as_utc
from backend.jobs.processor import process_job
from backend.privacy.subject_export import (
    SubjectIdentifierError,
    parse_subject_identifier,
)
from backend.privacy.text_pii import PiiClass
from backend.providers.mocks import MockASRProvider
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import (
    CONTENT,
    _get,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)
from backend.tests.test_job_processor import _providers
from backend.tests.test_retention_purge import (
    FlakyStorage,
    _content_rows,
    _expiry,
    _media,
    _meeting,
    _purge,
    _review,
    _set_policy,
    _sweep,
)

SUBJECT_PHONE = "+98 912 345 6789"
SUBJECT_EMAIL = "ali.rezaei@example.com"
TRANSCRIPTS = {
    "persian": "لطفاً با شماره ۰۹۱۲ ۳۴۵ ۶۷۸۹ تماس بگیرید و پرونده را پیگیری کنید.",
    "english": "Call me at +98 912 345 6789 or write to Ali.Rezaei@Example.com tomorrow.",
    "arabic": "رقم الهاتف ٠٩١٢-٣٤٥-٦٧٨٩ للتواصل بعد الاجتماع.",
    "other": "شماره دیگری ۰۹۳۵۱۲۳۴۵۶۷ برای هماهنگی ثبت شد.",
}


def _processed(client, tenant: dict, settings, name: str, index: int) -> str:
    accepted = _upload(
        client,
        tenant,
        key=f"lifecycle-{name}",
        content=CONTENT + bytes([index + 7]) * (index + 3),
        title=f"Call {name}",
    )
    assert accepted.status_code == 202, accepted.text
    providers = _providers(
        MockASRProvider(allow_mock=True, transcript=TRANSCRIPTS[name], language="fa")
    )
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id=f"lifecycle-{name}",
        settings=settings,
        providers=providers,
    )
    assert result["status"] == "completed", result
    return accepted.json()["meeting"]["id"]


def _subject_export(client, tenant: dict, identifiers: list[str], reason: str = "DSAR 2026-41"):
    return client.post(
        "/api/v1/privacy/subject-exports",
        json={"identifiers": identifiers, "reason": reason},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )


def _matched(client, tenant: dict, identifiers: list[str] | None = None) -> dict[str, dict]:
    response = _subject_export(client, tenant, identifiers or [SUBJECT_PHONE, SUBJECT_EMAIL])
    assert response.status_code == 200, response.text
    return {match["meeting_id"]: match for match in response.json()["matches"]}


# --- identifiers ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["+98 912 345 6789", "۰۹۱۲۳۴۵۶۷۸۹", "٠٩١٢-٣٤٥-٦٧٨٩", "0098 912 3456789", "+989123456789"],
)
def test_every_spelling_of_one_phone_is_one_subject(raw):
    parsed = parse_subject_identifier(raw)
    assert parsed.pii_class is PiiClass.PHONE and parsed.value == "09123456789"


@pytest.mark.parametrize(
    "raw",
    ["Ali Rezaei", "0912", "call +98 912 345 6789", "a@b.co and c@d.co", "کد پستی ۸۱۳۴۵۶۷۸۹۰"],
)
def test_only_one_supported_identifier_is_accepted(raw):
    with pytest.raises(SubjectIdentifierError):
        parse_subject_identifier(raw)


def test_the_subject_export_is_owner_only_and_never_through_a_key(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin", "member": "member"}
    )
    body = {"identifiers": [SUBJECT_PHONE], "reason": "DSAR 2026-40"}
    for name in ("admin", "member"):
        denied = personas[name].post("/api/v1/privacy/subject-exports", json=body)
        assert denied.status_code == 403, name
    key = _mint(client, owner.headers, owner.cookies, scopes=("meeting:read",))
    by_key = client.post(
        "/api/v1/privacy/subject-exports", json=body, headers=_bearer(key.json()["key"])
    )
    assert by_key.status_code == 403
    assert owner.post("/api/v1/privacy/subject-exports", json=body).status_code == 200
    invalid = owner.post(
        "/api/v1/privacy/subject-exports", json={"identifiers": ["Ali"], "reason": "abc"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_subject_identifier"
    no_reason = owner.post(
        "/api/v1/privacy/subject-exports", json={"identifiers": [SUBJECT_PHONE], "reason": " "}
    )
    assert no_reason.status_code == 422


# --- one retention cycle ------------------------------------------------------------


def test_a_retention_cycle_with_a_hold_failing_storage_and_subject_exports(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "lifecycle")
    workspace_id = tenant["workspace_id"]
    _set_policy(workspace_id, RetentionMode.FINITE, 30)
    ids = {
        name: _processed(client, tenant, settings, name, index)
        for index, name in enumerate(TRANSCRIPTS)
    }
    persian, english, arabic, other = (ids[n] for n in ("persian", "english", "arabic", "other"))

    # 1. Mixed-language identifiers: every spelling is found, the other caller is not.
    matched = _matched(client, tenant)
    assert set(matched) == {persian, english, arabic}
    english_segment = matched[english]["revisions"][0]["segments"][0]
    assert english_segment["matched"] == ["email", "phone"]
    assert english_segment["text"] == TRANSCRIPTS["english"]  # the original, for the subject
    assert matched[arabic]["revisions"][0]["segments"][0]["matched"] == ["phone"]
    assert matched[persian]["summary"]  # the analysis text names the subject too
    assert set(_matched(client, tenant, ["۰۹۱۲۳۴۵۶۷۸۹"])) == {persian, english, arabic}
    assert set(_matched(client, tenant, ["ALI.REZAEI@example.com"])) == {english}
    document = _subject_export(client, tenant, [SUBJECT_PHONE]).json()
    assert document["scope"]["meetings_scanned"] == 4 and document["scope"]["truncated"] is False
    assert document["subject"]["identifiers"] == [{"class": "phone", "value": "09123456789"}]

    # 2. A correction removes the number: the older revision still holds it.
    shown = _get(client, tenant, f"/api/v1/meetings/{persian}/transcript").json()
    corrected = client.post(
        f"/api/v1/meetings/{persian}/transcript/corrections",
        json={
            "base_revision_id": shown["revision"]["id"],
            "reason": "remove the phone number",
            "edits": [
                {"segment_id": shown["segments"][0]["id"], "text": "لطفاً پرونده را پیگیری کنید."}
            ],
        },
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert corrected.status_code == 201, corrected.text
    revisions = _matched(client, tenant)[persian]["revisions"]
    assert [r["is_current"] for r in revisions] == [False]

    # 3. A legal hold on the Arabic-script conversation.
    held = client.post(
        f"/api/v1/meetings/{arabic}/legal-hold",
        json={"reason": "complaint 2026-9", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    # 4. The clock: one second before the earliest expiry nothing happens.
    expiries = {name: _expiry(meeting_id) for name, meeting_id in ids.items()}
    before = _sweep(settings, workspace_id, now=min(expiries.values()) - timedelta(seconds=1))
    assert before["expired"] == 0
    assert set(_matched(client, tenant)) == {persian, english, arabic}

    # 5. Past every expiry with a failing object store: the unheld conversations
    # are denied at once and their purges stay retryable; the held one stays.
    real = create_object_storage(settings)
    flaky = FlakyStorage(real, code="s3_unavailable", failures=10_000)
    late = max(expiries.values()) + timedelta(seconds=1)
    expired = _sweep(settings, workspace_id, now=late, storage=flaky)
    assert expired["expired"] == 3 and expired["purges_completed"] == 0, expired
    for meeting_id in (persian, english, other):
        assert _meeting(meeting_id).deleted_at is not None
        assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 404
        purge = _purge(meeting_id)
        assert purge is not None and purge.state is PurgeState.PENDING
        assert purge.objects_pending > 0
        assert all(real.exists(workspace_id, m.storage_key) for m in _media(meeting_id))
    assert _meeting(arabic).deleted_at is None
    assert _get(client, tenant, f"/api/v1/meetings/{arabic}/transcript").status_code == 200
    # The subject export follows normal access: tombstoned conversations are out.
    assert set(_matched(client, tenant)) == {arabic}

    # 6. Storage recovers: the next due sweep completes every purge.
    flaky.failures = 0
    retry_at = max(as_utc(_purge(m).next_attempt_at) for m in (persian, english, other))
    done = _sweep(settings, workspace_id, now=retry_at + timedelta(seconds=1), storage=flaky)
    assert done["purges_completed"] == 3, done
    for meeting_id in (persian, english, other):
        assert _purge(meeting_id).state is PurgeState.COMPLETED
        assert _content_rows(meeting_id) == 0
        assert all(m.lifecycle_state is MediaLifecycleState.DELETED for m in _media(meeting_id))
    assert _content_rows(arabic) > 0
    assert all(real.exists(workspace_id, m.storage_key) for m in _media(arabic))

    # 7. Release: the held conversation expires at the next sweep and the
    # subject is no longer found anywhere.
    for media in _media(arabic):
        released = client.post(
            f"/api/v1/media/{media.id}/legal-hold/release",
            json={"reason": "complaint closed"},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert released.status_code == 200, released.text
    final = _sweep(settings, workspace_id, now=datetime.now(UTC) + timedelta(days=31))
    assert final["expired"] == 1 and final["purges_completed"] == 1, final
    assert _content_rows(arabic) == 0
    assert _matched(client, tenant) == {}

    # Every subject export was audited with counts and classes, never an identifier.
    with inspection_session_factory()() as db:
        audits = list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type == "subject.exported",
                    AuditEvent.workspace_id == workspace_id,
                )
                .order_by(AuditEvent.occurred_at)
            )
        )
    assert len(audits) == 8  # one per export taken above
    assert audits[0].details["meetings_matched"] == 3
    assert audits[0].details["identifier_classes"] == "email,phone"
    assert audits[-1].details["meetings_matched"] == 0
    for audit in audits:
        blob = json.dumps(audit.details, ensure_ascii=False)
        assert "912" not in blob and "٩١٢" not in blob and "۹۱۲" not in blob
        assert "rezaei" not in blob.lower() and audit.details["reason"] == "DSAR 2026-41"
