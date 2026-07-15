from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.project_forge_local import LocalProjectForge, LocalProjectForgeError
from src.project_version_store import ProjectVersionConflictError, ProjectVersionIntegrityError, owner_key_for


FIXED_NOW = datetime(2026, 7, 13, 9, 10, 11, tzinfo=timezone.utc)


def _git(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _source_repo(root: Path) -> tuple[Path, str]:
    repo = root / "source"
    repo.mkdir(parents=True)
    _git("init", "--quiet", cwd=repo)
    _git("config", "user.name", "Odysseus Test", cwd=repo)
    _git("config", "user.email", "odysseus@example.invalid", cwd=repo)
    (repo / "game.txt").write_text("persistent\n", encoding="utf-8")
    _git("add", "game.txt", cwd=repo)
    _git("commit", "--quiet", "-m", "Create game", cwd=repo)
    return repo, _git("rev-parse", "HEAD", cwd=repo)


def _forge(tmp_path: Path) -> LocalProjectForge:
    return LocalProjectForge(
        root=tmp_path / "data" / "project_forge",
        clock=lambda: FIXED_NOW,
        source_roots=(tmp_path / "projects",),
    )


def test_local_only_forge_retains_commit_and_durable_version_ref(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    forge = _forge(tmp_path)

    stored = forge.store_commit(
        owner_id="alice",
        repo_id="my-game",
        source_repo=source,
        commit_sha=commit_sha,
        idempotency_key="commit-1",
        version_label="Playable build",
        change_notes=("Keep sandbox output",),
        policy_snapshot={"schema": "odysseus.project_forge_policy.v1", "forge_mode": "local"},
    )

    bare = forge.store.repository_path(owner_id="alice", repo_id="my-game")
    assert _git("--git-dir", str(bare), "cat-file", "-t", commit_sha) == "commit"
    assert _git(
        "--git-dir",
        str(bare),
        "rev-parse",
        f"refs/odysseus/versions/{stored.version_id}^{{commit}}",
    ) == commit_sha
    assert stored.manifest["policy_snapshot"]["forge_mode"] == "local"
    assert "provider_statuses" not in stored.manifest
    assert forge.verify_version(owner_id="alice", repo_id="my-game", version_id=stored.version_id) == stored


def test_owner_scope_uses_distinct_bare_repositories(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    forge = _forge(tmp_path)

    alice = forge.store_commit(
        owner_id="alice",
        repo_id="shared-name",
        source_repo=source,
        commit_sha=commit_sha,
        idempotency_key="alice-1",
    )
    bob = forge.store_commit(
        owner_id="bob",
        repo_id="shared-name",
        source_repo=source,
        commit_sha=commit_sha,
        idempotency_key="bob-1",
    )

    assert alice.owner_key == owner_key_for("alice")
    assert bob.owner_key == owner_key_for("bob")
    assert forge.store.repository_path(owner_id="alice", repo_id="shared-name") != forge.store.repository_path(
        owner_id="bob", repo_id="shared-name"
    )


def test_completed_idempotent_replay_does_not_fetch_or_create_another_manifest(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    forge = _forge(tmp_path)
    kwargs = {
        "owner_id": "alice",
        "repo_id": "my-game",
        "source_repo": source,
        "commit_sha": commit_sha,
        "idempotency_key": "same-request",
        "version_label": "v1",
    }
    first = forge.store_commit(**kwargs)
    manifests_before = list((forge.store.project_root(owner_id="alice", repo_id="my-game") / "versions").glob("*/manifest.json"))

    with patch.object(forge, "_retain_commit", wraps=forge._retain_commit) as retain:
        second = forge.store_commit(**kwargs)
    manifests_after = list((forge.store.project_root(owner_id="alice", repo_id="my-game") / "versions").glob("*/manifest.json"))

    assert second == first
    assert retain.call_count == 0
    assert manifests_after == manifests_before

    with pytest.raises(ProjectVersionConflictError, match="different request"):
        forge.store_commit(**{**kwargs, "version_label": "v2"})


def test_retry_finishes_from_durable_ref_after_source_workspace_is_gone(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    forge = _forge(tmp_path)
    kwargs = {
        "owner_id": "alice",
        "repo_id": "my-game",
        "source_repo": source,
        "commit_sha": commit_sha,
        "idempotency_key": "crash-window",
        "version_label": "v1",
    }
    original_persist = forge.store.persist_version
    with patch.object(forge.store, "persist_version", side_effect=RuntimeError("simulated crash")):
        with pytest.raises(RuntimeError, match="simulated crash"):
            forge.store_commit(**kwargs)

    expired_source = projects / "expired-source"
    source.rename(expired_source)
    with patch.object(forge.store, "persist_version", wraps=original_persist) as persist:
        stored = forge.store_commit(**kwargs)

    assert persist.call_count == 1
    assert stored.commit_sha == commit_sha
    assert forge.verify_version(owner_id="alice", repo_id="my-game", version_id=stored.version_id) == stored


def test_verify_fails_if_durable_ref_no_longer_matches_manifest(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    forge = _forge(tmp_path)
    stored = forge.store_commit(
        owner_id="alice",
        repo_id="my-game",
        source_repo=source,
        commit_sha=commit_sha,
        idempotency_key="commit-1",
    )
    (source / "game.txt").write_text("changed\n", encoding="utf-8")
    _git("add", "game.txt", cwd=source)
    _git("commit", "--quiet", "-m", "Change game", cwd=source)
    other_sha = _git("rev-parse", "HEAD", cwd=source)
    bare = forge.store.repository_path(owner_id="alice", repo_id="my-game")
    _git("--git-dir", str(bare), "fetch", str(source), other_sha)
    _git("--git-dir", str(bare), "update-ref", f"refs/odysseus/versions/{stored.version_id}", other_sha)

    with pytest.raises(ProjectVersionIntegrityError, match="ref does not match"):
        forge.verify_version(owner_id="alice", repo_id="my-game", version_id=stored.version_id)


def test_source_must_be_local_git_repo_below_authorized_root(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    source, commit_sha = _source_repo(outside)
    forge = _forge(tmp_path)

    with pytest.raises(LocalProjectForgeError, match="outside authorized"):
        forge.store_commit(
            owner_id="alice",
            repo_id="my-game",
            source_repo=source,
            commit_sha=commit_sha,
            idempotency_key="outside",
        )
    with pytest.raises(LocalProjectForgeError, match="local filesystem"):
        forge.store_commit(
            owner_id="alice",
            repo_id="my-game",
            source_repo="https://example.invalid/repo.git",
            commit_sha=commit_sha,
            idempotency_key="remote",
        )
