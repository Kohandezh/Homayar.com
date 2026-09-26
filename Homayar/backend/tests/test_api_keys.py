"""WP03-T07: API-key scope (ADR 0013).

A key is a workspace-owned credential bound to the user who created it. It
acts as that user, in that one workspace, with ``scopes ∩ API_KEY_SCOPES ∩
current membership permissions`` — re-resolved on every request, so revoked,
expired or demoted memberships, disabled accounts and revoked/expired keys bite
at once. Keys never reach session, organization, share-token or key-management
routes, never carry ``api_key:manage`` and never expose their secret after
creation. Work accepted through a key belongs to the workspace and survives the
key's revocation.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from backend.database.saas_models import (
    ApiKey,
    AuditEvent,
    MeetingStatus,
    Membership,
    ProcessingJob,
    User,
    WorkspaceRole,
)
from backend.jobs.processor import process_job
from backend.security.authorization import API_KEY_SCOPES, Permission
from backend.security.context import CSRF_COOKIE
from backend.services.api_key_service import api_key_digest
from backend.tests import test_row_level_security as _rls
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user
from backend.tests.test_authorization_matrix import _doc_block, _insert_meeting
from backend.tests.test_job_processor import _providers
from backend.tests.test_share_organization import _insert_membership
from backend.tests.test_share_policies import _anonymous, _cookies, _signup
from backend.tests.test_uploads_api import _authenticated_workspace, _valid_metadata

# PostgreSQL probe role: the fixture is discovered from this module namespace.
probe_role = _rls.probe_role


def _owner(client, email_outbox):
    credentials, workspace_id = _authenticated_workspace(client, email_outbox)
    cookies = _cookies(client)
    headers = {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]}
    return credentials, workspace_id, cookies, headers


def _mint(client, headers, cookies, *, name="ci", scopes=("meeting:read",), expires_in_days=30):
    return client.post(
        "/api/v1/api-keys",
        json={"name": name, "scopes": list(scopes), "expires_in_days": expires_in_days},
        headers=headers,
        cookies=cookies,
    )


def _bearer(raw_key: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}", **extra}


def _key_upload(client, raw_key: str, *, idempotency_key: str):
    return client.post(
        "/api/v1/uploads",
        headers=_bearer(raw_key, **{"Idempotency-Key": idempotency_key}),
        data={"title": "Automated upload", "language": "fa"},
        files={"file": ("call.wav", b"staged wav bytes", "audio/wav")},
    )


def _allow_uploads(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=65.2),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)


def _error(response) -> str:
    return response.json()["error"]["code"]


# --- issuance ---------------------------------------------------------------------


def test_key_issuance_is_bounded_and_never_exposes_the_secret(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, cookies, headers = _owner(client, email_outbox)

    # a member cannot mint keys; scopes outside the fixed set are refused
    member_session, member_cookies, _ = _signup(client, email_outbox, email="member@example.com")
    _insert_membership(workspace_id, member_session["user"]["id"], WorkspaceRole.MEMBER)
    member_headers = {**headers, "X-CSRF-Token": member_cookies[CSRF_COOKIE]}
    refused = _mint(client, member_headers, member_cookies)
    assert refused.status_code == 403 and _error(refused) == "permission_denied"
    for scopes in (["member:manage"], ["api_key:manage"], ["not-a-scope"], ["meeting:delete"]):
        invalid = _mint(client, headers, cookies, scopes=scopes)
        assert invalid.status_code == 422 and _error(invalid) == "invalid_scopes", scopes
    too_long = _mint(client, headers, cookies, expires_in_days=settings.api_key_max_ttl_days + 1)
    assert too_long.status_code == 422 and _error(too_long) == "invalid_expiry"

    created = _mint(client, headers, cookies, scopes=["meeting:create", "meeting:read"])
    assert created.status_code == 201, created.text
    body = created.json()
    raw_key = body["key"]
    assert raw_key.startswith("hk_") and body["key_prefix"] == raw_key[3:11]
    assert body["scopes"] == ["meeting:create", "meeting:read"]
    assert body["expires_at"] is not None and body["revoked_at"] is None
    assert "key_hash" not in body

    # the secret is stored only as a keyed digest; listings carry the prefix
    with inspection_session_factory()() as db:
        row = db.get(ApiKey, body["id"])
        assert row.key_hash == api_key_digest(raw_key, settings)
        assert raw_key not in (row.key_hash, row.key_prefix, row.name)
        created_events = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "api_key.created")
        ).all()
        assert [event.target_id for event in created_events] == [body["id"]]
        assert raw_key not in str(created_events[0].details)
        assert row.key_hash not in str(created_events[0].details)
    listed = client.get("/api/v1/api-keys", headers=headers, cookies=cookies)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]
    assert "key" not in listed.json()["items"][0]

    # live keys per workspace are bounded
    monkeypatch.setattr(settings, "api_key_max_active_per_workspace", 1)
    crowded = _mint(client, headers, cookies, name="second")
    assert crowded.status_code == 422 and _error(crowded) == "too_many_api_keys"


# --- authority ----------------------------------------------------------------------


def test_key_acts_as_its_user_within_scopes_and_its_workspace(
    client, email_outbox, settings, monkeypatch
):
    _allow_uploads(monkeypatch)
    _, workspace_id, cookies, headers = _owner(client, email_outbox)
    raw_key = _mint(client, headers, cookies, scopes=["meeting:read", "meeting:create"]).json()[
        "key"
    ]
    stranger_session, _, _ = _signup(client, email_outbox, email="stranger@example.com")
    stranger_workspace = stranger_session["workspaces"][0]["id"]
    foreign_meeting = _insert_meeting(
        stranger_workspace, created_by=stranger_session["user"]["id"], title="Foreign"
    )
    _anonymous(client)

    # the key's workspace is the tenant; no header needed, another header refused
    assert client.get("/api/v1/meetings", headers=_bearer(raw_key)).status_code == 200
    assert (
        client.get(
            "/api/v1/meetings", headers=_bearer(raw_key, **{"X-Workspace-ID": workspace_id})
        ).status_code
        == 200
    )
    pinned = client.get(
        "/api/v1/meetings", headers=_bearer(raw_key, **{"X-Workspace-ID": stranger_workspace})
    )
    assert pinned.status_code == 403 and _error(pinned) == "workspace_access_denied"
    assert (
        client.get(f"/api/v1/meetings/{foreign_meeting}", headers=_bearer(raw_key)).status_code
        == 404
    )

    # scopes bound the key below the owner's own permissions
    for method, path, body in (
        ("GET", "/api/v1/members", None),
        ("PATCH", "/api/v1/admin/workspace-settings", {"retention_days": 30}),
        ("POST", "/api/v1/api-keys", {"name": "escalate", "scopes": ["meeting:read"]}),
        ("GET", "/api/v1/api-keys", None),
    ):
        response = client.request(method, path, json=body, headers=_bearer(raw_key))
        assert response.status_code == 403, (method, path, response.text)
        assert _error(response) == "api_key_scope_denied", (method, path)

    # people-only surfaces refuse keys outright
    for path in ("/api/v1/organizations", "/api/v1/auth/me", f"/api/v1/shares/{'A' * 40}"):
        refused = client.get(path, headers=_bearer(raw_key))
        assert refused.status_code == 401 and _error(refused) == "api_key_not_accepted", path

    # a granted write works without CSRF; the job is accepted for the workspace
    accepted = _key_upload(client, raw_key, idempotency_key="key-upload-1")
    assert accepted.status_code == 202, accepted.text
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, accepted.json()["job"]["id"])
        assert job.workspace_id == workspace_id
        key_row = db.scalar(select(ApiKey).where(ApiKey.workspace_id == workspace_id))
        assert key_row.last_used_at is not None

    # requests per key are rate limited
    monkeypatch.setattr(settings, "api_key_rate_limit_per_hour", 1)
    limited = client.get("/api/v1/meetings", headers=_bearer(raw_key))
    assert limited.status_code == 429 and _error(limited) == "api_key_rate_limited"
    assert limited.headers.get("Retry-After")


def test_stale_keys_and_changed_membership_are_refused_per_request(
    client, email_outbox, settings, monkeypatch
):
    _allow_uploads(monkeypatch)
    owner_credentials, workspace_id, owner_cookies, owner_headers = _owner(client, email_outbox)

    # guessed and malformed keys
    for guess in (f"hk_{secrets.token_hex(4)}.{secrets.token_urlsafe(32)}", "hk_", "hk_x.y"):
        refused = client.get("/api/v1/meetings", headers=_bearer(guess))
        assert refused.status_code == 401 and _error(refused) == "api_key_invalid"

    # expiry is evaluated per request
    expiring = _mint(client, owner_headers, owner_cookies, name="expiring", expires_in_days=1)
    assert expiring.status_code == 201, expiring.text
    with inspection_session_factory()() as db:
        row = db.get(ApiKey, expiring.json()["id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    expired = client.get("/api/v1/meetings", headers=_bearer(expiring.json()["key"]))
    assert expired.status_code == 401 and _error(expired) == "api_key_invalid"

    # revocation is immediate and idempotent
    live = _mint(client, owner_headers, owner_cookies, name="live")
    raw_live = live.json()["key"]
    assert client.get("/api/v1/meetings", headers=_bearer(raw_live)).status_code == 200
    for expected_change in (True, False):
        revoked = client.delete(
            f"/api/v1/api-keys/{live.json()['id']}", headers=owner_headers, cookies=owner_cookies
        )
        assert revoked.status_code == 204, expected_change
    refused = client.get("/api/v1/meetings", headers=_bearer(raw_live))
    assert refused.status_code == 401 and _error(refused) == "api_key_invalid"
    with inspection_session_factory()() as db:
        assert (
            db.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.event_type == "api_key.revoked",
                    AuditEvent.target_id == live.json()["id"],
                )
            )
            is not None
        )
        assert (
            len(
                db.scalars(
                    select(AuditEvent).where(AuditEvent.event_type == "api_key.revoked")
                ).all()
            )
            == 1
        )
    unknown = client.delete(
        "/api/v1/api-keys/00000000-0000-4000-8000-000000000000",
        headers=owner_headers,
        cookies=owner_cookies,
    )
    assert unknown.status_code == 404 and _error(unknown) == "api_key_not_found"

    # an admin's key follows the admin's *current* role and membership
    admin_session, admin_cookies, _ = _signup(client, email_outbox, email="admin@example.com")
    membership_id = _insert_membership(
        workspace_id, admin_session["user"]["id"], WorkspaceRole.ADMIN
    )
    admin_headers = {**owner_headers, "X-CSRF-Token": admin_cookies[CSRF_COOKIE]}
    admin_key = _mint(
        client,
        admin_headers,
        admin_cookies,
        name="admin",
        scopes=["meeting:read", "meeting:create"],
    )
    assert admin_key.status_code == 201, admin_key.text
    raw_admin = admin_key.json()["key"]
    _anonymous(client)
    first_upload = _key_upload(client, raw_admin, idempotency_key="admin-upload-first")
    assert first_upload.status_code == 202, first_upload.text

    login_user(client, owner_credentials)
    demoted = client.patch(
        f"/api/v1/members/{membership_id}",
        json={"role": "viewer"},
        headers=owner_headers,
        cookies=owner_cookies,
    )
    assert demoted.status_code == 200, demoted.text
    _anonymous(client)
    assert client.get("/api/v1/meetings", headers=_bearer(raw_admin)).status_code == 200
    narrowed = _key_upload(client, raw_admin, idempotency_key="admin-upload-second")
    assert narrowed.status_code == 403 and _error(narrowed) == "permission_denied"

    with inspection_session_factory()() as db:
        membership = db.get(Membership, membership_id)
        membership.is_active = False
        db.commit()
    gone = client.get("/api/v1/meetings", headers=_bearer(raw_admin))
    assert gone.status_code == 403 and _error(gone) == "workspace_access_denied"

    with inspection_session_factory()() as db:
        user = db.get(User, admin_session["user"]["id"])
        user.is_active = False
        db.commit()
    disabled = client.get("/api/v1/meetings", headers=_bearer(raw_admin))
    assert disabled.status_code == 401 and _error(disabled) == "api_key_invalid"


def test_revoking_a_key_does_not_cancel_accepted_work(client, email_outbox, settings, monkeypatch):
    _allow_uploads(monkeypatch)
    _, workspace_id, cookies, headers = _owner(client, email_outbox)
    raw_key = _mint(
        client, headers, cookies, name="ingest", scopes=["meeting:read", "meeting:create"]
    ).json()["key"]
    key_id = client.get("/api/v1/api-keys", headers=headers, cookies=cookies).json()["items"][0][
        "id"
    ]
    _anonymous(client)
    accepted = _key_upload(client, raw_key, idempotency_key="survives-revocation")
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    meeting_id = accepted.json()["meeting"]["id"]

    revoked = client.delete(f"/api/v1/api-keys/{key_id}", headers=headers, cookies=cookies)
    assert revoked.status_code == 204
    assert client.get("/api/v1/meetings", headers=_bearer(raw_key)).status_code == 401

    # the accepted job belongs to the workspace, not to the credential
    result = process_job(
        job_id, worker_id="test-api-key-worker", settings=settings, providers=_providers()
    )
    assert result["status"] == "completed"
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.workspace_id == workspace_id and job.state.value == "completed"
    login_user(client, {"email": "upload-0@example.com", "password": "SecurePass123"})
    meeting = client.get(f"/api/v1/meetings/{meeting_id}", headers={"X-Workspace-ID": workspace_id})
    assert meeting.status_code == 200 and meeting.json()["status"] == MeetingStatus.COMPLETED.value


# --- document and database ceiling ----------------------------------------------


def test_api_key_scope_document_matches_code():
    rows = _doc_block("api-key-scopes")
    documented = {row[0]: row[1] for row in rows}
    assert set(documented) == {permission.value for permission in Permission}
    assert {scope for scope, mark in documented.items() if mark == "✓"} == {
        permission.value for permission in API_KEY_SCOPES
    }
    assert Permission.API_KEY_MANAGE not in API_KEY_SCOPES


@pytest.mark.postgres
def test_api_key_context_exposes_only_the_presented_key(client, email_outbox, probe_role):
    """A presented key digest is a ceiling of one api_keys row until the tenant is bound."""
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    _, workspace_id, cookies, headers = _owner(client, email_outbox)
    first = _mint(client, headers, cookies, name="first").json()
    second = _mint(client, headers, cookies, name="second").json()
    other_session, other_cookies, _ = _signup(client, email_outbox, email="other@example.com")
    other_headers = {
        "X-Workspace-ID": other_session["workspaces"][0]["id"],
        "X-CSRF-Token": other_cookies[CSRF_COOKIE],
    }
    assert _mint(client, other_headers, other_cookies, name="elsewhere").status_code == 201
    digest = api_key_digest(first["key"], client.app.state.settings)

    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            _rls._bind(connection, api_key_hash=digest)
            assert connection.execute(text("SELECT id FROM api_keys")).scalars().all() == [
                first["id"]
            ]
            assert _rls._count(connection, "meetings") == 0
            assert _rls._count(connection, "memberships") == 0
            assert _rls._count(connection, "workspaces") == 0
            stamped = connection.execute(
                text("UPDATE api_keys SET last_used_at = now() WHERE id = :id"), {"id": first["id"]}
            )
            assert stamped.rowcount == 1
            invisible = connection.execute(
                text("UPDATE api_keys SET last_used_at = now() WHERE id = :id"),
                {"id": second["id"]},
            )
            assert invisible.rowcount == 0
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text(
                        "INSERT INTO api_keys (id, workspace_id, created_by_user_id, name, "
                        "key_prefix, key_hash, scopes, created_at, updated_at) VALUES "
                        "(:id, :ws, (SELECT created_by_user_id FROM api_keys WHERE id = :first), "
                        "'forged', 'deadbeef', :hash, '[]', now(), now())"
                    ),
                    {
                        "id": secrets.token_hex(8),
                        "ws": workspace_id,
                        "first": first["id"],
                        "hash": "f" * 64,
                    },
                )
            connection.rollback()
            # no context at all: nothing
            assert _rls._count(connection, "api_keys") == 0
            connection.rollback()
    finally:
        engine.dispose()
