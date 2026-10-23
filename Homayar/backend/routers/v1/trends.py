"""Issue trends (HOM-80, WP15-T02) and the workspace role views (HOM-82, WP15-T04).

* ``GET /trends/issues``  scoped issue-trend aggregates, MEETING_READ (API keys accepted)
* ``GET /trends/teams``   supervisor team comparison, MEETING_READ, session only
* ``GET /trends/me``      agent self-view, MEETING_READ, session only

The caller's workspace and record scope (``RecordAccess.meeting_clause``) are ANDed with
every filter, so a filter only narrows: a ``team_id`` outside the scope answers ``200``
with what the caller may see, never ``403``.  Reads, not audited.  Every answer carries the
``coverage/1`` counters of its window, the scorecard state and the missing-data
indicators (HOM-81), and a ``disclosure`` block (HOM-82).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.issue_trends import TrendFilterError, validate_filters
from backend.schemas.trends import IssueTrendResponse, SelfTrendResponse, TeamComparisonResponse
from backend.security.authorization import Permission
from backend.security.context import (
    Actor,
    WorkspaceContext,
    require_actor,
    require_permission,
    require_workspace,
)
from backend.services import role_trend_service as roles
from backend.services.issue_trend_service import (
    TREND_DEFAULT_GRAIN,
    TREND_DEFAULT_TIMEZONE,
    TrendSeriesError,
    add_trend_metadata,
    compute_issue_trends,
    trend_definition,
    validate_series,
)

router = APIRouter(prefix="/trends", tags=["trends"])

SERIES_KEYS = ("grain", "timezone")


def _refused(code: str, key: str | None, message: str, status_code: int = 422) -> HTTPException:
    """``error.code`` and ``error.details.key`` (the app's error envelope keeps only
    ``code``, ``message`` and ``details``)."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "key": key, "message": message, "details": {"key": key}},
    )


def require_session_permission(permission: Permission):
    """``permission`` from a signed-in session only (HOM-82).  ``require_actor`` answers
    every bearer that looks like an API key (valid, unknown, revoked, expired, over its
    rate limit) with ``401 api_key_not_accepted`` before any key lookup, so every key gets
    one answer, before the workspace and the permission."""

    async def dependency(
        actor: Actor = Depends(require_actor),
        workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
        db: Session = Depends(get_db),
    ) -> WorkspaceContext:
        context = await require_workspace(actor=actor, workspace_id=workspace_id, db=db)
        if not context.has(permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "message": "Permission denied"},
            )
        return context

    # Policy metadata read by the route-coverage test (never by the runtime).
    dependency.homayar_permission = permission  # type: ignore[attr-defined]
    dependency.homayar_write = False  # type: ignore[attr-defined]
    dependency.homayar_session_only = True  # type: ignore[attr-defined]
    return dependency


def _view_query(request: Request, now: datetime) -> tuple:
    try:
        return roles.workspace_query(roles.query_dict(request.query_params.multi_items()), now=now)
    except TrendFilterError as exc:
        raise _refused(exc.code.value, exc.key, str(exc)) from exc
    except TrendSeriesError as exc:
        raise _refused(exc.code, exc.key, str(exc)) from exc
    except roles.TrendViewError as exc:
        raise _refused(exc.code, exc.key, str(exc), exc.status_code) from exc


@router.get("/issues", response_model=IssueTrendResponse)
def read_issue_trends(
    request: Request,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IssueTrendResponse:
    """Call reasons, unresolved outcomes, QA and product/service complaints (HOM-80).

    Every query parameter except ``grain`` (``none``, ``day``, ``week``, ``month``) and
    ``timezone`` (an IANA name, default ``Asia/Tehran``) is a HOM-79 trend filter; a
    repeated key is a list.  Refusals are 422 in this order: the filter codes
    (``filter_*``), then ``trend_grain_invalid``, then ``trend_timezone_invalid``.  Then
    ``agent_user_id`` other than the caller's own id is ``403 agent_scope_denied``
    (HOM-82).
    """
    raw = roles.query_dict(request.query_params.multi_items())
    grain = raw.pop("grain", TREND_DEFAULT_GRAIN)
    timezone = raw.pop("timezone", TREND_DEFAULT_TIMEZONE)
    now = datetime.now(UTC)
    try:
        filters = validate_filters(raw, trend_definition(), now=now)
        validate_series(grain, timezone)
    except TrendFilterError as exc:
        raise _refused(exc.code.value, exc.key, str(exc)) from exc
    except TrendSeriesError as exc:
        raise _refused(exc.code, exc.key, str(exc)) from exc
    assert isinstance(grain, str) and isinstance(timezone, str)  # validate_series checked
    if filters.agent_user_ids and filters.agent_user_ids != (context.actor.user.id,):
        raise _refused(
            roles.AGENT_SCOPE_DENIED,
            "agent_user_id",
            "You may only filter by your own agent id",
            status_code=403,
        )
    try:
        answer = compute_issue_trends(
            db,
            workspace_id=context.workspace.id,
            access=context.record_access,
            filters=filters,
            now=now,
            grain=grain,
            timezone=timezone,
        )
    except TrendSeriesError as exc:  # a window that cannot be shown in the zone
        raise _refused(exc.code, exc.key, str(exc)) from exc
    answer = add_trend_metadata(
        db,
        answer,
        workspace_id=context.workspace.id,
        access=context.record_access,
        filters=filters,
        recompute_max_attempts=settings.recompute_max_attempts,
    )
    return IssueTrendResponse.model_validate(answer)


@router.get("/teams", response_model=TeamComparisonResponse)
def read_team_comparison(
    request: Request,
    context: WorkspaceContext = Depends(require_session_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TeamComparisonResponse:
    """Supervisor team comparison (HOM-82): one block per team, compared in pairs.

    Session only: every API key is ``401 api_key_not_accepted``.  Owners and admins
    compare every active team; members and viewers only the active teams they supervise
    (``403 team_scope_denied`` otherwise, also for a ``team_id`` they may not compare).  At
    most 10 teams (``422 trend_too_many_teams``).  ``agent_user_id`` is
    ``422 trend_parameter_not_allowed``.  A team is shown only when it is in the canonical
    released set of the caller's scope.  No rank: teams in a fixed order by name.
    """
    filters, grain, timezone = _view_query(request, datetime.now(UTC))
    try:
        teams = roles.compared_teams(roles.comparable_teams(db, context), filters.team_ids)
        answer = roles.team_comparison(
            db,
            context=context,
            filters=filters,
            teams=teams,
            grain=grain,
            timezone=timezone,
            recompute_max_attempts=settings.recompute_max_attempts,
        )
    except roles.TrendViewError as exc:
        raise _refused(exc.code, exc.key, str(exc), exc.status_code) from exc
    except TrendSeriesError as exc:  # a window that cannot be shown in the zone
        raise _refused(exc.code, exc.key, str(exc)) from exc
    return TeamComparisonResponse.model_validate(answer)


@router.get("/me", response_model=SelfTrendResponse)
def read_self_trends(
    request: Request,
    context: WorkspaceContext = Depends(require_session_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SelfTrendResponse:
    """Agent self-view (HOM-82): the caller's own meetings as agent, kind ``agent``.

    Session only: every API key is ``401 api_key_not_accepted``.  Every workspace role may
    call it.  Uploads the caller made for someone else do not count.  ``agent_user_id`` is
    ``422 trend_parameter_not_allowed``; ``team_id``, ``language`` and ``source_type``
    narrow the own meetings.
    """
    filters, grain, timezone = _view_query(request, datetime.now(UTC))
    try:
        answer = roles.self_view(
            db,
            context=context,
            filters=filters,
            grain=grain,
            timezone=timezone,
            recompute_max_attempts=settings.recompute_max_attempts,
        )
    except TrendSeriesError as exc:  # a window that cannot be shown in the zone
        raise _refused(exc.code, exc.key, str(exc)) from exc
    return SelfTrendResponse.model_validate(answer)
