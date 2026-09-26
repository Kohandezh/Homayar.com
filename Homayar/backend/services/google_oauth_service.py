from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    AuthIdentity,
    IdentityProvider,
    OAuthChallenge,
    User,
    utcnow,
)
from backend.security.normalization import normalize_email
from backend.security.tokens import random_token, token_digest
from backend.services.audit_service import record_audit_event
from backend.services.tenancy_service import create_personal_workspace


class GoogleOAuthError(ValueError):
    pass


class AccountLinkRequiredError(GoogleOAuthError):
    pass


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_google_oauth(
    db: Session,
    *,
    intent: str = "login",
    link_user_id: str | None = None,
    redirect_path: str = "/app",
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    if not settings.enable_google_auth:
        raise GoogleOAuthError("Google authentication is disabled")
    if intent not in {"login", "link"}:
        raise GoogleOAuthError("Unsupported OAuth intent")
    if intent == "link" and not link_user_id:
        raise GoogleOAuthError("Authenticated account required for linking")
    if not redirect_path.startswith("/app") or redirect_path.startswith("//"):
        redirect_path = "/app"

    state = random_token(32)
    nonce = random_token(32)
    verifier = random_token(64)
    challenge = OAuthChallenge(
        state_hash=token_digest(f"oauth-state:{state}", settings),
        nonce_hash=token_digest(f"oauth-nonce:{nonce}", settings),
        code_verifier=verifier,
        intent=intent,
        link_user_id=link_user_id,
        redirect_path=redirect_path[:240],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(challenge)
    db.flush()
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


async def complete_google_oauth(
    db: Session,
    *,
    state: str,
    code: str,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[User, str]:
    settings = settings or get_settings()
    if not settings.enable_google_auth:
        raise GoogleOAuthError("Google authentication is disabled")
    now = datetime.now(UTC)
    challenge = db.scalar(
        select(OAuthChallenge)
        .where(
            OAuthChallenge.state_hash == token_digest(f"oauth-state:{state}", settings),
            OAuthChallenge.consumed_at.is_(None),
        )
        .with_for_update()
    )
    if challenge is None:
        raise GoogleOAuthError("OAuth state is invalid")
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise GoogleOAuthError("OAuth state has expired")
    challenge.consumed_at = now

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=10)
    try:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.google_redirect_uri,
                "code_verifier": challenge.code_verifier,
            },
        )
        response.raise_for_status()
        token_payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise GoogleOAuthError("Google token exchange failed") from exc
    finally:
        if owns_client:
            await client.aclose()
    raw_id_token = token_payload.get("id_token")
    if not raw_id_token:
        raise GoogleOAuthError("Google did not return an ID token")
    try:
        claims = google_id_token.verify_oauth2_token(
            raw_id_token, GoogleAuthRequest(), settings.google_client_id
        )
    except Exception as exc:
        raise GoogleOAuthError("Google ID token validation failed") from exc
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise GoogleOAuthError("Unexpected Google token issuer")
    if not claims.get("email_verified"):
        raise GoogleOAuthError("Google email is not verified")
    nonce = claims.get("nonce", "")
    if not hmac.compare_digest(
        challenge.nonce_hash, token_digest(f"oauth-nonce:{nonce}", settings)
    ):
        raise GoogleOAuthError("OAuth nonce validation failed")
    subject = str(claims.get("sub", ""))
    if not subject:
        raise GoogleOAuthError("Google subject is missing")
    email = normalize_email(str(claims.get("email", "")))

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == IdentityProvider.GOOGLE,
            AuthIdentity.subject == subject,
        )
    )
    if challenge.intent == "link":
        user = db.get(User, challenge.link_user_id)
        if user is None or not user.is_active:
            raise GoogleOAuthError("Linking account is unavailable")
        if identity is not None and identity.user_id != user.id:
            raise GoogleOAuthError("This Google identity belongs to another account")
        if identity is None:
            existing_provider = db.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.user_id == user.id,
                    AuthIdentity.provider == IdentityProvider.GOOGLE,
                )
            )
            if existing_provider is not None:
                raise GoogleOAuthError("A Google identity is already linked")
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider=IdentityProvider.GOOGLE,
                    subject=subject,
                    verified_at=utcnow(),
                    profile={"email": email},
                )
            )
        if user.email is None:
            email_owner = db.scalar(select(User).where(User.email == email))
            if email_owner is not None and email_owner.id != user.id:
                raise GoogleOAuthError("Google email belongs to another account")
            user.email = email
            user.email_verified_at = now
    elif identity is not None:
        user = db.get(User, identity.user_id)
        if user is None or not user.is_active:
            raise GoogleOAuthError("Account is unavailable")
    else:
        email_owner = db.scalar(select(User).where(User.email == email))
        if email_owner is not None:
            raise AccountLinkRequiredError(
                "Sign in to your existing account before linking Google"
            )
        user = User(
            email=email,
            email_verified_at=now,
            display_name=str(claims.get("name", ""))[:160],
            locale="fa",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(
            AuthIdentity(
                user_id=user.id,
                provider=IdentityProvider.GOOGLE,
                subject=subject,
                verified_at=utcnow(),
                profile={"email": email},
            )
        )
        create_personal_workspace(db, user=user, settings=settings)

    user.last_login_at = now
    record_audit_event(
        db,
        event_type="auth.google_completed",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        details={"intent": challenge.intent},
        settings=settings,
    )
    db.flush()
    return user, challenge.redirect_path

