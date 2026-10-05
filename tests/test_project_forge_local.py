from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.project_forge_local import (
    LocalProjectForge,
    LocalProjectForgeError,
    _windows_path_units_including_nul,
)
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

    with (
        patch.object(forge, "_retain_commit", wraps=forge._retain_commit) as retain,
        patch.object(forge, "_assert_ref_lock_path_supported", side_effect=AssertionError("replay was blocked")),
    ):
        second = forge.store_commit(**kwargs)
    manifests_after = list((forge.store.project_root(owner_id="alice", repo_id="my-game") / "versions").glob("*/manifest.json"))

    assert second == first
    assert retain.call_count == 0
    assert manifests_after == manifests_before

    with pytest.raises(ProjectVersionConflictError, match="different request"):
        forge.store_commit(**{**kwargs, "version_label": "v2"})


def test_windows_path_unit_count_includes_utf16_units_and_terminating_nul() -> None:
    assert _windows_path_units_including_nul("x" * 259) == 260
    assert _windows_path_units_including_nul("x" * 260) == 261
    assert _windows_path_units_including_nul("x" * 257 + "😀") == 260


@pytest.mark.skipif(os.name != "nt", reason="Windows Git ref-lock boundary")
def test_windows_git_update_ref_fails_when_only_ref_lock_crosses_max_path(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    version_id = "pv_" + ("a" * 32)

    bare = None
    for width in range(1, 180):
        candidate = tmp_path / ("g" * width) / "repository.git"
        lock_path = candidate / "refs" / "odysseus" / "versions" / f"{version_id}.lock"
        candidate_units = _windows_path_units_including_nul(os.path.abspath(os.fspath(candidate)))
        lock_units = _windows_path_units_including_nul(os.path.abspath(os.fspath(lock_path)))
        if candidate_units <= 260 and lock_units > 260:
            bare = candidate
            break

    assert bare is not None, "test fixture could not place only the ref lock beyond MAX_PATH"
    bare.parent.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OBJECT_DIRECTORY": str(source / ".git" / "objects"),
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    initialized = subprocess.run(
        ["git", "init", "--quiet", "--bare", str(bare)],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert initialized.returncode == 0

    updated = subprocess.run(
        [
            "git",
            "--git-dir",
            str(bare),
            "update-ref",
            f"refs/odysseus/versions/{version_id}",
            commit_sha,
            "0" * len(commit_sha),
        ],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    stderr = updated.stderr.lower()
    has_lock_error = "cannot lock ref" in stderr
    has_path_error = "too long" in stderr
    assert updated.returncode != 0
    assert (has_lock_error, has_path_error) == (True, True)
    assert not (bare / "refs" / "odysseus" / "versions" / version_id).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Git ref-lock boundary")
def test_long_ref_lock_path_is_rejected_before_any_git_write(tmp_path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)

    selected_root = None
    selected_lock_units = 0
    for width in range(1, 180):
        candidate_root = tmp_path / ("r" * width)
        candidate_forge = LocalProjectForge(root=candidate_root, source_roots=(projects,))
        repository = candidate_forge.store.repository_path(owner_id="alice", repo_id="my-game")
        lock_path = repository / "refs" / "odysseus" / "versions" / ("pv_" + ("0" * 32) + ".lock")
        lock_units = _windows_path_units_including_nul(os.path.abspath(os.fspath(lock_path)))
        repository_units = _windows_path_units_including_nul(os.path.abspath(os.fspath(repository)))
        if lock_units > 260 and repository_units <= 260:
            selected_root = candidate_root
            selected_lock_units = lock_units
            break

    assert selected_root is not None, "test fixture could not isolate the Git ref-lock boundary"
    forge = LocalProjectForge(root=selected_root, source_roots=(projects,))
    repository = forge.store.repository_path(owner_id="alice", repo_id="my-game")
    assert selected_lock_units > 260
    assert _windows_path_units_including_nul(os.path.abspath(os.fspath(repository))) <= 260
    assert not repository.exists()

    git_operations: list[tuple[str, ...]] = []
    run_git = forge._run_git

    def track_git(arguments, **kwargs):
        git_operations.append(tuple(str(argument) for argument in arguments))
        return run_git(arguments, **kwargs)

    with patch.object(forge, "_run_git", side_effect=track_git):
        with pytest.raises(LocalProjectForgeError, match="shorter durable Forge root") as error:
            forge.store_commit(
                owner_id="alice",
                repo_id="my-game",
                source_repo=source,
                commit_sha=commit_sha,
                idempotency_key="long-ref-lock",
            )

    assert str(repository) not in str(error.value)
    assert repository.exists() is False
    assert not any(
        operation == "init" or operation == "fetch" or operation == "update-ref"
        for arguments in git_operations
        for operation in arguments
    )


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
