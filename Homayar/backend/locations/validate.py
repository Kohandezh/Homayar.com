"""Address validation primitives (no I/O)."""

from __future__ import annotations

import re

from backend.locations.gazetteer_data import IRAN_BBOX

_POSTAL_DIGITS = re.compile(r"^\d{10}$")


def is_valid_postal_code(code: str) -> bool:
    """Iranian 10-digit postal code, structural check only.

    Heuristic from the public format: the first five digits (area block) never
    contain 0 or 2, and a code is never one repeated digit.  Not a lookup
    against the national postal database.
    """
    if not _POSTAL_DIGITS.match(code):
        return False
    if any(ch in "02" for ch in code[:5]):
        return False
    return len(set(code)) > 1


def lat_lon_errors(latitude: float | None, longitude: float | None) -> list[str]:
    errors: list[str] = []
    if (latitude is None) != (longitude is None):
        errors.append("latitude_and_longitude_must_be_given_together")
        return errors
    if latitude is None:
        return errors
    if not -90.0 <= latitude <= 90.0:
        errors.append("latitude_out_of_range")
    if not -180.0 <= longitude <= 180.0:  # type: ignore[operator]
        errors.append("longitude_out_of_range")
    return errors


def outside_iran(latitude: float, longitude: float) -> bool:
    return not (
        IRAN_BBOX["min_lat"] <= latitude <= IRAN_BBOX["max_lat"]
        and IRAN_BBOX["min_lon"] <= longitude <= IRAN_BBOX["max_lon"]
    )
