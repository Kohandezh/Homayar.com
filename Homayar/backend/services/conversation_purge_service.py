"""Tombstone-first deletion and the durable purge of a conversation (WP07-T03, ADR 0021).

Two steps, never merged:

1. **Tombstone** (:func:`tombstone_meeting`, one transaction): the meeting and
   its recording get ``deleted_at``, share links are revoked, checkpoints can
   no longer be reused, every live media object is asked to go (a held one
   keeps its bytes) and a :class:`ConversationPurge` row records the durable
   work.  From this commit on, every application-mediated path refuses the
   conversation: reads, shares, exports, downloads, search, replay,
   checkpoint reuse and stage publication all check ``Meeting.deleted_at``
   on current server state (publication and replay under the meeting row
   lock).  A presigned URL issued earlier stays valid until its own expiry
   (``STORAGE_PRESIGN_TTL_SECONDS``); nothing here revokes it instantly.
2. **Purge** (:func:`run_purge_attempt`, bounded, retried): removes the
   derived content rows (transcripts, segments, redacted versions, derived
   text views and entities, analysis, evidence, summaries, action items,
   speakers, share recipients), scrubs the free-text
   title and filename of the meeting/recording, removes object bytes
   state-first/bytes-second behind the media row lock and closes the purge
   when every store is clean.  Ledger history (usage, provider usage, stage
   runs, audit, ingestion and media rows) is kept: it holds ids, amounts and
   states, not conversation content.

Legal hold: while any object of the conversation is held, derived content is
kept too and the purge is deferred (no attempt spent); after release the
next attempt proceeds.  The point of no return for content rows is the purge
commit; for bytes it is the locked removal in ``delete_objects``.  A hold
never restores what was already removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    ConversationPurge,
    EvidenceSpan,
    JobState,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    MeetingSpeaker,
    MeetingSummary,
    PiiMapping,
    ProcessingJob,
    PurgeState,
    Recording,
    RedactedSegment,
    ShareLink,
    ShareRecipient,
    StageCheckpoint,
    TextViewEntity,
    TextViewSegment,
    TranscriptRedaction,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptTextView,
)
from backend.jobs import checkpoints
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger
from backend.services.media_lifecycle_service import (
    delete_objects,
    media_for_meeting,
    retire_meeting_media,
)
from backend.storage.base import ObjectStorage, StorageError

ACTIVE_JOB_STATES = (JobState.QUEUED, JobState.RUNNING)
REASON_MEETING_DELETED = "meeting_deleted"
REASON_RETENTION_EXPIRED = "retention_expired"
ERROR_LEGAL_HOLD = "legal_hold"
ERROR_JOB_ACTIVE = "job_active"
ERROR_NOT_TOMBSTONED = "not_tombstoned"
ERROR_STORAGE_PENDING = "storage_pending"


@dataclass
class PurgeOutcome:
    """What one attempt did (ids and counts only)."""

    purge_id: str
    state: PurgeState | None = None
    skipped: bool = False
    deferred: str | None = None
    content_rows_removed: int = 0
    objects_removed: int = 0
    objects_held: int = 0
    objects_pending: int = 0
    failures: list[str] = field(default_factory=list)


def _lock_meeting(db: Session, *, workspace_id: str, meeting_id: str) -> Meeting | None:
    return db.scalar(
        select(Meeting)
        .where(Meeting.id == meeting_id, Meeting.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def active_job_exists(db: Session, *, workspace_id: str, meeting_id: str) -> bool:
    return (
        db.scalar(
            select(func.count(ProcessingJob.id)).where(
                ProcessingJob.workspace_id == workspace_id,
                ProcessingJob.meeting_id == meeting_id,
                ProcessingJob.state.in_(ACTIVE_JOB_STATES),
            )
        )
        or 0
    ) > 0


def purge_for_meeting(
    db: Session, *, workspace_id: str, meeting_id: str
) -> ConversationPurge | None:
    return db.scalar(
        select(ConversationPurge).where(
            ConversationPurge.workspace_id == workspace_id,
            ConversationPurge.meeting_id == meeting_id,
        )
    )


def tombstone_meeting(
    db: Session,
    *,
    meeting: Meeting,
    reason: str,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> ConversationPurge:
    """Commit-ready logical deletion of ``meeting`` (the caller commits).

    The caller holds the meeting (locked for the retention sweep; job
    cancellation for an explicit deletion happens before this call).
    Idempotent: a second call returns the existing purge row.
    """
    existing = purge_for_meeting(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    if meeting.deleted_at is None:
        meeting.deleted_at = now
    recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting.id))
    if recording is not None and recording.deleted_at is None:
        recording.deleted_at = now
    db.execute(
        update(ShareLink)
        .where(
            ShareLink.workspace_id == meeting.workspace_id,
            ShareLink.meeting_id == meeting.id,
            ShareLink.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    checkpoints.retire_meeting_checkpoints(db, meeting_id=meeting.id, now=now)
    retire_meeting_media(
        db,
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        reason=reason,
        actor_user_id=actor_user_id,
        now=now,
        settings=settings,
    )
    if existing is not None:
        return existing
    purge = ConversationPurge(
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        reason=reason[:40],
        state=PurgeState.PENDING,
        requested_at=now,
        requested_by_user_id=actor_user_id,
        next_attempt_at=now,
    )
    db.add(purge)
    db.flush()
    return purge


def _retry_delay(settings: Settings, attempts: int) -> timedelta:
    base = settings.retention_purge_retry_base_seconds
    seconds = min(base * (2 ** max(attempts - 1, 0)), settings.retention_purge_retry_max_seconds)
    return timedelta(seconds=seconds)


def _remove_content(db: Session, *, workspace_id: str, meeting: Meeting) -> int:
    """Delete the derived content rows of the conversation; returns the count.

    Bulk SQL deletes (the immutability guards refuse *updates* of transcript
    and analysis rows; removal is the purge itself).  Children first so the
    result does not depend on the database enforcing ON DELETE CASCADE.
    """
    removed = 0
    share_ids = select(ShareLink.id).where(
        ShareLink.workspace_id == workspace_id, ShareLink.meeting_id == meeting.id
    )
    removed += (
        db.execute(
            delete(ShareRecipient).where(
                ShareRecipient.workspace_id == workspace_id, ShareRecipient.share_id.in_(share_ids)
            )
        ).rowcount
        or 0
    )
    # Derived text views and entities (ADR 0034), then redacted versions and
    # their restricted mappings (WP07-T05): a view may point at a redaction.
    for model in (
        TextViewEntity,
        TextViewSegment,
        TranscriptTextView,
        PiiMapping,
        RedactedSegment,
        TranscriptRedaction,
    ):
        removed += (
            db.execute(
                delete(model).where(
                    model.workspace_id == workspace_id, model.meeting_id == meeting.id
                )
            ).rowcount
            or 0
        )
    for model in (
        EvidenceSpan,
        ActionItem,
        MeetingSummary,
        AnalysisRun,
        TranscriptSegment,
    ):
        removed += (
            db.execute(
                delete(model).where(
                    model.workspace_id == workspace_id, model.meeting_id == meeting.id
                )
            ).rowcount
            or 0
        )
    # One statement removes every revision (the parent link cascades inside it).
    removed += (
        db.execute(
            delete(TranscriptRevision).where(
                TranscriptRevision.workspace_id == workspace_id,
                TranscriptRevision.meeting_id == meeting.id,
            )
        ).rowcount
        or 0
    )
    removed += (
        db.execute(
            delete(MeetingSpeaker).where(
                MeetingSpeaker.workspace_id == workspace_id, MeetingSpeaker.meeting_id == meeting.id
            )
        ).rowcount
        or 0
    )
    # Free text a person typed about the conversation is content as well.
    meeting.title = ""
    recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting.id))
    if recording is not None:
        recording.original_filename = ""
    return removed


def _pending_checkpoint_keys(db: Session, *, workspace_id: str, meeting_id: str) -> list[str]:
    return list(
        db.scalars(
            select(StageCheckpoint.artifact_key).where(
                StageCheckpoint.workspace_id == workspace_id,
                StageCheckpoint.meeting_id == meeting_id,
                StageCheckpoint.object_deleted_at.is_(None),
            )
        )
    )


def _legacy_recording_key(db: Session, *, meeting_id: str) -> str | None:
    """Storage key of a pre-ledger recording (no media object), if any."""
    recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
    if recording is None or recording.media_object_id is not None:
        return None
    return recording.storage_key


def _remove_reappeared(
    db: Session, storage: ObjectStorage, *, workspace_id: str, meeting_id: str
) -> tuple[int, int]:
    """After a restore (WP07-T04): remove bytes of rows that were already
    closed but may be back from an object-store restore.  Idempotent: an
    absent object is fine; a storage error is counted, never read as gone.
    Returns (removed, failed)."""
    keys = list(
        db.scalars(
            select(MediaObject.storage_key).where(
                MediaObject.workspace_id == workspace_id,
                MediaObject.meeting_id == meeting_id,
                MediaObject.lifecycle_state == MediaLifecycleState.DELETED,
            )
        )
    ) + list(
        db.scalars(
            select(StageCheckpoint.artifact_key).where(
                StageCheckpoint.workspace_id == workspace_id,
                StageCheckpoint.meeting_id == meeting_id,
                StageCheckpoint.object_deleted_at.is_not(None),
            )
        )
    )
    db.rollback()
    removed = failed = 0
    for key in keys:
        try:
            if storage.delete(workspace_id, key):
                removed += 1
        except StorageError as exc:
            failed += 1
            logger.error(
                "PURGE_REAPPEARED_DELETE_FAILED meeting_id=%s error=%s", meeting_id, exc.code
            )
    return removed, failed


def _claim(
    db: Session, *, purge_id: str, workspace_id: str, now: datetime
) -> ConversationPurge | None:
    """Lock a due PENDING purge; a concurrent sweeper skips it (SKIP LOCKED)."""
    return db.scalar(
        select(ConversationPurge)
        .where(
            ConversationPurge.id == purge_id,
            ConversationPurge.workspace_id == workspace_id,
            ConversationPurge.state == PurgeState.PENDING,
            ConversationPurge.next_attempt_at <= now,
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


def run_purge_attempt(
    db: Session,
    storage: ObjectStorage,
    *,
    purge_id: str,
    workspace_id: str,
    now: datetime,
    settings: Settings,
) -> PurgeOutcome:
    """One bounded attempt on one purge (tenant-bound session; commits itself).

    Transaction 1 claims the row (moves ``next_attempt_at`` forward, counts
    the attempt unless a hold or an active job defers it) and removes content
    rows; the bytes are removed afterwards, each behind its row lock; the
    last transaction recounts every store and closes, reschedules or fails
    the purge.
    """
    outcome = PurgeOutcome(purge_id=purge_id)

    # --- 1. claim + content (one transaction) -------------------------
    purge = _claim(db, purge_id=purge_id, workspace_id=workspace_id, now=now)
    if purge is None:
        db.rollback()
        outcome.skipped = True
        return outcome
    meeting = _lock_meeting(db, workspace_id=workspace_id, meeting_id=purge.meeting_id)
    if meeting is None or meeting.deleted_at is None:
        # Never purge a live conversation (a restore must re-tombstone first).
        purge.attempts += 1
        purge.last_attempt_at = now
        purge.last_error = ERROR_NOT_TOMBSTONED
        purge.next_attempt_at = now + _retry_delay(settings, purge.attempts)
        _fail_if_exhausted(db, purge, now=now, settings=settings)
        db.commit()
        outcome.state = purge.state
        outcome.failures.append(ERROR_NOT_TOMBSTONED)
        return outcome

    media = media_for_meeting(db, workspace_id=workspace_id, meeting_id=meeting.id, for_update=True)
    held = [m for m in media if m.lifecycle_state is MediaLifecycleState.LEGAL_HOLD]
    job_active = active_job_exists(db, workspace_id=workspace_id, meeting_id=meeting.id)
    purge.last_attempt_at = now
    if held or job_active:
        outcome.deferred = ERROR_LEGAL_HOLD if held else ERROR_JOB_ACTIVE
        purge.next_attempt_at = now + timedelta(
            seconds=settings.retention_purge_hold_recheck_seconds
            if held
            else settings.retention_purge_retry_base_seconds
        )
    else:
        purge.attempts += 1
        purge.next_attempt_at = now + _retry_delay(settings, purge.attempts)
        # Late requests (e.g. a released hold without a recorded request).
        retire_meeting_media(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            reason=purge.reason,
            actor_user_id=None,
            now=now,
            settings=settings,
        )
        checkpoints.retire_meeting_checkpoints(db, meeting_id=meeting.id, now=now)
        if purge.content_purged_at is None:
            outcome.content_rows_removed = _remove_content(
                db, workspace_id=workspace_id, meeting=meeting
            )
            purge.content_rows_removed += outcome.content_rows_removed
            purge.content_purged_at = now
    db.commit()

    # --- 2. bytes (state committed first) -----------------------------
    pending_media = [
        m
        for m in media_for_meeting(db, workspace_id=workspace_id, meeting_id=meeting.id)
        if m.lifecycle_state is MediaLifecycleState.DELETION_PENDING
    ]
    if pending_media:
        closed = delete_objects(db, storage, objects=pending_media, now=now, settings=settings)
        outcome.objects_removed += len(closed)
        db.commit()
    # Stage outputs are derived content: a hold keeps them with the rest.
    keys = (
        []
        if outcome.deferred == ERROR_LEGAL_HOLD
        else _pending_checkpoint_keys(db, workspace_id=workspace_id, meeting_id=meeting.id)
    )
    if keys:
        removed = checkpoints.delete_artifact_objects(storage, workspace_id, keys)
        if removed:
            outcome.objects_removed += checkpoints.mark_objects_deleted(db, keys=removed, now=now)
            db.commit()
    reappeared_failed = 0
    if purge.reapplied_at is not None and not (held or job_active):
        removed_again, reappeared_failed = _remove_reappeared(
            db, storage, workspace_id=workspace_id, meeting_id=meeting.id
        )
        outcome.objects_removed += removed_again
    legacy_failed = False
    legacy_key = None if (held or job_active) else _legacy_recording_key(db, meeting_id=meeting.id)
    if legacy_key is not None:
        try:
            storage.delete(workspace_id, legacy_key)
        except StorageError as exc:
            legacy_failed = True
            logger.error(
                "PURGE_LEGACY_RECORDING_DELETE_FAILED meeting_id=%s error=%s",
                meeting.id,
                exc.code,
            )

    # --- 3. recount and settle ----------------------------------------
    purge = db.scalar(
        select(ConversationPurge)
        .where(ConversationPurge.id == purge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if purge is None or purge.state is not PurgeState.PENDING:
        db.rollback()
        outcome.state = purge.state if purge is not None else None
        return outcome
    states = [
        m.lifecycle_state
        for m in media_for_meeting(db, workspace_id=workspace_id, meeting_id=meeting.id)
    ]
    outcome.objects_held = sum(1 for s in states if s is MediaLifecycleState.LEGAL_HOLD)
    outcome.objects_pending = sum(
        1 for s in states if s not in (MediaLifecycleState.DELETED, MediaLifecycleState.LEGAL_HOLD)
    ) + len(_pending_checkpoint_keys(db, workspace_id=workspace_id, meeting_id=meeting.id))
    outcome.objects_pending += (1 if legacy_failed else 0) + reappeared_failed
    purge.objects_removed += outcome.objects_removed
    purge.objects_held = outcome.objects_held
    purge.objects_pending = outcome.objects_pending

    if outcome.deferred is not None:
        purge.last_error = outcome.deferred
    elif outcome.objects_pending == 0 and outcome.objects_held == 0 and purge.content_purged_at:
        purge.state = PurgeState.COMPLETED
        purge.completed_at = now
        purge.last_error = None
        record_audit_event(
            db,
            event_type="meeting.purge_completed",
            workspace_id=workspace_id,
            target_type="meeting",
            target_id=meeting.id,
            details={
                "purge_id": purge.id,
                "reason": purge.reason,
                "attempts": purge.attempts,
                "content_rows_removed": purge.content_rows_removed,
                "objects_removed": purge.objects_removed,
            },
            settings=settings,
        )
    else:
        purge.last_error = ERROR_STORAGE_PENDING
        outcome.failures.append(ERROR_STORAGE_PENDING)
        _fail_if_exhausted(db, purge, now=now, settings=settings)
    db.commit()
    outcome.state = purge.state
    return outcome


def _fail_if_exhausted(
    db: Session, purge: ConversationPurge, *, now: datetime, settings: Settings
) -> None:
    if purge.attempts < settings.retention_purge_max_attempts:
        return
    purge.state = PurgeState.FAILED
    logger.error(
        "RETENTION_PURGE_FAILED purge_id=%s meeting_id=%s attempts=%s error=%s",
        purge.id,
        purge.meeting_id,
        purge.attempts,
        purge.last_error,
    )
    record_audit_event(
        db,
        event_type="meeting.purge_failed",
        workspace_id=purge.workspace_id,
        target_type="meeting",
        target_id=purge.meeting_id,
        outcome="failed",
        details={
            "purge_id": purge.id,
            "attempts": purge.attempts,
            "last_error": purge.last_error,
            "objects_pending": purge.objects_pending,
        },
        settings=settings,
    )


def requeue_failed_purge(
    db: Session,
    *,
    purge: ConversationPurge,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> bool:
    """Operator action: give a FAILED purge a fresh attempt budget (audited)."""
    if purge.state is not PurgeState.FAILED:
        return False
    purge.state = PurgeState.PENDING
    purge.attempts = 0
    purge.next_attempt_at = now
    record_audit_event(
        db,
        event_type="meeting.purge_requeued",
        actor_user_id=actor_user_id,
        workspace_id=purge.workspace_id,
        target_type="meeting",
        target_id=purge.meeting_id,
        details={"purge_id": purge.id, "last_error": purge.last_error},
        settings=settings,
    )
    return True


def nudge_purge(db: Session, *, workspace_id: str, meeting_id: str | None, now: datetime) -> None:
    """Make a deferred purge due now (after a hold release); no-op otherwise."""
    if meeting_id is None:
        return
    db.execute(
        update(ConversationPurge)
        .where(
            ConversationPurge.workspace_id == workspace_id,
            ConversationPurge.meeting_id == meeting_id,
            ConversationPurge.state == PurgeState.PENDING,
            ConversationPurge.next_attempt_at > now,
        )
        .values(next_attempt_at=now)
    )
