from __future__ import annotations

import json
import subprocess

import pytest

from src.release_manifest import (
    ReleaseManifestError,
    build_release_manifest,
    read_release_manifest,
    validate_release_manifest,
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


def _repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    source = repo / "src" / "recent_changes.py"
    source.parent.mkdir()
    source.write_text("VERSION = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(changes): add release evidence")

    source.write_text("VERSION = 2\n", encoding="utf-8")
    test_file = repo / "tests" / "test_changes.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    secret = repo / ".env"
    secret.write_text("TOKEN=must-not-appear\n", encoding="utf-8")
    _git(repo, "add", "src/recent_changes.py", "tests/test_changes.py")
    _git(repo, "add", "-f", ".env")
    _git(
        repo,
        "commit",
        "-m",
        (
            "fix(changes): bind patch notes to revision "
            "token=must-not-appear"
        ),
    )
    return repo


def test_release_manifest_is_revision_bound_redacted_and_deterministic(
    tmp_path,
):
    repo = _repository(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    first = build_release_manifest(
        repo_root=repo,
        revision=head,
        ref="dev",
        max_commits=20,
    )
    second = build_release_manifest(
        repo_root=repo,
        revision=head,
        ref="dev",
        max_commits=20,
    )

    assert first == second
    assert first["revision"] == head
    assert first["short_revision"] == head[:12]
    assert first["ref"] == "dev"
    assert first["coverage"]["history_mode"] == "first_parent"
    assert first["commits"][0]["category"] == "Fixes"
    assert first["commits"][0]["scope"] == "changes"
    assert "src/recent_changes.py" in first["commits"][0]["paths"]
    assert "tests/test_changes.py" in first["commits"][0]["paths"]
    dumped = json.dumps(first, ensure_ascii=False)
    assert ".env" not in dumped
    assert "TOKEN=" not in dumped
    assert "must-not-appear" not in dumped
    assert "token=<redacted>" in dumped
    assert "test@example.com" not in dumped
    assert validate_release_manifest(first, expected_revision=head) is first


def test_release_manifest_read_requires_matching_runtime_revision(tmp_path):
    repo = _repository(tmp_path)
    manifest = build_release_manifest(repo_root=repo, ref="dev")
    output = tmp_path / "runtime" / "release-manifest.json"
    write_release_manifest(manifest, output)

    loaded, state = read_release_manifest(
        output,
        expected_revision=manifest["short_revision"],
    )
    mismatch, mismatch_state = read_release_manifest(
        output,
        expected_revision="f" * 40,
    )

    assert state == "ready"
    assert loaded == manifest
    assert mismatch is None
    assert mismatch_state == "revision_mismatch"


def test_release_manifest_rejects_tampered_content(tmp_path):
    repo = _repository(tmp_path)
    manifest = build_release_manifest(repo_root=repo, ref="dev")
    manifest["commits"][0]["subject"] = "tampered"

    with pytest.raises(
        ReleaseManifestError,
        match="content_digest_mismatch",
    ):
        validate_release_manifest(manifest)


def test_release_manifest_rejects_unsafe_paths_even_with_recomputed_digest(
    tmp_path,
):
    repo = _repository(tmp_path)
    manifest = build_release_manifest(repo_root=repo, ref="dev")
    manifest["commits"][0]["paths"] = ["data/private.db"]

    with pytest.raises(ReleaseManifestError, match="unsafe_commit_path"):
        validate_release_manifest(manifest)


def test_release_manifest_missing_file_has_bounded_state(tmp_path):
    document, state = read_release_manifest(
        tmp_path / "missing.json",
        expected_revision="a" * 40,
    )

    assert document is None
    assert state == "missing"
