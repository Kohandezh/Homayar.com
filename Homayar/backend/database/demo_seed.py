"""Demo workspace seed (HOM-175): believable SaaS data for sales demos.

``python -m backend.database.demo_seed`` (``make demo-seed``) fills a fresh
database with one organization, one workspace on a paid plan, one account per
workspace role, two teams, about thirty meetings in every status the product
console shows, a dead-lettered job, usage, API keys, share links and audit
history. Nothing comes from real people or real audio: accounts are on the
reserved ``example.com`` domain, recordings are a few seconds of generated
silence, and transcripts come from scripted in-process providers marked
``is_mock``.

Every row is written through the existing services (or, where no service
exists, the same model writes the bootstrap code uses) on the application
engine, so row-level security and model invariants apply exactly as for a
request. Meetings are processed by the real job processor.

The seed is idempotent: each object has a fixed natural key and is skipped
when it already exists, and audit events are only written together with the
object they describe, so a second run writes nothing.

It refuses every environment except development and test before it opens a
database connection.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.config import Settings
    from backend.database.saas_models import Meeting, User, Workspace

ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})

DEMO_ORGANIZATION_SLUG = "homayar-demo"
DEMO_WORKSPACE_SLUG = "default"
DEMO_WORKSPACE_NAME = "شرکت نمونه همایار"
DEMO_PLAN_KEY = "business"
DEMO_EMAIL_DOMAIN = "demo.example.com"
# Readable and low-entropy on purpose (secret scanners, sales demos).
DEMO_PASSWORD = "Homayar-Demo-1405"  # noqa: S105 - public demo credential
DEMO_ROLES = ("owner", "admin", "member", "viewer")
DEMO_DISPLAY_NAMES = {
    "owner": "سارا احمدی",
    "admin": "Reza Karimi",
    "member": "مریم حسینی",
    "viewer": "Ali Rahimi",
}
AUDIO_SECONDS_LIMIT = 36_000
CONCURRENT_JOBS_LIMIT = 10
DEMO_RETENTION_DAYS = 180
SAMPLE_RATE_HZ = 8_000
WORKER_ID = "demo-seed"
API_KEY_NAME = "CRM integration (demo)"
API_KEY_SCOPES = ("meeting:read", "meeting:create")
SCRIPTED_PROVIDER_VERSION = "demo-seed-v1"


class DemoSeedRefused(RuntimeError):
    """The seed does not run in this environment."""


@dataclass(frozen=True)
class DemoAccount:
    role: str
    email: str
    password: str
    display_name: str


@dataclass(frozen=True)
class DemoSeedResult:
    workspace_id: str
    organization_id: str
    workspace_name: str
    accounts: Mapping[str, DemoAccount]
    meeting_count: int
    console_url: str | None


# --- scripted content ------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


@dataclass(frozen=True)
class Script:
    language: str
    turns: tuple[Turn, ...]
    summary: str
    actions: tuple[tuple[str, str, int], ...]  # description, owner, source turn
    decision: tuple[str, int]  # description, source turn


SCRIPTS: tuple[Script, ...] = (
    Script(
        language="fa",
        turns=(
            Turn("SPEAKER_00", "سلام، برای پیگیری سفارش شمارهٔ دوازده تماس گرفتم."),
            Turn("SPEAKER_01", "سلام، سفارش شما فردا ارسال می‌شود."),
            Turn("SPEAKER_00", "لطفاً کد رهگیری را هم پیامک کنید."),
            Turn("SPEAKER_01", "حتماً، تا پایان امروز ارسال می‌کنم."),
        ),
        summary="مشتری وضعیت سفارش را پرسید؛ ارسال فردا انجام می‌شود.",
        actions=(("ارسال کد رهگیری با پیامک", "پشتیبانی", 3),),
        decision=("ارسال سفارش فردا", 1),
    ),
    Script(
        language="en",
        turns=(
            Turn("SPEAKER_00", "Thanks for joining the quarterly pricing review."),
            Turn("SPEAKER_01", "We propose a ten percent discount for annual plans."),
            Turn("SPEAKER_00", "Finance must approve it before Friday."),
            Turn("SPEAKER_01", "I will send the proposal to finance today."),
        ),
        summary="The team reviewed annual pricing and a ten percent discount.",
        actions=(("Send the discount proposal to finance", "Sales", 3),),
        decision=("Offer a ten percent annual discount", 1),
    ),
    Script(
        language="fa",
        turns=(
            Turn("SPEAKER_00", "جلسهٔ هفتگی تیم فروش را شروع کنیم."),
            Turn("SPEAKER_01", "این هفته هشت قرارداد جدید بستیم."),
            Turn("SPEAKER_00", "عالی است. گزارش را برای مدیریت بفرستید."),
            Turn("SPEAKER_01", "گزارش را پنجشنبه آماده می‌کنم."),
        ),
        summary="تیم فروش هشت قرارداد جدید بست و گزارش هفتگی آماده می‌شود.",
        actions=(("آماده‌سازی گزارش هفتگی فروش", "فروش", 3),),
        decision=("ارسال گزارش به مدیریت", 2),
    ),
    Script(
        language="en",
        turns=(
            Turn("SPEAKER_00", "Hello, I cannot log in to the customer portal."),
            Turn("SPEAKER_01", "I reset your password; please check your email."),
            Turn("SPEAKER_00", "It works now, thank you."),
            Turn("SPEAKER_01", "Great, I will close the ticket."),
        ),
        summary="A customer could not log in; support reset the password.",
        actions=(("Close the login support ticket", "Support", 3),),
        decision=("Reset the customer password", 1),
    ),
    Script(
        language="fa",
        turns=(
            Turn("SPEAKER_00", "مشتری از تأخیر در نصب دستگاه ناراضی است."),
            Turn("SPEAKER_01", "تکنسین فردا صبح به محل اعزام می‌شود."),
            Turn("SPEAKER_00", "یک تخفیف جبرانی هم پیشنهاد بدهیم."),
            Turn("SPEAKER_01", "موافقم، ده درصد تخفیف ثبت می‌کنم."),
        ),
        summary="برای جبران تأخیر نصب، تکنسین اعزام و تخفیف ثبت می‌شود.",
        actions=(("اعزام تکنسین برای نصب", "خدمات فنی", 1), ("ثبت تخفیف ده درصدی", "مالی", 3)),
        decision=("پیشنهاد تخفیف جبرانی", 2),
    ),
    Script(
        language="en",
        turns=(
            Turn("SPEAKER_00", "Let us plan the onboarding for the new branch."),
            Turn("SPEAKER_01", "Training starts next Monday for twelve agents."),
            Turn("SPEAKER_00", "Please share the training schedule with HR."),
            Turn("SPEAKER_01", "Sure, I will share it this afternoon."),
        ),
        summary="Onboarding for twelve agents in the new branch starts Monday.",
        actions=(("Share the training schedule with HR", "Operations", 3),),
        decision=("Start training next Monday", 1),
    ),
)

TITLES: tuple[str, ...] = (
    "پیگیری سفارش مشتری",
    "Quarterly pricing review",
    "جلسهٔ هفتگی تیم فروش",
    "Customer portal login issue",
    "شکایت از تأخیر نصب",
    "New branch onboarding",
)

# (status, how many). 30 meetings in total.
MEETING_PLAN: tuple[tuple[str, int], ...] = (
    ("completed", 20),
    ("partial", 3),
    ("failed", 2),
    ("dead_letter", 1),
    ("processing", 2),
    ("queued", 2),
)


def _meeting_specs() -> list[tuple[int, str, Script, str]]:
    specs: list[tuple[int, str, Script, str]] = []
    index = 0
    for outcome, count in MEETING_PLAN:
        for _ in range(count):
            script = SCRIPTS[index % len(SCRIPTS)]
            title = f"{TITLES[index % len(TITLES)]} #{index + 1}"
            specs.append((index, outcome, script, title))
            index += 1
    return specs


def _duration_seconds(index: int) -> int:
    return 20 + (index * 7) % 40


def _silent_wav(seconds: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE_HZ)
        writer.writeframes(b"\x00\x00" * (SAMPLE_RATE_HZ * seconds))
    return buffer.getvalue()


# --- scripted providers -------------------------------------------------------


def _scripted_bundle(script: Script, *, duration_seconds: int, outcome: str):
    """In-process providers that return ``script`` (no network, no models).

    ``partial`` leaves diarization unavailable (speakers unknown); ``failed``
    makes speech recognition refuse the recording permanently.
    """
    from backend.providers.base import (
        ASRProvider,
        DiarizationProvider,
        IntelligenceProvider,
        ProviderError,
        ProviderIdentity,
        text_digest,
    )
    from backend.providers.factory import ProviderBundle
    from backend.providers.schemas import (
        ActionItem as ProviderActionItem,
    )
    from backend.providers.schemas import (
        ASRRequest,
        ASRResult,
        ASRSegment,
        DiarizationRequest,
        DiarizationResult,
        DiarizationSegment,
        DiarizationStatus,
        IntelligenceInsight,
        IntelligenceRequest,
        IntelligenceResult,
        ProviderIssue,
    )

    step = duration_seconds / len(script.turns)
    times = [(round(i * step, 2), round((i + 1) * step, 2)) for i in range(len(script.turns))]
    script_digest = text_digest("\n".join(turn.text for turn in script.turns))

    class ScriptedASR(ASRProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_asr",
                model=SCRIPTED_PROVIDER_VERSION,
                params={"script_sha256": script_digest, "outcome": outcome},
            )

        async def transcribe(self, request: ASRRequest) -> ASRResult:
            if outcome == "failed":
                raise ProviderError(
                    "asr_input_rejected",
                    "Speech recognition could not read this recording",
                    retryable=False,
                )
            return ASRResult(
                provider="demo_seed_asr",
                model=SCRIPTED_PROVIDER_VERSION,
                language=script.language,
                text=" ".join(turn.text for turn in script.turns),
                segments=[
                    ASRSegment(
                        index=i,
                        text=turn.text,
                        start_seconds=start,
                        end_seconds=end,
                        confidence=0.9,
                    )
                    for i, (turn, (start, end)) in enumerate(zip(script.turns, times, strict=True))
                ],
                duration_seconds=duration_seconds,
                is_mock=True,
            )

    class ScriptedDiarization(DiarizationProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_diarization",
                model=SCRIPTED_PROVIDER_VERSION,
                params={"available": outcome != "partial"},
            )

        async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
            if outcome == "partial":
                return DiarizationResult(
                    provider="demo_seed_diarization",
                    status=DiarizationStatus.UNAVAILABLE,
                    issue=ProviderIssue(
                        code="diarization_unavailable",
                        message="Speaker separation was not available for this recording",
                        retryable=False,
                    ),
                    is_mock=True,
                )
            return DiarizationResult(
                provider="demo_seed_diarization",
                status=DiarizationStatus.AVAILABLE,
                segments=[
                    DiarizationSegment(
                        speaker_label=turn.speaker, start_seconds=start, end_seconds=end
                    )
                    for turn, (start, end) in zip(script.turns, times, strict=True)
                ],
                is_mock=True,
            )

    class ScriptedIntelligence(IntelligenceProvider):
        def identity(self) -> ProviderIdentity:
            return ProviderIdentity(
                provider="demo_seed_intelligence",
                model=SCRIPTED_PROVIDER_VERSION,
                params={"script_sha256": script_digest},
            )

        async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
            decision, decision_turn = script.decision
            return IntelligenceResult(
                provider="demo_seed_intelligence",
                model=SCRIPTED_PROVIDER_VERSION,
                summary=script.summary,
                summary_source_turn_indexes=list(range(len(request.turns))),
                action_items=[
                    ProviderActionItem(
                        description=description, owner=owner, source_turn_indexes=[turn]
                    )
                    for description, owner, turn in script.actions
                ],
                insights=[
                    IntelligenceInsight(
                        category="decision",
                        description=decision,
                        confidence=0.85,
                        source_turn_indexes=[decision_turn],
                    )
                ],
                is_mock=True,
            )

    return ProviderBundle(
        asr=ScriptedASR(),
        diarization=ScriptedDiarization(),
        intelligence=ScriptedIntelligence(),
    )


# --- environment and database --------------------------------------------------


def _refuse_unless_allowed(environment: str) -> None:
    if environment not in ALLOWED_ENVIRONMENTS:
        raise DemoSeedRefused(
            f"The demo seed refuses to run in the {environment!r} environment; "
            "it only runs in development or test."
        )


def _ensure_database(settings: Settings) -> None:
    """Reuse the configured engine when it already points at this database
    (the app is running, as in tests); otherwise configure one."""
    from sqlalchemy.engine import make_url

    from backend.database import relational

    engine = relational._engine
    target = make_url(settings.database_url)
    if engine is None or engine.url.render_as_string(hide_password=False) != (
        target.render_as_string(hide_password=False)
    ):
        relational.configure_database(settings)


def _session(*, user_id: str | None = None, workspace: Workspace | None = None) -> Session:
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context, bind_user_context

    db = get_session_factory()()
    if user_id is not None:
        bind_user_context(db, user_id)
    if workspace is not None:
        bind_tenant_context(
            db, workspace_id=workspace.id, organization_id=workspace.organization_id
        )
    return db


# --- steps ---------------------------------------------------------------------


def _accounts() -> dict[str, DemoAccount]:
    return {
        role: DemoAccount(
            role=role,
            email=f"{role}@{DEMO_EMAIL_DOMAIN}",
            password=DEMO_PASSWORD,
            display_name=DEMO_DISPLAY_NAMES[role],
        )
        for role in DEMO_ROLES
    }


def _ensure_users(accounts: Mapping[str, DemoAccount]) -> dict[str, User]:
    """Verified, active password accounts without personal workspaces
    (the pattern of ``ensure_development_demo_account``)."""
    from sqlalchemy import select

    from backend.database.saas_models import AuthIdentity, IdentityProvider, User, utcnow
    from backend.security.tokens import hash_password

    users: dict[str, User] = {}
    with _session() as db:
        for role, account in accounts.items():
            user = db.scalar(select(User).where(User.email == account.email))
            if user is None:
                user = User(
                    email=account.email,
                    display_name=account.display_name,
                    password_hash=hash_password(account.password),
                    locale="fa",
                    email_verified_at=datetime.now(UTC),
                    is_active=True,
                )
                db.add(user)
                db.flush()
                db.add(
                    AuthIdentity(
                        user_id=user.id,
                        provider=IdentityProvider.PASSWORD,
                        subject=account.email,
                        verified_at=utcnow(),
                        profile={"demo_seed": True},
                    )
                )
            users[role] = user
        db.commit()
    return users


def _find_workspace(owner: User) -> Workspace | None:
    from backend.services.tenancy_service import list_user_workspaces

    with _session(user_id=owner.id) as db:
        for workspace, membership in list_user_workspaces(db, owner.id):
            if (
                workspace.organization.slug == DEMO_ORGANIZATION_SLUG
                and membership.role.value == "owner"
            ):
                return workspace
    return None


def _demo_retention_days(settings: Settings) -> int:
    """A retention period that differs from the default the workspace starts with."""
    if settings.retention_days_default == DEMO_RETENTION_DAYS:
        return 365
    return DEMO_RETENTION_DAYS


def _ensure_workspace(owner: User, settings: Settings) -> Workspace:
    """The demo organization and workspace on the paid plan (the pattern of
    ``create_personal_workspace``, with a fixed slug and no free trial)."""
    from backend.database.saas_models import (
        Entitlement,
        Membership,
        Organization,
        Plan,
        PlanVersion,
        Subscription,
        SubscriptionStatus,
        Workspace,
        WorkspaceRole,
        new_id,
    )
    from backend.database.tenant_context import bind_tenant_context
    from backend.services.admin_service import update_workspace_settings
    from backend.services.audit_service import record_audit_event
    from backend.services.organization_service import bootstrap_owner_membership
    from backend.services.tenancy_service import ensure_plan_catalog

    existing = _find_workspace(owner)
    if existing is not None:
        return existing

    now = datetime.now(UTC)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_end = (period_start + timedelta(days=32)).replace(day=1)
    with _session(user_id=owner.id) as db:
        ensure_plan_catalog(db, settings)
        organization = Organization(
            id=new_id(), name=DEMO_WORKSPACE_NAME, slug=DEMO_ORGANIZATION_SLUG, status="active"
        )
        workspace = Workspace(
            id=new_id(),
            organization=organization,
            name=DEMO_WORKSPACE_NAME,
            slug=DEMO_WORKSPACE_SLUG,
            status="active",
            retention_days=settings.retention_days_default,
        )
        # Ids are known before the first write, so row-level security WITH
        # CHECK sees the new tenant.
        bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
        db.add(organization)
        db.flush()
        db.add(Membership(workspace=workspace, user=owner, role=WorkspaceRole.OWNER))
        db.flush()
        bootstrap_owner_membership(db, organization=organization, user=owner)
        # Pinned to the plan's current published version, as a grant is
        # (``tenancy_service._grant_demo``); no plan version is written here.
        plan = db.get(Plan, DEMO_PLAN_KEY)
        version = db.get(PlanVersion, (plan.key, plan.current_version))
        if version is None:
            raise RuntimeError(f"The {DEMO_PLAN_KEY!r} plan has no current published version")
        db.add(
            Subscription(
                workspace_id=workspace.id,
                plan_key=plan.key,
                plan_version=version.version,
                status=SubscriptionStatus.ACTIVE,
                provider="internal",
                starts_at=period_start,
            )
        )
        terms = version.entitlements_json if isinstance(version.entitlements_json, dict) else {}
        for metric, demo_limit in (
            ("audio_seconds", AUDIO_SECONDS_LIMIT),
            ("concurrent_jobs", CONCURRENT_JOBS_LIMIT),
        ):
            # The version's own term when it defines one; the catalog's paid
            # plans define none, so the demo allowance fills the gap.
            limit = terms.get(metric, demo_limit)
            db.add(
                Entitlement(
                    workspace_id=workspace.id,
                    metric=metric,
                    source=DEMO_PLAN_KEY,
                    limit_value=limit,
                    used_value=0,
                    reserved_value=0,
                    period_start=period_start,
                    period_end=period_end,
                )
            )
        db.flush()
        _, updated_fields = update_workspace_settings(
            db,
            workspace_id=workspace.id,
            # A real, neutral change (a longer retention than the default),
            # recorded once with the workspace it belongs to.
            changes={"retention_days": _demo_retention_days(settings)},
        )
        record_audit_event(
            db,
            event_type="workspace.settings.updated",
            actor_user_id=owner.id,
            workspace_id=workspace.id,
            target_type="workspace",
            target_id=workspace.id,
            details={"fields": ",".join(updated_fields)},
            settings=settings,
        )
        db.commit()
    found = _find_workspace(owner)
    assert found is not None
    return found


def _ensure_members(workspace: Workspace, users: Mapping[str, User], settings: Settings) -> None:
    """Admin, member and viewer join through the real invitation flow.

    The invitation is created and accepted in one transaction: a run that
    stops in between leaves no pending invitation (whose raw token would be
    lost) behind, so the next run starts from the same state as a clean one.
    """
    from sqlalchemy import select

    from backend.database.saas_models import Membership, WorkspaceRole
    from backend.services.audit_service import record_audit_event
    from backend.services.member_service import accept_invitation, create_invitation

    owner = users["owner"]
    for role in ("admin", "member", "viewer"):
        user = users[role]
        with _session(user_id=owner.id, workspace=workspace) as db:
            joined = db.scalar(
                select(Membership.id).where(
                    Membership.workspace_id == workspace.id, Membership.user_id == user.id
                )
            )
            if joined is not None:
                continue
            issued = create_invitation(
                db,
                workspace_id=workspace.id,
                invited_by_user_id=owner.id,
                actor_role=WorkspaceRole.OWNER,
                email=user.email,
                role=WorkspaceRole(role),
                expires_in_days=7,
                settings=settings,
            )
            record_audit_event(
                db,
                event_type="member.invitation.created",
                actor_user_id=owner.id,
                workspace_id=workspace.id,
                target_type="workspace_invitation",
                target_id=issued.invitation.id,
                details={"role": issued.invitation.role.value},
                settings=settings,
            )
            # From here the session acts as the invitee, as the accept route does.
            accepted = accept_invitation(
                db, raw_token=issued.raw_token, user=db.merge(user), settings=settings
            )
            record_audit_event(
                db,
                event_type="member.invitation.accepted",
                actor_user_id=user.id,
                workspace_id=accepted.workspace.id,
                target_type="workspace_invitation",
                target_id=accepted.invitation.id,
                details={"role": accepted.membership.role.value},
                settings=settings,
            )
            db.commit()


TEAMS: tuple[tuple[str, str, str], ...] = (
    # team name, supervisor role, agent role
    ("Sales", "admin", "member"),
    ("Support", "owner", "viewer"),
)


def _ensure_teams(workspace: Workspace, users: Mapping[str, User], settings: Settings) -> None:
    from sqlalchemy import select

    from backend.database.saas_models import Team, TeamAssignment, TeamRole
    from backend.services.audit_service import record_audit_event
    from backend.services.team_service import create_assignment, create_team, slugify

    owner = users["owner"]
    with _session(user_id=owner.id, workspace=workspace) as db:
        for name, supervisor_role, agent_role in TEAMS:
            team = db.scalar(
                select(Team).where(Team.workspace_id == workspace.id, Team.slug == slugify(name))
            )
            if team is None:
                team = create_team(db, workspace_id=workspace.id, name=name)
                record_audit_event(
                    db,
                    event_type="team.created",
                    actor_user_id=owner.id,
                    workspace_id=workspace.id,
                    target_type="team",
                    target_id=team.id,
                    settings=settings,
                )
            for role_key, team_role in (
                (supervisor_role, TeamRole.SUPERVISOR),
                (agent_role, TeamRole.AGENT),
            ):
                user = users[role_key]
                assigned = db.scalar(
                    select(TeamAssignment.id).where(
                        TeamAssignment.team_id == team.id,
                        TeamAssignment.user_id == user.id,
                        TeamAssignment.role == team_role,
                    )
                )
                if assigned is not None:
                    continue
                record = create_assignment(
                    db,
                    workspace_id=workspace.id,
                    team_id=team.id,
                    user_id=user.id,
                    role=team_role,
                    assigned_by_user_id=owner.id,
                    valid_from=None,
                    valid_until=None,
                )
                record_audit_event(
                    db,
                    event_type="team.assignment.created",
                    actor_user_id=owner.id,
                    workspace_id=workspace.id,
                    target_type="team_assignment",
                    target_id=record.assignment.id,
                    details={
                        "team_id": team.id,
                        "user_id": user.id,
                        "role": record.assignment.role.value,
                    },
                    settings=settings,
                )
        db.commit()


def _ensure_api_key(workspace: Workspace, owner: User, settings: Settings) -> None:
    from sqlalchemy import select

    from backend.database.saas_models import ApiKey, Membership
    from backend.services.api_key_service import create_api_key
    from backend.services.audit_service import record_audit_event

    with _session(user_id=owner.id, workspace=workspace) as db:
        exists = db.scalar(
            select(ApiKey.id).where(
                ApiKey.workspace_id == workspace.id, ApiKey.name == API_KEY_NAME
            )
        )
        if exists is not None:
            return
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace.id, Membership.user_id == owner.id
            )
        )
        # The raw key is never printed or stored: the demo shows the key list.
        key, _raw_key = create_api_key(
            db,
            workspace=db.merge(workspace),
            membership=membership,
            name=API_KEY_NAME,
            scopes=API_KEY_SCOPES,
            expires_in_days=min(90, settings.api_key_max_ttl_days),
            settings=settings,
        )
        record_audit_event(
            db,
            event_type="api_key.created",
            actor_user_id=owner.id,
            workspace_id=workspace.id,
            target_type="api_key",
            target_id=key.id,
            details={
                "name": key.name,
                "credential_prefix": key.key_prefix,
                "scopes": ",".join(key.scopes),
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            },
            settings=settings,
        )
        db.commit()


def _upload(
    workspace: Workspace,
    owner: User,
    *,
    index: int,
    title: str,
    script: Script,
    idempotency_key: str,
    settings: Settings,
) -> str | None:
    """Store the placeholder recording and create the meeting and its job.

    Returns the id of a job that has not had its first attempt yet: the new
    one, or one an earlier run created but stopped before driving it to its
    planned outcome. Returns ``None`` when the meeting was seeded completely.
    """
    from backend.database.saas_models import JobState
    from backend.services.audit_service import record_audit_event
    from backend.services.meeting_service import create_upload_job, find_job_by_idempotency
    from backend.storage.factory import create_object_storage

    with _session(user_id=owner.id, workspace=workspace) as db:
        existing = find_job_by_idempotency(
            db, workspace_id=workspace.id, idempotency_key=idempotency_key
        )
        if existing is not None:
            untouched = existing.state == JobState.QUEUED and existing.attempt_count == 0
            return existing.id if untouched else None
        duration = _duration_seconds(index)
        audio = _silent_wav(duration)
        filename = f"demo-meeting-{index + 1:02d}.wav"
        stored = create_object_storage(settings).put_stream(
            workspace.id,
            io.BytesIO(audio),
            content_type="audio/wav",
            original_filename=filename,
            expected_sha256=hashlib.sha256(audio).hexdigest(),
            max_bytes=len(audio),
        )
        created = create_upload_job(
            db,
            workspace_id=workspace.id,
            user_id=owner.id,
            title=title,
            language=script.language,
            storage_key=stored.key,
            original_filename=filename,
            media_type="audio/wav",
            container_format="wav",
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.sha256,
            duration_seconds=duration,
            channel_count=1,
            sample_rate_hz=SAMPLE_RATE_HZ,
            idempotency_key=idempotency_key,
            retention_days=workspace.retention_days,
            settings=settings,
            producer="demo_seed",
        )
        record_audit_event(
            db,
            event_type="meeting.uploaded",
            actor_user_id=owner.id,
            workspace_id=workspace.id,
            target_type="meeting",
            target_id=created.meeting.id,
            details={
                "size_bytes": stored.size_bytes,
                "duration_seconds": created.meeting.duration_seconds,
                "container": "wav",
            },
            settings=settings,
        )
        db.commit()
        return created.job.id


def _dead_letter(workspace: Workspace, job_id: str, settings: Settings) -> None:
    """A dead-lettered job, so the console offers a replay. It is parked
    directly (the processor only dead-letters after its retry budget), and
    the audit says so."""
    from backend.database.saas_models import JobState, Meeting, ProcessingJob
    from backend.jobs.failures import FailureClass, park_job
    from backend.services.audit_service import record_audit_event

    with _session(workspace=workspace) as db:
        job = db.get(ProcessingJob, job_id, with_for_update=True)
        meeting = db.get(Meeting, job.meeting_id)
        park_job(
            db,
            job,
            meeting,
            state=JobState.DEAD_LETTER,
            code="asr_unavailable",
            message="Speech recognition was unavailable; parked for replay (demo data)",
            failure_class=FailureClass.TRANSIENT,
            now=datetime.now(UTC),
        )
        record_audit_event(
            db,
            event_type="meeting.processing.failed",
            workspace_id=workspace.id,
            target_type="meeting",
            target_id=job.meeting_id,
            outcome="failure",
            details={
                "cause": "asr_unavailable",
                "failure_class": FailureClass.TRANSIENT.value,
                "retryable": True,
                "attempt": job.attempt_count,
                "terminal_reason": "demo_seed",
            },
            settings=settings,
        )
        db.commit()


def _start_processing(workspace: Workspace, job_id: str, settings: Settings) -> None:
    """A job a worker is running right now: the lease and status the
    processor's claim writes. A running worker may pick it up again after
    the lease expires."""
    from backend.database.saas_models import Meeting, MeetingStatus, ProcessingJob
    from backend.jobs.leases import LeasePolicy, database_now, grant_lease

    with _session(workspace=workspace) as db:
        job = db.get(ProcessingJob, job_id, with_for_update=True)
        grant_lease(
            job,
            worker_id=WORKER_ID,
            policy=LeasePolicy.from_settings(settings),
            now=database_now(db),
        )
        meeting = db.get(Meeting, job.meeting_id)
        meeting.status = MeetingStatus.PROCESSING
        db.commit()


def _ensure_meetings(workspace: Workspace, owner: User, settings: Settings) -> None:
    from backend.jobs.processor import process_job

    for index, outcome, script, title in _meeting_specs():
        job_id = _upload(
            workspace,
            owner,
            index=index,
            title=title,
            script=script,
            idempotency_key=f"demo-seed-meeting-{index + 1:02d}",
            settings=settings,
        )
        # A job found still queued is driven on now: an earlier run stopped
        # before it reached its planned outcome.
        if job_id is None or outcome == "queued":
            continue
        if outcome == "dead_letter":
            _dead_letter(workspace, job_id, settings)
        elif outcome == "processing":
            _start_processing(workspace, job_id, settings)
        else:
            result = process_job(
                job_id,
                worker_id=WORKER_ID,
                settings=settings,
                providers=_scripted_bundle(
                    script, duration_seconds=_duration_seconds(index), outcome=outcome
                ),
            )
            if result["status"] != outcome:
                raise RuntimeError(
                    f"Demo meeting {title!r} ended as {result['status']!r}, expected {outcome!r}"
                )


def _ensure_shares(workspace: Workspace, users: Mapping[str, User], settings: Settings) -> None:
    from sqlalchemy import select

    from backend.database.saas_models import Meeting, MeetingStatus, ShareAudience, ShareLink
    from backend.security.tokens import random_token
    from backend.services.audit_service import record_audit_event
    from backend.services.share_service import ShareSpec, issue_share

    owner = users["owner"]
    specs = (
        ShareSpec(
            audience=ShareAudience.WORKSPACE,
            recipients=(),
            allow_download=False,
            allow_export=True,
            expires_in_hours=None,
        ),
        ShareSpec(
            audience=ShareAudience.RECIPIENTS,
            recipients=(f"partner@{DEMO_EMAIL_DOMAIN}",),
            allow_download=False,
            allow_export=False,
            expires_in_hours=24 * 30,
        ),
    )
    with _session(user_id=owner.id, workspace=workspace) as db:
        meetings: Sequence[Meeting] = list(
            db.scalars(
                select(Meeting)
                .where(
                    Meeting.workspace_id == workspace.id,
                    Meeting.status == MeetingStatus.COMPLETED,
                )
                .order_by(Meeting.created_at.asc(), Meeting.id.asc())
                .limit(len(specs))
            )
        )
        current = db.merge(workspace)
        for meeting, spec in zip(meetings, specs, strict=False):
            shared = db.scalar(
                select(ShareLink.id).where(
                    ShareLink.meeting_id == meeting.id, ShareLink.audience == spec.audience
                )
            )
            if shared is not None:
                continue
            # The raw token is not kept: the demo shows the share list only.
            share = issue_share(
                db,
                workspace=current,
                meeting=meeting,
                created_by_user_id=owner.id,
                spec=spec,
                raw_token=random_token(32),
                settings=settings,
            )
            record_audit_event(
                db,
                event_type="meeting.share_created",
                actor_user_id=owner.id,
                workspace_id=workspace.id,
                target_type="share_link",
                target_id=share.id,
                details={
                    "meeting_id": meeting.id,
                    "audience": share.audience.value,
                    "allow_download": share.allow_download,
                    "allow_export": share.allow_export,
                    "recipient_count": spec.recipient_count,
                    "has_expiration": share.expires_at is not None,
                },
                settings=settings,
            )
        db.commit()


def _meeting_count(workspace: Workspace) -> int:
    from sqlalchemy import func, select

    from backend.database.saas_models import Meeting

    with _session(workspace=workspace) as db:
        return db.scalar(
            select(func.count()).select_from(Meeting).where(Meeting.workspace_id == workspace.id)
        )


# --- public interface ----------------------------------------------------------


def seed_demo_workspace(settings: Settings) -> DemoSeedResult:
    """Create (or find) the demo workspace and return its logins."""
    # Before any engine, connection or import of the database layer.
    _refuse_unless_allowed(settings.environment)
    _ensure_database(settings)

    accounts = _accounts()
    users = _ensure_users(accounts)
    workspace = _ensure_workspace(users["owner"], settings)
    _ensure_members(workspace, users, settings)
    _ensure_teams(workspace, users, settings)
    _ensure_api_key(workspace, users["owner"], settings)
    _ensure_meetings(workspace, users["owner"], settings)
    _ensure_shares(workspace, users, settings)
    return DemoSeedResult(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        workspace_name=workspace.name,
        accounts=accounts,
        meeting_count=_meeting_count(workspace),
        console_url=settings.frontend_url or None,
    )


def _print_result(result: DemoSeedResult) -> None:
    print(f"Demo workspace: {result.workspace_name} ({result.workspace_id})")
    print(f"Meetings: {result.meeting_count}")
    if result.console_url:
        print(f"Console: {result.console_url}")
    print("Logins (all roles share one password):")
    for role, account in result.accounts.items():
        print(f"  {role:<7} {account.email}  {account.password}")


def _raw_environments() -> list[str]:
    """Every spelling of the environment variable (settings read it in any
    case), normalized; the default when none is set."""
    values = [value for name, value in os.environ.items() if name.casefold() == "environment"]
    return [value.strip().lower() for value in values] or ["development"]


def main(argv: list[str] | None = None) -> int:
    # Decide from the raw variables first: a production environment is
    # refused even when its settings would not validate here.
    try:
        for environment in _raw_environments():
            _refuse_unless_allowed(environment)
        from pydantic import ValidationError

        from backend.config import Settings

        try:
            settings = Settings()
        except ValidationError as exc:
            problems = "; ".join(error["msg"] for error in exc.errors())
            print(f"The demo seed cannot read its settings: {problems}", file=sys.stderr)
            return 1
        # Settings may also come from the .env file; check what they resolved to.
        result = seed_demo_workspace(settings)
    except DemoSeedRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
