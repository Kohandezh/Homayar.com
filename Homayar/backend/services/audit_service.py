"""Application audit events (WP20-T02/T03, HOM-114, HOM-115; ADR 0036).

Model
-----
One ``audit_events`` row names the **actor** (``actor_user_id``; ``NULL`` for
the system, whose component is then named by the correlation id
``job:<id>`` / ``sweep:<task>:<run>``), the **tenant** (``workspace_id``;
``NULL`` for platform events such as a login), the **action**
(``event_type``, catalogued in ``AUDIT_EVENT_CATALOG``), the **target**
(``target_type`` / ``target_id``), the **time** (``occurred_at``), the
**result** (``outcome``) and the **correlation id** (``request_id``: the
request's ``X-Request-ID`` or the bound correlation id when the caller passes
none).  ``details`` holds ids, counts and enum values; secrets are redacted at
write time, personal data in free-text values (reasons, labels, integration
identifiers) is masked with the text PII detector, and the client IP is stored
only as a peppered hash.

Delivery
--------
* **Transactional** (every catalogued event except the refused attempts
  below): the row is added to the caller's session and commits *with* the
  operation it describes.  If the audit insert fails, the transaction rolls
  back and the operation does not happen (fail closed); the failure is counted
  in ``homayar_audit_write_failures_total{delivery="transactional"}``.
* **Best effort** (``record_audit_event_best_effort``): only for attempts the
  application refused (a failed login, a replayed refresh token), where there
  is no operation to roll back.  The response is unchanged when the audit
  write fails; the failure is logged (``AUDIT_WRITE_FAILED``, no details) and
  counted with ``delivery="best_effort"``.

"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.saas_models import AuditEvent
from backend.observability.correlation import current_correlation_id
from backend.observability.scrub import looks_like_secret
from backend.security.tokens import token_digest

SENSITIVE_DETAIL_KEYS = {
    "password",
    "passphrase",
    "token",
    "access_token",
    "refresh_token",
    "otp",
    "code",
    "secret",
    "private_key",
    "authorization",
    "cookie",
    "transcript",
}
MAX_DETAIL_STRING_LENGTH = 500
REDACTED = "[redacted]"
# Free text a user or an integration types (owner decision 2026-10-17, option B):
# personal data in it is replaced by class placeholders ("[PHONE_1]", "[EMAIL_1]",
# national id, bank card, IBAN, postal code) with the text PII detector, then the
# value is capped. Ids, counts and enum values stay as they are.
FREE_TEXT_DETAIL_KEYS = frozenset(
    {"reason", "release_reason", "deletion_reason", "name", "external_event_id",
     "source_provider"}
)
MAX_FREE_TEXT_LENGTH = 120

# Delivery classes (see the module docstring).
TRANSACTIONAL = "transactional"
BEST_EFFORT = "best_effort"

# Every event type the application records, by category (HOM-114). The audit
# coverage test fails when code records a type that is not listed here.
AUDIT_EVENT_CATALOG: dict[str, str] = {
    # Authentication and security changes
    "auth.register": "security",
    "auth.email_verified": "security",
    "auth.verification_resent": "security",
    "auth.login": "security",
    "auth.logout": "security",
    "auth.refresh_replay_detected": "security",
    "auth.password_reset_requested": "security",
    "auth.password_reset_completed": "security",
    "auth.otp_requested": "security",
    "auth.otp_verified": "security",
    "auth.google_completed": "security",
    "api_key.created": "security",
    "api_key.revoked": "security",
    # Roles, memberships and access grants
    "member.invitation.created": "access",
    "member.invitation.delivery_failed": "access",
    "member.invitation.revoked": "access",
    "member.invitation.accepted": "access",
    "member.role_changed": "access",
    "member.removed": "access",
    "team.created": "access",
    "team.updated": "access",
    "team.assignment.created": "access",
    "team.assignment.revoked": "access",
    "organization.member.granted": "access",
    "organization.member.changed": "access",
    "organization.member.revoked": "access",
    "organization.workspace.created": "access",
    "organization.workspace.member_installed": "access",
    "organization.workspace.member_removed": "access",
    "organization.scope.granted": "access",
    "organization.scope.revoked": "access",
    "meeting.share_created": "access",
    "meeting.share_recipients_changed": "access",
    "meeting.share_revoked": "access",
    # Privacy-sensitive reads, original/unredacted access and exports
    "transcript.original_accessed": "privacy_access",
    "pii.revealed": "privacy_access",
    "subject.exported": "privacy_access",
    "meeting.exported": "privacy_access",
    "meeting.share_accessed": "privacy_access",
    # Configuration and policy changes
    "workspace.settings.updated": "configuration",
    "ai_policy.updated": "configuration",
    "model_manifest.registered": "configuration",
    "service_location.created": "configuration",
    "service_location.updated": "configuration",
    "service_location.deleted": "configuration",
    "voice.node.registered": "configuration",
    "voice.node.drain_changed": "configuration",
    # Administrative mutations of processing
    "meeting.processing.replayed": "administration",
    "meeting.purge_requeued": "administration",
    # Deletion, retention and legal hold
    "meeting.deleted": "deletion",
    "meeting.retention_expired": "deletion",
    "meeting.purge_completed": "deletion",
    "meeting.purge_failed": "deletion",
    "meeting.tombstone_reapplied": "deletion",
    "meeting.recording.deleted_after_processing": "deletion",
    "meeting.recording.retained_after_processing": "deletion",
    "media.deletion_requested": "deletion",
    "media.deleted": "deletion",
    "media.orphan_removed": "deletion",
    "media.legal_hold_placed": "deletion",
    "media.legal_hold_released": "deletion",
    # Content and processing lifecycle
    "meeting.uploaded": "processing",
    "meeting.updated": "processing",
    "meeting.speaker_renamed": "processing",
    "meeting.processing.completed": "processing",
    "meeting.processing.failed": "processing",
    "meeting.processing.retry_scheduled": "processing",
    "meeting.processing.lease_expired": "processing",
    "meeting.processing.cancelled": "processing",
    "ingestion.accepted": "processing",
    "ingestion.replayed": "processing",
    "ingestion.conflict": "processing",
    "ingestion.rejected": "processing",
    "ingestion.duplicate_media": "processing",
    "media.registered": "processing",
    "media.integrity_checked": "processing",
    "media.orphans_reported": "processing",
    "usage.reconciled": "processing",
    "transcript.redaction_created": "processing",
    "transcript.revision.created": "processing",
    "action_item.created": "processing",
    "action_item.updated": "processing",
    "action_item.deleted": "processing",
    "voicemail.classified": "processing",
    "voicemail.updated": "processing",
}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(sensitive in lowered for sensitive in SENSITIVE_DETAIL_KEYS)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        if looks_like_secret(value):
            return REDACTED
        return value[:MAX_DETAIL_STRING_LENGTH]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:300]


def _is_free_text_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in FREE_TEXT_DETAIL_KEYS or lowered.endswith("_reason")


def _mask_free_text(value: str) -> str:
    """Personal data replaced by class placeholders, then capped (masking first, so
    the cap can never leave half an identifier behind)."""
    from backend.privacy.text_pii import redact

    if looks_like_secret(value):
        return REDACTED
    masked, _ = redact(value)
    return masked[:MAX_FREE_TEXT_LENGTH]


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in details.items():
        if _is_sensitive_key(key):
            sanitized[key] = REDACTED
        elif isinstance(value, str) and _is_free_text_key(key):
            sanitized[key] = _mask_free_text(value)
        else:
            sanitized[key] = _sanitize_value(value)
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


def record_audit_event_best_effort(db: Session, **fields: Any) -> bool:
    """Record and commit one event for a *refused* attempt; never raises.

    Use only where there is no operation to roll back (a failed login, a
    replayed refresh token).  ``db`` must hold no other pending work: the event
    commits on its own.
    """
    from backend.observability.metrics import observe_audit_write_failure
    from backend.services.logging_service import logger

    try:
        record_audit_event(db, **fields)
        db.commit()
    except Exception:
        db.rollback()
        observe_audit_write_failure(BEST_EFFORT)
        logger.warning(
            "AUDIT_WRITE_FAILED delivery=%s event_type=%s", BEST_EFFORT, fields.get("event_type")
        )
        return False
    return True


@event.listens_for(Engine, "handle_error")
def _count_audit_insert_failures(context: Any) -> None:
    statement = (context.statement or "").lstrip()[:40].upper()
    if statement.startswith("INSERT INTO AUDIT_EVENTS"):
        from backend.observability.metrics import observe_audit_write_failure

        observe_audit_write_failure(TRANSACTIONAL)
