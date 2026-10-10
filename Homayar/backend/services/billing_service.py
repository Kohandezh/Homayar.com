from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Entitlement,
    Plan,
    PlanVersion,
    Subscription,
    SubscriptionStatus,
    UsageEvent,
)
from backend.services.tenancy_service import DEMO_PLAN_KEY

CURRENT_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.PAST_DUE,
)


class BillingConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurrentBilling:
    subscription: Subscription | None
    plan: Plan | None
    # The terms the subscription was granted under (its pinned plan version),
    # which a later publish on the plan does not change.
    version: PlanVersion | None


@dataclass(frozen=True)
class UsageEventPage:
    items: list[UsageEvent]
    next_before: datetime | None
    next_before_id: str | None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_current_billing(
    db: Session,
    *,
    workspace_id: str,
    now: datetime | None = None,
) -> CurrentBilling:
    effective_at = _as_utc(now or datetime.now(UTC))
    subscriptions = db.scalars(
        select(Subscription)
        .where(
            Subscription.workspace_id == workspace_id,
            Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
        )
        .order_by(
            Subscription.starts_at.desc(),
            Subscription.created_at.desc(),
            Subscription.id.desc(),
        )
    )
    for subscription in subscriptions:
        starts_at = _as_utc(subscription.starts_at)
        ends_at = _as_utc(subscription.ends_at) if subscription.ends_at is not None else None
        if starts_at > effective_at or (ends_at is not None and ends_at <= effective_at):
            continue
        plan = db.get(Plan, subscription.plan_key)
        version = db.get(PlanVersion, (subscription.plan_key, subscription.plan_version))
        if plan is None or version is None:
            raise BillingConfigurationError(
                "Current subscription references an unknown plan or plan version"
            )
        return CurrentBilling(subscription=subscription, plan=plan, version=version)
    return CurrentBilling(subscription=None, plan=None, version=None)


def list_public_plans(db: Session, *, free_demo_enabled: bool) -> list[Plan]:
    """Public, active plans; the demo plan only while ENABLE_FREE_DEMO is on."""
    query = select(Plan).where(Plan.is_public.is_(True), Plan.is_active.is_(True))
    if not free_demo_enabled:
        query = query.where(Plan.key != DEMO_PLAN_KEY)
    return list(db.scalars(query.order_by(Plan.created_at.asc(), Plan.key.asc())))


def list_usage_snapshots(db: Session, *, workspace_id: str) -> list[Entitlement]:
    return list(
        db.scalars(
            select(Entitlement)
            .where(Entitlement.workspace_id == workspace_id)
            .order_by(Entitlement.metric.asc())
        )
    )


def list_usage_events(
    db: Session,
    *,
    workspace_id: str,
    limit: int,
    metric: str | None = None,
    before: datetime | None = None,
    before_id: str | None = None,
) -> UsageEventPage:
    query = select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
    if metric is not None:
        query = query.where(UsageEvent.metric == metric)
    if before is not None:
        if before_id is None:
            query = query.where(UsageEvent.occurred_at < before)
        else:
            query = query.where(
                or_(
                    UsageEvent.occurred_at < before,
                    and_(UsageEvent.occurred_at == before, UsageEvent.id < before_id),
                )
            )

    rows = list(
        db.scalars(
            query.order_by(UsageEvent.occurred_at.desc(), UsageEvent.id.desc()).limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    cursor = items[-1] if has_more and items else None
    return UsageEventPage(
        items=items,
        next_before=cursor.occurred_at if cursor is not None else None,
        next_before_id=cursor.id if cursor is not None else None,
    )
