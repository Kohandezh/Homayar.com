"""HOM-116/117: consistent encrypted backup, isolated restore, reconciliation and the
restore gate, against real PostgreSQL databases and the local object backend.

Every restore here goes into a separate staging database on the test server and an
empty object root; ``promote`` renames the staging database only after a PASS
reconciliation, and the restored application is then started against it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg import sql as psycopg_sql
from sqlalchemy.engine import make_url

from backend.backup import faults
from backend.backup.__main__ import main as cli
from backend.backup.create import BackupBusy, backup_lock, create_backup
from backend.backup.crypto import BackupKey, decrypt_to_bytes, file_sha256
from backend.backup.faults import FaultInjected
from backend.backup.logical import DatabaseBackupError, connect, database_name, with_database
from backend.backup.manifest import (
    BackupInvalid,
    list_complete_sets,
    list_partial_sets,
    read_manifest,
    write_manifest,
)
from backend.backup.metrics import read_values, record_success
from backend.backup.reconcile import (
    REPORT_FILE,
    drop_retired,
    list_retired,
    promote,
    reconcile_restore,
)
from backend.backup.release import PROJECT_DIR, code_heads
from backend.backup.restore import (
    RestoreRefused,
    describe_storage_target,
    read_marker,
    restore_backup,
    write_marker,
)
from backend.backup.retention import prune
from backend.backup.verify import verify_set
from backend.database.relational import reset_database_state
from backend.main import create_app
from backend.storage import create_object_storage
from backend.tests._database import configured_database_url
from backend.tests.conftest import login_user
from backend.tests.test_authorization_matrix import _cookies
from backend.tests.test_ingestion_ledger import CONTENT, _headers, _patch_media, _tenant, _upload
from backend.tests.test_retention_purge import _media, _processed, _review

pytestmark = pytest.mark.postgres

KEY = BackupKey("test-backup-1", b"\x11" * 32)
OTHER_KEY = BackupKey("test-backup-2", b"\x22" * 32)


@pytest.fixture
def settings(settings, tmp_path):
    """Deployments in this module journal their tombstones (ADR 0022)."""
    return settings.model_copy(update={"tombstone_journal_dir": str(tmp_path / "journal")})


@pytest.fixture
def databases():
    """Names of databases a test creates; dropped (with their connections) afterwards."""
    created: list[str] = []
    yield created
    admin = with_database(configured_database_url(), "postgres")
    with connect(admin, autocommit=True) as connection:
        for name in created:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _admin() -> str:
    return configured_database_url()


def _stamp_head(database_url: str) -> None:
    """The fixture schema is create_all() + RLS (equal to the migrations, see
    test_postgres_guarantees); a deployment's database carries the Alembic stamp."""
    with connect(database_url) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"
        )
        connection.execute("DELETE FROM alembic_version")
        connection.execute("INSERT INTO alembic_version VALUES (%s)", (code_heads()[0],))
        connection.commit()


def _names(prefix: str, databases: list[str]) -> tuple[str, str]:
    suffix = secrets.token_hex(4)
    staging, live = f"hy_{prefix}_restoring_{suffix}", f"hy_{prefix}_live_{suffix}"
    databases.extend([staging, live])
    return staging, live


def _target(settings, tmp_path: Path, name: str = "restored-objects"):
    target_settings = settings.model_copy(update={"storage_local_root": str(tmp_path / name)})
    return target_settings, create_object_storage(target_settings)


def _backup(settings, tmp_path: Path, **kwargs) -> dict:
    root = kwargs.pop("root", tmp_path / "backups")
    return create_backup(
        root=root,
        key=KEY,
        database_url=_admin(),
        storage=kwargs.pop("storage", create_object_storage(settings)),
        settings=settings,
        **kwargs,
    )


def _scalar(database_url: str, statement: str, params: tuple = ()):
    with connect(database_url) as connection:
        value = connection.execute(statement, params).fetchone()[0]
        connection.rollback()
    return value


def _delete(client, tenant: dict, meeting_id: str) -> None:
    response = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert response.status_code == 204, response.text


def _restore(settings, tmp_path, set_dir, staging, **kwargs):
    target_settings, storage = kwargs.pop("target", None) or _target(settings, tmp_path)
    state = restore_backup(
        set_dir=Path(set_dir),
        key=kwargs.pop("key", KEY),
        admin_url=_admin(),
        staging_database=staging,
        storage=storage,
        work_dir=kwargs.pop("work_dir", tmp_path / "restore-work"),
        storage_target=describe_storage_target(target_settings),
        live_database_url=kwargs.pop("live_database_url", settings.database_url),
        **kwargs,
    )
    return state, target_settings, storage


def _reconcile(set_dir, staging, target_settings, storage, tmp_path, **kwargs):
    return reconcile_restore(
        set_dir=Path(set_dir),
        key=KEY,
        admin_url=_admin(),
        staging_database=staging,
        settings=target_settings,
        storage=storage,
        work_dir=kwargs.pop("work_dir", tmp_path / "restore-work"),
        storage_target=kwargs.pop("storage_target", describe_storage_target(target_settings)),
        **kwargs,
    )


# --- end to end ---------------------------------------------------------------------


def test_backup_offsite_copy_isolated_restore_reconcile_promote_and_serve(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    alpha = _tenant(client, email_outbox, "bk-alpha")
    beta = _tenant(client, email_outbox, "bk-beta")
    kept = _processed(client, alpha, settings, key="bk-alpha-kept", index=0)
    erased = _processed(client, alpha, settings, key="bk-alpha-erased", index=1)
    beta_meeting = _processed(client, beta, settings, key="bk-beta-kept", index=2)
    _stamp_head(settings.database_url)
    source_storage = create_object_storage(settings)
    erased_keys = [m.storage_key for m in _media(erased)]
    kept_hashes = {
        m.storage_key: m.sha256
        for m in _media(kept)
        if source_storage.exists(alpha["workspace_id"], m.storage_key)
    }

    root = tmp_path / "backups"
    textfile = root / "metrics" / "homayar_backup.prom"
    monkeypatch.setenv("BACKUP_DATABASE_URL", _admin())  # never a DSN on argv
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    monkeypatch.setenv(
        "HOMAYAR_BACKUP_KEY", f"{KEY.key_id}:{base64.b64encode(KEY.secret).decode()}"
    )
    manifest = _backup(settings, tmp_path, root=root)
    set_dir = Path(manifest["path"])
    assert not textfile.exists()  # creating a set moves no gauge

    # Identity and integrity evidence of the set (HOM-116).
    assert manifest["backup_id"] == set_dir.name
    assert manifest["database"]["alembic_revision"] == code_heads()[0]
    assert manifest["database"]["code_heads"] == code_heads()
    assert manifest["application"]["release_sha"] != ""
    assert manifest["encryption"]["state"] == "encrypted"
    assert manifest["encryption"]["key_id"] == KEY.key_id
    assert manifest["encryption"]["plaintext_written_to_disk"] is False
    objects = manifest["stores"]["objects"]
    assert objects["included"] and objects["objects"] >= 3 and objects["missing_at_copy"] == 0
    assert manifest["tenants"] == 2 and manifest["tombstone_journal"]["configured"] is True
    assert {"redis", "secrets", "tombstone_journal"} <= set(manifest["excluded"])
    raw = b"".join(p.read_bytes() for p in set_dir.rglob("*.enc"))
    assert b"Weekly customer call" not in raw and alpha["credentials"]["email"].encode() not in raw
    catalog = json.loads(
        decrypt_to_bytes(
            set_dir / "catalog.json.enc",
            KEY,
            backup_id=manifest["backup_id"],
            name="catalog.json.enc",
        )
    )
    secret_values = [settings.jwt_secret, settings.token_pepper, settings.database_url]
    for blob in ((set_dir / "manifest.json").read_text(), json.dumps(catalog)):
        assert not [value for value in secret_values if value in blob]
    assert objects["integrity"] == "complete"
    summary = verify_set(set_dir, KEY)
    assert summary["decrypted"] and summary["objects"] == objects["objects"]
    snapshot_time = datetime.fromisoformat(manifest["created_at"]).timestamp()
    assert cli(["--root", str(root), "verify", manifest["backup_id"]]) == 0
    assert read_values(textfile) == {
        "postgres": pytest.approx(snapshot_time, abs=0.001),
        "objects": pytest.approx(snapshot_time, abs=0.001),
    }
    assert (root / "sets" / f"{manifest['backup_id']}.verified").is_file()

    # An encrypted copy off the primary host, through the operator CLI. A
    # directory counts as off-site only once its medium carries the marker.
    offsite = tmp_path / "offsite"
    offsite.mkdir()
    assert cli(["--root", str(root), "replicate", manifest["backup_id"], str(offsite)]) == 1
    assert cli(["--root", str(root), "replicate", manifest["backup_id"], str(root / "x")]) == 1
    assert "offsite" not in read_values(textfile)
    (offsite / ".homayar-offsite").write_text("drill medium\n")
    assert cli(["--root", str(root), "replicate", manifest["backup_id"], str(offsite)]) == 0
    replica = offsite / "sets" / manifest["backup_id"]
    assert verify_set(replica, None)["mac_verified"] is False  # checksums only, no key there
    assert read_values(textfile)["offsite"] == pytest.approx(snapshot_time, abs=0.001)

    # After the backup, the owner erases a conversation (journaled, purged at once).
    _delete(client, alpha, erased)
    assert not any(source_storage.exists(alpha["workspace_id"], k) for k in erased_keys)

    # Isolated restore FROM THE OFF-SITE COPY into a staging database + empty store.
    staging, live = _names("e2e", databases)
    state, target_settings, target_storage = _restore(settings, tmp_path, replica, staging)
    assert state["status"] == "loaded" and state["objects"]["written"] == objects["objects"]
    staging_url = with_database(_admin(), staging)
    assert read_marker(_admin(), staging)["state"] == "loaded"
    # Before reconciliation the erased conversation is back in staging, and no gate is open.
    assert _scalar(staging_url, "SELECT deleted_at IS NULL FROM meetings WHERE id = %s", (erased,))
    assert any(target_storage.exists(alpha["workspace_id"], k) for k in erased_keys)
    with pytest.raises(RestoreRefused) as early:
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=tmp_path / "restore-work" / REPORT_FILE,
            key=KEY,
        )
    assert early.value.code == "report_unreadable"
    assert cli(["gate", "--live-db", live]) == 1
    with pytest.raises(SystemExit):
        cli(["--admin-url", _admin(), "gate", "--live-db", live])  # no DSN flags any more

    report = _reconcile(replica, staging, target_settings, target_storage, tmp_path)
    assert report["status"] == "PASS", report["statuses"]
    checks = report["checks"]
    assert checks["tombstones"]["details"]["replay"]["reapplied"] == 1
    assert checks["tombstones"]["details"]["verify"]["alive"] == 0
    assert checks["non_resurrection"]["details"]["journaled"] == 1
    assert checks["rls_behavior"]["details"]["tenants_checked"] == 2
    assert checks["rls_behavior"]["details"]["own_meetings_visible"] >= 3
    assert checks["tenant_boundaries"]["details"]["per_tenant_tables_verified_at_load"] > 20
    assert checks["objects"]["details"]["required_missing"] == 0
    assert checks["objects"]["details"]["content_verified"] >= 3
    assert checks["tombstones"]["details"]["journal_identity"] == "unanchored"
    assert checks["tenant_boundaries"]["details"]["tenant_foreign_keys_checked"] > 5
    assert checks["secrets"]["status"] == "PASS"
    assert not _scalar(
        staging_url, "SELECT deleted_at IS NULL FROM meetings WHERE id = %s", (erased,)
    )
    assert not any(target_storage.exists(alpha["workspace_id"], k) for k in erased_keys)
    assert (
        _scalar(
            staging_url, "SELECT count(*) FROM transcript_segments WHERE meeting_id = %s", (erased,)
        )
        == 0
    )

    promoted = promote(
        admin_url=_admin(),
        staging_database=staging,
        live_database=live,
        report_path=tmp_path / "restore-work" / REPORT_FILE,
        key=KEY,
    )
    assert promoted["live_database"] == live
    assert cli(["gate", "--live-db", live]) == 0

    # The restored application starts and serves the restored tenants, isolated.
    live_settings = target_settings.model_copy(
        update={"database_url": with_database(_admin(), live)}
    )
    reset_database_state()
    with TestClient(create_app(live_settings)) as restored:
        assert restored.get("/healthz").status_code == 200
        login_user(restored, alpha["credentials"])
        restored_alpha = {**alpha, "cookies": _cookies(restored)}
        ok = restored.get(
            f"/api/v1/meetings/{kept}",
            headers=_headers(restored_alpha),
            cookies=restored_alpha["cookies"],
        )
        assert ok.status_code == 200, ok.text
        gone = restored.get(
            f"/api/v1/meetings/{erased}",
            headers=_headers(restored_alpha),
            cookies=restored_alpha["cookies"],
        )
        assert gone.status_code == 404
        restored.cookies.clear()
        login_user(restored, beta["credentials"])
        restored_beta = {**beta, "cookies": _cookies(restored)}
        own = restored.get(
            f"/api/v1/meetings/{beta_meeting}",
            headers=_headers(restored_beta),
            cookies=restored_beta["cookies"],
        )
        assert own.status_code == 200
        foreign = restored.get(
            f"/api/v1/meetings/{kept}",
            headers=_headers(restored_beta),
            cookies=restored_beta["cookies"],
        )
        assert foreign.status_code == 404
    for key, sha in kept_hashes.items():
        with target_storage.open_reader(alpha["workspace_id"], key) as reader:
            assert hashlib.sha256(reader.read()).hexdigest() == sha


# --- interruption ------------------------------------------------------------------


def _subprocess_env(settings, root: Path, marker: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HOMAYAR_BACKUP_")}
    env.update(
        {
            "ENVIRONMENT": "test",
            "DATABASE_URL": settings.database_url,
            "JWT_SECRET": settings.jwt_secret,
            "TOKEN_PEPPER": settings.token_pepper,
            "STORAGE_BACKEND": "local",
            "STORAGE_LOCAL_ROOT": settings.storage_local_root,
            "TOMBSTONE_JOURNAL_DIR": settings.tombstone_journal_dir,
            "BACKUP_ROOT": str(root),
            "HOMAYAR_BACKUP_KEY": f"{KEY.key_id}:{base64.b64encode(KEY.secret).decode()}",
            "HOMAYAR_BACKUP_FAULT": "during_objects:pause",
            "HOMAYAR_BACKUP_FAULT_MARKER": str(marker),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def test_interrupted_backups_never_leave_a_usable_set_and_release_the_lock(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-interrupt")
    for index in range(2):
        accepted = _upload(
            client, tenant, key=f"bk-interrupt-{index}", content=CONTENT + bytes([index]) * 9
        )
        assert accepted.status_code == 202, accepted.text
    _stamp_head(settings.database_url)
    root = tmp_path / "backups"
    textfile = root / "metrics" / "homayar_backup.prom"
    monkeypatch.setenv("ENVIRONMENT", "test")

    # 1. A failure during the object copy.
    monkeypatch.setenv("HOMAYAR_BACKUP_FAULT", "during_objects")
    with pytest.raises(FaultInjected):
        _backup(settings, tmp_path, root=root)
    monkeypatch.delenv("HOMAYAR_BACKUP_FAULT")
    assert list_complete_sets(root) == [] and list_partial_sets(root) == []
    assert not textfile.exists()

    # 2. An operator interrupt (^C) right after the database phase.
    original = faults.point

    def interrupt(name: str) -> None:
        if name == "after_database":
            raise KeyboardInterrupt
        original(name)

    monkeypatch.setattr(faults, "point", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _backup(settings, tmp_path, root=root)
    monkeypatch.setattr(faults, "point", original)
    assert list_complete_sets(root) == [] and list_partial_sets(root) == []

    # 3. SIGKILL in the middle of the object copy: nothing can clean up.
    marker = tmp_path / "paused"
    process = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "backend.backup", "create"],
        cwd=str(PROJECT_DIR),
        env=_subprocess_env(settings, root, marker),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 180
        while not marker.exists():
            assert process.poll() is None, process.communicate()[1][-2000:]
            assert time.monotonic() < deadline, "the backup never reached the object copy"
            time.sleep(0.2)
        with pytest.raises(BackupBusy):  # a concurrent run is refused, not interleaved
            with backup_lock(root):
                pass
    finally:
        process.send_signal(signal.SIGKILL)
        process.communicate()
    partial = list_partial_sets(root)
    assert len(partial) == 1 and list_complete_sets(root) == []
    with pytest.raises(BackupInvalid) as incomplete:
        verify_set(partial[0], KEY)
    assert incomplete.value.code == "incomplete"
    assert not textfile.exists()

    # The kernel released the lock: the next scheduled run completes.
    manifest = _backup(settings, tmp_path, root=root)
    assert [name for name, _ in list_complete_sets(root)] == [manifest["backup_id"]]
    assert not textfile.exists()  # only a verified set moves the gauges
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    monkeypatch.setenv(
        "HOMAYAR_BACKUP_KEY", f"{KEY.key_id}:{base64.b64encode(KEY.secret).decode()}"
    )
    assert cli(["--root", str(root), "verify", manifest["backup_id"]]) == 0
    fresh = read_values(textfile)
    assert set(fresh) == {"postgres", "objects"}
    older = datetime.fromisoformat(manifest["created_at"]).timestamp() - 3600
    record_success(textfile, {"postgres": older})  # verifying an older set later
    assert read_values(textfile)["postgres"] == fresh["postgres"]  # never moves backwards
    pruned = prune(root, partial_grace_seconds=0)
    assert pruned["stale_partials"] == [partial[0].name] and pruned["kept"] == [
        manifest["backup_id"]
    ]
    assert list_partial_sets(root) == []


# --- invalid and corrupted sets -------------------------------------------------------


def _copy(set_dir: Path, tmp_path: Path, label: str) -> Path:
    target = tmp_path / f"corrupt-{label}" / "sets" / set_dir.name
    shutil.copytree(set_dir, target)
    return target


def _flip(path: Path, offset: int = -20) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0x01
    path.write_bytes(bytes(data))


def _first_table_file(set_dir: Path) -> Path:
    return sorted((set_dir / "db").glob("*.copy.enc"), key=lambda p: -p.stat().st_size)[0]


def test_invalid_and_corrupted_sets_are_refused_before_any_restore(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-corrupt")
    assert _upload(client, tenant, key="bk-corrupt-1").status_code == 202
    _stamp_head(settings.database_url)
    set_dir = Path(_backup(settings, tmp_path)["path"])
    object_file = next((set_dir / "objects").rglob("*.enc"))

    def expect(path: Path, code: str, key=KEY) -> None:
        with pytest.raises(BackupInvalid) as error:
            verify_set(path, key)
        assert error.value.code == code, error.value

    case = _copy(set_dir, tmp_path, "metadata")
    manifest = json.loads((case / "manifest.json").read_text())
    manifest["created_at"] = "2020-01-01T00:00:00+00:00"
    (case / "manifest.json").write_text(json.dumps(manifest))
    expect(case, "manifest_tampered")

    case = _copy(set_dir, tmp_path, "bitrot")
    _flip(_first_table_file(case))
    expect(case, "checksum_mismatch")
    expect(case, "checksum_mismatch", key=None)  # caught off-site without the key too

    case = _copy(set_dir, tmp_path, "forged")
    table = _first_table_file(case)
    _flip(table)
    manifest = json.loads((case / "manifest.json").read_text())
    name = table.relative_to(case).as_posix()
    entry = next(e for e in manifest["files"] if e["name"] == name)
    entry["sha256"], entry["size"] = file_sha256(table)
    (case / "manifest.json").write_text(json.dumps(manifest))
    verify_set(case, None)  # checksums alone cannot see a rewritten manifest ...
    expect(case, "manifest_tampered")  # ... the keyed seal can

    case = _copy(set_dir, tmp_path, "truncated")
    victim = case / object_file.relative_to(set_dir)
    victim.write_bytes(victim.read_bytes()[:-7])
    expect(case, "size_mismatch")

    case = _copy(set_dir, tmp_path, "missing")
    (case / object_file.relative_to(set_dir)).unlink()
    expect(case, "file_missing")

    case = _copy(set_dir, tmp_path, "extra")
    (case / "db" / "planted.copy.enc").write_bytes(b"x")
    expect(case, "unexpected_files")

    case = _copy(set_dir, tmp_path, "nomanifest")
    (case / "manifest.json").unlink()
    expect(case, "manifest_missing")

    expect(set_dir, "wrong_key", key=OTHER_KEY)

    case = _copy(set_dir, tmp_path, "partial")
    partial = case.parent / f".partial-{case.name}"
    case.rename(partial)
    expect(partial, "incomplete")

    # Corrupt ciphertext under a validly sealed manifest (only the key holder could
    # do this; it models media corruption after checksums were recomputed).
    case = _copy(set_dir, tmp_path, "resealed")
    table = _first_table_file(case)
    _flip(table, offset=-3)
    manifest = read_manifest(case)
    name = table.relative_to(case).as_posix()
    entry = next(e for e in manifest["files"] if e["name"] == name)
    entry["sha256"], entry["size"] = file_sha256(table)
    write_manifest(case, {k: v for k, v in manifest.items() if k != "mac"}, KEY)
    verify_set(case, KEY, deep=False)
    expect(case, "decryption_failed")

    # A restore refuses every invalid set before it creates anything ...
    staging, _ = _names("corrupt", databases)
    for label in ("metadata", "bitrot", "forged", "truncated", "missing", "extra"):
        with pytest.raises(BackupInvalid):
            _restore(
                settings, tmp_path, tmp_path / f"corrupt-{label}" / "sets" / set_dir.name, staging
            )
        assert read_marker(_admin(), staging) is False
    # ... and a set that only fails while decrypting stops the load in its single
    # transaction: the staging database stays "restoring" and can never be promoted.
    with pytest.raises(Exception, match="authentication failed"):
        _restore(settings, tmp_path, case, staging)
    assert read_marker(_admin(), staging)["state"] == "restoring"
    assert _scalar(with_database(_admin(), staging), "SELECT count(*) FROM meetings") == 0


# --- restore failure, isolation guards and idempotent retry ---------------------------


def test_restore_failure_isolation_guards_and_idempotent_retry(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-retry")
    meeting = _processed(client, tenant, settings, key="bk-retry-1", index=3)
    _stamp_head(settings.database_url)
    set_dir = Path(_backup(settings, tmp_path)["path"])
    _delete(client, tenant, meeting)
    staging, live = _names("retry", databases)

    # Isolation: never into the database the application serves, by name alone
    # (host strings differ between an operator host and the Compose network).
    with pytest.raises(RestoreRefused) as same:
        _restore(settings, tmp_path, set_dir, database_name(settings.database_url))
    assert same.value.code == "staging_is_live"
    live_elsewhere = f"hy_live_restoring_{secrets.token_hex(3)}"
    with pytest.raises(RestoreRefused) as live_only:
        _restore(
            settings,
            tmp_path,
            set_dir,
            live_elsewhere,
            live_database_url=with_database(
                "postgresql+psycopg://app@10.0.0.5:5432/x", live_elsewhere
            ),
        )
    assert live_only.value.code == "staging_is_live"
    with pytest.raises(RestoreRefused) as unnamed:
        _restore(settings, tmp_path, set_dir, f"hy_plain_{secrets.token_hex(3)}")
    assert unnamed.value.code == "staging_name"
    # Never over a database that is not a restore target.
    foreign = f"hy_foreign_restoring_{secrets.token_hex(4)}"
    databases.append(foreign)
    with connect(with_database(_admin(), "postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{foreign}"')
    with pytest.raises(RestoreRefused) as not_ours:
        _restore(settings, tmp_path, set_dir, foreign)
    assert not_ours.value.code == "staging_not_ours"
    # Never into an object store that already holds this backup's tenants.
    occupied_target = _target(settings, tmp_path, "occupied")
    occupied_target[1].put_stream(
        tenant["workspace_id"], iter([b"bytes already there"]), content_type="audio/wav"
    )
    with pytest.raises(RestoreRefused) as occupied:
        _restore(settings, tmp_path, set_dir, staging, target=occupied_target)
    assert occupied.value.code == "target_store_not_empty"
    assert read_marker(_admin(), staging) is False
    # One run per staging database: a second concurrent run is refused.
    with connect(with_database(_admin(), "postgres"), autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"homayar-restore:{staging}",))
        with pytest.raises(RestoreRefused) as busy:
            _restore(settings, tmp_path, set_dir, staging, target=_target(settings, tmp_path, "b"))
        assert busy.value.code == "staging_busy"

    # A crash in the middle of the object restore.
    target = _target(settings, tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("HOMAYAR_BACKUP_FAULT", "during_object_restore")
    with pytest.raises(FaultInjected):
        _restore(settings, tmp_path, set_dir, staging, target=target)
    monkeypatch.delenv("HOMAYAR_BACKUP_FAULT")
    assert read_marker(_admin(), staging)["state"] == "restoring"
    with pytest.raises(RestoreRefused) as half:
        _reconcile(set_dir, staging, target[0], target[1], tmp_path)
    assert half.value.code == "staging_not_loaded"
    # A plain re-run refuses the half-filled store; --resume finishes the same restore.
    with pytest.raises(RestoreRefused) as again:
        _restore(settings, tmp_path, set_dir, staging, target=target)
    assert again.value.code == "target_store_not_empty"
    elsewhere = _target(settings, tmp_path, "elsewhere")
    with pytest.raises(RestoreRefused) as moved:
        _restore(settings, tmp_path, set_dir, staging, target=elsewhere, resume=True)
    assert moved.value.code == "storage_target_mismatch"
    state, *_ = _restore(settings, tmp_path, set_dir, staging, target=target, resume=True)
    assert state["status"] == "loaded" and state["objects"]["already_present"] >= 1
    with pytest.raises(RestoreRefused) as wrong_store:
        _reconcile(
            set_dir,
            staging,
            target[0],
            target[1],
            tmp_path,
            storage_target=describe_storage_target(elsewhere[0]),
        )
    assert wrong_store.value.code == "storage_target_mismatch"

    # The backup recorded no tombstone, but one followed it: an empty replica
    # must not pass (only an explicit owner decision could).
    no_files = tmp_path / "unsynced-replica"
    no_files.mkdir()
    unsynced = target[0].model_copy(update={"tombstone_journal_dir": str(no_files)})
    blind = _reconcile(set_dir, staging, unsynced, target[1], tmp_path)
    assert blind["statuses"]["tombstones"] == "FAIL"
    first = _reconcile(set_dir, staging, target[0], target[1], tmp_path)
    assert first["status"] == "PASS", first["statuses"]
    assert first["checks"]["tombstones"]["details"]["replay"]["reapplied"] == 1
    first_report = tmp_path / "first-report.json"
    shutil.copy(first["path"], first_report)
    second = _reconcile(set_dir, staging, target[0], target[1], tmp_path)  # idempotent
    assert second["status"] == "PASS", second["statuses"]
    replay = second["checks"]["tombstones"]["details"]["replay"]
    assert replay["reapplied"] == 0 and replay["already_tombstoned"] == 1

    # An older (genuinely sealed, PASS) report cannot promote a later state ...
    with pytest.raises(RestoreRefused) as stale_report:
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=first_report,
            key=KEY,
        )
    assert stale_report.value.code == "report_mismatch"
    # ... nor can any report while a reconcile is (or was left) running.
    current = read_marker(_admin(), staging)
    write_marker(_admin(), staging, {**current, "state": "reconciling"})
    with pytest.raises(RestoreRefused) as running:
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=Path(second["path"]),
            key=KEY,
        )
    assert running.value.code == "staging_not_reconciled"
    # Reconcile runs only against the set the restore verified.
    original = (set_dir / "manifest.json").read_bytes()
    edited = json.loads(original)
    edited["tombstone_journal"] = {"configured": True, "files": [], "records": 0}
    (set_dir / "manifest.json").write_text(json.dumps(edited))
    with pytest.raises(BackupInvalid) as tampered:
        _reconcile(set_dir, staging, target[0], target[1], tmp_path)
    assert tampered.value.code == "manifest_tampered"
    write_manifest(set_dir, {k: v for k, v in edited.items() if k != "mac"}, KEY)
    with pytest.raises(RestoreRefused) as resealed:
        _reconcile(set_dir, staging, target[0], target[1], tmp_path)
    assert resealed.value.code == "manifest_mismatch"
    (set_dir / "manifest.json").write_bytes(original)

    # Another backup cannot be restored over this staging database.
    other_set = Path(_backup(settings, tmp_path, root=tmp_path / "backups-2")["path"])
    with pytest.raises(RestoreRefused) as other:
        _restore(
            settings,
            tmp_path,
            other_set,
            staging,
            target=_target(settings, tmp_path, "o2"),
            work_dir=tmp_path / "work-2",
        )
    assert other.value.code == "staging_other_backup"

    # A newer release is required for a backup taken at an unknown revision.
    monkeypatch.setattr("backend.backup.restore.code_has_revision", lambda revision: False)
    with pytest.raises(RestoreRefused) as older:
        _restore(
            settings,
            tmp_path,
            set_dir,
            f"hy_never_{secrets.token_hex(3)}",
            target=_target(settings, tmp_path, "o3"),
            work_dir=tmp_path / "work-3",
        )
    assert older.value.code == "code_older_than_backup"


# --- RLS, tenant boundaries and object reconciliation -----------------------------------


def test_reconciliation_fails_on_broken_rls_or_crossed_tenants_and_blocks_promotion(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    alpha = _tenant(client, email_outbox, "bk-rls-a")
    beta = _tenant(client, email_outbox, "bk-rls-b")
    meeting = _processed(client, alpha, settings, key="bk-rls-a1", index=4)
    _processed(client, beta, settings, key="bk-rls-b1", index=5)
    _stamp_head(settings.database_url)
    set_dir = Path(_backup(settings, tmp_path)["path"])
    staging, live = _names("rls", databases)
    _, target_settings, storage = _restore(settings, tmp_path, set_dir, staging)
    staging_url = with_database(_admin(), staging)

    with connect(staging_url) as connection:
        connection.execute("DROP POLICY homayar_tenant_isolation ON meetings")
        connection.execute("CREATE POLICY leak ON meetings USING (true)")
        connection.execute(
            "UPDATE transcript_segments SET workspace_id = %s WHERE meeting_id = %s",
            (beta["workspace_id"], meeting),
        )
        # A row whose only tenant link is a plain foreign key (no meeting_id).
        crossed = connection.execute(
            "UPDATE usage_events SET workspace_id = %s WHERE id = (SELECT id FROM usage_events "
            "WHERE workspace_id = %s AND reservation_id IS NOT NULL LIMIT 1) RETURNING id",
            (beta["workspace_id"], alpha["workspace_id"]),
        ).fetchone()
        assert crossed is not None
        connection.commit()
    report = _reconcile(set_dir, staging, target_settings, storage, tmp_path)
    assert report["status"] == "FAIL"
    assert report["statuses"]["rls_structure"] == "FAIL"
    assert report["statuses"]["rls_behavior"] == "FAIL"
    assert report["statuses"]["tenant_boundaries"] == "FAIL"
    problems = report["checks"]["tenant_boundaries"]["details"]["problems"]
    assert any(p["table"] == "transcript_segments" for p in problems)
    assert any(
        p["table"] == "usage_events" and "points into another tenant" in p["problem"]
        for p in problems
    )
    with pytest.raises(RestoreRefused) as blocked:
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=tmp_path / "restore-work" / REPORT_FILE,
            key=KEY,
        )
    assert blocked.value.code == "not_reconciled"

    # A report edited to PASS does not verify.
    path = tmp_path / "restore-work" / REPORT_FILE
    forged = json.loads(path.read_text())
    forged["status"] = "PASS"
    path.write_text(json.dumps(forged))
    with pytest.raises(RestoreRefused) as tampered:
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=path,
            key=KEY,
        )
    assert tampered.value.code == "report_tampered"


def test_object_reconciliation_reports_orphans_and_fails_on_missing_bytes(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-objects")
    meeting = _processed(client, tenant, settings, key="bk-objects-1", index=6)
    _stamp_head(settings.database_url)
    set_dir = Path(_backup(settings, tmp_path)["path"])
    staging, live = _names("objects", databases)
    _, target_settings, storage = _restore(settings, tmp_path, set_dir, staging)
    workspace_id = tenant["workspace_id"]
    original = next(m for m in _media(meeting) if m.kind.name == "ORIGINAL")
    assert storage.delete(workspace_id, original.storage_key)
    # A checkpoint whose object the restored row says was removed (copied while
    # its cleanup was in flight): reconciliation removes the bytes again.
    with connect(with_database(_admin(), staging)) as connection:
        artifact = connection.execute(
            "UPDATE stage_checkpoints SET state = 'ABANDONED', object_deleted_at = now() "
            "WHERE id = (SELECT id FROM stage_checkpoints WHERE meeting_id = %s "
            "ORDER BY id LIMIT 1) RETURNING artifact_key",
            (meeting,),
        ).fetchone()[0]
        connection.commit()
    assert storage.exists(workspace_id, artifact)
    orphan = storage.put_stream(workspace_id, iter([b"stray bytes"]), content_type="audio/wav")
    # Bit rot in the target store after the restore: content no longer matches the row.
    with connect(with_database(_admin(), staging)) as connection:
        rotten = connection.execute(
            "SELECT artifact_key FROM stage_checkpoints WHERE meeting_id = %s "
            "AND state = 'PUBLISHED' ORDER BY id DESC LIMIT 1",
            (meeting,),
        ).fetchone()[0]
        connection.rollback()
    rotten_path = Path(target_settings.storage_local_root) / rotten
    rotten_path.write_bytes(b"{}" + rotten_path.read_bytes()[2:])

    report = _reconcile(set_dir, staging, target_settings, storage, tmp_path)
    details = report["checks"]["objects"]["details"]
    assert report["statuses"]["objects"] == "FAIL" and report["status"] == "FAIL"
    assert details["content_mismatch_keys"] == [rotten]
    assert details["required_missing_keys"] == [original.storage_key]
    assert details["orphan_keys"] == [orphan.key]
    assert storage.exists(workspace_id, orphan.key)  # reported, never deleted
    assert details["removed_again"] == 1 and not storage.exists(workspace_id, artifact)

    accepted = _reconcile(
        set_dir,
        staging,
        target_settings,
        storage,
        tmp_path,
        accept_missing="INC-42",
        accept_no_journal="OWNER-NO-DELETIONS",  # nothing was ever deleted here
    )
    assert accepted["status"] == "PASS", accepted["statuses"]
    assert accepted["checks"]["objects"]["details"]["accepted_missing"] == "INC-42"
    assert accepted["owner_overrides"] == {
        "accept_missing": "INC-42",
        "accept_no_journal": "OWNER-NO-DELETIONS",
    }
    # The live name already holds data: it is kept, marked, and must be dropped.
    with connect(with_database(_admin(), "postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{live}"')
    with connect(with_database(_admin(), live)) as connection:
        connection.execute("CREATE TABLE old_rows (id int)")
        connection.commit()
    with pytest.raises(RestoreRefused) as exists:
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=tmp_path / "restore-work" / REPORT_FILE,
            key=KEY,
        )
    assert exists.value.code == "live_database_exists"
    promoted = promote(
        admin_url=_admin(),
        staging_database=staging,
        live_database=live,
        report_path=tmp_path / "restore-work" / REPORT_FILE,
        key=KEY,
        retire_existing=True,
    )
    assert read_marker(_admin(), live)["state"] == "promoted"
    retired = promoted["retired_database"]
    databases.append(retired)
    listed = {item["database"]: item for item in list_retired(_admin())}
    assert retired in listed and listed[retired]["replaced_by_backup"] == promoted["backup_id"]
    with pytest.raises(RestoreRefused):
        drop_retired(_admin(), retired, confirm="wrong")
    with pytest.raises(RestoreRefused):
        drop_retired(_admin(), live, confirm=live)  # only retired databases
    assert drop_retired(_admin(), retired, confirm=retired) == {"dropped": retired}
    assert read_marker(_admin(), retired) is False


def test_a_conversation_held_in_the_backup_needs_an_owner_decision_before_promotion(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-hold")
    meeting = _processed(client, tenant, settings, key="bk-hold-1", index=7)
    held = client.post(
        f"/api/v1/meetings/{meeting}/legal-hold",
        json={"reason": "litigation 2026-11", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text
    _stamp_head(settings.database_url)
    set_dir = Path(_backup(settings, tmp_path)["path"])
    _delete(client, tenant, meeting)
    staging, live = _names("hold", databases)
    _, target_settings, storage = _restore(settings, tmp_path, set_dir, staging)

    report = _reconcile(set_dir, staging, target_settings, storage, tmp_path)
    assert report["status"] == "REVIEW_REQUIRED", report["statuses"]
    held_list = report["checks"]["non_resurrection"]["details"]["held_for_review"]
    assert held_list == [{"meeting_id": meeting, "workspace_id": tenant["workspace_id"]}]
    staging_url = with_database(_admin(), staging)
    assert not _scalar(
        staging_url, "SELECT deleted_at IS NULL FROM meetings WHERE id = %s", (meeting,)
    )
    with pytest.raises(RestoreRefused):
        promote(
            admin_url=_admin(),
            staging_database=staging,
            live_database=live,
            report_path=tmp_path / "restore-work" / REPORT_FILE,
            key=KEY,
        )

    decided = _reconcile(
        set_dir, staging, target_settings, storage, tmp_path, accept_held="LEGAL-7"
    )
    assert decided["status"] == "PASS", decided["statuses"]
    assert decided["checks"]["non_resurrection"]["details"]["accepted_held"] == "LEGAL-7"
    # The empty database a fresh postgres image creates under the live name is dropped.
    with connect(with_database(_admin(), "postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{live}"')
    promoted = promote(
        admin_url=_admin(),
        staging_database=staging,
        live_database=live,
        report_path=tmp_path / "restore-work" / REPORT_FILE,
        key=KEY,
        retire_existing=True,
    )
    assert promoted["retired_database"] is None and promoted["dropped_empty_database"]
    assert read_marker(_admin(), promoted["dropped_empty_database"]) is False


# --- deletion state: journal trust and the object-copy window ----------------------------


def test_reconciliation_refuses_an_empty_or_stale_tombstone_journal(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-journal")
    meetings = [
        _processed(client, tenant, settings, key=f"bk-journal-{i}", index=10 + i) for i in range(3)
    ]
    _delete(client, tenant, meetings[0])  # journaled before the backup
    _delete(client, tenant, meetings[1])
    _stamp_head(settings.database_url)
    manifest = _backup(settings, tmp_path)
    assert manifest["tombstone_journal"]["records"] == 2
    _delete(client, tenant, meetings[2])  # after the backup
    staging, _ = _names("journal", databases)
    _, target_settings, storage = _restore(settings, tmp_path, manifest["path"], staging)
    journal_dir = Path(settings.tombstone_journal_dir)

    def run(directory: Path, **kwargs):
        view = target_settings.model_copy(update={"tombstone_journal_dir": str(directory)})
        return _reconcile(manifest["path"], staging, view, storage, tmp_path, **kwargs)

    empty = tmp_path / "empty-replica"
    empty.mkdir()
    report = run(empty)  # an unmounted / failed replica
    assert report["status"] == "FAIL" and report["statuses"]["tombstones"] == "FAIL"

    stale = tmp_path / "stale-replica"
    stale.mkdir()
    first_file = sorted(journal_dir.iterdir())[0]
    (stale / first_file.name).write_text(first_file.read_text().splitlines(keepends=True)[0])
    report = run(stale)  # a replica older than the backup itself
    assert report["statuses"]["tombstones"] == "FAIL"
    assert report["checks"]["tombstones"]["details"]["stale_or_foreign_journal"]

    foreign = tmp_path / "foreign-replica"
    foreign.mkdir()
    lines = first_file.read_text().splitlines(keepends=True)
    (foreign / first_file.name).write_text(lines[1] + lines[0] + "".join(lines[2:]))
    report = run(foreign)  # same records, different bytes: not this deployment's file
    assert report["statuses"]["tombstones"] == "FAIL"
    assert any(
        "differs" in p
        for p in report["checks"]["tombstones"]["details"]["stale_or_foreign_journal"]
    )

    accepted = run(empty, accept_no_journal="OWNER-NO-JOURNAL-1")
    assert accepted["statuses"]["tombstones"] == "PASS"
    details = accepted["checks"]["tombstones"]["details"]
    assert details["owner_override_no_journal"] == "OWNER-NO-JOURNAL-1"
    assert accepted["owner_overrides"]["accept_no_journal"] == "OWNER-NO-JOURNAL-1"

    report = run(journal_dir)  # the real replica: the post-backup deletion is replayed
    assert report["status"] == "PASS", report["statuses"]
    assert report["checks"]["tombstones"]["details"]["journal_identity"] == "anchored"
    assert report["checks"]["tombstones"]["details"]["replay"]["reapplied"] == 1


def test_bytes_copied_after_the_snapshot_of_a_later_deleted_conversation_are_removed(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    import backend.backup.create as create_module

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-window")
    assert _upload(client, tenant, key="bk-window-early").status_code == 202
    _stamp_head(settings.database_url)
    late: dict[str, str] = {}
    copy_objects = create_module.backup_objects

    def upload_during_the_copy(storage, **kwargs):
        accepted = _upload(client, tenant, key="bk-window-late", content=CONTENT + b"late")
        assert accepted.status_code == 202, accepted.text
        late["meeting"] = accepted.json()["meeting"]["id"]
        return copy_objects(storage, **kwargs)

    monkeypatch.setattr(create_module, "backup_objects", upload_during_the_copy)
    manifest = _backup(settings, tmp_path)
    late_keys = [m.storage_key for m in _media(late["meeting"])]
    _delete(client, tenant, late["meeting"])  # journaled with its object keys
    staging, _ = _names("window", databases)
    _, target_settings, storage = _restore(settings, tmp_path, manifest["path"], staging)
    workspace_id = tenant["workspace_id"]
    assert late_keys and all(storage.exists(workspace_id, key) for key in late_keys)

    report = _reconcile(manifest["path"], staging, target_settings, storage, tmp_path)
    assert report["status"] == "PASS", report["statuses"]
    replay = report["checks"]["tombstones"]["details"]["replay"]
    assert replay["meeting_absent"] == 1 and replay["unknown_keys_removed"] == len(late_keys)
    assert not any(storage.exists(workspace_id, key) for key in late_keys)


# --- backup-side integrity --------------------------------------------------------------


def test_a_corrupt_source_object_degrades_the_set_and_never_passes_reconciliation(
    client, email_outbox, monkeypatch, settings, tmp_path, databases
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "bk-rot")
    meeting = _processed(client, tenant, settings, key="bk-rot-1", index=20)
    _stamp_head(settings.database_url)
    original = next(m for m in _media(meeting) if m.kind.name == "ORIGINAL")
    source_path = Path(settings.storage_local_root) / original.storage_key
    data = bytearray(source_path.read_bytes())
    data[-1] ^= 0x01  # bit rot on the primary store
    source_path.write_bytes(bytes(data))

    root = tmp_path / "backups"
    textfile = tmp_path / "textfile" / "homayar_backup.prom"
    manifest = _backup(settings, tmp_path, root=root)
    objects = manifest["stores"]["objects"]
    assert objects["integrity"] == "degraded" and objects["checksum_mismatch_at_copy"] == 1
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    monkeypatch.setenv(
        "HOMAYAR_BACKUP_KEY", f"{KEY.key_id}:{base64.b64encode(KEY.secret).decode()}"
    )
    assert cli(["--root", str(root), "verify", manifest["backup_id"]]) == 0
    assert set(read_values(textfile)) == {"postgres"}  # the objects gauge stays stale

    staging, _ = _names("rot", databases)
    _, target_settings, storage = _restore(settings, tmp_path, manifest["path"], staging)
    report = _reconcile(
        manifest["path"], staging, target_settings, storage, tmp_path, accept_no_journal="T"
    )
    details = report["checks"]["objects"]["details"]
    assert report["statuses"]["objects"] == "FAIL"
    assert original.storage_key in details["content_mismatch_keys"]
    assert details["backup_integrity"] == "degraded"


def test_a_role_subject_to_row_level_security_cannot_take_a_backup(settings, tmp_path):
    role = f"hy_backup_rls_{secrets.token_hex(3)}"
    password = secrets.token_hex(12)
    with connect(_admin(), autocommit=True) as connection:
        connection.execute(
            psycopg_sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                psycopg_sql.Identifier(role), psycopg_sql.Literal(password)
            )
        )
    try:
        url = (
            make_url(_admin())
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )
        with pytest.raises(DatabaseBackupError) as refused:
            create_backup(root=tmp_path / "b", key=KEY, database_url=url, storage=None)
        assert refused.value.code == "role_subject_to_rls"
        assert list_complete_sets(tmp_path / "b") == []
    finally:
        with connect(_admin(), autocommit=True) as connection:
            connection.execute(psycopg_sql.SQL("DROP ROLE {}").format(psycopg_sql.Identifier(role)))
