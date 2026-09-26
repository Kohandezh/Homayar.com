"""Private local-filesystem object storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Optional

from backend.storage.base import (
    DEFAULT_CHUNK_SIZE,
    ChecksumMismatchError,
    InvalidObjectKeyError,
    KeyFactory,
    ObjectConflictError,
    ObjectIncompleteError,
    ObjectListing,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectTooLargeError,
    PresignedAccess,
    PresignedAccessNotSupportedError,
    ReadableStream,
    StorageBackend,
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


class LocalObjectStorage(ObjectStorage):
    """Stores private objects below a tenant-scoped root with atomic publication."""

    supports_presigned_access = False

    def __init__(
        self,
        root: Path,
        *,
        max_size_bytes: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        key_factory: Optional[KeyFactory] = None,
    ) -> None:
        self._root = Path(root).expanduser()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root = self._root.resolve()
        if not self._root.is_dir():
            raise StorageError("Local storage root is not a directory", code="invalid_storage_root")
        os.chmod(self._root, 0o700)

        self._max_size_bytes = effective_size_limit(max_size_bytes, None)
        self._chunk_size = validate_chunk_size(chunk_size)
        self._key_factory = key_factory

    @property
    def root(self) -> Path:
        return self._root

    def _path_for_key(self, tenant_id: str, key: str) -> Path:
        validated = validate_owned_object_key(tenant_id, key)
        path = self._root.joinpath(*PurePosixPath(validated).parts)
        try:
            path.parent.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise InvalidObjectKeyError() from exc
        return path

    def _ensure_private_parent(self, parent: Path) -> None:
        current = self._root
        for part in parent.relative_to(self._root).parts:
            current = current / part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            entry = current.lstat()
            if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                raise InvalidObjectKeyError()
            os.chmod(current, 0o700)

        try:
            parent.resolve(strict=True).relative_to(self._root)
        except ValueError as exc:
            raise InvalidObjectKeyError() from exc

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.metadata.json")

    # ``open``/``lstat`` failures that mean the *key* resolves to something
    # that is not a plain private object (symlink, directory, over-long
    # name).  Every other OSError is a filesystem fault, never "missing".
    _KEY_SHAPE_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EISDIR, errno.ENAMETOOLONG})

    @classmethod
    def _safe_lstat(cls, path: Path) -> Optional[os.stat_result]:
        try:
            entry = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in cls._KEY_SHAPE_ERRNOS:
                raise InvalidObjectKeyError() from exc
            raise StorageError("Local object lookup failed", code="local_stat_failed") from exc
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise InvalidObjectKeyError()
        return entry

    @staticmethod
    def _publish_without_overwrite(temporary: Path, destination: Path) -> None:
        try:
            os.link(str(temporary), str(destination), follow_symlinks=False)
        except FileExistsError as exc:
            raise StorageError(
                "Generated object key already exists", code="object_key_collision"
            ) from exc
        try:
            temporary.unlink()
        except Exception:
            destination.unlink()
            raise

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
        destination = self._path_for_key(tenant_id, key)
        self._ensure_private_parent(destination.parent)

        content_type = validate_content_type(content_type)
        original_filename = sanitize_original_filename(original_filename)
        expected_sha256 = normalize_expected_checksum(expected_sha256)
        size_limit = effective_size_limit(self._max_size_bytes, max_bytes)

        object_temp: Optional[Path] = None
        metadata_temp: Optional[Path] = None
        published_object = False
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=destination.parent)
            object_temp = Path(temporary_name)
            os.chmod(object_temp, 0o600)

            digest = hashlib.sha256()
            size_bytes = 0
            with os.fdopen(descriptor, "wb") as output:
                for chunk in iter_stream(stream, self._chunk_size):
                    size_bytes += len(chunk)
                    if size_bytes > size_limit:
                        raise ObjectTooLargeError(size_limit, size_bytes)
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())

            actual_sha256 = digest.hexdigest()
            if expected_sha256 is not None and expected_sha256 != actual_sha256:
                raise ChecksumMismatchError(expected_sha256, actual_sha256)

            result = StoredObject(
                backend=StorageBackend.LOCAL,
                tenant_id=tenant_id,
                key=key,
                size_bytes=size_bytes,
                sha256=actual_sha256,
                content_type=content_type,
                original_filename=original_filename,
            )

            metadata_destination = self._metadata_path(destination)
            metadata_descriptor, metadata_name = tempfile.mkstemp(
                prefix=".metadata-",
                dir=destination.parent,
            )
            metadata_temp = Path(metadata_name)
            os.chmod(metadata_temp, 0o600)
            with os.fdopen(metadata_descriptor, "w", encoding="utf-8") as output:
                json.dump(
                    result.model_dump(mode="json"),
                    output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                output.flush()
                os.fsync(output.fileno())

            try:
                self._publish_without_overwrite(object_temp, destination)
            except StorageError as exc:
                if exc.code != "object_key_collision" or object_token is None:
                    raise
                # Caller-chosen identity already published: idempotent on
                # content, conflict on different content.
                return self._existing_identical(tenant_id, key, actual_sha256, size_bytes)
            object_temp = None
            published_object = True
            self._publish_without_overwrite(metadata_temp, metadata_destination)
            metadata_temp = None
            return result
        except Exception:
            if published_object:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            for temporary in (object_temp, metadata_temp):
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass

    def _existing_identical(
        self, tenant_id: str, key: str, sha256: str, size_bytes: int
    ) -> StoredObject:
        try:
            existing = self.get_metadata(tenant_id, key)
        except ObjectNotFoundError as exc:
            # The object file exists (the publish collided) but its metadata
            # does not: a write that never completed. Not ours to repair.
            raise ObjectIncompleteError(key) from exc
        if existing.sha256 != sha256 or existing.size_bytes != size_bytes:
            raise ObjectConflictError(key)
        return existing

    def open_reader(self, tenant_id: str, key: str) -> BinaryIO:
        path = self._path_for_key(tenant_id, key)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError() from exc
        except OSError as exc:
            if exc.errno in self._KEY_SHAPE_ERRNOS:
                raise InvalidObjectKeyError() from exc
            raise StorageError("Local object read failed", code="local_read_failed") from exc

        entry = os.fstat(descriptor)
        if not stat.S_ISREG(entry.st_mode):
            os.close(descriptor)
            raise InvalidObjectKeyError()
        return os.fdopen(descriptor, "rb")

    def get_metadata(self, tenant_id: str, key: str) -> StoredObject:
        path = self._path_for_key(tenant_id, key)
        metadata_path = self._metadata_path(path)
        if self._safe_lstat(path) is None or self._safe_lstat(metadata_path) is None:
            raise ObjectNotFoundError()
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(metadata_path, flags)
        except OSError as exc:
            raise InvalidObjectKeyError() from exc
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            result = StoredObject.model_validate_json(source.read())
        if result.tenant_id != tenant_id or result.key != key:
            raise InvalidObjectKeyError()
        return result

    def exists(self, tenant_id: str, key: str) -> bool:
        path = self._path_for_key(tenant_id, key)
        return self._safe_lstat(path) is not None

    def delete(self, tenant_id: str, key: str) -> bool:
        path = self._path_for_key(tenant_id, key)
        metadata_path = self._metadata_path(path)
        object_entry = self._safe_lstat(path)
        metadata_entry = self._safe_lstat(metadata_path)

        removed = False
        try:
            if object_entry is not None:
                try:
                    path.unlink()
                    removed = True
                except FileNotFoundError:
                    # Removed concurrently: the documented idempotent outcome.
                    removed = False
            if metadata_entry is not None:
                try:
                    metadata_path.unlink()
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise StorageError("Local object deletion failed", code="local_delete_failed") from exc
        return removed

    def iter_objects(self, tenant_id: str) -> Iterator[ObjectListing]:
        """Yield every owned object stored under the tenant's directory.

        Regular files only (no symlinks, no directories); sidecars
        (``.metadata.json``) and temporaries (``.upload-*``,
        ``.metadata-*``) are skipped.  Keys that do not pass
        ``validate_owned_object_key`` are skipped silently.  Returns
        nothing when the directory does not exist.
        """
        prefix = tenant_prefix(tenant_id)
        tenant_dir = self._root / "tenants" / tenant_id / "objects"
        if not tenant_dir.exists() or not tenant_dir.is_dir():
            return
        try:
            tenant_dir.resolve().relative_to(self._root)
        except ValueError:
            return
        try:
            entries = sorted(tenant_dir.iterdir())
        except OSError as exc:
            raise StorageError("Local object listing failed", code="local_list_failed") from exc
        for entry in entries:
            name = entry.name
            if name.endswith(".metadata.json"):
                continue
            if name.startswith(".upload-") or name.startswith(".metadata-"):
                continue
            try:
                st = entry.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError("Local object listing failed", code="local_list_failed") from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                continue
            key = prefix + name
            try:
                validate_owned_object_key(tenant_id, key)
            except InvalidObjectKeyError:
                continue
            try:
                sidecar_st = self._safe_lstat(self._metadata_path(entry))
            except InvalidObjectKeyError:
                sidecar_st = None
            yield ObjectListing(
                key=key,
                size_bytes=st.st_size,
                last_modified=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                complete=sidecar_st is not None,
            )

    def purge_incomplete(self, tenant_id: str, *, older_than: datetime) -> int:
        """Remove temporaries and orphaned sidecars from the tenant dir.

        Unlinks ``.upload-*`` and ``.metadata-*`` regular files whose
        mtime is ``<= older_than``, and metadata sidecars whose object
        file is gone.  Returns the number of removed items.  Raises
        ``StorageError`` (``local_purge_failed``) when the directory
        cannot be read or an entry cannot be removed.
        """
        tenant_prefix(tenant_id)  # validates the tenant id
        tenant_dir = self._root / "tenants" / tenant_id / "objects"
        if not tenant_dir.exists() or not tenant_dir.is_dir():
            return 0
        try:
            tenant_dir.resolve().relative_to(self._root)
        except ValueError:
            return 0
        count = 0
        older_than_ts = older_than.timestamp()
        try:
            entries = list(tenant_dir.iterdir())
        except OSError as exc:
            raise StorageError("Local purge failed", code="local_purge_failed") from exc
        for entry in entries:
            name = entry.name
            try:
                st = entry.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError("Local purge failed", code="local_purge_failed") from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                continue
            remove = False
            if name.startswith(".upload-") or name.startswith(".metadata-"):
                remove = st.st_mtime <= older_than_ts
            elif name.endswith(".metadata.json"):
                obj_name = name[: -len(".metadata.json")]
                remove = not (tenant_dir / obj_name).exists()
            if not remove:
                continue
            try:
                entry.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError("Local purge failed", code="local_purge_failed") from exc
            count += 1
        return count

    def presign_get(
        self,
        tenant_id: str,
        key: str,
        *,
        expires_in_seconds: Optional[int] = None,
    ) -> PresignedAccess:
        validate_owned_object_key(tenant_id, key)
        raise PresignedAccessNotSupportedError()


LocalStorage = LocalObjectStorage
