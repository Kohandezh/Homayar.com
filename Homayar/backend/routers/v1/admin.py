from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import JobState, ShareAudience
from backend.jobs.outbox import dispatch_job
from backend.jobs.replay import ReplayRefused, replay_dead_letter_job
from backend.media import MediaValidationPolicy
from backend.media.channel_roles import (
    InvalidChannelRoleMapping,
    UnreadableChannelRoleMappings,
    parse_mapping_body,
    validate_source,
)
from backend.observability.system_health import SystemHealthCollector, system_health_collector
from backend.review_sampling import SamplingError
from backend.schemas.admin import (
    AdminJobListResponse,
    AdminJobResponse,
    AdminJobStateCounts,
    AdminJobSummaryResponse,
    AdminLegalHoldListResponse,
    AdminLegalHoldResponse,
    AdminShareListResponse,
    AdminShareResponse,
    AuditEventListResponse,
    AuditEventResponse,
    AuditEventTypeListResponse,
    AuditEventTypeResponse,
    ChannelRoleMappingListResponse,
    ChannelRoleMappingResponse,
    JobReplayResponse,
    WorkspaceSettingsResponse,
    WorkspaceSettingsUpdateRequest,
)
from backend.schemas.coverage import CoverageResponse, ReviewSampleRequest, ReviewSampleResponse
from backend.schemas.meetings import JobResponse
from backend.schemas.system_health import SystemHealthResponse
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_platform_operator_read,
    require_write_permission,
)
from backend.services.admin_jobs_service import (
    InvalidJobCursor,
    list_workspace_jobs,
    summarize_workspace_jobs,
)
from backend.services.admin_legal_holds_service import (
    InvalidDueBefore,
    InvalidLegalHoldCursor,
    list_workspace_legal_holds,
)
from backend.services.admin_service import (
    ChannelRoleMappingNotFound,
    InvalidAuditCursor,
    InvalidAuditRange,
    InvalidWorkspaceSettings,
    WorkspaceNotFoundError,
    get_workspace_settings,
    list_channel_role_mappings,
    list_workspace_audit_events,
    remove_channel_role_mapping,
    sanitize_audit_details,
    set_channel_role_mapping,
    update_workspace_settings,
)
from backend.services.admin_shares_service import (
    InvalidShareCursor,
    ShareStatus,
    list_workspace_shares,
)
from backend.services.aggregate_metadata import coverage_missing_data, scorecard_block
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.coverage_service import (
    COVERAGE_VERSION,
    CoverageWindowError,
    compute_coverage,
    parse_window,
)
from backend.services.review_sampling_service import draw_review_sample

router = APIRouter(prefix="/admin", tags=["admin"])

# Administrative reads/writes are governed by explicit permissions that only
# OWNER/ADMIN hold (see backend/security/authorization.py); no extra role
# checks are layered on top so the matrix document stays the single policy.
require_audit_admin_context = require_permission(Permission.AUDIT_READ)
require_workspace_admin_context = require_permission(Permission.WORKSPACE_MANAGE)
require_workspace_admin_write_context = require_write_permission(Permission.WORKSPACE_MANAGE)

PRIVACY_POLICY_FIELDS = frozenset({"pii_redaction_enabled", "pii_redaction_classes"})


def _window_invalid(exc: CoverageWindowError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})


def _workspace_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "workspace_not_found", "message": "Workspace not found"},
    )


@router.get("/audit-events", response_model=AuditEventListResponse)
def list_audit_events(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    event_type: str | None = Query(default=None, min_length=1, max_length=100),
    outcome: str | None = Query(default=None, min_length=1, max_length=20),
    actor_user_id: str | None = Query(default=None, min_length=1, max_length=36),
    target_type: str | None = Query(default=None, min_length=1, max_length=60),
    target_id: str | None = Query(default=None, min_length=1, max_length=100),
    occurred_after: datetime | None = Query(default=None),
    occurred_before: datetime | None = Query(default=None),
    context: WorkspaceContext = Depends(require_audit_admin_context),
    db: Session = Depends(get_db),
) -> AuditEventListResponse:
    try:
        page = list_workspace_audit_events(
            db,
            workspace_id=context.workspace.id,
            limit=limit,
            cursor=cursor,
            event_type=event_type,
            outcome=outcome,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
        )
    except InvalidAuditCursor as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_cursor", "message": str(exc)},
        ) from exc
    except InvalidAuditRange as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_audit_range", "message": str(exc)},
        ) from exc

    return AuditEventListResponse(
        items=[
            AuditEventResponse(
                id=event.id,
                actor_user_id=event.actor_user_id,
                event_type=event.event_type,
                target_type=event.target_type,
                target_id=event.target_id,
                outcome=event.outcome,
                request_id=event.request_id,
                details=sanitize_audit_details(event.details),
                occurred_at=event.occurred_at,
            )
            for event in page.events
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/audit-event-types", response_model=AuditEventTypeListResponse)
def list_audit_event_types(
    context: WorkspaceContext = Depends(require_audit_admin_context),
) -> AuditEventTypeListResponse:
    # The catalog is static; the workspace context only applies the access
    # rules of GET /admin/audit-events.
    return AuditEventTypeListResponse(
        items=[
            AuditEventTypeResponse(
                event_type=member.value,
                group=member.group,
                target_type=member.target_type,
            )
            for member in sorted(AuditEventType, key=lambda member: member.value)
        ]
    )


@router.get("/shares", response_model=AdminShareListResponse)
def list_shares(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    status: ShareStatus | None = Query(default=None),
    audience: ShareAudience | None = Query(default=None),
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
) -> AdminShareListResponse:
    """Every share link of the workspace, newest first (HOM-192).

    ``status`` is ``revoked`` when the link was revoked, else ``expired`` when
    ``expires_at`` has passed, else ``active``. Filters combine with AND.
    Shares of soft-deleted meetings are left out. The token, its hash, the
    share URL and recipient addresses are never returned.
    """
    try:
        page = list_workspace_shares(
            db,
            workspace_id=context.workspace.id,
            limit=limit,
            cursor=cursor,
            status=status,
            audience=audience,
        )
    except InvalidShareCursor as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_cursor", "message": str(exc)},
        ) from exc

    return AdminShareListResponse(
        items=[AdminShareResponse.model_validate(row) for row in page.rows],
        next_cursor=page.next_cursor,
    )


@router.get("/workspace-settings", response_model=WorkspaceSettingsResponse)
def read_workspace_settings(
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
) -> WorkspaceSettingsResponse:
    try:
        workspace = get_workspace_settings(db, workspace_id=context.workspace.id)
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    return WorkspaceSettingsResponse.model_validate(workspace)


@router.patch("/workspace-settings", response_model=WorkspaceSettingsResponse)
def patch_workspace_settings(
    payload: WorkspaceSettingsUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WorkspaceSettingsResponse:
    changes = payload.model_dump(exclude_unset=True)
    if PRIVACY_POLICY_FIELDS & changes.keys() and not context.has(Permission.PII_ORIGINAL_ACCESS):
        # WP07-T06: whoever may switch redaction off could read originals, so
        # the policy needs the same right as original access (owner).
        raise HTTPException(
            status_code=403,
            detail={
                "code": "permission_denied",
                "message": "Changing the redaction policy needs pii:original_access",
            },
        )
    try:
        workspace, updated_fields = update_workspace_settings(
            db,
            workspace_id=context.workspace.id,
            changes=changes,
        )
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    except InvalidWorkspaceSettings as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_workspace_settings", "message": str(exc)},
        ) from exc

    record_audit_event(
        db,
        event_type=AuditEventType.WORKSPACE_SETTINGS_UPDATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace",
        target_id=context.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        details={"fields": ",".join(updated_fields)},
        settings=settings,
    )
    db.commit()
    db.refresh(workspace)
    return WorkspaceSettingsResponse.model_validate(workspace)


# --- Trusted channel-role mappings (HOM-37, ADR 0039) ----------------------------------
# Session-only administration: workspace:manage is never an API-key scope.  The
# mapping is the only origin of a trusted channel role; it lives in the
# workspace settings, never in an upload, file metadata or a heuristic.


def _invalid_mapping(exc: InvalidChannelRoleMapping) -> HTTPException:
    # Unreadable stored settings are a conflict with the stored state (409);
    # an invalid request is 422.
    status_code = 409 if isinstance(exc, UnreadableChannelRoleMappings) else 422
    return HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)})


def _audit_mapping_change(
    db: Session,
    *,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    change_kind: str,
    source_type: str,
    source_provider: str,
    version: int,
    digest: str | None,
    channels: tuple[tuple[int, str], ...],
    previous_version: int | None,
    previous_digest: str | None,
) -> None:
    record_audit_event(
        db,
        event_type=AuditEventType.WORKSPACE_SETTINGS_UPDATED,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace",
        target_id=context.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        details={
            "fields": "channel_role_mappings",
            "change_kind": change_kind,
            "source_type": source_type,
            "source_provider": source_provider,
            "mapping_version": version,
            "mapping_digest": digest,
            "previous_version": previous_version,
            "previous_digest": previous_digest,
            "channels": [{"channel": channel, "mapped_role": role} for channel, role in channels],
        },
        settings=settings,
    )


@router.get("/channel-role-mappings", response_model=ChannelRoleMappingListResponse)
def list_channel_role_mappings_route(
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
) -> ChannelRoleMappingListResponse:
    try:
        mappings = list_channel_role_mappings(db, workspace_id=context.workspace.id)
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    return ChannelRoleMappingListResponse(
        items=[ChannelRoleMappingResponse.from_mapping(mapping) for mapping in mappings]
    )


@router.put(
    "/channel-role-mappings/{source_type}/{source_provider}",
    response_model=ChannelRoleMappingResponse,
)
def put_channel_role_mapping(
    source_type: str,
    source_provider: str,
    request: Request,
    # Any JSON value; null and an empty body arrive as None and are refused by
    # the same rules as every other invalid body.
    payload: Any = Body(default=None),
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ChannelRoleMappingResponse:
    max_channels = MediaValidationPolicy().max_channels
    try:
        validate_source(source_type, source_provider)
        channels = parse_mapping_body(payload, max_channels=max_channels)
        mapping, previous, changed = set_channel_role_mapping(
            db,
            workspace_id=context.workspace.id,
            source_type=source_type,
            source_provider=source_provider,
            channels=channels,
            actor_user_id=context.actor.user.id,
            now=datetime.now(UTC),
            max_channels=max_channels,
        )
    except InvalidChannelRoleMapping as exc:
        raise _invalid_mapping(exc) from exc
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    if changed:
        _audit_mapping_change(
            db,
            request=request,
            context=context,
            settings=settings,
            change_kind="set",
            source_type=mapping.source_type,
            source_provider=mapping.source_provider,
            version=mapping.version,
            digest=mapping.digest,
            channels=mapping.channels,
            previous_version=previous.version if previous else None,
            previous_digest=previous.digest if previous else None,
        )
        db.commit()
    return ChannelRoleMappingResponse.from_mapping(mapping)


@router.delete("/channel-role-mappings/{source_type}/{source_provider}", status_code=204)
def delete_channel_role_mapping(
    source_type: str,
    source_provider: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        validate_source(source_type, source_provider)
        removal = remove_channel_role_mapping(
            db,
            workspace_id=context.workspace.id,
            source_type=source_type,
            source_provider=source_provider,
            actor_user_id=context.actor.user.id,
            now=datetime.now(UTC),
        )
    except InvalidChannelRoleMapping as exc:
        raise _invalid_mapping(exc) from exc
    except WorkspaceNotFoundError as exc:
        raise _workspace_not_found() from exc
    except ChannelRoleMappingNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "channel_role_mapping_not_found", "message": str(exc)},
        ) from exc
    _audit_mapping_change(
        db,
        request=request,
        context=context,
        settings=settings,
        change_kind="removed",
        source_type=removal.source_type,
        source_provider=removal.source_provider,
        version=removal.version,
        digest=removal.digest,
        channels=removal.channels,
        previous_version=removal.previous_version,
        previous_digest=removal.removed.digest if removal.removed else None,
    )
    db.commit()
    return Response(status_code=204)


@router.get("/jobs", response_model=AdminJobListResponse)
def list_jobs(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    state: list[JobState] | None = Query(default=None),
    stage: str | None = Query(default=None, min_length=1, max_length=40),
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdminJobListResponse:
    """The workspace's processing jobs, newest first (HOM-183).

    ``state`` may repeat (``?state=failed&state=dead_letter``); filters combine
    with AND. Jobs of soft-deleted meetings are left out. ``replayable`` is
    true when ``POST /admin/jobs/{job_id}/replay`` passes its static checks:
    dead-lettered, under the replay limit, meeting and recording still stored,
    retention not ended. Quota is not part of the flag, so a replayable job
    may still be refused with ``429`` when the workspace allowance is
    exhausted.
    """
    try:
        page = list_workspace_jobs(
            db,
            workspace_id=context.workspace.id,
            limit=limit,
            max_replays=settings.job_max_replays,
            cursor=cursor,
            states=state,
            stage=stage,
        )
    except InvalidJobCursor as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_cursor", "message": str(exc)},
        ) from exc

    return AdminJobListResponse(
        items=[
            AdminJobResponse(
                id=row.job.id,
                meeting_id=row.job.meeting_id,
                meeting_title=row.meeting_title,
                state=row.job.state,
                stage=row.job.stage,
                attempt_count=row.job.attempt_count,
                max_attempts=row.job.max_attempts,
                failure_code=row.job.failure_code,
                failure_class=row.job.failure_class,
                failure_message=row.failure_message,
                replay_count=row.job.replay_count,
                max_replays=settings.job_max_replays,
                replayable=row.replayable,
                created_at=row.job.created_at,
                updated_at=row.job.updated_at,
            )
            for row in page.rows
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/jobs/summary", response_model=AdminJobSummaryResponse)
def summarize_jobs(
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
) -> AdminJobSummaryResponse:
    """Job count per state for the workspace, same scope as ``GET /admin/jobs``."""
    counts = summarize_workspace_jobs(db, workspace_id=context.workspace.id)
    return AdminJobSummaryResponse(
        counts=AdminJobStateCounts(**{state.value: count for state, count in counts.items()}),
        total=sum(counts.values()),
    )


@router.get("/coverage", response_model=CoverageResponse)
def read_coverage(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    context: WorkspaceContext = Depends(require_workspace_admin_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CoverageResponse:
    """Ingestion, processing, evaluation and review coverage (HOM-72), ``coverage/1``.

    Four separate counters over the workspace's ingestion ledger rows with
    ``from <= received_at < to`` (ISO 8601 with a timezone; default: the 30
    days before now; at most 366 days). Each counter's denominator is the
    previous one's numerator, and there is no blended number. The answer also
    carries the scorecard state and the missing-data indicators (HOM-81).
    Refused with ``422 coverage_window_invalid``. A read, not audited.
    """
    try:
        window = parse_window(from_, to)
    except CoverageWindowError as exc:
        raise _window_invalid(exc) from exc
    coverage = compute_coverage(
        db,
        workspace_id=context.workspace.id,
        window=window,
        recompute_max_attempts=settings.recompute_max_attempts,
        # HOM-312 (U2): the app's cache keeps each meeting's unsupported findings verdict
        cache=getattr(request.app.state, "trend_judgment_cache", None),
        organization_id=context.workspace.organization_id,
    )
    scorecard = scorecard_block()
    return CoverageResponse.model_validate(
        {
            "coverage_version": COVERAGE_VERSION,
            "window": window.as_dict(),
            "counters": coverage.counters,
            "scorecard": scorecard,
            "missing_data": coverage_missing_data(coverage.counters, scorecard),
        }
    )


@router.post("/review-samples", response_model=ReviewSampleResponse)
def create_review_sample(
    payload: ReviewSampleRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewSampleResponse:
    """Draw a seeded review sample (HOM-72), ``review-sampling/1``.

    The population is the window's ``fully_evaluated`` meetings (the review
    denominator of ``GET /admin/coverage``). Without ``seed``, or with
    ``"seed": null``, the server picks one and returns it. Each scope value must
    be ``unassigned`` or have the shape of its column: a lowercase UUID for
    ``team_id`` and ``agent_user_id``, ``^[a-z][a-z0-9_]{0,29}$`` for ``source``,
    ``^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})?$`` for ``language``. Each draw is
    recorded as one ``review_sample.drawn`` audit event with the seed, the
    versions, the window, the normalized config and the digests, enough to draw
    it again. Refused with ``422`` and no audit event:
    ``coverage_window_invalid``, ``seed_invalid``, ``sampling_config_invalid``
    or ``sampling_config_empty``.
    """
    try:
        sample = draw_review_sample(
            db,
            workspace_id=context.workspace.id,
            raw_from=payload.from_,
            raw_to=payload.to,
            seed=payload.seed,
            config=payload.config,
            recompute_max_attempts=settings.recompute_max_attempts,
        )
    except CoverageWindowError as exc:
        raise _window_invalid(exc) from exc
    except SamplingError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc

    record_audit_event(
        db,
        event_type=AuditEventType.REVIEW_SAMPLE_DRAWN,
        actor_user_id=context.actor.user.id,
        workspace_id=context.workspace.id,
        target_type="workspace",
        target_id=context.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        details=sample.audit_details(),
        settings=settings,
    )
    db.commit()
    return ReviewSampleResponse.model_validate(
        {
            "coverage_version": COVERAGE_VERSION,
            "window": sample.window.as_dict(),
            "draw": sample.draw.to_json(),
        }
    )


@router.post("/jobs/{job_id}/replay", response_model=JobReplayResponse, status_code=202)
def replay_job(
    job_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace_admin_write_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobReplayResponse:
    """Requeue a dead-lettered job (WP06-T05): authorized, audited, bounded.

    Refusals leave the job untouched: ``409 job_not_replayable`` (not
    dead-lettered), ``409 replay_limit_reached``, ``409 meeting_deleted`` /
    ``retention_expired`` / ``recording_missing`` (nothing is resurrected),
    ``429 audio_quota_exceeded`` / ``concurrent_job_limit_reached`` (current
    entitlement does not cover the replay).
    """
    try:
        result = replay_dead_letter_job(
            db,
            job_id=job_id,
            workspace_id=context.workspace.id,
            actor_user_id=context.actor.user.id,
            settings=settings,
            request_id=getattr(request.state, "request_id", None),
        )
    except ReplayRefused as exc:
        db.rollback()
        detail: dict[str, object] = {"code": exc.code, "message": exc.message}
        if exc.details:
            detail["details"] = exc.details
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    db.commit()
    # Request-time publication through the shared publisher; the outbox
    # sweeper republishes the re-armed intent if this attempt fails.
    outcome = dispatch_job(result.job.id, settings)
    db.refresh(result.job)
    return JobReplayResponse(
        job=JobResponse.model_validate(result.job),
        replay=result.replay,
        max_replays=settings.job_max_replays,
        dispatch=outcome.state,
    )


@router.get("/legal-holds", response_model=AdminLegalHoldListResponse)
def list_legal_holds(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    due_before: datetime | None = Query(default=None),
    context: WorkspaceContext = Depends(require_permission(Permission.LEGAL_HOLD_MANAGE)),
    db: Session = Depends(get_db),
) -> AdminLegalHoldListResponse:
    """The workspace's media objects on legal hold, review due first (HOM-192).

    ``due_before`` keeps holds whose review date is on or before it; a value
    without a time zone is read as UTC. Holds of deleted conversations and of
    objects without a meeting are listed too (``meeting_title`` is then the
    stored title, or null without a meeting row). Owner only:
    ``legal_hold:manage`` is never an API-key scope. A read, not audited.
    """
    try:
        page = list_workspace_legal_holds(
            db,
            workspace_id=context.workspace.id,
            limit=limit,
            cursor=cursor,
            due_before=due_before,
        )
    except InvalidDueBefore as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_due_before", "message": str(exc)},
        ) from exc
    except InvalidLegalHoldCursor as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_cursor", "message": str(exc)},
        ) from exc

    return AdminLegalHoldListResponse(
        items=[
            AdminLegalHoldResponse(
                media_id=row.media.id,
                kind=row.media.kind,
                meeting_id=row.media.meeting_id,
                meeting_title=row.meeting_title,
                reason=row.media.legal_hold_reason,
                placed_by_user_id=row.media.legal_hold_by_user_id,
                placed_at=row.media.legal_hold_at,
                review_at=row.media.legal_hold_review_at,
            )
            for row in page.rows
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/system-health", response_model=SystemHealthResponse)
def read_system_health(
    response: Response,
    context: WorkspaceContext = Depends(
        require_platform_operator_read(Permission.WORKSPACE_MANAGE)
    ),
    collector: SystemHealthCollector = Depends(system_health_collector),
) -> SystemHealthResponse:
    """Deployment-wide health for a platform operator (HOM-227; the console, HOM-232).

    Dependencies (``up``, ``down``, ``timeout``), the schema revision against the
    code's migration head, the durable and broker queues, every periodic sweep, the
    backup freshness, the audit sink's verify run and the build identity. ``status``
    is ``down`` when the database is not up, ``degraded`` when anything else is not
    healthy, else ``ok``. Every source has its own deadline and the answer comes in
    under a second, also when a dependency hangs; a failed source gives ``null``,
    never a guess. The data is not the workspace's: the workspace only proves an
    active ``workspace:manage`` membership. Platform operators only, from a
    signed-in session (never an API key). A read, not audited.
    """
    response.headers["Cache-Control"] = "no-store"
    return SystemHealthResponse.model_validate(collector.collect())
