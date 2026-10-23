"""Reproducible release inputs and the release manifest (WP01-T04/T06, HOM-28, HOM-29).

``infra/release/release_manifest.py`` reads every input of a revision from Git objects; these
tests run Git against this repository (CI checks out full history) and skip cleanly when no
Git metadata is available.  Working-tree independence and the multiple-head refusal run in a
synthetic repository built from the committed inputs, so the real checkout is never modified.
"""

from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from backend.config import SECRET_FIELDS
from backend.observability import release

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = REPO_ROOT / "infra" / "release" / "release_manifest.py"
HOMAYAR = REPO_ROOT / "Homayar"
DIGEST = "sha256:" + "ab" * 32
CI_URL = "https://github.com/Kohandezh/Homayar.com/actions/runs/1"
# Fixed author/committer for synthetic commits; no real identity is recorded anywhere.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "release-test",
    "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
    "GIT_COMMITTER_NAME": "release-test",
    "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-10-18T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-18T00:00:00Z",
}


# No signing prompt and no user hooks in synthetic repositories.
GIT_FLAGS = ("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_manifest", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rm = _load_tool()


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed git arguments, test repositories only
        ["git", "-C", str(repo), *GIT_FLAGS, *args],  # noqa: S607 - git from PATH
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _head() -> str | None:
    if shutil.which("git") is None:
        return None
    completed = subprocess.run(  # noqa: S603 - fixed git arguments
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "HEAD^{commit}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


HEAD = _head()
requires_git = pytest.mark.skipif(HEAD is None, reason="no git metadata for this checkout")


def _cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this interpreter running the release tool
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _manifest(rev: str, repo: Path = REPO_ROOT, **kwargs: Any) -> dict[str, Any]:
    return rm.build_manifest(rm.GitRevision(repo, rev), **kwargs)


def _committed(path: str) -> bytes:
    assert HEAD is not None
    return rm.GitRevision(REPO_ROOT, HEAD).read([path])[path]


def _clean(*pathspecs: str) -> bool:
    status = _git(REPO_ROOT, "status", "--porcelain", "--untracked-files=all", "--", *pathspecs)
    return status.strip() == ""


def _migrations_clean() -> bool:
    return _clean("Homayar/backend/migrations/versions")


def _code_clean() -> bool:
    """The shipped code in the working tree is HEAD's (tests may differ)."""
    return _clean("Homayar/alembic.ini", "Homayar/backend", ":(exclude)Homayar/backend/tests")


@pytest.fixture(scope="module")
def head_manifest() -> dict[str, Any]:
    if HEAD is None:
        pytest.skip("no git metadata for this checkout")
    return _manifest(HEAD, images={"api": DIGEST}, validation={"ci": CI_URL})


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """A one-commit repository holding exactly the inputs the manifest reads at HEAD."""
    if HEAD is None:
        pytest.skip("no git metadata for this checkout")
    revision = rm.GitRevision(REPO_ROOT, HEAD)
    shipped = revision.list_blobs("Homayar/alembic.ini", "Homayar/backend/", recursive=True)
    paths = rm.static_inputs() + [p for p in shipped if not p.startswith("Homayar/backend/tests/")]
    repo = tmp_path / "repo"
    for path, blob in revision.read(paths).items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    _git(repo, "add", "--all")
    _git(repo, "commit", "--quiet", "--message", "release inputs", env=GIT_ENV)
    return repo


def _build_info_for(manifest: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    """build-info.json as the image of ``manifest``'s revision would write it."""
    if not _code_clean():
        pytest.skip("uncommitted backend changes: the working tree is not HEAD")
    python = manifest["runtime"]["python"]
    if not platform.python_version().startswith(f"{python}."):
        pytest.skip(f"interpreter {platform.python_version()} is not the release Python {python}")
    lock, pyproject = tmp_path / "uv.lock", tmp_path / "pyproject.toml"
    lock.write_bytes(_committed(rm.DEPENDENCY_FILES["uv_lock"]))
    pyproject.write_bytes(_committed(rm.PYPROJECT))
    return release.build_info_document(
        source_sha=manifest["source"]["sha"],
        source_date_epoch=manifest["source"]["commit_time_epoch"],
        profile=manifest["profile"],
        lock_path=lock,
        pyproject_path=pyproject,
        base_image=manifest["runtime"]["base_image"]["reference"],
        uv_version=manifest["runtime"]["uv"],
    )


# --- determinism and working-tree independence -------------------------------------------


@requires_git
def test_generate_twice_is_byte_identical() -> None:
    args = ("generate", "--rev", "HEAD", "--image", f"api={DIGEST}", "--validation", f"ci={CI_URL}")
    first, second = _cli(*args), _cli(*args)
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    manifest = json.loads(first.stdout)
    assert manifest["source"]["sha"] == HEAD
    assert manifest["artifacts"] == {"images": {"api": DIGEST}}
    assert manifest["validation"] == {"ci": CI_URL}
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    assert manifest["manifest_sha256"] == rm.sha256_bytes(rm.canonical_json(body).encode())
    # No wall clock: the build time is the commit time unless given.
    assert manifest["build"] == {"time": manifest["source"]["commit_time"], "time_source": "commit"}
    overridden = _cli(*args, "--build-time", "2030-01-01T00:00:00Z")
    assert json.loads(overridden.stdout)["build"]["time"] == "2030-01-01T00:00:00Z"
    assert json.loads(overridden.stdout)["manifest_sha256"] != manifest["manifest_sha256"]


@requires_git
def test_manifest_describes_the_revision(head_manifest: dict[str, Any]) -> None:
    project = tomllib.loads(_committed(rm.PYPROJECT).decode())["project"]
    assert head_manifest["schema_version"] == rm.SCHEMA_VERSION
    tree = _git(REPO_ROOT, "rev-parse", f"{HEAD}^{{tree}}").strip()
    assert head_manifest["source"]["tree_sha"] == tree
    assert head_manifest["application"] == {"name": project["name"], "version": project["version"]}
    assert head_manifest["compatibility"] == {"requires_python": project["requires-python"]}
    assert head_manifest["runtime"]["python"] == _committed(rm.PYTHON_VERSION_FILE).decode().strip()
    assert head_manifest["profile"] == "standard"
    assert set(head_manifest["build_inputs"]) == set(rm.IMAGES)
    for key, path in rm.DEPENDENCY_FILES.items():
        assert head_manifest["dependencies"][key] == {
            "path": path,
            "sha256": rm.sha256_bytes(_committed(path)),
        }
    args = head_manifest["build_inputs"]["api"]["build_args"]
    assert args["SOURCE_SHA"] == HEAD
    assert args["SOURCE_DATE_EPOCH"] == str(head_manifest["source"]["commit_time_epoch"])
    assert args["SOURCE_DATE"] == head_manifest["source"]["commit_time"]
    assert args["RELEASE_VERSION"] == project["version"]
    printed = _cli("build-args", "--rev", "HEAD")
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout == "".join(f"{key}={value}\n" for key, value in args.items())


def _dockerfile_text(image: str) -> str:
    return (REPO_ROOT / rm.IMAGES[image]).read_text(encoding="utf-8")


def _base_args(dockerfile: str) -> dict[str, str]:
    return {
        arg: value
        for arg, value in rm.dockerfile_args(dockerfile).items()
        if arg.endswith("_IMAGE")
    }


@pytest.mark.parametrize("image", sorted(rm.IMAGES))
def test_every_base_image_is_pinned_by_digest(image: str) -> None:
    dockerfile = _dockerfile_text(image)
    bases = _base_args(dockerfile)
    assert bases, f"{rm.IMAGES[image]} declares no *_IMAGE ARG"
    for arg, value in bases.items():
        base = rm.image_reference(value)
        assert base["pinned_by_digest"], (image, arg, base)
        # The tag stays readable in front of the digest (tag@sha256:...).
        assert re.fullmatch(r"[a-z0-9./-]+:[\w.-]+@sha256:[0-9a-f]{64}", value), (image, arg)
    assert _unpinned_stage_sources(dockerfile) == [], image


def _unpinned_stage_sources(dockerfile: str) -> list[str]:
    """Each FROM line that does not start from a pinned base-image ARG or an earlier stage."""
    # The base images the manifest records for this Dockerfile, kept only when pinned.
    pinned = {
        arg for arg, value in _base_args(dockerfile).items() if rm.image_reference(value)["digest"]
    }
    stages: set[str] = set()
    problems = []
    for line in dockerfile.splitlines():
        words = line.split()
        if not words or words[0].upper() != "FROM":
            continue
        # FROM [--platform=<platform>]... <source> [AS <name>]
        rest = list(itertools.dropwhile(lambda word: word.startswith("--"), words[1:]))
        if len(rest) not in (1, 3) or (len(rest) == 3 and rest[1].upper() != "AS"):
            problems.append(f"cannot parse: {line.strip()}")
            continue
        source = rest[0]
        variable = re.fullmatch(r"\$\{(\w+)\}|\$(\w+)", source)
        name = variable and (variable.group(1) or variable.group(2))
        # Stage names are case-insensitive in Docker.
        if source.lower() not in stages and name not in pinned:
            problems.append(f"not a pinned base-image ARG or an earlier stage: {line.strip()}")
        if len(rest) == 3:
            stages.add(rest[2].lower())
    return problems


PINNED_NODE = f"ARG NODE_IMAGE=node:22-alpine@{DIGEST}\n"


@pytest.mark.parametrize(
    "dockerfile",
    [
        f"{PINNED_NODE}FROM --platform=$BUILDPLATFORM alpine:3.22 AS tools\n",
        f"{PINNED_NODE}from alpine:3.22 as tools\n",
        f"{PINNED_NODE}FROM ${{NODE_IMAGE}} AS build\nFROM alpine:3.22\n",
        # An ARG without a default is not in the manifest's base_images.
        "ARG NODE_IMAGE\nFROM ${NODE_IMAGE}\n",
        "ARG NODE_IMAGE=node:22-alpine\nFROM ${NODE_IMAGE}\n",
        f"{PINNED_NODE}FROM ${{OTHER_IMAGE}}\n",
        # A stage name is only known after the FROM that defines it.
        f"{PINNED_NODE}FROM build\nFROM ${{NODE_IMAGE}} AS build\n",
        f"{PINNED_NODE}FROM\n",
        f"{PINNED_NODE}FROM ${{NODE_IMAGE}} AS build extra\n",
        f"{PINNED_NODE}FROM ${{NODE_IMAGE}} \\\n  AS build\n",
    ],
)
def test_stage_check_refuses_unpinned_sources(dockerfile: str) -> None:
    assert len(_unpinned_stage_sources(dockerfile)) == 1, dockerfile


@pytest.mark.parametrize(
    "dockerfile",
    [
        f"{PINNED_NODE}FROM ${{NODE_IMAGE}}\n",
        f"{PINNED_NODE}FROM $NODE_IMAGE\n",
        f"{PINNED_NODE}FROM --platform=$BUILDPLATFORM ${{NODE_IMAGE}} AS build\nFROM build\n",
        f"{PINNED_NODE}from ${{NODE_IMAGE}} as Build\nFROM build AS runtime\n",
        f"{PINNED_NODE}# FROM alpine:3.22 in a comment is not a stage\nFROM ${{NODE_IMAGE}}\n",
    ],
)
def test_stage_check_accepts_pinned_sources_and_earlier_stages(dockerfile: str) -> None:
    assert _unpinned_stage_sources(dockerfile) == [], dockerfile


def test_api_base_image_matches_the_python_version() -> None:
    dockerfile = _dockerfile_text("api")
    base = rm.image_reference(rm.dockerfile_args(dockerfile)["PYTHON_IMAGE"])
    python = (REPO_ROOT / rm.PYTHON_VERSION_FILE).read_text(encoding="utf-8").strip()
    assert base["reference"].startswith(f"python:{python}-")
    # A stale lock must fail the image build, not be installed.
    assert "uv sync --frozen" not in dockerfile
    assert dockerfile.count("uv sync --locked") == 2


def _compose_build_args(text: str) -> dict[str, dict[str, tuple[str, str]]]:
    """Dockerfile -> {ARG: (variable, default)} of each ``"${VAR:-default}"`` build argument."""
    result: dict[str, dict[str, tuple[str, str]]] = {}
    dockerfile, args_indent = None, None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if args_indent is not None and line.strip() and indent <= args_indent:
            args_indent = None
        if match := re.fullmatch(r"\s*dockerfile:\s*(\S+)\s*", line):
            dockerfile = match.group(1)
            result.setdefault(dockerfile, {})
        elif dockerfile and re.fullmatch(r"\s*args:\s*", line):
            args_indent = indent
        elif dockerfile and args_indent is not None:
            value = re.fullmatch(r'\s*(\w+):\s*"\$\{(\w+):-([^}]*)\}"\s*', line)
            if value:
                result[dockerfile][value.group(1)] = (value.group(2), value.group(3))
    return result


def test_compose_build_arg_defaults_are_the_dockerfile_pins() -> None:
    compose = (REPO_ROOT / "infra/compose/compose.onprem.yaml").read_text(encoding="utf-8")
    build_args = _compose_build_args(compose)
    for image, dockerfile in rm.IMAGES.items():
        assert dockerfile in build_args, f"compose.onprem.yaml does not build {image}"
        for arg, default in _base_args(_dockerfile_text(image)).items():
            # Same name, so an operator can still point at a mirror; same default as the pin.
            assert build_args[dockerfile].get(arg) == (arg, default), (image, arg)


def _with_base_images(manifest: dict[str, Any], **references: str) -> dict[str, Any]:
    """``manifest`` with ``image.ARG=reference`` base images and a valid checksum again."""
    changed = copy.deepcopy(manifest)
    for key, reference in references.items():
        image, arg = key.split(".")
        changed["build_inputs"][image]["base_images"][arg] = rm.image_reference(reference)
    body = {key: value for key, value in changed.items() if key != "manifest_sha256"}
    changed["manifest_sha256"] = rm.sha256_bytes(rm.canonical_json(body).encode("ascii"))
    return changed


PINNED_BASES = {
    "console-web.NODE_IMAGE": "node:22-alpine@" + DIGEST,
    "console-web.NGINX_IMAGE": "nginx:1.28-alpine@" + DIGEST,
    "egress-proxy.ALPINE_IMAGE": "alpine:3.22@" + DIGEST,
}


@requires_git
@pytest.mark.parametrize(
    "unpinned",
    [
        "alpine:3.22",
        "alpine:3.22@sha256:" + "AB" * 32,
        "alpine:3.22@sha256:abc123",
        "alpine:3.22@" + "ab" * 32,
    ],
)
def test_verify_refuses_an_unpinned_base_image(
    head_manifest: dict[str, Any], tmp_path: Path, unpinned: str
) -> None:
    build_info = _build_info_for(head_manifest, tmp_path)
    pinned = _with_base_images(head_manifest, **PINNED_BASES)
    assert rm.verify_documents(pinned, build_info) == []
    # One unpinned base among pinned ones; the checksum is valid, so only the pin check fails.
    denied = _with_base_images(pinned, **{"egress-proxy.ALPINE_IMAGE": unpinned})
    problems = rm.verify_documents(denied, build_info)
    assert len(problems) == 1, problems
    assert problems[0].startswith("build_inputs.egress-proxy.base_images.ALPINE_IMAGE:"), problems
    assert unpinned in problems[0]
    manifest_path, info_path = tmp_path / "manifest.json", tmp_path / "build-info.json"
    info_path.write_text(release.canonical_json(build_info))
    args = ("verify", "--manifest", str(manifest_path), "--build-info", str(info_path))
    manifest_path.write_text(rm.render(denied))
    refused = _cli(*args)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "egress-proxy" in refused.stdout and "ALPINE_IMAGE" in refused.stdout
    manifest_path.write_text(rm.render(pinned))
    allowed = _cli(*args)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def test_verify_refuses_a_base_image_that_claims_a_pin() -> None:
    claimed = {"reference": "alpine:3.22", "digest": DIGEST, "pinned_by_digest": True}
    manifest = {"build_inputs": {"egress-proxy": {"base_images": {"ALPINE_IMAGE": claimed}}}}
    problems = rm.base_image_problems(manifest)
    assert problems == [
        "build_inputs.egress-proxy.base_images.ALPINE_IMAGE: not pinned by digest ('alpine:3.22')"
    ]
    assert rm.base_image_problems({"build_inputs": {"api": {"base_images": {}}}}) == [
        "build_inputs.api.base_images: no base image recorded"
    ]
    assert rm.base_image_problems({}) == ["build_inputs: missing"]


@requires_git
def test_manifest_records_every_base_image_as_pinned(head_manifest: dict[str, Any]) -> None:
    for image, inputs in head_manifest["build_inputs"].items():
        assert inputs["base_images"], image
        for arg, base in inputs["base_images"].items():
            assert base["pinned_by_digest"] is True, (image, arg, base)
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", base["digest"]), (image, arg)
            assert base["reference"].endswith("@" + base["digest"]), (image, arg)
    reproducibility = head_manifest["reproducibility"]
    assert not [item for item in reproducibility["not_pinned"] if "(tag, not a digest)" in item]
    for name in ("api PYTHON_IMAGE", "console-web NODE_IMAGE", "console-web NGINX_IMAGE"):
        assert any(item.startswith(f"{name} ") for item in reproducibility["pinned"]), name
    assert any(item.startswith("egress-proxy ALPINE_IMAGE ") for item in reproducibility["pinned"])
    assert any("apk" in item and "squid" in item for item in reproducibility["not_pinned"])
    assert rm.base_image_problems(head_manifest) == []


@requires_git
def test_working_tree_changes_do_not_change_the_manifest(synthetic_repo: Path) -> None:
    before = rm.render(_manifest("HEAD", synthetic_repo))
    # A dirty edit of every kind of input, an untracked second migration head and stray files.
    (synthetic_repo / rm.DEPENDENCY_FILES["uv_lock"]).write_text("tampered\n", encoding="utf-8")
    (synthetic_repo / rm.API_DOCKERFILE).write_text("ARG PYTHON_IMAGE=evil:latest\n")
    pyproject = synthetic_repo / rm.PYPROJECT
    pyproject.write_text(pyproject.read_text().replace('version = "', 'version = "9'))
    (synthetic_repo / rm.MIGRATIONS_VERSIONS / "ffff_untracked.py").write_text(
        "revision = 'ffff'\ndown_revision = None\n"
    )
    (synthetic_repo / "Homayar" / ".env").write_text("JWT_SECRET=" + secrets.token_hex(16))
    (synthetic_repo / "untracked.txt").write_text("noise")
    assert _git(synthetic_repo, "status", "--porcelain").strip()
    after = rm.render(_manifest("HEAD", synthetic_repo))
    assert after == before


@requires_git
def test_generate_follows_the_requested_revision(synthetic_repo: Path) -> None:
    first = _manifest("HEAD", synthetic_repo)
    lock = synthetic_repo / rm.DEPENDENCY_FILES["uv_lock"]
    lock.write_bytes(lock.read_bytes() + b"\n")
    _git(synthetic_repo, "commit", "--quiet", "--all", "--message", "lock", env=GIT_ENV)
    second = _manifest("HEAD", synthetic_repo)
    assert second["source"]["sha"] != first["source"]["sha"]
    assert second["dependencies"]["uv_lock"] != first["dependencies"]["uv_lock"]
    assert _manifest("HEAD~1", synthetic_repo) == first


# --- migration head --------------------------------------------------------------------


@requires_git
def test_manifest_migration_head_is_alembics_head(head_manifest: dict[str, Any]) -> None:
    if not _migrations_clean():
        pytest.skip("uncommitted migration changes: the working tree is not HEAD")
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory(str(release.MIGRATIONS_DIR)).get_heads()
    revisions = list(ScriptDirectory(str(release.MIGRATIONS_DIR)).walk_revisions())
    assert heads == [head_manifest["schema"]["migration_head"]]
    assert release.code_migration_head() == head_manifest["schema"]["migration_head"]
    assert head_manifest["schema"]["migration_count"] == len(revisions)


@requires_git
def test_multiple_heads_are_refused(synthetic_repo: Path) -> None:
    branch = synthetic_repo / rm.MIGRATIONS_VERSIONS / "eeee_second_branch.py"
    branch.write_text("revision: str = 'eeee'\ndown_revision: str | None = 'missing00000'\n")
    _git(synthetic_repo, "add", "--all")
    _git(synthetic_repo, "commit", "--quiet", "--message", "broken chain", env=GIT_ENV)
    with pytest.raises(rm.ManifestError, match="unknown revisions: missing00000"):
        _manifest("HEAD", synthetic_repo)
    # A second independent branch: two heads, which Alembic's upgrade "head" would refuse.
    branch.write_text("revision: str = 'eeee'\ndown_revision: str | None = None\n")
    _git(synthetic_repo, "commit", "--quiet", "--all", "--message", "second head", env=GIT_ENV)
    with pytest.raises(rm.ManifestError, match="exactly one migration head, found 2"):
        _manifest("HEAD", synthetic_repo)
    result = _cli("generate", "--rev", "HEAD", "--repo", str(synthetic_repo))
    assert result.returncode == 2
    assert "found 2" in result.stderr and result.stdout == ""


def test_migration_parser() -> None:
    files = {
        "a.py": b"revision = 'a'\ndown_revision = None\n",
        "b.py": b"revision: str = 'b'\ndown_revision: str | None = 'a'\n",
        "c.py": b"revision = 'c'\ndown_revision = 'a'\n",
        "m.py": b"revision = 'm'\ndown_revision = ('b', 'c')\n",
    }
    assert rm.migration_summary(files) == {"migration_head": "m", "migration_count": 4}
    with pytest.raises(rm.ManifestError, match="found 2: b, c"):
        rm.migration_summary({key: files[key] for key in ("a.py", "b.py", "c.py")})
    with pytest.raises(rm.ManifestError, match="duplicate"):
        rm.migration_summary({**files, "d.py": files["a.py"]})
    with pytest.raises(rm.ManifestError, match="no literal 'revision'"):
        rm.migration_summary({"x.py": b"down_revision = None\n"})
    with pytest.raises(rm.ManifestError, match="not a literal"):
        rm.migration_summary({"x.py": b"revision = make_id()\n"})


# --- shipped code ----------------------------------------------------------------------


def test_shipped_code_rule_is_the_same_in_the_image_and_the_manifest() -> None:
    samples = [
        "alembic.ini",
        "backend/main.py",
        "backend/migrations/versions/x.py",
        "backend/providers/manifests/builtin.json",
        "backend/tests/test_x.py",
        "backend/__pycache__/main.cpython-312.pyc",
        "backend/x.pyc",
        "backend/.env",
        "backend/routers/.env.local",
        "backend/.pytest_cache/v/cache",
        "backend/.venv/bin/python",
    ]
    assert [rm.is_shipped(path) for path in samples] == [
        release.is_shipped(path) for path in samples
    ]
    assert [path for path in samples if rm.is_shipped(path)] == samples[:4]
    assert rm.SHIPPED_PATHS == release.SHIPPED_PATHS
    ignore = (REPO_ROOT / "infra/docker/api.Dockerfile.dockerignore").read_text(encoding="utf-8")
    for path in ("Homayar/alembic.ini", "Homayar/backend/**", "Homayar/backend/tests/**"):
        assert path in ignore.replace("!", "")


@requires_git
def test_code_digest_of_the_working_tree_is_the_revisions(head_manifest: dict[str, Any]) -> None:
    if not _code_clean():
        pytest.skip("uncommitted backend changes: the working tree is not HEAD")
    digests = release.code_file_digests()
    code = head_manifest["build_inputs"]["api"]["code"]
    assert code["file_count"] == len(digests)
    assert code["files_sha256"] == release.files_sha256(digests)


# --- build-info and verify -------------------------------------------------------------


def test_build_info_document_is_deterministic(tmp_path: Path) -> None:
    lock, pyproject = tmp_path / "uv.lock", tmp_path / "pyproject.toml"
    lock.write_bytes((HOMAYAR / "uv.lock").read_bytes())
    pyproject.write_bytes((HOMAYAR / "pyproject.toml").read_bytes())
    arguments: dict[str, Any] = {
        "source_sha": "1" * 40,
        "source_date_epoch": 1_792_000_000,
        "profile": "standard",
        "lock_path": lock,
        "pyproject_path": pyproject,
        "base_image": "python:3.12-slim-bookworm@" + DIGEST,
        "uv_version": "0.11.24",
    }
    first = release.canonical_json(release.build_info_document(**arguments))
    assert first == release.canonical_json(release.build_info_document(**arguments))
    document = json.loads(first)
    assert document["source"] == {"sha": "1" * 40, "date_epoch": 1_792_000_000, "released": True}
    assert document["build"] == {"base_image": arguments["base_image"], "uv_version": "0.11.24"}
    assert document["dependencies"]["uv_lock_sha256"] == release.sha256_file(lock)
    assert document["code"]["file_count"] == len(release.code_file_digests())
    unreleased = release.build_info_document(**{**arguments, "source_sha": release.UNRELEASED_SHA})
    assert unreleased["source"]["released"] is False
    with pytest.raises(ValueError, match="40-character"):
        release.build_info_document(**{**arguments, "source_sha": "HEAD"})
    with pytest.raises(ValueError, match="profile"):
        release.build_info_document(**{**arguments, "profile": "custom"})


@pytest.mark.parametrize(
    ("sha", "expected"),
    [("2" * 40, "2" * 40), (release.UNRELEASED_SHA, "unreleased"), (None, "unknown")],
)
def test_runtime_identity_marks_unreleased_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sha: str | None, expected: str
) -> None:
    path = tmp_path / "build-info.json"
    if sha is not None:
        path.write_text(json.dumps({"source": {"sha": sha}, "profile": "standard"}))
    uncached = release.load_build_info.__wrapped__
    monkeypatch.setattr(release, "load_build_info", lambda: uncached(str(path)))
    identity = release.runtime_identity()
    assert identity["source_sha"] == expected
    assert identity["profile"] == ("unknown" if sha is None else "standard")


@requires_git
def test_verify_passes_on_a_match_and_reports_each_mismatch(
    head_manifest: dict[str, Any], tmp_path: Path
) -> None:
    build_info = _build_info_for(head_manifest, tmp_path)
    assert rm.verify_documents(head_manifest, build_info) == []
    cases = {
        "source.sha": ("source", "sha", "3" * 40),
        "dependencies.uv_lock.sha256": ("dependencies", "uv_lock_sha256", "0" * 64),
        "schema.migration_head": ("schema", "migration_head", "000000000000"),
        "application.version": ("application", "version", "9.9.9"),
        "build_inputs.api.code.files_sha256": ("code", "files_sha256", "0" * 64),
        "runtime.base_image.reference": ("build", "base_image", "python:3.12-slim-trixie"),
    }
    for field, (section, key, value) in cases.items():
        changed = copy.deepcopy(build_info)
        changed[section][key] = value
        problems = rm.verify_documents(head_manifest, changed)
        assert [problem.split(":")[0] for problem in problems] == [field], problems
    unreleased = copy.deepcopy(build_info)
    unreleased["source"]["sha"] = release.UNRELEASED_SHA
    problems = rm.verify_documents(head_manifest, unreleased)
    assert any("unreleased" in problem for problem in problems)
    edited = copy.deepcopy(head_manifest)
    edited["schema"]["migration_head"] = "000000000000"
    assert rm.verify_documents(edited, build_info)[0].startswith("manifest_sha256:")


@requires_git
def test_verify_cli_and_image_labels(head_manifest: dict[str, Any], tmp_path: Path) -> None:
    build_info = _build_info_for(head_manifest, tmp_path)
    manifest_path, info_path = tmp_path / "manifest.json", tmp_path / "build-info.json"
    labels_path = tmp_path / "labels.json"
    manifest_path.write_text(rm.render(head_manifest))
    info_path.write_text(release.canonical_json(build_info))
    labels = {
        "org.opencontainers.image.revision": head_manifest["source"]["sha"],
        "org.opencontainers.image.version": head_manifest["application"]["version"],
        "org.opencontainers.image.created": head_manifest["source"]["commit_time"],
        "org.opencontainers.image.base.name": head_manifest["runtime"]["base_image"]["reference"],
        "org.opencontainers.image.title": "homayar-api",
    }
    labels_path.write_text(json.dumps(labels))
    args = ("verify", "--manifest", str(manifest_path), "--build-info", str(info_path))
    ok = _cli(*args, "--image-labels", str(labels_path))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ok.stdout.startswith("OK:")
    labels_path.write_text(json.dumps({**labels, "org.opencontainers.image.revision": "0" * 40}))
    bad_label = _cli(*args, "--image-labels", str(labels_path))
    assert bad_label.returncode == 1
    assert "label org.opencontainers.image.revision" in bad_label.stdout
    build_info["dependencies"]["uv_lock_sha256"] = "f" * 64
    info_path.write_text(release.canonical_json(build_info))
    mismatch = _cli(*args)
    assert mismatch.returncode == 1
    assert "MISMATCH" in mismatch.stdout
    assert f"build-info dependencies.uv_lock_sha256='{'f' * 64}'" in mismatch.stdout


# --- locks and secrets -----------------------------------------------------------------


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_requirements_txt_lists_exactly_the_locked_runtime_packages() -> None:
    lock = tomllib.loads((HOMAYAR / "uv.lock").read_text(encoding="utf-8"))
    packages = {_normalise(package["name"]): package for package in lock["package"]}
    project = tomllib.loads((HOMAYAR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    root = _normalise(project["name"])
    # Closure of the runtime dependencies with their requested extras; no dev group, no
    # optional extra of the project itself (``uv export --no-dev``).
    pending = [(dependency, ()) for dependency in packages[root].get("dependencies", [])]
    wanted: set[str] = set()
    seen: set[tuple[str, tuple[str, ...]]] = set()
    while pending:
        dependency, _ = pending.pop()
        name, extras = _normalise(dependency["name"]), tuple(dependency.get("extra", ()))
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        package = packages[name]
        wanted.add(f"{name}=={package['version']}")
        follow = list(package.get("dependencies", []))
        for extra in extras:
            follow += package.get("optional-dependencies", {}).get(extra, [])
        pending += [(item, ()) for item in follow]
    listed = set()
    for line in (HOMAYAR / "backend" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith((" ", "#")):
            continue
        requirement = line.split(";", 1)[0].strip()
        name, version = requirement.split("==", 1)
        listed.add(f"{_normalise(name)}=={version}")
    assert listed == wanted, {"missing": sorted(wanted - listed), "extra": sorted(listed - wanted)}


@requires_git
def test_manifest_holds_no_secret(head_manifest: dict[str, Any]) -> None:
    canary = "release-canary-" + secrets.token_hex(16)
    environment = {**os.environ, **{name.upper(): canary for name in SECRET_FIELDS}}
    result = _cli("generate", "--rev", "HEAD", env=environment)
    assert result.returncode == 0, result.stderr
    for text in (result.stdout, rm.render(head_manifest)):
        assert canary not in text
        lowered = text.lower()
        for name in SECRET_FIELDS:
            assert name not in lowered, name
        # Nothing env-like (KEY=value) and no Git identity (e-mail address).
        assert not re.search(r"\b[A-Z][A-Z0-9_]{2,}=\S", text)
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)


def test_arguments_that_could_carry_secrets_are_refused() -> None:
    for url in (
        "https://user:pass@ci.example.invalid/run/1",
        "https://ci.example.invalid/run/1?token=abc",
        "https://ci.example.invalid/run/1#frag",
        "file:///etc/passwd",
    ):
        with pytest.raises(rm.ManifestError, match="without credentials"):
            rm._named([f"ci={url}"], "validation", rm._check_url)
    with pytest.raises(rm.ManifestError, match="sha256"):
        rm._named(["api=latest"], "image", rm._check_digest)
    with pytest.raises(rm.ManifestError, match="twice"):
        rm._named([f"api={DIGEST}", f"api={DIGEST}"], "image", rm._check_digest)
    with pytest.raises(rm.ManifestError, match="build-time"):
        rm._check_time("2026-10-18 00:00:00")
    with pytest.raises(rm.ManifestError, match="invalid revision"):
        rm.GitRevision(REPO_ROOT, "--output=/tmp/x")
