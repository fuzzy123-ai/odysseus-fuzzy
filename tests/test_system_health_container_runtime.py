from src.system_health_agent_interface import CollectorState, HealthAgentInterfaceError
from src.system_health_container_runtime import (
    ContainerProbeState,
    ContainerRuntimeProbe,
    ContainerRuntimeType,
    build_container_collector_status,
    build_container_health_summary,
    build_container_runtime_status,
)


def test_podman_first_when_both_runtimes_are_detected():
    probe = ContainerRuntimeProbe.create(
        runtime_type="both",
        probe_state="ok",
        detected_runtimes=("docker", "podman"),
        summary="both container runtimes are available",
    )

    status = build_container_runtime_status(probe)

    assert status.runtime_type == ContainerRuntimeType.BOTH
    assert status.primary_runtime == "podman"
    assert status.fallback_runtime == "docker"
    assert status.state == CollectorState.OK


def test_none_runtime_does_not_crash_and_returns_setup_hint():
    probe = ContainerRuntimeProbe.create(
        runtime_type="none",
        probe_state="unavailable",
        summary="no supported container runtime is installed",
    )

    summary = build_container_health_summary(probe)

    assert summary.runtime_status.state == CollectorState.UNSUPPORTED
    assert summary.collector_status.setup_hint
    assert summary.collector_status.state == CollectorState.UNSUPPORTED


def test_permission_denied_is_modeled_as_warn_state():
    probe = ContainerRuntimeProbe.create(
        runtime_type="podman",
        probe_state="permission_denied",
        summary="podman exists but the agent lacks access",
    )

    collector_status = build_container_collector_status(build_container_runtime_status(probe))

    assert collector_status.state == CollectorState.WARN
    assert "permission" in collector_status.setup_hint.lower() or collector_status.setup_hint


def test_command_failed_and_unknown_are_modeled_conservatively():
    failed_probe = ContainerRuntimeProbe.create(
        runtime_type="docker",
        probe_state="command_failed",
        summary="docker command returned a non-zero status",
    )
    unknown_probe = ContainerRuntimeProbe.create(
        runtime_type="unknown",
        probe_state=ContainerProbeState.UNKNOWN,
        summary="host runtime selection is unclear",
    )

    failed_status = build_container_runtime_status(failed_probe)
    unknown_status = build_container_runtime_status(unknown_probe)

    assert failed_status.state == CollectorState.CRITICAL
    assert unknown_status.state == CollectorState.UNKNOWN


def test_to_dict_is_stable():
    probe = ContainerRuntimeProbe.create(
        runtime_type="podman",
        probe_state="ok",
        detected_runtimes=("podman",),
        summary="podman is available",
        command_hint="podman info --format json",
    )

    health_summary = build_container_health_summary(probe)

    assert health_summary.to_dict() == {
        "runtime_status": {
            "runtime_type": "podman",
            "probe_state": "ok",
            "primary_runtime": "podman",
            "fallback_runtime": "",
            "state": "ok",
            "summary": "podman is available",
            "setup_hint": "",
        },
        "collector_status": {
            "collector_id": "container-runtime",
            "state": "ok",
            "summary": "podman is available",
            "observed_value": "podman",
            "setup_hint": "",
        },
    }
    assert probe.to_dict() == {
        "runtime_type": "podman",
        "probe_state": "ok",
        "detected_runtimes": ("podman",),
        "summary": "podman is available",
        "command_hint": "podman info --format json",
        "setup_hint": "",
    }


def test_invalid_runtime_type_is_rejected():
    try:
        ContainerRuntimeProbe.create(
            runtime_type="containerd",
            probe_state="ok",
            summary="unsupported runtime",
        )
    except HealthAgentInterfaceError as exc:
        assert "runtime_type" in str(exc)
    else:
        raise AssertionError("expected HealthAgentInterfaceError")
