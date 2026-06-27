from pathlib import Path

import pytest

from src.server_project_deploy_handoff import build_project_deploy_handoff
from src.server_project_executor import (
    ProjectCommandResult,
    ProjectExecutionStep,
    build_default_project_execution_steps,
    execute_project_handoff,
    project_command_is_allowed,
)
from src.server_project_git_review import build_project_git_review_plan
from src.server_project_quality_gate import build_project_quality_gate_bundle
from src.server_project_registry import ServerProjectRegistry


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def _green_backup_evidence():
    return (
        {"evidence_id": "pre_update_snapshot", "state": "green", "result_label": "pass", "checked_at": "2026-06-27T10:00:00Z", "summary": "green"},
        {"evidence_id": "repository_check", "state": "green", "result_label": "pass", "checked_at": "2026-06-27T10:01:00Z", "summary": "green"},
        {"evidence_id": "restore_smoke", "state": "green", "result_label": "pass", "checked_at": "2026-06-27T10:02:00Z", "summary": "green"},
    )


def _ready_handoff():
    record = _record()
    return build_project_deploy_handoff(
        record=record,
        quality_bundle=build_project_quality_gate_bundle(record=record),
        git_review_plan=build_project_git_review_plan(record=record, changed_paths=("src/app.py",), operator_decision="go"),
        backup_evidence_inputs=_green_backup_evidence(),
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )


def test_default_execution_steps_are_whitelisted():
    steps = build_default_project_execution_steps(smoke_tests=("tests/test_server_project_executor.py",))

    assert [step.step_id for step in steps] == [
        "pre_update_snapshot",
        "update_version_metadata_env",
        "podman_compose_up",
        "smoke_test_1",
    ]
    assert all(project_command_is_allowed(step.argv) for step in steps)


def test_executor_blocks_without_live_flag_and_operator_go():
    calls = []

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append(argv)
        return ProjectCommandResult(exit_code=0)

    report = execute_project_handoff(
        handoff=_ready_handoff(),
        steps=(ProjectExecutionStep.create(step_id="status", argv=("git", "status", "--short", "--branch"), summary="status"),),
        command_runner=runner,
        live_enabled=False,
        operator_decision="hold",
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert calls == []
    assert "ODYSSEUS_PROJECT_EXECUTOR_LIVE_ENABLED is not enabled" in report.blockers
    assert "operator decision is not go" in report.blockers


def test_executor_runs_whitelisted_steps_with_fake_runner(tmp_path: Path):
    calls = []
    steps = (
        ProjectExecutionStep.create(step_id="status", argv=("git", "status", "--short", "--branch"), summary="status"),
        ProjectExecutionStep.create(step_id="tests", argv=("python", "-m", "pytest", "tests/test_server_project_executor.py", "-q"), summary="tests"),
    )

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append((argv, cwd, timeout_seconds, dict(env)))
        return ProjectCommandResult(exit_code=0, stdout="ok")

    report = execute_project_handoff(
        handoff=_ready_handoff(),
        steps=steps,
        cwd=tmp_path,
        command_runner=runner,
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "completed"
    assert report.succeeded is True
    assert report.executed is True
    assert [call[0] for call in calls] == [steps[0].argv, steps[1].argv]
    assert all(call[1] == tmp_path.resolve() for call in calls)


def test_executor_rejects_unwhitelisted_commands_before_running():
    def runner(argv, *, cwd, timeout_seconds, env):  # pragma: no cover
        raise AssertionError("unsafe command must not run")

    report = execute_project_handoff(
        handoff=_ready_handoff(),
        steps=(ProjectExecutionStep.create(step_id="danger", argv=("rm", "-rf", "/"), summary="danger"),),
        command_runner=runner,
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "step danger is not in the project executor command whitelist" in report.blockers


def test_executor_stops_on_first_failed_step():
    calls = []
    steps = (
        ProjectExecutionStep.create(step_id="status", argv=("git", "status", "--short", "--branch"), summary="status"),
        ProjectExecutionStep.create(step_id="tests", argv=("python", "-m", "pytest", "tests/test_server_project_executor.py", "-q"), summary="tests"),
    )

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append(argv)
        return ProjectCommandResult(exit_code=1, stderr="failed")

    report = execute_project_handoff(
        handoff=_ready_handoff(),
        steps=steps,
        command_runner=runner,
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "failed"
    assert calls == [steps[0].argv]
    assert report.blockers == ("step status failed",)


def test_result_redacts_secret_like_output():
    result = ProjectCommandResult(exit_code=1, stderr="Bearer abc123")

    assert result.to_dict()["stderr"] == "[redacted]"


def test_step_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="timeout_seconds"):
        ProjectExecutionStep.create(step_id="bad", argv=("git", "status", "--short", "--branch"), summary="bad", timeout_seconds=0)


def test_source_uses_bounded_subprocess_without_shell():
    source = Path("src/server_project_executor.py").read_text(encoding="utf-8")

    assert "subprocess.run(" in source
    assert "shell=False" in source
    assert "timeout=timeout_seconds" in source
    forbidden = ("shell=True", "os.system", "requests", "httpx", "paramiko")
    for fragment in forbidden:
        assert fragment not in source
