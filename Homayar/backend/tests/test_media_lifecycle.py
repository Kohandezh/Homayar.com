"""WP05-T03: original and derived media objects with their private lifecycle (ADR 0017).

The ORIGINAL upload is registered from the digest-verified stored object and
linked to the recording, the ledger row and the root job; derived kinds must
name their source; content columns are immutable; deletion is state first and
bytes second; a legal hold keeps the bytes; integrity is verified by
re-reading them; reads are tenant-scoped and never expose a location.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.database.saas_models import (
    MediaImmutableError,
    MediaIntegrity,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    Recording,
    Workspace,
)
from backend.jobs.processor import process_job
from backend.services.media_lifecycle_service import (
    MediaLifecycleError,
    delete_objects,
    place_legal_hold,
    register_derived,
    release_legal_hold,
    request_deletion,
    verify_integrity,
)
from backend.storage import create_object_storage
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    CONTENT,
    OTHER_CONTENT,
    REPO_ROOT,
    _audits,
    _get,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)
from backend.tests.test_job_processor import _providers

probe_role = _rls.probe_role


def _media_rows(meeting_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(MediaObject)
                .where(MediaObject.meeting_id == meeting_id)
                .order_by(MediaObject.created_at.asc())
            )
        )


def test_upload_registers_the_original_and_reads_are_tenant_scoped(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "media-one")
    other = _tenant(client, email_outbox, "media-two")
    accepted = _upload(client, tenant, key="media-event-1")
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    meeting_id, job_id = body["meeting"]["id"], body["job"]["id"]

    rows = _media_rows(meeting_id)
    assert len(rows) == 1
    original = rows[0]
    assert original.kind is MediaKind.ORIGINAL
    assert original.lifecycle_state is MediaLifecycleState.ACTIVE
    assert original.integrity is MediaIntegrity.UNVERIFIED
    assert original.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert original.size_bytes == len(CONTENT)
    assert original.content_type == "audio/wav" and original.container_format == "wav"
    assert original.duration_seconds == 13  # ceil(12.4) as on the recording
    assert original.storage_backend == settings.storage_backend == "local"
    assert original.producer == "console"
    assert original.ingestion_id == body["ingestion"]["id"]
    assert original.job_id == job_id
    assert original.derived_from_id is None
    assert original.created_by_user_id == tenant["user_id"]
    assert original.workspace_id == tenant["workspace_id"]
    assert original.organization_id == tenant["organization_id"]
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        meeting = db.get(Meeting, meeting_id)
        assert recording is not None and meeting is not None
        assert recording.media_object_id == original.id
        assert recording.storage_key == original.storage_key
        assert original.retention_expires_at == meeting.retention_expires_at
        assert create_object_storage(settings).exists(tenant["workspace_id"], original.storage_key)
    registered = _audits("media.registered")
    assert len(registered) == 1 and registered[0].target_id == original.id
    assert registered[0].details["kind"] == "original"
    assert registered[0].details["producer"] == "console"

    listed = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/media")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["id"] for item in items] == [original.id]
    assert items[0]["kind"] == "original" and items[0]["lifecycle_state"] == "active"
    assert items[0]["integrity"] == "unverified"
    assert "storage_key" not in items[0]
    assert items[0]["ingestion_id"] == body["ingestion"]["id"]

    # Another tenant cannot see the conversation's media through either header.
    assert _get(client, other, f"/api/v1/meetings/{meeting_id}/media").status_code == 404
    hijack = client.get(
        f"/api/v1/meetings/{meeting_id}/media",
        headers={"X-Workspace-ID": tenant["workspace_id"]},
        cookies=other["cookies"],
    )
    assert hijack.status_code in {403, 404}


def test_integrity_verification_reads_the_bytes(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "integrity")
    accepted = _upload(client, tenant, key="media-verify")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    media_id = _media_rows(meeting_id)[0].id

    verified = client.post(
        f"/api/v1/meetings/{meeting_id}/media/{media_id}/verify",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["integrity"] == "verified"
    assert verified.json()["last_verified_at"] is not None
    checks = _audits("media.integrity_checked")
    assert checks[-1].outcome == "success" and checks[-1].details["integrity"] == "verified"

    # Tamper with the stored bytes underneath: the row no longer matches.
    storage_root = Path(settings.storage_local_root)
    stored = [path for path in storage_root.rglob("*") if path.is_file() and path.suffix != ".json"]
    key = _media_rows(meeting_id)[0].storage_key
    target = next(path for path in stored if key.split("/")[-1] in path.name)
    target.write_bytes(OTHER_CONTENT)
    corrupt = client.post(
        f"/api/v1/meetings/{meeting_id}/media/{media_id}/verify",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert corrupt.status_code == 200 and corrupt.json()["integrity"] == "corrupt"
    assert _audits("media.integrity_checked")[-1].outcome == "failed"

    target.unlink()
    missing = client.post(
        f"/api/v1/meetings/{meeting_id}/media/{media_id}/verify",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert missing.status_code == 200 and missing.json()["integrity"] == "missing"

    # Unknown / foreign object ids are 404; the CSRF token is required.
    assert (
        client.post(
            f"/api/v1/meetings/{meeting_id}/media/{media_id[::-1]}/verify",
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/meetings/{meeting_id}/media/{media_id}/verify",
            headers={"X-Workspace-ID": tenant["workspace_id"]},
            cookies=tenant["cookies"],
        ).status_code
        == 403
    )


def test_meeting_deletion_is_state_first_and_legal_hold_keeps_the_bytes(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "deletion")
    storage = create_object_storage(settings)
    plain = _upload(client, tenant, key="media-plain")
    held = _upload(client, tenant, key="media-held", content=OTHER_CONTENT)
    assert plain.status_code == 202 and held.status_code == 202
    plain_meeting, held_meeting = plain.json()["meeting"]["id"], held.json()["meeting"]["id"]
    plain_media, held_media = _media_rows(plain_meeting)[0], _media_rows(held_meeting)[0]

    with inspection_session_factory()() as db:
        media = db.get(MediaObject, held_media.id)
        assert media is not None
        place_legal_hold(
            db,
            media=media,
            reason="litigation 2026-42",
            review_at=datetime.now(UTC) + timedelta(days=90),
            actor_user_id=tenant["user_id"],
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()
    assert _audits("media.legal_hold_placed")[-1].target_id == held_media.id

    for meeting_id in (plain_meeting, held_meeting):
        deleted = client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
        )
        assert deleted.status_code == 204, deleted.text

    # Plain: bytes gone, row closed, recording unavailable.
    plain_after = _media_rows(plain_meeting)[0]
    assert plain_after.lifecycle_state is MediaLifecycleState.DELETED
    assert plain_after.deleted_at is not None
    assert plain_after.deletion_reason == "meeting_deleted"
    assert plain_after.deletion_requested_at is not None
    assert not storage.exists(tenant["workspace_id"], plain_after.storage_key)
    closed = _audits("media.deleted")
    assert [event.target_id for event in closed] == [plain_media.id]

    # Held: the request is recorded, the bytes stay, the state stays held.
    held_after = _media_rows(held_meeting)[0]
    assert held_after.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert held_after.deletion_requested_at is not None and held_after.deleted_at is None
    assert storage.exists(tenant["workspace_id"], held_after.storage_key)
    with inspection_session_factory()() as db:
        for meeting_id in (plain_meeting, held_meeting):
            recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
            assert recording is not None and recording.deleted_at is not None
    requested = _audits("media.deletion_requested")
    assert {event.target_id for event in requested} == {plain_media.id, held_media.id}

    # Releasing the hold makes the recorded request pending; the sweep closes it.
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, held_media.id)
        assert media is not None
        pending = release_legal_hold(
            db,
            media=media,
            reason="matter closed",
            actor_user_id=tenant["user_id"],
            now=datetime.now(UTC),
            settings=settings,
        )
        assert pending is True
        assert media.lifecycle_state is MediaLifecycleState.DELETION_PENDING
        assert media.legal_hold_at is None and media.legal_hold_reason is None
        db.commit()
        closed_now = delete_objects(
            db, storage, objects=[media], now=datetime.now(UTC), settings=settings
        )
        assert [row.id for row in closed_now] == [held_media.id]
        db.commit()
    held_final = _media_rows(held_meeting)[0]
    assert held_final.lifecycle_state is MediaLifecycleState.DELETED
    assert not storage.exists(tenant["workspace_id"], held_final.storage_key)

    # Requests on closed rows are no-ops; a hold cannot be placed on them.
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, held_media.id)
        assert media is not None
        assert (
            request_deletion(
                db,
                media=media,
                reason="again",
                actor_user_id=None,
                now=datetime.now(UTC),
                settings=settings,
            )
            is False
        )
        with pytest.raises(MediaLifecycleError, match="already deleted"):
            place_legal_hold(
                db,
                media=media,
                reason="late hold",
                review_at=datetime.now(UTC) + timedelta(days=1),
                actor_user_id=None,
                now=datetime.now(UTC),
                settings=settings,
            )
        with pytest.raises(MediaLifecycleError, match="already deleted"):
            verify_integrity(
                db,
                storage,
                media=media,
                actor_user_id=None,
                now=datetime.now(UTC),
                settings=settings,
            )
        db.rollback()


def test_delete_after_processing_goes_through_the_lifecycle(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed")
    accepted = _upload(client, tenant, key="media-processed")
    assert accepted.status_code == 202, accepted.text
    meeting_id, job_id = accepted.json()["meeting"]["id"], accepted.json()["job"]["id"]
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        workspace.delete_audio_after_processing = True
        db.commit()

    result = process_job(
        job_id, worker_id="lifecycle-worker", settings=settings, providers=_providers()
    )
    assert result["status"] == "completed"
    media = _media_rows(meeting_id)[0]
    assert media.lifecycle_state is MediaLifecycleState.DELETED
    assert media.deletion_reason == "processed" and media.deleted_at is not None
    assert not create_object_storage(settings).exists(tenant["workspace_id"], media.storage_key)
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None and recording.deleted_at is not None
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state.value == "completed"
    processed = _audits("meeting.recording.deleted_after_processing")
    assert processed[-1].details["media_retained"] is False
    # The conversation and its results survive; only the bytes are gone.
    detail = _get(client, tenant, f"/api/v1/meetings/{meeting_id}")
    assert detail.status_code == 200 and detail.json()["recording"] is None
    listed = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/media")
    assert listed.json()["items"][0]["lifecycle_state"] == "deleted"


def test_delete_after_processing_under_a_hold_keeps_the_recording_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    """A held ORIGINAL keeps its bytes and its recording through the
    delete-after-processing policy; the sweep that closes the released
    request also retires the recording (nothing is pretended either way)."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "held-processed")
    accepted = _upload(client, tenant, key="media-held-processed")
    assert accepted.status_code == 202, accepted.text
    meeting_id, job_id = accepted.json()["meeting"]["id"], accepted.json()["job"]["id"]
    media_id = _media_rows(meeting_id)[0].id
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        workspace.delete_audio_after_processing = True
        media = db.get(MediaObject, media_id)
        assert media is not None
        place_legal_hold(
            db,
            media=media,
            reason="audit 2026-43",
            review_at=datetime.now(UTC) + timedelta(days=30),
            actor_user_id=tenant["user_id"],
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()

    result = process_job(
        job_id, worker_id="lifecycle-worker", settings=settings, providers=_providers()
    )
    assert result["status"] == "completed"
    storage = create_object_storage(settings)
    held = _media_rows(meeting_id)[0]
    assert held.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert held.deletion_requested_at is not None and held.deletion_reason == "processed"
    assert held.deleted_at is None
    assert storage.exists(tenant["workspace_id"], held.storage_key)
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None and recording.deleted_at is None
    retained = _audits("meeting.recording.retained_after_processing")
    assert retained[-1].target_id == meeting_id
    assert retained[-1].details["media_retained"] is True
    assert all(
        event.target_id != meeting_id
        for event in _audits("meeting.recording.deleted_after_processing")
    )
    detail = _get(client, tenant, f"/api/v1/meetings/{meeting_id}")
    assert detail.status_code == 200 and detail.json()["recording"] is not None

    # Releasing the hold resumes the recorded request; closing it removes
    # the bytes and retires the recording in the same step.
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        assert media is not None
        assert (
            release_legal_hold(
                db,
                media=media,
                reason="audit closed",
                actor_user_id=tenant["user_id"],
                now=datetime.now(UTC),
                settings=settings,
            )
            is True
        )
        db.commit()
        closed = delete_objects(
            db, storage, objects=[media], now=datetime.now(UTC), settings=settings
        )
        assert [row.id for row in closed] == [media_id]
        db.commit()
    final = _media_rows(meeting_id)[0]
    assert final.lifecycle_state is MediaLifecycleState.DELETED
    assert final.deletion_reason == "processed" and final.deleted_at is not None
    assert not storage.exists(tenant["workspace_id"], final.storage_key)
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None and recording.deleted_at is not None
    detail = _get(client, tenant, f"/api/v1/meetings/{meeting_id}")
    assert detail.status_code == 200 and detail.json()["recording"] is None


def test_derived_objects_name_their_source_and_content_is_immutable(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "derived")
    accepted = _upload(client, tenant, key="media-derived")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    storage = create_object_storage(settings)
    normalized_bytes = b"normalized pcm bytes"
    stored = storage.put_stream(
        tenant["workspace_id"],
        BytesIO(normalized_bytes),
        content_type="audio/wav",
        original_filename="normalized.wav",
        expected_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
    )

    with inspection_session_factory()() as db:
        original = db.scalar(select(MediaObject).where(MediaObject.meeting_id == meeting_id))
        assert original is not None
        derived = register_derived(
            db,
            source=original,
            kind=MediaKind.NORMALIZED,
            stored=stored,
            storage_backend=settings.storage_backend,
            producer="ffmpeg:loudnorm@8.1",
            job_id=original.job_id,
            actor_user_id=None,
            now=datetime.now(UTC),
            settings=settings,
            container_format="wav",
            duration_seconds=13,
            channel_count=1,
            sample_rate_hz=16_000,
        )
        db.commit()
        assert derived.derived_from_id == original.id
        assert derived.meeting_id == meeting_id and derived.ingestion_id == original.ingestion_id
        assert derived.retention_expires_at == original.retention_expires_at
        assert derived.sha256 == stored.sha256 and derived.size_bytes == len(normalized_bytes)

        with pytest.raises(MediaLifecycleError, match="cannot be ORIGINAL"):
            register_derived(
                db,
                source=original,
                kind=MediaKind.ORIGINAL,
                stored=stored,
                storage_backend=settings.storage_backend,
                producer="x",
                job_id=None,
                actor_user_id=None,
                now=datetime.now(UTC),
                settings=settings,
            )
        db.rollback()

    listed = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/media")
    assert [(item["kind"], item["derived_from_id"]) for item in listed.json()["items"]] == [
        ("original", None),
        ("normalized", original.id),
    ]
    assert listed.json()["items"][1]["producer"] == "ffmpeg:loudnorm@8.1"

    # Database constraints: lineage and one ORIGINAL per conversation.
    with inspection_session_factory()() as db:
        db.add(
            MediaObject(
                organization_id=tenant["organization_id"],
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                kind=MediaKind.CLIP,
                storage_backend="local",
                storage_key=f"{tenant['workspace_id']}/clip-without-source",
                content_type="audio/wav",
                size_bytes=10,
                sha256="1" * 64,
                producer="test",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    with inspection_session_factory()() as db:
        db.add(
            MediaObject(
                organization_id=tenant["organization_id"],
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                kind=MediaKind.ORIGINAL,
                storage_backend="local",
                storage_key=f"{tenant['workspace_id']}/second-original",
                content_type="audio/wav",
                size_bytes=10,
                sha256="2" * 64,
                producer="test",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    # ORM guard: content columns never change; lifecycle columns do.
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, original.id)
        assert media is not None
        for column, value in (
            ("sha256", "3" * 64),
            ("storage_key", "elsewhere"),
            ("kind", MediaKind.EXPORT),
            ("size_bytes", 1),
            ("producer", "rewritten"),
            ("workspace_id", tenant["workspace_id"][::-1]),
        ):
            setattr(media, column, value)
            with pytest.raises(MediaImmutableError, match=column):
                db.flush()
            db.rollback()
            media = db.get(MediaObject, original.id)
            assert media is not None
        media.lifecycle_state = MediaLifecycleState.RETENTION_PENDING
        db.commit()
        db.refresh(media)
        assert media.lifecycle_state is MediaLifecycleState.RETENTION_PENDING
        # Derived from a non-live source is refused.
        with pytest.raises(MediaLifecycleError, match="not live"):
            media.lifecycle_state = MediaLifecycleState.DELETED
            media.deleted_at = datetime.now(UTC)
            register_derived(
                db,
                source=media,
                kind=MediaKind.CLIP,
                stored=stored,
                storage_backend="local",
                producer="x",
                job_id=None,
                actor_user_id=None,
                now=datetime.now(UTC),
                settings=settings,
            )
        db.rollback()


@pytest.mark.postgres
def test_rls_binds_media_rows_to_the_tenant(client, email_outbox, monkeypatch, probe_role):
    _patch_media(monkeypatch)
    one = _tenant(client, email_outbox, "rls-media-one")
    two = _tenant(client, email_outbox, "rls-media-two")
    assert _upload(client, one, key="rls-media").status_code == 202
    assert _upload(client, two, key="rls-media", content=OTHER_CONTENT).status_code == 202
    with inspection_session_factory()() as db:
        forced = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'media_objects'"
            )
        ).one()
        assert tuple(forced) == (True, True)

    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            assert _rls._count(connection, "media_objects") == 0
            _rls._bind(connection, workspace_id=one["workspace_id"])
            rows = connection.execute(text("SELECT workspace_id, kind FROM media_objects")).all()
            assert rows == [(one["workspace_id"], "ORIGINAL")]
            touched = connection.execute(
                text(
                    "UPDATE media_objects SET lifecycle_state = 'DELETION_PENDING' "
                    "WHERE workspace_id = :ws"
                ),
                {"ws": two["workspace_id"]},
            )
            assert touched.rowcount == 0
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text("UPDATE media_objects SET workspace_id = :other"),
                    {"other": two["workspace_id"]},
                )
            connection.rollback()
    finally:
        engine.dispose()
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(MediaObject.id))) == 2


@pytest.mark.postgres
def test_migration_backfills_one_original_per_recording(
    client, email_outbox, monkeypatch, settings
):
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "media-migration")
    live = _upload(client, tenant, key="media-live")
    gone = _upload(client, tenant, key="media-gone", content=OTHER_CONTENT)
    assert live.status_code == 202 and gone.status_code == 202
    live_meeting, gone_meeting = live.json()["meeting"]["id"], gone.json()["meeting"]["id"]
    deleted = client.delete(
        f"/api/v1/meetings/{gone_meeting}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    before = {row.meeting_id: row for row in _media_rows(live_meeting) + _media_rows(gone_meeting)}

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    reset_settings_cache()
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        command.stamp(config, "head")
        command.downgrade(config, "a8c4e2f6d1b9")
        with inspection_session_factory()() as db:
            assert db.execute(text("SELECT to_regclass('media_objects')")).scalar() is None
            columns = (
                db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'recordings'"
                    )
                )
                .scalars()
                .all()
            )
            assert "media_object_id" not in columns
            assert db.scalar(select(func.count(Recording.id))) == 2  # recordings survive
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()

    with inspection_session_factory()() as db:
        rows = {row.meeting_id: row for row in db.scalars(select(MediaObject))}
        assert set(rows) == {live_meeting, gone_meeting}
        for meeting_id, original in before.items():
            restored = rows[meeting_id]
            assert restored.id != original.id
            assert restored.kind is MediaKind.ORIGINAL
            assert restored.producer == "backfill"
            assert restored.storage_backend == "local"
            assert restored.storage_key == original.storage_key
            assert restored.sha256 == original.sha256 and restored.size_bytes == original.size_bytes
            assert restored.ingestion_id == original.ingestion_id
            assert restored.job_id == original.job_id
            assert restored.workspace_id == tenant["workspace_id"]
            assert restored.organization_id == tenant["organization_id"]
            assert restored.created_by_user_id == tenant["user_id"]
            recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
            assert recording is not None and recording.media_object_id == restored.id
        assert rows[live_meeting].lifecycle_state is MediaLifecycleState.ACTIVE
        assert rows[gone_meeting].lifecycle_state is MediaLifecycleState.DELETED
        assert rows[gone_meeting].deleted_at is not None
        assert rows[gone_meeting].deletion_reason == "backfill"
        forced = db.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'media_objects'")
        ).scalar()
        assert forced is True
