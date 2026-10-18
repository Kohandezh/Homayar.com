"""Audit event model and coverage (HOM-114, ADR 0036).

Every recorded event names its actor, tenant, action, target, time, result and
correlation id; secrets never reach a row; refused attempts are recorded best
effort; a failed transactional audit write rolls back the operation it
describes; tenant admins see only their own workspace's events.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

import pytest
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from backend.database.saas_models import AuditEvent, Team
from backend.observability import metrics
from backend.security.context import CSRF_COOKIE, REFRESH_COOKIE
from backend.services import audit_service
from backend.services.audit_service import AUDIT_EVENT_CATALOG, record_audit_event
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_share_policies import _cookies

BACKEND = Path(__file__).resolve().parents[1]
EVENT_TYPE_SHAPE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
PASSWORD = "SecurePass123"  # noqa: S105 - the conftest test account password


def _events(event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == event_type)
                .order_by(AuditEvent.occurred_at)
            )
        )


def _row_text(event: AuditEvent) -> str:
    return json.dumps(
        {
            column: getattr(event, column)
            for column in (
                "event_type", "actor_user_id", "workspace_id", "target_type", "target_id",
                "outcome", "request_id", "ip_hash", "details",
            )
        },
        default=str,
        ensure_ascii=False,
    )


def _sample(name: str, **labels: str) -> float | None:
    """A sample as /metrics reports it, in single- and multiprocess mode."""
    exposition = metrics.in_process_exposition().decode()
    for family in text_string_to_metric_families(exposition):
        for sample in family.samples:
            if sample.name == name and sample.labels == labels:
                return sample.value
    return None


def _counter(name: str, **labels: str) -> float:
    return _sample(name, **labels) or 0.0


def test_failure_counters_are_exported_at_zero_and_bounded():
    # increase() needs a 0 sample before the first failure (PR #47 review, P4).
    for reason in metrics.AUTH_FAILURE_REASONS:
        assert _sample("homayar_auth_failures_total", reason=reason) is not None, reason
    for delivery in metrics.AUDIT_WRITE_DELIVERIES:
        assert _sample("homayar_audit_write_failures_total", delivery=delivery) is not None
    before = _counter("homayar_auth_failures_total", reason="other")
    metrics.observe_auth_failure("user@example.com")  # never a label value
    metrics.observe_audit_write_failure("anything")
    assert _counter("homayar_auth_failures_total", reason="other") == before + 1
    assert _sample("homayar_auth_failures_total", reason="user@example.com") is None


def test_a_failing_counter_write_never_fails_the_caller(monkeypatch):
    class FullDirectory:
        def labels(self, *labels):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(metrics, "AUTH_FAILURES", FullDirectory())
    monkeypatch.setattr(metrics, "AUDIT_WRITE_FAILURES", FullDirectory())
    monkeypatch.setattr(metrics, "guarded_write", metrics.WriteGuard())
    metrics.observe_auth_failure("refresh_replay")  # neither raises
    metrics.observe_audit_write_failure("transactional")


# --- catalog -------------------------------------------------------------------------


def _recorded_event_types() -> set[str]:
    """String literals that code passes as an audit event type.

    Covers ``event_type=``/``event=`` keywords, module constants named ``*_EVENT``,
    assignments to a local ``event_type`` and the positional event argument of the
    routers' ``_audit`` helpers.
    """
    found: set[str] = set()

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if EVENT_TYPE_SHAPE.fullmatch(node.value):
                return node.value
        return None

    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts or ".venv" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                for keyword in node.keywords:
                    if keyword.arg in ("event_type", "event"):
                        if value := literal(keyword.value):
                            found.add(value)
                if name == "_audit":
                    for argument in node.args:
                        if value := literal(argument):
                            found.add(value)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    target_name = getattr(target, "id", "")
                    if target_name == "event_type" or target_name.endswith("_EVENT"):
                        if value := literal(node.value):
                            found.add(value)
    return found


def test_every_recorded_event_type_is_catalogued():
    recorded = _recorded_event_types()
    assert recorded, "the scan found no audit event types"
    assert recorded - set(AUDIT_EVENT_CATALOG) == set()
    # and the catalog lists nothing the code no longer records
    assert set(AUDIT_EVENT_CATALOG) - recorded == set()


def test_catalog_categories_cover_the_required_operations():
    categories = set(AUDIT_EVENT_CATALOG.values())
    assert categories == {
        "security", "access", "privacy_access", "configuration", "administration",
        "deletion", "processing",
    }
    for required in (
        "auth.login", "auth.logout", "auth.refresh_replay_detected",
        "auth.password_reset_completed",
        "member.role_changed", "member.removed", "organization.scope.granted",
        "transcript.original_accessed", "pii.revealed", "subject.exported", "meeting.exported",
        "workspace.settings.updated", "ai_policy.updated", "model_manifest.registered",
        "meeting.processing.replayed", "meeting.deleted", "meeting.purge_completed",
        "media.legal_hold_released", "meeting.share_created", "api_key.revoked",
    ):
        assert required in AUDIT_EVENT_CATALOG, required


# --- authentication events -------------------------------------------------------------


def test_login_success_records_actor_target_time_result_and_correlation(client, email_outbox):
    credentials = register_user(client, email_outbox, email="audit-login@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": credentials["email"], "password": credentials["password"]},
        headers={"X-Request-ID": "login-request-0001"},
    )
    assert response.status_code == 200
    user_id = response.json()["user"]["id"]
    [event] = _events("auth.login")
    assert event.actor_user_id == user_id
    assert event.workspace_id is None  # platform event
    assert (event.target_type, event.target_id) == ("user", user_id)
    assert event.outcome == "success"
    assert event.request_id == "login-request-0001"
    assert event.occurred_at is not None
    assert event.ip_hash and len(event.ip_hash) == 64
    assert event.details == {"method": "password"}
    assert credentials["password"] not in _row_text(event)


def test_failed_login_is_recorded_without_identifier_or_password(client, email_outbox):
    before = _counter("homayar_auth_failures_total", reason="invalid_credentials")
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": "nobody@example.com", "password": "Wrong-Password-1"},
    )
    assert response.status_code == 401
    [event] = _events("auth.login")
    assert event.outcome == "failure"
    assert event.actor_user_id is None
    assert event.target_type == "login_identifier"
    assert event.target_id and len(event.target_id) == 16
    assert event.details == {"method": "password", "reason": "invalid_credentials"}
    text = _row_text(event)
    assert "nobody@example.com" not in text and "Wrong-Password-1" not in text
    assert _counter("homayar_auth_failures_total", reason="invalid_credentials") == before + 1


def test_logout_and_refresh_replay_are_audited(client, email_outbox):
    credentials = register_user(client, email_outbox, email="audit-logout@example.com")
    session = login_user(client, credentials)
    user_id = session["user"]["id"]
    stolen = client.cookies.get(REFRESH_COOKIE)
    csrf = client.cookies.get(CSRF_COOKIE)
    assert stolen and csrf
    rotated = client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert rotated.status_code == 200

    # The old refresh token comes back (with the current CSRF pair): replay,
    # detected before the CSRF hash check, and the session is revoked.
    csrf = client.cookies.get(CSRF_COOKIE)
    client.cookies.delete(REFRESH_COOKIE)
    client.cookies.set(REFRESH_COOKIE, stolen)
    replay = client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 401
    [replayed] = _events("auth.refresh_replay_detected")
    assert replayed.actor_user_id == user_id
    assert replayed.outcome == "denied"
    assert replayed.target_type == "login_session"
    assert replayed.details == {"session_revoked": True}
    assert stolen not in _row_text(replayed)

    client.cookies.clear()  # drop the replayed token set by hand above
    login_user(client, credentials)
    csrf = client.cookies.get(CSRF_COOKIE)
    logged_out = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logged_out.status_code == 200
    [logout] = _events("auth.logout")
    assert logout.actor_user_id == user_id
    assert logout.target_type == "login_session"
    assert logout.outcome == "success"


def test_model_manifest_registration_is_audited_once(client):
    from backend.database.relational import get_session_factory
    from backend.providers.registry import ModelManifest
    from backend.services.model_registry_service import register_manifest
    from backend.tests.test_model_registry import _bundle, _qualifying

    manifest = ModelManifest.model_validate(_qualifying(_bundle())[0])
    with get_session_factory()() as db:
        register_manifest(db, manifest)
        register_manifest(db, manifest)  # already registered: no second event
        db.commit()
    [event] = _events("model_manifest.registered")
    assert (event.target_type, event.target_id) == ("model_manifest", manifest.digest)
    assert event.workspace_id is None and event.actor_user_id is None
    assert event.details["manifest_id"] == manifest.manifest_id
    assert event.details["version"] == manifest.version


@pytest.mark.postgres
def test_a_failed_registration_audit_is_not_mistaken_for_a_duplicate(client, monkeypatch):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL column limits")
    from backend.database.relational import get_session_factory
    from backend.database.saas_models import ModelManifestRecord
    from backend.providers.registry import ModelManifest
    from backend.services import model_registry_service
    from backend.tests.test_model_registry import _bundle, _qualifying

    def too_long(db, **fields):  # a real database failure of the audit insert
        return record_audit_event(db, **{**fields, "outcome": "x" * 40})

    monkeypatch.setattr(model_registry_service, "record_audit_event", too_long)
    manifest = ModelManifest.model_validate(_qualifying(_bundle())[0])
    with get_session_factory()() as db:
        with pytest.raises(DBAPIError):
            model_registry_service.register_manifest(db, manifest)
        db.rollback()
    with inspection_session_factory()() as db:
        assert db.get(ModelManifestRecord, manifest.digest) is None


# --- secrets never reach a row -----------------------------------------------------------


def test_secret_keys_and_secret_shaped_values_are_redacted_at_write(client, settings):
    from backend.database.relational import get_session_factory

    bearer = "Bearer " + "q" * 20
    jwt = ".".join(["eyJ" + "hbGciOiJub25lIn0", "eyJ" + "zdWIiOiIxIn0", "c2ln" + "bmF0dXJl"])
    with get_session_factory()() as db:
        record_audit_event(
            db,
            event_type="auth.login",
            details={
                "password": "p" * 12,
                "refresh_token": "r" * 20,
                "authorization": bearer,
                "session_cookie": "c" * 16,
                "header": bearer,
                "note": jwt,
                "count": 3,
                "role": "admin",
            },
            settings=settings,
        )
        db.commit()
    [event] = _events("auth.login")
    redacted = audit_service.REDACTED
    for key in ("password", "refresh_token", "authorization", "session_cookie", "header", "note"):
        assert event.details[key] == redacted, key
    assert event.details["count"] == 3 and event.details["role"] == "admin"
    text = _row_text(event)
    for secret in ("p" * 12, "r" * 20, "q" * 20, "c" * 16, "bmF0dXJl"):
        assert secret not in text


# --- delivery semantics -------------------------------------------------------------------


def test_a_failed_best_effort_write_never_changes_the_response(client, email_outbox, monkeypatch):
    real = audit_service.record_audit_event

    def broken(db, **fields):
        if fields.get("outcome") == "failure":
            raise RuntimeError("audit store unavailable")
        return real(db, **fields)

    monkeypatch.setattr(audit_service, "record_audit_event", broken)
    before = _counter("homayar_audit_write_failures_total", delivery="best_effort")
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": "ghost@example.com", "password": "Wrong-Password-1"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert _counter("homayar_audit_write_failures_total", delivery="best_effort") == before + 1
    assert _events("auth.login") == []


@pytest.mark.postgres
def test_a_failed_transactional_write_rolls_back_the_operation(client, email_outbox, settings):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL column limits")
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context, bind_user_context

    credentials = register_user(client, email_outbox, email="audit-rollback@example.com")
    session = login_user(client, credentials)
    workspace_id = session["workspaces"][0]["id"]
    before = _counter("homayar_audit_write_failures_total", delivery="transactional")
    best_before = _counter("homayar_audit_write_failures_total", delivery="best_effort")
    with get_session_factory()() as db:
        bind_user_context(db, session["user"]["id"])
        bind_tenant_context(db, workspace_id=workspace_id)
        db.add(Team(workspace_id=workspace_id, name="never-created"))
        record_audit_event(
            db,
            event_type="team.created",
            workspace_id=workspace_id,
            outcome="x" * 40,  # longer than the column: the audit insert fails
            settings=settings,
        )
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(Team.id)).where(Team.name == "never-created")) == 0
    assert _counter("homayar_audit_write_failures_total", delivery="transactional") == before + 1
    assert _counter("homayar_audit_write_failures_total", delivery="best_effort") == best_before


@pytest.mark.postgres
def test_a_failed_best_effort_insert_is_counted_once_as_best_effort(client, settings):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL column limits")
    from backend.database.relational import get_session_factory

    transactional = _counter("homayar_audit_write_failures_total", delivery="transactional")
    best_effort = _counter("homayar_audit_write_failures_total", delivery="best_effort")
    with get_session_factory()() as db:
        written = audit_service.record_audit_event_best_effort(
            db, event_type="auth.login", outcome="x" * 40, settings=settings  # real DB error
        )
    assert written is False
    assert _counter("homayar_audit_write_failures_total", delivery="best_effort") == (
        best_effort + 1
    )
    assert _counter("homayar_audit_write_failures_total", delivery="transactional") == (
        transactional
    )


def test_a_failing_replay_audit_never_undoes_the_revocation(client, email_outbox, monkeypatch):
    from backend.database.saas_models import LoginSession

    credentials = register_user(client, email_outbox, email="audit-replay-fail@example.com")
    session = login_user(client, credentials)
    user_id = session["user"]["id"]
    stolen = client.cookies.get(REFRESH_COOKIE)
    csrf = client.cookies.get(CSRF_COOKIE)
    assert client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf}).status_code == 200
    real = audit_service.record_audit_event

    def broken(db, **fields):
        if fields.get("event_type") == "auth.refresh_replay_detected":
            raise RuntimeError("audit store unavailable")
        return real(db, **fields)

    monkeypatch.setattr(audit_service, "record_audit_event", broken)
    csrf = client.cookies.get(CSRF_COOKIE)
    client.cookies.delete(REFRESH_COOKIE)
    client.cookies.set(REFRESH_COOKIE, stolen)
    replay = client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 401
    with inspection_session_factory()() as db:
        sessions = list(db.scalars(select(LoginSession).where(LoginSession.user_id == user_id)))
    assert sessions and all(s.revoked_at is not None for s in sessions)
    assert _events("auth.refresh_replay_detected") == []


# --- masking and nesting -------------------------------------------------------------------


def test_nested_values_are_redacted_and_masked_too(client, settings):
    from backend.database.relational import get_session_factory

    token = "t" * 24
    with get_session_factory()() as db:
        record_audit_event(
            db,
            event_type="workspace.settings.updated",
            details={
                "provider": {"api_token": token, "endpoint": {"secret": token}, "label": "x"},
                "contacts": [f"call 0912{'1234567'}", {"password": token}],
                "deep": {"a": {"b": {"c": {"d": token}}}},
            },
            settings=settings,
        )
        db.commit()
    [event] = _events("workspace.settings.updated")
    assert token not in _row_text(event)
    assert event.details["provider"]["api_token"] == audit_service.REDACTED
    assert event.details["contacts"][0] == "call [PHONE_1]"
    assert event.details["contacts"][1] == {"password": audit_service.REDACTED}


def test_every_string_is_masked_unless_its_key_holds_ids_or_enums(client, settings):
    from backend.database.relational import get_session_factory

    phone, email = "0912" + "7654321", "ali.rezaei" + "@example.com"
    meeting_id = "0" * 7 + "912" + "1234567"  # digits that could read as a phone
    with get_session_factory()() as db:
        record_audit_event(
            db,
            event_type="meeting.updated",
            details={"message": f"call {phone} or {email}", "meeting_id": meeting_id,
                     "state": "completed", "digest": "a" * 64},
            settings=settings,
        )
        db.commit()
    [event] = _events("meeting.updated")
    assert event.details["message"] == "call [PHONE_1] or [EMAIL_1]"
    assert event.details["meeting_id"] == meeting_id
    assert event.details["state"] == "completed" and event.details["digest"] == "a" * 64


def test_an_uncatalogued_event_type_is_logged_once(client, settings, caplog):
    from backend.database.relational import get_session_factory
    from backend.services.logging_service import logger

    logger.disabled = False
    with caplog.at_level(logging.WARNING, logger="homayar"), get_session_factory()() as db:
        for _ in range(2):
            record_audit_event(db, event_type="test.not_catalogued", settings=settings)
        db.commit()
    warnings = [r.getMessage() for r in caplog.records
                if r.getMessage().startswith("AUDIT_EVENT_UNCATALOGUED")]
    assert warnings == ["AUDIT_EVENT_UNCATALOGUED event_type=test.not_catalogued"]
    assert len(_events("test.not_catalogued")) == 2  # the writes still happen


# --- tenant visibility ---------------------------------------------------------------------


def test_admins_read_only_their_workspace_events(client, email_outbox):
    tenants = []
    for label in ("one", "two"):
        credentials = register_user(client, email_outbox, email=f"audit-{label}@example.com")
        session = login_user(client, credentials)
        tenants.append((session["workspaces"][0]["id"], _cookies(client)))
        client.patch(
            "/api/v1/admin/workspace-settings",
            json={"name": f"Workspace {label}"},
            headers={
                "X-Workspace-ID": session["workspaces"][0]["id"],
                "X-CSRF-Token": client.cookies.get(CSRF_COOKIE),
            },
        )
    for workspace_id, cookies in tenants:
        listed = client.get(
            "/api/v1/admin/audit-events",
            headers={"X-Workspace-ID": workspace_id},
            cookies=cookies,
        )
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert items, "the admin sees their own events"
        ids = {item["id"] for item in items}
        with inspection_session_factory()() as db:
            own = set(
                db.scalars(select(AuditEvent.id).where(AuditEvent.workspace_id == workspace_id))
            )
        # only this workspace's rows: no other tenant, no platform (login) events
        assert ids <= own
        assert not any(item["event_type"].startswith("auth.") for item in items)
