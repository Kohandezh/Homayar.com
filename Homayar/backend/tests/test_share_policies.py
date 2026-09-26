"""WP03-T06: share contract — audiences, separate grants, revocation, downloads.

Portable behaviour (runs on SQLite and PostgreSQL). The database ceiling for a
share token is proven separately in ``test_row_level_security.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.database.saas_models import (
    Meeting,
    Membership,
    RecordScope,
    ShareLink,
    ShareRecipient,
    Workspace,
    WorkspaceRole,
)
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_insights_shares_api import _processed_meeting

STAGED_BYTES = b"staged wav bytes"  # what _upload stores for the processed meeting


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _headers(cookies: dict[str, str], workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]}


def _owner(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    cookies = _cookies(client)
    return workspace_id, meeting_id, cookies, _headers(cookies, workspace_id)


def _settings(client, headers, cookies, **changes):
    response = client.patch(
        "/api/v1/admin/workspace-settings", json=changes, headers=headers, cookies=cookies
    )
    assert response.status_code == 200, response.text
    return response.json()


def _share(client, headers, cookies, meeting_id: str, **body):
    return client.post(
        f"/api/v1/meetings/{meeting_id}/shares", json=body, headers=headers, cookies=cookies
    )


def _token(payload: dict) -> str:
    return payload["url"].rsplit("/", 1)[-1]


def _signup(client, email_outbox, *, email: str, verify: bool = True):
    credentials = register_user(client, email_outbox, email=email, verify=verify)
    verification_token = None if verify else email_outbox[-1]["token"]
    session = login_user(client, credentials)
    return session, _cookies(client), verification_token


def _anonymous(client):
    client.cookies.clear()


# --- creation policy ----------------------------------------------------------


def test_share_creation_policy_and_separate_grants(client, email_outbox, settings, monkeypatch):
    workspace_id, meeting_id, cookies, headers = _owner(client, email_outbox, settings, monkeypatch)

    # anonymous links are opt-in per workspace
    disabled = _share(client, headers, cookies, meeting_id, audience="link")
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "public_share_links_disabled"
    assert _settings(client, headers, cookies, public_share_links_enabled=True)[
        "public_share_links_enabled"
    ]
    link = _share(client, headers, cookies, meeting_id, audience="link", allow_export=True)
    assert link.status_code == 201, link.text
    assert link.json()["audience"] == "link"
    assert link.json()["require_authentication"] is False
    assert link.json()["allow_export"] is True and link.json()["allow_download"] is False

    # audio never goes to anonymous links; recipients must be named; workspace shares take none
    no_audio = _share(client, headers, cookies, meeting_id, audience="link", allow_download=True)
    assert no_audio.status_code == 422
    assert no_audio.json()["error"]["code"] == "download_requires_named_audience"
    assert (
        _share(client, headers, cookies, meeting_id, audience="recipients").json()["error"]["code"]
        == "recipients_required"
    )
    assert (
        _share(
            client, headers, cookies, meeting_id, audience="workspace", recipients=["a@example.com"]
        ).status_code
        == 422
    )
    assert (
        _share(client, headers, cookies, meeting_id, recipients=["not-an-email"]).status_code == 422
    )

    named = _share(
        client,
        headers,
        cookies,
        meeting_id,
        audience="recipients",
        recipients=["Guest@Example.com", "guest@example.com", "second@example.com"],
        allow_download=True,
    )
    assert named.status_code == 201, named.text
    recipients = named.json()["recipients"]
    assert [r["email"] for r in recipients] == ["guest@example.com", "second@example.com"]
    assert all(r["user_id"] is None and r["bound_at"] is None for r in recipients)

    # legacy clients: require_authentication=True -> workspace; nothing -> workspace
    legacy = _share(client, headers, cookies, meeting_id, require_authentication=True)
    assert legacy.status_code == 201 and legacy.json()["audience"] == "workspace"
    assert legacy.json()["require_authentication"] is True
    default = _share(client, headers, cookies, meeting_id)
    assert default.status_code == 201 and default.json()["audience"] == "workspace"

    listed = client.get(f"/api/v1/meetings/{meeting_id}/shares", headers=headers, cookies=cookies)
    assert listed.status_code == 200
    assert {item["audience"] for item in listed.json()} == {"link", "recipients", "workspace"}
    assert any(item["recipients"] for item in listed.json())
    assert not any("token" in key for item in listed.json() for key in item)


# --- anonymous links -----------------------------------------------------------


def test_link_share_view_and_export_without_audio_and_all_kill_switches(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, cookies, headers = _owner(client, email_outbox, settings, monkeypatch)
    _settings(client, headers, cookies, public_share_links_enabled=True)
    created = _share(client, headers, cookies, meeting_id, audience="link", allow_export=True)
    assert created.status_code == 201, created.text
    token = _token(created.json())
    share_id = created.json()["id"]

    _anonymous(client)
    view = client.get(f"/api/v1/shares/{token}")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["meeting_id"] == meeting_id and body["audience"] == "link"
    assert body["allow_export"] is True and body["allow_download"] is False
    assert body["transcript"]["segments"]
    export = client.get(f"/api/v1/shares/{token}/export", params={"format": "txt"})
    assert export.status_code == 200
    assert export.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"/api/v1/shares/{token}/export", params={"format": "pdf"}).status_code == 422
    audio = client.get(f"/api/v1/shares/{token}/recording")
    assert audio.status_code == 403
    assert audio.json()["error"]["code"] == "share_operation_not_permitted"

    # tampered or malformed tokens
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert client.get(f"/api/v1/shares/{tampered}").status_code == 404
    assert client.get("/api/v1/shares/short").status_code == 404

    # workspace-level kill switch takes effect immediately, and is reversible
    login_user(client, {"email": "upload-0@example.com", "password": "SecurePass123"})
    _settings(client, headers, cookies, public_share_links_enabled=False)
    _anonymous(client)
    assert client.get(f"/api/v1/shares/{token}").status_code == 404
    login_user(client, {"email": "upload-0@example.com", "password": "SecurePass123"})
    _settings(client, headers, cookies, public_share_links_enabled=True)
    _anonymous(client)
    assert client.get(f"/api/v1/shares/{token}").status_code == 200

    # expiry
    with inspection_session_factory()() as db:
        share = db.get(ShareLink, share_id)
        share.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/api/v1/shares/{token}").status_code == 404
    with inspection_session_factory()() as db:
        db.get(ShareLink, share_id).expires_at = datetime.now(UTC) + timedelta(hours=1)
        db.commit()
    assert client.get(f"/api/v1/shares/{token}").status_code == 200

    # deleted meeting
    with inspection_session_factory()() as db:
        db.get(Meeting, meeting_id).deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get(f"/api/v1/shares/{token}").status_code == 404
    assert client.get(f"/api/v1/shares/{token}/export", params={"format": "txt"}).status_code == 404
    with inspection_session_factory()() as db:
        db.get(Meeting, meeting_id).deleted_at = None
        db.commit()

    # revocation
    login_user(client, {"email": "upload-0@example.com", "password": "SecurePass123"})
    revoked = client.delete(
        f"/api/v1/meetings/{meeting_id}/shares/{share_id}", headers=headers, cookies=cookies
    )
    assert revoked.status_code == 204
    _anonymous(client)
    assert client.get(f"/api/v1/shares/{token}").status_code == 404
    assert client.get(f"/api/v1/shares/{token}/export", params={"format": "txt"}).status_code == 404


# --- named recipients ----------------------------------------------------------


def test_recipient_share_binds_verified_identity_and_streams_audio(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, cookies, headers = _owner(client, email_outbox, settings, monkeypatch)
    created = _share(
        client,
        headers,
        cookies,
        meeting_id,
        audience="recipients",
        recipients=["guest@example.com"],
        allow_download=True,
    )
    assert created.status_code == 201, created.text
    token = _token(created.json())
    share_id = created.json()["id"]
    view_path = f"/api/v1/shares/{token}"
    audio_path = f"/api/v1/shares/{token}/recording"

    _anonymous(client)
    unauthenticated = client.get(view_path)
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "share_authentication_required"

    # a different verified account is not the recipient, even though it holds the link
    _, other_cookies, _ = _signup(client, email_outbox, email="other@example.com")
    denied = client.get(view_path, cookies=other_cookies)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "share_recipient_denied"
    assert client.get(audio_path, cookies=other_cookies).status_code == 403

    # the right email on an *unverified* account proves nothing
    guest_session, guest_cookies, verify_token = _signup(
        client, email_outbox, email="guest@example.com", verify=False
    )
    unverified = client.get(view_path, cookies=guest_cookies)
    assert unverified.status_code == 403
    assert client.post("/api/v1/auth/verify-email", json={"token": verify_token}).status_code == 200

    allowed = client.get(view_path, cookies=guest_cookies)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["allow_download"] is True and allowed.json()["allow_export"] is False
    with inspection_session_factory()() as db:
        recipient = db.scalar(select(ShareRecipient).where(ShareRecipient.share_id == share_id))
        assert recipient.user_id == guest_session["user"]["id"]
        assert recipient.bound_at is not None
    # export was not granted
    no_export = client.get(f"{view_path}/export", params={"format": "txt"}, cookies=guest_cookies)
    assert no_export.status_code == 403

    # audio: exact object, bounded streaming, range support, immediate revocation
    full = client.get(audio_path, cookies=guest_cookies)
    assert full.status_code == 200, full.text
    assert full.content == STAGED_BYTES
    assert full.headers["content-length"] == str(len(STAGED_BYTES))
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["x-homayar-revocation"] == "immediate"
    assert full.headers["cache-control"] == "no-store"
    partial = client.get(audio_path, cookies=guest_cookies, headers={"Range": "bytes=4-9"})
    assert partial.status_code == 206, partial.text
    assert partial.content == STAGED_BYTES[4:10]
    assert partial.headers["content-range"] == f"bytes 4-9/{len(STAGED_BYTES)}"
    assert partial.headers["content-length"] == "6"
    tail = client.get(audio_path, cookies=guest_cookies, headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == STAGED_BYTES[-5:]
    open_ended = client.get(audio_path, cookies=guest_cookies, headers={"Range": "bytes=10-"})
    assert open_ended.status_code == 206 and open_ended.content == STAGED_BYTES[10:]
    for bad_range in ("bytes=100-", "bytes=9-4", "bytes=-0"):
        unsatisfiable = client.get(audio_path, cookies=guest_cookies, headers={"Range": bad_range})
        assert unsatisfiable.status_code == 416, bad_range

    # removing the recipient revokes that person, not the link
    with inspection_session_factory()() as db:
        db.delete(db.scalar(select(ShareRecipient).where(ShareRecipient.share_id == share_id)))
        db.commit()
    assert client.get(view_path, cookies=guest_cookies).status_code == 403
    assert client.get(audio_path, cookies=guest_cookies).status_code == 403
    with inspection_session_factory()() as db:
        db.add(
            ShareRecipient(
                workspace_id=workspace_id,
                share_id=share_id,
                email="guest@example.com",
                user_id=guest_session["user"]["id"],
                bound_at=datetime.now(UTC),
            )
        )
        db.commit()
    assert client.get(audio_path, cookies=guest_cookies).status_code == 200

    # revocation is immediate for streamed audio, including range continuations
    login_user(client, {"email": "upload-0@example.com", "password": "SecurePass123"})
    assert (
        client.delete(
            f"/api/v1/meetings/{meeting_id}/shares/{share_id}", headers=headers, cookies=cookies
        ).status_code
        == 204
    )
    assert client.get(view_path, cookies=guest_cookies).status_code == 404
    assert client.get(audio_path, cookies=guest_cookies).status_code == 404
    continuation = client.get(audio_path, cookies=guest_cookies, headers={"Range": "bytes=4-9"})
    assert continuation.status_code == 404


# --- workspace audience --------------------------------------------------------


def test_workspace_share_follows_membership_team_scope_and_issuer_authority(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, owner_cookies, owner_headers = _owner(
        client, email_outbox, settings, monkeypatch
    )
    issuer_session, issuer_cookies, _ = _signup(client, email_outbox, email="issuer@example.com")
    viewer_session, viewer_cookies, _ = _signup(client, email_outbox, email="viewer@example.com")
    _, outsider_cookies, _ = _signup(client, email_outbox, email="outsider@example.com")
    with inspection_session_factory()() as db:
        issuer_membership = Membership(
            workspace_id=workspace_id,
            user_id=issuer_session["user"]["id"],
            role=WorkspaceRole.MEMBER,
        )
        viewer_membership = Membership(
            workspace_id=workspace_id,
            user_id=viewer_session["user"]["id"],
            role=WorkspaceRole.VIEWER,
        )
        db.add_all([issuer_membership, viewer_membership])
        db.commit()
        issuer_membership_id, viewer_membership_id = issuer_membership.id, viewer_membership.id

    issuer_headers = _headers(issuer_cookies, workspace_id)
    created = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        json={"audience": "workspace"},
        headers=issuer_headers,
        cookies=issuer_cookies,
    )
    assert created.status_code == 201, created.text
    token = _token(created.json())
    path = f"/api/v1/shares/{token}"
    # a viewer cannot mint shares at all
    assert (
        client.post(
            f"/api/v1/meetings/{meeting_id}/shares",
            json={"audience": "workspace"},
            headers=_headers(viewer_cookies, workspace_id),
            cookies=viewer_cookies,
        ).status_code
        == 403
    )

    _anonymous(client)
    assert client.get(path).status_code == 401
    outside = client.get(path, cookies=outsider_cookies)
    assert outside.status_code == 403
    assert outside.json()["error"]["code"] == "share_membership_required"
    assert client.get(path, cookies=viewer_cookies).status_code == 200
    assert client.get(path, cookies=owner_cookies).status_code == 200
    # no export grant on this share
    no_export = client.get(f"{path}/export", params={"format": "json"}, cookies=viewer_cookies)
    assert no_export.status_code == 403

    # membership removed -> denied on the next request
    with inspection_session_factory()() as db:
        db.get(Membership, viewer_membership_id).is_active = False
        db.commit()
    assert client.get(path, cookies=viewer_cookies).status_code == 403
    with inspection_session_factory()() as db:
        db.get(Membership, viewer_membership_id).is_active = True
        db.commit()

    # team record scope: the viewer (no assignment) loses the record, and so
    # does the issuer, which suspends the share for everyone including the owner
    with inspection_session_factory()() as db:
        db.get(Workspace, workspace_id).record_scope = RecordScope.TEAM
        db.commit()
    assert client.get(path, cookies=owner_cookies).status_code == 404
    with inspection_session_factory()() as db:
        db.get(Meeting, meeting_id).agent_user_id = issuer_session["user"]["id"]
        db.commit()
    assert client.get(path, cookies=owner_cookies).status_code == 200  # issuer sees it again
    scoped = client.get(path, cookies=viewer_cookies)
    assert scoped.status_code == 403
    assert scoped.json()["error"]["code"] == "share_record_scope_denied"
    with inspection_session_factory()() as db:
        db.get(Workspace, workspace_id).record_scope = RecordScope.WORKSPACE
        db.commit()
    assert client.get(path, cookies=viewer_cookies).status_code == 200

    # issuer demoted below share:manage -> share suspended; restored -> works
    with inspection_session_factory()() as db:
        db.get(Membership, issuer_membership_id).role = WorkspaceRole.VIEWER
        db.commit()
    assert client.get(path, cookies=owner_cookies).status_code == 404
    with inspection_session_factory()() as db:
        db.get(Membership, issuer_membership_id).role = WorkspaceRole.MEMBER
        db.commit()
    assert client.get(path, cookies=owner_cookies).status_code == 200

    # issuer removed from the workspace -> share suspended for good
    with inspection_session_factory()() as db:
        db.get(Membership, issuer_membership_id).valid_until = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    assert client.get(path, cookies=owner_cookies).status_code == 404
    assert client.get(path, cookies=viewer_cookies).status_code == 404
