"""Typed provider request and result schemas."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


UNKNOWN_SPEAKER_LABEL = "speaker_unknown"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"^\+?[0-9]{7,20}$")


class ProviderSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProviderIssue(ProviderSchema):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class DeliveryStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class DiarizationStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ActionPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ASRRequest(ProviderSchema):
    audio_path: str = Field(min_length=1, max_length=4096)
    language: Optional[str] = Field(default=None, min_length=2, max_length=32)
    beam_size: int = Field(default=5, ge=1, le=20)
    vad_filter: bool = True


class ASRSegment(ProviderSchema):
    index: int = Field(ge=0)
    text: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    speaker_label: str = Field(default=UNKNOWN_SPEAKER_LABEL, min_length=1, max_length=128)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ASRSegment":
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must not precede start_seconds")
        return self


class ASRResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    language: str = Field(min_length=1, max_length=32)
    text: str
    segments: List[ASRSegment] = Field(default_factory=list)
    duration_seconds: float = Field(default=0, ge=0)
    is_mock: bool = False


class DiarizationRequest(ProviderSchema):
    audio_path: str = Field(min_length=1, max_length=4096)
    expected_speaker_count: Optional[int] = Field(default=None, ge=1, le=20)


class DiarizationSegment(ProviderSchema):
    speaker_label: str = Field(min_length=1, max_length=128)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "DiarizationSegment":
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must not precede start_seconds")
        return self


class DiarizationResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    status: DiarizationStatus
    segments: List[DiarizationSegment] = Field(default_factory=list)
    issue: Optional[ProviderIssue] = None
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_availability(self) -> "DiarizationResult":
        if self.status == DiarizationStatus.UNAVAILABLE:
            if self.issue is None:
                raise ValueError("Unavailable diarization requires an issue")
            if any(segment.speaker_label != UNKNOWN_SPEAKER_LABEL for segment in self.segments):
                raise ValueError("Unavailable diarization may only emit unknown speaker labels")
        return self


class TranscriptTurn(ProviderSchema):
    text: str = Field(min_length=1)
    speaker_label: str = Field(default=UNKNOWN_SPEAKER_LABEL, min_length=1, max_length=128)
    start_seconds: Optional[float] = Field(default=None, ge=0)
    end_seconds: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptTurn":
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("start_seconds and end_seconds must be provided together")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("end_seconds must not precede start_seconds")
        return self


class IntelligenceRequest(ProviderSchema):
    transcript_text: str = ""
    turns: List[TranscriptTurn] = Field(default_factory=list)
    language: str = Field(default="unknown", min_length=1, max_length=32)
    call_id: Optional[str] = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_transcript(self) -> "IntelligenceRequest":
        if not self.transcript_text.strip() and not self.turns:
            raise ValueError("A transcript or at least one transcript turn is required")
        return self


class ActionItem(ProviderSchema):
    description: str = Field(min_length=1, max_length=2000)
    owner: Optional[str] = Field(default=None, max_length=256)
    due_date: Optional[date] = None
    priority: ActionPriority = ActionPriority.MEDIUM
    source_turn_indexes: List[int] = Field(default_factory=list)

    @field_validator("source_turn_indexes")
    @classmethod
    def validate_source_indexes(cls, value: List[int]) -> List[int]:
        if any(index < 0 for index in value):
            raise ValueError("source turn indexes must be non-negative")
        return value


class IntelligenceInsight(ProviderSchema):
    category: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    source_turn_indexes: List[int] = Field(default_factory=list)


class ProviderUsageReport(ProviderSchema):
    """What a provider reported about one call (WP08-T04).  A field is
    ``None`` when the provider did not report it: never an invented zero."""

    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)


class IntelligenceResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    summary: str
    # Turns the summary is grounded in (WP04-T03); empty means the provider
    # offered no evidence and the summary is reported as insufficiently
    # evidenced — never as if it were.
    summary_source_turn_indexes: List[int] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    insights: List[IntelligenceInsight] = Field(default_factory=list)
    issue: Optional[ProviderIssue] = None
    is_mock: bool = False
    usage: Optional[ProviderUsageReport] = None

    @field_validator("summary_source_turn_indexes")
    @classmethod
    def validate_summary_sources(cls, value: List[int]) -> List[int]:
        if any(index < 0 for index in value):
            raise ValueError("summary source turn indexes must be non-negative")
        return value


class SMSRequest(ProviderSchema):
    to: str
    body: SecretStr
    message_type: Literal["notification", "transactional", "otp"] = "transactional"
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("to")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        if not _PHONE_RE.fullmatch(value):
            raise ValueError("SMS destination must be a valid international or local number")
        return value

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: SecretStr) -> SecretStr:
        body = value.get_secret_value()
        if not body.strip() or len(body) > 5000:
            raise ValueError("SMS body must contain between 1 and 5000 characters")
        return value


class SMSResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    status: DeliveryStatus
    message_id: Optional[str] = Field(default=None, max_length=512)
    accepted_at: Optional[datetime] = None
    issue: Optional[ProviderIssue] = None
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_status(self) -> "SMSResult":
        if self.status == DeliveryStatus.ACCEPTED and self.issue is not None:
            raise ValueError("Accepted SMS results cannot include an issue")
        if self.status != DeliveryStatus.ACCEPTED and self.issue is None:
            raise ValueError("Rejected or failed SMS results require an issue")
        return self


class EmailRequest(ProviderSchema):
    to: List[str] = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=998)
    text_body: Optional[SecretStr] = None
    html_body: Optional[SecretStr] = None
    cc: List[str] = Field(default_factory=list, max_length=100)
    bcc: List[str] = Field(default_factory=list, max_length=100)
    headers: Dict[str, str] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("to", "cc", "bcc")
    @classmethod
    def validate_addresses(cls, value: List[str]) -> List[str]:
        if any(not _EMAIL_RE.fullmatch(address) for address in value):
            raise ValueError("Email address is invalid")
        return value

    @field_validator("subject")
    @classmethod
    def prevent_subject_injection(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Email subject cannot contain line breaks")
        return value

    @field_validator("headers")
    @classmethod
    def prevent_header_injection(cls, value: Dict[str, str]) -> Dict[str, str]:
        for name, header_value in value.items():
            if not name or any(character in name for character in "\r\n:"):
                raise ValueError("Email header name is invalid")
            if "\r" in header_value or "\n" in header_value:
                raise ValueError("Email header value is invalid")
        return value

    @model_validator(mode="after")
    def require_body(self) -> "EmailRequest":
        if self.text_body is None and self.html_body is None:
            raise ValueError("At least one email body is required")
        return self


class EmailResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    status: DeliveryStatus
    message_id: Optional[str] = Field(default=None, max_length=512)
    accepted_at: Optional[datetime] = None
    issue: Optional[ProviderIssue] = None
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_status(self) -> "EmailResult":
        if self.status == DeliveryStatus.ACCEPTED and self.issue is not None:
            raise ValueError("Accepted email results cannot include an issue")
        if self.status != DeliveryStatus.ACCEPTED and self.issue is None:
            raise ValueError("Rejected or failed email results require an issue")
        return self


# --- Text-to-speech (AppVision F20/F74, local AI runtime) -------------------

TTS_AUDIO_FORMATS = ("wav", "mp3", "ogg", "flac", "pcm")


class TTSRequest(ProviderSchema):
    text: str = Field(min_length=1, max_length=5000)
    language: Optional[str] = Field(default=None, min_length=2, max_length=32)
    # Adapter-specific voice name (a Piper voice key, a server voice id).
    # ``None`` selects the adapter's configured default voice.
    voice_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    # Requested output rate; ``None`` = the voice's native rate.  An adapter
    # that cannot honour an explicit rate refuses instead of mislabelling.
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=48000)
    format: Literal["wav", "mp3", "ogg", "flac", "pcm"] = "wav"

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("TTS text must not be blank")
        return value


class TTSUsageReport(ProviderSchema):
    """What the synthesis consumed; ``None`` = not reported, never zero."""

    input_characters: Optional[int] = Field(default=None, ge=0)
    audio_seconds: Optional[float] = Field(default=None, ge=0)


class TTSResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    voice_id: Optional[str] = Field(default=None, max_length=128)
    format: Literal["wav", "mp3", "ogg", "flac", "pcm"]
    # Exactly one of the two carries the audio.
    audio_bytes: Optional[bytes] = Field(default=None, repr=False)
    audio_path: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    sample_rate: Optional[int] = Field(default=None, ge=1)
    # Measured from the audio when the container says so; ``None`` otherwise.
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    usage: Optional[TTSUsageReport] = None
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_audio(self) -> "TTSResult":
        if (self.audio_bytes is None) == (self.audio_path is None):
            raise ValueError("A TTS result carries exactly one of audio_bytes or audio_path")
        if self.audio_bytes is not None and not self.audio_bytes:
            raise ValueError("TTS audio must not be empty")
        return self
