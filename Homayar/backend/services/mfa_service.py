"""Privileged MFA: TOTP enrolment, verification and recovery codes (HOM-129, ADR 0076).

* The TOTP secret is stored only encrypted: AES-256-GCM under ``MFA_SECRET_KEYS``
  (the ``PII_MAPPING_KEYS`` keyring format), with the user id as associated data,
  so a ciphertext copied to another user's row does not decrypt.  The secret
  appears once, in the enrolment response; never in logs or audit details.
* A re-enrolment writes the *pending* columns; the confirmed factor keeps working
  until a code from the new secret confirms it.
* Recovery codes: ten per set, single use, stored only as a peppered digest.  A
  new set replaces the old one.
* A session is verified when its ``mfa_verified_at`` is younger than
  ``PRIVILEGED_MFA_MAX_AGE_MINUTES``; ``security.context`` asks for that on every
  privileged request.

Functions flush and never commit; the caller commits.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    LoginSession,
    Membership,
    MfaRecoveryCode,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    User,
    UserMfaTotp,
    Workspace,
    WorkspaceRole,
)
from backend.privacy.mapping_crypto import parse_keyring
from backend.security.authorization import (
    organization_membership_effective_clause,
    selectable_workspace_clause,
)
from backend.security.tokens import token_digest
from backend.security.totp import generate_totp_secret, otpauth_uri, verify_totp
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.speech.text_normalize import normalize_digits

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 10
PENDING_ENROLMENT_MINUTES = 15
PRIVILEGED_WORKSPACE_ROLES = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})
PRIVILEGED_ORGANIZATION_ROLES = frozenset({OrganizationRole.OWNER, OrganizationRole.ADMIN})
_RECOVERY_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


class MfaError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def mfa_unavailable() -> MfaError:
    return MfaError(503, "mfa_unavailable", "Two-factor authentication is not configured")


def mfa_required() -> MfaError:
    return MfaError(403, "mfa_required", "Verify your second factor to continue")


def mfa_enrollment_required() -> MfaError:
    return MfaError(403, "mfa_enrollment_required", "Set up two-factor authentication to continue")


def mfa_code_invalid() -> MfaError:
    return MfaError(400, "mfa_code_invalid", "The code is not valid")


def mfa_not_enrolled() -> MfaError:
    return MfaError(409, "mfa_not_enrolled", "Two-factor authentication is not set up")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- secret encryption ------------------------------------------------------------------


def _aad(user_id: str) -> bytes:
    return f"mfa-totp:{user_id}".encode()


def _keyring(settings: Settings) -> tuple[dict[str, bytes], str]:
    if not settings.mfa_secret_keys:
        raise mfa_unavailable()
    try:
        keyring = parse_keyring(settings.mfa_secret_keys)
    except ValueError as exc:
        raise mfa_unavailable() from exc
    if settings.mfa_secret_active_key_id not in keyring:
        raise mfa_unavailable()
    return keyring, settings.mfa_secret_active_key_id


def encrypt_totp_secret(
    secret_b32: str, *, user_id: str, settings: Settings
) -> tuple[str, bytes, bytes]:
    """``(key_id, nonce, ciphertext)`` of a secret, bound to ``user_id``."""
    keyring, key_id = _keyring(settings)
    nonce = os.urandom(12)
    ciphertext = AESGCM(keyring[key_id]).encrypt(nonce, secret_b32.encode(), _aad(user_id))
    return key_id, nonce, ciphertext


def decrypt_totp_secret(
    *,
    key_id: str | None,
    nonce: bytes | None,
    ciphertext: bytes | None,
    user_id: str,
    settings: Settings,
) -> str:
    keyring, _ = _keyring(settings)
    key = keyring.get(key_id or "")
    if key is None or nonce is None or ciphertext is None:
        raise mfa_unavailable()
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _aad(user_id)).decode()
    except InvalidTag as exc:
        raise mfa_unavailable() from exc


# --- state ------------------------------------------------------------------------------


def session_passes_mfa_gate(
    session: LoginSession | None, *, settings: Settings, now: datetime | None = None
) -> bool:
    if session is None or session.mfa_verified_at is None:
        return False
    now = now or datetime.now(UTC)
    age = now - _aware(session.mfa_verified_at)
    return age <= timedelta(minutes=settings.privileged_mfa_max_age_minutes)


def confirmed_totp(db: Session, *, user_id: str, lock: bool = False) -> UserMfaTotp | None:
    query = select(UserMfaTotp).where(
        UserMfaTotp.user_id == user_id, UserMfaTotp.confirmed_at.is_not(None)
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def has_confirmed_totp(db: Session, *, user_id: str) -> bool:
    return confirmed_totp(db, user_id=user_id) is not None


def recovery_codes_remaining(db: Session, *, user_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None))
        )
        or 0
    )


def user_is_privileged(db: Session, *, user: User, now: datetime | None = None) -> bool:
    """Owner/admin of a workspace or an organization right now, or a platform operator."""
    if user.system_role == "operator":
        return True
    now = now or datetime.now(UTC)
    workspace_role = db.scalar(
        select(Membership.id)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(
            Membership.user_id == user.id,
            Membership.role.in_(PRIVILEGED_WORKSPACE_ROLES),
            selectable_workspace_clause(now),
        )
        .limit(1)
    )
    if workspace_role is not None:
        return True
    organization_role = db.scalar(
        select(OrganizationMembership.id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.role.in_(PRIVILEGED_ORGANIZATION_ROLES),
            Organization.status == "active",
            organization_membership_effective_clause(now),
        )
        .limit(1)
    )
    return organization_role is not None


def mfa_status(
    db: Session,
    *,
    user: User,
    session: LoginSession | None,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, object]:
    now = now or datetime.now(UTC)
    enrolled = has_confirmed_totp(db, user_id=user.id)
    return {
        "totp_enrolled": enrolled,
        "recovery_codes_remaining": recovery_codes_remaining(db, user_id=user.id)
        if enrolled
        else 0,
        "session_verified": session_passes_mfa_gate(session, settings=settings, now=now),
        "required": settings.privileged_mfa_required and user_is_privileged(db, user=user, now=now),
    }


# --- recovery codes ---------------------------------------------------------------------


def normalize_recovery_code(code: str) -> str:
    return normalize_digits(code).replace("-", "").replace(" ", "").strip().lower()


def _recovery_code_digest(user_id: str, normalized: str, settings: Settings) -> str:
    return token_digest(f"mfa_recovery:{user_id}:{normalized}", settings)


def _issue_recovery_codes(
    db: Session, *, user_id: str, settings: Settings, now: datetime
) -> list[str]:
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes: set[str] = set()
    while len(codes) < RECOVERY_CODE_COUNT:
        codes.add("".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH)))
    for code in codes:
        db.add(
            MfaRecoveryCode(
                user_id=user_id,
                code_hash=_recovery_code_digest(user_id, code, settings),
                created_at=now,
            )
        )
    db.flush()
    half = RECOVERY_CODE_LENGTH // 2
    return [f"{code[:half]}-{code[half:]}" for code in sorted(codes)]


# --- enrolment --------------------------------------------------------------------------


def _check_enrolment_session(
    db: Session,
    *,
    user: User,
    session: LoginSession,
    settings: Settings,
    now: datetime,
) -> None:
    """A user with a factor re-enrols from a verified session; a user without one
    only within the fresh-login window, so a stolen old session cannot enrol."""
    if has_confirmed_totp(db, user_id=user.id):
        if not session_passes_mfa_gate(session, settings=settings, now=now):
            raise mfa_required()
        return
    login_age = now - _aware(session.created_at)
    if login_age > timedelta(minutes=settings.mfa_enrollment_fresh_login_minutes):
        raise MfaError(
            403,
            "mfa_reauthentication_required",
            "Sign in again to set up two-factor authentication",
        )


def _account_label(user: User) -> str:
    return user.email or user.phone_e164 or user.id


def start_totp_enrollment(
    db: Session,
    *,
    user: User,
    session: LoginSession,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, str]:
    """``(secret, otpauth_uri)`` of a new pending factor; it replaces an older pending one."""
    now = now or datetime.now(UTC)
    _keyring(settings)
    _check_enrolment_session(db, user=user, session=session, settings=settings, now=now)
    secret = generate_totp_secret()
    key_id, nonce, ciphertext = encrypt_totp_secret(secret, user_id=user.id, settings=settings)
    row = db.scalar(select(UserMfaTotp).where(UserMfaTotp.user_id == user.id).with_for_update())
    if row is None:
        row = UserMfaTotp(user_id=user.id)
        db.add(row)
    row.pending_encryption_key_id = key_id
    row.pending_nonce = nonce
    row.pending_ciphertext = ciphertext
    row.pending_created_at = now
    db.flush()
    return secret, otpauth_uri(
        secret, account=_account_label(user), issuer=settings.mfa_totp_issuer
    )


def _mark_session_verified(session: LoginSession, *, method: str, now: datetime) -> None:
    session.mfa_verified_at = now
    session.mfa_method = method


def confirm_totp_enrollment(
    db: Session,
    *,
    user: User,
    session: LoginSession,
    code: str,
    settings: Settings,
    request_id: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Make the pending factor the confirmed one; returns the new recovery codes."""
    now = now or datetime.now(UTC)
    _keyring(settings)
    _check_enrolment_session(db, user=user, session=session, settings=settings, now=now)
    row = db.scalar(select(UserMfaTotp).where(UserMfaTotp.user_id == user.id).with_for_update())
    if (
        row is None
        or row.pending_ciphertext is None
        or row.pending_created_at is None
        or now - _aware(row.pending_created_at) > timedelta(minutes=PENDING_ENROLMENT_MINUTES)
    ):
        raise MfaError(409, "mfa_enrollment_not_started", "Start the two-factor setup again")
    secret = decrypt_totp_secret(
        key_id=row.pending_encryption_key_id,
        nonce=row.pending_nonce,
        ciphertext=row.pending_ciphertext,
        user_id=user.id,
        settings=settings,
    )
    # Persian and Arabic-Indic digits count as the same code, as on the OTP route.
    code = normalize_digits(code)
    step = verify_totp(secret, code, unix_time=int(now.timestamp()), last_used_step=None)
    if step is None:
        raise mfa_code_invalid()
    row.encryption_key_id = row.pending_encryption_key_id
    row.nonce = row.pending_nonce
    row.ciphertext = row.pending_ciphertext
    row.confirmed_at = now
    row.last_used_step = step
    row.pending_encryption_key_id = None
    row.pending_nonce = None
    row.pending_ciphertext = None
    row.pending_created_at = None
    codes = _issue_recovery_codes(db, user_id=user.id, settings=settings, now=now)
    _mark_session_verified(session, method="totp", now=now)
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_MFA_TOTP_ENROLLED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request_id=request_id,
        settings=settings,
    )
    db.flush()
    return codes


# --- verification -----------------------------------------------------------------------


def _is_totp_code(code: str) -> bool:
    candidate = code.replace(" ", "")
    return len(candidate) == 6 and candidate.isascii() and candidate.isdigit()


def verify_mfa(
    db: Session,
    *,
    user: User,
    session: LoginSession,
    code: str,
    settings: Settings,
    request_id: str | None = None,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Verify this session with a TOTP or a recovery code; ``(method, codes_remaining)``.

    Raises ``mfa_code_invalid`` for a wrong, replayed or used code; the caller
    rolls back and records the refused attempt.
    """
    now = now or datetime.now(UTC)
    row = confirmed_totp(db, user_id=user.id, lock=True)
    if row is None:
        raise mfa_not_enrolled()
    code = normalize_digits(code)
    if _is_totp_code(code):
        secret = decrypt_totp_secret(
            key_id=row.encryption_key_id,
            nonce=row.nonce,
            ciphertext=row.ciphertext,
            user_id=user.id,
            settings=settings,
        )
        step = verify_totp(
            secret, code, unix_time=int(now.timestamp()), last_used_step=row.last_used_step
        )
        if step is None:
            raise mfa_code_invalid()
        row.last_used_step = step
        method = "totp"
        event_type = AuditEventType.AUTH_MFA_VERIFIED
    else:
        normalized = normalize_recovery_code(code)
        used = db.execute(
            update(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == _recovery_code_digest(user.id, normalized, settings),
                MfaRecoveryCode.used_at.is_(None),
            )
            .values(used_at=now)
            .execution_options(synchronize_session=False)
        )
        if used.rowcount != 1:
            raise mfa_code_invalid()
        method = "recovery_code"
        event_type = AuditEventType.AUTH_MFA_RECOVERY_CODE_USED
    _mark_session_verified(session, method=method, now=now)
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request_id=request_id,
        details={"method": method},
        settings=settings,
    )
    db.flush()
    return method, recovery_codes_remaining(db, user_id=user.id)


def regenerate_recovery_codes(
    db: Session,
    *,
    user: User,
    session: LoginSession,
    settings: Settings,
    request_id: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    if not has_confirmed_totp(db, user_id=user.id):
        raise mfa_not_enrolled()
    if not session_passes_mfa_gate(session, settings=settings, now=now):
        raise mfa_required()
    codes = _issue_recovery_codes(db, user_id=user.id, settings=settings, now=now)
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_MFA_RECOVERY_CODES_REGENERATED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request_id=request_id,
        settings=settings,
    )
    db.flush()
    return codes
