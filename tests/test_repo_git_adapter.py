from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.repo_git_adapter import (
    ForgeExactReaderReference,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotError,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
    ForgeSnapshotRequest,
    RepoGitAdapter,
    RepoGitAdapterError,
    RepoGitCommandResult,
    git_read_command_is_allowed,
)
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote


class EvilStr(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        return True


class EvilInt(int):
    pass


class EvilAuthorityBinding(ForgeSnapshotAuthorityBinding):
    def __eq__(self, other):
        return True


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


def _forge_snapshot(*files: ForgeSnapshotFile) -> ForgeSnapshotInventory:
    return ForgeSnapshotInventory(
        owner_scope="user:alice",
        repo_id="demo",
        version_id="pv_" + "a" * 32,
        commit_sha="b" * 40,
        manifest_sha256="sha256:" + "c" * 64,
        authority_binding=_forge_authority_binding(),
        files=tuple(files),
    )


def _forge_authority_binding(**changes: str) -> ForgeSnapshotAuthorityBinding:
    values = {
        "adapter_id": "forge.code",
        "adapter_version": "v1",
        "adapter_generation": "usi_generation_" + "9" * 64,
        "admission_policy_generation": "fca.forge_code.admission.v1",
    }
    values.update(changes)
    return ForgeSnapshotAuthorityBinding(**values)


def test_forge_snapshot_inventory_is_content_free_and_deterministic():
    first = ForgeSnapshotFile("src/main.py", "sha256:" + "1" * 64, 23)
    second = ForgeSnapshotFile("README.md", "sha256:" + "2" * 64, 9)

    left = _forge_snapshot(first, second)
    right = _forge_snapshot(second, first)

    assert [item.path for item in left.files] == ["README.md", "src/main.py"]
    assert left.snapshot_digest == right.snapshot_digest
    assert left.file("src/main.py") == first
    dumped = json.dumps(left.to_dict())
    assert "content" not in dumped.replace("content_sha256", "")
    assert "source body" not in dumped


@pytest.mark.parametrize(
    "path",
    (
        "/etc/passwd",
        "C:/Windows/file.py",
        "src\\main.py",
        "src/../main.py",
        "src//main.py",
        "src/\nmain.py",
        "src/file:stream.py",
        "src/file.py.",
        "src/file.py ",
        "src/file?.py",
        "src/CON.py",
    ),
)
def test_forge_snapshot_file_rejects_noncanonical_or_absolute_paths(path: str):
    with pytest.raises(ForgeSnapshotError, match="path"):
        ForgeSnapshotFile(path, "sha256:" + "d" * 64, 1)


def test_forge_snapshot_rejects_duplicate_paths_and_tampered_digest():
    item = ForgeSnapshotFile("src/main.py", "sha256:" + "e" * 64, 1)
    with pytest.raises(ForgeSnapshotError, match="duplicate"):
        _forge_snapshot(item, item)
    with pytest.raises(ForgeSnapshotError, match="digest"):
        ForgeSnapshotInventory(
            owner_scope="user:alice",
            repo_id="demo",
            version_id="pv_" + "a" * 32,
            commit_sha="b" * 40,
            manifest_sha256="sha256:" + "c" * 64,
            authority_binding=_forge_authority_binding(),
            files=(item,),
            snapshot_digest="sha256:" + "0" * 64,
        )


def test_forge_snapshot_rejects_case_insensitive_windows_path_collisions():
    upper = ForgeSnapshotFile("src/Main.py", "sha256:" + "1" * 64, 1)
    lower = ForgeSnapshotFile("src/main.py", "sha256:" + "2" * 64, 1)

    with pytest.raises(ForgeSnapshotError, match="case-insensitive duplicate"):
        _forge_snapshot(upper, lower)


def test_forge_snapshot_normalizes_nfc_and_resolves_casefolded_unicode_aliases():
    canonical = ForgeSnapshotFile("src/Caf\u00e9.py", "sha256:" + "3" * 64, 3)
    nfd_case_alias = "SRC/CAFE\u0301.PY"
    inventory = _forge_snapshot(canonical)

    assert canonical.path == "src/Caf\u00e9.py"
    assert inventory.file(nfd_case_alias) == canonical

    colliding = ForgeSnapshotFile(nfd_case_alias, "sha256:" + "4" * 64, 4)
    with pytest.raises(ForgeSnapshotError, match="case-insensitive duplicate"):
        _forge_snapshot(canonical, colliding)


def test_authority_binding_is_mandatory_and_changes_snapshot_digest():
    item = ForgeSnapshotFile("src/main.py", "sha256:" + "5" * 64, 5)
    baseline = _forge_snapshot(item)
    changed = ForgeSnapshotInventory(
        owner_scope=baseline.owner_scope,
        repo_id=baseline.repo_id,
        version_id=baseline.version_id,
        commit_sha=baseline.commit_sha,
        manifest_sha256=baseline.manifest_sha256,
        authority_binding=_forge_authority_binding(admission_policy_generation="fca.forge_code.admission.v2"),
        files=baseline.files,
    )

    assert baseline.snapshot_digest != changed.snapshot_digest
    assert baseline.to_dict()["authority_binding"] == baseline.authority_binding.to_dict()
    with pytest.raises(ForgeSnapshotError, match="authority_binding"):
        ForgeSnapshotInventory(
            owner_scope=baseline.owner_scope,
            repo_id=baseline.repo_id,
            version_id=baseline.version_id,
            commit_sha=baseline.commit_sha,
            manifest_sha256=baseline.manifest_sha256,
            authority_binding=None,
            files=baseline.files,
        )


def test_forge_authority_contracts_reject_scalar_and_dataclass_subclasses():
    with pytest.raises(ForgeSnapshotError, match="bounded Forge token"):
        _forge_authority_binding(adapter_id=EvilStr("forge.code"))
    with pytest.raises(ForgeSnapshotError, match="byte_count"):
        ForgeSnapshotFile("src/main.py", "sha256:" + "6" * 64, EvilInt(1))

    binding = _forge_authority_binding()
    evil_binding = EvilAuthorityBinding(
        adapter_id=binding.adapter_id,
        adapter_version=binding.adapter_version,
        adapter_generation=binding.adapter_generation,
        admission_policy_generation=binding.admission_policy_generation,
    )
    with pytest.raises(ForgeSnapshotError, match="authority_binding"):
        ForgeSnapshotRequest(
            owner_scope="user:alice",
            authorization_ref="forge.auth_context",
            repo_id="demo",
            version_id="pv_" + "a" * 32,
            commit_sha="b" * 40,
            authority_binding=evil_binding,
        )


def test_forge_request_and_reader_reference_require_immutable_versions_and_relative_paths():
    request = ForgeSnapshotRequest(
        owner_scope="user:alice",
        authorization_ref="forge.auth_context",
        repo_id="demo",
        version_id="pv_" + "a" * 32,
        commit_sha="b" * 40,
        authority_binding=_forge_authority_binding(),
    )
    snapshot = _forge_snapshot(ForgeSnapshotFile("src/main.py", "sha256:" + "f" * 64, 7))
    reference = ForgeExactReaderReference(
        owner_scope=request.owner_scope,
        repo_id=request.repo_id,
        version_id=request.version_id,
        commit_sha=request.commit_sha,
        snapshot_digest=snapshot.snapshot_digest,
        path="src/main.py",
        content_sha256="sha256:" + "f" * 64,
        max_bytes=7,
        authority_binding=request.authority_binding,
    )

    assert reference.snapshot_digest == snapshot.snapshot_digest
    with pytest.raises(ForgeSnapshotError, match="canonical NFC"):
        ForgeExactReaderReference(
            owner_scope=request.owner_scope,
            repo_id=request.repo_id,
            version_id=request.version_id,
            commit_sha=request.commit_sha,
            snapshot_digest=snapshot.snapshot_digest,
            path="src/cafe\u0301.py",
            content_sha256="sha256:" + "f" * 64,
            max_bytes=7,
            authority_binding=request.authority_binding,
        )
    with pytest.raises(ForgeSnapshotError, match="immutable Forge version"):
        ForgeSnapshotRequest(
            owner_scope="user:alice",
            authorization_ref="forge.auth_context",
            repo_id="demo",
            version_id="HEAD",
            commit_sha="b" * 40,
            authority_binding=_forge_authority_binding(),
        )
    with pytest.raises(ForgeSnapshotError, match="immutable Forge commit"):
        ForgeSnapshotRequest(
            owner_scope="user:alice",
            authorization_ref="forge.auth_context",
            repo_id="demo",
            version_id="pv_" + "a" * 32,
            commit_sha="main",
            authority_binding=_forge_authority_binding(),
        )
