"""WP07-T06: analysis, retrieval, export and shares routed to the permitted
transcript version, with policy provenance (ADR 0024).

Synthetic fixtures only.  Under an enforced workspace policy the provider
must never receive an original value, every read defaults to the redacted
version, originals need ``pii:original_access`` and an explicit request, and
placeholders written back by a person never overwrite the original values.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest
from sqlalchemy import func, select

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ActionItem,
    AuditEvent,
    Meeting,
    MeetingSummary,
    TranscriptRedaction,
    TranscriptSegment,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.processor import process_job
from backend.privacy.redaction_service import policy_digest
from backend.privacy.text_pii import PiiClass, redact
from backend.privacy.transcript_view import MeetingView, RedactedText, choose_view
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRResult, ASRSegment
from backend.services.transcript_service import current_analysis_run, current_revision
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_text_pii import FA, NID

NID_FA = NID.translate(FA)
SEGMENTS = [
    f"سلام، من علی هستم. کد ملی {NID_FA} و موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹ است.",
    "ایمیل ali.rezaei@example.com را بنویسید و دوباره ۰۹۱۲۳۴۵۶۷۸۹ را تکرار می‌کنم.",
]
RAW = ("۰۹۱۲", "0912", "ali.rezaei", "example.com", NID_FA, NID)


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    # One keyed configuration for the API and the processor (mappings retained).
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


class _SegmentedASR(MockASRProvider):
    """Two synthetic segments, so numbering across segments is exercised."""

    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript=" ".join(SEGMENTS), language="fa")

    async def transcribe(self, request):
        return ASRResult(
            provider="mock_asr",
            model="deterministic-v1",
            language="fa",
            text=" ".join(SEGMENTS),
            segments=[
                ASRSegment(index=i, text=text, start_seconds=i * 5, end_seconds=i * 5 + 4)
                for i, text in enumerate(SEGMENTS)
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


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# Generated identifiers, content digests and timestamps are random, so their hex digits or
# microseconds can contain a marker such as "0912" by chance (about 0.7% of runs of
# test_reads_default_to_redacted_and_the_original_needs_the_owner; CI run 38598299491).  They
# cannot carry a transcript value, so they are blanked by their exact shape before the markers
# are checked.  A digest must contain a letter, so a raw digit run (phone, national ID) is never
# blanked.
_GENERATED = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|(?<![0-9A-Za-z])(?=[0-9]*[a-f])[0-9a-f]{24,128}(?![0-9A-Za-z])"
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?"
)


def _no_raw(text: str) -> bool:
    visible = _GENERATED.sub(" ", text)
    return not any(marker in visible for marker in RAW)


_UUID_WITH_MARKER = "5ea8c0d0-9240-4faa-0912-034ab7d71ca5"


def test_raw_check_ignores_markers_inside_generated_values():
    """Regression: a random ID, digest or timestamp that contains "0912" is not a leak."""
    body = {
        "meeting_id": _UUID_WITH_MARKER,
        "digest": "e07352c17058c7ca0912a31590a9daf9d68e16" + "ab" * 13,
        "created_at": "2026-10-17T16:10:53.009123-04:00",
        "text": "کد ملی [IR_NATIONAL_ID_1] و موبایل [PHONE_1] است.",
    }
    text = _dump(body)
    assert "0912" in text  # a plain substring check reports a leak here
    assert _no_raw(text)
    assert _no_raw(repr(body))


@pytest.mark.parametrize("marker", RAW)
@pytest.mark.parametrize(
    "template",
    [
        "{marker}",
        "موبایل {marker} است",
        _UUID_WITH_MARKER + "{marker}",
        "{marker}" + _UUID_WITH_MARKER,
        "id=" + _UUID_WITH_MARKER + " text={marker}",
    ],
)
def test_raw_check_still_reports_every_marker(marker, template):
    leaked = template.format(marker=marker)
    assert not _no_raw(leaked)
    assert not _no_raw(_dump({"meeting_id": _UUID_WITH_MARKER, "text": leaked}))


def test_raw_check_reports_digit_runs_that_look_like_hex():
    # A long run of digits has no hex letter, so it is never blanked as a digest.
    assert not _no_raw("000000000000091234567890000000")
    assert not _no_raw(_dump({"value": "09123456789" * 3}))


def _owner_tenant(owner) -> dict:
    return {"workspace_id": owner.workspace_id, "cookies": owner.cookies, "user_id": owner.user_id}


def _process(client, tenant: dict, settings, label: str) -> tuple[str, _CapturingIntelligence]:
    accepted = _upload(client, tenant, key=f"{label}-0001")
    assert accepted.status_code == 202, accepted.text
    intelligence = _CapturingIntelligence()
    providers = ProviderBundle(
        asr=_SegmentedASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=intelligence,
    )
    result = process_job(
        accepted.json()["job"]["id"], worker_id=label, settings=settings, providers=providers
    )
    assert result["status"] == "completed", result
    return accepted.json()["meeting"]["id"], intelligence


def _set_policy(client, tenant: dict, **payload):
    return client.patch(
        "/api/v1/admin/workspace-settings",
        json=payload,
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )


def _get(client, tenant: dict, path: str, **params):
    return client.get(path, params=params, headers=_headers(tenant), cookies=tenant["cookies"])


# --- analysis input and provenance -----------------------------------------------


def test_the_provider_receives_only_redacted_text_and_provenance_is_recorded(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "route-provider")
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, intelligence = _process(client, tenant, settings, "route-provider")

    (request,) = intelligence.requests
    sent = request.transcript_text + " ".join(turn.text for turn in request.turns)
    assert _no_raw(sent), sent
    assert "[PHONE_1]" in request.turns[0].text and "[PHONE_1]" in request.turns[1].text
    assert "[EMAIL_1]" in request.turns[1].text and "[IR_NATIONAL_ID_1]" in request.turns[0].text

    digest = policy_digest(list(PiiClass))
    with inspection_session_factory()() as db:
        run = current_analysis_run(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        assert run.input_view == "redacted" and run.input_policy_digest == digest
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        redaction = db.scalar(
            select(TranscriptRedaction).where(TranscriptRedaction.revision_id == revision.id)
        )
        # The processor stored the version the analysis input corresponds to.
        assert redaction is not None and redaction.policy_digest == digest
        assert redaction.mapping_retained and redaction.entity_count == 4
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        # The mock summary echoes its input: it holds placeholders only.
        assert _no_raw(summary.overview) and "[PHONE_1]" in summary.overview
        segments = db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.revision_id == revision.id)
            .order_by(TranscriptSegment.sequence)
        ).all()
        assert [s.text for s in segments] == SEGMENTS  # the original stays the original


def test_without_a_policy_the_original_path_is_unchanged(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "route-off")
    meeting_id, intelligence = _process(client, tenant, settings, "route-off")
    assert "ali.rezaei@example.com" in intelligence.requests[0].turns[1].text
    with inspection_session_factory()() as db:
        run = current_analysis_run(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        assert run.input_view == "original" and run.input_policy_digest is None
        assert db.scalar(select(TranscriptRedaction.id)) is None

    transcript = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/transcript")
    assert transcript.status_code == 200, transcript.text
    body = transcript.json()
    assert body["view"] == "original" and body["privacy_enforced"] is False
    assert body["redaction"] is None and body["segments"][1]["text"] == SEGMENTS[1]
    # A redacted view is available on request without a policy.
    redacted = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/transcript", view="redacted")
    assert redacted.status_code == 200 and redacted.json()["view"] == "redacted"
    assert _no_raw(_dump(redacted.json()["segments"]))


# --- reads, permissions and audits --------------------------------------------------


def test_reads_default_to_redacted_and_the_original_needs_the_owner(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin", "member": "member"}
    )
    tenant = _owner_tenant(owner)
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "route-reads")
    base = f"/api/v1/meetings/{meeting_id}"

    for name, persona in {"owner": owner, **personas}.items():
        transcript = persona.get(f"{base}/transcript")
        assert transcript.status_code == 200, (name, transcript.text)
        body = transcript.json()
        assert body["view"] == "redacted" and body["privacy_enforced"] is True, name
        assert body["redaction"]["id"] is not None and body["redaction"]["entity_count"] == 4
        assert _no_raw(_dump(body)), name
        assert body["segments"][0]["text"].count("[PHONE_1]") == 1
        for path in ("summary", "evidence", "actions"):
            response = persona.get(f"{base}/{path}")
            assert response.status_code == 200, (name, path, response.text)
            assert _no_raw(_dump(response.json())), (name, path)
        exported = persona.get(f"{base}/export", params={"format": "json"})
        assert exported.status_code == 200, exported.text
        payload = exported.json()
        assert _no_raw(exported.content.decode()), name
        privacy = payload["privacy"]
        assert privacy["view"] == "redacted" and privacy["policy_enforced"] is True
        assert privacy["redaction_id"] == body["redaction"]["id"]
        assert privacy["analysis_input_view"] == "redacted"
        assert privacy["classes"] == sorted(c.value for c in PiiClass)
        assert "not anonymisation" in privacy["notice"]
        for fmt in ("txt", "srt", "vtt"):
            rendered = persona.get(f"{base}/export", params={"format": fmt})
            assert rendered.status_code == 200 and _no_raw(rendered.content.decode()), fmt

    # Evidence offsets point into the redacted text.
    evidence = owner.get(f"{base}/evidence").json()["items"]
    assert evidence  # the mock summary cites every turn
    segments = {s["id"]: s["text"] for s in owner.get(f"{base}/transcript").json()["segments"]}
    for item in evidence:
        assert segments[item["segment_id"]][item["text_start"] : item["text_end"]] == item["text"]

    # The original: owner only, explicit, audited; admins, members and keys are refused.
    for name in ("admin", "member"):
        for path in ("transcript", "summary", "evidence", "actions"):
            denied = personas[name].get(f"{base}/{path}", params={"view": "original"})
            assert denied.status_code == 403, (name, path)
            assert denied.json()["error"]["code"] == "original_access_denied"
        denied = personas[name].get(f"{base}/export", params={"format": "json", "view": "original"})
        assert denied.status_code == 403
    original = owner.get(f"{base}/transcript", params={"view": "original"})
    assert original.status_code == 200 and original.json()["view"] == "original"
    assert [s["text"] for s in original.json()["segments"]] == SEGMENTS
    exported = owner.get(f"{base}/export", params={"format": "json", "view": "original"})
    assert exported.status_code == 200 and "ali.rezaei@example.com" in exported.content.decode()
    assert exported.json()["privacy"]["view"] == "original"

    key = _mint(client, owner.headers, owner.cookies, scopes=("meeting:read",))
    assert key.status_code == 201, key.text
    raw_key = key.json()["key"]
    by_key = client.get(f"{base}/transcript", headers=_bearer(raw_key))
    assert by_key.status_code == 200 and by_key.json()["view"] == "redacted"
    assert _no_raw(_dump(by_key.json()))
    refused = client.get(
        f"{base}/transcript", params={"view": "original"}, headers=_bearer(raw_key)
    )
    assert refused.status_code == 403

    with inspection_session_factory()() as db:
        audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type.in_(["transcript.original_accessed", "meeting.exported"]),
                    AuditEvent.workspace_id == owner.workspace_id,
                )
            )
        )
    original_reads = [a for a in audits if a.event_type == "transcript.original_accessed"]
    assert {a.details["surface"] for a in original_reads} == {"transcript", "export"}
    assert all(a.actor_user_id == owner.user_id for a in original_reads)
    exports = [a for a in audits if a.event_type == "meeting.exported"]
    assert {a.details["view"] for a in exports} == {"redacted", "original"}
    assert all(_no_raw(repr(a.details)) for a in audits)


def test_search_is_not_an_oracle_for_redacted_values(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "route-search")
    # Processed without a policy: transcript and summary hold the originals.
    meeting_id, _ = _process(client, tenant, settings, "route-search")

    def search(q: str):
        response = _get(client, tenant, "/api/v1/search", q=q)
        assert response.status_code == 200, response.text
        return response.json()["items"]

    assert [item["id"] for item in search("ali.rezaei")] == [meeting_id]
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    for value in ("ali.rezaei", "example.com", "۰۹۱۲۳۴۵۶۷۸۹", NID_FA, "۳۴۵ ۶۷۸۹"):
        assert search(value) == [], value
    found = search("تکرار")
    assert [item["id"] for item in found] == [meeting_id]
    assert _no_raw(_dump(found))
    assert {match["kind"] for match in found[0]["matches"]} >= {"transcript", "summary"}
    # A summary written from the originals before the policy is redacted on read.
    summary = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/summary").json()
    assert _no_raw(summary["overview"]) and "[EMAIL_1]" in summary["overview"]


def test_shares_never_show_originals_under_the_policy(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "route-share")
    assert (
        _set_policy(
            client, tenant, pii_redaction_enabled=True, public_share_links_enabled=True
        ).status_code
        == 200
    )
    meeting_id, _ = _process(client, tenant, settings, "route-share")
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
    assert body["transcript"]["view"] == "redacted" and _no_raw(_dump(body))
    # The share session cannot write: the version is computed, not stored.
    assert body["transcript"]["redaction"]["id"] is None
    assert body["transcript"]["redaction"]["entity_count"] == 4
    exported = client.get(f"/api/v1/shares/{token}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    assert _no_raw(exported.content.decode())
    assert exported.json()["privacy"]["view"] == "redacted"
    txt = client.get(f"/api/v1/shares/{token}/export", params={"format": "txt"})
    assert txt.status_code == 200 and _no_raw(txt.content.decode())
    assert "not anonymisation" in txt.content.decode()


# --- placeholders written back ------------------------------------------------------


def test_placeholders_written_back_keep_the_originals(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "route-write")
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "route-write")
    base = f"/api/v1/meetings/{meeting_id}"
    shown = _get(client, tenant, f"{base}/transcript").json()
    first = shown["segments"][0]
    assert "[PHONE_1]" in first["text"]

    corrected = client.post(
        f"{base}/transcript/corrections",
        json={
            "base_revision_id": shown["revision"]["id"],
            "reason": "confirm the number",
            "edits": [{"segment_id": first["id"], "text": first["text"] + " (تأیید شد)"}],
        },
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["view"] == "redacted" and _no_raw(_dump(corrected.json()))
    assert corrected.json()["segments"][0]["text"].endswith("(تأیید شد)")
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        stored = db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.revision_id == revision.id)
            .order_by(TranscriptSegment.sequence)
        ).all()
        assert stored[0].text == SEGMENTS[0] + " (تأیید شد)"
        assert "[PHONE_1]" not in stored[0].text and stored[1].text == SEGMENTS[1]
        # The new revision got its redacted version at once.
        assert db.scalar(
            select(TranscriptRedaction.id).where(TranscriptRedaction.revision_id == revision.id)
        )

    created = client.post(
        f"{base}/actions",
        json={"description": "با [PHONE_1] و [EMAIL_1] تماس بگیرید", "priority": "high"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "با [PHONE_1] و [EMAIL_1] تماس بگیرید"
    action_id = created.json()["id"]
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert "ali.rezaei@example.com" in action.description and "[" not in action.description
    listed = _get(client, tenant, f"{base}/actions").json()
    edited = client.patch(
        f"{base}/actions/{action_id}",
        json={"description": listed[0]["description"] + " فوری"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert edited.status_code == 200, edited.text
    assert _no_raw(_dump(edited.json()))
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert action.description.endswith(" فوری") and "[" not in action.description
        assert "ali.rezaei@example.com" in action.description


def test_offsets_move_to_whole_placeholders():
    text = "call ۰۹۱۲ ۳۴۵ ۶۷۸۹ now"
    redacted, replacements = redact(text, [PiiClass.PHONE])
    item = RedactedText(redacted, tuple(replacements))
    assert redacted == "call [PHONE_1] now"
    value_start = text.index("۰۹۱۲")
    value_end = value_start + len("۰۹۱۲ ۳۴۵ ۶۷۸۹")
    # Outside the value: shifted; inside: widened to the whole placeholder.
    assert item.offset(0, end=False) == 0
    assert item.offset(value_start, end=False) == 5
    assert item.offset(value_start + 3, end=False) == 5
    assert item.offset(value_start + 3, end=True) == 5 + len("[PHONE_1]")
    assert item.offset(value_end, end=True) == 5 + len("[PHONE_1]")
    assert item.offset(len(text), end=True) == len(redacted)
    cut = redacted[item.offset(2, end=False) : item.offset(value_start + 4, end=True)]
    assert cut == "ll [PHONE_1]"


# --- reveal and policy changes --------------------------------------------------------


def test_reveal_is_owner_only_reasoned_and_audited(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin", "member": "member"}
    )
    tenant = _owner_tenant(owner)
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "route-reveal")
    redaction_id = owner.get(f"/api/v1/meetings/{meeting_id}/transcript").json()["redaction"]["id"]
    path = f"/api/v1/meetings/{meeting_id}/transcript/redactions/{redaction_id}/reveal"

    for name in ("admin", "member"):
        denied = personas[name].post(path, json={"placeholder": "[PHONE_1]", "reason": "callback"})
        assert denied.status_code == 403, name
    revealed = owner.post(path, json={"placeholder": "[EMAIL_1]", "reason": "customer callback"})
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["value"] == "ali.rezaei@example.com"
    assert owner.post(path, json={"placeholder": "[PHONE_9]", "reason": "x y z"}).status_code == 404
    assert owner.post(path, json={"placeholder": "0912", "reason": "abc"}).status_code == 422
    assert owner.post(path, json={"placeholder": "[PHONE_1]", "reason": " "}).status_code == 422
    other = f"/api/v1/meetings/{meeting_id}/transcript/redactions/{'0' * 36}/reveal"
    assert owner.post(other, json={"placeholder": "[PHONE_1]", "reason": "abc"}).status_code == 404
    with inspection_session_factory()() as db:
        audits = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "pii.revealed")))
    assert len(audits) == 1 and audits[0].actor_user_id == owner.user_id
    assert audits[0].details["placeholder"] == "[EMAIL_1]"
    assert audits[0].details["reason"] == "customer callback"
    assert _no_raw(repr(audits[0].details))


def test_changing_the_policy_needs_original_access(client, email_outbox, monkeypatch):
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin"}
    )
    admin = personas["admin"]
    for payload in ({"pii_redaction_enabled": True}, {"pii_redaction_classes": ["email"]}):
        denied = admin.patch("/api/v1/admin/workspace-settings", json=payload)
        assert denied.status_code == 403, denied.text
    allowed = admin.patch(
        "/api/v1/admin/workspace-settings", json={"public_share_links_enabled": True}
    )
    assert allowed.status_code == 200, allowed.text
    enabled = owner.patch("/api/v1/admin/workspace-settings", json={"pii_redaction_enabled": True})
    assert enabled.status_code == 200 and enabled.json()["pii_redaction_enabled"] is True


@pytest.mark.postgres
def test_concurrent_first_reads_create_one_redacted_version(
    client, email_outbox, monkeypatch, settings
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("unique-constraint races are PostgreSQL evidence")
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "route-race")
    meeting_id, _ = _process(client, tenant, settings, "route-race")  # no version yet
    workspace_id = tenant["workspace_id"]
    barrier = threading.Barrier(2, timeout=15)
    ids: list[str] = []
    errors: list[str] = []
    factory = get_session_factory()

    def first_read(name: str) -> None:
        try:
            with factory() as db:
                bind_tenant_context(db, workspace_id=workspace_id)
                workspace = db.get(Workspace, workspace_id)
                meeting = db.get(Meeting, meeting_id)
                revision = current_revision(db, workspace_id=workspace_id, meeting_id=meeting_id)
                view = MeetingView(
                    db,
                    meeting=meeting,
                    policy=choose_view(workspace, requested="redacted", may_read_original=False),
                    settings=settings,
                    now=datetime.now(UTC),
                    persist=True,
                )
                barrier.wait()
                ids.append(view.revision(revision).record.id)
                db.commit()
        except Exception as exc:  # noqa: BLE001 - surfaced through the assertion below
            errors.append(f"{name}:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=first_read, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert len(ids) == 2 and len(set(ids)) == 1
    with inspection_session_factory()() as db:
        count = db.scalar(
            select(func.count(TranscriptRedaction.id)).where(
                TranscriptRedaction.meeting_id == meeting_id
            )
        )
    assert count == 1
