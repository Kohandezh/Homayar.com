"""Privileged MFA: TOTP core, enrolment, verification, recovery codes and the gate (HOM-129).

ADR 0076. The shared ``settings`` fixture turns the gate off for older suites; this
module turns it on.  Every TOTP code is computed from the secret the API returned,
so the tests need no clock control; ``_forget_last_step`` lets one test use the
same 30-second step twice where replay protection is not the subject.
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select, update

from backend.config import Settings, get_settings
from backend.database.saas_models import (
    AuditEvent,
    LoginSession,
    Membership,
    MfaRecoveryCode,
    OrganizationMembership,
    OrganizationRole,
    User,
    UserMfaTotp,
    WorkspaceRole,
)
from backend.security.authorization import Permission
from backend.security.context import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    require_platform_operator_read,
)
from backend.security.totp import (
    decode_totp_secret,
    generate_totp_secret,
    hotp,
    otpauth_uri,
    totp_at,
    verify_totp,
)
from backend.services.mfa_service import MfaError, decrypt_totp_secret
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user

MEMBERS = "/api/v1/members"
WORKSPACE_SETTINGS = "/api/v1/admin/workspace-settings"
MFA = "/api/v1/auth/mfa"
ENROLL = "/api/v1/auth/mfa/totp/enroll"
CONFIRM = "/api/v1/auth/mfa/totp/confirm"
VERIFY = "/api/v1/auth/mfa/verify"
RECOVERY_CODES = "/api/v1/auth/mfa/recovery-codes"
PLATFORM_PROBE = "/api/v1/test-hom129/platform-probe"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"privileged_mfa_required": True})


# --- TOTP core (RFC 4226, RFC 6238) ------------------------------------------------------

RFC6238_KEYS = {
    "sha1": b"12345678901234567890",
    "sha256": b"12345678901234567890123456789012",
    "sha512": b"1234567890123456789012345678901234567890123456789012345678901234",
}
RFC6238_VECTORS = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize(
    ("unix_time", "sha1", "sha256", "sha512"),
    [pytest.param(*row, id=f"t{row[0]}") for row in RFC6238_VECTORS],
)
def test_totp_matches_every_rfc6238_vector(
    unix_time: int, sha1: str, sha256: str, sha512: str
) -> None:
    for algorithm, expected in (("sha1", sha1), ("sha256", sha256), ("sha512", sha512)):
        code = totp_at(RFC6238_KEYS[algorithm], unix_time, digits=8, algorithm=algorithm)
        assert code == expected, algorithm


def test_hotp_matches_the_rfc4226_vectors() -> None:
    expected = [
        "755224",
        "287082",
        "359152",
        "969429",
        "338314",
        "254676",
        "287922",
        "162583",
        "399871",
        "520489",
    ]
    assert [hotp(b"12345678901234567890", counter) for counter in range(10)] == expected


def test_generated_secret_is_20_random_bytes_in_unpadded_base32() -> None:
    first, second = generate_totp_secret(), generate_totp_secret()
    assert len(first) == 32 and "=" not in first and first != second
    assert len(decode_totp_secret(first)) == 20
    assert base64.b32encode(decode_totp_secret(first)).decode() == first


def test_verify_totp_accepts_the_drift_window_once_and_refuses_the_rest() -> None:
    secret = generate_totp_secret()
    key = decode_totp_secret(secret)
    now = 1_700_000_015
    step = now // 30
    for offset in (-1, 0, 1):
        code = totp_at(key, now + offset * 30)
        assert verify_totp(secret, code, unix_time=now, last_used_step=None) == step + offset
    for offset in (-2, 2):
        code = totp_at(key, now + offset * 30)
        assert verify_totp(secret, code, unix_time=now, last_used_step=None) is None
    current = totp_at(key, now)
    assert verify_totp(secret, current, unix_time=now, last_used_step=step) is None
    assert verify_totp(secret, current, unix_time=now, last_used_step=step + 1) is None
    assert verify_totp(secret, current, unix_time=now, last_used_step=step - 1) == step
    spaced = f"{current[:3]} {current[3:]}"
    assert verify_totp(secret, spaced, unix_time=now, last_used_step=None) == step
    for bad in (current[:5], current + "0", "abcdef", "", "１２３４５６"):
        assert verify_totp(secret, bad, unix_time=now, last_used_step=None) is None


def test_otpauth_uri_quotes_the_issuer_and_the_account() -> None:
    uri = otpauth_uri("JBSWY3DPEHPK3PXP", account="a b@example.com", issuer="Homayar Co")
    parts = urlsplit(uri)
    assert parts.scheme == "otpauth" and parts.netloc == "totp"
    assert parts.path == "/Homayar%20Co:a%20b%40example.com"
    assert parse_qs(parts.query) == {
        "secret": ["JBSWY3DPEHPK3PXP"],
        "issuer": ["Homayar Co"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }


# --- settings ----------------------------------------------------------------------------

VALID_MFA_KEY = "k1:" + base64.b64encode(b"k" * 32).decode()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"privileged_mfa_max_age_minutes": 4}, id="max-age-low"),
        pytest.param({"privileged_mfa_max_age_minutes": 1441}, id="max-age-high"),
        pytest.param({"mfa_enrollment_fresh_login_minutes": 0}, id="fresh-login-low"),
        pytest.param({"mfa_enrollment_fresh_login_minutes": 61}, id="fresh-login-high"),
        pytest.param({"break_glass_max_window_minutes": 0}, id="window-low"),
        pytest.param({"break_glass_max_window_minutes": 241}, id="window-high"),
        pytest.param({"mfa_secret_keys": "k1:not-base64!!"}, id="keyring-invalid"),
        pytest.param(
            {"mfa_secret_keys": "k1:" + base64.b64encode(b"short").decode()},
            id="keyring-short-key",
        ),
        pytest.param({"mfa_secret_keys": VALID_MFA_KEY}, id="active-key-missing"),
        pytest.param(
            {"mfa_secret_keys": VALID_MFA_KEY, "mfa_secret_active_key_id": "k2"},
            id="active-key-unknown",
        ),
        pytest.param({"mfa_secret_active_key_id": "k1"}, id="active-key-without-keyring"),
    ],
)
def test_settings_refuse_out_of_range_values_and_a_bad_keyring(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_settings_defaults_and_bounds() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.privileged_mfa_required is True
    assert defaults.privileged_mfa_max_age_minutes == 720
    assert defaults.mfa_enrollment_fresh_login_minutes == 15
    assert defaults.break_glass_enabled is False
    assert defaults.break_glass_max_window_minutes == 60
    assert defaults.mfa_totp_issuer == "Homayar"
    bounds = Settings(
        _env_file=None,
        privileged_mfa_max_age_minutes=5,
        mfa_enrollment_fresh_login_minutes=60,
        break_glass_max_window_minutes=240,
        mfa_secret_keys=VALID_MFA_KEY,
        mfa_secret_active_key_id="k1",  # noqa: S106 - a key id
    )
    assert bounds.privileged_mfa_max_age_minutes == 5
    assert VALID_MFA_KEY not in repr(bounds)
    assert bounds.model_dump()["mfa_secret_keys"] != VALID_MFA_KEY


def _production(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+psycopg://h:p@db.example.invalid/h?sslmode=verify-full",
        "redis_url": "rediss://:p@redis.example.invalid:6380/0",
        "jwt_secret": "j" * 48,
        "token_pepper": "p" * 48,
        "secure_cookies": True,
        "auto_create_schema": False,
        "allow_mock_providers": False,
        "_env_file": None,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.fixture
def config_log(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch):
    # An Alembic fileConfig earlier in this worker may have disabled the logger.
    monkeypatch.setattr(logging.getLogger("backend.config"), "disabled", False)
    caplog.set_level(logging.WARNING, logger="backend.config")
    return caplog


def _mfa_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "backend.config" and "PRIVILEGED_MFA" in record.getMessage()
    ]


def test_production_with_the_gate_on_and_no_keyring_starts_with_one_warning(
    config_log: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(**_production())
    assert settings.privileged_mfa_required is True
    warnings = _mfa_warnings(config_log)
    assert len(warnings) == 1 and "PRIVILEGED_MFA_KEYRING_MISSING" in warnings[0]


def test_production_with_the_gate_off_starts_with_one_warning(
    config_log: pytest.LogCaptureFixture,
) -> None:
    Settings(**_production(privileged_mfa_required=False))
    warnings = _mfa_warnings(config_log)
    assert len(warnings) == 1 and "PRIVILEGED_MFA_DISABLED" in warnings[0]


@pytest.mark.parametrize(
    "environment",
    [pytest.param("development", id="development"), pytest.param("staging", id="staging")],
)
def test_the_empty_keyring_warning_shows_in_every_environment_but_test(
    config_log: pytest.LogCaptureFixture, environment: str
) -> None:
    Settings(_env_file=None, environment=environment)
    warnings = _mfa_warnings(config_log)
    assert len(warnings) == 1 and "PRIVILEGED_MFA_KEYRING_MISSING" in warnings[0]


def test_the_test_environment_and_a_disabled_gate_log_no_keyring_warning(
    config_log: pytest.LogCaptureFixture,
) -> None:
    Settings(_env_file=None, environment="test")
    Settings(_env_file=None, environment="development", privileged_mfa_required=False)
    assert _mfa_warnings(config_log) == []


def test_production_with_a_keyring_starts_without_an_mfa_warning(
    config_log: pytest.LogCaptureFixture,
) -> None:
    keyring = {"mfa_secret_keys": VALID_MFA_KEY, "mfa_secret_active_key_id": "k1"}
    Settings(**_production(**keyring))
    assert _mfa_warnings(config_log) == []
    with pytest.raises(ValidationError, match="MFA_SECRET_KEYS"):
        Settings(**_production(**{**keyring, "mfa_secret_keys": "k1:not-base64!!"}))


# --- helpers -----------------------------------------------------------------------------


class Persona:
    """One signed-in session of one user, sent with explicit cookies."""

    def __init__(self, client, credentials: dict[str, str]) -> None:
        session = login_user(client, credentials)
        self.client = client
        self.credentials = credentials
        self.cookies = {
            name: value
            for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
            if (value := client.cookies.get(name))
        }
        client.cookies.clear()
        self.user_id: str = session["user"]["id"]
        self.workspace_id: str = session["workspaces"][0]["id"]
        self.organization_id: str = session["workspaces"][0]["organization_id"]

    def _headers(self, workspace_id: str | None) -> dict[str, str]:
        return {
            "X-CSRF-Token": self.cookies[CSRF_COOKIE],
            "X-Workspace-ID": workspace_id or self.workspace_id,
        }

    def get(self, path: str, *, workspace_id: str | None = None):
        return self.client.get(path, cookies=self.cookies, headers=self._headers(workspace_id))

    def post(self, path: str, *, json: dict | None = None, workspace_id: str | None = None):
        return self.client.post(
            path, json=json, cookies=self.cookies, headers=self._headers(workspace_id)
        )

    def patch(self, path: str, *, json: dict, workspace_id: str | None = None):
        return self.client.patch(
            path, json=json, cookies=self.cookies, headers=self._headers(workspace_id)
        )

    def login_again(self) -> Persona:
        return Persona(self.client, self.credentials)


def _signup(client, email_outbox, name: str) -> Persona:
    credentials = register_user(client, email_outbox, email=f"{name}@example.com", name=name)
    client.cookies.clear()
    return Persona(client, credentials)


def _code(secret: str) -> str:
    return totp_at(decode_totp_secret(secret), int(time.time()))


def _forget_last_step(user_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(UserMfaTotp).where(UserMfaTotp.user_id == user_id).values(last_used_step=None)
        )
        db.commit()


def _enrol(persona: Persona) -> tuple[str, list[str]]:
    started = persona.post(ENROLL)
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = persona.post(CONFIRM, json={"code": _code(secret)})
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


def _verify(persona: Persona, secret: str):
    _forget_last_step(persona.user_id)
    return persona.post(VERIFY, json={"code": _code(secret)})


def _status(persona: Persona) -> dict:
    response = persona.get(MFA)
    assert response.status_code == 200, response.text
    return response.json()


def _code_of(response) -> str | None:
    error = response.json().get("error")
    return error.get("code") if isinstance(error, dict) else None


def _add_membership(workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    with inspection_session_factory()() as db:
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role=role))
        db.commit()


def _shift_sessions(user_id: str, **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(LoginSession).where(LoginSession.user_id == user_id).values(**values))
        db.commit()


def _audit_rows(user_id: str) -> list[tuple[str, dict]]:
    with inspection_session_factory()() as db:
        return [
            (event_type, details)
            for event_type, details in db.execute(
                select(AuditEvent.event_type, AuditEvent.details)
                .where(AuditEvent.target_id == user_id)
                .order_by(AuditEvent.occurred_at)
            )
        ]


def _audit_types(user_id: str) -> list[str]:
    return [event_type for event_type, _ in _audit_rows(user_id)]


# --- enrolment and verification ----------------------------------------------------------


def test_a_fresh_session_enrols_and_gets_ten_distinct_recovery_codes(client, email_outbox):
    owner = _signup(client, email_outbox, "enrol-owner")
    assert _status(owner) == {
        "totp_enrolled": False,
        "recovery_codes_remaining": 0,
        "session_verified": False,
        "required": True,
    }

    started = owner.post(ENROLL)
    assert started.status_code == 200, started.text
    body = started.json()
    assert set(body) == {"secret", "otpauth_uri"}
    assert body["otpauth_uri"].startswith("otpauth://totp/Homayar:enrol-owner%40example.com?")
    assert f"secret={body['secret']}" in body["otpauth_uri"]
    assert _status(owner)["totp_enrolled"] is False  # pending is not enrolled

    confirmed = owner.post(CONFIRM, json={"code": _code(body["secret"])})
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    for code in codes:
        assert len(code) == 11 and code[5] == "-" and code == code.lower()
    assert _status(owner) == {
        "totp_enrolled": True,
        "recovery_codes_remaining": 10,
        "session_verified": True,
        "required": True,
    }
    assert "auth.mfa_totp_enrolled" in _audit_types(owner.user_id)
    assert owner.get(MEMBERS).status_code == 200


def test_enrolment_needs_a_fresh_login_when_no_factor_exists(client, email_outbox):
    owner = _signup(client, email_outbox, "stale-owner")
    _shift_sessions(owner.user_id, created_at=datetime.now(UTC) - timedelta(minutes=16))

    refused = owner.post(ENROLL)
    assert refused.status_code == 403
    assert _code_of(refused) == "mfa_reauthentication_required"

    fresh = owner.login_again()
    _enrol(fresh)
    assert _status(fresh)["totp_enrolled"] is True


def test_confirm_follows_the_same_freshness_rule(client, email_outbox):
    owner = _signup(client, email_outbox, "stale-confirm")
    secret = owner.post(ENROLL).json()["secret"]
    _shift_sessions(owner.user_id, created_at=datetime.now(UTC) - timedelta(minutes=16))
    refused = owner.post(CONFIRM, json={"code": _code(secret)})
    assert refused.status_code == 403
    assert _code_of(refused) == "mfa_reauthentication_required"


def test_re_enrolment_needs_a_verified_session_and_keeps_the_old_factor(client, email_outbox):
    owner = _signup(client, email_outbox, "re-enrol")
    old_secret, _ = _enrol(owner)
    second = owner.login_again()

    refused = second.post(ENROLL)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_required"

    assert _verify(second, old_secret).status_code == 200
    started = second.post(ENROLL)
    assert started.status_code == 200, started.text
    new_secret = started.json()["secret"]
    assert new_secret != old_secret

    # The pending factor does not replace the working one before confirmation.
    third = owner.login_again()
    assert _verify(third, old_secret).status_code == 200
    _forget_last_step(owner.user_id)
    confirmed = second.post(CONFIRM, json={"code": _code(new_secret)})
    assert confirmed.status_code == 200, confirmed.text

    fourth = owner.login_again()
    _forget_last_step(owner.user_id)
    assert fourth.post(VERIFY, json={"code": _code(old_secret)}).status_code == 400
    assert _verify(fourth, new_secret).status_code == 200


def test_confirm_without_a_pending_secret_or_with_a_wrong_code(client, email_outbox):
    owner = _signup(client, email_outbox, "confirm-errors")
    missing = owner.post(CONFIRM, json={"code": "123456"})
    assert missing.status_code == 409 and _code_of(missing) == "mfa_enrollment_not_started"

    secret = owner.post(ENROLL).json()["secret"]
    wrong = str((int(_code(secret)) + 1) % 1_000_000).zfill(6)
    refused = owner.post(CONFIRM, json={"code": wrong})
    assert refused.status_code == 400 and _code_of(refused) == "mfa_code_invalid"

    with inspection_session_factory()() as db:
        db.execute(
            update(UserMfaTotp)
            .where(UserMfaTotp.user_id == owner.user_id)
            .values(pending_created_at=datetime.now(UTC) - timedelta(minutes=16))
        )
        db.commit()
    expired = owner.post(CONFIRM, json={"code": _code(secret)})
    assert expired.status_code == 409 and _code_of(expired) == "mfa_enrollment_not_started"


def test_a_wrong_code_and_a_replayed_code_are_refused(client, email_outbox):
    owner = _signup(client, email_outbox, "replay")
    secret, _ = _enrol(owner)
    second = owner.login_again()

    wrong = str((int(_code(secret)) + 1) % 1_000_000).zfill(6)
    refused = second.post(VERIFY, json={"code": wrong})
    assert refused.status_code == 400 and _code_of(refused) == "mfa_code_invalid"
    assert "auth.mfa_verify_failed" in _audit_types(owner.user_id)

    _forget_last_step(owner.user_id)
    code = _code(secret)
    first = second.post(VERIFY, json={"code": code})
    assert first.status_code == 200, first.text
    assert first.json() == {"verified": True, "method": "totp", "recovery_codes_remaining": 10}
    replayed = owner.login_again().post(VERIFY, json={"code": code})
    assert replayed.status_code == 400 and _code_of(replayed) == "mfa_code_invalid"
    assert ("auth.mfa_verified", {"method": "totp"}) in _audit_rows(owner.user_id)


def test_verify_without_a_factor_is_refused(client, email_outbox):
    owner = _signup(client, email_outbox, "no-factor")
    response = owner.post(VERIFY, json={"code": "123456"})
    assert response.status_code == 409 and _code_of(response) == "mfa_not_enrolled"


def test_a_recovery_code_verifies_once_in_any_case_and_without_the_hyphen(client, email_outbox):
    owner = _signup(client, email_outbox, "recovery")
    _, codes = _enrol(owner)
    second = owner.login_again()

    used = second.post(VERIFY, json={"code": codes[0].upper().replace("-", "")})
    assert used.status_code == 200, used.text
    assert used.json() == {
        "verified": True,
        "method": "recovery_code",
        "recovery_codes_remaining": 9,
    }
    assert second.get(MEMBERS).status_code == 200
    again = owner.login_again().post(VERIFY, json={"code": codes[0]})
    assert again.status_code == 400 and _code_of(again) == "mfa_code_invalid"
    spaced = owner.login_again().post(VERIFY, json={"code": f" {codes[1].upper()} "})
    assert spaced.status_code == 200 and spaced.json()["recovery_codes_remaining"] == 8
    assert "auth.mfa_recovery_code_used" in _audit_types(owner.user_id)
    with inspection_session_factory()() as db:
        stored = db.scalars(
            select(MfaRecoveryCode.code_hash).where(MfaRecoveryCode.user_id == owner.user_id)
        ).all()
    for code in codes:
        assert all(code.replace("-", "") not in value for value in stored)


def test_regenerating_recovery_codes_invalidates_the_old_set(client, email_outbox):
    owner = _signup(client, email_outbox, "regenerate")
    _, old_codes = _enrol(owner)

    regenerated = owner.post(RECOVERY_CODES)
    assert regenerated.status_code == 200, regenerated.text
    new_codes = regenerated.json()["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)
    assert "auth.mfa_recovery_codes_regenerated" in _audit_types(owner.user_id)

    second = owner.login_again()
    refused = second.post(RECOVERY_CODES)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_required"
    assert second.post(VERIFY, json={"code": old_codes[0]}).status_code == 400
    assert second.post(VERIFY, json={"code": new_codes[0]}).status_code == 200


def test_regenerating_without_a_factor_is_refused(client, email_outbox):
    owner = _signup(client, email_outbox, "regenerate-none")
    response = owner.post(RECOVERY_CODES)
    assert response.status_code == 409 and _code_of(response) == "mfa_not_enrolled"


def test_confirm_and_verify_share_a_rate_limit_of_ten_per_window(client, email_outbox):
    owner = _signup(client, email_outbox, "rate-limit")
    secret = owner.post(ENROLL).json()["secret"]
    wrong = str((int(_code(secret)) + 5) % 1_000_000).zfill(6)
    for _ in range(5):
        assert owner.post(CONFIRM, json={"code": wrong}).status_code == 400
    assert owner.post(CONFIRM, json={"code": _code(secret)}).status_code == 200
    second = owner.login_again()
    for _ in range(4):
        assert second.post(VERIFY, json={"code": wrong}).status_code == 400
    limited = second.post(VERIFY, json={"code": wrong})
    assert limited.status_code == 429 and _code_of(limited) == "rate_limited"


def test_the_stored_secret_is_encrypted_and_bound_to_its_user(client, email_outbox, settings):
    owner = _signup(client, email_outbox, "encrypted")
    other = _signup(client, email_outbox, "encrypted-other")
    secret, _ = _enrol(owner)
    _enrol(other)

    with inspection_session_factory()() as db:
        row = db.scalar(select(UserMfaTotp).where(UserMfaTotp.user_id == owner.user_id))
        assert row is not None
        for column in UserMfaTotp.__table__.columns:
            value = getattr(row, column.key)
            if isinstance(value, bytes):
                assert secret.encode() not in value and decode_totp_secret(secret) not in value
            elif isinstance(value, str):
                assert secret not in value
        assert (
            decrypt_totp_secret(
                key_id=row.encryption_key_id,
                nonce=row.nonce,
                ciphertext=row.ciphertext,
                user_id=owner.user_id,
                settings=settings,
            )
            == secret
        )
        with pytest.raises(MfaError) as caught:
            decrypt_totp_secret(
                key_id=row.encryption_key_id,
                nonce=row.nonce,
                ciphertext=row.ciphertext,
                user_id=other.user_id,
                settings=settings,
            )
        assert caught.value.status_code == 503
        copied = (row.encryption_key_id, row.nonce, row.ciphertext)
        db.execute(
            update(UserMfaTotp)
            .where(UserMfaTotp.user_id == other.user_id)
            .values(encryption_key_id=copied[0], nonce=copied[1], ciphertext=copied[2])
        )
        db.commit()

    _forget_last_step(other.user_id)
    stolen = other.login_again().post(VERIFY, json={"code": _code(secret)})
    assert stolen.status_code == 503 and _code_of(stolen) == "mfa_unavailable"
    assert secret not in str(_audit_rows(owner.user_id))


def test_enrolment_without_the_keyring_answers_503(client, email_outbox, settings):
    owner = _signup(client, email_outbox, "no-keyring")
    client.app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"mfa_secret_keys": "", "mfa_secret_active_key_id": ""}
    )
    response = owner.post(ENROLL)
    assert response.status_code == 503 and _code_of(response) == "mfa_unavailable"
    # The gate stays closed: no factor can exist, so the owner stays refused.
    refused = owner.get(MEMBERS)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_enrollment_required"


def test_mfa_routes_need_a_session_and_csrf(client, email_outbox):
    owner = _signup(client, email_outbox, "csrf")
    assert client.get(MFA).status_code == 401
    no_csrf = client.post(ENROLL, cookies=owner.cookies)
    assert no_csrf.status_code == 403 and _code_of(no_csrf) == "csrf_failed"
    api_key = client.get(MFA, headers={"Authorization": "Bearer hk_abcd1234.not-a-real-key"})
    assert api_key.status_code == 401 and _code_of(api_key) == "api_key_not_accepted"


# --- the gate ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [
        pytest.param(WorkspaceRole.OWNER, id="owner"),
        pytest.param(WorkspaceRole.ADMIN, id="admin"),
    ],
)
def test_workspace_owner_and_admin_are_gated_on_read_and_write(client, email_outbox, role):
    owner = _signup(client, email_outbox, "gate-owner")
    if role is WorkspaceRole.OWNER:
        person = owner
    else:
        person = _signup(client, email_outbox, "gate-admin")
        _add_membership(owner.workspace_id, person.user_id, WorkspaceRole.ADMIN)
    workspace = owner.workspace_id

    for response in (
        person.get(MEMBERS, workspace_id=workspace),
        person.patch(WORKSPACE_SETTINGS, json={"retention_days": 30}, workspace_id=workspace),
    ):
        assert response.status_code == 403, response.text
        assert _code_of(response) == "mfa_enrollment_required"

    secret, _ = _enrol(person)
    assert person.get(MEMBERS, workspace_id=workspace).status_code == 200

    unverified = person.login_again()
    for response in (
        unverified.get(MEMBERS, workspace_id=workspace),
        unverified.patch(WORKSPACE_SETTINGS, json={"retention_days": 30}, workspace_id=workspace),
    ):
        assert response.status_code == 403 and _code_of(response) == "mfa_required"
    assert _status(unverified)["session_verified"] is False

    assert _verify(unverified, secret).status_code == 200
    assert unverified.get(MEMBERS, workspace_id=workspace).status_code == 200
    patched = unverified.patch(
        WORKSPACE_SETTINGS, json={"retention_days": 30}, workspace_id=workspace
    )
    assert patched.status_code == 200, patched.text


@pytest.mark.parametrize(
    "role",
    [
        pytest.param(WorkspaceRole.MEMBER, id="member"),
        pytest.param(WorkspaceRole.VIEWER, id="viewer"),
    ],
)
def test_member_and_viewer_are_never_asked_for_mfa(client, email_outbox, role):
    owner = _signup(client, email_outbox, "allow-owner")
    person = _signup(client, email_outbox, f"allow-{role.value}")
    _add_membership(owner.workspace_id, person.user_id, role)

    assert person.get(MEMBERS, workspace_id=owner.workspace_id).status_code == 200
    assert person.get("/api/v1/meetings", workspace_id=owner.workspace_id).status_code == 200
    denied = person.patch(
        WORKSPACE_SETTINGS, json={"retention_days": 30}, workspace_id=owner.workspace_id
    )
    assert denied.status_code == 403 and _code_of(denied) == "permission_denied"


def test_organization_owner_and_admin_are_gated_and_an_analyst_is_not(client, email_outbox):
    owner = _signup(client, email_outbox, "org-owner")
    admin = _signup(client, email_outbox, "org-admin")
    analyst = _signup(client, email_outbox, "org-analyst")
    with inspection_session_factory()() as db:
        db.add(
            OrganizationMembership(
                organization_id=owner.organization_id,
                user_id=admin.user_id,
                role=OrganizationRole.ADMIN,
            )
        )
        db.add(
            OrganizationMembership(
                organization_id=owner.organization_id,
                user_id=analyst.user_id,
                role=OrganizationRole.ANALYST,
            )
        )
        db.commit()
    path = f"/api/v1/organizations/{owner.organization_id}"

    for person in (owner, admin):
        refused = person.get(path)
        assert refused.status_code == 403 and _code_of(refused) == "mfa_enrollment_required"
        _enrol(person)
        assert person.get(path).status_code == 200
    reached = analyst.get(path)
    assert reached.status_code == 200, reached.text


def test_a_platform_operator_is_gated_even_where_the_workspace_role_is_not(client, email_outbox):
    probe = APIRouter()

    @probe.get(PLATFORM_PROBE)
    def platform_probe(
        _context=Depends(require_platform_operator_read(Permission.MEETING_READ)),
    ) -> dict[str, bool]:
        return {"ok": True}

    client.app.include_router(probe)
    owner = _signup(client, email_outbox, "platform-owner")
    operator = _signup(client, email_outbox, "platform-operator")
    _add_membership(owner.workspace_id, operator.user_id, WorkspaceRole.MEMBER)
    with inspection_session_factory()() as db:
        db.execute(update(User).where(User.id == operator.user_id).values(system_role="operator"))
        db.commit()

    # The member role alone is not gated ...
    assert operator.get(MEMBERS, workspace_id=owner.workspace_id).status_code == 200
    # ... but the platform guard is, for any role.
    refused = operator.get(PLATFORM_PROBE, workspace_id=owner.workspace_id)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_enrollment_required"
    secret, _ = _enrol(operator)
    assert operator.get(PLATFORM_PROBE, workspace_id=owner.workspace_id).status_code == 200
    unverified = operator.login_again()
    refused = unverified.get(PLATFORM_PROBE, workspace_id=owner.workspace_id)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_required"
    assert _verify(unverified, secret).status_code == 200
    assert unverified.get(PLATFORM_PROBE, workspace_id=owner.workspace_id).status_code == 200


def test_a_verification_older_than_the_max_age_no_longer_passes(client, email_outbox, settings):
    owner = _signup(client, email_outbox, "max-age")
    _enrol(owner)
    assert owner.get(MEMBERS).status_code == 200
    old = datetime.now(UTC) - timedelta(minutes=settings.privileged_mfa_max_age_minutes + 1)
    _shift_sessions(owner.user_id, mfa_verified_at=old)
    refused = owner.get(MEMBERS)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_required"
    assert _status(owner)["session_verified"] is False


def test_refresh_keeps_the_verification_and_a_new_login_does_not_inherit_it(client, email_outbox):
    owner = _signup(client, email_outbox, "refresh")
    _enrol(owner)
    refreshed = client.post(
        "/api/v1/auth/refresh",
        cookies=owner.cookies,
        headers={"X-CSRF-Token": owner.cookies[CSRF_COOKIE]},
    )
    assert refreshed.status_code == 200, refreshed.text
    owner.cookies = {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }
    client.cookies.clear()
    assert owner.get(MEMBERS).status_code == 200

    another = owner.login_again()
    refused = another.get(MEMBERS)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_required"


def test_with_the_gate_off_the_owner_needs_no_mfa(client, email_outbox, settings):
    client.app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"privileged_mfa_required": False}
    )
    owner = _signup(client, email_outbox, "gate-off")
    assert owner.get(MEMBERS).status_code == 200
    patched = owner.patch(WORKSPACE_SETTINGS, json={"retention_days": 30})
    assert patched.status_code == 200, patched.text
    assert _status(owner)["required"] is False


def test_api_keys_are_not_gated_and_mfa_routes_need_no_mfa(client, email_outbox):
    owner = _signup(client, email_outbox, "api-key-owner")
    # Privileged but unverified: the MFA routes and /auth/me stay reachable.
    assert owner.get(MFA).status_code == 200
    assert owner.get("/api/v1/auth/me").status_code == 200
    refused = owner.post(
        "/api/v1/api-keys", json={"name": "ci", "scopes": ["meeting:read"], "expires_in_days": 7}
    )
    assert refused.status_code == 403 and _code_of(refused) == "mfa_enrollment_required"

    _enrol(owner)
    created = owner.post(
        "/api/v1/api-keys", json={"name": "ci", "scopes": ["meeting:read"], "expires_in_days": 7}
    )
    assert created.status_code == 201, created.text
    raw_key = created.json()["key"]

    _shift_sessions(owner.user_id, mfa_verified_at=None)
    assert owner.get("/api/v1/meetings").status_code == 403
    by_key = client.get(
        "/api/v1/meetings",
        headers={"Authorization": f"Bearer {raw_key}", "X-Workspace-ID": owner.workspace_id},
    )
    assert by_key.status_code == 200, by_key.text


# --- every guard that resolves a role is gated (SPEC section 16) ------------------------

TRENDS_TEAMS = "/api/v1/trends/teams"
TRENDS_ME = "/api/v1/trends/me"
CAPABILITIES = "/api/v1/capabilities"


def test_the_session_only_trends_routes_are_gated_for_an_owner(client, email_outbox):
    owner = _signup(client, email_outbox, "trends-owner")
    member = _signup(client, email_outbox, "trends-member")
    _add_membership(owner.workspace_id, member.user_id, WorkspaceRole.MEMBER)

    for path in (TRENDS_TEAMS, TRENDS_ME):
        refused = owner.get(path)
        assert refused.status_code == 403, (path, refused.text)
        assert _code_of(refused) == "mfa_enrollment_required", path
    # Allow-control: a member reaches the self-view without MFA.
    assert member.get(TRENDS_ME, workspace_id=owner.workspace_id).status_code == 200

    secret, _ = _enrol(owner)
    for path in (TRENDS_TEAMS, TRENDS_ME):
        assert owner.get(path).status_code == 200, path
    unverified = owner.login_again()
    assert _code_of(unverified.get(TRENDS_TEAMS)) == "mfa_required"
    assert _verify(unverified, secret).status_code == 200
    assert unverified.get(TRENDS_TEAMS).status_code == 200


def test_a_plain_workspace_route_is_gated_for_an_owner_and_not_for_a_viewer(client, email_outbox):
    owner = _signup(client, email_outbox, "caps-owner")
    viewer = _signup(client, email_outbox, "caps-viewer")
    _add_membership(owner.workspace_id, viewer.user_id, WorkspaceRole.VIEWER)

    refused = owner.get(CAPABILITIES)
    assert refused.status_code == 403 and _code_of(refused) == "mfa_enrollment_required"
    assert viewer.get(CAPABILITIES, workspace_id=owner.workspace_id).status_code == 200
    _enrol(owner)
    assert owner.get(CAPABILITIES).status_code == 200


# --- Persian and Arabic-Indic digits (SPEC section 16) -------------------------------------

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
ARABIC_INDIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def test_codes_typed_in_persian_or_arabic_indic_digits_are_accepted(client, email_outbox):
    owner = _signup(client, email_outbox, "native-digits")
    secret = owner.post(ENROLL).json()["secret"]
    confirmed = owner.post(CONFIRM, json={"code": _code(secret).translate(PERSIAN_DIGITS)})
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]

    second = owner.login_again()
    _forget_last_step(owner.user_id)
    arabic = _code(secret).translate(ARABIC_INDIC_DIGITS)
    verified = second.post(VERIFY, json={"code": arabic})
    assert verified.status_code == 200, verified.text
    assert verified.json()["method"] == "totp"

    with_digit = next(code for code in codes if any(ch.isdigit() for ch in code))
    third = owner.login_again()
    recovered = third.post(VERIFY, json={"code": with_digit.translate(PERSIAN_DIGITS)})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["method"] == "recovery_code"
    again = owner.login_again().post(VERIFY, json={"code": with_digit})
    assert again.status_code == 400 and _code_of(again) == "mfa_code_invalid"
