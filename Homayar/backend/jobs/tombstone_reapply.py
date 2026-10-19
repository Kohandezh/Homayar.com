"""Reapply journaled tombstones to a restored deployment (WP07-T04, ADR 0022).

Run after a PostgreSQL (and object-store) restore, after the forward
migrations and BEFORE the API and workers take traffic::

    python -m backend.jobs.tombstone_reapply reapply
    python -m backend.jobs.tombstone_reapply verify   # exit 0 = safe to open

``reapply`` reads the tombstone journal (``TOMBSTONE_JOURNAL_DIR``) and, per
record, in the record's own tenant-bound session:

* a conversation that is alive again is tombstoned once more (its queued or
  running jobs are cancelled, share links revoked, media deletion requested —
  holds present in the restored database are respected) and audited
  ``meeting.tombstone_reapplied``;
* its purge row is (re)opened with a fresh attempt budget and
  ``reapplied_at``, so content rows restored from the backup are removed again
  and bytes that an object-store restore brought back are removed as well;
* one purge attempt runs at once (unless ``--no-purge``).

A record whose conversation does not exist in the restored database (the
backup predates it) needs no database change; its journaled object keys are
removed only with ``--remove-unknown-keys`` (never keys of a held object: the
journal omits them).  ``verify`` exits non-zero while any journaled
conversation is alive or the journal has untrustworthy lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from sqlalchemy import select

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ConversationPurge,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    PurgeState,
    StageCheckpoint,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.failures import MEETING_DELETED, cancel_job
from backend.jobs.leases import database_now
from backend.jobs.outbox import cancel_dispatch
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.conversation_purge_service import (
    ACTIVE_JOB_STATES,
    purge_for_meeting,
    run_purge_attempt,
    tombstone_meeting,
)
from backend.services.logging_service import logger
from backend.services.tombstone_journal import TombstoneJournal, TombstoneRecord, journal_for
from backend.storage import ObjectStorage, StorageError, create_object_storage


def _journal(settings: Settings, journal: TombstoneJournal | None) -> TombstoneJournal:
    journal = journal or journal_for(settings)
    if journal is None:
        raise SystemExit("TOMBSTONE_JOURNAL_DIR is not configured; nothing can be reapplied")
    return journal


def _reapply_one(
    record: TombstoneRecord,
    *,
    storage: ObjectStorage,
    settings: Settings,
    now: datetime | None,
    run_purge: bool,
    remove_unknown_keys: bool,
    counts: dict[str, int],
) -> None:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=record.workspace_id)
        current = now or database_now(db)
        meeting = db.scalar(
            select(Meeting)
            .where(Meeting.id == record.meeting_id, Meeting.workspace_id == record.workspace_id)
            .with_for_update()
        )
        if meeting is None:
            counts["meeting_absent"] += 1
            workspace_known = db.get(Workspace, record.workspace_id) is not None
            # A key still referenced by a live row of the restored database is
            # never removed (keys are unique; this is a belt-and-braces check).
            referenced = set(
                db.scalars(
                    select(MediaObject.storage_key).where(
                        MediaObject.workspace_id == record.workspace_id,
                        MediaObject.storage_key.in_(record.object_keys),
                        MediaObject.lifecycle_state != MediaLifecycleState.DELETED,
                    )
                )
            ) | set(
                db.scalars(
                    select(StageCheckpoint.artifact_key).where(
                        StageCheckpoint.workspace_id == record.workspace_id,
                        StageCheckpoint.artifact_key.in_(record.object_keys),
                        StageCheckpoint.object_deleted_at.is_(None),
                    )
                )
            )
            db.rollback()
            if remove_unknown_keys and workspace_known:
                for key in record.object_keys:
                    if key in referenced:
                        continue
                    try:
                        if storage.delete(record.workspace_id, key):
                            counts["unknown_keys_removed"] += 1
                    except StorageError:
                        counts["unknown_keys_failed"] += 1
            return

        if meeting.deleted_at is None:
            for job in db.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.workspace_id == record.workspace_id,
                    ProcessingJob.meeting_id == meeting.id,
                    ProcessingJob.state.in_(ACTIVE_JOB_STATES),
                )
            ):
                if cancel_job(
                    db,
                    job,
                    reason=MEETING_DELETED,
                    message="The meeting was deleted before the restored backup was taken",
                    now=current,
                ):
                    cancel_dispatch(db, job.id)
                    counts["jobs_cancelled"] += 1
            purge = tombstone_meeting(
                db,
                meeting=meeting,
                reason=record.reason,
                actor_user_id=None,
                now=current,
                settings=settings,
            )
            record_audit_event(
                db,
                event_type=AuditEventType.MEETING_TOMBSTONE_REAPPLIED,
                workspace_id=record.workspace_id,
                target_type="meeting",
                target_id=meeting.id,
                details={
                    "purge_id": purge.id,
                    "journal_purge_id": record.purge_id,
                    "tombstoned_at": record.tombstoned_at.isoformat(),
                    "reason": record.reason,
                },
                settings=settings,
            )
            counts["reapplied"] += 1
        else:
            purge = purge_for_meeting(
                db, workspace_id=record.workspace_id, meeting_id=meeting.id
            ) or tombstone_meeting(
                db,
                meeting=meeting,
                reason=record.reason,
                actor_user_id=None,
                now=current,
                settings=settings,
            )
            counts["already_tombstoned"] += 1

        # Content rows or bytes may be back from the backups: purge again.
        if purge.state is not PurgeState.PENDING or purge.content_purged_at is not None:
            counts["purges_reopened"] += 1
        purge.state = PurgeState.PENDING
        purge.completed_at = None
        purge.content_purged_at = None
        purge.attempts = 0
        purge.last_error = None
        purge.next_attempt_at = current
        purge.reapplied_at = current
        purge.journaled_at = purge.journaled_at or current
        purge_id = purge.id
        db.commit()

        if run_purge:
            outcome = run_purge_attempt(
                db,
                storage,
                purge_id=purge_id,
                workspace_id=record.workspace_id,
                now=current,
                settings=settings,
            )
            if outcome.state is PurgeState.COMPLETED:
                counts["purges_completed"] += 1


def reapply_tombstones(
    settings: Settings | None = None,
    *,
    journal: TombstoneJournal | None = None,
    storage: ObjectStorage | None = None,
    now: datetime | None = None,
    run_purge: bool = True,
    remove_unknown_keys: bool = False,
) -> dict[str, int]:
    settings = settings or get_settings()
    journal = _journal(settings, journal)
    storage = storage or create_object_storage(settings)
    records, rejected = journal.read()
    counts = {
        "records": len(records),
        "rejected_lines": rejected,
        "reapplied": 0,
        "already_tombstoned": 0,
        "meeting_absent": 0,
        "purges_reopened": 0,
        "purges_completed": 0,
        "jobs_cancelled": 0,
        "unknown_keys_removed": 0,
        "unknown_keys_failed": 0,
    }
    for record in sorted(records, key=lambda r: (r.tombstoned_at, r.meeting_id)):
        _reapply_one(
            record,
            storage=storage,
            settings=settings,
            now=now,
            run_purge=run_purge,
            remove_unknown_keys=remove_unknown_keys,
            counts=counts,
        )
    if rejected:
        logger.error("TOMBSTONE_JOURNAL_REJECTED_LINES count=%s", rejected)
    logger.info("TOMBSTONE_REAPPLY %s", counts)
    return counts


def verify_tombstones(
    settings: Settings | None = None, *, journal: TombstoneJournal | None = None
) -> dict[str, int]:
    """Counts only; ``alive`` and ``rejected_lines`` must be 0 before traffic."""
    settings = settings or get_settings()
    journal = _journal(settings, journal)
    records, rejected = journal.read()
    counts = {
        "records": len(records),
        "rejected_lines": rejected,
        "alive": 0,
        "purges_open": 0,
        "purges_failed": 0,
    }
    for record in records:
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=record.workspace_id)
            meeting = db.scalar(
                select(Meeting).where(
                    Meeting.id == record.meeting_id, Meeting.workspace_id == record.workspace_id
                )
            )
            if meeting is not None and meeting.deleted_at is None:
                counts["alive"] += 1
            purge = db.scalar(
                select(ConversationPurge).where(
                    ConversationPurge.workspace_id == record.workspace_id,
                    ConversationPurge.meeting_id == record.meeting_id,
                )
            )
            if purge is not None and purge.state is PurgeState.PENDING:
                counts["purges_open"] += 1
            if purge is not None and purge.state is PurgeState.FAILED:
                counts["purges_failed"] += 1
            db.rollback()
    return counts


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    parser = argparse.ArgumentParser(prog="python -m backend.jobs.tombstone_reapply")
    parser.add_argument("command", choices=("reapply", "verify"))
    parser.add_argument("--no-purge", action="store_true")
    parser.add_argument("--remove-unknown-keys", action="store_true")
    args = parser.parse_args(argv)
    configure_database()
    if args.command == "reapply":
        counts = reapply_tombstones(
            run_purge=not args.no_purge, remove_unknown_keys=args.remove_unknown_keys
        )
        print(json.dumps(counts))
        return 1 if counts["rejected_lines"] else 0
    counts = verify_tombstones()
    print(json.dumps(counts))
    return 1 if counts["alive"] or counts["rejected_lines"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
