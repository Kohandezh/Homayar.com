"""Stage checkpoints and attempt-scoped artifacts (WP06-T03).

A *checkpoint* is a committed stage result (ASR, diarization, analysis) with
enough provenance to decide whether a later attempt may reuse it instead of
calling the provider again.  It extends the lease/fencing model of WP06-T04:
nothing here is a second job system or state machine.

Identity
--------
``input_hash`` is the SHA-256 of a canonical JSON *manifest* of the declared
inputs of the stage: workspace and meeting scope, stage contract version,
artifact schema version, the source digest (recording SHA-256 and size,
materialisation version), the provider's ``ProviderIdentity`` (provider,
model, result-affecting parameters — never a credential), the request
parameters and, for the analysis stage, the digests of the upstream
artifacts it consumed plus the turn-building version.  Attempt ids, worker
ids, generations and timestamps are *not* inputs; they are execution
identity and live on the row separately (``attempt``, ``attempt_id``,
``lease_generation``).  Matching hashes prove matching declared inputs, not
model quality.

Reuse
-----
A checkpoint is reused only when every condition holds: reuse is enabled by
policy, the row is ``PUBLISHED`` (never a mere object on disk), its identity
matches, the meeting and recording are alive, and its artifact exists,
matches the recorded size and SHA-256 and validates against the stage
schema.  A missing or corrupt artifact invalidates the checkpoint (fenced
write) and the stage is recomputed.  Downstream invalidation is inherent:
the analysis manifest contains the upstream artifact digests, so a
recomputed transcript changes the analysis identity while an unchanged
transcript keeps it; changed audio changes every stage.

Artifact writes
---------------
Objects are written through the storage abstraction under an attempt-scoped
token (``art-<attempt id>-<stage>``): retries and stale workers write their
own objects, never over another attempt's; the same token with the same
content is idempotent, with different content a conflict.  Commit sequence
per stage: (1) declare the intent as a ``PENDING`` row under the fence, (2)
write the object, (3) read it back and verify size, digest and schema,
(4) re-lock the job, prove the lease, and publish the row (``PUBLISHED``) in
one transaction that also records provider usage.  A stale attempt can leave
an unreferenced object; it can never publish it.  Cleanup removes objects of
never-published or invalidated rows after a grace period, never a published
artifact of a live meeting, never a row whose attempt still holds the lease.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    CheckpointState,
    JobState,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    ProviderUsage,
    Recording,
    StageCheckpoint,
)
from backend.database.tenant_context import (
    SERVICE_SCOPE_USAGE_RECONCILIATION,
    bind_service_scope,
    bind_tenant_context,
)
from backend.jobs.leases import (
    Lease,
    StaleLeaseError,
    database_now,
    lock_owned_or_cancelled,
)
from backend.jobs.states import StageName
from backend.providers.base import ProviderIdentity
from backend.providers.schemas import (
    ASRResult,
    DiarizationResult,
    IntelligenceResult,
    ProviderSchema,
)
from backend.services.logging_service import logger
from backend.storage import (
    ObjectStorage,
    StorageError,
    create_object_storage,
)
from backend.storage.base import object_key_for

CONTRACT_VERSION = 1
# Materialisation contract: the recording bytes are verified against the
# stored SHA-256 and handed to the provider unchanged (no resampling, no
# normalisation). Bump when preprocessing changes what a provider sees.
MATERIALIZE_VERSION = 1
# ``backend.jobs.processor._transcript_turns`` (speaker assignment by overlap).
TURNS_VERSION = 1
ARTIFACT_CONTENT_TYPE = "application/json"

ARTIFACT_SCHEMAS: dict[StageName, tuple[type[ProviderSchema], int]] = {
    StageName.ASR: (ASRResult, 1),
    StageName.DIARIZATION: (DiarizationResult, 1),
    # 2: summary_source_turn_indexes (WP04-T03) — earlier artifacts lack
    # summary evidence and are not reused for a new analysis.
    # 3: summary_fields, decisions, key_moments (HOM-61).
    StageName.INTELLIGENCE: (IntelligenceResult, 3),
}

INVALID_ARTIFACT_MISSING = "artifact_missing"
INVALID_ARTIFACT_CORRUPT = "artifact_corrupt"
INVALID_MEETING_DELETED = "meeting_deleted"
INVALID_SUPERSEDED = "superseded"
ABANDONED_NOT_PUBLISHED = "not_published"
ABANDONED_STALE_ATTEMPT = "stale_attempt"
ABANDONED_WRITE_FAILED = "write_failed"
ABANDONED_NO_RESULT = "no_result"


# --- identity -----------------------------------------------------------------


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def input_hash_for(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


@dataclass(frozen=True)
class StageContext:
    """The claimed job as the checkpoint layer needs it (built by the processor)."""

    lease: Lease
    meeting_id: str
    recording_sha256: str
    recording_size_bytes: int
    recording_duration_seconds: int
    language: str

    @property
    def workspace_id(self) -> str:
        return self.lease.workspace_id

    @property
    def job_id(self) -> str:
        return self.lease.job_id


@dataclass(frozen=True)
class StageIdentity:
    stage: StageName
    input_hash: str
    manifest: dict[str, Any]

    @property
    def schema_name(self) -> str:
        return ARTIFACT_SCHEMAS[self.stage][0].__name__

    @property
    def schema_version(self) -> int:
        return ARTIFACT_SCHEMAS[self.stage][1]


def stage_identity(
    context: StageContext,
    stage: StageName,
    *,
    provider: ProviderIdentity,
    request: Mapping[str, Any],
    upstream: Mapping[str, Any] | None = None,
) -> StageIdentity:
    """Deterministic identity of one stage run from its declared inputs only."""
    if stage not in ARTIFACT_SCHEMAS:
        raise ValueError(f"{stage.value} has no checkpoint contract")
    schema, schema_version = ARTIFACT_SCHEMAS[stage]
    manifest: dict[str, Any] = {
        "contract": CONTRACT_VERSION,
        "scope": {"workspace_id": context.workspace_id, "meeting_id": context.meeting_id},
        "stage": stage.value,
        "schema": schema.__name__,
        "schema_version": schema_version,
        "source": {
            "recording_sha256": context.recording_sha256,
            "recording_size_bytes": context.recording_size_bytes,
            "materialize_version": MATERIALIZE_VERSION,
        },
        "provider": provider.as_manifest(),
        "request": {key: request[key] for key in sorted(request)},
    }
    if upstream is not None:
        manifest["upstream"] = {key: upstream[key] for key in sorted(upstream)}
    return StageIdentity(stage=stage, input_hash=input_hash_for(manifest), manifest=manifest)


def artifact_token(attempt_id: str, stage: StageName) -> str:
    """Attempt-scoped object token: one object per attempt and stage."""
    return f"art-{attempt_id}-{stage.value}"


# --- artifacts ----------------------------------------------------------------


class ArtifactInvalid(Exception):
    """The artifact behind a checkpoint cannot be trusted (``reason`` is stable)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class ArtifactUnverified(Exception):
    """Storage did not answer; the object may or may not exist."""

    def __init__(self, code: str) -> None:
        super().__init__(f"storage unavailable ({code})")
        self.code = code


class CheckpointConflict(Exception):
    """Another published checkpoint holds this identity (unique index)."""


class StageCancelled(Exception):
    """The job was cancelled (meeting deleted) while the attempt ran."""


def serialize_result(result: ProviderSchema) -> bytes:
    return result.model_dump_json(exclude_none=False).encode("utf-8")


def read_bounded(reader: Any, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` (+1 to detect overflow) from a binary reader."""
    buffer = bytearray()
    limit = max_bytes + 1
    while len(buffer) < limit:
        chunk = reader.read(min(1024 * 1024, limit - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
    return bytes(buffer)


def parse_artifact(data: bytes, schema: type[ProviderSchema]) -> ProviderSchema:
    """Strict, bounded decoding: JSON object → validated schema, nothing else."""
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "artifact is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "artifact is not a JSON object")
    try:
        return schema.model_validate(decoded)
    except ValidationError as exc:
        raise ArtifactInvalid(
            INVALID_ARTIFACT_CORRUPT, f"artifact does not match {schema.__name__}"
        ) from exc


def load_artifact(
    storage: ObjectStorage,
    *,
    workspace_id: str,
    key: str,
    expected_sha256: str,
    expected_size: int,
    schema: type[ProviderSchema],
    max_bytes: int,
) -> ProviderSchema:
    """Fetch, bound, verify (size, SHA-256) and validate one artifact.

    The key must belong to ``workspace_id`` (the storage layer rejects any
    other tenant's key); ``ArtifactUnverified`` is raised when storage did
    not answer, which is *not* evidence that the object is missing.
    """
    if expected_size > max_bytes:
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "recorded size exceeds the artifact bound")
    try:
        reader = storage.open_reader(workspace_id, key)
    except StorageError as exc:
        if exc.code in {"object_not_found", "invalid_object_key"}:
            raise ArtifactInvalid(INVALID_ARTIFACT_MISSING, exc.code) from exc
        raise ArtifactUnverified(exc.code) from exc
    try:
        with reader:
            data = read_bounded(reader, max_bytes=max_bytes)
    except StorageError as exc:
        raise ArtifactUnverified(exc.code) from exc
    if len(data) > max_bytes:
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "artifact exceeds the size bound")
    if len(data) != expected_size:
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "artifact size differs from the record")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "artifact digest differs from the record")
    return parse_artifact(data, schema)


# --- checkpoint rows ----------------------------------------------------------


@dataclass(frozen=True)
class ArtifactIntent:
    """A PENDING checkpoint row: the declared, not yet written, artifact."""

    checkpoint_id: str
    stage: StageName
    object_token: str
    identity: StageIdentity


@dataclass(frozen=True)
class ReusedStage:
    checkpoint_id: str
    artifact_sha256: str
    result: ProviderSchema
    provider: str | None
    model: str | None
    is_mock: bool


def _reusable_row(db: Session, *, meeting_id: str, stage: StageName, input_hash: str):
    return db.scalar(
        select(StageCheckpoint).where(
            StageCheckpoint.meeting_id == meeting_id,
            StageCheckpoint.stage == stage.value,
            StageCheckpoint.input_hash == input_hash,
            StageCheckpoint.state == CheckpointState.PUBLISHED,
        )
    )


def invalidate_checkpoint(checkpoint: StageCheckpoint, *, reason: str, now: datetime) -> None:
    """Mark a checkpoint unusable (row the caller holds in a tenant-bound tx)."""
    if checkpoint.state == CheckpointState.PENDING:
        checkpoint.state = CheckpointState.ABANDONED
    elif checkpoint.state == CheckpointState.PUBLISHED:
        checkpoint.state = CheckpointState.INVALIDATED
    if checkpoint.invalidated_at is None:
        checkpoint.invalidated_at = now
        checkpoint.invalidation_reason = reason[:80]


def resume_stage(
    context: StageContext,
    identity: StageIdentity,
    *,
    storage: ObjectStorage,
    settings: Settings,
) -> ReusedStage | None:
    """Return the verified result of a published checkpoint for ``identity``, or None.

    A published row whose artifact is missing or corrupt is invalidated under
    the fence so it is never offered again.  Storage that does not answer is
    treated as "recompute now" (the row is *not* invalidated).
    """
    if not settings.job_checkpoint_reuse_enabled:
        return None
    schema, _version = ARTIFACT_SCHEMAS[identity.stage]
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=context.workspace_id)
        meeting = db.get(Meeting, context.meeting_id)
        recording = db.scalar(select(Recording).where(Recording.meeting_id == context.meeting_id))
        alive = (
            meeting is not None
            and meeting.deleted_at is None
            and recording is not None
            and recording.deleted_at is None
            and recording.checksum_sha256 == context.recording_sha256
        )
        row = (
            _reusable_row(
                db,
                meeting_id=context.meeting_id,
                stage=identity.stage,
                input_hash=identity.input_hash,
            )
            if alive
            else None
        )
        if row is None:
            db.rollback()
            return None
        if (
            row.workspace_id != context.workspace_id
            or row.artifact_sha256 is None
            or row.artifact_size_bytes is None
            or row.artifact_schema != identity.schema_name
            or row.schema_version != identity.schema_version
        ):
            db.rollback()
            return None
        checkpoint_id = row.id
        key = row.artifact_key
        sha256 = row.artifact_sha256
        size = row.artifact_size_bytes
        provider, model, is_mock = row.provider, row.model, row.is_mock
        db.rollback()

    try:
        result = load_artifact(
            storage,
            workspace_id=context.workspace_id,
            key=key,
            expected_sha256=sha256,
            expected_size=size,
            schema=schema,
            max_bytes=settings.job_artifact_max_bytes,
        )
    except ArtifactUnverified as exc:
        logger.warning(
            "STAGE_CHECKPOINT_UNVERIFIED checkpoint_id=%s stage=%s storage_error=%s",
            checkpoint_id,
            identity.stage.value,
            exc.code,
        )
        return None
    except ArtifactInvalid as exc:
        _invalidate_under_fence(context, checkpoint_id, reason=exc.reason)
        logger.warning(
            "STAGE_CHECKPOINT_INVALIDATED checkpoint_id=%s stage=%s reason=%s",
            checkpoint_id,
            identity.stage.value,
            exc.reason,
        )
        return None
    return ReusedStage(
        checkpoint_id=checkpoint_id,
        artifact_sha256=sha256,
        result=result,
        provider=provider,
        model=model,
        is_mock=is_mock,
    )


def _invalidate_under_fence(context: StageContext, checkpoint_id: str, *, reason: str) -> None:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=context.workspace_id)
        try:
            job, now = lock_owned_or_cancelled(db, context.lease)
        except StaleLeaseError:
            db.rollback()
            return
        row = db.get(StageCheckpoint, checkpoint_id)
        if row is not None and row.state == CheckpointState.PUBLISHED:
            invalidate_checkpoint(row, reason=reason, now=now)
            db.commit()
        else:
            db.rollback()


def begin_artifact(context: StageContext, identity: StageIdentity) -> ArtifactIntent:
    """Declare the artifact (PENDING row) under the fence before writing the object."""
    token = artifact_token(context.lease.attempt_id, identity.stage)
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=context.workspace_id)
        job, _now = lock_owned_or_cancelled(db, context.lease)
        if job is None:
            db.rollback()
            raise StageCancelled()
        row = StageCheckpoint(
            workspace_id=context.workspace_id,
            meeting_id=context.meeting_id,
            job_id=context.job_id,
            attempt=context.lease.attempt,
            attempt_id=context.lease.attempt_id,
            lease_generation=context.lease.generation,
            stage=identity.stage.value,
            state=CheckpointState.PENDING,
            contract_version=CONTRACT_VERSION,
            input_hash=identity.input_hash,
            input_manifest=identity.manifest,
            artifact_key=object_key_for(context.workspace_id, token),
            artifact_schema=identity.schema_name,
            schema_version=identity.schema_version,
        )
        db.add(row)
        db.commit()
        return ArtifactIntent(
            checkpoint_id=row.id, stage=identity.stage, object_token=token, identity=identity
        )


@dataclass(frozen=True)
class StoredArtifact:
    key: str
    sha256: str
    size_bytes: int


def store_artifact(
    storage: ObjectStorage,
    context: StageContext,
    intent: ArtifactIntent,
    result: ProviderSchema,
    *,
    settings: Settings,
) -> StoredArtifact:
    """Write the artifact under the intent's token and verify it by reading it back."""
    schema, _version = ARTIFACT_SCHEMAS[intent.stage]
    if not isinstance(result, schema):
        raise TypeError(f"{intent.stage.value} artifact must be a {schema.__name__}")
    data = serialize_result(result)
    if len(data) > settings.job_artifact_max_bytes:
        raise ArtifactInvalid(INVALID_ARTIFACT_CORRUPT, "artifact exceeds the size bound")
    stored = storage.put_stream(
        context.workspace_id,
        BytesIO(data),
        content_type=ARTIFACT_CONTENT_TYPE,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        max_bytes=settings.job_artifact_max_bytes,
        object_token=intent.object_token,
    )
    # Verify what storage will serve, not what we meant to write.
    load_artifact(
        storage,
        workspace_id=context.workspace_id,
        key=stored.key,
        expected_sha256=stored.sha256,
        expected_size=stored.size_bytes,
        schema=schema,
        max_bytes=settings.job_artifact_max_bytes,
    )
    return StoredArtifact(key=stored.key, sha256=stored.sha256, size_bytes=stored.size_bytes)


def publish_checkpoint(
    context: StageContext,
    intent: ArtifactIntent,
    stored: StoredArtifact,
    result: ProviderSchema,
    *,
    latency_ms: int | None,
    settings: Settings,
) -> str | None:
    """PENDING → PUBLISHED under the fence; records provider usage; returns the id.

    Returns ``None`` when the job was cancelled meanwhile (nothing published).
    Raises ``StaleLeaseError`` for a superseded attempt and
    ``CheckpointConflict`` when a published checkpoint already holds the
    identity (the caller retries and reuses it).
    """
    provider = getattr(result, "provider", None)
    model = getattr(result, "model", None)
    is_mock = bool(getattr(result, "is_mock", False))
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=context.workspace_id)
        job, now = lock_owned_or_cancelled(db, context.lease)
        if job is None:
            db.rollback()
            return None
        # Meeting row lock (WP07-T03): a tombstone committed by a concurrent
        # deletion or retention purge is seen here; nothing publishes after it.
        meeting = db.get(Meeting, context.meeting_id, with_for_update=True, populate_existing=True)
        if meeting is None or meeting.deleted_at is not None:
            db.rollback()
            return None
        row = db.get(StageCheckpoint, intent.checkpoint_id)
        if (
            row is None
            or row.state != CheckpointState.PENDING
            or row.attempt_id != context.lease.attempt_id
            or row.artifact_key != stored.key
        ):
            db.rollback()
            raise CheckpointConflict()
        # The current attempt's verified result is the authority for this
        # identity: an older published row (reuse disabled, or storage did
        # not answer during lookup) is superseded in the same transaction,
        # so the partial unique index keeps exactly one PUBLISHED row.
        previous = _reusable_row(
            db, meeting_id=context.meeting_id, stage=intent.stage, input_hash=row.input_hash
        )
        if previous is not None and previous.id != row.id:
            invalidate_checkpoint(previous, reason=INVALID_SUPERSEDED, now=now)
            db.flush()
        row.state = CheckpointState.PUBLISHED
        row.artifact_sha256 = stored.sha256
        row.artifact_size_bytes = stored.size_bytes
        row.provider = provider[:128] if provider else None
        row.model = model[:256] if model else None
        row.is_mock = is_mock
        row.published_at = now
        db.add(
            ProviderUsage(
                workspace_id=context.workspace_id,
                meeting_id=context.meeting_id,
                job_id=context.job_id,
                stage=intent.stage.value,
                provider=provider or "unknown",
                model=model or "default",
                audio_seconds=(
                    context.recording_duration_seconds
                    if intent.stage in {StageName.ASR, StageName.DIARIZATION}
                    else None
                ),
                latency_ms=latency_ms or 0,
                # Reported by the provider (WP08-T04); null = not reported.
                input_tokens=getattr(getattr(result, "usage", None), "input_tokens", None),
                output_tokens=getattr(getattr(result, "usage", None), "output_tokens", None),
                success=True,
            )
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise CheckpointConflict() from exc
        return row.id


def abandon_intent(context: StageContext, intent: ArtifactIntent, *, reason: str) -> None:
    """Mark our own PENDING row ABANDONED (best effort; cleanup covers the rest)."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=context.workspace_id)
        try:
            _job, now = lock_owned_or_cancelled(db, context.lease)
        except StaleLeaseError:
            db.rollback()
            return
        row = db.get(StageCheckpoint, intent.checkpoint_id)
        if row is not None and row.state == CheckpointState.PENDING:
            invalidate_checkpoint(row, reason=reason, now=now)
            db.commit()
        else:
            db.rollback()


# --- deletion and cleanup -----------------------------------------------------


def retire_meeting_checkpoints(db: Session, *, meeting_id: str, now: datetime) -> list[str]:
    """Invalidate every checkpoint of a deleted meeting; return the object keys."""
    keys: list[str] = []
    for row in db.scalars(
        select(StageCheckpoint).where(
            StageCheckpoint.meeting_id == meeting_id,
            StageCheckpoint.object_deleted_at.is_(None),
        )
    ):
        invalidate_checkpoint(row, reason=INVALID_MEETING_DELETED, now=now)
        keys.append(row.artifact_key)
    return keys


def delete_artifact_objects(
    storage: ObjectStorage, workspace_id: str, keys: list[str]
) -> list[str]:
    """Best-effort object deletion; returns the keys that are now absent."""
    removed: list[str] = []
    for key in keys:
        try:
            storage.delete(workspace_id, key)
        except StorageError as exc:
            logger.error(
                "STAGE_ARTIFACT_DELETE_FAILED workspace_id=%s error=%s", workspace_id, exc.code
            )
            continue
        removed.append(key)
    return removed


def mark_objects_deleted(db: Session, *, keys: list[str], now: datetime) -> int:
    count = 0
    for row in db.scalars(
        select(StageCheckpoint).where(
            StageCheckpoint.artifact_key.in_(keys),
            StageCheckpoint.object_deleted_at.is_(None),
        )
    ):
        row.object_deleted_at = now
        count += 1
    return count


def discover_workspaces(*, interval_seconds: int, now: datetime | None = None) -> list[str]:
    """Every workspace to sweep, in a rotating order (read-only service scope; ids only).

    A fixed ``LIMIT`` used to cut the list, so a workspace whose id sorted
    after the first batch was never swept (HOM-210).  Every run now gets every
    workspace; callers still stop when their item budget is spent.  The start
    moves by one per sweep interval of database time, so a budget used up
    early does not leave the same workspaces at the end of every run.
    """
    from backend.database.saas_models import Entitlement

    with get_session_factory()() as db:
        bind_service_scope(db, SERVICE_SCOPE_USAGE_RECONCILIATION)
        ids = list(
            db.scalars(
                select(Entitlement.workspace_id).distinct().order_by(Entitlement.workspace_id.asc())
            ).all()
        )
        current = now or database_now(db)
        db.rollback()
    return rotate(ids, run=int(current.timestamp() // interval_seconds))


def rotate(ids: list[str], *, run: int) -> list[str]:
    """``ids`` starting at position ``run`` (mod len), keeping every id once."""
    if not ids:
        return []
    start = run % len(ids)
    return ids[start:] + ids[:start]


def _meeting_held(db: Session, meeting_id: str) -> bool:
    return (
        db.scalar(
            select(MediaObject.id)
            .where(
                MediaObject.meeting_id == meeting_id,
                MediaObject.lifecycle_state == MediaLifecycleState.LEGAL_HOLD,
            )
            .limit(1)
        )
        is not None
    )


def _cleanup_candidates(db: Session, *, workspace_id: str, cutoff: datetime, limit: int):
    """Rows whose object may be removed: never published or invalidated, past
    the grace period, and not owned by an attempt that still holds the lease;
    plus published rows of deleted meetings (customer deletion backstop)."""
    rows = db.scalars(
        select(StageCheckpoint)
        .where(
            StageCheckpoint.workspace_id == workspace_id,
            StageCheckpoint.object_deleted_at.is_(None),
            StageCheckpoint.updated_at <= cutoff,
        )
        .order_by(StageCheckpoint.updated_at.asc())
        .limit(limit)
    ).all()
    eligible = []
    for row in rows:
        meeting = db.get(Meeting, row.meeting_id)
        meeting_deleted = meeting is None or meeting.deleted_at is not None
        if row.state == CheckpointState.PUBLISHED and not meeting_deleted:
            continue  # the authoritative result of a live meeting: never touched
        if meeting_deleted and _meeting_held(db, row.meeting_id):
            continue  # WP07-T03: a legal hold keeps the derived stage outputs too
        if row.state == CheckpointState.PENDING:
            job = db.get(ProcessingJob, row.job_id)
            if (
                job is not None
                and job.state == JobState.RUNNING
                and job.lease_generation == row.lease_generation
            ):
                continue  # the attempt may still be writing this object
        eligible.append((row, meeting_deleted))
    return eligible


def sweep_abandoned_artifacts(
    settings: Settings | None = None,
    *,
    storage: ObjectStorage | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Remove objects of abandoned/invalidated checkpoints (bounded, conservative).

    Returns counts: ``scanned`` rows examined, ``deleted`` objects removed,
    ``absent`` rows whose object was already gone, ``unavailable`` rows left
    untouched because storage did not answer.
    """
    settings = settings or get_settings()
    storage = storage or create_object_storage(settings)
    batch = limit or settings.job_artifact_sweep_batch_size
    grace = timedelta(seconds=settings.job_artifact_grace_seconds)
    counts = {"workspaces": 0, "scanned": 0, "deleted": 0, "absent": 0, "unavailable": 0}
    remaining = batch
    for workspace_id in discover_workspaces(
        interval_seconds=settings.job_artifact_sweep_interval_seconds, now=now
    ):
        if remaining <= 0:
            break
        counts["workspaces"] += 1
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            current = now or database_now(db)
            candidates = _cleanup_candidates(
                db, workspace_id=workspace_id, cutoff=current - grace, limit=remaining
            )
            for row, meeting_deleted in candidates:
                counts["scanned"] += 1
                remaining -= 1
                try:
                    removed = storage.delete(workspace_id, row.artifact_key)
                except StorageError as exc:
                    counts["unavailable"] += 1
                    logger.warning(
                        "STAGE_ARTIFACT_CLEANUP_UNAVAILABLE checkpoint_id=%s error=%s",
                        row.id,
                        exc.code,
                    )
                    continue
                counts["deleted" if removed else "absent"] += 1
                if row.state == CheckpointState.PENDING:
                    invalidate_checkpoint(row, reason=ABANDONED_NOT_PUBLISHED, now=current)
                elif row.state == CheckpointState.PUBLISHED and meeting_deleted:
                    invalidate_checkpoint(row, reason=INVALID_MEETING_DELETED, now=current)
                row.object_deleted_at = current
            db.commit()
    if counts["deleted"] or counts["unavailable"]:
        logger.info("STAGE_ARTIFACT_CLEANUP %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database

    configure_database()
    print(json.dumps(sweep_abandoned_artifacts(), ensure_ascii=False))
    sys.exit(0)
