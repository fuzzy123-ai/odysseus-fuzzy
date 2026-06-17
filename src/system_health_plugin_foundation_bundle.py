"""Foundation bundle readiness models for system health checker plugin work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.system_health_agent_interface import HealthAgentInterfaceError


class FoundationComponentStatus(StrEnum):
    READY = "ready"
    PLANNED = "planned"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    NOT_APPLICABLE = "not_applicable"


_FOUNDATION_COMPONENT_IDS = (
    "interface",
    "basic_collectors",
    "advanced_collectors",
    "rule_engine",
    "telegram_pull",
    "auto_alerting_decision",
    "container_runtime_adapter",
    "ops_readiness",
    "dashboard_summary",
)

_RUNTIME_GATE_IDS = (
    "host_agent_runtime",
    "telegram_delivery",
    "ui_integration",
    "privileged_host_access",
    "real_network_io",
)

_ALL_COMPONENT_IDS = _FOUNDATION_COMPONENT_IDS + _RUNTIME_GATE_IDS


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_component_id(value: Any) -> str:
    text = _normalize_text(value, field_name="component_id").strip().lower()
    if text not in _ALL_COMPONENT_IDS:
        raise HealthAgentInterfaceError("unsupported component_id")
    return text


def _normalize_status(value: FoundationComponentStatus | str) -> FoundationComponentStatus:
    if isinstance(value, FoundationComponentStatus):
        return value
    text = _normalize_text(value, field_name="status").lower()
    try:
        return FoundationComponentStatus(text)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported foundation component status") from exc


def _is_foundation_component(component_id: str) -> bool:
    return component_id in _FOUNDATION_COMPONENT_IDS


@dataclass(frozen=True, slots=True)
class FoundationComponent:
    component_id: str
    status: FoundationComponentStatus
    summary: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        component_id: Any,
        status: FoundationComponentStatus | str,
        summary: Any,
        next_action: Any = "",
    ) -> "FoundationComponent":
        normalized_id = _normalize_component_id(component_id)
        normalized_status = _normalize_status(status)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True)
        if normalized_status in {
            FoundationComponentStatus.PLANNED,
            FoundationComponentStatus.BLOCKED,
            FoundationComponentStatus.DEFERRED,
        } and not normalized_next_action:
            normalized_next_action = "leave this capability outside foundation mode until runtime approval exists"
        return cls(
            component_id=normalized_id,
            status=normalized_status,
            summary=_normalize_text(summary, field_name="summary"),
            next_action=normalized_next_action,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "status": self.status.value,
            "summary": self.summary,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class FoundationBundleReadiness:
    mode: str
    foundation_ready: bool
    components: tuple[FoundationComponent, ...]

    @classmethod
    def create(
        cls,
        *,
        mode: Any,
        components: Iterable[FoundationComponent],
    ) -> "FoundationBundleReadiness":
        normalized_components = tuple(components)
        if not normalized_components:
            raise HealthAgentInterfaceError("components must not be empty")
        if any(not isinstance(item, FoundationComponent) for item in normalized_components):
            raise HealthAgentInterfaceError("components must contain FoundationComponent instances")

        seen = {item.component_id for item in normalized_components}
        missing = set(_ALL_COMPONENT_IDS) - seen
        if missing:
            raise HealthAgentInterfaceError(f"components missing required ids: {', '.join(sorted(missing))}")

        foundation_ready = all(
            item.status == FoundationComponentStatus.READY
            for item in normalized_components
            if _is_foundation_component(item.component_id)
        ) and all(
            item.status != FoundationComponentStatus.READY
            for item in normalized_components
            if not _is_foundation_component(item.component_id)
        )

        return cls(
            mode=_normalize_text(mode, field_name="mode"),
            foundation_ready=foundation_ready,
            components=tuple(sorted(normalized_components, key=lambda item: item.component_id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "foundation_ready": self.foundation_ready,
            "components": tuple(item.to_dict() for item in self.components),
        }


def build_foundation_bundle_readiness() -> FoundationBundleReadiness:
    return FoundationBundleReadiness.create(
        mode="foundation",
        components=(
            FoundationComponent.create(
                component_id="interface",
                status="ready",
                summary="host-facing health snapshot interface model exists",
            ),
            FoundationComponent.create(
                component_id="basic_collectors",
                status="ready",
                summary="basic collector reading models are available",
            ),
            FoundationComponent.create(
                component_id="advanced_collectors",
                status="ready",
                summary="advanced collector parsers for fixture payloads are available",
            ),
            FoundationComponent.create(
                component_id="rule_engine",
                status="ready",
                summary="rule evaluation and alert synthesis models are available",
            ),
            FoundationComponent.create(
                component_id="telegram_pull",
                status="ready",
                summary="telegram pull command rendering model is available without bot delivery",
            ),
            FoundationComponent.create(
                component_id="auto_alerting_decision",
                status="ready",
                summary="auto-alerting decision logic exists without push execution",
            ),
            FoundationComponent.create(
                component_id="container_runtime_adapter",
                status="ready",
                summary="container runtime adapter model exists for fixture data",
            ),
            FoundationComponent.create(
                component_id="ops_readiness",
                status="ready",
                summary="ops readiness checklist model is available",
            ),
            FoundationComponent.create(
                component_id="dashboard_summary",
                status="ready",
                summary="dashboard summary model is available for sanitized status aggregation",
            ),
            FoundationComponent.create(
                component_id="host_agent_runtime",
                status="deferred",
                summary="real host-agent runtime execution stays outside foundation mode",
            ),
            FoundationComponent.create(
                component_id="telegram_delivery",
                status="deferred",
                summary="telegram push or delivery runtime is intentionally not enabled",
            ),
            FoundationComponent.create(
                component_id="ui_integration",
                status="planned",
                summary="ui or route integration is not part of the foundation bundle",
            ),
            FoundationComponent.create(
                component_id="privileged_host_access",
                status="blocked",
                summary="privileged host access remains blocked in foundation mode",
                next_action="keep privileged host access disabled until an explicit operator-approved runtime phase exists",
            ),
            FoundationComponent.create(
                component_id="real_network_io",
                status="blocked",
                summary="real network or token-bearing delivery remains blocked in foundation mode",
                next_action="leave network delivery disabled until operator-owned runtime transport is approved",
            ),
        ),
    )
