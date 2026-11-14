"""MinIO keys reach ``mc`` without a process argument (HOM-340).

On a shared host any local user can read process arguments (``ps``, ``/proc/<pid>/cmdline``).
So ``minio-bootstrap`` (``compose.onprem.yaml``), the ``minio-tools`` scripts of
``backup-minio.sh`` and ``restore-minio.sh``, and the ``BOOTSTRAP`` heredoc of
``infra/ci/minio-contract-target.sh`` must give ``mc``:

* the root keys through ``MC_HOST_<alias>``, set with the ``export`` builtin (no new process);
* the app keys on stdin of ``mc admin user add`` (the pinned ``mc`` reads them from a pipe);
* never a secret as an argument, and never the app access key on stdout (the container log).

The pinned ``mc`` (2025-04-16) reads ``MC_HOST_<alias>`` with a regex and no percent-decoding:
the access key ends at the first ``:``, the secret at the last ``@``, and a second ``:`` turns
the rest into a session token. So the scripts reject a ``:`` or a line break in the root keys,
and a line break in the app keys, before any ``mc`` call. Every other character passes as is.

Each test runs the real script text with ``sh`` and a fake ``mc`` on ``PATH`` that records its
argv, its ``MC_HOST_*`` variables and its stdin. Every value below is a placeholder. The
``minio-contract-target.sh`` section also runs that whole script once with a fake ``docker``
and ``curl``, to prove no host-side process gets a key as an argument either.
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
CI_TARGET_SCRIPT = REPO_ROOT / "infra" / "ci" / "minio-contract-target.sh"

ROOT_USER = "unit-root-user"
ROOT_PASSWORD = "unit-root-password-0000"  # noqa: S105 - placeholder
ACCESS_KEY = "unit-app-access-key"
SECRET_KEY = "unit-app-secret-key-0000"  # noqa: S105 - placeholder
DENIED_ACCESS_KEY = "unit-denied-access-key"
DENIED_SECRET_KEY = "unit-denied-secret-key-0000"  # noqa: S105 - placeholder
PROBE_ACCESS_KEY = "unit-probe-access-key"
PROBE_SECRET_KEY = "unit-probe-secret-key-0000"  # noqa: S105 - placeholder
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
elif args[:2] == ["version", "info"]:
    print('{"status": "Enabled"}')
elif args[:2] == ["anonymous", "get"]:
    print('{"permission": "private"}')
"""

# A stand-in for docker in the whole-script `up` test: it records every argv (and, for a
# `run`, the key values that reached it by environment name only) and answers what
# `wait_for_final_start` needs, without starting any container.
FAKE_DOCKER = """\
import json, os, sys

args = sys.argv[1:]
call = {"argv": args}
if args[:1] == ["run"]:
    call["run_env"] = {
        name: os.environ[name]
        for name in ("MC_HOST_local", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")
        if name in os.environ
    }
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\\n")
if args[:1] == ["inspect"]:
    print("true")
elif args[:1] == ["logs"]:
    print("** Starting MinIO **")
elif args[:1] == ["run"]:
    print("fake-container-id")
"""

# A stand-in for curl: it records its argv and answers the health check with success.
FAKE_CURL = """\
import json, os, sys

with open(os.environ["FAKE_CURL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
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
        # A CRLF env file: mc's ReadLine drops a trailing \r, the API keeps it (HOM-340 review).
        pytest.param({"STORAGE_SECRET_KEY": "unit-app-secret\r"}, id="cr-at-end-of-app-secret"),
        pytest.param({"STORAGE_ACCESS_KEY": "unit-app\raccess"}, id="cr-in-app-access"),
        pytest.param({"MINIO_ROOT_PASSWORD": "unit-root-password\r"}, id="cr-in-root-password"),
        pytest.param({"MINIO_ROOT_USER": "unit-root\r"}, id="cr-in-root-user"),
    ],
)
def test_bootstrap_rejects_keys_mc_cannot_carry(tmp_path: Path, overrides: dict[str, str]) -> None:
    completed, calls = _run(_bootstrap_script(), tmp_path, _environment(**overrides))

    assert completed.returncode != 0
    assert calls == []  # rejected before any mc call
    assert "must not contain" in completed.stderr
    for value in overrides.values():
        for part in filter(None, re.split(r"[:\r\n]", value)):
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
@pytest.mark.parametrize(
    "password",
    ["unit:root-password", "unit-root\npassword", "unit-root-password\r"],
    ids=["colon", "newline", "cr"],
)
def test_tools_scripts_reject_root_keys_mc_cannot_carry(
    tmp_path: Path, path: Path, password: str
) -> None:
    script = _tools_script(path).replace("/backups/", f"{tmp_path}/backups/")
    environment = _environment(MINIO_ROOT_PASSWORD=password, BACKUP_SET="minio/x", DELETE_EXTRA="0")
    completed, calls = _run(script, tmp_path, environment)

    assert completed.returncode != 0
    assert calls == []
    assert "must not contain" in completed.stderr


# --- infra/ci/minio-contract-target.sh --------------------------------------------------------


def _ci_bootstrap_script() -> str:
    """The ``BOOTSTRAP`` heredoc the target hands to ``docker run ... sh -ec``."""
    _require(CI_TARGET_SCRIPT)
    match = re.search(
        r"read -r -d '' BOOTSTRAP <<'SH' \|\| true\n(.*?)\nSH\n",
        CI_TARGET_SCRIPT.read_text(encoding="utf-8"),
        re.S,
    )
    assert match is not None, "no BOOTSTRAP heredoc in minio-contract-target.sh"
    return match.group(1)


def _ci_target_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "MINIO_BUCKET": "homayar-private",
        "MC_HOST_local": f"http://{ROOT_USER}:{ROOT_PASSWORD}@127.0.0.1:9000",
        "APP_ACCESS_KEY": ACCESS_KEY,
        "APP_SECRET_KEY": SECRET_KEY,
        "DENIED_ACCESS_KEY": DENIED_ACCESS_KEY,
        "DENIED_SECRET_KEY": DENIED_SECRET_KEY,
        "PROBE_ACCESS_KEY": PROBE_ACCESS_KEY,
        "PROBE_SECRET_KEY": PROBE_SECRET_KEY,
    }
    environment.update(overrides)
    return environment


def _run_ci_bootstrap(
    tmp_path: Path, fail: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    # BOOTSTRAP writes /tmp/step.out; keep that inside the test folder.
    script = _ci_bootstrap_script().replace(
        "/tmp/step.out",  # noqa: S108 - the path BOOTSTRAP uses, replaced here
        str(tmp_path / "step.out"),
    )
    return _run(script, tmp_path, _ci_target_environment(), fail=fail)


def test_ci_target_bootstrap_gives_mc_no_key_as_an_argument(tmp_path: Path) -> None:
    completed, calls = _run_ci_bootstrap(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert calls, "the bootstrap made no mc call"
    assert (
        _argv_leaks(
            calls, ROOT_USER, ROOT_PASSWORD, SECRET_KEY, DENIED_SECRET_KEY, PROBE_SECRET_KEY
        )
        == []
    )
    users = [
        (ACCESS_KEY, SECRET_KEY),
        (DENIED_ACCESS_KEY, DENIED_SECRET_KEY),
        (PROBE_ACCESS_KEY, PROBE_SECRET_KEY),
    ]
    adds = [call for call in calls if call["argv"][:3] == ["admin", "user", "add"]]
    assert len(adds) == 3
    for access_key, secret_key in users:
        matching = [call for call in adds if call["stdin"] == f"{access_key}\n{secret_key}\n"]
        assert matching, f"no admin user add read the keys of {access_key} from stdin"
        assert matching[0]["argv"] == ["admin", "user", "add", "local"]
    keys = [ROOT_USER, ROOT_PASSWORD]
    for access_key, secret_key in users:
        keys += [access_key, secret_key]
    output = completed.stdout + completed.stderr
    for value in keys:
        assert value not in output


def test_ci_target_bootstrap_user_add_failure_shows_step_and_no_key(tmp_path: Path) -> None:
    completed, calls = _run_ci_bootstrap(tmp_path, fail="admin user add")

    assert completed.returncode != 0
    assert "MinIO bootstrap failed at: add the app user" in completed.stderr
    # A user-credential step hides mc output (mc echoes the access key).
    assert "Unable to reach the server" not in completed.stderr
    assert not any(call["argv"][:3] == ["admin", "policy", "attach"] for call in calls)
    output = completed.stdout + completed.stderr
    for value in (
        ROOT_USER,
        ROOT_PASSWORD,
        ACCESS_KEY,
        SECRET_KEY,
        DENIED_ACCESS_KEY,
        DENIED_SECRET_KEY,
        PROBE_ACCESS_KEY,
        PROBE_SECRET_KEY,
    ):
        assert value not in output


def _run_ci_up(
    tmp_path: Path, port: str
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]], dict[str, str]]:
    """Run ``minio-contract-target.sh up`` with a fake ``docker`` and ``curl``.

    Returns the process, every recorded host-side call (docker and curl), and the env
    file values the script wrote.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker_log = tmp_path / "docker-calls.jsonl"
    curl_log = tmp_path / "curl-calls.jsonl"
    docker_log.write_text("", encoding="utf-8")
    curl_log.write_text("", encoding="utf-8")
    for name, text in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        fake = bin_dir / name
        fake.write_text(f"#!{sys.executable}\n{text}", encoding="utf-8")
        fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path),
        "GITHUB_ACTIONS": "",
        "FAKE_DOCKER_LOG": str(docker_log),
        "FAKE_CURL_LOG": str(curl_log),
    }
    env_file = tmp_path / "minio-contract.env"
    completed = subprocess.run(  # noqa: S603 - fixed argv, script from the repo
        [bash, str(CI_TARGET_SCRIPT), "up", port, str(env_file)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    calls = [
        json.loads(line)
        for line in docker_log.read_text(encoding="utf-8").splitlines()
        + curl_log.read_text(encoding="utf-8").splitlines()
    ]
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            values[key] = value
    return completed, calls, values


def test_ci_target_up_starts_no_host_process_with_a_key_argument(tmp_path: Path) -> None:
    completed, calls, values = _run_ci_up(tmp_path, port="45731")

    assert completed.returncode == 0, completed.stderr
    assert calls, "the script started no host-side process"
    assert values["HOMAYAR_MINIO_TEST"] == "1"
    assert values["HOMAYAR_MINIO_ENDPOINT_URL"] == "http://127.0.0.1:45731"
    assert set(values) == {
        "HOMAYAR_MINIO_TEST",
        "HOMAYAR_MINIO_ENDPOINT_URL",
        "HOMAYAR_MINIO_BUCKET",
        "HOMAYAR_MINIO_ACCESS_KEY",
        "HOMAYAR_MINIO_SECRET_KEY",
        "HOMAYAR_MINIO_DENIED_ACCESS_KEY",
        "HOMAYAR_MINIO_DENIED_SECRET_KEY",
        "HOMAYAR_MINIO_PROBE_ACCESS_KEY",
        "HOMAYAR_MINIO_PROBE_SECRET_KEY",
    }
    (minio_run,) = [
        call for call in calls if "run_env" in call and "MINIO_ROOT_USER" in call["run_env"]
    ]
    assert (
        _argv_leaks(
            calls,
            minio_run["run_env"]["MINIO_ROOT_USER"],
            minio_run["run_env"]["MINIO_ROOT_PASSWORD"],
            values["HOMAYAR_MINIO_ACCESS_KEY"],
            values["HOMAYAR_MINIO_SECRET_KEY"],
            values["HOMAYAR_MINIO_DENIED_ACCESS_KEY"],
            values["HOMAYAR_MINIO_DENIED_SECRET_KEY"],
            values["HOMAYAR_MINIO_PROBE_ACCESS_KEY"],
            values["HOMAYAR_MINIO_PROBE_SECRET_KEY"],
        )
        == []
    )
