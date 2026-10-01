"""Extract location mentions from Persian/English conversation text.

Deterministic and conservative: every mention carries its original-text span
and a confidence; a name that matches several places is reported as
ambiguous with all its candidates and is never silently resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.locations.gazetteer import Gazetteer, Place, get_gazetteer
from backend.locations.textnorm import NormalizedText, is_word_char, normalize_char_stream
from backend.locations.validate import is_valid_postal_code, lat_lon_errors

# Normalized landmark markers -> landmark type.
_LANDMARK_MARKERS: dict[str, str] = {
    "خیابان": "street",
    "خ": "street",
    "میدان": "square",
    "بلوار": "boulevard",
    "کوچه": "alley",
    "بن بست": "alley",
    "بزرگراه": "highway",
    "اتوبان": "highway",
    "چهارراه": "intersection",
    "چهار راه": "intersection",
    "سه راه": "intersection",
    "پل": "bridge",
    "محله": "neighbourhood",
    "شهرک": "neighbourhood",
    "street": "street",
    "st": "street",
    "avenue": "street",
    "ave": "street",
    "square": "square",
    "sq": "square",
    "boulevard": "boulevard",
    "blvd": "boulevard",
    "alley": "alley",
    "highway": "highway",
}
_NUMBERED_MARKERS: dict[str, str] = {
    "پلاک": "plate",
    "plate": "plate",
    "no": "plate",
    "منطقه": "district",
    "ناحیه": "district",
    "district": "district",
    "واحد": "unit",
    "طبقه": "floor",
}
_PROVINCE_WORDS = ("استان",)
_CITY_WORDS = ("شهر", "شهرستان")
_EN_PROVINCE_WORDS = ("province",)
_EN_CITY_WORDS = ("city",)
_STOP_WORDS = frozenset(
    {
        "و",
        "در",
        "به",
        "از",
        "که",
        "را",
        "با",
        "هستم",
        "هست",
        "است",
        "نزدیک",
        "کنار",
        "روبروی",
        "جنب",
        "and",
        "in",
        "at",
        "near",
        "the",
        "i",
        "am",
        "is",
    }
)

_POSTAL_RE = re.compile(r"(?<![\d.])(\d{5})[- ]?(\d{5})(?![\d.])")
_COORD_RE = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{2,8})\s*[,/ ]\s*(-?\d{1,3}\.\d{2,8})(?![\d.])")
_WORD_RE = re.compile(r"[^\s,.;:!?()\[\]«»\"'،؛؟-]+")


@dataclass
class LocationCandidate:
    type: str  # province|city|region|postal_code|coordinates|landmark|district|plate|...
    text: str
    start: int
    end: int
    confidence: float
    ambiguous: bool = False
    places: list[Place] = field(default_factory=list)
    value: dict[str, Any] = field(default_factory=dict)
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "confidence": round(self.confidence, 3),
            "ambiguous": self.ambiguous,
            "places": [p.as_dict() for p in self.places],
            "value": self.value,
            "note": self.note,
        }


@dataclass
class ResolvedLocation:
    latitude: float
    longitude: float
    precision: str  # coordinates|city|province
    confidence: float
    city_key: str | None = None
    province_key: str | None = None
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "precision": self.precision,
            "confidence": round(self.confidence, 3),
            "city_key": self.city_key,
            "province_key": self.province_key,
            "source": self.source,
        }


@dataclass
class ExtractionResult:
    status: str  # resolved|ambiguous|unresolved
    candidates: list[LocationCandidate]
    resolved: ResolvedLocation | None = None
    clarification: dict[str, Any] | None = None
    postal_code: str | None = None
    landmarks: list[str] = field(default_factory=list)


class _Spans:
    def __init__(self) -> None:
        self._spans: list[tuple[int, int]] = []

    def overlaps(self, start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in self._spans)

    def add(self, start: int, end: int) -> None:
        self._spans.append((start, end))


def _bounded(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return not is_word_char(before) and not is_word_char(after)


def _word_before(text: str, start: int) -> str:
    words = _WORD_RE.findall(text[:start])
    return words[-1] if words else ""


def _word_after(text: str, end: int) -> str:
    match = _WORD_RE.search(text, end)
    return match.group(0) if match and not text[end : match.start()].strip(" ") else ""


def _candidate(norm: NormalizedText, n_start: int, n_end: int, **kwargs: Any) -> LocationCandidate:
    start, end = norm.span(n_start, n_end)
    return LocationCandidate(text=norm.original[start:end], start=start, end=end, **kwargs)


def _extract_coordinates(norm: NormalizedText, spans: _Spans) -> list[LocationCandidate]:
    out = []
    for match in _COORD_RE.finditer(norm.text):
        lat, lon = float(match.group(1)), float(match.group(2))
        if lat_lon_errors(lat, lon):
            continue
        spans.add(match.start(), match.end())
        out.append(
            _candidate(
                norm,
                match.start(),
                match.end(),
                type="coordinates",
                confidence=0.95,
                value={"latitude": lat, "longitude": lon},
            )
        )
    return out


def _extract_postal(norm: NormalizedText, spans: _Spans) -> list[LocationCandidate]:
    out = []
    for match in _POSTAL_RE.finditer(norm.text):
        if spans.overlaps(match.start(), match.end()):
            continue
        code = match.group(1) + match.group(2)
        valid = is_valid_postal_code(code)
        before = _word_before(norm.text, match.start())
        cue = before in {"پستی", "postal", "کدپستی", "code", "zip"}
        spans.add(match.start(), match.end())
        out.append(
            _candidate(
                norm,
                match.start(),
                match.end(),
                type="postal_code",
                confidence=(0.9 if cue else 0.7) if valid else 0.3,
                value={"postal_code": code, "valid": valid},
                note=None if valid else "does not follow the Iranian postal code pattern",
            )
        )
    return out


def _marker_regex(markers: dict[str, str]) -> re.Pattern[str]:
    alternatives = sorted(markers, key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(m) for m in alternatives) + r")(?!\w)")


_LANDMARK_RE = _marker_regex(_LANDMARK_MARKERS)
_NUMBERED_RE = _marker_regex(_NUMBERED_MARKERS)
_ALL_MARKERS = frozenset(_LANDMARK_MARKERS) | frozenset(_NUMBERED_MARKERS)


def _extract_numbered(norm: NormalizedText, spans: _Spans) -> list[LocationCandidate]:
    out = []
    for match in _NUMBERED_RE.finditer(norm.text):
        after = re.match(r"\s*(?:\.|:)?\s*(\d{1,5})(?!\d)", norm.text[match.end() :])
        if not after:
            continue
        end = match.end() + after.end()
        if spans.overlaps(match.start(), end):
            continue
        kind = _NUMBERED_MARKERS[match.group(1)]
        spans.add(match.start(), end)
        out.append(
            _candidate(
                norm,
                match.start(),
                end,
                type=kind,
                confidence=0.8,
                value={"number": after.group(1)},
            )
        )
    return out


def _extract_landmarks(
    norm: NormalizedText, spans: _Spans, gazetteer: Gazetteer
) -> list[LocationCandidate]:
    out = []
    text = norm.text
    for match in _LANDMARK_RE.finditer(text):
        if spans.overlaps(match.start(), match.end()):
            continue
        marker = match.group(1)
        if marker in {
            "st",
            "sq",
            "ave",
            "blvd",
            "street",
            "square",
            "avenue",
            "boulevard",
            "alley",
            "highway",
        }:
            # English: the name precedes the marker ("Valiasr St").
            words = _WORD_RE.findall(text[: match.start()])[-2:]
            words = [w for w in words if w not in _STOP_WORDS and w not in _ALL_MARKERS][-2:]
            if not words:
                continue
            name = " ".join(words)
            start = text.rfind(words[0], 0, match.start())
            end = match.end()
        else:
            name_words: list[str] = []
            cursor = match.end()
            for word_match in _WORD_RE.finditer(text, match.end()):
                gap = text[cursor : word_match.start()]
                word = word_match.group(0)
                if gap.strip() or word in _ALL_MARKERS or word in _STOP_WORDS:
                    break
                if word.isdigit():
                    if not name_words:  # "کوچه ۱۲": a numbered alley/street
                        name_words.append(word)
                        cursor = word_match.end()
                    break
                if name_words and word in gazetteer.aliases:
                    break  # "میدان ونک تهران": the city is not part of the name
                name_words.append(word)
                cursor = word_match.end()
                if len(name_words) >= 3:
                    break
            if not name_words:
                continue
            name = " ".join(name_words)
            start, end = match.start(), cursor
        if spans.overlaps(start, end):
            continue
        spans.add(start, end)
        out.append(
            _candidate(
                norm,
                start,
                end,
                type="landmark",
                confidence=0.6,
                value={"landmark_type": _LANDMARK_MARKERS[marker], "name": name},
                note="unresolved free-text landmark",
            )
        )
    return out


def _extract_places(
    norm: NormalizedText, spans: _Spans, gazetteer: Gazetteer
) -> list[LocationCandidate]:
    out = []
    text = norm.text
    for alias in gazetteer.alias_list():
        start = text.find(alias)
        while start != -1:
            end = start + len(alias)
            if _bounded(text, start, end) and not spans.overlaps(start, end):
                places = list(gazetteer.aliases[alias])
                before = _word_before(text, start)
                after = _word_after(text, end)
                confidence = 0.75
                note = None
                span_start = start
                if before in _PROVINCE_WORDS or after in _EN_PROVINCE_WORDS:
                    places = [p for p in places if p.kind == "province"] or places
                    confidence = 0.9
                    span_start = (
                        text.rfind(before, 0, start) if before in _PROVINCE_WORDS else start
                    )
                elif before in _CITY_WORDS or after in _EN_CITY_WORDS:
                    places = [p for p in places if p.kind == "city"] or places
                    confidence = 0.9
                    span_start = text.rfind(before, 0, start) if before in _CITY_WORDS else start
                places = _collapse_capital(places)
                ambiguous = len(places) > 1
                if ambiguous:
                    confidence = min(confidence, 0.5)
                    note = "name matches several places"
                kinds = {p.kind for p in places}
                ctype = (
                    "region"
                    if ambiguous and kinds == {"province"}
                    else (places[0].kind if len(kinds) == 1 else "place")
                )
                spans.add(span_start, end)
                out.append(
                    _candidate(
                        norm,
                        span_start,
                        end,
                        type=ctype,
                        confidence=confidence,
                        ambiguous=ambiguous,
                        places=places,
                        note=note,
                    )
                )
            start = text.find(alias, end)
    out.sort(key=lambda c: c.start)
    return out


def _collapse_capital(places: list[Place]) -> list[Place]:
    """A city sharing its name with the province it is capital of ("Isfahan")
    is one intent, not an ambiguity: prefer the city (more precise)."""
    if len(places) < 2:
        return places
    cities = [p for p in places if p.kind == "city"]
    provinces = [p for p in places if p.kind == "province"]
    if cities and provinces:
        kept_provinces = [
            prov for prov in provinces if not any(c.key == prov.capital_city_key for c in cities)
        ]
        return cities + kept_provinces
    return places


def _clarification(slot: str, options: list[Place], question_fa: str) -> dict[str, Any]:
    return {
        "slot": slot,
        "options": [
            {
                "key": p.key,
                "label_fa": p.name_fa,
                "label_en": p.name_en,
                "kind": p.kind,
                "province_key": p.province_key,
            }
            for p in options
        ],
        "question_fa": question_fa,
    }


def _resolve(
    candidates: list[LocationCandidate], gazetteer: Gazetteer
) -> tuple[str, ResolvedLocation | None, dict[str, Any] | None]:
    coords = [c for c in candidates if c.type == "coordinates"]
    if coords:
        c = coords[0]
        return (
            "resolved",
            ResolvedLocation(
                c.value["latitude"],
                c.value["longitude"],
                "coordinates",
                c.confidence,
                source="explicit_coordinates",
            ),
            None,
        )

    place_cands = [c for c in candidates if c.places]
    mentioned_provinces = {
        p.key for c in place_cands if not c.ambiguous for p in c.places if p.kind == "province"
    }
    firm_cities: dict[str, tuple[Place, float]] = {}
    ambiguous: list[LocationCandidate] = []
    for cand in place_cands:
        places = cand.places
        if cand.ambiguous:
            # Disambiguate by an explicitly mentioned province or a firm city's province.
            context = mentioned_provinces | {
                p.province_key for p, _ in firm_cities.values() if p.province_key
            }
            narrowed = [
                p
                for p in places
                if (p.kind == "city" and p.province_key in context)
                or (p.kind == "province" and p.key in context)
            ]
            if len(narrowed) == 1:
                places = narrowed
            else:
                ambiguous.append(cand)
                continue
        place = places[0]
        if place.kind == "city":
            firm_cities[place.key] = (place, cand.confidence)

    # Late disambiguation: a firm city mentioned after the ambiguous name.
    still_ambiguous = []
    for cand in ambiguous:
        context = mentioned_provinces | {
            p.province_key for p, _ in firm_cities.values() if p.province_key
        }
        narrowed = [
            p
            for p in cand.places
            if (p.kind == "city" and p.province_key in context)
            or (p.kind == "province" and p.key in context)
        ]
        if len(narrowed) == 1 and narrowed[0].kind == "city":
            firm_cities[narrowed[0].key] = (narrowed[0], 0.6)
        elif len(narrowed) == 1:
            mentioned_provinces.add(narrowed[0].key)
        else:
            still_ambiguous.append(cand)

    if len(firm_cities) > 1:
        options = [p for p, _ in firm_cities.values()]
        return (
            "ambiguous",
            None,
            _clarification(
                "city", options, "کدام شهر مد نظر شماست؟ " + " یا ".join(p.name_fa for p in options)
            ),
        )
    if still_ambiguous and not firm_cities:
        cand = still_ambiguous[0]
        slot = "province" if all(p.kind == "province" for p in cand.places) else "city"
        labels = [
            p.name_fa
            + (
                f" ({gazetteer.get(p.province_key).name_fa})"  # type: ignore[union-attr]
                if p.province_key and gazetteer.get(p.province_key)
                else ""
            )
            for p in cand.places
        ]
        return (
            "ambiguous",
            None,
            _clarification(slot, cand.places, "منظورتان کدام است؟ " + " یا ".join(labels)),
        )
    if firm_cities:
        place, confidence = next(iter(firm_cities.values()))
        return (
            "resolved",
            ResolvedLocation(
                place.latitude,
                place.longitude,
                "city",
                min(confidence, 0.8),
                city_key=place.key,
                province_key=place.province_key,
                source="city_centroid",
            ),
            None,
        )
    if mentioned_provinces:
        if len(mentioned_provinces) > 1:
            options = [gazetteer.get(k) for k in sorted(mentioned_provinces)]
            return (
                "ambiguous",
                None,
                _clarification(
                    "province",
                    [p for p in options if p],
                    "کدام استان مد نظر شماست؟",
                ),
            )
        province = gazetteer.get(next(iter(mentioned_provinces)))
        assert province is not None
        return (
            "resolved",
            ResolvedLocation(
                province.latitude,
                province.longitude,
                "province",
                0.4,
                province_key=province.key,
                source="province_centroid",
            ),
            {"slot": "city", "options": [], "question_fa": "در کدام شهر هستید؟"},
        )
    return (
        "unresolved",
        None,
        {
            "slot": "city",
            "options": [],
            "question_fa": "لطفاً شهر یا محدوده‌ی خود را بفرمایید.",
        },
    )


def extract_locations(text: str, gazetteer: Gazetteer | None = None) -> ExtractionResult:
    gazetteer = gazetteer or get_gazetteer()
    norm = normalize_char_stream(text or "")
    spans = _Spans()
    candidates: list[LocationCandidate] = []
    candidates += _extract_coordinates(norm, spans)
    candidates += _extract_postal(norm, spans)
    candidates += _extract_numbered(norm, spans)
    candidates += _extract_landmarks(norm, spans, gazetteer)
    candidates += _extract_places(norm, spans, gazetteer)
    candidates.sort(key=lambda c: c.start)
    status, resolved, clarification = _resolve(candidates, gazetteer)
    postal = next(
        (
            c.value["postal_code"]
            for c in candidates
            if c.type == "postal_code" and c.value.get("valid")
        ),
        None,
    )
    landmarks = [c.value["name"] for c in candidates if c.type == "landmark"]
    return ExtractionResult(
        status=status,
        candidates=candidates,
        resolved=resolved,
        clarification=clarification,
        postal_code=postal,
        landmarks=landmarks,
    )
