"""Delete-after-processing survives a worker killed after the COMPLETED commit (HOM-168).

The job is committed COMPLETED before the post-success step that removes the
recording under the workspace's delete-after-processing policy. A worker that
dies in between (SIGKILL, OOM, node loss) leaves the broker to redeliver the
task, and the periodic sweeps keep running. Whatever path is taken, the audio
must not outlive the policy, and a legal hold must still keep it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import backend.jobs.processor as processor
from backend.database.saas_models import MediaLifecycleState, MediaObject, Recording, Workspace
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.jobs.processor import process_job
from backend.jobs.recovery import sweep_expired_leases
from backend.jobs.retention_purge import run_retention_purge
from backend.services.media_lifecycle_service import place_legal_hold
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_job_processor import _providers
from backend.tests.test_media_lifecycle import _media_rows, _patch_media, _tenant, _upload


class WorkerKilled(BaseException):
    """Stands in for SIGKILL: not an Exception, so no handler in process_job runs."""


def _kill_before_cleanup(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def killed(claim, **_kwargs):
        calls.append(claim.job_id)
        raise WorkerKilled

    monkeypatch.setattr(processor, "_delete_audio_after_success", killed)
    return calls


def _completed_then_killed(client, email_outbox, monkeypatch, settings, key: str, *, hold: bool):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, key)
    accepted = _upload(client, tenant, key=f"media-{key}")
    assert accepted.status_code == 202, accepted.text
    meeting_id, job_id = accepted.json()["meeting"]["id"], accepted.json()["job"]["id"]
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        workspace.delete_audio_after_processing = True
        if hold:
            media = db.scalar(select(MediaObject).where(MediaObject.meeting_id == meeting_id))
            assert media is not None
            place_legal_hold(
                db,
                media=media,
                reason="audit 2026-44",
                review_at=datetime.now(UTC) + timedelta(days=30),
                actor_user_id=tenant["user_id"],
                now=datetime.now(UTC),
                settings=settings,
            )
        db.commit()

    original = processor._delete_audio_after_success
    killed = _kill_before_cleanup(monkeypatch)
    with pytest.raises(WorkerKilled):
        process_job(job_id, worker_id="killed-worker", settings=settings, providers=_providers())
    assert killed == [job_id], "the crash window between COMPLETED and cleanup was not reached"
    monkeypatch.setattr(processor, "_delete_audio_after_success", original)
    return tenant, meeting_id, job_id


def _redeliver_and_sweep(job_id: str, settings, *, retention_purge: bool = True) -> None:
    # The broker redelivers the unacknowledged task to another worker ...
    process_job(job_id, worker_id="redelivered-worker", settings=settings, providers=_providers())
    # ... and the periodic sweeps run, well after the reconciliation grace period.
    later = datetime.now(UTC) + timedelta(days=2)
    sweep_expired_leases(settings)
    reconcile_media_objects(settings, now=later)
    if retention_purge:
        run_retention_purge(settings, now=later)


def test_audio_is_deleted_after_a_kill_between_completion_and_cleanup(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, job_id = _completed_then_killed(
        client, email_outbox, monkeypatch, settings, "killed-cleanup", hold=False
    )

    # Media reconciliation alone closes the request (no retention purge).
    _redeliver_and_sweep(job_id, settings, retention_purge=False)

    media = _media_rows(meeting_id)[0]
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
    assert {
        "lifecycle": media.lifecycle_state,
        "reason": media.deletion_reason,
        "bytes_present": create_object_storage(settings).exists(
            tenant["workspace_id"], media.storage_key
        ),
        "recording_deleted": recording is not None and recording.deleted_at is not None,
    } == {
        "lifecycle": MediaLifecycleState.DELETED,
        "reason": "processed",
        "bytes_present": False,
        "recording_deleted": True,
    }


def test_a_legal_hold_still_keeps_the_audio_after_the_same_kill(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, job_id = _completed_then_killed(
        client, email_outbox, monkeypatch, settings, "killed-held", hold=True
    )

    _redeliver_and_sweep(job_id, settings)

    media = _media_rows(meeting_id)[0]
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
    assert media.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    # The request was recorded with the completion, so the release resumes it.
    assert media.deletion_requested_at is not None and media.deletion_reason == "processed"
    assert media.deleted_at is None
    assert create_object_storage(settings).exists(tenant["workspace_id"], media.storage_key)
    assert recording is not None and recording.deleted_at is None
