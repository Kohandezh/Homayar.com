"""HOM-109: the signed offline licence (entitlements) of an on-prem install.

Every deny-case of SPEC section 3 with its allow-control. Keys are throwaway
Ed25519 pairs generated in ``tmp_path`` at run time; time is injected where the
code takes it and relative-to-now licence dates where only the wall clock
exists (HTTP and CLI); the licence cache and the ``get_settings`` cache are
cleared around every test so nothing depends on order or a shared cache.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from backend.config import Settings, reset_settings_cache
from backend.database.relational import reset_database_state
from backend.database.saas_models import (
    Entitlement,
    JobState,
    Meeting,
    ProcessingJob,
    Recording,
    UsageReservation,
    User,
    Workspace,
)
from backend.licensing.__main__ import main as licensing_main
from backend.licensing.offline_license import (
    LicenseRestrictedError,
    clear_license_cache,
    current_license_status,
    evaluate,
    format_zulu,
    status_line,
)
from backend.main import create_app
from backend.security.context import CSRF_COOKIE
from backend.services.entitlement_service import (
    QuotaExceededError,
    commit_usage,
    release_usage,
    reserve_usage,
)
from backend.services.rate_limit_service import RateLimiter
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user

REPO_ROOT = Path(__file__).resolve().parents[3]
# Injected clock for the evaluate-level tests; the base document is valid here.
NOW = datetime(2030, 6, 15, 12, 0, 0, tzinfo=UTC)
# Every licence write gets its own mtime: a same-size rewrite inside one coarse
# filesystem clock tick would otherwise keep the cache key of the old file.
_MTIMES = itertools.count(1_900_000_000 * 10**9, 10**6)
_FOLDERS = itertools.count()


# --- keys, licence files and settings ---------------------------------------------------


def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def _private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


def _fingerprint(key: Ed25519PrivateKey) -> str:
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def _z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _doc(**overrides: Any) -> dict:
    """A valid document on the injected 2030 clock (valid at ``NOW``)."""
    doc = {
        "schema": "homayar-offline-license/1",
        "license_id": "L-2030-001",
        "customer_id": "acme",
        "customer_name": "Acme Co",
        "issued_at": "2029-01-01T00:00:00Z",
        "expires_at": "2031-01-01T00:00:00Z",
        "grace_days": 14,
        "modules": ["insights", "quality"],
        "limits": {"audio_seconds": 360000, "concurrent_jobs": 4},
    }
    doc.update(overrides)
    return doc


def _now_doc(**overrides: Any) -> dict:
    """A valid document on the real wall clock (HTTP and CLI tests)."""
    now = datetime.now(UTC)
    doc = _doc(
        issued_at=_z(now - timedelta(days=30)),
        expires_at=_z(now + timedelta(days=60)),
    )
    doc.update(overrides)
    return doc


def _expired_doc(**overrides: Any) -> dict:
    """A signed-and-expired document on the real wall clock."""
    now = datetime.now(UTC)
    doc = _now_doc(
        issued_at=_z(now - timedelta(days=60)),
        expires_at=_z(now - timedelta(days=20)),
    )
    doc.update(overrides)
    return doc


def _trust_dir(tmp_path: Path) -> Path:
    trust = tmp_path / "keys"
    trust.mkdir()
    return trust


def _trust_key(trust: Path, key: Ed25519PrivateKey, name: str = "vendor.pem") -> None:
    (trust / name).write_bytes(_public_pem(key))


def _write_licence(
    tmp_path: Path,
    doc: dict,
    key: Ed25519PrivateKey,
    *,
    name: str = "license.json",
    pad_to_bytes: int = 0,
    signature: bytes | None = None,
) -> tuple[Path, Path]:
    """Writes ``<name>`` and ``<name>.sig``; the signature covers the exact bytes."""
    payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    if pad_to_bytes:
        payload = payload + b" " * (pad_to_bytes - len(payload))
    path = tmp_path / name
    path.write_bytes(payload)
    sig_path = Path(f"{path}.sig")
    sig_path.write_bytes(key.sign(payload) if signature is None else signature)
    moment = next(_MTIMES)
    for written in (path, sig_path):
        os.utime(written, ns=(moment, moment))
    return path, sig_path


def _settings_for(license_file: Path | str, trust: Path | str) -> Settings:
    return Settings(
        _env_file=None,
        offline_license_file=str(license_file),
        offline_license_trust_dir=str(trust),
    )


def _enforce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, doc: dict, **write_kwargs: Any
) -> tuple[Settings, Ed25519PrivateKey]:
    """A verified licence in a folder of its own, a trusted key, and env and
    settings pointing at it (``reserve_usage`` reads ``get_settings()``)."""
    folder = tmp_path / f"licence-{next(_FOLDERS)}"
    folder.mkdir()
    key = _key()
    trust = _trust_dir(folder)
    _trust_key(trust, key)
    license_file, _ = _write_licence(folder, doc, key, **write_kwargs)
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(license_file))
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(trust))
    reset_settings_cache()
    return _settings_for(license_file, trust), key


@pytest.fixture(autouse=True)
def _isolated_licence_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OFFLINE_LICENSE_FILE", raising=False)
    monkeypatch.delenv("OFFLINE_LICENSE_TRUST_DIR", raising=False)
    reset_settings_cache()
    clear_license_cache()
    yield
    clear_license_cache()
    reset_settings_cache()


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*args: Any, **kwargs: Any):
        raise AssertionError("licence evaluation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)


# --- 1. verification (SPEC 2.2, 2.3) ----------------------------------------------------


def test_empty_setting_is_not_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings, _ = _enforce(monkeypatch, tmp_path, _doc())
    assert evaluate(settings, NOW).enforced

    status = evaluate(_settings_for("", settings.offline_license_trust_dir), NOW)
    assert status.state == "not_enforced"
    assert status.enforced is False
    assert status.processing_allowed is True
    assert status.reason is None
    assert status.license_id is None
    assert status.modules == ()
    assert status.limits == {}
    assert status.days_left is None
    assert status.warnings == ()


@pytest.mark.parametrize("missing", ["file", "sig", "both"])
def test_missing_file_or_signature_is_state_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
):
    settings, _ = _enforce(monkeypatch, tmp_path, _doc())
    if missing in ("file", "both"):
        Path(settings.offline_license_file).unlink()
    if missing in ("sig", "both"):
        Path(f"{settings.offline_license_file}.sig").unlink()
    status = evaluate(settings, NOW)
    assert status.state == "missing"
    assert status.processing_allowed is False
    assert status.license_id is None
    assert "license_missing" in status.warnings


@pytest.mark.parametrize(
    "trust_setup",
    ["empty_setting", "missing_dir", "empty_dir", "only_non_pem_files"],
    ids=["empty-setting", "missing-dir", "empty-dir", "non-pem"],
)
def test_no_trusted_keys_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trust_setup: str
):
    key = _key()
    if trust_setup == "empty_setting":
        trust = ""
    elif trust_setup == "missing_dir":
        trust = tmp_path / "absent"
    elif trust_setup == "empty_dir":
        trust = _trust_dir(tmp_path)
    else:
        trust = _trust_dir(tmp_path)
        (trust / "notes.txt").write_bytes(b"not a key")
    license_file, _ = _write_licence(tmp_path, _doc(), key)
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(license_file))
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(trust))
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "invalid"
    assert status.reason == "no_trusted_keys"
    assert status.processing_allowed is False
    assert status.license_id is None


@pytest.mark.parametrize("bad_pem", ["private_key", "garbage"])
def test_bad_trust_key_is_invalid_and_the_same_dir_without_it_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_pem: str
):
    key = _key()
    trust = _trust_dir(tmp_path)
    if bad_pem == "private_key":
        (trust / "oops.pem").write_bytes(_private_pem(_key()))
    else:
        (trust / "oops.pem").write_bytes(b"not a pem at all")
    _trust_key(trust, key, name="zz-vendor.pem")
    license_file, _ = _write_licence(tmp_path, _doc(), key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "invalid"
    assert status.reason == "bad_trust_key"
    assert "oops.pem" in status.detail
    assert status.processing_allowed is False

    (trust / "oops.pem").unlink()
    assert evaluate(_settings_for(license_file, trust), NOW).state == "valid"


def test_key_rotation_the_second_trusted_key_verifies(tmp_path: Path):
    old, new = _key(), _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, old, name="a-old.pem")
    _trust_key(trust, new, name="b-new.pem")
    license_file, _ = _write_licence(tmp_path, _doc(), new)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "valid"
    assert status.key_fingerprint == _fingerprint(new)


def test_signature_by_an_untrusted_key_is_bad_signature(tmp_path: Path):
    signing, trusted = _key(), _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, trusted)
    license_file, _ = _write_licence(tmp_path, _doc(), signing)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "invalid"
    assert status.reason == "bad_signature"
    assert status.processing_allowed is False


@pytest.mark.parametrize("case", ["tampered_byte", "sig_63_bytes"], ids=str)
def test_bad_signatures_with_their_allow_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    if case == "tampered_byte":
        license_file, _ = _write_licence(tmp_path, _doc(), key)
        payload = bytearray(license_file.read_bytes())
        payload[7] ^= 0x20
        license_file.write_bytes(bytes(payload))
    else:
        license_file, _ = _write_licence(tmp_path, _doc(), key, signature=key.sign(b"x" * 8)[:63])
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "invalid"
    assert status.reason == "bad_signature"

    good, _ = _write_licence(tmp_path, _doc(), key)
    assert evaluate(_settings_for(good, trust), NOW).state == "valid"


@pytest.mark.parametrize("size", [65536, 65537])
def test_the_size_bound(tmp_path: Path, size: int):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    license_file, _ = _write_licence(tmp_path, _doc(), key, pad_to_bytes=size)
    assert license_file.stat().st_size == size
    status = evaluate(_settings_for(license_file, trust), NOW)
    if size == 65536:
        assert status.state == "valid"
    else:
        assert status.state == "invalid"
        assert status.reason == "too_large"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "homayar-offline-license/2"),
        ("license_id", "-leading-dash"),
        ("license_id", "x" * 65),
        ("license_id", "L-1\n"),
        ("license_id", 7),
        ("customer_id", "with space"),
        ("customer_name", "  "),
        ("customer_name", "n" * 201),
        ("customer_name", "bad\nname"),
        ("issued_at", "2029-01-01T00:00:00+00:00"),
        ("issued_at", "2029-01-01 00:00:00Z"),
        ("issued_at", "2029-01-01T00:00:00Z\n"),
        ("issued_at", "\u06f2\u06f0\u06f2\u06f9-01-01T00:00:00Z"),
        ("issued_at", "2029-02-30T00:00:00Z"),
        ("expires_at", "2028-12-31T00:00:00Z"),
        ("grace_days", True),
        ("grace_days", 91),
        ("grace_days", -1),
        ("grace_days", "7"),
        ("modules", ["quality", "quality"]),
        ("modules", ["quality", 7]),
        ("modules", "quality"),
        ("limits", {"audio_seconds": True}),
        ("limits", {"audio_seconds": -1}),
        ("limits", {"audio_seconds": "10"}),
        ("limits", [1, 2]),
        ("limits", {"warp_factor": "9"}),
    ],
)
def test_field_rule_violations_are_malformed(tmp_path: Path, field: str, value: Any):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    license_file, _ = _write_licence(tmp_path, _doc(**{field: value}), key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "invalid"
    assert status.reason == "malformed"
    assert status.license_id is None


def test_a_signed_licence_with_a_missing_or_extra_top_level_key_is_malformed(
    tmp_path: Path,
):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)

    doc = _doc()
    doc.pop("grace_days")
    license_file, _ = _write_licence(tmp_path, doc, key)
    assert evaluate(_settings_for(license_file, trust), NOW).reason == "malformed"

    license_file, _ = _write_licence(tmp_path, _doc(extra_top_level="no"), key)
    assert evaluate(_settings_for(license_file, trust), NOW).reason == "malformed"


@pytest.mark.parametrize(
    "text",
    [
        '{"schema": "homayar-offline-license/1", "schema": "homayar-offline-license/1"}',
        '{"schema": "homayar-offline-license/1", "limits": {"a": 1, "a": 2}}',
        "not json at all",
    ],
    ids=["duplicate-top", "duplicate-nested", "not-json"],
)
def test_duplicate_json_keys_and_garbage_are_malformed(tmp_path: Path, text: str):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    path = tmp_path / "license.json"
    path.write_text(text, encoding="utf-8")
    Path(f"{path}.sig").write_bytes(key.sign(text.encode("utf-8")))
    status = evaluate(_settings_for(path, trust), NOW)
    assert status.state == "invalid"
    assert status.reason == "malformed"


def test_unknown_modules_and_limits_are_ignored_and_reported(tmp_path: Path):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    doc = _doc(
        modules=["quality", "telepathy", "insights"],
        limits={"audio_seconds": 1200, "warp_factor": 9},
    )
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "valid"
    assert sorted(status.modules) == ["insights", "quality"]
    assert status.unknown_modules == ("telepathy",)
    assert status.limits == {"audio_seconds": 1200}
    assert status.unknown_limits == ("warp_factor",)


def test_null_limit_means_no_cap(tmp_path: Path):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    doc = _doc(limits={"audio_seconds": None, "concurrent_jobs": None})
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "valid"
    assert status.limits == {"audio_seconds": None, "concurrent_jobs": None}


# Amendment 3 item 1: derived dates that leave the datetime range are malformed.
@pytest.mark.parametrize(
    ("overrides", "state", "reason"),
    [
        ({"expires_at": "9999-12-31T23:59:59Z", "grace_days": 14}, "invalid", "malformed"),
        ({"expires_at": "9999-12-31T23:59:59Z", "grace_days": 0}, "valid", None),
        ({"issued_at": "0001-01-01T00:00:00Z"}, "invalid", "malformed"),
        ({"issued_at": "0001-01-02T00:00:00Z"}, "valid", None),
    ],
    ids=["max-expiry-with-grace", "max-expiry-no-grace", "min-issued", "min-issued-plus-1d"],
)
def test_overflowing_derived_dates_are_malformed(
    tmp_path: Path, overrides: dict, state: str, reason: str | None
):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    license_file, _ = _write_licence(tmp_path, _doc(**overrides), key)
    settings = _settings_for(license_file, trust)
    status = evaluate(settings, NOW)
    assert (status.state, status.reason) == (state, reason)
    assert current_license_status(settings, NOW).state == state
    if "issued_at" in overrides and state == "valid":
        assert format_zulu(status.issued_at) == "0001-01-02T00:00:00Z"
        assert "license: state=valid" in status_line(status)


def test_an_unexpected_evaluation_error_is_invalid_malformed_and_logged_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    from backend.licensing import offline_license as licensing_module

    settings, _ = _enforce(monkeypatch, tmp_path, _doc())

    def broken(_settings_like):
        raise RuntimeError("secret detail that must not be logged")

    monkeypatch.setattr(licensing_module, "_verified_license", broken)
    status = evaluate(settings, NOW)
    assert (status.state, status.reason) == ("invalid", "malformed")
    assert status.processing_allowed is False
    assert status.license_id is None
    with caplog.at_level(logging.WARNING, logger="homayar"):
        for _ in range(3):
            assert current_license_status(settings, NOW).state == "invalid"
    failures = [r.message for r in caplog.records if "EVALUATION_FAILED" in r.message]
    assert failures == ["OFFLINE_LICENSE_EVALUATION_FAILED error_type=RuntimeError"]
    assert all(record.levelno == logging.WARNING for record in caplog.records)
    assert "secret detail" not in caplog.text


# --- 2. states, days_left, warnings (SPEC 2.4) ------------------------------------------


@pytest.mark.parametrize(
    ("expires_at", "at", "state"),
    [
        ("2031-01-01T00:00:00Z", datetime(2030, 6, 15, 12), "valid"),
        ("2031-01-01T00:00:00Z", datetime(2028, 12, 31, 0), "valid"),
        ("2031-01-01T00:00:00Z", datetime(2028, 12, 30, 23, 59, 59), "invalid"),
        ("2030-06-01T00:00:00Z", datetime(2030, 6, 1, 0), "grace"),
        ("2030-06-01T00:00:00Z", datetime(2030, 6, 14, 23, 59, 59), "grace"),
        ("2030-06-01T00:00:00Z", datetime(2030, 6, 15, 0), "expired"),
        ("2030-06-01T00:00:00Z", datetime(2031, 1, 1), "expired"),
    ],
)
def test_time_states(tmp_path: Path, expires_at: str, at: datetime, state: str):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    doc = _doc(expires_at=expires_at)
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), at.replace(tzinfo=UTC))
    assert status.state == state
    if state == "invalid":
        assert status.reason == "not_yet_valid"
        assert status.license_id is None
        assert "license_invalid" in status.warnings
    if state == "grace":
        assert status.processing_allowed is True
        assert status.license_id == "L-2030-001"
        assert "license_in_grace" in status.warnings
    if state == "expired":
        assert status.processing_allowed is False
        assert status.license_id == "L-2030-001"
        assert "license_expired" in status.warnings


def test_days_left_and_expires_soon(tmp_path: Path):
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)

    doc = _doc(expires_at="2030-06-27T12:00:00Z")
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "valid"
    assert status.days_left == 12
    assert status.warnings == ("license_expires_soon",)

    doc = _doc(expires_at="2030-07-15T12:00:00Z")
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.days_left == 30
    assert status.warnings == ("license_expires_soon",)

    doc = _doc(expires_at="2030-07-16T12:00:00Z")
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.days_left == 31
    assert status.warnings == ()

    doc = _doc(expires_at="2030-06-05T00:00:00Z")
    license_file, _ = _write_licence(tmp_path, doc, key)
    status = evaluate(_settings_for(license_file, trust), NOW)
    assert status.state == "grace"
    assert status.grace_until == datetime(2030, 6, 19, tzinfo=UTC)
    assert status.days_left == 3
    status = evaluate(_settings_for(license_file, trust), datetime(2030, 6, 18, 23, 0, tzinfo=UTC))
    assert status.days_left == 0


def test_expired_days_left_is_zero_and_fields_stay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings, _ = _enforce(monkeypatch, tmp_path, _doc(expires_at="2030-06-01T00:00:00Z"))
    status = evaluate(settings, NOW)
    assert status.state == "expired"
    assert status.days_left == 0
    assert status.license_id == "L-2030-001"
    assert status.customer_name == "Acme Co"
    assert status.expires_at == datetime(2030, 6, 1, tzinfo=UTC)
    assert status.grace_until == datetime(2030, 6, 15, tzinfo=UTC)
    assert status.key_fingerprint is not None


def test_cache_follows_file_changes_and_state_follows_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings, key = _enforce(monkeypatch, tmp_path, _doc())
    assert evaluate(settings, NOW).state == "valid"

    folder = Path(settings.offline_license_file).parent
    _write_licence(folder, _doc(expires_at="2030-06-01T00:00:00Z"), key)
    assert evaluate(settings, NOW).state == "expired"
    assert evaluate(settings, datetime(2030, 6, 2, tzinfo=UTC)).state == "grace"


def test_no_network_is_used_by_evaluation_and_the_status_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _enforce(monkeypatch, tmp_path, _now_doc())
    reset_settings_cache()
    settings = Settings(_env_file=None)
    assert settings.offline_license_file
    _block_network(monkeypatch)
    assert current_license_status(settings).state == "valid"
    clear_license_cache()
    assert licensing_main(["status-line"]) == 0
    assert capsys.readouterr().out.strip().startswith("license: state=valid")


# --- 3. the processing gate and licence caps (SPEC 2.5) ---------------------------------


def _workspace_and_meeting(client, email_outbox, *, title="Licenced call"):
    credentials = register_user(client, email_outbox, email="licence-owner@example.com")
    login_user(client, credentials)
    with inspection_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        workspace = db.scalar(select(Workspace))
        meeting = Meeting(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            title=title,
            language="fa",
            duration_seconds=100,
        )
        db.add(meeting)
        db.commit()
        return workspace.id, meeting.id, user.id


def _owner_id() -> str:
    with inspection_session_factory()() as db:
        return db.scalar(select(User.id).where(User.email == "licence-owner@example.com"))


def test_restricted_reserve_refuses_new_keys_but_not_replays_or_settlements(
    client, email_outbox, monkeypatch, tmp_path: Path
):
    workspace_id, meeting_id, _ = _workspace_and_meeting(client, email_outbox)
    with inspection_session_factory()() as db:
        kept = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=100,
            idempotency_key="licence:kept",
        )
        other = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=100,
            idempotency_key="licence:other",
        )
        db.commit()
        kept_id, other_id = kept.id, other.id

    _enforce(monkeypatch, tmp_path, _expired_doc())

    with inspection_session_factory()() as db:
        replayed = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=100,
            idempotency_key="licence:kept",
        )
        assert replayed.id == kept_id

        with pytest.raises(LicenseRestrictedError) as refused:
            reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                amount=10,
                idempotency_key="licence:new",
            )
        assert refused.value.state == "expired"
        assert not isinstance(refused.value, QuotaExceededError)
        db.rollback()

        settled = commit_usage(db, kept_id, actual_amount=90)
        assert settled.state.value == "committed"
        db.commit()

        released = release_usage(db, other_id, reason="licence test")
        assert released.state.value == "released"
        db.commit()

    with inspection_session_factory()() as db:
        with pytest.raises(LicenseRestrictedError):
            reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                amount=10,
                idempotency_key="licence:new2",
            )
        db.rollback()


def test_licence_cap_refuses_over_the_cap_with_the_cap_as_limit(
    client, email_outbox, monkeypatch, tmp_path: Path
):
    workspace_id, meeting_id, _ = _workspace_and_meeting(client, email_outbox)
    _enforce(monkeypatch, tmp_path, _now_doc(limits={"audio_seconds": 1200}))

    with inspection_session_factory()() as db:
        at_cap = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=1200,
            idempotency_key="cap:exact",
        )
        assert at_cap.reserved_value == 1200
        db.commit()

    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=_owner_id(),
            title="Second",
            language="fa",
            duration_seconds=10,
        )
        db.add(meeting)
        db.commit()
        other_meeting = meeting.id

    with inspection_session_factory()() as db:
        with pytest.raises(QuotaExceededError) as refused:
            reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=other_meeting,
                amount=1,
                idempotency_key="cap:over",
            )
        assert refused.value.limit == 1200
        assert not isinstance(refused.value, LicenseRestrictedError)
        db.rollback()


def test_licence_cap_below_the_row_limit_wins_and_null_adds_no_condition(
    client, email_outbox, monkeypatch, tmp_path: Path
):
    workspace_id, meeting_id, _ = _workspace_and_meeting(client, email_outbox)
    _enforce(monkeypatch, tmp_path, _now_doc(limits={"audio_seconds": None}))

    with inspection_session_factory()() as db:
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "audio_seconds",
            )
        )
        assert entitlement.limit_value == 1800
        with pytest.raises(QuotaExceededError) as refused:
            reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                amount=1801,
                idempotency_key="row:over",
            )
        assert refused.value.limit == 1800
        db.rollback()

    _enforce(monkeypatch, tmp_path, _now_doc(limits={"audio_seconds": 500}))
    with inspection_session_factory()() as db:
        with pytest.raises(QuotaExceededError) as refused:
            reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                amount=501,
                idempotency_key="cap:over",
            )
        assert refused.value.limit == 500
        db.rollback()
        ok = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=500,
            idempotency_key="cap:exact",
        )
        assert ok.reserved_value == 500
        db.commit()


def test_licence_cap_applies_in_grace(client, email_outbox, monkeypatch, tmp_path: Path):
    workspace_id, meeting_id, _ = _workspace_and_meeting(client, email_outbox)
    now = datetime.now(UTC)
    _enforce(
        monkeypatch,
        tmp_path,
        _doc(
            issued_at=_z(now - timedelta(days=30)),
            expires_at=_z(now - timedelta(hours=1)),
            limits={"audio_seconds": 100},
        ),
    )
    with inspection_session_factory()() as db:
        ok = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=100,
            idempotency_key="grace:exact",
        )
        assert ok.reserved_value == 100
        db.commit()
    with inspection_session_factory()() as db:
        with pytest.raises(QuotaExceededError):
            reserve_usage(
                db,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                amount=1,
                idempotency_key="grace:over",
            )
        db.rollback()


def test_not_enforced_reserve_is_unchanged(client, email_outbox):
    workspace_id, meeting_id, _ = _workspace_and_meeting(client, email_outbox)
    with inspection_session_factory()() as db:
        ok = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=100,
            idempotency_key="plain:1",
        )
        assert ok.reserved_value == 100
        db.commit()


# --- 4. the HTTP surface: uploads, modules, header (SPEC 2.5, 2.6, 2.8) ------------------


@contextmanager
def _licensed_client(settings, email_outbox, monkeypatch, **licence: Any):
    from backend.routers.v1 import auth as auth_router

    async def capture_verification(user, token, _settings):
        email_outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    async def capture_reset(user, token, _settings):
        email_outbox.append({"kind": "reset", "user_id": user.id, "token": token})

    monkeypatch.setattr(auth_router, "_send_verification_message", capture_verification)
    monkeypatch.setattr(auth_router, "_send_reset_message", capture_reset)
    RateLimiter.reset_memory()
    app = create_app(settings.model_copy(update=licence))
    with TestClient(app) as test_client:
        yield test_client
    reset_database_state()


def _valid_metadata():
    from backend.media import AudioStreamMetadata, MediaMetadata

    return MediaMetadata(
        original_filename="customer-call.wav",
        extension=".wav",
        container="wav",
        detected_format_names=("wav",),
        mime_type="audio/wav",
        duration_seconds=300.25,
        size_bytes=16,
        audio_streams=(
            AudioStreamMetadata(index=0, codec="pcm_s16le", channels=1, sample_rate_hz=16_000),
        ),
        video_streams=(),
    )


def _upload(client, workspace_id: str, key: str):
    return client.post(
        "/api/v1/uploads",
        headers={
            "X-Workspace-ID": workspace_id,
            "Idempotency-Key": key,
            "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
        },
        data={"title": "Weekly customer call", "language": "fa"},
        files={"file": ("customer-call.wav", b"staged wav bytes", "audio/wav")},
    )


def _upload_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(),
    )
    monkeypatch.setattr(
        "backend.routers.v1.meetings.dispatch_job",
        lambda job_id, _settings: True,
    )


def _own_workspace(client, email_outbox) -> tuple[str, str]:
    credentials = register_user(client, email_outbox)
    session = login_user(client, credentials)
    return session["workspaces"][0]["id"], session["user"]["id"]


def test_upload_under_a_valid_licence_is_202_without_the_header(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    licence_settings, _ = _enforce(monkeypatch, tmp_path, _now_doc())
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        _upload_patches(monkeypatch)
        accepted = _upload(client, workspace_id, "upload-valid-1")
        assert accepted.status_code == 202, accepted.text
        assert "x-homayar-license" not in accepted.headers


@pytest.mark.parametrize("licence_case", ["expired", "tampered", "invalid_trust"], ids=str)
def test_upload_refusals_come_from_the_licence_decision(
    settings, email_outbox, monkeypatch, tmp_path: Path, licence_case: str
):
    key = _key()
    trust = _trust_dir(tmp_path)
    if licence_case == "invalid_trust":
        (trust / "oops.pem").write_bytes(b"garbage")
    _trust_key(trust, key)
    if licence_case == "tampered":
        license_file, _ = _write_licence(tmp_path, _now_doc(), key)
        payload = bytearray(license_file.read_bytes())
        payload[7] ^= 0x20
        license_file.write_bytes(bytes(payload))
        expected_state, expected_reason = "invalid", "bad_signature"
    elif licence_case == "expired":
        license_file, _ = _write_licence(tmp_path, _expired_doc(), key)
        expected_state, expected_reason = "expired", None
    else:
        license_file, _ = _write_licence(tmp_path, _now_doc(), key)
        expected_state, expected_reason = "invalid", "bad_trust_key"
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(license_file))
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(trust))

    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=str(license_file),
        offline_license_trust_dir=str(trust),
    ) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        _upload_patches(monkeypatch)
        refused = _upload(client, workspace_id, "upload-refused-1")
        assert refused.status_code == 403, refused.text
        error = refused.json()["error"]
        assert error["code"] == "license_restricted"
        assert error["message"] == "The offline licence does not allow new processing"
        assert error["details"]["state"] == expected_state
        assert error["details"].get("reason") == expected_reason
        assert refused.headers.get("x-homayar-license") == expected_state

        with inspection_session_factory()() as db:
            assert db.scalar(select(func.count(Meeting.id))) == 0
            assert db.scalar(select(func.count(Recording.id))) == 0
            assert db.scalar(select(func.count(ProcessingJob.id))) == 0
            assert db.scalar(select(func.count(UsageReservation.id))) == 0


def test_grace_allows_processing_and_sets_the_header(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    now = datetime.now(UTC)
    licence_settings, _ = _enforce(
        monkeypatch,
        tmp_path,
        _doc(
            issued_at=_z(now - timedelta(days=30)),
            expires_at=_z(now - timedelta(hours=1)),
        ),
    )
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        _upload_patches(monkeypatch)
        accepted = _upload(client, workspace_id, "upload-grace-1")
        assert accepted.status_code == 202, accepted.text
        assert accepted.headers.get("x-homayar-license") == "grace"
        listed = client.get("/api/v1/meetings", headers={"X-Workspace-ID": workspace_id})
        assert listed.status_code == 200
        assert listed.headers.get("x-homayar-license") == "grace"


def test_restricted_keeps_reads_and_exports_and_refuses_module_writes(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    licence_settings, _ = _enforce(
        monkeypatch,
        tmp_path,
        _expired_doc(modules=["quality", "voicemail"]),
    )
    from backend.tests.test_authorization_matrix import _insert_meeting

    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id, user_id = _own_workspace(client, email_outbox)
        meeting_id = _insert_meeting(workspace_id, created_by=user_id, title="Old call")
        headers = {"X-Workspace-ID": workspace_id}

        read = client.get(f"/api/v1/meetings/{meeting_id}", headers=headers)
        assert read.status_code == 200, read.text
        export = client.get(
            f"/api/v1/meetings/{meeting_id}/report/export",
            headers=headers,
            params={"format": "json"},
        )
        assert export.status_code == 200, export.text
        listed = client.get("/api/v1/voicemails", headers=headers)
        assert listed.status_code == 200, listed.text

        write = client.post(
            "/api/v1/voicemails",
            headers={**headers, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
            data={"external_message_id": "vm-licence-1"},
            files={"file": ("vm.wav", b"staged bytes", "audio/wav")},
        )
        assert write.status_code == 403, write.text
        error = write.json()["error"]
        assert error["code"] == "license_restricted"
        assert error["details"]["state"] == "expired"
        assert error["details"]["module"] == "voicemail"


def test_valid_licence_modules_intersect_the_plan_and_report_not_licensed(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    licence_settings, _ = _enforce(monkeypatch, tmp_path, _now_doc(modules=["quality"]))
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        headers = {"X-Workspace-ID": workspace_id}

        refused = client.get("/api/v1/voicemails", headers=headers)
        assert refused.status_code == 403, refused.text
        error = refused.json()["error"]
        assert error["code"] == "module_disabled"
        assert error["details"] == {"module": "voicemail", "reason": "not_licensed"}

        allowed = client.get("/api/v1/scorecards", headers=headers)
        assert allowed.status_code == 200, allowed.text

        capabilities = client.get("/api/v1/capabilities", headers=headers).json()
        by_key = {module["key"]: module for module in capabilities["modules"]}
        assert by_key["quality"]["licensed"] is True
        assert by_key["voicemail"]["licensed"] is False
        assert by_key["voicemail"]["reason"] == "not_licensed"


def test_restricted_capabilities_report_the_plan_as_if_not_enforced(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    licence_settings, _ = _enforce(monkeypatch, tmp_path, _expired_doc(modules=["quality"]))
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        headers = {"X-Workspace-ID": workspace_id}

        capabilities = client.get("/api/v1/capabilities", headers=headers).json()
        assert all(module["licensed"] is True for module in capabilities["modules"])
        listed = client.get("/api/v1/voicemails", headers=headers)
        assert listed.status_code == 200, listed.text


def test_not_enforced_http_behaviour_is_unchanged(settings, email_outbox, monkeypatch):
    with _licensed_client(settings, email_outbox, monkeypatch) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        headers = {"X-Workspace-ID": workspace_id}
        listed = client.get("/api/v1/voicemails", headers=headers)
        assert listed.status_code == 200, listed.text
        assert "x-homayar-license" not in listed.headers
        capabilities = client.get("/api/v1/capabilities", headers=headers).json()
        assert all(module["licensed"] is True for module in capabilities["modules"])


def test_the_header_middleware_survives_an_evaluation_error(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    from backend.licensing import offline_license as licensing_module

    licence_settings, _ = _enforce(monkeypatch, tmp_path, _now_doc())
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:

        def broken(_settings, _now=None):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(licensing_module, "current_license_status", broken)
        answer = client.get("/healthz")
        assert answer.status_code == 200, answer.text
        assert answer.headers.get("x-homayar-license") == "invalid"


def test_the_state_is_logged_once_and_on_change_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    settings, _ = _enforce(monkeypatch, tmp_path, _doc())
    with caplog.at_level(logging.WARNING, logger="homayar"):
        evaluate(settings, NOW)
        current_license_status(settings, NOW)
        current_license_status(settings, NOW)
    lines = [
        record.message for record in caplog.records if "OFFLINE_LICENSE_STATE" in record.message
    ]
    assert len(lines) == 1
    assert "state=valid" in lines[0]
    assert "license_id=L-2030-001" in lines[0]
    assert "Acme" not in lines[0]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="homayar"):
        current_license_status(settings, NOW)
    assert "OFFLINE_LICENSE_STATE" not in caplog.text

    with caplog.at_level(logging.WARNING, logger="homayar"):
        current_license_status(settings, datetime(2031, 1, 2, tzinfo=UTC))
    changed = [
        record.message for record in caplog.records if "OFFLINE_LICENSE_STATE" in record.message
    ]
    assert len(changed) == 1
    assert "state=grace" in changed[0]


def test_the_error_is_reexported_next_to_the_quota_error():
    from backend.services import entitlement_service

    assert entitlement_service.LicenseRestrictedError is LicenseRestrictedError
    assert not issubclass(LicenseRestrictedError, QuotaExceededError)
    error = LicenseRestrictedError("expired", None)
    assert (error.state, error.reason) == ("expired", None)


def test_replay_under_a_restricted_licence_answers_403_license_restricted(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    from backend.tests.test_admin_api import _session_cookies
    from backend.tests.test_admin_jobs_api import _seed_job
    from backend.tests.test_job_failure_policy import _replay

    licence_settings, _ = _enforce(monkeypatch, tmp_path, _expired_doc())
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id, _ = _own_workspace(client, email_outbox)
        job_id = _seed_job(workspace_id, state=JobState.DEAD_LETTER)
        cookies = _session_cookies(client)
        refused = _replay(client, workspace_id, job_id, cookies=cookies)
        assert refused.status_code == 403, refused.text
        error = refused.json()["error"]
        assert error["code"] == "license_restricted"
        assert error["details"]["state"] == "expired"


# --- 5. the operator endpoint (SPEC 2.7) ------------------------------------------------


LICENSE_API = "/api/v1/admin/license"
OPERATOR_EMAIL = "licence-operator@example.com"


def _make_operator(operator: bool = True) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(User)
            .where(User.email == OPERATOR_EMAIL)
            .values(system_role="operator" if operator else "user")
        )
        db.commit()


def _operator(client, email_outbox, *, operator: bool = True) -> str:
    register_user(client, email_outbox, email=OPERATOR_EMAIL)
    session = login_user(client, {"email": OPERATOR_EMAIL, "password": "SecurePass123"})
    _make_operator(operator)
    return session["workspaces"][0]["id"]


def _get_license(client, workspace_id: str):
    return client.get(LICENSE_API, headers={"X-Workspace-ID": workspace_id})


def test_endpoint_roles_session_and_api_key(settings, email_outbox, monkeypatch, tmp_path: Path):
    licence_settings, _ = _enforce(monkeypatch, tmp_path, _now_doc())
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id = _operator(client, email_outbox, operator=False)
        denied = _get_license(client, workspace_id)
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == "platform_operator_required"

        _make_operator(True)
        allowed = _get_license(client, workspace_id)
        assert allowed.status_code == 200, allowed.text
        assert allowed.headers["cache-control"] == "no-store"

        minted = client.post(
            "/api/v1/api-keys",
            json={"name": "licence-key", "scopes": ["meeting:read"], "expires_in_days": 30},
            headers={
                "X-Workspace-ID": workspace_id,
                "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
            },
        )
        assert minted.status_code == 201, minted.text
        cookies = dict(client.cookies)
        client.cookies.clear()
        keyed = client.get(
            LICENSE_API,
            headers={
                "Authorization": f"Bearer {minted.json()['key']}",
                "X-Workspace-ID": workspace_id,
            },
        )
        assert keyed.status_code == 403, keyed.text
        client.cookies.update(cookies)
        assert _get_license(client, workspace_id).status_code == 200

        client.cookies.clear()
        anonymous = _get_license(client, workspace_id)
        assert anonymous.status_code == 401, anonymous.text


def test_endpoint_body_for_a_valid_licence(settings, email_outbox, monkeypatch, tmp_path: Path):
    now = datetime.now(UTC)
    doc = _now_doc(expires_at=_z(now + timedelta(days=12, hours=1)))
    licence_settings, key = _enforce(monkeypatch, tmp_path, doc)
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id = _operator(client, email_outbox)
        response = _get_license(client, workspace_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "enforced",
            "state",
            "reason",
            "processing_allowed",
            "license_id",
            "customer_id",
            "customer_name",
            "issued_at",
            "expires_at",
            "grace_until",
            "days_left",
            "modules",
            "unknown_modules",
            "limits",
            "unknown_limits",
            "key_fingerprint",
            "warnings",
            "counters",
        }
        assert body["enforced"] is True
        assert body["state"] == "valid"
        assert body["reason"] is None
        assert body["processing_allowed"] is True
        assert body["license_id"] == "L-2030-001"
        assert body["customer_id"] == "acme"
        assert body["customer_name"] == "Acme Co"
        assert body["issued_at"] == doc["issued_at"]
        assert body["expires_at"] == doc["expires_at"]
        assert body["grace_until"] == _z(
            datetime.strptime(doc["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            + timedelta(days=14)
        )
        assert body["days_left"] == 12
        assert body["modules"] == ["insights", "quality"]
        assert body["unknown_modules"] == []
        assert body["limits"] == {"audio_seconds": 360000, "concurrent_jobs": 4}
        assert body["unknown_limits"] == []
        assert body["key_fingerprint"] == _fingerprint(key)
        assert body["warnings"] == ["license_expires_soon"]
        metrics = [counter["metric"] for counter in body["counters"]]
        assert metrics == sorted(metrics)
        counters = {counter["metric"]: counter for counter in body["counters"]}
        assert counters["audio_seconds"]["limit"] == 1800
        assert counters["audio_seconds"]["license_limit"] == 360000
        assert counters["audio_seconds"]["used"] == 0
        assert counters["audio_seconds"]["reserved"] == 0
        assert counters["audio_seconds"]["remaining"] == 1800
        assert counters["concurrent_jobs"]["license_limit"] == 4


@pytest.mark.parametrize("licence_case", ["expired", "invalid", "missing", "not_enforced"], ids=str)
def test_endpoint_body_per_state(
    settings, email_outbox, monkeypatch, tmp_path: Path, licence_case: str
):
    doc: dict = {}
    if licence_case == "not_enforced":
        licence = {}
    elif licence_case == "missing":
        (tmp_path / "keys").mkdir()
        licence = {
            "offline_license_file": str(tmp_path / "absent.json"),
            "offline_license_trust_dir": str(tmp_path / "keys"),
        }
    else:
        if licence_case == "expired":
            doc = _expired_doc()
        else:
            doc = _now_doc()
        licence_settings, _ = _enforce(monkeypatch, tmp_path, doc)
        licence = {
            "offline_license_file": licence_settings.offline_license_file,
            "offline_license_trust_dir": licence_settings.offline_license_trust_dir,
        }
        if licence_case == "invalid":
            Path(licence["offline_license_trust_dir"], "vendor.pem").unlink()

    with _licensed_client(settings, email_outbox, monkeypatch, **licence) as client:
        workspace_id = _operator(client, email_outbox)
        response = _get_license(client, workspace_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == licence_case
        assert body["enforced"] == (licence_case != "not_enforced")
        if licence_case in ("missing", "invalid", "not_enforced"):
            assert body["license_id"] is None
            assert body["customer_name"] is None
            assert body["modules"] == []
            assert body["limits"] == {}
            assert body["key_fingerprint"] is None
            assert body["days_left"] is None
            if licence_case != "not_enforced":
                assert body["processing_allowed"] is False
        else:
            assert body["license_id"] == "L-2030-001"
            assert body["days_left"] == 0
            assert body["warnings"] == ["license_expired"]
            assert body["processing_allowed"] is False
            assert body["expires_at"] == doc["expires_at"]
        counters = body["counters"]
        assert [counter["metric"] for counter in counters] == sorted(
            counter["metric"] for counter in counters
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_at": "9999-12-31T23:59:59Z", "grace_days": 14},
        {"issued_at": "0001-01-01T00:00:00Z"},
    ],
    ids=["max-expiry-with-grace", "min-issued"],
)
def test_overflowing_dates_keep_reads_and_refuse_processing(
    settings, email_outbox, monkeypatch, tmp_path: Path, overrides: dict
):
    licence_settings, _ = _enforce(monkeypatch, tmp_path, _now_doc(**overrides))
    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id = _operator(client, email_outbox)
        body = _get_license(client, workspace_id).json()
        assert (body["state"], body["reason"]) == ("invalid", "malformed")
        assert body["license_id"] is None

        headers = {"X-Workspace-ID": workspace_id}
        listed = client.get("/api/v1/voicemails", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.headers.get("x-homayar-license") == "invalid"

        _upload_patches(monkeypatch)
        refused = _upload(client, workspace_id, "upload-overflow-1")
        assert refused.status_code == 403, refused.text
        error = refused.json()["error"]
        assert error["code"] == "license_restricted"
        assert error["details"] == {"state": "invalid", "reason": "malformed"}


def test_an_unexpected_evaluation_error_keeps_module_reads(
    settings, email_outbox, monkeypatch, tmp_path: Path
):
    from backend.licensing import offline_license as licensing_module

    licence_settings, _ = _enforce(monkeypatch, tmp_path, _now_doc())

    def broken(_settings_like):
        raise RuntimeError("unexpected")

    with _licensed_client(
        settings,
        email_outbox,
        monkeypatch,
        offline_license_file=licence_settings.offline_license_file,
        offline_license_trust_dir=licence_settings.offline_license_trust_dir,
    ) as client:
        workspace_id = _operator(client, email_outbox)
        monkeypatch.setattr(licensing_module, "_verified_license", broken)
        headers = {"X-Workspace-ID": workspace_id}

        listed = client.get("/api/v1/voicemails", headers=headers)
        assert listed.status_code == 200, listed.text
        capabilities = client.get("/api/v1/capabilities", headers=headers)
        assert capabilities.status_code == 200, capabilities.text
        body = _get_license(client, workspace_id)
        assert body.status_code == 200, body.text
        assert (body.json()["state"], body.json()["reason"]) == ("invalid", "malformed")

        write = client.post(
            "/api/v1/voicemails",
            headers={**headers, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
            data={"external_message_id": "vm-unexpected-1"},
            files={"file": ("vm.wav", b"staged bytes", "audio/wav")},
        )
        assert write.status_code == 403, write.text
        assert write.json()["error"]["code"] == "license_restricted"
        _upload_patches(monkeypatch)
        refused = _upload(client, workspace_id, "upload-unexpected-1")
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["details"]["reason"] == "malformed"


# --- 6. the status line (SPEC 2.9) -------------------------------------------------------


def test_status_line_valid_licence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    now = datetime.now(UTC)
    expires = _z(now + timedelta(days=12, hours=1))
    doc = _doc(
        license_id="L-2027-001",
        customer_id="acme",
        issued_at="2020-01-01T00:00:00Z",
        expires_at=expires,
        grace_days=14,
        modules=["insights", "quality", "telepathy"],
        limits={"audio_seconds": 360000, "concurrent_jobs": None, "warp": 3},
    )
    key = _key()
    trust = _trust_dir(tmp_path)
    _trust_key(trust, key)
    license_file, _ = _write_licence(tmp_path, doc, key)
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(license_file))
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(trust))
    reset_settings_cache()

    assert licensing_main(["status-line"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    grace_until = _z(
        datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) + timedelta(days=14)
    )
    assert out.strip() == (
        "license: state=valid reason=- id=L-2027-001 customer=acme "
        f"expires={expires} grace_until={grace_until} days_left=12 "
        "modules=insights,quality limits=audio_seconds=360000,"
        f"concurrent_jobs=unlimited key={_fingerprint(key)}"
    )
    assert "Acme" not in out


def test_status_line_states_without_a_verified_licence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    assert licensing_main(["status-line"]) == 0
    assert capsys.readouterr().out.strip() == "license: state=not_enforced"

    trust = _trust_dir(tmp_path)
    key = _key()
    _trust_key(trust, key)
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(tmp_path / "license.json"))
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(trust))
    reset_settings_cache()
    assert licensing_main(["status-line"]) == 0
    assert capsys.readouterr().out.strip() == (
        "license: state=missing reason=file_missing id=- customer=- expires=- "
        "grace_until=- days_left=- modules=- limits=- key=-"
    )

    _write_licence(tmp_path, _now_doc(), key)
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(tmp_path / "absent"))
    reset_settings_cache()
    assert licensing_main(["status-line"]) == 0
    assert capsys.readouterr().out.strip() == (
        "license: state=invalid reason=no_trusted_keys id=- customer=- "
        "expires=- grace_until=- days_left=- modules=- limits=- key=-"
    )


def test_status_line_exits_1_without_stdout_when_the_settings_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture
):
    _enforce(monkeypatch, tmp_path, _now_doc())
    monkeypatch.setenv("DISABLED_MODULES", "telepathy")
    reset_settings_cache()
    capfd.readouterr()
    assert licensing_main(["status-line"]) == 1
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "ValidationError" in captured.err
    assert "state=" not in captured.err


def test_status_line_reports_an_unexpected_evaluation_error_as_invalid_on_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture
):
    from backend.licensing import offline_license as licensing_module

    _enforce(monkeypatch, tmp_path, _now_doc())

    def broken(_settings_like):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(licensing_module, "_verified_license", broken)
    capfd.readouterr()
    assert licensing_main(["status-line"]) == 0
    out = capfd.readouterr().out
    assert out.count("\n") == 1
    assert out.startswith("license: state=invalid reason=malformed id=-")


# --- 7. settings, compose and env example (SPEC 2.1, 2.10) -------------------------------


def test_settings_read_the_two_environment_names(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", "/etc/licence.json")
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", "/etc/keys")
    settings = Settings(_env_file=None)
    assert settings.offline_license_file == "/etc/licence.json"
    assert settings.offline_license_trust_dir == "/etc/keys"


def test_compose_onprem_carries_the_licence_env_and_bind_only_on_api_and_worker():
    compose = yaml.safe_load((REPO_ROOT / "infra/compose/compose.onprem.yaml").read_text())
    for name in ("api", "worker"):
        environment = compose["services"][name]["environment"]
        assert environment["OFFLINE_LICENSE_FILE"] == "${OFFLINE_LICENSE_FILE:-}"
        assert (
            environment["OFFLINE_LICENSE_TRUST_DIR"]
            == "${OFFLINE_LICENSE_TRUST_DIR:-/app/license/keys}"
        )
        binds = [
            volume
            for volume in compose["services"][name]["volumes"]
            if isinstance(volume, str) and volume.endswith(":/app/license:ro")
        ]
        assert len(binds) == 1
        assert binds[0].startswith("${OFFLINE_LICENSE_DIR:-")
    for name in ("scheduler", "migrate", "backup-tool", "tombstone-reapply"):
        environment = compose["services"][name].get("environment") or {}
        assert "OFFLINE_LICENSE_FILE" not in environment
    assert (REPO_ROOT / "infra/compose/license/README.md").exists()


def test_env_example_lists_the_two_settings():
    text = (REPO_ROOT / "Homayar/.env.example").read_text()
    assert "OFFLINE_LICENSE_FILE=" in text
    assert "OFFLINE_LICENSE_TRUST_DIR=" in text
