"""Trusted, non-persistent runtime context for tool usage instrumentation."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from typing import Any
from urllib.parse import urlparse

from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageModelScope,
    ToolUsageSurface,
)


_MAX_REFERENCE_INPUT = 512


@dataclass(frozen=True, slots=True)
class TrustedToolUsageContext:
    """Server-derived values only; raw references exist solely until HMAC building."""

    surface: ToolUsageSurface
    agent_mode: ToolUsageAgentMode
    model_scope: ToolUsageModelScope
    owner_identity: str | None = field(default=None, repr=False)
    session_identity: str | None = field(default=None, repr=False)
    run_identity: str | None = field(default=None, repr=False)
    correlation_identity: str | None = field(default=None, repr=False)
    incognito: bool = False
    is_nobody: bool = False

    @classmethod
    def create(
        cls,
        *,
        surface: ToolUsageSurface | str,
        agent_mode: ToolUsageAgentMode | str,
        model_scope: ToolUsageModelScope | str = ToolUsageModelScope.UNKNOWN,
        owner_identity: Any = None,
        session_identity: Any = None,
        run_identity: Any = None,
        correlation_identity: Any = None,
        incognito: bool = False,
        is_nobody: bool = False,
    ) -> "TrustedToolUsageContext":
        if not isinstance(incognito, bool) or not isinstance(is_nobody, bool):
            raise ValueError("incognito and is_nobody must be boolean")
        return cls(
            surface=ToolUsageSurface(surface),
            agent_mode=ToolUsageAgentMode(agent_mode),
            model_scope=ToolUsageModelScope(model_scope),
            owner_identity=_reference(owner_identity),
            session_identity=_reference(session_identity),
            run_identity=_reference(run_identity),
            correlation_identity=_reference(correlation_identity),
            incognito=incognito,
            is_nobody=is_nobody,
        )

    @property
    def persistence_allowed(self) -> bool:
        return not self.incognito and not self.is_nobody

    def audit_dict(self) -> dict[str, Any]:
        return {
            "contract": "odysseus.trusted_tool_usage_context.v1",
            "surface": self.surface.value,
            "agent_mode": self.agent_mode.value,
            "model_scope": self.model_scope.value,
            "incognito": self.incognito,
            "is_nobody": self.is_nobody,
            "persistence_allowed": self.persistence_allowed,
            "owner_identity_visible": False,
            "session_identity_visible": False,
            "run_identity_visible": False,
            "correlation_identity_visible": False,
            "raw_content_visible": False,
        }


def trusted_model_scope(endpoint_url: Any) -> ToolUsageModelScope:
    """Classify a server-selected endpoint without exposing its host or model name."""

    text = str(endpoint_url or "").strip()
    if not text:
        return ToolUsageModelScope.UNKNOWN
    parsed = urlparse(text if "://" in text else f"http://{text}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ToolUsageModelScope.UNKNOWN
    if host in {"localhost", "host.docker.internal", "host.containers.internal"}:
        return ToolUsageModelScope.LOCAL
    try:
        if ipaddress.ip_address(host).is_loopback:
            return ToolUsageModelScope.LOCAL
    except ValueError:
        pass
    return ToolUsageModelScope.REMOTE


def _reference(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if callable(value):
        raise ValueError("trusted reference must not be callable")
    text = str(value)
    if len(text) > _MAX_REFERENCE_INPUT:
        raise ValueError("trusted reference exceeds bounded HMAC input length")
    return text
