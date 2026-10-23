"""The backup tool's admin DSN route in ``compose.onprem.yaml`` (HOM-219).

``backend.backup`` reads its superuser / BYPASSRLS DSN from ``BACKUP_DATABASE_URL_FILE`` (a
file path) before it falls back to ``DATABASE_URL``. In Compose the host variable
``BACKUP_DATABASE_URL_FILE`` names that file, and only ``backup-tool`` mounts it read-only at
``/run/secrets/homayar-backup-database-url``. When the host variable is unset, the container
variable is empty, so the tool keeps today's fallback (an unset value must never become
``/dev/null``, which would read as an empty DSN).

Two kinds of check: the rendered config (``docker compose config``, skipped without docker
unless ``CI`` is set) and a static read of the YAML that always runs. Every value below is a
placeholder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPO_ROOT / "infra" / "compose"
COMPOSE_FILE = COMPOSE_DIR / "compose.onprem.yaml"

BACKUP_SERVICE = "backup-tool"
ADMIN_DSN_TARGET = "/run/secrets/homayar-backup-database-url"
PLACEHOLDER_PATH = "/ci-only/homayar/backup-database-url"
# Every profile of the file, so every service renders.
PROFILES = ("onprem", "egress", "ops", "restore", "monitoring", "migrate")

# The shape of the CI container-config env (.github/workflows/ci.yml), placeholders only.
CI_ENV = {
    "PUBLIC_ORIGIN": "http://localhost:8080",
    "CORS_ORIGINS": "http://localhost:8080",
    "TRUSTED_HOSTS": "localhost,127.0.0.1",
    "DATABASE_URL": "postgresql+psycopg://homayar:ci-only@postgres:5432/homayar",
    "REDIS_URL": "redis://:ci-only@redis:6379/0",
    "JWT_SECRET": "ci-only-jwt-secret-000000000000000000000000",
    "TOKEN_PEPPER": "ci-only-token-pepper-000000000000000000000",
    "STORAGE_ACCESS_KEY": "homayar-app",
    "STORAGE_SECRET_KEY": "ci-only-storage-secret-0000000000000000",
    "STORAGE_BUCKET": "homayar-private",
    "STORAGE_REGION": "us-east-1",
    "POSTGRES_DB": "homayar",
    "POSTGRES_USER": "homayar",
    "POSTGRES_PASSWORD": "ci-only-postgres",
    "REDIS_PASSWORD": "ci-only-redis",
    "MINIO_ROOT_USER": "homayar-root",
    "MINIO_ROOT_PASSWORD": "ci-only-minio-root",
    "NEO4J_PASSWORD": "ci-only-neo4j",
    "CONSOLE_WEB_IMAGE": "example.invalid/homayar/console-web@sha256:" + "2" * 64,
    "API_IMAGE": "example.invalid/homayar/api@sha256:" + "3" * 64,
}


def _require_compose_file() -> None:
    if not COMPOSE_FILE.exists():
        pytest.skip("infra/compose is not part of this checkout")


def _environment(service: dict[str, Any]) -> dict[str, Any]:
    environment = service.get("environment") or {}
    assert isinstance(environment, dict), environment
    return environment


def _volumes(service: dict[str, Any]) -> list[dict[str, Any]]:
    return [volume for volume in service.get("volumes") or [] if isinstance(volume, dict)]


def _leaks(services: dict[str, dict[str, Any]]) -> list[str]:
    """Every place outside backup-tool that carries the admin DSN key, path or mount."""
    found = []
    for name, service in services.items():
        environment = _environment(service)
        if "BACKUP_DATABASE_URL" in environment:
            found.append(f"{name}: env BACKUP_DATABASE_URL")
        if name == BACKUP_SERVICE:
            continue
        for key, value in environment.items():
            if key.startswith("BACKUP_DATABASE_URL"):
                found.append(f"{name}: env {key}")
            if any(marker in str(value) for marker in (PLACEHOLDER_PATH, ADMIN_DSN_TARGET)):
                found.append(f"{name}: env value of {key}")
        for volume in _volumes(service):
            if volume.get("source") == PLACEHOLDER_PATH or volume.get("target") == ADMIN_DSN_TARGET:
                found.append(f"{name}: volume {volume.get('source')}:{volume.get('target')}")
    return found


# --- rendered config ------------------------------------------------------------------------


def _render(env_file: Path, admin_file: str | None) -> dict[str, dict[str, Any]]:
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("CI"):
            pytest.fail("docker is required on CI to render the Compose config")
        pytest.skip("docker is not installed")

    # Keep the caller's PATH and docker config, but drop anything that would change the render.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BACKUP_", "COMPOSE_"))
    }
    env.update(CI_ENV)
    if admin_file is not None:
        env["BACKUP_DATABASE_URL_FILE"] = admin_file

    argv = [docker, "compose", "--file", str(COMPOSE_FILE), "--env-file", str(env_file)]
    for profile in PROFILES:
        argv += ["--profile", profile]
    argv += ["config", "--format", "json"]
    assert not any("://" in arg for arg in argv), argv

    completed = subprocess.run(  # noqa: S603 - fixed argv, values only through env
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    services = json.loads(completed.stdout)["services"]
    assert BACKUP_SERVICE in services
    return services


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, dict[str, Any]]]:
    _require_compose_file()
    # An empty env file, so a local infra/compose/.env cannot set the variable.
    env_file = tmp_path_factory.mktemp("compose") / "empty.env"
    env_file.write_text("", encoding="utf-8")
    return {
        "set": _render(env_file, PLACEHOLDER_PATH),
        "unset": _render(env_file, None),
    }


def _admin_mounts(service: dict[str, Any]) -> list[dict[str, Any]]:
    return [volume for volume in _volumes(service) if volume.get("target") == ADMIN_DSN_TARGET]


def test_every_service_of_the_file_renders(
    rendered: dict[str, dict[str, dict[str, Any]]],
) -> None:
    declared = set(yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"])
    assert set(rendered["set"]) == declared
    assert set(rendered["unset"]) == declared


def test_set_host_file_is_mounted_read_only_for_backup_tool(
    rendered: dict[str, dict[str, dict[str, Any]]],
) -> None:
    service = rendered["set"][BACKUP_SERVICE]

    assert _environment(service)["BACKUP_DATABASE_URL_FILE"] == ADMIN_DSN_TARGET
    [mount] = _admin_mounts(service)
    assert mount["type"] == "bind"
    assert mount["source"] == PLACEHOLDER_PATH
    assert mount["read_only"] is True


def test_unset_host_file_keeps_the_database_url_fallback(
    rendered: dict[str, dict[str, dict[str, Any]]],
) -> None:
    service = rendered["unset"][BACKUP_SERVICE]

    # Empty, not /dev/null: the tool treats empty as unset and uses DATABASE_URL.
    assert _environment(service)["BACKUP_DATABASE_URL_FILE"] == ""
    [mount] = _admin_mounts(service)
    assert mount["source"] == "/dev/null"
    assert mount["read_only"] is True


@pytest.mark.parametrize("case", ["set", "unset"])
def test_no_other_service_gets_the_admin_dsn_route(
    rendered: dict[str, dict[str, dict[str, Any]]], case: str
) -> None:
    assert _leaks(rendered[case]) == []


@pytest.mark.parametrize("case", ["set", "unset"])
def test_backup_tool_argv_holds_no_dsn(
    rendered: dict[str, dict[str, dict[str, Any]]], case: str
) -> None:
    service = rendered[case][BACKUP_SERVICE]
    argv = [*(service.get("entrypoint") or []), *(service.get("command") or [])]

    assert argv
    assert not any("://" in arg or "postgres" in arg for arg in argv), argv


def test_leak_check_denies_an_app_service_and_allows_backup_tool() -> None:
    allowed = {
        BACKUP_SERVICE: {
            "environment": {"BACKUP_DATABASE_URL_FILE": ADMIN_DSN_TARGET},
            "volumes": [{"type": "bind", "source": PLACEHOLDER_PATH, "target": ADMIN_DSN_TARGET}],
        },
        "api": {"environment": {"DATABASE_URL": "placeholder"}},
    }
    assert _leaks(allowed) == []

    denied = {
        "api": {"environment": {"BACKUP_DATABASE_URL_FILE": ADMIN_DSN_TARGET}},
        "worker": {"volumes": [{"type": "bind", "source": PLACEHOLDER_PATH, "target": "/x"}]},
        "scheduler": {"environment": {"OTHER": PLACEHOLDER_PATH}},
        BACKUP_SERVICE: {"environment": {"BACKUP_DATABASE_URL": "placeholder"}},
    }
    assert len(_leaks(denied)) == 5


# --- static YAML, no docker -----------------------------------------------------------------


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    _require_compose_file()
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_app_environment_anchor_has_no_admin_dsn_key(compose: dict[str, Any]) -> None:
    anchor = compose["x-app-environment"]

    assert [key for key in anchor if key.startswith("BACKUP_DATABASE_URL")] == []


def test_backup_tool_declares_the_secret_file_route(compose: dict[str, Any]) -> None:
    service = compose["services"][BACKUP_SERVICE]

    assert service["environment"]["BACKUP_DATABASE_URL_FILE"] == (
        f"${{BACKUP_DATABASE_URL_FILE:+{ADMIN_DSN_TARGET}}}"
    )
    assert f"${{BACKUP_DATABASE_URL_FILE:-/dev/null}}:{ADMIN_DSN_TARGET}:ro" in service["volumes"]
    assert "BACKUP_DATABASE_URL" not in service["environment"]


def test_no_other_service_declares_the_admin_dsn(compose: dict[str, Any]) -> None:
    for name, service in compose["services"].items():
        if name == BACKUP_SERVICE:
            continue
        keys = [key for key in service.get("environment") or {} if "BACKUP_DATABASE_URL" in key]
        volumes = [str(volume) for volume in service.get("volumes") or []]
        assert keys == [], name
        assert [volume for volume in volumes if ADMIN_DSN_TARGET in volume] == [], name
