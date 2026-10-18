"""Chunked authenticated encryption of backup files (HOM-116, ADR 0035).

Every file of a backup set is encrypted on the way to disk; no plaintext copy
of the database or of an object is ever written.  The format (version 1)::

    header = MAGIC (8 bytes) || salt (32 random bytes)
    body   = chunk_0 || chunk_1 || ... || chunk_n
    chunk  = AES-256-GCM(file_key, nonce_i, plaintext_i, aad=header)

* ``file_key = HKDF-SHA256(master_key, salt, info="homayar-backup/1|<backup_id>|<name>")``:
  a fresh key per file, bound to the backup and to the file's name inside it,
  so a file copied into another set or renamed inside one does not decrypt.
* ``nonce_i = i (11 bytes, big endian) || last`` where ``last`` is 1 only on
  the final chunk (the STREAM construction): reordering, dropping or
  truncating chunks fails authentication, including a cut at a chunk boundary.
* Every chunk but the last holds exactly ``CHUNK_SIZE`` plaintext bytes; the
  last holds 0..``CHUNK_SIZE`` bytes (an empty file is one empty last chunk).

The master key comes from the operator (``key_id:base64(32 bytes)``, the
secret manager), never from the backup.  The manifest records only its id and
a one-way fingerprint.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"HYBKENC1"
SALT_SIZE = 32
HEADER_SIZE = len(MAGIC) + SALT_SIZE
CHUNK_SIZE = 1024 * 1024
TAG_SIZE = 16
ALGORITHM = "AES-256-GCM/STREAM-1MiB/HKDF-SHA256"
_COUNTER_LIMIT = 2**88


class BackupKeyError(ValueError):
    """The backup key is missing or malformed."""


class DecryptionError(RuntimeError):
    """A file does not authenticate under the key, backup id and name."""


@dataclass(frozen=True)
class BackupKey:
    key_id: str
    secret: bytes

    def __post_init__(self) -> None:
        if not self.key_id or len(self.key_id) > 40 or ":" in self.key_id:
            raise BackupKeyError("backup key id must be 1-40 characters without ':'")
        if len(self.secret) != 32:
            raise BackupKeyError("backup keys must be 32 bytes (AES-256)")

    @property
    def fingerprint(self) -> str:
        """One-way identifier of the key material (safe to record)."""
        return hmac.new(
            self.secret, b"homayar-backup-key-fingerprint/1", hashlib.sha256
        ).hexdigest()[:32]

    def derive(self, info: str, salt: bytes = b"") -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=salt or None, info=info.encode("utf-8")
        ).derive(self.secret)

    def mac(self, purpose: str, payload: bytes) -> str:
        return hmac.new(
            self.derive(f"homayar-backup/1|mac|{purpose}"), payload, hashlib.sha256
        ).hexdigest()

    def __repr__(self) -> str:  # never print the secret
        return f"BackupKey(key_id={self.key_id!r}, fingerprint={self.fingerprint!r})"


def parse_key(value: str) -> BackupKey:
    """Parse ``key_id:base64(32 bytes)`` (same shape as ``PII_MAPPING_KEYS``)."""
    key_id, _, encoded = value.strip().partition(":")
    if not key_id or not encoded:
        raise BackupKeyError("backup key must be key_id:base64key")
    try:
        secret = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BackupKeyError("backup key is not valid base64") from exc
    return BackupKey(key_id, secret)


def load_key(*, key_file: str | None = None, env: dict[str, str] | None = None) -> BackupKey:
    """The key from ``key_file`` or ``HOMAYAR_BACKUP_KEY_FILE`` / ``HOMAYAR_BACKUP_KEY``."""
    environ = os.environ if env is None else env
    path = key_file or environ.get("HOMAYAR_BACKUP_KEY_FILE", "")
    if path:
        try:
            return parse_key(Path(path).read_text(encoding="ascii"))
        except OSError as exc:
            raise BackupKeyError(f"backup key file is not readable: {type(exc).__name__}") from exc
    value = environ.get("HOMAYAR_BACKUP_KEY", "")
    if not value:
        raise BackupKeyError(
            "no backup key: set HOMAYAR_BACKUP_KEY_FILE (preferred) or HOMAYAR_BACKUP_KEY"
        )
    return parse_key(value)


def generate_key(key_id: str) -> str:
    """A new ``key_id:base64`` key line for the secret manager."""
    BackupKey(key_id, b"\0" * 32)  # validates the id
    return f"{key_id}:{base64.b64encode(secrets.token_bytes(32)).decode('ascii')}"


def _file_key(key: BackupKey, salt: bytes, backup_id: str, name: str) -> AESGCM:
    return AESGCM(key.derive(f"homayar-backup/1|{backup_id}|{name}", salt=salt))


def _nonce(counter: int, last: bool) -> bytes:
    if counter >= _COUNTER_LIMIT:
        raise OverflowError("file too large for one encrypted stream")
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


class EncryptingWriter:
    """Write ``plaintext`` as an encrypted file; tracks sizes and digests.

    The file is created exclusively (mode 0600) and fsynced on ``close``.
    ``abort`` removes it.  Use as a context manager: an exception aborts.
    """

    def __init__(self, path: Path, key: BackupKey, *, backup_id: str, name: str) -> None:
        self.path = Path(path)
        self.name = name
        salt = secrets.token_bytes(SALT_SIZE)
        self._header = MAGIC + salt
        self._aead = _file_key(key, salt, backup_id, name)
        self._buffer = bytearray()
        self._counter = 0
        self._cipher_hash = hashlib.sha256()
        self._plain_hash = hashlib.sha256()
        self.size = 0
        self.plaintext_size = 0
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._emit_raw(self._header)

    def _emit_raw(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        self._cipher_hash.update(data)
        self.size += len(data)

    def _emit_chunk(self, plaintext: bytes, *, last: bool) -> None:
        nonce = _nonce(self._counter, last)
        self._counter += 1
        self._emit_raw(self._aead.encrypt(nonce, plaintext, self._header))

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("write to a closed EncryptingWriter")
        data = bytes(data)
        self._plain_hash.update(data)
        self.plaintext_size += len(data)
        self._buffer.extend(data)
        # Strictly greater: a final full chunk is held back so it can carry
        # the "last" flag on close.
        while len(self._buffer) > CHUNK_SIZE:
            self._emit_chunk(bytes(self._buffer[:CHUNK_SIZE]), last=False)
            del self._buffer[:CHUNK_SIZE]
        return len(data)

    def close(self) -> None:
        if self._closed:
            return
        self._emit_chunk(bytes(self._buffer), last=True)
        self._buffer.clear()
        os.fsync(self._fd)
        os.close(self._fd)
        self._closed = True

    def abort(self) -> None:
        if not self._closed:
            os.close(self._fd)
            self._closed = True
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    @property
    def sha256(self) -> str:
        return self._cipher_hash.hexdigest()

    @property
    def plaintext_sha256(self) -> str:
        return self._plain_hash.hexdigest()

    def __enter__(self) -> EncryptingWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def decrypt_chunks(path: Path, key: BackupKey, *, backup_id: str, name: str) -> Iterator[bytes]:
    """Yield the authenticated plaintext of ``path`` chunk by chunk.

    Raises ``DecryptionError`` on any tampering, truncation, wrong key, wrong
    backup id or wrong name; a consumer must treat everything it received
    before the error as untrusted.
    """
    with open(path, "rb") as handle:
        header = handle.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE or not header.startswith(MAGIC):
            raise DecryptionError(f"{name}: not an encrypted backup file")
        aead = _file_key(key, header[len(MAGIC) :], backup_id, name)
        block = CHUNK_SIZE + TAG_SIZE
        counter = 0
        current = handle.read(block)
        while True:
            following = handle.read(block)
            last = not following
            if len(current) < TAG_SIZE:
                raise DecryptionError(f"{name}: truncated")
            try:
                plaintext = aead.decrypt(_nonce(counter, last), current, header)
            except InvalidTag as exc:
                raise DecryptionError(f"{name}: authentication failed") from exc
            counter += 1
            yield plaintext
            if last:
                return
            current = following


def decrypt_to_bytes(path: Path, key: BackupKey, *, backup_id: str, name: str) -> bytes:
    return b"".join(decrypt_chunks(path, key, backup_id=backup_id, name=name))


def file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size
