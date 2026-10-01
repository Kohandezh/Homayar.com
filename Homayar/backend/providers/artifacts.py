"""Offline model artifact verification (local AI runtime, offline profile).

A manifest may pin the local files a model needs (``ModelManifest.artifacts``:
path relative to the artifact root, ``file`` or ``directory``, SHA-256).  An
offline deployment proves before any job that every pinned artifact is present
and byte-identical; nothing is downloaded and nothing is assumed.

A directory digest is the SHA-256 of the sorted lines
``<relative posix path>\\0<file sha256>\\n`` of every regular file below it
(symbolic links are refused: an artifact is content, not a pointer).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from backend.providers.registry import ModelArtifact, ModelManifest

OK = "ok"
MISSING = "missing"
MISMATCH = "mismatch"
UNSAFE = "unsafe_path"
_CHUNK = 1024 * 1024


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_digest(path: Path) -> str:
    lines: list[str] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"symbolic link inside artifact: {item.relative_to(path)}")
        if item.is_file():
            lines.append(f"{item.relative_to(path).as_posix()}\0{file_digest(item)}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactCheck:
    path: str
    kind: str
    expected_sha256: str
    actual_sha256: str | None
    status: str

    def entry(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "status": self.status,
        }


@dataclass(frozen=True)
class ArtifactReport:
    manifest_id: str
    version: int
    checks: tuple[ArtifactCheck, ...] = field(default_factory=tuple)

    @property
    def declared(self) -> bool:
        return bool(self.checks)

    @property
    def ok(self) -> bool:
        return self.declared and all(check.status == OK for check in self.checks)

    def document(self) -> dict[str, object]:
        return {
            "manifest_id": self.manifest_id,
            "version": self.version,
            "declared": self.declared,
            "ok": self.ok,
            "artifacts": [check.entry() for check in self.checks],
        }


def _check(artifact: ModelArtifact, root: Path) -> ArtifactCheck:
    def result(status: str, actual: str | None = None) -> ArtifactCheck:
        return ArtifactCheck(artifact.path, artifact.kind, artifact.sha256, actual, status)

    base = root.resolve()
    target = root / artifact.path
    try:
        resolved = target.resolve()
    except OSError:
        return result(MISSING)
    if base != resolved and base not in resolved.parents:
        return result(UNSAFE)
    if target.is_symlink():
        return result(UNSAFE)
    try:
        if artifact.kind == "file":
            if not target.is_file():
                return result(MISSING)
            actual = file_digest(target)
        else:
            if not target.is_dir():
                return result(MISSING)
            actual = directory_digest(target)
    except ValueError:
        return result(UNSAFE)
    except OSError:
        return result(MISSING)
    return result(OK if actual == artifact.sha256 else MISMATCH, actual)


def verify_artifacts(manifest: ModelManifest, root: str | Path) -> ArtifactReport:
    """Check every artifact the manifest pins under ``root``."""
    base = Path(root)
    return ArtifactReport(
        manifest.manifest_id,
        manifest.version,
        tuple(_check(artifact, base) for artifact in manifest.artifacts),
    )
