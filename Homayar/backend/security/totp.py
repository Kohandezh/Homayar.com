"""Time-based one-time passwords (RFC 4226 HOTP, RFC 6238 TOTP) for privileged MFA (HOM-129).

Pure functions on the standard library, so the second factor works on an offline
install: the authenticator app and the server only share the secret and the clock.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
_SECRET_BYTES = 20
_ALGORITHMS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}


def hotp(key: bytes, counter: int, *, digits: int = TOTP_DIGITS, algorithm: str = "sha1") -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), _ALGORITHMS[algorithm]).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def totp_at(
    key: bytes,
    unix_time: int,
    *,
    digits: int = TOTP_DIGITS,
    period: int = TOTP_PERIOD_SECONDS,
    algorithm: str = "sha1",
) -> str:
    return hotp(key, unix_time // period, digits=digits, algorithm=algorithm)


def generate_totp_secret() -> str:
    """A new shared secret: 20 random bytes as unpadded base32 (32 characters)."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def decode_totp_secret(secret_b32: str) -> bytes:
    normalized = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(normalized + "=" * (-len(normalized) % 8))


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    unix_time: int,
    last_used_step: int | None,
    window: int = 1,
) -> int | None:
    """The time step ``code`` belongs to, or ``None``.

    Steps from ``window`` before to ``window`` after the current one count (clock
    drift), and only steps after ``last_used_step``, so a code is accepted once.
    """
    candidate = code.replace(" ", "")
    if len(candidate) != TOTP_DIGITS or not candidate.isascii() or not candidate.isdigit():
        return None
    key = decode_totp_secret(secret_b32)
    now_step = unix_time // TOTP_PERIOD_SECONDS
    for step in range(now_step - window, now_step + window + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(hotp(key, step), candidate):
            return step
    return None


def otpauth_uri(secret_b32: str, *, account: str, issuer: str) -> str:
    label = f"{quote(issuer, safe='')}:{quote(account, safe='')}"
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )
