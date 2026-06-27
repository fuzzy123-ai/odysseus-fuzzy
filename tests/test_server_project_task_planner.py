import json
from pathlib import Path

import pytest

from src.server_project_provisioner import provision_project_workspace
from src.server_project_registry import ServerProjectRegistry
from src.server_project_repo_provisioner import ProjectRepoCommandResult, provision_project_local_git_repo
from src.server_project_task_planner import (
    ServerProjectTaskPlannerError,
    build_planner_task_bundle,
    run_planner_task,
)
from src.server_project_task_runner import ServerProjectTaskRunnerError
from src.server_project_task_runner import ProjectTaskCommandResult


def _record(project_type: str = "app"):
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type=project_type,
        created_at="2026-06-27T10:00:00Z",
    )


class FakeGitRunner:
    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        (cwd / ".git").mkdir(exist_ok=True)
        return ProjectRepoCommandResult(exit_code=0, stdout="git ok")


class FakeTaskRunner:
    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        return ProjectTaskCommandResult(exit_code=0, stdout="task ok")


def _workspace_and_repo(record, tmp_path: Path) -> None:
    provision_project_workspace(
        record=record,
        projects_root=tmp_path,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )
    provision_project_local_git_repo(
        record=record,
        projects_root=tmp_path,
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeGitRunner(),
    )


def test_planner_bundle_converts_structured_output_to_task_plan():
    record = _record()

    bundle = build_planner_task_bundle(
        record=record,
        objective="Add a small app entrypoint",
        file_writes=[{"path": "src/app.py", "content": "print('hi')\n"}],
        acceptance_criteria=["app entrypoint exists"],
        live_enabled=True,
        operator_decision="go",
    )

    assert bundle.ready_for_task_runner is True
    assert bundle.planner_blockers == ()
    assert bundle.task_plan.can_execute is True
    assert bundle.task_plan.file_writes[0].path == "src/app.py"
    assert bundle.task_plan.checks[0].argv == ("python", "-m", "pytest", "tests", "-q")
    assert "C:\\" not in json.dumps(bundle.to_dict())


def test_planner_bundle_adds_node_check_for_javascript_file():
    record = _record(project_type="website")

    bundle = build_planner_task_bundle(
        record=record,
        objective="Add browser script",
        file_writes=[{"path": "src/app.js", "content": "console.log('hi');\n"}],
        acceptance_criteria=["script parses"],
        check_profile="auto",
    )

    assert ("node", "--check", "src/app.js") in [check.argv for check in bundle.task_plan.checks]
    assert ("git", "status", "--short", "--branch") in [check.argv for check in bundle.task_plan.checks]


def test_planner_bundle_blocks_incomplete_planner_output():
    record = _record()

    bundle = build_planner_task_bundle(
        record=record,
        objective="Add a small app entrypoint",
        file_writes=[],
        checks=[],
        acceptance_criteria=[],
        check_profile="none",
    )

    assert bundle.ready_for_task_runner is False
    assert "planner output must include at least one file write" in bundle.planner_blockers
    assert "planner output must include checks or a default check profile" in bundle.planner_blockers
    assert "planner output should include acceptance criteria" in bundle.planner_blockers


def test_run_planner_task_executes_through_task_runner_with_universal_check(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)

    report = run_planner_task(
        record=record,
        projects_root=tmp_path,
        objective="Add docs note",
        file_writes=[{"path": "docs/note.md", "content": "# Note\n"}],
        checks=[{"argv": ["git", "status", "--short", "--branch"]}],
        acceptance_criteria=["note exists"],
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeTaskRunner(),
    )

    assert report.status == "completed"
    assert report.executed is True
    assert report.task_report is not None
    assert report.task_report.written_files == ("projects/kundenportal-mvp/repo/docs/note.md",)
    assert (tmp_path / "kundenportal-mvp" / "repo" / "docs" / "note.md").is_file()
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_run_planner_task_does_not_execute_when_planner_has_blockers(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)

    report = run_planner_task(
        record=record,
        projects_root=tmp_path,
        objective="Add docs note",
        file_writes=[],
        checks=[{"argv": ["git", "status", "--short", "--branch"]}],
        acceptance_criteria=[],
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert report.task_report is None
    assert report.bundle.planner_blockers


def test_planner_adapter_rejects_secret_paths_and_unsupported_profiles():
    record = _record()

    with pytest.raises(ServerProjectTaskPlannerError, match="check_profile"):
        build_planner_task_bundle(
            record=record,
            objective="Add app",
            file_writes=[{"path": "src/app.py", "content": "print('hi')\n"}],
            acceptance_criteria=["ok"],
            check_profile="curl",
        )

    with pytest.raises(ServerProjectTaskRunnerError, match="secret"):
        build_planner_task_bundle(
            record=record,
            objective="Add app",
            file_writes=[{"path": "src/app.py", "content": "TOKEN=abc123"}],
            acceptance_criteria=["ok"],
        )

    with pytest.raises(ServerProjectTaskRunnerError, match="repo-relative"):
        build_planner_task_bundle(
            record=record,
            objective="Add app",
            file_writes=[{"path": r"C:\tmp\app.py", "content": "x"}],
            acceptance_criteria=["ok"],
        )


def test_source_has_no_live_provider_runtime():
    source = Path("src/server_project_task_planner.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "cloudflared", "shell=True")
    for fragment in forbidden:
        assert fragment not in source
