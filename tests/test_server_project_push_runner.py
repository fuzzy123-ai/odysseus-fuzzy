import json
from pathlib import Path

import pytest

from src.server_project_provisioner import provision_project_workspace
from src.server_project_push_runner import (
    ProjectPushCommandResult,
    ServerProjectPushRunnerError,
    build_project_push_plan,
    project_push_command_is_allowed,
    run_project_push,
)
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


class FakePushRunner:
    def __init__(self, *, fail_at: int | None = None):
        self.fail_at = fail_at
        self.calls = []

    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        self.calls.append((tuple(argv), cwd, timeout_seconds, dict(env)))
        index = len(self.calls)
        return ProjectPushCommandResult(
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


def test_push_plan_blocks_without_go_or_confirmed_commit():
    record = _record()

    plan = build_project_push_plan(
        record=record,
        branch="project/kundenportal-mvp/work",
        commit_ref="abc1234",
        commit_confirmed=False,
    )

    assert plan.decision == "hold"
    assert plan.can_push is False
    assert "operator decision is not go" in plan.blockers
    assert "local commit must be confirmed before push" in plan.blockers
    assert plan.remote_name == "fuzzy"
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_push_runner_executes_status_and_push_sequence(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)
    repo_path = tmp_path / "kundenportal-mvp" / "repo"
    runner = FakePushRunner()

    report = run_project_push(
        record=record,
        projects_root=tmp_path,
        branch="project/kundenportal-mvp/work",
        commit_ref="abc1234",
        commit_confirmed=True,
        live_enabled=True,
        operator_decision="go",
        command_runner=runner,
    )

    assert report.status == "pushed"
    assert report.executed is True
    assert report.blockers == ()
    assert report.pushed_ref == "fuzzy/project/kundenportal-mvp/work@abc1234"
    assert [call[0] for call in runner.calls] == [
        ("git", "status", "--short", "--branch"),
        ("git", "push", "fuzzy", "project/kundenportal-mvp/work"),
    ]
    assert runner.calls[0][1] == repo_path
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_push_runner_blocks_when_repo_missing(tmp_path: Path):
    record = _record()
    provision_project_workspace(
        record=record,
        projects_root=tmp_path,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )

    report = run_project_push(
        record=record,
        projects_root=tmp_path,
        branch="project/kundenportal-mvp/work",
        commit_ref="abc1234",
        commit_confirmed=True,
        live_enabled=True,
        operator_decision="go",
        command_runner=FakePushRunner(),
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "run repo provisioning first" in report.blockers[0]


def test_push_runner_reports_push_failure(tmp_path: Path):
    record = _record()
    _workspace_and_repo(record, tmp_path)

    report = run_project_push(
        record=record,
        projects_root=tmp_path,
        branch="project/kundenportal-mvp/work",
        commit_ref="abc1234",
        commit_confirmed=True,
        live_enabled=True,
        operator_decision="go",
        command_runner=FakePushRunner(fail_at=2),
    )

    assert report.status == "failed"
    assert report.executed is True
    assert report.blockers == ("push command failed: git push",)
    assert len(report.command_results) == 2


def test_push_runner_rejects_origin_unsafe_branch_and_bad_commit_ref():
    record = _record()

    with pytest.raises(ServerProjectPushRunnerError, match="origin"):
        build_project_push_plan(
            record=record,
            branch="project/kundenportal-mvp/work",
            commit_ref="abc1234",
            commit_confirmed=True,
            remote_name="origin",
        )

    with pytest.raises(ServerProjectPushRunnerError, match="branch"):
        build_project_push_plan(
            record=record,
            branch="../main",
            commit_ref="abc1234",
            commit_confirmed=True,
        )

    with pytest.raises(ServerProjectPushRunnerError, match="commit_ref"):
        build_project_push_plan(
            record=record,
            branch="project/kundenportal-mvp/work",
            commit_ref="not-a-hash",
            commit_confirmed=True,
        )


def test_push_command_allowlist_is_tight():
    assert project_push_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert project_push_command_is_allowed(("git", "push", "fuzzy", "project/kundenportal-mvp/work")) is True
    with pytest.raises(ServerProjectPushRunnerError, match="origin"):
        project_push_command_is_allowed(("git", "push", "origin", "project/kundenportal-mvp/work"))
    assert project_push_command_is_allowed(("git", "push", "--force", "fuzzy", "dev")) is False
    assert project_push_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_provider_runtime():
    source = Path("src/server_project_push_runner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared", "force-push")
    for fragment in forbidden:
        assert fragment not in source
