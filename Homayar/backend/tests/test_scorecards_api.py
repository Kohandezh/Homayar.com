"""HOM-54: the scorecard routes under ``/api/v1/scorecards`` (SPEC sections 5 and 7).

Each route with its deny case and the closest allow case: the shapes, the key and body
rules, the version conflict, the codes of an invalid definition, the audit event of a
publish (and none for a refused one), workspace isolation, and the role and API-key
matrix.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import AuditEvent, ScorecardVersion
from backend.scorecards import SCORECARDS_VERSION, definition_digest, validate_definition
from backend.security.authorization import API_KEY_SCOPES
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _cookies, _Persona, _workspace_with_roles

BASE = "/api/v1/scorecards"
KEY = "inbound_sales"
ITEM_KEYS = {
    "scorecard_key",
    "version",
    "name",
    "digest",
    "scorecards_version",
    "created_at",
    "created_by_user_id",
}


def _definition(name: str = "Inbound sales", threshold: int = 80) -> dict:
    return {
        "name": name,
        "pass_threshold": threshold,
        "criteria": [
            {
                "criterion_id": "greeting",
                "label": "Greets the customer",
                "weight": 60,
                "check": {
                    "check_id": "greeting",
                    "version": 1,
                    "kind": "required_phrase",
                    "phrases": ["hello"],
                },
            },
            {
                "criterion_id": "disclosure",
                "label": "Says the call is recorded",
                "weight": 40,
                "auto_fail": True,
                "check": {
                    "check_id": "disclosure",
                    "version": 1,
                    "kind": "required_phrase",
                    "phrases": ["this call is recorded"],
                },
            },
        ],
    }


def _body(expected: int = 0, reason: str = "first publish", **extra) -> dict:
    return {"definition": _definition(), "reason": reason, "expected_version": expected, **extra}


def _code(response) -> str:
    return response.json()["error"]["code"]


def _rows() -> list[tuple]:
    with inspection_session_factory()() as db:
        return [
            tuple(row)
            for row in db.execute(
                select(
                    ScorecardVersion.workspace_id,
                    ScorecardVersion.scorecard_key,
                    ScorecardVersion.version,
                ).order_by(
                    ScorecardVersion.workspace_id,
                    ScorecardVersion.scorecard_key,
                    ScorecardVersion.version,
                )
            ).all()
        ]


def _audits() -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == "scorecard.published")
                .order_by(AuditEvent.occurred_at)
            )
        )


@pytest.fixture
def team(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    return {"owner": owner, **people}


# --- publish and read --------------------------------------------------------------------


def test_the_owner_publishes_and_everyone_reads_the_versions(team):
    owner = team["owner"]
    created = owner.post(f"{BASE}/{KEY}/versions", json=_body(reason="  first publish  "))
    assert created.status_code == 201, created.text
    one = created.json()
    assert set(one) == ITEM_KEYS | {"reason", "definition"}
    assert one["scorecard_key"] == KEY and one["version"] == 1
    assert one["name"] == "Inbound sales"
    assert one["scorecards_version"] == SCORECARDS_VERSION
    assert one["digest"] == definition_digest(_definition())
    assert one["definition"] == validate_definition(_definition()).model_dump(mode="json")
    assert one["reason"] == "first publish"  # stripped
    assert one["created_by_user_id"] == owner.user_id
    assert one["created_at"]

    second = owner.post(
        f"{BASE}/{KEY}/versions",
        json={**_body(expected=1, reason="stricter"), "definition": _definition(threshold=90)},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    other = owner.post(f"{BASE}/outbound_sales/versions", json=_body(reason="outbound"))
    assert other.status_code == 201, other.text

    for reader in (owner, team["admin"], team["member"], team["viewer"]):
        listed = reader.get(BASE)
        assert listed.status_code == 200, listed.text
        assert set(listed.json()) == {"items"}
        items = listed.json()["items"]
        assert [(item["scorecard_key"], item["version"]) for item in items] == [
            (KEY, 2),
            ("outbound_sales", 1),
        ]
        assert all(set(item) == ITEM_KEYS for item in items)

        history = reader.get(f"{BASE}/{KEY}/versions")
        assert history.status_code == 200, history.text
        assert set(history.json()) == {"items"}
        assert [(item["version"], item["reason"]) for item in history.json()["items"]] == [
            (2, "stricter"),
            (1, "first publish"),
        ]
        assert all(set(item) == ITEM_KEYS | {"reason"} for item in history.json()["items"])

        shown = reader.get(f"{BASE}/{KEY}/versions/1")
        assert shown.status_code == 200, shown.text
        assert shown.json() == one


def test_unknown_keys_and_versions_are_404(team):
    owner = team["owner"]
    missing = owner.get(f"{BASE}/{KEY}/versions")
    assert missing.status_code == 404 and _code(missing) == "scorecard_not_found"
    assert owner.get(BASE).json() == {"items": []}
    assert owner.post(f"{BASE}/{KEY}/versions", json=_body()).status_code == 201
    for version in (0, 2, -1, 2**40):
        absent = owner.get(f"{BASE}/{KEY}/versions/{version}")
        assert absent.status_code == 404 and _code(absent) == "scorecard_version_not_found"
    other = owner.get(f"{BASE}/outbound_sales/versions/1")
    assert other.status_code == 404 and _code(other) == "scorecard_version_not_found"
    assert owner.get(f"{BASE}/{KEY}/versions/one").status_code == 422


# --- the rules of a publish --------------------------------------------------------------


def test_a_stale_expected_version_is_409_and_writes_nothing(team):
    owner = team["owner"]
    early = owner.post(f"{BASE}/{KEY}/versions", json=_body(expected=1))
    assert early.status_code == 409, early.text
    assert _code(early) == "scorecard_version_conflict"
    assert early.json()["error"]["details"] == {"current_version": 0}
    assert owner.post(f"{BASE}/{KEY}/versions", json=_body()).status_code == 201
    stale = owner.post(f"{BASE}/{KEY}/versions", json=_body(expected=0))
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"current_version": 1}
    assert [row[2] for row in _rows()] == [1]
    assert len(_audits()) == 1


@pytest.mark.parametrize(
    ("definition", "code"),
    [
        ("not a definition", "scorecard_invalid"),
        ([], "scorecard_invalid"),
        ({**_definition(), "owner": "x"}, "scorecard_invalid"),
        ({**_definition(), "pass_threshold": 0}, "scorecard_invalid"),
        ({**_definition(), "criteria": _definition()["criteria"][:1]}, "weights_invalid"),
        (
            {
                **_definition(),
                "criteria": [
                    _definition()["criteria"][0],
                    {**_definition()["criteria"][1], "auto_fail": False, "weight": 0},
                    {
                        **_definition()["criteria"][1],
                        "criterion_id": "x2",
                        "check": {**_definition()["criteria"][1]["check"], "check_id": "x2"},
                    },
                ],
            },
            "weight_zero_not_auto_fail",
        ),
        (
            {**_definition(), "criteria": [_definition()["criteria"][0]] * 2},
            "duplicate_criterion",
        ),
        (
            {
                **_definition(),
                "criteria": [
                    {**_definition()["criteria"][0], "check": {"kind": "sms"}},
                    _definition()["criteria"][1],
                ],
            },
            "check_invalid",
        ),
    ],
)
def test_an_invalid_definition_is_422_with_its_code_and_writes_nothing(team, definition, code):
    owner = team["owner"]
    refused = owner.post(f"{BASE}/{KEY}/versions", json={**_body(), "definition": definition})
    assert refused.status_code == 422, refused.text
    assert _code(refused) == code
    assert set(refused.json()["error"]) == {"code", "message", "request_id"}
    checked = owner.post(f"{BASE}/validate", json={"definition": definition})
    assert checked.status_code == 422 and _code(checked) == code
    assert _rows() == [] and _audits() == []


@pytest.mark.parametrize(
    "key",
    ["Inbound", "1nbound", "a", "in-bound", "inbound.sales", "a" * 65, "inbound_sales%0A"],
)
def test_a_key_that_breaks_the_pattern_is_422_on_every_route(team, key):
    owner = team["owner"]
    for response in (
        owner.get(f"{BASE}/{key}/versions"),
        owner.get(f"{BASE}/{key}/versions/1"),
        owner.post(f"{BASE}/{key}/versions", json=_body()),
    ):
        assert response.status_code == 422, (key, response.text)
        assert _code(response) == "scorecard_key_invalid"
    assert _rows() == []
    allowed = owner.post(f"{BASE}/ab/versions", json=_body())  # two characters
    assert allowed.status_code == 201, allowed.text


@pytest.mark.parametrize(
    "change",
    [
        {"reason": ""},
        {"reason": "   "},
        {"reason": "a" * 501},
        {"reason": 5},
        {"expected_version": -1},
        {"expected_version": "0"},
        {"expected_version": True},
        {"expected_version": 0.0},
        {"expected_version": None},
        {"owner": "x"},
    ],
)
def test_a_bad_reason_or_expected_version_is_422(team, change):
    owner = team["owner"]
    refused = owner.post(f"{BASE}/{KEY}/versions", json={**_body(), **change})
    assert refused.status_code == 422, refused.text
    assert _code(refused) == "validation_error"
    for missing in ("definition", "reason", "expected_version"):
        body = _body()
        del body[missing]
        assert owner.post(f"{BASE}/{KEY}/versions", json=body).status_code == 422
    assert _rows() == []
    longest = owner.post(f"{BASE}/{KEY}/versions", json=_body(reason="a" * 500))
    assert longest.status_code == 201, longest.text


def test_validate_returns_the_normalized_definition_and_stores_nothing(team):
    owner = team["owner"]
    checked = owner.post(f"{BASE}/validate", json={"definition": _definition()})
    assert checked.status_code == 200, checked.text
    assert checked.json() == {
        "valid": True,
        "digest": definition_digest(_definition()),
        "definition": validate_definition(_definition()).model_dump(mode="json"),
    }
    assert checked.json()["definition"]["description"] is None  # defaults filled in
    assert _rows() == [] and _audits() == []
    extra = owner.post(f"{BASE}/validate", json={"definition": _definition(), "reason": "x"})
    assert extra.status_code == 422


# --- audit -------------------------------------------------------------------------------


def test_a_publish_writes_one_audit_event_and_a_refused_one_writes_none(team):
    owner = team["owner"]
    created = owner.post(f"{BASE}/{KEY}/versions", json=_body(reason="pilot scorecard"))
    assert created.status_code == 201
    [event] = _audits()
    assert event.workspace_id == owner.workspace_id
    assert event.actor_user_id == owner.user_id
    assert (event.target_type, event.target_id, event.outcome) == ("scorecard", KEY, "success")
    assert event.details == {
        "scorecard_key": KEY,
        "version": 1,
        "digest": created.json()["digest"],
        "reason": "pilot scorecard",
    }
    # Refused: a conflict, an invalid definition, a bad key, a viewer.
    owner.post(f"{BASE}/{KEY}/versions", json=_body(expected=0))
    owner.post(f"{BASE}/{KEY}/versions", json={**_body(expected=1), "definition": {}})
    owner.post(f"{BASE}/Bad/versions", json=_body(expected=1))
    team["viewer"].post(f"{BASE}/{KEY}/versions", json=_body(expected=1))
    assert len(_audits()) == 1
    admin = team["admin"].post(f"{BASE}/{KEY}/versions", json=_body(expected=1, reason="r2"))
    assert admin.status_code == 201
    assert [(e.actor_user_id, e.details["version"]) for e in _audits()] == [
        (owner.user_id, 1),
        (team["admin"].user_id, 2),
    ]


# --- workspace isolation -----------------------------------------------------------------


def test_a_key_of_one_workspace_is_invisible_from_another(client, email_outbox, team):
    owner = team["owner"]
    assert owner.post(f"{BASE}/{KEY}/versions", json=_body()).status_code == 201
    credentials = register_user(client, email_outbox, email="scorecard-other@example.com")
    session = login_user(client, credentials)
    stranger = _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))

    assert stranger.get(BASE).json() == {"items": []}
    assert _code(stranger.get(f"{BASE}/{KEY}/versions")) == "scorecard_not_found"
    assert _code(stranger.get(f"{BASE}/{KEY}/versions/1")) == "scorecard_version_not_found"
    # Its own version 1 of the same key, from expected_version 0.
    own = stranger.post(f"{BASE}/{KEY}/versions", json=_body(reason="mine"))
    assert own.status_code == 201, own.text
    assert own.json()["version"] == 1
    assert [item["version"] for item in owner.get(f"{BASE}/{KEY}/versions").json()["items"]] == [1]
    assert owner.get(f"{BASE}/{KEY}/versions/1").json()["reason"] == "first publish"
    # The stranger cannot reach the other workspace by naming it.
    foreign = client.get(
        BASE, cookies=stranger.cookies, headers={"X-Workspace-ID": owner.workspace_id}
    )
    assert foreign.status_code in (403, 404)
    assert len(_rows()) == 2


# --- roles and API keys ------------------------------------------------------------------


def test_member_and_viewer_read_but_cannot_publish_admin_and_owner_can(team):
    for name in ("member", "viewer"):
        persona = team[name]
        assert persona.get(BASE).status_code == 200
        for response in (
            persona.post(f"{BASE}/{KEY}/versions", json=_body()),
            persona.post(f"{BASE}/validate", json={"definition": _definition()}),
        ):
            assert response.status_code == 403, (name, response.text)
            assert _code(response) == "permission_denied"
    assert _rows() == []
    assert team["admin"].post(f"{BASE}/{KEY}/versions", json=_body()).status_code == 201
    assert team["owner"].post(f"{BASE}/{KEY}/versions", json=_body(expected=1)).status_code == 201
    assert (
        team["admin"].post(f"{BASE}/validate", json={"definition": _definition()}).status_code
        == 200
    )
    for name in ("member", "viewer"):
        assert team[name].get(f"{BASE}/{KEY}/versions").status_code == 200
        assert team[name].get(f"{BASE}/{KEY}/versions/2").status_code == 200


def test_a_publish_needs_the_csrf_token(client, team):
    owner = team["owner"]
    for path, body in (
        (f"{BASE}/{KEY}/versions", _body()),
        (f"{BASE}/validate", {"definition": _definition()}),
    ):
        refused = client.post(
            path, json=body, cookies=owner.cookies, headers={"X-Workspace-ID": owner.workspace_id}
        )
        assert refused.status_code == 403 and _code(refused) == "csrf_failed"
    assert _rows() == []


def test_an_api_key_gets_403_on_every_route(client, team):
    owner = team["owner"]
    assert owner.post(f"{BASE}/{KEY}/versions", json=_body()).status_code == 201
    every_scope = sorted(permission.value for permission in API_KEY_SCOPES)
    key = _mint(client, owner.headers, owner.cookies, name="all", scopes=every_scope)
    assert key.status_code == 201, key.text
    headers = _bearer(key.json()["key"], **{"X-Workspace-ID": owner.workspace_id})
    for response in (
        client.get(BASE, headers=headers),
        client.get(f"{BASE}/{KEY}/versions", headers=headers),
        client.get(f"{BASE}/{KEY}/versions/1", headers=headers),
        client.post(f"{BASE}/{KEY}/versions", json=_body(expected=1), headers=headers),
        client.post(f"{BASE}/validate", json={"definition": _definition()}, headers=headers),
    ):
        assert response.status_code == 403, response.text
        assert _code(response) == "api_key_scope_denied"
    # No key can be given the scorecard permissions.
    for scope in ("scorecard:read", "scorecard:manage"):
        refused = _mint(client, owner.headers, owner.cookies, name=scope, scopes=[scope])
        assert refused.status_code == 422, refused.text
    assert [row[2] for row in _rows()] == [1]
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ScorecardVersion)) == 1
