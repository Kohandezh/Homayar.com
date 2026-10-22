from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import (
    ActionItem,
    Meeting,
    MeetingSummary,
    Recording,
    ShareAudience,
    ShareLink,
    Workspace,
)
from backend.privacy.transcript_view import MeetingView, enforced_view
from backend.routers.v1.insights import (
    _export_action,
    _export_revision,
    _quality,
    _transcript_response,
    action_response,
    export_privacy,
    summary_response,
)
from backend.schemas.insights import (
    ShareCreatedResponse,
    ShareCreateRequest,
    SharedMeetingResponse,
    ShareLinkResponse,
    ShareRecipientsUpdateRequest,
)
from backend.security.context import (
    Actor,
    Permission,
    WorkspaceContext,
    optional_actor,
    require_permission,
    require_write_permission,
)
from backend.security.tokens import token_digest
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.download_service import deliver_recording, ensure_shared_recording_playable
from backend.services.export_service import (
    ExportSegment,
    MeetingExport,
    render_meeting_export,
)
from backend.services.rate_limit_service import RateLimiter, RateLimitExceeded, RateLimitUnavailable
from backend.services.share_service import (
    ShareGrant,
    ShareOperation,
    ShareSpec,
    edit_recipients,
    issue_share,
    normalized_phone_recipients,
    normalized_recipients,
    resolve_share_grant,
    share_not_found,
    share_token_hash,
)
from backend.storage import create_object_storage

router = APIRouter(tags=["meeting sharing"])
_SHARE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_AUDITED_IDS = 20  # recipient row ids listed per audit row; counts are always complete


def _meeting_or_404(db: Session, *, context: WorkspaceContext, meeting_id: str) -> Meeting:
    meeting = db.scalar(
        select(Meeting).where(
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


def _share_response(share: ShareLink) -> ShareLinkResponse:
    return ShareLinkResponse.model_validate(share)


# --- management (workspace members holding share:manage) ----------------------


@router.post(
    "/meetings/{meeting_id}/shares",
    response_model=ShareCreatedResponse,
    status_code=201,
)
def create_share(
    meeting_id: str,
    payload: ShareCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.SHARE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ShareCreatedResponse:
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    raw_token = secrets.token_urlsafe(32)
    spec = ShareSpec(
        audience=ShareAudience(payload.resolved_audience()),
        recipients=normalized_recipients([str(email) for email in payload.recipients]),
        phone_recipients=normalized_phone_recipients(payload.phone_recipients),
        allow_download=payload.allow_download,
        allow_export=payload.allow_export,
        expires_in_hours=payload.expires_in_hours,
    )
    share = issue_share(
        db,
        workspace=context.workspace,
        meeting=meeting,
        created_by_user_id=context.actor.user.id,
        spec=spec,
        raw_token=raw_token,
        settings=settings,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_SHARE_CREATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="share_link",
        target_id=share.id,
        request_id=getattr(request.state, "request_id", None),
        details={
            "meeting_id": meeting.id,
            "audience": share.audience.value,
            "allow_download": share.allow_download,
            "allow_export": share.allow_export,
            "recipient_count": spec.recipient_count,
            "has_expiration": share.expires_at is not None,
        },
        settings=settings,
    )
    db.commit()
    db.refresh(share)
    base_url = settings.frontend_url.rstrip("/")
    return ShareCreatedResponse(
        **_share_response(share).model_dump(),
        url=f"{base_url}/share/{raw_token}",
    )


@router.get(
    "/meetings/{meeting_id}/shares",
    response_model=list[ShareLinkResponse],
)
def list_shares(
    meeting_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.SHARE_MANAGE)),
    db: Session = Depends(get_db),
) -> list[ShareLinkResponse]:
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    shares = db.scalars(
        select(ShareLink)
        .options(selectinload(ShareLink.recipients))
        .where(
            ShareLink.workspace_id == context.workspace.id,
            ShareLink.meeting_id == meeting.id,
        )
        .order_by(ShareLink.created_at.desc())
    )
    return [_share_response(share) for share in shares]


@router.delete(
    "/meetings/{meeting_id}/shares/{share_id}",
    status_code=204,
    response_class=Response,
)
def revoke_share(
    meeting_id: str,
    share_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.SHARE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    _meeting_or_404(db, context=context, meeting_id=meeting_id)
    share = db.scalar(
        select(ShareLink).where(
            ShareLink.id == share_id,
            ShareLink.workspace_id == context.workspace.id,
            ShareLink.meeting_id == meeting_id,
        )
    )
    if share is None:
        raise share_not_found()
    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
        record_audit_event(
            db,
            event_type=AuditEventType.MEETING_SHARE_REVOKED,
            actor_user_id=context.actor.user.id,
            workspace_id=context.workspace.id,
            target_type="share_link",
            target_id=share.id,
            request_id=getattr(request.state, "request_id", None),
            details={"meeting_id": meeting_id},
            settings=settings,
        )
        db.commit()
    return Response(status_code=204)


@router.patch(
    "/meetings/{meeting_id}/shares/{share_id}/recipients",
    response_model=ShareLinkResponse,
)
def edit_share_recipients(
    meeting_id: str,
    share_id: str,
    payload: ShareRecipientsUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.SHARE_MANAGE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ShareLinkResponse:
    """Add or remove recipients of a ``recipients`` share (delta, row-locked, audited)."""
    _meeting_or_404(db, context=context, meeting_id=meeting_id)
    edit = edit_recipients(
        db,
        share_id=share_id,
        workspace_id=context.workspace.id,
        meeting_id=meeting_id,
        add_emails=normalized_recipients([str(email) for email in payload.add_emails]),
        add_phones=normalized_phone_recipients(payload.add_phones),
        remove=payload.remove,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_SHARE_RECIPIENTS_CHANGED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="share_link",
        target_id=edit.share.id,
        request_id=getattr(request.state, "request_id", None),
        # Counts and row ids only: never addresses (ADR 0006, decision 8).
        details={
            "meeting_id": meeting_id,
            "added": len(edit.added_ids),
            "removed": len(edit.removed_ids),
            "added_ids": ",".join(edit.added_ids[:_AUDITED_IDS]),
            "removed_ids": ",".join(edit.removed_ids[:_AUDITED_IDS]),
            "ids_truncated": max(len(edit.added_ids), len(edit.removed_ids)) > _AUDITED_IDS,
        },
        settings=settings,
    )
    db.commit()
    db.refresh(edit.share)
    return _share_response(edit.share)


# --- token-addressed access ---------------------------------------------------


def _rate_limit_share(
    settings: Settings,
    *,
    token_hash: str,
    request: Request,
) -> None:
    ip = request.client.host if request.client else "unknown"
    ip_hash = token_digest(f"share-ip:{ip}", settings)
    try:
        RateLimiter(settings).hit(
            f"share:{token_hash}:{ip_hash}",
            limit=120,
            window_seconds=3600,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "share_rate_limited", "message": "Too many share requests"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "rate_limiter_unavailable",
                "message": "Share service is temporarily unavailable",
            },
        ) from exc


def _authorized_grant(
    db: Session,
    *,
    share_token: str,
    request: Request,
    actor: Actor | None,
    operation: ShareOperation,
    settings: Settings,
) -> ShareGrant:
    if not _SHARE_TOKEN.fullmatch(share_token):
        raise share_not_found()
    _rate_limit_share(settings, token_hash=share_token_hash(share_token, settings), request=request)
    return resolve_share_grant(
        db,
        raw_token=share_token,
        actor=actor.user if actor else None,
        operation=operation,
        settings=settings,
    )


def _record_access(
    db: Session,
    *,
    grant: ShareGrant,
    request: Request,
    operation: ShareOperation,
    settings: Settings,
    extra: dict | None = None,
) -> None:
    grant.share.access_count += 1
    grant.share.last_accessed_at = datetime.now(UTC)
    details = {
        "operation": operation,
        "audience": grant.share.audience.value,
        "authenticated": grant.actor_user is not None,
    }
    if extra:
        details.update(extra)
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_SHARE_ACCESSED,
        actor_user_id=grant.actor_user.id if grant.actor_user else None,
        workspace_id=grant.share.workspace_id,
        target_type="share_link",
        target_id=grant.share.id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        details=details,
        settings=settings,
    )


def _share_view(db: Session, *, grant: ShareGrant, settings: Settings) -> MeetingView:
    """A share never shows originals under an enforced policy (WP07-T06); the
    share session cannot write, so the redacted texts are computed, not stored."""
    workspace = db.get(Workspace, grant.share.workspace_id)
    assert workspace is not None
    return MeetingView(
        db,
        meeting=grant.meeting,
        policy=enforced_view(workspace),
        settings=settings,
        now=datetime.now(UTC),
        persist=False,
    )


def _summary_and_actions(
    db: Session, *, grant: ShareGrant
) -> tuple[MeetingSummary | None, list[ActionItem]]:
    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == grant.share.workspace_id,
            MeetingSummary.meeting_id == grant.meeting.id,
        )
    )
    actions = list(
        db.scalars(
            select(ActionItem)
            .where(
                ActionItem.workspace_id == grant.share.workspace_id,
                ActionItem.meeting_id == grant.meeting.id,
            )
            .order_by(ActionItem.created_at.asc())
        )
    )
    return summary, actions


@router.get("/shares/{share_token}", response_model=SharedMeetingResponse)
def access_share(
    share_token: str,
    request: Request,
    actor: Actor | None = Depends(optional_actor),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SharedMeetingResponse:
    grant = _authorized_grant(
        db,
        share_token=share_token,
        request=request,
        actor=actor,
        operation="view",
        settings=settings,
    )
    summary, actions = _summary_and_actions(db, grant=grant)
    view = _share_view(db, grant=grant, settings=settings)
    _record_access(
        db,
        grant=grant,
        request=request,
        operation="view",
        settings=settings,
        extra={"view": view.policy.view},
    )
    meeting = grant.meeting
    response = SharedMeetingResponse(
        meeting_id=meeting.id,
        title=meeting.title,
        language=meeting.language,
        duration_seconds=meeting.duration_seconds,
        created_at=meeting.created_at,
        audience=grant.share.audience.value,
        allow_download=grant.share.allow_download,
        allow_export=grant.share.allow_export,
        expires_at=grant.share.expires_at,
        transcript=_transcript_response(db, meeting=meeting, view=view),
        summary=summary_response(summary, view) if summary else None,
        actions=[action_response(action, view) for action in actions],
        quality=_quality(db, meeting, with_job=False, settings=settings),
    )
    db.commit()
    return response


@router.get("/shares/{share_token}/export")
def export_share(
    share_token: str,
    request: Request,
    format_name: Literal["txt", "json", "srt", "vtt"] = Query(alias="format"),
    actor: Actor | None = Depends(optional_actor),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    grant = _authorized_grant(
        db,
        share_token=share_token,
        request=request,
        actor=actor,
        operation="export",
        settings=settings,
    )
    meeting = grant.meeting
    view = _share_view(db, grant=grant, settings=settings)
    transcript = _transcript_response(db, meeting=meeting, view=view)
    summary, actions = _summary_and_actions(db, grant=grant)
    rendered = render_meeting_export(
        MeetingExport(
            meeting_id=meeting.id,
            title=meeting.title,
            language=meeting.language,
            duration_seconds=meeting.duration_seconds,
            created_at=meeting.created_at,
            summary=view.text(summary.overview) if summary else None,
            topics=view.texts(list(summary.topics or [])) if summary else [],
            segments=[
                ExportSegment(
                    sequence=segment.sequence,
                    speaker=segment.speaker_name,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                )
                for segment in transcript.segments
            ],
            actions=[_export_action(action, view) for action in actions],
            transcript_revision=_export_revision(transcript),
            summary_evidence_status=summary.evidence_status if summary else None,
            quality=_quality(db, meeting, with_job=False, settings=settings).model_dump(),
            privacy=export_privacy(db, meeting=meeting, view=view, transcript=transcript),
        ),
        format_name,
    )
    _record_access(
        db,
        grant=grant,
        request=request,
        operation="export",
        settings=settings,
        extra={"format": format_name, "view": view.policy.view},
    )
    db.commit()
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="homayar-{meeting.id}.{rendered.extension}"'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/shares/{share_token}/recording")
def download_shared_recording(
    share_token: str,
    request: Request,
    actor: Actor | None = Depends(optional_actor),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Original audio through a share; re-authorized on every (range) request."""
    grant = _authorized_grant(
        db,
        share_token=share_token,
        request=request,
        actor=actor,
        operation="download",
        settings=settings,
    )
    # Before the access audit: a refused request is not counted as a download.
    recording = ensure_shared_recording_playable(
        workspace=grant.workspace,
        meeting=grant.meeting,
        recording=db.scalar(
            select(Recording).where(
                Recording.meeting_id == grant.meeting.id,
                Recording.workspace_id == grant.share.workspace_id,
                Recording.deleted_at.is_(None),
            )
        ),
        now=datetime.now(UTC),
    )
    range_header = request.headers.get("range")
    # One audit row per download start; range continuations are not re-counted.
    if not range_header or range_header.strip().startswith("bytes=0-"):
        _record_access(db, grant=grant, request=request, operation="download", settings=settings)
    db.commit()
    return deliver_recording(
        storage=create_object_storage(settings),
        workspace_id=grant.share.workspace_id,
        recording=recording,
        range_header=range_header,
        presign_ttl_seconds=min(settings.storage_presign_ttl_seconds, 300),
    )
