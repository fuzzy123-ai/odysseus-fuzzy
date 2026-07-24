from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.repo_commit_runner import (
    RepoCommitCommandResult,
    RepoCommitRunnerError,
    build_repo_commit_plan,
    repo_commit_command_is_allowed,
    run_git_commit_subprocess_command,
    run_repo_local_commit,
)
from src.repo_registry import RepoRecord, RepoRegistry


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout


def _make_repo(base: Path) -> Path:
    repo = base / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
    (repo / "README.md").write_text("two\n", encoding="utf-8")
    return repo


def _registry(*, allowed_actions=None) -> RepoRegistry:
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
            allowed_actions=allowed_actions
            or ["status", "changed_paths", "commit_plan", "commit"],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


def test_commit_plan_blocks_without_gates(tmp_path: Path):
    repo = _make_repo(tmp_path)
    status = _git(repo, "status", "--short", "--branch")

    plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=False,
        content_reviewed=False,
        confirmed=False,
        status_output=status,
    )

    assert plan.decision == "hold"
    assert plan.can_commit is False
    assert "confirmed=true is required" in plan.blockers[0]
    assert "checks_passed=true" in plan.blockers[1]
    assert "content_reviewed=true" in plan.blockers[2]
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_commit_runner_commits_only_exact_reviewed_path(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "NOTES.md").write_text("leave dirty\n", encoding="utf-8")

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
    )

    assert report.status == "committed"
    assert report.executed is True
    assert report.committed_paths == ("repos/demo/README.md",)
    assert report.commit_sha == _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "chore: update readme"
    status = _git(repo, "status", "--short")
    assert "README.md" not in status
    assert "NOTES.md" in status
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_commit_runner_blocks_preexisting_staging(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "NOTES.md").write_text("staged elsewhere\n", encoding="utf-8")
    _git(repo, "add", "NOTES.md")

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert any("pre-existing staged changes" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_requires_repo_policy_and_reviewed_content(tmp_path: Path):
    _make_repo(tmp_path)

    report = run_repo_local_commit(
        registry=_registry(allowed_actions=["status"]),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
    )

    assert report.status == "blocked"
    assert "allowed_actions does not include commit" in report.blockers[0]


def test_commit_runner_rejects_unsafe_paths_messages_and_commands(tmp_path: Path):
    repo = _make_repo(tmp_path)
    status = _git(repo, "status", "--short", "--branch")

    with pytest.raises(RepoCommitRunnerError, match="repo-relative"):
        build_repo_commit_plan(
            record=_registry().get("demo"),
            repo_path=repo,
            objective="Update readme",
            changed_paths=(r"C:\tmp\README.md",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
            status_output=status,
        )

    with pytest.raises(RepoCommitRunnerError, match="blocked repo path"):
        build_repo_commit_plan(
            record=_registry().get("demo"),
            repo_path=repo,
            objective="Update env",
            changed_paths=(".env",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
            status_output=status,
        )

    with pytest.raises(RepoCommitRunnerError, match="secret"):
        build_repo_commit_plan(
            record=_registry().get("demo"),
            repo_path=repo,
            objective="Update readme",
            changed_paths=("README.md",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
            commit_message="chore: TOKEN=abc123",
            status_output=status,
        )

    assert repo_commit_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert repo_commit_command_is_allowed(("git", "add", "--", "README.md")) is True
    assert repo_commit_command_is_allowed(("git", "commit", "-m", "chore: update readme")) is True
    assert repo_commit_command_is_allowed(
        ("git", "commit", "-m", "chore: update readme", "-m", "Readable\nbody")
    ) is True
    assert repo_commit_command_is_allowed(("git", "diff", "--cached", "--name-only", "-z")) is True
    assert repo_commit_command_is_allowed(("git", "rev-parse", "--verify", "HEAD^{commit}")) is True
    assert repo_commit_command_is_allowed(
        ("git", "commit", "-m", "chore: update", "-m", "TOKEN=abc123")
    ) is False
    assert repo_commit_command_is_allowed(("git", "commit", "--amend", "-m", "unsafe")) is False
    assert repo_commit_command_is_allowed(("git", "commit", "-a", "-m", "unsafe")) is False
    assert repo_commit_command_is_allowed(("git", "commit", "-F", "message.txt")) is False
    assert repo_commit_command_is_allowed(("git", "commit", "--no-verify", "-m", "unsafe")) is False
    assert repo_commit_command_is_allowed(("git", "add", "--", r"C:\tmp\README.md")) is False
    assert repo_commit_command_is_allowed(("git", "push", "fuzzy", "dev")) is False
    assert repo_commit_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_remote_mutation():
    source = Path("src/repo_commit_runner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared", "git push", "force")
    for fragment in forbidden:
        assert fragment not in source


def test_commit_runner_writes_exact_multiline_body(tmp_path: Path):
    repo = _make_repo(tmp_path)

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        commit_message="feat: described update",
        commit_body="A useful description.\n\nChange notes:\n- First note\n- Second note",
    )

    assert report.status == "committed"
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "feat: described update"
    assert _git(repo, "log", "-1", "--pretty=%b").strip() == (
        "A useful description.\n\nChange notes:\n- First note\n- Second note"
    )


@pytest.mark.parametrize("gate", ["checks_passed", "content_reviewed", "confirmed"])
def test_commit_runner_never_accepts_string_boolean_gates(tmp_path: Path, gate: str):
    repo = _make_repo(tmp_path)
    values = {"checks_passed": True, "content_reviewed": True, "confirmed": True}
    values[gate] = "true"

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        **values,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert any(f"{gate} must be a boolean" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_blocks_clean_file_and_directory(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "CLEAN.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "CLEAN.md")
    _git(repo, "commit", "-m", "add clean file")
    folder = repo / "folder"
    folder.mkdir()
    (folder / "nested.txt").write_text("dirty\n", encoding="utf-8")
    status = _git(repo, "status", "--short", "--branch")

    clean_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Clean file",
        changed_paths=("CLEAN.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=status,
    )
    directory_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Directory",
        changed_paths=("folder",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=status,
    )

    assert clean_plan.decision == "hold"
    assert any("not changed in the initial repository status" in blocker for blocker in clean_plan.blockers)
    assert directory_plan.decision == "hold"
    assert any("directory" in blocker or "not changed" in blocker for blocker in directory_plan.blockers)


def test_commit_runner_blocks_if_staged_set_changes_after_review(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def injecting_runner(argv, *, cwd, timeout_seconds, env):
        result = run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if argv[:3] == ("git", "add", "--"):
            (cwd / "EXTRA.md").write_text("unexpected\n", encoding="utf-8")
            _git(cwd, "add", "EXTRA.md")
        return result

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=injecting_runner,
    )

    assert report.status == "failed"
    assert report.commit_sha == ""
    assert any("staging_requires_review" in blocker for blocker in report.blockers)
    assert sorted(_git(repo, "diff", "--cached", "--name-only").splitlines()) == ["EXTRA.md", "README.md"]
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_requires_verified_full_sha_after_commit(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def invalid_sha_runner(argv, *, cwd, timeout_seconds, env):
        if argv == ("git", "rev-parse", "--verify", "HEAD^{commit}"):
            return RepoCommitCommandResult(exit_code=0, stdout="abc123\n")
        return run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=invalid_sha_runner,
    )

    assert report.status == "reconcile_required"
    assert report.commit_sha == ""
    assert report.committed_paths == ("repos/demo/README.md",)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "chore: update readme"
