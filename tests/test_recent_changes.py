from __future__ import annotations

import subprocess

from src.recent_changes import collect_recent_changes, list_change_history, read_change_snapshot


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def test_recent_changes_collects_dirty_and_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    tracked = repo / "src" / "feature.py"
    tracked.parent.mkdir()
    tracked.write_text("print('old')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    tracked.write_text("print('new')\n", encoding="utf-8")
    new_file = repo / "docs" / "patch.md"
    new_file.parent.mkdir()
    new_file.write_text("patch notes\n", encoding="utf-8")

    history_dir = tmp_path / "history"
    snapshot = collect_recent_changes(repo_root=repo, history_dir=history_dir, hours=12)

    assert snapshot["persisted"] is True
    assert any(item["path"] == "src/feature.py" for item in snapshot["tracked_changes"])
    assert "docs/patch.md" in snapshot["untracked_files"]
    assert "repo_root" not in snapshot
    assert snapshot["repo_name"] == "repo"
    assert snapshot["repo_fingerprint"]
    assert any(item["domain"] == "Docs/Roadmaps" for item in snapshot["change_evidence"])
    assert "Areas:" in snapshot["patch_notes"]
    assert "Tracked changes:" in snapshot["patch_notes"]
    assert "New files:" in snapshot["patch_notes"]


def test_recent_changes_history_dedupes_and_reads_latest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    file_path = repo / "README.md"
    file_path.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    file_path.write_text("two\n", encoding="utf-8")

    history_dir = tmp_path / "history"
    first = collect_recent_changes(repo_root=repo, history_dir=history_dir, hours=12)
    second = collect_recent_changes(repo_root=repo, history_dir=history_dir, hours=12)

    assert first["persisted"] is True
    assert second["persisted"] is False
    assert second["duplicate_of"] == first["id"]
    rows = list_change_history(history_dir=history_dir)
    assert [row["id"] for row in rows] == [first["id"]]
    latest = read_change_snapshot("latest", history_dir=history_dir)
    assert latest is not None
    assert latest["id"] == first["id"]


def test_recent_changes_skips_private_attachment_and_output_noise(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    private_attachment = repo / ".codex-remote-attachments" / "secret" / "photo.jpg"
    private_attachment.parent.mkdir(parents=True)
    private_attachment.write_bytes(b"private")
    generated_output = repo / "output" / "debug.txt"
    generated_output.parent.mkdir()
    generated_output.write_text("noise\n", encoding="utf-8")
    useful = repo / "src" / "new_feature.py"
    useful.parent.mkdir()
    useful.write_text("print('ok')\n", encoding="utf-8")

    snapshot = collect_recent_changes(
        repo_root=repo,
        history_dir=tmp_path / "history",
        hours=12,
        persist=False,
    )

    rendered = snapshot["patch_notes"]
    assert ".codex-remote-attachments" not in rendered
    assert "output/debug.txt" not in rendered
    assert "src/new_feature.py" in rendered
    assert str(repo) not in rendered
