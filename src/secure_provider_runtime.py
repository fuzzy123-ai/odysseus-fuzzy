"""Runtime hook for secure-chat provider/model selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
from typing import Any
from urllib.parse import urlparse

from src.chat_security_state import ChatSecurityState, ProviderScope, SecurityMode, normalize_security_mode
from src.secure_model_routing import ModelCandidate, ModelRouteDecision, ModelUse, decide_model_route


class SecureProviderRuntimeError(ValueError):
    """Raised when a provider/model selection violates secure runtime policy."""


@dataclass(frozen=True, slots=True)
class ProviderRuntimeGate:
    security_mode: SecurityMode
    provider_scope: ProviderScope
    route_decision: ModelRouteDecision

    @property
    def allowed(self) -> bool:
        return self.route_decision.allowed

    @property
    def block_reason(self) -> str:
        return self.route_decision.block_reason


def provider_scope_for_base_url(base_url: Any) -> ProviderScope:
    """Classify a configured provider endpoint as local-only or default scope."""

    parsed = urlparse(str(base_url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ProviderScope.DEFAULT
    if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
        return ProviderScope.LOCAL_ONLY
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return ProviderScope.DEFAULT
    if address.is_loopback or address.is_private or address.is_link_local:
        return ProviderScope.LOCAL_ONLY
    return ProviderScope.DEFAULT


def enforce_session_provider_runtime_gate(
    *,
    security_mode: Any,
    session_id: Any,
    owner: Any,
    provider_base_url: Any,
    model_id: Any,
    provider_id: Any = "session-provider",
) -> ProviderRuntimeGate:
    """Block external provider/model choices before a secure session can use them."""

    mode = normalize_security_mode(str(security_mode or "normal"))
    provider_scope = provider_scope_for_base_url(provider_base_url)
    requested_by = str(owner or "runtime").strip() or "runtime"
    state = ChatSecurityState.create(
        chat_id=str(session_id or "pending-session"),
        thread_id=str(session_id or "pending-session"),
        security_mode=mode,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        requested_by=requested_by,
    )
    route = decide_model_route(
        state=state,
        primary=ModelCandidate.create(
            model_id=str(model_id or "pending-model"),
            provider_id=str(provider_id or "session-provider"),
            provider_scope=provider_scope,
            use=ModelUse.CHAT,
        ),
    )
    if not route.allowed:
        raise SecureProviderRuntimeError(route.block_reason)
    return ProviderRuntimeGate(
        security_mode=mode,
        provider_scope=provider_scope,
        route_decision=route,
    )
