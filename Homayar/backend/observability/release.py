"""Release identity of the running backend (WP01-T04/T06, HOM-28, HOM-29).

The release build (HOM-28) is meant to build the API image from an exact source
revision and write ``build-info.json`` into it with this command; until that
build step lands no image contains the file, and ``runtime_identity`` reports
``unknown`` for everything but the migration head and Python version::

    python -m backend.observability.release write-build-info \
        --source-sha <sha> --source-date-epoch <epoch> --profile <profile> \
        --base-image <PYTHON_IMAGE> --uv-version <UV_VERSION> \
        --lock <path to uv.lock> --pyproject <path> --output /app/build-info.json

The document holds only facts that the image inputs determine (source
revision, lock and pyproject digests, the digest of the shipped code files,
Python version, the digest of the installed distribution set, the Alembic
migration head, the build profile,
the base image reference and uv version the build used and
``SOURCE_DATE_EPOCH``), so building the same revision twice yields the same
file.  An image built without release arguments carries the all-zero
``UNRELEASED_SHA`` and reports ``source_sha="unreleased"``.  At runtime
``/metrics`` exposes it as ``homayar_build_info`` and compares the migration
head with the database's ``alembic_version``; the release manifest
(``infra/release/release_manifest.py``) records the same values for the
revision, so an operator can check that a running backend is the one the
manifest describes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Mapping
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

BUILD_INFO_SCHEMA = 1
# SOURCE_SHA of an image built without release arguments (developer and CI smoke builds).
UNRELEASED_SHA = "0" * 40
# Bounded vocabulary: the profile is a ``homayar_build_info`` label.
RELEASE_PROFILES = ("standard", "ai-local")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "backend" / "migrations"
BUILD_INFO_PATH = PROJECT_ROOT / "build-info.json"
APPLICATION_NAME = "homayar-platform"
# What the API image ships of this code (infra/docker/api.Dockerfile.dockerignore):
# alembic.ini and backend/ without the tests, environments and caches.  The release
# manifest computes the same digest from the Git objects of the revision.
SHIPPED_PATHS = ("alembic.ini", "backend")
UNSHIPPED_PREFIXES = ("backend/tests/",)
UNSHIPPED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".venv"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_shipped(relative: str) -> bool:
    """Whether the image ships the file at this POSIX path relative to the project root."""
    parts = relative.split("/")
    return not (
        relative.startswith(UNSHIPPED_PREFIXES)
        or relative.endswith(".pyc")
        or any(part in UNSHIPPED_PARTS or part.startswith(".env") for part in parts)
    )


def code_file_digests(root: Path = PROJECT_ROOT) -> dict[str, str]:
    """sha256 of every shipped code file under ``root``, by POSIX relative path."""
    digests: dict[str, str] = {}
    for top in SHIPPED_PATHS:
        base = root / top
        for path in [base] if base.is_file() else base.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and is_shipped(relative):
                digests[relative] = sha256_file(path)
    return digests


def files_sha256(digests: Mapping[str, str]) -> str:
    """Digest of a file set: the sorted lines ``<path>\\0<sha256 of content>\\n``."""
    listing = "".join(f"{path}\0{digests[path]}\n" for path in sorted(digests))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


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
    base_image: str | None = None,
    uv_version: str | None = None,
    code_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ValueError("source_sha must be a full 40-character lowercase git SHA")
    if profile not in RELEASE_PROFILES:
        raise ValueError(f"profile must be one of {', '.join(RELEASE_PROFILES)}")
    heads = migration_heads(migrations_dir)
    if len(heads) != 1:
        raise ValueError(f"Expected exactly one migration head, found {heads}")
    entries = installed_distributions()
    code = code_file_digests(code_root)
    return {
        "schema_version": BUILD_INFO_SCHEMA,
        "application": {
            "name": APPLICATION_NAME,
            "version": _pyproject_version(pyproject_path) or "unknown",
        },
        "source": {
            "sha": source_sha,
            "date_epoch": int(source_date_epoch),
            "released": source_sha != UNRELEASED_SHA,
        },
        "profile": profile,
        "build": {"base_image": base_image or "unknown", "uv_version": uv_version or "unknown"},
        "code": {"files_sha256": files_sha256(code), "file_count": len(code)},
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
    source_sha = str(source.get("sha") or "unknown")
    return {
        "version": str((info.get("application") or {}).get("version") or application_version()),
        # "unknown": no build-info.json (not an image); "unreleased": built without SOURCE_SHA.
        "source_sha": "unreleased" if source_sha == UNRELEASED_SHA else source_sha,
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
    write.add_argument("--profile", required=True, choices=RELEASE_PROFILES)
    write.add_argument("--base-image")
    write.add_argument("--uv-version")
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
            base_image=args.base_image,
            uv_version=args.uv_version,
        )
        args.output.write_text(canonical_json(document), encoding="utf-8")
        return 0
    print(canonical_json(load_build_info() or {"build_info": None, **runtime_identity()}), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - build/operator entrypoint
    sys.exit(_main(sys.argv[1:]))
