"""HOM-82 (WP15-T04): the pure disclosure rules of the role-scoped trend views.

No app and no database: the conversations are real judgments of ``judge_conversation`` with
an agent, a team and an instant, or ``WeekStats`` built by hand.  The rules are in
``services/issue_trend_service.py`` (section "disclosure") and the query rules of the views
in ``services/role_trend_service.py``; the README of ``backend/issue_trends`` explains them
("HOM-82: role views and disclosure").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import chain, combinations
from typing import Any

import pytest

from backend.issue_trends import CellKind, TrendFilterError
from backend.issue_trends.samples import MIN_COHORT_AGENTS
from backend.services.issue_trend_service import (
    LAYER_POINTS,
    LAYER_WINDOW,
    REASON_COMPLEMENTARY,
    REASON_SMALL_GROUP,
    JudgedConversation,
    SeriesPoint,
    WeekStats,
    apply_masks,
    canonical_release,
    capped,
    cohort_kind,
    cohort_masks,
    cohort_stats,
    organization_release,
    point_finder,
    remainder_unsafe,
    team_parts,
    week_passes,
    week_stats,
    weekly_release,
)
from backend.services.role_trend_service import (
    ComparedTeam,
    TrendViewError,
    check_view_keys,
    compared_teams,
    current_week_start,
    is_week_boundary,
    organization_query,
    team_comparisons,
    workspace_query,
)
from backend.tests.test_issue_trends_counting import judge, ok, stored

COMPLAINT = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"), outcome=ok("escalated"))
ABSTAINED = stored(intent="insufficient_evidence", topic="insufficient_evidence")
START = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
VIEWER = "viewer"

_ids = iter(range(1, 1_000_000))


def conversation(
    team: str | None,
    agent: str | None,
    *,
    at: datetime = START,
    extraction: Any = COMPLAINT,
    excluded: bool = False,
) -> JudgedConversation:
    number = next(_ids)
    judgment = judge(
        extraction,
        meeting_id=f"m{number}",
        ingestion_id=f"i{number}",
        media_group_id=f"g{number}",
        is_counted_copy=not excluded,
        **({"extraction_run_id": None} if extraction is None else {}),
    )
    return JudgedConversation(at, team, agent, "fa", "file_upload", judgment)


def calls(team: str | None, agents: list[str | None], each: int, **options: Any):
    return [conversation(team, agent, **options) for agent in agents for _ in range(each)]


def people(prefix: str, count: int) -> list[str | None]:
    return [f"{prefix}{n}" for n in range(1, count + 1)]


def release_of(judged, *, points=None):
    point_of = point_finder(points) if points else None
    parts, rest = team_parts(judged, VIEWER, n_points=len(points or []), point_of=point_of)
    return parts, rest, canonical_release(parts, rest, has_points=bool(points))


def remainder(parts, rest, released: set[str]):
    built = rest
    for team_id, part in parts.items():
        if team_id not in released:
            built = built.unite(part.others)
    return built


# --- 4.1: the kind comes from the data (closes L1) -----------------------------------------

KIND_CASES = [
    # (agents of the cohort, kind, all-or-nothing applies)
    (set(), CellKind.RATE, False),  # no agent is in the numbers
    ({VIEWER}, CellKind.AGENT, False),  # only the viewer's own calls
    ({"a1"}, CellKind.AGENT, True),  # one other agent
    (set(people("a", 4)), CellKind.AGENT, True),  # 4 others: still a small group
    (set(people("a", 5)), CellKind.RATE, False),  # 5 others: not small
    ({VIEWER, *people("a", 4)}, CellKind.AGENT, True),  # the viewer knows their own calls
    ({VIEWER, *people("a", 5)}, CellKind.RATE, False),
]


@pytest.mark.parametrize(("agents", "kind", "gated"), KIND_CASES)
def test_the_kind_comes_from_the_agents_of_the_cohort(agents, kind, gated):
    assert cohort_kind(frozenset(agents), VIEWER) == (kind, gated)


def test_the_self_view_is_always_an_agent_cohort_without_extra_rules():
    assert cohort_kind(frozenset(people("a", 7)), VIEWER, self_view=True) == (
        CellKind.AGENT,
        False,
    )


def test_a_phantom_agent_filter_value_changes_nothing():
    # L1: a second agent id that owns no meeting does not change the conversations, so it
    # does not change the kind either.
    one_agent = calls("t", ["a1"], 4)
    stats = cohort_stats(one_agent)
    assert stats.agents == frozenset({"a1"})
    assert cohort_kind(stats.agents, VIEWER) == (CellKind.AGENT, True)
    assert MIN_COHORT_AGENTS == 5


# --- 4.1: all or nothing per layer ---------------------------------------------------------


def _masks(judged, *, points=None):
    point_of = point_finder(points) if points else None
    options = {"n_points": len(points or []), "point_of": point_of}
    everyone = cohort_stats(judged, **options)
    others = cohort_stats(judged, without_agent=VIEWER, **options)
    return cohort_masks(everyone, others, VIEWER, has_points=bool(points))


TWO_POINTS = [
    SeriesPoint(START, START + timedelta(days=1), False),
    SeriesPoint(START + timedelta(days=1), START + timedelta(days=2), False),
]


def test_one_row_under_the_minimum_withholds_the_whole_window_layer():
    small = calls("t", ["a1", "a2"], 10)  # 20 conversations, every row 20 < 30
    assert _masks(small) == frozenset({LAYER_WINDOW})
    assert _masks(small, points=TWO_POINTS) == frozenset({LAYER_WINDOW, LAYER_POINTS})
    # allow-control: every row at least 30, no point: nothing withheld
    assert _masks(calls("t", ["a1", "a2"], 15)) == frozenset()


def test_the_points_layer_is_decided_on_its_own():
    later = START + timedelta(days=1, hours=1)
    judged = [*calls("t", ["a1", "a2"], 16), *calls("t", ["a1"], 2, at=later)]  # 32 + 2
    assert _masks(judged, points=TWO_POINTS) == frozenset({LAYER_POINTS})


def test_the_viewer_cohort_is_exempt_and_the_viewer_part_is_tested():
    # only the viewer's own calls: no extra rule, even under 30
    assert _masks(calls("t", [VIEWER], 3)) == frozenset()
    # the viewer and one other agent: 40 calls in all, but the other's part is 10 < 30
    mixed = [*calls("t", [VIEWER], 30), *calls("t", ["a1"], 10)]
    assert _masks(mixed) == frozenset({LAYER_WINDOW})
    # allow-control: the other's part is 30
    assert _masks([*calls("t", [VIEWER], 30), *calls("t", ["a1"], 30)]) == frozenset()


def test_masking_keeps_denominators_and_hidden_cells():
    rows = [
        {"cell": {"status": "computed", "numerator": 3, "denominator": 40, "ratio": 0.075,
                  "reason": None, "minimum_sample": 30},
         "pending_review": 1, "absent_pending_review": 0, "missing": {"x": 2}},
        {"cell": {"status": "insufficient_sample", "numerator": None, "denominator": 9,
                  "ratio": None, "reason": "below_minimum_sample", "minimum_sample": 30},
         "pending_review": None, "absent_pending_review": None, "missing": {}},
        {"cell": {"status": "not_computable", "numerator": None, "denominator": 40,
                  "ratio": None, "reason": "scorecard_results_unavailable",
                  "minimum_sample": 30},
         "pending_review": None, "absent_pending_review": None, "missing": {}},
    ]  # fmt: skip
    masked, points = apply_masks(rows, [], frozenset({LAYER_WINDOW}))
    assert masked[0]["cell"] == {
        "status": "withheld",
        "numerator": None,
        "denominator": 40,
        "ratio": None,
        "reason": REASON_COMPLEMENTARY,
        "minimum_sample": 30,
    }
    assert masked[0]["pending_review"] is None and masked[0]["missing"] == {"x": 2}
    assert masked[1] == rows[1] and masked[2] == rows[2] and points == []


# --- 4.2: one canonical released-team set (closes L2) --------------------------------------


def test_the_workspace_total_minus_a_team_never_isolates_one_agent():
    # worked example 1: T has 5 agents; A works alone with no team (12 calls)
    judged = [*calls("t", people("b", 5), 40), *calls(None, ["a"], 12)]
    parts, rest, release = release_of(judged)
    assert release.teams == {"t"} and release.window == frozenset()
    # allow-control: with A on 30 calls the remainder holds no hidden cell
    judged = [*calls("t", people("b", 5), 40), *calls(None, ["a"], 30)]
    assert release_of(judged)[2].window == {"t"}


def test_the_review_attack_on_two_teams_is_closed():
    # worked example 1b: T1 and T2 with 5 agents and 100 calls each, n1 alone with 12 calls
    judged = [
        *calls("t1", people("p", 5), 20),
        *calls("t2", people("q", 5), 20),
        *calls(None, ["n1"], 12),
    ]
    parts, rest, release = release_of(judged)
    assert release.window == {"t2"}  # a tie at 100: the smaller id moves first
    assert not remainder_unsafe(remainder(parts, rest, {"t2"}), LAYER_WINDOW)
    # every request gets the same answer for each team
    for requested in ({"t1"}, {"t2"}, {"t1", "t2"}):
        assert (requested <= release.window) == (requested == {"t2"}), requested


def test_the_team_with_the_fewest_conversations_moves_first():
    judged = [
        *calls("big", people("p", 5), 30),
        *calls("small", people("q", 5), 7),
        *calls(None, ["n1"], 12),
    ]
    assert release_of(judged)[2].window == {"big"}


SAFE_REMAINDERS = [
    ("only the viewer's own calls", calls(None, [VIEWER], 3)),
    ("calls with no agent", calls(None, [None], 3)),
    ("a small group with every cell at least 30", calls(None, ["n1"], 30)),
    ("five other agents", calls(None, people("n", 5), 1)),
]


@pytest.mark.parametrize(("_label", "rest"), SAFE_REMAINDERS)
def test_a_safe_remainder_withholds_no_team(_label, rest):
    judged = [*calls("t1", people("p", 5), 20), *rest]
    assert release_of(judged)[2].window == {"t1"}


def test_the_points_pass_withholds_points_only():
    later = START + timedelta(days=1, hours=1)
    judged = [
        *calls("t1", people("p", 5), 20),
        *calls(None, ["n1"], 25),  # 25 calls: the window cell is under 30 ...
        *calls(None, ["n1"], 5, at=later),  # ... 30 in all, but the second point has 5
    ]
    _parts, _rest, release = release_of(judged, points=TWO_POINTS)
    assert release.window == {"t1"} and release.points == frozenset()


def _subsets(items):
    items = sorted(items)
    return chain.from_iterable(combinations(items, size) for size in range(len(items) + 1))


def test_every_subset_of_teams_as_the_request():
    later = START + timedelta(days=1, hours=1)
    judged = [
        *calls("t1", [*people("p", 5), VIEWER], 20),  # the viewer is an agent of t1
        *calls("t2", people("q", 5), 20),
        *calls("t3", ["r1", "r2"], 20),  # small, every cell 40: shown as an agent cohort
        *calls("t4", ["s1"], 10),  # small and under 30: withheld by 4.1
        *calls("t5", ["u1", "u2"], 18),
        *calls("t5", ["u1"], 4, at=later),  # small, a point under 30: points withheld
        *calls(None, ["n1"], 12),
        *calls(None, [None], 3),
    ]
    for points in (None, TWO_POINTS):
        parts, rest, release = release_of(judged, points=points)
        teams = set(parts)
        for requested in _subsets(teams):
            requested = set(requested)
            # /trends/teams releases a team only from the canonical set
            assert {t for t in requested if t in release.window} <= release.window
            # /trends/issues?team_id= is released only if every requested team is in it
            released_union = requested <= release.window
            assert released_union == all(t in release.window for t in requested)
        for layer, released in ((LAYER_WINDOW, release.window), (LAYER_POINTS, release.points)):
            if layer == LAYER_POINTS and not points:
                continue
            canonical = remainder(parts, rest, set(released))
            assert not remainder_unsafe(canonical, layer), (points, layer)
            for kept in _subsets(released):
                # S minus any union of released teams minus own: never unsafe, and always
                # the canonical remainder plus whole released teams
                built = remainder(parts, rest, set(kept))
                assert not remainder_unsafe(built, layer), (points, layer, kept)
                expected = canonical
                for team_id in set(released) - set(kept):
                    expected = expected.unite(parts[team_id].others)
                assert built.included == expected.included
                assert built.agents == expected.agents
        # t4 (withheld by 4.1) and n1 make a small remainder; t3 and t5 tie at 40 calls, so
        # t3 (the smaller id) moves into it, and 4 agents with 65 calls are then safe
        assert release.window == {"t1", "t2", "t5"}, points


# --- 4.3: one decision per workspace and week ----------------------------------------------


def week(*groups: tuple[str | None, list[str]]) -> dict[str | None, WeekStats]:
    """One week: per team id (``None``: no team), its distinct agents, one call each."""
    rows = [("family", "call_reason"), ("issue", "intent_report_dissatisfaction")]
    return {
        team: WeekStats(len(agents), capped(agents), {row: capped(agents) for row in rows})
        for team, agents in groups
    }


B2_WEEKS = [
    week(("t1", people("a", 6)), ("t2", people("b", 2)), (None, people("c", 5))),
    week(("t1", people("a", 6)), ("t2", people("b", 6)), (None, ["n1"])),
    week(("t1", people("a", 2)), ("t2", people("b", 6)), (None, people("c", 5))),
]


def test_the_week_test_counts_agents_and_rows():
    assert week_passes(week(("t", people("a", 5)))["t"])
    assert not week_passes(week(("t", people("a", 4)))["t"])
    assert week_passes(WeekStats(0, frozenset(), {}))  # an empty week passes
    assert week_passes(WeekStats(7, frozenset(), {}))  # calls with no agent pass
    # M2 (worked example 5): five agents, but every row's denominator holds only a1
    one_row_agent = WeekStats(
        60, capped(people("a", 5)), {("family", "call_reason"): frozenset({"a1"})}
    )
    assert not week_passes(one_row_agent)
    # the capped sets still unite correctly
    assert len(capped(people("a", 9))) == MIN_COHORT_AGENTS
    united = week(("x", people("a", 3)))["x"].unite(week(("y", ["b1", "b2"]))["y"])
    assert week_passes(united)


def test_week_stats_test_every_row_whatever_the_selectors():
    judged = [
        *[conversation("t", agent) for agent in people("a", 5)],
        *[conversation("t", agent, extraction=ABSTAINED) for agent in people("b", 3)],
    ]
    stats = week_stats(judged)
    assert len(stats.agents) == MIN_COHORT_AGENTS
    assert len(stats.rows[("family", "call_reason")]) == MIN_COHORT_AGENTS
    # no extraction row: every row is missing, so no row has a denominator
    unanalysed = week_stats([conversation("t", a, extraction=None) for a in people("b", 3)])
    assert unanalysed.rows == {} and not week_passes(unanalysed)  # 3 agents still fail
    assert len(unanalysed.agents) == 3


def test_the_per_week_decision_closes_the_three_week_attack():
    rel = weekly_release(B2_WEEKS)
    assert rel == [frozenset({"t1"}), frozenset(), frozenset({"t2"})]

    def block(team_ids, first, last):
        return organization_release(B2_WEEKS[first : last + 1], rel[first : last + 1], team_ids)

    assert block(frozenset({"t1"}), 0, 0) == (True, None)
    assert block(frozenset({"t1"}), 0, 1) == (False, REASON_COMPLEMENTARY)
    assert block(frozenset({"t2"}), 1, 2) == (False, REASON_COMPLEMENTARY)
    assert block(frozenset({"t2"}), 2, 2) == (True, None)
    assert block(None, 1, 1) == (True, None)  # W week 2 alone: 13 agents
    assert block(frozenset({"t1"}), 2, 2) == (False, REASON_SMALL_GROUP)

    # In general: a block released for a window is released in each of its weeks, and in
    # each week the workspace minus any part of Rel_k still passes the week test.
    for first in range(3):
        for last in range(first, 3):
            for team_ids in (None, frozenset({"t1"}), frozenset({"t2"})):
                if not block(team_ids, first, last)[0]:
                    continue
                for k in range(first, last + 1):
                    assert block(team_ids, k, k)[0], (team_ids, first, last, k)
    for parts, released in zip(B2_WEEKS, rel, strict=True):
        whole = WeekStats(0, frozenset(), {})
        for stats in parts.values():
            whole = whole.unite(stats)
        if not week_passes(whole):
            continue
        for kept in _subsets(released):
            left = WeekStats(0, frozenset(), {})
            for team_id, stats in parts.items():
                if team_id not in kept:
                    left = left.unite(stats)
            assert not left.included or week_passes(left), kept


def test_team_activity_never_changes_the_decision():
    # A1 (worked example 6): T1 and T2 with 6 agents each, n1 alone with no team
    weeks = [week(("t1", people("a", 6)), ("t2", people("b", 6)), (None, ["n1"]))]
    rel = weekly_release(weeks)
    assert rel == [frozenset()]
    for active in ({"t1", "t2"}, {"t1"}, {"t2"}, set()):
        shown = {t: organization_release(weeks, rel, frozenset({t})) for t in sorted(active)}
        assert all(answer == (False, REASON_COMPLEMENTARY) for answer in shown.values()), active
        assert organization_release(weeks, rel, None) == (True, None)


def test_a_team_grant_and_the_whole_workspace_come_from_one_decision():
    # M1: a team grant (W, A) and a whole grant on the same data, in either order
    unsafe = [week(("a", people("a", 6)), ("b", people("b", 5)), (None, ["n1"]))]
    rel = weekly_release(unsafe)
    assert organization_release(unsafe, rel, frozenset({"a"})) == (False, REASON_COMPLEMENTARY)
    assert organization_release(unsafe, rel, None) == (True, None)
    safe = [week(("a", people("a", 6)), ("b", people("b", 5)), (None, people("n", 5)))]
    rel = weekly_release(safe)
    assert organization_release(safe, rel, frozenset({"a"})) == (True, None)
    assert organization_release(safe, rel, None) == (True, None)
    rest = safe[0]["b"].unite(safe[0][None])
    assert week_passes(rest)  # W minus A is the rest plus B: it passes


def test_an_empty_team_week_passes():
    weeks = [
        week(("t1", people("a", 6)), (None, people("c", 5))),
        week((None, people("c", 5))),  # t1 has no call this week
    ]
    assert organization_release(weeks, weekly_release(weeks), frozenset({"t1"})) == (True, None)


# --- 5: comparisons -------------------------------------------------------------------------


def _row(numerator, denominator, *, family="call_reason", issue=None, status="computed",
         reason=None):  # fmt: skip
    return {
        "level": "family" if issue is None else "issue",
        "family": family,
        "issue": issue,
        "cell": {"status": status, "numerator": numerator, "denominator": denominator,
                 "ratio": None, "reason": reason, "minimum_sample": 30},
    }  # fmt: skip


def test_comparisons_are_pairs_in_a_fixed_order_and_never_a_rank():
    qa = _row(None, 60, family="qa", status="not_computable",
              reason="scorecard_results_unavailable")  # fmt: skip
    a = ComparedTeam("a", CellKind.RATE, False, [_row(30, 60), _row(5, 40), qa])
    b = ComparedTeam("b", CellKind.RATE, False, [_row(10, 50), _row(1, 60), qa])
    small = ComparedTeam("c", CellKind.AGENT, False, [_row(9, 60), _row(1, 60), qa])
    masked = ComparedTeam("d", CellKind.RATE, True, [_row(None, 60, status="withheld")] * 2 + [qa])
    result = team_comparisons([a, b, small, masked])
    assert [(c["left_team_id"], c["right_team_id"]) for c in result[::3]] == [
        ("a", "b"), ("a", "c"), ("a", "d"), ("b", "c"), ("b", "d"), ("c", "d")
    ]  # fmt: skip
    first = result[0]
    assert first["status"] == "computed" and first["difference"] == 0.3  # 1/2 - 1/5
    assert first["minimum_sample"] == 50
    assert result[1]["status"] == "insufficient_sample" and result[1]["difference"] is None
    assert result[2]["status"] == "not_computable"
    assert result[2]["reason"] == "scorecard_results_unavailable"
    assert {result[3]["reason"], result[4]["reason"]} == {REASON_SMALL_GROUP}
    assert result[6]["reason"] == REASON_COMPLEMENTARY
    for comparison in result:
        assert not {"rank", "score", "best", "worst"} & set(comparison)


def test_compared_teams_check_the_count_then_the_scope():
    comparable = [(f"t{n}", f"Team {n}") for n in range(12)]
    with pytest.raises(TrendViewError) as many:
        compared_teams(comparable, [])
    assert (many.value.status_code, many.value.code, many.value.key) == (
        422, "trend_too_many_teams", "team_id"
    )  # fmt: skip
    with pytest.raises(TrendViewError) as foreign:
        compared_teams(comparable[:3], ["t0", "elsewhere"])
    assert (foreign.value.status_code, foreign.value.code) == (403, "team_scope_denied")
    with pytest.raises(TrendViewError) as none:
        compared_teams([], [])
    assert (none.value.code, none.value.key) == ("team_scope_denied", None)
    # allow-control and the fixed order: name case-folded, then id
    assert compared_teams([("2", "beta"), ("1", "Alpha")], []) == [("1", "Alpha"), ("2", "beta")]


# --- 4.3 step 1: the organization grid ------------------------------------------------------

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # a Wednesday
WEEK_START = current_week_start(NOW)
W = timedelta(days=7)


def test_the_current_week_starts_on_saturday_in_tehran():
    assert WEEK_START == datetime(2026, 10, 16, 20, 30, tzinfo=UTC)  # Sat 00:00 +03:30
    assert is_week_boundary(WEEK_START) and not is_week_boundary(WEEK_START + timedelta(hours=1))
    assert current_week_start(WEEK_START) == WEEK_START


def _window(raw):
    filters, grain = organization_query(raw, now=NOW)
    return filters.window.start, filters.window.end, grain


def test_the_grid_window_defaults():
    assert _window({}) == (WEEK_START - 4 * W, WEEK_START, "none")
    assert _window({"from": (WEEK_START - 2 * W).isoformat()})[:2] == (
        WEEK_START - 2 * W, WEEK_START
    )  # fmt: skip
    assert _window({"to": (WEEK_START - W).isoformat(), "grain": "week"}) == (
        WEEK_START - 5 * W, WEEK_START - W, "week"
    )  # fmt: skip
    assert _window({"from": (WEEK_START - 52 * W).isoformat(), "to": WEEK_START.isoformat()})[
        :2
    ] == (WEEK_START - 52 * W, WEEK_START)


ORGANIZATION_REFUSALS = [
    ({"from": (WEEK_START - 2 * W + timedelta(hours=1)).isoformat()},
     ("trend_window_not_aligned", "from")),
    ({"to": (WEEK_START - timedelta(hours=1)).isoformat()}, ("trend_window_not_aligned", "to")),
    ({"to": (WEEK_START + W).isoformat()}, ("trend_window_not_aligned", "to")),
    ({"from": (WEEK_START - 53 * W).isoformat(), "to": WEEK_START.isoformat()},
     ("filter_window_invalid", None)),
    ({"from": WEEK_START.isoformat()}, ("filter_window_invalid", None)),
    ({"grain": "day"}, ("trend_grain_invalid", "grain")),
    ({"grain": "month"}, ("trend_grain_invalid", "grain")),
    ({"team_id": "xyz"}, ("trend_parameter_not_allowed", "team_id")),
    ({"agent_user_id": "xyz"}, ("trend_parameter_not_allowed", "agent_user_id")),
    ({"language": "fa"}, ("trend_parameter_not_allowed", "language")),
    ({"source_type": "api"}, ("trend_parameter_not_allowed", "source_type")),
    ({"timezone": "UTC"}, ("trend_parameter_not_allowed", "timezone")),
    ({"team_id": "xyz", "workspace_id": "w"}, ("filter_scope_not_allowed", "workspace_id")),
    ({"team_id": "xyz", "product": "x"}, ("trend_parameter_not_allowed", "team_id")),
    ({"product": "x"}, ("filter_unsupported", "product")),
    ({"from": ["2026-01-03T00:00:00+03:30", "2026-01-10T00:00:00+03:30"]},
     ("filter_bad_type", "from")),
]  # fmt: skip


@pytest.mark.parametrize(("raw", "expected"), ORGANIZATION_REFUSALS)
def test_organization_query_refusals(raw, expected):
    with pytest.raises((TrendViewError, TrendFilterError, ValueError)) as refused:
        organization_query(raw, now=NOW)
    error = refused.value
    code = error.code.value if isinstance(error, TrendFilterError) else error.code
    assert (code, error.key) == expected


WORKSPACE_REFUSALS = [
    ({"agent_user_id": "xyz"}, ("trend_parameter_not_allowed", "agent_user_id")),
    ({"agent_user_id": "xyz", "scope": "all"}, ("filter_scope_not_allowed", "scope")),
    ({"agent_user_id": "xyz", "foo": "1"}, ("trend_parameter_not_allowed", "agent_user_id")),
    ({"foo": "1"}, ("filter_unknown_key", "foo")),
    ({"grain": "hour"}, ("trend_grain_invalid", "grain")),
]


@pytest.mark.parametrize(("raw", "expected"), WORKSPACE_REFUSALS)
def test_workspace_view_query_refusals(raw, expected):
    with pytest.raises((TrendViewError, TrendFilterError, ValueError)) as refused:
        workspace_query(raw, now=NOW)
    error = refused.value
    code = error.code.value if isinstance(error, TrendFilterError) else error.code
    assert (code, error.key) == expected
    # allow-control: the same query without the refused keys
    check_view_keys({"team_id": "x"}, frozenset({"agent_user_id"}))
