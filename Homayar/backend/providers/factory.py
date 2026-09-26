"""Production provider selection from validated application settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from backend.config import Settings
from backend.providers import padyar
from backend.providers.base import (
    ASRProvider,
    DiarizationProvider,
    IntelligenceProvider,
    MockProviderDisabledError,
    ProviderError,
)
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.mocks import (
    MockASRProvider,
    MockDiarizationProvider,
    MockIntelligenceProvider,
)
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.whisper import LocalWhisperProvider

_MOCK_ENVIRONMENTS = frozenset({"development", "test"})


def _provider_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _require_mock_permission(settings: Settings) -> None:
    if settings.environment not in _MOCK_ENVIRONMENTS or not settings.allow_mock_providers:
        raise MockProviderDisabledError()


def _unsupported_provider(kind: str) -> ProviderError:
    return ProviderError(
        f"unsupported_{kind}_provider",
        f"Configured {kind} provider is not supported",
        retryable=False,
    )


def create_asr_provider(settings: Settings) -> ASRProvider:
    name = _provider_name(settings.asr_provider)
    if name == "local_whisper":
        return LocalWhisperProvider(
            model_name=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    if name in {"mock", "mock_asr", "deterministic_mock"}:
        _require_mock_permission(settings)
        return MockASRProvider(allow_mock=True)
    if name in padyar.NAMES:
        raise padyar.contract_unavailable("speech recognition")
    raise _unsupported_provider("asr")


def create_diarization_provider(settings: Settings) -> DiarizationProvider:
    name = _provider_name(settings.diarization_provider)
    if name == "unavailable":
        return UnavailableDiarizationProvider()
    if name in {"mock", "mock_diarization", "deterministic_mock"}:
        _require_mock_permission(settings)
        return MockDiarizationProvider(allow_mock=True)
    if name in padyar.NAMES:
        raise padyar.contract_unavailable("diarization")
    raise _unsupported_provider("diarization")


def _compatible_endpoint(settings: Settings) -> tuple[str, str]:
    if settings.local_ai_base_url.strip():
        return settings.local_ai_base_url, ""

    backend = _provider_name(settings.llm_provider)
    candidates: list[tuple[str, str]] = []
    if backend in {"auto", "zai"} and settings.zai_base_url.strip() and settings.zai_api_key:
        candidates.append((settings.zai_base_url, settings.zai_api_key))
    if (
        backend in {"auto", "gapgpt"}
        and settings.gapgpt_base_url.strip()
        and settings.gapgpt_api_key
    ):
        candidates.append((settings.gapgpt_base_url, settings.gapgpt_api_key))
    if candidates:
        return candidates[0]
    if backend not in {"auto", "local", "zai", "gapgpt"}:
        raise _unsupported_provider("intelligence")
    raise ProviderError(
        "intelligence_provider_unconfigured",
        "OpenAI-compatible intelligence provider is not configured",
        retryable=False,
    )


def create_intelligence_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> IntelligenceProvider:
    name = _provider_name(settings.intelligence_provider)
    if name == "openai_compatible":
        endpoint, api_key = _compatible_endpoint(settings)
        return OpenAICompatibleIntelligenceProvider(
            base_url=endpoint,
            model=settings.llm_model,
            api_key=api_key,
            client=client,
        )
    if name in {"mock", "mock_intelligence", "deterministic_mock"}:
        _require_mock_permission(settings)
        return MockIntelligenceProvider(allow_mock=True)
    if name in padyar.NAMES:
        raise padyar.contract_unavailable("analysis")
    raise _unsupported_provider("intelligence")


def create_intelligence_fallbacks(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[IntelligenceProvider, ...]:
    """The configured same-policy fallback analysis endpoint (WP08-T05), if any.

    It is only a *candidate*: the workspace policy must approve fallback and
    the endpoint must pass the same eligibility filter as the primary."""
    if not settings.llm_fallback_base_url.strip():
        return ()
    return (
        OpenAICompatibleIntelligenceProvider(
            base_url=settings.llm_fallback_base_url,
            model=settings.llm_fallback_model,
            api_key=settings.llm_fallback_api_key,
            client=client,
        ),
    )


@dataclass(frozen=True)
class ProviderBundle:
    asr: ASRProvider
    diarization: DiarizationProvider
    intelligence: IntelligenceProvider
    # Same-policy fallback candidates per stage, in configured order.
    fallbacks: Mapping[str, tuple[object, ...]] = field(default_factory=dict)


class ProviderFactory:
    def __init__(
        self,
        settings: Settings,
        *,
        intelligence_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._intelligence_client = intelligence_client

    def create_asr(self) -> ASRProvider:
        return create_asr_provider(self._settings)

    def create_diarization(self) -> DiarizationProvider:
        return create_diarization_provider(self._settings)

    def create_intelligence(self) -> IntelligenceProvider:
        return create_intelligence_provider(
            self._settings,
            client=self._intelligence_client,
        )

    def create_all(self) -> ProviderBundle:
        fallbacks = create_intelligence_fallbacks(self._settings, client=self._intelligence_client)
        return ProviderBundle(
            asr=self.create_asr(),
            diarization=self.create_diarization(),
            intelligence=self.create_intelligence(),
            fallbacks={"intelligence": fallbacks} if fallbacks else {},
        )


def create_provider_bundle(
    settings: Settings,
    *,
    intelligence_client: httpx.AsyncClient | None = None,
) -> ProviderBundle:
    return ProviderFactory(
        settings,
        intelligence_client=intelligence_client,
    ).create_all()
