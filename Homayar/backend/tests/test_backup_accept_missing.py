"""HOM-215: an owner may accept lost objects only for the exact set a FAIL report showed.

No database and no app: the digest helper, the ``objects`` decision, the refusals
``reconcile_restore`` and the CLI make before they load anything, and the argument check
of ``infra/scripts/restore-isolated.sh`` (a fake ``docker`` first on PATH records every
call, so a call before the check is visible). The PostgreSQL round trip is in
test_backup_restore.py.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.backup import reconcile as reconcile_module
from backend.backup.__main__ import main as cli
from backend.backup.crypto import BackupKey
from backend.backup.manifest import BackupInvalid
from backend.backup.restore import RestoreRefused
from backend.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "scripts" / "restore-isolated.sh"
KEY = BackupKey("test-backup-1", b"\x11" * 32)
BACKUP_ID = "20261023T020000Z-0a1b2c3d"
OTHER_BACKUP_ID = "20261022T020000Z-0a1b2c3d"
DIGEST_FORM = re.compile(r"sha256:[0-9a-f]{64}")
# More than the 100 keys the other lists of the report keep.
MISSING = [f"tenants/w1/objects/missing-{index:03d}" for index in range(150)]
MISMATCH = [f"tenants/w1/objects/rotten-{index:03d}" for index in range(120)]
WELL_FORMED = "sha256:" + "0" * 64
# Never connected to: every test here must work without a database.
NO_DATABASE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


def _digest(missing=MISSING, mismatch=MISMATCH, backup_id=BACKUP_ID) -> str:
    return reconcile_module.missing_set_digest(backup_id, missing, mismatch)


def _objects(missing=MISSING, mismatch=MISMATCH, *, reference=None, digest=None, **kwargs):
    return reconcile_module.objects_check(
        BACKUP_ID,
        missing=missing,
        content_mismatch=mismatch,
        missing_explained=kwargs.pop("missing_explained", []),
        removal_failed=kwargs.pop("removal_failed", []),
        accept_missing=reference,
        accept_missing_digest=digest,
        **kwargs,
    )


# --- the digest -----------------------------------------------------------------------


def test_the_digest_is_the_same_for_the_same_sets_in_any_order():
    digest = _digest()
    assert DIGEST_FORM.fullmatch(digest)
    assert _digest(list(reversed(MISSING)), MISMATCH[::-1]) == digest
    assert _digest(iter(MISSING), tuple(MISMATCH)) == digest
    assert _digest(MISSING + MISSING[:3], MISMATCH) == digest  # a set: a repeat adds nothing


@pytest.mark.parametrize(
    "missing, mismatch",
    [
        pytest.param(MISSING[1:], MISMATCH, id="a missing key removed"),
        pytest.param(MISSING, MISMATCH[1:], id="a mismatch key removed"),
        pytest.param(MISSING + ["tenants/w1/objects/extra"], MISMATCH, id="a key added"),
        pytest.param(MISSING[1:] + ["tenants/w1/objects/other"], MISMATCH, id="a key replaced"),
        pytest.param(MISSING[1:], MISMATCH + MISSING[:1], id="a key moved to mismatch"),
        pytest.param(MISSING + MISMATCH[:1], MISMATCH[1:], id="a key moved to missing"),
        pytest.param([], [], id="nothing lost"),
    ],
)
def test_the_digest_changes_with_any_other_set(missing, mismatch):
    assert _digest(missing, mismatch) != _digest()


def test_the_digest_is_bound_to_the_backup():
    assert _digest(backup_id=OTHER_BACKUP_ID) != _digest()


@pytest.mark.parametrize(
    "first, second",
    [
        pytest.param((["a,b"], []), (["a", "b"], []), id="separator inside a key"),
        pytest.param((['a"', "b"], []), (['a", "b'], []), id="quote inside a key"),
        pytest.param((["x"], []), ([], ["x"]), id="same key, other list"),
        pytest.param(([""], []), ([], []), id="empty key"),
    ],
)
def test_the_digest_encoding_is_unambiguous(first, second):
    assert _digest(*first) != _digest(*second)


# --- the objects decision ---------------------------------------------------------------


def test_a_fail_lists_every_lost_key_and_the_digest_of_the_set():
    check = _objects(MISSING[::-1], MISMATCH[::-1])
    details = check["details"]
    assert check["status"] == "FAIL"
    assert details["required_missing"] == 150 and details["required_missing_keys"] == MISSING
    assert details["content_mismatch"] == 120 and details["content_mismatch_keys"] == MISMATCH
    assert details["missing_set_digest"] == _digest()
    assert details["accepted_missing"] is None and details["accept_missing_refused"] is None


@pytest.mark.parametrize(
    "missing, mismatch",
    [
        pytest.param(MISSING, MISMATCH, id="both"),
        pytest.param(MISSING, [], id="missing only"),
        pytest.param([], MISMATCH, id="mismatch only"),
    ],
)
def test_the_digest_of_the_reviewed_set_accepts_it_and_records_every_key(missing, mismatch):
    digest = _digest(missing, mismatch)
    check = _objects(missing, mismatch, reference="INC-42", digest=digest)
    details = check["details"]
    assert check["status"] == "PASS"
    assert details["missing_set_digest"] == digest
    assert details["accepted_missing"] == {
        "reference": "INC-42",
        "digest": digest,
        "missing_keys": missing,
        "content_mismatch_keys": mismatch,
    }
    assert details["accept_missing_refused"] is None


def test_an_accepted_set_still_fails_when_lingering_bytes_could_not_be_removed():
    check = _objects(reference="INC-42", digest=_digest(), removal_failed=["tenants/w1/x"])
    assert check["status"] == "FAIL"
    assert check["details"]["removal_failed_keys"] == ["tenants/w1/x"]


@pytest.mark.parametrize(
    "supplied",
    [
        pytest.param(lambda: _digest(MISSING[1:], MISMATCH), id="one key fewer"),
        pytest.param(lambda: _digest(MISSING + ["tenants/w1/objects/x"], MISMATCH), id="one more"),
        pytest.param(lambda: _digest(MISSING[1:], MISMATCH + MISSING[:1]), id="a key moved"),
        pytest.param(lambda: _digest(backup_id=OTHER_BACKUP_ID), id="another backup"),
        pytest.param(lambda: _digest(["tenants/w2/objects/x"], []), id="another set"),
        pytest.param(lambda: WELL_FORMED, id="well formed, of nothing"),
    ],
)
def test_a_digest_of_any_other_set_accepts_nothing(supplied):
    supplied = supplied()
    check = _objects(reference="INC-42", digest=supplied)
    details = check["details"]
    assert check["status"] == "FAIL"
    assert details["accepted_missing"] is None
    assert details["accept_missing_refused"] == {
        "reference": "INC-42",
        "supplied_digest": supplied,
        "actual_digest": _digest(),
    }


@pytest.mark.parametrize(
    "supplied",
    [
        pytest.param(lambda: _digest(), id="digest of an earlier set"),
        pytest.param(lambda: _digest([], []), id="digest of the empty set"),
        pytest.param(lambda: WELL_FORMED, id="any well formed"),
    ],
)
def test_an_override_when_nothing_is_lost_is_refused(supplied):
    supplied = supplied()
    check = _objects([], [], reference="INC-42", digest=supplied)
    details = check["details"]
    assert check["status"] == "FAIL"
    assert details["missing_set_digest"] is None and details["accepted_missing"] is None
    assert details["accept_missing_refused"] == {
        "reference": "INC-42",
        "supplied_digest": supplied,
        "actual_digest": None,
    }
    explained = [{"key": "tenants/w1/objects/gone", "reason": "deleted_after_snapshot"}]
    with_explained = _objects(
        [], [], reference="INC-42", digest=supplied, missing_explained=explained
    )
    assert with_explained["status"] == "FAIL"


def test_without_an_override_nothing_lost_passes_as_before():
    check = _objects([], [])
    assert check["status"] == "PASS"
    assert check["details"]["missing_set_digest"] is None
    assert check["details"]["accept_missing_refused"] is None
    explained = [{"key": "tenants/w1/objects/gone", "reason": "deleted_after_snapshot"}]
    assert _objects([], [], missing_explained=explained)["status"] == "WARN"


@pytest.mark.parametrize("blank", ["", " ", " \t\n"])
def test_a_blank_reference_is_no_override(blank):
    accepted = _objects(reference="INC-42", digest=_digest())  # control
    assert accepted["status"] == "PASS" and accepted["details"]["accepted_missing"]
    check = _objects(reference=blank, digest=_digest())
    assert check["status"] == "FAIL"
    assert check["details"]["accepted_missing"] is None
    assert check["details"]["accept_missing_refused"] is None
    assert _objects([], [], reference=blank)["status"] == "PASS"  # as without an override


# --- refusals before anything is loaded ---------------------------------------------------

UNBOUND = [
    pytest.param("INC-42", None, "accept_missing_needs_digest", id="reference only"),
    pytest.param(None, WELL_FORMED, "accept_missing_needs_reference", id="digest only"),
    pytest.param(" \t", WELL_FORMED, "accept_missing_needs_reference", id="blank reference"),
    pytest.param("INC-42", "sha256:" + "a" * 63, "accept_missing_digest_malformed", id="63 hex"),
    pytest.param("INC-42", "sha256:" + "a" * 65, "accept_missing_digest_malformed", id="65 hex"),
    pytest.param("INC-42", "sha256:" + "A" * 64, "accept_missing_digest_malformed", id="upper"),
    pytest.param("INC-42", "a" * 64, "accept_missing_digest_malformed", id="no prefix"),
    pytest.param("INC-42", WELL_FORMED + "\n", "accept_missing_digest_malformed", id="newline"),
]


def _reconcile_nothing(tmp_path: Path, reference, digest):
    """Every other argument points at nothing: a refusal must come before any of it."""
    return reconcile_module.reconcile_restore(
        set_dir=tmp_path / "no-such-set",
        key=KEY,
        admin_url=NO_DATABASE,
        staging_database="hy_none_restoring",
        settings=None,
        storage=None,
        work_dir=tmp_path / "no-such-work-dir",
        storage_target="local:none",
        accept_missing=reference,
        accept_missing_digest=digest,
    )


@pytest.mark.parametrize("reference, digest, code", UNBOUND)
def test_reconcile_refuses_an_unbound_override_before_anything_else(
    tmp_path, reference, digest, code
):
    with pytest.raises(RestoreRefused) as refused:
        _reconcile_nothing(tmp_path, reference, digest)
    assert refused.value.code == code
    assert "previous FAIL report" in refused.value.message
    other_flag = (
        "--accept-missing REF"
        if code == "accept_missing_needs_reference"
        else "--accept-missing-digest"
    )
    assert other_flag in refused.value.message


def test_reconcile_takes_a_reference_with_a_well_formed_digest(tmp_path):
    # Past the override check, the first step reads the (absent) set.
    with pytest.raises(BackupInvalid) as invalid:
        _reconcile_nothing(tmp_path, "INC-42", WELL_FORMED)
    assert invalid.value.code == "manifest_missing"


@pytest.mark.parametrize("reference", [None, "", " \t"])
def test_reconcile_treats_a_blank_reference_alone_as_no_override(tmp_path, reference):
    with pytest.raises(BackupInvalid) as invalid:
        _reconcile_nothing(tmp_path, reference, None)
    assert invalid.value.code == "manifest_missing"


def _offline(tmp_path: Path, monkeypatch) -> None:
    """The CLI's settings, key and admin DSN without the conftest ``settings`` fixture
    (it resets the PostgreSQL test schema): local storage under tmp_path, a database
    URL nothing connects to."""
    settings = Settings(
        environment="test",
        database_url=NO_DATABASE,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        storage_backend="local",
        storage_local_root=str(tmp_path / "objects"),
        upload_quarantine_dir=str(tmp_path / "quarantine"),
        upload_dir=str(tmp_path / "uploads"),
        _env_file=None,
    )
    monkeypatch.setattr("backend.config.get_settings", lambda: settings)
    monkeypatch.delenv("BACKUP_DATABASE_URL", raising=False)
    monkeypatch.delenv("BACKUP_DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv(
        "HOMAYAR_BACKUP_KEY", f"{KEY.key_id}:{base64.b64encode(KEY.secret).decode()}"
    )


def _cli(tmp_path: Path, capsys, *flags: str) -> tuple[int, dict]:
    capsys.readouterr()
    code = cli(
        [
            "--root",
            str(tmp_path / "backups"),
            "reconcile",
            BACKUP_ID,
            "--staging-db",
            "hy_none_restoring",
            "--work-dir",
            str(tmp_path / "work"),
            *flags,
        ]
    )
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("reference, digest, code", UNBOUND)
def test_the_cli_refuses_an_unbound_override_before_loading_the_key(
    tmp_path, capsys, monkeypatch, reference, digest, code
):
    # No key at all: a later refusal would be "backup_key".
    monkeypatch.delenv("HOMAYAR_BACKUP_KEY", raising=False)
    monkeypatch.delenv("HOMAYAR_BACKUP_KEY_FILE", raising=False)
    flags = [] if reference is None else ["--accept-missing", reference]
    flags += [] if digest is None else ["--accept-missing-digest", digest]
    exit_code, output = _cli(tmp_path, capsys, *flags)
    assert exit_code == 1
    assert output["error"] == code and "previous FAIL report" in output["message"]


def test_the_cli_takes_a_reference_with_a_well_formed_digest(tmp_path, capsys, monkeypatch):
    _offline(tmp_path, monkeypatch)
    exit_code, output = _cli(
        tmp_path, capsys, "--accept-missing", "INC-42", "--accept-missing-digest", WELL_FORMED
    )
    # Past both override checks, the first step reads the (absent) set.
    assert exit_code == 1 and output["error"] == "manifest_missing"


def test_the_cli_passes_the_reference_and_the_digest_to_reconcile(tmp_path, capsys, monkeypatch):
    seen: dict = {}

    def reconcile_restore(**kwargs):
        seen.update(kwargs)
        return {"status": "PASS", "statuses": {}, "path": "report.json", "timings_seconds": {}}

    _offline(tmp_path, monkeypatch)
    monkeypatch.setattr(reconcile_module, "reconcile_restore", reconcile_restore)
    exit_code, _ = _cli(
        tmp_path, capsys, "--accept-missing", "INC-42", "--accept-missing-digest", WELL_FORMED
    )
    assert exit_code == 0
    assert seen["accept_missing"] == "INC-42" and seen["accept_missing_digest"] == WELL_FORMED


# --- restore-isolated.sh -----------------------------------------------------------------

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG}"
case " $* " in
  *" ps "*) exit 0 ;;
esac
exit 1
"""


def _script(tmp_path: Path, *flags: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run the script with a complete environment: without the override check it would
    go on to ``docker compose`` (a fake that records the call, lists nothing running and
    fails every other command)."""
    if not SCRIPT.exists():
        pytest.skip("infra/scripts is not part of this checkout")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    key_file = tmp_path / "backup.key"
    key_file.write_text("placeholder", encoding="utf-8")
    journal = tmp_path / "journal"
    journal.mkdir()
    backup_root = tmp_path / "backups"
    calls = tmp_path / "docker-calls.log"
    env = {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path),
        "FAKE_DOCKER_LOG": str(calls),
        "COMPOSE_PROJECT_NAME": "homayar-restore-test",
        "POSTGRES_DB": "homayar",
        "BACKUP_ROOT": str(backup_root),
        "BACKUP_KEY_FILE": str(key_file),
        "TOMBSTONE_JOURNAL_PATH": str(journal),
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, values only through env
        [bash, str(SCRIPT), "--backup", BACKUP_ID, *flags],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed, calls, backup_root


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--accept-missing", "INC-42"], id="reference only"),
        pytest.param(["--accept-missing-digest", WELL_FORMED], id="digest only"),
        pytest.param(
            ["--accept-missing", " ", "--accept-missing-digest", WELL_FORMED], id="blank reference"
        ),
        pytest.param(
            ["--accept-missing", "INC-42", "--accept-missing-digest", "sha256:" + "a" * 63],
            id="63 hex",
        ),
        pytest.param(
            ["--accept-missing", "INC-42", "--accept-missing-digest", "sha256:" + "A" * 64],
            id="upper",
        ),
        pytest.param(
            ["--accept-missing", "INC-42", "--accept-missing-digest", "a" * 64], id="no prefix"
        ),
    ],
)
def test_the_script_refuses_an_unbound_override_before_any_compose_call(tmp_path, flags):
    completed, calls, backup_root = _script(tmp_path, *flags)
    assert completed.returncode != 0
    assert "Run without the override first" in completed.stderr, completed.stderr
    assert "missing_set_digest" in completed.stderr
    assert not calls.exists(), calls.read_text()
    assert not (backup_root / "restores").exists()  # no timeline, no metrics dir


def test_the_script_takes_both_and_records_them_in_the_timeline(tmp_path):
    digest = _digest()
    completed, calls, backup_root = _script(
        tmp_path, "--accept-missing", "INC-42", "--accept-missing-digest", digest
    )
    # Past the argument checks it lists the running services, then the (fake) store
    # start fails.
    assert completed.returncode != 0
    assert "Run without the override first" not in completed.stderr
    assert calls.exists() and " up -d --wait postgres " in calls.read_text()
    timeline = (backup_root / "restores" / BACKUP_ID / "timeline.log").read_text()
    assert "owner_override --accept-missing=INC-42\n" in timeline
    assert f"owner_override --accept-missing-digest={digest}\n" in timeline
