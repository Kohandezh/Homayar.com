"""Coverage counters of one workspace (HOM-72, WP14-T01): ``coverage/1``.

Four separate counters, each with its own definition, numerator and
denominator, and never one blended number.  Each counter's denominator is the
previous counter's numerator, so they reconcile by construction:

    ingestion   valid / ingested            every ledger row in the window
    processing  transcribed / valid         the accepted rows
    evaluation  fully_evaluated / transcribed
    review      reviewed / fully_evaluated  no source yet (HOM-73)

The population is the ledger (``ingestion_records``) of the workspace with
``from <= received_at < to``.  A meeting without a ledger row (legacy or demo
data) is not counted; a transient refusal writes no ledger row, so it is not
counted either.

A meeting is fully evaluated exactly when its call report is complete
(``is_report_complete``), and its evaluation bucket is the first entry of the
report's ``completeness.notices``.  The facts are loaded with a fixed number
of set-based queries (the count does not grow with the number of meetings) and
assessed with the same pure functions the report uses.

HOM-299: ``compute_population_coverage`` applies the same rules to meetings that are not
ledger rows (the voicemail rows of ``GET /voicemails/analytics``), so its counters reconcile
with the ``total`` that route shows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    EvidenceSpan,
    IngestionRecord,
    IngestionStatus,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    ProcessingStageRun,
    StageState,
    TranscriptRevision,
    TranscriptSource,
)
from backend.providers.schemas import SUMMARY_FIELD_NAMES
from backend.schemas.meetings import MeetingQualityResponse
from backend.services.analysis_claims import (
    SECTION_DECISION,
    SECTION_KEY_MOMENT,
    SECTION_SUMMARY_FIELD,
    STRUCTURED_SUMMARY_SCHEMA_VERSION,
    claim_section,
)
from backend.services.evidence_service import FINDING_ACTION_ITEM, FINDING_INSIGHT, FINDING_SUMMARY
from backend.services.quality_service import assess_quality
from backend.services.report_service import (
    FAILED_STATUSES,
    IN_PROGRESS_STATUSES,
    NOTICE_ANALYSIS_PARTIAL,
    NOTICE_ANALYSIS_RECOMPUTE_FAILED,
    NOTICE_ANALYSIS_STALE,
    NOTICE_ANALYSIS_UNAVAILABLE,
    NOTICE_MOCK_ANALYSIS,
    NOTICE_TRANSCRIPT_PARTIAL,
    NOTICE_UNSUPPORTED_FINDINGS,
    completeness_notices,
    evidence_status_of,
    is_report_complete,
    is_supported,
)

COVERAGE_VERSION = "coverage/1"
WINDOW_BASIS = "ingestion_received_at"
DEFAULT_WINDOW = timedelta(days=30)
MAX_WINDOW = timedelta(days=366)
WINDOW_INVALID = "coverage_window_invalid"

STATUS_COMPUTED = "computed"
STATUS_NOT_COMPUTABLE = "not_computable"
REASON_EMPTY_DENOMINATOR = "empty_denominator"
REASON_REVIEW_RECORDS_UNAVAILABLE = "review_records_unavailable"

BUCKET_FULLY_EVALUATED = "fully_evaluated"
BUCKET_TRANSCRIBED = "transcribed"
TRANSCRIBABLE_STATUSES = frozenset({MeetingStatus.COMPLETED, MeetingStatus.PARTIAL})

# Buckets in precedence order: a row goes to the first that matches.
INGESTION_BUCKETS = ("rejected", "accepted")
PROCESSING_BUCKETS = ("deleted", "in_progress", "failed", BUCKET_TRANSCRIBED, "no_transcript")
EVALUATION_BUCKETS = (
    BUCKET_FULLY_EVALUATED,
    NOTICE_TRANSCRIPT_PARTIAL,
    NOTICE_ANALYSIS_UNAVAILABLE,
    NOTICE_ANALYSIS_PARTIAL,
    NOTICE_ANALYSIS_STALE,
    NOTICE_ANALYSIS_RECOMPUTE_FAILED,
    NOTICE_MOCK_ANALYSIS,
    NOTICE_UNSUPPORTED_FINDINGS,
)

DEFINITIONS = {
    "ingestion": (
        "Share of ingestion events in the window that were accepted. Every ledger row "
        "received in the window counts once; a rejected event is never valid."
    ),
    "processing": (
        "Share of accepted events whose conversation has a usable transcript. It needs "
        "status completed or partial and a current transcript revision with at least one "
        "segment; deleted, in progress and failed conversations never count."
    ),
    "evaluation": (
        "Share of transcribed conversations whose call report is complete: completed "
        "status, complete transcript and analysis, analysis on the current transcript, no "
        "mock result and every finding supported by evidence."
    ),
    "review": (
        "Share of fully evaluated conversations that a person has completed a review of. "
        "The review records arrive with HOM-73, so this counter cannot be computed yet."
    ),
}
LABELS = {
    "ingestion": ("valid", "ingested"),
    "processing": ("transcribed", "valid"),
    "evaluation": ("fully_evaluated", "transcribed"),
    "review": ("reviewed", "fully_evaluated"),
}


class CoverageWindowError(ValueError):
    code = WINDOW_INVALID


@dataclass(frozen=True)
class CoverageWindow:
    """``start <= received_at < end``, both in UTC."""

    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.start, "to": self.end, "basis": WINDOW_BASIS}


def _instant(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise CoverageWindowError(f"{name} is not an ISO 8601 datetime") from exc
    else:
        raise CoverageWindowError(f"{name} must be an ISO 8601 datetime string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CoverageWindowError(f"{name} needs a timezone")
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:  # year 1 or 9999 shifted out of range by the offset
        raise CoverageWindowError(f"{name} is outside the supported date range") from exc


def parse_window(
    raw_from: object = None, raw_to: object = None, *, now: datetime | None = None
) -> CoverageWindow:
    """The window from two optional ISO 8601 values with a timezone.

    Defaults: ``to`` is now and ``from`` is 30 days before ``to``.  Refused
    (``coverage_window_invalid``): a value without a timezone, ``from >= to``,
    or a span over 366 days.  A date that cannot be converted to UTC, or a
    ``to`` that cannot be shifted back by the default 30 days, is refused too.
    """
    end = _instant(raw_to, "to") if raw_to is not None else (now or datetime.now(UTC))
    end = end.astimezone(UTC)
    if raw_from is not None:
        start = _instant(raw_from, "from")
    else:
        try:
            start = end - DEFAULT_WINDOW
        except OverflowError as exc:
            raise CoverageWindowError("to is too early for the default 30-day window") from exc
    if start >= end:
        raise CoverageWindowError("from must be before to")
    if end - start > MAX_WINDOW:
        raise CoverageWindowError("the window may span at most 366 days")
    return CoverageWindow(start=start, end=end)


@dataclass(frozen=True)
class EvaluatedMeeting:
    """A meeting of the processing ``transcribed`` bucket, with its assessment."""

    meeting_id: str
    team_id: str | None
    agent_user_id: str | None
    source: str
    language: str
    revision_source: str
    quality: MeetingQualityResponse
    bucket: str


@dataclass(frozen=True)
class Coverage:
    window: CoverageWindow
    counters: dict[str, dict[str, Any]]
    evaluated: list[EvaluatedMeeting] = field(default_factory=list)

    @property
    def fully_evaluated(self) -> list[EvaluatedMeeting]:
        return [meeting for meeting in self.evaluated if meeting.bucket == BUCKET_FULLY_EVALUATED]


def _counter(
    name: str, *, numerator: int, denominator: int, buckets: Mapping[str, int]
) -> dict[str, Any]:
    numerator_label, denominator_label = LABELS[name]
    computable = denominator > 0
    return {
        "definition": DEFINITIONS[name],
        "numerator_label": numerator_label,
        "denominator_label": denominator_label,
        "numerator": numerator if computable else 0,
        "denominator": denominator,
        "ratio": round(numerator / denominator, 4) if computable else None,
        "status": STATUS_COMPUTED if computable else STATUS_NOT_COMPUTABLE,
        "reason": None if computable else REASON_EMPTY_DENOMINATOR,
        "buckets": dict(buckets),
    }


def _review_counter(denominator: int) -> dict[str, Any]:
    """The missing review source wins over ``empty_denominator``."""
    numerator_label, denominator_label = LABELS["review"]
    return {
        "definition": DEFINITIONS["review"],
        "numerator_label": numerator_label,
        "denominator_label": denominator_label,
        "numerator": None,
        "denominator": denominator,
        "ratio": None,
        "status": STATUS_NOT_COMPUTABLE,
        "reason": REASON_REVIEW_RECORDS_UNAVAILABLE,
        "buckets": {},
    }


# --- the report's unsupported findings count -----------------------------------


def _cited_count(point: object, spans: Mapping[str, Any], finding_type: str) -> int:
    """How many citations of ``point`` name a span of ``finding_type`` that the
    report can show (``_point_refs`` of ``routers/v1/reports.py``)."""
    ids = point.get("evidence_ids") if isinstance(point, dict) else None
    count = 0
    for evidence_id in ids or []:
        span = spans.get(str(evidence_id))
        if span is not None and span.finding_type == finding_type:
            count += 1
    return count


def unsupported_finding_count(
    *,
    summary: Any | None,
    actions: Iterable[Any],
    spans: Iterable[Any],
) -> int:
    """The ``unsupported`` count of a meeting's call report, from stored rows.

    ``summary`` has ``evidence_status``, ``key_points``, ``schema_version`` and
    ``analysis_run_id``; ``actions`` are the meeting's AI action items (``id``,
    ``evidence_status``); ``spans`` are all evidence spans of the meeting (``id``,
    ``finding_type``, ``finding_index``, ``finding_id``, ``analysis_run_id``).
    It follows ``_summary_parts`` and ``_action_findings`` of
    ``routers/v1/reports.py`` finding by finding; the coverage parity tests hold
    the two together.
    """
    all_spans = list(spans)
    findings: list[tuple[str, int]] = []
    if summary is not None:
        run_id = summary.analysis_run_id
        # list_evidence: the summary run's spans plus human spans (no run)
        visible = {
            span.id: span
            for span in all_spans
            if span.analysis_run_id is None
            or (run_id is not None and span.analysis_run_id == run_id)
        }
        overview_refs = sum(
            1
            for span in visible.values()
            if span.finding_type == FINDING_SUMMARY
            and span.finding_index is None
            and run_id is not None
            and span.analysis_run_id == run_id
        )
        findings.append((evidence_status_of(summary.evidence_status), overview_refs))
        structured = summary.schema_version >= STRUCTURED_SUMMARY_SCHEMA_VERSION
        fields: set[str] = set()
        for raw in list(summary.key_points or []):
            section = claim_section(raw)
            if section is None:
                status = evidence_status_of(
                    raw.get("evidence_status") if isinstance(raw, dict) else None
                )
                findings.append((status, _cited_count(raw, visible, FINDING_INSIGHT)))
                continue
            if not structured:
                continue
            status = evidence_status_of(raw.get("evidence_status"))
            if section == SECTION_SUMMARY_FIELD:
                name = raw.get("category")
                if name not in SUMMARY_FIELD_NAMES or name in fields:
                    continue
                fields.add(name)
                if raw.get("status") != "stated":
                    continue
                findings.append((status, _cited_count(raw, visible, FINDING_SUMMARY)))
            elif section in (SECTION_DECISION, SECTION_KEY_MOMENT):
                findings.append((status, _cited_count(raw, visible, FINDING_INSIGHT)))
    by_action: Counter[str] = Counter(
        span.finding_id
        for span in all_spans
        if span.finding_type == FINDING_ACTION_ITEM and span.finding_id is not None
    )
    for action in actions:
        findings.append((evidence_status_of(action.evidence_status), by_action[action.id]))
    return sum(1 for status, cited in findings if not is_supported(status, cited))


# --- set-based loading ---------------------------------------------------------


def _ledger_rows(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    population: Sequence[ColumnElement[bool]] = (),
) -> Sequence[Any]:
    """The window's ledger rows.  With ``population`` only the rows that join to a meeting
    of the workspace and pass every clause: a row with no meeting cannot be attributed."""
    meeting_of_row = and_(
        Meeting.id == IngestionRecord.meeting_id,
        Meeting.workspace_id == workspace_id,
    )
    statement = (
        select(
            IngestionRecord.status,
            IngestionRecord.reason,
            Meeting.id.label("meeting_id"),
            Meeting.status.label("meeting_status"),
            Meeting.deleted_at,
            Meeting.team_id,
            Meeting.agent_user_id,
            Meeting.source,
            Meeting.language,
        )
        .select_from(IngestionRecord)
        .where(
            IngestionRecord.workspace_id == workspace_id,
            IngestionRecord.received_at >= window.start,
            IngestionRecord.received_at < window.end,
            *population,
        )
    )
    if population:
        statement = statement.join(Meeting, meeting_of_row)
    else:
        statement = statement.outerjoin(Meeting, meeting_of_row)
    return db.execute(statement).all()


def transcribable_clauses(workspace_id: str) -> tuple[ColumnElement[bool], ...]:
    """An accepted ledger row of the workspace whose live meeting is in a transcribable
    status, with no window.  For a select that joins ``IngestionRecord`` to ``Meeting`` on
    ``IngestionRecord.meeting_id``.  The trend population (HOM-80) shares it."""
    return (
        IngestionRecord.workspace_id == workspace_id,
        IngestionRecord.status == IngestionStatus.ACCEPTED,
        Meeting.workspace_id == workspace_id,
        Meeting.deleted_at.is_(None),
        Meeting.status.in_(sorted(TRANSCRIBABLE_STATUSES, key=lambda status: status.value)),
    )


def has_segments(segment_count: int | None) -> bool:
    """The current transcript revision has at least one segment: a transcribable
    meeting with it is in the processing bucket ``transcribed``."""
    return segment_count is not None and segment_count > 0


def _candidate_meetings(
    workspace_id: str, window: CoverageWindow, population: Sequence[ColumnElement[bool]] = ()
):
    """Accepted, live meetings of the window in a transcribable status that pass every
    ``population`` clause (a subquery)."""
    return (
        select(Meeting.id)
        .join(IngestionRecord, IngestionRecord.meeting_id == Meeting.id)
        .where(
            *transcribable_clauses(workspace_id),
            IngestionRecord.received_at >= window.start,
            IngestionRecord.received_at < window.end,
            *population,
        )
    )


@dataclass
class _Facts:
    revisions: dict[str, TranscriptRevision] = field(default_factory=dict)
    runs: dict[str, AnalysisRun] = field(default_factory=dict)
    failed_recomputes: dict[tuple[str, str], int] = field(default_factory=dict)
    stage_runs: dict[str, list[tuple[str, StageState, str | None]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    summaries: dict[str, Any] = field(default_factory=dict)
    actions: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))
    spans: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))


def _load_facts(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    population: Sequence[ColumnElement[bool]] = (),
    candidates: Select[Any] | None = None,
) -> _Facts:
    """Everything the quality assessment and the report count read, for every
    candidate meeting at once: eight statements, whatever the meeting count.
    ``candidates`` (a select of meeting ids) replaces the ledger candidates (HOM-299)."""
    if candidates is None:
        candidates = _candidate_meetings(workspace_id, window, population)
    facts = _Facts()
    for revision in db.scalars(
        select(TranscriptRevision).where(
            TranscriptRevision.workspace_id == workspace_id,
            TranscriptRevision.is_current.is_(True),
            TranscriptRevision.meeting_id.in_(candidates),
        )
    ):
        facts.revisions[revision.meeting_id] = revision
    for run in db.scalars(
        select(AnalysisRun).where(
            AnalysisRun.workspace_id == workspace_id,
            AnalysisRun.is_current.is_(True),
            AnalysisRun.meeting_id.in_(candidates),
        )
    ):
        facts.runs[run.meeting_id] = run
    for meeting_id, revision_id, count in db.execute(
        select(AnalysisRun.meeting_id, AnalysisRun.transcript_revision_id, func.count())
        .where(
            AnalysisRun.workspace_id == workspace_id,
            AnalysisRun.state == StageState.FAILED,
            AnalysisRun.meeting_id.in_(candidates),
        )
        .group_by(AnalysisRun.meeting_id, AnalysisRun.transcript_revision_id)
    ):
        facts.failed_recomputes[(meeting_id, revision_id)] = int(count)

    # latest_job_for_meeting: the newest job of each meeting (id breaks a tie)
    latest_jobs: dict[str, tuple[datetime, str]] = {}
    for job_id, meeting_id, created_at in db.execute(
        select(ProcessingJob.id, ProcessingJob.meeting_id, ProcessingJob.created_at).where(
            ProcessingJob.workspace_id == workspace_id,
            ProcessingJob.meeting_id.in_(candidates),
        )
    ):
        newest = latest_jobs.get(meeting_id)
        if newest is None or (created_at, job_id) > newest:
            latest_jobs[meeting_id] = (created_at, job_id)
    latest_job_ids = {job_id: meeting_id for meeting_id, (_, job_id) in latest_jobs.items()}
    # stage runs of each job's current attempt, in sequence order
    for job_id, stage, state, reason in db.execute(
        select(
            ProcessingStageRun.job_id,
            ProcessingStageRun.stage,
            ProcessingStageRun.state,
            ProcessingStageRun.reason_code,
        )
        .join(
            ProcessingJob,
            and_(
                ProcessingJob.id == ProcessingStageRun.job_id,
                ProcessingJob.attempt_count == ProcessingStageRun.attempt,
            ),
        )
        .where(
            ProcessingStageRun.workspace_id == workspace_id,
            ProcessingStageRun.meeting_id.in_(candidates),
        )
        .order_by(ProcessingStageRun.job_id, ProcessingStageRun.sequence.asc())
    ):
        meeting_id = latest_job_ids.get(job_id)
        if meeting_id is not None:
            facts.stage_runs[meeting_id].append((stage, state, reason))

    for row in db.execute(
        select(
            MeetingSummary.meeting_id,
            MeetingSummary.evidence_status,
            MeetingSummary.key_points,
            MeetingSummary.schema_version,
            MeetingSummary.analysis_run_id,
        ).where(
            MeetingSummary.workspace_id == workspace_id,
            MeetingSummary.meeting_id.in_(candidates),
        )
    ):
        facts.summaries[row.meeting_id] = row
    for row in db.execute(
        select(
            ActionItem.meeting_id,
            ActionItem.id,
            ActionItem.evidence_status,
            ActionItem.created_by,
        ).where(
            ActionItem.workspace_id == workspace_id,
            ActionItem.meeting_id.in_(candidates),
        )
    ):
        facts.actions[row.meeting_id].append(row)
    for row in db.execute(
        select(
            EvidenceSpan.meeting_id,
            EvidenceSpan.id,
            EvidenceSpan.finding_type,
            EvidenceSpan.finding_index,
            EvidenceSpan.finding_id,
            EvidenceSpan.analysis_run_id,
        ).where(
            EvidenceSpan.workspace_id == workspace_id,
            EvidenceSpan.meeting_id.in_(candidates),
        )
    ):
        facts.spans[row.meeting_id].append(row)
    return facts


def _assess(
    facts: _Facts, *, meeting_id: str, revision: TranscriptRevision, recompute_max_attempts: int
) -> MeetingQualityResponse:
    """``assess_meeting`` from the preloaded facts.

    The segments, speaker count, duration and usage only feed speech coverage,
    the speaker count and usage, which neither completeness nor the risk flags
    read, so they are left empty here.
    """
    run = facts.runs.get(meeting_id)
    summary = facts.summaries.get(meeting_id)
    return MeetingQualityResponse.model_validate(
        assess_quality(
            revision=revision,
            run=run,
            summary_status=summary.evidence_status if summary is not None else None,
            action_statuses=[action.evidence_status for action in facts.actions[meeting_id]],
            stage_runs=facts.stage_runs.get(meeting_id, []),
            segments=[],
            speaker_count=0,
            duration_seconds=0,
            recompute_failures=facts.failed_recomputes.get((meeting_id, revision.id), 0),
            recompute_max_attempts=recompute_max_attempts,
        ).as_dict()
    )


def _is_candidate(row: Any) -> bool:
    """An accepted row whose live meeting is completed or partial: its processing
    bucket is decided by the transcript."""
    return (
        row.status != IngestionStatus.REJECTED
        and row.meeting_id is not None
        and row.deleted_at is None
        and row.meeting_status not in IN_PROGRESS_STATUSES
        and row.meeting_status not in FAILED_STATUSES
    )


def _count(
    rows: Iterable[Any],
    facts: _Facts,
    *,
    window: CoverageWindow,
    recompute_max_attempts: int,
) -> Coverage:
    """The four counters of ``rows`` (ledger rows of ``_ledger_rows``) with ``facts``
    loaded for every candidate among them."""
    ingestion = dict.fromkeys(INGESTION_BUCKETS, 0)
    rejected_reasons: Counter[str] = Counter()
    processing = dict.fromkeys(PROCESSING_BUCKETS, 0)
    evaluation = dict.fromkeys(EVALUATION_BUCKETS, 0)
    candidates = []
    for row in rows:
        if row.status == IngestionStatus.REJECTED:
            ingestion["rejected"] += 1
            rejected_reasons[row.reason] += 1
            continue
        ingestion["accepted"] += 1
        if row.meeting_id is None or row.deleted_at is not None:
            processing["deleted"] += 1
        elif row.meeting_status in IN_PROGRESS_STATUSES:
            processing["in_progress"] += 1
        elif row.meeting_status in FAILED_STATUSES:
            processing["failed"] += 1
        else:
            candidates.append(row)  # completed or partial: decided by the transcript

    evaluated: list[EvaluatedMeeting] = []
    for row in candidates:
        revision = facts.revisions.get(row.meeting_id)
        if not has_segments(revision.segment_count if revision is not None else None):
            processing["no_transcript"] += 1
            continue
        processing[BUCKET_TRANSCRIBED] += 1
        quality = _assess(
            facts,
            meeting_id=row.meeting_id,
            revision=revision,
            recompute_max_attempts=recompute_max_attempts,
        )
        unsupported = unsupported_finding_count(
            summary=facts.summaries.get(row.meeting_id),
            actions=[a for a in facts.actions[row.meeting_id] if a.created_by == "ai"],
            spans=facts.spans[row.meeting_id],
        )
        status = MeetingStatus(row.meeting_status)
        if is_report_complete(status=status, quality=quality, unsupported=unsupported):
            bucket = BUCKET_FULLY_EVALUATED
        else:
            bucket = completeness_notices(status=status, quality=quality, unsupported=unsupported)[
                0
            ]
        evaluation[bucket] += 1
        evaluated.append(
            EvaluatedMeeting(
                meeting_id=row.meeting_id,
                team_id=row.team_id,
                agent_user_id=row.agent_user_id,
                source=row.source,
                language=row.language,
                revision_source=TranscriptSource(revision.source).value,
                quality=quality,
                bucket=bucket,
            )
        )

    ingested = sum(ingestion.values())
    ingestion_counter = _counter(
        "ingestion", numerator=ingestion["accepted"], denominator=ingested, buckets=ingestion
    )
    ingestion_counter["rejected_reasons"] = dict(sorted(rejected_reasons.items()))
    counters = {
        "ingestion": ingestion_counter,
        "processing": _counter(
            "processing",
            numerator=processing[BUCKET_TRANSCRIBED],
            denominator=ingestion["accepted"],
            buckets=processing,
        ),
        "evaluation": _counter(
            "evaluation",
            numerator=evaluation[BUCKET_FULLY_EVALUATED],
            denominator=processing[BUCKET_TRANSCRIBED],
            buckets=evaluation,
        ),
        "review": _review_counter(evaluation[BUCKET_FULLY_EVALUATED]),
    }
    return Coverage(window=window, counters=counters, evaluated=evaluated)


def _rows_and_facts(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    population: Sequence[ColumnElement[bool]],
) -> tuple[Sequence[Any], _Facts]:
    rows = _ledger_rows(db, workspace_id=workspace_id, window=window, population=population)
    facts = (
        _load_facts(db, workspace_id=workspace_id, window=window, population=population)
        if any(_is_candidate(row) for row in rows)
        else _Facts()
    )
    return rows, facts


def compute_coverage(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    recompute_max_attempts: int,
    population: Sequence[ColumnElement[bool]] = (),
) -> Coverage:
    """The four ``coverage/1`` counters of the workspace for ``window``.

    ``population`` narrows the ledger rows with the same rules (HOM-81): extra clauses on
    ``IngestionRecord`` joined to ``Meeting``, ANDed.  A ledger row then counts only if it
    joins to a meeting of the workspace (live or soft-deleted) and passes every clause.
    Empty (the default) is the whole workspace, ``GET /admin/coverage``.
    """
    rows, facts = _rows_and_facts(
        db, workspace_id=workspace_id, window=window, population=population
    )
    return _count(rows, facts, window=window, recompute_max_attempts=recompute_max_attempts)


def compute_coverage_by_team(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    recompute_max_attempts: int,
    population: Sequence[ColumnElement[bool]] = (),
    teams: Iterable[str | None] = (),
) -> tuple[Coverage, dict[str | None, Coverage]]:
    """``compute_coverage`` once more, also split by team (HOM-82): the same statements
    (1 ledger statement, plus 8 fact statements when a candidate exists).

    Each ledger row counts in the whole and in the group of its meeting's ``team_id``; the
    key ``None`` holds the meetings with no team.  A ledger row with no meeting (possible
    only with an empty ``population``, where the meeting join is outer) counts in the whole
    and in no group.  Every id of ``teams`` has a group, empty when it has no row.
    """
    rows, facts = _rows_and_facts(
        db, workspace_id=workspace_id, window=window, population=population
    )
    grouped: dict[str | None, list[Any]] = {team_id: [] for team_id in teams}
    grouped = defaultdict(list, grouped)
    for row in rows:
        if row.meeting_id is not None:
            grouped[row.team_id].append(row)

    def count(subset: Iterable[Any]) -> Coverage:
        return _count(subset, facts, window=window, recompute_max_attempts=recompute_max_attempts)

    return count(rows), {team_id: count(team_rows) for team_id, team_rows in grouped.items()}


class _PopulationRow(NamedTuple):
    """A meeting of a population that is not the ledger, with the attributes of a
    ``_ledger_rows`` row: always accepted, no rejection reason."""

    status: IngestionStatus
    reason: str | None
    meeting_id: str
    meeting_status: MeetingStatus
    deleted_at: datetime | None
    team_id: str | None
    agent_user_id: str | None
    source: str
    language: str


def compute_population_coverage(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    rows: Iterable[Any],
    candidates: Select[Any],
    recompute_max_attempts: int,
) -> Coverage:
    """The ``coverage/1`` counters over meetings that are not ledger rows (HOM-299).

    ``rows`` are the meetings of the population (``meeting_id``, ``meeting_status``,
    ``deleted_at``, ``team_id``, ``agent_user_id``, ``source``, ``language``), each one
    accepted event; ``candidates`` selects the ids of those that are live and completed or
    partial.  The same buckets and assessment as ``compute_coverage``, with 8 fact
    statements only when a candidate exists.  Only ``processing`` and ``evaluation`` are
    meant to be read: ``ingestion`` is all accepted and ``review`` has no source.
    """
    population = [
        _PopulationRow(
            IngestionStatus.ACCEPTED,
            None,
            row.meeting_id,
            row.meeting_status,
            row.deleted_at,
            row.team_id,
            row.agent_user_id,
            row.source,
            row.language,
        )
        for row in rows
    ]
    facts = (
        _load_facts(db, workspace_id=workspace_id, window=window, candidates=candidates)
        if any(_is_candidate(row) for row in population)
        else _Facts()
    )
    return _count(population, facts, window=window, recompute_max_attempts=recompute_max_attempts)
