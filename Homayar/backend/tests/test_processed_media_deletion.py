"""HOM-314: delete-after-processing covers every media object of the meeting.

A workspace with ``delete_audio_after_processing`` asks for its audio to go once
processing succeeds. The completion transaction records the request on every
object of the meeting that is not deleted, in the same commit as COMPLETED.
The post-success step then removes the bytes of every pending row through
``delete_objects``. A legal hold keeps exactly the objects it holds.

The race tests use real PostgreSQL sessions in threads. They learn that a
session waits from ``pg_stat_activity`` and order the steps with events, never
with a clock (as in ``test_derived_legal_hold.py``).
"""

from __future__ import annotations

import dataclasses
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

import backend.jobs.processor as processor
from backend.database.saas_models import (
    MediaKind,
    MediaLifecycleState,
    ProcessingJob,
    Recording,
    Workspace,
)
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.media import derivatives
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.schemas import ASRRequest, ASRResult
from backend.services.media_lifecycle_service import LIVE_STATES
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_audio_derivative_job import _providers as _audio_providers
from backend.tests.test_audio_derivative_job import _stereo_wav
from backend.tests.test_audio_derivative_job import _upload as _upload_audio
from backend.tests.test_derived_legal_hold import (
    DEADLINE,
    _blocked,
    _close,
    _derive,
    _events,
    _hold,
    _hold_body,
    _on_meeting_row,
    _pause_after_first,
    _post,
    _registration,
    _row,
    _utc,
    _Worker,
)
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import _providers
from backend.tests.test_media_lifecycle import _media_rows
from backend.tests.test_processed_audio_deletion_durability import (
    WorkerKilled,
    _kill_before_cleanup,
    _redeliver_and_sweep,
)

REQUESTED = "media.deletion_requested"
DELETED = "media.deleted"
RECORDING_DELETED = "meeting.recording.deleted_after_processing"
RECORDING_RETAINED = "meeting.recording.retained_after_processing"
UNAVAILABLE = "media.derivative_unavailable"


# --- helpers ----------------------------------------------------------------------------


class _RefusingASR(ASRProvider):
    """A permanent provider refusal: the job ends failed."""

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        raise ProviderError("asr_rejected", "The provider refused the audio", retryable=False)


def _meeting(client, tenant: dict, key: str) -> tuple[str, str, str]:
    """One uploaded conversation; returns its meeting id, its job id and its ORIGINAL's id."""
    accepted = _upload(client, tenant, key=key, content=f"staged {key}".encode())
    assert accepted.status_code == 202, accepted.text
    meeting_id, job_id = accepted.json()["meeting"]["id"], accepted.json()["job"]["id"]
    return meeting_id, job_id, _media_rows(meeting_id)[0].id


def _delete_after_processing(tenant: dict) -> None:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        workspace.delete_audio_after_processing = True
        db.commit()


def _process(job_id: str, settings, label: str = "w", **kwargs) -> dict:
    return processor.process_job(
        job_id,
        worker_id=f"processed-media-{label}",
        settings=settings,
        providers=kwargs.pop("providers", None) or _providers(),
        **kwargs,
    )


def _recording(meeting_id: str) -> Recording:
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None
        return recording


def _present(settings, media_id: str) -> bool:
    row = _row(media_id)
    return create_object_storage(settings).exists(row.workspace_id, row.storage_key)


def _assert_deleted(settings, media_id: str) -> None:
    """Deleted for ``processed``, its bytes gone, one request and one deletion event."""
    row = _row(media_id)
    assert row.lifecycle_state is MediaLifecycleState.DELETED, media_id
    assert row.deletion_reason == "processed", media_id
    assert row.deletion_requested_at is not None and row.deleted_at is not None, media_id
    assert not _present(settings, media_id), media_id
    (requested,) = _events(REQUESTED, media_id)
    assert requested.target_type == "media"
    assert requested.details["reason"] == "processed"
    assert len(_events(DELETED, media_id)) == 1, media_id


def _assert_kept(settings, media_id: str) -> None:
    """Held with the ``processed`` request recorded, its bytes present, no deletion event."""
    row = _row(media_id)
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD, media_id
    assert row.deletion_requested_at is not None, media_id
    assert row.deletion_reason == "processed", media_id
    assert row.deleted_at is None, media_id
    assert _present(settings, media_id), media_id
    (requested,) = _events(REQUESTED, media_id)
    assert requested.target_type == "media"
    assert requested.details["reason"] == "processed"
    assert _events(DELETED, media_id) == [], media_id


def _assert_live_and_unrequested(settings, media_id: str) -> None:
    row = _row(media_id)
    assert row.lifecycle_state is MediaLifecycleState.ACTIVE, media_id
    assert row.deletion_requested_at is None and row.deleted_at is None, media_id
    assert _present(settings, media_id), media_id
    assert _events(REQUESTED, media_id) == [], media_id


def _retained_once(meeting_id: str) -> None:
    (event,) = _events(RECORDING_RETAINED, meeting_id)
    assert event.target_type == "meeting"
    assert event.details == {"media_retained": True, "reason": "legal_hold"}
    assert _events(RECORDING_DELETED, meeting_id) == []


def _deleted_once(meeting_id: str) -> None:
    (event,) = _events(RECORDING_DELETED, meeting_id)
    assert event.target_type == "meeting"
    assert event.details == {"media_retained": False}
    assert _events(RECORDING_RETAINED, meeting_id) == []


# --- A: every object of the meeting -----------------------------------------------------


def test_every_object_of_the_meeting_goes_after_processing(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-all")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-all")
    # Derived objects that exist before the job (A2), one of them two levels down.
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    gone_id = _derive(settings, tenant, original_id)
    _close(settings, gone_id)
    gone_before = _row(gone_id)
    _delete_after_processing(tenant)
    # The post-success step runs twice: the second run changes nothing (A5).
    real = processor._delete_audio_after_success

    def twice(claim, **kwargs) -> None:
        real(claim, **kwargs)
        real(claim, **kwargs)

    monkeypatch.setattr(processor, "_delete_audio_after_success", twice)

    assert _process(job_id, settings)["status"] == "completed"

    for media_id in (original_id, child_id, grandchild_id):
        _assert_deleted(settings, media_id)
    assert [row.id for row in _media_rows(meeting_id) if row.lifecycle_state in LIVE_STATES] == []
    assert _recording(meeting_id).deleted_at is not None
    _deleted_once(meeting_id)
    # An object deleted before the job is left as it was.
    gone = _row(gone_id)
    assert (gone.deletion_reason, gone.deleted_at) == ("test", gone_before.deleted_at)
    assert len(_events(REQUESTED, gone_id)) == 1 and len(_events(DELETED, gone_id)) == 1

    # The sweep after it changes nothing either (A5).
    later = datetime.now(UTC) + timedelta(days=2)
    reconcile_media_objects(settings, now=later, workspace_ids=[tenant["workspace_id"]])
    for media_id in (original_id, child_id, grandchild_id):
        _assert_deleted(settings, media_id)
    _deleted_once(meeting_id)


def test_without_the_policy_or_without_success_every_object_stays(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-kept")

    # The policy off: every object stays live after the job.
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-kept-off")
    child_id = _derive(settings, tenant, original_id)
    assert _process(job_id, settings, "off")["status"] == "completed"
    for media_id in (original_id, child_id):
        _assert_live_and_unrequested(settings, media_id)
    assert _recording(meeting_id).deleted_at is None
    assert _events(RECORDING_DELETED, meeting_id) == []

    # The policy on, but the job fails: nothing is deleted.
    _delete_after_processing(tenant)
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-kept-failed")
    child_id = _derive(settings, tenant, original_id)
    result = _process(job_id, settings, "failed", providers=_providers(_RefusingASR()))
    assert result["status"] == "failed"
    for media_id in (original_id, child_id):
        _assert_live_and_unrequested(settings, media_id)
    assert _recording(meeting_id).deleted_at is None
    assert _events(RECORDING_DELETED, meeting_id) == []


def test_a_recording_without_an_original_row_is_still_removed(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-unregistered")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-unregistered")
    storage_key = _row(original_id).storage_key
    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM media_objects WHERE id = :id"), {"id": original_id})
        db.commit()
    _delete_after_processing(tenant)

    assert _process(job_id, settings)["status"] == "completed"

    assert not create_object_storage(settings).exists(tenant["workspace_id"], storage_key)
    assert _recording(meeting_id).deleted_at is not None
    _deleted_once(meeting_id)


def test_no_meeting_event_for_a_conversation_deleted_before_the_step(
    client, email_outbox, monkeypatch, settings
):
    """A conversation deletion between the completion and the post-success step.

    The tombstone requested every object and marked the recording deleted; the
    route's first purge attempt failed (it only logs). The step still removes the
    bytes of every pending object, but the meeting events describe a recording
    that was live when the step started, so none is written (as before HOM-314).
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-conversation-deleted")
    _delete_after_processing(tenant)

    # Allow-control: the normal case writes the meeting event once.
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-not-deleted")
    assert _process(job_id, settings, "normal")["status"] == "completed"
    _assert_deleted(settings, original_id)
    _deleted_once(meeting_id)

    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-deleted-first")
    child_id = _derive(settings, tenant, original_id)

    def failing_purge(*_args, **_kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("backend.routers.v1.meetings.run_purge_attempt", failing_purge)
    real_step = processor._delete_audio_after_success
    deletions: list[int] = []

    def deleted_first(claim, **kwargs) -> None:
        deleted = client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
        )
        deletions.append(deleted.status_code)
        real_step(claim, **kwargs)

    monkeypatch.setattr(processor, "_delete_audio_after_success", deleted_first)

    assert _process(job_id, settings, "deleted-first")["status"] == "completed"

    assert deletions == [204]
    assert _recording(meeting_id).deleted_at is not None
    assert _events(RECORDING_DELETED, meeting_id) == []
    assert _events(RECORDING_RETAINED, meeting_id) == []
    for media_id in (original_id, child_id):
        row = _row(media_id)
        assert row.lifecycle_state is MediaLifecycleState.DELETED, media_id
        assert not _present(settings, media_id), media_id
        assert len(_events(REQUESTED, media_id)) == 1, media_id
        assert len(_events(DELETED, media_id)) == 1, media_id


def test_the_completion_requests_every_object_and_the_sweep_closes_them_after_a_kill(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-killed")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-killed")
    child_id = _derive(settings, tenant, original_id)
    _delete_after_processing(tenant)
    real = processor._delete_audio_after_success
    killed = _kill_before_cleanup(monkeypatch)

    with pytest.raises(WorkerKilled):
        _process(job_id, settings, "killed")
    assert killed == [job_id], "the crash window between COMPLETED and cleanup was not reached"
    monkeypatch.setattr(processor, "_delete_audio_after_success", real)

    # The request of every object was committed with COMPLETED (A3).
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.completed_at is not None
        completed_at = job.completed_at
    for media_id in (original_id, child_id):
        row = _row(media_id)
        assert row.lifecycle_state is MediaLifecycleState.DELETION_PENDING, media_id
        assert _utc(row.deletion_requested_at) == _utc(completed_at), media_id
        assert row.deletion_reason == "processed"
        assert _present(settings, media_id)
        assert len(_events(REQUESTED, media_id)) == 1

    # Media reconciliation alone closes every row (no retention purge).
    _redeliver_and_sweep(job_id, settings, retention_purge=False)
    for media_id in (original_id, child_id):
        _assert_deleted(settings, media_id)
    assert _recording(meeting_id).deleted_at is not None


# --- B: a legal hold is respected per object --------------------------------------------


def test_a_held_meeting_keeps_every_object_and_a_release_resumes_the_request(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-held")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-held")
    child_id = _derive(settings, tenant, original_id)
    held = _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    assert held.status_code == 200, held.text
    _delete_after_processing(tenant)

    assert _process(job_id, settings)["status"] == "completed"

    for media_id in (original_id, child_id):
        _assert_kept(settings, media_id)
    assert _recording(meeting_id).deleted_at is None
    _retained_once(meeting_id)

    # B4: each object released by its own id ends deleted, its bytes gone.
    for media_id in (child_id, original_id):
        released = _post(
            client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": "closed"}
        )
        assert released.status_code == 200, released.text
        assert released.json()["lifecycle_state"] == "deleted"
        _assert_deleted(settings, media_id)
    assert _recording(meeting_id).deleted_at is not None


def test_a_held_meeting_keeps_every_object_after_a_kill_and_the_sweeps(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-held-killed")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-held-killed")
    child_id = _derive(settings, tenant, original_id)
    held = _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    assert held.status_code == 200, held.text
    _delete_after_processing(tenant)
    real = processor._delete_audio_after_success
    killed = _kill_before_cleanup(monkeypatch)

    with pytest.raises(WorkerKilled):
        _process(job_id, settings, "held-killed")
    assert killed == [job_id]
    monkeypatch.setattr(processor, "_delete_audio_after_success", real)
    _redeliver_and_sweep(job_id, settings)

    for media_id in (original_id, child_id):
        _assert_kept(settings, media_id)
    assert _recording(meeting_id).deleted_at is None


def test_a_release_after_the_request_gives_the_deleted_event_not_the_retained_one(
    client, email_outbox, monkeypatch, settings
):
    """The meeting event is decided on the ORIGINAL's state under its lock.

    The ORIGINAL is held when the step records the request. A release by its id
    commits before the step removes bytes, and the release removes the bytes. The
    step must not report the recording as retained.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-released-between")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-released-between")
    _hold(client, tenant, original_id)
    _delete_after_processing(tenant)
    real_delete = processor.delete_objects
    releases: list[int] = []

    def release_first(*args, **kwargs):
        if not releases:
            released = _post(
                client,
                tenant,
                f"/api/v1/media/{original_id}/legal-hold/release",
                {"reason": "closed"},
            )
            releases.append(released.status_code)
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(processor, "delete_objects", release_first)

    assert _process(job_id, settings)["status"] == "completed"

    assert releases == [200]
    _assert_deleted(settings, original_id)
    assert _recording(meeting_id).deleted_at is not None
    _deleted_once(meeting_id)


def test_a_conversation_deletion_after_the_request_gives_no_meeting_event(
    client, email_outbox, monkeypatch, settings
):
    """A conversation deletion that commits between the request and the removal.

    The recording was live when the step began. The tombstone commits before the
    step removes bytes (its first purge attempt fails, it only logs). The step
    still closes every pending object, but writes no meeting event for a
    conversation the user deleted.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-deleted-between")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-deleted-between")
    child_id = _derive(settings, tenant, original_id)
    _delete_after_processing(tenant)

    def failing_purge(*_args, **_kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("backend.routers.v1.meetings.run_purge_attempt", failing_purge)
    real_delete = processor.delete_objects
    deletions: list[int] = []

    def deleted_first(*args, **kwargs):
        if not deletions:
            deleted = client.delete(
                f"/api/v1/meetings/{meeting_id}",
                headers=_headers(tenant),
                cookies=tenant["cookies"],
            )
            deletions.append(deleted.status_code)
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(processor, "delete_objects", deleted_first)

    assert _process(job_id, settings)["status"] == "completed"

    assert deletions == [204]
    assert _recording(meeting_id).deleted_at is not None
    assert _events(RECORDING_DELETED, meeting_id) == []
    assert _events(RECORDING_RETAINED, meeting_id) == []
    for media_id in (original_id, child_id):
        _assert_deleted(settings, media_id)


def test_the_removal_locks_the_rows_oldest_first(client, email_outbox, monkeypatch, settings):
    """The ORIGINAL is not always the oldest row: its time comes from the app clock,
    a job derivative's from the database clock. The removal sorts by (created_at, id)."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-lock-order")
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-lock-order")
    earlier = _utc(_row(original_id).created_at) - timedelta(minutes=1)
    older_id = _derive(settings, tenant, original_id, now=earlier)
    newer_id = _derive(settings, tenant, original_id)
    _delete_after_processing(tenant)
    real_delete = processor.delete_objects
    received: list[list[str]] = []

    def recorded(db, storage, *, objects, **kwargs):
        rows = list(objects)
        received.append([row.id for row in rows])
        return real_delete(db, storage, objects=rows, **kwargs)

    monkeypatch.setattr(processor, "delete_objects", recorded)

    assert _process(job_id, settings)["status"] == "completed"

    ordered = [row.id for row in _media_rows(meeting_id)]
    assert ordered[0] == older_id  # the derivative sorts before the ORIGINAL
    by_time = sorted(_media_rows(meeting_id), key=lambda row: (_utc(row.created_at), row.id))
    assert received == [[row.id for row in by_time]]
    assert set(received[0]) == {older_id, original_id, newer_id}
    for media_id in (older_id, original_id, newer_id):
        _assert_deleted(settings, media_id)
    _deleted_once(meeting_id)


def test_a_hold_keeps_exactly_the_objects_it_holds(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "processed-per-object")
    _delete_after_processing(tenant)

    # The ORIGINAL held by id (its derived object inherits), the derived object released by
    # its own id: the derived object goes, the ORIGINAL stays.
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-original-held")
    child_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, original_id)
    released = _post(
        client, tenant, f"/api/v1/media/{child_id}/legal-hold/release", {"reason": "not needed"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["lifecycle_state"] == "active"
    assert _process(job_id, settings, "original-held")["status"] == "completed"
    _assert_kept(settings, original_id)
    _assert_deleted(settings, child_id)
    assert _recording(meeting_id).deleted_at is None
    _retained_once(meeting_id)

    # The reverse: a derived object held by its own id stays, the ORIGINAL goes.
    meeting_id, job_id, original_id = _meeting(client, tenant, "processed-derived-held")
    child_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, child_id)
    assert _process(job_id, settings, "derived-held")["status"] == "completed"
    _assert_deleted(settings, original_id)
    _assert_kept(settings, child_id)
    assert _recording(meeting_id).deleted_at is not None
    _deleted_once(meeting_id)


# --- D1: the derivative job no longer skips such workspaces -----------------------------


@pytest.mark.ffmpeg
def test_with_derivatives_on_the_derivative_is_made_and_then_deleted(
    client, email_outbox, monkeypatch, settings, tmp_path: Path
):
    outcomes: list[str] = []
    real = processor.derive_audio_for_job

    def recording(*args, **kwargs) -> str:
        outcomes.append(real(*args, **kwargs))
        return outcomes[-1]

    monkeypatch.setattr(processor, "derive_audio_for_job", recording)
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_args, **_kw: True)
    tenant = _tenant(client, email_outbox, "processed-derived")
    _delete_after_processing(tenant)
    content = _stereo_wav(tmp_path / "call.wav").read_bytes()
    job_id, meeting_id = _upload_audio(client, tenant, content, key="processed-derived")
    enabled = settings.model_copy(update={"media_derivatives_enabled": True})

    result = _process(job_id, enabled, "derived", providers=_audio_providers())

    assert result["status"] == "completed"
    assert outcomes == ["registered"]
    rows = _media_rows(meeting_id)
    assert [row.kind for row in rows] == [MediaKind.ORIGINAL, MediaKind.NORMALIZED]
    original_id, derived_id = (row.id for row in rows)
    assert rows[1].derived_from_id == original_id
    for media_id in (original_id, derived_id):
        _assert_deleted(settings, media_id)
    assert _events(UNAVAILABLE) == []
    keys = {item.key for item in create_object_storage(settings).iter_objects(rows[0].workspace_id)}
    assert keys.isdisjoint({row.storage_key for row in rows})
    assert _recording(meeting_id).deleted_at is not None
    _deleted_once(meeting_id)


def test_the_audio_deleted_after_processing_reason_code_is_gone():
    assert not hasattr(derivatives, "AUDIO_DELETED_AFTER_PROCESSING")
    assert "audio_deleted_after_processing" not in derivatives.REASON_CODES


# --- races (PostgreSQL) -----------------------------------------------------------------


@pytest.mark.postgres
def test_race_a_meeting_hold_between_the_request_and_the_removal_keeps_the_bytes(
    client, email_outbox, monkeypatch, settings
):
    """A hold that commits after the request and before a row is locked for removal wins.

    The removal locks each row and reads it again before it removes the bytes; it
    waits here on the rows the hold has locked and then finds them held.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-processed-hold")
    meeting_id, job_id, original_id = _meeting(client, tenant, "race-processed-hold")
    child_id = _derive(settings, tenant, original_id)
    _delete_after_processing(tenant)
    removal_entered, removal_gate = threading.Event(), threading.Event()
    real_delete = processor.delete_objects

    def paused_delete(*args, **kwargs):
        removal_entered.set()
        assert removal_gate.wait(DEADLINE)
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(processor, "delete_objects", paused_delete)
    job = _Worker(lambda: _process(job_id, settings, "race-hold"))
    job.start()
    assert removal_entered.wait(DEADLINE)  # the request is committed, no byte is removed yet
    for media_id in (original_id, child_id):
        assert _row(media_id).lifecycle_state is MediaLifecycleState.DELETION_PENDING

    hold_entered, hold_gate = threading.Event(), threading.Event()
    _pause_after_first(monkeypatch, "place_legal_hold", hold_entered, hold_gate)
    hold = _Worker(
        lambda: _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    )
    hold.start()
    assert hold_entered.wait(DEADLINE)  # every media row is locked by the hold, not committed
    removal_gate.set()
    waited_on = _blocked(job)
    hold_gate.set()
    response = hold.finish()
    result = job.finish()

    assert response.status_code == 200, response.text
    assert result["status"] == "completed"
    # The removal's locking read of a media row (pg_stat_activity cuts the long
    # statement before its FOR UPDATE).
    assert re.match(r"SELECT media_objects\.id\b.*\bFROM media_objects\b", waited_on, re.S), (
        waited_on
    )
    for media_id in (original_id, child_id):
        _assert_kept(settings, media_id)
    assert _recording(meeting_id).deleted_at is None
    _retained_once(meeting_id)


@pytest.mark.postgres
def test_race_the_post_success_request_waits_for_an_inflight_registration_and_covers_it(
    client, email_outbox, monkeypatch, settings
):
    """The post-success request locks the meeting row before it reads the media rows.

    The completion half already covers every row it can see, and after it nothing
    can be derived. To test the post-success request on its own, the job runs
    without the policy and the step is then called with the policy on.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-processed-register")
    meeting_id, job_id, original_id = _meeting(client, tenant, "race-processed-register")
    real_step = processor._delete_audio_after_success
    claims: list = []
    monkeypatch.setattr(
        processor, "_delete_audio_after_success", lambda claim, **_kw: claims.append(claim)
    )
    assert _process(job_id, settings, "race-register")["status"] == "completed"
    (claim,) = claims
    claim = dataclasses.replace(claim, delete_audio_after_processing=True)
    _assert_live_and_unrequested(settings, original_id)

    inserted, gate = threading.Event(), threading.Event()
    registration = _Worker(
        _registration(settings, tenant, original_id, job_locks=True, inserted=inserted, gate=gate)
    )
    registration.start()
    assert inserted.wait(DEADLINE)  # the derived row is inserted, the meeting row locked
    storage = create_object_storage(settings)
    step = _Worker(lambda: real_step(claim, storage=storage, settings=settings))
    step.start()
    waited_on = _blocked(step)
    gate.set()
    derived_id = registration.finish()
    step.finish()

    assert _on_meeting_row(waited_on), waited_on
    for media_id in (original_id, derived_id):
        _assert_deleted(settings, media_id)
    assert _recording(meeting_id).deleted_at is not None
    _deleted_once(meeting_id)
