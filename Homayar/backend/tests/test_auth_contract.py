"""Negative cases from docs/architecture/auth-session-contract.md (WP03-T02)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import Response
from freezegun import freeze_time

from backend.config import Settings
from backend.routers.v1.auth import _set_session_cookies
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services import google_oauth_service
from backend.services.auth_service import SessionTokens
from backend.services.google_oauth_service import (
    AccountLinkRequiredError,
    GoogleOAuthError,
    complete_google_oauth,
    start_google_oauth,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


class TestOtpChallenges:
    def test_code_is_bound_to_its_challenge_and_a_new_request_invalidates_the_old_one(
        self, client, sms_outbox, settings: Settings
    ):
        with freeze_time(datetime.now(UTC)) as frozen:
            first = client.post("/api/v1/auth/otp/request", json={"phone": "09121230001"})
            assert first.status_code == 202
            first_id, first_code = first.json()["challenge_id"], sms_outbox[-1]["code"]

            frozen.tick(settings.otp_resend_cooldown_seconds + 1)
            second = client.post("/api/v1/auth/otp/request", json={"phone": "09121230001"})
            assert second.status_code == 202
            second_id, second_code = second.json()["challenge_id"], sms_outbox[-1]["code"]
            assert second_id != first_id

            cross = client.post(
                "/api/v1/auth/otp/verify", json={"challenge_id": second_id, "code": first_code}
            )
            assert cross.status_code == 400

            stale = client.post(
                "/api/v1/auth/otp/verify", json={"challenge_id": first_id, "code": first_code}
            )
            assert stale.status_code == 400, "superseded challenge must not authenticate"

            valid = client.post(
                "/api/v1/auth/otp/verify", json={"challenge_id": second_id, "code": second_code}
            )
            assert valid.status_code == 200, valid.text


class TestSessionCookies:
    def test_login_cookies_are_scoped_httponly_and_lax(self, client, email_outbox):
        credentials = register_user(client, email_outbox)
        response = client.post(
            "/api/v1/auth/login",
            json={"identifier": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200
        cookies = {
            header.split("=", 1)[0]: header.lower()
            for header in response.headers.get_list("set-cookie")
        }
        assert set(cookies) == {ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE}
        assert "httponly" in cookies[ACCESS_COOKIE] and "httponly" in cookies[REFRESH_COOKIE]
        assert "httponly" not in cookies[CSRF_COOKIE]
        assert all("samesite=lax" in value for value in cookies.values())
        assert "path=/api/v1/auth" in cookies[REFRESH_COOKIE]
        assert "path=/;" in cookies[ACCESS_COOKIE] or cookies[ACCESS_COOKIE].endswith("path=/")
        # The test settings run over plain HTTP; production requires SECURE_COOKIES.
        assert all("; secure" not in value for value in cookies.values())

    def test_secure_cookies_setting_marks_every_cookie_secure(self, settings: Settings):
        secure = settings.model_copy(
            update={"secure_cookies": True, "cookie_domain": "app.example"}
        )
        response = Response()
        tokens = SessionTokens(
            access_token="a",  # noqa: S106 - placeholder values
            refresh_token="r",  # noqa: S106
            csrf_token="c",  # noqa: S106
            access_expires_in=60,
        )
        _set_session_cookies(response, tokens, secure)
        values = [header.lower() for header in response.headers.getlist("set-cookie")]
        assert len(values) == 3
        assert all("; secure" in value and "domain=app.example" in value for value in values)


def _google_enabled(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "enable_google_auth": True,
            "google_client_id": "test-client-id",
            "google_client_secret": "test-client-secret",
        }
    )


class _FakeTokenResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"id_token": "fake-id-token"}


class _FakeHttpClient:
    async def post(self, *_args, **_kwargs) -> _FakeTokenResponse:
        return _FakeTokenResponse()


def _start(db, settings: Settings, **kwargs) -> tuple[str, str]:
    url = start_google_oauth(db, settings=settings, **kwargs)
    query = parse_qs(urlparse(url).query)
    return query["state"][0], query["nonce"][0]


def _claims(*, sub: str, email: str, nonce: str) -> dict:
    return {
        "iss": "https://accounts.google.com",
        "email_verified": True,
        "nonce": nonce,
        "sub": sub,
        "email": email,
        "name": "Google User",
    }


class TestGoogleLinking:
    def test_link_start_requires_an_authenticated_csrf_session(self, client):
        response = client.get("/api/v1/auth/google/link/start", follow_redirects=False)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_google_sign_in_never_merges_into_an_existing_email_account(
        self, client, email_outbox, settings: Settings, monkeypatch
    ):
        credentials = register_user(client, email_outbox, email="owner@example.com")
        google = _google_enabled(settings)
        with inspection_session_factory()() as db:
            state, nonce = _start(db, google)
            db.commit()
            monkeypatch.setattr(
                google_oauth_service.google_id_token,
                "verify_oauth2_token",
                lambda *_a, **_k: _claims(sub="sub-1", email=credentials["email"], nonce=nonce),
            )
            with pytest.raises(AccountLinkRequiredError):
                await complete_google_oauth(
                    db, state=state, code="code", settings=google, http_client=_FakeHttpClient()
                )

    @pytest.mark.asyncio
    async def test_google_identity_cannot_be_linked_to_two_accounts(
        self, client, email_outbox, settings: Settings, monkeypatch
    ):
        first = register_user(client, email_outbox, email="first@example.com")
        first_id = login_user(client, first)["user"]["id"]
        second = register_user(client, email_outbox, email="second@example.com")
        second_id = login_user(client, second)["user"]["id"]
        google = _google_enabled(settings)
        with inspection_session_factory()() as db:
            state, nonce = _start(db, google, intent="link", link_user_id=first_id)
            db.commit()
            monkeypatch.setattr(
                google_oauth_service.google_id_token,
                "verify_oauth2_token",
                lambda *_a, **_k: _claims(sub="shared-sub", email="g@example.com", nonce=nonce),
            )
            user, _ = await complete_google_oauth(
                db, state=state, code="code", settings=google, http_client=_FakeHttpClient()
            )
            assert user.id == first_id
            db.commit()

            state, nonce = _start(db, google, intent="link", link_user_id=second_id)
            db.commit()
            monkeypatch.setattr(
                google_oauth_service.google_id_token,
                "verify_oauth2_token",
                lambda *_a, **_k: _claims(sub="shared-sub", email="g@example.com", nonce=nonce),
            )
            with pytest.raises(GoogleOAuthError, match="another account"):
                await complete_google_oauth(
                    db, state=state, code="code", settings=google, http_client=_FakeHttpClient()
                )
