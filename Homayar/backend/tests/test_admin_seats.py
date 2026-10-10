"""HOM-167 F1: owner and admin memberships consume the plan's seats.

* the free demo has one seat, which its owner holds: no admin can be added
  (invitation, promotion, delegated installation), while members and viewers
  stay free;
* a pending admin invitation reserves a seat, accepting it consumes the seat,
  revoking it or demoting an admin frees one;
* a limit lowered below the current admins keeps them and refuses new ones;
* an admin membership outside its validity window holds no seat;
* concurrent promotions never exceed the limit (PostgreSQL, application role).
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    Membership,
    Plan,
    PlanVersion,
    Subscription,
    WorkspaceRole,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.routers.v1 import members as members_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.member_service import SeatLimitReachedError, change_member_role
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


class _User:
    def __init__(self, client: TestClient, email_outbox: list[dict[str, str]], email: str):
        client.cookies.clear()
        session = login_user(client, register_user(client, email_outbox, email=email))
        self.client = client
        self.user_id = session["user"]["id"]
        self.personal_workspace_id = session["workspaces"][0]["id"]
        self.cookies = {
            name: value
            for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
            if (value := client.cookies.get(name))
        }

    def call(self, method: str, path: str, workspace_id: str | None = None, **kwargs):
        headers = {"X-CSRF-Token": self.cookies[CSRF_COOKIE]}
        if workspace_id is not None:
            headers["X-Workspace-ID"] = workspace_id
        self.client.cookies.clear()
        return self.client.request(method, path, cookies=self.cookies, headers=headers, **kwargs)


@pytest.fixture
def invitations(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    delivered: dict[str, str] = {}

    async def capture(*, email, raw_token, workspace_name, settings):
        delivered[email] = raw_token
        return True

    monkeypatch.setattr(members_router, "send_invitation_email", capture)
    return delivered


def _pin_seats(workspace_id: str, seats: int) -> None:
    """An operator moves the workspace to a private plan with ``seats`` seats."""
    key = f"seats_{seats}"
    terms = {"seats": seats}
    with inspection_session_factory()() as db:
        if db.get(Plan, key) is None:
            db.add(
                Plan(
                    key=key,
                    name=key,
                    is_public=False,
                    is_active=True,
                    entitlements_json=terms,
                    display_json={},
                    current_version=1,
                )
            )
            db.flush()
            db.add(
                PlanVersion(
                    plan_key=key,
                    version=1,
                    name=key,
                    is_public=False,
                    entitlements_json=terms,
                    source="operator",
                )
            )
            db.flush()
        db.execute(
            update(Subscription)
            .where(Subscription.workspace_id == workspace_id)
            .values(plan_key=key, plan_version=1)
        )
        db.commit()


def _invite(owner: _User, workspace_id: str, email: str, role: str):
    return owner.call(
        "POST",
        "/api/v1/members/invitations",
        workspace_id,
        json={"email": email, "role": role},
    )


def _accept(user: _User, token: str):
    return user.call("POST", "/api/v1/members/invitations/accept", json={"token": token})


def _set_role(owner: _User, workspace_id: str, membership_id: str, role: str):
    return owner.call(
        "PATCH", f"/api/v1/members/{membership_id}", workspace_id, json={"role": role}
    )


def _join(
    owner: _User,
    workspace_id: str,
    user: _User,
    role: str,
    invitations: dict[str, str],
    email: str,
) -> str:
    invited = _invite(owner, workspace_id, email, role)
    assert invited.status_code == 201, invited.text
    accepted = _accept(user, invitations[email])
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["membership"]["id"]


def _roles(workspace_id: str) -> dict[str, str]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.is_active.is_(True)
            )
        )
        return {row.user_id: row.role.value for row in rows}


def _refused(response) -> bool:
    return response.status_code == 409 and response.json()["error"]["code"] == "seat_limit_reached"


def test_the_demo_seat_is_the_owners_so_no_admin_can_be_added(
    client: TestClient, email_outbox: list[dict[str, str]], invitations: dict[str, str]
) -> None:
    owner = _User(client, email_outbox, "seat-owner@example.com")
    member = _User(client, email_outbox, "seat-member@example.com")
    workspace = owner.personal_workspace_id

    assert _refused(_invite(owner, workspace, "seat-admin@example.com", "admin"))
    # Allow-controls: members and viewers need no seat.
    member_id = _join(owner, workspace, member, "member", invitations, "seat-member@example.com")
    assert _invite(owner, workspace, "seat-viewer@example.com", "viewer").status_code == 201

    assert _refused(_set_role(owner, workspace, member_id, "admin"))
    assert _roles(workspace) == {owner.user_id: "owner", member.user_id: "member"}
    assert _set_role(owner, workspace, member_id, "viewer").status_code == 200


def test_pending_admin_invitations_reserve_and_revocation_frees_a_seat(
    client: TestClient, email_outbox: list[dict[str, str]], invitations: dict[str, str]
) -> None:
    owner = _User(client, email_outbox, "reserve-owner@example.com")
    first = _User(client, email_outbox, "reserve-first@example.com")
    member = _User(client, email_outbox, "reserve-member@example.com")
    workspace = owner.personal_workspace_id
    _pin_seats(workspace, 2)  # the owner plus one admin
    member_id = _join(owner, workspace, member, "member", invitations, "reserve-member@example.com")

    pending = _invite(owner, workspace, "reserve-first@example.com", "admin")
    assert pending.status_code == 201, pending.text
    # The pending invitation holds the free seat.
    assert _refused(_invite(owner, workspace, "reserve-second@example.com", "admin"))
    assert _refused(_set_role(owner, workspace, member_id, "admin"))

    revoked = owner.call("DELETE", f"/api/v1/members/invitations/{pending.json()['id']}", workspace)
    assert revoked.status_code == 204
    assert _set_role(owner, workspace, member_id, "admin").status_code == 200
    # Both seats are held now.
    assert _refused(_invite(owner, workspace, "reserve-first@example.com", "admin"))

    # Demoting the admin frees the seat for an invitation that is then accepted.
    assert _set_role(owner, workspace, member_id, "member").status_code == 200
    _join(owner, workspace, first, "admin", invitations, "reserve-first@example.com")
    assert _roles(workspace)[first.user_id] == "admin"
    assert _refused(_set_role(owner, workspace, member_id, "admin"))


def test_accepting_needs_a_free_seat_and_a_lowered_limit_keeps_current_admins(
    client: TestClient, email_outbox: list[dict[str, str]], invitations: dict[str, str]
) -> None:
    owner = _User(client, email_outbox, "lowered-owner@example.com")
    kept = _User(client, email_outbox, "lowered-kept@example.com")
    late = _User(client, email_outbox, "lowered-late@example.com")
    workspace = owner.personal_workspace_id
    _pin_seats(workspace, 3)
    _join(owner, workspace, kept, "admin", invitations, "lowered-kept@example.com")
    assert _invite(owner, workspace, "lowered-late@example.com", "admin").status_code == 201

    _pin_seats(workspace, 2)  # an operator lowers the limit under the pending invitation

    assert _refused(_accept(late, invitations["lowered-late@example.com"]))
    listed = owner.call("GET", "/api/v1/members/invitations", workspace).json()["items"]
    assert {item["email"]: item["status"] for item in listed}["lowered-late@example.com"] == (
        "pending"
    )
    assert _roles(workspace) == {owner.user_id: "owner", kept.user_id: "admin"}


def test_an_admin_outside_its_validity_window_holds_no_seat(
    client: TestClient, email_outbox: list[dict[str, str]], invitations: dict[str, str]
) -> None:
    owner = _User(client, email_outbox, "window-owner@example.com")
    expired = _User(client, email_outbox, "window-expired@example.com")
    member = _User(client, email_outbox, "window-member@example.com")
    workspace = owner.personal_workspace_id
    _pin_seats(workspace, 2)
    expired_id = _join(
        owner, workspace, expired, "admin", invitations, "window-expired@example.com"
    )
    member_id = _join(owner, workspace, member, "member", invitations, "window-member@example.com")
    assert _refused(_set_role(owner, workspace, member_id, "admin"))

    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(Membership.id == expired_id)
            .values(valid_until=datetime.now(UTC) - timedelta(minutes=1))
        )
        db.commit()

    assert _set_role(owner, workspace, member_id, "admin").status_code == 200


@pytest.mark.postgres
def test_concurrent_promotions_never_exceed_the_limit(
    client: TestClient, email_outbox: list[dict[str, str]], invitations: dict[str, str]
) -> None:
    owner = _User(client, email_outbox, "race-owner@example.com")
    one = _User(client, email_outbox, "race-one@example.com")
    two = _User(client, email_outbox, "race-two@example.com")
    workspace = owner.personal_workspace_id
    _pin_seats(workspace, 2)  # one free seat for two concurrent promotions
    targets = [
        _join(owner, workspace, user, "member", invitations, email)
        for user, email in ((one, "race-one@example.com"), (two, "race-two@example.com"))
    ]

    barrier = threading.Barrier(len(targets))
    outcomes: list[str] = []

    def promote(membership_id: str) -> None:
        try:
            with get_session_factory()() as db:
                bind_user_context(db, owner.user_id)
                bind_tenant_context(db, workspace_id=workspace)
                barrier.wait(timeout=15)
                change_member_role(
                    db,
                    workspace_id=workspace,
                    membership_id=membership_id,
                    actor_user_id=owner.user_id,
                    actor_role=WorkspaceRole.OWNER,
                    new_role=WorkspaceRole.ADMIN,
                )
                time.sleep(0.3)  # hold the lock so the other promotion queues behind it
                db.commit()
                outcomes.append("promoted")
        except SeatLimitReachedError:
            outcomes.append("refused")
        except BaseException as exc:  # surfaced by the assertion below
            outcomes.append(repr(exc))

    threads = [threading.Thread(target=promote, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a promotion hung"
    assert sorted(outcomes) == ["promoted", "refused"]
    assert sorted(_roles(workspace).values()) == ["admin", "member", "owner"]
