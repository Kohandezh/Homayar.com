"""HOM-351 phase 2 demo: the local AI timeout setting, the Compose passthrough
and the two qualified demo manifests.

No network, no GPU, no Docker: the manifests are read from the repo, the
identities come from the real adapters (no model is loaded) and the Compose
file is read as YAML.  The weight sha256 values themselves were read from the
Hugging Face API (Whisper) and ADR 0047 Amendment 1 (Qwen3-8B); Foreman
confirms them against what the server verified.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from backend.config import Settings
from backend.providers.artifacts import verify_artifacts
from backend.providers.factory import (
    create_asr_provider,
    create_intelligence_fallbacks,
    create_intelligence_provider,
)
from backend.providers.local_llm import local_llm_manifest_template
from backend.providers.registry import (
    ManifestError,
    ManifestRegistry,
    ManifestStatus,
    load_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = REPO_ROOT / "infra" / "manifests" / "hom351-demo"
ASR_MANIFEST_FILE = DEMO_DIR / "asr.local-whisper.large-v3-p40-demo.json"
LLM_MANIFEST_FILE = DEMO_DIR / "intelligence.llama-cpp.qwen3-8b-q4km-demo.json"
COMPOSE_ONPREM = REPO_ROOT / "infra" / "compose" / "compose.onprem.yaml"
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

# The demo settings (SPEC item 4's overlay will set these in Compose).
DEMO_BASE_URL = "http://local-ai:8080/v1"
DEMO_LLM_MODEL = "qwen3-8b-q4km"
ASR_MANIFEST_ID = "asr.local-whisper.large-v3-p40-demo"
LLM_MANIFEST_ID = "intelligence.llama-cpp.qwen3-8b-q4km-demo"

# Qwen3-8B values from ADR 0047 Amendment 1 (docs/architecture/decisions/0047...).
QWEN_REPO = "Qwen/Qwen3-8B-GGUF"
QWEN_REPO_COMMIT = "7c41481f57cb95916b40956ab2f0b139b296d974"
QWEN_GGUF = "Qwen3-8B-Q4_K_M.gguf"
QWEN_GGUF_SHA256 = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"

# Whisper values from the Hugging Face API for Systran/faster-whisper-large-v3,
# read 2026-11-12 (license MIT, the pinned main revision and the LFS sha256).
SYSTRAN_REPO = "Systran/faster-whisper-large-v3"
SYSTRAN_COMMIT = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
# Every file faster-whisper 1.2.0 downloads for large-v3 (its allow_patterns:
# config.json, preprocessor_config.json, model.bin, tokenizer.json, vocabulary.*).
ASR_ARTIFACT_PATHS = (
    "asr/large-v3/config.json",
    "asr/large-v3/preprocessor_config.json",
    "asr/large-v3/model.bin",
    "asr/large-v3/tokenizer.json",
    "asr/large-v3/vocabulary.json",
)

DEMO_APPROVAL_NOTE = (
    "Approved for internal demo use (homlead 2026-11-12); owner sign-off A5 in "
    "ADR 0047 still open; NVIDIA CUDA libraries and container under NVIDIA EULA, "
    "no redistribution."
)


def _demo_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "asr_provider": "local_whisper",
        "asr_profile": "gpu-p40-large-v3",
        "intelligence_provider": "openai_compatible",
        "local_ai_base_url": DEMO_BASE_URL,
        "llm_model": DEMO_LLM_MODEL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _timeout_of(provider: Any) -> float:
    # Test seam: the adapter keeps the httpx timeout; no public accessor exists.
    return float(provider._timeout.read)


# --- U3 item 1: the chat timeout setting (LOCAL_AI_TIMEOUT_SECONDS) -------------------------


def test_timeout_default_is_60_seconds() -> None:
    assert Settings(_env_file=None).local_ai_timeout_seconds == 60.0


def test_timeout_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_AI_TIMEOUT_SECONDS", "300")
    assert Settings(_env_file=None).local_ai_timeout_seconds == 300.0


def test_timeout_900_is_allowed() -> None:
    settings = Settings(_env_file=None, local_ai_timeout_seconds=900)
    assert settings.local_ai_timeout_seconds == 900.0


@pytest.mark.parametrize("value", ["0", "-30", "900.5", "901"])
def test_timeout_outside_the_range_is_a_settings_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LOCAL_AI_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_local_endpoint_gets_the_configured_timeout() -> None:
    provider = create_intelligence_provider(_demo_settings(local_ai_timeout_seconds=300))
    assert _timeout_of(provider) == 300.0


def test_local_endpoint_keeps_the_60_second_default() -> None:
    provider = create_intelligence_provider(_demo_settings())
    assert _timeout_of(provider) == 60.0


def test_cloud_endpoint_keeps_60_seconds() -> None:
    settings = _demo_settings(
        local_ai_timeout_seconds=300,
        local_ai_base_url="",
        llm_provider="zai",
        zai_api_key="zai-test-key",
    )
    provider = create_intelligence_provider(settings)
    assert _timeout_of(provider) == 60.0


def test_fallback_endpoint_keeps_60_seconds() -> None:
    settings = _demo_settings(
        local_ai_timeout_seconds=300,
        llm_fallback_base_url="http://fallback.test/v1",
        llm_fallback_model="fallback-model",
    )
    fallbacks = create_intelligence_fallbacks(settings)
    assert len(fallbacks) == 1
    assert _timeout_of(fallbacks[0]) == 60.0


def test_env_example_sets_a_valid_active_timeout() -> None:
    match = re.search(r"^LOCAL_AI_TIMEOUT_SECONDS=(\S+)$", ENV_EXAMPLE.read_text(), re.MULTILINE)
    assert match is not None, "LOCAL_AI_TIMEOUT_SECONDS must appear in .env.example"
    assert float(match.group(1)) == 60.0


# --- U3 item 2: the Compose passthrough (x-app-environment) ----------------------------------


@pytest.mark.parametrize(
    ("name", "default"),
    [
        ("LLM_MODEL", "default"),
        ("LOCAL_AI_TIMEOUT_SECONDS", "60"),
        ("WHISPER_MODEL_PATH", ""),
        ("WHISPER_LOCAL_FILES_ONLY", "false"),
        ("HF_HUB_OFFLINE", ""),
        ("MODEL_ARTIFACT_ROOT", ""),
    ],
)
def test_compose_anchor_passes_the_gpu_ai_settings(name: str, default: str) -> None:
    anchor = yaml.safe_load(COMPOSE_ONPREM.read_text(encoding="utf-8"))["x-app-environment"]
    assert anchor.get(name) == f"${{{name}:-{default}}}", name


# --- U3 item 5: the two qualified demo manifests ---------------------------------------------


@pytest.fixture(scope="module")
def demo_registry() -> ManifestRegistry:
    return ManifestRegistry.from_files([ASR_MANIFEST_FILE, LLM_MANIFEST_FILE])


@pytest.fixture(scope="module")
def asr_manifest(demo_registry: ManifestRegistry):
    return demo_registry.get(ASR_MANIFEST_ID, 1)


@pytest.fixture(scope="module")
def llm_manifest(demo_registry: ManifestRegistry):
    return demo_registry.get(LLM_MANIFEST_ID, 1)


def test_both_demo_manifests_load(asr_manifest, llm_manifest) -> None:
    assert asr_manifest is not None
    assert llm_manifest is not None
    for manifest in (asr_manifest, llm_manifest):
        assert manifest.status is ManifestStatus.QUALIFIED
        assert manifest.license_policy == "approved"
        assert not re.fullmatch(r"(?i)unpinned|latest", manifest.model_revision)


def test_asr_manifest_resolves_strict_for_the_demo_profile(demo_registry) -> None:
    identity = create_asr_provider(_demo_settings()).identity()
    resolved = demo_registry.resolve("asr", identity, strict=True)
    assert resolved.manifest_id == ASR_MANIFEST_ID


def test_intelligence_manifest_resolves_strict_for_the_demo_endpoint(demo_registry) -> None:
    identity = create_intelligence_provider(_demo_settings()).identity()
    resolved = demo_registry.resolve("intelligence", identity, strict=True)
    assert resolved.manifest_id == LLM_MANIFEST_ID


@pytest.mark.parametrize(
    "update",
    [
        {"local_ai_base_url": "http://other-ai:8080/v1"},
        {"llm_model": "qwen3-8b-q8_0"},
    ],
    ids=["other-endpoint", "other-model"],
)
def test_another_endpoint_or_model_is_refused(demo_registry, update: dict[str, Any]) -> None:
    identity = create_intelligence_provider(_demo_settings(**update)).identity()
    with pytest.raises(ManifestError):
        demo_registry.resolve("intelligence", identity, strict=True)


def test_another_asr_compute_type_is_refused(demo_registry) -> None:
    settings = _demo_settings(asr_profile="cpu-large-v3")
    identity = create_asr_provider(settings).identity()
    with pytest.raises(ManifestError):
        demo_registry.resolve("asr", identity, strict=True)


def test_demo_files_register_through_model_manifest_paths() -> None:
    settings = _demo_settings(
        model_manifest_paths=",".join(str(path) for path in (ASR_MANIFEST_FILE, LLM_MANIFEST_FILE))
    )
    registry = load_registry(settings)
    resolved = registry.resolve(
        "intelligence", create_intelligence_provider(_demo_settings()).identity(), strict=True
    )
    assert resolved.manifest_id == LLM_MANIFEST_ID


def test_asr_manifest_pins_the_systran_revision(asr_manifest) -> None:
    assert asr_manifest.model_revision == SYSTRAN_COMMIT
    assert asr_manifest.license == "MIT"


def test_llm_manifest_pins_the_qwen_revision(llm_manifest) -> None:
    assert llm_manifest.model_revision == QWEN_REPO_COMMIT
    assert llm_manifest.license == "Apache-2.0"


# C2: a networked manifest cannot declare artifacts; the GGUF pin lives in notes.
def test_llm_manifest_declares_no_artifacts_but_pins_the_gguf_in_notes(llm_manifest) -> None:
    assert llm_manifest.artifacts == []
    assert llm_manifest.endpoint_kind.value == "private"
    for part in (QWEN_REPO, QWEN_REPO_COMMIT, QWEN_GGUF, QWEN_GGUF_SHA256, "Apache-2.0"):
        assert part in llm_manifest.notes
    assert len(llm_manifest.notes or "") <= 500


def test_llm_manifest_identity_equals_the_local_llm_template(llm_manifest) -> None:
    template = local_llm_manifest_template(
        "llama_cpp", model=DEMO_LLM_MODEL, base_url=DEMO_BASE_URL
    )
    assert llm_manifest.provider == template["provider"]
    assert llm_manifest.model == template["model"]
    assert llm_manifest.identity_params == template["identity_params"]
    assert llm_manifest.endpoint == template["endpoint"]
    assert llm_manifest.structured_output.value == template["structured_output"]


# C1: the ASR manifest declares every weight file so the worker verifies them.
def test_asr_manifest_declares_every_weight_file(asr_manifest) -> None:
    assert tuple(artifact.path for artifact in asr_manifest.artifacts) == ASR_ARTIFACT_PATHS
    for artifact in asr_manifest.artifacts:
        assert artifact.kind == "file"
        assert re.fullmatch(r"[0-9a-f]{64}", artifact.sha256), artifact.path
    assert asr_manifest.endpoint_kind.value == "in_process"
    assert asr_manifest.offline_capable is True


def test_asr_artifacts_are_relative_and_report_missing_under_an_empty_root(
    asr_manifest, tmp_path: Path
) -> None:
    report = verify_artifacts(asr_manifest, tmp_path)
    assert report.declared
    assert not report.ok
    assert all(check.status == "missing" for check in report.checks)


def test_both_manifest_notes_carry_the_demo_approval(asr_manifest, llm_manifest) -> None:
    for manifest in (asr_manifest, llm_manifest):
        assert "Approved for internal demo use (homlead 2026-11-12)" in manifest.notes
        assert "ADR 0047 still open" in manifest.notes
