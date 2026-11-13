"""HOM-96 (WP17-T05): the pure parts of the analytical tools, without a database.

* The registry: exactly five tools, read-only, schemas with ``additionalProperties: false``
  whose property names equal the keys each param check accepts, and a pinned digest.
* ``ToolLimits`` and its caps.
* ``ToolResult`` and ``Fact``: the exact JSON keys, the abstention shape, determinism and
  the half-even rounding of a mean.
* The param checks of every tool, each defect with its ``detail`` and ``key``, and the
  order of SPEC A3 (unknown key, then trend filters, then the tool's own params).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import MappingProxyType

import pytest

from backend.copilot_intents import RefusalReason
from backend.copilot_tools import (
    ACCEPTED_KEYS,
    DEFAULT_LIMITS,
    LIMIT_CAPS,
    REGISTRY_DIGEST,
    TOOLS,
    TOOLS_VERSION,
    AbstainReason,
    Fact,
    ParamDefect,
    ParamError,
    ResultStatus,
    ToolLimits,
    ToolResult,
    check_limits,
    check_params,
    registry_digest,
    round_half_even_2,
    tool_schemas,
)
from backend.copilot_tools.params import (
    ActionItemsDueParams,
    ConversationCountParams,
    IssueAggregateParams,
    ScorecardSummaryParams,
    TopIssuesParams,
)
from backend.security.authorization import Permission

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
TEAM = "7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"
USER = "0c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
VERSION = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
TOOL_NAMES = (
    "action_items_due",
    "conversation_count",
    "issue_aggregate",
    "scorecard_summary",
    "top_issues",
)
# Pinned on purpose: a changed schema, description or permission changes it.
PINNED_DIGEST = "ccc1658cbbe2d3ba67e512a91753e5a6d0a6d5190b6faede638dac35fffc5f0d"


def defect(tool: str, params: object) -> tuple[str, str | None, str | None]:
    with pytest.raises(ParamError) as raised:
        check_params(tool, params, now=NOW)
    return raised.value.reason.value, raised.value.detail, raised.value.key


# --- registry -------------------------------------------------------------------------------


def test_exactly_five_tools_and_the_mapping_is_read_only():
    assert tuple(TOOLS) == TOOL_NAMES
    assert isinstance(TOOLS, MappingProxyType)
    with pytest.raises(TypeError):
        TOOLS["sql"] = TOOLS["conversation_count"]  # type: ignore[index]
    assert TOOLS_VERSION == "copilot-tools/1"


def test_tool_schemas_are_closed_objects_sorted_by_name():
    schemas = tool_schemas()
    assert [schema["name"] for schema in schemas] == list(TOOL_NAMES)
    for schema in schemas:
        assert set(schema) == {"name", "description", "input_schema"}
        assert "\n" not in schema["description"] and schema["description"].endswith(".")
        body = schema["input_schema"]
        assert body["type"] == "object"
        assert body["additionalProperties"] is False
        assert set(body["required"]) <= set(body["properties"])


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_schema_properties_equal_the_keys_the_check_accepts(tool):
    """Both directions: no schema property the check refuses, no accepted key missing."""
    assert set(TOOLS[tool].input_schema["properties"]) == set(ACCEPTED_KEYS[tool])


@pytest.mark.parametrize(
    ("tool", "required"),
    [
        pytest.param("action_items_due", ["due_before"], id="action_items_due"),
        pytest.param("conversation_count", [], id="conversation_count"),
        pytest.param("issue_aggregate", ["intent", "params"], id="issue_aggregate"),
        pytest.param("scorecard_summary", ["scorecard_version_id"], id="scorecard_summary"),
        pytest.param("top_issues", ["family"], id="top_issues"),
    ],
)
def test_required_schema_keys_are_the_required_params(tool, required):
    assert TOOLS[tool].input_schema["required"] == required
    for key in required:
        params = {}
        if tool == "issue_aggregate":
            params = {"intent": "issue_count", "params": {}}
        elif tool == "action_items_due":
            params = {"due_before": "2026-10-10T00:00:00+00:00"}
        elif tool == "scorecard_summary":
            params = {"scorecard_version_id": VERSION}
        elif tool == "top_issues":
            params = {"family": "call_reason"}
        params.pop(key)
        assert defect(tool, params) == ("params_invalid", "param_missing", key)


def test_permissions_are_sorted_and_scorecards_need_scorecard_read():
    for spec in TOOLS.values():
        values = [p.value for p in spec.required_permissions]
        assert values == sorted(values)
        assert Permission.MEETING_READ in spec.required_permissions
    assert TOOLS["scorecard_summary"].required_permissions == (
        Permission.MEETING_READ,
        Permission.SCORECARD_READ,
    )


def test_the_registry_digest_is_pinned_and_schemas_are_copies():
    assert REGISTRY_DIGEST == PINNED_DIGEST
    assert registry_digest() == PINNED_DIGEST
    schemas = tool_schemas()
    schemas[0]["input_schema"]["properties"].clear()
    schemas[0]["description"] = "changed"
    assert registry_digest() == PINNED_DIGEST
    assert tool_schemas()[0]["input_schema"]["properties"]


# --- limits ---------------------------------------------------------------------------------


def test_default_limits_and_caps():
    assert DEFAULT_LIMITS == ToolLimits(5000, 100, 5000, 10000)
    assert LIMIT_CAPS == ToolLimits(20000, 500, 15000, 30000)
    assert check_limits(DEFAULT_LIMITS) is DEFAULT_LIMITS
    assert check_limits(LIMIT_CAPS) is LIMIT_CAPS
    assert check_limits(ToolLimits(1, 1, 1, 1)).max_rows == 1


@pytest.mark.parametrize(
    "limits",
    [
        pytest.param(ToolLimits(20001, 100, 5000, 10000), id="max_rows-above-cap"),
        pytest.param(ToolLimits(5000, 501, 5000, 10000), id="max_citations-above-cap"),
        pytest.param(ToolLimits(5000, 100, 15001, 10000), id="timeout-above-cap"),
        pytest.param(ToolLimits(5000, 100, 5000, 30001), id="budget-above-cap"),
        pytest.param(ToolLimits(0, 100, 5000, 10000), id="zero"),
        pytest.param(ToolLimits(True, 100, 5000, 10000), id="bool"),  # type: ignore[arg-type]
        pytest.param(ToolLimits(5000.0, 100, 5000, 10000), id="float"),  # type: ignore[arg-type]
        pytest.param({"max_rows": 1}, id="not-limits"),
    ],
)
def test_bad_limits_are_a_value_error(limits):
    with pytest.raises(ValueError):
        check_limits(limits)


# --- results --------------------------------------------------------------------------------


FACT_KEYS = {
    "name",
    "group",
    "value",
    "parts",
    "citations",
    "citations_total",
    "citations_complete",
    "citations_withheld",
}
RESULT_KEYS = {
    "tools_version",
    "tool",
    "status",
    "reason",
    "detail",
    "key",
    "filters",
    "scope",
    "facts",
    "provenance",
    "limits",
}


def test_reason_and_defect_values_are_exact():
    assert {reason.value for reason in AbstainReason} == {
        "tool_unknown",
        "permission_denied",
        "params_invalid",
        "scope_not_allowed",
        "unsupported_filter",
        "individual_ranking_not_supported",
        "intent_not_supported",
        "row_limit_exceeded",
        "time_limit_exceeded",
        "below_minimum_sample",
        "not_computable",
        "withheld",
        "out_of_scope",
        "invalid_request",
        "outside_caller_scope",
        "causal_not_supported",
        "forecast_not_supported",
    }
    # every HOM-92 refusal passes through unchanged
    assert {reason.value for reason in RefusalReason} <= {r.value for r in AbstainReason}
    assert {d.value for d in ParamDefect} == {
        "params_not_mapping",
        "param_unknown",
        "param_missing",
        "param_bad_type",
        "param_bad_value",
    }
    assert {status.value for status in ResultStatus} == {"answered", "abstain"}


def test_a_cited_fact_caps_its_citations_and_says_if_complete():
    ids = ["c", "a", "e", "b", "d"]
    capped = Fact.cited("conversations", value=5, ids=ids, max_citations=3)
    assert capped.to_json() == {
        "name": "conversations",
        "group": None,
        "value": 5,
        "parts": None,
        "citations": ["a", "b", "c"],
        "citations_total": 5,
        "citations_complete": False,
        "citations_withheld": False,
    }
    whole = Fact.cited("x", value=5, ids=ids, max_citations=5, group={"status": "open"})
    assert whole.to_json()["citations"] == ["a", "b", "c", "d", "e"]
    assert whole.to_json()["citations_complete"] is True
    assert whole.to_json()["group"] == {"status": "open"}
    with pytest.raises(ValueError):
        Fact.cited("x", value=2, ids=["a", "a"], max_citations=5)


def test_a_withheld_fact_lists_nothing_and_keeps_its_total():
    fact = Fact.withheld("issue_count", value=7, total=7, group={"row": "issue:complaint_refund"})
    assert set(fact.to_json()) == FACT_KEYS
    assert fact.to_json()["citations"] == []
    assert fact.to_json()["citations_total"] == 7
    assert fact.to_json()["citations_complete"] is False
    assert fact.to_json()["citations_withheld"] is True


def test_ratio_parts_are_numerator_and_denominator():
    fact = Fact.cited("issue_rate", value=0.375, ids=["a"], max_citations=5, parts=(12, 32))
    assert fact.to_json()["parts"] == {"numerator": 12, "denominator": 32}


def test_an_abstention_has_no_filters_facts_or_provenance():
    result = ToolResult.abstain(
        "conversation_count",
        AbstainReason.PARAMS_INVALID,
        restricted=True,
        limits=DEFAULT_LIMITS,
        detail="param_unknown",
        key="foo",
    )
    data = result.to_json()
    assert set(data) == RESULT_KEYS
    assert data == {
        "tools_version": "copilot-tools/1",
        "tool": "conversation_count",
        "status": "abstain",
        "reason": "params_invalid",
        "detail": "param_unknown",
        "key": "foo",
        "filters": None,
        "scope": {"restricted": True},
        "facts": [],
        "provenance": {},
        "limits": {
            "max_rows": 5000,
            "max_citations": 100,
            "statement_timeout_ms": 5000,
            "time_budget_ms": 10000,
        },
    }
    with pytest.raises(ValueError):
        ToolResult(
            tool="x",
            status=ResultStatus.ABSTAIN,
            reason=AbstainReason.WITHHELD,
            detail=None,
            key=None,
            filters={"a": 1},
            restricted=False,
            facts=(),
            limits=DEFAULT_LIMITS,
        )


def test_an_answered_result_is_deterministic_and_a_copy():
    facts = [Fact.cited("conversations", value=2, ids=["b", "a"], max_citations=10)]
    filters = {"window": {"from": "x", "to": "y"}, "team_id": []}
    one = ToolResult.answered(
        "conversation_count", filters=filters, facts=facts, restricted=False, limits=DEFAULT_LIMITS
    )
    filters["team_id"].append("changed")
    two = ToolResult.answered(
        "conversation_count",
        filters={"window": {"from": "x", "to": "y"}, "team_id": []},
        facts=facts,
        restricted=False,
        limits=DEFAULT_LIMITS,
    )
    assert json.dumps(one.to_json(), sort_keys=True) == json.dumps(two.to_json(), sort_keys=True)
    data = one.to_json()
    data["filters"]["team_id"].append("x")
    assert one.to_json()["filters"]["team_id"] == []
    assert data["status"] == "answered" and data["reason"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(Fraction(12345, 1000), 12.34, id="half-to-even-down"),
        pytest.param(Fraction(12355, 1000), 12.36, id="half-to-even-up"),
        pytest.param(Fraction(975, 14), 69.64, id="69.642857"),
        pytest.param(Fraction(100, 3), 33.33, id="third"),
        pytest.param(Fraction(200, 3), 66.67, id="two-thirds"),
        pytest.param(Fraction(85), 85.0, id="whole"),
        pytest.param(Fraction(1, 200), 0.0, id="0.005-to-even"),
        pytest.param(Fraction(3, 200), 0.02, id="0.015-to-even"),
    ],
)
def test_round_half_even_2_is_exact(value, expected):
    assert round_half_even_2(value) == expected


# --- param checks ---------------------------------------------------------------------------


def test_conversation_count_defaults():
    checked = check_params("conversation_count", {}, now=NOW)
    assert isinstance(checked, ConversationCountParams)
    assert checked.group_by == "none"
    assert checked.window.end == NOW and checked.window.start == NOW - timedelta(days=30)
    assert checked.window.to_json()["basis"] == "meeting_created_at"
    assert checked.team_ids == () and checked.agent_user_ids == ()


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param(
            {"zzz": 1, "aaa": 2}, ("params_invalid", "param_unknown", "aaa"), id="unknown-sorted"
        ),
        pytest.param(
            {"group_by": "nope", "team_id": "bad"},
            ("params_invalid", "filter_bad_value", "team_id"),
            id="filter-before-own-param",
        ),
        pytest.param(
            {"aaa": 1, "team_id": "bad"},
            ("params_invalid", "param_unknown", "aaa"),
            id="unknown-before-filter",
        ),
        pytest.param(
            {"team_id": 5}, ("params_invalid", "filter_bad_type", "team_id"), id="team-type"
        ),
        pytest.param(
            {"from": "2026-10-02T00:00:00+00:00", "to": "2026-10-01T00:00:00+00:00"},
            ("params_invalid", "filter_window_invalid", None),
            id="window-reversed",
        ),
        pytest.param(
            {"from": "2026-10-02T00:00:00"},
            ("params_invalid", "filter_window_invalid", "from"),
            id="no-timezone",
        ),
        pytest.param(
            {"group_by": 3}, ("params_invalid", "param_bad_type", "group_by"), id="group-type"
        ),
        pytest.param(
            {"group_by": "hour"},
            ("params_invalid", "param_bad_value", "group_by"),
            id="group-value",
        ),
        pytest.param(
            {"group_by": "agent_user_id"},
            ("individual_ranking_not_supported", None, "group_by"),
            id="group-by-agent",
        ),
        pytest.param(
            {"language": "fa"}, ("params_invalid", "param_unknown", "language"), id="language"
        ),
    ],
)
def test_conversation_count_defects(params, expected):
    assert defect("conversation_count", params) == expected


def test_conversation_count_normalizes_filters():
    checked = check_params(
        "conversation_count",
        {"team_id": [TEAM, TEAM], "agent_user_id": USER, "group_by": "week"},
        now=NOW,
    )
    assert checked.team_ids == (TEAM,) and checked.agent_user_ids == (USER,)
    assert checked.group_by == "week"


def test_action_items_due_defaults_and_normalization():
    checked = check_params(
        "action_items_due",
        {
            "due_before": "2026-10-10T03:30:00+03:30",
            "status": ["open", "open"],
            "assignee_user_id": [USER, USER],
            "team_id": TEAM,
        },
        now=NOW,
    )
    assert isinstance(checked, ActionItemsDueParams)
    assert checked.window.to_json() == {
        "from": None,
        "to": "2026-10-10T00:00:00+00:00",
        "basis": "action_item_due_at",
    }
    assert checked.statuses == ("open",)
    assert checked.assignee_user_ids == (USER,)
    assert checked.team_ids == (TEAM,)
    assert checked.group_by == "none"
    default = check_params("action_items_due", {"due_before": "2026-10-10T00:00:00Z"}, now=NOW)
    assert default.statuses == ("in_progress", "open")


AFTER = "2026-10-01T00:00:00+00:00"
BEFORE = "2026-10-10T00:00:00+00:00"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({}, ("params_invalid", "param_missing", "due_before"), id="missing"),
        pytest.param(
            {"due_before": 5}, ("params_invalid", "param_bad_type", "due_before"), id="type"
        ),
        pytest.param(
            {"due_before": "2026-10-10"},
            ("params_invalid", "param_bad_value", "due_before"),
            id="no-timezone",
        ),
        pytest.param(
            {"due_before": "tomorrow"},
            ("params_invalid", "param_bad_value", "due_before"),
            id="text",
        ),
        pytest.param(
            {"due_before": AFTER, "due_after": BEFORE},
            ("params_invalid", "param_bad_value", "due_after"),
            id="after-not-before",
        ),
        pytest.param(
            {"due_before": BEFORE, "due_after": BEFORE},
            ("params_invalid", "param_bad_value", "due_after"),
            id="after-equal",
        ),
        pytest.param(
            {"due_before": "2027-10-03T00:00:00+00:00", "due_after": AFTER},
            ("params_invalid", "param_bad_value", "due_after"),
            id="span-over-366-days",
        ),
        pytest.param(
            {"due_before": BEFORE, "due_after": "x"},
            ("params_invalid", "param_bad_value", "due_after"),
            id="bad-after",
        ),
        pytest.param(
            {"due_after": "x"},
            ("params_invalid", "param_bad_value", "due_after"),
            id="due_after-sorts-before-due_before",
        ),
        pytest.param(
            {"due_before": BEFORE, "assignee_user_id": "ABC"},
            ("params_invalid", "param_bad_value", "assignee_user_id"),
            id="assignee-value",
        ),
        pytest.param(
            {"due_before": BEFORE, "assignee_user_id": [1]},
            ("params_invalid", "param_bad_type", "assignee_user_id"),
            id="assignee-type",
        ),
        pytest.param(
            {"due_before": BEFORE, "assignee_user_id": []},
            ("params_invalid", "param_bad_value", "assignee_user_id"),
            id="assignee-empty",
        ),
        pytest.param(
            {"due_before": BEFORE, "status": "done"},
            ("params_invalid", "param_bad_value", "status"),
            id="status-done",
        ),
        pytest.param(
            {"due_before": BEFORE, "status": []},
            ("params_invalid", "param_bad_value", "status"),
            id="status-empty",
        ),
        pytest.param(
            {"due_before": BEFORE, "status": {"open": 1}},
            ("params_invalid", "param_bad_type", "status"),
            id="status-type",
        ),
        pytest.param(
            {"due_before": BEFORE, "group_by": "assignee_user_id"},
            ("individual_ranking_not_supported", None, "group_by"),
            id="group-by-assignee",
        ),
        pytest.param(
            {"due_before": BEFORE, "group_by": "agent_user_id"},
            ("individual_ranking_not_supported", None, "group_by"),
            id="group-by-agent",
        ),
        pytest.param(
            {"due_before": BEFORE, "group_by": "day"},
            ("params_invalid", "param_bad_value", "group_by"),
            id="group-day",
        ),
        pytest.param(
            {"due_before": BEFORE, "team_id": "x"},
            ("params_invalid", "filter_bad_value", "team_id"),
            id="team",
        ),
        pytest.param(
            {"due_before": BEFORE, "from": AFTER},
            ("params_invalid", "param_unknown", "from"),
            id="no-window-keys",
        ),
    ],
)
def test_action_items_due_defects(params, expected):
    assert defect("action_items_due", params) == expected


def test_action_items_due_span_of_exactly_366_days_passes():
    checked = check_params(
        "action_items_due",
        {"due_after": AFTER, "due_before": "2027-10-02T00:00:00+00:00"},
        now=NOW,
    )
    assert checked.window.start == datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({}, ("params_invalid", "param_missing", "scorecard_version_id"), id="missing"),
        pytest.param(
            {"scorecard_version_id": 1},
            ("params_invalid", "param_bad_type", "scorecard_version_id"),
            id="type",
        ),
        pytest.param(
            {"scorecard_version_id": VERSION.upper()},
            ("params_invalid", "param_bad_value", "scorecard_version_id"),
            id="uppercase",
        ),
        pytest.param(
            {"scorecard_version_id": VERSION, "agent_user_id": "x"},
            ("params_invalid", "filter_bad_value", "agent_user_id"),
            id="agent",
        ),
        pytest.param(
            {"agent_user_id": "x"},
            ("params_invalid", "filter_bad_value", "agent_user_id"),
            id="filter-before-missing-version",
        ),
        pytest.param(
            {"scorecard_version_id": VERSION, "group_by": "status"},
            ("params_invalid", "param_unknown", "group_by"),
            id="no-group-by",
        ),
    ],
)
def test_scorecard_summary_defects(params, expected):
    assert defect("scorecard_summary", params) == expected


def test_scorecard_summary_checked():
    checked = check_params("scorecard_summary", {"scorecard_version_id": VERSION}, now=NOW)
    assert isinstance(checked, ScorecardSummaryParams)
    assert checked.scorecard_version_id == VERSION
    assert checked.window.to_json()["basis"] == "meeting_created_at"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"params": {}}, ("params_invalid", "param_missing", "intent"), id="no-intent"),
        pytest.param(
            {"intent": "issue_count"}, ("params_invalid", "param_missing", "params"), id="no-params"
        ),
        pytest.param(
            {"intent": 3, "params": {}},
            ("params_invalid", "param_bad_type", "intent"),
            id="intent-type",
        ),
        pytest.param(
            {"intent": "issue_count", "params": []},
            ("params_invalid", "param_bad_type", "params"),
            id="params-type",
        ),
        pytest.param(
            {"intent": "issue_count", "params": {}, "family": "qa"},
            ("params_invalid", "param_unknown", "family"),
            id="filters-only-inside-params",
        ),
    ],
)
def test_issue_aggregate_defects(params, expected):
    assert defect("issue_aggregate", params) == expected


def test_issue_aggregate_keeps_any_intent_text_for_the_routing():
    checked = check_params("issue_aggregate", {"intent": "forecast", "params": {"a": 1}}, now=NOW)
    assert checked == IssueAggregateParams(intent="forecast", params={"a": 1})


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({}, ("params_invalid", "param_missing", "family"), id="missing"),
        pytest.param(
            {"family": ["call_reason"]},
            ("params_invalid", "param_bad_type", "family"),
            id="A7-list",
        ),
        pytest.param(
            {"family": "nope"}, ("params_invalid", "param_bad_value", "family"), id="unknown"
        ),
        pytest.param(
            {"family": "call_reason", "limit": True},
            ("params_invalid", "param_bad_type", "limit"),
            id="limit-bool",
        ),
        pytest.param(
            {"family": "call_reason", "limit": "5"},
            ("params_invalid", "param_bad_type", "limit"),
            id="limit-text",
        ),
        pytest.param(
            {"family": "call_reason", "limit": 0},
            ("params_invalid", "param_bad_value", "limit"),
            id="limit-0",
        ),
        pytest.param(
            {"family": "call_reason", "limit": 11},
            ("params_invalid", "param_bad_value", "limit"),
            id="limit-11",
        ),
        pytest.param(
            {"family": "call_reason", "source_type": "fax"},
            ("params_invalid", "filter_bad_value", "source_type"),
            id="source",
        ),
        pytest.param(
            {"limit": 0, "language": "x"},
            ("params_invalid", "filter_bad_value", "language"),
            id="filter-before-own-params",
        ),
        pytest.param(
            {"family": "call_reason", "issue": "complaint_refund"},
            ("params_invalid", "param_unknown", "issue"),
            id="no-issue-selector",
        ),
    ],
)
def test_top_issues_defects(params, expected):
    assert defect("top_issues", params) == expected


def test_top_issues_defaults():
    checked = check_params("top_issues", {"family": "qa", "language": "FA"}, now=NOW)
    assert checked == TopIssuesParams(family="qa", limit=5, filters={"language": "FA"})
    assert check_params("top_issues", {"family": "call_reason", "limit": 10}, now=NOW).limit == 10


def test_params_must_be_a_mapping_with_text_keys():
    assert defect("conversation_count", ["a"]) == ("params_invalid", "params_not_mapping", None)
    assert defect("conversation_count", {1: "a"}) == ("params_invalid", "params_not_mapping", None)


def test_check_params_programming_errors():
    with pytest.raises(ValueError):
        check_params("conversation_count", {}, now=datetime(2026, 10, 5))
    with pytest.raises(ValueError):
        check_params("sql", {}, now=NOW)
