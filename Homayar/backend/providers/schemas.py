"""Typed provider request and result schemas."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

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


class ASRLanguageDecision(ProviderSchema):
    """How the ASR language was chosen (``backend.speech.language_policy``)."""

    source: Literal["requested", "detected", "fallback"]
    requested: str | None = Field(default=None, min_length=1, max_length=32)
    detected: str | None = Field(default=None, min_length=1, max_length=32)
    probability: float | None = Field(default=None, ge=0, le=1)
    reason: Literal["low_confidence", "no_speech"] | None = None
    policy_version: str = Field(min_length=1, max_length=64)


class ASRChunkSpan(ProviderSchema):
    """One chunk of a chunked call, in original seconds (``backend.speech.chunking``)."""

    index: int = Field(ge=0)
    start_seconds: float = Field(ge=0)  # audio given to ASR
    end_seconds: float = Field(ge=0)
    owned_start_seconds: float = Field(ge=0)  # the part of the timeline the chunk owns
    owned_end_seconds: float = Field(ge=0)


class ASRChunking(ProviderSchema):
    """How a long call was cut for ASR (HOM-40); HOM-45/46 reconcile speakers by it."""

    version: str = Field(min_length=1, max_length=64)
    chunks: list[ASRChunkSpan] = Field(min_length=1)


class ASRResult(ProviderSchema):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    language: str = Field(min_length=1, max_length=32)
    text: str
    segments: List[ASRSegment] = Field(default_factory=list)
    duration_seconds: float = Field(default=0, ge=0)
    is_mock: bool = False
    # Optional so results and checkpoints written before HOM-39 still load.
    language_decision: ASRLanguageDecision | None = None
    # Set only on the merged result of a chunked call (HOM-40); None otherwise.
    chunking: ASRChunking | None = None


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


# Same values as the HOM-52 conversation taxonomy; HOM-53 unifies them.
class ClaimStatus(str, Enum):
    """Whether the call states a detail (HOM-61).  ``not_stated`` is an
    explicit abstention: no text and no evidence, never a guess."""

    STATED = "stated"
    NOT_STATED = "not_stated"


# Same values as the HOM-52 conversation taxonomy; HOM-53 unifies them.
class ClaimBasis(str, Enum):
    EXPLICIT = "explicit"  # a speaker says it
    INFERRED = "inferred"  # the model concluded it


# Fixed order: the provider schema, the stored claims and the report use it.
SUMMARY_FIELD_NAMES = ("purpose", "resolution", "next_steps")


def _non_negative(value: List[int]) -> List[int]:
    if any(index < 0 for index in value):
        raise ValueError("source turn indexes must be non-negative")
    return value


class SummaryField(ProviderSchema):
    status: ClaimStatus
    text: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    basis: Optional[ClaimBasis] = None
    source_turn_indexes: List[int] = Field(default_factory=list)

    @field_validator("source_turn_indexes")
    @classmethod
    def validate_source_indexes(cls, value: List[int]) -> List[int]:
        return _non_negative(value)

    @model_validator(mode="after")
    def validate_status(self) -> "SummaryField":
        if self.status == ClaimStatus.STATED:
            if self.text is None or self.basis is None:
                raise ValueError("a stated summary field needs text and a basis")
        elif self.text is not None or self.basis is not None or self.source_turn_indexes:
            raise ValueError("a not_stated summary field carries no text, basis or evidence")
        return self


class _DecisionDetail(ProviderSchema):
    status: ClaimStatus = ClaimStatus.NOT_STATED
    value: Optional[object] = None  # typed by each subclass

    @model_validator(mode="after")
    def validate_status(self) -> "_DecisionDetail":
        stated = self.status == ClaimStatus.STATED
        if stated != (self.value is not None):
            raise ValueError("a stated detail needs a value; a not_stated detail has none")
        return self


class DecisionOwner(_DecisionDetail):
    value: Optional[str] = Field(default=None, min_length=1, max_length=256)


class DecisionDueDate(_DecisionDetail):
    value: Optional[date] = None


class SummaryFields(ProviderSchema):
    purpose: SummaryField  # why the call happened
    resolution: SummaryField  # how the call ended
    next_steps: SummaryField  # what happens after the call


class Decision(ProviderSchema):
    description: str = Field(min_length=1, max_length=2000)
    basis: ClaimBasis
    owner: DecisionOwner = Field(default_factory=DecisionOwner)
    due_date: DecisionDueDate = Field(default_factory=DecisionDueDate)
    source_turn_indexes: List[int] = Field(default_factory=list)

    @field_validator("source_turn_indexes")
    @classmethod
    def validate_source_indexes(cls, value: List[int]) -> List[int]:
        return _non_negative(value)


class KeyMoment(ProviderSchema):
    kind: str = Field(min_length=1, max_length=64)  # e.g. commitment, objection
    description: str = Field(min_length=1, max_length=2000)
    source_turn_indexes: List[int] = Field(default_factory=list)

    @field_validator("source_turn_indexes")
    @classmethod
    def validate_source_indexes(cls, value: List[int]) -> List[int]:
        return _non_negative(value)


# Deepest nesting of arrays and objects (counted together) an extraction may have; a
# real one has 5.  Deeper output is refused, so it can never exhaust the stack later.
MAX_JSON_DEPTH = 32


def _is_json_value(value: object) -> bool:
    """Plain, finite, bounded JSON: object (string keys), array, string, finite number,
    bool or null, at most ``MAX_JSON_DEPTH`` levels deep.

    Iterative, so a value of any depth is judged without a ``RecursionError``.  A
    non-finite number (NaN, an infinity, or ``1e400`` read as one) is refused: it would
    break the deterministic result and the exact checkpoint round trip.
    """
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if item is None or isinstance(item, (str, int)):  # bool is an int
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                return False
            continue
        if not isinstance(item, (list, dict)) or depth >= MAX_JSON_DEPTH:
            return False
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                return False
            pending.extend((child, depth + 1) for child in item.values())
        else:
            pending.extend((child, depth + 1) for child in item)
    return True


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
    # Structured claims (HOM-61). ``summary_fields`` is None for a legacy
    # result (an artifact of schema 2 or older); a structured result always
    # has it, even when every field is not_stated.
    summary_fields: Optional[SummaryFields] = None
    decisions: List[Decision] = Field(default_factory=list)
    key_moments: List[KeyMoment] = Field(default_factory=list)
    # The taxonomy extraction (HOM-53): the raw JSON value the model gave, never
    # coerced (``Any``: ``ProviderSchema`` would strip strings and object keys).  The
    # analysis stage judges it, so a bad one (even a non-object) never fails the
    # result.  None with ``extraction_missing`` False: a legacy result (a provider
    # that is never asked).  ``extraction_missing``: the provider was asked, but
    # the output had no extraction (the key was absent or null).
    extraction: Any = None
    extraction_missing: bool = False

    @field_validator("summary_source_turn_indexes")
    @classmethod
    def validate_summary_sources(cls, value: List[int]) -> List[int]:
        if any(index < 0 for index in value):
            raise ValueError("summary source turn indexes must be non-negative")
        return value

    @field_validator("extraction")
    @classmethod
    def validate_extraction(cls, value: Any) -> Any:
        if not _is_json_value(value):
            raise ValueError("the extraction must be plain, finite, bounded JSON")
        return value

    @model_validator(mode="after")
    def require_structured_summary(self) -> "IntelligenceResult":
        if (self.decisions or self.key_moments) and self.summary_fields is None:
            raise ValueError("decisions and key moments need summary_fields")
        if self.extraction_missing and self.extraction is not None:
            raise ValueError("a missing extraction has no value")
        return self


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
