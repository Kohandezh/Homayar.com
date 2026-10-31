"""Session C integration: the backend gaps the console journey depends on.

G1/G7 sign-in method discovery and honest phone-OTP failures, G2 verification
e-mail resend, G9 the session lists only selectable workspaces, G10 summary /
action / evidence responses declare their own view, G11 workspace-scoped
inline playback.  Plus the SMTP delivery the verification journey needs.
Synthetic data only; no e-mail, SMS or provider leaves the process.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select

from backend.config import Settings, get_settings
from backend.database.saas_models import AuditEvent, Membership, OtpChallenge, Workspace
from backend.routers.v1 import auth as auth_router
from backend.security.context import CSRF_COOKIE
from backend.services import email_service
from backend.services.otp_service import OtpDeliveryError, PhoneAuthUnavailableError
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _cookies, _workspace_with_roles
from backend.tests.test_ingestion_ledger import CONTENT, _headers, _patch_media, _tenant, _upload
from backend.tests.test_privacy_routing import _get, _process, _set_policy

# --- G1 / G7: sign-in methods --------------------------------------------------------


def test_auth_methods_report_only_enabled_and_configured_methods(client, settings):
    response = client.get("/api/v1/auth/methods")
    assert response.status_code == 200, response.text
    # The test settings enable phone OTP with (fake) Asanak settings; Google is off.
    assert response.json() == {
        "password": True,
        "google": False,
        "phone_otp": True,
        "registration": True,
    }
    assert response.headers["cache-control"] == "no-store"
    assert "test-only-key" not in response.text

    defaults = settings.model_copy(update={"enable_phone_auth": False})
    assert auth_router.available_auth_methods(defaults).model_dump() == {
        "password": True,
        "google": False,
        "phone_otp": False,
        "registration": True,
    }
    # "Enabled" is not "available": a flag without its credentials reports false
    # (and the settings validator refuses to start with that combination).
    unconfigured = settings.model_copy(
        update={"enable_google_auth": True, "google_client_id": "", "google_client_secret": ""}
    )
    assert auth_router.available_auth_methods(unconfigured).google is False
    with pytest.raises(ValueError, match="Google authentication is enabled"):
        Settings(enable_google_auth=True, _env_file=None)
    phone_without_sender = settings.model_copy(update={"asanak_sender": ""})
    assert auth_router.available_auth_methods(phone_without_sender).phone_otp is False
    configured = settings.model_copy(
        update={
            "enable_google_auth": True,
            "google_client_id": "client-id",
            "google_client_secret": "client-secret",
        }
    )
    assert auth_router.available_auth_methods(configured).google is True


def _challenges() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count(OtpChallenge.id)))


def test_phone_otp_unavailable_or_undelivered_is_503_not_500(
    client, settings, sms_outbox, monkeypatch
):
    disabled = settings.model_copy(update={"enable_phone_auth": False})
    client.app.dependency_overrides[get_settings] = lambda: disabled
    refused = client.post("/api/v1/auth/otp/request", json={"phone": "09121234567"})
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "phone_auth_unavailable"
    assert _challenges() == 0
    client.app.dependency_overrides[get_settings] = lambda: settings

    def failing_sms(phone, code, _settings):
        raise OtpDeliveryError("HTTPStatusError")

    monkeypatch.setattr(auth_router, "_send_asanak_code", failing_sms)
    undelivered = client.post("/api/v1/auth/otp/request", json={"phone": "09121234567"})
    assert undelivered.status_code == 503, undelivered.text
    assert undelivered.json()["error"]["code"] == "otp_delivery_failed"
    assert undelivered.headers["retry-after"] == "30"
    # Nothing was committed: no challenge, and no resend cooldown was started.
    assert _challenges() == 0

    monkeypatch.setattr(
        auth_router,
        "_send_asanak_code",
        lambda phone, code, _settings: sms_outbox.append({"phone": phone, "code": code}),
    )
    delivered = client.post("/api/v1/auth/otp/request", json={"phone": "09121234567"})
    assert delivered.status_code == 202, delivered.text
    assert _challenges() == 1 and len(sms_outbox) == 1


def test_asanak_sender_classifies_configuration_and_transport_failures(settings):
    with pytest.raises(PhoneAuthUnavailableError):
        auth_router._send_asanak_code(
            "+989121234567", "123456", settings.model_copy(update={"asanak_api_key": ""})
        )
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as mock:
        mock.post(settings.asanak_base_url).mock(return_value=httpx.Response(502))
        with pytest.raises(OtpDeliveryError):
            auth_router._send_asanak_code("+989121234567", "123456", settings)
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as mock:
        mock.post(settings.asanak_base_url).mock(side_effect=httpx.ConnectTimeout("timeout"))
        with pytest.raises(OtpDeliveryError):
            auth_router._send_asanak_code("+989121234567", "123456", settings)


# --- G2: verification resend ---------------------------------------------------------


def _resend(client, cookies: dict[str, str] | None, *, csrf: bool = True):
    headers = {"X-CSRF-Token": cookies[CSRF_COOKIE]} if cookies and csrf else {}
    return client.post("/api/v1/auth/verify-email/resend", cookies=cookies or {}, headers=headers)


def test_verification_resend_supersedes_the_old_link_and_is_rate_limited(client, email_outbox):
    credentials = register_user(
        client, email_outbox, email="resend@example.com", name="Resend", verify=False
    )
    first_token = email_outbox[-1]["token"]
    session = login_user(client, credentials)
    assert session["requires_verification"] is True
    cookies = _cookies(client)
    client.cookies.clear()

    assert _resend(client, None).status_code == 401
    missing_csrf = _resend(client, cookies, csrf=False)
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"

    accepted = _resend(client, cookies)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json() == {"status": "accepted", "resend_after": 60}
    assert len(email_outbox) == 2 and email_outbox[-1]["kind"] == "verify"
    second_token = email_outbox[-1]["token"]
    assert second_token != first_token

    cooldown = _resend(client, cookies)
    assert cooldown.status_code == 429, cooldown.text
    assert cooldown.json()["error"]["code"] == "verification_cooldown"
    assert int(cooldown.headers["retry-after"]) >= 1
    assert len(email_outbox) == 2

    # Only the newest link verifies.
    stale = client.post("/api/v1/auth/verify-email", json={"token": first_token})
    assert stale.status_code == 400
    assert stale.json()["error"]["code"] == "invalid_verification_token"
    verified = client.post("/api/v1/auth/verify-email", json={"token": second_token})
    assert verified.status_code == 200, verified.text

    already = _resend(client, cookies)
    assert already.status_code == 202
    assert already.json() == {"status": "already_verified", "resend_after": 0}
    assert len(email_outbox) == 2
    me = client.get("/api/v1/auth/me", cookies=cookies)
    assert me.json()["requires_verification"] is False

    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent.event_type).where(AuditEvent.actor_user_id == me.json()["user"]["id"])
        ).all()
    assert events.count("auth.verification_resent") == 1


@pytest.mark.asyncio
async def test_verification_message_raises_when_delivery_did_not_happen(monkeypatch):
    async def undelivered(_email, _token):
        return False

    monkeypatch.setattr(email_service, "send_verification_email", undelivered)

    class _User:
        email = "someone@example.com"

    with pytest.raises(auth_router.EmailDeliveryError):
        await auth_router._send_verification_message(_User(), "t" * 40, None)


# --- SMTP delivery -------------------------------------------------------------------


class _FakeSMTP:
    sent: list = []
    events: list[str] = []
    fail = False

    def __init__(self, host, port, timeout=None, **_kwargs):
        self.events.append(f"connect {host}:{port}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.events.append("quit")
        return False

    def starttls(self, context=None):
        self.events.append("starttls")

    def login(self, username, password):
        self.events.append(f"login {username}")

    def send_message(self, message):
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({})
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch, settings):
    _FakeSMTP.sent, _FakeSMTP.events, _FakeSMTP.fail = [], [], False
    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)
    configured = settings.model_copy(
        update={
            "smtp_host": "smtp.test.invalid",
            "smtp_port": 587,
            "smtp_username": "mailer",
            "smtp_password": "not-a-real-password",
            "smtp_from": "noreply@homayar.test",
        }
    )
    monkeypatch.setattr(email_service, "get_settings", lambda: configured)
    return _FakeSMTP


@pytest.mark.asyncio
async def test_verification_email_is_delivered_over_smtp_without_logging_the_address(
    fake_smtp, caplog
):
    caplog.set_level(logging.INFO, logger="homayar")
    assert await email_service.send_verification_email("person@example.com", "tok" * 14) is True
    (message,) = fake_smtp.sent
    assert message["To"] == "person@example.com"
    assert message["From"] == "noreply@homayar.test"
    body = message.get_body(("plain",)).get_content()
    assert "http://testserver/app/verify-email?token=" + "tok" * 14 in body
    assert "۲۴ ساعت" in body and "24 hours" in body
    assert fake_smtp.events[:3] == ["connect smtp.test.invalid:587", "starttls", "login mailer"]
    assert "person@example.com" not in caplog.text

    fake_smtp.fail = True
    assert await email_service.send_password_reset_email("person@example.com", "x" * 40) is False
    assert "SMTPRecipientsRefused" in caplog.text and "person@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_unconfigured_smtp_reports_not_sent(monkeypatch, settings):
    monkeypatch.setattr(
        email_service, "get_settings", lambda: settings.model_copy(update={"smtp_host": ""})
    )
    assert await email_service.send_email("person@example.com", "s", "b") is False


# --- G9: selectable workspaces -------------------------------------------------------


def test_session_lists_exactly_the_workspaces_a_request_may_select(
    client, email_outbox, monkeypatch
):
    owner, people, membership_ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]
    shared_workspace = owner.workspace_id

    def listed() -> set[str]:
        response = client.get("/api/v1/auth/me", cookies=member.cookies)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["workspaces"]}

    def selectable(workspace_id: str) -> bool:
        response = client.get(
            "/api/v1/meetings",
            cookies=member.cookies,
            headers={"X-Workspace-ID": workspace_id},
        )
        assert response.status_code in (200, 403), response.text
        return response.status_code == 200

    all_ids = listed()
    assert shared_workspace in all_ids and len(all_ids) == 2
    personal = (all_ids - {shared_workspace}).pop()

    def assert_consistent():
        ids = listed()
        for workspace_id in (personal, shared_workspace):
            assert (workspace_id in ids) == selectable(workspace_id), workspace_id
        return ids

    with inspection_session_factory()() as db:
        membership = db.get(Membership, membership_ids["member"])
        membership.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert assert_consistent() == {personal}
    # The login response uses the same list.
    relogin = login_user(
        client, {"email": "matrix-member@example.com", "password": "SecurePass123"}
    )
    assert {item["id"] for item in relogin["workspaces"]} == {personal}

    with inspection_session_factory()() as db:
        membership = db.get(Membership, membership_ids["member"])
        membership.valid_until = None
        membership.valid_from = datetime.now(UTC) + timedelta(days=1)
        db.commit()
    assert assert_consistent() == {personal}

    with inspection_session_factory()() as db:
        membership = db.get(Membership, membership_ids["member"])
        membership.valid_from = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert assert_consistent() == {personal, shared_workspace}

    with inspection_session_factory()() as db:
        db.get(Workspace, shared_workspace).status = "suspended"
        db.commit()
    assert assert_consistent() == {personal}


# --- G10: the view of every content response -----------------------------------------


def test_summary_actions_and_evidence_declare_their_own_view(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)

    def add_action(tenant: dict, base: str) -> None:
        # The mock analysis proposes no action items; a person's item is
        # rendered through the same view.
        created = client.post(
            f"{base}/actions",
            json={"description": "Call back on 09123456789 about the offer"},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert created.status_code == 201, created.text

    plain = _tenant(client, email_outbox, "view-plain")
    meeting_id, _ = _process(client, plain, settings, "view-plain")
    base = f"/api/v1/meetings/{meeting_id}"
    add_action(plain, base)
    summary = _get(client, plain, f"{base}/summary").json()
    assert (summary["view"], summary["privacy_enforced"], summary["redaction"]) == (
        "original",
        False,
        None,
    )
    actions = _get(client, plain, f"{base}/actions").json()
    assert len(actions) == 1 and "09123456789" in actions[0]["description"]
    for item in actions:
        assert (item["view"], item["privacy_enforced"], item["redaction"]) == (
            "original",
            False,
            None,
        )
    evidence = _get(client, plain, f"{base}/evidence").json()
    assert (evidence["view"], evidence["privacy_enforced"]) == ("original", False)
    # A redacted view on request, without a workspace policy, says so.
    asked = _get(client, plain, f"{base}/summary", view="redacted").json()
    assert asked["view"] == "redacted" and asked["privacy_enforced"] is False
    assert asked["redaction"]["classes"] and len(asked["redaction"]["policy_digest"]) == 64

    enforced = _tenant(client, email_outbox, "view-enforced")
    assert _set_policy(client, enforced, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, enforced, settings, "view-enforced")
    base = f"/api/v1/meetings/{meeting_id}"
    add_action(enforced, base)
    summary = _get(client, enforced, f"{base}/summary").json()
    assert summary["view"] == "redacted" and summary["privacy_enforced"] is True
    assert summary["redaction"]["detector_version"]
    assert summary["analysis_run"]["input_view"] == "redacted"
    actions = _get(client, enforced, f"{base}/actions").json()
    assert len(actions) == 1 and all(
        item["view"] == "redacted" and item["privacy_enforced"] for item in actions
    )
    assert "09123456789" not in actions[0]["description"]
    assert "[PHONE_" in actions[0]["description"]
    evidence = _get(client, enforced, f"{base}/evidence").json()
    assert evidence["view"] == "redacted" and evidence["redaction"] is not None
    # The owner's explicit, audited original is labelled original, still enforced.
    original = _get(client, enforced, f"{base}/summary", view="original").json()
    assert original["view"] == "original" and original["privacy_enforced"] is True
    assert original["redaction"] is None


# --- G11: workspace-scoped playback --------------------------------------------------


def test_workspace_scoped_playback_is_authorized_on_every_range_request(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    owner, people, membership_ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]
    tenant = {"workspace_id": owner.workspace_id, "cookies": owner.cookies}
    accepted = _upload(client, tenant, key="playback-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    playback = f"/api/v1/workspaces/{owner.workspace_id}/meetings/{meeting_id}/recording"

    # The header-based route cannot select the shared workspace for a native
    # <audio> element (no header): it resolves the member's oldest workspace.
    legacy = client.get(f"/api/v1/meetings/{meeting_id}/recording", cookies=member.cookies)
    assert legacy.status_code == 404

    full = client.get(playback, cookies=member.cookies)
    assert full.status_code == 200, full.text
    assert full.content == CONTENT
    assert full.headers["content-disposition"].startswith("inline;")
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["cache-control"] == "no-store"
    assert full.headers["x-homayar-revocation"] == "immediate"

    partial = client.get(playback, cookies=member.cookies, headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == CONTENT[:4]
    assert partial.headers["content-range"] == f"bytes 0-3/{len(CONTENT)}"
    suffix = client.get(playback, cookies=member.cookies, headers={"Range": "bytes=-5"})
    assert suffix.status_code == 206 and suffix.content == CONTENT[-5:]
    unsatisfiable = client.get(
        playback, cookies=member.cookies, headers={"Range": f"bytes={len(CONTENT) + 10}-"}
    )
    assert unsatisfiable.status_code == 416
    assert unsatisfiable.headers["content-range"] == f"bytes */{len(CONTENT)}"
    malformed = client.get(playback, cookies=member.cookies, headers={"Range": "items=0-1"})
    assert malformed.status_code == 416

    # No session, another workspace in the path, an outsider: all refused.
    client.cookies.clear()
    assert client.get(playback).status_code == 401
    personal = next(
        item["id"]
        for item in client.get("/api/v1/auth/me", cookies=member.cookies).json()["workspaces"]
        if item["id"] != owner.workspace_id
    )
    cross = client.get(
        f"/api/v1/workspaces/{personal}/meetings/{meeting_id}/recording", cookies=member.cookies
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "meeting_not_found"
    outsider_credentials = register_user(
        client, email_outbox, email="outsider@example.com", name="Outsider"
    )
    login_user(client, outsider_credentials)
    outsider = client.get(playback, cookies=_cookies(client))
    assert outsider.status_code == 403
    assert outsider.json()["error"]["code"] == "workspace_access_denied"

    # Membership removal ends playback on the next (range) request.
    removed = client.delete(
        f"/api/v1/members/{membership_ids['member']}",
        cookies=owner.cookies,
        headers=_headers(tenant),
    )
    assert removed.status_code == 204, removed.text
    after_removal = client.get(playback, cookies=member.cookies, headers={"Range": "bytes=4-7"})
    assert after_removal.status_code == 403

    # Deletion (tombstone) ends it for everyone, the owner included.
    assert client.get(playback, cookies=owner.cookies).status_code == 200
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", cookies=owner.cookies, headers=_headers(tenant)
    )
    assert deleted.status_code == 204, deleted.text
    gone = client.get(playback, cookies=owner.cookies)
    assert gone.status_code == 404

    # A signed-out session cannot continue a player either.
    logout = client.post(
        "/api/v1/auth/logout",
        cookies=owner.cookies,
        headers={"X-CSRF-Token": owner.cookies[CSRF_COOKIE]},
    )
    assert logout.status_code == 200
    assert client.get(playback, cookies=owner.cookies).status_code == 401
