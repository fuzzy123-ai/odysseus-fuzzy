"""Container runtime adapter planning for System Health Checker.

The adapter plans Podman/Docker commands but does not execute them and does not
require sockets inside the Odysseus container.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from .health_model import CollectorStatus, HealthModelError, HealthState


class RuntimeKind(StrEnum):
    PODMAN = "podman"
    DOCKER = "docker"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class RuntimeAvailability:
    podman_available: bool
    docker_available: bool
    rootless_preferred: bool = True


@dataclass(frozen=True, slots=True)
class ContainerRuntimePlan:
    kind: RuntimeKind
    command: tuple[str, ...]
    socket_required: bool
    setup_hint: str


def choose_runtime(availability: RuntimeAvailability) -> ContainerRuntimePlan:
    if not isinstance(availability, RuntimeAvailability):
        raise HealthModelError("availability must be RuntimeAvailability")
    if availability.podman_available:
        return ContainerRuntimePlan(
            kind=RuntimeKind.PODMAN,
            command=("podman", "ps", "--format", "json"),
            socket_required=False,
            setup_hint="Using Podman CLI; rootless mode is preferred.",
        )
    if availability.docker_available:
        return ContainerRuntimePlan(
            kind=RuntimeKind.DOCKER,
            command=("docker", "ps", "--format", "json"),
            socket_required=False,
            setup_hint="Using Docker CLI fallback from the host-agent, not a mounted Odysseus socket.",
        )
    return ContainerRuntimePlan(
        kind=RuntimeKind.NONE,
        command=(),
        socket_required=False,
        setup_hint="Install Podman or Docker on the Debian host-agent machine.",
    )


def container_runtime_status(*, availability: RuntimeAvailability, observed_at: Any) -> CollectorStatus:
    plan = choose_runtime(availability)
    if plan.kind == RuntimeKind.NONE:
        return CollectorStatus.create(
            kind="containers",
            state=HealthState.UNKNOWN,
            summary="No container runtime detected",
            observed_at=observed_at,
            details={"runtime": plan.kind.value, "setup_hint": plan.setup_hint},
        )
    return CollectorStatus.create(
        kind="containers",
        state=HealthState.OK,
        summary=f"{plan.kind.value} runtime detected",
        observed_at=observed_at,
        details={
            "runtime": plan.kind.value,
            "command": list(plan.command),
            "socket_required": plan.socket_required,
            "setup_hint": plan.setup_hint,
        },
    )


def normalize_available_binaries(binaries: Iterable[Any]) -> RuntimeAvailability:
    names = {str(binary or "").strip().lower() for binary in binaries}
    return RuntimeAvailability(
        podman_available="podman" in names,
        docker_available="docker" in names,
        rootless_preferred=True,
    )
