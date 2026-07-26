from __future__ import annotations

import asyncio
import json
import subprocess

from src.recent_changes import (
    collect_recent_changes,
    list_change_history,
    read_change_snapshot,
    record_post_update_snapshot,
    record_pre_update_snapshot,
)
from src.release_manifest import (
    build_release_manifest,
    write_release_manifest,
)


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
    assert snapshot["trigger"] == "manual"
    assert any(item["domain"] == "Docs/Roadmaps" for item in snapshot["change_evidence"])
    assert "Areas:" in snapshot["patch_notes"]
    assert "Trigger: manual" in snapshot["patch_notes"]
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


def test_recent_changes_redacts_secret_and_private_paths_across_snapshot_payload(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    secret = repo / ".env"
    secret.write_text("TOKEN=old\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    secret.write_text("TOKEN=new\n", encoding="utf-8")
    private_key = repo / "deploy.key"
    private_key.write_text("PRIVATE KEY\n", encoding="utf-8")
    private_data = repo / "data" / "private.db"
    private_data.parent.mkdir()
    private_data.write_text("private\n", encoding="utf-8")
    private_log = repo / "logs" / "app.log"
    private_log.parent.mkdir()
    private_log.write_text("private\n", encoding="utf-8")
    useful = repo / "src" / "feature.py"
    useful.parent.mkdir()
    useful.write_text("print('ok')\n", encoding="utf-8")

    snapshot = collect_recent_changes(
        repo_root=repo,
        history_dir=tmp_path / "history",
        hours=12,
        persist=False,
    )

    dumped = json.dumps(snapshot, ensure_ascii=False)
    assert "src/feature.py" in dumped
    assert ".env" not in dumped
    assert "deploy.key" not in dumped
    assert "data/private.db" not in dumped
    assert "logs/app.log" not in dumped
    assert "TOKEN=" not in dumped
    assert "PRIVATE KEY" not in dumped
    assert str(repo) not in dumped


def test_recent_changes_tool_output_uses_redacted_snapshot_payload(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    env_file = repo / ".env"
    env_file.write_text("TOKEN=old\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    env_file.write_text("TOKEN=new\n", encoding="utf-8")
    useful = repo / "src" / "agent_gate.py"
    useful.parent.mkdir()
    useful.write_text("print('ok')\n", encoding="utf-8")

    import src.recent_changes as recent_changes
    from src.tool_domains.repo_skills import do_recent_changes

    monkeypatch.setattr(recent_changes, "BASE_DIR", str(repo))
    monkeypatch.setattr(recent_changes, "RECENT_CHANGES_DIR", str(tmp_path / "history"))

    result = asyncio.run(do_recent_changes('{"action":"collect","hours":12}', owner="tester"))

    assert result["exit_code"] == 0
    dumped = json.dumps(result, ensure_ascii=False)
    assert "src/agent_gate.py" in dumped
    assert ".env" not in dumped
    assert "TOKEN=" not in dumped
    assert str(repo) not in dumped


def test_recent_changes_retention_and_trigger_policy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    file_path = repo / "README.md"
    file_path.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    history_dir = tmp_path / "history"
    collect_recent_changes(
        repo_root=repo,
        history_dir=history_dir,
        hours=12,
        force=True,
        trigger="startup",
        retention_limit=2,
    )
    file_path.write_text("two\n", encoding="utf-8")
    collect_recent_changes(
        repo_root=repo,
        history_dir=history_dir,
        hours=12,
        force=True,
        trigger="update-check",
        retention_limit=2,
    )
    file_path.write_text("three\n", encoding="utf-8")
    latest = collect_recent_changes(
        repo_root=repo,
        history_dir=history_dir,
        hours=12,
        force=True,
        trigger="unknown",
        retention_limit=2,
    )

    rows = list_change_history(history_dir=history_dir, limit=10)
    assert len(rows) == 2
    assert rows[0]["id"] == latest["id"]
    assert rows[0]["trigger"] == "manual"
    assert any(row["trigger"] == "update_check" for row in rows)
    assert latest["retention"]["trimmed"] == 1
    assert latest["retention"]["history_count"] == 2


def test_recent_changes_pre_and_post_update_helpers_are_local_snapshots(tmp_path, monkeypatch):
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

    import src.recent_changes as recent_changes

    monkeypatch.setattr(recent_changes, "BASE_DIR", str(repo))
    monkeypatch.setattr(recent_changes, "RECENT_CHANGES_DIR", str(tmp_path / "history"))

    pre = record_pre_update_snapshot()
    post = record_post_update_snapshot()

    assert pre["trigger"] == "pre_update"
    assert post["trigger"] == "post_update"
    assert pre["persisted"] is True
    assert post["persisted"] is True


def test_recent_changes_uses_revision_bound_manifest_without_git(
    tmp_path,
    monkeypatch,
):
    release_repo = tmp_path / "release-repo"
    release_repo.mkdir()
    _git(release_repo, "init")
    _git(release_repo, "config", "user.email", "test@example.com")
    _git(release_repo, "config", "user.name", "Test User")
    feature = release_repo / "src" / "patchnotes.py"
    feature.parent.mkdir()
    feature.write_text("ENABLED = True\n", encoding="utf-8")
    _git(release_repo, "add", ".")
    _git(
        release_repo,
        "commit",
        "-m",
        "feat(changes): expose revision-bound patch notes",
    )
    manifest = build_release_manifest(
        repo_root=release_repo,
        ref="dev",
    )
    manifest_path = tmp_path / "runtime" / "release-manifest.json"
    write_release_manifest(manifest, manifest_path)

    app_root = tmp_path / "app-without-git"
    app_root.mkdir()
    (app_root / "app.py").write_text("# runtime\n", encoding="utf-8")
    history_dir = tmp_path / "history"

    import src.recent_changes as recent_changes

    monkeypatch.setattr(
        recent_changes,
        "RELEASE_MANIFEST_FILE",
        str(manifest_path),
    )
    monkeypatch.setenv(
        "ODYSSEUS_GIT_COMMIT",
        "f" * 40,
    )
    monkeypatch.setenv(
        "ODYSSEUS_RELEASE_REVISION",
        manifest["revision"],
    )

    snapshot = collect_recent_changes(
        repo_root=app_root,
        history_dir=history_dir,
        hours=24,
        persist=False,
    )

    assert snapshot["git_available"] is False
    assert snapshot["git_error_count"] == 5
    assert snapshot["git_errors"] == []
    assert snapshot["evidence_source"] == "release_manifest"
    assert snapshot["evidence_state"] == "ready"
    assert snapshot["release_manifest"]["revision"] == manifest["revision"]
    assert snapshot["commits"][0]["category"] == "Features"
    assert snapshot["commits"][0]["scope"] == "changes"
    assert snapshot["tracked_changes"] == [
        {"status": "release", "path": "src/patchnotes.py"}
    ]
    assert snapshot["recent_files"] == []
    assert "revision-bound release commit(s)" in snapshot["summary"][0]
    assert "Evidence: release_manifest (ready)" in snapshot["patch_notes"]


def test_recent_changes_fails_visibly_on_manifest_revision_mismatch(
    tmp_path,
    monkeypatch,
):
    release_repo = tmp_path / "release-repo"
    release_repo.mkdir()
    _git(release_repo, "init")
    _git(release_repo, "config", "user.email", "test@example.com")
    _git(release_repo, "config", "user.name", "Test User")
    source = release_repo / "app.py"
    source.write_text("# release\n", encoding="utf-8")
    _git(release_repo, "add", ".")
    _git(release_repo, "commit", "-m", "feat: release")
    manifest = build_release_manifest(repo_root=release_repo, ref="dev")
    manifest_path = tmp_path / "runtime" / "release-manifest.json"
    write_release_manifest(manifest, manifest_path)

    app_root = tmp_path / "app-without-git"
    app_root.mkdir()
    (app_root / "app.py").write_text("# runtime\n", encoding="utf-8")

    import src.recent_changes as recent_changes

    monkeypatch.setattr(
        recent_changes,
        "RELEASE_MANIFEST_FILE",
        str(manifest_path),
    )
    monkeypatch.setenv("ODYSSEUS_GIT_COMMIT", "f" * 40)

    snapshot = collect_recent_changes(
        repo_root=app_root,
        history_dir=tmp_path / "history",
        hours=24,
        persist=False,
    )

    assert snapshot["evidence_source"] == "filesystem_mtime"
    assert snapshot["evidence_state"] == "degraded"
    assert snapshot["release_manifest"] == {
        "state": "revision_mismatch"
    }
    assert "Authoritative Git/release evidence is unavailable" in (
        snapshot["summary"][0]
    )
    assert "Evidence: filesystem_mtime (degraded)" in snapshot["patch_notes"]
