"""Egress probes run INSIDE an application container (stdlib only).

Prints one JSON object: probe name -> outcome. Used by
test-egress-network.sh; it never contacts a real provider (the allowed and
denied destinations are disposable containers of the test).
"""

from __future__ import annotations

import json
import os
import socket
import sys

PROXY = ("egress-proxy", 3128)
TIMEOUT = 4.0


def connect(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return "connected"
    except socket.gaierror:
        return "no_dns"
    except TimeoutError:
        return "timeout"
    except OSError as exc:
        return f"refused:{exc.errno}"


def via_proxy(request: bytes) -> str:
    try:
        with socket.create_connection(PROXY, timeout=TIMEOUT) as sock:
            sock.sendall(request)
            head = sock.recv(4096).split(b"\r\n", 1)[0].decode("latin-1")
            return head.split(" ", 2)[1] if head.startswith("HTTP/") else f"bad:{head[:40]}"
    except OSError as exc:
        return f"error:{type(exc).__name__}"


def connect_then_get(host: str, port: int) -> str:
    try:
        with socket.create_connection(PROXY, timeout=TIMEOUT) as sock:
            sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            reply = sock.recv(4096)
            status = reply.split(b" ", 2)[1].decode()
            if status != "200":
                return f"connect_{status}"
            sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            data = sock.recv(4096)
            return "tunnel_" + data.split(b" ", 2)[1].decode()
    except OSError as exc:
        return f"error:{type(exc).__name__}"


def get(url: str) -> bytes:
    host = url.split("/")[2]
    return f"GET {url} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()


def main() -> None:
    allowed_ip = os.environ.get("ALLOWED_IP", "")
    only_direct = "--direct-only" in sys.argv
    results = {
        "direct_allowed_name": connect("egress-test-allowed", 8080),
        "direct_allowed_ip": connect(allowed_ip, 8080) if allowed_ip else "skipped",
        "direct_public_ip": connect("1.1.1.1", 443),
        "direct_metadata": connect("169.254.169.254", 80),
    }
    if not only_direct:
        results.update(
            {
                "proxy_get_allowed": via_proxy(get("http://egress-test-allowed:8080/")),
                "proxy_connect_allowed": connect_then_get("egress-test-allowed", 8080),
                "proxy_connect_denied_host": connect_then_get("egress-test-denied", 8080),
                "proxy_connect_allowed_host_other_port": connect_then_get("egress-test-allowed", 22),
                "proxy_get_denied": via_proxy(get("http://egress-test-denied:8080/")),
                "proxy_metadata": connect_then_get("169.254.169.254", 80),
                "proxy_manager": via_proxy(get("http://egress-proxy:3128/squid-internal-mgr/menu")),
            }
        )
        try:
            import httpx  # the application's own client, honouring HTTP(S)_PROXY

            results["app_client_allowed"] = str(httpx.get("http://egress-test-allowed:8080/", timeout=TIMEOUT).status_code)
            results["app_client_denied"] = str(httpx.get("http://egress-test-denied:8080/", timeout=TIMEOUT).status_code)
        except ImportError:
            results["app_client_allowed"] = "no_httpx"
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
