"""Object-store side of a backup set (HOM-116/117).

What the database expects to exist is read inside the database snapshot
(``collect_references``); the bytes are then copied through the
application's own tenant-scoped storage interface, so the same code backs up
the bundled MinIO, any S3-compatible store and the local backend, and only
keys that pass the tenant-prefix validation are ever read or written.

Expectations per referenced key:

* ``required`` – a live row needs the bytes (active, retention-pending or
  held media; published checkpoints; live pre-ledger recordings);
* ``optional`` – the bytes may legitimately be gone (deletion pending,
  unpublished checkpoints, deleted pre-ledger recordings);
* ``gone`` – the row says the bytes were removed.

Objects are copied after the snapshot: an object deleted in between is
recorded as ``missing_at_copy`` (its tombstone is journaled and re-applied on
restore), an object created in between is copied and shows up as an orphan
of the snapshot during reconciliation.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

from backend.backup import faults
from backend.backup.crypto import BackupKey, EncryptingWriter, decrypt_chunks
from backend.backup.logical import Snapshot
from backend.storage import ObjectStorage
from backend.storage.base import ObjectNotFoundError, tenant_prefix

ARTIFACT_CONTENT_TYPE = "application/json"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
_RANK = {"gone": 0, "optional": 1, "required": 2}
_MEDIA_EXPECTATION = {
    "ACTIVE": "required",
    "RETENTION_PENDING": "required",
    "LEGAL_HOLD": "required",
    "DELETION_PENDING": "optional",
    "DELETED": "gone",
}


class ObjectRestoreRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def object_file_name(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"objects/{digest[:2]}/{digest}.enc"


def _table_exists(snapshot: Snapshot, table: str) -> bool:
    return table in snapshot.tables


def _merge(references: dict[str, dict[str, Any]], ref: dict[str, Any]) -> None:
    """Keep the strongest expectation when two rows point at one key.

    A legal hold on any row of the key sticks: held bytes are never explained
    away as an intended deletion.
    """
    current = references.get(ref["key"])
    held = bool(ref.get("held")) or bool(current and current.get("held"))
    if current is None or _RANK[ref["expectation"]] > _RANK[current["expectation"]]:
        references[ref["key"]] = ref
    if held:
        references[ref["key"]]["held"] = True


def collect_references(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    """Every object key a row of the snapshot points at, with its expectation."""
    references: dict[str, dict[str, Any]] = {}
    if _table_exists(snapshot, "media_objects"):
        for key, workspace_id, sha256, size, state, content_type in snapshot.query(
            "SELECT storage_key, workspace_id, sha256, size_bytes, lifecycle_state, content_type "
            "FROM media_objects"
        ):
            _merge(
                references,
                {
                    "key": key,
                    "workspace_id": workspace_id,
                    "sha256": sha256,
                    "size": size,
                    "expectation": _MEDIA_EXPECTATION.get(str(state).upper(), "required"),
                    "content_type": content_type,
                    "source": "media_object",
                    "held": str(state).upper() == "LEGAL_HOLD",
                },
            )
    if _table_exists(snapshot, "recordings"):
        for key, workspace_id, sha256, size, deleted, media_type in snapshot.query(
            "SELECT storage_key, workspace_id, checksum_sha256, size_bytes, "
            "deleted_at IS NOT NULL, media_type FROM recordings"
        ):
            if key in references:
                continue  # the media ledger row of the same bytes decides
            _merge(
                references,
                {
                    "key": key,
                    "workspace_id": workspace_id,
                    "sha256": sha256,
                    "size": size,
                    "expectation": "optional" if deleted else "required",
                    "content_type": media_type,
                    "source": "recording",
                },
            )
    if _table_exists(snapshot, "stage_checkpoints"):
        for key, workspace_id, sha256, size, state, removed in snapshot.query(
            "SELECT artifact_key, workspace_id, artifact_sha256, artifact_size_bytes, state, "
            "object_deleted_at IS NOT NULL FROM stage_checkpoints"
        ):
            if removed:
                expectation = "gone"
            elif str(state).upper() == "PUBLISHED":
                expectation = "required"
            else:
                expectation = "optional"
            _merge(
                references,
                {
                    "key": key,
                    "workspace_id": workspace_id,
                    "sha256": sha256,
                    "size": size,
                    "expectation": expectation,
                    "content_type": ARTIFACT_CONTENT_TYPE,
                    "source": "checkpoint",
                },
            )
    return references


def backup_objects(
    storage: ObjectStorage,
    *,
    workspaces: list[str],
    references: dict[str, dict[str, Any]],
    directory: Path,
    key: BackupKey,
    backup_id: str,
    on_file=None,
) -> dict[str, Any]:
    """Copy every object under the snapshot's tenants into encrypted files.

    Returns the inventory (one entry per copied object) and the findings of
    the copy.  A listing failure aborts the backup (``StorageError``): a set
    that silently misses a tenant's objects must never be called complete.
    """
    inventory: list[dict[str, Any]] = []
    vanished: list[str] = []
    mismatched: list[dict[str, Any]] = []
    listed: set[str] = set()
    for workspace_id in sorted(workspaces):
        for listing in storage.iter_objects(workspace_id):
            if not listing.complete:
                continue
            listed.add(listing.key)
            ref = references.get(listing.key)
            name = object_file_name(listing.key)
            writer = EncryptingWriter(directory / name, key, backup_id=backup_id, name=name)
            try:
                with closing(storage.open_reader(workspace_id, listing.key)) as reader:
                    for block in iter(lambda: reader.read(1024 * 1024), b""):
                        writer.write(block)
            except ObjectNotFoundError:
                writer.abort()
                vanished.append(listing.key)
                continue
            except BaseException:
                writer.abort()
                raise
            writer.close()
            entry = {
                "key": listing.key,
                "workspace_id": workspace_id,
                "file": name,
                "size": writer.plaintext_size,
                "sha256": writer.plaintext_sha256,
                "content_type": (ref or {}).get("content_type") or DEFAULT_CONTENT_TYPE,
                "expectation": (ref or {}).get("expectation"),
            }
            if ref and ref.get("sha256") and ref["sha256"] != writer.plaintext_sha256:
                mismatched.append({"key": listing.key, "source": ref.get("source")})
            inventory.append(entry)
            if on_file is not None:
                on_file(name, writer)
    known = set(workspaces)
    missing = sorted(
        ref["key"]
        for ref in references.values()
        if ref["expectation"] == "required"
        and ref["workspace_id"] in known
        and ref["key"] not in listed
    )
    missing += sorted(
        set(vanished) & {k for k, r in references.items() if r["expectation"] == "required"}
    )
    return {
        "inventory": inventory,
        "missing_at_copy": sorted(set(missing)),
        "vanished_during_copy": sorted(vanished),
        "checksum_mismatch_at_copy": mismatched,
        "objects": len(inventory),
        "bytes": sum(entry["size"] for entry in inventory),
    }


class _ChunkReader(io.RawIOBase):
    """A read-only file object over an iterator of byte chunks."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data = self._buffer + b"".join(self._chunks)
            self._buffer = b""
            return data
        while len(self._buffer) < size:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                break
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data


def target_has_objects(storage: ObjectStorage, workspaces: list[str]) -> list[str]:
    """Workspaces of the backup that already hold objects in the target store."""
    occupied = []
    for workspace_id in sorted(workspaces):
        for _ in storage.iter_objects(workspace_id):
            occupied.append(workspace_id)
            break
    return occupied


def restore_objects(
    storage: ObjectStorage,
    *,
    inventory: list[dict[str, Any]],
    directory: Path,
    key: BackupKey,
    backup_id: str,
) -> dict[str, Any]:
    """Write every inventoried object into the target store, checksum-verified.

    Idempotent: an object already present with the same bytes is accepted
    as is (the storage contract of ``object_token`` writes), so an
    interrupted restore can simply be run again; different bytes under the
    same key fail the restore (``ObjectConflictError``).
    """
    written = 0
    present = 0
    restored_bytes = 0
    for entry in inventory:
        workspace_id = entry["workspace_id"]
        object_key = entry["key"]
        prefix = tenant_prefix(workspace_id)
        if not object_key.startswith(prefix):
            raise ObjectRestoreRefused(
                "tenant_prefix_mismatch", f"object outside its tenant prefix in {entry['file']}"
            )
        token = object_key[len(prefix) :]
        if storage.exists(workspace_id, object_key):
            present += 1
        else:
            written += 1
        stream = _ChunkReader(
            decrypt_chunks(directory / entry["file"], key, backup_id=backup_id, name=entry["file"])
        )
        storage.put_stream(
            workspace_id,
            stream,
            content_type=entry.get("content_type") or DEFAULT_CONTENT_TYPE,
            expected_sha256=entry["sha256"],
            object_token=token,
        )
        restored_bytes += entry["size"]
        faults.point("during_object_restore")
    return {
        "objects": len(inventory),
        "written": written,
        "already_present": present,
        "bytes": restored_bytes,
    }
