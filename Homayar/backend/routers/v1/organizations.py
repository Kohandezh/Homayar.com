"""Organization administration and scoped analytics routes (WP03-T03).

Every route under ``/organizations/{organization_id}`` resolves the caller's
*current* organization membership (``require_organization``) and an explicit
organization permission; none of them grants workspace content access.

HOM-82 adds the role-scoped trend views: executive aggregates
(``/trends/issues``, ``organization:analytics:read``) and the analyst drill-down
(``/workspaces/{workspace_id}/trends/issues``, ``organization:records:read``).  Both are
session only, like every organization route, and withhold any cohort that rests on 1 to
4 agents in some week (``role_trend_service``).

HOM-299 (ADR 0041, L2) adds ``coverage``, ``scorecard``, ``missing_data`` and ``disclosure``
to each ``/aggregates`` item: those of the executive view's block for the same workspace,
grant and closed-week window.  Its existing counts stay.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.relational import get_db
from backend.database.saas_models import OrganizationRole, WorkspaceRole
from backend.issue_trends import TrendFilterError, TrendFilters
from backend.schemas.organizations import (
    AggregatesResponse,
    InstalledWorkspaceMemberResponse,
    OrganizationListResponse,
    OrganizationMemberGrantRequest,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    OrganizationMemberUpdateRequest,
    OrganizationResponse,
    OrganizationSummaryResponse,
    OrganizationWorkspaceCreateRequest,
    OrganizationWorkspaceListResponse,
    OrganizationWorkspaceResponse,
    ScopedRecordListResponse,
    ScopedRecordResponse,
    ScopeGrantListResponse,
    ScopeGrantRequest,
    ScopeGrantResponse,
    WorkspaceAggregateResponse,
    WorkspaceMemberInstallRequest,
)
from backend.schemas.trends import OrganizationDrilldownResponse, OrganizationTrendResponse
from backend.security.authorization import OrganizationPermission
from backend.security.context import (
    Actor,
    OrganizationContext,
    require_actor,
    require_organization_permission,
    require_organization_write_permission,
)
from backend.services import organization_service as service
from backend.services import role_trend_service as roles
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.services.issue_trend_service import TrendSeriesError

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _raise(exc: service.OrganizationServiceError) -> Never:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.public_message},
    ) from exc


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _member_response(
    record: service.OrganizationMemberRecord, *, now: datetime
) -> OrganizationMemberResponse:
    membership = record.membership
    return OrganizationMemberResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        email=record.user.email,
        display_name=record.user.display_name,
        role=membership.role.value,
        is_active=membership.is_active,
        valid_from=membership.valid_from,
        valid_until=membership.valid_until,
        revoked_at=membership.revoked_at,
        is_effective=service.is_effective(membership, now=now),
        granted_by_user_id=membership.granted_by_user_id,
        created_at=membership.created_at,
    )


def _grant_response(grant, *, user_id: str) -> ScopeGrantResponse:
    return ScopeGrantResponse(
        id=grant.id,
        organization_id=grant.organization_id,
        membership_id=grant.membership_id,
        user_id=user_id,
        workspace_id=grant.workspace_id,
        team_id=grant.team_id,
        granted_by_user_id=grant.granted_by_user_id,
        revoked_at=grant.revoked_at,
        created_at=grant.created_at,
    )


def _audit(
    request: Request,
    db: Session,
    context: OrganizationContext,
    *,
    event_type: str,
    target_type: str,
    target_id: str,
    details: dict,
    workspace_id: str | None = None,
    settings: Settings,
) -> None:
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=context.actor.user.id,
        workspace_id=workspace_id,
        target_type=target_type,
        target_id=target_id,
        request_id=_request_id(request),
        ip_address=request.client.host if request.client else None,
        details={"organization_id": context.organization.id, **details},
        settings=settings,
    )


# --- organizations ------------------------------------------------------------------


@router.get("", response_model=OrganizationListResponse)
def list_my_organizations(
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
) -> OrganizationListResponse:
    rows = service.list_user_organizations(db, user_id=actor.user.id)
    items = [
        OrganizationSummaryResponse(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status=organization.status,
            role=membership.role.value,
            valid_until=membership.valid_until,
        )
        for organization, membership in rows
    ]
    return OrganizationListResponse(items=items, total=len(items))


@router.get("/{organization_id}", response_model=OrganizationResponse)
def read_organization(
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.ORGANIZATION_READ)
    ),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    workspaces = service.list_organization_workspaces(
        db, organization=context.organization, access=context.access
    )
    return OrganizationResponse(
        id=context.organization.id,
        name=context.organization.name,
        slug=context.organization.slug,
        status=context.organization.status,
        role=context.membership.role.value,
        unrestricted=context.access.unrestricted,
        workspace_count=len(workspaces),
        created_at=context.organization.created_at,
    )


# --- members ----------------------------------------------------------------------------


@router.get("/{organization_id}/members", response_model=OrganizationMemberListResponse)
def list_organization_members(
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.MEMBER_READ)
    ),
    db: Session = Depends(get_db),
) -> OrganizationMemberListResponse:
    now = datetime.now(UTC)
    records = service.list_members(db, organization_id=context.organization.id)
    items = [_member_response(record, now=now) for record in records]
    return OrganizationMemberListResponse(items=items, total=len(items))


@router.post(
    "/{organization_id}/members", response_model=OrganizationMemberResponse, status_code=201
)
def grant_organization_member(
    payload: OrganizationMemberGrantRequest,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.MEMBER_MANAGE)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OrganizationMemberResponse:
    now = datetime.now(UTC)
    try:
        record = service.grant_membership(
            db,
            organization=context.organization,
            actor=context.membership,
            email=str(payload.email),
            role=OrganizationRole(payload.role),
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            now=now,
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED,
        target_type="organization_membership",
        target_id=record.membership.id,
        details={"role": record.membership.role.value, "user_id": record.user.id},
        settings=settings,
    )
    db.commit()
    return _member_response(record, now=now)


@router.patch(
    "/{organization_id}/members/{membership_id}", response_model=OrganizationMemberResponse
)
def update_organization_member(
    membership_id: str,
    payload: OrganizationMemberUpdateRequest,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.MEMBER_MANAGE)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OrganizationMemberResponse:
    now = datetime.now(UTC)
    try:
        record = service.change_membership(
            db,
            organization=context.organization,
            actor=context.membership,
            membership_id=membership_id,
            role=OrganizationRole(payload.role) if payload.role else None,
            valid_until=payload.valid_until,
            clear_valid_until=payload.clear_valid_until,
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_MEMBER_CHANGED,
        target_type="organization_membership",
        target_id=record.membership.id,
        details={
            "role": record.membership.role.value,
            "user_id": record.user.id,
            "valid_until": (
                record.membership.valid_until.isoformat() if record.membership.valid_until else ""
            ),
        },
        settings=settings,
    )
    db.commit()
    return _member_response(record, now=now)


@router.delete(
    "/{organization_id}/members/{membership_id}",
    status_code=204,
    response_class=Response,
)
def revoke_organization_member(
    membership_id: str,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.MEMBER_MANAGE)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        record = service.revoke_membership(
            db,
            organization=context.organization,
            actor=context.membership,
            membership_id=membership_id,
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_MEMBER_REVOKED,
        target_type="organization_membership",
        target_id=record.membership.id,
        details={"role": record.membership.role.value, "user_id": record.user.id},
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)


# --- workspaces -------------------------------------------------------------------------


@router.get("/{organization_id}/workspaces", response_model=OrganizationWorkspaceListResponse)
def list_organization_workspaces(
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.WORKSPACE_READ)
    ),
    db: Session = Depends(get_db),
) -> OrganizationWorkspaceListResponse:
    now = datetime.now(UTC)
    workspaces = service.list_organization_workspaces(
        db, organization=context.organization, access=context.access
    )
    roles = service.workspace_roles_for_user(
        db,
        workspace_ids=[workspace.id for workspace in workspaces],
        user_id=context.actor.user.id,
        now=now,
    )
    items = []
    for workspace in workspaces:
        teams = context.access.team_filter(workspace.id)
        my_role = roles.get(workspace.id)
        items.append(
            OrganizationWorkspaceResponse(
                id=workspace.id,
                organization_id=workspace.organization_id,
                name=workspace.name,
                slug=workspace.slug,
                status=workspace.status,
                record_scope=workspace.record_scope.value,
                my_workspace_role=my_role.value if my_role else None,
                scoped_team_ids=sorted(teams) if teams is not None else None,
                created_at=workspace.created_at,
            )
        )
    return OrganizationWorkspaceListResponse(items=items, total=len(items))


@router.post(
    "/{organization_id}/workspaces",
    response_model=OrganizationWorkspaceResponse,
    status_code=201,
)
def create_organization_workspace(
    payload: OrganizationWorkspaceCreateRequest,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.WORKSPACE_CREATE)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OrganizationWorkspaceResponse:
    workspace = service.create_organization_workspace(
        db,
        organization=context.organization,
        owner=context.actor.user,
        name=payload.name,
        settings=settings,
    )
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_WORKSPACE_CREATED,
        target_type="workspace",
        target_id=workspace.id,
        workspace_id=workspace.id,
        details={"name": workspace.name, "slug": workspace.slug},
        settings=settings,
    )
    db.commit()
    return OrganizationWorkspaceResponse(
        id=workspace.id,
        organization_id=workspace.organization_id,
        name=workspace.name,
        slug=workspace.slug,
        status=workspace.status,
        record_scope=workspace.record_scope.value,
        my_workspace_role=WorkspaceRole.OWNER.value,
        scoped_team_ids=None,
        created_at=workspace.created_at,
    )


@router.post(
    "/{organization_id}/workspaces/{workspace_id}/members",
    response_model=InstalledWorkspaceMemberResponse,
    status_code=201,
)
def install_workspace_member(
    workspace_id: str,
    payload: WorkspaceMemberInstallRequest,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.WORKSPACE_ADMINISTER)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InstalledWorkspaceMemberResponse:
    try:
        membership = service.install_workspace_member(
            db,
            organization=context.organization,
            actor=context.membership,
            workspace_id=workspace_id,
            user_id=payload.user_id,
            role=WorkspaceRole(payload.role),
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_WORKSPACE_MEMBER_INSTALLED,
        target_type="membership",
        target_id=membership.id,
        workspace_id=membership.workspace_id,
        details={"role": membership.role.value, "user_id": membership.user_id},
        settings=settings,
    )
    db.commit()
    return InstalledWorkspaceMemberResponse(
        membership_id=membership.id,
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role.value,
        is_active=membership.is_active,
    )


@router.delete(
    "/{organization_id}/workspaces/{workspace_id}/members/{membership_id}",
    status_code=204,
    response_class=Response,
)
def remove_workspace_member(
    workspace_id: str,
    membership_id: str,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.WORKSPACE_ADMINISTER)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        membership = service.remove_workspace_member(
            db,
            organization=context.organization,
            actor=context.membership,
            workspace_id=workspace_id,
            membership_id=membership_id,
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_WORKSPACE_MEMBER_REMOVED,
        target_type="membership",
        target_id=membership.id,
        workspace_id=membership.workspace_id,
        details={"role": membership.role.value, "user_id": membership.user_id},
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)


# --- scope grants -------------------------------------------------------------------------


@router.get("/{organization_id}/scope-grants", response_model=ScopeGrantListResponse)
def list_scope_grants(
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.MEMBER_READ)
    ),
    db: Session = Depends(get_db),
) -> ScopeGrantListResponse:
    grants = service.list_scope_grants(db, organization_id=context.organization.id)
    members = {
        record.membership.id: record.user.id
        for record in service.list_members(db, organization_id=context.organization.id)
    }
    items = [
        _grant_response(grant, user_id=members.get(grant.membership_id, "")) for grant in grants
    ]
    return ScopeGrantListResponse(items=items, total=len(items))


@router.post("/{organization_id}/scope-grants", response_model=ScopeGrantResponse, status_code=201)
def create_scope_grant(
    payload: ScopeGrantRequest,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.MEMBER_MANAGE)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ScopeGrantResponse:
    try:
        grant = service.grant_scope(
            db,
            organization=context.organization,
            actor=context.membership,
            membership_id=payload.membership_id,
            workspace_id=payload.workspace_id,
            team_id=payload.team_id,
        )
        target = service.get_member(
            db, organization_id=context.organization.id, membership_id=grant.membership_id
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_SCOPE_GRANTED,
        target_type="organization_scope_grant",
        target_id=grant.id,
        details={
            "membership_id": grant.membership_id,
            "user_id": target.user.id,
            "workspace_id": grant.workspace_id,
            "team_id": grant.team_id or "",
        },
        settings=settings,
    )
    db.commit()
    return _grant_response(grant, user_id=target.user.id)


@router.delete(
    "/{organization_id}/scope-grants/{grant_id}", status_code=204, response_class=Response
)
def revoke_scope_grant(
    grant_id: str,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_write_permission(OrganizationPermission.MEMBER_MANAGE)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        grant = service.revoke_scope(
            db, organization=context.organization, actor=context.membership, grant_id=grant_id
        )
    except service.OrganizationServiceError as exc:
        _raise(exc)
    _audit(
        request,
        db,
        context,
        event_type=AuditEventType.ORGANIZATION_SCOPE_REVOKED,
        target_type="organization_scope_grant",
        target_id=grant.id,
        details={"membership_id": grant.membership_id, "workspace_id": grant.workspace_id},
        settings=settings,
    )
    db.commit()
    return Response(status_code=204)


# --- scoped analytics -------------------------------------------------------------------------


def _judgment_cache(request: Request):
    """The app's trend judgment cache (HOM-312), handed to the services."""
    return getattr(request.app.state, "trend_judgment_cache", None)


def _aggregate_filters(
    since: datetime | None, until: datetime | None, now: datetime
) -> TrendFilters:
    """The window ``W`` of the ``/aggregates`` metadata (HOM-299, ADR 0041 decision 13): the
    organization grid query of ``since`` and ``until`` when the HOM-82 parser accepts it,
    else its default, the 4 closed weeks before this week.  Never a 422."""
    raw: dict[str, str | list[str]] = {}
    if since is not None:
        raw["from"] = since.isoformat()
    if until is not None:
        raw["to"] = until.isoformat()
    try:
        filters, _grain = roles.organization_query(raw, now=now)
    except (TrendFilterError, TrendSeriesError, roles.TrendViewError):
        filters, _grain = roles.organization_query({}, now=now)
    return filters


@router.get("/{organization_id}/aggregates", response_model=AggregatesResponse)
def read_aggregates(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.ANALYTICS_READ)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AggregatesResponse:
    """Counts per scoped workspace, and (HOM-299) each item's ``coverage``, ``scorecard``,
    ``missing_data`` and ``disclosure``: those of the executive view's block for the same
    workspace, grant and window ``coverage_window`` (ADR 0041, L2).  A block HOM-82 withholds
    is withheld here too; the existing counts keep their values."""
    now = datetime.now(UTC)
    filters = _aggregate_filters(since, until, now)
    aggregates = service.workspace_aggregates(
        db,
        organization=context.organization,
        access=context.access,
        since=since,
        until=until,
        trend_block=partial(
            roles.organization_workspace_block,
            db,
            user_id=context.access.user_id,
            filters=filters,
            grain="none",
            weeks=roles.organization_weeks(filters),
            recompute_max_attempts=settings.recompute_max_attempts,
            cache=_judgment_cache(request),
        ),
    )
    return AggregatesResponse(
        organization_id=context.organization.id,
        since=since,
        until=until,
        generated_at=datetime.now(UTC),
        items=[
            WorkspaceAggregateResponse(
                workspace_id=item.workspace.id,
                workspace_name=item.workspace.name,
                team_ids=sorted(item.team_ids) if item.team_ids is not None else None,
                meetings_total=item.meetings_total,
                meetings_by_status=item.meetings_by_status,
                audio_seconds_total=item.audio_seconds_total,
                action_items_total=item.action_items_total,
                **item.metadata,
            )
            for item in aggregates
        ],
        disclosure_policy=roles.organization_disclosure_policy(),
        coverage_window=filters.window.to_json(),
    )


@router.get("/{organization_id}/records", response_model=ScopedRecordListResponse)
def list_scoped_records(
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.RECORDS_READ)
    ),
    db: Session = Depends(get_db),
) -> ScopedRecordListResponse:
    records, truncated = service.scoped_records(
        db, organization=context.organization, access=context.access
    )
    items = [
        ScopedRecordResponse(
            workspace_id=record.workspace_id,
            meeting_id=record.meeting.id,
            team_id=record.meeting.team_id,
            title=record.meeting.title,
            status=record.meeting.status.value,
            language=record.meeting.language,
            duration_seconds=record.meeting.duration_seconds,
            created_at=record.meeting.created_at,
        )
        for record in records
    ]
    return ScopedRecordListResponse(items=items, total=len(items), truncated=truncated)


# --- role-scoped trend views (HOM-82) -----------------------------------------------------------


def _trend_query(request: Request) -> tuple:
    """The organization grid query, or a 422 with ``error.code`` and ``error.details.key``."""
    try:
        return roles.organization_query(
            roles.query_dict(request.query_params.multi_items()), now=datetime.now(UTC)
        )
    except (TrendFilterError, TrendSeriesError, roles.TrendViewError) as exc:
        code = exc.code.value if isinstance(exc, TrendFilterError) else exc.code
        raise HTTPException(
            status_code=422,
            detail={"code": code, "message": str(exc), "details": {"key": exc.key}},
        ) from exc


@router.get("/{organization_id}/trends/issues", response_model=OrganizationTrendResponse)
def read_organization_trends(
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.ANALYTICS_READ)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OrganizationTrendResponse:
    """Executive aggregates (HOM-82): one block per scoped workspace, no total.

    Org owner, admin, analyst and executive; a session only (every API key is
    ``401 api_key_not_accepted``).  Whole closed weeks from Saturday 00:00 Asia/Tehran,
    grain ``none`` or ``week``.  ``team_id``, ``agent_user_id``, ``language``,
    ``source_type`` and ``timezone`` are ``422 trend_parameter_not_allowed``.  A workspace
    block is withheld when a week, or a row of a week, rests on 1 to 4 agents.
    """
    filters, grain = _trend_query(request)
    answer = roles.organization_trends(
        db,
        organization=context.organization,
        access=context.access,
        filters=filters,
        grain=grain,
        recompute_max_attempts=settings.recompute_max_attempts,
        cache=_judgment_cache(request),
    )
    return OrganizationTrendResponse.model_validate(answer)


@router.get(
    "/{organization_id}/workspaces/{workspace_id}/trends/issues",
    response_model=OrganizationDrilldownResponse,
)
def read_organization_drilldown(
    workspace_id: str,
    request: Request,
    context: OrganizationContext = Depends(
        require_organization_permission(OrganizationPermission.RECORDS_READ)
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OrganizationDrilldownResponse:
    """Analyst drill-down (HOM-82): the grant unit of one workspace and one block per
    active team inside it, decided per week.

    Org owner, admin and analyst (an executive is ``403 permission_denied``); a session
    only.  A workspace outside the caller's scope is ``404 workspace_not_found``.  The
    query is the one of the executive aggregates.  No list of conversations.
    """
    filters, grain = _trend_query(request)
    scope = service.scoped_workspace(
        db, organization=context.organization, access=context.access, workspace_id=workspace_id
    )
    if scope is None:
        _raise(service.OrganizationWorkspaceNotFoundError())
    workspace, team_ids = scope
    answer = roles.organization_drilldown(
        db,
        organization=context.organization,
        workspace=workspace,
        team_ids=team_ids,
        user_id=context.access.user_id,
        filters=filters,
        grain=grain,
        recompute_max_attempts=settings.recompute_max_attempts,
        cache=_judgment_cache(request),
    )
    return OrganizationDrilldownResponse.model_validate(answer)
