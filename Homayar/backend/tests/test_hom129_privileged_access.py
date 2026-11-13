"""Account cut-off and break-glass access (HOM-129, ADR 0076).

The cut-off functions and the break-glass window run as the host CLI does: through
the application session, committed by the test.  The gate of privileged MFA stays off
here (the shared fixture); ``test_hom129_mfa.py`` covers it.  This module turns
break-glass on and records every alert instead of sending email.
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.config import Settings, get_settings
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ApiKey,
    AuditEvent,
    AuthIdentity,
    BreakGlassWindow,
    IdentityProvider,
    LoginSession,
    Membership,
    MfaRecoveryCode,
    User,
    UserMfaTotp,
    WorkspaceRole,
)
from backend.observability import metrics
from backend.operator_access import __main__ as cli
from backend.routers.v1 import auth as auth_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.security.tokens import hash_password
from backend.security.totp import decode_totp_secret, totp_at
from backend.services import email_service
from backend.services import privileged_access_service as access
from backend.services.privileged_access_service import PrivilegedAccessError
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import register_user
from backend.tests.test_federation import (
    _graft_oidc_identity,
    _make_oidc_user,
    _mapping,
    _sync,
)

OPERATOR = "ops-admin"
PASSWORD = "SecurePass123"  # noqa: S105 - test credential


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"break_glass_enabled": True, "break_glass_alert_emails": "ops@example.com"}
    )


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None, dict]]:
    sent: list[tuple[str, str | None, dict]] = []

    def capture(_settings, *, event: str, user_email: str | None, details: dict) -> None:
        sent.append((event, user_email, dict(details)))

    monkeypatch.setattr(access, "send_break_glass_alert", capture)
    return sent


# --- helpers -----------------------------------------------------------------------------


class Login:
    """One login of one user, sent with explicit cookies."""

    def __init__(self, client, response) -> None:
        assert response.status_code == 200, response.text
        self.client = client
        self.cookies = {
            name: value
            for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
            if (value := client.cookies.get(name))
        }
        client.cookies.clear()
        body = response.json()
        self.user_id: str = body["user"]["id"]
        self.workspace_id: str = body["workspaces"][0]["id"]

    def headers(self, workspace_id: str | None = None) -> dict[str, str]:
        return {
            "X-CSRF-Token": self.cookies[CSRF_COOKIE],
            "X-Workspace-ID": workspace_id or self.workspace_id,
        }

    def get(self, path: str, *, workspace_id: str | None = None):
        return self.client.get(path, cookies=self.cookies, headers=self.headers(workspace_id))

    def post(self, path: str, *, json: dict | None = None):
        return self.client.post(path, json=json, cookies=self.cookies, headers=self.headers())

    def refresh(self):
        response = self.client.post(
            "/api/v1/auth/refresh",
            cookies=self.cookies,
            headers={"X-CSRF-Token": self.cookies[CSRF_COOKIE]},
        )
        self.client.cookies.clear()
        return response


def _login(client, email: str, password: str = PASSWORD):
    response = client.post("/api/v1/auth/login", json={"identifier": email, "password": password})
    if response.status_code != 200:
        client.cookies.clear()
    return response


def _signup(client, email_outbox, name: str) -> tuple[str, Login]:
    email = f"{name}@example.com"
    register_user(client, email_outbox, email=email, password=PASSWORD, name=name)
    client.cookies.clear()
    return email, Login(client, _login(client, email))


def _service(function, **kwargs):
    with get_session_factory()() as db:
        result = function(db, **kwargs)
        db.commit()
        return result


def _refusal(function, **kwargs) -> str:
    with get_session_factory()() as db:
        with pytest.raises(PrivilegedAccessError) as caught:
            function(db, **kwargs)
        db.rollback()
    return caught.value.code


def _error(response) -> dict:
    error = dict(response.json()["error"])
    error.pop("request_id", None)
    return error


def _mint_key(session: Login) -> str:
    response = session.post(
        "/api/v1/api-keys",
        json={"name": "ci", "scopes": ["meeting:read"], "expires_in_days": 7},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _by_key(client, raw_key: str, workspace_id: str):
    return client.get(
        "/api/v1/meetings",
        headers={"Authorization": f"Bearer {raw_key}", "X-Workspace-ID": workspace_id},
    )


def _audit(event_type: str, user_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == event_type, AuditEvent.target_id == user_id
                )
            )
        )


def _sessions(user_id: str) -> list[LoginSession]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(LoginSession).where(LoginSession.user_id == user_id)))


def _designate(client, email_outbox, settings, name: str) -> tuple[str, str, str]:
    """A break-glass owner; returns (email, user id, personal workspace id)."""
    email, session = _signup(client, email_outbox, name)
    _service(access.designate_break_glass, email=email, actor_label=OPERATOR, settings=settings)
    return email, session.user_id, session.workspace_id


def _open(email: str, settings: Settings, minutes: int = 30) -> BreakGlassWindow:
    return _service(
        access.open_break_glass_window,
        email=email,
        minutes=minutes,
        reason="Primary IdP is down",
        actor_label=OPERATOR,
        settings=settings,
    )


# --- cut-off (D6) ------------------------------------------------------------------------


def test_disable_user_refuses_every_token_key_and_login_and_enable_allows_a_new_login(
    client, email_outbox, settings
):
    email, owner = _signup(client, email_outbox, "disable-me")
    raw_key = _mint_key(owner)
    assert owner.get("/api/v1/members").status_code == 200
    assert _by_key(client, raw_key, owner.workspace_id).status_code == 200

    user = _service(
        access.disable_user,
        email=email,
        reason="Left the company",
        actor_label=OPERATOR,
        settings=settings,
    )
    assert user.is_active is False

    assert owner.get("/api/v1/members").status_code == 401
    assert owner.refresh().status_code == 401
    refused_key = _by_key(client, raw_key, owner.workspace_id)
    assert refused_key.status_code == 401 and _error(refused_key)["code"] == "api_key_invalid"
    refused_login = _login(client, email)
    assert refused_login.status_code == 401
    assert _error(refused_login)["code"] == "invalid_credentials"
    [event] = _audit("auth.user_disabled", user.id)
    assert event.actor_user_id is None and event.target_type == "user"
    assert event.details == {"actor": OPERATOR, "reason": "Left the company"}

    _service(access.enable_user, email=email, actor_label=OPERATOR, settings=settings)
    fresh = Login(client, _login(client, email))
    assert fresh.get("/api/v1/members").status_code == 200
    assert owner.get("/api/v1/members").status_code == 401
    assert owner.refresh().status_code == 401
    assert _audit("auth.user_enabled", user.id)[0].details == {"actor": OPERATOR}


def test_revoke_user_sessions_ends_every_session_and_leaves_other_users_alone(
    client, email_outbox, settings
):
    email, first = _signup(client, email_outbox, "revoke-me")
    second = Login(client, _login(client, email))
    _, bystander = _signup(client, email_outbox, "bystander")

    count = _service(
        access.revoke_user_sessions,
        email=email,
        reason="Lost laptop",
        actor_label=OPERATOR,
        settings=settings,
    )
    assert count == 2
    for session in (first, second):
        assert session.get("/api/v1/members").status_code == 401
        assert session.refresh().status_code == 401
    assert bystander.get("/api/v1/members").status_code == 200
    again = Login(client, _login(client, email))
    assert again.get("/api/v1/members").status_code == 200
    [event] = _audit("auth.sessions_revoked", first.user_id)
    assert event.details == {"actor": OPERATOR, "reason": "Lost laptop", "sessions": 2}


def test_group_removal_refuses_the_workspace_and_keeps_a_direct_membership(client, email_outbox):
    _, owner = _signup(client, email_outbox, "fed-owner")
    org = owner.get("/api/v1/organizations").json()["items"][0]["id"]
    created = owner.post(f"/api/v1/organizations/{org}/workspaces", json={"name": "Sales"})
    assert created.status_code == 201, created.text
    sales = created.json()["id"]
    _mapping(org, sales, "sales", WorkspaceRole.MEMBER, created_by=owner.user_id)
    user_id, identity_id = _make_oidc_user(org, groups=("sales",))
    with inspection_session_factory()() as db:
        user = db.get(User, user_id)
        user.password_hash = hash_password(PASSWORD)
        email = user.email
        db.add(
            Membership(workspace_id=owner.workspace_id, user_id=user_id, role=WorkspaceRole.VIEWER)
        )
        db.commit()
    _sync(identity_id, ("sales",))
    federated = Login(client, _login(client, email))
    assert federated.get("/api/v1/meetings", workspace_id=sales).status_code == 200

    # The IdP no longer lists the group: the reconciler (ADR 0066) removes the row.
    _sync(identity_id, ())
    refused = federated.get("/api/v1/meetings", workspace_id=sales)
    assert refused.status_code == 403
    assert _error(refused)["code"] == "workspace_access_denied"
    kept = federated.get("/api/v1/meetings", workspace_id=owner.workspace_id)
    assert kept.status_code == 200


def test_reset_user_mfa_removes_the_factor_and_codes_and_revokes_sessions(
    client, email_outbox, settings
):
    email, owner = _signup(client, email_outbox, "mfa-reset")
    secret = owner.post("/api/v1/auth/mfa/totp/enroll").json()["secret"]
    code = totp_at(decode_totp_secret(secret), int(datetime.now(UTC).timestamp()))
    confirmed = owner.post("/api/v1/auth/mfa/totp/confirm", json={"code": code})
    assert confirmed.status_code == 200, confirmed.text

    _service(
        access.reset_user_mfa,
        email=email,
        reason="Phone lost",
        actor_label=OPERATOR,
        settings=settings,
    )
    with inspection_session_factory()() as db:
        assert (
            db.scalars(select(UserMfaTotp).where(UserMfaTotp.user_id == owner.user_id)).all() == []
        )
        assert (
            db.scalars(
                select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == owner.user_id)
            ).all()
            == []
        )
    assert owner.get("/api/v1/auth/mfa").status_code == 401
    fresh = Login(client, _login(client, email))
    assert fresh.get("/api/v1/auth/mfa").json()["totp_enrolled"] is False
    assert fresh.post("/api/v1/auth/mfa/totp/enroll").status_code == 200
    assert _audit("auth.mfa_reset", owner.user_id)[0].details == {
        "actor": OPERATOR,
        "reason": "Phone lost",
    }


@pytest.mark.parametrize(
    "function",
    [
        pytest.param(access.disable_user, id="disable"),
        pytest.param(access.revoke_user_sessions, id="revoke"),
        pytest.param(access.reset_user_mfa, id="mfa-reset"),
    ],
)
def test_cut_off_refuses_an_unknown_user_and_a_blank_reason(
    client, email_outbox, settings, function
):
    email, _ = _signup(client, email_outbox, "refusals")
    common = {"actor_label": OPERATOR, "settings": settings}
    assert _refusal(function, email="nobody@example.com", reason="x", **common) == "user_not_found"
    assert _refusal(function, email=email, reason="  ", **common) == "reason_required"
    assert _refusal(access.enable_user, email="nobody@example.com", **common) == "user_not_found"


# --- break-glass (D7) --------------------------------------------------------------------


def test_with_the_setting_off_designation_and_opening_are_refused(client, email_outbox, settings):
    off = settings.model_copy(update={"break_glass_enabled": False})
    email, _ = _signup(client, email_outbox, "bg-off")
    common = {"actor_label": OPERATOR, "settings": off}
    assert _refusal(access.designate_break_glass, email=email, **common) == "break_glass_disabled"
    assert (
        _refusal(access.open_break_glass_window, email=email, minutes=5, reason="x", **common)
        == "break_glass_disabled"
    )
    assert Settings(_env_file=None).break_glass_enabled is False


def test_outside_a_window_a_password_login_looks_like_a_wrong_password(
    client, email_outbox, settings, alerts
):
    email, user_id, _ = _designate(client, email_outbox, settings, "bg-closed")
    wrong = _login(client, email, "WrongPass999")
    refused = _login(client, email)
    assert refused.status_code == wrong.status_code == 401
    assert (
        _error(refused)
        == _error(wrong)
        == {
            "code": "invalid_credentials",
            "message": "Invalid credentials",
        }
    )
    assert alerts == [("login_refused", email, {"method": "password"})]
    assert _audit("auth.break_glass_login_refused", user_id)[0].details == {"method": "password"}
    assert [s for s in _sessions(user_id) if s.revoked_at is None] == []


def test_inside_an_open_window_the_login_works_and_ends_with_the_window(
    client, email_outbox, settings, alerts
):
    email, user_id, workspace_id = _designate(client, email_outbox, settings, "bg-open")
    window = _open(email, settings, minutes=30)
    assert alerts[0][0] == "window_opened" and alerts[0][1] == email
    assert alerts[0][2]["reason"] == "Primary IdP is down" and alerts[0][2]["minutes"] == 30

    session = Login(client, _login(client, email))
    assert session.get("/api/v1/members").status_code == 200
    [row] = [s for s in _sessions(user_id) if s.revoked_at is None]
    assert row.break_glass_window_id == window.id
    expires = (
        row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    )
    window_end = window.expires_at
    window_end = window_end.replace(tzinfo=UTC) if window_end.tzinfo is None else window_end
    assert expires <= window_end
    assert [event for event, *_ in alerts] == ["window_opened", "login"]
    opened = _audit("auth.break_glass_opened", user_id)[0].details
    assert opened["actor"] == OPERATOR and opened["minutes"] == 30
    assert opened["reason"] == "Primary IdP is down" and opened["expires_at"]
    assert _audit("auth.break_glass_login", user_id)[0].details == {"method": "password"}
    assert session.refresh().status_code == 200
    assert workspace_id


def test_an_expired_window_ends_the_session_and_its_refresh(client, email_outbox, settings):
    email, _, _ = _designate(client, email_outbox, settings, "bg-expire")
    window = _open(email, settings)
    session = Login(client, _login(client, email))
    assert session.get("/api/v1/members").status_code == 200
    with inspection_session_factory()() as db:
        db.execute(
            update(BreakGlassWindow)
            .where(BreakGlassWindow.id == window.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    assert session.get("/api/v1/members").status_code == 401
    assert session.refresh().status_code == 401


def test_closing_the_window_ends_the_session_at_once(client, email_outbox, settings, alerts):
    email, user_id, _ = _designate(client, email_outbox, settings, "bg-close")
    _open(email, settings)
    session = Login(client, _login(client, email))
    closed = _service(
        access.close_break_glass_window, email=email, actor_label=OPERATOR, settings=settings
    )
    assert closed is not None and closed.closed_by == OPERATOR
    assert session.get("/api/v1/members").status_code == 401
    assert session.refresh().status_code == 401
    assert [event for event, *_ in alerts] == ["window_opened", "login", "window_closed"]
    assert _audit("auth.break_glass_closed", user_id)
    assert _login(client, email).status_code == 401


def test_turning_the_setting_off_ends_an_open_window(client, email_outbox, settings):
    email, _, _ = _designate(client, email_outbox, settings, "bg-setting")
    _open(email, settings)
    session = Login(client, _login(client, email))
    assert session.get("/api/v1/members").status_code == 200
    off = settings.model_copy(update={"break_glass_enabled": False})
    client.app.dependency_overrides[get_settings] = lambda: off
    assert session.get("/api/v1/members").status_code == 401
    assert session.refresh().status_code == 401


def test_window_rules(client, email_outbox, settings):
    email, _, _ = _designate(client, email_outbox, settings, "bg-rules")
    plain, _ = _signup(client, email_outbox, "bg-plain")
    common = {"actor_label": OPERATOR, "settings": settings}
    too_long = settings.break_glass_max_window_minutes + 1
    opening = access.open_break_glass_window
    assert _refusal(opening, email=email, minutes=too_long, reason="x", **common) == (
        "window_too_long"
    )
    assert _refusal(opening, email=email, minutes=0, reason="x", **common) == "window_invalid"
    assert _refusal(opening, email=email, minutes=5, reason=" ", **common) == "reason_required"
    assert _refusal(opening, email=plain, minutes=5, reason="x", **common) == "not_designated"
    assert (
        _refusal(opening, email="nobody@example.com", minutes=5, reason="x", **common)
        == "user_not_found"
    )
    _open(email, settings, minutes=settings.break_glass_max_window_minutes)
    assert _refusal(opening, email=email, minutes=5, reason="x", **common) == (
        "window_already_open"
    )


def test_otp_and_google_logins_are_refused_even_inside_a_window(
    client, email_outbox, sms_outbox, settings, alerts, monkeypatch
):
    email, user_id, _ = _designate(client, email_outbox, settings, "bg-otp")
    phone = "+989121230001"
    with inspection_session_factory()() as db:
        db.execute(update(User).where(User.id == user_id).values(phone_e164=phone))
        db.add(
            AuthIdentity(
                user_id=user_id, provider=IdentityProvider.PHONE, subject=phone, profile={}
            )
        )
        db.commit()
    _open(email, settings)

    requested = client.post("/api/v1/auth/otp/request", json={"phone": "09121230001"})
    assert requested.status_code == 202, requested.text
    verified = client.post(
        "/api/v1/auth/otp/verify",
        json={"challenge_id": requested.json()["challenge_id"], "code": sms_outbox[-1]["code"]},
    )
    assert verified.status_code == 401
    assert _error(verified) == {"code": "invalid_credentials", "message": "Invalid credentials"}

    async def fake_google_oauth(_db, *, state, code, settings):
        with get_session_factory()() as db:
            return db.get(User, user_id), "/app"

    monkeypatch.setattr(auth_router, "complete_google_oauth", fake_google_oauth)
    google = client.get(
        "/api/v1/auth/google/callback",
        params={"state": "s", "code": "c"},
        follow_redirects=False,
    )
    assert google.status_code == 401
    assert _error(google)["code"] == "invalid_credentials"
    assert [s for s in _sessions(user_id) if s.revoked_at is None] == []
    assert [(event, details) for event, _, details in alerts[1:]] == [
        ("login_refused", {"method": "otp"}),
        ("login_refused", {"method": "google"}),
    ]


def test_designation_needs_a_password_and_no_federated_identity(client, email_outbox, settings):
    email, session = _signup(client, email_outbox, "bg-federated")
    org = session.get("/api/v1/organizations").json()["items"][0]["id"]
    _graft_oidc_identity(session.user_id, org)
    common = {"actor_label": OPERATOR, "settings": settings}
    assert _refusal(access.designate_break_glass, email=email, **common) == "federated_account"

    other, other_session = _signup(client, email_outbox, "bg-nopassword")
    with inspection_session_factory()() as db:
        db.execute(update(User).where(User.id == other_session.user_id).values(password_hash=None))
        db.commit()
    assert _refusal(access.designate_break_glass, email=other, **common) == "password_required"


def test_designation_revokes_sessions_and_undesignation_closes_the_window(
    client, email_outbox, settings
):
    email, session = _signup(client, email_outbox, "bg-undesignate")
    _service(access.designate_break_glass, email=email, actor_label=OPERATOR, settings=settings)
    assert session.get("/api/v1/members").status_code == 401
    window = _open(email, settings)
    inside = Login(client, _login(client, email))

    off = settings.model_copy(update={"break_glass_enabled": False})
    user = _service(access.undesignate_break_glass, email=email, actor_label=OPERATOR, settings=off)
    assert user.is_break_glass is False
    assert inside.get("/api/v1/members").status_code == 401
    with inspection_session_factory()() as db:
        assert db.get(BreakGlassWindow, window.id).closed_by == OPERATOR
    assert _audit("auth.break_glass_designated", user.id)
    assert _audit("auth.break_glass_undesignated", user.id)[0].details == {
        "actor": OPERATOR,
        "window_closed": True,
    }
    assert Login(client, _login(client, email)).get("/api/v1/members").status_code == 200


def test_close_is_allowed_with_the_setting_off(client, email_outbox, settings):
    email, _, _ = _designate(client, email_outbox, settings, "bg-close-off")
    _open(email, settings)
    off = settings.model_copy(update={"break_glass_enabled": False})
    closed = _service(
        access.close_break_glass_window, email=email, actor_label=OPERATOR, settings=off
    )
    assert closed is not None and closed.closed_at is not None


@pytest.mark.parametrize(
    "layer",
    [
        pytest.param("seam", id="seam-raises"),
        pytest.param("email", id="email-send-raises"),
    ],
)
def test_a_failing_alert_breaks_neither_the_window_nor_the_login(
    client, email_outbox, settings, monkeypatch, layer
):
    def boom(*_args, **_kwargs):
        raise RuntimeError("smtp down")

    if layer == "seam":
        monkeypatch.setattr(access, "send_break_glass_alert", boom)
    else:
        monkeypatch.setattr(email_service, "send_email_sync", boom)
    email, _, _ = _designate(client, email_outbox, settings, f"bg-alert-{layer}")
    window = _open(email, settings)
    assert window.id
    session = Login(client, _login(client, email))
    assert session.get("/api/v1/members").status_code == 200
    _service(access.close_break_glass_window, email=email, actor_label=OPERATOR, settings=settings)
    assert _login(client, email).status_code == 401


def test_the_real_seam_mails_every_address_and_holds_no_secret(settings, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    def capture(to: str, subject: str, body: str) -> bool:
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr(email_service, "send_email_sync", capture)
    many = settings.model_copy(update={"break_glass_alert_emails": "a@example.com, b@example.com"})
    access.send_break_glass_alert(
        many, event="login", user_email="bg@example.com", details={"method": "password"}
    )
    assert [to for to, *_ in sent] == ["a@example.com", "b@example.com"]
    assert all("login" in subject for _, subject, _ in sent)
    assert "bg@example.com" in sent[0][2] and "method: password" in sent[0][2]
    assert settings.jwt_secret not in sent[0][2] and settings.token_pepper not in sent[0][2]


def test_a_normal_user_login_and_requests_are_unchanged(client, email_outbox, settings, alerts):
    email, session = _signup(client, email_outbox, "bg-control")
    assert session.get("/api/v1/members").status_code == 200
    [row] = _sessions(session.user_id)
    assert row.break_glass_window_id is None
    expires = (
        row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    )
    assert expires > datetime.now(UTC) + timedelta(days=settings.refresh_token_days - 1)
    assert session.refresh().status_code == 200
    assert alerts == []
    assert email


def test_the_metric_counts_window_and_login_events(client, email_outbox, settings, alerts):
    def value(event: str) -> float | None:
        return metrics.REGISTRY.get_sample_value(
            "homayar_break_glass_events_total", {"event": event}
        )

    if value("window_opened") is None:
        pytest.skip("counters are written to the multiprocess directory")
    before = {event: value(event) for event in metrics.BREAK_GLASS_EVENTS}
    email, _, _ = _designate(client, email_outbox, settings, "bg-metric")
    assert _login(client, email).status_code == 401
    _open(email, settings)
    Login(client, _login(client, email))
    _service(access.close_break_glass_window, email=email, actor_label=OPERATOR, settings=settings)
    after = {event: value(event) for event in metrics.BREAK_GLASS_EVENTS}
    assert {event: after[event] - before[event] for event in after} == {
        "window_opened": 1,
        "window_closed": 1,
        "login": 1,
        "login_refused": 1,
    }


# --- API keys (D9, SPEC section 15) ------------------------------------------------------


def test_a_break_glass_account_never_uses_api_keys(client, email_outbox, settings):
    email, session = _signup(client, email_outbox, "bg-keys")
    before = _mint_key(session)
    _service(access.designate_break_glass, email=email, actor_label=OPERATOR, settings=settings)
    workspace_id = session.workspace_id

    outside = _by_key(client, before, workspace_id)
    assert outside.status_code == 401 and _error(outside)["code"] == "api_key_invalid"
    _open(email, settings)
    inside_session = Login(client, _login(client, email))
    inside = _by_key(client, before, workspace_id)
    assert inside.status_code == 401 and _error(inside)["code"] == "api_key_invalid"
    created = inside_session.post(
        "/api/v1/api-keys",
        json={"name": "bg", "scopes": ["meeting:read"], "expires_in_days": 7},
    )
    assert created.status_code == 403 and _error(created)["code"] == "permission_denied"
    with inspection_session_factory()() as db:
        names = db.scalars(select(ApiKey.name).where(ApiKey.workspace_id == workspace_id)).all()
    assert names == ["ci"]

    _, normal = _signup(client, email_outbox, "keys-control")
    assert _by_key(client, _mint_key(normal), normal.workspace_id).status_code == 200


# --- the CLI (D8) ------------------------------------------------------------------------


def test_the_cli_returns_0_on_success_and_2_with_the_code_on_a_refusal(
    client, email_outbox, settings, alerts, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.getpass, "getuser", lambda: OPERATOR)
    email, session = _signup(client, email_outbox, "cli-user")

    assert cli.main(["break-glass", "designate", "--email", email]) == 0
    assert capsys.readouterr().out.startswith("designated user=")
    assert (
        cli.main(["break-glass", "open", "--email", email, "--minutes", "15", "--reason", "drill"])
        == 0
    )
    assert capsys.readouterr().out.startswith("opened window=")
    assert cli.main(["break-glass", "status"]) == 0
    status = capsys.readouterr().out
    assert f"designated user={session.user_id}" in status and "open window=" in status
    assert cli.main(["break-glass", "close", "--email", email]) == 0
    assert capsys.readouterr().out.startswith("closed window=")
    assert cli.main(["revoke-sessions", "--email", email, "--reason", "drill"]) == 0
    assert capsys.readouterr().out.strip() == "revoked sessions=0"

    off = settings.model_copy(update={"break_glass_enabled": False})
    monkeypatch.setattr(cli, "get_settings", lambda: off)
    assert (
        cli.main(["break-glass", "open", "--email", email, "--minutes", "5", "--reason", "x"]) == 2
    )
    assert capsys.readouterr().err.strip() == "break_glass_disabled"
    assert cli.main(["disable-user", "--email", "nobody@example.com", "--reason", "x"]) == 2
    assert capsys.readouterr().err.strip() == "user_not_found"

    assert cli.main(["disable-user", "--email", email, "--reason", "offboarding"]) == 0
    assert _audit("auth.user_disabled", session.user_id)[0].details["actor"] == OPERATOR
    assert cli.main(["enable-user", "--email", email]) == 0
    assert cli.main(["mfa-reset", "--email", email, "--reason", "lost phone"]) == 0
    assert cli.main(["break-glass", "undesignate", "--email", email]) == 0
    capsys.readouterr()


# --- turn 4: the open race and the CLI's stdout ----------------------------------------


@pytest.mark.postgres
def test_two_concurrent_opens_store_one_window(client, email_outbox, settings, alerts):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL row locks")
    email, user_id, _ = _designate(client, email_outbox, settings, "bg-race")
    common = {"email": email, "minutes": 10, "actor_label": OPERATOR, "settings": settings}
    outcome: dict[str, str] = {}

    def second_open() -> None:
        with get_session_factory()() as db:
            try:
                access.open_break_glass_window(db, reason="second", **common)
                db.commit()
                outcome["result"] = "opened"
            except PrivilegedAccessError as exc:
                db.rollback()
                outcome["result"] = exc.code

    first = get_session_factory()()
    try:
        access.open_break_glass_window(first, reason="first", **common)
        second = threading.Thread(target=second_open)
        second.start()
        # The first open holds the users row (FOR UPDATE) until it commits.
        second.join(timeout=1.0)
        assert second.is_alive(), "the second open did not wait for the first"
        first.commit()
        second.join(timeout=30)
        assert not second.is_alive()
    finally:
        first.close()
    assert outcome == {"result": "window_already_open"}
    with inspection_session_factory()() as db:
        reasons = db.scalars(
            select(BreakGlassWindow.reason).where(BreakGlassWindow.user_id == user_id)
        ).all()
    assert reasons == ["first"]


def test_the_cli_writes_only_its_result_to_stdout(
    client, email_outbox, settings, monkeypatch, capfd
):
    email, _, _ = _designate(client, email_outbox, settings, "cli-stdout")
    # No recipients: the real alert seam logs BREAK_GLASS_ALERT_NO_RECIPIENTS.
    quiet = settings.model_copy(update={"break_glass_alert_emails": ""})
    monkeypatch.setattr(cli, "get_settings", lambda: quiet)
    # setup_logging's shape (a root handler on the real stdout) plus a named logger's own
    # stdout handler, whatever an earlier test in this worker did to logging.
    root_handler = logging.StreamHandler(sys.__stdout__)
    named = logging.getLogger("homayar")
    named_handler = logging.StreamHandler(sys.__stdout__)
    monkeypatch.setattr(logging.getLogger(), "handlers", [root_handler])
    monkeypatch.setattr(named, "handlers", [named_handler])
    monkeypatch.setattr(named, "disabled", False)
    monkeypatch.setattr(named, "level", logging.INFO)
    capfd.readouterr()

    code = cli.main(
        ["break-glass", "open", "--email", email, "--minutes", "5", "--reason", "drill"]
    )

    out, err = capfd.readouterr()
    assert code == 0
    assert out.startswith("opened window=") and out.count("\n") == 1, out
    assert err.count("BREAK_GLASS_ALERT_NO_RECIPIENTS") == 2, err
    # The handlers write to stdout again after the command.
    assert root_handler.stream is sys.__stdout__ and named_handler.stream is sys.__stdout__
