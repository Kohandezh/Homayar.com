"""Idempotent voicemail import through the canonical ingestion path.

No second ingestion system: the staged audio goes through the same steps as
``POST /uploads`` - ``media.validate_media_upload``, object storage,
``ingestion_service.accept_ingestion`` (ledger row, duplicate-media policy,
audit) and ``meeting_service.create_upload_job`` (meeting, recording, usage
reservation, processing job and dispatch outbox).  The only addition is the
``voicemail_messages`` row written in the same transaction.

Identity: ``(workspace, VOICEMAIL_SOURCE_TYPE, "vm:" + external_message_id)``
in the ingestion ledger, and ``uq_voicemail_external_message`` on the side
table.  A repeated external id returns the existing voicemail (``replayed``)
when the payload matches, and ``409 ingestion_conflict`` when it does not.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import IngestionRecord, ProcessingJob
from backend.jobs.outbox import dispatch_job
from backend.media import MediaValidationPolicy, validate_media_upload
from backend.security.context import WorkspaceContext
from backend.security.idempotency import scoped_idempotency_key
from backend.security.normalization import normalize_iranian_mobile
from backend.services.ingestion_service import (
    IngestionDecision,
    IngestionError,
    IngestionIdentity,
    IngestionPayload,
    accept_ingestion,
    find_ingestion,
    payload_digest,
    resolve_existing,
)
from backend.services.logging_service import logger
from backend.services.meeting_service import UploadIdempotencyConflict, create_upload_job
from backend.services.upload_service import StagedUpload
from backend.speech.text_normalize import normalize_digits
from backend.storage import create_object_storage
from backend.voicemail.models import (
    VOICEMAIL_MEETING_SOURCE,
    VOICEMAIL_SOURCE_PROVIDER,
    VOICEMAIL_SOURCE_TYPE,
    VoicemailMessage,
    VoicemailStatus,
)

EVENT_PREFIX = "vm:"
# The ledger's external_event_id alphabet, minus the prefix length.
EXTERNAL_ID_PATTERN = r"^[A-Za-z0-9._:@/+=-]{1,197}$"


class VoicemailImportError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 409, details=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class VoicemailEnvelope:
    external_message_id: str
    received_at: datetime | None = None
    caller_number: str | None = None
    mailbox: str | None = None
    extension: str | None = None
    language: str = "fa"
    title: str | None = None


@dataclass(frozen=True)
class VoicemailImportResult:
    voicemail: VoicemailMessage
    job: ProcessingJob | None
    ingestion: IngestionRecord
    replayed: bool


def normalize_caller_number(value: str | None) -> tuple[str | None, bool]:
    """``(+989xxxxxxxxx, True)`` for an Iranian mobile; else digits-only, ``False``."""
    if value is None or not value.strip():
        return None, False
    try:
        return normalize_iranian_mobile(value), True
    except ValueError:
        # Landlines, international and extensions: keep a comparable,
        # digits-only form (leading "+" kept) so repeat-caller detection works.
        raw = normalize_digits(value.strip())
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None, False
        return (("+" if raw.startswith("+") else "") + digits)[:32], False


def stage_bytes(
    data: bytes, *, filename: str, content_type: str, settings: Settings
) -> StagedUpload:
    """Stage in-memory audio (connector / programmatic import) like an upload."""
    if not data:
        raise VoicemailImportError("empty_upload", "The voicemail audio is empty", status_code=422)
    limit = settings.max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise VoicemailImportError(
            "upload_too_large", f"Upload exceeds the {limit}-byte limit", status_code=413
        )
    quarantine = Path(settings.upload_quarantine_dir)
    quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix="voicemail-", dir=quarantine)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.chmod(raw_path, 0o600)
    return StagedUpload(
        path=Path(raw_path),
        filename=filename,
        claimed_content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def voicemail_for_external_id(
    db: Session, *, workspace_id: str, external_message_id: str
) -> VoicemailMessage | None:
    return db.scalar(
        select(VoicemailMessage).where(
            VoicemailMessage.workspace_id == workspace_id,
            VoicemailMessage.external_message_id == external_message_id,
        )
    )


def _replay(
    db: Session,
    *,
    record: IngestionRecord,
    payload_hash: str,
    context: WorkspaceContext,
    request_id: str | None,
    settings: Settings,
) -> VoicemailImportResult:
    outcome = resolve_existing(
        db,
        record=record,
        payload_hash=payload_hash,
        actor_user_id=context.actor.user.id,
        request_id=request_id,
        now=datetime.now(UTC),
        settings=settings,
    )
    db.commit()
    if outcome.decision is IngestionDecision.CONFLICT:
        raise VoicemailImportError(
            "ingestion_conflict",
            "This voicemail id was already imported with a different payload",
            details={"ingestion_id": record.id, "conflict_count": outcome.record.conflict_count},
        )
    if outcome.decision is IngestionDecision.REJECTED_REPLAY:
        raise VoicemailImportError(
            record.reason or "media_rejected",
            "This voicemail was rejected before with the same payload",
            status_code=422,
            details={"ingestion_id": record.id, "replayed": True},
        )
    voicemail = (
        db.scalar(select(VoicemailMessage).where(VoicemailMessage.meeting_id == record.meeting_id))
        if record.meeting_id
        else None
    )
    if outcome.decision is IngestionDecision.TARGET_DELETED or voicemail is None:
        raise VoicemailImportError(
            "ingestion_target_deleted",
            "This voicemail was imported before, but its conversation was deleted",
            status_code=410,
            details={"ingestion_id": record.id},
        )
    job = db.get(ProcessingJob, record.job_id) if record.job_id else None
    return VoicemailImportResult(voicemail, job, record, replayed=True)


def import_voicemail(
    db: Session,
    *,
    context: WorkspaceContext,
    staged: StagedUpload,
    envelope: VoicemailEnvelope,
    settings: Settings,
    request_id: str | None = None,
    dispatch: bool = True,
) -> VoicemailImportResult:
    """Import one voicemail; commits.  The caller deletes ``staged`` afterwards.

    Raises :class:`VoicemailImportError` for ledger decisions and lets media
    (``MediaValidationError``), quota (``QuotaExceededError``) and storage
    (``StorageError``) errors propagate after rolling back, exactly like the
    upload route.
    """
    workspace_id = context.workspace.id
    received_at = envelope.received_at or datetime.now(UTC)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    identity = IngestionIdentity(
        source_type=VOICEMAIL_SOURCE_TYPE,
        source_provider=VOICEMAIL_SOURCE_PROVIDER,
        external_event_id=EVENT_PREFIX + envelope.external_message_id,
    )
    caller_number, caller_valid = normalize_caller_number(envelope.caller_number)
    language = envelope.language.lower()
    payload = IngestionPayload(
        media_sha256=staged.sha256,
        media_size_bytes=staged.size_bytes,
        original_filename=staged.filename.strip()[:300] or None,
        title=(envelope.title or "").strip()[:240] or None,
        language=language,
        declared_content_type=staged.claimed_content_type,
        extra={
            "caller_number": caller_number or "",
            "extension": envelope.extension or "",
            # Only a caller-declared time is part of the payload identity; the
            # server clock would make every retry look like a conflict.
            "received_at": (
                received_at.astimezone(UTC).isoformat() if envelope.received_at else ""
            ),
        },
    )
    payload_hash = payload_digest(identity, payload)

    existing = find_ingestion(db, workspace_id=workspace_id, identity=identity)
    if existing is not None:
        return _replay(
            db,
            record=existing,
            payload_hash=payload_hash,
            context=context,
            request_id=request_id,
            settings=settings,
        )

    metadata = validate_media_upload(
        staged.path,
        original_filename=staged.filename,
        declared_mime_type=staged.claimed_content_type,
        policy=MediaValidationPolicy.from_settings(settings),
        ffprobe_path=settings.ffprobe_path,
        timeout_seconds=settings.ffprobe_timeout_seconds,
        ffmpeg_path=settings.ffmpeg_path,
        ffmpeg_timeout_seconds=settings.ffmpeg_timeout_seconds,
    )
    storage = create_object_storage(settings)
    with staged.path.open("rb") as source:
        stored = storage.put_stream(
            workspace_id,
            source,
            content_type=metadata.mime_type,
            original_filename=metadata.original_filename,
            expected_sha256=staged.sha256,
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
        )
    persisted = False
    title = (envelope.title or "").strip()[:240] or (
        f"Voicemail {caller_number or 'unknown caller'}"
        + (f" ({envelope.mailbox})" if envelope.mailbox else "")
    )[:240]
    scoped_key = scoped_idempotency_key(
        workspace_id=workspace_id,
        purpose="voicemail-import",
        # The job key alphabet/length differs from the ledger id: digest it.
        client_key="vm-" + hashlib.sha256(envelope.external_message_id.encode()).hexdigest(),
        settings=settings,
    )
    duration = math.ceil(metadata.duration_seconds)

    def _create(record: IngestionRecord):
        created = create_upload_job(
            db,
            workspace_id=workspace_id,
            user_id=context.actor.user.id,
            title=title,
            language=language,
            storage_key=stored.key,
            original_filename=metadata.original_filename,
            media_type=metadata.mime_type,
            container_format=metadata.container,
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.sha256,
            duration_seconds=duration,
            channel_count=metadata.channels,
            sample_rate_hz=metadata.sample_rate_hz,
            idempotency_key=scoped_key,
            retention_days=context.workspace.retention_days,
            settings=settings,
            ingestion_id=record.id,
            producer=VOICEMAIL_SOURCE_PROVIDER,
        )
        created.meeting.source = VOICEMAIL_MEETING_SOURCE
        db.add(
            VoicemailMessage(
                workspace_id=workspace_id,
                meeting_id=created.meeting.id,
                ingestion_id=record.id,
                external_message_id=envelope.external_message_id,
                mailbox=envelope.mailbox,
                extension=envelope.extension,
                caller_number=caller_number,
                caller_number_valid=caller_valid,
                received_at=received_at,
                duration_seconds=duration,
                status=VoicemailStatus.RECEIVED.value,
            )
        )
        return created

    try:
        try:
            outcome = accept_ingestion(
                db,
                workspace=context.workspace,
                identity=identity,
                payload=payload,
                received_at=received_at,
                now=datetime.now(UTC),
                actor_user_id=context.actor.user.id,
                api_key_id=context.actor.api_key.id if context.actor.api_key else None,
                request_id=request_id,
                settings=settings,
                create=_create,
            )
            db.flush()
            job_id = outcome.created.job.id
            record = outcome.record
            voicemail = voicemail_for_external_id(
                db, workspace_id=workspace_id, external_message_id=envelope.external_message_id
            )
            if voicemail is None:  # pragma: no cover - written by _create above
                raise RuntimeError("Voicemail row missing after acceptance")
            db.commit()
            persisted = True
        except IngestionError as exc:
            db.commit()  # the audited policy refusal
            raise VoicemailImportError(
                exc.code,
                str(exc),
                status_code=exc.status_code,
                details={"ingestion_id": exc.ingestion.id} if exc.ingestion else {},
            ) from exc
        except (IntegrityError, UploadIdempotencyConflict):
            # A concurrent import claimed the identity first.
            db.rollback()
            existing = find_ingestion(db, workspace_id=workspace_id, identity=identity)
            if existing is None:
                raise
            return _replay(
                db,
                record=existing,
                payload_hash=payload_hash,
                context=context,
                request_id=request_id,
                settings=settings,
            )
        except Exception:
            db.rollback()
            raise
    finally:
        if not persisted:
            try:
                storage.delete(workspace_id, stored.key)
            except Exception as exc:  # noqa: BLE001 - the orphan sweep reconciles storage
                logger.error(
                    "ORPHAN_VOICEMAIL_DELETE_FAILED workspace_id=%s error_type=%s",
                    workspace_id,
                    type(exc).__name__,
                )

    if dispatch:
        # Outbox row committed with the job; the sweeper republishes on failure.
        dispatch_job(job_id, settings)
    return VoicemailImportResult(voicemail, db.get(ProcessingJob, job_id), record, replayed=False)
