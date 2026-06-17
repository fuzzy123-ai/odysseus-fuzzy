"""Container runtime adapter models for host-provided health snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.system_health_agent_interface import CollectorState, CollectorStatus, HealthAgentInterfaceError


class ContainerRuntimeType(StrEnum):
    PODMAN = "podman"
    DOCKER = "docker"
    BOTH = "both"
    NONE = "none"
    UNKNOWN = "unknown"


class ContainerProbeState(StrEnum):
    OK = "ok"
    PERMISSION_DENIED = "permission_denied"
    COMMAND_FAILED = "command_failed"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_runtime_type(value: ContainerRuntimeType | str) -> ContainerRuntimeType:
    if isinstance(value, ContainerRuntimeType):
        return value
    normalized = _normalize_text(value, field_name="runtime_type").lower()
    try:
        return ContainerRuntimeType(normalized)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported runtime_type") from exc


def _normalize_probe_state(value: ContainerProbeState | str) -> ContainerProbeState:
    if isinstance(value, ContainerProbeState):
        return value
    normalized = _normalize_text(value, field_name="probe_state").lower()
    try:
        return ContainerProbeState(normalized)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported probe_state") from exc


def _primary_runtime(runtime_type: ContainerRuntimeType) -> str:
    if runtime_type == ContainerRuntimeType.BOTH:
        return "podman"
    if runtime_type in {ContainerRuntimeType.PODMAN, ContainerRuntimeType.DOCKER}:
        return runtime_type.value
    return ""


def _fallback_runtime(runtime_type: ContainerRuntimeType) -> str:
    if runtime_type == ContainerRuntimeType.BOTH:
        return "docker"
    return ""


def _collector_state_for_probe(probe: "ContainerRuntimeProbe") -> CollectorState:
    if probe.runtime_type == ContainerRuntimeType.NONE:
        return CollectorState.UNSUPPORTED
    if probe.probe_state == ContainerProbeState.OK:
        return CollectorState.OK
    if probe.probe_state == ContainerProbeState.PERMISSION_DENIED:
        return CollectorState.WARN
    if probe.probe_state == ContainerProbeState.COMMAND_FAILED:
        return CollectorState.CRITICAL
    return CollectorState.UNKNOWN


@dataclass(frozen=True, slots=True)
class ContainerRuntimeProbe:
    runtime_type: ContainerRuntimeType
    probe_state: ContainerProbeState
    detected_runtimes: tuple[str, ...]
    summary: str
    command_hint: str
    setup_hint: str

    @classmethod
    def create(
        cls,
        *,
        runtime_type: ContainerRuntimeType | str,
        probe_state: ContainerProbeState | str,
        detected_runtimes: tuple[str, ...] | list[str] | None = None,
        summary: Any,
        command_hint: Any = "",
        setup_hint: Any = "",
    ) -> "ContainerRuntimeProbe":
        normalized_runtime_type = _normalize_runtime_type(runtime_type)
        normalized_probe_state = _normalize_probe_state(probe_state)
        normalized_runtimes = tuple(
            sorted(
                {
                    _normalize_text(item, field_name="detected_runtime").lower()
                    for item in (detected_runtimes or ())
                    if str(item or "").strip()
                }
            )
        )
        if normalized_runtime_type == ContainerRuntimeType.BOTH and normalized_runtimes not in {
            ("docker", "podman"),
            ("podman", "docker"),
        }:
            normalized_runtimes = ("docker", "podman")
        if normalized_runtime_type == ContainerRuntimeType.PODMAN and not normalized_runtimes:
            normalized_runtimes = ("podman",)
        if normalized_runtime_type == ContainerRuntimeType.DOCKER and not normalized_runtimes:
            normalized_runtimes = ("docker",)
        if normalized_runtime_type in {ContainerRuntimeType.NONE, ContainerRuntimeType.UNKNOWN}:
            normalized_runtimes = ()

        normalized_setup_hint = _normalize_text(setup_hint, field_name="setup_hint", allow_empty=True)
        if normalized_runtime_type == ContainerRuntimeType.NONE and not normalized_setup_hint:
            normalized_setup_hint = "install podman or docker on the host, or disable container checks"
        elif normalized_probe_state == ContainerProbeState.PERMISSION_DENIED and not normalized_setup_hint:
            normalized_setup_hint = "grant the health agent permission to inspect the local container runtime"
        elif normalized_probe_state == ContainerProbeState.UNAVAILABLE and not normalized_setup_hint:
            normalized_setup_hint = "verify the selected runtime is installed and reachable on the host"

        return cls(
            runtime_type=normalized_runtime_type,
            probe_state=normalized_probe_state,
            detected_runtimes=normalized_runtimes,
            summary=_normalize_text(summary, field_name="summary"),
            command_hint=_normalize_text(command_hint, field_name="command_hint", allow_empty=True),
            setup_hint=normalized_setup_hint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_type": self.runtime_type.value,
            "probe_state": self.probe_state.value,
            "detected_runtimes": self.detected_runtimes,
            "summary": self.summary,
            "command_hint": self.command_hint,
            "setup_hint": self.setup_hint,
        }


@dataclass(frozen=True, slots=True)
class ContainerRuntimeStatus:
    runtime_type: ContainerRuntimeType
    probe_state: ContainerProbeState
    primary_runtime: str
    fallback_runtime: str
    state: CollectorState
    summary: str
    setup_hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_type": self.runtime_type.value,
            "probe_state": self.probe_state.value,
            "primary_runtime": self.primary_runtime,
            "fallback_runtime": self.fallback_runtime,
            "state": self.state.value,
            "summary": self.summary,
            "setup_hint": self.setup_hint,
        }


@dataclass(frozen=True, slots=True)
class ContainerHealthSummary:
    runtime_status: ContainerRuntimeStatus
    collector_status: CollectorStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_status": self.runtime_status.to_dict(),
            "collector_status": self.collector_status.to_dict(),
        }


def build_container_runtime_status(probe: ContainerRuntimeProbe) -> ContainerRuntimeStatus:
    if not isinstance(probe, ContainerRuntimeProbe):
        raise HealthAgentInterfaceError("probe must be a ContainerRuntimeProbe")
    return ContainerRuntimeStatus(
        runtime_type=probe.runtime_type,
        probe_state=probe.probe_state,
        primary_runtime=_primary_runtime(probe.runtime_type),
        fallback_runtime=_fallback_runtime(probe.runtime_type),
        state=_collector_state_for_probe(probe),
        summary=probe.summary,
        setup_hint=probe.setup_hint,
    )


def build_container_collector_status(status: ContainerRuntimeStatus) -> CollectorStatus:
    if not isinstance(status, ContainerRuntimeStatus):
        raise HealthAgentInterfaceError("status must be a ContainerRuntimeStatus")
    observed_runtime = status.primary_runtime or status.runtime_type.value
    if status.fallback_runtime:
        observed_runtime = f"{status.primary_runtime} (fallback: {status.fallback_runtime})"
    return CollectorStatus.create(
        collector_id="container-runtime",
        state=status.state,
        summary=status.summary,
        observed_value=observed_runtime,
        setup_hint=status.setup_hint,
    )


def build_container_health_summary(probe: ContainerRuntimeProbe) -> ContainerHealthSummary:
    runtime_status = build_container_runtime_status(probe)
    return ContainerHealthSummary(
        runtime_status=runtime_status,
        collector_status=build_container_collector_status(runtime_status),
    )
