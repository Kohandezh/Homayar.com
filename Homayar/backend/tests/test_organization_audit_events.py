"""HOM-243: the organization audit log read API.

An organization owner or admin reads the organization's audit events: the
eight ``organization.*`` event types whose ``details.organization_id`` names
the organization, whether they were stored with no workspace (member and
scope changes) or bound to one of the organization's workspaces (workspace
creation and delegated installations). Row selection holds under PostgreSQL
row-level security: the NULL-workspace rows are read under the organization
context and each workspace's rows under that workspace bound in turn, and
the request ends back in the organization context with no workspace bound.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    Organization,
    OrganizationRole,
    Workspace,
)
from backend.database.tenant_context import (
    bind_organization_context,
    bind_user_context,
    current_context,
)
from backend.security.authorization import ORGANIZATION_ROLE_PERMISSIONS, OrganizationPermission
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.audit_event_types import AuditEventType
from backend.services.organization_audit_service import list_organization_audit_events
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_organizations import _grant, _org, _persona


def _error(response) -> str:
    return response.json()["error"]["code"]


def _insert_org_event(
    *,
    event_type: str,
    organization_id: str,
    workspace_id: str | None = None,
    actor_user_id: str | None = None,
    target_id: str | None = None,
    occurred_at: datetime | None = None,
    outcome: str = "success",
    details: dict | None = None,
) -> str:
    """A registered audit row, written past the application.

    The catalog's own target type keeps the write listener happy; every
    other field matches what ``organizations._audit`` stores.
    """
    with inspection_session_factory()() as db:
        event = AuditEvent(
            event_type=event_type,
            target_type=AuditEventType(event_type).target_type,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            target_id=target_id,
            outcome=outcome,
            details={"organization_id": organization_id, **(details or {})},
            occurred_at=occurred_at or datetime.now(UTC),
        )
        db.add(event)
        db.commit()
        return event.id


def _insert_workspace_settings_event(
    *, workspace_id: str, actor_user_id: str, occurred_at: datetime | None = None
) -> str:
    with inspection_session_factory()() as db:
        event = AuditEvent(
            event_type=AuditEventType.WORKSPACE_SETTINGS_UPDATED.value,
            target_type=AuditEventType.WORKSPACE_SETTINGS_UPDATED.target_type,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            outcome="success",
            details={"fields": "name"},
            occurred_at=occurred_at or datetime.now(UTC),
        )
        db.add(event)
        db.commit()
        return event.id


def _iso(moment: str) -> datetime:
    parsed = datetime.fromisoformat(moment)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


def _mint_api_key(persona, client) -> str:
    minted = persona.post(
        "/api/v1/api-keys",
        workspace_id=persona.personal_workspace_id,
        json={"name": "audit-key", "scopes": ["meeting:read"]},
    )
    assert minted.status_code == 201, minted.text
    return minted.json()["key"]


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name, value in (
            (ACCESS_COOKIE, client.cookies.get(ACCESS_COOKIE)),
            (REFRESH_COOKIE, client.cookies.get(REFRESH_COOKIE)),
            (CSRF_COOKIE, client.cookies.get(CSRF_COOKIE)),
        )
        if value
    }


# --- permission and catalog --------------------------------------------------------------


def test_audit_read_belongs_to_owner_and_admin_only():
    assert OrganizationPermission.AUDIT_READ.value == "organization:audit:read"
    assert (
        OrganizationPermission.AUDIT_READ in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.OWNER]
    )
    assert (
        OrganizationPermission.AUDIT_READ in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.ADMIN]
    )
    assert (
        OrganizationPermission.AUDIT_READ
        not in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.ANALYST]
    )
    assert (
        OrganizationPermission.AUDIT_READ
        not in ORGANIZATION_ROLE_PERMISSIONS[OrganizationRole.EXECUTIVE]
    )


def test_audit_event_types_route_lists_the_organization_catalog(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-types-owner@example.com", name="Owner")
    listed = owner.get(_org(owner, "/audit-event-types"))
    assert listed.status_code == 200, listed.text
    expected = sorted(
        member.value for member in AuditEventType if member.value.startswith("organization.")
    )
    items = listed.json()["items"]
    assert [item["event_type"] for item in items] == expected
    assert len(items) == 8
    for item in items:
        registered = AuditEventType(item["event_type"])
        assert item["group"] == registered.group == "organization"
        assert item["target_type"] == registered.target_type


# --- row selection ----------------------------------------------------------------------


def test_owner_reads_events_of_both_bindings_newest_first(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-read-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    assert sales.status_code == 201, sales.text
    support = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Support"})
    assert support.status_code == 201, support.text
    member = _persona(client, email_outbox, email="org-read-member@example.com", name="Member")
    granted = _grant(owner, org, member, "analyst")
    scope = owner.post(
        f"/api/v1/organizations/{org}/scope-grants",
        json={"membership_id": granted["id"], "workspace_id": sales.json()["id"]},
    )
    assert scope.status_code == 201, scope.text
    installed = owner.post(
        f"/api/v1/organizations/{org}/workspaces/{sales.json()['id']}/members",
        json={"user_id": member.user_id, "role": "member"},
    )
    assert installed.status_code == 201, installed.text

    listed = owner.get(_org(owner, "/audit-events"))
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert set(payload) == {"items", "next_cursor"}
    types = {item["event_type"] for item in payload["items"]}
    assert {
        AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        AuditEventType.ORGANIZATION_SCOPE_GRANTED.value,
        AuditEventType.ORGANIZATION_WORKSPACE_CREATED.value,
        AuditEventType.ORGANIZATION_WORKSPACE_MEMBER_INSTALLED.value,
    } <= types
    assert all(item["details"]["organization_id"] == org for item in payload["items"])
    assert all(
        set(item)
        == {
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
        for item in payload["items"]
    )
    moments = [_iso(item["occurred_at"]) for item in payload["items"]]
    assert moments == sorted(moments, reverse=True)


def test_foreign_organization_and_foreign_workspace_rows_are_hidden(client, email_outbox):
    alice = _persona(client, email_outbox, email="org-d1-alice@example.com", name="Alice")
    bob = _persona(client, email_outbox, email="org-d1-bob@example.com", name="Bob")
    org_a, org_b = alice.organization_id, bob.organization_id
    at = datetime.now(UTC)

    # D1 allow: org A's NULL-workspace event...
    ours = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        organization_id=org_a,
        occurred_at=at,
    )
    # D1 deny: the same type and time for org B.
    theirs = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        organization_id=org_b,
        occurred_at=at,
    )
    # D2 deny: details say A but the row sits in org B's workspace.
    misfiled = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_WORKSPACE_CREATED.value,
        organization_id=org_a,
        workspace_id=bob.personal_workspace_id,
        occurred_at=at,
    )
    # D2 allow: the same type bound to a workspace of org A.
    sales = alice.post(f"/api/v1/organizations/{org_a}/workspaces", json={"name": "Sales"})
    assert sales.status_code == 201, sales.text

    listed = alice.get(_org(alice, "/audit-events"))
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert ours in _ids(items)
    assert sales.json()["id"] in {item["target_id"] for item in items}
    assert theirs not in _ids(items)
    assert misfiled not in _ids(items)


def test_admin_reads_workspace_bound_rows_whatever_the_workspace_status(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-status-owner@example.com", name="Owner")
    admin = _persona(client, email_outbox, email="org-status-admin@example.com", name="Admin")
    org = owner.organization_id
    _grant(owner, org, admin, "admin")
    sales = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    assert sales.status_code == 201, sales.text
    sales_id = sales.json()["id"]
    installed = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_WORKSPACE_MEMBER_INSTALLED.value,
        organization_id=org,
        workspace_id=sales_id,
        occurred_at=datetime.now(UTC),
    )
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, sales_id)
        assert workspace is not None
        workspace.status = "suspended"
        db.commit()

    # The admin is no member of Sales: the organization grant alone reaches
    # its rows, and a workspace that is not active still counts (B4).
    listed = admin.get(f"/api/v1/organizations/{org}/audit-events")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert installed in _ids(items)
    assert sales_id in {
        item["target_id"]
        for item in items
        if item["event_type"] == AuditEventType.ORGANIZATION_WORKSPACE_CREATED.value
    }


def test_non_organization_types_stay_hidden_even_with_org_details(client, email_outbox):
    alice = _persona(client, email_outbox, email="org-d3-alice@example.com", name="Alice")
    org = alice.organization_id
    at = datetime.now(UTC)
    _insert_org_event(
        event_type=AuditEventType.WORKSPACE_SETTINGS_UPDATED.value,
        organization_id=org,
        workspace_id=alice.personal_workspace_id,
        actor_user_id=alice.user_id,
        occurred_at=at,
    )
    ours = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_SCOPE_REVOKED.value,
        organization_id=org,
        occurred_at=at + timedelta(seconds=1),
    )

    listed = alice.get(_org(alice, "/audit-events"))
    assert listed.status_code == 200, listed.text
    assert _ids(listed.json()["items"]) == [ours]


def test_details_are_sanitized_on_read(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-sanitize-owner@example.com", name="Owner")
    org = owner.organization_id
    _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        organization_id=org,
        occurred_at=datetime.now(UTC),
        details={"password": "super-secret-value"},
    )
    listed = owner.get(_org(owner, "/audit-events"))
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    assert item["details"]["password"] == "[redacted]"  # noqa: S105 - the sanitized key
    assert "super-secret-value" not in listed.text


# --- access -------------------------------------------------------------------------------


def test_access_is_bounded_by_membership_and_role(client, email_outbox):
    alice = _persona(client, email_outbox, email="org-d4-alice@example.com", name="Alice")
    bob = _persona(client, email_outbox, email="org-d4-bob@example.com", name="Bob")
    admin = _persona(client, email_outbox, email="org-d4-admin@example.com", name="Admin")
    analyst = _persona(client, email_outbox, email="org-d4-analyst@example.com", name="Analyst")
    executive = _persona(client, email_outbox, email="org-d4-exec@example.com", name="Executive")
    org = alice.organization_id
    _grant(alice, org, admin, "admin")
    _grant(alice, org, analyst, "analyst")
    _grant(alice, org, executive, "executive")

    # D4: the owner of org B cannot even see org A's route.
    denied = bob.get(f"/api/v1/organizations/{org}/audit-events")
    assert denied.status_code == 404
    assert _error(denied) == "organization_not_found"

    # D5: analyst and executive hold no audit right on either route.
    for persona in (analyst, executive):
        for path in ("/audit-events", "/audit-event-types"):
            refused = persona.get(f"/api/v1/organizations/{org}{path}")
            assert refused.status_code == 403, (persona.email, path, refused.text)
            assert _error(refused) == "permission_denied"

    # D5 allow: the admin reads both.
    for path in ("/audit-events", "/audit-event-types"):
        allowed = admin.get(f"/api/v1/organizations/{org}{path}")
        assert allowed.status_code == 200, (path, allowed.text)


def test_a_session_is_required_and_an_api_key_is_refused(client, email_outbox):
    alice = _persona(client, email_outbox, email="org-d6-alice@example.com", name="Alice")
    org = alice.organization_id
    anonymous = client.get(f"/api/v1/organizations/{org}/audit-events")
    assert anonymous.status_code == 401
    assert _error(anonymous) == "authentication_required"

    raw_key = _mint_api_key(alice, client)
    refused = client.get(
        f"/api/v1/organizations/{org}/audit-events",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert refused.status_code == 401
    assert _error(refused) == "api_key_not_accepted"

    allowed = alice.get(_org(alice, "/audit-events"))
    assert allowed.status_code == 200, allowed.text


# --- filters, validation and paging -----------------------------------------------------


def test_filters_narrow_and_bad_input_is_refused(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-filter-owner@example.com", name="Owner")
    org = owner.organization_id
    base = datetime.now(UTC) - timedelta(hours=1)
    member = _persona(client, email_outbox, email="org-filter-member@example.com", name="Member")
    kept = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        organization_id=org,
        actor_user_id=owner.user_id,
        occurred_at=base,
    )
    other = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_REVOKED.value,
        organization_id=org,
        actor_user_id=member.user_id,
        occurred_at=base + timedelta(minutes=10),
    )

    # D7: one event type hides the other org types and returns its own.
    narrow = owner.get(
        _org(owner, "/audit-events"),
        params={"event_type": AuditEventType.ORGANIZATION_MEMBER_GRANTED.value},
    )
    assert narrow.status_code == 200, narrow.text
    assert _ids(narrow.json()["items"]) == [kept]

    by_actor = owner.get(_org(owner, "/audit-events"), params={"actor_user_id": member.user_id})
    assert _ids(by_actor.json()["items"]) == [other]
    by_outcome = owner.get(_org(owner, "/audit-events"), params={"outcome": "failure"})
    assert by_outcome.json()["items"] == []
    by_target = owner.get(
        _org(owner, "/audit-events"),
        params={"target_type": "organization_membership"},
    )
    assert _ids(by_target.json()["items"]) == [other, kept]
    after = owner.get(
        _org(owner, "/audit-events"),
        params={"occurred_after": (base + timedelta(minutes=5)).isoformat()},
    )
    assert _ids(after.json()["items"]) == [other]
    before = owner.get(
        _org(owner, "/audit-events"),
        params={"occurred_before": (base + timedelta(minutes=5)).isoformat()},
    )
    assert _ids(before.json()["items"]) == [kept]
    window = owner.get(
        _org(owner, "/audit-events"),
        params={
            "occurred_after": base.isoformat(),
            "occurred_before": (base + timedelta(minutes=30)).isoformat(),
        },
    )
    assert _ids(window.json()["items"]) == [other, kept]

    reversed_range = owner.get(
        _org(owner, "/audit-events"),
        params={
            "occurred_after": (base + timedelta(minutes=30)).isoformat(),
            "occurred_before": base.isoformat(),
        },
    )
    assert reversed_range.status_code == 400
    assert _error(reversed_range) == "invalid_audit_range"
    bad_cursor = owner.get(_org(owner, "/audit-events"), params={"cursor": "not-a-cursor"})
    assert bad_cursor.status_code == 400
    assert _error(bad_cursor) == "invalid_cursor"
    for limit in ("0", "101"):
        refused = owner.get(_org(owner, "/audit-events"), params={"limit": limit})
        assert refused.status_code == 422, limit
    single = owner.get(_org(owner, "/audit-events"), params={"limit": "1"})
    assert single.status_code == 200
    assert single.json()["next_cursor"]


def test_paging_walks_every_event_once_across_workspaces(client, email_outbox):
    alice = _persona(client, email_outbox, email="org-page-alice@example.com", name="Alice")
    bob = _persona(client, email_outbox, email="org-page-bob@example.com", name="Bob")
    org_a, org_b = alice.organization_id, bob.organization_id
    sales = alice.post(f"/api/v1/organizations/{org_a}/workspaces", json={"name": "Sales"})
    support = alice.post(f"/api/v1/organizations/{org_a}/workspaces", json={"name": "Support"})
    assert sales.status_code == 201 and support.status_code == 201

    base = datetime.now(UTC) - timedelta(hours=2)
    expected: list[str] = []
    for index in range(9):
        workspace_id = (sales.json()["id"], support.json()["id"], None)[index % 3]
        expected.append(
            _insert_org_event(
                event_type=AuditEventType.ORGANIZATION_MEMBER_CHANGED.value,
                organization_id=org_a,
                workspace_id=workspace_id,
                occurred_at=base + timedelta(minutes=index),
            )
        )
    # D8 deny: org B's event, newer than everything of A.
    _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_CHANGED.value,
        organization_id=org_b,
        occurred_at=base + timedelta(hours=1),
    )
    # The two workspace creations of the setup are org A events too, newer
    # than every crafted row; Support was created after Sales.
    with inspection_session_factory()() as db:
        created = list(
            db.scalars(
                select(AuditEvent.id)
                .where(
                    AuditEvent.event_type == AuditEventType.ORGANIZATION_WORKSPACE_CREATED.value,
                    AuditEvent.target_id.in_([sales.json()["id"], support.json()["id"]]),
                )
                .order_by(AuditEvent.occurred_at.asc())
            )
        )
    assert len(created) == 2

    walked: list[str] = []
    cursor: str | None = None
    for _ in range(20):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = alice.get(_org(alice, "/audit-events"), params=params)
        assert page.status_code == 200, page.text
        payload = page.json()
        walked.extend(_ids(payload["items"]))
        cursor = payload["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    assert walked == list(reversed(created)) + list(reversed(expected))
    assert len(walked) == len(set(walked))


# --- the workspace audit surface stays untouched ----------------------------------------


def test_admin_audit_events_remain_workspace_scoped(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-d9-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    assert sales.status_code == 201, sales.text
    _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        organization_id=org,
        occurred_at=datetime.now(UTC),
    )

    listed = owner.get("/api/v1/admin/audit-events", workspace_id=sales.json()["id"])
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert items, "the workspace's own creation event is there"
    assert all(
        item["event_type"] != AuditEventType.ORGANIZATION_MEMBER_GRANTED.value for item in items
    )
    assert {item["event_type"] for item in items} == {
        AuditEventType.ORGANIZATION_WORKSPACE_CREATED.value
    }


def test_workspace_audit_events_still_work(client, email_outbox):
    credentials = register_user(client, email_outbox, email="org-ws-audit@example.com")
    session = login_user(client, credentials)
    workspace_id = session["workspaces"][0]["id"]
    cookies = _cookies(client)
    client.cookies.clear()
    _insert_workspace_settings_event(workspace_id=workspace_id, actor_user_id=session["user"]["id"])
    listed = client.get(
        "/api/v1/admin/audit-events",
        headers={"X-Workspace-ID": workspace_id},
        cookies=cookies,
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert {item["event_type"] for item in items} == {"workspace.settings.updated"}


# --- row-level security (PostgreSQL evidence) -------------------------------------------


@pytest.mark.postgres
def test_rls_bind_loop_and_context_restore(client, email_outbox):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    alice = _persona(client, email_outbox, email="org-rls-alice@example.com", name="Alice")
    org_id = alice.organization_id
    sales = alice.post(f"/api/v1/organizations/{org_id}/workspaces", json={"name": "Sales"})
    assert sales.status_code == 201, sales.text
    sales_id = sales.json()["id"]
    bound_id = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_WORKSPACE_MEMBER_INSTALLED.value,
        organization_id=org_id,
        workspace_id=sales_id,
        occurred_at=datetime.now(UTC),
    )
    null_id = _insert_org_event(
        event_type=AuditEventType.ORGANIZATION_MEMBER_GRANTED.value,
        organization_id=org_id,
        occurred_at=datetime.now(UTC),
    )

    # The route works end to end under RLS as well: the workspace-bound event
    # of the organization is answered, not only the NULL-workspace rows.
    listed = alice.get(_org(alice, "/audit-events"))
    assert listed.status_code == 200, listed.text
    assert {bound_id, null_id} <= set(_ids(listed.json()["items"]))

    # B5: after the query the request is left in the organization context,
    # with no workspace bound, proven on the session the service used.
    with get_session_factory()() as db:
        bind_user_context(db, alice.user_id)
        bind_organization_context(db, org_id)
        organization = db.get(Organization, org_id)
        assert organization is not None
        page = list_organization_audit_events(db, organization=organization, limit=50)
        assert {bound_id, null_id} <= {event.id for event in page.events}
        assert current_context(db).workspace_id is None
        bound_now = db.execute(text("SELECT current_setting('homayar.workspace_id', true)"))
        assert bound_now.scalar() in (None, "")
        org_now = db.execute(text("SELECT current_setting('homayar.organization_id', true)"))
        assert org_now.scalar() == org_id
        # The policy itself agrees: in the organization context alone the
        # workspace-bound row is hidden again (the trap the per-workspace
        # binding exists for) and the NULL-workspace row stays readable.
        assert db.scalars(select(AuditEvent.id).where(AuditEvent.id == bound_id)).all() == []
        assert db.scalars(select(AuditEvent.id).where(AuditEvent.id == null_id)).all() == [null_id]
        workspace_ids = set(
            db.scalars(select(Workspace.id).where(Workspace.organization_id == org_id))
        )
        assert sales_id in workspace_ids
        db.rollback()
