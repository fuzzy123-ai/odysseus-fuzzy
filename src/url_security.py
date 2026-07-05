"""URL validation helpers for server-side outbound requests."""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


_INTERNAL_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}

_INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".intranet",
)

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _resolve_hostname_ips(hostname: str) -> list[ipaddress._BaseAddress]:
    ips: list[ipaddress._BaseAddress] = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
        if family in (socket.AF_INET, socket.AF_INET6):
            ips.append(ipaddress.ip_address(sockaddr[0]))
    return ips


def _blocked_ip(addr: ipaddress._BaseAddress) -> bool:
    return (
        any(addr in net for net in _BLOCKED_NETWORKS)
        or addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


def _host_resolves_publicly(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host in _INTERNAL_HOSTNAMES or host.endswith(_INTERNAL_SUFFIXES):
        return False
    try:
        return not _blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        addrs = _resolve_hostname_ips(host)
    except OSError:
        return False
    return bool(addrs) and all(not _blocked_ip(addr) for addr in addrs)


def resolve_public_hostname_ips(hostname: str) -> tuple[str, ...]:
    """Resolve a hostname and return public IP literals suitable for pinning."""

    host = hostname.strip().lower()
    if host in _INTERNAL_HOSTNAMES or host.endswith(_INTERNAL_SUFFIXES):
        raise ValueError("Host is internal")
    try:
        addr = ipaddress.ip_address(host)
        addrs = [addr]
    except ValueError:
        try:
            addrs = _resolve_hostname_ips(host)
        except OSError as exc:
            raise ValueError("Host could not be resolved") from exc
    if not addrs or any(_blocked_ip(addr) for addr in addrs):
        raise ValueError("Host must resolve only to public IP addresses")
    return tuple(str(addr) for addr in addrs)


def is_public_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _host_resolves_publicly(parsed.hostname)


def validate_public_http_url(url: str, *, max_length: int = 2048) -> str:
    """Validate a user/API-token supplied server-side HTTP(S) endpoint.

    This is for untrusted outbound URLs, not admin-created model endpoints
    that are intentionally allowed to point at private model providers. DNS
    failures fail closed. Use ``PinnedPublicHttpTransport.for_url`` for the
    actual outbound request so DNS cannot be rebound between validation and
    connect.
    """
    cleaned = (url or "").strip()
    if len(cleaned) > max_length:
        raise ValueError("URL is too long")
    if not is_public_http_url(cleaned):
        raise ValueError("URL must point to a public HTTP(S) endpoint")
    return cleaned


@dataclass(frozen=True, slots=True)
class PinnedPublicHttpEndpoint:
    url: str
    hostname: str
    port: int | None
    pinned_ips: tuple[str, ...]


class PinnedPublicHttpTransport(httpx.AsyncBaseTransport):
    """HTTPX transport for untrusted URLs with resolved-public IP pinning.

    The request URL is rewritten to a pre-resolved public IP literal, while the
    original Host header and TLS SNI hostname are preserved. That removes the
    second DNS resolution normally performed by the HTTP client and closes the
    token-supplied base_url DNS-rebinding gap.
    """

    def __init__(
        self,
        endpoint: PinnedPublicHttpEndpoint,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self._transport = transport or httpx.AsyncHTTPTransport(retries=0)
        self.requests: int = 0

    @classmethod
    def for_url(cls, url: str) -> "PinnedPublicHttpTransport":
        cleaned = validate_public_http_url(url)
        parsed = urlparse(cleaned)
        hostname = (parsed.hostname or "").lower()
        pinned_ips = resolve_public_hostname_ips(hostname)
        return cls(PinnedPublicHttpEndpoint(
            url=cleaned,
            hostname=hostname,
            port=parsed.port,
            pinned_ips=pinned_ips,
        ))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        hostname = (request.url.host or "").lower()
        if hostname != self.endpoint.hostname:
            raise httpx.ConnectError("Pinned public transport host mismatch", request=request)
        pinned_ip = self.endpoint.pinned_ips[self.requests % len(self.endpoint.pinned_ips)]
        self.requests += 1
        rewritten_url = request.url.copy_with(host=pinned_ip)
        headers = request.headers.copy()
        headers["Host"] = _host_header(self.endpoint.hostname, request.url.port)
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self.endpoint.hostname
        pinned_request = httpx.Request(
            request.method,
            rewritten_url,
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._transport.aclose()


def _host_header(hostname: str, port: int | None) -> str:
    if port is None:
        return hostname
    return f"{hostname}:{port}"


def direct_base_url_enabled() -> bool:
    """Whether API-token callers may provide an arbitrary direct base_url.

    Direct token-supplied endpoints are deliberately opt-in. Admin-created
    endpoint rows remain the normal path for local/LAN providers because they
    are stored server-side and owner-scoped before use.
    """
    return os.getenv("ODYSSEUS_API_TOKEN_DIRECT_BASE_URL_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
