"""Mandatory TLS on the internal hops in production (HOM-161).

The matrix is in ``docs/security/transport-policy.md``: cloud production needs TLS on every
hop; on-premise production needs it too, except where an explicit per-hop opt-out names a
server on the same host. Every value below is an obviously fake fixture. Each test runs with
no ``Settings`` variable and no AWS variable from the environment, an empty shared AWS
config, and without the repository's ``.env`` file.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.config import PROJECT_ROOT, Settings

MARK = "Qz7tls"
PASSWORD = f"fake-password-{MARK}"

TLS_DATABASE = (
    f"postgresql+psycopg://homayar:{PASSWORD}@db.example.invalid:5432/homayar?sslmode=verify-full"
)
TLS_REDIS = f"rediss://:{PASSWORD}@redis.example.invalid:6380/0?ssl_cert_reqs=required"
TLS_NEO4J = "neo4j+s://graph.example.invalid:7687"
TLS_STORAGE = "https://objects.example.invalid"
COMPOSE = PROJECT_ROOT.parent / "infra" / "compose"


def _pg(netloc: str, query: str = "") -> str:
    return f"postgresql+psycopg://homayar:{PASSWORD}@{netloc}/homayar{query}"


@dataclass(frozen=True)
class Hop:
    name: str
    field: str
    variable: str
    opt_out: str
    tls: tuple[str, ...]
    # Plaintext to a server on this host: accepted on-premise with the opt-out only.
    same_host: tuple[str, ...]
    # Plaintext to another (or no named) host: refused even with the opt-out.
    other_host: tuple[str, ...]
    # Neither TLS nor a recognised plaintext form: always refused.
    unrecognised: tuple[str, ...]

    @property
    def opt_out_variable(self) -> str:
        return self.opt_out.upper()


HOPS = (
    Hop(
        name="postgresql",
        field="database_url",
        variable="DATABASE_URL",
        opt_out="database_allow_insecure_transport",
        tls=(
            _pg("db.example.invalid", "?sslmode=require"),
            _pg("db.example.invalid:5432", "?sslmode=verify-ca"),
            _pg("db.example.invalid", "?application_name=api&sslmode=verify-full"),
            _pg("", "?host=db-a.example.invalid,db-b.example.invalid&sslmode=require"),
        ),
        same_host=(
            _pg("postgres:5432"),
            _pg("localhost"),
            _pg("127.0.0.1:5432"),
            _pg("[::1]:5432"),
            _pg("postgres", "?sslmode=disable"),
            _pg("postgres", "?sslmode=prefer"),
            _pg("postgres", "?sslmode=allow"),
            _pg("postgres", "?sslmode=REQUIRE"),
            _pg("postgres", "?sslmode=require&sslmode=disable"),
            _pg("postgres", "?sslmode="),
            _pg("", "?host=/var/run/postgresql"),
            _pg("", "?host=postgres:5432&host=localhost:5433"),
        ),
        other_host=(
            _pg("db.example.invalid"),
            _pg("10.0.0.5:5432"),
            _pg("postgres.internal"),
            _pg("167772165"),
            _pg("0x0a000005"),
            _pg("postgres.", "?sslmode=disable"),
            _pg("postgres", "?host=db.example.invalid"),
            _pg("", "?host=postgres,db.example.invalid"),
            _pg("", "?hostaddr=10.0.0.5"),
            _pg("postgres", "?hostaddr=10.0.0.5"),
            _pg("postgres", "?service=homayar"),
            _pg("db.example.invalid", "?sslmode=require#&sslmode=disable"),
            _pg(""),
        ),
        unrecognised=(
            _pg("[::1"),
            # A database name that is itself a connection string (libpq can expand one).
            "postgresql+psycopg://homayar@postgres/host=db.example.invalid sslmode=disable"
            "?sslmode=verify-full",
            "postgresql+psycopg://homayar@postgres/postgresql://db.example.invalid/homayar",
        ),
    ),
    Hop(
        name="redis",
        field="redis_url",
        variable="REDIS_URL",
        opt_out="redis_allow_insecure_transport",
        tls=(
            f"rediss://:{PASSWORD}@redis.example.invalid:6380/0",
            TLS_REDIS,
            f"REDISS://:{PASSWORD}@redis.example.invalid:6380/0?ssl_cert_reqs=none",
        ),
        same_host=(
            f"redis://:{PASSWORD}@redis:6379/0",
            "redis://localhost:6379/0",
            f"redis://:{PASSWORD}@127.0.0.1:6379/1",
            "unix:///run/redis/redis.sock?db=0",
            "redis://redis:6379/0?host=localhost",
        ),
        other_host=(
            f"redis://:{PASSWORD}@redis.example.invalid:6379/0",
            f"redis://:{PASSWORD}@192.168.1.10:6379/0",
            f"redis://:{PASSWORD}@2130706433:6379/0",
            "redis:///0",
            "redis://redis:6379/0?host=redis.example.invalid",
            "redis://redis:6379/0?socket_timeout=5&hostname=10.0.0.5",
        ),
        unrecognised=(
            "sentinel://redis:26379",
            "http://redis:6379",
            "unix://",
            f"redis://:{PASSWORD}@[::1:6379/0",
        ),
    ),
    Hop(
        name="neo4j",
        field="neo4j_uri",
        variable="NEO4J_URI",
        opt_out="neo4j_allow_insecure_transport",
        tls=(TLS_NEO4J, "bolt+s://graph.example.invalid:7687", "BOLT+S://graph.example.invalid"),
        same_host=("bolt://neo4j:7687", "neo4j://localhost:7687", "bolt://127.0.0.1:7687"),
        other_host=("bolt://graph.example.invalid:7687", "neo4j://10.1.2.3:7687", "bolt://:7687"),
        unrecognised=(
            "bolt+ssc://neo4j:7687",
            "neo4j+ssc://graph.example.invalid:7687",
            "http://neo4j:7474",
        ),
    ),
    Hop(
        name="object_storage",
        field="storage_endpoint_url",
        variable="STORAGE_ENDPOINT_URL",
        opt_out="storage_allow_insecure_endpoint",
        tls=(TLS_STORAGE, "HTTPS://s3.example.invalid:8443/base", ""),
        same_host=("http://minio:9000", "HTTP://minio:9000", "http://127.0.0.1:9000"),
        other_host=(
            "http://minio.example.invalid:9000",
            "http://10.0.0.5:9000",
            "http://:9000",
        ),
        unrecognised=("ftp://minio:9000", "minio:9000", " http://minio:9000"),
    ),
)
HOPS_BY_NAME = {hop.name: hop for hop in HOPS}


def _cases(kind: str) -> list[pytest.param]:
    return [
        pytest.param(hop, value, id=f"{hop.name}-{index}")
        for hop in HOPS
        for index, value in enumerate(getattr(hop, kind))
    ]


def _plaintext_cases() -> list[pytest.param]:
    return _cases("same_host") + _cases("other_host") + _cases("unrecognised")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No Settings variable, no AWS variable and an empty shared AWS config."""
    names = set(Settings.model_fields) | {f"{name}_file" for name in Settings.model_fields}
    for key in list(os.environ):
        if key.lower() in names or key.upper().startswith("AWS_"):
            monkeypatch.delenv(key)
    config = tmp_path / "aws" / "config"
    config.parent.mkdir()
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws" / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.fixture(autouse=True)
def audit_logger_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Earlier in-process Alembic runs (fileConfig) disable loggers that already exist."""
    monkeypatch.setattr(logging.getLogger("backend.config"), "disabled", False)


def production(**overrides: object) -> dict[str, object]:
    """A production configuration with TLS on all four hops (the Neo4j projection on)."""
    kwargs: dict[str, object] = {
        "environment": "production",
        "deployment_mode": "cloud",
        "database_url": TLS_DATABASE,
        "redis_url": TLS_REDIS,
        "jwt_secret": f"fake-jwt-secret-{MARK}-0123456789abcdef",
        "token_pepper": f"fake-token-pepper-{MARK}-0123456789abcdef",
        "secure_cookies": True,
        "enable_demo_account": False,
        "allow_mock_providers": False,
        "auto_create_schema": False,
        "storage_backend": "s3",
        "storage_endpoint_url": TLS_STORAGE,
        "storage_access_key": f"fake-storage-access-key-{MARK}",
        "storage_secret_key": f"fake-storage-secret-key-{MARK}",
        "enable_neo4j_projection": True,
        "neo4j_uri": TLS_NEO4J,
        "neo4j_password": PASSWORD,
        "_env_file": None,
    }
    kwargs.update(overrides)
    return kwargs


def _hop(hop: Hop, value: str, *, mode: str, opt_out: bool) -> dict[str, object]:
    return production(deployment_mode=mode, **{hop.field: value, hop.opt_out: opt_out})


def _texts(error: BaseException) -> list[str]:
    texts: list[str] = []
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None:
            continue
        texts += [str(current), repr(current), repr(current.args)]
        if isinstance(current, ValidationError):
            texts += [current.json(), repr(current.errors())]
        pending += [current.__cause__, current.__context__]
    return texts


def _refused(kwargs: dict[str, object], value: str) -> str:
    """Construct, expect a refusal, prove it carries neither the value nor its password."""
    with pytest.raises(ValidationError) as caught:
        Settings(**kwargs)
    for text in _texts(caught.value):
        assert PASSWORD not in text
        if len(value.strip()) > 12:
            assert value not in text
    errors = caught.value.errors()
    assert len(errors) == 1
    return errors[0]["msg"]


def _audit_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "backend.config" and "TRANSPORT_TLS_OPT_OUT" in record.getMessage()
    ]


# --- TLS is accepted everywhere ------------------------------------------------------------


@pytest.mark.parametrize("mode", ["cloud", "onprem"])
@pytest.mark.parametrize(("hop", "value"), _cases("tls"))
def test_tls_is_accepted_in_both_modes_and_logs_nothing(
    hop: Hop, value: str, mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="backend.config")

    settings = Settings(**_hop(hop, value, mode=mode, opt_out=False))

    assert getattr(settings, hop.field) == value
    assert _audit_lines(caplog) == []


def test_the_baseline_production_configuration_is_valid() -> None:
    settings = Settings(**production())
    assert settings.is_production and settings.deployment_mode == "cloud"


# --- cloud: no opt-out ---------------------------------------------------------------------


@pytest.mark.parametrize("opt_out", [False, True])
@pytest.mark.parametrize(("hop", "value"), _plaintext_cases())
def test_cloud_production_refuses_plaintext_even_with_the_opt_out(
    hop: Hop, value: str, opt_out: bool
) -> None:
    message = _refused(_hop(hop, value, mode="cloud", opt_out=opt_out), value)

    assert hop.variable in message
    assert "DEPLOYMENT_MODE=cloud" in message
    assert hop.opt_out_variable in message


# --- on-premise: TLS unless an explicit opt-out names a same-host server -------------------


@pytest.mark.parametrize(("hop", "value"), _plaintext_cases())
def test_onprem_production_refuses_plaintext_without_the_opt_out(hop: Hop, value: str) -> None:
    message = _refused(_hop(hop, value, mode="onprem", opt_out=False), value)

    assert hop.variable in message
    assert f"{hop.opt_out_variable}=true" in message


@pytest.mark.parametrize(("hop", "value"), _cases("same_host"))
def test_onprem_opt_out_accepts_a_same_host_server_and_logs_the_hop(
    hop: Hop, value: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="backend.config")

    settings = Settings(**_hop(hop, value, mode="onprem", opt_out=True))

    assert getattr(settings, hop.field) == value
    [record] = _audit_lines(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        f"TRANSPORT_TLS_OPT_OUT deployment_mode=onprem hops={hop.name}({hop.opt_out_variable})"
    )


@pytest.mark.parametrize(("hop", "value"), _cases("other_host"))
def test_onprem_opt_out_refuses_another_or_no_host(hop: Hop, value: str) -> None:
    message = _refused(_hop(hop, value, mode="onprem", opt_out=True), value)

    assert hop.opt_out_variable in message
    assert "same host" in message
    assert hop.variable in message


@pytest.mark.parametrize(("hop", "value"), _cases("unrecognised"))
def test_onprem_opt_out_never_covers_an_unrecognised_transport(hop: Hop, value: str) -> None:
    message = _refused(_hop(hop, value, mode="onprem", opt_out=True), value)

    assert hop.variable in message


def test_audit_line_names_every_hop_in_use_once_without_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    kwargs = production(deployment_mode="onprem")
    for hop in HOPS:
        kwargs[hop.field] = hop.same_host[0]
        kwargs[hop.opt_out] = True

    Settings(**kwargs)

    [record] = _audit_lines(caplog)
    assert record.getMessage() == (
        "TRANSPORT_TLS_OPT_OUT deployment_mode=onprem hops="
        "object_storage(STORAGE_ALLOW_INSECURE_ENDPOINT),"
        "postgresql(DATABASE_ALLOW_INSECURE_TRANSPORT),"
        "redis(REDIS_ALLOW_INSECURE_TRANSPORT),"
        "neo4j(NEO4J_ALLOW_INSECURE_TRANSPORT)"
    )
    assert PASSWORD not in caplog.text
    for hop in HOPS:
        assert hop.same_host[0] not in caplog.text


def test_an_opt_out_with_tls_in_place_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="backend.config")
    kwargs = production(deployment_mode="onprem", **{hop.opt_out: True for hop in HOPS})

    Settings(**kwargs)

    assert _audit_lines(caplog) == []


# --- scope ---------------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
def test_rules_apply_to_production_only(environment: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="backend.config")
    kwargs = production(environment=environment)
    for hop in HOPS:
        kwargs[hop.field] = hop.other_host[0]

    settings = Settings(**kwargs)

    assert settings.environment == environment
    assert _audit_lines(caplog) == []


@pytest.mark.parametrize(
    ("query", "tls"),
    [
        # urlsplit would read sslmode=require and drop the rest as a fragment; the engine
        # passes ("require#", "disable") to the driver.
        ("?sslmode=require#&sslmode=disable", False),
        # urlsplit would see no query at all; the engine connects with sslmode=require.
        ("#x?sslmode=require", True),
    ],
)
def test_postgresql_rule_reads_the_url_as_the_engine_does(query: str, tls: bool) -> None:
    from sqlalchemy.engine import make_url

    url = _pg("db.example.invalid", query)
    modes = make_url(url).query["sslmode"]
    assert (modes == "require") is tls

    if tls:
        assert Settings(**production(database_url=url)).database_url == url
    else:
        assert "DATABASE_URL" in _refused(production(database_url=url), url)


def test_neo4j_is_checked_only_while_the_projection_is_enabled() -> None:
    plaintext = "bolt://graph.example.invalid:7687"
    settings = Settings(**production(enable_neo4j_projection=False, neo4j_uri=plaintext))
    assert settings.neo4j_uri == plaintext

    message = _refused(production(enable_neo4j_projection=True, neo4j_uri=plaintext), plaintext)
    assert "NEO4J_URI" in message


def test_local_storage_has_no_object_storage_hop() -> None:
    settings = Settings(
        **production(storage_backend="local", storage_endpoint_url="http://minio.example.invalid")
    )
    assert settings.storage_backend == "local"


def test_the_default_redis_url_is_refused_in_production() -> None:
    kwargs = production()
    del kwargs["redis_url"]

    message = _refused(kwargs, "")

    assert "REDIS_URL must use rediss://" in message


@pytest.mark.parametrize("hop", HOPS, ids=[hop.name for hop in HOPS])
def test_opt_outs_default_off_and_read_the_environment(
    hop: Hop, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert getattr(Settings(_env_file=None), hop.opt_out) is False

    monkeypatch.setenv(hop.opt_out_variable, "true")
    monkeypatch.setenv("DEPLOYMENT_MODE", "onprem")
    kwargs = production(**{hop.field: hop.same_host[0]})
    del kwargs["deployment_mode"]

    settings = Settings(**kwargs)

    assert getattr(settings, hop.opt_out) is True
    assert settings.deployment_mode == "onprem"


# --- the shipped Compose files -------------------------------------------------------------


def _compose_values(name: str, key: str, indent: int) -> list[str]:
    compose_file = COMPOSE / name
    if not compose_file.exists():
        pytest.skip("infra/compose is not part of this checkout")
    text = compose_file.read_text(encoding="utf-8")
    pattern = rf'^{" " * indent}{key}:\s*"?([^"\n]*?)"?\s*$'
    return re.findall(pattern, text, flags=re.MULTILINE)


def _onprem_value(key: str) -> str:
    values = _compose_values("compose.onprem.yaml", key, 2)
    assert len(values) == 1, key
    return values[0]


def test_onprem_compose_opts_out_per_hop_for_its_bundled_services(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="backend.config")
    flags = {
        "database_allow_insecure_transport": _onprem_value("DATABASE_ALLOW_INSECURE_TRANSPORT"),
        "redis_allow_insecure_transport": _onprem_value("REDIS_ALLOW_INSECURE_TRANSPORT"),
        "storage_allow_insecure_endpoint": _onprem_value("STORAGE_ALLOW_INSECURE_ENDPOINT"),
    }
    assert set(flags.values()) == {"true"}
    assert _compose_values("compose.onprem.yaml", "NEO4J_ALLOW_INSECURE_TRANSPORT", 2) == []

    # DATABASE_URL and REDIS_URL are operator input that must name the bundled services.
    settings = Settings(
        **production(
            deployment_mode=_onprem_value("DEPLOYMENT_MODE"),
            database_url=_pg("postgres:5432"),
            redis_url=f"redis://:{PASSWORD}@redis:6379/0",
            storage_endpoint_url=_onprem_value("STORAGE_ENDPOINT_URL"),
            enable_neo4j_projection=False,
            **flags,
        )
    )

    assert settings.deployment_mode == "onprem"
    [record] = _audit_lines(caplog)
    assert "hops=object_storage(" in record.getMessage()
    assert "postgresql(" in record.getMessage() and "redis(" in record.getMessage()


def test_neo4j_overlay_runs_on_premise_and_is_refused_in_cloud() -> None:
    uris = _compose_values("compose.neo4j.yaml", "NEO4J_URI", 6)
    flags = _compose_values("compose.neo4j.yaml", "NEO4J_ALLOW_INSECURE_TRANSPORT", 6)
    assert len(uris) == 2 and len(set(uris)) == 1  # api and worker
    assert flags == ["true", "true"]
    overlay = {"neo4j_uri": uris[0], "neo4j_allow_insecure_transport": flags[0]}

    onprem = Settings(**production(deployment_mode="onprem", **overlay))
    assert onprem.neo4j_allow_insecure_transport is True

    message = _refused(production(deployment_mode="cloud", **overlay), uris[0])
    assert "NEO4J_URI" in message and "DEPLOYMENT_MODE=cloud" in message
