"""Scoped issue-trend aggregates (HOM-80, WP15-T02): ``GET /trends/issues``.

The population is the ``coverage/1`` processing bucket ``transcribed`` of the window
(``transcribable_clauses`` and ``has_segments`` of ``coverage_service``), ANDed with the
caller's record scope (``RecordAccess.meeting_clause``) and the population filters.  Two
statements load it, whatever the number of conversations, media groups or points:

    P  every conversation of the window with its current transcript revision, its
       current analysis run and that run's ``analysis_extractions`` row;
    G  the media groups that hold a conversation of P: the root of each
       ``duplicate_of_id`` chain (a recursive query over the whole workspace) and every
       member's ``received_at`` and transcribed state.

The rules stay in ``backend.issue_trends``: ``counted_copy_id`` picks the counted copy of
a group, ``judge_conversation`` and ``tally`` count, ``build_rows`` makes the cells.  SQL
returns rows only, never a count, a ratio or a rounding.  A series cuts the window at
local midnights of one timezone; every point is tallied on its own.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import ColumnElement, and_, select, union
from sqlalchemy.orm import Session, aliased

from backend.database.saas_models import (
    AnalysisExtraction,
    AnalysisRun,
    IngestionRecord,
    IngestionSourceType,
    Meeting,
    StageState,
    TranscriptRevision,
)
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    CellKind,
    ConversationFacts,
    ConversationJudgment,
    MediaGroupMember,
    TrendDefinition,
    TrendFilters,
    build_rows,
    counted_copy_id,
    judge_conversation,
    load_definition,
    tally,
    trend_provenance,
)
from backend.security.authorization import RecordAccess
from backend.services.coverage_service import has_segments, transcribable_clauses

TREND_DEFINITION_ID = "trends"
TREND_DEFINITION_VERSION = 1
TREND_GRAINS = ("none", "day", "week", "month")
TREND_DEFAULT_GRAIN = "none"
TREND_DEFAULT_TIMEZONE = "Asia/Tehran"
TREND_CALENDAR = "gregorian"
TREND_WEEK_STARTS_ON = "saturday"
GRAIN_INVALID = "trend_grain_invalid"
TIMEZONE_INVALID = "trend_timezone_invalid"

_SATURDAY = 5  # date.weekday()


class TrendSeriesError(ValueError):
    """A bad ``grain`` or ``timezone``.  The route maps it to a 422."""

    def __init__(self, code: str, key: str, message: str | None = None) -> None:
        super().__init__(message or f"{key} is not valid")
        self.code = code
        self.key = key


def trend_definition() -> TrendDefinition:
    """The pinned definition of every trend answer (cached by ``load_definition``)."""
    return load_definition(TREND_DEFINITION_ID, TREND_DEFINITION_VERSION)


# --- series ---------------------------------------------------------------------------------


@cache
def _zone_names() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_series(grain: object, timezone: object) -> ZoneInfo:
    """Check ``grain`` then ``timezone`` and return the zone.

    A grain is one of ``TREND_GRAINS``.  A timezone is an exact, case-sensitive name of
    ``zoneinfo.available_timezones()``: ``ZoneInfo`` alone accepts ``utc`` on a
    case-insensitive file system and raises ``IsADirectoryError`` for ``America``, so the
    name list gives one answer on every platform and never opens a path from user input.
    A repeated query key arrives as a list and is refused the same way.
    """
    if not isinstance(grain, str) or grain not in TREND_GRAINS:
        raise TrendSeriesError(
            GRAIN_INVALID, "grain", f"grain must be one of: {', '.join(TREND_GRAINS)}"
        )
    if not isinstance(timezone, str) or timezone not in _zone_names():
        raise TrendSeriesError(
            TIMEZONE_INVALID, "timezone", "timezone must be an IANA time zone name"
        )
    return ZoneInfo(timezone)


@dataclass(frozen=True)
class SeriesPoint:
    """``start <= received_at < end``, both in UTC."""

    start: datetime
    end: datetime
    partial: bool  # cut by the window: not a whole day, week or month


def _is_boundary_date(day: date, grain: str) -> bool:
    if grain == "week":
        return day.weekday() == _SATURDAY
    if grain == "month":
        return day.day == 1
    return True


def _first_boundary_date(day: date, grain: str) -> date | None:
    """The first boundary date on or after ``day``; ``None`` past the calendar's end."""
    try:
        if grain == "week":
            return day + timedelta(days=(_SATURDAY - day.weekday()) % 7)
        if grain == "month" and day.day != 1:
            return (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    except OverflowError:
        return None
    return day


def _next_boundary_date(day: date, grain: str) -> date | None:
    try:
        if grain == "day":
            return day + timedelta(days=1)
        if grain == "week":
            return day + timedelta(days=7)
        return (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    except OverflowError:
        return None


def _midnight(day: date, zone: ZoneInfo) -> datetime | None:
    """Local midnight of ``day`` as a UTC instant.  Fold 0 (PEP 495): a midnight in a
    DST gap is the first instant of the local day.  ``None`` outside the UTC range."""
    try:
        return datetime.combine(day, time(), tzinfo=zone).astimezone(UTC)
    except OverflowError:
        return None


def series_points(start: datetime, end: datetime, grain: str, zone: ZoneInfo) -> list[SeriesPoint]:
    """The points of ``[start, end)``: cut at every local boundary strictly inside the
    window (day: midnight; week: Saturday midnight; month: day 1 of the Gregorian month).
    The first point starts at ``start`` and the last ends at ``end``.  ``[]`` for
    ``none``.  A window whose ends cannot be shown in ``zone`` (near year 1 or 9999) is
    refused with ``trend_timezone_invalid``."""
    if grain == "none":
        return []
    try:
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    except OverflowError as exc:
        raise TrendSeriesError(
            TIMEZONE_INVALID, "timezone", "the window cannot be shown in this timezone"
        ) from exc

    def aligned(instant: datetime, local: datetime) -> bool:
        day = local.date()
        return _is_boundary_date(day, grain) and _midnight(day, zone) == instant

    cuts: list[datetime] = []
    day = _first_boundary_date(local_start.date(), grain)
    while day is not None:
        instant = _midnight(day, zone)
        if instant is not None:
            if instant >= end:
                break
            if instant > start:
                cuts.append(instant)
        day = _next_boundary_date(day, grain)
    edges = [start, *cuts, end]
    last = len(edges) - 2
    return [
        SeriesPoint(
            start=edges[index],
            end=edges[index + 1],
            partial=(index == 0 and not aligned(start, local_start))
            or (index == last and not aligned(end, local_end)),
        )
        for index in range(len(edges) - 1)
    ]


# --- loading --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Conversation:
    received_at: datetime  # UTC
    facts: ConversationFacts


def _utc(value: datetime) -> datetime:
    """SQLite gives back a naive ``received_at``; it is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _current_revision(workspace_id: str) -> ColumnElement[bool]:
    return and_(
        TranscriptRevision.meeting_id == Meeting.id,
        TranscriptRevision.is_current.is_(True),
        TranscriptRevision.workspace_id == workspace_id,
    )


def _population_clauses(
    workspace_id: str, access: RecordAccess, filters: TrendFilters
) -> list[ColumnElement[bool]]:
    """The window's transcribable ledger rows, the caller's scope and the population
    filters, all by AND (a filter only narrows).  ``IngestionRecord`` joined to
    ``Meeting``."""
    clauses = [
        *transcribable_clauses(workspace_id),
        IngestionRecord.received_at >= filters.window.start,
        IngestionRecord.received_at < filters.window.end,
        access.meeting_clause(),
    ]
    if filters.team_ids:
        clauses.append(Meeting.team_id.in_(filters.team_ids))
    if filters.agent_user_ids:
        clauses.append(Meeting.agent_user_id.in_(filters.agent_user_ids))
    if filters.languages:
        clauses.append(Meeting.language.in_(filters.languages))
    if filters.source_types:
        clauses.append(
            IngestionRecord.source_type.in_(
                [IngestionSourceType(value) for value in filters.source_types]
            )
        )
    return clauses


def _population_rows(db: Session, workspace_id: str, clauses: Sequence[ColumnElement[bool]]):
    """Statement P: one row per conversation.  The joins cannot repeat a meeting:
    ``uq_ingestion_meeting``, ``uq_transcript_revision_current``,
    ``uq_analysis_run_current`` and ``uq_analysis_extraction_run`` (if one ever broke,
    ``tally`` refuses a meeting judged twice).  Only the current run and its own row."""
    return db.execute(
        select(
            IngestionRecord.id.label("ingestion_id"),
            IngestionRecord.received_at,
            Meeting.id.label("meeting_id"),
            TranscriptRevision.id.label("revision_id"),
            TranscriptRevision.is_mock.label("revision_is_mock"),
            TranscriptRevision.segment_count,
            AnalysisRun.id.label("run_id"),
            AnalysisRun.state.label("run_state"),
            AnalysisRun.transcript_revision_id.label("run_revision_id"),
            AnalysisRun.is_mock.label("run_is_mock"),
            AnalysisExtraction.analysis_run_id.label("extraction_run_id"),
            AnalysisExtraction.result,
        )
        .select_from(IngestionRecord)
        .join(Meeting, Meeting.id == IngestionRecord.meeting_id)
        .outerjoin(TranscriptRevision, _current_revision(workspace_id))
        .outerjoin(
            AnalysisRun,
            and_(
                AnalysisRun.meeting_id == Meeting.id,
                AnalysisRun.is_current.is_(True),
                AnalysisRun.workspace_id == workspace_id,
            ),
        )
        .outerjoin(
            AnalysisExtraction,
            and_(
                AnalysisExtraction.analysis_run_id == AnalysisRun.id,
                AnalysisExtraction.workspace_id == workspace_id,
            ),
        )
        .where(*clauses)
    ).all()


def _group_members(db: Session, workspace_id: str, clauses: Sequence[ColumnElement[bool]]):
    """Statement G: every member of the media groups that hold a row of the window.

    ``up`` walks each linked row of the workspace up its ``duplicate_of_id`` chain;
    ``links`` keeps the ancestor that has no link, the root.  A group is its root plus
    every linked row.  A row with no link that nothing links to is a group of one and is
    not returned.  Members outside the window, the filters and the scope are read here,
    only their ``received_at`` and transcribed state, only to decide the counted copy.

    The recursion is a ``UNION``: it drops a repeated (member, ancestor) pair, so it ends
    even if a manual edit made ``duplicate_of_id`` loop.  A row in a loop, or linked into
    one, finds no root and is a group of one.
    """
    up = (
        select(
            IngestionRecord.id.label("member_id"),
            IngestionRecord.duplicate_of_id.label("ancestor_id"),
        )
        .where(
            IngestionRecord.workspace_id == workspace_id,
            IngestionRecord.duplicate_of_id.is_not(None),
        )
        .cte("trend_media_up", recursive=True)
    )
    parent = aliased(IngestionRecord, name="trend_parent")
    up = up.union(
        select(up.c.member_id, parent.duplicate_of_id)
        .join(parent, parent.id == up.c.ancestor_id)
        .where(parent.workspace_id == workspace_id, parent.duplicate_of_id.is_not(None))
    )
    root = aliased(IngestionRecord, name="trend_root")
    links = (
        select(up.c.member_id, up.c.ancestor_id.label("root_id"))
        .join(root, root.id == up.c.ancestor_id)
        .where(root.workspace_id == workspace_id, root.duplicate_of_id.is_(None))
        .cte("trend_media_links")
    )
    members = union(
        select(links.c.member_id, links.c.root_id),
        select(links.c.root_id.label("member_id"), links.c.root_id),
    ).cte("trend_media_members")
    window_rows = (
        select(IngestionRecord.id)
        .join(Meeting, Meeting.id == IngestionRecord.meeting_id)
        .where(*clauses)
    )
    touched = select(members.c.root_id).where(members.c.member_id.in_(window_rows))
    transcribable = (
        select(
            IngestionRecord.id.label("ingestion_id"),
            TranscriptRevision.segment_count.label("segment_count"),
        )
        .join(Meeting, Meeting.id == IngestionRecord.meeting_id)
        .outerjoin(TranscriptRevision, _current_revision(workspace_id))
        .where(*transcribable_clauses(workspace_id))
        .subquery("trend_transcribable")
    )
    member = aliased(IngestionRecord, name="trend_member")
    return db.execute(
        select(
            members.c.member_id,
            members.c.root_id,
            member.received_at,
            transcribable.c.ingestion_id.label("transcribable_id"),
            transcribable.c.segment_count,
        )
        .select_from(members)
        .join(member, member.id == members.c.member_id)
        .outerjoin(transcribable, transcribable.c.ingestion_id == members.c.member_id)
        .where(members.c.root_id.in_(touched))
    ).all()


def _load_conversations(
    db: Session, *, workspace_id: str, access: RecordAccess, filters: TrendFilters
) -> list[_Conversation]:
    clauses = _population_clauses(workspace_id, access, filters)
    rows = _population_rows(db, workspace_id, clauses)
    groups: dict[str, list[MediaGroupMember]] = defaultdict(list)
    for item in _group_members(db, workspace_id, clauses):
        groups[item.root_id].append(
            MediaGroupMember(
                ingestion_id=item.member_id,
                received_at=_utc(item.received_at),
                transcribed=item.transcribable_id is not None and has_segments(item.segment_count),
            )
        )
    root_of = {member.ingestion_id: root for root, group in groups.items() for member in group}
    counted = {root: counted_copy_id(group) for root, group in groups.items()}

    conversations = []
    for item in rows:
        if not has_segments(item.segment_count):
            continue  # coverage bucket no_transcript
        group_id = root_of.get(item.ingestion_id, item.ingestion_id)
        has_run = item.run_id is not None
        conversations.append(
            _Conversation(
                received_at=_utc(item.received_at),
                facts=ConversationFacts(
                    meeting_id=item.meeting_id,
                    ingestion_id=item.ingestion_id,
                    media_group_id=group_id,
                    is_counted_copy=counted.get(group_id, item.ingestion_id) == item.ingestion_id,
                    current_revision_id=item.revision_id,
                    transcript_is_mock=bool(item.revision_is_mock),
                    analysis_run_id=item.run_id,
                    analysis_state=StageState(item.run_state).value if has_run else None,
                    analysis_revision_id=item.run_revision_id if has_run else None,
                    analysis_is_mock=bool(item.run_is_mock) if has_run else False,
                    extraction_run_id=item.extraction_run_id,
                    extraction=item.result if item.extraction_run_id is not None else None,
                ),
            )
        )
    return conversations


# --- the answer -----------------------------------------------------------------------------


def _block(
    judgments: Iterable[ConversationJudgment],
    definition: TrendDefinition,
    filters: TrendFilters,
    kind: CellKind,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts = tally(judgments, definition)
    rows = build_rows(counts, definition, filters, kind=kind, policy=SAMPLE_POLICY_V1)
    population = {
        "included": counts.included,
        "excluded": {exclusion.value: count for exclusion, count in counts.excluded.items()},
    }
    return population, [row.to_json() for row in rows]


def compute_issue_trends(
    db: Session,
    *,
    workspace_id: str,
    access: RecordAccess,
    filters: TrendFilters,
    now: datetime,
    grain: str = TREND_DEFAULT_GRAIN,
    timezone: str = TREND_DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """The trend answer of the workspace for ``filters`` as ``access`` may see it.

    ``now`` is the clock ``validate_filters`` used for the default window; it must have
    a timezone (``ValueError``) and is not read otherwise.  Raises ``TrendSeriesError``
    for a bad ``grain`` or ``timezone``, before any query.  The cell kind is ``agent``
    when ``filters`` names exactly one ``agent_user_id``, else ``rate``.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    zone = validate_series(grain, timezone)
    definition = trend_definition()
    window = filters.window
    points = series_points(window.start, window.end, grain, zone)
    kind = CellKind.AGENT if len(filters.agent_user_ids) == 1 else CellKind.RATE

    judged = [
        (conversation.received_at, judge_conversation(conversation.facts, definition))
        for conversation in _load_conversations(
            db, workspace_id=workspace_id, access=access, filters=filters
        )
    ]
    population, rows = _block((judgment for _, judgment in judged), definition, filters, kind)

    # Every row of the window lands in exactly one point: an included conversation (its
    # own counted copy) and an excluded duplicate both by their own received_at.
    starts = [point.start for point in points]
    per_point: list[list[ConversationJudgment]] = [[] for _ in points]
    if points:
        for received_at, judgment in judged:
            per_point[bisect_right(starts, received_at) - 1].append(judgment)
    series_answer = []
    for point, judgments in zip(points, per_point, strict=True):
        point_population, point_rows = _block(judgments, definition, filters, kind)
        series_answer.append(
            {
                "from": point.start.astimezone(zone).isoformat(),
                "to": point.end.astimezone(zone).isoformat(),
                "partial": point.partial,
                "population": point_population,
                "rows": point_rows,
            }
        )

    return {
        **trend_provenance(definition, SAMPLE_POLICY_V1),
        "window": window.to_json(),
        "filters": filters.to_json(),
        "scope": {"restricted": access.restricted},
        "series": {
            "grain": grain,
            "timezone": timezone,
            "calendar": TREND_CALENDAR,
            "week_starts_on": TREND_WEEK_STARTS_ON,
        },
        "population": population,
        "rows": rows,
        "points": series_answer,
    }
