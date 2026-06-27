from pathlib import Path

import pytest

from src.server_project_quality_gate import (
    ProjectQualityGateSpec,
    ServerProjectQualityGateError,
    build_project_quality_gate_bundle,
)
from src.server_project_registry import ServerProjectRegistry


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def test_default_project_quality_gates_are_plan_ready_and_dry_run_only():
    bundle = build_project_quality_gate_bundle(record=_record())

    assert bundle.decision == "plan_ready"
    assert bundle.deploy_gate_ready is True
    assert bundle.required_gate_count == 3
    assert bundle.ready_gate_count == 3
    assert bundle.blockers == ()
    assert [result.spec.gate_type for result in bundle.results] == ["test", "build", "smoke"]
    assert all("command_execution" in result.command_plan.blocked_live_actions for result in bundle.results)


def test_custom_focused_pytest_gate_is_plan_ready():
    spec = ProjectQualityGateSpec.create(
        gate_id="unit",
        gate_type="test",
        command_text="python -m pytest tests/test_server_project_quality_gate.py -q",
        timeout_seconds=120,
    )

    bundle = build_project_quality_gate_bundle(record=_record(), gate_specs=(spec,))

    assert bundle.decision == "plan_ready"
    assert bundle.results[0].command_plan.command.command_class == "focused_pytest"


def test_unbounded_or_non_pytest_test_gate_is_blocked():
    spec = ProjectQualityGateSpec.create(
        gate_id="npm_test",
        gate_type="test",
        command_text="npm test",
        timeout_seconds=120,
    )

    bundle = build_project_quality_gate_bundle(record=_record(), gate_specs=(spec,))

    assert bundle.decision == "blocked"
    assert bundle.ready_gate_count == 0
    assert "npm_test" in bundle.blockers[0]


def test_network_host_and_destructive_commands_are_blocked():
    specs = (
        {"gate_id": "network", "gate_type": "evidence", "command_text": "curl https://example.invalid"},
        {"gate_id": "host", "gate_type": "build", "command_text": "podman compose up -d"},
        {"gate_id": "destructive", "gate_type": "evidence", "command_text": "git reset --hard HEAD"},
    )

    bundle = build_project_quality_gate_bundle(record=_record(), gate_specs=specs)

    assert bundle.decision == "blocked"
    assert bundle.ready_gate_count == 0
    assert {result.command_plan.command.command_class for result in bundle.results} == {
        "blocked_network",
        "blocked_host_command",
        "blocked_destructive",
    }


def test_optional_blocked_gate_does_not_block_required_deploy_gate():
    specs = (
        {"gate_id": "unit", "gate_type": "test", "command_text": "python -m pytest tests/test_server_project_quality_gate.py -q"},
        {"gate_id": "optional_network", "gate_type": "evidence", "command_text": "curl https://example.invalid", "required": False},
    )

    bundle = build_project_quality_gate_bundle(record=_record(), gate_specs=specs)

    assert bundle.decision == "plan_ready"
    assert bundle.required_gate_count == 1
    assert bundle.ready_gate_count == 1
    assert bundle.blockers == ()


def test_rejects_secret_like_or_host_path_gate_text():
    with pytest.raises(ServerProjectQualityGateError, match="secret material"):
        ProjectQualityGateSpec.create(
            gate_id="secret",
            gate_type="test",
            command_text="python -m pytest TOKEN=abc123 tests/test_demo.py",
        )

    with pytest.raises(ServerProjectQualityGateError, match="absolute paths"):
        ProjectQualityGateSpec.create(
            gate_id="path",
            gate_type="test",
            command_text=r"python -m pytest D:\Sensitive\test_demo.py",
        )


def test_bundle_requires_at_least_one_gate():
    with pytest.raises(ServerProjectQualityGateError, match="at least one"):
        build_project_quality_gate_bundle(record=_record(), gate_specs=())


def test_source_has_no_live_execution_runtime():
    source = Path("src/server_project_quality_gate.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "shell=True", "os.system")
    for fragment in forbidden:
        assert fragment not in source
