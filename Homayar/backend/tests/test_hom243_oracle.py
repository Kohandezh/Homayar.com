"""HOM-243 oracle: the organization audit log read API (SPEC B1-B8, D1-D9)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select, text, update

from backend.database.relational import get_db, get_session_factory
from backend.database.saas_models import AuditEvent, OrganizationRole, Workspace
from backend.database.tenant_context import current_context
from backend.security.authorization import ORGANIZATION_ROLE_PERMISSIONS, OrganizationPermission
from backend.security.context import ACCESS_COOKIE
from backend.services.admin_service import sanitize_audit_details
from backend.services.audit_event_types import AuditEventType
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_authorization_matrix import _route_policy
from backend.tests.test_organizations import Persona, _grant, _persona

AUDIT_READ = "organization:audit:read"
ORG_EVENT_TYPES = (
    "organization.member.changed",
    "organization.member.granted",
    "organization.member.revoked",
    "organization.scope.granted",
    "organization.scope.revoked",
    "organization.workspace.created",
    "organization.workspace.member_installed",
    "organization.workspace.member_removed",
)
WORKSPACE_BOUND_TYPES = (
    "organization.workspace.created",
    "organization.workspace.member_installed",
    "organization.workspace.member_removed",
)
ITEM_KEYS = {
    "id",
    "actor_user_id",
    "event_type",
    "target_type",
    "target_id",
    "outcome",
    "request_id",
    "details",
    "occurred_at",
}
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
ADMIN_AUDIT = "/api/v1/admin/audit-events"
ADMIN_TYPES = "/api/v1/admin/audit-event-types"
REDACTED = "[redacted]"


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_offset(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _events_path(org: str) -> str:
    return f"/api/v1/organizations/{org}/audit-events"


def _types_path(org: str) -> str:
    return f"/api/v1/organizations/{org}/audit-event-types"


def _code(response) -> str:
    return response.json()["error"]["code"]


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> dict:
    padded = cursor + "=" * (-len(cursor) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def _cursor_at(event_id: str) -> str:
    with inspection_session_factory()() as db:
        row = db.get(AuditEvent, event_id)
        assert row is not None
        moment = _utc(row.occurred_at)
    return _encode_cursor({"v": 1, "occurred_at": moment.isoformat(), "id": event_id})


@dataclass
class World:
    owner_a: Persona
    admin_a: Persona
    analyst_a: Persona
    executive_a: Persona
    owner_b: Persona
    org_a: str
    org_b: str
    ops: str
    sales: str
    archive: str
    field_ws: str
    seeded: dict[str, str] = field(default_factory=dict)

    @property
    def personal_a(self) -> str:
        return self.owner_a.personal_workspace_id

    @property
    def personal_b(self) -> str:
        return self.owner_b.personal_workspace_id


def _create_workspace(owner: Persona, org: str, name: str) -> str:
    created = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _world(client, email_outbox) -> World:
    owner_a = _persona(client, email_outbox, email="o243-owner-a@example.com", name="Owner A")
    admin_a = _persona(client, email_outbox, email="o243-admin-a@example.com", name="Admin A")
    analyst_a = _persona(client, email_outbox, email="o243-analyst-a@example.com", name="Ana A")
    executive_a = _persona(client, email_outbox, email="o243-exec-a@example.com", name="Exec A")
    owner_b = _persona(client, email_outbox, email="o243-owner-b@example.com", name="Owner B")
    org_a, org_b = owner_a.organization_id, owner_b.organization_id
    assert org_a != org_b
    _grant(owner_a, org_a, admin_a, "admin")
    _grant(owner_a, org_a, analyst_a, "analyst")
    _grant(owner_a, org_a, executive_a, "executive")
    ops = _create_workspace(owner_a, org_a, "Ops")
    sales = _create_workspace(owner_a, org_a, "Sales")
    archive = _create_workspace(owner_a, org_a, "Archive")
    field_ws = _create_workspace(owner_b, org_b, "Field")
    installed = owner_a.post(
        f"/api/v1/organizations/{org_a}/workspaces/{ops}/members",
        json={"user_id": analyst_a.user_id, "role": "viewer"},
    )
    assert installed.status_code == 201, installed.text
    removed = owner_a.delete(
        f"/api/v1/organizations/{org_a}/workspaces/{ops}/members/"
        f"{installed.json()['membership_id']}"
    )
    assert removed.status_code == 204, removed.text
    with inspection_session_factory()() as db:
        db.execute(update(Workspace).where(Workspace.id == archive).values(status="suspended"))
        db.commit()
    return World(
        owner_a=owner_a,
        admin_a=admin_a,
        analyst_a=analyst_a,
        executive_a=executive_a,
        owner_b=owner_b,
        org_a=org_a,
        org_b=org_b,
        ops=ops,
        sales=sales,
        archive=archive,
        field_ws=field_ws,
    )


def _insert(
    world: World,
    label: str,
    *,
    event_type: str,
    workspace_id: str | None,
    details: dict,
    at: datetime,
    actor: str | None = None,
    outcome: str = "success",
) -> str:
    with inspection_session_factory()() as db:
        row = AuditEvent(
            event_type=event_type,
            workspace_id=workspace_id,
            actor_user_id=actor,
            target_type=AuditEventType(event_type).target_type,
            target_id=f"t-{label}"[:100],
            outcome=outcome,
            request_id=f"req-{label}"[:80],
            details=details,
            occurred_at=at,
        )
        db.add(row)
        db.commit()
        world.seeded[label] = row.id
        return row.id


ALLOW_A = (
    "a.member.granted",
    "a.member.changed",
    "a.member.revoked",
    "a.scope.granted",
    "a.scope.revoked",
    "a.workspace.created@sales",
    "a.member_installed@ops",
    "a.member_removed@personal",
    "a.member_installed@archive",
    "a.secrets",
)
DENY_A = (
    "b.member.granted.same_time",
    "b.workspace.created@field",
    "forged.a@b_personal",
    "forged.a@field",
    "b@ops",
    "nonorg.settings@ops",
    "nonorg.login.null",
    "missing_org@ops",
    "null_org",
    "camel_org",
    "nested_org",
)
SECRET_DETAILS_EXTRA = {
    "password": "p" * 12,
    "note": "Bearer " + "q" * 24,
    "ip": "10.1.2.3",
    "role": "admin",
    "long": "x" * 700,
    "deep": {"a": {"b": {"c": {"d": "e"}}}},
}


def _seed(world: World) -> None:
    a, b = world.org_a, world.org_b
    owner, admin = world.owner_a.user_id, world.admin_a.user_id
    rows = (
        ("a.member.granted", "organization.member.granted", None, {}, 10, owner, "success"),
        ("a.member.changed", "organization.member.changed", None, {}, 11, admin, "success"),
        ("a.member.revoked", "organization.member.revoked", None, {}, 12, owner, "denied"),
        ("a.scope.granted", "organization.scope.granted", None, {}, 13, admin, "success"),
        ("a.scope.revoked", "organization.scope.revoked", None, {}, 14, owner, "denied"),
        (
            "a.workspace.created@sales",
            "organization.workspace.created",
            world.sales,
            {},
            15,
            owner,
            "success",
        ),
        (
            "a.member_installed@ops",
            "organization.workspace.member_installed",
            world.ops,
            {},
            16,
            admin,
            "success",
        ),
        (
            "a.member_removed@personal",
            "organization.workspace.member_removed",
            world.personal_a,
            {},
            17,
            owner,
            "failure",
        ),
        (
            "a.member_installed@archive",
            "organization.workspace.member_installed",
            world.archive,
            {},
            18,
            admin,
            "success",
        ),
        (
            "a.secrets",
            "organization.member.changed",
            None,
            SECRET_DETAILS_EXTRA,
            19,
            owner,
            "success",
        ),
    )
    for label, event_type, workspace_id, extra, minute, actor, outcome in rows:
        _insert(
            world,
            label,
            event_type=event_type,
            workspace_id=workspace_id,
            details={"organization_id": a, **extra},
            at=_at(minute),
            actor=actor,
            outcome=outcome,
        )
    owner_b = world.owner_b.user_id
    _insert(
        world,
        "b.member.granted.same_time",
        event_type="organization.member.granted",
        workspace_id=None,
        details={"organization_id": b},
        at=_at(10),
        actor=owner_b,
    )
    _insert(
        world,
        "b.workspace.created@field",
        event_type="organization.workspace.created",
        workspace_id=world.field_ws,
        details={"organization_id": b},
        at=_at(15),
        actor=owner_b,
    )
    _insert(
        world,
        "forged.a@b_personal",
        event_type="organization.workspace.created",
        workspace_id=world.personal_b,
        details={"organization_id": a},
        at=_at(20),
        actor=owner,
    )
    _insert(
        world,
        "forged.a@field",
        event_type="organization.workspace.member_installed",
        workspace_id=world.field_ws,
        details={"organization_id": a},
        at=_at(21),
        actor=admin,
    )
    _insert(
        world,
        "b@ops",
        event_type="organization.workspace.member_installed",
        workspace_id=world.ops,
        details={"organization_id": b},
        at=_at(22),
        actor=owner_b,
    )
    _insert(
        world,
        "nonorg.settings@ops",
        event_type="workspace.settings.updated",
        workspace_id=world.ops,
        details={"organization_id": a, "fields": "name"},
        at=_at(23),
        actor=owner,
    )
    _insert(
        world,
        "nonorg.login.null",
        event_type="auth.login",
        workspace_id=None,
        details={"organization_id": a, "method": "password"},
        at=_at(24),
        actor=owner,
    )
    _insert(
        world,
        "missing_org@ops",
        event_type="organization.workspace.member_removed",
        workspace_id=world.ops,
        details={},
        at=_at(25),
        actor=owner,
    )
    _insert(
        world,
        "null_org",
        event_type="organization.member.changed",
        workspace_id=None,
        details={"organization_id": None},
        at=_at(26),
        actor=owner,
    )
    _insert(
        world,
        "camel_org",
        event_type="organization.scope.granted",
        workspace_id=None,
        details={"organizationId": a},
        at=_at(27),
        actor=owner,
    )
    _insert(
        world,
        "nested_org",
        event_type="organization.scope.revoked",
        workspace_id=None,
        details={"organization": {"id": a}},
        at=_at(28),
        actor=owner,
    )


def _expected_ids(org: str, **filters) -> list[str]:
    with inspection_session_factory()() as db:
        workspaces = set(db.scalars(select(Workspace.id).where(Workspace.organization_id == org)))
        rows = list(db.scalars(select(AuditEvent)))
    chosen = []
    for row in rows:
        if row.event_type not in ORG_EVENT_TYPES:
            continue
        if not isinstance(row.details, dict) or row.details.get("organization_id") != org:
            continue
        if row.workspace_id is not None and row.workspace_id not in workspaces:
            continue
        moment = _utc(row.occurred_at)
        if "event_type" in filters and row.event_type != filters["event_type"]:
            continue
        if "outcome" in filters and row.outcome != filters["outcome"]:
            continue
        if "actor_user_id" in filters and row.actor_user_id != filters["actor_user_id"]:
            continue
        if "target_type" in filters and row.target_type != filters["target_type"]:
            continue
        if "target_id" in filters and row.target_id != filters["target_id"]:
            continue
        if "occurred_after" in filters and moment < filters["occurred_after"]:
            continue
        if "occurred_before" in filters and moment >= filters["occurred_before"]:
            continue
        chosen.append((moment, row.id))
    chosen.sort(reverse=True)
    return [event_id for _, event_id in chosen]


def _walk(persona: Persona, org: str, *, limit: int, params: dict | None = None) -> list[list]:
    pages: list[list] = []
    cursor = None
    for _ in range(500):
        query = {"limit": limit, **(params or {})}
        if cursor is not None:
            query["cursor"] = cursor
        response = persona.get(_events_path(org), params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= limit
        pages.append([item["id"] for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages
        assert body["items"], "a page with a next_cursor must not be empty"
    raise AssertionError("paging did not end")


def _types_of(world: World, ids: list[str]) -> set[str]:
    with inspection_session_factory()() as db:
        return set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.id.in_(ids))))


# --- B1 ------------------------------------------------------------------------------------


def test_b1_audit_read_belongs_to_owner_and_admin_only():
    permission = OrganizationPermission(AUDIT_READ)
    assert permission.name == "AUDIT_READ"
    assert permission in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.OWNER]
    assert permission in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.ADMIN]
    assert permission not in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.ANALYST]
    assert permission not in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.EXECUTIVE]
    records = OrganizationPermission.RECORDS_READ
    assert records in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.ANALYST]
    assert records not in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.EXECUTIVE]


# --- B2 / B7 route policy ------------------------------------------------------------------


def test_b2_b7_both_routes_declare_the_audit_read_policy(client):
    policies = {
        route.path: _route_policy(route)
        for route in client.app.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    }
    prefix = "/api/v1/organizations/{organization_id}"
    assert policies.get(f"{prefix}/records") == "organization:records:read"
    assert policies.get(f"{prefix}/audit-events") == AUDIT_READ
    assert policies.get(f"{prefix}/audit-event-types") == AUDIT_READ


# --- D5 / B1 / B2 / B7: roles ----------------------------------------------------------------


def test_d5_analyst_and_executive_are_denied_owner_and_admin_are_allowed(client, email_outbox):
    world = _world(client, email_outbox)
    org = world.org_a
    assert world.analyst_a.get(f"/api/v1/organizations/{org}/records").status_code == 200
    assert world.executive_a.get(f"/api/v1/organizations/{org}").status_code == 200
    for persona in (world.owner_a, world.admin_a):
        listed = persona.get(_events_path(org))
        assert listed.status_code == 200, listed.text
        catalog = persona.get(_types_path(org))
        assert catalog.status_code == 200, catalog.text
    for persona in (world.analyst_a, world.executive_a):
        for path in (_events_path(org), _types_path(org)):
            denied = persona.get(path)
            assert denied.status_code == 403, (path, denied.text)
            assert _code(denied) == "permission_denied"
            assert "items" not in denied.json()


# --- D4: another organization -------------------------------------------------------------


def test_d4_another_organizations_owner_gets_not_found(client, email_outbox):
    owner_a = _persona(client, email_outbox, email="o243-d4-a@example.com", name="A")
    owner_b = _persona(client, email_outbox, email="o243-d4-b@example.com", name="B")
    org_a, org_b = owner_a.organization_id, owner_b.organization_id
    foreign_reference = owner_b.get(f"/api/v1/organizations/{org_a}/members")
    assert foreign_reference.status_code == 404
    assert _code(foreign_reference) == "organization_not_found"
    for path in (_events_path(org_a), _types_path(org_a)):
        assert owner_a.get(path).status_code == 200, path
        foreign = owner_b.get(path)
        assert foreign.status_code == 404, foreign.text
        assert _code(foreign) == "organization_not_found"
    for path in (_events_path(org_b), _types_path(org_b)):
        assert owner_b.get(path).status_code == 200, path
    guessed = "00000000-0000-4000-8000-000000000000"
    for path in (_events_path(guessed), _types_path(guessed)):
        missing = owner_a.get(path)
        assert missing.status_code == 404, missing.text
        assert _code(missing) == "organization_not_found"


# --- D6: no session, API keys ---------------------------------------------------------------


def test_d6_no_session_and_api_keys_are_refused_like_other_org_routes(client, email_outbox):
    owner = _persona(client, email_outbox, email="o243-d6@example.com", name="Owner")
    org = owner.organization_id
    reference = f"/api/v1/organizations/{org}/members"
    minted = owner.post(
        "/api/v1/api-keys",
        workspace_id=owner.personal_workspace_id,
        json={"name": "oracle", "scopes": ["audit:read"], "expires_in_days": 30},
    )
    assert minted.status_code == 201, minted.text
    api_key = minted.json()["key"]
    key_header = {"Authorization": f"Bearer {api_key}"}
    token_header = {"Authorization": f"Bearer {owner.cookies[ACCESS_COOKIE]}"}
    client.cookies.clear()
    anonymous_reference = client.get(reference)
    assert anonymous_reference.status_code == 401, anonymous_reference.text
    keyed_reference = client.get(reference, headers=key_header)
    assert keyed_reference.status_code == 401, keyed_reference.text
    assert _code(keyed_reference) == "api_key_not_accepted"
    bearer_reference = client.get(reference, headers=token_header)
    assert bearer_reference.status_code == 200, bearer_reference.text
    for path in (_events_path(org), _types_path(org)):
        assert owner.get(path).status_code == 200, path
        client.cookies.clear()
        anonymous = client.get(path)
        assert anonymous.status_code == 401, anonymous.text
        assert _code(anonymous) == _code(anonymous_reference)
        keyed = client.get(path, headers=key_header)
        assert keyed.status_code == 401, keyed.text
        assert _code(keyed) == "api_key_not_accepted"
        bearer = client.get(path, headers=token_header)
        assert bearer.status_code == 200, bearer.text


# --- B4 / D1 / D2 / D3: row selection --------------------------------------------------------


def test_b4_d1_d2_d3_only_org_events_of_this_org_and_its_workspaces(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    listed = world.owner_a.get(_events_path(world.org_a), params={"limit": 100})
    ids = _ids(listed)
    assert len(ids) < 100
    assert ids == _expected_ids(world.org_a)
    for label in ALLOW_A:
        assert world.seeded[label] in ids, label
    for label in DENY_A:
        assert world.seeded[label] not in ids, label
    assert set(WORKSPACE_BOUND_TYPES) <= _types_of(world, ids)
    assert set(ORG_EVENT_TYPES) == _types_of(world, ids)
    with inspection_session_factory()() as db:
        natural = set(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.event_type.in_(WORKSPACE_BOUND_TYPES),
                    AuditEvent.workspace_id.in_([world.ops, world.sales, world.archive]),
                    AuditEvent.id.notin_(list(world.seeded.values())),
                )
            )
        )
    assert natural and natural <= set(ids)
    other = _ids(world.owner_b.get(_events_path(world.org_b), params={"limit": 100}))
    assert other == _expected_ids(world.org_b)
    assert world.seeded["b.member.granted.same_time"] in other
    assert world.seeded["b.workspace.created@field"] in other
    for label in ("b@ops", "forged.a@b_personal", "forged.a@field", *ALLOW_A):
        assert world.seeded[label] not in other, label
    assert not set(ids) & set(other)


@pytest.mark.postgres
def test_b5_postgres_rls_keeps_the_workspace_bound_types(client, email_outbox):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    world = _world(client, email_outbox)
    _seed(world)
    ids = _ids(world.owner_a.get(_events_path(world.org_a), params={"limit": 100}))
    assert ids == _expected_ids(world.org_a)
    for label in (
        "a.workspace.created@sales",
        "a.member_installed@ops",
        "a.member_removed@personal",
        "a.member_installed@archive",
    ):
        assert world.seeded[label] in ids, label
    for label in ("forged.a@b_personal", "forged.a@field", "b@ops", "nonorg.settings@ops"):
        assert world.seeded[label] not in ids, label
    assert set(WORKSPACE_BOUND_TYPES) <= _types_of(world, ids)
    walked = [i for page in _walk(world.admin_a, world.org_a, limit=3) for i in page]
    assert walked == ids


# --- B5: the request ends in the organization context ------------------------------------


def test_b5_the_request_ends_in_the_organization_context(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    seen: list[tuple] = []
    postgres = is_postgres_url(configured_database_url())

    def watched_db():
        session = get_session_factory()()
        try:
            yield session
        finally:
            settings = None
            if postgres:
                settings = session.execute(
                    text(
                        "SELECT current_setting('homayar.workspace_id', true), "
                        "current_setting('homayar.organization_id', true)"
                    )
                ).one()
            seen.append((current_context(session), settings))
            session.close()

    client.app.dependency_overrides[get_db] = watched_db
    try:
        listed = world.owner_a.get(_events_path(world.org_a), params={"limit": 100})
        catalog = world.owner_a.get(_types_path(world.org_a))
    finally:
        client.app.dependency_overrides.pop(get_db, None)
    assert listed.status_code == 200, listed.text
    assert catalog.status_code == 200, catalog.text
    assert set(WORKSPACE_BOUND_TYPES) <= _types_of(world, _ids(listed))
    assert len(seen) >= 2
    for context, settings in seen:
        assert context.workspace_id is None
        assert context.organization_id == world.org_a
        assert context.user_id == world.owner_a.user_id
        if settings is not None:
            assert settings[0] in (None, "")
            assert settings[1] == world.org_a


# --- B3: response shape, order, sanitizing ------------------------------------------------


def test_b3_response_shape_order_and_sanitized_details(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    response = world.owner_a.get(_events_path(world.org_a), params={"limit": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert all(set(item) == ITEM_KEYS for item in body["items"])
    by_id = {item["id"]: item for item in body["items"]}
    stamps = [_utc(datetime.fromisoformat(item["occurred_at"])) for item in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    secret = by_id[world.seeded["a.secrets"]]
    raw = {"organization_id": world.org_a, **SECRET_DETAILS_EXTRA}
    assert secret["details"] == sanitize_audit_details(raw)
    assert secret["details"]["organization_id"] == world.org_a
    assert secret["details"]["password"] == REDACTED
    assert secret["details"]["note"] == REDACTED
    assert secret["details"]["ip"] == REDACTED
    assert secret["details"]["role"] == "admin"
    assert secret["details"]["long"] == "x" * 500
    assert "q" * 24 not in response.text and "p" * 12 not in response.text
    revoked = by_id[world.seeded["a.member.revoked"]]
    assert revoked["event_type"] == "organization.member.revoked"
    assert revoked["actor_user_id"] == world.owner_a.user_id
    assert revoked["outcome"] == "denied"
    assert revoked["target_type"] == "organization_membership"
    assert revoked["target_id"] == "t-a.member.revoked"
    assert revoked["request_id"] == "req-a.member.revoked"
    assert _utc(datetime.fromisoformat(revoked["occurred_at"])) == _at(12)
    default_page = world.owner_a.get(_events_path(world.org_a)).json()
    assert [i["id"] for i in default_page["items"]] == _expected_ids(world.org_a)[:50]


# --- B3 / D7: filters ------------------------------------------------------------------------


def test_b3_d7_every_filter_narrows_the_org_set(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    org = world.org_a
    everything = _ids(world.owner_a.get(_events_path(org), params={"limit": 100}))
    admin_id = world.admin_a.user_id
    cases = (
        (
            {"event_type": "organization.member.granted"},
            {"event_type": "organization.member.granted"},
        ),
        ({"outcome": "denied"}, {"outcome": "denied"}),
        ({"actor_user_id": admin_id}, {"actor_user_id": admin_id}),
        ({"target_type": "workspace"}, {"target_type": "workspace"}),
        ({"target_id": "t-a.scope.granted"}, {"target_id": "t-a.scope.granted"}),
        ({"occurred_after": _iso_z(_at(13))}, {"occurred_after": _at(13)}),
        ({"occurred_after": _iso_offset(_at(13))}, {"occurred_after": _at(13)}),
        ({"occurred_before": _iso_z(_at(13))}, {"occurred_before": _at(13)}),
        (
            {"occurred_after": _iso_z(_at(12)), "occurred_before": _iso_offset(_at(16))},
            {"occurred_after": _at(12), "occurred_before": _at(16)},
        ),
        (
            {"event_type": "organization.workspace.member_installed", "actor_user_id": admin_id},
            {"event_type": "organization.workspace.member_installed", "actor_user_id": admin_id},
        ),
    )
    for params, filters in cases:
        got = _ids(world.owner_a.get(_events_path(org), params={"limit": 100, **params}))
        assert got, params
        assert got == _expected_ids(org, **filters), params
        assert set(got) <= set(everything), params
        assert len(got) < len(everything), params

    granted = _ids(
        world.owner_a.get(
            _events_path(org), params={"limit": 100, "event_type": "organization.member.granted"}
        )
    )
    assert world.seeded["a.member.granted"] in granted
    assert world.seeded["b.member.granted.same_time"] not in granted
    assert _types_of(world, granted) == {"organization.member.granted"}

    after = _ids(world.owner_a.get(_events_path(org), params={"occurred_after": _iso_z(_at(13))}))
    before = _ids(world.owner_a.get(_events_path(org), params={"occurred_before": _iso_z(_at(13))}))
    assert world.seeded["a.scope.granted"] in after
    assert world.seeded["a.scope.granted"] not in before
    assert world.seeded["a.member.revoked"] in before

    workspace_targets = _ids(
        world.owner_a.get(_events_path(org), params={"limit": 100, "target_type": "workspace"})
    )
    assert world.seeded["a.workspace.created@sales"] in workspace_targets
    assert world.seeded["nonorg.settings@ops"] not in workspace_targets

    for params in (
        {"event_type": "workspace.settings.updated"},
        {"event_type": "auth.login"},
        {"event_type": "ORGANIZATION.MEMBER.GRANTED"},
        {"actor_user_id": world.owner_b.user_id},
        {"target_id": "t-b.member.granted.same_time"},
        {"target_id": "t-nonorg.settings@ops"},
    ):
        empty = world.owner_a.get(_events_path(org), params=params)
        assert _ids(empty) == [], params
        assert empty.json()["next_cursor"] is None


# --- B3: errors and bounds -------------------------------------------------------------------


BAD_PARAMS = (
    pytest.param({"limit": "0"}, 422, id="limit-zero"),
    pytest.param({"limit": "101"}, 422, id="limit-101"),
    pytest.param({"limit": "-1"}, 422, id="limit-negative"),
    pytest.param({"limit": "many"}, 422, id="limit-text"),
    pytest.param({"cursor": "a" * 513}, 422, id="cursor-too-long"),
    pytest.param({"event_type": ""}, 422, id="event-type-empty"),
    pytest.param({"event_type": "e" * 101}, 422, id="event-type-too-long"),
    pytest.param({"outcome": "o" * 21}, 422, id="outcome-too-long"),
    pytest.param({"actor_user_id": "u" * 37}, 422, id="actor-too-long"),
    pytest.param({"target_type": "t" * 61}, 422, id="target-type-too-long"),
    pytest.param({"target_id": "i" * 101}, 422, id="target-id-too-long"),
    pytest.param({"occurred_after": "yesterday"}, 422, id="after-not-a-date"),
    pytest.param({"cursor": "not-a-cursor"}, 400, id="cursor-garbage"),
    pytest.param(
        {
            "cursor": _encode_cursor(
                {
                    "v": 2,
                    "occurred_at": "2026-03-01T09:00:00+00:00",
                    "id": "00000000-0000-4000-8000-000000000001",
                }
            )
        },
        400,
        id="cursor-version-2",
    ),
    pytest.param(
        {
            "cursor": _encode_cursor(
                {
                    "v": 1,
                    "occurred_at": "2026-03-01T09:00:00",
                    "id": "00000000-0000-4000-8000-000000000001",
                }
            )
        },
        400,
        id="cursor-naive-time",
    ),
    pytest.param(
        {
            "cursor": _encode_cursor(
                {"v": 1, "occurred_at": "2026-03-01T09:00:00+00:00", "id": "not-a-uuid"}
            )
        },
        400,
        id="cursor-bad-id",
    ),
    pytest.param(
        {"occurred_after": "2026-03-01T10:00:00Z", "occurred_before": "2026-03-01T10:00:00Z"},
        400,
        id="range-equal",
    ),
    pytest.param(
        {"occurred_after": "2026-03-01T11:00:00Z", "occurred_before": "2026-03-01T10:00:00Z"},
        400,
        id="range-inverted",
    ),
)


@pytest.mark.parametrize(("params", "status"), BAD_PARAMS)
def test_b3_errors_match_the_workspace_audit_endpoint(client, email_outbox, params, status):
    owner = _persona(client, email_outbox, email="o243-errors@example.com", name="Owner")
    org = owner.organization_id
    refused = owner.get(_events_path(org), params=params)
    reference = owner.get(ADMIN_AUDIT, workspace_id=owner.personal_workspace_id, params=params)
    assert reference.status_code == status, reference.text
    assert refused.status_code == status, refused.text
    assert _code(refused) == _code(reference)
    if status == 400:
        expected = "invalid_cursor" if "cursor" in params else "invalid_audit_range"
        assert _code(refused) == expected


def test_b3_bounds_allow_controls(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    org = world.org_a
    expected = _expected_ids(org)
    assert len(expected) > 2
    one = world.owner_a.get(_events_path(org), params={"limit": 1})
    assert _ids(one) == expected[:1] and one.json()["next_cursor"] is not None
    hundred = world.owner_a.get(_events_path(org), params={"limit": 100})
    assert _ids(hundred) == expected and hundred.json()["next_cursor"] is None
    ranged = world.owner_a.get(
        _events_path(org),
        params={
            "occurred_after": _iso_z(_at(10)),
            "occurred_before": _iso_z(_at(10) + timedelta(seconds=1)),
        },
    )
    assert _ids(ranged) == [world.seeded["a.member.granted"]]
    nexted = world.owner_a.get(
        _events_path(org), params={"limit": 1, "cursor": one.json()["next_cursor"]}
    )
    assert _ids(nexted) == expected[1:2]
    padded = one.json()["next_cursor"] + "=" * (-len(one.json()["next_cursor"]) % 4)
    assert (
        _ids(world.owner_a.get(_events_path(org), params={"limit": 1, "cursor": padded}))
        == (expected[1:2])
    )


# --- B6 / D8: paging ----------------------------------------------------------------------


@pytest.mark.parametrize("limit", (1, 2, 5, 7), ids=("limit-1", "limit-2", "limit-5", "limit-7"))
def test_b6_d8_paging_is_complete_stable_and_never_leaves_the_org(client, email_outbox, limit):
    world = _world(client, email_outbox)
    _seed(world)
    homes = (None, world.ops, world.sales, world.personal_a, world.archive)
    for step in range(10):
        _insert(
            world,
            f"page.a.{step}",
            event_type=(
                "organization.member.changed"
                if homes[step % len(homes)] is None
                else WORKSPACE_BOUND_TYPES[step % 3]
            ),
            workspace_id=homes[step % len(homes)],
            details={"organization_id": world.org_a},
            at=_at(100 + step),
        )
        _insert(
            world,
            f"page.b.{step}",
            event_type="organization.member.granted",
            workspace_id=None,
            details={"organization_id": world.org_b},
            at=_at(100 + step),
        )
    expected = _expected_ids(world.org_a)
    assert len(expected) > 20
    pages = _walk(world.owner_a, world.org_a, limit=limit)
    walked = [event_id for page in pages for event_id in page]
    assert walked == expected
    assert len(walked) == len(set(walked))
    assert all(len(page) == limit for page in pages[:-1])
    b_ids = {world.seeded[f"page.b.{step}"] for step in range(10)}
    b_ids |= {world.seeded["b.member.granted.same_time"], world.seeded["b@ops"]}
    assert not b_ids & set(walked)
    assert set(WORKSPACE_BOUND_TYPES) <= _types_of(world, walked)


def test_b6_last_page_and_cursor_format(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    org = world.org_a
    expected = _expected_ids(org)
    total = len(expected)
    exact = world.owner_a.get(_events_path(org), params={"limit": total})
    assert _ids(exact) == expected and exact.json()["next_cursor"] is None
    short = world.owner_a.get(_events_path(org), params={"limit": total - 1})
    assert _ids(short) == expected[:-1]
    cursor = short.json()["next_cursor"]
    assert cursor is not None
    payload = _decode_cursor(cursor)
    assert set(payload) == {"v", "occurred_at", "id"}
    assert payload["v"] == 1 and payload["id"] == expected[-2]
    assert datetime.fromisoformat(payload["occurred_at"]).tzinfo is not None
    last = world.owner_a.get(_events_path(org), params={"limit": total - 1, "cursor": cursor})
    assert _ids(last) == expected[-1:] and last.json()["next_cursor"] is None


def test_b6_equal_times_across_workspaces_are_ordered_by_id(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    same = _at(300)
    homes = (
        (None, "organization.member.changed"),
        (world.ops, "organization.workspace.member_installed"),
        (world.sales, "organization.workspace.created"),
        (world.personal_a, "organization.workspace.member_removed"),
        (world.archive, "organization.workspace.member_installed"),
        (None, "organization.scope.granted"),
    )
    tied = [
        _insert(
            world,
            f"tie.a.{index}",
            event_type=event_type,
            workspace_id=home,
            details={"organization_id": world.org_a},
            at=same,
        )
        for index, (home, event_type) in enumerate(homes)
    ]
    foreign = _insert(
        world,
        "tie.b",
        event_type="organization.member.granted",
        workspace_id=None,
        details={"organization_id": world.org_b},
        at=same,
    )
    expected = _expected_ids(world.org_a)
    assert [event_id for event_id in expected if event_id in tied] == sorted(tied, reverse=True)
    for limit in (1, 2, 4):
        walked = [i for page in _walk(world.owner_a, world.org_a, limit=limit) for i in page]
        assert walked == expected, limit
        assert foreign not in walked
    middle = sorted(tied, reverse=True)[2]
    after = world.owner_a.get(
        _events_path(world.org_a), params={"limit": 100, "cursor": _cursor_at(middle)}
    )
    assert _ids(after) == expected[expected.index(middle) + 1 :]


def test_b6_d8_filtered_paging_with_a_cursor_in_the_workspace_format(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    org = world.org_a
    for step in range(6):
        _insert(
            world,
            f"granted.a.{step}",
            event_type="organization.member.granted",
            workspace_id=None,
            details={"organization_id": org},
            at=_at(200 + step),
        )
        _insert(
            world,
            f"granted.b.{step}",
            event_type="organization.member.granted",
            workspace_id=None,
            details={"organization_id": world.org_b},
            at=_at(200 + step),
        )
    params = {"event_type": "organization.member.granted"}
    expected = _expected_ids(org, event_type="organization.member.granted")
    walked = [i for page in _walk(world.owner_a, org, limit=2, params=params) for i in page]
    assert walked == expected
    foreign = {world.seeded[f"granted.b.{step}"] for step in range(6)}
    assert not foreign & set(walked)
    pivot = world.seeded["granted.a.3"]
    rest = world.owner_a.get(
        _events_path(org), params={"limit": 100, "cursor": _cursor_at(pivot), **params}
    )
    assert _ids(rest) == expected[expected.index(pivot) + 1 :]
    foreign_pivot = world.seeded["granted.b.3"]
    from_foreign = world.owner_a.get(
        _events_path(org), params={"limit": 100, "cursor": _cursor_at(foreign_pivot), **params}
    )
    assert not foreign & set(_ids(from_foreign))


# --- B7: the org event type catalog -------------------------------------------------------


def test_b7_the_catalog_lists_the_eight_org_types_with_group_and_target(client, email_outbox):
    world = _world(client, email_outbox)
    expected = [
        {
            "event_type": member.value,
            "group": member.group,
            "target_type": member.target_type,
        }
        for member in sorted(AuditEventType, key=lambda member: member.value)
        if member.value in ORG_EVENT_TYPES
    ]
    assert [item["event_type"] for item in expected] == sorted(ORG_EVENT_TYPES)
    assert {
        member.value for member in AuditEventType if member.value.startswith("organization.")
    } == set(ORG_EVENT_TYPES)
    for persona in (world.owner_a, world.admin_a):
        response = persona.get(_types_path(world.org_a))
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items"}
        assert all(set(item) == {"event_type", "group", "target_type"} for item in body["items"])
        assert body["items"] == expected
    assert world.owner_b.get(_types_path(world.org_b)).json()["items"] == expected


# --- B8 / D9: the workspace audit endpoints are unchanged ---------------------------------


def test_b8_d9_workspace_audit_endpoints_keep_their_behaviour(client, email_outbox):
    world = _world(client, email_outbox)
    _seed(world)
    listed = world.owner_a.get(ADMIN_AUDIT, workspace_id=world.ops, params={"limit": 100})
    ids = set(_ids(listed))
    with inspection_session_factory()() as db:
        own = set(db.scalars(select(AuditEvent.id).where(AuditEvent.workspace_id == world.ops)))
    assert ids == own
    for label in ("a.member_installed@ops", "nonorg.settings@ops", "b@ops", "missing_org@ops"):
        assert world.seeded[label] in ids, label
    for label in ("a.member.granted", "a.member.changed", "a.scope.granted", "a.secrets"):
        assert world.seeded[label] not in ids, label
    assert world.seeded["a.workspace.created@sales"] not in ids
    catalog = world.owner_a.get(ADMIN_TYPES, workspace_id=world.ops)
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["items"] == [
        {"event_type": member.value, "group": member.group, "target_type": member.target_type}
        for member in sorted(AuditEventType, key=lambda member: member.value)
    ]
