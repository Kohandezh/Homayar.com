"""HOM-82 (WP15-T04): the role-scoped trend views through the routes.

* ``GET /api/v1/trends/teams``  supervisor team comparison (session only)
* ``GET /api/v1/trends/me``     agent self-view (session only)
* ``GET /api/v1/trends/issues`` with the HOM-82 rules (kind from data, the canonical team set,
  ``agent_user_id`` only the caller's own)
* ``GET /api/v1/organizations/{id}/trends/issues`` executive aggregates
* ``GET /api/v1/organizations/{id}/workspaces/{id}/trends/issues`` analyst drill-down

Each test builds one dataset and checks a table of cases; every refused case has its closest
allowed case and checks ``error.code``.  The rules are unit-tested without the app in
``test_role_trend_disclosure.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event, true, update

from backend.database.relational import get_engine, get_session_factory
from backend.database.saas_models import (
    Membership,
    OrganizationMembership,
    RecordScope,
    Team,
    TeamAssignment,
    TeamRole,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import (
    bind_organization_context,
    bind_tenant_context,
    bind_user_context,
)
from backend.issue_trends import validate_filters
from backend.security.context import CSRF_COOKIE
from backend.services.coverage_service import (
    CoverageWindow,
    compute_coverage,
    compute_coverage_by_team,
)
from backend.services.issue_trend_service import judge_population, trend_definition
from backend.services.rate_limit_service import RateLimiter
from backend.services.role_trend_service import current_week_start
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _cookies, _Persona, _workspace_with_roles
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trend_routes import _place
from backend.tests.test_issue_trend_service import (
    BASE,
    DAY,
    WINDOW,
    add_team,
    add_user,
    included,
    seed,
)
from backend.tests.test_organizations import _grant, _persona

TEAMS = "/api/v1/trends/teams"
ME = "/api/v1/trends/me"
ISSUES = "/api/v1/trends/issues"
WEEK = timedelta(days=7)


def _answer(response) -> tuple[int, str | None, str | None]:
    """``(status, error.code, error.details.key)``; the code and key are ``None`` on 200."""
    if response.status_code == 200:
        return 200, None, None
    error = response.json()["error"]
    return response.status_code, error["code"], (error.get("details") or {}).get("key")


def _params(*extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [*WINDOW.items(), *extra]


def _assign(
    place: Any,
    team_id: str,
    user_id: str,
    role: str,
    *,
    by: str,
    expired: bool = False,
) -> None:
    now = datetime.now(UTC)
    with inspection_session_factory()() as db:
        db.add(
            TeamAssignment(
                workspace_id=place.workspace_id,
                team_id=team_id,
                user_id=user_id,
                role=TeamRole(role),
                assigned_by_user_id=by,
                valid_from=now - 3 * DAY if expired else now - DAY,
                valid_until=now - DAY if expired else None,
            )
        )
        db.commit()


def _deactivate(team_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Team).where(Team.id == team_id).values(is_active=False))
        db.commit()


def _expire_membership(membership_id: str) -> None:
    now = datetime.now(UTC)
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.id == membership_id)
            .values(valid_from=now - 3 * DAY, valid_until=now - DAY)
        )
        db.commit()


# The query statements of one route call, made of a fixed part F (session, user, membership
# and the HOM-80 population statements P and G, plus 9 coverage statements) and the view's own
# statements.  ``/trends/issues`` is F = 14 for an unrestricted caller with a coverage candidate:
# 3 (session, user, membership) + 2 (P and G) + 9 (coverage).
ISSUES_STATEMENTS = 14


def _statements(send) -> tuple[int, Any]:
    """Query statements of one request.  The ``set_config`` calls of the row-level-security
    binding (PostgreSQL only) are left out, so one count holds on SQLite and PostgreSQL."""
    seen: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "set_config" not in statement:
            seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = send()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    return len(seen), response.json()


def _row(block: dict[str, Any], family: str, issue: str | None = None) -> dict[str, Any]:
    return next(r for r in block["rows"] if r["family"] == family and r["issue"] == issue)


def _agents(count: int, prefix: str) -> list[str]:
    return [add_user(f"{prefix}{n}") for n in range(count)]


def _calls(place: Any, team_id: str | None, agents: list[str], each: int, **options: Any):
    for agent in agents:
        for _ in range(each):
            seed(place, team_id=team_id, agent_user_id=agent, **options)


def _coverage_matches_population(block: dict[str, Any]) -> bool:
    processing = block["coverage"]["processing"]
    counted = block["population"]["included"] + sum(block["population"]["excluded"].values())
    return processing["numerator"] == counted


# --- 1. workspace views: who may call (deny table W1 to W12) ----------------------------------


def test_workspace_views_allow_and_deny(client, email_outbox, monkeypatch):
    owner, people, memberships = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"sup": "member", "agent": "member", "viewer": "viewer", "stale": "member"},
    )
    place = _place(owner)
    alpha, beta, gamma = (add_team(place, name) for name in ("alpha", "beta", "gamma"))
    _deactivate(gamma)
    _assign(place, alpha, people["sup"].user_id, "supervisor", by=owner.user_id)
    _assign(place, alpha, people["agent"].user_id, "agent", by=owner.user_id)
    _assign(place, beta, people["stale"].user_id, "supervisor", by=owner.user_id, expired=True)
    seed(place, team_id=alpha, agent_user_id=people["agent"].user_id)
    seed(place, team_id=beta)
    seed(place, team_id=alpha, created_by=people["agent"].user_id)  # an upload, not as agent
    RateLimiter.reset_memory()  # five sign-ups an hour per address; this test needs six
    outsider_session = login_user(
        client, register_user(client, email_outbox, email="trend-out@example.com")
    )
    outsider = _Persona(client, owner.workspace_id, outsider_session, _cookies(client))
    reader = _mint(client, owner.headers, owner.cookies, scopes=["meeting:read"])
    writer = _mint(client, owner.headers, owner.cookies, scopes=["meeting:create"])
    assert reader.status_code == 201 and writer.status_code == 201, reader.text
    unknown = str(uuid.uuid4())
    eleven = [("team_id", str(uuid.uuid4())) for _ in range(11)]
    sup, agent, viewer = people["sup"], people["agent"], people["viewer"]

    # (label, persona, path, extra params, (status, code, key))
    cases = [
        ("owner compares", owner, TEAMS, (), (200, None, None)),
        ("supervisor compares own team", sup, TEAMS, (), (200, None, None)),
        ("W3 outsider", outsider, TEAMS, (), (403, "workspace_access_denied", None)),
        ("W3 outsider, me", outsider, ME, (), (403, "workspace_access_denied", None)),
        (
            "W5 scope key",
            owner,
            TEAMS,
            (("workspace_id", owner.workspace_id),),
            (422, "filter_scope_not_allowed", "workspace_id"),
        ),
        (
            "W5 scope key, me",
            agent,
            ME,
            (("role", "owner"),),
            (422, "filter_scope_not_allowed", "role"),
        ),
        (
            "W6 agent filter on teams",
            owner,
            TEAMS,
            (("agent_user_id", "xyz"),),
            (422, "trend_parameter_not_allowed", "agent_user_id"),
        ),
        (
            "W6 agent filter on me",
            agent,
            ME,
            (("agent_user_id", agent.user_id),),
            (422, "trend_parameter_not_allowed", "agent_user_id"),
        ),
        ("W6 allow: no agent filter", agent, ME, (), (200, None, None)),
        ("W7 viewer without assignment", viewer, TEAMS, (), (403, "team_scope_denied", None)),
        ("W7 agent only", agent, TEAMS, (), (403, "team_scope_denied", None)),
        ("W7 allow: viewer self-view", viewer, ME, (), (200, None, None)),
        ("W8 expired supervisor", people["stale"], TEAMS, (), (403, "team_scope_denied", None)),
        (
            "W9 another team",
            sup,
            TEAMS,
            (("team_id", beta),),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "W9 unknown team",
            sup,
            TEAMS,
            (("team_id", unknown),),
            (403, "team_scope_denied", "team_id"),
        ),
        ("W9 allow: supervised team", sup, TEAMS, (("team_id", alpha),), (200, None, None)),
        (
            "W10 inactive team",
            owner,
            TEAMS,
            (("team_id", gamma),),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "W10 unknown team",
            owner,
            TEAMS,
            (("team_id", unknown),),
            (403, "team_scope_denied", "team_id"),
        ),
        ("W10 allow: active team", owner, TEAMS, (("team_id", beta),), (200, None, None)),
        ("W11 eleven teams", owner, TEAMS, tuple(eleven), (422, "trend_too_many_teams", "team_id")),
        # control: ten ids pass the count check, then the scope check refuses unknown ids
        (
            "W11 control: ten teams",
            owner,
            TEAMS,
            tuple(eleven[:10]),
            (403, "team_scope_denied", "team_id"),
        ),
        (
            "W12 another agent",
            owner,
            ISSUES,
            (("agent_user_id", agent.user_id),),
            (403, "agent_scope_denied", "agent_user_id"),
        ),
        (
            "W12 own and another",
            agent,
            ISSUES,
            (("agent_user_id", agent.user_id), ("agent_user_id", sup.user_id)),
            (403, "agent_scope_denied", "agent_user_id"),
        ),
        (
            "W12 allow: own agent id",
            agent,
            ISSUES,
            (("agent_user_id", agent.user_id),),
            (200, None, None),
        ),
    ]
    for label, persona, path, extra, expected in cases:
        response = persona.get(path, params=_params(*extra))
        assert _answer(response) == expected, (label, response.text)

    # the statements of a member who supervises: F + 1 (teams) + 1 (their assignments)
    issues_count = _statements(lambda: sup.get(ISSUES, params=_params()))[0]
    teams_count = _statements(lambda: sup.get(TEAMS, params=_params()))[0]
    assert issues_count == ISSUES_STATEMENTS and teams_count == issues_count + 2, (
        issues_count,
        teams_count,
    )

    # the allowed answers name the right teams and the right meetings
    assert [t["team"]["name"] for t in owner.get(TEAMS, params=_params()).json()["teams"]] == [
        "alpha",
        "beta",
    ]
    assert [t["team"]["id"] for t in sup.get(TEAMS, params=_params()).json()["teams"]] == [alpha]
    assert included(agent.get(ME, params=_params()).json()) == 1  # not the upload
    assert included(viewer.get(ME, params=_params()).json()) == 0

    # W4: the same user after the membership window ended
    _expire_membership(memberships["stale"])
    expired = people["stale"].get(ME, params=_params())
    assert _answer(expired) == (403, "workspace_access_denied", None), expired.text
    assert _answer(agent.get(ME, params=_params()))[0] == 200  # allow: in its window

    # W11 by default: eleven comparable teams
    for n in range(9):
        add_team(place, f"extra{n}")
    assert _answer(owner.get(TEAMS, params=_params())) == (422, "trend_too_many_teams", "team_id")
    assert _answer(owner.get(TEAMS, params=_params(("team_id", alpha))))[0] == 200

    # W1 and W2: no session, and every API key gets one answer before any lookup
    client.cookies.clear()
    for path in (TEAMS, ME, ISSUES):
        anonymous = client.get(
            path, params=_params(), headers={"X-Workspace-ID": place.workspace_id}
        )
        assert _answer(anonymous) == (401, "authentication_required", None), path
    keys = {
        "with the scope": _bearer(reader.json()["key"]),
        "without the scope": _bearer(writer.json()["key"]),
        "with a foreign workspace": _bearer(
            reader.json()["key"], **{"X-Workspace-ID": str(uuid.uuid4())}
        ),
        "unknown": _bearer("hk_00000000.nope"),
    }
    for label, headers in keys.items():
        for path in (TEAMS, ME):
            refused = client.get(path, params=_params(), headers=headers)
            assert _answer(refused) == (401, "api_key_not_accepted", None), (label, path)
    allowed = client.get(ISSUES, params=_params(), headers=keys["with the scope"])
    assert allowed.status_code == 200, allowed.text


# --- 2. the team comparison answer ----------------------------------------------------------


def test_team_comparison_answer(client, email_outbox, monkeypatch):
    owner, _people_, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles={})
    place = _place(owner)
    first = add_team(place, "Alpha")
    second = add_team(place, "beta")
    _calls(place, first, _agents(5, "p"), 20)  # 100 calls ...
    _calls(place, second, _agents(5, "q"), 20, language="en")  # ... 100 calls
    lone = add_user("n1")
    _calls(place, None, [lone], 12)  # one agent alone, no team
    seed(place, team_id=first)  # an unattributed call: first has 101 calls
    pair = (("team_id", first), ("team_id", second))

    # Worked example 1b: one canonical released set, whatever the request names.  The two
    # teams leave n1 alone, so the team with fewer calls (second: 100) is withheld.
    body = owner.get(TEAMS, params=_params(*pair)).json()
    blocks = {block["team"]["id"]: block for block in body["teams"]}
    assert [block["team"]["name"] for block in body["teams"]] == ["Alpha", "beta"]
    assert blocks[first]["disclosure"] == {
        "status": "released",
        "reason": None,
        "kind": "rate",
        "masked": [],
    }
    assert blocks[second]["disclosure"]["status"] == "masked"
    assert blocks[second]["disclosure"]["masked"] == ["window"]
    assert _row(blocks[second], "service_complaint")["cell"]["status"] == "withheld"
    assert _row(blocks[second], "service_complaint")["cell"]["denominator"] == 100
    for one in (first, second):
        alone = owner.get(TEAMS, params=_params(("team_id", one))).json()["teams"][0]
        assert alone["disclosure"] == blocks[one]["disclosure"], one
        issue = owner.get(ISSUES, params=_params(("team_id", one))).json()
        assert issue["disclosure"]["status"] == blocks[one]["disclosure"]["status"], one
    both = owner.get(ISSUES, params=_params(*pair)).json()
    assert both["disclosure"]["status"] == "masked"
    whole = owner.get(ISSUES, params=_params()).json()
    assert whole["disclosure"]["status"] == "released" and whole["disclosure"]["kind"] == "rate"
    assert all(c["status"] == "withheld" for c in body["comparisons"] if c["family"] != "qa")

    # A third, small team (2 agents, 40 calls) is the smallest: it moves into the remainder,
    # which then holds 3 agents and 52 calls, and both large teams are released.
    third = add_team(place, "Gamma")
    _calls(place, third, _agents(2, "r"), 20)
    count_two, two = _statements(lambda: owner.get(TEAMS, params=_params(*pair)))
    count_three, body = _statements(lambda: owner.get(TEAMS, params=_params()))
    issues_count = _statements(lambda: owner.get(ISSUES, params=_params()))[0]
    team_count = _statements(lambda: owner.get(ISSUES, params=_params(("team_id", first))))[0]
    assert issues_count == team_count == ISSUES_STATEMENTS  # team_id: the same F
    # one load of the scope, split in Python: F + 1 (the active teams), for 2 or 3 teams
    assert count_two == count_three == issues_count + 1, (count_two, count_three)
    blocks = {block["team"]["id"]: block for block in body["teams"]}
    assert [block["team"]["name"] for block in body["teams"]] == ["Alpha", "beta", "Gamma"]
    assert blocks[first]["disclosure"]["status"] == "released"
    assert blocks[second]["disclosure"]["status"] == "released"
    assert blocks[third]["disclosure"]["kind"] == "agent"
    assert blocks[third]["disclosure"]["status"] == "masked"
    assert blocks[second]["mix"] == {"language": {"en": 100}, "source_type": {"file_upload": 100}}
    assert blocks[first]["mix"]["language"] == {"fa": 101}
    for block in body["teams"]:
        assert block["coverage"]["basis"] == "trend_population"
        assert _coverage_matches_population(block), block["team"]
        assert {"coverage", "scorecard", "missing_data"} <= set(block)
    assert body["notices"] == ["correlation_not_cause", "unadjusted_comparison"]
    pairs = [(c["left_team_id"], c["right_team_id"]) for c in body["comparisons"]]
    assert pairs[0] == (first, second) and set(pairs) == {
        (first, second),
        (first, third),
        (second, third),
    }
    compared = [c for c in body["comparisons"] if c["left_team_id"] == first]
    complaint = next(
        c for c in compared if c["right_team_id"] == second and c["family"] == "service_complaint"
    )
    assert complaint["status"] == "computed" and complaint["minimum_sample"] == 50
    assert complaint["difference"] == 0.0
    qa = next(c for c in compared if c["right_team_id"] == second and c["family"] == "qa")
    assert qa["status"] == "not_computable"
    small = [c for c in body["comparisons"] if third in (c["left_team_id"], c["right_team_id"])]
    assert {c["reason"] for c in small if c["family"] != "qa"} == {"small_group"}
    for comparison in body["comparisons"]:
        assert not {"rank", "score", "best", "worst"} & set(comparison)
    assert two["teams"][0]["team"]["id"] == first


# --- 3. the self-view and the HOM-82 rules on GET /trends/issues -------------------------------


def test_self_view_and_issue_trend_disclosure(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"agent": "member"}
    )
    place = _place(owner)
    agent = people["agent"]
    team = add_team(place, "Tier 1")
    others = _agents(5, "b")
    _calls(place, team, [agent.user_id], 3)  # the agent's own calls
    _calls(place, team, others, 40)  # 200 calls of five other agents
    for other in others[:2]:  # uploads by the agent for someone else: not the self-view
        seed(place, team_id=team, agent_user_id=other, created_by=agent.user_id)
    lone = add_user("lone")
    _calls(place, None, [lone], 12)  # worked example 1: A alone with no team

    me = agent.get(ME, params=_params()).json()
    assert included(me) == 3 and me["subject"] == {"agent_user_id": agent.user_id}
    assert me["disclosure"] == {"status": "released", "reason": None, "kind": "agent", "masked": []}
    assert _row(me, "service_complaint")["cell"]["numerator"] is None  # HOM-79: under 30
    assert me["coverage"]["basis"] == "trend_population"
    assert me["coverage"]["processing"]["numerator"] == 3
    later = BASE + 2 * DAY
    _calls(place, team, [agent.user_id], 9, received_at=later)
    window = (("from", later.isoformat()), ("to", (later + DAY).isoformat()))
    count_small, _small = _statements(lambda: agent.get(ME, params=_params()))
    count_large, large = _statements(lambda: agent.get(ME, params=list(window)))
    issues_count = _statements(lambda: agent.get(ISSUES, params=_params()))[0]
    assert count_small == count_large == issues_count == ISSUES_STATEMENTS  # me: F + 0
    assert included(large) == 9

    # Worked example 1: the workspace total minus a team that is all but one person
    whole = owner.get(ISSUES, params=_params()).json()
    assert whole["disclosure"]["kind"] == "rate" and whole["disclosure"]["status"] == "released"
    assert included(whole) == 3 + 200 + 2 + 12
    narrowed = owner.get(ISSUES, params=_params(("team_id", team))).json()
    assert narrowed["disclosure"]["status"] == "masked", narrowed["disclosure"]
    cell = _row(narrowed, "service_complaint")["cell"]
    assert cell["status"] == "withheld" and cell["numerator"] is None
    assert cell["denominator"] == 205
    # allow-control: with A on 30 calls the remainder hides nothing and the team is released
    _calls(place, None, [lone], 18)
    released = owner.get(ISSUES, params=_params(("team_id", team))).json()
    assert released["disclosure"]["status"] == "released"

    # Worked examples 2 and 3: no other agent's cohort, no phantom second id
    for params in (
        (("agent_user_id", others[0]),),
        (("agent_user_id", owner.user_id), ("agent_user_id", str(uuid.uuid4()))),
    ):
        refused = owner.get(ISSUES, params=_params(*params))
        assert _answer(refused) == (403, "agent_scope_denied", "agent_user_id"), params

    # The points of a small group are all shown or all withheld, apart from the window
    small = Tenant(client, email_outbox, "trend-points@example.com")
    # F2: an owner of a workspace with no active team has nothing to compare (200, empty)
    empty = small.get(TEAMS, **WINDOW)
    assert empty.status_code == 200, empty.text
    assert empty.json()["teams"] == [] and empty.json()["comparisons"] == []
    named = small.get(TEAMS, team_id=str(uuid.uuid4()), **WINDOW)
    assert _answer(named) == (403, "team_scope_denied", "team_id")  # a named team: refused
    member = agent.get(TEAMS, params=_params())  # control: a member with no supervised team
    assert _answer(member) == (403, "team_scope_denied", None)
    pair = _agents(2, "s")
    _calls(small, None, pair, 16)  # 32 calls on day 1
    _calls(small, None, pair[:1], 2, received_at=BASE + DAY)  # 2 calls on day 2
    two_days = {"from": BASE.isoformat(), "to": (BASE + 2 * DAY).isoformat()}
    daily = small.get(ISSUES, grain="day", timezone="UTC", **two_days).json()
    assert daily["disclosure"]["kind"] == "agent"
    assert daily["disclosure"]["masked"] == ["points"]
    assert _row(daily, "service_complaint")["cell"]["numerator"] == 34
    for point in daily["points"]:
        if not included(point):
            continue  # an empty day shows 0 of 0
        for row in point["rows"]:
            assert row["cell"]["numerator"] is None, (point["from"], row["family"])
    plain = small.get(ISSUES, **two_days).json()
    assert plain["rows"] == daily["rows"]  # the window does not change with the grain


# --- organization views ---------------------------------------------------------------------


def _org_workspace(owner, org: str, name: str) -> SimpleNamespace:
    created = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": name})
    assert created.status_code == 201, created.text
    return SimpleNamespace(
        workspace_id=created.json()["id"], organization_id=org, owner_id=owner.user_id
    )


def _scope_grant(owner, org: str, membership: dict, place, team_id: str | None = None):
    payload = {"membership_id": membership["id"], "workspace_id": place.workspace_id}
    if team_id is not None:
        payload["team_id"] = team_id
    granted = owner.post(f"/api/v1/organizations/{org}/scope-grants", json=payload)
    assert granted.status_code == 201, granted.text


def _week_window(start: datetime, weeks: int = 1, **extra: str) -> dict[str, str]:
    return {"from": start.isoformat(), "to": (start + weeks * WEEK).isoformat(), **extra}


def _drill(org: str, place) -> str:
    return f"/api/v1/organizations/{org}/workspaces/{place.workspace_id}/trends/issues"


def _released(block: dict[str, Any]) -> tuple[str, str | None]:
    return block["disclosure"]["status"], block["disclosure"]["reason"]


def _install_member(place, user_id: str) -> str:
    """A workspace ``member`` row, like a delegated installation, without the seat checks."""
    with inspection_session_factory()() as db:
        membership = Membership(
            workspace_id=place.workspace_id,
            user_id=user_id,
            role=WorkspaceRole.MEMBER,
            is_active=True,
            valid_from=datetime.now(UTC) - DAY,
        )
        db.add(membership)
        db.commit()
        return membership.id


def _set_membership_role(membership_id: str, role: WorkspaceRole) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(Membership).where(Membership.id == membership_id).values(role=role))
        db.commit()


def _set_record_scope(place, scope: RecordScope) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == place.workspace_id).values(record_scope=scope)
        )
        db.commit()


def _expire_organization_membership(membership_id: str) -> None:
    now = datetime.now(UTC)
    with inspection_session_factory()() as db:
        db.execute(
            update(OrganizationMembership)
            .where(OrganizationMembership.id == membership_id)
            .values(valid_from=now - 3 * DAY, valid_until=now - DAY)
        )
        db.commit()


def test_org_views_allow_and_deny(client, email_outbox):
    owner = _persona(client, email_outbox, email="ot-owner@example.com", name="Owner")
    analyst = _persona(client, email_outbox, email="ot-analyst@example.com", name="Analyst")
    executive = _persona(client, email_outbox, email="ot-exec@example.com", name="Exec")
    narrow = _persona(client, email_outbox, email="ot-narrow@example.com", name="Narrow")
    gone = _persona(client, email_outbox, email="ot-gone@example.com", name="Gone")
    RateLimiter.reset_memory()  # five sign-ups an hour per address; this test needs six
    stranger = _persona(client, email_outbox, email="ot-stranger@example.com", name="Str")
    org = owner.organization_id
    support = _org_workspace(owner, org, "Support")
    sales = _org_workspace(owner, org, "Sales")
    tier1 = owner.post("/api/v1/teams", workspace_id=support.workspace_id, json={"name": "T1"})
    assert tier1.status_code == 201, tier1.text
    tier1 = tier1.json()["id"]
    memberships = {
        name: _grant(owner, org, person, role)
        for name, person, role in (
            ("analyst", analyst, "analyst"),
            ("executive", executive, "executive"),
            ("narrow", narrow, "analyst"),
            ("gone", gone, "analyst"),
        )
    }
    for name in ("analyst", "executive", "gone"):
        _scope_grant(owner, org, memberships[name], support)
    _scope_grant(owner, org, memberships["narrow"], support, tier1)
    revoked = owner.delete(f"/api/v1/organizations/{org}/members/{memberships['gone']['id']}")
    assert revoked.status_code == 204, revoked.text
    week_start = current_week_start(datetime.now(UTC))  # computed once for the whole test
    closed = week_start - 2 * WEEK
    seed(support, received_at=closed + DAY, team_id=tier1)
    executive_path = f"/api/v1/organizations/{org}/trends/issues"
    window = _week_window(closed, 2)

    # (label, persona, path, params, (status, code, key))
    cases = [
        ("owner reads", owner, executive_path, window, (200, None, None)),
        ("executive reads", executive, executive_path, window, (200, None, None)),
        ("analyst reads", analyst, executive_path, window, (200, None, None)),
        ("O3 stranger", stranger, executive_path, window, (404, "organization_not_found", None)),
        (
            "O3 stranger, drill-down",
            stranger,
            _drill(org, support),
            window,
            (404, "organization_not_found", None),
        ),
        ("O4 revoked", gone, executive_path, window, (404, "organization_not_found", None)),
        (
            "O5 executive drills down",
            executive,
            _drill(org, support),
            window,
            (403, "permission_denied", None),
        ),
        ("O5 allow: analyst drills down", analyst, _drill(org, support), window, (200, None, None)),
        (
            "O6 workspace without a grant",
            analyst,
            _drill(org, sales),
            window,
            (404, "workspace_not_found", None),
        ),
        (
            "O6 another organization's workspace",
            analyst,
            f"/api/v1/organizations/{org}/workspaces/{stranger.personal_workspace_id}"
            "/trends/issues",
            window,
            (404, "workspace_not_found", None),
        ),
        (
            "O6 unknown workspace",
            analyst,
            f"/api/v1/organizations/{org}/workspaces/{uuid.uuid4()}/trends/issues",
            window,
            (404, "workspace_not_found", None),
        ),
        ("O6 allow: a team grant", narrow, _drill(org, support), window, (200, None, None)),
        ("O8 grain day", executive, executive_path, {**window, "grain": "day"},
         (422, "trend_grain_invalid", "grain")),
        ("O8 grain month", analyst, _drill(org, support), {**window, "grain": "month"},
         (422, "trend_grain_invalid", "grain")),
        ("O8 allow: grain week", executive, executive_path, {**window, "grain": "week"},
         (200, None, None)),
        ("O9 from not aligned",
         executive,
         executive_path,
         {**window, "from": (closed + timedelta(hours=1)).isoformat()},
         (422, "trend_window_not_aligned", "from")),
        ("O9 open week",
         executive,
         executive_path,
         {**window, "to": (week_start + 2 * WEEK).isoformat()},
         (422, "trend_window_not_aligned", "to")),
        ("O10 scope key", executive, executive_path, {**window, "workspace_id": "x"},
         (422, "filter_scope_not_allowed", "workspace_id")),
        ("O11 53 weeks", executive, executive_path, _week_window(week_start - 53 * WEEK, 53),
         (422, "filter_window_invalid", None)),
        ("O11 allow: 52 weeks", executive, executive_path,
         _week_window(week_start - 52 * WEEK, 52), (200, None, None)),
        ("default window", executive, executive_path, {}, (200, None, None)),
    ]  # fmt: skip
    for key, value in (
        ("team_id", "xyz"),
        ("agent_user_id", executive.user_id),
        ("language", "fa"),
        ("source_type", "api"),
        ("timezone", "UTC"),
    ):
        for path in (executive_path, _drill(org, support)):
            cases.append(
                (
                    f"O7 {key}",
                    analyst,
                    path,
                    {**window, key: value},
                    (422, "trend_parameter_not_allowed", key),
                )
            )
    for label, persona, path, params, expected in cases:
        response = persona.get(path, params=params)
        assert _answer(response) == expected, (label, response.text)

    # the team grant sees its team only; an inactive granted team drops the workspace
    narrowed = narrow.get(_drill(org, support), params=window).json()
    assert narrowed["team_ids"] == [tier1] and [t["team"]["id"] for t in narrowed["teams"]] == [
        tier1
    ]
    default = executive.get(executive_path).json()["window"]
    assert default["to"] == week_start.isoformat()
    _deactivate(tier1)
    assert narrow.get(executive_path, params=window).json()["workspaces"] == []
    dropped = narrow.get(_drill(org, support), params=window)
    assert _answer(dropped) == (404, "workspace_not_found", None)

    # O4: a membership after its window
    _expire_organization_membership(memberships["executive"]["id"])
    expired = executive.get(executive_path, params=window)
    assert _answer(expired) == (404, "organization_not_found", None), expired.text

    # O1 and O2: no session, and every API key, valid or not
    key = _mint(
        client,
        {"X-CSRF-Token": owner.cookies[CSRF_COOKIE], "X-Workspace-ID": support.workspace_id},
        owner.cookies,
        scopes=["meeting:read"],
    )
    assert key.status_code == 201, key.text
    client.cookies.clear()
    for path in (executive_path, _drill(org, support)):
        assert _answer(client.get(path, params=window)) == (401, "authentication_required", None)
        for raw in (key.json()["key"], "hk_00000000.nope"):
            refused = client.get(path, params=window, headers=_bearer(raw))
            assert _answer(refused) == (401, "api_key_not_accepted", None), (path, raw)


def test_org_views_withhold_small_groups(client, email_outbox):
    owner = _persona(client, email_outbox, email="ow-owner@example.com", name="Owner")
    analyst = _persona(client, email_outbox, email="ow-analyst@example.com", name="Analyst")
    narrow = _persona(client, email_outbox, email="ow-narrow@example.com", name="Narrow")
    executive = _persona(client, email_outbox, email="ow-exec@example.com", name="Exec")
    org = owner.organization_id
    support = _org_workspace(owner, org, "Support")
    t1, t2 = (add_team(support, name) for name in ("T1", "T2"))
    memberships = {
        name: _grant(owner, org, person, role)
        for name, person, role in (
            ("analyst", analyst, "analyst"),
            ("narrow", narrow, "analyst"),
            ("executive", executive, "executive"),
        )
    }
    _scope_grant(owner, org, memberships["analyst"], support)
    _scope_grant(owner, org, memberships["narrow"], support, t1)
    week_start = current_week_start(datetime.now(UTC))  # computed once for the whole test
    weeks = [week_start - n * WEEK for n in (3, 2, 1)]

    # Worked example 4 (the B2 table): distinct agents per group and week, one call each
    a, b, c = _agents(6, "a"), _agents(6, "b"), _agents(5, "c")
    n1 = add_user("n1")
    table = [
        ((t1, a), (t2, b[:2]), (None, c)),
        ((t1, a), (t2, b), (None, [n1])),
        ((t1, a[:2]), (t2, b), (None, c)),
    ]
    for start, groups in zip(weeks, table, strict=True):
        for team_id, agents in groups:
            _calls(support, team_id, agents, 1, received_at=start + DAY)
    seed(support, received_at=weeks[0] + DAY, with_meeting=False)  # a ledger row, no meeting
    drill = _drill(org, support)

    def drilled(first: int, count: int = 1, persona=analyst) -> dict[str, Any]:
        response = persona.get(drill, params=_week_window(weeks[first], count))
        assert response.status_code == 200, response.text
        body = response.json()
        teams = {block["team"]["id"]: _released(block) for block in body["teams"]}
        return {"workspace": _released(body["workspace"]), **teams, "body": body}

    expectations = [
        # (first week, weeks, workspace, T1, T2)
        (0, 1, ("released", None), ("released", None), ("withheld", "small_group")),
        (0, 2, ("released", None), ("withheld", "complementary_disclosure"),
         ("withheld", "small_group")),
        (1, 1, ("released", None), ("withheld", "complementary_disclosure"),
         ("withheld", "complementary_disclosure")),
        (1, 2, ("released", None), ("withheld", "small_group"),
         ("withheld", "complementary_disclosure")),
        (2, 1, ("released", None), ("withheld", "small_group"), ("released", None)),
    ]  # fmt: skip
    for first, count, workspace, team1, team2 in expectations:
        answer = drilled(first, count)
        assert (answer["workspace"], answer[t1], answer[t2]) == (workspace, team1, team2), (
            first,
            count,
        )
    week_one = drilled(0)["body"]
    for block in (week_one["workspace"], week_one["teams"][0]):
        assert _coverage_matches_population(block), block["disclosure"]
    withheld = week_one["teams"][1]
    assert withheld["population"] is None and withheld["coverage"] is None
    assert withheld["missing_data"] is None and withheld["rows"] == []
    assert week_one["teams"][1]["scorecard"]["status"] == "not_available"
    weekly = analyst.get(drill, params=_week_window(weeks[0], 1, grain="week")).json()
    assert (
        len(weekly["workspace"]["points"]) == 1
        and weekly["workspace"]["points"][0]["partial"] is False
    )

    # M1: the team grant (W, T1) comes from the same weekly decision.  In week 2 T1 alone
    # passes, but the workspace minus T1 and T2 is n1 alone, so Rel_2 is empty.
    executive_path = f"/api/v1/organizations/{org}/trends/issues"
    granted = narrow.get(executive_path, params=_week_window(weeks[0], 1)).json()["workspaces"]
    assert [w["team_ids"] for w in granted] == [[t1]] and _released(granted[0]) == (
        "released",
        None,
    )
    second = narrow.get(executive_path, params=_week_window(weeks[1], 1)).json()["workspaces"]
    assert _released(second[0]) == ("withheld", "complementary_disclosure")
    assert _released(drilled(1, persona=narrow)["body"]["workspace"]) == (
        "withheld",
        "complementary_disclosure",
    )
    whole = analyst.get(executive_path, params=_week_window(weeks[0], 3)).json()["workspaces"]
    assert [w["workspace_id"] for w in whole] == [support.workspace_id]
    assert _released(whole[0]) == ("released", None)  # 13 agents every week

    # compute_coverage_by_team (A4): the whole equals compute_coverage, the groups add up,
    # and a ledger row with no meeting is in the whole only
    coverage_window = CoverageWindow(start=weeks[0], end=weeks[2] + WEEK)
    with inspection_session_factory()() as db:
        everything = compute_coverage(
            db, workspace_id=support.workspace_id, window=coverage_window, recompute_max_attempts=3
        )
        whole_cov, groups = compute_coverage_by_team(
            db,
            workspace_id=support.workspace_id,
            window=coverage_window,
            recompute_max_attempts=3,
            teams=[t1, t2, "no-such-team"],
        )
    assert whole_cov.counters == everything.counters
    assert set(groups) == {t1, t2, None, "no-such-team"}
    processing = [groups[key].counters["processing"] for key in groups]
    assert sum(p["numerator"] for p in processing) == whole_cov.counters["processing"]["numerator"]
    assert (
        sum(p["denominator"] for p in processing) + 1
        == whole_cov.counters["processing"]["denominator"]
    )
    assert groups["no-such-team"].counters["processing"]["denominator"] == 0

    # Worked example 5 (M2): five agents in a week, but only a1's calls are analysed
    sales = _org_workspace(owner, org, "Sales")
    _scope_grant(owner, org, memberships["executive"], sales)
    team = add_team(sales, "T")
    agents = _agents(5, "m")
    _calls(sales, team, agents[:1], 12, received_at=weeks[0] + DAY)
    _calls(sales, team, agents[1:], 3, received_at=weeks[0] + DAY, run=None)
    _calls(sales, team, agents, 1, received_at=weeks[1] + DAY)  # allow-control week
    first = executive.get(executive_path, params=_week_window(weeks[0], 1)).json()["workspaces"]
    assert _released(first[0]) == ("withheld", "small_group")
    assert first[0]["population"] is None and first[0]["coverage"] is None
    later = executive.get(executive_path, params=_week_window(weeks[1], 1)).json()["workspaces"]
    assert _released(later[0]) == ("released", None)

    # A6 (the final review's probe): the analyst also supervises team T as a restricted
    # member of the workspace.  T has 4 agents with 10 calls each, n1 has 12 calls and no
    # team.  The block passes (5 agents), but minus what the analyst knows it is n1 alone.
    probe = _org_workspace(owner, org, "Probe")
    _scope_grant(owner, org, memberships["analyst"], probe)
    _scope_grant(owner, org, memberships["executive"], probe)  # no membership: the control
    tier = add_team(probe, "T")
    _calls(probe, tier, _agents(4, "pt"), 10, received_at=weeks[0] + DAY)
    _calls(probe, None, [add_user("pn1")], 12, received_at=weeks[0] + DAY)
    membership_id = _install_member(probe, analyst.user_id)
    _set_record_scope(probe, RecordScope.TEAM)
    _assign(probe, tier, analyst.user_id, "supervisor", by=owner.user_id)
    one_week = _week_window(weeks[0], 1)

    def probe_block(persona, *, drill: bool = False) -> tuple[str, str | None]:
        if drill:
            return _released(persona.get(_drill(org, probe), params=one_week).json()["workspace"])
        answer = persona.get(executive_path, params=one_week).json()["workspaces"]
        return _released(next(w for w in answer if w["workspace_id"] == probe.workspace_id))

    withheld = ("withheld", "complementary_disclosure")
    assert probe_block(analyst) == withheld
    assert probe_block(analyst, drill=True) == withheld
    assert probe_block(executive) == ("released", None)  # no workspace membership: unchanged
    _set_membership_role(membership_id, WorkspaceRole.ADMIN)  # unrestricted: unchanged
    assert probe_block(analyst) == ("released", None)
    _set_membership_role(membership_id, WorkspaceRole.MEMBER)
    _set_record_scope(probe, RecordScope.WORKSPACE)  # unrestricted: unchanged
    assert probe_block(analyst) == ("released", None)
    _set_record_scope(probe, RecordScope.TEAM)
    assert probe_block(analyst) == withheld
    _calls(probe, None, _agents(4, "pn"), 1, received_at=weeks[0] + DAY)  # n2 to n5
    assert probe_block(analyst) == ("released", None)  # the rest has 5 agents
    assert probe_block(analyst, drill=True) == ("released", None)

    # Cost: the executive view grows by the same number of statements per workspace, and
    # the drill-down does not grow with the teams
    shapes = []
    for name, team_count in (("Ops 1", 1), ("Ops 3", 3), ("Ops 5", 1)):
        place = _org_workspace(owner, org, name)
        for n in range(team_count):
            seed(place, team_id=add_team(place, f"{name}-{n}"), received_at=weeks[0] + DAY)
        shapes.append(place)
    window = _week_window(weeks[0], 1)
    counts = []
    for place in shapes:
        _scope_grant(owner, org, memberships["executive"], place)
        counts.append(_statements(lambda: executive.get(executive_path, params=window))[0])
    # per released workspace: P and G, the caller's membership (A6), and 9 coverage statements
    assert counts[1] - counts[0] == counts[2] - counts[1] == 12, counts
    one_team = _statements(lambda: owner.get(_drill(org, shapes[0]), params=window))
    three_teams = _statements(lambda: owner.get(_drill(org, shapes[1]), params=window))
    # 3 (session, user, organization membership) + 1 (the workspace) + 1 (its teams)
    # + 2 (P and G) + 1 (the caller's workspace membership, A6; the owner's is unrestricted)
    # + 9 (one grouped coverage run), for 1 or 3 teams
    assert one_team[0] == three_teams[0] == 17, (one_team[0], three_teams[0])
    assert len(three_teams[1]["teams"]) == 3


@pytest.mark.postgres
def test_org_views_never_read_outside_grants(client, email_outbox):
    owner = _persona(client, email_outbox, email="or-owner@example.com", name="Owner")
    analyst = _persona(client, email_outbox, email="or-analyst@example.com", name="Analyst")
    org = owner.organization_id
    granted = _org_workspace(owner, org, "Granted")
    other = _org_workspace(owner, org, "Other")
    membership = _grant(owner, org, analyst, "analyst")
    _scope_grant(owner, org, membership, granted)
    week_start = current_week_start(datetime.now(UTC))
    closed = week_start - WEEK
    seed(granted, received_at=closed + DAY)
    for _ in range(3):
        seed(other, received_at=closed + DAY)
    window = _week_window(closed)

    answer = analyst.get(f"/api/v1/organizations/{org}/trends/issues", params=window).json()
    assert [w["workspace_id"] for w in answer["workspaces"]] == [granted.workspace_id]
    assert included(answer["workspaces"][0]) == 1
    assert other.workspace_id not in str(answer)
    refused = analyst.get(_drill(org, other), params=window)
    assert _answer(refused) == (404, "workspace_not_found", None)

    # Row-level security, not only the app filter: with the granted workspace bound as the
    # tenant, the loader asked for the other workspace reads nothing
    filters = validate_filters(window, trend_definition(), now=datetime.now(UTC))
    factory = get_session_factory()
    with factory() as db:
        bind_user_context(db, analyst.user_id)
        bind_organization_context(db, org)
        bind_tenant_context(db, workspace_id=granted.workspace_id, organization_id=org)
        assert (
            judge_population(db, workspace_id=other.workspace_id, scope=true(), filters=filters)
            == []
        )
        assert (
            len(
                judge_population(
                    db, workspace_id=granted.workspace_id, scope=true(), filters=filters
                )
            )
            == 1
        )
        # allow-control: bound to the other workspace, its rows are there
        bind_tenant_context(db, workspace_id=other.workspace_id, organization_id=org)
        assert (
            len(
                judge_population(db, workspace_id=other.workspace_id, scope=true(), filters=filters)
            )
            == 3
        )
        db.rollback()
