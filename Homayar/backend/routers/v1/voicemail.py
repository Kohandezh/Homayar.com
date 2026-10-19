"""Voicemail API (P0 voicemail processing pipeline).

Routes (mounted under ``settings.api_prefix`` by the integrator):

* ``POST  /voicemails``            import one voicemail (multipart), MEETING_CREATE
* ``GET   /voicemails``            list / filter / search, MEETING_READ
* ``GET   /voicemails/analytics``  aggregates for a window, MEETING_READ
* ``GET   /voicemails/{id}``       one voicemail with a transcript excerpt, MEETING_READ
* ``PATCH /voicemails/{id}``       follow-up state and assignment, MEETING_UPDATE
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import ActionItem, ActionStatus, Meeting
from backend.media import MediaValidationError
from backend.schemas.meetings import JobResponse
from backend.schemas.voicemail import (
    DayCount,
    VoicemailAnalyticsResponse,
    VoicemailDetailResponse,
    VoicemailImportResponse,
    VoicemailListResponse,
    VoicemailResponse,
    VoicemailUpdateRequest,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.entitlement_service import QuotaExceededError
from backend.services.ingestion_service import IngestionIdentity, audit_rejection
from backend.services.rate_limit_service import RateLimiter, RateLimitExceeded, RateLimitUnavailable
from backend.services.team_service import validate_agent_attribution, validate_team_attribution
from backend.services.upload_service import UploadRejectedError, stage_upload
from backend.storage import StorageError
from backend.voicemail.ingest import (
    EVENT_PREFIX,
    EXTERNAL_ID_PATTERN,
    VoicemailEnvelope,
    VoicemailImportError,
    import_voicemail,
    normalize_caller_number,
    voicemail_for_external_id,
)
from backend.voicemail.models import (
    VOICEMAIL_SOURCE_PROVIDER,
    VOICEMAIL_SOURCE_TYPE,
    FollowUpState,
    VoicemailCategory,
    VoicemailPriority,
    VoicemailStatus,
)
from backend.voicemail.search import (
    VoicemailFilters,
    get_voicemail,
    list_voicemails,
    transcript_excerpt,
    voicemail_analytics,
)

router = APIRouter(tags=["voicemail"])

_MAX_ANALYTICS_DAYS = 366


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail={"code": "voicemail_not_found", "message": "Voicemail not found"}
    )


def _media_failure(error: Exception) -> HTTPException:
    if isinstance(error, UploadRejectedError):
        status = 413 if error.code == "upload_too_large" else 422
        return HTTPException(status_code=status, detail={"code": error.code, "message": str(error)})
    if isinstance(error, MediaValidationError):
        if not error.terminal:
            return HTTPException(
                status_code=503,
                detail={"code": error.code, "message": str(error)},
                headers={"Retry-After": "30"},
            )
        status = 413 if error.code in {"media_file_too_large", "media_duration_exceeded"} else 422
        return HTTPException(status_code=status, detail={"code": error.code, "message": str(error)})
    if isinstance(error, QuotaExceededError):
        return HTTPException(
            status_code=429,
            detail={
                "code": "concurrent_job_limit_reached"
                if error.metric == "concurrent_jobs"
                else "audio_quota_exceeded",
                "message": "The workspace processing allowance is exhausted",
            },
        )
    return HTTPException(
        status_code=503,
        detail={"code": "storage_unavailable", "message": "Recording storage is unavailable"},
        headers={"Retry-After": "30"},
    )


def _enforce_rate_limit(settings: Settings, workspace_id: str) -> None:
    # Shares the upload budget: a voicemail import is an upload.
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
            detail={"code": "rate_limiter_unavailable", "message": "Temporarily unavailable"},
        ) from exc


@router.post("/voicemails", response_model=VoicemailImportResponse, status_code=202)
async def import_voicemail_route(
    request: Request,
    file: UploadFile = File(...),
    external_message_id: str = Form(..., pattern=EXTERNAL_ID_PATTERN),
    received_at: datetime | None = Form(default=None),
    caller_number: str | None = Form(default=None, max_length=40),
    mailbox: str | None = Form(default=None, max_length=80),
    extension: str | None = Form(default=None, max_length=40),
    title: str | None = Form(default=None, max_length=240),
    language: str = Form(default="fa", min_length=2, max_length=12, pattern=r"^[A-Za-z-]+$"),
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_CREATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VoicemailImportResponse:
    user = context.actor.user
    if user.email_verified_at is None and user.phone_verified_at is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "verified_identity_required",
                "message": "Verify an email address or phone number before uploading",
            },
        )
    workspace_id = context.workspace.id
    request_id = getattr(request.state, "request_id", None)
    if (
        voicemail_for_external_id(
            db, workspace_id=workspace_id, external_message_id=external_message_id
        )
        is None
    ):
        _enforce_rate_limit(settings, workspace_id)
    envelope = VoicemailEnvelope(
        external_message_id=external_message_id,
        received_at=received_at,
        caller_number=caller_number,
        mailbox=(mailbox or "").strip() or None,
        extension=(extension or "").strip() or None,
        language=language,
        title=title,
    )
    staged = None
    try:
        staged = await stage_upload(
            file,
            quarantine_dir=Path(settings.upload_quarantine_dir),
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
        )
        result = import_voicemail(
            db,
            context=context,
            staged=staged,
            envelope=envelope,
            settings=settings,
            request_id=request_id,
        )
    except VoicemailImportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc), "details": exc.details},
        ) from exc
    except (UploadRejectedError, MediaValidationError, QuotaExceededError, StorageError) as exc:
        db.rollback()
        audit_rejection(
            db,
            workspace_id=workspace_id,
            identity=IngestionIdentity(
                source_type=VOICEMAIL_SOURCE_TYPE,
                source_provider=VOICEMAIL_SOURCE_PROVIDER,
                external_event_id=EVENT_PREFIX + external_message_id,
            ),
            reason=getattr(exc, "code", None) or type(exc).__name__.lower(),
            terminal=not (isinstance(exc, MediaValidationError) and not exc.terminal)
            and not isinstance(exc, StorageError),
            actor_user_id=user.id,
            request_id=request_id,
            settings=settings,
        )
        db.commit()
        raise _media_failure(exc) from exc
    finally:
        if staged is not None:
            staged.delete()
    return VoicemailImportResponse(
        voicemail=VoicemailResponse.model_validate(result.voicemail),
        job=JobResponse.model_validate(result.job) if result.job is not None else None,
        idempotent_replay=result.replayed,
    )


@router.get("/voicemails", response_model=VoicemailListResponse)
def list_voicemails_route(
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = Query(default=None),
    status: VoicemailStatus | None = Query(default=None),
    category: VoicemailCategory | None = Query(default=None),
    priority: VoicemailPriority | None = Query(default=None),
    follow_up_state: FollowUpState | None = Query(default=None),
    assigned_user_id: str | None = Query(default=None, max_length=36),
    caller_number: str | None = Query(default=None, max_length=40),
    received_from: datetime | None = Query(default=None),
    received_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, min_length=2, max_length=200),
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> VoicemailListResponse:
    items = list_voicemails(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        filters=VoicemailFilters(
            status=status.value if status else None,
            category=category.value if category else None,
            priority=priority.value if priority else None,
            follow_up_state=follow_up_state.value if follow_up_state else None,
            assigned_user_id=assigned_user_id,
            caller_number=normalize_caller_number(caller_number)[0] if caller_number else None,
            received_from=received_from,
            received_to=received_to,
            q=q,
            before=before,
            limit=limit,
        ),
    )
    return VoicemailListResponse(
        items=[VoicemailResponse.model_validate(item) for item in items],
        next_before=items[-1].received_at if len(items) == limit else None,
    )


@router.get("/voicemails/analytics", response_model=VoicemailAnalyticsResponse)
def voicemail_analytics_route(
    received_from: datetime | None = Query(default=None),
    received_to: datetime | None = Query(default=None),
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> VoicemailAnalyticsResponse:
    end = received_to or datetime.now(UTC)
    start = received_from or end - timedelta(days=30)
    if start >= end or end - start > timedelta(days=_MAX_ANALYTICS_DAYS):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_window", "message": "Window must be 1 second to 366 days"},
        )
    stats = voicemail_analytics(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        received_from=start,
        received_to=end,
    )
    return VoicemailAnalyticsResponse(
        received_from=start,
        received_to=end,
        total=stats.total,
        by_category=stats.by_category,
        by_priority=stats.by_priority,
        by_status=stats.by_status,
        by_follow_up_state=stats.by_follow_up_state,
        by_day=[DayCount(day=day, count=count) for day, count in stats.by_day],
        followed_up=stats.followed_up,
        median_follow_up_seconds=stats.median_follow_up_seconds,
    )


def _detail(db: Session, voicemail) -> VoicemailDetailResponse:
    excerpt, revision_id = transcript_excerpt(db, voicemail)
    meeting = db.get(Meeting, voicemail.meeting_id)
    base = VoicemailResponse.model_validate(voicemail).model_dump()
    return VoicemailDetailResponse(
        **base,
        meeting_status=meeting.status.value if meeting else "unknown",
        transcript_excerpt=excerpt,
        transcript_revision_id=revision_id,
    )


@router.get("/voicemails/{voicemail_id}", response_model=VoicemailDetailResponse)
def get_voicemail_route(
    voicemail_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> VoicemailDetailResponse:
    voicemail = get_voicemail(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        voicemail_id=voicemail_id,
    )
    if voicemail is None:
        raise _not_found()
    return _detail(db, voicemail)


@router.patch("/voicemails/{voicemail_id}", response_model=VoicemailDetailResponse)
def update_voicemail_route(
    voicemail_id: str,
    payload: VoicemailUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VoicemailDetailResponse:
    voicemail = get_voicemail(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        voicemail_id=voicemail_id,
    )
    if voicemail is None:
        raise _not_found()
    fields = payload.model_fields_set
    changed: list[str] = []
    now = datetime.now(UTC)
    action = (
        db.scalar(
            select(ActionItem).where(
                ActionItem.id == voicemail.follow_up_action_id,
                ActionItem.workspace_id == context.workspace.id,
            )
        )
        if voicemail.follow_up_action_id
        else None
    )
    if "assigned_user_id" in fields:
        voicemail.assigned_user_id = validate_agent_attribution(
            db, context=context, agent_user_id=payload.assigned_user_id
        )
        if action is not None:
            action.assignee_user_id = voicemail.assigned_user_id
        changed.append("assigned_user_id")
    if "assigned_team_id" in fields:
        voicemail.assigned_team_id = (
            validate_team_attribution(db, context=context, team_id=payload.assigned_team_id)
            if payload.assigned_team_id is not None
            else None
        )
        changed.append("assigned_team_id")
    if "follow_up_state" in fields and payload.follow_up_state is not None:
        state = payload.follow_up_state
        if state is FollowUpState.ACTION_CREATED and action is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "no_follow_up_action",
                    "message": "No follow-up action exists for this voicemail",
                },
            )
        voicemail.follow_up_state = state.value
        if state is FollowUpState.DONE:
            voicemail.follow_up_done_at = voicemail.follow_up_done_at or now
            if action is not None and action.status in (
                ActionStatus.OPEN,
                ActionStatus.IN_PROGRESS,
            ):
                action.status = ActionStatus.DONE
        else:
            voicemail.follow_up_done_at = None
        changed.append("follow_up_state")
    if not changed:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_changes", "message": "No updatable field was provided"},
        )
    record_audit_event(
        db,
        event_type=AuditEventType.VOICEMAIL_UPDATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="voicemail",
        target_id=voicemail.id,
        request_id=getattr(request.state, "request_id", None),
        details={"fields": ",".join(changed), "follow_up_state": voicemail.follow_up_state},
        settings=settings,
    )
    db.commit()
    db.refresh(voicemail)
    return _detail(db, voicemail)
