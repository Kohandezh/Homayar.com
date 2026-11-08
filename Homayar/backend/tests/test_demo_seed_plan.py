"""HOM-365: the demo seed's call plan, checked without a database.

The dating rule (SPEC rule 5 and rule 7) is a pure function of the meeting
index and the anchor week, so the two remaining AC6 instants (the last minute
of a week and a month boundary) are checked here instead of through the
endpoint: ``demo_seed._received_at`` and ``demo_seed._attribution`` decide
every ledger row's closed week. The plan-shape checks pin what the endpoint
test needs from the plan: 30 assessable calls per team and week, at least 5
distinct agents per team-week, at least 3 intents with a numerator every
week, ratios that move, and outcome abstentions that leave at least one issue
row under the sample minimum (AC4).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from backend.database import demo_seed
from backend.services.role_trend_service import current_week_start

# AC6: every instant is in the past, so no check here can expire.
INSTANTS = (
    datetime(2026, 10, 31, 0, 30, tzinfo=UTC),  # Saturday 04:00 Tehran
    datetime(2026, 11, 6, 20, 29, tzinfo=UTC),  # Friday 23:59 Tehran
    datetime(2026, 9, 30, 20, 30, tzinfo=UTC),  # 00:00 Tehran on 2026-10-01
)
TREND_TEAMS = (demo_seed.CALL_CENTER, demo_seed.CUSTOMER_CARE)


def _trend_meetings() -> tuple[tuple[str, str, int, object], ...]:
    return demo_seed._trend_meetings()


@pytest.mark.parametrize("instant", INSTANTS, ids=[instant.isoformat() for instant in INSTANTS])
def test_every_meeting_falls_in_its_planned_closed_week(instant):
    week_start = current_week_start(instant)
    specs = demo_seed._meeting_specs()
    assert len(specs) == 270  # 240 scripted calls + the 30 console-demo meetings
    for index, _outcome, _script, _title in specs:
        received = demo_seed._received_at(index, week_start)
        _team, _agent, weeks_back = demo_seed._attribution(index)
        assert 1 <= weeks_back <= 4
        week = week_start - timedelta(weeks=weeks_back)
        assert week <= received < week + timedelta(weeks=1), index
        assert week_start - timedelta(days=28) <= received < week_start, index


def test_the_call_plan_covers_whole_closed_weeks_only():
    # Saturday 04:00 Tehran: the anchor is that Saturday 00:00, and every call
    # sits in one of the 4 closed weeks before it.
    instant = INSTANTS[0]
    week_start = current_week_start(instant)
    assert week_start == instant.replace(hour=0, minute=0) - timedelta(hours=3, minutes=30)
    for team, _agent, week, _call in _trend_meetings():
        assert team in TREND_TEAMS
        assert week in (1, 2, 3, 4)


def test_each_team_has_30_calls_a_week_by_5_agents():
    meetings = _trend_meetings()
    counts: Counter[tuple[str, int]] = Counter()
    agents: dict[tuple[str, int], set[str]] = {}
    for team, agent, week, _call in meetings:
        counts[(team, week)] += 1
        agents.setdefault((team, week), set()).add(agent)
    assert set(counts) == {(team, week) for team in TREND_TEAMS for week in (1, 2, 3, 4)}
    assert set(counts.values()) == {30}
    for key, distinct in agents.items():
        assert len(distinct) >= 5, key


def test_trend_meetings_precede_the_console_plan():
    specs = demo_seed._meeting_specs()
    trend_count = len(_trend_meetings())
    assert len(specs) == trend_count + sum(count for _, count in demo_seed.MEETING_PLAN)
    outcomes = [outcome for _index, outcome, _script, _title in specs]
    assert outcomes[:trend_count] == ["completed"] * trend_count
    # The console plan of HOM-175 keeps its own statuses and attribution, now
    # at the tail indexes, so it is created last: the console's "first
    # completed meeting" (newest first) stays a scripted mock one (e2e).
    tail = Counter(outcomes[trend_count:])
    assert tail == {outcome: count for outcome, count in demo_seed.MEETING_PLAN}
    for index in range(trend_count, len(specs)):
        assert demo_seed._attribution(index) == demo_seed.MEETING_ATTRIBUTION[index - trend_count]


def _intent_counts(week: int) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _team, _agent, call_week, call in _trend_meetings():
        if call_week == week:
            counts[call.intent] += 1
    return counts


def test_at_least_three_intents_have_a_numerator_every_week():
    used = {call.intent for call in demo_seed.CALL_SCRIPTS.values()}
    for week in (1, 2, 3, 4):
        counts = _intent_counts(week)
        assert sum(counts.values()) == 60, week  # two teams, 30 calls each
        for intent in used:
            assert counts[intent] > 0, (week, intent)
        assert sum(1 for intent, count in counts.items() if count > 0) >= 3, week


def test_workspace_intent_ratios_move_across_the_weeks():
    ratios: dict[str, set[float]] = {}
    for week in (1, 2, 3, 4):
        counts = _intent_counts(week)
        for intent, count in counts.items():
            ratios.setdefault(intent, set()).add(round(count / 60, 4))
    moving = [intent for intent, values in ratios.items() if len(values) > 1]
    assert len(moving) >= 2, moving


def test_outcome_abstentions_leave_a_row_under_the_sample_minimum():
    for team in TREND_TEAMS:
        for week in (1, 2, 3, 4):
            abstained = sum(
                1
                for call_team, _agent, call_week, call in _trend_meetings()
                if (call_team, call_week) == (team, week) and call.outcome is None
            )
            # Every call states its intent, so the intent rows keep the full 30
            # (AC1), while the outcome rows sit under the minimum of 30 (AC4).
            assert 1 <= abstained <= 29, (team, week)


def test_every_call_script_declares_valid_labels():
    from backend.analysis_taxonomy import load_taxonomy

    taxonomy = load_taxonomy("conversation", 1)
    intents = {label.id for label in taxonomy.dimension("intent").labels}
    topics = {label.id for label in taxonomy.dimension("topic").labels}
    outcomes = {label.id for label in taxonomy.dimension("outcome").labels}
    for key, call in demo_seed.CALL_SCRIPTS.items():
        assert call.intent in intents, key
        assert call.topics, key
        for topic, _turn in call.topics:
            assert topic in topics, (key, topic)
        if call.outcome is not None:
            assert call.outcome in outcomes, key
        # Cited turns exist in the script (4 turns each).
        assert 0 <= call.intent_turn < len(call.turns), key
        assert 0 <= call.outcome_turn < len(call.turns), key
        for _topic, turn in call.topics:
            assert 0 <= turn < len(call.turns), key


def test_the_plan_blocks_match_the_expanded_meetings():
    planned = sum(count for _team, _week, _key, count in demo_seed.TREND_CALL_BLOCKS)
    assert planned == len(_trend_meetings()) == 240
    for team, week, key, count in demo_seed.TREND_CALL_BLOCKS:
        assert team in {name for name, _supervisor, _agents in demo_seed.TEAMS}
        assert key in demo_seed.CALL_SCRIPTS
        assert 1 <= week <= 4
        assert count >= 1
