"""The retired legacy stack stays retired; the live surface stays as it was (N11 / F01).

The unmounted Neo4j dashboard, graph, Copilot, LDAP sync and VoIP webhook
routers and the services only they used were removed; ``test_truthful_outputs``
keeps those modules from becoming importable again. This module covers what the
import guard cannot see: the settings only that stack read, the production
validator clause for the webhook, the modules that must keep importing and the
mounted routes. Inventory: Homayar/docs/architecture/legacy-retirement.md.
"""

from __future__ import annotations

import importlib
import re

import pytest

from backend.config import Settings
from backend.main import create_app

RETIRED_SETTINGS = (
    "llm_model_qc",
    "llm_model_problem",
    "llm_model_insight",
    "llm_model_teacher",
    "llm_model_copilot",
    "ldap_server",
    "ldap_port",
    "ldap_use_ssl",
    "ldap_bind_dn",
    "ldap_bind_password",
    "ldap_search_base",
    "ldap_user_ou",
    "voip_webhook_secret",
    "enable_voip_webhook",
    "admin_default_password",
)
LIVE_SETTINGS = ("llm_model", "redis_url")

# Variables an existing deployment may still export for the retired stack.
LEGACY_ENVIRONMENT = {
    "LDAP_SERVER": "ad.example.invalid",
    "VOIP_WEBHOOK_SECRET": "legacy-webhook-value",
    "ENABLE_VOIP_WEBHOOK": "true",
    "LLM_MODEL_QC": "legacy-qc-model",
    "ADMIN_DEFAULT_PASSWORD": "legacy-bootstrap-value",
}

LIVE_MODULES = (
    "backend.database.neo4j_connection",
    "backend.database.seed",
    "backend.providers.factory",
    "backend.services.rate_limit_service",
)

LEGACY_ROUTE = re.compile(r"/(dashboard|graph|copilot|webhook|ldap|voip|ws)\b")


def _settings(environment: str) -> Settings:
    overrides: dict = {"environment": environment, "_env_file": None}
    if environment == "production":
        overrides.update(
            database_url="postgresql+psycopg://db.example.invalid/homayar",
            jwt_secret="j" * 48,
            token_pepper="p" * 48,
            secure_cookies=True,
            enable_demo_account=False,
            allow_mock_providers=False,
            auto_create_schema=False,
        )
    return Settings(**overrides)


def _clear_legacy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in LEGACY_ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)


def _assert_not_exposed(settings: Settings, variable: str) -> None:
    name = variable.lower()
    assert not hasattr(settings, name), f"{name} is still a Settings attribute"
    assert name not in settings.model_dump()
    assert not settings.model_extra  # extra="ignore": unknown input is dropped


@pytest.mark.parametrize("name", RETIRED_SETTINGS)
def test_retired_setting_is_not_a_field(name: str) -> None:
    assert name not in Settings.model_fields


@pytest.mark.parametrize("name", LIVE_SETTINGS)
def test_live_setting_is_still_a_field(name: str) -> None:
    assert name in Settings.model_fields


@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("variable", sorted(LEGACY_ENVIRONMENT))
def test_legacy_environment_variable_is_ignored(
    monkeypatch: pytest.MonkeyPatch, environment: str, variable: str
) -> None:
    _clear_legacy_environment(monkeypatch)
    monkeypatch.setenv(variable, LEGACY_ENVIRONMENT[variable])

    settings = _settings(environment)

    assert settings.environment == environment
    _assert_not_exposed(settings, variable)


@pytest.mark.parametrize("environment", ["development", "production"])
def test_all_legacy_environment_variables_together_are_ignored(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    _clear_legacy_environment(monkeypatch)
    for variable, value in LEGACY_ENVIRONMENT.items():
        monkeypatch.setenv(variable, value)

    settings = _settings(environment)

    for variable in LEGACY_ENVIRONMENT:
        _assert_not_exposed(settings, variable)


def test_production_no_longer_requires_a_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # The removed validator clause refused ENABLE_VOIP_WEBHOOK without a secret.
    _clear_legacy_environment(monkeypatch)
    monkeypatch.setenv("ENABLE_VOIP_WEBHOOK", "true")

    assert _settings("production").is_production


@pytest.mark.parametrize("module", LIVE_MODULES)
def test_live_module_still_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module


def test_no_legacy_route_is_mounted(tmp_path) -> None:
    app = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite:///{tmp_path / 'unused.db'}",
            jwt_secret="j" * 48,
            token_pepper="p" * 48,
            _env_file=None,
        )
    )
    paths = {getattr(route, "path", "") for route in app.routes}

    assert sorted(path for path in paths if LEGACY_ROUTE.search(path)) == []
    assert "/api/v1/meetings" in paths  # allow-control: the v1 surface is mounted
