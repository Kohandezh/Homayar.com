"""HOM-84 (WP15-T06, F61): the SQL statements of each trend route do not grow with the data.

A cost guard without a clock: one request per route, as the caller that sees the most, and
two restricted callers (the supervisor on ``/trends/issues``; the agent b1 as an organization
analyst who also holds a restricted membership in A, on the organization trends route), on the
reference dataset (``_trend_reference``), then on twice its conversations (every cohort
doubled, the same agents), then with 3 more teams of 5 agents each.  The statement count of
each route must stay the same (no N+1).  The ``set_config`` calls of the row-level-security
binding (PostgreSQL only) are left out, so one count holds on SQLite and PostgreSQL; the
literal numbers below are the counts of the reference dataset, pinned because they are the
same on both.

The pins include the authentication statements of the request (the session and its user, the
workspace or organization membership, and for an API key the key, its user and its
``last_used_at`` update).  A failure right after a change to authentication may be that
change, not a trend query: compare the statement lists before changing a pin.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event

from backend.database.relational import get_engine
from backend.tests._trend_reference import (
    Reference,
    add_teams,
    build_reference,
    seed_conversations,
)
from backend.tests.test_api_keys import _bearer

ROW = "issue:intent_report_dissatisfaction"

# route -> statements of one request on the reference dataset.  A = 3 (session, user,
# workspace membership); O = 4 (session, user, organization membership, scoped workspaces);
# P and G = the 2 population statements; coverage = 9 (1 ledger + 8 facts).
STATEMENTS = {
    "trends_issues": 14,  # A + P, G + coverage (the HOM-80 pin)
    "trends_issues_api_key": 15,  # key, user, last_used_at write, membership + P, G + coverage
    "trends_issues_supervisor": 15,  # A + the supervisor's assignments + P, G + coverage
    "trends_teams": 15,  # A + active teams + P, G + grouped coverage
    "trends_me": 15,  # A + the agent's assignments (restricted) + P, G + coverage
    "trends_conversations": 7,  # A + P, G + 2 evidence reads: ADR 0040 decision 13, 14 - 9 + 2
    "organization_trends": 28,  # O + 2 workspaces x (P, G, the caller's membership, coverage)
    # O + the analyst's scope grants + 1 workspace x (P, G, membership, its assignments, the
    # known meetings, coverage): ADR 0038 decision 7, A6
    "organization_trends_restricted": 19,
    "organization_drilldown": 17,  # O + active teams + P, G + membership + grouped coverage
    "organization_aggregates": 32,  # O + 2 workspaces x (2 counts + 12), ADR 0041 decision 25
    "voicemail_analytics": 12,  # A + the voicemail rows + 8 facts (ADR 0041 decision 24)
    "admin_coverage": 12,  # A + coverage
}


def _requests(ref: Reference) -> dict[str, tuple[str, str, str | None, dict[str, Any]]]:
    """route -> (caller, path, X-Workspace-ID, params)."""
    a, org = ref.a.workspace_id, ref.organization_id
    window = ref.window()
    return {
        "trends_issues": ("owner", "/api/v1/trends/issues", a, {**window, "grain": "week"}),
        "trends_issues_api_key": ("api_key", "/api/v1/trends/issues", a, window),
        "trends_issues_supervisor": ("supervisor", "/api/v1/trends/issues", a, window),
        "trends_teams": ("owner", "/api/v1/trends/teams", a, {**window, "grain": "week"}),
        "trends_me": ("agent", "/api/v1/trends/me", a, {**window, "grain": "week"}),
        "trends_conversations": (
            "owner",
            "/api/v1/trends/conversations",
            a,
            {**window, "trend_view": "issues", "row": ROW, "limit": "100"},
        ),
        "organization_trends": (
            "owner",
            f"/api/v1/organizations/{org}/trends/issues",
            None,
            {**window, "grain": "week"},
        ),
        "organization_trends_restricted": (
            "agent",
            f"/api/v1/organizations/{org}/trends/issues",
            None,
            {**window, "grain": "week"},
        ),
        "organization_drilldown": (
            "owner",
            f"/api/v1/organizations/{org}/workspaces/{a}/trends/issues",
            None,
            {**window, "grain": "week"},
        ),
        "organization_aggregates": ("owner", f"/api/v1/organizations/{org}/aggregates", None, {}),
        "voicemail_analytics": (
            "owner",
            "/api/v1/voicemails/analytics",
            a,
            {"received_from": window["from"], "received_to": window["to"]},
        ),
        "admin_coverage": ("owner", "/api/v1/admin/coverage", a, window),
    }


def _count(ref: Reference, caller: str, path: str, workspace: str | None, params: dict) -> int:
    seen: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "set_config" not in statement:
            seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        if caller == "api_key":
            ref.client.cookies.clear()
            response = ref.client.get(path, params=params, headers=_bearer(ref.reader_key))
        else:
            response = ref.people[caller].get(path, workspace_id=workspace, params=params)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, (path, response.text)
    return len(seen)


def _counts(ref: Reference) -> dict[str, int]:
    return {
        name: _count(ref, caller, path, workspace, params)
        for name, (caller, path, workspace, params) in _requests(ref).items()
    }


def test_trend_route_statements_do_not_grow_with_the_rows(client, email_outbox):
    ref = build_reference(client, email_outbox)
    base = _counts(ref)

    seed_conversations(ref)  # twice the conversations of every cohort
    doubled = _counts(ref)
    add_teams(ref, 3)  # 3 more teams, 15 more agents
    more_teams = _counts(ref)

    assert doubled == base, {k: (base[k], doubled[k]) for k in base if base[k] != doubled[k]}
    assert more_teams == base, {
        k: (base[k], more_teams[k]) for k in base if base[k] != more_teams[k]
    }
    assert base == STATEMENTS, sorted(base.items())
