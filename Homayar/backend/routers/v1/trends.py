"""Issue trends (HOM-80, WP15-T02).

* ``GET /trends/issues``  scoped issue-trend aggregates, MEETING_READ

The caller's workspace and record scope (``RecordAccess.meeting_clause``) are ANDed with
every filter, so a filter only narrows: a ``team_id`` outside the scope answers ``200``
with what the caller may see, never ``403``.  A read, not audited.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.database.relational import get_db
from backend.issue_trends import TrendFilterError, validate_filters
from backend.schemas.trends import IssueTrendResponse
from backend.security.authorization import Permission
from backend.security.context import WorkspaceContext, require_permission
from backend.services.issue_trend_service import (
    TREND_DEFAULT_GRAIN,
    TREND_DEFAULT_TIMEZONE,
    TrendSeriesError,
    compute_issue_trends,
    trend_definition,
    validate_series,
)

router = APIRouter(prefix="/trends", tags=["trends"])

SERIES_KEYS = ("grain", "timezone")


def _refused(code: str, key: str | None, message: str) -> HTTPException:
    """``error.code`` and ``error.details.key`` (the app's error envelope keeps only
    ``code``, ``message`` and ``details``)."""
    return HTTPException(
        status_code=422,
        detail={"code": code, "key": key, "message": message, "details": {"key": key}},
    )


@router.get("/issues", response_model=IssueTrendResponse)
def read_issue_trends(
    request: Request,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> IssueTrendResponse:
    """Call reasons, unresolved outcomes, QA and product/service complaints (HOM-80).

    Every query parameter except ``grain`` (``none``, ``day``, ``week``, ``month``) and
    ``timezone`` (an IANA name, default ``Asia/Tehran``) is a HOM-79 trend filter; a
    repeated key is a list.  Refusals are 422 in this order: the filter codes
    (``filter_*``), then ``trend_grain_invalid``, then ``trend_timezone_invalid``.
    """
    raw: dict[str, str | list[str]] = {}
    for key, value in request.query_params.multi_items():
        held = raw.get(key)
        if held is None:
            raw[key] = value
        elif isinstance(held, list):
            held.append(value)
        else:
            raw[key] = [held, value]
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
    return IssueTrendResponse.model_validate(answer)
