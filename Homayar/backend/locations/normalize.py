"""Normalized address object and validation (errors vs warnings)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.locations.gazetteer import get_gazetteer
from backend.locations.textnorm import normalize_text
from backend.locations.validate import is_valid_postal_code, lat_lon_errors, outside_iran


@dataclass
class NormalizedAddress:
    province: str | None = None
    city: str | None = None
    district: str | None = None
    street: str | None = None
    postal_code: str | None = None
    address_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    province_key: str | None = None
    city_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AddressValidation:
    address: NormalizedAddress
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = normalize_text(value)
    return cleaned or None


def normalize_postal_code(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"[\s-]", "", normalize_text(value))
    return digits or None


def normalize_address(
    *,
    province: str | None = None,
    city: str | None = None,
    district: str | None = None,
    street: str | None = None,
    postal_code: str | None = None,
    address_text: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> AddressValidation:
    gazetteer = get_gazetteer()
    address = NormalizedAddress(
        province=_clean(province),
        city=_clean(city),
        district=_clean(district),
        street=_clean(street),
        postal_code=normalize_postal_code(postal_code),
        address_text=(address_text or "").strip() or None,
        latitude=latitude,
        longitude=longitude,
    )
    result = AddressValidation(address=address)
    if address.postal_code is not None and not is_valid_postal_code(address.postal_code):
        result.errors.append("invalid_postal_code")
    coord_errors = lat_lon_errors(latitude, longitude)
    result.errors.extend(coord_errors)
    if not coord_errors and latitude is not None and outside_iran(latitude, longitude):  # type: ignore[arg-type]
        result.warnings.append("coordinates_outside_iran")

    city_place = gazetteer.find_city(address.city)
    province_place = gazetteer.find_province(address.province)
    if city_place:
        address.city_key = city_place.key
    elif address.city:
        result.warnings.append("city_not_in_gazetteer")
    if province_place:
        address.province_key = province_place.key
    elif address.province:
        result.warnings.append("province_not_in_gazetteer")
    if city_place and province_place and city_place.province_key != province_place.key:
        # Same-name cities in several provinces: re-check against all matches.
        matches = [p for p in gazetteer.lookup(address.city or "") if p.kind == "city"]
        if not any(p.province_key == province_place.key for p in matches):
            result.warnings.append("city_province_mismatch")
    if address.city and not city_place:
        matches = [p for p in gazetteer.lookup(address.city) if p.kind == "city"]
        if province_place:
            scoped = [p for p in matches if p.province_key == province_place.key]
            if len(scoped) == 1:
                address.city_key = scoped[0].key
                result.warnings.remove("city_not_in_gazetteer")
        elif len(matches) > 1:
            result.warnings[result.warnings.index("city_not_in_gazetteer")] = "city_ambiguous"
    return result
