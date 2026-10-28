"""HOM-175: the demo workspace seed fills a fresh database with believable data.

The seed runs against the test database through the application engine (the
non-bypass role on PostgreSQL); these tests count the rows it wrote with the
inspection session, run it a second time to prove it is idempotent, log in
with the printed accounts and read the console endpoints. Production (and
every environment except development and test) is refused before any engine
or connection exists.

HOM-326: every demo meeting enters through the ingestion ledger, with a
``received_at`` in the 4 closed weeks before the current Asia/Tehran week, and
carries a team and an agent from ``MEETING_ATTRIBUTION``. Five extra agents
(no password, not logins) give the ``Call Center`` team 5 distinct agents a
week, so the demo workspace, that team and the trend views are released while
``Sales`` and ``Support`` (1 agent each) are withheld as small groups.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.database import demo_seed
from backend.database.demo_seed import seed_demo_workspace
from backend.database.saas_models import (
    ActionItem,
    ApiKey,
    AuditEvent,
    DuplicateMediaPolicy,
    Entitlement,
    EvidenceSpan,
    IngestionRecord,
    IngestionStatus,
    JobState,
    MediaObject,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    Membership,
    Organization,
    Plan,
    PlanVersion,
    ProcessingJob,
    ProcessingStageRun,
    Recording,
    RetentionMode,
    ShareLink,
    Subscription,
    SubscriptionStatus,
    Team,
    TeamAssignment,
    TeamRole,
    TranscriptSegment,
    UsageEvent,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
)
from backend.services.role_trend_service import current_week_start
from backend.tests._database import inspection_session_factory

PERSIAN = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")
ROLES = ("owner", "admin", "member", "viewer")
AGENT_KEYS = tuple(key for key, _name in demo_seed.DEMO_AGENTS)
# Workspace members of a clean run: the 4 logins and the 5 agents (role member).
MEMBERSHIPS = {"owner": 1, "admin": 1, "member": 1 + len(AGENT_KEYS), "viewer": 1}
INVITED = 3 + len(AGENT_KEYS)
UNREACHABLE_DATABASE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nothing"
HOMAYAR_ROOT = Path(__file__).resolve().parents[2]

# Every table the seed writes to, counted inside the demo workspace.
WORKSPACE_TABLES = (
    Membership,
    Team,
    TeamAssignment,
    Meeting,
    Recording,
    MediaObject,
    ProcessingJob,
    TranscriptSegment,
    MeetingSummary,
    ActionItem,
    EvidenceSpan,
    ProcessingStageRun,
    Subscription,
    Entitlement,
    UsageEvent,
    AuditEvent,
    ApiKey,
    ShareLink,
    IngestionRecord,
)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _counts(workspace_id: str) -> dict[str, int]:
    with inspection_session_factory()() as db:
        counts = {
            model.__tablename__: db.scalar(
                select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
            )
            for model in WORKSPACE_TABLES
        }
        counts["organizations"] = db.scalar(select(func.count()).select_from(Organization))
        counts["workspaces"] = db.scalar(select(func.count()).select_from(Workspace))
        counts["users"] = db.scalar(select(func.count()).select_from(User))
    return counts


def _planned_meeting_statuses() -> dict[str, int]:
    """Meeting statuses of a clean run, from the seed's own plan."""
    planned: dict[str, int] = {}
    for outcome, count in demo_seed.MEETING_PLAN:
        # A dead-lettered job shows its meeting as failed.
        status = "failed" if outcome == "dead_letter" else outcome
        planned[status] = planned.get(status, 0) + count
    return planned


def _planned_job_states() -> dict[str, int]:
    states = {"completed": "completed", "partial": "completed", "processing": "running"}
    planned: dict[str, int] = {}
    for outcome, count in demo_seed.MEETING_PLAN:
        state = states.get(outcome, outcome)
        planned[state] = planned.get(state, 0) + count
    return planned


def _state(workspace_id: str) -> dict[str, dict[str, int]]:
    """What a clean run leaves behind, grouped so an interrupted run can be compared."""
    with inspection_session_factory()() as db:

        def grouped(column, model) -> dict[str, int]:
            rows = db.execute(
                select(column, func.count())
                .where(model.workspace_id == workspace_id)
                .group_by(column)
            )
            return {getattr(key, "value", key): count for key, count in rows}

        invitations = list(
            db.scalars(
                select(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace_id)
            )
        )
        return {
            "meetings": grouped(Meeting.status, Meeting),
            "jobs": grouped(ProcessingJob.state, ProcessingJob),
            "memberships": grouped(Membership.role, Membership),
            "audit": grouped(AuditEvent.event_type, AuditEvent),
            "invitations": {
                "accepted": sum(1 for row in invitations if row.accepted_at is not None),
                "revoked": sum(1 for row in invitations if row.revoked_at is not None),
                "total": len(invitations),
            },
        }


def _assert_clean_run_state(workspace_id: str) -> None:
    state = _state(workspace_id)
    assert state["meetings"] == _planned_meeting_statuses()
    assert state["jobs"] == _planned_job_states()
    assert state["memberships"] == MEMBERSHIPS
    assert state["invitations"] == {"accepted": INVITED, "revoked": 0, "total": INVITED}
    audit = state["audit"]
    assert audit["member.invitation.created"] == INVITED
    assert audit["member.invitation.accepted"] == INVITED
    assert "member.invitation.revoked" not in audit
    planned = sum(count for _, count in demo_seed.MEETING_PLAN)
    assert audit["meeting.uploaded"] == planned
    assert audit["ingestion.accepted"] == planned
    assert "ingestion.replayed" not in audit
    assert "meeting.updated" not in audit
    assert audit["workspace.settings.updated"] == 1


def _fail_once(monkeypatch, target: str, *, on_call: int = 1) -> dict[str, int]:
    """Make ``target`` raise on its ``on_call``-th call, then behave normally."""
    module_name, _, attribute = target.rpartition(".")
    module = sys.modules[module_name]
    original = getattr(module, attribute)
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == on_call:
            raise RuntimeError("demo seed interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, attribute, flaky)
    return calls


def _login(client, account) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": account.email, "password": account.password},
    )
    assert response.status_code == 200, response.text


def _get(client, workspace_id: str, path: str) -> dict:
    response = client.get(path, headers={"X-Workspace-ID": workspace_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_seed_creates_a_complete_demo_workspace(client, settings):
    result = seed_demo_workspace(settings)

    with inspection_session_factory()() as db:
        # One organization and one workspace in total: no personal workspaces.
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert db.scalar(select(func.count()).select_from(Workspace)) == 1
        workspace = db.get(Workspace, result.workspace_id)
        assert workspace is not None
        assert workspace.organization_id == result.organization_id
        assert PERSIAN.search(workspace.name)

        live = list(
            db.scalars(
                select(Subscription).where(
                    Subscription.workspace_id == result.workspace_id,
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                    ),
                )
            )
        )
        assert live
        assert {subscription.plan_key for subscription in live} <= {"professional", "business"}
        # Pinned to the plan's current published version, as a grant is.
        for subscription in live:
            plan = db.get(Plan, subscription.plan_key)
            assert subscription.plan_version == plan.current_version
            assert db.get(PlanVersion, (plan.key, subscription.plan_version)) is not None

        # Accounts: one per role, reserved domain, verified and active.
        assert set(result.accounts) == set(ROLES)
        for role in ROLES:
            account = result.accounts[role]
            domain = account.email.rpartition("@")[2]
            assert domain == "example.com" or domain.endswith(".example.com"), account.email
            user = db.scalar(select(User).where(User.email == account.email))
            assert user is not None and user.is_active and user.email_verified_at is not None
            membership = db.scalar(
                select(Membership).where(
                    Membership.workspace_id == result.workspace_id,
                    Membership.user_id == user.id,
                    Membership.is_active.is_(True),
                )
            )
            assert membership is not None and membership.role == WorkspaceRole(role)

        # Teams: 2 or 3, each with a supervisor and an agent.
        teams = list(db.scalars(select(Team).where(Team.workspace_id == result.workspace_id)))
        assert 2 <= len(teams) <= 3
        for team in teams:
            roles = set(
                db.scalars(select(TeamAssignment.role).where(TeamAssignment.team_id == team.id))
            )
            assert {TeamRole.SUPERVISOR, TeamRole.AGENT} <= roles

        # Meetings: 25 to 40, every status the console shows.
        meetings = list(
            db.scalars(select(Meeting).where(Meeting.workspace_id == result.workspace_id))
        )
        assert 25 <= len(meetings) <= 40
        statuses = {meeting.status for meeting in meetings}
        assert {
            MeetingStatus.COMPLETED,
            MeetingStatus.PROCESSING,
            MeetingStatus.FAILED,
            MeetingStatus.PARTIAL,
        } <= statuses

        texts: list[str] = []
        for meeting in meetings:
            if meeting.status != MeetingStatus.COMPLETED:
                continue
            segments = list(
                db.scalars(
                    select(TranscriptSegment.text).where(TranscriptSegment.meeting_id == meeting.id)
                )
            )
            assert segments, meeting.title
            texts.extend(segments)
            for model in (MeetingSummary, ActionItem, EvidenceSpan, ProcessingStageRun):
                assert db.scalar(
                    select(func.count()).select_from(model).where(model.meeting_id == meeting.id)
                ), (model.__tablename__, meeting.title)
        assert any(PERSIAN.search(text) for text in texts)
        assert any(LATIN.search(text) for text in texts)

        # Placeholder media only.
        for model in (Recording, MediaObject):
            sizes = list(
                db.scalars(
                    select(model.size_bytes).where(model.workspace_id == result.workspace_id)
                )
            )
            assert sizes and max(sizes) <= 2_000_000

        assert db.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(
                ProcessingJob.workspace_id == result.workspace_id,
                ProcessingJob.state == JobState.DEAD_LETTER,
            )
        )

        # Usage: an event inside an entitlement's period, and a non-empty bar.
        now = datetime.now(UTC)
        entitlements = list(
            db.scalars(select(Entitlement).where(Entitlement.workspace_id == result.workspace_id))
        )
        assert any(entitlement.used_value > 0 for entitlement in entitlements)
        events = list(
            db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == result.workspace_id))
        )
        assert any(
            event.metric == entitlement.metric
            and _as_utc(entitlement.period_start) <= _as_utc(event.occurred_at) <= now
            and (
                entitlement.period_end is None
                or _as_utc(event.occurred_at) < _as_utc(entitlement.period_end)
            )
            for event in events
            for entitlement in entitlements
        )

        event_types = set(
            db.scalars(
                select(AuditEvent.event_type).where(AuditEvent.workspace_id == result.workspace_id)
            )
        )
        for prefix in ("member.", "meeting.", "api_key."):
            assert any(name.startswith(prefix) for name in event_types), prefix
        assert "workspace.settings.updated" in event_types
        # The audited settings change is a real, neutral one: a longer retention
        # than the default. Uploading the same file twice still works.
        assert workspace.retention_mode == RetentionMode.FINITE
        assert workspace.retention_days != settings.retention_days_default
        assert workspace.duplicate_media_policy == DuplicateMediaPolicy.ALLOW
        audited = db.scalar(
            select(AuditEvent.details).where(
                AuditEvent.workspace_id == result.workspace_id,
                AuditEvent.event_type == "workspace.settings.updated",
            )
        )
        assert audited == {"fields": "retention_days"}

        api_keys = db.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.workspace_id == result.workspace_id)
        )
        assert 1 <= api_keys <= 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(ShareLink)
                .where(ShareLink.workspace_id == result.workspace_id)
            )
            >= 2
        )


def test_seed_is_idempotent(client, settings):
    first = seed_demo_workspace(settings)
    before = _counts(first.workspace_id)

    second = seed_demo_workspace(settings)

    assert _counts(first.workspace_id) == before
    assert second.workspace_id == first.workspace_id
    assert second.organization_id == first.organization_id
    assert {role: account.email for role, account in second.accounts.items()} == {
        role: account.email for role, account in first.accounts.items()
    }


def test_demo_accounts_log_in_and_console_pages_have_data(client, settings):
    result = seed_demo_workspace(settings)
    workspace_id = result.workspace_id

    for role in ("viewer", "member", "admin", "owner"):
        _login(client, result.accounts[role])
    # The last login (owner) is the session the client now carries.
    meetings = _get(client, workspace_id, "/api/v1/meetings")
    assert meetings["items"]
    assert _get(client, workspace_id, "/api/v1/members")["items"]
    assert _get(client, workspace_id, "/api/v1/billing/usage")["items"]
    assert _get(client, workspace_id, "/api/v1/admin/audit-events")["items"]
    _get(client, workspace_id, "/api/v1/admin/workspace-settings")

    with inspection_session_factory()() as db:
        completed = list(
            db.scalars(
                select(Meeting.id).where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.status == MeetingStatus.COMPLETED,
                )
            )
        )
    assert completed
    for meeting_id in completed:
        quality = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
        assert quality["transcription"] != "unavailable"
        assert quality["analysis"] != "unavailable"
        assert quality["segment_count"] >= 1


def test_clean_run_matches_the_plan(client, settings):
    result = seed_demo_workspace(settings)

    _assert_clean_run_state(result.workspace_id)


def test_rerun_after_an_interrupted_invitation_matches_a_clean_run(client, settings, monkeypatch):
    import backend.services.member_service  # noqa: F401 - the module the seed calls

    _fail_once(monkeypatch, "backend.services.member_service.accept_invitation", on_call=2)
    with pytest.raises(RuntimeError, match="interrupted"):
        seed_demo_workspace(settings)
    monkeypatch.undo()

    result = seed_demo_workspace(settings)

    _assert_clean_run_state(result.workspace_id)


def test_rerun_after_an_interrupted_meeting_matches_a_clean_run(client, settings, monkeypatch):
    import backend.jobs.processor  # noqa: F401 - the module the seed calls

    # The 5th process_job call belongs to a meeting planned as completed.
    _fail_once(monkeypatch, "backend.jobs.processor.process_job", on_call=5)
    with pytest.raises(RuntimeError, match="interrupted"):
        seed_demo_workspace(settings)
    monkeypatch.undo()

    result = seed_demo_workspace(settings)

    _assert_clean_run_state(result.workspace_id)
    before = _counts(result.workspace_id)
    seed_demo_workspace(settings)
    assert _counts(result.workspace_id) == before


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_seed_refuses_outside_development_and_test(client, settings, environment):
    refused = settings.model_copy(
        update={"environment": environment, "database_url": UNREACHABLE_DATABASE}
    )

    with pytest.raises(RuntimeError, match=environment):
        seed_demo_workspace(refused)

    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("value", ["production", "PRODUCTION", " production "])
def test_main_refuses_production_before_settings_or_database(client, monkeypatch, capsys, value):
    monkeypatch.setenv("ENVIRONMENT", value)
    # Settings() would not even validate: production needs long secrets.
    monkeypatch.setenv("JWT_SECRET", "short")
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)

    code = demo_seed.main([])

    assert isinstance(code, int) and code != 0
    assert "production" in capsys.readouterr().err
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0


def test_main_refuses_a_lowercase_production_variable(client, monkeypatch, capsys):
    # Settings read the variable in any case; so does the refusal.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("environment", "production")
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)

    code = demo_seed.main([])

    assert isinstance(code, int) and code != 0
    assert "production" in capsys.readouterr().err
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0


def test_main_reports_invalid_settings_without_a_traceback(client, monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "not-a-number")

    code = demo_seed.main([])

    assert code == 1
    err = capsys.readouterr().err
    assert "cannot read its settings" in err
    assert "Traceback" not in err
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0


def test_module_entrypoint_exits_non_zero_in_production():
    env = {
        **os.environ,
        "ENVIRONMENT": "production",
        "JWT_SECRET": "short",
        "DATABASE_URL": UNREACHABLE_DATABASE,
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
        [sys.executable, "-m", "backend.database.demo_seed"],
        cwd=HOMAYAR_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode != 0
    assert "production" in completed.stderr


def test_main_seeds_in_development_and_prints_logins(client, settings, monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("DATABASE_APP_ROLE", settings.database_app_role)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", settings.storage_local_root)
    monkeypatch.setenv("FRONTEND_URL", "http://testserver/app")

    code = demo_seed.main([])

    output = capsys.readouterr().out
    assert code == 0
    with inspection_session_factory()() as db:
        emails = set(db.scalars(select(User.email)))
    logins = {f"{role}@{demo_seed.DEMO_EMAIL_DOMAIN}" for role in ROLES}
    agents = {f"{key}@{demo_seed.DEMO_EMAIL_DOMAIN}" for key in AGENT_KEYS}
    assert emails == logins | agents
    for email in logins:
        assert email in output
    # The agents are not logins: never printed.
    for email in agents:
        assert email not in output
    assert output.count(demo_seed.DEMO_PASSWORD) == len(ROLES)
    assert demo_seed.DEMO_PASSWORD in output
    assert "http://testserver/app" in output


# --- HOM-326: ledger rows, agents, weeks and the trend views -------------------------------


INCLUDED_STATUSES = (MeetingStatus.COMPLETED, MeetingStatus.PARTIAL)


def _team_names(db, workspace_id: str) -> dict[str, str]:
    rows = db.execute(select(Team.id, Team.name).where(Team.workspace_id == workspace_id))
    return {team_id: name for team_id, name in rows}


def test_attribution_plan_covers_every_meeting():
    plan = demo_seed.MEETING_ATTRIBUTION
    assert len(plan) == sum(count for _, count in demo_seed.MEETING_PLAN)
    team_names = {name for name, _supervisor, _agents in demo_seed.TEAMS}
    agents_of = {name: set(agents) for name, _supervisor, agents in demo_seed.TEAMS}
    for team, agent, weeks_back in plan:
        assert team is None or team in team_names
        # A team meeting's agent is an agent of that team.
        if team is not None and agent is not None:
            assert agent in agents_of[team]
        assert 1 <= weeks_back <= 4


def test_every_meeting_has_one_accepted_ledger_row(client, settings):
    result = seed_demo_workspace(settings)

    with inspection_session_factory()() as db:
        meetings = list(
            db.scalars(select(Meeting).where(Meeting.workspace_id == result.workspace_id))
        )
        assert len(meetings) == 30
        for meeting in meetings:
            rows = list(
                db.scalars(select(IngestionRecord).where(IngestionRecord.meeting_id == meeting.id))
            )
            assert len(rows) == 1, meeting.title
            row = rows[0]
            assert row.status == IngestionStatus.ACCEPTED
            assert row.job_id is not None
            job = db.get(ProcessingJob, row.job_id)
            assert job is not None and job.meeting_id == meeting.id
            assert row.source_provider == "console"
            assert row.media_hash == meeting.recording.checksum_sha256
            audits = db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.workspace_id == result.workspace_id,
                    AuditEvent.event_type == "ingestion.accepted",
                    AuditEvent.target_id == row.id,
                )
            )
            assert audits == 1


def test_ledger_rows_fall_in_the_four_closed_weeks(client, settings):
    result = seed_demo_workspace(settings)
    week_start = current_week_start(datetime.now(UTC))

    with inspection_session_factory()() as db:
        rows = db.execute(
            select(Meeting.title, IngestionRecord.received_at)
            .join(IngestionRecord, IngestionRecord.meeting_id == Meeting.id)
            .where(Meeting.workspace_id == result.workspace_id)
        ).all()
    titles = {title: index for index, _outcome, _script, title in demo_seed._meeting_specs()}
    weeks: dict[int, int] = {}
    for title, received_at in rows:
        received_at = _as_utc(received_at)
        assert week_start - timedelta(days=28) <= received_at < week_start, title
        # A pure function of the meeting index and the week start.
        assert received_at == demo_seed._received_at(titles[title], week_start), title
        week = (week_start - received_at) // timedelta(days=7) + 1
        weeks[week] = weeks.get(week, 0) + 1
    assert len(weeks) >= 3
    assert sum(weeks.values()) == 30


def test_meetings_carry_the_planned_team_and_agent(client, settings):
    result = seed_demo_workspace(settings)

    with inspection_session_factory()() as db:
        names = _team_names(db, result.workspace_id)
        emails = {user_id: email for user_id, email in db.execute(select(User.id, User.email))}
        planned = {
            title: demo_seed.MEETING_ATTRIBUTION[index]
            for index, _outcome, _script, title in demo_seed._meeting_specs()
        }
        meetings = list(
            db.scalars(select(Meeting).where(Meeting.workspace_id == result.workspace_id))
        )
        for meeting in meetings:
            team, agent, _weeks_back = planned[meeting.title]
            assert names.get(meeting.team_id) == team, meeting.title
            expected = f"{agent}@{demo_seed.DEMO_EMAIL_DOMAIN}" if agent else None
            assert emails.get(meeting.agent_user_id) == expected, meeting.title
            if meeting.agent_user_id is None:
                continue
            # The agent is an effective member, and holds the agent role in the team.
            assert db.scalar(
                select(Membership.id).where(
                    Membership.workspace_id == result.workspace_id,
                    Membership.user_id == meeting.agent_user_id,
                    Membership.is_active.is_(True),
                )
            )
            if meeting.team_id is not None:
                assert db.scalar(
                    select(TeamAssignment.id).where(
                        TeamAssignment.team_id == meeting.team_id,
                        TeamAssignment.user_id == meeting.agent_user_id,
                        TeamAssignment.role == TeamRole.AGENT,
                        TeamAssignment.revoked_at.is_(None),
                    )
                ), meeting.title

        # The released team has 5 distinct agents among its included meetings.
        call_center = next(team_id for team_id, name in names.items() if name == "Call Center")
        agents = set(
            db.scalars(
                select(Meeting.agent_user_id).where(
                    Meeting.team_id == call_center, Meeting.status.in_(INCLUDED_STATUSES)
                )
            )
        )
        assert len(agents) == 5


def test_agents_are_members_without_a_password_and_cannot_log_in(client, settings):
    result = seed_demo_workspace(settings)

    with inspection_session_factory()() as db:
        for key, name in demo_seed.DEMO_AGENTS:
            user = db.scalar(
                select(User).where(User.email == f"{key}@{demo_seed.DEMO_EMAIL_DOMAIN}")
            )
            assert user is not None and user.password_hash is None
            assert user.display_name == name
            membership = db.scalar(
                select(Membership).where(
                    Membership.workspace_id == result.workspace_id,
                    Membership.user_id == user.id,
                )
            )
            assert membership is not None and membership.role == WorkspaceRole.MEMBER
    assert set(result.accounts) == set(ROLES)

    refused = client.post(
        "/api/v1/auth/login",
        json={
            "identifier": f"{AGENT_KEYS[0]}@{demo_seed.DEMO_EMAIL_DOMAIN}",
            "password": demo_seed.DEMO_PASSWORD,
        },
    )
    assert refused.status_code == 401, refused.text
    _login(client, result.accounts["owner"])


def test_trend_views_release_the_demo_workspace(client, settings):
    result = seed_demo_workspace(settings)
    org, workspace_id = result.organization_id, result.workspace_id
    _login(client, result.accounts["owner"])

    # Organization overview: released, every meeting counted.
    response = client.get(f"/api/v1/organizations/{org}/aggregates")
    assert response.status_code == 200, response.text
    (item,) = response.json()["items"]
    assert item["disclosure"]["status"] == "released"
    assert item["meetings_total"] == 30
    assert sum(item["meetings_by_status"].values()) == 30

    # Drill-down: Call Center released, the 1-agent teams withheld as small groups.
    response = client.get(f"/api/v1/organizations/{org}/workspaces/{workspace_id}/trends/issues")
    assert response.status_code == 200, response.text
    drilldown = response.json()
    assert drilldown["workspace"]["disclosure"]["status"] == "released"
    teams = {block["team"]["name"]: block["disclosure"] for block in drilldown["teams"]}
    assert teams["Call Center"]["status"] == "released"
    for name in ("Sales", "Support"):
        assert teams[name]["status"] == "withheld", name
        assert teams[name]["reason"] == "small_group", name

    # Team comparison: Call Center released with data; the small teams are agent cohorts.
    teams_view = _get(client, workspace_id, "/api/v1/trends/teams")
    blocks = {block["team"]["name"]: block for block in teams_view["teams"]}
    call_center = blocks["Call Center"]
    assert call_center["disclosure"]["status"] == "released"
    assert call_center["disclosure"]["kind"] == "rate"
    assert call_center["population"]["included"] > 0
    for name in ("Sales", "Support"):
        assert blocks[name]["disclosure"]["kind"] == "agent", name

    # Workspace trends: a population, not withheld.
    issues = _get(client, workspace_id, "/api/v1/trends/issues")
    assert issues["population"]["included"] > 0
    assert issues["disclosure"]["status"] == "released"


# --- a database seeded by the seed before HOM-326 ------------------------------------------


def _seed_as_before_hom326(settings, monkeypatch) -> str:
    """Seed the way the seed did before HOM-326: no agents, the 2 old teams, no team or
    agent on any meeting, and no ingestion ledger row (``accept_ingestion`` only runs
    ``create``)."""
    from backend.services import ingestion_service

    def without_ledger(_db, *, create, **_kwargs):
        record = SimpleNamespace(id=None)
        return ingestion_service.IngestionOutcome(
            ingestion_service.IngestionDecision.ACCEPTED, record, created=create(record)
        )

    with monkeypatch.context() as patch:
        patch.setattr(demo_seed, "DEMO_AGENTS", ())
        patch.setattr(demo_seed, "TEAMS", demo_seed.TEAMS[:2])
        patch.setattr(
            demo_seed,
            "MEETING_ATTRIBUTION",
            ((None, None, 1),) * len(demo_seed.MEETING_ATTRIBUTION),
        )
        patch.setattr(ingestion_service, "accept_ingestion", without_ledger)
        result = seed_demo_workspace(settings)
    with inspection_session_factory()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(IngestionRecord)
                .where(IngestionRecord.workspace_id == result.workspace_id)
            )
            == 0
        )
    return result.workspace_id


def test_seed_refuses_a_database_seeded_before_the_ledger(client, settings, monkeypatch):
    workspace_id = _seed_as_before_hom326(settings, monkeypatch)
    before = _counts(workspace_id)

    with pytest.raises(demo_seed.DemoSeedRefused, match="older demo seed") as refused:
        seed_demo_workspace(settings)

    assert "Reset the database" in str(refused.value)
    # Nothing new: no agent, member, team, assignment, meeting, ledger row or audit event.
    assert _counts(workspace_id) == before


def test_main_reports_a_database_seeded_before_the_ledger(client, settings, monkeypatch, capsys):
    _seed_as_before_hom326(settings, monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("DATABASE_APP_ROLE", settings.database_app_role)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", settings.storage_local_root)

    code = demo_seed.main([])

    assert code != 0
    captured = capsys.readouterr()
    assert "older demo seed" in captured.err
    assert "Reset the database" in captured.err
    assert "Traceback" not in captured.err
    assert demo_seed.DEMO_PASSWORD not in captured.out
