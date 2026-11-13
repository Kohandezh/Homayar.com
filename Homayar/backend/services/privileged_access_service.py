"""Account cut-off and break-glass access (HOM-129, ADR 0076).

Operators run these from the server host (``python -m backend.operator_access``):

* cut-off: disable or enable a user, revoke every session of a user, reset a user's
  MFA.  Each change acts on the next request (``security.context`` checks the user
  and the session on every request);
* break-glass: a local account with ``users.is_break_glass`` signs in by password
  only inside an open, time-boxed window.  Opening, closing and every login attempt
  are audited, counted by a metric and sent to ``BREAK_GLASS_ALERT_EMAILS``.

Functions take keyword arguments, flush and never commit (the caller commits), and
raise ``PrivilegedAccessError`` with a ``code`` on refusal.  Their audit events carry
no acting user (``actor`` in the details is the operator's name on the host).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.saas_models import (
    AuthIdentity,
    BreakGlassWindow,
    IdentityProvider,
    LoginSession,
    MfaRecoveryCode,
    User,
    UserMfaTotp,
)
from backend.observability.metrics import observe_break_glass_event
from backend.security.normalization import normalize_email
from backend.services import email_service
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event, record_audit_event_best_effort
from backend.services.auth_service import revoke_all_user_sessions
from backend.services.logging_service import logger

REASON_MAX_LENGTH = 500
ACTOR_MAX_LENGTH = 128


class PrivilegedAccessError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _user_by_email(db: Session, email: str, *, lock: bool = False) -> User:
    try:
        normalized = normalize_email(email)
    except ValueError as exc:
        raise PrivilegedAccessError("user_not_found", "No user with this email") from exc
    query = select(User).where(User.email == normalized)
    if lock:
        query = query.with_for_update()
    user = db.scalar(query)
    if user is None:
        raise PrivilegedAccessError("user_not_found", "No user with this email")
    return user


def _reason(reason: str | None) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise PrivilegedAccessError("reason_required", "A reason is required")
    return cleaned[:REASON_MAX_LENGTH]


def _audit(
    db: Session,
    event_type: AuditEventType,
    user: User,
    *,
    actor_label: str,
    settings: Settings,
    reason: str | None = None,
    **details: Any,
) -> None:
    entry: dict[str, Any] = {"actor": actor_label[:ACTOR_MAX_LENGTH]}
    if reason is not None:
        entry["reason"] = reason
    entry.update(details)
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=None,
        target_type="user",
        target_id=user.id,
        details=entry,
        settings=settings,
    )


# --- cut-off (D6) -----------------------------------------------------------------------


def disable_user(
    db: Session,
    *,
    email: str,
    reason: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> User:
    """Refuse every access token, refresh token, API key and login of the user."""
    reason = _reason(reason)
    user = _user_by_email(db, email)
    user.is_active = False
    revoke_all_user_sessions(db, user)
    _audit(
        db,
        AuditEventType.AUTH_USER_DISABLED,
        user,
        actor_label=actor_label,
        settings=settings,
        reason=reason,
    )
    db.flush()
    return user


def enable_user(
    db: Session,
    *,
    email: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> User:
    """Allow new logins again; sessions revoked by ``disable_user`` stay revoked."""
    user = _user_by_email(db, email)
    user.is_active = True
    _audit(db, AuditEventType.AUTH_USER_ENABLED, user, actor_label=actor_label, settings=settings)
    db.flush()
    return user


def revoke_user_sessions(
    db: Session,
    *,
    email: str,
    reason: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Revoke every session of the user; returns how many were still unrevoked."""
    reason = _reason(reason)
    user = _user_by_email(db, email)
    revoked = revoke_all_user_sessions(db, user)
    _audit(
        db,
        AuditEventType.AUTH_SESSIONS_REVOKED,
        user,
        actor_label=actor_label,
        settings=settings,
        reason=reason,
        sessions=revoked,
    )
    db.flush()
    return revoked


def reset_user_mfa(
    db: Session,
    *,
    email: str,
    reason: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> User:
    """Remove the TOTP factor and the recovery codes; the user enrols again after a
    new login (within the fresh-login window)."""
    reason = _reason(reason)
    user = _user_by_email(db, email)
    db.execute(delete(UserMfaTotp).where(UserMfaTotp.user_id == user.id))
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    revoke_all_user_sessions(db, user)
    _audit(
        db,
        AuditEventType.AUTH_MFA_RESET,
        user,
        actor_label=actor_label,
        settings=settings,
        reason=reason,
    )
    db.flush()
    return user


# --- break-glass (D7) -------------------------------------------------------------------


def send_break_glass_alert(
    settings: Settings, *, event: str, user_email: str | None, details: dict
) -> None:
    """One email per address in ``BREAK_GLASS_ALERT_EMAILS``; never raises.

    ``details`` holds operator names, reasons, times and the login method only.
    """
    recipients = [
        address.strip()
        for address in settings.break_glass_alert_emails.split(",")
        if address.strip()
    ]
    if not recipients:
        logger.warning("BREAK_GLASS_ALERT_NO_RECIPIENTS event=%s", event)
        return
    subject = f"Homayar break-glass: {event}"
    lines = [
        f"Break-glass event: {event}",
        f"Account: {user_email or '-'}",
        f"Time (UTC): {datetime.now(UTC).isoformat(timespec='seconds')}",
        *(f"{key}: {value}" for key, value in sorted(details.items())),
        "",
        "Runbook: docs/operations/break-glass.md",
    ]
    for address in recipients:
        try:
            delivered = email_service.send_email_sync(address, subject, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - an alert never breaks the action
            logger.error(
                "BREAK_GLASS_ALERT_FAILED event=%s error_type=%s", event, type(exc).__name__
            )
            continue
        if not delivered:
            logger.error("BREAK_GLASS_ALERT_NOT_SENT event=%s", event)


def notify_break_glass(
    settings: Settings, *, event: str, user_email: str | None, details: dict
) -> None:
    """Count the event and alert; a failing alert seam is logged, never raised."""
    observe_break_glass_event(event)
    try:
        send_break_glass_alert(settings, event=event, user_email=user_email, details=details)
    except Exception as exc:  # noqa: BLE001 - an alert never breaks the action
        logger.error("BREAK_GLASS_ALERT_FAILED event=%s error_type=%s", event, type(exc).__name__)


def window_is_open(window: BreakGlassWindow, *, settings: Settings, now: datetime) -> bool:
    return (
        settings.break_glass_enabled
        and window.closed_at is None
        and _aware(window.expires_at) > now
    )


def _unclosed_window(db: Session, *, user_id: str, now: datetime) -> BreakGlassWindow | None:
    return db.scalar(
        select(BreakGlassWindow)
        .where(
            BreakGlassWindow.user_id == user_id,
            BreakGlassWindow.closed_at.is_(None),
            BreakGlassWindow.expires_at > now,
        )
        .order_by(BreakGlassWindow.expires_at.desc())
        .limit(1)
    )


def current_break_glass_window(
    db: Session, *, user_id: str, settings: Settings, now: datetime | None = None
) -> BreakGlassWindow | None:
    """The user's open window, or ``None`` (always ``None`` while the setting is off)."""
    if not settings.break_glass_enabled:
        return None
    return _unclosed_window(db, user_id=user_id, now=now or datetime.now(UTC))


def session_window_is_open(
    db: Session, *, session: LoginSession, settings: Settings, now: datetime
) -> bool:
    """A break-glass account's session serves only while its own window is open."""
    if session.break_glass_window_id is None:
        return False
    window = db.get(BreakGlassWindow, session.break_glass_window_id)
    return window is not None and window_is_open(window, settings=settings, now=now)


def _designated_user(db: Session, email: str, *, lock: bool = False) -> User:
    user = _user_by_email(db, email, lock=lock)
    if not user.is_break_glass:
        raise PrivilegedAccessError("not_designated", "The account is not a break-glass account")
    return user


def designate_break_glass(
    db: Session,
    *,
    email: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> User:
    if not settings.break_glass_enabled:
        raise PrivilegedAccessError("break_glass_disabled", "BREAK_GLASS_ENABLED is false")
    user = _user_by_email(db, email)
    if not user.password_hash:
        raise PrivilegedAccessError("password_required", "The account has no password")
    federated = db.scalar(
        select(AuthIdentity.id).where(
            AuthIdentity.user_id == user.id,
            AuthIdentity.provider == IdentityProvider.OIDC,
            AuthIdentity.revoked_at.is_(None),
        )
    )
    if federated is not None:
        raise PrivilegedAccessError(
            "federated_account", "A federated account cannot be a break-glass account"
        )
    user.is_break_glass = True
    revoke_all_user_sessions(db, user)
    _audit(
        db,
        AuditEventType.AUTH_BREAK_GLASS_DESIGNATED,
        user,
        actor_label=actor_label,
        settings=settings,
    )
    db.flush()
    return user


def undesignate_break_glass(
    db: Session,
    *,
    email: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> User:
    """Allowed with the setting off too, so an operator can always clean up."""
    now = now or datetime.now(UTC)
    user = _user_by_email(db, email)
    window = _unclosed_window(db, user_id=user.id, now=now)
    if window is not None:
        window.closed_at = now
        window.closed_by = actor_label[:ACTOR_MAX_LENGTH]
    user.is_break_glass = False
    revoke_all_user_sessions(db, user)
    _audit(
        db,
        AuditEventType.AUTH_BREAK_GLASS_UNDESIGNATED,
        user,
        actor_label=actor_label,
        settings=settings,
        window_closed=window is not None,
    )
    db.flush()
    return user


def open_break_glass_window(
    db: Session,
    *,
    email: str,
    minutes: int,
    reason: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> BreakGlassWindow:
    now = now or datetime.now(UTC)
    if not settings.break_glass_enabled:
        raise PrivilegedAccessError("break_glass_disabled", "BREAK_GLASS_ENABLED is false")
    # The users row is the lock: before the first window there is no window row to lock,
    # so two concurrent opens serialize here and the second sees the first's window.
    user = _designated_user(db, email, lock=True)
    reason = _reason(reason)
    if minutes < 1:
        raise PrivilegedAccessError("window_invalid", "A window lasts at least 1 minute")
    if minutes > settings.break_glass_max_window_minutes:
        raise PrivilegedAccessError(
            "window_too_long",
            f"A window lasts at most {settings.break_glass_max_window_minutes} minutes",
        )
    if _unclosed_window(db, user_id=user.id, now=now) is not None:
        raise PrivilegedAccessError("window_already_open", "A window is already open")
    window = BreakGlassWindow(
        user_id=user.id,
        reason=reason,
        opened_by=actor_label[:ACTOR_MAX_LENGTH],
        opened_at=now,
        expires_at=now + timedelta(minutes=minutes),
    )
    db.add(window)
    db.flush()
    expires_at = window.expires_at.isoformat()
    _audit(
        db,
        AuditEventType.AUTH_BREAK_GLASS_OPENED,
        user,
        actor_label=actor_label,
        settings=settings,
        reason=reason,
        minutes=minutes,
        expires_at=expires_at,
    )
    db.flush()
    notify_break_glass(
        settings,
        event="window_opened",
        user_email=user.email,
        details={
            "actor": actor_label[:ACTOR_MAX_LENGTH],
            "reason": reason,
            "minutes": minutes,
            "expires_at": expires_at,
        },
    )
    return window


def close_break_glass_window(
    db: Session,
    *,
    email: str,
    actor_label: str,
    settings: Settings,
    now: datetime | None = None,
) -> BreakGlassWindow | None:
    """Close the open window (if any) and end every session at once; allowed with the
    setting off too."""
    now = now or datetime.now(UTC)
    user = _user_by_email(db, email)
    window = _unclosed_window(db, user_id=user.id, now=now)
    if window is not None:
        window.closed_at = now
        window.closed_by = actor_label[:ACTOR_MAX_LENGTH]
    revoke_all_user_sessions(db, user)
    _audit(
        db,
        AuditEventType.AUTH_BREAK_GLASS_CLOSED,
        user,
        actor_label=actor_label,
        settings=settings,
        window_closed=window is not None,
    )
    db.flush()
    notify_break_glass(
        settings,
        event="window_closed",
        user_email=user.email,
        details={"actor": actor_label[:ACTOR_MAX_LENGTH], "window_closed": window is not None},
    )
    return window


def list_break_glass_status(
    db: Session, *, settings: Settings, now: datetime | None = None
) -> tuple[list[User], list[tuple[User, BreakGlassWindow]]]:
    """Designated accounts and their open windows (no secrets)."""
    now = now or datetime.now(UTC)
    designated = list(db.scalars(select(User).where(User.is_break_glass.is_(True))))
    windows = [
        (user, window)
        for user in designated
        if (window := current_break_glass_window(db, user_id=user.id, settings=settings, now=now))
        is not None
    ]
    return designated, windows


# --- login path ---------------------------------------------------------------------------


def record_break_glass_login(
    db: Session,
    *,
    user: User,
    method: str,
    request_id: str | None,
    ip_address: str | None,
    settings: Settings,
) -> None:
    """Audit a successful break-glass login in the login's own transaction."""
    record_audit_event(
        db,
        event_type=AuditEventType.AUTH_BREAK_GLASS_LOGIN,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request_id=request_id,
        ip_address=ip_address,
        details={"method": method},
        settings=settings,
    )


def record_break_glass_login_refused(
    db: Session,
    *,
    user_id: str,
    method: str,
    request_id: str | None,
    ip_address: str | None,
    settings: Settings,
) -> None:
    """Best-effort audit of a refused break-glass login (``db`` holds no other work)."""
    record_audit_event_best_effort(
        db,
        event_type=AuditEventType.AUTH_BREAK_GLASS_LOGIN_REFUSED,
        outcome="denied",
        actor_user_id=user_id,
        target_type="user",
        target_id=user_id,
        request_id=request_id,
        ip_address=ip_address,
        details={"method": method},
        settings=settings,
    )
