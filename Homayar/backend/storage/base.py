"""Object storage contracts and shared safety primitives."""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePath, PurePosixPath
from typing import BinaryIO, Callable, Iterable, Iterator, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_PRESIGN_TTL_SECONDS = 900

_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

ReadableStream = Union[BinaryIO, Iterable[bytes]]
KeyFactory = Callable[[], str]


class StorageBackend(str, Enum):
    LOCAL = "local"
    S3 = "s3"


class StorageError(Exception):
    """Base storage error with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str = "storage_error") -> None:
        super().__init__(message)
        self.code = code


class StorageConfigurationError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="storage_configuration_error")


class InvalidTenantError(StorageError):
    def __init__(self) -> None:
        super().__init__("Tenant identifier is invalid", code="invalid_tenant")


class InvalidObjectKeyError(StorageError):
    def __init__(self) -> None:
        super().__init__(
            "Object key is invalid or belongs to another tenant", code="invalid_object_key"
        )


class InvalidStreamError(StorageError):
    def __init__(self) -> None:
        super().__init__("Object stream must yield bytes", code="invalid_stream")


class ObjectNotFoundError(StorageError):
    def __init__(self) -> None:
        super().__init__("Object was not found", code="object_not_found")


class ObjectTooLargeError(StorageError):
    def __init__(self, limit_bytes: int, observed_bytes: int) -> None:
        super().__init__(
            f"Object exceeds the {limit_bytes}-byte size limit",
            code="object_too_large",
        )
        self.limit_bytes = limit_bytes
        self.observed_bytes = observed_bytes


class ChecksumMismatchError(StorageError):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__("Object checksum does not match", code="checksum_mismatch")
        self.expected = expected
        self.actual = actual


class ObjectConflictError(StorageError):
    """An object with the requested identity exists with *different* content."""

    def __init__(self, key: str) -> None:
        super().__init__(
            "An object with this identity already exists with different content",
            code="object_conflict",
        )
        self.key = key


class ObjectIncompleteError(StorageError):
    """An object with the requested identity exists but its write never completed."""

    def __init__(self, key: str) -> None:
        super().__init__(
            "An object with this identity exists but is incomplete", code="object_incomplete"
        )
        self.key = key


class PresignedAccessNotSupportedError(StorageError):
    def __init__(self) -> None:
        super().__init__(
            "This storage backend does not support presigned access",
            code="presigned_access_not_supported",
        )


class InvalidPresignTTL(StorageError):
    def __init__(self, max_ttl_seconds: int) -> None:
        super().__init__(
            f"Presigned access TTL must be between 1 and {max_ttl_seconds} seconds",
            code="invalid_presign_ttl",
        )


class StoredObject(BaseModel):
    """Metadata returned only after a complete, checksum-verified write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: StorageBackend
    tenant_id: str
    key: str
    size_bytes: int = Field(ge=0)
    sha256: str
    content_type: str
    original_filename: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if not _CHECKSUM_RE.fullmatch(normalized):
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        return normalized


class PresignedAccess(BaseModel):
    """Short-lived read access produced by a storage backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1)
    method: str = "GET"
    expires_at: datetime


class ObjectListing(BaseModel):
    """One object reported by a storage listing (frozen, never mutated)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    size_bytes: int = Field(ge=0)
    last_modified: datetime | None = None
    complete: bool = True


def validate_tenant_id(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not _TENANT_RE.fullmatch(tenant_id):
        raise InvalidTenantError()
    return tenant_id


def tenant_prefix(tenant_id: str) -> str:
    return f"tenants/{validate_tenant_id(tenant_id)}/objects/"


def generate_object_key(tenant_id: str, key_factory: Optional[KeyFactory] = None) -> str:
    token = (key_factory or (lambda: uuid.uuid4().hex))()
    if not isinstance(token, str) or not _OBJECT_TOKEN_RE.fullmatch(token):
        raise StorageConfigurationError(
            "Generated object tokens must be URL-safe and at least 16 characters"
        )
    return f"{tenant_prefix(tenant_id)}{token}"


def validate_object_token(token: str) -> str:
    """A caller-chosen object token (WP06-T03 attempt-scoped artifacts).

    Same alphabet and length as generated tokens, so the key stays a single
    path segment below the tenant prefix: no separators, no traversal, no
    collision with another tenant's namespace.
    """
    if not isinstance(token, str) or not _OBJECT_TOKEN_RE.fullmatch(token):
        raise InvalidObjectKeyError()
    return token


def object_key_for(
    tenant_id: str,
    object_token: Optional[str],
    key_factory: Optional[KeyFactory] = None,
) -> str:
    """Deterministic key for a caller-chosen token, else a fresh random key."""
    if object_token is None:
        return generate_object_key(tenant_id, key_factory)
    return f"{tenant_prefix(tenant_id)}{validate_object_token(object_token)}"


def validate_owned_object_key(tenant_id: str, key: str) -> str:
    prefix = tenant_prefix(tenant_id)
    if not isinstance(key, str) or "\\" in key or not key.startswith(prefix):
        raise InvalidObjectKeyError()

    path = PurePosixPath(key)
    token = key[len(prefix) :]
    if path.is_absolute() or len(path.parts) != 4 or not _OBJECT_TOKEN_RE.fullmatch(token):
        raise InvalidObjectKeyError()
    if any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidObjectKeyError()
    return key


def normalize_expected_checksum(expected_sha256: Optional[str]) -> Optional[str]:
    if expected_sha256 is None:
        return None
    normalized = expected_sha256.lower()
    if not _CHECKSUM_RE.fullmatch(normalized):
        raise ValueError("expected_sha256 must contain 64 hexadecimal characters")
    return normalized


def effective_size_limit(configured_limit: int, requested_limit: Optional[int]) -> int:
    if configured_limit <= 0:
        raise StorageConfigurationError("max_size_bytes must be positive")
    if requested_limit is None:
        return configured_limit
    if requested_limit <= 0:
        raise ValueError("max_bytes must be positive")
    return min(configured_limit, requested_limit)


def validate_chunk_size(chunk_size: int) -> int:
    if chunk_size <= 0:
        raise StorageConfigurationError("chunk_size must be positive")
    return chunk_size


def iter_stream(stream: ReadableStream, chunk_size: int) -> Iterator[bytes]:
    """Yield normalized byte chunks from a file-like object or iterable."""

    read = getattr(stream, "read", None)
    if callable(read):
        while True:
            chunk = read(chunk_size)
            if chunk == b"":
                return
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise InvalidStreamError()
            if chunk:
                yield bytes(chunk)
        return

    try:
        iterator = iter(stream)
    except TypeError as exc:
        raise InvalidStreamError() from exc

    for chunk in iterator:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise InvalidStreamError()
        if chunk:
            yield bytes(chunk)


def sanitize_original_filename(filename: Optional[str]) -> Optional[str]:
    if filename is None:
        return None
    if not isinstance(filename, str):
        raise ValueError("original_filename must be a string")
    leaf = PurePath(filename.replace("\\", "/")).name
    cleaned = "".join(character for character in leaf if character.isprintable()).strip()
    return cleaned[:255] or None


def validate_content_type(content_type: str) -> str:
    if not isinstance(content_type, str):
        raise ValueError("content_type must be a string")
    normalized = content_type.strip()
    if not normalized or len(normalized) > 255 or "\r" in normalized or "\n" in normalized:
        raise ValueError("content_type is invalid")
    return normalized


class ObjectStorage(ABC):
    """Tenant-scoped private object storage interface."""

    supports_presigned_access = False
    # Whether recording delivery may redirect to a presigned URL when the backend supports
    # one. S3 takes it from STORAGE_PRESIGNED_DELIVERY; false streams through the API (HOM-340).
    presigned_delivery = True

    @abstractmethod
    def put_stream(
        self,
        tenant_id: str,
        stream: ReadableStream,
        *,
        content_type: str,
        original_filename: Optional[str] = None,
        expected_sha256: Optional[str] = None,
        max_bytes: Optional[int] = None,
        object_token: Optional[str] = None,
    ) -> StoredObject:
        """Store a complete, checksum-verified object and return its metadata.

        Without ``object_token`` the key is fresh and random (uploads).  With
        one, the key is ``<tenant prefix><token>`` and the write is
        **idempotent on content**: an existing object with the same SHA-256
        is returned as the stored object (nothing rewritten); one with
        different content raises ``ObjectConflictError``; an existing object
        whose write never completed raises ``ObjectIncompleteError``.  A
        published object is never overwritten in place.
        """
        raise NotImplementedError

    def store_stream(
        self,
        tenant_id: str,
        stream: ReadableStream,
        *,
        content_type: str,
        original_filename: Optional[str] = None,
        expected_sha256: Optional[str] = None,
        max_bytes: Optional[int] = None,
        object_token: Optional[str] = None,
    ) -> StoredObject:
        return self.put_stream(
            tenant_id,
            stream,
            content_type=content_type,
            original_filename=original_filename,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
            object_token=object_token,
        )

    @abstractmethod
    def open_reader(self, tenant_id: str, key: str) -> BinaryIO:
        raise NotImplementedError

    def open_range_reader(self, tenant_id: str, key: str, *, start: int, end: int) -> BinaryIO:
        """A reader at byte ``start`` of the object; the caller reads up to ``end`` (inclusive).

        The default opens the object and seeks. A reader that cannot seek is read forward
        and the bytes are dropped, one chunk at a time, so memory stays one chunk. S3
        overrides this with a ranged ``GetObject``.
        """
        if start < 0 or end < start:
            raise ValueError("byte range is invalid")
        reader = self.open_reader(tenant_id, key)
        try:
            try:
                reader.seek(start)
            except (AttributeError, OSError):  # io.UnsupportedOperation is an OSError
                remaining = start
                while remaining > 0:
                    chunk = reader.read(min(DEFAULT_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
        except BaseException:
            reader.close()
            raise
        return reader

    @abstractmethod
    def exists(self, tenant_id: str, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, tenant_id: str, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def presign_get(
        self,
        tenant_id: str,
        key: str,
        *,
        expires_in_seconds: Optional[int] = None,
    ) -> PresignedAccess:
        raise NotImplementedError

    @abstractmethod
    def iter_objects(self, tenant_id: str) -> Iterator[ObjectListing]:
        """Yield every object stored under the tenant's own prefix.

        Only keys that pass ``validate_owned_object_key`` are yielded;
        anything else under the prefix is skipped silently.  Never lists
        another tenant's prefix.  Raises ``StorageError`` (with a stable
        code) when the backend cannot answer.
        """
        raise NotImplementedError

    def purge_incomplete(self, tenant_id: str, *, older_than: datetime) -> int:
        """Remove backend-private leftovers of writes that never completed.

        Returns the number of removed items.  The default validates the
        tenant id (a purge is never unscoped) and returns 0: backends that
        do not produce durable temporaries (S3 with single-request
        ``put_object``, no multipart uploads) need no cleanup.
        """
        validate_tenant_id(tenant_id)
        return 0
