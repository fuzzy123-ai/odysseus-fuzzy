from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.code_lineage_contract import FileEventKind, HistoryState
from src.code_lineage_git_adapter import (
    CodeLineageGitAdapter,
    CodeLineageGitAdapterError,
    GitCommandResult,
    git_history_command_is_allowed,
)


INDEXED_AT = "2026-07-18T19:45:00Z"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout


def _git_no_cwd(*args: str) -> str:
    completed = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return completed.stdout


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()


def _make_history_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")
    src = repo / "src"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    root = _commit(repo, "root")
    _git(repo, "checkout", "-b", "feature")
    (src / "feature.py").write_text("def feature():\n    return 2\n", encoding="utf-8")
    _commit(repo, "feature add")
    _git(repo, "checkout", "master")
    _git(repo, "mv", "src/a.py", "src/b.py")
    rename = _commit(repo, "rename a to b")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    (src / "b.py").unlink()
    delete = _commit(repo, "delete b")
    assert root and rename and delete
    return repo


def test_history_adapter_reads_merge_rename_delete_and_blob_ids(tmp_path: Path):
    repo = _make_history_repo(tmp_path)
    adapter = CodeLineageGitAdapter(repo, repo_id="demo")

    history = adapter.read_history(indexed_at=INDEXED_AT, max_commits=20)
    changes = [change for change_set in history.change_sets for change in change_set.changes]

    assert history.repo_id == "demo"
    assert history.history_state is HistoryState.COMPLETE
    assert history.shallow_repository is False
    assert history.missing_ref == ""
    assert any(len(commit.parent_ids) == 2 for commit in history.commits)
    assert any(change.event_kind is FileEventKind.RENAMED and change.old_path == "src/a.py" and change.new_path == "src/b.py" for change in changes)
    assert any(change.event_kind is FileEventKind.DELETED and change.old_path == "src/b.py" and change.new_path == "" for change in changes)
    assert all(change.old_blob_id or change.new_blob_id for change in changes)
    assert json.loads(json.dumps(history.to_dict(), sort_keys=True))["repo_id"] == "demo"


def test_history_output_is_deterministic_for_same_repository(tmp_path: Path):
    repo = _make_history_repo(tmp_path)
    adapter = CodeLineageGitAdapter(repo, repo_id="demo")

    first = adapter.read_history(indexed_at=INDEXED_AT, max_commits=20)
    second = adapter.read_history(indexed_at=INDEXED_AT, max_commits=20)

    assert first.to_dict() == second.to_dict()
    assert [item.commit_id for item in first.commits] == sorted(item.commit_id for item in first.commits)
    assert [item.commit_id for item in first.change_sets] == sorted(item.commit_id for item in first.change_sets)


def test_shallow_repository_boundary_is_represented_without_fetch(tmp_path: Path):
    source = _make_history_repo(tmp_path / "source")
    shallow = tmp_path / "shallow"
    _git_no_cwd("clone", "--depth", "1", source.resolve().as_uri(), str(shallow))
    adapter = CodeLineageGitAdapter(shallow, repo_id="demo")

    history = adapter.read_history(indexed_at=INDEXED_AT, max_commits=20)

    assert history.shallow_repository is True
    assert history.history_state is HistoryState.PARTIAL
    assert any(commit.history_state is HistoryState.PARTIAL for commit in history.commits)
    assert all(commit.shallow_boundary or not commit.missing_parent_ids for commit in history.commits)


def test_missing_or_rewritten_range_returns_partial_slice_without_raw_output(tmp_path: Path):
    repo = _make_history_repo(tmp_path)
    adapter = CodeLineageGitAdapter(repo, repo_id="demo")

    history = adapter.read_history(start_ref="missing_ref", end_ref="HEAD", indexed_at=INDEXED_AT, max_commits=20)

    assert history.history_state is HistoryState.PARTIAL
    assert history.missing_ref == "missing_ref"
    assert history.commits == ()
    assert str(tmp_path) not in json.dumps(history.to_dict())


def test_command_allowlist_rejects_fetch_writes_shell_forms_and_unbounded_refs():
    assert git_history_command_is_allowed(("git", "rev-parse", "--is-shallow-repository")) is True
    assert git_history_command_is_allowed(("git", "fetch")) is False
    assert git_history_command_is_allowed(("git", "reset", "--hard")) is False
    assert git_history_command_is_allowed(("git", "rev-list", "--all")) is False
    assert git_history_command_is_allowed(("git", "rev-list", "--topo-order", "--parents", "--max-count", "999999", "HEAD")) is False
    assert git_history_command_is_allowed(("git", "rev-parse", "--verify", "--help^{commit}")) is False


def test_adapter_uses_argument_arrays_timeout_and_redacts_failures(tmp_path: Path):
    repo = _make_history_repo(tmp_path)
    head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    calls: list[tuple[str, ...]] = []

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append(argv)
        if argv == ("git", "rev-parse", "--is-shallow-repository"):
            return GitCommandResult(0, "false\n")
        if argv == ("git", "rev-parse", "--verify", "HEAD^{commit}"):
            return GitCommandResult(0, f"{head}\n")
        if argv[:4] == ("git", "rev-list", "--topo-order", "--parents"):
            return GitCommandResult(0, f"{head}\n")
        return GitCommandResult(1, "", f"fatal: token=abc path {tmp_path}")

    adapter = CodeLineageGitAdapter(repo, repo_id="demo", command_runner=runner, timeout_seconds=999)

    with pytest.raises(CodeLineageGitAdapterError) as exc:
        adapter.read_history(indexed_at=INDEXED_AT)

    assert all(isinstance(call, tuple) for call in calls)
    assert adapter.timeout_seconds == 10
    assert "token=abc" not in str(exc.value)
    assert str(tmp_path) not in str(exc.value)


def test_invalid_roots_refs_and_limits_fail_closed(tmp_path: Path):
    with pytest.raises(CodeLineageGitAdapterError, match="local Git repository"):
        CodeLineageGitAdapter(tmp_path / "missing", repo_id="demo")

    repo = _make_history_repo(tmp_path)
    adapter = CodeLineageGitAdapter(repo, repo_id="demo")

    with pytest.raises(CodeLineageGitAdapterError, match="bounded Git ref"):
        adapter.read_history(end_ref="--help", indexed_at=INDEXED_AT)
    with pytest.raises(CodeLineageGitAdapterError, match="max_commits"):
        adapter.read_history(indexed_at=INDEXED_AT, max_commits=0)
