"""Media reconciliation: retry-safe finalize, orphan detection and missing flags (WP05-T04).

The periodic sweep picks up each workspace and performs four duties:

1. **Pending retry** — ``DELETION_PENDING`` rows whose deletion request is
   older than the grace period are retried; ``delete_objects`` removes the
   bytes and closes the row only when the object is gone.
2. **Orphan detection** — objects that exist under the tenant prefix without
   a live database row (``MediaObject``, ``StageCheckpoint``, pre-ledger
   ``Recording``) are reported (default) or removed after the grace period.
   Removal is gated on ``MEDIA_RECONCILIATION_REMOVE_ORPHANS`` and audited
   without the storage key.
3. **Missing detection** — live media rows whose bytes are absent from
   storage are flagged ``MISSING`` through ``verify_integrity``; never
   fabricated, never removed.
4. **Incomplete purge** — backend-private temporaries that were never
   published are cleaned up.

Discovery reuses ``discover_workspaces`` from the checkpoint layer; each
workspace is processed in its own tenant-bound transaction.  A listing
failure leaves the orphan and missing steps untouched for that pass
(the pending retry has already been committed); a backend read failure
while verifying one row skips only that row.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    MediaIntegrity,
    MediaLifecycleState,
    MediaObject,
    Recording,
    StageCheckpoint,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.checkpoints import discover_workspaces
from backend.jobs.leases import database_now
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger
from backend.services.media_lifecycle_service import (
    MediaLifecycleError,
    delete_objects,
    holds_due_for_review,
    verify_integrity,
)
from backend.storage import ObjectStorage, StorageError, create_object_storage


def reconcile_media_objects(
    settings: Settings | None = None,
    *,
    storage: ObjectStorage | None = None,
    limit: int | None = None,
    now: datetime | None = None,
    workspace_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    """Retry pending deletions, report/remove orphans, flag missing.

    Returns counts: ``workspaces``, ``pending_closed``,
    ``pending_failed``, ``orphans_found``, ``orphans_removed``,
    ``orphans_failed``, ``missing_flagged``, ``incomplete_purged``,
    ``unavailable``.
    """
    settings = settings or get_settings()
    storage = storage or create_object_storage(settings)
    batch = limit or settings.media_reconciliation_batch_size
    grace = timedelta(seconds=settings.media_reconciliation_grace_seconds)
    counts: dict[str, int] = {
        "workspaces": 0,
        "pending_closed": 0,
        "pending_failed": 0,
        "orphans_found": 0,
        "orphans_removed": 0,
        "orphans_failed": 0,
        "missing_flagged": 0,
        "incomplete_purged": 0,
        "unavailable": 0,
        "holds_review_due": 0,
    }
    remaining = batch
    workspaces = (
        list(workspace_ids)
        if workspace_ids is not None
        else discover_workspaces(
            interval_seconds=settings.media_reconciliation_interval_seconds, now=now
        )
    )

    for workspace_id in workspaces:
        if remaining <= 0:
            break
        counts["workspaces"] += 1

        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            current = now or database_now(db)
            cutoff = current - grace

            # --- (a) PENDING RETRY --------------------------------
            pending_rows = list(
                db.scalars(
                    select(MediaObject)
                    .where(
                        MediaObject.workspace_id == workspace_id,
                        MediaObject.lifecycle_state == MediaLifecycleState.DELETION_PENDING,
                        MediaObject.deletion_requested_at <= cutoff,
                    )
                    .order_by(MediaObject.deletion_requested_at.asc())
                    .limit(remaining)
                )
            )
            if pending_rows:
                closed = delete_objects(
                    db,
                    storage,
                    objects=pending_rows,
                    now=current,
                    settings=settings,
                )
                counts["pending_closed"] += len(closed)
                counts["pending_failed"] += len(pending_rows) - len(closed)
                remaining -= len(pending_rows)
                db.commit()
            if remaining <= 0:
                # Budget exhausted by the pending retry: do not list the
                # tenant prefix or load rows for work that cannot run.
                continue

            # --- (b) LISTING --------------------------------------
            try:
                listing = {item.key: item for item in storage.iter_objects(workspace_id)}
            except StorageError as exc:
                counts["unavailable"] += 1
                logger.warning(
                    "MEDIA_RECONCILIATION_LISTING_UNAVAILABLE workspace_id=%s error=%s",
                    workspace_id,
                    exc.code,
                )
                db.commit()
                continue

            # --- (c) LIVE KEYS ------------------------------------
            live_keys: set[str] = set()
            for key in db.scalars(
                select(MediaObject.storage_key).where(
                    MediaObject.workspace_id == workspace_id,
                    MediaObject.lifecycle_state != MediaLifecycleState.DELETED,
                )
            ):
                live_keys.add(key)
            for key in db.scalars(
                select(StageCheckpoint.artifact_key).where(
                    StageCheckpoint.workspace_id == workspace_id,
                    StageCheckpoint.object_deleted_at.is_(None),
                )
            ):
                live_keys.add(key)
            for key in db.scalars(
                select(Recording.storage_key).where(
                    Recording.workspace_id == workspace_id,
                    Recording.media_object_id.is_(None),
                    Recording.deleted_at.is_(None),
                )
            ):
                live_keys.add(key)

            # --- (d) ORPHANS --------------------------------------
            ws_orphans = 0
            ws_orphan_bytes = 0
            ws_oldest_age = 0

            for key, item in listing.items():
                if key in live_keys:
                    continue
                if item.last_modified is None:
                    continue
                if item.last_modified > cutoff:
                    continue
                if remaining <= 0:
                    break
                remaining -= 1
                counts["orphans_found"] += 1
                ws_orphans += 1
                age_seconds = int((current - item.last_modified).total_seconds())
                ws_orphan_bytes += item.size_bytes
                ws_oldest_age = max(ws_oldest_age, age_seconds)

                if settings.media_reconciliation_remove_orphans:
                    try:
                        storage.delete(workspace_id, key)
                    except StorageError as exc:
                        counts["orphans_failed"] += 1
                        logger.error(
                            "MEDIA_ORPHAN_DELETE_FAILED workspace_id=%s error_type=%s",
                            workspace_id,
                            type(exc).__name__,
                        )
                        continue
                    counts["orphans_removed"] += 1
                    record_audit_event(
                        db,
                        event_type=AuditEventType.MEDIA_ORPHAN_REMOVED,
                        workspace_id=workspace_id,
                        target_type="workspace",
                        target_id=workspace_id,
                        details={
                            "size_bytes": item.size_bytes,
                            "age_seconds": age_seconds,
                            "complete": item.complete,
                        },
                        settings=settings,
                    )

            if not settings.media_reconciliation_remove_orphans and ws_orphans > 0:
                record_audit_event(
                    db,
                    event_type=AuditEventType.MEDIA_ORPHANS_REPORTED,
                    workspace_id=workspace_id,
                    target_type="workspace",
                    target_id=workspace_id,
                    details={
                        "count": ws_orphans,
                        "total_bytes": ws_orphan_bytes,
                        "oldest_age_seconds": ws_oldest_age,
                    },
                    settings=settings,
                )

            # --- (e) MISSING --------------------------------------
            live_media = list(
                db.scalars(
                    select(MediaObject).where(
                        MediaObject.workspace_id == workspace_id,
                        MediaObject.lifecycle_state.in_(
                            [
                                MediaLifecycleState.ACTIVE,
                                MediaLifecycleState.RETENTION_PENDING,
                                MediaLifecycleState.LEGAL_HOLD,
                            ]
                        ),
                        MediaObject.created_at <= cutoff,
                        MediaObject.integrity != MediaIntegrity.MISSING,
                    )
                )
            )

            for media in live_media:
                if remaining <= 0:
                    break
                if media.storage_key in listing:
                    continue
                remaining -= 1
                try:
                    result = verify_integrity(
                        db,
                        storage,
                        media=media,
                        actor_user_id=None,
                        now=current,
                        settings=settings,
                    )
                except MediaLifecycleError as exc:
                    logger.warning(
                        "MEDIA_RECONCILIATION_VERIFY_REFUSED media_id=%s code=%s",
                        media.id,
                        exc.code,
                    )
                    continue
                except StorageError as exc:
                    # Backend could not answer (not "object absent"): leave the
                    # row untouched for this pass instead of aborting the sweep.
                    counts["unavailable"] += 1
                    logger.warning(
                        "MEDIA_RECONCILIATION_VERIFY_UNAVAILABLE media_id=%s error=%s",
                        media.id,
                        exc.code,
                    )
                    continue
                if result is MediaIntegrity.MISSING:
                    counts["missing_flagged"] += 1

            # --- (e2) HOLDS DUE FOR REVIEW (report only, WP07-T02) --
            due = holds_due_for_review(db, workspace_id=workspace_id, now=current)
            if due:
                counts["holds_review_due"] += due
                logger.warning(
                    "MEDIA_LEGAL_HOLD_REVIEW_DUE workspace_id=%s count=%s", workspace_id, due
                )

            # --- (f) PURGE INCOMPLETE -----------------------------
            try:
                counts["incomplete_purged"] += storage.purge_incomplete(
                    workspace_id, older_than=cutoff
                )
            except StorageError:
                counts["unavailable"] += 1

            # --- (g) COMMIT ---------------------------------------
            db.commit()

    if any(
        counts[k]
        for k in (
            "pending_closed",
            "pending_failed",
            "orphans_found",
            "orphans_removed",
            "orphans_failed",
            "missing_flagged",
            "incomplete_purged",
            "unavailable",
            "holds_review_due",
        )
    ):
        logger.info("MEDIA_RECONCILIATION %s", counts)

    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(reconcile_media_objects(), ensure_ascii=False))
    sys.exit(0)
