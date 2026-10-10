"""Admin seats (HOM-167 F1): owner and admin memberships consume a plan's seats.

A workspace's seat limit is the ``seats`` term of the plan version its current
subscription was granted under.  Effective owner and admin memberships (active
and inside their validity window, as authorization counts them) hold a seat;
members and viewers are free.  A plan version without ``seats``, a ``null``
value, or a workspace without a current subscription has no seat limit; an
inconsistent subscription or a malformed value fails closed (no free seat).
A pending admin invitation reserves a seat, which accepting it then consumes.
Seat-consuming changes take the workspace row lock, the same lock invitation
creation already takes, so concurrent changes cannot exceed the limit.
Existing seat holders above a lowered limit keep their roles; only new ones
are refused.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.saas_models import (
    Membership,
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
)
from backend.security.authorization import membership_effective_clause
from backend.services.billing_service import BillingConfigurationError, get_current_billing

SEAT_ROLES = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})
MAX_SEATS = 2_147_483_647


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _holds_seat(membership: Membership, now: datetime) -> bool:
    return (
        membership.is_active
        and membership.role in SEAT_ROLES
        and _as_utc(membership.valid_from) <= now
        and (membership.valid_until is None or _as_utc(membership.valid_until) > now)
    )


def consumes_seat(
    role: WorkspaceRole, current: Membership | None = None, *, now: datetime | None = None
) -> bool:
    """Whether granting ``role`` needs a seat that ``current`` does not already hold."""
    if role not in SEAT_ROLES:
        return False
    return current is None or not _holds_seat(current, now or datetime.now(UTC))


def lock_seats(db: Session, *, workspace_id: str) -> None:
    """Serialize seat-consuming changes of one workspace (its row, ``FOR UPDATE``)."""
    db.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())


def seat_limit(db: Session, *, workspace_id: str, now: datetime | None = None) -> int | None:
    """The seat limit of the workspace's current plan version; ``None`` is unlimited."""
    try:
        current = get_current_billing(db, workspace_id=workspace_id, now=now)
    except BillingConfigurationError:
        return 0
    if current.version is None:
        return None
    terms = current.version.entitlements_json
    if not isinstance(terms, dict) or terms.get("seats") is None:
        return None
    limit = terms["seats"]
    return limit if type(limit) is int and 0 <= limit <= MAX_SEATS else 0


def seats_reserved(
    db: Session,
    *,
    workspace_id: str,
    include_pending_invitations: bool,
    now: datetime | None = None,
) -> int:
    """Effective owner/admin memberships, plus pending admin invitations when asked."""
    now = now or datetime.now(UTC)
    held = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.role.in_(SEAT_ROLES),
            membership_effective_clause(now),
        )
    )
    if not include_pending_invitations:
        return held or 0
    pending = db.scalar(
        select(func.count())
        .select_from(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.role.in_(SEAT_ROLES),
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at > now,
        )
    )
    return (held or 0) + (pending or 0)


def seat_available(
    db: Session,
    *,
    workspace_id: str,
    include_pending_invitations: bool,
    now: datetime | None = None,
) -> bool:
    """Whether one more seat fits; call under ``lock_seats``."""
    limit = seat_limit(db, workspace_id=workspace_id, now=now)
    if limit is None:
        return True
    reserved = seats_reserved(
        db,
        workspace_id=workspace_id,
        include_pending_invitations=include_pending_invitations,
        now=now,
    )
    return reserved < limit
