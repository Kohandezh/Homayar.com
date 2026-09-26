from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    AuditEvent,
    Membership,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.routers.v1 import admin as admin_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.services.admin_service import WorkspaceNotFoundError
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


@pytest.fixture
def admin_client(client):
    client.app.include_router(admin_router.router, prefix="/api/v1")
    return client


def _session_cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _user_and_workspace(email: str) -> tuple[str, str]:
    factory = inspection_session_factory()
    with factory() as db:
        row = db.execute(
            select(User.id, Workspace.id)
            .join(Membership, Membership.user_id == User.id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(User.email == email)
            .order_by(Membership.created_at.asc())
        ).first()
        assert row is not None
        return row[0], row[1]


def _register_and_login(admin_client, email_outbox, *, email: str):
    credentials = register_user(admin_client, email_outbox, email=email)
    login_user(admin_client, credentials)
    user_id, workspace_id = _user_and_workspace(email)
    return user_id, workspace_id, _session_cookies(admin_client)


def _add_membership(*, workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    factory = inspection_session_factory()
    with factory() as db:
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role=role))
        db.commit()


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def test_owner_can_update_safe_settings_and_update_is_audited(
    admin_client,
    email_outbox,
):
    owner_id, workspace_id, cookies = _register_and_login(
        admin_client,
        email_outbox,
        email="admin-owner@example.com",
    )
    headers = {"X-Workspace-ID": workspace_id}

    read_response = admin_client.get(
        "/api/v1/admin/workspace-settings",
        cookies=cookies,
        headers=headers,
    )
    assert read_response.status_code == 200
    assert set(read_response.json()) == {
        "id",
        "name",
        "retention_days",
        "retention_mode",
        "delete_audio_after_processing",
        "record_scope",
        "public_share_links_enabled",
        "organization_share_links_enabled",
        "duplicate_media_policy",
        "pii_redaction_enabled",
        "pii_redaction_classes",
        "updated_at",
    }
    assert read_response.json()["retention_mode"] == "finite"
    assert read_response.json()["record_scope"] == "workspace"
    assert read_response.json()["duplicate_media_policy"] == "allow"
    assert read_response.json()["public_share_links_enabled"] is False
    assert read_response.json()["organization_share_links_enabled"] is True

    no_csrf = admin_client.patch(
        "/api/v1/admin/workspace-settings",
        json={"retention_days": 30},
        cookies=cookies,
        headers=headers,
    )
    assert no_csrf.status_code == 403
    assert _error_code(no_csrf) == "csrf_failed"

    write_headers = {**headers, "X-CSRF-Token": cookies[CSRF_COOKIE]}
    unknown = admin_client.patch(
        "/api/v1/admin/workspace-settings",
        json={"settings_json": {"unsafe": True}},
        cookies=cookies,
        headers=write_headers,
    )
    assert unknown.status_code == 422
    assert _error_code(unknown) == "validation_error"

    updated = admin_client.patch(
        "/api/v1/admin/workspace-settings",
        json={
            "name": "  Customer Success  ",
            "retention_days": 45,
            "delete_audio_after_processing": True,
        },
        cookies=cookies,
        headers=write_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Customer Success"
    assert updated.json()["retention_days"] == 45
    assert updated.json()["delete_audio_after_processing"] is True

    factory = inspection_session_factory()
    with factory() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.event_type == "workspace.settings.updated",
            )
        )
        assert event is not None
        assert event.actor_user_id == owner_id
        assert event.target_id == workspace_id
        assert event.details == {"fields": "delete_audio_after_processing,name,retention_days"}
        assert event.ip_hash is None


def test_audit_events_are_tenant_scoped_filtered_sanitized_and_paginated(
    admin_client,
    email_outbox,
):
    owner_id, workspace_id, cookies = _register_and_login(
        admin_client,
        email_outbox,
        email="audit-owner@example.com",
    )
    _, other_workspace_id, _ = _register_and_login(
        admin_client,
        email_outbox,
        email="audit-other@example.com",
    )
    occurred_at = datetime.now(UTC) - timedelta(minutes=5)
    event_ids = [str(uuid.uuid4()) for _ in range(3)]

    factory = inspection_session_factory()
    with factory() as db:
        for event_id in event_ids:
            db.add(
                AuditEvent(
                    id=event_id,
                    workspace_id=workspace_id,
                    actor_user_id=owner_id,
                    event_type="meeting.read",
                    target_type="meeting",
                    target_id="meeting-1",
                    outcome="success",
                    request_id="request-safe-123",
                    ip_hash="hashed-ip-must-not-be-returned",
                    occurred_at=occurred_at,
                    details={
                        "safe": "visible",
                        "ip_address": "203.0.113.9",
                        "access_token": "raw-token",
                        "nested": {"authorization": "Bearer raw-token"},
                    },
                )
            )
        db.add(
            AuditEvent(
                workspace_id=workspace_id,
                actor_user_id=owner_id,
                event_type="meeting.read",
                target_type="meeting",
                target_id="meeting-2",
                outcome="failure",
                occurred_at=occurred_at - timedelta(minutes=1),
            )
        )
        db.add(
            AuditEvent(
                workspace_id=other_workspace_id,
                event_type="meeting.read",
                target_type="meeting",
                target_id="meeting-1",
                outcome="success",
                occurred_at=occurred_at + timedelta(minutes=1),
            )
        )
        db.commit()

    headers = {"X-Workspace-ID": workspace_id}
    first = admin_client.get(
        "/api/v1/admin/audit-events",
        params={
            "event_type": "meeting.read",
            "outcome": "success",
            "target_id": "meeting-1",
            "limit": 2,
        },
        cookies=cookies,
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"]
    assert all(item["event_type"] == "meeting.read" for item in first_body["items"])
    assert all("ip_hash" not in item for item in first_body["items"])
    assert first_body["items"][0]["details"] == {
        "safe": "visible",
        "ip_address": "[redacted]",
        "access_token": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }

    second = admin_client.get(
        "/api/v1/admin/audit-events",
        params={
            "event_type": "meeting.read",
            "outcome": "success",
            "target_id": "meeting-1",
            "limit": 2,
            "cursor": first_body["next_cursor"],
        },
        cookies=cookies,
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["next_cursor"] is None
    returned_ids = {item["id"] for item in first_body["items"] + second_body["items"]}
    assert returned_ids == set(event_ids)

    invalid_cursor = admin_client.get(
        "/api/v1/admin/audit-events",
        params={"cursor": "not-a-cursor"},
        cookies=cookies,
        headers=headers,
    )
    assert invalid_cursor.status_code == 400
    assert _error_code(invalid_cursor) == "invalid_cursor"


def test_only_owner_and_admin_can_access_workspace_admin_endpoints(
    admin_client,
    email_outbox,
):
    _, workspace_id, owner_cookies = _register_and_login(
        admin_client,
        email_outbox,
        email="role-owner@example.com",
    )
    admin_id, admin_workspace_id, admin_cookies = _register_and_login(
        admin_client,
        email_outbox,
        email="role-admin@example.com",
    )
    member_id, _, member_cookies = _register_and_login(
        admin_client,
        email_outbox,
        email="role-member@example.com",
    )
    _add_membership(workspace_id=workspace_id, user_id=admin_id, role=WorkspaceRole.ADMIN)
    _add_membership(workspace_id=workspace_id, user_id=member_id, role=WorkspaceRole.MEMBER)

    admin_read = admin_client.get(
        "/api/v1/admin/workspace-settings",
        cookies=admin_cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert admin_read.status_code == 200
    admin_write = admin_client.patch(
        "/api/v1/admin/workspace-settings",
        json={"retention_days": 60},
        cookies=admin_cookies,
        headers={
            "X-Workspace-ID": workspace_id,
            "X-CSRF-Token": admin_cookies[CSRF_COOKIE],
        },
    )
    assert admin_write.status_code == 200

    for path in (
        "/api/v1/admin/workspace-settings",
        "/api/v1/admin/audit-events",
    ):
        denied = admin_client.get(
            path,
            cookies=member_cookies,
            headers={"X-Workspace-ID": workspace_id},
        )
        assert denied.status_code == 403
        assert _error_code(denied) == "permission_denied"

    inaccessible = admin_client.get(
        "/api/v1/admin/workspace-settings",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": admin_workspace_id},
    )
    assert inaccessible.status_code == 403
    assert _error_code(inaccessible) == "workspace_access_denied"


def test_missing_workspace_uses_stable_not_found_error(
    admin_client,
    email_outbox,
    monkeypatch,
):
    _, workspace_id, cookies = _register_and_login(
        admin_client,
        email_outbox,
        email="missing-workspace-owner@example.com",
    )

    def missing_workspace(*_args, **_kwargs):
        raise WorkspaceNotFoundError("gone")

    monkeypatch.setattr(admin_router, "get_workspace_settings", missing_workspace)
    response = admin_client.get(
        "/api/v1/admin/workspace-settings",
        cookies=cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert response.status_code == 404
    assert _error_code(response) == "workspace_not_found"
