"""Retention expiry and the durable conversation purge sweep (WP07-T03, ADR 0021).

Per workspace (discovered like the other sweepers, each processed in its own
tenant-bound session), two duties:

1. **Expire** — conversations of a ``FINITE`` or ``IMMEDIATE`` workspace
   whose stored ``retention_expires_at`` has passed are tombstoned
   (``retention_expired``).  Eligibility, re-checked at the mutation
   boundary under the workspace (shared) and meeting (exclusive) row locks:
   the workspace policy is enforceable *now* (``is_age_based_deletion_enabled``;
   ``INDEFINITE`` and ``LEGACY_UNRESOLVED`` never act), the conversation is
   not already tombstoned, the expiry lies at or before ``now``, no
   processing job of the conversation is ``queued``/``running`` (terminal
   jobs — completed, failed, cancelled, dead-lettered — are neither mutated
   nor replayed, and do not block), and no object is under legal hold.
   A conversation that fails a check stays as it is and is looked at again
   by the next sweep; nothing makes it permanently ineligible.
2. **Purge** — every ``PENDING`` purge that is due (from an explicit
   deletion or from step 1) gets one bounded attempt
   (:func:`run_purge_attempt`).

Between the two, every tombstone not yet in the tombstone journal (WP07-T04,
ADR 0022) is written there, before the purge destroys anything.

The counts expose progress and alert conditions: ``purges_failed_open``
(FAILED rows awaiting an operator), ``purges_overdue`` (PENDING for more
than 24 hours without a hold, F15's alert threshold) and ``journal_backlog``
(tombstones not yet journaled, e.g. no journal configured).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ConversationPurge,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    PurgeState,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.checkpoints import discover_workspaces
from backend.jobs.leases import as_utc, database_now
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.conversation_purge_service import (
    ACTIVE_JOB_STATES,
    ERROR_LEGAL_HOLD,
    REASON_RETENTION_EXPIRED,
    active_job_exists,
    run_purge_attempt,
    tombstone_meeting,
)
from backend.services.logging_service import logger
from backend.services.media_lifecycle_service import media_for_meeting
from backend.services.retention_policy import is_age_based_deletion_enabled
from backend.services.tombstone_journal import journal_for, journal_pending
from backend.storage import ObjectStorage, create_object_storage

OVERDUE_AFTER = timedelta(hours=24)


def _expiry_candidates(db: Session, *, workspace_id: str, now: datetime, limit: int) -> list[str]:
    active_job = exists().where(
        ProcessingJob.meeting_id == Meeting.id,
        ProcessingJob.state.in_(ACTIVE_JOB_STATES),
    )
    held = exists().where(
        MediaObject.meeting_id == Meeting.id,
        MediaObject.lifecycle_state == MediaLifecycleState.LEGAL_HOLD,
    )
    return list(
        db.scalars(
            select(Meeting.id)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.deleted_at.is_(None),
                Meeting.retention_expires_at.is_not(None),
                Meeting.retention_expires_at <= now,
                ~active_job,
                ~held,
            )
            .order_by(Meeting.retention_expires_at.asc(), Meeting.id.asc())
            .limit(limit)
        )
    )


def expire_meeting(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    now: datetime,
    settings: Settings,
) -> str:
    """Tombstone one expired conversation if it is still eligible; commits.

    Returns ``expired`` or the reason it was left alone.
    """
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if workspace is None or not is_age_based_deletion_enabled(workspace):
        db.rollback()
        return "policy_not_enforceable"
    meeting = db.scalar(
        select(Meeting)
        .where(Meeting.id == meeting_id, Meeting.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    expires = as_utc(meeting.retention_expires_at) if meeting is not None else None
    if meeting is None or meeting.deleted_at is not None or expires is None or expires > now:
        db.rollback()
        return "not_due"
    if active_job_exists(db, workspace_id=workspace_id, meeting_id=meeting_id):
        db.rollback()
        return "job_active"
    media = media_for_meeting(db, workspace_id=workspace_id, meeting_id=meeting_id, for_update=True)
    if any(m.lifecycle_state is MediaLifecycleState.LEGAL_HOLD for m in media):
        db.rollback()
        return "legal_hold"
    purge = tombstone_meeting(
        db,
        meeting=meeting,
        reason=REASON_RETENTION_EXPIRED,
        actor_user_id=None,
        now=now,
        settings=settings,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_RETENTION_EXPIRED,
        workspace_id=workspace_id,
        target_type="meeting",
        target_id=meeting_id,
        details={
            "purge_id": purge.id,
            "retention_mode": workspace.retention_mode.value,
            "expired_at": expires.isoformat(),
        },
        settings=settings,
    )
    db.commit()
    return "expired"


def run_retention_purge(
    settings: Settings | None = None,
    *,
    storage: ObjectStorage | None = None,
    limit: int | None = None,
    now: datetime | None = None,
    workspace_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Expire due conversations and drive due purges; returns counts."""
    settings = settings or get_settings()
    storage = storage or create_object_storage(settings)
    batch = limit or settings.retention_purge_batch_size
    counts: dict[str, int] = {
        "workspaces": 0,
        "expired": 0,
        "expiry_skipped": 0,
        "purge_attempts": 0,
        "purges_completed": 0,
        "purges_deferred_hold": 0,
        "purges_deferred_job": 0,
        "purges_retry": 0,
        "purges_failed": 0,
        "content_rows_removed": 0,
        "objects_removed": 0,
        "purges_failed_open": 0,
        "purges_overdue": 0,
        "journaled": 0,
        "journal_backlog": 0,
    }
    journal = journal_for(settings)
    remaining = batch
    workspaces = (
        list(workspace_ids)
        if workspace_ids is not None
        else discover_workspaces(
            interval_seconds=settings.retention_purge_interval_seconds, now=now
        )
    )

    for workspace_id in workspaces:
        if remaining <= 0:
            break
        counts["workspaces"] += 1
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            current = now or database_now(db)

            # --- 1. EXPIRE ------------------------------------------------
            workspace = db.get(Workspace, workspace_id)
            if workspace is not None and is_age_based_deletion_enabled(workspace):
                candidates = _expiry_candidates(
                    db, workspace_id=workspace_id, now=current, limit=remaining
                )
                db.rollback()
                for meeting_id in candidates:
                    result = expire_meeting(
                        db,
                        workspace_id=workspace_id,
                        meeting_id=meeting_id,
                        now=current,
                        settings=settings,
                    )
                    if result == "expired":
                        counts["expired"] += 1
                    else:
                        counts["expiry_skipped"] += 1
            else:
                db.rollback()

            # --- 1b. JOURNAL (WP07-T04) -----------------------------------
            # Every tombstone reaches the journal outside the backups before
            # the purge destroys anything a restore could bring back.
            if journal is not None:
                counts["journaled"] += journal_pending(
                    db, journal, workspace_id=workspace_id, now=current, limit=batch
                )

            # --- 2. PURGE -------------------------------------------------
            due = list(
                db.scalars(
                    select(ConversationPurge.id)
                    .where(
                        ConversationPurge.workspace_id == workspace_id,
                        ConversationPurge.state == PurgeState.PENDING,
                        ConversationPurge.next_attempt_at <= current,
                    )
                    .order_by(ConversationPurge.next_attempt_at.asc(), ConversationPurge.id.asc())
                    .limit(remaining)
                )
            )
            db.rollback()
            for purge_id in due:
                outcome = run_purge_attempt(
                    db,
                    storage,
                    purge_id=purge_id,
                    workspace_id=workspace_id,
                    now=current,
                    settings=settings,
                )
                if outcome.skipped:
                    continue
                remaining -= 1
                counts["purge_attempts"] += 1
                counts["content_rows_removed"] += outcome.content_rows_removed
                counts["objects_removed"] += outcome.objects_removed
                if outcome.state is PurgeState.COMPLETED:
                    counts["purges_completed"] += 1
                elif outcome.state is PurgeState.FAILED:
                    counts["purges_failed"] += 1
                elif outcome.deferred == ERROR_LEGAL_HOLD:
                    counts["purges_deferred_hold"] += 1
                elif outcome.deferred is not None:
                    counts["purges_deferred_job"] += 1
                else:
                    counts["purges_retry"] += 1

            # --- 3. ALERT CONDITIONS -------------------------------------
            failed_open = (
                db.scalar(
                    select(func.count(ConversationPurge.id)).where(
                        ConversationPurge.workspace_id == workspace_id,
                        ConversationPurge.state == PurgeState.FAILED,
                    )
                )
                or 0
            )
            overdue = (
                db.scalar(
                    select(func.count(ConversationPurge.id)).where(
                        ConversationPurge.workspace_id == workspace_id,
                        ConversationPurge.state == PurgeState.PENDING,
                        ConversationPurge.requested_at <= current - OVERDUE_AFTER,
                        (ConversationPurge.last_error.is_(None))
                        | (ConversationPurge.last_error != ERROR_LEGAL_HOLD),
                    )
                )
                or 0
            )
            unjournaled = (
                db.scalar(
                    select(func.count(ConversationPurge.id)).where(
                        ConversationPurge.workspace_id == workspace_id,
                        ConversationPurge.journaled_at.is_(None),
                    )
                )
                or 0
            )
            db.rollback()
            counts["journal_backlog"] += unjournaled
            if unjournaled:
                logger.warning(
                    "TOMBSTONE_JOURNAL_BACKLOG workspace_id=%s count=%s journal=%s",
                    workspace_id,
                    unjournaled,
                    "configured" if journal is not None else "not_configured",
                )
            counts["purges_failed_open"] += failed_open
            counts["purges_overdue"] += overdue
            if failed_open or overdue:
                logger.warning(
                    "RETENTION_PURGE_ALERT workspace_id=%s failed=%s overdue=%s",
                    workspace_id,
                    failed_open,
                    overdue,
                )

    if any(v for k, v in counts.items() if k != "workspaces"):
        logger.info("RETENTION_PURGE %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(run_retention_purge(), ensure_ascii=False))
    sys.exit(0)
