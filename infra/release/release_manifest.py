#!/usr/bin/env python3
"""Release manifest of an exact source revision (WP01-T04/T06, HOM-28, HOM-29; ADR 0036).

    python3 infra/release/release_manifest.py generate --rev <commit> [--profile standard] \\
        [--image api=sha256:<digest>]... [--validation ci=https://...]... \\
        [--build-time 2026-10-18T00:00:00Z] [--output manifest.json]
    python3 infra/release/release_manifest.py build-args --rev <commit> [--profile standard]
    python3 infra/release/release_manifest.py verify --manifest manifest.json \\
        --build-info build-info.json [--image-labels labels.json]

``generate`` reads every input from the Git object database at the revision
(``git cat-file``, ``git ls-tree``), never from the working tree, so untracked
files, uncommitted edits and the caller's environment cannot change the result.
The output is canonical JSON (sorted keys, no wall-clock time: the build time is
the commit time unless ``--build-time`` is given) with ``manifest_sha256`` over
the canonical body: an integrity checksum against accidental change, not proof
of authorship (it is unkeyed, so whoever edits a manifest can recompute it).
It records file digests, versions and the migration head only: no environment
variable, ``.env`` content or Git identity.

``build-args`` prints the ``KEY=VALUE`` Docker build arguments of the API image
for the revision.  ``verify`` compares a manifest with the ``build-info.json``
of an image (``backend/observability/release.py``) and, optionally, with the
image's OCI labels; it exits 1 and prints each mismatch.

Standard library only (Python 3.12).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPOSITORY = "https://github.com/Kohandezh/Homayar.com"
UNRELEASED_SHA = "0" * 40

PRODUCT_DIR = "Homayar"
PYPROJECT = "Homayar/pyproject.toml"
PYTHON_VERSION_FILE = "Homayar/.python-version"
MIGRATIONS_VERSIONS = "Homayar/backend/migrations/versions"
API_DOCKERFILE = "infra/docker/api.Dockerfile"
# Manifest key -> path at the revision.
DEPENDENCY_FILES = {
    "uv_lock": "Homayar/uv.lock",
    "pyproject": PYPROJECT,
    "backend_requirements": "Homayar/backend/requirements.txt",
    "console_package_lock": "Homayar/frontend/package-lock.json",
    "website_pnpm_lock": "website/pnpm-lock.yaml",
}
# Image -> Dockerfile; its build context filter is "<Dockerfile>.dockerignore".
IMAGES = {
    "api": API_DOCKERFILE,
    "console-web": "infra/docker/console-web.Dockerfile",
    "egress-proxy": "infra/docker/egress-proxy.Dockerfile",
}
# Code the API image ships, relative to PRODUCT_DIR (api.Dockerfile.dockerignore); the
# same rule as backend.observability.release.is_shipped, which hashes the image's copy.
SHIPPED_PATHS = ("alembic.ini", "backend")
UNSHIPPED_PREFIXES = ("backend/tests/",)
UNSHIPPED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".venv"})
# Release profile -> INSTALL_AI_LOCAL build argument of the API image.
PROFILES = {"standard": "false", "ai-local": "true"}
# The images are input-reproducible (same source, locks and base digest), not bit-for-bit.
ALWAYS_PINNED = [
    "source revision (SOURCE_SHA, SOURCE_DATE_EPOCH)",
    "Python dependencies (Homayar/uv.lock)",
    "uv version (UV_VERSION)",
]
NEVER_PINNED = [
    "Debian packages installed with apt-get at build time",
    "image and layer digests (builds are not bit-for-bit reproducible)",
    "not signed / no provenance attestation; images are local ids, not registry digests",
]

_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_DOCKER_ARG = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", re.MULTILINE)
# Files Alembic loads as revisions (alembic.script.base._only_source_rev_file).
_REVISION_FILE = re.compile(r"(?!\.\#|__init__)(.*\.py)$")


class ManifestError(Exception):
    """An input at the revision is missing or inconsistent."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitRevision:
    """Read-only access to one commit through the object database."""

    def __init__(self, repo: Path, rev: str) -> None:
        self.repo = repo
        if not rev or rev.startswith("-"):
            raise ManifestError(f"invalid revision {rev!r}")
        resolved = self._git("rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}")
        self.sha = resolved.decode("ascii").strip()
        if not _SHA.fullmatch(self.sha):
            raise ManifestError(f"{rev!r} does not resolve to a SHA-1 commit")
        header = self._git("cat-file", "commit", self.sha).split(b"\n\n", 1)[0]
        fields: dict[str, str] = {}
        for line in header.decode("utf-8", "replace").splitlines():
            key, _, value = line.partition(" ")
            fields.setdefault(key, value)
        self.tree_sha = fields["tree"]
        # "committer <name> <email> <epoch> <tz>": only the epoch is used.
        self.commit_time = int(fields["committer"].rsplit(" ", 2)[1])

    def _git(self, *args: str, stdin: bytes | None = None) -> bytes:
        # GIT_* variables could point at another repository, index or object store.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_NO_REPLACE_OBJECTS="1", LC_ALL="C")
        completed = subprocess.run(  # noqa: S603 - git with arguments built by this module
            ["git", "-C", str(self.repo), *args],  # noqa: S607 - git from PATH, like CI
            input=stdin,
            capture_output=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", "replace").strip()
            raise ManifestError(f"git {args[0]} failed: {message or completed.returncode}")
        return completed.stdout

    def list_blobs(self, *pathspecs: str, recursive: bool = False) -> list[str]:
        """Files at the pathspecs (a directory needs a trailing slash)."""
        flags = ["-r"] if recursive else []
        output = self._git("ls-tree", "-z", *flags, "--full-tree", self.sha, "--", *pathspecs)
        paths = []
        for entry in output.split(b"\0"):
            if not entry:
                continue
            meta, path = entry.decode("utf-8").split("\t", 1)
            if meta.split(" ")[1] == "blob":
                paths.append(path)
        return sorted(paths)

    def read(self, paths: list[str]) -> dict[str, bytes]:
        """The blobs at ``paths`` (one ``git cat-file --batch`` call)."""
        for path in paths:
            if "\n" in path or path.startswith("/"):
                raise ManifestError(f"unsupported path {path!r}")
        request = "".join(f"{self.sha}:{path}\n" for path in paths).encode("utf-8")
        output = self._git("cat-file", "--batch", stdin=request)
        blobs: dict[str, bytes] = {}
        offset = 0
        for path in paths:
            end = output.index(b"\n", offset)
            header = output[offset:end].decode("utf-8", "replace").split(" ")
            offset = end + 1
            if header[-1] == "missing" or len(header) != 3:
                raise ManifestError(f"{path} does not exist at {self.sha}")
            if header[1] != "blob":
                raise ManifestError(f"{path} is a {header[1]} at {self.sha}, not a file")
            size = int(header[2])
            blobs[path] = output[offset : offset + size]
            offset += size + 1
        return blobs


def parse_revision_file(path: str, source: bytes) -> tuple[str, tuple[str, ...]]:
    """``(revision, down revisions)`` from the literal assignments of an Alembic script."""
    try:
        module = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise ManifestError(f"{path}: not valid Python ({exc.msg})") from exc
    values: dict[str, Any] = {}
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
            try:
                values[target.id] = ast.literal_eval(value)
            except ValueError as exc:
                raise ManifestError(f"{path}: {target.id} is not a literal") from exc
    revision = values.get("revision")
    if not isinstance(revision, str) or not revision:
        raise ManifestError(f"{path}: no literal 'revision' identifier")
    down = values.get("down_revision")
    if down is None:
        return revision, ()
    if isinstance(down, str):
        return revision, (down,)
    if isinstance(down, (tuple, list)) and all(isinstance(item, str) for item in down):
        return revision, tuple(down)
    raise ManifestError(f"{path}: down_revision must be None, a string or a sequence of strings")


def migration_summary(files: dict[str, bytes]) -> dict[str, Any]:
    """The single head and the number of revisions; refuses several heads or a broken chain."""
    parents: dict[str, tuple[str, ...]] = {}
    for path in sorted(files):
        revision, down = parse_revision_file(path, files[path])
        if revision in parents:
            raise ManifestError(f"duplicate migration revision {revision} ({path})")
        parents[revision] = down
    if not parents:
        raise ManifestError("no migration revisions found")
    referenced = {item for down in parents.values() for item in down}
    unknown = sorted(referenced - parents.keys())
    if unknown:
        raise ManifestError(f"down_revision names unknown revisions: {', '.join(unknown)}")
    heads = sorted(parents.keys() - referenced)
    if len(heads) != 1:
        raise ManifestError(
            f"expected exactly one migration head, found {len(heads)}: {', '.join(heads)}"
        )
    return {"migration_head": heads[0], "migration_count": len(parents)}


def is_shipped(relative: str) -> bool:
    """Whether the API image ships this path (relative to PRODUCT_DIR)."""
    parts = relative.split("/")
    return not (
        relative.startswith(UNSHIPPED_PREFIXES)
        or relative.endswith(".pyc")
        or any(part in UNSHIPPED_PARTS or part.startswith(".env") for part in parts)
    )


def files_sha256(digests: dict[str, str]) -> str:
    """Digest of a file set: the sorted lines ``<path>\\0<sha256 of content>\\n``."""
    listing = "".join(f"{path}\0{digests[path]}\n" for path in sorted(digests))
    return sha256_bytes(listing.encode("utf-8"))


def shipped_code(revision: GitRevision) -> dict[str, Any]:
    prefix = f"{PRODUCT_DIR}/"
    paths = revision.list_blobs(*(f"{prefix}{top}" for top in SHIPPED_PATHS), recursive=True)
    shipped = [path for path in paths if is_shipped(path.removeprefix(prefix))]
    digests = {
        path.removeprefix(prefix): sha256_bytes(blob)
        for path, blob in revision.read(shipped).items()
    }
    return {
        "root": PRODUCT_DIR,
        "paths": list(SHIPPED_PATHS),
        "excluded": [*UNSHIPPED_PREFIXES, *sorted(UNSHIPPED_PARTS), ".env*", "*.pyc"],
        "file_count": len(digests),
        "files_sha256": files_sha256(digests),
    }


def dockerfile_args(text: str) -> dict[str, str]:
    """Default value of each ``ARG NAME=value`` (first declaration wins)."""
    values: dict[str, str] = {}
    for name, value in _DOCKER_ARG.findall(text):
        values.setdefault(name, value.strip("\"'"))
    return values


def image_reference(reference: str) -> dict[str, Any]:
    digest = reference.partition("@")[2]
    pinned = bool(_DIGEST.fullmatch(digest))
    return {
        "reference": reference,
        "digest": digest if pinned else None,
        "pinned_by_digest": pinned,
    }


def _text(blob: bytes, path: str) -> str:
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{path} is not UTF-8") from exc


def _file(path: str, blob: bytes) -> dict[str, str]:
    return {"path": path, "sha256": sha256_bytes(blob)}


def static_inputs() -> list[str]:
    """Every fixed path ``generate`` reads (the migration scripts are listed at the revision)."""
    paths = set(DEPENDENCY_FILES.values()) | {PYTHON_VERSION_FILE}
    for dockerfile in IMAGES.values():
        paths |= {dockerfile, f"{dockerfile}.dockerignore"}
    return sorted(paths)


def reproducibility(build_inputs: dict[str, Any]) -> dict[str, Any]:
    pinned, not_pinned = list(ALWAYS_PINNED), list(NEVER_PINNED)
    for image, inputs in sorted(build_inputs.items()):
        for arg, base in sorted(inputs["base_images"].items()):
            entry = f"{image} {arg} {base['reference']}"
            (pinned if base["pinned_by_digest"] else not_pinned).append(
                entry if base["pinned_by_digest"] else f"{entry} (tag, not a digest)"
            )
    return {"level": "inputs", "pinned": pinned, "not_pinned": not_pinned}


def release_build_args(revision: GitRevision, version: str, profile: str) -> dict[str, str]:
    return {
        "SOURCE_SHA": revision.sha,
        "SOURCE_DATE_EPOCH": str(revision.commit_time),
        "SOURCE_DATE": iso_utc(revision.commit_time),
        "RELEASE_VERSION": version,
        "RELEASE_PROFILE": profile,
        "INSTALL_AI_LOCAL": PROFILES[profile],
    }


def _project(blobs: dict[str, bytes]) -> dict[str, Any]:
    try:
        project = tomllib.loads(_text(blobs[PYPROJECT], PYPROJECT)).get("project")
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{PYPROJECT}: {exc}") from exc
    if not isinstance(project, dict):
        raise ManifestError(f"{PYPROJECT}: no [project] table")
    for key in ("name", "version", "requires-python"):
        if not isinstance(project.get(key), str):
            raise ManifestError(f"{PYPROJECT}: [project] {key} is missing")
    return project


def build_manifest(
    revision: GitRevision,
    *,
    profile: str = "standard",
    images: dict[str, str] | None = None,
    validation: dict[str, str] | None = None,
    build_time: str | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ManifestError(f"profile must be one of {', '.join(PROFILES)}")
    migration_files = [
        path
        for path in revision.list_blobs(f"{MIGRATIONS_VERSIONS}/")
        if path.count("/") == MIGRATIONS_VERSIONS.count("/") + 1
        and _REVISION_FILE.match(path.rsplit("/", 1)[1])
    ]
    blobs = revision.read(static_inputs() + migration_files)
    project = _project(blobs)
    python = _text(blobs[PYTHON_VERSION_FILE], PYTHON_VERSION_FILE).strip()
    api_args = dockerfile_args(_text(blobs[API_DOCKERFILE], API_DOCKERFILE))
    for name in ("PYTHON_IMAGE", "UV_VERSION"):
        if name not in api_args:
            raise ManifestError(f"{API_DOCKERFILE}: no default for ARG {name}")
    base_image = image_reference(api_args["PYTHON_IMAGE"])
    if not base_image["reference"].startswith(f"python:{python}-"):
        raise ManifestError(
            f"{API_DOCKERFILE} PYTHON_IMAGE {base_image['reference']} does not match "
            f"{PYTHON_VERSION_FILE} ({python})"
        )
    build_inputs: dict[str, Any] = {}
    for name, dockerfile in IMAGES.items():
        ignore = f"{dockerfile}.dockerignore"
        args = dockerfile_args(_text(blobs[dockerfile], dockerfile))
        build_inputs[name] = {
            "dockerfile": _file(dockerfile, blobs[dockerfile]),
            "dockerignore": _file(ignore, blobs[ignore]),
            "base_images": {
                arg: image_reference(value) for arg, value in args.items() if arg.endswith("_IMAGE")
            },
        }
    build_inputs["api"]["build_args"] = release_build_args(revision, project["version"], profile)
    build_inputs["api"]["code"] = shipped_code(revision)
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "repository": SOURCE_REPOSITORY,
            "sha": revision.sha,
            "tree_sha": revision.tree_sha,
            "commit_time": iso_utc(revision.commit_time),
            "commit_time_epoch": revision.commit_time,
        },
        "application": {"name": project["name"], "version": project["version"]},
        "profile": profile,
        "build": {
            "time": build_time or iso_utc(revision.commit_time),
            "time_source": "argument" if build_time else "commit",
        },
        "runtime": {"python": python, "base_image": base_image, "uv": api_args["UV_VERSION"]},
        "dependencies": {key: _file(path, blobs[path]) for key, path in DEPENDENCY_FILES.items()},
        "schema": {
            "migrations_path": MIGRATIONS_VERSIONS,
            **migration_summary({path: blobs[path] for path in migration_files}),
        },
        "build_inputs": build_inputs,
        "artifacts": {"images": dict(sorted((images or {}).items()))},
        "validation": dict(sorted((validation or {}).items())),
        "compatibility": {"requires_python": project["requires-python"]},
        "reproducibility": reproducibility(build_inputs),
    }
    return {**body, "manifest_sha256": sha256_bytes(canonical_json(body).encode("ascii"))}


def _get(document: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def verify_documents(
    manifest: dict[str, Any],
    build_info: dict[str, Any],
    labels: dict[str, str] | None = None,
) -> list[str]:
    """Every mismatch between a manifest and an image's build-info (and OCI labels)."""
    problems = []
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != sha256_bytes(canonical_json(body).encode("ascii")):
        problems.append("manifest_sha256: does not match the manifest body (changed afterwards?)")
    pairs = [
        ("source.sha", "source.sha"),
        ("source.commit_time_epoch", "source.date_epoch"),
        ("application.version", "application.version"),
        ("profile", "profile"),
        ("dependencies.uv_lock.sha256", "dependencies.uv_lock_sha256"),
        ("dependencies.pyproject.sha256", "dependencies.pyproject_sha256"),
        ("schema.migration_head", "schema.migration_head"),
        ("build_inputs.api.code.files_sha256", "code.files_sha256"),
        ("build_inputs.api.code.file_count", "code.file_count"),
        ("runtime.base_image.reference", "build.base_image"),
        ("runtime.uv", "build.uv_version"),
    ]
    for manifest_key, info_key in pairs:
        expected, actual = _get(manifest, manifest_key), _get(build_info, info_key)
        if expected is None or expected != actual:
            problems.append(
                f"{manifest_key}: manifest={expected!r} build-info {info_key}={actual!r}"
            )
    python, running = _get(manifest, "runtime.python"), _get(build_info, "runtime.python_version")
    if (
        not isinstance(python, str)
        or not isinstance(running, str)
        or not (running == python or running.startswith(f"{python}."))
    ):
        problems.append(
            f"runtime.python: manifest={python!r} build-info python_version={running!r}"
        )
    if _get(build_info, "source.sha") == UNRELEASED_SHA:
        problems.append("build-info: the image was built without release arguments (unreleased)")
    if labels is not None:
        expected_labels = {
            "org.opencontainers.image.revision": _get(manifest, "source.sha"),
            "org.opencontainers.image.version": _get(manifest, "application.version"),
            "org.opencontainers.image.created": _get(manifest, "source.commit_time"),
            "org.opencontainers.image.base.name": _get(manifest, "runtime.base_image.reference"),
        }
        for label, expected in expected_labels.items():
            if labels.get(label) != expected:
                problems.append(f"label {label}: manifest={expected!r} image={labels.get(label)!r}")
    return problems


def _named(values: list[str], what: str, check: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        name, separator, value = item.partition("=")
        if not separator or not _NAME.fullmatch(name):
            raise ManifestError(f"--{what} must be NAME=VALUE with a lowercase name, got {item!r}")
        if name in result:
            raise ManifestError(f"--{what} {name} given twice")
        check(name, value)
        result[name] = value
    return result


def _check_digest(name: str, value: str) -> None:
    if not _DIGEST.fullmatch(value):
        raise ManifestError(f"--image {name}: expected sha256:<64 lowercase hex>, got {value!r}")


def _check_url(name: str, value: str) -> None:
    # Links only: a URL with credentials, a query or a fragment could carry a secret.
    parts = urlsplit(value)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or len(value) > 2048
    ):
        raise ManifestError(
            f"--validation {name}: expected an http(s) URL without credentials, query or fragment"
        )


def _check_time(value: str) -> str:
    try:
        if not _TIME.fullmatch(value):
            raise ValueError
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ManifestError(f"--build-time must be YYYY-MM-DDTHH:MM:SSZ, got {value!r}") from exc
    return value


def _load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read {what} {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ManifestError(f"{what} {path} is not a JSON object")
    return document


def _write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_manifest.py", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "build-args"):
        command = commands.add_parser(name)
        command.add_argument("--rev", required=True, help="commit to describe (Git objects only)")
        command.add_argument("--repo", type=Path, default=REPO_ROOT)
        command.add_argument("--profile", choices=sorted(PROFILES), default="standard")
        command.add_argument("--output", type=Path)
        if name == "generate":
            command.add_argument(
                "--image", action="append", default=[], metavar="NAME=sha256:DIGEST"
            )
            command.add_argument("--validation", action="append", default=[], metavar="NAME=URL")
            command.add_argument("--build-time", metavar="YYYY-MM-DDTHH:MM:SSZ")
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--build-info", required=True, type=Path)
    verify.add_argument("--image-labels", type=Path, help="JSON of docker inspect .Config.Labels")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            manifest = _load_json(args.manifest, "manifest")
            build_info = _load_json(args.build_info, "build-info")
            labels = _load_json(args.image_labels, "image labels") if args.image_labels else None
            problems = verify_documents(manifest, build_info, labels)
            if problems:
                print(f"MISMATCH: {args.build_info} does not match {args.manifest}:")
                for problem in problems:
                    print(f"  - {problem}")
                return 1
            print(
                f"OK: {args.build_info} matches manifest {manifest['manifest_sha256'][:16]} "
                f"(source {_get(manifest, 'source.sha')}, version "
                f"{_get(manifest, 'application.version')}, migration head "
                f"{_get(manifest, 'schema.migration_head')})"
            )
            return 0
        revision = GitRevision(args.repo, args.rev)
        if args.command == "build-args":
            project = _project(revision.read([PYPROJECT]))
            values = release_build_args(revision, project["version"], args.profile)
            _write("".join(f"{key}={value}\n" for key, value in values.items()), args.output)
            return 0
        manifest = build_manifest(
            revision,
            profile=args.profile,
            images=_named(args.image, "image", _check_digest),
            validation=_named(args.validation, "validation", _check_url),
            build_time=_check_time(args.build_time) if args.build_time else None,
        )
        _write(render(manifest), args.output)
        return 0
    except ManifestError as exc:
        print(f"release_manifest: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
