"""Application-layer egress control for AI providers (WP08-T06, ADR 0031).

An adapter may send conversation data only to the endpoint the processor has
just permitted: ``permit(endpoint)`` wraps one policy-approved stage call
(ADR 0028) and ``check(url)`` runs inside the adapter before any connection.
No permit, or a permit for another endpoint, is refused with
``egress_not_permitted``: a code path that bypasses routing (a refused
provider, a hidden fallback, a stray client) cannot reach the network.

``validate_endpoint`` is the endpoint (SSRF) policy for manifests and
adapters: cloud-metadata, link-local, unspecified and multicast addresses are
never an AI endpoint; a *cloud* endpoint must be HTTPS on a public host; a
*private* endpoint may be an internal host or address (explicitly approved
on-premise endpoints).  Addresses a hostname resolves to at request time are
the network layer's job (egress proxy / firewall, see the Session A handoff).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from backend.providers.base import ProviderError

EGRESS_NOT_PERMITTED = "egress_not_permitted"
_PERMIT: ContextVar[str | None] = ContextVar("homayar_ai_egress_permit", default=None)

_METADATA_HOSTS = frozenset(
    {"metadata", "metadata.google.internal", "metadata.azure.com", "instance-data"}
)
_METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(value)
    for value in ("169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254")
)


def _normalize(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port or {"http": 80, "https": 443}.get(scheme, 0)
    return f"{scheme}://{host}:{port}{parts.path.rstrip('/')}"


@contextmanager
def permit(endpoint: str) -> Iterator[None]:
    """Allow requests to exactly ``endpoint`` for the duration of the block."""
    token = _PERMIT.set(_normalize(endpoint))
    try:
        yield
    finally:
        _PERMIT.reset(token)


def check(url: str) -> None:
    """Refuse a provider request that no routing decision covers."""
    allowed = _PERMIT.get()
    if allowed is None or _normalize(url) != allowed:
        raise ProviderError(
            EGRESS_NOT_PERMITTED,
            "An AI provider request was refused: no approved routing decision covers it",
            retryable=False,
        )


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def validate_endpoint(url: str, *, kind: str | None) -> str | None:
    """Why ``url`` cannot be an AI endpoint of this kind, or ``None``.

    ``kind`` is ``cloud``, ``private``, ``local`` (offline deployment profile:
    loopback, private addresses and internal names only, see
    ``is_local_host``) or ``None`` (only the checks that hold for every
    endpoint)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "an endpoint is an HTTP(S) URL with a host"
    if parts.username or parts.password or parts.query or parts.fragment:
        return "an endpoint carries no credentials, query or fragment"
    host = parts.hostname.lower()
    address = _address(host)
    if host in _METADATA_HOSTS or address in _METADATA_ADDRESSES:
        return "cloud metadata services are never an endpoint"
    if address is not None and (
        address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        return "link-local, unspecified, multicast and reserved addresses are never an endpoint"
    if kind == "cloud":
        if parts.scheme != "https":
            return "a cloud endpoint uses HTTPS"
        if host == "localhost" or host.endswith(".localhost"):
            return "a cloud endpoint is a public host"
        if address is not None and (
            address.is_private or address.is_loopback or not address.is_global
        ):
            return "a cloud endpoint is a public host"
    if kind == "local" and not is_local_host(host):
        return "an offline deployment only reaches loopback, private or internal hosts"
    return None


# Name suffixes that never resolve on the public DNS (RFC 6761/6762/8375 and
# common on-premise conventions).  A name outside them is treated as public.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")


def is_local_host(host: str) -> bool:
    """Whether ``host`` is, by its spelling, on this machine or the private
    network: loopback / RFC 1918 / ULA addresses, ``localhost``, single-label
    names (container/service names) and reserved internal suffixes.  What a
    name resolves to at request time remains the network layer's job."""
    value = host.strip().lower().rstrip(".")
    if not value:
        return False
    address = _address(value)
    if address is not None:
        if address in _METADATA_ADDRESSES or address.is_link_local:
            return False
        return address.is_loopback or address.is_private
    if value in _METADATA_HOSTS:
        return False
    if value == "localhost" or "." not in value:
        return True
    return value.endswith(_LOCAL_SUFFIXES)


def is_local_endpoint(url: str) -> bool:
    return validate_endpoint(url, kind="local") is None
