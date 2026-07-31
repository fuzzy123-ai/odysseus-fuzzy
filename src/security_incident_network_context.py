"""Trusted ingress context and fail-open self-egress suppression helpers.

This module is deliberately offline: own public addresses are supplied by a
server-owned deployment snapshot.  It never resolves an address, calls a
provider, or preserves raw forwarding headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import math
import re
import time
from typing import Any, Iterable, Mapping


NETWORK_CONTEXT_POLICY_VERSION = "ops-alert-c2.v1"
MAX_TRUSTED_PROXY_NETWORKS = 16
MAX_OWN_PUBLIC_IPS = 16
MAX_EGRESS_SNAPSHOT_AGE_SECONDS = 24 * 60 * 60
SECURITY_CRITICAL_EVENT_CLASSES = frozenset({
    "authentication_failure", "lockout", "new_privileged_session",
    "role_change", "credential_change", "step_up_failure", "break_glass",
    "session_anomaly", "remediation", "independent_security_reason",
})
SUPPRESSION_EVENT_CLASSES = SECURITY_CRITICAL_EVENT_CLASSES | frozenset({"external_access_origin_only"})
ACCESS_CONTEXT_REASON_CODES = frozenset({
    "direct_peer", "direct_peer_unavailable", "trusted_proxy_configuration_invalid",
    "trusted_proxy_forwarding_unavailable", "trusted_proxy_forwarding_invalid",
    "trusted_proxy_forwarding_conflict", "trusted_proxy_forwarded",
})
_PROVENANCE = frozenset({"direct_peer", "trusted_proxy_forwarded"})
_ORIGIN_ONLY_EVENT = "external_access_origin_only"
_FORWARDED_FOR = re.compile(r'^for=(?:"?)([^;,"]+)(?:"?)$', re.IGNORECASE)
_PRIVATE_PROXY_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"), ipaddress.ip_network("fc00::/7"),
)


class SecurityIncidentNetworkContextError(ValueError):
    """Raised for unsafe or ambiguous network context."""


@dataclass(frozen=True, slots=True)
class AccessSourceContext:
    canonical_ip: str | None
    provenance: str
    is_public: bool
    reason_code: str

    def as_incident_projection(self) -> dict[str, Any]:
        """Return the sole raw-IP projection permitted for the incident record."""
        return {
            "canonical_ip": self.canonical_ip or "",
            "provenance": self.provenance,
            "is_public": self.is_public,
            "reason_code": self.reason_code,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class OwnPublicEgressSnapshot:
    addresses: tuple[str, ...]
    observed_at: float
    ttl_seconds: int
    source: str = "configured_deployment_data"

    def is_fresh(self, *, now: Any = None) -> bool:
        try:
            current = _now(now)
        except SecurityIncidentNetworkContextError:
            return False
        return 0 <= current - self.observed_at <= self.ttl_seconds


def canonical_ip(value: Any) -> str:
    """Normalize one bare IPv4/IPv6 address, rejecting hosts, ports and zones."""
    if not isinstance(value, str):
        raise SecurityIncidentNetworkContextError("IP address unavailable")
    text = value.strip()
    if not text or len(text) > 80 or "%" in text or "/" in text or "[" in text or "]" in text:
        raise SecurityIncidentNetworkContextError("IP address unavailable")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        raise SecurityIncidentNetworkContextError("IP address unavailable") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def derive_access_source_context(request: Any, *, trusted_proxy_networks: Iterable[Any] = ()) -> AccessSourceContext:
    """Derive an ingress IP only from a direct peer or explicitly trusted proxy.

    Every forwarding header present on a trusted hop must name the same single
    address.  A chain, malformed header or untrusted peer is never evidence.
    """
    try:
        peer = canonical_ip(getattr(getattr(request, "client", None), "host", None))
    except SecurityIncidentNetworkContextError:
        return AccessSourceContext(None, "unknown", False, "direct_peer_unavailable")
    try:
        networks = _trusted_networks(trusted_proxy_networks)
    except SecurityIncidentNetworkContextError:
        return AccessSourceContext(None, "unknown", False, "trusted_proxy_configuration_invalid")
    if not any(ipaddress.ip_address(peer) in network for network in networks):
        return AccessSourceContext(peer, "direct_peer", _is_public(peer), "direct_peer")
    values = _trusted_forwarded_candidates(getattr(request, "headers", {}) or {})
    if values is None:
        return AccessSourceContext(None, "unknown", False, "trusted_proxy_forwarding_unavailable")
    try:
        candidates = {canonical_ip(value) for value in values}
    except SecurityIncidentNetworkContextError:
        return AccessSourceContext(None, "unknown", False, "trusted_proxy_forwarding_invalid")
    if len(candidates) != 1:
        return AccessSourceContext(None, "unknown", False, "trusted_proxy_forwarding_conflict")
    source = next(iter(candidates))
    return AccessSourceContext(source, "trusted_proxy_forwarded", _is_public(source), "trusted_proxy_forwarded")


def build_own_public_egress_snapshot(
    addresses: Iterable[Any], *, observed_at: Any, ttl_seconds: Any = MAX_EGRESS_SNAPSHOT_AGE_SECONDS,
    source: str = "configured_deployment_data",
) -> OwnPublicEgressSnapshot:
    """Validate a configured, server-owned snapshot without discovering IPs."""
    if source != "configured_deployment_data":
        raise SecurityIncidentNetworkContextError("own egress source unavailable")
    if isinstance(addresses, (str, bytes)):
        raise SecurityIncidentNetworkContextError("own egress snapshot unavailable")
    try:
        values = tuple(sorted({canonical_ip(value) for value in addresses}))
    except (TypeError, SecurityIncidentNetworkContextError):
        raise SecurityIncidentNetworkContextError("own egress snapshot unavailable") from None
    if not values or len(values) > MAX_OWN_PUBLIC_IPS or any(not _is_public(value) for value in values):
        raise SecurityIncidentNetworkContextError("own egress snapshot unavailable")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= MAX_EGRESS_SNAPSHOT_AGE_SECONDS:
        raise SecurityIncidentNetworkContextError("own egress snapshot unavailable")
    return OwnPublicEgressSnapshot(values, _now(observed_at), ttl_seconds, source)


def decide_self_egress_suppression(
    *, incident_id: Any, event_class: Any, source_context: Any,
    own_public_egress: OwnPublicEgressSnapshot | None, independent_security_reason: Any = False,
    now: Any = None,
) -> dict[str, Any]:
    """Return a bounded audit decision; uncertainty always leaves alerts enabled."""
    reason = "notification_required_unknown"
    suppress = False
    context = _context(source_context)
    event = _suppression_event_class(event_class)
    critical = event in SECURITY_CRITICAL_EVENT_CLASSES or independent_security_reason is True
    if critical:
        reason = "notification_required_security_critical"
    elif event != _ORIGIN_ONLY_EVENT:
        reason = "notification_required_not_origin_only"
    elif context is None or context.provenance not in _PROVENANCE or not context.canonical_ip:
        reason = "notification_required_source_unknown"
    elif not context.is_public:
        reason = "notification_required_source_not_public"
    elif not isinstance(own_public_egress, OwnPublicEgressSnapshot) or not own_public_egress.is_fresh(now=now):
        reason = "notification_required_own_egress_unavailable"
    elif context.canonical_ip not in own_public_egress.addresses:
        reason = "notification_required_egress_mismatch"
    else:
        suppress = True
        reason = "suppressed_exact_fresh_self_egress_match"
    return {
        "policy_version": NETWORK_CONTEXT_POLICY_VERSION,
        "incident_ref": _incident_ref(incident_id),
        "event_class": event,
        "decision": "suppress_notification" if suppress else "notify",
        "reason_code": reason,
        "source_ref": _source_ref(context.canonical_ip) if context and context.canonical_ip else "",
        "raw_content_visible": False,
    }


def _trusted_networks(values: Iterable[Any]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    if isinstance(values, (str, bytes)):
        raise SecurityIncidentNetworkContextError("trusted proxy configuration unavailable")
    try:
        raw = tuple(values)
    except TypeError:
        raise SecurityIncidentNetworkContextError("trusted proxy configuration unavailable") from None
    if len(raw) > MAX_TRUSTED_PROXY_NETWORKS:
        raise SecurityIncidentNetworkContextError("trusted proxy configuration unavailable")
    if any(not isinstance(value, str) or not value or "%" in value for value in raw):
        raise SecurityIncidentNetworkContextError("trusted proxy configuration unavailable")
    try:
        networks = tuple(ipaddress.ip_network(value, strict=False) for value in raw)
    except ValueError:
        raise SecurityIncidentNetworkContextError("trusted proxy configuration unavailable") from None
    if any(not any(network.version == allowed.version and network.subnet_of(allowed) for allowed in _PRIVATE_PROXY_NETWORKS) for network in networks):
        raise SecurityIncidentNetworkContextError("trusted proxy configuration unavailable")
    return networks


def _trusted_forwarded_candidates(headers: Any) -> tuple[str, ...] | None:
    if not hasattr(headers, "get"):
        return None
    raw = []
    for name in ("forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        value = headers.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            return None
        raw.append((name, value.strip()))
    if not raw:
        return None
    result = []
    for name, value in raw:
        if name == "forwarded":
            match = _FORWARDED_FOR.fullmatch(value)
            if not match:
                return None
            result.append(match.group(1))
        else:
            if "," in value:
                return None
            result.append(value)
    return tuple(result)


def validate_access_source_context(value: Any) -> AccessSourceContext:
    """Validate the one fixed IP-context schema shared by all consumers."""
    candidate = value.as_incident_projection() if isinstance(value, AccessSourceContext) else value
    if not isinstance(candidate, Mapping) or set(candidate) != {"canonical_ip", "provenance", "is_public", "reason_code", "raw_content_visible"}:
        raise SecurityIncidentNetworkContextError("access context unavailable")
    if candidate.get("raw_content_visible") is not False or type(candidate.get("is_public")) is not bool:
        raise SecurityIncidentNetworkContextError("access context unavailable")
    try:
        normalized = canonical_ip(candidate.get("canonical_ip"))
    except SecurityIncidentNetworkContextError:
        raise SecurityIncidentNetworkContextError("access context unavailable") from None
    provenance, reason_code = candidate.get("provenance"), candidate.get("reason_code")
    if normalized != candidate.get("canonical_ip") or provenance not in _PROVENANCE or reason_code not in ACCESS_CONTEXT_REASON_CODES or candidate["is_public"] is not _is_public(normalized):
        raise SecurityIncidentNetworkContextError("access context unavailable")
    return AccessSourceContext(normalized, provenance, candidate["is_public"], reason_code)


def _context(value: Any) -> AccessSourceContext | None:
    try:
        return validate_access_source_context(value)
    except SecurityIncidentNetworkContextError:
        return None


def _suppression_event_class(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    event = value.strip().lower()
    return event if event in SUPPRESSION_EVENT_CLASSES else "unknown"


def _is_public(value: str) -> bool:
    return ipaddress.ip_address(value).is_global


def _now(value: Any = None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc).timestamp() if value.tzinfo else value.replace(tzinfo=timezone.utc).timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise SecurityIncidentNetworkContextError("network context clock unavailable")
    return float(value)


def _source_ref(address: str) -> str:
    return "source_ip:sha256:" + hashlib.sha256((NETWORK_CONTEXT_POLICY_VERSION + "|" + address).encode("utf-8")).hexdigest()


def _incident_ref(value: Any) -> str:
    text = str(value or "").strip()
    return "incident:sha256:" + hashlib.sha256((NETWORK_CONTEXT_POLICY_VERSION + "|" + text).encode("utf-8")).hexdigest()


__all__ = [
    "ACCESS_CONTEXT_REASON_CODES", "AccessSourceContext", "MAX_EGRESS_SNAPSHOT_AGE_SECONDS", "NETWORK_CONTEXT_POLICY_VERSION",
    "OwnPublicEgressSnapshot", "SECURITY_CRITICAL_EVENT_CLASSES", "SecurityIncidentNetworkContextError",
    "build_own_public_egress_snapshot", "canonical_ip", "decide_self_egress_suppression",
    "derive_access_source_context", "validate_access_source_context",
]
