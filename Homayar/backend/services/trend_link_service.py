"""Supporting conversations of a trend cell (HOM-83, WP15-T05): ``GET /trends/conversations``.

The route drills into one cell of a workspace trend view (``trend_view``): ``issues``
(``GET /trends/issues``), ``teams`` (one team block of ``GET /trends/teams``) or ``me``
(``GET /trends/me``).  The cell is decided by the view's own function (``issue_cohort``,
``team_scope`` with ``team_cohort``, ``self_cohort``) and made with the same ``cohort_parts``
and ``apply_masks`` calls, so a link never undoes a HOM-82 decision.  Only a ``computed``
cell gives links; every other cell is the same ``403 trend_links_withheld``.

The list is the numerator: the included conversations of the cohort (and of the point) whose
state for the row is ``counted``.  A link holds ids only: the meeting, the judged run and the
extraction evidence spans that support the row.  The caller opens text through the meeting
routes, which apply the view choice and its audit.  Every answer carries the notice
``correlation_not_cause``.  Reads, not audited.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import AnalysisExtraction, EvidenceSpan
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    ConversationJudgment,
    IssueState,
    TrendDefinition,
    TrendFilters,
    trend_provenance,
)
from backend.issue_trends.filters import selected_family_ids, selected_issue_ids
from backend.issue_trends.judgment_cache import JudgmentCache
from backend.issue_trends.samples import ASSOCIATION_NOTICE
from backend.security.context import WorkspaceContext
from backend.services import role_trend_service as roles
from backend.services.analysis_extraction import EXTRACTION_FIELDS
from backend.services.evidence_service import FINDING_EXTRACTION
from backend.services.issue_trend_service import (
    CohortDecision,
    JudgedConversation,
    SeriesPoint,
    apply_masks,
    cohort_parts,
    disclosure_block,
    disclosure_policy,
    issue_cohort,
    point_finder,
    series_block,
    series_points,
    trend_definition,
    validate_series,
)

TREND_VIEWS = ("issues", "teams", "me")
LINK_KEYS = ("trend_view", "row", "point", "offset", "limit")
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_OFFSET = 999_999

VIEW_INVALID = "trend_view_invalid"
WINDOW_REQUIRED = "trend_window_required"
TEAM_INVALID = "trend_team_invalid"
ROW_INVALID = "trend_row_invalid"
POINT_INVALID = "trend_point_invalid"
PAGE_INVALID = "trend_page_invalid"
LINKS_WITHHELD = "trend_links_withheld"
WITHHELD_MESSAGE = "This cell gives no conversation links"

_ROW = re.compile(r"(family|issue):([a-z0-9_]+)")
_INDEX = re.compile(r"0|[1-9][0-9]{0,5}")


def _refused(code: str, key: str | None, message: str) -> roles.TrendViewError:
    return roles.TrendViewError(422, code, key, message)


def _one(value: object) -> str | None:
    """A query value given once (a string), or ``None`` when it is absent or repeated."""
    return value if isinstance(value, str) else None


# --- the link parameters --------------------------------------------------------------------


def split_link_query(
    raw: Mapping[str, str | list[str]],
) -> tuple[str, dict[str, str | list[str]], dict[str, str | list[str]]]:
    """``trend_view``, the other link keys, and the rest (the view's own query).
    ``trend_view`` is required, exactly once, one of ``TREND_VIEWS``."""
    view = _one(raw.get("trend_view"))
    if view not in TREND_VIEWS:
        raise _refused(VIEW_INVALID, "trend_view", "trend_view must be issues, teams or me")
    link = {key: value for key, value in raw.items() if key in LINK_KEYS and key != "trend_view"}
    rest = {key: value for key, value in raw.items() if key not in LINK_KEYS}
    return view, link, rest


def require_window(raw: Mapping[str, object]) -> None:
    """A4: ``from`` and ``to`` are required (the view's echoed window), ``from`` first.  A
    repeated end is refused before, by the view's own check (``filter_bad_type``)."""
    for key in ("from", "to"):
        if key not in raw:
            raise _refused(WINDOW_REQUIRED, key, f"{key} is required: pass the view's window")


def parse_team(filters: TrendFilters) -> str:
    """``trend_view=teams`` drills into exactly one team block."""
    if len(filters.team_ids) != 1:
        raise _refused(TEAM_INVALID, "team_id", "name exactly one team_id")
    return filters.team_ids[0]


@dataclass(frozen=True)
class LinkRow:
    level: str  # "family" or "issue"
    family: str
    issue: str | None

    def to_json(self) -> dict[str, Any]:
        return {"level": self.level, "family": self.family, "issue": self.issue}

    def matches(self, row: Mapping[str, Any]) -> bool:
        return (row["level"], row["family"], row["issue"]) == (
            self.level,
            self.family,
            self.issue,
        )


def parse_row(value: object, filters: TrendFilters, definition: TrendDefinition) -> LinkRow:
    """``family:<id>`` or ``issue:<id>``, exactly once, a row the view shows for the same
    selectors (``selected_family_ids`` and ``selected_issue_ids``)."""
    match = _ROW.fullmatch(value) if isinstance(value, str) else None
    if match is not None:
        level, row_id = match.groups()
        if level == "family" and row_id in selected_family_ids(filters, definition):
            return LinkRow("family", row_id, None)
        if level == "issue" and row_id in selected_issue_ids(filters, definition):
            issue = definition.issue(row_id)
            assert issue is not None  # selected ids are active issues
            return LinkRow("issue", issue.family, row_id)
    raise _refused(ROW_INVALID, "row", "row must be family:<id> or issue:<id> of the view")


def parse_point(value: object, points: Sequence[SeriesPoint]) -> int | None:
    """The index of a point of the view's series; absent: the window cell."""
    if value is None:
        return None
    if isinstance(value, str) and _INDEX.fullmatch(value) and int(value) < len(points):
        return int(value)
    raise _refused(POINT_INVALID, "point", "point must be the index of a point of the series")


def parse_page(offset: object, limit: object) -> tuple[int, int]:
    """``offset`` 0 to 999999 (default 0), then ``limit`` 1 to 100 (default 50)."""
    start = 0 if offset is None else _number(offset, "offset", 0, MAX_OFFSET)
    size = DEFAULT_LIMIT if limit is None else _number(limit, "limit", 1, MAX_LIMIT)
    return start, size


def _number(value: object, key: str, low: int, high: int) -> int:
    if isinstance(value, str) and _INDEX.fullmatch(value) and low <= int(value) <= high:
        return int(value)
    raise _refused(PAGE_INVALID, key, f"{key} must be an integer from {low} to {high}")


# --- the decision ---------------------------------------------------------------------------


def linkable(cell: Mapping[str, Any]) -> bool:
    """A3: only a ``computed`` cell (after the masks) gives links.  ``insufficient_sample``
    (also a rate cell whose numerator is shown), ``withheld`` and ``not_computable`` never."""
    return cell["status"] == "computed"


def withheld() -> roles.TrendViewError:
    """The one answer of every non-linkable cell: nothing in it depends on the cell."""
    return roles.TrendViewError(403, LINKS_WITHHELD, None, WITHHELD_MESSAGE)


def row_state(judgment: ConversationJudgment, row: LinkRow) -> IssueState:
    if row.issue is not None:
        return judgment.issue(row.issue).state
    return judgment.family(row.family).state


def supporting_finding_indexes(
    result: Mapping[str, Any],
    judgment: ConversationJudgment,
    row: LinkRow,
    definition: TrendDefinition,
) -> frozenset[int]:
    """The ``finding_index`` of every kept, auto-accepted value that makes ``row`` hold in
    this conversation.  An issue: the values of its references (dimension and label).  A
    family: the same over its issues whose state is ``counted``.  The index is the position
    among the kept values over intent, topic, entity and outcome
    (``analysis_extraction.kept_values``).  ``result`` is the stored row that was judged."""
    if row.issue is not None:
        issue = definition.issue(row.issue)
        issues = [issue] if issue is not None else []
    else:
        issues = [
            issue
            for issue in definition.active_issues(row.family)
            if any(
                i.issue_id == issue.id and i.state is IssueState.COUNTED for i in judgment.issues
            )
        ]
    wanted = {
        (reference.dimension, reference.label) for issue in issues for reference in issue.match
    }
    indexes: set[int] = set()
    position = 0
    for name in EXTRACTION_FIELDS:
        for value in result["fields"][name]["values"]:
            if (name, value["label"]) in wanted and value["disposition"] == "auto_accepted":
                indexes.add(position)
            position += 1
    return frozenset(indexes)


def _evidence(
    db: Session,
    *,
    workspace_id: str,
    page: Sequence[JudgedConversation],
    row: LinkRow,
    definition: TrendDefinition,
) -> dict[str, list[str]]:
    """The evidence ids of each conversation of the page: two statements, whatever the page
    size.  E1 reads the stored rows of the judged runs (they never change); E2 reads the
    extraction spans of those runs."""
    runs = {c.analysis_run_id: c for c in page if c.analysis_run_id is not None}
    if not runs:
        return {}
    results: dict[str, Any] = {
        run_id: result
        for run_id, result in db.execute(
            select(AnalysisExtraction.analysis_run_id, AnalysisExtraction.result).where(
                AnalysisExtraction.workspace_id == workspace_id,
                AnalysisExtraction.analysis_run_id.in_(sorted(runs)),
            )
        )
    }
    wanted = {
        run_id: supporting_finding_indexes(results[run_id], c.judgment, row, definition)
        for run_id, c in runs.items()
        if run_id in results
    }
    found: dict[str, list[str]] = defaultdict(list)
    spans = db.execute(
        select(
            EvidenceSpan.id,
            EvidenceSpan.meeting_id,
            EvidenceSpan.analysis_run_id,
            EvidenceSpan.finding_index,
        )
        .where(
            EvidenceSpan.workspace_id == workspace_id,
            EvidenceSpan.analysis_run_id.in_(sorted(runs)),
            EvidenceSpan.meeting_id.in_(sorted(c.judgment.meeting_id for c in runs.values())),
            EvidenceSpan.finding_type == FINDING_EXTRACTION,
        )
        .order_by(EvidenceSpan.finding_index, EvidenceSpan.created_at, EvidenceSpan.id)
    )
    for span_id, meeting_id, run_id, finding_index in spans:
        if runs[run_id].judgment.meeting_id == meeting_id and finding_index in wanted.get(
            run_id, frozenset()
        ):
            found[meeting_id].append(span_id)
    return found


def _decision(
    db: Session,
    *,
    context: WorkspaceContext,
    trend_view: str,
    filters: TrendFilters,
    points: Sequence[SeriesPoint],
    team_id: str | None,
    cache: JudgmentCache | None = None,
) -> CohortDecision:
    """The cohort decision of the view the cell comes from: the view's own function.
    ``cache``: the app's trend judgment cache (HOM-312), handed on."""
    if trend_view == "teams":
        assert team_id is not None
        scope = roles.team_scope(db, context=context, filters=filters, points=points, cache=cache)
        return roles.team_cohort(scope, team_id)
    if trend_view == "me":
        return roles.self_cohort(db, context=context, filters=filters, cache=cache)
    return issue_cohort(
        db,
        workspace_id=context.workspace.id,
        access=context.record_access,
        filters=filters,
        points=points,
        cache=cache,
        organization_id=context.workspace.organization_id,
    )


def trend_links(
    db: Session,
    *,
    context: WorkspaceContext,
    trend_view: str,
    filters: TrendFilters,
    grain: str,
    timezone: str,
    row: LinkRow,
    point: int | None,
    offset: int,
    limit: int,
    team: tuple[str, str] | None = None,
    cache: JudgmentCache | None = None,
) -> dict[str, Any]:
    """The conversations behind one cell, or ``withheld()`` (a ``TrendViewError``).

    The cell comes from the view's decision and the view's ``cohort_parts`` and
    ``apply_masks``.  The decision reads only the cell status; the list is built only for a
    ``computed`` cell, and its length is its numerator."""
    zone = validate_series(grain, timezone)
    definition = trend_definition()
    points = series_points(filters.window.start, filters.window.end, grain, zone)
    decision = _decision(
        db,
        context=context,
        trend_view=trend_view,
        filters=filters,
        points=points,
        team_id=team[0] if team is not None else None,
        cache=cache,
    )
    _population, rows, series_answer = cohort_parts(
        decision.conversations, definition, filters, decision.kind, points, zone
    )
    rows, series_answer = apply_masks(rows, series_answer, decision.masked)
    layer = rows if point is None else series_answer[point]["rows"]
    cell = next(item["cell"] for item in layer if row.matches(item))
    if not linkable(cell):
        raise withheld()

    in_point = point_finder(points) if point is not None else None
    counted = sorted(
        (
            c
            for c in decision.conversations
            if c.included
            and (in_point is None or in_point(c.received_at) == point)
            and row_state(c.judgment, row) is IssueState.COUNTED
        ),
        key=lambda c: c.judgment.meeting_id,
    )
    if len(counted) != cell["numerator"]:  # one load: never true, never more than the cell
        raise RuntimeError("the supporting conversations do not match the numerator")
    page = counted[offset : offset + limit]
    evidence = _evidence(
        db, workspace_id=context.workspace.id, page=page, row=row, definition=definition
    )
    return {
        **trend_provenance(definition, SAMPLE_POLICY_V1),
        "disclosure_policy": disclosure_policy(),
        "notices": [ASSOCIATION_NOTICE],
        "trend_view": trend_view,
        "window": filters.window.to_json(),
        "filters": filters.to_json(),
        "scope": {"restricted": context.record_access.restricted},
        "series": series_block(grain, timezone),
        "team": {"id": team[0], "name": team[1]} if team is not None else None,
        "row": row.to_json(),
        "point": (
            None
            if point is None
            else {
                "index": point,
                "from": series_answer[point]["from"],
                "to": series_answer[point]["to"],
                "partial": series_answer[point]["partial"],
            }
        ),
        "cell": cell,
        "disclosure": disclosure_block(decision.kind, decision.masked),
        "page": {"offset": offset, "limit": limit, "total": cell["numerator"]},
        "conversations": [
            {
                "meeting_id": c.judgment.meeting_id,
                "state": IssueState.COUNTED.value,
                "analysis_run_id": c.analysis_run_id,
                "evidence_ids": evidence.get(c.judgment.meeting_id, []),
            }
            for c in page
        ],
    }
