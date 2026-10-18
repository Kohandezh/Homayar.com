"""Create one consistent, encrypted backup set (HOM-116, ADR 0035).

Order of work (one run, one lock):

1. take the exclusive backup lock of ``BACKUP_ROOT`` (``flock``: released by
   the kernel even when the process is killed);
2. open a ``REPEATABLE READ READ ONLY`` snapshot of PostgreSQL and read, in
   it, the catalog (tables, columns, counts, per-tenant counts, Alembic
   revision, sequences, tenants, object references, PII key ids in use) and
   every table's rows;
3. copy the objects of the snapshot's tenants from the object store;
4. record the tombstone-journal watermark (the journal itself is NOT backed
   up: it must outlive the backups, see ``docs/operations/backup-restore.md``);
5. write the encrypted catalog, then the MAC-sealed manifest, fsync, and
   rename ``.partial-<id>`` to ``<id>``.

Any failure or interruption before step 5 removes the partial directory
(when the process can still run code) or leaves it behind (``SIGKILL``,
power loss); either way no complete set exists and nothing lists, verifies
or restores it.  ``prune`` removes stale partial directories.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import sql

from backend.backup import faults
from backend.backup.crypto import ALGORITHM, CHUNK_SIZE, BackupKey, EncryptingWriter
from backend.backup.logical import (
    SKIP_TABLES,
    connect,
    database_name,
    describe_snapshot,
    list_tables,
    open_snapshot,
    server_identity,
)
from backend.backup.manifest import (
    CATALOG_NAME,
    FORMAT,
    LOCK_NAME,
    FileEntry,
    final_dir,
    new_backup_id,
    partial_dir,
    sets_dir,
    write_manifest,
)
from backend.backup.objects import backup_objects, collect_references
from backend.backup.release import code_heads, release_metadata
from backend.services.tombstone_journal import journal_for
from backend.storage import ObjectStorage

EXCLUDED_STORES = {
    "redis": (
        "ephemeral queue/cache state; jobs are recovered from PostgreSQL (outbox, recovery sweep)"
    ),
    "neo4j": "optional projection, rebuilt from PostgreSQL",
    "secrets": (
        "JWT/pepper/storage/PII/backup keys live in the secret manager; only names, "
        "key ids and keyed fingerprints are recorded"
    ),
    "tombstone_journal": "must outlive every backup; replicated separately and replayed on restore",
    "container_images": "pulled from the registry by the recorded image reference",
    "model_manifests_and_weights": (
        "release artifacts; pins are recorded as configuration references"
    ),
    "minio_iam_and_bucket_policy": "recreated by minio-bootstrap from the deployment secrets",
    "transient_files": "upload quarantine, legacy upload dir, celerybeat schedule",
}

# Non-secret settings recorded as configuration references.
_CONFIG_FIELDS = (
    "environment",
    "deployment_mode",
    "app_name",
    "database_app_role",
    "storage_backend",
    "storage_bucket",
    "storage_endpoint_url",
    "storage_region",
    "max_upload_size_mb",
    "jwt_issuer",
    "jwt_audience",
    "pii_mapping_active_key_id",
    "model_manifest_paths",
    "model_manifest_pins",
    "asr_provider",
    "diarization_provider",
    "intelligence_provider",
    "enable_google_auth",
    "enable_phone_auth",
)

REQUIRED_SECRETS = (
    "DATABASE_URL / POSTGRES_PASSWORD",
    "JWT_SECRET",
    "TOKEN_PEPPER",
    "STORAGE_ACCESS_KEY / STORAGE_SECRET_KEY",
    "MINIO_ROOT_USER / MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "PII_MAPPING_KEYS",
    "HOMAYAR_BACKUP_KEY",
)


class BackupBusy(RuntimeError):
    """Another backup holds the lock of this backup root."""


@contextmanager
def backup_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupBusy(f"another backup is running in {root}") from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(fd)  # releases the flock


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(path: Path) -> None:
    """Make every directory entry of the set durable before the rename."""
    for directory, _, _ in os.walk(path, topdown=False):
        _fsync_dir(Path(directory))


def _journal_watermark(settings: Any) -> dict[str, Any]:
    """Where the tombstone journal stood at backup time, and its fingerprint.

    The journal files are append-only: a replica of THIS deployment's journal
    that is at least as new as the backup holds every recorded file with the
    same first ``size`` bytes.  Reconcile checks that, so an empty, stale or
    foreign replica cannot pass for the real one.
    """
    journal = journal_for(settings) if settings is not None else None
    if journal is None:
        return {"configured": False}
    fingerprint = []
    for path in journal.files():
        data = path.read_bytes()
        fingerprint.append(
            {"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    records, rejected = journal.read()
    latest = max((r.tombstoned_at for r in records), default=None)
    return {
        "configured": True,
        "files": fingerprint,
        "records": len(records),
        "rejected_lines": rejected,
        "latest_tombstoned_at": latest.isoformat() if latest else None,
    }


def _explain_missing(database_url: str, keys: list[str]) -> dict[str, str]:
    """Why a required object was absent at copy time, from the LIVE rows.

    Objects are copied after the snapshot: one whose row was deleted (or is
    being deleted) by then was removed on purpose, e.g. a purge or "delete
    audio after processing".  Anything else is unexplained loss.
    """
    if not keys:
        return {}
    reasons: dict[str, str] = {}
    with connect(database_url, autocommit=True) as connection:
        tables = set(list_tables(connection))
        if "media_objects" in tables:
            for key, state in connection.execute(
                "SELECT storage_key, lifecycle_state FROM media_objects "
                "WHERE storage_key = ANY(%s)",
                (keys,),
            ):
                if str(state).upper() in {"DELETED", "DELETION_PENDING"}:
                    reasons[key] = f"media {str(state).lower()} after the snapshot"
        if "stage_checkpoints" in tables:
            for key, removed, state in connection.execute(
                "SELECT artifact_key, object_deleted_at IS NOT NULL, state FROM stage_checkpoints "
                "WHERE artifact_key = ANY(%s)",
                (keys,),
            ):
                if removed or str(state).upper() != "PUBLISHED":
                    reasons[key] = "checkpoint object removed after the snapshot"
        if "recordings" in tables:
            for (key,) in connection.execute(
                "SELECT storage_key FROM recordings WHERE storage_key = ANY(%s) "
                "AND deleted_at IS NOT NULL",
                (keys,),
            ):
                reasons.setdefault(key, "recording deleted after the snapshot")
        known = set()
        for table, column in (
            ("media_objects", "storage_key"),
            ("stage_checkpoints", "artifact_key"),
            ("recordings", "storage_key"),
        ):
            if table in tables:
                known |= {
                    row[0]
                    for row in connection.execute(
                        sql.SQL("SELECT {} FROM {} WHERE {} = ANY(%s)").format(
                            sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
                        ),
                        (keys,),
                    )
                }
        for key in keys:
            if key not in known:
                reasons.setdefault(key, "row removed after the snapshot")
    return reasons


def config_references(
    settings: Any, key: BackupKey, *, database_url: str, pii_key_ids: list[str]
) -> dict[str, Any]:
    """Configuration needed to rebuild the deployment, without secret values.

    Secrets the restored data depends on are pinned by a fingerprint keyed
    with the backup key (not reversible without that key), so a restore can
    tell the operator that, e.g., ``TOKEN_PEPPER`` is not the one the API-key
    and share-token hashes were made with.
    """
    host, port = server_identity(database_url)
    values = {name: getattr(settings, name, None) for name in _CONFIG_FIELDS} if settings else {}
    fingerprints: dict[str, str] = {}
    if settings is not None:
        pepper = str(getattr(settings, "token_pepper", "") or "")
        if pepper:
            fingerprints["TOKEN_PEPPER"] = key.mac("secret-fingerprint", pepper.encode("utf-8"))
        keyring = str(getattr(settings, "pii_mapping_keys", "") or "")
        for entry in filter(None, (part.strip() for part in keyring.split(","))):
            key_id, _, material = entry.partition(":")
            if key_id and material:
                fingerprints[f"PII_MAPPING_KEYS:{key_id}"] = key.mac(
                    "secret-fingerprint", material.encode("utf-8")
                )
    return {
        "settings": values,
        "database": {"host": host, "port": port, "name": database_name(database_url)},
        "tombstone_journal_dir": getattr(settings, "tombstone_journal_dir", "") if settings else "",
        "pii_mapping_key_ids_in_use": pii_key_ids,
        "required_secrets": list(REQUIRED_SECRETS),
        "secret_fingerprints": fingerprints,
    }


def create_backup(
    *,
    root: Path,
    key: BackupKey,
    database_url: str,
    storage: ObjectStorage | None,
    settings: Any = None,
    lock_timeout_ms: int = 60000,
    metrics_path: Path | None = None,  # deprecated: gauges move only after verify
) -> dict[str, Any]:
    """Write one complete set below ``root``; returns its manifest (+ ``path``).

    No freshness gauge moves here: only a successful deep ``verify`` (and
    ``replicate`` for the off-site copy) advances them.
    """
    root = Path(root)
    sets_dir(root).mkdir(parents=True, exist_ok=True, mode=0o700)
    with backup_lock(root):
        backup_id = new_backup_id()
        work = partial_dir(root, backup_id)
        work.mkdir(mode=0o700)
        try:
            manifest = _write_set(
                work,
                backup_id=backup_id,
                key=key,
                database_url=database_url,
                storage=storage,
                settings=settings,
                lock_timeout_ms=lock_timeout_ms,
            )
            faults.point("before_publish")
            destination = final_dir(root, backup_id)
            os.rename(work, destination)
            _fsync_dir(sets_dir(root))
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
    return {**manifest, "path": str(destination)}


def _write_set(
    work: Path,
    *,
    backup_id: str,
    key: BackupKey,
    database_url: str,
    storage: ObjectStorage | None,
    settings: Any,
    lock_timeout_ms: int,
) -> dict[str, Any]:
    started_at = _utc_now()
    started = time.monotonic()
    files: list[FileEntry] = []

    def record(name: str, writer: EncryptingWriter) -> None:
        files.append(FileEntry(name, writer.size, writer.sha256, writer.plaintext_size))

    with open_snapshot(database_url, lock_timeout_ms=lock_timeout_ms) as snapshot:
        database = describe_snapshot(snapshot)
        workspaces = (
            [row[0] for row in snapshot.query("SELECT id FROM workspaces ORDER BY id")]
            if "workspaces" in snapshot.tables
            else []
        )
        references = collect_references(snapshot)
        pii_key_ids = (
            sorted(row[0] for row in snapshot.query("SELECT DISTINCT key_id FROM pii_mappings"))
            if "pii_mappings" in snapshot.tables
            and any(c[0] == "key_id" for c in snapshot.columns["pii_mappings"])
            else []
        )
        for table in snapshot.tables:
            if table in SKIP_TABLES:
                continue
            name = f"db/{table}.copy.enc"
            with EncryptingWriter(work / name, key, backup_id=backup_id, name=name) as writer:
                for chunk in snapshot.copy_out(table):
                    writer.write(chunk)
                    faults.point("during_table_copy")
            record(name, writer)
        snapshot_meta = {
            "database_time": snapshot.database_time,
            "wal_lsn": snapshot.wal_lsn,
            "exported_snapshot": snapshot.snapshot_id,
            "server_version": snapshot.server_version,
            "isolation": "repeatable read, read only, access share locks on every table",
        }
    database_seconds = round(time.monotonic() - started, 3)
    faults.point("after_database")

    objects_started = time.monotonic()
    if storage is not None:
        object_result = backup_objects(
            storage,
            workspaces=workspaces,
            references=references,
            directory=work,
            key=key,
            backup_id=backup_id,
            on_file=lambda name, writer: (record(name, writer), faults.point("during_objects")),
        )
    else:
        object_result = {
            "inventory": [],
            "missing_at_copy": [],
            "vanished_during_copy": [],
            "checksum_mismatch_at_copy": [],
            "objects": 0,
            "bytes": 0,
        }
    explained = _explain_missing(database_url, object_result["missing_at_copy"])
    object_result["missing_explained"] = [
        {"key": key, "reason": reason} for key, reason in sorted(explained.items())
    ]
    object_result["missing_unexplained"] = sorted(
        key for key in object_result["missing_at_copy"] if key not in explained
    )
    degraded = bool(
        object_result["missing_unexplained"] or object_result["checksum_mismatch_at_copy"]
    )
    object_result["integrity"] = "degraded" if degraded else "complete"
    objects_seconds = round(time.monotonic() - objects_started, 3)

    catalog = {
        "backup_id": backup_id,
        "database": database,
        "workspaces": workspaces,
        "object_references": sorted(references.values(), key=lambda r: r["key"]),
        "objects": object_result,
        "config": config_references(
            settings, key, database_url=database_url, pii_key_ids=pii_key_ids
        ),
    }
    with EncryptingWriter(
        work / CATALOG_NAME, key, backup_id=backup_id, name=CATALOG_NAME
    ) as writer:
        payload = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for offset in range(0, len(payload), CHUNK_SIZE):
            writer.write(payload[offset : offset + CHUNK_SIZE])
    record(CATALOG_NAME, writer)

    revisions = database["alembic_revisions"]
    tables = database["tables"]
    manifest = {
        "format": FORMAT,
        "backup_id": backup_id,
        "created_at": snapshot_meta["database_time"],
        "started_at": started_at,
        "finished_at": _utc_now(),
        "durations_seconds": {
            "database": database_seconds,
            "objects": objects_seconds,
            "total": round(time.monotonic() - started, 3),
        },
        "snapshot": snapshot_meta,
        "application": release_metadata(),
        "database": {
            "alembic_revision": revisions[0] if len(revisions) == 1 else revisions,
            "code_heads": code_heads(),
            "tables": len(tables),
            "rows": sum(entry["rows"] for entry in tables.values()),
            "server_version": snapshot_meta["server_version"],
        },
        "stores": {
            "postgresql": {"included": True, "method": "logical COPY in one exported snapshot"},
            "objects": {
                "included": storage is not None,
                "backend": getattr(settings, "storage_backend", None) if settings else None,
                "bucket": getattr(settings, "storage_bucket", None) if settings else None,
                "objects": object_result["objects"],
                "bytes": object_result["bytes"],
                "integrity": object_result.get("integrity", "complete"),
                "missing_at_copy": len(object_result["missing_at_copy"]),
                "missing_unexplained": len(object_result.get("missing_unexplained", [])),
                "vanished_during_copy": len(object_result["vanished_during_copy"]),
                "checksum_mismatch_at_copy": len(object_result["checksum_mismatch_at_copy"]),
            },
        },
        "excluded": EXCLUDED_STORES,
        "tenants": len(workspaces),
        "tombstone_journal": _journal_watermark(settings),
        "encryption": {
            "state": "encrypted",
            "algorithm": ALGORITHM,
            "key_id": key.key_id,
            "key_fingerprint": key.fingerprint,
            "plaintext_written_to_disk": False,
        },
        "files": [entry.as_dict() for entry in sorted(files, key=lambda f: f.name)],
        "size_bytes": sum(entry.size for entry in files),
    }
    write_manifest(work, manifest, key)
    _fsync_tree(work)
    return manifest
