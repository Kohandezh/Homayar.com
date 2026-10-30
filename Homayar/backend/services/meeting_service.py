"""Tenant-scoped meeting persistence and durable job creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    ConversationDirection,
    ConversationType,
    IngestionSourceType,
    JobState,
    MediaObject,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    Recording,
    Workspace,
)
from backend.jobs.outbox import record_dispatch_intent
from backend.media.channels import ChannelInspection, channel_inspection_record
from backend.services.entitlement_service import reserve_usage
from backend.services.media_lifecycle_service import register_original
from backend.services.retention_policy import retention_expiry_for


class UploadIdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class CreatedUpload:
    meeting: Meeting
    recording: Recording
    job: ProcessingJob
    media: MediaObject


def conversation_type_for_source(
    source_type: IngestionSourceType | str | None,
) -> ConversationType:
    """The conversation type an ingestion source type implies (HOM-58).

    A voicemail is a voicemail and a meeting bot records a meeting; every other
    source, and no source, is a call.
    """
    if source_type is None:
        return ConversationType.CALL
    source = IngestionSourceType(source_type)
    if source is IngestionSourceType.VOICEMAIL:
        return ConversationType.VOICEMAIL
    if source is IngestionSourceType.MEETING_BOT:
        return ConversationType.MEETING
    return ConversationType.CALL


def find_job_by_idempotency(
    db: Session,
    *,
    workspace_id: str,
    idempotency_key: str,
) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob)
        .join(Meeting, Meeting.id == ProcessingJob.meeting_id)
        .where(
            ProcessingJob.workspace_id == workspace_id,
            ProcessingJob.idempotency_key == idempotency_key,
            Meeting.deleted_at.is_(None),
        )
    )


def create_upload_job(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    title: str,
    language: str,
    storage_key: str,
    original_filename: str,
    media_type: str,
    container_format: str,
    size_bytes: int,
    checksum_sha256: str,
    duration_seconds: int,
    channel_count: int | None,
    sample_rate_hz: int | None,
    idempotency_key: str,
    retention_days: int,
    settings: Settings | None = None,
    ingestion_id: str | None = None,
    producer: str = "upload",
    channel_inspection: ChannelInspection | None = None,
    source_type: str | None = None,
    conversation_type: ConversationType | str | None = None,
    direction: ConversationDirection | str | None = None,
    started_at: datetime | None = None,
    external_source_system: str | None = None,
    external_conversation_id: str | None = None,
) -> CreatedUpload:
    if duration_seconds <= 0 or size_bytes <= 0:
        raise ValueError("Recording duration and size must be positive")
    # The conversation fields (HOM-58).  Without an explicit type, the
    # ingestion source type decides; the source reference is both or neither.
    kind = (
        ConversationType(conversation_type)
        if conversation_type is not None
        else conversation_type_for_source(source_type)
    )
    try:
        started_by = ConversationDirection(direction) if direction is not None else None
    except ValueError as exc:
        raise ValueError(f"Unknown conversation direction: {direction!r}") from exc
    if (external_source_system is None) != (external_conversation_id is None):
        raise ValueError(
            "external_source_system and external_conversation_id are set together or not at all"
        )
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise ValueError("Unknown workspace")
    if find_job_by_idempotency(
        db,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    ):
        raise UploadIdempotencyConflict("Upload idempotency key already exists")

    meeting = Meeting(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title=title.strip(),
        language=language,
        status=MeetingStatus.QUEUED,
        duration_seconds=duration_seconds,
        source="upload",
        # Server-side policy only (WP07-T01): the caller's ``retention_days``
        # is the workspace's own value; the mode decides whether an expiry exists.
        retention_expires_at=retention_expiry_for(workspace, now=datetime.now(UTC)),
        conversation_type=kind.value,
        direction=started_by.value if started_by is not None else None,
        started_at=started_at,
        external_source_system=external_source_system,
        external_conversation_id=external_conversation_id,
    )
    db.add(meeting)
    db.flush()

    recording = Recording(
        workspace_id=workspace_id,
        meeting_id=meeting.id,
        storage_key=storage_key,
        original_filename=original_filename,
        media_type=media_type,
        container_format=container_format,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        duration_seconds=duration_seconds,
        channel_count=channel_count,
        sample_rate_hz=sample_rate_hz,
    )
    db.add(recording)

    audio_reservation = reserve_usage(
        db,
        workspace_id=workspace_id,
        meeting_id=meeting.id,
        amount=duration_seconds,
        idempotency_key=f"{idempotency_key}:audio",
        metric="audio_seconds",
    )
    concurrency_reservation = reserve_usage(
        db,
        workspace_id=workspace_id,
        meeting_id=meeting.id,
        amount=1,
        idempotency_key=f"{idempotency_key}:concurrency",
        metric="concurrent_jobs",
    )
    active_settings = settings or get_settings()
    # Channel roles (HOM-37) are attributed here, once, with the mapping the
    # workspace holds for this source in this transaction; the record keeps
    # that mapping's version, so a later mapping change never rewrites it.
    channel_record = None
    if channel_inspection is not None:
        if source_type is None:
            raise ValueError("A channel inspection needs its ingestion source type")
        channel_record = channel_inspection_record(
            channel_inspection,
            source_type=source_type,
            source_provider=producer,
            workspace_settings=workspace.settings_json,
        )
    job = ProcessingJob(
        workspace_id=workspace_id,
        meeting_id=meeting.id,
        reservation_id=audio_reservation.id,
        concurrency_reservation_id=concurrency_reservation.id,
        idempotency_key=idempotency_key,
        state=JobState.QUEUED,
        stage="queued",
        # Retry budget and overall deadline (WP06-T05).
        max_attempts=active_settings.job_max_attempts,
        deadline_at=datetime.now(UTC) + timedelta(seconds=active_settings.job_deadline_seconds),
    )
    db.add(job)
    db.flush()
    # Outbox row in the same transaction: the job cannot exist without its
    # publish intent, and nothing is published before this commits.
    record_dispatch_intent(db, job, settings=active_settings)
    # The ORIGINAL media object of the conversation (WP05-T03): the stored,
    # digest-verified bytes with their provenance and lifecycle.
    media = register_original(
        db,
        workspace=workspace,
        meeting=meeting,
        recording=recording,
        storage_backend=active_settings.storage_backend,
        producer=producer,
        ingestion_id=ingestion_id,
        job_id=job.id,
        actor_user_id=user_id,
        now=datetime.now(UTC),
        settings=active_settings,
        channel_inspection=channel_record,
    )
    return CreatedUpload(meeting=meeting, recording=recording, job=job, media=media)


def latest_job_for_meeting(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.workspace_id == workspace_id,
            ProcessingJob.meeting_id == meeting_id,
        )
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )
