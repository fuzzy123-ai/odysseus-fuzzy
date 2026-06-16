import pytest

from plugins.system_health_checker.health_model import HealthModelError, HealthState
from plugins.system_health_checker.runtime_adapter import (
    RuntimeAvailability,
    RuntimeKind,
    choose_runtime,
    container_runtime_status,
    normalize_available_binaries,
)


OBSERVED_AT = "2026-06-16T12:00:00Z"


def test_podman_is_preferred_when_both_runtimes_exist():
    plan = choose_runtime(RuntimeAvailability(podman_available=True, docker_available=True))

    assert plan.kind == RuntimeKind.PODMAN
    assert plan.command[0] == "podman"
    assert plan.socket_required is False
    assert "rootless" in plan.setup_hint.lower()


def test_docker_is_fallback_without_socket_requirement():
    plan = choose_runtime(RuntimeAvailability(podman_available=False, docker_available=True))

    assert plan.kind == RuntimeKind.DOCKER
    assert plan.command[0] == "docker"
    assert plan.socket_required is False
    assert "not a mounted Odysseus socket" in plan.setup_hint


def test_no_runtime_returns_setup_hint():
    plan = choose_runtime(RuntimeAvailability(podman_available=False, docker_available=False))

    assert plan.kind == RuntimeKind.NONE
    assert plan.command == ()
    assert "Install Podman or Docker" in plan.setup_hint


def test_container_runtime_status_is_ok_when_runtime_exists():
    status = container_runtime_status(
        availability=RuntimeAvailability(podman_available=True, docker_available=False),
        observed_at=OBSERVED_AT,
    )

    assert status.state == HealthState.OK
    assert status.details["runtime"] == "podman"
    assert status.details["socket_required"] is False


def test_container_runtime_status_is_unknown_when_no_runtime_exists():
    status = container_runtime_status(
        availability=RuntimeAvailability(podman_available=False, docker_available=False),
        observed_at=OBSERVED_AT,
    )

    assert status.state == HealthState.UNKNOWN
    assert status.details["runtime"] == "none"
    assert "setup_hint" in status.details


def test_normalize_available_binaries_detects_runtime_names():
    availability = normalize_available_binaries(["python", "podman", "docker"])

    assert availability.podman_available is True
    assert availability.docker_available is True
    assert availability.rootless_preferred is True


def test_choose_runtime_requires_availability_model():
    with pytest.raises(HealthModelError, match="availability must be RuntimeAvailability"):
        choose_runtime("podman")
