"""HOM-96 (WP17-T05): ``run_tool``, the read-only analytical tools of the Copilot.

The check order of ``run_tool`` (most of it without a database), then each tool against a
fixed dataset with a hand-computed reference: mixed teams, agents and statuses, deleted
meetings, another workspace's rows and rows on the window edges.  Then the leak checks
(guessed ids, record scope, PostgreSQL RLS as the app role), the limits (rows, time
budget, statement timeout), determinism and the read-only statement check.

Ids are fixed (``fid``), so two runs, and a run on the same rows inserted in another
order, can be compared byte for byte.
"""

from __future__ import annotations

import copy
import hashlib
import json
import types
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.orm import Session

from backend.copilot_tools import DEFAULT_LIMITS, ToolLimits, ToolResult
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ActionItem,
    ActionStatus,
    ApiKey,
    Meeting,
    MeetingStatus,
    Membership,
    ScorecardResultRecord,
    ScorecardVersion,
    Team,
    TranscriptRevision,
    TranscriptSource,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.security.authorization import RecordAccess
from backend.security.context import Actor, WorkspaceContext
from backend.services import copilot_tool_service
from backend.services.copilot_tool_service import run_tool
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trend_service import BASE as TREND_BASE
from backend.tests.test_issue_trend_service import BILLING, COMPLAINT, add_user, seed
from backend.tests.test_issue_trends_counting import ok, stored

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
FROM = datetime(2026, 10, 1, tzinfo=UTC)
TO = datetime(2026, 10, 4, tzinfo=UTC)
WINDOW = {"from": FROM.isoformat(), "to": TO.isoformat()}
DUE_BEFORE = "2026-10-10T00:00:00+00:00"
SC_FROM = datetime(2026, 9, 1, tzinfo=UTC)
SC_WINDOW = {"from": SC_FROM.isoformat(), "to": (SC_FROM + timedelta(days=1)).isoformat()}
TREND_WINDOW = {"from": TREND_BASE.isoformat(), "to": (TREND_BASE + timedelta(days=1)).isoformat()}
RANDOM_ID = "3f1e2d3c-4b5a-4f6e-8d7c-6b5a4f3e2d1c"
# A dissatisfied caller whose topic the model could not tell: the complaint rows miss it.
UNKNOWN_TOPIC = stored(intent=ok("report_dissatisfaction"), topic="insufficient_evidence")
NS = uuid.UUID("5b0f1c9e-6a43-4d1e-9a7f-2f3c4d5e6f70")
TZ = "+03:30"


def fid(label: str) -> str:
    """A fixed id per label: the same rows get the same ids in every test and order."""
    return str(uuid.uuid5(NS, label))


T1, T2, TB = fid("team-1"), fid("team-2"), fid("team-b")
U1, U2, U3 = fid("user-1"), fid("user-2"), fid("user-3")
V1, V2, V3, VB = fid("version-1"), fid("version-2"), fid("version-3"), fid("version-b")


def m(label: str) -> str:
    return fid(f"meeting-{label}")


def a(label: str) -> str:
    return fid(f"action-{label}")


def r(label: str) -> str:
    return fid(f"result-{label}")


# --- the caller -----------------------------------------------------------------------------


def unrestricted(user_id: str) -> RecordAccess:
    return RecordAccess(
        user_id=user_id,
        unrestricted=True,
        supervised_team_ids=frozenset(),
        agent_team_ids=frozenset(),
    )


def restricted(user_id: str, *supervised: str) -> RecordAccess:
    return RecordAccess(
        user_id=user_id,
        unrestricted=False,
        supervised_team_ids=frozenset(supervised),
        agent_team_ids=frozenset(),
    )


def make_context(
    workspace_id: str,
    organization_id: str | None,
    access: RecordAccess,
    *,
    role: WorkspaceRole = WorkspaceRole.OWNER,
    scopes: list[str] | None = None,
) -> WorkspaceContext:
    """A caller as authorization builds it.  ``scopes``: an API-key actor with these scopes."""
    user = User(id=access.user_id, email="caller@example.com", display_name="caller")
    key = None if scopes is None else ApiKey(scopes=list(scopes))
    return WorkspaceContext(
        actor=Actor(
            user=user, session=None, token_source="api_key" if key else "cookie", api_key=key
        ),
        workspace=Workspace(
            id=workspace_id, organization_id=organization_id or fid("org"), name="w", slug="w"
        ),
        membership=Membership(workspace_id=workspace_id, user_id=access.user_id, role=role),
        record_access=access,
    )


def context_of(
    tenant: Tenant,
    access: RecordAccess | None = None,
    *,
    role: WorkspaceRole = WorkspaceRole.OWNER,
    scopes: list[str] | None = None,
) -> WorkspaceContext:
    return make_context(
        tenant.workspace_id,
        tenant.organization_id,
        access or unrestricted(tenant.owner_id),
        role=role,
        scopes=scopes,
    )


@contextmanager
def bound(tenant: Tenant, factory=None) -> Iterator[Session]:
    """A session bound to the tenant (the inspection role; ``factory`` for the app role)."""
    with (factory or inspection_session_factory())() as db:
        bind_user_context(db, tenant.owner_id)
        bind_tenant_context(
            db, workspace_id=tenant.workspace_id, organization_id=tenant.organization_id
        )
        yield db


def run(
    tenant: Tenant,
    name: object,
    params: object,
    access: RecordAccess | None = None,
    *,
    role: WorkspaceRole = WorkspaceRole.OWNER,
    scopes: list[str] | None = None,
    limits: ToolLimits = DEFAULT_LIMITS,
    now: datetime = NOW,
) -> dict[str, Any]:
    ctx = context_of(tenant, access, role=role, scopes=scopes)
    with bound(tenant) as db:
        result = run_tool(db, context=ctx, name=name, params=params, now=now, limits=limits)
        assert isinstance(result, ToolResult)
        return result.to_json()


def dumps(answer: dict[str, Any]) -> str:
    return json.dumps(answer, sort_keys=True)


def facts(answer: dict[str, Any]) -> list[tuple[str, Any, Any, list[str]]]:
    return [(f["name"], f["group"], f["value"], f["citations"]) for f in answer["facts"]]


def reason(answer: dict[str, Any]) -> tuple[str, str | None, str | None]:
    assert answer["status"] == "abstain", answer
    assert answer["filters"] is None and answer["facts"] == [] and answer["provenance"] == {}
    return answer["reason"], answer["detail"], answer["key"]


def same_but_echo(answers: list[dict[str, Any]], echoed: list[str]) -> None:
    """SPEC A11: the answers are byte-equal once the caller's own id in ``filters`` is
    replaced by one placeholder.  An abstention (filters null) is compared in full."""
    texts = []
    for answer, value in zip(answers, echoed, strict=True):
        data = copy.deepcopy(answer)
        if data["filters"] is not None:
            data["filters"] = json.loads(json.dumps(data["filters"]).replace(value, "<id>"))
        texts.append(dumps(data))
    assert len(set(texts)) == 1, texts


# --- the dataset ----------------------------------------------------------------------------


@dataclass
class World:
    a: Tenant
    b: Tenant


def _users(db: Session) -> None:
    for user_id, name in ((U1, "u1"), (U2, "u2"), (U3, "u3")):
        db.add(User(id=user_id, email=f"{name}-{user_id[:8]}@example.com", display_name=name))
    db.flush()


def _meeting(
    tenant: Tenant,
    label: str,
    created_at: datetime,
    *,
    team: str | None = None,
    agent: str | None = None,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    deleted: bool = False,
) -> Meeting:
    return Meeting(
        id=m(label),
        workspace_id=tenant.workspace_id,
        created_by_user_id=tenant.owner_id,
        title=label,
        status=status,
        duration_seconds=30,
        team_id=team,
        agent_user_id=agent,
        created_at=created_at,
        deleted_at=created_at if deleted else None,
    )


def _action(
    tenant: Tenant,
    label: str,
    meeting: str,
    due: datetime | None,
    status: ActionStatus,
    priority: str,
    assignee: str | None = None,
) -> ActionItem:
    return ActionItem(
        id=a(label),
        workspace_id=tenant.workspace_id,
        meeting_id=m(meeting),
        description=label,
        due_at=due,
        status=status,
        priority=priority,
        assignee_user_id=assignee,
    )


def _revision(tenant: Tenant, meeting: str) -> TranscriptRevision:
    return TranscriptRevision(
        id=fid(f"revision-{meeting}"),
        workspace_id=tenant.workspace_id,
        meeting_id=m(meeting),
        revision_number=1,
        source=TranscriptSource.ASR,
        reason="seed",
        language="fa",
        segment_count=2,
        is_mock=False,
        is_current=True,
    )


def _version(tenant: Tenant, version_id: str, key: str) -> ScorecardVersion:
    return ScorecardVersion(
        id=version_id,
        workspace_id=tenant.workspace_id,
        scorecard_key=key,
        version=1,
        scorecards_version="scorecards/1",
        document={},
        digest="0" * 64,
        reason="seed",
    )


# outcome, reason, applicable, earned
PASSED_90 = ("passed", None, 100, 90)
PASSED_75 = ("passed", None, 80, 60)
FAILED = ("failed", "below_threshold", 70, 20)
NOT_APPLICABLE = ("not_applicable", "no_applicable_criteria", 0, 0)


def _result(
    tenant: Tenant,
    label: str,
    meeting: str,
    version: str,
    score: tuple[str, str | None, int, int],
    created_at: datetime,
) -> ScorecardResultRecord:
    outcome, why, applicable, earned = score
    return ScorecardResultRecord(
        id=r(label),
        workspace_id=tenant.workspace_id,
        meeting_id=m(meeting),
        scorecard_version_id=version,
        transcript_revision_id=fid(f"revision-{meeting}"),
        scorecards_version="scorecards/1",
        checks_version="checks/1",
        definition_digest="0" * 64,
        input_digest=hashlib.sha256(label.encode()).hexdigest(),
        conversation_type="call",
        direction=None,
        outcome=outcome,
        reason=why,
        complete=True,
        pass_threshold=70,
        applicable_weight=applicable,
        earned_weight=earned,
        failed_weight=applicable - earned,
        insufficient_weight=0,
        check_run={},
        result={},
        created_at=created_at,
    )


def _rows(world: World) -> list[list[Any]]:
    """The dataset, one list per table in insert order (parents first)."""
    A, B = world.a, world.b
    h = timedelta(hours=1)
    teams = [
        Team(id=T1, workspace_id=A.workspace_id, name="t1", slug="t1"),
        Team(id=T2, workspace_id=A.workspace_id, name="t2", slug="t2"),
        Team(id=TB, workspace_id=B.workspace_id, name="tb", slug="tb"),
    ]
    meetings = [
        _meeting(A, "1", FROM, team=T1, agent=U1),  # created_at == from: counted
        _meeting(A, "2", FROM + 21 * h, team=T1, agent=U2, status=MeetingStatus.FAILED),
        _meeting(A, "3", FROM + 34 * h, team=T2, agent=U2),
        _meeting(A, "4", FROM + 70 * h, status=MeetingStatus.PROCESSING),
        _meeting(A, "5", TO),  # created_at == to: not counted
        _meeting(A, "6", FROM + 36 * h, team=T1, agent=U2, deleted=True),
        _meeting(A, "7", FROM - timedelta(seconds=1), team=T1, agent=U1),
        _meeting(B, "b1", FROM + 34 * h, team=TB),
        _meeting(B, "b2", FROM + 5 * h, team=TB),
    ]
    sc_meetings = [
        _meeting(A, f"s{i}", SC_FROM + h + timedelta(minutes=i), team=T1 if i % 2 == 0 else T2)
        for i in range(31)
    ]
    sc_meetings += [
        _meeting(A, "sd", SC_FROM + 3 * h, team=T1, deleted=True),
        _meeting(A, "so", SC_FROM - h, team=T1),
        _meeting(B, "sb", SC_FROM + h, team=TB),
    ]
    day = timedelta(days=1)
    actions = [
        _action(A, "1", "1", FROM + 4 * day, ActionStatus.OPEN, "high", U1),
        _action(A, "2", "2", FROM + 5 * day, ActionStatus.IN_PROGRESS, "medium", U2),
        _action(A, "3", "3", FROM + 9 * day, ActionStatus.OPEN, "medium"),  # due == before
        _action(A, "4", "3", None, ActionStatus.OPEN, "low"),
        _action(A, "5", "4", FROM + 3 * day, ActionStatus.DONE, "low"),
        _action(A, "6", "6", FROM + 4 * day, ActionStatus.OPEN, "high"),  # deleted meeting
        _action(A, "7", "4", FROM + 2 * day, ActionStatus.OPEN, "low"),
        _action(A, "8", "5", FROM + day, ActionStatus.OPEN, "medium", U1),
        _action(B, "b1", "b1", FROM + 4 * day, ActionStatus.OPEN, "high"),
    ]
    revisions = [_revision(A, f"s{i}") for i in range(31)]
    revisions += [_revision(A, "sd"), _revision(A, "so"), _revision(B, "sb")]
    versions = [
        _version(A, V1, "quality"),
        _version(A, V2, "boundary"),
        _version(A, V3, "nothing"),
        _version(B, VB, "quality"),
    ]
    at = SC_FROM + 5 * h
    scores = [PASSED_90] * 10 + [PASSED_75] * 10 + [FAILED] * 7 + [NOT_APPLICABLE] * 3
    results = [_result(A, f"v1-s{i}", f"s{i}", V1, score, at) for i, score in enumerate(scores)]
    results += [
        _result(A, "v1-s30-old", "s30", V1, ("failed", "below_threshold", 100, 10), at),
        _result(A, "v1-s30-new", "s30", V1, ("passed", None, 100, 100), at + h),
        _result(A, "v1-sd", "sd", V1, ("passed", None, 100, 100), at),
        _result(A, "v1-so", "so", V1, ("passed", None, 100, 100), at),
        _result(B, "vb-sb", "sb", VB, ("passed", None, 100, 100), at),
    ]
    results += [_result(A, f"v2-s{i}", f"s{i}", V2, PASSED_75, at) for i in range(30)]
    results += [_result(A, f"v3-s{i}", f"s{i}", V3, NOT_APPLICABLE, at) for i in range(30)]
    return [teams, meetings + sc_meetings, actions, revisions, versions, results]


def seed_world(world: World, *, reverse: bool = False) -> None:
    with inspection_session_factory()() as db:
        for table in _rows(world):
            for row in reversed(table) if reverse else table:
                db.add(row)
            db.flush()
        db.commit()


def clear_world(world: World) -> None:
    """Remove the dataset with bulk deletes (no ORM delete event), children first."""
    workspaces = [world.a.workspace_id, world.b.workspace_id]
    with inspection_session_factory()() as db:
        for model in (ScorecardResultRecord, ScorecardVersion, ActionItem, TranscriptRevision):
            db.execute(delete(model).where(model.workspace_id.in_(workspaces)))
        db.execute(delete(Meeting).where(Meeting.workspace_id.in_(workspaces)))
        db.execute(delete(Team).where(Team.workspace_id.in_(workspaces)))
        db.commit()


@pytest.fixture
def world(client, email_outbox) -> World:
    built = World(
        a=Tenant(client, email_outbox, "tools-a@example.com"),
        b=Tenant(client, email_outbox, "tools-b@example.com"),
    )
    with inspection_session_factory()() as db:
        _users(db)
        db.commit()
    seed_world(built)
    return built


def local(day: int, hour: int = 0, minute: int = 0) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00{TZ}"


# --- the check order, no database -----------------------------------------------------------

WS = fid("workspace")
OWNER = fid("owner")


@pytest.fixture
def offline() -> Iterator[Session]:
    """A session with no database, bound to ``WS``: steps 1 to 8 never run a query."""
    db = Session()
    bind_tenant_context(db, workspace_id=WS)
    yield db
    db.close()


def check(
    db: Session,
    name: object,
    params: object,
    *,
    scopes: list[str] | None = None,
    access: RecordAccess | None = None,
) -> dict[str, Any]:
    ctx = make_context(WS, None, access or unrestricted(OWNER), scopes=scopes)
    return run_tool(db, context=ctx, name=name, params=params, now=NOW).to_json()


def test_step_1_programming_errors_are_value_errors(offline):
    ctx = make_context(WS, None, unrestricted(OWNER))
    with pytest.raises(ValueError):
        run_tool(
            offline, context=ctx, name="conversation_count", params={}, now=datetime(2026, 1, 1)
        )
    with pytest.raises(ValueError):
        run_tool(offline, context=object(), name="conversation_count", params={}, now=NOW)  # type: ignore[arg-type]
    for limits in (ToolLimits(20001, 1, 1, 1), ToolLimits(1, 1, 1, 0), "x"):
        with pytest.raises(ValueError):
            run_tool(
                offline,
                context=ctx,
                name="conversation_count",
                params={},
                now=NOW,
                limits=limits,  # type: ignore[arg-type]
            )


def test_step_2_an_unbound_or_wrong_bound_session_is_a_value_error():
    ctx = make_context(WS, None, unrestricted(OWNER))
    unbound = Session()
    with pytest.raises(ValueError):
        run_tool(unbound, context=ctx, name="conversation_count", params={}, now=NOW)
    other = Session()
    bind_tenant_context(other, workspace_id=fid("other"))
    with pytest.raises(ValueError):
        run_tool(other, context=ctx, name="sql", params={}, now=NOW)


@pytest.mark.parametrize(
    ("name", "params", "expected", "tool"),
    [
        pytest.param("sql", {}, ("tool_unknown", None, None), None, id="unknown-name"),
        pytest.param(7, {}, ("tool_unknown", None, None), None, id="name-not-text"),
        pytest.param(
            "sql",
            "not a mapping",
            ("tool_unknown", None, None),
            None,
            id="unknown-beats-bad-params",
        ),
        pytest.param(
            "conversation_count",
            ["a"],
            ("params_invalid", "params_not_mapping", None),
            "conversation_count",
            id="not-a-mapping",
        ),
        pytest.param(
            "conversation_count",
            {"product": 1, "workspace_id": 2},
            ("scope_not_allowed", None, "workspace_id"),
            "conversation_count",
            id="scope-beats-unsupported",
        ),
        pytest.param(
            "issue_aggregate",
            {"scope": "all", "role": "owner"},
            ("scope_not_allowed", None, "role"),
            "issue_aggregate",
            id="scope-first-sorted",
        ),
        pytest.param(
            "conversation_count",
            {"aaa": 1, "sentiment": 2},
            ("unsupported_filter", None, "sentiment"),
            "conversation_count",
            id="unsupported-beats-unknown",
        ),
        pytest.param(
            "conversation_count",
            {"zzz": 1, "aaa": 2},
            ("params_invalid", "param_unknown", "aaa"),
            "conversation_count",
            id="unknown-key",
        ),
        pytest.param(
            "conversation_count",
            {"group_by": "agent_user_id"},
            ("individual_ranking_not_supported", None, "group_by"),
            "conversation_count",
            id="rank-agents",
        ),
        pytest.param(
            "action_items_due",
            {"due_before": DUE_BEFORE, "group_by": "assignee_user_id"},
            ("individual_ranking_not_supported", None, "group_by"),
            "action_items_due",
            id="rank-assignees",
        ),
    ],
)
def test_the_check_order(offline, name, params, expected, tool):
    answer = check(offline, name, params)
    assert reason(answer) == expected
    assert answer["tool"] == tool
    assert answer["scope"] == {"restricted": False}


def test_an_unknown_tool_name_is_never_echoed(offline):
    answer = check(offline, "drop_table_meetings", {})
    assert "drop_table" not in dumps(answer)


def test_permission_beats_bad_params_and_scope_keys(offline):
    """A9: an API key never carries scorecard:read."""
    key = ["meeting:read"]
    for params in ({"workspace_id": "x", "bogus": 1}, "not a mapping", {"sentiment": 1}):
        answer = check(offline, "scorecard_summary", params, scopes=key)
        assert reason(answer) == ("permission_denied", None, None)
    # the allow side of the same key: a tool it has the permission for checks its params
    answer = check(offline, "conversation_count", {"workspace_id": "x"}, scopes=key)
    assert reason(answer) == ("scope_not_allowed", None, "workspace_id")


def test_an_abstention_holds_no_param_value(offline):
    value_id = "0b6f0000-0000-4000-8000-000000000000"
    for params in (
        {"team_id": value_id.upper()},
        {"group_by": value_id},
        {"workspace_id": value_id},
        {value_id: 1},
    ):
        answer = check(offline, "conversation_count", params)
        assert answer["status"] == "abstain"
        assert value_id not in dumps(answer).lower() or answer["key"] == value_id


def test_scope_restricted_is_set_on_an_abstention(offline):
    answer = check(offline, "sql", {}, access=restricted(OWNER, T1))
    assert answer["scope"] == {"restricted": True}


# --- conversation_count ---------------------------------------------------------------------


def test_conversation_count_total_and_status_groups(world):
    answer = run(world.a, "conversation_count", {**WINDOW, "group_by": "status"})
    assert answer["status"] == "answered"
    assert answer["filters"] == {
        "window": {
            "from": "2026-10-01T00:00:00+00:00",
            "to": "2026-10-04T00:00:00+00:00",
            "basis": "meeting_created_at",
        },
        "team_id": [],
        "agent_user_id": [],
        "group_by": "status",
        "series": None,
    }
    # m1 (on from), m2, m3, m4; not m5 (on to), m6 (deleted), m7 (before), B's meetings
    assert facts(answer) == [
        ("conversations", None, 4, sorted([m("1"), m("2"), m("3"), m("4")])),
        ("conversations", {"status": "completed"}, 2, sorted([m("1"), m("3")])),
        ("conversations", {"status": "failed"}, 1, [m("2")]),
        ("conversations", {"status": "processing"}, 1, [m("4")]),
    ]
    first = answer["facts"][0]
    assert first["citations_total"] == 4 and first["citations_complete"] is True
    assert answer["provenance"] == {} and answer["scope"] == {"restricted": False}


def test_conversation_count_team_groups_put_null_first(world):
    answer = run(world.a, "conversation_count", {**WINDOW, "group_by": "team_id"})
    teams = [(None, [m("4")]), (T1, sorted([m("1"), m("2")])), (T2, [m("3")])]
    teams = [teams[0], *sorted(teams[1:], key=lambda item: item[0])]
    assert facts(answer)[1:] == [
        ("conversations", {"team_id": team}, len(ids), ids) for team, ids in teams
    ]


def test_conversation_count_by_day_has_every_point_with_zeros(world):
    answer = run(world.a, "conversation_count", {**WINDOW, "group_by": "day"})
    assert answer["filters"]["series"] == {"grain": "day", "timezone": "Asia/Tehran"}
    assert facts(answer)[1:] == [
        (
            "conversations",
            {"point": 0, "from": local(1, 3, 30), "to": local(2), "partial": True},
            1,
            [m("1")],
        ),
        (
            "conversations",
            {"point": 1, "from": local(2), "to": local(3), "partial": False},
            2,
            sorted([m("2"), m("3")]),
        ),
        ("conversations", {"point": 2, "from": local(3), "to": local(4), "partial": False}, 0, []),
        (
            "conversations",
            {"point": 3, "from": local(4), "to": local(4, 3, 30), "partial": True},
            1,
            [m("4")],
        ),
    ]


def test_conversation_count_by_week_and_month(world):
    week = run(world.a, "conversation_count", {**WINDOW, "group_by": "week"})
    # the Iranian week starts on Saturday: 2026-10-03 00:00 Tehran
    assert [(f["group"]["from"], f["value"]) for f in week["facts"][1:]] == [
        (local(1, 3, 30), 3),
        (local(3), 1),
    ]
    month = run(world.a, "conversation_count", {**WINDOW, "group_by": "month"})
    assert [(f["group"]["partial"], f["value"]) for f in month["facts"][1:]] == [(True, 4)]


def test_conversation_count_filters_narrow(world):
    team = run(world.a, "conversation_count", {**WINDOW, "team_id": [T1, T2]})
    assert team["facts"][0]["value"] == 3  # m4 has no team
    assert team["filters"]["team_id"] == sorted([T1, T2])
    agent = run(world.a, "conversation_count", {**WINDOW, "agent_user_id": U1})
    assert facts(agent) == [("conversations", None, 1, [m("1")])]


def test_conversation_count_defaults_to_30_days_before_now(world):
    answer = run(world.a, "conversation_count", {}, now=TO)
    assert answer["filters"]["window"]["from"] == "2026-09-04T00:00:00+00:00"
    # m1..m4 and m7 (2026-09-30); the scorecard meetings of 2026-09-01 are before it
    assert answer["facts"][0]["value"] == 5


def test_agent_filter_in_scope_answers_but_group_by_agent_abstains(world):
    """Deny case: group_by=agent_user_id; allow control: one agent as a filter."""
    denied = run(world.a, "conversation_count", {**WINDOW, "group_by": "agent_user_id"})
    assert reason(denied) == ("individual_ranking_not_supported", None, "group_by")
    allowed = run(world.a, "conversation_count", {**WINDOW, "agent_user_id": U1})
    assert allowed["status"] == "answered" and allowed["facts"][0]["value"] == 1


def test_a_scope_key_abstains_and_the_same_params_without_it_answer(world):
    denied = run(world.a, "conversation_count", {**WINDOW, "workspace_id": world.b.workspace_id})
    assert reason(denied) == ("scope_not_allowed", None, "workspace_id")
    assert world.b.workspace_id not in dumps(denied)
    allowed = run(world.a, "conversation_count", WINDOW)
    assert allowed["facts"][0]["value"] == 4


# --- action_items_due -----------------------------------------------------------------------


def test_action_items_due_total_overdue_and_status_groups(world):
    answer = run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "status"})
    assert answer["filters"] == {
        "window": {"from": None, "to": DUE_BEFORE, "basis": "action_item_due_at"},
        "team_id": [],
        "assignee_user_id": [],
        "status": ["in_progress", "open"],
        "group_by": "status",
    }
    # a1, a2, a7, a8.  Not: a3 (due == before), a4 (no due), a5 (done), a6 (deleted
    # meeting), B's item.  Overdue at NOW (10-05 12:00): a1, a7, a8.
    assert facts(answer) == [
        ("action_items_due", None, 4, sorted([a("1"), a("2"), a("7"), a("8")])),
        ("action_items_overdue", None, 3, sorted([a("1"), a("7"), a("8")])),
        ("action_items_due", {"status": "in_progress"}, 1, [a("2")]),
        ("action_items_due", {"status": "open"}, 3, sorted([a("1"), a("7"), a("8")])),
    ]


def test_action_items_due_priority_and_team_groups(world):
    priority = run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "priority"})
    assert facts(priority)[2:] == [
        ("action_items_due", {"priority": "high"}, 1, [a("1")]),
        ("action_items_due", {"priority": "low"}, 1, [a("7")]),
        ("action_items_due", {"priority": "medium"}, 2, sorted([a("2"), a("8")])),
    ]
    team = run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "team_id"})
    assert facts(team)[2:] == [
        ("action_items_due", {"team_id": None}, 2, sorted([a("7"), a("8")])),
        ("action_items_due", {"team_id": T1}, 2, sorted([a("1"), a("2")])),
    ]


def test_action_items_due_after_status_and_assignee_filters(world):
    after = run(
        world.a,
        "action_items_due",
        {"due_before": DUE_BEFORE, "due_after": "2026-10-03T03:30:00+03:30"},
    )
    assert after["filters"]["window"]["from"] == "2026-10-03T00:00:00+00:00"
    # a7 is due exactly at due_after: counted; a8 (10-02) is not
    assert facts(after)[0] == ("action_items_due", None, 3, sorted([a("1"), a("2"), a("7")]))
    status = run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "status": "in_progress"})
    assert facts(status)[:2] == [
        ("action_items_due", None, 1, [a("2")]),
        ("action_items_overdue", None, 0, []),
    ]
    assignee = run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "assignee_user_id": U1})
    assert facts(assignee)[0] == ("action_items_due", None, 2, sorted([a("1"), a("8")]))
    team = run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "team_id": T1})
    assert facts(team)[0] == ("action_items_due", None, 2, sorted([a("1"), a("2")]))


# --- scorecard_summary ----------------------------------------------------------------------


def _v1_chosen() -> list[str]:
    return sorted([r(f"v1-s{i}") for i in range(30)] + [r("v1-s30-new")])


def test_scorecard_summary_outcomes_and_exact_mean(world):
    answer = run(world.a, "scorecard_summary", {"scorecard_version_id": V1, **SC_WINDOW})
    assert answer["status"] == "answered"
    chosen = _v1_chosen()
    passed = sorted([r(f"v1-s{i}") for i in range(20)] + [r("v1-s30-new")])
    failed = sorted(r(f"v1-s{i}") for i in range(20, 27))
    not_applicable = sorted(r(f"v1-s{i}") for i in range(27, 30))
    scored = sorted(set(chosen) - set(not_applicable))
    top = 100
    assert [(f["name"], f["group"], f["value"], f["citations_total"]) for f in answer["facts"]] == [
        ("scorecard_results", None, 31, 31),
        ("scorecard_results", {"outcome": "failed"}, 7, 7),
        ("scorecard_results", {"outcome": "not_applicable"}, 3, 3),
        ("scorecard_results", {"outcome": "passed"}, 21, 21),
        # (10*90 + 10*75 + 7*200/7 + 100) / 28 = 1950/28 = 975/14 = 69.642857...
        ("mean_score_percent", None, 69.64, 28),
    ]
    assert answer["facts"][0]["citations"] == chosen[:top]
    assert answer["facts"][1]["citations"] == failed
    assert answer["facts"][2]["citations"] == not_applicable
    assert answer["facts"][3]["citations"] == passed
    mean = answer["facts"][4]
    assert mean["parts"] == {"numerator": 975, "denominator": 14}
    assert mean["citations"] == scored
    # the older result of s30, the deleted meeting, the meeting before the window and
    # workspace B are never cited
    text_ = dumps(answer)
    for left_out in ("v1-s30-old", "v1-sd", "v1-so", "vb-sb"):
        assert r(left_out) not in text_


def test_scorecard_summary_minimum_boundary(world):
    """Deny: minimum - 1 chosen results; allow: exactly the minimum (30)."""
    allowed = run(world.a, "scorecard_summary", {"scorecard_version_id": V2, **SC_WINDOW})
    assert allowed["status"] == "answered"
    assert allowed["facts"][0]["value"] == 30
    assert allowed["facts"][-1]["value"] == 75.0
    assert allowed["facts"][-1]["parts"] == {"numerator": 75, "denominator": 1}
    later = {**SC_WINDOW, "from": (SC_FROM + timedelta(hours=1, seconds=30)).isoformat()}
    denied = run(world.a, "scorecard_summary", {"scorecard_version_id": V2, **later})
    assert reason(denied) == ("below_minimum_sample", None, None)


def test_scorecard_summary_without_applicable_weight_is_not_computable(world):
    answer = run(world.a, "scorecard_summary", {"scorecard_version_id": V3, **SC_WINDOW})
    assert reason(answer) == ("not_computable", None, None)


def test_scorecard_summary_needs_scorecard_read(world):
    """A9.  Deny: an API key with meeting:read; allow: a session of the same role."""
    params = {"scorecard_version_id": V1, **SC_WINDOW}
    denied = run(world.a, "scorecard_summary", params, scopes=["meeting:read"])
    assert reason(denied) == ("permission_denied", None, None)
    viewer = run(world.a, "scorecard_summary", params, role=WorkspaceRole.VIEWER)
    assert viewer["status"] == "answered"
    keyed = run(world.a, "conversation_count", WINDOW, scopes=["meeting:read"])
    assert keyed["status"] == "answered"


# --- guessed ids, record scope and other workspaces -----------------------------------------


@pytest.mark.parametrize(
    ("tool", "key", "base"),
    [
        pytest.param("conversation_count", "team_id", WINDOW, id="conversation-team"),
        pytest.param("conversation_count", "agent_user_id", WINDOW, id="conversation-agent"),
        pytest.param("action_items_due", "team_id", {"due_before": DUE_BEFORE}, id="action-team"),
        pytest.param(
            "action_items_due", "assignee_user_id", {"due_before": DUE_BEFORE}, id="action-assignee"
        ),
        pytest.param(
            "scorecard_summary", "team_id", {"scorecard_version_id": V1, **SC_WINDOW}, id="sc-team"
        ),
        pytest.param("scorecard_summary", "scorecard_version_id", SC_WINDOW, id="sc-version"),
        pytest.param("top_issues", "team_id", {"family": "call_reason"}, id="top-issues-team"),
    ],
)
def test_guessed_ids_give_the_same_answer(world, tool, key, base):
    """SPEC 2.5 and A11: a real id of workspace B, a real id of A outside the caller's
    scope and a random UUID.  The caller is a member who supervises no team and has no
    call of their own here."""
    ids = {
        "team_id": (TB, T1, RANDOM_ID),
        "agent_user_id": (world.b.owner_id, U1, RANDOM_ID),
        "assignee_user_id": (world.b.owner_id, U1, RANDOM_ID),
        "scorecard_version_id": (VB, V1, RANDOM_ID),
    }[key]
    caller = restricted(U3)
    answers = [
        run(world.a, tool, {**base, key: value}, caller, role=WorkspaceRole.MEMBER) for value in ids
    ]
    same_but_echo(answers, list(ids))
    for answer in answers:
        assert all(f["value"] == 0 for f in answer["facts"] if f["name"] != "mean_score_percent")
        assert all(f["citations"] == [] for f in answer["facts"])


def test_a_foreign_team_counts_zero_and_an_own_team_counts(world):
    foreign = run(world.a, "conversation_count", {**WINDOW, "team_id": TB})
    assert facts(foreign) == [("conversations", None, 0, [])]
    own = run(world.a, "conversation_count", {**WINDOW, "team_id": T1})
    assert facts(own) == [("conversations", None, 2, sorted([m("1"), m("2")]))]


def test_a_restricted_member_sees_own_calls_and_supervised_teams_only(world):
    """U1 is the agent of m1 (team T1) and supervises T2 (m3).  m2 (T1, agent U2) and m4
    (no team) are outside their scope."""
    caller = restricted(U1, T2)
    answer = run(world.a, "conversation_count", WINDOW, caller, role=WorkspaceRole.MEMBER)
    assert answer["scope"] == {"restricted": True}
    assert facts(answer) == [("conversations", None, 2, sorted([m("1"), m("3")]))]
    t1 = run(
        world.a, "conversation_count", {**WINDOW, "team_id": T1}, caller, role=WorkspaceRole.MEMBER
    )
    assert facts(t1) == [("conversations", None, 1, [m("1")])]  # own call only
    supervisor = restricted(U3, T2)
    denied = run(world.a, "conversation_count", {**WINDOW, "team_id": T1}, supervisor)
    assert facts(denied) == [("conversations", None, 0, [])]
    allowed = run(world.a, "conversation_count", {**WINDOW, "team_id": T2}, supervisor)
    assert facts(allowed) == [("conversations", None, 1, [m("3")])]
    actions = run(world.a, "action_items_due", {"due_before": DUE_BEFORE}, caller)
    assert facts(actions)[0] == ("action_items_due", None, 1, [a("1")])


def test_workspace_b_never_appears_in_workspace_a_answers(world):
    b_ids = [m("b1"), m("b2"), m("sb"), a("b1"), r("vb-sb"), TB, VB, world.b.workspace_id]
    answers = [
        run(world.a, "conversation_count", {**WINDOW, "group_by": "team_id"}),
        run(world.a, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "team_id"}),
        run(world.a, "scorecard_summary", {"scorecard_version_id": V1, **SC_WINDOW}),
    ]
    for answer in answers:
        assert answer["status"] == "answered"
        assert not any(value in dumps(answer) for value in b_ids)
    b_answer = run(world.b, "conversation_count", WINDOW)
    assert facts(b_answer) == [("conversations", None, 2, sorted([m("b1"), m("b2")]))]


# --- limits ---------------------------------------------------------------------------------


def _limits(**changes: int) -> ToolLimits:
    values = DEFAULT_LIMITS.to_json() | changes
    return ToolLimits(**values)


def test_the_row_limit_boundary(world):
    """Deny: max_rows + 1 rows; allow: exactly max_rows rows."""
    exact = run(world.a, "conversation_count", WINDOW, limits=_limits(max_rows=4))
    assert exact["facts"][0]["value"] == 4
    assert exact["limits"]["max_rows"] == 4
    over = run(world.a, "conversation_count", WINDOW, limits=_limits(max_rows=3))
    assert reason(over) == ("row_limit_exceeded", None, None)
    params = {"scorecard_version_id": V1, **SC_WINDOW}
    loaded = run(world.a, "scorecard_summary", params, limits=_limits(max_rows=32))
    assert loaded["status"] == "answered"  # 32 loaded rows: s30 has two results
    assert reason(run(world.a, "scorecard_summary", params, limits=_limits(max_rows=31)))[0] == (
        "row_limit_exceeded"
    )
    due = {"due_before": DUE_BEFORE}
    assert run(world.a, "action_items_due", due, limits=_limits(max_rows=4))["status"] == "answered"
    assert reason(run(world.a, "action_items_due", due, limits=_limits(max_rows=3)))[0] == (
        "row_limit_exceeded"
    )


def test_citations_are_capped_with_the_total(world):
    answer = run(world.a, "conversation_count", WINDOW, limits=_limits(max_citations=2))
    first = answer["facts"][0]
    assert first["citations"] == sorted([m("1"), m("2"), m("3"), m("4")])[:2]
    assert first["citations_total"] == 4 and first["citations_complete"] is False
    assert first["citations_withheld"] is False


def test_the_time_budget_abstains(world, monkeypatch):
    ticks = iter(range(0, 10_000, 100))  # every reading is 100 s after the one before
    monkeypatch.setattr(
        copilot_tool_service, "time", types.SimpleNamespace(monotonic=lambda: next(ticks))
    )
    answer = run(world.a, "conversation_count", WINDOW, limits=_limits(time_budget_ms=30000))
    assert reason(answer) == ("time_limit_exceeded", None, None)


def _postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


@pytest.mark.postgres
def test_a_cancelled_statement_abstains_and_the_session_stays_usable(world):
    ctx = context_of(world.a)
    with inspection_session_factory()() as locker, bound(world.a) as db:
        if not _postgres(db):
            pytest.skip("PostgreSQL only")
        db.execute(text("SELECT set_config('statement_timeout', '7s', true)"))
        locker.execute(text("LOCK TABLE meetings IN ACCESS EXCLUSIVE MODE"))
        try:
            result = run_tool(
                db,
                context=ctx,
                name="conversation_count",
                params=WINDOW,
                now=NOW,
                limits=_limits(statement_timeout_ms=200),
            )
        finally:
            locker.rollback()
        assert reason(result.to_json()) == ("time_limit_exceeded", None, None)
        assert db.execute(text("SELECT 1")).scalar_one() == 1
        assert db.execute(text("SHOW statement_timeout")).scalar_one() == "7s"
        again = run_tool(db, context=ctx, name="conversation_count", params=WINDOW, now=NOW)
        assert again.to_json()["facts"][0]["value"] == 4
        assert db.execute(text("SHOW statement_timeout")).scalar_one() == "7s"


# --- determinism and read-only --------------------------------------------------------------


def _all_answers(world: World) -> dict[str, Any]:
    return {
        "count": run(world.a, "conversation_count", {**WINDOW, "group_by": "day"}),
        "status": run(world.a, "conversation_count", {**WINDOW, "group_by": "status"}),
        "actions": run(
            world.a, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "team_id"}
        ),
        "scorecard": run(world.a, "scorecard_summary", {"scorecard_version_id": V1, **SC_WINDOW}),
        "restricted": run(world.a, "conversation_count", WINDOW, restricted(U1, T2)),
    }


def test_two_runs_and_another_insert_order_give_the_same_json(world):
    first = dumps(_all_answers(world))
    assert dumps(_all_answers(world)) == first
    clear_world(world)
    seed_world(world, reverse=True)
    assert dumps(_all_answers(world)) == first


def test_the_tools_run_only_selects_and_savepoints(world):
    seen: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        seen.append(statement)

    ctx = context_of(world.a)
    calls = [
        ("conversation_count", {**WINDOW, "group_by": "week"}),
        ("action_items_due", {"due_before": DUE_BEFORE}),
        ("scorecard_summary", {"scorecard_version_id": V1, **SC_WINDOW}),
        ("issue_aggregate", {"intent": "issue_count", "params": {"family": "call_reason"}}),
        ("top_issues", {"family": "call_reason"}),
        ("conversation_count", {**WINDOW}),
    ]
    with bound(world.a) as db:
        db.connection()  # the tenant binding statements run before the listener
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            results = [
                run_tool(db, context=ctx, name=name, params=params, now=NOW)
                for name, params in calls
            ]
            results.append(
                run_tool(
                    db,
                    context=ctx,
                    name="conversation_count",
                    params=WINDOW,
                    now=NOW,
                    limits=_limits(max_rows=1),
                )
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
    assert [r.to_json()["status"] for r in results[:3]] == ["answered"] * 3
    assert seen
    allowed = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")
    for statement in seen:
        assert statement.lstrip().upper().startswith(allowed), statement


def test_pending_session_objects_are_not_flushed(world):
    """SPEC A12: the savepoint is on the connection, so a pending meeting stays pending and
    is not counted."""
    ctx = context_of(world.a)
    with bound(world.a) as db:
        pending = _meeting(world.a, "pending", FROM + timedelta(hours=2))
        db.add(pending)
        result = run_tool(db, context=ctx, name="conversation_count", params=WINDOW, now=NOW)
        assert result.to_json()["facts"][0]["value"] == 4
        assert pending in db.new
        db.rollback()


# --- the issue tools ------------------------------------------------------------------------


@dataclass
class Trends:
    tenant: Tenant
    complaints: list[str]  # meeting ids, sorted
    billing: list[str]


@pytest.fixture
def trends(client, email_outbox) -> Trends:
    """32 conversations in the trend window, no agent: 12 complaints, 20 billing calls."""
    tenant = Tenant(client, email_outbox, "tools-trends@example.com")
    complaints, billing = [], []
    for index in range(32):
        seeded = seed(
            tenant,
            received_at=TREND_BASE + timedelta(minutes=index),
            extraction=COMPLAINT if index < 12 else BILLING,
        )
        assert seeded.meeting_id is not None
        (complaints if index < 12 else billing).append(seeded.meeting_id)
    return Trends(tenant, sorted(complaints), sorted(billing))


def meetings_of(tenant: Tenant) -> list[str]:
    with inspection_session_factory()() as db:
        return sorted(
            db.scalars(
                text("SELECT id FROM meetings WHERE workspace_id = :w"), {"w": tenant.workspace_id}
            )
        )


def issue(tenant: Tenant, intent: str, access: RecordAccess | None = None, **params: Any):
    return run(
        tenant, "issue_aggregate", {"intent": intent, "params": {**TREND_WINDOW, **params}}, access
    )


def test_issue_count_and_rate_with_citations(trends):
    complaints = trends.complaints
    count = issue(trends.tenant, "issue_count", issue="complaint_refund")
    assert count["status"] == "answered"
    assert facts(count) == [("issue_count", {"row": "issue:complaint_refund"}, 12, complaints)]
    assert count["facts"][0]["parts"] is None
    assert count["filters"]["intent"] == "issue_count" and count["filters"]["series"] is None
    assert count["filters"]["issue"] == ["complaint_refund"]
    provenance = count["provenance"]
    assert provenance["catalogue_version"] == "copilot-intents/1"
    assert len(provenance["catalogue_digest"]) == 64
    assert provenance["trends_version"] == "issue-trends/1"
    assert provenance["disclosure"] == {
        "status": "released",
        "reason": None,
        "kind": "rate",
        "masked": [],
    }
    rate = issue(trends.tenant, "issue_rate", family="service_complaint")
    by_row = {f["group"]["row"]: f for f in rate["facts"]}
    family = by_row["family:service_complaint"]
    assert family["value"] == 0.375 and family["parts"] == {"numerator": 12, "denominator": 32}
    assert family["citations"] == complaints
    assert by_row["issue:complaint_pricing"]["value"] == 0.0
    assert by_row["issue:complaint_pricing"]["citations"] == []
    assert list(by_row)[0] == "family:service_complaint"  # build_rows order: family first


def test_issue_citations_are_capped(trends):
    answer = run(
        trends.tenant,
        "issue_aggregate",
        {"intent": "issue_count", "params": {**TREND_WINDOW, "issue": "complaint_refund"}},
        limits=_limits(max_citations=5),
    )
    fact = answer["facts"][0]
    assert fact["citations"] == trends.complaints[:5]
    assert fact["citations_total"] == 12 and fact["citations_complete"] is False


def test_issue_trend_points_and_an_empty_point_abstains(trends):
    week = issue(trends.tenant, "issue_trend", issue="complaint_refund")
    assert week["filters"]["series"] == {"grain": "week", "timezone": "Asia/Tehran"}
    assert [(f["name"], f["group"], f["value"], f["parts"]) for f in week["facts"]] == [
        (
            "issue_rate",
            {
                "row": "issue:complaint_refund",
                "point": 0,
                "from": "2026-10-01T12:30:00+03:30",
                "to": "2026-10-02T12:30:00+03:30",
                "partial": True,
            },
            0.375,
            {"numerator": 12, "denominator": 32},
        )
    ]
    # by day the second point (2026-10-02 in Tehran) holds no conversation
    day = issue(trends.tenant, "issue_trend", issue="complaint_refund", grain="day")
    assert reason(day) == ("not_computable", "empty_denominator", None)


def test_issue_row_limit(trends):
    params = {"intent": "issue_count", "params": {**TREND_WINDOW, "family": "call_reason"}}
    exact = run(trends.tenant, "issue_aggregate", params, limits=_limits(max_rows=32))
    assert exact["status"] == "answered"
    over = run(trends.tenant, "issue_aggregate", params, limits=_limits(max_rows=31))
    assert reason(over) == ("row_limit_exceeded", None, None)


@pytest.mark.parametrize(
    ("intent", "params", "expected"),
    [
        pytest.param("forecast", {}, ("forecast_not_supported", None, None), id="forecast"),
        pytest.param("causal_question", {}, ("causal_not_supported", None, None), id="causal"),
        pytest.param(
            "agent_ranking", {}, ("individual_ranking_not_supported", None, None), id="ranking"
        ),
        pytest.param("out_of_scope", {}, ("out_of_scope", None, None), id="out-of-scope"),
        pytest.param("weather", {}, ("out_of_scope", "intent_unknown", None), id="unknown-intent"),
        pytest.param(
            "issue_count",
            {"family": "call_reason", "product": "x"},
            ("unsupported_filter", "filter_unsupported", "product"),
            id="product",
        ),
        pytest.param(
            "issue_count",
            {"family": "call_reason", "workspace_id": "x"},
            ("scope_not_allowed", None, "workspace_id"),
            id="inner-scope-key",
        ),
        pytest.param(
            "issue_count", {}, ("invalid_request", "target_required", None), id="no-target"
        ),
        pytest.param(
            "cohort_comparison",
            {"family": "call_reason", "compare_by": "language", "compare_values": ["fa", "en"]},
            ("intent_not_supported", None, None),
            id="comparison",
        ),
        pytest.param(
            "coverage_status", {}, ("intent_not_supported", None, None), id="coverage-status"
        ),
        pytest.param(
            "evidence_examples",
            {"family": "call_reason"},
            ("intent_not_supported", None, None),
            id="evidence",
        ),
        pytest.param(
            "issue_count",
            {"family": "qa"},
            ("not_computable", "scorecard_results_unavailable", None),
            id="qa",
        ),
    ],
)
def test_issue_aggregate_refusals_and_unsupported_intents(world, intent, params, expected):
    answer = run(world.a, "issue_aggregate", {"intent": intent, "params": {**WINDOW, **params}})
    assert reason(answer) == expected
    assert answer["tool"] == "issue_aggregate"


def test_a_forecast_abstains_and_a_count_with_a_selector_answers(trends):
    denied = issue(trends.tenant, "forecast")
    assert reason(denied) == ("forecast_not_supported", None, None)
    allowed = issue(trends.tenant, "issue_count", family="call_reason")
    assert allowed["status"] == "answered"


def test_a_small_rate_count_is_shown_with_its_citations_withheld(client, email_outbox):
    tenant = Tenant(client, email_outbox, "tools-small@example.com")
    for index in range(5):
        seed(tenant, received_at=TREND_BASE + timedelta(minutes=index), extraction=COMPLAINT)
    count = issue(tenant, "issue_count", issue="complaint_refund")
    assert count["facts"] == [
        {
            "name": "issue_count",
            "group": {"row": "issue:complaint_refund"},
            "value": 5,
            "parts": None,
            "citations": [],
            "citations_total": 5,
            "citations_complete": False,
            "citations_withheld": True,
        }
    ]
    rate = issue(tenant, "issue_rate", issue="complaint_refund")
    assert reason(rate) == ("below_minimum_sample", "below_minimum_sample", None)


def test_the_stricter_cell_kind_hides_a_small_count_of_named_agents(client, email_outbox):
    """A2: five agents make a rate cohort from the data, but naming them makes the routing
    kind agent; the stricter kind hides the count under the minimum."""
    tenant = Tenant(client, email_outbox, "tools-kind@example.com")
    agents = sorted(add_user(f"agent{i}") for i in range(5))
    for index, agent in enumerate(agents):
        seed(
            tenant,
            received_at=TREND_BASE + timedelta(minutes=index),
            extraction=COMPLAINT,
            agent_user_id=agent,
        )
    shown = issue(tenant, "issue_count", issue="complaint_refund")
    assert shown["facts"][0]["value"] == 5
    assert shown["provenance"]["disclosure"]["kind"] == "rate"
    hidden = issue(tenant, "issue_count", issue="complaint_refund", agent_user_id=agents)
    assert reason(hidden) == ("below_minimum_sample", "below_minimum_sample", None)


@pytest.fixture
def masked_tenant(client, email_outbox) -> tuple[Tenant, str]:
    """31 calls of one agent, all dissatisfied: 25 with a topic the model could not tell, 6
    refund complaints.  The complaint rows have 6 assessable calls (under 30), so a viewer
    other than the agent gets the whole window layer withheld (HOM-82 all or nothing)."""
    tenant = Tenant(client, email_outbox, "tools-masked@example.com")
    agent = add_user("agent")
    for index in range(31):
        seed(
            tenant,
            received_at=TREND_BASE + timedelta(minutes=index),
            extraction=UNKNOWN_TOPIC if index < 25 else COMPLAINT,
            agent_user_id=agent,
        )
    return tenant, agent


def test_a_masked_row_abstains_withheld(masked_tenant):
    tenant, agent = masked_tenant
    denied = issue(tenant, "issue_count", issue="intent_report_dissatisfaction")
    assert reason(denied) == ("withheld", None, None)
    top = run(tenant, "top_issues", {"family": "call_reason", **TREND_WINDOW})
    assert reason(top) == ("withheld", None, None)
    # the agent's own view: no mask
    own = issue(tenant, "issue_count", restricted(agent), issue="intent_report_dissatisfaction")
    assert own["status"] == "answered" and own["facts"][0]["value"] == 31


def test_top_issues_ranks_and_cuts(trends):
    answer = run(trends.tenant, "top_issues", {"family": "call_reason", "limit": 3, **TREND_WINDOW})
    assert answer["status"] == "answered"
    assert [(f["group"], f["value"], f["citations_total"]) for f in answer["facts"]] == [
        ({"rank": 1, "row": "issue:intent_resolve_billing"}, 20, 20),
        ({"rank": 2, "row": "issue:intent_report_dissatisfaction"}, 12, 12),
        ({"rank": 3, "row": "issue:intent_cancel_service"}, 0, 0),  # ties: issue id order
    ]
    assert answer["facts"][0]["citations"] == trends.billing
    assert answer["filters"]["family"] == "call_reason" and answer["filters"]["limit"] == 3
    assert answer["provenance"]["catalogue_version"] == "copilot-intents/1"


def test_top_issues_of_qa_is_not_computable(trends):
    answer = run(trends.tenant, "top_issues", {"family": "qa", **TREND_WINDOW})
    assert reason(answer) == ("not_computable", "scorecard_results_unavailable", None)


def test_top_issues_outside_the_scope_is_refused(trends):
    member = restricted(fid("member"), T2)
    answer = run(trends.tenant, "top_issues", {"family": "call_reason", "team_id": T1}, member)
    assert reason(answer) == ("outside_caller_scope", None, "team_id")


def test_issue_guessed_teams_give_the_same_answer(trends):
    member = restricted(fid("member"), T2)
    answers = [
        issue(trends.tenant, "issue_count", member, family="call_reason", team_id=value)
        for value in (TB, T1, RANDOM_ID)
    ]
    same_but_echo(answers, [TB, T1, RANDOM_ID])
    assert reason(answers[0]) == ("outside_caller_scope", None, "team_id")
    owner = [
        issue(trends.tenant, "issue_count", family="call_reason", team_id=v)
        for v in (TB, RANDOM_ID)
    ]
    same_but_echo(owner, [TB, RANDOM_ID])
    assert reason(owner[0]) == ("not_computable", "empty_denominator", None)


def test_issue_answers_are_deterministic(trends):
    first = issue(trends.tenant, "issue_rate", family="call_reason")
    assert dumps(issue(trends.tenant, "issue_rate", family="call_reason")) == dumps(first)


# --- PostgreSQL: row-level security as the app role -----------------------------------------


@pytest.mark.postgres
def test_rls_as_the_app_role_never_returns_workspace_b(world):
    with inspection_session_factory()() as probe:
        if not _postgres(probe):
            pytest.skip("PostgreSQL only")
    for index in range(3):
        seed(world.a, received_at=TREND_BASE + timedelta(minutes=index), extraction=COMPLAINT)
        seed(world.b, received_at=TREND_BASE + timedelta(minutes=index), extraction=COMPLAINT)
    calls = [
        ("conversation_count", {**WINDOW, "group_by": "team_id"}),
        ("conversation_count", {**WINDOW, "team_id": TB}),
        ("action_items_due", {"due_before": DUE_BEFORE, "team_id": [T1, TB]}),
        ("scorecard_summary", {"scorecard_version_id": V1, **SC_WINDOW}),
        ("scorecard_summary", {"scorecard_version_id": VB, **SC_WINDOW}),
        (
            "issue_aggregate",
            {"intent": "issue_count", "params": {**TREND_WINDOW, "issue": "complaint_refund"}},
        ),
        ("top_issues", {"family": "service_complaint", **TREND_WINDOW}),
    ]
    ctx = context_of(world.a)
    b_meetings = set(meetings_of(world.b))
    with bound(world.a, get_session_factory()) as app:
        assert app.execute(text("SELECT current_user")).scalar_one() == "homayar_app"
        # RLS: the app role sees no row of B even without a WHERE clause
        assert (
            app.execute(
                text("SELECT count(*) FROM meetings WHERE workspace_id = :b"),
                {"b": world.b.workspace_id},
            ).scalar_one()
            == 0
        )
        as_app = [
            run_tool(app, context=ctx, name=name, params=params, now=NOW).to_json()
            for name, params in calls
        ]
    as_dba = [run(world.a, name, params) for name, params in calls]
    assert [dumps(x) for x in as_app] == [dumps(x) for x in as_dba]
    for answer in as_app:
        text_ = dumps(answer)
        assert not any(meeting in text_ for meeting in b_meetings)
        assert r("vb-sb") not in text_ and a("b1") not in text_
    assert as_app[5]["facts"][0]["value"] == 3
