from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    AuthIdentity,
    IdentityProvider,
    LoginSession,
    OneTimeToken,
    User,
    new_id,
    utcnow,
)
from backend.security.normalization import normalize_email
from backend.security.tokens import (
    create_access_token,
    hash_password,
    make_refresh_token,
    parse_refresh_session_id,
    random_token,
    token_digest,
    verify_password,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.tenancy_service import create_personal_workspace


class AuthenticationError(ValueError):
    pass


class RefreshTokenReplayError(AuthenticationError):
    """Raised after the compromised session has been marked for revocation."""


class RegistrationConflictError(ValueError):
    pass


class RegistrationClosedError(ValueError):
    """REGISTRATION_ENABLED is false and no open invitation covers this signup."""


class InvalidOneTimeTokenError(ValueError):
    pass


class VerificationNotApplicableError(ValueError):
    """The account has no e-mail address to verify (phone sign-up)."""


VERIFY_EMAIL_TTL_MINUTES = 24 * 60


@dataclass(frozen=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_in: int


def _hash_context(value: str | None, settings: Settings) -> str | None:
    return token_digest(value, settings) if value else None


def issue_one_time_token(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    ttl_minutes: int,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    db.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user_id,
            OneTimeToken.purpose == purpose,
            OneTimeToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    raw_token = random_token(40)
    db.add(
        OneTimeToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_digest(f"{purpose}:{raw_token}", settings),
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
    )
    db.flush()
    return raw_token


def consume_one_time_token(
    db: Session,
    *,
    raw_token: str,
    purpose: str,
    settings: Settings | None = None,
) -> User:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    digest = token_digest(f"{purpose}:{raw_token}", settings)
    token = db.scalar(
        select(OneTimeToken)
        .where(
            OneTimeToken.token_hash == digest,
            OneTimeToken.purpose == purpose,
            OneTimeToken.consumed_at.is_(None),
            OneTimeToken.expires_at > now,
        )
        .with_for_update()
    )
    if token is None:
        raise InvalidOneTimeTokenError("Invalid or expired token")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise InvalidOneTimeTokenError("Invalid or expired token")
    token.consumed_at = now
    db.flush()
    return user


def register_email_user(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    locale: str = "fa",
    settings: Settings | None = None,
) -> tuple[User, str]:
    settings = settings or get_settings()
    normalized_email = normalize_email(email)
    existing = db.scalar(select(User).where(User.email == normalized_email))
    if existing is not None:
        raise RegistrationConflictError("An account already exists for this email")

    user = User(
        email=normalized_email,
        display_name=display_name.strip()[:160],
        password_hash=hash_password(password),
        locale=locale if locale in {"fa", "en"} else "fa",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=IdentityProvider.PASSWORD,
            subject=normalized_email,
            verified_at=utcnow(),
            profile={},
        )
    )
    create_personal_workspace(db, user=user, settings=settings)
    verification_token = issue_one_time_token(
        db,
        user_id=user.id,
        purpose="verify_email",
        ttl_minutes=VERIFY_EMAIL_TTL_MINUTES,
        settings=settings,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_REGISTER,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        details={"method": "email"},
        settings=settings,
    )
    try:
        db.flush()
    except IntegrityError as exc:
        raise RegistrationConflictError("An account already exists for this email") from exc
    return user, verification_token


def verify_email(
    db: Session, raw_token: str, settings: Settings | None = None
) -> User:
    settings = settings or get_settings()
    user = consume_one_time_token(
        db, raw_token=raw_token, purpose="verify_email", settings=settings
    )
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_EMAIL_VERIFIED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        settings=settings,
    )
    return user


def email_verification_pending(user: User) -> bool:
    """Whether a verification link can still change anything for ``user``."""
    if not user.email:
        raise VerificationNotApplicableError("The account has no e-mail address")
    return user.email_verified_at is None


def reissue_email_verification(
    db: Session, *, user: User, settings: Settings | None = None
) -> str:
    """A fresh verification token; every older unconsumed one is superseded
    (``issue_one_time_token``), so only the newest link can verify."""
    settings = settings or get_settings()
    token = issue_one_time_token(
        db,
        user_id=user.id,
        purpose="verify_email",
        ttl_minutes=VERIFY_EMAIL_TTL_MINUTES,
        settings=settings,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_VERIFICATION_RESENT,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        settings=settings,
    )
    return token


def authenticate_password(
    db: Session,
    *,
    identifier: str,
    password: str,
    settings: Settings | None = None,
) -> User:
    settings = settings or get_settings()
    if identifier == "demo" and settings.enable_demo_account:
        normalized_email = "demo@local.homayar"
    else:
        try:
            normalized_email = normalize_email(identifier)
        except ValueError:
            normalized_email = "invalid@invalid.local"
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not verify_password(password, user.password_hash if user else None):
        raise AuthenticationError("Invalid credentials")
    assert user is not None
    if not user.is_active:
        raise AuthenticationError("Account is disabled")
    user.last_login_at = datetime.now(UTC)
    return user


def create_login_session(
    db: Session,
    *,
    user: User,
    user_agent: str | None,
    ip_address: str | None,
    settings: Settings | None = None,
) -> SessionTokens:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    session_id = new_id()
    refresh_token = make_refresh_token(session_id)
    csrf_token = random_token(32)
    session = LoginSession(
        id=session_id,
        user_id=user.id,
        refresh_token_hash=token_digest(f"refresh:{refresh_token}", settings),
        csrf_token_hash=token_digest(f"csrf:{csrf_token}", settings),
        user_agent_hash=_hash_context(user_agent, settings),
        ip_prefix=_hash_context(ip_address, settings),
        expires_at=now + timedelta(days=settings.refresh_token_days),
        last_seen_at=now,
    )
    db.add(session)
    db.flush()
    access_token = create_access_token(
        user_id=user.id,
        session_id=session.id,
        session_version=user.session_version,
        settings=settings,
        now=now,
    )
    db.flush()
    return SessionTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        access_expires_in=settings.access_token_minutes * 60,
    )


def rotate_refresh_token(
    db: Session,
    *,
    raw_refresh_token: str,
    csrf_token: str,
    settings: Settings | None = None,
) -> tuple[User, SessionTokens]:
    settings = settings or get_settings()
    session_id = parse_refresh_session_id(raw_refresh_token)
    if session_id is None:
        raise AuthenticationError("Invalid refresh token")
    session = db.scalar(
        select(LoginSession).where(LoginSession.id == session_id).with_for_update()
    )
    now = datetime.now(UTC)
    if session is None:
        raise AuthenticationError("Invalid refresh token")
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if session.revoked_at is not None or expires_at <= now:
        raise AuthenticationError("Invalid refresh token")
    presented_hash = token_digest(f"refresh:{raw_refresh_token}", settings)
    if not hmac.compare_digest(session.refresh_token_hash, presented_hash):
        session.revoked_at = now
        db.flush()
        raise RefreshTokenReplayError("Refresh token replay detected")
    presented_csrf_hash = token_digest(f"csrf:{csrf_token}", settings)
    if not hmac.compare_digest(session.csrf_token_hash, presented_csrf_hash):
        raise AuthenticationError("Invalid CSRF token")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        session.revoked_at = now
        raise AuthenticationError("Account is unavailable")

    new_refresh = make_refresh_token(session.id)
    new_csrf = random_token(32)
    session.refresh_token_hash = token_digest(f"refresh:{new_refresh}", settings)
    session.csrf_token_hash = token_digest(f"csrf:{new_csrf}", settings)
    session.last_seen_at = now
    access = create_access_token(
        user_id=user.id,
        session_id=session.id,
        session_version=user.session_version,
        settings=settings,
        now=now,
    )
    db.flush()
    return user, SessionTokens(
        access_token=access,
        refresh_token=new_refresh,
        csrf_token=new_csrf,
        access_expires_in=settings.access_token_minutes * 60,
    )


def revoke_session(db: Session, session_id: str) -> None:
    session = db.get(LoginSession, session_id)
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)


def revoke_all_user_sessions(db: Session, user: User) -> None:
    now = datetime.now(UTC)
    db.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    user.session_version += 1


def request_password_reset(
    db: Session,
    email: str,
    settings: Settings | None = None,
) -> tuple[User, str] | None:
    settings = settings or get_settings()
    try:
        normalized = normalize_email(email)
    except ValueError:
        return None
    user = db.scalar(select(User).where(User.email == normalized, User.is_active.is_(True)))
    if user is None:
        return None
    token = issue_one_time_token(
        db, user_id=user.id, purpose="reset_password", ttl_minutes=30, settings=settings
    )
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_PASSWORD_RESET_REQUESTED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        settings=settings,
    )
    return user, token


def reset_password(
    db: Session,
    *,
    raw_token: str,
    new_password: str,
    settings: Settings | None = None,
) -> User:
    settings = settings or get_settings()
    user = consume_one_time_token(
        db, raw_token=raw_token, purpose="reset_password", settings=settings
    )
    user.password_hash = hash_password(new_password)
    revoke_all_user_sessions(db, user)
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_PASSWORD_RESET_COMPLETED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        settings=settings,
    )
    return user


def ensure_development_demo_account(
    db: Session, settings: Settings | None = None
) -> User | None:
    settings = settings or get_settings()
    if not settings.enable_demo_account:
        return None
    if settings.environment == "production":
        raise RuntimeError("Demo account cannot be enabled in production")
    email = "demo@local.homayar"
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return user
    user = User(
        email=email,
        display_name="Demo",
        password_hash=hash_password("demo"),
        email_verified_at=datetime.now(UTC),
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=IdentityProvider.PASSWORD,
            subject=email,
            verified_at=utcnow(),
            profile={"development_only": True},
        )
    )
    create_personal_workspace(db, user=user, name="Demo Workspace", settings=settings)
    db.flush()
    return user
