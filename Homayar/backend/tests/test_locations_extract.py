from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.locations.extract import ResolvedLocation, extract_locations
from backend.locations.gazetteer import get_gazetteer
from backend.locations.normalize import normalize_address
from backend.locations.routing import (
    RoutingFilters,
    bounding_box,
    haversine_km,
    is_open_at,
    rank_locations,
    route,
    validate_opening_hours,
)
from backend.locations.textnorm import normalize_char_stream, normalize_text
from backend.locations.validate import is_valid_postal_code


def _types(result):
    return [c.type for c in result.candidates]


# --- normalization ----------------------------------------------------------


def test_normalizer_maps_arabic_letters_zwnj_and_digits_keeping_offsets():
    text = "ساكن كرج، پلاك ۴۵ و خرم‌آباد"
    norm = normalize_char_stream(text)
    assert "ساکن کرج" in norm.text
    assert "45" in norm.text
    assert "خرم آباد" in norm.text
    start = norm.text.index("کرج")
    o_start, o_end = norm.span(start, start + 3)
    assert text[o_start:o_end] == "كرج"


def test_normalizer_drops_diacritics_and_tatweel():
    assert normalize_text("تهـــران") == "تهران"
    assert normalize_text("شِیراز") == "شیراز"


# --- extraction -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "city_key"),
    [
        ("سلام، از اصفهان زنگ می‌زنم", "isfahan"),
        ("من ساكن تهران هستم", "tehran"),
        ("شهر کاشان، استان اصفهان", "kashan"),
        ("در شيراز نمایندگی دارید؟", "shiraz"),
        ("خرم‌آباد هستم", "khorramabad"),
        ("خرمآباد هستم", "khorramabad"),
        ("از بندر عباس تماس می‌گیرم", "bandar_abbas"),
        ("بندرعباس", "bandar_abbas"),
        ("I am calling from Mashhad", "mashhad"),
        ("customer in tabriz", "tabriz"),
        ("نورآباد فارس", "nurabad_mamasani"),
        ("نورآباد لرستان", "nurabad_delfan"),
        ("آستانه اشرفیه هستم", "astaneh_ashrafiyeh"),
        ("میدان ونک تهران", "tehran"),
    ],
)
def test_city_mentions_resolve(text, city_key):
    result = extract_locations(text)
    assert result.status == "resolved", (text, result.clarification)
    assert result.resolved is not None
    assert result.resolved.precision == "city"
    assert result.resolved.city_key == city_key


def test_candidates_carry_original_spans():
    text = "ساكن كرج هستم"
    result = extract_locations(text)
    cand = next(c for c in result.candidates if c.type == "city")
    assert text[cand.start : cand.end] == cand.text == "كرج"


def test_province_prefix_restricts_to_province_and_falls_back_to_centroid():
    result = extract_locations("استان فارس هستم")
    assert result.status == "resolved"
    assert result.resolved.precision == "province"
    assert result.resolved.confidence < 0.5
    assert result.clarification and result.clarification["slot"] == "city"
    province = next(c for c in result.candidates if c.type == "province")
    assert province.text == "استان فارس"


def test_capital_name_is_one_intent_not_ambiguity():
    result = extract_locations("اصفهان")
    assert result.status == "resolved"
    assert result.resolved.city_key == "isfahan"
    assert not result.candidates[0].ambiguous


def test_ambiguous_city_name_is_never_silently_resolved():
    result = extract_locations("از نورآباد تماس می‌گیرم")
    assert result.status == "ambiguous"
    assert result.resolved is None
    keys = {o["key"] for o in result.clarification["options"]}
    assert keys == {"nurabad_mamasani", "nurabad_delfan"}
    assert "فارس" in result.clarification["question_fa"]


def test_ambiguous_region_name():
    result = extract_locations("از آذربایجان هستم")
    assert result.status == "ambiguous"
    assert result.clarification["slot"] == "province"
    assert {o["key"] for o in result.clarification["options"]} == {
        "east_azerbaijan",
        "west_azerbaijan",
    }
    khorasan = extract_locations("خراسان")
    assert khorasan.status == "ambiguous"
    assert len(khorasan.clarification["options"]) == 3


def test_astaneh_alone_is_ambiguous_between_provinces():
    result = extract_locations("آستانه")
    assert result.status == "ambiguous"
    assert {o["key"] for o in result.clarification["options"]} == {
        "astaneh_ashrafiyeh",
        "astaneh_markazi",
    }


def test_two_different_cities_need_clarification():
    result = extract_locations("تهران یا کرج، فرقی نمی‌کند")
    assert result.status == "ambiguous"
    assert result.clarification["slot"] == "city"


def test_street_named_after_a_city_is_a_landmark_not_the_city():
    result = extract_locations("خیابان شیراز، تهران")
    assert result.resolved.city_key == "tehran"
    landmark = next(c for c in result.candidates if c.type == "landmark")
    assert landmark.value["name"] == "شیراز"


def test_full_persian_address_components():
    text = "ساكن تهران، منطقه ۳، خيابان ولیعصر، کوچه ۱۲، پلاک ۴۵، کد پستی ۱۴۳۵۶-۷۸۹۱۳"
    result = extract_locations(text)
    types = _types(result)
    assert "district" in types and "plate" in types and "postal_code" in types
    assert result.postal_code == "1435678913"
    assert "ولیعصر" in result.landmarks
    assert "12" in result.landmarks
    plate = next(c for c in result.candidates if c.type == "plate")
    assert plate.value["number"] == "45"
    assert text[plate.start : plate.end] == "پلاک ۴۵"
    district = next(c for c in result.candidates if c.type == "district")
    assert district.value["number"] == "3"


@pytest.mark.parametrize(
    ("text", "kind", "name"),
    [
        ("میدان آزادی", "square", "آزادی"),
        ("بلوار کشاورز", "boulevard", "کشاورز"),
        ("بزرگراه همت", "highway", "همت"),
        ("near Valiasr St", "street", "valiasr"),
    ],
)
def test_landmarks_are_unresolved_text(text, kind, name):
    result = extract_locations(text)
    landmark = next(c for c in result.candidates if c.type == "landmark")
    assert landmark.value == {"landmark_type": kind, "name": name}
    assert result.status == "unresolved"


def test_explicit_coordinates_with_persian_decimal_separator():
    result = extract_locations("موقعیتم ۳۵٫۷۰۱, ۵۱٫۴۰۱ است")
    assert result.status == "resolved"
    assert result.resolved.precision == "coordinates"
    assert result.resolved.latitude == pytest.approx(35.701)
    assert result.resolved.longitude == pytest.approx(51.401)


def test_out_of_range_coordinates_are_ignored():
    result = extract_locations("95.1234, 51.4000")
    assert all(c.type != "coordinates" for c in result.candidates)


def test_invalid_postal_code_is_flagged():
    result = extract_locations("کد پستی ۰۰۰۰۰۰۰۰۰۰")
    cand = next(c for c in result.candidates if c.type == "postal_code")
    assert cand.value["valid"] is False
    assert result.postal_code is None


def test_nothing_found_asks_for_city():
    result = extract_locations("سلام وقت بخیر")
    assert result.status == "unresolved"
    assert result.candidates == []
    assert result.clarification["slot"] == "city"


# --- gazetteer / validation -------------------------------------------------


def test_gazetteer_has_all_31_provinces_and_aliases():
    gazetteer = get_gazetteer()
    provinces = [p for p in gazetteer.places.values() if p.kind == "province"]
    assert len(provinces) == 31
    assert gazetteer.find_city("Esfahan").key == "isfahan"
    assert gazetteer.find_city("اصفهان").key == "isfahan"
    assert gazetteer.find_city("نورآباد") is None  # ambiguous
    assert all(p.as_dict()["approximate"] for p in provinces)


@pytest.mark.parametrize(
    ("code", "ok"),
    [
        ("1435678913", True),
        ("0435678913", False),
        ("1425678913", False),
        ("111111111", False),
        ("1111111111", False),
        ("14356a8913", False),
    ],
)
def test_postal_code_validation(code, ok):
    assert is_valid_postal_code(code) is ok


def test_normalize_address_errors_and_warnings():
    bad = normalize_address(postal_code="123", latitude=91.0, longitude=10.0)
    assert "invalid_postal_code" in bad.errors
    assert "latitude_out_of_range" in bad.errors
    outside = normalize_address(latitude=48.85, longitude=2.35, city="Paris")
    assert outside.ok
    assert "coordinates_outside_iran" in outside.warnings
    assert "city_not_in_gazetteer" in outside.warnings
    good = normalize_address(
        province="استان اصفهان".replace("استان ", ""), city="كاشان", postal_code="14356-78913"
    )
    assert good.ok and good.address.city == "کاشان" and good.address.city_key == "kashan"
    assert good.address.postal_code == "1435678913"
    scoped = normalize_address(province="لرستان", city="نورآباد")
    assert scoped.address.city_key == "nurabad_delfan" and not scoped.warnings
    ambiguous = normalize_address(city="نورآباد")
    assert "city_ambiguous" in ambiguous.warnings
    half = normalize_address(latitude=35.0)
    assert "latitude_and_longitude_must_be_given_together" in half.errors


# --- geometry ---------------------------------------------------------------


def test_haversine_tehran_isfahan_is_about_340_km():
    g = get_gazetteer()
    tehran, isfahan = g.get("tehran"), g.get("isfahan")
    distance = haversine_km(tehran.latitude, tehran.longitude, isfahan.latitude, isfahan.longitude)
    assert distance == pytest.approx(340, rel=0.05)
    assert haversine_km(35.0, 51.0, 35.0, 51.0) == 0


def test_bounding_box_contains_radius():
    box = bounding_box(35.6892, 51.3890, 50)
    for lat, lon in [(35.6892 + 0.44, 51.3890), (35.6892, 51.3890 + 0.54)]:
        assert haversine_km(35.6892, 51.3890, lat, lon) < 50
        assert box.min_lat <= lat <= box.max_lat and box.min_lon <= lon <= box.max_lon


# --- opening hours ----------------------------------------------------------


HOURS = {"sat": [["09:00", "17:00"]], "sun": [["09:00", "17:00"]], "thu": [["22:00", "02:00"]]}


def test_open_now_uses_tehran_time():
    # Saturday 2026-09-26 06:00 UTC = 09:30 Tehran.
    assert is_open_at(HOURS, datetime(2026, 9, 26, 6, 0, tzinfo=UTC)) is True
    # Saturday 05:00 UTC = 08:30 Tehran: closed.
    assert is_open_at(HOURS, datetime(2026, 9, 26, 5, 0, tzinfo=UTC)) is False
    # Friday 2026-09-25 00:00 Tehran (20:30 UTC Thursday): overnight from Thursday.
    assert is_open_at(HOURS, datetime(2026, 9, 24, 20, 30, tzinfo=UTC)) is True
    assert is_open_at(None, datetime.now(UTC)) is None


def test_opening_hours_validation():
    assert validate_opening_hours(HOURS) == []
    assert validate_opening_hours({"xyz": []}) == ["unknown_day:xyz"]
    assert validate_opening_hours({"sat": [["9", "x"]]}) == ["invalid_range:sat"]


# --- ranking ----------------------------------------------------------------


@dataclass
class Loc:
    id: str
    name: str
    latitude: float | None
    longitude: float | None
    kind: str = "branch"
    city: str | None = None
    is_active: bool = True
    service_tags: list[str] = field(default_factory=list)
    opening_hours: dict[str, Any] | None = None


LOCS = [
    Loc("t1", "Tehran Vanak", 35.7575, 51.4100, city="تهران", service_tags=["repair"]),
    Loc("t2", "Tehran South", 35.6000, 51.4000, city="تهران", opening_hours=HOURS),
    Loc("k1", "Karaj", 35.8400, 50.9391, city="کرج", kind="dealer", service_tags=["sales"]),
    Loc("i1", "Isfahan", 32.6546, 51.6680, city="اصفهان"),
    Loc("x1", "Closed", 35.69, 51.39, city="تهران", is_active=False),
    Loc("n1", "No coords", None, None),
]


def _q(lat, lon, precision="coordinates", confidence=0.95):
    return ResolvedLocation(lat, lon, precision, confidence)


def test_rank_by_distance_and_skip_inactive_and_unplaced():
    ranked = rank_locations(LOCS, _q(35.70, 51.40), RoutingFilters(limit=10))
    ids = [r.location.id for r in ranked]
    assert ids[:2] == ["t1", "t2"] and ids[-1] == "i1"
    assert "x1" not in ids and "n1" not in ids
    assert ranked[0].distance_km < ranked[1].distance_km


def test_rank_filters_kind_tag_and_open_now():
    q = _q(35.70, 51.40)
    assert [r.location.id for r in rank_locations(LOCS, q, RoutingFilters(kinds=["dealer"]))] == [
        "k1"
    ]
    assert [
        r.location.id for r in rank_locations(LOCS, q, RoutingFilters(service_tag="REPAIR"))
    ] == ["t1"]
    open_ranked = rank_locations(
        LOCS, q, RoutingFilters(open_now=True, reference_time=datetime(2026, 9, 26, 6, tzinfo=UTC))
    )
    assert [r.location.id for r in open_ranked] == ["t2"]
    assert "open now" in open_ranked[0].reason
    with_inactive = rank_locations(LOCS, q, RoutingFilters(include_inactive=True, limit=10))
    assert "x1" in [r.location.id for r in with_inactive]


def test_prefer_same_city_beats_a_closer_other_city():
    # A point on Tehran's western edge is closer to Karaj than to Vanak.
    q = _q(35.80, 51.05, precision="city", confidence=0.8)
    plain = rank_locations(LOCS, q, RoutingFilters(limit=3), query_city_name="تهران")
    assert plain[0].location.id == "k1"
    preferred = rank_locations(
        LOCS, q, RoutingFilters(limit=3, prefer_same_city=True), query_city_name="تهران"
    )
    assert preferred[0].location.id in {"t1", "t2"} and preferred[0].same_city
    assert "approximate centroid" in preferred[0].reason


def test_route_needs_clarification_for_ambiguous_and_unresolved_text():
    loader_calls = []

    def loader(bbox):
        loader_calls.append(bbox)
        return LOCS

    ambiguous = route(extract_locations("نورآباد"), loader, RoutingFilters())
    assert ambiguous.status == "needs_clarification"
    assert ambiguous.results == []
    assert len(ambiguous.clarification["options"]) == 2
    unresolved = route(extract_locations("سلام"), loader, RoutingFilters())
    assert unresolved.status == "needs_clarification"
    assert loader_calls == []  # never queried, never silently picked


def test_route_city_text_uses_bbox_and_returns_ranking():
    seen = []

    def loader(bbox):
        seen.append(bbox)
        return [
            loc
            for loc in LOCS
            if loc.latitude is not None
            and bbox.min_lat <= loc.latitude <= bbox.max_lat
            and bbox.min_lon <= loc.longitude <= bbox.max_lon
        ]

    result = route(extract_locations("از کرج"), loader, RoutingFilters(max_distance_km=60))
    assert result.status == "ok"
    assert result.results[0].location.id == "k1"
    assert "i1" not in [r.location.id for r in result.results]
    assert seen and seen[0] is not None
    none = route(extract_locations("از کرج"), lambda b: [], RoutingFilters(max_distance_km=5))
    assert none.status == "no_match"


def test_route_province_only_is_low_confidence_with_city_question():
    result = route(extract_locations("استان اصفهان"), lambda b: LOCS, RoutingFilters())
    assert result.status == "ok"
    assert result.confidence < 0.5
    assert result.clarification["slot"] == "city"
    assert any("province" in n for n in result.notes)
