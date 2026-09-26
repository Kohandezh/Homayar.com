"""WP07-T01 (ADR 0020): explicit retention semantics and the legacy zero.

The workspace row is the only retention authority.  ``finite`` needs a
positive number of days, ``indefinite``/``immediate`` carry no number, the
migrated ambiguous ``retention_days = 0`` becomes ``legacy_unresolved`` and
is never destructive, and a client can neither choose that value nor send a
bare ``0`` again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from backend.database.saas_models import AuditEvent, Meeting, RetentionMode, Workspace
from backend.services.retention_policy import (
    InvalidRetentionPolicy,
    is_age_based_deletion_enabled,
    retention_expiry_for,
    validate_retention_policy,
)
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    REPO_ROOT,
    _get,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)

probe_role = _rls.probe_role

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _workspace(mode: RetentionMode, days: int) -> Workspace:
    return Workspace(
        organization_id="org",
        name="w",
        slug="w",
        retention_mode=mode,
        retention_days=days,
    )


def test_expiry_and_enforcement_follow_the_mode() -> None:
    finite = _workspace(RetentionMode.FINITE, 30)
    assert retention_expiry_for(finite, now=NOW) == NOW + timedelta(days=30)
    assert is_age_based_deletion_enabled(finite)

    immediate = _workspace(RetentionMode.IMMEDIATE, 0)
    assert retention_expiry_for(immediate, now=NOW) == NOW
    assert is_age_based_deletion_enabled(immediate)

    for mode in (RetentionMode.INDEFINITE, RetentionMode.LEGACY_UNRESOLVED):
        workspace = _workspace(mode, 0)
        assert retention_expiry_for(workspace, now=NOW) is None
        assert not is_age_based_deletion_enabled(workspace)


@pytest.mark.parametrize(
    ("mode", "days", "ok"),
    [
        (RetentionMode.FINITE, 1, True),
        (RetentionMode.FINITE, 3650, True),
        (RetentionMode.FINITE, 0, False),
        (RetentionMode.FINITE, 3651, False),
        (RetentionMode.INDEFINITE, 0, True),
        (RetentionMode.INDEFINITE, 7, False),
        (RetentionMode.IMMEDIATE, 0, True),
        (RetentionMode.IMMEDIATE, 1, False),
        (RetentionMode.LEGACY_UNRESOLVED, 0, True),
        (RetentionMode.LEGACY_UNRESOLVED, 1, False),
    ],
)
def test_policy_pairs_are_validated(mode: RetentionMode, days: int, ok: bool) -> None:
    if ok:
        validate_retention_policy(mode, days)
    else:
        with pytest.raises(InvalidRetentionPolicy):
            validate_retention_policy(mode, days)
    with pytest.raises(InvalidRetentionPolicy):
        validate_retention_policy(mode, True)  # type: ignore[arg-type]  # bool is not a count


def _patch(client, tenant: dict, payload: dict):
    return client.patch(
        "/api/v1/admin/workspace-settings",
        json=payload,
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )


def _policy(workspace_id: str) -> tuple[RetentionMode, int]:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        assert workspace is not None
        return workspace.retention_mode, workspace.retention_days


def _expiry(meeting_id: str) -> datetime | None:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        value = meeting.retention_expires_at
        return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def test_admin_changes_the_policy_explicitly_and_uploads_follow_it(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "retention-modes")
    workspace_id = tenant["workspace_id"]
    assert _policy(workspace_id) == (RetentionMode.FINITE, 90)

    # A bare zero is ambiguous and refused; the legacy sentinel cannot be chosen.
    zero = _patch(client, tenant, {"retention_days": 0})
    assert zero.status_code == 422, zero.text
    assert zero.json()["error"]["code"] == "invalid_workspace_settings"
    assert "ambiguous" in zero.json()["error"]["message"]
    legacy = _patch(client, tenant, {"retention_mode": "legacy_unresolved"})
    assert legacy.status_code == 422 and "legacy" in legacy.json()["error"]["message"]
    assert _policy(workspace_id) == (RetentionMode.FINITE, 90)

    # Finite: a positive number is enough while the mode is finite.
    finite = _patch(client, tenant, {"retention_days": 30})
    assert finite.status_code == 200, finite.text
    assert (finite.json()["retention_mode"], finite.json()["retention_days"]) == ("finite", 30)
    before = datetime.now(UTC)
    accepted = _upload(client, tenant, key="retention-finite-0001")
    assert accepted.status_code == 202, accepted.text
    expiry = _expiry(accepted.json()["meeting"]["id"])
    assert expiry is not None
    assert timedelta(days=30) <= expiry - before < timedelta(days=30, minutes=5)

    # Indefinite: the days are zeroed and a conversation carries no expiry.
    indefinite = _patch(client, tenant, {"retention_mode": "indefinite"})
    assert indefinite.status_code == 200, indefinite.text
    assert (indefinite.json()["retention_mode"], indefinite.json()["retention_days"]) == (
        "indefinite",
        0,
    )
    days_under_indefinite = _patch(client, tenant, {"retention_days": 45})
    assert days_under_indefinite.status_code == 422  # the mode must be sent as well
    assert _policy(workspace_id) == (RetentionMode.INDEFINITE, 0)
    forever = _upload(client, tenant, key="retention-indefinite-0001", content=b"second bytes")
    assert forever.status_code == 202, forever.text
    assert _expiry(forever.json()["meeting"]["id"]) is None

    # Immediate: eligible from ingestion (the sweep of T03 waits for a terminal job).
    immediate = _patch(client, tenant, {"retention_mode": "immediate"})
    assert immediate.status_code == 200 and immediate.json()["retention_days"] == 0
    with_days = _patch(client, tenant, {"retention_mode": "immediate", "retention_days": 3})
    assert with_days.status_code == 422
    before = datetime.now(UTC)
    now_row = _upload(client, tenant, key="retention-immediate-0001", content=b"third bytes")
    assert now_row.status_code == 202, now_row.text
    expiry = _expiry(now_row.json()["meeting"]["id"])
    assert expiry is not None and before - timedelta(seconds=5) <= expiry <= datetime.now(UTC)

    # Back to finite needs a number when the stored days are 0.
    back_without_days = _patch(client, tenant, {"retention_mode": "finite"})
    assert back_without_days.status_code == 422
    back = _patch(client, tenant, {"retention_mode": "finite", "retention_days": 7})
    assert back.status_code == 200 and back.json()["retention_days"] == 7

    # Every change is audited with the field names only.
    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.event_type == "workspace.settings.updated",
            )
            .order_by(AuditEvent.occurred_at.asc())
        ).all()
    fields = [event.details["fields"] for event in events]
    assert fields == [
        "retention_days",
        "retention_mode",
        "retention_mode",
        "retention_days,retention_mode",
    ]
    assert all("0" not in event.details and "legacy" not in str(event.details) for event in events)
    listing = _get(client, tenant, "/api/v1/admin/workspace-settings")
    assert listing.status_code == 200 and listing.json()["retention_mode"] == "finite"


def test_legacy_zero_is_never_destructive_until_resolved(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "retention-legacy")
    workspace_id = tenant["workspace_id"]
    # What the migration leaves behind for a pre-WP07 workspace with retention_days = 0.
    with inspection_session_factory()() as db:
        db.execute(
            text(
                "UPDATE workspaces SET retention_days = 0, retention_mode = 'LEGACY_UNRESOLVED' "
                "WHERE id = :id"
            ),
            {"id": workspace_id},
        )
        db.commit()
    settings = _get(client, tenant, "/api/v1/admin/workspace-settings")
    assert settings.json()["retention_mode"] == "legacy_unresolved"
    assert settings.json()["retention_days"] == 0

    # No age-based expiry is stamped while the value is unresolved.
    kept = _upload(client, tenant, key="retention-legacy-0001")
    assert kept.status_code == 202, kept.text
    assert _expiry(kept.json()["meeting"]["id"]) is None
    with inspection_session_factory()() as db:
        assert not is_age_based_deletion_enabled(db.get(Workspace, workspace_id))

    # A positive number resolves it explicitly to finite; the sentinel never returns.
    resolved = _patch(client, tenant, {"retention_days": 14})
    assert resolved.status_code == 200, resolved.text
    assert (resolved.json()["retention_mode"], resolved.json()["retention_days"]) == ("finite", 14)
    assert _policy(workspace_id) == (RetentionMode.FINITE, 14)


def test_member_without_workspace_manage_cannot_change_retention(client, email_outbox, monkeypatch):
    from backend.tests.test_authorization_matrix import _workspace_with_roles

    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    response = personas["member"].patch(
        "/api/v1/admin/workspace-settings", json={"retention_mode": "immediate"}
    )
    assert response.status_code == 403, response.text
    assert _policy(owner.workspace_id) == (RetentionMode.FINITE, 90)


@pytest.mark.postgres
def test_migration_marks_the_ambiguous_zero_unresolved_and_downgrade_is_lossless(
    client, email_outbox, monkeypatch, settings
):
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache

    _patch_media(monkeypatch)
    finite = _tenant(client, email_outbox, "retention-mig-finite")
    zero = _tenant(client, email_outbox, "retention-mig-zero")

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    reset_settings_cache()
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        command.stamp(config, "head")
        command.downgrade(config, "b9d5f3a7c2e8")
        with inspection_session_factory()() as db:
            columns = (
                db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'workspaces'"
                    )
                )
                .scalars()
                .all()
            )
            assert "retention_mode" not in columns and "retention_days" in columns
            # The pre-WP07 ambiguous value, as a legacy deployment would hold it.
            db.execute(
                text("UPDATE workspaces SET retention_days = 0 WHERE id = :id"),
                {"id": zero["workspace_id"]},
            )
            db.commit()
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()

    assert _policy(finite["workspace_id"]) == (RetentionMode.FINITE, 90)
    assert _policy(zero["workspace_id"]) == (RetentionMode.LEGACY_UNRESOLVED, 0)
    with inspection_session_factory()() as db:
        with pytest.raises(Exception, match="ck_workspace_retention_mode"):
            db.execute(
                text("UPDATE workspaces SET retention_days = 0 WHERE id = :id"),
                {"id": finite["workspace_id"]},
            )
            db.commit()
