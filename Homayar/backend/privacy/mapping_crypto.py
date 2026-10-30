"""Encryption of restricted PII mappings (WP07-T05, ADR 0023).

AES-256-GCM with a 96-bit random nonce per value.  The associated data binds a
ciphertext to its tenant, redaction and placeholder, so a row copied to another
tenant or placeholder does not decrypt.  Keys come from ``PII_MAPPING_KEYS``
(secret manager), never from the database; ``key_id`` on each row selects the
key, which allows rotation without re-encrypting old rows at once.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class MappingKeyUnavailable(RuntimeError):
    """No key is configured (or the row's key is no longer configured)."""


class MappingIntegrityError(RuntimeError):
    """The ciphertext does not verify under its key and associated data."""


def parse_keyring(value: str, *, name: str = "PII_MAPPING_KEYS") -> dict[str, bytes]:
    """Parse ``key_id:base64key`` entries. ``name`` is the variable named in errors
    (other keyrings, such as ``WEBHOOK_SECRET_KEYS``, use the same format)."""
    keyring: dict[str, bytes] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        key_id, _, encoded = entry.partition(":")
        if not key_id or not encoded or len(key_id) > 40:
            raise ValueError(f"{name} entries must be key_id:base64key")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"{name} contains an invalid base64 key") from exc
        if len(key) != 32:
            raise ValueError(f"{name} keys must be 32 bytes (AES-256)")
        keyring[key_id] = key
    return keyring


def _aad(workspace_id: str, redaction_id: str, placeholder: str) -> bytes:
    return f"homayar-pii-mapping/1|{workspace_id}|{redaction_id}|{placeholder}".encode()


class MappingCipher:
    def __init__(self, keyring: dict[str, bytes], active_key_id: str) -> None:
        if active_key_id not in keyring:
            raise MappingKeyUnavailable("no active PII mapping key")
        self._keyring = keyring
        self.active_key_id = active_key_id

    @classmethod
    def from_settings(cls, settings) -> MappingCipher | None:
        if not settings.pii_mapping_keys:
            return None
        return cls(parse_keyring(settings.pii_mapping_keys), settings.pii_mapping_active_key_id)

    def encrypt(
        self, value: str, *, workspace_id: str, redaction_id: str, placeholder: str
    ) -> tuple[str, bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keyring[self.active_key_id]).encrypt(
            nonce, value.encode("utf-8"), _aad(workspace_id, redaction_id, placeholder)
        )
        return self.active_key_id, nonce, ciphertext

    def decrypt(
        self,
        *,
        key_id: str,
        nonce: bytes,
        ciphertext: bytes,
        workspace_id: str,
        redaction_id: str,
        placeholder: str,
    ) -> str:
        key = self._keyring.get(key_id)
        if key is None:
            raise MappingKeyUnavailable("the mapping key is no longer configured")
        try:
            plain = AESGCM(key).decrypt(
                nonce, ciphertext, _aad(workspace_id, redaction_id, placeholder)
            )
        except InvalidTag as exc:
            raise MappingIntegrityError("mapping does not verify") from exc
        return plain.decode("utf-8")
