"""HOM-84 (WP15-T06, F61): who may call each trend route, and what an allowed answer counts.

One table over the reference dataset (``_trend_reference``).  Callers:

    owner          workspace A owner, also the organization owner
    admin          workspace A admin
    supervisor     workspace A member, supervisor of alpha-big (record scope ``team``)
    analyst        workspace A viewer with no team assignment (the workspace analyst role),
                   with one upload of its own (no team, no agent)
    agent          workspace A member, agent of alpha-big (the agent b1); also an organization
                   analyst with a scope grant on all of A (a restricted workspace membership)
    api_key        an API key of the owner in A, scope ``meeting:read``
    api_writer     an API key of the owner in A, scope ``meeting:create`` only
    unknown_key    a bearer that looks like an API key and is no key (``hk_...``)
    org_admin      organization admin, no workspace membership
    org_analyst    organization analyst, scope grant on all of A
    org_executive  organization executive, scope grant on all of A
    org_narrow     organization analyst, team grant on beta-mid
    nobody         no credentials

Every refused case names its control (``pair``): the closest request that differs in one
thing only (the caller, one query key or the workspace) and is not refused by the same rule.
Each refused request is valid in every other way, so the code comes from the intended rule.
The scope of an allowed answer is checked with the hand counts of ``_trend_reference``:
workspace A has 74 conversations in the window, alpha-big 36, the agent b1 8, the analyst's
upload 1 (an upload is not a call as agent, so ``/trends/me`` stays 0), workspace B 47.

Checked against the routers for the 401 cells: no credentials is ``authentication_required``
(``security/context.py`` ``require_actor``, ``_unauthorized``); a bearer that starts with
``hk_`` is ``api_key_not_accepted`` before any key lookup on a session-only route
(``require_actor``, ``looks_like_api_key``); an inactive team is not comparable, so naming it
on ``/trends/teams`` is ``team_scope_denied`` (``role_trend_service.compared_teams``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.tests._trend_reference import Reference, build_reference
from backend.tests.test_api_keys import _bearer

ISSUES = "/api/v1/trends/issues"
TEAMS = "/api/v1/trends/teams"
ME = "/api/v1/trends/me"
LINKS = "/api/v1/trends/conversations"
VOICEMAILS = "/api/v1/voicemails/analytics"
COVERAGE = "/api/v1/admin/coverage"
ROW = "issue:intent_report_dissatisfaction"
UNKNOWN_KEY = "hk_00000000.nope"


def _included(count: int) -> Callable[[dict[str, Any]], bool]:
    return lambda body: body["population"]["included"] == count


def _teams(*names: str) -> Callable[[dict[str, Any]], bool]:
    return lambda body: [t["team"]["name"] for t in body["teams"]] == list(names)


def _total(count: int) -> Callable[[dict[str, Any]], bool]:
    return lambda body: body["page"]["total"] == count


def _voicemails(count: int) -> Callable[[dict[str, Any]], bool]:
    return lambda body: body["total"] == count


def _send(ref: Reference, caller: str, path: str, workspace: str | None, params: dict[str, Any]):
    client = ref.client
    if caller == "nobody":
        client.cookies.clear()
        return client.get(path, params=params)
    if caller in ("api_key", "api_writer", "unknown_key"):
        client.cookies.clear()
        key = {"api_key": ref.reader_key, "api_writer": ref.writer_key}.get(caller, UNKNOWN_KEY)
        extra = {"X-Workspace-ID": workspace} if workspace else {}
        return client.get(path, params=params, headers=_bearer(key, **extra))
    return ref.people[caller].get(path, workspace_id=workspace, params=params)


def _answer(response) -> tuple[int, str | None]:
    if response.status_code == 200:
        return 200, None
    return response.status_code, response.json()["error"]["code"]


def matrix(ref: Reference) -> list[dict[str, Any]]:
    """The cells: ``id``, ``route``, ``caller``, ``workspace`` (the ``X-Workspace-ID``),
    ``params``, the expected ``status`` and ``code``, ``pair`` (refused cells) and ``check``
    (the scope of an allowed answer)."""
    a, b, org = ref.a.workspace_id, ref.b.workspace_id, ref.organization_id
    big, mid, old = ref.teams["big"], ref.teams["mid"], ref.teams["old"]
    b1, b2 = ref.agents["big"][0], ref.agents["big"][1]
    window = ref.window()
    received = {"received_from": window["from"], "received_to": window["to"]}
    executive = f"/api/v1/organizations/{org}/trends/issues"
    drill_a = f"/api/v1/organizations/{org}/workspaces/{a}/trends/issues"
    drill_b = f"/api/v1/organizations/{org}/workspaces/{b}/trends/issues"
    aggregates = f"/api/v1/organizations/{org}/aggregates"
    links = {"row": ROW, "limit": "100", **window}

    def cell(
        cell_id: str,
        route: str,
        caller: str,
        status: int = 200,
        code: str | None = None,
        *,
        workspace: str | None = a,
        params: dict[str, Any] | None = None,
        pair: str | None = None,
        check: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": cell_id,
            "route": route,
            "caller": caller,
            "workspace": workspace,
            "params": window if params is None else params,
            "status": status,
            "code": code,
            "pair": pair,
            "check": check,
        }

    def org_cell(cell_id: str, route: str, caller: str, *rest: Any, **options: Any):
        return cell(cell_id, route, caller, *rest, workspace=None, **options)

    return [
        # 1. GET /trends/issues: session or API key, meeting:read, the caller's record scope
        cell("I1", ISSUES, "owner", check=_included(74)),
        cell("I2", ISSUES, "admin", check=_included(74)),
        cell("I3", ISSUES, "supervisor", check=_included(36)),  # alpha-big only
        cell("I4", ISSUES, "analyst", check=_included(1)),  # its own upload only
        cell("I5", ISSUES, "agent", params={**window, "agent_user_id": b1}, check=_included(8)),
        cell("I6", ISSUES, "agent", 403, "agent_scope_denied",
             params={**window, "agent_user_id": b2}, pair="I5"),
        cell("I7", ISSUES, "supervisor", 403, "agent_scope_denied",
             params={**window, "agent_user_id": b1}, pair="I3"),
        cell("I8", ISSUES, "api_key", check=_included(74)),
        cell("I9", ISSUES, "api_writer", 403, "api_key_scope_denied", pair="I8"),
        cell("I10", ISSUES, "api_key", 403, "workspace_access_denied", workspace=b, pair="I8"),
        cell("I11", ISSUES, "org_analyst", 403, "workspace_access_denied", pair="I1"),
        cell("I12", ISSUES, "nobody", 401, "authentication_required", pair="I1"),
        cell("I13", ISSUES, "supervisor", params={**window, "team_id": big}, check=_included(36)),
        # a team outside the scope narrows (ADR 0038 decision 9): 200 with nothing counted
        cell("I14", ISSUES, "supervisor", params={**window, "team_id": mid}, check=_included(0)),
        # 2. GET /trends/teams: session only; owners and admins every team, others supervised
        cell("T1", TEAMS, "owner", check=_teams("alpha-big", "beta-mid", "gamma-small")),
        cell("T2", TEAMS, "admin", check=_teams("alpha-big", "beta-mid", "gamma-small")),
        cell("T3", TEAMS, "supervisor", check=_teams("alpha-big")),
        cell("T4", TEAMS, "supervisor", params={**window, "team_id": big},
             check=_teams("alpha-big")),
        cell("T5", TEAMS, "supervisor", 403, "team_scope_denied",
             params={**window, "team_id": mid}, pair="T4"),
        cell("T6", TEAMS, "owner", params={**window, "team_id": mid}, check=_teams("beta-mid")),
        cell("T7", TEAMS, "analyst", 403, "team_scope_denied", pair="T3"),
        cell("T8", TEAMS, "agent", 403, "team_scope_denied", pair="T3"),
        cell("T9", TEAMS, "api_key", 401, "api_key_not_accepted", pair="T1"),
        cell("T10", TEAMS, "unknown_key", 401, "api_key_not_accepted", pair="T1"),
        cell("T11", TEAMS, "nobody", 401, "authentication_required", pair="T1"),
        cell("T12", TEAMS, "owner", 403, "team_scope_denied",
             params={**window, "team_id": old}, pair="T6"),  # zeta-old is inactive
        # 3. GET /trends/me: session only, every role, the caller's own calls as agent
        cell("M1", ME, "agent", check=_included(8)),
        cell("M2", ME, "owner", check=_included(0)),
        cell("M3", ME, "admin", check=_included(0)),
        cell("M4", ME, "supervisor", check=_included(0)),
        cell("M5", ME, "analyst", check=_included(0)),  # its upload is not a call as agent
        cell("M6", ME, "agent", 422, "trend_parameter_not_allowed",
             params={**window, "agent_user_id": b2}, pair="M1"),
        cell("M7", ME, "api_key", 401, "api_key_not_accepted", pair="M2"),
        cell("M8", ME, "unknown_key", 401, "api_key_not_accepted", pair="M2"),
        cell("M9", ME, "nobody", 401, "authentication_required", pair="M2"),
        # 4. GET /trends/conversations: session only, the view's own scope rules, then the cell
        cell("L1", LINKS, "owner", params={**links, "trend_view": "issues"}, check=_total(38)),
        cell("L2", LINKS, "admin", params={**links, "trend_view": "issues"}, check=_total(38)),
        cell("L3", LINKS, "supervisor", params={**links, "trend_view": "issues"},
             check=_total(25)),
        cell("L4", LINKS, "supervisor", 403, "agent_scope_denied",
             params={**links, "trend_view": "issues", "agent_user_id": b1}, pair="L3"),
        cell("L5", LINKS, "supervisor", params={**links, "trend_view": "teams", "team_id": big},
             check=_total(25)),
        cell("L6", LINKS, "supervisor", 403, "team_scope_denied",
             params={**links, "trend_view": "teams", "team_id": mid}, pair="L5"),
        # the agent passes the scope rule with its own id; its own cell is under 30 (L7)
        cell("L7", LINKS, "agent", 403, "trend_links_withheld",
             params={**links, "trend_view": "issues", "agent_user_id": b1}),
        cell("L8", LINKS, "agent", 403, "agent_scope_denied",
             params={**links, "trend_view": "issues", "agent_user_id": b2}, pair="L7"),
        cell("L9", LINKS, "analyst", 403, "trend_links_withheld",
             params={**links, "trend_view": "issues"}),  # its 1 call: insufficient
        cell("L10", LINKS, "api_key", 401, "api_key_not_accepted",
             params={**links, "trend_view": "issues"}, pair="L1"),
        cell("L11", LINKS, "unknown_key", 401, "api_key_not_accepted",
             params={**links, "trend_view": "issues"}, pair="L1"),
        cell("L12", LINKS, "nobody", 401, "authentication_required",
             params={**links, "trend_view": "issues"}, pair="L1"),
        # 5. GET /organizations/{id}/trends/issues: organization:analytics:read, session only
        org_cell("O1", executive, "owner",
                 check=lambda body: [w["workspace_id"] for w in body["workspaces"]] == [a, b]),
        org_cell("O2", executive, "org_admin",
                 check=lambda body: [w["workspace_id"] for w in body["workspaces"]] == [a, b]),
        org_cell("O3", executive, "org_analyst",
                 check=lambda body: [w["workspace_id"] for w in body["workspaces"]] == [a]),
        org_cell("O4", executive, "org_executive",
                 check=lambda body: [w["workspace_id"] for w in body["workspaces"]] == [a]),
        org_cell("O5", executive, "org_narrow",
                 check=lambda body: [w["team_ids"] for w in body["workspaces"]] == [[mid]]),
        org_cell("O6", executive, "supervisor", 404, "organization_not_found", pair="O3"),
        org_cell("O7", executive, "api_key", 401, "api_key_not_accepted", pair="O1"),
        org_cell("O8", executive, "nobody", 401, "authentication_required", pair="O1"),
        org_cell("O9", executive, "agent",  # organization analyst with a restricted membership
                 check=lambda body: [w["workspace_id"] for w in body["workspaces"]] == [a]),
        # 6. the drill-down: organization:records:read (not executives), the grant's workspaces
        org_cell("D1", drill_a, "owner", check=lambda body: len(body["teams"]) == 3),
        org_cell("D2", drill_a, "org_admin", check=lambda body: len(body["teams"]) == 3),
        org_cell("D3", drill_a, "org_analyst", check=lambda body: len(body["teams"]) == 3),
        org_cell("D4", drill_a, "org_executive", 403, "permission_denied", pair="D3"),
        org_cell("D5", drill_b, "org_analyst", 404, "workspace_not_found", pair="D3"),
        org_cell("D6", drill_b, "owner", check=lambda body: body["workspace_id"] == b),
        org_cell("D7", drill_a, "org_narrow",
                 check=lambda body: [t["team"]["id"] for t in body["teams"]] == [mid]),
        org_cell("D8", drill_a, "supervisor", 404, "organization_not_found", pair="D3"),
        org_cell("D9", drill_a, "api_key", 401, "api_key_not_accepted", pair="D1"),
        org_cell("D10", drill_a, "agent", check=lambda body: len(body["teams"]) == 3),
        # 7. GET /organizations/{id}/aggregates: organization:analytics:read, session only
        org_cell("A1", aggregates, "owner", params={},
                 check=lambda body: [i["workspace_id"] for i in body["items"]] == [a, b]),
        org_cell("A2", aggregates, "org_admin", params={},
                 check=lambda body: [i["workspace_id"] for i in body["items"]] == [a, b]),
        org_cell("A3", aggregates, "org_analyst", params={},
                 check=lambda body: [i["workspace_id"] for i in body["items"]] == [a]),
        org_cell("A4", aggregates, "org_executive", params={},
                 check=lambda body: [i["workspace_id"] for i in body["items"]] == [a]),
        org_cell("A5", aggregates, "org_narrow", params={},
                 check=lambda body: [i["team_ids"] for i in body["items"]] == [[mid]]),
        org_cell("A6", aggregates, "supervisor", 404, "organization_not_found", params={},
                 pair="A3"),
        org_cell("A7", aggregates, "api_key", 401, "api_key_not_accepted", params={},
                 pair="A1"),
        # 8. GET /voicemails/analytics: session or API key, meeting:read, the record scope
        cell("V1", VOICEMAILS, "owner", params=received, check=_voicemails(3)),
        cell("V2", VOICEMAILS, "admin", params=received, check=_voicemails(3)),
        cell("V3", VOICEMAILS, "supervisor", params=received, check=_voicemails(2)),
        cell("V4", VOICEMAILS, "analyst", params=received, check=_voicemails(0)),
        cell("V5", VOICEMAILS, "agent", params=received, check=_voicemails(0)),
        cell("V6", VOICEMAILS, "api_key", params=received, check=_voicemails(3)),
        cell("V7", VOICEMAILS, "api_writer", 403, "api_key_scope_denied", params=received,
             pair="V6"),
        cell("V8", VOICEMAILS, "org_analyst", 403, "workspace_access_denied", params=received,
             pair="V1"),
        # 9. GET /admin/coverage: workspace:manage (owner and admin), no API key scope for it
        cell("C1", COVERAGE, "owner",
             check=lambda body: body["counters"]["ingestion"]["denominator"] == 79),
        cell("C2", COVERAGE, "admin",
             check=lambda body: body["counters"]["ingestion"]["denominator"] == 79),
        cell("C3", COVERAGE, "supervisor", 403, "permission_denied", pair="C2"),
        cell("C4", COVERAGE, "analyst", 403, "permission_denied", pair="C2"),
        cell("C5", COVERAGE, "agent", 403, "permission_denied", pair="C2"),
        cell("C6", COVERAGE, "api_key", 403, "api_key_scope_denied", pair="C1"),
    ]  # fmt: skip


def test_every_trend_route_allows_and_denies_by_the_matrix(client, email_outbox):
    ref = build_reference(client, email_outbox)
    cells = matrix(ref)
    by_id = {c["id"]: c for c in cells}
    assert len(by_id) == len(cells)

    for item in cells:
        response = _send(ref, item["caller"], item["route"], item["workspace"], item["params"])
        assert _answer(response) == (item["status"], item["code"]), (item["id"], response.text)
        if item["status"] == 200:
            assert item["check"] is not None and item["check"](response.json()), item["id"]

    # every deny names a control that differs in one thing and is not refused by the same rule
    for item in cells:
        if item["status"] == 200 or item["pair"] is None:
            continue
        control = by_id[item["pair"]]
        assert control["code"] != item["code"], item["id"]
        differences = [key for key in ("caller", "route", "workspace") if control[key] != item[key]]
        changed = set(control["params"].items()) ^ set(item["params"].items())
        assert len(differences) + len({key for key, _ in changed}) == 1, (item["id"], changed)
    unpaired = {c["id"] for c in cells if c["status"] != 200 and c["pair"] is None}
    assert unpaired == {"L7", "L9"}  # cell refusals: they are the controls, not a scope rule


def test_allowed_answers_count_only_the_callers_scope(client, email_outbox):
    """F61: a supervisor's answer counts only its team, an agent's self-view only its own calls,
    and no answer counts a meeting of another workspace."""
    ref = build_reference(client, email_outbox)
    window = ref.window()
    big = ref.meetings(team="big", week={0, 1, 2}, extraction={"C", "V"})

    supervised = _send(
        ref,
        "supervisor",
        LINKS,
        ref.a.workspace_id,
        {**window, "trend_view": "issues", "row": ROW, "limit": "100"},
    ).json()
    assert {c["meeting_id"] for c in supervised["conversations"]} == big

    own = _send(ref, "agent", ME, ref.a.workspace_id, window).json()
    b1 = ref.agents["big"][0]
    assert own["subject"] == {"agent_user_id": b1}
    # b1's own calls: 7 with an extraction + the no_run gap (in the population, missing)
    assert own["population"]["included"] == 8
    assert {r["cell"]["denominator"] for r in own["rows"] if r["family"] == "call_reason"} == {7}

    upload = ref.meetings(created_by=ref.people["analyst"].user_id)
    assert len(upload) == 1
    analyst = _send(ref, "analyst", LINKS, ref.a.workspace_id,
                    {**window, "trend_view": "issues", "row": ROW, "limit": "100"})  # fmt: skip
    assert analyst.json()["error"]["code"] == "trend_links_withheld"  # 1 call: under 30
    seen = _send(ref, "analyst", ISSUES, ref.a.workspace_id, window).json()
    assert seen["population"]["included"] == 1

    owner_links = _send(
        ref,
        "owner",
        LINKS,
        ref.a.workspace_id,
        {**window, "trend_view": "issues", "row": ROW, "limit": "100"},
    ).json()
    listed = {c["meeting_id"] for c in owner_links["conversations"]}
    assert not listed & ref.meetings(workspace_id=ref.b.workspace_id)
    assert not listed & ref.meetings(gap="outside")
