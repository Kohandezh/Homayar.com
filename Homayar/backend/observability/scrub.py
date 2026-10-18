"""Credential and personal-data scrubbing for diagnostics (WP20-T01, HOM-113).

Log messages are built from ``%``-format arguments the call site chose, and
exception texts come from libraries, so a credential or a customer's e-mail can
reach a log line without anyone meaning to log it (a URL with a password, an
echoed header, a dict repr, a database error that quotes a row).  Two layers:

* ``scrub_text`` replaces the *value* of every credential-shaped token it
  recognises and keeps the surrounding text, so the line stays useful;
* ``scrub_personal_data`` replaces phone numbers, e-mails, national ids, bank
  cards, IBANs and postal codes with class placeholders (the text PII detector).

``diagnostic_text`` applies both to a bounded prefix of the input (the regexes
are not linear on adversarial input; a log line has no business being longer).
This is defence in depth, not the policy: code must still never log request
headers, bodies, transcripts or settings, and database errors are logged by
class and SQLSTATE only (``backend.services.logging_service``).
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"
MAX_DIAGNOSTIC_LENGTH = 2000
TRUNCATED = " …[truncated]"

# The project's own secret settings (backend.config.SECRET_FIELDS) plus generic names.
_SECRET_SETTING_NAMES = (
    "database_url",
    "redis_url",
    "jwt_secret",
    "token_pepper",
    "google_client_secret",
    "asanak_api_key",
    "storage_access_key",
    "storage_secret_key",
    "pii_mapping_keys",
    "llm_fallback_api_key",
    "zai_api_key",
    "gapgpt_api_key",
    "smtp_password",
    "neo4j_password",
    "sentry_dsn",
    "metrics_bearer_token",
    "audit_sink_hmac_key",
    "audit_export_database_url",
)
_SECRET_KEY_NAME = (
    r"(?:[a-z0-9_]*(?:password|passwd|passphrase|secret|token|pepper|credential|api_?key"
    r"|access_key|private_key|mapping_keys?|dsn)s?|" + "|".join(_SECRET_SETTING_NAMES) + r")"
)

# ``Authorization: Bearer abc``, ``cookie=...``, ``X-API-Key: ...`` – the header
# name stays, everything up to the end of the value goes.
_HEADER_VALUE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-csrf-token)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\r\n,;]+)"
)
# ``password=...``, ``"token": "..."``, ``{'api_key': '...'}``, ``STORAGE_SECRET_KEY=...``
_KEY_VALUE = re.compile(
    r"(?i)(?<![a-z0-9_])(['\"]?" + _SECRET_KEY_NAME + r"['\"]?)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,&;)}\]]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,512}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,2048}\.[A-Za-z0-9_-]{4,2048}\.[A-Za-z0-9_-]{4,2048}")
# scheme://user:password@host – keep the user and host; the password may hold
# '/' and '@' (everything up to the last '@' before the host).
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,20}://)([^:/@\s]*):(\S+)@")
# scheme://key@host (a DSN whose user part is the secret, e.g. Sentry).
_URL_USER = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,20}://)([^:/@\s]+)@")
# Provider-style keys and Homayar API keys (``hk_<prefix>.<secret>``).
_API_KEY = re.compile(r"\b(?:sk[-_][A-Za-z0-9_-]{8,256}|hk_[0-9a-f]{8}\.[A-Za-z0-9_-]{16,256})")


def _bounded(text: str) -> str:
    if len(text) <= MAX_DIAGNOSTIC_LENGTH:
        return text
    return text[:MAX_DIAGNOSTIC_LENGTH] + TRUNCATED


def scrub_text(text: str) -> str:
    """``text`` with credential-shaped values replaced by ``[redacted]``."""
    if not text:
        return text
    text = _bounded(text)
    if "://" in text:
        text = _URL_PASSWORD.sub(rf"\1\2:{REDACTED}@", text)
        text = _URL_USER.sub(rf"\1{REDACTED}@", text)
    text = _HEADER_VALUE.sub(rf"\1\2{REDACTED}", text)
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    if "eyJ" in text:
        text = _JWT.sub(REDACTED, text)
    text = _API_KEY.sub(REDACTED, text)
    return _KEY_VALUE.sub(rf"\1\2{REDACTED}", text)


def scrub_personal_data(text: str) -> str:
    """Phone numbers, e-mails, national ids, cards, IBANs and postal codes masked."""
    if not text:
        return text
    from backend.privacy.text_pii import redact

    masked, _ = redact(_bounded(text))
    return masked


def diagnostic_text(text: str) -> str:
    """What may be written to a log: bounded, credentials and personal data masked."""
    return scrub_personal_data(scrub_text(text))


def looks_like_secret(value: str) -> bool:
    """Whether a whole value is credential-shaped (used for audit detail values)."""
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.casefold().startswith(("bearer ", "basic ")):
        return True
    return bool(_JWT.fullmatch(stripped) or _API_KEY.fullmatch(stripped))
