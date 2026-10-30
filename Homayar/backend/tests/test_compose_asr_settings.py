"""The ASR settings group of ``Settings`` in the Compose app environment (HOM-343).

HOM-39 added the ASR profile and language-policy settings to ``Settings``
(``asr_profile``, ``asr_allowed_languages``, ``asr_default_language``,
``asr_language_min_probability``). The Compose files passed only ``ASR_PROVIDER``, so a
Compose install could not set a Whisper profile or the language policy and the code always
used its defaults. Both ``x-app-environment`` anchors now carry every setting whose name
starts with ``asr_``, with the code default after ``:-``, so an unset variable keeps
today's behaviour.

The names are read from ``Settings.model_fields`` at test time, so a future ``asr_*``
setting that is not added to Compose fails this file. Two kinds of check: the rendered
config (``docker compose config``, skipped without docker unless ``CI`` is set) and a
static read of the YAML that always runs. Every value below is a placeholder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.config import Settings
from backend.providers.factory import create_asr_provider
from backend.providers.whisper import LocalWhisperProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPO_ROOT / "infra" / "compose"
ONPREM_FILE = COMPOSE_DIR / "compose.onprem.yaml"
APP_SERVER_FILE = COMPOSE_DIR / "compose.onprem.app-server.yaml"
CLOUD_FILE = COMPOSE_DIR / "compose.cloud.yaml"

APP_SERVICES = ("api", "worker", "scheduler")

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

# Cloud needs TLS on every hop (the cloud step of ci.yml), placeholders only.
CLOUD_ENV = {
    "DATABASE_URL": (
        "postgresql+psycopg://homayar:ci-only@db.example.invalid:5432/homayar?sslmode=verify-full"
    ),
    "REDIS_URL": "rediss://:ci-only@redis.example.invalid:6380/0?ssl_cert_reqs=required",
}

# The app-server overlay adds required variables of its own (the migration owner).
APP_SERVER_ENV = {
    "MIGRATION_DATABASE_URL": "postgresql+psycopg://homayar:ci-only@postgres:5432/homayar",
}

# AC3, amendment A1: the default language stays in the allowed list.
ASR_ENV_FILE = {
    "ASR_PROFILE": "cpu-small",
    "ASR_ALLOWED_LANGUAGES": "fa,en,ar",
    "ASR_DEFAULT_LANGUAGE": "en",
    "ASR_LANGUAGE_MIN_PROBABILITY": "0.7",
}


def _require_compose_files() -> None:
    if not (ONPREM_FILE.exists() and CLOUD_FILE.exists()):
        pytest.skip("infra/compose is not part of this checkout")


def asr_env_defaults() -> dict[str, str]:
    """Every ``asr_*`` field of ``Settings`` as ``ENV_NAME -> str(code default)``."""
    defaults = {
        name.upper(): str(field.default)
        for name, field in Settings.model_fields.items()
        if name.startswith("asr_")
    }
    assert "ASR_PROVIDER" in defaults, "the ASR settings group of Settings is missing"
    return defaults


def _anchor_default(anchor: dict[str, Any], env_name: str) -> str | None:
    """The default after ``:-`` in an anchor entry, or None when it is not that shape."""
    value = anchor.get(env_name)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\$\{([A-Z0-9_]+):-(.*)\}", value)
    if match is None or match.group(1) != env_name:
        return None
    return match.group(2)


def _environment(service: dict[str, Any]) -> dict[str, Any]:
    environment = service.get("environment") or {}
    assert isinstance(environment, dict), environment
    return environment


# --- rendered config ------------------------------------------------------------------------


def _render(
    files: tuple[Path, ...], profiles: tuple[str, ...], extra_env: dict[str, str], env_file: Path
) -> dict[str, dict[str, Any]]:
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("CI"):
            pytest.fail("docker is required on CI to render the Compose config")
        pytest.skip("docker is not installed")

    # A clean environment: a developer's shell ASR_* or COMPOSE_* cannot change the render.
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": os.environ.get("HOME", ""),
    }
    env.update(CI_ENV)
    env.update(extra_env)

    argv = [docker, "compose"]
    for file in files:
        argv += ["--file", str(file)]
    argv += ["--env-file", str(env_file)]
    for profile in profiles:
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
    return json.loads(completed.stdout)["services"]


# S1: on-prem base. S2: on-prem base plus the app-server overlay. S3: cloud.
SETUPS = {
    "S1": (ONPREM_FILE,),
    "S2": (ONPREM_FILE, APP_SERVER_FILE),
    "S3": (CLOUD_FILE,),
}
SETUP_ENV = {"S1": {}, "S2": APP_SERVER_ENV, "S3": CLOUD_ENV}
SETUP_PROFILES = {"S1": ("onprem",), "S2": ("onprem",), "S3": ("cloud",)}


@pytest.fixture(scope="module")
def rendered_defaults(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    _require_compose_files()
    # An empty env file, so no ASR_* variable is set anywhere.
    env_file = tmp_path_factory.mktemp("compose") / "empty.env"
    env_file.write_text("", encoding="utf-8")
    return {
        setup: _render(files, SETUP_PROFILES[setup], SETUP_ENV[setup], env_file)
        for setup, files in SETUPS.items()
    }


@pytest.fixture(scope="module")
def rendered_set(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    _require_compose_files()
    env_file = tmp_path_factory.mktemp("compose-set") / "asr.env"
    env_file.write_text(
        "".join(f"{name}={value}\n" for name, value in ASR_ENV_FILE.items()),
        encoding="utf-8",
    )
    return {
        setup: _render(files, SETUP_PROFILES[setup], SETUP_ENV[setup], env_file)
        for setup, files in SETUPS.items()
    }


@pytest.fixture(scope="module")
def compose_documents() -> dict[str, Any]:
    _require_compose_files()
    return {
        "onprem": yaml.safe_load(ONPREM_FILE.read_text(encoding="utf-8")),
        "app-server": yaml.safe_load(APP_SERVER_FILE.read_text(encoding="utf-8")),
        "cloud": yaml.safe_load(CLOUD_FILE.read_text(encoding="utf-8")),
    }


def _declared_services(document: dict[str, Any], profiles: tuple[str, ...]) -> set[str]:
    """The services a render of ``profiles`` must show (no profiles means always)."""
    names = set()
    for name, service in document["services"].items():
        service_profiles = service.get("profiles") or []
        if not service_profiles or set(service_profiles) & set(profiles):
            names.add(name)
    return names


def test_every_declared_service_renders(
    rendered_defaults: dict[str, dict[str, Any]],
    compose_documents: dict[str, Any],
) -> None:
    documents = {
        "S1": compose_documents["onprem"],
        "S2": compose_documents["onprem"],
        "S3": compose_documents["cloud"],
    }
    for setup, document in documents.items():
        expected = _declared_services(document, SETUP_PROFILES[setup])
        assert set(rendered_defaults[setup]) == expected, setup
    # The overlay overrides services of the base file and adds none of its own.
    overlay_names = set(compose_documents["app-server"]["services"])
    assert overlay_names <= set(compose_documents["onprem"]["services"])


@pytest.mark.parametrize("setup", ["S1", "S2", "S3"])
@pytest.mark.parametrize("service", APP_SERVICES)
def test_defaults_reach_every_app_service(
    rendered_defaults: dict[str, dict[str, Any]], setup: str, service: str
) -> None:
    environment = _environment(rendered_defaults[setup][service])

    for env_name, default in asr_env_defaults().items():
        assert environment.get(env_name) == default, (setup, service, env_name)


@pytest.mark.parametrize("setup", ["S1", "S2", "S3"])
@pytest.mark.parametrize("service", APP_SERVICES)
def test_the_whole_anchor_reaches_every_app_service(
    rendered_defaults: dict[str, dict[str, Any]],
    compose_documents: dict[str, Any],
    setup: str,
    service: str,
) -> None:
    anchor = compose_documents["onprem" if setup != "S3" else "cloud"]["x-app-environment"]

    assert set(anchor) <= set(_environment(rendered_defaults[setup][service])), (setup, service)


@pytest.mark.parametrize("setup", ["S1", "S2", "S3"])
@pytest.mark.parametrize("service", APP_SERVICES)
def test_env_file_values_reach_every_app_service(
    rendered_set: dict[str, dict[str, Any]], setup: str, service: str
) -> None:
    environment = _environment(rendered_set[setup][service])

    expected = {**ASR_ENV_FILE, "ASR_PROVIDER": "local_whisper"}
    for env_name, value in expected.items():
        assert environment.get(env_name) == value, (setup, service, env_name)


# --- the worker's provider follows the environment (AC4) ------------------------------------


def _settings_from_worker(environment: dict[str, Any]) -> Settings:
    """Settings built the way the worker reads its rendered ASR environment."""
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": False,
        "whisper_model": str(environment["WHISPER_MODEL"]),
    }
    for env_name in asr_env_defaults():
        values[env_name.lower()] = str(environment[env_name])
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("setup", ["S1", "S2", "S3"])
def test_worker_profile_follows_the_env_file(
    rendered_set: dict[str, dict[str, Any]], setup: str
) -> None:
    settings = _settings_from_worker(_environment(rendered_set[setup]["worker"]))

    assert settings.asr_profile == "cpu-small"
    provider = create_asr_provider(settings)
    assert isinstance(provider, LocalWhisperProvider)
    assert provider.identity().model == "small"


def test_worker_defaults_keep_whisper_model(
    rendered_defaults: dict[str, dict[str, Any]],
) -> None:
    settings = _settings_from_worker(_environment(rendered_defaults["S1"]["worker"]))

    assert settings.asr_profile == ""
    provider = create_asr_provider(settings)
    assert isinstance(provider, LocalWhisperProvider)
    assert settings.whisper_model == "base"
    assert provider.identity().model == settings.whisper_model


# --- static YAML, no docker -----------------------------------------------------------------


@pytest.mark.parametrize("file_name", ["onprem", "cloud"])
def test_anchor_declares_every_asr_setting_with_the_code_default(
    compose_documents: dict[str, Any], file_name: str
) -> None:
    anchor = compose_documents[file_name]["x-app-environment"]

    for env_name, default in asr_env_defaults().items():
        assert env_name in anchor, (file_name, env_name)
        assert _anchor_default(anchor, env_name) == default, (file_name, env_name)
