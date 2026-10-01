from __future__ import annotations

import base64
import itertools
import os
import re
import runpy
import sys
from pathlib import Path

import neo4j
import pytest
import yaml

from backend.config import Settings, reset_settings_cache

HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = HOMAYAR_ROOT.parent
DEPLOYMENT_DOC = REPOSITORY_ROOT / "docs" / "operations" / "deployment.md"
ENV_EXAMPLE = HOMAYAR_ROOT / ".env.example"
DEVELOPMENT_COMPOSE = HOMAYAR_ROOT / "docker-compose.yml"
INFRA_COMPOSE_FILES = (
    REPOSITORY_ROOT / "infra" / "compose" / "compose.cloud.yaml",
    REPOSITORY_ROOT / "infra" / "compose" / "compose.onprem.yaml",
)
SEED_SCRIPT = HOMAYAR_ROOT / "backend" / "database" / "seed.py"
README = HOMAYAR_ROOT / "README.md"

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
BASE_DEFAULTS = {
    "database_url": "sqlite:///./homayar-dev.db",
    "redis_url": "redis://localhost:6379/0",
}
SPELLINGS = ("upper", "lower", "mixed")
SOURCES = ("arguments", "environment", "files")
PII_KEY_ID = "fakekey"
URL_CREDENTIAL_FIELDS = ("database_url", "redis_url")
LEAK_WINDOW = 12
AMENDED_SETTINGS = ("storage_allow_insecure_endpoint",)


def _filler(field: str) -> str:
    return (field.replace("_", "") * 8)[:40]


def _fake_value(field: str, tag: str = "") -> str:
    label = field.replace("_", "-") + (f"-{tag}" if tag else "")
    marker = f"fake-{label}-{_filler(field)}"
    if field == "database_url":
        return f"postgresql+psycopg://fake-user:{marker}@db.example.invalid/homayar"
    if field == "redis_url":
        return f"redis://:{marker}@redis.example.invalid:6379/0"
    if field == "sentry_dsn":
        return f"https://{marker}@fake-sentry-dsn-host.invalid/1"
    if field == "pii_mapping_keys":
        material = base64.b64encode((b"fake-pii-" + (tag or "k").encode() * 32)[:32]).decode()
        return f"{PII_KEY_ID}{tag}:{material}"
    return marker


FAKE_SECRETS = {field: _fake_value(field) for field in SECRET_FIELDS}


def _sensitive_parts(field: str, value: str) -> list[str]:
    if field in URL_CREDENTIAL_FIELDS:
        match = re.search(r"://[^@/]*:([^@/]+)@", value)
        if match:
            return [match.group(1)]
    if field == "pii_mapping_keys":
        keys = [part.partition(":")[2].strip() for part in value.split(",") if ":" in part]
        return [value, *(key for key in keys if key)]
    return [value]


def _part_leaks(part: str, text: str) -> bool:
    size = min(len(part), LEAK_WINDOW)
    return any(part[start : start + size] in text for start in range(len(part) - size + 1))


def _leaked_fields(text: str, values: dict) -> list[str]:
    return sorted(
        field
        for field, value in values.items()
        if field in SECRET_FIELDS
        and isinstance(value, str)
        and value.strip()
        and (
            value in text
            or any(_part_leaks(part, text) for part in _sensitive_parts(field, value.strip()))
        )
    )


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return chain


def _error_text(exc: BaseException) -> str:
    return "\n".join(f"{error!s}\n{error!r}" for error in _exception_chain(exc))


def _variable(name: str, spelling: str) -> str:
    if spelling == "upper":
        return name.upper()
    if spelling == "lower":
        return name.lower()
    return "".join(
        character.upper() if index % 2 == 0 else character.lower()
        for index, character in enumerate(name)
    )


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode("utf-8"))
    return path


def _companions(field: str, value: str) -> dict[str, str]:
    if field == "pii_mapping_keys" and value.strip():
        return {"pii_mapping_active_key_id": value.strip().partition(":")[0]}
    return {}


def _all_companions(values: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for field, value in values.items():
        merged.update(_companions(field, value))
    return merged


def _mismatched_fields(settings: Settings, values: dict[str, str]) -> list[str]:
    return sorted(field for field, value in values.items() if getattr(settings, field) != value)


def _test_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"environment": "test", **overrides})


def _production_overrides() -> dict:
    return {
        "environment": "production",
        "database_url": FAKE_SECRETS["database_url"],
        "jwt_secret": FAKE_SECRETS["jwt_secret"],
        "token_pepper": FAKE_SECRETS["token_pepper"],
        "secure_cookies": True,
        "enable_demo_account": False,
        "allow_mock_providers": False,
        "auto_create_schema": False,
    }


def _production_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_production_overrides(), **overrides})


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        name = key.lower()
        if name.endswith("_file"):
            name = name[: -len("_file")]
        if name in Settings.model_fields or name in AMENDED_SETTINGS:
            monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _set_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    content: str,
    spelling: str = "upper",
) -> Path:
    path = _write(tmp_path, f"{field}.secret", content)
    monkeypatch.setenv(_variable(f"{field}_file", spelling), str(path))
    return path


def _supply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str, values: dict[str, str]
) -> dict[str, str]:
    if source == "arguments":
        return dict(values)
    for field, value in values.items():
        if source == "environment":
            monkeypatch.setenv(field.upper(), value)
        else:
            _set_secret_file(monkeypatch, tmp_path, field, value + "\n")
    return {}


def _newline_variants(value: str) -> dict[str, tuple[str, str]]:
    return {
        "lf": (value + "\n", value),
        "crlf": (value + "\r\n", value),
        "unterminated": (value, value),
        "two_lf": (value + "\n\n", value + "\n"),
        "two_crlf": (value + "\r\n\r\n", value + "\r\n"),
        "lf_then_crlf": (value + "\n\r\n", value + "\n"),
        "lone_cr": (value + "\r", value + "\r"),
        "surrounding_spaces": ("  " + value + "  \n", "  " + value + "  "),
        "surrounding_tabs": ("\t" + value + "\t\n", "\t" + value + "\t"),
        "internal_lf": (value + "\n" + value + "\n", value + "\n" + value),
        "internal_crlf": (value + "\r\n" + value + "\r\n", value + "\r\n" + value),
        "only_lf": ("\n", ""),
        "only_crlf": ("\r\n", ""),
        "empty": ("", ""),
    }


VARIANT_NAMES = tuple(_newline_variants("v"))
VARIANT_CASES = [
    (field, variant)
    for field in SECRET_FIELDS
    for variant in VARIANT_NAMES
    if not (field == "pii_mapping_keys" and variant.startswith("internal"))
]


class TestSecretReferenceFiles:
    @pytest.mark.parametrize("spelling", SPELLINGS)
    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_file_variable_supplies_the_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, spelling: str
    ) -> None:
        value = FAKE_SECRETS[field]
        _set_secret_file(monkeypatch, tmp_path, field, value + "\n", spelling)

        settings = _test_settings(**_companions(field, value))

        assert getattr(settings, field) == value

    @pytest.mark.parametrize(("field", "variant"), VARIANT_CASES)
    def test_exactly_one_trailing_newline_is_removed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, variant: str
    ) -> None:
        content, expected = _newline_variants(FAKE_SECRETS[field])[variant]
        _set_secret_file(monkeypatch, tmp_path, field, content)

        settings = _test_settings(**_companions(field, expected))

        assert getattr(settings, field) == expected

    def test_file_for_one_field_and_value_for_another_are_both_used(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_secret_file(monkeypatch, tmp_path, "jwt_secret", FAKE_SECRETS["jwt_secret"] + "\n")
        monkeypatch.setenv("TOKEN_PEPPER", FAKE_SECRETS["token_pepper"])

        settings = _test_settings()

        assert settings.jwt_secret == FAKE_SECRETS["jwt_secret"]
        assert settings.token_pepper == FAKE_SECRETS["token_pepper"]

    def test_file_values_satisfy_production(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for field in ("database_url", "jwt_secret", "token_pepper"):
            _set_secret_file(monkeypatch, tmp_path, field, FAKE_SECRETS[field] + "\n")
        overrides = {
            key: value for key, value in _production_overrides().items() if key not in SECRET_FIELDS
        }

        settings = Settings(_env_file=None, **overrides)

        assert settings.is_production
        assert settings.jwt_secret == FAKE_SECRETS["jwt_secret"]
        assert settings.database_url == FAKE_SECRETS["database_url"]


def _load_other_secrets(monkeypatch: pytest.MonkeyPatch, field: str) -> dict[str, str]:
    others = {name: value for name, value in FAKE_SECRETS.items() if name != field}
    for name, value in others.items():
        monkeypatch.setenv(name.upper(), value)
    for name, value in _all_companions(others).items():
        monkeypatch.setenv(name.upper(), value)
    return others


class TestSecretReferenceFailures:
    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_value_and_file_together_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
    ) -> None:
        plain = _fake_value(field, "plain")
        from_file = _fake_value(field, "file")
        monkeypatch.setenv(field.upper(), plain)
        _set_secret_file(monkeypatch, tmp_path, field, from_file + "\n")

        with pytest.raises(ValueError) as caught:
            _test_settings(**_companions(field, plain))

        text = _error_text(caught.value)
        assert field in text.lower()
        assert _leaked_fields(text, {field: plain}) == []
        assert _leaked_fields(text, {field: from_file}) == []

    @pytest.mark.parametrize(
        ("value_spelling", "file_spelling"), list(itertools.product(SPELLINGS, SPELLINGS))
    )
    def test_conflict_is_detected_in_any_spelling(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        value_spelling: str,
        file_spelling: str,
    ) -> None:
        plain = _fake_value("jwt_secret", "plain")
        from_file = _fake_value("jwt_secret", "file")
        monkeypatch.setenv(_variable("jwt_secret", value_spelling), plain)
        _set_secret_file(monkeypatch, tmp_path, "jwt_secret", from_file + "\n", file_spelling)

        with pytest.raises(ValueError) as caught:
            _test_settings()

        text = _error_text(caught.value)
        assert "jwt_secret" in text.lower()
        assert _leaked_fields(text, {"jwt_secret": plain}) == []
        assert _leaked_fields(text, {"jwt_secret": from_file}) == []

    def test_only_the_file_variable_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from_file = _fake_value("jwt_secret", "file")
        _set_secret_file(monkeypatch, tmp_path, "jwt_secret", from_file + "\n")

        assert _test_settings().jwt_secret == from_file

    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_missing_file_is_rejected_naming_variable_and_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
    ) -> None:
        others = _load_other_secrets(monkeypatch, field)
        missing = tmp_path / "absent" / f"{field}.secret"
        monkeypatch.setenv(f"{field}_FILE".upper(), str(missing))

        with pytest.raises(ValueError) as caught:
            _test_settings()

        text = _error_text(caught.value)
        assert field in text.lower()
        assert str(missing) in text
        assert _leaked_fields(text, others) == []

    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_existing_file_for_the_same_variable_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
    ) -> None:
        others = _load_other_secrets(monkeypatch, field)
        value = FAKE_SECRETS[field]
        _set_secret_file(monkeypatch, tmp_path, field, value + "\n")

        settings = _test_settings(**_companions(field, value))

        assert getattr(settings, field) == value
        assert _mismatched_fields(settings, others) == []

    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_unreadable_path_is_rejected_naming_variable_and_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
    ) -> None:
        others = _load_other_secrets(monkeypatch, field)
        directory = tmp_path / f"{field}-directory"
        directory.mkdir()
        monkeypatch.setenv(f"{field}_FILE".upper(), str(directory))

        with pytest.raises(ValueError) as caught:
            _test_settings()

        text = _error_text(caught.value)
        assert field in text.lower()
        assert str(directory) in text
        assert _leaked_fields(text, others) == []

    @pytest.mark.parametrize("content", ["", "\n", "\r\n"])
    @pytest.mark.parametrize("field", ["jwt_secret", "token_pepper"])
    def test_empty_file_fails_the_production_length_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, content: str
    ) -> None:
        _set_secret_file(monkeypatch, tmp_path, field, content)
        overrides = {key: value for key, value in _production_overrides().items() if key != field}

        assert getattr(_test_settings(), field) == ""
        with pytest.raises(ValueError) as caught:
            Settings(_env_file=None, **overrides)

        text = _error_text(caught.value)
        assert field in text.lower()
        assert _leaked_fields(text, FAKE_SECRETS) == []

    @pytest.mark.parametrize("field", ["jwt_secret", "token_pepper"])
    def test_long_enough_file_passes_the_production_length_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
    ) -> None:
        _set_secret_file(monkeypatch, tmp_path, field, FAKE_SECRETS[field] + "\n")
        overrides = {key: value for key, value in _production_overrides().items() if key != field}

        settings = Settings(_env_file=None, **overrides)

        assert settings.is_production
        assert getattr(settings, field) == FAKE_SECRETS[field]


PRODUCTION_ERROR_CASES = {
    "jwt_secret_too_short": {"jwt_secret": "fake-jwt-short-" + "jwtshort" * 2},
    "token_pepper_too_short": {"token_pepper": "fake-pepper-short-" + "pepper" * 2},
    "sqlite_database": {"database_url": "sqlite:///fake-database-url-sqlite-" + "sqlite" * 4},
    "insecure_cookies": {"secure_cookies": False},
    "demo_account": {"enable_demo_account": True},
    "mock_providers": {"allow_mock_providers": True},
    "manifest_not_strict": {"model_manifest_strict": False},
    "auto_create_schema": {"auto_create_schema": True},
    "smtp_without_tls": {
        "smtp_host": "smtp.example.invalid",
        "smtp_port": 25,
        "smtp_use_tls": False,
    },
    "s3_plain_http_endpoint": {
        "storage_backend": "s3",
        "storage_bucket": "homayar-private",
        "storage_endpoint_url": "http://minio.example.invalid:9000",
    },
    "s3_plain_http_endpoint_flag_in_cloud": {
        "storage_backend": "s3",
        "storage_bucket": "homayar-private",
        "storage_endpoint_url": "http://minio.example.invalid:9000",
        "deployment_mode": "cloud",
        "storage_allow_insecure_endpoint": True,
    },
    "s3_plain_http_endpoint_onprem_without_flag": {
        "storage_backend": "s3",
        "storage_bucket": "homayar-private",
        "storage_endpoint_url": "http://minio.example.invalid:9000",
        "deployment_mode": "onprem",
    },
    "google_auth_without_client_id": {"enable_google_auth": True, "google_client_id": ""},
    "s3_without_access_key": {"storage_backend": "s3", "storage_access_key": ""},
}
NON_PRODUCTION_ERROR_CASES = {
    "fallback_without_model": {"llm_fallback_base_url": "http://llm.example.invalid/v1"},
    "pii_active_key_unknown": {"pii_mapping_active_key_id": "otherkey"},
    "pii_keyring_malformed": {"pii_mapping_keys": "fake-pii-malformed-" + "piikeys" * 5},
    "retention_out_of_range": {"retention_days_default": 0},
    "google_auth_without_client_id": {"enable_google_auth": True, "google_client_id": ""},
    "s3_without_access_key": {"storage_backend": "s3", "storage_access_key": ""},
}


def _environment_base(environment: str) -> dict:
    if environment == "production":
        return {k: v for k, v in _production_overrides().items() if k not in SECRET_FIELDS}
    return {"environment": environment}


def _build_with_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
    environment: str,
    rule: dict,
) -> tuple[dict[str, str], dict]:
    secrets = {field: value for field, value in FAKE_SECRETS.items() if field not in rule}
    for field in rule:
        if field in SECRET_FIELDS:
            monkeypatch.delenv(field.upper(), raising=False)
            monkeypatch.delenv(f"{field}_FILE".upper(), raising=False)
    arguments = {
        **_environment_base(environment),
        **_all_companions(secrets),
        **_supply(monkeypatch, tmp_path, source, secrets),
        **rule,
    }
    present = {**secrets, **{k: v for k, v in rule.items() if k in SECRET_FIELDS}}
    return present, arguments


class TestNoSecretExposure:
    @pytest.mark.parametrize("source", SOURCES)
    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_repr_and_str_hide_every_secret(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: str, source: str
    ) -> None:
        present, arguments = _build_with_secrets(monkeypatch, tmp_path, source, environment, {})

        settings = Settings(_env_file=None, **arguments)

        assert _mismatched_fields(settings, present) == []
        for text in (repr(settings), str(settings), f"{settings}"):
            assert _leaked_fields(text, present) == []
            assert "environment" in text
            assert environment in text

    @pytest.mark.parametrize("source", SOURCES)
    @pytest.mark.parametrize("case", sorted(PRODUCTION_ERROR_CASES))
    def test_production_validator_errors_hide_every_secret(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str, source: str
    ) -> None:
        present, arguments = _build_with_secrets(monkeypatch, tmp_path, source, "production", {})
        baseline = Settings(_env_file=None, **arguments)
        assert _mismatched_fields(baseline, present) == []

        rule = PRODUCTION_ERROR_CASES[case]
        present, arguments = _build_with_secrets(monkeypatch, tmp_path, source, "production", rule)
        with pytest.raises(ValueError) as caught:
            Settings(_env_file=None, **arguments)

        assert _leaked_fields(_error_text(caught.value), present) == []

    @pytest.mark.parametrize("source", SOURCES)
    @pytest.mark.parametrize("case", sorted(NON_PRODUCTION_ERROR_CASES))
    def test_non_production_validator_errors_hide_every_secret(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str, source: str
    ) -> None:
        present, arguments = _build_with_secrets(monkeypatch, tmp_path, source, "test", {})
        baseline = Settings(_env_file=None, **arguments)
        assert _mismatched_fields(baseline, present) == []

        rule = NON_PRODUCTION_ERROR_CASES[case]
        present, arguments = _build_with_secrets(monkeypatch, tmp_path, source, "test", rule)
        with pytest.raises(ValueError) as caught:
            Settings(_env_file=None, **arguments)

        assert _leaked_fields(_error_text(caught.value), present) == []


SMTP_HOST = "smtp.example.invalid"
INSECURE_FLAG = "storage_allow_insecure_endpoint"
PLAIN_ENDPOINT = "http://minio.example.invalid:9000"
NON_HTTPS_ENDPOINTS = [
    PLAIN_ENDPOINT,
    "HTTP://minio.example.invalid:9000",
    "minio.example.invalid:9000",
    "ftp://minio.example.invalid",
]
S3_CREDENTIALS = {
    "storage_bucket": "homayar-private",
    "storage_access_key": FAKE_SECRETS["storage_access_key"],
    "storage_secret_key": FAKE_SECRETS["storage_secret_key"],
}


def _smtp(port: int | None, use_tls: bool | None, host: str = SMTP_HOST) -> dict:
    smtp: dict = {"smtp_host": host}
    if port is not None:
        smtp["smtp_port"] = port
    if use_tls is not None:
        smtp["smtp_use_tls"] = use_tls
    return smtp


def _set_optional(monkeypatch: pytest.MonkeyPatch, name: str, value: str | None) -> None:
    if value is not None:
        monkeypatch.setenv(name, value)


class TestProductionSmtpTransport:
    @pytest.mark.parametrize("use_tls", [False, None], ids=["tls_false", "tls_unset"])
    @pytest.mark.parametrize("port", [25, 2525, 1025, None], ids=lambda port: f"port_{port}")
    def test_production_rejects_plaintext_smtp(
        self, port: int | None, use_tls: bool | None
    ) -> None:
        with pytest.raises(ValueError) as caught:
            _production_settings(
                smtp_password=FAKE_SECRETS["smtp_password"], **_smtp(port, use_tls)
            )

        text = _error_text(caught.value)
        assert re.search(r"smtp_(host|port|use_tls)", text.lower())
        assert _leaked_fields(text, FAKE_SECRETS) == []

    @pytest.mark.parametrize("use_tls", [False, None], ids=["tls_false", "tls_unset"])
    @pytest.mark.parametrize("port", [587, 465])
    def test_production_accepts_starttls_and_implicit_tls_ports_without_the_flag(
        self, port: int, use_tls: bool | None
    ) -> None:
        settings = _production_settings(**_smtp(port, use_tls))

        assert settings.smtp_host == SMTP_HOST
        assert settings.smtp_port == port

    @pytest.mark.parametrize("port", [25, 2525, 587, 465, None], ids=lambda port: f"port_{port}")
    def test_production_accepts_any_port_with_the_tls_flag(self, port: int | None) -> None:
        settings = _production_settings(**_smtp(port, True))

        assert settings.smtp_use_tls is True

    @pytest.mark.parametrize("port", [25, 587, None], ids=lambda port: f"port_{port}")
    def test_production_accepts_smtp_disabled(self, port: int | None) -> None:
        assert _production_settings(**_smtp(port, False, host="")).smtp_host == ""

    @pytest.mark.parametrize("environment", ["development", "test"])
    def test_non_production_keeps_plaintext_smtp(self, environment: str) -> None:
        settings = Settings(_env_file=None, environment=environment, **_smtp(25, False))

        assert settings.smtp_use_tls is False

    @pytest.mark.parametrize(
        ("port", "use_tls"), [("25", "false"), ("2525", None), (None, None), ("1025", "0")]
    )
    def test_production_rejects_plaintext_smtp_from_environment(
        self, monkeypatch: pytest.MonkeyPatch, port: str | None, use_tls: str | None
    ) -> None:
        monkeypatch.setenv("SMTP_HOST", SMTP_HOST)
        _set_optional(monkeypatch, "SMTP_PORT", port)
        _set_optional(monkeypatch, "SMTP_USE_TLS", use_tls)

        with pytest.raises(ValueError) as caught:
            _production_settings()

        assert re.search(r"smtp_(host|port|use_tls)", _error_text(caught.value).lower())

    @pytest.mark.parametrize(
        ("port", "use_tls"),
        [("587", None), ("587", "false"), ("465", "false"), ("25", "true"), ("2525", "TRUE")],
    )
    def test_production_accepts_encrypted_smtp_from_environment(
        self, monkeypatch: pytest.MonkeyPatch, port: str, use_tls: str | None
    ) -> None:
        monkeypatch.setenv("SMTP_HOST", SMTP_HOST)
        monkeypatch.setenv("SMTP_PORT", port)
        _set_optional(monkeypatch, "SMTP_USE_TLS", use_tls)

        assert _production_settings().smtp_port == int(port)


def _s3(endpoint: str, mode: str | None, flag: bool | None) -> dict:
    arguments = {"storage_backend": "s3", "storage_endpoint_url": endpoint, **S3_CREDENTIALS}
    if mode is not None:
        arguments["deployment_mode"] = mode
    if flag is not None:
        arguments[INSECURE_FLAG] = flag
    return arguments


class TestProductionObjectStoreEndpoint:
    @pytest.mark.parametrize("endpoint", NON_HTTPS_ENDPOINTS)
    @pytest.mark.parametrize(
        ("mode", "flag"),
        [
            ("cloud", None),
            ("cloud", False),
            ("cloud", True),
            (None, True),
            ("onprem", None),
            ("onprem", False),
        ],
    )
    def test_production_rejects_non_https_endpoint_without_the_onprem_opt_in(
        self, mode: str | None, flag: bool | None, endpoint: str
    ) -> None:
        with pytest.raises(ValueError) as caught:
            _production_settings(**_s3(endpoint, mode, flag))

        text = _error_text(caught.value)
        assert "storage_endpoint_url" in text.lower()
        assert _leaked_fields(text, FAKE_SECRETS) == []

    @pytest.mark.parametrize(
        "endpoint", ["http://minio:9000", *NON_HTTPS_ENDPOINTS], ids=lambda endpoint: endpoint
    )
    def test_production_onprem_opt_in_accepts_non_https_endpoint(self, endpoint: str) -> None:
        settings = _production_settings(**_s3(endpoint, "onprem", True))

        assert settings.storage_endpoint_url == endpoint
        assert settings.deployment_mode == "onprem"

    @pytest.mark.parametrize(
        "endpoint", ["", "https://s3.example.invalid", "https://minio.example.invalid:9443/"]
    )
    @pytest.mark.parametrize("mode", ["cloud", "onprem"])
    def test_production_accepts_https_or_provider_default_without_the_flag(
        self, mode: str, endpoint: str
    ) -> None:
        settings = _production_settings(**_s3(endpoint, mode, None))

        assert settings.storage_endpoint_url == endpoint

    @pytest.mark.parametrize("environment", ["development", "test"])
    @pytest.mark.parametrize("mode", ["cloud", "onprem"])
    def test_non_production_keeps_plain_http_endpoint(self, environment: str, mode: str) -> None:
        settings = Settings(
            _env_file=None, environment=environment, **_s3(PLAIN_ENDPOINT, mode, None)
        )

        assert settings.storage_endpoint_url == PLAIN_ENDPOINT

    def test_production_local_storage_ignores_the_endpoint(self) -> None:
        settings = _production_settings(
            storage_backend="local", storage_endpoint_url=PLAIN_ENDPOINT
        )

        assert settings.storage_backend == "local"

    def test_insecure_endpoint_flag_defaults_to_false(self) -> None:
        assert getattr(_test_settings(), INSECURE_FLAG) is False

    @pytest.mark.parametrize(
        ("mode", "flag"),
        [("cloud", "true"), (None, "true"), ("onprem", None), ("onprem", "false")],
    )
    def test_production_rejects_plain_http_endpoint_from_environment(
        self, monkeypatch: pytest.MonkeyPatch, mode: str | None, flag: str | None
    ) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("STORAGE_ENDPOINT_URL", PLAIN_ENDPOINT)
        _set_optional(monkeypatch, "DEPLOYMENT_MODE", mode)
        _set_optional(monkeypatch, INSECURE_FLAG.upper(), flag)

        with pytest.raises(ValueError) as caught:
            _production_settings(**S3_CREDENTIALS)

        assert "storage_endpoint_url" in _error_text(caught.value).lower()

    @pytest.mark.parametrize(
        ("variable", "flag"),
        [
            (INSECURE_FLAG.upper(), "true"),
            (INSECURE_FLAG, "true"),
            (INSECURE_FLAG.upper(), "TRUE"),
            (INSECURE_FLAG.upper(), "1"),
        ],
    )
    def test_production_onprem_opt_in_from_environment(
        self, monkeypatch: pytest.MonkeyPatch, variable: str, flag: str
    ) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("STORAGE_ENDPOINT_URL", "http://minio:9000")
        monkeypatch.setenv("DEPLOYMENT_MODE", "onprem")
        monkeypatch.setenv(variable, flag)

        settings = _production_settings(**S3_CREDENTIALS)

        assert getattr(settings, INSECURE_FLAG) is True
        assert settings.storage_endpoint_url == "http://minio:9000"

    def test_production_accepts_https_endpoint_from_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.setenv("STORAGE_ENDPOINT_URL", "https://s3.example.invalid")

        assert _production_settings(**S3_CREDENTIALS).storage_backend == "s3"


REQUIRED_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?\?[^}]*\}")


def _is_required_credential_reference(value: str) -> bool:
    if REQUIRED_REFERENCE.fullmatch(value):
        return True
    _, separator, password = value.partition("/")
    return bool(separator) and bool(REQUIRED_REFERENCE.fullmatch(password))


def _environment_entries(node) -> dict[str, str]:
    if isinstance(node, list):
        return dict(str(item).partition("=")[::2] for item in node)
    if isinstance(node, dict):
        return {str(key): "" if value is None else str(value) for key, value in node.items()}
    return {}


def _find_key(node, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key and isinstance(value, str):
                found.append(value)
            found.extend(_find_key(value, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_key(item, key))
    return found


class _FakeResult:
    def single(self):
        return {"count": 0}

    def data(self):
        return []

    def __iter__(self):
        return iter(())

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def run(self, *args, **kwargs):
        return _FakeResult()

    def __getattr__(self, name):
        return lambda *args, **kwargs: _FakeResult()


class _FakeDriver:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def session(self, *args, **kwargs):
        return _FakeSession()

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _DriverCalls(list):
    def __repr__(self) -> str:
        return f"<{len(self)} Neo4j driver call(s)>"


@pytest.fixture
def neo4j_connections(monkeypatch: pytest.MonkeyPatch) -> _DriverCalls:
    calls = _DriverCalls()

    def driver(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeDriver()

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", staticmethod(driver))
    monkeypatch.setattr(sys, "argv", [str(SEED_SCRIPT)])
    return calls


def _run_seed() -> BaseException | None:
    try:
        runpy.run_path(str(SEED_SCRIPT), run_name="__main__")
    except SystemExit as exc:
        return None if exc.code in (0, None) else exc
    except Exception as exc:
        return exc
    return None


def _connection(calls: list[tuple[tuple, dict]]) -> tuple[object, object, object]:
    args, kwargs = calls[0]
    uri = kwargs.get("uri", args[0] if args else None)
    auth = kwargs.get("auth", args[1] if len(args) > 1 else None)
    if isinstance(auth, (tuple, list)) and len(auth) >= 2:
        return uri, auth[0], auth[1]
    return uri, getattr(auth, "principal", None), getattr(auth, "credentials", None)


NEO4J_URI = "bolt://fake-neo4j-host.invalid:7687"
NEO4J_USER = "fake-neo4j-user"


class TestNoEmbeddedCredentials:
    def test_development_compose_takes_neo4j_auth_from_a_required_reference(self) -> None:
        document = yaml.safe_load(DEVELOPMENT_COMPOSE.read_text(encoding="utf-8"))
        environment = _environment_entries(document["services"]["neo4j"].get("environment"))

        has_auth = "NEO4J_AUTH" in environment
        assert has_auth, "the neo4j service no longer sets NEO4J_AUTH"
        is_reference = _is_required_credential_reference(environment["NEO4J_AUTH"])
        assert is_reference, "NEO4J_AUTH must take its password from a required ${VAR:?} reference"

    @pytest.mark.parametrize("path", INFRA_COMPOSE_FILES, ids=lambda path: path.name)
    def test_infra_compose_jwt_secret_is_recognised_as_a_required_reference(
        self, path: Path
    ) -> None:
        values = _find_key(yaml.safe_load(path.read_text(encoding="utf-8")), "JWT_SECRET")

        assert values
        assert all(_is_required_credential_reference(value) for value in values)

    def test_seed_connects_with_the_configured_neo4j_settings(
        self, monkeypatch: pytest.MonkeyPatch, neo4j_connections: list
    ) -> None:
        password = FAKE_SECRETS["neo4j_password"]
        monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("NEO4J_USER", NEO4J_USER)
        monkeypatch.setenv("NEO4J_PASSWORD", password)

        outcome = _run_seed()

        succeeded = outcome is None
        assert succeeded, f"seed failed: {type(outcome).__name__}"
        opened = bool(neo4j_connections)
        assert opened, "seed opened no Neo4j driver"
        uri, user, used_password = _connection(neo4j_connections)
        same_uri = uri == NEO4J_URI
        same_user = user == NEO4J_USER
        same_password = used_password == password
        assert same_uri, "seed did not use NEO4J_URI from settings"
        assert same_user, "seed did not use NEO4J_USER from settings"
        assert same_password, "seed did not use NEO4J_PASSWORD from settings"

    def test_seed_reads_the_password_through_a_secret_reference(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, neo4j_connections: list
    ) -> None:
        password = _fake_value("neo4j_password", "file")
        monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("NEO4J_USER", NEO4J_USER)
        _set_secret_file(monkeypatch, tmp_path, "neo4j_password", password + "\n")

        outcome = _run_seed()

        succeeded = outcome is None
        assert succeeded, f"seed failed: {type(outcome).__name__}"
        opened = bool(neo4j_connections)
        assert opened, "seed opened no Neo4j driver"
        _, _, used_password = _connection(neo4j_connections)
        same_password = used_password == password
        assert same_password, "seed did not use the NEO4J_PASSWORD_FILE value"

    @pytest.mark.parametrize("password", ["", None], ids=["empty", "absent"])
    def test_seed_refuses_to_run_without_a_password(
        self, monkeypatch: pytest.MonkeyPatch, neo4j_connections: list, password: str | None
    ) -> None:
        monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("NEO4J_USER", NEO4J_USER)
        if password is not None:
            monkeypatch.setenv("NEO4J_PASSWORD", password)

        outcome = _run_seed()

        refused = outcome is not None
        assert refused, "seed ran with an empty Neo4j password"
        opened = bool(neo4j_connections)
        assert not opened, "seed opened a Neo4j driver before refusing"


PLACEHOLDER_WORDS = (
    "your",
    "change",
    "placeholder",
    "example",
    "replace",
    "random",
    "generate",
    "set-",
    "set_",
    "xxx",
    "***",
    "...",
    "redacted",
    "required",
    "strong",
    "secret-ref",
    "see ",
)


def _documented_values(variable: str) -> list[str]:
    pattern = re.compile(rf"^\s*{variable}\s*=(.*)$")
    values: list[str] = []
    for line in README.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            values.append(match.group(1).split(" #", 1)[0].strip())
    return values


def _is_placeholder(value: str) -> bool:
    if not value or re.fullmatch(r"<[^>]*>|\[[^\]]*\]|\$?\{[^}]*\}", value):
        return True
    return any(word in value.lower() for word in PLACEHOLDER_WORDS)


class TestReadmeCredentialPlaceholders:
    def test_readme_documents_neo4j_password_as_a_placeholder(self) -> None:
        values = _documented_values("NEO4J_PASSWORD")

        documented = bool(values)
        assert documented, "README no longer documents NEO4J_PASSWORD"
        placeholders = all(_is_placeholder(value) for value in values)
        assert placeholders, "README documents a real-looking NEO4J_PASSWORD value"

    @pytest.mark.parametrize("variable", ["ZAI_API_KEY", "GAPGPT_API_KEY"])
    def test_readme_existing_placeholders_are_recognised(self, variable: str) -> None:
        values = _documented_values(variable)

        assert values
        assert all(_is_placeholder(value) for value in values)


class TestPlainValuesStillWork:
    @pytest.mark.parametrize("spelling", SPELLINGS)
    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_plain_environment_variable(
        self, monkeypatch: pytest.MonkeyPatch, field: str, spelling: str
    ) -> None:
        value = FAKE_SECRETS[field]
        monkeypatch.setenv(_variable(field, spelling), value)

        assert getattr(_test_settings(**_companions(field, value)), field) == value

    @pytest.mark.parametrize("spelling", ["upper", "lower"])
    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_dotenv_file(self, tmp_path: Path, field: str, spelling: str) -> None:
        value = FAKE_SECRETS[field]
        dotenv = _write(tmp_path, ".env", f"{_variable(field, spelling)}={value}\n")

        settings = Settings(_env_file=dotenv, environment="test", **_companions(field, value))

        assert getattr(settings, field) == value

    @pytest.mark.parametrize("field", SECRET_FIELDS)
    def test_unset_field_keeps_its_default(self, field: str) -> None:
        assert getattr(_test_settings(), field) == BASE_DEFAULTS.get(field, "")

    def test_plain_values_satisfy_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for field in ("database_url", "jwt_secret", "token_pepper"):
            monkeypatch.setenv(field.upper(), FAKE_SECRETS[field])

        settings = Settings(_env_file=None, **_environment_base("production"))

        assert settings.is_production
        assert settings.token_pepper == FAKE_SECRETS["token_pepper"]


def _section(text: str, title: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading and title in heading.group(2).lower():
            level = len(heading.group(1))
            body: list[str] = [line]
            for following in lines[index + 1 :]:
                nested = re.match(r"^(#{1,6})\s", following)
                if nested and len(nested.group(1)) <= level:
                    break
                body.append(following)
            return "\n".join(body)
    return None


class TestDocumentation:
    def test_deployment_doc_has_a_secret_references_section(self) -> None:
        section = _section(DEPLOYMENT_DOC.read_text(encoding="utf-8"), "secret references")

        assert section is not None
        lowered = section.lower()
        assert "_file" in lowered
        assert [field for field in SECRET_FIELDS if field not in lowered] == []
        newline_words = ("newline", "new line", "line break", "line ending", "crlf", "\\n")
        conflict_words = ("both", "conflict", "together", "mutually", "exclusive", "either")
        assert any(word in lowered for word in newline_words)
        assert any(word in lowered for word in conflict_words)
        assert "smtp" in lowered and "tls" in lowered
        assert "https" in lowered and "endpoint" in lowered

    def test_env_example_documents_file_references_in_one_comment_block(self) -> None:
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        mentions = [index for index, line in enumerate(lines) if "_file" in line.lower()]

        assert mentions
        assert all(lines[index].lstrip().startswith("#") for index in mentions)
        block = lines[mentions[0] : mentions[-1] + 1]
        assert all(not line.strip() or line.lstrip().startswith("#") for line in block)

    def test_env_example_assigns_no_file_reference(self) -> None:
        assignments = [
            line
            for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*_FILE\s*=", line, re.IGNORECASE)
        ]

        assert assignments == []
