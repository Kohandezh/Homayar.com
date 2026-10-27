"""HOM-312: the coverage counters of the SQL path equal those of the Python path.

``compute_coverage`` and ``compute_coverage_by_team`` (the SQL path: slim fact rows, each
meeting's bucket from ``_evaluation_bucket``) must give exactly the counters of
``reference_coverage`` and ``reference_coverage_by_team`` (the HOM-72 Python path: ORM facts,
``assess_quality`` and the report's completeness rules), with the same number of statements.
The lazily built ``evaluated`` list and every meeting's bucket must be equal too.

Two datasets: the HOM-84 reference dataset (``_trend_reference``), and a seeded randomized set
(``_coverage_generated``) with every reachable evaluation and processing bucket and missing
and malformed fields.  The seed is printed; ``COVERAGE_PARITY_SEED`` replays another one.
Each runs under several populations, windows and recompute limits.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from sqlalchemy import event, select

from backend.database.saas_models import IngestionRecord, IngestionStatus, Meeting, Team
from backend.services import coverage_service as coverage
from backend.services.coverage_service import CoverageWindow
from backend.tests._coverage_generated import seed_generated
from backend.tests._database import inspection_session_factory
from backend.tests._trend_reference import build_reference
from backend.tests.test_coverage import BASE, Tenant

SEED = int(os.environ.get("COVERAGE_PARITY_SEED", "312"))
UNREACHABLE = "analysis_recompute_failed"  # needs a stale run, and stale is the earlier notice


def _populations(teams: list[str], agent: str | None = None) -> list[tuple[str, list[Any]]]:
    accepted = IngestionRecord.status == IngestionStatus.ACCEPTED
    populations: list[tuple[str, list[Any]]] = [
        ("workspace", []),
        ("one team", [accepted, Meeting.team_id.in_(teams[:1])]),
        ("language en", [accepted, Meeting.language == "en"]),
        ("no team", [accepted, Meeting.team_id.is_(None)]),
    ]
    if agent is not None:
        populations.append(("one agent", [accepted, Meeting.agent_user_id == agent]))
    return populations


def _counted(db: Any, call: Any, **kwargs: Any) -> tuple[Any, int]:
    seen: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "set_config" not in statement:
            seen.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        return call(db, **kwargs), len(seen)
    finally:
        event.remove(bind, "before_cursor_execute", record)


def _assert_parity(db: Any, *, label: str, teams: list[str], **kwargs: Any) -> dict[str, Any]:
    """Both paths on the same arguments; the reference counters."""
    fast, fast_statements = _counted(db, coverage.compute_coverage, **kwargs)
    slow, slow_statements = _counted(db, coverage.reference_coverage, **kwargs)
    assert fast.counters == slow.counters, (label, SEED)
    assert fast_statements == slow_statements, (label, fast_statements, slow_statements)

    def by_id(meetings: list[Any]) -> list[Any]:
        return sorted(meetings, key=lambda meeting: meeting.meeting_id)

    assert by_id(fast.evaluated) == by_id(slow.evaluated), (label, SEED)
    expected = {meeting.meeting_id: meeting.bucket for meeting in slow.evaluated}
    rows, _facts, bucket_of = coverage._counting(
        db,
        workspace_id=kwargs["workspace_id"],
        window=kwargs["window"],
        population=kwargs["population"],
    )
    buckets = {row.meeting_id: bucket_of(row) for row in rows if row.meeting_id in expected}
    assert buckets == expected, (label, SEED)

    (whole, split), fast_statements = _counted(
        db, coverage.compute_coverage_by_team, teams=teams, **kwargs
    )
    (whole_ref, split_ref), slow_statements = _counted(
        db, coverage.reference_coverage_by_team, teams=teams, **kwargs
    )
    assert whole.counters == whole_ref.counters, (label, SEED)
    assert {team: part.counters for team, part in split.items()} == {
        team: part.counters for team, part in split_ref.items()
    }, (label, SEED)
    assert fast_statements == slow_statements, (label, fast_statements, slow_statements)
    return slow.counters


def test_the_sql_path_counts_like_the_python_path_on_a_generated_set(client, email_outbox):
    print(f"HOM-312 coverage parity seed: {SEED} (COVERAGE_PARITY_SEED replays another)")
    tenant = Tenant(client, email_outbox, "coverage-parity@example.com")
    generated = seed_generated(tenant, seed=SEED)
    windows = {
        "day": CoverageWindow(start=BASE, end=BASE + timedelta(days=1)),
        "hours": CoverageWindow(start=BASE + timedelta(hours=6), end=BASE + timedelta(hours=18)),
    }
    whole: dict[str, Any] = {}
    with inspection_session_factory()() as db:
        for limit in (1, 3):
            for window_name, window in windows.items():
                for name, population in _populations(generated.teams):
                    counters = _assert_parity(
                        db,
                        label=f"{name}, {window_name}, limit {limit}",
                        teams=generated.teams,
                        workspace_id=tenant.workspace_id,
                        window=window,
                        recompute_max_attempts=limit,
                        population=population,
                    )
                    if (limit, window_name, name) == (3, "day", "workspace"):
                        whole = counters
    evaluation = whole["evaluation"]["buckets"]
    assert evaluation[UNREACHABLE] == 0
    assert all(count > 0 for name, count in evaluation.items() if name != UNREACHABLE), evaluation
    processing = whole["processing"]["buckets"]
    assert all(count > 0 for count in processing.values()), processing
    assert whole["ingestion"]["buckets"]["rejected"] > 0


def test_the_sql_path_counts_like_the_python_path_on_the_reference_dataset(client, email_outbox):
    ref = build_reference(client, email_outbox)
    windows = {
        "W1-W3": CoverageWindow(start=ref.start, end=ref.end),
        "wide": CoverageWindow(
            start=ref.start - timedelta(weeks=6), end=ref.end + timedelta(days=7)
        ),
    }
    with inspection_session_factory()() as db:
        for place in (ref.a, ref.b):
            teams = sorted(
                db.scalars(select(Team.id).where(Team.workspace_id == place.workspace_id))
            )
            agent = ref.agents["big"][0] if place is ref.a else ref.agents["delta"][0]
            for limit in (1, 3):
                for window_name, window in windows.items():
                    for name, population in _populations(teams, agent):
                        _assert_parity(
                            db,
                            label=f"{place.workspace_id} {name}, {window_name}, limit {limit}",
                            teams=teams,
                            workspace_id=place.workspace_id,
                            window=window,
                            recompute_max_attempts=limit,
                            population=population,
                        )
