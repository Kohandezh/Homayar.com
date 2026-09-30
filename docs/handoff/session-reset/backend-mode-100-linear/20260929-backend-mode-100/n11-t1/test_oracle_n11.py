from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.config import Settings
from backend.main import create_app
from backend.tests.test_truthful_outputs import (
    RANDOM_ALLOWLIST,
    RANDOM_PATTERN,
    REMOVED_MODULES,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = BACKEND_ROOT.parent
REPOSITORY_ROOT = PRODUCT_ROOT.parent

RETIRED_MODULES = (
    "backend.routers.copilot",
    "backend.routers.dashboard",
    "backend.routers.graph",
    "backend.routers.ldap_sync",
    "backend.routers.webhook",
    "backend.services.copilot_service",
    "backend.services.dashboard_service",
    "backend.services.graph_service",
    "backend.services.issue_aggregation_service",
    "backend.services.call_service",
    "backend.services.ad_service",
    "backend.services.user_service",
    "backend.services.pdf_service",
    "backend.services.redis_service",
    "backend.services.llm_router",
    "backend.database.models",
    "backend.websocket",
    "backend.websocket.manager",
)

BASE_REMOVED_MODULES = (
    "backend.services.stt_service",
    "backend.services.analysis_service",
    "backend.routers.calls",
    "backend.celery_app.tasks",
    "backend.auth",
    "backend.middleware.auth",
    "backend.routers.auth",
)

LIVE_MODULES = (
    "backend.database.neo4j_connection",
    "backend.database.seed",
    "backend.providers.factory",
    "backend.services.rate_limit_service",
)

SIBLING_MODULES = (
    "backend.main",
    "backend.routers",
    "backend.routers.v1",
    "backend.routers.v1.auth",
    "backend.services",
    "backend.services.auth_service",
    "backend.database",
    "backend.database.relational",
    "backend.database.saas_models",
)

DEAD_SETTINGS = (
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

LIVE_SETTINGS = (
    "llm_model",
    "redis_url",
    "llm_provider",
    "zai_api_key",
    "zai_base_url",
    "gapgpt_api_key",
    "gapgpt_base_url",
    "neo4j_uri",
    "neo4j_user",
    "neo4j_password",
    "neo4j_database",
    "enable_neo4j_projection",
)

BASE_SETTINGS_FIELDS = frozenset(
    """
    access_token_minutes admin_default_password allow_mock_providers allowed_extensions
    api_key_max_active_per_workspace api_key_max_ttl_days api_key_rate_limit_per_hour
    api_prefix app_name asanak_api_key asanak_base_url asanak_sender asr_provider
    auto_create_schema billing_provider cookie_domain cors_origins csrf_header_name
    database_app_role database_max_overflow database_pool_size database_url
    demo_quota_seconds deployment_mode diarization_provider dispatch_max_attempts
    dispatch_retry_base_seconds dispatch_retry_max_seconds dispatch_sweep_batch_size
    dispatch_sweep_interval_seconds enable_demo_account enable_free_demo enable_google_auth
    enable_neo4j_projection enable_phone_auth enable_voip_webhook environment ffmpeg_path
    ffmpeg_timeout_seconds ffprobe_path ffprobe_timeout_seconds frontend_url gapgpt_api_key
    gapgpt_base_url google_client_id google_client_secret google_redirect_uri
    intelligence_provider job_artifact_grace_seconds job_artifact_max_bytes
    job_artifact_sweep_batch_size job_artifact_sweep_interval_seconds
    job_attempt_timeout_seconds job_checkpoint_reuse_enabled job_deadline_seconds
    job_lease_renew_interval_seconds job_lease_seconds job_max_attempts job_max_replays
    job_recovery_batch_size job_recovery_sweep_interval_seconds job_retry_delay_seconds
    job_retry_jitter_ratio job_retry_max_delay_seconds job_stage_timeout_seconds
    jwt_audience jwt_expiry_hours jwt_issuer jwt_secret ldap_bind_dn ldap_bind_password
    ldap_port ldap_search_base ldap_server ldap_use_ssl ldap_user_ou llm_fallback_api_key
    llm_fallback_base_url llm_fallback_model llm_model llm_model_copilot llm_model_insight
    llm_model_problem llm_model_qc llm_model_teacher llm_provider local_ai_base_url
    log_level max_audio_duration_seconds max_upload_size_mb media_decode_probe_seconds
    media_min_bytes_per_second media_reconciliation_batch_size
    media_reconciliation_grace_seconds media_reconciliation_interval_seconds
    media_reconciliation_remove_orphans metrics_enabled model_artifact_root
    model_manifest_paths model_manifest_pins model_manifest_strict neo4j_database
    neo4j_password neo4j_uri neo4j_user otp_max_attempts otp_resend_cooldown_seconds
    otp_ttl_seconds pii_mapping_active_key_id pii_mapping_keys
    provider_circuit_cooldown_seconds provider_circuit_failure_threshold
    provider_rate_limits public_url rate_limit_fail_open redis_url refresh_token_days
    retention_days_default retention_purge_batch_size retention_purge_hold_recheck_seconds
    retention_purge_interval_seconds retention_purge_max_attempts
    retention_purge_retry_base_seconds retention_purge_retry_max_seconds secure_cookies
    sentry_dsn smtp_from smtp_host smtp_password smtp_port smtp_use_tls smtp_username
    storage_access_key storage_backend storage_bucket storage_endpoint_url
    storage_local_root storage_presign_ttl_seconds storage_region storage_secret_key
    tasks_eager token_pepper tombstone_journal_dir trusted_hosts tts_base_url
    tts_default_voice tts_model tts_piper_binary tts_provider tts_voices upload_dir
    upload_quarantine_dir upload_rate_limit_per_hour usage_reconciliation_batch_size
    usage_reconciliation_grace_seconds usage_reconciliation_interval_seconds
    usage_reconciliation_repair_counters voice_default_workspace_call_limit
    voice_deployment_profile voice_global_call_limit voice_node_heartbeat_ttl_seconds
    voice_slot_lease_seconds voip_webhook_secret whisper_compute_type whisper_device
    whisper_local_files_only whisper_model whisper_model_path
    worker_concurrency_per_workspace zai_api_key zai_base_url
    """.split()
)

LEGACY_ENVIRONMENT = (
    ("LDAP_SERVER", "ad.legacy-n11.example"),
    ("VOIP_WEBHOOK_SECRET", "legacy-voip-secret-n11-4d1f"),
    ("ENABLE_VOIP_WEBHOOK", "true"),
    ("LLM_MODEL_QC", "legacy-qc-model-n11-7b2e"),
    ("ADMIN_DEFAULT_PASSWORD", "legacy-admin-password-n11-93c0"),
)

LEGACY_SENTINELS = (
    "ad.legacy-n11.example",
    "legacy-voip-secret-n11-4d1f",
    "legacy-qc-model-n11-7b2e",
    "legacy-admin-password-n11-93c0",
)

LEGACY_ENVIRONMENT_SPELLINGS = (
    ("enable_voip_webhook", "true"),
    ("Enable_Voip_Webhook", "1"),
    ("ENABLE_VOIP_WEBHOOK", "sometimes"),
    ("ldap_server", "ad.legacy-n11.example"),
    ("LDAP_PORT", "ldaps"),
    ("LDAP_USE_SSL", "maybe"),
    ("VOIP_WEBHOOK_SECRET", ""),
    ("ADMIN_DEFAULT_PASSWORD", ""),
    ("llm_model_qc", "legacy-qc-model-n11-7b2e"),
)

PRODUCTION = {
    "environment": "production",
    "database_url": "postgresql+psycopg://homayar:unused@database.test/homayar",
    "jwt_secret": "j" * 48,
    "token_pepper": "p" * 48,
    "secure_cookies": True,
    "allow_mock_providers": False,
    "auto_create_schema": False,
    "enable_demo_account": False,
    "enable_google_auth": False,
    "enable_phone_auth": False,
    "storage_backend": "local",
    "api_prefix": "/api/v1",
}

PRODUCTION_ENVIRONMENT = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+psycopg://homayar:unused@database.test/homayar",
    "JWT_SECRET": "j" * 48,
    "TOKEN_PEPPER": "p" * 48,
    "SECURE_COOKIES": "true",
    "ALLOW_MOCK_PROVIDERS": "false",
    "AUTO_CREATE_SCHEMA": "false",
    "ENABLE_DEMO_ACCOUNT": "false",
    "ENABLE_GOOGLE_AUTH": "false",
    "ENABLE_PHONE_AUTH": "false",
    "STORAGE_BACKEND": "local",
}

INSECURE_PRODUCTION = (
    {"secure_cookies": False},
    {"jwt_secret": "short"},
    {"allow_mock_providers": True},
    {"database_url": "sqlite:///unsafe.db"},
    {"auto_create_schema": True},
)

DOCUMENTATION_ROUTES = frozenset(
    {
        ("GET", "/api/docs"),
        ("HEAD", "/api/docs"),
        ("GET", "/api/openapi.json"),
        ("HEAD", "/api/openapi.json"),
        ("GET", "/docs/oauth2-redirect"),
        ("HEAD", "/docs/oauth2-redirect"),
    }
)

BASE_ROUTES = frozenset(
    {
        ("GET", "/api/docs"),
        ("HEAD", "/api/docs"),
        ("GET", "/api/health"),
        ("GET", "/api/openapi.json"),
        ("HEAD", "/api/openapi.json"),
        ("GET", "/api/v1/admin/ai-policy"),
        ("PUT", "/api/v1/admin/ai-policy"),
        ("GET", "/api/v1/admin/ai-policy/versions"),
        ("GET", "/api/v1/admin/audit-events"),
        ("GET", "/api/v1/admin/jobs/{job_id}/policy-snapshots"),
        ("POST", "/api/v1/admin/jobs/{job_id}/replay"),
        ("GET", "/api/v1/admin/workspace-settings"),
        ("PATCH", "/api/v1/admin/workspace-settings"),
        ("GET", "/api/v1/api-keys"),
        ("POST", "/api/v1/api-keys"),
        ("DELETE", "/api/v1/api-keys/{key_id}"),
        ("POST", "/api/v1/auth/forgot-password"),
        ("GET", "/api/v1/auth/google/callback"),
        ("GET", "/api/v1/auth/google/link/start"),
        ("GET", "/api/v1/auth/google/start"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/methods"),
        ("POST", "/api/v1/auth/otp/request"),
        ("POST", "/api/v1/auth/otp/verify"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/reset-password"),
        ("POST", "/api/v1/auth/verify-email"),
        ("POST", "/api/v1/auth/verify-email/resend"),
        ("GET", "/api/v1/billing/management"),
        ("GET", "/api/v1/billing/plans"),
        ("GET", "/api/v1/billing/subscription"),
        ("GET", "/api/v1/billing/usage"),
        ("GET", "/api/v1/billing/usage/events"),
        ("GET", "/api/v1/ingestions"),
        ("GET", "/api/v1/ingestions/{ingestion_id}"),
        ("GET", "/api/v1/locations"),
        ("POST", "/api/v1/locations"),
        ("POST", "/api/v1/locations/nearest"),
        ("POST", "/api/v1/locations/resolve"),
        ("DELETE", "/api/v1/locations/{location_id}"),
        ("GET", "/api/v1/locations/{location_id}"),
        ("PATCH", "/api/v1/locations/{location_id}"),
        ("POST", "/api/v1/media/{media_id}/legal-hold"),
        ("POST", "/api/v1/media/{media_id}/legal-hold/release"),
        ("GET", "/api/v1/meetings"),
        ("DELETE", "/api/v1/meetings/{meeting_id}"),
        ("GET", "/api/v1/meetings/{meeting_id}"),
        ("PATCH", "/api/v1/meetings/{meeting_id}"),
        ("GET", "/api/v1/meetings/{meeting_id}/actions"),
        ("POST", "/api/v1/meetings/{meeting_id}/actions"),
        ("DELETE", "/api/v1/meetings/{meeting_id}/actions/{action_id}"),
        ("PATCH", "/api/v1/meetings/{meeting_id}/actions/{action_id}"),
        ("GET", "/api/v1/meetings/{meeting_id}/evidence"),
        ("GET", "/api/v1/meetings/{meeting_id}/evidence/{evidence_id}"),
        ("GET", "/api/v1/meetings/{meeting_id}/export"),
        ("GET", "/api/v1/meetings/{meeting_id}/job"),
        ("POST", "/api/v1/meetings/{meeting_id}/legal-hold"),
        ("GET", "/api/v1/meetings/{meeting_id}/media"),
        ("POST", "/api/v1/meetings/{meeting_id}/media/{media_id}/verify"),
        ("GET", "/api/v1/meetings/{meeting_id}/quality"),
        ("GET", "/api/v1/meetings/{meeting_id}/recording"),
        ("GET", "/api/v1/meetings/{meeting_id}/shares"),
        ("POST", "/api/v1/meetings/{meeting_id}/shares"),
        ("DELETE", "/api/v1/meetings/{meeting_id}/shares/{share_id}"),
        ("PATCH", "/api/v1/meetings/{meeting_id}/shares/{share_id}/recipients"),
        ("PATCH", "/api/v1/meetings/{meeting_id}/speakers/{speaker_id}"),
        ("GET", "/api/v1/meetings/{meeting_id}/summary"),
        ("GET", "/api/v1/meetings/{meeting_id}/transcript"),
        ("POST", "/api/v1/meetings/{meeting_id}/transcript/corrections"),
        ("POST", "/api/v1/meetings/{meeting_id}/transcript/redactions/{redaction_id}/reveal"),
        ("GET", "/api/v1/meetings/{meeting_id}/transcript/revisions"),
        ("GET", "/api/v1/members"),
        ("GET", "/api/v1/members/invitations"),
        ("POST", "/api/v1/members/invitations"),
        ("POST", "/api/v1/members/invitations/accept"),
        ("DELETE", "/api/v1/members/invitations/{invitation_id}"),
        ("DELETE", "/api/v1/members/{membership_id}"),
        ("PATCH", "/api/v1/members/{membership_id}"),
        ("GET", "/api/v1/organizations"),
        ("GET", "/api/v1/organizations/{organization_id}"),
        ("GET", "/api/v1/organizations/{organization_id}/aggregates"),
        ("GET", "/api/v1/organizations/{organization_id}/members"),
        ("POST", "/api/v1/organizations/{organization_id}/members"),
        ("DELETE", "/api/v1/organizations/{organization_id}/members/{membership_id}"),
        ("PATCH", "/api/v1/organizations/{organization_id}/members/{membership_id}"),
        ("GET", "/api/v1/organizations/{organization_id}/records"),
        ("GET", "/api/v1/organizations/{organization_id}/scope-grants"),
        ("POST", "/api/v1/organizations/{organization_id}/scope-grants"),
        ("DELETE", "/api/v1/organizations/{organization_id}/scope-grants/{grant_id}"),
        ("GET", "/api/v1/organizations/{organization_id}/workspaces"),
        ("POST", "/api/v1/organizations/{organization_id}/workspaces"),
        ("POST", "/api/v1/organizations/{organization_id}/workspaces/{workspace_id}/members"),
        (
            "DELETE",
            "/api/v1/organizations/{organization_id}/workspaces/{workspace_id}"
            "/members/{membership_id}",
        ),
        ("POST", "/api/v1/privacy/subject-exports"),
        ("GET", "/api/v1/search"),
        ("GET", "/api/v1/shares/{share_token}"),
        ("GET", "/api/v1/shares/{share_token}/export"),
        ("GET", "/api/v1/shares/{share_token}/recording"),
        ("GET", "/api/v1/teams"),
        ("POST", "/api/v1/teams"),
        ("GET", "/api/v1/teams/{team_id}"),
        ("PATCH", "/api/v1/teams/{team_id}"),
        ("POST", "/api/v1/teams/{team_id}/assignments"),
        ("DELETE", "/api/v1/teams/{team_id}/assignments/{assignment_id}"),
        ("POST", "/api/v1/uploads"),
        ("POST", "/api/v1/uploads/"),
        ("POST", "/api/v1/voice/calls"),
        ("GET", "/api/v1/voice/calls/{session_id}"),
        ("POST", "/api/v1/voice/calls/{session_id}/transitions"),
        ("GET", "/api/v1/voice/capacity"),
        ("POST", "/api/v1/voice/nodes"),
        ("POST", "/api/v1/voice/nodes/{node_id}/drain"),
        ("POST", "/api/v1/voice/nodes/{node_id}/heartbeat"),
        ("POST", "/api/v1/voice/recover"),
        ("GET", "/api/v1/voicemails"),
        ("POST", "/api/v1/voicemails"),
        ("GET", "/api/v1/voicemails/analytics"),
        ("GET", "/api/v1/voicemails/{voicemail_id}"),
        ("PATCH", "/api/v1/voicemails/{voicemail_id}"),
        ("GET", "/api/v1/workspaces/{workspace_id}/meetings/{meeting_id}/recording"),
        ("GET", "/docs/oauth2-redirect"),
        ("HEAD", "/docs/oauth2-redirect"),
        ("GET", "/healthz"),
        ("GET", "/readyz"),
    }
)

LEGACY_ENDPOINTS = (
    ("POST", "/api/webhook/voip/call-event"),
    ("POST", "/api/webhook/voip/batch"),
    ("POST", "/api/v1/webhook/voip/call-event"),
    ("POST", "/api/v1/webhook/voip/batch"),
    ("POST", "/api/copilot/chat"),
    ("GET", "/api/dashboard/kpis"),
    ("GET", "/api/graph/stats"),
    ("POST", "/api/ldap/sync"),
)

RETIREMENT_INVENTORY = PRODUCT_ROOT / "docs" / "architecture" / "legacy-retirement.md"

RETIRED_PATH_SPELLINGS = (
    ("routers/copilot.py", "backend.routers.copilot"),
    ("routers/dashboard.py", "backend.routers.dashboard"),
    ("routers/graph.py", "backend.routers.graph"),
    ("routers/ldap_sync.py", "backend.routers.ldap_sync"),
    ("routers/webhook.py", "backend.routers.webhook"),
    ("services/copilot_service.py", "backend.services.copilot_service"),
    ("services/dashboard_service.py", "backend.services.dashboard_service"),
    ("services/graph_service.py", "backend.services.graph_service"),
    ("services/issue_aggregation_service.py", "backend.services.issue_aggregation_service"),
    ("services/call_service.py", "backend.services.call_service"),
    ("services/ad_service.py", "backend.services.ad_service"),
    ("services/user_service.py", "backend.services.user_service"),
    ("services/pdf_service.py", "backend.services.pdf_service"),
    ("services/redis_service.py", "backend.services.redis_service"),
    ("services/llm_router.py", "backend.services.llm_router"),
    ("database/models.py", "backend.database.models"),
    ("websocket/", "backend.websocket"),
)

ADR_DRAFT_DIRECTORY = PRODUCT_ROOT / "docs" / "handoff" / "session-a" / "adr"
ACCEPTED_ADR_DIRECTORY = REPOSITORY_ROOT / "docs" / "architecture" / "decisions"

ADR_DRAFT_HASHES = {
    "0026-model-manifests-and-preflight.md": (
        "c1658629d70cd2f0acbec51877df1ed677796ba08d2825c6a5e88741e24402e1"
    ),
    "0027-ai-policies-and-run-snapshots.md": (
        "7da86283059640cb9c2ecdafb3ad7736d73e93a4a45537223c711e096a268f6b"
    ),
    "0028-privacy-eligibility-routing.md": (
        "f89fff959038302342fcb8b0bdbd3f44f5aaa10cae5ec6978600507f3707c483"
    ),
    "0029-adapter-contract-usage-and-padyar-boundary.md": (
        "b15441d9bd9d0cae0ba948010bc05a1b28c80effaf59d049afb8b9b6745d792c"
    ),
    "0030-provider-resilience-and-same-policy-fallback.md": (
        "210c57410f467278a43ae4b01db4bc878ffddf2ed332d6ee4b42ca61ef51b8db"
    ),
    "0031-egress-denial.md": ("74bbb11e89415ae6be73cd23ceb45ecd96017caaaa255c9932fb7edcd3d9323d"),
}

ACCEPTED_ADR_HASHES = {
    "0026-model-manifests-and-preflight.md": (
        "4e207ec8eaaf414aefae362f55f6e6f7e808c7c9e2800a257e55677d18c9803b"
    ),
    "0027-ai-policies-and-run-snapshots.md": (
        "8bdba633adcb19c85d32e67147d3ecb74444c89a1799156929d61f30e5d4032c"
    ),
    "0028-privacy-eligibility-routing.md": (
        "d45476000dfbeb23747e2964a820c7536c541e9ccb760fe506e204b35355558e"
    ),
    "0029-adapter-contract-usage-and-padyar-boundary.md": (
        "bea89088eef2528147e6e66a1911314932a6362becbf0d5bc6a7d3c7cb8fd565"
    ),
    "0030-provider-resilience-and-same-policy-fallback.md": (
        "bc8afb613529d0dd3b5262d567fe05d322befb0bcb133b21b7ea6cc4c769f491"
    ),
    "0031-egress-denial.md": ("3ef14b4ae81fb3345cacdd3be9b7097fbfe3e1d3f50079f95be693d98b8955e1"),
}

DEAD_SETTING_REFERENCE = re.compile(
    r"\.(?:" + "|".join(DEAD_SETTINGS) + r")\b"
    r"|[\"'](?:" + "|".join(DEAD_SETTINGS) + r")[\"']",
    re.IGNORECASE,
)


def _development(**values: object) -> Settings:
    return Settings(**{"environment": "development", "_env_file": None, **values})


def _production(**values: object) -> Settings:
    return Settings(**{**PRODUCTION, "_env_file": None, **values})


BUILDERS: tuple[Callable[..., Settings], ...] = (_development, _production)
BUILDER_IDS = ("development", "production")


def _application(**values: object) -> FastAPI:
    return create_app(
        Settings(
            **{
                "environment": "test",
                "database_url": PRODUCTION["database_url"],
                "jwt_secret": "j" * 48,
                "token_pepper": "p" * 48,
                "api_prefix": "/api/v1",
                "trusted_hosts": "testserver,localhost,127.0.0.1",
                "_env_file": None,
                **values,
            }
        )
    )


def _served_routes(app: FastAPI) -> set[tuple[str, str]]:
    served: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or {type(route).__name__}
        served.update((method, route.path) for method in methods)
    return served


def _spec(module: str) -> object:
    try:
        return importlib.util.find_spec(module)
    except ModuleNotFoundError:
        return None


def _assert_not_exposed(settings: Settings, name: str) -> None:
    attribute = name.lower()
    assert not hasattr(settings, attribute), f"{attribute} is still exposed on Settings"
    assert attribute not in settings.model_dump()
    assert not settings.model_extra


def _files_referencing(pattern: re.Pattern[str]) -> list[str]:
    found = []
    for path in BACKEND_ROOT.rglob("*.py"):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative.startswith(("tests/", ".venv/")) or "/.venv/" in relative:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            found.append(relative)
    return sorted(found)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def legacy_free_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    for name in DEAD_SETTINGS:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    yield


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_retired_module_has_no_import_spec(module: str) -> None:
    assert _spec(module) is None, f"{module} must not be importable"


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_importing_retired_module_fails_on_the_module_itself(module: str) -> None:
    own_names = {module, *(m for m in RETIRED_MODULES if module.startswith(f"{m}."))}
    with pytest.raises(ModuleNotFoundError) as error:
        importlib.import_module(module)
    assert error.value.name in own_names, error.value


@pytest.mark.parametrize("module", LIVE_MODULES + SIBLING_MODULES)
def test_live_module_still_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module
    assert _spec(module) is not None


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_forbidden_legacy_guard_lists_retired_module(module: str) -> None:
    assert module in REMOVED_MODULES


@pytest.mark.parametrize("module", BASE_REMOVED_MODULES)
def test_forbidden_legacy_guard_keeps_existing_entry(module: str) -> None:
    assert module in REMOVED_MODULES


def test_forbidden_legacy_guard_only_appends() -> None:
    assert tuple(REMOVED_MODULES[: len(BASE_REMOVED_MODULES)]) == BASE_REMOVED_MODULES
    assert len(REMOVED_MODULES) == len(set(REMOVED_MODULES))


def test_random_number_guard_is_unchanged() -> None:
    assert RANDOM_ALLOWLIST == {"database/seed.py"}
    assert RANDOM_PATTERN.pattern == r"^\s*(import random\b|from random import)"
    assert RANDOM_PATTERN.flags & re.MULTILINE


@pytest.mark.parametrize("name", DEAD_SETTINGS)
def test_dead_setting_is_not_a_settings_field(name: str) -> None:
    assert name not in Settings.model_fields


@pytest.mark.parametrize("name", LIVE_SETTINGS)
def test_live_setting_is_still_a_settings_field(name: str) -> None:
    assert name in Settings.model_fields


def test_every_other_base_setting_is_kept() -> None:
    missing = BASE_SETTINGS_FIELDS - set(DEAD_SETTINGS) - set(Settings.model_fields)
    assert missing == set()


def test_no_setting_is_introduced() -> None:
    assert set(Settings.model_fields) - BASE_SETTINGS_FIELDS == set()


def test_no_backend_code_reads_a_dead_setting() -> None:
    assert _files_referencing(DEAD_SETTING_REFERENCE) == []


def test_live_setting_reference_is_still_detected() -> None:
    assert _files_referencing(re.compile(r"\.llm_model\b")) != []


@pytest.mark.parametrize(
    "values",
    (
        {"enable_voip_webhook": True},
        {"enable_voip_webhook": True, "voip_webhook_secret": ""},
        {"enable_voip_webhook": "true", "voip_webhook_secret": None},
        {"ldap_port": None, "ldap_use_ssl": None},
    ),
    ids=("switch-on", "switch-on-empty-secret", "switch-on-null-secret", "null-ldap"),
)
def test_production_accepts_retired_setting_keywords(values: dict[str, object]) -> None:
    settings = _production(**values)
    assert settings.is_production
    for name in values:
        _assert_not_exposed(settings, name)


def test_production_with_only_live_settings_is_valid() -> None:
    settings = _production()
    assert settings.is_production
    assert settings.llm_model == "default"


@pytest.mark.parametrize(
    "values",
    INSECURE_PRODUCTION,
    ids=("insecure-cookies", "short-jwt", "mock-providers", "sqlite", "auto-schema"),
)
def test_production_still_rejects_insecure_configuration(
    values: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in LEGACY_ENVIRONMENT:
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        _production(**values)


@pytest.mark.parametrize(("name", "value"), LEGACY_ENVIRONMENT, ids=lambda item: str(item))
@pytest.mark.parametrize("build", BUILDERS, ids=BUILDER_IDS)
def test_legacy_environment_variable_is_ignored(
    build: Callable[..., Settings], name: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, value)
    _assert_not_exposed(build(), name)


@pytest.mark.parametrize(
    ("name", "value"), LEGACY_ENVIRONMENT_SPELLINGS, ids=lambda item: str(item) or "empty"
)
@pytest.mark.parametrize("build", BUILDERS, ids=BUILDER_IDS)
def test_legacy_environment_spelling_is_ignored(
    build: Callable[..., Settings], name: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, value)
    _assert_not_exposed(build(), name)


@pytest.mark.parametrize("build", BUILDERS, ids=BUILDER_IDS)
def test_all_legacy_environment_variables_together_are_ignored(
    build: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in LEGACY_ENVIRONMENT:
        monkeypatch.setenv(name, value)
    settings = build()
    for name, _ in LEGACY_ENVIRONMENT:
        _assert_not_exposed(settings, name)
    exposed = settings.model_dump_json() + repr(settings)
    for sentinel in LEGACY_SENTINELS:
        assert sentinel not in exposed


@pytest.mark.parametrize("build", BUILDERS, ids=BUILDER_IDS)
def test_live_environment_variables_are_still_read_next_to_legacy_ones(
    build: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in LEGACY_ENVIRONMENT:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LLM_MODEL", "live-model-n11-5a1c")
    monkeypatch.setenv("REDIS_URL", "redis://live-redis.test:6379/4")
    settings = build()
    assert settings.llm_model == "live-model-n11-5a1c"
    assert settings.redis_url == "redis://live-redis.test:6379/4"


def test_production_from_environment_alone_ignores_legacy_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in PRODUCTION_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENABLE_VOIP_WEBHOOK", "true")
    monkeypatch.setenv("LDAP_SERVER", "ad.legacy-n11.example")
    monkeypatch.setenv("ADMIN_DEFAULT_PASSWORD", "legacy-admin-password-n11-93c0")
    settings = Settings(_env_file=None)
    assert settings.is_production
    for name in ("ENABLE_VOIP_WEBHOOK", "LDAP_SERVER", "ADMIN_DEFAULT_PASSWORD"):
        _assert_not_exposed(settings, name)


def test_production_from_environment_alone_is_valid_without_legacy_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in PRODUCTION_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).is_production


def test_production_from_environment_alone_still_rejects_insecure_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {**PRODUCTION_ENVIRONMENT, "SECURE_COOKIES": "false"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENABLE_VOIP_WEBHOOK", "true")
    monkeypatch.setenv("VOIP_WEBHOOK_SECRET", "legacy-voip-secret-n11-4d1f")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("build", BUILDERS, ids=BUILDER_IDS)
def test_legacy_keys_in_dotenv_file_are_ignored(
    build: Callable[..., Settings], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    lines = [
        f"{name}={value}" for name, value in LEGACY_ENVIRONMENT if name != "VOIP_WEBHOOK_SECRET"
    ]
    env_file.write_text("\n".join([*lines, "LLM_MODEL=dotenv-live-model-n11"]) + "\n")
    settings = build(_env_file=env_file)
    assert settings.llm_model == "dotenv-live-model-n11"
    for name, _ in LEGACY_ENVIRONMENT:
        _assert_not_exposed(settings, name)


def test_application_serves_exactly_the_base_routes() -> None:
    served = _served_routes(_application())
    assert sorted(served - BASE_ROUTES) == []
    assert sorted(BASE_ROUTES - served) == []


def test_production_application_serves_the_base_routes_without_documentation() -> None:
    served = _served_routes(create_app(_production()))
    expected = BASE_ROUTES - DOCUMENTATION_ROUTES
    assert sorted(served - expected) == []
    assert sorted(expected - served) == []


@pytest.mark.parametrize(("method", "path"), LEGACY_ENDPOINTS)
def test_legacy_endpoint_is_not_served(method: str, path: str) -> None:
    client = TestClient(_application())
    response = client.request(method, path, headers={"X-Webhook-Secret": "legacy"}, json={})
    assert response.status_code == 404


def test_liveness_endpoint_is_served() -> None:
    client = TestClient(_application())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_retirement_inventory_exists() -> None:
    assert RETIREMENT_INVENTORY.is_file(), f"missing {RETIREMENT_INVENTORY}"


@pytest.mark.parametrize(
    ("path", "module"), RETIRED_PATH_SPELLINGS, ids=[path for path, _ in RETIRED_PATH_SPELLINGS]
)
def test_retirement_inventory_lists_retired_path(path: str, module: str) -> None:
    assert RETIREMENT_INVENTORY.is_file(), f"missing {RETIREMENT_INVENTORY}"
    text = RETIREMENT_INVENTORY.read_text(encoding="utf-8")
    assert path in text or module in text


def test_retirement_inventory_records_status_evidence_and_replacement() -> None:
    assert RETIREMENT_INVENTORY.is_file(), f"missing {RETIREMENT_INVENTORY}"
    text = RETIREMENT_INVENTORY.read_text(encoding="utf-8")
    assert "RETIRED" in text
    assert "mounted" in text.lower()
    assert "replacement" in text.lower()
    assert "capability not offered" in text


def test_retirement_inventory_records_unsafe_secret_comparison() -> None:
    assert RETIREMENT_INVENTORY.is_file(), f"missing {RETIREMENT_INVENTORY}"
    text = RETIREMENT_INVENTORY.read_text(encoding="utf-8")
    assert "!=" in text
    lowered = text.lower()
    assert any(
        marker in lowered
        for marker in ("constant-time", "constant time", "compare_digest", "timing")
    )


def test_retirement_inventory_defers_dependency_pruning() -> None:
    assert RETIREMENT_INVENTORY.is_file(), f"missing {RETIREMENT_INVENTORY}"
    text = RETIREMENT_INVENTORY.read_text(encoding="utf-8")
    for marker in ("ldap3", "reportlab", "WP01-T04", "HOM-28"):
        assert marker in text, marker


def test_adr_draft_supersession_note() -> None:
    readme = ADR_DRAFT_DIRECTORY / "README.md"
    assert readme.is_file(), f"missing {readme}"
    text = readme.read_text(encoding="utf-8")
    assert "supersede" in text.lower()
    assert "docs/architecture/decisions" in text
    assert "0026" in text
    assert "0031" in text


@pytest.mark.parametrize("name", sorted(ADR_DRAFT_HASHES))
def test_adr_draft_is_kept_unedited(name: str) -> None:
    assert _sha256(ADR_DRAFT_DIRECTORY / name) == ADR_DRAFT_HASHES[name]


@pytest.mark.parametrize("name", sorted(ACCEPTED_ADR_HASHES))
def test_accepted_adr_is_unchanged(name: str) -> None:
    assert _sha256(ACCEPTED_ADR_DIRECTORY / name) == ACCEPTED_ADR_HASHES[name]
