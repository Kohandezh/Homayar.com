"""HOM-128 (WP23-T02): IdP group mappings, the reconciler, honest deprovisioning.

Three kinds of evidence (ADR 0066):

* the reconciler: mappings become ``source='federated'`` memberships, highest
  role wins, ``direct`` rows are never touched, an admin decision wins, seats
  are skipped not raised, and a second identical sync changes nothing;
* the endpoints: strict bodies, delegation and workspace checks, CSRF and
  session-only auth, and mapping changes re-applied at once without asking
  any IdP;
* deprovisioning: refresh asks a fake checker live (gone = sessions revoked
  and federated memberships removed, unavailable = fail closed and nothing
  revoked), the sweep is bounded and isolated, API keys carry a freshness
  gate, and the settings keep the <= 15 minutes promise arithmetically true.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from backend.config import Settings
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    AuthIdentity,
    FederationGroupMapping,
    IdentityProvider,
    LoginSession,
    Membership,
    Meeting,
    MeetingStatus,
    User,
    WorkspaceRole,
)
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.federation_service import (
    FederatedIdentityActive,
    FederatedIdentityGone,
    FederatedIdentityUnavailable,
    FederationIdentityError,
    FederationSyncResult,
    get_federated_identity_checker,
    revalidate_stale_federated_identities,
    set_federated_identity_checker,
    sync_federated_memberships,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user

ISSUER = "https://idp.example.com"
UUID_ZERO = "00000000-0000-4000-8000-000000000000"


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


class FakeChecker:
    """Test double for the HOM-127 checker; per-identity answers, exceptions."""

    def __init__(self) -> None:
        self.answers: dict[str, object] = {}
        self.default: object = FederatedIdentityActive(frozenset())
        self.checked: list[str] = []

    def answer(self, identity_id: str, status: object) -> None:
        self.answers[identity_id] = status

    def check(self, identity: AuthIdentity) -> object:
        self.checked.append(identity.id)
        answer = self.answers.get(identity.id, self.default)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def checker() -> FakeChecker:
    fake = FakeChecker()
    set_federated_identity_checker(fake)
    yield fake
    set_federated_identity_checker(None)


def _error(response) -> str:
    return response.json()["error"]["code"]


def _create_workspace(owner: Persona, org: str, name: str) -> str:
    response = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _identity_kwargs(
    organization_id: str,
    *,
    issuer: str = ISSUER,
    groups: tuple[str, ...] = (),
    verified_at: datetime | None = None,
    profile: dict | None = None,
) -> dict:
    return dict(
        provider=IdentityProvider.OIDC,
        subject=f"{issuer}|{uuid.uuid4().hex[:12]}",
        verified_at=verified_at or datetime.now(UTC),
        profile=profile
        if profile is not None
        else {"issuer": issuer, "organization_id": organization_id, "groups": list(groups)},
    )


def _make_oidc_user(
    organization_id: str,
    *,
    groups: tuple[str, ...] = (),
    verified_at: datetime | None = None,
    profile: dict | None = None,
) -> tuple[str, str]:
    """A direct-insert user with an OIDC identity; returns (user_id, identity_id)."""
    with inspection_session_factory()() as db:
        user = User(
            email=f"fed-{uuid.uuid4().hex[:10]}@example.com",
            display_name="Federated",
            is_active=True,
        )
        db.add(user)
        db.flush()
        identity = AuthIdentity(user_id=user.id, **_identity_kwargs(
            organization_id, groups=groups, verified_at=verified_at, profile=profile
        ))
        db.add(identity)
        db.commit()
        return user.id, identity.id


def _graft_oidc_identity(
    user_id: str,
    organization_id: str,
    *,
    groups: tuple[str, ...] = (),
    verified_at: datetime | None = None,
    profile: dict | None = None,
) -> str:
    with inspection_session_factory()() as db:
        identity = AuthIdentity(
            user_id=user_id,
            **_identity_kwargs(
                organization_id, groups=groups, verified_at=verified_at, profile=profile
            ),
        )
        db.add(identity)
        db.commit()
        return identity.id


def _mapping(
    organization_id: str,
    workspace_id: str,
    group: str,
    role: WorkspaceRole,
    *,
    created_by: str,
    revoked: bool = False,
) -> str:
    with inspection_session_factory()() as db:
        mapping = FederationGroupMapping(
            organization_id=organization_id,
            issuer=ISSUER,
            group_name=group,
            workspace_id=workspace_id,
            role=role,
            created_by_user_id=created_by,
        )
        if revoked:
            mapping.revoked_at = datetime.now(UTC)
            mapping.revoked_by_user_id = created_by
        db.add(mapping)
        db.commit()
        return mapping.id


def _sync(
    identity_id: str,
    groups: tuple[str, ...] | frozenset[str],
    *,
    confirmed: bool = True,
):
    """Run the reconciler through the application session, which obeys the
    PostgreSQL row-level security policies in CI; the inspection session is
    only for setup and for reading results."""
    with get_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        assert identity is not None
        result = sync_federated_memberships(
            db, identity=identity, groups=groups, confirmed=confirmed
        )
        db.commit()
    return result


def _membership(workspace_id: str, user_id: str) -> tuple[str, bool, str] | None:
    with inspection_session_factory()() as db:
        row = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
        if row is None:
            return None
        return row.role.value, row.is_active, row.source


def _audit_events(event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type)))


def _insert_meeting(workspace_id: str, *, created_by: str, title: str) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=created_by,
            title=title,
            status=MeetingStatus.COMPLETED,
            duration_seconds=60,
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _set_workspace_status(workspace_id: str, status: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE workspaces SET status = :status WHERE id = :id"),
            {"status": status, "id": workspace_id},
        )
        db.commit()


# --- the reconciler -----------------------------------------------------------------


def remove_federation_group_mappings(connection) -> None:
    """Take a ``create_all`` schema back to before HOM-128.

    Older migration tests build the full ORM, drop the newer tables and stamp
    an older revision.  Running this revision's own downgrade removes the
    mapping table and the two columns exactly as the migration wrote them; on
    a ``create_all`` schema the ``memberships`` rebuild happens in batch mode.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from backend.migrations.versions.e8b4d2f7a9c1_federation_group_mappings import (
        downgrade,
    )

    with Operations.context(MigrationContext.configure(connection)):
        downgrade()


def test_sync_grants_the_highest_role_and_tracks_the_source(client, email_outbox):
    owner = _persona(client, email_outbox, email="sync-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    ops = _create_workspace(owner, org, "Ops")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, sales, "sales-leads", WorkspaceRole.ADMIN, created_by=owner.user_id)
    _mapping(org, ops, "ops", WorkspaceRole.VIEWER, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org)

    result = _sync(identity_id, ("sales", "sales-leads", "ops"))
    assert set(result.granted) == {ops, sales}
    assert result.role_changed == () and result.removed == ()
    assert _membership(sales, user_id) == ("admin", True, "federated")
    assert _membership(ops, user_id) == ("viewer", True, "federated")

    # "sales-leads" gone from the report: the highest remaining role is member
    downgraded = _sync(identity_id, ("sales", "ops"))
    assert downgraded.role_changed == (sales,)
    assert _membership(sales, user_id) == ("member", True, "federated")

    # group removed upstream -> the federated row goes inactive
    removed = _sync(identity_id, ())
    assert set(removed.removed) == {ops, sales}
    assert _membership(sales, user_id) == ("member", False, "federated")
    assert _membership(ops, user_id) == ("viewer", False, "federated")


def test_one_sync_changes_one_workspace_and_removes_another(client, email_outbox):
    # One flush path, two workspaces, one role change and one removal: on
    # PostgreSQL each write must land while its own tenant is bound.
    owner = _persona(client, email_outbox, email="twoflush-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    ops = _create_workspace(owner, org, "Ops")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, ops, "ops", WorkspaceRole.VIEWER, created_by=owner.user_id)
    _mapping(org, sales, "sales-admin", WorkspaceRole.ADMIN, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org)

    assert set(_sync(identity_id, ("sales", "ops")).granted) == {sales, ops}
    assert _membership(sales, user_id) == ("member", True, "federated")
    assert _membership(ops, user_id) == ("viewer", True, "federated")

    changed = _sync(identity_id, ("sales", "sales-admin"))
    assert changed.role_changed == (sales,)
    assert changed.removed == (ops,)
    assert _membership(sales, user_id) == ("admin", True, "federated")
    assert _membership(ops, user_id) == ("viewer", False, "federated")


def test_a_suspended_workspace_loses_its_federated_row(client, email_outbox):
    # SPEC 3.3/3.5: only active workspaces count as desired, so the row in a
    # suspended workspace is removed even though its group still maps.
    owner = _persona(client, email_outbox, email="suspended-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org)
    assert _sync(identity_id, ("sales",)).granted == (sales,)

    _set_workspace_status(sales, "suspended")
    result = _sync(identity_id, ("sales",))
    assert result.removed == (sales,)
    assert _membership(sales, user_id) == ("member", False, "federated")


def test_a_second_identical_sync_changes_nothing_and_writes_no_audit(client, email_outbox):
    owner = _persona(client, email_outbox, email="idem-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    user_id, identity_id = _make_oidc_user(org, groups=("sales",))
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)

    first = _sync(identity_id, ("sales",))
    assert first.granted == (sales,)
    assert len(_audit_events("federation.membership.synced")) == 1

    second = _sync(identity_id, ("sales",))
    assert second == FederationSyncResult()
    assert len(_audit_events("federation.membership.synced")) == 1
    assert _membership(sales, user_id) == ("member", True, "federated")


def test_direct_and_owner_rows_are_never_touched(client, email_outbox):
    owner = _persona(client, email_outbox, email="direct-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    _mapping(org, sales, "sales", WorkspaceRole.ADMIN, created_by=owner.user_id)
    owner_identity = _graft_oidc_identity(owner.user_id, org)
    user_id, identity_id = _make_oidc_user(org)
    with inspection_session_factory()() as db:
        db.add(
            Membership(
                workspace_id=sales,
                user_id=user_id,
                role=WorkspaceRole.VIEWER,
                is_active=True,
            )
        )
        db.commit()

    # the workspace owner's own row is a direct row, whatever groups say
    assert _sync(owner_identity, ("sales",)).kept_direct == (sales,)
    assert _membership(sales, owner.user_id) == ("owner", True, "direct")
    # a direct row of the federated user in the mapped workspace stays as it is
    assert _sync(identity_id, ("sales",)).kept_direct == (sales,)
    assert _membership(sales, user_id) == ("viewer", True, "direct")


def test_sync_stays_inside_the_identity_organization(client, email_outbox):
    owner = _persona(client, email_outbox, email="scope-owner@example.com", name="Owner")
    other = _persona(client, email_outbox, email="scope-other@example.com", name="Other")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    foreign_ws = other.personal_workspace_id
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org)
    with inspection_session_factory()() as db:
        db.add(
            Membership(
                workspace_id=foreign_ws,
                user_id=user_id,
                role=WorkspaceRole.MEMBER,
                is_active=True,
                source="federated",
            )
        )
        db.commit()

    assert _sync(identity_id, ("sales",)).granted == (sales,)
    emptied = _sync(identity_id, ())
    assert emptied.removed == (sales,)
    # the other organization's federated row keeps its state
    assert _membership(foreign_ws, user_id) == ("member", True, "federated")


def test_revoked_mappings_inactive_workspaces_and_case_differences_do_not_match(
    client, email_outbox
):
    owner = _persona(client, email_outbox, email="edge-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    paused = _create_workspace(owner, org, "Paused")
    gone = _create_workspace(owner, org, "Gone")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id, revoked=True)
    _mapping(org, paused, "paused", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, gone, "gone", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _set_workspace_status(paused, "disabled")
    user_id, identity_id = _make_oidc_user(org)

    result = _sync(identity_id, ("sales", "paused", "gone", "Sales"))
    assert result.granted == (gone,)
    assert _membership(sales, user_id) is None
    assert _membership(paused, user_id) is None
    assert _membership(gone, user_id) == ("member", True, "federated")


def test_a_full_seat_skips_only_that_workspace(client, email_outbox):
    owner = _persona(client, email_outbox, email="seat-owner@example.com", name="Owner")
    org = owner.organization_id
    free = _create_workspace(owner, org, "Free")
    # the personal workspace is on the one-seat demo plan and the owner holds it
    _mapping(
        org, owner.personal_workspace_id, "personal", WorkspaceRole.ADMIN, created_by=owner.user_id
    )
    _mapping(org, free, "free", WorkspaceRole.MEMBER, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org)

    result = _sync(identity_id, ("personal", "free"))
    assert result.skipped_seat_limit == (owner.personal_workspace_id,)
    assert result.granted == (free,)
    assert _membership(owner.personal_workspace_id, user_id) is None
    assert _membership(free, user_id) == ("member", True, "federated")
    skips = _audit_events("federation.membership.seat_skipped")
    assert [event.target_id for event in skips] == [owner.personal_workspace_id]


def test_a_malformed_profile_is_refused(client, email_outbox):
    owner = _persona(client, email_outbox, email="mal-owner@example.com", name="Owner")
    _, identity_id = _make_oidc_user(owner.organization_id, profile={"issuer": ISSUER})
    with pytest.raises(FederationIdentityError):
        _sync(identity_id, ("sales",))


# --- admin decision wins ---------------------------------------------------------------


def test_an_admin_role_change_and_removal_make_the_row_direct(client, email_outbox):
    owner = _persona(client, email_outbox, email="admin-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org)
    assert _sync(identity_id, ("sales",)).granted == (sales,)

    members = owner.get("/api/v1/members", workspace_id=sales)
    assert members.status_code == 200, members.text
    membership_id = next(
        item["id"] for item in members.json()["items"] if item["user_id"] == user_id
    )
    changed = owner.patch(
        f"/api/v1/members/{membership_id}", workspace_id=sales, json={"role": "viewer"}
    )
    assert changed.status_code == 200, changed.text
    assert _membership(sales, user_id) == ("viewer", True, "direct")

    # a later sync with a matching group neither overwrites the role nor re-grants
    assert _sync(identity_id, ("sales",)).kept_direct == (sales,)
    assert _membership(sales, user_id) == ("viewer", True, "direct")

    assert owner.delete(f"/api/v1/members/{membership_id}", workspace_id=sales).status_code == 204
    assert _membership(sales, user_id) == ("viewer", False, "direct")
    resurrected = _sync(identity_id, ("sales",))
    assert resurrected.granted == () and resurrected.kept_direct == (sales,)
    assert _membership(sales, user_id) == ("viewer", False, "direct")


# --- the mapping endpoints ---------------------------------------------------------------


def test_mapping_lifecycle_list_create_revoke_and_audit(client, email_outbox):
    owner = _persona(client, email_outbox, email="map-owner@example.com", name="Owner")
    analyst = _persona(client, email_outbox, email="map-analyst@example.com", name="Analyst")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    granted = owner.post(
        f"/api/v1/organizations/{org}/members", json={"email": analyst.email, "role": "analyst"}
    )
    assert granted.status_code == 201, granted.text
    path = f"/api/v1/organizations/{org}/federation/group-mappings"

    assert owner.get(path).json()["items"] == []
    created = owner.post(
        path,
        json={
            "issuer": ISSUER,
            "group_name": "sales",
            "workspace_id": sales,
            "role": "admin",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["issuer"] == ISSUER and body["group_name"] == "sales"
    assert body["workspace_id"] == sales and body["role"] == "admin"
    assert body["created_by_user_id"] == owner.user_id and body["created_at"]

    duplicate = owner.post(
        path,
        json={
            "issuer": ISSUER,
            "group_name": "sales",
            "workspace_id": sales,
            "role": "admin",
        },
    )
    assert duplicate.status_code == 409 and _error(duplicate) == "group_mapping_exists"

    assert [item["id"] for item in owner.get(path).json()["items"]] == [body["id"]]
    assert owner.delete(f"{path}/{body['id']}").status_code == 204
    assert owner.get(path).json()["items"] == []
    twice = owner.delete(f"{path}/{body['id']}")
    assert twice.status_code == 404 and _error(twice) == "group_mapping_not_found"

    assert [event.target_id for event in _audit_events("federation.group_mapping.created")] == [
        body["id"]
    ]
    assert [event.target_id for event in _audit_events("federation.group_mapping.revoked")] == [
        body["id"]
    ]
    # an organization analyst holds no administer permission
    assert analyst.get(path).status_code == 403


def test_mapping_audit_rows_carry_no_workspace_without_an_identity(client, email_outbox):
    # The F1 path (no OIDC identity to re-sync): the audit rows are flushed
    # with only the organization bound, so the workspace id rides in details
    # and the row itself carries no workspace (RLS write check).
    owner = _persona(client, email_outbox, email="f1-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    path = f"/api/v1/organizations/{org}/federation/group-mappings"

    created = owner.post(
        path,
        json={
            "issuer": ISSUER,
            "group_name": "sales",
            "workspace_id": sales,
            "role": "member",
        },
    )
    assert created.status_code == 201, created.text
    assert owner.delete(f"{path}/{created.json()['id']}").status_code == 204

    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type.in_(
                    ["federation.group_mapping.created", "federation.group_mapping.revoked"]
                )
            )
        ).all()
    assert len(rows) == 2
    for row in rows:
        assert row.workspace_id is None
        assert row.details["workspace_id"] == sales


def test_mapping_bodies_are_strict_and_workspaces_must_belong(client, email_outbox):
    owner = _persona(client, email_outbox, email="strict-owner@example.com", name="Owner")
    stranger = _persona(client, email_outbox, email="strict-other@example.com", name="Other")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    path = f"/api/v1/organizations/{org}/federation/group-mappings"
    good = {"issuer": ISSUER, "group_name": "sales", "workspace_id": sales, "role": "viewer"}

    for overrides in (
        {"role": "owner"},
        {"role": "chief"},
        {"issuer": f" {ISSUER}"},
        {"group_name": " sales"},
        {"issuer": ""},
        {"extra": 1},
    ):
        refused = owner.post(path, json={**good, **overrides})
        assert refused.status_code == 422, (refused.text, overrides)

    foreign = owner.post(path, json={**good, "workspace_id": stranger.personal_workspace_id})
    assert foreign.status_code == 404 and _error(foreign) == "workspace_not_found"
    unknown = owner.post(path, json={**good, "workspace_id": UUID_ZERO})
    assert unknown.status_code == 404 and _error(unknown) == "workspace_not_found"
    assert owner.post(path, json=good).status_code == 201


def test_mapping_routes_need_a_session_with_csrf(client, email_outbox):
    owner = _persona(client, email_outbox, email="auth-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    path = f"/api/v1/organizations/{org}/federation/group-mappings"

    no_csrf = owner.client.post(
        path,
        cookies=owner.cookies,
        json={
            "issuer": ISSUER,
            "group_name": "sales",
            "workspace_id": sales,
            "role": "member",
        },
    )
    assert no_csrf.status_code == 403 and _error(no_csrf) == "csrf_failed"

    minted = owner.post(
        "/api/v1/api-keys",
        workspace_id=sales,
        json={"name": "ci", "scopes": ["meeting:read"], "expires_in_days": 30},
    )
    assert minted.status_code == 201, minted.text
    raw_key = minted.json()["key"]
    with_key = owner.client.get(path, headers={"Authorization": f"Bearer {raw_key}"})
    assert with_key.status_code == 401 and _error(with_key) == "api_key_not_accepted"


def test_mapping_changes_re_apply_at_once_without_asking_the_idp(client, email_outbox):
    owner = _persona(client, email_outbox, email="apply-owner@example.com", name="Owner")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    stale = datetime.now(UTC) - timedelta(minutes=30)
    user_id, identity_id = _make_oidc_user(org, groups=("sales",), verified_at=stale)
    path = f"/api/v1/organizations/{org}/federation/group-mappings"

    created = owner.post(
        path,
        json={
            "issuer": ISSUER,
            "group_name": "sales",
            "workspace_id": sales,
            "role": "member",
        },
    )
    assert created.status_code == 201, created.text
    assert _membership(sales, user_id) == ("member", True, "federated")

    assert owner.delete(f"{path}/{created.json()['id']}").status_code == 204
    assert _membership(sales, user_id) == ("member", False, "federated")
    with inspection_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        # no IdP was asked: the stale confirmation was not refreshed
        assert identity.verified_at.replace(tzinfo=UTC) == stale
        assert identity.profile["groups"] == ["sales"]


# --- deprovisioning on refresh ---------------------------------------------------------------


def _refresh(persona: Persona):
    response = persona.client.post(
        "/api/v1/auth/refresh",
        cookies=persona.cookies,
        headers={"X-CSRF-Token": persona.cookies[CSRF_COOKIE]},
    )
    # a successful refresh rotates every cookie; keep the persona current
    for cookie in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        value = response.cookies.get(cookie)
        if value:
            persona.cookies[cookie] = value
    return response


def _login_sessions_of(user_id: str) -> list[LoginSession]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(LoginSession).where(LoginSession.user_id == user_id)))


def _oidc_identity_of(user_id: str) -> AuthIdentity | None:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.user_id == user_id,
                AuthIdentity.provider == IdentityProvider.OIDC,
            )
        )


def test_refresh_with_an_active_idp_applies_the_groups(client, email_outbox, checker):
    owner = _persona(client, email_outbox, email="refresh-owner@example.com", name="Owner")
    fed = _persona(client, email_outbox, email="refresh-fed@example.com", name="Fed")
    org = owner.organization_id
    team = _create_workspace(owner, org, "Team")
    _mapping(org, team, "team", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _graft_oidc_identity(fed.user_id, org)

    checker.default = FederatedIdentityActive(frozenset({"team"}))
    response = _refresh(fed)
    assert response.status_code == 200, response.text
    assert _membership(team, fed.user_id) == ("member", True, "federated")
    assert _membership(fed.personal_workspace_id, fed.user_id) == ("owner", True, "direct")
    identity = _oidc_identity_of(fed.user_id)
    assert identity.profile["groups"] == ["team"]
    assert identity.verified_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)


def test_refresh_with_a_gone_idp_deprovisions_and_commits(client, email_outbox, checker):
    owner = _persona(client, email_outbox, email="gone-owner@example.com", name="Owner")
    fed = _persona(client, email_outbox, email="gone-fed@example.com", name="Fed")
    org = owner.organization_id
    team = _create_workspace(owner, org, "Team")
    _mapping(org, team, "team", WorkspaceRole.MEMBER, created_by=owner.user_id)
    identity_id = _graft_oidc_identity(fed.user_id, org, groups=("team",))
    checker.default = FederatedIdentityActive(frozenset({"team"}))
    assert _refresh(fed).status_code == 200
    assert _membership(team, fed.user_id) == ("member", True, "federated")

    checker.default = FederatedIdentityGone()
    refused = _refresh(fed)
    assert refused.status_code == 401, refused.text
    assert _error(refused) == "federation_reauthentication_required"

    sessions = _login_sessions_of(fed.user_id)
    assert sessions and all(session.revoked_at is not None for session in sessions)
    blocked = fed.client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {fed.cookies[ACCESS_COOKIE]}"},
    )
    assert blocked.status_code == 401, blocked.text
    assert _membership(team, fed.user_id) == ("member", False, "federated")
    assert _membership(fed.personal_workspace_id, fed.user_id)[1] is True
    with inspection_session_factory()() as db:
        assert db.get(AuthIdentity, identity_id).revoked_at is not None
    assert _audit_events("federation.identity.deprovisioned")


def test_refresh_gone_deprovisions_federated_rows_in_two_workspaces(client, email_outbox, checker):
    owner = _persona(client, email_outbox, email="gone2-owner@example.com", name="Owner")
    fed = _persona(client, email_outbox, email="gone2-fed@example.com", name="Fed")
    org = owner.organization_id
    sales = _create_workspace(owner, org, "Sales")
    ops = _create_workspace(owner, org, "Ops")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, ops, "ops", WorkspaceRole.VIEWER, created_by=owner.user_id)
    identity_id = _graft_oidc_identity(fed.user_id, org)
    assert set(_sync(identity_id, ("sales", "ops")).granted) == {sales, ops}

    checker.default = FederatedIdentityGone()
    refused = _refresh(fed)
    assert refused.status_code == 401, refused.text
    assert _error(refused) == "federation_reauthentication_required"

    # everything above must have been committed by the failing request
    with inspection_session_factory()() as db:
        sessions = list(
            db.scalars(select(LoginSession).where(LoginSession.user_id == fed.user_id))
        )
        assert sessions and all(session.revoked_at is not None for session in sessions)
        for workspace_id in (sales, ops):
            row = db.scalar(
                select(Membership).where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id == fed.user_id,
                )
            )
            assert row is not None and row.is_active is False
        assert db.get(AuthIdentity, identity_id).revoked_at is not None
        deprovisioned = db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "federation.identity.deprovisioned"
            )
        ).all()
        assert [event.target_id for event in deprovisioned] == [fed.user_id]


def test_refresh_fails_closed_when_the_idp_does_not_answer(client, email_outbox, checker):
    owner = _persona(client, email_outbox, email="unavail-owner@example.com", name="Owner")
    identity_id = _graft_oidc_identity(owner.user_id, owner.organization_id)
    for answer in (FederatedIdentityUnavailable("idp_down"), RuntimeError("connection reset")):
        checker.default = answer
        refused = _refresh(owner)
        assert refused.status_code == 401, refused.text
        assert _error(refused) == "federation_reauthentication_required"
        sessions = _login_sessions_of(owner.user_id)
        assert sessions and all(session.revoked_at is None for session in sessions)
        with inspection_session_factory()() as db:
            assert db.get(AuthIdentity, identity_id).revoked_at is None

    # the presented refresh token still works once the IdP answers again
    checker.default = FederatedIdentityActive(frozenset())
    assert _refresh(owner).status_code == 200

    # a malformed profile fails closed the same way
    malformed = _persona(
        client, email_outbox, email="unavail-malformed@example.com", name="Malformed"
    )
    _graft_oidc_identity(malformed.user_id, malformed.organization_id, profile={})
    checker.default = FederatedIdentityActive(frozenset())
    refused = _refresh(malformed)
    assert refused.status_code == 401 and _error(refused) == (
        "federation_reauthentication_required"
    )


def test_refresh_without_a_checker_fails_closed_for_federated_users_only(client, email_outbox):
    federated = _persona(client, email_outbox, email="nochecker-fed@example.com", name="Fed")
    plain = _persona(client, email_outbox, email="nochecker-plain@example.com", name="Plain")
    assert get_federated_identity_checker() is None
    _graft_oidc_identity(federated.user_id, federated.organization_id)

    refused = _refresh(federated)
    assert refused.status_code == 401 and _error(refused) == (
        "federation_reauthentication_required"
    )
    assert _refresh(plain).status_code == 200


# --- the API key freshness gate ---------------------------------------------------------------


def _mint_key(persona: Persona, workspace_id: str) -> str:
    minted = persona.post(
        "/api/v1/api-keys",
        workspace_id=workspace_id,
        json={"name": "ci", "scopes": ["meeting:read"], "expires_in_days": 30},
    )
    assert minted.status_code == 201, minted.text
    return minted.json()["key"]


def test_api_keys_of_federated_users_carry_a_freshness_gate(client, email_outbox):
    owner = _persona(client, email_outbox, email="key-owner@example.com", name="Owner")
    org = owner.organization_id
    team = _create_workspace(owner, org, "Team")
    identity_id = _graft_oidc_identity(owner.user_id, org)
    raw_key = _mint_key(owner, team)
    list_url = "/api/v1/meetings"

    def set_verified(minutes_ago: int) -> None:
        with inspection_session_factory()() as db:
            identity = db.get(AuthIdentity, identity_id)
            identity.verified_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
            db.commit()

    set_verified(14)
    fresh = owner.client.get(
        list_url, headers={"Authorization": f"Bearer {raw_key}", "X-Workspace-ID": team}
    )
    assert fresh.status_code == 200, fresh.text
    set_verified(16)
    stale = owner.client.get(
        list_url, headers={"Authorization": f"Bearer {raw_key}", "X-Workspace-ID": team}
    )
    assert stale.status_code == 401 and _error(stale) == (
        "federation_reauthentication_required"
    )

    # a non-federated user's key has no gate
    plain = _persona(client, email_outbox, email="key-plain@example.com", name="Plain")
    plain_key = _mint_key(plain, plain.personal_workspace_id)
    with inspection_session_factory()() as db:
        db.execute(
            text(
                "UPDATE auth_identities SET verified_at = :old "
                "WHERE user_id = :user AND provider = 'PASSWORD'"
            ),
            {"old": datetime.now(UTC) - timedelta(days=400), "user": plain.user_id},
        )
        db.commit()
    unchanged = plain.client.get(
        list_url,
        headers={
            "Authorization": f"Bearer {plain_key}",
            "X-Workspace-ID": plain.personal_workspace_id,
        },
    )
    assert unchanged.status_code == 200, unchanged.text


# --- the login freshness gate (5e) ----------------------------------------------------


def test_login_refuses_a_stale_federated_identity(client, email_outbox):
    fed = _persona(client, email_outbox, email="logingate-fed@example.com", name="Fed")
    plain = _persona(client, email_outbox, email="logingate-plain@example.com", name="Plain")
    identity_id = _graft_oidc_identity(fed.user_id, fed.organization_id)
    credentials = {"identifier": "logingate-fed@example.com", "password": "SecurePass123"}

    def set_verified(minutes_ago: int) -> None:
        with inspection_session_factory()() as db:
            identity = db.get(AuthIdentity, identity_id)
            identity.verified_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
            db.commit()

    set_verified(16)
    sessions_before = len(_login_sessions_of(fed.user_id))
    refused = client.post("/api/v1/auth/login", json=credentials)
    assert refused.status_code == 401, refused.text
    assert _error(refused) == "federation_reauthentication_required"
    # nothing was created: no new session exists for the refused login
    assert len(_login_sessions_of(fed.user_id)) == sessions_before

    set_verified(14)
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200

    # a revoked OIDC identity logs in normally, however stale it is
    with inspection_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        identity.verified_at = datetime.now(UTC) - timedelta(days=30)
        identity.revoked_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200

    # a user without an OIDC identity is not affected
    plain_credentials = {
        "identifier": "logingate-plain@example.com",
        "password": "SecurePass123",
    }
    assert client.post("/api/v1/auth/login", json=plain_credentials).status_code == 200


def test_otp_refusal_keeps_the_challenge_consumed(client, email_outbox, sms_outbox):
    from backend.services.auth_service import FederationReauthenticationRequired

    fed = _persona(client, email_outbox, email="otpgate-fed@example.com", name="Fed")
    identity_id = _graft_oidc_identity(fed.user_id, fed.organization_id)
    with inspection_session_factory()() as db:
        db.add(
            AuthIdentity(
                user_id=fed.user_id,
                provider=IdentityProvider.PHONE,
                subject="+989121230001",
                verified_at=datetime.now(UTC),
                profile={},
            )
        )
        identity = db.get(AuthIdentity, identity_id)
        identity.verified_at = datetime.now(UTC) - timedelta(minutes=16)
        db.commit()

    requested = client.post("/api/v1/auth/otp/request", json={"phone": "09121230001"})
    assert requested.status_code == 202, requested.text
    challenge_id = requested.json()["challenge_id"]
    code = sms_outbox[-1]["code"]

    refused = client.post(
        "/api/v1/auth/otp/verify", json={"challenge_id": challenge_id, "code": code}
    )
    assert refused.status_code == 401, refused.text
    assert _error(refused) == "federation_reauthentication_required"
    # the commit stored the consumed challenge: the same code is dead
    replay = client.post(
        "/api/v1/auth/otp/verify", json={"challenge_id": challenge_id, "code": code}
    )
    assert replay.status_code == 400 and _error(replay) == "invalid_otp"


def test_google_callback_refusal_redirects_to_login(client, email_outbox, monkeypatch):
    from backend.routers.v1 import auth as auth_router
    from backend.services.auth_service import FederationReauthenticationRequired

    fed = _persona(client, email_outbox, email="googlegate-fed@example.com", name="Fed")
    with inspection_session_factory()() as db:
        user = db.get(User, fed.user_id)
        assert user is not None

    async def fake_google_oauth(_db, *, state, code, settings):
        return user, "/app"

    monkeypatch.setattr(auth_router, "complete_google_oauth", fake_google_oauth)

    def refused_login(*_args, **_kwargs):
        raise FederationReauthenticationRequired(
            "Sign in again to confirm your identity provider account"
        )

    monkeypatch.setattr(auth_router, "create_login_session", refused_login)
    response = client.get(
        "/api/v1/auth/google/callback",
        params={"state": "s", "code": "c"},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    assert response.headers["location"].endswith(
        "/login?error=federation_reauthentication_required"
    )


# --- the periodic sweep ---------------------------------------------------------------


def _stale_identity(organization_id: str, minutes: int) -> tuple[str, str]:
    return _make_oidc_user(
        organization_id, verified_at=datetime.now(UTC) - timedelta(minutes=minutes)
    )


def test_the_sweep_checks_stale_identities_oldest_first_within_the_limit(client, email_outbox):
    owner = _persona(client, email_outbox, email="sweep-owner@example.com", name="Owner")
    org = owner.organization_id
    _fresh_user, _fresh_identity = _stale_identity(org, 5)
    _mid_user, _mid_identity = _stale_identity(org, 11)
    _old_user, old_identity = _stale_identity(org, 12)
    _oldest_user, oldest_identity = _stale_identity(org, 13)

    checker = FakeChecker()
    with inspection_session_factory()() as db:
        counts = revalidate_stale_federated_identities(db, checker=checker, limit=2)
        db.commit()
    assert counts == {"active": 2, "gone": 0, "unavailable": 0}
    assert checker.checked == [oldest_identity, old_identity]


def test_one_raising_identity_does_not_stop_the_sweep(client, email_outbox):
    owner = _persona(client, email_outbox, email="sweep2-owner@example.com", name="Owner")
    org = owner.organization_id
    _raising_user, raising = _stale_identity(org, 13)
    _active_user, _active = _stale_identity(org, 12)
    _gone_user, gone = _stale_identity(org, 11)

    checker = FakeChecker()
    checker.answer(raising, ValueError("userinfo refused"))
    checker.answer(gone, FederatedIdentityGone())
    with inspection_session_factory()() as db:
        counts = revalidate_stale_federated_identities(db, checker=checker, limit=10)
        db.commit()
    assert counts == {"active": 1, "gone": 1, "unavailable": 1}
    with inspection_session_factory()() as db:
        assert db.get(AuthIdentity, gone).revoked_at is not None
        assert db.get(AuthIdentity, raising).revoked_at is None
    assert _audit_events("federation.identity.deprovisioned")


def test_the_beat_schedule_has_the_federation_entry():
    from backend.celery_app import (
        FEDERATION_REVALIDATION_TASK,
        PROCESSING_QUEUE,
        celery_app,
    )
    from backend.jobs import federation_revalidation as job

    entry = celery_app.conf.beat_schedule["federation-revalidation"]
    assert entry["task"] == FEDERATION_REVALIDATION_TASK == (
        "backend.jobs.revalidate_federated_identities"
    )
    assert entry["schedule"] == 300.0
    assert entry["options"] == {"queue": PROCESSING_QUEUE, "expires": 300.0}
    assert FEDERATION_REVALIDATION_TASK in celery_app.tasks
    assert callable(job.revalidate_federated_identities_task)


# --- settings ---------------------------------------------------------------------------


def _federation_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        environment="test",
        deployment_mode="cloud",
        database_url=f"sqlite:///{tmp_path}/federation-settings.db",
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        auto_create_schema=False,
        tasks_eager=True,
        storage_backend="local",
        storage_local_root=str(tmp_path / "objects"),
        upload_quarantine_dir=str(tmp_path / "quarantine"),
        upload_dir=str(tmp_path / "uploads"),
        _env_file=None,
        **overrides,
    )


def test_federation_settings_defaults_pass_and_bad_arithmetic_fails(tmp_path):
    defaults = _federation_settings(tmp_path)
    assert defaults.federation_max_unconfirmed_minutes == 15
    assert defaults.federation_revalidation_max_age_minutes == 10
    assert defaults.federation_revalidation_interval_seconds == 300
    with pytest.raises(ValidationError):
        _federation_settings(tmp_path, federation_max_unconfirmed_minutes=14)
    with pytest.raises(ValidationError):
        _federation_settings(tmp_path, access_token_minutes=16)
    with pytest.raises(ValidationError):
        _federation_settings(tmp_path, federation_revalidation_interval_seconds=301)


# --- attribution stays owner/admin only ------------------------------------------------


def test_a_mapped_member_cannot_attribute_but_a_mapped_admin_can(client, email_outbox):
    owner = _persona(client, email_outbox, email="attr-owner@example.com", name="Owner")
    org = owner.organization_id
    team = _create_workspace(owner, org, "Team")
    meeting_id = _insert_meeting(team, created_by=owner.user_id, title="board call")
    _mapping(org, team, "member-group", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, team, "admin-group", WorkspaceRole.ADMIN, created_by=owner.user_id)
    # a password-login persona grafted with the federated identity
    fed = _persona(client, email_outbox, email="attr-fed@example.com", name="Fed")
    identity_id = _graft_oidc_identity(fed.user_id, org)

    assert _sync(identity_id, ("member-group",)).granted == (team,)
    denied = fed.patch(
        f"/api/v1/meetings/{meeting_id}",
        workspace_id=team,
        json={"agent_user_id": owner.user_id},
    )
    assert denied.status_code == 403, denied.text
    assert _error(denied) == "attribution_change_denied"

    promoted = _sync(identity_id, ("member-group", "admin-group"))
    assert promoted.role_changed == (team,)
    allowed = fed.patch(
        f"/api/v1/meetings/{meeting_id}",
        workspace_id=team,
        json={"agent_user_id": owner.user_id},
    )
    assert allowed.status_code == 200, allowed.text


# --- the schema -------------------------------------------------------------------------


def test_the_schema_defaults_and_checks(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/schema.db")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, is_active, system_role, locale, "
                "session_version, created_at, updated_at) "
                "VALUES ('u1', 'u1@example.com', 'U', 1, 'user', 'fa', 1, :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status, created_at, updated_at) "
                "VALUES ('o1', 'O', 'o', 'active', :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, name, slug, status, "
                "record_scope, retention_days, retention_mode, delete_audio_after_processing, "
                "pii_redaction_classes, settings_json, created_at, updated_at) "
                "VALUES ('w1', 'o1', 'W', 'w', 'active', 'WORKSPACE', 90, 'FINITE', 0, '[]', "
                "'{}', :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO memberships (id, workspace_id, user_id, role, is_active, "
                "valid_from, created_at, updated_at) "
                "VALUES ('m1', 'w1', 'u1', 'MEMBER', 1, :now, :now, :now)"
            ),
            {"now": now},
        )
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT source FROM memberships WHERE id = 'm1'")).scalar_one()
            == "direct"
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            # a different user, so only ck_membership_source can refuse this
            connection.execute(
                text(
                    "INSERT INTO memberships (id, workspace_id, user_id, role, is_active, "
                    "source, valid_from, created_at, updated_at) "
                    "VALUES ('m2', 'w1', 'u2', 'MEMBER', 1, 'magic', :now, :now, :now)"
                ),
                {"now": now},
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO federation_group_mappings (id, organization_id, issuer, "
                    "group_name, workspace_id, role, created_by_user_id, created_at, "
                    "updated_at) VALUES ('g1', 'o1', :issuer, 'grp', 'w1', 'OWNER', 'u1', "
                    ":now, :now)"
                ),
                {"issuer": ISSUER, "now": now},
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO federation_group_mappings (id, organization_id, issuer, "
                "group_name, workspace_id, role, created_by_user_id, created_at, updated_at) "
                "VALUES ('g2', 'o1', :issuer, 'grp', 'w1', 'ADMIN', 'u1', :now, :now)"
            ),
            {"issuer": ISSUER, "now": now},
        )
    engine.dispose()
