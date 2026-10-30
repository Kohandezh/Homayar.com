"""MinIO keys reach ``mc`` without a process argument (HOM-340).

On a shared host any local user can read process arguments (``ps``, ``/proc/<pid>/cmdline``).
So ``minio-bootstrap`` (``compose.onprem.yaml``) and the ``minio-tools`` scripts of
``backup-minio.sh`` and ``restore-minio.sh`` must give ``mc``:

* the root keys through ``MC_HOST_<alias>``, set with the ``export`` builtin (no new process);
* the app keys on stdin of ``mc admin user add`` (the pinned ``mc`` reads them from a pipe);
* never a secret as an argument, and never the app access key on stdout (the container log).

The pinned ``mc`` (2025-04-16) reads ``MC_HOST_<alias>`` with a regex and no percent-decoding:
the access key ends at the first ``:``, the secret at the last ``@``, and a second ``:`` turns
the rest into a session token. So the scripts reject a ``:`` or a line break in the root keys,
and a line break in the app keys, before any ``mc`` call. Every other character passes as is.

Each test runs the real script text with ``sh`` and a fake ``mc`` on ``PATH`` that records its
argv, its ``MC_HOST_*`` variables and its stdin. Every value below is a placeholder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infra" / "compose" / "compose.onprem.yaml"
BACKUP_SCRIPT = REPO_ROOT / "infra" / "scripts" / "backup-minio.sh"
RESTORE_SCRIPT = REPO_ROOT / "infra" / "scripts" / "restore-minio.sh"

ROOT_USER = "unit-root-user"
ROOT_PASSWORD = "unit-root-password-0000"  # noqa: S105 - placeholder
ACCESS_KEY = "unit-app-access-key"
SECRET_KEY = "unit-app-secret-key-0000"  # noqa: S105 - placeholder
# Characters a URL would treat as special. The pinned mc takes them raw from MC_HOST_*.
SPECIAL_PASSWORD = "unit@root/pw%23#q?a+b=c&d ~e"  # noqa: S105 - placeholder

# A stand-in for mc: one JSON line per call, then the stdout real mc prints, so a test can
# see when the access key would reach the container log.
FAKE_MC = """\
import json, os, sys

args = sys.argv[1:]
call = {
    "argv": args,
    "env": {k: v for k, v in os.environ.items() if k.startswith("MC_HOST_")},
}
if args[:3] == ["admin", "user", "add"]:
    call["stdin"] = sys.stdin.read()
with open(os.environ["FAKE_MC_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\\n")
if os.environ.get("FAKE_MC_FAIL") and " ".join(args).startswith(os.environ["FAKE_MC_FAIL"]):
    print("mc: <ERROR> Unable to reach the server (fake).", file=sys.stderr)
    sys.exit(1)
if args[:3] == ["admin", "user", "add"]:
    first = call["stdin"].split("\\n")[0] if len(args) == 3 else args[3]
    print(f"Added user `{first}` successfully.")
elif args[:3] == ["admin", "policy", "attach"]:
    print(f"Attached Policies: [{args[4]}]\\nTo User: {args[-1]}")
"""


def _require(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path.relative_to(REPO_ROOT)} is not part of this checkout")


def _bootstrap_script() -> str:
    _require(COMPOSE_FILE)
    service = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"][
        "minio-bootstrap"
    ]
    assert service["entrypoint"] == ["/bin/sh", "-ec"]
    (script,) = service["command"]
    # Compose turns $$ into $ before the container sees the script.
    return script.replace("$$", "$")


def _tools_script(path: Path) -> str:
    """The script that ``path`` hands to the ``minio-tools`` service (``/bin/sh -ec``)."""
    _require(path)
    match = re.search(r"\n  minio-tools '\n(.*?)\n  '\n", path.read_text(encoding="utf-8"), re.S)
    assert match is not None, f"no minio-tools script in {path.name}"
    return match.group(1)


def _environment(**overrides: str) -> dict[str, str]:
    environment = {
        "MINIO_ENDPOINT": "http://minio:9000",
        "MINIO_BUCKET": "homayar-private",
        "MINIO_ROOT_USER": ROOT_USER,
        "MINIO_ROOT_PASSWORD": ROOT_PASSWORD,
        "STORAGE_ACCESS_KEY": ACCESS_KEY,
        "STORAGE_SECRET_KEY": SECRET_KEY,
    }
    environment.update(overrides)
    return environment


def _run(
    script: str, tmp_path: Path, environment: dict[str, str], fail: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "mc"
    fake.write_text(f"#!{sys.executable}\n{FAKE_MC}", encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "mc-calls.jsonl"
    log.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path),
        "FAKE_MC_LOG": str(log),
        "FAKE_MC_FAIL": fail,
        **environment,
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, script text from the repo
        [sh, "-ec", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return completed, calls


def _argv_leaks(calls: list[dict[str, Any]], *values: str) -> list[str]:
    return [
        " ".join(call["argv"])
        for call in calls
        if any(value in arg for value in values for arg in call["argv"])
    ]


# --- minio-bootstrap -------------------------------------------------------------------------


def test_bootstrap_gives_mc_no_secret_as_an_argument(tmp_path: Path) -> None:
    completed, calls = _run(_bootstrap_script(), tmp_path, _environment())

    assert completed.returncode == 0, completed.stderr
    assert calls, "the bootstrap made no mc call"
    assert _argv_leaks(calls, ROOT_USER, ROOT_PASSWORD, SECRET_KEY) == []
    expected = f"http://{ROOT_USER}:{ROOT_PASSWORD}@minio:9000"
    assert {call["env"].get("MC_HOST_local") for call in calls} == {expected}
    assert not any(call["argv"][:2] == ["alias", "set"] for call in calls)


def test_bootstrap_sends_the_app_keys_on_stdin(tmp_path: Path) -> None:
    completed, calls = _run(_bootstrap_script(), tmp_path, _environment())

    assert completed.returncode == 0, completed.stderr
    (add,) = [call for call in calls if call["argv"][:3] == ["admin", "user", "add"]]
    assert add["argv"] == ["admin", "user", "add", "local"]
    assert add["stdin"] == f"{ACCESS_KEY}\n{SECRET_KEY}\n"
    (attach,) = [call for call in calls if call["argv"][:3] == ["admin", "policy", "attach"]]
    assert attach["argv"] == [
        "admin",
        "policy",
        "attach",
        "local",
        "homayar-app",
        "--user",
        ACCESS_KEY,
    ]


def test_bootstrap_log_holds_no_key(tmp_path: Path) -> None:
    completed, _ = _run(_bootstrap_script(), tmp_path, _environment())

    assert completed.returncode == 0, completed.stderr
    output = completed.stdout + completed.stderr
    for value in (ROOT_USER, ROOT_PASSWORD, ACCESS_KEY, SECRET_KEY):
        assert value not in output


def test_bootstrap_passes_url_special_characters_raw(tmp_path: Path) -> None:
    completed, calls = _run(
        _bootstrap_script(), tmp_path, _environment(MINIO_ROOT_PASSWORD=SPECIAL_PASSWORD)
    )

    assert completed.returncode == 0, completed.stderr
    expected = f"http://{ROOT_USER}:{SPECIAL_PASSWORD}@minio:9000"
    assert {call["env"].get("MC_HOST_local") for call in calls} == {expected}
    assert _argv_leaks(calls, SPECIAL_PASSWORD) == []


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"MINIO_ROOT_PASSWORD": "unit:root-password"}, id="colon-in-root-password"),
        pytest.param({"MINIO_ROOT_USER": "unit:root"}, id="colon-in-root-user"),
        pytest.param({"MINIO_ROOT_PASSWORD": "unit-root\npassword"}, id="newline-in-root"),
        pytest.param({"STORAGE_SECRET_KEY": "unit-app\nsecret"}, id="newline-in-app-secret"),
        pytest.param({"STORAGE_ACCESS_KEY": "unit-app\naccess"}, id="newline-in-app-access"),
    ],
)
def test_bootstrap_rejects_keys_mc_cannot_carry(tmp_path: Path, overrides: dict[str, str]) -> None:
    completed, calls = _run(_bootstrap_script(), tmp_path, _environment(**overrides))

    assert completed.returncode != 0
    assert calls == []  # rejected before any mc call
    assert "must not contain" in completed.stderr
    for value in overrides.values():
        for part in re.split(r"[:\n]", value):
            assert part not in completed.stdout + completed.stderr


def test_bootstrap_accepts_a_colon_in_the_app_secret(tmp_path: Path) -> None:
    # The app keys go on stdin, line by line, so only a line break is a problem there.
    completed, calls = _run(
        _bootstrap_script(),
        tmp_path,
        _environment(STORAGE_SECRET_KEY="unit:app:secret"),  # noqa: S106 - placeholder
    )

    assert completed.returncode == 0, completed.stderr
    (add,) = [call for call in calls if call["argv"][:3] == ["admin", "user", "add"]]
    assert add["stdin"] == f"{ACCESS_KEY}\nunit:app:secret\n"


def test_bootstrap_failure_still_shows_the_error_and_exits_non_zero(tmp_path: Path) -> None:
    completed, _ = _run(_bootstrap_script(), tmp_path, _environment(), fail="mb")

    assert completed.returncode != 0
    assert "Unable to reach the server" in completed.stderr


def test_bootstrap_user_add_failure_is_not_hidden(tmp_path: Path) -> None:
    completed, calls = _run(_bootstrap_script(), tmp_path, _environment(), fail="admin user add")

    assert completed.returncode != 0
    assert "Unable to reach the server" in completed.stderr
    assert not any(call["argv"][:3] == ["admin", "policy", "attach"] for call in calls)


# --- minio-tools (backup-minio.sh, restore-minio.sh) -----------------------------------------


@pytest.mark.parametrize(
    ("path", "alias"),
    [
        pytest.param(BACKUP_SCRIPT, "source", id="backup"),
        pytest.param(RESTORE_SCRIPT, "target", id="restore"),
    ],
)
def test_tools_scripts_give_mc_no_secret_as_an_argument(
    tmp_path: Path, path: Path, alias: str
) -> None:
    # The script writes under /backups in the container; point it at the test folder.
    script = _tools_script(path).replace("/backups/", f"{tmp_path}/backups/")
    environment = _environment(
        MINIO_ROOT_PASSWORD=SPECIAL_PASSWORD, BACKUP_SET="minio/x", DELETE_EXTRA="0"
    )
    completed, calls = _run(script, tmp_path, environment)

    assert completed.returncode == 0, completed.stderr
    assert calls
    assert _argv_leaks(calls, ROOT_USER, SPECIAL_PASSWORD) == []
    expected = f"http://{ROOT_USER}:{SPECIAL_PASSWORD}@minio:9000"
    assert {call["env"].get(f"MC_HOST_{alias}") for call in calls} == {expected}
    assert not any(call["argv"][:2] == ["alias", "set"] for call in calls)


@pytest.mark.parametrize("path", [BACKUP_SCRIPT, RESTORE_SCRIPT], ids=["backup", "restore"])
def test_tools_scripts_reject_a_colon_in_the_root_keys(tmp_path: Path, path: Path) -> None:
    script = _tools_script(path).replace("/backups/", f"{tmp_path}/backups/")
    environment = _environment(
        MINIO_ROOT_PASSWORD="unit:root-password",  # noqa: S106 - placeholder
        BACKUP_SET="minio/x",
        DELETE_EXTRA="0",
    )
    completed, calls = _run(script, tmp_path, environment)

    assert completed.returncode != 0
    assert calls == []
    assert "must not contain" in completed.stderr
