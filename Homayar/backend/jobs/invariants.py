"""Invariants of accepted processing jobs (WP06-T07).

One reusable checker for what the durable-processing design promises about
*every* accepted job, whatever faults happened on the way (worker kill,
broker outage, lost acknowledgement, duplicate delivery, storage fault):

======================================  ========================================
``running_lease``                       RUNNING carries an owner and a lease that
                                        is not older than two recovery sweeps
``terminal_released``                   a terminal job holds no lease and has a
                                        completion time
``queued_delivery_path``                a QUEUED job has an outbox intent that can
                                        still deliver it (PENDING for its current
                                        generation, or PUBLISHED)
``reservations_match_state``            open jobs hold both reservations;
                                        COMPLETED settled the audio reservation
                                        and released the slot; failed / parked /
                                        cancelled jobs hold nothing
``single_settlement``                   at most one COMMITTED reservation per
                                        meeting and metric, at most one
                                        ``committed`` ledger row per reservation
``ledger_counters``                     entitlement counters equal the sums the
                                        reservation rows imply
``meeting_status``                      the meeting's status agrees with its
                                        latest job
``checkpoint_state``                    a PUBLISHED checkpoint carries its
                                        verified digest, was published by a
                                        generation the job has seen and belongs
                                        to a live meeting; one PUBLISHED row per
                                        identity; stage runs point at existing
                                        checkpoints of their own meeting
``artifact_missing``                    (with storage) the object behind a
                                        PUBLISHED checkpoint of a live meeting
                                        exists
======================================  ========================================

Artifact evidence is reported separately: ``checked`` objects confirmed
present, ``missing`` (a violation), ``unverified`` (storage did not answer —
never taken as proof of absence), ``orphans`` (objects of never-published
or invalidated rows still awaiting cleanup) and ``retired`` (objects already
removed on purpose).  Without a storage handle no object is inspected.

Used by the fault-injection tests and as an operator check
(``python -m backend.jobs.invariants``): discovery of workspaces runs under
the read-only ``usage_reconciliation`` scope (entitlements only), each
workspace is then inspected in its own tenant-bound, read-only transaction.
Nothing is repaired here; violations point at the runbook.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    CheckpointState,
    DispatchState,
    Entitlement,
    JobDispatch,
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    ProcessingStageRun,
    ReservationState,
    StageCheckpoint,
    UsageEvent,
    UsageReservation,
)
from backend.database.tenant_context import (
    bind_tenant_context,
)
from backend.jobs.checkpoints import discover_workspaces
from backend.jobs.failures import TERMINAL_STATES
from backend.jobs.leases import as_utc, database_now
from backend.jobs.usage_reconciliation import expected_counters
from backend.services.logging_service import logger
from backend.storage import ObjectStorage, StorageError

OPEN_STATES: frozenset[JobState] = frozenset({JobState.QUEUED, JobState.RUNNING})
FAILED_STATES: frozenset[JobState] = frozenset(
    {JobState.FAILED, JobState.DEAD_LETTER, JobState.CANCELLED}
)


@dataclass(frozen=True)
class Violation:
    workspace_id: str
    rule: str
    detail: str
    job_id: str | None = None
    meeting_id: str | None = None


@dataclass
class ArtifactEvidence:
    """What was actually observed about artifact objects (never inferred)."""

    checked: int = 0
    missing: int = 0
    unverified: int = 0
    orphans: int = 0
    retired: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _reservation_states(db: Session, job: ProcessingJob) -> dict[str, ReservationState | None]:
    states: dict[str, ReservationState | None] = {}
    for label, reservation_id in (
        ("audio", job.reservation_id),
        ("concurrency", job.concurrency_reservation_id),
    ):
        if not reservation_id:
            states[label] = None
            continue
        reservation = db.get(UsageReservation, reservation_id)
        states[label] = reservation.state if reservation is not None else None
    return states


def check_workspace(
    db: Session,
    workspace_id: str,
    *,
    settings: Settings,
    now: datetime | None = None,
    storage: ObjectStorage | None = None,
    evidence: ArtifactEvidence | None = None,
) -> list[Violation]:
    """Read-only inspection of every job, reservation, entitlement and checkpoint.

    Objects are inspected only when ``storage`` is given; ``evidence`` (when
    passed) accumulates what was observed.
    """
    now = now or database_now(db)
    violations: list[Violation] = []
    evidence = evidence if evidence is not None else ArtifactEvidence()

    def flag(rule: str, detail: str, job: ProcessingJob | None = None) -> None:
        violations.append(
            Violation(
                workspace_id=workspace_id,
                rule=rule,
                detail=detail,
                job_id=job.id if job is not None else None,
                meeting_id=job.meeting_id if job is not None else None,
            )
        )

    stale_after = timedelta(seconds=settings.job_recovery_sweep_interval_seconds * 2)
    jobs = db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.workspace_id == workspace_id)
        .order_by(ProcessingJob.created_at.asc(), ProcessingJob.id.asc())
    ).all()
    latest_by_meeting: dict[str, ProcessingJob] = {}
    for job in jobs:
        latest_by_meeting[job.meeting_id] = job  # ordered by creation: last wins

        if job.state == JobState.RUNNING:
            expires = as_utc(job.lease_expires_at)
            if not job.locked_by or expires is None:
                flag("running_lease", "running without owner or lease", job)
            elif expires + stale_after < now:
                flag(
                    "running_lease",
                    f"lease expired at {expires.isoformat()} and was not recovered",
                    job,
                )
        elif job.state in TERMINAL_STATES:
            if job.locked_by or job.lease_expires_at is not None or job.attempt_id:
                flag("terminal_released", "terminal job still carries a lease", job)
            if job.completed_at is None:
                flag("terminal_released", "terminal job has no completion time", job)

        if job.state == JobState.QUEUED:
            dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job.id))
            if dispatch is None:
                flag("queued_delivery_path", "queued job has no outbox intent", job)
            elif dispatch.state == DispatchState.PENDING:
                if dispatch.generation != job.lease_generation:
                    flag(
                        "queued_delivery_path",
                        f"pending intent armed for generation {dispatch.generation}, "
                        f"job is at {job.lease_generation}",
                        job,
                    )
            elif dispatch.state != DispatchState.PUBLISHED:
                flag(
                    "queued_delivery_path",
                    f"queued job with a {dispatch.state.value} intent cannot be delivered",
                    job,
                )

        states = _reservation_states(db, job)
        if job.state in OPEN_STATES:
            if states["audio"] != ReservationState.RESERVED:
                flag("reservations_match_state", "open job without a reserved allowance", job)
            if (
                job.concurrency_reservation_id
                and states["concurrency"] != ReservationState.RESERVED
            ):
                flag("reservations_match_state", "open job without a reserved slot", job)
        elif job.state == JobState.COMPLETED:
            if states["audio"] != ReservationState.COMMITTED:
                flag("reservations_match_state", "completed job did not settle its allowance", job)
            if states["concurrency"] == ReservationState.RESERVED:
                flag("reservations_match_state", "completed job still holds its slot", job)
        elif job.state in FAILED_STATES:
            for label, state in states.items():
                if state == ReservationState.RESERVED:
                    flag(
                        "reservations_match_state",
                        f"{job.state.value} job still holds its {label} reservation",
                        job,
                    )

    # single settlement per meeting/metric and per reservation
    duplicate_meetings = db.execute(
        select(UsageReservation.meeting_id, UsageReservation.entitlement_id, func.count())
        .where(
            UsageReservation.workspace_id == workspace_id,
            UsageReservation.state == ReservationState.COMMITTED,
        )
        .group_by(UsageReservation.meeting_id, UsageReservation.entitlement_id)
        .having(func.count() > 1)
    ).all()
    for meeting_id, entitlement_id, count in duplicate_meetings:
        violations.append(
            Violation(
                workspace_id=workspace_id,
                rule="single_settlement",
                detail=f"{count} committed reservations for entitlement {entitlement_id}",
                meeting_id=meeting_id,
            )
        )
    duplicate_events = db.execute(
        select(UsageEvent.reservation_id, func.count())
        .where(
            UsageEvent.workspace_id == workspace_id,
            UsageEvent.event_type == "committed",
            UsageEvent.reservation_id.is_not(None),
        )
        .group_by(UsageEvent.reservation_id)
        .having(func.count() > 1)
    ).all()
    for reservation_id, count in duplicate_events:
        violations.append(
            Violation(
                workspace_id=workspace_id,
                rule="single_settlement",
                detail=f"{count} committed ledger rows for reservation {reservation_id}",
            )
        )

    # ledger versus counters
    for entitlement in db.scalars(
        select(Entitlement).where(Entitlement.workspace_id == workspace_id)
    ):
        expected = expected_counters(db, entitlement.id)
        observed = (entitlement.used_value, entitlement.reserved_value)
        if expected != observed:
            violations.append(
                Violation(
                    workspace_id=workspace_id,
                    rule="ledger_counters",
                    detail=(
                        f"{entitlement.metric}: counters used/reserved {observed} but the "
                        f"reservation rows imply {expected}"
                    ),
                )
            )

    # stage checkpoints and their artifacts (WP06-T03)
    jobs_by_id = {job.id: job for job in jobs}
    checkpoints = db.scalars(
        select(StageCheckpoint)
        .where(StageCheckpoint.workspace_id == workspace_id)
        .order_by(StageCheckpoint.created_at.asc(), StageCheckpoint.id.asc())
    ).all()
    live_identities: dict[tuple[str, str, str], int] = {}
    for checkpoint in checkpoints:
        job = jobs_by_id.get(checkpoint.job_id)
        meeting = db.get(Meeting, checkpoint.meeting_id)
        meeting_alive = meeting is not None and meeting.deleted_at is None
        if checkpoint.state == CheckpointState.PUBLISHED:
            identity = (checkpoint.meeting_id, checkpoint.stage, checkpoint.input_hash)
            live_identities[identity] = live_identities.get(identity, 0) + 1
            if (
                checkpoint.artifact_sha256 is None
                or checkpoint.artifact_size_bytes is None
                or checkpoint.published_at is None
                or checkpoint.object_deleted_at is not None
            ):
                flag("checkpoint_state", "published checkpoint without verified artifact", job)
            if job is not None and checkpoint.lease_generation > job.lease_generation:
                flag(
                    "checkpoint_state",
                    f"checkpoint published by generation {checkpoint.lease_generation}, "
                    f"job is at {job.lease_generation}",
                    job,
                )
            if not meeting_alive:
                flag("checkpoint_state", "deleted meeting still has a published checkpoint", job)
            elif storage is not None:
                try:
                    present = storage.exists(workspace_id, checkpoint.artifact_key)
                except StorageError:
                    evidence.unverified += 1
                else:
                    if present:
                        evidence.checked += 1
                    else:
                        evidence.missing += 1
                        flag(
                            "artifact_missing",
                            f"{checkpoint.stage} artifact of checkpoint {checkpoint.id} is gone",
                            job,
                        )
        elif checkpoint.object_deleted_at is not None:
            evidence.retired += 1
        elif storage is not None:
            try:
                if storage.exists(workspace_id, checkpoint.artifact_key):
                    evidence.orphans += 1
            except StorageError:
                evidence.unverified += 1
    for (meeting_id, stage, _hash), count in live_identities.items():
        if count > 1:
            violations.append(
                Violation(
                    workspace_id=workspace_id,
                    rule="checkpoint_state",
                    detail=f"{count} published {stage} checkpoints for one identity",
                    meeting_id=meeting_id,
                )
            )
    checkpoints_by_id = {checkpoint.id: checkpoint for checkpoint in checkpoints}
    for run in db.scalars(
        select(ProcessingStageRun).where(
            ProcessingStageRun.workspace_id == workspace_id,
            ProcessingStageRun.checkpoint_id.is_not(None),
        )
    ):
        checkpoint = checkpoints_by_id.get(run.checkpoint_id or "")
        if checkpoint is None or checkpoint.meeting_id != run.meeting_id:
            flag(
                "checkpoint_state",
                f"stage run {run.stage} references a checkpoint of another meeting or none",
                jobs_by_id.get(run.job_id),
            )
        elif run.reused and checkpoint.job_id == run.job_id and checkpoint.attempt == run.attempt:
            flag(
                "checkpoint_state",
                f"stage run {run.stage} claims to reuse the checkpoint it produced",
                jobs_by_id.get(run.job_id),
            )

    # meeting status agrees with its latest job
    for meeting_id, job in latest_by_meeting.items():
        meeting = db.get(Meeting, meeting_id)
        if meeting is None:
            flag("meeting_status", "job without a meeting", job)
            continue
        if meeting.deleted_at is not None:
            if job.state in OPEN_STATES:
                flag("meeting_status", "deleted meeting still has an open job", job)
            continue
        expected_statuses: set[MeetingStatus]
        if job.state == JobState.COMPLETED:
            expected_statuses = {MeetingStatus.COMPLETED, MeetingStatus.PARTIAL}
        elif job.state in {JobState.FAILED, JobState.DEAD_LETTER}:
            expected_statuses = {MeetingStatus.FAILED}
        elif job.state == JobState.CANCELLED:
            expected_statuses = {MeetingStatus.CANCELLED}
        else:
            expected_statuses = {
                MeetingStatus.QUEUED,
                MeetingStatus.PROCESSING,
                MeetingStatus.TRANSCRIBING,
                MeetingStatus.DIARIZING,
                MeetingStatus.ANALYZING,
            }
        if meeting.status not in expected_statuses:
            flag(
                "meeting_status",
                f"meeting is {meeting.status.value} while its latest job is {job.state.value}",
                job,
            )
    return violations


def check_all(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    storage: ObjectStorage | None = None,
) -> dict:
    """Inspect every workspace (or the first ``limit``). Returns counts and violations.

    Pass ``storage`` to also confirm the objects behind published checkpoints;
    the ``artifacts`` block then says what was observed.
    """
    settings = settings or get_settings()
    # Every workspace (HOM-210); an explicit ``limit`` inspects fewer and the
    # summary says how many of how many were looked at.
    known = discover_workspaces(interval_seconds=settings.usage_reconciliation_interval_seconds)
    workspaces = known[:limit] if limit else known
    violations: list[Violation] = []
    evidence = ArtifactEvidence()
    for workspace_id in workspaces:
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            violations.extend(
                check_workspace(
                    db, workspace_id, settings=settings, storage=storage, evidence=evidence
                )
            )
            db.rollback()
    summary = {
        "workspaces": len(workspaces),
        "workspaces_known": len(known),
        "violations": len(violations),
        "by_rule": {},
        "artifacts": ({**evidence.as_dict(), "inspected": storage is not None}),
        "items": [asdict(v) for v in violations],
    }
    for violation in violations:
        summary["by_rule"][violation.rule] = summary["by_rule"].get(violation.rule, 0) + 1
    if violations:
        logger.error(
            "JOB_INVARIANTS_VIOLATED %s", {k: v for k, v in summary.items() if k != "items"}
        )
    return summary


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    from backend.database.relational import configure_database
    from backend.storage import create_object_storage

    configure_database()
    active = get_settings()
    inspect_objects = "--artifacts" in sys.argv[1:]
    result = check_all(active, storage=create_object_storage(active) if inspect_objects else None)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1 if result["violations"] else 0)
