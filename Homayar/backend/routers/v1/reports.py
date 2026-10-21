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
from backend.providers.schemas import SUMMARY_FIELD_NAMES, ClaimBasis
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
    ReportDecisionDetail,
    ReportEvidenceRef,
    ReportFinding,
    ReportFindingCounts,
    ReportKeyMoment,
    ReportKeyPoint,
    ReportOutcome,
    ReportQA,
    ReportSummary,
    ReportSummaryField,
    ReportSummaryFields,
    ReviewActionResponse,
)
from backend.security.context import Permission, WorkspaceContext, require_permission
from backend.services.analysis_claims import (
    SECTION_DECISION,
    SECTION_KEY_MOMENT,
    SECTION_SUMMARY_FIELD,
    STRUCTURED_SUMMARY_SCHEMA_VERSION,
    claim_section,
)
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


_BASES = frozenset(basis.value for basis in ClaimBasis)
_UNAVAILABLE_DETAIL = ReportDecisionDetail(status="unavailable", value=None)


def _point_refs(
    point: object,
    spans: dict[str, EvidenceSpan],
    render,
    finding_type: str = FINDING_INSIGHT,
) -> list[ReportEvidenceRef]:
    """The citations a key point names that the evidence table holds as spans
    of ``finding_type`` of this meeting; any other id is dropped, never invented."""
    ids = point.get("evidence_ids") if isinstance(point, dict) else None
    refs = []
    for evidence_id in ids or []:
        span = spans.get(str(evidence_id))
        if span is not None and span.finding_type == finding_type:
            refs.append(render(span))
    return refs


def _text(point: object, key: str) -> str | None:
    value = point.get(key) if isinstance(point, dict) else None
    return value if isinstance(value, str) else None


def _confidence(point: object) -> float | None:
    value = point.get("confidence") if isinstance(point, dict) else None
    return float(value) if isinstance(value, int | float) else None


def _basis(point: object) -> str | None:
    value = point.get("basis") if isinstance(point, dict) else None
    return value if value in _BASES else None


def _detail(point: object, key: str) -> ReportDecisionDetail:
    """A structured decision's owner or due date; anything malformed is
    ``unavailable``, never upgraded to stated."""
    detail = point.get(key) if isinstance(point, dict) else None
    status = detail.get("status") if isinstance(detail, dict) else None
    value = detail.get("value") if isinstance(detail, dict) else None
    if status == "stated" and isinstance(value, str):
        return ReportDecisionDetail(status="stated", value=value)
    if status == "not_stated":
        return ReportDecisionDetail(status="not_stated", value=None)
    return _UNAVAILABLE_DETAIL


def _empty_field(status: str) -> ReportSummaryField:
    return ReportSummaryField(
        status=status, text=None, basis=None, evidence_status=None, supported=None, evidence=[]
    )


def _summary_parts(
    db: Session, *, meeting: Meeting, summary: MeetingSummary, view: MeetingView, render
) -> tuple[ReportSummary, list[ReportDecision], list[ReportFinding], list[str | None]]:
    """The summary, the decisions, the findings, and each decision's basis as
    the outcome rule reads it (``None`` only for a legacy decision)."""
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
    # The overview's own citations; summary field spans carry a finding_index.
    overview_refs = [
        render(span)
        for span in spans.values()
        if span.finding_type == FINDING_SUMMARY
        and span.finding_index is None
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
            section="overview",
            basis=None,
        )
    ]
    structured = summary.schema_version >= STRUCTURED_SUMMARY_SCHEMA_VERSION
    key_points: list[ReportKeyPoint] = []
    fields: dict[str, ReportSummaryField] = {}
    moments: list[ReportKeyMoment] = []
    raw_points = list(summary.key_points or [])
    # Key points hold the insights and, for a structured summary, the tagged
    # claims (HOM-61); the stored position is the evidence finding_index.
    for index, raw in enumerate(raw_points):
        point = view.point(raw)
        section = claim_section(raw)
        if section is None:
            refs = _point_refs(raw, spans, render)
            status = evidence_status_of(
                raw.get("evidence_status") if isinstance(raw, dict) else None
            )
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
                    section="insight",
                    basis=None,
                )
            )
            continue
        if not structured:
            continue
        status = evidence_status_of(raw.get("evidence_status"))
        if section == SECTION_SUMMARY_FIELD:
            name = raw.get("category")
            if name not in SUMMARY_FIELD_NAMES or name in fields:
                continue
            if raw.get("status") != "stated":
                fields[name] = _empty_field(
                    "not_stated" if raw.get("status") == "not_stated" else "unavailable"
                )
                continue
            refs = _point_refs(raw, spans, render, FINDING_SUMMARY)
            fields[name] = ReportSummaryField(
                status="stated",
                text=_text(point, "description") or "",
                basis=_basis(raw),
                evidence_status=status,
                supported=is_supported(status, len(refs)),
                evidence=refs,
            )
            findings.append(
                ReportFinding(
                    finding_type=FINDING_SUMMARY,
                    finding_id=None,
                    finding_index=index,
                    category=name,
                    text=fields[name].text or "",
                    evidence_status=status,
                    supported=bool(fields[name].supported),
                    evidence=refs,
                    section="summary_field",
                    basis=fields[name].basis,
                )
            )
        elif section in (SECTION_DECISION, SECTION_KEY_MOMENT):
            refs = _point_refs(raw, spans, render)
            supported = is_supported(status, len(refs))
            text = _text(point, "description") or ""
            if section == SECTION_KEY_MOMENT:
                first = refs[0] if refs else None
                moments.append(
                    ReportKeyMoment(
                        index=index,
                        kind=_text(point, "category") or "",
                        text=text,
                        evidence_status=status,
                        supported=supported,
                        evidence=refs,
                        start_ms=first.start_ms if first is not None else None,
                        speaker_key=first.speaker_key if first is not None else None,
                    )
                )
            findings.append(
                ReportFinding(
                    finding_type=FINDING_INSIGHT,
                    finding_id=None,
                    finding_index=index,
                    category=_text(point, "category"),
                    text=text,
                    evidence_status=status,
                    supported=supported,
                    evidence=refs,
                    section=section,
                    basis=_basis(raw) if section == SECTION_DECISION else None,
                )
            )
    decisions: list[ReportDecision] = []
    outcome_basis: list[str | None] = []
    # A decision key point (legacy, or labeled "decision" in a structured
    # result) is copied in order: the n-th one is the next equal key point
    # after the previous match, so two identical decisions get their own
    # positions.  A structured decision (HOM-61) is not a visible key point.
    cursor = 0
    for raw, point in zip(summary.decisions or [], rendered.decisions, strict=True):
        refs = _point_refs(raw, spans, render)
        status = evidence_status_of(raw.get("evidence_status") if isinstance(raw, dict) else None)
        is_claim = structured and claim_section(raw) == SECTION_DECISION
        if is_claim:
            basis = _basis(raw)
            # A structured decision with an unreadable basis is never upgraded.
            outcome_basis.append(basis or "inferred")
        elif structured:
            # An insight labeled "decision" in a structured result: the model
            # did not mark it explicit, so it is an inferred decision.
            basis = "inferred"
            outcome_basis.append(basis)
        else:
            basis = None  # a legacy decision keeps the legacy rule
            outcome_basis.append(None)
        key_point_index = None
        if not is_claim:
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
                basis=basis,
                owner=_detail(point, "owner") if is_claim else _UNAVAILABLE_DETAIL,
                due_date=_detail(point, "due_date") if is_claim else _UNAVAILABLE_DETAIL,
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
        fields=ReportSummaryFields(
            **{name: fields.get(name, _empty_field("unavailable")) for name in SUMMARY_FIELD_NAMES}
        ),
        key_moments_status="available" if structured else "unavailable",
        key_moments=moments,
    )
    return report_summary, decisions, findings, outcome_basis


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
                section="action_item",
                basis=None,
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
    outcome_basis: list[str | None] = []
    if summary is not None:
        report_summary, decisions, findings, outcome_basis = _summary_parts(
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
                decisions_basis=outcome_basis,
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
