"""Homayar alone chooses the S3 endpoint; ambient AWS endpoint overrides (HOM-159, WP02-F1).

botocore takes an endpoint from ``AWS_ENDPOINT_URL_S3``, ``AWS_ENDPOINT_URL`` and the active
profile of the shared AWS config (``endpoint_url`` and its ``services`` section) whenever a
client is built without one. The production object-store rule judged only
``STORAGE_ENDPOINT_URL``, so an ambient ``http://`` override sent object traffic in plaintext.

Contract:

* the S3 client never takes an ambient endpoint: requests go to ``STORAGE_ENDPOINT_URL`` or,
  when it is empty, to the provider default, whatever the environment and shared config say;
* production with ``STORAGE_BACKEND=s3`` refuses to start (settings validation) while any
  ambient override is set to something other than an ``https://`` URL with a host; the error
  names the variable and never carries a value, a file path or a credential;
* local storage and the development, test and staging environments are never refused.

Every value is an obviously fake fixture. No request leaves the process: the botocore
transport is replaced and only records the URL it was asked to reach.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from backend.config import PROJECT_ROOT, Settings
from backend.storage import S3ObjectStorage, create_object_storage

MARK = "qz7fake"
AMBIENT_HOST = f"ambient-{MARK}.example.invalid"
CONFIGURED_HOST = "configured.example.invalid"
TENANT = "tenant-hom159"
OBJECT_KEY = f"tenants/{TENANT}/objects/{'a' * 32}"

INSECURE_VALUES: dict[str, str] = {
    "http": f"http://{AMBIENT_HOST}:9000",
    "http_upper_with_userinfo": f"HTTP://akid-{MARK}:sk-{MARK}@{AMBIENT_HOST}",
    "scheme_less": f"{AMBIENT_HOST}:9000",
}
SECURE_VALUES: dict[str, str] = {
    "https": f"https://{AMBIENT_HOST}",
    "https_upper_port_path": f"HTTPS://{AMBIENT_HOST}:8443/base",
}

FAKE_CREDENTIALS = {
    "storage_access_key": f"fake-storage-access-key-{MARK}",
    "storage_secret_key": f"fake-storage-secret-key-{MARK}",
}
S3 = {"storage_backend": "s3", **FAKE_CREDENTIALS}


# --- environment isolation ---------------------------------------------------------------


def aws_config_path(tmp_path: Path) -> Path:
    return tmp_path / "aws" / "config"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No Settings variable, no ambient AWS variable and no real shared AWS config file."""
    names = set(Settings.model_fields) | {f"{name}_file" for name in Settings.model_fields}
    for key in list(os.environ):
        if key.lower() in names or key.upper().startswith("AWS_"):
            monkeypatch.delenv(key)
    config = aws_config_path(tmp_path)
    config.parent.mkdir()
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws" / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    # boto3 caches a process-wide default session (and with it the shared config it read).
    import boto3

    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)


def production_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "environment": "production",
        "database_url": (
            f"postgresql+psycopg://homayar:fake-db-{MARK}@db.example.invalid/homayar"
            "?sslmode=verify-full"
        ),
        "redis_url": f"rediss://:fake-redis-{MARK}@redis.example.invalid:6380/0",
        "jwt_secret": f"fake-jwt-secret-{MARK}-0123456789abcdef",
        "token_pepper": f"fake-token-pepper-{MARK}-0123456789abcdef",
        "secure_cookies": True,
        "enable_demo_account": False,
        "allow_mock_providers": False,
        "auto_create_schema": False,
        "_env_file": None,
    }
    kwargs.update(overrides)
    return kwargs


# --- ambient endpoint sources ------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One place botocore reads an S3 endpoint from, and what a refusal must name."""

    id: str
    apply: Callable[[pytest.MonkeyPatch, Path, str], None]
    names_it: Callable[[str], bool]


def _env(variable: str) -> Callable[[pytest.MonkeyPatch, Path, str], None]:
    def apply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
        monkeypatch.setenv(variable, value)

    return apply


def _shared_config(
    template: str, profile: str | None = None
) -> Callable[[pytest.MonkeyPatch, Path, str], None]:
    def apply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
        aws_config_path(tmp_path).write_text(template.replace("@VALUE@", value), "utf-8")
        if profile is not None:
            monkeypatch.setenv("AWS_PROFILE", profile)

    return apply


def _names_config_endpoint(message: str) -> bool:
    return "endpoint_url" in message and "config" in message.lower()


SOURCES: list[Source] = [
    Source(
        "AWS_ENDPOINT_URL",
        _env("AWS_ENDPOINT_URL"),
        lambda message: "AWS_ENDPOINT_URL" in message and "AWS_ENDPOINT_URL_S3" not in message,
    ),
    Source(
        "AWS_ENDPOINT_URL_S3",
        _env("AWS_ENDPOINT_URL_S3"),
        lambda message: "AWS_ENDPOINT_URL_S3" in message,
    ),
    Source(
        "config_default_profile",
        _shared_config(
            "[default]\n"
            "region = us-east-1\n"
            f"aws_access_key_id = cfg-akid-{MARK}\n"
            f"aws_secret_access_key = cfg-sk-{MARK}\n"
            "endpoint_url = @VALUE@\n"
        ),
        _names_config_endpoint,
    ),
    Source(
        "config_named_profile",
        _shared_config(
            "[default]\nregion = us-east-1\n\n[profile homayar-ops]\nendpoint_url = @VALUE@\n",
            profile="homayar-ops",
        ),
        _names_config_endpoint,
    ),
    Source(
        "config_services_section",
        _shared_config(
            "[default]\n"
            "services = homayar-s3\n\n"
            "[services homayar-s3]\n"
            "s3 =\n"
            "  endpoint_url = @VALUE@\n"
        ),
        _names_config_endpoint,
    ),
]
SOURCE_IDS = [source.id for source in SOURCES]


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


def assert_refused(
    caught: pytest.ExceptionInfo[ValidationError],
    source: Source,
    value: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = str(caught.value)
    assert source.names_it(message), f"the refusal does not name {source.id}: {message}"
    for text in exception_texts(caught.value):
        lowered = text.lower()
        assert value.lower() not in lowered, "the ambient endpoint value leaked"
        assert MARK not in lowered, "an endpoint, host or credential value leaked"
        assert str(tmp_path) not in text, "a file path leaked"
    for record in caplog.records:
        if record.name.startswith("backend"):
            assert MARK not in record.getMessage().lower(), "a value was logged"


def set_fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", f"env-akid-{MARK}")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", f"env-sk-{MARK}")
    monkeypatch.setenv("AWS_SESSION_TOKEN", f"env-session-{MARK}")


# --- production refusal: deny ------------------------------------------------------------


@pytest.mark.parametrize("value_id", sorted(INSECURE_VALUES))
@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_production_refuses_an_insecure_ambient_endpoint(
    source: Source,
    value_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    value = INSECURE_VALUES[value_id]
    set_fake_aws_credentials(monkeypatch)
    source.apply(monkeypatch, tmp_path, value)

    with pytest.raises(ValidationError) as caught:
        Settings(**production_kwargs(**S3))

    assert_refused(caught, source, value, tmp_path, caplog)


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_onprem_insecure_flag_does_not_cover_ambient_endpoints(
    source: Source,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """STORAGE_ALLOW_INSECURE_ENDPOINT keeps applying to STORAGE_ENDPOINT_URL only."""
    value = INSECURE_VALUES["http"]
    source.apply(monkeypatch, tmp_path, value)

    with pytest.raises(ValidationError) as caught:
        Settings(
            **production_kwargs(
                **S3,
                deployment_mode="onprem",
                storage_endpoint_url="http://minio:9000",
                storage_allow_insecure_endpoint=True,
            )
        )

    assert_refused(caught, source, value, tmp_path, caplog)


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_production_refuses_an_insecure_ambient_endpoint_beside_an_explicit_one(
    source: Source,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    value = INSECURE_VALUES["http"]
    source.apply(monkeypatch, tmp_path, value)

    with pytest.raises(ValidationError) as caught:
        Settings(**production_kwargs(**S3, storage_endpoint_url=f"https://{CONFIGURED_HOST}"))

    assert_refused(caught, source, value, tmp_path, caplog)


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
@pytest.mark.parametrize("where", ["environment", "shared_config"])
def test_ambient_ignore_flag_does_not_waive_the_refusal(
    source: Source,
    where: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    value = INSECURE_VALUES["http"]
    source.apply(monkeypatch, tmp_path, value)
    if where == "environment":
        monkeypatch.setenv("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "true")
    else:
        config = aws_config_path(tmp_path)
        text = config.read_text(encoding="utf-8") or "[default]\n"
        config.write_text(
            text.replace("[default]\n", "[default]\nignore_configured_endpoint_urls = true\n", 1),
            encoding="utf-8",
        )

    with pytest.raises(ValidationError) as caught:
        Settings(**production_kwargs(**S3))

    assert_refused(caught, source, value, tmp_path, caplog)


def test_every_present_environment_override_is_judged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A secure service-specific variable does not hide an insecure global one."""
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", SECURE_VALUES["https"])
    monkeypatch.setenv("AWS_ENDPOINT_URL", INSECURE_VALUES["http"])

    with pytest.raises(ValidationError) as caught:
        Settings(**production_kwargs(**S3))

    assert_refused(caught, SOURCES[0], INSECURE_VALUES["http"], tmp_path, caplog)


def test_every_present_shared_config_override_is_judged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A secure profile endpoint_url does not hide an insecure services-section one."""
    value = INSECURE_VALUES["http"]
    aws_config_path(tmp_path).write_text(
        "[default]\n"
        f"endpoint_url = {SECURE_VALUES['https']}\n"
        "services = homayar-s3\n\n"
        "[services homayar-s3]\n"
        "s3 =\n"
        f"  endpoint_url = {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as caught:
        Settings(**production_kwargs(**S3))

    assert_refused(caught, SOURCES[4], value, tmp_path, caplog)


# --- production refusal: allow -----------------------------------------------------------


@pytest.mark.parametrize("value_id", sorted(SECURE_VALUES))
@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
@pytest.mark.parametrize("deployment_mode", ["cloud", "onprem"])
def test_production_accepts_an_https_ambient_endpoint(
    source: Source,
    value_id: str,
    deployment_mode: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source.apply(monkeypatch, tmp_path, SECURE_VALUES[value_id])

    settings = Settings(**production_kwargs(**S3, deployment_mode=deployment_mode))

    assert settings.storage_backend == "s3"
    assert settings.storage_endpoint_url == ""


@pytest.mark.parametrize("variable", [None, "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"])
def test_production_accepts_no_ambient_endpoint(
    variable: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing set, or a variable set to the empty string (botocore treats it as unset)."""
    aws_config_path(tmp_path).write_text("[default]\nregion = us-east-1\n", encoding="utf-8")
    if variable is not None:
        monkeypatch.setenv(variable, "")

    settings = Settings(**production_kwargs(**S3))

    assert settings.storage_backend == "s3"


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_production_local_storage_ignores_ambient_endpoints(
    source: Source, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source.apply(monkeypatch, tmp_path, INSECURE_VALUES["http"])

    settings = Settings(
        **production_kwargs(storage_backend="local", storage_local_root=str(tmp_path / "objects"))
    )

    assert settings.storage_backend == "local"


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
@pytest.mark.parametrize("environment", ["development", "test", "staging"])
def test_ambient_endpoint_rule_is_production_only(
    source: Source, environment: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source.apply(monkeypatch, tmp_path, INSECURE_VALUES["http"])

    settings = Settings(_env_file=None, environment=environment, **S3)

    assert settings.environment == environment


def test_onprem_insecure_flag_still_covers_storage_endpoint_url() -> None:
    settings = Settings(
        **production_kwargs(
            **S3,
            deployment_mode="onprem",
            storage_endpoint_url="http://minio:9000",
            storage_allow_insecure_endpoint=True,
        )
    )

    assert settings.storage_endpoint_url == "http://minio:9000"


# --- the client never takes an ambient endpoint ------------------------------------------


class _TransportDisabled(Exception):
    """Raised by the replaced botocore transport: nothing leaves the process."""


@pytest.fixture
def sent_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import botocore.endpoint

    urls: list[str] = []

    def _send(self: object, request: object) -> object:
        urls.append(str(request.url))  # type: ignore[attr-defined]
        raise _TransportDisabled("transport disabled in this test")

    monkeypatch.setattr(botocore.endpoint.Endpoint, "_send", _send)
    return urls


def reached_hosts(storage: object, sent_urls: list[str]) -> list[tuple[str, str]]:
    """Drive one object request and return the (scheme, host) pairs it tried to reach."""
    with suppress(Exception):
        storage.exists(TENANT, OBJECT_KEY)  # type: ignore[attr-defined]
    assert sent_urls, "no request reached the transport"
    return [
        (urlsplit(url).scheme.lower(), (urlsplit(url).hostname or "").lower()) for url in sent_urls
    ]


def assert_provider_default(hosts: list[tuple[str, str]]) -> None:
    for scheme, host in hosts:
        assert AMBIENT_HOST not in host, "the client took the ambient endpoint"
        assert scheme == "https"
        assert host.endswith(".amazonaws.com"), host


def development_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, environment="development", **{**S3, **overrides})


@pytest.mark.parametrize("value_id", ["http", "scheme_less"])
@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_s3_client_ignores_an_ambient_endpoint(
    source: Source,
    value_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sent_urls: list[str],
) -> None:
    source.apply(monkeypatch, tmp_path, INSECURE_VALUES[value_id])

    storage = create_object_storage(development_settings())

    assert_provider_default(reached_hosts(storage, sent_urls))


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_production_s3_client_ignores_an_https_ambient_endpoint(
    source: Source, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent_urls: list[str]
) -> None:
    source.apply(monkeypatch, tmp_path, SECURE_VALUES["https"])

    storage = create_object_storage(Settings(**production_kwargs(**S3)))

    assert_provider_default(reached_hosts(storage, sent_urls))


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_s3_client_uses_storage_endpoint_url_over_an_ambient_one(
    source: Source, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent_urls: list[str]
) -> None:
    source.apply(monkeypatch, tmp_path, INSECURE_VALUES["http"])

    storage = create_object_storage(
        development_settings(storage_endpoint_url=f"https://{CONFIGURED_HOST}")
    )

    assert set(reached_hosts(storage, sent_urls)) == {("https", CONFIGURED_HOST)}


def test_ambient_ignore_flag_cannot_reenable_ambient_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent_urls: list[str]
) -> None:
    aws_config_path(tmp_path).write_text(
        "[default]\n"
        "ignore_configured_endpoint_urls = false\n"
        f"endpoint_url = {INSECURE_VALUES['http']}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "false")
    monkeypatch.setenv("AWS_ENDPOINT_URL", INSECURE_VALUES["http"])
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", INSECURE_VALUES["http"])

    storage = create_object_storage(development_settings())

    assert_provider_default(reached_hosts(storage, sent_urls))


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_directly_constructed_s3_storage_ignores_an_ambient_endpoint(
    source: Source, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent_urls: list[str]
) -> None:
    source.apply(monkeypatch, tmp_path, INSECURE_VALUES["http"])

    storage = S3ObjectStorage(
        "homayar-private",
        max_size_bytes=1024,
        region_name="us-east-1",
        access_key_id=FAKE_CREDENTIALS["storage_access_key"],
        secret_access_key=FAKE_CREDENTIALS["storage_secret_key"],
    )

    assert_provider_default(reached_hosts(storage, sent_urls))


@pytest.mark.parametrize("source", SOURCES, ids=SOURCE_IDS)
def test_local_backend_is_unaffected_by_ambient_endpoints(
    source: Source, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent_urls: list[str]
) -> None:
    source.apply(monkeypatch, tmp_path, INSECURE_VALUES["http"])
    settings = Settings(
        **production_kwargs(storage_backend="local", storage_local_root=str(tmp_path / "objects"))
    )

    storage = create_object_storage(settings)
    stored = storage.put_stream(TENANT, BytesIO(b"fake-object"), content_type="audio/mpeg")

    assert storage.exists(TENANT, stored.key) is True
    assert sent_urls == []


# --- operator documentation --------------------------------------------------------------


def test_deployment_guide_documents_the_ambient_endpoint_rule() -> None:
    guide = PROJECT_ROOT.parent / "docs" / "operations" / "deployment.md"
    if not guide.exists():
        pytest.skip("docs/operations is not part of this checkout")
    text = guide.read_text(encoding="utf-8")

    assert "AWS_ENDPOINT_URL" in text
