"""The primary call report (HOM-59, WP12-T02): one read of one meeting.

Summary and outcome first, then the QA result with its evidence, then the
review actions the caller can take, and what is missing.  Every text is in
the caller's view (original or redacted, ADR 0024), exactly as the
``/summary``, ``/actions`` and ``/evidence`` endpoints render it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.schemas.insights import ContentRedactionInfo
from backend.schemas.meetings import MeetingQualityResponse, ReportCompleteness

EvidenceStatusLiteral = Literal["sufficient", "partial", "insufficient"]
OutcomeState = Literal["explicit", "inferred", "not_stated", "unavailable"]
FindingType = Literal["summary", "insight", "action_item"]


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


class ReportDecision(BaseModel):
    text: str
    # The key point this decision is (decisions are the key points of
    # category "decision"); null when no key point matches.
    key_point_index: int | None
    confidence: float | None
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]


class ReportOutcome(BaseModel):
    state: OutcomeState
    decisions: list[ReportDecision]


class ReportFinding(BaseModel):
    finding_type: FindingType
    finding_id: str | None  # the action item id; null for the summary and insights
    finding_index: int | None  # the key point index; null for the summary and actions
    category: str | None  # insights only
    text: str
    evidence_status: EvidenceStatusLiteral
    supported: bool
    evidence: list[ReportEvidenceRef]


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
    view: Literal["original", "redacted"] = "original"
    privacy_enforced: bool = False
    redaction: ContentRedactionInfo | None = None
