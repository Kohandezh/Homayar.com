from __future__ import annotations

from datetime import datetime

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

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


class MeetingDetailResponse(MeetingResponse):
    recording: RecordingResponse | None = None
    latest_job: JobResponse | None = None
    quality: MeetingQualityResponse | None = None
    # The ledger row that brought the conversation in (WP05-T01); null for
    # conversations that predate the ledger and were not backfilled.
    ingestion: IngestionResponse | None = None


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    next_before: datetime | None = None


class UploadAcceptedResponse(BaseModel):
    meeting: MeetingResponse
    recording: RecordingResponse
    job: JobResponse
    usage: UsageResponse
    idempotent_replay: bool = False
    ingestion: IngestionResponse | None = None


class MeetingUpdateRequest(BaseModel):
    """Partial update; ``workspace_id`` is deliberately not accepted (extra=forbid)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    team_id: str | None = Field(default=None, max_length=36)
    agent_user_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def require_a_change(self) -> MeetingUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self
