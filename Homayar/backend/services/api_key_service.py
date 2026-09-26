"""API keys (WP03-T07, ADR 0013).

An API key is a **workspace-owned, user-bound** credential:

* owner: exactly one workspace (``api_keys.workspace_id``; a workspace table
  under row-level security). Organization-owned keys and service principals
  without a user are *not* defined here (WP16-T02);
* principal: the user who created the key. Every request re-resolves that
  user's *current* membership in the key's workspace, so the key stops the
  moment the membership is revoked, expires or the account is disabled;
* authority: the effective permissions of a request are
  ``scopes ∩ API_KEY_SCOPES ∩ permissions of the current membership role``.
  Creation requires ``api_key:manage`` on the workspace and the requested
  scopes must be within the creator's own permissions; ``api_key:manage``
  itself is never grantable to a key, so keys cannot mint or widen keys;
* verifier: only the peppered SHA-256 digest of the raw key is stored
  (``key_hash``); the raw key is returned once at creation. ``key_prefix`` is
  a public identifier for listings and support, never a secret;
* limits: expiry is mandatory and bounded, live keys per workspace are
  bounded, requests per key are rate limited;
* revocation stops new requests at once (checked per request, no caching).
  Work already accepted through the key (uploads, processing jobs) belongs to
  the workspace and is neither cancelled nor lost by revoking the key.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import ApiKey, Membership, User, Workspace
from backend.database.tenant_context import bind_capability_context
from backend.security.authorization import API_KEY_SCOPES, Permission, role_has
from backend.security.tokens import token_digest
from backend.services.rate_limit_service import RateLimiter, RateLimitExceeded, RateLimitUnavailable

KEY_PREFIX = "hk_"
MAX_RAW_KEY_LENGTH = 128
# The last-use stamp is evidence, not metering: at most one write per interval.
LAST_USED_STAMP_INTERVAL = timedelta(seconds=60)


class ApiKeyError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **kwargs) -> None:
        super().__init__(
            status_code=status_code, detail={"code": code, "message": message}, **kwargs
        )


def api_key_invalid() -> ApiKeyError:
    # Unknown, revoked, expired and disabled keys look alike to the caller.
    return ApiKeyError(
        401, "api_key_invalid", "API key is not valid", headers={"WWW-Authenticate": "Bearer"}
    )


def looks_like_api_key(value: str) -> bool:
    return value.startswith(KEY_PREFIX)


def api_key_digest(raw_key: str, settings: Settings) -> str:
    return token_digest(f"api_key:{raw_key}", settings)


def generate_raw_key() -> tuple[str, str]:
    """Return ``(raw_key, key_prefix)``; the prefix is public, the rest is the secret."""
    prefix = secrets.token_hex(4)
    return f"{KEY_PREFIX}{prefix}.{secrets.token_urlsafe(32)}", prefix


def parse_scopes(values: Iterable[str]) -> frozenset[Permission]:
    scopes: set[Permission] = set()
    for value in values:
        try:
            permission = Permission(value)
        except ValueError as exc:
            raise ApiKeyError(422, "invalid_scopes", f"Unknown scope {value!r}") from exc
        if permission not in API_KEY_SCOPES:
            raise ApiKeyError(
                422, "invalid_scopes", f"Scope {value!r} cannot be granted to an API key"
            )
        scopes.add(permission)
    if not scopes:
        raise ApiKeyError(422, "invalid_scopes", "At least one scope is required")
    return frozenset(scopes)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


# --- management (session-authenticated owners/admins) -------------------------


def create_api_key(
    db: Session,
    *,
    workspace: Workspace,
    membership: Membership,
    name: str,
    scopes: Iterable[str],
    expires_in_days: int,
    settings: Settings,
) -> tuple[ApiKey, str]:
    """Issue a key bound to ``membership.user_id``; returns the row and the raw key (once)."""
    requested = parse_scopes(scopes)
    beyond = sorted(scope.value for scope in requested if not role_has(membership.role, scope))
    if beyond:
        raise ApiKeyError(
            403, "scope_grant_denied", "Scopes beyond your own permissions: " + ", ".join(beyond)
        )
    if not 1 <= expires_in_days <= settings.api_key_max_ttl_days:
        raise ApiKeyError(
            422,
            "invalid_expiry",
            f"Expiry must be between 1 and {settings.api_key_max_ttl_days} days",
        )
    now = datetime.now(UTC)
    live = db.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(
            ApiKey.workspace_id == workspace.id,
            ApiKey.revoked_at.is_(None),
            (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now),
        )
    )
    if (live or 0) >= settings.api_key_max_active_per_workspace:
        raise ApiKeyError(422, "too_many_api_keys", "Too many active API keys in this workspace")
    raw_key, prefix = generate_raw_key()
    key = ApiKey(
        workspace_id=workspace.id,
        created_by_user_id=membership.user_id,
        name=name.strip(),
        key_prefix=prefix,
        key_hash=api_key_digest(raw_key, settings),
        scopes=sorted(scope.value for scope in requested),
        expires_at=now + timedelta(days=expires_in_days),
    )
    db.add(key)
    db.flush()
    return key, raw_key


def list_api_keys(db: Session, *, workspace_id: str) -> list[ApiKey]:
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.workspace_id == workspace_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id)
        )
    )


def get_api_key(db: Session, *, workspace_id: str, key_id: str) -> ApiKey:
    key = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == workspace_id))
    if key is None:
        raise ApiKeyError(404, "api_key_not_found", "API key not found")
    return key


def revoke_api_key(db: Session, *, key: ApiKey, now: datetime | None = None) -> bool:
    """Revoke idempotently; returns whether this call changed the row."""
    if key.revoked_at is not None:
        return False
    key.revoked_at = now or datetime.now(UTC)
    db.flush()
    return True


# --- authentication (per request, nothing cached) ---------------------------


def authenticate_api_key(
    db: Session, *, raw_key: str, settings: Settings, now: datetime | None = None
) -> tuple[ApiKey, User]:
    """Resolve a presented raw key to its row and bound user, or raise ``401``.

    Binds only the key digest as a capability so the database exposes exactly
    the presented key's row; the tenant is bound later by ``require_workspace``
    after the bound user's current membership was validated.
    """
    if not looks_like_api_key(raw_key) or len(raw_key) > MAX_RAW_KEY_LENGTH:
        raise api_key_invalid()
    now = now or datetime.now(UTC)
    digest = api_key_digest(raw_key, settings)
    bind_capability_context(db, api_key_hash=digest)
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
    if key is None or key.revoked_at is not None:
        raise api_key_invalid()
    expires_at = _as_utc(key.expires_at)
    if expires_at is not None and expires_at <= now:
        raise api_key_invalid()
    user = db.get(User, key.created_by_user_id)
    if user is None or not user.is_active:
        raise api_key_invalid()
    try:
        RateLimiter(settings).hit(
            f"api-key:{key.id}",
            limit=settings.api_key_rate_limit_per_hour,
            window_seconds=3600,
        )
    except RateLimitExceeded as exc:
        raise ApiKeyError(
            429,
            "api_key_rate_limited",
            "Too many requests for this API key",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise ApiKeyError(
            503, "rate_limiter_unavailable", "API key service is temporarily unavailable"
        ) from exc
    last_used = _as_utc(key.last_used_at)
    if last_used is None or now - last_used >= LAST_USED_STAMP_INTERVAL:
        key.last_used_at = now
        db.flush()
    return key, user
