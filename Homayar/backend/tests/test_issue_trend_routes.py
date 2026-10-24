"""HOM-80 (WP15-T02): ``GET /api/v1/trends/issues``.

``meeting:read`` plus the caller's record scope (``RecordAccess.meeting_clause``), like
``GET /voicemails/analytics``.  Every query parameter except ``grain`` and ``timezone`` goes
to the HOM-79 filter validator; each refusal is a 422 with ``error.code`` and
``error.details.key``.  Scope is never a filter: a team outside the caller's scope only
narrows the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event, select

from backend.database.relational import get_engine
from backend.database.saas_models import Workspace
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _Persona, _workspace_with_roles
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trend_service import (
    ANSWER_KEYS,
    BASE,
    BILLING,
    DAY,
    WINDOW,
    excluded,
    included,
    row,
    seed,
)

TRENDS = "/api/v1/trends/issues"


def _place(owner: _Persona) -> SimpleNamespace:
    """What ``seed`` needs about the owner's workspace."""
    with inspection_session_factory()() as db:
        organization_id = db.scalar(
            select(Workspace.organization_id).where(Workspace.id == owner.workspace_id)
        )
    return SimpleNamespace(
        workspace_id=owner.workspace_id, organization_id=organization_id, owner_id=owner.user_id
    )


def _get(persona: _Persona, *params: tuple[str, str], window: bool = True):
    query = [*(WINDOW.items() if window else ()), *params]
    return persona.get(TRENDS, params=query)


def _refusal(response) -> tuple[str, str | None]:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    return error["code"], error["details"]["key"]


def _people(client, email_outbox, monkeypatch, **roles: str):
    owner, people, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles=roles)
    return owner, people


# --- deny-cases 6 and 7: session and membership ---------------------------------------------


def test_no_session_is_401_and_a_viewer_reads(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, viewer="viewer")
    seed(_place(owner))

    allowed = _get(people["viewer"])
    assert allowed.status_code == 200, allowed.text
    assert included(allowed.json()) == 1

    client.cookies.clear()
    anonymous = client.get(TRENDS, params=WINDOW, headers={"X-Workspace-ID": owner.workspace_id})
    assert anonymous.status_code == 401, anonymous.text


def test_a_user_without_a_membership_is_refused(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")
    seed(_place(owner))
    session = login_user(
        client, register_user(client, email_outbox, email="trend-outsider@example.com")
    )
    outsider = _Persona(client, owner.workspace_id, session, dict(client.cookies))

    denied = _get(outsider)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "workspace_access_denied"
    assert "included" not in denied.text
    # allow-control: a member of the workspace reads it
    assert _get(people["member"]).status_code == 200


def test_api_keys_need_meeting_read_and_stay_in_their_workspace(client, email_outbox):
    mine = Tenant(client, email_outbox, "trend-key-mine@example.com")
    other = Tenant(client, email_outbox, "trend-key-other@example.com")
    seed(mine)
    seed(other)
    seed(other)
    reader = _mint(client, mine.headers, mine.cookies, scopes=["meeting:read"])
    writer = _mint(client, mine.headers, mine.cookies, scopes=["meeting:create"])
    assert reader.status_code == 201, reader.text
    assert writer.status_code == 201, writer.text
    client.cookies.clear()  # the key alone authenticates

    allowed = client.get(TRENDS, params=WINDOW, headers=_bearer(reader.json()["key"]))
    assert allowed.status_code == 200, allowed.text
    assert included(allowed.json()) == 1  # only the key's workspace
    assert allowed.json()["scope"] == {"restricted": False}
    pinned = client.get(
        TRENDS,
        params=WINDOW,
        headers=_bearer(reader.json()["key"], **{"X-Workspace-ID": mine.workspace_id}),
    )
    assert pinned.status_code == 200 and included(pinned.json()) == 1

    no_scope = client.get(
        TRENDS,
        params=WINDOW,
        headers=_bearer(writer.json()["key"], **{"X-Workspace-ID": mine.workspace_id}),
    )
    assert no_scope.status_code == 403, no_scope.text
    assert no_scope.json()["error"]["code"] == "api_key_scope_denied"

    foreign = client.get(
        TRENDS,
        params=WINDOW,
        headers=_bearer(reader.json()["key"], **{"X-Workspace-ID": other.workspace_id}),
    )
    assert foreign.status_code == 403, foreign.text
    assert foreign.json()["error"]["code"] == "workspace_access_denied"
    assert "included" not in foreign.text and "included" not in no_scope.text


# --- deny-cases 4 and 5: filters ------------------------------------------------------------


def test_a_scope_key_in_the_query_is_refused(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")

    refused = _get(people["member"], ("workspace_id", owner.workspace_id))
    assert _refusal(refused) == ("filter_scope_not_allowed", "workspace_id")
    # allow-control: the same request without it
    assert _get(people["member"]).status_code == 200


# (query params, (error.code, error.details.key)); one setup serves every case
FILTER_REFUSALS = [
    ((("product", "x"),), ("filter_unsupported", "product")),
    ((("foo", "1"),), ("filter_unknown_key", "foo")),
    ((("aaa", "x"), ("workspace_id", "y")), ("filter_scope_not_allowed", "workspace_id")),
    (
        (("from", "2026-01-01T00:00:00Z"), ("from", "2026-01-02T00:00:00Z")),
        ("filter_bad_type", "from"),
    ),
    ((("team_id", "not-a-uuid"),), ("filter_bad_value", "team_id")),
    ((("team_id", ""),), ("filter_bad_value", "team_id")),
    ((("service_area", "nope"),), ("filter_bad_value", "service_area")),
]


def test_filter_refusals_are_422_with_their_key(client, email_outbox, monkeypatch):
    _owner, people = _people(client, email_outbox, monkeypatch, member="member")
    for params, expected in FILTER_REFUSALS:
        assert _refusal(_get(people["member"], *params)) == expected, params


def test_window_refusals_name_the_end_at_fault(client, email_outbox, monkeypatch):
    _owner, people = _people(client, email_outbox, monkeypatch, member="member")
    member = people["member"]

    naive = _get(
        member, ("from", "2026-01-01T00:00:00"), ("to", "2026-01-02T00:00:00Z"), window=False
    )
    assert _refusal(naive) == ("filter_window_invalid", "from")
    reversed_window = _get(
        member, ("from", "2026-01-02T00:00:00Z"), ("to", "2026-01-01T00:00:00Z"), window=False
    )
    assert _refusal(reversed_window) == ("filter_window_invalid", None)
    default = _get(member, window=False)
    assert default.status_code == 200, default.text
    window = default.json()["window"]
    span = datetime.fromisoformat(window["to"]) - datetime.fromisoformat(window["from"])
    assert span == timedelta(days=30)
    assert window["to"].endswith("+00:00")


def test_a_service_area_is_a_selector(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")
    seed(_place(owner))

    answer = _get(people["member"], ("service_area", "refund"))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert [r["issue"] for r in body["rows"]] == ["complaint_refund"]
    assert body["filters"]["service_area"] == ["refund"]


def test_repeated_list_keys_are_lists(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")
    seed(_place(owner), language="fa")
    seed(_place(owner), language="en")

    both = _get(people["member"], ("language", "fa"), ("language", "EN"))
    assert both.status_code == 200, both.text
    assert both.json()["filters"]["language"] == ["en", "fa"]
    assert included(both.json()) == 2


# --- deny-case 12: series -------------------------------------------------------------------


SERIES_REFUSALS = [
    ((("grain", "hour"),), ("trend_grain_invalid", "grain")),
    ((("grain", "day"), ("grain", "day")), ("trend_grain_invalid", "grain")),
    ((("grain", ""),), ("trend_grain_invalid", "grain")),
    ((("timezone", "Mars/Base"),), ("trend_timezone_invalid", "timezone")),
    ((("timezone", "utc"),), ("trend_timezone_invalid", "timezone")),
    ((("timezone", "asia/tehran"),), ("trend_timezone_invalid", "timezone")),
    ((("timezone", "UTC"), ("timezone", "UTC")), ("trend_timezone_invalid", "timezone")),
    # A2: filter errors first, then grain, then timezone
    ((("workspace_id", "x"), ("grain", "hour")), ("filter_scope_not_allowed", "workspace_id")),
    ((("foo", "1"), ("timezone", "Mars/Base")), ("filter_unknown_key", "foo")),
    ((("timezone", "Mars/Base"), ("grain", "hour")), ("trend_grain_invalid", "grain")),
    (
        (("timezone", "Mars/Base"), ("grain", "day"), ("grain", "week")),
        ("trend_grain_invalid", "grain"),
    ),
]


def test_series_refusals(client, email_outbox, monkeypatch):
    _owner, people = _people(client, email_outbox, monkeypatch, member="member")
    for params, expected in SERIES_REFUSALS:
        assert _refusal(_get(people["member"], *params)) == expected, params


def test_a_weekly_utc_series_is_answered(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")
    seed(_place(owner))

    answer = _get(people["member"], ("grain", "week"), ("timezone", "UTC"))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == ANSWER_KEYS | {"coverage", "scorecard", "missing_data", "notices"}
    assert body["notices"] == ["correlation_not_cause"]  # HOM-83 (A1)
    assert body["series"] == {
        "grain": "week",
        "timezone": "UTC",
        "calendar": "gregorian",
        "week_starts_on": "saturday",
    }
    assert [p["from"] for p in body["points"]] == [WINDOW["from"]]
    assert body["points"][0]["partial"] is True
    assert body["points"][0]["population"] == body["population"]
    assert body["points"][0]["rows"] == body["rows"]


def test_the_default_series_is_none_in_tehran(client, email_outbox, monkeypatch):
    _owner, people = _people(client, email_outbox, monkeypatch, member="member")
    body = _get(people["member"]).json()
    assert body["series"]["grain"] == "none" and body["series"]["timezone"] == "Asia/Tehran"
    assert body["points"] == []
    assert body["scope"] == {"restricted": False}


# --- deny-cases 2, 3 and 9 through the route: record scope -----------------------------------


def _team_scoped(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, sup="member", lone="member")
    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text
    team_a = owner.post("/api/v1/teams", json={"name": "Team A"}).json()["id"]
    team_b = owner.post("/api/v1/teams", json={"name": "Team B"}).json()["id"]
    assigned = owner.post(
        f"/api/v1/teams/{team_a}/assignments",
        json={"user_id": people["sup"].user_id, "role": "supervisor"},
    )
    assert assigned.status_code == 201, assigned.text
    return owner, people, team_a, team_b


def test_a_team_scoped_member_sees_only_their_conversations(client, email_outbox, monkeypatch):
    owner, people, team_a, team_b = _team_scoped(client, email_outbox, monkeypatch)
    sup, lone = people["sup"], people["lone"]
    place = _place(owner)
    seed(place, team_id=team_a)  # supervised team
    seed(place, team_id=team_b, agent_user_id=sup.user_id)  # their own call as agent
    seed(place, team_id=team_b)  # another team, not theirs
    seed(place, created_by=lone.user_id)  # someone else's upload

    sup_body = _get(sup).json()
    assert included(sup_body) == 2 and sup_body["scope"] == {"restricted": True}
    assert included(_get(lone).json()) == 1
    # allow-control: the owner sees all four
    assert included(_get(owner).json()) == 4

    # deny-case 3: a team outside the scope only narrows, never 403
    narrowed = _get(sup, ("team_id", team_b))
    assert narrowed.status_code == 200, narrowed.text
    assert included(narrowed.json()) == 1  # only their own agent call of team B
    foreign_only = _get(lone, ("team_id", team_b))
    assert foreign_only.status_code == 200 and included(foreign_only.json()) == 0
    assert included(_get(owner, ("team_id", team_b)).json()) == 2


def test_a_hidden_counted_copy_shows_only_as_an_exclusion(client, email_outbox, monkeypatch):
    owner, people, team_a, team_b = _team_scoped(client, email_outbox, monkeypatch)
    place = _place(owner)
    hidden = seed(place, team_id=team_b, extraction=BILLING)
    visible = seed(
        place,
        received_at=BASE + timedelta(hours=1),
        team_id=team_a,
        duplicate_of=hidden.ingestion_id,
    )

    scoped = _get(people["sup"])
    assert scoped.status_code == 200, scoped.text
    body = scoped.json()
    assert included(body) == 0 and excluded(body) == 1
    for identifier in (*hidden, *visible, team_b):
        assert identifier not in scoped.text
    full = _get(owner).json()
    assert included(full) == 1 and excluded(full) == 1
    assert row(full, family="call_reason", issue="intent_resolve_billing")["cell"]["numerator"] == 1


# --- cost -----------------------------------------------------------------------------------


def test_the_route_statement_count_does_not_grow(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")
    place = _place(owner)
    small_day, large_day = BASE, BASE + 2 * DAY
    for _ in range(2):
        seed(place, received_at=small_day)
    for hour in range(12):
        seed(place, received_at=large_day + timedelta(hours=hour))

    def statements(day: datetime, *params: tuple[str, str]) -> tuple[int, dict[str, Any]]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = people["member"].get(
                TRENDS,
                params=[
                    ("from", day.isoformat()),
                    ("to", (day + DAY).isoformat()),
                    *params,
                ],
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return len(seen), response.json()

    small_count, small = statements(small_day)
    large_count, large = statements(large_day)
    day_count, _ = statements(large_day, ("grain", "day"))
    assert included(small) == 2 and included(large) == 12
    assert small_count == large_count == day_count


def test_the_route_answers_now_by_default(client, email_outbox, monkeypatch):
    owner, people = _people(client, email_outbox, monkeypatch, member="member")
    seed(_place(owner), received_at=datetime.now(UTC) - timedelta(hours=1))
    body = _get(people["member"], window=False).json()
    assert included(body) == 1
