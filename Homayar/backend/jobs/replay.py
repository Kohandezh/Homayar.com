"""Operator replay of dead-lettered jobs (WP06-T05).

Replay is the only way a parked (``DEAD_LETTER``) job runs again — never a
hand edit of ``processing_jobs``.  It is:

* **authorized** — workspace administration permission plus CSRF (router);
* **audited** — ``meeting.processing.replayed`` records the actor, the
  previous failure and the new budget;
* **bounded** — at most ``JOB_MAX_REPLAYS`` per job; each replay grants a
  fresh ``max_attempts`` budget *on top of* the attempts already recorded
  (stage history is kept, attempt numbers keep counting) and a new deadline;
* **policy-preserving** — the parked job's reservations were released, so
  quota is reserved again under the workspace's *current* entitlement (a
  replay that does not fit is refused and changes nothing); the meeting and
  its recording must still exist and retention must not have expired, so a
  deleted resource is never resurrected;
* **fencing/outbox compatible** — the lease generation advances (nothing
  from the parked attempts can write) and the job's own outbox row is
  re-armed for the new generation and published by the shared publisher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.config import Settings
from backend.database.saas_models import JobState, Meeting, MeetingStatus, ProcessingJob
from backend.jobs.failures import RetryPolicy
from backend.jobs.leases import as_utc, database_now, release_lease
from backend.jobs.outbox import rearm_dispatch
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.entitlement_service import QuotaExceededError, reserve_usage
from backend.services.logging_service import logger

REPLAY_EVENT = AuditEventType.MEETING_PROCESSING_REPLAYED


@dataclass
class ReplayRefused(Exception):
    code: str
    message: str
    status_code: int = 409
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - diagnostics
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class ReplayResult:
    job: ProcessingJob
    replay: int
    reservation_id: str


def replay_dead_letter_job(
    db: Session,
    *,
    job_id: str,
    workspace_id: str,
    actor_user_id: str,
    settings: Settings,
    request_id: str | None = None,
) -> ReplayResult:
    """Requeue one dead-lettered job in the caller's transaction (no I/O).

    Raises ``ReplayRefused`` with nothing written.  The caller commits and
    then publishes through ``outbox.dispatch_job`` (the sweeper backs it up).
    """
    policy = RetryPolicy.from_settings(settings)
    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.workspace_id == workspace_id)
        .with_for_update()
    )
    if job is None:
        raise ReplayRefused("job_not_found", "Processing job not found", status_code=404)
    if job.state != JobState.DEAD_LETTER:
        raise ReplayRefused(
            "job_not_replayable",
            "Only dead-lettered jobs can be replayed",
            details={"state": job.state.value},
        )
    if job.replay_count >= policy.max_replays:
        raise ReplayRefused(
            "replay_limit_reached",
            "The job has reached its replay limit",
            details={"replay_count": job.replay_count, "max_replays": policy.max_replays},
        )
    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.recording))
        .where(Meeting.id == job.meeting_id, Meeting.workspace_id == workspace_id)
        .with_for_update()
    )
    now = database_now(db)
    if meeting is None or meeting.deleted_at is not None:
        raise ReplayRefused(
            "meeting_deleted", "The meeting was deleted; replay does not resurrect it"
        )
    retention = as_utc(meeting.retention_expires_at)
    if retention is not None and retention <= now:
        raise ReplayRefused(
            "retention_expired", "The meeting's retention period has ended; upload it again"
        )
    recording = meeting.recording
    if recording is None or recording.deleted_at is not None:
        raise ReplayRefused(
            "recording_missing", "The recording is no longer stored; upload it again"
        )

    replay = job.replay_count + 1
    try:
        audio = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            amount=recording.duration_seconds,
            idempotency_key=f"replay:{job.id}:{replay}:audio",
            metric="audio_seconds",
        )
        concurrency = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            amount=1,
            idempotency_key=f"replay:{job.id}:{replay}:concurrency",
            metric="concurrent_jobs",
        )
    except QuotaExceededError as exc:
        raise ReplayRefused(
            "concurrent_job_limit_reached"
            if exc.metric == "concurrent_jobs"
            else "audio_quota_exceeded",
            "The workspace processing allowance is exhausted",
            status_code=429,
            details={
                "metric": exc.metric,
                "limit": exc.limit,
                "used": exc.used,
                "reserved": exc.reserved,
                "requested": exc.requested,
                "remaining": exc.remaining,
            },
        ) from exc

    # Flat audit details: the audit sanitizer redacts keys containing "code"
    # and stringifies nested values, so the previous failure is recorded
    # under plain names.
    previous = {
        "previous_failure": job.failure_code,
        "previous_class": job.failure_class,
        "previous_attempts": job.attempt_count,
        "previous_generation": job.lease_generation,
        "previous_reservation_id": job.reservation_id,
    }
    job.reservation_id = audio.id
    job.concurrency_reservation_id = concurrency.id
    job.lease_generation += 1  # fence: nothing from the parked attempts may write
    job.max_attempts = job.attempt_count + policy.max_attempts
    job.replay_count = replay
    job.last_replayed_at = now
    job.deadline_at = now + policy.deadline
    job.state = JobState.QUEUED
    job.stage = "replay_scheduled"
    job.retry_after = now
    job.completed_at = None
    job.failure_code = None
    job.failure_message = None
    job.failure_class = None
    release_lease(job)
    meeting.status = MeetingStatus.QUEUED
    rearm_dispatch(db, job, due=now, settings=settings)
    record_audit_event(
        db,
        event_type=REPLAY_EVENT,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target_type="meeting",
        target_id=meeting.id,
        request_id=request_id,
        details={
            "job_id": job.id,
            "replay": replay,
            "max_replays": policy.max_replays,
            "generation": job.lease_generation,
            "max_attempts": job.max_attempts,
            "deadline_at": job.deadline_at.isoformat(),
            "reservation_id": audio.id,
            **previous,
        },
        settings=settings,
    )
    db.flush()
    logger.warning(
        "JOB_REPLAYED job_id=%s replay=%s generation=%s previous_failure=%s",
        job.id,
        replay,
        job.lease_generation,
        previous["previous_failure"],
    )
    return ReplayResult(job=job, replay=replay, reservation_id=audio.id)
