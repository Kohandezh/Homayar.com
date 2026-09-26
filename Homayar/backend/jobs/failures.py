"""Failure classes, bounded retry budgets and terminal parking (WP06-T05).

One vocabulary for *why* a job stopped progressing, persisted on the job row
(``failure_class`` + ``failure_code``) so the API, the audit trail and the
operator runbook agree:

======================  =================================================
``publication``         the outbox could not deliver the job to the broker
``transient``           a retryable execution failure (provider, storage,
                        stage/attempt timeout) — retried with back-off
``permanent``           an input or policy failure; retrying cannot help
``abandoned``           the worker stopped renewing its lease
``deadline``            the job's overall deadline passed before it finished
``cancelled``           the meeting was deleted (or the job cancelled)
======================  =================================================

Terminal representation: ``FAILED`` is final for ``permanent``;
``DEAD_LETTER`` parks everything that *could* have succeeded (publication,
exhausted transient/abandoned attempts, deadline) and is the only state an
operator may replay (``backend.jobs.replay``); ``CANCELLED`` is final.
Parking always releases the job's quota reservations — a parked job never
holds or charges allowance.

Budgets: ``max_attempts`` execution attempts per replay window, exponential
back-off between attempts (``JOB_RETRY_DELAY_SECONDS`` doubling up to
``JOB_RETRY_MAX_DELAY_SECONDS``), one overall ``deadline_at`` per job
(``JOB_DEADLINE_SECONDS`` from creation / replay), and ``JOB_MAX_REPLAYS``
operator replays. Publication attempts (``job_dispatches``) and stage rows
stay separate counters.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    ReservationState,
    UsageReservation,
)
from backend.jobs.leases import release_lease
from backend.services.entitlement_service import release_usage


class FailureClass(str, enum.Enum):
    PUBLICATION = "publication"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    ABANDONED = "abandoned"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"


# Failure codes owned by the policy layer (provider/storage codes pass through).
PUBLICATION_FAILED = "publication_failed"
JOB_DEADLINE_EXCEEDED = "job_deadline_exceeded"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
MEETING_DELETED = "meeting_deleted"
LEASE_EXPIRED = "lease_expired"

TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.DEAD_LETTER}
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay: timedelta
    max_delay: timedelta
    deadline: timedelta
    max_replays: int

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            max_attempts=settings.job_max_attempts,
            base_delay=timedelta(seconds=settings.job_retry_delay_seconds),
            max_delay=timedelta(seconds=settings.job_retry_max_delay_seconds),
            deadline=timedelta(seconds=settings.job_deadline_seconds),
            max_replays=settings.job_max_replays,
        )

    def retry_delay(self, attempt: int) -> timedelta:
        """Back-off before the attempt that follows ``attempt`` (1-based)."""
        exponent = max(attempt - 1, 0)
        delay = self.base_delay * (2**exponent)
        return min(delay, self.max_delay)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RetryVerdict(str, enum.Enum):
    ALLOWED = "allowed"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    DEADLINE_EXCEEDED = "deadline_exceeded"


def retry_verdict(job: ProcessingJob, *, due: datetime) -> RetryVerdict:
    """May ``job`` be queued for another attempt that would start at ``due``?"""
    if job.attempt_count >= job.max_attempts:
        return RetryVerdict.ATTEMPTS_EXHAUSTED
    deadline = _as_utc(job.deadline_at)
    if deadline is not None and _as_utc(due) >= deadline:
        return RetryVerdict.DEADLINE_EXCEEDED
    return RetryVerdict.ALLOWED


def deadline_passed(job: ProcessingJob, *, now: datetime) -> bool:
    deadline = _as_utc(job.deadline_at)
    return deadline is not None and deadline <= _as_utc(now)  # type: ignore[operator]


def release_job_reservations(db: Session, job: ProcessingJob, *, reason: str) -> None:
    """Release whatever the job still reserves (idempotent; committed rows untouched)."""
    for reservation_id in (job.reservation_id, job.concurrency_reservation_id):
        if not reservation_id:
            continue
        reservation = db.get(UsageReservation, reservation_id)
        if reservation is not None and reservation.state == ReservationState.RESERVED:
            release_usage(db, reservation.id, reason=reason)


def park_job(
    db: Session,
    job: ProcessingJob,
    meeting: Meeting | None,
    *,
    state: JobState,
    code: str,
    message: str,
    failure_class: FailureClass,
    now: datetime,
    release_reason: str | None = None,
) -> None:
    """Move ``job`` to a terminal failure state on a row the caller owns/locked.

    Drops the lease, records the classified reason, releases every still
    reserved allowance and reflects the outcome on the (live) meeting.  The
    caller records the audit event with its own details and commits.
    """
    if state not in {JobState.FAILED, JobState.DEAD_LETTER, JobState.CANCELLED}:
        raise ValueError(f"{state.value} is not a terminal failure state")
    job.state = state
    job.stage = state.value
    job.completed_at = now
    job.retry_after = None
    job.failure_code = code[:80]
    job.failure_message = message[:500]
    job.failure_class = failure_class.value
    release_lease(job)
    release_job_reservations(db, job, reason=release_reason or code)
    if meeting is not None and meeting.deleted_at is None:
        meeting.status = (
            MeetingStatus.CANCELLED if state == JobState.CANCELLED else MeetingStatus.FAILED
        )


def cancel_job(
    db: Session,
    job: ProcessingJob,
    *,
    reason: str,
    message: str,
    now: datetime,
) -> bool:
    """Cancel a QUEUED/RUNNING job (deletion path). Returns False when terminal.

    A running attempt loses its lease here; its next fenced write sees
    CANCELLED and stops without persisting anything.  The caller withdraws
    the pending publication (``outbox.cancel_dispatch``) in the same
    transaction.
    """
    if job.state in TERMINAL_STATES:
        return False
    park_job(
        db,
        job,
        None,
        state=JobState.CANCELLED,
        code=reason,
        message=message,
        failure_class=FailureClass.CANCELLED,
        now=now,
    )
    return True
