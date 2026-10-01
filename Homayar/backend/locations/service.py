"""Tenant-scoped CRUD and routing queries for service locations.

Every query filters by ``workspace_id`` explicitly (defence in depth on top of
RLS).  Writes record audit events; the caller commits.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.locations.extract import ResolvedLocation, extract_locations
from backend.locations.gazetteer import get_gazetteer
from backend.locations.models import LOCATION_KINDS, ServiceLocation
from backend.locations.normalize import normalize_address
from backend.locations.routing import BoundingBox, RoutingFilters, RoutingResult, route
from backend.locations.validate import lat_lon_errors, outside_iran
from backend.services.audit_service import record_audit_event

_ADDRESS_FIELDS = (
    "province",
    "city",
    "district",
    "street",
    "postal_code",
    "address_text",
    "latitude",
    "longitude",
)
_PLAIN_FIELDS = ("kind", "name", "code", "phone", "is_active", "service_tags", "opening_hours")


class LocationServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code
        self.extra = extra


def _apply(location: ServiceLocation, values: dict[str, Any]) -> list[str]:
    from backend.locations.routing import validate_opening_hours

    if "kind" in values and values["kind"] not in LOCATION_KINDS:
        raise LocationServiceError("invalid_kind", "Unknown location kind", 422)
    if "opening_hours" in values:
        errors = validate_opening_hours(values["opening_hours"])
        if errors:
            raise LocationServiceError(
                "invalid_opening_hours", "Opening hours are malformed", 422, errors=errors
            )
    for name in _PLAIN_FIELDS:
        if name in values:
            value = values[name]
            if name == "service_tags":
                value = sorted({str(t).strip() for t in (value or []) if str(t).strip()})
            setattr(location, name, value)
    merged = {name: getattr(location, name) for name in _ADDRESS_FIELDS}
    merged.update({k: v for k, v in values.items() if k in _ADDRESS_FIELDS})
    validation = normalize_address(**merged)
    if validation.errors:
        raise LocationServiceError(
            "invalid_address", "The address is invalid", 422, errors=validation.errors
        )
    address = validation.address
    for name in _ADDRESS_FIELDS:
        setattr(location, name, getattr(address, name))
    return validation.warnings


def _audit(
    db: Session,
    *,
    event: str,
    workspace_id: str,
    actor_user_id: str,
    location: ServiceLocation,
    request_id: str | None,
    details: dict | None = None,
) -> None:
    record_audit_event(
        db,
        event_type=event,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target_type="service_location",
        target_id=location.id,
        request_id=request_id,
        details={"code": location.code, "kind": location.kind, **(details or {})},
    )


def _code_taken(db: Session, workspace_id: str, code: str, exclude_id: str | None = None) -> bool:
    stmt = select(ServiceLocation.id).where(
        ServiceLocation.workspace_id == workspace_id, ServiceLocation.code == code
    )
    if exclude_id:
        stmt = stmt.where(ServiceLocation.id != exclude_id)
    return db.scalar(stmt) is not None


def create_location(
    db: Session,
    *,
    workspace_id: str,
    actor_user_id: str,
    values: dict[str, Any],
    request_id: str | None = None,
) -> tuple[ServiceLocation, list[str]]:
    if _code_taken(db, workspace_id, values["code"]):
        raise LocationServiceError("code_taken", "A location with this code exists", 409)
    location = ServiceLocation(workspace_id=workspace_id, service_tags=[])
    warnings = _apply(location, values)
    db.add(location)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise LocationServiceError("code_taken", "A location with this code exists", 409) from exc
    _audit(
        db,
        event="service_location.created",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        location=location,
        request_id=request_id,
    )
    return location, warnings


def get_location(db: Session, *, workspace_id: str, location_id: str) -> ServiceLocation:
    location = db.scalar(
        select(ServiceLocation).where(
            ServiceLocation.workspace_id == workspace_id, ServiceLocation.id == location_id
        )
    )
    if location is None:
        raise LocationServiceError("location_not_found", "Location not found", 404)
    return location


def list_locations(
    db: Session,
    *,
    workspace_id: str,
    kind: str | None = None,
    active: bool | None = None,
    city: str | None = None,
) -> list[ServiceLocation]:
    stmt = select(ServiceLocation).where(ServiceLocation.workspace_id == workspace_id)
    if kind:
        stmt = stmt.where(ServiceLocation.kind == kind)
    if active is not None:
        stmt = stmt.where(ServiceLocation.is_active.is_(active))
    if city:
        from backend.locations.textnorm import normalize_text

        stmt = stmt.where(ServiceLocation.city == normalize_text(city))
    return list(db.scalars(stmt.order_by(ServiceLocation.name, ServiceLocation.id)))


def update_location(
    db: Session,
    *,
    workspace_id: str,
    location_id: str,
    actor_user_id: str,
    values: dict[str, Any],
    request_id: str | None = None,
) -> tuple[ServiceLocation, list[str]]:
    location = get_location(db, workspace_id=workspace_id, location_id=location_id)
    if "code" in values and _code_taken(db, workspace_id, values["code"], location.id):
        raise LocationServiceError("code_taken", "A location with this code exists", 409)
    warnings = _apply(location, values)
    db.flush()
    _audit(
        db,
        event="service_location.updated",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        location=location,
        request_id=request_id,
        details={"fields": ",".join(sorted(values))},
    )
    return location, warnings


def delete_location(
    db: Session,
    *,
    workspace_id: str,
    location_id: str,
    actor_user_id: str,
    request_id: str | None = None,
) -> None:
    location = get_location(db, workspace_id=workspace_id, location_id=location_id)
    _audit(
        db,
        event="service_location.deleted",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        location=location,
        request_id=request_id,
    )
    db.delete(location)
    db.flush()


def _loader(db: Session, workspace_id: str, include_inactive: bool):
    def load(bbox: BoundingBox | None) -> list[ServiceLocation]:
        stmt = select(ServiceLocation).where(
            ServiceLocation.workspace_id == workspace_id,
            ServiceLocation.latitude.is_not(None),
        )
        if not include_inactive:
            stmt = stmt.where(ServiceLocation.is_active.is_(True))
        if bbox is not None:
            stmt = stmt.where(
                ServiceLocation.latitude.between(bbox.min_lat, bbox.max_lat),
                ServiceLocation.longitude.between(bbox.min_lon, bbox.max_lon),
            )
        return list(db.scalars(stmt))

    return load


def find_nearest(
    db: Session,
    *,
    workspace_id: str,
    text: str | None,
    latitude: float | None,
    longitude: float | None,
    filters: RoutingFilters,
) -> tuple[RoutingResult, Any]:
    extraction = extract_locations(text) if text else None
    explicit = None
    query_city_name = None
    if latitude is not None or longitude is not None:
        errors = lat_lon_errors(latitude, longitude)
        if errors:
            raise LocationServiceError(
                "invalid_coordinates", "Invalid coordinates", 422, errors=errors
            )
        explicit = ResolvedLocation(
            latitude,
            longitude,
            "coordinates",
            0.95,  # type: ignore[arg-type]
            source="request_coordinates",
        )
    if extraction and extraction.resolved and extraction.resolved.city_key:
        place = get_gazetteer().get(extraction.resolved.city_key)
        query_city_name = place.name_fa if place else None
    result = route(
        extraction,
        _loader(db, workspace_id, filters.include_inactive),
        filters,
        explicit=explicit,
        query_city_name=query_city_name,
    )
    if explicit is not None and outside_iran(explicit.latitude, explicit.longitude):
        result.notes.append("coordinates_outside_iran")
    return result, extraction
