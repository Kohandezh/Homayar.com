"""Secret references, secret redaction and mandatory production settings (HOM-32, WP02-T03).

Every value below is an obviously fake fixture. Each test runs with every environment variable
that names a ``Settings`` field (or its ``*_FILE`` reference) removed, and never reads the
repository's ``.env`` file.
"""

from __future__ import annotations

import ast
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import backend.database.seed as seed_module
from backend.config import PROJECT_ROOT, Settings

# The secret-bearing fields named by the contract (HOM-32).
SECRET_FIELDS = (
    "database_url",
    "redis_url",
    "jwt_secret",
    "token_pepper",
    "google_client_secret",
    "asanak_api_key",
    "storage_access_key",
    "storage_secret_key",
    "pii_mapping_keys",
    "llm_fallback_api_key",
    "zai_api_key",
    "gapgpt_api_key",
    "smtp_password",
    "neo4j_password",
    "sentry_dsn",
)

MARK = "Qz7fake"
FAKE_PII_KEY = base64.b64encode(b"fake-pii-mapping-key-32-bytes-Qz").decode()
FAKE_SECRETS: dict[str, str] = {
    "database_url": f"postgresql+psycopg://homayar:fake-db-password-{MARK}@db.example.invalid/homayar",
    "redis_url": f"rediss://:fake-redis-password-{MARK}@redis.example.invalid:6380/0",
    "jwt_secret": f"fake-jwt-secret-{MARK}-0123456789abcdef",
    "token_pepper": f"fake-token-pepper-{MARK}-0123456789abcdef",
    "google_client_secret": f"fake-google-client-secret-{MARK}",
    "asanak_api_key": f"fake-asanak-api-key-{MARK}",
    "storage_access_key": f"fake-storage-access-key-{MARK}",
    "storage_secret_key": f"fake-storage-secret-key-{MARK}",
    "pii_mapping_keys": f"fake1:{FAKE_PII_KEY}",
    "llm_fallback_api_key": f"fake-llm-fallback-api-key-{MARK}",
    "zai_api_key": f"fake-zai-api-key-{MARK}",
    "gapgpt_api_key": f"fake-gapgpt-api-key-{MARK}",
    "smtp_password": f"fake-smtp-password-{MARK}",
    "neo4j_password": f"fake-neo4j-password-{MARK}",
    "sentry_dsn": f"https://fakepublickey{MARK}@sentry.example.invalid/7",
}
# Settings a secret needs to validate (PII keys need their active key id).
COMPANIONS: dict[str, dict[str, object]] = {
    "pii_mapping_keys": {"pii_mapping_active_key_id": "fake1"}
}


def _distinctive_parts(value: str) -> list[str]:
    """The whole value plus the fragments that identify it (a URL's password, a key's bytes)."""
    parts = [value]
    parts += [token for token in re.split(r"[:@/]", value) if MARK in token or len(token) >= 40]
    return parts


def assert_no_secret(text: str, secrets: dict[str, str]) -> None:
    for field, value in secrets.items():
        for part in _distinctive_parts(value):
            assert part not in text, f"{field} value leaked"


def exception_texts(exc: BaseException) -> list[str]:
    """Every rendering of an exception and of the exceptions chained to it."""
    texts: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        texts += [str(current), repr(current), repr(current.args)]
        if isinstance(current, ValidationError):
            texts += [current.json(), repr(current.errors())]
        pending += [current.__cause__, current.__context__]
    return texts


def production_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "environment": "production",
        "database_url": FAKE_SECRETS["database_url"],
        "jwt_secret": FAKE_SECRETS["jwt_secret"],
        "token_pepper": FAKE_SECRETS["token_pepper"],
        "secure_cookies": True,
        "enable_demo_account": False,
        "allow_mock_providers": False,
        "auto_create_schema": False,
        "_env_file": None,
    }
    kwargs.update(overrides)
    return kwargs


def all_secrets_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = dict(FAKE_SECRETS)
    kwargs["pii_mapping_active_key_id"] = "fake1"
    kwargs.update(overrides)
    return kwargs


def write_secret(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """testenv.sh exports test secrets and flags; remove every Settings variable and reference."""
    names = set(Settings.model_fields) | {f"{name}_file" for name in Settings.model_fields}
    for key in list(os.environ):
        if key.lower() in names:
            monkeypatch.delenv(key)


# --- AC1: secret references -------------------------------------------------------------


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_file_reference_supplies_every_secret_field(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = FAKE_SECRETS[field]
    secret = write_secret(tmp_path / field, value.encode() + b"\n")
    monkeypatch.setenv(f"{field.upper()}_FILE", str(secret))

    settings = Settings(_env_file=None, **COMPANIONS.get(field, {}))

    assert getattr(settings, field) == value
    assert isinstance(getattr(settings, field), str)


@pytest.mark.parametrize("name", ["jwt_secret_file", "Jwt_Secret_File", "JWT_SECRET_FILE"])
def test_file_reference_name_is_case_insensitive(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "jwt", FAKE_SECRETS["jwt_secret"].encode() + b"\n")
    monkeypatch.setenv(name, str(secret))

    assert Settings(_env_file=None).jwt_secret == FAKE_SECRETS["jwt_secret"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"fake-value\n", "fake-value"),
        (b"fake-value\r\n", "fake-value"),
        (b"fake-value", "fake-value"),
        (b"fake-value\n\n", "fake-value\n"),
        (b"fake-value\r\n\r\n", "fake-value\r\n"),
        (b"fake-value\r", "fake-value\r"),
        (b"fake-value\n\r", "fake-value\n\r"),
        (b" \tfake-value \t\n", " \tfake-value \t"),
        (b"line-one\nline-two\n", "line-one\nline-two"),
        (b"\n", ""),
        (b"", ""),
        ("﻿key-هما\n".encode(), "﻿key-هما"),
    ],
)
def test_exactly_one_trailing_newline_is_removed(
    content: bytes, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "smtp", content)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(secret))

    assert Settings(_env_file=None).smtp_password == expected


def test_file_reference_resolves_through_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = write_secret(tmp_path / "real", FAKE_SECRETS["zai_api_key"].encode())
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setenv("ZAI_API_KEY_FILE", str(link))

    assert Settings(_env_file=None).zai_api_key == FAKE_SECRETS["zai_api_key"]


# --- AC2: conflicts and unreadable references are loud and never leak -------------------


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_value_and_reference_together_are_rejected(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_value = FAKE_SECRETS[field]
    direct_value = f"other-direct-{MARK}-{field}"
    secret = write_secret(tmp_path / field, file_value.encode())
    monkeypatch.setenv(field.upper(), direct_value)
    monkeypatch.setenv(f"{field.upper()}_FILE", str(secret))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None, **COMPANIONS.get(field, {}))

    message = str(caught.value)
    assert f"{field.upper()}_FILE" in message
    assert re.search(rf"\b{field.upper()}\b", message)
    for text in exception_texts(caught.value):
        assert_no_secret(text, {field: file_value, "direct": direct_value})


@pytest.mark.parametrize(
    ("plain", "reference"),
    [("jwt_secret", "JWT_SECRET_FILE"), ("JWT_SECRET", "jwt_secret_file")],
)
def test_conflict_detection_ignores_letter_case(
    plain: str, reference: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "jwt", FAKE_SECRETS["jwt_secret"].encode())
    monkeypatch.setenv(plain, f"direct-{MARK}-value")
    monkeypatch.setenv(reference, str(secret))

    with pytest.raises(ValueError, match="JWT_SECRET_FILE"):
        Settings(_env_file=None)


def test_empty_plain_value_still_conflicts_with_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "jwt", FAKE_SECRETS["jwt_secret"].encode())
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret))

    with pytest.raises(ValueError, match="JWT_SECRET_FILE"):
        Settings(_env_file=None)


def test_reference_set_twice_with_different_case_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = write_secret(tmp_path / "one", FAKE_SECRETS["jwt_secret"].encode())
    second = write_secret(tmp_path / "two", FAKE_SECRETS["token_pepper"].encode())
    monkeypatch.setenv("JWT_SECRET_FILE", str(first))
    monkeypatch.setenv("jwt_secret_file", str(second))

    with pytest.raises(ValueError, match="JWT_SECRET_FILE") as caught:
        Settings(_env_file=None)
    for text in exception_texts(caught.value):
        assert_no_secret(text, FAKE_SECRETS)


def test_missing_reference_file_names_variable_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "absent" / "jwt"
    monkeypatch.setenv("JWT_SECRET_FILE", str(missing))
    monkeypatch.setenv("TOKEN_PEPPER", FAKE_SECRETS["token_pepper"])

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert "JWT_SECRET_FILE" in message
    assert str(missing) in message
    for text in exception_texts(caught.value):
        assert_no_secret(text, FAKE_SECRETS)


def test_existing_reference_file_is_the_allow_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    present = write_secret(tmp_path / "jwt", FAKE_SECRETS["jwt_secret"].encode())
    monkeypatch.setenv("JWT_SECRET_FILE", str(present))

    assert Settings(_env_file=None).jwt_secret == FAKE_SECRETS["jwt_secret"]


def test_mixed_case_reference_error_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("Jwt_Secret_File", str(missing))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "JWT_SECRET_FILE" in str(caught.value).upper()
    assert str(missing) in str(caught.value)


def test_directory_reference_is_rejected_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(tmp_path))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "SMTP_PASSWORD_FILE" in str(caught.value)
    assert str(tmp_path) in str(caught.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_fifo_reference_is_rejected_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(fifo))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "SMTP_PASSWORD_FILE" in str(caught.value)
    assert str(fifo) in str(caught.value)


@pytest.mark.skipif(not Path("/dev/zero").exists(), reason="needs a character device")
def test_device_reference_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PASSWORD_FILE", "/dev/zero")

    with pytest.raises(ValueError, match="SMTP_PASSWORD_FILE"):
        Settings(_env_file=None)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads files mode 000"
)
def test_permission_denied_reference_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "locked", FAKE_SECRETS["smtp_password"].encode())
    secret.chmod(0)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(secret))

    try:
        with pytest.raises(ValueError) as caught:
            Settings(_env_file=None)
    finally:
        secret.chmod(0o600)
    assert "SMTP_PASSWORD_FILE" in str(caught.value)
    assert str(secret) in str(caught.value)


def test_open_failure_is_reported_without_chaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "locked", FAKE_SECRETS["smtp_password"].encode())
    real_open = os.open

    def deny(path, flags, *args, **kwargs):
        if os.fspath(path) == str(secret):
            raise PermissionError(13, "Permission denied", str(secret))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(secret))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "SMTP_PASSWORD_FILE" in str(caught.value)
    assert str(secret) in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_non_utf8_reference_is_rejected_without_echoing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "binary", b"FAKEHEAD-" + MARK.encode() + b"\xff\xfeFAKETAIL")
    monkeypatch.setenv("GAPGPT_API_KEY_FILE", str(secret))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "GAPGPT_API_KEY_FILE" in str(caught.value)
    assert str(secret) in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    for text in exception_texts(caught.value):
        for fragment in ("FAKEHEAD", "FAKETAIL", MARK, "0xff", "\\xff"):
            assert fragment not in text


# --- A9: bounded reads, no special files, no kernel file systems ------------------------

SECRET_FILE_LIMIT = 64 * 1024


def _record_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    real_open = os.open

    def recording_open(path, flags, *args, **kwargs):
        opened.append(os.fspath(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    return opened


def test_secret_file_of_exactly_64_kib_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "limit", b"k" * SECRET_FILE_LIMIT)
    monkeypatch.setenv("ZAI_API_KEY_FILE", str(secret))

    assert Settings(_env_file=None).zai_api_key == "k" * SECRET_FILE_LIMIT


@pytest.mark.parametrize("size", [SECRET_FILE_LIMIT + 1, 64 * 1024 * 1024])
def test_secret_file_larger_than_64_kib_is_rejected_after_a_bounded_read(
    size: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "sparse"
    with secret.open("wb") as handle:
        handle.write(MARK.encode())
        handle.truncate(size)  # sparse: the tail is a hole, never written
    read_sizes: list[int] = []
    real_read = os.read

    def counting_read(descriptor: int, length: int) -> bytes:
        data = real_read(descriptor, length)
        read_sizes.append(len(data))
        return data

    monkeypatch.setattr(os, "read", counting_read)
    monkeypatch.setenv("ZAI_API_KEY_FILE", str(secret))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "ZAI_API_KEY_FILE" in str(caught.value)
    assert str(secret) in str(caught.value)
    assert "64 KiB" in str(caught.value)
    assert 0 < sum(read_sizes) <= SECRET_FILE_LIMIT + 1
    for text in exception_texts(caught.value):
        assert MARK not in text


@pytest.mark.parametrize("kind", ["directory", "fifo", "device"])
def test_non_regular_path_is_rejected_before_it_is_opened(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if kind == "directory":
        target = tmp_path
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("needs POSIX FIFOs")
        target = tmp_path / "fifo"
        os.mkfifo(target)
    else:
        target = Path("/dev/zero")
        if not target.exists():
            pytest.skip("needs a character device")
    opened = _record_opens(monkeypatch)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(target))

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    assert "SMTP_PASSWORD_FILE" in str(caught.value)
    assert str(target) in str(caught.value)
    assert "not a regular file" in str(caught.value)
    assert str(target) not in opened


@pytest.mark.parametrize(
    "target",
    ["/proc/self/environ", "/proc/self/status", "/proc", "/sys/kernel/fake-absent-entry", "/sys"],
)
def test_kernel_file_system_paths_are_rejected(
    target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ_marker = f"environ-{MARK}-marker"
    monkeypatch.setenv("HOMAYAR_TEST_ENVIRON_MARKER", environ_marker)
    opened = _record_opens(monkeypatch)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", target)

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert "SMTP_PASSWORD_FILE" in message
    assert target in message
    assert "/proc or /sys" in message
    assert target not in opened
    for text in exception_texts(caught.value):
        assert environ_marker not in text


def test_symlink_into_proc_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    link = tmp_path / "looks-harmless"
    link.symlink_to("/proc/self/environ")
    opened = _record_opens(monkeypatch)
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(link))

    with pytest.raises(ValueError, match="/proc or /sys") as caught:
        Settings(_env_file=None)

    assert str(link) in str(caught.value)
    assert str(link) not in opened


def test_directories_merely_named_proc_or_sys_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "proc" / "sys"
    folder.mkdir(parents=True)
    secret = write_secret(folder / "secret", FAKE_SECRETS["smtp_password"].encode())
    monkeypatch.setenv("SMTP_PASSWORD_FILE", str(secret))

    assert Settings(_env_file=None).smtp_password == FAKE_SECRETS["smtp_password"]


def test_empty_reference_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_FILE", "")

    with pytest.raises(ValueError, match="JWT_SECRET_FILE"):
        Settings(_env_file=None)


def test_empty_file_yields_empty_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = write_secret(tmp_path / "empty", b"")
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret))

    assert Settings(_env_file=None).jwt_secret == ""


def test_empty_file_fails_production_jwt_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "empty", b"\n")
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret))
    kwargs = production_kwargs()
    del kwargs["jwt_secret"]

    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(**kwargs)


def test_explicit_keyword_takes_precedence_over_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "jwt", FAKE_SECRETS["jwt_secret"].encode())
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret))

    assert Settings(_env_file=None, jwt_secret="k" * 40).jwt_secret == "k" * 40


def test_broken_reference_is_loud_even_under_an_explicit_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET_FILE", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="JWT_SECRET_FILE"):
        Settings(_env_file=None, jwt_secret="k" * 40)


# --- .env files: references at .env precedence ------------------------------------------


def _dotenv(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "settings.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_dotenv_reference_is_honoured(tmp_path: Path) -> None:
    secret = write_secret(tmp_path / "pepper", FAKE_SECRETS["token_pepper"].encode() + b"\n")
    env_file = _dotenv(tmp_path, f"TOKEN_PEPPER_FILE={secret}")

    assert Settings(_env_file=env_file).token_pepper == FAKE_SECRETS["token_pepper"]


def test_dotenv_value_and_reference_together_are_rejected(tmp_path: Path) -> None:
    secret = write_secret(tmp_path / "pepper", FAKE_SECRETS["token_pepper"].encode())
    env_file = _dotenv(
        tmp_path, f"TOKEN_PEPPER=direct-{MARK}-pepper", f"TOKEN_PEPPER_FILE={secret}"
    )

    with pytest.raises(ValueError, match="TOKEN_PEPPER_FILE") as caught:
        Settings(_env_file=env_file)
    for text in exception_texts(caught.value):
        assert_no_secret(text, {"file": FAKE_SECRETS["token_pepper"], "d": f"direct-{MARK}-pepper"})


def test_environment_reference_overrides_dotenv_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "pepper", FAKE_SECRETS["token_pepper"].encode())
    env_file = _dotenv(tmp_path, "TOKEN_PEPPER=")
    monkeypatch.setenv("TOKEN_PEPPER_FILE", str(secret))

    assert Settings(_env_file=env_file).token_pepper == FAKE_SECRETS["token_pepper"]


def test_environment_value_overrides_dotenv_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "pepper", FAKE_SECRETS["token_pepper"].encode())
    env_file = _dotenv(tmp_path, f"TOKEN_PEPPER_FILE={secret}")
    monkeypatch.setenv("TOKEN_PEPPER", "e" * 40)

    assert Settings(_env_file=env_file).token_pepper == "e" * 40


def test_shadowed_dotenv_reference_is_still_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _dotenv(tmp_path, f"TOKEN_PEPPER_FILE={tmp_path / 'missing'}")
    monkeypatch.setenv("TOKEN_PEPPER", "e" * 40)

    with pytest.raises(ValueError, match="TOKEN_PEPPER_FILE"):
        Settings(_env_file=env_file)


# --- AC3: no secret in repr, str or error text ------------------------------------------


def test_repr_and_str_hide_every_secret_value() -> None:
    settings = Settings(_env_file=None, api_prefix="/api/v1", **all_secrets_kwargs())

    for text in (repr(settings), str(settings)):
        assert_no_secret(text, FAKE_SECRETS)
        assert "environment='development'" in text
        assert "api_prefix='/api/v1'" in text
    assert settings.jwt_secret == FAKE_SECRETS["jwt_secret"]


def test_repr_hides_values_supplied_through_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for field, value in FAKE_SECRETS.items():
        monkeypatch.setenv(
            f"{field.upper()}_FILE", str(write_secret(tmp_path / field, value.encode()))
        )

    settings = Settings(_env_file=None, pii_mapping_active_key_id="fake1")

    assert {field: getattr(settings, field) for field in SECRET_FIELDS} == FAKE_SECRETS
    assert_no_secret(repr(settings), FAKE_SECRETS)
    assert_no_secret(str(settings), FAKE_SECRETS)


def test_contract_fields_are_the_referencable_and_hidden_fields() -> None:
    from backend.config import SECRET_FIELDS as CONFIGURED_SECRET_FIELDS

    hidden = {name for name, field in Settings.model_fields.items() if field.repr is False}
    assert set(CONFIGURED_SECRET_FIELDS) == set(SECRET_FIELDS) == hidden


PRODUCTION_FAILURES: dict[str, dict[str, object]] = {
    "insecure_cookies": {"secure_cookies": False},
    "demo_account": {"enable_demo_account": True},
    "mock_providers": {"allow_mock_providers": True},
    "manifest_not_strict": {"model_manifest_strict": False},
    "auto_schema": {"auto_create_schema": True},
    "short_jwt": {"jwt_secret": f"short-{MARK}"},
    "short_pepper": {"token_pepper": f"pepper-{MARK}"},
    "sqlite": {"database_url": f"sqlite:///fake-{MARK}.db"},
    "plaintext_smtp": {"smtp_host": "smtp.example.invalid", "smtp_port": 25},
    "http_object_store": {
        "storage_backend": "s3",
        "storage_endpoint_url": "http://minio.example.invalid:9000",
    },
}


@pytest.mark.parametrize("case", sorted(PRODUCTION_FAILURES))
def test_production_errors_contain_no_secret(case: str) -> None:
    kwargs = production_kwargs(**all_secrets_kwargs(**PRODUCTION_FAILURES[case]))
    secrets = {field: str(kwargs[field]) for field in SECRET_FIELDS}

    with pytest.raises(ValidationError) as caught:
        Settings(**kwargs)

    for text in exception_texts(caught.value):
        assert_no_secret(text, secrets)


NON_PRODUCTION_FAILURES: dict[str, dict[str, object]] = {
    "bad_pii_base64": {"pii_mapping_keys": f"fake1:not-base64-{MARK}!!"},
    "short_pii_key": {"pii_mapping_keys": f"fake1:{base64.b64encode(MARK.encode()).decode()}"},
    "unknown_active_key": {"pii_mapping_active_key_id": "absent"},
    "google_without_client_id": {"enable_google_auth": True, "google_client_id": ""},
    "s3_without_bucket": {"storage_backend": "s3", "storage_bucket": ""},
    "bad_port": {"smtp_port": "not-a-port"},
    "bad_environment": {"environment": "prod"},
}


@pytest.mark.parametrize("case", sorted(NON_PRODUCTION_FAILURES))
def test_other_settings_errors_contain_no_secret(case: str) -> None:
    kwargs = all_secrets_kwargs(**NON_PRODUCTION_FAILURES[case])
    secrets = {field: str(kwargs[field]) for field in SECRET_FIELDS}

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **kwargs)

    for text in exception_texts(caught.value):
        assert_no_secret(text, secrets)


def test_errors_hide_unrelated_dotenv_secrets(tmp_path: Path) -> None:
    unrelated = f"fake-minio-root-password-{MARK}"
    env_file = _dotenv(tmp_path, f"MINIO_ROOT_PASSWORD={unrelated}", "SECURE_COOKIES=false")
    kwargs = production_kwargs(_env_file=env_file)
    del kwargs["secure_cookies"]

    with pytest.raises(ValidationError, match="SECURE_COOKIES") as caught:
        Settings(**kwargs)

    for text in exception_texts(caught.value):
        assert unrelated not in text


def test_validation_errors_keep_their_type_and_message() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(**production_kwargs(secure_cookies=False))

    errors = caught.value.errors()
    assert [error["type"] for error in errors] == ["value_error"]
    assert "SECURE_COOKIES must be enabled in production" in errors[0]["msg"]
    assert caught.value.title == "Settings"


# --- AC4: mandatory secure production settings ------------------------------------------


@pytest.mark.parametrize("port", [25, 1025, 2525])
def test_production_rejects_plaintext_smtp(port: int) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(
            **production_kwargs(
                smtp_host="smtp.example.invalid", smtp_use_tls=False, smtp_port=port
            )
        )
    message = str(caught.value)
    assert "SMTP_HOST" in message
    assert "SMTP_USE_TLS" in message
    assert "SMTP_PORT" in message


@pytest.mark.parametrize(
    "overrides",
    [
        {"smtp_host": "smtp.example.invalid", "smtp_use_tls": False, "smtp_port": 587},
        {"smtp_host": "smtp.example.invalid", "smtp_use_tls": True, "smtp_port": 587},
        {"smtp_host": "smtp.example.invalid", "smtp_use_tls": False, "smtp_port": 465},
        {"smtp_host": "smtp.example.invalid", "smtp_use_tls": True, "smtp_port": 465},
        {"smtp_host": "smtp.example.invalid", "smtp_use_tls": True, "smtp_port": 25},
        {"smtp_host": "", "smtp_use_tls": False, "smtp_port": 25},
    ],
)
def test_production_accepts_encrypted_smtp_or_no_smtp(overrides: dict[str, object]) -> None:
    settings = Settings(**production_kwargs(**overrides))
    assert settings.smtp_port == overrides["smtp_port"]


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
def test_plaintext_smtp_rule_is_production_only(environment: str) -> None:
    settings = Settings(
        _env_file=None,
        environment=environment,
        smtp_host="smtp.example.invalid",
        smtp_use_tls=False,
        smtp_port=25,
    )
    assert settings.smtp_use_tls is False


S3_CREDENTIALS = {
    "storage_backend": "s3",
    "storage_access_key": FAKE_SECRETS["storage_access_key"],
    "storage_secret_key": FAKE_SECRETS["storage_secret_key"],
}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://minio.example.invalid:9000",
        "HTTP://minio.example.invalid",
        "Http://minio.example.invalid",
        "ftp://minio.example.invalid",
        "minio.example.invalid:9000",
        "https:/minio.example.invalid",
        "https://",
        " https://minio.example.invalid",
        "https://[::1",
    ],
)
def test_production_rejects_non_https_object_store_endpoint(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="STORAGE_ENDPOINT_URL") as caught:
        Settings(**production_kwargs(**S3_CREDENTIALS, storage_endpoint_url=endpoint))
    for text in exception_texts(caught.value):
        assert_no_secret(text, {"endpoint": endpoint} if len(endpoint) > 10 else {})


@pytest.mark.parametrize(
    "endpoint",
    ["", "https://minio.example.invalid", "HTTPS://s3.example.invalid:8443/base"],
)
@pytest.mark.parametrize("deployment_mode", ["cloud", "onprem"])
def test_production_accepts_https_or_default_object_store_endpoint(
    endpoint: str, deployment_mode: str
) -> None:
    settings = Settings(
        **production_kwargs(
            **S3_CREDENTIALS, deployment_mode=deployment_mode, storage_endpoint_url=endpoint
        )
    )
    assert settings.storage_endpoint_url == endpoint
    assert settings.storage_allow_insecure_endpoint is False


@pytest.mark.parametrize("endpoint", ["http://minio:9000", "HTTP://minio:9000"])
def test_onprem_production_may_opt_in_to_an_insecure_endpoint(endpoint: str) -> None:
    settings = Settings(
        **production_kwargs(
            **S3_CREDENTIALS,
            deployment_mode="onprem",
            storage_endpoint_url=endpoint,
            storage_allow_insecure_endpoint=True,
        )
    )
    assert settings.storage_endpoint_url == endpoint


def test_cloud_production_cannot_opt_in_to_an_insecure_endpoint() -> None:
    with pytest.raises(ValidationError, match="STORAGE_ENDPOINT_URL"):
        Settings(
            **production_kwargs(
                **S3_CREDENTIALS,
                deployment_mode="cloud",
                storage_endpoint_url="http://minio.example.invalid:9000",
                storage_allow_insecure_endpoint=True,
            )
        )


def test_onprem_production_without_the_flag_rejects_an_insecure_endpoint() -> None:
    with pytest.raises(ValidationError, match="STORAGE_ALLOW_INSECURE_ENDPOINT"):
        Settings(
            **production_kwargs(
                **S3_CREDENTIALS,
                deployment_mode="onprem",
                storage_endpoint_url="http://minio:9000",
            )
        )


def test_insecure_endpoint_flag_defaults_off_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).storage_allow_insecure_endpoint is False

    monkeypatch.setenv("STORAGE_ALLOW_INSECURE_ENDPOINT", "true")
    monkeypatch.setenv("DEPLOYMENT_MODE", "onprem")
    settings = Settings(**production_kwargs(**S3_CREDENTIALS, storage_endpoint_url="http://m:9000"))

    assert settings.storage_allow_insecure_endpoint is True


def test_onprem_compose_profile_passes_the_object_store_rule() -> None:
    compose_file = PROJECT_ROOT.parent / "infra" / "compose" / "compose.onprem.yaml"
    if not compose_file.exists():
        pytest.skip("infra/compose is not part of this checkout")
    compose = compose_file.read_text(encoding="utf-8")

    def app_value(key: str) -> str:
        values = re.findall(rf'^  {key}:\s*"?([^"\n]*?)"?\s*$', compose, flags=re.MULTILINE)
        assert len(values) == 1, key
        return values[0]

    settings = Settings(
        **production_kwargs(
            **S3_CREDENTIALS,
            deployment_mode=app_value("DEPLOYMENT_MODE"),
            storage_endpoint_url=app_value("STORAGE_ENDPOINT_URL"),
            storage_allow_insecure_endpoint=app_value("STORAGE_ALLOW_INSECURE_ENDPOINT"),
        )
    )
    assert settings.deployment_mode == "onprem"
    assert settings.storage_allow_insecure_endpoint is True


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
def test_http_object_store_rule_is_production_only(environment: str) -> None:
    settings = Settings(
        _env_file=None,
        environment=environment,
        storage_endpoint_url="http://localhost:9000",
        **S3_CREDENTIALS,
    )
    assert settings.storage_endpoint_url == "http://localhost:9000"


def test_production_local_storage_ignores_endpoint_scheme() -> None:
    settings = Settings(
        **production_kwargs(storage_backend="local", storage_endpoint_url="http://unused.invalid")
    )
    assert settings.storage_backend == "local"


# --- AC5: no embedded literal credentials -----------------------------------------------


def test_dev_compose_takes_neo4j_credential_from_a_required_reference() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    values = re.findall(r"^\s*NEO4J_AUTH:\s*(.+?)\s*$", compose, flags=re.MULTILINE)

    assert len(values) == 1
    user, separator, password = values[0].strip("\"'").partition("/")
    assert separator == "/"
    assert user in {"neo4j", "${NEO4J_USER:-neo4j}"}
    assert re.fullmatch(r"\$\{NEO4J_PASSWORD:\?[^}]+\}", password)


class _SeedStopped(Exception):
    """Raised by the fake driver's session so the seed never touches a database."""


class _FakeGraphDatabase:
    calls: list[tuple[str, tuple[str, str]]]

    def __init__(self) -> None:
        self.calls = []

    def driver(self, uri: str, auth: tuple[str, str]):
        self.calls.append((uri, auth))
        return self

    def session(self, **_: object):
        raise _SeedStopped

    def close(self) -> None:
        return None


@pytest.fixture
def fake_graph(monkeypatch: pytest.MonkeyPatch) -> _FakeGraphDatabase:
    fake = _FakeGraphDatabase()
    monkeypatch.setattr(seed_module, "GraphDatabase", fake)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    return fake


def test_seed_refuses_an_absent_password_before_opening_a_driver(
    fake_graph: _FakeGraphDatabase,
) -> None:
    with pytest.raises((RuntimeError, ValueError, SystemExit), match="NEO4J_PASSWORD"):
        seed_module.seed()
    assert fake_graph.calls == []


def test_seed_refuses_an_empty_password_before_opening_a_driver(
    fake_graph: _FakeGraphDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEO4J_PASSWORD", "")

    with pytest.raises((RuntimeError, ValueError, SystemExit), match="NEO4J_PASSWORD"):
        seed_module.seed()
    assert fake_graph.calls == []


def test_seed_connects_with_settings_read_at_run_time(
    fake_graph: _FakeGraphDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = write_secret(tmp_path / "neo4j", FAKE_SECRETS["neo4j_password"].encode() + b"\n")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.example.invalid:7687")
    monkeypatch.setenv("NEO4J_USER", "seed-user")
    monkeypatch.setenv("NEO4J_PASSWORD_FILE", str(secret))

    with pytest.raises(_SeedStopped):
        seed_module.seed()

    assert fake_graph.calls == [
        ("bolt://neo4j.example.invalid:7687", ("seed-user", FAKE_SECRETS["neo4j_password"]))
    ]


def test_seed_script_exits_non_zero_without_a_password() -> None:
    env = dict(os.environ)  # already stripped of Settings variables by the autouse fixture
    env.update(NEO4J_PASSWORD="", NEO4J_URI="bolt://127.0.0.1:9")
    result = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
        [sys.executable, "-m", "backend.database.seed"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode != 0
    assert "NEO4J_PASSWORD" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_seed_refuses_outside_development_and_test(
    environment: str, fake_graph: _FakeGraphDatabase
) -> None:
    graph = {"neo4j_uri": "bolt://graph.example.invalid:7687"}
    password = FAKE_SECRETS["neo4j_password"]
    if environment == "production":
        settings = Settings(**production_kwargs(neo4j_password=password, **graph))
    else:
        settings = Settings(
            _env_file=None, environment=environment, neo4j_password=password, **graph
        )

    with pytest.raises((RuntimeError, ValueError, SystemExit), match="ENVIRONMENT") as caught:
        seed_module.seed(settings)

    assert fake_graph.calls == []
    for text in exception_texts(caught.value):
        assert_no_secret(text, {"neo4j_password": password})


def test_seed_reads_the_environment_setting_at_run_time(
    fake_graph: _FakeGraphDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("NEO4J_PASSWORD", FAKE_SECRETS["neo4j_password"])

    with pytest.raises((RuntimeError, ValueError, SystemExit), match="ENVIRONMENT"):
        seed_module.seed()
    assert fake_graph.calls == []


@pytest.mark.parametrize("environment", ["development", "test"])
def test_seed_runs_in_development_and_test(
    environment: str, fake_graph: _FakeGraphDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("NEO4J_PASSWORD", FAKE_SECRETS["neo4j_password"])

    with pytest.raises(_SeedStopped):
        seed_module.seed()
    assert len(fake_graph.calls) == 1


def test_seed_script_exits_non_zero_outside_development_and_test() -> None:
    env = dict(os.environ)  # already stripped of Settings variables by the autouse fixture
    env.update(
        ENVIRONMENT="staging",
        NEO4J_PASSWORD=FAKE_SECRETS["neo4j_password"],
        NEO4J_URI="bolt://127.0.0.1:9",
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
        [sys.executable, "-m", "backend.database.seed"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode != 0
    assert "ENVIRONMENT" in result.stderr
    assert "Traceback" not in result.stderr
    assert FAKE_SECRETS["neo4j_password"] not in result.stderr + result.stdout


def test_seed_source_embeds_no_connection_literal() -> None:
    tree = ast.parse(Path(seed_module.__file__).read_text(encoding="utf-8"))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert not [value for value in literals if re.match(r"(bolt|neo4j)(\+s|\+ssc)?://", value)]
    assert not hasattr(seed_module, "AUTH")


# --- AC6: plain variables and .env files keep working -----------------------------------


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_plain_environment_variable_still_supplies_the_value(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(field.upper(), FAKE_SECRETS[field])

    settings = Settings(_env_file=None, **COMPANIONS.get(field, {}))

    assert getattr(settings, field) == FAKE_SECRETS[field]


def test_plain_variable_keeps_its_exact_value(monkeypatch: pytest.MonkeyPatch) -> None:
    exact = f" fake-{MARK}-with-newline\n"
    monkeypatch.setenv("smtp_password", exact)

    assert Settings(_env_file=None).smtp_password == exact


def test_dotenv_value_still_works_and_environment_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _dotenv(tmp_path, f"ZAI_API_KEY=dotenv-{MARK}", f"GAPGPT_API_KEY=dotenv-{MARK}")
    monkeypatch.setenv("GAPGPT_API_KEY", f"environment-{MARK}")

    settings = Settings(_env_file=env_file)

    assert settings.zai_api_key == f"dotenv-{MARK}"
    assert settings.gapgpt_api_key == f"environment-{MARK}"
