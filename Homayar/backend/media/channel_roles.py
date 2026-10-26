"""Trusted channel-to-role mappings of ingestion sources (HOM-37, ADR 0039).

A workspace administrator states which channel of a source carries which
party.  A source is ``(source_type, source_provider)`` inside one workspace,
the identity of the ingestion ledger (ADR 0016).  The mapping is the only
origin of a trusted role: nothing in the upload request, the file metadata,
an API-key scope or a heuristic can set one.

Storage is ``Workspace.settings_json[SETTINGS_KEY]``, one entry per source::

    {"file_upload:console": {"source_type": "file_upload", "source_provider": "console",
     "version": 2, "digest": "<sha256>", "active": true,
     "channels": [{"channel": 0, "role": "agent"}, ...],
     "updated_at": "<iso>", "updated_by_user_id": "<id>"}}

``version`` grows by one on every change of that source, removals included (a
removed entry stays with ``active: false``), so a number is never reused.
``digest`` names the content: source and channels, in channel order.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from backend.database.saas_models import IngestionSourceType

ROLE_AGENT: Final = "agent"
ROLE_CUSTOMER: Final = "customer"
ROLE_UNKNOWN: Final = "unknown"
MAPPABLE_ROLES: Final = frozenset({ROLE_AGENT, ROLE_CUSTOMER})
SETTINGS_KEY: Final = "channel_role_mappings"

SOURCE_TYPES: Final = frozenset(member.value for member in IngestionSourceType)
_SOURCE_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class InvalidChannelRoleMapping(ValueError):
    code = "invalid_channel_role_mapping"


class UnreadableChannelRoleMappings(InvalidChannelRoleMapping):
    """The workspace settings, or their whole mapping value, are not a JSON object.

    Attribution treats it as an unreadable mapping; a write refuses it instead
    of replacing it, which would drop every other source's mapping and restart
    the version numbers.
    """

    code = "channel_role_mappings_unreadable"


@dataclass(frozen=True, slots=True)
class ChannelRoleMapping:
    source_type: str
    source_provider: str
    version: int
    digest: str
    channels: tuple[tuple[int, str], ...]  # (channel, role), sorted by channel
    updated_at: datetime
    updated_by_user_id: str | None

    def role_of(self, channel: int) -> str | None:
        return next((role for index, role in self.channels if index == channel), None)


def source_key(source_type: str, source_provider: str) -> str:
    return f"{source_type}:{source_provider}"


def validate_source(source_type: Any, source_provider: Any) -> tuple[str, str]:
    if not isinstance(source_type, str) or source_type not in SOURCE_TYPES:
        raise InvalidChannelRoleMapping("Unknown ingestion source type")
    if not isinstance(source_provider, str) or not _SOURCE_PROVIDER_RE.fullmatch(source_provider):
        raise InvalidChannelRoleMapping("Invalid ingestion source provider")
    return source_type, source_provider


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_mapping_channels(
    entries: Sequence[tuple[Any, Any]], *, max_channels: int
) -> tuple[tuple[int, str], ...]:
    """Channel/role pairs sorted by channel; refuses everything else.

    At least one entry; each channel an integer in ``0..max_channels-1`` and
    named once; each role ``agent`` or ``customer`` and used once.
    """
    if not entries:
        raise InvalidChannelRoleMapping("A mapping needs at least one channel")
    channels: dict[int, str] = {}
    for channel, role in entries:
        if not _is_int(channel) or not 0 <= channel < max_channels:
            raise InvalidChannelRoleMapping(
                f"Channel must be an integer from 0 to {max_channels - 1}"
            )
        if not isinstance(role, str) or role not in MAPPABLE_ROLES:
            raise InvalidChannelRoleMapping("Role must be agent or customer")
        if channel in channels:
            raise InvalidChannelRoleMapping("A channel may appear only once")
        if role in channels.values():
            raise InvalidChannelRoleMapping("A role may appear only once")
        channels[channel] = role
    return tuple(sorted(channels.items()))


def parse_mapping_body(body: Any, *, max_channels: int) -> tuple[tuple[int, str], ...]:
    """The request body ``{"channels": [{"channel": int, "role": str}, ...]}``."""
    if not isinstance(body, dict) or set(body) != {"channels"}:
        raise InvalidChannelRoleMapping('The body must be {"channels": [...]}')
    raw = body["channels"]
    if not isinstance(raw, list):
        raise InvalidChannelRoleMapping("channels must be a list")
    entries: list[tuple[Any, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"channel", "role"}:
            raise InvalidChannelRoleMapping('Each entry must be {"channel": ..., "role": ...}')
        entries.append((entry["channel"], entry["role"]))
    return validate_mapping_channels(entries, max_channels=max_channels)


def mapping_digest(
    source_type: str, source_provider: str, channels: Sequence[tuple[int, str]]
) -> str:
    canonical = json.dumps(
        {
            "source_type": source_type,
            "source_provider": source_provider,
            "channels": [[channel, role] for channel, role in sorted(channels)],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mapping_entry(mapping: ChannelRoleMapping, *, active: bool = True) -> dict[str, Any]:
    """The JSON stored for ``mapping`` under its source key."""
    return {
        "source_type": mapping.source_type,
        "source_provider": mapping.source_provider,
        "version": mapping.version,
        "digest": mapping.digest,
        "active": active,
        "channels": [{"channel": channel, "role": role} for channel, role in mapping.channels],
        "updated_at": mapping.updated_at.isoformat(),
        "updated_by_user_id": mapping.updated_by_user_id,
    }


def _entries(settings_json: Any) -> Mapping[str, Any]:
    if settings_json is None:
        return {}
    if not isinstance(settings_json, Mapping):
        raise UnreadableChannelRoleMappings("Workspace settings are not an object")
    stored = settings_json.get(SETTINGS_KEY)
    if stored is None:
        return {}
    if not isinstance(stored, dict):
        raise UnreadableChannelRoleMappings("Stored channel role mappings are not an object")
    return stored


def readable_entries(settings_json: Any) -> Mapping[str, Any]:
    """Every stored entry by source key; raises :class:`UnreadableChannelRoleMappings`."""
    return _entries(settings_json)


def stored_version(settings_json: Mapping[str, Any] | None, key: str) -> int:
    """The last version written for a source key (active or removed), 0 when none."""
    try:
        entry = _entries(settings_json).get(key)
    except InvalidChannelRoleMapping:
        return 0
    if isinstance(entry, dict) and _is_int(entry.get("version")) and entry["version"] > 0:
        return entry["version"]
    return 0


def _parse_entry(key: str, entry: Any) -> ChannelRoleMapping | None:
    """None for a removed entry; refuses anything that is not a whole mapping."""
    if not isinstance(entry, dict) or entry.get("active") not in (True, False):
        raise InvalidChannelRoleMapping("Stored channel role mapping is unreadable")
    if entry["active"] is False:
        return None
    try:
        source_type, source_provider = validate_source(
            entry.get("source_type"), entry.get("source_provider")
        )
        raw_channels = entry.get("channels")
        if not isinstance(raw_channels, list) or not all(
            isinstance(item, dict) and set(item) == {"channel", "role"} for item in raw_channels
        ):
            raise InvalidChannelRoleMapping("Stored channels are unreadable")
        channels = validate_mapping_channels(
            [(item["channel"], item["role"]) for item in raw_channels], max_channels=64
        )
        version, digest = entry.get("version"), entry.get("digest")
        updated_by = entry.get("updated_by_user_id")
        if (
            key != source_key(source_type, source_provider)
            or not _is_int(version)
            or version < 1
            or not isinstance(digest, str)
            or not _DIGEST_RE.fullmatch(digest)
            or digest != mapping_digest(source_type, source_provider, channels)
            or not (updated_by is None or isinstance(updated_by, str))
        ):
            raise InvalidChannelRoleMapping("Stored channel role mapping is inconsistent")
        updated_at = datetime.fromisoformat(entry.get("updated_at"))
    except (TypeError, ValueError) as exc:
        if isinstance(exc, InvalidChannelRoleMapping):
            raise
        raise InvalidChannelRoleMapping("Stored channel role mapping is unreadable") from None
    return ChannelRoleMapping(
        source_type=source_type,
        source_provider=source_provider,
        version=version,
        digest=digest,
        channels=channels,
        updated_at=updated_at,
        updated_by_user_id=updated_by,
    )


def mapping_from_settings(
    settings_json: Mapping[str, Any] | None, *, source_type: str, source_provider: str
) -> ChannelRoleMapping | None:
    """The active mapping of one source; None when absent or removed.

    Raises :class:`InvalidChannelRoleMapping` when the stored value cannot be read.
    """
    key = source_key(source_type, source_provider)
    entries = _entries(settings_json)
    if key not in entries:
        return None
    return _parse_entry(key, entries[key])


def active_mappings(settings_json: Mapping[str, Any] | None) -> list[ChannelRoleMapping]:
    """Every readable active mapping, sorted by source type then provider."""
    mappings: list[ChannelRoleMapping] = []
    try:
        entries = _entries(settings_json)
    except InvalidChannelRoleMapping:
        return []
    for key, entry in entries.items():
        try:
            mapping = _parse_entry(key, entry)
        except InvalidChannelRoleMapping:
            continue
        if mapping is not None:
            mappings.append(mapping)
    return sorted(mappings, key=lambda item: (item.source_type, item.source_provider))
