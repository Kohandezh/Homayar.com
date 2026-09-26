"""WP06-T07: fault injection over accepted jobs, reconciled against the invariants.

Worker death mid-stage, broker outage, lost acknowledgement (redelivery
during and after an attempt), duplicate delivery and storage faults are
injected deterministically (gates, counters, fake clocks — never sleeps);
afterwards every accepted job must satisfy ``backend.jobs.invariants``:
no lost or stuck job, honest terminal states, one settlement per meeting.
Threaded scenarios (row locking) are PostgreSQL evidence; the SQLite
fallback runs the sequential ones for shape.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    DispatchState,
    JobDispatch,
    JobState,
    ProcessingJob,
    Recording,
    ReservationState,
    UsageReservation,
    Workspace,
)
from backend.jobs import invariants, outbox, recovery
from backend.jobs.processor import process_job
from backend.storage import StorageError, create_object_storage
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_job_failure_policy import _make_due, _reservations
from backend.tests.test_job_leases import (
    GatedASRProvider,
    _expire_lease,
    _job,
    _wait_until,
)
from backend.tests.test_job_processor import _providers, _queued_upload
from backend.tests.test_usage_ledger import _events

DEADLINE_SECONDS = 15.0


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


class FaultyStorage:
    """Delegating object storage that fails on demand.

    ``fail_reads_per_key`` fails the first N reads of every *recording* key
    (an outage while materialising); ``fail_writes`` fails the next N
    ``put_stream`` calls (an outage while the worker stores a stage
    artifact); ``fail_deletes`` fails every delete; ``fail_exists`` makes
    existence checks raise (storage that does not answer).  Artifact
    read-backs are never faulted by the per-key rule: a stage artifact is a
    fresh key per attempt, so a per-key fault would never let a job
    converge, which is a property of the fault model, not of the system.
    """

    def __init__(
        self,
        inner,
        *,
        fail_reads_per_key: int = 0,
        fail_deletes: bool = False,
        fail_writes: int = 0,
        fail_exists: bool = False,
    ):
        self._inner = inner
        self.fail_reads_per_key = fail_reads_per_key
        self.fail_deletes = fail_deletes
        self.fail_writes = fail_writes
        self.fail_exists = fail_exists
        self.read_failures: dict[str, int] = {}
        self.delete_attempts = 0
        self.write_attempts = 0
        self.write_failures = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def _is_artifact(key: str) -> bool:
        return key.rsplit("/", 1)[-1].startswith("art-")

    def open_reader(self, tenant_id: str, key: str) -> BinaryIO:
        failed = self.read_failures.get(key, 0)
        if not self._is_artifact(key) and failed < self.fail_reads_per_key:
            self.read_failures[key] = failed + 1
            raise StorageError("simulated object store outage", code="storage_unavailable")
        return self._inner.open_reader(tenant_id, key)

    def put_stream(self, tenant_id: str, stream, **kwargs):
        self.write_attempts += 1
        if self.fail_writes > 0:
            self.fail_writes -= 1
            self.write_failures += 1
            raise StorageError("simulated object store write outage", code="storage_unavailable")
        return self._inner.put_stream(tenant_id, stream, **kwargs)

    def exists(self, tenant_id: str, key: str) -> bool:
        if self.fail_exists:
            raise StorageError("simulated object store outage", code="storage_unavailable")
        return self._inner.exists(tenant_id, key)

    def delete(self, tenant_id: str, key: str) -> bool:
        self.delete_attempts += 1
        if self.fail_deletes:
            raise StorageError("simulated delete outage", code="storage_unavailable")
        return self._inner.delete(tenant_id, key)


def _violations(workspace_id: str, settings) -> list[invariants.Violation]:
    with inspection_session_factory()() as db:
        return invariants.check_workspace(db, workspace_id, settings=settings)


def _states(job_ids: list[str]) -> dict[str, str]:
    return {job_id: _job(job_id).state.value for job_id in job_ids}


def _drive(
    job_ids: list[str],
    *,
    settings,
    broker: FakeBroker,
    storage,
    providers,
    duplicate_delivery: bool,
    max_rounds: int = 12,
) -> int:
    """Publish, deliver (twice when asked), recover and retry until every job is terminal."""
    clock = datetime.now(UTC)
    delivered = 0
    for round_number in range(1, max_rounds + 1):
        outbox.sweep_dispatch_outbox(settings, now=clock)
        while delivered < len(broker.published):
            job_id, _ = broker.published[delivered]
            delivered += 1
            copies = 2 if duplicate_delivery else 1
            for copy in range(copies):
                process_job(
                    job_id,
                    worker_id=f"worker-r{round_number}-c{copy}",
                    settings=settings,
                    providers=providers,
                    storage=storage,
                )
        recovery.sweep_expired_leases(settings)
        for job_id in job_ids:
            job = _job(job_id)
            if job.state == JobState.QUEUED and job.retry_after is not None:
                _make_due(job_id)
        if all(_job(job_id).state.value in {"completed", "failed", "dead_letter", "cancelled"}
               for job_id in job_ids):
            return round_number
        clock += timedelta(seconds=settings.dispatch_retry_max_seconds + 1)
    raise AssertionError(f"jobs did not converge: {_states(job_ids)}")


# --- scenarios ----------------------------------------------------------------


def test_storage_and_broker_faults_with_duplicate_delivery_converge_and_charge_once(
    client, email_outbox, monkeypatch, settings, broker
):
    jobs = [_queued_upload(client, email_outbox, monkeypatch) for _ in range(3)]
    job_ids = [job_id for job_id, _ in jobs]
    storage = FaultyStorage(create_object_storage(settings), fail_reads_per_key=1)
    broker.fail_times = 2  # the first two publications fail, then the broker is back

    rounds = _drive(
        job_ids,
        settings=settings,
        broker=broker,
        storage=storage,
        providers=_providers(),
        duplicate_delivery=True,
    )
    assert rounds >= 2  # at least one retry round was needed
    assert len(broker.published) >= len(job_ids)
    for job_id, workspace_id in jobs:
        job = _job(job_id)
        assert job.state == JobState.COMPLETED, job.failure_code
        # first attempt hit the storage fault (transient), the retry succeeded
        assert job.attempt_count == 2 and job.failure_class is None
        assert job.dispatch.state == DispatchState.PUBLISHED
        reservations = _reservations(job_id)
        assert reservations[job.reservation_id] == "committed"
        assert sum(1 for e in _events(workspace_id) if e.event_type == "committed") == 1
        assert _violations(workspace_id, settings) == []
    assert len(storage.read_failures) == len(job_ids)


@pytest.mark.postgres
def test_worker_killed_mid_stage_is_recovered_and_the_late_writer_is_fenced(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locking is PostgreSQL evidence")
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    gate = threading.Event()
    result: dict[str, dict] = {}

    def doomed_worker() -> None:
        result["value"] = process_job(
            job_id,
            worker_id="worker-killed",
            settings=settings,
            providers=_providers(GatedASRProvider(gate, "late transcript")),
        )

    thread = threading.Thread(target=doomed_worker)
    thread.start()
    _wait_until(lambda: _job(job_id).state == JobState.RUNNING)
    # the worker "dies": its heartbeat stops and the lease lapses
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings) == {"scanned": 1, "recovered": 1}
    assert _job(job_id).state == JobState.QUEUED
    # a healthy worker takes the redelivered job
    second = process_job(
        job_id, worker_id="worker-healthy", settings=settings, providers=_providers()
    )
    assert second["status"] in {"completed", "partial"}
    # the dead worker resumes: its late writes are fenced
    gate.set()
    thread.join(timeout=DEADLINE_SECONDS)
    assert result["value"]["status"] == "superseded"

    job = _job(job_id)
    assert job.state == JobState.COMPLETED and job.attempt_count == 2
    assert sum(1 for e in _events(workspace_id) if e.event_type == "committed") == 1
    assert _violations(workspace_id, settings) == []


@pytest.mark.postgres
def test_lost_acknowledgement_redelivers_during_and_after_the_attempt(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locking is PostgreSQL evidence")
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    gate = threading.Event()
    result: dict[str, dict] = {}

    def worker() -> None:
        result["value"] = process_job(
            job_id,
            worker_id="worker-first",
            settings=settings,
            providers=_providers(GatedASRProvider(gate, "first delivery")),
        )

    thread = threading.Thread(target=worker)
    thread.start()
    _wait_until(lambda: _job(job_id).state == JobState.RUNNING)
    # the broker redelivers (acknowledgement lost) while the attempt is live
    during = process_job(job_id, worker_id="worker-dup", settings=settings, providers=_providers())
    assert during["status"] in {"already_running", "busy"}
    gate.set()
    thread.join(timeout=DEADLINE_SECONDS)
    assert result["value"]["status"] in {"completed", "partial"}
    # ... and again after completion
    after = process_job(job_id, worker_id="worker-dup2", settings=settings, providers=_providers())
    assert after == {"job_id": job_id, "status": "completed"}

    job = _job(job_id)
    assert job.attempt_count == 1
    with inspection_session_factory()() as db:
        texts = list(db.scalars(select(Recording.id).where(Recording.meeting_id == job.meeting_id)))
        assert len(texts) == 1
    assert sum(1 for e in _events(workspace_id) if e.event_type == "committed") == 1
    assert _violations(workspace_id, settings) == []


def test_object_delete_failure_after_success_keeps_results_and_the_single_charge(
    client, email_outbox, monkeypatch, settings, broker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        workspace.delete_audio_after_processing = True
        db.commit()
    storage = FaultyStorage(create_object_storage(settings), fail_deletes=True)
    result = process_job(
        job_id, worker_id="worker", settings=settings, providers=_providers(), storage=storage
    )
    assert result["status"] in {"completed", "partial"}
    assert storage.delete_attempts == 1
    job = _job(job_id)
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == job.meeting_id))
        assert recording is not None and recording.deleted_at is None  # nothing was pretended
        assert storage.exists(workspace_id, recording.storage_key)
    assert job.state == JobState.COMPLETED
    assert sum(1 for e in _events(workspace_id) if e.event_type == "committed") == 1
    assert _violations(workspace_id, settings) == []


def test_invariant_checker_reports_corruption_and_the_summary_counts_it(
    client, email_outbox, monkeypatch, settings, broker
):
    done_id, done_workspace = _queued_upload(client, email_outbox, monkeypatch)
    assert process_job(done_id, worker_id="w", settings=settings, providers=_providers())[
        "status"
    ] in {"completed", "partial"}
    queued_id, queued_workspace = _queued_upload(client, email_outbox, monkeypatch)
    for workspace_id in (done_workspace, queued_workspace):
        assert _violations(workspace_id, settings) == []
    assert invariants.check_all(settings)["violations"] == 0

    with inspection_session_factory()() as db:
        # a settled reservation that "forgot" its settlement, and a queued job
        # whose only delivery path was withdrawn
        done = db.get(ProcessingJob, done_id)
        reservation = db.get(UsageReservation, done.reservation_id)
        reservation.state = ReservationState.RESERVED
        dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == queued_id))
        dispatch.state = DispatchState.CANCELLED
        db.commit()

    done_rules = sorted(v.rule for v in _violations(done_workspace, settings))
    assert done_rules == ["ledger_counters", "reservations_match_state"]
    queued_rules = [v.rule for v in _violations(queued_workspace, settings)]
    assert queued_rules == ["queued_delivery_path"]
    summary = invariants.check_all(settings)
    assert summary["workspaces"] == 2 and summary["violations"] == 3
    assert summary["by_rule"] == {
        "ledger_counters": 1,
        "reservations_match_state": 1,
        "queued_delivery_path": 1,
    }
    assert {item["job_id"] for item in summary["items"] if item["job_id"]} == {done_id, queued_id}


# --- WP06-T03 follow-up: artifact faults inside the worker --------------------


def test_artifact_write_faults_in_the_worker_converge_and_charge_once(
    client, email_outbox, monkeypatch, settings, broker
):
    from backend.database.saas_models import CheckpointState, StageCheckpoint

    jobs = [_queued_upload(client, email_outbox, monkeypatch) for _ in range(2)]
    job_ids = [job_id for job_id, _ in jobs]
    storage = FaultyStorage(create_object_storage(settings), fail_writes=2)
    broker.fail_times = 1

    rounds = _drive(
        job_ids,
        settings=settings,
        broker=broker,
        storage=storage,
        providers=_providers(),
        duplicate_delivery=True,
    )
    assert rounds >= 2 and storage.write_failures == 2
    with inspection_session_factory()() as db:
        rows = db.scalars(select(StageCheckpoint)).all()
        abandoned = [r for r in rows if r.state == CheckpointState.ABANDONED]
        published = [r for r in rows if r.state == CheckpointState.PUBLISHED]
        assert len(abandoned) == 2 and {r.invalidation_reason for r in abandoned} == {
            "write_failed"
        }
        assert len(published) == 3 * len(job_ids)
        for row in published:
            assert storage.exists(row.workspace_id, row.artifact_key)
        for row in abandoned:
            assert not storage.exists(row.workspace_id, row.artifact_key)
    for job_id, workspace_id in jobs:
        job = _job(job_id)
        assert job.state == JobState.COMPLETED and job.failure_class is None
        assert sum(1 for e in _events(workspace_id) if e.event_type == "committed") == 1
        with inspection_session_factory()() as db:
            assert invariants.check_workspace(
                db, workspace_id, settings=settings, storage=storage
            ) == []


def test_invariant_checker_distinguishes_missing_unverified_and_retired_artifacts(
    client, email_outbox, monkeypatch, settings, broker
):
    from backend.database.saas_models import CheckpointState, StageCheckpoint

    storage = FaultyStorage(create_object_storage(settings))
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    assert process_job(job_id, worker_id="w", settings=settings, providers=_providers(),
                       storage=storage)["status"] in {"completed", "partial"}
    with inspection_session_factory()() as db:
        rows = db.scalars(select(StageCheckpoint).where(
            StageCheckpoint.workspace_id == workspace_id)).all()
        assert len(rows) == 3 and all(r.state == CheckpointState.PUBLISHED for r in rows)
        asr = next(r for r in rows if r.stage == "asr")
        asr_key = asr.artifact_key
    # without a storage handle nothing is inspected
    summary = invariants.check_all(settings)
    assert summary["violations"] == 0 and summary["artifacts"]["inspected"] is False
    # with storage: all present
    summary = invariants.check_all(settings, storage=storage)
    assert summary["artifacts"] == {"checked": 3, "missing": 0, "unverified": 0,
                                    "orphans": 0, "retired": 0, "inspected": True}
    # a published artifact vanished: a violation, not a guess
    assert storage.delete(workspace_id, asr_key)
    summary = invariants.check_all(settings, storage=storage)
    assert summary["by_rule"] == {"artifact_missing": 1}
    assert summary["artifacts"]["missing"] == 1 and summary["artifacts"]["checked"] == 2
    # storage that does not answer is "unverified", never "missing"
    storage.fail_exists = True
    summary = invariants.check_all(settings, storage=storage)
    assert summary["violations"] == 0
    assert summary["artifacts"]["unverified"] == 3 and summary["artifacts"]["missing"] == 0
    storage.fail_exists = False
    # an intentionally retired object is neither
    with inspection_session_factory()() as db:
        row = db.scalar(select(StageCheckpoint).where(StageCheckpoint.artifact_key == asr_key))
        row.state = CheckpointState.INVALIDATED
        row.invalidation_reason = "artifact_missing"
        row.invalidated_at = row.object_deleted_at = datetime.now(UTC)
        db.commit()
    summary = invariants.check_all(settings, storage=storage)
    assert summary["violations"] == 0
    assert summary["artifacts"]["retired"] == 1 and summary["artifacts"]["checked"] == 2
