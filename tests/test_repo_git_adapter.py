from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.repo_git_adapter import (
    RepoGitAdapter,
    RepoGitAdapterError,
    RepoGitCommandResult,
    git_read_command_is_allowed,
)
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", "https://x-access-token:secret-value@github.com/fuzzy123-ai/demo.git")
    readme.write_text("two\n", encoding="utf-8")
    return repo


def _registry() -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner="fuzzy123-ai",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            remotes=[
                RepoRemote.create(
                    name="origin",
                    url="https://github.com/fuzzy123-ai/demo.git",
                    purpose="origin",
                )
            ],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


def test_adapter_reads_status_log_changed_paths_and_redacted_remotes(tmp_path: Path):
    repo = _make_repo(tmp_path)
    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo})

    snapshot = adapter.snapshot("demo")
    dumped = json.dumps(snapshot.to_dict())

    assert snapshot.current_branch == "main"
    assert snapshot.status.dirty is True
    assert any("README.md" in entry for entry in snapshot.status.entries)
    assert snapshot.commits[0].subject == "initial commit"
    assert len(snapshot.changed_paths) == 1
    assert snapshot.changed_paths[0].path == "README.md"
    assert snapshot.remotes[0].url_redacted == "https://github.com/fuzzy123-ai/demo.git"
    assert "secret-value" not in dumped
    assert "x-access-token" not in dumped
    assert str(tmp_path) not in dumped


def test_adapter_rejects_unknown_repo(tmp_path: Path):
    repo = _make_repo(tmp_path)
    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo})

    with pytest.raises(RepoGitAdapterError, match="unknown repo"):
        adapter.status("missing")


def test_adapter_rejects_repo_root_outside_allowed_workspace(tmp_path: Path):
    repo = _make_repo(tmp_path / "outside")
    allowed_workspace = tmp_path / "allowed"
    allowed_workspace.mkdir()
    adapter = RepoGitAdapter(
        registry=_registry(),
        repo_roots={"demo": repo},
        workspace_base=allowed_workspace,
    )

    with pytest.raises(RepoGitAdapterError, match="outside the allowed workspace"):
        adapter.status("demo")


def test_adapter_requires_local_git_repository(tmp_path: Path):
    repo = tmp_path / "plain-folder"
    repo.mkdir()
    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo})

    with pytest.raises(RepoGitAdapterError, match="local Git repository"):
        adapter.status("demo")


def test_adapter_bounds_and_redacts_command_output(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def fake_runner(argv, *, cwd: Path, timeout_seconds: int, env):
        return RepoGitCommandResult(
            exit_code=0,
            stdout="## main\n M C:/Users/nkatz/secret.txt password=supersecret\n" + ("x" * 20_000),
            stderr="token=abc123",
        )

    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo}, command_runner=fake_runner)
    status = adapter.status("demo")

    dumped = json.dumps(status.to_dict())
    assert "C:/Users" not in dumped
    assert "supersecret" not in dumped
    assert len(dumped) < 11_000


def test_git_read_command_allowlist_is_tight():
    assert git_read_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert git_read_command_is_allowed(("git", "branch", "--show-current")) is True
    assert git_read_command_is_allowed(
        ("git", "log", "--max-count", "10", "--date=iso", "--pretty=format:%H%x09%ad%x09%an%x09%s")
    ) is True
    assert git_read_command_is_allowed(("git", "diff", "--name-status")) is True
    assert git_read_command_is_allowed(("git", "diff", "--stat")) is True
    assert git_read_command_is_allowed(("git", "remote", "-v")) is True
    assert git_read_command_is_allowed(("git", "log", "--max-count", "101", "--date=iso", "--pretty=format:%H")) is False
    assert git_read_command_is_allowed(("git", "add", ".")) is False
    assert git_read_command_is_allowed(("git", "commit", "-m", "x")) is False
    assert git_read_command_is_allowed(("git", "push", "origin", "main")) is False
    assert git_read_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_write_runtime():
    source = Path("src/repo_git_adapter.py").read_text(encoding="utf-8").lower()

    assert "subprocess.run(" in source
    assert "shell=false" in source
    forbidden = ("git add", "git commit", "git push", "reset --hard", "force-push")
    for fragment in forbidden:
        assert fragment not in source
