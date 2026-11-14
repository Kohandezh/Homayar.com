"""Local faster-whisper ASR adapter."""

from __future__ import annotations

import asyncio
import numbers
import threading
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from backend.providers import warm_models
from backend.providers.base import ASRProvider, ProviderError, ProviderIdentity
from backend.providers.schemas import (
    ASRLanguageDecision,
    ASRNonSpeechSpan,
    ASRRequest,
    ASRResult,
    ASRSegment,
    UNKNOWN_SPEAKER_LABEL,
)
from backend.services.logging_service import logger
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

# Silero VAD options (faster-whisper ``VadOptions`` keyword arguments) for the
# one rescue call made when the default VAD kept no audio. On 8 kHz phone audio
# the default threshold 0.5 can remove real speech (ADR 0044, amendment HOM-40).
RESCUE_VAD_PARAMETERS: dict[str, Any] = {"threshold": 0.25}


def _default_audio_loader(path: str) -> Any:
    """Mono float32 samples at 16 kHz: the decode faster-whisper does for a
    path (HOM-348). Imported here so the module loads without the library."""
    from faster_whisper.audio import decode_audio

    return decode_audio(path, sampling_rate=16000)


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


def _vad_kept_no_audio(info: Any) -> bool:
    """VAD removed all audio (``duration_after_vad`` is a number <= 0).

    faster-whisper still detects a language on the empty, padded features, so
    its probabilities say nothing about the call (SPEC 13).
    """
    kept = getattr(info, "duration_after_vad", None)
    return isinstance(kept, numbers.Real) and not isinstance(kept, bool) and kept <= 0


def _language_probabilities(info: Any) -> list[tuple[str, float]]:
    """Detection result of a ``transcribe`` call made without a language."""
    if _vad_kept_no_audio(info):
        return []  # decide() then falls back with reason "no_speech"
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
        batch_size: int = 1,
        nonspeech_gate: bool = True,
        audio_loader: Optional[Callable[[str], Any]] = None,
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
        if not 1 <= batch_size <= 16:
            raise ValueError("Whisper batch size must be between 1 and 16")
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
        # An injected model bypasses the warm cache completely (HOM-41).
        self._injected = model is not None
        self._batch_size = batch_size
        # Music and tone gate (HOM-348, ADR 0078): drop decoded segments whose
        # audio is music or a tone. ``audio_loader`` injects the samples a test
        # wants; ``None`` decodes the path like faster-whisper does.
        self._nonspeech_gate = nonspeech_gate
        self._audio_loader = audio_loader
        # Per-instance batched pipeline for an injected model (never cached).
        self._injected_pipeline: Any = None

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
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def language_policy(self) -> LanguagePolicy:
        return self._language_policy

    def language_policy_identity(self, language: str | None) -> dict | None:
        # An explicit language makes the policy irrelevant: keep the identity
        # byte-identical to the one before HOM-39 so old checkpoints are reused.
        if normalize_language(language) is not None:
            return None
        return self._language_policy.descriptor()

    def decode_identity(self) -> dict | None:
        """How a transcript is decoded; batching changes the numerics, so a
        batched result must not be reused as an unbatched checkpoint (HOM-41).
        ``None`` keeps the identity as before."""
        if self._batch_size == 1:
            return None
        return {"batch_size": self._batch_size}

    def nonspeech_identity(self) -> dict | None:
        """The music and tone gate a checkpoint records (HOM-348, ADR 0078).
        ``None`` (gate off) keeps the identity as before."""
        if not self._nonspeech_gate:
            return None
        from backend.speech.nonspeech import NONSPEECH_VERSION

        return {"version": NONSPEECH_VERSION}

    def _cache_key(self) -> tuple:
        """Every value that changes what is loaded. The language policy is not
        part of it: it changes decisions, not the loaded weights."""
        indexes = (
            (self._device_index,)
            if isinstance(self._device_index, int)
            else tuple(self._device_index)
        )
        factory: Any = (
            self._model_factory
            if self._model_factory is not None
            else "faster_whisper.WhisperModel"
        )
        return (
            "local_whisper",
            self._model_path or self._model_name,
            self._device,
            self._compute_type,
            indexes,
            self._cpu_threads,
            self._local_files_only,
            factory,
        )

    def _load_model(self) -> Any:
        # Runs on a warm-cache miss only, so the directory check cannot make a
        # cached model fail (HOM-41).
        if self._model_path is not None and not Path(self._model_path).is_dir():
            raise ProviderError(
                "asr_model_missing",
                "The local Whisper model directory is not present",
                retryable=False,
            )
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
        return factory(self._model_path or self._model_name, **kwargs)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                self._model = warm_models.get_or_load(self._cache_key(), self._load_model)
        return self._model

    def _decode_engine(self, vad_filter: bool) -> Any:
        """What a decode call runs on: the batched pipeline when batching
        applies, else the model itself. Language detection never uses this."""
        if self._batch_size == 1 or not vad_filter:
            return self._get_model()
        if self._injected:
            if self._injected_pipeline is None:
                from faster_whisper import BatchedInferencePipeline

                self._injected_pipeline = BatchedInferencePipeline(model=self._model)
            return self._injected_pipeline

        def loader() -> Any:
            from faster_whisper import BatchedInferencePipeline

            return BatchedInferencePipeline(model=self._get_model())

        # One pipeline per cached model, stored with the model in the warm
        # cache under the model key plus ("batched",).
        return warm_models.get_or_load((*self._cache_key(), ("batched",)), loader)

    def _decode_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """The kwargs of a decode call: today's plus ``batch_size`` when the
        call goes through the batched pipeline."""
        if self._batch_size > 1 and kwargs.get("vad_filter"):
            return {**kwargs, "batch_size": self._batch_size}
        return kwargs

    @staticmethod
    def _checked_path(audio_path: str) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise ProviderError("audio_not_found", "Audio file was not found", retryable=False)
        if path.stat().st_size == 0:
            raise ProviderError("audio_empty", "Audio file is empty", retryable=False)
        return str(path)

    @staticmethod
    def _transcribe_with_rescue(
        engine: Any, path: str, kwargs: dict[str, Any]
    ) -> tuple[Any, Any, dict[str, Any]]:
        """One ``transcribe`` call, and one rescue call with
        ``RESCUE_VAD_PARAMETERS`` when VAD kept no audio (HOM-40). ``engine``
        is the model, or the batched pipeline when batching applies.

        Returns the kwargs of the call that gave the result, so a second
        decode uses the same VAD options.
        """
        raw_segments, info = engine.transcribe(path, **kwargs)
        if kwargs["vad_filter"] and _vad_kept_no_audio(info):
            kwargs = {**kwargs, "vad_parameters": RESCUE_VAD_PARAMETERS}
            raw_segments, info = engine.transcribe(path, **kwargs)
        return raw_segments, info, kwargs

    def _detect(
        self, model: Any, path: str, kwargs: dict[str, Any]
    ) -> tuple[Any, Any, LanguageDecision, dict[str, Any]]:
        """First call without a language. faster-whisper detects the language
        before it returns; the segments generator is lazy and not consumed here."""
        raw_segments, info, kwargs = self._transcribe_with_rescue(model, path, kwargs)
        decision = decide(None, self._language_policy, _language_probabilities(info))
        return raw_segments, info, decision, kwargs

    def _drop_nonspeech(
        self, path: str, segments: list[ASRSegment]
    ) -> tuple[list[ASRSegment], list[ASRNonSpeechSpan]]:
        """Music and tone segments never become text (HOM-348, ADR 0078).

        Fail open: any error, including a missing numpy or faster-whisper,
        keeps the segments exactly as they are, and the result carries no
        non-speech spans. A segment whose clipped span is empty or under
        1.5 s is speech (amendments 1 and 4); a removed segment keeps its
        own, unclipped times.
        """
        try:
            from backend.speech.nonspeech import classify_span

            loader = self._audio_loader or _default_audio_loader
            samples = loader(path)
            kept: list[ASRSegment] = []
            spans: list[ASRNonSpeechSpan] = []
            for segment in segments:
                kind = classify_span(samples, 16_000, segment.start_seconds, segment.end_seconds)
                if kind == "speech":
                    kept.append(segment)
                else:
                    spans.append(
                        ASRNonSpeechSpan(
                            start_seconds=segment.start_seconds,
                            end_seconds=segment.end_seconds,
                            kind=kind,
                        )
                    )
        except Exception as exc:  # the gate must never fail a transcript
            logger.warning("ASR_NONSPEECH_GATE_FAILED error=%s", type(exc).__name__)
            return segments, []
        spans.sort(key=lambda span: span.start_seconds)
        reindexed = [
            segment.model_copy(update={"index": index}) for index, segment in enumerate(kept)
        ]
        return reindexed, spans

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
            raw_segments, info, _ = self._transcribe_with_rescue(
                self._decode_engine(request.vad_filter),
                path,
                self._decode_kwargs({**kwargs, "language": requested}),
            )
            if request.vad_filter and _vad_kept_no_audio(info):
                # The rescue call kept no audio either: no speech (F26).
                raw_segments = ()
        else:
            raw_segments, info, decision, kwargs = self._detect(model, path, kwargs)
            if _vad_kept_no_audio(info):
                # VAD found no speech: any decoded text would be made up (F26).
                # Do not read the first generator and do not decode again.
                raw_segments = ()
            elif decision.language != normalize_language(getattr(info, "language", None)):
                # The policy overrides the detection: never read the first
                # generator, decode again with the decided language.
                raw_segments, info = self._decode_engine(request.vad_filter).transcribe(
                    path, **self._decode_kwargs({**kwargs, "language": decision.language})
                )

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

        # Before the gate: every decoded segment with text sets the duration,
        # so the gate never changes it (amendment 2).
        segment_duration = max((segment.end_seconds for segment in segments), default=0.0)

        non_speech: list[ASRNonSpeechSpan] = []
        if self._nonspeech_gate and segments:
            segments, non_speech = self._drop_nonspeech(path, segments)
            text_parts = [segment.text for segment in segments]

        info_duration = float(getattr(info, "duration", 0) or 0)
        return ASRResult(
            provider="local_whisper",
            model=self._model_name,
            language=decision.language,
            text=" ".join(text_parts),
            segments=segments,
            non_speech=non_speech,
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
        _, _, decision, _ = self._detect(self._get_model(), path, kwargs)
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
        the first (detecting) ``transcribe`` call, plus the same rescue call
        ``transcribe`` makes when VAD kept no audio.
        """
        return self._mapped(lambda: self._decide_language_sync(audio_path, requested))

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return await asyncio.to_thread(self._mapped, lambda: self._transcribe_sync(request))


WhisperASRProvider = LocalWhisperProvider
