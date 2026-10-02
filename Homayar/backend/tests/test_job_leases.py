"""WP06-T04: execution leases, renewal, fencing and abandoned-attempt recovery.

Locking, fencing and transactional guarantees are only evidence on PostgreSQL
(``postgres`` marker); the SQLite fallback runs the sequential cases for
shape, not for isolation.  Concurrency is bounded with events and deadlines,
never long sleeps.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from backend.config import REVEAL_SECRETS_CONTEXT, Settings
from backend.database.saas_models import (
    AuditEvent,
    DispatchState,
    Entitlement,
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    ProcessingStageRun,
    Recording,
    StageState,
    TranscriptSegment,
    UsageEvent,
    Workspace,
)
from backend.jobs import processor, recovery
from backend.jobs.leases import (
    Lease,
    LeasePolicy,
    RenewalOutcome,
    StaleLeaseError,
    renew_lease,
)
from backend.jobs.processor import ProcessingError, process_job
from backend.jobs.states import StageName, StageRecord
from backend.jobs.tasks import process_meeting_job
from backend.providers.base import ASRProvider
from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRRequest,
    ASRResult,
    TranscriptTurn,
)
from backend.security.context import CSRF_COOKIE
from backend.storage import create_object_storage
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_job_processor import _providers, _queued_upload

DEADLINE_SECONDS = 15.0


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    """Deterministic publisher: recovery re-arms intents and publishes through it."""
    from backend.jobs import outbox

    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


# --- helpers -----------------------------------------------------------------


def _job(job_id: str) -> ProcessingJob:
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        _ = job.dispatch  # load before expunge
        db.expunge(job)
        return job


def _claim(job_id: str, worker: str, settings: Settings) -> processor.ClaimedJob:
    claim, status = processor._claim_job(job_id, worker_id=worker, settings=settings)
    assert status == "claimed", status
    assert claim is not None
    return claim


def _expire_lease(job_id: str) -> None:
    """Simulate a worker that stopped renewing (DBA write, no sleeping)."""
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state == JobState.RUNNING
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()


def _wait_until(predicate: Callable[[], bool], *, timeout: float = DEADLINE_SECONDS) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=timeout)
    pause = threading.Event()
    while not predicate():
        assert datetime.now(UTC) < deadline, "condition not reached before the deadline"
        pause.wait(0.05)


def _asr(text_value: str) -> ASRResult:
    return ASRResult(provider="lease-test", model="m", language="fa", text=text_value)


def _completed_records() -> list[StageRecord]:
    return [
        StageRecord(stage=StageName.MATERIALIZE).finish(StageState.COMPLETED),
        StageRecord(stage=StageName.ASR).finish(StageState.COMPLETED, provider="lease-test"),
    ]


def _persist(claim: processor.ClaimedJob, settings: Settings, text_value: str) -> str:
    return processor._persist_success(
        claim,
        asr=_asr(text_value),
        diarization=None,
        intelligence=None,
        turns=[TranscriptTurn(text=text_value, speaker_label=UNKNOWN_SPEAKER_LABEL)],
        records=_completed_records(),
        settings=settings,
    )


def _count(model) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count(model.id)))


def _settlements() -> int:
    """Billable settlements only: the ledger also records reserve/release rows (WP06-T06)."""
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count(UsageEvent.id)).where(UsageEvent.event_type == "committed")
        )


def _forged(lease: Lease, **changes) -> Lease:
    data = {
        "job_id": lease.job_id,
        "workspace_id": lease.workspace_id,
        "worker_id": lease.worker_id,
        "generation": lease.generation,
        "attempt_id": lease.attempt_id,
        "attempt": lease.attempt,
        "expires_at": lease.expires_at,
    }
    data.update(changes)
    return Lease(**data)


def _job_payload(client, workspace_id: str, meeting_id: str) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/job", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


class GatedASRProvider(ASRProvider):
    """Blocks inside the ASR stage until released (simulates a long/partitioned worker)."""

    def __init__(self, gate: threading.Event, transcript: str) -> None:
        self.gate = gate
        self.transcript = transcript

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.gate.wait, DEADLINE_SECONDS)
        return _asr(self.transcript)


class SleepingASRProvider(ASRProvider):
    async def transcribe(self, request: ASRRequest) -> ASRResult:
        await asyncio.sleep(DEADLINE_SECONDS)
        return _asr("too late")


# --- claims ------------------------------------------------------------------


def test_claim_grants_a_lease_with_a_generation(client, email_outbox, monkeypatch, settings):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    before = _job(job_id)
    assert before.lease_generation == 0 and before.lease_expires_at is None

    claim = _claim(job_id, "worker-a", settings)
    job = _job(job_id)
    assert job.state == JobState.RUNNING
    assert (job.lease_generation, job.attempt_count) == (1, 1)
    assert job.locked_by == "worker-a" and job.attempt_id == claim.lease.attempt_id
    assert job.lease_expires_at is not None and job.lease_renewed_at is not None
    assert job.lease_expires_at > job.lease_renewed_at

    # a second delivery meets a healthy lease
    _, status = processor._claim_job(job_id, worker_id="worker-b", settings=settings)
    assert status == "already_running"
    assert process_job(job_id, worker_id="worker-c", settings=settings, providers=_providers()) == {
        "job_id": job_id,
        "status": "already_running",
    }
    payload = _job_payload(client, workspace_id, job.meeting_id)
    assert payload["state"] == "running"
    assert payload["lease"]["generation"] == 1
    assert payload["lease"]["attempt_id"] == claim.lease.attempt_id
    assert payload["lease"]["expires_at"] is not None


@pytest.mark.postgres
def test_competing_claims_grant_exactly_one_lease(client, email_outbox, monkeypatch, settings):
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    workers = 4
    barrier = threading.Barrier(workers, timeout=DEADLINE_SECONDS)
    outcomes: dict[str, str] = {}

    def attempt(name: str) -> None:
        barrier.wait()
        _, status = processor._claim_job(job_id, worker_id=name, settings=settings)
        outcomes[name] = status

    threads = [threading.Thread(target=attempt, args=(f"claimant-{i}",)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=DEADLINE_SECONDS)
    assert len(outcomes) == workers
    assert sum(1 for status in outcomes.values() if status == "claimed") == 1
    assert set(outcomes.values()) <= {"claimed", "already_running", "busy"}
    job = _job(job_id)
    assert (job.lease_generation, job.attempt_count) == (1, 1)
    assert job.locked_by == next(n for n, s in outcomes.items() if s == "claimed")


# --- renewal -----------------------------------------------------------------


def test_renewal_only_for_the_current_owner_generation_and_unexpired_lease(
    client, email_outbox, monkeypatch, settings
):
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    policy = LeasePolicy.from_settings(settings)
    claim = _claim(job_id, "worker-a", settings)
    first_expiry = _job(job_id).lease_expires_at

    assert renew_lease(claim.lease, policy) == RenewalOutcome.RENEWED
    renewed = _job(job_id)
    assert renewed.lease_expires_at >= first_expiry
    assert renewed.lease_renewed_at >= renewed.locked_at

    # another owner, or a guessed generation, never renews
    other_owner = _forged(claim.lease, worker_id="worker-b")
    assert renew_lease(other_owner, policy) == RenewalOutcome.SUPERSEDED
    next_generation = _forged(claim.lease, generation=claim.lease.generation + 1)
    assert renew_lease(next_generation, policy) == RenewalOutcome.SUPERSEDED
    assert _job(job_id).lease_expires_at == renewed.lease_expires_at

    # an expired lease is not resurrected, even before anyone recovers it
    _expire_lease(job_id)
    expired_at = _job(job_id).lease_expires_at
    assert renew_lease(claim.lease, policy) == RenewalOutcome.EXPIRED
    assert _job(job_id).lease_expires_at == expired_at
    assert _job(job_id).locked_by == "worker-a"  # recovery, not renewal, changes ownership


def test_renewal_stops_at_the_attempt_deadline(client, email_outbox, monkeypatch, settings):
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    policy = LeasePolicy.from_settings(settings)
    claim = _claim(job_id, "worker-a", settings)
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        job.locked_at = datetime.now(UTC) - policy.attempt_timeout - timedelta(seconds=1)
        db.commit()
    assert renew_lease(claim.lease, policy) == RenewalOutcome.DEADLINE_EXCEEDED
    # the lease itself keeps its expiry and lapses on its own -> recovery
    assert _job(job_id).state == JobState.RUNNING


@pytest.mark.postgres
def test_heartbeat_renews_a_long_running_stage(client, email_outbox, monkeypatch, settings):
    fast = settings.model_copy(
        update={"job_lease_seconds": 4, "job_lease_renew_interval_seconds": 1}
    )
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    gate = threading.Event()
    result: dict[str, dict] = {}

    def run() -> None:
        result["value"] = process_job(
            job_id,
            worker_id="heartbeat-worker",
            settings=fast,
            providers=_providers(GatedASRProvider(gate, "kept alive")),
        )

    thread = threading.Thread(target=run)
    thread.start()
    _wait_until(lambda: _job(job_id).state == JobState.RUNNING)
    claimed_at = _job(job_id).locked_at
    # two renewals observed while the provider is still blocked
    _wait_until(lambda: (_job(job_id).lease_renewed_at - claimed_at) >= timedelta(seconds=2))
    assert _job(job_id).state == JobState.RUNNING
    assert recovery.sweep_expired_leases(fast) == {"scanned": 0}  # healthy: never reclaimed
    gate.set()
    thread.join(timeout=DEADLINE_SECONDS)
    assert result["value"]["status"] == "completed"
    job = _job(job_id)
    assert job.state == JobState.COMPLETED and job.lease_generation == 1
    assert job.locked_by is None and job.lease_expires_at is None


# --- recovery ----------------------------------------------------------------


def test_recovery_fences_the_expired_attempt_and_requeues_through_the_outbox(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    policy = LeasePolicy.from_settings(settings)
    claim_a = _claim(job_id, "worker-a", settings)
    processor._set_stage(claim_a, stage="transcribing")

    # healthy leases are ignored
    assert recovery.sweep_expired_leases(settings) == {"scanned": 0}
    assert _job(job_id).lease_generation == 1

    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings) == {"scanned": 1, "recovered": 1}
    job = _job(job_id)
    assert job.state == JobState.QUEUED and job.stage == "recovery_scheduled"
    assert job.lease_generation == 2 and job.attempt_count == 1
    assert job.locked_by is None and job.lease_expires_at is None and job.attempt_id is None
    assert job.failure_code == "lease_expired" and job.retry_after is not None
    # one intent for the new generation, published by the regular publisher
    assert job.dispatch.generation == 2 and job.dispatch.state == DispatchState.PUBLISHED
    assert broker.published == [(job_id, workspace_id)]
    with inspection_session_factory()() as db:
        assert db.get(Meeting, job.meeting_id).status == MeetingStatus.QUEUED
        runs = {
            r.stage: (r.state.value, r.reason_code)
            for r in db.scalars(
                select(ProcessingStageRun).where(
                    ProcessingStageRun.job_id == job_id, ProcessingStageRun.attempt == 1
                )
            )
        }
        assert runs == {
            "materialize": ("completed", None),
            "asr": ("failed", "lease_expired"),
            "diarization": ("skipped", "not_reached"),
            "intelligence": ("skipped", "not_reached"),
        }
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.lease_expired")
        )
        assert audit is not None
        assert (audit.details["generation"], audit.details["next_generation"]) == (1, 2)
    payload = _job_payload(client, workspace_id, job.meeting_id)
    assert payload["state"] == "queued" and payload["failure_code"] == "lease_expired"
    assert payload["lease"]["generation"] == 2 and payload["dispatch"]["generation"] == 2

    # the old owner is fenced: it cannot renew or transition the job
    assert renew_lease(claim_a.lease, policy) == RenewalOutcome.NOT_RUNNING
    with pytest.raises(StaleLeaseError):
        processor._set_stage(claim_a, stage="diarizing")

    # the new claim advances the generation again; the old lease stays fenced
    _claim(job_id, "worker-b", settings)
    assert _job(job_id).lease_generation == 3
    assert renew_lease(claim_a.lease, policy) == RenewalOutcome.SUPERSEDED

    # duplicate scans for an old generation are inert; a healthy lease is left alone
    stale_candidate = recovery.ExpiredLease(job_id, workspace_id, generation=1)
    assert recovery.recover_expired_lease(stale_candidate, settings).state == "superseded"
    healthy = recovery.ExpiredLease(job_id, workspace_id, generation=3)
    assert recovery.recover_expired_lease(healthy, settings).state == "healthy"
    assert _job(job_id).lease_generation == 3 and len(broker.published) == 1


def test_stale_claim_is_rejected_at_every_protected_write(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    with inspection_session_factory()() as db:
        db.get(Workspace, workspace_id).delete_audio_after_processing = True
        db.commit()
    claim_a = _claim(job_id, "worker-a", settings)
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings)["recovered"] == 1
    claim_b = _claim(job_id, "worker-b", settings)
    storage = create_object_storage(settings)

    # stale results, terminal status, failure record and usage settlement
    with pytest.raises(StaleLeaseError):
        _persist(claim_a, settings, "stale transcript")
    assert _count(TranscriptSegment) == 0 and _settlements() == 0
    error = ProcessingError("asr_temporarily_unavailable", "later", retryable=True)
    assert processor._finalize_failure(claim_a, error=error, settings=settings) == "superseded"
    job = _job(job_id)
    assert job.state == JobState.RUNNING and job.locked_by == "worker-b"
    assert job.lease_generation == claim_b.lease.generation == 3
    with inspection_session_factory()() as db:
        assert db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.event_type.in_(
                    ["meeting.processing.failed", "meeting.processing.retry_scheduled"]
                )
            )
        ) == 0

    # the current owner commits; the stale owner may not delete the audio afterwards
    assert _persist(claim_b, settings, "authoritative transcript") == "completed"
    processor._delete_audio_after_success(claim_a, storage=storage, settings=settings)
    assert storage.exists(workspace_id, claim_a.recording_key)
    processor._delete_audio_after_success(claim_b, storage=storage, settings=settings)
    assert not storage.exists(workspace_id, claim_b.recording_key)
    with inspection_session_factory()() as db:
        segments = list(db.scalars(select(TranscriptSegment.text)))
        assert segments == ["authoritative transcript"]
        assert (
            db.scalar(
                select(func.count(UsageEvent.id)).where(UsageEvent.event_type == "committed")
            )
            == 1
        )
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        assert (audio.used_value, audio.reserved_value) == (13, 0)
        recording = db.scalar(select(Recording).where(Recording.meeting_id == job.meeting_id))
        assert recording.deleted_at is not None
    # even a stale *success* after completion changes nothing
    with pytest.raises(StaleLeaseError):
        _persist(claim_a, settings, "stale again")
    assert _settlements() == 1 and _job(job_id).state == JobState.COMPLETED


@pytest.mark.postgres
def test_stale_worker_resuming_after_recovery_cannot_overwrite_the_new_attempt(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    gate_a = threading.Event()
    result_a: dict[str, dict] = {}

    def worker_a() -> None:
        result_a["value"] = process_job(
            job_id,
            worker_id="worker-a",
            settings=settings,
            providers=_providers(GatedASRProvider(gate_a, "transcript from A")),
        )

    thread = threading.Thread(target=worker_a)
    thread.start()
    _wait_until(lambda: _job(job_id).stage == "transcribing")
    # A stops renewing (partition / pause); the sweeper fences it and requeues
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings)["recovered"] == 1
    result_b = process_job(
        job_id,
        worker_id="worker-b",
        settings=settings,
        providers=_providers(),  # "پیگیری قرارداد انجام شود"
    )
    assert result_b["status"] == "completed"
    # A resumes with its provider result in hand: rejected at the fence
    gate_a.set()
    thread.join(timeout=DEADLINE_SECONDS)
    assert result_a["value"] == {"job_id": job_id, "status": "superseded", "code": "job_claim_lost"}

    job = _job(job_id)
    assert job.state == JobState.COMPLETED and job.attempt_count == 2
    assert job.lease_generation == 3 and job.locked_by is None
    with inspection_session_factory()() as db:
        assert list(db.scalars(select(TranscriptSegment.text))) == ["پیگیری قرارداد انجام شود"]
        assert (
            db.scalar(
                select(func.count(UsageEvent.id)).where(UsageEvent.event_type == "committed")
            )
            == 1
        )
        completed = db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.event_type == "meeting.processing.completed"
            )
        )
        assert completed == 1
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        assert (audio.used_value, audio.reserved_value) == (13, 0)
    # an old message for the finished job is inert
    assert process_meeting_job.run(job_id, workspace_id=workspace_id) == {
        "job_id": job_id,
        "status": "completed",
    }
    assert _job(job_id).attempt_count == 2


def test_old_message_arriving_during_the_new_attempt_is_inert(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    _claim(job_id, "worker-a", settings)
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings)["recovered"] == 1
    _claim(job_id, "worker-b", settings)
    # the redelivered/late message of attempt 1 finds B's healthy lease
    assert process_meeting_job.run(job_id, workspace_id=workspace_id) == {
        "job_id": job_id,
        "status": "already_running",
    }
    job = _job(job_id)
    assert (job.attempt_count, job.lease_generation, job.locked_by) == (2, 3, "worker-b")


def test_deletion_racing_renewal_and_recovery(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    policy = LeasePolicy.from_settings(settings)
    claim = _claim(job_id, "worker-a", settings)
    deleted = client.delete(
        f"/api/v1/meetings/{claim.meeting_id}",
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
    )
    assert deleted.status_code == 204, deleted.text
    job = _job(job_id)
    assert job.state == JobState.CANCELLED and job.locked_by is None
    assert job.lease_expires_at is None  # a cancelled job holds no lease

    assert renew_lease(claim.lease, policy) == RenewalOutcome.NOT_RUNNING
    assert recovery.sweep_expired_leases(settings) == {"scanned": 0}
    candidate = recovery.ExpiredLease(job_id, workspace_id, generation=claim.lease.generation)
    assert recovery.recover_expired_lease(candidate, settings).state == "not_running"
    # the worker's own end-of-attempt writes see the cancellation and stop
    assert _persist(claim, settings, "after deletion") == "cancelled"
    error = ProcessingError("processing_failed", "boom", retryable=True)
    assert processor._finalize_failure(claim, error=error, settings=settings) == "cancelled"
    assert _count(TranscriptSegment) == 0 and _settlements() == 0
    job = _job(job_id)
    assert job.state == JobState.CANCELLED and job.dispatch.state == DispatchState.CANCELLED
    with inspection_session_factory()() as db:
        reserved = db.scalar(
            select(func.sum(Entitlement.reserved_value)).where(
                Entitlement.workspace_id == workspace_id
            )
        )
        assert reserved == 0
    assert broker.published == []


def test_recovery_parks_the_job_when_attempts_are_exhausted(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    assert _job(job_id).max_attempts == 3
    states = []
    for attempt in range(1, 4):
        _claim(job_id, f"worker-{attempt}", settings)
        _expire_lease(job_id)
        counts = recovery.sweep_expired_leases(settings)
        states.append(next(k for k in counts if k != "scanned"))
    assert states == ["recovered", "recovered", "exhausted"]
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.attempt_count == 3
    assert job.failure_code == "lease_expired" and job.completed_at is not None
    assert job.lease_generation == 6 and job.locked_by is None
    # no new intent for the parked job; the last one was for generation 4
    assert job.dispatch.generation == 4 and job.dispatch.state == DispatchState.PUBLISHED
    assert len(broker.published) == 2
    with inspection_session_factory()() as db:
        assert db.get(Meeting, job.meeting_id).status == MeetingStatus.FAILED
        entitlements = list(
            db.scalars(select(Entitlement).where(Entitlement.workspace_id == workspace_id))
        )
        assert entitlements and all(e.reserved_value == 0 for e in entitlements)
        assert all(e.used_value == 0 for e in entitlements)  # nothing was delivered
        failed = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.failed")
        )
        # (audit details redact *_code values; the reason is asserted on the job above)
        assert failed is not None and failed.details["attempt"] == 3
        assert failed.details["generation"] == 5 and failed.details["retryable"] is False
    # late deliveries find the terminal state
    assert process_job(job_id, worker_id="late", settings=settings, providers=_providers()) == {
        "job_id": job_id,
        "status": "dead_letter",
    }
    assert recovery.sweep_expired_leases(settings) == {"scanned": 0}
    payload = _job_payload(client, workspace_id, job.meeting_id)
    assert payload["state"] == "dead_letter" and payload["failure_code"] == "lease_expired"


def test_stage_timeout_requeues_through_the_outbox(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    bounded = settings.model_copy(
        update={"job_stage_timeout_seconds": 1, "job_attempt_timeout_seconds": 60}
    )
    job_id, _ = _queued_upload(client, email_outbox, monkeypatch)
    started = datetime.now(UTC)
    result = process_job(
        job_id,
        worker_id="slow-worker",
        settings=bounded,
        providers=_providers(SleepingASRProvider()),
    )
    assert datetime.now(UTC) - started < timedelta(seconds=DEADLINE_SECONDS)
    assert result == {"job_id": job_id, "status": "retry_scheduled", "code": "stage_timeout"}
    job = _job(job_id)
    assert job.state == JobState.QUEUED and job.stage == "retry_scheduled"
    assert job.locked_by is None and job.retry_after is not None
    assert job.dispatch.state == DispatchState.PENDING
    assert job.dispatch.generation == job.lease_generation == 1
    assert job.dispatch.next_attempt_at == job.retry_after
    assert broker.published == []  # not due yet: nothing published


@pytest.mark.postgres
def test_recovery_scope_reads_lease_columns_only_and_never_leaks(
    client, email_outbox, monkeypatch, settings, broker: FakeBroker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("RLS scope test needs PostgreSQL")
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import SERVICE_SCOPE_RECOVERY, bind_service_scope

    job_a, _ = _queued_upload(client, email_outbox, monkeypatch)
    job_b, _ = _queued_upload(client, email_outbox, monkeypatch)
    _claim(job_a, "worker-a", settings)
    _expire_lease(job_a)
    factory = get_session_factory()  # application engine: homayar_app role
    with factory() as db:
        assert db.scalar(select(func.count(ProcessingJob.id))) == 0  # no context
        bind_service_scope(db, SERVICE_SCOPE_RECOVERY)
        assert set(db.scalars(select(ProcessingJob.id))) == {job_a, job_b}
        for table in (
            "meetings",
            "recordings",
            "transcript_segments",
            "usage_reservations",
            "job_dispatches",
            "audit_events",
        ):
            # (platform-level audit rows without a workspace are visible by design)
            count = db.execute(
                text(f"SELECT count(*) FROM {table} WHERE workspace_id IS NOT NULL")  # noqa: S608
            ).scalar_one()
            assert count == 0, table
        # discovery is read-only: the scope cannot fence or requeue anything
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE processing_jobs SET lease_generation = lease_generation + 1")
            )
            db.flush()
        db.rollback()
    assert recovery.sweep_expired_leases(settings) == {"scanned": 1, "recovered": 1}
    # neither the scope nor the tenant context survives on a reused connection
    with factory() as db:
        assert db.scalar(select(func.count(ProcessingJob.id))) == 0
        assert db.execute(text("SELECT count(*) FROM job_dispatches")).scalar_one() == 0
        assert db.execute(
            text("SELECT current_setting('homayar.service_scope', true)")
        ).scalar() in (None, "")
        db.rollback()


def test_lease_settings_are_validated(settings):
    with pytest.raises(ValueError):
        Settings(
            **{
                **settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True}),
                "job_lease_seconds": 10,
                "job_lease_renew_interval_seconds": 6,
                "_env_file": None,
            }
        )
    with pytest.raises(ValueError):
        Settings(
            **{
                **settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True}),
                "job_stage_timeout_seconds": 100,
                "job_attempt_timeout_seconds": 50,
                "_env_file": None,
            }
        )
