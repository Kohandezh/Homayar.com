"""HOM-218: the backup guards that had no test, checked without a database.

The Alembic child environment, a failed deep verify, concurrent freshness
textfile writers, a CLI ``create`` that must move no gauge, and the real CLI
``create`` output read by ``infra/scripts/backup.sh``.  The PostgreSQL guards
(``staging_changed``, ``staging_busy``) are in test_backup_restore.py.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.backup import metrics, release
from backend.backup.__main__ import main as cli
from backend.backup.crypto import HEADER_SIZE, file_sha256
from backend.backup.manifest import read_manifest, write_manifest
from backend.tests.test_backup_format import _key, _verifiable_set

BACKUP_ID = "20261018T020000Z-0000000c"
NOW = 1_793_000_000.0


def _use_test_key(monkeypatch) -> None:
    key = _key()
    monkeypatch.setenv(
        "HOMAYAR_BACKUP_KEY", f"{key.key_id}:{base64.b64encode(key.secret).decode()}"
    )
    monkeypatch.delenv("HOMAYAR_BACKUP_KEY_FILE", raising=False)


# --- the Alembic child process gets one DATABASE_URL and no DATABASE_URL_FILE ----------


def _run_alembic_env(monkeypatch, returncode: int = 0, stderr: str = "") -> dict[str, str]:
    seen: dict[str, dict[str, str]] = {}

    def run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(release.subprocess, "run", run)
    release.run_alembic("postgresql+psycopg://admin:pw@db/homayar_restoring", "head")
    return seen["env"]


def test_alembic_child_env_drops_every_spelling_of_the_database_url_settings(monkeypatch):
    for name in (
        "DATABASE_URL",
        "database_url",
        "Database_Url",
        "DATABASE_URL_FILE",
        "database_url_file",
        "Database_Url_File",
    ):
        monkeypatch.setenv(name, f"postgresql://{name}@wrong/live")
    near = {
        "DATABASE_URL_RO": "postgresql://ro@replica/live",
        "TEST_DATABASE_URL": "postgresql://test@db/test",
        "HOMAYAR_DATABASE_URL": "postgresql://other@db/other",
    }
    for name, value in near.items():
        monkeypatch.setenv(name, value)

    env = _run_alembic_env(monkeypatch)
    assert [k for k in env if k.upper() == "DATABASE_URL"] == ["DATABASE_URL"]
    assert env["DATABASE_URL"] == "postgresql+psycopg://admin:pw@db/homayar_restoring"
    assert [k for k in env if k.upper() == "DATABASE_URL_FILE"] == []
    assert {k: env.get(k) for k in near} == near
    assert env["PATH"] == os.environ["PATH"]


def test_a_failed_alembic_child_raises_and_masks_the_dsn_password(monkeypatch):
    stderr = (
        "Traceback (most recent call last):\n"
        "psycopg.OperationalError: connection to postgresql://user:secret@host:5432/db failed\n"
    )
    with pytest.raises(release.MigrationError) as failed:
        _run_alembic_env(monkeypatch, returncode=1, stderr=stderr)
    message = str(failed.value)
    assert "alembic upgrade head failed" in message
    assert "://***@host:5432/db" in message
    assert "secret" not in message


# --- a failed deep verify marks nothing and moves no gauge -----------------------------


def _break_ciphertext(set_dir: Path, *, reseal: bool) -> None:
    """Flip one ciphertext byte of the table file; ``reseal`` fixes its size and
    SHA-256 in the manifest (sealed again), so only the decryption can see it."""
    name = "db/meetings.copy.enc"
    path = set_dir / name
    data = bytearray(path.read_bytes())
    data[HEADER_SIZE + 2] ^= 0x01
    path.write_bytes(bytes(data))
    if reseal:
        manifest = {k: v for k, v in read_manifest(set_dir).items() if k != "mac"}
        digest, size = file_sha256(path)
        for entry in manifest["files"]:
            if entry["name"] == name:
                entry.update(size=size, sha256=digest)
        write_manifest(set_dir, manifest, _key())


@pytest.mark.parametrize(
    ("reseal", "code"), [(True, "decryption_failed"), (False, "checksum_mismatch")]
)
@pytest.mark.parametrize("earlier_gauge", [False, True])
def test_a_failed_deep_verify_marks_nothing_and_moves_no_gauge(
    tmp_path, monkeypatch, capsys, reseal, code, earlier_gauge
):
    root = tmp_path / "root"
    set_dir = _verifiable_set(root, BACKUP_ID)
    textfile = tmp_path / "collector" / "homayar_backup.prom"
    if earlier_gauge:
        metrics.record_success(textfile, {"postgres": 1000.0, "objects": 1000.0}, now=NOW)
    before = textfile.read_bytes() if earlier_gauge else None
    _use_test_key(monkeypatch)
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    _break_ciphertext(set_dir, reseal=reseal)

    capsys.readouterr()
    assert cli(["--root", str(root), "verify", BACKUP_ID]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == code
    assert not (root / "sets" / f"{BACKUP_ID}.verified").exists()
    if earlier_gauge:
        assert textfile.read_bytes() == before
    else:
        assert not textfile.exists()


def test_an_intact_set_deep_verify_marks_it_and_a_shallow_verify_does_not(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "root"
    _verifiable_set(root, BACKUP_ID)
    textfile = tmp_path / "collector" / "homayar_backup.prom"
    marker = root / "sets" / f"{BACKUP_ID}.verified"
    _use_test_key(monkeypatch)
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))

    assert cli(["--root", str(root), "verify", BACKUP_ID, "--no-key"]) == 0
    assert json.loads(capsys.readouterr().out)["decrypted"] is False
    assert not marker.exists() and not textfile.exists()

    assert cli(["--root", str(root), "verify", BACKUP_ID]) == 0
    assert json.loads(capsys.readouterr().out)["gauges_updated"] == ["postgres"]
    assert marker.is_file()
    assert set(metrics.read_values(textfile)) == {"postgres"}


# --- concurrent textfile writers never lose each other's target ------------------------


def test_two_concurrent_textfile_writers_never_lose_each_others_target(tmp_path, monkeypatch):
    path = tmp_path / "collector" / "homayar_backup.prom"
    real_read = metrics.read_values
    first_read = threading.Event()
    second_read = threading.Event()
    second_done = threading.Event()

    def read_values(target: Path) -> dict[str, float]:
        values = real_read(target)
        if threading.current_thread().name == "first-writer":
            first_read.set()
            # Holding the lock, the second writer cannot read before this one renames.
            if second_read.wait(timeout=1.0):
                second_done.wait(timeout=2.0)
        else:
            second_read.set()
        return values

    monkeypatch.setattr(metrics, "read_values", read_values)
    errors: list[BaseException] = []

    def write(timestamps: dict[str, float], done: threading.Event | None = None) -> None:
        try:
            metrics.record_success(path, timestamps, now=NOW)
        except BaseException as exc:  # noqa: BLE001 - reported by the main thread
            errors.append(exc)
        finally:
            if done is not None:
                done.set()

    first = threading.Thread(target=write, args=({"postgres": NOW - 10},), name="first-writer")
    second = threading.Thread(
        target=write, args=({"objects": NOW - 20}, second_done), name="second-writer"
    )
    first.start()
    assert first_read.wait(timeout=2.0)
    second.start()
    first.join(timeout=4.0)
    second.join(timeout=4.0)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert real_read(path) == {"postgres": NOW - 10, "objects": NOW - 20}
    assert path.stat().st_mode & 0o777 == 0o644
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


# --- CLI create moves no gauge; its real output drives backup.sh -----------------------


def _stub_create(monkeypatch, root: Path, integrity: str) -> None:
    def create_backup(**kwargs):
        assert kwargs["root"] == root and kwargs["storage"] is None
        return {
            "backup_id": BACKUP_ID,
            "created_at": "2026-10-18T02:00:00+00:00",
            "path": str(root / "sets" / BACKUP_ID),
            "size_bytes": 1234,
            "stores": {
                "postgres": {"tables": 1},
                "objects": {"included": True, "objects": 2, "integrity": integrity},
            },
            "durations_seconds": {"total": 1.0},
        }

    monkeypatch.setattr("backend.backup.create.create_backup", create_backup)
    monkeypatch.setattr(
        "backend.config.get_settings", lambda: SimpleNamespace(database_url="postgresql://x@db/x")
    )
    monkeypatch.setenv("BACKUP_DATABASE_URL", "postgresql://admin@db/homayar")
    _use_test_key(monkeypatch)


def test_cli_create_writes_no_gauge_and_marks_nothing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    textfile = tmp_path / "collector" / "homayar_backup.prom"
    monkeypatch.setenv("HOMAYAR_BACKUP_TEXTFILE", str(textfile))
    _stub_create(monkeypatch, root, "complete")

    assert cli(["--root", str(root), "create", "--no-objects"]) == 0
    assert json.loads(capsys.readouterr().out)["backup_id"] == BACKUP_ID
    assert not textfile.exists()
    assert not (root / "sets" / f"{BACKUP_ID}.verified").exists()

    _verifiable_set(root, BACKUP_ID)
    assert cli(["--root", str(root), "verify", BACKUP_ID]) == 0
    assert set(metrics.read_values(textfile)) == {"postgres"}
    assert (root / "sets" / f"{BACKUP_ID}.verified").is_file()


_FAKE_DOCKER_REAL_CREATE = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG}"
case " $* " in
  *" ps "*) printf 'postgres\\nminio\\n' ;;
  *" create "*) cat "${FAKE_CREATE_OUTPUT}" ;;
  *" verify "*) exit "${FAKE_VERIFY_STATUS:-0}" ;;
esac
exit 0
"""


@pytest.mark.parametrize(
    ("integrity", "verify_status", "expected_exit"),
    [("degraded", "4", 3), ("degraded", "0", 3), ("complete", "0", 0)],
)
def test_backup_sh_reads_the_real_cli_create_output(
    tmp_path, monkeypatch, capsys, integrity, verify_status, expected_exit
):
    _stub_create(monkeypatch, tmp_path / "cli-root", integrity)
    capsys.readouterr()
    assert cli(["--root", str(tmp_path / "cli-root"), "create", "--no-objects"]) == 0
    create_output = tmp_path / "create-output.json"
    create_output.write_text(capsys.readouterr().out)

    script = Path(__file__).resolve().parents[3] / "infra" / "scripts" / "backup.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(_FAKE_DOCKER_REAL_CREATE)
    (bin_dir / "docker").chmod(0o755)
    for name in ("root", "offsite", "collector"):
        (tmp_path / name).mkdir()
    (tmp_path / "backup.key").write_text("k:" + base64.b64encode(b"\x01" * 32).decode())
    log = tmp_path / "docker.log"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BACKUP_", "COMPOSE_", "NODE_EXPORTER_", "EGRESS_"))
    }
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "BACKUP_ROOT": str(tmp_path / "root"),
            "BACKUP_KEY_FILE": str(tmp_path / "backup.key"),
            "BACKUP_OFFSITE_PATH": str(tmp_path / "offsite"),
            "NODE_EXPORTER_TEXTFILE_DIR": str(tmp_path / "collector"),
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_CREATE_OUTPUT": str(create_output),
            "FAKE_VERIFY_STATUS": verify_status,
        }
    )
    result = subprocess.run(  # noqa: S603 - the repository's own script
        ["bash", str(script)],  # noqa: S607 - bash from PATH, as cron runs it
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_exit, result.stderr
    calls = log.read_text()
    assert f"verify {BACKUP_ID}" in calls and " prune" in calls
    if expected_exit == 3:
        assert f"backup {BACKUP_ID} has degraded object integrity" in result.stderr
    else:
        assert f"Backup {BACKUP_ID} completed." in result.stdout
