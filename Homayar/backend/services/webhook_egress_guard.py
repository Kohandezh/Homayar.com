"""The one webhook address rule and the connect-time guard (HOM-357, ADR 0050 D7).

The URL policy judges what the URL says; ``address_refusal`` here is the one
rule both sides share. The guarded backend resolves the webhook host once,
judges every address, and connects only to the checked addresses, so the name
is never resolved a second time and a public name that rebinds to a private,
loopback or metadata address never gets a socket (the guard refuses the whole
request before connecting).

With an environment proxy (``HTTP_PROXY``/``HTTPS_PROXY``/``NO_PROXY``,
ADR 0031) the request goes to the proxy unchanged and the proxy resolves the
target and enforces its own rules: the guard is wired into the client's
default transport only, never into the proxy mounts httpx builds from the
environment.
"""

from __future__ import annotations

import ipaddress
import socket

import httpcore
import httpx

from backend.providers.egress import _METADATA_ADDRESSES

# IPv6 forms that carry an IPv4 address in their last 32 bits: NAT64 (the
# well-known prefix) and the old IPv4-compatible form. They are judged as
# that IPv4 address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class WebhookAddressRefused(Exception):
    """The connect-time guard refused the request: any one address was refused."""


def _embedded_ipv4(address: Address | None) -> Address | None:
    if address is None or address.version == 4:
        return address
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    # ``::`` and ``::1`` are the IPv6 unspecified and loopback addresses, not IPv4.
    if address in _NAT64 or (address in _IPV4_COMPATIBLE and int(address) > 1):
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def address_refusal(address: Address, *, production: bool, cloud: bool = False) -> str | None:
    """Why ``address`` cannot receive a webhook request, or ``None``.

    The one address rule, shared by the URL policy (an IP literal) and the
    connect-time guard (every address a name resolves to):

    * every deployment refuses metadata, link-local, unspecified and
      multicast addresses;
    * production also refuses loopback;
    * a production *cloud* deployment also refuses every non-global address
      (RFC 1918, CGNAT ``100.64.0.0/10``, IPv6 ULA, reserved and
      documentation ranges), so a request can only leave for the public
      network. On-premise production keeps private addresses allowed
      (on-premise CRMs live there), as does every non-production deployment.

    An IPv6 form that carries an IPv4 address is judged as that address. The
    reason never repeats the URL or the host.
    """
    address = _embedded_ipv4(address)
    if address in _METADATA_ADDRESSES:
        return "cloud metadata services are never a webhook endpoint"
    if address.is_link_local or address.is_unspecified or address.is_multicast:
        return "link-local, unspecified and multicast addresses are never a webhook endpoint"
    if production:
        if address.is_loopback:
            return "a webhook URL is not a loopback host in production"
        if cloud and not address.is_global:
            return "a cloud webhook URL is a public address"
    return None


class GuardedSyncBackend(httpcore.SyncBackend):
    """Resolves once, judges every address, then connects to the addresses only."""

    def __init__(self, *, production: bool, cloud: bool) -> None:
        super().__init__()
        self._production = production
        self._cloud = cloud

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: tuple | None = None,
    ) -> httpcore.NetworkStream:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise httpcore.ConnectError("name resolution failed") from exc
        addresses: list[str] = []
        for info in infos:
            try:
                address = ipaddress.ip_address(info[-1][0])
            except ValueError as exc:
                raise httpcore.ConnectError("name resolution failed") from exc
            if address_refusal(address, production=self._production, cloud=self._cloud):
                raise WebhookAddressRefused(
                    "the webhook address check refused the resolved addresses"
                )
            addresses.append(info[-1][0])
        if not addresses:
            raise httpcore.ConnectError("name resolution failed")
        failure: Exception | None = None
        for address in addresses:
            try:
                return super().connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                failure = exc
        raise failure


def apply_address_guard(client: httpx.Client, *, production: bool, cloud: bool) -> httpx.Client:
    """Give ``client``'s default transport the guarded backend, in place.

    The client is built exactly as before, so httpx still reads the
    environment proxies into its own mounts; only the default, non-proxy
    transport connects through the guard. Private httpx attributes are used
    here and nowhere else; the connect-guard tests fail if this stops working.
    """
    pool = client._transport._pool
    pool._network_backend = GuardedSyncBackend(production=production, cloud=cloud)
    return client


def guarded_client(timeout: float, *, production: bool, cloud: bool) -> httpx.Client:
    """The delivery client: environment proxies kept, direct connects guarded."""
    return apply_address_guard(
        httpx.Client(timeout=timeout, follow_redirects=False),
        production=production,
        cloud=cloud,
    )
