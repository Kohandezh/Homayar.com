"""The primary call report (HOM-59, WP12-T02): one read of one meeting.

Summary and outcome first, then the QA result with its evidence, then the
review actions the caller can take, and what is missing.  Every text is in
the caller's view (original or redacted, ADR 0024), exactly as the
``/summary``, ``/actions`` and ``/evidence`` endpoints render it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.providers.schemas import ClaimBasis
from backend.schemas.insights import ContentRedactionInfo
from backend.schemas.meetings import MeetingQualityResponse, ReportCompleteness

EvidenceStatusLiteral = Literal["sufficient", "partial", "insufficient"]
OutcomeState = Literal["explicit", "inferred", "not_stated", "unavailable"]
FindingType = Literal["summary", "insight", "action_item"]
# HOM-61: ``unavailable`` means there is no structured analysis to read (no
# summary, or a summary written before structured claims existed).
ClaimStatusLiteral = Literal["stated", "not_stated", "unavailable"]
FindingSection = Literal[
    "overview", "summary_field", "insight", "decision", "key_moment", "action_item"
]
# HOM-53: the taxonomy extraction of the current analysis run.
ExtractionStatusLiteral = Literal["available", "invalid", "unavailable"]
ExtractionFieldStatusLiteral = Literal[
    "stated", "not_stated", "insufficient_evidence", "contradictory", "unavailable"
]
AbstainReasonLiteral = Literal["model", "contradictory", "low_confidence"]


class ReportEvidenceRef(BaseModel):
    """One citation, identical to ``GET /meetings/{id}/evidence/{id}`` for the
    same caller and view (offsets and ``text`` are in that view)."""

    id: str
    transcript_revision_id: str
    # False when the analysis read an older revision than the current one
    # (stale analysis): the segment is not in the current transcript.
    is_current_revision: bool
    segment_id: str
    speaker_key: str
    text_start: int
    text_end: int
    text: str
    start_ms: int
    end_ms: int
    evidence_type: str


class ReportKeyPoint(BaseModel):
    index: int
    category: str | None
    text: str
    confidence: float | None
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]


class ReportSummaryField(BaseModel):
    """One structured summary field.  Only a ``stated`` field has text, a
    basis and evidence; the other statuses carry nulls and no evidence."""

    status: ClaimStatusLiteral
    text: str | None
    basis: ClaimBasis | None
    evidence_status: EvidenceStatusLiteral | None
    supported: bool | None
    evidence: list[ReportEvidenceRef]


class ReportSummaryFields(BaseModel):
    purpose: ReportSummaryField
    resolution: ReportSummaryField
    next_steps: ReportSummaryField


class ReportKeyMoment(BaseModel):
    index: int  # the evidence finding_index of this moment
    kind: str
    text: str
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]
    # From the first evidence reference; null when the moment has none.
    start_ms: int | None
    speaker_key: str | None


class ReportSummary(BaseModel):
    overview: str
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]
    key_points: list[ReportKeyPoint]
    topics: list[str]
    sentiment: str | None
    analysis_run_id: str | None
    provider: str
    model: str
    fields: ReportSummaryFields
    key_moments_status: Literal["available", "unavailable"]
    key_moments: list[ReportKeyMoment]


class ReportDecisionDetail(BaseModel):
    status: ClaimStatusLiteral
    value: str | None  # the owner, or the due date as YYYY-MM-DD


class ReportDecision(BaseModel):
    text: str
    # The key point this decision is (a legacy decision is a key point of
    # category "decision"); null when no key point matches and for a
    # structured decision.
    key_point_index: int | None
    confidence: float | None
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]
    # Null for a legacy decision, or for a stored basis that cannot be read;
    # the second one never counts as explicit.
    basis: ClaimBasis | None
    owner: ReportDecisionDetail
    due_date: ReportDecisionDetail


class ReportOutcome(BaseModel):
    state: OutcomeState
    decisions: list[ReportDecision]


class ReportFinding(BaseModel):
    # ``section`` (not ``finding_type``) tells the overview and insights apart
    # from the new claims (HOM-61).  A claim's ``finding_index`` is its evidence
    # index, not a position in ``summary.key_points``.
    finding_type: FindingType
    finding_id: str | None  # the action item id; null for the summary and insights
    # The evidence finding_index (the key point index of an insight); null for
    # the overview and actions.
    finding_index: int | None
    category: str | None  # insights, decisions, key moments and summary field names
    text: str
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]
    section: FindingSection
    basis: ClaimBasis | None


class ReportFindingCounts(BaseModel):
    total: int
    supported: int
    unsupported: int
    sufficient: int
    partial: int
    insufficient: int


class ReportQA(BaseModel):
    quality: MeetingQualityResponse
    findings: list[ReportFinding]
    counts: ReportFindingCounts


class ReportExtractionValue(BaseModel):
    """One kept value (auto-accepted or for review).  A withheld value is never here."""

    index: int  # the evidence finding_index of this value
    label: str
    # From the taxonomy version the row was judged with; null when that version
    # cannot be loaded.
    label_name_en: str | None
    label_name_fa: str | None
    basis: ClaimBasis
    critical: bool
    confidence: float | None
    review: Literal["required", "not_required"]
    review_reason: Literal["critical_label"] | None
    text: str | None  # entity values only, in the caller's view
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]


class ReportExtractionField(BaseModel):
    """One extraction field as stored.  ``unavailable`` is a rejected field, or a
    field of a block that is ``unavailable``: no reason, no review, no values.
    ``insufficient_evidence`` and ``contradictory`` are not ``not_stated``."""

    status: ExtractionFieldStatusLiteral
    abstain_reason: AbstainReasonLiteral | None
    review_required: bool
    values: list[ReportExtractionValue]


class ReportExtractionFields(BaseModel):
    intent: ReportExtractionField
    topic: ReportExtractionField
    entity: ReportExtractionField
    outcome: ReportExtractionField


class ReportExtraction(BaseModel):
    """The taxonomy extraction of the current analysis run (HOM-53).

    ``available``: the stored extraction is valid.  ``invalid``: it broke a rule
    (a rejected field, or an unknown key); the fields the validator accepted or
    abstained on are still shown.  ``unavailable``: the current run has no
    extraction (no analysis, a legacy result, or an output without one); the ids
    are null and every field is ``unavailable``.  Withheld values and validator
    issues are never shown.
    """

    status: ExtractionStatusLiteral
    needs_review: bool
    review_count: int
    analysis_run_id: str | None
    taxonomy_id: str | None
    taxonomy_version: int | None
    taxonomy_digest: str | None
    validator_version: str | None
    policy_version: str | None
    policy_digest: str | None
    fields: ReportExtractionFields


class ReviewActionResponse(BaseModel):
    action: str
    available: bool
    reason: str | None


class CallReportResponse(BaseModel):
    meeting_id: str
    report_version: Literal[1] = 1
    summary: ReportSummary | None
    outcome: ReportOutcome
    qa: ReportQA
    review_actions: list[ReviewActionResponse]
    completeness: ReportCompleteness
    extraction: ReportExtraction
    view: Literal["original", "redacted"] = "original"
    privacy_enforced: bool = False
    redaction: ContentRedactionInfo | None = None
