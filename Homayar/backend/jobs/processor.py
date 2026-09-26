"""Durable, idempotent meeting-processing orchestration.

Execution ownership (WP06-T04) lives in ``backend.jobs.leases``: a claim
grants a lease with a fencing generation; every transaction that commits a
protected change (stage transition, results, completion, failure, usage
settlement) first re-locks the job row and proves the lease is still valid.
A stale worker — one whose lease expired or was fenced by recovery — is
rejected before its first write and reports ``superseded``.

Retries never go through the broker: a retryable failure requeues the job and
re-arms its outbox intent (``rearm_dispatch``) for ``retry_after``; the
dispatch sweeper publishes it when due.  Abandoned RUNNING attempts are
recovered by ``backend.jobs.recovery``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
import secrets
import tempfile
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ActionItem,
    EvidenceStatus,
    JobState,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    ProcessingStageRun,
    ProviderUsage,
    Recording,
    StageState,
    TranscriptSource,
    Workspace,
    new_id,
)
from backend.database.tenant_context import bind_job_context, bind_tenant_context
from backend.jobs import checkpoints as ckpt
from backend.jobs.failures import (
    ATTEMPTS_EXHAUSTED,
    JOB_DEADLINE_EXCEEDED,
    FailureClass,
    RetryPolicy,
    RetryVerdict,
    park_job,
    retry_verdict,
)
from backend.jobs.leases import (
    Lease,
    LeaseHeartbeat,
    LeasePolicy,
    LeaseVerdict,
    RenewalOutcome,
    StaleLeaseError,
    as_utc,
    claim_verdict,
    database_now,
    grant_lease,
    lock_owned_or_cancelled,
    release_lease,
)
from backend.jobs.outbox import rearm_dispatch
from backend.jobs.states import (
    STAGE_ORDER,
    StageName,
    StageReason,
    StageRecord,
    meeting_status_for,
    not_reached,
)
from backend.privacy.redaction_service import policy_digest, redact_revision
from backend.privacy.text_pii import PiiClass, Pseudonymizer, parse_classes, redact
from backend.providers import egress
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle, create_provider_bundle
from backend.providers.registry import (
    ResolvedManifest,
    is_strict,
    parse_rate_limits,
    resolve_bundle,
    resolve_fallbacks,
)
from backend.providers.resilience import (
    CIRCUIT_OPEN,
    RATE_LIMITED,
    Admission,
    admit,
    health_rank,
    record_failure,
    record_success,
)
from backend.providers.routing import (
    POLICY_CHANGED,
    Selection,
    analysis_text_classes,
)
from backend.providers.routing import select as select_candidate
from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRRequest,
    ASRResult,
    DiarizationRequest,
    DiarizationResult,
    DiarizationStatus,
    IntelligenceRequest,
    IntelligenceResult,
    ProviderSchema,
    TranscriptTurn,
)
from backend.services.ai_policy_service import (
    DEFAULT_EFFECTIVE,
    EffectivePolicy,
    current_policy,
    record_run_snapshot,
)
from backend.services.audit_service import record_audit_event
from backend.services.entitlement_service import commit_usage, release_usage
from backend.services.evidence_service import (
    FINDING_ACTION_ITEM,
    FINDING_INSIGHT,
    FINDING_SUMMARY,
    EvidencePlan,
    resolve_turn_references,
)
from backend.services.logging_service import logger
from backend.services.media_lifecycle_service import (
    mark_deleted,
    original_for_meeting,
    request_deletion,
)
from backend.services.model_registry_service import register_resolved
from backend.services.quality_service import assess_quality
from backend.services.transcript_service import (
    REASON_PROCESSING,
    REASON_REPROCESSING,
    EngineProvenance,
    SegmentDraft,
    create_revision,
    current_revision,
    ensure_speakers,
    record_analysis_run,
)
from backend.storage import ObjectStorage, StorageError, create_object_storage

# Stage pointer written by ``_set_stage`` -> stage that was in flight.
STAGE_BY_POINTER: dict[str, StageName] = {
    "processing": StageName.MATERIALIZE,
    "transcribing": StageName.ASR,
    "diarizing": StageName.DIARIZATION,
    "analyzing": StageName.INTELLIGENCE,
}


class ProcessingError(RuntimeError):
    """A classified processing failure (WP06-T05).

    ``retryable`` selects the retry budget; ``failure_class`` is what gets
    persisted (``transient`` for retryable, ``permanent`` otherwise, unless
    the raiser knows better).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        failure_class: FailureClass | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable
        # The earliest useful retry (provider Retry-After, open circuit,
        # request window; WP08-T05): the back-off is never shorter.
        self.retry_after_seconds = retry_after_seconds
        self.failure_class = failure_class or (
            FailureClass.TRANSIENT if retryable else FailureClass.PERMANENT
        )


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    workspace_id: str
    meeting_id: str
    language: str
    recording_key: str
    recording_filename: str
    recording_size_bytes: int
    recording_sha256: str
    recording_duration_seconds: int
    delete_audio_after_processing: bool
    lease: Lease
    # Text-privacy policy snapshotted at claim (WP07-T06): empty = the
    # provider receives the original transcript.
    pii_classes: tuple[str, ...] = ()
    # AI processing policy read at claim (WP08-T02): every attempt runs under
    # the workspace's *current* version, recorded in ``run_policy_snapshots``.
    ai_policy: EffectivePolicy = DEFAULT_EFFECTIVE
    # AI policy digest + workspace redaction classes at claim (WP08-T03): a
    # stage starts only while the workspace still has exactly these.
    policy_fingerprint: str = ""
    # Overall job deadline (WP06-T05): bounds every provider call (WP08-T05).
    deadline_at: datetime | None = None

    @property
    def privacy_classes(self) -> list[PiiClass]:
        return parse_classes(self.pii_classes)

    @property
    def privacy_digest(self) -> str | None:
        return policy_digest(self.privacy_classes) if self.pii_classes else None


def _workspace_pii_classes(workspace: Workspace) -> tuple[str, ...]:
    """The workspace's text-redaction classes (WP07-T06); empty = originals."""
    if not workspace.pii_redaction_enabled:
        return ()
    return tuple(c.value for c in parse_classes(workspace.pii_redaction_classes or [])) or tuple(
        sorted(c.value for c in PiiClass)
    )


def _policy_fingerprint(ai_policy_digest: str, pii_classes: tuple[str, ...]) -> str:
    return f"{ai_policy_digest}:{','.join(pii_classes)}"


def _ensure_policy_current(claim: ClaimedJob) -> None:
    """Re-read the workspace's AI and redaction policy before a stage starts
    (WP08-T03).  A snapshot explains an attempt; it never authorizes work
    after the policy changed: the attempt ends and the next one (reusing
    committed checkpoints) runs under the current policy."""
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        workspace = db.get(Workspace, claim.workspace_id)
        current = (
            _policy_fingerprint(
                current_policy(db, workspace_id=claim.workspace_id).digest,
                _workspace_pii_classes(workspace),
            )
            if workspace is not None
            else None
        )
    if current != claim.policy_fingerprint:
        raise ProcessingError(
            POLICY_CHANGED,
            "The workspace AI or privacy policy changed during processing; "
            "the next attempt applies it",
            retryable=True,
        )


def _claim_job(
    job_id: str,
    *,
    worker_id: str,
    settings: Settings,
) -> tuple[ClaimedJob | None, str]:
    policy = LeasePolicy.from_settings(settings)
    factory = get_session_factory()
    with factory() as db:
        # Service authority is exactly one job: the job id makes that row
        # visible; its workspace then scopes everything else in this session.
        bind_job_context(db, job_id)
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            exists = db.scalar(select(ProcessingJob.id).where(ProcessingJob.id == job_id))
            return None, "busy" if exists else "not_found"
        bind_tenant_context(db, workspace_id=job.workspace_id)
        now = database_now(db)
        verdict = claim_verdict(job, now=now)
        if verdict == LeaseVerdict.TERMINAL:
            return None, job.state.value
        if verdict == LeaseVerdict.NOT_DUE:
            # Delivered early (clock skew): keep the intent durable instead of
            # dropping the message — the sweeper republishes at retry_after.
            assert job.retry_after is not None
            rearm_dispatch(db, job, due=job.retry_after, settings=settings)
            db.commit()
            return None, verdict.value
        if verdict != LeaseVerdict.CLAIMABLE:
            return None, verdict.value

        meeting = db.scalar(
            select(Meeting)
            .options(selectinload(Meeting.recording))
            .where(
                Meeting.id == job.meeting_id,
                Meeting.workspace_id == job.workspace_id,
                Meeting.deleted_at.is_(None),
            )
        )
        if meeting is None or meeting.recording is None or meeting.recording.deleted_at is not None:
            return None, "recording_missing"

        recording = meeting.recording
        workspace = db.get(Workspace, meeting.workspace_id)
        if workspace is None:
            return None, "recording_missing"
        ai_policy = current_policy(db, workspace_id=job.workspace_id)
        pii_classes = _workspace_pii_classes(workspace)
        lease = grant_lease(job, worker_id=worker_id, policy=policy, now=now)
        meeting.status = MeetingStatus.PROCESSING
        db.commit()
        return (
            ClaimedJob(
                job_id=job.id,
                workspace_id=job.workspace_id,
                meeting_id=meeting.id,
                language=meeting.language,
                recording_key=recording.storage_key,
                recording_filename=recording.original_filename,
                recording_size_bytes=recording.size_bytes,
                recording_sha256=recording.checksum_sha256,
                recording_duration_seconds=recording.duration_seconds,
                delete_audio_after_processing=workspace.delete_audio_after_processing,
                lease=lease,
                pii_classes=pii_classes,
                ai_policy=ai_policy,
                policy_fingerprint=_policy_fingerprint(ai_policy.digest, pii_classes),
                deadline_at=job.deadline_at,
            ),
            "claimed",
        )


# Kept as a module alias: every protected write of the processor and of the
# checkpoint layer uses the same fence (WP06-T04, ``backend.jobs.leases``).
_lock_owned_or_cancelled = lock_owned_or_cancelled


def _set_stage(claim: ClaimedJob, *, stage: str) -> None:
    status_by_stage = {
        "transcribing": MeetingStatus.TRANSCRIBING,
        "diarizing": MeetingStatus.DIARIZING,
        "analyzing": MeetingStatus.ANALYZING,
    }
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        job, _now = _lock_owned_or_cancelled(db, claim.lease)
        if job is None:
            raise ProcessingError(
                "meeting_unavailable",
                "The meeting is no longer available",
                retryable=False,
            )
        meeting = db.get(Meeting, claim.meeting_id)
        if meeting is None or meeting.deleted_at is not None:
            raise ProcessingError(
                "meeting_unavailable",
                "The meeting is no longer available",
                retryable=False,
            )
        job.stage = stage
        meeting.status = status_by_stage[stage]
        db.commit()


def _deadline_error(message: str) -> ProcessingError:
    # Retryable on purpose: the retry verdict sees the passed deadline and
    # dead-letters the job (replayable), as for every other deadline.
    return ProcessingError(
        JOB_DEADLINE_EXCEEDED, message, retryable=True, failure_class=FailureClass.DEADLINE
    )


def _run_stage[T](
    heartbeat: LeaseHeartbeat,
    coroutine: Coroutine[Any, Any, T],
    policy: LeasePolicy,
    *,
    deadline_at: datetime | None = None,
) -> T:
    """Run one provider call under the stage timeout and the job deadline,
    whichever comes first (WP08-T05), unless the lease is lost.

    ``wait_for`` bounds cooperative (awaiting) providers; a provider that
    blocks the loop is bounded by the attempt deadline and the worker time
    limit instead, and its late result is rejected at the transaction boundary.
    """
    if heartbeat.lost.is_set():
        coroutine.close()
        raise StaleLeaseError(heartbeat.outcome or RenewalOutcome.EXPIRED)
    timeout = policy.stage_timeout.total_seconds()
    bounded_by_deadline = False
    if deadline_at is not None:
        remaining = (as_utc(deadline_at) - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            coroutine.close()
            raise _deadline_error("The job deadline passed before the stage could run")
        if remaining < timeout:
            timeout, bounded_by_deadline = remaining, True
    try:
        return asyncio.run(asyncio.wait_for(coroutine, timeout=timeout))
    except TimeoutError as exc:
        if bounded_by_deadline:
            raise _deadline_error("The job deadline passed during a processing stage") from exc
        raise ProcessingError(
            "stage_timeout",
            "A processing stage exceeded its time limit",
            retryable=True,
        ) from exc


# Retry spreading (the project draws random numbers only through ``secrets``).
_jitter = secrets.SystemRandom().random

_ADMISSION_MESSAGES = {
    CIRCUIT_OPEN: "The provider is failing; its circuit is open until it recovers",
    RATE_LIMITED: "The provider's request budget for this minute is used up",
}
# Failures after which an approved same-policy fallback may be tried.
_FALLBACK_CODES = frozenset({"stage_timeout", CIRCUIT_OPEN, RATE_LIMITED})


def _health_session(claim: ClaimedJob):
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=claim.workspace_id)
    return db


def _admit(claim: ClaimedJob, resolved: ResolvedManifest, settings: Settings) -> Admission:
    limits = parse_rate_limits(settings.provider_rate_limits)
    limit = limits.get(resolved.manifest_id) if resolved.manifest is not None else None
    with _health_session(claim) as db:
        admission = admit(db, resolved, limit_per_minute=limit, now=database_now(db))
        db.commit()
    return admission


def _record_provider_outcome(
    claim: ClaimedJob,
    resolved: ResolvedManifest,
    settings: Settings,
    *,
    failure: str | None = None,
    retry_after_seconds: float | None = None,
) -> None:
    with _health_session(claim) as db:
        now = database_now(db)
        if failure is None:
            record_success(db, resolved, now=now)
        else:
            record_failure(
                db,
                resolved,
                code=failure,
                retry_after_seconds=retry_after_seconds,
                threshold=settings.provider_circuit_failure_threshold,
                cooldown=timedelta(seconds=settings.provider_circuit_cooldown_seconds),
                now=now,
            )
        db.commit()


def _call_provider[T](
    claim: ClaimedJob,
    resolved: ResolvedManifest,
    make_call: Callable[[], Coroutine[Any, Any, T]],
    *,
    heartbeat: LeaseHeartbeat,
    policy: LeasePolicy,
    settings: Settings,
) -> T:
    """One provider call under circuit, request window and deadline (WP08-T05).

    Only networked endpoints have a circuit and a window; an open circuit or
    a spent window ends the attempt (retryable, with the time to wait) before
    the call.  Transient failures and timeouts count against the circuit;
    permanent ones (authentication, schema) do not: retrying cannot fix them.
    """
    networked = resolved.placement.networked
    if networked:
        admission = _admit(claim, resolved, settings)
        if not admission.allowed:
            assert admission.code is not None
            raise ProcessingError(
                admission.code,
                _ADMISSION_MESSAGES[admission.code],
                retryable=True,
                retry_after_seconds=admission.retry_after_seconds,
            )
    endpoint = resolved.placement.endpoint
    try:
        if networked and endpoint is not None:
            # The only permit an adapter can hold (WP08-T06): this endpoint,
            # for this approved call, nothing else.
            with egress.permit(endpoint):
                result = _run_stage(heartbeat, make_call(), policy, deadline_at=claim.deadline_at)
        else:
            result = _run_stage(heartbeat, make_call(), policy, deadline_at=claim.deadline_at)
    except ProviderError as exc:
        if networked and exc.retryable:
            _record_provider_outcome(
                claim,
                resolved,
                settings,
                failure=exc.code,
                retry_after_seconds=exc.retry_after_seconds,
            )
        raise
    except ProcessingError as exc:
        if networked and exc.code == "stage_timeout":
            _record_provider_outcome(claim, resolved, settings, failure=exc.code)
        raise
    if networked:
        _record_provider_outcome(claim, resolved, settings)
    return result


def _use_manifest(record: StageRecord, resolved: ResolvedManifest) -> None:
    record.manifest_id = resolved.manifest_id
    record.manifest_version = resolved.version
    record.manifest_digest = resolved.digest


def _materialize_recording(
    claim: ClaimedJob,
    *,
    storage: ObjectStorage,
    target_directory: Path,
) -> Path:
    allowed_suffixes = {".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac", ".mp4", ".webm"}
    suffix = Path(claim.recording_filename).suffix.lower()
    if suffix not in allowed_suffixes:
        suffix = ".media"
    destination = target_directory / f"recording{suffix}"
    digest = hashlib.sha256()
    observed = 0
    try:
        reader = storage.open_reader(claim.workspace_id, claim.recording_key)
        with reader, destination.open("xb") as output:
            os.chmod(destination, 0o600)
            while chunk := reader.read(1024 * 1024):
                observed += len(chunk)
                if observed > claim.recording_size_bytes:
                    raise ProcessingError(
                        "recording_integrity_failed",
                        "Stored recording failed integrity verification",
                        retryable=False,
                    )
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    except StorageError as exc:
        raise ProcessingError(
            exc.code,
            "Stored recording is temporarily unavailable",
            retryable=exc.code != "object_not_found",
        ) from exc
    if observed != claim.recording_size_bytes or digest.hexdigest() != claim.recording_sha256:
        raise ProcessingError(
            "recording_integrity_failed",
            "Stored recording failed integrity verification",
            retryable=False,
        )
    return destination


def _speaker_for_segment(
    start_seconds: float,
    end_seconds: float,
    diarization: DiarizationResult | None,
) -> str:
    if diarization is None or diarization.status != DiarizationStatus.AVAILABLE:
        return UNKNOWN_SPEAKER_LABEL
    best_label = UNKNOWN_SPEAKER_LABEL
    best_overlap = 0.0
    for segment in diarization.segments:
        overlap = max(
            0.0,
            min(end_seconds, segment.end_seconds) - max(start_seconds, segment.start_seconds),
        )
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = segment.speaker_label
    return best_label


def _transcript_turns(
    asr: ASRResult,
    diarization: DiarizationResult | None,
) -> list[TranscriptTurn]:
    return [
        TranscriptTurn(
            text=segment.text,
            speaker_label=_speaker_for_segment(
                segment.start_seconds,
                segment.end_seconds,
                diarization,
            ),
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
        )
        for segment in asr.segments
    ]


def _analysis_input(
    claim: ClaimedJob, text: str, turns: list[TranscriptTurn]
) -> tuple[str, list[TranscriptTurn]]:
    """What the intelligence provider receives (WP07-T06, ADR 0024).

    Under the claimed policy the provider gets the redacted text only.  Turns
    are redacted in order with one pseudonymizer, exactly like the persisted
    redacted version of the revision these turns become, so placeholders in
    the analysis output match the redacted transcript.
    """
    if not claim.pii_classes:
        return text, turns
    classes = claim.privacy_classes
    pseudonymizer = Pseudonymizer()
    redacted_turns = [
        turn.model_copy(update={"text": redact(turn.text, classes, pseudonymizer=pseudonymizer)[0]})
        for turn in turns
    ]
    redacted_text = redact(text, classes, pseudonymizer=pseudonymizer)[0]
    return redacted_text, redacted_turns


def _action_due_at(due_date) -> datetime | None:
    if due_date is None:
        return None
    return datetime.combine(due_date, time.min, tzinfo=UTC)


def persist_stage_runs(
    db: Session,
    job: ProcessingJob,
    records: list[StageRecord],
) -> None:
    """Replace the stage rows of the job's current attempt with the truthful outcome."""
    seen = {record.stage for record in records}
    complete = list(records) + [not_reached(stage) for stage in STAGE_ORDER if stage not in seen]
    db.execute(
        delete(ProcessingStageRun).where(
            ProcessingStageRun.job_id == job.id,
            ProcessingStageRun.attempt == job.attempt_count,
        )
    )
    for sequence, record in enumerate(complete):
        db.add(
            ProcessingStageRun(
                workspace_id=job.workspace_id,
                meeting_id=job.meeting_id,
                job_id=job.id,
                attempt=job.attempt_count,
                sequence=sequence,
                stage=record.stage.value,
                state=record.state,
                reason_code=(record.reason_code or None) and record.reason_code[:80],
                reason_message=(record.reason_message or None) and record.reason_message[:500],
                provider=record.provider,
                model=record.model,
                is_mock=record.is_mock,
                latency_ms=record.latency_ms,
                started_at=record.started_at,
                finished_at=record.finished_at,
                checkpoint_id=record.checkpoint_id,
                reused=record.reused,
                manifest_id=record.manifest_id,
                manifest_version=record.manifest_version,
                manifest_digest=record.manifest_digest,
            )
        )


def _stage_record(stage: StageName, manifests: dict[str, ResolvedManifest]) -> StageRecord:
    """A stage record carrying the manifest its adapter resolved to (WP08-T01)."""
    record = StageRecord(stage=stage)
    resolved = manifests.get(stage.value)
    if resolved is not None:
        record.manifest_id = resolved.manifest_id
        record.manifest_version = resolved.version
        record.manifest_digest = resolved.digest
    return record


def _placement_entry(stage: str, resolved: ResolvedManifest) -> dict[str, Any]:
    """What one stage of this attempt is configured to use and where its data
    goes (no credential; the endpoint is the declared or identity endpoint)."""
    placement = resolved.placement
    return {
        "stage": stage,
        "provider": resolved.identity.provider,
        "model": resolved.identity.model,
        "manifest_id": resolved.manifest_id,
        "manifest_version": resolved.version,
        "manifest_digest": resolved.digest,
        "manifest_status": (
            resolved.manifest.status.value if resolved.manifest is not None else "unregistered"
        ),
        "endpoint_kind": placement.endpoint_kind.value,
        "region": placement.region,
        "endpoint": placement.endpoint,
        "placement": "declared" if placement.declared else "inferred",
    }


def _record_policy_snapshot(
    claim: ClaimedJob,
    stages: list[dict[str, Any]],
    *,
    settings: Settings,
) -> None:
    """Write the attempt's policy snapshot before any stage runs (fenced)."""
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        job, now = _lock_owned_or_cancelled(db, claim.lease)
        if job is None:
            raise ProcessingError(
                "meeting_unavailable",
                "The meeting is no longer available",
                retryable=False,
            )
        record_run_snapshot(
            db,
            workspace_id=claim.workspace_id,
            meeting_id=claim.meeting_id,
            job_id=claim.job_id,
            attempt=job.attempt_count,
            effective=claim.ai_policy,
            text_privacy_digest=claim.privacy_digest,
            strict_manifests=is_strict(settings),
            stages=stages,
            now=now,
        )
        db.commit()


def _stage_summary(records: list[StageRecord]) -> list[dict[str, str | None]]:
    return [
        {"stage": record.stage.value, "state": record.state.value, "reason": record.reason_code}
        for record in records
    ]


# --- stage checkpoints (WP06-T03) ---------------------------------------------


def _stage_context(claim: ClaimedJob) -> ckpt.StageContext:
    return ckpt.StageContext(
        lease=claim.lease,
        meeting_id=claim.meeting_id,
        recording_sha256=claim.recording_sha256,
        recording_size_bytes=claim.recording_size_bytes,
        recording_duration_seconds=claim.recording_duration_seconds,
        language=claim.language,
    )


def _mark_reused(record: StageRecord, reused: ckpt.ReusedStage) -> None:
    record.finish(
        StageState.COMPLETED,
        reason=StageReason.CHECKPOINT_REUSED,
        message="Reused the committed result of an earlier attempt",
        provider=reused.provider,
        model=reused.model,
        is_mock=reused.is_mock,
    )
    record.checkpoint_id = reused.checkpoint_id
    record.reused = True


def _elapsed_ms(record: StageRecord) -> int:
    return max(0, round((datetime.now(UTC) - record.started_at).total_seconds() * 1000))


def _commit_stage(
    context: ckpt.StageContext,
    identity: ckpt.StageIdentity,
    result: ProviderSchema,
    *,
    record: StageRecord,
    storage: ObjectStorage,
    settings: Settings,
) -> tuple[str, str]:
    """Declare, write, verify and publish the artifact of a completed stage.

    Returns ``(checkpoint_id, artifact_sha256)``.  Storage faults abandon the
    intent and fail the attempt as *transient* (the next attempt writes its
    own object); cancellation and lost leases end the attempt as elsewhere.
    """
    try:
        intent = ckpt.begin_artifact(context, identity)
    except ckpt.StageCancelled as exc:
        raise ProcessingError(
            "meeting_unavailable", "The meeting is no longer available", retryable=False
        ) from exc
    try:
        stored = ckpt.store_artifact(storage, context, intent, result, settings=settings)
    except (StorageError, ckpt.ArtifactInvalid, ckpt.ArtifactUnverified) as exc:
        ckpt.abandon_intent(context, intent, reason=ckpt.ABANDONED_WRITE_FAILED)
        logger.warning(
            "STAGE_ARTIFACT_WRITE_FAILED job_id=%s stage=%s error_type=%s",
            context.job_id,
            identity.stage.value,
            type(exc).__name__,
        )
        raise ProcessingError(
            "artifact_write_failed",
            "A stage result could not be stored",
            retryable=True,
        ) from exc
    try:
        checkpoint_id = ckpt.publish_checkpoint(
            context,
            intent,
            stored,
            result,
            latency_ms=_elapsed_ms(record),
            settings=settings,
        )
    except ckpt.CheckpointConflict as exc:
        # Another published checkpoint already holds this identity: the
        # next attempt reuses it; our object is unreferenced and cleaned up.
        ckpt.abandon_intent(context, intent, reason=ckpt.INVALID_SUPERSEDED)
        raise ProcessingError(
            "checkpoint_conflict",
            "A committed result for this stage already exists",
            retryable=True,
        ) from exc
    if checkpoint_id is None:
        raise ProcessingError(
            "meeting_unavailable", "The meeting is no longer available", retryable=False
        )
    return checkpoint_id, stored.sha256


def _identity_params(identity: ckpt.StageIdentity | None) -> dict[str, Any]:
    if identity is None:
        return {}
    provider = identity.manifest.get("provider") or {}
    params = provider.get("params") if isinstance(provider, dict) else None
    return dict(params) if isinstance(params, dict) else {}


def _persist_success(
    claim: ClaimedJob,
    *,
    asr: ASRResult,
    diarization: DiarizationResult | None,
    intelligence: IntelligenceResult | None,
    turns: list[TranscriptTurn],
    records: list[StageRecord],
    settings: Settings,
    asr_identity: ckpt.StageIdentity | None = None,
    intelligence_identity: ckpt.StageIdentity | None = None,
) -> str:
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        # Fence first: the row lock is held until commit, so nothing below can
        # be written by an attempt that lost its lease (partially or at all).
        job, now = _lock_owned_or_cancelled(db, claim.lease)
        if job is None:
            return "cancelled"
        # Meeting row lock (WP07-T03): a tombstone committed by a concurrent
        # deletion or retention purge is seen here; nothing publishes after it.
        meeting = db.get(Meeting, claim.meeting_id, with_for_update=True, populate_existing=True)
        if meeting is None or meeting.deleted_at is not None:
            raise ProcessingError(
                "meeting_unavailable",
                "The meeting is no longer available",
                retryable=False,
            )

        # Analysis results are replaced by this outcome; transcript revisions
        # and speaker rows are appended to (WP04-T02): earlier revisions stay
        # readable and a person's speaker names survive reprocessing.
        db.execute(
            delete(ActionItem).where(
                ActionItem.meeting_id == claim.meeting_id,
                ActionItem.created_by == "ai",
            )
        )
        db.execute(delete(MeetingSummary).where(MeetingSummary.meeting_id == claim.meeting_id))

        by_stage = {record.stage: record for record in records}
        asr_record = by_stage.get(StageName.ASR)
        intelligence_record = by_stage.get(StageName.INTELLIGENCE)
        previous_revision = current_revision(
            db, workspace_id=claim.workspace_id, meeting_id=claim.meeting_id
        )
        drafts = [
            SegmentDraft(
                sequence=index,
                speaker_key=turn.speaker_label,
                start_ms=round((turn.start_seconds or 0) * 1000),
                end_ms=round((turn.end_seconds or 0) * 1000),
                text=turn.text,
                confidence=(asr.segments[index].confidence if index < len(asr.segments) else None),
                language=asr.language,
            )
            for index, turn in enumerate(turns)
        ]
        revision, persisted_segments = create_revision(
            db,
            meeting=meeting,
            source=TranscriptSource.ASR,
            reason=REASON_REPROCESSING if previous_revision is not None else REASON_PROCESSING,
            language=asr.language or claim.language,
            drafts=drafts,
            now=now,
            provenance=EngineProvenance(
                job_id=job.id,
                attempt=job.attempt_count,
                checkpoint_id=asr_record.checkpoint_id if asr_record is not None else None,
                config_hash=asr_identity.input_hash if asr_identity is not None else None,
                asr_provider=asr.provider,
                asr_model=asr.model,
                diarization_provider=(diarization.provider if diarization is not None else None),
                diarization_status=(
                    diarization.status.value if diarization is not None else "skipped"
                ),
                is_mock=asr.is_mock or (diarization.is_mock if diarization is not None else False),
            ),
        )
        if claim.pii_classes:
            # The redacted version the analysis input corresponds to (WP07-T06).
            redact_revision(
                db,
                revision=revision,
                classes=claim.privacy_classes,
                settings=settings,
                now=now,
            )
        speaker_labels = {turn.speaker_label for turn in turns}
        ensure_speakers(
            db,
            workspace_id=claim.workspace_id,
            meeting_id=claim.meeting_id,
            labels=sorted(speaker_labels or ({UNKNOWN_SPEAKER_LABEL} if turns else set())),
        )
        # Evidence (WP04-T03): every reference the provider offered is resolved
        # against the segments of the revision it read; anything else is
        # rejected and counted, never repaired or guessed.
        plan = EvidencePlan()
        summary_status = EvidenceStatus.INSUFFICIENT
        key_points: list[dict[str, Any]] = []
        pending_actions: list[ActionItem] = []
        if intelligence is not None:
            summary_references = resolve_turn_references(
                intelligence.summary_source_turn_indexes, persisted_segments
            )
            summary_status = summary_references.status
            plan.cite(
                meeting=meeting,
                revision=revision,
                references=summary_references,
                finding_type=FINDING_SUMMARY,
                now=now,
            )
            for index, insight in enumerate(intelligence.insights):
                references = resolve_turn_references(
                    insight.source_turn_indexes, persisted_segments
                )
                evidence_ids = plan.cite(
                    meeting=meeting,
                    revision=revision,
                    references=references,
                    finding_type=FINDING_INSIGHT,
                    finding_index=index,
                    confidence=insight.confidence,
                    now=now,
                )
                key_points.append(
                    {
                        "category": insight.category,
                        "description": insight.description,
                        "confidence": insight.confidence,
                        "source_turn_indexes": insight.source_turn_indexes,
                        "evidence_ids": evidence_ids,
                        "evidence_status": references.status.value,
                    }
                )
            for action in intelligence.action_items:
                references = resolve_turn_references(action.source_turn_indexes, persisted_segments)
                action_id = new_id()
                plan.cite(
                    meeting=meeting,
                    revision=revision,
                    references=references,
                    finding_type=FINDING_ACTION_ITEM,
                    finding_id=action_id,
                    now=now,
                )
                source_segment = references.segments[0] if references.segments else None
                pending_actions.append(
                    ActionItem(
                        id=action_id,
                        workspace_id=claim.workspace_id,
                        meeting_id=claim.meeting_id,
                        description=action.description,
                        assignee_text=action.owner,
                        due_at=_action_due_at(action.due_date),
                        priority=action.priority.value,
                        source_segment_id=(
                            source_segment.id if source_segment is not None else None
                        ),
                        source_timestamp_ms=(
                            source_segment.start_ms if source_segment is not None else None
                        ),
                        created_by="ai",
                        evidence_status=references.status.value,
                    )
                )

        analysis_run = record_analysis_run(
            db,
            meeting=meeting,
            revision=revision,
            state=(
                intelligence_record.state if intelligence_record is not None else StageState.SKIPPED
            ),
            reason_code=(
                intelligence_record.reason_code if intelligence_record is not None else None
            ),
            now=now,
            job_id=job.id,
            attempt=job.attempt_count,
            checkpoint_id=(
                intelligence_record.checkpoint_id if intelligence_record is not None else None
            ),
            config_hash=(
                intelligence_identity.input_hash if intelligence_identity is not None else None
            ),
            provider=intelligence.provider if intelligence is not None else None,
            model=intelligence.model if intelligence is not None else None,
            identity_params=_identity_params(intelligence_identity),
            schema_version=ckpt.ARTIFACT_SCHEMAS[StageName.INTELLIGENCE][1],
            is_mock=intelligence.is_mock if intelligence is not None else False,
            reference_count=plan.reference_count,
            rejected_reference_count=plan.rejected_reference_count,
            input_view="redacted" if claim.pii_classes else "original",
            input_policy_digest=claim.privacy_digest,
            manifest_digest=(
                intelligence_record.manifest_digest if intelligence_record is not None else None
            ),
        )
        plan.attach(db, analysis_run)
        if intelligence is not None:
            db.add(
                MeetingSummary(
                    workspace_id=claim.workspace_id,
                    meeting_id=claim.meeting_id,
                    overview=intelligence.summary,
                    decisions=[
                        point for point in key_points if point["category"].casefold() == "decision"
                    ],
                    key_points=key_points,
                    topics=list(
                        dict.fromkeys(insight.category for insight in intelligence.insights)
                    ),
                    provider=intelligence.provider,
                    model=intelligence.model,
                    schema_version=1,
                    analysis_run_id=analysis_run.id,
                    evidence_status=summary_status.value,
                )
            )
        for pending in pending_actions:
            pending.analysis_run_id = analysis_run.id
            db.add(pending)
        db.flush()

        latency_by_stage = {record.stage: record.latency_ms or 0 for record in records}
        # Provider usage of a checkpointed stage was recorded when its
        # checkpoint was published (that attempt made the call); a reused
        # stage made no call at all. Only stages that ended without a
        # checkpoint (empty transcript, direct persistence) are recorded here.

        def _needs_usage_row(stage: StageName) -> bool:
            record = by_stage.get(stage)
            return record is None or record.checkpoint_id is None

        usage_rows: list[ProviderUsage] = []
        if _needs_usage_row(StageName.ASR):
            usage_rows.append(
                ProviderUsage(
                    workspace_id=claim.workspace_id,
                    meeting_id=claim.meeting_id,
                    job_id=claim.job_id,
                    stage="asr",
                    provider=asr.provider,
                    model=asr.model,
                    audio_seconds=claim.recording_duration_seconds,
                    latency_ms=latency_by_stage.get(StageName.ASR, 0),
                    success=True,
                )
            )
        # A provider that was not (or could not be) called produces no usage row;
        # an "unavailable" diarization must never look like a successful call.
        if (
            diarization is not None
            and diarization.status == DiarizationStatus.AVAILABLE
            and _needs_usage_row(StageName.DIARIZATION)
        ):
            usage_rows.append(
                ProviderUsage(
                    workspace_id=claim.workspace_id,
                    meeting_id=claim.meeting_id,
                    job_id=claim.job_id,
                    stage="diarization",
                    provider=diarization.provider,
                    model="default",
                    audio_seconds=claim.recording_duration_seconds,
                    latency_ms=latency_by_stage.get(StageName.DIARIZATION, 0),
                    success=True,
                )
            )
        if intelligence is not None and _needs_usage_row(StageName.INTELLIGENCE):
            usage_rows.append(
                ProviderUsage(
                    workspace_id=claim.workspace_id,
                    meeting_id=claim.meeting_id,
                    job_id=claim.job_id,
                    stage="intelligence",
                    provider=intelligence.provider,
                    model=intelligence.model,
                    latency_ms=latency_by_stage.get(StageName.INTELLIGENCE, 0),
                    success=True,
                )
            )
        db.add_all(usage_rows)

        outcome = meeting_status_for(records)
        provider_metadata = {
            "asr_provider": asr.provider,
            "asr_model": asr.model,
            "diarization_provider": diarization.provider if diarization is not None else None,
            "diarization_status": (
                diarization.status.value if diarization is not None else "skipped"
            ),
            "intelligence_provider": (intelligence.provider if intelligence is not None else None),
            "intelligence_model": intelligence.model if intelligence is not None else None,
            "mock": asr.is_mock
            or (diarization.is_mock if diarization is not None else False)
            or (intelligence.is_mock if intelligence is not None else False),
            "outcome": outcome.value,
            "stages": _stage_summary(records),
            # flat string: the audit sanitizer stringifies nested values
            "reused_stages": ",".join(record.stage.value for record in records if record.reused),
            "attempt": job.attempt_count,
            "generation": job.lease_generation,
            # "transcript*" keys are redacted by the audit sanitizer (they could
            # carry text); these are ids and a number, named accordingly.
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            "analysis_run_id": analysis_run.id,
            "evidence_count": len(plan.spans),
            "rejected_references": plan.rejected_reference_count,
            **assess_quality(
                revision=revision,
                run=analysis_run,
                summary_status=summary_status.value if intelligence is not None else None,
                action_statuses=[pending.evidence_status for pending in pending_actions],
                stage_runs=[
                    (record.stage.value, record.state, record.reason_code) for record in records
                ],
                segments=[(segment.start_ms, segment.end_ms) for segment in persisted_segments],
                speaker_count=len(speaker_labels),
                duration_seconds=claim.recording_duration_seconds,
            ).audit_details(),
        }
        # Settlement happens inside the fenced transaction: a stale attempt
        # never reaches this point, and the reservation itself is committed
        # at most once (commit_usage is idempotent on reservation state).
        commit_usage(
            db,
            job.reservation_id,
            actual_amount=min(
                job_reservation_amount(db, job.reservation_id),
                claim.recording_duration_seconds,
            ),
            provider_metadata=provider_metadata,
        )
        if job.concurrency_reservation_id:
            release_usage(db, job.concurrency_reservation_id, reason="job_completed")

        job.state = JobState.COMPLETED
        job.stage = outcome.value
        job.completed_at = now
        release_lease(job)
        meeting.status = outcome
        persist_stage_runs(db, job, records)
        record_audit_event(
            db,
            event_type="meeting.processing.completed",
            workspace_id=claim.workspace_id,
            target_type="meeting",
            target_id=claim.meeting_id,
            details=provider_metadata,
            settings=settings,
        )
        db.commit()
    return outcome.value


def job_reservation_amount(db: Session, reservation_id: str) -> int:
    from backend.database.saas_models import UsageReservation

    reservation = db.get(UsageReservation, reservation_id)
    if reservation is None:
        raise ProcessingError(
            "usage_reservation_missing",
            "Processing allowance reservation is missing",
            retryable=False,
        )
    return reservation.reserved_value


def _finalize_failure(
    claim: ClaimedJob,
    *,
    error: ProcessingError,
    settings: Settings,
    records: list[StageRecord] | None = None,
) -> str:
    """Record the failed attempt under the fence and apply the retry policy.

    Returns ``retry_scheduled`` (requeued through the outbox with back-off),
    ``failed`` (permanent), ``dead_letter`` (retry budget or job deadline
    exhausted; replayable), ``cancelled`` (meeting deleted meanwhile) or
    ``superseded`` (we no longer own the job; nothing written).
    """
    policy = RetryPolicy.from_settings(settings)
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        try:
            job, now = _lock_owned_or_cancelled(db, claim.lease)
        except StaleLeaseError:
            db.rollback()
            return "superseded"
        if job is None:
            return "cancelled"
        meeting = db.get(Meeting, claim.meeting_id)
        stage_records = list(records or [])
        for record in stage_records:
            if record.state == StageState.RUNNING:
                record.finish(
                    StageState.FAILED,
                    reason=error.code,
                    message=error.public_message,
                )
        persist_stage_runs(db, job, stage_records)

        # Bounded exponential back-off with positive jitter (WP08-T05); a
        # provider's Retry-After or an open circuit may only lengthen it.
        delay = policy.retry_delay(job.attempt_count) * (
            1 + settings.job_retry_jitter_ratio * _jitter()
        )
        if error.retry_after_seconds:
            delay = max(delay, timedelta(seconds=error.retry_after_seconds))
        due = now + delay
        verdict = retry_verdict(job, due=due) if error.retryable else None
        details: dict[str, Any] = {
            "failure_code": error.code,
            "cause": error.code,  # the audit sanitizer redacts keys containing "code"
            "failure_class": error.failure_class.value,
            "retryable": error.retryable,
            "attempt": job.attempt_count,
            "generation": job.lease_generation,
        }
        if verdict == RetryVerdict.ALLOWED:
            job.failure_code = error.code[:80]
            job.failure_message = error.public_message[:500]
            job.failure_class = error.failure_class.value
            release_lease(job)
            job.state = JobState.QUEUED
            job.stage = "retry_scheduled"
            job.retry_after = due
            rearm_dispatch(db, job, due=due, settings=settings)
            if meeting is not None and meeting.deleted_at is None:
                meeting.status = MeetingStatus.QUEUED
            details["retry_after"] = due.isoformat()
            event_type = "meeting.processing.retry_scheduled"
            result = "retry_scheduled"
        else:
            if verdict is None:
                state, code, failure_class = JobState.FAILED, error.code, error.failure_class
                message = error.public_message
                terminal_reason = "permanent"
            elif verdict == RetryVerdict.DEADLINE_EXCEEDED:
                state, code = JobState.DEAD_LETTER, JOB_DEADLINE_EXCEEDED
                failure_class = FailureClass.DEADLINE
                message = (
                    f"{error.public_message}; the job deadline passed before another "
                    "attempt could run"
                )
                terminal_reason = verdict.value
            else:
                state, code, failure_class = JobState.DEAD_LETTER, error.code, error.failure_class
                message = f"{error.public_message}; the processing retry limit was reached"
                terminal_reason = verdict.value
            park_job(
                db,
                job,
                meeting,
                state=state,
                code=code,
                message=message,
                failure_class=failure_class,
                now=now,
                release_reason=error.code,
            )
            details["terminal_reason"] = terminal_reason
            event_type = "meeting.processing.failed"
            result = state.value
        record_audit_event(
            db,
            event_type=event_type,
            workspace_id=claim.workspace_id,
            target_type="meeting",
            target_id=claim.meeting_id,
            outcome="failure",
            details=details,
            settings=settings,
        )
        db.commit()
        return result


def _finalize_unclaimable_job(
    job_id: str,
    *,
    reason: str,
    settings: Settings,
) -> str:
    factory = get_session_factory()
    with factory() as db:
        bind_job_context(db, job_id)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None:
            return "not_found"
        bind_tenant_context(db, workspace_id=job.workspace_id)
        if job.state != JobState.QUEUED:
            # Running (owned by someone) or terminal: not ours to finalize.
            return job.state.value
        meeting = db.get(Meeting, job.meeting_id)
        now = database_now(db)
        if reason == "attempts_exhausted":
            # Budget used up while queued (legacy or edited rows): keep the
            # class of the last recorded failure.
            try:
                failure_class = FailureClass(job.failure_class or "")
            except ValueError:
                failure_class = FailureClass.TRANSIENT
            state, code = JobState.DEAD_LETTER, ATTEMPTS_EXHAUSTED
            message = "Processing retry limit was reached"
        elif reason == "deadline_exceeded":
            state, code = JobState.DEAD_LETTER, JOB_DEADLINE_EXCEEDED
            failure_class = FailureClass.DEADLINE
            message = "The job deadline passed before processing could complete"
        else:
            state, code = JobState.FAILED, reason
            failure_class = FailureClass.PERMANENT
            message = "The meeting recording is unavailable"
        park_job(
            db,
            job,
            meeting,
            state=state,
            code=code,
            message=message,
            failure_class=failure_class,
            now=now,
        )
        record_audit_event(
            db,
            event_type="meeting.processing.failed",
            workspace_id=job.workspace_id,
            target_type="meeting",
            target_id=job.meeting_id,
            outcome="failure",
            details={
                "failure_code": code,
                "cause": code,
                "failure_class": failure_class.value,
                "retryable": state == JobState.DEAD_LETTER,
                "terminal_reason": reason,
            },
            settings=settings,
        )
        db.commit()
        return job.state.value


def _delete_audio_after_success(
    claim: ClaimedJob,
    *,
    storage: ObjectStorage,
    settings: Settings,
) -> None:
    if not claim.delete_audio_after_processing:
        return
    factory = get_session_factory()
    # Object deletion is not transactional: only the attempt that completed
    # the job (its generation is still on the row) may remove the recording.
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        job = db.get(ProcessingJob, claim.job_id)
        authoritative = (
            job is not None
            and job.state == JobState.COMPLETED
            and job.lease_generation == claim.lease.generation
        )
        db.rollback()
    if not authoritative:
        return

    # The ORIGINAL media object records the request first (WP05-T03); a
    # legal hold keeps the bytes and the recording.  The recording is marked
    # deleted only once the bytes are gone (WP06 invariant: nothing is
    # pretended); a failed removal leaves the row pending for the sweep.
    now = datetime.now(UTC)
    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        recording = db.scalar(
            select(Recording).where(
                Recording.workspace_id == claim.workspace_id,
                Recording.meeting_id == claim.meeting_id,
            )
        )
        if recording is None or recording.deleted_at is not None:
            return
        media = original_for_meeting(
            db, workspace_id=claim.workspace_id, meeting_id=claim.meeting_id
        )
        may_delete = True
        if media is not None:
            may_delete = request_deletion(
                db, media=media, reason="processed", actor_user_id=None, now=now, settings=settings
            )
        if not may_delete:
            record_audit_event(
                db,
                event_type="meeting.recording.retained_after_processing",
                workspace_id=claim.workspace_id,
                target_type="meeting",
                target_id=claim.meeting_id,
                details={"media_retained": True, "reason": "legal_hold"},
                settings=settings,
            )
        db.commit()
    if not may_delete:
        return
    try:
        storage.delete(claim.workspace_id, claim.recording_key)
    except Exception as exc:
        logger.error(
            "PROCESSED_AUDIO_DELETE_FAILED meeting_id=%s error_type=%s",
            claim.meeting_id,
            type(exc).__name__,
        )
        return

    with factory() as db:
        bind_tenant_context(db, workspace_id=claim.workspace_id)
        recording = db.scalar(
            select(Recording).where(
                Recording.workspace_id == claim.workspace_id,
                Recording.meeting_id == claim.meeting_id,
            )
        )
        if recording is None or recording.deleted_at is not None:
            return
        recording.deleted_at = datetime.now(UTC)
        media = original_for_meeting(
            db, workspace_id=claim.workspace_id, meeting_id=claim.meeting_id
        )
        if media is not None:
            mark_deleted(db, media=media, now=datetime.now(UTC), settings=settings)
        record_audit_event(
            db,
            event_type="meeting.recording.deleted_after_processing",
            workspace_id=claim.workspace_id,
            target_type="meeting",
            target_id=claim.meeting_id,
            details={"media_retained": False},
            settings=settings,
        )
        db.commit()


def _classify_exception(exc: Exception, *, job_id: str) -> ProcessingError:
    if isinstance(exc, StaleLeaseError):
        return ProcessingError(exc.code, exc.public_message, retryable=False)
    if isinstance(exc, ProviderError):
        return ProcessingError(
            exc.code,
            exc.public_message,
            retryable=exc.retryable,
            retry_after_seconds=exc.retry_after_seconds,
        )
    if isinstance(exc, ProcessingError):
        return exc
    if type(exc).__name__ == "SoftTimeLimitExceeded":  # Celery attempt time limit
        return ProcessingError(
            "attempt_timeout",
            "Meeting processing exceeded the attempt time limit",
            retryable=True,
        )
    logger.error(
        "JOB_PROCESSING_FAILED job_id=%s error_type=%s",
        job_id,
        type(exc).__name__,
        exc_info=True,
    )
    return ProcessingError("processing_failed", "Meeting processing failed", retryable=True)


def _diarize(
    context: ckpt.StageContext,
    record: StageRecord,
    *,
    claim: ClaimedJob,
    resolved: ResolvedManifest,
    active_providers: ProviderBundle,
    media_path: Path,
    heartbeat: LeaseHeartbeat,
    policy: LeasePolicy,
    storage: ObjectStorage,
    settings: Settings,
) -> tuple[DiarizationResult, str | None]:
    """Run (or reuse) the diarization stage; returns the result and the
    artifact digest of a committed or reused checkpoint."""
    request = DiarizationRequest(audio_path=str(media_path))
    identity = ckpt.stage_identity(
        context,
        StageName.DIARIZATION,
        provider=active_providers.diarization.identity(),
        request={"expected_speaker_count": request.expected_speaker_count},
    )
    reused = ckpt.resume_stage(context, identity, storage=storage, settings=settings)
    if reused is not None:
        assert isinstance(reused.result, DiarizationResult)
        _mark_reused(record, reused)
        return reused.result, reused.artifact_sha256
    diarization = _call_provider(
        claim,
        resolved,
        lambda: active_providers.diarization.diarize(request),
        heartbeat=heartbeat,
        policy=policy,
        settings=settings,
    )
    if diarization.status != DiarizationStatus.AVAILABLE:
        # An unavailable diarization is truthful but not a committed result:
        # a later attempt asks again.
        record.finish(
            StageState.UNAVAILABLE,
            reason=StageReason.DIARIZATION_UNAVAILABLE,
            message="Speaker diarization is not available; speakers are unknown",
            provider=diarization.provider,
            is_mock=diarization.is_mock,
        )
        return diarization, None
    record.checkpoint_id, sha = _commit_stage(
        context, identity, diarization, record=record, storage=storage, settings=settings
    )
    record.finish(StageState.COMPLETED, provider=diarization.provider, is_mock=diarization.is_mock)
    return diarization, sha


def _analyze(
    claim: ClaimedJob,
    *,
    selection: Selection,
    adapters: list[Any],
    classes_for: Callable[[ResolvedManifest], tuple[str, ...]],
    record: StageRecord,
    context: ckpt.StageContext,
    asr: ASRResult,
    turns: list[TranscriptTurn],
    upstream: dict[str, Any],
    heartbeat: LeaseHeartbeat,
    policy: LeasePolicy,
    storage: ObjectStorage,
    settings: Settings,
) -> tuple[IntelligenceResult, ckpt.StageIdentity, ClaimedJob]:
    """The analysis stage over its eligible candidates in rank order (WP08-T05).

    Each candidate gets the text its placement permits (T03) and its own
    checkpoint identity.  A committed result of any eligible candidate is
    reused before anything is called.  A transient failure moves to the next
    eligible candidate only when the policy approved fallback (otherwise there
    is none in the selection); permanent failures stop.  Returns the result,
    its identity and the claim whose text policy produced it.
    """
    plans: list[tuple[int, ClaimedJob, IntelligenceRequest, ckpt.StageIdentity]] = []
    for index in selection.order:
        candidate_claim = dataclasses.replace(
            claim, pii_classes=classes_for(selection.candidates[index])
        )
        text, analysis_turns = _analysis_input(candidate_claim, asr.text, turns)
        request = IntelligenceRequest(
            transcript_text=text,
            turns=analysis_turns,
            language=asr.language,
            call_id=claim.meeting_id,
        )
        identity_request: dict[str, Any] = {"language": request.language}
        if candidate_claim.privacy_digest is not None:
            # Only under a policy, so existing checkpoints keep their identity.
            identity_request["privacy_policy"] = candidate_claim.privacy_digest
        identity = ckpt.stage_identity(
            context,
            StageName.INTELLIGENCE,
            provider=adapters[index].identity(),
            request=identity_request,
            upstream=upstream,
        )
        plans.append((index, candidate_claim, request, identity))

    for index, candidate_claim, _request, identity in plans:
        reused = ckpt.resume_stage(context, identity, storage=storage, settings=settings)
        if reused is not None:
            assert isinstance(reused.result, IntelligenceResult)
            _use_manifest(record, selection.candidates[index])
            _mark_reused(record, reused)
            return reused.result, identity, candidate_claim

    failed: list[str] = []
    for position, (index, candidate_claim, request, identity) in enumerate(plans):
        resolved, adapter = selection.candidates[index], adapters[index]
        try:
            result = _call_provider(
                candidate_claim,
                resolved,
                lambda adapter=adapter, request=request: adapter.analyze(request),
                heartbeat=heartbeat,
                policy=policy,
                settings=settings,
            )
        except (ProviderError, ProcessingError) as exc:
            transient = (isinstance(exc, ProviderError) and exc.retryable) or (
                isinstance(exc, ProcessingError) and exc.code in _FALLBACK_CODES
            )
            if transient and position + 1 < len(plans):
                failed.append(exc.code)
                _ensure_policy_current(claim)
                continue
            raise
        _use_manifest(record, resolved)
        record.checkpoint_id, _sha = _commit_stage(
            context, identity, result, record=record, storage=storage, settings=settings
        )
        record.finish(
            StageState.COMPLETED,
            reason=StageReason.FALLBACK_USED if failed else None,
            message=(
                f"The primary analysis provider failed ({', '.join(failed)}); this result "
                "comes from an approved same-policy fallback"
                if failed
                else None
            ),
            provider=result.provider,
            model=result.model,
            is_mock=result.is_mock,
        )
        return result, identity, candidate_claim
    raise AssertionError("an allowed analysis stage has at least one eligible candidate")


def process_job(
    job_id: str,
    *,
    worker_id: str,
    settings: Settings | None = None,
    providers: ProviderBundle | None = None,
    storage: ObjectStorage | None = None,
) -> dict[str, str]:
    settings = settings or get_settings()
    policy = LeasePolicy.from_settings(settings)
    claim, status = _claim_job(job_id, worker_id=worker_id, settings=settings)
    if claim is None:
        if status in {"attempts_exhausted", "deadline_exceeded", "recording_missing"}:
            status = _finalize_unclaimable_job(
                job_id,
                reason=status,
                settings=settings,
            )
        return {"job_id": job_id, "status": status}

    records: list[StageRecord] = []
    heartbeat = LeaseHeartbeat(claim.lease, policy).start()
    try:
        active_storage = storage or create_object_storage(settings)
        active_providers = providers or create_provider_bundle(settings)
        # Model preflight (WP08-T01): every stage adapter must match a
        # registered manifest (a qualified one in strict mode) before any
        # stage runs; failures are typed and not retried.
        manifests = resolve_bundle(active_providers, settings)
        register_resolved(manifests.values())
        # Same-policy fallback candidates (WP08-T05): analysis only; each needs
        # its own manifest like any adapter.
        fallback_manifests = resolve_fallbacks(active_providers, settings).get("intelligence", [])
        register_resolved(fallback_manifests)
        intelligence_adapters = [
            active_providers.intelligence,
            *(active_providers.fallbacks.get("intelligence", ()) if fallback_manifests else ()),
        ]
        candidates = {stage: [resolved] for stage, resolved in manifests.items()}
        candidates["intelligence"] = [manifests["intelligence"], *fallback_manifests]
        # Eligibility (WP08-T03): the workspace policy and capability decide
        # before anything is called; a refused adapter is never called.  Only
        # the eligible reach ranking by health (open circuits last, T05).
        ai_policy = claim.ai_policy.policy
        workspace_classes = claim.pii_classes

        def classes_for(resolved: ResolvedManifest) -> tuple[str, ...]:
            # A cloud analysis endpoint under ``cloud_text: redacted`` gets the
            # redacted transcript even when the workspace shows originals.
            return analysis_text_classes(
                workspace_classes, resolved.placement, ai_policy.intelligence
            )

        with _health_session(claim) as health_db:
            rank = health_rank(health_db, now=database_now(health_db))
            selections: dict[str, Selection] = {
                stage: select_candidate(
                    stage,
                    stage_candidates,
                    ai_policy.stage(stage),
                    language=claim.language,
                    text_redacted=lambda resolved: bool(classes_for(resolved)),
                    rank=rank,
                    fallbacks_from=1,
                )
                for stage, stage_candidates in candidates.items()
            }
        first_analysis = selections["intelligence"].chosen
        if first_analysis is not None:
            claim = dataclasses.replace(claim, pii_classes=classes_for(first_analysis))
        # Policy snapshot (WP08-T02): the policy version, model placement and
        # decision of every candidate this attempt runs under, before any stage.
        _record_policy_snapshot(
            claim,
            [
                {
                    **_placement_entry(stage, resolved),
                    **selection.decisions[index].entry(),
                    "candidate": "primary" if index == 0 else "fallback",
                    "rank": selection.order.index(index) if index in selection.order else None,
                }
                for stage, selection in selections.items()
                for index, resolved in enumerate(selection.candidates)
            ],
            settings=settings,
        )
        asr_decision = selections["asr"].decision
        if not asr_decision.allowed:
            # No transcript can exist without speech recognition: a visible,
            # permanent failure; nothing is sent anywhere else instead.
            records.append(_stage_record(StageName.ASR, manifests))
            raise ProcessingError(
                asr_decision.reason or "provider_not_permitted",
                asr_decision.message or "The workspace AI policy refuses this stage",
                retryable=False,
            )
        with tempfile.TemporaryDirectory(prefix="homayar-job-") as temporary_name:
            temporary_dir = Path(temporary_name)
            os.chmod(temporary_dir, 0o700)
            materialize = StageRecord(stage=StageName.MATERIALIZE)
            records.append(materialize)
            media_path = _materialize_recording(
                claim,
                storage=active_storage,
                target_directory=temporary_dir,
            )
            materialize.finish(StageState.COMPLETED)

            context = _stage_context(claim)

            _ensure_policy_current(claim)
            _set_stage(claim, stage="transcribing")
            asr_record = _stage_record(StageName.ASR, manifests)
            records.append(asr_record)
            asr_request = ASRRequest(audio_path=str(media_path), language=claim.language)
            asr_identity = ckpt.stage_identity(
                context,
                StageName.ASR,
                provider=active_providers.asr.identity(),
                request={
                    "language": asr_request.language,
                    "beam_size": asr_request.beam_size,
                    "vad_filter": asr_request.vad_filter,
                },
            )
            asr_reused = ckpt.resume_stage(
                context, asr_identity, storage=active_storage, settings=settings
            )
            asr_sha: str | None = None
            intelligence_identity: ckpt.StageIdentity | None = None
            if asr_reused is not None:
                asr = asr_reused.result
                assert isinstance(asr, ASRResult)
                asr_sha = asr_reused.artifact_sha256
            else:
                asr = _call_provider(
                    claim,
                    manifests["asr"],
                    lambda: active_providers.asr.transcribe(asr_request),
                    heartbeat=heartbeat,
                    policy=policy,
                    settings=settings,
                )
            diarization: DiarizationResult | None = None
            intelligence: IntelligenceResult | None = None
            turns: list[TranscriptTurn] = []
            if not asr.text.strip() and not asr.segments:
                # Silence or unintelligible audio is an honest outcome, not a
                # worker failure: the audio was processed, nothing was found.
                # Not a checkpoint: only completed results are committed.
                asr_record.finish(
                    StageState.INSUFFICIENT_EVIDENCE,
                    reason=StageReason.EMPTY_TRANSCRIPT,
                    message="Speech recognition returned an empty transcript",
                    provider=asr.provider,
                    model=asr.model,
                    is_mock=asr.is_mock,
                )
                for stage in (StageName.DIARIZATION, StageName.INTELLIGENCE):
                    records.append(
                        StageRecord(stage=stage).finish(
                            StageState.SKIPPED,
                            reason=StageReason.NO_TRANSCRIPT,
                            message="Skipped because no transcript was produced",
                        )
                    )
            else:
                if asr_reused is not None:
                    _mark_reused(asr_record, asr_reused)
                else:
                    asr_record.checkpoint_id, asr_sha = _commit_stage(
                        context,
                        asr_identity,
                        asr,
                        record=asr_record,
                        storage=active_storage,
                        settings=settings,
                    )
                    asr_record.finish(
                        StageState.COMPLETED,
                        provider=asr.provider,
                        model=asr.model,
                        is_mock=asr.is_mock,
                    )

                _ensure_policy_current(claim)
                _set_stage(claim, stage="diarizing")
                diarization_record = _stage_record(StageName.DIARIZATION, manifests)
                records.append(diarization_record)
                diarization_decision = selections["diarization"].decision
                diarization_sha: str | None = None
                if not diarization_decision.allowed:
                    # Refused by policy: speakers stay unknown, nothing is sent.
                    diarization_record.finish(
                        StageState.UNAVAILABLE,
                        reason=diarization_decision.reason,
                        message=diarization_decision.message,
                    )
                else:
                    diarization, diarization_sha = _diarize(
                        context,
                        diarization_record,
                        claim=claim,
                        resolved=manifests["diarization"],
                        active_providers=active_providers,
                        media_path=media_path,
                        heartbeat=heartbeat,
                        policy=policy,
                        storage=active_storage,
                        settings=settings,
                    )
                turns = _transcript_turns(asr, diarization)
                if not turns and asr.text.strip():
                    turns = [TranscriptTurn(text=asr.text, speaker_label=UNKNOWN_SPEAKER_LABEL)]

                _ensure_policy_current(claim)
                _set_stage(claim, stage="analyzing")
                intelligence_record = _stage_record(StageName.INTELLIGENCE, manifests)
                records.append(intelligence_record)
                intelligence_decision = selections["intelligence"].decision
                if not intelligence_decision.allowed:
                    # Refused by policy: no analysis and no substitute provider;
                    # the transcript is kept and the meeting is partial.
                    intelligence_record.finish(
                        StageState.UNAVAILABLE,
                        reason=intelligence_decision.reason,
                        message=intelligence_decision.message,
                    )
                else:
                    intelligence, intelligence_identity, claim = _analyze(
                        claim,
                        selection=selections["intelligence"],
                        adapters=intelligence_adapters,
                        classes_for=classes_for,
                        record=intelligence_record,
                        context=context,
                        asr=asr,
                        turns=turns,
                        upstream={
                            "asr_artifact_sha256": asr_sha,
                            "diarization_artifact_sha256": diarization_sha or "unavailable",
                            "turns_version": ckpt.TURNS_VERSION,
                        },
                        heartbeat=heartbeat,
                        policy=policy,
                        storage=active_storage,
                        settings=settings,
                    )
        result = _persist_success(
            claim,
            asr=asr,
            diarization=diarization,
            intelligence=intelligence,
            turns=turns,
            records=records,
            settings=settings,
            asr_identity=asr_identity,
            intelligence_identity=intelligence_identity,
        )
        heartbeat.stop()
        if result in {MeetingStatus.COMPLETED.value, MeetingStatus.PARTIAL.value}:
            _delete_audio_after_success(
                claim,
                storage=active_storage,
                settings=settings,
            )
        return {"job_id": job_id, "status": result}
    except Exception as exc:
        error = _classify_exception(exc, job_id=job_id)
    finally:
        heartbeat.stop()

    if error.code == StaleLeaseError.code:
        # We are not the owner any more: nothing was written by this attempt
        # after the fence, and the owner (or recovery) decides the job's fate.
        logger.warning(
            "JOB_ATTEMPT_SUPERSEDED job_id=%s generation=%s",
            job_id,
            claim.lease.generation,
        )
        return {"job_id": job_id, "status": "superseded", "code": error.code}

    outcome = _finalize_failure(
        claim,
        error=error,
        settings=settings,
        records=records,
    )
    if outcome in {"retry_scheduled", "cancelled", "superseded"}:
        return {"job_id": job_id, "status": outcome, "code": error.code}
    return {"job_id": job_id, "status": "failed", "code": error.code}
