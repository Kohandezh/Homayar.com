from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from backend.database.saas_models import ActionStatus
from backend.schemas.meetings import MeetingQualityResponse


class SpeakerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    speaker_key: str
    display_name: str | None
    identity_type: str
    confidence: float | None


class TranscriptSegmentResponse(BaseModel):
    id: str
    sequence: int
    speaker_key: str
    speaker_name: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None
    language: str | None


class TranscriptRevisionResponse(BaseModel):
    """Provenance of one immutable transcript revision (WP04-T02)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str
    revision_number: int
    parent_revision_id: str | None
    source: str
    reason: str
    note: str | None
    language: str
    segment_count: int
    created_at: datetime
    created_by_user_id: str | None
    job_id: str | None
    attempt: int | None
    checkpoint_id: str | None
    config_hash: str | None
    asr_provider: str | None
    asr_model: str | None
    diarization_provider: str | None
    diarization_status: str | None
    is_mock: bool
    is_current: bool
    superseded_at: datetime | None


class TranscriptRevisionListResponse(BaseModel):
    meeting_id: str
    current_revision_id: str | None
    items: list[TranscriptRevisionResponse]


class TranscriptRedactionInfo(BaseModel):
    """The redacted version a response was rendered from (WP07-T06).

    ``id`` is ``None`` when the version was computed for a share view (share
    sessions never write); the texts are identical to the persisted version
    of the same policy digest.
    """

    id: str | None
    detector_version: str
    classes: list[str]
    policy_digest: str
    entity_count: int
    mapping_retained: bool


class ContentRedactionInfo(BaseModel):
    """How a summary, action item or evidence text was redacted (gap G10).

    Present only when ``view == "redacted"``: the texts passed through the same
    deterministic detector and classes as the transcript (placeholders share
    the transcript's numbering).  It says nothing about identifiers the
    detector does not cover (names, addresses) and nothing about the audio.
    """

    detector_version: str
    classes: list[str]
    policy_digest: str


class TranscriptResponse(BaseModel):
    meeting_id: str
    language: str
    revision: TranscriptRevisionResponse | None = None
    segments: list[TranscriptSegmentResponse]
    speakers: list[SpeakerResponse]
    # WP07-T06: which version the texts are, and whether the workspace
    # enforces redaction (then "original" was an explicit, audited request).
    view: Literal["original", "redacted"] = "original"
    privacy_enforced: bool = False
    redaction: TranscriptRedactionInfo | None = None


class PiiRevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    placeholder: str = Field(pattern=r"^\[[A-Z_]{3,24}_[0-9]{1,5}\]$", max_length=40)
    reason: str = Field(min_length=3, max_length=200)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("A reason is required")
        return value


class PiiRevealResponse(BaseModel):
    redaction_id: str
    placeholder: str
    value: str


class TranscriptSegmentEditRequest(BaseModel):
    segment_id: str = Field(min_length=1, max_length=36)
    text: str | None = Field(default=None, min_length=1, max_length=8000)
    speaker_key: str | None = Field(default=None, min_length=1, max_length=60)

    @model_validator(mode="after")
    def require_a_change(self) -> TranscriptSegmentEditRequest:
        if self.text is None and self.speaker_key is None:
            raise ValueError("An edit must change the text or the speaker")
        return self


class TranscriptCorrectionRequest(BaseModel):
    """A person's correction of the current revision: a new revision is created."""

    base_revision_id: str = Field(min_length=1, max_length=36)
    note: str | None = Field(default=None, max_length=500)
    edits: list[TranscriptSegmentEditRequest] = Field(min_length=1, max_length=500)


class EvidenceRefResponse(BaseModel):
    """A finding's citation, embedded in the finding (WP04-T03)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    transcript_revision_id: str
    segment_id: str
    speaker_key: str
    text_start: int
    text_end: int
    start_ms: int
    end_ms: int
    evidence_type: str


class EvidenceSpanResponse(BaseModel):
    """A citation resolved to its immutable text and audio range."""

    id: str
    meeting_id: str
    analysis_run_id: str | None
    transcript_revision_id: str
    revision_number: int
    is_current_revision: bool
    segment_id: str
    speaker_key: str
    finding_type: str
    finding_id: str | None
    finding_index: int | None
    text_start: int
    text_end: int
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    evidence_type: str
    created_at: datetime


class EvidenceListResponse(BaseModel):
    meeting_id: str
    analysis_run_id: str | None
    current_revision_id: str | None
    items: list[EvidenceSpanResponse]
    # Gap G10: the view these texts were rendered in (not inferred from the
    # transcript response).
    view: Literal["original", "redacted"] = "original"
    privacy_enforced: bool = False
    redaction: ContentRedactionInfo | None = None


class AnalysisRunResponse(BaseModel):
    """Provenance of the analysis pass that produced a summary / AI actions."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    transcript_revision_id: str
    job_id: str | None
    attempt: int | None
    checkpoint_id: str | None
    stage: str
    state: str
    reason_code: str | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    prompt_sha256: str | None
    output_schema_sha256: str | None
    config_hash: str | None
    schema_version: int
    is_mock: bool
    reference_count: int = 0
    rejected_reference_count: int = 0
    input_view: str = "original"
    input_policy_digest: str | None = None
    is_current: bool
    created_at: datetime
    superseded_at: datetime | None


class SummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    meeting_id: str
    overview: str
    decisions: list[dict]
    key_points: list[dict]
    topics: list[str]
    sentiment: str | None
    provider: str
    model: str
    schema_version: int
    analysis_run_id: str | None = None
    analysis_run: AnalysisRunResponse | None = None
    evidence_status: str = "insufficient"
    created_at: datetime
    updated_at: datetime
    # Gap G10: the view this summary was rendered in.  ``analysis_run.input_view``
    # separately says which text the model was given.
    view: Literal["original", "redacted"] = "original"
    privacy_enforced: bool = False
    redaction: ContentRedactionInfo | None = None


class ActionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str
    description: str
    assignee_text: str | None
    assignee_user_id: str | None
    due_at: datetime | None
    priority: str
    status: str
    source_segment_id: str | None
    source_timestamp_ms: int | None
    confidence: float | None
    created_by: str
    analysis_run_id: str | None = None
    evidence_status: str = "insufficient"
    evidence: list[EvidenceRefResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    # Gap G10: per item, because the list response has no envelope.
    view: Literal["original", "redacted"] = "original"
    privacy_enforced: bool = False
    redaction: ContentRedactionInfo | None = None


class ActionItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=2000)
    assignee_text: str | None = Field(default=None, max_length=200)
    assignee_user_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None
    priority: str = Field(default="medium", pattern=r"^(low|medium|high)$")
    source_segment_id: str | None = Field(default=None, max_length=36)


class ActionItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=2000)
    assignee_text: str | None = Field(default=None, max_length=200)
    assignee_user_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None
    priority: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    status: ActionStatus | None = None

    @model_validator(mode="after")
    def require_update(self) -> ActionItemUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one action item field is required")
        return self


class SpeakerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=160)

    @field_validator("display_name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Speaker name cannot be empty")
        return normalized


class SearchMatchResponse(BaseModel):
    kind: str
    snippet: str
    timestamp_ms: int | None = None


class SearchMeetingResponse(BaseModel):
    id: str
    title: str
    status: str
    language: str
    duration_seconds: int
    created_at: datetime
    matches: list[SearchMatchResponse]


class SearchResponse(BaseModel):
    query: str
    items: list[SearchMeetingResponse]


ShareAudienceLiteral = Literal["link", "workspace", "recipients", "organization"]


class ShareCreateRequest(BaseModel):
    """Share contract (WP03-T06).

    ``audience`` decides who may open the link; ``allow_export`` and
    ``allow_download`` are separate grants (viewing the result is implied).
    ``require_authentication`` is accepted for older clients: ``True`` maps to
    the ``workspace`` audience, ``False`` to ``link``; an explicit ``audience``
    wins. With neither, the default is ``workspace``. ``recipients`` (emails)
    and ``phone_recipients`` (Iranian mobile numbers) name the audience of a
    ``recipients`` share; together they stay within the recipient limit.
    """

    model_config = ConfigDict(extra="forbid")

    expires_in_hours: int | None = Field(default=168, ge=1, le=2160)
    require_authentication: bool | None = None
    audience: ShareAudienceLiteral | None = None
    recipients: list[EmailStr] = Field(default_factory=list, max_length=50)
    phone_recipients: list[str] = Field(default_factory=list, max_length=50)
    allow_download: bool = False
    allow_export: bool = False

    def resolved_audience(self) -> ShareAudienceLiteral:
        if self.audience is not None:
            return self.audience
        if self.require_authentication is False:
            return "link"
        return "workspace"


class ShareRecipientsUpdateRequest(BaseModel):
    """Delta edit of a ``recipients`` share: add addresses, remove recipient rows by id."""

    model_config = ConfigDict(extra="forbid")

    add_emails: list[EmailStr] = Field(default_factory=list, max_length=50)
    add_phones: list[str] = Field(default_factory=list, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def require_a_change(self) -> ShareRecipientsUpdateRequest:
        if not (self.add_emails or self.add_phones or self.remove):
            raise ValueError("At least one addition or removal is required")
        return self


class ShareRecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str | None
    phone_e164: str | None
    user_id: str | None
    bound_at: datetime | None


class ShareLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str
    require_authentication: bool
    audience: ShareAudienceLiteral
    allow_download: bool
    allow_export: bool
    recipients: list[ShareRecipientResponse] = Field(default_factory=list)
    expires_at: datetime | None
    revoked_at: datetime | None
    access_count: int
    last_accessed_at: datetime | None
    created_at: datetime


class ShareCreatedResponse(ShareLinkResponse):
    url: str


class SharedMeetingResponse(BaseModel):
    meeting_id: str
    title: str
    language: str
    duration_seconds: int
    created_at: datetime
    audience: ShareAudienceLiteral = "workspace"
    allow_download: bool = False
    allow_export: bool = False
    expires_at: datetime | None = None
    transcript: TranscriptResponse | None
    summary: SummaryResponse | None
    actions: list[ActionItemResponse]
    quality: MeetingQualityResponse | None = None
