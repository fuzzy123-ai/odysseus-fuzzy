"""Runtime hook for secure-chat provider/model selection."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any, Mapping
from urllib.parse import urlparse

from src.chat_security_state import ProviderScope, SecurityMode
from src.privacy_runtime import create_runtime_security_state, is_dsgvo_mode_enabled
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


def should_enforce_session_provider_runtime_gate(
    security_mode: Any,
    *,
    settings: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether session provider choice needs runtime policy validation."""

    return bool(str(security_mode or "").strip()) or is_dsgvo_mode_enabled(settings)


def enforce_session_provider_runtime_gate(
    *,
    security_mode: Any,
    session_id: Any,
    owner: Any,
    provider_base_url: Any,
    model_id: Any,
    provider_id: Any = "session-provider",
    settings: Mapping[str, Any] | None = None,
) -> ProviderRuntimeGate:
    """Block external provider/model choices before a secure session can use them."""

    provider_scope = provider_scope_for_base_url(provider_base_url)
    requested_by = str(owner or "runtime").strip() or "runtime"
    state = create_runtime_security_state(
        chat_id=str(session_id or "pending-session"),
        thread_id=str(session_id or "pending-session"),
        security_mode=security_mode,
        requested_by=requested_by,
        settings=settings,
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
        security_mode=state.security_mode,
        provider_scope=provider_scope,
        route_decision=route,
    )
