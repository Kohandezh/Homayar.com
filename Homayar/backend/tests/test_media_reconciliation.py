"""WP05-T04: media reconciliation sweep.

Retry-safe finalize of pending deletions, orphan detection (report-only by
default, removal when enabled), missing-byte detection that never fabricates
or removes data, and bounded tenant-scoped operation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from sqlalchemy import select

from backend.database.saas_models import (
    MediaIntegrity,
    MediaLifecycleState,
    MediaObject,
    Recording,
    StageCheckpoint,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.storage import (
    LocalObjectStorage,
    StorageError,
    create_object_storage,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    _audits,
    _headers,
    _patch_media,
    _tenant,
    _upload,
    _widen_concurrency,
)


def _media_rows(meeting_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(MediaObject)
                .where(MediaObject.meeting_id == meeting_id)
                .order_by(MediaObject.created_at.asc())
            )
        )


# ------------------------------------------------------------------
# 1. Pending deletions are retried and the recording is retired
# ------------------------------------------------------------------


def test_pending_deletions_are_retried_and_the_recording_is_retired(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-pending")
    storage = create_object_storage(settings)

    accepted = _upload(client, tenant, key="recon-pending-1")
    assert accepted.status_code == 202
    meeting_id = accepted.json()["meeting"]["id"]

    # Make the storage delete fail once
    _original_delete = LocalObjectStorage.delete
    _calls = {"n": 0}

    def _failing_delete(self, tenant_id, key):
        _calls["n"] += 1
        if _calls["n"] <= 1:
            raise StorageError("simulated", code="test_failure")
        return _original_delete(self, tenant_id, key)

    monkeypatch.setattr(LocalObjectStorage, "delete", _failing_delete)

    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert deleted.status_code == 204

    media = _media_rows(meeting_id)[0]
    assert media.lifecycle_state is MediaLifecycleState.DELETION_PENDING
    assert storage.exists(tenant["workspace_id"], media.storage_key)

    # Restore so the sweep can succeed
    monkeypatch.setattr(LocalObjectStorage, "delete", _original_delete)

    now = datetime.now(UTC) + timedelta(hours=2)
    counts = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )

    assert counts["pending_closed"] == 1
    media = _media_rows(meeting_id)[0]
    assert media.lifecycle_state is MediaLifecycleState.DELETED
    assert not storage.exists(tenant["workspace_id"], media.storage_key)

    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None
        assert recording.deleted_at is not None

    deleted_audits = _audits("media.deleted")
    assert any(a.target_id == media.id for a in deleted_audits)


# ------------------------------------------------------------------
# 2. Orphans are reported by default and removed when enabled
# ------------------------------------------------------------------


def test_orphan_objects_are_reported_by_default_and_removed_when_enabled(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-orphan")
    storage = create_object_storage(settings)

    # Write an orphan object directly (no DB row)
    orphan = storage.put_stream(
        tenant["workspace_id"],
        BytesIO(b"orphan-bytes-for-reconciliation"),
        content_type="audio/wav",
    )

    now = datetime.now(UTC) + timedelta(hours=2)

    # --- first sweep: report-only (default) ---
    counts = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts["orphans_found"] == 1
    assert storage.exists(tenant["workspace_id"], orphan.key)

    reported = _audits("media.orphans_reported")
    assert len(reported) == 1
    assert reported[0].details["count"] == 1
    assert "key" not in reported[0].details

    # --- second sweep: removal enabled ---
    monkeypatch.setattr(settings, "media_reconciliation_remove_orphans", True)
    counts2 = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts2["orphans_found"] == 1
    assert counts2["orphans_removed"] == 1
    assert not storage.exists(tenant["workspace_id"], orphan.key)

    removed = _audits("media.orphan_removed")
    assert len(removed) == 1
    assert removed[0].details["size_bytes"] == orphan.size_bytes
    assert "key" not in removed[0].details

    # --- third sweep: fresh object never touched ---
    fresh = storage.put_stream(
        tenant["workspace_id"],
        BytesIO(b"fresh-orphan-bytes"),
        content_type="audio/wav",
    )
    counts3 = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=datetime.now(UTC),
    )
    assert counts3["orphans_found"] == 0
    assert storage.exists(tenant["workspace_id"], fresh.key)


# ------------------------------------------------------------------
# 3. Live objects and checkpoint artifacts are never orphans
# ------------------------------------------------------------------


def test_live_objects_and_checkpoint_artifacts_are_never_orphans(
    client, email_outbox, monkeypatch, settings
):
    from backend.jobs.processor import process_job
    from backend.tests.test_job_processor import _providers

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-live")
    storage = create_object_storage(settings)

    accepted = _upload(client, tenant, key="recon-live-1")
    assert accepted.status_code == 202
    job_id = accepted.json()["job"]["id"]
    result = process_job(
        job_id,
        worker_id="recon-worker",
        settings=settings,
        providers=_providers(),
    )
    assert result["status"] == "completed"

    _widen_concurrency(tenant["workspace_id"])
    accepted2 = _upload(client, tenant, key="recon-live-2")
    assert accepted2.status_code == 202

    monkeypatch.setattr(settings, "media_reconciliation_remove_orphans", True)
    now = datetime.now(UTC) + timedelta(hours=2)
    counts = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts["orphans_found"] == 0

    # The completed job left checkpoint artifacts behind; they are live
    # keys and must still be in storage after the sweep.
    with inspection_session_factory()() as db:
        artifact_keys = list(
            db.scalars(
                select(StageCheckpoint.artifact_key).where(
                    StageCheckpoint.workspace_id == tenant["workspace_id"],
                    StageCheckpoint.object_deleted_at.is_(None),
                )
            )
        )
    assert artifact_keys
    for key in artifact_keys:
        assert storage.exists(tenant["workspace_id"], key)

    for mid in (
        accepted.json()["meeting"]["id"],
        accepted2.json()["meeting"]["id"],
    ):
        for m in _media_rows(mid):
            if m.lifecycle_state is not MediaLifecycleState.DELETED:
                assert storage.exists(tenant["workspace_id"], m.storage_key)


# ------------------------------------------------------------------
# 4. Missing objects are flagged, not fabricated
# ------------------------------------------------------------------


def test_missing_objects_are_flagged_not_fabricated(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-missing")
    storage = create_object_storage(settings)

    accepted = _upload(client, tenant, key="recon-missing-1")
    assert accepted.status_code == 202
    meeting_id = accepted.json()["meeting"]["id"]
    media = _media_rows(meeting_id)[0]

    # Remove the bytes directly
    storage.delete(tenant["workspace_id"], media.storage_key)

    now = datetime.now(UTC) + timedelta(hours=2)
    counts = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts["missing_flagged"] == 1

    media = _media_rows(meeting_id)[0]
    assert media.lifecycle_state is MediaLifecycleState.ACTIVE
    assert media.integrity is MediaIntegrity.MISSING
    assert media.last_verified_at is not None

    checks = _audits("media.integrity_checked")
    assert checks[-1].outcome == "failed"

    # Second sweep: already MISSING, not audited again
    audit_count_before = len(_audits("media.integrity_checked"))
    counts2 = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts2["missing_flagged"] == 0
    assert len(_audits("media.integrity_checked")) == audit_count_before


# ------------------------------------------------------------------
# 5. The sweep is bounded and tenant-scoped
# ------------------------------------------------------------------


def test_the_sweep_is_bounded_and_tenant_scoped(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant_a = _tenant(client, email_outbox, "recon-scope-a")
    tenant_b = _tenant(client, email_outbox, "recon-scope-b")
    storage = create_object_storage(settings)

    # Each tenant gets an orphan
    orphan_a = storage.put_stream(
        tenant_a["workspace_id"],
        BytesIO(b"orphan-a"),
        content_type="audio/wav",
    )
    orphan_b = storage.put_stream(
        tenant_b["workspace_id"],
        BytesIO(b"orphan-b"),
        content_type="audio/wav",
    )

    # Each tenant gets a pending deletion
    _original_delete = LocalObjectStorage.delete
    _block = {"active": True}

    def _blocking_delete(self, tid, key):
        if _block["active"]:
            raise StorageError("blocked", code="test_block")
        return _original_delete(self, tid, key)

    monkeypatch.setattr(LocalObjectStorage, "delete", _blocking_delete)

    acc_a = _upload(client, tenant_a, key="scope-a-1")
    assert acc_a.status_code == 202
    mid_a = acc_a.json()["meeting"]["id"]
    client.delete(
        f"/api/v1/meetings/{mid_a}",
        headers=_headers(tenant_a),
        cookies=tenant_a["cookies"],
    )

    acc_b = _upload(client, tenant_b, key="scope-b-1")
    assert acc_b.status_code == 202
    mid_b = acc_b.json()["meeting"]["id"]
    client.delete(
        f"/api/v1/meetings/{mid_b}",
        headers=_headers(tenant_b),
        cookies=tenant_b["cookies"],
    )

    monkeypatch.setattr(LocalObjectStorage, "delete", _original_delete)
    _block["active"] = False

    assert _media_rows(mid_a)[0].lifecycle_state is MediaLifecycleState.DELETION_PENDING
    assert _media_rows(mid_b)[0].lifecycle_state is MediaLifecycleState.DELETION_PENDING

    monkeypatch.setattr(settings, "media_reconciliation_remove_orphans", True)
    now = datetime.now(UTC) + timedelta(hours=2)

    # limit=1 on A: only one unit of work, and once the pending retry has
    # used the budget the tenant prefix is not listed at all.
    class _CountingStorage(LocalObjectStorage):
        listings = 0

        def iter_objects(self, tenant_id):
            type(self).listings += 1
            return super().iter_objects(tenant_id)

    counting_storage = _CountingStorage(
        Path(settings.storage_local_root),
        max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )
    counts_lim = reconcile_media_objects(
        settings,
        storage=counting_storage,
        workspace_ids=[tenant_a["workspace_id"]],
        now=now,
        limit=1,
    )
    assert counts_lim["pending_closed"] == 1
    assert _CountingStorage.listings == 0
    work = (
        counts_lim["pending_closed"]
        + counts_lim["pending_failed"]
        + counts_lim["orphans_found"]
        + counts_lim["missing_flagged"]
    )
    assert work == 1

    # Full sweep of A finishes the rest
    reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant_a["workspace_id"]],
        now=now,
    )

    # A is fully reconciled; B's orphan and pending row are untouched
    assert not storage.exists(tenant_a["workspace_id"], orphan_a.key)
    assert _media_rows(mid_a)[0].lifecycle_state is MediaLifecycleState.DELETED
    assert storage.exists(tenant_b["workspace_id"], orphan_b.key)
    assert _media_rows(mid_b)[0].lifecycle_state is MediaLifecycleState.DELETION_PENDING


# ------------------------------------------------------------------
# 6. A backend read failure while verifying skips only that row
# ------------------------------------------------------------------


def test_a_backend_read_failure_during_verification_skips_the_row_and_continues(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-verify-fail")
    storage = create_object_storage(settings)

    accepted = _upload(client, tenant, key="recon-verify-fail-1")
    assert accepted.status_code == 202
    meeting_id = accepted.json()["meeting"]["id"]
    media = _media_rows(meeting_id)[0]

    # Bytes are absent from the listing, but the backend cannot answer the
    # read either (a transient failure, not "object absent").
    storage.delete(tenant["workspace_id"], media.storage_key)

    class _UnreadableStorage(LocalObjectStorage):
        def open_reader(self, tenant_id, key):
            raise StorageError("simulated backend failure", code="s3_read_failed")

    unreadable = _UnreadableStorage(
        Path(settings.storage_local_root),
        max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )
    now = datetime.now(UTC) + timedelta(hours=2)
    counts = reconcile_media_objects(
        settings,
        storage=unreadable,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts["missing_flagged"] == 0
    assert counts["unavailable"] == 1

    # Row untouched: still ACTIVE and not flagged; no integrity audit written
    media = _media_rows(meeting_id)[0]
    assert media.lifecycle_state is MediaLifecycleState.ACTIVE
    assert media.integrity is MediaIntegrity.UNVERIFIED
    assert media.last_verified_at is None
    assert _audits("media.integrity_checked") == []

    # Once the backend answers, the same row is flagged MISSING
    counts2 = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )
    assert counts2["missing_flagged"] == 1
    assert _media_rows(meeting_id)[0].integrity is MediaIntegrity.MISSING


# ------------------------------------------------------------------
# 7. Listing failure skips the orphan and missing steps
# ------------------------------------------------------------------


def test_listing_failure_skips_orphan_and_missing_steps_after_the_pending_retry(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-fail")
    storage = create_object_storage(settings)

    # Orphan object
    orphan = storage.put_stream(
        tenant["workspace_id"],
        BytesIO(b"orphan-for-failure-test"),
        content_type="audio/wav",
    )

    # Pending deletion
    _original_delete = LocalObjectStorage.delete
    _block = {"active": True}

    def _blocking_delete(self, tid, key):
        if _block["active"]:
            raise StorageError("blocked", code="test_block")
        return _original_delete(self, tid, key)

    monkeypatch.setattr(LocalObjectStorage, "delete", _blocking_delete)

    accepted = _upload(client, tenant, key="recon-fail-1")
    assert accepted.status_code == 202
    meeting_id = accepted.json()["meeting"]["id"]
    client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )

    monkeypatch.setattr(LocalObjectStorage, "delete", _original_delete)
    _block["active"] = False

    assert _media_rows(meeting_id)[0].lifecycle_state is MediaLifecycleState.DELETION_PENDING

    # Storage double with failing iter_objects
    class _FailingListingStorage(LocalObjectStorage):
        def iter_objects(self, tenant_id):
            raise StorageError("boom", code="s3_list_failed")

    failing_storage = _FailingListingStorage(
        Path(settings.storage_local_root),
        max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )

    monkeypatch.setattr(settings, "media_reconciliation_remove_orphans", True)
    now = datetime.now(UTC) + timedelta(hours=2)
    counts = reconcile_media_objects(
        settings,
        storage=failing_storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )

    assert counts["unavailable"] == 1

    # Orphan still present (listing failed, removal never ran)
    assert storage.exists(tenant["workspace_id"], orphan.key)

    # Pending row was still closed (step a before listing)
    assert counts["pending_closed"] == 1
    assert _media_rows(meeting_id)[0].lifecycle_state is MediaLifecycleState.DELETED


# ------------------------------------------------------------------
# 7. Invariants hold after reconciliation
# ------------------------------------------------------------------


def test_invariants_hold_after_reconciliation(client, email_outbox, monkeypatch, settings):
    from backend.jobs.invariants import check_workspace

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "recon-inv")
    storage = create_object_storage(settings)

    _original_delete = LocalObjectStorage.delete
    _calls = {"n": 0}

    def _failing_delete(self, tid, key):
        _calls["n"] += 1
        if _calls["n"] <= 1:
            raise StorageError("simulated", code="test_failure")
        return _original_delete(self, tid, key)

    monkeypatch.setattr(LocalObjectStorage, "delete", _failing_delete)

    accepted = _upload(client, tenant, key="recon-inv-1")
    assert accepted.status_code == 202
    meeting_id = accepted.json()["meeting"]["id"]

    client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )

    monkeypatch.setattr(LocalObjectStorage, "delete", _original_delete)

    now = datetime.now(UTC) + timedelta(hours=2)
    reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=now,
    )

    with inspection_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        violations = check_workspace(
            db,
            tenant["workspace_id"],
            settings=settings,
            storage=storage,
        )
        db.rollback()

    assert violations == []
