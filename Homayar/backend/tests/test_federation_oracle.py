from __future__ import annotations

import importlib
import re
from dataclasses import is_dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    AuthIdentity,
    IdentityProvider,
    LoginSession,
    Meeting,
    Membership,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.routers.v1 import members as members_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user
from backend.tests.test_authorization_matrix import _insert_meeting
from backend.tests.test_organizations import Persona, _grant, _persona

ISSUER = "https://idp.example.test/realms/acme"
OTHER_ISSUER = "https://idp.example.test/realms/other"
SIGN_IN = "SecurePass123"
REAUTH = "federation_reauthentication_required"
HOM77_REVISION = "5f3a91c0d7b2"
RESULT_FIELDS = ("granted", "role_changed", "removed", "kept_direct", "skipped_seat_limit")


def _fed():
    return importlib.import_module("backend.services.federation_service")


def _mapping_model():
    return importlib.import_module("backend.database.saas_models").FederationGroupMapping


def _set_checker(checker) -> None:
    try:
        module = _fed()
    except ModuleNotFoundError:
        return
    module.set_federated_identity_checker(checker)


@pytest.fixture(autouse=True)
def _checker_reset():
    _set_checker(None)
    yield
    _set_checker(None)


class FakeChecker:
    def __init__(self, default=None, by_subject=None):
        self.default = default
        self.by_subject = dict(by_subject or {})
        self.calls: list[str] = []

    def check(self, identity):
        self.calls.append(identity.subject)
        answer = self.by_subject.get(identity.subject, self.default)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _active(*groups: str):
    return _fed().FederatedIdentityActive(groups=frozenset(groups))


def _gone():
    return _fed().FederatedIdentityGone()


def _unavailable():
    return _fed().FederatedIdentityUnavailable()


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _close(left: datetime | None, right: datetime, seconds: float = 2.0) -> bool:
    return left is not None and abs((_utc(left) - right).total_seconds()) <= seconds


def _code(response) -> str:
    return response.json()["error"]["code"]


def _organization(slug: str) -> str:
    with inspection_session_factory()() as db:
        organization = Organization(name=slug, slug=slug)
        db.add(organization)
        db.commit()
        return organization.id


def _workspace(organization_id: str, slug: str, *, status: str = "active") -> str:
    with inspection_session_factory()() as db:
        workspace = Workspace(organization_id=organization_id, name=slug, slug=slug, status=status)
        db.add(workspace)
        db.commit()
        return workspace.id


def _user(email: str) -> str:
    with inspection_session_factory()() as db:
        user = User(
            email=email,
            display_name=email.split("@")[0],
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        db.commit()
        return user.id


def _org_member(organization_id: str, user_id: str) -> None:
    with inspection_session_factory()() as db:
        db.add(
            OrganizationMembership(
                organization_id=organization_id,
                user_id=user_id,
                role=OrganizationRole.ANALYST,
            )
        )
        db.commit()


def _subject(user_id: str, issuer: str = ISSUER) -> str:
    return f"{issuer}|{user_id}"


def _identity(
    user_id: str,
    organization_id: str,
    *,
    issuer: str = ISSUER,
    groups: list[str] | None = None,
    minutes_ago: float = 0.0,
    provider: IdentityProvider = IdentityProvider.OIDC,
    profile: dict | None = None,
    revoked: bool = False,
) -> str:
    now = datetime.now(UTC)
    data = (
        dict(profile)
        if profile is not None
        else {"issuer": issuer, "organization_id": organization_id}
    )
    if groups is not None:
        data["groups"] = sorted(groups)
    with inspection_session_factory()() as db:
        identity = AuthIdentity(
            user_id=user_id,
            provider=provider,
            subject=_subject(user_id, issuer),
            verified_at=now - timedelta(minutes=minutes_ago),
            profile=data,
        )
        if revoked:
            identity.revoked_at = now - timedelta(minutes=1)
        db.add(identity)
        db.commit()
        return identity.id


def _identity_row(identity_id: str) -> AuthIdentity:
    with inspection_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        assert identity is not None
        db.expunge(identity)
        return identity


def _set_identity(identity_id: str, **values) -> None:
    with inspection_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        for name, value in values.items():
            setattr(identity, name, value)
        db.commit()


def _mapping(
    organization_id: str,
    workspace_id: str,
    group: str,
    role: WorkspaceRole,
    *,
    created_by: str,
    issuer: str = ISSUER,
    revoked: bool = False,
) -> str:
    model = _mapping_model()
    with inspection_session_factory()() as db:
        row = model(
            organization_id=organization_id,
            issuer=issuer,
            group_name=group,
            workspace_id=workspace_id,
            role=role,
            created_by_user_id=created_by,
        )
        if revoked:
            row.revoked_at = datetime.now(UTC)
            row.revoked_by_user_id = created_by
        db.add(row)
        db.commit()
        return row.id


def _membership(
    workspace_id: str,
    user_id: str,
    role: WorkspaceRole,
    *,
    source: str | None = None,
    active: bool = True,
) -> str:
    with inspection_session_factory()() as db:
        row = Membership(workspace_id=workspace_id, user_id=user_id, role=role, is_active=active)
        if source is not None:
            row.source = source
        db.add(row)
        db.commit()
        return row.id


def _rows(user_id: str) -> dict[str, tuple[str, bool, str]]:
    with inspection_session_factory()() as db:
        rows = db.scalars(select(Membership).where(Membership.user_id == user_id)).all()
        return {row.workspace_id: (row.role.value, row.is_active, row.source) for row in rows}


def _row_ids(user_id: str, workspace_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(Membership.id).where(
                    Membership.user_id == user_id, Membership.workspace_id == workspace_id
                )
            )
        )


def _audit(**filters) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        query = select(AuditEvent)
        for name, value in filters.items():
            query = query.where(getattr(AuditEvent, name) == value)
        return list(db.scalars(query.order_by(AuditEvent.occurred_at.asc())))


def _audit_total() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


def _sync(identity_id: str, groups, *, now: datetime | None = None, confirmed: bool = True):
    fed = _fed()
    with get_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        result = fed.sync_federated_memberships(
            db,
            identity=identity,
            groups=groups,
            now=now or datetime.now(UTC),
            confirmed=confirmed,
        )
        db.commit()
    return result


def _revalidate(identity_id: str, checker, *, now: datetime | None = None) -> str:
    fed = _fed()
    with get_session_factory()() as db:
        identity = db.get(AuthIdentity, identity_id)
        outcome = fed.revalidate_federated_identity(
            db, identity=identity, checker=checker, now=now or datetime.now(UTC)
        )
        db.commit()
    return outcome


def _sweep(checker, settings: Settings, *, limit: int | None = None) -> dict[str, int]:
    fed = _fed()
    extra = {} if limit is None else {"limit": limit}
    with get_session_factory()() as db:
        counts = fed.revalidate_stale_federated_identities(
            db, checker=checker, now=datetime.now(UTC), settings=settings, **extra
        )
        db.commit()
    return {key: counts[key] for key in ("active", "gone", "unavailable")}


def _ids(result, name: str) -> set[str]:
    return set(getattr(result, name))


def _changed(result) -> dict[str, set[str]]:
    return {name: _ids(result, name) for name in RESULT_FIELDS if _ids(result, name)}


def _owner(client, email_outbox, prefix: str) -> tuple[Persona, str]:
    owner = _persona(client, email_outbox, email=f"{prefix}-owner@example.com", name="Owner")
    return owner, owner.organization_id


def _org_workspace(owner: Persona, organization_id: str, name: str) -> str:
    created = owner.post(f"/api/v1/organizations/{organization_id}/workspaces", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _mappings_path(organization_id: str, mapping_id: str | None = None) -> str:
    path = f"/api/v1/organizations/{organization_id}/federation/group-mappings"
    return path if mapping_id is None else f"{path}/{mapping_id}"


def _create(persona: Persona, organization_id: str, workspace_id: str, **body):
    payload = {
        "issuer": ISSUER,
        "group_name": "sales",
        "workspace_id": workspace_id,
        "role": "member",
        **body,
    }
    return persona.post(_mappings_path(organization_id), json=payload)


def _listed(persona: Persona, organization_id: str) -> list[dict]:
    listed = persona.get(_mappings_path(organization_id))
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


def _refresh(client, cookies: dict[str, str]):
    client.cookies.clear()
    return client.post(
        "/api/v1/auth/refresh",
        cookies={REFRESH_COOKIE: cookies[REFRESH_COOKIE], CSRF_COOKIE: cookies[CSRF_COOKIE]},
        headers={"X-CSRF-Token": cookies[CSRF_COOKIE]},
    )


def _me_with(client, access_token: str):
    client.cookies.clear()
    return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})


def _sessions(user_id: str) -> list[tuple[str, datetime | None]]:
    with inspection_session_factory()() as db:
        rows = db.scalars(select(LoginSession).where(LoginSession.user_id == user_id)).all()
        return [(row.id, row.revoked_at) for row in rows]


def _account(user_id: str) -> tuple[int, bool]:
    with inspection_session_factory()() as db:
        user = db.get(User, user_id)
        return user.session_version, user.is_active


def _mint_key(persona: Persona) -> str:
    created = persona.post(
        "/api/v1/api-keys",
        workspace_id=persona.personal_workspace_id,
        json={"name": "fed", "scopes": ["meeting:read"], "expires_in_days": 30},
    )
    assert created.status_code == 201, created.text
    return created.json()["key"]


def _key_get(client, raw_key: str, path: str = "/api/v1/meetings"):
    client.cookies.clear()
    return client.get(path, headers={"Authorization": f"Bearer {raw_key}"})


def _federated_persona(client, email_outbox, prefix: str, organization_id: str, **identity):
    persona = _persona(client, email_outbox, email=f"{prefix}@example.com", name=prefix)
    identity_id = _identity(persona.user_id, organization_id, **identity)
    return persona, identity_id


def test_control_plain_user_refresh_and_api_key_work(client, email_outbox):
    plain = _persona(client, email_outbox, email="control-plain@example.com", name="Plain")
    raw_key = _mint_key(plain)
    assert _key_get(client, raw_key).status_code == 200
    refreshed = _refresh(client, plain.cookies)
    assert refreshed.status_code == 200, refreshed.text
    assert _me_with(client, plain.cookies[ACCESS_COOKIE]).status_code == 200


def test_sync_grants_a_federated_membership_for_a_mapped_group(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "grant")
    sales = _workspace(org, "grant-sales")
    user_id = _user("grant-fed@example.com")
    identity_id = _identity(user_id, org, minutes_ago=30)
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    now = datetime.now(UTC)

    result = _sync(identity_id, ["support", "sales"], now=now)

    assert is_dataclass(result)
    for name in RESULT_FIELDS:
        assert isinstance(getattr(result, name), tuple), name
    with pytest.raises(AttributeError):
        result.granted = ()
    assert _changed(result) == {"granted": {sales}}
    assert _rows(user_id) == {sales: ("member", True, "federated")}
    identity = _identity_row(identity_id)
    assert identity.profile["groups"] == ["sales", "support"]
    assert identity.profile["issuer"] == ISSUER and identity.profile["organization_id"] == org
    assert _close(identity.verified_at, now)
    events = _audit(actor_user_id=user_id)
    assert len(events) == 1
    assert events[0].target_type == "membership"
    assert {"action", "role", "user_id", "issuer"} <= set(events[0].details)
    assert events[0].details["user_id"] == user_id
    assert events[0].details["issuer"] == ISSUER


def test_group_names_match_exactly_and_case_sensitively(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "case")
    lower = _workspace(org, "case-lower")
    upper = _workspace(org, "case-upper")
    user_id = _user("case-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, lower, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, upper, "Sales", WorkspaceRole.MEMBER, created_by=owner.user_id)

    denied = _sync(identity_id, ["SALES", " sales", "sales ", "sales\n"])
    assert _changed(denied) == {}
    assert _rows(user_id) == {}

    allowed = _sync(identity_id, ["sales"])
    assert _changed(allowed) == {"granted": {lower}}
    assert _rows(user_id) == {lower: ("member", True, "federated")}

    both = _sync(identity_id, ["sales", "Sales"])
    assert _changed(both) == {"granted": {upper}}
    assert _rows(user_id) == {
        lower: ("member", True, "federated"),
        upper: ("member", True, "federated"),
    }


def test_issuer_is_compared_exactly(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "issuer")
    trailing = _workspace(org, "issuer-trailing")
    other = _workspace(org, "issuer-other")
    exact = _workspace(org, "issuer-exact")
    user_id = _user("issuer-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(
        org, trailing, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id, issuer=ISSUER + "/"
    )
    _mapping(
        org, other, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id, issuer=OTHER_ISSUER
    )
    _mapping(org, exact, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)

    result = _sync(identity_id, ["sales"])

    assert _changed(result) == {"granted": {exact}}
    assert _rows(user_id) == {exact: ("member", True, "federated")}


def test_a_revoked_mapping_grants_nothing(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "revoked")
    gone = _workspace(org, "revoked-gone")
    live = _workspace(org, "revoked-live")
    user_id = _user("revoked-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, gone, "sales", WorkspaceRole.ADMIN, created_by=owner.user_id, revoked=True)
    _mapping(org, live, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)

    result = _sync(identity_id, ["sales"])

    assert _changed(result) == {"granted": {live}}
    assert _rows(user_id) == {live: ("member", True, "federated")}


def test_the_highest_role_wins_and_roles_follow_the_groups(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "highest")
    main = _workspace(org, "highest-main")
    side = _workspace(org, "highest-side")
    user_id = _user("highest-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, main, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, main, "leads", WorkspaceRole.ADMIN, created_by=owner.user_id)
    _mapping(org, main, "viewers", WorkspaceRole.VIEWER, created_by=owner.user_id)
    _mapping(org, side, "viewers", WorkspaceRole.VIEWER, created_by=owner.user_id)

    first = _sync(identity_id, ["sales", "viewers"])
    assert _changed(first) == {"granted": {main, side}}
    assert _rows(user_id) == {
        main: ("member", True, "federated"),
        side: ("viewer", True, "federated"),
    }
    [row_id] = _row_ids(user_id, main)

    upgraded = _sync(identity_id, ["viewers", "leads", "sales"])
    assert _changed(upgraded) == {"role_changed": {main}}
    assert _rows(user_id)[main] == ("admin", True, "federated")

    downgraded = _sync(identity_id, ["viewers"])
    assert _changed(downgraded) == {"role_changed": {main}}
    assert _rows(user_id)[main] == ("viewer", True, "federated")

    removed = _sync(identity_id, [])
    assert _changed(removed) == {"removed": {main, side}}
    assert _rows(user_id) == {
        main: ("viewer", False, "federated"),
        side: ("viewer", False, "federated"),
    }

    back = _sync(identity_id, ["sales"])
    assert _changed(back) == {"granted": {main}}
    assert _rows(user_id) == {
        main: ("member", True, "federated"),
        side: ("viewer", False, "federated"),
    }
    assert _row_ids(user_id, main) == [row_id]


def test_a_second_sync_with_the_same_groups_changes_nothing(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "idem")
    sales = _workspace(org, "idem-sales")
    leads = _workspace(org, "idem-leads")
    user_id = _user("idem-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, leads, "leads", WorkspaceRole.ADMIN, created_by=owner.user_id)

    first = _sync(identity_id, ["sales", "leads"])
    assert _changed(first) == {"granted": {sales, leads}}
    rows = _rows(user_id)
    events = _audit_total()
    assert len(_audit(actor_user_id=user_id)) == 2

    second = _sync(identity_id, ["sales", "leads"])
    assert _changed(second) == {}
    third = _sync(identity_id, ["leads", "sales", "sales"])
    assert _changed(third) == {}
    assert _rows(user_id) == rows
    assert _audit_total() == events


def test_direct_rows_are_never_touched_and_missing_rows_become_federated(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "direct")
    kept = _workspace(org, "direct-kept")
    paused = _workspace(org, "direct-paused")
    fresh = _workspace(org, "direct-fresh")
    user_id = _user("direct-fed@example.com")
    identity_id = _identity(user_id, org)
    _membership(kept, user_id, WorkspaceRole.VIEWER)
    _membership(paused, user_id, WorkspaceRole.MEMBER, active=False)
    for workspace_id in (kept, paused, fresh):
        _mapping(org, workspace_id, "sales", WorkspaceRole.ADMIN, created_by=owner.user_id)

    result = _sync(identity_id, ["sales"])

    assert _changed(result) == {"granted": {fresh}, "kept_direct": {kept, paused}}
    assert _rows(user_id) == {
        kept: ("viewer", True, "direct"),
        paused: ("member", False, "direct"),
        fresh: ("admin", True, "federated"),
    }

    emptied = _sync(identity_id, [])
    assert _changed(emptied) == {"removed": {fresh}}
    assert _rows(user_id) == {
        kept: ("viewer", True, "direct"),
        paused: ("member", False, "direct"),
        fresh: ("admin", False, "federated"),
    }


def test_an_owner_row_is_never_touched(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "ownerrow")
    identity_id = _identity(owner.user_id, org)
    _mapping(
        org, owner.personal_workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id
    )

    result = _sync(identity_id, ["sales"])

    assert _changed(result) == {"kept_direct": {owner.personal_workspace_id}}
    assert _rows(owner.user_id)[owner.personal_workspace_id] == ("owner", True, "direct")
    _sync(identity_id, [])
    assert _rows(owner.user_id)[owner.personal_workspace_id] == ("owner", True, "direct")


def test_other_organizations_and_inactive_workspaces_are_out_of_reach(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "reach")
    other_org = _organization("reach-other-org")
    own = _workspace(org, "reach-own")
    off = _workspace(org, "reach-off", status="suspended")
    foreign = _workspace(other_org, "reach-foreign")
    foreign_mapped = _workspace(other_org, "reach-foreign-mapped")
    foreign_kept = _workspace(other_org, "reach-foreign-kept")
    user_id = _user("reach-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, own, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, off, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, foreign, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(other_org, foreign_mapped, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _membership(foreign_kept, user_id, WorkspaceRole.MEMBER, source="federated")

    result = _sync(identity_id, ["sales"])

    assert _changed(result) == {"granted": {own}}
    assert _rows(user_id) == {
        own: ("member", True, "federated"),
        foreign_kept: ("member", True, "federated"),
    }

    emptied = _sync(identity_id, [])
    assert _changed(emptied) == {"removed": {own}}
    assert _rows(user_id) == {
        own: ("member", False, "federated"),
        foreign_kept: ("member", True, "federated"),
    }


def test_a_full_workspace_is_skipped_and_the_others_still_apply(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "seat")
    full = owner.personal_workspace_id
    open_workspace = _workspace(org, "seat-open")
    user_id = _user("seat-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, full, "leads", WorkspaceRole.ADMIN, created_by=owner.user_id)
    _mapping(org, open_workspace, "leads", WorkspaceRole.ADMIN, created_by=owner.user_id)
    _mapping(org, full, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)

    skipped = _sync(identity_id, ["leads"])
    assert _changed(skipped) == {"granted": {open_workspace}, "skipped_seat_limit": {full}}
    assert _rows(user_id) == {open_workspace: ("admin", True, "federated")}
    skip_events = [
        event for event in _audit(actor_user_id=user_id) if event.target_type == "workspace"
    ]
    assert len(skip_events) == 1

    member = _sync(identity_id, ["sales"])
    assert _changed(member) == {"granted": {full}, "removed": {open_workspace}}
    assert _rows(user_id) == {
        full: ("member", True, "federated"),
        open_workspace: ("admin", False, "federated"),
    }

    promoted = _sync(identity_id, ["sales", "leads"])
    assert _changed(promoted) == {"granted": {open_workspace}, "skipped_seat_limit": {full}}
    assert _rows(user_id) == {
        full: ("member", True, "federated"),
        open_workspace: ("admin", True, "federated"),
    }


def test_a_malformed_identity_raises_and_changes_nothing(client, email_outbox):
    fed = _fed()
    owner, org = _owner(client, email_outbox, "malformed")
    sales = _workspace(org, "malformed-sales")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    no_issuer = _user("malformed-a@example.com")
    no_org = _user("malformed-b@example.com")
    good = _user("malformed-c@example.com")
    no_issuer_identity = _identity(no_issuer, org, profile={"organization_id": org})
    no_org_identity = _identity(no_org, org, profile={"issuer": ISSUER})
    good_identity = _identity(good, org)

    for identity_id in (no_issuer_identity, no_org_identity):
        with pytest.raises(fed.FederationIdentityError):
            _sync(identity_id, ["sales"])
    assert _rows(no_issuer) == {} and _rows(no_org) == {}

    assert _changed(_sync(good_identity, ["sales"])) == {"granted": {sales}}


def test_an_unconfirmed_sync_keeps_verified_at_and_the_stored_groups(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "unconfirmed")
    sales = _workspace(org, "unconfirmed-sales")
    leads = _workspace(org, "unconfirmed-leads")
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, leads, "leads", WorkspaceRole.MEMBER, created_by=owner.user_id)
    user_id = _user("unconfirmed-fed@example.com")
    identity_id = _identity(user_id, org, groups=["sales"], minutes_ago=30)
    before = _identity_row(identity_id)

    result = _sync(identity_id, ["leads"], confirmed=False)

    assert _changed(result) == {"granted": {leads}}
    after = _identity_row(identity_id)
    assert after.profile["groups"] == ["sales"]
    assert _utc(after.verified_at) == _utc(before.verified_at)

    now = datetime.now(UTC)
    _sync(identity_id, ["sales"], now=now)
    confirmed = _identity_row(identity_id)
    assert confirmed.profile["groups"] == ["sales"]
    assert _close(confirmed.verified_at, now)


def test_a_workspace_role_change_makes_the_row_direct_and_it_sticks(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "rolechange")
    workspace_id = _org_workspace(owner, org, "Role Change")
    user_id = _user("rolechange-fed@example.com")
    identity_id = _identity(user_id, org)
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _sync(identity_id, ["sales"])
    [membership_id] = _row_ids(user_id, workspace_id)

    changed = owner.patch(
        f"/api/v1/members/{membership_id}", workspace_id=workspace_id, json={"role": "viewer"}
    )
    assert changed.status_code == 200, changed.text
    assert _rows(user_id) == {workspace_id: ("viewer", True, "direct")}

    again = _sync(identity_id, ["sales"])
    assert _changed(again) == {"kept_direct": {workspace_id}}
    assert _rows(user_id) == {workspace_id: ("viewer", True, "direct")}
    _sync(identity_id, [])
    assert _rows(user_id) == {workspace_id: ("viewer", True, "direct")}


def test_a_workspace_removal_sticks_and_an_untouched_row_comes_back(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "removal")
    workspace_id = _org_workspace(owner, org, "Removal")
    removed_user = _user("removal-a@example.com")
    untouched_user = _user("removal-b@example.com")
    removed_identity = _identity(removed_user, org)
    untouched_identity = _identity(untouched_user, org)
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _sync(removed_identity, ["sales"])
    _sync(untouched_identity, ["sales"])
    [membership_id] = _row_ids(removed_user, workspace_id)

    deleted = owner.delete(f"/api/v1/members/{membership_id}", workspace_id=workspace_id)
    assert deleted.status_code == 204, deleted.text
    assert _rows(removed_user) == {workspace_id: ("member", False, "direct")}

    denied = _sync(removed_identity, ["sales"])
    assert workspace_id not in _ids(denied, "granted")
    assert _rows(removed_user) == {workspace_id: ("member", False, "direct")}
    assert _row_ids(removed_user, workspace_id) == [membership_id]

    _sync(untouched_identity, [])
    assert _rows(untouched_user) == {workspace_id: ("member", False, "federated")}
    allowed = _sync(untouched_identity, ["sales"])
    assert _changed(allowed) == {"granted": {workspace_id}}
    assert _rows(untouched_user) == {workspace_id: ("member", True, "federated")}


def test_organization_install_and_removal_make_the_row_direct(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "orgroute")
    workspace_id = _org_workspace(owner, org, "Org Route")
    installed, installed_identity = _federated_persona(client, email_outbox, "orgroute-a", org)
    withdrawn, withdrawn_identity = _federated_persona(client, email_outbox, "orgroute-b", org)
    _grant(owner, org, installed, "analyst")
    _grant(owner, org, withdrawn, "analyst")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _sync(installed_identity, ["sales"])
    _sync(withdrawn_identity, ["sales"])

    install = owner.post(
        f"/api/v1/organizations/{org}/workspaces/{workspace_id}/members",
        json={"user_id": installed.user_id, "role": "viewer"},
    )
    assert install.status_code == 201, install.text
    assert _rows(installed.user_id)[workspace_id] == ("viewer", True, "direct")
    _sync(installed_identity, ["sales"])
    assert _rows(installed.user_id)[workspace_id] == ("viewer", True, "direct")
    _sync(installed_identity, [])
    assert _rows(installed.user_id)[workspace_id] == ("viewer", True, "direct")

    [membership_id] = _row_ids(withdrawn.user_id, workspace_id)
    removal = owner.delete(
        f"/api/v1/organizations/{org}/workspaces/{workspace_id}/members/{membership_id}"
    )
    assert removal.status_code == 204, removal.text
    assert _rows(withdrawn.user_id)[workspace_id] == ("member", False, "direct")
    _sync(withdrawn_identity, ["sales"])
    assert _rows(withdrawn.user_id)[workspace_id] == ("member", False, "direct")


def test_accepting_an_invitation_makes_the_row_direct(client, email_outbox, monkeypatch):
    _fed()
    delivered: dict[str, str] = {}

    async def capture_invitation(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture_invitation)
    owner, org = _owner(client, email_outbox, "invite")
    workspace_id = _org_workspace(owner, org, "Invite")
    invited, identity_id = _federated_persona(client, email_outbox, "invite-fed", org)
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _sync(identity_id, ["sales"])
    _sync(identity_id, [])
    assert _rows(invited.user_id)[workspace_id] == ("member", False, "federated")

    invitation = owner.post(
        "/api/v1/members/invitations",
        workspace_id=workspace_id,
        json={"email": invited.email, "role": "member"},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": delivered[invited.email]},
        cookies=invited.cookies,
        headers={"X-CSRF-Token": invited.cookies[CSRF_COOKIE]},
    )
    assert accepted.status_code == 200, accepted.text
    assert _rows(invited.user_id)[workspace_id] == ("member", True, "direct")

    _sync(identity_id, [])
    assert _rows(invited.user_id)[workspace_id] == ("member", True, "direct")


def test_mapping_routes_create_list_and_revoke(client, email_outbox):
    owner, org = _owner(client, email_outbox, "routes")
    admin = _persona(client, email_outbox, email="routes-admin@example.com", name="Admin")
    _grant(owner, org, admin, "admin")
    workspace_id = _org_workspace(owner, org, "Routes")

    created = _create(owner, org, workspace_id, group_name="leads", role="admin")
    assert created.status_code == 201, created.text
    body = created.json()
    assert {
        "id",
        "organization_id",
        "issuer",
        "group_name",
        "workspace_id",
        "role",
        "created_by_user_id",
        "created_at",
    } <= set(body)
    assert (
        body["organization_id"],
        body["issuer"],
        body["group_name"],
        body["workspace_id"],
        body["role"],
        body["created_by_user_id"],
    ) == (org, ISSUER, "leads", workspace_id, "admin", owner.user_id)
    created_events = _audit(target_type="federation_group_mapping", target_id=body["id"])
    assert len(created_events) == 1 and created_events[0].actor_user_id == owner.user_id

    upper = _create(owner, org, workspace_id, group_name="Sales")
    lower = _create(admin, org, workspace_id, group_name="sales", role="viewer")
    assert upper.status_code == 201, upper.text
    assert lower.status_code == 201, lower.text
    assert lower.json()["created_by_user_id"] == admin.user_id

    listed = _listed(owner, org)
    assert [item["id"] for item in listed] == [body["id"], upper.json()["id"], lower.json()["id"]]
    assert [item["id"] for item in _listed(admin, org)] == [item["id"] for item in listed]

    revoked = owner.delete(_mappings_path(org, body["id"]))
    assert revoked.status_code == 204, revoked.text
    assert [item["id"] for item in _listed(owner, org)] == [upper.json()["id"], lower.json()["id"]]
    assert len(_audit(target_type="federation_group_mapping", target_id=body["id"])) == 2
    with inspection_session_factory()() as db:
        row = db.get(_mapping_model(), body["id"])
        assert row.revoked_at is not None and row.revoked_by_user_id == owner.user_id

    twice = owner.delete(_mappings_path(org, body["id"]))
    assert twice.status_code == 404 and _code(twice) == "group_mapping_not_found"
    unknown = owner.delete(_mappings_path(org, "00000000-0000-4000-8000-000000000000"))
    assert unknown.status_code == 404 and _code(unknown) == "group_mapping_not_found"


def test_the_mapping_body_is_strict(client, email_outbox):
    owner, org = _owner(client, email_outbox, "strict")
    workspace_id = _org_workspace(owner, org, "Strict")
    accepted = 0

    for role in ("owner", "OWNER", "Admin", "", "superuser"):
        refused = _create(owner, org, workspace_id, group_name=f"role-{role}", role=role)
        assert refused.status_code == 422, (role, refused.text)
    for role in ("admin", "member", "viewer"):
        allowed = _create(owner, org, workspace_id, group_name=f"role-{role}", role=role)
        assert allowed.status_code == 201, (role, allowed.text)
        accepted += 1

    for issuer in (" " + ISSUER, ISSUER + " ", ISSUER + "\n", "", "https://" + "a" * 505):
        refused = _create(owner, org, workspace_id, issuer=issuer, group_name="issuer-case")
        assert refused.status_code == 422, (issuer[:20], refused.text)
    longest_issuer = "https://" + "a" * 504
    allowed = _create(owner, org, workspace_id, issuer=longest_issuer, group_name="issuer-case")
    assert allowed.status_code == 201, allowed.text
    accepted += 1

    for group in (" sales", "sales ", "\tsales", "", "g" * 257):
        refused = _create(owner, org, workspace_id, group_name=group)
        assert refused.status_code == 422, (group[:20], refused.text)
    for group in ("Domain Users", "g" * 256, "0b7c9a52-3f1e-4c2d-9e8f-123456789abc"):
        allowed = _create(owner, org, workspace_id, group_name=group)
        assert allowed.status_code == 201, (group[:20], allowed.text)
        accepted += 1

    smuggled = owner.post(
        _mappings_path(org),
        json={
            "issuer": ISSUER,
            "group_name": "extra",
            "workspace_id": workspace_id,
            "role": "member",
            "organization_id": org,
        },
    )
    assert smuggled.status_code == 422, smuggled.text
    for missing in ("issuer", "group_name", "workspace_id", "role"):
        payload = {
            "issuer": ISSUER,
            "group_name": f"missing-{missing}",
            "workspace_id": workspace_id,
            "role": "member",
        }
        payload.pop(missing)
        refused = owner.post(_mappings_path(org), json=payload)
        assert refused.status_code == 422, (missing, refused.text)
    for null_field in ("issuer", "group_name", "role"):
        payload = {
            "issuer": ISSUER,
            "group_name": "nulls",
            "workspace_id": workspace_id,
            "role": "member",
            null_field: None,
        }
        refused = owner.post(_mappings_path(org), json=payload)
        assert refused.status_code == 422, (null_field, refused.text)

    assert len(_listed(owner, org)) == accepted


def test_mapping_routes_refuse_foreign_workspaces_roles_and_duplicates(client, email_outbox):
    owner, org = _owner(client, email_outbox, "deny")
    admin = _persona(client, email_outbox, email="deny-admin@example.com", name="Admin")
    analyst = _persona(client, email_outbox, email="deny-analyst@example.com", name="Analyst")
    executive = _persona(client, email_outbox, email="deny-exec@example.com", name="Exec")
    outsider = _persona(client, email_outbox, email="deny-outsider@example.com", name="Out")
    _grant(owner, org, admin, "admin")
    _grant(owner, org, analyst, "analyst")
    _grant(owner, org, executive, "executive")
    workspace_id = _org_workspace(owner, org, "Deny")
    other_org = outsider.organization_id

    foreign = _create(owner, org, outsider.personal_workspace_id)
    assert foreign.status_code == 404 and _code(foreign) == "workspace_not_found", foreign.text
    unknown = _create(owner, org, "00000000-0000-4000-8000-000000000000")
    assert unknown.status_code == 404 and _code(unknown) == "workspace_not_found", unknown.text
    allowed = _create(owner, org, workspace_id)
    assert allowed.status_code == 201, allowed.text

    for persona in (analyst, executive):
        refused = _create(persona, org, workspace_id, group_name="leads")
        assert refused.status_code == 403 and _code(refused) == "permission_denied", refused.text
        assert persona.get(_mappings_path(org)).status_code == 403
        delete = persona.delete(_mappings_path(org, allowed.json()["id"]))
        assert delete.status_code == 403, delete.text
    by_admin = _create(admin, org, workspace_id, group_name="leads")
    assert by_admin.status_code == 201, by_admin.text

    assert outsider.get(_mappings_path(org)).status_code == 404
    assert _create(outsider, org, workspace_id, group_name="x").status_code == 404

    duplicate = _create(owner, org, workspace_id)
    assert duplicate.status_code == 409 and _code(duplicate) == "group_mapping_exists"
    other_workspace = _org_workspace(owner, org, "Deny Two")
    assert _create(owner, org, other_workspace).status_code == 201
    assert owner.delete(_mappings_path(org, allowed.json()["id"])).status_code == 204
    again = _create(owner, org, workspace_id)
    assert again.status_code == 201, again.text

    theirs = _create(outsider, other_org, outsider.personal_workspace_id, group_name="theirs")
    assert theirs.status_code == 201, theirs.text
    cross = owner.delete(_mappings_path(org, theirs.json()["id"]))
    assert cross.status_code == 404 and _code(cross) == "group_mapping_not_found"
    assert [item["id"] for item in _listed(outsider, other_org)] == [theirs.json()["id"]]
    assert theirs.json()["id"] not in {item["id"] for item in _listed(owner, org)}


def test_mapping_routes_refuse_api_keys_and_need_csrf(client, email_outbox):
    owner, org = _owner(client, email_outbox, "keys")
    workspace_id = _org_workspace(owner, org, "Keys")
    raw_key = _mint_key(owner)
    payload = {
        "issuer": ISSUER,
        "group_name": "sales",
        "workspace_id": workspace_id,
        "role": "member",
    }
    bearer = {"Authorization": f"Bearer {raw_key}"}
    client.cookies.clear()

    for method, path, body in (
        ("GET", _mappings_path(org), None),
        ("POST", _mappings_path(org), payload),
        ("DELETE", _mappings_path(org, "00000000-0000-4000-8000-000000000000"), None),
    ):
        refused = client.request(method, path, json=body, headers=bearer)
        assert refused.status_code == 401, (method, refused.text)
        assert _code(refused) == "api_key_not_accepted", method

    no_csrf = client.post(_mappings_path(org), json=payload, cookies=owner.cookies)
    assert no_csrf.status_code == 403 and _code(no_csrf) == "csrf_failed", no_csrf.text
    client.cookies.clear()
    wrong_csrf = client.post(
        _mappings_path(org),
        json=payload,
        cookies=owner.cookies,
        headers={"X-CSRF-Token": "not-the-token"},
    )
    assert wrong_csrf.status_code == 403 and _code(wrong_csrf) == "csrf_failed", wrong_csrf.text
    client.cookies.clear()
    assert _listed(owner, org) == []

    allowed = owner.post(_mappings_path(org), json=payload)
    assert allowed.status_code == 201, allowed.text
    assert len(_listed(owner, org)) == 1


def test_creating_and_revoking_a_mapping_applies_at_once(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "apply")
    workspace_id = _org_workspace(owner, org, "Apply")
    member, identity_id = _federated_persona(
        client, email_outbox, "apply-fed", org, groups=["sales"], minutes_ago=30
    )
    _grant(owner, org, member, "analyst")
    other_issuer_user = _user("apply-other@example.com")
    _org_member(org, other_issuer_user)
    _identity(other_issuer_user, org, issuer=OTHER_ISSUER, groups=["sales"])
    revoked_user = _user("apply-revoked@example.com")
    _org_member(org, revoked_user)
    _identity(revoked_user, org, groups=["sales"], revoked=True)
    before = _identity_row(identity_id)
    assert member.get("/api/v1/meetings", workspace_id=workspace_id).status_code == 403

    created = _create(owner, org, workspace_id)
    assert created.status_code == 201, created.text

    assert member.get("/api/v1/meetings", workspace_id=workspace_id).status_code == 200
    assert _rows(member.user_id)[workspace_id] == ("member", True, "federated")
    assert _rows(other_issuer_user) == {}
    assert _rows(revoked_user) == {}
    after = _identity_row(identity_id)
    assert _utc(after.verified_at) == _utc(before.verified_at)
    assert after.profile["groups"] == ["sales"]

    revoked = owner.delete(_mappings_path(org, created.json()["id"]))
    assert revoked.status_code == 204, revoked.text
    assert member.get("/api/v1/meetings", workspace_id=workspace_id).status_code == 403
    assert _rows(member.user_id)[workspace_id] == ("member", False, "federated")


def test_reapply_counts_the_identities_of_the_organization_and_issuer(client, email_outbox):
    fed = _fed()
    owner, org = _owner(client, email_outbox, "reapply")
    other_org = _organization("reapply-other-org")
    workspace_id = _workspace(org, "reapply-sales")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    with_groups = _user("reapply-a@example.com")
    without_groups = _user("reapply-b@example.com")
    wrong_issuer = _user("reapply-c@example.com")
    revoked = _user("reapply-d@example.com")
    elsewhere = _user("reapply-e@example.com")
    for user_id in (with_groups, without_groups, wrong_issuer, revoked):
        _org_member(org, user_id)
    _identity(with_groups, org, groups=["sales"])
    _identity(without_groups, org)
    _identity(wrong_issuer, org, issuer=OTHER_ISSUER, groups=["sales"])
    _identity(revoked, org, groups=["sales"], revoked=True)
    _identity(elsewhere, other_org, groups=["sales"])

    with get_session_factory()() as db:
        count = fed.reapply_organization_mappings(db, organization_id=org, issuer=ISSUER)
        db.commit()

    assert count == 2
    assert _rows(with_groups) == {workspace_id: ("member", True, "federated")}
    for user_id in (without_groups, wrong_issuer, revoked, elsewhere):
        assert _rows(user_id) == {}, user_id


def test_refresh_with_an_active_identity_applies_the_groups(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "active")
    workspace_id = _workspace(org, "active-sales")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    member, identity_id = _federated_persona(
        client, email_outbox, "active-fed", org, minutes_ago=30
    )
    checker = FakeChecker(default=_active("sales"))
    _set_checker(checker)

    refreshed = _refresh(client, member.cookies)

    assert refreshed.status_code == 200, refreshed.text
    assert checker.calls == [_subject(member.user_id)]
    assert _rows(member.user_id)[workspace_id] == ("member", True, "federated")
    identity = _identity_row(identity_id)
    assert identity.profile["groups"] == ["sales"]
    assert _close(identity.verified_at, datetime.now(UTC), seconds=30)
    assert identity.revoked_at is None
    assert client.get("/api/v1/auth/me").status_code == 200


def test_refresh_when_the_idp_says_gone_deprovisions_and_commits(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "gone")
    workspace_id = _workspace(org, "gone-sales")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    member, identity_id = _federated_persona(client, email_outbox, "gone-fed", org)
    _sync(identity_id, ["sales"])
    personal = member.personal_workspace_id
    version, _ = _account(member.user_id)
    old_access = member.cookies[ACCESS_COOKIE]
    assert _me_with(client, old_access).status_code == 200
    checker = FakeChecker(default=_gone())
    _set_checker(checker)

    refused = _refresh(client, member.cookies)

    assert refused.status_code == 401, refused.text
    assert _code(refused) == REAUTH
    sessions = _sessions(member.user_id)
    assert sessions and all(revoked_at is not None for _, revoked_at in sessions)
    assert _account(member.user_id) == (version + 1, True)
    assert _rows(member.user_id) == {
        workspace_id: ("member", False, "federated"),
        personal: ("owner", True, "direct"),
    }
    assert _identity_row(identity_id).revoked_at is not None
    deprovisioned = [
        event
        for event in _audit(target_type="user", target_id=member.user_id)
        if "deprovision" in event.event_type
    ]
    assert len(deprovisioned) == 1
    assert _me_with(client, old_access).status_code == 401
    assert _refresh(client, member.cookies).status_code == 401

    calls = len(checker.calls)
    client.cookies.clear()
    login_user(client, {"email": member.email, "password": SIGN_IN})
    again = client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}
    )
    assert again.status_code == 200, again.text
    assert len(checker.calls) == calls


@pytest.mark.parametrize("failure", ["unavailable", "no_checker", "raises", "malformed"])
def test_refresh_when_the_idp_does_not_answer_fails_closed_and_revokes_nothing(
    client, email_outbox, failure
):
    _fed()
    owner, org = _owner(client, email_outbox, f"closed-{failure}")
    workspace_id = _workspace(org, f"closed-{failure}-sales")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    member, identity_id = _federated_persona(
        client, email_outbox, f"closed-{failure}-fed", org, minutes_ago=30
    )
    _sync(identity_id, ["sales"], now=datetime.now(UTC) - timedelta(minutes=30))
    if failure == "malformed":
        _set_identity(identity_id, profile={"organization_id": org, "groups": ["sales"]})
    before = _identity_row(identity_id)
    version, _ = _account(member.user_id)
    checker = {
        "unavailable": FakeChecker(default=_unavailable()),
        "no_checker": None,
        "raises": FakeChecker(default=RuntimeError("idp timeout")),
        "malformed": FakeChecker(default=_active("sales")),
    }[failure]
    _set_checker(checker)

    refused = _refresh(client, member.cookies)

    assert refused.status_code == 401, refused.text
    assert _code(refused) == REAUTH
    sessions = _sessions(member.user_id)
    assert sessions and all(revoked_at is None for _, revoked_at in sessions)
    assert _account(member.user_id) == (version, True)
    assert _rows(member.user_id)[workspace_id] == ("member", True, "federated")
    after = _identity_row(identity_id)
    assert after.revoked_at is None
    assert _utc(after.verified_at) == _utc(before.verified_at)
    assert _me_with(client, member.cookies[ACCESS_COOKIE]).status_code == 200

    _set_checker(FakeChecker(default=_active("sales")))
    retried = _refresh(client, member.cookies)
    if failure == "malformed":
        assert retried.status_code == 401 and _code(retried) == REAUTH
    else:
        assert retried.status_code == 200, retried.text


@pytest.mark.parametrize("kind", ["password_only", "other_provider", "revoked_oidc"])
def test_refresh_of_a_non_federated_user_is_unchanged(client, email_outbox, kind):
    _fed()
    owner, org = _owner(client, email_outbox, f"plain-{kind}")
    plain = _persona(client, email_outbox, email=f"plain-{kind}-user@example.com", name="Plain")
    if kind == "other_provider":
        _identity(plain.user_id, org, provider=IdentityProvider.GOOGLE, minutes_ago=600)
    if kind == "revoked_oidc":
        _identity(plain.user_id, org, minutes_ago=600, revoked=True)
    rows = _rows(plain.user_id)
    version, _ = _account(plain.user_id)
    checker = FakeChecker(default=_gone())
    _set_checker(checker)

    refreshed = _refresh(client, plain.cookies)

    assert refreshed.status_code == 200, refreshed.text
    assert checker.calls == []
    assert _account(plain.user_id) == (version, True)
    assert all(revoked_at is None for _, revoked_at in _sessions(plain.user_id))
    assert _rows(plain.user_id) == rows
    assert client.get("/api/v1/auth/me").status_code == 200


def test_an_api_key_of_a_federated_user_needs_a_recent_confirmation(
    client, email_outbox, settings, monkeypatch
):
    _fed()
    owner, org = _owner(client, email_outbox, "apikey")
    member = _persona(client, email_outbox, email="apikey-fed@example.com", name="Fed")
    raw_key = _mint_key(member)
    assert _key_get(client, raw_key).status_code == 200
    identity_id = _identity(member.user_id, org, minutes_ago=16)
    checker = FakeChecker(default=_active())
    _set_checker(checker)

    stale = _key_get(client, raw_key)
    assert stale.status_code == 401, stale.text
    assert _code(stale) == REAUTH

    _set_identity(identity_id, verified_at=datetime.now(UTC) - timedelta(minutes=14))
    assert _key_get(client, raw_key).status_code == 200
    assert checker.calls == []

    _set_identity(identity_id, verified_at=datetime.now(UTC) - timedelta(minutes=16))
    assert _code(_key_get(client, raw_key)) == REAUTH
    monkeypatch.setattr(settings, "federation_max_unconfirmed_minutes", 30)
    assert _key_get(client, raw_key).status_code == 200
    monkeypatch.setattr(settings, "federation_max_unconfirmed_minutes", 15)
    assert _key_get(client, raw_key).status_code == 401

    _set_identity(identity_id, revoked_at=datetime.now(UTC))
    assert _key_get(client, raw_key).status_code == 200


def test_an_api_key_of_a_user_with_another_provider_is_unchanged(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "apikey-other")
    member = _persona(client, email_outbox, email="apikey-other-user@example.com", name="Other")
    raw_key = _mint_key(member)
    _identity(member.user_id, org, provider=IdentityProvider.GOOGLE, minutes_ago=600)
    assert _key_get(client, raw_key).status_code == 200


def test_revalidate_returns_the_idp_answer_and_changes_state_only_on_gone(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "reval")
    workspace_id = _workspace(org, "reval-sales")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    cases = {
        "no_checker": (None, "unavailable"),
        "raises": (FakeChecker(default=RuntimeError("boom")), "unavailable"),
        "unavailable": (FakeChecker(default=_unavailable()), "unavailable"),
        "malformed": (FakeChecker(default=_active("sales")), "unavailable"),
        "active": (FakeChecker(default=_active("sales")), "active"),
        "gone": (FakeChecker(default=_gone()), "gone"),
    }
    users = {}
    for name in cases:
        user_id = _user(f"reval-{name}@example.com")
        _membership(owner.personal_workspace_id, user_id, WorkspaceRole.VIEWER)
        identity_id = _identity(user_id, org, minutes_ago=30)
        if name != "active":
            _sync(identity_id, ["sales"], now=datetime.now(UTC) - timedelta(minutes=30))
        if name == "malformed":
            _set_identity(identity_id, profile={"issuer": ISSUER})
        users[name] = (user_id, identity_id, _identity_row(identity_id), _account(user_id))

    for name, (checker, expected) in cases.items():
        user_id, identity_id, before, account = users[name]
        assert _revalidate(identity_id, checker) == expected, name
        after = _identity_row(identity_id)
        if expected == "unavailable":
            assert after.revoked_at is None, name
            assert _utc(after.verified_at) == _utc(before.verified_at), name
            assert _account(user_id) == account, name
            assert _rows(user_id)[workspace_id] == ("member", True, "federated"), name
    active_user, active_identity, _, _ = users["active"]
    assert _rows(active_user)[workspace_id] == ("member", True, "federated")
    assert _close(_identity_row(active_identity).verified_at, datetime.now(UTC), seconds=30)

    gone_user, gone_identity, _, (version, _) = users["gone"]
    assert _identity_row(gone_identity).revoked_at is not None
    assert _account(gone_user) == (version + 1, True)
    assert _rows(gone_user) == {
        workspace_id: ("member", False, "federated"),
        owner.personal_workspace_id: ("viewer", True, "direct"),
    }
    assert [
        event
        for event in _audit(target_type="user", target_id=gone_user)
        if "deprovision" in event.event_type
    ]


def test_the_sweep_checks_only_stale_identities_oldest_first(client, email_outbox, settings):
    _fed()
    owner, org = _owner(client, email_outbox, "sweep")
    ages = {"m5": 5, "m11": 11, "m30": 30}
    identities = {}
    for name, minutes in ages.items():
        user_id = _user(f"sweep-{name}@example.com")
        identities[name] = (user_id, _identity(user_id, org, minutes_ago=minutes))
    revoked_user = _user("sweep-revoked@example.com")
    _identity(revoked_user, org, minutes_ago=60, revoked=True)
    other_user = _user("sweep-google@example.com")
    _identity(other_user, org, minutes_ago=60, provider=IdentityProvider.GOOGLE)
    fresh_before = _identity_row(identities["m5"][1])

    checker = FakeChecker(default=_active())
    counts = _sweep(checker, settings)

    assert checker.calls == [_subject(identities["m30"][0]), _subject(identities["m11"][0])]
    assert counts == {"active": 2, "gone": 0, "unavailable": 0}
    now = datetime.now(UTC)
    for name in ("m11", "m30"):
        assert _close(_identity_row(identities[name][1]).verified_at, now, seconds=30), name
    assert _utc(_identity_row(identities["m5"][1]).verified_at) == _utc(fresh_before.verified_at)

    _set_identity(identities["m30"][1], verified_at=now - timedelta(minutes=30))
    _set_identity(identities["m11"][1], verified_at=now - timedelta(minutes=11))
    limited = FakeChecker(default=_active())
    assert _sweep(limited, settings, limit=1) == {"active": 1, "gone": 0, "unavailable": 0}
    assert limited.calls == [_subject(identities["m30"][0])]


def test_the_sweep_handles_each_identity_on_its_own(client, email_outbox, settings):
    _fed()
    owner, org = _owner(client, email_outbox, "isolate")
    workspace_id = _workspace(org, "isolate-sales")
    _mapping(org, workspace_id, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    users = {}
    for name, minutes in (("raises", 50), ("gone", 40), ("active", 30), ("malformed", 20)):
        user_id = _user(f"isolate-{name}@example.com")
        identity_id = _identity(user_id, org, minutes_ago=minutes)
        _sync(identity_id, ["sales"], now=datetime.now(UTC) - timedelta(minutes=minutes))
        users[name] = (user_id, identity_id)
    _set_identity(users["malformed"][1], profile={"issuer": ISSUER})
    raising_before = _identity_row(users["raises"][1])
    checker = FakeChecker(
        default=_active("sales"),
        by_subject={
            _subject(users["raises"][0]): RuntimeError("idp down"),
            _subject(users["gone"][0]): _gone(),
        },
    )

    counts = _sweep(checker, settings)

    assert counts == {"active": 1, "gone": 1, "unavailable": 2}
    gone_user, gone_identity = users["gone"]
    assert _identity_row(gone_identity).revoked_at is not None
    assert _rows(gone_user)[workspace_id] == ("member", False, "federated")
    active_user, active_identity = users["active"]
    assert _close(_identity_row(active_identity).verified_at, datetime.now(UTC), seconds=30)
    assert _rows(active_user)[workspace_id] == ("member", True, "federated")
    raising_user, raising_identity = users["raises"]
    raising_after = _identity_row(raising_identity)
    assert raising_after.revoked_at is None
    assert _utc(raising_after.verified_at) == _utc(raising_before.verified_at)
    assert _rows(raising_user)[workspace_id] == ("member", True, "federated")
    assert _identity_row(users["malformed"][1]).revoked_at is None


def test_the_beat_schedules_the_sweep_and_the_task_needs_a_checker(client, email_outbox):
    _fed()
    importlib.import_module("backend.jobs.federation_revalidation")
    from backend.celery_app import celery_app

    entries = [
        entry for entry in celery_app.conf.beat_schedule.values() if "federat" in entry["task"]
    ]
    assert len(entries) == 1, celery_app.conf.beat_schedule
    entry = entries[0]
    assert entry["schedule"] == 300.0
    assert entry["options"]["expires"] == 300.0
    assert entry["task"] in celery_app.tasks
    task = celery_app.tasks[entry["task"]]

    owner, org = _owner(client, email_outbox, "beat")
    user_id = _user("beat-fed@example.com")
    identity_id = _identity(user_id, org, minutes_ago=30)
    before = _identity_row(identity_id)

    task()
    unchanged = _identity_row(identity_id)
    assert unchanged.revoked_at is None
    assert _utc(unchanged.verified_at) == _utc(before.verified_at)

    _set_checker(FakeChecker(default=_gone()))
    task()
    assert _identity_row(identity_id).revoked_at is not None


def test_federation_settings_have_defaults_and_refuse_a_broken_promise():
    defaults = Settings(environment="test", _env_file=None)
    assert defaults.federation_max_unconfirmed_minutes == 15
    assert defaults.federation_revalidation_max_age_minutes == 10
    assert defaults.federation_revalidation_interval_seconds == 300

    for allowed in (
        {
            "federation_revalidation_max_age_minutes": 10,
            "federation_revalidation_interval_seconds": 300,
        },
        {
            "federation_revalidation_max_age_minutes": 14,
            "federation_revalidation_interval_seconds": 60,
        },
        {
            "federation_revalidation_max_age_minutes": 1,
            "federation_revalidation_interval_seconds": 30,
        },
        {"access_token_minutes": 15},
        {"access_token_minutes": 20, "federation_max_unconfirmed_minutes": 20},
        {"federation_max_unconfirmed_minutes": 30, "federation_revalidation_max_age_minutes": 25},
    ):
        built = Settings(environment="test", _env_file=None, **allowed)
        for name, value in allowed.items():
            assert getattr(built, name) == value, allowed

    for refused in (
        {"federation_revalidation_max_age_minutes": 11},
        {"federation_revalidation_interval_seconds": 301},
        {
            "federation_revalidation_max_age_minutes": 14,
            "federation_revalidation_interval_seconds": 61,
        },
        {"access_token_minutes": 16},
        {"access_token_minutes": 21, "federation_max_unconfirmed_minutes": 20},
        {"federation_max_unconfirmed_minutes": 0},
        {"federation_revalidation_max_age_minutes": 0},
        {"federation_revalidation_interval_seconds": 29},
    ):
        with pytest.raises(ValueError):
            Settings(environment="test", _env_file=None, **refused)


def test_a_mapped_member_cannot_change_attribution_and_a_mapped_admin_can(client, email_outbox):
    _fed()
    owner, org = _owner(client, email_outbox, "attr")
    workspace_id = _org_workspace(owner, org, "Attribution")
    meeting_id = _insert_meeting(
        workspace_id, created_by=owner.user_id, title="Call", agent_user_id=owner.user_id
    )
    _mapping(org, workspace_id, "agents", WorkspaceRole.MEMBER, created_by=owner.user_id)
    _mapping(org, workspace_id, "watchers", WorkspaceRole.VIEWER, created_by=owner.user_id)
    _mapping(org, workspace_id, "leads", WorkspaceRole.ADMIN, created_by=owner.user_id)
    member, member_identity = _federated_persona(client, email_outbox, "attr-member", org)
    viewer, viewer_identity = _federated_persona(client, email_outbox, "attr-viewer", org)
    admin, admin_identity = _federated_persona(client, email_outbox, "attr-admin", org)
    _sync(member_identity, ["agents"])
    _sync(viewer_identity, ["watchers"])
    _sync(admin_identity, ["leads"])
    path = f"/api/v1/meetings/{meeting_id}"

    by_member = member.patch(path, workspace_id=workspace_id, json={"agent_user_id": None})
    assert by_member.status_code == 403, by_member.text
    assert _code(by_member) == "attribution_change_denied"
    by_viewer = viewer.patch(path, workspace_id=workspace_id, json={"agent_user_id": None})
    assert by_viewer.status_code == 403, by_viewer.text
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).agent_user_id == owner.user_id

    by_admin = admin.patch(path, workspace_id=workspace_id, json={"agent_user_id": None})
    assert by_admin.status_code == 200, by_admin.text
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).agent_user_id is None


def _schema_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _schema_parents(db: Session) -> tuple[str, str, str, str]:
    organization = Organization(name="Schema", slug="schema")
    db.add(organization)
    db.flush()
    first = Workspace(organization_id=organization.id, name="One", slug="one")
    second = Workspace(organization_id=organization.id, name="Two", slug="two")
    user = User(email="schema@example.com", display_name="Schema")
    db.add_all([first, second, user])
    db.commit()
    return organization.id, first.id, second.id, user.id


def _refused(db: Session, row) -> None:
    db.add(row)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_the_schema_refuses_an_owner_mapping_and_an_unknown_source():
    model = _mapping_model()
    engine = _schema_engine()
    try:
        with Session(engine) as db:
            org, first, second, user_id = _schema_parents(db)

            def mapping(group: str, role: WorkspaceRole, workspace_id: str = first):
                return model(
                    organization_id=org,
                    issuer=ISSUER,
                    group_name=group,
                    workspace_id=workspace_id,
                    role=role,
                    created_by_user_id=user_id,
                )

            _refused(db, mapping("owners", WorkspaceRole.OWNER))
            for role in (WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER):
                db.add(mapping(f"g-{role.value}", role))
                db.commit()

            _refused(db, mapping("g-admin", WorkspaceRole.VIEWER))
            db.add(mapping("g-admin", WorkspaceRole.VIEWER, second))
            db.add(mapping("G-ADMIN", WorkspaceRole.VIEWER))
            db.commit()
            existing = db.scalar(
                select(model).where(model.group_name == "g-admin", model.workspace_id == first)
            )
            existing.revoked_at = datetime.now(UTC)
            existing.revoked_by_user_id = user_id
            db.commit()
            db.add(mapping("g-admin", WorkspaceRole.MEMBER))
            db.commit()

            for source in ("bogus", "Federated", "DIRECT", ""):
                row = Membership(workspace_id=first, user_id=user_id, role=WorkspaceRole.MEMBER)
                row.source = source
                _refused(db, row)
            federated = Membership(workspace_id=first, user_id=user_id, role=WorkspaceRole.MEMBER)
            federated.source = "federated"
            db.add(federated)
            db.commit()
            implicit = Membership(workspace_id=second, user_id=user_id, role=WorkspaceRole.MEMBER)
            db.add(implicit)
            db.commit()
            assert implicit.source == "direct"

            other_user = User(email="schema-raw@example.com", display_name="Raw")
            db.add(other_user)
            db.commit()
            now = datetime.now(UTC).isoformat()
            db.execute(
                text(
                    "INSERT INTO memberships (id, workspace_id, user_id, role, is_active, "
                    "valid_from, created_at, updated_at) "
                    "VALUES (:id, :w, :u, 'MEMBER', 1, :now, :now, :now)"
                ),
                {"id": "raw-membership", "w": first, "u": other_user.id, "now": now},
            )
            db.commit()
            stored = db.execute(
                text("SELECT source FROM memberships WHERE id = 'raw-membership'")
            ).scalar_one()
            assert stored == "direct"

        revoked_at = Base.metadata.tables["auth_identities"].c.revoked_at
        assert revoked_at.nullable is True
    finally:
        engine.dispose()


def _federation_migration():
    from backend.observability.release import PROJECT_ROOT

    folder = PROJECT_ROOT / "backend" / "migrations" / "versions"
    found = [
        path
        for path in sorted(folder.glob("*.py"))
        if "federation_group_mappings" in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return importlib.import_module(f"backend.migrations.versions.{found[0].stem}")


def test_one_migration_adds_the_mapping_table():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from backend.observability.release import PROJECT_ROOT

    module = _federation_migration()
    assert callable(module.upgrade) and callable(module.downgrade)
    assert module.down_revision == HOM77_REVISION
    assert re.fullmatch(r"[0-9a-f]{12}", module.revision), module.revision
    assert module.revision != HOM77_REVISION
    script = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    assert script.get_revision(module.revision) is not None
    # A later head (HOM-93, the retrieval index) may sit on top of this one,
    # but the chain stays single and still contains this revision.
    heads = script.get_heads()
    assert len(heads) == 1
    assert module.revision in {revision.revision for revision in script.walk_revisions()}


def _pg_scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _pg_column(table: str, column: str):
    return _pg_scalar(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c",
        t=table,
        c=column,
    ), _pg_scalar(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c",
        t=table,
        c=column,
    )


@pytest.mark.postgres
def test_on_postgres_the_migration_round_trips(client, email_outbox, monkeypatch, settings):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    module = _federation_migration()
    owner = _persona(client, email_outbox, email="pg-owner@example.com", name="Owner")
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    try:
        command.stamp(config, "head")
        command.downgrade(config, module.down_revision)
        assert _pg_scalar("SELECT to_regclass('public.federation_group_mappings') IS NULL")
        assert _pg_column("memberships", "source")[1] == 0
        assert _pg_column("auth_identities", "revoked_at")[1] == 0

        command.upgrade(config, "head")
        assert _pg_scalar("SELECT to_regclass('public.federation_group_mappings') IS NOT NULL")
        assert _pg_column("auth_identities", "revoked_at")[1] == 1
        assert _pg_scalar(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE oid = 'public.federation_group_mappings'::regclass"
        )
        assert _rows(owner.user_id)[owner.personal_workspace_id] == ("owner", True, "direct")
    finally:
        reset_settings_cache()
