"""Restore a backup set into an isolated staging database (HOM-117, ADR 0035).

The restore never writes into the database the application serves:

* it creates ``<staging>`` (e.g. ``homayar_restoring``) on the target server,
  refuses when that name is the live database or the backup's source
  database on the same server, and marks it with a ``COMMENT ON DATABASE``
  carrying the backup id and the state (``restoring`` -> ``loaded`` ->
  ``reconciled`` -> ``promoted``);
* the application only ever connects to the live name, so restored rows are
  unreachable for users until ``promote`` renames the staging database, and
  ``promote`` requires a PASS reconciliation report (tombstones re-applied,
  tenant boundaries and RLS verified, objects reconciled) sealed with the
  backup key;
* objects go into the target store only if it holds no objects of the
  backup's tenants (a fresh, isolated store), or on ``resume`` of the same
  restore, where identical bytes are accepted and different bytes fail.

Steps: verify the set (manifest MAC, every checksum) -> create the staging
database -> ``alembic upgrade <backup revision>`` -> load every table in one
transaction (row counts and all foreign keys checked) -> ``alembic upgrade
head`` -> re-apply the row-level-security grants and policies -> restore the
objects (checksum-verified writes) -> state ``loaded``.  Any failure leaves
the staging database marked ``restoring`` (never promotable); running the
same restore again drops and recreates it.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from backend.backup import faults
from backend.backup.crypto import BackupKey, decrypt_chunks
from backend.backup.logical import (
    DatabaseBackupError,
    connect,
    database_name,
    load_tables,
    server_identity,
    with_database,
)
from backend.backup.manifest import BackupInvalid, read_manifest
from backend.backup.objects import restore_objects, target_has_objects
from backend.backup.release import code_has_revision, code_heads, run_alembic
from backend.backup.verify import load_catalog, verify_set
from backend.database.rls import APP_ROLE, apply_row_level_security
from backend.storage import ObjectStorage

MARKER_PREFIX = "homayar-restore "
STAGING_WORD = "restoring"
STATE_FILE = "restore-state.json"
_DATABASE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class RestoreRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(UTC).isoformat()


def validate_database_name(name: str) -> str:
    if not _DATABASE_NAME.fullmatch(name or ""):
        raise RestoreRefused("invalid_database_name", f"{name!r} is not a plain identifier")
    return name


def read_marker(admin_url: str, database: str) -> dict[str, Any] | None | bool:
    """``False`` when the database does not exist, ``None`` when it exists
    without a restore marker, else the marker."""
    with connect(with_database(admin_url, "postgres"), autocommit=True) as connection:
        row = connection.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (database,),
        ).fetchone()
    if row is None:
        return False
    comment = row[0] or ""
    if not comment.startswith(MARKER_PREFIX):
        return None
    try:
        marker = json.loads(comment[len(MARKER_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return marker if isinstance(marker, dict) else None


def marker_payload(marker: dict[str, Any]) -> str:
    return MARKER_PREFIX + json.dumps(marker, sort_keys=True, separators=(",", ":"))


def marker_statement(database: str, marker: dict[str, Any]) -> str:
    payload = marker_payload(marker)
    return (
        f'COMMENT ON DATABASE "{validate_database_name(database)}" IS '
        + "'"
        + payload.replace("'", "''")
        + "'"
    )


def write_marker(admin_url: str, database: str, marker: dict[str, Any]) -> None:
    with connect(with_database(admin_url, "postgres"), autocommit=True) as connection:
        connection.execute(marker_statement(database, marker))


def cas_marker(
    admin_url: str, database: str, expected: dict[str, Any], marker: dict[str, Any]
) -> None:
    """Replace the marker only if it is still exactly ``expected``.

    Every writer holds ``staging_lock``; the comparison is the second line of
    defence (a run that lost its lock, or a marker changed by hand)."""
    with connect(with_database(admin_url, "postgres")) as connection:
        current = connection.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (database,),
        ).fetchone()
        if current is None or current[0] != marker_payload(expected):
            connection.rollback()
            raise RestoreRefused("staging_changed", f"the marker of {database} changed underneath")
        connection.execute(marker_statement(database, marker))
        connection.commit()


@contextmanager
def staging_lock(admin_url: str, staging: str) -> Iterator[None]:
    """One restore / reconcile / promote at a time per staging database.

    A session-level advisory lock on the maintenance database, held for the
    whole run and released by the server if the process dies."""
    connection = connect(with_database(admin_url, "postgres"), autocommit=True)
    try:
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (f"homayar-restore:{staging}",)
        ).fetchone()[0]
        if not acquired:
            raise RestoreRefused(
                "staging_busy", f"another restore, reconcile or promote is working on {staging}"
            )
        yield
    finally:
        connection.close()


def _isolation_guard(
    *, admin_url: str, staging: str, live_database_url: str | None, catalog: dict[str, Any]
) -> None:
    """The staging database is never the live one, whatever the host strings.

    Host names differ between an operator host and the Compose network
    (``10.0.0.5`` vs ``postgres``), so the live and source names are refused
    by name alone, and every staging name must say what it is."""
    if live_database_url and database_name(live_database_url) == staging:
        raise RestoreRefused(
            "staging_is_live", "the staging database is the one the application serves"
        )
    source = catalog["config"]["database"]
    if source["name"] == staging:
        raise RestoreRefused(
            "staging_is_source", "the staging database is the backup's own source database"
        )
    if STAGING_WORD not in staging:
        raise RestoreRefused(
            "staging_name",
            f"a staging database name must contain {STAGING_WORD!r} (e.g. homayar_{STAGING_WORD})",
        )


def _prepare_staging(admin_url: str, staging: str, backup_id: str, manifest_mac: str) -> dict:
    marker = read_marker(admin_url, staging)
    with connect(with_database(admin_url, "postgres"), autocommit=True) as connection:
        if marker is not False:
            if marker is None:
                raise RestoreRefused(
                    "staging_not_ours", f"database {staging} exists and is not a restore target"
                )
            if marker.get("state") == "promoted":
                raise RestoreRefused("staging_promoted", f"database {staging} was already promoted")
            if marker.get("backup_id") != backup_id:
                raise RestoreRefused(
                    "staging_other_backup",
                    f"database {staging} holds a restore of {marker.get('backup_id')}",
                )
            # Idempotent retry: a half-done restore of the same backup is redone.
            connection.execute(f'DROP DATABASE "{staging}" WITH (FORCE)')
        connection.execute(f"CREATE DATABASE \"{staging}\" TEMPLATE template0 ENCODING 'UTF8'")
    marker = {
        "backup_id": backup_id,
        "manifest_mac": manifest_mac,
        "state": "restoring",
        "at": _now(),
    }
    write_marker(admin_url, staging, marker)
    return marker


def describe_storage_target(settings: Any) -> str:
    """Identity of the object store a restore writes into (no credentials)."""
    if settings.storage_backend == "local":
        return f"local:{Path(settings.storage_local_root).resolve()}"
    endpoint = settings.storage_endpoint_url or "aws"
    return f"s3:{endpoint}/{settings.storage_bucket}"


def state_path(work_dir: Path) -> Path:
    return Path(work_dir) / STATE_FILE


def read_state(work_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads(state_path(work_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_state(work_dir: Path, state: dict[str, Any]) -> None:
    Path(work_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_path(work_dir)
    temporary = path.with_name(STATE_FILE + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def restore_backup(
    *,
    set_dir: Path,
    key: BackupKey,
    admin_url: str,
    staging_database: str,
    storage: ObjectStorage | None,
    work_dir: Path,
    storage_target: str = "",
    live_database_url: str | None = None,
    app_role: str = APP_ROLE,
    resume: bool = False,
) -> dict[str, Any]:
    """``storage_target`` names the object store (``describe_storage_target(settings)``);
    a resume and the reconciliation must use the same one."""
    staging = validate_database_name(staging_database)
    if storage is not None and not storage_target:
        raise RestoreRefused("storage_target_missing", "name the target object store")
    timings: dict[str, float] = {}
    started = time.monotonic()
    started_at = _now()

    def lap(name: str, since: float) -> float:
        timings[name] = round(time.monotonic() - since, 3)
        return time.monotonic()

    mark = time.monotonic()
    verify_set(set_dir, key, deep=False)
    manifest = read_manifest(set_dir)
    backup_id = manifest["backup_id"]
    catalog = load_catalog(set_dir, manifest, key)
    revisions = catalog["database"]["alembic_revisions"]
    if len(revisions) != 1:
        raise RestoreRefused(
            "unmigrated_source",
            "the backup has no single Alembic revision; restore needs a migrated source",
        )
    revision = revisions[0]
    if not code_has_revision(revision):
        raise RestoreRefused(
            "code_older_than_backup",
            f"revision {revision} is not in this release; "
            "restore with the backup's release or a newer one",
        )
    _isolation_guard(
        admin_url=admin_url, staging=staging, live_database_url=live_database_url, catalog=catalog
    )
    previous = read_state(work_dir)
    if resume and not (previous and previous.get("backup_id") == backup_id):
        raise RestoreRefused(
            "nothing_to_resume", "no earlier restore of this backup in the work dir"
        )
    if resume and previous.get("storage_target") != (storage_target if storage else None):
        raise RestoreRefused(
            "storage_target_mismatch",
            "a resume must write into the object store the interrupted restore used",
        )
    if storage is not None and not resume:
        occupied = target_has_objects(storage, catalog["workspaces"])
        if occupied:
            raise RestoreRefused(
                "target_store_not_empty",
                f"the target object store already holds objects of {len(occupied)} tenant(s) of "
                "this backup; restore into an empty, isolated store (or --resume this restore)",
            )
    mark = lap("verify", mark)
    with staging_lock(admin_url, staging):
        return _restore_locked(
            set_dir=Path(set_dir),
            key=key,
            admin_url=admin_url,
            staging=staging,
            storage=storage,
            work_dir=work_dir,
            storage_target=storage_target,
            app_role=app_role,
            manifest=manifest,
            catalog=catalog,
            revision=revision,
            timings=timings,
            started=started,
            started_at=started_at,
            mark=mark,
        )


def _restore_locked(
    *,
    set_dir: Path,
    key: BackupKey,
    admin_url: str,
    staging: str,
    storage: ObjectStorage | None,
    work_dir: Path,
    storage_target: str,
    app_role: str,
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    revision: str,
    timings: dict[str, float],
    started: float,
    started_at: str,
    mark: float,
) -> dict[str, Any]:
    backup_id = manifest["backup_id"]

    def lap(name: str, since: float) -> float:
        timings[name] = round(time.monotonic() - since, 3)
        return time.monotonic()

    state: dict[str, Any] = {
        "backup_id": backup_id,
        "set_dir": str(set_dir),
        "staging_database": staging,
        "server": list(server_identity(admin_url)),
        "storage_target": storage_target if storage is not None else None,
        # The set this restore verified; reconcile accepts no other.
        "manifest_mac": manifest["mac"],
        "backup_created_at": manifest["created_at"],
        "started_at": started_at,
        "status": "restoring",
        "phases": {},
    }
    write_state(work_dir, state)

    restoring = _prepare_staging(admin_url, staging, backup_id, manifest["mac"])
    staging_url = with_database(admin_url, staging)
    run_alembic(staging_url, revision)
    mark = lap("schema_at_backup_revision", mark)
    faults.point("before_load")

    def read_table(table: str):
        name = f"db/{table}.copy.enc"
        return decrypt_chunks(Path(set_dir) / name, key, backup_id=backup_id, name=name)

    loaded = load_tables(staging_url, catalog["database"], read_table)
    mark = lap("load", mark)

    heads = code_heads()
    if heads != [revision]:
        run_alembic(staging_url, "head")
    mark = lap("migrate_forward", mark)

    engine = create_engine(staging_url)
    try:
        with engine.begin() as connection:
            apply_row_level_security(connection, role=app_role)
    finally:
        engine.dispose()
    faults.point("before_objects")

    objects: dict[str, Any] = {"objects": 0, "written": 0, "already_present": 0, "bytes": 0}
    if storage is not None:
        objects = restore_objects(
            storage,
            inventory=catalog["objects"]["inventory"],
            directory=Path(set_dir),
            key=key,
            backup_id=backup_id,
        )
    mark = lap("objects", mark)

    cas_marker(admin_url, staging, restoring, {**restoring, "state": "loaded", "at": _now()})
    timings["total"] = round(time.monotonic() - started, 3)
    state.update(
        {
            "status": "loaded",
            "finished_at": _now(),
            "revision_at_backup": revision,
            "revision_now": heads,
            "database": {
                "tables": loaded["tables"],
                "rows": loaded["rows"],
                "tenant_tables_verified": loaded["tenant_tables_verified"],
                "foreign_keys_verified": loaded["foreign_keys_verified"],
            },
            "objects": objects,
            "timings_seconds": timings,
        }
    )
    write_state(work_dir, state)
    return state


__all__ = [
    "BackupInvalid",
    "DatabaseBackupError",
    "RestoreRefused",
    "read_marker",
    "restore_backup",
    "write_marker",
]
