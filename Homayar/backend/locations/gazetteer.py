"""In-memory gazetteer over the bundled static data (approximate centroids)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.locations.gazetteer_data import AMBIGUOUS_REGION_ALIASES, CITIES, PROVINCES
from backend.locations.textnorm import normalize_text


@dataclass(frozen=True)
class Place:
    key: str
    kind: str  # "province" | "city"
    name_fa: str
    name_en: str
    latitude: float
    longitude: float
    province_key: str | None = None
    capital_city_key: str | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "name_fa": self.name_fa,
            "name_en": self.name_en,
            "province_key": self.province_key,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "approximate": True,
        }


@dataclass(frozen=True)
class Gazetteer:
    places: dict[str, Place]
    aliases: dict[str, tuple[Place, ...]]  # normalized alias -> places

    def get(self, key: str) -> Place | None:
        return self.places.get(key)

    def lookup(self, name: str) -> tuple[Place, ...]:
        return self.aliases.get(normalize_text(name), ())

    def find_city(self, name: str | None) -> Place | None:
        if not name:
            return None
        matches = [p for p in self.lookup(name) if p.kind == "city"]
        return matches[0] if len(matches) == 1 else None

    def find_province(self, name: str | None) -> Place | None:
        if not name:
            return None
        matches = [p for p in self.lookup(name) if p.kind == "province"]
        return matches[0] if len(matches) == 1 else None

    def alias_list(self) -> list[str]:
        """Aliases longest first, for greedy matching."""
        return sorted(self.aliases, key=len, reverse=True)


@lru_cache(maxsize=1)
def get_gazetteer() -> Gazetteer:
    places: dict[str, Place] = {}
    aliases: dict[str, list[Place]] = {}

    def add(alias: str, place: Place) -> None:
        normalized = normalize_text(alias)
        if not normalized:
            return
        bucket = aliases.setdefault(normalized, [])
        if place not in bucket:
            bucket.append(place)

    for key, fa, en, lat, lon, capital, extra in PROVINCES:
        place = Place(key, "province", fa, en, lat, lon, capital_city_key=capital)
        places[key] = place
        for alias in (fa, en, *extra):
            add(alias, place)
    for key, fa, en, province, lat, lon, extra in CITIES:
        place = Place(key, "city", fa, en, lat, lon, province_key=province)
        places[key] = place
        for alias in (fa, en, *extra):
            add(alias, place)
    for alias, keys in AMBIGUOUS_REGION_ALIASES.items():
        for key in keys:
            add(alias, places[key])
    return Gazetteer(places=places, aliases={k: tuple(v) for k, v in aliases.items()})
