import json
from pathlib import Path

import pytest

from src.server_project_provisioner import provision_project_workspace
from src.server_project_registry import ServerProjectRegistry
from src.server_project_repo_provisioner import ProjectRepoCommandResult, provision_project_local_git_repo
from src.server_project_task_runner import (
    ProjectTaskCheck,
    ProjectTaskCommandResult,
    ProjectTaskFileWrite,
    ServerProjectTaskRunnerError,
    build_project_task_plan,
    project_task_command_is_allowed,
    run_project_task,
)


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


class FakeGitRunner:
    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        (cwd / ".git").mkdir(exist_ok=True)
        return ProjectRepoCommandResult(exit_code=0, stdout="git ok")


class FakeTaskRunner:
    def __init__(self, *, exit_code: int = 0):
        self.exit_code = exit_code
        self.calls = []

    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        self.calls.append((tuple(argv), cwd, timeout_seconds, dict(env)))
        return ProjectTaskCommandResult(exit_code=self.exit_code, stdout="check ok")


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


def _write(path: str = "src/app.py", content: str = "print('hello')\n") -> ProjectTaskFileWrite:
    return ProjectTaskFileWrite.create(path=path, content=content)


def _check() -> ProjectTaskCheck:
    return ProjectTaskCheck.create(argv=("python", "-m", "pytest", "tests", "-q"))


def test_task_plan_blocks_without_live_go():
    record = _record()

    plan = build_project_task_plan(
        record=record,
        objective="Add a small app entrypoint",
        file_writes=(_write(),),
        checks=(_check(),),
    )

    assert plan.decision == "hold"
    assert plan.can_execute is False
    assert "operator decision is not go" in plan.blockers
    assert plan.repo_directory == "projects/kundenportal-mvp/repo"
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_task_runner_writes_files_and_runs_checks_in_project_repo(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)
    runner = FakeTaskRunner()

    report = run_project_task(
        record=record,
        projects_root=tmp_path,
        objective="Add a small app entrypoint",
        file_writes=(_write("src/app.py", "print('hi')\n"),),
        checks=(_check(),),
        live_enabled=True,
        operator_decision="go",
        command_runner=runner,
    )

    assert report.status == "completed"
    assert report.executed is True
    assert report.blockers == ()
    assert report.written_files == ("projects/kundenportal-mvp/repo/src/app.py",)
    assert (tmp_path / "kundenportal-mvp" / "repo" / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert runner.calls[0][0] == ("python", "-m", "pytest", "tests", "-q")
    assert runner.calls[0][1] == tmp_path / "kundenportal-mvp" / "repo"
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_task_runner_blocks_when_local_repo_is_missing(tmp_path: Path):
    record = _record()
    provision_project_workspace(
        record=record,
        projects_root=tmp_path,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )

    report = run_project_task(
        record=record,
        projects_root=tmp_path,
        objective="Add a small app entrypoint",
        file_writes=(_write(),),
        checks=(_check(),),
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeTaskRunner(),
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "run repo provisioning first" in report.blockers[0]


def test_task_runner_reports_check_failure_after_writes(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)

    report = run_project_task(
        record=record,
        projects_root=tmp_path,
        objective="Add a small app entrypoint",
        file_writes=(_write(),),
        checks=(_check(),),
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeTaskRunner(exit_code=1),
    )

    assert report.status == "failed"
    assert report.executed is True
    assert report.check_reports[0].status == "failed"
    assert "task check failed" in report.blockers[0]


def test_task_runner_rejects_unsafe_paths_secrets_and_commands():
    with pytest.raises(ServerProjectTaskRunnerError, match="repo-relative"):
        ProjectTaskFileWrite.create(path=r"C:\tmp\app.py", content="x")

    with pytest.raises(ServerProjectTaskRunnerError, match="blocked"):
        ProjectTaskFileWrite.create(path=".env", content="DEBUG=1")

    with pytest.raises(ServerProjectTaskRunnerError, match="secret"):
        ProjectTaskFileWrite.create(path="src/app.py", content="TOKEN=abc123")

    with pytest.raises(ServerProjectTaskRunnerError, match="not allowed"):
        ProjectTaskCheck.create(argv=("git", "reset", "--hard"))


def test_task_command_allowlist_is_tight():
    assert project_task_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert project_task_command_is_allowed(("python", "-m", "pytest", "tests", "-q")) is True
    assert project_task_command_is_allowed(("npm", "run", "build")) is True
    assert project_task_command_is_allowed(("node", "--check", "src/app.js")) is True
    assert project_task_command_is_allowed(("python", "-m", "pytest", r"C:\tmp\tests")) is False
    assert project_task_command_is_allowed(("curl", "https://example.test")) is False


def test_source_uses_shell_false_and_no_provider_runtime():
    source = Path("src/server_project_task_runner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared", "gh repo create")
    for fragment in forbidden:
        assert fragment not in source
