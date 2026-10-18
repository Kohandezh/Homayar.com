"""Recovery of abandoned RUNNING attempts (WP06-T04).

Responsibilities are split deliberately:

* ``backend.jobs.outbox`` publishes *intents* (initial, retry, recovery) and
  retries publication;
* this module notices RUNNING attempts whose lease lapsed (worker killed,
  network partition, stuck beyond the attempt deadline), **fences** the
  previous owner by advancing the lease generation, and requeues the job by
  re-arming its outbox intent in the same transaction.  It never publishes
  through another path and never touches healthy leases.

Discovery runs under the read-only ``recovery`` service scope (RLS policy on
``processing_jobs`` only: ids, state and lease columns).  Each recovery then
opens its own transaction bound to the job id **and** the workspace read from
the persisted row, locks the row ``FOR UPDATE SKIP LOCKED`` and re-checks
state, generation and expiry against database time — so two sweepers, or a
sweeper racing a worker that is committing under a still-valid lease, cannot
both act: at most one fence per generation, and an owner that commits first
wins (its commit holds the row lock and ends the RUNNING state).

Bounded: an abandoned attempt consumes one of ``max_attempts``; when none
remain the job is parked ``dead_letter`` with ``failure_code = lease_expired``
and its reservations are released.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from sqlalchemy import select

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    StageState,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_RECOVERY,
    bind_job_context,
    bind_service_scope,
    bind_tenant_context,
)
from backend.jobs.failures import (
    JOB_DEADLINE_EXCEEDED,
    LEASE_EXPIRED,
    MEETING_DELETED,
    FailureClass,
    RetryVerdict,
    park_job,
    retry_verdict,
)
from backend.jobs.leases import as_utc, database_now, release_lease
from backend.jobs.outbox import dispatch_job, rearm_dispatch
from backend.jobs.processor import STAGE_BY_POINTER, persist_stage_runs
from backend.jobs.states import STAGE_ORDER, StageRecord
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger


@dataclass(frozen=True)
class ExpiredLease:
    job_id: str
    workspace_id: str
    generation: int


@dataclass(frozen=True)
class RecoveryOutcome:
    job_id: str
    # recovered | exhausted | cancelled | healthy | superseded | not_running |
    # busy | not_found
    state: str
    generation: int | None = None


def discover_expired_leases(*, limit: int) -> list[ExpiredLease]:
    """Ids of RUNNING jobs whose lease lapsed, oldest expiry first (read-only)."""
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_RECOVERY)
        now = database_now(db)
        rows = db.execute(
            select(
                ProcessingJob.id,
                ProcessingJob.workspace_id,
                ProcessingJob.lease_generation,
            )
            .where(
                ProcessingJob.state == JobState.RUNNING,
                ProcessingJob.lease_expires_at <= now,
            )
            .order_by(ProcessingJob.lease_expires_at.asc(), ProcessingJob.id.asc())
            .limit(limit)
        ).all()
        db.rollback()
    return [ExpiredLease(job_id=r[0], workspace_id=r[1], generation=r[2]) for r in rows]


def _abandoned_stage_records(pointer: str) -> list[StageRecord]:
    """Truthful stage rows for an attempt nobody finished.

    The stage pointer only advances after the previous stage completed, so
    earlier stages are ``completed`` (provider unknown), the in-flight one
    ``failed`` with ``lease_expired`` and the rest are added as not reached.
    """
    current = STAGE_BY_POINTER.get(pointer)
    records: list[StageRecord] = []
    if current is None:
        return records
    for stage in STAGE_ORDER:
        record = StageRecord(stage=stage)
        if stage == current:
            record.finish(
                StageState.FAILED,
                reason=LEASE_EXPIRED,
                message="The worker stopped renewing its lease; the attempt was abandoned",
            )
            record.latency_ms = None
            records.append(record)
            break
        record.finish(StageState.COMPLETED)
        record.latency_ms = None
        records.append(record)
    return records


def recover_expired_lease(candidate: ExpiredLease, settings: Settings) -> RecoveryOutcome:
    """Fence one abandoned attempt and requeue (or park) the job."""
    with get_session_factory()() as db:
        bind_job_context(db, candidate.job_id)
        bind_tenant_context(db, workspace_id=candidate.workspace_id)
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == candidate.job_id)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            exists = db.scalar(select(ProcessingJob.id).where(ProcessingJob.id == candidate.job_id))
            db.rollback()
            return RecoveryOutcome(candidate.job_id, "busy" if exists else "not_found")
        now = database_now(db)
        if job.state != JobState.RUNNING:
            db.rollback()
            return RecoveryOutcome(candidate.job_id, "not_running", job.lease_generation)
        if job.lease_generation != candidate.generation:
            db.rollback()
            return RecoveryOutcome(candidate.job_id, "superseded", job.lease_generation)
        expires = as_utc(job.lease_expires_at)
        if expires is None or expires > now:
            db.rollback()
            return RecoveryOutcome(candidate.job_id, "healthy", job.lease_generation)

        abandoned_generation = job.lease_generation
        abandoned_attempt = job.attempt_count
        meeting = db.get(Meeting, job.meeting_id)
        meeting_alive = meeting is not None and meeting.deleted_at is None

        # Record the abandoned attempt, then fence: the old owner's generation
        # is gone the moment this commits, so its later writes are rejected.
        persist_stage_runs(db, job, _abandoned_stage_records(job.stage))
        job.lease_generation += 1
        release_lease(job)
        job.failure_code = LEASE_EXPIRED
        job.failure_message = "The worker stopped renewing its lease; the attempt was abandoned"
        job.failure_class = FailureClass.ABANDONED.value

        details = {
            "failure_code": LEASE_EXPIRED,
            "cause": LEASE_EXPIRED,
            "failure_class": FailureClass.ABANDONED.value,
            "attempt": abandoned_attempt,
            "generation": abandoned_generation,
            "next_generation": job.lease_generation,
        }
        verdict = retry_verdict(job, due=now)
        if not meeting_alive:
            park_job(
                db,
                job,
                None,
                state=JobState.CANCELLED,
                code=MEETING_DELETED,
                message="The meeting was deleted; the abandoned attempt is not retried",
                failure_class=FailureClass.CANCELLED,
                now=now,
            )
            state = "cancelled"
            event_type = "meeting.processing.cancelled"
        elif verdict == RetryVerdict.ALLOWED:
            job.state = JobState.QUEUED
            job.stage = "recovery_scheduled"
            job.retry_after = now
            rearm_dispatch(db, job, due=now, settings=settings)
            assert meeting is not None
            meeting.status = MeetingStatus.QUEUED
            state = "recovered"
            event_type = "meeting.processing.lease_expired"
        else:
            if verdict == RetryVerdict.DEADLINE_EXCEEDED:
                code, failure_class = JOB_DEADLINE_EXCEEDED, FailureClass.DEADLINE
                message = "The worker stopped renewing its lease and the job deadline passed"
            else:
                code, failure_class = LEASE_EXPIRED, FailureClass.ABANDONED
                message = (
                    "The worker stopped renewing its lease and the processing retry "
                    "limit was reached"
                )
            park_job(
                db,
                job,
                meeting,
                state=JobState.DEAD_LETTER,
                code=code,
                message=message,
                failure_class=failure_class,
                now=now,
                release_reason=LEASE_EXPIRED,
            )
            state = "exhausted"
            event_type = "meeting.processing.failed"
            details["retryable"] = False
            details["terminal_reason"] = verdict.value
        record_audit_event(
            db,
            event_type=event_type,
            workspace_id=job.workspace_id,
            target_type="meeting",
            target_id=job.meeting_id,
            outcome="failure",
            details=details,
            settings=settings,
        )
        generation = job.lease_generation
        db.commit()

    logger.warning(
        "JOB_LEASE_RECOVERED job_id=%s attempt=%s generation=%s state=%s",
        candidate.job_id,
        abandoned_attempt,
        abandoned_generation,
        state,
    )
    if state == "recovered":
        # Same publisher as every other intent; if this fails or is skipped
        # the dispatch sweeper picks the PENDING row up.
        dispatch_job(candidate.job_id, settings)
    return RecoveryOutcome(candidate.job_id, state, generation)


def sweep_expired_leases(
    settings: Settings | None = None, *, limit: int | None = None
) -> dict[str, int]:
    """Recover every expired RUNNING attempt (bounded batch). Returns counts."""
    settings = settings or get_settings()
    batch = limit or settings.job_recovery_batch_size
    candidates = discover_expired_leases(limit=batch)
    counts: dict[str, int] = {"scanned": len(candidates)}
    for candidate in candidates:
        outcome = recover_expired_lease(candidate, settings)
        counts[outcome.state] = counts.get(outcome.state, 0) + 1
    if candidates:
        logger.info("JOB_LEASE_SWEEP %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    summary = sweep_expired_leases()
    print(json.dumps(summary))
    sys.exit(1 if summary.get("exhausted") else 0)
