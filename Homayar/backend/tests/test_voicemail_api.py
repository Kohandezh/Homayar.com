from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import (
    ActionItem,
    IngestionRecord,
    Meeting,
    Membership,
    ProcessingJob,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    User,
    WorkspaceRole,
)
from backend.routers.v1 import voicemail as voicemail_router
from backend.security.context import CSRF_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_uploads_api import _authenticated_workspace, _valid_metadata
from backend.voicemail.models import VOICEMAIL_SOURCE_TYPE, VoicemailMessage
from backend.voicemail.pipeline import on_transcript_ready

BASE = "/api/v1/voicemails"


@pytest.fixture
def vm_client(client, settings, monkeypatch):
    """The test app with the voicemail router mounted locally (not in main.py yet)."""
    client.app.include_router(voicemail_router.router, prefix=settings.api_prefix)
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    dispatched: list[str] = []
    monkeypatch.setattr(
        "backend.voicemail.ingest.dispatch_job",
        lambda job_id, _settings: dispatched.append(job_id) or True,
    )
    client.dispatched = dispatched
    return client


def headers(client, workspace_id: str, *, write: bool = True) -> dict[str, str]:
    result = {"X-Workspace-ID": workspace_id}
    if write:
        result["X-CSRF-Token"] = client.cookies.get(CSRF_COOKIE)
    return result


def import_vm(
    client,
    workspace_id: str,
    *,
    external_id: str = "msg-0001",
    audio: bytes = b"voicemail wav bytes",
    caller: str = "0912 345 6789",
    received_at: datetime | None = None,
):
    data = {"external_message_id": external_id, "caller_number": caller, "mailbox": "sales"}
    if received_at is not None:
        data["received_at"] = received_at.isoformat()
    return client.post(
        BASE,
        headers=headers(client, workspace_id),
        data=data,
        files={"file": ("vm.wav", audio, "audio/wav")},
    )


def add_transcript(meeting_id: str, texts: list[str], *, revision_number: int = 1) -> str:
    factory = inspection_session_factory()
    with factory() as db:
        meeting = db.get(Meeting, meeting_id)
        for old in db.scalars(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id, TranscriptRevision.is_current.is_(True)
            )
        ):
            old.is_current = False
            old.superseded_at = datetime.now(UTC)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=meeting.workspace_id,
            meeting_id=meeting_id,
            revision_number=revision_number,
            source=TranscriptSource.ASR,
            reason="asr",
            language="fa",
            segment_count=len(texts),
            is_current=True,
        )
        db.add(revision)
        db.flush()
        for index, text in enumerate(texts):
            db.add(
                TranscriptSegment(
                    workspace_id=meeting.workspace_id,
                    meeting_id=meeting_id,
                    revision_id=revision.id,
                    sequence=index,
                    start_ms=index * 1000,
                    end_ms=index * 1000 + 900,
                    text=text,
                )
            )
        db.commit()
        return revision.id


def run_pipeline(meeting_id: str):
    factory = inspection_session_factory()
    with factory() as db:
        outcome = on_transcript_ready(db, meeting_id)
        db.commit()
        return outcome


def test_import_is_idempotent_and_reuses_the_ingestion_path(vm_client, email_outbox) -> None:
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    first = import_vm(vm_client, workspace_id)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["idempotent_replay"] is False
    vm = body["voicemail"]
    assert vm["caller_number"] == "+989123456789" and vm["caller_number_valid"] is True
    assert vm["status"] == "received" and vm["duration_seconds"] == 43
    assert vm["category"] is None and vm["priority"] is None
    assert body["job"]["state"] == "queued"
    assert vm_client.dispatched == [body["job"]["id"]]

    replay = import_vm(vm_client, workspace_id)
    assert replay.status_code == 202, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["voicemail"]["id"] == vm["id"]
    assert len(vm_client.dispatched) == 1

    conflict = import_vm(vm_client, workspace_id, audio=b"different audio bytes")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "ingestion_conflict"

    factory = inspection_session_factory()
    with factory() as db:
        assert db.scalar(select(func.count(VoicemailMessage.id))) == 1
        assert db.scalar(select(func.count(ProcessingJob.id))) == 1
        record = db.scalar(select(IngestionRecord))
        assert record.source_type == VOICEMAIL_SOURCE_TYPE
        assert record.external_event_id == "vm:msg-0001"
        assert record.meeting_id == vm["meeting_id"]
        meeting = db.get(Meeting, vm["meeting_id"])
        assert meeting.source == "voicemail"


def test_import_rejects_invalid_external_id(vm_client, email_outbox) -> None:
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    response = import_vm(vm_client, workspace_id, external_id="bad id with spaces")
    assert response.status_code == 422


def test_list_search_filters_detail_and_follow_up(vm_client, email_outbox) -> None:
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    _widen_concurrency(workspace_id)
    now = datetime.now(UTC)
    a = import_vm(
        vm_client,
        workspace_id,
        external_id="a-1",
        audio=b"aaaa",
        received_at=now - timedelta(hours=2),
    ).json()["voicemail"]
    b = import_vm(
        vm_client,
        workspace_id,
        external_id="b-1",
        audio=b"bbbb",
        caller="+44 20 7946 0000",
        received_at=now - timedelta(hours=1),
    )
    assert b.status_code == 202, b.text
    b = b.json()["voicemail"]
    add_transcript(a["meeting_id"], ["فوری! از خدمات شما ناراضی هستم", "لطفا تماس بگیرید"])
    add_transcript(b["meeting_id"], ["Hi, how much is the premium plan? I'd like a quote"])
    run_pipeline(a["meeting_id"])
    run_pipeline(b["meeting_id"])

    h = headers(vm_client, workspace_id, write=False)
    listed = vm_client.get(BASE, headers=h).json()["items"]
    assert [item["id"] for item in listed] == [b["id"], a["id"]]

    complaints = vm_client.get(BASE, headers=h, params={"category": "complaint"}).json()["items"]
    assert [item["id"] for item in complaints] == [a["id"]]
    assert complaints[0]["priority"] == "urgent"
    sales = vm_client.get(BASE, headers=h, params={"priority": "normal"}).json()["items"]
    assert [item["id"] for item in sales] == [b["id"]]
    assert vm_client.get(BASE, headers=h, params={"status": "received"}).json()["items"] == []

    found = vm_client.get(BASE, headers=h, params={"q": "premium"}).json()["items"]
    assert [item["id"] for item in found] == [b["id"]]
    found_fa = vm_client.get(BASE, headers=h, params={"q": "ناراضی"}).json()["items"]
    assert [item["id"] for item in found_fa] == [a["id"]]
    by_caller = vm_client.get(BASE, headers=h, params={"caller_number": "09123456789"}).json()[
        "items"
    ]
    assert [item["id"] for item in by_caller] == [a["id"]]
    windowed = vm_client.get(
        BASE, headers=h, params={"received_from": (now - timedelta(minutes=90)).isoformat()}
    ).json()["items"]
    assert [item["id"] for item in windowed] == [b["id"]]

    detail = vm_client.get(f"{BASE}/{a['id']}", headers=h)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["transcript_excerpt"].startswith("فوری!")
    assert detail_body["follow_up_state"] == "action_created"
    assert detail_body["classifier_kind"] == "rules"
    assert any(r["code"] == "urgency_terms" for r in detail_body["priority_reasons"])

    done = vm_client.patch(
        f"{BASE}/{a['id']}",
        headers=headers(vm_client, workspace_id),
        json={"follow_up_state": "done"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["follow_up_state"] == "done" and done.json()["follow_up_done_at"]
    factory = inspection_session_factory()
    with factory() as db:
        action = db.get(ActionItem, detail_body["follow_up_action_id"])
        assert action.status.value == "done"

    stats = vm_client.get(f"{BASE}/analytics", headers=h)
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["total"] == 2
    assert body["by_category"] == {"complaint": 1, "sales_inquiry": 1}
    assert body["by_priority"] == {"urgent": 1, "normal": 1}
    assert body["followed_up"] == 1
    assert body["median_follow_up_seconds"] >= 7000
    assert sum(day["count"] for day in body["by_day"]) == 2

    assert (
        vm_client.patch(
            f"{BASE}/{a['id']}", headers=headers(vm_client, workspace_id), json={}
        ).status_code
        == 422
    )


def test_cross_tenant_access_is_denied(vm_client, email_outbox) -> None:
    _, workspace_a = _authenticated_workspace(vm_client, email_outbox)
    vm = import_vm(vm_client, workspace_a).json()["voicemail"]

    other = register_user(vm_client, email_outbox, email="other-tenant@example.com")
    login_user(vm_client, other)
    factory = inspection_session_factory()
    with factory() as db:
        workspace_b = db.scalar(
            select(Membership.workspace_id)
            .join(User, User.id == Membership.user_id)
            .where(User.email == "other-tenant@example.com")
        )
    h_b = headers(vm_client, workspace_b, write=False)
    assert vm_client.get(BASE, headers=h_b).json()["items"] == []
    assert vm_client.get(f"{BASE}/{vm['id']}", headers=h_b).status_code == 404
    assert (
        vm_client.patch(
            f"{BASE}/{vm['id']}",
            headers=headers(vm_client, workspace_b),
            json={"follow_up_state": "done"},
        ).status_code
        == 404
    )
    assert vm_client.get(f"{BASE}/analytics", headers=h_b).json()["total"] == 0
    # Naming the other tenant's workspace is refused outright.
    assert vm_client.get(
        BASE, headers=headers(vm_client, workspace_a, write=False)
    ).status_code in (403, 404)


def test_viewer_cannot_import_or_update(vm_client, email_outbox) -> None:
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    vm = import_vm(vm_client, workspace_id).json()["voicemail"]
    factory = inspection_session_factory()
    with factory() as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id))
        membership.role = WorkspaceRole.VIEWER
        db.commit()

    denied = import_vm(vm_client, workspace_id, external_id="msg-viewer")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    patched = vm_client.patch(
        f"{BASE}/{vm['id']}",
        headers=headers(vm_client, workspace_id),
        json={"follow_up_state": "done"},
    )
    assert patched.status_code == 403
    # Reading stays allowed for a viewer.
    assert (
        vm_client.get(BASE, headers=headers(vm_client, workspace_id, write=False)).status_code
        == 200
    )


def test_write_requires_csrf(vm_client, email_outbox) -> None:
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    response = vm_client.post(
        BASE,
        headers={"X-Workspace-ID": workspace_id},
        data={"external_message_id": "no-csrf"},
        files={"file": ("vm.wav", b"x", "audio/wav")},
    )
    assert response.status_code == 403
