"""Transactional outbox for job dispatch (WP06-T02).

Ownership and transaction boundaries
------------------------------------
* ``record_dispatch_intent`` is called by ``create_upload_job`` **inside the
  upload transaction**: meeting, recording, quota reservations, job and the
  ``job_dispatches`` row commit together or not at all. Nothing is published
  before that commit.
* ``dispatch_job`` (request-time optimisation) and ``sweep_dispatch_outbox``
  (recovery) share one publisher: lock the row ``FOR UPDATE SKIP LOCKED``,
  publish, mark ``PUBLISHED`` with the broker message id, commit. A failure
  before the mark leaves the row ``PENDING`` and it is republished later.
* Delivery is therefore **at-least-once**. Consumers must tolerate duplicates:
  ``processor.process_job`` claims the job row and returns early for
  running/terminal jobs, and results/usage are written idempotently.
* Publication failures back off exponentially up to ``dispatch_max_attempts``
  and then park the row as ``FAILED`` (visible on the job's API payload).
* Discovery across tenants runs under the ``dispatch`` service scope, which
  the RLS policy grants on ``job_dispatches`` only. Processing itself still
  binds the job's own tenant from the persisted row, never from the message.
* Requeues go through the same row (WP06-T04): a retryable processing failure
  and the recovery of an abandoned attempt ``rearm_dispatch`` the intent for
  the job's current lease generation inside their own transaction; the same
  publisher delivers it when due. There is no second retry loop (no broker
  level ``task.retry``), so publication attempts (this row), processing
  attempts (``processing_jobs.attempt_count``), stage attempts
  (``processing_stage_runs``) and the one billable settlement per job stay
  separate counters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    DispatchState,
    JobDispatch,
    JobState,
    Meeting,
    ProcessingJob,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_DISPATCH,
    bind_job_context,
    bind_service_scope,
    bind_tenant_context,
)
from backend.jobs.dispatcher import publish_processing_job
from backend.jobs.failures import PUBLICATION_FAILED, FailureClass, park_job
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger


@dataclass(frozen=True)
class DispatchOutcome:
    job_id: str
    state: str  # published | deferred | failed | not_due | busy | not_found | cancelled


def record_dispatch_intent(db: Session, job: ProcessingJob, *, settings: Settings) -> JobDispatch:
    """Persist the publish intent in the caller's transaction (no I/O)."""
    dispatch = JobDispatch(
        workspace_id=job.workspace_id,
        job_id=job.id,
        state=DispatchState.PENDING,
        max_attempts=settings.dispatch_max_attempts,
        next_attempt_at=datetime.now(UTC),
    )
    db.add(dispatch)
    db.flush()
    return dispatch


def rearm_dispatch(
    db: Session, job: ProcessingJob, *, due: datetime, settings: Settings
) -> JobDispatch:
    """Arm (again) the job's publish intent for its current lease generation.

    Used in the caller's transaction by the two paths that requeue a job:
    a retryable failure (due at ``retry_after``) and the recovery of an
    abandoned attempt (due now).  The row is unique per job; arming it for a
    generation it already carries in PENDING is a no-op, so duplicate scans
    cannot arm it twice.  Publication attempts restart per arming — they are
    dispatch attempts, not processing attempts.
    """
    dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job.id).with_for_update())
    if dispatch is None:
        dispatch = JobDispatch(workspace_id=job.workspace_id, job_id=job.id)
        db.add(dispatch)
    elif dispatch.state == DispatchState.PENDING and dispatch.generation == job.lease_generation:
        return dispatch
    dispatch.state = DispatchState.PENDING
    dispatch.generation = job.lease_generation
    dispatch.attempt_count = 0
    dispatch.max_attempts = settings.dispatch_max_attempts
    dispatch.next_attempt_at = due
    dispatch.published_at = None
    dispatch.message_id = None
    dispatch.last_error = None
    db.flush()
    return dispatch


def cancel_dispatch(db: Session, job_id: str) -> bool:
    """Withdraw a pending publication (same transaction as the cancellation)."""
    dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
    if dispatch is None or dispatch.state != DispatchState.PENDING:
        return False
    dispatch.state = DispatchState.CANCELLED
    db.flush()
    return True


def _backoff(settings: Settings, attempt: int) -> timedelta:
    seconds = min(
        settings.dispatch_retry_base_seconds * (2 ** max(attempt - 1, 0)),
        settings.dispatch_retry_max_seconds,
    )
    return timedelta(seconds=seconds)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _mark_published(dispatch: JobDispatch, *, message_id: str, now: datetime) -> None:
    dispatch.state = DispatchState.PUBLISHED
    dispatch.published_at = now
    dispatch.message_id = message_id[:120]
    dispatch.last_error = None


def dispatch_job(
    job_id: str,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> DispatchOutcome:
    """Publish one pending row now if it is due; safe to call repeatedly."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_DISPATCH)
        locked = db.execute(
            select(JobDispatch)
            .where(JobDispatch.job_id == job_id)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if locked is None:
            exists = db.scalar(select(JobDispatch.id).where(JobDispatch.job_id == job_id))
            db.rollback()
            return DispatchOutcome(job_id, "busy" if exists else "not_found")
        if locked.state == DispatchState.CANCELLED:
            db.rollback()
            return DispatchOutcome(job_id, "cancelled")
        if locked.state != DispatchState.PENDING:
            db.rollback()
            return DispatchOutcome(job_id, locked.state.value)
        if _as_utc(locked.next_attempt_at) > now:
            db.rollback()
            return DispatchOutcome(job_id, "not_due")

        locked.attempt_count += 1
        try:
            message_id = publish_processing_job(job_id, settings, workspace_id=locked.workspace_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            locked.last_error = error
            if locked.attempt_count >= locked.max_attempts:
                locked.state = DispatchState.FAILED
                state = "failed"
                # Same transaction: the intent is exhausted *and* the job is
                # parked, or neither (WP06-T05).
                parked = _park_undeliverable_job(
                    db, locked, settings=settings, now=now, error=error
                )
                logger.error(
                    "JOB_DISPATCH_FAILED job_id=%s attempts=%s error_type=%s parked=%s",
                    job_id,
                    locked.attempt_count,
                    type(exc).__name__,
                    parked,
                )
            else:
                locked.next_attempt_at = now + _backoff(settings, locked.attempt_count)
                state = "deferred"
                logger.warning(
                    "JOB_DISPATCH_DEFERRED job_id=%s attempt=%s error_type=%s",
                    job_id,
                    locked.attempt_count,
                    type(exc).__name__,
                )
            db.commit()
            return DispatchOutcome(job_id, state)

        # Publish succeeded. If this commit fails the row stays PENDING and the
        # message is delivered again later — the consumer tolerates that.
        _mark_published(locked, message_id=message_id, now=now)
        db.commit()
        return DispatchOutcome(job_id, "published")


def _park_undeliverable_job(
    db: Session,
    dispatch: JobDispatch,
    *,
    settings: Settings,
    now: datetime,
    error: str,
) -> bool:
    """Dead-letter the job whose publication attempts are exhausted.

    Publication failure is its own failure class (WP06-T05): the job never
    ran, so instead of a second retry loop it is parked ``dead_letter`` with
    ``failure_code = publication_failed``, its reservations are released and
    an operator may replay it once the broker is back.  Authority comes from
    the persisted job row — job context first, then the job's own tenant —
    never from the message; the dispatch scope alone cannot touch jobs.
    Returns False when the job is no longer queued for this intent (it was
    delivered after all, cancelled, or is being claimed right now).
    """
    bind_job_context(db, dispatch.job_id)
    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == dispatch.job_id)
        .with_for_update(skip_locked=True)
    )
    if job is None or job.state != JobState.QUEUED or job.lease_generation != dispatch.generation:
        return False
    bind_tenant_context(db, workspace_id=job.workspace_id)
    meeting = db.get(Meeting, job.meeting_id)
    park_job(
        db,
        job,
        meeting,
        state=JobState.DEAD_LETTER,
        code=PUBLICATION_FAILED,
        message=(
            "The job could not be published to the processing queue; "
            "publication attempts were exhausted"
        ),
        failure_class=FailureClass.PUBLICATION,
        now=now,
    )
    record_audit_event(
        db,
        event_type="meeting.processing.failed",
        workspace_id=job.workspace_id,
        target_type="meeting",
        target_id=job.meeting_id,
        outcome="failure",
        details={
            "failure_code": PUBLICATION_FAILED,
            "cause": PUBLICATION_FAILED,
            "failure_class": FailureClass.PUBLICATION.value,
            "retryable": True,
            "terminal_reason": "publication_exhausted",
            "dispatch_attempts": dispatch.attempt_count,
            "generation": job.lease_generation,
            "last_error": error,
        },
        settings=settings,
    )
    return True


def sweep_dispatch_outbox(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Publish every due PENDING row (bounded batch). Returns outcome counts."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    batch = limit or settings.dispatch_sweep_batch_size
    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_DISPATCH)
        due = list(
            db.scalars(
                select(JobDispatch.job_id)
                .where(
                    JobDispatch.state == DispatchState.PENDING,
                    JobDispatch.next_attempt_at <= now,
                )
                .order_by(JobDispatch.next_attempt_at.asc(), JobDispatch.id.asc())
                .limit(batch)
            )
        )
        db.rollback()
    counts: dict[str, int] = {"scanned": len(due)}
    for job_id in due:
        outcome = dispatch_job(job_id, settings, now=now)
        counts[outcome.state] = counts.get(outcome.state, 0) + 1
    if due:
        logger.info("JOB_DISPATCH_SWEEP %s", counts)
    return counts


def verify_job_payload(job_id: str, workspace_id: str | None) -> bool:
    """A broker message may *assert* a workspace; the persisted job decides.

    Reads only the one job row (job-scoped context). Returns False when the
    assertion does not match, so the consumer rejects the message without
    touching any tenant data.
    """
    if workspace_id is None:
        return True
    with get_session_factory()() as db:
        bind_job_context(db, job_id)
        persisted = db.scalar(select(ProcessingJob.workspace_id).where(ProcessingJob.id == job_id))
        db.rollback()
    if persisted is None:
        # Unknown job: let the processor report not_found through its own path.
        return True
    if persisted != workspace_id:
        logger.error(
            "JOB_PAYLOAD_REJECTED job_id=%s asserted_workspace=%s", job_id, workspace_id[:36]
        )
        return False
    return True


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    import json
    import sys

    from backend.database.relational import configure_database

    configure_database()
    summary = sweep_dispatch_outbox()
    print(json.dumps(summary))
    sys.exit(1 if summary.get("failed") else 0)
