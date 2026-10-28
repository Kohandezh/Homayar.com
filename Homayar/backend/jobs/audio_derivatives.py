"""The job step that derives and registers an ORIGINAL's derivative (HOM-38, ADR 0042).

Runs once per attempt, right after MATERIALIZE, on the materialized ORIGINAL
(its bytes already checked against the stored digest).  It never raises and
never changes what ASR receives, the stage runs, the checkpoints, the provider
usage or the meeting status.  It returns ``"registered"`` or a reason code.

Order (SPEC 5.5):

1. Pre-checks, read-only, before any decode; the first that applies wins.
2. :func:`backend.media.derivatives.derive_audio` in the job's temporary
   directory, then an upload under a random key (each attempt owns its object).
3. The fenced registration: one transaction that proves the lease
   (``lock_owned_or_cancelled``), locks the meeting row and the ORIGINAL row,
   re-checks the source, then calls ``register_derived``.  A stale attempt can
   upload bytes but can never register them.

On a known refusal the attempt removes the object it uploaded; on any other
commit error the bytes stay for the orphan sweep.  Every outcome but
``registered``, ``derivatives_disabled``, ``already_derived`` and
``stale_attempt`` is audited as ``media.derivative_unavailable`` (for operators
only; consumers treat "no derivative" as unknown).
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    Recording,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.leases import StaleLeaseError, lock_owned_or_cancelled
from backend.media import derivatives as media
from backend.media.channel_store import load_channel_inspection
from backend.media.derivatives import DerivedAudio, SourceFacts, derive_audio
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger
from backend.services.media_lifecycle_service import (
    MediaLifecycleError,
    may_derive_from,
    media_by_id,
    original_for_meeting,
    register_derived,
)
from backend.storage import ObjectStorage, ObjectTooLargeError

REGISTERED = "registered"
# media_objects.size_bytes is a 32-bit INTEGER.
_SIZE_COLUMN_MAX = 2_147_483_647
_NOT_AUDITED = frozenset({media.DERIVATIVES_DISABLED, media.ALREADY_DERIVED, media.STALE_ATTEMPT})


@dataclass(frozen=True)
class _Source:
    """What the pre-check read: the ORIGINAL (when there is one) and its facts."""

    media_id: str | None
    facts: SourceFacts | None
    inspection: dict[str, Any] | None


def derive_audio_for_job(
    claim: Any,
    media_path: Path,
    *,
    storage: ObjectStorage,
    settings: Settings,
    heartbeat: Any,
) -> str:
    """Derive, upload and register the ORIGINAL's derivative; never raises."""
    if not settings.media_derivatives_enabled:
        return media.DERIVATIVES_DISABLED
    media_id: str | None = None
    try:
        reason, source = _precheck(claim, settings)
        media_id = source.media_id
        if reason is not None:
            return _unavailable(claim, media_id, reason, settings)
        assert source.facts is not None and media_id is not None
        return _derive_and_register(claim, media_path, source, storage, settings, heartbeat)
    except Exception as exc:  # noqa: BLE001 - the derivation never fails the job (SPEC 5.5)
        logger.error(
            "MEDIA_DERIVATIVE_ERROR media_id=%s error_type=%s", media_id, type(exc).__name__
        )
    # An unexpected error is still a reason: one try to keep it under the fence (a stale
    # attempt writes nothing), and never an error for the job.
    try:
        return _unavailable(claim, media_id, media.DERIVATIVE_FAILED, settings)
    except Exception as exc:  # noqa: BLE001 - the derivation never fails the job (SPEC 5.5)
        logger.error(
            "MEDIA_DERIVATIVE_UNAVAILABLE media_id=%s reason=%s error_type=%s",
            media_id,
            media.DERIVATIVE_FAILED,
            type(exc).__name__,
        )
        return media.DERIVATIVE_FAILED


def _max_bytes(settings: Settings) -> int:
    """The storage cap of every object (``max_upload_size_mb``), at most the size column."""
    return min(settings.max_upload_size_mb * 1024 * 1024, _SIZE_COLUMN_MAX)


def _existing(db: Session, original_id: str) -> bool:
    return (
        db.scalar(
            select(MediaObject.id)
            .where(
                MediaObject.derived_from_id == original_id,
                MediaObject.producer == media.DERIVATIVE_PRODUCER,
                MediaObject.lifecycle_state != MediaLifecycleState.DELETED,
            )
            .limit(1)
        )
        is not None
    )


def _source_refusal(db: Session, original: MediaObject, claim: Any) -> str | None:
    """The source checks repeated under the lock (``source_facts_unknown`` is not one).

    A held ORIGINAL is derived, and its derivative is created held (HOM-313);
    a held one with a recorded deletion request is not live for this.
    """
    if not may_derive_from(original):
        return media.SOURCE_NOT_LIVE
    if original.sha256 != claim.recording_sha256:
        return media.SOURCE_CHANGED
    if _existing(db, original.id):
        return media.ALREADY_DERIVED
    return None


def _facts(original: MediaObject, inspection: dict[str, Any] | None) -> SourceFacts | None:
    channels, rate = original.channel_count, original.sample_rate_hz
    if channels is None or not 1 <= channels <= media.MAX_CHANNELS or rate is None or rate <= 0:
        return None
    codec = inspection.get("codec") if isinstance(inspection, dict) else None
    return SourceFacts(
        media_id=original.id,
        sha256=original.sha256,
        sample_rate_hz=rate,
        channel_count=channels,
        codec=codec if isinstance(codec, str) else None,
        duration_seconds=(
            float(original.duration_seconds) if original.duration_seconds is not None else None
        ),
    )


def _precheck(claim: Any, settings: Settings) -> tuple[str | None, _Source]:
    """SPEC 5.5 pre-checks after ``derivatives_disabled``, in order, read-only."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        original = original_for_meeting(
            db, workspace_id=claim.workspace_id, meeting_id=claim.meeting_id
        )
        recording = db.scalar(select(Recording).where(Recording.meeting_id == claim.meeting_id))
        inspection = (
            load_channel_inspection(db, recording=recording) if recording is not None else None
        )
        media_id = original.id if original is not None else None
        facts = _facts(original, inspection) if original is not None else None
        source = _Source(media_id=media_id, facts=facts, inspection=inspection)
        reason: str | None = None
        if claim.delete_audio_after_processing:
            reason = media.AUDIO_DELETED_AFTER_PROCESSING
        elif original is None:
            reason = media.SOURCE_NOT_REGISTERED
        else:
            refusal = _source_refusal(db, original, claim)
            if refusal in (media.SOURCE_NOT_LIVE, media.SOURCE_CHANGED):
                reason = refusal
            elif facts is None:
                reason = media.SOURCE_FACTS_UNKNOWN
            else:
                reason = refusal  # None or already_derived
        db.rollback()
    return reason, source


def _derive_and_register(
    claim: Any,
    media_path: Path,
    source: _Source,
    storage: ObjectStorage,
    settings: Settings,
    heartbeat: Any,
) -> str:
    assert source.facts is not None and source.media_id is not None
    max_bytes = _max_bytes(settings)
    with tempfile.TemporaryDirectory(prefix="derivative-", dir=media_path.parent) as work:
        outcome = derive_audio(
            media_path,
            source.facts,
            inspection=source.inspection,
            work_dir=Path(work),
            settings=settings,
            max_bytes=max_bytes,
        )
        if not isinstance(outcome, DerivedAudio):
            return _unavailable(claim, source.media_id, outcome.reason, settings)
        if heartbeat is not None and heartbeat.lost.is_set():
            return _unavailable(claim, source.media_id, media.STALE_ATTEMPT, settings)
        try:
            with outcome.path.open("rb") as stream:
                stored = storage.put_stream(
                    claim.workspace_id,
                    stream,
                    content_type=media.DERIVATIVE_CONTENT_TYPE,
                    expected_sha256=outcome.sha256,
                    max_bytes=max_bytes,
                )
        except ObjectTooLargeError:
            return _unavailable(claim, source.media_id, media.DERIVATIVE_TOO_LARGE, settings)
        except Exception as exc:  # noqa: BLE001 - a storage fault is a reason, never an error
            logger.warning(
                "MEDIA_DERIVATIVE_STORAGE_FAILED media_id=%s error_type=%s",
                source.media_id,
                type(exc).__name__,
            )
            return _unavailable(claim, source.media_id, media.STORAGE_FAILED, settings)
    return _register(claim, source.media_id, outcome, stored, storage, settings)


def _register(
    claim: Any,
    original_id: str,
    outcome: DerivedAudio,
    stored: Any,
    storage: ObjectStorage,
    settings: Settings,
) -> str:
    """The fenced registration (SPEC 5.5, design Q6)."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        try:
            job, now = lock_owned_or_cancelled(db, claim.lease)
        except StaleLeaseError:
            db.rollback()
            _remove(storage, claim.workspace_id, stored.key)
            return _logged(original_id, media.STALE_ATTEMPT)
        reason: str | None = None
        if job is None:
            reason = media.JOB_CANCELLED
        else:
            meeting = db.get(
                Meeting, claim.meeting_id, with_for_update=True, populate_existing=True
            )
            if meeting is None or meeting.deleted_at is not None:
                reason = media.MEETING_DELETED
        original = None
        if reason is None:
            original = media_by_id(
                db, workspace_id=claim.workspace_id, media_id=original_id, for_update=True
            )
            if original is None:
                reason = media.SOURCE_NOT_REGISTERED
            else:
                reason = _source_refusal(db, original, claim)
        if reason is None:
            assert original is not None
            try:
                derived = register_derived(
                    db,
                    source=original,
                    kind=MediaKind.NORMALIZED,
                    stored=stored,
                    storage_backend=stored.backend.value,
                    producer=media.DERIVATIVE_PRODUCER,
                    job_id=claim.job_id,
                    actor_user_id=None,
                    now=now,
                    settings=settings,
                    container_format=media.DERIVATIVE_CONTAINER,
                    duration_seconds=math.ceil(
                        outcome.sample_count / media.DERIVATIVE_SAMPLE_RATE_HZ
                    ),
                    channel_count=outcome.channel_count,
                    sample_rate_hz=media.DERIVATIVE_SAMPLE_RATE_HZ,
                )
            except MediaLifecycleError as exc:
                reason = (
                    exc.code
                    if exc.code in (media.SOURCE_NOT_LIVE, media.TENANT_MISMATCH)
                    else media.DERIVATIVE_FAILED
                )
            else:
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    _remove(storage, claim.workspace_id, stored.key)
                    return _unavailable(claim, original_id, media.STORAGE_CONFLICT, settings)
                logger.info(
                    "MEDIA_DERIVATIVE_REGISTERED media_id=%s derivative_id=%s",
                    original_id,
                    derived.id,
                )
                return REGISTERED
        # A known refusal: nothing was added; our object goes, the reason is kept.
        _remove(storage, claim.workspace_id, stored.key)
        if reason not in _NOT_AUDITED:
            _audit(db, claim, original_id, reason, settings)
        db.commit()
        return _logged(original_id, reason)


def _remove(storage: ObjectStorage, workspace_id: str, key: str) -> None:
    """Best effort; a leftover is reported by the orphan sweep (WP05-T04)."""
    try:
        storage.delete(workspace_id, key)
    except Exception as exc:  # noqa: BLE001 - cleanup never fails the job
        logger.warning("MEDIA_DERIVATIVE_CLEANUP_FAILED error_type=%s", type(exc).__name__)


def _audit(
    db: Session, claim: Any, original_id: str | None, reason: str, settings: Settings
) -> None:
    record_audit_event(
        db,
        event_type=AuditEventType.MEDIA_DERIVATIVE_UNAVAILABLE,
        actor_user_id=None,
        workspace_id=claim.workspace_id,
        target_type="media",
        target_id=original_id,
        details={
            "producer": media.DERIVATIVE_PRODUCER,
            "reason": reason,
            "job_id": claim.job_id,
        },
        settings=settings,
    )


def _logged(original_id: str | None, reason: str) -> str:
    logger.warning("MEDIA_DERIVATIVE_UNAVAILABLE media_id=%s reason=%s", original_id, reason)
    return reason


def _unavailable(claim: Any, original_id: str | None, reason: str, settings: Settings) -> str:
    """Keep the reason under the fence (audited unless SPEC says log only) and return it."""
    if reason not in _NOT_AUDITED:
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=claim.workspace_id)
            try:
                lock_owned_or_cancelled(db, claim.lease)
            except StaleLeaseError:
                # A stale attempt writes nothing; the reason is only logged.
                db.rollback()
                return _logged(original_id, reason)
            _audit(db, claim, original_id, reason, settings)
            db.commit()
    return _logged(original_id, reason)
