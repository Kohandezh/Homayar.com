"""Credential scrubbing for diagnostics (WP20-T01, HOM-113).

Log messages are built from ``%``-format arguments the call site chose, and
exception texts come from libraries, so a credential can reach a log line
without anyone meaning to log it (a URL with a password, an echoed header).
``scrub_text`` replaces the *value* of every credential-shaped token it
recognises and keeps the surrounding text, so the line stays useful.

This is defence in depth, not the policy: code must still never log request
headers, bodies, transcripts or settings.  Personal data in free text is the
job of ``backend.privacy.text_pii``; this module only knows credentials.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

# ``Authorization: Bearer abc``, ``cookie=...``, ``X-API-Key: ...`` – the header
# name stays, everything up to the end of the value goes.
_HEADER_VALUE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-csrf-token)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\r\n,;]+)"
)
# ``password=...``, ``"token": "..."``, ``api_key=...`` in messages or reprs.
_KEY_VALUE = re.compile(
    r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|token|api_key|apikey|access_key|"
    r"private_key|pepper|credential)s?)(\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,&;)}\]]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")
# scheme://user:password@host – keep the user and host for the operator.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^:/@\s]*):([^@\s/]+)@")
# Provider-style keys and Homayar API keys (``hk_<prefix>.<secret>``).
_API_KEY = re.compile(r"\b(?:sk[-_][A-Za-z0-9_-]{8,}|hk_[0-9a-f]{8}\.[A-Za-z0-9_-]{16,})")


def scrub_text(text: str) -> str:
    """``text`` with credential-shaped values replaced by ``[redacted]``."""
    if not text:
        return text
    text = _URL_USERINFO.sub(rf"\1\2:{REDACTED}@", text)
    text = _HEADER_VALUE.sub(rf"\1\2{REDACTED}", text)
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _API_KEY.sub(REDACTED, text)
    return _KEY_VALUE.sub(rf"\1\2{REDACTED}", text)


def looks_like_secret(value: str) -> bool:
    """Whether a whole value is credential-shaped (used for audit detail values)."""
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.casefold().startswith(("bearer ", "basic ")):
        return True
    return bool(_JWT.fullmatch(stripped) or _API_KEY.fullmatch(stripped))
