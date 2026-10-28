"""Original and derived media objects with their private lifecycle (WP05-T03, ADR 0017).

Every byte stream Homayar keeps for a conversation is one :class:`MediaObject`
row: the ORIGINAL upload (immutable, at most one per conversation) and the
derived artifacts a later stage may produce (NORMALIZED, ENHANCED,
CHANNEL_DERIVED, CLIP, EXPORT), each with its digest, size, type, duration,
storage location, tenant, provenance (who/what produced it, from which
object, under which ingestion and job) and lifecycle state.

Lifecycle: ``active`` → (``retention_pending`` — WP05-T06/WP07) →
``deletion_pending`` → ``deleted``; ``legal_hold`` overrides deletion: a hold
records the deletion request and keeps the bytes until the hold is released,
at which point the pending deletion proceeds.  Rows are never removed; the
content columns are immutable (ORM guard); only lifecycle, hold, integrity
and the SET NULL links may change.  Physical deletion happens *after* the
state transition committed and is idempotent: a row stays
``deletion_pending`` until the object is gone.

A hold follows lineage (HOM-313): a derived object of a held source is
created held, and a hold or release by id covers the object's descendants.
Lock order: the meeting row first, then media rows (as the derivative job
and the purge paths do), so these paths serialize per conversation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    MediaIntegrity,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    Recording,
    Workspace,
    new_id,
)
from backend.media.channel_store import store_channel_inspection
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger
from backend.storage.base import ObjectNotFoundError, ObjectStorage, StoredObject

LIVE_STATES = frozenset(
    {
        MediaLifecycleState.ACTIVE,
        MediaLifecycleState.RETENTION_PENDING,
        MediaLifecycleState.LEGAL_HOLD,
    }
)
_VERIFY_CHUNK = 1024 * 1024
_MAX_REASON = 40
_MAX_HOLD_REASON = 200


class MediaLifecycleError(ValueError):
    """A refused lifecycle transition (stable ``code``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _details(media: MediaObject, **extra: Any) -> dict[str, Any]:
    details: dict[str, Any] = {
        "media_id": media.id,
        "kind": media.kind.value,
        "state": media.lifecycle_state.value,
        "meeting_id": media.meeting_id,
    }
    details.update({key: value for key, value in extra.items() if value is not None})
    return details


def register_original(
    db: Session,
    *,
    workspace: Workspace,
    meeting: Meeting,
    recording: Recording,
    storage_backend: str,
    producer: str,
    ingestion_id: str | None,
    job_id: str | None,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
    channel_inspection: dict[str, Any] | None = None,
) -> MediaObject:
    """Record the ORIGINAL object of a conversation from its verified recording.

    Called inside the upload transaction after the object was stored with a
    verified digest; the recording row is linked to the media row.  The
    partial unique index refuses a second ORIGINAL for the same conversation.
    ``channel_inspection`` (HOM-37) is kept with the object, once, before it is
    inserted.
    """
    media = MediaObject(
        id=new_id(),
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        meeting_id=meeting.id,
        ingestion_id=ingestion_id,
        job_id=job_id,
        kind=MediaKind.ORIGINAL,
        storage_backend=storage_backend,
        storage_key=recording.storage_key,
        content_type=recording.media_type,
        container_format=recording.container_format,
        size_bytes=recording.size_bytes,
        sha256=recording.checksum_sha256,
        duration_seconds=recording.duration_seconds,
        channel_count=recording.channel_count,
        sample_rate_hz=recording.sample_rate_hz,
        original_filename=recording.original_filename,
        producer=producer,
        created_at=now,
        created_by_user_id=actor_user_id,
        lifecycle_state=MediaLifecycleState.ACTIVE,
        retention_expires_at=meeting.retention_expires_at,
    )
    if channel_inspection is not None:
        store_channel_inspection(db, media_object=media, record=channel_inspection)
    db.add(media)
    db.flush()
    recording.media_object_id = media.id
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_REGISTERED,
        actor_user_id=actor_user_id,
        workspace_id=workspace.id,
        target_type="media",
        target_id=media.id,
        details=_details(
            media, size_bytes=media.size_bytes, producer=producer, ingestion_id=ingestion_id
        ),
        settings=settings,
    )
    return media


def register_derived(
    db: Session,
    *,
    source: MediaObject,
    kind: MediaKind,
    stored: StoredObject,
    storage_backend: str,
    producer: str,
    job_id: str | None,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
    container_format: str | None = None,
    duration_seconds: int | None = None,
    channel_count: int | None = None,
    sample_rate_hz: int | None = None,
) -> MediaObject:
    """Record an artifact derived from ``source`` (never an ORIGINAL).

    The stored object must already be complete and digest-verified by the
    storage layer; the row inherits tenant, conversation, ingestion and
    retention from its source so a derived object can never outlive the
    retention of what it was made from.

    The meeting row and then the source row are locked and the source is read
    again (HOM-313), so the decision uses the committed state even when the
    caller loaded ``source`` earlier.  A held source gives a held object in
    the same transaction (``media.legal_hold_inherited``).
    """
    if kind is MediaKind.ORIGINAL:
        raise MediaLifecycleError("invalid_media_kind", "A derived object cannot be ORIGINAL")
    lock_meeting(db, workspace_id=source.workspace_id, meeting_id=source.meeting_id, exclusive=True)
    # The fresh read below drops unflushed changes; flush the caller's own first.
    db.flush()
    locked = media_by_id(db, workspace_id=source.workspace_id, media_id=source.id, for_update=True)
    if locked is None or not may_derive_from(locked):
        raise MediaLifecycleError("source_not_live", "The source object is not live")
    source = locked
    if stored.tenant_id != source.workspace_id:
        raise MediaLifecycleError("tenant_mismatch", "The object belongs to another tenant")
    media = MediaObject(
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        meeting_id=source.meeting_id,
        ingestion_id=source.ingestion_id,
        job_id=job_id,
        derived_from_id=source.id,
        kind=kind,
        storage_backend=storage_backend,
        storage_key=stored.key,
        content_type=stored.content_type,
        container_format=container_format,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        duration_seconds=duration_seconds,
        channel_count=channel_count,
        sample_rate_hz=sample_rate_hz,
        original_filename=stored.original_filename,
        producer=producer,
        created_at=now,
        created_by_user_id=actor_user_id,
        lifecycle_state=MediaLifecycleState.ACTIVE,
        retention_expires_at=source.retention_expires_at,
    )
    held = source.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    if held:
        # Held when inserted: the commit never shows this row active.
        _copy_hold(media, holder=source, now=now)
    db.add(media)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_REGISTERED,
        actor_user_id=actor_user_id,
        workspace_id=source.workspace_id,
        target_type="media",
        target_id=media.id,
        details=_details(
            media, size_bytes=media.size_bytes, producer=producer, derived_from_id=source.id
        ),
        settings=settings,
    )
    if held:
        _record_inherited(
            db, media=media, holder=source, actor_user_id=actor_user_id, settings=settings
        )
    return media


def may_derive_from(source: MediaObject) -> bool:
    """A live source, unless it is held with a recorded deletion request.

    The owner asked for that content to go; the hold only delays the
    deletion, so no new copy is made of it (HOM-313).
    """
    if source.lifecycle_state not in LIVE_STATES:
        return False
    return not (
        source.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
        and source.deletion_requested_at is not None
    )


def lock_meeting(
    db: Session, *, workspace_id: str, meeting_id: str | None, exclusive: bool = False
) -> None:
    """Lock the meeting row before any media row of it (HOM-313).

    Registration (``exclusive``: ``FOR UPDATE``, as the derivative job and
    the purge paths lock it) and the hold paths take this lock first, so a
    hold and a registration of the same conversation run one after the other,
    and a statement that reads the meeting's media rows after the lock sees
    every committed row.  The hold paths take ``FOR KEY SHARE``: it still
    conflicts with ``FOR UPDATE``, but not with a plain update of the meeting
    row.  A conversation deletion writes its tombstone after it locked the
    media rows; a stronger lock here would deadlock with it.  An object whose
    meeting row is gone (``meeting_id`` null) has no lock to take.
    """
    if meeting_id is None:
        return
    db.execute(
        select(Meeting.id)
        .where(Meeting.id == meeting_id, Meeting.workspace_id == workspace_id)
        .with_for_update(read=not exclusive, key_share=not exclusive)
    )


def _locked(statement: Any, for_update: bool) -> Any:
    """Row lock plus a fresh read: a lifecycle decision is made on the
    committed state, never on a copy loaded before a concurrent hold or
    deletion committed (WP07-T03)."""
    if not for_update:
        return statement
    return statement.with_for_update().execution_options(populate_existing=True)


def media_for_meeting(
    db: Session, *, workspace_id: str, meeting_id: str, for_update: bool = False
) -> list[MediaObject]:
    statement = (
        select(MediaObject)
        .where(MediaObject.workspace_id == workspace_id, MediaObject.meeting_id == meeting_id)
        .order_by(MediaObject.created_at.asc(), MediaObject.id.asc())
    )
    return list(db.scalars(_locked(statement, for_update)))


def media_by_id(
    db: Session, *, workspace_id: str, media_id: str, for_update: bool = False
) -> MediaObject | None:
    statement = select(MediaObject).where(
        MediaObject.workspace_id == workspace_id, MediaObject.id == media_id
    )
    return db.scalar(_locked(statement, for_update))


def original_for_meeting(db: Session, *, workspace_id: str, meeting_id: str) -> MediaObject | None:
    return db.scalar(
        select(MediaObject).where(
            MediaObject.workspace_id == workspace_id,
            MediaObject.meeting_id == meeting_id,
            MediaObject.kind == MediaKind.ORIGINAL,
        )
    )


def request_deletion(
    db: Session,
    *,
    media: MediaObject,
    reason: str,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> bool:
    """Ask for the bytes to go; returns whether the caller may delete them now.

    ``deleted`` rows are left alone; a ``legal_hold`` row records the request
    and keeps its bytes (``False``); every other live row becomes
    ``deletion_pending`` (``True``).  A repeated request is idempotent.
    """
    if media.lifecycle_state is MediaLifecycleState.DELETED:
        return False
    if media.deletion_requested_at is None:
        media.deletion_requested_at = now
        media.deletion_reason = reason[:_MAX_REASON]
        record_audit_event(
            db,
            event_type=AuditEventType.MEDIA_DELETION_REQUESTED,
            actor_user_id=actor_user_id,
            workspace_id=media.workspace_id,
            target_type="media",
            target_id=media.id,
            details=_details(media, reason=media.deletion_reason),
            settings=settings,
        )
    if media.lifecycle_state is MediaLifecycleState.LEGAL_HOLD:
        return False
    media.lifecycle_state = MediaLifecycleState.DELETION_PENDING
    return True


def mark_deleted(
    db: Session,
    *,
    media: MediaObject,
    now: datetime,
    settings: Settings,
    actor_user_id: str | None = None,
) -> None:
    """The object is gone from storage: close the row (idempotent)."""
    if media.lifecycle_state is MediaLifecycleState.DELETED:
        return
    if media.lifecycle_state is MediaLifecycleState.LEGAL_HOLD:
        raise MediaLifecycleError("legal_hold", "A held object cannot be marked deleted")
    media.lifecycle_state = MediaLifecycleState.DELETED
    media.deleted_at = now
    # The recording that presents an ORIGINAL is unavailable once its bytes
    # are gone, whichever path removed them (a released hold closed by a
    # sweep included): the row never claims bytes that no longer exist.
    if media.kind is MediaKind.ORIGINAL:
        recording = db.scalar(select(Recording).where(Recording.media_object_id == media.id))
        if recording is not None and recording.deleted_at is None:
            recording.deleted_at = now
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_DELETED,
        actor_user_id=actor_user_id,
        workspace_id=media.workspace_id,
        target_type="media",
        target_id=media.id,
        details=_details(media, reason=media.deletion_reason),
        settings=settings,
    )


def retire_meeting_media(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    reason: str,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> list[MediaObject]:
    """Request deletion of every object of a conversation; returns those now pending.

    Held objects keep their bytes and are not returned; the caller commits and
    then calls :func:`delete_objects` on the returned rows.
    """
    pending: list[MediaObject] = []
    for media in media_for_meeting(
        db, workspace_id=workspace_id, meeting_id=meeting_id, for_update=True
    ):
        if request_deletion(
            db, media=media, reason=reason, actor_user_id=actor_user_id, now=now, settings=settings
        ):
            pending.append(media)
    return pending


def delete_objects(
    db: Session,
    storage: ObjectStorage,
    *,
    objects: Iterable[MediaObject],
    now: datetime,
    settings: Settings,
    actor_user_id: str | None = None,
) -> list[MediaObject]:
    """Remove the bytes of ``deletion_pending`` rows and close them.

    Not transactional with the caller's state change (object stores are not);
    a failed removal leaves the row pending for the next sweep and is logged
    without the key.  Returns the rows closed in this call.

    Point of no return (WP07-T03): each row is locked and re-read right
    before its bytes are removed, and the lock is held until the caller
    commits.  A hold (or anything else) committed after the caller loaded
    the row is therefore seen here and the bytes stay; a hold that arrives
    while the removal runs waits for the lock and then finds ``deleted``.
    A storage error (denied, unavailable, failed) is never read as "gone".
    """
    closed: list[MediaObject] = []
    for loaded in objects:
        media = media_by_id(
            db, workspace_id=loaded.workspace_id, media_id=loaded.id, for_update=True
        )
        if media is None or media.lifecycle_state is not MediaLifecycleState.DELETION_PENDING:
            continue
        try:
            storage.delete(media.workspace_id, media.storage_key)
        except Exception as exc:
            logger.error(
                "MEDIA_OBJECT_DELETE_FAILED media_id=%s kind=%s error_type=%s",
                media.id,
                media.kind.value,
                type(exc).__name__,
            )
            continue
        mark_deleted(db, media=media, now=now, settings=settings, actor_user_id=actor_user_id)
        closed.append(media)
    return closed


MIN_HOLD_REASON = 3
MAX_HOLD_REVIEW_DAYS = 3650


def _validate_hold_reason(reason: str) -> str:
    cleaned = " ".join(str(reason).split())
    if len(cleaned) < MIN_HOLD_REASON:
        raise MediaLifecycleError("hold_reason_required", "A legal hold needs a reason")
    return cleaned[:_MAX_HOLD_REASON]


def validate_hold_review_at(review_at: datetime, *, now: datetime) -> datetime:
    """A review date must lie in the future and within the maximum horizon."""
    if review_at.tzinfo is None:
        raise MediaLifecycleError("hold_review_invalid", "The review date needs a timezone")
    if review_at <= now:
        raise MediaLifecycleError("hold_review_invalid", "The review date must be in the future")
    if review_at > now + timedelta(days=MAX_HOLD_REVIEW_DAYS):
        raise MediaLifecycleError(
            "hold_review_invalid", f"The review date may be at most {MAX_HOLD_REVIEW_DAYS} days out"
        )
    return review_at


def place_legal_hold(
    db: Session,
    *,
    media: MediaObject,
    reason: str,
    review_at: datetime,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> bool:
    """Hold the object (WP07-T02): owner, reason and review date are recorded.

    Returns ``True`` when this call placed the hold; an already held object
    is left untouched (``False``), so repeated requests are idempotent and
    never rewrite who placed the hold or why.  A hold overrides retention
    expiry, a recorded deletion request and orphan/temporary cleanup until
    it is released; it cannot restore an object that is already deleted.
    """
    if media.lifecycle_state is MediaLifecycleState.DELETED:
        raise MediaLifecycleError("already_deleted", "The object was already deleted")
    cleaned_reason = _validate_hold_reason(reason)
    review_at = validate_hold_review_at(review_at, now=now)
    if media.lifecycle_state is MediaLifecycleState.LEGAL_HOLD:
        return False
    media.lifecycle_state = MediaLifecycleState.LEGAL_HOLD
    media.legal_hold_at = now
    media.legal_hold_reason = cleaned_reason
    media.legal_hold_by_user_id = actor_user_id
    media.legal_hold_review_at = review_at
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_LEGAL_HOLD_PLACED,
        actor_user_id=actor_user_id,
        workspace_id=media.workspace_id,
        target_type="media",
        target_id=media.id,
        details=_details(
            media,
            review_at=review_at.isoformat(),
            deletion_requested=media.deletion_requested_at is not None,
        ),
        settings=settings,
    )
    return True


def release_legal_hold(
    db: Session,
    *,
    media: MediaObject,
    reason: str,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> bool:
    """Lift the hold with an audited reason; returns whether a recorded
    deletion request is now pending (the caller removes the bytes after commit)."""
    cleaned_reason = _validate_hold_reason(reason)
    if media.lifecycle_state is not MediaLifecycleState.LEGAL_HOLD:
        return False
    return _lift_hold(
        db,
        media=media,
        cleaned_reason=cleaned_reason,
        actor_user_id=actor_user_id,
        settings=settings,
    )


def _lift_hold(
    db: Session,
    *,
    media: MediaObject,
    cleaned_reason: str,
    actor_user_id: str | None,
    settings: Settings,
    source_id: str | None = None,
) -> bool:
    held_since = media.legal_hold_at
    media.legal_hold_at = None
    media.legal_hold_reason = None
    media.legal_hold_by_user_id = None
    media.legal_hold_review_at = None
    pending = media.deletion_requested_at is not None
    media.lifecycle_state = (
        MediaLifecycleState.DELETION_PENDING if pending else MediaLifecycleState.ACTIVE
    )
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_LEGAL_HOLD_RELEASED,
        actor_user_id=actor_user_id,
        workspace_id=media.workspace_id,
        target_type="media",
        target_id=media.id,
        details=_details(
            media,
            deletion_pending=pending,
            release_reason=cleaned_reason,
            held_since=held_since.isoformat() if held_since else None,
            source_id=source_id,
        ),
        settings=settings,
    )
    return pending


def _copy_hold(media: MediaObject, *, holder: MediaObject, now: datetime) -> None:
    """Hold ``media`` with the recorded owner, reason and review date of ``holder``."""
    media.lifecycle_state = MediaLifecycleState.LEGAL_HOLD
    media.legal_hold_at = now
    media.legal_hold_reason = holder.legal_hold_reason
    media.legal_hold_by_user_id = holder.legal_hold_by_user_id
    media.legal_hold_review_at = holder.legal_hold_review_at


def _record_inherited(
    db: Session,
    *,
    media: MediaObject,
    holder: MediaObject,
    actor_user_id: str | None,
    settings: Settings,
) -> None:
    review_at = media.legal_hold_review_at
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_LEGAL_HOLD_INHERITED,
        actor_user_id=actor_user_id,
        workspace_id=media.workspace_id,
        target_type="media",
        target_id=media.id,
        details=_details(
            media,
            derived_from_id=media.derived_from_id,
            source_id=holder.id,
            review_at=review_at.isoformat() if review_at else None,
            deletion_requested=media.deletion_requested_at is not None,
        ),
        settings=settings,
    )


def _lock_object(db: Session, *, workspace_id: str, media_id: str) -> MediaObject | None:
    """The object by id, locked and read again after its meeting row is locked."""
    media = media_by_id(db, workspace_id=workspace_id, media_id=media_id)
    if media is None:
        return None
    lock_meeting(db, workspace_id=workspace_id, meeting_id=media.meeting_id)
    return media_by_id(db, workspace_id=workspace_id, media_id=media_id, for_update=True)


def _descendant_ids(db: Session, *, media: MediaObject) -> set[str]:
    found: set[str] = set()
    frontier = {media.id}
    while frontier:
        children = set(
            db.scalars(
                select(MediaObject.id).where(
                    MediaObject.workspace_id == media.workspace_id,
                    MediaObject.derived_from_id.in_(frontier),
                )
            )
        )
        frontier = children - found - {media.id}
        found |= frontier
    return found


def _locked_descendants(db: Session, *, media: MediaObject) -> list[MediaObject]:
    """Every descendant of the locked ``media``, locked, oldest first.

    The ids are read after the caller's locks, and the rows are locked in the
    same order as the meeting's rows elsewhere (``created_at``, ``id``).  A new
    derived row needs its source's lock, so reading again after the locks finds
    any row inserted in between; with the meeting lock held there is none.
    """
    seen: set[str] = set()
    locked: list[MediaObject] = []
    while ids := _descendant_ids(db, media=media) - seen:
        seen |= ids
        statement = (
            select(MediaObject)
            .where(MediaObject.workspace_id == media.workspace_id, MediaObject.id.in_(ids))
            .order_by(MediaObject.created_at.asc(), MediaObject.id.asc())
        )
        locked.extend(db.scalars(_locked(statement, True)))
    return sorted(locked, key=lambda row: (row.created_at, row.id))


def hold_meeting_objects(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    reason: str,
    review_at: datetime,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> list[MediaObject]:
    """Hold every object of a conversation that is not deleted; returns them all.

    The meeting row is locked before the objects are read (HOM-313): a derived
    object whose registration held that lock is committed and read here, and
    one registered after this commit waits and is created held.  Each object
    is held directly (``media.legal_hold_placed``).
    """
    lock_meeting(db, workspace_id=workspace_id, meeting_id=meeting_id)
    objects = media_for_meeting(
        db, workspace_id=workspace_id, meeting_id=meeting_id, for_update=True
    )
    for media in objects:
        if media.lifecycle_state is MediaLifecycleState.DELETED:
            continue
        place_legal_hold(
            db,
            media=media,
            reason=reason,
            review_at=review_at,
            actor_user_id=actor_user_id,
            now=now,
            settings=settings,
        )
    return objects


def hold_with_descendants(
    db: Session,
    *,
    workspace_id: str,
    media_id: str,
    reason: str,
    review_at: datetime,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
) -> MediaObject | None:
    """Hold one object by id and every descendant that is not deleted or held yet.

    The object gets ``place_legal_hold`` (an already held object is left as it
    is); each descendant takes the object's recorded hold and gets
    ``media.legal_hold_inherited``.  Like the meeting hold, this covers a
    ``deletion_pending`` descendant: its recorded request is kept and waits
    for the release.  Returns ``None`` when there is no such object.
    """
    media = _lock_object(db, workspace_id=workspace_id, media_id=media_id)
    if media is None:
        return None
    place_legal_hold(
        db,
        media=media,
        reason=reason,
        review_at=review_at,
        actor_user_id=actor_user_id,
        now=now,
        settings=settings,
    )
    for descendant in _locked_descendants(db, media=media):
        if descendant.lifecycle_state not in (
            MediaLifecycleState.DELETED,
            MediaLifecycleState.LEGAL_HOLD,
        ):
            _copy_hold(descendant, holder=media, now=now)
            _record_inherited(
                db, media=descendant, holder=media, actor_user_id=actor_user_id, settings=settings
            )
    return media


def release_with_descendants(
    db: Session,
    *,
    workspace_id: str,
    media_id: str,
    reason: str,
    actor_user_id: str | None,
    settings: Settings,
) -> tuple[MediaObject | None, list[MediaObject]]:
    """Release one held object by id and every held descendant.

    Returns the object (``None`` when there is none) and the released rows
    whose recorded deletion request is now pending, oldest first; the caller
    removes their bytes after the commit.  An object that is not held is
    refused (``not_held``) and nothing changes.
    """
    media = _lock_object(db, workspace_id=workspace_id, media_id=media_id)
    if media is None:
        return None, []
    if media.lifecycle_state is not MediaLifecycleState.LEGAL_HOLD:
        raise MediaLifecycleError("not_held", "The object is not held")
    cleaned_reason = _validate_hold_reason(reason)
    pending: list[MediaObject] = []
    if _lift_hold(
        db,
        media=media,
        cleaned_reason=cleaned_reason,
        actor_user_id=actor_user_id,
        settings=settings,
    ):
        pending.append(media)
    for descendant in _locked_descendants(db, media=media):
        if descendant.lifecycle_state is not MediaLifecycleState.LEGAL_HOLD:
            continue
        if _lift_hold(
            db,
            media=descendant,
            cleaned_reason=cleaned_reason,
            actor_user_id=actor_user_id,
            settings=settings,
            source_id=media.id,
        ):
            pending.append(descendant)
    return media, pending


def holds_due_for_review(db: Session, *, workspace_id: str, now: datetime) -> int:
    """Number of held objects whose review date has passed (reported, never acted on)."""
    return int(
        db.scalar(
            select(func.count(MediaObject.id)).where(
                MediaObject.workspace_id == workspace_id,
                MediaObject.lifecycle_state == MediaLifecycleState.LEGAL_HOLD,
                MediaObject.legal_hold_review_at <= now,
            )
        )
        or 0
    )


def verify_integrity(
    db: Session,
    storage: ObjectStorage,
    *,
    media: MediaObject,
    actor_user_id: str | None,
    now: datetime,
    settings: Settings,
    chunk_size: int = _VERIFY_CHUNK,
) -> MediaIntegrity:
    """Re-read the object and compare digest and size with the row (audited)."""
    if media.lifecycle_state is MediaLifecycleState.DELETED:
        raise MediaLifecycleError("already_deleted", "The object was already deleted")
    result = MediaIntegrity.VERIFIED
    try:
        digest = hashlib.sha256()
        size = 0
        with storage.open_reader(media.workspace_id, media.storage_key) as reader:
            while True:
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                if size > media.size_bytes:
                    break
        if size != media.size_bytes or digest.hexdigest() != media.sha256:
            result = MediaIntegrity.CORRUPT
    except ObjectNotFoundError:
        result = MediaIntegrity.MISSING
    media.integrity = result
    media.last_verified_at = now
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_INTEGRITY_CHECKED,
        actor_user_id=actor_user_id,
        workspace_id=media.workspace_id,
        target_type="media",
        target_id=media.id,
        outcome="success" if result is MediaIntegrity.VERIFIED else "failed",
        details=_details(media, integrity=result.value),
        settings=settings,
    )
    return result
