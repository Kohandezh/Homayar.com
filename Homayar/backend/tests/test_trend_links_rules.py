"""HOM-83 (WP15-T05): the pure rules of the trend links, without the app.

* ``linkable``: only a ``computed`` cell (after the HOM-82 masks) gives links (SPEC A3).
* ``supporting_finding_indexes``: the kept, auto-accepted values that make a row hold, by
  their ``finding_index`` (the position over intent, topic, entity, outcome).
* The link parameters: ``trend_view``, the required window, ``team_id``, ``row``, ``point``,
  ``offset`` and ``limit``, each refusal with its code and key and its closest allowed case.

The routes are tested in ``test_trend_links_routes.py``.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from backend.issue_trends import judge_conversation, load_definition, validate_filters
from backend.services.issue_trend_service import TrendSeriesError, series_points, validate_series
from backend.services.role_trend_service import TrendViewError
from backend.services.trend_link_service import (
    LinkRow,
    linkable,
    parse_page,
    parse_point,
    parse_row,
    parse_team,
    require_window,
    split_link_query,
    supporting_finding_indexes,
)
from backend.tests.test_issue_trend_service import NOW, WINDOW
from backend.tests.test_issue_trends_counting import facts, ok, stored

DEFINITION = load_definition("trends", 1)
TEAM_A, TEAM_B = "7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e", "0c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
COMPLAINT = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"), outcome=ok("escalated"))


def cause_keys(value: Any, path: str = "") -> list[str]:
    """Every key of a JSON answer whose name holds ``cause`` (SPEC rule 8: no field says
    one trend or one conversation causes an issue).  The notice is a value, not a key."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if "cause" in str(key).lower():
                found.append(f"{path}.{key}")
            found.extend(cause_keys(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(cause_keys(item, f"{path}[{index}]"))
    return found


def _cell(status: str, numerator: int | None, denominator: int = 40, reason=None) -> dict:
    return {
        "status": status,
        "numerator": numerator,
        "denominator": denominator,
        "ratio": None,
        "reason": reason,
        "minimum_sample": 30,
    }


# --- linkable -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "cell", "expected"),
    [
        ("computed", _cell("computed", 12), True),
        ("computed, numerator 0", _cell("computed", 0), True),
        (
            "A3: rate cell under 30, numerator shown",
            _cell("insufficient_sample", 3, 12, "below_minimum_sample"),
            False,
        ),
        (
            "agent cell under 30, numerator hidden",
            _cell("insufficient_sample", None, 12, "below_minimum_sample"),
            False,
        ),
        ("withheld", _cell("withheld", None, 40, "complementary_disclosure"), False),
        ("empty denominator", _cell("not_computable", 0, 0, "empty_denominator"), False),
        (
            "the qa row",
            _cell("not_computable", None, 40, "scorecard_results_unavailable"),
            False,
        ),
    ],
)
def test_only_a_computed_cell_is_linkable(label, cell, expected):
    assert linkable(cell) is expected, label


# --- supporting finding indexes -------------------------------------------------------------


def _judged(result: dict[str, Any]):
    return judge_conversation(facts(result), DEFINITION)


def _value(label: str, disposition: str = "auto_accepted") -> dict[str, Any]:
    return {
        "label": label,
        "basis": "explicit",
        "source_turn_indexes": [0],
        "confidence": 0.9,
        "critical": False,
        "text": None,
        "disposition": disposition,
        "review_reason": None if disposition == "auto_accepted" else "critical_label",
    }


def _with_entity(result: dict[str, Any]) -> dict[str, Any]:
    """The same result with one entity value: the outcome value moves from index 2 to 3."""
    changed = copy.deepcopy(result)
    changed["fields"]["entity"]["values"] = [_value("order_number")]
    return changed


def _pending_pricing(result: dict[str, Any]) -> dict[str, Any]:
    """Topic ``pricing`` waiting for a person before the auto-accepted ``refund``, and a
    withheld topic value that holds no position."""
    changed = copy.deepcopy(result)
    topic = changed["fields"]["topic"]
    topic["values"] = [_value("pricing", "review_required"), _value("refund")]
    withheld = {key: value for key, value in _value("delivery").items()}
    withheld.pop("disposition")
    withheld.pop("review_reason")
    topic["withheld"] = [{**withheld, "reason": "below_threshold"}]
    topic["review_required"] = True  # some kept value waits for a person
    changed["needs_review"] = True
    return changed


def _foreign_label(result: dict[str, Any]) -> dict[str, Any]:
    """A topic value whose label is an intent label: it is never the intent reference."""
    changed = copy.deepcopy(result)
    changed["fields"]["topic"]["values"].append(_value("report_dissatisfaction"))
    return changed


ISSUE = "issue"
FAMILY = "family"


@pytest.mark.parametrize(
    ("label", "result", "judged_from", "row", "expected"),
    [
        (
            "one reference",
            COMPLAINT,
            COMPLAINT,
            (ISSUE, "call_reason", "intent_report_dissatisfaction"),
            {0},
        ),
        (
            "two references (AND)",
            COMPLAINT,
            COMPLAINT,
            (ISSUE, "service_complaint", "complaint_refund"),
            {0, 1},
        ),
        (
            "the outcome",
            COMPLAINT,
            COMPLAINT,
            (ISSUE, "unresolved_outcome", "outcome_escalated"),
            {2},
        ),
        (
            "a family: its counted issues",
            COMPLAINT,
            COMPLAINT,
            (FAMILY, "service_complaint", None),
            {0, 1},
        ),
        ("a family of one dimension", COMPLAINT, COMPLAINT, (FAMILY, "call_reason", None), {0}),
        (
            "an entity value moves the index",
            _with_entity(COMPLAINT),
            COMPLAINT,
            (ISSUE, "unresolved_outcome", "outcome_escalated"),
            {3},
        ),
        (
            "a pending value is not evidence",
            _pending_pricing(COMPLAINT),
            COMPLAINT,
            (ISSUE, "service_complaint", "complaint_refund"),
            {0, 2},
        ),
        (
            "a family skips a pending issue",
            _pending_pricing(COMPLAINT),
            _pending_pricing(COMPLAINT),
            (FAMILY, "service_complaint", None),
            {0, 2},
        ),
        (
            "a label of another dimension",
            _foreign_label(COMPLAINT),
            COMPLAINT,
            (ISSUE, "call_reason", "intent_report_dissatisfaction"),
            {0},
        ),
    ],
)
def test_supporting_finding_indexes(label, result, judged_from, row, expected):
    level, family, issue = row
    judgment = _judged(judged_from)
    found = supporting_finding_indexes(result, judgment, LinkRow(level, family, issue), DEFINITION)
    assert found == frozenset(expected), label


def test_the_family_case_is_judged_on_a_valid_row():
    """The pending pricing row above is a real stored shape: the judge reads it."""
    judgment = _judged(_pending_pricing(COMPLAINT))
    assert judgment.bucket is None
    assert judgment.issue("complaint_refund").state.value == "counted"
    assert judgment.issue("complaint_pricing").state.value == "pending_review"


# --- the link parameters --------------------------------------------------------------------


def _code(call) -> tuple[int, str, str | None]:
    try:
        call()
    except TrendViewError as exc:
        return exc.status_code, exc.code, exc.key
    except TrendSeriesError as exc:
        return 422, exc.code, exc.key
    return 200, "", None


def _filters(**raw: Any):
    raw.setdefault("from", WINDOW["from"])
    raw.setdefault("to", WINDOW["to"])
    return validate_filters(raw, DEFINITION, now=NOW)


def _points(grain: str):
    filters = _filters()
    return series_points(
        filters.window.start, filters.window.end, grain, validate_series(grain, "Asia/Tehran")
    )


DAY_POINTS = _points("day")
OK = (200, "", None)

PARAMETER_CASES = [
    # (label, call, expected)
    (
        "trend_view missing",
        lambda: split_link_query({"row": "family:qa"}),
        (422, "trend_view_invalid", "trend_view"),
    ),
    (
        "trend_view repeated",
        lambda: split_link_query({"trend_view": ["issues", "me"]}),
        (422, "trend_view_invalid", "trend_view"),
    ),
    (
        "trend_view unknown",
        lambda: split_link_query({"trend_view": "org"}),
        (422, "trend_view_invalid", "trend_view"),
    ),
    ("allow: trend_view issues", lambda: split_link_query({"trend_view": "issues"}), OK),
    (
        "A4: from missing",
        lambda: require_window({"to": WINDOW["to"]}),
        (422, "trend_window_required", "from"),
    ),
    (
        "A4: both missing, from first",
        lambda: require_window({}),
        (422, "trend_window_required", "from"),
    ),
    (
        "A4: to missing",
        lambda: require_window({"from": WINDOW["from"]}),
        (422, "trend_window_required", "to"),
    ),
    ("allow: both", lambda: require_window(dict(WINDOW)), OK),
    ("no team", lambda: parse_team(_filters()), (422, "trend_team_invalid", "team_id")),
    (
        "two teams",
        lambda: parse_team(_filters(team_id=[TEAM_A, TEAM_B])),
        (422, "trend_team_invalid", "team_id"),
    ),
    ("allow: one team", lambda: parse_team(_filters(team_id=TEAM_A)), OK),
    (
        "row missing",
        lambda: parse_row(None, _filters(), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "row repeated",
        lambda: parse_row(["family:qa", "family:qa"], _filters(), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "row without a level",
        lambda: parse_row("complaint_refund", _filters(), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "row of another level",
        lambda: parse_row("team:complaint_refund", _filters(), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "row unknown",
        lambda: parse_row("issue:nope", _filters(), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "row a family as an issue",
        lambda: parse_row("issue:call_reason", _filters(), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "row not selected",
        lambda: parse_row("family:qa", _filters(issue="complaint_refund"), DEFINITION),
        (422, "trend_row_invalid", "row"),
    ),
    (
        "allow: a selected issue",
        lambda: parse_row("issue:complaint_refund", _filters(issue="complaint_refund"), DEFINITION),
        OK,
    ),
    ("allow: the qa row", lambda: parse_row("family:qa", _filters(), DEFINITION), OK),
    ("point with grain none", lambda: parse_point("0", []), (422, "trend_point_invalid", "point")),
    (
        "point negative",
        lambda: parse_point("-1", DAY_POINTS),
        (422, "trend_point_invalid", "point"),
    ),
    (
        "point not a number",
        lambda: parse_point("x", DAY_POINTS),
        (422, "trend_point_invalid", "point"),
    ),
    (
        "point with a sign",
        lambda: parse_point("+0", DAY_POINTS),
        (422, "trend_point_invalid", "point"),
    ),
    (
        "point past the last",
        lambda: parse_point(str(len(DAY_POINTS)), DAY_POINTS),
        (422, "trend_point_invalid", "point"),
    ),
    (
        "point repeated",
        lambda: parse_point(["0", "0"], DAY_POINTS),
        (422, "trend_point_invalid", "point"),
    ),
    ("allow: the last point", lambda: parse_point(str(len(DAY_POINTS) - 1), DAY_POINTS), OK),
    ("allow: no point", lambda: parse_point(None, []), OK),
    ("offset negative", lambda: parse_page("-1", None), (422, "trend_page_invalid", "offset")),
    ("offset not a number", lambda: parse_page("x", None), (422, "trend_page_invalid", "offset")),
    (
        "offset too large",
        lambda: parse_page("1000000", None),
        (422, "trend_page_invalid", "offset"),
    ),
    ("limit 0", lambda: parse_page(None, "0"), (422, "trend_page_invalid", "limit")),
    ("limit 101", lambda: parse_page(None, "101"), (422, "trend_page_invalid", "limit")),
    ("limit repeated", lambda: parse_page(None, ["5", "5"]), (422, "trend_page_invalid", "limit")),
    ("offset before limit", lambda: parse_page("x", "0"), (422, "trend_page_invalid", "offset")),
    ("allow: limit 100, offset 999999", lambda: parse_page("999999", "100"), OK),
]


@pytest.mark.parametrize(
    ("label", "call", "expected"), PARAMETER_CASES, ids=[c[0] for c in PARAMETER_CASES]
)
def test_link_parameters(label, call, expected):
    assert _code(call) == expected, label


def test_the_parsed_values():
    assert DAY_POINTS, "the window holds at least one day point"
    assert split_link_query(
        {"trend_view": "teams", "row": "family:qa", "limit": "5", "from": "x"}
    ) == ("teams", {"row": "family:qa", "limit": "5"}, {"from": "x"})
    assert parse_row("issue:complaint_refund", _filters(), DEFINITION) == LinkRow(
        "issue", "service_complaint", "complaint_refund"
    )
    assert parse_row("family:qa", _filters(), DEFINITION).to_json() == {
        "level": "family",
        "family": "qa",
        "issue": None,
    }
    assert parse_point(None, DAY_POINTS) is None
    assert parse_point("0", DAY_POINTS) == 0
    assert parse_page(None, None) == (0, 50)
    assert parse_page("3", "7") == (3, 7)
    assert parse_team(_filters(team_id=TEAM_A)) == TEAM_A


def test_cause_keys_finds_a_key_and_ignores_the_notice():
    assert cause_keys({"notices": ["correlation_not_cause"], "rows": [{"cell": {}}]}) == []
    assert cause_keys({"rows": [{"root_cause": 1}]}) == [".rows[0].root_cause"]
