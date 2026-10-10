"""WP03-T03: organization membership, delegated administration, scoped analytics.

Positive and negative behaviour of the organization layer (ADR 0012):

* registration bootstraps exactly one organization owner, nothing else
  changes for the personal workspace (trial grant untouched);
* organization roles are granted inside the hierarchy, never `owner`, never
  to unverified accounts; revocation and expiry bite on the next request
  even with a valid session; other organizations are invisible;
* workspaces created inside an organization carry no trial entitlement; an
  organization admin has **no** workspace content access until an explicit,
  audited delegated installation — an ordinary workspace membership that the
  workspace routes and record scope govern afterwards;
* analysts and executives see only their granted workspaces/teams, as
  aggregates or metadata, never transcript content;
* under PostgreSQL row-level security the organization context exposes
  nothing of another organization and vanishes with the transaction.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    Entitlement,
    Meeting,
    MeetingStatus,
    Membership,
    OrganizationMembership,
    OrganizationRole,
    OrganizationScopeGrant,
    Subscription,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import (
    bind_organization_context,
    bind_user_context,
    clear_context,
)
from backend.security.authorization import (
    DELEGATED_WORKSPACE_ROLES,
    ORGANIZATION_GRANTABLE_ROLES,
    ORGANIZATION_ROLE_PERMISSIONS,
    OrganizationPermission,
)
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user


class Persona:
    def __init__(self, client, session: dict, cookies: dict[str, str]):
        self.client = client
        self.user_id = session["user"]["id"]
        self.email = session["user"]["email"]
        self.cookies = cookies
        self.personal_workspace_id = session["workspaces"][0]["id"]

    def _headers(self, workspace_id: str | None) -> dict[str, str]:
        headers = {"X-CSRF-Token": self.cookies[CSRF_COOKIE]}
        if workspace_id:
            headers["X-Workspace-ID"] = workspace_id
        return headers

    def get(self, path: str, *, workspace_id: str | None = None, **kwargs):
        return self.client.get(
            path, cookies=self.cookies, headers=self._headers(workspace_id), **kwargs
        )

    def post(self, path: str, *, workspace_id: str | None = None, **kwargs):
        return self.client.post(
            path, cookies=self.cookies, headers=self._headers(workspace_id), **kwargs
        )

    def patch(self, path: str, *, workspace_id: str | None = None, **kwargs):
        return self.client.patch(
            path, cookies=self.cookies, headers=self._headers(workspace_id), **kwargs
        )

    def delete(self, path: str, *, workspace_id: str | None = None, **kwargs):
        return self.client.delete(
            path, cookies=self.cookies, headers=self._headers(workspace_id), **kwargs
        )

    def organizations(self) -> list[dict]:
        response = self.get("/api/v1/organizations")
        assert response.status_code == 200, response.text
        return response.json()["items"]

    @property
    def organization_id(self) -> str:
        owned = [item for item in self.organizations() if item["role"] == "owner"]
        assert len(owned) == 1, owned
        return owned[0]["id"]


def _persona(client, email_outbox, *, email: str, name: str) -> Persona:
    credentials = register_user(client, email_outbox, email=email, name=name)
    session = login_user(client, credentials)
    cookies = {
        cookie: value
        for cookie in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(cookie))
    }
    client.cookies.clear()
    return Persona(client, session, cookies)


def _org(persona: Persona, path: str = "") -> str:
    return f"/api/v1/organizations/{persona.organization_id}{path}"


def _grant(owner: Persona, org: str, target: Persona, role: str, **extra) -> dict:
    response = owner.post(
        f"/api/v1/organizations/{org}/members",
        json={"email": target.email, "role": role, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _insert_meeting(
    workspace_id: str,
    *,
    created_by: str,
    title: str,
    duration: int,
    team_id: str | None = None,
    status: MeetingStatus = MeetingStatus.COMPLETED,
) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=created_by,
            title=title,
            status=status,
            duration_seconds=duration,
            team_id=team_id,
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _entitlement_rows(workspace_id: str) -> tuple[int, int]:
    with inspection_session_factory()() as db:
        subscriptions = db.scalars(
            select(Subscription.id).where(Subscription.workspace_id == workspace_id)
        ).all()
        entitlements = db.scalars(
            select(Entitlement.id).where(Entitlement.workspace_id == workspace_id)
        ).all()
        return len(subscriptions), len(entitlements)


def _audit_types(*, target_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent.event_type)
                .where(AuditEvent.target_id == target_id)
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


# --- bootstrap ---------------------------------------------------------------------------


def test_registration_bootstraps_one_owner_and_leaves_the_trial_untouched(client, email_outbox):
    owner = _persona(client, email_outbox, email="org-owner@example.com", name="Org Owner")
    organizations = owner.organizations()
    assert len(organizations) == 1 and organizations[0]["role"] == "owner"
    detail = owner.get(_org(owner))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["role"] == "owner" and body["unrestricted"] is True
    assert body["workspace_count"] == 1
    # the personal workspace keeps exactly the demo grant registration made;
    # the organization layer adds none
    subscriptions, entitlements = _entitlement_rows(owner.personal_workspace_id)
    assert subscriptions == 1 and entitlements >= 1
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(OrganizationMembership).where(OrganizationMembership.user_id == owner.user_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].granted_by_user_id == owner.user_id and rows[0].revoked_at is None


# --- membership and hierarchy ------------------------------------------------------------


def test_organization_roles_follow_the_grant_hierarchy_and_revocation_is_immediate(
    client, email_outbox
):
    owner = _persona(client, email_outbox, email="hier-owner@example.com", name="Owner")
    admin = _persona(client, email_outbox, email="hier-admin@example.com", name="Admin")
    analyst = _persona(client, email_outbox, email="hier-analyst@example.com", name="Analyst")
    executive = _persona(client, email_outbox, email="hier-exec@example.com", name="Exec")
    org = owner.organization_id

    granted_admin = _grant(owner, org, admin, "admin")
    assert granted_admin["role"] == "admin" and granted_admin["is_effective"] is True
    assert _audit_types(target_id=granted_admin["id"]) == ["organization.member.granted"]
    # the organization is now listed for the admin, with their role
    assert [o["role"] for o in admin.organizations() if o["id"] == org] == ["admin"]

    # an admin grants analyst/executive but not admin; nobody grants owner
    granted_analyst = _grant(admin, org, analyst, "analyst")
    granted_exec = _grant(admin, org, executive, "executive")
    denied = admin.post(
        f"/api/v1/organizations/{org}/members", json={"email": owner.email, "role": "admin"}
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == (
        "organization_grant_denied"
    )
    as_owner = owner.post(
        f"/api/v1/organizations/{org}/members", json={"email": analyst.email, "role": "owner"}
    )
    assert as_owner.status_code == 422
    # unknown / unverified accounts are refused; duplicates conflict
    unknown = owner.post(
        f"/api/v1/organizations/{org}/members",
        json={"email": "nobody@example.com", "role": "analyst"},
    )
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "invalid_user"
    duplicate = owner.post(
        f"/api/v1/organizations/{org}/members", json={"email": analyst.email, "role": "executive"}
    )
    assert duplicate.status_code == 409
    # tenant ids never travel in bodies
    smuggled = owner.post(
        f"/api/v1/organizations/{org}/members",
        json={"email": analyst.email, "role": "analyst", "organization_id": org},
    )
    assert smuggled.status_code == 422

    # analysts/executives cannot read the member list; the admin can
    assert analyst.get(f"/api/v1/organizations/{org}/members").status_code == 403
    listed = admin.get(f"/api/v1/organizations/{org}/members")
    assert listed.status_code == 200
    assert {m["role"] for m in listed.json()["items"]} == {"owner", "admin", "analyst", "executive"}

    # self-management and touching the owner are refused
    me = owner.patch(
        f"/api/v1/organizations/{org}/members/{granted_admin['id']}", json={"role": "analyst"}
    )
    assert me.status_code == 200  # owner demotes the admin ...
    own = admin.patch(  # ... who no longer manages anyone
        f"/api/v1/organizations/{org}/members/{granted_exec['id']}", json={"role": "analyst"}
    )
    assert own.status_code == 403 and own.json()["error"]["code"] == "permission_denied"
    with inspection_session_factory()() as db:
        owner_membership_id = db.scalar(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == org,
                OrganizationMembership.user_id == owner.user_id,
            )
        )
    restored = owner.patch(
        f"/api/v1/organizations/{org}/members/{granted_admin['id']}", json={"role": "admin"}
    )
    assert restored.status_code == 200
    untouchable = admin.delete(f"/api/v1/organizations/{org}/members/{owner_membership_id}")
    assert untouchable.status_code == 403
    selfish = owner.patch(
        f"/api/v1/organizations/{org}/members/{owner_membership_id}", json={"role": "admin"}
    )
    assert selfish.status_code == 403

    # revocation: the analyst's still-valid session loses the organization
    assert analyst.get(_org(owner)).status_code == 200
    revoked = owner.delete(f"/api/v1/organizations/{org}/members/{granted_analyst['id']}")
    assert revoked.status_code == 204
    assert analyst.get(f"/api/v1/organizations/{org}").status_code == 404
    assert [o["id"] for o in analyst.organizations() if o["id"] == org] == []
    assert _audit_types(target_id=granted_analyst["id"])[-1] == "organization.member.revoked"
    # re-grant reuses the row
    regranted = _grant(owner, org, analyst, "analyst")
    assert regranted["id"] == granted_analyst["id"] and regranted["revoked_at"] is None

    # expiry: a window that closed is as good as revoked, the session still valid
    with inspection_session_factory()() as db:
        row = db.get(OrganizationMembership, granted_exec["id"])
        row.valid_from = datetime.now(UTC) - timedelta(minutes=10)
        row.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert executive.get(f"/api/v1/organizations/{org}").status_code == 404
    # a window in the future is refused, an inverted window is refused
    future = owner.patch(
        f"/api/v1/organizations/{org}/members/{granted_exec['id']}",
        json={"clear_valid_until": True},
    )
    assert future.status_code == 200 and future.json()["is_effective"] is True
    assert executive.get(f"/api/v1/organizations/{org}").status_code == 200
    inverted = owner.post(
        f"/api/v1/organizations/{org}/members",
        json={
            "email": "hier-late@example.com",
            "role": "analyst",
            "valid_from": "2030-01-01T00:00:00Z",
            "valid_until": "2029-01-01T00:00:00Z",
        },
    )
    assert inverted.status_code in {422}


def test_other_organizations_are_invisible(client, email_outbox):
    alice = _persona(client, email_outbox, email="inv-alice@example.com", name="Alice")
    bob = _persona(client, email_outbox, email="inv-bob@example.com", name="Bob")
    other = bob.organization_id
    assert alice.get(f"/api/v1/organizations/{other}").status_code == 404
    assert alice.get(f"/api/v1/organizations/{other}/members").status_code == 404
    assert alice.get(f"/api/v1/organizations/{other}/workspaces").status_code == 404
    grant = alice.post(
        f"/api/v1/organizations/{other}/members", json={"email": alice.email, "role": "admin"}
    )
    assert grant.status_code == 404
    guessed = alice.get("/api/v1/organizations/00000000-0000-4000-8000-000000000000")
    assert guessed.status_code == 404
    assert [o["id"] for o in alice.organizations()] == [alice.organization_id]


# --- workspaces and delegated administration ------------------------------------------------


def test_organization_workspaces_have_no_trial_and_administration_is_explicit(client, email_outbox):
    owner = _persona(client, email_outbox, email="ws-owner@example.com", name="Owner")
    admin = _persona(client, email_outbox, email="ws-admin@example.com", name="Admin")
    analyst = _persona(client, email_outbox, email="ws-analyst@example.com", name="Analyst")
    outsider = _persona(client, email_outbox, email="ws-outsider@example.com", name="Out")
    org = owner.organization_id
    _grant(owner, org, admin, "admin")
    _grant(owner, org, analyst, "analyst")

    personal_grants = _entitlement_rows(owner.personal_workspace_id)
    created = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    assert created.status_code == 201, created.text
    sales = created.json()
    assert sales["slug"] == "sales" and sales["my_workspace_role"] == "owner"
    again = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    assert again.status_code == 201 and again.json()["slug"] == "sales-2"
    # no trial: no subscription, no entitlement; the personal one keeps its own
    assert _entitlement_rows(sales["id"]) == (0, 0)
    assert _entitlement_rows(owner.personal_workspace_id) == personal_grants
    assert _audit_types(target_id=sales["id"]) == ["organization.workspace.created"]
    # the owner is a real workspace owner there
    assert owner.get("/api/v1/meetings", workspace_id=sales["id"]).status_code == 200

    # the organization admin sees the workspace listed ...
    listed = admin.get(f"/api/v1/organizations/{org}/workspaces")
    assert listed.status_code == 200
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert set(by_id) == {owner.personal_workspace_id, sales["id"], again.json()["id"]}
    assert by_id[sales["id"]]["my_workspace_role"] is None
    # ... but has no content access anywhere until installed explicitly
    assert admin.get("/api/v1/meetings", workspace_id=sales["id"]).status_code == 403
    assert (
        admin.get("/api/v1/meetings", workspace_id=owner.personal_workspace_id).status_code == 403
    )
    blocked = analyst.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "X"})
    assert blocked.status_code == 403

    # delegated administration: install self as workspace admin, audited
    installed = admin.post(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members",
        json={"user_id": admin.user_id, "role": "admin"},
    )
    assert installed.status_code == 201, installed.text
    membership_id = installed.json()["membership_id"]
    assert _audit_types(target_id=membership_id) == ["organization.workspace.member_installed"]
    assert admin.get("/api/v1/meetings", workspace_id=sales["id"]).status_code == 200
    # the installed row is an ordinary membership: the workspace owner sees and manages it
    members = owner.get("/api/v1/members", workspace_id=sales["id"])
    assert members.status_code == 200
    assert {m["role"] for m in members.json()["items"]} == {"owner", "admin"}
    # the admin can now administer the workspace through the workspace routes
    team = admin.post("/api/v1/teams", workspace_id=sales["id"], json={"name": "Inbound"})
    assert team.status_code == 201, team.text

    # limits of delegation
    as_owner = admin.post(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members",
        json={"user_id": admin.user_id, "role": "owner"},
    )
    assert as_owner.status_code == 422
    not_a_member = admin.post(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members",
        json={"user_id": outsider.user_id, "role": "viewer"},
    )
    assert not_a_member.status_code == 404
    assert not_a_member.json()["error"]["code"] == "organization_member_not_found"
    by_analyst = analyst.post(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members",
        json={"user_id": analyst.user_id, "role": "viewer"},
    )
    assert by_analyst.status_code == 403
    foreign = admin.post(
        f"/api/v1/organizations/{org}/workspaces/{outsider.personal_workspace_id}/members",
        json={"user_id": admin.user_id, "role": "admin"},
    )
    assert foreign.status_code == 404
    with inspection_session_factory()() as db:
        owner_membership_id = db.scalar(
            select(Membership.id).where(
                Membership.workspace_id == sales["id"], Membership.user_id == owner.user_id
            )
        )
    untouchable = admin.delete(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members/{owner_membership_id}"
    )
    assert untouchable.status_code == 403

    # an analyst installed as viewer sees content there and only there
    viewer = admin.post(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members",
        json={"user_id": analyst.user_id, "role": "viewer"},
    )
    assert viewer.status_code == 201
    assert analyst.get("/api/v1/meetings", workspace_id=sales["id"]).status_code == 200
    assert (
        analyst.get("/api/v1/meetings", workspace_id=owner.personal_workspace_id).status_code == 403
    )
    # withdrawal through the organization route bites immediately
    removed = admin.delete(
        f"/api/v1/organizations/{org}/workspaces/{sales['id']}/members/"
        f"{viewer.json()['membership_id']}"
    )
    assert removed.status_code == 204
    assert analyst.get("/api/v1/meetings", workspace_id=sales["id"]).status_code == 403
    # the organization membership was not touched by any of this
    assert analyst.get(f"/api/v1/organizations/{org}").status_code == 200


# --- analysts and executives -----------------------------------------------------------------


def test_delegated_installation_needs_a_free_admin_seat(client, email_outbox):
    # HOM-167 F1: the personal workspace is on the free demo, whose one seat the owner holds.
    owner = _persona(client, email_outbox, email="seat-owner@example.com", name="Owner")
    admin = _persona(client, email_outbox, email="seat-admin@example.com", name="Admin")
    org = owner.organization_id
    _grant(owner, org, admin, "admin")
    path = f"/api/v1/organizations/{org}/workspaces/{owner.personal_workspace_id}/members"

    refused = admin.post(path, json={"user_id": admin.user_id, "role": "admin"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "seat_limit_reached"
    # Allow-control: a member role needs no seat.
    installed = admin.post(path, json={"user_id": admin.user_id, "role": "member"})
    assert installed.status_code == 201, installed.text


def test_analysts_and_executives_see_granted_scopes_only_and_never_content(client, email_outbox):
    owner = _persona(client, email_outbox, email="sc-owner@example.com", name="Owner")
    admin = _persona(client, email_outbox, email="sc-admin@example.com", name="Admin")
    analyst = _persona(client, email_outbox, email="sc-analyst@example.com", name="Analyst")
    executive = _persona(client, email_outbox, email="sc-exec@example.com", name="Exec")
    stranger = _persona(client, email_outbox, email="sc-stranger@example.com", name="Str")
    org = owner.organization_id
    admin_grant = _grant(owner, org, admin, "admin")
    analyst_grant = _grant(owner, org, analyst, "analyst")
    exec_grant = _grant(owner, org, executive, "executive")

    support = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Support"})
    sales = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    support_id, sales_id = support.json()["id"], sales.json()["id"]
    team = owner.post("/api/v1/teams", workspace_id=support_id, json={"name": "Tier 1"})
    assert team.status_code == 201, team.text
    tier1 = team.json()["id"]
    other_team = owner.post("/api/v1/teams", workspace_id=sales_id, json={"name": "SMB"})
    smb = other_team.json()["id"]
    _insert_meeting(
        support_id, created_by=owner.user_id, title="t1 call", duration=100, team_id=tier1
    )
    _insert_meeting(
        support_id,
        created_by=owner.user_id,
        title="unassigned",
        duration=50,
        status=MeetingStatus.PARTIAL,
    )
    _insert_meeting(sales_id, created_by=owner.user_id, title="smb call", duration=30, team_id=smb)
    personal_meeting = _insert_meeting(
        owner.personal_workspace_id, created_by=owner.user_id, title="private", duration=7
    )

    # nothing granted yet: empty scopes, no workspaces, no records
    assert executive.get(f"/api/v1/organizations/{org}/aggregates").json()["items"] == []
    assert executive.get(f"/api/v1/organizations/{org}/workspaces").json()["items"] == []
    assert analyst.get(f"/api/v1/organizations/{org}/records").json()["items"] == []

    # grants: executive -> Support / Tier 1 only; analyst -> Sales (whole)
    exec_scope = owner.post(
        f"/api/v1/organizations/{org}/scope-grants",
        json={"membership_id": exec_grant["id"], "workspace_id": support_id, "team_id": tier1},
    )
    assert exec_scope.status_code == 201, exec_scope.text
    analyst_scope = admin.post(
        f"/api/v1/organizations/{org}/scope-grants",
        json={"membership_id": analyst_grant["id"], "workspace_id": sales_id},
    )
    assert analyst_scope.status_code == 201, analyst_scope.text
    # refused grants: admins are not scoped, foreign workspaces/teams, duplicates
    for payload, code, status in (
        (
            {"membership_id": admin_grant["id"], "workspace_id": sales_id},
            "invalid_scope_target",
            422,
        ),
        (
            {"membership_id": exec_grant["id"], "workspace_id": stranger.personal_workspace_id},
            "workspace_not_found",
            404,
        ),
        (
            {"membership_id": exec_grant["id"], "workspace_id": support_id, "team_id": smb},
            "invalid_team",
            422,
        ),
        (
            {"membership_id": exec_grant["id"], "workspace_id": support_id, "team_id": tier1},
            "scope_grant_exists",
            409,
        ),
    ):
        refused = owner.post(f"/api/v1/organizations/{org}/scope-grants", json=payload)
        assert refused.status_code == status, refused.text
        assert refused.json()["error"]["code"] == code
    assert (
        analyst.post(
            f"/api/v1/organizations/{org}/scope-grants",
            json={"membership_id": analyst_grant["id"], "workspace_id": support_id},
        ).status_code
        == 403
    )

    # executive: aggregates over Support / Tier 1 only, no records route
    aggregates = executive.get(f"/api/v1/organizations/{org}/aggregates")
    assert aggregates.status_code == 200, aggregates.text
    items = aggregates.json()["items"]
    assert [(i["workspace_id"], i["team_ids"]) for i in items] == [(support_id, [tier1])]
    assert items[0]["meetings_total"] == 1 and items[0]["audio_seconds_total"] == 100
    assert items[0]["meetings_by_status"] == {"completed": 1}
    assert executive.get(f"/api/v1/organizations/{org}/records").status_code == 403
    scoped = executive.get(f"/api/v1/organizations/{org}/workspaces").json()["items"]
    assert [(w["id"], w["scoped_team_ids"], w["my_workspace_role"]) for w in scoped] == [
        (support_id, [tier1], None)
    ]
    # analyst: whole Sales workspace, metadata only
    records = analyst.get(f"/api/v1/organizations/{org}/records")
    assert records.status_code == 200, records.text
    body = records.json()
    assert body["truncated"] is False and [r["title"] for r in body["items"]] == ["smb call"]
    assert set(body["items"][0]) == {
        "workspace_id",
        "meeting_id",
        "team_id",
        "title",
        "status",
        "language",
        "duration_seconds",
        "created_at",
    }
    analyst_aggregates = analyst.get(f"/api/v1/organizations/{org}/aggregates").json()["items"]
    assert [
        (i["workspace_id"], i["team_ids"], i["audio_seconds_total"]) for i in analyst_aggregates
    ] == [(sales_id, None, 30)]
    # ... and still no content access: the workspace routes need a membership
    assert analyst.get("/api/v1/meetings", workspace_id=sales_id).status_code == 403
    assert executive.get("/api/v1/meetings", workspace_id=support_id).status_code == 403
    # owners/admins aggregate every workspace of the organization, personal one included
    everything = owner.get(f"/api/v1/organizations/{org}/aggregates").json()["items"]
    assert {i["workspace_id"] for i in everything} == {
        owner.personal_workspace_id,
        support_id,
        sales_id,
    }
    assert sum(i["audio_seconds_total"] for i in everything) == 187
    windowed = owner.get(
        f"/api/v1/organizations/{org}/aggregates", params={"until": "2000-01-01T00:00:00Z"}
    ).json()["items"]
    assert all(i["meetings_total"] == 0 for i in windowed)
    # the stranger's personal workspace was never aggregated
    assert personal_meeting and all(
        i["workspace_id"] != stranger.personal_workspace_id for i in everything
    )

    # an inactive team leaves the executive without a visible scope; reactivating
    # restores it
    paused = owner.patch(
        f"/api/v1/teams/{tier1}", workspace_id=support_id, json={"is_active": False}
    )
    assert paused.status_code == 200, paused.text
    assert executive.get(f"/api/v1/organizations/{org}/aggregates").json()["items"] == []
    resumed = owner.patch(
        f"/api/v1/teams/{tier1}", workspace_id=support_id, json={"is_active": True}
    )
    assert resumed.status_code == 200, resumed.text
    assert len(executive.get(f"/api/v1/organizations/{org}/aggregates").json()["items"]) == 1
    # revoking the grant empties the executive's view on the next request
    revoked = owner.delete(f"/api/v1/organizations/{org}/scope-grants/{exec_scope.json()['id']}")
    assert revoked.status_code == 204
    assert executive.get(f"/api/v1/organizations/{org}/aggregates").json()["items"] == []
    listed = admin.get(f"/api/v1/organizations/{org}/scope-grants").json()["items"]
    assert {g["id"]: g["revoked_at"] is not None for g in listed} == {
        exec_scope.json()["id"]: True,
        analyst_scope.json()["id"]: False,
    }
    # demoting the analyst to executive keeps grants; revoking the membership
    # revokes them
    owner.delete(f"/api/v1/organizations/{org}/members/{analyst_grant['id']}")
    with inspection_session_factory()() as db:
        grant = db.get(OrganizationScopeGrant, analyst_scope.json()["id"])
        assert grant is not None and grant.revoked_at is not None


# --- row-level security ----------------------------------------------------------------------


@pytest.mark.postgres
def test_organization_context_is_bounded_and_transaction_scoped(client, email_outbox):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    alice = _persona(client, email_outbox, email="rls-alice@example.com", name="Alice")
    bob = _persona(client, email_outbox, email="rls-bob@example.com", name="Bob")
    carol = _persona(client, email_outbox, email="rls-carol@example.com", name="Carol")
    org_a, org_b = alice.organization_id, bob.organization_id
    extra = alice.post(f"/api/v1/organizations/{org_a}/workspaces", json={"name": "Ops"})
    ops_id = extra.json()["id"]
    carol_grant = _grant(alice, org_a, carol, "analyst")

    # A corrupt grant (written past the application, e.g. by hand) that
    # points at another organization's workspace must expose nothing: the
    # workspaces policy admits only workspaces of the bound organization.
    with inspection_session_factory()() as db:
        db.add(
            OrganizationScopeGrant(
                organization_id=org_a,
                membership_id=carol_grant["id"],
                workspace_id=bob.personal_workspace_id,
                granted_by_user_id=alice.user_id,
            )
        )
        db.commit()
    assert carol.get(f"/api/v1/organizations/{org_a}/workspaces").json()["items"] == []
    assert carol.get(f"/api/v1/organizations/{org_a}/aggregates").json()["items"] == []

    factory = get_session_factory()
    with factory() as db:
        # organization context alone (no workspace): memberships and workspaces
        # of *this* organization only, plus one's own rows
        bind_user_context(db, alice.user_id)
        bind_organization_context(db, org_a)
        memberships = db.scalars(select(OrganizationMembership.organization_id)).all()
        assert set(memberships) == {org_a}
        workspaces = db.scalars(select(Workspace.id)).all()
        assert set(workspaces) == {alice.personal_workspace_id, ops_id}
        # a grant of another organization is neither visible nor writable
        assert (
            db.scalars(
                select(OrganizationScopeGrant.id).where(
                    OrganizationScopeGrant.organization_id == org_b
                )
            ).all()
            == []
        )
        db.add(
            OrganizationScopeGrant(
                organization_id=org_b,
                membership_id=carol_grant["id"],
                workspace_id=bob.personal_workspace_id,
                granted_by_user_id=alice.user_id,
            )
        )
        with pytest.raises(Exception, match="row-level security"):
            db.flush()
        db.rollback()
        # the corrupt grant's workspace stays invisible to its holder as well:
        # carol sees her own personal workspace (membership) and nothing else
        bind_user_context(db, carol.user_id)
        bind_organization_context(db, org_a)
        assert set(db.scalars(select(Workspace.id))) == {carol.personal_workspace_id}
        db.rollback()
        # the variables were transaction-local: once the session forgets its
        # context (end of request) the next transaction carries nothing and
        # sees nothing — a missing organization context is never a bypass
        clear_context(db)
        value = db.execute(text("SELECT current_setting('homayar.organization_id', true)")).scalar()
        assert value in (None, "")
        assert db.scalars(select(OrganizationMembership.id)).all() == []
        assert db.scalars(select(Workspace.id)).all() == []
        db.rollback()
    with factory() as db:
        # a user context without organization sees only their own memberships
        bind_user_context(db, bob.user_id)
        assert set(db.scalars(select(OrganizationMembership.organization_id))) == {org_b}
        assert set(db.scalars(select(Workspace.id))) == {bob.personal_workspace_id}
        db.rollback()


# --- document <-> code ------------------------------------------------------------------------


MATRIX_DOC = Path(__file__).resolve().parents[2] / "docs" / "security" / "authorization-matrix.md"


def _doc_block(name: str) -> list[list[str]]:
    content = MATRIX_DOC.read_text(encoding="utf-8")
    match = re.search(
        rf"<!-- matrix:{name}:start -->(.*?)<!-- matrix:{name}:end -->", content, re.S
    )
    assert match, f"matrix block {name!r} missing from {MATRIX_DOC}"
    rows = []
    for line in match.group(1).strip().splitlines()[2:]:
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def test_organization_matrix_document_matches_code():
    """§3a / §4a of the matrix are rebuilt from the policy module (ADR 0012)."""
    roles = [
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.ANALYST,
        OrganizationRole.EXECUTIVE,
    ]
    expected_permissions = [
        [permission.value]
        + ["✓" if permission in ORGANIZATION_ROLE_PERMISSIONS[role] else "–" for role in roles]
        for permission in OrganizationPermission
    ]
    assert _doc_block("organization-permissions") == expected_permissions
    expected_grants = [
        [role.value, ", ".join(sorted(r.value for r in ORGANIZATION_GRANTABLE_ROLES[role])) or "–"]
        for role in roles
    ]
    assert _doc_block("organization-grants") == expected_grants
    expected_delegation = [
        [role.value, ", ".join(sorted(r.value for r in DELEGATED_WORKSPACE_ROLES[role])) or "–"]
        for role in roles
    ]
    assert _doc_block("organization-delegation") == expected_delegation
    # every organization role is documented, the owner is never grantable and
    # nobody delegates the workspace owner role
    for role in roles:
        assert OrganizationRole.OWNER not in ORGANIZATION_GRANTABLE_ROLES[role]
        assert WorkspaceRole.OWNER not in DELEGATED_WORKSPACE_ROLES[role]
