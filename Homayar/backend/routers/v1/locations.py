"""Service locations and nearest-branch routing (not mounted yet; integrator)."""

from __future__ import annotations

from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.database.relational import get_db
from backend.locations.extract import ExtractionResult, extract_locations
from backend.locations.routing import RoutingFilters
from backend.locations.service import (
    LocationServiceError,
    create_location,
    delete_location,
    find_nearest,
    get_location,
    list_locations,
    update_location,
)
from backend.schemas.locations import (
    LocationCreateRequest,
    LocationListResponse,
    LocationResponse,
    LocationUpdateRequest,
    LocationWriteResponse,
    NearestItem,
    NearestRequest,
    NearestResponse,
    ResolveRequest,
    ResolveResponse,
)
from backend.security.context import (
    Permission,
    WorkspaceContext,
    require_permission,
    require_write_permission,
)

router = APIRouter(prefix="/locations", tags=["locations"])


def _raise(exc: LocationServiceError) -> Never:
    detail = {"code": exc.code, "message": exc.public_message}
    detail.update(exc.extra)
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _resolve_response(result: ExtractionResult) -> ResolveResponse:
    return ResolveResponse(
        status=result.status,  # type: ignore[arg-type]
        candidates=[c.as_dict() for c in result.candidates],  # type: ignore[misc]
        resolved=result.resolved.as_dict() if result.resolved else None,  # type: ignore[arg-type]
        clarification=result.clarification,  # type: ignore[arg-type]
        postal_code=result.postal_code,
        landmarks=result.landmarks,
    )


@router.get("", response_model=LocationListResponse)
def list_workspace_locations(
    kind: str | None = None,
    active: bool | None = None,
    city: str | None = None,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> LocationListResponse:
    items = list_locations(
        db, workspace_id=context.workspace.id, kind=kind, active=active, city=city
    )
    return LocationListResponse(
        items=[LocationResponse.model_validate(i) for i in items], total=len(items)
    )


@router.post("", response_model=LocationWriteResponse, status_code=201)
def create_workspace_location(
    payload: LocationCreateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> LocationWriteResponse:
    try:
        location, warnings = create_location(
            db,
            workspace_id=context.workspace.id,
            actor_user_id=context.actor.user.id,
            values=payload.model_dump(exclude_none=True),
            request_id=_request_id(request),
        )
    except LocationServiceError as exc:
        _raise(exc)
    db.commit()
    db.refresh(location)
    return LocationWriteResponse(
        **LocationResponse.model_validate(location).model_dump(), warnings=warnings
    )


@router.post("/resolve", response_model=ResolveResponse)
def resolve_location_text(
    payload: ResolveRequest,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
) -> ResolveResponse:
    return _resolve_response(extract_locations(payload.text))


@router.post("/nearest", response_model=NearestResponse)
def nearest_locations(
    payload: NearestRequest,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> NearestResponse:
    filters = RoutingFilters(
        kinds=list(payload.kinds) if payload.kinds else None,
        service_tag=payload.service_tag,
        include_inactive=payload.include_inactive,
        open_now=payload.open_now,
        reference_time=payload.reference_time,
        prefer_same_city=payload.prefer_same_city,
        max_distance_km=payload.max_distance_km,
        limit=payload.limit,
    )
    try:
        result, extraction = find_nearest(
            db,
            workspace_id=context.workspace.id,
            text=payload.text,
            latitude=payload.latitude,
            longitude=payload.longitude,
            filters=filters,
        )
    except LocationServiceError as exc:
        _raise(exc)
    return NearestResponse(
        status=result.status,  # type: ignore[arg-type]
        query=result.query.as_dict() if result.query else None,  # type: ignore[arg-type]
        confidence=round(result.confidence, 3),
        results=[
            NearestItem(
                location=LocationResponse.model_validate(r.location),
                distance_km=r.distance_km,
                reason=r.reason,
                open_now=r.open_now,
                same_city=r.same_city,
            )
            for r in result.results
        ],
        clarification=result.clarification,  # type: ignore[arg-type]
        notes=result.notes,
        extraction=_resolve_response(extraction) if extraction else None,
    )


@router.get("/{location_id}", response_model=LocationResponse)
def read_location(
    location_id: str,
    context: WorkspaceContext = Depends(require_permission(Permission.MEETING_READ)),
    db: Session = Depends(get_db),
) -> LocationResponse:
    try:
        return LocationResponse.model_validate(
            get_location(db, workspace_id=context.workspace.id, location_id=location_id)
        )
    except LocationServiceError as exc:
        _raise(exc)


@router.patch("/{location_id}", response_model=LocationWriteResponse)
def patch_location(
    location_id: str,
    payload: LocationUpdateRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> LocationWriteResponse:
    values = {k: getattr(payload, k) for k in payload.model_fields_set}
    if not values:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_changes", "message": "No updatable field was provided"},
        )
    try:
        location, warnings = update_location(
            db,
            workspace_id=context.workspace.id,
            location_id=location_id,
            actor_user_id=context.actor.user.id,
            values=values,
            request_id=_request_id(request),
        )
    except LocationServiceError as exc:
        _raise(exc)
    db.commit()
    db.refresh(location)
    return LocationWriteResponse(
        **LocationResponse.model_validate(location).model_dump(), warnings=warnings
    )


@router.delete("/{location_id}", status_code=204)
def remove_location(
    location_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_write_permission(Permission.WORKSPACE_MANAGE)),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_location(
            db,
            workspace_id=context.workspace.id,
            location_id=location_id,
            actor_user_id=context.actor.user.id,
            request_id=_request_id(request),
        )
    except LocationServiceError as exc:
        _raise(exc)
    db.commit()
    return Response(status_code=204)
