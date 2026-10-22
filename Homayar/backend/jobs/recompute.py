"""Recompute a meeting's analysis after a transcript correction (HOM-63, ADR 0037).

A correction appends a new current revision; the analysis that read the old
one is then stale (derived in ``quality_service``, never stored).  A recompute
reruns only what depends on the transcript text: the derived text view of the
target revision, the intelligence pass over its segments (under the same
eligibility, fallback and redaction rules as the full job), the evidence plan,
a new analysis run, the summary and the AI action items.  It never fetches
media, reruns ASR or diarization, writes a transcript revision, a stage
checkpoint, a processing job or usage, and never changes ``Meeting.status``:
the full job (``process_job``) always writes an ASR revision, which would
replace the person's correction.

The provider call runs without a row lock.  The writes then take the meeting
row lock and the lock ``correct_revision`` takes on the current revision row,
and check again: a target that is no longer current is ``superseded`` (only
the audit event is written); a current run that already read the target is
``up_to_date`` (nothing is written).  A failure appends a failed, non-current
run and keeps the current outputs.  The sweeper dispatches stale meetings
again until a revision has ``recompute_max_attempts`` failed runs; after that
only a person can ask (the manual route).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from backend.celery_app import PROCESSING_QUEUE
from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AnalysisRun,
    Meeting,
    StageState,
    TranscriptRevision,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.checkpoints import discover_workspaces
from backend.jobs.leases import LeasePolicy, database_now
from backend.jobs.processor import (
    ProcessingError,
    _ensure_policy_current,
    _health_session,
    _policy_fingerprint,
    _workspace_pii_classes,
    analysis_candidates,
    call_analysis,
    derive_revision_views,
    persist_analysis,
)
from backend.jobs.states import StageReason
from backend.observability.correlation import MAX_CORRELATION_ID_LENGTH, correlation_scope
from backend.privacy.redaction_service import policy_digest
from backend.privacy.text_pii import parse_classes
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle, create_provider_bundle
from backend.providers.registry import ResolvedManifest, resolve_bundle, resolve_fallbacks
from backend.providers.resilience import health_rank
from backend.providers.routing import analysis_text_classes
from backend.providers.routing import select as select_candidate
from backend.providers.schemas import IntelligenceResult, TranscriptTurn
from backend.services.ai_policy_service import EffectivePolicy, current_policy
from backend.services.analysis_extraction import extraction_audit_details
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.logging_service import logger
from backend.services.model_registry_service import register_resolved
from backend.services.quality_service import failed_recomputes
from backend.services.transcript_service import (
    current_analysis_run,
    current_revision,
    record_analysis_run,
    revision_by_id,
    segments_of,
)

# Outcomes of one recompute.
COMPLETED = "completed"
UP_TO_DATE = "up_to_date"
SUPERSEDED = "superseded"
NO_PRIOR_ANALYSIS = "no_prior_analysis"
FAILED = "failed"

# What asked for a recompute (the ``trigger`` of ``meeting.recompute.requested``).
TRIGGER_CORRECTION = "correction"
TRIGGER_MANUAL = "manual"

# A failure that is neither a provider nor a classified processing error.
RECOMPUTE_FAILED = "recompute_failed"
# Recomputes one sweep dispatches at most (all workspaces together).
SWEEP_BATCH_SIZE = 100


@dataclass(frozen=True)
class RecomputeWork:
    """One recompute: the revision it reads and the policy it runs under.

    Satisfies ``processor.ProviderWork``, so provider calls share the full
    job's circuit, request window, egress permit and policy check.
    """

    workspace_id: str
    meeting_id: str
    revision_id: str
    previous_run_id: str
    language: str
    # The workspace's text-redaction classes (reader view); an analysis
    # endpoint may still get stricter ones (``analysis_text_classes``).
    pii_classes: tuple[str, ...]
    ai_policy: EffectivePolicy
    policy_fingerprint: str
    turns: tuple[TranscriptTurn, ...]
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class _Analysis:
    intelligence: IntelligenceResult | None
    state: StageState
    reason_code: str | None
    pii_classes: tuple[str, ...]
    identity_params: dict[str, Any]
    manifest_digest: str | None
    # The turns the answering model received; empty without an analysis (HOM-53).
    model_turns: tuple[TranscriptTurn, ...] = ()


class _RecomputeFailed(Exception):
    def __init__(self, code: str, pii_classes: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.pii_classes = pii_classes


def recompute_correlation_id(meeting_id: str, delivery: str | None = None) -> str:
    """``recompute:<meeting id>:<delivery>``; ``recompute:<meeting id>`` is the
    prefix an operator searches (HOM-113, like ``job:<id>``)."""
    run = (delivery or uuid.uuid4().hex)[:8]
    return f"recompute:{meeting_id}:{run}"[:MAX_CORRELATION_ID_LENGTH]


# --- requests and dispatch ---------------------------------------------------------


def record_recompute_request(
    db: Session,
    *,
    meeting: Meeting,
    revision_id: str,
    trigger: str,
    actor_user_id: str | None,
    request_id: str | None,
    settings: Settings,
) -> bool:
    """Audit a person's request in the caller's transaction.

    Returns False (and records nothing) when the meeting has no analysis run:
    there is nothing to recompute.
    """
    run = current_analysis_run(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    if run is None:
        return False
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_RECOMPUTE_REQUESTED,
        actor_user_id=actor_user_id,
        workspace_id=meeting.workspace_id,
        target_type="meeting",
        target_id=meeting.id,
        request_id=request_id,
        details={"trigger": trigger, "revision_id": revision_id, "analysis_run_id": run.id},
        settings=settings,
    )
    return True


def dispatch_recompute(
    meeting_id: str, target_revision_id: str, *, workspace_id: str, settings: Settings
) -> bool:
    """Hand one recompute to the processing queue; never raises.

    With ``tasks_eager`` (development, tests; the Celery app's
    ``task_always_eager`` follows the same setting) it runs here, with these
    settings.  A lost dispatch is not lost work: the sweeper finds the stale
    meeting again.
    """
    try:
        if settings.tasks_eager:
            run_recompute(
                meeting_id, target_revision_id, workspace_id=workspace_id, settings=settings
            )
        else:
            from backend.jobs.tasks import recompute_meeting_analysis_task

            recompute_meeting_analysis_task.apply_async(
                args=[meeting_id, target_revision_id],
                kwargs={"workspace_id": workspace_id},
                queue=PROCESSING_QUEUE,
            )
    except Exception as exc:
        logger.error(
            "ANALYSIS_RECOMPUTE_DISPATCH_FAILED meeting_id=%s error_type=%s",
            meeting_id,
            type(exc).__name__,
        )
        return False
    return True


def run_recompute(
    meeting_id: str,
    target_revision_id: str,
    *,
    workspace_id: str | None = None,
    settings: Settings | None = None,
    delivery: str | None = None,
) -> str:
    """The task body: one recompute under its own correlation id."""
    with correlation_scope(recompute_correlation_id(meeting_id, delivery)):
        return recompute_meeting_analysis(
            meeting_id, target_revision_id, workspace_id=workspace_id, settings=settings
        )


# --- the recompute -----------------------------------------------------------------


def recompute_meeting_analysis(
    meeting_id: str,
    target_revision_id: str,
    *,
    workspace_id: str | None = None,
    settings: Settings | None = None,
    providers: ProviderBundle | None = None,
) -> str:
    """Recompute the analysis outputs of one meeting from one revision.

    Returns ``completed``, ``up_to_date``, ``superseded``,
    ``no_prior_analysis`` or ``failed``.  A meeting or revision that does not
    exist (or a deleted meeting) is ``superseded``: there is no current target.
    """
    settings = settings or get_settings()
    workspace_id = workspace_id or _workspace_of(meeting_id, settings)
    if workspace_id is None:
        return SUPERSEDED
    loaded = _load(meeting_id, target_revision_id, workspace_id=workspace_id, settings=settings)
    if isinstance(loaded, str):
        return loaded
    try:
        analysis = _analyze(loaded, settings=settings, providers=providers)
        return _persist(loaded, analysis, settings=settings)
    except _RecomputeFailed as failure:
        code, classes = failure.code, failure.pii_classes
    except Exception as exc:
        code, classes = _failure_code(exc, meeting_id=meeting_id), ()
    logger.warning("ANALYSIS_RECOMPUTE_FAILED meeting_id=%s code=%s", meeting_id, code)
    return _persist_failure(loaded, code=code, pii_classes=classes, settings=settings)


def _workspace_of(meeting_id: str, settings: Settings) -> str | None:
    """The workspace of a meeting when the caller did not name it (a direct
    operator call): every workspace is bound in turn and only an id is read."""
    for workspace_id in discover_workspaces(
        interval_seconds=settings.recompute_sweep_interval_seconds
    ):
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            found = db.scalar(
                select(Meeting.id).where(
                    Meeting.id == meeting_id, Meeting.workspace_id == workspace_id
                )
            )
            db.rollback()
        if found is not None:
            return workspace_id
    return None


def _load(
    meeting_id: str, revision_id: str, *, workspace_id: str, settings: Settings
) -> RecomputeWork | str:
    """Read what the recompute needs (no lock), or the outcome when there is
    nothing to do."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        meeting = db.scalar(
            select(Meeting).where(
                Meeting.id == meeting_id,
                Meeting.workspace_id == workspace_id,
                Meeting.deleted_at.is_(None),
            )
        )
        workspace = db.get(Workspace, workspace_id)
        target = (
            revision_by_id(
                db, workspace_id=workspace_id, meeting_id=meeting_id, revision_id=revision_id
            )
            if meeting is not None
            else None
        )
        if meeting is None or workspace is None or target is None:
            return SUPERSEDED
        run = current_analysis_run(db, workspace_id=workspace_id, meeting_id=meeting_id)
        if run is None:
            return NO_PRIOR_ANALYSIS
        if not target.is_current:
            _record_superseded(db, meeting=meeting, revision_id=revision_id, settings=settings)
            db.commit()
            return SUPERSEDED
        if run.transcript_revision_id == target.id:
            return UP_TO_DATE
        ai_policy = current_policy(db, workspace_id=workspace_id)
        pii_classes = _workspace_pii_classes(workspace)
        turns = tuple(
            TranscriptTurn(
                text=segment.text,
                speaker_label=segment.speaker_key,
                start_seconds=segment.start_ms / 1000,
                end_seconds=segment.end_ms / 1000,
            )
            for segment in segments_of(db, target)
        )
        return RecomputeWork(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            revision_id=target.id,
            previous_run_id=run.id,
            language=target.language or meeting.language,
            pii_classes=pii_classes,
            ai_policy=ai_policy,
            policy_fingerprint=_policy_fingerprint(ai_policy.digest, pii_classes),
            turns=turns,
        )


def _analyze(
    work: RecomputeWork, *, settings: Settings, providers: ProviderBundle | None
) -> _Analysis:
    """The intelligence pass over the target revision (no lock held).

    Preflight, eligibility, ranking, redaction and fallback are the full
    job's (``process_job``); only the checkpoints are left out: the
    intelligence checkpoint identity does not name the revision it read.
    """
    if not work.turns:
        return _Analysis(None, StageState.SKIPPED, StageReason.NO_TRANSCRIPT.value, (), {}, None)
    first_classes: tuple[str, ...] = ()
    try:
        active = providers or create_provider_bundle(settings)
        manifests = resolve_bundle(active, settings)
        register_resolved(manifests.values())
        fallback_manifests = resolve_fallbacks(active, settings).get("intelligence", [])
        register_resolved(fallback_manifests)
        adapters = [
            active.intelligence,
            *(active.fallbacks.get("intelligence", ()) if fallback_manifests else ()),
        ]
        ai_policy = work.ai_policy.policy

        def classes_for(resolved: ResolvedManifest) -> tuple[str, ...]:
            return analysis_text_classes(
                work.pii_classes, resolved.placement, ai_policy.intelligence
            )

        with _health_session(work) as health_db:
            selection = select_candidate(
                "intelligence",
                [manifests["intelligence"], *fallback_manifests],
                ai_policy.stage("intelligence"),
                language=work.language,
                text_redacted=lambda resolved: bool(classes_for(resolved)),
                rank=health_rank(health_db, now=database_now(health_db)),
                fallbacks_from=1,
            )
        if not selection.decision.allowed:
            # Refused by policy: no analysis and no substitute, as in the full job.
            return _Analysis(None, StageState.UNAVAILABLE, selection.decision.reason, (), {}, None)
        candidates = analysis_candidates(
            selection,
            classes_for,
            text=" ".join(turn.text for turn in work.turns),
            turns=list(work.turns),
            language=work.language,
            call_id=work.meeting_id,
        )
        first_classes = candidates[0].pii_classes
        candidate, result, failed = call_analysis(
            work,
            candidates,
            selection=selection,
            adapters=adapters,
            heartbeat=None,
            policy=LeasePolicy.from_settings(settings),
            settings=settings,
            before_fallback=lambda: _ensure_policy_current(work),
        )
    except (ProviderError, ProcessingError) as exc:
        raise _RecomputeFailed(exc.code, first_classes) from exc
    return _Analysis(
        intelligence=result,
        state=StageState.COMPLETED,
        reason_code=StageReason.FALLBACK_USED.value if failed else None,
        pii_classes=candidate.pii_classes,
        identity_params=dict(adapters[candidate.index].identity().params),
        manifest_digest=selection.candidates[candidate.index].digest,
        model_turns=tuple(candidate.request.turns),
    )


def _lock_target(
    db: Session, work: RecomputeWork, *, settings: Settings
) -> tuple[Meeting, TranscriptRevision] | str:
    """Take the meeting row lock and the current-revision lock (the one
    ``correct_revision`` takes), then check the target again.

    The meeting lock is ``FOR NO KEY UPDATE``: it still waits for a tombstone,
    but it does not block the ``FOR KEY SHARE`` lock that a correction's new
    revision row takes on the meeting (its foreign key) while the correction
    holds the revision lock.  ``FOR UPDATE`` here deadlocked with it.
    """
    meeting = db.get(
        Meeting,
        work.meeting_id,
        with_for_update={"key_share": True},
        populate_existing=True,
    )
    if meeting is None or meeting.deleted_at is not None:
        return SUPERSEDED
    current = current_revision(
        db, workspace_id=work.workspace_id, meeting_id=work.meeting_id, for_update=True
    )
    if current is None or current.id != work.revision_id:
        _record_superseded(db, meeting=meeting, revision_id=work.revision_id, settings=settings)
        db.commit()
        return SUPERSEDED
    run = current_analysis_run(db, workspace_id=work.workspace_id, meeting_id=work.meeting_id)
    if run is not None and run.transcript_revision_id == current.id:
        return UP_TO_DATE
    return meeting, current


def _persist(work: RecomputeWork, analysis: _Analysis, *, settings: Settings) -> str:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=work.workspace_id)
        now = database_now(db)
        locked = _lock_target(db, work, settings=settings)
        if isinstance(locked, str):
            db.rollback()
            return locked
        meeting, revision = locked
        previous = current_analysis_run(
            db, workspace_id=work.workspace_id, meeting_id=work.meeting_id
        )
        segments = segments_of(db, revision)
        derive_revision_views(
            db,
            meeting=meeting,
            revision=revision,
            segments=segments,
            analysis_classes=analysis.pii_classes,
            view_classes=work.pii_classes,
            settings=settings,
            now=now,
        )
        persisted = persist_analysis(
            db,
            meeting=meeting,
            revision=revision,
            segments=segments,
            intelligence=analysis.intelligence,
            model_turns=analysis.model_turns,
            state=analysis.state,
            reason_code=analysis.reason_code,
            now=now,
            provenance={
                "identity_params": analysis.identity_params,
                "input_view": "redacted" if analysis.pii_classes else "original",
                "input_policy_digest": _digest(analysis.pii_classes),
                "manifest_digest": analysis.manifest_digest,
            },
        )
        record_audit_event(
            db,
            event_type=AuditEventType.MEETING_RECOMPUTE_COMPLETED,
            workspace_id=work.workspace_id,
            target_type="meeting",
            target_id=work.meeting_id,
            details={
                "outcome": COMPLETED,
                "analysis_state": analysis.state.value,
                "revision_id": revision.id,
                "analysis_run_id": persisted.run.id,
                "previous_analysis_run_id": previous.id if previous is not None else None,
                "evidence_count": len(persisted.plan.spans),
                "rejected_reference_count": persisted.plan.rejected_reference_count,
                "action_item_count": len(persisted.actions),
                **extraction_audit_details(persisted.extraction, persisted.extraction_reason),
            },
            settings=settings,
        )
        db.commit()
    return COMPLETED


def _persist_failure(
    work: RecomputeWork, *, code: str, pii_classes: tuple[str, ...], settings: Settings
) -> str:
    """Append a failed, non-current run on the target; the current run, the
    summary and the action items stay as they are."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=work.workspace_id)
        now = database_now(db)
        locked = _lock_target(db, work, settings=settings)
        if isinstance(locked, str):
            db.rollback()
            return locked
        meeting, revision = locked
        failed = record_analysis_run(
            db,
            meeting=meeting,
            revision=revision,
            state=StageState.FAILED,
            reason_code=code,
            now=now,
            input_view="redacted" if pii_classes else "original",
            input_policy_digest=_digest(pii_classes),
            is_current=False,
        )
        failures, _ = failed_recomputes(
            db, workspace_id=work.workspace_id, meeting_id=work.meeting_id, revision_id=revision.id
        )
        record_audit_event(
            db,
            event_type=AuditEventType.MEETING_RECOMPUTE_FAILED,
            workspace_id=work.workspace_id,
            target_type="meeting",
            target_id=work.meeting_id,
            outcome="failure",
            details={
                "outcome": FAILED,
                "cause": code,
                "revision_id": revision.id,
                "analysis_run_id": failed.id,
                "failed_attempt_count": failures,
            },
            settings=settings,
        )
        db.commit()
    return FAILED


def _record_superseded(
    db: Session, *, meeting: Meeting, revision_id: str, settings: Settings
) -> None:
    current = current_revision(db, workspace_id=meeting.workspace_id, meeting_id=meeting.id)
    record_audit_event(
        db,
        event_type=AuditEventType.MEETING_RECOMPUTE_SUPERSEDED,
        workspace_id=meeting.workspace_id,
        target_type="meeting",
        target_id=meeting.id,
        details={
            "outcome": SUPERSEDED,
            "revision_id": revision_id,
            "current_revision_id": current.id if current is not None else None,
        },
        settings=settings,
    )


def _digest(pii_classes: tuple[str, ...]) -> str | None:
    return policy_digest(parse_classes(pii_classes)) if pii_classes else None


def _failure_code(exc: Exception, *, meeting_id: str) -> str:
    if type(exc).__name__ == "SoftTimeLimitExceeded":  # Celery attempt time limit
        return "attempt_timeout"
    logger.error(
        "ANALYSIS_RECOMPUTE_ERROR meeting_id=%s error_type=%s",
        meeting_id,
        type(exc).__name__,
        exc_info=True,
    )
    return RECOMPUTE_FAILED


# --- the sweeper -------------------------------------------------------------------


def sweep_stale_analyses(
    *, settings: Settings | None = None, workspace_ids: list[str] | None = None
) -> dict[str, int]:
    """Dispatch a recompute for every stale meeting below the failure cap.

    Covers a dispatch that was lost (broker down, worker killed before it
    ran).  A meeting without an analysis run is never stale; one whose current
    revision has ``recompute_max_attempts`` failed runs waits for a person.
    With ``recompute_on_correction`` off there is no automatic recompute at
    all: the sweep dispatches nothing (``disabled`` is 1) and only a person can
    ask (the manual route).
    """
    settings = settings or get_settings()
    counts = {
        "workspaces": 0,
        "stale": 0,
        "dispatched": 0,
        "at_failure_cap": 0,
        "errors": 0,
        "disabled": 0,
    }
    if not settings.recompute_on_correction:
        counts["disabled"] = 1
        return counts
    workspaces = (
        list(workspace_ids)
        if workspace_ids is not None
        else discover_workspaces(interval_seconds=settings.recompute_sweep_interval_seconds)
    )
    remaining = SWEEP_BATCH_SIZE
    for workspace_id in workspaces:
        if remaining <= 0:
            break
        counts["workspaces"] += 1
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            stale = _stale_meetings(db, workspace_id=workspace_id)
            db.rollback()
        for meeting_id, revision_id, failures in stale:
            counts["stale"] += 1
            if failures >= settings.recompute_max_attempts:
                counts["at_failure_cap"] += 1
                continue
            if remaining <= 0:
                continue
            remaining -= 1
            if dispatch_recompute(
                meeting_id, revision_id, workspace_id=workspace_id, settings=settings
            ):
                counts["dispatched"] += 1
            else:
                counts["errors"] += 1
    return counts


def _stale_meetings(db: Session, *, workspace_id: str) -> list[tuple[str, str, int]]:
    """``(meeting id, current revision id, failed recomputes of it)`` of every
    live meeting whose current run read another revision, oldest revision first."""
    current_run = aliased(AnalysisRun)
    failed = aliased(AnalysisRun)
    failures = (
        select(func.count(failed.id))
        .where(
            failed.meeting_id == Meeting.id,
            failed.transcript_revision_id == TranscriptRevision.id,
            failed.state == StageState.FAILED,
        )
        .correlate(Meeting, TranscriptRevision)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Meeting.id, TranscriptRevision.id, failures)
        .join(
            TranscriptRevision,
            and_(
                TranscriptRevision.meeting_id == Meeting.id,
                TranscriptRevision.is_current.is_(True),
            ),
        )
        .join(
            current_run,
            and_(current_run.meeting_id == Meeting.id, current_run.is_current.is_(True)),
        )
        .where(
            Meeting.workspace_id == workspace_id,
            Meeting.deleted_at.is_(None),
            current_run.transcript_revision_id != TranscriptRevision.id,
        )
        .order_by(TranscriptRevision.created_at.asc(), Meeting.id.asc())
    ).all()
    return [(meeting_id, revision_id, int(count or 0)) for meeting_id, revision_id, count in rows]
