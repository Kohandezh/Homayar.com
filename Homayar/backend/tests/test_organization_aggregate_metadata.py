"""HOM-299: coverage, scorecard and missing-data metadata on the organization aggregates.

``GET /api/v1/organizations/{id}/aggregates`` gives every item ``coverage``, ``scorecard``,
``missing_data`` and ``disclosure`` (ADR 0041).  The window ``W`` is the window the HOM-82
executive view would use for ``from=since`` and ``to=until``, or its default (decision 13).
Leader decision L2: the item metadata is the HOM-82 block of the same workspace, grant and
``W``, field by field, so the two routes never differ and a withheld block stays withheld.
The existing fields keep their values on every request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from backend.database.relational import get_session_factory
from backend.database.saas_models import Meeting, MeetingStatus, RecordScope, WorkspaceRole
from backend.database.tenant_context import (
    bind_organization_context,
    bind_tenant_context,
    bind_user_context,
)
from backend.issue_trends.samples import DISCLOSURE_VERSION, MIN_COHORT_AGENTS
from backend.security.context import CSRF_COOKIE
from backend.services.aggregate_metadata import scorecard_block
from backend.services.coverage_service import (
    EVALUATION_BUCKETS,
    PROCESSING_BUCKETS,
    CoverageWindow,
    compute_coverage,
)
from backend.services.role_trend_service import current_week_start
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_issue_trend_service import DAY, add_team, add_user, seed
from backend.tests.test_organizations import _grant, _persona
from backend.tests.test_role_trend_routes import (
    WEEK,
    _assign,
    _install_member,
    _org_workspace,
    _scope_grant,
    _set_membership_role,
    _set_record_scope,
    _statements,
)

METADATA_KEYS = ("coverage", "scorecard", "missing_data", "disclosure")
RELEASED = {"status": "released", "reason": None, "kind": "rate", "masked": []}


def _aggregates(org: str) -> str:
    return f"/api/v1/organizations/{org}/aggregates"


def _trends(org: str) -> str:
    return f"/api/v1/organizations/{org}/trends/issues"


def _span(start: datetime, weeks: int = 1) -> dict[str, str]:
    """``since`` and ``until`` of ``/aggregates`` for whole weeks from ``start``."""
    return {"since": start.isoformat(), "until": (start + weeks * WEEK).isoformat()}


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _window_of(block: dict[str, Any]) -> tuple[datetime, datetime, str]:
    return _instant(block["from"]), _instant(block["to"]), block["basis"]


def _meeting(
    place: Any,
    week: datetime,
    *,
    received_week: datetime | None = None,
    team_id: str | None = None,
    agent: str | None = None,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    created_by: str | None = None,
    deleted: bool = False,
    segments: int | None = 2,
    run_mock: bool = False,
) -> str:
    """A ledger row and its meeting, created one day into ``week`` and received one day into
    ``received_week`` (default: the same week)."""
    seeded = seed(
        place,
        received_at=(received_week or week) + DAY,
        team_id=team_id,
        agent_user_id=agent,
        status=status,
        created_by=created_by,
        deleted=deleted,
        segments=segments,
        run_mock=run_mock,
    )
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == seeded.meeting_id).values(created_at=week + DAY)
        )
        db.commit()
    return seeded.meeting_id


def _meetings(place: Any, week: datetime, team_id: str | None, agents: list[str], **options):
    return [_meeting(place, week, team_id=team_id, agent=agent, **options) for agent in agents]


def _agents(count: int, prefix: str) -> list[str]:
    return [add_user(f"{prefix}{n}") for n in range(count)]


def _get(persona, org: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    response = persona.get(_aggregates(org), params=params or {})
    assert response.status_code == 200, response.text
    return response.json()


def _item(answer: dict[str, Any], place: Any) -> dict[str, Any]:
    return next(i for i in answer["items"] if i["workspace_id"] == place.workspace_id)


def _released(item: dict[str, Any]) -> tuple[str, str | None]:
    return item["disclosure"]["status"], item["disclosure"]["reason"]


def _metadata(block: dict[str, Any]) -> dict[str, Any]:
    return {key: block[key] for key in METADATA_KEYS}


def _same_as_hom82(persona, org: str, place: Any, params: dict[str, str]) -> dict[str, Any]:
    """The ``/aggregates`` item of ``place``, after checking that its metadata equals the
    HOM-82 block of the same caller, workspace and window ``W`` field by field (L2)."""
    answer = _get(persona, org, params)
    item = _item(answer, place)
    window = answer["coverage_window"]
    trends = persona.get(_trends(org), params={"from": window["from"], "to": window["to"]})
    assert trends.status_code == 200, trends.text
    assert trends.json()["window"] == window
    block = next(w for w in trends.json()["workspaces"] if w["workspace_id"] == place.workspace_id)
    assert block["team_ids"] == item["team_ids"]
    assert _metadata(item) == _metadata(block), (item["disclosure"], block["disclosure"])
    if item["coverage"] is None:
        assert item["missing_data"] is None and item["disclosure"]["status"] == "withheld"
    else:
        _assert_counters(item)
    assert item["scorecard"] == scorecard_block()
    return item


def _assert_counters(item: dict[str, Any]) -> None:
    """AC4 and AC5 on a released item: the ``coverage/1`` rules of the HOM-82 block."""
    coverage = item["coverage"]
    assert coverage["version"] == "coverage/1"
    assert coverage["basis"] == ("workspace" if item["team_ids"] is None else "trend_population")
    assert coverage["window"]["basis"] == "ingestion_received_at"
    processing, evaluation = coverage["processing"], coverage["evaluation"]
    assert list(processing["buckets"]) == list(PROCESSING_BUCKETS)
    assert sum(processing["buckets"].values()) == processing["denominator"]
    assert evaluation["denominator"] == processing["buckets"]["transcribed"]
    assert list(evaluation["buckets"]) == list(EVALUATION_BUCKETS)
    assert sum(evaluation["buckets"].values()) == evaluation["denominator"]
    codes = item["missing_data"]["indicators"]
    assert codes == sorted(set(codes)) and "scorecard_unavailable" in codes
    assert item["disclosure"] == RELEASED


def _closed_weeks() -> tuple[datetime, list[datetime]]:
    week_start = current_week_start(datetime.now(UTC))
    return week_start, [week_start - n * WEEK for n in (3, 2, 1)]


# --- AC3, AC4, AC5: the released item ------------------------------------------------------------


def test_released_item_metadata_is_the_hom82_block(client, email_outbox):
    owner = _persona(client, email_outbox, email="am-owner@example.com", name="Owner")
    org = owner.organization_id
    place = _org_workspace(owner, org, "Calls")
    _, (_, week, _) = _closed_weeks()
    agents = _agents(5, "am")
    _meetings(place, week, None, agents)  # 5 included calls
    _meeting(place, week, agent=agents[0], status=MeetingStatus.FAILED)
    _meeting(place, week, agent=agents[1], status=MeetingStatus.QUEUED)
    _meeting(place, week, agent=agents[2], segments=None)  # completed, no transcript
    _meeting(place, week, agent=agents[3], deleted=True)
    seed(place, received_at=datetime.now(UTC), agent_user_id=agents[4])  # this week, not in W

    answer = _get(owner, org, _span(week))
    item = _same_as_hom82(owner, org, place, _span(week))
    # the existing fields, as on base (live meetings by created_at)
    assert item["meetings_total"] == 8
    assert item["meetings_by_status"] == {"completed": 6, "failed": 1, "queued": 1}
    assert item["audio_seconds_total"] == 240 and item["action_items_total"] == 0
    assert answer["since"] is not None and answer["until"] is not None
    # the HOM-82 counters: every ledger row of the window, a soft-deleted meeting included
    assert item["coverage"]["basis"] == "workspace"
    assert item["coverage"]["processing"]["buckets"] == {
        "deleted": 1,
        "in_progress": 1,
        "failed": 1,
        "transcribed": 5,
        "no_transcript": 1,
    }
    assert _window_of(item["coverage"]["window"]) == (week, week + WEEK, "ingestion_received_at")
    assert answer["coverage_window"] == item["coverage"]["window"]
    assert answer["disclosure_policy"]["version"] == DISCLOSURE_VERSION
    assert answer["disclosure_policy"]["min_cohort_agents"] == MIN_COHORT_AGENTS
    assert answer["disclosure_policy"]["grid"]["week_starts_on"] == "saturday"

    # AC5: an empty item (the owner's personal workspace) is released with empty_population
    personal = type("Place", (), {"workspace_id": owner.personal_workspace_id})()
    empty = _same_as_hom82(owner, org, personal, _span(week))
    assert empty["meetings_total"] == 0
    assert "empty_population" in empty["missing_data"]["indicators"]
    assert empty["coverage"]["processing"]["reason"] == "empty_denominator"


# --- L2: the R7 fixture of the final review ------------------------------------------------------


def test_a_meeting_received_in_another_week_is_counted_as_hom82_counts_it(client, email_outbox):
    """Week k has 5 agents.  Agent F has one completed call with a mock analysis, received
    in week k-2 (where F is alone) and created in week k.  ``/aggregates`` must not show F's
    call in week k: its metadata is the HOM-82 block, which puts the call in week k-2."""
    owner = _persona(client, email_outbox, email="r7-owner@example.com", name="Owner")
    org = owner.organization_id
    place = _org_workspace(owner, org, "R7")
    _, (two_before, _, week) = _closed_weeks()
    _meetings(place, week, None, _agents(5, "r7"))
    _meeting(place, week, received_week=two_before, agent=add_user("r7f"), run_mock=True)

    # the counters first: subtracting one route from the other must leave nothing
    item = _item(_get(owner, org, _span(week)), place)
    trend = owner.get(_trends(org), params={"from": week.isoformat(), "to": _span(week)["until"]})
    block = next(w for w in trend.json()["workspaces"] if w["workspace_id"] == place.workspace_id)
    for name in ("processing", "evaluation"):
        assert item["coverage"][name]["buckets"] == block["coverage"][name]["buckets"], name
    item = _same_as_hom82(owner, org, place, _span(week))
    assert item["disclosure"] == RELEASED
    assert item["coverage"]["evaluation"]["buckets"]["mock_analysis"] == 0
    assert item["coverage"]["processing"]["denominator"] == 5
    assert item["meetings_total"] == 6  # the existing field, by created_at, as on base
    # weeks k-2 to k: F alone in week k-2, so both routes withhold the block
    item = _same_as_hom82(owner, org, place, _span(two_before, 3))
    assert _released(item) == ("withheld", "small_group")
    assert item["meetings_total"] == 6


# --- deny-cases: small groups, grants, restricted memberships, deleted meetings ------------------


def test_every_item_metadata_equals_the_hom82_block(client, email_outbox):
    owner = _persona(client, email_outbox, email="as-owner@example.com", name="Owner")
    narrow = _persona(client, email_outbox, email="as-narrow@example.com", name="Narrow")
    analyst = _persona(client, email_outbox, email="as-analyst@example.com", name="Analyst")
    executive = _persona(client, email_outbox, email="as-exec@example.com", name="Exec")
    org = owner.organization_id
    _, (_, week, _) = _closed_weeks()
    window = _span(week)
    memberships = {
        name: _grant(owner, org, person, role)
        for name, person, role in (
            ("narrow", narrow, "analyst"),
            ("analyst", analyst, "analyst"),
            ("executive", executive, "executive"),
        )
    }

    # 4 agents: withheld; the existing fields keep their values
    small = _org_workspace(owner, org, "Small")
    _meetings(small, week, None, _agents(4, "as"))
    item = _same_as_hom82(owner, org, small, window)
    assert _released(item) == ("withheld", "small_group")
    assert item["meetings_total"] == 4 and item["meetings_by_status"] == {"completed": 4}
    # allow-control: a fifth agent releases it with full counts
    _meeting(small, week, agent=add_user("as4"))
    item = _same_as_hom82(owner, org, small, window)
    assert item["coverage"]["processing"]["denominator"] == item["meetings_total"] == 5

    # five agents have meetings, but only two have completed calls: withheld
    mixed = _org_workspace(owner, org, "Mixed")
    agents = _agents(5, "mx")
    _meetings(mixed, week, None, agents[:2])
    _meetings(mixed, week, None, agents[2:], status=MeetingStatus.PROCESSING)
    item = _same_as_hom82(owner, org, mixed, window)
    assert _released(item) == ("withheld", "small_group")
    assert item["meetings_by_status"] == {"completed": 2, "processing": 3}
    _meetings(mixed, week, None, agents[2:])  # allow-control
    assert _released(_same_as_hom82(owner, org, mixed, window)) == ("released", None)

    # a team grant: a meeting of a team outside the grant changes no number
    grant = _org_workspace(owner, org, "Grant")
    t, u = add_team(grant, "T"), add_team(grant, "U")
    t_agents, u_agents = _agents(5, "agt"), _agents(5, "agu")
    _meetings(grant, week, t, t_agents)
    _meetings(grant, week, u, u_agents)
    _scope_grant(owner, org, memberships["executive"], grant, t)
    before = _same_as_hom82(executive, org, grant, window)
    assert before["team_ids"] == [t] and before["coverage"]["processing"]["denominator"] == 5
    whole = _same_as_hom82(owner, org, grant, window)["coverage"]["processing"]["denominator"]
    _meeting(grant, week, team_id=u, agent=u_agents[0])
    after = _same_as_hom82(executive, org, grant, window)
    assert _metadata(after) == _metadata(before) and after["meetings_total"] == 5
    owner_after = _same_as_hom82(owner, org, grant, window)
    assert owner_after["coverage"]["processing"]["denominator"] == whole + 1  # allow-control
    # a soft-deleted meeting of the granted team: in the deleted bucket, as HOM-82 counts it,
    # and never in meetings_total
    _meeting(grant, week, team_id=t, agent=t_agents[1], deleted=True)
    after = _same_as_hom82(executive, org, grant, window)
    assert after["coverage"]["processing"]["buckets"]["deleted"] == 1
    assert after["meetings_total"] == 5
    _meeting(grant, week, team_id=t, agent=t_agents[1])  # a live one is counted
    after = _same_as_hom82(executive, org, grant, window)
    assert after["coverage"]["processing"]["buckets"]["transcribed"] == 6
    assert after["meetings_total"] == 6

    # A6: Narrow holds a team grant on T and supervises K as a restricted member
    a6 = _org_workspace(owner, org, "A6")
    t, k = add_team(a6, "T"), add_team(a6, "K")
    _meetings(a6, week, t, _agents(5, "a6t"))
    _meetings(a6, week, k, _agents(5, "a6k"))
    others = _agents(5, "a6n")
    _meetings(a6, week, None, others[:4], status=MeetingStatus.QUEUED, created_by=narrow.user_id)
    _meeting(a6, week, agent=others[4], status=MeetingStatus.QUEUED)
    _scope_grant(owner, org, memberships["narrow"], a6, t)
    membership_id = _install_member(a6, narrow.user_id)
    _set_record_scope(a6, RecordScope.TEAM)
    _assign(a6, k, narrow.user_id, "supervisor", by=owner.user_id)
    assert _released(_same_as_hom82(narrow, org, a6, window)) == ("released", None)
    _set_membership_role(membership_id, WorkspaceRole.ADMIN)
    assert _released(_same_as_hom82(narrow, org, a6, window)) == ("released", None)

    # A6 withholds (the HOM-82 probe): team P has 4 agents, n1 has no team, the analyst
    # supervises P, so the block minus what it knows is n1 alone
    probe = _org_workspace(owner, org, "Probe")
    _scope_grant(owner, org, memberships["analyst"], probe)
    _scope_grant(owner, org, memberships["executive"], probe)
    p = add_team(probe, "P")
    _meetings(probe, week, p, _agents(4, "pp"))
    _meeting(probe, week, agent=add_user("pn1"))
    _install_member(probe, analyst.user_id)
    _set_record_scope(probe, RecordScope.TEAM)
    _assign(probe, p, analyst.user_id, "supervisor", by=owner.user_id)
    withheld = _same_as_hom82(analyst, org, probe, window)
    assert _released(withheld) == ("withheld", "complementary_disclosure")
    assert withheld["meetings_total"] == 5
    # allow-control: the executive has no workspace membership
    assert _released(_same_as_hom82(executive, org, probe, window)) == ("released", None)

    # an API key gets the same answer as on base
    key = _mint(
        client,
        {"X-CSRF-Token": owner.cookies[CSRF_COOKIE], "X-Workspace-ID": small.workspace_id},
        owner.cookies,
        scopes=["meeting:read"],
    )
    assert key.status_code == 201, key.text
    client.cookies.clear()
    refused = client.get(_aggregates(org), headers=_bearer(key.json()["key"]))
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "api_key_not_accepted"


@pytest.mark.postgres
def test_a_workspace_outside_the_scope_adds_no_count(client, email_outbox):
    owner = _persona(client, email_outbox, email="ao-owner@example.com", name="Owner")
    analyst = _persona(client, email_outbox, email="ao-analyst@example.com", name="Analyst")
    org = owner.organization_id
    _, (_, week, _) = _closed_weeks()
    window = _span(week)
    granted = _org_workspace(owner, org, "Granted")
    other = _org_workspace(owner, org, "Other")
    _scope_grant(owner, org, _grant(owner, org, analyst, "analyst"), granted)
    _meetings(granted, week, None, _agents(5, "aog"))
    _meetings(other, week, None, _agents(6, "aoo"))

    answer = _get(analyst, org, window)
    assert [i["workspace_id"] for i in answer["items"]] == [granted.workspace_id]
    item = _same_as_hom82(analyst, org, granted, window)
    assert item["coverage"]["processing"]["denominator"] == 5
    assert other.workspace_id not in str(answer)
    # allow-control: the owner sees both
    assert {i["workspace_id"] for i in _get(owner, org, window)["items"]} >= {
        granted.workspace_id,
        other.workspace_id,
    }

    # row-level security, not only the app filter: bound to the granted workspace, the
    # coverage of the other workspace reads nothing
    coverage_window = CoverageWindow(start=week, end=week + WEEK)

    def denominator(db, workspace_id: str) -> int:
        counters = compute_coverage(
            db, workspace_id=workspace_id, window=coverage_window, recompute_max_attempts=3
        ).counters
        return counters["processing"]["denominator"]

    with get_session_factory()() as db:
        bind_user_context(db, analyst.user_id)
        bind_organization_context(db, org)
        bind_tenant_context(db, workspace_id=granted.workspace_id, organization_id=org)
        assert denominator(db, other.workspace_id) == 0
        assert denominator(db, granted.workspace_id) == 5
        bind_tenant_context(db, workspace_id=other.workspace_id, organization_id=org)
        assert denominator(db, other.workspace_id) == 6
        db.rollback()


# --- decision 13: the window ---------------------------------------------------------------------


def test_the_window_follows_the_hom82_parser(client, email_outbox):
    owner = _persona(client, email_outbox, email="aw-owner@example.com", name="Owner")
    org = owner.organization_id
    place = _org_workspace(owner, org, "Window")
    week_start, (first, second, _) = _closed_weeks()
    agents = _agents(5, "aw")
    _meetings(place, first, None, agents)
    _meetings(place, second, None, agents)
    seed(place, received_at=datetime.now(UTC), agent_user_id=agents[0])  # this week
    default = (week_start - 4 * WEEK, week_start)

    def naive(value: datetime) -> str:
        return value.astimezone(UTC).replace(tzinfo=None).isoformat()

    after = second + WEEK
    # (label, params, meetings_total as on base, W)
    cases = [
        ("both aligned", {"since": first.isoformat(), "until": after.isoformat()}, 10,
         (first, after)),
        ("only since", {"since": first.isoformat()}, 11, (first, week_start)),
        ("only until", {"until": after.isoformat()}, 10, (after - 4 * WEEK, after)),
        ("no window", {}, 11, default),
        ("unaligned since",
         {"since": (first + timedelta(hours=1)).isoformat(), "until": after.isoformat()}, 10,
         default),
        ("naive bounds", {"since": naive(first), "until": naive(after)}, 10, default),
        ("53 weeks", {"since": (week_start - 53 * WEEK).isoformat(),
                      "until": week_start.isoformat()}, 10, default),
        ("since after until", {"since": second.isoformat(), "until": first.isoformat()}, 0,
         default),
        ("until in the open week", {"since": first.isoformat(),
                                    "until": (week_start + WEEK).isoformat()}, 11, default),
    ]  # fmt: skip
    for label, params, total, expected in cases:
        answer = _get(owner, org, params)
        item = _same_as_hom82(owner, org, place, params)
        assert item["meetings_total"] == total, label
        assert _window_of(answer["coverage_window"]) == (*expected, "ingestion_received_at"), label
        assert item["coverage"]["window"] == answer["coverage_window"], label
        assert item["coverage"]["processing"]["denominator"] == 10, label  # first and second


# --- D11: statements per workspace ---------------------------------------------------------------

# The statements one more scoped workspace adds to ``/aggregates`` (ADR 0041 decision 26).
# Base: 2 (status counts, action items).  HOM-299, always: the HOM-82 block's P and G and the
# caller's workspace membership, so 3.  A released block: plus the coverage ledger statement,
# and 8 fact statements when a meeting may be transcribed.  A restricted workspace membership
# (A6): plus the assignments and the known meetings.
BASE_PER_WORKSPACE = 2
METADATA_PER_WORKSPACE = 3
LEDGER_STATEMENT = 1
FACT_STATEMENTS = 8
A6_STATEMENTS = 2


def test_statements_per_workspace(client, email_outbox):
    owner = _persona(client, email_outbox, email="ac-owner@example.com", name="Owner")
    executive = _persona(client, email_outbox, email="ac-exec@example.com", name="Exec")
    org = owner.organization_id
    membership = _grant(owner, org, executive, "executive")
    _, (_, week, _) = _closed_weeks()

    def count() -> int:
        return _statements(lambda: executive.get(_aggregates(org), params=_span(week)))[0]

    def add(name: str, agents: int, failed: int = 0) -> Any:
        place = _org_workspace(owner, org, name)
        _meetings(place, week, None, _agents(agents, f"ac{name}"))
        _meetings(place, week, None, _agents(failed, f"af{name}"), status=MeetingStatus.FAILED)
        _scope_grant(owner, org, membership, place)
        return place

    add("Empty", 0)
    counts = [count()]
    add("One", 1)  # withheld: 1 agent
    counts.append(count())
    add("Twelve", 8, failed=4)  # released, 12 meetings
    counts.append(count())
    add("TwentyFour", 16, failed=8)  # released, 24 meetings
    counts.append(count())
    member = add("Member", 8, failed=4)  # released, the caller is a restricted member
    _install_member(member, executive.user_id)
    _set_record_scope(member, RecordScope.TEAM)
    counts.append(count())
    steps = [after - before for before, after in zip(counts, counts[1:], strict=False)]
    released = BASE_PER_WORKSPACE + METADATA_PER_WORKSPACE + LEDGER_STATEMENT + FACT_STATEMENTS
    assert steps == [
        BASE_PER_WORKSPACE + METADATA_PER_WORKSPACE,
        released,
        released,
        released + A6_STATEMENTS,
    ], (counts, steps)
