import json
from pathlib import Path

import pytest

from src.server_project_commit_runner import (
    ProjectCommitCommandResult,
    ServerProjectCommitRunnerError,
    build_project_commit_plan,
    project_commit_command_is_allowed,
    run_project_local_commit,
)
from src.server_project_provisioner import provision_project_workspace
from src.server_project_registry import ServerProjectRegistry
from src.server_project_repo_provisioner import ProjectRepoCommandResult, provision_project_local_git_repo


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


class FakeGitInitRunner:
    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        (cwd / ".git").mkdir(exist_ok=True)
        return ProjectRepoCommandResult(exit_code=0, stdout="git init ok")


class FakeCommitRunner:
    def __init__(self, *, fail_at: int | None = None):
        self.fail_at = fail_at
        self.calls = []

    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        self.calls.append((tuple(argv), cwd, timeout_seconds, dict(env)))
        index = len(self.calls)
        return ProjectCommitCommandResult(
            exit_code=1 if self.fail_at == index else 0,
            stdout="ok",
            stderr="failed" if self.fail_at == index else "",
        )


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
        command_runner=FakeGitInitRunner(),
    )


def test_commit_plan_blocks_without_go_or_green_checks():
    record = _record()

    plan = build_project_commit_plan(
        record=record,
        objective="Add app entrypoint",
        changed_paths=("src/app.py",),
        checks_passed=False,
    )

    assert plan.decision == "hold"
    assert plan.can_commit is False
    assert "operator decision is not go" in plan.blockers
    assert "task checks must be green before commit" in plan.blockers
    assert plan.commit_message == "feat: add app entrypoint"
    assert plan.push_gate.startswith("Push is not executed")
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_commit_runner_executes_status_add_commit_sequence(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)
    repo_path = tmp_path / "kundenportal-mvp" / "repo"
    (repo_path / "src").mkdir()
    (repo_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    runner = FakeCommitRunner()

    report = run_project_local_commit(
        record=record,
        projects_root=tmp_path,
        objective="Add app entrypoint",
        changed_paths=("src/app.py",),
        checks_passed=True,
        live_enabled=True,
        operator_decision="go",
        command_runner=runner,
    )

    assert report.status == "committed"
    assert report.executed is True
    assert report.blockers == ()
    assert report.committed_paths == ("projects/kundenportal-mvp/repo/src/app.py",)
    assert [call[0] for call in runner.calls] == [
        ("git", "status", "--short", "--branch"),
        ("git", "add", "--", "src/app.py"),
        ("git", "commit", "-m", "feat: add app entrypoint"),
    ]
    assert runner.calls[0][1] == repo_path
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_commit_runner_blocks_when_repo_missing(tmp_path: Path):
    record = _record()
    provision_project_workspace(
        record=record,
        projects_root=tmp_path,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )

    report = run_project_local_commit(
        record=record,
        projects_root=tmp_path,
        objective="Add app entrypoint",
        changed_paths=("src/app.py",),
        checks_passed=True,
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeCommitRunner(),
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "run repo provisioning first" in report.blockers[0]


def test_commit_runner_reports_git_failure(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)

    report = run_project_local_commit(
        record=record,
        projects_root=tmp_path,
        objective="Add app entrypoint",
        changed_paths=("src/app.py",),
        checks_passed=True,
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeCommitRunner(fail_at=2),
    )

    assert report.status == "failed"
    assert report.executed is True
    assert report.blockers == ("commit command failed: git add",)
    assert len(report.command_results) == 2


def test_commit_runner_rejects_unsafe_paths_messages_and_origin():
    record = _record()

    with pytest.raises(ServerProjectCommitRunnerError, match="repo-relative"):
        build_project_commit_plan(
            record=record,
            objective="Add app",
            changed_paths=(r"C:\tmp\app.py",),
            checks_passed=True,
        )

    with pytest.raises(ServerProjectCommitRunnerError, match="blocked"):
        build_project_commit_plan(
            record=record,
            objective="Add app",
            changed_paths=(".env",),
            checks_passed=True,
        )

    with pytest.raises(ServerProjectCommitRunnerError, match="secret"):
        build_project_commit_plan(
            record=record,
            objective="Add app",
            changed_paths=("src/app.py",),
            checks_passed=True,
            commit_message="feat: TOKEN=abc123",
        )

    origin_plan = build_project_commit_plan(
        record=record,
        objective="Add app",
        changed_paths=("src/app.py",),
        checks_passed=True,
        push_remote="origin",
        live_enabled=True,
        operator_decision="go",
    )

    assert origin_plan.decision == "blocked"
    assert "origin is blocked" in origin_plan.blockers[0]


def test_commit_command_allowlist_is_tight():
    assert project_commit_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert project_commit_command_is_allowed(("git", "add", "--", "src/app.py")) is True
    assert project_commit_command_is_allowed(("git", "commit", "-m", "feat: add app")) is True
    assert project_commit_command_is_allowed(("git", "add", "--", r"C:\tmp\app.py")) is False
    assert project_commit_command_is_allowed(("git", "reset", "--hard")) is False
    assert project_commit_command_is_allowed(("git", "push", "origin", "dev")) is False


def test_source_uses_shell_false_and_no_push_runtime():
    source = Path("src/server_project_commit_runner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared", "git push", "force")
    for fragment in forbidden:
        assert fragment not in source
