from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from backend.database.saas_models import ConversationDirection, ConversationType
from backend.schemas.ingestions import IngestionResponse


class MeetingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    team_id: str | None = None
    agent_user_id: str | None = None
    title: str
    language: str
    status: str
    duration_seconds: int
    source: str
    created_at: datetime
    updated_at: datetime
    # The conversation model (HOM-58): what the conversation is, who started
    # it, its id in the source system and when it happened there.
    conversation_type: str
    direction: str | None = None
    external_source_system: str | None = None
    external_conversation_id: str | None = None
    started_at: datetime | None = None


class RecordingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    media_type: str
    container_format: str
    size_bytes: int
    checksum_sha256: str
    duration_seconds: int
    channel_count: int | None
    sample_rate_hz: int | None


class MediaObjectResponse(BaseModel):
    """One stored byte stream of the conversation and its lifecycle (WP05-T03).

    The storage key is deliberately absent: access goes through the recording
    download route, never through a location.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str | None = None
    kind: str
    lifecycle_state: str
    integrity: str
    storage_backend: str
    content_type: str
    container_format: str | None = None
    size_bytes: int
    sha256: str
    duration_seconds: int | None = None
    channel_count: int | None = None
    sample_rate_hz: int | None = None
    original_filename: str | None = None
    producer: str
    derived_from_id: str | None = None
    ingestion_id: str | None = None
    job_id: str | None = None
    created_at: datetime
    created_by_user_id: str | None = None
    retention_expires_at: datetime | None = None
    deletion_requested_at: datetime | None = None
    deletion_reason: str | None = None
    deleted_at: datetime | None = None
    legal_hold_at: datetime | None = None
    legal_hold_reason: str | None = None
    legal_hold_by_user_id: str | None = None
    legal_hold_review_at: datetime | None = None
    last_verified_at: datetime | None = None


class LegalHoldRequest(BaseModel):
    """Place a hold (WP07-T02): who did it comes from the session, never the body."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=200)
    review_at: datetime


class LegalHoldReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=200)


class MediaObjectListResponse(BaseModel):
    items: list[MediaObjectResponse]


class StageRunResponse(BaseModel):
    """Typed outcome of one processing stage (WP04-T01)."""

    model_config = ConfigDict(from_attributes=True)

    attempt: int = 0
    stage: str
    state: str
    reason_code: str | None = None
    reason_message: str | None = None
    provider: str | None = None
    model: str | None = None
    is_mock: bool = False
    latency_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Checkpoint provenance (WP06-T03): produced or reused checkpoint.
    checkpoint_id: str | None = None
    reused: bool = False
    # Model manifest the stage ran under (WP08-T01); "unregistered" in
    # development/test when no manifest matched, null for older runs.
    manifest_id: str | None = None
    manifest_version: int | None = None
    manifest_digest: str | None = None


class DispatchResponse(BaseModel):
    """Outbox state of the job's broker publication (WP06-T02)."""

    model_config = ConfigDict(from_attributes=True)

    state: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    published_at: datetime | None
    last_error: str | None
    generation: int = 0


class LeaseResponse(BaseModel):
    """Execution ownership of the current/last attempt (WP06-T04)."""

    generation: int
    attempt_id: str | None
    expires_at: datetime | None
    renewed_at: datetime | None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: str
    stage: str
    attempt_count: int
    max_attempts: int
    failure_code: str | None
    failure_message: str | None
    retry_after: datetime | None = None
    # Failure policy (WP06-T05): classified reason, overall deadline, replays.
    failure_class: str | None = None
    deadline_at: datetime | None = None
    replay_count: int = 0
    last_replayed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    stages: list[StageRunResponse] = Field(
        default_factory=list, validation_alias=AliasChoices("stages", "stage_runs")
    )
    dispatch: DispatchResponse | None = None
    # Lease columns are read from the job row and exposed as one ``lease`` object.
    lease_generation: int = Field(default=0, exclude=True)
    attempt_id: str | None = Field(default=None, exclude=True)
    lease_expires_at: datetime | None = Field(default=None, exclude=True)
    lease_renewed_at: datetime | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lease(self) -> LeaseResponse:
        return LeaseResponse(
            generation=self.lease_generation,
            attempt_id=self.attempt_id,
            expires_at=self.lease_expires_at,
            renewed_at=self.lease_renewed_at,
        )


class UsageResponse(BaseModel):
    metric: str = "audio_seconds"
    limit: int | None
    used: int
    reserved: int
    remaining: int | None


class DegradationResponse(BaseModel):
    stage: str
    state: str
    reason: str | None = None


class UsageStateResponse(BaseModel):
    state: str
    audio_seconds: int | None = None


class MeetingQualityResponse(BaseModel):
    """Quality, coverage and degradation of a meeting's results (WP04-T06)."""

    transcription: str
    speaker_attribution: str
    analysis: str
    evidence: str
    analysis_stale: bool
    transcript_revision_id: str | None
    analysed_revision_id: str | None
    analysis_run_id: str | None
    segment_count: int
    speaker_count: int
    speech_coverage_ratio: float | None
    is_mock: bool
    degradations: list[DegradationResponse] = Field(default_factory=list)
    usage: UsageStateResponse | None = None
    # Recompute after a correction (HOM-63): ``up_to_date`` (not stale),
    # ``pending`` (stale, a recompute is due) or ``failed`` (stale, the failed
    # recomputes reached the cap; ``recompute_failure_code`` is the last reason).
    recompute_status: str = "up_to_date"
    recompute_failure_code: str | None = None


class ReportCompleteness(BaseModel):
    """What is missing from a meeting's results: one rule for every read that
    carries it (HOM-59, HOM-60)."""

    is_complete: bool
    notices: list[str]
    meeting_status: str
    transcription: str
    speaker_attribution: str
    analysis: str
    evidence: str


class PlaybackResponse(BaseModel):
    """An authorized, time-ranged way to play a meeting's recording (HOM-60).

    ``media_url`` is the inline playback route, authorized on every request
    (every ``Range`` request too), so it carries no token.  ``url`` adds a W3C
    Media Fragment ``#t=<start>,<end>`` in seconds.  No storage location is
    ever part of the body.
    """

    meeting_id: str
    media_url: str
    url: str
    start_ms: int
    end_ms: int | None
    duration_ms: int | None
    content_type: str
    size_bytes: int
    accept_ranges: Literal["bytes"] = "bytes"
    completeness: ReportCompleteness
    privacy_enforced: bool
    # Audio is never masked; the console says so next to the player.
    audio_redacted: Literal[False] = False


class ChannelAudioStreamResponse(BaseModel):
    index: int
    codec: str
    channels: int
    channel_layout: str
    sample_rate_hz: int
    duration_seconds: float | None


class ChannelThresholdsResponse(BaseModel):
    frame_ms: int
    active_threshold_dbfs: float
    silent_max_active_share: float
    duplicate_min_correlation: float
    duplicate_max_lag_ms: float
    narrowband_below_hz: int


class ChannelResultResponse(BaseModel):
    """One channel: its signal measures and its role attribution (HOM-37)."""

    index: int
    rms_dbfs: float | None
    active_share: float | None
    silent: bool | None
    duplicated: bool | None
    role: Literal["agent", "customer", "unknown"]
    attribution: Literal["trusted", "unknown"]
    reason: str | None


class ChannelInspectionResponse(BaseModel):
    """Channel metadata, signal flags and trusted roles recorded at ingest (HOM-37).

    Read-only and as stored: a later change of the workspace's channel-role
    mapping does not change it.  Signal measures are a heuristic, not speech
    detection; see ``backend/media/README.md``.
    """

    version: str
    status: Literal["complete", "unavailable"]
    unavailable_reason: str | None
    channel_count: int
    channel_layout: str
    sample_rate_hz: int
    codec: str
    duration_seconds: float
    narrowband: bool
    audio_streams: list[ChannelAudioStreamResponse]
    analysis_seconds: float | None
    analysis_sample_rate_hz: int | None
    thresholds: ChannelThresholdsResponse
    flags: list[str]
    duplicate_pairs: list[list[int]]
    mapping_source_type: str
    mapping_source_provider: str
    mapping_version: int | None
    mapping_digest: str | None
    channels: list[ChannelResultResponse]


class MeetingDetailResponse(MeetingResponse):
    recording: RecordingResponse | None = None
    latest_job: JobResponse | None = None
    quality: MeetingQualityResponse | None = None
    # The ledger row that brought the conversation in (WP05-T01); null for
    # conversations that predate the ledger and were not backfilled.
    ingestion: IngestionResponse | None = None
    # Channel facts and roles recorded at ingest (HOM-37); null when no
    # record is kept for the recording.
    channel_inspection: ChannelInspectionResponse | None = None


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    next_before: datetime | None = None


class ConversationListResponse(MeetingListResponse):
    """``GET /conversations``: the same rows, ids and page as ``GET /meetings`` (HOM-58)."""


class MeetingCountsResponse(BaseModel):
    # One key per meeting status, in enum order; a status with no meetings is 0.
    counts: dict[str, int]
    total: int


class UploadAcceptedResponse(BaseModel):
    meeting: MeetingResponse
    recording: RecordingResponse
    job: JobResponse
    usage: UsageResponse
    idempotent_replay: bool = False
    ingestion: IngestionResponse | None = None


class MeetingUpdateRequest(BaseModel):
    """Partial update; ``workspace_id`` is deliberately not accepted (extra=forbid).

    The source reference (``external_source_system``, ``external_conversation_id``)
    is immutable, so it is not a field here and sending it is a 422.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    team_id: str | None = Field(default=None, max_length=36)
    agent_user_id: str | None = Field(default=None, max_length=36)
    conversation_type: ConversationType | None = None
    # An explicit null clears these two.
    direction: ConversationDirection | None = None
    started_at: AwareDatetime | None = None

    @field_validator("conversation_type")
    @classmethod
    def conversation_type_not_null(cls, value: ConversationType | None) -> ConversationType:
        # Runs only when the field is sent; the column is NOT NULL.
        if value is None:
            raise ValueError("conversation_type cannot be null")
        return value

    @model_validator(mode="after")
    def require_a_change(self) -> MeetingUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self
