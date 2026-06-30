import pytest

from src.podman_readonly_evidence import (
    PodmanReadOnlyEvidenceError,
    build_podman_readonly_evidence_plan,
    summarize_podman_evidence_plan,
)


def test_builds_podman_only_readonly_commands():
    plan = build_podman_readonly_evidence_plan(
        actions=("ps", "logs", "inspect", "port", "health"),
        targets=("odysseus-app", "odysseus-chroma"),
        tail=50,
    )

    payload = plan.to_dict()

    assert payload["runtime"] == "podman"
    assert payload["status"] == "planned"
    assert payload["live_execution_required"] is True
    assert payload["operator_go_required"] is True
    assert all(command["argv"][0] == "podman" for command in payload["commands"])
    assert not any("docker" in command["command_text"] for command in payload["commands"])
    assert not any("restart" in command["argv"] or "exec" in command["argv"] for command in payload["commands"])
    assert any(command["argv"] == ("podman", "ps", "--format", "json") for command in payload["commands"])
    assert any(command["argv"] == ("podman", "logs", "--tail", "50", "odysseus-app") for command in payload["commands"])
    assert any(command["argv"] == ("podman", "inspect", "--format", "{{json .State.Health}}", "odysseus-app") for command in payload["commands"])


def test_logs_and_inspect_require_safe_targets():
    with pytest.raises(PodmanReadOnlyEvidenceError, match="requires at least one target"):
        build_podman_readonly_evidence_plan(actions=("logs",), targets=())

    with pytest.raises(PodmanReadOnlyEvidenceError, match="unsupported characters"):
        build_podman_readonly_evidence_plan(actions=("inspect",), targets=("../../secret",))

    with pytest.raises(PodmanReadOnlyEvidenceError, match="reserved"):
        build_podman_readonly_evidence_plan(actions=("inspect",), targets=("docker",))


def test_rejects_unsupported_or_mutating_actions():
    for action in ("restart", "exec", "compose up", "rm", "docker ps"):
        with pytest.raises(PodmanReadOnlyEvidenceError, match="unsupported action"):
            build_podman_readonly_evidence_plan(actions=(action,), targets=("odysseus-app",))


def test_summarize_plan_is_redacted_and_stable():
    plan = build_podman_readonly_evidence_plan(actions=("ps", "logs"), targets=("odysseus-app",))

    summary = summarize_podman_evidence_plan(plan)

    assert summary == {
        "runtime": "podman",
        "status": "planned",
        "command_count": 2,
        "actions": ("ps", "logs"),
        "mutation_allowed": False,
        "operator_go_required": True,
        "live_execution_required": True,
    }
