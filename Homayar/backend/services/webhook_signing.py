"""Signing and secret handling for outbound webhooks (HOM-89, ADR 0050).

Pure functions, no database and no network. The delivery worker signs each
request with every usable key of the endpoint; receivers verify with
``homayar_client.verify_webhook``.

A signature is ``HMAC-SHA256(secret, "<event_id>.<t>." + raw body)`` as 64
lowercase hex chars. Binding the event id and the timestamp into the signed
bytes means neither header can be changed or replayed with a new value
without breaking the signature.

Signing secrets must stay readable (HMAC needs the secret itself), so they are
encrypted at rest with AES-256-GCM under ``WEBHOOK_SECRET_KEYS``, never hashed.
The associated data binds a ciphertext to its workspace, endpoint and key id,
so a row copied to another endpoint or workspace does not decrypt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.privacy.mapping_crypto import parse_keyring

SECRET_PREFIX = "whsec_"  # noqa: S105 - a prefix, not a credential
KEY_ID_PREFIX = "whk_"
SIGNATURE_VERSION = "v1"
USER_AGENT = "Homayar-Webhooks/1"
ID_HEADER = "Homayar-Webhook-Id"
TIMESTAMP_HEADER = "Homayar-Webhook-Timestamp"
SIGNATURE_HEADER = "Homayar-Webhook-Signature"


class WebhookSigningUnavailable(RuntimeError):
    """No keyring is configured, or a secret's keyring key is no longer configured."""


class WebhookSecretIntegrityError(RuntimeError):
    """A stored secret does not verify under its key and associated data."""


def new_signing_secret() -> str:
    """``whsec_`` + base64url of 32 random bytes, no padding (49 chars)."""
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def new_key_id() -> str:
    """``whk_`` + 24 lowercase hex chars (12 random bytes). Public, sent in every request."""
    return KEY_ID_PREFIX + secrets.token_hex(12)


def signed_content(event_id: str, timestamp: int, body: bytes) -> bytes:
    # bool is an int subclass; True must not sign as "True".
    if type(timestamp) is not int or timestamp < 0:
        raise ValueError("the webhook timestamp is a non-negative integer of unix seconds")
    return f"{event_id}.{timestamp}.".encode("ascii") + body


def signature(secret: str, *, event_id: str, timestamp: int, body: bytes) -> str:
    content = signed_content(event_id, timestamp, body)
    return hmac.new(secret.encode("utf-8"), content, hashlib.sha256).hexdigest()


def signature_header(
    keys: Sequence[tuple[str, str]], *, event_id: str, timestamp: int, body: bytes
) -> str:
    """One ``v1=<key_id>:<hex>`` entry per ``(key_id, secret)``, in the given order.

    The caller passes the active key first, then retiring keys, newest first.
    """
    if not keys:
        raise ValueError("a webhook request needs at least one signing key")
    return ",".join(
        f"{SIGNATURE_VERSION}={key_id}:"
        f"{signature(secret, event_id=event_id, timestamp=timestamp, body=body)}"
        for key_id, secret in keys
    )


def request_headers(
    keys: Sequence[tuple[str, str]], *, event_id: str, timestamp: int, body: bytes
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        ID_HEADER: event_id,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: signature_header(keys, event_id=event_id, timestamp=timestamp, body=body),
    }


def encode_payload(
    *,
    event_id: str,
    event_type: str,
    workspace_id: str,
    data: dict[str, Any],
    created_at: datetime,
) -> bytes:
    """The envelope as the exact bytes that are stored, signed and sent.

    It is serialized once, at enqueue. Every attempt sends these same bytes, so
    a receiver never sees two different bodies for one event id.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    envelope = {
        "created_at": created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": data,
        "id": event_id,
        "type": event_type,
        "workspace_id": workspace_id,
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def parse_webhook_keyring(value: str) -> dict[str, bytes]:
    """``WEBHOOK_SECRET_KEYS``: the ``PII_MAPPING_KEYS`` format, parsed by the same code.

    ``parse_keyring`` names ``PII_MAPPING_KEYS`` in its errors (its file is
    frozen), so the error is raised again under the webhook variable's name.
    """
    try:
        return parse_keyring(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("PII_MAPPING_KEYS", "WEBHOOK_SECRET_KEYS")) from exc


def _aad(workspace_id: str, endpoint_id: str, key_id: str) -> bytes:
    return f"homayar-webhook-secret/1|{workspace_id}|{endpoint_id}|{key_id}".encode()


class WebhookSecretCipher:
    def __init__(self, keyring: dict[str, bytes], active_key_id: str) -> None:
        if active_key_id not in keyring:
            raise WebhookSigningUnavailable("no active webhook secret key")
        self._keyring = keyring
        self.active_key_id = active_key_id

    @classmethod
    def from_settings(cls, settings) -> WebhookSecretCipher | None:
        if not settings.webhook_secret_keys:
            return None
        return cls(
            parse_webhook_keyring(settings.webhook_secret_keys),
            settings.webhook_secret_active_key_id,
        )

    def has_key(self, encryption_key_id: str) -> bool:
        return encryption_key_id in self._keyring

    def encrypt(
        self, secret: str, *, workspace_id: str, endpoint_id: str, key_id: str
    ) -> tuple[str, bytes, bytes]:
        """Return ``(encryption_key_id, nonce, ciphertext)`` for the signing key row."""
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keyring[self.active_key_id]).encrypt(
            nonce, secret.encode("utf-8"), _aad(workspace_id, endpoint_id, key_id)
        )
        return self.active_key_id, nonce, ciphertext

    def decrypt(
        self,
        *,
        encryption_key_id: str,
        nonce: bytes,
        ciphertext: bytes,
        workspace_id: str,
        endpoint_id: str,
        key_id: str,
    ) -> str:
        key = self._keyring.get(encryption_key_id)
        if key is None:
            raise WebhookSigningUnavailable("the webhook secret key is no longer configured")
        try:
            plain = AESGCM(key).decrypt(nonce, ciphertext, _aad(workspace_id, endpoint_id, key_id))
        except InvalidTag as exc:
            raise WebhookSecretIntegrityError("webhook signing secret does not verify") from exc
        return plain.decode("utf-8")
