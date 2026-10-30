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

HOM-81: ``add_trend_metadata`` adds the ``coverage/1`` counters of the window
(``compute_coverage``, over the same scope and population filters when there are any),
the scorecard block and the missing-data indicators.  ``compute_issue_trends`` stays at
its two statements.

HOM-82: the same two statements serve the role views (``role_trend_service``), and the pure
disclosure rules live here (section "disclosure"): the cell kind comes from the agents of a
cohort, an agent cohort is all or nothing per layer, team cohorts come from one canonical
released set, and organization views decide per workspace and week.

HOM-83: ``issue_cohort`` is the decision of ``GET /trends/issues``; the view and the
supporting-conversation links (``trend_link_service``) both call it.

HOM-290: P also reads each current run's stored judgment of the current rules
(``trend_judgment_store``) and then needs no JSON for that run.  One more statement, S, reads
one representative extraction per distinct stored key; its judged content is every run's of
the key.  S runs only when P found a stored judgment.  A run without one is judged as before.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import (
    CTE,
    ColumnElement,
    and_,
    case,
    null,
    or_,
    select,
    type_coerce,
    union,
)
from sqlalchemy.orm import Session, aliased

from backend.database.saas_models import (
    AnalysisExtraction,
    AnalysisRun,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    Meeting,
    StageState,
    TranscriptRevision,
)
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    CellKind,
    ConversationFacts,
    ConversationJudgment,
    IssueState,
    JudgedContent,
    MediaGroupMember,
    SignatureJudge,
    TrendDefinition,
    TrendFilters,
    build_rows,
    counted_copy_id,
    load_definition,
    tally,
    trend_provenance,
)
from backend.issue_trends.samples import DISCLOSURE_VERSION, MIN_COHORT_AGENTS
from backend.security.authorization import RecordAccess
from backend.services.aggregate_metadata import scorecard_block, trend_missing_data
from backend.services.coverage_service import (
    COVERAGE_VERSION,
    CoverageWindow,
    among_ids,
    compute_coverage,
    has_segments,
    transcribable_clauses,
)
from backend.services.trend_judgment_cache import JudgmentCache, judgment_key
from backend.services.trend_judgment_store import current_judgments, stored_contents

TREND_DEFINITION_ID = "trends"
TREND_DEFINITION_VERSION = 1
TREND_GRAINS = ("none", "day", "week", "month")
TREND_DEFAULT_GRAIN = "none"
TREND_DEFAULT_TIMEZONE = "Asia/Tehran"
TREND_CALENDAR = "gregorian"
TREND_WEEK_STARTS_ON = "saturday"
GRAIN_INVALID = "trend_grain_invalid"
TIMEZONE_INVALID = "trend_timezone_invalid"
COVERAGE_BASIS_WORKSPACE = "workspace"
COVERAGE_BASIS_TREND_POPULATION = "trend_population"

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
class JudgedConversation:
    """One conversation of a loaded scope, judged once (HOM-82 splits cohorts from it)."""

    received_at: datetime  # UTC
    team_id: str | None
    agent_user_id: str | None
    language: str
    source_type: str  # an IngestionSourceType value
    judgment: ConversationJudgment
    # The current run that was judged (HOM-83 reads its evidence).  No decision reads it.
    analysis_run_id: str | None = None

    @property
    def included(self) -> bool:
        return self.judgment.exclusion is None


def _utc(value: datetime) -> datetime:
    """SQLite gives back a naive ``received_at``; it is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _current_revision(workspace_id: str) -> ColumnElement[bool]:
    return and_(
        TranscriptRevision.meeting_id == Meeting.id,
        TranscriptRevision.is_current.is_(True),
        TranscriptRevision.workspace_id == workspace_id,
    )


def filter_clauses(
    scope: ColumnElement[bool], filters: TrendFilters, *, teams: bool = True
) -> list[ColumnElement[bool]]:
    """The scope clause and the population filters, by AND (a filter only narrows).
    ``IngestionRecord`` joined to ``Meeting``.  The trend population and its coverage
    (HOM-81) share them.  ``teams=False`` leaves out the ``team_id`` filter: HOM-82 loads
    the whole scope and splits teams in Python."""
    clauses = [scope]
    if teams and filters.team_ids:
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


def _scope_clauses(access: RecordAccess, filters: TrendFilters) -> list[ColumnElement[bool]]:
    return filter_clauses(access.meeting_clause(), filters)


def _population_clauses(
    workspace_id: str, scope_clauses: Sequence[ColumnElement[bool]], filters: TrendFilters
) -> list[ColumnElement[bool]]:
    """The window's transcribable ledger rows and the scope clauses, all by AND.
    ``IngestionRecord`` joined to ``Meeting``."""
    return [
        *transcribable_clauses(workspace_id),
        IngestionRecord.received_at >= filters.window.start,
        IngestionRecord.received_at < filters.window.end,
        *scope_clauses,
    ]


def _population_rows(
    db: Session,
    workspace_id: str,
    clauses: Sequence[ColumnElement[bool]],
    cached_runs: Sequence[str] | None = None,
    *,
    results: bool = True,
    stored: CTE | None = None,
):
    """Statement P: one row per conversation.  The joins cannot repeat a meeting:
    ``uq_ingestion_meeting``, ``uq_transcript_revision_current``,
    ``uq_analysis_run_current``, ``uq_analysis_extraction_run`` and ``uq_trend_judgment_run``
    (if one ever broke, ``tally`` refuses a meeting judged twice).  Only the current run and
    its own row.

    ``stored`` (HOM-290, ``trend_judgment_store.current_judgments``): the workspace's stored
    judgments of the current rules, hash-joined on the current run.  ``content_key`` is the
    run's stored key, or NULL.  For a run with a key, ``result`` is NULL and
    ``extraction_run_id`` is the run (a stored judgment means the row exists).

    ``cached_runs`` (HOM-312, the judgment cache): the runs whose stored row the caller
    already judged.  For them ``result`` is NULL and ``extraction_run_id`` is the run (a
    cached judgment means the row exists; rows are never removed while their run lives).
    With ``stored`` or ``cached_runs``, the other runs read their row with a subquery, so the
    wide extraction table is read only for them.  Still one statement: the ids are one
    parameter.

    ``results=False`` (the warm-up): no ``result`` column, so no JSON is read or decoded."""
    columns = [
        IngestionRecord.id.label("ingestion_id"),
        IngestionRecord.received_at,
        IngestionRecord.source_type,
        Meeting.id.label("meeting_id"),
        Meeting.team_id,
        Meeting.agent_user_id,
        Meeting.language,
        TranscriptRevision.id.label("revision_id"),
        TranscriptRevision.is_mock.label("revision_is_mock"),
        TranscriptRevision.segment_count,
        AnalysisRun.id.label("run_id"),
        AnalysisRun.state.label("run_state"),
        AnalysisRun.transcript_revision_id.label("run_revision_id"),
        AnalysisRun.is_mock.label("run_is_mock"),
    ]
    statement = (
        select(*columns)
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
    )
    known: list[ColumnElement[bool]] = []  # runs whose row is not read
    if stored is not None:
        statement = statement.add_columns(stored.c.content_key).outerjoin(
            stored, stored.c.analysis_run_id == AnalysisRun.id
        )
        known.append(stored.c.content_key.is_not(None))
    if cached_runs is not None:
        known.append(_among_runs(db, cached_runs))
    if not known:
        extraction = [AnalysisExtraction.analysis_run_id.label("extraction_run_id")]
        if results:
            extraction.append(AnalysisExtraction.result)
        statement = statement.add_columns(*extraction).outerjoin(
            AnalysisExtraction,
            and_(
                AnalysisExtraction.analysis_run_id == AnalysisRun.id,
                AnalysisExtraction.workspace_id == workspace_id,
            ),
        )
    else:
        skip = known[0] if len(known) == 1 else or_(*known)
        own_row = and_(
            AnalysisExtraction.analysis_run_id == AnalysisRun.id,
            AnalysisExtraction.workspace_id == workspace_id,
        )
        statement = statement.add_columns(
            case(
                (skip, AnalysisRun.id),
                else_=select(AnalysisExtraction.analysis_run_id).where(own_row).scalar_subquery(),
            ).label("extraction_run_id"),
        )
        if results:
            statement = statement.add_columns(
                type_coerce(
                    case(
                        (skip, null()),
                        else_=select(AnalysisExtraction.result).where(own_row).scalar_subquery(),
                    ),
                    AnalysisExtraction.result.type,
                ).label("result"),
            )
    return db.execute(statement.where(*clauses)).all()


def _among_runs(db: Session, run_ids: Sequence[str]) -> ColumnElement[bool]:
    """``AnalysisRun.id`` is one of ``run_ids``, sent as one statement parameter."""
    return among_ids(db, AnalysisRun.id, run_ids, "trend_cached_runs")


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


def judge_population(
    db: Session,
    *,
    workspace_id: str,
    scope: ColumnElement[bool],
    filters: TrendFilters,
    teams: bool = True,
    judge: SignatureJudge | None = None,
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> list[JudgedConversation]:
    """Statements P and G for the window's population under ``scope`` and the population
    filters (``teams=False`` leaves out ``team_id``), every conversation judged once.

    HOM-312: ``judge`` (a ``SignatureJudge`` of the trend definition, a new one when None)
    gives judgments equal to ``judge_conversation``'s, sharing equal parts.  With ``cache``
    (the app's ``JudgmentCache``; ``organization_id`` names the workspace's organization in
    its key), the stored rows of runs judged before are not read again: only their cached
    ``JudgedContent``, pinned for this request, is used."""
    definition = trend_definition()
    with cache.request() if cache is not None else nullcontext():
        return _judge_population(
            db,
            workspace_id=workspace_id,
            scope=scope,
            filters=filters,
            teams=teams,
            judge=judge,
            cache=cache,
            organization_id=organization_id,
            definition=definition,
        )


def _judge_population(
    db: Session,
    *,
    workspace_id: str,
    scope: ColumnElement[bool],
    filters: TrendFilters,
    teams: bool,
    judge: SignatureJudge | None,
    cache: JudgmentCache | None,
    organization_id: str | None,
    definition: TrendDefinition,
) -> list[JudgedConversation]:
    pinned: dict[str, JudgedContent] = {}
    key = judgment_key(organization_id, workspace_id, definition)
    if cache is not None:
        judge = cache.judge(definition)
        pinned = cache.pin(key, filters.window.start, filters.window.end)
    elif judge is None:
        judge = SignatureJudge(definition)
    clauses = _population_clauses(
        workspace_id, filter_clauses(scope, filters, teams=teams), filters
    )
    rows = _population_rows(
        db,
        workspace_id,
        clauses,
        None if cache is None else sorted(pinned),
        stored=current_judgments(workspace_id, definition),
    )
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

    # HOM-290, statement S: the runs with a stored judgment of the current rules take the
    # content of their key, judged once per key.  ``unstored``: a stored judgment whose run has
    # no extraction row (against the invariant), read as no row.
    keys = {
        item.run_id: item.content_key
        for item in rows
        if has_segments(item.segment_count) and item.content_key is not None
    }
    contents: dict[str, JudgedContent] = {}
    unstored: set[str] = set()
    if keys:
        contents, unstored = stored_contents(
            db, workspace_id=workspace_id, keys=keys, build=judge.content
        )
    if cache is not None:
        hits: list[str] = []
        misses: dict[str, tuple[datetime, object]] = {}
        for item in rows:
            if not has_segments(item.segment_count) or item.extraction_run_id is None:
                continue
            if item.run_id in keys:
                continue  # stored: never the cache
            if item.run_id in pinned:
                hits.append(item.run_id)
            else:
                misses[item.run_id] = (_utc(item.received_at), item.result)
        contents.update({run_id: pinned[run_id] for run_id in hits})
        contents.update(cache.resolve(key, hits=hits, misses=misses, build=judge.content))

    conversations = []
    for item in rows:
        if not has_segments(item.segment_count):
            continue  # coverage bucket no_transcript
        group_id = root_of.get(item.ingestion_id, item.ingestion_id)
        has_run = item.run_id is not None
        extraction_run_id = None if item.run_id in unstored else item.extraction_run_id
        facts = ConversationFacts(
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
            extraction_run_id=extraction_run_id,
            extraction=item.result if extraction_run_id is not None else None,
        )
        conversations.append(
            JudgedConversation(
                received_at=_utc(item.received_at),
                team_id=item.team_id,
                agent_user_id=item.agent_user_id,
                language=item.language,
                source_type=IngestionSourceType(item.source_type).value,
                judgment=judge.judge(
                    facts, contents.get(item.run_id) if extraction_run_id else None
                ),
                analysis_run_id=item.run_id,
            )
        )
    return conversations


def warm_judgments(
    db: Session,
    *,
    cache: JudgmentCache,
    workspace_id: str,
    organization_id: str | None,
    start: datetime,
    end: datetime,
    batch: int = 256,
    between: Callable[[], None] | None = None,
) -> int:
    """The warm-up's work for one workspace (HOM-312, ``trend_judgment_warmup``): statement P over
    every transcribable conversation received in ``[start, end)``, with the runs already in
    ``cache`` and the runs with a stored judgment of the current rules (HOM-290: a request
    reads those through their key, never the cache) left out, and the stored rows of the
    others judged with the cache's ``SignatureJudge`` and kept, the most recent first.  The
    workspace must be bound as the tenant.  Returns the number of entries added.

    Statement P runs without the JSON (``results=False``): it only chooses the runs, the
    newest first, at most the free room.  They go to ``cache.resolve`` in batches of at most
    ``batch``, each also at most the free room, with ``evict=False``: the warm-up never
    evicts, and it stops when the cache is full.  Each batch reads and decodes only its own
    stored rows (one statement, the run ids as one parameter).  Before each batch the read
    transaction is ended and ``between`` runs: there the warm-up may wait for requests, and
    it holds no transaction, no claim and no decoded row, only the chosen run ids and times.
    At the end the runs it added move to the recent end of the map, oldest first
    (``JudgmentCache.touch``), so a request that must evict takes the oldest first."""
    definition = trend_definition()
    key = judgment_key(organization_id, workspace_id, definition)
    clauses = [
        *transcribable_clauses(workspace_id),
        IngestionRecord.received_at >= start,
        IngestionRecord.received_at < end,
    ]
    pinned = cache.pin(key, start, end)
    chosen = sorted(
        (
            (_utc(item.received_at), item.run_id)
            for item in _population_rows(
                db,
                workspace_id,
                clauses,
                results=False,
                stored=current_judgments(workspace_id, definition),
            )
            if has_segments(item.segment_count)
            and item.extraction_run_id is not None
            and item.content_key is None
            and item.run_id not in pinned
        ),
        reverse=True,
    )[: cache.room()]
    build = cache.judge(definition).content
    added = done = 0
    while done < len(chosen):
        db.rollback()  # no open transaction while waiting (the tenant is bound again on use)
        if between is not None:
            between()
        size = min(batch, cache.room())
        if size <= 0:
            break  # full
        part = chosen[done : done + size]
        done += size
        rows = dict(
            db.execute(
                select(AnalysisExtraction.analysis_run_id, AnalysisExtraction.result).where(
                    AnalysisExtraction.workspace_id == workspace_id,
                    among_ids(
                        db,
                        AnalysisExtraction.analysis_run_id,
                        [run_id for _at, run_id in part],
                        "trend_warm_runs",
                    ),
                )
            ).all()
        )
        chunk = {run_id: (at, rows[run_id]) for at, run_id in part if run_id in rows}
        cache.resolve(key, hits=(), misses=chunk, build=build, evict=False)
        added += cache.held(key, chunk)
    cache.touch(key, [run_id for _at, run_id in reversed(chosen[:done])])
    return added


def scope_meeting_ids(
    db: Session, *, workspace_id: str, scope: ColumnElement[bool], filters: TrendFilters
) -> frozenset[str]:
    """The meetings of the window's population under ``scope`` (HOM-82, A6: what a caller
    knows through a restricted workspace membership).  One statement."""
    clauses = _population_clauses(workspace_id, filter_clauses(scope, filters), filters)
    return frozenset(
        db.scalars(
            select(Meeting.id)
            .select_from(IngestionRecord)
            .join(Meeting, Meeting.id == IngestionRecord.meeting_id)
            .where(*clauses)
        )
    )


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


def point_finder(points: Sequence[SeriesPoint]) -> Callable[[datetime], int]:
    """The index of the point that holds ``received_at``: a row of the window lands in
    exactly one point, an included conversation and an excluded duplicate both by their
    own ``received_at``."""
    starts = [point.start for point in points]
    return lambda received_at: bisect_right(starts, received_at) - 1


def cohort_parts(
    conversations: Sequence[JudgedConversation],
    definition: TrendDefinition,
    filters: TrendFilters,
    kind: CellKind,
    points: Sequence[SeriesPoint],
    zone: ZoneInfo,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """``population``, ``rows`` and ``points`` of one cohort."""
    population, rows = _block((c.judgment for c in conversations), definition, filters, kind)
    per_point: list[list[ConversationJudgment]] = [[] for _ in points]
    if points:
        index = point_finder(points)
        for conversation in conversations:
            per_point[index(conversation.received_at)].append(conversation.judgment)
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
    return population, rows, series_answer


def series_block(grain: str, timezone: str) -> dict[str, str]:
    return {
        "grain": grain,
        "timezone": timezone,
        "calendar": TREND_CALENDAR,
        "week_starts_on": TREND_WEEK_STARTS_ON,
    }


# --- disclosure (HOM-82) --------------------------------------------------------------------
#
# Pure rules of the role views (README "HOM-82: role views and disclosure").  The kind of a
# cohort comes from its agents, an agent cohort is all or nothing per layer, and team cohorts
# are released from one canonical set per caller, window and non-team filters.  Organization
# views decide per workspace and week.  Nothing here reads the database or the clock.

LAYER_WINDOW = "window"
LAYER_POINTS = "points"
STATUS_RELEASED = "released"
STATUS_MASKED = "masked"
STATUS_WITHHELD = "withheld"
REASON_SMALL_GROUP = "small_group"
REASON_COMPLEMENTARY = "complementary_disclosure"
CELL_WITHHELD = "withheld"

RowKey = tuple[str, str]  # ("family", id) or ("issue", id)
_IN_DENOMINATOR = frozenset({IssueState.COUNTED, IssueState.PENDING_REVIEW, IssueState.ABSENT})


def disclosure_policy() -> dict[str, Any]:
    return {"version": DISCLOSURE_VERSION, "min_cohort_agents": MIN_COHORT_AGENTS}


def denominator_rows(judgment: ConversationJudgment) -> frozenset[RowKey]:
    """The rows (every available family and active issue) whose denominator holds this
    conversation: state counted, pending_review or absent."""
    return frozenset(
        {("issue", i.issue_id) for i in judgment.issues if i.state in _IN_DENOMINATOR}
        | {("family", f.family_id) for f in judgment.families if f.state in _IN_DENOMINATOR}
    )


def _denominator_rows_by_shape() -> Callable[[ConversationJudgment], frozenset[RowKey]]:
    """``denominator_rows``, worked out once per issue and family tuple (HOM-312): equal
    judgments of a ``SignatureJudge`` share their tuples.  For one loop only: the tuples it
    keys on are kept alive with the cached rows, so their ids stay unique."""
    cache: dict[tuple[int, int], tuple[ConversationJudgment, frozenset[RowKey]]] = {}

    def rows_of(judgment: ConversationJudgment) -> frozenset[RowKey]:
        shape = (id(judgment.issues), id(judgment.families))
        cached = cache.get(shape)
        if cached is None:
            cached = cache[shape] = (judgment, denominator_rows(judgment))
        return cached[1]

    return rows_of


@dataclass(frozen=True)
class Layer:
    """The cells of one layer: per row, its denominator, and whether it holds a
    conversation with an agent (other than the viewer, for a stats built without them)."""

    denominators: Mapping[RowKey, int]
    with_agent: frozenset[RowKey]

    def unite(self, other: Layer) -> Layer:
        keys = set(self.denominators) | set(other.denominators)
        return Layer(
            {key: self.denominators.get(key, 0) + other.denominators.get(key, 0) for key in keys},
            self.with_agent | other.with_agent,
        )


EMPTY_LAYER = Layer({}, frozenset())


@dataclass(frozen=True)
class CohortStats:
    """What the disclosure rules read about a set of conversations: the included count,
    the distinct agents at window level, and the window and point layers."""

    included: int
    agents: frozenset[str]
    window: Layer
    points: tuple[Layer, ...]

    def unite(self, other: CohortStats) -> CohortStats:
        length = max(len(self.points), len(other.points))
        mine = (*self.points, *[EMPTY_LAYER] * (length - len(self.points)))
        theirs = (*other.points, *[EMPTY_LAYER] * (length - len(other.points)))
        return CohortStats(
            self.included + other.included,
            self.agents | other.agents,
            self.window.unite(other.window),
            tuple(a.unite(b) for a, b in zip(mine, theirs, strict=True)),
        )


def empty_stats(n_points: int = 0) -> CohortStats:
    return CohortStats(0, frozenset(), EMPTY_LAYER, (EMPTY_LAYER,) * n_points)


def cohort_stats(
    conversations: Iterable[JudgedConversation],
    *,
    n_points: int = 0,
    point_of: Callable[[datetime], int] | None = None,
    without_agent: str | None = None,
) -> CohortStats:
    """The stats of the included conversations.  ``without_agent`` leaves out the
    conversations of that agent (the viewer's own calls)."""
    included = 0
    agents: set[str] = set()
    window: dict[RowKey, int] = defaultdict(int)
    window_agent: set[RowKey] = set()
    points: list[dict[RowKey, int]] = [defaultdict(int) for _ in range(n_points)]
    points_agent: list[set[RowKey]] = [set() for _ in range(n_points)]
    rows_of = _denominator_rows_by_shape()
    for conversation in conversations:
        if not conversation.included:
            continue
        agent = conversation.agent_user_id
        if without_agent is not None and agent == without_agent:
            continue
        included += 1
        if agent is not None:
            agents.add(agent)
        rows = rows_of(conversation.judgment)
        index = point_of(conversation.received_at) if point_of is not None and n_points else None
        for key in rows:
            window[key] += 1
            if index is not None:
                points[index][key] += 1
        if agent is not None:
            window_agent |= rows
            if index is not None:
                points_agent[index] |= rows
    return CohortStats(
        included,
        frozenset(agents),
        Layer(dict(window), frozenset(window_agent)),
        tuple(
            Layer(dict(layer), frozenset(flags))
            for layer, flags in zip(points, points_agent, strict=True)
        ),
    )


def cohort_kind(
    agents: frozenset[str], viewer: str | None, *, self_view: bool = False
) -> tuple[CellKind, bool]:
    """The kind of a cohort on a workspace view, and whether the all-or-nothing rule
    applies.  No agent: rate.  Only the viewer (the self-view always): agent, no extra rule.
    1 to 4 agents other than the viewer: agent, all or nothing.  Else rate."""
    if self_view:
        return CellKind.AGENT, False
    others = agents - {viewer} if viewer is not None else agents
    if not agents:
        return CellKind.RATE, False
    if not others:
        return CellKind.AGENT, False
    if len(others) < MIN_COHORT_AGENTS:
        return CellKind.AGENT, True
    return CellKind.RATE, False


def _hides(layer: Layer, minimum: int) -> bool:
    return any(0 < denominator < minimum for denominator in layer.denominators.values())


def agent_masks(
    cohort: CohortStats,
    without_viewer: CohortStats | None,
    *,
    has_points: bool,
    minimum: int = SAMPLE_POLICY_V1.min_denominator_agent,
) -> frozenset[str]:
    """All or nothing for an agent cohort seen by someone other than its only agent: the
    layers to withhold.  ``without_viewer`` is the cohort without the viewer's calls (what
    they get by subtracting their own), tested too when given.  A withheld window layer
    always withholds the points layer."""
    tests = [cohort] if without_viewer is None else [cohort, without_viewer]
    if any(_hides(test.window, minimum) for test in tests):
        return frozenset({LAYER_WINDOW, LAYER_POINTS} if has_points else {LAYER_WINDOW})
    if has_points and any(_hides(point, minimum) for test in tests for point in test.points):
        return frozenset({LAYER_POINTS})
    return frozenset()


def remainder_unsafe(
    remainder: CohortStats, layer: str, *, minimum: int = SAMPLE_POLICY_V1.min_denominator_agent
) -> bool:
    """A remainder (built without the viewer's calls) is unsafe for a layer when it holds 1
    to 4 agents and a cell of that layer is under the minimum and holds an agent."""
    if not 1 <= len(remainder.agents) < MIN_COHORT_AGENTS:
        return False
    layers = (remainder.window,) if layer == LAYER_WINDOW else remainder.points
    return any(
        0 < denominator < minimum and key in cells.with_agent
        for cells in layers
        for key, denominator in cells.denominators.items()
    )


@dataclass(frozen=True)
class TeamPart:
    """One team of a scope: its included count, its stats without the viewer's calls and
    the layers the all-or-nothing rule withheld."""

    included: int
    others: CohortStats
    masked: frozenset[str]


@dataclass(frozen=True)
class TeamRelease:
    teams: frozenset[str]  # every team id with a conversation in the scope
    window: frozenset[str]  # the canonical released set, window layer
    points: frozenset[str]  # and points layer (empty without points)


def canonical_release(
    teams: Mapping[str, TeamPart],
    rest: CohortStats,
    *,
    has_points: bool,
    minimum: int = SAMPLE_POLICY_V1.min_denominator_agent,
) -> TeamRelease:
    """The released teams of one scope (4.2): over every team, whatever a request names.
    ``rest``: the conversations with no team, without the viewer's calls.  While the
    remainder is unsafe, the team with the fewest included conversations (then the smallest
    id) moves into it; the points pass runs the same way over the window-released teams."""

    def run(candidates: list[str], layer: str) -> list[str]:
        remainder = rest
        for team_id in teams:
            if team_id not in candidates:
                remainder = remainder.unite(teams[team_id].others)
        released = list(candidates)
        while released and remainder_unsafe(remainder, layer, minimum=minimum):
            remainder = remainder.unite(teams[released.pop(0)].others)
        return released

    order = sorted(teams, key=lambda team_id: (teams[team_id].included, team_id))
    window = run([t for t in order if LAYER_WINDOW not in teams[t].masked], LAYER_WINDOW)
    points = (
        run([t for t in window if LAYER_POINTS not in teams[t].masked], LAYER_POINTS)
        if has_points
        else []
    )
    return TeamRelease(frozenset(teams), frozenset(window), frozenset(points))


def team_filter_masks(
    requested: frozenset[str], release: TeamRelease, *, has_points: bool
) -> frozenset[str]:
    """The layers withheld for a cohort of the ``requested`` teams (one team on
    ``/trends/teams``, the union on ``/trends/issues?team_id=``): the window when a requested
    team with data is not in the canonical window set, the points when one is not in the
    points set.  A requested team with no conversation in the scope adds nothing."""
    present = requested & release.teams
    if not present <= release.window:
        return frozenset({LAYER_WINDOW, LAYER_POINTS} if has_points else {LAYER_WINDOW})
    if has_points and not present <= release.points:
        return frozenset({LAYER_POINTS})
    return frozenset()


def team_parts(
    conversations: Sequence[JudgedConversation],
    viewer: str | None,
    *,
    n_points: int,
    point_of: Callable[[datetime], int] | None,
) -> tuple[dict[str, TeamPart], CohortStats]:
    """Every team of a loaded scope as a ``TeamPart``, and the no-team rest (without the
    viewer's calls)."""
    by_team: dict[str | None, list[JudgedConversation]] = defaultdict(list)
    for conversation in conversations:
        by_team[conversation.team_id].append(conversation)
    parts: dict[str, TeamPart] = {}
    for team_id, members in by_team.items():
        if team_id is None:
            continue
        everyone = cohort_stats(members, n_points=n_points, point_of=point_of)
        others = cohort_stats(members, n_points=n_points, point_of=point_of, without_agent=viewer)
        parts[team_id] = TeamPart(
            everyone.included,
            others,
            cohort_masks(everyone, others, viewer, has_points=bool(n_points)),
        )
    rest = cohort_stats(
        by_team.get(None, []), n_points=n_points, point_of=point_of, without_agent=viewer
    )
    return parts, rest


def cohort_masks(
    everyone: CohortStats,
    others: CohortStats,
    viewer: str | None,
    *,
    has_points: bool,
    self_view: bool = False,
) -> frozenset[str]:
    """The layers 4.1 withholds for one cohort (``others``: without the viewer's calls)."""
    _kind, gated = cohort_kind(everyone.agents, viewer, self_view=self_view)
    if not gated:
        return frozenset()
    viewer_inside = viewer is not None and viewer in everyone.agents
    return agent_masks(everyone, others if viewer_inside else None, has_points=has_points)


def _withheld_row(row: dict[str, Any]) -> dict[str, Any]:
    cell = row["cell"]
    if cell["status"] == "not_computable" or (
        cell["status"] == "insufficient_sample" and cell["numerator"] is None
    ):
        return row  # nothing to hide, or hidden already (HOM-79)
    return {
        **row,
        "cell": {
            **cell,
            "status": CELL_WITHHELD,
            "numerator": None,
            "ratio": None,
            "reason": REASON_COMPLEMENTARY,
        },
        "pending_review": None,
        "absent_pending_review": None,
    }


def apply_masks(
    rows: list[dict[str, Any]], points: list[dict[str, Any]], masked: frozenset[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``rows`` and ``points`` with the masked layers withheld."""
    if LAYER_WINDOW in masked:
        rows = [_withheld_row(row) for row in rows]
    if LAYER_POINTS in masked:
        points = [{**point, "rows": [_withheld_row(r) for r in point["rows"]]} for point in points]
    return rows, points


def disclosure_block(kind: CellKind, masked: frozenset[str]) -> dict[str, Any]:
    return {
        "status": STATUS_MASKED if masked else STATUS_RELEASED,
        "reason": REASON_COMPLEMENTARY if masked else None,
        "kind": kind.value,
        "masked": sorted(masked),
    }


def withheld_block() -> dict[str, Any]:
    """``disclosure`` of a withheld organization cohort; the reason is set by the caller."""
    return {"status": STATUS_WITHHELD, "reason": REASON_SMALL_GROUP, "kind": None, "masked": []}


# Organization views (4.3): distinct agents capped at MIN_COHORT_AGENTS ids decide 0, 1 to 4
# or at least 5, and two capped sets still unite correctly (A5).


def capped(ids: Iterable[str]) -> frozenset[str]:
    distinct = set(ids)
    if len(distinct) > MIN_COHORT_AGENTS:
        return frozenset(sorted(distinct)[:MIN_COHORT_AGENTS])
    return frozenset(distinct)


@dataclass(frozen=True)
class WeekStats:
    """One cohort in one week: included conversations, their agents (capped) and the
    agents (capped) of every row's denominator."""

    included: int
    agents: frozenset[str]
    rows: Mapping[RowKey, frozenset[str]]

    def unite(self, other: WeekStats) -> WeekStats:
        keys = set(self.rows) | set(other.rows)
        return WeekStats(
            self.included + other.included,
            capped(self.agents | other.agents),
            {
                key: capped(self.rows.get(key, frozenset()) | other.rows.get(key, frozenset()))
                for key in keys
            },
        )


EMPTY_WEEK = WeekStats(0, frozenset(), {})


def week_stats(conversations: Iterable[JudgedConversation]) -> WeekStats:
    included = 0
    agents: set[str] = set()
    rows: dict[RowKey, set[str]] = defaultdict(set)
    rows_of = _denominator_rows_by_shape()
    for conversation in conversations:
        if not conversation.included:
            continue
        included += 1
        agent = conversation.agent_user_id
        if agent is None:
            continue
        agents.add(agent)
        for key in rows_of(conversation.judgment):
            rows[key].add(agent)
    return WeekStats(included, capped(agents), {key: capped(ids) for key, ids in rows.items()})


def week_passes(week: WeekStats) -> bool:
    """0 or at least 5 distinct agents, in the included conversations and in the
    denominator of every row (whatever the selectors)."""

    def enough(ids: frozenset[str]) -> bool:
        return not ids or len(ids) >= MIN_COHORT_AGENTS

    return enough(week.agents) and all(enough(ids) for ids in week.rows.values())


def _unite_weeks(parts: Iterable[WeekStats]) -> WeekStats:
    united = EMPTY_WEEK
    for part in parts:
        united = united.unite(part)
    return united


def weekly_release(weeks: Sequence[Mapping[str | None, WeekStats]]) -> list[frozenset[str]]:
    """``Rel_k`` per week: the teams (every team id with data that week, active or not)
    that pass the week test, or none when the workspace minus them does not pass.  ``None``
    is the part with no team."""
    released = []
    for parts in weeks:
        passing = {
            team_id
            for team_id, stats in parts.items()
            if team_id is not None and stats.included and week_passes(stats)
        }
        rest = _unite_weeks(stats for team_id, stats in parts.items() if team_id not in passing)
        if rest.included and not week_passes(rest):
            passing = set()
        released.append(frozenset(passing))
    return released


def organization_release(
    weeks: Sequence[Mapping[str | None, WeekStats]],
    released: Sequence[frozenset[str]],
    teams: frozenset[str] | None,
) -> tuple[bool, str | None]:
    """Whether a block is released for the window, and else why.  ``teams=None`` is the
    whole workspace: it passes the week test every week.  A team block or a team-grant
    block: in every week, each of its teams with data is in ``Rel_k`` (an empty team-week
    passes)."""
    small = blocked = False
    for parts, rel in zip(weeks, released, strict=True):
        if teams is None:
            small = small or not week_passes(_unite_weeks(parts.values()))
            continue
        present = {t for t in teams if t in parts and parts[t].included}
        own = _unite_weeks(parts[t] for t in present)
        small = small or (own.included > 0 and not week_passes(own))
        blocked = blocked or not present <= rel
    if small:
        return False, REASON_SMALL_GROUP
    if blocked:
        return False, REASON_COMPLEMENTARY
    return True, None


@dataclass(frozen=True)
class CohortDecision:
    """The cohort of one workspace trend view and what HOM-82 withholds of it.  The view
    and the HOM-83 links both build their cells from it."""

    conversations: Sequence[JudgedConversation]
    kind: CellKind
    masked: frozenset[str]


def issue_cohort(
    db: Session,
    *,
    workspace_id: str,
    access: RecordAccess,
    filters: TrendFilters,
    points: Sequence[SeriesPoint],
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> CohortDecision:
    """The cohort of ``GET /trends/issues`` and its decision (statements P and G).  The kind
    comes from the agents of the population (``cohort_kind``), not from the filter, and an
    agent cohort is all or nothing per layer.  With ``team_id`` the whole scope is loaded and
    the cohort is released only when every requested team with data is in the canonical
    released set."""
    point_of = point_finder(points) if points else None
    viewer = access.user_id
    requested = frozenset(filters.team_ids)

    judged = judge_population(
        db,
        workspace_id=workspace_id,
        scope=access.meeting_clause(),
        filters=filters,
        teams=not requested,
        cache=cache,
        organization_id=organization_id,
    )
    cohort = [c for c in judged if c.team_id in requested] if requested else judged
    n_points = len(points)
    everyone = cohort_stats(cohort, n_points=n_points, point_of=point_of)
    kind, _gated = cohort_kind(everyone.agents, viewer)
    masked = set(
        cohort_masks(
            everyone,
            cohort_stats(cohort, n_points=n_points, point_of=point_of, without_agent=viewer),
            viewer,
            has_points=bool(points),
        )
    )
    if requested:
        parts, rest = team_parts(judged, viewer, n_points=n_points, point_of=point_of)
        release = canonical_release(parts, rest, has_points=bool(points))
        masked |= team_filter_masks(requested, release, has_points=bool(points))
    return CohortDecision(cohort, kind, frozenset(masked))


def compute_issue_trends(
    db: Session,
    *,
    workspace_id: str,
    access: RecordAccess,
    filters: TrendFilters,
    now: datetime,
    grain: str = TREND_DEFAULT_GRAIN,
    timezone: str = TREND_DEFAULT_TIMEZONE,
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """The trend answer of the workspace for ``filters`` as ``access`` may see it.

    ``now`` is the clock ``validate_filters`` used for the default window; it must have
    a timezone (``ValueError``) and is not read otherwise.  Raises ``TrendSeriesError``
    for a bad ``grain`` or ``timezone``, before any query.

    HOM-82: the cell kind comes from the agents of the population (``cohort_kind``), not
    from the filter, and an agent cohort is all or nothing per layer.  With ``team_id`` the
    whole scope is loaded (still two statements) and the answer is released only when every
    requested team with data is in the canonical released set (``issue_cohort``).
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a datetime with a timezone")
    zone = validate_series(grain, timezone)
    definition = trend_definition()
    window = filters.window
    points = series_points(window.start, window.end, grain, zone)
    decision = issue_cohort(
        db,
        workspace_id=workspace_id,
        access=access,
        filters=filters,
        points=points,
        cache=cache,
        organization_id=organization_id,
    )
    population, rows, series_answer = cohort_parts(
        decision.conversations, definition, filters, decision.kind, points, zone
    )
    rows, series_answer = apply_masks(rows, series_answer, decision.masked)

    return {
        **trend_provenance(definition, SAMPLE_POLICY_V1),
        "disclosure_policy": disclosure_policy(),
        "window": window.to_json(),
        "filters": filters.to_json(),
        "scope": {"restricted": access.restricted},
        "series": series_block(grain, timezone),
        "population": population,
        "rows": rows,
        "points": series_answer,
        "disclosure": disclosure_block(decision.kind, decision.masked),
    }


# --- coverage, scorecard and missing data (HOM-81) -----------------------------------------


def _has_population_filter(filters: TrendFilters) -> bool:
    return bool(
        filters.team_ids or filters.agent_user_ids or filters.languages or filters.source_types
    )


def coverage_block(window: Any, basis: str, counters: Mapping[str, Any]) -> dict[str, Any]:
    """The ``coverage`` key of a trend answer: the processing and evaluation counters."""
    return {
        "version": COVERAGE_VERSION,
        "window": window.to_json(),
        "basis": basis,
        "processing": counters["processing"],
        "evaluation": counters["evaluation"],
    }


def coverage_window(filters: TrendFilters) -> CoverageWindow:
    return CoverageWindow(start=filters.window.start, end=filters.window.end)


def trend_coverage(
    db: Session,
    *,
    workspace_id: str,
    access: RecordAccess,
    filters: TrendFilters,
    recompute_max_attempts: int,
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """The ``coverage/1`` processing and evaluation counters of the trend window.

    ``workspace`` basis for an unrestricted caller with no population filter: then the
    counters equal those of ``GET /admin/coverage``.  Otherwise ``trend_population``: the
    same ``compute_coverage`` rules over the accepted ledger rows whose meeting passes the
    caller's scope and the population filters, so no count is about a meeting outside the
    scope.  Selectors, grain and timezone never change it.  At most 9 statements (1 ledger
    statement, plus 8 fact statements when a meeting may be transcribed), whatever the
    number of conversations.  ``cache`` and ``organization_id``: the app's cache of the
    unsupported findings verdicts (HOM-312, U2; ``compute_coverage``).
    """
    if access.restricted or _has_population_filter(filters):
        basis = COVERAGE_BASIS_TREND_POPULATION
        population = [
            IngestionRecord.status == IngestionStatus.ACCEPTED,
            *_scope_clauses(access, filters),
        ]
    else:
        basis, population = COVERAGE_BASIS_WORKSPACE, []
    counters = compute_coverage(
        db,
        workspace_id=workspace_id,
        window=coverage_window(filters),
        recompute_max_attempts=recompute_max_attempts,
        population=population,
        cache=cache,
        organization_id=organization_id,
    ).counters
    return coverage_block(filters.window, basis, counters)


def complete_block(block: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    """A cohort block (``population`` and ``rows``) with ``coverage``, ``scorecard`` and
    ``missing_data`` (HOM-81) added, ``missing_data`` read after any mask."""
    complete = {**block, "coverage": coverage, "scorecard": scorecard_block()}
    complete["missing_data"] = trend_missing_data(complete)
    return complete


def add_trend_metadata(
    db: Session,
    answer: dict[str, Any],
    *,
    workspace_id: str,
    access: RecordAccess,
    filters: TrendFilters,
    recompute_max_attempts: int,
    cache: JudgmentCache | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """``answer`` of ``compute_issue_trends`` (same workspace, access and filters) with
    ``coverage``, ``scorecard`` and ``missing_data`` added.  A new dict; every existing
    key keeps its value.  ``compute_issue_trends`` itself stays at 2 statements."""
    return complete_block(
        answer,
        trend_coverage(
            db,
            workspace_id=workspace_id,
            access=access,
            filters=filters,
            recompute_max_attempts=recompute_max_attempts,
            cache=cache,
            organization_id=organization_id,
        ),
    )
