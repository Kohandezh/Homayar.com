"""HOM-320: the old ``/aggregates`` fields follow the HOM-82 small-group rule (ADR 0041 R1).

``meetings_total``, ``meetings_by_status``, ``audio_seconds_total`` and ``action_items_total``
of an item count exactly the HOM-82 population of the item (owner ruling B'): the live meetings
of the workspace, narrowed to the granted teams for a team grant, whose ledger row was received
in the item's closed-week window ``W`` (``coverage_window``).  ``created_at`` plays no part.
When the item's HOM-82 block is withheld, the four fields are ``null`` (the keys stay).

Every check goes through ``_checked``: the item metadata equals the workspace block of
``/trends/issues`` for the same caller and ``W`` (HOM-299, L2), the four fields are ``null``
exactly when that block is withheld, and a released item reconciles with the block's own
``coverage/1`` processing counter (AC7):

    meetings_total = processing.denominator - processing.buckets.deleted
    meetings_by_status, in progress statuses = processing.buckets.in_progress
    meetings_by_status, failed and cancelled = processing.buckets.failed
    meetings_by_status, completed and partial = transcribed + no_transcript

A rejected ledger row has no meeting, so the accepted rows with a live meeting are exactly the
counted meetings.  No test asserts a duration: "now" only picks the weeks.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update

from backend.database.saas_models import (
    ActionItem,
    Meeting,
    MeetingStatus,
    RecordScope,
    WorkspaceRole,
)
from backend.services.coverage_service import TRANSCRIBABLE_STATUSES
from backend.services.organization_service import workspace_aggregates
from backend.services.report_service import FAILED_STATUSES, IN_PROGRESS_STATUSES
from backend.tests._database import inspection_session_factory
from backend.tests.test_issue_trend_service import DAY, add_team, add_user, seed
from backend.tests.test_organization_aggregate_metadata import (
    _agents,
    _closed_weeks,
    _get,
    _instant,
    _item,
    _same_as_hom82,
    _span,
    _window_of,
)
from backend.tests.test_organizations import _grant, _persona
from backend.tests.test_role_trend_routes import (
    WEEK,
    _assign,
    _install_member,
    _org_workspace,
    _scope_grant,
    _set_membership_role,
    _set_record_scope,
)

OLD_FIELDS = ("meetings_total", "meetings_by_status", "audio_seconds_total", "action_items_total")
WITHHELD = (None, None, None, None)
MINUTE = timedelta(minutes=1)


def _old(item: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(item[name] for name in OLD_FIELDS)


def _completed(total: int, actions: int = 0) -> tuple[Any, ...]:
    """The old fields of ``total`` completed seeded calls (30 seconds each)."""
    return (total, {"completed": total} if total else {}, 30 * total, actions)


def _call(
    place: Any,
    *,
    received_at: datetime | None,
    created_at: datetime | None = None,
    agent: str | None = None,
    team_id: str | None = None,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    deleted: bool = False,
    action_items: int = 0,
) -> str:
    """A meeting created at ``created_at`` (default: ``received_at``) with ``action_items``
    action items, and its ledger row received at ``received_at``; ``None``: no ledger row."""
    with inspection_session_factory()() as db:
        if received_at is None:
            meeting = Meeting(
                workspace_id=place.workspace_id,
                created_by_user_id=place.owner_id,
                title="No ledger row",
                status=status,
                duration_seconds=30,
                team_id=team_id,
                agent_user_id=agent,
            )
            db.add(meeting)
            db.commit()
            meeting_id = meeting.id
        else:
            meeting_id = seed(
                place,
                received_at=received_at,
                team_id=team_id,
                agent_user_id=agent,
                status=status,
                deleted=deleted,
            ).meeting_id
        db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id)
            .values(created_at=created_at or received_at)
        )
        db.add_all(
            ActionItem(
                workspace_id=place.workspace_id,
                meeting_id=meeting_id,
                description="Call the customer back",
                created_by="user",
            )
            for _ in range(action_items)
        )
        db.commit()
    return meeting_id


def _calls(place: Any, at: datetime, agents: list[str], team_id: str | None = None) -> None:
    """One completed call per agent, received (and created) at ``at``, a minute apart."""
    for n, agent in enumerate(agents):
        _call(place, received_at=at + n * MINUTE, agent=agent, team_id=team_id)


def _reconciles(item: dict[str, Any]) -> None:
    """AC7: a released item's counts and the ``coverage/1`` processing counter of its block."""
    processing = item["coverage"]["processing"]
    buckets, by_status = processing["buckets"], item["meetings_by_status"]

    def among(statuses: Any) -> int:
        return sum(by_status.get(status.value, 0) for status in statuses)

    assert item["meetings_total"] == sum(by_status.values())
    assert item["meetings_total"] == processing["denominator"] - buckets["deleted"]
    assert among(IN_PROGRESS_STATUSES) == buckets["in_progress"]
    assert among(FAILED_STATUSES) == buckets["failed"]
    assert among(TRANSCRIBABLE_STATUSES) == buckets["transcribed"] + buckets["no_transcript"]


def _checked(persona, org: str, place: Any, params: dict[str, str] | None = None) -> dict:
    """The item of ``place`` (status 200, present), checked against the HOM-82 block."""
    item = _same_as_hom82(persona, org, place, params or {})
    assert set(OLD_FIELDS) <= set(item)
    if item["disclosure"]["status"] == "withheld":
        assert _old(item) == WITHHELD, item
    else:
        _reconciles(item)
    return item


def _reason(item: dict[str, Any]) -> tuple[str, str | None]:
    return item["disclosure"]["status"], item["disclosure"]["reason"]


# --- AC1, AC3: the week test decides --------------------------------------------------------------


def test_four_agents_in_one_week_withhold_the_old_fields_and_five_release_them(
    client, email_outbox
):
    owner = _persona(client, email_outbox, email="hw-owner@example.com", name="Owner")
    org = owner.organization_id
    _, (_, second, third) = _closed_weeks()
    window = _span(second, 2)  # W = the last two closed weeks
    agents = _agents(5, "hw")

    def populate(name: str, third_week_agents: list[str]) -> Any:
        place = _org_workspace(owner, org, name)
        _calls(place, second + DAY, agents)
        _calls(place, third + DAY, third_week_agents)
        _call(place, received_at=second + 2 * DAY, agent=agents[0], status=MeetingStatus.FAILED)
        _call(place, received_at=third + 2 * DAY, agent=agents[1], status=MeetingStatus.QUEUED)
        _call(place, received_at=third + 3 * DAY, agent=agents[2], action_items=2)
        return place

    # allow-control: 5 agents in every week of W, released with the B' counts
    five = populate("Five", agents)
    item = _checked(owner, org, five, window)
    assert _reason(item) == ("released", None)
    assert _old(item) == (13, {"completed": 11, "failed": 1, "queued": 1}, 390, 2)

    # the same data with 4 agents in the last week: withheld, the four keys null
    four = populate("Four", agents[:4])
    item = _checked(owner, org, four, window)
    assert _reason(item) == ("withheld", "small_group")
    assert _old(item) == WITHHELD

    # 1 agent: withheld too
    one = _org_workspace(owner, org, "One")
    _calls(one, second + DAY, agents[:1])
    _calls(one, third + DAY, agents[:1])
    item = _checked(owner, org, one, window)
    assert _reason(item) == ("withheld", "small_group")
    assert _old(item) == WITHHELD
    # the same answer with no window: W is the 4 closed weeks, still 1 agent in two of them
    assert _old(_checked(owner, org, one)) == WITHHELD
    assert _old(_checked(owner, org, five)) == _old(_checked(owner, org, five, window))


# --- AC2: complementary disclosure ----------------------------------------------------------------


def test_a_team_grant_whose_remainder_fails_withholds_the_old_fields(client, email_outbox):
    owner = _persona(client, email_outbox, email="hc-owner@example.com", name="Owner")
    executive = _persona(client, email_outbox, email="hc-exec@example.com", name="Exec")
    org = owner.organization_id
    membership = _grant(owner, org, executive, "executive")
    _, (_, _, week) = _closed_weeks()
    window = _span(week)

    split = _org_workspace(owner, org, "Split")
    t, u = add_team(split, "T"), add_team(split, "U")
    _calls(split, week + DAY, _agents(5, "hct"), t)
    _calls(split, week + DAY, _agents(4, "hcu"), u)
    _scope_grant(owner, org, membership, split, t)
    item = _checked(executive, org, split, window)
    assert item["team_ids"] == [t]
    assert _reason(item) == ("withheld", "complementary_disclosure")
    assert _old(item) == WITHHELD
    # allow-control: the same grant, the remainder now has 5 agents; T's calls only
    _call(split, received_at=week + 2 * DAY, agent=add_user("hcu4"), team_id=u)
    item = _checked(executive, org, split, window)
    assert _reason(item) == ("released", None)
    assert _old(item) == _completed(5)

    # allow-control: a remainder of 0 agents
    alone = _org_workspace(owner, org, "Alone")
    t = add_team(alone, "T")
    _calls(alone, week + DAY, _agents(5, "hca"), t)
    _scope_grant(owner, org, membership, alone, t)
    item = _checked(executive, org, alone, window)
    assert _reason(item) == ("released", None)
    assert _old(item) == _completed(5)


def test_a_restricted_workspace_membership_withholds_the_old_fields(client, email_outbox):
    """A6: team P has 4 agents, n1 has no team; the analyst supervises P as a restricted
    member, so the block minus what the analyst knows is n1 alone."""
    owner = _persona(client, email_outbox, email="hr-owner@example.com", name="Owner")
    analyst = _persona(client, email_outbox, email="hr-analyst@example.com", name="Analyst")
    org = owner.organization_id
    _, (_, _, week) = _closed_weeks()
    window = _span(week)
    probe = _org_workspace(owner, org, "Probe")
    _scope_grant(owner, org, _grant(owner, org, analyst, "analyst"), probe)
    p = add_team(probe, "P")
    _calls(probe, week + DAY, _agents(4, "hrp"), p)
    _call(probe, received_at=week + 2 * DAY, agent=add_user("hrn1"))
    membership_id = _install_member(probe, analyst.user_id)
    _set_record_scope(probe, RecordScope.TEAM)
    _assign(probe, p, analyst.user_id, "supervisor", by=owner.user_id)

    item = _checked(analyst, org, probe, window)
    assert _reason(item) == ("withheld", "complementary_disclosure")
    assert _old(item) == WITHHELD
    # allow-control: the same caller with an unrestricted membership
    _set_membership_role(membership_id, WorkspaceRole.ADMIN)
    item = _checked(analyst, org, probe, window)
    assert _reason(item) == ("released", None)
    assert _old(item) == _completed(5)


# --- AC4: the population ----------------------------------------------------------------------


def test_the_old_fields_count_the_hom82_population_of_the_window(client, email_outbox):
    """W is the last closed week.  Each deny-case adds a meeting that must not count, then its
    closest allow-control adds one that must."""
    owner = _persona(client, email_outbox, email="hp-owner@example.com", name="Owner")
    executive = _persona(client, email_outbox, email="hp-exec@example.com", name="Exec")
    org = owner.organization_id
    week_start, (_, _, week) = _closed_weeks()
    window = _span(week)
    place = _org_workspace(owner, org, "Population")
    t, u = add_team(place, "T"), add_team(place, "U")
    t_agents, u_agents = _agents(5, "hpt"), _agents(5, "hpu")
    _calls(place, week + DAY, t_agents, t)
    _calls(place, week + DAY, u_agents, u)
    _scope_grant(owner, org, _grant(owner, org, executive, "executive"), place, t)

    def counted(persona=executive) -> tuple[Any, ...]:
        item = _checked(persona, org, place, window)
        assert _reason(item) == ("released", None)
        return _old(item)

    assert counted() == _completed(5)
    assert counted(owner) == _completed(10)

    # received the minute before W.start (created in W): not counted
    _call(
        place,
        received_at=week - MINUTE,
        created_at=week + DAY,
        agent=t_agents[0],
        team_id=t,
        action_items=2,
    )
    assert counted() == _completed(5)
    # received at W.start (created before W): counted, with its action item
    _call(
        place,
        received_at=week,
        created_at=week - 2 * DAY,
        agent=t_agents[0],
        team_id=t,
        action_items=1,
    )
    assert counted() == _completed(6, actions=1)

    # received at W.end, the current week (created in W): not counted
    _call(place, received_at=week_start, created_at=week + DAY, agent=t_agents[1], team_id=t)
    assert counted() == _completed(6, actions=1)
    # received the minute before W.end: counted
    _call(place, received_at=week_start - MINUTE, agent=t_agents[1], team_id=t)
    assert counted() == _completed(7, actions=1)

    # soft-deleted, received in W: not counted; a live one, same week: counted
    _call(place, received_at=week + 3 * DAY, agent=t_agents[2], team_id=t, deleted=True)
    assert counted() == _completed(7, actions=1)
    _call(place, received_at=week + 3 * DAY, agent=t_agents[2], team_id=t)
    assert counted() == _completed(8, actions=1)

    # a meeting of the non-granted team U: not counted for the grant; one of T: counted
    _call(place, received_at=week + 4 * DAY, agent=u_agents[0], team_id=u)
    assert counted() == _completed(8, actions=1)
    _call(place, received_at=week + 4 * DAY, agent=t_agents[3], team_id=t)
    assert counted() == _completed(9, actions=1)

    # no ledger row (created in W): not counted; the same with a ledger row in W: counted
    _call(place, received_at=None, created_at=week + 5 * DAY, agent=t_agents[4], team_id=t)
    assert counted() == _completed(9, actions=1)
    _call(place, received_at=week + 5 * DAY, agent=t_agents[4], team_id=t)
    assert counted() == _completed(10, actions=1)

    # created now (this week) but received in W: counted
    _call(
        place,
        received_at=week + 6 * DAY,
        created_at=datetime.now(UTC),
        agent=t_agents[4],
        team_id=t,
    )
    assert counted() == _completed(11, actions=1)

    # the owner sees the whole workspace: U's two calls of the window too
    assert counted(owner) == _completed(17, actions=1)


# --- AC5: the current-week leak ---------------------------------------------------------------


def test_meetings_received_in_the_current_week_never_appear(client, email_outbox):
    owner = _persona(client, email_outbox, email="hl-owner@example.com", name="Owner")
    org = owner.organization_id
    week_start, (_, _, last) = _closed_weeks()
    now = datetime.now(UTC)
    solo = _org_workspace(owner, org, "Solo")
    _call(solo, received_at=week_start, agent=add_user("hls"), action_items=1)
    crowd = _org_workspace(owner, org, "Crowd")
    for n, agent in enumerate(_agents(6, "hlc")):
        _call(crowd, received_at=week_start + n * MINUTE, agent=agent)

    windows = [
        {},  # the default: the 4 closed weeks before this week
        _span(last),  # the last closed week
        {"since": week_start.isoformat()},  # refused (no closed week), the default
        {"since": (now - timedelta(hours=1)).isoformat(), "until": (now + MINUTE).isoformat()},
        {"since": week_start.isoformat(), "until": (week_start + WEEK).isoformat()},
    ]
    for params in windows:
        for place in (solo, crowd):
            item = _checked(owner, org, place, params)
            assert _reason(item) == ("released", None), params  # 0 agents in W
            assert _old(item) == _completed(0), params

    # allow-control: the same calls received in the last closed week are counted
    _call(solo, received_at=last + DAY, created_at=now, agent=add_user("hlx"))
    assert _old(_checked(owner, org, solo, _span(last))) == WITHHELD  # 1 agent in W
    for n, agent in enumerate(_agents(5, "hlw")):
        _call(crowd, received_at=last + DAY + n * MINUTE, created_at=now, agent=agent)
    assert _old(_checked(owner, org, crowd, _span(last))) == _completed(5)
    assert _old(_checked(owner, org, crowd)) == _completed(5)


# --- AC6: the window --------------------------------------------------------------------------


def test_the_counts_follow_the_closed_week_window_never_the_free_one(client, email_outbox):
    owner = _persona(client, email_outbox, email="hn-owner@example.com", name="Owner")
    org = owner.organization_id
    week_start, (first, second, third) = _closed_weeks()
    place = _org_workspace(owner, org, "Window")
    agents = _agents(5, "hn")
    for calls, week in ((1, first), (2, second), (3, third)):  # 5, 10 and 15 calls
        for n in range(calls):
            _calls(place, week + DAY + n * timedelta(hours=2), agents)
    _call(place, received_at=datetime.now(UTC) - MINUTE, agent=agents[0])  # this week
    default = (week_start - 4 * WEEK, week_start)
    hour = second + DAY  # the first calls of the second week, inside one hour

    def naive(value: datetime) -> str:
        return value.astimezone(UTC).replace(tzinfo=None).isoformat()

    def span(since: datetime | None, until: datetime | None) -> dict[str, str]:
        params = {"since": since, "until": until}
        return {key: value.isoformat() for key, value in params.items() if value is not None}

    # (label, params, W, meetings_total)
    cases = [
        ("both aligned", span(first, third), (first, third), 15),
        ("only since", span(second, None), (second, week_start), 25),
        ("only until", span(None, third), (third - 4 * WEEK, third), 15),
        ("no window", {}, default, 30),
        ("the whole second week", span(second, third), (second, third), 10),
        ("one hour", span(hour, hour + timedelta(hours=1)), default, 30),
        ("unaligned since", span(first + timedelta(hours=1), third), default, 30),
        ("naive bounds", {"since": naive(first), "until": naive(third)}, default, 30),
        ("53 weeks", span(week_start - 53 * WEEK, week_start), default, 30),
        ("since after until", span(third, first), default, 30),
        ("until in the open week", span(first, week_start + WEEK), default, 30),
        ("the current week", span(week_start, week_start + WEEK), default, 30),
    ]
    for label, params, expected, total in cases:
        answer = _get(owner, org, params)
        assert _window_of(answer["coverage_window"]) == (*expected, "ingestion_received_at"), label
        item = _checked(owner, org, place, params)
        assert _old(item) == _completed(total), label
        if "since" in params and "+" in params["since"]:  # LD3: the request is echoed
            assert _instant(answer["since"]) == datetime.fromisoformat(params["since"]), label
        if "until" in params and "+" in params["until"]:
            assert _instant(answer["until"]) == datetime.fromisoformat(params["until"]), label
        if not params:
            assert answer["since"] is None and answer["until"] is None
    item = _item(_get(owner, org, span(hour, hour + timedelta(hours=1))), place)
    assert item["meetings_total"] == 30  # never the 5 calls of that hour


# --- LD5: no count without the decision -------------------------------------------------------


def test_the_service_has_no_count_without_the_block():
    parameters = inspect.signature(workspace_aggregates).parameters
    assert parameters["trend_block"].default is inspect.Parameter.empty
    assert parameters["window"].default is inspect.Parameter.empty
    assert "since" not in parameters and "until" not in parameters
