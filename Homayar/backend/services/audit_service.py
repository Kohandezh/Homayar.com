from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import AuditEvent
from backend.observability.correlation import current_correlation_id
from backend.security.tokens import token_digest

SENSITIVE_DETAIL_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "otp",
    "code",
    "secret",
    "transcript",
}


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in details.items():
        lowered = key.casefold()
        if any(sensitive in lowered for sensitive in SENSITIVE_DETAIL_KEYS):
            sanitized[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key] = value
        else:
            sanitized[key] = str(value)[:300]
    return sanitized


def record_audit_event(
    db: Session,
    *,
    event_type: str,
    actor_user_id: str | None = None,
    workspace_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    request_id: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> AuditEvent:
    settings = settings or get_settings()
    # Without an explicit request id the bound correlation id names the work
    # (request, ``job:<id>`` or ``sweep:<task>:<run>``, HOM-113).
    correlation = request_id or current_correlation_id()
    event = AuditEvent(
        event_type=event_type,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        request_id=correlation[:80] if correlation else None,
        ip_hash=token_digest(f"ip:{ip_address}", settings) if ip_address else None,
        details=_sanitize_details(details),
    )
    db.add(event)
    return event

