from __future__ import annotations

from sqlalchemy import func, select

from backend.database.saas_models import (
    Entitlement,
    Meeting,
    Membership,
    ProcessingJob,
    Recording,
    User,
    Workspace,
)
from backend.media import AudioStreamMetadata, MalformedMediaError, MediaMetadata
from backend.security.context import CSRF_COOKIE
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


def _authenticated_workspace(client, email_outbox, *, verify: bool = True):
    credentials = register_user(
        client,
        email_outbox,
        email=f"upload-{len(email_outbox)}@example.com",
        verify=verify,
    )
    login_user(client, credentials)
    factory = inspection_session_factory()
    with factory() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        assert user is not None
        workspace = db.scalar(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id)
        )
        assert workspace is not None
        return credentials, workspace.id


def _valid_metadata(*, duration_seconds: float = 300.25) -> MediaMetadata:
    return MediaMetadata(
        original_filename="customer-call.wav",
        extension=".wav",
        container="wav",
        detected_format_names=("wav",),
        mime_type="audio/wav",
        duration_seconds=duration_seconds,
        size_bytes=16,
        audio_streams=(
            AudioStreamMetadata(
                index=0,
                codec="pcm_s16le",
                channels=1,
                sample_rate_hz=16_000,
            ),
        ),
        video_streams=(),
    )


def _upload(
    client,
    *,
    workspace_id: str,
    key: str,
    include_csrf: bool = True,
):
    headers = {
        "X-Workspace-ID": workspace_id,
        "Idempotency-Key": key,
    }
    if include_csrf:
        headers["X-CSRF-Token"] = client.cookies.get(CSRF_COOKIE)
    return client.post(
        "/api/v1/uploads",
        headers=headers,
        data={"title": "Weekly customer call", "language": "fa"},
        files={"file": ("customer-call.wav", b"staged wav bytes", "audio/wav")},
    )


def test_upload_is_durable_private_and_idempotent(
    client,
    email_outbox,
    settings,
    monkeypatch,
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(),
    )
    queued: list[str] = []
    monkeypatch.setattr(
        "backend.routers.v1.meetings.dispatch_job",
        lambda job_id, _settings: queued.append(job_id) or True,
    )

    first = _upload(client, workspace_id=workspace_id, key="upload-key-0001")
    assert first.status_code == 202, first.text
    payload = first.json()
    assert payload["idempotent_replay"] is False
    assert payload["meeting"]["duration_seconds"] == 301
    assert payload["meeting"]["status"] == "queued"
    assert payload["usage"]["reserved"] == 301
    assert len(queued) == 1

    replay = _upload(client, workspace_id=workspace_id, key="upload-key-0001")
    assert replay.status_code == 202, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job"]["id"] == payload["job"]["id"]
    assert len(queued) == 1

    factory = inspection_session_factory()
    with factory() as db:
        assert db.scalar(select(func.count(Meeting.id))) == 1
        assert db.scalar(select(func.count(Recording.id))) == 1
        assert db.scalar(select(func.count(ProcessingJob.id))) == 1
        recording = db.scalar(select(Recording))
        assert recording is not None
        assert create_object_storage(settings).exists(workspace_id, recording.storage_key)


def test_upload_enforces_csrf_verification_media_and_idempotency(
    client,
    email_outbox,
    monkeypatch,
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(),
    )

    no_csrf = _upload(
        client,
        workspace_id=workspace_id,
        key="upload-key-csrf",
        include_csrf=False,
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_failed"

    invalid_key = _upload(client, workspace_id=workspace_id, key="short")
    assert invalid_key.status_code == 400
    assert invalid_key.json()["error"]["code"] == "invalid_idempotency_key"

    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: (_ for _ in ()).throw(MalformedMediaError()),
    )
    malformed = _upload(client, workspace_id=workspace_id, key="upload-key-bad-media")
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "malformed_media"

    _, unverified_workspace_id = _authenticated_workspace(
        client,
        email_outbox,
        verify=False,
    )
    unverified = _upload(
        client,
        workspace_id=unverified_workspace_id,
        key="upload-key-unverified",
    )
    assert unverified.status_code == 403
    assert unverified.json()["error"]["code"] == "verified_identity_required"


def test_upload_reservations_enforce_concurrency_and_audio_quota(
    client,
    email_outbox,
    monkeypatch,
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.dispatch_job",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=300),
    )

    first = _upload(client, workspace_id=workspace_id, key="upload-key-concurrency-1")
    assert first.status_code == 202, first.text
    second = _upload(client, workspace_id=workspace_id, key="upload-key-concurrency-2")
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "concurrent_job_limit_reached"

    factory = inspection_session_factory()
    with factory() as db:
        concurrency = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "concurrent_jobs",
            )
        )
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        assert concurrency is not None and audio is not None
        concurrency.limit_value = 2
        audio.limit_value = 500
        db.commit()

    third = _upload(client, workspace_id=workspace_id, key="upload-key-audio-quota")
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "audio_quota_exceeded"

    with factory() as db:
        assert db.scalar(select(func.count(Meeting.id))) == 1
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        assert audio is not None
        assert (audio.used_value, audio.reserved_value) == (0, 300)
