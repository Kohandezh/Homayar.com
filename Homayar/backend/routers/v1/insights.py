from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import StringConstraints
from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import (
    ActionItem,
    ActionStatus,
    EvidenceSpan,
    EvidenceStatus,
    Meeting,
    MeetingSpeaker,
    MeetingSummary,
    Membership,
    TranscriptRedaction,
    TranscriptRevision,
    TranscriptSegment,
)
from backend.jobs import recompute
from backend.privacy.redaction_service import RedactionError, reveal_placeholder
from backend.privacy.text_pii import DETECTOR_VERSION
from backend.privacy.transcript_view import (
    MeetingView,
    PlaceholderRefused,
    ViewDenied,
    ViewPolicy,
    choose_view,
)
from backend.schemas.insights import (
    ActionItemCreateRequest,
    ActionItemResponse,
    ActionItemUpdateRequest,
    AnalysisRecomputeResponse,
    ContentRedactionInfo,
    EvidenceListResponse,
    EvidenceSpanResponse,
    PiiRevealRequest,
    PiiRevealResponse,
    SearchMatchResponse,
    SearchMeetingResponse,
    SearchResponse,
    SpeakerResponse,
    SpeakerUpdateRequest,
    SummaryResponse,
    TranscriptCorrectionRequest,
    TranscriptFilters,
    TranscriptRedactionInfo,
    TranscriptResponse,
    TranscriptRevisionListResponse,
    TranscriptRevisionResponse,
    TranscriptSegmentResponse,
)
from backend.schemas.meetings import MeetingQualityResponse
from backend.security.authorization import membership_effective_clause
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)
from backend.services.analysis_claims import claim_section
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.evidence_service import (
    FINDING_ACTION_ITEM,
    evidence_by_id,
    human_evidence,
    list_evidence,
)
from backend.services.export_service import (
    ExportAction,
    ExportEvidence,
    ExportRevision,
    ExportSegment,
    MeetingExport,
    render_meeting_export,
)
from backend.services.meeting_service import latest_job_for_meeting
from backend.services.quality_service import assess_meeting
from backend.services.rate_limit_service import RateLimiter, RateLimitExceeded, RateLimitUnavailable
from backend.services.report_service import evidence_status_of, meeting_completeness
from backend.services.transcript_service import (
    SegmentEdit,
    TranscriptError,
    correct_revision,
    current_analysis_run,
    current_revision,
    current_segments_clause,
    filter_segments,
    list_revisions,
    revision_by_id,
    segments_of,
)

router = APIRouter(tags=["meeting intelligence"])

ViewParam = Literal["original", "redacted"]
_VIEW_QUERY = Query(
    default=None,
    description=(
        "Transcript version (WP07-T06). Default: redacted when the workspace enforces "
        "redaction, otherwise original; 'original' under an enforced policy needs "
        "pii:original_access and is audited."
    ),
)
SpeakerKey = Annotated[str, StringConstraints(min_length=1, max_length=64)]
# Names no value and no placeholder (HOM-288).
_PLACEHOLDER_REFUSED = (
    "A masked value cannot be joined to other text or added when it is not shown. "
    "Keep a space around it, or remove it."
)


def invalid_time_range(message: str = "end_ms must be greater than start_ms") -> HTTPException:
    """A time window that selects nothing (HOM-60)."""
    return HTTPException(status_code=422, detail={"code": "invalid_time_range", "message": message})


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


def _speaker_map(speakers: list[MeetingSpeaker]) -> dict[str, str]:
    return {
        speaker.speaker_key: speaker.display_name or speaker.speaker_key for speaker in speakers
    }


def meeting_view(
    db: Session,
    *,
    context: WorkspaceContext,
    meeting: Meeting,
    requested: str | None,
    settings: Settings,
    request: Request | None,
    surface: str,
) -> MeetingView:
    """The caller's view of one meeting's texts (WP07-T06, ADR 0024).

    Under an enforced policy the original needs ``pii:original_access`` (never
    an API key) and an explicit request, and every such read is audited.
    """
    try:
        policy: ViewPolicy = choose_view(
            context.workspace,
            requested=requested,
            may_read_original=context.has(Permission.PII_ORIGINAL_ACCESS),
        )
    except ViewDenied as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": exc.code,
                "message": "This workspace redacts transcripts; the original needs "
                "pii:original_access",
            },
        ) from exc
    if policy.enforced and not policy.redacted:
        record_audit_event(
            db,
            event_type=AuditEventType.TRANSCRIPT_ORIGINAL_ACCESSED,
            actor_user_id=context.actor.user.id,
            workspace_id=meeting.workspace_id,
            target_type="meeting",
            target_id=meeting.id,
            request_id=getattr(request.state, "request_id", None) if request else None,
            details={"surface": surface},
            settings=settings,
        )
    return MeetingView(
        db,
        meeting=meeting,
        policy=policy,
        settings=settings,
        now=datetime.now(UTC),
        persist=True,
        actor_user_id=context.actor.user.id,
    )


def _redaction_info(view: MeetingView, revision: TranscriptRevision | None):
    redaction = view.revision(revision) if revision is not None else None
    if redaction is None or view.policy.digest is None:
        return None
    return TranscriptRedactionInfo(
        id=redaction.record.id if redaction.record is not None else None,
        detector_version=DETECTOR_VERSION,
        classes=[c.value for c in view.policy.classes],
        policy_digest=view.policy.digest,
        entity_count=redaction.entity_count,
        mapping_retained=bool(redaction.record and redaction.record.mapping_retained),
    )


def _transcript_response(
    db: Session,
    *,
    meeting: Meeting,
    view: MeetingView,
    revision: TranscriptRevision | None = None,
    filters: TranscriptFilters | None = None,
    settings: Settings | None = None,
) -> TranscriptResponse:
    """The segments of one revision — the current one unless asked otherwise —
    in the caller's view (original or redacted).

    With ``filters`` (HOM-60 navigation, possibly empty) only the matching
    segments are returned, and ``filters``, ``total_segments`` and
    ``completeness`` are set; without it (a share, an export) they stay
    null.  A filter only drops whole segments, so it never shows a text the
    unfiltered response would not.
    """
    if revision is None:
        revision = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    segments = segments_of(db, revision) if revision is not None else []
    navigation: dict[str, object] = {}
    if filters is not None:
        navigation = {
            "filters": filters,
            "total_segments": len(segments),
            "completeness": meeting_completeness(
                status=meeting.status, quality=_quality(db, meeting, settings=settings)
            ),
        }
        segments = filter_segments(
            segments,
            speaker_keys=frozenset(filters.speaker_keys),
            start_ms=filters.start_ms,
            end_ms=filters.end_ms,
        )
    speakers = list(
        db.scalars(
            select(MeetingSpeaker)
            .where(
                MeetingSpeaker.workspace_id == meeting.workspace_id,
                MeetingSpeaker.meeting_id == meeting.id,
            )
            .order_by(MeetingSpeaker.speaker_key.asc())
        )
    )
    names = _speaker_map(speakers)
    return TranscriptResponse(
        meeting_id=meeting.id,
        language=meeting.language,
        revision=(
            TranscriptRevisionResponse.model_validate(revision) if revision is not None else None
        ),
        segments=[
            TranscriptSegmentResponse(
                id=segment.id,
                sequence=segment.sequence,
                speaker_key=segment.speaker_key,
                speaker_name=names.get(segment.speaker_key, segment.speaker_key),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=view.segment_text(segment, revision) if revision is not None else segment.text,
                confidence=segment.confidence,
                language=segment.language,
            )
            for segment in segments
        ],
        speakers=[SpeakerResponse.model_validate(speaker) for speaker in speakers],
        view=view.policy.view,
        privacy_enforced=view.policy.enforced,
        redaction=_redaction_info(view, revision),
        **navigation,
    )


def _view_fields(view: MeetingView) -> dict[str, object]:
    """The view a free text (summary, action, evidence) was rendered in (G10)."""
    policy = view.policy
    redaction = (
        ContentRedactionInfo(
            detector_version=DETECTOR_VERSION,
            classes=[c.value for c in policy.classes],
            policy_digest=policy.digest,
        )
        if view.redacted and policy.digest is not None
        else None
    )
    return {"view": policy.view, "privacy_enforced": policy.enforced, "redaction": redaction}


def decision_detail(detail: object) -> tuple[str, str | None]:
    """The only shape a stored decision detail (HOM-61 owner / due date)
    leaves in: a stated string value, not_stated, or unavailable
    (HOM-294); anything else is never upgraded to stated."""
    status = detail.get("status") if isinstance(detail, dict) else None
    value = detail.get("value") if isinstance(detail, dict) else None
    if status == "stated" and isinstance(value, str):
        return "stated", value
    if status == "not_stated":
        return "not_stated", None
    return "unavailable", None


def _detail_fields(view: MeetingView, point: dict) -> dict[str, object]:
    """The owner and due date of a point, each present key in the only shape
    it leaves in (HOM-294); an absent detail key stays absent."""
    fields: dict[str, object] = {}
    for key in ("owner", "due_date"):
        if key not in point:
            continue
        status, value = decision_detail(point[key])
        fields[key] = {"status": status, "value": view.text(value) if value is not None else None}
    return fields


def _decision_point(view: MeetingView, point: object) -> object:
    """A decision in this view, including a structured decision's owner and
    due date (HOM-61); a present detail key leaves only in its allowed
    shape, an absent one stays absent."""
    rendered = view.point(point)
    if not isinstance(rendered, dict):
        return rendered
    return {**rendered, **_detail_fields(view, rendered)}


def _insight_point(view: MeetingView, point: object) -> object:
    """An insight key point in this view: only the keys the processor writes
    leave, each in its allowed shape, and a present owner or due date in the
    decision detail shape (HOM-304); any other key is dropped."""
    rendered = view.point(point)
    if not isinstance(rendered, dict):
        return rendered
    allowed: dict[str, object] = _detail_fields(view, rendered)
    for key, value in rendered.items():
        if key in ("category", "description"):
            if isinstance(value, str):
                allowed[key] = value
        elif key == "confidence":
            if value is None or (isinstance(value, int | float) and not isinstance(value, bool)):
                allowed[key] = value
        elif key == "source_turn_indexes":
            if isinstance(value, list) and all(
                isinstance(item, int) and not isinstance(item, bool) for item in value
            ):
                allowed[key] = value
        elif key == "evidence_ids":
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                allowed[key] = value
        elif key == "evidence_status":
            allowed[key] = evidence_status_of(value)
    return allowed


def summary_response(summary: MeetingSummary, view: MeetingView) -> SummaryResponse:
    response = SummaryResponse.model_validate(summary).model_copy(update=_view_fields(view))
    # Structured claims (HOM-61) are stored next to the insights; the key
    # points of /summary stay the insights only, each behind the allow list.
    response = response.model_copy(
        update={
            "key_points": [
                _insight_point(view, point)
                for point in response.key_points
                if claim_section(point) is None
            ]
        }
    )
    response = response.model_copy(
        update={"decisions": [_decision_point(view, point) for point in response.decisions]}
    )
    if not view.redacted:
        return response
    return response.model_copy(
        update={
            "overview": view.text(response.overview) or "",
            "topics": view.texts(response.topics),
        }
    )


def action_response(action: ActionItem, view: MeetingView) -> ActionItemResponse:
    response = ActionItemResponse.model_validate(action).model_copy(update=_view_fields(view))
    if not view.redacted:
        return response
    evidence = []
    for span, ref in zip(action.evidence, response.evidence, strict=True):
        _text, start, end = view.span(
            span.segment, span.segment.revision, span.text_start, span.text_end
        )
        evidence.append(ref.model_copy(update={"text_start": start, "text_end": end}))
    return response.model_copy(
        update={
            "description": view.text(response.description) or "",
            "assignee_text": view.text(response.assignee_text),
            "evidence": evidence,
        }
    )


@router.get("/meetings/{meeting_id}/transcript", response_model=TranscriptResponse)
def get_transcript(
    meeting_id: str,
    request: Request,
    revision_id: str | None = Query(default=None, min_length=1, max_length=36),
    view: ViewParam | None = _VIEW_QUERY,
    speaker_key: list[SpeakerKey] | None = Query(
        default=None,
        max_length=20,
        description="Keep the segments of these speakers (repeatable, at most 20).",
    ),
    start_ms: int | None = Query(
        default=None, ge=0, description="Keep the segments that end after this time."
    ),
    end_ms: int | None = Query(
        default=None, ge=1, description="Keep the segments that start before this time."
    ),
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TranscriptResponse:
    """The segments of one revision, optionally only those of some speakers
    that overlap ``[start_ms, end_ms)`` (HOM-60); ``speakers`` stays the
    meeting's full list and ``total_segments`` the unfiltered count."""
    meeting = _meeting_or_404(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    revision: TranscriptRevision | None = None
    if revision_id is not None:
        revision = revision_by_id(
            db, workspace_id=meeting.workspace_id, meeting_id=meeting.id, revision_id=revision_id
        )
        if revision is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "revision_not_found", "message": "Transcript revision not found"},
            )
    # Before the view: a refused window writes no original-access audit row.
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise invalid_time_range()
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="transcript",
    )
    filters = TranscriptFilters(
        speaker_keys=sorted(set(speaker_key or ())), start_ms=start_ms, end_ms=end_ms
    )
    response = _transcript_response(
        db,
        meeting=meeting,
        view=meeting_view_,
        revision=revision,
        filters=filters,
        settings=settings,
    )
    db.commit()  # a first redacted version or an original-access audit
    return response


@router.get(
    "/meetings/{meeting_id}/transcript/revisions",
    response_model=TranscriptRevisionListResponse,
)
def list_transcript_revisions(
    meeting_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> TranscriptRevisionListResponse:
    """Every revision of the meeting's transcript, newest first (WP04-T02)."""
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    revisions = list_revisions(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    return TranscriptRevisionListResponse(
        meeting_id=meeting.id,
        current_revision_id=next((r.id for r in revisions if r.is_current), None),
        items=[TranscriptRevisionResponse.model_validate(r) for r in revisions],
    )


@router.post(
    "/meetings/{meeting_id}/transcript/corrections",
    response_model=TranscriptResponse,
    status_code=201,
)
def correct_transcript(
    meeting_id: str,
    payload: TranscriptCorrectionRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TranscriptResponse:
    """Record a person's correction as a new revision of the current one.

    The base revision is never modified; the correction becomes the current
    revision and is audited by ids and counts (never by transcript text).
    Under the redacted view placeholders go back to their values; one that
    would be stored as text or shown raw is refused (``422
    invalid_placeholder``, HOM-288) and nothing is written.
    After the commit a recompute of the analysis on the corrected revision is
    dispatched (HOM-63) unless ``recompute_on_correction`` is off; a failed
    dispatch never fails the correction (the sweeper finds the meeting).
    """
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    view = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=None,
        settings=settings,
        request=request,
        surface="correction",
    )

    def _restored(text: str, base: TranscriptRevision, segment_id: str) -> str:
        # A person edits the redacted text: placeholders go back to originals.
        try:
            return view.restore_segment(text, revision=base, segment_id=segment_id)
        except PlaceholderRefused as exc:
            raise TranscriptError(exc.code, _PLACEHOLDER_REFUSED) from exc

    try:
        revision, _segments, changed = correct_revision(
            db,
            meeting=meeting,
            base_revision_id=payload.base_revision_id,
            edits=[
                SegmentEdit(edit.segment_id, text=edit.text, speaker_key=edit.speaker_key)
                for edit in payload.edits
            ],
            actor_user_id=context.actor.user.id,
            now=datetime.now(UTC),
            note=payload.reason,
            restore=_restored,
        )
    except TranscriptError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.public_message},
        ) from exc
    record_audit_event(
        db,
        event_type=AuditEventType.TRANSCRIPT_REVISION_CREATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="transcript_revision",
        target_id=revision.id,
        request_id=getattr(request.state, "request_id", None),
        details={
            "meeting_id": meeting.id,
            "revision_number": revision.revision_number,
            "parent_revision_id": revision.parent_revision_id,
            "source": revision.source.value,
            "reason": revision.reason,
            "correction_reason": payload.reason[:120],
            "segments_changed": changed,
            "segment_count": revision.segment_count,
        },
        settings=settings,
    )
    recompute_due = settings.recompute_on_correction and recompute.record_recompute_request(
        db,
        meeting=meeting,
        revision_id=revision.id,
        trigger=recompute.TRIGGER_CORRECTION,
        actor_user_id=context.actor.user.id,
        request_id=getattr(request.state, "request_id", None),
        settings=settings,
    )
    response = _transcript_response(
        db,
        meeting=meeting,
        view=view,
        revision=revision,
        filters=TranscriptFilters(),
        settings=settings,
    )
    revision_id, workspace_id = revision.id, meeting.workspace_id
    db.commit()
    if recompute_due:
        recompute.dispatch_recompute(
            meeting_id, revision_id, workspace_id=workspace_id, settings=settings
        )
    return response


def _enforce_recompute_rate_limit(settings: Settings, workspace_id: str) -> None:
    """Manual recompute requests per workspace and minute (HOM-63), like uploads."""
    try:
        RateLimiter(settings).hit(
            f"recompute:workspace:{workspace_id}",
            limit=settings.recompute_manual_rate_limit_per_minute,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "recompute_rate_limited", "message": "Too many recompute requests"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "rate_limiter_unavailable",
                "message": "Recompute is temporarily unavailable",
            },
        ) from exc


@router.post(
    "/meetings/{meeting_id}/analysis/recompute",
    response_model=AnalysisRecomputeResponse,
    status_code=202,
)
def recompute_analysis(
    meeting_id: str,
    request: Request,
    response: Response,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AnalysisRecomputeResponse:
    """Ask again for the recompute of a stale analysis (HOM-63).

    Not stale: ``200 up_to_date`` and nothing is written.  Stale: the request
    is audited, a recompute is dispatched and ``202`` carries the recompute
    status after the dispatch.  Allowed after ``failed``: a person may ask for
    one more attempt beyond the sweeper's cap.  Every request counts against
    the workspace's ``recompute_manual_rate_limit_per_minute`` (429 over it).
    """
    _enforce_recompute_rate_limit(settings, context.workspace.id)
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    quality = _quality(db, meeting, settings=settings)
    if not quality.analysis_stale or quality.transcript_revision_id is None:
        response.status_code = 200
        return AnalysisRecomputeResponse(status=quality.recompute_status)
    revision_id, workspace_id = quality.transcript_revision_id, meeting.workspace_id
    recompute.record_recompute_request(
        db,
        meeting=meeting,
        revision_id=revision_id,
        trigger=recompute.TRIGGER_MANUAL,
        actor_user_id=context.actor.user.id,
        request_id=getattr(request.state, "request_id", None),
        settings=settings,
    )
    db.commit()
    recompute.dispatch_recompute(
        meeting_id, revision_id, workspace_id=workspace_id, settings=settings
    )
    return AnalysisRecomputeResponse(
        status=_quality(db, meeting, settings=settings).recompute_status
    )


def _evidence_response(
    span: EvidenceSpan, *, current_revision_id: str | None, view: MeetingView
) -> EvidenceSpanResponse:
    segment = span.segment
    text, text_start, text_end = view.span(
        segment, segment.revision, span.text_start, span.text_end
    )
    return EvidenceSpanResponse(
        id=span.id,
        meeting_id=span.meeting_id,
        analysis_run_id=span.analysis_run_id,
        transcript_revision_id=span.transcript_revision_id,
        revision_number=segment.revision.revision_number,
        is_current_revision=span.transcript_revision_id == current_revision_id,
        segment_id=span.segment_id,
        speaker_key=span.speaker_key,
        finding_type=span.finding_type,
        finding_id=span.finding_id,
        finding_index=span.finding_index,
        text_start=text_start,
        text_end=text_end,
        text=text,
        start_ms=span.start_ms,
        end_ms=span.end_ms,
        confidence=span.confidence,
        evidence_type=span.evidence_type,
        created_at=span.created_at,
    )


@router.get("/meetings/{meeting_id}/evidence", response_model=EvidenceListResponse)
def list_meeting_evidence(
    meeting_id: str,
    request: Request,
    view: ViewParam | None = _VIEW_QUERY,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvidenceListResponse:
    """Citations of the current analysis run and of people, resolved to text and audio."""
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="evidence",
    )
    run = current_analysis_run(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    current_id = current.id if current is not None else None
    spans = list_evidence(
        db,
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        run_id=run.id if run is not None else None,
    )
    response = EvidenceListResponse(
        meeting_id=meeting.id,
        analysis_run_id=run.id if run is not None else None,
        current_revision_id=current_id,
        items=[
            _evidence_response(span, current_revision_id=current_id, view=meeting_view_)
            for span in spans
        ],
        **_view_fields(meeting_view_),
    )
    db.commit()
    return response


@router.get("/meetings/{meeting_id}/evidence/{evidence_id}", response_model=EvidenceSpanResponse)
def get_meeting_evidence(
    meeting_id: str,
    evidence_id: str,
    request: Request,
    view: ViewParam | None = _VIEW_QUERY,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvidenceSpanResponse:
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    span = evidence_by_id(
        db, workspace_id=meeting.workspace_id, meeting_id=meeting.id, evidence_id=evidence_id
    )
    if span is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "evidence_not_found", "message": "Evidence not found"},
        )
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="evidence",
    )
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    response = _evidence_response(
        span, current_revision_id=current.id if current else None, view=meeting_view_
    )
    db.commit()
    return response


def _quality(
    db: Session,
    meeting: Meeting,
    *,
    with_job: bool = True,
    settings: Settings | None = None,
) -> MeetingQualityResponse:
    """The meeting's quality assessment (WP04-T06).

    ``with_job=False`` is the share-token view: the job, its stage runs and the
    usage reservation lie outside the share ceiling (workspace-only tables),
    so a shared meeting reports the result states without stage degradations
    or usage.  ``settings`` carries the recompute attempt cap (HOM-63).
    """
    job = (
        latest_job_for_meeting(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
        if with_job
        else None
    )
    return MeetingQualityResponse.model_validate(
        assess_meeting(
            db,
            meeting=meeting,
            job=job,
            recompute_max_attempts=(
                settings.recompute_max_attempts if settings is not None else None
            ),
        ).as_dict()
    )


@router.get("/meetings/{meeting_id}/quality", response_model=MeetingQualityResponse)
def get_meeting_quality(
    meeting_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MeetingQualityResponse:
    """Quality, coverage and degradation of the meeting's results (WP04-T06)."""
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    return _quality(db, meeting, settings=settings)


@router.get("/meetings/{meeting_id}/summary", response_model=SummaryResponse)
def get_summary(
    meeting_id: str,
    request: Request,
    view: ViewParam | None = _VIEW_QUERY,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SummaryResponse:
    meeting = _meeting_or_404(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == context.workspace.id,
            MeetingSummary.meeting_id == meeting.id,
        )
    )
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "summary_not_ready", "message": "Summary is not available"},
        )
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="summary",
    )
    response = summary_response(summary, meeting_view_)
    db.commit()
    return response


@router.get("/meetings/{meeting_id}/actions", response_model=list[ActionItemResponse])
def list_actions(
    meeting_id: str,
    request: Request,
    view: ViewParam | None = _VIEW_QUERY,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ActionItemResponse]:
    meeting = _meeting_or_404(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    actions = list(
        db.scalars(
            select(ActionItem)
            .where(
                ActionItem.workspace_id == context.workspace.id,
                ActionItem.meeting_id == meeting.id,
            )
            .order_by(ActionItem.created_at.asc())
        )
    )
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="actions",
    )
    response = [action_response(action, meeting_view_) for action in actions]
    db.commit()
    return response


def _source_segment(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    segment_id: str | None,
) -> TranscriptSegment | None:
    if segment_id is None:
        return None
    segment = db.scalar(
        select(TranscriptSegment).where(
            TranscriptSegment.id == segment_id,
            TranscriptSegment.workspace_id == workspace_id,
            TranscriptSegment.meeting_id == meeting_id,
            current_segments_clause(workspace_id),
        )
    )
    if segment is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_source_segment",
                "message": "Source transcript segment is invalid",
            },
        )
    return segment


def _validate_assignee(
    db: Session,
    *,
    workspace_id: str,
    user_id: str | None,
) -> None:
    if user_id is None:
        return
    membership = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
            membership_effective_clause(datetime.now(UTC)),
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_assignee", "message": "Assignee is not a workspace member"},
        )


@router.post(
    "/meetings/{meeting_id}/actions",
    response_model=ActionItemResponse,
    status_code=201,
)
def create_action(
    meeting_id: str,
    payload: ActionItemCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionItemResponse:
    meeting = _meeting_or_404(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    _validate_assignee(
        db,
        workspace_id=context.workspace.id,
        user_id=payload.assignee_user_id,
    )
    segment = _source_segment(
        db,
        workspace_id=context.workspace.id,
        meeting_id=meeting.id,
        segment_id=payload.source_segment_id,
    )
    view = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=None,
        settings=settings,
        request=request,
        surface="actions",
    )
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    action = ActionItem(
        workspace_id=context.workspace.id,
        meeting_id=meeting.id,
        description=view.restore(payload.description.strip(), revision=current),
        assignee_text=(
            view.restore(payload.assignee_text.strip(), revision=current)
            if payload.assignee_text
            else None
        ),
        assignee_user_id=payload.assignee_user_id,
        due_at=payload.due_at,
        priority=payload.priority,
        status=ActionStatus.OPEN,
        source_segment_id=segment.id if segment else None,
        source_timestamp_ms=segment.start_ms if segment else None,
        created_by="user",
        evidence_status=(
            EvidenceStatus.SUFFICIENT.value if segment else EvidenceStatus.INSUFFICIENT.value
        ),
    )
    db.add(action)
    db.flush()
    if segment is not None:
        # The person's own citation of the current revision (WP04-T03).
        human_evidence(
            db,
            meeting=meeting,
            segment=segment,
            finding_type=FINDING_ACTION_ITEM,
            finding_id=action.id,
            now=datetime.now(UTC),
        )
    record_audit_event(
        db,
        event_type=AuditEventType.ACTION_ITEM_CREATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="action_item",
        target_id=action.id,
        request_id=getattr(request.state, "request_id", None),
        details={"meeting_id": meeting.id},
        settings=settings,
    )
    db.commit()
    db.refresh(action)
    return action_response(
        action,
        MeetingView(
            db,
            meeting=meeting,
            policy=view.policy,
            settings=settings,
            now=datetime.now(UTC),
            persist=True,
            actor_user_id=context.actor.user.id,
        ),
    )


def _action_or_404(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    action_id: str,
) -> ActionItem:
    action = db.scalar(
        select(ActionItem).where(
            ActionItem.id == action_id,
            ActionItem.workspace_id == workspace_id,
            ActionItem.meeting_id == meeting_id,
        )
    )
    if action is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "action_not_found", "message": "Action item not found"},
        )
    return action


@router.patch(
    "/meetings/{meeting_id}/actions/{action_id}",
    response_model=ActionItemResponse,
)
def update_action(
    meeting_id: str,
    action_id: str,
    payload: ActionItemUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ActionItemResponse:
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    action = _action_or_404(
        db,
        workspace_id=context.workspace.id,
        meeting_id=meeting_id,
        action_id=action_id,
    )
    view = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=None,
        settings=settings,
        request=request,
        surface="actions",
    )
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    fields = payload.model_fields_set
    # The stored text tells which shown placeholder was a fragment (HOM-284).
    if "description" in fields and payload.description is not None:
        action.description = view.restore(
            payload.description.strip(), revision=current, previous=action.description
        )
    if "assignee_text" in fields:
        action.assignee_text = (
            view.restore(
                payload.assignee_text.strip(), revision=current, previous=action.assignee_text
            )
            if payload.assignee_text
            else None
        )
    if "assignee_user_id" in fields:
        _validate_assignee(
            db,
            workspace_id=context.workspace.id,
            user_id=payload.assignee_user_id,
        )
        action.assignee_user_id = payload.assignee_user_id
    if "due_at" in fields:
        action.due_at = payload.due_at
    if payload.priority is not None:
        action.priority = payload.priority
    if payload.status is not None:
        action.status = payload.status
    record_audit_event(
        db,
        event_type=AuditEventType.ACTION_ITEM_UPDATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="action_item",
        target_id=action.id,
        request_id=getattr(request.state, "request_id", None),
        details={"fields": ",".join(sorted(fields))},
        settings=settings,
    )
    db.commit()
    db.refresh(action)
    return action_response(
        action,
        MeetingView(
            db,
            meeting=meeting,
            policy=view.policy,
            settings=settings,
            now=datetime.now(UTC),
            persist=True,
            actor_user_id=context.actor.user.id,
        ),
    )


@router.delete(
    "/meetings/{meeting_id}/actions/{action_id}",
    status_code=204,
    response_class=Response,
)
def delete_action(
    meeting_id: str,
    action_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    _meeting_or_404(db, context=context, meeting_id=meeting_id)
    action = _action_or_404(
        db,
        workspace_id=context.workspace.id,
        meeting_id=meeting_id,
        action_id=action_id,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.ACTION_ITEM_DELETED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="action_item",
        target_id=action.id,
        request_id=getattr(request.state, "request_id", None),
        details={"meeting_id": meeting_id},
        settings=settings,
    )
    db.delete(action)
    db.commit()
    return Response(status_code=204)


@router.patch(
    "/meetings/{meeting_id}/speakers/{speaker_id}",
    response_model=SpeakerResponse,
)
def update_speaker(
    meeting_id: str,
    speaker_id: str,
    payload: SpeakerUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.MEETING_UPDATE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SpeakerResponse:
    _meeting_or_404(db, context=context, meeting_id=meeting_id)
    speaker = db.scalar(
        select(MeetingSpeaker).where(
            MeetingSpeaker.id == speaker_id,
            MeetingSpeaker.workspace_id == context.workspace.id,
            MeetingSpeaker.meeting_id == meeting_id,
        )
    )
    if speaker is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "speaker_not_found", "message": "Speaker not found"},
        )
    speaker.display_name = payload.display_name
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_SPEAKER_RENAMED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="meeting_speaker",
        target_id=speaker.id,
        request_id=getattr(request.state, "request_id", None),
        settings=settings,
    )
    db.commit()
    db.refresh(speaker)
    return SpeakerResponse.model_validate(speaker)


def _like_pattern(query: str) -> str:
    escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, needle: str, *, max_length: int = 240) -> str:
    normalized = " ".join(text.split())
    index = normalized.casefold().find(needle.casefold())
    if index < 0:
        return normalized[:max_length]
    start = max(0, index - 70)
    end = min(len(normalized), start + max_length)
    return normalized[start:end]


def _search_matches(
    db: Session,
    *,
    meeting: Meeting,
    query: str,
    pattern: str,
) -> list[SearchMatchResponse]:
    matches: list[SearchMatchResponse] = []
    if query.casefold() in meeting.title.casefold():
        matches.append(SearchMatchResponse(kind="title", snippet=meeting.title))
    segment = db.scalar(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.workspace_id == meeting.workspace_id,
            TranscriptSegment.meeting_id == meeting.id,
            current_segments_clause(meeting.workspace_id),
            func.lower(TranscriptSegment.text).like(pattern, escape="\\"),
        )
        .order_by(TranscriptSegment.sequence.asc())
        .limit(1)
    )
    if segment is not None:
        matches.append(
            SearchMatchResponse(
                kind="transcript",
                snippet=_snippet(segment.text, query),
                timestamp_ms=segment.start_ms,
            )
        )
    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == meeting.workspace_id,
            MeetingSummary.meeting_id == meeting.id,
            or_(
                func.lower(MeetingSummary.overview).like(pattern, escape="\\"),
                func.lower(cast(MeetingSummary.topics, String)).like(pattern, escape="\\"),
            ),
        )
    )
    if summary is not None:
        matches.append(
            SearchMatchResponse(kind="summary", snippet=_snippet(summary.overview, query))
        )
    action = db.scalar(
        select(ActionItem)
        .where(
            ActionItem.workspace_id == meeting.workspace_id,
            ActionItem.meeting_id == meeting.id,
            func.lower(ActionItem.description).like(pattern, escape="\\"),
        )
        .order_by(ActionItem.created_at.asc())
        .limit(1)
    )
    if action is not None:
        matches.append(
            SearchMatchResponse(
                kind="action",
                snippet=_snippet(action.description, query),
                timestamp_ms=action.source_timestamp_ms,
            )
        )
    return matches[:4]


def _redacted_search_matches(
    db: Session,
    *,
    meeting: Meeting,
    query: str,
    view: MeetingView,
) -> tuple[list[SearchMatchResponse], bool]:
    """Matches computed on the redacted texts only (WP07-T06).

    The database candidate query also looks at original texts; a meeting is
    kept only when the query occurs in what the caller may read (title,
    speaker names, redacted transcript, summary and actions), so a search for
    a redacted value is not an oracle for it.  Returns the matches and whether
    a speaker name matched.
    """
    needle = query.casefold()
    matches: list[SearchMatchResponse] = []
    if needle in meeting.title.casefold():
        matches.append(SearchMatchResponse(kind="title", snippet=meeting.title))
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    if current is not None:
        for segment in segments_of(db, current):
            text = view.segment_text(segment, current)
            if needle in text.casefold():
                matches.append(
                    SearchMatchResponse(
                        kind="transcript",
                        snippet=_snippet(text, query),
                        timestamp_ms=segment.start_ms,
                    )
                )
                break
    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == meeting.workspace_id,
            MeetingSummary.meeting_id == meeting.id,
        )
    )
    if summary is not None:
        overview = view.text(summary.overview) or ""
        topics = " ".join(view.texts([str(topic) for topic in summary.topics or []]))
        if needle in overview.casefold() or needle in topics.casefold():
            matches.append(SearchMatchResponse(kind="summary", snippet=_snippet(overview, query)))
    for action in db.scalars(
        select(ActionItem)
        .where(
            ActionItem.workspace_id == meeting.workspace_id,
            ActionItem.meeting_id == meeting.id,
        )
        .order_by(ActionItem.created_at.asc())
    ).all():
        description = view.text(action.description) or ""
        if needle in description.casefold():
            matches.append(
                SearchMatchResponse(
                    kind="action",
                    snippet=_snippet(description, query),
                    timestamp_ms=action.source_timestamp_ms,
                )
            )
            break
    speaker_match = any(
        needle in (name or "").casefold()
        for name in db.scalars(
            select(MeetingSpeaker.display_name).where(
                MeetingSpeaker.workspace_id == meeting.workspace_id,
                MeetingSpeaker.meeting_id == meeting.id,
            )
        )
    )
    return matches[:4], speaker_match


@router.get("/search", response_model=SearchResponse)
def search_meetings(
    q: str = Query(min_length=2, max_length=160),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=50),
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    query_text = q.strip()
    if len(query_text) < 2:
        raise HTTPException(
            status_code=422,
            detail={"code": "search_query_too_short", "message": "Search query is too short"},
        )
    pattern = _like_pattern(query_text)
    query = select(Meeting).where(
        Meeting.workspace_id == context.workspace.id,
        Meeting.deleted_at.is_(None),
        context.record_access.meeting_clause(),
        or_(
            func.lower(Meeting.title).like(pattern, escape="\\"),
            exists(
                select(TranscriptSegment.id).where(
                    TranscriptSegment.workspace_id == context.workspace.id,
                    TranscriptSegment.meeting_id == Meeting.id,
                    current_segments_clause(context.workspace.id),
                    func.lower(TranscriptSegment.text).like(pattern, escape="\\"),
                )
            ),
            exists(
                select(MeetingSummary.id).where(
                    MeetingSummary.workspace_id == context.workspace.id,
                    MeetingSummary.meeting_id == Meeting.id,
                    or_(
                        func.lower(MeetingSummary.overview).like(pattern, escape="\\"),
                        func.lower(cast(MeetingSummary.topics, String)).like(
                            pattern,
                            escape="\\",
                        ),
                    ),
                )
            ),
            exists(
                select(ActionItem.id).where(
                    ActionItem.workspace_id == context.workspace.id,
                    ActionItem.meeting_id == Meeting.id,
                    func.lower(ActionItem.description).like(pattern, escape="\\"),
                )
            ),
            exists(
                select(MeetingSpeaker.id).where(
                    MeetingSpeaker.workspace_id == context.workspace.id,
                    MeetingSpeaker.meeting_id == Meeting.id,
                    func.lower(MeetingSpeaker.display_name).like(pattern, escape="\\"),
                )
            ),
        ),
    )
    if created_after is not None:
        query = query.where(Meeting.created_at >= created_after)
    if created_before is not None:
        query = query.where(Meeting.created_at <= created_before)
    # Search always uses the default view: redacted under an enforced policy,
    # for every caller (no original-text search there, WP07-T06).
    policy = choose_view(context.workspace, requested=None, may_read_original=False)
    if policy.redacted:
        candidates = list(
            db.scalars(query.order_by(Meeting.created_at.desc()).limit(min(limit * 4, 200)))
        )
        items: list[SearchMeetingResponse] = []
        for meeting in candidates:
            view = MeetingView(
                db,
                meeting=meeting,
                policy=policy,
                settings=settings,
                now=datetime.now(UTC),
                persist=False,
            )
            matches, speaker_match = _redacted_search_matches(
                db, meeting=meeting, query=query_text, view=view
            )
            if not matches and not speaker_match:
                continue
            items.append(
                SearchMeetingResponse(
                    id=meeting.id,
                    title=meeting.title,
                    status=meeting.status.value,
                    language=meeting.language,
                    duration_seconds=meeting.duration_seconds,
                    created_at=meeting.created_at,
                    matches=matches,
                )
            )
            if len(items) == limit:
                break
        return SearchResponse(query=query_text, items=items)
    meetings = list(db.scalars(query.order_by(Meeting.created_at.desc()).limit(limit)))
    return SearchResponse(
        query=query_text,
        items=[
            SearchMeetingResponse(
                id=meeting.id,
                title=meeting.title,
                status=meeting.status.value,
                language=meeting.language,
                duration_seconds=meeting.duration_seconds,
                created_at=meeting.created_at,
                matches=_search_matches(
                    db,
                    meeting=meeting,
                    query=query_text,
                    pattern=pattern,
                ),
            )
            for meeting in meetings
        ],
    )


def _export_evidence(span: EvidenceSpan, view: MeetingView) -> ExportEvidence:
    _text, text_start, text_end = view.span(
        span.segment, span.segment.revision, span.text_start, span.text_end
    )
    return ExportEvidence(
        id=span.id,
        transcript_revision_id=span.transcript_revision_id,
        segment_id=span.segment_id,
        text_start=text_start,
        text_end=text_end,
        start_ms=span.start_ms,
        end_ms=span.end_ms,
        evidence_type=span.evidence_type,
    )


def _export_action(action: ActionItem, view: MeetingView) -> ExportAction:
    return ExportAction(
        description=view.text(action.description) or "",
        assignee=view.text(action.assignee_text),
        due_at=action.due_at,
        priority=action.priority,
        status=action.status.value,
        source_timestamp_ms=action.source_timestamp_ms,
        evidence_status=action.evidence_status,
        evidence=[_export_evidence(span, view) for span in action.evidence],
    )


def export_privacy(
    db: Session, *, meeting: Meeting, view: MeetingView, transcript: TranscriptResponse
) -> dict[str, object]:
    """Privacy provenance of an export: the view, the redacted version and
    what the current analysis read (WP07-T06).  Never a value."""
    revision = (
        revision_by_id(
            db,
            workspace_id=meeting.workspace_id,
            meeting_id=meeting.id,
            revision_id=transcript.revision.id,
        )
        if transcript.revision is not None
        else None
    )
    provenance = view.provenance(revision)
    run = current_analysis_run(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    provenance["analysis_input_view"] = run.input_view if run is not None else None
    provenance["analysis_input_policy_digest"] = (
        run.input_policy_digest if run is not None else None
    )
    return provenance


def _export_revision(transcript: TranscriptResponse) -> ExportRevision | None:
    revision = transcript.revision
    if revision is None:
        return None
    return ExportRevision(
        id=revision.id,
        revision_number=revision.revision_number,
        source=revision.source,
        created_at=revision.created_at,
        asr_provider=revision.asr_provider,
        asr_model=revision.asr_model,
        is_mock=revision.is_mock,
    )


@router.get("/meetings/{meeting_id}/export")
def export_meeting(
    meeting_id: str,
    request: Request,
    format_name: Literal["txt", "json", "srt", "vtt"] = Query(alias="format"),
    view: ViewParam | None = _VIEW_QUERY,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    meeting = _meeting_or_404(
        db,
        context=context,
        meeting_id=meeting_id,
    )
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="export",
    )
    transcript = _transcript_response(db, meeting=meeting, view=meeting_view_)
    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == context.workspace.id,
            MeetingSummary.meeting_id == meeting.id,
        )
    )
    actions = list(
        db.scalars(
            select(ActionItem)
            .where(
                ActionItem.workspace_id == context.workspace.id,
                ActionItem.meeting_id == meeting.id,
            )
            .order_by(ActionItem.created_at.asc())
        )
    )
    rendered = render_meeting_export(
        MeetingExport(
            meeting_id=meeting.id,
            title=meeting.title,
            language=meeting.language,
            duration_seconds=meeting.duration_seconds,
            created_at=meeting.created_at,
            summary=meeting_view_.text(summary.overview) if summary else None,
            topics=meeting_view_.texts(list(summary.topics or [])) if summary else [],
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
            actions=[_export_action(action, meeting_view_) for action in actions],
            transcript_revision=_export_revision(transcript),
            summary_evidence_status=summary.evidence_status if summary else None,
            quality=_quality(db, meeting, settings=settings).model_dump(),
            privacy=export_privacy(db, meeting=meeting, view=meeting_view_, transcript=transcript),
        ),
        format_name,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_EXPORTED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="meeting",
        target_id=meeting.id,
        request_id=getattr(request.state, "request_id", None),
        details={"format": format_name, "view": meeting_view_.policy.view},
        settings=settings,
    )
    db.commit()
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="homayar-{meeting.id}.{rendered.extension}"'
            )
        },
    )


@router.post(
    "/meetings/{meeting_id}/transcript/redactions/{redaction_id}/reveal",
    response_model=PiiRevealResponse,
)
def reveal_redacted_value(
    meeting_id: str,
    redaction_id: str,
    payload: PiiRevealRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.PII_ORIGINAL_ACCESS)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PiiRevealResponse:
    """Decrypt one redacted value (WP07-T06): owner only, reason required,
    audited as ``pii.revealed`` without the value, never cached."""
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    redaction = db.scalar(
        select(TranscriptRedaction).where(
            TranscriptRedaction.id == redaction_id,
            TranscriptRedaction.workspace_id == meeting.workspace_id,
            TranscriptRedaction.meeting_id == meeting.id,
        )
    )
    if redaction is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "redaction_not_found", "message": "Redacted version not found"},
        )
    try:
        value = reveal_placeholder(
            db,
            redaction=redaction,
            placeholder=payload.placeholder,
            actor_user_id=context.actor.user.id,
            reason=payload.reason,
            settings=settings,
            request_id=getattr(request.state, "request_id", None),
        )
    except RedactionError as exc:
        db.rollback()
        status_code = 404 if exc.code == "mapping_not_retained" else 409
        raise HTTPException(
            status_code=status_code, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    db.commit()
    return PiiRevealResponse(
        redaction_id=redaction.id, placeholder=payload.placeholder, value=value
    )
