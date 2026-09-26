from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Cookie, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import (
    ApiKey,
    LoginSession,
    Membership,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from backend.database.tenant_context import (
    bind_organization_context,
    bind_tenant_context,
    bind_user_context,
)
from backend.security.authorization import (
    ROLE_PERMISSIONS,
    OrganizationAccess,
    OrganizationPermission,
    Permission,
    RecordAccess,
    organization_membership_effective_clause,
    organization_role_has,
    resolve_organization_access,
    resolve_record_access,
    role_has,
    selectable_workspace_clause,
)
from backend.security.tokens import AccessTokenError, decode_access_token, token_digest
from backend.services.api_key_service import authenticate_api_key, looks_like_api_key

__all__ = [
    "ACCESS_COOKIE",
    "CSRF_COOKIE",
    "REFRESH_COOKIE",
    "ROLE_PERMISSIONS",
    "Actor",
    "OrganizationContext",
    "OrganizationPermission",
    "Permission",
    "WorkspaceContext",
    "optional_actor",
    "require_actor",
    "require_actor_or_api_key",
    "require_csrf",
    "require_organization",
    "require_organization_permission",
    "require_organization_write_permission",
    "require_path_permission",
    "require_permission",
    "require_workspace",
    "require_write_permission",
]

ACCESS_COOKIE = "homayar_access"
REFRESH_COOKIE = "homayar_refresh"
CSRF_COOKIE = "homayar_csrf"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    """Who is acting: a signed-in user (session) or a user-bound API key.

    ``session`` is ``None`` for API-key actors; ``api_key`` is ``None`` for
    session actors.  ``token_source`` is ``cookie``, ``bearer`` or ``api_key``.
    """

    user: User
    session: LoginSession | None
    token_source: str
    api_key: ApiKey | None = None

    @property
    def scopes(self) -> frozenset[Permission] | None:
        """Permissions an API-key actor may exercise; ``None`` for a session actor."""
        if self.api_key is None:
            return None
        return frozenset(_parse_scope(value) for value in self.api_key.scopes) - {None}


def _parse_scope(value: str) -> Permission | None:
    try:
        return Permission(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class WorkspaceContext:
    actor: Actor
    workspace: Workspace
    membership: Membership
    record_access: RecordAccess

    def has(self, permission: Permission) -> bool:
        # An API key never exceeds its granted scopes; within them it is bound
        # by the *current* membership role like any session (WP03-T07).
        if not self.scope_permits(permission):
            return False
        return role_has(self.membership.role, permission)

    def scope_permits(self, permission: Permission) -> bool:
        scopes = self.actor.scopes
        return scopes is None or permission in scopes


@dataclass(frozen=True)
class OrganizationContext:
    """The caller's *current* organization membership (WP03-T03).

    Carries organization authority only: no workspace is selected and no
    workspace content is reachable through it.
    """

    actor: Actor
    organization: Organization
    membership: OrganizationMembership
    access: OrganizationAccess

    def has(self, permission: OrganizationPermission) -> bool:
        return organization_role_has(self.membership.role, permission)


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "authentication_required", "message": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_actor(
    request: Request,
    *,
    raw_token: str,
    source: str,
    db: Session,
    settings: Settings,
) -> Actor:
    try:
        payload = decode_access_token(raw_token, settings)
    except AccessTokenError as exc:
        raise _unauthorized(str(exc)) from exc

    session = db.get(LoginSession, payload["sid"])
    user = db.get(User, payload["sub"])
    now = datetime.now(UTC)
    if session is None or user is None:
        raise _unauthorized("Session is no longer valid")
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        session.user_id != user.id
        or session.revoked_at is not None
        or expires_at <= now
        or not user.is_active
        or int(payload["sv"]) != user.session_version
    ):
        raise _unauthorized("Session is no longer valid")
    session.last_seen_at = now
    request.state.auth_token_source = source
    # Row-level security: the validated user id is the only identity the
    # database trusts for the tenancy bootstrap tables (memberships, workspaces).
    bind_user_context(db, user.id)
    return Actor(user=user, session=session, token_source=source)


def _api_key_not_accepted() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": "api_key_not_accepted",
            "message": "This endpoint requires a user session",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_api_key_actor(
    request: Request, *, raw_key: str, db: Session, settings: Settings
) -> Actor:
    """An API key acts as the user it is bound to (WP03-T07); validated per request."""
    key, user = authenticate_api_key(db, raw_key=raw_key, settings=settings)
    request.state.auth_token_source = "api_key"  # noqa: S105 - transport label
    request.state.api_key_id = key.id
    bind_user_context(db, user.id)
    return Actor(user=user, session=None, token_source="api_key", api_key=key)  # noqa: S106


async def require_actor(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    cookie_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Actor:
    """A signed-in user.  API keys are refused here: session, organization and
    token-addressed routes are people-only (``require_workspace`` accepts keys)."""
    if bearer is not None:
        if looks_like_api_key(bearer.credentials):
            raise _api_key_not_accepted()
        raw_token = bearer.credentials
        source = "bearer"
    elif cookie_token:
        raw_token = cookie_token
        source = "cookie"
    else:
        raise _unauthorized()
    return _resolve_actor(
        request,
        raw_token=raw_token,
        source=source,
        db=db,
        settings=settings,
    )


async def require_actor_or_api_key(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    cookie_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Actor:
    if bearer is not None and looks_like_api_key(bearer.credentials):
        return _resolve_api_key_actor(request, raw_key=bearer.credentials, db=db, settings=settings)
    return await require_actor(
        request, bearer=bearer, cookie_token=cookie_token, db=db, settings=settings
    )


async def optional_actor(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    cookie_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Actor | None:
    if bearer is not None:
        if looks_like_api_key(bearer.credentials):
            raise _api_key_not_accepted()
        raw_token = bearer.credentials
        source = "bearer"
    elif cookie_token:
        raw_token = cookie_token
        source = "cookie"
    else:
        return None
    return _resolve_actor(
        request,
        raw_token=raw_token,
        source=source,
        db=db,
        settings=settings,
    )


def _verify_csrf(
    actor: Actor, *, csrf_cookie: str | None, csrf_header: str | None, settings: Settings
) -> Actor:
    # Header-carried credentials (bearer tokens, API keys) are not sent by
    # browsers automatically, so they need no CSRF proof; cookie sessions do.
    if actor.token_source in ("bearer", "api_key"):
        return actor
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_failed", "message": "CSRF validation failed"},
        )
    presented_hash = token_digest(f"csrf:{csrf_header}", settings)
    if actor.session is None or not hmac.compare_digest(
        actor.session.csrf_token_hash, presented_hash
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_failed", "message": "CSRF validation failed"},
        )
    return actor


async def require_csrf(
    request: Request,
    actor: Actor = Depends(require_actor),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    settings: Settings = Depends(get_settings),
) -> Actor:
    return _verify_csrf(actor, csrf_cookie=csrf_cookie, csrf_header=csrf_header, settings=settings)


async def _require_csrf_or_api_key(
    actor: Actor = Depends(require_actor_or_api_key),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    settings: Settings = Depends(get_settings),
) -> Actor:
    return _verify_csrf(actor, csrf_cookie=csrf_cookie, csrf_header=csrf_header, settings=settings)


async def require_workspace(
    actor: Actor = Depends(require_actor_or_api_key),
    workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    db: Session = Depends(get_db),
) -> WorkspaceContext:
    """Resolve the caller's *current* membership from server-side state.

    The header only selects among workspaces the caller already belongs to; it
    never confers access.  Memberships outside their validity window, inactive
    memberships and non-active workspaces are invisible here, so a still-valid
    session loses access on the very next request after revocation.  An API
    key is pinned to the workspace it belongs to: a header naming another
    workspace is refused, and the bound user's membership there is required.
    """
    return _resolve_workspace(actor, workspace_id, db)


def _resolve_workspace(actor: Actor, workspace_id: str | None, db: Session) -> WorkspaceContext:
    now = datetime.now(UTC)
    if actor.api_key is not None:
        if workspace_id and workspace_id != actor.api_key.workspace_id:
            raise HTTPException(
                status_code=403,
                detail={"code": "workspace_access_denied", "message": "Workspace access denied"},
            )
        workspace_id = actor.api_key.workspace_id
    query = (
        select(Workspace, Membership)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == actor.user.id, selectable_workspace_clause(now))
    )
    if workspace_id:
        query = query.where(Workspace.id == workspace_id)
    else:
        query = query.order_by(Membership.created_at.asc()).limit(1)
    row = db.execute(query).first()
    if row is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "workspace_access_denied", "message": "Workspace access denied"},
        )
    workspace, membership = row
    # Only now, after the membership was validated server-side, does the
    # database transaction learn which tenant this request acts for.
    bind_tenant_context(db, workspace_id=workspace.id, organization_id=workspace.organization_id)
    record_access = resolve_record_access(db, workspace=workspace, membership=membership, now=now)
    return WorkspaceContext(
        actor=actor,
        workspace=workspace,
        membership=membership,
        record_access=record_access,
    )


def require_permission(permission: Permission):
    async def dependency(
        context: WorkspaceContext = Depends(require_workspace),
    ) -> WorkspaceContext:
        if not context.scope_permits(permission):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "api_key_scope_denied",
                    "message": "The API key does not carry this scope",
                },
            )
        if not context.has(permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "message": "Permission denied"},
            )
        return context

    # Policy metadata read by the route-coverage test (never by the runtime).
    dependency.homayar_permission = permission  # type: ignore[attr-defined]
    dependency.homayar_write = False  # type: ignore[attr-defined]
    return dependency


def require_path_permission(permission: Permission):
    """Like ``require_permission``, with the workspace named in the path
    (``/workspaces/{workspace_id}/...``) instead of the ``X-Workspace-ID``
    header.  For resources a browser element fetches by itself (``<audio>``
    cannot send headers, gap G11): the session cookie authenticates, the path
    only selects among the caller's current memberships exactly as the header
    does, and every request - each Range request of a seeking player - is
    authorized again.  No credential ever appears in the URL."""

    async def dependency(
        workspace_id: str,
        actor: Actor = Depends(require_actor_or_api_key),
        db: Session = Depends(get_db),
    ) -> WorkspaceContext:
        context = _resolve_workspace(actor, workspace_id, db)
        if not context.scope_permits(permission):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "api_key_scope_denied",
                    "message": "The API key does not carry this scope",
                },
            )
        if not context.has(permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "message": "Permission denied"},
            )
        return context

    dependency.homayar_permission = permission  # type: ignore[attr-defined]
    dependency.homayar_write = False  # type: ignore[attr-defined]
    return dependency


def require_write_permission(permission: Permission):
    permission_dependency = require_permission(permission)

    async def dependency(
        context: WorkspaceContext = Depends(permission_dependency),
        csrf_actor: Actor = Depends(_require_csrf_or_api_key),
    ) -> WorkspaceContext:
        if context.actor.user.id != csrf_actor.user.id:
            raise _unauthorized("Session identity mismatch")
        return context

    dependency.homayar_permission = permission  # type: ignore[attr-defined]
    dependency.homayar_write = True  # type: ignore[attr-defined]
    return dependency


# --- organization scope (WP03-T03) ---------------------------------------------------


async def require_organization(
    organization_id: str,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
) -> OrganizationContext:
    """Resolve the caller's effective membership of the path organization.

    The path segment only names the organization; membership state decides.
    Revoked, inactive or expired memberships and non-active organizations are
    invisible, so a still-valid session loses the organization on the very
    next request — and an organization the caller does not belong to looks
    exactly like one that does not exist (``404``).
    """
    now = datetime.now(UTC)
    row = db.execute(
        select(Organization, OrganizationMembership)
        .join(
            OrganizationMembership,
            OrganizationMembership.organization_id == Organization.id,
        )
        .where(
            Organization.id == organization_id,
            Organization.status == "active",
            OrganizationMembership.user_id == actor.user.id,
            organization_membership_effective_clause(now),
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "organization_not_found", "message": "Organization not found"},
        )
    organization, membership = row
    # Only now, after the membership was validated server-side, does the
    # transaction learn which organization this request acts for.
    bind_organization_context(db, organization.id)
    access = resolve_organization_access(db, membership=membership, now=now)
    return OrganizationContext(
        actor=actor, organization=organization, membership=membership, access=access
    )


def require_organization_permission(permission: OrganizationPermission):
    async def dependency(
        context: OrganizationContext = Depends(require_organization),
    ) -> OrganizationContext:
        if not context.has(permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "message": "Permission denied"},
            )
        return context

    # Policy metadata read by the route-coverage test (never by the runtime).
    dependency.homayar_permission = permission  # type: ignore[attr-defined]
    dependency.homayar_write = False  # type: ignore[attr-defined]
    return dependency


def require_organization_write_permission(permission: OrganizationPermission):
    permission_dependency = require_organization_permission(permission)

    async def dependency(
        context: OrganizationContext = Depends(permission_dependency),
        csrf_actor: Actor = Depends(require_csrf),
    ) -> OrganizationContext:
        if context.actor.user.id != csrf_actor.user.id:
            raise _unauthorized("Session identity mismatch")
        return context

    dependency.homayar_permission = permission  # type: ignore[attr-defined]
    dependency.homayar_write = True  # type: ignore[attr-defined]
    return dependency
