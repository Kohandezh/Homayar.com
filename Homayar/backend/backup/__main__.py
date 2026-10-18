"""Operator CLI: ``python -m backend.backup <command>`` (ADR 0035).

Common environment: ``BACKUP_ROOT`` (default ``/backups``),
``HOMAYAR_BACKUP_KEY_FILE`` (or ``HOMAYAR_BACKUP_KEY``), the application's
own settings (``DATABASE_URL``, ``STORAGE_*``, ``TOMBSTONE_JOURNAL_DIR``).
The administrative DSN comes from ``BACKUP_DATABASE_URL``,
``BACKUP_DATABASE_URL_FILE`` or else ``DATABASE_URL`` -- never from the
command line (a password on argv is visible to every local user).  It must
log in as a superuser / BYPASSRLS role (the bundled Compose
``POSTGRES_USER`` is one); the application itself switches to
``homayar_app`` with ``SET ROLE``.

Commands (exit 0 = success, 1 = refused/failed, 2 = usage):

  keygen KEY_ID                     print a new key line for the secret manager
  create                            take one consistent, encrypted backup
  list                              complete sets (and interrupted partials)
  verify [ID|PATH] [--no-key]       prove a set is complete and intact; a deep
                                    verify marks it verified and moves the
                                    postgres/objects freshness gauges
  prune [--dry-run]                 apply 30 daily + 12 monthly retention
  replicate ID DEST                 copy a set off the host (directory or s3://)
  fetch ID SOURCE                   pull an s3:// copy back and verify it
  restore ID --staging-db NAME      restore into an isolated staging database
  reconcile ID --staging-db NAME    replay tombstones, verify, write the report
  promote ID --staging-db NAME --live-db NAME   rename staging to live (PASS only)
  gate --live-db NAME               exit 0 only for a promoted, reconciled restore
  textfile-check                    can this user write the freshness textfile? (exit 4 if not)
  retired                           databases kept by promote; overdue past drop_after
  drop-retired NAME --confirm NAME [--live-db NAME]
                                    drop one (never the live, a staging or an in-use one)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from backend.backup.crypto import BackupKeyError, generate_key, load_key
from backend.backup.manifest import BackupInvalid, final_dir, list_complete_sets, list_partial_sets


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _root(args) -> Path:
    return Path(args.root or os.environ.get("BACKUP_ROOT") or "/backups")


def _set_dir(args, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate
    return final_dir(_root(args), value)


def _work_dir(args, backup_id: str) -> Path:
    return Path(args.work_dir) if args.work_dir else _root(args) / "restores" / backup_id


def _settings_with_target_storage(args):
    from backend.config import get_settings

    settings = get_settings()
    updates: dict[str, Any] = {}
    if getattr(args, "target_storage_root", None):
        updates.update(storage_backend="local", storage_local_root=args.target_storage_root)
    if getattr(args, "target_bucket", None):
        updates.update(storage_bucket=args.target_bucket)
    return settings.model_copy(update=updates) if updates else settings


def _admin_url(args, settings=None) -> str:
    if os.environ.get("BACKUP_DATABASE_URL"):
        return os.environ["BACKUP_DATABASE_URL"]
    if os.environ.get("BACKUP_DATABASE_URL_FILE"):
        return Path(os.environ["BACKUP_DATABASE_URL_FILE"]).read_text(encoding="utf-8").strip()
    if settings is None:
        from backend.config import get_settings

        settings = get_settings()
    return settings.database_url


# The step itself succeeded; only the freshness textfile could not be written.
# The gauge stays stale (or missing) and its alert fires; callers go on.
EXIT_GAUGE_NOT_WRITTEN = 4


def _record_gauges(path: Path, targets: dict[str, float]) -> str | None:
    """Write the freshness gauges; a failure is reported, never fatal to the step."""
    from backend.backup import metrics

    try:
        metrics.record_success(path, targets)
    except OSError as exc:
        message = f"freshness gauge not written to {path}: {exc.strerror or exc}"
        print(f"WARNING: {message}", file=sys.stderr)
        return message
    return None


def cmd_keygen(args) -> int:
    print(generate_key(args.key_id))
    return 0


def cmd_create(args) -> int:
    from backend.backup.create import create_backup
    from backend.config import get_settings
    from backend.storage import create_object_storage

    settings = get_settings()
    manifest = create_backup(
        root=_root(args),
        key=load_key(key_file=args.key_file),
        database_url=_admin_url(args, settings),
        storage=None if args.no_objects else create_object_storage(settings),
        settings=settings,
    )
    _print(
        {
            k: manifest[k]
            for k in (
                "backup_id",
                "created_at",
                "path",
                "size_bytes",
                "stores",
                "durations_seconds",
            )
        }
    )
    return 0


def cmd_list(args) -> int:
    root = _root(args)
    _print(
        {
            "complete": [name for name, _ in list_complete_sets(root)],
            "partial": [p.name for p in list_partial_sets(root)],
        }
    )
    return 0


def cmd_verify(args) -> int:
    from backend.backup.verify import verify_set

    sets = [path for _, path in list_complete_sets(_root(args))]
    target = _set_dir(args, args.backup) if args.backup else (sets[-1] if sets else None)
    if target is None:
        raise BackupInvalid("no_backup", "no complete set in the backup root")
    key = None if args.no_key else load_key(key_file=args.key_file)
    summary = verify_set(target, key, deep=not args.no_key)
    gauge_error = _mark_verified(target, summary) if summary["decrypted"] else None
    _print(summary)
    return EXIT_GAUGE_NOT_WRITTEN if gauge_error else 0


def _mark_verified(set_dir: Path, summary: dict[str, Any]) -> str | None:
    """A deep verify is what makes a set count: retention prefers it and the
    freshness gauges move (to its snapshot time, never backwards).  The set
    is marked verified even when the gauges cannot be written."""
    from datetime import UTC, datetime

    from backend.backup import metrics
    from backend.backup.manifest import read_manifest

    manifest = read_manifest(set_dir)
    if set_dir.parent.name == "sets":
        marker = set_dir.parent / f"{manifest['backup_id']}.verified"
        marker.write_text(datetime.now(UTC).isoformat() + "\n", encoding="ascii")
    snapshot = datetime.fromisoformat(manifest["created_at"]).timestamp()
    targets = {"postgres": snapshot}
    objects = manifest["stores"]["objects"]
    if objects.get("included") and objects.get("integrity", "complete") == "complete":
        targets["objects"] = snapshot
    root = set_dir.parent.parent if set_dir.parent.name == "sets" else set_dir.parent
    error = _record_gauges(metrics.textfile_path(root), targets)
    if error:
        summary["gauge_error"] = error
    else:
        summary["gauges_updated"] = sorted(targets)
    return error


def cmd_prune(args) -> int:
    from backend.backup.replicate import prune_s3
    from backend.backup.retention import prune

    if args.destination and args.destination.startswith("s3://"):
        result = prune_s3(
            args.destination, daily=args.daily, monthly=args.monthly, dry_run=args.dry_run
        )
    else:
        root = Path(args.destination) if args.destination else _root(args)
        result = prune(root, daily=args.daily, monthly=args.monthly, dry_run=args.dry_run)
    _print(result)
    return 0


def cmd_replicate(args) -> int:
    from datetime import datetime

    from backend.backup import metrics
    from backend.backup.manifest import read_manifest
    from backend.backup.replicate import replicate_to_directory, replicate_to_s3

    set_dir = _set_dir(args, args.backup)
    if args.destination.startswith("s3://"):
        result = replicate_to_s3(set_dir, args.destination)
    else:
        result = replicate_to_directory(set_dir, Path(args.destination))
    created = datetime.fromisoformat(read_manifest(set_dir)["created_at"]).timestamp()
    error = _record_gauges(metrics.textfile_path(_root(args)), {"offsite": created})
    if error:
        result["gauge_error"] = error
    _print(result)
    return EXIT_GAUGE_NOT_WRITTEN if error else 0


def cmd_textfile_check(args) -> int:
    from backend.backup import metrics

    path = metrics.textfile_path(_root(args))
    try:
        metrics.probe(path)
    except OSError as exc:
        _print({"textfile": str(path), "writable": False, "error": exc.strerror or str(exc)})
        return EXIT_GAUGE_NOT_WRITTEN
    _print({"textfile": str(path), "writable": True})
    return 0


def cmd_fetch(args) -> int:
    from backend.backup.replicate import fetch_from_s3

    _print({"path": str(fetch_from_s3(args.source, args.backup, _root(args)))})
    return 0


def cmd_restore(args) -> int:
    from backend.backup.restore import describe_storage_target, restore_backup
    from backend.config import get_settings
    from backend.storage import create_object_storage

    settings = _settings_with_target_storage(args)
    set_dir = _set_dir(args, args.backup)
    state = restore_backup(
        set_dir=set_dir,
        key=load_key(key_file=args.key_file),
        admin_url=_admin_url(args, settings),
        staging_database=args.staging_db,
        storage=None if args.no_objects else create_object_storage(settings),
        work_dir=_work_dir(args, set_dir.name),
        storage_target=describe_storage_target(settings),
        # The database the application serves (resolved settings, so a
        # DATABASE_URL_FILE deployment is guarded as well).
        live_database_url=get_settings().database_url,
        app_role=settings.database_app_role or "homayar_app",
        resume=args.resume,
    )
    _print(state)
    return 0


def cmd_reconcile(args) -> int:
    from backend.backup.reconcile import reconcile_restore
    from backend.backup.restore import describe_storage_target
    from backend.storage import create_object_storage

    settings = _settings_with_target_storage(args)
    set_dir = _set_dir(args, args.backup)
    report = reconcile_restore(
        set_dir=set_dir,
        key=load_key(key_file=args.key_file),
        admin_url=_admin_url(args, settings),
        staging_database=args.staging_db,
        settings=settings,
        storage=create_object_storage(settings),
        work_dir=_work_dir(args, set_dir.name),
        storage_target=describe_storage_target(settings),
        app_role=settings.database_app_role or "homayar_app",
        accept_held=args.accept_held,
        accept_missing=args.accept_missing,
        accept_no_journal=args.accept_no_journal,
        revoke_share_links=args.revoke_share_links,
    )
    _print(
        {
            "status": report["status"],
            "statuses": report["statuses"],
            "path": report["path"],
            "timings_seconds": report["timings_seconds"],
        }
    )
    return 0 if report["status"] == "PASS" else 1


def cmd_promote(args) -> int:
    from backend.backup.reconcile import REPORT_FILE, promote
    from backend.config import get_settings

    settings = get_settings()
    set_dir = _set_dir(args, args.backup)
    _print(
        promote(
            admin_url=_admin_url(args, settings),
            staging_database=args.staging_db,
            live_database=args.live_db,
            report_path=_work_dir(args, set_dir.name) / REPORT_FILE,
            key=load_key(key_file=args.key_file),
            retire_existing=args.retire_existing,
        )
    )
    return 0


def cmd_gate(args) -> int:
    from backend.backup.restore import read_marker

    marker = read_marker(_admin_url(args), args.live_db)
    if isinstance(marker, dict) and marker.get("state") == "promoted":
        _print({"gate": "open", "backup_id": marker.get("backup_id")})
        return 0
    if marker is None:
        _print({"gate": "open", "note": "not a restored database"})
        return 0
    _print({"gate": "closed", "marker": marker if isinstance(marker, dict) else "database missing"})
    return 1


def cmd_retired(args) -> int:
    from backend.backup.reconcile import list_retired

    _print(list_retired(_admin_url(args)))
    return 0


def cmd_drop_retired(args) -> int:
    from backend.backup.logical import database_name
    from backend.backup.reconcile import drop_retired
    from backend.config import get_settings

    settings = get_settings()
    _print(
        drop_retired(
            _admin_url(args, settings),
            args.database,
            confirm=args.confirm,
            live_database=args.live_db or database_name(settings.database_url),
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.backup",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", help="backup root (default $BACKUP_ROOT or /backups)")
    parser.add_argument("--key-file", help="key file (default $HOMAYAR_BACKUP_KEY_FILE)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen")
    p.add_argument("key_id")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("create")
    p.add_argument("--no-objects", action="store_true", help="database only (the set says so)")
    p.set_defaults(func=cmd_create)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p = sub.add_parser("verify")
    p.add_argument("backup", nargs="?")
    p.add_argument("--no-key", action="store_true", help="checksums only (off-site, no key)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("prune")
    p.add_argument("--daily", type=int, default=30)
    p.add_argument("--monthly", type=int, default=12)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--destination", help="an off-site copy (directory or s3://) instead of the root"
    )
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("replicate")
    p.add_argument("backup")
    p.add_argument("destination")
    p.set_defaults(func=cmd_replicate)

    p = sub.add_parser("fetch")
    p.add_argument("backup")
    p.add_argument("source")
    p.set_defaults(func=cmd_fetch)

    for name, func in (("restore", cmd_restore), ("reconcile", cmd_reconcile)):
        p = sub.add_parser(name)
        p.add_argument("backup")
        p.add_argument("--staging-db", required=True)
        p.add_argument("--work-dir")
        p.add_argument("--target-storage-root", help="restore objects into this local root")
        p.add_argument("--target-bucket", help="restore objects into this bucket")
        p.set_defaults(func=func)
        if name == "restore":
            p.add_argument("--no-objects", action="store_true")
            p.add_argument("--resume", action="store_true")
        else:
            p.add_argument("--accept-held", help="owner decision reference for held conversations")
            p.add_argument("--accept-missing", help="owner decision reference for missing objects")
            p.add_argument(
                "--accept-no-journal", help="owner decision reference: no tombstone journal"
            )
            p.add_argument("--revoke-share-links", action="store_true")

    p = sub.add_parser("promote")
    p.add_argument("backup")
    p.add_argument("--staging-db", required=True)
    p.add_argument("--live-db", required=True)
    p.add_argument("--work-dir")
    p.add_argument("--retire-existing", action="store_true")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("gate")
    p.add_argument("--live-db", required=True)
    p.set_defaults(func=cmd_gate)

    sub.add_parser("textfile-check").set_defaults(func=cmd_textfile_check)

    sub.add_parser("retired").set_defaults(func=cmd_retired)

    p = sub.add_parser("drop-retired")
    p.add_argument("database")
    p.add_argument("--confirm", required=True)
    p.add_argument("--live-db", help="the live database (default: the application's)")
    p.set_defaults(func=cmd_drop_retired)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BackupInvalid, BackupKeyError) as exc:
        _print({"error": getattr(exc, "code", "backup_key"), "message": str(exc)})
        return 1
    except Exception as exc:  # stable codes from the restore/database layers
        code = getattr(exc, "code", None)
        if code is None:
            raise
        _print({"error": code, "message": getattr(exc, "message", str(exc))})
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
