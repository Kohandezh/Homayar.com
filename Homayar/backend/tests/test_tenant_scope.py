"""Two-tenant negative suite across API resources (WP03-T04 / F05).

An intruder with their own workspace must not read, mutate, export, share or
manage anything that belongs to another workspace, whether they present their
own workspace header with a guessed id or the victim's workspace header.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.database.saas_models import ActionItem, Membership, ShareLink, User
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_insights_shares_api import _processed_meeting
from backend.tests.test_meetings_api import _workspace_for_user


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _headers(cookies: dict[str, str], workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]}


def test_intruder_cannot_touch_another_workspaces_resources(
    client, email_outbox, settings, monkeypatch
):
    _, victim_workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    victim_cookies = _cookies(client)
    victim_headers = _headers(victim_cookies, victim_workspace_id)

    assert (
        client.patch(
            "/api/v1/admin/workspace-settings",
            json={"public_share_links_enabled": True},
            headers=victim_headers,
            cookies=victim_cookies,
        ).status_code
        == 200
    )
    created_share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        json={"expires_in_hours": 24, "require_authentication": False},
        headers=victim_headers,
        cookies=victim_cookies,
    )
    assert created_share.status_code == 201, created_share.text
    share_id = created_share.json()["id"]

    with inspection_session_factory()() as db:
        db.add(
            ActionItem(
                workspace_id=victim_workspace_id,
                meeting_id=meeting_id,
                description="Victim action",
                created_by="user",
            )
        )
        db.commit()
        action_id = db.scalar(
            select(ActionItem.id).where(ActionItem.meeting_id == meeting_id)
        )
        victim_membership_id = db.scalar(
            select(Membership.id).where(Membership.workspace_id == victim_workspace_id)
        )
    assert action_id and victim_membership_id

    intruder = register_user(client, email_outbox, email="intruder@example.com")
    intruder_user_id = login_user(client, intruder)["user"]["id"]
    intruder_cookies = _cookies(client)
    with inspection_session_factory()() as db:
        intruder_workspace_id = _workspace_for_user(db, intruder_user_id).id

    def attempts(workspace_id: str):
        headers = _headers(intruder_cookies, workspace_id)
        common = {"headers": headers, "cookies": intruder_cookies}
        return [
            ("GET meeting", client.get(f"/api/v1/meetings/{meeting_id}", **common)),
            ("GET job", client.get(f"/api/v1/meetings/{meeting_id}/job", **common)),
            ("GET recording", client.get(f"/api/v1/meetings/{meeting_id}/recording", **common)),
            ("GET transcript", client.get(f"/api/v1/meetings/{meeting_id}/transcript", **common)),
            ("GET summary", client.get(f"/api/v1/meetings/{meeting_id}/summary", **common)),
            ("GET actions", client.get(f"/api/v1/meetings/{meeting_id}/actions", **common)),
            (
                "GET export",
                client.get(f"/api/v1/meetings/{meeting_id}/export?format=txt", **common),
            ),
            ("GET shares", client.get(f"/api/v1/meetings/{meeting_id}/shares", **common)),
            (
                "POST share",
                client.post(
                    f"/api/v1/meetings/{meeting_id}/shares",
                    json={"expires_in_hours": 1, "require_authentication": False},
                    **common,
                ),
            ),
            (
                "DELETE share",
                client.delete(f"/api/v1/meetings/{meeting_id}/shares/{share_id}", **common),
            ),
            (
                "PATCH action",
                client.patch(
                    f"/api/v1/meetings/{meeting_id}/actions/{action_id}",
                    json={"status": "done"},
                    **common,
                ),
            ),
            (
                "PATCH meeting",
                client.patch(
                    f"/api/v1/meetings/{meeting_id}", json={"title": "hijacked"}, **common
                ),
            ),
            ("DELETE meeting", client.delete(f"/api/v1/meetings/{meeting_id}", **common)),
            (
                "PATCH membership",
                client.patch(
                    f"/api/v1/members/{victim_membership_id}", json={"role": "admin"}, **common
                ),
            ),
        ]

    # Own workspace header + guessed ids: the records must be invisible (404).
    for label, response in attempts(intruder_workspace_id):
        assert response.status_code == 404, f"{label}: {response.status_code} {response.text}"

    # Victim's workspace header without membership: rejected before any lookup.
    for label, response in attempts(victim_workspace_id):
        assert response.status_code in {403, 404}, f"{label}: {response.status_code}"

    # Nothing changed for the victim.
    detail = client.get(
        f"/api/v1/meetings/{meeting_id}", headers=victim_headers, cookies=victim_cookies
    )
    assert detail.status_code == 200 and detail.json()["title"] != "hijacked"
    shares = client.get(
        f"/api/v1/meetings/{meeting_id}/shares", headers=victim_headers, cookies=victim_cookies
    )
    assert shares.status_code == 200
    payload = shares.json()
    listed = payload["items"] if isinstance(payload, dict) else payload
    assert [share["id"] for share in listed] == [share_id]
    with inspection_session_factory()() as db:
        share = db.get(ShareLink, share_id)
        assert share is not None and share.revoked_at is None
        assert db.scalar(select(User).where(User.email == "intruder@example.com")) is not None


def test_removed_member_loses_access_on_the_next_request(
    client, email_outbox, settings, monkeypatch
):
    _, victim_workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    guest = register_user(client, email_outbox, email="guest@example.com")
    guest_user_id = login_user(client, guest)["user"]["id"]
    guest_cookies = _cookies(client)
    headers = _headers(guest_cookies, victim_workspace_id)

    from backend.database.saas_models import WorkspaceRole

    with inspection_session_factory()() as db:
        membership = Membership(
            workspace_id=victim_workspace_id, user_id=guest_user_id, role=WorkspaceRole.MEMBER
        )
        db.add(membership)
        db.commit()
        membership_id = membership.id

    allowed = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers, cookies=guest_cookies)
    assert allowed.status_code == 200, allowed.text

    with inspection_session_factory()() as db:
        row = db.get(Membership, membership_id)
        assert row is not None
        row.is_active = False
        db.commit()

    # Same still-valid session and access token: membership is checked per request.
    denied = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers, cookies=guest_cookies)
    assert denied.status_code == 403
    listing = client.get("/api/v1/meetings", headers=headers, cookies=guest_cookies)
    assert listing.status_code == 403


def test_background_processing_writes_only_into_the_owning_workspace(
    client, email_outbox, settings, monkeypatch
):
    from backend.database.saas_models import (
        AuditEvent,
        MeetingSummary,
        ProcessingStageRun,
        ProviderUsage,
        TranscriptSegment,
    )

    _, victim_workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    with inspection_session_factory()() as db:
        for model in (TranscriptSegment, MeetingSummary, ProcessingStageRun, ProviderUsage):
            rows = db.scalars(select(model).where(model.meeting_id == meeting_id)).all()
            assert rows, model.__name__
            assert {row.workspace_id for row in rows} == {victim_workspace_id}, model.__name__
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.target_id == meeting_id)
        ).all()
        assert audits and {event.workspace_id for event in audits} == {victim_workspace_id}
