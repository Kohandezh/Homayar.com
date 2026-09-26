"""Validation and tenant namespacing for client idempotency keys."""

from __future__ import annotations

import re

from backend.config import Settings
from backend.security.tokens import token_digest

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def validate_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not _IDEMPOTENCY_KEY.fullmatch(normalized):
        raise ValueError("Idempotency-Key must contain 8-128 URL-safe ASCII characters")
    return normalized


def scoped_idempotency_key(
    *,
    workspace_id: str,
    purpose: str,
    client_key: str,
    settings: Settings,
) -> str:
    validated = validate_idempotency_key(client_key)
    return token_digest(
        f"idempotency:{workspace_id}:{purpose}:{validated}",
        settings,
    )
