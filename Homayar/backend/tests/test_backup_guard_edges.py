"""HOM-221: edge cases of the retired-database and host-path guards.

No database and no containers. The retired-note checks call ``_retired_entry``
and the CLI with a fake catalog; the script checks run ``backup.sh`` and
``restore-isolated.sh`` with a fake ``docker`` first on PATH that records every
call and the host paths Compose would receive. The PostgreSQL cases (large
objects, the CLI against a real server, the report digest) are in
``test_backup_restore.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.backup import reconcile as reconcile_module
from backend.backup.__main__ import main as cli
from backend.backup.logical import database_name
from backend.backup.reconcile import RETIRED_PREFIX, _retired_entry, drop_retired
from backend.backup.restore import RestoreRefused

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SH = REPO_ROOT / "infra" / "scripts" / "backup.sh"
RESTORE_ISOLATED_SH = REPO_ROOT / "infra" / "scripts" / "restore-isolated.sh"
BACKUP_ID = "20261023T020000Z-0a1b2c3d"
NOW = datetime(2026, 10, 30, tzinfo=UTC)
MALFORMED = {"malformed_note": True, "overdue": True}


def _nested_list(depth: int) -> list:
    value: list = []
    for _ in range(depth):
        value = [value]
    return value


# --- C2: a retired note is a flat JSON object or it is malformed ----------------------


def test_a_note_too_deep_to_parse_is_malformed_not_a_crash():
    comment = RETIRED_PREFIX + "[" * 100000 + "]" * 100000
    name = "homayar_retired_20261018000000"
    assert _retired_entry(name, comment, NOW) == {"database": name, **MALFORMED}


@pytest.mark.parametrize(
    "note",
    [
        {"drop_after": _nested_list(5000)},
        {"drop_after": "2026-11-20T00:00:00+00:00", "extra": [1]},
        {"drop_after": "2026-11-20T00:00:00+00:00", "extra": {}},
    ],
    ids=["deep-list-value", "list-value", "object-value"],
)
def test_a_note_with_a_non_scalar_value_is_malformed_and_prints(note):
    name = "homayar_retired_20261018000000"
    entry = _retired_entry(name, RETIRED_PREFIX + json.dumps(note), NOW)
    assert entry == {"database": name, **MALFORMED}
    json.dumps(entry, indent=2, sort_keys=True, default=str)  # what the CLI prints


def test_a_promote_note_keeps_its_fields_and_its_deadline():
    note = {
        "retired_at": "2026-10-29T00:00:00+00:00",
        "drop_after": "2026-11-05T00:00:00+00:00",
        "replaced_by_backup": BACKUP_ID,
    }
    name = "homayar_retired_20261029000000"
    entry = _retired_entry(name, RETIRED_PREFIX + json.dumps(note, sort_keys=True), NOW)
    assert entry == {**note, "database": name, "overdue": False}
    scalars = {"drop_after": "2026-10-25T00:00:00+00:00", "n": 1, "x": 1.5, "b": True, "z": None}
    late = _retired_entry(name, RETIRED_PREFIX + json.dumps(scalars), NOW)
    assert late == {**scalars, "database": name, "overdue": True}


def _fake_catalog(monkeypatch, rows: list[tuple[str, str | None]]) -> None:
    """``list_retired`` reads this catalog instead of a server."""

    class _Result:
        def fetchall(self):
            return rows

    class _Connection:
        def execute(self, *_args, **_kwargs):
            return _Result()

    @contextmanager
    def fake_connect(_url, autocommit=False):
        yield _Connection()

    monkeypatch.setattr(reconcile_module, "connect", fake_connect)


def test_the_retired_command_prints_a_deep_note_as_malformed(monkeypatch, capsys):
    monkeypatch.setenv("BACKUP_DATABASE_URL", "postgresql://admin@127.0.0.1:1/postgres")
    deep = RETIRED_PREFIX + "[" * 100000 + "]" * 100000
    nested = RETIRED_PREFIX + json.dumps({"drop_after": _nested_list(5000)})
    _fake_catalog(
        monkeypatch,
        [
            ("a_retired_20261018000000", deep),
            ("b_retired_20261018000000", nested),
            ("homayar", None),
        ],
    )
    capsys.readouterr()
    assert cli(["retired"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"database": "a_retired_20261018000000", **MALFORMED},
        {"database": "b_retired_20261018000000", **MALFORMED},
    ]


# --- C3: drop-retired always protects the application's database ---------------------


def test_every_given_live_database_is_protected_before_any_query(monkeypatch):
    def no_server(*_args, **_kwargs):
        raise AssertionError("refused before any connection")

    monkeypatch.setattr(reconcile_module, "connect", no_server)
    admin = "postgresql://admin@127.0.0.1:1/postgres"
    live = ["homayar", "other_retired_20261018000000"]
    for name in live:
        with pytest.raises(RestoreRefused) as refused:
            drop_retired(admin, name, confirm=name, live_databases=live)
        assert refused.value.code == "live_database"


@pytest.mark.parametrize("given", ["one-string", "list"])
def test_a_single_live_database_name_given_as_a_string_is_protected(monkeypatch, given):
    def no_server(*_args, **_kwargs):
        raise AssertionError("refused before any connection")

    monkeypatch.setattr(reconcile_module, "connect", no_server)
    name = "hy_live_retired_20261030000000"
    live = name if given == "one-string" else [name]
    with pytest.raises(RestoreRefused) as refused:
        drop_retired(
            "postgresql://admin@127.0.0.1:1/postgres", name, confirm=name, live_databases=live
        )
    assert refused.value.code == "live_database"


@pytest.mark.parametrize("extra", [[], ["--live-db", "other"]], ids=["default", "live-db"])
def test_the_cli_always_protects_the_application_database(monkeypatch, capsys, extra):
    from backend.config import get_settings

    application = database_name(get_settings().database_url)
    monkeypatch.setenv("BACKUP_DATABASE_URL", "postgresql://admin@127.0.0.1:1/postgres")
    seen: list[dict] = []

    def record(admin_url, database, **kwargs):
        seen.append({"admin_url": admin_url, "database": database, **kwargs})
        return {"dropped": database}

    monkeypatch.setattr(reconcile_module, "drop_retired", record)
    target = "x_retired_20261018000000"
    assert cli(["drop-retired", target, "--confirm", target, *extra]) == 0
    expected = [application, *extra[1:]]
    assert seen == [
        {
            "admin_url": "postgresql://admin@127.0.0.1:1/postgres",
            "database": target,
            "confirm": target,
            "live_databases": expected,
        }
    ]


# --- shell scripts with a fake docker ---------------------------------------------------

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG}"
for name in BACKUP_ROOT BACKUP_KEY_FILE TOMBSTONE_JOURNAL_PATH NODE_EXPORTER_TEXTFILE_DIR; do
  eval "value=\\${${name}-<unset>}"
  printf 'ENV %s=%s\\n' "${name}" "${value}" >> "${FAKE_DOCKER_LOG}"
done
case " $* " in
  *" ps "*) exit 0 ;;
esac
exit 1
"""


def _run(script: Path, tmp_path: Path, env: dict[str, str], *args: str, cwd: Path | None = None):
    """Run one script with a fake docker; return (completed, recorded calls or "")."""
    if not script.exists():
        pytest.skip("infra/scripts is not part of this checkout")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    calls = tmp_path / "docker-calls.log"
    full_env = {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path),
        "FAKE_DOCKER_LOG": str(calls),
        **env,
    }
    elsewhere = cwd or tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    completed = subprocess.run(  # noqa: S603 - fixed argv, values only through env
        [bash, str(script), *args],
        env=full_env,
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed, calls.read_text() if calls.exists() else ""


def _compose_dir(tmp_path: Path) -> Path:
    compose = tmp_path / "compose"
    compose.mkdir(exist_ok=True)
    (compose / "compose.onprem.yaml").write_text("", encoding="utf-8")
    return compose.resolve()


def _dirs(tmp_path: Path, *names: str) -> list[Path]:
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    return [path.resolve() for path in paths]


# --- C4: a leading // never hides one host path inside another -------------------------


def _backup_env(tmp_path: Path, root: str, offsite: str, textfile: str) -> dict[str, str]:
    key = tmp_path / "backup.key"
    key.write_text("placeholder", encoding="utf-8")
    return {
        "COMPOSE_FILE": str(_compose_dir(tmp_path) / "compose.onprem.yaml"),
        "BACKUP_ROOT": root,
        "BACKUP_KEY_FILE": str(key),
        "BACKUP_OFFSITE_PATH": offsite,
        "NODE_EXPORTER_TEXTFILE_DIR": textfile,
    }


OFFSITE_INSIDE = "ERROR: BACKUP_OFFSITE_PATH is inside BACKUP_ROOT: that is not an off-site copy."
ROOT_INSIDE = "ERROR: BACKUP_ROOT is inside BACKUP_OFFSITE_PATH: that is not an off-site copy."
TEXTFILE_INSIDE = (
    "ERROR: NODE_EXPORTER_TEXTFILE_DIR is inside BACKUP_ROOT: "
    "node_exporter cannot read it through the 0700 backup root."
)


@pytest.mark.parametrize("slashes", ["//", "///"])
@pytest.mark.parametrize("side", ["root", "inner"])
@pytest.mark.parametrize("case", ["offsite-in-root", "root-in-offsite", "textfile-in-root"])
def test_a_leading_double_slash_does_not_bypass_containment(tmp_path, case, side, slashes):
    collector, outer = _dirs(tmp_path, "collector", "outer")
    inner = _dirs(tmp_path, "outer/inner")[0]
    spelled = {"root": str(outer), "inner": str(inner)}
    spelled[side] = slashes + spelled[side].lstrip("/")
    if case == "offsite-in-root":
        env = _backup_env(tmp_path, spelled["root"], spelled["inner"], str(collector))
        expected = OFFSITE_INSIDE
    elif case == "root-in-offsite":
        env = _backup_env(tmp_path, spelled["inner"], spelled["root"], str(collector))
        expected = ROOT_INSIDE
    else:
        offsite = _dirs(tmp_path, "offsite")[0]
        env = _backup_env(tmp_path, spelled["root"], str(offsite), spelled["inner"])
        expected = TEXTFILE_INSIDE
    completed, calls = _run(BACKUP_SH, tmp_path, env)
    assert completed.returncode == 1
    assert expected in completed.stderr.splitlines(), completed.stderr
    assert calls == ""  # refused before any docker call


@pytest.mark.parametrize("spelled", ["root", "offsite", "textfile"])
def test_disjoint_directories_spelled_with_double_slash_pass_the_checks(tmp_path, spelled):
    root, offsite, collector = _dirs(tmp_path, "root", "offsite", "collector")
    paths = {"root": str(root), "offsite": str(offsite), "textfile": str(collector)}
    paths[spelled] = "/" + paths[spelled]
    env = _backup_env(tmp_path, paths["root"], paths["offsite"], paths["textfile"])
    completed, calls = _run(BACKUP_SH, tmp_path, env)
    assert completed.returncode != 0  # the fake docker lists nothing running
    assert "postgres is not running in this Compose project." in completed.stderr
    assert " ps " in f" {calls.splitlines()[0]} ", calls


# --- C5: restore-isolated.sh resolves relative host paths as Compose does --------------


def _restore_env(tmp_path: Path, **paths: str) -> dict[str, str]:
    return {
        "COMPOSE_FILE": str(_compose_dir(tmp_path) / "compose.onprem.yaml"),
        "COMPOSE_PROJECT_NAME": "homayar-restore-test",
        "POSTGRES_DB": "homayar",
        **paths,
    }


def _docker_env(calls: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in calls.splitlines():
        if line.startswith("ENV "):
            name, _, value = line[4:].partition("=")
            values.setdefault(name, value)
    return values


def test_relative_paths_are_resolved_against_the_compose_directory(tmp_path):
    compose = _compose_dir(tmp_path)
    (compose / "keys").mkdir()
    (compose / "keys" / "backup.key").write_text("placeholder", encoding="utf-8")
    (compose / "journal").mkdir()
    env = _restore_env(
        tmp_path,
        BACKUP_ROOT="rel-backups",
        BACKUP_KEY_FILE="keys/backup.key",
        TOMBSTONE_JOURNAL_PATH="journal",
    )
    completed, calls = _run(RESTORE_ISOLATED_SH, tmp_path, env, "--backup", BACKUP_ID)
    assert "not readable" not in completed.stderr, completed.stderr
    assert "TOMBSTONE_JOURNAL_PATH must be" not in completed.stderr, completed.stderr
    assert " up -d --wait postgres " in calls, completed.stderr  # every check passed
    restores = compose / "rel-backups" / "restores" / BACKUP_ID
    assert "restore START" in (restores / "timeline.log").read_text()
    assert (restores / "metrics").is_dir()
    assert not list((tmp_path / "elsewhere").rglob("restores"))
    assert _docker_env(calls) == {
        "BACKUP_ROOT": str(compose / "rel-backups"),
        "BACKUP_KEY_FILE": str(compose / "keys" / "backup.key"),
        "TOMBSTONE_JOURNAL_PATH": str(compose / "journal"),
        "NODE_EXPORTER_TEXTFILE_DIR": str(restores / "metrics"),
    }


def test_a_relative_journal_is_optional_with_accept_no_journal(tmp_path):
    compose = _compose_dir(tmp_path)
    (compose / "backup.key").write_text("placeholder", encoding="utf-8")
    env = _restore_env(tmp_path, BACKUP_ROOT="rel-backups", BACKUP_KEY_FILE="backup.key")
    completed, calls = _run(
        RESTORE_ISOLATED_SH,
        tmp_path,
        env,
        "--backup",
        BACKUP_ID,
        "--accept-no-journal",
        "owner-ref",
    )
    assert " up -d --wait postgres " in calls, completed.stderr
    assert _docker_env(calls)["TOMBSTONE_JOURNAL_PATH"] == "<unset>"


@pytest.mark.parametrize("which", ["key", "journal"])
def test_a_relative_path_that_exists_only_under_the_current_directory_is_refused(tmp_path, which):
    compose = _compose_dir(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "only-here").mkdir(parents=True)
    (elsewhere / "only-here" / "backup.key").write_text("placeholder", encoding="utf-8")
    (compose / "backup.key").write_text("placeholder", encoding="utf-8")
    (compose / "journal").mkdir()
    if which == "key":
        paths = {"BACKUP_KEY_FILE": "only-here/backup.key", "TOMBSTONE_JOURNAL_PATH": "journal"}
        expected = f"ERROR: Backup key file is not readable: {compose}/only-here/backup.key"
    else:
        paths = {"BACKUP_KEY_FILE": "backup.key", "TOMBSTONE_JOURNAL_PATH": "only-here"}
        expected = (
            "ERROR: TOMBSTONE_JOURNAL_PATH must be the replicated journal directory: "
            f"{compose}/only-here"
        )
    env = _restore_env(tmp_path, BACKUP_ROOT=str(tmp_path / "backups"), **paths)
    completed, calls = _run(RESTORE_ISOLATED_SH, tmp_path, env, "--backup", BACKUP_ID)
    assert completed.returncode == 1
    assert expected in completed.stderr.splitlines(), completed.stderr
    assert calls == ""


@pytest.mark.parametrize("script", [BACKUP_SH, RESTORE_ISOLATED_SH], ids=lambda p: p.name)
def test_help_works_without_the_compose_directory(tmp_path, script):
    env = {"COMPOSE_FILE": str(tmp_path / "missing" / "compose.onprem.yaml")}
    env["BACKUP_ROOT"] = str(tmp_path / "backups")
    completed, calls = _run(script, tmp_path, env, "--help")
    assert completed.returncode == 0, completed.stderr
    assert "Usage:" in completed.stdout and calls == ""
