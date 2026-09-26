"""Local faster-whisper ASR adapter."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from backend.providers.base import ASRProvider, ProviderError, ProviderIdentity
from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    ASRSegment,
    UNKNOWN_SPEAKER_LABEL,
)


ModelFactory = Callable[..., Any]


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
    ) -> None:
        if not model_name.strip() or not device.strip() or not compute_type.strip():
            raise ValueError("Whisper model, device, and compute type are required")
        if model is not None and model_factory is not None:
            raise ValueError("Provide either a loaded model or a model factory, not both")
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._model = model
        self._model_factory = model_factory
        self._model_lock = threading.Lock()

    def identity(self) -> ProviderIdentity:
        # The compute type changes the numerics of the transcript; the device
        # does not declare a different model, so it is not part of the identity.
        return ProviderIdentity(
            provider="local_whisper",
            model=self._model_name,
            params={"compute_type": self._compute_type},
        )

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                factory = self._model_factory
                if factory is None:
                    from faster_whisper import WhisperModel

                    factory = WhisperModel
                self._model = factory(
                    self._model_name,
                    device=self._device,
                    compute_type=self._compute_type,
                )
        return self._model

    def _transcribe_sync(self, request: ASRRequest) -> ASRResult:
        path = Path(request.audio_path)
        if not path.is_file():
            raise ProviderError("audio_not_found", "Audio file was not found", retryable=False)
        if path.stat().st_size == 0:
            raise ProviderError("audio_empty", "Audio file is empty", retryable=False)

        kwargs = {
            "beam_size": request.beam_size,
            "vad_filter": request.vad_filter,
        }
        if request.language not in {None, "auto"}:
            kwargs["language"] = request.language

        model = self._get_model()
        raw_segments, info = model.transcribe(str(path), **kwargs)
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
        detected_language = getattr(info, "language", None)
        language = detected_language or request.language or "unknown"
        if language == "auto":
            language = "unknown"
        return ASRResult(
            provider="local_whisper",
            model=self._model_name,
            language=language,
            text=" ".join(text_parts),
            segments=segments,
            duration_seconds=max(segment_duration, info_duration),
        )

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        try:
            return await asyncio.to_thread(self._transcribe_sync, request)
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


WhisperASRProvider = LocalWhisperProvider
