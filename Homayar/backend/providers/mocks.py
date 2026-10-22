"""Explicitly enabled deterministic providers for isolated tests and demos."""

from __future__ import annotations

import hashlib
import io
import wave

from backend.providers.base import (
    ASRProvider,
    DiarizationProvider,
    EmailProvider,
    IntelligenceProvider,
    MockProviderDisabledError,
    ProviderError,
    ProviderIdentity,
    SMSProvider,
    TTSProvider,
    text_digest,
)
from backend.providers.schemas import (
    SUMMARY_FIELD_NAMES,
    ASRRequest,
    ASRResult,
    ASRSegment,
    DeliveryStatus,
    DiarizationRequest,
    DiarizationResult,
    DiarizationStatus,
    EmailRequest,
    EmailResult,
    ClaimStatus,
    IntelligenceRequest,
    IntelligenceResult,
    ProviderIssue,
    SMSRequest,
    SMSResult,
    SummaryField,
    SummaryFields,
    TTSRequest,
    TTSResult,
    TTSUsageReport,
    UNKNOWN_SPEAKER_LABEL,
)


def _require_mock_permission(allow_mock: bool) -> None:
    if allow_mock is not True:
        raise MockProviderDisabledError()


def _stable_id(namespace: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()[:24]
    return f"mock-{namespace}-{digest}"


class MockASRProvider(ASRProvider):
    def __init__(
        self,
        *,
        allow_mock: bool = False,
        transcript: str = "Deterministic mock transcript.",
        language: str = "en",
    ) -> None:
        _require_mock_permission(allow_mock)
        if not transcript.strip():
            raise ValueError("Mock transcript cannot be empty")
        self._transcript = transcript.strip()
        self._language = language

    def identity(self) -> ProviderIdentity:
        # The configured transcript *is* the model of this mock: a different
        # canned transcript is a different result and must not share checkpoints.
        return ProviderIdentity(
            provider="mock_asr",
            model="deterministic-v1",
            params={"transcript_sha256": text_digest(self._transcript), "language": self._language},
        )

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        language = request.language if request.language not in {None, "auto"} else self._language
        return ASRResult(
            provider="mock_asr",
            model="deterministic-v1",
            language=language,
            text=self._transcript,
            segments=[
                ASRSegment(
                    index=0,
                    text=self._transcript,
                    start_seconds=0,
                    end_seconds=0,
                    speaker_label=UNKNOWN_SPEAKER_LABEL,
                )
            ],
            duration_seconds=0,
            is_mock=True,
        )


class MockDiarizationProvider(DiarizationProvider):
    def __init__(self, *, allow_mock: bool = False) -> None:
        _require_mock_permission(allow_mock)

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="mock_diarization", model="deterministic-v1")

    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        return DiarizationResult(
            provider="mock_diarization",
            status=DiarizationStatus.UNAVAILABLE,
            issue=ProviderIssue(
                code="diarization_unavailable",
                message="Mock diarization does not assign speakers",
                retryable=False,
            ),
            is_mock=True,
        )


class MockIntelligenceProvider(IntelligenceProvider):
    def __init__(self, *, allow_mock: bool = False, max_summary_chars: int = 500) -> None:
        _require_mock_permission(allow_mock)
        if max_summary_chars <= 0:
            raise ValueError("max_summary_chars must be positive")
        self._max_summary_chars = max_summary_chars

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="mock_intelligence",
            model="deterministic-v1",
            params={"max_summary_chars": self._max_summary_chars},
        )

    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        transcript = request.transcript_text or " ".join(turn.text for turn in request.turns)
        normalized = " ".join(transcript.split())
        summary = normalized[: self._max_summary_chars]
        return IntelligenceResult(
            provider="mock_intelligence",
            model="deterministic-v1",
            summary=summary,
            # The canned summary is the transcript text itself: every supplied
            # turn grounds it. No turns (text-only request) means no evidence.
            summary_source_turn_indexes=list(range(len(request.turns))),
            action_items=[],
            insights=[],
            # HOM-61: a mock states nothing it cannot know, so every structured
            # field abstains and there are no decisions or key moments.
            summary_fields=SummaryFields(
                **{
                    name: SummaryField(status=ClaimStatus.NOT_STATED)
                    for name in SUMMARY_FIELD_NAMES
                }
            ),
            # HOM-53: the same for the taxonomy extraction: every dimension not_stated.
            extraction={
                name: {"status": "not_stated", "values": []}
                for name in ("intent", "topic", "entity", "outcome")
            },
            is_mock=True,
        )


class MockTTSProvider(TTSProvider):
    """Deterministic silent WAV (10 ms per input character, capped at 30 s),
    marked ``is_mock``: a stand-in for tests and demos, never speech."""

    def __init__(self, *, allow_mock: bool = False, sample_rate: int = 16000) -> None:
        _require_mock_permission(allow_mock)
        if not 8000 <= sample_rate <= 48000:
            raise ValueError("Mock sample rate must be between 8000 and 48000")
        self._sample_rate = sample_rate

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="mock_tts",
            model="deterministic-v1",
            params={"sample_rate": self._sample_rate},
        )

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        if request.format != "wav":
            raise ProviderError("tts_format_unsupported", "The mock synthesizer only emits WAV")
        rate = request.sample_rate or self._sample_rate
        frames = int(rate * min(len(request.text) * 0.01, 30.0))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(b"\x00\x00" * frames)
        duration = frames / float(rate)
        return TTSResult(
            provider="mock_tts",
            model="deterministic-v1",
            voice_id=request.voice_id,
            format="wav",
            audio_bytes=buffer.getvalue(),
            sample_rate=rate,
            duration_seconds=duration,
            usage=TTSUsageReport(input_characters=len(request.text), audio_seconds=duration),
            is_mock=True,
        )


class MockSMSProvider(SMSProvider):
    def __init__(self, *, allow_mock: bool = False) -> None:
        _require_mock_permission(allow_mock)

    async def send(self, request: SMSRequest) -> SMSResult:
        message_id = _stable_id("sms", request.to, request.body.get_secret_value())
        return SMSResult(
            provider="mock_sms",
            status=DeliveryStatus.ACCEPTED,
            message_id=message_id,
            is_mock=True,
        )


class MockEmailProvider(EmailProvider):
    def __init__(self, *, allow_mock: bool = False) -> None:
        _require_mock_permission(allow_mock)

    async def send(self, request: EmailRequest) -> EmailResult:
        text_body = request.text_body.get_secret_value() if request.text_body is not None else ""
        html_body = request.html_body.get_secret_value() if request.html_body is not None else ""
        message_id = _stable_id(
            "email",
            ",".join(request.to),
            request.subject,
            text_body,
            html_body,
        )
        return EmailResult(
            provider="mock_email",
            status=DeliveryStatus.ACCEPTED,
            message_id=message_id,
            is_mock=True,
        )


DeterministicMockASRProvider = MockASRProvider
DeterministicMockDiarizationProvider = MockDiarizationProvider
DeterministicMockIntelligenceProvider = MockIntelligenceProvider
DeterministicMockSMSProvider = MockSMSProvider
DeterministicMockEmailProvider = MockEmailProvider
