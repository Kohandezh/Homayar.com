"""HOM-300: the operator scripts check the admin DSN file before they start Docker.

No database and no containers: a fake ``docker`` first on PATH records every call
plus the ``BACKUP_DATABASE_URL_FILE`` value Compose would receive, so a check that
runs too late (or not at all) is visible. The DSN files hold a placeholder only;
it must never reach stdout, stderr or the recorded Docker calls.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = {
    "backup.sh": REPO_ROOT / "infra" / "scripts" / "backup.sh",
    "restore-isolated.sh": REPO_ROOT / "infra" / "scripts" / "restore-isolated.sh",
}
BACKUP_ID = "20261023T020000Z-0a1b2c3d"
# Placeholder content only (A9): this string must never appear in any output.
PLACEHOLDER_DSN = (
    "postgresql+psycopg://placeholder-user:placeholder-secret@postgres:5432/placeholder"
)
DENIED = "Backup admin DSN file (BACKUP_DATABASE_URL_FILE) is missing or not a readable file:"

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG}"
printf 'BACKUP_DATABASE_URL_FILE=%s\\n' "${BACKUP_DATABASE_URL_FILE-<unset>}" \\
  >> "${FAKE_DOCKER_LOG}"
case " $* " in
  *" ps "*) exit 0 ;;
esac
exit 1
"""


def _run(
    script: Path,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
    flags: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run one script in a complete environment: with the DSN-file check passing
    (or absent) it goes on to ``docker compose`` (a fake that records every call,
    answers ``ps`` with nothing running and fails every other command, so the run
    ends at the first real Docker step). The working directory is elsewhere, never
    the Compose directory. Every run also asserts the placeholder DSN never leaks
    into stdout, stderr or the recorded calls (A9)."""
    if not script.exists():
        pytest.skip("infra/scripts is not part of this checkout")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    key_file = tmp_path / "backup.key"
    key_file.write_text("placeholder", encoding="utf-8")
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir(exist_ok=True)
    (compose_dir / "compose.onprem.yaml").write_text("", encoding="utf-8")
    backup_root = tmp_path / "backups"
    calls = tmp_path / "docker-calls.log"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    env = {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path),
        "FAKE_DOCKER_LOG": str(calls),
        "BACKUP_ROOT": str(backup_root),
        "BACKUP_KEY_FILE": str(key_file),
        "COMPOSE_FILE": str(compose_dir / "compose.onprem.yaml"),
    }
    if script.name == "restore-isolated.sh":
        journal = tmp_path / "journal"
        journal.mkdir(exist_ok=True)
        env.update(
            COMPOSE_PROJECT_NAME="homayar-restore-test",
            POSTGRES_DB="homayar",
            TOMBSTONE_JOURNAL_PATH=str(journal),
        )
    else:
        textfile_dir = tmp_path / "textfile"
        textfile_dir.mkdir(exist_ok=True)
        env["NODE_EXPORTER_TEXTFILE_DIR"] = str(textfile_dir)
    env.update(extra_env or {})
    args = [bash, str(script)]
    if script.name == "restore-isolated.sh":
        args += ["--backup", BACKUP_ID, *flags]
    else:
        args += ["--no-offsite", *flags]
    completed = subprocess.run(  # noqa: S603 - fixed argv, values only through env
        args,
        env=env,
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls_text = calls.read_text() if calls.exists() else ""
    for output in (completed.stdout, completed.stderr, calls_text):
        assert PLACEHOLDER_DSN not in output, output
    return completed, calls, backup_root


def _docker_calls(calls: Path) -> str:
    return calls.read_text() if calls.exists() else ""


def _first_call_argv(calls: Path) -> str:
    for line in _docker_calls(calls).splitlines():
        if not line.startswith("BACKUP_DATABASE_URL_FILE="):
            return line
    return ""


def _docker_env_value(calls: Path) -> str:
    for line in _docker_calls(calls).splitlines():
        if line.startswith("BACKUP_DATABASE_URL_FILE="):
            return line.partition("=")[2]
    return "<no docker call>"


def _reached_first_docker_call(
    script: Path, completed: subprocess.CompletedProcess, calls: Path
) -> None:
    """The env checks passed: the script reached ``docker compose ... ps`` and then
    failed at the first real Docker step (the fake lists nothing running)."""
    assert completed.returncode != 0
    assert DENIED not in completed.stderr, completed.stderr
    assert " ps " in f" {_first_call_argv(calls)} ", _docker_calls(calls)
    if script.name == "backup.sh":
        assert "postgres is not running" in completed.stderr, completed.stderr
    else:
        assert " up -d --wait postgres " in _docker_calls(calls), _docker_calls(calls)


def _refused_before_docker(
    completed: subprocess.CompletedProcess, calls: Path, expected_path: str
) -> None:
    assert completed.returncode == 1
    assert f"ERROR: {DENIED} {expected_path}" in completed.stderr.splitlines(), completed.stderr
    assert not calls.exists(), _docker_calls(calls)


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a1_unset_reaches_docker_without_the_variable(script_name, tmp_path):
    completed, calls, _ = _run(SCRIPTS[script_name], tmp_path)
    _reached_first_docker_call(SCRIPTS[script_name], completed, calls)
    assert _docker_env_value(calls) == "<unset>"


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a2_empty_is_the_same_as_unset(script_name, tmp_path):
    completed, calls, _ = _run(SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": ""})
    _reached_first_docker_call(SCRIPTS[script_name], completed, calls)
    assert _docker_env_value(calls) == ""


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a3_a_missing_absolute_path_is_refused_before_docker(script_name, tmp_path):
    missing = str(tmp_path / "no-such-dsn")
    completed, calls, backup_root = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": missing}
    )
    _refused_before_docker(completed, calls, missing)
    if script_name == "restore-isolated.sh":
        assert not (backup_root / "restores").exists()


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a4_a_directory_is_refused_like_a_missing_file(script_name, tmp_path):
    directory = tmp_path / "docker-made-a-directory"
    directory.mkdir()
    completed, calls, backup_root = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": str(directory)}
    )
    _refused_before_docker(completed, calls, str(directory))
    if script_name == "restore-isolated.sh":
        assert not (backup_root / "restores").exists()


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a5_an_unreadable_file_is_refused(script_name, tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("everything is readable for root")
    unreadable = tmp_path / "dsn-mode-000"
    unreadable.write_text(PLACEHOLDER_DSN + "\n", encoding="utf-8")
    unreadable.chmod(0o000)
    completed, calls, backup_root = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": str(unreadable)}
    )
    _refused_before_docker(completed, calls, str(unreadable))
    if script_name == "restore-isolated.sh":
        assert not (backup_root / "restores").exists()


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a6_a_readable_absolute_file_passes_and_compose_sees_it(script_name, tmp_path):
    dsn_file = tmp_path / "dsn.txt"
    dsn_file.write_text(PLACEHOLDER_DSN + "\n", encoding="utf-8")
    completed, calls, _ = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": str(dsn_file)}
    )
    _reached_first_docker_call(SCRIPTS[script_name], completed, calls)
    assert _docker_env_value(calls) == str(dsn_file)


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a7_a_relative_name_resolves_against_the_compose_directory(script_name, tmp_path):
    dsn_file = tmp_path / "compose" / "backup-database-url"
    dsn_file.parent.mkdir(exist_ok=True)
    dsn_file.write_text(PLACEHOLDER_DSN + "\n", encoding="utf-8")
    completed, calls, _ = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": "backup-database-url"}
    )
    _reached_first_docker_call(SCRIPTS[script_name], completed, calls)
    assert _docker_env_value(calls) == str(dsn_file.resolve())


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a8_a_relative_name_missing_there_shows_the_resolved_path(script_name, tmp_path):
    # The file exists relative to the working directory, not in the Compose directory.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / "backup-database-url").write_text(PLACEHOLDER_DSN + "\n", encoding="utf-8")
    resolved = str((tmp_path / "compose" / "backup-database-url").resolve())
    completed, calls, backup_root = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": "backup-database-url"}
    )
    _refused_before_docker(completed, calls, resolved)
    if script_name == "restore-isolated.sh":
        assert not (backup_root / "restores").exists()


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_a9_the_placeholder_content_never_leaks(script_name, tmp_path):
    dsn_file = tmp_path / "dsn.txt"
    dsn_file.write_text(PLACEHOLDER_DSN + "\n", encoding="utf-8")
    completed, calls, _ = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": str(dsn_file)}
    )
    assert PLACEHOLDER_DSN not in completed.stdout
    assert PLACEHOLDER_DSN not in completed.stderr
    assert PLACEHOLDER_DSN not in _docker_calls(calls)


def test_a10_the_accept_missing_refusal_still_wins(tmp_path):
    completed, calls, backup_root = _run(
        SCRIPTS["restore-isolated.sh"],
        tmp_path,
        {"BACKUP_DATABASE_URL_FILE": str(tmp_path / "no-such-dsn")},
        flags=("--accept-missing", "INC-42"),
    )
    assert completed.returncode == 1
    assert "Run without the override first" in completed.stderr, completed.stderr
    assert DENIED not in completed.stderr, completed.stderr
    assert not calls.exists(), _docker_calls(calls)
    assert not (backup_root / "restores").exists()


@pytest.mark.parametrize("script_name", sorted(SCRIPTS))
def test_the_dsn_refusal_wins_over_the_next_environment_check(script_name, tmp_path):
    missing = str(tmp_path / "no-such-dsn")
    if script_name == "backup.sh":
        later_message = "NODE_EXPORTER_TEXTFILE_DIR does not exist"
        later_env = {"NODE_EXPORTER_TEXTFILE_DIR": str(tmp_path / "no-such-textfile-dir")}
    else:
        later_message = "TOMBSTONE_JOURNAL_PATH must be the replicated journal directory"
        later_env = {"TOMBSTONE_JOURNAL_PATH": str(tmp_path / "no-such-journal")}
    completed, calls, backup_root = _run(
        SCRIPTS[script_name], tmp_path, {"BACKUP_DATABASE_URL_FILE": missing, **later_env}
    )
    _refused_before_docker(completed, calls, missing)
    assert later_message not in completed.stderr, completed.stderr
    if script_name == "restore-isolated.sh":
        assert not (backup_root / "restores").exists()
    # Control: the same environment without BACKUP_DATABASE_URL_FILE fails at the later
    # check, so that environment really is broken there and the DSN message wins by order.
    control, control_calls, _ = _run(SCRIPTS[script_name], tmp_path, dict(later_env))
    assert control.returncode == 1
    assert later_message in control.stderr, control.stderr
    assert not control_calls.exists(), _docker_calls(control_calls)
