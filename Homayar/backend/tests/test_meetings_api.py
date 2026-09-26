from __future__ import annotations

import io

from sqlalchemy import select

from backend.database.saas_models import (
    Meeting,
    MeetingStatus,
    Membership,
    Recording,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


def _session_cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _workspace_for_user(db, user_id: str) -> Workspace:
    workspace = db.scalar(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at.asc())
    )
    assert workspace is not None
    return workspace


def test_meeting_reads_files_and_mutations_are_tenant_scoped(
    client,
    email_outbox,
    settings,
):
    owner_credentials = register_user(
        client,
        email_outbox,
        email="meeting-owner@example.com",
    )
    login_user(client, owner_credentials)
    owner_cookies = _session_cookies(client)

    outsider_credentials = register_user(
        client,
        email_outbox,
        email="meeting-outsider@example.com",
    )
    login_user(client, outsider_credentials)
    outsider_cookies = _session_cookies(client)

    factory = inspection_session_factory()
    with factory() as db:
        owner = db.scalar(select(User).where(User.email == owner_credentials["email"]))
        outsider = db.scalar(select(User).where(User.email == outsider_credentials["email"]))
        assert owner is not None and outsider is not None
        owner_workspace = _workspace_for_user(db, owner.id)
        outsider_workspace = _workspace_for_user(db, outsider.id)

        stored = create_object_storage(settings).put_stream(
            owner_workspace.id,
            io.BytesIO(b"private-recording"),
            content_type="audio/wav",
            original_filename="private.wav",
        )
        meeting = Meeting(
            workspace_id=owner_workspace.id,
            created_by_user_id=owner.id,
            title="Owner private call",
            language="fa",
            status=MeetingStatus.COMPLETED,
            duration_seconds=60,
        )
        db.add(meeting)
        db.flush()
        db.add(
            Recording(
                workspace_id=owner_workspace.id,
                meeting_id=meeting.id,
                storage_key=stored.key,
                original_filename="private.wav",
                media_type="audio/wav",
                container_format="wav",
                size_bytes=stored.size_bytes,
                checksum_sha256=stored.sha256,
                duration_seconds=60,
                channel_count=1,
                sample_rate_hz=16000,
            )
        )
        db.add(
            Membership(
                workspace_id=owner_workspace.id,
                user_id=outsider.id,
                role=WorkspaceRole.VIEWER,
            )
        )
        db.commit()
        meeting_id = meeting.id
        owner_workspace_id = owner_workspace.id
        outsider_workspace_id = outsider_workspace.id

    own_detail = client.get(
        f"/api/v1/meetings/{meeting_id}",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert own_detail.status_code == 200
    assert own_detail.json()["recording"]["original_filename"] == "private.wav"

    outsider_detail = client.get(
        f"/api/v1/meetings/{meeting_id}",
        cookies=outsider_cookies,
        headers={"X-Workspace-ID": outsider_workspace_id},
    )
    assert outsider_detail.status_code == 404
    outsider_file = client.get(
        f"/api/v1/meetings/{meeting_id}/recording",
        cookies=outsider_cookies,
        headers={"X-Workspace-ID": outsider_workspace_id},
    )
    assert outsider_file.status_code == 404

    viewer_detail = client.get(
        f"/api/v1/meetings/{meeting_id}",
        cookies=outsider_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert viewer_detail.status_code == 200
    viewer_update = client.patch(
        f"/api/v1/meetings/{meeting_id}",
        json={"title": "Forbidden rename"},
        cookies=outsider_cookies,
        headers={
            "X-Workspace-ID": owner_workspace_id,
            "X-CSRF-Token": outsider_cookies[CSRF_COOKIE],
        },
    )
    assert viewer_update.status_code == 403

    owner_file = client.get(
        f"/api/v1/meetings/{meeting_id}/recording",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert owner_file.status_code == 200
    assert owner_file.content == b"private-recording"
    assert "attachment" in owner_file.headers["content-disposition"]

    owner_delete = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        cookies=owner_cookies,
        headers={
            "X-Workspace-ID": owner_workspace_id,
            "X-CSRF-Token": owner_cookies[CSRF_COOKIE],
        },
    )
    assert owner_delete.status_code == 204
    assert not create_object_storage(settings).exists(owner_workspace_id, stored.key)
    deleted_detail = client.get(
        f"/api/v1/meetings/{meeting_id}",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": owner_workspace_id},
    )
    assert deleted_detail.status_code == 404
