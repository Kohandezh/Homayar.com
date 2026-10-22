"""Evidence spans: findings bound to immutable transcript text and audio (WP04-T03).

Every AI finding (summary overview, key point / decision, action item)
references the turns the provider cited.  A reference is *valid* only when it
names a segment of the revision the analysis actually read; anything else is
rejected — counted, never silently repaired or guessed.  Each valid reference
becomes an ``EvidenceSpan`` row: revision, segment, speaker, character range
and the segment's original audio offsets (whole-segment spans until word
alignment exists, WP10).  A finding's ``evidence_status`` says how well it is
grounded; a person's action item gets a ``human`` span when it names a
segment of the current revision.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    AnalysisRun,
    EvidenceSpan,
    EvidenceStatus,
    Meeting,
    TranscriptRevision,
    TranscriptSegment,
    new_id,
)

FINDING_SUMMARY = "summary"
FINDING_INSIGHT = "insight"
FINDING_ACTION_ITEM = "action_item"
FINDING_EXTRACTION = "extraction"
EVIDENCE_TURN_REFERENCE = "turn_reference"
EVIDENCE_HUMAN = "human"


@dataclass(frozen=True)
class ResolvedReferences:
    """Provider turn indexes split into segments of the analysed revision and rejects."""

    segments: tuple[TranscriptSegment, ...]
    rejected: int

    @property
    def offered(self) -> int:
        return len(self.segments) + self.rejected

    @property
    def status(self) -> EvidenceStatus:
        return evidence_status_for(valid=len(self.segments), rejected=self.rejected)


def evidence_status_for(*, valid: int, rejected: int) -> EvidenceStatus:
    if valid == 0:
        return EvidenceStatus.INSUFFICIENT
    if rejected:
        return EvidenceStatus.PARTIAL
    return EvidenceStatus.SUFFICIENT


def resolve_turn_references(
    indexes: Iterable[int], segments: Sequence[TranscriptSegment]
) -> ResolvedReferences:
    """Map turn indexes onto the segments the provider was shown, in order.

    Turn ``i`` of the request is segment ``i`` of the analysed revision (the
    processor builds the request from the revision's segments in sequence).
    Duplicates collapse to one reference; out-of-range indexes are rejected.
    """
    seen: set[int] = set()
    resolved: list[TranscriptSegment] = []
    rejected = 0
    for index in indexes:
        if index in seen:
            continue
        seen.add(index)
        if 0 <= index < len(segments):
            resolved.append(segments[index])
        else:
            rejected += 1
    return ResolvedReferences(segments=tuple(resolved), rejected=rejected)


@dataclass
class EvidencePlan:
    """Evidence rows to persist for one analysis, built before the run is recorded."""

    spans: list[EvidenceSpan] = field(default_factory=list)
    reference_count: int = 0
    rejected_reference_count: int = 0

    def cite(
        self,
        *,
        meeting: Meeting,
        revision: TranscriptRevision,
        references: ResolvedReferences,
        finding_type: str,
        now: datetime,
        finding_id: str | None = None,
        finding_index: int | None = None,
        confidence: float | None = None,
    ) -> list[str]:
        """Record the valid references of one finding; returns the new span ids."""
        self.reference_count += references.offered
        self.rejected_reference_count += references.rejected
        ids: list[str] = []
        for segment in references.segments:
            span = _span(
                meeting=meeting,
                revision=revision,
                segment=segment,
                finding_type=finding_type,
                finding_id=finding_id,
                finding_index=finding_index,
                confidence=confidence,
                evidence_type=EVIDENCE_TURN_REFERENCE,
                now=now,
            )
            self.spans.append(span)
            ids.append(span.id)
        return ids

    def attach(self, db: Session, run: AnalysisRun) -> None:
        for span in self.spans:
            span.analysis_run_id = run.id
        db.add_all(self.spans)
        db.flush()


def _span(
    *,
    meeting: Meeting,
    revision: TranscriptRevision,
    segment: TranscriptSegment,
    finding_type: str,
    finding_id: str | None,
    finding_index: int | None,
    confidence: float | None,
    evidence_type: str,
    now: datetime,
) -> EvidenceSpan:
    if segment.revision_id != revision.id:
        raise ValueError("evidence must cite a segment of the analysed revision")
    return EvidenceSpan(
        id=new_id(),
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        transcript_revision_id=revision.id,
        segment_id=segment.id,
        speaker_key=segment.speaker_key,
        finding_type=finding_type,
        finding_id=finding_id,
        finding_index=finding_index,
        text_start=0,
        text_end=len(segment.text),
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        confidence=confidence,
        evidence_type=evidence_type,
        created_at=now,
    )


def human_evidence(
    db: Session,
    *,
    meeting: Meeting,
    segment: TranscriptSegment,
    finding_type: str,
    finding_id: str,
    now: datetime,
) -> EvidenceSpan:
    """A person's citation of one segment of the current revision (no run)."""
    revision = db.get(TranscriptRevision, segment.revision_id)
    if revision is None or not revision.is_current:
        raise ValueError("human evidence must cite the current revision")
    span = _span(
        meeting=meeting,
        revision=revision,
        segment=segment,
        finding_type=finding_type,
        finding_id=finding_id,
        finding_index=None,
        confidence=None,
        evidence_type=EVIDENCE_HUMAN,
        now=now,
    )
    db.add(span)
    db.flush()
    return span


def list_evidence(
    db: Session, *, workspace_id: str, meeting_id: str, run_id: str | None
) -> list[EvidenceSpan]:
    """Spans of the current analysis run plus human spans, oldest first."""
    query = select(EvidenceSpan).where(
        EvidenceSpan.workspace_id == workspace_id,
        EvidenceSpan.meeting_id == meeting_id,
    )
    if run_id is None:
        query = query.where(EvidenceSpan.analysis_run_id.is_(None))
    else:
        query = query.where(
            (EvidenceSpan.analysis_run_id == run_id) | EvidenceSpan.analysis_run_id.is_(None)
        )
    return list(db.scalars(query.order_by(EvidenceSpan.created_at.asc(), EvidenceSpan.id.asc())))


def evidence_by_id(
    db: Session, *, workspace_id: str, meeting_id: str, evidence_id: str
) -> EvidenceSpan | None:
    return db.scalar(
        select(EvidenceSpan).where(
            EvidenceSpan.id == evidence_id,
            EvidenceSpan.workspace_id == workspace_id,
            EvidenceSpan.meeting_id == meeting_id,
        )
    )
