from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 180

CANARY_DB_PASSWORD = "fx-canary-dbpw44"
CANARY_REDIS_PASSWORD = "fx-canary-redis40"
CANARY_JWT_SECRET = "fx-canary-jwtsec-ret441-7abcde-fghijk"
CANARY_TOKEN_PEPPER = "fx-canary-pepper-4417ab-cdefgh-ijklm"
CANARY_S3_ACCESS_KEY = "fx-canary-access-4"
CANARY_S3_SECRET_KEY = "fx-canary-s3secr-et4"
CANARY_METRICS_TOKEN = "fx-canary-metric-stoken-4417ab-cdef"
CANARY_HOST = "qxcanary-host.example"
CANARY_EMAIL = "ops@qxcanary-mail.example"
CANARY_DSN = f"postgresql+psycopg://homayar:{CANARY_DB_PASSWORD}@127.0.0.1:1/homayar"
RELEASE_SHA = "4f2c9e7a1b3d5f60718293a4b5c6d7e8f9012345"
MIGRATION_HEAD = "0f1e2d3c4b5a"

COLLECT_ENV = {
    "ENVIRONMENT": "test",
    "DEPLOYMENT_MODE": "onprem",
    "DEPLOYMENT_TOPOLOGY": "single-host-cpu",
    "ASR_PROFILE": "cpu-small",
    "DATABASE_URL": CANARY_DSN,
    "REDIS_URL": f"redis://:{CANARY_REDIS_PASSWORD}@127.0.0.1:1/0",
    "JWT_SECRET": CANARY_JWT_SECRET,
    "TOKEN_PEPPER": CANARY_TOKEN_PEPPER,
    "STORAGE_ACCESS_KEY": CANARY_S3_ACCESS_KEY,
    "STORAGE_SECRET_KEY": CANARY_S3_SECRET_KEY,
    "METRICS_BEARER_TOKEN": CANARY_METRICS_TOKEN,
    "PUBLIC_URL": f"https://console.{CANARY_HOST}",
    "SMTP_HOST": f"mail.{CANARY_HOST}",
    "SMTP_PORT": "587",
    "SMTP_FROM": CANARY_EMAIL,
}

CANARIES = (
    CANARY_DB_PASSWORD,
    CANARY_REDIS_PASSWORD,
    CANARY_JWT_SECRET,
    CANARY_TOKEN_PEPPER,
    CANARY_S3_ACCESS_KEY,
    CANARY_S3_SECRET_KEY,
    CANARY_METRICS_TOKEN,
    CANARY_HOST,
    CANARY_EMAIL,
)

BUILD_INFO = {
    "schema_version": 1,
    "application": {"name": "homayar-platform", "version": "0.6.0"},
    "source": {"sha": RELEASE_SHA, "date_epoch": 1794264000, "released": True},
    "profile": "standard",
    "build": {"base_image": "python:3.12-slim", "uv_version": "0.5.11"},
    "code": {"files_sha256": hashlib.sha256(b"code").hexdigest(), "file_count": 812},
    "runtime": {"python_version": platform.python_version(), "implementation": "CPython"},
    "dependencies": {
        "uv_lock_sha256": hashlib.sha256(b"lock").hexdigest(),
        "pyproject_sha256": hashlib.sha256(b"pyproject").hexdigest(),
        "installed_set_sha256": hashlib.sha256(b"installed").hexdigest(),
        "installed_count": 143,
    },
    "schema": {"migration_head": MIGRATION_HEAD},
}

RUNNER = """
import functools
import json
import pathlib
import runpy
import sys

import backend.observability.release as release

info_path = pathlib.Path(sys.argv[1])
document = json.loads(info_path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def fake_load_build_info(path=str(info_path)):
    return document


release.BUILD_INFO_PATH = info_path
release.load_build_info = fake_load_build_info
sys.argv = ["backend.support_bundle", *sys.argv[2:]]
runpy.run_module("backend.support_bundle", run_name="__main__", alter_sys=True)
"""

API_IMAGE_ID = "sha256:" + hashlib.sha256(b"oracle api image").hexdigest()
CONSOLE_IMAGE_ID = "sha256:" + hashlib.sha256(b"oracle console image").hexdigest()
API_DIGEST_REF = "ghcr.io/example/homayar-api@sha256:" + hashlib.sha256(b"oracle digest").hexdigest()
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def base_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "SYSTEMROOT") if key in os.environ}
    env.update(extra or {})
    return env


def run_module(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        [sys.executable, "-m", "backend.support_bundle", *argv],
        cwd=HOMAYAR_ROOT,
        env=base_env(env),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def run_scan(
    target: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return run_module("scan", str(target), *args, env=env)


def output(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


def walk_pairs(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from walk_pairs(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_pairs(item)


def scalar(item: Any) -> Any:
    if isinstance(item, dict) and "value" in item:
        return item["value"]
    return item


def values_for(document: Any, names: set[str]) -> list[Any]:
    return [scalar(item) for key, item in walk_pairs(document) if key.lower() in names]


def settings_view(document: Any) -> dict[str, Any]:
    stack = [document]
    while stack:
        current = stack.pop(0)
        if isinstance(current, dict):
            if any(str(key).lower() == "database_url" for key in current):
                return {str(key).lower(): scalar(item) for key, item in current.items()}
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return {}


@pytest.fixture(scope="module")
def collected(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    folder = tmp_path_factory.mktemp("collect")
    info = folder / "build-info.json"
    info.write_text(json.dumps(BUILD_INFO), encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv, the package's own CLI
        [sys.executable, "-c", RUNNER, str(info), "collect"],
        cwd=HOMAYAR_ROOT,
        env=base_env(COLLECT_ENV),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


@pytest.fixture(scope="module")
def collected_document(collected: subprocess.CompletedProcess[str]) -> Any:
    if collected.returncode != 0:
        return {"collect_failed": collected.returncode}
    try:
        return json.loads(collected.stdout)
    except ValueError:
        return {"collect_output_not_json": True}


def test_collect_exits_zero_and_prints_exactly_one_json_document(
    collected: subprocess.CompletedProcess[str],
) -> None:
    assert collected.returncode == 0, collected.stderr[-3000:]
    document = json.loads(collected.stdout)
    assert isinstance(document, dict)


def test_collect_contains_the_release_source_sha(collected_document: Any) -> None:
    assert RELEASE_SHA in json.dumps(collected_document)


def test_collect_contains_the_python_version(collected_document: Any) -> None:
    assert platform.python_version() in json.dumps(collected_document)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ({"deployment_mode"}, "onprem"),
        ({"deployment_topology"}, "single-host-cpu"),
        ({"environment", "app_environment"}, "test"),
        ({"asr_profile"}, "cpu-small"),
    ],
    ids=["deployment_mode", "deployment_topology", "app_environment", "asr_profile"],
)
def test_collect_copies_an_allowlisted_public_setting_with_its_value(
    collected_document: Any, names: set[str], expected: str
) -> None:
    assert expected in values_for(collected_document, names)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("database_url", "set"),
        ("redis_url", "set"),
        ("jwt_secret", "set"),
        ("token_pepper", "set"),
        ("storage_access_key", "set"),
        ("storage_secret_key", "set"),
        ("metrics_bearer_token", "set"),
        ("public_url", "set"),
        ("smtp_host", "set"),
        ("smtp_from", "set"),
        ("google_client_secret", "unset"),
    ],
)
def test_collect_reports_a_non_allowlisted_setting_only_as_set_or_unset(
    collected_document: Any, name: str, expected: str
) -> None:
    view = settings_view(collected_document)
    assert name in view, f"collect reports no settings entry named {name}"
    assert view[name] == expected


def test_collect_copies_no_url_or_email_setting_value(collected_document: Any) -> None:
    view = settings_view(collected_document)
    assert view, "collect reports no settings section"
    for name, value in view.items():
        if isinstance(value, str):
            assert "://" not in value, name
            assert not EMAIL_RE.search(value), name


@pytest.mark.parametrize(
    "canary",
    CANARIES,
    ids=[
        "db_password",
        "redis_password",
        "jwt_secret",
        "token_pepper",
        "s3_access_key",
        "s3_secret_key",
        "metrics_token",
        "host_name",
        "email",
    ],
)
def test_collect_never_outputs_a_canary_value(
    collected: subprocess.CompletedProcess[str], canary: str
) -> None:
    assert collected.returncode == 0, collected.stderr[-3000:]
    assert isinstance(json.loads(collected.stdout), dict)
    assert canary not in collected.stdout
    assert canary not in collected.stderr


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_clean_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    bundle.mkdir()
    documents = {
        "host.json": {
            "uname": "Linux 6.8.0-49-generic x86_64",
            "docker_version": "27.3.1",
            "compose_version": "v2.29.7",
            "images": [
                {"ref": "homayar/api:0.6.0", "id": API_IMAGE_ID, "digest": API_DIGEST_REF},
                {"ref": "homayar/console-web:0.6.0", "id": CONSOLE_IMAGE_ID},
            ],
            "release_sha": RELEASE_SHA,
            "readyz": "http://127.0.0.1:8080/readyz",
            "bind_address": "0.0.0.0",
            "disk_free_gib": {"homayar_home": 120.5, "docker_root": 300.25},
            "gpu": "Tesla P40, 535.183.01, 23040 MiB, 512 MiB",
        },
        "preflight.json": {"mode": "host", "status": "ok", "checks": {"gpu_driver": "skip", "disk": "ok"}},
        "logs-summary.json": {
            "api": {"lines_24h": 1200, "error_lines": 3, "warning_lines": 5, "critical_lines": 0},
            "worker": {"lines_24h": 800, "error_lines": 0, "warning_lines": 1, "critical_lines": 0},
        },
        "container.json": {
            "release": {"version": "0.6.0", "source_sha": RELEASE_SHA, "migration_head": MIGRATION_HEAD},
            "module": "backend.observability.release",
            "python": "3.12.8",
            "settings": {"DEPLOYMENT_MODE": "onprem", "DATABASE_URL": "set", "SMTP_PASSWORD": "unset"},
            "database": {"reachable": True, "server_version": "17.2", "alembic_version": MIGRATION_HEAD},
        },
    }
    for name, document in documents.items():
        (bundle / name).write_text(json.dumps(document, indent=2), encoding="utf-8")
    (bundle / "compose-ps.txt").write_text(
        "api running healthy 0\nproxy running healthy 0\nscheduler running  0\n", encoding="utf-8"
    )
    manifest = {
        "format_version": 1,
        "created_at": "2026-11-13T01:56:00Z",
        "files": [
            {"path": path.name, "sha256": sha256_of(path)}
            for path in sorted(bundle.iterdir())
            if path.is_file()
        ],
    }
    (bundle / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return bundle


def write_env_file(root: Path, lines: list[str]) -> Path:
    env_file = root / "onprem.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_file


def assert_clean(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, output(proc)


def assert_finding(
    proc: subprocess.CompletedProcess[str], kind: str, file_name: str, secret: str | None
) -> None:
    text = output(proc)
    assert proc.returncode == 1, text
    lines = [line for line in text.splitlines() if kind in line and file_name in line]
    assert lines, f"no output line names both the kind {kind} and the file {file_name}: {text}"
    if secret is not None:
        assert secret not in text


CLEAN_ENV_FILE_LINES = [
    "POSTGRES_USER=homayar",
    "POSTGRES_DB=homayar",
    "DATABASE_URL=postgresql+psycopg://homayar:fx-scandb-pw88@postgres:5432/homayar",
    "STORAGE_SECRET_KEY=fx-scans3-secret-8",
    "JWT_SECRET=fx-scanjw-tsecre-t8817a-bcdefg-hijk",
]


def test_a_clean_bundle_with_digests_a_sha_and_loopback_scans_clean(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    env_file = write_env_file(tmp_path, CLEAN_ENV_FILE_LINES)
    proc = run_scan(bundle, "--env-file", str(env_file), env={"ORACLE_API_TOKEN": "fx-procen-vt0ken"})
    assert_clean(proc)


def test_a_clean_bundle_scans_clean_without_an_env_file(tmp_path: Path) -> None:
    assert_clean(run_scan(write_clean_bundle(tmp_path)))


def test_a_secret_value_from_the_env_file_is_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.json").write_text('{"note": "fx-scans3-secret-8"}\n', encoding="utf-8")
    env_file = write_env_file(tmp_path, CLEAN_ENV_FILE_LINES)
    assert_finding(run_scan(bundle, "--env-file", str(env_file)), "secret_value", "planted.json", "fx-scans3-secret-8")


def test_a_same_length_non_secret_value_is_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.json").write_text('{"note": "fx-plainn-0tsecr-e"}\n', encoding="utf-8")
    assert len("fx-plainn-0tsecr-e") == len("fx-scans3-secret-8")
    env_file = write_env_file(tmp_path, CLEAN_ENV_FILE_LINES)
    assert_clean(run_scan(bundle, "--env-file", str(env_file)))


def test_a_secret_value_from_the_process_env_is_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("token fx-procen-vt0ken in a file\n", encoding="utf-8")
    proc = run_scan(bundle, env={"ORACLE_API_TOKEN": "fx-procen-vt0ken"})
    assert_finding(proc, "secret_value", "planted.txt", "fx-procen-vt0ken")


def test_a_process_env_value_under_a_non_secret_name_is_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("greeting fx-procen-vgreet in a file\n", encoding="utf-8")
    assert_clean(run_scan(bundle, env={"ORACLE_GREETING": "fx-procen-vgreet"}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORACLE_DB_PASSWORD", "fx-namepa-ssw0r"),
        ("ORACLE_CLIENT_SECRET", "fx-namese-cr3tva"),
        ("ORACLE_SESSION_TOKEN", "fx-namet0-kenva"),
        ("ORACLE_SIGNING_KEY", "fx-namek3-yvalu"),
        ("SENTRY_DSN", "fx-nameds-nvalu"),
        ("ORACLE_WEBHOOK_URL", "fx-nameur-lvalu"),
        ("ORACLE_CREDENTIALS", "fx-namecr-3dvalu"),
        ("ORACLE_PRIVATE_BLOB", "fx-namepr-1vvalu"),
        ("oracle_lower_api_token", "fx-namelo-werto"),
    ],
    ids=["PASSWORD", "SECRET", "TOKEN", "KEY", "DSN", "_URL", "CREDENTIAL", "PRIVATE", "lowercase-name"],
)
def test_each_secret_name_pattern_makes_its_value_a_finding(tmp_path: Path, name: str, value: str) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(f"value {value} here\n", encoding="utf-8")
    env_file = write_env_file(tmp_path, [f"{name}={value}"])
    assert_finding(run_scan(bundle, "--env-file", str(env_file)), "secret_value", "planted.txt", value)


def test_a_secret_value_of_eight_characters_is_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("short fx-eight here\n", encoding="utf-8")
    env_file = write_env_file(tmp_path, ["ORACLE_SHORT_TOKEN=fx-eight"])
    assert_finding(run_scan(bundle, "--env-file", str(env_file)), "secret_value", "planted.txt", "fx-eight")


def test_a_secret_value_of_seven_characters_is_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("short fx-seve here\n", encoding="utf-8")
    env_file = write_env_file(tmp_path, ["ORACLE_SHORT_TOKEN=fx-seve"])
    assert_clean(run_scan(bundle, "--env-file", str(env_file)))


def test_a_secret_value_that_contains_equals_signs_is_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("key fx-b64sec-retv== here\n", encoding="utf-8")
    env_file = write_env_file(tmp_path, ["ORACLE_WEBHOOK_SECRET=fx-b64sec-retv" + "=="])
    assert_finding(run_scan(bundle, "--env-file", str(env_file)), "secret_value", "planted.txt", "fx-b64sec-retv")


def test_a_double_quoted_env_file_value_is_matched_without_its_quotes(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("quoted fx-quoted-t0ken here\n", encoding="utf-8")
    env_file = write_env_file(tmp_path, ['ORACLE_QUOTED_TOKEN="fx-quoted-t0ken"'])
    assert_finding(run_scan(bundle, "--env-file", str(env_file)), "secret_value", "planted.txt", "fx-quoted-t0ken")


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("dsn postgresql+psycopg://homayar:fx-urlpas-sw0r@db.internal:5432/homayar end", "fx-urlpas-sw0r"),
        ("cache redis://:fx-redisp-assw0r@redis:6379/0 end", "fx-redisp-assw0r"),
        ("store https://minio-user:fx-miniop-assw0r@minio:9000/bucket end", "fx-miniop-assw0r"),
    ],
    ids=["postgres-dsn", "redis-empty-user", "https"],
)
def test_url_credentials_are_a_finding(tmp_path: Path, text: str, secret: str) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(text + "\n", encoding="utf-8")
    assert_finding(run_scan(bundle), "url_credentials", "planted.txt", secret)


def test_urls_without_credentials_are_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(
        "probe http://127.0.0.1:8080/readyz and postgresql://postgres:5432/homayar and https://example.org/docs\n",
        encoding="utf-8",
    )
    assert_clean(run_scan(bundle))


@pytest.mark.parametrize("label", ["PRIVATE KEY", "RSA PRIVATE KEY", "OPENSSH PRIVATE KEY", "EC PRIVATE KEY"])
def test_a_private_key_block_is_a_finding(tmp_path: Path, label: str) -> None:
    bundle = write_clean_bundle(tmp_path)
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCoracleBodyQx"
    (bundle / "planted.pem").write_text(
        f"-----BEGIN {label}-----\n{body}\n-----END {label}-----\n", encoding="utf-8"
    )
    assert_finding(run_scan(bundle), "private_key", "planted.pem", body)


def test_a_public_key_and_a_certificate_are_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.pem").write_text(
        "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAoraclePublicKeyBodyQx\n-----END PUBLIC KEY-----\n"
        "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgoracleCertBodyQx\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    assert_clean(run_scan(bundle))


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_a_jwt_is_a_finding(tmp_path: Path) -> None:
    token = ".".join(
        [
            b64url(b'{"alg":"HS256","typ":"JWT"}'),
            b64url(b'{"sub":"oracle-user","exp":1794264000}'),
            b64url(b"oracle-signature-bytes-qx"),
        ]
    )
    assert token.startswith("eyJ")
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(f"Authorization: Bearer {token}\n", encoding="utf-8")
    assert_finding(run_scan(bundle), "jwt", "planted.txt", token)


def test_dotted_names_that_do_not_start_with_eyj_are_not_a_jwt(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(
        "module backend.observability.release and file build-info.json.sha256\n", encoding="utf-8"
    )
    assert_clean(run_scan(bundle))


def test_an_email_address_is_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("contact oracle.person@example.org today\n", encoding="utf-8")
    assert_finding(run_scan(bundle), "email", "planted.txt", "oracle.person@example.org")


def test_an_at_sign_without_an_address_is_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("send it to user@ later, or ping @team\n", encoding="utf-8")
    assert_clean(run_scan(bundle))


@pytest.mark.parametrize(
    "text",
    ["سلام", "۱۲۳", "ݐ", "ﮊ", "ﷲ", "ﻩ"],
    ids=["U+0600-block-word", "U+0600-block-persian-digits", "U+0750-block", "U+FB50-block", "U+FDF2", "U+FE70-block"],
)
def test_arabic_script_text_is_a_finding(tmp_path: Path, text: str) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(f"transcript line: {text} end\n", encoding="utf-8")
    assert_finding(run_scan(bundle), "persian_text", "planted.txt", text)


def test_ascii_text_is_not_persian_text(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("transcript line: salam end\n", encoding="utf-8")
    assert_clean(run_scan(bundle))


def test_non_arabic_unicode_text_is_not_persian_text(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("café naïve Ωmega שלום\n", encoding="utf-8")
    assert_clean(run_scan(bundle))


AUDIO_HEADERS = {
    "riff-wave": b"RIFF\x24\x08\x00\x00WAVEfmt \x10\x00\x00\x00",
    "id3": b"ID3\x04\x00\x00\x00\x00\x00\x00",
    "mp3-frame-sync": b"\xff\xfb\x90\x64",
    "ogg": b"OggS\x00\x02\x00\x00",
    "flac": b"fLaC\x00\x00\x00\x22",
    "ftyp-at-offset-4": b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00",
    "ebml": bytes.fromhex("1A45DFA3") + b"\x9f\x42\x86\x81\x01",
}


@pytest.mark.parametrize("header", list(AUDIO_HEADERS.values()), ids=list(AUDIO_HEADERS))
def test_an_audio_signature_is_a_finding(tmp_path: Path, header: bytes) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.bin").write_bytes(header + b"\x00" * 1024)
    assert_finding(run_scan(bundle), "audio", "planted.bin", None)


FIVE_MIB_OF_LINES = (b"a" * 63 + b"\n") * (5 * 1024 * 1024 // 64)


def test_a_file_over_five_mib_is_an_audio_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_bytes(FIVE_MIB_OF_LINES + b"a")
    assert_finding(run_scan(bundle), "audio", "planted.txt", None)


def test_a_file_of_exactly_five_mib_is_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    assert len(FIVE_MIB_OF_LINES) == 5 * 1024 * 1024
    (bundle / "planted.txt").write_bytes(FIVE_MIB_OF_LINES)
    assert_clean(run_scan(bundle))


@pytest.mark.parametrize(
    ("text", "address"),
    [("peer 10.1.2.3 connected", "10.1.2.3"), ("db at 192.168.10.20:5432", "192.168.10.20")],
    ids=["10.1.2.3", "with-port"],
)
def test_an_ipv4_address_is_a_finding(tmp_path: Path, text: str, address: str) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text(text + "\n", encoding="utf-8")
    assert_finding(run_scan(bundle), "ip_address", "planted.txt", address)


def test_loopback_and_any_address_are_not_a_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    (bundle / "planted.txt").write_text("listen 127.0.0.1:8080 and bind 0.0.0.0\n", encoding="utf-8")
    assert_clean(run_scan(bundle))


def test_digests_and_a_forty_hex_sha_alone_are_not_findings(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "images.txt").write_text(
        f"{API_IMAGE_ID}\n{CONSOLE_IMAGE_ID}\n{API_DIGEST_REF}\n{RELEASE_SHA}\n", encoding="utf-8"
    )
    assert_clean(run_scan(bundle))


def test_scan_reads_nested_folders_and_hidden_files_and_reports_one_line_per_finding(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    nested = bundle / "deep" / "er"
    nested.mkdir(parents=True)
    (nested / "notes.txt").write_text("mail oracle.nested@example.org\n", encoding="utf-8")
    (bundle / ".hidden.json").write_text('{"peer": "10.9.8.7"}\n', encoding="utf-8")
    proc = run_scan(bundle)
    text = output(proc)
    assert proc.returncode == 1, text
    assert any("email" in line and "notes.txt" in line for line in text.splitlines()), text
    assert any("ip_address" in line and ".hidden.json" in line for line in text.splitlines()), text
    assert "oracle.nested@example.org" not in text
    assert "10.9.8.7" not in text


def test_scan_of_an_empty_folder_is_clean(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert_clean(run_scan(bundle))


def test_scan_of_a_missing_folder_fails_closed_with_exit_2(tmp_path: Path) -> None:
    proc = run_scan(tmp_path / "does-not-exist")
    assert proc.returncode == 2, output(proc)


def test_scan_with_a_missing_env_file_fails_closed_with_exit_2(tmp_path: Path) -> None:
    bundle = write_clean_bundle(tmp_path)
    proc = run_scan(bundle, "--env-file", str(tmp_path / "missing.env"))
    assert proc.returncode == 2, output(proc)


def test_scan_without_a_folder_is_a_usage_error() -> None:
    proc = run_module("scan")
    assert proc.returncode == 2, output(proc)


def test_an_unknown_command_is_a_usage_error() -> None:
    proc = run_module("frobnicate")
    assert proc.returncode == 2, output(proc)
