"""Copilot intent routing and the two answer gates (HOM-92)."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from backend.copilot_intents import (
    COHORT_KEYS,
    DEFAULT_EVIDENCE_LIMIT,
    DEFAULT_GRAIN,
    MAX_EVIDENCE_LIMIT,
    MAX_QUERY_LENGTH,
    AbstainReason,
    CallerScope,
    Gate,
    Grain,
    IntentArguments,
    IntentRequest,
    RefusalReason,
    RequestDefect,
    Route,
    RoutingDecision,
    gate_aggregate,
    gate_evidence,
    load_catalogue,
    route_request,
)
from backend.issue_trends import (
    ASSOCIATION_NOTICE,
    SAMPLE_POLICY_V1,
    SCOPE_KEYS,
    UNSUPPORTED_KEYS,
    CellKind,
    CellReason,
    CellStatus,
    compare_cells,
    sample_cell,
    unavailable_cell,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
WORKSPACE = "11111111-1111-4111-8111-111111111111"
TEAM_A = "7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"
TEAM_B = "8e0f3d2b-1c5a-4f9b-8d4e-6a7b8c9d0e1f"
TEAM_OTHER = "9f1a4e3c-2d6b-4a0c-9e5f-7b8c9d0e1f2a"
TEAM_MADE_UP = "00000000-0000-4000-8000-000000000000"
AGENT_SELF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
AGENT_OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CONVERSATION = "3f0c1d2e-4b5a-4c6d-8e7f-9a0b1c2d3e4f"

AGGREGATE_INTENTS = ("issue_count", "issue_rate", "issue_trend", "cohort_comparison")
RATE_MINIMUM = SAMPLE_POLICY_V1.min_denominator_rate
COMPARISON_MINIMUM = SAMPLE_POLICY_V1.min_denominator_comparison


@pytest.fixture(scope="module")
def catalogue():
    return load_catalogue()


WHOLE = CallerScope(workspace_id=WORKSPACE, team_ids=None, agent_user_ids=None)
TEAMS = CallerScope(
    workspace_id=WORKSPACE, team_ids=frozenset({TEAM_A, TEAM_B}), agent_user_ids=None
)
SELF = CallerScope(
    workspace_id=WORKSPACE, team_ids=frozenset({TEAM_A}), agent_user_ids=frozenset({AGENT_SELF})
)


def _route(catalogue, intent: str, params: dict | None = None, scope: CallerScope = WHOLE):
    return route_request(IntentRequest(intent, params or {}), catalogue, scope=scope, now=NOW)


def _valid_params(intent: str) -> dict:
    return {
        "issue_count": {"issue": "complaint_refund"},
        "issue_rate": {"family": "service_complaint"},
        "issue_trend": {"issue": "complaint_refund", "grain": "month"},
        "cohort_comparison": {
            "issue": "complaint_refund",
            "compare_by": "language",
            "compare_values": ["fa", "en"],
        },
        "coverage_status": {},
        "evidence_examples": {"issue": "complaint_refund"},
        "conversation_lookup": {"conversation_id": CONVERSATION},
    }[intent]


def _assert_refused(decision: RoutingDecision, reason: RefusalReason) -> None:
    assert decision.route is Route.REFUSE
    assert decision.reason is reason
    assert decision.gate is Gate.NONE
    assert decision.filters is None and decision.arguments is None
    assert decision.cell_kind is None and decision.notices == ()


# --- rule 1: unknown and out-of-scope intents ------------------------------------------------


@pytest.mark.parametrize("intent", ["nope", "", "ISSUE_COUNT", "issue count", "drop table"])
def test_an_unknown_intent_refuses_out_of_scope(catalogue, intent: str) -> None:
    decision = _route(catalogue, intent, {"issue": "complaint_refund"})
    _assert_refused(decision, RefusalReason.OUT_OF_SCOPE)
    assert decision.intent_id is None
    assert decision.detail == RequestDefect.INTENT_UNKNOWN.value


@pytest.mark.parametrize("intent", [None, 3, ("issue_count",)])
def test_an_intent_that_is_not_text_refuses_out_of_scope(catalogue, intent: object) -> None:
    decision = _route(catalogue, intent, {})  # type: ignore[arg-type]
    _assert_refused(decision, RefusalReason.OUT_OF_SCOPE)
    assert decision.intent_id is None


def test_the_out_of_scope_intent_refuses_out_of_scope(catalogue) -> None:
    decision = _route(catalogue, "out_of_scope")
    _assert_refused(decision, RefusalReason.OUT_OF_SCOPE)
    assert decision.intent_id == "out_of_scope"
    assert decision.detail is None


def test_allow_control_a_known_intent_routes(catalogue) -> None:
    decision = _route(catalogue, "issue_count", {"issue": "complaint_refund"})
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.reason is None


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        ("causal_question", RefusalReason.CAUSAL_NOT_SUPPORTED),
        ("agent_ranking", RefusalReason.INDIVIDUAL_RANKING_NOT_SUPPORTED),
        ("forecast", RefusalReason.FORECAST_NOT_SUPPORTED),
    ],
)
def test_the_other_refusal_intents_refuse_with_their_reason(catalogue, intent, reason) -> None:
    _assert_refused(_route(catalogue, intent), reason)
    # Their params are never used, so an unsupported key does not change the reason.
    _assert_refused(_route(catalogue, intent, {"product": "x", "aaa": 1}), reason)


# --- rule 2: scope keys ----------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(SCOPE_KEYS))
def test_a_scope_key_refuses_scope_not_allowed(catalogue, key: str) -> None:
    params = {**_valid_params("issue_rate"), key: WORKSPACE}
    decision = _route(catalogue, "issue_rate", params)
    _assert_refused(decision, RefusalReason.SCOPE_NOT_ALLOWED)
    assert decision.key == key
    assert decision.intent_id == "issue_rate"


def test_allow_control_the_same_request_without_the_scope_key_routes(catalogue) -> None:
    decision = _route(catalogue, "issue_rate", _valid_params("issue_rate"))
    assert decision.route is Route.SQL_AGGREGATE


def test_a_scope_key_wins_over_an_unknown_key(catalogue) -> None:
    params = {"aaa": "x", "workspace_id": WORKSPACE, "issue": "complaint_refund"}
    decision = _route(catalogue, "issue_rate", params)
    _assert_refused(decision, RefusalReason.SCOPE_NOT_ALLOWED)
    assert decision.key == "workspace_id"


@pytest.mark.parametrize(
    "extra",
    [
        {"product": "x"},
        {"team_id": "not-a-uuid"},
        {"from": "yesterday"},
        {"grain": "hour"},
        {"compare_by": "agent_user_id"},
        {"team_id": TEAM_OTHER},
    ],
)
def test_a_scope_key_wins_over_every_other_defect(catalogue, extra: dict) -> None:
    params = {"organization_id": "x", **extra}
    for intent in ("issue_rate", "cohort_comparison", "nope", "out_of_scope", "forecast"):
        decision = _route(catalogue, intent, params, scope=TEAMS)
        _assert_refused(decision, RefusalReason.SCOPE_NOT_ALLOWED)
        assert decision.key == "organization_id"


def test_two_scope_keys_report_the_first_in_sorted_order(catalogue) -> None:
    decision = _route(catalogue, "issue_rate", {"workspace_id": "a", "role": "admin"})
    assert decision.key == "role"


# --- rule 3: unsupported filters -------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(UNSUPPORTED_KEYS))
@pytest.mark.parametrize("intent", [*AGGREGATE_INTENTS, "coverage_status", "evidence_examples"])
def test_an_unsupported_key_refuses_unsupported_filter(catalogue, intent: str, key: str) -> None:
    decision = _route(catalogue, intent, {**_valid_params(intent), key: "x"})
    _assert_refused(decision, RefusalReason.UNSUPPORTED_FILTER)
    assert decision.key == key
    assert decision.detail == "filter_unsupported"


def test_an_unsupported_key_wins_over_an_unknown_key_and_a_bad_param(catalogue) -> None:
    params = {"aaa": "x", "sentiment": "angry", "compare_by": "nope", "issue": "nope"}
    decision = _route(catalogue, "cohort_comparison", params)
    _assert_refused(decision, RefusalReason.UNSUPPORTED_FILTER)
    assert decision.key == "sentiment"


def test_allow_control_a_service_area_topic_routes_normally(catalogue) -> None:
    decision = _route(catalogue, "issue_rate", {"service_area": "refund"})
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.reason is None
    assert decision.filters.service_areas == ("refund",)


# --- rule 4: other filter errors -------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "detail", "key"),
    [
        ({"issue": "complaint_refund", "aaa": "x"}, "filter_unknown_key", "aaa"),
        ({"issue": "complaint_refund", "grain": "week"}, "filter_unknown_key", "grain"),
        ({"issue": 3}, "filter_bad_type", "issue"),
        (
            {"issue": "complaint_refund", "from": ["2026-01-01T00:00:00Z"]},
            "filter_bad_type",
            "from",
        ),
        ({"issue": "complaint_nope"}, "filter_bad_value", "issue"),
        ({"issue": "complaint_refund", "team_id": "TEAM"}, "filter_bad_value", "team_id"),
        ({"issue": "complaint_refund", "language": []}, "filter_bad_value", "language"),
        ({"issue": "complaint_refund", "from": "2026-01-01"}, "filter_window_invalid", "from"),
        (
            {
                "issue": "complaint_refund",
                "from": "2026-10-01T00:00:00Z",
                "to": "2026-09-01T00:00:00Z",
            },
            "filter_window_invalid",
            None,
        ),
        (
            {
                "issue": "complaint_refund",
                "from": "2024-01-01T00:00:00Z",
                "to": "2026-01-01T00:00:00Z",
            },
            "filter_window_invalid",
            None,
        ),
    ],
)
def test_other_filter_errors_refuse_invalid_request(catalogue, params, detail, key) -> None:
    decision = _route(catalogue, "issue_rate", params)
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)
    assert (decision.detail, decision.key) == (detail, key)


@pytest.mark.parametrize("intent", ["issue_rate", "out_of_scope", "nope", "forecast"])
def test_a_naive_now_raises(catalogue, intent: str) -> None:
    with pytest.raises(ValueError, match="timezone"):
        route_request(
            IntentRequest(intent, {"workspace_id": "x"}),
            catalogue,
            scope=WHOLE,
            now=datetime(2026, 10, 23, 12, 0),
        )


def test_now_must_be_a_datetime(catalogue) -> None:
    with pytest.raises(ValueError):
        route_request(IntentRequest("issue_rate", {}), catalogue, scope=WHOLE, now="2026")  # type: ignore[arg-type]


@pytest.mark.parametrize("params", [None, [("issue", "x")], "issue=x", {3: "x"}])
def test_params_that_are_not_a_text_keyed_mapping_refuse_invalid_request(catalogue, params) -> None:
    decision = route_request(IntentRequest("issue_rate", params), catalogue, scope=WHOLE, now=NOW)
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)
    assert decision.detail == RequestDefect.PARAMS_NOT_MAPPING.value


def test_programming_errors_raise(catalogue) -> None:
    request = IntentRequest("issue_rate", {"issue": "complaint_refund"})
    with pytest.raises(ValueError):
        route_request({"intent": "issue_rate"}, catalogue, scope=WHOLE, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        route_request(request, object(), scope=WHOLE, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        route_request(request, catalogue, scope={"workspace_id": WORKSPACE}, now=NOW)  # type: ignore[arg-type]


# --- rule 5: caller scope --------------------------------------------------------------------


def test_a_team_outside_the_scope_refuses_outside_caller_scope(catalogue) -> None:
    decision = _route(
        catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": TEAM_OTHER}, scope=TEAMS
    )
    _assert_refused(decision, RefusalReason.OUTSIDE_CALLER_SCOPE)
    assert decision.key == "team_id"


def test_a_made_up_team_gets_the_same_decision_field_for_field(catalogue) -> None:
    real = _route(
        catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": TEAM_OTHER}, scope=TEAMS
    )
    made_up = _route(
        catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": TEAM_MADE_UP}, scope=TEAMS
    )
    assert made_up == real
    assert repr(made_up) == repr(real)
    assert made_up.to_json() == real.to_json()


def test_allow_control_a_team_inside_the_scope_routes(catalogue) -> None:
    decision = _route(
        catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": TEAM_A}, scope=TEAMS
    )
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.reason is None
    assert decision.filters.team_ids == (TEAM_A,)


def test_one_team_outside_the_scope_refuses_the_whole_list(catalogue) -> None:
    params = {"issue": "complaint_refund", "team_id": [TEAM_A, TEAM_OTHER]}
    _assert_refused(
        _route(catalogue, "issue_rate", params, scope=TEAMS), RefusalReason.OUTSIDE_CALLER_SCOPE
    )


def test_a_whole_workspace_scope_allows_any_well_formed_team(catalogue) -> None:
    for team in (TEAM_A, TEAM_OTHER, TEAM_MADE_UP):
        decision = _route(catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": team})
        assert decision.route is Route.SQL_AGGREGATE


def test_an_empty_team_scope_allows_no_team_filter(catalogue) -> None:
    scope = CallerScope(workspace_id=WORKSPACE, team_ids=frozenset(), agent_user_ids=None)
    _assert_refused(
        _route(
            catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": TEAM_A}, scope=scope
        ),
        RefusalReason.OUTSIDE_CALLER_SCOPE,
    )
    assert _route(catalogue, "issue_rate", {"issue": "complaint_refund"}, scope=scope).route is (
        Route.SQL_AGGREGATE
    )


def test_a_malformed_team_is_invalid_before_the_scope_check(catalogue) -> None:
    decision = _route(
        catalogue, "issue_rate", {"issue": "complaint_refund", "team_id": "x"}, scope=TEAMS
    )
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)


def test_an_agent_outside_the_scope_refuses_outside_caller_scope(catalogue) -> None:
    for agent in (AGENT_OTHER, "cccccccc-cccc-4ccc-8ccc-cccccccccccc"):
        decision = _route(
            catalogue,
            "issue_rate",
            {"issue": "complaint_refund", "agent_user_id": agent},
            scope=SELF,
        )
        _assert_refused(decision, RefusalReason.OUTSIDE_CALLER_SCOPE)
        assert decision.key == "agent_user_id"


def test_allow_control_the_agents_own_rate_routes_as_an_agent_cell(catalogue) -> None:
    decision = _route(
        catalogue,
        "issue_rate",
        {"issue": "complaint_refund", "agent_user_id": AGENT_SELF},
        scope=SELF,
    )
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.cell_kind is CellKind.AGENT


def test_a_team_scoped_caller_without_an_agent_filter_gets_a_rate_cell(catalogue) -> None:
    decision = _route(catalogue, "issue_rate", {"issue": "complaint_refund"}, scope=TEAMS)
    assert decision.cell_kind is CellKind.RATE
    assert _route(catalogue, "issue_rate", {"issue": "complaint_refund"}).cell_kind is (
        CellKind.RATE
    )


AGENTS_ONLY = CallerScope(
    workspace_id=WORKSPACE, team_ids=None, agent_user_ids=frozenset({AGENT_SELF})
)


@pytest.mark.parametrize("scope", [SELF, AGENTS_ONLY])
def test_an_agent_scoped_caller_always_gets_an_agent_cell(catalogue, scope) -> None:
    # With or without the filter: the scope alone already narrows the cohort to agents,
    # so a hidden agent count must stay hidden (issue_trends "Small counts stay visible").
    without = _route(catalogue, "issue_rate", {"issue": "complaint_refund"}, scope=scope)
    with_filter = _route(
        catalogue,
        "issue_rate",
        {"issue": "complaint_refund", "agent_user_id": AGENT_SELF},
        scope=scope,
    )
    assert without.cell_kind is CellKind.AGENT
    assert with_filter.cell_kind is CellKind.AGENT
    for intent in ("issue_count", "issue_trend", "cohort_comparison"):
        assert _route(catalogue, intent, _valid_params(intent), scope=scope).cell_kind is (
            CellKind.AGENT
        )


def test_team_scope_is_checked_for_evidence_too(catalogue) -> None:
    params = {"issue": "complaint_refund", "team_id": TEAM_OTHER}
    _assert_refused(
        _route(catalogue, "evidence_examples", params, scope=TEAMS),
        RefusalReason.OUTSIDE_CALLER_SCOPE,
    )
    params["team_id"] = TEAM_B
    assert _route(catalogue, "evidence_examples", params, scope=TEAMS).route is (
        Route.EVIDENCE_RETRIEVAL
    )


def test_team_cohorts_are_checked_against_the_scope(catalogue) -> None:
    params = {
        "issue": "complaint_refund",
        "compare_by": "team_id",
        "compare_values": [TEAM_A, TEAM_OTHER],
    }
    decision = _route(catalogue, "cohort_comparison", params, scope=TEAMS)
    _assert_refused(decision, RefusalReason.OUTSIDE_CALLER_SCOPE)
    assert decision.key == "compare_values"
    made_up = {**params, "compare_values": [TEAM_A, TEAM_MADE_UP]}
    assert _route(catalogue, "cohort_comparison", made_up, scope=TEAMS) == decision
    allowed = {**params, "compare_values": [TEAM_A, TEAM_B]}
    decision = _route(catalogue, "cohort_comparison", allowed, scope=TEAMS)
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.arguments.compare_values == (TEAM_A, TEAM_B)


def test_a_conversation_id_is_never_checked_here(catalogue) -> None:
    # The package cannot know a conversation's team.  HOM-94 checks it and answers an
    # unseen id like a missing one: no authorized evidence, so gate_evidence abstains.
    for scope in (WHOLE, TEAMS, SELF):
        decision = _route(
            catalogue, "conversation_lookup", {"conversation_id": CONVERSATION}, scope=scope
        )
        assert decision.route is Route.EVIDENCE_RETRIEVAL
        assert gate_evidence(decision, 0).reason is AbstainReason.NO_EVIDENCE


def test_scope_values_must_be_well_typed() -> None:
    with pytest.raises(ValueError):
        CallerScope(workspace_id="", team_ids=None, agent_user_ids=None)
    with pytest.raises(ValueError):
        CallerScope(workspace_id=WORKSPACE, team_ids={TEAM_A}, agent_user_ids=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CallerScope(workspace_id=WORKSPACE, team_ids=None, agent_user_ids=frozenset({3}))  # type: ignore[arg-type]


# --- rule 6: valid requests route ------------------------------------------------------------


@pytest.mark.parametrize("intent", [*AGGREGATE_INTENTS, "coverage_status"])
def test_a_valid_aggregate_request_routes_sql(catalogue, intent: str) -> None:
    decision = _route(catalogue, intent, _valid_params(intent))
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.reason is None and decision.detail is None and decision.key is None
    assert decision.intent_id == intent
    assert decision.catalogue_version == catalogue.version
    assert decision.catalogue_digest == catalogue.digest
    assert decision.filters is not None
    assert isinstance(decision.arguments, IntentArguments)


@pytest.mark.parametrize("intent", ["evidence_examples", "conversation_lookup"])
def test_a_valid_evidence_request_routes_evidence(catalogue, intent: str) -> None:
    decision = _route(catalogue, intent, _valid_params(intent))
    assert decision.route is Route.EVIDENCE_RETRIEVAL
    assert decision.reason is None and decision.detail is None
    assert decision.gate is Gate.EVIDENCE
    assert decision.cell_kind is None


def test_refusals_carry_the_catalogue_pins(catalogue) -> None:
    decision = _route(catalogue, "nope")
    assert decision.catalogue_version == catalogue.version
    assert decision.catalogue_digest == catalogue.digest


def test_the_filters_are_the_normalized_issue_trends_filters(catalogue) -> None:
    decision = _route(
        catalogue,
        "issue_rate",
        {"issue": ["complaint_refund", "complaint_invoice", "complaint_refund"], "language": "FA"},
    )
    assert decision.filters.issues == ("complaint_invoice", "complaint_refund")
    assert decision.filters.languages == ("fa",)
    assert decision.filters.window.end == NOW
    assert decision.filters.window.start == NOW - timedelta(days=30)


NARROW_SCOPES = {
    "teams": TEAMS,
    "agent_self": SELF,
    "agents_only": AGENTS_ONLY,
    "no_team": CallerScope(workspace_id=WORKSPACE, team_ids=frozenset(), agent_user_ids=None),
}


@pytest.mark.parametrize("name", sorted(NARROW_SCOPES))
def test_coverage_status_refuses_a_caller_narrower_than_the_workspace(catalogue, name) -> None:
    # compute_coverage counts the whole workspace.  A team or agent caller would see the
    # counts of other teams, so the intent refuses, whatever the params say.
    scope = NARROW_SCOPES[name]
    first = _route(catalogue, "coverage_status", {}, scope=scope)
    _assert_refused(first, RefusalReason.OUTSIDE_CALLER_SCOPE)
    assert first.detail == RequestDefect.WHOLE_WORKSPACE_REQUIRED.value
    assert first.key is None
    for params in (
        {"from": "2026-10-01T00:00:00Z"},
        {"from": "yesterday"},
        {"team_id": TEAM_A},
        {"team_id": TEAM_OTHER},
        {"product": "x"},
        {"aaa": 1},
    ):
        assert _route(catalogue, "coverage_status", params, scope=scope) == first, params


def test_allow_control_a_whole_workspace_caller_gets_coverage(catalogue) -> None:
    decision = _route(catalogue, "coverage_status", {}, scope=WHOLE)
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.reason is None


def test_a_scope_key_still_wins_for_a_narrow_coverage_caller(catalogue) -> None:
    decision = _route(catalogue, "coverage_status", {"workspace_id": WORKSPACE}, scope=TEAMS)
    _assert_refused(decision, RefusalReason.SCOPE_NOT_ALLOWED)


def test_coverage_status_takes_only_a_window(catalogue) -> None:
    decision = _route(
        catalogue,
        "coverage_status",
        {"from": "2026-10-01T00:00:00+03:30", "to": "2026-10-20T00:00:00Z"},
    )
    assert decision.route is Route.SQL_AGGREGATE
    assert decision.gate is Gate.NONE
    assert decision.cell_kind is None
    for key, value in (("team_id", TEAM_A), ("issue", "complaint_refund"), ("language", "fa")):
        refused = _route(catalogue, "coverage_status", {key: value})
        _assert_refused(refused, RefusalReason.INVALID_REQUEST)
        assert (refused.detail, refused.key) == (RequestDefect.PARAM_NOT_ALLOWED.value, key)


# --- intent params ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent", ["issue_count", "issue_rate", "issue_trend", "evidence_examples"]
)
def test_a_selector_target_is_required(catalogue, intent: str) -> None:
    decision = _route(catalogue, intent, {"language": "fa"})
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)
    assert decision.detail == RequestDefect.TARGET_REQUIRED.value


@pytest.mark.parametrize("intent", ["issue_count", "issue_rate", "evidence_examples"])
def test_selectors_that_select_nothing_are_invalid(catalogue, intent: str) -> None:
    decision = _route(catalogue, intent, {"family": "call_reason", "issue": "complaint_refund"})
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)
    assert decision.detail == RequestDefect.TARGET_SELECTS_NOTHING.value


def test_evidence_for_an_unavailable_family_selects_nothing(catalogue) -> None:
    decision = _route(catalogue, "evidence_examples", {"family": "qa"})
    assert decision.detail == RequestDefect.TARGET_SELECTS_NOTHING.value
    # The aggregate routes, and gate_aggregate abstains on the unavailable cell.
    assert _route(catalogue, "issue_rate", {"family": "qa"}).route is Route.SQL_AGGREGATE


def test_issue_trend_grain(catalogue) -> None:
    default = _route(catalogue, "issue_trend", {"issue": "complaint_refund"})
    assert default.arguments.grain is DEFAULT_GRAIN is Grain.WEEK
    for grain in Grain:
        decision = _route(
            catalogue, "issue_trend", {"issue": "complaint_refund", "grain": grain.value}
        )
        assert decision.arguments.grain is grain
    for bad, detail in (
        ("hour", "param_bad_value"),
        ("WEEK", "param_bad_value"),
        (7, "param_bad_type"),
        (["week"], "param_bad_type"),
    ):
        decision = _route(catalogue, "issue_trend", {"issue": "complaint_refund", "grain": bad})
        _assert_refused(decision, RefusalReason.INVALID_REQUEST)
        assert (decision.detail, decision.key) == (detail, "grain")


def test_cohort_comparison_arguments(catalogue) -> None:
    assert COHORT_KEYS == ("language", "source_type", "team_id")
    decision = _route(
        catalogue,
        "cohort_comparison",
        {"issue": "complaint_refund", "compare_by": "language", "compare_values": ["FA", "en"]},
    )
    assert decision.arguments.compare_by == "language"
    assert decision.arguments.compare_values == ("fa", "en")  # order kept: left, right
    assert decision.notices == (ASSOCIATION_NOTICE,)
    assert decision.cell_kind is CellKind.RATE
    sources = _route(
        catalogue,
        "cohort_comparison",
        {
            "family": "call_reason",
            "compare_by": "source_type",
            "compare_values": ("sip_voip", "file_upload"),
        },
    )
    assert sources.route is Route.SQL_AGGREGATE


@pytest.mark.parametrize(
    ("params", "detail", "key"),
    [
        ({"compare_values": ["fa", "en"]}, "param_missing", "compare_by"),
        ({"compare_by": "language"}, "param_missing", "compare_values"),
        (
            {"compare_by": ["language"], "compare_values": ["fa", "en"]},
            "param_bad_type",
            "compare_by",
        ),
        ({"compare_by": "family", "compare_values": ["fa", "en"]}, "param_bad_value", "compare_by"),
        ({"compare_by": "language", "compare_values": "fa"}, "param_bad_type", "compare_values"),
        (
            {"compare_by": "language", "compare_values": ["fa", 3]},
            "param_bad_type",
            "compare_values",
        ),
        ({"compare_by": "language", "compare_values": ["fa"]}, "param_bad_value", "compare_values"),
        (
            {"compare_by": "language", "compare_values": ["fa", "en", "de"]},
            "param_bad_value",
            "compare_values",
        ),
        (
            {"compare_by": "language", "compare_values": ["fa", "FA"]},
            "param_bad_value",
            "compare_values",
        ),
        (
            {"compare_by": "language", "compare_values": ["fa", "e"]},
            "param_bad_value",
            "compare_values",
        ),
        (
            {"compare_by": "team_id", "compare_values": [TEAM_A, "x"]},
            "param_bad_value",
            "compare_values",
        ),
        (
            {"compare_by": "language", "compare_values": ["fa", "en"], "language": "fa"},
            "cohort_conflict",
            "language",
        ),
    ],
)
def test_cohort_comparison_param_errors(catalogue, params, detail, key) -> None:
    decision = _route(catalogue, "cohort_comparison", {"issue": "complaint_refund", **params})
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)
    assert (decision.detail, decision.key) == (detail, key)


def test_comparing_two_agents_is_refused_as_individual_ranking(catalogue) -> None:
    params = {
        "issue": "complaint_refund",
        "compare_by": "agent_user_id",
        "compare_values": [AGENT_SELF, AGENT_OTHER],
    }
    decision = _route(catalogue, "cohort_comparison", params)
    _assert_refused(decision, RefusalReason.INDIVIDUAL_RANKING_NOT_SUPPORTED)
    assert decision.key == "compare_by"


@pytest.mark.parametrize(
    "target",
    [
        {"issue": ["complaint_refund", "complaint_invoice"]},
        {"family": ["call_reason", "service_complaint"]},
        {"family": "service_complaint", "issue": "complaint_refund"},
        {"service_area": "refund"},
        {"issue": "complaint_refund", "service_area": "refund"},
        {},
    ],
)
def test_cohort_comparison_needs_exactly_one_row(catalogue, target: dict) -> None:
    params = {**target, "compare_by": "language", "compare_values": ["fa", "en"]}
    decision = _route(catalogue, "cohort_comparison", params)
    _assert_refused(decision, RefusalReason.INVALID_REQUEST)
    assert decision.detail == RequestDefect.TARGET_NOT_ONE_ROW.value


def test_evidence_examples_limit(catalogue) -> None:
    decision = _route(catalogue, "evidence_examples", {"issue": "complaint_refund"})
    assert decision.arguments.limit == DEFAULT_EVIDENCE_LIMIT
    top = _route(
        catalogue, "evidence_examples", {"issue": "complaint_refund", "limit": MAX_EVIDENCE_LIMIT}
    )
    assert top.arguments.limit == MAX_EVIDENCE_LIMIT
    for bad, detail in (
        (0, "param_bad_value"),
        (MAX_EVIDENCE_LIMIT + 1, "param_bad_value"),
        (True, "param_bad_type"),
        ("5", "param_bad_type"),
        (2.0, "param_bad_type"),
    ):
        refused = _route(
            catalogue, "evidence_examples", {"issue": "complaint_refund", "limit": bad}
        )
        _assert_refused(refused, RefusalReason.INVALID_REQUEST)
        assert (refused.detail, refused.key) == (detail, "limit")


def test_conversation_lookup_params(catalogue) -> None:
    decision = _route(
        catalogue,
        "conversation_lookup",
        {"conversation_id": CONVERSATION, "query": "  the refund  "},
    )
    assert decision.arguments.conversation_id == CONVERSATION
    assert decision.arguments.query == "the refund"
    assert decision.filters is None
    long_query = "x" * MAX_QUERY_LENGTH
    assert _route(
        catalogue, "conversation_lookup", {"conversation_id": CONVERSATION, "query": long_query}
    ).arguments.query == (long_query)
    cases = [
        ({}, "param_missing", "conversation_id"),
        ({"conversation_id": CONVERSATION.upper()}, "param_bad_value", "conversation_id"),
        ({"conversation_id": "42"}, "param_bad_value", "conversation_id"),
        ({"conversation_id": [CONVERSATION]}, "param_bad_type", "conversation_id"),
        ({"conversation_id": CONVERSATION, "query": "   "}, "param_bad_value", "query"),
        ({"conversation_id": CONVERSATION, "query": "a\nb"}, "param_bad_value", "query"),
        (
            {"conversation_id": CONVERSATION, "query": "x" * (MAX_QUERY_LENGTH + 1)},
            "param_bad_value",
            "query",
        ),
        ({"conversation_id": CONVERSATION, "query": 3}, "param_bad_type", "query"),
        ({"conversation_id": CONVERSATION, "team_id": TEAM_A}, "param_not_allowed", "team_id"),
        (
            {"conversation_id": CONVERSATION, "from": "2026-10-01T00:00:00Z"},
            "param_not_allowed",
            "from",
        ),
        ({"conversation_id": CONVERSATION, "limit": 3}, "filter_unknown_key", "limit"),
    ]
    for params, detail, key in cases:
        refused = _route(catalogue, "conversation_lookup", params)
        _assert_refused(refused, RefusalReason.INVALID_REQUEST)
        assert (refused.detail, refused.key) == (detail, key), params


# --- rule 7: sample minimums come from the cell ----------------------------------------------


def _sql(catalogue, intent: str = "issue_rate"):
    decision = _route(catalogue, intent, _valid_params(intent))
    assert decision.route is Route.SQL_AGGREGATE
    return decision


def test_a_cell_below_the_minimum_abstains(catalogue) -> None:
    decision = _sql(catalogue)
    cell = sample_cell(3, RATE_MINIMUM - 1)
    assert cell.status is CellStatus.INSUFFICIENT_SAMPLE
    gated = gate_aggregate(decision, cell)
    assert gated.route is Route.ABSTAIN
    assert gated.reason is AbstainReason.BELOW_MINIMUM_SAMPLE
    assert gated.detail == CellReason.BELOW_MINIMUM_SAMPLE.value
    assert gated == replace(
        decision,
        route=Route.ABSTAIN,
        reason=AbstainReason.BELOW_MINIMUM_SAMPLE,
        detail="below_minimum_sample",
    )


def test_allow_control_a_cell_at_exactly_the_minimum_passes_unchanged(catalogue) -> None:
    decision = _sql(catalogue)
    cell = sample_cell(3, RATE_MINIMUM)
    assert cell.status is CellStatus.COMPUTED
    assert gate_aggregate(decision, cell) is decision


def test_an_empty_denominator_abstains_not_computable(catalogue) -> None:
    gated = gate_aggregate(_sql(catalogue), sample_cell(0, 0))
    assert gated.route is Route.ABSTAIN
    assert gated.reason is AbstainReason.NOT_COMPUTABLE
    assert gated.detail == CellReason.EMPTY_DENOMINATOR.value


def test_an_unavailable_source_abstains_not_computable(catalogue) -> None:
    cell = unavailable_cell(120, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=RATE_MINIMUM)
    gated = gate_aggregate(_sql(catalogue), cell)
    assert gated.reason is AbstainReason.NOT_COMPUTABLE
    assert gated.detail == "scorecard_results_unavailable"


def test_a_hidden_agent_cell_abstains(catalogue) -> None:
    cell = sample_cell(1, 4, kind=CellKind.AGENT)
    assert cell.numerator is None
    assert gate_aggregate(_sql(catalogue), cell).reason is AbstainReason.BELOW_MINIMUM_SAMPLE


def test_comparisons_are_gated_by_their_status(catalogue) -> None:
    decision = _sql(catalogue, "cohort_comparison")
    passing = compare_cells((30, COMPARISON_MINIMUM + 10), (20, COMPARISON_MINIMUM))
    assert passing.status is CellStatus.COMPUTED
    assert gate_aggregate(decision, passing) is decision
    # A side above the rate minimum but under the comparison minimum.
    short = compare_cells((20, COMPARISON_MINIMUM + 10), (9, COMPARISON_MINIMUM - 1))
    assert short.right.denominator >= RATE_MINIMUM
    assert gate_aggregate(decision, short).reason is AbstainReason.BELOW_MINIMUM_SAMPLE
    empty = compare_cells((0, 0), (20, COMPARISON_MINIMUM))
    assert gate_aggregate(decision, empty).reason is AbstainReason.NOT_COMPUTABLE
    assert gate_aggregate(decision, empty).detail == "empty_denominator"


AGENT_MINIMUM = SAMPLE_POLICY_V1.min_denominator_agent


def test_a_comparison_intent_takes_only_comparisons(catalogue) -> None:
    decision = _sql(catalogue, "cohort_comparison")
    passing = compare_cells((30, COMPARISON_MINIMUM), (20, COMPARISON_MINIMUM))
    assert gate_aggregate(decision, [passing, passing]) is decision
    # A lone cell would skip the comparison minimum of each side.
    with pytest.raises(ValueError):
        gate_aggregate(decision, sample_cell(3, RATE_MINIMUM))
    with pytest.raises(ValueError):
        gate_aggregate(decision, [passing, sample_cell(3, RATE_MINIMUM)])


@pytest.mark.parametrize("intent", ["issue_count", "issue_rate", "issue_trend"])
def test_other_cell_intents_take_only_cells(catalogue, intent: str) -> None:
    passing = compare_cells((30, COMPARISON_MINIMUM), (20, COMPARISON_MINIMUM))
    with pytest.raises(ValueError):
        gate_aggregate(_sql(catalogue, intent), passing)


def test_issue_count_shows_an_exact_small_rate_count(catalogue) -> None:
    # HOM-79 rule: small counts stay visible, F49.  HOM-97 shows the denominator and the
    # minimum next to the count.
    decision = _sql(catalogue, "issue_count")
    small = sample_cell(5, 10)
    assert small.status is CellStatus.INSUFFICIENT_SAMPLE and small.numerator == 5
    assert gate_aggregate(decision, small) is decision
    assert gate_aggregate(decision, sample_cell(5, RATE_MINIMUM)) is decision
    assert gate_aggregate(decision, [small, sample_cell(0, 3)]) is decision


def test_issue_count_abstains_when_the_count_is_hidden(catalogue) -> None:
    decision = _sql(catalogue, "issue_count")
    hidden = sample_cell(3, 29, kind=CellKind.AGENT)
    assert hidden.denominator == AGENT_MINIMUM - 1 and hidden.numerator is None
    gated = gate_aggregate(decision, hidden)
    assert gated.route is Route.ABSTAIN
    assert gated.reason is AbstainReason.BELOW_MINIMUM_SAMPLE
    assert gated.detail == "below_minimum_sample"
    shown = sample_cell(3, AGENT_MINIMUM, kind=CellKind.AGENT)
    assert gate_aggregate(decision, shown) is decision
    assert gate_aggregate(decision, [sample_cell(5, 10), hidden]).reason is (
        AbstainReason.BELOW_MINIMUM_SAMPLE
    )


def test_issue_count_abstains_when_not_computable(catalogue) -> None:
    decision = _sql(catalogue, "issue_count")
    empty = gate_aggregate(decision, sample_cell(0, 0))
    assert empty.reason is AbstainReason.NOT_COMPUTABLE
    assert empty.detail == "empty_denominator"
    source = unavailable_cell(120, CellReason.SCORECARD_RESULTS_UNAVAILABLE, minimum=RATE_MINIMUM)
    assert gate_aggregate(decision, source).reason is AbstainReason.NOT_COMPUTABLE
    # not_computable wins over a hidden count, in any order.
    hidden = sample_cell(3, 29, kind=CellKind.AGENT)
    assert gate_aggregate(decision, [hidden, sample_cell(0, 0)]).reason is (
        AbstainReason.NOT_COMPUTABLE
    )


def test_issue_rate_still_abstains_on_a_small_cell(catalogue) -> None:
    gated = gate_aggregate(_sql(catalogue, "issue_rate"), sample_cell(5, 10))
    assert gated.reason is AbstainReason.BELOW_MINIMUM_SAMPLE
    trend = gate_aggregate(_sql(catalogue, "issue_trend"), [sample_cell(5, 10)])
    assert trend.reason is AbstainReason.BELOW_MINIMUM_SAMPLE


def test_a_series_passes_only_when_every_point_is_computed(catalogue) -> None:
    decision = _sql(catalogue, "issue_trend")
    good = [sample_cell(1, RATE_MINIMUM), sample_cell(2, RATE_MINIMUM + 5)]
    assert gate_aggregate(decision, good) is decision
    assert gate_aggregate(decision, tuple(good)) is decision
    weak = [*good, sample_cell(1, RATE_MINIMUM - 1)]
    assert gate_aggregate(decision, weak).reason is AbstainReason.BELOW_MINIMUM_SAMPLE
    # not_computable wins over below_minimum_sample, whatever the order.
    mixed = [sample_cell(1, RATE_MINIMUM - 1), sample_cell(0, 0)]
    assert gate_aggregate(decision, mixed).reason is AbstainReason.NOT_COMPUTABLE


@pytest.mark.parametrize("bad", [[], (), None, 3, {"status": "computed"}, [sample_cell(1, 40), 3]])
def test_gate_aggregate_refuses_a_shape_it_does_not_know(catalogue, bad: object) -> None:
    with pytest.raises(ValueError):
        gate_aggregate(_sql(catalogue), bad)  # type: ignore[arg-type]


def test_gate_aggregate_only_gates_cell_decisions(catalogue) -> None:
    cell = sample_cell(0, 0)
    evidence = _route(catalogue, "evidence_examples", _valid_params("evidence_examples"))
    with pytest.raises(ValueError):
        gate_aggregate(evidence, cell)
    coverage = _route(catalogue, "coverage_status")
    with pytest.raises(ValueError):
        gate_aggregate(coverage, cell)
    with pytest.raises(ValueError):
        gate_aggregate("decision", cell)  # type: ignore[arg-type]


def test_a_gate_never_changes_a_refusal_or_an_abstention(catalogue) -> None:
    refused = _route(catalogue, "nope")
    assert gate_aggregate(refused, sample_cell(0, 0)) is refused
    assert gate_evidence(refused, 0) is refused
    abstained = gate_aggregate(_sql(catalogue), sample_cell(0, 0))
    assert gate_aggregate(abstained, sample_cell(1, RATE_MINIMUM)) is abstained


# --- gate_evidence ---------------------------------------------------------------------------


def test_no_authorized_evidence_abstains(catalogue) -> None:
    decision = _route(catalogue, "evidence_examples", _valid_params("evidence_examples"))
    gated = gate_evidence(decision, 0)
    assert gated.route is Route.ABSTAIN
    assert gated.reason is AbstainReason.NO_EVIDENCE
    assert gated.intent_id == "evidence_examples"


def test_allow_control_one_authorized_span_passes_unchanged(catalogue) -> None:
    decision = _route(catalogue, "evidence_examples", _valid_params("evidence_examples"))
    assert gate_evidence(decision, 1) is decision
    assert gate_evidence(decision, 25) is decision


@pytest.mark.parametrize("bad", [-1, True, False, 1.0, "1", None])
def test_gate_evidence_refuses_a_bad_count(catalogue, bad: object) -> None:
    decision = _route(catalogue, "evidence_examples", _valid_params("evidence_examples"))
    with pytest.raises(ValueError):
        gate_evidence(decision, bad)  # type: ignore[arg-type]


def test_gate_evidence_only_gates_evidence_decisions(catalogue) -> None:
    with pytest.raises(ValueError):
        gate_evidence(_sql(catalogue), 1)


# --- rule 8: determinism and no mutation -----------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "params", "scope"),
    [
        ("issue_trend", {"issue": ["complaint_refund"], "grain": "day", "language": ["FA"]}, TEAMS),
        (
            "cohort_comparison",
            {"family": "call_reason", "compare_by": "team_id", "compare_values": [TEAM_B, TEAM_A]},
            TEAMS,
        ),
        ("issue_rate", {"issue": "complaint_refund", "team_id": [TEAM_OTHER]}, TEAMS),
        ("conversation_lookup", {"conversation_id": CONVERSATION, "query": " x "}, SELF),
        ("nope", {"workspace_id": "x"}, WHOLE),
    ],
)
def test_routing_is_deterministic_and_never_mutates(catalogue, intent, params, scope) -> None:
    before_params, before_scope = copy.deepcopy(params), copy.deepcopy(scope)
    first = route_request(IntentRequest(intent, params), catalogue, scope=scope, now=NOW)
    second = route_request(
        IntentRequest(intent, copy.deepcopy(params)),
        load_catalogue(),
        scope=copy.deepcopy(scope),
        now=NOW,
    )
    assert first == second
    assert first.to_json() == second.to_json()
    assert params == before_params
    assert scope == before_scope


def test_a_read_only_mapping_is_accepted(catalogue) -> None:
    params = MappingProxyType({"issue": "complaint_refund"})
    decision = route_request(IntentRequest("issue_rate", params), catalogue, scope=WHOLE, now=NOW)
    assert decision.route is Route.SQL_AGGREGATE


def test_the_same_instant_in_another_timezone_gives_the_same_decision(catalogue) -> None:
    tehran = NOW.astimezone(datetime.fromisoformat("2026-10-23T15:30:00+03:30").tzinfo)
    first = _route(catalogue, "issue_rate", {"issue": "complaint_refund"})
    second = route_request(
        IntentRequest("issue_rate", {"issue": "complaint_refund"}),
        catalogue,
        scope=WHOLE,
        now=tehran,
    )
    assert first == second


def test_decisions_are_frozen(catalogue) -> None:
    decision = _sql(catalogue)
    with pytest.raises(AttributeError):
        decision.route = Route.REFUSE  # type: ignore[misc]


# --- rule 9: evidence never gives a number ---------------------------------------------------


def test_evidence_decisions_carry_nothing_to_count(catalogue) -> None:
    for intent in ("evidence_examples", "conversation_lookup"):
        decision = _route(catalogue, intent, _valid_params(intent))
        assert decision.cell_kind is None
        assert decision.gate is Gate.EVIDENCE
        assert "cell_kind" in decision.to_json() and decision.to_json()["cell_kind"] is None


def test_a_number_with_examples_is_two_requests(catalogue) -> None:
    # "How many refund complaints last month, and show me some": one aggregate request and
    # one evidence request with the same filters.  The number comes only from the first.
    shared = {
        "issue": "complaint_refund",
        "from": "2026-09-01T00:00:00Z",
        "to": "2026-10-01T00:00:00Z",
    }
    number = _route(catalogue, "issue_count", shared)
    examples = _route(catalogue, "evidence_examples", {**shared, "limit": 3})
    assert number.route is Route.SQL_AGGREGATE
    assert examples.route is Route.EVIDENCE_RETRIEVAL
    assert number.filters == examples.filters
    # The examples request cannot be gated as a number.
    with pytest.raises(ValueError):
        gate_aggregate(examples, sample_cell(5, RATE_MINIMUM))


def test_to_json_shape(catalogue) -> None:
    decision = _route(catalogue, "issue_trend", {"issue": "complaint_refund"})
    data = decision.to_json()
    assert set(data) == {
        "route",
        "intent_id",
        "reason",
        "detail",
        "key",
        "gate",
        "cell_kind",
        "filters",
        "arguments",
        "notices",
        "catalogue_version",
        "catalogue_digest",
    }
    assert data["route"] == "sql_aggregate"
    assert data["arguments"]["grain"] == "week"
    assert data["filters"] == decision.filters.to_json()
    refused = _route(catalogue, "nope").to_json()
    assert refused["filters"] is None and refused["arguments"] is None
    assert refused["reason"] == "out_of_scope"
