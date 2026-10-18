"""WP07-T04: tombstones survive a backup restore (ADR 0022).

Every tombstone is journaled outside the database and object-store backups;
after a restore, ``reapply_tombstones`` re-tombstones conversations that came
back alive, reopens their purge (content rows and bytes restored by the
backups are removed again) and ``verify_tombstones`` gates traffic.  Holds in
the restored database are respected; the journal carries no content.
"""

from __future__ import annotations

import io
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from backend.database.saas_models import (
    CheckpointState,
    ConversationPurge,
    JobState,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    PurgeState,
    Recording,
    ShareLink,
    StageCheckpoint,
)
from backend.jobs.retention_purge import run_retention_purge
from backend.jobs.tombstone_reapply import reapply_tombstones, verify_tombstones
from backend.services.tombstone_journal import (
    TombstoneJournal,
    TombstoneRecord,
    decode,
    encode,
)
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    _audits,
    _get,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)
from backend.tests.test_retention_purge import (
    _checkpoint_keys,
    _content_rows,
    _media,
    _processed,
    _purge,
    _review,
)


@pytest.fixture
def settings(settings, tmp_path):
    """The module's deployments journal their tombstones."""
    return settings.model_copy(update={"tombstone_journal_dir": str(tmp_path / "journal")})


def _journal(settings) -> TombstoneJournal:
    return TombstoneJournal(settings.tombstone_journal_dir)


def _delete(client, tenant: dict, meeting_id: str) -> None:
    response = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert response.status_code == 204, response.text


def _objects(settings, meeting_id: str) -> list[str]:
    return [m.storage_key for m in _media(meeting_id)] + [
        k for k, _ in _checkpoint_keys(meeting_id)
    ]


def _snapshot_objects(settings, target: Path) -> None:
    shutil.copytree(settings.storage_local_root, target)


def _restore_objects(settings, source: Path) -> None:
    shutil.copytree(source, settings.storage_local_root, dirs_exist_ok=True)


def _simulate_database_restore_before(meeting_id: str) -> None:
    """The restored database predates the tombstone (PostgreSQL-safe variant):
    the conversation is alive, its media live, shares valid, no purge row."""
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(deleted_at=None))
        db.execute(
            update(Recording).where(Recording.meeting_id == meeting_id).values(deleted_at=None)
        )
        db.execute(
            update(MediaObject)
            .where(MediaObject.meeting_id == meeting_id)
            .values(
                lifecycle_state=MediaLifecycleState.ACTIVE,
                deletion_requested_at=None,
                deletion_reason=None,
            )
        )
        db.execute(
            update(ShareLink).where(ShareLink.meeting_id == meeting_id).values(revoked_at=None)
        )
        db.execute(
            update(StageCheckpoint)
            .where(StageCheckpoint.meeting_id == meeting_id)
            .values(state=CheckpointState.PUBLISHED, invalidated_at=None, invalidation_reason=None)
        )
        purge = db.scalar(
            select(ConversationPurge).where(ConversationPurge.meeting_id == meeting_id)
        )
        if purge is not None:
            db.delete(purge)
        db.commit()


# --- the journal itself ------------------------------------------------------


def test_journal_lines_are_content_free_verified_and_deduplicated(tmp_path):
    journal = TombstoneJournal(tmp_path / "j")
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    record = TombstoneRecord(
        purge_id="p1",
        workspace_id="w1",
        meeting_id="m1",
        reason="meeting_deleted",
        tombstoned_at=now,
        object_keys=("tenants/w1/objects/" + "a" * 32,),
    )
    assert decode(encode(record)) == record
    journal.append([record, record], now=now)
    journal.append(
        [TombstoneRecord("p2", "w1", "m2", "retention_expired", now, ())],
        now=now + timedelta(days=1),
    )
    files = journal.files()
    assert [p.name for p in files] == ["tombstones-20260923.jsonl", "tombstones-20260924.jsonl"]
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"
    # An edited line and a torn tail are rejected, never trusted.
    with files[0].open("a") as handle:
        handle.write(encode(record).replace('"m1"', '"m9"'))
        handle.write('{"v": 1, "purge_id": "p3"')
    records, rejected = journal.read()
    assert sorted(r.meeting_id for r in records) == ["m1", "m2"]
    assert rejected == 2


def test_deletion_is_journaled_at_once_without_content(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "journal-inline")
    meeting_id = _processed(client, tenant, settings, key="journal-inline-01")
    keys = _objects(settings, meeting_id)
    _delete(client, tenant, meeting_id)
    records, rejected = _journal(settings).read()
    assert rejected == 0 and [r.meeting_id for r in records] == [meeting_id]
    assert records[0].workspace_id == tenant["workspace_id"]
    assert records[0].reason == "meeting_deleted"
    assert set(records[0].object_keys) == set(keys)
    raw = "".join(p.read_text() for p in _journal(settings).files())
    assert "Weekly customer call" not in raw and "customer-call" not in raw
    assert "قرارداد" not in raw
    assert _purge(meeting_id).journaled_at is not None


def test_an_unwritable_journal_never_blocks_deletion_and_the_sweep_catches_up(
    client, email_outbox, monkeypatch, settings, tmp_path, caplog
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "journal-late")
    accepted = _upload(client, tenant, key="journal-late-0001")
    meeting_id = accepted.json()["meeting"]["id"]
    blocker = Path(settings.tombstone_journal_dir)
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory")  # makes every append fail
    _delete(client, tenant, meeting_id)
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 404
    assert _purge(meeting_id).journaled_at is None
    backlog = run_retention_purge(
        settings, now=datetime.now(UTC), workspace_ids=[tenant["workspace_id"]]
    )
    assert backlog["journal_backlog"] == 1 and backlog["journaled"] == 0
    logged = [
        r.getMessage() for r in caplog.records if "TOMBSTONE_JOURNAL_UNAVAILABLE" in r.getMessage()
    ]
    # A bounded reason and the error class only: never the path or the OS error text.
    assert logged and all("reason=write_failed cause=" in line for line in logged)
    assert not any(str(blocker) in line or "Errno" in line for line in logged)
    blocker.unlink()
    caught_up = run_retention_purge(
        settings, now=datetime.now(UTC), workspace_ids=[tenant["workspace_id"]]
    )
    assert caught_up["journaled"] == 1 and caught_up["journal_backlog"] == 0
    assert [r.meeting_id for r in _journal(settings).read()[0]] == [meeting_id]


# --- restores ----------------------------------------------------------------


def test_real_sqlite_backup_restore_is_undone_by_reapply_before_traffic(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        pytest.skip(
            "file-level backup/restore drill runs on SQLite; PostgreSQL uses the simulated restore"
        )
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "restore-sqlite")
    workspace_id = tenant["workspace_id"]
    doomed = _processed(client, tenant, settings, key="restore-sqlite-01")
    kept = _processed(client, tenant, settings, key="restore-sqlite-02", index=1)
    kept_rows = _content_rows(kept)
    keys = _objects(settings, doomed)

    # Backups of both authoritative stores, taken while the conversation lived.
    backup_db = tmp_path / "backup.db"
    with sqlite3.connect(url.database) as live, sqlite3.connect(backup_db) as copy:
        live.backup(copy)
    _snapshot_objects(settings, tmp_path / "objects-backup")

    _delete(client, tenant, doomed)
    storage = create_object_storage(settings)
    assert _content_rows(doomed) == 0 and not any(storage.exists(workspace_id, k) for k in keys)

    # Disaster: restore both backups.  The deleted conversation is back.
    with sqlite3.connect(backup_db) as copy, sqlite3.connect(url.database) as live:
        copy.backup(live)
    _restore_objects(settings, tmp_path / "objects-backup")
    assert _get(client, tenant, f"/api/v1/meetings/{doomed}").status_code == 200
    assert _content_rows(doomed) > 0 and all(storage.exists(workspace_id, k) for k in keys)
    assert verify_tombstones(settings)["alive"] == 1

    counts = reapply_tombstones(settings)
    assert counts["reapplied"] == 1 and counts["purges_completed"] == 1, counts
    assert verify_tombstones(settings) == {
        "records": 1,
        "rejected_lines": 0,
        "alive": 0,
        "purges_open": 0,
        "purges_failed": 0,
    }
    assert _get(client, tenant, f"/api/v1/meetings/{doomed}").status_code == 404
    assert _content_rows(doomed) == 0
    assert not any(storage.exists(workspace_id, k) for k in keys)
    assert _get(client, tenant, f"/api/v1/meetings/{kept}").status_code == 200
    assert _content_rows(kept) == kept_rows


def test_simulated_restore_before_the_tombstone_is_reapplied(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "restore-sim")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key="restore-sim-0001")
    keys = _objects(settings, meeting_id)
    # Tombstone only (the purge has not run when the "backup" is restored).
    monkeypatch.setattr("backend.routers.v1.meetings.run_purge_attempt", lambda *_a, **_k: None)
    _delete(client, tenant, meeting_id)
    _simulate_database_restore_before(meeting_id)
    with inspection_session_factory()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.meeting_id == meeting_id))
        job.state = JobState.QUEUED  # a restored backup can hold unfinished work
        db.commit()
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 200

    counts = reapply_tombstones(settings)
    assert counts["reapplied"] == 1 and counts["jobs_cancelled"] == 1, counts
    assert counts["purges_completed"] == 1
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 404
    assert _content_rows(meeting_id) == 0
    storage = create_object_storage(settings)
    assert not any(storage.exists(workspace_id, k) for k in keys)
    purge = _purge(meeting_id)
    assert purge.reason == "meeting_deleted" and purge.reapplied_at is not None
    with inspection_session_factory()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.meeting_id == meeting_id))
        assert job.state is JobState.CANCELLED
    reapplied = [e for e in _audits("meeting.tombstone_reapplied") if e.target_id == meeting_id]
    assert len(reapplied) == 1


def test_bytes_brought_back_by_an_object_restore_are_removed_again(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "restore-bytes")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key="restore-bytes-01")
    keys = _objects(settings, meeting_id)
    _snapshot_objects(settings, tmp_path / "objects-backup")
    _delete(client, tenant, meeting_id)
    assert _purge(meeting_id).state is PurgeState.COMPLETED
    # Only the object store is restored (the database is newer).
    _restore_objects(settings, tmp_path / "objects-backup")
    storage = create_object_storage(settings)
    assert all(storage.exists(workspace_id, k) for k in keys)

    counts = reapply_tombstones(settings)
    assert counts["already_tombstoned"] == 1 and counts["purges_reopened"] == 1, counts
    assert counts["purges_completed"] == 1
    assert not any(storage.exists(workspace_id, k) for k in keys)
    assert _purge(meeting_id).objects_removed == 2 * len(keys)
    # Reapplying twice is harmless.
    again = reapply_tombstones(settings)
    assert again["purges_completed"] == 1 and again["reapplied"] == 0


def test_reapply_respects_a_hold_present_in_the_restored_database(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "restore-hold")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key="restore-hold-01")
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-9", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text
    rows = _content_rows(meeting_id)
    _delete(client, tenant, meeting_id)
    record = _journal(settings).read()[0][0]
    held_keys = {m.storage_key for m in _media(meeting_id)}
    assert not held_keys & set(record.object_keys)  # the journal omits held objects
    # Restore of a backup taken before the deletion (hold already in place).
    with inspection_session_factory()() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(deleted_at=None))
        db.execute(
            update(Recording).where(Recording.meeting_id == meeting_id).values(deleted_at=None)
        )
        db.execute(
            update(MediaObject)
            .where(MediaObject.meeting_id == meeting_id)
            .values(deletion_requested_at=None, deletion_reason=None)
        )
        db.delete(
            db.scalar(select(ConversationPurge).where(ConversationPurge.meeting_id == meeting_id))
        )
        db.commit()

    counts = reapply_tombstones(settings)
    assert counts["reapplied"] == 1 and counts["purges_completed"] == 0, counts
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 404
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == "legal_hold"
    assert _content_rows(meeting_id) == rows
    storage = create_object_storage(settings)
    assert all(m.lifecycle_state is MediaLifecycleState.LEGAL_HOLD for m in _media(meeting_id))
    assert all(storage.exists(workspace_id, key) for key in held_keys)
    assert verify_tombstones(settings)["alive"] == 0


def test_keys_of_a_conversation_unknown_to_the_restored_database_need_an_explicit_flag(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "restore-unknown")
    workspace_id = tenant["workspace_id"]
    storage = create_object_storage(settings)
    stray = storage.put_stream(workspace_id, io.BytesIO(b"x" * 64), content_type="audio/wav")
    live = _upload(client, tenant, key="restore-unknown-01").json()["meeting"]["id"]
    live_key = _media(live)[0].storage_key
    record = TombstoneRecord(
        purge_id="gone-purge",
        workspace_id=workspace_id,
        meeting_id="00000000-0000-0000-0000-000000000000",
        reason="meeting_deleted",
        tombstoned_at=datetime.now(UTC),
        object_keys=(stray.key, live_key),
    )
    _journal(settings).append([record], now=datetime.now(UTC))

    cautious = reapply_tombstones(settings)
    assert cautious["meeting_absent"] == 1 and cautious["unknown_keys_removed"] == 0
    assert storage.exists(workspace_id, stray.key)
    explicit = reapply_tombstones(settings, remove_unknown_keys=True)
    assert explicit["unknown_keys_removed"] == 1
    assert not storage.exists(workspace_id, stray.key)
    assert storage.exists(workspace_id, live_key)  # referenced by a live row: kept
    assert _get(client, tenant, f"/api/v1/meetings/{live}").status_code == 200


def test_verify_fails_on_untrustworthy_journal_lines(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "restore-verify")
    meeting_id = _upload(client, tenant, key="restore-verify-01").json()["meeting"]["id"]
    _delete(client, tenant, meeting_id)
    path = _journal(settings).files()[0]
    path.write_text(path.read_text().replace("meeting_deleted", "meeting_restored"))
    result = verify_tombstones(settings)
    assert result["rejected_lines"] == 1 and result["records"] == 0
