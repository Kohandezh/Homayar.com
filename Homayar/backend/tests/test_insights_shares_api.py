from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select

from backend.database.saas_models import ProcessingJob, ShareLink
from backend.jobs.processor import process_job
from backend.security.context import CSRF_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_job_processor import _providers
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata


def _processed_meeting(client, email_outbox, settings, monkeypatch):
    credentials, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=65.2),
    )
    monkeypatch.setattr(
        "backend.routers.v1.meetings.dispatch_job",
        lambda *_args, **_kwargs: True,
    )
    upload = _upload(client, workspace_id=workspace_id, key="insights-upload-key")
    assert upload.status_code == 202, upload.text
    payload = upload.json()
    result = process_job(
        payload["job"]["id"],
        worker_id="test-insights-worker",
        settings=settings,
        providers=_providers(),
    )
    assert result["status"] == "completed"
    return credentials, workspace_id, payload["meeting"]["id"]


def _write_headers(client, workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-ID": workspace_id,
        "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
    }


def test_transcript_actions_search_and_exports_are_tenant_scoped(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    _, workspace_id, meeting_id = _processed_meeting(
        client,
        email_outbox,
        settings,
        monkeypatch,
    )
    read_headers = {"X-Workspace-ID": workspace_id}

    transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers=read_headers,
    )
    assert transcript.status_code == 200, transcript.text
    transcript_payload = transcript.json()
    assert transcript_payload["segments"][0]["text"] == "پیگیری قرارداد انجام شود"
    segment_id = transcript_payload["segments"][0]["id"]
    speaker_id = transcript_payload["speakers"][0]["id"]

    rename = client.patch(
        f"/api/v1/meetings/{meeting_id}/speakers/{speaker_id}",
        headers=_write_headers(client, workspace_id),
        json={"display_name": "مدیر فروش"},
    )
    assert rename.status_code == 200, rename.text
    renamed_transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers=read_headers,
    )
    assert renamed_transcript.json()["segments"][0]["speaker_name"] == "مدیر فروش"

    created = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={
            "description": "ارسال نسخه نهایی قرارداد",
            "assignee_text": "تیم فروش",
            "priority": "high",
            "source_segment_id": segment_id,
        },
    )
    assert created.status_code == 201, created.text
    action = created.json()
    assert action["source_timestamp_ms"] == 0
    updated = client.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{action['id']}",
        headers=_write_headers(client, workspace_id),
        json={"status": "done", "priority": "low"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "done"

    search = client.get(
        "/api/v1/search",
        headers=read_headers,
        params={"q": "قرارداد"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["items"][0]["id"] == meeting_id
    assert {match["kind"] for match in search.json()["items"][0]["matches"]} >= {
        "transcript",
        "action",
    }

    json_export = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers=read_headers,
        params={"format": "json"},
    )
    assert json_export.status_code == 200, json_export.text
    assert json_export.json()["meeting_id"] == meeting_id
    assert json_export.json()["segments"][0]["speaker"] == "مدیر فروش"
    for format_name in ("txt", "srt", "vtt"):
        exported = client.get(
            f"/api/v1/meetings/{meeting_id}/export",
            headers=read_headers,
            params={"format": format_name},
        )
        assert exported.status_code == 200, exported.text
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.content

    outsider = register_user(
        client,
        email_outbox,
        email="insights-outsider@example.com",
    )
    outsider_session = login_user(client, outsider)
    outsider_workspace_id = outsider_session["workspaces"][0]["id"]
    hidden = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers={"X-Workspace-ID": outsider_workspace_id},
    )
    assert hidden.status_code == 404
    isolated_search = client.get(
        "/api/v1/search",
        headers={"X-Workspace-ID": outsider_workspace_id},
        params={"q": "قرارداد"},
    )
    assert isolated_search.status_code == 200
    assert isolated_search.json()["items"] == []


def test_share_links_are_hashed_expiring_auth_optional_and_revocable(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client,
        email_outbox,
        settings,
        monkeypatch,
    )
    # Anonymous links are an explicit workspace opt-in (WP03-T06).
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, workspace_id),
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    public_share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": False},
    )
    assert public_share.status_code == 201, public_share.text
    assert public_share.json()["audience"] == "link"
    public_payload = public_share.json()
    public_path = urlparse(public_payload["url"]).path
    raw_token = public_path.rsplit("/", 1)[-1]
    public_api_path = f"/api/v1/shares/{raw_token}"
    assert public_path == f"/app/share/{raw_token}"

    factory = inspection_session_factory()
    with factory() as db:
        share = db.get(ShareLink, public_payload["id"])
        assert share is not None
        assert share.token_hash != raw_token
        assert len(share.token_hash) == 64

    client.cookies.clear()
    public_access = client.get(public_api_path)
    assert public_access.status_code == 200, public_access.text
    assert public_access.json()["meeting_id"] == meeting_id
    assert public_access.json()["transcript"]["segments"]

    login_user(client, credentials)
    protected_share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": True},
    )
    assert protected_share.status_code == 201, protected_share.text
    protected_path = urlparse(protected_share.json()["url"]).path
    protected_token = protected_path.rsplit("/", 1)[-1]
    protected_api_path = f"/api/v1/shares/{protected_token}"
    client.cookies.clear()
    authentication_required = client.get(protected_api_path)
    assert authentication_required.status_code == 401
    assert (
        authentication_required.json()["error"]["code"]
        == "share_authentication_required"
    )

    login_user(client, credentials)
    authenticated_access = client.get(protected_api_path)
    assert authenticated_access.status_code == 200
    revoked = client.delete(
        f"/api/v1/meetings/{meeting_id}/shares/{protected_share.json()['id']}",
        headers=_write_headers(client, workspace_id),
    )
    assert revoked.status_code == 204
    assert client.get(protected_api_path).status_code == 404

    with factory() as db:
        job = db.scalar(select(ProcessingJob))
        assert job is not None and job.state.value == "completed"
