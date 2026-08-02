"""Bounded server-owned public-egress discovery and refresh.

The discovery boundary is intentionally fixed to Cloudflare's HTTPS trace
endpoint.  It returns only a validated ``OwnPublicEgressSnapshot`` and never
returns, logs, or embeds the provider body or provider exception.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import math
import threading
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.security_incident_network_context import (
    MAX_EGRESS_SNAPSHOT_AGE_SECONDS,
    OwnPublicEgressSnapshot,
    SecurityIncidentNetworkContextError,
    build_own_public_egress_snapshot,
    canonical_ip,
)


PUBLIC_EGRESS_ENDPOINT = "https://cloudflare.com/cdn-cgi/trace"
PUBLIC_EGRESS_DISABLE_ENV = "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED"
MAX_TRACE_BODY_BYTES = 2 * 1024
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 3.0
DEFAULT_REFRESH_INTERVAL_SECONDS = 15 * 60
DEFAULT_SNAPSHOT_TTL_SECONDS = 60 * 60
_GENERIC_ERROR = "public egress discovery unavailable"


class PublicEgressDiscoveryError(RuntimeError):
    """Raised without provider data when the bounded discovery fails."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def discovery_enabled_from_disable_value(value: Any) -> bool:
    """Default on; only one strict boolean value may disable discovery."""
    if value is None or value == "":
        return True
    if value == "true":
        return False
    if value == "false":
        return True
    return False


def discover_public_egress_snapshot(
    *,
    opener: Any = None,
    clock: Callable[[], float] = time.time,
    timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ttl_seconds: int = DEFAULT_SNAPSHOT_TTL_SECONDS,
) -> OwnPublicEgressSnapshot:
    """Fetch one canonical public address through the fixed HTTPS endpoint."""
    _validate_static_contract(timeout_seconds, ttl_seconds)
    request = Request(
        PUBLIC_EGRESS_ENDPOINT,
        headers={"Accept": "text/plain", "User-Agent": "Odysseus-Security-Egress/1"},
        method="GET",
    )
    try:
        response = _open(opener, request, timeout_seconds)
        with response:
            status = getattr(response, "status", None)
            final_url = response.geturl()
            body = response.read(MAX_TRACE_BODY_BYTES + 1)
        if status != 200 or final_url != PUBLIC_EGRESS_ENDPOINT:
            raise PublicEgressDiscoveryError(_GENERIC_ERROR)
        address = _parse_trace_body(body)
        observed_at = _clock_value(clock)
        return build_own_public_egress_snapshot(
            (address,),
            observed_at=observed_at,
            ttl_seconds=ttl_seconds,
            source="cloudflare_trace",
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
    raise PublicEgressDiscoveryError(_GENERIC_ERROR)


class PublicEgressRefreshController:
    """Atomically publish only validated snapshots and prune stale state."""

    def __init__(
        self,
        *,
        opener: Any = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        ttl_seconds: int = DEFAULT_SNAPSHOT_TTL_SECONDS,
    ) -> None:
        _validate_static_contract(timeout_seconds, ttl_seconds)
        if (
            isinstance(refresh_interval_seconds, bool)
            or not isinstance(refresh_interval_seconds, (int, float))
            or not math.isfinite(float(refresh_interval_seconds))
            or float(refresh_interval_seconds) <= 0
            or float(refresh_interval_seconds) >= ttl_seconds
        ):
            raise ValueError("public egress refresh unavailable")
        if not callable(clock) or not callable(sleeper):
            raise ValueError("public egress refresh unavailable")
        self._opener = opener
        self._clock = clock
        self._sleeper = sleeper
        self._timeout_seconds = float(timeout_seconds)
        self._refresh_interval_seconds = float(refresh_interval_seconds)
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: OwnPublicEgressSnapshot | None = None

    def current_snapshot(self) -> OwnPublicEgressSnapshot | None:
        """Return the current snapshot only while it remains fresh."""
        with self._lock:
            snapshot = self._snapshot
            if snapshot is not None and not snapshot.is_fresh(now=self._safe_now()):
                self._snapshot = None
                snapshot = None
            return snapshot

    def refresh_now(self) -> OwnPublicEgressSnapshot | None:
        """Synchronous bounded refresh for controlled callers and tests."""
        try:
            snapshot = self._discover()
            return self._replace(snapshot)
        except PublicEgressDiscoveryError:
            return self.current_snapshot()

    async def run(
        self,
        publish: Callable[[OwnPublicEgressSnapshot | None], None],
    ) -> None:
        """Refresh off the event loop until cancelled, clearing on shutdown."""
        if not callable(publish):
            raise ValueError("public egress refresh unavailable")
        try:
            while True:
                try:
                    candidate = await asyncio.to_thread(self._discover)
                    snapshot = self._replace(candidate)
                except asyncio.CancelledError:
                    raise
                except PublicEgressDiscoveryError:
                    snapshot = self.current_snapshot()
                publish(snapshot)
                await self._sleeper(self._refresh_interval_seconds)
        finally:
            publish(None)

    def _discover(self) -> OwnPublicEgressSnapshot:
        return discover_public_egress_snapshot(
            opener=self._opener,
            clock=self._clock,
            timeout_seconds=self._timeout_seconds,
            ttl_seconds=self._ttl_seconds,
        )

    def _replace(self, snapshot: Any) -> OwnPublicEgressSnapshot:
        if not isinstance(snapshot, OwnPublicEgressSnapshot) or not snapshot.is_fresh(now=self._safe_now()):
            raise PublicEgressDiscoveryError(_GENERIC_ERROR)
        with self._lock:
            self._snapshot = snapshot
            return snapshot

    def _safe_now(self) -> float:
        try:
            return _clock_value(self._clock)
        except PublicEgressDiscoveryError:
            return float("inf")


def _open(opener: Any, request: Request, timeout_seconds: float) -> Any:
    selected = opener if opener is not None else build_opener(_NoRedirectHandler())
    open_method = getattr(selected, "open", None)
    if callable(open_method):
        return open_method(request, timeout=timeout_seconds)
    if callable(selected):
        return selected(request, timeout=timeout_seconds)
    raise PublicEgressDiscoveryError(_GENERIC_ERROR)


def _parse_trace_body(body: Any) -> str:
    if not isinstance(body, bytes) or not body or len(body) > MAX_TRACE_BODY_BYTES:
        raise PublicEgressDiscoveryError(_GENERIC_ERROR)
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError:
        raise PublicEgressDiscoveryError(_GENERIC_ERROR) from None
    fields = []
    for line in text.splitlines():
        if line.startswith("ip="):
            fields.append(line[3:])
    if len(fields) != 1 or not fields[0] or fields[0] != fields[0].strip():
        raise PublicEgressDiscoveryError(_GENERIC_ERROR)
    try:
        normalized = canonical_ip(fields[0])
    except SecurityIncidentNetworkContextError:
        raise PublicEgressDiscoveryError(_GENERIC_ERROR) from None
    if normalized != fields[0]:
        raise PublicEgressDiscoveryError(_GENERIC_ERROR)
    try:
        snapshot = build_own_public_egress_snapshot(
            (normalized,), observed_at=0, ttl_seconds=1, source="cloudflare_trace"
        )
    except SecurityIncidentNetworkContextError:
        raise PublicEgressDiscoveryError(_GENERIC_ERROR) from None
    return snapshot.addresses[0]


def _clock_value(clock: Callable[[], float]) -> float:
    try:
        value = clock()
    except Exception:
        raise PublicEgressDiscoveryError(_GENERIC_ERROR) from None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise PublicEgressDiscoveryError(_GENERIC_ERROR)
    return float(value)


def _validate_static_contract(timeout_seconds: Any, ttl_seconds: Any) -> None:
    endpoint = urlsplit(PUBLIC_EGRESS_ENDPOINT)
    if (
        endpoint.scheme != "https"
        or endpoint.hostname != "cloudflare.com"
        or endpoint.port is not None
        or endpoint.path != "/cdn-cgi/trace"
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("public egress discovery unavailable")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or not 0 < float(timeout_seconds) <= DEFAULT_DISCOVERY_TIMEOUT_SECONDS
    ):
        raise ValueError("public egress discovery unavailable")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= MAX_EGRESS_SNAPSHOT_AGE_SECONDS
    ):
        raise ValueError("public egress discovery unavailable")


__all__ = [
    "DEFAULT_DISCOVERY_TIMEOUT_SECONDS",
    "DEFAULT_REFRESH_INTERVAL_SECONDS",
    "DEFAULT_SNAPSHOT_TTL_SECONDS",
    "MAX_TRACE_BODY_BYTES",
    "PUBLIC_EGRESS_DISABLE_ENV",
    "PUBLIC_EGRESS_ENDPOINT",
    "PublicEgressDiscoveryError",
    "PublicEgressRefreshController",
    "discover_public_egress_snapshot",
    "discovery_enabled_from_disable_value",
]
