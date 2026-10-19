"""The primary call report (HOM-59, WP12-T02).

One read of one meeting for the console's report page: the summary and the
outcome, the QA result (the quality assessment plus every AI finding with its
evidence), the review actions the caller can take now, and what is missing.
It is built from the same helpers as ``/summary``, ``/actions``,
``/evidence`` and ``/quality``, so it can never say more than they do.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import ActionItem, EvidenceSpan, Meeting, MeetingSummary
from backend.privacy.transcript_view import MeetingView
from backend.routers.v1 import insights, shares
from backend.routers.v1.insights import (
    _VIEW_QUERY,
    ViewParam,
    _evidence_response,
    _meeting_or_404,
    _quality,
    _view_fields,
    action_response,
    meeting_view,
    summary_response,
)
from backend.schemas.meetings import MeetingQualityResponse
from backend.schemas.reports import (
    CallReportResponse,
    ReportDecision,
    ReportEvidenceRef,
    ReportFinding,
    ReportFindingCounts,
    ReportKeyPoint,
    ReportOutcome,
    ReportQA,
    ReportSummary,
    ReviewActionResponse,
)
from backend.security.context import Permission, WorkspaceContext, require_permission
from backend.services.evidence_service import (
    FINDING_ACTION_ITEM,
    FINDING_INSIGHT,
    FINDING_SUMMARY,
    list_evidence,
)
from backend.services.report_service import (
    evidence_status_of,
    finding_counts,
    is_supported,
    meeting_completeness,
    outcome_state,
    review_action_reason,
)
from backend.services.transcript_service import current_revision

router = APIRouter(tags=["meeting intelligence"])

REPORT_VERSION = 1


def endpoint_permission(endpoint: Callable[..., object]) -> Permission:
    """The permission an endpoint declares through ``require_permission`` /
    ``require_write_permission`` (the same metadata the route inventory test
    reads), so a review action can never disagree with its endpoint."""
    for parameter in inspect.signature(endpoint).parameters.values():
        dependency = getattr(parameter.default, "dependency", None)
        permission = getattr(dependency, "homayar_permission", None)
        if permission is not None:
            return permission
    raise LookupError(f"{endpoint.__name__} declares no permission")


@dataclass(frozen=True)
class ReviewAction:
    name: str
    endpoint: Callable[..., object]
    needs_transcript: bool = False
    needs_speakers: bool = False
    permission: Permission = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "permission", endpoint_permission(self.endpoint))


# Stable order; the console renders the list as given.
REVIEW_ACTIONS: tuple[ReviewAction, ...] = (
    ReviewAction("correct_transcript", insights.correct_transcript, needs_transcript=True),
    ReviewAction(
        "rename_speaker", insights.update_speaker, needs_transcript=True, needs_speakers=True
    ),
    ReviewAction("edit_action_items", insights.create_action),
    ReviewAction("share", shares.create_share),
    ReviewAction("export", insights.export_meeting),
)


def _ref(span: EvidenceSpan, *, current_revision_id: str | None, view: MeetingView):
    return ReportEvidenceRef.model_validate(
        _evidence_response(span, current_revision_id=current_revision_id, view=view).model_dump()
    )


def _point_refs(point: object, spans: dict[str, EvidenceSpan], render) -> list[ReportEvidenceRef]:
    """The citations a key point names that the evidence table holds as
    insight spans of this meeting; any other id is dropped, never invented."""
    ids = point.get("evidence_ids") if isinstance(point, dict) else None
    refs = []
    for evidence_id in ids or []:
        span = spans.get(str(evidence_id))
        if span is not None and span.finding_type == FINDING_INSIGHT:
            refs.append(render(span))
    return refs


def _text(point: object, key: str) -> str | None:
    value = point.get(key) if isinstance(point, dict) else None
    return value if isinstance(value, str) else None


def _confidence(point: object) -> float | None:
    value = point.get("confidence") if isinstance(point, dict) else None
    return float(value) if isinstance(value, int | float) else None


def _summary_parts(
    db: Session, *, meeting: Meeting, summary: MeetingSummary, view: MeetingView, render
) -> tuple[ReportSummary, list[ReportDecision], list[ReportFinding]]:
    rendered = summary_response(summary, view)
    spans = {
        span.id: span
        for span in list_evidence(
            db,
            workspace_id=meeting.workspace_id,
            meeting_id=meeting.id,
            run_id=summary.analysis_run_id,
        )
    }
    overview_refs = [
        render(span)
        for span in spans.values()
        if span.finding_type == FINDING_SUMMARY
        and summary.analysis_run_id is not None
        and span.analysis_run_id == summary.analysis_run_id
    ]
    overview_status = evidence_status_of(summary.evidence_status)
    findings = [
        ReportFinding(
            finding_type=FINDING_SUMMARY,
            finding_id=None,
            finding_index=None,
            category=None,
            text=rendered.overview,
            evidence_status=overview_status,
            supported=is_supported(overview_status, len(overview_refs)),
            evidence=overview_refs,
        )
    ]
    key_points: list[ReportKeyPoint] = []
    raw_points = list(summary.key_points or [])
    for index, (raw, point) in enumerate(zip(raw_points, rendered.key_points, strict=True)):
        refs = _point_refs(raw, spans, render)
        status = evidence_status_of(raw.get("evidence_status") if isinstance(raw, dict) else None)
        key_point = ReportKeyPoint(
            index=index,
            category=_text(point, "category"),
            text=_text(point, "description") or "",
            confidence=_confidence(raw),
            evidence_status=status,
            supported=is_supported(status, len(refs)),
            evidence=refs,
        )
        key_points.append(key_point)
        findings.append(
            ReportFinding(
                finding_type=FINDING_INSIGHT,
                finding_id=None,
                finding_index=index,
                category=key_point.category,
                text=key_point.text,
                evidence_status=status,
                supported=key_point.supported,
                evidence=refs,
            )
        )
    decisions: list[ReportDecision] = []
    # Decisions are the decision key points, copied in order: the n-th
    # decision is the next equal key point after the previous match, so two
    # identical decisions get their own positions.
    cursor = 0
    for raw, point in zip(summary.decisions or [], rendered.decisions, strict=True):
        refs = _point_refs(raw, spans, render)
        status = evidence_status_of(raw.get("evidence_status") if isinstance(raw, dict) else None)
        key_point_index = next(
            (i for i in range(cursor, len(raw_points)) if raw_points[i] == raw), None
        )
        if key_point_index is not None:
            cursor = key_point_index + 1
        decisions.append(
            ReportDecision(
                text=_text(point, "description") or "",
                key_point_index=key_point_index,
                confidence=_confidence(raw),
                evidence_status=status,
                supported=is_supported(status, len(refs)),
                evidence=refs,
            )
        )
    report_summary = ReportSummary(
        overview=rendered.overview,
        evidence_status=overview_status,
        supported=findings[0].supported,
        evidence=overview_refs,
        key_points=key_points,
        topics=rendered.topics,
        sentiment=rendered.sentiment,
        analysis_run_id=summary.analysis_run_id,
        provider=summary.provider,
        model=summary.model,
    )
    return report_summary, decisions, findings


def _action_findings(
    db: Session, *, meeting: Meeting, view: MeetingView, render
) -> list[ReportFinding]:
    """AI action items, in the order of ``/actions``.  A person's own action
    items are review work, not AI findings, so they are not QA findings."""
    actions = db.scalars(
        select(ActionItem)
        .where(
            ActionItem.workspace_id == meeting.workspace_id,
            ActionItem.meeting_id == meeting.id,
            ActionItem.created_by == "ai",
        )
        .order_by(ActionItem.created_at.asc())
    )
    findings = []
    for action in actions:
        refs = [render(span) for span in action.evidence]
        status = evidence_status_of(action.evidence_status)
        findings.append(
            ReportFinding(
                finding_type=FINDING_ACTION_ITEM,
                finding_id=action.id,
                finding_index=None,
                category=None,
                text=action_response(action, view).description,
                evidence_status=status,
                supported=is_supported(status, len(refs)),
                evidence=refs,
            )
        )
    return findings


def _review_actions(
    context: WorkspaceContext, quality: MeetingQualityResponse
) -> list[ReviewActionResponse]:
    responses = []
    for action in REVIEW_ACTIONS:
        reason = review_action_reason(
            permitted=context.has(action.permission),
            needs_transcript=action.needs_transcript,
            needs_speakers=action.needs_speakers,
            quality=quality,
        )
        responses.append(
            ReviewActionResponse(action=action.name, available=reason is None, reason=reason)
        )
    return responses


@router.get("/meetings/{meeting_id}/report", response_model=CallReportResponse)
def get_call_report(
    meeting_id: str,
    request: Request,
    view: ViewParam | None = _VIEW_QUERY,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CallReportResponse:
    """Summary, outcome, QA with evidence, review actions and completeness of
    one meeting, in every processing state (never 404 for a missing summary)."""
    meeting = _meeting_or_404(db, context=context, meeting_id=meeting_id)
    meeting_view_ = meeting_view(
        db,
        context=context,
        meeting=meeting,
        requested=view,
        settings=settings,
        request=request,
        surface="report",
    )
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    current_id = current.id if current is not None else None

    def render(span: EvidenceSpan) -> ReportEvidenceRef:
        return _ref(span, current_revision_id=current_id, view=meeting_view_)

    summary = db.scalar(
        select(MeetingSummary).where(
            MeetingSummary.workspace_id == context.workspace.id,
            MeetingSummary.meeting_id == meeting.id,
        )
    )
    report_summary = None
    decisions: list[ReportDecision] = []
    findings: list[ReportFinding] = []
    if summary is not None:
        report_summary, decisions, findings = _summary_parts(
            db, meeting=meeting, summary=summary, view=meeting_view_, render=render
        )
    findings.extend(_action_findings(db, meeting=meeting, view=meeting_view_, render=render))

    quality = _quality(db, meeting)
    counts = finding_counts((finding.evidence_status, finding.supported) for finding in findings)
    unsupported = counts["unsupported"]
    response = CallReportResponse(
        meeting_id=meeting.id,
        report_version=REPORT_VERSION,
        summary=report_summary,
        outcome=ReportOutcome(
            state=outcome_state(
                has_summary=summary is not None,
                decisions_supported=[decision.supported for decision in decisions],
            ),
            decisions=decisions,
        ),
        qa=ReportQA(quality=quality, findings=findings, counts=ReportFindingCounts(**counts)),
        review_actions=_review_actions(context, quality),
        completeness=meeting_completeness(
            status=meeting.status, quality=quality, unsupported=unsupported
        ),
        **_view_fields(meeting_view_),
    )
    db.commit()  # a first redacted version or an original-access audit
    return response
