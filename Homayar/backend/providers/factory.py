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
    TTSProvider,
)
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.local_tts import (
    LocalHTTPTTSProvider,
    PiperCLITTSProvider,
    UnavailableTTSProvider,
)
from backend.providers.mocks import (
    MockASRProvider,
    MockDiarizationProvider,
    MockIntelligenceProvider,
    MockTTSProvider,
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
            # Offline runtime knobs; optional until the settings carry them.
            local_files_only=bool(getattr(settings, "whisper_local_files_only", False)),
            model_path=getattr(settings, "whisper_model_path", None) or None,
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


def _setting(settings: Settings, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return value.strip() if isinstance(value, str) else default


def _voice_map(value: str) -> dict[str, str]:
    """``voice_id=/path/model.onnx`` entries, comma separated."""
    voices: dict[str, str] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        voice, _, path = entry.partition("=")
        if not voice.strip() or not path.strip() or voice.strip() in voices:
            raise ValueError(f"Invalid TTS voice entry {entry!r}")
        voices[voice.strip()] = path.strip()
    return voices


def create_tts_provider(
    settings: Settings, *, client: httpx.AsyncClient | None = None
) -> TTSProvider:
    """Speech synthesis adapter.  Defaults to the explicit unavailable adapter:
    the settings fields (``tts_provider``, ``tts_piper_binary``,
    ``tts_voices``, ``tts_default_voice``, ``tts_base_url``, ``tts_model``)
    are optional until the configuration carries them."""
    name = _provider_name(_setting(settings, "tts_provider", "unavailable") or "unavailable")
    if name == "unavailable":
        return UnavailableTTSProvider()
    if name in {"mock", "mock_tts", "deterministic_mock"}:
        _require_mock_permission(settings)
        return MockTTSProvider(allow_mock=True)
    try:
        if name in {"piper", "piper_cli", "local_piper"}:
            voices = _voice_map(_setting(settings, "tts_voices"))
            return PiperCLITTSProvider(
                binary=_setting(settings, "tts_piper_binary", "piper") or "piper",
                voices=voices,
                default_voice=_setting(settings, "tts_default_voice") or next(iter(voices), ""),
            )
        if name in {"openai_compatible_tts", "local_http"}:
            return LocalHTTPTTSProvider(
                base_url=_setting(settings, "tts_base_url"),
                model=_setting(settings, "tts_model"),
                default_voice=_setting(settings, "tts_default_voice"),
                client=client,
            )
    except ValueError as exc:
        raise ProviderError(
            "tts_provider_unconfigured", f"Speech synthesis is misconfigured: {exc}"
        ) from exc
    raise _unsupported_provider("tts")


@dataclass(frozen=True)
class ProviderBundle:
    asr: ASRProvider
    diarization: DiarizationProvider
    intelligence: IntelligenceProvider
    # Same-policy fallback candidates per stage, in configured order.
    fallbacks: Mapping[str, tuple[object, ...]] = field(default_factory=dict)
    # Speech synthesis (local runtime); not a processing stage (registry.STAGES).
    tts: TTSProvider | None = None


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

    def create_tts(self) -> TTSProvider:
        return create_tts_provider(self._settings)

    def create_all(self) -> ProviderBundle:
        fallbacks = create_intelligence_fallbacks(self._settings, client=self._intelligence_client)
        return ProviderBundle(
            asr=self.create_asr(),
            diarization=self.create_diarization(),
            intelligence=self.create_intelligence(),
            fallbacks={"intelligence": fallbacks} if fallbacks else {},
            tts=self.create_tts(),
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
