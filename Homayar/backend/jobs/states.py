"""Typed stage names and reason codes for processing outcomes (WP04-T01).

A stage ends in exactly one ``StageState``; anything other than ``completed``
carries a ``StageReason`` so API, UI and exports can say *why* a result is
degraded instead of presenting it as fully successful.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.database.saas_models import MeetingStatus, StageState


class StageName(str, enum.Enum):
    MATERIALIZE = "materialize"
    ASR = "asr"
    DIARIZATION = "diarization"
    INTELLIGENCE = "intelligence"


STAGE_ORDER: tuple[StageName, ...] = (
    StageName.MATERIALIZE,
    StageName.ASR,
    StageName.DIARIZATION,
    StageName.INTELLIGENCE,
)


class StageReason(str, enum.Enum):
    EMPTY_TRANSCRIPT = "empty_transcript"
    NO_TRANSCRIPT = "no_transcript"
    DIARIZATION_UNAVAILABLE = "diarization_unavailable"
    PROVIDER_ERROR = "provider_error"
    PROCESSING_ERROR = "processing_error"
    NOT_REACHED = "not_reached"
    # Informational on a *completed* stage (WP06-T03): the result came from a
    # committed checkpoint of an earlier attempt; no provider was called.
    CHECKPOINT_REUSED = "checkpoint_reused"
    # Informational on a *completed* stage (WP08-T05): the primary provider
    # failed transiently and an approved same-policy fallback produced it.
    FALLBACK_USED = "fallback_used"


DEGRADED_STATES: frozenset[StageState] = frozenset(
    {
        StageState.PARTIAL,
        StageState.UNAVAILABLE,
        StageState.INSUFFICIENT_EVIDENCE,
        StageState.SKIPPED,
    }
)


@dataclass
class StageRecord:
    stage: StageName
    state: StageState = StageState.RUNNING
    reason_code: str | None = None
    reason_message: str | None = None
    provider: str | None = None
    model: str | None = None
    is_mock: bool = False
    latency_ms: int | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    # Checkpoint provenance (WP06-T03).
    checkpoint_id: str | None = None
    reused: bool = False
    # Model manifest provenance (WP08-T01).
    manifest_id: str | None = None
    manifest_version: int | None = None
    manifest_digest: str | None = None

    def finish(
        self,
        state: StageState,
        *,
        reason: StageReason | str | None = None,
        message: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        is_mock: bool = False,
    ) -> StageRecord:
        now = datetime.now(UTC)
        self.state = state
        self.reason_code = reason.value if isinstance(reason, StageReason) else reason
        self.reason_message = message
        self.provider = provider
        self.model = model
        self.is_mock = is_mock
        self.finished_at = now
        self.latency_ms = max(0, round((now - self.started_at).total_seconds() * 1000))
        return self


def meeting_status_for(records: list[StageRecord]) -> MeetingStatus:
    """A meeting is only ``completed`` when every stage completed; any degraded
    stage makes it ``partial``. Failures are decided by the job, not here."""
    if any(record.state in DEGRADED_STATES for record in records):
        return MeetingStatus.PARTIAL
    return MeetingStatus.COMPLETED


def not_reached(stage: StageName) -> StageRecord:
    record = StageRecord(stage=stage)
    record.finish(StageState.SKIPPED, reason=StageReason.NOT_REACHED)
    record.latency_ms = None
    record.started_at = record.finished_at or record.started_at
    return record
