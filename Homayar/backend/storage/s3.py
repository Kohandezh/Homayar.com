"""S3-compatible private object storage with bounded streaming uploads."""

from __future__ import annotations

import base64
import hashlib
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, BinaryIO, Dict, Optional
from urllib.parse import quote

from backend.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_PRESIGN_TTL_SECONDS,
    ChecksumMismatchError,
    InvalidObjectKeyError,
    InvalidPresignTTL,
    KeyFactory,
    ObjectConflictError,
    ObjectListing,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectTooLargeError,
    PresignedAccess,
    ReadableStream,
    StorageBackend,
    StorageConfigurationError,
    StorageError,
    StoredObject,
    effective_size_limit,
    iter_stream,
    normalize_expected_checksum,
    object_key_for,
    sanitize_original_filename,
    tenant_prefix,
    validate_chunk_size,
    validate_content_type,
    validate_owned_object_key,
)


class S3ObjectStorage(ObjectStorage):
    """S3 storage using an injected client or a lazily constructed boto3 client.

    The target bucket must have public access disabled. Objects are uploaded without
    a public ACL and are exposed only through short-lived signed GET requests.

    ``purge_incomplete`` is a no-op: objects are uploaded in a single
    ``put_object`` request (no multipart uploads), so there are no
    incomplete uploads to abort.

    Error classification (WP05-T05 contract): only a 404 that is not a
    bucket-level code means *object missing* (``ObjectNotFoundError`` /
    ``exists() is False``).  Access denial, transport failures and other
    provider errors surface as ``StorageError`` with the operation's stable
    code (``s3_read_failed``, ``s3_head_failed``, ``s3_delete_failed``,
    ``s3_list_failed``, ``s3_upload_failed``); a missing or misconfigured
    bucket surfaces as ``s3_bucket_unavailable``.
    """

    supports_presigned_access = True

    def __init__(
        self,
        bucket: str,
        *,
        max_size_bytes: int,
        client: Optional[Any] = None,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        presign_ttl_seconds: int = 300,
        max_presign_ttl_seconds: int = DEFAULT_MAX_PRESIGN_TTL_SECONDS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        spool_memory_bytes: int = 8 * 1024 * 1024,
        native_checksum: bool = False,
        server_side_encryption: Optional[str] = None,
        kms_key_id: Optional[str] = None,
        force_path_style: bool = False,
        key_factory: Optional[KeyFactory] = None,
    ) -> None:
        if not isinstance(bucket, str) or not bucket.strip() or "\r" in bucket or "\n" in bucket:
            raise StorageConfigurationError("S3 bucket name is invalid")
        if (access_key_id is None) != (secret_access_key is None):
            raise StorageConfigurationError(
                "S3 access key and secret key must be provided together"
            )
        if max_presign_ttl_seconds <= 0 or max_presign_ttl_seconds > 3600:
            raise StorageConfigurationError("max_presign_ttl_seconds must be between 1 and 3600")
        if presign_ttl_seconds <= 0 or presign_ttl_seconds > max_presign_ttl_seconds:
            raise StorageConfigurationError("presign_ttl_seconds exceeds the configured maximum")
        if spool_memory_bytes <= 0:
            raise StorageConfigurationError("spool_memory_bytes must be positive")
        if kms_key_id and server_side_encryption != "aws:kms":
            raise StorageConfigurationError("kms_key_id requires aws:kms server-side encryption")

        self._bucket = bucket.strip()
        self._max_size_bytes = effective_size_limit(max_size_bytes, None)
        self._presign_ttl_seconds = presign_ttl_seconds
        self._max_presign_ttl_seconds = max_presign_ttl_seconds
        self._chunk_size = validate_chunk_size(chunk_size)
        self._spool_memory_bytes = spool_memory_bytes
        self._native_checksum = native_checksum
        self._server_side_encryption = server_side_encryption
        self._kms_key_id = kms_key_id
        self._key_factory = key_factory
        self._client = (
            client
            if client is not None
            else self._create_client(
                endpoint_url=endpoint_url,
                region_name=region_name,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                force_path_style=force_path_style,
            )
        )

    @staticmethod
    def _create_client(
        *,
        endpoint_url: Optional[str],
        region_name: Optional[str],
        access_key_id: Optional[str],
        secret_access_key: Optional[str],
        force_path_style: bool,
    ) -> Any:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageConfigurationError(
                "boto3 is required when an S3 client is not injected"
            ) from exc

        kwargs: Dict[str, Any] = {
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if force_path_style else "auto"},
            )
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if region_name:
            kwargs["region_name"] = region_name
        if access_key_id is not None:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
        return boto3.client("s3", **kwargs)

    # Error codes that share HTTP 404 with a missing *object* but mean the
    # bucket or its configuration is wrong.  They must never be reported as
    # ``ObjectNotFoundError``: a caller would otherwise flag live rows as
    # MISSING or answer 404 while the store itself is unreachable.
    _BUCKET_LEVEL_CODES = frozenset({"NoSuchBucket", "PermanentRedirect", "NoSuchBucketPolicy"})

    @classmethod
    def _is_not_found(cls, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        error = response.get("Error", {})
        metadata = response.get("ResponseMetadata", {})
        code = str(error.get("Code", ""))
        status = metadata.get("HTTPStatusCode")
        if code in cls._BUCKET_LEVEL_CODES:
            return False
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    @classmethod
    def _is_bucket_unavailable(cls, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        return str(response.get("Error", {}).get("Code", "")) in cls._BUCKET_LEVEL_CODES

    @classmethod
    def _wrap(cls, exc: Exception, message: str, code: str) -> StorageError:
        """Map a client failure to a stable code; bucket-level faults get their own."""
        if cls._is_bucket_unavailable(exc):
            return StorageError(
                "S3 bucket is unavailable or misconfigured", code="s3_bucket_unavailable"
            )
        return StorageError(message, code=code)

    @staticmethod
    def _is_precondition_failed(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"412", "PreconditionFailed"} or status == 412

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
        key = object_key_for(tenant_id, object_token, self._key_factory)
        content_type = validate_content_type(content_type)
        original_filename = sanitize_original_filename(original_filename)
        expected_sha256 = normalize_expected_checksum(expected_sha256)
        size_limit = effective_size_limit(self._max_size_bytes, max_bytes)

        with tempfile.SpooledTemporaryFile(max_size=self._spool_memory_bytes, mode="w+b") as spool:
            digest = hashlib.sha256()
            size_bytes = 0
            for chunk in iter_stream(stream, self._chunk_size):
                size_bytes += len(chunk)
                if size_bytes > size_limit:
                    raise ObjectTooLargeError(size_limit, size_bytes)
                spool.write(chunk)
                digest.update(chunk)

            actual_sha256 = digest.hexdigest()
            if expected_sha256 is not None and expected_sha256 != actual_sha256:
                raise ChecksumMismatchError(expected_sha256, actual_sha256)

            metadata = {"sha256": actual_sha256, "tenant-id": tenant_id}
            if original_filename is not None:
                metadata["original-filename"] = quote(original_filename, safe="._-")

            request: Dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": key,
                "Body": spool,
                "ContentLength": size_bytes,
                "ContentType": content_type,
                "Metadata": metadata,
            }
            if self._native_checksum:
                request["ChecksumSHA256"] = base64.b64encode(digest.digest()).decode("ascii")
            if self._server_side_encryption:
                request["ServerSideEncryption"] = self._server_side_encryption
            if self._kms_key_id:
                request["SSEKMSKeyId"] = self._kms_key_id
            if object_token is not None:
                # Conditional create: a caller-chosen identity is never
                # overwritten; an existing object is compared by checksum.
                request["IfNoneMatch"] = "*"

            spool.seek(0)
            try:
                self._client.put_object(**request)
            except Exception as exc:
                if object_token is not None and self._is_precondition_failed(exc):
                    return self._existing_identical(
                        tenant_id, key, actual_sha256, size_bytes, content_type
                    )
                if object_token is not None:
                    # Never delete a caller-chosen key on an ambiguous failure:
                    # it may be another attempt's completed object.
                    raise self._wrap(exc, "S3 object upload failed", "s3_upload_failed") from exc
                # A timed-out PUT may have succeeded remotely. Remove only this new,
                # generated tenant key so a caller never receives an ambiguous result.
                cleanup_failed = False
                try:
                    self._client.delete_object(Bucket=self._bucket, Key=key)
                except Exception:
                    cleanup_failed = True
                if cleanup_failed:
                    raise StorageError(
                        "S3 object upload failed and cleanup could not be confirmed",
                        code="s3_upload_cleanup_failed",
                    ) from exc
                raise self._wrap(exc, "S3 object upload failed", "s3_upload_failed") from exc

        return StoredObject(
            backend=StorageBackend.S3,
            tenant_id=tenant_id,
            key=key,
            size_bytes=size_bytes,
            sha256=actual_sha256,
            content_type=content_type,
            original_filename=original_filename,
        )

    def _existing_identical(
        self, tenant_id: str, key: str, sha256: str, size_bytes: int, content_type: str
    ) -> StoredObject:
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise self._wrap(exc, "S3 object metadata lookup failed", "s3_head_failed") from exc
        metadata = head.get("Metadata", {}) if isinstance(head, dict) else {}
        existing_sha256 = str(metadata.get("sha256", "")).lower()
        existing_size = head.get("ContentLength") if isinstance(head, dict) else None
        if existing_sha256 != sha256 or (existing_size is not None and existing_size != size_bytes):
            raise ObjectConflictError(key)
        return StoredObject(
            backend=StorageBackend.S3,
            tenant_id=tenant_id,
            key=key,
            size_bytes=size_bytes,
            sha256=sha256,
            content_type=content_type,
        )

    def open_reader(self, tenant_id: str, key: str) -> BinaryIO:
        validated = validate_owned_object_key(tenant_id, key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=validated)
        except Exception as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError() from exc
            raise self._wrap(exc, "S3 object read failed", "s3_read_failed") from exc
        body = response.get("Body") if isinstance(response, dict) else None
        if body is None or not callable(getattr(body, "read", None)):
            raise StorageError("S3 returned an invalid object body", code="invalid_s3_response")
        return body

    def exists(self, tenant_id: str, key: str) -> bool:
        validated = validate_owned_object_key(tenant_id, key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=validated)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise self._wrap(exc, "S3 object metadata lookup failed", "s3_head_failed") from exc

    def delete(self, tenant_id: str, key: str) -> bool:
        validated = validate_owned_object_key(tenant_id, key)
        if not self.exists(tenant_id, validated):
            return False
        try:
            self._client.delete_object(Bucket=self._bucket, Key=validated)
        except Exception as exc:
            raise self._wrap(exc, "S3 object deletion failed", "s3_delete_failed") from exc
        return True

    def iter_objects(self, tenant_id: str) -> Iterator[ObjectListing]:
        """Yield every owned object under the tenant's S3 prefix.

        Uses paginated ``list_objects_v2`` calls.  Keys that do not pass
        ``validate_owned_object_key`` are skipped.  Any client exception
        raises ``StorageError`` with code ``s3_list_failed`` (or
        ``s3_bucket_unavailable`` for a missing/misconfigured bucket).
        """
        prefix = tenant_prefix(tenant_id)
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Prefix": prefix,
        }
        while True:
            try:
                response = self._client.list_objects_v2(**kwargs)
            except Exception as exc:
                raise self._wrap(exc, "S3 object listing failed", "s3_list_failed") from exc
            for obj in response.get("Contents", []):
                key = obj["Key"]
                try:
                    validate_owned_object_key(tenant_id, key)
                except InvalidObjectKeyError:
                    continue
                last_modified = obj.get("LastModified")
                if last_modified is not None and last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=UTC)
                yield ObjectListing(
                    key=key,
                    size_bytes=obj.get("Size", 0),
                    last_modified=last_modified,
                    complete=True,
                )
            if not response.get("IsTruncated"):
                break
            kwargs["ContinuationToken"] = response["NextContinuationToken"]

    def presign_get(
        self,
        tenant_id: str,
        key: str,
        *,
        expires_in_seconds: Optional[int] = None,
    ) -> PresignedAccess:
        validated = validate_owned_object_key(tenant_id, key)
        ttl = self._presign_ttl_seconds if expires_in_seconds is None else expires_in_seconds
        if ttl <= 0 or ttl > self._max_presign_ttl_seconds:
            raise InvalidPresignTTL(self._max_presign_ttl_seconds)
        if not self.exists(tenant_id, validated):
            raise ObjectNotFoundError()
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": validated},
                ExpiresIn=ttl,
                HttpMethod="GET",
            )
        except Exception as exc:
            raise StorageError(
                "S3 presigned URL generation failed", code="s3_presign_failed"
            ) from exc
        return PresignedAccess(
            url=url,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        )


S3Storage = S3ObjectStorage
