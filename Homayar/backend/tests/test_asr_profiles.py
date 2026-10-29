"""HOM-39: named ASR model profiles, their settings and how the factory uses them.

Every profile names a builtin manifest whose identity equals the profile's,
so routing finds it. The settings refuse an unknown profile, a profile for a
provider that is not local Whisper, and a language policy that cannot work.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from backend.config import Settings
from backend.providers.asr_profiles import (
    ASR_PROFILES,
    ASRProfile,
    get_asr_profile,
    list_asr_profiles,
)
from backend.providers.factory import create_asr_provider
from backend.providers.registry import BUILTIN_MANIFESTS, ManifestRegistry, manifest_digest
from backend.providers.schemas import ASRRequest
from backend.providers.whisper import LocalWhisperProvider
from backend.speech.language_policy import LanguagePolicy

EXPECTED = {
    "cpu-base": ("base", "cpu", "int8", "asr.local-whisper.base"),
    "cpu-small": ("small", "cpu", "int8", "asr.local-whisper.small"),
    "cpu-medium": ("medium", "cpu", "int8", "asr.local-whisper.medium"),
    "cpu-large-v3-turbo": ("large-v3-turbo", "cpu", "int8", "asr.local-whisper.large-v3-turbo"),
    "cpu-large-v3": ("large-v3", "cpu", "int8", "asr.local-whisper.large-v3"),
    "gpu-p40-large-v3": (
        "large-v3",
        "cuda",
        "int8_float32",
        "asr.local-whisper.large-v3-int8-float32",
    ),
}


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "local_ai_base_url": "http://intelligence.test/v1",
        "llm_model": "conversation-model",
        "allow_mock_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


# --- profiles -------------------------------------------------------------------


def test_profile_table_is_exact() -> None:
    assert set(ASR_PROFILES) == set(EXPECTED)
    for name, (model, device, compute_type, manifest_id) in EXPECTED.items():
        profile = ASR_PROFILES[name]
        assert isinstance(profile, ASRProfile)
        assert profile.name == name
        assert (profile.model, profile.device, profile.compute_type, profile.manifest_id) == (
            model,
            device,
            compute_type,
            manifest_id,
        )
        assert profile.device_index == (0,)
        assert profile.cpu_threads == 0


def test_profiles_are_read_only_and_listed_by_name() -> None:
    assert isinstance(ASR_PROFILES, MappingProxyType)
    with pytest.raises(TypeError):
        ASR_PROFILES["x"] = ASR_PROFILES["cpu-base"]  # type: ignore[index]
    with pytest.raises(AttributeError):
        ASR_PROFILES["cpu-base"].model = "tiny"  # type: ignore[misc]
    names = [profile.name for profile in list_asr_profiles()]
    assert names == sorted(EXPECTED)


def test_get_asr_profile() -> None:
    assert get_asr_profile("cpu-small") is ASR_PROFILES["cpu-small"]
    with pytest.raises(ValueError) as exc:
        get_asr_profile("nope")
    for name in EXPECTED:
        assert name in str(exc.value)


def test_every_profile_has_a_matching_builtin_manifest() -> None:
    registry = ManifestRegistry.from_files([BUILTIN_MANIFESTS])
    by_id = {manifest.manifest_id: manifest for manifest in registry.all()}
    for profile in list_asr_profiles():
        manifest = by_id[profile.manifest_id]
        assert manifest.stage == "asr"
        assert manifest.provider == "local_whisper"
        assert manifest.model == profile.model
        assert manifest.identity_params == {"compute_type": profile.compute_type}
        assert manifest.status.value == "development"
        assert manifest.model_revision == "unpinned"
        assert manifest.languages == ["*"]
        assert manifest.offline_capable is True
        # Routing resolves the provider the profile builds to this manifest.
        provider = LocalWhisperProvider(
            model_name=profile.model, device=profile.device, compute_type=profile.compute_type
        )
        assert manifest in registry.candidates("asr", provider.identity())


def test_existing_asr_manifests_are_unchanged() -> None:
    # Full-content digests of the three ASR manifests at f856f60 (before HOM-39).
    # Operators may have qualified or pinned them, so no field may change.
    expected = {
        "asr.local-whisper.base": (
            "13bc5959fd1eb7a53452473b84137740aa9c0160ff89cc4e772c4291e4f73855"
        ),
        "asr.local-whisper.large-v3": (
            "5cfe6dd7d77f552d2549d08c38985b3e64b78a90ab814834013d71cb649bd949"
        ),
        "asr.local-whisper.large-v3-fp16": (
            "2230a1f2592418468fd543d6b9cc1b4f9e65c46171d30332c1cb20363995049f"
        ),
    }
    registry = ManifestRegistry.from_files([BUILTIN_MANIFESTS])
    digests = {manifest.manifest_id: manifest_digest(manifest) for manifest in registry.all()}
    assert {manifest_id: digests[manifest_id] for manifest_id in expected} == expected


# --- settings -------------------------------------------------------------------


def test_settings_defaults() -> None:
    settings = _settings()
    assert settings.asr_profile == ""
    assert settings.asr_allowed_languages == "fa,en"
    assert settings.asr_default_language == "fa"
    assert settings.asr_language_min_probability == 0.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_profile": "nope"},
        {"asr_profile": "cpu-base", "asr_provider": "mock"},
        {"asr_default_language": "de"},
        {"asr_default_language": "auto"},
        {"asr_allowed_languages": ""},
        {"asr_allowed_languages": " "},
        {"asr_allowed_languages": ","},
        {"asr_allowed_languages": "auto"},
        {"asr_language_min_probability": 1.5},
        {"asr_language_min_probability": -0.01},
    ],
)
def test_settings_reject_invalid_asr_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"asr_profile": "cpu-large-v3"},
        {"asr_profile": "gpu-p40-large-v3", "asr_provider": " Local-Whisper "},
        {"asr_provider": "mock"},
        {"asr_default_language": "en"},
        {"asr_allowed_languages": "fa-IR, EN", "asr_default_language": "FA"},
        {"asr_language_min_probability": 0.0},
        {"asr_language_min_probability": 1.0},
    ],
)
def test_settings_accept_valid_asr_values(overrides: dict[str, Any]) -> None:
    _settings(**overrides)


# --- factory --------------------------------------------------------------------


def _model_factory_calls(provider: LocalWhisperProvider, tmp_path: Path) -> list[tuple]:
    calls: list[tuple] = []

    def factory(source: str, **kwargs: Any) -> Any:
        calls.append((source, kwargs))
        return SimpleNamespace(
            transcribe=lambda path, **kw: (iter([]), SimpleNamespace(language="fa", duration=0.0))
        )

    provider._model_factory = factory  # test seam: no real model is loaded
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    asyncio.run(provider.transcribe(ASRRequest(audio_path=str(audio), language="fa")))
    return calls


def test_factory_without_profile_keeps_legacy_settings(tmp_path: Path) -> None:
    provider = create_asr_provider(
        _settings(whisper_model="small", whisper_device="cpu", whisper_compute_type="int8")
    )
    assert isinstance(provider, LocalWhisperProvider)
    assert provider.identity().model == "small"
    assert provider.device == "cpu"
    assert provider.device_index == 0
    assert provider.cpu_threads == 0
    assert provider.language_policy == LanguagePolicy()
    assert _model_factory_calls(provider, tmp_path) == [
        ("small", {"device": "cpu", "compute_type": "int8"})
    ]


def test_factory_with_profile_ignores_legacy_model_settings(tmp_path: Path) -> None:
    provider = create_asr_provider(
        _settings(
            asr_profile="gpu-p40-large-v3",
            whisper_model="tiny",
            whisper_device="cpu",
            whisper_compute_type="float32",
            whisper_local_files_only=True,
            asr_allowed_languages="fa,en,ar",
            asr_default_language="fa",
            asr_language_min_probability=0.6,
        )
    )
    assert isinstance(provider, LocalWhisperProvider)
    assert provider.identity().as_manifest() == {
        "provider": "local_whisper",
        "model": "large-v3",
        "params": {"compute_type": "int8_float32"},
    }
    assert provider.device == "cuda"
    assert provider.language_policy == LanguagePolicy(
        allowed=("fa", "en", "ar"), default="fa", min_probability=0.6
    )
    assert _model_factory_calls(provider, tmp_path) == [
        (
            "large-v3",
            {"device": "cuda", "compute_type": "int8_float32", "local_files_only": True},
        )
    ]


def test_factory_with_profile_keeps_the_model_path(tmp_path: Path) -> None:
    model_dir = tmp_path / "weights"
    model_dir.mkdir()
    provider = create_asr_provider(
        _settings(asr_profile="cpu-medium", whisper_model_path=str(model_dir))
    )
    calls = _model_factory_calls(provider, tmp_path)
    assert calls == [(str(model_dir), {"device": "cpu", "compute_type": "int8"})]


# --- provider device options ----------------------------------------------------


@pytest.mark.parametrize(
    ("device_index", "cpu_threads", "extra"),
    [
        (0, 0, {}),
        ((0,), 0, {}),
        (1, 0, {"device_index": 1}),
        ((1,), 0, {"device_index": 1}),
        ((0, 1), 0, {"device_index": [0, 1]}),
        (0, 6, {"cpu_threads": 6}),
    ],
)
def test_device_options_reach_the_model_only_when_not_default(
    tmp_path: Path, device_index: Any, cpu_threads: int, extra: dict[str, Any]
) -> None:
    provider = LocalWhisperProvider(
        model_name="base",
        device="cpu",
        compute_type="int8",
        device_index=device_index,
        cpu_threads=cpu_threads,
    )
    assert provider.device_index == device_index
    assert provider.cpu_threads == cpu_threads
    assert _model_factory_calls(provider, tmp_path) == [
        ("base", {"device": "cpu", "compute_type": "int8", **extra})
    ]


def test_multi_gpu_index_reads_back_as_given() -> None:
    provider = LocalWhisperProvider(
        model_name="large-v3", device="cuda", compute_type="int8_float32", device_index=(0, 1)
    )
    assert provider.device_index == (0, 1)


@pytest.mark.parametrize(
    "kwargs",
    [{"cpu_threads": -1}, {"device_index": -1}, {"device_index": ()}, {"device_index": (0, -2)}],
)
def test_invalid_device_options_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        LocalWhisperProvider(model_name="base", device="cpu", compute_type="int8", **kwargs)
