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

HOM-312 (U2): with the app's ``JudgmentCache``, the ``unsupported_findings`` verdict of a
meeting (``unsupported_finding_count``) is kept per meeting and reused while its fingerprint
(read in the revisions statement) is unchanged and the entry is younger than the time limit.
The summary, AI action item and span statements then read only the meetings whose verdict is
needed and not kept.  ADR 0043 says what can still be stale, and for how long.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import ColumnElement, Select, String, and_, any_, bindparam, func, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import InstrumentedAttribute, Session

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
from backend.services.trend_judgment_cache import JudgmentCache, VerdictKey

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


class Coverage:
    """The counters of a window, and the assessed ``transcribed`` meetings.

    HOM-312: ``evaluated`` may be built only when it is read (``evaluate``), from facts that
    were already loaded, so a caller that needs only the counters pays nothing for it."""

    def __init__(
        self,
        window: CoverageWindow,
        counters: dict[str, dict[str, Any]],
        evaluated: list[EvaluatedMeeting] | None = None,
        *,
        evaluate: Callable[[], list[EvaluatedMeeting]] | None = None,
    ) -> None:
        self.window = window
        self.counters = counters
        self._evaluated = evaluated
        self._evaluate = evaluate

    @property
    def evaluated(self) -> list[EvaluatedMeeting]:
        if self._evaluated is None:
            self._evaluated = self._evaluate() if self._evaluate is not None else []
        return self._evaluated

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

# The finding types whose spans ``unsupported_finding_count`` reads (HOM-312).
REPORT_FINDING_TYPES = (FINDING_SUMMARY, FINDING_INSIGHT, FINDING_ACTION_ITEM)
# The rules of a cached verdict (HOM-312, U2): bump it when ``unsupported_finding_count`` or
# what it reads changes, so no verdict judged by the old rules is reused.
VERDICT_VERSION = "verdict/1"


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
    # with the verdict cache (U2): the unsupported count of every meeting whose bucket needs
    # it; ``summaries``, ``actions`` and ``spans`` then hold only the meetings judged now
    verdicts: dict[str, int] | None = None
    verdicts_kept: int = 0  # entries added to the cache (the warm-up counts them)


@dataclass(frozen=True)
class _VerdictCache:
    cache: JudgmentCache
    key: VerdictKey
    evict: bool = True  # False for the warm-up: fill only the free room


def among_ids(
    db: Session, column: InstrumentedAttribute[Any], ids: Sequence[str], name: str
) -> ColumnElement[bool]:
    """``column`` is one of ``ids``, sent as one statement parameter whatever their number
    (HOM-312): a PostgreSQL array, or a JSON array read with SQLite's ``json_each``."""
    if db.get_bind().dialect.name == "postgresql":
        return column == any_(bindparam(name, list(ids), type_=ARRAY(String)))
    values = func.json_each(bindparam(name, json.dumps(list(ids))))
    return column.in_(select(values.table_valued("value").c.value))


def _with_fingerprint(db: Session, revisions: Select[Any], workspace_id: str) -> Select[Any]:
    """``revisions`` (the slim revision statement) with each meeting's verdict fingerprint (U2):
    its summary (id, ``updated_at``, run), and its AI action items and its spans of
    ``REPORT_FINDING_TYPES``, each as (count, latest change, largest id).

    The action items and spans are aggregated per meeting row, through the index on the
    meeting (a ``LATERAL`` aggregate on PostgreSQL, correlated subqueries on SQLite), so the
    memory does not grow with the window.  (A grouped aggregate of the whole window, joined
    back, needed a large shared-memory hash on PostgreSQL and failed with ``DiskFull`` on
    Docker's default 64 MB ``/dev/shm`` for the 12-week organization routes at 100k.)  Still
    one statement."""
    of_actions = (
        ActionItem.workspace_id == workspace_id,
        ActionItem.meeting_id == TranscriptRevision.meeting_id,
        ActionItem.created_by == "ai",
    )
    of_spans = (
        EvidenceSpan.workspace_id == workspace_id,
        EvidenceSpan.meeting_id == TranscriptRevision.meeting_id,
        EvidenceSpan.finding_type.in_(REPORT_FINDING_TYPES),
    )
    revisions = revisions.add_columns(
        MeetingSummary.id.label("summary_id"),
        MeetingSummary.updated_at.label("summary_changed"),
        MeetingSummary.analysis_run_id.label("summary_run"),
    ).outerjoin(
        MeetingSummary,
        and_(
            MeetingSummary.meeting_id == TranscriptRevision.meeting_id,
            MeetingSummary.workspace_id == workspace_id,
        ),
    )
    parts = (
        ("action", ActionItem.updated_at, ActionItem.id, of_actions),
        ("span", EvidenceSpan.created_at, EvidenceSpan.id, of_spans),
    )
    if db.get_bind().dialect.name == "postgresql":
        for name, changed, last, where in parts:
            per_meeting = (
                select(
                    func.count().label("count"),
                    func.max(changed).label("changed"),
                    func.max(last).label("last"),
                )
                .where(*where)
                .lateral(f"verdict_{name}s")
            )
            revisions = revisions.add_columns(
                per_meeting.c.count.label(f"{name}_count"),
                per_meeting.c.changed.label(f"{name}_changed"),
                per_meeting.c.last.label(f"{name}_last"),
            ).join(per_meeting, true())  # an aggregate always has one row
        return revisions
    for name, changed, last, where in parts:
        revisions = revisions.add_columns(
            select(func.count()).where(*where).scalar_subquery().label(f"{name}_count"),
            select(func.max(changed)).where(*where).scalar_subquery().label(f"{name}_changed"),
            select(func.max(last)).where(*where).scalar_subquery().label(f"{name}_last"),
        )
    return revisions


def _fingerprint(revision: Any) -> tuple[Any, ...]:
    """The verdict fingerprint of a meeting, from its slim revision row (U2)."""
    return (
        revision.summary_id,
        revision.summary_changed,
        revision.summary_run,
        revision.action_count or 0,
        revision.action_changed,
        revision.action_last,
        revision.span_count or 0,
        revision.span_changed,
        revision.span_last,
    )


def _fingerprint_of(summary: Any | None, actions: Sequence[Any], spans: Sequence[Any]):
    """``_fingerprint`` of the rows a verdict was judged from: equal to the revision row's
    exactly when nothing changed between the two statements."""
    return (
        summary.id if summary is not None else None,
        summary.updated_at if summary is not None else None,
        summary.analysis_run_id if summary is not None else None,
        len(actions),
        max((action.updated_at for action in actions), default=None),
        max((action.id for action in actions), default=None),
        len(spans),
        max((span.created_at for span in spans), default=None),
        max((span.id for span in spans), default=None),
    )


def _digest(fingerprint: tuple[Any, ...]) -> bytes:
    """What a verdict entry keeps of a fingerprint: a 128-bit ``blake2b`` digest of its
    canonical text (ADR 0043: a collision chance of 2^-128 per compare; the whole tuple would
    cost 171% more memory per entry).  The text is ``repr`` of the tuple, which is exact for its
    values (str, int, None, datetime), with every aware datetime in UTC first: two aware
    datetimes at the same instant are equal whatever their zone, so they must give the same
    text."""
    canonical = tuple(
        value.astimezone(UTC) if isinstance(value, datetime) and value.tzinfo else value
        for value in fingerprint
    )
    return hashlib.blake2b(repr(canonical).encode(), digest_size=16).digest()


def _load_facts(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    population: Sequence[ColumnElement[bool]] = (),
    candidates: Select[Any] | None = None,
    slim: bool = False,
    verdicts: _VerdictCache | None = None,
) -> _Facts:
    """Everything the quality assessment and the report count read, for every
    candidate meeting at once: eight statements, whatever the meeting count.
    ``candidates`` (a select of meeting ids) replaces the ledger candidates (HOM-299).

    ``slim`` (HOM-312): the revisions and runs come as rows of the columns the assessment
    reads, not as ORM objects; only revisions with a segment (the ``transcribed`` meetings),
    each with its meeting's columns (``meeting_status``, ``team_id``, ``agent_user_id``,
    ``meeting_source``, ``language``).

    ``verdicts`` (U2, slim only): the revision rows also carry each meeting's verdict
    fingerprint; after the runs and stage runs, the meetings whose bucket reaches the
    ``unsupported_findings`` step take their kept verdict, and the summary, AI action item and
    span statements read only the others (one parameter), whose verdicts are judged and kept.
    Still eight statements."""
    if candidates is None:
        candidates = _candidate_meetings(workspace_id, window, population)
    facts = _Facts()
    if slim:
        revisions: Select[Any] = (
            select(
                TranscriptRevision.meeting_id,
                TranscriptRevision.id,
                TranscriptRevision.segment_count,
                TranscriptRevision.is_mock,
                TranscriptRevision.source,
                TranscriptRevision.diarization_status,
                Meeting.status.label("meeting_status"),
                Meeting.team_id,
                Meeting.agent_user_id,
                Meeting.source.label("meeting_source"),
                Meeting.language,
            )
            .join(Meeting, Meeting.id == TranscriptRevision.meeting_id)
            .where(
                TranscriptRevision.workspace_id == workspace_id,
                TranscriptRevision.is_current.is_(True),
                TranscriptRevision.segment_count > 0,
                TranscriptRevision.meeting_id.in_(candidates),
            )
        )
        if verdicts is not None:
            revisions = _with_fingerprint(db, revisions, workspace_id)
        runs: Select[Any] = select(
            AnalysisRun.meeting_id,
            AnalysisRun.id,
            AnalysisRun.state,
            AnalysisRun.transcript_revision_id,
            AnalysisRun.is_mock,
        ).where(
            AnalysisRun.workspace_id == workspace_id,
            AnalysisRun.is_current.is_(True),
            AnalysisRun.meeting_id.in_(candidates),
        )
        for revision in db.execute(revisions):
            facts.revisions[revision.meeting_id] = revision
        for run in db.execute(runs):
            facts.runs[run.meeting_id] = run
    else:
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

    if verdicts is not None:
        return _load_verdicts(db, facts, workspace_id=workspace_id, verdicts=verdicts)
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
    spans_statement = select(
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
    if slim:
        # HOM-312: only the finding types ``unsupported_finding_count`` counts; the others
        # (``extraction``) never change a count.
        spans_statement = spans_statement.where(EvidenceSpan.finding_type.in_(REPORT_FINDING_TYPES))
    for row in db.execute(spans_statement):
        facts.spans[row.meeting_id].append(row)
    return facts


def _load_verdicts(
    db: Session, facts: _Facts, *, workspace_id: str, verdicts: _VerdictCache
) -> _Facts:
    """The last three fact statements of ``_load_facts`` with the verdict cache (U2).

    The meetings that need a verdict are those whose bucket is not decided before the
    ``unsupported_findings`` step (``_notice_before_verdict``, without the meeting's partial
    status, so the set never misses one ``_evaluation_bucket`` asks for).  A verdict is kept
    only when the rows it was judged from have the fingerprint the lookup used."""
    needed = {
        meeting_id: _fingerprint(revision)
        for meeting_id, revision in facts.revisions.items()
        if _notice_before_verdict(
            revision,
            facts.runs.get(meeting_id),
            facts.stage_runs.get(meeting_id, ()),
            partial_meeting=False,
        )
        is None
    }
    found = verdicts.cache.verdicts(
        verdicts.key, {meeting_id: _digest(print_) for meeting_id, print_ in needed.items()}
    )
    misses = [meeting_id for meeting_id in needed if meeting_id not in found]
    for row in db.execute(
        select(
            MeetingSummary.meeting_id,
            MeetingSummary.id,
            MeetingSummary.updated_at,
            MeetingSummary.evidence_status,
            MeetingSummary.key_points,
            MeetingSummary.schema_version,
            MeetingSummary.analysis_run_id,
        ).where(
            MeetingSummary.workspace_id == workspace_id,
            among_ids(db, MeetingSummary.meeting_id, misses, "coverage_verdict_misses"),
        )
    ):
        facts.summaries[row.meeting_id] = row
    for row in db.execute(
        select(
            ActionItem.meeting_id,
            ActionItem.id,
            ActionItem.evidence_status,
            ActionItem.created_by,
            ActionItem.updated_at,
        ).where(
            ActionItem.workspace_id == workspace_id,
            ActionItem.created_by == "ai",
            among_ids(db, ActionItem.meeting_id, misses, "coverage_verdict_misses"),
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
            EvidenceSpan.created_at,
        ).where(
            EvidenceSpan.workspace_id == workspace_id,
            EvidenceSpan.finding_type.in_(REPORT_FINDING_TYPES),
            among_ids(db, EvidenceSpan.meeting_id, misses, "coverage_verdict_misses"),
        )
    ):
        facts.spans[row.meeting_id].append(row)
    hits = list(found)  # moved to the recent end after the new entries (``keep_verdicts``)
    judged: dict[str, tuple[Any, int]] = {}
    for meeting_id in misses:
        summary = facts.summaries.get(meeting_id)
        actions = facts.actions.get(meeting_id, [])
        spans = facts.spans.get(meeting_id, [])
        verdict = unsupported_finding_count(summary=summary, actions=actions, spans=spans)
        found[meeting_id] = verdict
        unchanged = _fingerprint_of(summary, actions, spans) == needed[meeting_id]
        judged[meeting_id] = (_digest(needed[meeting_id]) if unchanged else None, verdict)
    facts.verdicts_kept = verdicts.cache.keep_verdicts(
        verdicts.key, judged, evict=verdicts.evict, recent=hits
    )
    facts.verdicts = found
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

    counters = _counters(ingestion, rejected_reasons, processing, evaluation)
    return Coverage(window=window, counters=counters, evaluated=evaluated)


def _counters(
    ingestion: Mapping[str, int],
    rejected_reasons: Counter[str],
    processing: Mapping[str, int],
    evaluation: Mapping[str, int],
) -> dict[str, dict[str, Any]]:
    """The four counters from the bucket counts."""
    ingested = sum(ingestion.values())
    ingestion_counter = _counter(
        "ingestion", numerator=ingestion["accepted"], denominator=ingested, buckets=ingestion
    )
    ingestion_counter["rejected_reasons"] = dict(sorted(rejected_reasons.items()))
    return {
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


def reference_coverage(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    recompute_max_attempts: int,
    population: Sequence[ColumnElement[bool]] = (),
) -> Coverage:
    """The HOM-72 Python path: the facts as ORM objects and every transcribed meeting
    assessed with ``_assess`` (``assess_quality`` and the report's completeness rules).
    HOM-312 keeps it as the oracle of the parity test (``test_coverage_sql_parity.py``);
    ``compute_coverage`` must give the same counters."""
    rows, facts = _rows_and_facts(
        db, workspace_id=workspace_id, window=window, population=population
    )
    return _count(rows, facts, window=window, recompute_max_attempts=recompute_max_attempts)


def reference_coverage_by_team(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    recompute_max_attempts: int,
    population: Sequence[ColumnElement[bool]] = (),
    teams: Iterable[str | None] = (),
) -> tuple[Coverage, dict[str | None, Coverage]]:
    """The HOM-72 Python path of ``compute_coverage_by_team`` (the parity oracle)."""
    rows, facts = _rows_and_facts(
        db, workspace_id=workspace_id, window=window, population=population
    )

    def count(subset: Iterable[Any]) -> Coverage:
        return _count(subset, facts, window=window, recompute_max_attempts=recompute_max_attempts)

    return count(rows), {
        team_id: count(team_rows) for team_id, team_rows in _by_team(rows, teams).items()
    }


def _by_team(rows: Iterable[Any], teams: Iterable[str | None]) -> dict[str | None, list[Any]]:
    """The ledger rows of each team: every id of ``teams``, and every ``team_id`` (``None``
    for no team) of a row that has a meeting."""
    grouped: dict[str | None, list[Any]] = {team_id: [] for team_id in teams}
    grouped = defaultdict(list, grouped)
    for row in rows:
        if row.meeting_id is not None:
            grouped[row.team_id].append(row)
    return grouped


def _evaluation_bucket(row: Any, facts: _Facts) -> str:
    """The evaluation bucket of one ``transcribed`` meeting (HOM-312): the first notice of
    ``completeness_notices`` (``report_service.py``), from the same facts ``_assess`` reads,
    without building the quality response.  ``row`` is its ledger row; its current revision
    has a segment.  The rules, in the notices' order:

    * ``transcript_partial``: the ASR stage of the newest job's current attempt is partial
      (``assess_quality``: the last ``asr`` stage run wins);
    * ``analysis_unavailable``: no current run, or its state is not completed or partial;
    * ``analysis_partial``: the run is partial, or the meeting status is partial;
    * ``analysis_stale``: the run read another revision than the current one;
    * ``analysis_recompute_failed``: stale and the failed recomputes of the current revision
      reached the limit (never first: stale comes before it);
    * ``mock_analysis``: the current revision or the run is a mock;
    * ``unsupported_findings``: ``unsupported_finding_count`` is not zero (with the verdict
      cache, the count ``_load_verdicts`` found or judged);
    * else ``fully_evaluated`` (``is_report_complete``).
    """
    meeting_id = row.meeting_id
    notice = _notice_before_verdict(
        facts.revisions[meeting_id],
        facts.runs.get(meeting_id),
        facts.stage_runs.get(meeting_id, ()),
        partial_meeting=MeetingStatus(row.meeting_status) == MeetingStatus.PARTIAL,
    )
    if notice is not None:
        return notice
    if facts.verdicts is not None:
        unsupported = facts.verdicts[meeting_id]
    else:
        unsupported = unsupported_finding_count(
            summary=facts.summaries.get(meeting_id),
            actions=[a for a in facts.actions.get(meeting_id, ()) if a.created_by == "ai"],
            spans=facts.spans.get(meeting_id, ()),
        )
    if unsupported:
        return NOTICE_UNSUPPORTED_FINDINGS
    return BUCKET_FULLY_EVALUATED


def _notice_before_verdict(
    revision: Any, run: Any | None, stage_runs: Iterable[Any], *, partial_meeting: bool
) -> str | None:
    """The first notice of the rules before ``unsupported_findings`` (``_evaluation_bucket``),
    or ``None`` when the bucket is decided by the verdict."""
    by_stage = {stage: state for stage, state, _reason in stage_runs}
    if by_stage.get("asr") == StageState.PARTIAL:
        return NOTICE_TRANSCRIPT_PARTIAL
    if run is None or run.state not in (StageState.COMPLETED, StageState.PARTIAL):
        return NOTICE_ANALYSIS_UNAVAILABLE
    if run.state == StageState.PARTIAL or partial_meeting:
        return NOTICE_ANALYSIS_PARTIAL
    if run.transcript_revision_id != revision.id:
        # ``analysis_recompute_failed`` needs a stale run, and stale is the earlier notice.
        return NOTICE_ANALYSIS_STALE
    if revision.is_mock or run.is_mock:
        return NOTICE_MOCK_ANALYSIS
    return None


def _tally(
    rows: Iterable[Any], facts: _Facts, bucket_of: Callable[[Any], str]
) -> dict[str, dict[str, Any]]:
    """The four counters of ``rows``, as ``_count`` counts them, with the evaluation bucket
    of each ``transcribed`` meeting from ``bucket_of``."""
    ingestion = dict.fromkeys(INGESTION_BUCKETS, 0)
    rejected_reasons: Counter[str] = Counter()
    processing = dict.fromkeys(PROCESSING_BUCKETS, 0)
    evaluation = dict.fromkeys(EVALUATION_BUCKETS, 0)
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
            revision = facts.revisions.get(row.meeting_id)
            if not has_segments(revision.segment_count if revision is not None else None):
                processing["no_transcript"] += 1
                continue
            processing[BUCKET_TRANSCRIBED] += 1
            evaluation[bucket_of(row)] += 1
    return _counters(ingestion, rejected_reasons, processing, evaluation)


def _verdict_cache(
    cache: JudgmentCache | None, organization_id: str | None, workspace_id: str
) -> _VerdictCache | None:
    if cache is None:
        return None
    return _VerdictCache(cache, VerdictKey(organization_id, workspace_id, VERDICT_VERSION))


def _counting(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    population: Sequence[ColumnElement[bool]],
    verdicts: _VerdictCache | None = None,
) -> tuple[Sequence[Any], _Facts, Callable[[Any], str]]:
    """The ledger rows, the slim facts (8 statements, only when a candidate exists) and a
    function giving each ``transcribed`` meeting's evaluation bucket, worked out once per
    meeting however many groups count it.  With ``verdicts`` the request is marked as using
    the cache while it runs (``JudgmentCache.request``: the warm-up waits)."""
    with verdicts.cache.request() if verdicts is not None else nullcontext():
        rows = _ledger_rows(db, workspace_id=workspace_id, window=window, population=population)
        facts = (
            _load_facts(
                db,
                workspace_id=workspace_id,
                window=window,
                population=population,
                slim=True,
                verdicts=verdicts,
            )
            if any(_is_candidate(row) for row in rows)
            else _Facts()
        )
    buckets: dict[str, str] = {}

    def bucket_of(row: Any) -> str:
        bucket = buckets.get(row.meeting_id)
        if bucket is None:
            bucket = buckets[row.meeting_id] = _evaluation_bucket(row, facts)
        return bucket

    return rows, facts, bucket_of


def warm_verdicts(
    db: Session,
    *,
    cache: JudgmentCache,
    workspace_id: str,
    organization_id: str | None,
    window: CoverageWindow,
    batch: int = 1000,
    between: Callable[[], None] | None = None,
) -> int:
    """The warm-up's verdict work for one workspace (HOM-312, U2; ``trend_judgment_warmup``): the
    fact statements of ``compute_coverage`` with ``cache`` over the candidate meetings of
    ``window``, so the verdicts not kept yet are judged and kept, only in the free room
    (never an eviction).  The workspace must be bound as the tenant.  Returns the number of
    entries added.

    One statement lists the candidate meetings; then the eight fact statements run per batch
    of at most ``batch`` meetings, so the report rows held at once are those of one batch.
    Before each batch the read transaction is ended and ``between`` runs (the warm-up may
    wait there, holding no transaction and no row)."""
    key = VerdictKey(organization_id, workspace_id, VERDICT_VERSION)
    meeting_ids = sorted(db.scalars(_candidate_meetings(workspace_id, window)))
    added = 0
    for start in range(0, len(meeting_ids), batch):
        db.rollback()
        if between is not None:
            between()
        if cache.room() <= 0:
            break
        part = meeting_ids[start : start + batch]
        facts = _load_facts(
            db,
            workspace_id=workspace_id,
            window=window,
            slim=True,
            verdicts=_VerdictCache(cache, key, evict=False),
            candidates=select(Meeting.id).where(
                among_ids(db, Meeting.id, part, "coverage_warm_meetings")
            ),
        )
        added += facts.verdicts_kept
    return added


def _no_evaluated() -> list[EvaluatedMeeting]:
    raise RuntimeError(
        "coverage counted with the verdict cache has no evaluated list "
        "(its facts hold only the meetings judged now); count it without the cache"
    )


def compute_coverage(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    recompute_max_attempts: int,
    population: Sequence[ColumnElement[bool]] = (),
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> Coverage:
    """The four ``coverage/1`` counters of the workspace for ``window``.

    ``population`` narrows the ledger rows with the same rules (HOM-81): extra clauses on
    ``IngestionRecord`` joined to ``Meeting``, ANDed.  A ledger row then counts only if it
    joins to a meeting of the workspace (live or soft-deleted) and passes every clause.
    Empty (the default) is the whole workspace, ``GET /admin/coverage``.

    HOM-312: the same statements as ``reference_coverage`` (1 ledger statement, plus 8 fact
    statements when a candidate exists), with slim revision and run rows, and each meeting's
    bucket from ``_evaluation_bucket``.  ``evaluated`` (review sampling) is built only when
    read, by ``_count`` over the same facts, so it is the reference path's list.

    ``cache`` (U2): the app's ``JudgmentCache``, for the counters only (the same counters,
    the same statements); reading ``evaluated`` then raises.  ``organization_id`` is the
    workspace's organization, part of the cache key.
    """
    rows, facts, bucket_of = _counting(
        db,
        workspace_id=workspace_id,
        window=window,
        population=population,
        verdicts=_verdict_cache(cache, organization_id, workspace_id),
    )

    def evaluate() -> list[EvaluatedMeeting]:
        return _count(
            rows, facts, window=window, recompute_max_attempts=recompute_max_attempts
        ).evaluated

    return Coverage(
        window,
        _tally(rows, facts, bucket_of),
        evaluate=_no_evaluated if facts.verdicts is not None else evaluate,
    )


def compute_coverage_by_team(
    db: Session,
    *,
    workspace_id: str,
    window: CoverageWindow,
    recompute_max_attempts: int,
    population: Sequence[ColumnElement[bool]] = (),
    teams: Iterable[str | None] = (),
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> tuple[Coverage, dict[str | None, Coverage]]:
    """``compute_coverage`` once more, also split by team (HOM-82): the same statements
    (1 ledger statement, plus 8 fact statements when a candidate exists).

    Each ledger row counts in the whole and in the group of its meeting's ``team_id``; the
    key ``None`` holds the meetings with no team.  A ledger row with no meeting (possible
    only with an empty ``population``, where the meeting join is outer) counts in the whole
    and in no group.  Every id of ``teams`` has a group, empty when it has no row.
    HOM-312: each meeting is assessed once, for the whole and its team; ``cache`` and
    ``organization_id`` as in ``compute_coverage``.
    """
    rows, facts, bucket_of = _counting(
        db,
        workspace_id=workspace_id,
        window=window,
        population=population,
        verdicts=_verdict_cache(cache, organization_id, workspace_id),
    )

    def coverage(subset: Sequence[Any]) -> Coverage:
        def evaluate() -> list[EvaluatedMeeting]:
            return _count(
                subset, facts, window=window, recompute_max_attempts=recompute_max_attempts
            ).evaluated

        return Coverage(
            window,
            _tally(subset, facts, bucket_of),
            evaluate=_no_evaluated if facts.verdicts is not None else evaluate,
        )

    return coverage(rows), {
        team_id: coverage(team_rows) for team_id, team_rows in _by_team(rows, teams).items()
    }


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
