from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    Entitlement,
    Membership,
    Organization,
    Plan,
    Subscription,
    SubscriptionStatus,
    User,
    Workspace,
    WorkspaceRole,
    new_id,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.security.authorization import selectable_workspace_clause
from backend.security.normalization import normalize_workspace_slug
from backend.services.organization_service import bootstrap_owner_membership

PLAN_CATALOG = (
    {
        "key": "demo",
        "name": "Free Demo",
        "is_public": True,
        "entitlements": {"audio_seconds": 1800, "concurrent_jobs": 1, "seats": 1},
    },
    {
        "key": "professional",
        "name": "Professional",
        "is_public": True,
        "entitlements": {},
    },
    {
        "key": "business",
        "name": "Business",
        "is_public": True,
        "entitlements": {},
    },
    {
        "key": "enterprise",
        "name": "Enterprise",
        "is_public": False,
        "entitlements": {},
    },
    {
        "key": "on_premise",
        "name": "On-Premise",
        "is_public": False,
        "entitlements": {},
    },
)


def ensure_plan_catalog(db: Session, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    for item in PLAN_CATALOG:
        plan = db.get(Plan, item["key"])
        entitlements = dict(item["entitlements"])
        if item["key"] == "demo":
            entitlements["audio_seconds"] = settings.demo_quota_seconds
        if plan is None:
            db.add(
                Plan(
                    key=item["key"],
                    name=item["name"],
                    is_public=item["is_public"],
                    is_active=True,
                    entitlements_json=entitlements,
                    display_json={},
                )
            )
        else:
            plan.name = item["name"]
            plan.is_public = item["is_public"]
            plan.entitlements_json = entitlements


def create_personal_workspace(
    db: Session,
    *,
    user: User,
    name: str | None = None,
    settings: Settings | None = None,
) -> Workspace:
    settings = settings or get_settings()
    ensure_plan_catalog(db, settings)
    workspace_name = (name or user.display_name or "My Workspace").strip()[:180]
    base_slug = normalize_workspace_slug(workspace_name)
    unique_suffix = secrets.token_hex(3)
    organization = Organization(
        id=new_id(),
        name=workspace_name,
        slug=f"{base_slug}-{unique_suffix}",
        status="active",
    )
    workspace = Workspace(
        id=new_id(),
        organization=organization,
        name=workspace_name,
        slug="default",
        status="active",
        retention_days=settings.retention_days_default,
    )
    # The new tenant's ids are generated here, so the transaction can be scoped
    # to them before the first row is written (row-level security WITH CHECK).
    bind_user_context(db, user.id)
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=organization.id)
    db.add(organization)
    db.flush()
    db.add(Membership(workspace=workspace, user=user, role=WorkspaceRole.OWNER))
    db.flush()
    # The creator owns the organization as well (WP03-T03, ADR 0012); this is
    # the only organization role a bootstrap ever grants.
    bootstrap_owner_membership(db, organization=organization, user=user)

    now = datetime.now(UTC)
    if settings.enable_free_demo:
        db.add(
            Subscription(
                workspace_id=workspace.id,
                plan_key="demo",
                status=SubscriptionStatus.TRIALING,
                provider="internal",
                starts_at=now,
            )
        )
        db.add(
            Entitlement(
                workspace_id=workspace.id,
                metric="audio_seconds",
                source="demo",
                limit_value=settings.demo_quota_seconds,
                used_value=0,
                reserved_value=0,
                period_start=now,
                period_end=None,
            )
        )
        db.add(
            Entitlement(
                workspace_id=workspace.id,
                metric="concurrent_jobs",
                source="demo",
                limit_value=1,
                used_value=0,
                reserved_value=0,
                period_start=now,
                period_end=None,
            )
        )
    db.flush()
    return workspace


def list_user_workspaces(
    db: Session, user_id: str, *, now: datetime | None = None
) -> list[tuple[Workspace, Membership]]:
    """The workspaces a request of ``user_id`` may select right now.

    Same predicate as ``require_workspace`` (``selectable_workspace_clause``):
    an inactive, not-yet-valid or expired membership and a workspace that is
    not ``active`` are left out, so the session never lists a workspace that
    every request would refuse with ``workspace_access_denied``.
    """
    rows = db.execute(
        select(Workspace, Membership)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(
            Membership.user_id == user_id,
            selectable_workspace_clause(now or datetime.now(UTC)),
        )
        .order_by(Membership.created_at.asc())
    )
    return list(rows.all())

