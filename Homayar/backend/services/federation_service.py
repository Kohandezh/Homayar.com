"""IdP group mappings and honest deprovisioning (HOM-128, ADR 0066).

OIDC has no push channel: the app only learns that a user was disabled or left
a group when it asks the IdP.  This module owns the two halves of that truth:

* the reconciler turns a federated user's current IdP groups into workspace
  memberships through the organization's explicit ``federation_group_mappings``
  rows, touching only memberships it created (``source == "federated"``); and
* the revalidation paths decide *when* the IdP is asked (every refresh, the
  periodic sweep) and what happens when it says "gone" (revoke sessions, drop
  federated memberships, revoke the identity) or does not answer (fail closed;
  change nothing).

The real checker (a userinfo call) arrives with HOM-127; until then no checker
is configured and every revalidation answers "unavailable".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    AuthIdentity,
    FederationGroupMapping,
    IdentityProvider,
    Membership,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import (
    bind_organization_context,
    bind_tenant_context,
    bind_user_context,
    current_context,
    restore_context,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger
from backend.services.seat_service import consumes_seat, lock_seats, seat_available

# admin > member > viewer; a mapping never grants owner (ck_federation_mapping_role).
_ROLE_RANK = {WorkspaceRole.ADMIN: 0, WorkspaceRole.MEMBER: 1, WorkspaceRole.VIEWER: 2}


@dataclass(frozen=True)
class FederatedIdentityActive:
    groups: frozenset[str]


@dataclass(frozen=True)
class FederatedIdentityGone:
    pass


@dataclass(frozen=True)
class FederatedIdentityUnavailable:
    reason: str = "unavailable"


FederatedIdentityStatus = (
    FederatedIdentityActive | FederatedIdentityGone | FederatedIdentityUnavailable
)


class FederatedIdentityChecker(Protocol):
    def check(self, identity: AuthIdentity) -> FederatedIdentityStatus: ...


class FederationIdentityError(Exception):
    """The identity profile lacks the fields the mapping rules need."""


@dataclass(frozen=True)
class FederationSyncResult:
    granted: tuple[str, ...] = ()
    role_changed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    kept_direct: tuple[str, ...] = ()
    skipped_seat_limit: tuple[str, ...] = ()


_checker: FederatedIdentityChecker | None = None


def set_federated_identity_checker(checker: FederatedIdentityChecker | None) -> None:
    global _checker
    _checker = checker


def get_federated_identity_checker() -> FederatedIdentityChecker | None:
    return _checker


def federated_identity_is_stale(
    db: Session, *, user_id: str, now: datetime, settings: Settings | None = None
) -> bool:
    """Whether the user's unrevoked OIDC identity has no fresh IdP confirmation.

    The one freshness rule behind both gates (HOM-128, 5b API keys and 5e new
    login sessions): older than ``federation_max_unconfirmed_minutes`` (or
    never confirmed) means refuse.  Users with no OIDC identity, or only a
    revoked one, are not affected.  No IdP call runs here; a request must never
    wait on one.
    """
    settings = settings or get_settings()
    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.user_id == user_id,
            AuthIdentity.provider == IdentityProvider.OIDC,
            AuthIdentity.revoked_at.is_(None),
        )
    )
    if identity is None:
        return False
    verified_at = identity.verified_at
    if verified_at is not None and verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=UTC)
    return verified_at is None or now - verified_at > timedelta(
        minutes=settings.federation_max_unconfirmed_minutes
    )


def _identity_profile(identity: AuthIdentity) -> dict:
    profile = identity.profile
    return profile if isinstance(profile, dict) else {}


def _profile_scope(identity: AuthIdentity) -> tuple[str, str]:
    """The (issuer, organization_id) the IdP connection belongs to.

    Raises ``FederationIdentityError`` when the profile is malformed: HOM-127
    writes both keys at login, so anything else was never confirmed by an IdP.
    """
    profile = _identity_profile(identity)
    issuer = profile.get("issuer")
    organization_id = profile.get("organization_id")
    if (
        not isinstance(issuer, str)
        or not issuer
        or not isinstance(organization_id, str)
        or not organization_id
    ):
        raise FederationIdentityError("The identity profile lacks issuer or organization_id")
    return issuer, organization_id


def _record_sync_event(
    db: Session,
    *,
    settings: Settings,
    event_type: AuditEventType,
    actor_user_id: str,
    target_type: str,
    target_id: str,
    workspace_id: str | None,
    details: dict,
) -> None:
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target_type=target_type,
        target_id=target_id,
        details=details,
        settings=settings,
    )


def sync_federated_memberships(
    db: Session,
    *,
    identity: AuthIdentity,
    groups: Iterable[str],
    now: datetime | None = None,
    settings: Settings | None = None,
    confirmed: bool = True,
) -> FederationSyncResult:
    """Turn the identity's current groups into workspace memberships.

    Desired state: for each unrevoked mapping of this organization and issuer
    whose group matches, the highest mapped role (admin > member > viewer) in
    its workspace.  Rows nobody decided by hand (``source == "federated"``)
    follow the IdP; every ``direct`` row is never touched.  Flushed, never
    committed; idempotent (an unchanged second call writes no audit event).
    ``confirmed=False`` (mapping changed, or deprovisioning) leaves
    ``verified_at`` and ``profile["groups"]`` alone: no IdP was asked.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    issuer, organization_id = _profile_scope(identity)
    user_id = identity.user_id
    group_values = frozenset(groups)

    if confirmed:
        stored = sorted(group_values)
        if _identity_profile(identity).get("groups") != stored:
            identity.profile = {**_identity_profile(identity), "groups": stored}
        identity.verified_at = now

    # The reconciler acts for the federated user; the organization context
    # exposes the mappings, the tenant context each workspace's rows.  With
    # ``autoflush=False`` every write is checked against the policy of the
    # context bound at flush time, so each workspace step flushes while its own
    # tenant is bound.  The caller's context is left exactly as it was found.
    saved_context = current_context(db)
    try:
        bind_user_context(db, user_id)
        bind_organization_context(db, organization_id)

        candidates: dict[str, WorkspaceRole] = {}
        if group_values:
            mappings = db.scalars(
                select(FederationGroupMapping).where(
                    FederationGroupMapping.organization_id == organization_id,
                    FederationGroupMapping.issuer == issuer,
                    FederationGroupMapping.revoked_at.is_(None),
                    FederationGroupMapping.group_name.in_(sorted(group_values)),
                )
            ).all()
            for mapping in mappings:
                current = candidates.get(mapping.workspace_id)
                if current is None or _ROLE_RANK[mapping.role] < _ROLE_RANK[current]:
                    candidates[mapping.workspace_id] = mapping.role

        # Desired state (SPEC 3.3): only active workspaces of this organization
        # count, so a suspended (or foreign) workspace is not desired at all
        # and an active federated row there is removed below (SPEC 3.5).
        desired: dict[str, WorkspaceRole] = {}
        for workspace_id in sorted(candidates):
            bind_tenant_context(
                db, workspace_id=workspace_id, organization_id=organization_id
            )
            workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id))
            if (
                workspace is None
                or workspace.organization_id != organization_id
                or workspace.status != "active"
            ):
                continue
            desired[workspace_id] = candidates[workspace_id]

        granted: list[str] = []
        role_changed: list[str] = []
        kept_direct: list[str] = []
        skipped_seat_limit: list[str] = []

        for workspace_id in sorted(desired):
            role = desired[workspace_id]
            bind_tenant_context(
                db, workspace_id=workspace_id, organization_id=organization_id
            )
            # Same lock order as the member routes (HOM-128 F4): the seat lock
            # first when the desired role holds one, then the row FOR UPDATE,
            # only then the hand-decision check.
            if consumes_seat(role):
                lock_seats(db, workspace_id=workspace_id)
            existing = db.scalar(
                select(Membership)
                .where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id == user_id,
                )
                .with_for_update()
            )
            if existing is None:
                # A first grant (any role) also takes the workspace lock, so
                # two concurrent syncs cannot both insert the same
                # (workspace, user) row; whoever lost re-reads the winner's
                # row and continues with it.
                lock_seats(db, workspace_id=workspace_id)
                existing = db.scalar(
                    select(Membership)
                    .where(
                        Membership.workspace_id == workspace_id,
                        Membership.user_id == user_id,
                    )
                    .with_for_update()
                )
            if existing is not None and existing.source == "direct":
                # A person decided this row: owners, invitations, admin installs
                # and every admin-touched federated row.  The IdP has no say.
                kept_direct.append(workspace_id)
                continue

            if consumes_seat(role, existing, now=now) and not seat_available(
                db, workspace_id=workspace_id, include_pending_invitations=True, now=now
            ):
                # A seat refusal never blocks the other workspaces of this sync.
                skipped_seat_limit.append(workspace_id)
                _record_sync_event(
                    db,
                    settings=settings,
                    event_type=AuditEventType.FEDERATION_MEMBERSHIP_SEAT_SKIPPED,
                    actor_user_id=user_id,
                    target_type="workspace",
                    target_id=workspace_id,
                    workspace_id=workspace_id,
                    details={
                        "action": "skipped",
                        "role": role.value,
                        "user_id": user_id,
                        "issuer": issuer,
                    },
                )
                db.flush()
                continue

            action: str | None = None
            membership_id: str | None = None
            if existing is None:
                membership = Membership(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role=role,
                    is_active=True,
                    source="federated",
                    valid_from=now,
                )
                db.add(membership)
                db.flush()
                granted.append(workspace_id)
                action = "granted"
                membership_id = membership.id
            else:
                was_inactive = not existing.is_active
                role_differs = existing.role != role
                existing.role = role
                existing.is_active = True
                existing.valid_until = None
                if was_inactive:
                    existing.valid_from = now
                    granted.append(workspace_id)
                    action = "granted"
                elif role_differs:
                    role_changed.append(workspace_id)
                    action = "role_changed"
                membership_id = existing.id
            if action is not None:
                _record_sync_event(
                    db,
                    settings=settings,
                    event_type=AuditEventType.FEDERATION_MEMBERSHIP_SYNCED,
                    actor_user_id=user_id,
                    target_type="membership",
                    target_id=membership_id,
                    workspace_id=workspace_id,
                    details={
                        "action": action,
                        "role": role.value,
                        "user_id": user_id,
                        "issuer": issuer,
                    },
                )
            # Flush the row and its audit while this workspace's tenant is
            # bound; the next step rebinds another tenant (RLS).
            db.flush()

        removed: list[str] = []
        stale = db.scalars(
            select(Membership)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Membership.user_id == user_id,
                Membership.is_active.is_(True),
                Membership.source == "federated",
                Workspace.organization_id == organization_id,
            )
        ).all()
        for membership in stale:
            if membership.workspace_id in desired:
                continue
            bind_tenant_context(
                db, workspace_id=membership.workspace_id, organization_id=organization_id
            )
            membership.is_active = False
            removed.append(membership.workspace_id)
            _record_sync_event(
                db,
                settings=settings,
                event_type=AuditEventType.FEDERATION_MEMBERSHIP_SYNCED,
                actor_user_id=user_id,
                target_type="membership",
                target_id=membership.id,
                workspace_id=membership.workspace_id,
                details={
                    "action": "removed",
                    "role": membership.role.value,
                    "user_id": user_id,
                    "issuer": issuer,
                },
            )
            db.flush()

        db.flush()
        return FederationSyncResult(
            granted=tuple(granted),
            role_changed=tuple(role_changed),
            removed=tuple(removed),
            kept_direct=tuple(kept_direct),
            skipped_seat_limit=tuple(skipped_seat_limit),
        )
    finally:
        restore_context(db, saved_context)


def reapply_organization_mappings(
    db: Session,
    *,
    organization_id: str,
    issuer: str,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Re-run the reconciler for every identity of this organization and issuer.

    Called when an admin creates or revokes a mapping: the change applies at
    once, from each identity's stored groups, without asking any IdP
    (``confirmed=False``, so ``verified_at`` does not move).  The identity's
    own profile decides membership, not an organization membership row.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    identities = db.scalars(
        select(AuthIdentity).where(
            AuthIdentity.provider == IdentityProvider.OIDC,
            AuthIdentity.revoked_at.is_(None),
        )
    ).all()
    reconciled = 0
    for identity in identities:
        profile = _identity_profile(identity)
        if profile.get("organization_id") != organization_id or profile.get("issuer") != issuer:
            continue
        groups = profile.get("groups")
        if not isinstance(groups, list):
            groups = []
        try:
            sync_federated_memberships(
                db, identity=identity, groups=groups, now=now, settings=settings, confirmed=False
            )
        except FederationIdentityError:
            logger.warning(
                "FEDERATION_REAPPLY_SKIPPED reason=malformed_profile identity_id=%s", identity.id
            )
            continue
        reconciled += 1
    return reconciled


def _deprovision(
    db: Session,
    *,
    identity: AuthIdentity,
    issuer: str,
    organization_id: str,
    now: datetime,
    settings: Settings,
) -> None:
    # Imported here: auth_service revalidates through this module (5a).
    from backend.services.auth_service import revoke_all_user_sessions

    user = db.get(User, identity.user_id)
    if user is not None:
        revoke_all_user_sessions(db, user)
    sync_federated_memberships(
        db, identity=identity, groups=(), now=now, settings=settings, confirmed=False
    )
    identity.revoked_at = now
    # Direct memberships and User.is_active stay: a user may also be a direct
    # member elsewhere; disabling the account is an admin decision (ADR 0066).
    record_audit_event(
        db,
        event_type=AuditEventType.FEDERATION_IDENTITY_DEPROVISIONED,
        actor_user_id=identity.user_id,
        target_type="user",
        target_id=identity.user_id,
        details={"issuer": issuer, "organization_id": organization_id},
        settings=settings,
    )
    db.flush()


def revalidate_federated_identity(
    db: Session,
    *,
    identity: AuthIdentity,
    checker: FederatedIdentityChecker | None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> Literal["active", "gone", "unavailable"]:
    """Ask the checker once and apply the answer.

    "unavailable" (no checker, a malformed profile, a raising checker) changes
    nothing: the caller fails closed and the user signs in again.  "gone"
    revokes every session, removes this organization's federated memberships
    and revokes the identity; "active" applies the reported groups.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    if checker is None:
        logger.warning("FEDERATION_CHECK_UNAVAILABLE reason=no_checker identity_id=%s", identity.id)
        return "unavailable"
    try:
        issuer, organization_id = _profile_scope(identity)
    except FederationIdentityError:
        logger.warning(
            "FEDERATION_CHECK_UNAVAILABLE reason=malformed_profile identity_id=%s", identity.id
        )
        return "unavailable"
    try:
        status = checker.check(identity)
    except Exception as exc:
        logger.warning(
            "FEDERATION_CHECK_UNAVAILABLE reason=checker_raised identity_id=%s error_type=%s",
            identity.id,
            type(exc).__name__,
        )
        return "unavailable"
    if isinstance(status, FederatedIdentityUnavailable):
        logger.warning(
            "FEDERATION_CHECK_UNAVAILABLE reason=%s identity_id=%s", status.reason, identity.id
        )
        return "unavailable"
    if isinstance(status, FederatedIdentityGone):
        _deprovision(
            db,
            identity=identity,
            issuer=issuer,
            organization_id=organization_id,
            now=now,
            settings=settings,
        )
        return "gone"
    sync_federated_memberships(
        db, identity=identity, groups=status.groups, now=now, settings=settings, confirmed=True
    )
    return "active"


def revalidate_stale_federated_identities(
    db: Session,
    *,
    checker: FederatedIdentityChecker | None,
    now: datetime | None = None,
    settings: Settings | None = None,
    limit: int = 200,
) -> dict[str, int]:
    """Revalidate every stale unrevoked OIDC identity, oldest first.

    The ids are read first, then each identity is handled in its own
    transaction (committed per identity, HOM-128 F2): no row locks are held
    across a live IdP call of another identity, and one failing identity only
    rolls its own work back (it counts as "unavailable"; the next pass picks
    it up again).
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=settings.federation_revalidation_max_age_minutes)
    counts = {"active": 0, "gone": 0, "unavailable": 0}
    identity_ids = db.scalars(
        select(AuthIdentity.id)
        .where(
            AuthIdentity.provider == IdentityProvider.OIDC,
            AuthIdentity.revoked_at.is_(None),
            AuthIdentity.verified_at < cutoff,
        )
        .order_by(AuthIdentity.verified_at.asc(), AuthIdentity.id.asc())
        .limit(limit)
    ).all()
    for identity_id in identity_ids:
        identity = db.get(AuthIdentity, identity_id)
        if identity is None:  # deleted between the read and here
            continue
        verified_at = identity.verified_at
        if verified_at is not None and verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=UTC)
        if (
            identity.revoked_at is not None  # deprovisioned in the meantime
            or verified_at is None  # never confirmed; the sweep's query skipped it
            or verified_at >= cutoff  # confirmed again after the id read
        ):
            continue
        try:
            outcome = revalidate_federated_identity(
                db, identity=identity, checker=checker, now=now, settings=settings
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "FEDERATION_SWEEP_IDENTITY_FAILED identity_id=%s error_type=%s",
                identity_id,
                type(exc).__name__,
            )
            counts["unavailable"] += 1
            continue
        counts[outcome] += 1
    return counts
