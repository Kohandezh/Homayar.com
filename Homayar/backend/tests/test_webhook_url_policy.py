"""HOM-89: the webhook endpoint URL policy (DESIGN section 7).

The same check runs at create, at update and again before every send, so a
URL that is refused here never receives a request.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.webhook_service import (
    WEBHOOK_URL_MAX_LENGTH,
    url_refusal_for,
    webhook_url_refusal,
)

ALLOWED_EVERYWHERE = [
    "https://hooks.example.com/x",
    "https://hooks.example.com/x?token=abc",
    "https://HOOKS.Example.com:8443/path/to?x=1#frag",
    "https://10.0.0.5/x",
    "https://172.16.0.9/x",
    "https://[2001:db8::1]/x",
    # An international name is sent in its IDNA form, which passes every check.
    "https://bücher.example/x",
    "https://xn--bcher-kva.example/x",
]

ALLOWED_OUTSIDE_PRODUCTION = [
    "http://10.0.0.5:8080/hook",
    "http://192.168.1.20/hook",
    "http://hooks.example.com/x",
    "https://localhost/x",
    "https://127.0.0.1/x",
    "https://[::1]/x",
    "http://crm/hook",
]

REFUSED_EVERYWHERE = [
    "ftp://hooks.example.com/x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "hooks.example.com/x",
    "https:///x",
    "http://./x",
    "https://user:pw@hooks.example.com/x",
    "https://user@hooks.example.com/x",
    "https://:pw@hooks.example.com/x",
    "http://169.254.169.254/latest",
    "http://169.254.170.2/v2/credentials",
    "http://100.100.100.200/latest",
    "http://[fd00:ec2::254]/latest",
    "http://metadata.google.internal/",
    "http://METADATA.google.internal./",
    "http://METADATA.GOOGLE.INTERNAL/",
    "http://metadata/",
    "http://instance-data/latest",
    "http://metadata.azure.com/",
    "http://[fe80::1]/",
    "http://169.254.1.1/",
    "http://0.0.0.0/",
    "http://[::]/",
    "http://224.0.0.1/",
    "http://[ff02::1]/",
    "http://[::ffff:169.254.169.254]/",
    # NAT64 and IPv4-compatible IPv6 forms of 169.254.169.254.
    "http://[64:ff9b::a9fe:a9fe]/",
    "http://[::a9fe:a9fe]/",
    "http://[::169.254.169.254]/",
    # Full-width digits: httpx refuses this host, so no delivery could reach it.
    "http://１６９.２５４.１６９.２５４/",
    "http://[fe80::1%25eth0]/",
    # Numeric host spellings some resolvers read as an IPv4 address.
    "http://2852039166/",
    "http://0xA9FEA9FE/",
    "http://169.254.43518/",
    "http://0251.0376.0251.0376/",
    "http://169.254.169.0254/",
    "https://hooks.example.com:99999/x",
    "https://[fe80::1/x",
    "https://hooks.example.com/x y",
    "https://hooks.example.com/x\n",
]

REFUSED_IN_PRODUCTION_ONLY = [
    "http://hooks.example.com/x",
    "http://10.0.0.5:8080/hook",
    "https://localhost/x",
    "https://LOCALHOST./x",
    "https://LocalHost/x",
    "https://api.localhost/x",
    "https://127.0.0.1/x",
    "https://127.8.9.10/x",
    "https://[::1]/x",
    "https://[::ffff:127.0.0.1]/x",
    "https://[64:ff9b::7f00:1]/x",
    "https://[::127.0.0.1]/x",
]


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("url", ALLOWED_EVERYWHERE)
def test_allowed_everywhere(url: str, production: bool) -> None:
    assert webhook_url_refusal(url, production=production) is None


@pytest.mark.parametrize("url", ALLOWED_OUTSIDE_PRODUCTION)
def test_allowed_outside_production(url: str) -> None:
    assert webhook_url_refusal(url, production=False) is None


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("url", REFUSED_EVERYWHERE)
def test_refused_everywhere(url: str, production: bool) -> None:
    reason = webhook_url_refusal(url, production=production)

    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("url", REFUSED_IN_PRODUCTION_ONLY)
def test_refused_in_production(url: str) -> None:
    reason = webhook_url_refusal(url, production=True)

    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("url", ["https://[::]/x", "https://[::1]/x"])
def test_the_unspecified_and_loopback_ipv6_addresses_are_not_read_as_ipv4(url: str) -> None:
    # ``::`` is refused as unspecified; ``::1`` stays a loopback (allowed outside production).
    expected = url.startswith("https://[::]")
    assert (webhook_url_refusal(url, production=False) is not None) is expected


# --- the production cloud rule (SPEC A5.4) ---------------------------------------------

INTERNAL_NAMES = [
    "https://minio:9000/x",
    "https://api/x",
    "https://POSTGRES./x",
    "https://crm.internal/x",
    "https://printer.local/x",
    "https://a.localhost/x",
]


@pytest.mark.parametrize("url", INTERNAL_NAMES)
def test_a_production_cloud_refuses_internal_host_names(url: str) -> None:
    assert webhook_url_refusal(url, production=True, cloud=True)


@pytest.mark.parametrize("url", INTERNAL_NAMES[:5])
def test_an_onprem_production_keeps_internal_host_names(url: str) -> None:
    assert webhook_url_refusal(url, production=True, cloud=False) is None


@pytest.mark.parametrize(
    "url", ["https://[2001:db8::1]/x", "https://10.0.0.5/x", "https://hooks.example.com/x"]
)
def test_the_cloud_rule_judges_names_only(url: str) -> None:
    assert webhook_url_refusal(url, production=True, cloud=True) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:9000/x",
        "http://127.0.0.1:9000/x",
        "https://minio:9000/x",
        "https://crm.internal/x",
    ],
)
def test_outside_production_the_cloud_rule_is_off(url: str) -> None:
    assert webhook_url_refusal(url, production=False, cloud=True) is None


@pytest.mark.parametrize(
    ("production", "mode", "refused"),
    [(True, "cloud", True), (True, "onprem", False), (False, "cloud", False)],
)
def test_the_deployment_settings_choose_the_rule(
    production: bool, mode: str, refused: bool
) -> None:
    settings = SimpleNamespace(is_production=production, deployment_mode=mode)

    assert (url_refusal_for("https://minio:9000/x", settings) is not None) is refused


def test_length_limit_is_1000_characters() -> None:
    base = "https://hooks.example.com/"
    exact = base + "a" * (1000 - len(base))

    assert WEBHOOK_URL_MAX_LENGTH == 1000
    assert len(exact) == 1000
    assert webhook_url_refusal(exact, production=True) is None
    assert webhook_url_refusal(exact + "a", production=True)


def test_reason_does_not_repeat_the_url() -> None:
    url = "https://user:s3cret-token@hooks.example.com/x?token=abc"

    reason = webhook_url_refusal(url, production=False)

    assert reason
    assert "s3cret-token" not in reason
    assert "token=abc" not in reason
