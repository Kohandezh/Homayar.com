from __future__ import annotations

import base64
import binascii
import copy
import ipaddress
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.database.saas_models import (
    AuditEvent,
    DuplicateMediaPolicy,
    RecordScope,
    RetentionMode,
    Workspace,
)
from backend.media.channel_roles import (
    SETTINGS_KEY,
    ChannelRoleMapping,
    InvalidChannelRoleMapping,
    active_mappings,
    mapping_digest,
    mapping_entry,
    mapping_from_settings,
    readable_entries,
    source_key,
    stored_version,
    validate_mapping_channels,
    validate_source,
)
from backend.privacy.text_pii import parse_classes
from backend.services.retention_policy import (
    MAX_RETENTION_DAYS,
    InvalidRetentionPolicy,
    validate_retention_policy,
)

MAX_AUDIT_DETAIL_DEPTH = 4
MAX_AUDIT_DETAIL_ITEMS = 50
MAX_AUDIT_DETAIL_STRING_LENGTH = 500
ALLOWED_WORKSPACE_SETTINGS = frozenset(
    {
        "name",
        "retention_days",
        "retention_mode",
        "delete_audio_after_processing",
        "record_scope",
        "public_share_links_enabled",
        "organization_share_links_enabled",
        "duplicate_media_policy",
        "pii_redaction_enabled",
        "pii_redaction_classes",
    }
)

_SENSITIVE_DETAIL_TERMS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "credential",
    "otp",
    "transcript",
)
_JWT_PATTERN = re.compile(r"^eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class InvalidAuditCursor(ValueError):
    pass


class InvalidAuditRange(ValueError):
    pass


class WorkspaceNotFoundError(LookupError):
    pass


class InvalidWorkspaceSettings(ValueError):
    pass


@dataclass(frozen=True)
class AuditEventPage:
    events: list[AuditEvent]
    next_cursor: str | None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _encode_audit_cursor(event: AuditEvent) -> str:
    payload = {
        "v": 1,
        "occurred_at": _normalize_datetime(event.occurred_at).isoformat(),
        "id": event.id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_audit_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "occurred_at", "id"}:
            raise ValueError
        if (
            payload["v"] != 1
            or not isinstance(payload["occurred_at"], str)
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        event_id = str(uuid.UUID(payload["id"]))
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        if occurred_at.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidAuditCursor("Invalid audit pagination cursor") from exc
    return occurred_at, event_id


def list_workspace_audit_events(
    db: Session,
    *,
    workspace_id: str,
    limit: int,
    cursor: str | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
) -> AuditEventPage:
    if occurred_after is not None and occurred_before is not None:
        if _normalize_datetime(occurred_after) >= _normalize_datetime(occurred_before):
            raise InvalidAuditRange("occurred_after must be earlier than occurred_before")

    query = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
    if event_type is not None:
        query = query.where(AuditEvent.event_type == event_type)
    if outcome is not None:
        query = query.where(AuditEvent.outcome == outcome)
    if actor_user_id is not None:
        query = query.where(AuditEvent.actor_user_id == actor_user_id)
    if target_type is not None:
        query = query.where(AuditEvent.target_type == target_type)
    if target_id is not None:
        query = query.where(AuditEvent.target_id == target_id)
    if occurred_after is not None:
        query = query.where(AuditEvent.occurred_at >= occurred_after)
    if occurred_before is not None:
        query = query.where(AuditEvent.occurred_at < occurred_before)
    if cursor is not None:
        cursor_time, cursor_id = _decode_audit_cursor(cursor)
        query = query.where(
            or_(
                AuditEvent.occurred_at < cursor_time,
                and_(AuditEvent.occurred_at == cursor_time, AuditEvent.id < cursor_id),
            )
        )

    events = list(
        db.scalars(
            query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
        )
    )
    has_more = len(events) > limit
    page_events = events[:limit]
    next_cursor = _encode_audit_cursor(page_events[-1]) if has_more else None
    return AuditEventPage(events=page_events, next_cursor=next_cursor)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return (
        any(term in lowered for term in _SENSITIVE_DETAIL_TERMS)
        or lowered == "ip"
        or lowered.startswith("ip_")
        or lowered.endswith("_ip")
        or lowered in {"ipaddress", "ip_address"}
    )


def _is_sensitive_string(value: str) -> bool:
    stripped = value.strip()
    if stripped.casefold().startswith("bearer ") or stripped.startswith(("sk-", "sk_")):
        return True
    if _JWT_PATTERN.fullmatch(stripped):
        return True
    try:
        ipaddress.ip_address(stripped)
    except ValueError:
        return False
    return True


def _sanitize_audit_value(value: Any, *, depth: int) -> Any:
    if depth >= MAX_AUDIT_DETAIL_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (raw_key, nested_value) in enumerate(value.items()):
            if index >= MAX_AUDIT_DETAIL_ITEMS:
                sanitized["_truncated"] = True
                break
            key = str(raw_key)[:100]
            sanitized[key] = (
                "[redacted]"
                if _is_sensitive_key(key)
                else _sanitize_audit_value(nested_value, depth=depth + 1)
            )
        return sanitized
    if isinstance(value, list):
        return [
            _sanitize_audit_value(item, depth=depth + 1) for item in value[:MAX_AUDIT_DETAIL_ITEMS]
        ]
    if isinstance(value, str):
        if _is_sensitive_string(value):
            return "[redacted]"
        return value[:MAX_AUDIT_DETAIL_STRING_LENGTH]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return str(value)[:MAX_AUDIT_DETAIL_STRING_LENGTH]


def sanitize_audit_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    sanitized = _sanitize_audit_value(details, depth=0)
    return sanitized if isinstance(sanitized, dict) else {}


def get_workspace_settings(db: Session, *, workspace_id: str) -> Workspace:
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.status == "active",
        )
    )
    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found")
    return workspace


def update_workspace_settings(
    db: Session,
    *,
    workspace_id: str,
    changes: dict[str, Any],
) -> tuple[Workspace, tuple[str, ...]]:
    unknown_fields = set(changes) - ALLOWED_WORKSPACE_SETTINGS
    if unknown_fields:
        raise InvalidWorkspaceSettings("Unknown workspace setting")
    if not changes:
        raise InvalidWorkspaceSettings("At least one workspace setting is required")

    workspace = get_workspace_settings(db, workspace_id=workspace_id)
    for field, value in changes.items():
        if field == "name":
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 180:
                raise InvalidWorkspaceSettings("Invalid workspace name")
            workspace.name = value.strip()
        elif field in ("retention_days", "retention_mode"):
            continue  # validated together below: the pair is one policy
        elif field == "delete_audio_after_processing":
            if not isinstance(value, bool):
                raise InvalidWorkspaceSettings("Invalid audio deletion setting")
            workspace.delete_audio_after_processing = value
        elif field == "record_scope":
            try:
                workspace.record_scope = RecordScope(value)
            except ValueError as exc:
                raise InvalidWorkspaceSettings("Invalid record scope") from exc
        elif field == "public_share_links_enabled":
            if not isinstance(value, bool):
                raise InvalidWorkspaceSettings("Invalid public share link setting")
            workspace.public_share_links_enabled = value
        elif field == "organization_share_links_enabled":
            if not isinstance(value, bool):
                raise InvalidWorkspaceSettings("Invalid organization share link setting")
            workspace.organization_share_links_enabled = value
        elif field == "duplicate_media_policy":
            try:
                workspace.duplicate_media_policy = DuplicateMediaPolicy(value)
            except ValueError as exc:
                raise InvalidWorkspaceSettings("Invalid duplicate media policy") from exc
        elif field == "pii_redaction_enabled":
            if not isinstance(value, bool):
                raise InvalidWorkspaceSettings("Invalid PII redaction setting")
            workspace.pii_redaction_enabled = value
        elif field == "pii_redaction_classes":
            # WP07-T05: only detector-supported classes, at least one.
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise InvalidWorkspaceSettings("Invalid PII redaction classes")
            try:
                classes = parse_classes(value)
            except ValueError as exc:
                raise InvalidWorkspaceSettings("Unsupported PII redaction class") from exc
            if not classes:
                raise InvalidWorkspaceSettings("At least one PII redaction class is required")
            workspace.pii_redaction_classes = [c.value for c in classes]

    if "retention_days" in changes or "retention_mode" in changes:
        _apply_retention_policy(
            workspace,
            mode=changes.get("retention_mode"),
            retention_days=changes.get("retention_days"),
        )

    db.flush()
    return workspace, tuple(sorted(changes))


def _apply_retention_policy(workspace: Workspace, *, mode: Any, retention_days: Any) -> None:
    """Resolve a (mode, days) change against the current policy (ADR 0020).

    * ``retention_mode`` alone: ``FINITE`` keeps the current positive days
      (or needs ``retention_days``); the other modes zero the days.
    * ``retention_days`` alone: ``0`` is refused as ambiguous; a positive
      value is accepted when the current mode is ``FINITE`` or the legacy
      unresolved value (which it thereby resolves explicitly).  Under
      ``INDEFINITE``/``IMMEDIATE`` the mode must be sent as well.
    * ``LEGACY_UNRESOLVED`` can never be chosen.
    """
    if mode is not None:
        try:
            new_mode = RetentionMode(mode)
        except ValueError as exc:
            raise InvalidWorkspaceSettings("Invalid retention mode") from exc
        if new_mode is RetentionMode.LEGACY_UNRESOLVED:
            raise InvalidWorkspaceSettings(
                "The legacy unresolved retention value cannot be chosen; pick an explicit mode"
            )
    else:
        new_mode = workspace.retention_mode
        if retention_days == 0:
            raise InvalidWorkspaceSettings(
                "retention_days 0 is ambiguous; set retention_mode to indefinite or immediate"
            )
        if new_mode is RetentionMode.LEGACY_UNRESOLVED:
            new_mode = RetentionMode.FINITE  # a positive number is an explicit finite choice
        elif new_mode is not RetentionMode.FINITE:
            raise InvalidWorkspaceSettings(
                f"retention_days has no meaning under {new_mode.value}; set retention_mode=finite"
            )

    if retention_days is None:
        new_days = workspace.retention_days if new_mode is RetentionMode.FINITE else 0
        if new_mode is RetentionMode.FINITE and new_days < 1:
            raise InvalidWorkspaceSettings(
                f"Finite retention needs retention_days between 1 and {MAX_RETENTION_DAYS}"
            )
    else:
        if type(retention_days) is not int:
            raise InvalidWorkspaceSettings("Invalid retention period")
        new_days = retention_days

    try:
        validate_retention_policy(new_mode, new_days)
    except InvalidRetentionPolicy as exc:
        raise InvalidWorkspaceSettings(str(exc)) from exc
    workspace.retention_mode = new_mode
    workspace.retention_days = new_days


# --- Trusted channel-role mappings (HOM-37, ADR 0039) -----------------------------------


class ChannelRoleMappingNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ChannelRoleMappingRemoval:
    """What a removal recorded: the new version number and the removed content.

    ``removed`` is None when the removed entry was unreadable; then ``digest`` is
    None and ``channels`` is empty.
    """

    source_type: str
    source_provider: str
    version: int
    digest: str | None
    channels: tuple[tuple[int, str], ...]
    previous_version: int | None
    removed: ChannelRoleMapping | None


def _locked_workspace(db: Session, *, workspace_id: str) -> Workspace:
    """The caller's workspace row, locked so concurrent mapping writes serialise."""
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.status == "active")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found")
    return workspace


def _stored_entry(workspace: Workspace, key: str) -> tuple[bool, ChannelRoleMapping | None]:
    """``(present, mapping)`` for one source key.

    Raises :class:`UnreadableChannelRoleMappings` when the settings or the
    whole mapping value are not an object (a write must not replace them).
    ``mapping`` is None for a removed entry and for an unreadable one.
    """
    entries = readable_entries(workspace.settings_json)
    if key not in entries:
        return False, None
    try:
        source_type, source_provider = key.split(":", 1)
        return True, mapping_from_settings(
            workspace.settings_json, source_type=source_type, source_provider=source_provider
        )
    except InvalidChannelRoleMapping:
        return True, None


def _entry_is_active(workspace: Workspace, key: str) -> bool:
    entry = readable_entries(workspace.settings_json).get(key)
    return not (isinstance(entry, dict) and entry.get("active") is False)


def _write_entry(workspace: Workspace, key: str, entry: dict[str, Any]) -> None:
    settings_json = copy.deepcopy(dict(workspace.settings_json or {}))
    entries = dict(readable_entries(settings_json))
    entries[key] = entry
    settings_json[SETTINGS_KEY] = entries
    workspace.settings_json = settings_json
    flag_modified(workspace, "settings_json")


def list_channel_role_mappings(db: Session, *, workspace_id: str) -> list[ChannelRoleMapping]:
    """Readable active mappings; unreadable ones are left out, never an error."""
    return active_mappings(get_workspace_settings(db, workspace_id=workspace_id).settings_json)


def set_channel_role_mapping(
    db: Session,
    *,
    workspace_id: str,
    source_type: str,
    source_provider: str,
    channels: Sequence[tuple[int, str]],
    actor_user_id: str,
    now: datetime,
    max_channels: int,
) -> tuple[ChannelRoleMapping, ChannelRoleMapping | None, bool]:
    """Set or replace a source's mapping: ``(current, previous, changed)``.

    The same content as the active mapping changes nothing (``changed`` False).
    An unreadable entry of this source is replaced; unreadable settings as a
    whole raise :class:`UnreadableChannelRoleMappings` and nothing changes.
    """
    source_type, source_provider = validate_source(source_type, source_provider)
    ordered = validate_mapping_channels(channels, max_channels=max_channels)
    workspace = _locked_workspace(db, workspace_id=workspace_id)
    key = source_key(source_type, source_provider)
    _, previous = _stored_entry(workspace, key)
    digest = mapping_digest(source_type, source_provider, ordered)
    if previous is not None and previous.digest == digest:
        return previous, previous, False
    mapping = ChannelRoleMapping(
        source_type=source_type,
        source_provider=source_provider,
        version=stored_version(workspace.settings_json, key) + 1,
        digest=digest,
        channels=ordered,
        updated_at=now,
        updated_by_user_id=actor_user_id,
    )
    _write_entry(workspace, key, mapping_entry(mapping))
    db.flush()
    return mapping, previous, True


def remove_channel_role_mapping(
    db: Session,
    *,
    workspace_id: str,
    source_type: str,
    source_provider: str,
    actor_user_id: str,
    now: datetime,
) -> ChannelRoleMappingRemoval:
    """Remove a source's active (or unreadable) entry.

    The removal takes the next version number of the source, so no number is
    reused.  An unreadable entry can be removed too, so an administrator can
    clear it; unreadable settings as a whole raise
    :class:`UnreadableChannelRoleMappings` and nothing changes.
    """
    source_type, source_provider = validate_source(source_type, source_provider)
    workspace = _locked_workspace(db, workspace_id=workspace_id)
    key = source_key(source_type, source_provider)
    present, removed = _stored_entry(workspace, key)
    if not present or (removed is None and not _entry_is_active(workspace, key)):
        raise ChannelRoleMappingNotFound("No active channel role mapping for this source")
    previous_version = stored_version(workspace.settings_json, key)
    removal = ChannelRoleMappingRemoval(
        source_type=source_type,
        source_provider=source_provider,
        version=previous_version + 1,
        digest=removed.digest if removed else None,
        channels=removed.channels if removed else (),
        previous_version=previous_version or None,
        removed=removed,
    )
    _write_entry(
        workspace,
        key,
        {
            "source_type": source_type,
            "source_provider": source_provider,
            "version": removal.version,
            "digest": removal.digest,
            "active": False,
            "channels": [{"channel": channel, "role": role} for channel, role in removal.channels],
            "updated_at": now.isoformat(),
            "updated_by_user_id": actor_user_id,
        },
    )
    db.flush()
    return removal
