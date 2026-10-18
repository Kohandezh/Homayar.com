from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import (
    IngestionRecord,
    IngestionSourceType,
    MediaLifecycleState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    Recording,
)
from backend.jobs.failures import MEETING_DELETED, cancel_job
from backend.jobs.outbox import cancel_dispatch, dispatch_job
from backend.media import MediaValidationError, MediaValidationPolicy, validate_media_upload
from backend.schemas.ingestions import IngestionResponse
from backend.schemas.meetings import (
    JobResponse,
    LegalHoldReleaseRequest,
    LegalHoldRequest,
    MediaObjectListResponse,
    MediaObjectResponse,
    MeetingCountsResponse,
    MeetingDetailResponse,
    MeetingListResponse,
    MeetingQualityResponse,
    MeetingResponse,
    MeetingUpdateRequest,
    RecordingResponse,
    UploadAcceptedResponse,
    UsageResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_path_permission,
    require_permission,
    require_write_permission,
)
from backend.security.idempotency import scoped_idempotency_key, validate_idempotency_key
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.conversation_purge_service import (
    REASON_MEETING_DELETED,
    nudge_purge,
    run_purge_attempt,
    tombstone_meeting,
)
from backend.services.download_service import deliver_recording
from backend.services.entitlement_service import (
    QuotaExceededError,
    get_usage_snapshot,
)
from backend.services.ingestion_service import (
    PROVIDER_API,
    PROVIDER_CONSOLE,
    IngestionDecision,
    IngestionError,
    IngestionIdentity,
    IngestionPayload,
    accept_ingestion,
    audit_rejection,
    find_ingestion,
    ingestion_by_job,
    ingestion_for_meeting,
    payload_digest,
    record_rejection,
    resolve_existing,
)
from backend.services.logging_service import logger
from backend.services.media_lifecycle_service import (
    MediaLifecycleError,
    delete_objects,
    media_by_id,
    media_for_meeting,
    place_legal_hold,
    release_legal_hold,
    verify_integrity,
)
from backend.services.meeting_service import (
    UploadIdempotencyConflict,
    create_upload_job,
    find_job_by_idempotency,
    latest_job_for_meeting,
)
from backend.services.quality_service import assess_meeting
from backend.services.rate_limit_service import RateLimiter, RateLimitExceeded, RateLimitUnavailable
from backend.services.team_service import validate_agent_attribution, validate_team_attribution
from backend.services.tombstone_journal import (
    JOURNAL_INLINE_LIMIT,
    journal_for,
    journal_pending,
)
from backend.services.upload_service import UploadRejectedError, stage_upload
from backend.storage import StorageError, create_object_storage

router = APIRouter(tags=["meetings"])


def _get_meeting(
    db: Session,
    *,
    context: WorkspaceContext,
    meeting_id: str,
) -> Meeting:
    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.recording))
        .where(
            Meeting.id == meeting_id,
            Meeting.workspace_id == context.workspace.id,
            Meeting.deleted_at.is_(None),
            context.record_access.meeting_clause(),
        )
    )
    if meeting is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "meeting_not_found", "message": "Meeting not found"},
        )
    return meeting


def _meeting_response(meeting: Meeting) -> MeetingResponse:
    return MeetingResponse.model_validate(meeting)


def _job_response(job) -> JobResponse | None:
    return JobResponse.model_validate(job) if job is not None else None


def _usage_response(snapshot) -> UsageResponse:
    return UsageResponse(
        metric=snapshot.metric,
        limit=snapshot.limit,
        used=snapshot.used,
        reserved=snapshot.reserved,
        remaining=snapshot.remaining,
    )


def _upload_response(
    db: Session,
    *,
    context: WorkspaceContext,
    job,
    idempotent_replay: bool,
    ingestion: IngestionRecord | None,
) -> UploadAcceptedResponse:
    workspace_id = context.workspace.id
    meeting = _get_meeting(
        db,
        context=context,
        meeting_id=job.meeting_id,
    )
    if meeting.recording is None:
        raise RuntimeError("Upload job is missing its recording")
    usage = get_usage_snapshot(db, workspace_id, "audio_seconds")
    return UploadAcceptedResponse(
        meeting=_meeting_response(meeting),
        recording=RecordingResponse.model_validate(meeting.recording),
        job=JobResponse.model_validate(job),
        usage=_usage_response(usage),
        idempotent_replay=idempotent_replay,
        ingestion=IngestionResponse.model_validate(ingestion) if ingestion is not None else None,
    )


def _require_verified_identity(context: WorkspaceContext) -> None:
    user = context.actor.user
    if user.email_verified_at is None and user.phone_verified_at is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "verified_identity_required",
                "message": "Verify an email address or phone number before uploading",
            },
        )


def _enforce_upload_rate_limit(settings: Settings, workspace_id: str) -> None:
    try:
        RateLimiter(settings).hit(
            f"upload:workspace:{workspace_id}",
            limit=settings.upload_rate_limit_per_hour,
            window_seconds=3600,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "upload_rate_limited", "message": "Too many uploads"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "rate_limiter_unavailable",
                "message": "Upload service is temporarily unavailable",
            },
        ) from exc


_OVERSIZE_CODES = frozenset({"upload_too_large", "media_file_too_large", "media_duration_exceeded"})
# Hint for clients retrying a transient (non-terminal) refusal: the inspection
# tooling or the object store, not the bytes, failed; the identity stays free.
_TRANSIENT_RETRY_AFTER_SECONDS = 30


def _refusal_status(code: str, *, terminal: bool = True) -> int:
    """HTTP status of a media refusal: 413 for size/duration bounds, 503 when the
    inspection tooling (not the bytes) failed, 422 otherwise."""
    if not terminal:
        return 503
    return 413 if code in _OVERSIZE_CODES else 422


def _upload_failure(error: Exception) -> HTTPException:
    if isinstance(error, UploadRejectedError):
        return HTTPException(
            status_code=_refusal_status(error.code),
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(error, MediaValidationError):
        return HTTPException(
            status_code=_refusal_status(error.code, terminal=error.terminal),
            detail={"code": error.code, "message": str(error)},
            headers=None
            if error.terminal
            else {"Retry-After": str(_TRANSIENT_RETRY_AFTER_SECONDS)},
        )
    if isinstance(error, QuotaExceededError):
        code = (
            "concurrent_job_limit_reached"
            if error.metric == "concurrent_jobs"
            else "audio_quota_exceeded"
        )
        return HTTPException(
            status_code=429,
            detail={
                "code": code,
                "message": "The workspace processing allowance is exhausted",
                "details": {
                    "metric": error.metric,
                    "limit": error.limit,
                    "used": error.used,
                    "reserved": error.reserved,
                    "requested": error.requested,
                    "remaining": error.remaining,
                },
            },
        )
    if isinstance(error, StorageError):
        return HTTPException(
            status_code=503,
            detail={
                "code": "storage_unavailable",
                "message": "Recording storage is temporarily unavailable",
            },
            headers={"Retry-After": str(_TRANSIENT_RETRY_AFTER_SECONDS)},
        )
    return HTTPException(
        status_code=500,
        detail={"code": "upload_failed", "message": "Upload could not be completed"},
    )


def _ingestion_refusal(error: IngestionError) -> HTTPException:
    details: dict[str, str | int] = {}
    if error.ingestion is not None:
        details["ingestion_id"] = error.ingestion.id
        if error.ingestion.meeting_id:
            details["meeting_id"] = error.ingestion.meeting_id
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": str(error), "details": details},
    )


def _settle_existing(
    db: Session,
    *,
    context: WorkspaceContext,
    record: IngestionRecord,
    payload_hash: str,
    request_id: str | None,
    settings: Settings,
) -> UploadAcceptedResponse:
    """Answer a request whose ingestion identity already exists (cases A / B).

    The decision is recorded (audit row, conflict counter) and committed
    before the response, whatever it is.
    """
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
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ingestion_conflict",
                "message": "This event id was already ingested with a different payload",
                "details": {
                    "ingestion_id": outcome.record.id,
                    "conflict_count": outcome.record.conflict_count,
                },
            },
        )
    if outcome.decision is IngestionDecision.REJECTED_REPLAY:
        reason = outcome.record.reason or "media_rejected"
        raise HTTPException(
            status_code=_refusal_status(reason),
            detail={
                "code": reason,
                "message": "This event was rejected before with the same payload",
                "details": {
                    "ingestion_id": outcome.record.id,
                    "reason": reason,
                    "replayed": True,
                },
            },
        )
    job = db.get(ProcessingJob, record.job_id) if record.job_id else None
    if outcome.decision is IngestionDecision.TARGET_DELETED or job is None:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "ingestion_target_deleted",
                "message": "This event was ingested before, but its conversation was deleted",
                "details": {"ingestion_id": outcome.record.id},
            },
        )
    return _upload_response(
        db, context=context, job=job, idempotent_replay=True, ingestion=outcome.record
    )


def _record_refusal(
    db: Session,
    error: Exception,
    *,
    context: WorkspaceContext,
    identity: IngestionIdentity,
    payload: IngestionPayload | None,
    received_at: datetime,
    request_id: str | None,
    settings: Settings,
) -> HTTPException | None:
    """Persist the decision behind a refused upload (WP05-T02) and build the answer.

    A terminal media refusal of a staged payload becomes a ``rejected`` ledger
    row (same identity as an acceptance would have had); everything else —
    transient inspection failures, storage errors, refusals before the payload
    exists — is audited only.  Returns ``None`` when a concurrent request
    claimed the identity first: the caller answers from that row.
    """
    workspace_id = context.workspace.id
    actor_id = context.actor.user.id
    reason = getattr(error, "code", None) or "storage_error"
    terminal = not isinstance(error, (MediaValidationError, StorageError)) or (
        isinstance(error, MediaValidationError) and error.terminal
    )
    if isinstance(error, MediaValidationError) and error.terminal and payload is not None:
        try:
            record = record_rejection(
                db,
                workspace=context.workspace,
                identity=identity,
                payload=payload,
                reason=error.code,
                received_at=received_at,
                now=datetime.now(UTC),
                actor_user_id=actor_id,
                api_key_id=context.actor.api_key.id if context.actor.api_key else None,
                request_id=request_id,
                settings=settings,
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        failure = _upload_failure(error)
        failure.detail["details"] = {"ingestion_id": record.id, "reason": error.code}
        return failure
    audit_rejection(
        db,
        workspace_id=workspace_id,
        identity=identity,
        reason=reason,
        terminal=terminal,
        actor_user_id=actor_id,
        request_id=request_id,
        settings=settings,
        media_size_bytes=payload.media_size_bytes if payload is not None else None,
    )
    db.commit()
    return _upload_failure(error)


@router.post("/uploads", response_model=UploadAcceptedResponse, status_code=202)
@router.post(
    "/uploads/",
    response_model=UploadAcceptedResponse,
    status_code=202,
    include_in_schema=False,
)
async def upload_meeting(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None, max_length=240),
    language: str = Form(default="fa", min_length=2, max_length=12, pattern=r"^[A-Za-z-]+$"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_CREATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UploadAcceptedResponse:
    _require_verified_identity(context)
    workspace_id = context.workspace.id
    request_id = getattr(request.state, "request_id", None)
    received_at = datetime.now(UTC)
    try:
        external_event_id = validate_idempotency_key(idempotency_key)
        scoped_key = scoped_idempotency_key(
            workspace_id=workspace_id,
            purpose="meeting-upload",
            client_key=idempotency_key,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_idempotency_key", "message": str(exc)},
        ) from exc
    # The client's Idempotency-Key is the external event id of a file upload
    # (WP05-T01); the peppered digest stays the job's key (WP06 invariant).
    identity = IngestionIdentity(
        source_type=IngestionSourceType.FILE_UPLOAD,
        source_provider=PROVIDER_API if context.actor.api_key is not None else PROVIDER_CONSOLE,
        external_event_id=external_event_id,
    )

    # A known event id is a replay or a conflict, decided once the payload
    # digest is known; only a new event counts against the upload allowance.
    known = find_ingestion(db, workspace_id=workspace_id, identity=identity) is not None
    legacy_job = None
    if not known:
        # Jobs accepted before the ledger existed (WP05-T01 backfill) keep
        # replaying through their peppered key.
        legacy_job = find_job_by_idempotency(
            db, workspace_id=workspace_id, idempotency_key=scoped_key
        )
    if not known and legacy_job is None:
        _enforce_upload_rate_limit(settings, workspace_id)

    staged = None
    stored = None
    persisted = False
    payload: IngestionPayload | None = None
    payload_hash: str | None = None
    storage = create_object_storage(settings)
    try:
        staged = await stage_upload(
            file,
            quarantine_dir=Path(settings.upload_quarantine_dir),
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
        )
        payload = IngestionPayload(
            media_sha256=staged.sha256,
            media_size_bytes=staged.size_bytes,
            original_filename=staged.filename.strip()[:300] or None,
            title=(title or "").strip()[:240] or None,
            language=language.lower(),
            declared_content_type=staged.claimed_content_type,
        )
        payload_hash = payload_digest(identity, payload)

        existing = find_ingestion(db, workspace_id=workspace_id, identity=identity)
        if existing is None and legacy_job is not None:
            existing = ingestion_by_job(db, workspace_id=workspace_id, job_id=legacy_job.id)
            if existing is None:
                return _upload_response(
                    db, context=context, job=legacy_job, idempotent_replay=True, ingestion=None
                )
        if existing is not None:
            return _settle_existing(
                db,
                context=context,
                record=existing,
                payload_hash=payload_hash,
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
        with staged.path.open("rb") as source:
            stored = storage.put_stream(
                workspace_id,
                source,
                content_type=metadata.mime_type,
                original_filename=metadata.original_filename,
                expected_sha256=staged.sha256,
                max_bytes=settings.max_upload_size_mb * 1024 * 1024,
            )

        meeting_title = (title or Path(metadata.original_filename).stem).strip()[:240]
        if not meeting_title:
            meeting_title = "Untitled meeting"

        def _create(_record: IngestionRecord):
            return create_upload_job(
                db,
                workspace_id=workspace_id,
                user_id=context.actor.user.id,
                title=meeting_title,
                language=language.lower(),
                storage_key=stored.key,
                original_filename=metadata.original_filename,
                media_type=metadata.mime_type,
                container_format=metadata.container,
                size_bytes=stored.size_bytes,
                checksum_sha256=stored.sha256,
                duration_seconds=math.ceil(metadata.duration_seconds),
                channel_count=metadata.channels,
                sample_rate_hz=metadata.sample_rate_hz,
                idempotency_key=scoped_key,
                retention_days=context.workspace.retention_days,
                settings=settings,
                ingestion_id=_record.id,
                producer=identity.source_provider,
            )

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
            created = outcome.created
            usage = get_usage_snapshot(db, workspace_id, "audio_seconds")
            record_audit_event(
                db,
                event_type=AuditEventType.MEETING_UPLOADED,
                actor_user_id=context.actor.user.id,
                workspace_id=workspace_id,
                target_type="meeting",
                target_id=created.meeting.id,
                request_id=request_id,
                details={
                    "size_bytes": stored.size_bytes,
                    "duration_seconds": created.meeting.duration_seconds,
                    "container": metadata.container,
                    "ingestion_id": outcome.record.id,
                },
                settings=settings,
            )
            response = UploadAcceptedResponse(
                meeting=_meeting_response(created.meeting),
                recording=RecordingResponse.model_validate(created.recording),
                job=JobResponse.model_validate(created.job),
                usage=_usage_response(usage),
                idempotent_replay=False,
                ingestion=IngestionResponse.model_validate(outcome.record),
            )
            db.commit()
            persisted = True
        except IngestionError as exc:
            # A policy refusal (duplicate media): nothing was created, the
            # audit row of the decision is committed.
            db.commit()
            raise _ingestion_refusal(exc) from exc
        except (QuotaExceededError, StorageError) as exc:
            db.rollback()
            raise _upload_failure(exc) from exc
        except (IntegrityError, UploadIdempotencyConflict):
            # A concurrent request claimed the same identity first (the
            # unique index decided); answer from its committed row.
            db.rollback()
            existing = find_ingestion(db, workspace_id=workspace_id, identity=identity)
            if existing is None:
                legacy_job = find_job_by_idempotency(
                    db, workspace_id=workspace_id, idempotency_key=scoped_key
                )
                if legacy_job is None:
                    raise
                existing = ingestion_by_job(db, workspace_id=workspace_id, job_id=legacy_job.id)
                if existing is None:
                    return _upload_response(
                        db, context=context, job=legacy_job, idempotent_replay=True, ingestion=None
                    )
            return _settle_existing(
                db,
                context=context,
                record=existing,
                payload_hash=payload_hash,
                request_id=request_id,
                settings=settings,
            )
    except HTTPException:
        raise
    except (UploadRejectedError, MediaValidationError, StorageError) as exc:
        db.rollback()
        refusal = _record_refusal(
            db,
            exc,
            context=context,
            identity=identity,
            payload=payload,
            received_at=received_at,
            request_id=request_id,
            settings=settings,
        )
        if refusal is None and payload_hash is not None:
            existing = find_ingestion(db, workspace_id=workspace_id, identity=identity)
            if existing is not None:
                return _settle_existing(
                    db,
                    context=context,
                    record=existing,
                    payload_hash=payload_hash,
                    request_id=request_id,
                    settings=settings,
                )
        raise (refusal or _upload_failure(exc)) from exc
    finally:
        if staged is not None:
            staged.delete()
        if stored is not None and not persisted:
            try:
                storage.delete(workspace_id, stored.key)
            except Exception as exc:
                logger.error(
                    "ORPHAN_UPLOAD_DELETE_FAILED workspace_id=%s error_type=%s",
                    workspace_id,
                    type(exc).__name__,
                )

    # The outbox row committed with the job; this is only the request-time
    # publish attempt. If it fails or the process dies here, the sweeper
    # republishes from the committed row (at-least-once).
    dispatch_job(response.job.id, settings)
    return response


@router.get("/meetings", response_model=MeetingListResponse)
def list_meetings(
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = Query(default=None),
    status: MeetingStatus | None = Query(default=None),
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> MeetingListResponse:
    query = select(Meeting).where(
        Meeting.workspace_id == context.workspace.id,
        Meeting.deleted_at.is_(None),
        context.record_access.meeting_clause(),
    )
    if before is not None:
        query = query.where(Meeting.created_at < before)
    if status is not None:
        query = query.where(Meeting.status == status)
    meetings = list(
        db.scalars(query.order_by(Meeting.created_at.desc(), Meeting.id.desc()).limit(limit))
    )
    next_before = meetings[-1].created_at if len(meetings) == limit else None
    return MeetingListResponse(
        items=[_meeting_response(meeting) for meeting in meetings],
        next_before=next_before,
    )


# Declared before /meetings/{meeting_id}, so "counts" is never read as a meeting id.
@router.get("/meetings/counts", response_model=MeetingCountsResponse)
def count_meetings(
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> MeetingCountsResponse:
    rows = db.execute(
        select(Meeting.status, func.count())
        .where(
            Meeting.workspace_id == context.workspace.id,
            Meeting.deleted_at.is_(None),
            context.record_access.meeting_clause(),
        )
        .group_by(Meeting.status)
    ).all()
    counts = {status.value: 0 for status in MeetingStatus}
    for status, count in rows:
        counts[status.value] = int(count)
    return MeetingCountsResponse(counts=counts, total=sum(counts.values()))


@router.get("/meetings/{meeting_id}", response_model=MeetingDetailResponse)
def get_meeting(
    meeting_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> MeetingDetailResponse:
    meeting = _get_meeting(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    job = latest_job_for_meeting(
        db,
        workspace_id=context.workspace.id,
        meeting_id=meeting.id,
    )
    ingestion = ingestion_for_meeting(db, workspace_id=context.workspace.id, meeting_id=meeting.id)
    return MeetingDetailResponse(
        **_meeting_response(meeting).model_dump(),
        recording=(
            RecordingResponse.model_validate(meeting.recording)
            if meeting.recording is not None and meeting.recording.deleted_at is None
            else None
        ),
        latest_job=_job_response(job),
        quality=MeetingQualityResponse.model_validate(
            assess_meeting(db, meeting=meeting, job=job).as_dict()
        ),
        ingestion=IngestionResponse.model_validate(ingestion) if ingestion is not None else None,
    )


@router.get("/meetings/{meeting_id}/job", response_model=JobResponse)
def get_meeting_job(
    meeting_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> JobResponse:
    _get_meeting(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    job = latest_job_for_meeting(
        db,
        workspace_id=context.workspace.id,
        meeting_id=meeting_id,
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": "Processing job not found"},
        )
    return JobResponse.model_validate(job)


@router.patch("/meetings/{meeting_id}", response_model=MeetingResponse)
def update_meeting(
    meeting_id: str,
    payload: MeetingUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MeetingResponse:
    meeting = _get_meeting(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    changed: list[str] = []
    if "title" in payload.model_fields_set and payload.title is not None:
        meeting.title = payload.title.strip()
        changed.append("title")
    if "team_id" in payload.model_fields_set:
        meeting.team_id = validate_team_attribution(db, context=context, team_id=payload.team_id)
        changed.append("team_id")
    if "agent_user_id" in payload.model_fields_set:
        meeting.agent_user_id = validate_agent_attribution(
            db, context=context, agent_user_id=payload.agent_user_id
        )
        changed.append("agent_user_id")
    if not changed:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_changes", "message": "No updatable field was provided"},
        )
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_UPDATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="meeting",
        target_id=meeting.id,
        request_id=getattr(request.state, "request_id", None),
        details={"fields": ",".join(changed)},
        settings=settings,
    )
    db.commit()
    db.refresh(meeting)
    return _meeting_response(meeting)


@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(
    meeting_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_DELETE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    meeting = _get_meeting(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    job = latest_job_for_meeting(
        db,
        workspace_id=context.workspace.id,
        meeting_id=meeting.id,
    )
    now = datetime.now(UTC)
    if job is not None and cancel_job(
        db,
        job,
        reason=MEETING_DELETED,
        message="The meeting was deleted before processing finished",
        now=now,
    ):
        # A running attempt lost its lease above; its next fenced write sees
        # CANCELLED and stops without persisting anything. Reservations were
        # released; a still-pending publication is withdrawn here.
        cancel_dispatch(db, job.id)

    meeting.status = MeetingStatus.CANCELLED
    # Tombstone first (WP07-T03, ADR 0021): from this commit on every read,
    # share, export, download, search, replay and publication path refuses
    # the conversation; the durable purge row carries the rest.
    purge = tombstone_meeting(
        db,
        meeting=meeting,
        reason=REASON_MEETING_DELETED,
        actor_user_id=context.actor.user.id,
        now=now,
        settings=settings,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_DELETED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="meeting",
        target_id=meeting.id,
        request_id=getattr(request.state, "request_id", None),
        settings=settings,
    )
    db.commit()

    # The tombstone reaches the journal outside the backups (WP07-T04) before
    # any destructive step; a failure is logged and the sweep writes it later.
    journal = journal_for(settings)
    if journal is not None:
        journal_pending(
            db, journal, workspace_id=context.workspace.id, now=now, limit=JOURNAL_INLINE_LIMIT
        )

    # First purge attempt right away; the retention purge sweep retries
    # whatever this one could not finish (storage failure, hold, crash).
    try:
        run_purge_attempt(
            db,
            create_object_storage(settings),
            purge_id=purge.id,
            workspace_id=context.workspace.id,
            now=datetime.now(UTC),
            settings=settings,
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "MEETING_PURGE_ATTEMPT_FAILED meeting_id=%s error_type=%s",
            meeting.id,
            type(exc).__name__,
        )
    return None


@router.get("/meetings/{meeting_id}/media", response_model=MediaObjectListResponse)
def list_meeting_media(
    meeting_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> MediaObjectListResponse:
    meeting = _get_meeting(db, context=context, meeting_id=meeting_id)
    objects = media_for_meeting(db, workspace_id=context.workspace.id, meeting_id=meeting.id)
    return MediaObjectListResponse(
        items=[MediaObjectResponse.model_validate(media) for media in objects]
    )


@router.post("/meetings/{meeting_id}/media/{media_id}/verify", response_model=MediaObjectResponse)
def verify_meeting_media(
    meeting_id: str,
    media_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MediaObjectResponse:
    """Re-read one object and record whether its bytes still match the row."""
    meeting = _get_meeting(db, context=context, meeting_id=meeting_id)
    media = media_by_id(db, workspace_id=context.workspace.id, media_id=media_id)
    if media is None or media.meeting_id != meeting.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "media_not_found", "message": "Media object not found"},
        )
    try:
        verify_integrity(
            db,
            create_object_storage(settings),
            media=media,
            actor_user_id=context.actor.user.id,
            now=datetime.now(UTC),
            settings=settings,
        )
    except MediaLifecycleError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    db.commit()
    db.refresh(media)
    return MediaObjectResponse.model_validate(media)


def _media_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail={"code": "media_not_found", "message": "Media object not found"}
    )


def _hold_refused(exc: MediaLifecycleError) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})


@router.post("/meetings/{meeting_id}/legal-hold", response_model=MediaObjectListResponse)
def hold_meeting_media(
    meeting_id: str,
    payload: LegalHoldRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.LEGAL_HOLD_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MediaObjectListResponse:
    """Hold every live object of a conversation (WP07-T02, owner only).

    The hold overrides retention expiry and any deletion request until it is
    released; already deleted objects cannot be held and are reported as such.
    """
    meeting = _get_meeting(db, context=context, meeting_id=meeting_id)
    now = datetime.now(UTC)
    # Locked, fresh rows (WP07-T03): a removal that already started is seen
    # as ``deleted`` instead of being overwritten by the hold.
    objects = media_for_meeting(
        db, workspace_id=context.workspace.id, meeting_id=meeting.id, for_update=True
    )
    if not objects:
        raise _media_not_found()
    try:
        for media in objects:
            if media.lifecycle_state is MediaLifecycleState.DELETED:
                continue
            place_legal_hold(
                db,
                media=media,
                reason=payload.reason,
                review_at=payload.review_at,
                actor_user_id=context.actor.user.id,
                now=now,
                settings=settings,
            )
    except MediaLifecycleError as exc:
        db.rollback()
        raise _hold_refused(exc) from exc
    db.commit()
    for media in objects:
        db.refresh(media)
    return MediaObjectListResponse(
        items=[MediaObjectResponse.model_validate(media) for media in objects]
    )


@router.post("/media/{media_id}/legal-hold", response_model=MediaObjectResponse)
def hold_media_object(
    media_id: str,
    payload: LegalHoldRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.LEGAL_HOLD_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MediaObjectResponse:
    """Hold one object by id — also reachable for objects of a deleted conversation."""
    media = media_by_id(db, workspace_id=context.workspace.id, media_id=media_id, for_update=True)
    if media is None:
        raise _media_not_found()
    try:
        place_legal_hold(
            db,
            media=media,
            reason=payload.reason,
            review_at=payload.review_at,
            actor_user_id=context.actor.user.id,
            now=datetime.now(UTC),
            settings=settings,
        )
    except MediaLifecycleError as exc:
        db.rollback()
        raise _hold_refused(exc) from exc
    db.commit()
    db.refresh(media)
    return MediaObjectResponse.model_validate(media)


@router.post("/media/{media_id}/legal-hold/release", response_model=MediaObjectResponse)
def release_media_hold(
    media_id: str,
    payload: LegalHoldReleaseRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.LEGAL_HOLD_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MediaObjectResponse:
    """Release a hold with an audited reason; a recorded deletion request then runs."""
    media = media_by_id(db, workspace_id=context.workspace.id, media_id=media_id, for_update=True)
    if media is None:
        raise _media_not_found()
    if media.lifecycle_state is not MediaLifecycleState.LEGAL_HOLD:
        raise HTTPException(
            status_code=409, detail={"code": "not_held", "message": "The object is not held"}
        )
    now = datetime.now(UTC)
    try:
        pending = release_legal_hold(
            db,
            media=media,
            reason=payload.reason,
            actor_user_id=context.actor.user.id,
            now=now,
            settings=settings,
        )
    except MediaLifecycleError as exc:
        db.rollback()
        raise _hold_refused(exc) from exc
    # A purge deferred by this hold becomes due now (WP07-T03).
    nudge_purge(db, workspace_id=context.workspace.id, meeting_id=media.meeting_id, now=now)
    db.commit()  # state first (ADR 0017) ...
    if pending:
        # ... bytes second: the recorded deletion request resumes now; a
        # failed removal leaves the row pending for the reconciliation sweep.
        delete_objects(
            db,
            create_object_storage(settings),
            objects=[media],
            now=now,
            settings=settings,
            actor_user_id=context.actor.user.id,
        )
        db.commit()
    db.refresh(media)
    return MediaObjectResponse.model_validate(media)


@router.get("/meetings/{meeting_id}/recording")
def download_recording(
    meeting_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    meeting = _get_meeting(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    recording: Recording | None = meeting.recording
    if recording is None or recording.deleted_at is not None:
        raise HTTPException(
            status_code=404,
            detail={"code": "recording_not_found", "message": "Recording not found"},
        )

    return deliver_recording(
        storage=create_object_storage(settings),
        workspace_id=context.workspace.id,
        recording=recording,
        range_header=request.headers.get("range"),
        presign_ttl_seconds=settings.storage_presign_ttl_seconds,
    )


@router.get("/workspaces/{workspace_id}/meetings/{meeting_id}/recording")
def play_recording(
    meeting_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_path_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Inline playback of the original recording (gap G11).

    A native ``<audio>`` element cannot send ``X-Workspace-ID``, so the
    workspace is part of the path and the session cookie authenticates.  Each
    request, including every ``Range`` request, re-checks the current
    membership, ``meeting:read``, the record scope and the tombstone, so a
    removed member, an expired session or a deleted meeting stops playback on
    the next request.  With presigning storage the redirect is object-scoped
    and expires after ``STORAGE_PRESIGN_TTL_SECONDS`` (expiry-bounded, as for
    downloads)."""
    meeting = _get_meeting(db, context=context, meeting_id=meeting_id)
    recording: Recording | None = meeting.recording
    if recording is None or recording.deleted_at is not None:
        raise HTTPException(
            status_code=404,
            detail={"code": "recording_not_found", "message": "Recording not found"},
        )
    return deliver_recording(
        storage=create_object_storage(settings),
        workspace_id=context.workspace.id,
        recording=recording,
        range_header=request.headers.get("range"),
        presign_ttl_seconds=settings.storage_presign_ttl_seconds,
        disposition="inline",
    )
