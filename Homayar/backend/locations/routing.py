"""Nearest-location ranking (pure Python; no PostGIS).

The database step is a bounding-box prefilter on the indexed
(workspace_id, latitude, longitude) columns - plain range predicates that work
identically on SQLite and PostgreSQL - followed by exact haversine distances
here.  Uncertain queries return ``needs_clarification`` and never silently
pick a place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any, Protocol

from backend.locations.extract import ExtractionResult, ResolvedLocation
from backend.locations.textnorm import normalize_text

EARTH_RADIUS_KM = 6371.0088
_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # datetime.weekday() order

try:  # pragma: no cover - depends on host tz database
    from zoneinfo import ZoneInfo

    TEHRAN_TZ: Any = ZoneInfo("Asia/Tehran")
except Exception:  # noqa: BLE001 - no tz database: Iran has used +03:30 without DST since 2022
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


class LocationLike(Protocol):
    id: str
    kind: str
    name: str
    city: str | None
    latitude: float | None
    longitude: float | None
    is_active: bool
    service_tags: list[str] | None
    opening_hours: dict[str, Any] | None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


def bounding_box(lat: float, lon: float, radius_km: float) -> BoundingBox:
    """A box that contains every point within ``radius_km`` (conservative)."""
    d_lat = math.degrees(radius_km / EARTH_RADIUS_KM)
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    d_lon = min(180.0, math.degrees(radius_km / (EARTH_RADIUS_KM * cos_lat)))
    return BoundingBox(
        max(-90.0, lat - d_lat),
        min(90.0, lat + d_lat),
        max(-180.0, lon - d_lon),
        min(180.0, lon + d_lon),
    )


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    h = int(hours)
    if h == 24 and int(minutes) == 0:
        return time(23, 59, 59)
    return time(h, int(minutes))


def validate_opening_hours(hours: dict[str, Any] | None) -> list[str]:
    """Format: {"sat": [["09:00", "17:00"], ...], ...}; days mon..sun."""
    if hours is None:
        return []
    errors = []
    for day, ranges in hours.items():
        if day not in _DAY_KEYS:
            errors.append(f"unknown_day:{day}")
            continue
        if not isinstance(ranges, list):
            errors.append(f"invalid_ranges:{day}")
            continue
        for item in ranges:
            try:
                start, end = item
                _parse_hhmm(start)
                _parse_hhmm(end)
            except (TypeError, ValueError):
                errors.append(f"invalid_range:{day}")
    return errors


def is_open_at(hours: dict[str, Any] | None, reference: datetime) -> bool | None:
    """``None`` when hours are unknown.  Overnight ranges (22:00-02:00) spill
    into the next day.  ``reference`` is converted to Asia/Tehran."""
    if not hours:
        return None
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    local = reference.astimezone(TEHRAN_TZ)
    now = local.time().replace(tzinfo=None)
    today = _DAY_KEYS[local.weekday()]
    yesterday = _DAY_KEYS[(local.weekday() - 1) % 7]
    for start_s, end_s in hours.get(today, []) or []:
        start, end = _parse_hhmm(start_s), _parse_hhmm(end_s)
        if start <= end:
            if start <= now < end or (end == time(23, 59, 59) and now >= start):
                return True
        elif now >= start:
            return True
    for start_s, end_s in hours.get(yesterday, []) or []:
        start, end = _parse_hhmm(start_s), _parse_hhmm(end_s)
        if start > end and now < end:
            return True
    return False


@dataclass
class RoutingFilters:
    kinds: list[str] | None = None
    service_tag: str | None = None
    include_inactive: bool = False
    open_now: bool = False
    reference_time: datetime | None = None
    prefer_same_city: bool = False
    max_distance_km: float | None = None
    limit: int = 5


@dataclass
class RankedLocation:
    location: Any
    distance_km: float
    reason: str
    open_now: bool | None
    same_city: bool


@dataclass
class RoutingResult:
    status: str  # ok|needs_clarification|no_match
    query: ResolvedLocation | None
    results: list[RankedLocation] = field(default_factory=list)
    confidence: float = 0.0
    clarification: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


def rank_locations(
    locations: list[LocationLike],
    query: ResolvedLocation,
    filters: RoutingFilters,
    *,
    query_city_name: str | None = None,
) -> list[RankedLocation]:
    reference = filters.reference_time or datetime.now(UTC)
    wanted_tag = normalize_text(filters.service_tag) if filters.service_tag else None
    query_city = normalize_text(query_city_name) if query_city_name else None
    ranked = []
    for loc in locations:
        if loc.latitude is None or loc.longitude is None:
            continue
        if not filters.include_inactive and not loc.is_active:
            continue
        if filters.kinds and loc.kind not in filters.kinds:
            continue
        if wanted_tag and wanted_tag not in {normalize_text(t) for t in (loc.service_tags or [])}:
            continue
        open_state = is_open_at(loc.opening_hours, reference)
        if filters.open_now and open_state is not True:
            continue
        distance = haversine_km(query.latitude, query.longitude, loc.latitude, loc.longitude)
        if filters.max_distance_km is not None and distance > filters.max_distance_km:
            continue
        same_city = bool(query_city and loc.city and normalize_text(loc.city) == query_city)
        parts = [f"{distance:.1f} km from the {query.precision} point"]
        if query.precision != "coordinates":
            parts.append("distance measured from an approximate centroid")
        if same_city:
            parts.append("same city")
        if open_state is True:
            parts.append("open now")
        elif open_state is None:
            parts.append("opening hours unknown")
        ranked.append(
            RankedLocation(loc, round(distance, 2), "; ".join(parts), open_state, same_city)
        )
    if filters.prefer_same_city and query_city:
        ranked.sort(key=lambda r: (not r.same_city, r.distance_km, r.location.name))
    else:
        ranked.sort(key=lambda r: (r.distance_km, r.location.name))
    return ranked[: max(1, filters.limit)]


def route(
    extraction: ExtractionResult | None,
    locations_loader,
    filters: RoutingFilters,
    *,
    explicit: ResolvedLocation | None = None,
    query_city_name: str | None = None,
    min_confidence: float = 0.3,
) -> RoutingResult:
    """``locations_loader(bbox_or_None)`` returns candidate rows."""
    query = explicit or (extraction.resolved if extraction else None)
    if query is None:
        clarification = extraction.clarification if extraction else None
        return RoutingResult(
            status="needs_clarification",
            query=None,
            clarification=clarification
            or {"slot": "city", "options": [], "question_fa": "لطفاً شهر خود را بفرمایید."},
            notes=[f"location {extraction.status if extraction else 'missing'}"],
        )
    if query.confidence < min_confidence:
        return RoutingResult(
            status="needs_clarification",
            query=query,
            clarification=extraction.clarification if extraction else None,
            notes=["location confidence below threshold"],
        )
    notes: list[str] = []
    radius = filters.max_distance_km
    bbox = bounding_box(query.latitude, query.longitude, radius) if radius else None
    ranked = rank_locations(
        list(locations_loader(bbox)), query, filters, query_city_name=query_city_name
    )
    clarification = None
    if query.precision == "province":
        notes.append("only a province was given; ranking from its approximate centroid")
        clarification = extraction.clarification if extraction else None
    if not ranked:
        return RoutingResult(
            status="no_match",
            query=query,
            confidence=query.confidence,
            clarification=clarification,
            notes=notes + ["no location matched the filters"],
        )
    return RoutingResult(
        status="ok",
        query=query,
        results=ranked,
        confidence=query.confidence,
        clarification=clarification,
        notes=notes,
    )
