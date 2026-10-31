from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    AuthIdentity,
    IdentityProvider,
    OtpChallenge,
    User,
    utcnow,
)
from backend.security.normalization import normalize_iranian_mobile
from backend.security.tokens import token_digest
from backend.services.audit_service import record_audit_event
from backend.services.auth_service import RegistrationClosedError
from backend.services.tenancy_service import create_personal_workspace


class OtpCooldownError(ValueError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__("OTP resend cooldown is active")


class OtpVerificationError(ValueError):
    pass


class IdentityLinkConflictError(ValueError):
    pass


class PhoneAuthUnavailableError(RuntimeError):
    """Phone sign-in is disabled or its SMS provider is not configured."""


class OtpDeliveryError(RuntimeError):
    """The SMS provider did not accept the code; nothing is committed."""


def request_otp(
    db: Session,
    *,
    phone: str,
    send_code: Callable[[str, str], None],
    purpose: str = "login",
    link_user_id: str | None = None,
    settings: Settings | None = None,
) -> OtpChallenge:
    settings = settings or get_settings()
    if not settings.enable_phone_auth:
        raise PhoneAuthUnavailableError("Phone authentication is disabled")
    normalized = normalize_iranian_mobile(phone)
    now = datetime.now(UTC)
    latest = db.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.phone_e164 == normalized)
        .order_by(OtpChallenge.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        created_at = latest.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        elapsed = (now - created_at).total_seconds()
        if elapsed < settings.otp_resend_cooldown_seconds:
            raise OtpCooldownError(round(settings.otp_resend_cooldown_seconds - elapsed))

    # A new request supersedes every open challenge for this phone so that only
    # the most recently delivered code can authenticate.
    db.execute(
        update(OtpChallenge)
        .where(OtpChallenge.phone_e164 == normalized, OtpChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    challenge = OtpChallenge(
        phone_e164=normalized,
        code_hash="pending",
        purpose=purpose,
        link_user_id=link_user_id,
        max_attempts=settings.otp_max_attempts,
        expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
    )
    db.add(challenge)
    db.flush()
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge.code_hash = token_digest(f"otp:{challenge.id}:{code}", settings)
    send_code(normalized, code)
    record_audit_event(
        db,
        event_type="auth.otp_requested",
        actor_user_id=link_user_id,
        target_type="phone",
        target_id=token_digest(normalized, settings)[:16],
        details={"purpose": purpose},
        settings=settings,
    )
    db.flush()
    return challenge


def verify_otp(
    db: Session,
    *,
    challenge_id: str,
    code: str,
    settings: Settings | None = None,
) -> User:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    challenge = db.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.id == challenge_id)
        .with_for_update()
    )
    if challenge is None or challenge.consumed_at is not None:
        raise OtpVerificationError("OTP is invalid")
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise OtpVerificationError("OTP has expired")
    if challenge.attempts >= challenge.max_attempts:
        raise OtpVerificationError("OTP attempt limit exceeded")
    challenge.attempts += 1
    expected = token_digest(f"otp:{challenge.id}:{code.strip()}", settings)
    if not hmac.compare_digest(challenge.code_hash, expected):
        db.flush()
        raise OtpVerificationError("OTP is invalid")

    challenge.consumed_at = now
    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == IdentityProvider.PHONE,
            AuthIdentity.subject == challenge.phone_e164,
        )
    )
    if challenge.link_user_id:
        if identity is not None and identity.user_id != challenge.link_user_id:
            raise IdentityLinkConflictError("This mobile identity belongs to another account")
        user = db.get(User, challenge.link_user_id)
        if user is None or not user.is_active:
            raise OtpVerificationError("Account is unavailable")
        if identity is None:
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider=IdentityProvider.PHONE,
                    subject=challenge.phone_e164,
                    verified_at=utcnow(),
                    profile={},
                )
            )
        user.phone_e164 = challenge.phone_e164
        user.phone_verified_at = now
    elif identity is not None:
        user = db.get(User, identity.user_id)
        if user is None or not user.is_active:
            raise OtpVerificationError("Account is unavailable")
    else:
        if not settings.registration_enabled:
            raise RegistrationClosedError("Self-registration is closed on this server")
        user = User(
            phone_e164=challenge.phone_e164,
            phone_verified_at=now,
            display_name="",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(
            AuthIdentity(
                user_id=user.id,
                provider=IdentityProvider.PHONE,
                subject=challenge.phone_e164,
                verified_at=utcnow(),
                profile={},
            )
        )
        create_personal_workspace(db, user=user, settings=settings)

    record_audit_event(
        db,
        event_type="auth.otp_verified",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        details={"purpose": challenge.purpose},
        settings=settings,
    )
    db.flush()
    return user

