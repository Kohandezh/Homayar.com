"""REGISTRATION_ENABLED (HOM-336): closing self-sign-up, invitations stay open.

Every test runs against both values of the setting. The "open" runs are the
allow-controls from the SPEC table: they prove the gate itself changes nothing
when registration is enabled.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.config import Settings
from backend.database.saas_models import (
    AuditEvent,
    AuthIdentity,
    IdentityProvider,
    Membership,
    OneTimeToken,
    Organization,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
)
from backend.security.context import CSRF_COOKIE
from backend.security.normalization import normalize_email, normalize_iranian_mobile
from backend.security.tokens import random_token, token_digest
from backend.services.auth_service import register_email_user
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import TEST_APP_ROLE, inspection_session_factory
from backend.tests._isolation import canonical_database_url, empty_database_url
from backend.tests._transport import APP_REDIS_URL


@pytest.fixture(params=[True, False], ids=["open", "closed"])
def settings(request: pytest.FixtureRequest, tmp_path: Path) -> Settings:
    # Same shape as the conftest fixture, plus the HOM-336 switch and Google
    # credentials so the callback route can be exercised.
    if "client" in request.fixturenames:
        database_url = canonical_database_url(tmp_path)
    else:
        database_url = empty_database_url(tmp_path)
    return Settings(
        environment="test",
        deployment_mode="cloud",
        database_url=database_url,
        database_app_role=TEST_APP_ROLE,
        redis_url=APP_REDIS_URL,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        auto_create_schema=True,
        tasks_eager=True,
        secure_cookies=False,
        enable_free_demo=True,
        demo_quota_seconds=1800,
        storage_backend="local",
        storage_local_root=str(tmp_path / "objects"),
        upload_quarantine_dir=str(tmp_path / "quarantine"),
        upload_dir=str(tmp_path / "legacy-uploads"),
        enable_phone_auth=True,
        asanak_api_key="test-only-key",
        asanak_sender="TEST",
        enable_google_auth=True,
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",  # noqa: S106 - placeholder value
        cors_origins="http://testserver",
        trusted_hosts="testserver,localhost,127.0.0.1",
        frontend_url="http://testserver/app",
        public_url="http://testserver",
        trend_judgment_warmup_enabled=False,
        registration_enabled=request.param,
        _env_file=None,
    )


REGISTER_PAYLOAD = {
    "email": "newcomer@example.com",
    "password": "SecurePass123",
    "display_name": "Newcomer",
    "locale": "en",
}


def error_code(response) -> str:
    return response.json()["error"]["code"]


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    return {"X-CSRF-Token": token} if token else {}


def table_counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        return {
            table.__tablename__: count
            for table, count in (
                (User, db.scalar(select(func.count()).select_from(User))),
                (Organization, db.scalar(select(func.count()).select_from(Organization))),
                (Workspace, db.scalar(select(func.count()).select_from(Workspace))),
                (AuthIdentity, db.scalar(select(func.count()).select_from(AuthIdentity))),
                (OneTimeToken, db.scalar(select(func.count()).select_from(OneTimeToken))),
                (AuditEvent, db.scalar(select(func.count()).select_from(AuditEvent))),
            )
        }


def service_account(
    settings: Settings, *, email: str, name: str = "Service Owner"
) -> tuple[str, str]:
    """Create an account through the service, not the gated HTTP route."""
    with inspection_session_factory()() as db:
        user, verification_token = register_email_user(
            db,
            email=email,
            password="SecurePass123",  # noqa: S106 - fixture credential
            display_name=name,
            locale="en",
            settings=settings,
        )
        db.commit()
        return user.id, verification_token


def seed_invitation(
    settings: Settings,
    *,
    email: str,
    state: str = "open",
) -> str:
    """An owner's personal workspace plus one invitation for ``email``.

    ``state`` is one of ``open``, ``expired``, ``revoked``, ``accepted``.
    Returns the raw invitation token.
    """
    now = datetime.now(UTC)
    with inspection_session_factory()() as db:
        owner_id, _ = service_account(settings, email="inviter@example.com")
        membership = db.scalar(select(Membership).where(Membership.user_id == owner_id))
        assert membership is not None
        raw_token = random_token(32)
        invitation = WorkspaceInvitation(
            workspace_id=membership.workspace_id,
            invited_by_user_id=owner_id,
            email=normalize_email(email),
            role=WorkspaceRole.MEMBER,
            token_hash=token_digest(f"workspace-invitation:{raw_token}", settings),
            expires_at=now + timedelta(days=7),
        )
        if state == "expired":
            invitation.expires_at = now - timedelta(days=1)
        elif state == "revoked":
            invitation.revoked_at = now
        elif state == "accepted":
            invitation.accepted_at = now
        db.add(invitation)
        db.commit()
        return raw_token


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Generator[None, None, None]:
    RateLimiter.reset_memory()
    yield
    RateLimiter.reset_memory()


class TestRegisterGate:
    def test_registration_enabled_decides_the_default_for_a_stranger(
        self, client: TestClient, settings: Settings, email_outbox: list[dict[str, str]]
    ):
        before = table_counts()
        response = client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)

        if settings.registration_enabled:
            assert response.status_code == 202, response.text
            assert response.json()["status"] == "pending_verification"
            assert email_outbox and email_outbox[-1]["kind"] == "verify"
        else:
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "registration_closed"
            message = response.json()["error"]["message"]
            assert message == "Self-registration is closed on this server"
            assert email_outbox == []
            assert table_counts() == before

    def test_a_closed_server_never_reveals_an_existing_email(
        self, client: TestClient, settings: Settings
    ):
        service_account(settings, email=REGISTER_PAYLOAD["email"])

        response = client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)

        if settings.registration_enabled:
            assert response.status_code == 409, response.text
            assert error_code(response) == "account_exists"
        else:
            # The 403 comes before the duplicate check: no account enumeration.
            assert response.status_code == 403, response.text
            assert error_code(response) == "registration_closed"

    def test_an_open_invitation_for_the_same_email_passes_the_gate(
        self, client: TestClient, settings: Settings, email_outbox: list[dict[str, str]]
    ):
        # Different letter case on the registration e-mail must still match.
        raw_token = seed_invitation(settings, email="Newcomer@Example.com")
        payload = {**REGISTER_PAYLOAD, "email": "newcomer@example.com"}
        response = client.post(
            "/api/v1/auth/register", json={**payload, "invitation_token": raw_token}
        )

        assert response.status_code == 202, response.text
        assert response.json()["status"] == "pending_verification"
        assert email_outbox and email_outbox[-1]["kind"] == "verify"
        # The invitation is neither accepted nor consumed by registering.
        with inspection_session_factory()() as db:
            stored = db.scalar(
                select(WorkspaceInvitation).where(
                    WorkspaceInvitation.token_hash
                    == token_digest(f"workspace-invitation:{raw_token}", settings)
                )
            )
            assert stored is not None and stored.accepted_at is None

    @pytest.mark.parametrize(
        "state", ["other-email", "expired", "revoked", "accepted", "unknown"]
    )
    def test_everything_but_an_open_same_email_invitation_is_refused(
        self, client: TestClient, settings: Settings, state: str
    ):
        token = "t" * 43
        if state == "unknown":
            pass
        elif state == "other-email":
            token = seed_invitation(settings, email="someone-else@example.com")
        else:
            token = seed_invitation(settings, email=REGISTER_PAYLOAD["email"], state=state)
        before = table_counts()

        response = client.post(
            "/api/v1/auth/register", json={**REGISTER_PAYLOAD, "invitation_token": token}
        )

        if settings.registration_enabled:
            # Open server: the token is accepted and ignored.
            assert response.status_code == 202, response.text
        else:
            assert response.status_code == 403, response.text
            assert error_code(response) == "registration_closed"
            assert table_counts() == before


class TestMethodsEndpoint:
    def test_methods_reports_the_registration_switch(self, client: TestClient, settings: Settings):
        response = client.get("/api/v1/auth/methods")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["registration"] is settings.registration_enabled
        assert body["password"] is True


class TestPhoneOtp:
    def test_an_unknown_phone_creates_an_account_only_when_open(
        self, client: TestClient, settings: Settings, sms_outbox: list[dict[str, str]]
    ):
        requested = client.post("/api/v1/auth/otp/request", json={"phone": "09121230001"})
        assert requested.status_code == 202, requested.text
        challenge_id = requested.json()["challenge_id"]
        before = table_counts()

        response = client.post(
            "/api/v1/auth/otp/verify",
            json={"challenge_id": challenge_id, "code": sms_outbox[-1]["code"]},
        )

        if settings.registration_enabled:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 403, response.text
            assert error_code(response) == "registration_closed"
            assert "set-cookie" not in response.headers
            assert table_counts() == before

    def test_a_known_phone_still_signs_in_while_closed(
        self, client: TestClient, settings: Settings, sms_outbox: list[dict[str, str]]
    ):
        phone = normalize_iranian_mobile("09121230002")
        now = datetime.now(UTC)
        with inspection_session_factory()() as db:
            existing = User(
                phone_e164=phone,
                phone_verified_at=now,
                display_name="",
                is_active=True,
            )
            db.add(existing)
            db.flush()
            db.add(
                AuthIdentity(
                    user_id=existing.id,
                    provider=IdentityProvider.PHONE,
                    subject=phone,
                    verified_at=now,
                    profile={},
                )
            )
            db.commit()

        requested = client.post("/api/v1/auth/otp/request", json={"phone": "09121230002"})
        assert requested.status_code == 202, requested.text
        response = client.post(
            "/api/v1/auth/otp/verify",
            json={
                "challenge_id": requested.json()["challenge_id"],
                "code": sms_outbox[-1]["code"],
            },
        )

        assert response.status_code == 200, response.text
        assert "set-cookie" in response.headers


def _google_claims(*, sub: str, email: str, nonce: str) -> dict:
    return {
        "iss": "https://accounts.google.com",
        "email_verified": True,
        "nonce": nonce,
        "sub": sub,
        "email": email,
        "name": "Google User",
    }


class _FakeTokenResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"id_token": "fake-id-token"}


class _FakeAsyncClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def post(self, *_args, **_kwargs) -> _FakeTokenResponse:
        return _FakeTokenResponse()

    async def aclose(self) -> None:
        return None


class TestGoogleCallback:
    def test_an_unknown_subject_creates_an_account_only_when_open(
        self, client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ):
        from urllib.parse import parse_qs, urlparse

        from backend.services import google_oauth_service
        from backend.services.google_oauth_service import start_google_oauth

        with inspection_session_factory()() as db:
            url = start_google_oauth(db, settings=settings)
            query = parse_qs(urlparse(url).query)
            state, nonce = query["state"][0], query["nonce"][0]
            db.commit()
        monkeypatch.setattr(google_oauth_service.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(
            google_oauth_service.google_id_token,
            "verify_oauth2_token",
            lambda *_a, **_k: _google_claims(
                sub="google-newcomer", email="google-newcomer@example.com", nonce=nonce
            ),
        )
        before = table_counts()

        response = client.get(
            "/api/v1/auth/google/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )

        if settings.registration_enabled:
            assert response.status_code == 302, response.text
            assert response.headers["location"] == "http://testserver/app"
        else:
            assert response.status_code == 302, response.text
            assert (
                response.headers["location"]
                == f"{settings.frontend_url}/login?error=registration_closed"
            )
            assert "set-cookie" not in response.headers
            assert table_counts() == before

    def test_a_known_google_user_still_signs_in_while_closed(
        self,
        client: TestClient,
        settings: Settings,
        email_outbox: list[dict[str, str]],
        monkeypatch: pytest.MonkeyPatch,
    ):
        from urllib.parse import parse_qs, urlparse

        from backend.services import google_oauth_service
        from backend.services.google_oauth_service import start_google_oauth

        user_id, _ = service_account(settings, email="google-known@example.com")
        with inspection_session_factory()() as db:
            db.add(
                AuthIdentity(
                    user_id=user_id,
                    provider=IdentityProvider.GOOGLE,
                    subject="google-known-sub",
                    verified_at=datetime.now(UTC),
                    profile={},
                )
            )
            url = start_google_oauth(db, settings=settings)
            query = parse_qs(urlparse(url).query)
            state, nonce = query["state"][0], query["nonce"][0]
            db.commit()
        monkeypatch.setattr(google_oauth_service.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr(
            google_oauth_service.google_id_token,
            "verify_oauth2_token",
            lambda *_a, **_k: _google_claims(
                sub="google-known-sub", email="google-known@example.com", nonce=nonce
            ),
        )

        response = client.get(
            "/api/v1/auth/google/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )

        assert response.status_code == 302, response.text
        assert response.headers["location"] == "http://testserver/app"
        assert "set-cookie" in response.headers


class TestInvitedJourney:
    def test_an_invited_newcomer_registers_verifies_and_accepts_while_closed(
        self,
        client: TestClient,
        settings: Settings,
        email_outbox: list[dict[str, str]],
    ):
        raw_token = seed_invitation(settings, email="invited@example.com")

        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": "invited@example.com",
                "password": "SecurePass123",
                "display_name": "Invited Person",
                "locale": "en",
                "invitation_token": raw_token,
            },
        )
        assert registered.status_code == 202, registered.text

        verified = client.post(
            "/api/v1/auth/verify-email",
            json={"token": email_outbox[-1]["token"]},
        )
        assert verified.status_code == 200, verified.text

        login = client.post(
            "/api/v1/auth/login",
            json={"identifier": "invited@example.com", "password": "SecurePass123"},
        )
        assert login.status_code == 200, login.text

        accepted = client.post(
            "/api/v1/members/invitations/accept",
            json={"token": raw_token},
            headers=csrf_headers(client),
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["membership"]["role"] == "member"

    def test_an_existing_verified_account_still_accepts_while_closed(
        self, client: TestClient, settings: Settings, email_outbox: list[dict[str, str]]
    ):
        user_id, verification_token = service_account(settings, email="existing@example.com")
        raw_token = seed_invitation(settings, email="existing@example.com")
        assert (
            client.post("/api/v1/auth/verify-email", json={"token": verification_token}).status_code
            == 200
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"identifier": "existing@example.com", "password": "SecurePass123"},
        )
        assert login.status_code == 200, login.text

        accepted = client.post(
            "/api/v1/members/invitations/accept",
            json={"token": raw_token},
            headers=csrf_headers(client),
        )

        assert accepted.status_code == 200, accepted.text


class TestExistingFlowsUnaffected:
    def test_login_me_refresh_and_the_password_reset_flow_still_work(
        self, client: TestClient, settings: Settings, email_outbox: list[dict[str, str]]
    ):
        service_account(settings, email="member@example.com")

        login = client.post(
            "/api/v1/auth/login",
            json={"identifier": "member@example.com", "password": "SecurePass123"},
        )
        assert login.status_code == 200, login.text
        assert client.get("/api/v1/auth/me").status_code == 200

        refreshed = client.post("/api/v1/auth/refresh", headers=csrf_headers(client))
        assert refreshed.status_code == 200, refreshed.text

        forgot = client.post(
            "/api/v1/auth/forgot-password", json={"email": "member@example.com"}
        )
        assert forgot.status_code == 202, forgot.text
        reset_token = email_outbox[-1]["token"]
        assert email_outbox[-1]["kind"] == "reset"

        reset = client.post(
            "/api/v1/auth/reset-password",
            json={"token": reset_token, "password": "NewSecurePass456"},
            headers=csrf_headers(client),
        )
        assert reset.status_code == 200, reset.text
        again = client.post(
            "/api/v1/auth/login",
            json={"identifier": "member@example.com", "password": "NewSecurePass456"},
        )
        assert again.status_code == 200, again.text


@pytest.mark.postgres
class TestInvitationLookupUnderRowLevelSecurity:
    """PostgreSQL only: the gate reads the invitation as the app role, with no
    signed-in user, so the token capability must be what makes the row visible
    (backend/database/rls.py, workspace_invitations policy)."""

    def test_the_lookup_succeeds_only_through_the_token_capability(
        self, client: TestClient, settings: Settings, email_outbox: list[dict[str, str]]
    ):
        raw_token = seed_invitation(settings, email=REGISTER_PAYLOAD["email"])

        response = client.post(
            "/api/v1/auth/register",
            json={**REGISTER_PAYLOAD, "invitation_token": raw_token},
        )

        assert response.status_code == 202, response.text
