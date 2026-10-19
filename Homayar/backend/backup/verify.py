"""Verify a backup set offline (HOM-116/117).

``verify_set`` refuses anything it cannot prove:

* a partial directory (interrupted run) or a missing manifest;
* a manifest that is not valid, names an unknown format, was sealed with
  another key, or whose MAC does not verify (tampered metadata);
* a listed file that is missing or whose size / SHA-256 differs (bit rot,
  truncated copy, tampering) and any file that is not listed;
* with ``deep=True`` (default) a file that does not decrypt and
  authenticate, and a catalog that does not match the manifest.

Without a key only the structure and the ciphertext checksums are checked
(useful at an off-site copy that never holds the key).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.backup.crypto import (
    BackupKey,
    DecryptionError,
    decrypt_chunks,
    decrypt_to_bytes,
    file_sha256,
)
from backend.backup.manifest import (
    CATALOG_NAME,
    PARTIAL_PREFIX,
    BackupInvalid,
    check_manifest_mac,
    read_manifest,
)


def _listed_files(directory: Path) -> set[str]:
    found = set()
    for path in directory.rglob("*"):
        if path.is_file():
            found.add(path.relative_to(directory).as_posix())
    found.discard("manifest.json")
    return found


def load_catalog(directory: Path, manifest: dict[str, Any], key: BackupKey) -> dict[str, Any]:
    try:
        payload = decrypt_to_bytes(
            directory / CATALOG_NAME, key, backup_id=manifest["backup_id"], name=CATALOG_NAME
        )
    except FileNotFoundError as exc:
        raise BackupInvalid("file_missing", CATALOG_NAME) from exc
    except DecryptionError as exc:
        raise BackupInvalid("decryption_failed", str(exc)) from exc
    catalog = json.loads(payload)
    if catalog.get("backup_id") != manifest["backup_id"]:
        raise BackupInvalid("catalog_mismatch", "catalog belongs to another backup")
    return catalog


def verify_set(directory: Path, key: BackupKey | None, *, deep: bool = True) -> dict[str, Any]:
    """Return a summary of a verified set or raise ``BackupInvalid``."""
    directory = Path(directory)
    if directory.name.startswith(PARTIAL_PREFIX):
        raise BackupInvalid("incomplete", "partial set of an interrupted backup")
    if not directory.is_dir():
        raise BackupInvalid("set_missing", str(directory))
    manifest = read_manifest(directory)
    if directory.name != manifest["backup_id"]:
        raise BackupInvalid("manifest_mismatch", "directory name differs from the backup id")
    if key is not None:
        check_manifest_mac(manifest, key)

    listed = {entry["name"]: entry for entry in manifest["files"]}
    present = _listed_files(directory)
    unexpected = sorted(present - set(listed))
    if unexpected:
        raise BackupInvalid("unexpected_files", f"{len(unexpected)} file(s) not in the manifest")
    for name, entry in sorted(listed.items()):
        path = directory / name
        if not path.is_file():
            raise BackupInvalid("file_missing", name)
        digest, size = file_sha256(path)
        if size != entry["size"]:
            raise BackupInvalid("size_mismatch", name)
        if digest != entry["sha256"]:
            raise BackupInvalid("checksum_mismatch", name)

    summary: dict[str, Any] = {
        "backup_id": manifest["backup_id"],
        "created_at": manifest["created_at"],
        "files": len(listed),
        "size_bytes": sum(entry["size"] for entry in listed.values()),
        "mac_verified": key is not None,
        "decrypted": False,
    }
    if key is None or not deep:
        return summary

    for name, entry in sorted(listed.items()):
        plaintext = 0
        try:
            for chunk in decrypt_chunks(
                directory / name, key, backup_id=manifest["backup_id"], name=name
            ):
                plaintext += len(chunk)
        except DecryptionError as exc:
            raise BackupInvalid("decryption_failed", str(exc)) from exc
        if plaintext != entry["plaintext_size"]:
            raise BackupInvalid("size_mismatch", f"{name} plaintext")
    catalog = load_catalog(directory, manifest, key)
    expected_tables = {f"db/{table}.copy.enc" for table in catalog["database"]["tables"]}
    expected_objects = {entry["file"] for entry in catalog["objects"]["inventory"]}
    if expected_tables | expected_objects | {CATALOG_NAME} != set(listed):
        raise BackupInvalid("catalog_mismatch", "catalog and manifest list different files")
    summary["decrypted"] = True
    summary["tables"] = len(expected_tables)
    summary["objects"] = len(expected_objects)
    return summary
