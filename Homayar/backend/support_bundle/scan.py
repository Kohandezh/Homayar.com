"""Fail-closed scanner for support-bundle directories (HOM-110).

``scan_tree`` walks a directory and reports ``(path, kind)`` findings. A
finding line never carries the matched text, only the kind and the file.
The allow-controls: image IDs and digests (long hex runs), the 40-hex
release SHA and the loopback addresses ``127.0.0.1`` / ``0.0.0.0`` are not
findings.

The scan fails closed: a directory or file that cannot be read, an env file
that is missing or unreadable, or a walk that did not cover every regular
file raises :class:`ScanError`, and the CLI exits 2 instead of reporting a
false "clean".
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

# A file larger than this is reported as audio regardless of its content.
AUDIO_MAX_BYTES = 5 * 1024 * 1024
# Text checks read at most this many bytes (bigger files are audio findings
# already, but secrets anywhere in the head still matter).
READ_CAP_BYTES = 10 * 1024 * 1024

SECRET_NAME_RE = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|DSN|_URL|CREDENTIAL|PRIVATE", re.IGNORECASE)
SECRET_MIN_LENGTH = 8
# The user part may be empty: redis://:pass@host is the real REDIS_URL form.
URL_CREDENTIALS_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]*:[^\s/@]+@")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
IPV4_RE = re.compile(
    r"(?<![0-9.])(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(?![0-9.])"
)
PERSIAN_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")
# JSON \\uXXXX escapes, so Persian hidden as escapes is still found.
UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
ALLOWED_IPS = frozenset({"127.0.0.1", "0.0.0.0"})

KINDS: tuple[str, ...] = (
    "secret_value",
    "url_credentials",
    "private_key",
    "jwt",
    "email",
    "persian_text",
    "audio",
    "ip_address",
)


class ScanError(Exception):
    """The scan could not read something; the result would be unreliable."""


@dataclass(frozen=True, order=True)
class Finding:
    path: Path
    kind: str


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines as data. Never executed, never printed.

    Raises :class:`ScanError` when the file is missing or unreadable: an env
    file the scanner cannot read must stop the scan, not empty it.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ScanError(f"cannot read the env file {path}: {exc.strerror or type(exc).__name__}") from exc
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = _strip_quotes(value.strip())
    return values


def find_secrets(env_file: Path | None) -> list[str]:
    """Values of secret-looking variables from the env file and the process env."""
    found: dict[str, None] = {}
    sources: list[dict[str, str]] = [dict(os.environ)]
    if env_file is not None:
        sources.append(parse_env_file(env_file))
    for source in sources:
        for name, value in source.items():
            if SECRET_NAME_RE.search(name) and len(value) >= SECRET_MIN_LENGTH:
                found.setdefault(value, None)
    return list(found)


def _is_audio(head: bytes, size: int) -> bool:
    if size > AUDIO_MAX_BYTES:
        return True
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return True
    if head.startswith(b"ID3"):
        return True
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:  # MP3 frame sync
        return True
    if head.startswith(b"OggS"):
        return True
    if head.startswith(b"fLaC"):
        return True
    if head[4:8] == b"ftyp":
        return True
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # EBML (matroska/webm)
        return True
    return False


def _unescape_json(text: str) -> str:
    """Decode ``\\uXXXX`` escapes (JSON) so hidden Persian is still found."""
    return UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


def scan_file(path: Path, secrets: list[str]) -> list[str]:
    """The finding kinds of one file (text findings never include the match)."""
    kinds: list[str] = []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            data = handle.read(READ_CAP_BYTES)
    except OSError as exc:
        raise ScanError(f"cannot read {path}: {exc.strerror or type(exc).__name__}") from exc
    if _is_audio(data[:16], size):
        kinds.append("audio")
    text = data.decode("utf-8", errors="ignore")
    # Cheap literal guards first: without them the regexes backtrack
    # quadratically on a large file with no match (a 5 MiB bundle member).
    for secret in secrets:
        if secret.encode("utf-8") in data:
            kinds.append("secret_value")
            break
    if "@" in text:
        if URL_CREDENTIALS_RE.search(text):
            kinds.append("url_credentials")
        if EMAIL_RE.search(text):
            kinds.append("email")
    if "-----BEGIN" in text and PRIVATE_KEY_RE.search(text):
        kinds.append("private_key")
    if "eyJ" in text and JWT_RE.search(text):
        kinds.append("jwt")
    persian_text = text
    if r"\u" in text:
        persian_text = text + "\n" + _unescape_json(text)
    if PERSIAN_RE.search(persian_text):
        kinds.append("persian_text")
    if "." in text:
        for match in IPV4_RE.finditer(text):
            if match.group(0) not in ALLOWED_IPS:
                kinds.append("ip_address")
                break
    return sorted(set(kinds))


def _walk_regular_files(root: Path) -> list[Path]:
    """Every regular file under ``root``; :class:`ScanError` when the walk
    cannot look somewhere (a permission problem must not pass silently)."""

    def on_error(exc: OSError) -> None:
        name = getattr(exc, "filename", None) or str(root)
        raise ScanError(f"cannot walk {name}: {exc.strerror or type(exc).__name__}") from exc

    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, onerror=on_error):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise ScanError(
                    f"cannot inspect {path}: {exc.strerror or type(exc).__name__}"
                ) from exc
            if stat.S_ISREG(mode):
                found.append(path)
    return found


def scan_tree(root: Path, secrets: list[str]) -> list[Finding]:
    """Every finding under ``root``, sorted by path then kind.

    Fails closed: unreadable entries raise :class:`ScanError`, and a walk
    whose scanned count differs from the regular files found also raises,
    so a silent partial scan can never report "clean".
    """
    files = _walk_regular_files(root)
    findings: list[Finding] = []
    scanned = 0
    for path in files:
        for kind in scan_file(path, secrets):
            findings.append(Finding(path=path, kind=kind))
        scanned += 1
    if scanned != len(files):  # coverage: a partial scan must never be "clean"
        raise ScanError(f"scanned {scanned} of {len(files)} regular files under {root}")
    return sorted(findings)
