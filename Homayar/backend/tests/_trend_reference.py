"""HOM-84 (WP15-T06): the trend reference dataset, shared by the reconciliation, permission
and statement-count tests.

One organization holds two workspaces.  Workspace A is the owner's own workspace with record
scope ``team``, three active teams and one inactive team (zeta-old, no conversation).
Workspace B was made inside the organization by the owner (its owner member), has record
scope ``team`` and two teams.  Every conversation of the window lies in one of three closed
weeks W1, W2, W3 (Saturday 00:00 Asia/Tehran, the trend grid), so the workspace views and the
organization views read the same window.  W0 is the closed week before W1.

Extractions (the stored ``PolicyResult`` of the pinned taxonomy and policy):

    C  complaint   intent report_dissatisfaction, topic refund, outcome escalated
    B  billing     intent resolve_billing, topic invoice
    V  vague       intent report_dissatisfaction, topic abstained (insufficient_evidence)

Workspace A, the window (agent: per week W1 / W2 / W3):

    team         agents          per agent                    convs  C   B   V
    alpha-big    b1..b5 (5)      W1 C C B, W2 C B, W3 C V        35  20  10   5
    beta-mid     m1 m2 m3 (3)    see MID below                   31   9  22   0
    gamma-small  s1 s2 (2)       s1: C B C, s2: B C B             6   3   3   0

    MID: conversations per agent in W1 / W2 / W3: m1 4/4/3, m2 4/3/3, m3 3/4/3.  The first
    of each agent-week is C, the rest B.  So W1 11 (3 C), W2 11 (3 C), W3 9 (3 C).

Processing gaps in alpha-big, agent b1 (the agent persona):

    no_run         W2, completed, transcribed, no analysis run (in the population, missing)
    failed         W1, status failed
    queued         W3, status queued (in progress)
    no_transcript  W2, completed, a current revision with 0 segments
    deleted        W1, a soft-deleted meeting

Also in A: one rejected ledger row (W1, no meeting); the analyst persona's own upload (W3, C,
no team, no agent); one C call in W0 with no team and no agent (in the default 4-week window of
``/aggregates`` only); one conversation of b2 five weeks before W1 (outside every window); and
four voicemails (VM1, VM2 alpha-big completed; VM3 beta-mid failed; VM4 alpha-big completed,
five weeks before W1).  Voicemails have no ledger row.

Workspace B:

    team          agents          per agent                    convs  C   B   V
    delta-b       d1..d5 (5)      W1 C, W2 B, W3 B                15   5  10   0
    epsilon-four  e1..e4 (4)      W1 only: 6 C, 1 B, 1 V          32  24   4   4

    The supervisor persona is also a member of B and supervises epsilon-four.

The test modules hold the hand-made expected numbers; this module only seeds.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, update

from backend.database.saas_models import (
    AnalysisRun,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    Meeting,
    MeetingStatus,
    Membership,
    RecordScope,
    StageState,
    Team,
    TeamAssignment,
    TeamRole,
    TranscriptRevision,
    TranscriptSource,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.security.context import CSRF_COOKIE
from backend.services.rate_limit_service import RateLimiter
from backend.services.role_trend_service import current_week_start
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _mint
from backend.tests.test_issue_trend_service import _extraction_row
from backend.tests.test_issue_trends_counting import ok, stored
from backend.tests.test_organizations import Persona, _grant, _persona
from backend.voicemail.models import VOICEMAIL_MEETING_SOURCE, VoicemailMessage

WEEK = timedelta(days=7)
DAY = timedelta(days=1)

EXTRACTIONS = {
    "C": stored(intent=ok("report_dissatisfaction"), topic=ok("refund"), outcome=ok("escalated")),
    "B": stored(intent=ok("resolve_billing"), topic=ok("invoice")),
    "V": stored(intent=ok("report_dissatisfaction"), topic="insufficient_evidence"),
}

# (week index, extraction) per agent.
BIG = [(0, "C"), (0, "C"), (0, "B"), (1, "C"), (1, "B"), (2, "C"), (2, "V")]
MID = {
    0: [4, 4, 3],  # m1, m2, m3 in W1
    1: [4, 3, 4],
    2: [3, 3, 3],
}
SMALL = [
    [(0, "C"), (1, "B"), (2, "C")],  # s1
    [(0, "B"), (1, "C"), (2, "B")],  # s2
]
DELTA = [(0, "C"), (1, "B"), (2, "B")]
EPSILON = [(0, "C")] * 6 + [(0, "B"), (0, "V")]  # per agent, all in W1
GAPS = (  # (gap, week) for agent b1 in alpha-big
    ("no_run", 1),
    ("failed", 0),
    ("queued", 2),
    ("no_transcript", 1),
    ("deleted", 0),
)


@dataclass(frozen=True)
class Conversation:
    """One seeded ledger row and its meeting (``meeting_id`` is ``None`` for a rejected row)."""

    meeting_id: str | None
    workspace_id: str
    team: str | None  # "big", "mid", "small", "delta"
    agent_user_id: str | None
    week: int | None  # 0, 1, 2 (W1..W3); -1 for W0; None five weeks before W1
    extraction: str | None  # "C", "B", "V"; None for a gap
    gap: str | None = None  # see GAPS, or "rejected", "outside"
    created_by: str | None = None  # the uploader when it is not the owner


@dataclass
class Reference:
    client: Any
    organization_id: str
    a: SimpleNamespace  # workspace_id, organization_id, owner_id
    b: SimpleNamespace
    week_start: datetime  # Saturday 00:00 Asia/Tehran of this week, UTC
    start: datetime  # W1 start
    teams: dict[str, str]  # "big", "mid", "small", "old" (inactive) in A; "delta", "epsilon" in B
    agents: dict[str, list[str]]  # "big" (b1 is the agent persona), "mid", "small", "delta",
    # "epsilon"
    people: dict[str, Persona]  # see build_reference
    memberships: dict[str, dict[str, Any]]  # organization memberships by persona name
    reader_key: str  # an API key of the owner in A, scope meeting:read
    writer_key: str  # an API key of the owner in A, scope meeting:create only
    conversations: list[Conversation] = field(default_factory=list)
    voicemails: list[tuple[str, str, int | None]] = field(default_factory=list)  # id, team, week
    _minute: int = 0

    @property
    def end(self) -> datetime:
        return self.start + 3 * WEEK

    def window(self, weeks: Iterable[int] = (0, 1, 2)) -> dict[str, str]:
        chosen = sorted(weeks)
        return {
            "from": (self.start + chosen[0] * WEEK).isoformat(),
            "to": (self.start + (chosen[-1] + 1) * WEEK).isoformat(),
        }

    def at(self, week: int) -> datetime:
        """A fresh instant inside ``week`` (1 day after its start, one minute apart)."""
        self._minute += 1
        return self.start + week * WEEK + DAY + timedelta(minutes=self._minute)

    def meetings(self, **match: Any) -> set[str]:
        """Ids of the seeded meetings whose fields equal ``match`` (``extraction`` may be a
        set of letters)."""
        found = set()
        for item in self.conversations:
            if item.meeting_id is None:
                continue
            ok_ = True
            for key, value in match.items():
                have = getattr(item, key)
                if isinstance(value, set | frozenset):
                    ok_ = ok_ and have in value
                else:
                    ok_ = ok_ and have == value
            if ok_:
                found.add(item.meeting_id)
        return found


def _register(client, email_outbox, name: str) -> Persona:
    RateLimiter.reset_memory()  # five sign-ups an hour per address; the dataset needs ten
    return _persona(client, email_outbox, email=f"ref-{name}@example.com", name=name)


def _users(prefix: str, count: int) -> list[str]:
    with inspection_session_factory()() as db:
        users = [
            User(
                email=f"{prefix}{n}-{uuid.uuid4().hex[:8]}@example.com", display_name=f"{prefix}{n}"
            )
            for n in range(1, count + 1)
        ]
        db.add_all(users)
        db.commit()
        return [user.id for user in users]


def _team(workspace_id: str, name: str) -> str:
    with inspection_session_factory()() as db:
        team = Team(workspace_id=workspace_id, name=name, slug=f"{name}-{uuid.uuid4().hex[:8]}")
        db.add(team)
        db.commit()
        return team.id


def _member(workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    """A workspace membership row, like a delegated installation, without the seat checks."""
    with inspection_session_factory()() as db:
        db.add(
            Membership(
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
                is_active=True,
                valid_from=datetime.now(UTC) - DAY,
            )
        )
        db.commit()


def _assign(workspace_id: str, team_id: str, user_id: str, role: TeamRole, by: str) -> None:
    with inspection_session_factory()() as db:
        db.add(
            TeamAssignment(
                workspace_id=workspace_id,
                team_id=team_id,
                user_id=user_id,
                role=role,
                assigned_by_user_id=by,
                valid_from=datetime.now(UTC) - DAY,
                valid_until=None,
            )
        )
        db.commit()


def _conversation(
    db,
    place: SimpleNamespace,
    *,
    received_at: datetime,
    team_id: str | None,
    agent_user_id: str | None,
    extraction: str | None,
    gap: str | None = None,
    created_by: str | None = None,
) -> str | None:
    """One ledger row and (unless rejected) its meeting, revision, run and extraction row."""
    status = {"failed": MeetingStatus.FAILED, "queued": MeetingStatus.QUEUED}.get(
        gap or "", MeetingStatus.COMPLETED
    )
    meeting_id = None
    if gap != "rejected":
        meeting = Meeting(
            workspace_id=place.workspace_id,
            created_by_user_id=created_by or place.owner_id,
            title="Reference call",
            status=status,
            duration_seconds=30,
            deleted_at=received_at if gap == "deleted" else None,
            team_id=team_id,
            agent_user_id=agent_user_id,
            language="fa",
        )
        db.add(meeting)
        db.flush()
        meeting_id = meeting.id
    db.add(
        IngestionRecord(
            organization_id=place.organization_id,
            workspace_id=place.workspace_id,
            source_type=IngestionSourceType.FILE_UPLOAD,
            source_provider="test",
            external_event_id=str(uuid.uuid4()),
            payload_hash="0" * 64,
            status=IngestionStatus.REJECTED if gap == "rejected" else IngestionStatus.ACCEPTED,
            reason="unsupported_media" if gap == "rejected" else None,
            received_at=received_at,
            meeting_id=meeting_id,
        )
    )
    if meeting_id is None or gap in ("failed", "queued"):
        return meeting_id
    revision = TranscriptRevision(
        workspace_id=place.workspace_id,
        meeting_id=meeting_id,
        revision_number=1,
        source=TranscriptSource.ASR,
        reason="seed",
        language="fa",
        segment_count=0 if gap == "no_transcript" else 2,
        is_mock=False,
        is_current=True,
    )
    db.add(revision)
    db.flush()
    if gap in ("no_run", "no_transcript"):
        return meeting_id
    run = AnalysisRun(
        workspace_id=place.workspace_id,
        meeting_id=meeting_id,
        transcript_revision_id=revision.id,
        state=StageState.COMPLETED,
        is_mock=False,
        is_current=True,
    )
    db.add(run)
    db.flush()
    if extraction is not None:  # the deleted gap has a run and no extraction row
        db.add(_extraction_row(place.workspace_id, meeting_id, run.id, EXTRACTIONS[extraction]))
    return meeting_id


def seed_conversations(ref: Reference, *, voicemails: bool = True) -> None:
    """Every conversation of the module docstring, once more each call (a second call doubles
    the conversations of every cohort and keeps every agent set)."""
    rows: list[tuple[Any, ...]] = []  # place, team, agent, week, extraction, gap[, uploader]
    big, mid, small, delta, epsilon = (
        ref.agents[name] for name in ("big", "mid", "small", "delta", "epsilon")
    )
    for agent in big:
        rows += [(ref.a, "big", agent, week, kind, None) for week, kind in BIG]
    for week, counts in MID.items():
        for agent, count in zip(mid, counts, strict=True):
            rows += [
                (ref.a, "mid", agent, week, "C" if n == 0 else "B", None) for n in range(count)
            ]
    for agent, plan in zip(small, SMALL, strict=True):
        rows += [(ref.a, "small", agent, week, kind, None) for week, kind in plan]
    rows += [(ref.a, "big", big[0], week, None, gap) for gap, week in GAPS]
    rows.append((ref.a, None, None, 0, None, "rejected"))
    rows.append((ref.a, "big", big[1], None, "C", "outside"))
    rows.append((ref.a, None, None, 2, "C", None, ref.people["analyst"].user_id))
    rows.append((ref.a, None, None, -1, "C", None))  # W0
    for agent in delta:
        rows += [(ref.b, "delta", agent, week, kind, None) for week, kind in DELTA]
    for agent in epsilon:
        rows += [(ref.b, "epsilon", agent, week, kind, None) for week, kind in EPSILON]

    with inspection_session_factory()() as db:
        for place, team, agent, week, kind, gap, *uploader in rows:
            received_at = ref.at(week) if week is not None else ref.start - 5 * WEEK + DAY
            created_by = uploader[0] if uploader else None
            meeting_id = _conversation(
                db,
                place,
                received_at=received_at,
                team_id=ref.teams[team] if team is not None else None,
                agent_user_id=agent,
                extraction=kind,
                gap=None if gap == "outside" else gap,
                created_by=created_by,
            )
            ref.conversations.append(
                Conversation(
                    meeting_id, place.workspace_id, team, agent, week, kind, gap, created_by
                )
            )
        db.commit()
    if voicemails:
        for team, week, status in (
            ("big", 0, MeetingStatus.COMPLETED),
            ("big", 2, MeetingStatus.COMPLETED),
            ("mid", 1, MeetingStatus.FAILED),
            ("big", None, MeetingStatus.COMPLETED),
        ):
            received_at = ref.at(week) if week is not None else ref.start - 5 * WEEK + DAY
            ref.voicemails.append((_voicemail(ref, team, received_at, status), team, week))


def _voicemail(ref: Reference, team: str, received_at: datetime, status: MeetingStatus) -> str:
    """A voicemail row and its meeting (no ledger row); a current revision of 2 segments when
    completed, no analysis run."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=ref.a.workspace_id,
            created_by_user_id=ref.a.owner_id,
            title="Voicemail",
            status=status,
            duration_seconds=10,
            team_id=ref.teams[team],
            source=VOICEMAIL_MEETING_SOURCE,
        )
        db.add(meeting)
        db.flush()
        if status is MeetingStatus.COMPLETED:
            db.add(
                TranscriptRevision(
                    workspace_id=ref.a.workspace_id,
                    meeting_id=meeting.id,
                    revision_number=1,
                    source=TranscriptSource.ASR,
                    reason="asr",
                    language="fa",
                    segment_count=2,
                    is_current=True,
                )
            )
        db.add(
            VoicemailMessage(
                workspace_id=ref.a.workspace_id,
                meeting_id=meeting.id,
                external_message_id=f"vm-{uuid.uuid4().hex}",
                received_at=received_at,
                duration_seconds=10,
            )
        )
        db.commit()
        return meeting.id


def add_teams(ref: Reference, count: int) -> None:
    """``count`` more active teams in A, each with 5 new agents and one C call per agent and
    week (the statement-count test grows the number of teams with it)."""
    with inspection_session_factory()() as db:
        for n in range(count):
            name = f"extra-{len(ref.teams)}-{n}"
            team = Team(
                workspace_id=ref.a.workspace_id, name=name, slug=f"{name}-{uuid.uuid4().hex[:8]}"
            )
            db.add(team)
            db.flush()
            ref.teams[name] = team.id
        db.commit()
    for name in [key for key in ref.teams if key.startswith("extra-")]:
        if name in ref.agents:
            continue
        ref.agents[name] = _users(f"{name}-agent", 5)
        with inspection_session_factory()() as db:
            for agent in ref.agents[name]:
                for week in (0, 1, 2):
                    meeting_id = _conversation(
                        db,
                        ref.a,
                        received_at=ref.at(week),
                        team_id=ref.teams[name],
                        agent_user_id=agent,
                        extraction="C",
                    )
                    ref.conversations.append(
                        Conversation(meeting_id, ref.a.workspace_id, name, agent, week, "C")
                    )
            db.commit()


def build_reference(client, email_outbox, *, conversations: bool = True) -> Reference:
    """The personas, teams, grants, keys and (by default) the conversations.

    ``people``: ``owner`` (workspace A owner and organization owner), ``admin`` (A admin),
    ``supervisor`` (A member, supervisor of alpha-big), ``analyst`` (A viewer with no team
    assignment: the workspace analyst role), ``agent`` (A member, agent of alpha-big, the
    agent b1; also an organization analyst with a scope grant on all of A, so an organization
    caller with a restricted workspace membership), ``org_admin``, ``org_analyst`` and
    ``org_executive`` (scope grant on all of A), ``org_narrow`` (organization analyst with a team
    grant on beta-mid).  The supervisor is also a member of B, supervisor of epsilon-four.
    """
    people = {name: _register(client, email_outbox, name) for name in (
        "owner", "admin", "supervisor", "analyst", "agent",
        "org_admin", "org_analyst", "org_executive", "org_narrow",
    )}  # fmt: skip
    owner = people["owner"]
    org = owner.organization_id
    a_id = owner.personal_workspace_id
    created = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Reference B"})
    assert created.status_code == 201, created.text
    with inspection_session_factory()() as db:
        assert db.scalar(select(Workspace.organization_id).where(Workspace.id == a_id)) == org
        db.execute(
            update(Workspace)
            .where(Workspace.id.in_([a_id, created.json()["id"]]))
            .values(record_scope=RecordScope.TEAM)
        )
        db.commit()
    a = SimpleNamespace(workspace_id=a_id, organization_id=org, owner_id=owner.user_id)
    b = SimpleNamespace(
        workspace_id=created.json()["id"], organization_id=org, owner_id=owner.user_id
    )

    teams = {
        "big": _team(a_id, "alpha-big"),
        "mid": _team(a_id, "beta-mid"),
        "small": _team(a_id, "gamma-small"),
        "old": _team(a_id, "zeta-old"),
        "delta": _team(b.workspace_id, "delta-b"),
        "epsilon": _team(b.workspace_id, "epsilon-four"),
    }
    with inspection_session_factory()() as db:
        db.execute(update(Team).where(Team.id == teams["old"]).values(is_active=False))
        db.commit()
    agents = {
        "big": [people["agent"].user_id, *_users("b", 4)],  # b1 is the agent persona
        "mid": _users("m", 3),
        "small": _users("s", 2),
        "delta": _users("d", 5),
        "epsilon": _users("e", 4),
    }
    _member(a_id, people["admin"].user_id, WorkspaceRole.ADMIN)
    _member(a_id, people["supervisor"].user_id, WorkspaceRole.MEMBER)
    _member(a_id, people["analyst"].user_id, WorkspaceRole.VIEWER)
    _member(a_id, people["agent"].user_id, WorkspaceRole.MEMBER)
    _assign(a_id, teams["big"], people["supervisor"].user_id, TeamRole.SUPERVISOR, owner.user_id)
    _assign(a_id, teams["big"], people["agent"].user_id, TeamRole.AGENT, owner.user_id)
    _member(b.workspace_id, people["supervisor"].user_id, WorkspaceRole.MEMBER)
    _assign(
        b.workspace_id,
        teams["epsilon"],
        people["supervisor"].user_id,
        TeamRole.SUPERVISOR,
        owner.user_id,
    )

    memberships = {
        "org_admin": _grant(owner, org, people["org_admin"], "admin"),
        "org_analyst": _grant(owner, org, people["org_analyst"], "analyst"),
        "org_executive": _grant(owner, org, people["org_executive"], "executive"),
        "org_narrow": _grant(owner, org, people["org_narrow"], "analyst"),
        "agent": _grant(owner, org, people["agent"], "analyst"),
    }
    grants = (
        ("org_analyst", None),
        ("org_executive", None),
        ("org_narrow", "mid"),
        ("agent", None),
    )
    for name, team in grants:
        payload = {"membership_id": memberships[name]["id"], "workspace_id": a_id}
        if team is not None:
            payload["team_id"] = teams[team]
        granted = owner.post(f"/api/v1/organizations/{org}/scope-grants", json=payload)
        assert granted.status_code == 201, granted.text

    keys = []
    for scopes in (["meeting:read"], ["meeting:create"]):
        minted = _mint(
            client,
            {"X-CSRF-Token": owner.cookies[CSRF_COOKIE], "X-Workspace-ID": a_id},
            owner.cookies,
            scopes=scopes,
        )
        assert minted.status_code == 201, minted.text
        keys.append(minted.json()["key"])
    client.cookies.clear()

    week_start = current_week_start(datetime.now(UTC))
    ref = Reference(
        client=client,
        organization_id=org,
        a=a,
        b=b,
        week_start=week_start,
        start=week_start - 3 * WEEK,
        teams=teams,
        agents=agents,
        people=people,
        memberships=memberships,
        reader_key=keys[0],
        writer_key=keys[1],
    )
    if conversations:
        seed_conversations(ref)
    return ref
