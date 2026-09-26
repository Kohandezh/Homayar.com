"""WP03-T06 follow-up: organization audience, phone recipients, recipient editing.

Extends ``test_share_policies.py`` (ADR 0006 amendment of 2026-09-10):

* an ``organization`` share admits only *current* members of the organization
  the source workspace belongs to (workspace memberships in any of its
  workspaces, or organization memberships), re-checked on every access; the
  workspace kill switch hides such shares from everyone; leaving, expiry and
  revocation bite on the next request; nothing else of the tenant is reached;
* a phone recipient is matched only against an account whose phone was
  verified through the existing OTP contract and is then bound to that user
  id — a reassigned number transfers nothing;
* recipient edits need current ``share:manage`` authority on a visible record,
  are deltas under a row lock (concurrent editors lose nothing), are audited by
  count and id, and a removed recipient is refused at once.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    AuditEvent,
    Membership,
    ShareRecipient,
    User,
    WorkspaceRole,
)
from backend.security.context import CSRF_COOKIE
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user
from backend.tests.test_share_policies import (
    STAGED_BYTES,
    _anonymous,
    _owner,
    _settings,
    _share,
    _signup,
    _token,
)

OWNER_CREDENTIALS = {"email": "upload-0@example.com", "password": "SecurePass123"}


def _csrf(cookies: dict[str, str]) -> dict[str, str]:
    return {"X-CSRF-Token": cookies[CSRF_COOKIE]}


def _organization_id(client, cookies: dict[str, str]) -> str:
    response = client.get("/api/v1/organizations", cookies=cookies)
    assert response.status_code == 200, response.text
    owned = [item for item in response.json()["items"] if item["role"] == "owner"]
    assert len(owned) == 1
    return owned[0]["id"]


def _grant_organization_role(client, cookies, org: str, email: str, role: str) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org}/members",
        json={"email": email, "role": role},
        cookies=cookies,
        headers=_csrf(cookies),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _insert_membership(workspace_id: str, user_id: str, role: WorkspaceRole) -> str:
    with inspection_session_factory()() as db:
        membership = Membership(workspace_id=workspace_id, user_id=user_id, role=role)
        db.add(membership)
        db.commit()
        return membership.id


def _phone_login(client, sms_outbox, phone: str) -> tuple[dict, dict[str, str]]:
    """Sign in through the existing OTP contract; the phone becomes verified."""
    _anonymous(client)
    requested = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 202, requested.text
    verified = client.post(
        "/api/v1/auth/otp/verify",
        json={"challenge_id": requested.json()["challenge_id"], "code": sms_outbox[-1]["code"]},
    )
    assert verified.status_code == 200, verified.text
    cookies = {
        name: value
        for name in ("homayar_access", "homayar_refresh", CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }
    _anonymous(client)
    return verified.json(), cookies


def _recipients(client, headers, cookies, meeting_id: str, share_id: str, **body):
    return client.patch(
        f"/api/v1/meetings/{meeting_id}/shares/{share_id}/recipients",
        json=body,
        headers=headers,
        cookies=cookies,
    )


# --- organization audience ----------------------------------------------------------------


def test_organization_share_admits_current_organization_members_only(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, owner_cookies, headers = _owner(
        client, email_outbox, settings, monkeypatch
    )
    org = _organization_id(client, owner_cookies)
    created = _share(client, headers, owner_cookies, meeting_id, audience="organization")
    assert created.status_code == 201, created.text
    assert created.json()["audience"] == "organization"
    assert created.json()["require_authentication"] is True
    token = _token(created.json())
    view_path = f"/api/v1/shares/{token}"
    with_recipients = _share(
        client,
        headers,
        owner_cookies,
        meeting_id,
        audience="organization",
        recipients=["x@example.com"],
    )
    assert with_recipients.status_code == 422

    _anonymous(client)
    assert client.get(view_path).status_code == 401

    # an organization admin (organization membership, no workspace membership)
    admin_session, admin_cookies, _ = _signup(client, email_outbox, email="org-admin@example.com")
    admin_grant = _grant_organization_role(
        client, owner_cookies, org, "org-admin@example.com", "admin"
    )
    assert (
        client.get(
            "/api/v1/meetings", cookies=admin_cookies, headers={"X-Workspace-ID": workspace_id}
        ).status_code
        == 403
    )
    allowed = client.get(view_path, cookies=admin_cookies)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["allow_download"] is False and allowed.json()["allow_export"] is False
    assert client.get(f"{view_path}/recording", cookies=admin_cookies).status_code == 403
    assert (
        client.get(
            f"{view_path}/export", params={"format": "txt"}, cookies=admin_cookies
        ).status_code
        == 403
    )

    # a member of a sibling workspace of the same organization
    sibling = client.post(
        f"/api/v1/organizations/{org}/workspaces",
        json={"name": "Sibling"},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert sibling.status_code == 201, sibling.text
    colleague_session, colleague_cookies, _ = _signup(
        client, email_outbox, email="colleague@example.com"
    )
    colleague_membership = _insert_membership(
        sibling.json()["id"], colleague_session["user"]["id"], WorkspaceRole.MEMBER
    )
    assert client.get(view_path, cookies=colleague_cookies).status_code == 200

    # a record-scoped member of the source workspace outside the record's team:
    # the workspace routes hide the record, the explicit organization share does not
    _settings(client, headers, owner_cookies, record_scope="team")
    scoped_session, scoped_cookies, _ = _signup(client, email_outbox, email="scoped@example.com")
    _insert_membership(workspace_id, scoped_session["user"]["id"], WorkspaceRole.MEMBER)
    hidden = client.get(
        f"/api/v1/meetings/{meeting_id}",
        cookies=scoped_cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert hidden.status_code == 404
    assert client.get(view_path, cookies=scoped_cookies).status_code == 200

    # a stranger with their own organization
    _, stranger_cookies, _ = _signup(client, email_outbox, email="stranger@example.com")
    denied = client.get(view_path, cookies=stranger_cookies)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "share_organization_membership_required"

    # leaving the organization bites on the next request, valid session or not
    revoked = client.delete(
        f"/api/v1/organizations/{org}/members/{admin_grant['id']}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert revoked.status_code == 204
    assert client.get(view_path, cookies=admin_cookies).status_code == 403
    with inspection_session_factory()() as db:
        membership = db.get(Membership, colleague_membership)
        membership.valid_from = datetime.now(UTC) - timedelta(minutes=10)
        membership.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(view_path, cookies=colleague_cookies).status_code == 403

    # the workspace kill switch hides organization shares from everyone
    _settings(client, headers, owner_cookies, organization_share_links_enabled=False)
    assert client.get(view_path, cookies=scoped_cookies).status_code == 404
    assert client.get(view_path, cookies=owner_cookies).status_code == 404
    disabled = _share(client, headers, owner_cookies, meeting_id, audience="organization")
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "organization_share_links_disabled"
    _settings(client, headers, owner_cookies, organization_share_links_enabled=True)
    assert client.get(view_path, cookies=scoped_cookies).status_code == 200

    # revocation
    login_user(client, OWNER_CREDENTIALS)
    assert (
        client.delete(
            f"/api/v1/meetings/{meeting_id}/shares/{created.json()['id']}",
            headers=headers,
            cookies=owner_cookies,
        ).status_code
        == 204
    )
    assert client.get(view_path, cookies=scoped_cookies).status_code == 404


# --- phone recipients ---------------------------------------------------------------------


def test_phone_recipients_bind_a_verified_phone_identity_only(
    client, email_outbox, sms_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, owner_cookies, headers = _owner(
        client, email_outbox, settings, monkeypatch
    )
    invalid = _share(
        client,
        headers,
        owner_cookies,
        meeting_id,
        audience="recipients",
        phone_recipients=["12"],
    )
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "invalid_recipient"
    created = _share(
        client,
        headers,
        owner_cookies,
        meeting_id,
        audience="recipients",
        phone_recipients=["0912 123 0001", "+98 912 123 0001"],
        allow_download=True,
    )
    assert created.status_code == 201, created.text
    recipients = created.json()["recipients"]
    assert [(r["email"], r["phone_e164"]) for r in recipients] == [(None, "+989121230001")]
    token = _token(created.json())
    view_path = f"/api/v1/shares/{token}"
    audio_path = f"{view_path}/recording"

    # an email account without a verified phone is not the recipient
    _, email_cookies, _ = _signup(client, email_outbox, email="mail-only@example.com")
    denied = client.get(view_path, cookies=email_cookies)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "share_recipient_denied"

    # the phone's owner, verified through the OTP contract, is bound on first access
    phone_session, phone_cookies = _phone_login(client, sms_outbox, "09121230001")
    allowed = client.get(view_path, cookies=phone_cookies)
    assert allowed.status_code == 200, allowed.text
    assert client.get(audio_path, cookies=phone_cookies).content == STAGED_BYTES
    with inspection_session_factory()() as db:
        recipient = db.scalar(
            select(ShareRecipient).where(ShareRecipient.share_id == created.json()["id"])
        )
        assert recipient.user_id == phone_session["user"]["id"]
        assert recipient.bound_at is not None
        assert recipient.email is None and recipient.phone_e164 == "+989121230001"

    # the number is reassigned to another account: the grant does not follow the
    # number, and the bound account keeps it until the issuer removes the recipient
    other_session, other_cookies, _ = _signup(client, email_outbox, email="new-holder@example.com")
    with inspection_session_factory()() as db:
        previous = db.get(User, phone_session["user"]["id"])
        previous.phone_e164 = "+989121230009"  # the first holder moved to a new number
        db.flush()  # the number is unique per account: release it before reassigning
        holder = db.get(User, other_session["user"]["id"])
        holder.phone_e164 = "+989121230001"
        holder.phone_verified_at = datetime.now(UTC)
        db.commit()
    refused = client.get(view_path, cookies=other_cookies)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "share_recipient_denied"
    assert client.get(view_path, cookies=phone_cookies).status_code == 200
    with inspection_session_factory()() as db:
        recipient = db.scalar(
            select(ShareRecipient).where(ShareRecipient.share_id == created.json()["id"])
        )
        assert recipient.user_id == phone_session["user"]["id"]

    # a recipient list needs at least one address of either kind
    empty = _share(client, headers, owner_cookies, meeting_id, audience="recipients")
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "recipients_required"


# --- recipient editing ----------------------------------------------------------------------


def test_recipient_edits_need_authority_are_audited_and_bite_immediately(
    client, email_outbox, sms_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, owner_cookies, headers = _owner(
        client, email_outbox, settings, monkeypatch
    )
    created = _share(
        client,
        headers,
        owner_cookies,
        meeting_id,
        audience="recipients",
        recipients=["first@example.com"],
        allow_download=True,
    )
    assert created.status_code == 201, created.text
    share_id = created.json()["id"]
    token = _token(created.json())
    view_path = f"/api/v1/shares/{token}"
    audio_path = f"{view_path}/recording"
    first_id = created.json()["recipients"][0]["id"]

    # a viewer cannot edit; a workspace share has no editable recipients
    viewer_session, viewer_cookies, _ = _signup(client, email_outbox, email="viewer@example.com")
    _insert_membership(workspace_id, viewer_session["user"]["id"], WorkspaceRole.VIEWER)
    forbidden = _recipients(
        client,
        {**headers, "X-CSRF-Token": viewer_cookies[CSRF_COOKIE]},
        viewer_cookies,
        meeting_id,
        share_id,
        add_emails=["second@example.com"],
    )
    assert forbidden.status_code == 403
    workspace_share = _share(client, headers, owner_cookies, meeting_id, audience="workspace")
    not_editable = _recipients(
        client,
        headers,
        owner_cookies,
        meeting_id,
        workspace_share.json()["id"],
        add_emails=["second@example.com"],
    )
    assert not_editable.status_code == 422
    assert not_editable.json()["error"]["code"] == "recipients_not_editable"

    # additions: an email and a phone; duplicates are ignored, unknown removals refused
    edited = _recipients(
        client,
        headers,
        owner_cookies,
        meeting_id,
        share_id,
        add_emails=["second@example.com", "FIRST@example.com"],
        add_phones=["09121230002"],
    )
    assert edited.status_code == 200, edited.text
    listed = {(r["email"], r["phone_e164"]) for r in edited.json()["recipients"]}
    assert listed == {
        ("first@example.com", None),
        ("second@example.com", None),
        (None, "+989121230002"),
    }
    missing = _recipients(
        client,
        headers,
        owner_cookies,
        meeting_id,
        share_id,
        remove=["00000000-0000-4000-8000-000000000000"],
    )
    assert missing.status_code == 404
    too_many = _recipients(
        client,
        headers,
        owner_cookies,
        meeting_id,
        share_id,
        # 3 present + 48 new = 51 > MAX_RECIPIENTS (the request schema caps each list at 50)
        add_emails=[f"bulk{i}@example.com" for i in range(48)],
    )
    assert too_many.status_code == 422 and too_many.json()["error"]["code"] == "too_many_recipients"

    # the first recipient is bound and downloading; removal bites at once,
    # including a range continuation of the stream
    first_session, first_cookies, _ = _signup(client, email_outbox, email="first@example.com")
    assert client.get(view_path, cookies=first_cookies).status_code == 200
    assert client.get(audio_path, cookies=first_cookies).status_code == 200
    removed = _recipients(client, headers, owner_cookies, meeting_id, share_id, remove=[first_id])
    assert removed.status_code == 200, removed.text
    assert first_id not in {r["id"] for r in removed.json()["recipients"]}
    assert client.get(view_path, cookies=first_cookies).status_code == 403
    continuation = client.get(audio_path, cookies=first_cookies, headers={"Range": "bytes=4-9"})
    assert continuation.status_code == 403
    # the added email recipient and the phone recipient are admitted
    _, second_cookies, _ = _signup(client, email_outbox, email="second@example.com")
    assert client.get(view_path, cookies=second_cookies).status_code == 200
    _, phone_cookies = _phone_login(client, sms_outbox, "09121230002")
    assert client.get(view_path, cookies=phone_cookies).status_code == 200

    # audited by count and id, never by address
    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "meeting.share_recipients_changed")
            .order_by(AuditEvent.occurred_at.asc())
        ).all()
        assert [(e.details["added"], e.details["removed"]) for e in events] == [
            ("2", "0"),
            ("0", "1"),
        ] or [(int(e.details["added"]), int(e.details["removed"])) for e in events] == [
            (2, 0),
            (0, 1),
        ]
        assert all("example.com" not in str(e.details) for e in events)
        assert all("+98" not in str(e.details) for e in events)


@pytest.mark.postgres
def test_concurrent_recipient_edits_lose_nothing(client, email_outbox, settings, monkeypatch):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locking is PostgreSQL evidence")
    _, meeting_id, owner_cookies, headers = _owner(client, email_outbox, settings, monkeypatch)
    created = _share(
        client,
        headers,
        owner_cookies,
        meeting_id,
        audience="recipients",
        recipients=["base@example.com"],
    )
    share_id = created.json()["id"]

    def edit(index: int):
        return _recipients(
            client,
            headers,
            owner_cookies,
            meeting_id,
            share_id,
            add_emails=[f"parallel-{index}@example.com"],
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(edit, range(4)))
    assert statuses == [200, 200, 200, 200]
    with inspection_session_factory()() as db:
        emails = set(
            db.scalars(select(ShareRecipient.email).where(ShareRecipient.share_id == share_id))
        )
    assert emails == {"base@example.com", *(f"parallel-{i}@example.com" for i in range(4))}
