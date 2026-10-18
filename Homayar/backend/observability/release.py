"""Release identity of the running backend (WP01-T04/T06, HOM-28, HOM-29).

The release build (HOM-28) is meant to build the API image from an exact source
revision and write ``build-info.json`` into it with this command; until that
build step lands no image contains the file, and ``runtime_identity`` reports
``unknown`` for everything but the migration head and Python version::

    python -m backend.observability.release write-build-info \
        --source-sha <sha> --source-date-epoch <epoch> --profile <profile> \
        --lock <path to uv.lock> --pyproject <path> --output /app/build-info.json

The document holds only facts that the image inputs determine (source
revision, lock and pyproject digests, Python version, the digest of the
installed distribution set, the Alembic migration head, the build profile and
``SOURCE_DATE_EPOCH``), so building the same revision twice yields the same
file.  At runtime ``/metrics`` exposes it as ``homayar_build_info`` and
compares the migration head with the database's ``alembic_version``; the
release manifest (``infra/release/release_manifest.py``) records the same
values for the revision, so an operator can check that a running backend is
the one the manifest describes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

BUILD_INFO_SCHEMA = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "backend" / "migrations"
BUILD_INFO_PATH = PROJECT_ROOT / "build-info.json"
APPLICATION_NAME = "homayar-platform"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migration_heads(migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Alembic heads of the migration scripts shipped with this code (sorted)."""
    from alembic.script import ScriptDirectory

    return sorted(ScriptDirectory(str(migrations_dir)).get_heads())


@lru_cache(maxsize=1)
def code_migration_head() -> str | None:
    """The single migration head of this code, ``None`` when it is not exactly one."""
    try:
        heads = migration_heads()
    except Exception:
        return None
    return heads[0] if len(heads) == 1 else None


def installed_distributions() -> list[str]:
    """``name==version`` of every distribution in the running environment, sorted."""
    entries = {
        f"{(dist.metadata['Name'] or '').strip().lower().replace('_', '-')}=={dist.version}"
        for dist in metadata.distributions()
        if dist.metadata["Name"]
    }
    return sorted(entries)


def dependency_set_sha256(entries: list[str] | None = None) -> str:
    lines = entries if entries is not None else installed_distributions()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def application_version() -> str:
    try:
        return metadata.version(APPLICATION_NAME)
    except metadata.PackageNotFoundError:
        return _pyproject_version(PROJECT_ROOT / "pyproject.toml") or "unknown"


def _pyproject_version(path: Path) -> str | None:
    if not path.is_file():
        return None
    import tomllib

    with path.open("rb") as handle:
        return tomllib.load(handle).get("project", {}).get("version")


def build_info_document(
    *,
    source_sha: str,
    source_date_epoch: int,
    profile: str,
    lock_path: Path,
    pyproject_path: Path,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> dict[str, Any]:
    if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ValueError("source_sha must be a full 40-character lowercase git SHA")
    heads = migration_heads(migrations_dir)
    if len(heads) != 1:
        raise ValueError(f"Expected exactly one migration head, found {heads}")
    entries = installed_distributions()
    return {
        "schema_version": BUILD_INFO_SCHEMA,
        "application": {
            "name": APPLICATION_NAME,
            "version": _pyproject_version(pyproject_path) or "unknown",
        },
        "source": {"sha": source_sha, "date_epoch": int(source_date_epoch)},
        "profile": profile,
        "runtime": {
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "dependencies": {
            "uv_lock_sha256": sha256_file(lock_path),
            "pyproject_sha256": sha256_file(pyproject_path),
            "installed_set_sha256": dependency_set_sha256(entries),
            "installed_count": len(entries),
        },
        "schema": {"migration_head": heads[0]},
    }


def canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


@lru_cache(maxsize=1)
def load_build_info(path: str = str(BUILD_INFO_PATH)) -> dict[str, Any] | None:
    """The image's ``build-info.json`` (``None`` outside a release image)."""
    file = Path(path)
    if not file.is_file():
        return None
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def runtime_identity() -> dict[str, str]:
    """Bounded labels for ``homayar_build_info`` (one series per process)."""
    info = load_build_info() or {}
    source = info.get("source") or {}
    dependencies = info.get("dependencies") or {}
    return {
        "version": str((info.get("application") or {}).get("version") or application_version()),
        "source_sha": str(source.get("sha") or "unknown"),
        "profile": str(info.get("profile") or "unknown"),
        "migration_head": str(
            (info.get("schema") or {}).get("migration_head") or code_migration_head() or "unknown"
        ),
        "python_version": platform.python_version(),
        "uv_lock_sha256": str(dependencies.get("uv_lock_sha256") or "unknown")[:16],
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.observability.release")
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write-build-info")
    write.add_argument("--source-sha", required=True)
    write.add_argument("--source-date-epoch", required=True, type=int)
    write.add_argument("--profile", required=True)
    write.add_argument("--lock", required=True, type=Path)
    write.add_argument("--pyproject", required=True, type=Path)
    write.add_argument("--output", required=True, type=Path)
    commands.add_parser("show")
    args = parser.parse_args(argv)
    if args.command == "write-build-info":
        document = build_info_document(
            source_sha=args.source_sha,
            source_date_epoch=args.source_date_epoch,
            profile=args.profile,
            lock_path=args.lock,
            pyproject_path=args.pyproject,
        )
        args.output.write_text(canonical_json(document), encoding="utf-8")
        return 0
    print(canonical_json(load_build_info() or {"build_info": None, **runtime_identity()}), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - build/operator entrypoint
    sys.exit(_main(sys.argv[1:]))
