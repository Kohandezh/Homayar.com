"""Execution leases, renewal and fencing for processing jobs (WP06-T04).

Ownership model (columns on ``processing_jobs``, no second state machine):

* ``locked_by`` / ``locked_at`` – owner identity and claim time of the
  current attempt (the attempt deadline is anchored on ``locked_at``);
* ``attempt_id`` – identity of the current attempt;
* ``lease_generation`` – fencing token; advances on every claim and on every
  recovery fence, never decreases;
* ``lease_expires_at`` / ``lease_renewed_at`` – lease and heartbeat state;
* ``attempt_count`` / ``max_attempts`` – bounded processing attempts.

Rules
-----
* A claim is atomic: the row is locked ``FOR UPDATE``, the verdict is taken
  against **database time**, and the new generation is written in the same
  transaction.  Competing claimants see ``already_running`` or ``busy``.
* Authority = ``state = RUNNING`` **and** the generation the worker was
  handed **and** the same owner **and** an unexpired lease.  Every protected
  write (stage transition, results, completion, failure, usage settlement)
  re-locks the row and checks all four inside the writing transaction, so a
  stale worker is rejected before the first row is touched and nothing
  partial is committed.
* Renewal obeys the same rule.  An expired lease is not renewed even if
  nobody has taken the job yet; reacquisition only happens through the
  recovery fence (``backend.jobs.recovery``) and a fresh claim, which advance
  the generation.  Renewal also stops at the attempt deadline, so a heartbeat
  cannot keep a permanently stuck attempt alive.
* Timing comes from one ``LeasePolicy`` built from settings; no module keeps
  its own constants.

Database time: on PostgreSQL ``now()`` is used for every lease decision, so
worker clocks need not agree.  The SQLite fallback uses the process clock and
is not evidence for any of the guarantees above.
"""

from __future__ import annotations

import enum
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import JobState, ProcessingJob
from backend.database.tenant_context import bind_job_context, bind_tenant_context
from backend.services.logging_service import logger

TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.DEAD_LETTER}
)


@dataclass(frozen=True)
class LeasePolicy:
    lease: timedelta
    renew_interval: timedelta
    stage_timeout: timedelta
    attempt_timeout: timedelta
    retry_delay: timedelta
    recovery_batch_size: int

    @classmethod
    def from_settings(cls, settings: Settings) -> LeasePolicy:
        return cls(
            lease=timedelta(seconds=settings.job_lease_seconds),
            renew_interval=timedelta(seconds=settings.job_lease_renew_interval_seconds),
            stage_timeout=timedelta(seconds=settings.job_stage_timeout_seconds),
            attempt_timeout=timedelta(seconds=settings.job_attempt_timeout_seconds),
            retry_delay=timedelta(seconds=settings.job_retry_delay_seconds),
            recovery_batch_size=settings.job_recovery_batch_size,
        )


@dataclass(frozen=True)
class Lease:
    """What a worker holds after a successful claim. Immutable; never forged."""

    job_id: str
    workspace_id: str
    worker_id: str
    generation: int
    attempt_id: str
    attempt: int
    expires_at: datetime


class LeaseVerdict(str, enum.Enum):
    CLAIMABLE = "claimable"
    ALREADY_RUNNING = "already_running"
    LEASE_EXPIRED = "lease_expired"  # abandoned attempt; only recovery may fence it
    NOT_DUE = "not_due"
    DEADLINE_EXCEEDED = "deadline_exceeded"  # the job's overall deadline passed (WP06-T05)
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    TERMINAL = "terminal"


class RenewalOutcome(str, enum.Enum):
    RENEWED = "renewed"
    NOT_FOUND = "not_found"
    NOT_RUNNING = "not_running"  # cancelled/finished/requeued meanwhile
    SUPERSEDED = "superseded"  # another generation or owner holds the job
    EXPIRED = "expired"  # our lease lapsed before this renewal
    DEADLINE_EXCEEDED = "deadline_exceeded"  # attempt timeout reached


class StaleLeaseError(RuntimeError):
    """The caller no longer owns the job; nothing was written."""

    code = "job_claim_lost"

    def __init__(self, outcome: RenewalOutcome) -> None:
        super().__init__(f"The processing job is no longer owned by this worker ({outcome.value})")
        self.outcome = outcome
        self.public_message = "The processing job is no longer owned by this worker"


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def database_now(db: Session) -> datetime:
    """Authoritative time for lease decisions (server clock on PostgreSQL)."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        value = db.execute(text("SELECT now()")).scalar_one()
        return as_utc(value)  # type: ignore[return-value]
    return datetime.now(UTC)


def worker_id_for(value: str) -> str:
    return value[:120]


def claim_verdict(job: ProcessingJob, *, now: datetime) -> LeaseVerdict:
    if job.state in TERMINAL_STATES:
        return LeaseVerdict.TERMINAL
    if job.state == JobState.RUNNING:
        expires = as_utc(job.lease_expires_at)
        if expires is not None and expires > now:
            return LeaseVerdict.ALREADY_RUNNING
        return LeaseVerdict.LEASE_EXPIRED
    retry_after = as_utc(job.retry_after)
    if retry_after is not None and retry_after > now:
        return LeaseVerdict.NOT_DUE
    deadline = as_utc(job.deadline_at)
    if deadline is not None and deadline <= now:
        return LeaseVerdict.DEADLINE_EXCEEDED
    if job.attempt_count >= job.max_attempts:
        return LeaseVerdict.ATTEMPTS_EXHAUSTED
    return LeaseVerdict.CLAIMABLE


def lease_expiry(policy: LeasePolicy, *, now: datetime, attempt_started_at: datetime) -> datetime:
    """Next expiry: one lease from now, never past the attempt deadline."""
    return min(now + policy.lease, attempt_started_at + policy.attempt_timeout)


def grant_lease(
    job: ProcessingJob, *, worker_id: str, policy: LeasePolicy, now: datetime
) -> Lease:
    """Write the new attempt onto a row the caller holds ``FOR UPDATE``.

    The caller has already taken ``claim_verdict`` == CLAIMABLE in the same
    transaction; this only records the ownership.
    """
    owner = worker_id_for(worker_id)
    job.state = JobState.RUNNING
    job.stage = "processing"
    job.attempt_count += 1
    job.lease_generation += 1
    job.attempt_id = str(uuid.uuid4())
    job.locked_by = owner
    job.locked_at = now
    job.lease_renewed_at = now
    job.lease_expires_at = lease_expiry(policy, now=now, attempt_started_at=now)
    job.started_at = job.started_at or now
    job.retry_after = None
    job.failure_code = None
    job.failure_message = None
    job.failure_class = None
    return Lease(
        job_id=job.id,
        workspace_id=job.workspace_id,
        worker_id=owner,
        generation=job.lease_generation,
        attempt_id=job.attempt_id,
        attempt=job.attempt_count,
        expires_at=job.lease_expires_at,
    )


def ownership_outcome(job: ProcessingJob | None, lease: Lease, *, now: datetime) -> RenewalOutcome:
    """Why ``lease`` does (RENEWED) or does not confer authority over ``job`` now."""
    if job is None:
        return RenewalOutcome.NOT_FOUND
    if job.state != JobState.RUNNING:
        return RenewalOutcome.NOT_RUNNING
    if job.lease_generation != lease.generation or job.locked_by != lease.worker_id:
        return RenewalOutcome.SUPERSEDED
    expires = as_utc(job.lease_expires_at)
    if expires is None or expires <= now:
        return RenewalOutcome.EXPIRED
    return RenewalOutcome.RENEWED


def owned_job(db: Session, lease: Lease) -> tuple[ProcessingJob, datetime]:
    """Lock the job row and prove ``lease`` still owns it, or raise.

    Must be the first statement of every transaction that commits a protected
    change; the row lock is held until that transaction ends, so a recovery
    fence or a competing claim cannot interleave.
    """
    job = db.scalar(
        select(ProcessingJob).where(ProcessingJob.id == lease.job_id).with_for_update()
    )
    now = database_now(db)
    outcome = ownership_outcome(job, lease, now=now)
    if outcome != RenewalOutcome.RENEWED:
        raise StaleLeaseError(outcome)
    assert job is not None
    return job, now


def lock_owned_or_cancelled(db: Session, lease: Lease) -> tuple[ProcessingJob | None, datetime]:
    """Lock the job; return it when owned, ``None`` when cancelled, else raise.

    Cancellation (meeting deleted while the attempt ran) is a legitimate end
    of the attempt and is reported as such; every other loss of ownership
    means a newer attempt or the recovery fence took over and we are stale.
    """
    job = db.scalar(
        select(ProcessingJob).where(ProcessingJob.id == lease.job_id).with_for_update()
    )
    now = database_now(db)
    outcome = ownership_outcome(job, lease, now=now)
    if outcome == RenewalOutcome.RENEWED:
        return job, now
    if (
        outcome == RenewalOutcome.NOT_RUNNING
        and job is not None
        and job.state == JobState.CANCELLED
        and job.lease_generation == lease.generation
    ):
        return None, now
    raise StaleLeaseError(outcome)


def release_lease(job: ProcessingJob) -> None:
    """Drop ownership columns on a row the caller owns (state set by caller)."""
    job.locked_by = None
    job.lease_expires_at = None
    job.lease_renewed_at = None
    job.attempt_id = None


def renew_lease(
    lease: Lease,
    policy: LeasePolicy,
    *,
    session_factory=None,
) -> RenewalOutcome:
    """Extend the lease if, and only if, ``lease`` is still the valid owner."""
    if session_factory is None:
        from backend.database.relational import get_session_factory

        session_factory = get_session_factory()
    with session_factory() as db:
        bind_job_context(db, lease.job_id)
        bind_tenant_context(db, workspace_id=lease.workspace_id)
        job = db.scalar(
            select(ProcessingJob).where(ProcessingJob.id == lease.job_id).with_for_update()
        )
        now = database_now(db)
        outcome = ownership_outcome(job, lease, now=now)
        if outcome != RenewalOutcome.RENEWED:
            db.rollback()
            return outcome
        assert job is not None
        started = as_utc(job.locked_at) or now
        if started + policy.attempt_timeout <= now:
            db.rollback()
            return RenewalOutcome.DEADLINE_EXCEEDED
        job.lease_expires_at = lease_expiry(policy, now=now, attempt_started_at=started)
        job.lease_renewed_at = now
        db.commit()
        return RenewalOutcome.RENEWED


class LeaseHeartbeat:
    """Background renewal for one attempt.

    Renewal failures that are *verdicts* (superseded, expired, not running,
    deadline) set ``lost`` and stop the loop; the processing thread checks
    ``lost`` at every stage boundary and every protected write re-checks
    ownership anyway.  Transient database errors are logged and retried on
    the next tick — the lease simply expires if they persist.  A stuck
    provider call cannot be interrupted from here; it is bounded by the stage
    timeout, the attempt deadline and, ultimately, the worker time limit.
    """

    def __init__(self, lease: Lease, policy: LeasePolicy, *, session_factory=None) -> None:
        self.lease = lease
        self.policy = policy
        self._session_factory = session_factory
        self._stop = threading.Event()
        self.lost = threading.Event()
        self.outcome: RenewalOutcome | None = None
        self.renewals = 0
        self._thread = threading.Thread(
            target=self._run, name=f"lease-heartbeat-{lease.job_id[:8]}", daemon=True
        )

    def start(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.policy.renew_interval.total_seconds()))

    def _run(self) -> None:
        interval = self.policy.renew_interval.total_seconds()
        while not self._stop.wait(interval):
            try:
                outcome = renew_lease(
                    self.lease, self.policy, session_factory=self._session_factory
                )
            except Exception as exc:
                logger.warning(
                    "JOB_LEASE_RENEWAL_ERROR job_id=%s error_type=%s",
                    self.lease.job_id,
                    type(exc).__name__,
                )
                continue
            if outcome == RenewalOutcome.RENEWED:
                self.renewals += 1
                continue
            self.outcome = outcome
            self.lost.set()
            logger.warning(
                "JOB_LEASE_LOST job_id=%s generation=%s outcome=%s",
                self.lease.job_id,
                self.lease.generation,
                outcome.value,
            )
            return
