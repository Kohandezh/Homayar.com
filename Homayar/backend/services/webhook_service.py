"""Outbound webhook endpoints, signing keys and event enqueue (HOM-89, ADR 0047).

The URL policy here runs at create, at update and again before every send.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from backend.providers.egress import _METADATA_ADDRESSES, _METADATA_HOSTS, _address

# Only events that something sends are advertised. Later tickets add business
# events together with their producers.
WEBHOOK_EVENT_TYPES: tuple[str, ...] = ("webhook.test",)

WEBHOOK_URL_MAX_LENGTH = 1000

# Spaces and control characters. urlsplit() silently drops some of them, so
# they are refused before parsing.
_UNSAFE_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
# A last label that is all digits or 0x-hex: resolvers may read such a host
# as an IPv4 address (2852039166, 0xA9FEA9FE, 169.254.43518). No real domain
# ends this way, so only the canonical dotted form of an address is accepted.
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]*)")


def webhook_url_refusal(url: str, *, production: bool) -> str | None:
    """Why ``url`` cannot be a webhook endpoint, or ``None``.

    Refused everywhere: too long, not http(s), no host, userinfo, cloud
    metadata, link-local, unspecified or multicast addresses. In production
    also plain http and loopback. Private addresses stay allowed: on-premise
    CRMs live there. What a hostname resolves to at send time is the network
    layer's job (ADR 0031). The reason never repeats the URL, which can carry a
    token in its query.
    """
    if len(url) > WEBHOOK_URL_MAX_LENGTH:
        return f"a webhook URL is at most {WEBHOOK_URL_MAX_LENGTH} characters"
    if _UNSAFE_CHARACTERS.search(url):
        return "a webhook URL has no spaces or control characters"
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a port out of range
    except ValueError:
        return "the webhook URL is not valid"
    if parts.scheme not in {"http", "https"}:
        return "a webhook URL uses http or https"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "a webhook URL has a host"
    if "@" in parts.netloc:
        return "a webhook URL carries no user name or password"
    address = _address(host)
    if address is not None and address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address is None and (":" in host or _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1])):
        return "a webhook URL host is a name or a dotted IP address"
    if host in _METADATA_HOSTS or address in _METADATA_ADDRESSES:
        return "cloud metadata services are never a webhook endpoint"
    if address is not None and (
        address.is_link_local or address.is_unspecified or address.is_multicast
    ):
        return "link-local, unspecified and multicast addresses are never a webhook endpoint"
    if production:
        if parts.scheme != "https":
            return "a webhook URL uses https in production"
        if _is_loopback(host, address):
            return "a webhook URL is not a loopback host in production"
    return None


def _is_loopback(host: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> bool:
    if address is not None:
        return address.is_loopback
    return host == "localhost" or host.endswith(".localhost")
