"""HOM-81 (WP15-T03): coverage, scorecard state and missing data in the aggregates.

``GET /trends/issues`` gets a ``coverage`` block (the ``coverage/1`` processing and
evaluation counters of the same window), and both it and ``GET /admin/coverage`` get a
``scorecard`` block and a ``missing_data`` summary.  With an unrestricted caller and no
population filter the trend coverage is the workspace coverage; otherwise it is counted
over the trend population, so a restricted caller never sees a count about a meeting
outside their scope.  Every indicator is a pure function of the same response.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select

from backend.config import get_settings
from backend.database.saas_models import AuditEvent, IngestionSourceType, MeetingStatus
from backend.services.aggregate_metadata import (
    MISSING_DATA_INDICATORS,
    counter_indicators,
    coverage_missing_data,
    scorecard_block,
    trend_missing_data,
)
from backend.services.coverage_service import CoverageWindow, compute_coverage
from backend.services.issue_trend_service import add_trend_metadata, compute_issue_trends
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import COUNTER_KEYS, COVERAGE, Tenant
from backend.tests.test_issue_trend_routes import _get, _place, _team_scoped
from backend.tests.test_issue_trend_service import (
    ANSWER_KEYS,
    BASE,
    DAY,
    NOW,
    WINDOW,
    add_team,
    add_user,
    excluded,
    filters_of,
    included,
    restricted,
    seed,
    unrestricted,
)

SCORECARD = {
    "status": "not_available",
    "version": None,
    "digest": None,
    "reason": "scorecard_results_unavailable",
}
NEW_KEYS = {"coverage", "scorecard", "missing_data"}


def full_trends(
    tenant: Any, *, access=None, grain: str = "none", timezone: str = "Asia/Tehran", **raw: Any
) -> dict[str, Any]:
    """``compute_issue_trends`` then ``add_trend_metadata``, like the route."""
    access = access or unrestricted(tenant.owner_id)
    filters = filters_of(**raw)
    with inspection_session_factory()() as db:
        answer = compute_issue_trends(
            db,
            workspace_id=tenant.workspace_id,
            access=access,
            filters=filters,
            now=NOW,
            grain=grain,
            timezone=timezone,
        )
        return add_trend_metadata(
            db,
            answer,
            workspace_id=tenant.workspace_id,
            access=access,
            filters=filters,
            recompute_max_attempts=get_settings().recompute_max_attempts,
        )


def expected_indicators(answer: dict[str, Any]) -> list[str]:
    """SPEC section 4.3, written from the tables, for a trend answer."""
    codes: set[str] = set()
    for name in ("processing", "evaluation"):
        counter = answer["coverage"][name]
        if counter["status"] == "computed" and counter["numerator"] < counter["denominator"]:
            codes.add(f"{name}_incomplete")
        if counter["status"] == "not_computable" and counter["reason"] != "empty_denominator":
            codes.add(f"{name}_unavailable")
    if answer["scorecard"]["status"] == "not_available":
        codes.add("scorecard_unavailable")
    if answer["population"]["included"] == 0:
        codes.add("empty_population")
    for item in answer["rows"]:
        if sum(item["missing"].values()) > 0:
            codes.add("missing_values")
        if (item["pending_review"] or 0) > 0 or (item["absent_pending_review"] or 0) > 0:
            codes.add("pending_review")
        if item["cell"]["status"] == "insufficient_sample":
            codes.add("insufficient_sample")
        if (
            item["cell"]["status"] == "not_computable"
            and item["cell"]["reason"] != "empty_denominator"
        ):
            codes.add("source_unavailable")
    return sorted(codes)


def assert_coverage_block(answer: dict[str, Any], basis: str) -> None:
    coverage = answer["coverage"]
    assert set(coverage) == {"version", "window", "basis", "processing", "evaluation"}
    assert coverage["version"] == "coverage/1"
    assert coverage["window"] == answer["window"]
    assert coverage["basis"] == basis
    processing, evaluation = coverage["processing"], coverage["evaluation"]
    assert set(processing) == COUNTER_KEYS and set(evaluation) == COUNTER_KEYS
    population = answer["population"]
    counted = population["included"] + sum(population["excluded"].values())
    if processing["status"] == "computed":
        assert counted == processing["numerator"]
    else:
        assert counted == 0 and processing["numerator"] == 0
    assert evaluation["denominator"] == processing["numerator"]
    assert answer["scorecard"] == SCORECARD
    assert answer["missing_data"] == {"indicators": expected_indicators(answer)}


# --- pure: the scorecard block and the indicator rules ---------------------------------------


def _counter(numerator, denominator, status="computed", reason=None) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator, "status": status, "reason": reason}


@pytest.mark.parametrize(
    ("counter", "expected"),
    [
        (_counter(3, 4), {"processing_incomplete"}),
        (_counter(4, 4), set()),
        (_counter(0, 0, "not_computable", "empty_denominator"), set()),
        (
            _counter(None, 2, "not_computable", "review_records_unavailable"),
            {"processing_unavailable"},
        ),
    ],
)
def test_counter_rules(counter, expected):
    assert counter_indicators("processing", counter) == expected


def test_the_scorecard_block_is_fixed_and_fresh():
    block = scorecard_block()
    assert block == SCORECARD
    block["version"] = "made-up"  # a caller cannot change the next answer
    assert scorecard_block() == SCORECARD


def _trend_row(**overrides: Any) -> dict[str, Any]:
    item = {
        "cell": {"status": "computed", "reason": None},
        "pending_review": 0,
        "absent_pending_review": 0,
        "missing": {"analysis_unavailable": 0},
    }
    item.update(overrides)
    return item


def _trend(rows: list[dict[str, Any]], included_count: int = 40) -> dict[str, Any]:
    full = _counter(40, 40)
    return {
        "coverage": {"processing": full, "evaluation": full},
        "scorecard": scorecard_block(),
        "population": {"included": included_count, "excluded": {"duplicate_media": 0}},
        "rows": rows,
        "points": [{"rows": [_trend_row(missing={"analysis_unavailable": 9})]}],
    }


TREND_CASES = [
    ([_trend_row()], 40, ["scorecard_unavailable"]),
    ([_trend_row()], 0, ["empty_population", "scorecard_unavailable"]),
    (
        [_trend_row(missing={"analysis_unavailable": 1})],
        40,
        ["missing_values", "scorecard_unavailable"],
    ),
    (
        [_trend_row(pending_review=None, absent_pending_review=2)],
        40,
        ["pending_review", "scorecard_unavailable"],
    ),
    ([_trend_row(pending_review=None, absent_pending_review=None)], 40, ["scorecard_unavailable"]),
    (
        [_trend_row(cell={"status": "insufficient_sample", "reason": "below_minimum_sample"})],
        40,
        ["insufficient_sample", "scorecard_unavailable"],
    ),
    (
        [_trend_row(cell={"status": "not_computable", "reason": "scorecard_results_unavailable"})],
        40,
        ["scorecard_unavailable", "source_unavailable"],
    ),
    (
        [_trend_row(cell={"status": "not_computable", "reason": "empty_denominator"})],
        40,
        ["scorecard_unavailable"],
    ),
]


@pytest.mark.parametrize(("rows", "included_count", "expected"), TREND_CASES)
def test_trend_response_rules_read_only_top_level_rows(rows, included_count, expected):
    answer = _trend(rows, included_count)
    before = deepcopy(answer)
    assert trend_missing_data(answer) == {"indicators": expected}
    assert answer == before  # pure: the answer is not changed
    assert set(expected) <= MISSING_DATA_INDICATORS


def test_coverage_response_rules():
    counters = {
        "ingestion": _counter(2, 3),
        "processing": _counter(2, 2),
        "evaluation": _counter(1, 2),
        "review": _counter(None, 1, "not_computable", "review_records_unavailable"),
    }
    assert coverage_missing_data(counters, scorecard_block()) == {
        "indicators": [
            "evaluation_incomplete",
            "ingestion_incomplete",
            "review_unavailable",
            "scorecard_unavailable",
        ]
    }
    empty = _counter(0, 0, "not_computable", "empty_denominator")
    counters = {"ingestion": empty, "processing": empty, "evaluation": empty}
    counters["review"] = _counter(None, 0, "not_computable", "review_records_unavailable")
    assert coverage_missing_data(counters, scorecard_block()) == {
        "indicators": ["empty_population", "review_unavailable", "scorecard_unavailable"]
    }


# --- the service: scope, filters, other workspaces, cost -------------------------------------


def test_trend_coverage_follows_scope_and_filters(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "meta-scope@example.com")
    other = Tenant(client, email_outbox, "meta-other@example.com")
    team_a, team_b, team_empty = (add_team(tenant, name) for name in ("a", "b", "empty"))
    sup = add_user("sup")
    first = seed(tenant, team_id=team_a)
    seed(
        tenant,
        received_at=BASE + timedelta(hours=1),
        team_id=team_a,
        duplicate_of=first.ingestion_id,
    )
    seed(tenant, team_id=team_a, segments=0)  # no_transcript
    seed(tenant, team_id=team_a, deleted=True)  # deleted
    seed(tenant, team_id=team_a, language="en", source_type=IngestionSourceType.API)
    seed(tenant, team_id=team_b)  # transcribed, outside the supervisor's scope
    seed(tenant, team_id=team_b, status=MeetingStatus.FAILED)
    seed(tenant, ledger="rejected")
    seed(tenant, with_meeting=False)  # accepted, the meeting row is gone
    seed(tenant, received_at=BASE - DAY, team_id=team_a)  # outside the window
    for _ in range(3):
        seed(other)

    # the owner without a population filter: the workspace coverage
    whole = full_trends(tenant)
    assert set(whole) == ANSWER_KEYS | NEW_KEYS
    assert_coverage_block(whole, "workspace")
    with inspection_session_factory()() as db:
        workspace = compute_coverage(
            db,
            workspace_id=tenant.workspace_id,
            window=CoverageWindow(start=BASE, end=BASE + DAY),
            recompute_max_attempts=settings.recompute_max_attempts,
        ).counters
    assert whole["coverage"]["processing"] == workspace["processing"]
    assert whole["coverage"]["evaluation"] == workspace["evaluation"]
    processing = whole["coverage"]["processing"]
    assert processing["denominator"] == 8  # the other workspace's rows are not here
    assert processing["buckets"] == {
        "deleted": 2,
        "in_progress": 0,
        "failed": 1,
        "transcribed": 4,
        "no_transcript": 1,
    }
    assert included(whole) == 3 and excluded(whole) == 1
    assert "processing_incomplete" in whole["missing_data"]["indicators"]

    # selectors, grain and timezone never change the block
    for options in (
        {"family": "qa"},
        {"issue": "complaint_refund"},
        {"service_area": "refund"},
        {"grain": "day"},
        {"grain": "week", "timezone": "UTC"},
    ):
        assert full_trends(tenant, **options)["coverage"] == whole["coverage"], options

    # allow-control: the other workspace counts its own rows
    assert full_trends(other)["coverage"]["processing"]["denominator"] == 3

    # deny-cases: a team filter, a restricted caller; the trend population basis
    cases = {
        "team_a": ({"team_id": team_a}, None, 5, 3),
        "restricted": ({}, restricted(sup, team_a), 5, 3),
        "restricted_team_b": ({"team_id": team_b}, restricted(sup, team_a), 0, 0),
        "team_b": ({"team_id": team_b}, None, 2, 1),
        "language": ({"language": "en"}, None, 1, 1),
        "source_type": ({"source_type": "api"}, None, 1, 1),
        "empty": ({"team_id": team_empty}, None, 0, 0),
    }
    for name, (raw, access, denominator, transcribed) in cases.items():
        answer = full_trends(tenant, access=access, **raw)
        assert_coverage_block(answer, "trend_population")
        counter = answer["coverage"]["processing"]
        assert counter["denominator"] == denominator, name
        assert counter["buckets"]["transcribed"] == transcribed, name
        assert counter["numerator"] == transcribed, name
        assert counter["buckets"]["deleted"] == (1 if name in ("team_a", "restricted") else 0), name
    empty = full_trends(tenant, team_id=team_empty)
    assert empty["coverage"]["processing"]["status"] == "not_computable"
    assert empty["coverage"]["processing"]["reason"] == "empty_denominator"
    assert "empty_population" in empty["missing_data"]["indicators"]
    assert not {"processing_incomplete", "processing_unavailable"} & set(
        empty["missing_data"]["indicators"]
    )

    # cost: the added statements do not grow with the conversations
    small_day, large_day, empty_day = BASE + 3 * DAY, BASE + 4 * DAY, BASE + 6 * DAY
    seed(tenant, received_at=small_day, team_id=team_a)
    for hour in range(10):
        seed(tenant, received_at=large_day + timedelta(hours=hour), team_id=team_a)

    def statements(day, *, access=None, **raw) -> int:
        access = access or unrestricted(tenant.owner_id)
        filters = filters_of(**{"from": day.isoformat(), "to": (day + DAY).isoformat()}, **raw)
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            seen.append(statement)

        with inspection_session_factory()() as db:
            answer = compute_issue_trends(
                db, workspace_id=tenant.workspace_id, access=access, filters=filters, now=NOW
            )
            engine = db.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                add_trend_metadata(
                    db,
                    answer,
                    workspace_id=tenant.workspace_id,
                    access=access,
                    filters=filters,
                    recompute_max_attempts=settings.recompute_max_attempts,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    counts = {
        "small": statements(small_day),
        "large": statements(large_day),
        "small_team": statements(small_day, team_id=team_a),
        "large_team": statements(large_day, team_id=team_a),
        "large_restricted": statements(large_day, access=restricted(sup, team_a)),
    }
    assert counts == dict.fromkeys(counts, 9)  # 1 ledger statement + 8 fact statements
    assert statements(empty_day) == statements(empty_day, team_id=team_a) == 1


# --- the routes ------------------------------------------------------------------------------


def test_both_routes_carry_the_metadata(client, email_outbox, monkeypatch):
    owner, people, team_a, team_b = _team_scoped(client, email_outbox, monkeypatch)
    sup = people["sup"]
    place = _place(owner)
    seed(place, team_id=team_a)
    seed(place, team_id=team_b)  # outside the supervisor's scope
    seed(place, ledger="rejected")
    with inspection_session_factory()() as db:
        audits = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.workspace_id == owner.workspace_id)
        )

    whole = _get(owner)
    assert whole.status_code == 200, whole.text
    body = whole.json()
    assert set(body) == ANSWER_KEYS | NEW_KEYS
    assert_coverage_block(body, "workspace")
    coverage = owner.get(COVERAGE, params=WINDOW)
    assert coverage.status_code == 200, coverage.text
    counters = coverage.json()["counters"]
    assert body["coverage"]["processing"] == counters["processing"]
    assert body["coverage"]["evaluation"] == counters["evaluation"]
    assert body["coverage"]["processing"]["denominator"] == 2  # rejected: ingestion only
    assert counters["ingestion"]["denominator"] == 3

    coverage_body = coverage.json()
    assert set(coverage_body) == {
        "coverage_version",
        "window",
        "counters",
        "scorecard",
        "missing_data",
    }
    assert coverage_body["scorecard"] == SCORECARD
    assert coverage_body["missing_data"] == coverage_missing_data(counters, SCORECARD)
    assert "review_unavailable" in coverage_body["missing_data"]["indicators"]
    assert "ingestion_incomplete" in coverage_body["missing_data"]["indicators"]
    empty_window = {"from": (BASE + 9 * DAY).isoformat(), "to": (BASE + 10 * DAY).isoformat()}
    nothing = owner.get(COVERAGE, params=empty_window).json()
    assert nothing["missing_data"] == {
        "indicators": ["empty_population", "review_unavailable", "scorecard_unavailable"]
    }

    # deny-case: the restricted member's coverage leaves out team B's meeting
    scoped = _get(sup).json()
    assert scoped["scope"] == {"restricted": True}
    assert_coverage_block(scoped, "trend_population")
    assert scoped["coverage"]["processing"]["denominator"] == 1
    assert scoped["coverage"]["processing"]["numerator"] == 1
    assert team_b not in _get(sup).text
    assert sup.get(COVERAGE, params=WINDOW).status_code == 403

    with inspection_session_factory()() as db:
        after = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.workspace_id == owner.workspace_id)
        )
    assert after == audits  # neither route is audited
