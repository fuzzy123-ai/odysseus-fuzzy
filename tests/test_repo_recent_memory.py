from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.repo_recent_memory import collect_repo_change_capsule, list_repo_change_history
from src.repo_registry import RepoRecord, RepoRegistry


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _make_repo(base: Path) -> Path:
    repo = base / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    tracked = repo / "src" / "repo_agent.py"
    tracked.parent.mkdir()
    tracked.write_text("print('old')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Add repo control skeleton")
    tracked.write_text("print('new')\n", encoding="utf-8")
    docs = repo / "docs" / "notes.md"
    docs.parent.mkdir()
    docs.write_text("roadmap notes\n", encoding="utf-8")
    return repo


def _registry(*, privacy_class: str) -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id=f"{privacy_class}-demo",
            title=f"{privacy_class.title()} Demo",
            repo_kind="project",
            owner="fuzzy123-ai",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            privacy_class=privacy_class,
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


@pytest.mark.parametrize(
    ("privacy_class", "paths_expected", "subjects_expected", "external_allowed"),
    [
        ("public", True, True, True),
        ("private", True, True, False),
        ("sensitive", False, False, False),
    ],
)
def test_repo_change_capsule_respects_privacy_classes(
    tmp_path: Path,
    privacy_class: str,
    paths_expected: bool,
    subjects_expected: bool,
    external_allowed: bool,
):
    _make_repo(tmp_path)

    report = collect_repo_change_capsule(
        registry=_registry(privacy_class=privacy_class),
        repo_id=f"{privacy_class}-demo",
        workspace_base=tmp_path,
        history_dir=tmp_path / "history",
        hours=24,
    )
    payload = report.to_dict()
    snapshot = payload["snapshot"]

    assert snapshot["privacy_class"] == privacy_class
    assert snapshot["external_summary_allowed"] is external_allowed
    assert snapshot["counts"]["tracked_changes"] == 1
    assert snapshot["counts"]["untracked_files"] == 1
    assert snapshot["redaction_policy"]["raw_diffs_included"] is False
    assert snapshot["domains"]
    assert bool(snapshot["tracked_changes"]) is paths_expected
    assert bool(snapshot["untracked_files"]) is paths_expected
    assert any(commit.get("subject") for commit in snapshot["commits"]) is subjects_expected

    dumped = json.dumps(payload, ensure_ascii=True)
    assert "repo_root" not in dumped
    assert "numstat" not in dumped
    assert "diff --git" not in dumped
    assert str(tmp_path) not in dumped
    if privacy_class == "sensitive":
        assert "src/repo_agent.py" not in dumped
        assert "docs/notes.md" not in dumped
        assert "Add repo control skeleton" not in dumped


def test_repo_change_capsule_persists_deduped_history_and_memory_records(tmp_path: Path):
    _make_repo(tmp_path)
    history_dir = tmp_path / "history"
    registry = _registry(privacy_class="private")

    first = collect_repo_change_capsule(
        registry=registry,
        repo_id="private-demo",
        workspace_base=tmp_path,
        history_dir=history_dir,
        hours=24,
    )
    second = collect_repo_change_capsule(
        registry=registry,
        repo_id="private-demo",
        workspace_base=tmp_path,
        history_dir=history_dir,
        hours=24,
    )
    rows = list_repo_change_history(repo_id="private-demo", history_dir=history_dir)

    first_payload = first.to_dict()
    second_payload = second.to_dict()
    assert first_payload["persisted"] is True
    assert second_payload["persisted"] is False
    assert second_payload["duplicate_of"] == first_payload["snapshot"]["id"]
    assert [row["id"] for row in rows] == [first_payload["snapshot"]["id"]]
    assert first_payload["memory_records"][0]["source"] == "repo_recent_changes"
    assert first_payload["memory_records"][0]["metadata"]["external_summary_allowed"] is False
    assert first_payload["raptorgraph_event"]["event"] == "repo_recent_changes_snapshot"
    assert first_payload["raptorgraph_event"]["memory_record_ids"] == [
        first_payload["memory_records"][0]["memory_id"]
    ]
