"""Rules of the primary call report (HOM-59, WP12-T02).

Pure functions over facts the router already loaded: nothing here reads the
database or upgrades a state.  A finding is *supported* only when its evidence
status says it is grounded and at least one citation from the evidence table
backs it; an outcome is *explicit* only when a supported decision is explicit
(or a legacy decision without a basis is supported); and the
completeness notices name every reason the report is not complete.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from backend.database.saas_models import EvidenceStatus, MeetingStatus
from backend.schemas.meetings import MeetingQualityResponse, ReportCompleteness

OUTCOME_EXPLICIT = "explicit"
OUTCOME_INFERRED = "inferred"
OUTCOME_NOT_STATED = "not_stated"
OUTCOME_UNAVAILABLE = "unavailable"

REASON_PERMISSION_DENIED = "permission_denied"
REASON_NO_TRANSCRIPT = "no_transcript"
REASON_NO_SPEAKERS = "no_speakers"

NOTICE_PROCESSING_IN_PROGRESS = "processing_in_progress"
NOTICE_PROCESSING_FAILED = "processing_failed"
NOTICE_TRANSCRIPT_UNAVAILABLE = "transcript_unavailable"
NOTICE_TRANSCRIPT_PARTIAL = "transcript_partial"
NOTICE_ANALYSIS_UNAVAILABLE = "analysis_unavailable"
NOTICE_ANALYSIS_PARTIAL = "analysis_partial"
NOTICE_ANALYSIS_STALE = "analysis_stale"
NOTICE_ANALYSIS_RECOMPUTE_FAILED = "analysis_recompute_failed"
NOTICE_MOCK_ANALYSIS = "mock_analysis"
NOTICE_UNSUPPORTED_FINDINGS = "unsupported_findings"

IN_PROGRESS_STATUSES = frozenset(
    {
        MeetingStatus.UPLOADING,
        MeetingStatus.UPLOADED,
        MeetingStatus.QUEUED,
        MeetingStatus.PROCESSING,
        MeetingStatus.TRANSCRIBING,
        MeetingStatus.DIARIZING,
        MeetingStatus.ANALYZING,
    }
)
FAILED_STATUSES = frozenset({MeetingStatus.FAILED, MeetingStatus.CANCELLED})


def evidence_status_of(value: object) -> str:
    """A stored status as one of the three values; anything else (a legacy
    point without a status) is ``insufficient``, never upgraded."""
    try:
        return EvidenceStatus(str(getattr(value, "value", value))).value
    except ValueError:
        return EvidenceStatus.INSUFFICIENT.value


def is_supported(evidence_status: str, evidence_count: int) -> bool:
    return evidence_status != EvidenceStatus.INSUFFICIENT.value and evidence_count > 0


def outcome_state(
    *,
    has_summary: bool,
    decisions_supported: Sequence[bool],
    decisions_basis: Sequence[str | None] | None = None,
) -> str:
    """``explicit`` needs a supported decision whose basis is explicit (HOM-61).

    A legacy decision has no basis (``None``) and keeps the earlier rule: it
    counts as explicit when it is supported.
    """
    if not has_summary:
        return OUTCOME_UNAVAILABLE
    if not decisions_supported:
        return OUTCOME_NOT_STATED
    basis = decisions_basis if decisions_basis is not None else [None] * len(decisions_supported)
    if any(
        supported and value in (None, OUTCOME_EXPLICIT)
        for supported, value in zip(decisions_supported, basis, strict=True)
    ):
        return OUTCOME_EXPLICIT
    return OUTCOME_INFERRED


def finding_counts(findings: Iterable[tuple[str, bool]]) -> dict[str, int]:
    """Counts of ``(evidence_status, supported)`` pairs."""
    counts = {
        "total": 0,
        "supported": 0,
        "unsupported": 0,
        EvidenceStatus.SUFFICIENT.value: 0,
        EvidenceStatus.PARTIAL.value: 0,
        EvidenceStatus.INSUFFICIENT.value: 0,
    }
    for status, supported in findings:
        counts["total"] += 1
        counts["supported" if supported else "unsupported"] += 1
        counts[status] += 1
    return counts


def completeness_notices(
    *, status: MeetingStatus, quality: MeetingQualityResponse, unsupported: int
) -> list[str]:
    """Every reason the report is not complete, in a fixed order.

    Empty exactly when ``is_complete`` holds: each condition of
    ``is_report_complete`` that fails adds one code here.
    """
    notices: list[str] = []
    if status in IN_PROGRESS_STATUSES:
        notices.append(NOTICE_PROCESSING_IN_PROGRESS)
    if status in FAILED_STATUSES:
        notices.append(NOTICE_PROCESSING_FAILED)
    if quality.transcription == "unavailable":
        notices.append(NOTICE_TRANSCRIPT_UNAVAILABLE)
    if quality.transcription == "partial":
        notices.append(NOTICE_TRANSCRIPT_PARTIAL)
    if quality.analysis == "unavailable":
        notices.append(NOTICE_ANALYSIS_UNAVAILABLE)
    if quality.analysis == "partial" or status == MeetingStatus.PARTIAL:
        notices.append(NOTICE_ANALYSIS_PARTIAL)
    if quality.analysis_stale:
        notices.append(NOTICE_ANALYSIS_STALE)
    if quality.recompute_status == "failed":
        notices.append(NOTICE_ANALYSIS_RECOMPUTE_FAILED)
    if quality.is_mock:
        notices.append(NOTICE_MOCK_ANALYSIS)
    if unsupported:
        notices.append(NOTICE_UNSUPPORTED_FINDINGS)
    return notices


def is_report_complete(
    *, status: MeetingStatus, quality: MeetingQualityResponse, unsupported: int
) -> bool:
    return (
        status == MeetingStatus.COMPLETED
        and quality.transcription == "complete"
        and quality.analysis == "complete"
        and not quality.analysis_stale
        and not quality.is_mock
        and unsupported == 0
    )


def meeting_completeness(
    *, status: MeetingStatus, quality: MeetingQualityResponse, unsupported: int = 0
) -> ReportCompleteness:
    """The one completeness object of a meeting (report, transcript, playback).

    The transcript and playback descriptor carry no findings, so they pass
    ``unsupported=0`` and never show ``unsupported_findings``.
    """
    return ReportCompleteness(
        is_complete=is_report_complete(status=status, quality=quality, unsupported=unsupported),
        notices=completeness_notices(status=status, quality=quality, unsupported=unsupported),
        meeting_status=status.value,
        transcription=quality.transcription,
        speaker_attribution=quality.speaker_attribution,
        analysis=quality.analysis,
        evidence=quality.evidence,
    )


def review_action_reason(
    *,
    permitted: bool,
    needs_transcript: bool,
    needs_speakers: bool,
    quality: MeetingQualityResponse,
) -> str | None:
    """Why the caller cannot take an action now, or ``None`` when it can.

    The permission comes first, as in the endpoint (403 before any lookup).
    """
    if not permitted:
        return REASON_PERMISSION_DENIED
    if needs_transcript and quality.transcription == "unavailable":
        return REASON_NO_TRANSCRIPT
    if needs_speakers and quality.speaker_count == 0:
        return REASON_NO_SPEAKERS
    return None
