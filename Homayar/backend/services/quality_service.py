"""Quality, coverage and degradation states of a meeting's results (WP04-T06).

One assessment, derived only from persisted facts, feeds the API, the shared
view, the exports and the completion audit, so every surface says the same
thing.  Nothing here fabricates a better state than the data supports:
transcription is *unavailable* when no text exists, speaker attribution is at
most *inferred* until a person verifies it (WP14), analysis is *stale* when
the current transcript is newer than the one it read, and evidence follows
the per-finding statuses of WP04-T03.  The recompute status (HOM-63, ADR 0037)
is derived the same way: a stale analysis is *pending* until its revision has
``recompute_max_attempts`` failed recompute runs, then *failed*.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    EvidenceStatus,
    Meeting,
    MeetingSpeaker,
    MeetingSummary,
    ProcessingJob,
    ProcessingStageRun,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    UsageReservation,
)
from backend.services.transcript_service import current_analysis_run, current_revision


class TranscriptionState(str, enum.Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SpeakerAttributionState(str, enum.Enum):
    VERIFIED = "verified"  # reserved: a person confirmed the mapping (WP14)
    INFERRED = "inferred"  # diarization assigned labels; nobody verified them
    UNKNOWN = "unknown"


class AnalysisState(str, enum.Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class RecomputeStatus(str, enum.Enum):
    UP_TO_DATE = "up_to_date"  # the analysis read the current revision
    PENDING = "pending"  # stale; a recompute is due (or running)
    FAILED = "failed"  # stale; the failed recomputes reached the cap


SETTLED_STAGE_STATES: frozenset[StageState] = frozenset(
    {StageState.COMPLETED, StageState.RUNNING, StageState.QUEUED}
)


@dataclass(frozen=True)
class Degradation:
    stage: str
    state: str
    reason: str | None


@dataclass(frozen=True)
class UsageState:
    state: str
    audio_seconds: int | None


@dataclass(frozen=True)
class MeetingQuality:
    transcription: TranscriptionState
    speaker_attribution: SpeakerAttributionState
    analysis: AnalysisState
    evidence: EvidenceStatus
    analysis_stale: bool
    transcript_revision_id: str | None
    analysed_revision_id: str | None
    analysis_run_id: str | None
    segment_count: int
    speaker_count: int
    speech_coverage_ratio: float | None
    is_mock: bool
    degradations: list[Degradation] = field(default_factory=list)
    usage: UsageState | None = None
    recompute_status: RecomputeStatus = RecomputeStatus.UP_TO_DATE
    recompute_failure_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "transcription",
            "speaker_attribution",
            "analysis",
            "evidence",
            "recompute_status",
        ):
            payload[key] = getattr(self, key).value
        return payload

    def audit_details(self) -> dict[str, Any]:
        """Flat, text-free states for the completion audit event.

        The audit sanitizer redacts any key containing ``transcript`` (such a
        key could carry text), so the transcription state travels as
        ``asr_state``.
        """
        return {
            "asr_state": self.transcription.value,
            "speaker_state": self.speaker_attribution.value,
            "analysis_state": self.analysis.value,
            "evidence_state": self.evidence.value,
            "speech_coverage_ratio": self.speech_coverage_ratio,
        }


def aggregate_evidence(statuses: Iterable[str | EvidenceStatus]) -> EvidenceStatus:
    values = [EvidenceStatus(str(getattr(s, "value", s))) for s in statuses]
    if not values:
        return EvidenceStatus.INSUFFICIENT
    if all(value == EvidenceStatus.SUFFICIENT for value in values):
        return EvidenceStatus.SUFFICIENT
    if any(value != EvidenceStatus.INSUFFICIENT for value in values):
        return EvidenceStatus.PARTIAL
    return EvidenceStatus.INSUFFICIENT


def speech_coverage(segments: Sequence[tuple[int, int]], *, duration_seconds: int) -> float | None:
    if duration_seconds <= 0:
        return None
    covered = sum(max(0, end - start) for start, end in segments)
    return round(min(1.0, covered / (duration_seconds * 1000)), 4)


def assess_quality(
    *,
    revision: TranscriptRevision | None,
    run: AnalysisRun | None,
    summary_status: str | None,
    action_statuses: Iterable[str],
    stage_runs: Iterable[tuple[str, StageState, str | None]],
    segments: Sequence[tuple[int, int]],
    speaker_count: int,
    duration_seconds: int,
    usage: UsageState | None = None,
    recompute_failures: int = 0,
    recompute_failure_code: str | None = None,
    recompute_max_attempts: int | None = None,
) -> MeetingQuality:
    """Pure assessment from the parts every surface has at hand.

    ``recompute_failures`` counts the failed recompute runs of the current
    revision and ``recompute_failure_code`` is the newest one's reason; without
    ``recompute_max_attempts`` a stale analysis is never reported as failed.
    """
    stage_list = list(stage_runs)
    by_stage = {stage: state for stage, state, _reason in stage_list}
    degradations = [
        Degradation(stage=stage, state=state.value, reason=reason)
        for stage, state, reason in stage_list
        if state not in SETTLED_STAGE_STATES
    ]

    if revision is None or revision.segment_count == 0:
        transcription = TranscriptionState.UNAVAILABLE
    elif by_stage.get("asr") == StageState.PARTIAL:
        transcription = TranscriptionState.PARTIAL
    else:
        transcription = TranscriptionState.COMPLETE

    if (
        revision is not None
        and revision.segment_count
        and revision.diarization_status == "available"
    ):
        speakers = SpeakerAttributionState.INFERRED
    else:
        speakers = SpeakerAttributionState.UNKNOWN

    if run is None or run.state in {StageState.SKIPPED, StageState.UNAVAILABLE, StageState.FAILED}:
        analysis = AnalysisState.UNAVAILABLE
    elif run.state == StageState.PARTIAL:
        analysis = AnalysisState.PARTIAL
    elif run.state == StageState.COMPLETED:
        analysis = AnalysisState.COMPLETE
    else:
        analysis = AnalysisState.UNAVAILABLE

    statuses: list[str] = []
    if summary_status is not None:
        statuses.append(summary_status)
    statuses.extend(action_statuses)
    evidence = aggregate_evidence(statuses)

    stale = run is not None and revision is not None and run.transcript_revision_id != revision.id
    recompute = RecomputeStatus.UP_TO_DATE
    if stale:
        capped = recompute_max_attempts is not None and recompute_failures >= recompute_max_attempts
        recompute = RecomputeStatus.FAILED if capped else RecomputeStatus.PENDING

    return MeetingQuality(
        transcription=transcription,
        speaker_attribution=speakers,
        analysis=analysis,
        evidence=evidence,
        analysis_stale=stale,
        transcript_revision_id=revision.id if revision is not None else None,
        analysed_revision_id=run.transcript_revision_id if run is not None else None,
        analysis_run_id=run.id if run is not None else None,
        segment_count=revision.segment_count if revision is not None else 0,
        speaker_count=speaker_count,
        speech_coverage_ratio=speech_coverage(segments, duration_seconds=duration_seconds),
        is_mock=bool(
            (revision is not None and revision.is_mock) or (run is not None and run.is_mock)
        ),
        degradations=degradations,
        usage=usage,
        recompute_status=recompute,
        recompute_failure_code=(
            recompute_failure_code if recompute == RecomputeStatus.FAILED else None
        ),
    )


def failed_recomputes(
    db: Session, *, workspace_id: str, meeting_id: str, revision_id: str
) -> tuple[int, str | None]:
    """How many recomputes of ``revision_id`` failed, and the newest reason.

    A failed recompute is the only writer of a ``failed`` analysis run (the
    full job ends a failed analysis stage without one), so the count is exact.
    """
    reasons = list(
        db.scalars(
            select(AnalysisRun.reason_code)
            .where(
                AnalysisRun.workspace_id == workspace_id,
                AnalysisRun.meeting_id == meeting_id,
                AnalysisRun.transcript_revision_id == revision_id,
                AnalysisRun.state == StageState.FAILED,
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        )
    )
    return len(reasons), (reasons[0] if reasons else None)


def _latest_stage_runs(
    db: Session, *, job: ProcessingJob | None
) -> list[tuple[str, StageState, str | None]]:
    if job is None:
        return []
    rows = db.execute(
        select(ProcessingStageRun.stage, ProcessingStageRun.state, ProcessingStageRun.reason_code)
        .where(
            ProcessingStageRun.job_id == job.id,
            ProcessingStageRun.attempt == job.attempt_count,
        )
        .order_by(ProcessingStageRun.sequence.asc())
    ).all()
    return [(stage, state, reason) for stage, state, reason in rows]


def _usage_state(db: Session, *, job: ProcessingJob | None) -> UsageState | None:
    if job is None:
        return None
    reservation = db.get(UsageReservation, job.reservation_id)
    if reservation is None:
        return None
    return UsageState(state=reservation.state.value, audio_seconds=reservation.committed_value)


def assess_meeting(
    db: Session,
    *,
    meeting: Meeting,
    job: ProcessingJob | None,
    recompute_max_attempts: int | None = None,
) -> MeetingQuality:
    """Assessment from persisted rows (API, shared view, exports)."""
    revision = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    run = current_analysis_run(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    failures, failure_code = 0, None
    if revision is not None and run is not None and run.transcript_revision_id != revision.id:
        failures, failure_code = failed_recomputes(
            db, workspace_id=meeting.workspace_id, meeting_id=meeting.id, revision_id=revision.id
        )
    if recompute_max_attempts is None:
        recompute_max_attempts = get_settings().recompute_max_attempts
    summary_status = db.scalar(
        select(MeetingSummary.evidence_status).where(
            MeetingSummary.workspace_id == meeting.workspace_id,
            MeetingSummary.meeting_id == meeting.id,
        )
    )
    action_statuses = list(
        db.scalars(
            select(ActionItem.evidence_status).where(
                ActionItem.workspace_id == meeting.workspace_id,
                ActionItem.meeting_id == meeting.id,
            )
        )
    )
    segments: list[tuple[int, int]] = []
    if revision is not None:
        segments = [
            (start, end)
            for start, end in db.execute(
                select(TranscriptSegment.start_ms, TranscriptSegment.end_ms).where(
                    TranscriptSegment.revision_id == revision.id
                )
            ).all()
        ]
    speaker_count = db.scalar(
        select(func.count(MeetingSpeaker.id)).where(
            MeetingSpeaker.workspace_id == meeting.workspace_id,
            MeetingSpeaker.meeting_id == meeting.id,
        )
    )
    return assess_quality(
        revision=revision,
        run=run,
        summary_status=summary_status,
        action_statuses=action_statuses,
        stage_runs=_latest_stage_runs(db, job=job),
        segments=segments,
        speaker_count=int(speaker_count or 0),
        duration_seconds=meeting.duration_seconds,
        usage=_usage_state(db, job=job),
        recompute_failures=failures,
        recompute_failure_code=failure_code,
        recompute_max_attempts=recompute_max_attempts,
    )
