"""HOM-365: the demo seed gives the owner's ``/trends`` default query real percent values.

One full ``seed_demo_workspace(settings, now=<instant>)`` per dating instant, then
``GET /api/v1/trends/issues`` as the demo owner on the real clock with the page's default
query: the 4 closed Asia/Tehran weeks before the instant's Tehran week, grain ``week``,
timezone ``Asia/Tehran``, with and without ``team_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from sqlalchemy import func, select

from backend.database.demo_seed import seed_demo_workspace
from backend.database.saas_models import IngestionRecord, Meeting, Organization, User, Workspace
from backend.issue_trends.samples import (
    MIN_COHORT_AGENTS,
    SAMPLE_POLICY_V1,
    sample_policy_digest,
)
from backend.tests._database import inspection_session_factory

ISSUES = "/api/v1/trends/issues"
TEHRAN = "Asia/Tehran"
MINIMUM = 30
SAMPLE_POLICY_VERSION = "trend-sample-policy/1"
SAMPLE_POLICY_DIGEST = "e0a00f274f7bdac50774b294df188487f2e67077c99de4de261a5563031e13a9"
SMALL_TEAMS = ("Sales", "Support")
SOURCE_REASON = "scorecard_results_unavailable"

INSTANTS = [
    pytest.param(
        datetime(2026, 10, 31, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 30, 20, 30, tzinfo=UTC),
        id="saturday-first-hour",
    ),
    pytest.param(
        datetime(2026, 11, 6, 20, 29, tzinfo=UTC),
        datetime(2026, 10, 30, 20, 30, tzinfo=UTC),
        id="friday-last-minute",
    ),
    pytest.param(
        datetime(2026, 9, 30, 20, 30, tzinfo=UTC),
        datetime(2026, 9, 25, 20, 30, tzinfo=UTC),
        id="tehran-month-start",
    ),
]


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _login(client, account) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": account.email, "password": account.password},
    )
    assert response.status_code == 200, response.text


def _get(client, workspace_id: str, path: str, params: dict[str, str] | None = None) -> dict:
    response = client.get(path, params=params or {}, headers={"X-Workspace-ID": workspace_id})
    assert response.status_code == 200, response.text
    return response.json()


def _default_query(week_start: datetime, team_id: str | None = None) -> dict[str, str]:
    params = {
        "from": _iso(week_start - timedelta(days=28)),
        "to": _iso(week_start),
        "grain": "week",
        "timezone": TEHRAN,
    }
    if team_id is not None:
        params["team_id"] = team_id
    return params


def _issues(client, workspace_id: str, week_start: datetime, team_id: str | None = None):
    answer = _get(client, workspace_id, ISSUES, _default_query(week_start, team_id))
    assert _instant(answer["window"]["from"]) == week_start - timedelta(days=28)
    assert _instant(answer["window"]["to"]) == week_start
    assert answer["series"]["grain"] == "week"
    assert answer["series"]["timezone"] == TEHRAN
    assert answer["sample_policy"]["version"] == SAMPLE_POLICY_VERSION
    assert answer["sample_policy"]["digest"] == SAMPLE_POLICY_DIGEST
    assert answer["disclosure_policy"]["min_cohort_agents"] == 5
    return answer


def _released(answer: dict) -> bool:
    disclosure = answer["disclosure"]
    return (
        disclosure["status"] == "released"
        and disclosure["kind"] == "rate"
        and not disclosure["masked"]
    )


def _week_points(answer: dict, week_start: datetime) -> list[dict]:
    points = answer["points"]
    assert len(points) == 4, [(p["from"], p["to"]) for p in points]
    for index, point in enumerate(points):
        start = week_start - timedelta(days=7 * (4 - index))
        assert _instant(point["from"]) == start
        assert _instant(point["to"]) == start + timedelta(days=7)
        assert point["partial"] is False
    return points


def _issue_cells(rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {(row["family"], row["issue"]): row["cell"] for row in rows if row["level"] == "issue"}


def _steady_issues(answer: dict, week_start: datetime) -> set[tuple[str, str]]:
    points = [_issue_cells(point["rows"]) for point in _week_points(answer, week_start)]
    keys = set.intersection(*(set(cells) for cells in points))
    return {
        key
        for key in keys
        if all(
            cells[key]["status"] == "computed" and (cells[key]["numerator"] or 0) > 0
            for cells in points
        )
    }


def _every_cell(answer: dict) -> list[dict]:
    rows = list(answer["rows"])
    for point in answer["points"]:
        rows.extend(point["rows"])
    return [row["cell"] for row in rows]


def _below_minimum(answer: dict) -> list[dict]:
    return [
        cell
        for cell in _every_cell(answer)
        if cell["status"] == "insufficient_sample" and cell["reason"] == "below_minimum_sample"
    ]


def _assert_sample_rule(answer: dict) -> None:
    for cell in _every_cell(answer):
        assert cell["minimum_sample"] == MINIMUM, cell
        denominator = cell["denominator"]
        if cell["status"] == "not_computable" and cell["reason"] == SOURCE_REASON:
            continue
        if denominator == 0:
            assert cell["status"] == "not_computable", cell
        elif denominator < MINIMUM:
            assert cell["status"] == "insufficient_sample", cell
            assert cell["reason"] == "below_minimum_sample", cell
            assert cell["ratio"] is None, cell
        else:
            assert cell["status"] == "computed", cell
            assert cell["reason"] is None, cell
            exact = Fraction(cell["numerator"], denominator)
            assert abs(Fraction(str(cell["ratio"])) - exact) < Fraction(1, 1000), cell


def test_sample_minimum_and_cohort_minimum_are_unchanged():
    assert SAMPLE_POLICY_V1.version == SAMPLE_POLICY_VERSION
    assert SAMPLE_POLICY_V1.min_denominator_rate == MINIMUM
    assert SAMPLE_POLICY_V1.min_denominator_agent == 30
    assert SAMPLE_POLICY_V1.min_denominator_comparison == 50
    assert SAMPLE_POLICY_V1.hide_agent_counts_below_minimum is True
    assert sample_policy_digest(SAMPLE_POLICY_V1) == SAMPLE_POLICY_DIGEST
    assert MIN_COHORT_AGENTS == 5


def _row_count(model) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(model))


def _assert_trends_page(client, result, week_start: datetime) -> None:
    workspace_id, org = result.workspace_id, result.organization_id
    _login(client, result.accounts["owner"])

    workspace = _issues(client, workspace_id, week_start)
    assert _released(workspace), workspace["disclosure"]
    _assert_sample_rule(workspace)

    teams = _get(client, workspace_id, "/api/v1/teams")["items"]
    assert set(SMALL_TEAMS) <= {team["name"] for team in teams}
    team_answers = {
        team["name"]: _issues(client, workspace_id, week_start, team["id"]) for team in teams
    }
    released = {name: answer for name, answer in team_answers.items() if _released(answer)}
    for answer in released.values():
        _assert_sample_rule(answer)

    drilldown_path = f"/api/v1/organizations/{org}/workspaces/{workspace_id}/trends/issues"
    window = _default_query(week_start)
    grid = {"from": window["from"], "to": window["to"], "grain": "week"}
    drilldown = _get(client, workspace_id, drilldown_path, grid)
    blocks = {block["team"]["name"]: block["disclosure"] for block in drilldown["teams"]}
    for name in SMALL_TEAMS:
        assert not _released(team_answers[name]), team_answers[name]["disclosure"]
        assert blocks[name]["status"] == "withheld", (name, blocks[name])
        assert blocks[name]["reason"] == "small_group", (name, blocks[name])

    steady = _steady_issues(workspace, week_start)
    moving = {key for key in steady if len(set(_ratios(workspace, key))) > 1}
    team_steady = {name: _steady_issues(answer, week_start) for name, answer in released.items()}
    rich = {name for name, issues in team_steady.items() if len(issues) >= 3}
    shown = [
        cell
        for answer in (workspace, *(released[name] for name in sorted(released)))
        for cell in _below_minimum(answer)
    ]
    verdicts = {
        "AC1 workspace: >= 3 issue rows computed, numerator > 0, in all 4 weeks": len(steady) >= 3,
        "AC2 two released teams with AC1": len(rich) >= 2
        and all(blocks[name]["status"] == "released" for name in rich),
        "AC3 >= 2 of the AC1 rows move across the 4 weeks": len(moving) >= 2,
        "AC4 an insufficient_sample cell on the page": bool(shown)
        and all(1 <= cell["denominator"] < MINIMUM for cell in shown),
    }
    details = {
        "steady": sorted(steady),
        "ratios": {str(key): _ratios(workspace, key) for key in sorted(steady)},
        "teams": {name: sorted(issues) for name, issues in team_steady.items()},
        "withheld": {name: answer["disclosure"] for name, answer in team_answers.items()},
        "below_minimum": shown[:5],
    }
    failed = [name for name, held in verdicts.items() if not held]
    assert not failed, (failed, details)


def test_a_naive_dating_instant_is_refused_before_any_write(client, settings):
    models = (Organization, Workspace, User, Meeting, IngestionRecord)
    before = {model.__tablename__: _row_count(model) for model in models}
    with pytest.raises(ValueError):
        seed_demo_workspace(settings, now=datetime(2026, 10, 31, 0, 30))
    assert {model.__tablename__: _row_count(model) for model in models} == before


@pytest.mark.parametrize(("now", "week_start"), INSTANTS)
def test_default_trends_query_shows_real_trends(client, settings, now, week_start):
    result = seed_demo_workspace(settings, now=now)
    _assert_trends_page(client, result, week_start)


def _rows(answer: dict) -> list[list[dict]]:
    return [point["rows"] for point in answer["points"]]


def _ratios(answer: dict, key: tuple[str, str]) -> list[float | None]:
    return [_issue_cells(rows)[key]["ratio"] for rows in _rows(answer)]
