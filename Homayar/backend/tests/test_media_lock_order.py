"""HOM-330: media work locks its rows in one order, so two sessions never deadlock.

Each test runs two real PostgreSQL sessions in threads. One session stops while
it holds its row locks; the other is then seen in ``pg_stat_activity`` waiting
on a lock that the first one holds (``pg_blocking_pids``). This is the
interleaving that deadlocked before HOM-330. Then the first session goes on.
Neither may be aborted as a deadlock (SQLSTATE ``40P01``), and the end state
must be correct. Steps are ordered with events and lock waits, never with a clock.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import ModuleType, SimpleNamespace
from typing import Any

import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ConversationPurge,
    DispatchState,
    JobDispatch,
    JobState,
    MediaIntegrity,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    PurgeState,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import audio_derivatives, media_reconciliation, tombstone_reapply
from backend.jobs.leases import Lease, LeasePolicy, database_now, grant_lease
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.jobs.replay import replay_dead_letter_job
from backend.jobs.tombstone_reapply import reapply_tombstones
from backend.media import derivatives
from backend.media.derivatives import DerivedAudio
from backend.routers.v1 import meetings as meetings_router
from backend.services import conversation_purge_service
from backend.services.conversation_purge_service import run_purge_attempt
from backend.services.entitlement_service import release_usage
from backend.services.media_lifecycle_service import (
    media_by_id,
    place_legal_hold,
    register_derived,
    request_deletion,
)
from backend.services.tombstone_journal import TombstoneJournal, TombstoneRecord
from backend.storage import create_object_storage
from backend.storage.base import StoredObject
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload

REASON = "litigation 2026-42"
# Only fails a hung test; no assertion depends on how long anything took.
DEADLINE = 30.0


# --- helpers ----------------------------------------------------------------------------


class _Worker(threading.Thread):
    """Runs ``work`` in its own thread and keeps its result or its error."""

    def __init__(self, work: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._work = work
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._work()
        except BaseException as exc:  # noqa: BLE001 - checked by _settled()
            self.error = exc


def _deadlock(error: BaseException | None) -> bool:
    """SQLSTATE 40P01, raised by psycopg or wrapped by SQLAlchemy."""
    while error is not None:
        if isinstance(error, psycopg.errors.DeadlockDetected):
            return True
        if getattr(error, "sqlstate", None) == "40P01":
            return True
        error = getattr(error, "orig", None) or error.__cause__
    return False


def _settled(*workers: _Worker) -> list[Any]:
    """Every session ends and none was aborted as a deadlock; returns their results."""
    for worker in workers:
        worker.join(DEADLINE)
    assert not [worker for worker in workers if worker.is_alive()], "a session did not finish"
    deadlocks = [worker.error for worker in workers if _deadlock(worker.error)]
    assert deadlocks == [], deadlocks
    for worker in workers:
        if worker.error is not None:
            raise worker.error
    return [worker.result for worker in workers]


def _pid(db: Session) -> int:
    return db.execute(text("SELECT pg_backend_pid()")).scalar_one()


def _pause(monkeypatch, module: ModuleType, name: str, *, after: bool) -> SimpleNamespace:
    """The first call of ``module.name`` stops until ``gate`` is set.

    It stops after the real call (``after``) or before it, and keeps the backend
    pid of the caller's session (its first argument) in ``pid``.
    """
    real = getattr(module, name)
    pause = SimpleNamespace(entered=threading.Event(), gate=threading.Event(), pid=None)

    def stop(db: Session) -> None:
        if pause.entered.is_set():
            return
        pause.pid = _pid(db)
        pause.entered.set()
        assert pause.gate.wait(DEADLINE)

    def paused(db: Session, *args: Any, **kwargs: Any) -> Any:
        if not after:
            stop(db)
        result = real(db, *args, **kwargs)
        if after:
            stop(db)
        return result

    monkeypatch.setattr(module, name, paused)
    return pause


def _recorded(monkeypatch, module: ModuleType, name: str) -> SimpleNamespace:
    """Keeps the backend pid of the session that calls ``module.name`` first."""
    real = getattr(module, name)
    seen = SimpleNamespace(pid=None)

    def recording(db: Session, *args: Any, **kwargs: Any) -> Any:
        if seen.pid is None:
            seen.pid = _pid(db)
        return real(db, *args, **kwargs)

    monkeypatch.setattr(module, name, recording)
    return seen


class _PausedStorage:
    """The real storage; its first ``delete`` waits for ``gate`` before it runs."""

    def __init__(self, real: Any, entered: threading.Event, gate: threading.Event) -> None:
        self._real = real
        self._entered = entered
        self._gate = gate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def delete(self, tenant_id: str, key: str) -> bool:
        if not self._entered.is_set():
            self._entered.set()
            assert self._gate.wait(DEADLINE)
        return self._real.delete(tenant_id, key)


def _blocked(worker: _Worker, holder_pid: int) -> int:
    """The backend pid of the session that waits on a lock held by ``holder_pid``.

    Only backends of this test's database count. Fails when ``worker`` ends
    without waiting.
    """
    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        with inspection_session_factory()() as db:
            waiting = db.scalars(
                text(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND CAST(:holder AS integer) = ANY(pg_blocking_pids(pid))"
                ),
                {"holder": holder_pid},
            ).first()
        if waiting is not None:
            return waiting
        if not worker.is_alive():
            pytest.fail(f"the session never waited on a lock (error: {worker.error!r})")
        time.sleep(0.01)
    pytest.fail("no session waited on the paused session's locks before the deadline")


def _post(client, tenant: dict, path: str, payload: dict):
    return client.post(path, json=payload, headers=_headers(tenant), cookies=tenant["cookies"])


def _delete_meeting(client, tenant: dict, meeting_id: str):
    return client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )


def _hold_body() -> dict:
    return {"reason": REASON, "review_at": (datetime.now(UTC) + timedelta(days=90)).isoformat()}


def _late() -> datetime:
    """A sweep time after the reconciliation grace and the hold recheck of now."""
    return datetime.now(UTC) + timedelta(hours=2)


def _conversation(client, tenant: dict, key: str) -> tuple[str, str, str]:
    """One uploaded conversation: its job id, meeting id and ORIGINAL's id."""
    accepted = _upload(client, tenant, key=key, content=f"staged {key}".encode())
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    with inspection_session_factory()() as db:
        original_id = db.scalar(
            select(MediaObject.id).where(
                MediaObject.meeting_id == meeting_id, MediaObject.kind == MediaKind.ORIGINAL
            )
        )
    assert original_id is not None
    return accepted.json()["job"]["id"], meeting_id, original_id


def _stored(settings, workspace_id: str) -> StoredObject:
    payload = f"derived {uuid.uuid4()}".encode()
    return create_object_storage(settings).put_stream(
        workspace_id,
        BytesIO(payload),
        content_type="audio/wav",
        original_filename="normalized.wav",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _derive(settings, tenant: dict, source_id: str, *, now: datetime) -> str:
    """One derived object of ``source_id``, registered at ``now`` in its own transaction."""
    stored = _stored(settings, tenant["workspace_id"])
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        source = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=source_id)
        assert source is not None
        derived = register_derived(
            db,
            source=source,
            kind=MediaKind.NORMALIZED,
            stored=stored,
            storage_backend=settings.storage_backend,
            producer="test-derivative/1",
            job_id=None,
            actor_user_id=None,
            now=now,
            settings=settings,
            container_format="wav",
            duration_seconds=3,
            channel_count=2,
            sample_rate_hz=16_000,
        )
        db.commit()
        return derived.id


def _request(settings, media_id: str, *, now: datetime) -> None:
    with inspection_session_factory()() as db:
        request_deletion(
            db,
            media=db.get(MediaObject, media_id),
            reason="owner asked",
            actor_user_id=None,
            now=now,
            settings=settings,
        )
        db.commit()


def _pending_pair(settings, tenant: dict, source_id: str) -> tuple[str, str]:
    """Two ``deletion_pending`` children of ``source_id``.

    The first is created first, the second has the earlier deletion request.
    """
    start = datetime.now(UTC)
    first = _derive(settings, tenant, source_id, now=start + timedelta(seconds=1))
    second = _derive(settings, tenant, source_id, now=start + timedelta(seconds=2))
    _request(settings, second, now=start + timedelta(seconds=10))
    _request(settings, first, now=start + timedelta(seconds=20))
    for media_id in (first, second):
        assert _row(media_id).lifecycle_state is MediaLifecycleState.DELETION_PENDING
    return first, second


def _row(media_id: str) -> MediaObject:
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        assert media is not None
        return media


def _rows(meeting_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(MediaObject).where(MediaObject.meeting_id == meeting_id)))


def _purge(meeting_id: str) -> ConversationPurge:
    with inspection_session_factory()() as db:
        purge = db.scalar(
            select(ConversationPurge).where(ConversationPurge.meeting_id == meeting_id)
        )
        assert purge is not None
        return purge


def _has_bytes(settings, media: MediaObject) -> bool:
    return create_object_storage(settings).exists(media.workspace_id, media.storage_key)


def _assert_bytes_match_state(settings, media: MediaObject) -> None:
    """Deleted without bytes and without a hold, or held with its bytes."""
    if media.lifecycle_state is MediaLifecycleState.DELETED:
        assert not _has_bytes(settings, media), media.id
        assert media.legal_hold_at is None, media.id
    else:
        assert media.lifecycle_state is MediaLifecycleState.LEGAL_HOLD, media.id
        assert _has_bytes(settings, media), media.id


def _attempt(tenant: dict, settings, purge_id: str, now: datetime) -> Callable[[], Any]:
    def work() -> Any:
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            return run_purge_attempt(
                db,
                create_object_storage(settings),
                purge_id=purge_id,
                workspace_id=tenant["workspace_id"],
                now=now,
                settings=settings,
            )

    return work


def _settle_job(job_id: str) -> None:
    """The job's reservations are released and its dispatch is published.

    Cancelling it then writes no row before ``DELETE /meetings`` locks the media rows.
    """
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        for reservation_id in (job.reservation_id, job.concurrency_reservation_id):
            if reservation_id is not None:
                release_usage(db, reservation_id, reason="settled")
        dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
        if dispatch is not None:
            dispatch.state = DispatchState.PUBLISHED
        db.commit()


def _lease(settings, job_id: str) -> Lease:
    """A running attempt of the job, as a worker's claim leaves it."""
    with inspection_session_factory()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        lease = grant_lease(
            job,
            worker_id="test-lock-order",
            policy=LeasePolicy.from_settings(settings),
            now=database_now(db),
        )
        db.commit()
        return lease


# --- P1: a hold or a release by id against the media reconciliation sweep ---------------


@pytest.mark.postgres
@pytest.mark.parametrize("action", ["hold", "release"])
def test_a_hold_or_release_by_id_and_the_sweep_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings, action
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, f"order-sweep-{action}")
    _job_id, meeting_id, original_id = _conversation(client, tenant, f"order-sweep-{action}")
    first, second = _pending_pair(settings, tenant, original_id)
    if action == "release":
        with inspection_session_factory()() as db:
            place_legal_hold(
                db,
                media=db.get(MediaObject, original_id),
                reason=REASON,
                review_at=datetime.now(UTC) + timedelta(days=90),
                actor_user_id=tenant["user_id"],
                now=datetime.now(UTC),
                settings=settings,
            )
            db.commit()
        _request(settings, original_id, now=datetime.now(UTC))
        assert _row(original_id).lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    sweeper = _recorded(monkeypatch, media_reconciliation, "delete_objects")
    entered, gate = threading.Event(), threading.Event()
    storage = _PausedStorage(create_object_storage(settings), entered, gate)
    sweep = _Worker(
        lambda: reconcile_media_objects(
            settings, storage=storage, now=_late(), workspace_ids=[tenant["workspace_id"]]
        )
    )
    sweep.start()
    assert entered.wait(DEADLINE)  # a pending row is locked, its bytes not removed yet
    path, body = {
        "hold": (f"/api/v1/media/{original_id}/legal-hold", _hold_body()),
        "release": (f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}),
    }[action]
    route = _Worker(lambda: _post(client, tenant, path, body))
    route.start()
    _blocked(route, sweeper.pid)
    gate.set()
    counts, response = _settled(sweep, route)

    assert response.status_code == 200, response.text
    descendants = [_row(first), _row(second)]
    for media in descendants:
        _assert_bytes_match_state(settings, media)
    closed = [m for m in descendants if m.lifecycle_state is MediaLifecycleState.DELETED]
    assert counts["pending_closed"] == len(closed)
    original = _row(original_id)
    if action == "hold":
        assert original.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
        assert _has_bytes(settings, original)
    else:
        # Released with a recorded deletion request: the route removed it, and the
        # sweep removed both children (the release did not hold them).
        for media in (original, *descendants):
            assert media.lifecycle_state is MediaLifecycleState.DELETED, media.id
            assert not _has_bytes(settings, media), media.id
    assert {m.id for m in _rows(meeting_id)} == {original_id, first, second}


# --- P2: a release by id against the purge attempt -----------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("first", ["release", "attempt"])
def test_a_release_and_a_purge_attempt_finish_without_a_deadlock_and_keep_the_nudge(
    client, email_outbox, monkeypatch, settings, first
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, f"order-purge-{first}")
    _job_id, meeting_id, original_id = _conversation(client, tenant, f"order-purge-{first}")
    held = _post(client, tenant, f"/api/v1/media/{original_id}/legal-hold", _hold_body())
    assert held.status_code == 200, held.text
    assert _delete_meeting(client, tenant, meeting_id).status_code == 204
    deferred = _purge(meeting_id)  # the inline attempt deferred it to the hold recheck
    assert deferred.state is PurgeState.PENDING
    assert deferred.last_error == "legal_hold"
    assert deferred.next_attempt_at > datetime.now(UTC)

    release = _Worker(
        lambda: _post(
            client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
        )
    )
    attempt = _Worker(_attempt(tenant, settings, deferred.id, _late()))
    released_from = datetime.now(UTC)
    if first == "release":
        pause = _pause(monkeypatch, meetings_router, "nudge_purge", after=False)
        release.start()
        assert pause.entered.wait(DEADLINE)  # the release holds its locks, nudge not sent
        attempt.start()
        _blocked(attempt, pause.pid)
    else:
        pause = _pause(monkeypatch, conversation_purge_service, "_claim", after=True)
        attempt.start()
        assert pause.entered.wait(DEADLINE)  # the attempt holds its claim
        release.start()
        _blocked(release, pause.pid)
    pause.gate.set()
    response, _outcome = _settled(release, attempt)
    released_by = datetime.now(UTC)

    assert response.status_code == 200, response.text
    purge = _purge(meeting_id)
    if purge.state is not PurgeState.COMPLETED:
        assert purge.state is PurgeState.PENDING
        assert released_from <= purge.next_attempt_at <= released_by
    original = _row(original_id)
    assert original.lifecycle_state is MediaLifecycleState.DELETED
    assert not _has_bytes(settings, original)


# --- P3: the derivative registration against DELETE /meetings --------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("job", ["settled-by-hand", "upload"])
def test_the_derivative_registration_and_a_conversation_deletion_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings, tmp_path, job
):
    """``settled-by-hand`` builds a job state by hand: its reservations released and its
    dispatch published, so cancelling it writes no row before the media rows. Only that
    state deadlocked before HOM-330. ``upload`` keeps the upload job as it is (reservations
    still reserved): its cancellation writes the job row first, so it never deadlocked, and
    it must not start to.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, f"order-register-{job}")
    job_id, meeting_id, original_id = _conversation(client, tenant, f"order-register-{job}")
    if job == "settled-by-hand":
        _settle_job(job_id)
    original = _row(original_id)
    claim = SimpleNamespace(
        workspace_id=tenant["workspace_id"],
        meeting_id=meeting_id,
        job_id=job_id,
        lease=_lease(settings, job_id),
        recording_sha256=original.sha256,
    )
    stored = _stored(settings, tenant["workspace_id"])
    outcome = DerivedAudio(
        path=tmp_path / "derivative.wav",
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        record={},
        channel_count=2,
        sample_count=48_000,
    )
    pause = _pause(monkeypatch, conversation_purge_service, "retire_meeting_media", after=True)
    deletion = _Worker(lambda: _delete_meeting(client, tenant, meeting_id))
    deletion.start()
    assert pause.entered.wait(DEADLINE)  # the media rows are locked, the tombstone is not written
    registration = _Worker(
        lambda: audio_derivatives._register(
            claim, original_id, outcome, stored, create_object_storage(settings), settings
        )
    )
    registration.start()
    _blocked(registration, pause.pid)
    pause.gate.set()
    deleted, reason = _settled(deletion, registration)

    assert deleted.status_code == 204
    assert reason == derivatives.JOB_CANCELLED
    assert not create_object_storage(settings).exists(tenant["workspace_id"], stored.key)
    rows = _rows(meeting_id)
    assert [row.id for row in rows] == [original_id]
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, job_id).state is JobState.CANCELLED


# --- the sweep's missing check against a hold by id ---------------------------------------


def _lost_pair(settings, tenant: dict, source_id: str) -> tuple[str, str]:
    """Two live children of ``source_id`` whose bytes are gone from storage.

    The first is created first and has the larger id, so creation order and
    primary-key order differ.
    """
    larger, smaller = sorted(
        (_derive(settings, tenant, source_id, now=datetime.now(UTC)) for _ in range(2)),
        reverse=True,
    )
    start = datetime.now(UTC)
    storage = create_object_storage(settings)
    with inspection_session_factory()() as db:
        for media_id, seconds in ((larger, 1), (smaller, 2)):
            db.execute(
                text("UPDATE media_objects SET created_at = :at WHERE id = :id"),
                {"at": start + timedelta(seconds=seconds), "id": media_id},
            )
        db.commit()
    for media_id in (larger, smaller):
        media = _row(media_id)
        storage.delete(media.workspace_id, media.storage_key)
        assert not _has_bytes(settings, media)
    return larger, smaller


@pytest.mark.postgres
def test_a_hold_by_id_and_the_sweep_missing_check_finish_without_a_deadlock(
    client, email_outbox, monkeypatch, settings
):
    """A third session holds ``FOR KEY SHARE`` on the second child, as a foreign-key
    check of a new row that points at it does. The hold then waits on that child while
    it holds the first, and an update of the second child does not wait for it.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "order-missing")
    _job_id, _meeting_id, original_id = _conversation(client, tenant, "order-missing")
    first, second = _lost_pair(settings, tenant, original_id)
    route = _Worker(
        lambda: _post(client, tenant, f"/api/v1/media/{original_id}/legal-hold", _hold_body())
    )
    sweep = _Worker(
        lambda: reconcile_media_objects(
            settings, now=_late(), workspace_ids=[tenant["workspace_id"]]
        )
    )
    with inspection_session_factory()() as share:
        share.execute(
            text("SELECT id FROM media_objects WHERE id = :id FOR KEY SHARE"), {"id": second}
        )
        route.start()
        route_pid = _blocked(route, _pid(share))
        sweep.start()
        _blocked(sweep, route_pid)
        share.rollback()
    counts, response = _settled(sweep, route)

    assert response.status_code == 200, response.text
    assert counts["missing_flagged"] == 2
    for media_id in (first, second):
        row = _row(media_id)
        assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD, media_id
        assert row.integrity is MediaIntegrity.MISSING, media_id
    original = _row(original_id)
    assert original.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert _has_bytes(settings, original)


# --- the tombstone reapply against a replay ----------------------------------------------


def _dead_letter(job_id: str) -> None:
    """The job is parked as dead-lettered, its reservations released (as parking does)."""
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        for reservation_id in (job.reservation_id, job.concurrency_reservation_id):
            if reservation_id is not None:
                release_usage(db, reservation_id, reason="dead_letter")
        job.state = JobState.DEAD_LETTER
        db.commit()


def _replay(settings, tenant: dict, job_id: str) -> None:
    """An operator replay of the dead-lettered job, committed."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        replay_dead_letter_job(
            db,
            job_id=job_id,
            workspace_id=tenant["workspace_id"],
            actor_user_id=tenant["user_id"],
            settings=settings,
        )
        db.commit()


@pytest.mark.postgres
def test_the_tombstone_reapply_cancels_a_job_made_active_between_its_job_and_meeting_locks(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "order-reapply")
    job_id, meeting_id, _original_id = _conversation(client, tenant, "order-reapply")
    _dead_letter(job_id)
    journal = TombstoneJournal(tmp_path / "journal")
    journal.append(
        [
            TombstoneRecord(
                purge_id=str(uuid.uuid4()),
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                reason="meeting_deleted",
                tombstoned_at=datetime.now(UTC),
                object_keys=(),
            )
        ],
        now=datetime.now(UTC),
    )
    pause = _pause(monkeypatch, tombstone_reapply, "_lock_jobs", after=True)
    reapply = _Worker(
        lambda: reapply_tombstones(
            settings, journal=journal, storage=create_object_storage(settings), run_purge=False
        )
    )
    reapply.start()
    assert pause.entered.wait(DEADLINE)  # no job is active yet, the meeting row is not locked
    _replay(settings, tenant, job_id)
    pause.gate.set()
    (counts,) = _settled(reapply)

    assert counts["reapplied"] == 1 and counts["jobs_cancelled"] == 1, counts
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, job_id).state is JobState.CANCELLED
        assert db.get(Meeting, meeting_id).deleted_at is not None
