import json
from pathlib import Path

import pytest

from src.server_project_provisioner import provision_project_workspace
from src.server_project_registry import ServerProjectRegistry
from src.server_project_repo_provisioner import (
    ProjectRepoCommandResult,
    ServerProjectRepoProvisioningError,
    build_project_repo_provisioning_plan,
    git_command_is_allowed,
    provision_project_local_git_repo,
)


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def _workspace(record, tmp_path: Path) -> None:
    provision_project_workspace(
        record=record,
        projects_root=tmp_path,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )


class FakeGitRunner:
    def __init__(self, *, exit_code: int = 0):
        self.exit_code = exit_code
        self.calls = []

    def __call__(self, argv, *, cwd: Path, timeout_seconds: int, env):
        self.calls.append((tuple(argv), cwd, timeout_seconds, dict(env)))
        if self.exit_code == 0:
            (cwd / ".git").mkdir(exist_ok=True)
        return ProjectRepoCommandResult(exit_code=self.exit_code, stdout="ok" if self.exit_code == 0 else "")


def test_repo_provisioning_plan_blocks_without_go_and_keeps_provider_gated():
    record = _record()

    plan = build_project_repo_provisioning_plan(
        record=record,
        remote_provider="github",
        remote_namespace="fuzzy123-ai",
    )

    assert plan.decision == "hold"
    assert plan.can_execute_local_init is False
    assert "operator decision is not go" in plan.blockers
    assert plan.repo_directory == "projects/kundenportal-mvp/repo"
    assert plan.remote_provider == "github"
    assert "fuzzy123-ai/kundenportal-mvp" in plan.provider_gate
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_repo_provisioning_initializes_local_git_repo_after_workspace_go(tmp_path: Path):
    record = _record()
    _workspace(record, tmp_path)
    runner = FakeGitRunner()

    report = provision_project_local_git_repo(
        record=record,
        projects_root=tmp_path,
        live_enabled=True,
        operator_decision="go",
        command_runner=runner,
    )

    assert report.status == "provisioned"
    assert report.executed is True
    assert report.blockers == ()
    assert runner.calls[0][0] == ("git", "init", "-b", "dev")
    assert runner.calls[0][1] == tmp_path / "kundenportal-mvp" / "repo"
    marker = json.loads((tmp_path / "kundenportal-mvp" / "repo" / ".odysseus-repo.json").read_text(encoding="utf-8"))
    assert marker["project_slug"] == "kundenportal-mvp"
    assert marker["repo_name"] == "kundenportal-mvp"
    assert marker["remote_provider"] == "none"
    assert report.written_files == ("projects/kundenportal-mvp/repo/.odysseus-repo.json",)
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_repo_provisioning_blocks_when_workspace_missing(tmp_path: Path):
    record = _record()
    runner = FakeGitRunner()

    report = provision_project_local_git_repo(
        record=record,
        projects_root=tmp_path,
        live_enabled=True,
        operator_decision="go",
        command_runner=runner,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "run workspace provisioning first" in report.blockers[0]
    assert runner.calls == []


def test_repo_provisioning_reports_git_failure(tmp_path: Path):
    record = _record()
    _workspace(record, tmp_path)

    report = provision_project_local_git_repo(
        record=record,
        projects_root=tmp_path,
        live_enabled=True,
        operator_decision="go",
        command_runner=FakeGitRunner(exit_code=1),
    )

    assert report.status == "failed"
    assert report.executed is True
    assert report.blockers == ("git init failed",)
    assert not (tmp_path / "kundenportal-mvp" / "repo" / ".odysseus-repo.json").exists()


def test_repo_provisioning_rejects_unsafe_provider_and_branch_values():
    record = _record()

    missing_namespace = build_project_repo_provisioning_plan(
        record=record,
        live_enabled=True,
        operator_decision="go",
        remote_provider="github",
    )

    assert missing_namespace.decision == "hold"
    assert missing_namespace.blockers == ("remote_namespace is required for provider repo planning",)

    with pytest.raises(ServerProjectRepoProvisioningError, match="remote_namespace"):
        build_project_repo_provisioning_plan(
            record=record,
            live_enabled=True,
            operator_decision="go",
            remote_provider="github",
            remote_namespace="fuzzy123-ai/team",
        )

    with pytest.raises(ServerProjectRepoProvisioningError, match="branch"):
        build_project_repo_provisioning_plan(
            record=record,
            live_enabled=True,
            operator_decision="go",
            default_branch="../main",
        )


def test_git_command_allowlist_is_tight():
    assert git_command_is_allowed(("git", "init", "-b", "dev")) is True
    assert git_command_is_allowed(("git", "remote", "add", "origin", "https://example.test/repo.git")) is False
    assert git_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_provider_runtime():
    source = Path("src/server_project_repo_provisioner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared", "gh repo create")
    for fragment in forbidden:
        assert fragment not in source
