"""Backup set layout and manifest (HOM-116, ADR 0035).

A backup set is one directory below ``<BACKUP_ROOT>/sets/``::

    <backup_id>/
      manifest.json          plaintext, MAC-protected: identity, versions,
                             stores, encryption state, every file's checksum
      catalog.json.enc       encrypted: tables, columns, row counts, per-tenant
                             counts, object references and inventory, config
                             references (never secret values)
      db/<table>.copy.enc    encrypted COPY stream of one table
      objects/<xx>/<h>.enc   encrypted bytes of one stored object

A set is written under ``sets/.partial-<backup_id>/`` and renamed into place
only after every file is fsynced and the manifest is written: the rename is
the completion marker.  Anything still named ``.partial-*`` is an interrupted
run and is never listed, verified as complete or restored.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.backup.crypto import ALGORITHM, BackupKey

FORMAT = "homayar-backup/1"
MANIFEST_NAME = "manifest.json"
CATALOG_NAME = "catalog.json.enc"
SETS_DIR = "sets"
PARTIAL_PREFIX = ".partial-"
LOCK_NAME = ".backup.lock"

_BACKUP_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
_FILE_NAME = re.compile(
    r"^(catalog\.json\.enc|db/[a-z_][a-z0-9_]{0,62}\.copy\.enc|objects/[0-9a-f]{2}/[0-9a-f]{64}\.enc)$"
)


class BackupInvalid(RuntimeError):
    """A backup set is incomplete, altered or unusable; ``code`` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FileEntry:
    name: str
    size: int
    sha256: str
    plaintext_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "plaintext_size": self.plaintext_size,
        }


def new_backup_id(now: datetime | None = None) -> str:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return f"{moment:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def valid_backup_id(value: str) -> bool:
    return bool(_BACKUP_ID.fullmatch(value))


def valid_file_name(value: str) -> bool:
    return bool(_FILE_NAME.fullmatch(value))


def sets_dir(root: Path) -> Path:
    return Path(root) / SETS_DIR


def partial_dir(root: Path, backup_id: str) -> Path:
    return sets_dir(root) / f"{PARTIAL_PREFIX}{backup_id}"


def final_dir(root: Path, backup_id: str) -> Path:
    return sets_dir(root) / backup_id


def _canonical(manifest: dict[str, Any]) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def seal(manifest: dict[str, Any], key: BackupKey) -> dict[str, Any]:
    return {**manifest, "mac": key.mac("manifest", _canonical(manifest))}


def write_manifest(directory: Path, manifest: dict[str, Any], key: BackupKey) -> None:
    sealed = seal(manifest, key)
    path = Path(directory) / MANIFEST_NAME
    temporary = path.with_name(MANIFEST_NAME + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(sealed, indent=2, sort_keys=True) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def read_manifest(directory: Path) -> dict[str, Any]:
    """Parse and structurally validate the manifest (no key needed)."""
    path = Path(directory) / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="ascii"))
    except FileNotFoundError as exc:
        raise BackupInvalid("manifest_missing", f"{path.name} is missing") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupInvalid("manifest_corrupt", "manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise BackupInvalid("manifest_corrupt", "manifest is not an object")
    if manifest.get("format") != FORMAT:
        raise BackupInvalid("format_unsupported", f"format {manifest.get('format')!r}")
    backup_id = manifest.get("backup_id")
    if not isinstance(backup_id, str) or not valid_backup_id(backup_id):
        raise BackupInvalid("manifest_corrupt", "backup_id is missing or malformed")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise BackupInvalid("manifest_corrupt", "file list is missing")
    names = set()
    for entry in files:
        if not isinstance(entry, dict) or not valid_file_name(str(entry.get("name", ""))):
            raise BackupInvalid("manifest_corrupt", "file entry is malformed")
        if not isinstance(entry.get("size"), int) or not isinstance(entry.get("sha256"), str):
            raise BackupInvalid("manifest_corrupt", f"file entry {entry['name']} is malformed")
        if entry["name"] in names:
            raise BackupInvalid("manifest_corrupt", f"duplicate file entry {entry['name']}")
        names.add(entry["name"])
    if CATALOG_NAME not in names:
        raise BackupInvalid("manifest_corrupt", "catalog is not listed")
    encryption = manifest.get("encryption")
    if not isinstance(encryption, dict) or encryption.get("state") != "encrypted":
        raise BackupInvalid("manifest_corrupt", "encryption state is missing")
    if encryption.get("algorithm") != ALGORITHM:
        raise BackupInvalid("format_unsupported", f"algorithm {encryption.get('algorithm')!r}")
    if not isinstance(manifest.get("mac"), str):
        raise BackupInvalid("manifest_corrupt", "manifest MAC is missing")
    return manifest


def check_manifest_mac(manifest: dict[str, Any], key: BackupKey) -> None:
    encryption = manifest["encryption"]
    if (
        encryption.get("key_id") != key.key_id
        or encryption.get("key_fingerprint") != key.fingerprint
    ):
        raise BackupInvalid(
            "wrong_key",
            f"set was encrypted with key {encryption.get('key_id')!r}, not {key.key_id!r}",
        )
    expected = key.mac("manifest", _canonical(manifest))
    if not secrets.compare_digest(expected, manifest["mac"]):
        raise BackupInvalid("manifest_tampered", "manifest MAC does not verify")


def list_complete_sets(root: Path) -> list[tuple[str, Path]]:
    """Complete sets (renamed into place) sorted oldest first."""
    directory = sets_dir(root)
    if not directory.is_dir():
        return []
    found = [
        (entry.name, entry)
        for entry in directory.iterdir()
        if entry.is_dir() and valid_backup_id(entry.name)
    ]
    return sorted(found)


def list_partial_sets(root: Path) -> list[Path]:
    directory = sets_dir(root)
    if not directory.is_dir():
        return []
    return sorted(
        entry
        for entry in directory.iterdir()
        if entry.is_dir() and entry.name.startswith(PARTIAL_PREFIX)
    )
