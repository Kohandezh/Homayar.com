"""HOM-109 oracle: the signed offline licence, written from SPEC.md before the fix."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import runpy
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from fastapi.routing import APIRoute
from freezegun import freeze_time
from sqlalchemy import func, select, update

from backend.config import reset_settings_cache
from backend.database.saas_models import (
    AuditEvent,
    Entitlement,
    JobState,
    Meeting,
    Membership,
    ProcessingJob,
    Recording,
    ScorecardVersion,
    UsageReservation,
    User,
    WorkspaceRole,
)
from backend.jobs import outbox
from backend.security.authorization import API_KEY_SCOPES
from backend.security.context import CSRF_COOKIE
from backend.services.entitlement_service import (
    QuotaExceededError,
    commit_usage,
    release_usage,
    reserve_usage,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, pin_negotiated_plan, register_user
from backend.tests.test_api_keys import _bearer
from backend.tests.test_authorization_matrix import _route_policy
from backend.tests.test_capabilities import _add_membership, _caller, _scorecard_body
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_job_failure_policy import _dead_letter_after_one_attempt
from backend.tests.test_report_export import _bundle_processed
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

API = "/api/v1/admin/license"
CAPABILITIES = "/api/v1/capabilities"
SCORECARDS = "/api/v1/scorecards"
HEADER = "X-Homayar-License"
SCHEMA = "homayar-offline-license/1"
MESSAGE = "The offline licence does not allow new processing"
LICENSE_ID = "L-2027-001"
CUSTOMER_ID = "acme"
CUSTOMER_NAME = "شرکت آزمون Acme"
ALL_MODULES = ["quality", "insights", "copilot", "voice", "voicemail", "integrations"]
HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = HOMAYAR_ROOT.parent
UNREACHABLE_DATABASE = "postgresql+psycopg://nobody:nothing@127.0.0.1:9/none"
UNREACHABLE_REDIS = "redis://127.0.0.1:9/0"
ENDPOINT_FIELDS = {
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
LICENCE_SCALARS = (
    "license_id",
    "customer_id",
    "customer_name",
    "issued_at",
    "expires_at",
    "grace_until",
    "days_left",
    "key_fingerprint",
)
COUNTER_FIELDS = ("metric", "limit", "license_limit", "used", "reserved", "remaining")
STATUS_KEYS = [
    "state",
    "reason",
    "id",
    "customer",
    "expires",
    "grace_until",
    "days_left",
    "modules",
    "limits",
    "key",
]
WINDOWS = {
    "valid": (-10, 100),
    "grace": (-200, -2),
    "expired": (-200, -30),
    "not_yet_valid": (5, 200),
}
ANY = object()


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _public(key):
    return key.public_key() if hasattr(key, "public_key") else key


def _public_pem(key) -> bytes:
    return _public(key).public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _fingerprint(key) -> str:
    der = _public(key).public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()[:16]


def _encode(body) -> bytes:
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _body(*, issued: datetime, expires: datetime, grace_days: int = 14, **changes) -> dict:
    body = {
        "schema": SCHEMA,
        "license_id": LICENSE_ID,
        "customer_id": CUSTOMER_ID,
        "customer_name": CUSTOMER_NAME,
        "issued_at": _ts(issued),
        "expires_at": _ts(expires),
        "grace_days": grace_days,
        "modules": list(ALL_MODULES),
        "limits": {"audio_seconds": None, "concurrent_jobs": None},
    }
    body.update(changes)
    return body


def _window(state: str, *, at: datetime | None = None, **changes) -> dict:
    now = at or _now()
    issued, expires = WINDOWS[state]
    return _body(
        issued=now + timedelta(days=issued), expires=now + timedelta(days=expires), **changes
    )


class Licence:
    def __init__(self, root: Path) -> None:
        self.dir = root / "license"
        self.keys = self.dir / "keys"
        self.file = self.dir / "license.json"
        self.sig = self.dir / "license.json.sig"
        self.keys.mkdir(parents=True)
        self.signer = ed25519.Ed25519PrivateKey.generate()
        self.trust(self.signer, "vendor.pem")

    _stamp = 0

    @classmethod
    def put(cls, path: Path, data: bytes) -> None:
        staged = path.with_name(path.name + ".staged")
        staged.write_bytes(data)
        cls._stamp = max(cls._stamp + 1_000_000, staged.stat().st_mtime_ns)
        os.utime(staged, ns=(cls._stamp, cls._stamp))
        os.replace(staged, path)

    def trust(self, key, name: str) -> Path:
        path = self.keys / name
        self.put(path, _public_pem(key))
        return path

    def write(
        self,
        body: dict | None = None,
        *,
        raw: bytes | None = None,
        key=None,
        signature: bytes | None = None,
    ) -> bytes:
        data = raw if raw is not None else _encode(body)
        self.put(self.file, data)
        self.put(self.sig, signature if signature is not None else (key or self.signer).sign(data))
        return data

    def tamper(self, data: bytes) -> None:
        changed = data.replace(b'"acme"', b'"acmf"', 1)
        assert changed != data and len(changed) == len(data)
        self.put(self.file, changed)

    def remove(self) -> None:
        self.file.unlink(missing_ok=True)
        self.sig.unlink(missing_ok=True)


@pytest.fixture(params=["unset", "empty"])
def unenforced(request) -> str:
    return request.param


@pytest.fixture
def blank_trust_dir() -> None:
    return None


@pytest.fixture(autouse=True)
def licence(request, tmp_path, monkeypatch) -> Iterator[Licence]:
    current = Licence(tmp_path)
    if "unenforced" in request.fixturenames:
        if request.getfixturevalue("unenforced") == "unset":
            monkeypatch.delenv("OFFLINE_LICENSE_FILE", raising=False)
            monkeypatch.delenv("OFFLINE_LICENSE_TRUST_DIR", raising=False)
        else:
            monkeypatch.setenv("OFFLINE_LICENSE_FILE", "")
            monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", "")
    else:
        monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(current.file))
        trust = "" if "blank_trust_dir" in request.fixturenames else str(current.keys)
        monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", trust)
    reset_settings_cache()
    yield current
    reset_settings_cache()


def _make_restricted(licence: Licence, state: str) -> None:
    if state == "expired":
        licence.write(_window("expired"))
    elif state == "invalid":
        licence.tamper(licence.write(_window("valid")))
    elif state == "missing":
        licence.remove()
    else:
        raise AssertionError(state)


def _licence_error_class():
    try:
        from backend.services.entitlement_service import LicenseRestrictedError
    except ImportError:
        from backend.licensing import LicenseRestrictedError
    return LicenseRestrictedError


def _system_role(email: str, role: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(User).where(User.email == email).values(system_role=role))
        db.commit()


def _membership(workspace_id: str, **values) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership).where(Membership.workspace_id == workspace_id).values(**values)
        )
        db.commit()


def _sign_in(client, email_outbox, email: str) -> tuple[dict, str]:
    credentials = register_user(client, email_outbox, email=email)
    session = login_user(client, credentials)
    return credentials, session["workspaces"][0]["id"]


def _owner(client, email_outbox, email: str = "licence-owner@example.com") -> str:
    return _sign_in(client, email_outbox, email)[1]


def _operator(
    client, email_outbox, *, email: str = "licence-operator@example.com", operator: bool = True
) -> str:
    workspace_id = _owner(client, email_outbox, email)
    if operator:
        _system_role(email, "operator")
    return workspace_id


@pytest.fixture
def operator(client, email_outbox) -> str:
    return _operator(client, email_outbox)


def _read(client, workspace_id: str, path: str, **params):
    return client.get(path, headers={"X-Workspace-ID": workspace_id}, params=params)


def _write_headers(client, workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}


def _status(client, workspace_id: str) -> dict:
    response = _read(client, workspace_id, API)
    assert response.status_code == 200, response.text
    body = response.json()
    assert ENDPOINT_FIELDS <= set(body), sorted(ENDPOINT_FIELDS - set(body))
    return body


def _state(client, workspace_id: str) -> tuple[str, str | None]:
    body = _status(client, workspace_id)
    return body["state"], body["reason"]


def _no_licence_fields(body: dict) -> None:
    for field in LICENCE_SCALARS:
        assert body[field] is None, (field, body[field])
    assert body["modules"] == []
    assert body["unknown_modules"] == []
    assert body["unknown_limits"] == []
    assert body["limits"] in (None, {})


def _media(monkeypatch, seconds: float = 300.0) -> None:
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=seconds),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)


def _upload(client, workspace_id: str, key: str, content: bytes):
    return client.post(
        "/api/v1/uploads",
        headers={**_write_headers(client, workspace_id), "Idempotency-Key": key},
        data={"title": "Licence call", "language": "fa"},
        files={"file": ("licence-call.wav", content, "audio/wav")},
    )


def _publish_scorecard(client, workspace_id: str, key: str):
    return client.post(
        f"{SCORECARDS}/{key}/versions",
        json=_scorecard_body(),
        headers=_write_headers(client, workspace_id),
    )


def _rows(workspace_id: str) -> dict:
    with inspection_session_factory()() as db:
        counts: dict = {
            model.__name__: db.scalar(
                select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
            )
            for model in (Meeting, Recording, ProcessingJob, UsageReservation)
        }
        for row in db.scalars(select(Entitlement).where(Entitlement.workspace_id == workspace_id)):
            counts[row.metric] = (row.used_value, row.reserved_value)
        return counts


def _limit(workspace_id: str, metric: str) -> int | None:
    with inspection_session_factory()() as db:
        row = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == metric
            )
        )
        assert row is not None, metric
        return row.limit_value


def _set_limit(workspace_id: str, metric: str, value: int | None) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Entitlement)
            .where(Entitlement.workspace_id == workspace_id, Entitlement.metric == metric)
            .values(limit_value=value)
        )
        db.commit()


def _error(response) -> dict:
    return response.json()["error"]


def _restricted(response, state: str, reason=ANY, module: str | None = None) -> None:
    assert response.status_code == 403, response.text
    error = _error(response)
    assert error["code"] == "license_restricted", response.text
    assert error["message"] == MESSAGE
    assert error["details"]["state"] == state, error
    if reason is not ANY:
        assert error["details"]["reason"] == reason, error
    if module is not None:
        assert error["details"]["module"] == module, error


def _module_refusal(response, module: str, reason: str) -> None:
    assert response.status_code == 403, response.text
    error = _error(response)
    assert error["code"] == "module_disabled", response.text
    assert error["details"]["module"] == module
    assert error["details"]["reason"] == reason


def _listed(client, workspace_id: str) -> dict[str, dict]:
    response = _read(client, workspace_id, CAPABILITIES)
    assert response.status_code == 200, response.text
    return {module["key"]: module for module in response.json()["modules"]}


def _workspace_with_meetings(client, email_outbox, email: str, count: int = 3):
    register_user(client, email_outbox, email=email)
    with inspection_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email))
        workspace_id = db.scalar(
            select(Membership.workspace_id).where(Membership.user_id == user.id)
        )
        meetings = [
            Meeting(
                workspace_id=workspace_id,
                created_by_user_id=user.id,
                title=f"licence meeting {index}",
                language="fa",
                duration_seconds=60,
            )
            for index in range(count)
        ]
        db.add_all(meetings)
        db.commit()
        return workspace_id, [meeting.id for meeting in meetings]


def _reserve(
    workspace_id: str, meeting_id: str, amount: int, key: str, metric: str = "audio_seconds"
) -> str:
    with inspection_session_factory()() as db:
        reservation = reserve_usage(
            db,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            amount=amount,
            idempotency_key=key,
            metric=metric,
        )
        db.commit()
        return reservation.id


@contextmanager
def _no_network() -> Iterator[list]:
    attempts: list = []

    def refuse(*args, **kwargs):
        attempts.append(args[1:] if args else kwargs)
        raise OSError("HOM-109 oracle: network is off")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", refuse)
        patch.setattr(socket.socket, "connect_ex", refuse)
        patch.setattr(socket, "create_connection", refuse)
        yield attempts


def _cli() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = UNREACHABLE_DATABASE
    env["REDIS_URL"] = UNREACHABLE_REDIS
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "backend.licensing", "status-line"],
        cwd=HOMAYAR_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _cli_in_process(monkeypatch, capsys) -> tuple[int, str]:
    reset_settings_cache()
    monkeypatch.setattr(sys, "argv", ["backend.licensing", "status-line"])
    code = 0
    try:
        runpy.run_module("backend.licensing", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = 0 if exc.code is None else exc.code
    return code, capsys.readouterr().out


def _parse_status_line(output: str) -> list[tuple[str, str]]:
    lines = output.splitlines()
    assert len(lines) == 1, output
    line = lines[0]
    assert line.startswith("license: "), line
    pairs = []
    for token in line[len("license: ") :].split(" "):
        key, sep, value = token.partition("=")
        assert sep == "=", token
        pairs.append((key, value))
    return pairs


# --- 2.1 the switch: not enforced is exactly as today ------------------------------------


def test_not_enforced_keeps_uploads_modules_and_headers_as_today(
    client, email_outbox, monkeypatch, unenforced
):
    workspace_id = _owner(client, email_outbox, "free-run@example.com")
    _media(monkeypatch)
    accepted = _upload(client, workspace_id, "licence-free-0001", b"free run wav one")
    assert accepted.status_code == 202, accepted.text
    assert HEADER not in accepted.headers
    listed = _read(client, workspace_id, CAPABILITIES)
    assert listed.status_code == 200, listed.text
    assert HEADER not in listed.headers
    assert all(m["licensed"] and m["enabled"] for m in listed.json()["modules"])
    assert _read(client, workspace_id, SCORECARDS).status_code == 200
    published = _publish_scorecard(client, workspace_id, "lic_free")
    assert published.status_code < 400, published.text


def test_not_enforced_reservations_are_bounded_only_by_the_row(client, email_outbox, unenforced):
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "free-row@example.com")
    row_limit = _limit(workspace_id, "audio_seconds")
    assert row_limit is not None and row_limit > 1
    _reserve(workspace_id, meetings[0], row_limit, "free-row-all")
    with pytest.raises(QuotaExceededError) as refused:
        _reserve(workspace_id, meetings[1], 1, "free-row-one-more")
    assert refused.value.limit == row_limit


def test_not_enforced_operator_endpoint_reports_not_enforced(client, email_outbox, unenforced):
    workspace_id = _operator(client, email_outbox)
    body = _status(client, workspace_id)
    assert body["enforced"] is False
    assert body["state"] == "not_enforced"
    assert body["reason"] is None
    assert body["processing_allowed"] is True
    _no_licence_fields(body)
    assert body["warnings"] == []


def test_not_enforced_status_line(unenforced):
    result = _cli()
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["license: state=not_enforced"], result.stdout


# --- 2.3 verification --------------------------------------------------------------------


def test_a_signed_licence_inside_its_window_is_valid(client, operator, licence):
    data = licence.write(_window("valid", limits={"audio_seconds": 360000}))
    signature = licence.sig.read_bytes()
    response = _read(client, operator, API)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert HEADER not in response.headers
    body = response.json()
    assert ENDPOINT_FIELDS <= set(body)
    assert (body["enforced"], body["state"], body["reason"]) == (True, "valid", None)
    assert body["processing_allowed"] is True
    assert body["license_id"] == LICENSE_ID
    assert body["customer_id"] == CUSTOMER_ID
    assert body["customer_name"] == CUSTOMER_NAME
    sent = json.loads(data)
    assert body["issued_at"] == sent["issued_at"]
    assert body["expires_at"] == sent["expires_at"]
    grace_until = datetime.strptime(sent["expires_at"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=14)
    assert body["grace_until"] == grace_until.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert sorted(body["modules"]) == sorted(ALL_MODULES)
    assert body["unknown_modules"] == [] and body["unknown_limits"] == []
    assert body["limits"]["audio_seconds"] == 360000
    assert body["key_fingerprint"] == _fingerprint(licence.signer)
    assert isinstance(body["days_left"], int) and body["days_left"] > 30
    assert body["warnings"] == []
    assert signature.hex() not in response.text
    assert "PRIVATE KEY" not in response.text


@pytest.mark.parametrize("gone", ["licence_file", "signature_file", "both_files"])
def test_a_missing_licence_or_signature_is_missing(client, operator, licence, gone):
    licence.write(_window("valid"))
    assert _state(client, operator) == ("valid", None)
    if gone in ("licence_file", "both_files"):
        licence.file.unlink()
    if gone in ("signature_file", "both_files"):
        licence.sig.unlink()
    body = _status(client, operator)
    assert body["state"] == "missing"
    assert body["enforced"] is True
    assert body["processing_allowed"] is False
    _no_licence_fields(body)
    assert "license_missing" in body["warnings"]


@pytest.mark.parametrize("layout", ["trust_dir_missing", "trust_dir_empty", "no_pem_suffix"])
def test_no_trusted_key_is_invalid_no_trusted_keys(client, operator, licence, layout):
    licence.write(_window("valid"))
    assert _state(client, operator) == ("valid", None)
    if layout == "trust_dir_missing":
        shutil.rmtree(licence.keys)
    elif layout == "trust_dir_empty":
        (licence.keys / "vendor.pem").unlink()
    else:
        pem = (licence.keys / "vendor.pem").read_bytes()
        (licence.keys / "vendor.pem").unlink()
        licence.put(licence.keys / "vendor.pub", pem)
        licence.put(licence.keys / "vendor.pem.txt", pem)
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("invalid", "no_trusted_keys")
    assert body["processing_allowed"] is False
    _no_licence_fields(body)
    assert "license_invalid" in body["warnings"]


def test_an_empty_trust_dir_setting_is_invalid_no_trusted_keys(
    client, operator, licence, blank_trust_dir
):
    licence.write(_window("valid"))
    assert _state(client, operator) == ("invalid", "no_trusted_keys")


def _private_pem() -> bytes:
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _ec_public_pem() -> bytes:
    return _public_pem(ec.generate_private_key(ec.SECP256R1()))


BAD_TRUST_FILES: dict[str, Callable[[], bytes]] = {
    "ed25519_private_key": _private_pem,
    "ec_p256_public_key": _ec_public_pem,
    "not_a_key": lambda: b"-----BEGIN PUBLIC KEY-----\nbm90IGEga2V5\n-----END PUBLIC KEY-----\n",
    "empty_file": lambda: b"",
}


@pytest.mark.parametrize("bad", sorted(BAD_TRUST_FILES))
def test_a_trust_pem_that_is_not_an_ed25519_public_key_is_bad_trust_key(
    client, operator, licence, bad
):
    licence.write(_window("valid"))
    content = BAD_TRUST_FILES[bad]()
    extra = licence.keys / "zz-extra.pem"
    licence.put(extra, content)
    response = _read(client, operator, API)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["reason"]) == ("invalid", "bad_trust_key")
    assert "PRIVATE KEY" not in response.text
    for line in content.decode("ascii").splitlines():
        if line and not line.startswith("-----"):
            assert line not in response.text
    extra.unlink()
    assert _state(client, operator) == ("valid", None)


def test_one_changed_byte_after_signing_is_bad_signature(client, operator, licence):
    data = licence.write(_window("valid"))
    assert _state(client, operator) == ("valid", None)
    licence.tamper(data)
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("invalid", "bad_signature")
    _no_licence_fields(body)
    licence.put(licence.file, data)
    assert _state(client, operator) == ("valid", None)


def test_a_licence_signed_by_an_untrusted_key_is_bad_signature(client, operator, licence):
    stranger = ed25519.Ed25519PrivateKey.generate()
    licence.write(_window("valid"), key=stranger)
    assert _state(client, operator) == ("invalid", "bad_signature")
    licence.write(_window("valid"))
    assert _state(client, operator) == ("valid", None)


def test_key_rotation_any_trusted_key_verifies(client, operator, licence):
    second = ed25519.Ed25519PrivateKey.generate()
    licence.trust(second, "zz-second.pem")
    licence.write(_window("valid"), key=second)
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("valid", None)
    assert body["key_fingerprint"] == _fingerprint(second)


@pytest.mark.parametrize("length", [63, 65, 128])
def test_a_signature_that_is_not_64_bytes_is_bad_signature(client, operator, licence, length):
    data = licence.write(_window("valid"))
    good = licence.signer.sign(data)
    assert len(good) == 64
    assert _state(client, operator) == ("valid", None)
    licence.put(licence.sig, (good * 2)[:length])
    assert _state(client, operator) == ("invalid", "bad_signature")
    licence.put(licence.sig, good)
    assert _state(client, operator) == ("valid", None)


def test_key_material_next_to_or_inside_the_licence_is_never_trusted(client, operator, licence):
    stranger = ed25519.Ed25519PrivateKey.generate()
    licence.put(licence.dir / "vendor.pem", _public_pem(stranger))
    licence.put(licence.dir / "license.json.pem", _public_pem(stranger))
    embedded = _window("valid", public_key=_public_pem(stranger).decode("ascii"))
    licence.write(embedded, key=stranger)
    assert _state(client, operator) == ("invalid", "bad_signature")


def test_the_signature_is_checked_before_the_json(client, operator, licence):
    stranger = ed25519.Ed25519PrivateKey.generate()
    licence.write(raw=b"{not json", key=stranger)
    assert _state(client, operator) == ("invalid", "bad_signature")
    licence.write(raw=b"{not json")
    assert _state(client, operator) == ("invalid", "malformed")


def _padded(body: dict, size: int) -> bytes:
    data = _encode(body)
    assert len(data) <= size
    return data + b" " * (size - len(data))


def test_a_licence_over_65536_bytes_is_too_large(client, operator, licence):
    licence.write(raw=_padded(_window("valid"), 65536))
    assert _state(client, operator) == ("valid", None)
    licence.write(raw=_padded(_window("valid"), 65537))
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("invalid", "too_large")
    _no_licence_fields(body)


def _dup_top(body: dict) -> bytes:
    text = _encode(body).decode("utf-8")
    return text.replace('"license_id": ', '"license_id": "L-dup", "license_id": ', 1).encode()


def _dup_nested(body: dict) -> bytes:
    text = _encode(body).decode("utf-8")
    return text.replace('"limits": {', '"limits": {"audio_seconds": 5, ', 1).encode()


def _without(field: str) -> Callable[[dict], bytes]:
    return lambda body: _encode({key: value for key, value in body.items() if key != field})


def _with(**changes) -> Callable[[dict], bytes]:
    return lambda body: _encode({**body, **changes})


def _issued(transform: Callable[[str], str]) -> Callable[[dict], bytes]:
    return lambda body: _encode({**body, "issued_at": transform(body["issued_at"])})


MALFORMED: dict[str, Callable[[dict], bytes]] = {
    "duplicate_top_level_key": _dup_top,
    "duplicate_key_inside_limits": _dup_nested,
    "unknown_top_level_key": _with(notes="extra"),
    "schema_other_version": _with(schema="homayar-offline-license/2"),
    "schema_missing": _without("schema"),
    "license_id_missing": _without("license_id"),
    "license_id_leading_dash": _with(license_id="-L1"),
    "license_id_65_chars": _with(license_id="L" * 65),
    "license_id_with_space": _with(license_id="L 1"),
    "license_id_empty": _with(license_id=""),
    "license_id_number": _with(license_id=2027),
    "customer_id_with_slash": _with(customer_id="acme/x"),
    "customer_id_null": _with(customer_id=None),
    "customer_name_blank": _with(customer_name="   "),
    "customer_name_201_chars": _with(customer_name="a" * 201),
    "customer_name_newline": _with(customer_name="Acme\nCo"),
    "customer_name_nul": _with(customer_name="Acme\u0000"),
    "issued_at_offset": _issued(lambda value: value.replace("Z", "+00:00")),
    "issued_at_fraction": _issued(lambda value: value.replace("Z", ".000Z")),
    "issued_at_space_separator": _issued(lambda value: value.replace("T", " ")),
    "issued_at_lowercase_z": _issued(lambda value: value.replace("Z", "z")),
    "issued_at_impossible_date": _with(issued_at="2026-02-30T00:00:00Z"),
    "expires_at_missing": _without("expires_at"),
    "expires_at_equal_to_issued_at": lambda body: _encode(
        {**body, "expires_at": body["issued_at"]}
    ),
    "expires_at_before_issued_at": lambda body: _encode(
        {
            **body,
            "expires_at": _ts(
                datetime.strptime(body["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                - timedelta(days=1)
            ),
        }
    ),
    "grace_days_bool": _with(grace_days=True),
    "grace_days_91": _with(grace_days=91),
    "grace_days_negative": _with(grace_days=-1),
    "grace_days_float": _with(grace_days=1.0),
    "grace_days_string": _with(grace_days="14"),
    "modules_duplicate": _with(modules=["quality", "quality"]),
    "modules_string": _with(modules="quality"),
    "modules_number_item": _with(modules=["quality", 7]),
    "modules_null": _with(modules=None),
    "limits_list": _with(limits=[]),
    "limits_null": _with(limits=None),
    "limits_bool_value": _with(limits={"audio_seconds": True}),
    "limits_negative_value": _with(limits={"audio_seconds": -1}),
    "limits_float_value": _with(limits={"audio_seconds": 1.5}),
    "limits_string_value": _with(limits={"audio_seconds": "100"}),
    "top_level_list": lambda body: b"[]",
    "top_level_string": lambda body: b'"licence"',
    "not_json": lambda body: b"{not json",
    "not_utf8": lambda body: b'{"schema": "\xff\xfe"}',
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_signed_licence_that_breaks_the_schema_is_malformed(client, operator, licence, case):
    licence.write(raw=MALFORMED[case](_window("valid")))
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("invalid", "malformed")
    assert body["processing_allowed"] is False
    _no_licence_fields(body)


VALID_EDGES: dict[str, dict] = {
    "license_id_64_chars": {"license_id": "L" * 64},
    "ids_with_dot_underscore_dash": {"license_id": "L.2027_a-b", "customer_id": "acme.co_1-x"},
    "customer_name_200_chars": {"customer_name": "a" * 200},
    "customer_name_padded": {"customer_name": "  Acme  "},
    "grace_days_0": {"grace_days": 0},
    "grace_days_90": {"grace_days": 90},
    "modules_empty": {"modules": []},
    "limits_empty": {"limits": {}},
    "limits_zero": {"limits": {"audio_seconds": 0}},
    "limits_null_value": {"limits": {"audio_seconds": None}},
}


@pytest.mark.parametrize("case", sorted(VALID_EDGES))
def test_the_closest_well_formed_licences_are_valid(client, operator, licence, case):
    licence.write(_window("valid", **VALID_EDGES[case]))
    assert _state(client, operator) == ("valid", None)


@pytest.mark.parametrize("case", ["duplicate_top_level_key", "unknown_top_level_key"])
def test_the_same_file_without_the_schema_break_is_valid(client, operator, licence, case):
    body = _window("valid")
    licence.write(raw=MALFORMED[case](body))
    assert _state(client, operator) == ("invalid", "malformed")
    licence.write(body)
    assert _state(client, operator) == ("valid", None)


# --- 2.4 time states ---------------------------------------------------------------------


@pytest.mark.parametrize("state", ["valid", "grace", "expired"])
def test_time_states_of_a_verified_licence(client, operator, licence, state):
    data = licence.write(_window(state))
    body = _status(client, operator)
    assert body["state"] == state
    assert body["enforced"] is True
    assert body["processing_allowed"] is (state != "expired")
    sent = json.loads(data)
    assert body["license_id"] == LICENSE_ID
    assert body["customer_id"] == CUSTOMER_ID
    assert body["expires_at"] == sent["expires_at"]
    assert body["key_fingerprint"] == _fingerprint(licence.signer)
    if state == "valid":
        assert body["warnings"] == []
        assert body["days_left"] > 30
    elif state == "grace":
        assert body["reason"] is None
        assert "license_in_grace" in body["warnings"]
        assert body["days_left"] > 0
    else:
        assert "license_expired" in body["warnings"]
        assert body["days_left"] == 0


def test_a_licence_issued_more_than_a_day_ahead_is_not_yet_valid(client, operator, licence):
    licence.write(_window("not_yet_valid"))
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("invalid", "not_yet_valid")
    assert body["processing_allowed"] is False
    _no_licence_fields(body)
    assert "license_invalid" in body["warnings"]


BOUNDARIES = {
    "one_second_before_expiry": ("expires_at", -1, "valid", None),
    "exactly_at_expiry": ("expires_at", 0, "grace", None),
    "one_second_before_grace_end": ("grace_until", -1, "grace", None),
    "exactly_at_grace_end": ("grace_until", 0, "expired", None),
    "exactly_one_day_before_issue": ("issued_minus_day", 0, "valid", None),
    "one_second_earlier_than_that": ("issued_minus_day", -1, "invalid", "not_yet_valid"),
}


@pytest.mark.parametrize("case", sorted(BOUNDARIES))
def test_exact_time_boundaries(client, email_outbox, licence, case):
    anchor, offset, state, reason = BOUNDARIES[case]
    edge = _now()
    if anchor == "expires_at":
        body = _body(issued=edge - timedelta(days=100), expires=edge, grace_days=10)
    elif anchor == "grace_until":
        body = _body(
            issued=edge - timedelta(days=100), expires=edge - timedelta(days=10), grace_days=10
        )
    else:
        body = _body(issued=edge + timedelta(days=1), expires=edge + timedelta(days=100))
    licence.write(body)
    with freeze_time(edge + timedelta(seconds=offset)):
        workspace_id = _operator(client, email_outbox)
        assert client.get("/api/v1/auth/me").status_code == 200
        assert _state(client, workspace_id) == (state, reason)


def test_zero_grace_days_expire_exactly_at_expires_at(client, email_outbox, licence):
    edge = _now()
    licence.write(_body(issued=edge - timedelta(days=100), expires=edge, grace_days=0))
    with freeze_time(edge):
        workspace_id = _operator(client, email_outbox)
        assert client.get("/api/v1/auth/me").status_code == 200
        assert _state(client, workspace_id)[0] == "expired"


def test_the_state_follows_the_clock_without_a_file_change(client, email_outbox, licence):
    edge = _now()
    licence.write(
        _body(issued=edge - timedelta(days=10), expires=edge + timedelta(days=1), grace_days=1)
    )
    with freeze_time(edge):
        credentials, workspace_id = _sign_in(client, email_outbox, "clock@example.com")
        _system_role("clock@example.com", "operator")
        assert client.get("/api/v1/auth/me").status_code == 200
        first = _state(client, workspace_id)[0]
    with freeze_time(edge + timedelta(days=1)):
        login_user(client, credentials)
        assert client.get("/api/v1/auth/me").status_code == 200
        second = _state(client, workspace_id)[0]
    with freeze_time(edge + timedelta(days=2)):
        login_user(client, credentials)
        assert client.get("/api/v1/auth/me").status_code == 200
        third = _state(client, workspace_id)[0]
    assert (first, second, third) == ("valid", "grace", "expired")


@pytest.mark.parametrize(("days", "soon"), [(30, True), (31, False)], ids=["30_days", "31_days"])
def test_valid_days_left_and_the_expires_soon_warning(client, email_outbox, licence, days, soon):
    edge = _now()
    licence.write(_body(issued=edge - timedelta(days=10), expires=edge + timedelta(days=days)))
    with freeze_time(edge):
        workspace_id = _operator(client, email_outbox)
        body = _status(client, workspace_id)
    assert body["state"] == "valid"
    assert body["days_left"] == days
    assert ("license_expires_soon" in body["warnings"]) is soon
    if not soon:
        assert body["warnings"] == []


def test_grace_days_left_count_to_grace_until(client, email_outbox, licence):
    edge = _now()
    licence.write(
        _body(issued=edge - timedelta(days=100), expires=edge - timedelta(days=2), grace_days=7)
    )
    with freeze_time(edge):
        workspace_id = _operator(client, email_outbox)
        body = _status(client, workspace_id)
    assert body["state"] == "grace"
    assert body["grace_until"] == _ts(edge + timedelta(days=5))
    assert body["days_left"] == 5
    assert "license_in_grace" in body["warnings"]
    assert "license_expires_soon" not in body["warnings"]


# --- 2.5 the processing gate -------------------------------------------------------------


def test_a_tampered_licence_refuses_upload_and_the_untampered_file_allows_it(
    client, email_outbox, monkeypatch, licence
):
    data = licence.write(_window("valid"))
    workspace_id = _owner(client, email_outbox)
    _media(monkeypatch)
    before = _rows(workspace_id)
    licence.tamper(data)
    refused = _upload(client, workspace_id, "licence-tamper-0001", b"tampered wav one")
    _restricted(refused, "invalid", "bad_signature")
    assert _rows(workspace_id) == before
    licence.put(licence.file, data)
    accepted = _upload(client, workspace_id, "licence-tamper-0002", b"tampered wav two")
    assert accepted.status_code == 202, accepted.text
    assert HEADER not in accepted.headers


def test_a_missing_licence_refuses_upload(client, email_outbox, monkeypatch, licence):
    licence.write(_window("valid"))
    workspace_id = _owner(client, email_outbox)
    _media(monkeypatch)
    licence.remove()
    before = _rows(workspace_id)
    _restricted(_upload(client, workspace_id, "licence-missing-01", b"missing wav"), "missing")
    assert _rows(workspace_id) == before


def test_expired_refuses_processing_and_every_read_and_export_still_works(
    client, email_outbox, settings, monkeypatch, licence
):
    licence.write(_window("valid"))
    credentials, workspace_id, meeting_id = _bundle_processed(
        client, email_outbox, settings, monkeypatch
    )
    published = _publish_scorecard(client, workspace_id, "lic_kept")
    assert published.status_code < 400, published.text
    before = _rows(workspace_id)
    licence.write(_window("expired"))

    reads = {
        "scorecards": _read(client, workspace_id, SCORECARDS),
        "meetings": _read(client, workspace_id, "/api/v1/meetings"),
        "report_export": _read(
            client, workspace_id, f"/api/v1/meetings/{meeting_id}/report/export", format="json"
        ),
        "transcript_export": _read(
            client, workspace_id, f"/api/v1/meetings/{meeting_id}/export", format="json"
        ),
        "subject_export": client.post(
            "/api/v1/privacy/subject-exports",
            json={"identifiers": ["09121234567"], "reason": "DSAR licence"},
            headers=_write_headers(client, workspace_id),
        ),
    }
    for name, response in reads.items():
        assert response.status_code == 200, (name, response.text)
    assert [item["scorecard_key"] for item in reads["scorecards"].json()["items"]] == ["lic_kept"]
    assert meeting_id in reads["meetings"].text
    for name, response in reads.items():
        assert response.headers.get(HEADER) == "expired", name

    _restricted(_upload(client, workspace_id, "licence-expired-0001", b"expired wav"), "expired")
    _restricted(_publish_scorecard(client, workspace_id, "lic_new"), "expired", module="quality")
    assert _rows(workspace_id) == before
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ScorecardVersion)) == 1

    signed_out = client.post("/api/v1/auth/logout", headers=_write_headers(client, workspace_id))
    assert signed_out.status_code == 200, signed_out.text
    login_user(client, credentials)
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_write_headers(client, workspace_id)
    )
    assert deleted.status_code == 204, deleted.text


def test_grace_allows_processing_with_the_header_and_valid_has_none(
    client, email_outbox, monkeypatch, licence
):
    licence.write(_window("valid"))
    workspace_id = _owner(client, email_outbox)
    _set_limit(workspace_id, "concurrent_jobs", 10)
    _media(monkeypatch)
    in_valid = _upload(client, workspace_id, "licence-grace-0001", b"grace wav one")
    assert in_valid.status_code == 202, in_valid.text
    assert HEADER not in in_valid.headers
    licence.write(_window("grace"))
    in_grace = _upload(client, workspace_id, "licence-grace-0002", b"grace wav two")
    assert in_grace.status_code == 202, in_grace.text
    assert in_grace.headers.get(HEADER) == "grace"


@pytest.mark.parametrize(("cap", "allowed"), [(300, True), (299, False)], ids=["at_cap", "over"])
def test_the_licence_audio_cap_through_upload(
    client, email_outbox, monkeypatch, licence, cap, allowed
):
    licence.write(_window("valid", limits={"audio_seconds": cap}))
    workspace_id = _owner(client, email_outbox)
    _media(monkeypatch, seconds=300.0)
    before = _rows(workspace_id)
    response = _upload(client, workspace_id, "licence-cap-000001", b"cap wav")
    if allowed:
        assert response.status_code == 202, response.text
        return
    assert response.status_code == 429, response.text
    error = _error(response)
    assert error["code"] == "audio_quota_exceeded"
    assert error["details"]["limit"] == cap
    assert _rows(workspace_id) == before


@pytest.mark.parametrize(("cap", "allowed"), [(1, True), (0, False)], ids=["one_job", "zero"])
def test_the_licence_concurrency_cap_through_upload(
    client, email_outbox, monkeypatch, licence, cap, allowed
):
    licence.write(_window("valid", limits={"concurrent_jobs": cap}))
    workspace_id = _owner(client, email_outbox)
    _media(monkeypatch)
    before = _rows(workspace_id)
    response = _upload(client, workspace_id, "licence-jobs-00001", b"jobs wav")
    if allowed:
        assert response.status_code == 202, response.text
        return
    assert response.status_code == 429, response.text
    error = _error(response)
    assert error["code"] == "concurrent_job_limit_reached"
    assert error["details"]["limit"] == 0
    assert _rows(workspace_id) == before


CAP_CASES = {
    "exactly_at_cap": (1800, 600, 600, None),
    "over_cap_reports_cap": (1800, 600, 601, 600),
    "row_smaller_than_cap": (500, 600, 501, 500),
    "row_unlimited_reports_cap": (None, 600, 601, 600),
    "null_cap_adds_nothing": (None, None, 5000, None),
    "null_cap_row_still_applies": (1800, None, 1801, 1800),
}


@pytest.mark.parametrize("case", sorted(CAP_CASES))
def test_reserve_usage_applies_the_licence_cap_and_the_row_limit(
    client, email_outbox, licence, case
):
    row_limit, cap, amount, refused_limit = CAP_CASES[case]
    licence.write(_window("valid", limits={"audio_seconds": cap}))
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "cap@example.com")
    _set_limit(workspace_id, "audio_seconds", row_limit)
    if refused_limit is None:
        _reserve(workspace_id, meetings[0], amount, f"cap-{case}")
        assert _rows(workspace_id)["audio_seconds"] == (0, amount)
        return
    before = _rows(workspace_id)
    with pytest.raises(QuotaExceededError) as refused:
        _reserve(workspace_id, meetings[0], amount, f"cap-{case}")
    assert refused.value.limit == refused_limit
    assert refused.value.metric == "audio_seconds"
    assert _rows(workspace_id) == before


def test_the_cap_counts_used_and_reserved_values(client, email_outbox, licence):
    licence.write(_window("valid", limits={"audio_seconds": 600}))
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "used@example.com")
    first = _reserve(workspace_id, meetings[0], 200, "used-first")
    with inspection_session_factory()() as db:
        commit_usage(db, first, actual_amount=200)
        db.commit()
    _reserve(workspace_id, meetings[1], 400, "used-second")
    with pytest.raises(QuotaExceededError) as refused:
        _reserve(workspace_id, meetings[2], 1, "used-third")
    assert (refused.value.limit, refused.value.used, refused.value.reserved) == (600, 200, 400)


def test_the_licence_cap_applies_per_workspace(client, email_outbox, licence):
    licence.write(_window("valid", limits={"audio_seconds": 600}))
    first, first_meetings = _workspace_with_meetings(client, email_outbox, "ws-a@example.com")
    second, second_meetings = _workspace_with_meetings(client, email_outbox, "ws-b@example.com")
    _reserve(first, first_meetings[0], 600, "per-ws-a")
    _reserve(second, second_meetings[0], 600, "per-ws-b")
    with pytest.raises(QuotaExceededError):
        _reserve(first, first_meetings[1], 1, "per-ws-a-more")


def test_grace_applies_the_licence_cap(client, email_outbox, licence):
    licence.write(_window("grace", limits={"audio_seconds": 600}))
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "grace-cap@example.com")
    with pytest.raises(QuotaExceededError) as refused:
        _reserve(workspace_id, meetings[0], 601, "grace-over")
    assert refused.value.limit == 600
    _reserve(workspace_id, meetings[0], 600, "grace-at")


@pytest.mark.parametrize("state", ["expired", "invalid", "missing"])
def test_restricted_reserve_usage_raises_the_licence_error_and_writes_nothing(
    client, email_outbox, licence, state
):
    licence.write(_window("valid"))
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "gate@example.com")
    _make_restricted(licence, state)
    before = _rows(workspace_id)
    with pytest.raises(Exception) as refused:
        _reserve(workspace_id, meetings[0], 10, f"gate-{state}")
    error_class = _licence_error_class()
    assert isinstance(refused.value, error_class), repr(refused.value)
    assert not isinstance(refused.value, QuotaExceededError)
    assert not issubclass(error_class, QuotaExceededError)
    assert refused.value.state == state
    assert _rows(workspace_id) == before


def test_restricted_reused_key_returns_the_reservation_and_a_new_key_is_refused(
    client, email_outbox, licence
):
    licence.write(_window("valid"))
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "reuse@example.com")
    first = _reserve(workspace_id, meetings[0], 100, "reuse-audio")
    licence.write(_window("expired"))
    assert _reserve(workspace_id, meetings[0], 100, "reuse-audio") == first
    with pytest.raises(Exception) as refused:
        _reserve(workspace_id, meetings[1], 100, "reuse-new-key")
    assert isinstance(refused.value, _licence_error_class()), repr(refused.value)
    assert _rows(workspace_id)["audio_seconds"] == (0, 100)


def test_restricted_in_flight_commit_and_release_succeed(client, email_outbox, licence):
    licence.write(_window("valid"))
    workspace_id, meetings = _workspace_with_meetings(client, email_outbox, "flight@example.com")
    kept = _reserve(workspace_id, meetings[0], 100, "flight-commit")
    dropped = _reserve(workspace_id, meetings[1], 50, "flight-release")
    licence.write(_window("expired"))
    with inspection_session_factory()() as db:
        assert commit_usage(db, kept, actual_amount=80).state.value == "committed"
        assert release_usage(db, dropped, reason="job_cancelled").state.value == "released"
        db.commit()
    assert _rows(workspace_id)["audio_seconds"] == (80, 0)


def _replay(client, workspace_id: str, job_id: str):
    return client.post(
        f"/api/v1/admin/jobs/{job_id}/replay", headers=_write_headers(client, workspace_id)
    )


def test_replay_of_a_parked_job_is_refused_while_restricted(
    client, email_outbox, settings, monkeypatch, licence
):
    licence.write(_window("valid"))
    monkeypatch.setattr(outbox, "publish_processing_job", FakeBroker())
    job_id, workspace_id = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    licence.write(_window("expired"))
    before = _rows(workspace_id)
    _restricted(_replay(client, workspace_id, job_id), "expired")
    assert _rows(workspace_id) == before
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.state == JobState.DEAD_LETTER and job.replay_count == 0
    licence.write(_window("valid"))
    accepted = _replay(client, workspace_id, job_id)
    assert accepted.status_code == 202, accepted.text


def _import_voicemail(client, workspace_id: str, external_id: str):
    return client.post(
        "/api/v1/voicemails",
        headers=_write_headers(client, workspace_id),
        data={
            "external_message_id": external_id,
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
        },
        files={"file": ("vm.wav", external_id.encode(), "audio/wav")},
    )


def test_voicemail_import_is_refused_while_restricted_and_the_list_still_reads(
    client, email_outbox, monkeypatch, licence
):
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda job_id, _settings: True)
    licence.write(_window("valid"))
    workspace_id = _owner(client, email_outbox)
    licence.write(_window("expired"))
    listed = _read(client, workspace_id, "/api/v1/voicemails")
    assert listed.status_code == 200, listed.text
    _restricted(_import_voicemail(client, workspace_id, "vm-licence-1"), "expired")
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(VoicemailMessage)) == 0
    licence.write(_window("valid"))
    accepted = _import_voicemail(client, workspace_id, "vm-licence-2")
    assert accepted.status_code == 202, accepted.text


# --- 2.6 modules -------------------------------------------------------------------------


def test_a_module_left_out_of_a_valid_licence_is_not_licensed(client, email_outbox, licence):
    licence.write(_window("valid", modules=["insights"]))
    workspace_id = _owner(client, email_outbox)
    listed = _listed(client, workspace_id)
    assert listed["insights"]["licensed"] is True and listed["insights"]["enabled"] is True
    assert listed["quality"]["licensed"] is False
    assert listed["quality"]["reason"] == "not_licensed"
    _module_refusal(_read(client, workspace_id, SCORECARDS), "quality", "not_licensed")
    licence.write(_window("valid", modules=["insights", "quality"]))
    assert _read(client, workspace_id, SCORECARDS).status_code == 200
    assert _listed(client, workspace_id)["quality"]["licensed"] is True


def test_licence_modules_intersect_the_plan_rule(client, email_outbox, licence):
    licence.write(_window("valid", modules=["quality", "insights"]))
    workspace_id = _owner(client, email_outbox)
    pin_negotiated_plan(
        workspace_id, "negotiated_licence_mix", {"modules": ["quality", "voicemail"]}
    )
    assert _read(client, workspace_id, SCORECARDS).status_code == 200
    _module_refusal(
        _read(client, workspace_id, "/api/v1/trends/issues"), "insights", "not_licensed"
    )
    _module_refusal(_read(client, workspace_id, "/api/v1/voicemails"), "voicemail", "not_licensed")
    listed = _listed(client, workspace_id)
    assert [key for key, module in listed.items() if module["licensed"]] == ["quality"]


def test_unknown_module_and_limit_names_are_ignored_and_reported(client, operator, licence):
    licence.write(
        _window(
            "valid",
            modules=["quality", "teleport"],
            limits={"audio_seconds": 900, "minutes_of_magic": 5},
        )
    )
    body = _status(client, operator)
    assert (body["state"], body["reason"]) == ("valid", None)
    assert body["modules"] == ["quality"]
    assert body["unknown_modules"] == ["teleport"]
    assert body["limits"]["audio_seconds"] == 900
    assert "minutes_of_magic" not in body["limits"]
    assert body["unknown_limits"] == ["minutes_of_magic"]
    assert _read(client, operator, SCORECARDS).status_code == 200
    _module_refusal(_read(client, operator, "/api/v1/trends/issues"), "insights", "not_licensed")


def test_restricted_lets_safe_methods_through_and_refuses_writes(client, email_outbox, licence):
    licence.write(_window("valid", modules=["insights"]))
    workspace_id = _owner(client, email_outbox)
    licence.write(_window("expired", modules=["insights"]))
    assert _read(client, workspace_id, SCORECARDS).status_code == 200
    listed = _listed(client, workspace_id)
    for key in ALL_MODULES:
        assert listed[key]["licensed"] is True, key
        assert listed[key]["enabled"] is True, key
    _restricted(
        _publish_scorecard(client, workspace_id, "lic_refused"), "expired", module="quality"
    )


def test_restricted_safe_methods_still_obey_the_admin_and_install_switches(
    client, email_outbox, licence
):
    licence.write(_window("valid"))
    workspace_id = _owner(client, email_outbox)
    licence.write(_window("expired"))
    switch = "/api/v1/admin/capabilities/quality"
    headers = _write_headers(client, workspace_id)
    assert client.put(switch, json={"enabled": False}, headers=headers).status_code == 200
    _module_refusal(_read(client, workspace_id, SCORECARDS), "quality", "disabled_by_admin")
    assert client.put(switch, json={"enabled": True}, headers=headers).status_code == 200
    assert _read(client, workspace_id, SCORECARDS).status_code == 200
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(client.app.state.settings, "disabled_modules", "quality")
        _module_refusal(_read(client, workspace_id, SCORECARDS), "quality", "disabled_for_install")
    _restricted(_publish_scorecard(client, workspace_id, "lic_switch"), "expired", module="quality")


def test_restricted_refusal_order_is_authentication_membership_module_permission(
    client, email_outbox, licence
):
    licence.write(_window("valid"))
    owner, _, workspace_id = _caller(client, email_outbox, email="order-owner@example.com")
    viewer, viewer_id, _ = _caller(client, email_outbox, email="order-viewer@example.com")
    outsider, _, _ = _caller(client, email_outbox, email="order-outsider@example.com")
    _add_membership(workspace_id=workspace_id, user_id=viewer_id, role=WorkspaceRole.VIEWER)
    path = f"{SCORECARDS}/lic_order/versions"
    in_valid = viewer.request(workspace_id, "POST", path, json=_scorecard_body())
    assert in_valid.status_code == 403 and _error(in_valid)["code"] == "permission_denied"
    licence.write(_window("expired"))
    client.cookies.clear()
    anonymous = client.post(path, json=_scorecard_body(), headers={"X-Workspace-ID": workspace_id})
    assert anonymous.status_code == 401, anonymous.text
    stranger = outsider.request(workspace_id, "POST", path, json=_scorecard_body())
    assert stranger.status_code in (403, 404), stranger.text
    assert _error(stranger)["code"] == "workspace_access_denied"
    _restricted(
        viewer.request(workspace_id, "POST", path, json=_scorecard_body()),
        "expired",
        module="quality",
    )
    _restricted(
        owner.request(workspace_id, "POST", path, json=_scorecard_body()),
        "expired",
        module="quality",
    )


# --- 2.7 the operator endpoint -----------------------------------------------------------


def test_the_endpoint_is_for_platform_operator_sessions_only(client, email_outbox, licence):
    licence.write(_window("valid"))
    email = "licence-owner-only@example.com"
    workspace_id = _operator(client, email_outbox, email=email, operator=False)
    denied = _read(client, workspace_id, API)
    assert denied.status_code == 403, denied.text
    assert _error(denied)["code"] == "platform_operator_required"
    _membership(workspace_id, role=WorkspaceRole.ADMIN)
    denied = _read(client, workspace_id, API)
    assert denied.status_code == 403 and _error(denied)["code"] == "platform_operator_required"
    _membership(workspace_id, role=WorkspaceRole.OWNER)
    _system_role(email, "operator")
    allowed = _read(client, workspace_id, API)
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["cache-control"] == "no-store"

    minted = client.post(
        "/api/v1/api-keys",
        json={
            "name": "licence-key",
            "scopes": sorted(scope.value for scope in API_KEY_SCOPES),
            "expires_in_days": 30,
        },
        headers=_write_headers(client, workspace_id),
    )
    assert minted.status_code == 201, minted.text
    cookies = dict(client.cookies)
    client.cookies.clear()
    keyed = client.get(
        API, headers=_bearer(minted.json()["key"], **{"X-Workspace-ID": workspace_id})
    )
    assert keyed.status_code in (401, 403), keyed.text
    assert _error(keyed)["code"] in {
        "api_key_scope_denied",
        "platform_operator_required",
        "api_key_not_accepted",
    }
    anonymous = client.get(API, headers={"X-Workspace-ID": workspace_id})
    assert anonymous.status_code == 401, anonymous.text
    client.cookies.update(cookies)
    assert _read(client, workspace_id, API).status_code == 200


def test_the_route_declares_the_operator_read_policy_and_is_in_openapi(client):
    routes = [
        route
        for route in client.app.routes
        if isinstance(route, APIRoute) and route.path == API and "GET" in route.methods
    ]
    assert len(routes) == 1
    assert _route_policy(routes[0]) == "workspace:manage +operator"
    assert "get" in client.app.openapi()["paths"][API]


def test_the_endpoint_is_a_read_and_writes_no_audit_event(client, operator, licence):
    licence.write(_window("valid"))
    with inspection_session_factory()() as db:
        before = db.scalar(select(func.count()).select_from(AuditEvent))
    assert _read(client, operator, API).status_code == 200
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == before


def test_counters_show_each_row_with_the_licence_limit(client, email_outbox, monkeypatch, licence):
    licence.write(_window("valid", limits={"audio_seconds": 1000, "concurrent_jobs": None}))
    workspace_id = _operator(client, email_outbox)
    _media(monkeypatch, seconds=300.0)
    assert _upload(client, workspace_id, "licence-count-0001", b"count wav").status_code == 202
    _set_limit(workspace_id, "concurrent_jobs", None)
    row_limit = _limit(workspace_id, "audio_seconds")
    other = _owner(client, email_outbox, "counter-other@example.com")
    assert other != workspace_id
    login_user(client, {"email": "licence-operator@example.com", "password": "SecurePass123"})
    body = _status(client, workspace_id)
    counters = [{field: entry[field] for field in COUNTER_FIELDS} for entry in body["counters"]]
    assert counters == [
        {
            "metric": "audio_seconds",
            "limit": row_limit,
            "license_limit": 1000,
            "used": 0,
            "reserved": 300,
            "remaining": min(row_limit, 1000) - 300,
        },
        {
            "metric": "concurrent_jobs",
            "limit": None,
            "license_limit": None,
            "used": 0,
            "reserved": 1,
            "remaining": None,
        },
    ]


def test_a_licence_evaluation_through_the_api_opens_no_connection(client, operator, licence):
    licence.write(_window("grace"))
    with _no_network() as attempts:
        response = _read(client, operator, API)
        listed = _read(client, operator, CAPABILITIES)
    assert attempts == []
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "grace"
    assert listed.headers.get(HEADER) == "grace"


# --- 2.8 warnings: header and log --------------------------------------------------------


@pytest.mark.parametrize("state", ["grace", "expired", "invalid", "missing"])
def test_every_api_response_carries_the_state_header_when_not_valid(
    client, email_outbox, licence, state
):
    licence.write(_window("valid"))
    workspace_id = _owner(client, email_outbox)
    listed = _read(client, workspace_id, CAPABILITIES)
    assert listed.status_code == 200 and HEADER not in listed.headers
    if state == "grace":
        licence.write(_window("grace"))
    else:
        _make_restricted(licence, state)
    listed = _read(client, workspace_id, CAPABILITIES)
    assert listed.status_code == 200, listed.text
    assert listed.headers.get(HEADER) == state
    client.cookies.clear()
    anonymous = client.get("/api/v1/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.headers.get(HEADER) == state


def test_the_state_is_logged_once_per_change_without_the_customer_name(
    client, operator, licence, caplog
):
    caplog.set_level(logging.WARNING)
    licence.tamper(licence.write(_window("valid")))
    assert _state(client, operator)[0] == "invalid"
    caplog.clear()
    licence.write(_window("grace"))
    assert _state(client, operator)[0] == "grace"
    assert _state(client, operator)[0] == "grace"
    licence.write(_window("expired"))
    assert _state(client, operator)[0] == "expired"
    lines = [record for record in caplog.records if "OFFLINE_LICENSE_STATE" in record.getMessage()]
    grace = [record for record in lines if "state=grace" in record.getMessage()]
    expired = [record for record in lines if "state=expired" in record.getMessage()]
    assert len(grace) == 1, [record.getMessage() for record in lines]
    assert len(expired) == 1, [record.getMessage() for record in lines]
    for record in grace + expired:
        assert record.levelno == logging.WARNING
        assert f"license_id={LICENSE_ID}" in record.getMessage()
    for record in caplog.records:
        assert CUSTOMER_NAME not in record.getMessage()
        assert "PRIVATE KEY" not in record.getMessage()


# --- 2.9 the status line -----------------------------------------------------------------


def test_the_status_line_of_a_valid_licence_is_exact_and_offline(licence, monkeypatch, capsys):
    edge = _now()
    body = _body(
        issued=edge - timedelta(days=10),
        expires=edge + timedelta(days=40),
        modules=["quality"],
        limits={"audio_seconds": 360000, "concurrent_jobs": None},
    )
    licence.write(body)
    with freeze_time(edge), _no_network() as attempts:
        code, output = _cli_in_process(monkeypatch, capsys)
    assert attempts == []
    assert code == 0
    expected = (
        f"license: state=valid reason=- id={LICENSE_ID} customer={CUSTOMER_ID} "
        f"expires={body['expires_at']} grace_until={_ts(edge + timedelta(days=54))} "
        "days_left=40 modules=quality limits=audio_seconds=360000,concurrent_jobs=unlimited "
        f"key={_fingerprint(licence.signer)}"
    )
    assert output.splitlines() == [expected]
    assert CUSTOMER_NAME not in output


def test_the_status_line_runs_as_a_module_without_a_database(licence):
    licence.write(_window("grace", modules=["quality", "insights"]))
    result = _cli()
    assert result.returncode == 0, result.stderr
    pairs = _parse_status_line(result.stdout)
    assert [key for key, _ in pairs] == STATUS_KEYS
    fields = dict(pairs)
    assert fields["state"] == "grace"
    assert fields["reason"] == "-"
    assert fields["id"] == LICENSE_ID
    assert fields["customer"] == CUSTOMER_ID
    assert re.fullmatch(r"\d+", fields["days_left"])
    assert set(fields["modules"].split(",")) == {"quality", "insights"}
    assert set(fields["limits"].split(",")) == {
        "audio_seconds=unlimited",
        "concurrent_jobs=unlimited",
    }
    assert fields["key"] == _fingerprint(licence.signer)
    assert CUSTOMER_NAME not in result.stdout + result.stderr


@pytest.mark.parametrize("state", ["invalid", "missing"])
def test_the_status_line_without_a_verified_licence(licence, state):
    stranger = ed25519.Ed25519PrivateKey.generate()
    if state == "invalid":
        licence.write(_window("valid"), key=stranger)
    else:
        licence.remove()
    result = _cli()
    assert result.returncode == 0, result.stderr
    fields = dict(_parse_status_line(result.stdout))
    assert fields["state"] == state
    if state == "invalid":
        assert fields["reason"] == "bad_signature"
    for key in (
        "id",
        "customer",
        "expires",
        "grace_until",
        "days_left",
        "modules",
        "limits",
        "key",
    ):
        assert fields[key] == "-", (key, fields[key])


# --- 2.10 compose, env example and docs --------------------------------------------------


def _environment(service: dict) -> dict:
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        return dict(item.split("=", 1) if "=" in item else (item, None) for item in raw)
    return dict(raw)


def test_onprem_compose_passes_the_licence_to_api_and_worker_only():
    compose = yaml.safe_load(
        (REPO_ROOT / "infra" / "compose" / "compose.onprem.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    for name in ("api", "worker"):
        environment = _environment(services[name])
        assert environment.get("OFFLINE_LICENSE_FILE") == "${OFFLINE_LICENSE_FILE:-}", name
        assert (
            environment.get("OFFLINE_LICENSE_TRUST_DIR")
            == "${OFFLINE_LICENSE_TRUST_DIR:-/app/license/keys}"
        ), name
        mounts = services[name].get("volumes") or []
        assert any(
            (isinstance(mount, str) and mount.endswith(":/app/license:ro"))
            or (
                isinstance(mount, dict)
                and mount.get("target") == "/app/license"
                and mount.get("read_only") is True
            )
            for mount in mounts
        ), (name, mounts)
    for name, service in services.items():
        if name in ("api", "worker"):
            continue
        assert "OFFLINE_LICENSE_FILE" not in _environment(service), name


def test_env_example_lists_both_settings_empty():
    text = (HOMAYAR_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("OFFLINE_LICENSE_FILE", "OFFLINE_LICENSE_TRUST_DIR"):
        assert re.search(rf"^#?\s*{name}=\s*$", text, re.MULTILINE), name


def test_the_adr_and_the_runbook_section_exist():
    adr = REPO_ROOT / "docs" / "architecture" / "decisions" / "0073-signed-offline-license.md"
    text = adr.read_text(encoding="utf-8")
    for heading in ("## Context", "## Decision", "## Consequences", "## Alternatives"):
        assert re.search(rf"^{re.escape(heading)}", text, re.MULTILINE), heading
    assert re.search(r"^### D1\b", text, re.MULTILINE)
    runbook = (REPO_ROOT / "docs" / "operations" / "onprem-lifecycle.md").read_text(
        encoding="utf-8"
    )
    assert re.search(r"^#+ .*Offline licen[cs]e", runbook, re.MULTILINE)
