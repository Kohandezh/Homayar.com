"""Verify Homayar webhook requests on the receiver side.

This module uses only the standard library, so a receiver can also copy it on
its own. Every request carries three headers:

- ``Homayar-Webhook-Id``: the event id. It is the same on every retry, so use
  it to drop duplicates.
- ``Homayar-Webhook-Timestamp``: unix seconds when this attempt was signed.
- ``Homayar-Webhook-Signature``: ``v1=<key_id>:<hex>`` entries joined by
  ``,``, one per signing key that is still valid.

``hex`` is ``HMAC-SHA256(secret, "<id>.<timestamp>." + raw body)``. Verify the
raw body bytes before you parse them; JSON that is parsed and written again is
not the same bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

ID_HEADER = "Homayar-Webhook-Id"
TIMESTAMP_HEADER = "Homayar-Webhook-Timestamp"
SIGNATURE_HEADER = "Homayar-Webhook-Signature"
DEFAULT_TOLERANCE_SECONDS = 300

_V1_ENTRY = re.compile(r"v1=([A-Za-z0-9_.-]{1,64}):([0-9a-f]{64})")
# More digits than this can never be within any tolerance of a real clock. The
# check also keeps int() away from very long strings.
_MAX_TIMESTAMP_DIGITS = 15


class WebhookVerificationError(Exception):
    """The request did not pass verification. Answer it with a 4xx and do nothing.

    ``code`` is one of ``missing_header``, ``malformed_header``,
    ``timestamp_out_of_tolerance``, ``unknown_key``, ``invalid_signature``,
    ``invalid_payload``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def verify_webhook(
    *,
    headers: Any,
    body: bytes,
    secrets: Mapping[str, str],
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Check one webhook request and return its event as a dict.

    ``headers`` is any mapping with ``items()``; names match in any letter case.
    ``body`` is the raw request body. ``secrets`` maps key id to secret; keep an
    old secret here until its key expires after a rotation. Raises
    ``WebhookVerificationError`` with the code of the first failing check.
    """
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise TypeError("body must be the raw request bytes, not decoded text")
    body = bytes(body)
    event_id = _header(headers, ID_HEADER)
    timestamp = _header(headers, TIMESTAMP_HEADER)
    signature = _header(headers, SIGNATURE_HEADER)
    if not (event_id and timestamp and signature):
        raise WebhookVerificationError("missing_header", "a Homayar-Webhook header is missing")

    if not (timestamp.isascii() and timestamp.isdigit()):
        raise WebhookVerificationError("malformed_header", "the timestamp is not unix seconds")
    entries = [
        (match.group(1), match.group(2))
        for match in (_V1_ENTRY.fullmatch(part.strip()) for part in signature.split(","))
        if match is not None
    ]
    if not entries:
        raise WebhookVerificationError("malformed_header", "the signature has no v1 entry")

    current = time.time() if now is None else now
    digits = timestamp.lstrip("0") or "0"
    if len(digits) > _MAX_TIMESTAMP_DIGITS or abs(current - int(digits)) > tolerance_seconds:
        raise WebhookVerificationError(
            "timestamp_out_of_tolerance", "the timestamp is too far from the current time"
        )

    known = [(key_id, digest) for key_id, digest in entries if key_id in secrets]
    if not known:
        raise WebhookVerificationError("unknown_key", "no signature uses a known key id")
    content = f"{event_id}.{timestamp}.".encode() + body
    expected: dict[str, str] = {}
    verified = False
    for key_id, digest in known:
        if key_id not in expected:
            secret = secrets[key_id]
            key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
            expected[key_id] = hmac.new(key, content, hashlib.sha256).hexdigest()
        # Check every entry, not only until the first match, so the time taken
        # does not depend on which entry is right.
        verified |= hmac.compare_digest(expected[key_id], digest)
    if not verified:
        raise WebhookVerificationError("invalid_signature", "the signature does not match")

    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        event = None
    if not isinstance(event, dict) or event.get("id") != event_id:
        raise WebhookVerificationError(
            "invalid_payload", "the body is not an event with this webhook id"
        )
    return event


def _header(headers: Any, name: str) -> str:
    """The header's value; several fields of one name are joined by ``,`` (RFC 9110)."""
    wanted = name.lower()
    values = [str(value).strip() for key, value in headers.items() if str(key).lower() == wanted]
    return ",".join(value for value in values if value)


class WebhookDeduplicator:
    """Remembers event ids in memory, for one process.

    An example only. A real receiver stores handled ids in its database under a
    unique key, in the same transaction as the work the event causes, and keeps
    them for at least 7 days.
    """

    def __init__(self, ttl_seconds: int = 7 * 24 * 3600) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def seen_before(self, event_id: str, *, now: float | None = None) -> bool:
        """``False`` the first time (the id is then recorded), ``True`` within the TTL."""
        current = time.time() if now is None else now
        with self._lock:
            first = self._seen.get(event_id)
            if first is not None and current - first <= self.ttl_seconds:
                return True
            self._seen[event_id] = current
            self._seen.move_to_end(event_id)
            while self._seen:
                oldest_id, oldest = next(iter(self._seen.items()))
                if current - oldest <= self.ttl_seconds:
                    break
                del self._seen[oldest_id]
            return False

    def __len__(self) -> int:
        return len(self._seen)
