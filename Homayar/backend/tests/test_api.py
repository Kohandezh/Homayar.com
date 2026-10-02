from __future__ import annotations

from datetime import datetime, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from backend.config import Settings
from backend.database.saas_models import LoginSession, OtpChallenge, User
from backend.security.context import CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


class TestSystem:
    def test_liveness_is_public_and_truthful(self, client: TestClient):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["version"] == "0.5.0"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-request-id"]

    def test_readiness_reports_missing_redis(self, client: TestClient):
        response = client.get("/readyz")
        assert response.status_code == 200  # tasks_eager makes Redis optional in tests
        assert response.json()["checks"]["database"] == "ready"
        assert response.json()["checks"]["redis"] == "unavailable"

    def test_production_rejects_insecure_configuration(self):
        with pytest.raises(ValidationError):
            Settings(
                environment="production",
                database_url="sqlite:///unsafe.db",
                jwt_secret="short",
                token_pepper="short",
                _env_file=None,
            )

    def test_production_rejects_demo_backdoor(self):
        with pytest.raises(ValidationError):
            Settings(
                environment="production",
                database_url="postgresql+psycopg://db.example/homayar",
                jwt_secret="j" * 48,
                token_pepper="p" * 48,
                secure_cookies=True,
                enable_demo_account=True,
                _env_file=None,
            )


class TestEmailAuthentication:
    def test_register_verify_login_and_me(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        credentials = register_user(client, email_outbox)
        body = login_user(client, credentials)
        assert body["user"]["email"] == credentials["email"]
        assert body["user"]["email_verified"] is True
        assert body["requires_verification"] is False
        assert body["workspaces"][0]["role"] == "owner"
        assert client.cookies.get("homayar_access")
        assert client.cookies.get(REFRESH_COOKIE)
        assert client.cookies.get(CSRF_COOKIE)

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] == credentials["email"]

    def test_unverified_user_is_explicitly_limited(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        credentials = register_user(client, email_outbox, verify=False)
        body = login_user(client, credentials)
        assert body["requires_verification"] is True

    def test_verification_token_is_single_use(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        register_user(client, email_outbox, verify=False)
        token = email_outbox[-1]["token"]
        first = client.post("/api/v1/auth/verify-email", json={"token": token})
        second = client.post("/api/v1/auth/verify-email", json={"token": token})
        assert first.status_code == 200
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "invalid_verification_token"

    def test_public_registration_forbids_role_mass_assignment(self, client: TestClient):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "attacker@example.com",
                "password": "SecurePass123",
                "display_name": "Attacker",
                "role": "owner",
                "system_role": "operator",
            },
        )
        assert response.status_code == 422
        fields = {item["field"] for item in response.json()["error"]["details"]}
        assert "body.role" in fields
        assert "body.system_role" in fields

    def test_duplicate_registration_is_rejected(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        register_user(client, email_outbox)
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "OWNER@example.com",
                "password": "AnotherPass123",
                "display_name": "Duplicate",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "account_exists"

    def test_invalid_login_is_generic(self, client: TestClient):
        response = client.post(
            "/api/v1/auth/login",
            json={"identifier": "missing@example.com", "password": "WrongPassword123"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Invalid credentials"

    def test_forged_token_is_rejected(self, client: TestClient, settings: Settings):
        forged = jwt.encode(
            {
                "sub": "attacker",
                "sid": "not-a-session",
                "sv": 1,
                "type": "access",
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc).timestamp() + 300,
                "jti": "forged",
            },
            "wrong-secret",
            algorithm="HS256",
        )
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401

    def test_refresh_rotates_and_replay_revokes_session(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        credentials = register_user(client, email_outbox)
        login_user(client, credentials)
        old_refresh = client.cookies.get(REFRESH_COOKIE)
        old_csrf = client.cookies.get(CSRF_COOKIE)
        assert old_refresh and old_csrf

        refreshed = client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": old_csrf},
        )
        assert refreshed.status_code == 200
        new_refresh = client.cookies.get(REFRESH_COOKIE)
        new_csrf = client.cookies.get(CSRF_COOKIE)
        assert new_refresh and new_refresh != old_refresh

        replay = client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": old_csrf},
            cookies={REFRESH_COOKIE: old_refresh, CSRF_COOKIE: old_csrf},
        )
        assert replay.status_code == 401
        after_replay = client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": new_csrf},
            cookies={REFRESH_COOKIE: new_refresh, CSRF_COOKIE: new_csrf},
        )
        assert after_replay.status_code == 401

    def test_logout_requires_csrf_and_revokes(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        credentials = register_user(client, email_outbox)
        login_user(client, credentials)
        denied = client.post("/api/v1/auth/logout")
        assert denied.status_code == 403
        csrf = client.cookies.get(CSRF_COOKIE)
        response = client.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_password_reset_is_single_use_and_revokes_sessions(
        self, client: TestClient, email_outbox: list[dict[str, str]]
    ):
        credentials = register_user(client, email_outbox)
        login_user(client, credentials)
        reset_request = client.post(
            "/api/v1/auth/forgot-password", json={"email": credentials["email"]}
        )
        assert reset_request.status_code == 202
        token = email_outbox[-1]["token"]
        reset = client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": "NewSecurePass456"},
        )
        assert reset.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
        reused = client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": "AnotherSecure789"},
        )
        assert reused.status_code == 400
        login_user(
            client,
            {"email": credentials["email"], "password": "NewSecurePass456"},
        )

    def test_unknown_password_reset_is_not_enumerable(self, client: TestClient):
        response = client.post(
            "/api/v1/auth/forgot-password", json={"email": "unknown@example.com"}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "accepted"


class TestPhoneAuthentication:
    def test_phone_normalization_otp_login_and_replay_rejection(
        self, client: TestClient, sms_outbox: list[dict[str, str]]
    ):
        requested = client.post("/api/v1/auth/otp/request", json={"phone": "۰۹۱۲ ۱۲۳ ۴۵۶۷"})
        assert requested.status_code == 202
        assert sms_outbox[-1]["phone"] == "+989121234567"
        challenge_id = requested.json()["challenge_id"]
        verified = client.post(
            "/api/v1/auth/otp/verify",
            json={"challenge_id": challenge_id, "code": sms_outbox[-1]["code"]},
        )
        assert verified.status_code == 200
        assert verified.json()["user"]["phone_e164"] == "+989121234567"
        assert verified.json()["user"]["phone_verified"] is True
        replay = client.post(
            "/api/v1/auth/otp/verify",
            json={"challenge_id": challenge_id, "code": sms_outbox[-1]["code"]},
        )
        assert replay.status_code == 400

    @pytest.mark.parametrize(
        ("phone", "native"),
        [
            ("09120000311", "۰۱۲۳۴۵۶۷۸۹"),
            ("09120000312", "٠١٢٣٤٥٦٧٨٩"),
        ],
        ids=["persian", "arabic_indic"],
    )
    def test_otp_code_typed_in_native_digits_signs_in(
        self, client: TestClient, sms_outbox: list[dict[str, str]], phone: str, native: str
    ):
        to_native = str.maketrans("0123456789", native)
        requested = client.post("/api/v1/auth/otp/request", json={"phone": phone})
        assert requested.status_code == 202
        challenge_id = requested.json()["challenge_id"]
        code = sms_outbox[-1]["code"]
        wrong = f"{(int(code) + 1) % 1_000_000:06d}".translate(to_native)
        refused = client.post(
            "/api/v1/auth/otp/verify", json={"challenge_id": challenge_id, "code": wrong}
        )
        assert refused.status_code == 400
        assert refused.json()["error"]["code"] == "invalid_otp"
        verified = client.post(
            "/api/v1/auth/otp/verify",
            json={"challenge_id": challenge_id, "code": code.translate(to_native)},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["user"]["phone_e164"] == "+98" + phone[1:]
        assert client.get("/api/v1/auth/me").status_code == 200

    def test_otp_resend_cooldown(
        self, client: TestClient, sms_outbox: list[dict[str, str]]
    ):
        first = client.post("/api/v1/auth/otp/request", json={"phone": "09121234567"})
        second = client.post("/api/v1/auth/otp/request", json={"phone": "+989121234567"})
        assert first.status_code == 202
        assert second.status_code == 429
        assert int(second.headers["retry-after"]) > 0

    def test_otp_attempt_limit_is_persisted(
        self, client: TestClient, sms_outbox: list[dict[str, str]], settings: Settings
    ):
        requested = client.post("/api/v1/auth/otp/request", json={"phone": "09125550000"})
        challenge_id = requested.json()["challenge_id"]
        for _ in range(settings.otp_max_attempts):
            response = client.post(
                "/api/v1/auth/otp/verify",
                json={"challenge_id": challenge_id, "code": "000000"},
            )
            assert response.status_code == 400
        final = client.post(
            "/api/v1/auth/otp/verify",
            json={"challenge_id": challenge_id, "code": sms_outbox[-1]["code"]},
        )
        assert final.status_code in {400, 429}
        with inspection_session_factory()() as db:
            challenge = db.scalar(
                select(OtpChallenge).where(OtpChallenge.id == challenge_id)
            )
            assert challenge is not None
            assert challenge.attempts == settings.otp_max_attempts

    def test_invalid_phone_is_rejected(self, client: TestClient):
        response = client.post("/api/v1/auth/otp/request", json={"phone": "+12025550123"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_phone"


def test_deactivated_user_session_is_rejected(
    client: TestClient, email_outbox: list[dict[str, str]]
):
    credentials = register_user(client, email_outbox)
    body = login_user(client, credentials)
    with inspection_session_factory()() as db:
        user = db.get(User, body["user"]["id"])
        assert user is not None
        user.is_active = False
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_refresh_session_is_stored_as_hash(
    client: TestClient, email_outbox: list[dict[str, str]]
):
    credentials = register_user(client, email_outbox)
    login_user(client, credentials)
    raw = client.cookies.get(REFRESH_COOKIE)
    assert raw
    with inspection_session_factory()() as db:
        session = db.scalar(select(LoginSession))
        assert session is not None
        assert raw not in session.refresh_token_hash
