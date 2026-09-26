from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from backend.config import Settings, get_settings

_password_hash = PasswordHash.recommended()
_dummy_password_hash = _password_hash.hash("homayar-timing-defense-only")
_development_secret = secrets.token_urlsafe(48)


class AccessTokenError(ValueError):
    pass


def _runtime_secret(settings: Settings) -> str:
    secret = settings.token_pepper or settings.jwt_secret
    if secret:
        return secret
    if settings.environment in {"development", "test"}:
        return _development_secret
    raise RuntimeError("TOKEN_PEPPER is not configured")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _dummy_password_hash
    try:
        valid = _password_hash.verify(password, candidate_hash)
    except Exception:
        valid = False
    return bool(valid and password_hash)


def random_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def token_digest(value: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return hmac.new(
        _runtime_secret(settings).encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def safe_fingerprint(value: str, settings: Settings | None = None) -> str:
    return token_digest(value, settings)[:16]


def create_access_token(
    *,
    user_id: str,
    session_id: str,
    session_version: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    settings = settings or get_settings()
    if not settings.jwt_secret:
        if settings.environment not in {"development", "test"}:
            raise RuntimeError("JWT_SECRET is not configured")
        secret = _development_secret
    else:
        secret = settings.jwt_secret
    now = now or datetime.now(UTC)
    payload = {
        "sub": user_id,
        "sid": session_id,
        "sv": session_version,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": random_token(16),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(
    token: str,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    secret = settings.jwt_secret or (
        _development_secret if settings.environment in {"development", "test"} else ""
    )
    if not secret:
        raise AccessTokenError("JWT secret is not configured")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "sid", "sv", "type", "exp", "iat", "jti"]},
        )
    except InvalidTokenError as exc:
        raise AccessTokenError("Invalid or expired access token") from exc
    if payload.get("type") != "access":
        raise AccessTokenError("Unexpected token type")
    if now is not None and datetime.fromtimestamp(payload["exp"], UTC) <= now:
        raise AccessTokenError("Access token expired")
    return payload


def make_refresh_token(session_id: str) -> str:
    return f"{session_id}.{random_token(48)}"


def parse_refresh_session_id(token: str) -> str | None:
    session_id, separator, _secret = token.partition(".")
    if not separator:
        return None
    try:
        import uuid

        uuid.UUID(session_id)
    except (ValueError, TypeError):
        return None
    return session_id

