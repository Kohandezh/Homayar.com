"""Supported trend filters and their pure validator (HOM-79, D3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from functools import cache

import pytest

from backend.database.saas_models import IngestionSourceType
from backend.issue_trends import (
    DEFAULT_WINDOW,
    MAX_FILTER_VALUES,
    MAX_WINDOW,
    SCOPE_KEYS,
    SOURCE_TYPES,
    UNSUPPORTED_KEYS,
    WINDOW_BASIS,
    FilterErrorCode,
    FilterKey,
    TrendDefinition,
    TrendFilterError,
    TrendFilters,
    TrendWindow,
    load_definition,
    selected_family_ids,
    selected_issue_ids,
    service_area_ids,
    validate_filters,
)
from backend.services import coverage_service


@cache
def _definition() -> TrendDefinition:
    return load_definition("trends", 1)


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
TEAM = "7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"
TEAM_2 = "0a9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"


def check(raw: dict[str, object]) -> TrendFilters:
    return validate_filters(raw, _definition(), now=NOW)


def refused(raw: dict[str, object]) -> tuple[FilterErrorCode, str | None]:
    with pytest.raises(TrendFilterError) as caught:
        check(raw)
    return caught.value.code, caught.value.key


# --- constants ------------------------------------------------------------------------------


def test_window_rules_are_the_coverage_rules() -> None:
    assert WINDOW_BASIS == coverage_service.WINDOW_BASIS
    assert DEFAULT_WINDOW == coverage_service.DEFAULT_WINDOW
    assert MAX_WINDOW == coverage_service.MAX_WINDOW


def test_source_types_are_the_ingestion_enum() -> None:
    assert SOURCE_TYPES == tuple(member.value for member in IngestionSourceType)


def test_filter_keys_and_codes() -> None:
    assert [key.value for key in FilterKey] == [
        "from",
        "to",
        "team_id",
        "agent_user_id",
        "language",
        "source_type",
        "family",
        "issue",
        "service_area",
    ]
    assert all(code.value.startswith("filter_") for code in FilterErrorCode)
    assert SCOPE_KEYS == {"workspace_id", "organization_id", "role", "scope"}
    assert "product" in UNSUPPORTED_KEYS
    assert not (SCOPE_KEYS & UNSUPPORTED_KEYS)
    assert not ({key.value for key in FilterKey} & (SCOPE_KEYS | UNSUPPORTED_KEYS))


# --- valid input ----------------------------------------------------------------------------


def test_defaults() -> None:
    filters = check({})
    assert filters.window == TrendWindow(start=NOW - timedelta(days=30), end=NOW)
    assert filters.team_ids == filters.agent_user_ids == filters.languages == ()
    assert filters.source_types == filters.families == filters.issues == filters.service_areas == ()


def test_a_full_valid_query_is_normalized() -> None:
    filters = check(
        {
            "from": "2026-09-23T03:30:00+03:30",
            "to": "2026-10-23T00:00:00Z",
            "team_id": [TEAM, TEAM_2, TEAM],
            "agent_user_id": TEAM,
            "language": ["FA", "en", "fa"],
            "source_type": ("voicemail", "api"),
            "family": "service_complaint",
            "issue": ["complaint_refund"],
            "service_area": ["refund"],
        }
    )
    assert filters.window.start == datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
    assert filters.window.end == datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
    assert filters.window.start.utcoffset() == timedelta(0)
    assert filters.team_ids == (TEAM_2, TEAM)
    assert filters.agent_user_ids == (TEAM,)
    assert filters.languages == ("en", "fa")
    assert filters.source_types == ("api", "voicemail")
    assert filters.families == ("service_complaint",)
    assert filters.issues == ("complaint_refund",)
    assert filters.service_areas == ("refund",)
    assert filters.to_json() == {
        "window": {
            "from": "2026-09-23T00:00:00+00:00",
            "to": "2026-10-23T00:00:00+00:00",
            "basis": "ingestion_received_at",
        },
        "team_id": [TEAM_2, TEAM],
        "agent_user_id": [TEAM],
        "language": ["en", "fa"],
        "source_type": ["api", "voicemail"],
        "family": ["service_complaint"],
        "issue": ["complaint_refund"],
        "service_area": ["refund"],
    }


def test_window_to_json() -> None:
    window = TrendWindow(
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert window.to_json() == {
        "from": "2026-01-01T00:00:00+00:00",
        "to": "2026-01-02T00:00:00+00:00",
        "basis": "ingestion_received_at",
    }


def test_only_from_defaults_to_now() -> None:
    filters = check({"from": "2026-10-01T00:00:00+00:00"})
    assert filters.window.end == NOW


def test_only_to_defaults_from_30_days_earlier() -> None:
    filters = check({"to": "2026-10-10T00:00:00+00:00"})
    assert filters.window.start == datetime(2026, 9, 10, tzinfo=UTC)


def test_the_366_day_boundary() -> None:
    end = datetime(2026, 10, 1, tzinfo=UTC)
    ok = check({"from": (end - MAX_WINDOW).isoformat(), "to": end.isoformat()})
    assert ok.window.end - ok.window.start == MAX_WINDOW
    assert refused(
        {"from": (end - MAX_WINDOW - timedelta(seconds=1)).isoformat(), "to": end.isoformat()}
    ) == (FilterErrorCode.WINDOW_INVALID, None)


def test_the_value_limit() -> None:
    teams = [f"{i:08x}-0b4f-4e8a-9c3d-5f6a7b8c9d0e" for i in range(MAX_FILTER_VALUES)]
    assert len(check({"team_id": teams + teams[:3]}).team_ids) == MAX_FILTER_VALUES
    extra = teams + ["ffffffff-0b4f-4e8a-9c3d-5f6a7b8c9d0e"]
    assert refused({"team_id": extra}) == (FilterErrorCode.BAD_VALUE, "team_id")


def test_every_family_and_active_issue_and_topic_is_accepted() -> None:
    families = [family.id for family in _definition().families]
    assert check({"family": families}).families == tuple(sorted(families))
    issues = [issue.id for issue in _definition().active_issues()]
    assert check({"issue": issues}).issues == tuple(sorted(issues))
    topics = list(service_area_ids(_definition()))
    assert check({"service_area": topics}).service_areas == tuple(sorted(topics))


def test_validation_is_pure() -> None:
    raw: dict[str, object] = {"team_id": [TEAM, TEAM], "language": ["FA"]}
    before = {"team_id": [TEAM, TEAM], "language": ["FA"]}
    first = check(raw)
    assert raw == before
    assert check(raw) == first


# --- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(SCOPE_KEYS))
def test_scope_keys_are_refused(key: str) -> None:
    assert refused({key: "x"}) == (FilterErrorCode.SCOPE_NOT_ALLOWED, key)


@pytest.mark.parametrize("key", sorted(UNSUPPORTED_KEYS))
def test_unsupported_keys_are_refused(key: str) -> None:
    assert refused({key: "x"}) == (FilterErrorCode.UNSUPPORTED, key)


@pytest.mark.parametrize("key", ["aaa", "Team_id", "teams", "outcome", "intent", "cause", ""])
def test_unknown_keys_are_refused(key: str) -> None:
    assert refused({key: "x"}) == (FilterErrorCode.UNKNOWN_KEY, key)


def test_key_class_order() -> None:
    assert refused({"aaa": "x", "workspace_id": "y"}) == (
        FilterErrorCode.SCOPE_NOT_ALLOWED,
        "workspace_id",
    )
    assert refused({"aaa": "x", "product": "y"}) == (FilterErrorCode.UNSUPPORTED, "product")
    assert refused({"role": "x", "scope": "y", "product": "z"}) == (
        FilterErrorCode.SCOPE_NOT_ALLOWED,
        "role",
    )
    assert refused({"zzz": "x", "bbb": "y"}) == (FilterErrorCode.UNKNOWN_KEY, "bbb")
    # a key code wins over a type or value error on another key
    assert refused({"team_id": 5, "zzz": "x"}) == (FilterErrorCode.UNKNOWN_KEY, "zzz")


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ({"team_id": 5}, "team_id"),
        ({"team_id": [TEAM, 5]}, "team_id"),
        ({"language": {"fa": 1}}, "language"),
        ({"from": ["2026-10-01T00:00:00+00:00"]}, "from"),
        ({"to": 1}, "to"),
        ({"family": None}, "family"),
        ({"issue": b"complaint_refund"}, "issue"),
    ],
)
def test_bad_types(raw: dict[str, object], key: str) -> None:
    assert refused(raw) == (FilterErrorCode.BAD_TYPE, key)


def test_type_errors_come_before_value_errors_and_in_key_order() -> None:
    assert refused({"agent_user_id": "nope", "team_id": 5}) == (FilterErrorCode.BAD_TYPE, "team_id")
    assert refused({"language": 1, "family": 2}) == (FilterErrorCode.BAD_TYPE, "family")
    assert refused({"agent_user_id": "nope", "team_id": "nope"}) == (
        FilterErrorCode.BAD_VALUE,
        "agent_user_id",
    )


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ({"team_id": "not-a-uuid"}, "team_id"),
        ({"team_id": TEAM.upper()}, "team_id"),
        ({"team_id": []}, "team_id"),
        ({"team_id": ""}, "team_id"),
        ({"agent_user_id": [TEAM, ""]}, "agent_user_id"),
        ({"language": "f"}, "language"),
        ({"language": "fa_IR"}, "language"),
        ({"language": "a" * 13}, "language"),
        ({"source_type": "FILE_UPLOAD"}, "source_type"),
        ({"source_type": "upload"}, "source_type"),
        ({"family": "complaints"}, "family"),
        ({"issue": "intent_refund"}, "issue"),
        ({"service_area": "resolved"}, "service_area"),
        ({"service_area": "phone"}, "service_area"),
        ({"from": ""}, "from"),
        ({"to": ""}, "to"),
    ],
)
def test_bad_values(raw: dict[str, object], key: str) -> None:
    assert refused(raw) == (FilterErrorCode.BAD_VALUE, key)


def test_a_deprecated_issue_is_a_bad_value(tmp_path) -> None:
    import json
    from pathlib import Path

    from backend.issue_trends import load_definition_file

    released = Path(__file__).resolve().parent.parent / "issue_trends" / "definitions"
    document = json.loads((released / "trends.v1.json").read_text(encoding="utf-8"))
    for issue in document["issues"]:
        if issue["id"] == "intent_unsolicited":
            issue["status"] = "deprecated"
    path = tmp_path / "trends.v1.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    definition = load_definition_file(path)
    with pytest.raises(TrendFilterError) as caught:
        validate_filters({"issue": "intent_unsolicited"}, definition, now=NOW)
    assert (caught.value.code, caught.value.key) == (FilterErrorCode.BAD_VALUE, "issue")


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ({"from": "2026-10-01T00:00:00"}, "from"),
        ({"to": "2026-10-01"}, "to"),
        ({"from": "yesterday"}, "from"),
        ({"from": "0001-01-01T00:00:00+05:00"}, "from"),
        ({"to": "0001-01-10T00:00:00+00:00"}, "to"),
        ({"from": "2026-10-02T00:00:00+00:00", "to": "2026-10-01T00:00:00+00:00"}, None),
        ({"from": "2026-10-01T00:00:00+00:00", "to": "2026-10-01T00:00:00+00:00"}, None),
        ({"from": "2024-01-01T00:00:00+00:00", "to": "2026-01-01T00:00:00+00:00"}, None),
    ],
)
def test_window_errors(raw: dict[str, object], key: str | None) -> None:
    assert refused(raw) == (FilterErrorCode.WINDOW_INVALID, key)


def test_value_errors_come_before_window_errors() -> None:
    assert refused({"from": "2026-10-01T00:00:00", "team_id": "bad"}) == (
        FilterErrorCode.BAD_VALUE,
        "team_id",
    )


def test_a_naive_now_is_a_programming_error() -> None:
    naive = datetime(2026, 10, 23, 12, 0)
    with pytest.raises(ValueError) as caught:
        validate_filters({}, _definition(), now=naive)
    assert not isinstance(caught.value, TrendFilterError)
    # A4: checked first, even when to is given and even before a key error
    with pytest.raises(ValueError) as caught:
        validate_filters({"to": "2026-10-01T00:00:00+00:00"}, _definition(), now=naive)
    assert not isinstance(caught.value, TrendFilterError)
    with pytest.raises(ValueError) as caught:
        validate_filters({"workspace_id": "x"}, _definition(), now=naive)
    assert not isinstance(caught.value, TrendFilterError)
    with pytest.raises(ValueError):
        validate_filters({}, _definition(), now="2026-10-23")  # type: ignore[arg-type]


def test_now_in_another_timezone_is_converted() -> None:
    tehran = timezone(timedelta(hours=3, minutes=30))
    filters = validate_filters({}, _definition(), now=NOW.astimezone(tehran))
    assert filters.window.end == NOW
    assert filters.window.end.utcoffset() == timedelta(0)


# --- selectors ------------------------------------------------------------------------------


def test_no_selector_selects_everything() -> None:
    filters = check({})
    assert selected_issue_ids(filters, _definition()) == tuple(
        issue.id for issue in _definition().active_issues()
    )
    assert selected_family_ids(filters, _definition()) == (
        "call_reason",
        "unresolved_outcome",
        "service_complaint",
        "qa",
    )


def test_family_selector() -> None:
    filters = check({"family": ["unresolved_outcome", "qa"]})
    assert selected_family_ids(filters, _definition()) == ("unresolved_outcome", "qa")
    assert selected_issue_ids(filters, _definition()) == (
        "outcome_unresolved",
        "outcome_escalated",
        "outcome_follow_up_scheduled",
    )


def test_issue_and_service_area_selectors_return_no_family_rows() -> None:
    filters = check({"service_area": "refund"})
    assert selected_family_ids(filters, _definition()) == ()
    assert selected_issue_ids(filters, _definition()) == ("complaint_refund",)
    filters = check({"issue": ["outcome_escalated", "intent_unsolicited"]})
    assert selected_family_ids(filters, _definition()) == ()
    assert selected_issue_ids(filters, _definition()) == ("intent_unsolicited", "outcome_escalated")


def test_selectors_combine_by_and() -> None:
    assert (
        selected_issue_ids(
            check({"family": "call_reason", "issue": "complaint_refund"}), _definition()
        )
        == ()
    )
    assert selected_issue_ids(
        check({"family": "service_complaint", "service_area": ["refund", "invoice"]}), _definition()
    ) == ("complaint_refund", "complaint_invoice")
    assert (
        selected_issue_ids(
            check({"issue": "complaint_refund", "service_area": "invoice"}), _definition()
        )
        == ()
    )


def test_population_filters_do_not_select_rows() -> None:
    filters = check({"team_id": TEAM, "language": "fa"})
    assert selected_issue_ids(filters, _definition()) == selected_issue_ids(
        check({}), _definition()
    )
    assert len(selected_family_ids(filters, _definition())) == 4
