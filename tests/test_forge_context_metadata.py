from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

import pytest

from core.auth import AuthManager
from src.forge_context_metadata import (
    ForgeContextMetadata,
    ForgeContextMetadataError,
)
from src.private_context_confirmation_app import CookieConfirmationAuthority
from src.project_forge_local import LocalProjectForge
from src.repo_registry import RepoRecord, RepoRegistry
from src.unified_source_index_owner_scope import OwnerScope


def _run_git(work: Path, *arguments: str, stdout=subprocess.DEVNULL) -> None:
    try:
        result = subprocess.run(
            ("git", "-c", "core.hooksPath=" + str(work / ".no-hooks"),
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "core.fsmonitor=false",
             "-C", str(work), *arguments),
            check=False, timeout=10, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AssertionError("fixture_git_unavailable") from None
    if result.returncode != 0:
        raise AssertionError("fixture_git_failed") from None


def _commit_sha(work: Path, output_path: Path) -> str:
    with output_path.open("wb") as output:
        _run_git(work, "rev-parse", "HEAD", stdout=output)
    raw = output_path.read_bytes()
    if len(raw) > 128 or re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\r?\n", raw) is None:
        raise AssertionError("git commit identity was not a closed SHA")
    return raw.strip().decode("ascii")


@pytest.fixture
def real_public_forge(tmp_path: Path, request) -> dict[str, object]:
    owned = tempfile.TemporaryDirectory(prefix="fm-", dir=Path(__file__).resolve().parents[1] / ".abc-test-tmp")
    request.addfinalizer(owned.cleanup)
    tmp_path = Path(owned.name)
    workspace = tmp_path / "workspace"
    server_projects = tmp_path / "server-projects"
    source = workspace / "repos" / "demo"
    source.mkdir(parents=True)
    (source / ".no-hooks").mkdir()
    server_projects.mkdir()
    _run_git(source, "init", "-b", "main")
    _run_git(source, "config", "user.email", "forge-metadata@example.invalid")
    _run_git(source, "config", "user.name", "Forge Metadata Fixture")
    readme = b"Public retained project\n"
    code = b"print('retained')\n"
    (source / "README.md").write_bytes(readme)
    (source / "src").mkdir()
    (source / "src" / "main.py").write_bytes(code)
    _run_git(source, "add", "README.md", "src/main.py")
    _run_git(source, "commit", "-m", "retained public fixture")
    commit = _commit_sha(source, tmp_path / "commit.sha")

    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("alice", "Password-123", is_admin=True)
    token = auth.create_session("alice", "Password-123")
    assert token
    owner_sid = auth.subject_id_for_username("alice")
    assert owner_sid

    forge = LocalProjectForge(
        root=tmp_path / "versions",
        source_roots=(workspace, server_projects),
    )
    stored = forge.store_existing_commit(
        owner_id=owner_sid,
        repo_id="demo",
        source_repo=source,
        commit_sha=commit,
        idempotency_key="fixture-retain-0001",
        policy_snapshot={"forge_context": "public_registered"},
        version_label="Public retained fixture",
        artifacts=(),
    )

    registry_path = tmp_path / "repo_registry.json"
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Public Demo",
            repo_kind="project",
            owner=owner_sid,
            path_ref="workspace/repos/demo",
            workspace_root="workspace",
            project_root="workspace/repos/demo",
            default_branch="main",
            current_branch="main",
            privacy_class="public",
            provider_scope="default",
            remotes=(),
            created_at="2026-10-04T00:00:00Z",
        )
    )
    registry.save_json(registry_path)

    service = ForgeContextMetadata(
        local_forge=forge,
        registry_path=registry_path,
        auth_manager=auth,
        workspace_root=workspace,
        server_projects_root=server_projects,
    )
    return {
        "auth": auth,
        "token": token,
        "owner_sid": owner_sid,
        "service": service,
        "stored": stored,
        "registry_path": registry_path,
        "workspace": workspace,
        "server_projects": server_projects,
    }


def _capture_fixture(fixture: dict[str, object]):
    auth = fixture["auth"]
    service = fixture["service"]
    token = fixture["token"]
    with CookieConfirmationAuthority(auth).hold(token, "chat-owned") as binding:
        manifest = service.capture_version_metadata(
            binding,
            "demo",
            fixture["stored"].version_id,
            ("README.md",),
        )
    return auth, service, token, manifest


def test_real_public_retained_version_metadata_is_bound_and_revalidated(real_public_forge) -> None:
    auth, service, token, manifest = _capture_fixture(real_public_forge)
    descriptor = manifest.descriptor
    assert real_public_forge["stored"].manifest["artifacts"] == []
    assert descriptor.owner_scope == OwnerScope.for_subject_id(auth.subject_id_for_username("alice")).value
    assert descriptor.selected_paths == ("README.md",)
    assert manifest.item.classification == "public"
    assert descriptor.payload_sha256 == hashlib.sha256(b"Public retained project\n").hexdigest()
    assert len(manifest.digest) == 64
    assert "README.md" not in repr(manifest)
    with CookieConfirmationAuthority(auth).hold(token, "chat-owned") as binding:
        assert service.resolve_selection(binding, descriptor) == descriptor
        assert service.revalidate_metadata(binding, manifest) == manifest
        code_manifest = service.capture_version_metadata(
            binding,
            "demo",
            real_public_forge["stored"].version_id,
            ("src/main.py",),
        )
        assert code_manifest.descriptor.source_id != descriptor.source_id
        assert code_manifest.descriptor.payload_sha256 == hashlib.sha256(b"print('retained')\n").hexdigest()
        with pytest.raises(ForgeContextMetadataError) as multiple_paths:
            service.capture_version_metadata(
                binding,
                "demo",
                real_public_forge["stored"].version_id,
                ("README.md", "src/main.py"),
            )
    assert multiple_paths.value.code == "invalid_context"


def test_wrong_sid_private_registry_and_sensitive_path_fail_closed(real_public_forge) -> None:
    auth = real_public_forge["auth"]
    service = real_public_forge["service"]
    auth.create_user("bob", "Password-123", is_admin=True)
    bob = auth.create_session("bob", "Password-123")
    assert bob
    with CookieConfirmationAuthority(auth).hold(bob, "chat-owned") as binding:
        with pytest.raises(ForgeContextMetadataError) as wrong_sid:
            service.capture_version_metadata(binding, "demo", real_public_forge["stored"].version_id, ("README.md",))
    assert wrong_sid.value.code == "forge_context_policy"

    registry_path = real_public_forge["registry_path"]
    registry = RepoRegistry.load_or_empty(registry_path)
    record = registry.get("demo")
    registry.put(record.with_policy(privacy_class="private", updated_at="2026-10-04T00:01:00Z"))
    registry.save_json(registry_path)
    with CookieConfirmationAuthority(auth).hold(real_public_forge["token"], "chat-owned") as binding:
        with pytest.raises(ForgeContextMetadataError) as private_repo:
            service.capture_version_metadata(binding, "demo", real_public_forge["stored"].version_id, ("README.md",))
    registry.put(record.with_policy(privacy_class="public", updated_at="2026-10-04T00:02:00Z"))
    registry.save_json(registry_path)
    with CookieConfirmationAuthority(auth).hold(real_public_forge["token"], "chat-owned") as binding:
        with pytest.raises(ForgeContextMetadataError) as sensitive_path:
            service.capture_version_metadata(binding, "demo", real_public_forge["stored"].version_id, (".env",))
    assert private_repo.value.code == "forge_context_policy"
    assert sensitive_path.value.code == "invalid_context"


def test_foreign_descriptor_path_and_version_drift_are_rejected(real_public_forge) -> None:
    auth, service, token, manifest = _capture_fixture(real_public_forge)
    foreign = replace(manifest.descriptor, owner_scope=OwnerScope.for_subject_id("owner_" + "b" * 32).value)
    with CookieConfirmationAuthority(auth).hold(token, "chat-owned") as binding:
        with pytest.raises(ForgeContextMetadataError) as foreign_ref:
            service.resolve_selection(binding, foreign)
        with pytest.raises(ForgeContextMetadataError) as bad_path:
            service.capture_version_metadata(binding, "demo", manifest.descriptor.version_id, ("../outside",))
        with pytest.raises(ForgeContextMetadataError) as bad_version:
            service.capture_version_metadata(binding, "demo", "pv_" + "0" * 32, ("README.md",))
    assert foreign_ref.value.code == "forge_context_stale"
    assert bad_path.value.code == "invalid_context"
    assert bad_version.value.code == "forge_context_unavailable"
