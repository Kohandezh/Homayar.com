"""HOM-357 part E: the connect-time address guard of the delivery worker.

Every test pins what the worker resolves and connects to by patching the
``socket`` module attributes the guard reads (``getaddrinfo``,
``create_connection``), so nothing reaches the real network. The allow
controls connect to a dead socket pair (the TLS handshake then fails; the
pinned address is the point) or to a real local server on 127.0.0.1.
"""

from __future__ import annotations

import logging
import socket
import threading
from datetime import UTC, datetime

import httpx
import pytest

from backend.database.saas_models import WebhookEndpoint
from backend.jobs import webhook_delivery
from backend.jobs.webhook_delivery import deliver_webhook
from backend.tests.test_webhook_api import KEYRING_KEY
from backend.tests.test_webhook_delivery import (
    Receiver,
    _attempts,
    _delivery,
    _one_delivery,
    _update,
)


@pytest.fixture
def settings(settings):
    """The shared test settings plus a webhook keyring."""
    return settings.model_copy(
        update={"webhook_secret_keys": f"wk1:{KEYRING_KEY}", "webhook_secret_active_key_id": "wk1"}
    )


@pytest.fixture(autouse=True)
def worker_settings(settings, monkeypatch):
    """The worker reads get_settings(); here that is the test app's settings."""
    monkeypatch.setattr(webhook_delivery, "get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def no_publish(settings, monkeypatch):
    monkeypatch.setattr(settings, "tasks_eager", True)


@pytest.fixture(autouse=True)
def no_env_proxy(monkeypatch):
    """The direct-path tests must not depend on the runner's proxy environment."""
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class Resolution:
    """Patches ``socket.getaddrinfo`` and ``socket.create_connection``.

    ``resolve`` maps each host to one address, a list of addresses, or a
    ``socket.gaierror``. ``hosts`` records each scripted resolution and
    ``connects`` the address of every connect attempt. A host not in the
    map (an IP literal) delegates to the real ``getaddrinfo`` and is not
    recorded. A connect answers one end of a dead socket pair, so even a
    request that escapes the guard cannot reach the network;
    ``real_connect`` delegates to the real ``create_connection`` (for the
    local-server allow control).
    """

    def __init__(self, monkeypatch, resolve: dict, *, real_connect: bool = False) -> None:
        self.hosts: list[str] = []
        self.connects: list[tuple[str, int]] = []
        self._resolve = resolve
        real_create_connection = socket.create_connection
        real_getaddrinfo = socket.getaddrinfo
        guard = self

        def getaddrinfo(host, port, *args, **kwargs):
            # Positional (stdlib create_connection) or keyword (the guard).
            if host not in resolve:
                return real_getaddrinfo(host, port, *args, **kwargs)
            guard.hosts.append(host)
            answer = resolve[host]
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, str):
                answer = [answer]
            return [
                (
                    socket.AF_INET6 if ":" in address else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (address, port),
                )
                for address in answer
            ]

        def dead_socket(address, *_args, **_kwargs):
            guard.connects.append(address)
            client, peer = socket.socketpair()
            peer.close()
            return client

        def recording(address, *args, **kwargs):
            guard.connects.append(address)
            return real_create_connection(address, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        monkeypatch.setattr(socket, "create_connection", recording if real_connect else dead_socket)


def _production(monkeypatch, settings, mode: str = "cloud") -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "deployment_mode", mode)


def _assert_refused(delivery_id: str) -> None:
    row = _delivery(delivery_id)
    assert (row["state"], row["last_status_code"], row["last_error"]) == (
        "pending",
        None,
        "address_refused",
    )
    assert row["next_attempt_at"] is not None  # the normal retry schedule
    [attempt] = _attempts(delivery_id)
    assert (attempt["outcome"], attempt["status_code"], attempt["error_code"]) == (
        "failed",
        None,
        "address_refused",
    )


def _assert_pinned_connect(delivery_id: str, resolution: Resolution, address: str, port: int):
    assert resolution.hosts == ["hooks.example.com"]  # resolved exactly once
    assert resolution.connects == [(address, port)]
    [attempt] = _attempts(delivery_id)
    assert attempt["outcome"] == "failed"
    assert attempt["error_code"] == "connection_error"  # the dead socket ends the TLS handshake


# --- a resolved private address never gets a socket (E1, E2) ---------------------------


@pytest.mark.parametrize(
    "address",
    ["10.0.0.5", "127.0.0.1", "169.254.169.254", "100.64.1.1", "fd00::5", "::ffff:10.0.0.5"],
)
def test_a_production_cloud_refuses_every_private_resolution(
    client, email_outbox, monkeypatch, now, settings, address
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    resolution = Resolution(monkeypatch, {"hooks.example.com": address})

    deliver_webhook(delivery_id, now=now)

    assert resolution.connects == []
    _assert_refused(delivery_id)


def test_a_mixed_answer_with_one_private_address_is_refused(
    client, email_outbox, monkeypatch, now, settings
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    resolution = Resolution(monkeypatch, {"hooks.example.com": ["93.184.216.34", "10.0.0.5"]})

    deliver_webhook(delivery_id, now=now)

    assert resolution.connects == []
    _assert_refused(delivery_id)


# --- allow controls: the checked address is pinned (E3, E4, E5) ------------------------


def test_a_public_resolution_is_connected_to_by_its_address_only(
    client, email_outbox, monkeypatch, now, settings
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    resolution = Resolution(monkeypatch, {"hooks.example.com": "93.184.216.34"})

    deliver_webhook(delivery_id, now=now)

    _assert_pinned_connect(delivery_id, resolution, "93.184.216.34", 443)


@pytest.mark.parametrize(
    ("address", "refused"),
    [
        ("10.0.0.5", False),  # on-premise CRMs live on the private network
        ("169.254.169.254", True),
        ("127.0.0.1", True),
    ],
)
def test_an_onprem_production_keeps_private_addresses(
    client, email_outbox, monkeypatch, now, settings, address, refused
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings, mode="onprem")
    resolution = Resolution(monkeypatch, {"hooks.example.com": address})

    deliver_webhook(delivery_id, now=now)

    if refused:
        assert resolution.connects == []
        _assert_refused(delivery_id)
    else:
        _assert_pinned_connect(delivery_id, resolution, address, 443)


@pytest.mark.parametrize(
    ("address", "refused"),
    [
        ("127.0.0.1", False),  # local development sends to the loopback
        ("169.254.169.254", True),
    ],
)
def test_a_non_production_cloud_keeps_the_loopback(
    client, email_outbox, monkeypatch, now, settings, address, refused
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    resolution = Resolution(monkeypatch, {"hooks.example.com": address})

    deliver_webhook(delivery_id, now=now)

    if refused:
        assert resolution.connects == []
        _assert_refused(delivery_id)
    else:
        _assert_pinned_connect(delivery_id, resolution, address, 443)


# --- a name to the loopback delivers through a real local server (E5 allow) ------------


class LocalServer:
    """A local HTTP receiver on 127.0.0.1 that answers 204 to every request."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.requests = 0
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self._threads = [threading.Thread(target=self._serve, daemon=True)]

    def __enter__(self) -> LocalServer:
        self._threads[0].start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        for thread in list(self._threads):
            thread.join(timeout=5)
        self._server.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            handler = threading.Thread(target=self._answer, args=(connection,), daemon=True)
            self._threads.append(handler)
            handler.start()

    def _answer(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(1)
            try:
                data = b""
                while b"\r\n\r\n" not in data:
                    part = connection.recv(65536)
                    if not part:
                        return
                    data += part
                self.requests += 1
                self._reply(connection, data.split(b"\r\n")[0].decode("ascii"))
            except OSError:
                return

    def _reply(self, connection: socket.socket, first_line: str) -> None:
        connection.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        try:
            connection.recv(65536)  # hold the connection until the client closes
        except OSError:
            pass


def test_a_resolution_to_the_loopback_delivers_outside_production(
    client, email_outbox, monkeypatch, now
):
    with LocalServer() as server:
        url = f"http://hooks.example.com:{server.port}/x"
        _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now, url=url)
        resolution = Resolution(monkeypatch, {"hooks.example.com": "127.0.0.1"}, real_connect=True)

        deliver_webhook(delivery_id, now=now)

    assert resolution.hosts.count("hooks.example.com") == 1  # the guard resolved it once
    assert resolution.connects == [("127.0.0.1", server.port)]
    assert server.requests == 1
    assert _delivery(delivery_id)["state"] == "delivered"
    [attempt] = _attempts(delivery_id)
    assert (attempt["outcome"], attempt["status_code"], attempt["error_code"]) == (
        "delivered",
        204,
        None,
    )


# --- the refusal is its own outcome and log line (E7) -----------------------------------


def test_a_refusal_is_not_a_connection_error_and_logs_no_host(
    client, email_outbox, monkeypatch, now, settings, caplog
):
    caplog.set_level(logging.DEBUG)
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    resolution = Resolution(monkeypatch, {"hooks.example.com": "10.0.0.5"})

    deliver_webhook(delivery_id, now=now)

    assert resolution.connects == []
    text = "\n".join(record.getMessage() for record in caplog.records if record.name == "homayar")
    assert "WEBHOOK_DELIVERY_ADDRESS_REFUSED delivery_id=" in text
    assert "WEBHOOK_DELIVERY_SEND_ERROR" not in text
    for forbidden in ("hooks.example.com", "10.0.0.5"):
        assert forbidden not in text
    assert _delivery(delivery_id)["last_error"] == "address_refused"


def test_a_dns_failure_stays_a_connection_error(client, email_outbox, monkeypatch, now, settings):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    resolution = Resolution(
        monkeypatch, {"hooks.example.com": socket.gaierror("name or service not known")}
    )

    deliver_webhook(delivery_id, now=now)

    assert resolution.connects == []
    row = _delivery(delivery_id)
    assert (row["state"], row["last_error"]) == ("pending", "connection_error")
    [attempt] = _attempts(delivery_id)
    assert attempt["error_code"] == "connection_error"


def test_an_injected_client_never_meets_the_guard(client, email_outbox, monkeypatch, now, settings):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    receiver = Receiver(httpx.Response(204))

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    assert _delivery(delivery_id)["state"] == "delivered"


# --- an environment proxy keeps priority (E8) -------------------------------------------


class ProxyServer(LocalServer):
    """Answers CONNECT like a plain forward proxy and records the first line."""

    def __init__(self) -> None:
        super().__init__()
        self.connect_lines: list[str] = []

    def _reply(self, connection: socket.socket, first_line: str) -> None:
        self.connect_lines.append(first_line)
        connection.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")


def test_with_an_env_proxy_the_request_goes_to_the_proxy_unguarded(
    client, email_outbox, monkeypatch, now, settings
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    with ProxyServer() as proxy:
        proxy_url = f"http://127.0.0.1:{proxy.port}"
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        resolution = Resolution(monkeypatch, {"hooks.example.com": "10.0.0.5"}, real_connect=True)

        deliver_webhook(delivery_id, now=now)

    # The proxy's own loopback address is never judged, and the URL name is
    # not resolved by the guard: the proxy resolves it.
    assert proxy.connect_lines == ["CONNECT hooks.example.com:443 HTTP/1.1"]
    assert "hooks.example.com" not in resolution.hosts
    assert resolution.connects == [("127.0.0.1", proxy.port)]
    row = _delivery(delivery_id)
    assert row["last_error"] != "address_refused"


def test_a_no_proxy_host_goes_direct_and_is_judged(
    client, email_outbox, monkeypatch, now, settings
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    with ProxyServer() as proxy:
        proxy_url = f"http://127.0.0.1:{proxy.port}"
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        monkeypatch.setenv("NO_PROXY", "hooks.example.com")
        resolution = Resolution(monkeypatch, {"hooks.example.com": "10.0.0.5"})

        deliver_webhook(delivery_id, now=now)

    assert proxy.requests == 0
    assert resolution.connects == []
    _assert_refused(delivery_id)


# --- the URL policy at claim refuses the non-global literals (E6) ----------------------


@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("https://10.1.2.3/h", True),
        ("https://[fd00::1]/h", True),
        ("https://100.64.0.1/h", True),
        ("https://[::ffff:10.1.2.3]/h", True),
        ("https://93.184.216.34/h", False),
    ],
)
def test_the_url_policy_refuses_non_global_literals_at_send(
    client, email_outbox, monkeypatch, now, settings, url, refused
):
    _, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _production(monkeypatch, settings)
    _update(WebhookEndpoint, created["endpoint"]["id"], url=url)
    resolution = Resolution(monkeypatch, {"example.com": "93.184.216.34"})

    deliver_webhook(delivery_id, now=now)

    row = _delivery(delivery_id)
    if refused:
        assert resolution.connects == []
        assert (row["state"], row["last_error"]) == ("dead_letter", "url_refused")
    else:
        assert resolution.connects == [("93.184.216.34", 443)]
        assert row["last_error"] in (None, "connection_error")


# --- the guard is wired into the delivery client's default transport -------------------


def test_the_guarded_client_keeps_the_env_proxies_and_guards_the_default_transport(
    monkeypatch, settings
):
    """E8's wiring: env proxies mount their own transports, only the default one is guarded."""
    from backend.services.webhook_egress_guard import (
        GuardedSyncBackend,
        apply_address_guard,
    )

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("NO_PROXY", "hooks.example.com")
    client = apply_address_guard(
        httpx.Client(timeout=10.0, follow_redirects=False), production=True, cloud=True
    )
    try:
        assert isinstance(client._transport._pool._network_backend, GuardedSyncBackend)
        transport = client._transport_for_url(httpx.URL("https://hooks.example.com/x"))
        assert transport is client._transport  # the guarded default (NO_PROXY)
        proxied = client._transport_for_url(httpx.URL("https://proxied.example.com/x"))
        assert proxied is not client._transport  # the proxy's own transport
    finally:
        client.close()
