"""Local faster-whisper ASR adapter."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from backend.providers.base import ASRProvider, ProviderError, ProviderIdentity
from backend.providers.schemas import (
    ASRLanguageDecision,
    ASRRequest,
    ASRResult,
    ASRSegment,
    UNKNOWN_SPEAKER_LABEL,
)
from backend.speech.language_policy import (
    POLICY_VERSION,
    LanguageDecision,
    LanguagePolicy,
    decide,
    normalize_language,
)


ModelFactory = Callable[..., Any]
DeviceIndex = int | tuple[int, ...]
_T = TypeVar("_T")


def _device_index_kwarg(device_index: DeviceIndex) -> int | list[int] | None:
    """The ``device_index`` the model factory gets, or ``None`` for the default.

    faster-whisper takes an int or a list of ints.
    """
    indexes = (device_index,) if isinstance(device_index, int) else tuple(device_index)
    if indexes == (0,):
        return None
    if len(indexes) == 1:
        return indexes[0]
    return list(indexes)


def _language_probabilities(info: Any) -> list[tuple[str, float]]:
    """Detection result of a ``transcribe`` call made without a language."""
    probabilities = getattr(info, "all_language_probs", None)
    if probabilities is not None:
        return [(str(language), float(probability)) for language, probability in probabilities]
    language = getattr(info, "language", None)
    probability = getattr(info, "language_probability", None)
    if language and probability is not None:
        return [(str(language), float(probability))]
    return []


class LocalWhisperProvider(ASRProvider):
    """Runs an injected local Whisper configuration without inventing speakers."""

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str = "default",
        model: Optional[Any] = None,
        model_factory: Optional[ModelFactory] = None,
        local_files_only: bool = False,
        model_path: Optional[str] = None,
        device_index: DeviceIndex = 0,
        cpu_threads: int = 0,
        language_policy: LanguagePolicy | None = None,
    ) -> None:
        if not model_name.strip() or not device.strip() or not compute_type.strip():
            raise ValueError("Whisper model, device, and compute type are required")
        if model is not None and model_factory is not None:
            raise ValueError("Provide either a loaded model or a model factory, not both")
        indexes = (device_index,) if isinstance(device_index, int) else tuple(device_index)
        if not indexes or any(index < 0 for index in indexes):
            raise ValueError("Whisper device index must be one or more indexes of 0 or more")
        if cpu_threads < 0:
            raise ValueError("Whisper CPU threads must be 0 (library default) or more")
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._model = model
        self._model_factory = model_factory
        self._model_lock = threading.Lock()
        # Offline runtime: load weights only from the local cache/``model_path``
        # (verified against the manifest's artifacts by preflight); never
        # download at runtime.  Neither changes the model identity.
        self._local_files_only = local_files_only
        self._model_path = model_path.strip() if model_path and model_path.strip() else None
        # Where and how fast the model runs: not part of the identity either.
        self._device_index = device_index if isinstance(device_index, int) else indexes
        self._cpu_threads = cpu_threads
        self._language_policy = language_policy if language_policy is not None else LanguagePolicy()

    def identity(self) -> ProviderIdentity:
        # The compute type changes the numerics of the transcript; the device
        # does not declare a different model, so it is not part of the identity.
        return ProviderIdentity(
            provider="local_whisper",
            model=self._model_name,
            params={"compute_type": self._compute_type},
        )

    @property
    def device(self) -> str:
        return self._device

    @property
    def device_index(self) -> DeviceIndex:
        return self._device_index

    @property
    def cpu_threads(self) -> int:
        return self._cpu_threads

    @property
    def language_policy(self) -> LanguagePolicy:
        return self._language_policy

    def language_policy_identity(self, language: str | None) -> dict | None:
        # An explicit language makes the policy irrelevant: keep the identity
        # byte-identical to the one before HOM-39 so old checkpoints are reused.
        if normalize_language(language) is not None:
            return None
        return self._language_policy.descriptor()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                factory = self._model_factory
                if factory is None:
                    from faster_whisper import WhisperModel

                    factory = WhisperModel
                kwargs: dict[str, Any] = {
                    "device": self._device,
                    "compute_type": self._compute_type,
                }
                if self._local_files_only:
                    kwargs["local_files_only"] = True
                # Only non-default values, so existing model factories keep working.
                device_index = _device_index_kwarg(self._device_index)
                if device_index is not None:
                    kwargs["device_index"] = device_index
                if self._cpu_threads:
                    kwargs["cpu_threads"] = self._cpu_threads
                source = self._model_path or self._model_name
                if self._model_path is not None and not Path(self._model_path).is_dir():
                    raise ProviderError(
                        "asr_model_missing",
                        "The local Whisper model directory is not present",
                        retryable=False,
                    )
                self._model = factory(source, **kwargs)
        return self._model

    @staticmethod
    def _checked_path(audio_path: str) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise ProviderError("audio_not_found", "Audio file was not found", retryable=False)
        if path.stat().st_size == 0:
            raise ProviderError("audio_empty", "Audio file is empty", retryable=False)
        return str(path)

    def _detect(
        self, model: Any, path: str, kwargs: dict[str, Any]
    ) -> tuple[Any, Any, LanguageDecision]:
        """First call without a language. faster-whisper detects the language
        before it returns; the segments generator is lazy and not consumed here."""
        raw_segments, info = model.transcribe(path, **kwargs)
        decision = decide(None, self._language_policy, _language_probabilities(info))
        return raw_segments, info, decision

    def _transcribe_sync(self, request: ASRRequest) -> ASRResult:
        path = self._checked_path(request.audio_path)
        kwargs: dict[str, Any] = {
            "beam_size": request.beam_size,
            "vad_filter": request.vad_filter,
        }
        requested = normalize_language(request.language)

        model = self._get_model()
        if requested is not None:
            decision = decide(requested, self._language_policy)
            raw_segments, info = model.transcribe(path, **kwargs, language=requested)
        else:
            raw_segments, info, decision = self._detect(model, path, kwargs)
            if decision.language != normalize_language(getattr(info, "language", None)):
                # The policy overrides the detection: never read the first
                # generator, decode again with the decided language.
                raw_segments, info = model.transcribe(path, **kwargs, language=decision.language)

        segments = []
        text_parts = []
        for raw_segment in raw_segments:
            text = str(getattr(raw_segment, "text", "")).strip()
            if not text:
                continue
            segment = ASRSegment(
                index=len(segments),
                text=text,
                start_seconds=float(getattr(raw_segment, "start")),
                end_seconds=float(getattr(raw_segment, "end")),
                speaker_label=UNKNOWN_SPEAKER_LABEL,
            )
            segments.append(segment)
            text_parts.append(text)

        segment_duration = max((segment.end_seconds for segment in segments), default=0.0)
        info_duration = float(getattr(info, "duration", 0) or 0)
        return ASRResult(
            provider="local_whisper",
            model=self._model_name,
            language=decision.language,
            text=" ".join(text_parts),
            segments=segments,
            duration_seconds=max(segment_duration, info_duration),
            language_decision=ASRLanguageDecision(
                source=decision.source.value,
                requested=decision.requested,
                detected=decision.detected,
                probability=(
                    None
                    if decision.probability is None
                    else min(max(decision.probability, 0.0), 1.0)
                ),
                reason=decision.reason,
                policy_version=POLICY_VERSION,
            ),
        )

    def _decide_language_sync(self, audio_path: str, requested: str | None) -> LanguageDecision:
        path = self._checked_path(audio_path)
        if normalize_language(requested) is not None:
            return decide(requested, self._language_policy)
        # Same decoding options as a default transcription request.
        defaults = ASRRequest(audio_path=path)
        kwargs = {"beam_size": defaults.beam_size, "vad_filter": defaults.vad_filter}
        _, _, decision = self._detect(self._get_model(), path, kwargs)
        return decision

    @staticmethod
    def _mapped(call: Callable[[], _T]) -> _T:
        try:
            return call()
        except ProviderError:
            raise
        except ImportError as exc:
            raise ProviderError(
                "asr_unavailable",
                "Local Whisper is not installed",
                retryable=False,
            ) from exc
        except Exception as exc:
            raise ProviderError(
                "asr_failed",
                "Local Whisper transcription failed",
                retryable=False,
            ) from exc

    def decide_language(self, audio_path: str, requested: str | None) -> LanguageDecision:
        """The language ``transcribe`` would use, without decoding any text.

        An explicit request makes no model call. An auto request makes only
        the first (detecting) ``transcribe`` call.
        """
        return self._mapped(lambda: self._decide_language_sync(audio_path, requested))

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return await asyncio.to_thread(self._mapped, lambda: self._transcribe_sync(request))


WhisperASRProvider = LocalWhisperProvider
