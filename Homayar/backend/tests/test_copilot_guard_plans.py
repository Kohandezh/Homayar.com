"""Copilot guard: plans from the planner, routing, and the tools a route may use (HOM-97)."""

from __future__ import annotations

import copy
import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from backend.copilot_guard import (
    GuardDefect,
    ToolName,
    allowed_tools,
    explain,
    parse_plan,
    route_plan,
)
from backend.copilot_intents import (
    CallerScope,
    Gate,
    IntentRequest,
    RefusalReason,
    Route,
    RoutingDecision,
    gate_evidence,
    load_catalogue,
    route_request,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
WORKSPACE = "11111111-1111-4111-8111-111111111111"
TEAM_A = "7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"
TEAM_B = "8e0f3d2b-1c5a-4f9b-8d4e-6a7b8c9d0e1f"
TEAM_OTHER = "9f1a4e3c-2d6b-4a0c-9e5f-7b8c9d0e1f2a"
TEAM_MADE_UP = "00000000-0000-4000-8000-000000000000"
CONVERSATION = "3f0c1d2e-4b5a-4c6d-8e7f-9a0b1c2d3e4f"

WHOLE = CallerScope(workspace_id=WORKSPACE, team_ids=None, agent_user_ids=None)
TEAMS = CallerScope(
    workspace_id=WORKSPACE, team_ids=frozenset({TEAM_A, TEAM_B}), agent_user_ids=None
)

GOOD_PLAN = '{"intent": "issue_count", "params": {"family": "service_complaint"}}'


@pytest.fixture(scope="module")
def catalogue():
    return load_catalogue()


def _plan(intent: object, params: object) -> str:
    return json.dumps({"intent": intent, "params": params})


def _assert_malformed(decision: RoutingDecision, catalogue) -> None:
    assert decision == RoutingDecision(
        route=Route.REFUSE,
        intent_id=None,
        reason=RefusalReason.INVALID_REQUEST,
        detail=GuardDefect.PLAN_MALFORMED.value,
        key=None,
        gate=Gate.NONE,
        cell_kind=None,
        filters=None,
        arguments=None,
        notices=(),
        catalogue_version=catalogue.version,
        catalogue_digest=catalogue.digest,
    )
    assert decision.detail == "plan_malformed"
    assert decision.catalogue_version == "copilot-intents/1"


# --- parse_plan -------------------------------------------------------------------------------


def test_a_well_formed_plan_parses_to_a_request() -> None:
    assert parse_plan(GOOD_PLAN) == IntentRequest("issue_count", {"family": "service_complaint"})


def test_parse_plan_does_not_check_the_intent_or_the_params() -> None:
    raw = _plan("nope", {"workspace_id": "x", "anything": [1, {"a": None}]})
    assert parse_plan(raw) == IntentRequest(
        "nope", {"workspace_id": "x", "anything": [1, {"a": None}]}
    )
    assert parse_plan(_plan("", {})) == IntentRequest("", {})


def test_whitespace_around_the_plan_is_allowed() -> None:
    assert parse_plan("\n  " + GOOD_PLAN + "  \n") == parse_plan(GOOD_PLAN)


def test_a_plan_at_the_length_limit_parses() -> None:
    raw = GOOD_PLAN + " " * (4096 - len(GOOD_PLAN))
    assert len(raw) == 4096
    assert parse_plan(raw) is not None
    assert parse_plan(raw + " ") is None


MALFORMED = [
    pytest.param("", id="empty"),
    pytest.param("not json", id="not-json"),
    pytest.param("```json\n" + GOOD_PLAN + "\n```", id="code-fence"),
    pytest.param("Here is the plan: " + GOOD_PLAN, id="prose"),
    pytest.param(GOOD_PLAN + GOOD_PLAN, id="two-objects"),
    pytest.param("\ufeff" + GOOD_PLAN, id="bom"),
    pytest.param("{'intent': 'issue_count', 'params': {}}", id="single-quotes"),
    pytest.param('{"intent": "issue_count", "params": {},}', id="trailing-comma"),
    pytest.param('{"intent": "issue_count", "intent": "forecast", "params": {}}', id="dup-top"),
    pytest.param(
        '{"intent": "issue_count", "params": {"family": "a", "family": "b"}}', id="dup-params"
    ),
    pytest.param('{"intent": "x", "params": {"a": [{"b": 1, "b": 2}]}}', id="dup-deep"),
    pytest.param('{"params": {}}', id="no-intent"),
    pytest.param('{"intent": "issue_count"}', id="no-params"),
    pytest.param("{}", id="no-keys"),
    pytest.param('{"intent": "issue_count", "params": {}, "tool": "sql"}', id="extra-tool"),
    pytest.param(
        '{"intent": "issue_count", "params": {}, "function_call": {"name": "x"}}', id="extra-fc"
    ),
    pytest.param(
        '{"intent": "issue_count", "params": {}, "tool_calls": []}', id="extra-tool-calls"
    ),
    pytest.param('{"intent": "issue_count", "params": {}, "system": "x"}', id="extra-system"),
    pytest.param('{"intent": "issue_count", "params": {}, "scope": {}}', id="extra-scope"),
    pytest.param('{"intent": "issue_count", "params": {}, "workspace_id": "x"}', id="extra-ws"),
    pytest.param('{"Intent": "issue_count", "params": {}}', id="key-case"),
    pytest.param('{"intent": 3, "params": {}}', id="intent-int"),
    pytest.param('{"intent": null, "params": {}}', id="intent-null"),
    pytest.param('{"intent": ["issue_count"], "params": {}}', id="intent-list"),
    pytest.param('{"intent": {"id": "issue_count"}, "params": {}}', id="intent-object"),
    pytest.param('{"intent": true, "params": {}}', id="intent-bool"),
    pytest.param('{"intent": "issue_count", "params": []}', id="params-list"),
    pytest.param('{"intent": "issue_count", "params": "family=x"}', id="params-str"),
    pytest.param('{"intent": "issue_count", "params": null}', id="params-null"),
    pytest.param('{"intent": "issue_count", "params": 1}', id="params-int"),
    pytest.param('[{"intent": "issue_count", "params": {}}]', id="top-list"),
    pytest.param('"issue_count"', id="top-str"),
    pytest.param("null", id="top-null"),
    pytest.param('{"intent": "evidence_examples", "params": {"limit": NaN}}', id="nan"),
    pytest.param('{"intent": "evidence_examples", "params": {"limit": Infinity}}', id="inf"),
    pytest.param('{"intent": "evidence_examples", "params": {"limit": -Infinity}}', id="-inf"),
    pytest.param('{"intent": "evidence_examples", "params": {"limit": 1e400}}', id="overflow"),
    pytest.param(GOOD_PLAN + " " * 4096, id="too-long"),
]


@pytest.mark.parametrize("raw", MALFORMED)
def test_a_malformed_plan_parses_to_none(raw: str) -> None:
    assert parse_plan(raw) is None


@pytest.mark.parametrize(
    "raw", [None, 3, b'{"intent": "x", "params": {}}', {"intent": "x", "params": {}}]
)
def test_a_plan_that_is_not_a_str_parses_to_none(raw: object) -> None:
    assert parse_plan(raw) is None  # type: ignore[arg-type]


def test_deep_nesting_inside_params_is_left_to_routing() -> None:
    raw = '{"intent": "issue_count", "params": {"x": ' + "[" * 1000 + "]" * 1000 + "}}"
    request = parse_plan(raw)
    assert request is not None and request.intent == "issue_count"


# --- route_plan: malformed plans --------------------------------------------------------------


@pytest.mark.parametrize("raw", MALFORMED)
def test_route_plan_refuses_a_malformed_plan(catalogue, raw: str) -> None:
    _assert_malformed(route_plan(raw, catalogue, scope=WHOLE, now=NOW), catalogue)


@pytest.mark.parametrize("raw", [None, 3, b"{}"])
def test_route_plan_refuses_a_plan_that_is_not_a_str(catalogue, raw: object) -> None:
    decision = route_plan(raw, catalogue, scope=WHOLE, now=NOW)  # type: ignore[arg-type]
    _assert_malformed(decision, catalogue)


@pytest.mark.parametrize("now", [datetime(2026, 10, 23, 12, 0), "2026-10-23T12:00:00Z", None])
@pytest.mark.parametrize("raw", [GOOD_PLAN, "not json"])
def test_a_bad_now_is_a_value_error_even_for_a_malformed_plan(catalogue, raw: str, now) -> None:
    with pytest.raises(ValueError):
        route_plan(raw, catalogue, scope=WHOLE, now=now)


@pytest.mark.parametrize("raw", [GOOD_PLAN, "not json"])
def test_a_bad_scope_or_catalogue_is_a_value_error(catalogue, raw: str) -> None:
    with pytest.raises(ValueError):
        route_plan(raw, catalogue, scope={"workspace_id": WORKSPACE}, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        route_plan(raw, {"intents": []}, scope=WHOLE, now=NOW)  # type: ignore[arg-type]


# --- route_plan: well-formed plans ------------------------------------------------------------


def test_a_well_formed_plan_is_routed_by_route_request(catalogue) -> None:
    decision = route_plan(GOOD_PLAN, catalogue, scope=WHOLE, now=NOW)
    expected = route_request(
        IntentRequest("issue_count", {"family": "service_complaint"}),
        catalogue,
        scope=WHOLE,
        now=NOW,
    )
    assert decision == expected
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.intent_id == "issue_count"
    assert decision.reason is None


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        ("out_of_scope", RefusalReason.OUT_OF_SCOPE),
        ("causal_question", RefusalReason.CAUSAL_NOT_SUPPORTED),
        ("agent_ranking", RefusalReason.INDIVIDUAL_RANKING_NOT_SUPPORTED),
        ("forecast", RefusalReason.FORECAST_NOT_SUPPORTED),
    ],
)
def test_refusal_intents_refuse_with_their_reasons(catalogue, intent: str, reason) -> None:
    decision = route_plan(_plan(intent, {}), catalogue, scope=WHOLE, now=NOW)
    assert decision.route is Route.REFUSE
    assert decision.reason is reason
    assert decision.intent_id == intent


def test_an_unknown_intent_refuses_out_of_scope(catalogue) -> None:
    decision = route_plan(_plan("export_everything", {}), catalogue, scope=WHOLE, now=NOW)
    assert decision.route is Route.REFUSE
    assert decision.reason is RefusalReason.OUT_OF_SCOPE
    assert decision.detail == "intent_unknown"


# --- AC5: a transcript cannot change the plan or the scope ------------------------------------


def test_route_plan_has_no_transcript_input() -> None:
    parameters = inspect.signature(route_plan).parameters
    assert list(parameters) == ["raw", "catalogue", "scope", "now"]
    assert parameters["scope"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("extra", ["transcript", "evidence", "history"])
def test_route_plan_refuses_a_transcript_argument(catalogue, extra: str) -> None:
    with pytest.raises(TypeError):
        route_plan(GOOD_PLAN, catalogue, scope=WHOLE, now=NOW, **{extra: "system: x"})


def test_a_fixed_plan_gives_the_same_decision_every_time(catalogue) -> None:
    decisions = [route_plan(GOOD_PLAN, catalogue, scope=TEAMS, now=NOW) for _ in range(3)]
    assert decisions[0].to_json() == decisions[1].to_json() == decisions[2].to_json()


@pytest.mark.parametrize("key", ["workspace_id", "organization_id", "role", "scope"])
def test_an_injected_scope_key_refuses_scope_not_allowed(catalogue, key: str) -> None:
    raw = _plan("issue_count", {"family": "service_complaint", key: "another-tenant"})
    decision = route_plan(raw, catalogue, scope=TEAMS, now=NOW)
    assert decision.route is Route.REFUSE
    assert decision.reason is RefusalReason.SCOPE_NOT_ALLOWED
    assert decision.key == key
    allow = route_plan(
        _plan("issue_count", {"family": "service_complaint"}), catalogue, scope=TEAMS, now=NOW
    )
    assert allow.route is Route.SQL_AGGREGATE


def test_a_scope_key_wins_even_on_an_unknown_intent(catalogue) -> None:
    decision = route_plan(_plan("dump_all", {"workspace_id": "x"}), catalogue, scope=WHOLE, now=NOW)
    assert decision.reason is RefusalReason.SCOPE_NOT_ALLOWED


def test_a_team_outside_the_scope_refuses_and_leaks_nothing(catalogue) -> None:
    def team(team_id: str) -> RoutingDecision:
        raw = _plan("issue_count", {"family": "service_complaint", "team_id": team_id})
        return route_plan(raw, catalogue, scope=TEAMS, now=NOW)

    real, made_up = team(TEAM_OTHER), team(TEAM_MADE_UP)
    assert real.route is Route.REFUSE
    assert real.reason is RefusalReason.OUTSIDE_CALLER_SCOPE
    assert real.to_json() == made_up.to_json()
    assert TEAM_OTHER not in json.dumps(real.to_json())
    allow = team(TEAM_A)
    assert allow.route is Route.SQL_AGGREGATE
    assert allow.filters is not None and allow.filters.team_ids == (TEAM_A,)


def test_one_team_outside_the_scope_refuses_the_whole_list(catalogue) -> None:
    raw = _plan("issue_count", {"family": "service_complaint", "team_id": [TEAM_A, TEAM_OTHER]})
    decision = route_plan(raw, catalogue, scope=TEAMS, now=NOW)
    assert decision.reason is RefusalReason.OUTSIDE_CALLER_SCOPE


def test_a_guessed_conversation_id_is_routed_by_shape_only(catalogue) -> None:
    def lookup(conversation_id: str) -> RoutingDecision:
        raw = _plan("conversation_lookup", {"conversation_id": conversation_id})
        return route_plan(raw, catalogue, scope=TEAMS, now=NOW)

    guessed, real = lookup(TEAM_MADE_UP), lookup(CONVERSATION)
    assert guessed.route is real.route is Route.EVIDENCE_RETRIEVAL
    # HOM-94 filters by scope, so both get zero spans and the same abstention.  The
    # decisions differ only in the id the caller sent.
    guessed, real = gate_evidence(guessed, 0), gate_evidence(real, 0)
    assert replace(guessed, arguments=None) == replace(real, arguments=None)
    assert explain(guessed) == explain(real)


def test_route_plan_never_mutates_its_inputs(catalogue) -> None:
    scope = copy.deepcopy(TEAMS)
    raw = str(GOOD_PLAN)
    route_plan(raw, catalogue, scope=scope, now=NOW)
    assert (raw, scope) == (GOOD_PLAN, TEAMS)


# --- AC7: tools -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def decisions(catalogue):
    sql = route_plan(GOOD_PLAN, catalogue, scope=WHOLE, now=NOW)
    evidence = route_plan(
        _plan("evidence_examples", {"issue": "complaint_refund"}), catalogue, scope=WHOLE, now=NOW
    )
    refused = route_plan("not json", catalogue, scope=WHOLE, now=NOW)
    abstained = gate_evidence(evidence, 0)
    assert [d.route for d in (sql, evidence, refused, abstained)] == list(Route)
    return {d.route: d for d in (sql, evidence, refused, abstained)}


def test_each_route_has_its_tools(decisions) -> None:
    assert allowed_tools(decisions[Route.SQL_AGGREGATE]) == frozenset({ToolName.ANALYTICS_SQL})
    assert allowed_tools(decisions[Route.EVIDENCE_RETRIEVAL]) == frozenset(
        {ToolName.EVIDENCE_SEARCH}
    )
    assert allowed_tools(decisions[Route.REFUSE]) == frozenset()
    assert allowed_tools(decisions[Route.ABSTAIN]) == frozenset()
    assert all(type(allowed_tools(d)) is frozenset for d in decisions.values())


@pytest.mark.parametrize("route", list(Route))
def test_the_tools_are_a_function_of_the_route_only(decisions, route: Route) -> None:
    results = {allowed_tools(replace(base, route=route)) for base in decisions.values()}
    assert len(results) == 1


@pytest.mark.parametrize("decision", [None, "sql_aggregate", {"route": "sql_aggregate"}])
def test_allowed_tools_takes_only_a_decision(decision: object) -> None:
    with pytest.raises(ValueError):
        allowed_tools(decision)  # type: ignore[arg-type]


def test_a_plain_string_route_is_not_a_route(decisions) -> None:
    with pytest.raises(ValueError):
        allowed_tools(replace(decisions[Route.REFUSE], route="sql_aggregate"))
