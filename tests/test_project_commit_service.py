from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.project_commit_service import ProjectCommitService, ProjectCommitServiceError
from src.project_forge_contract import ProjectCommitRequest, ProjectForgeContractError
from src.project_forge_local import LocalProjectForge
from src.project_forge_policy import ProjectForgePolicy
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


def _registry() -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner="owner@example.com",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            allowed_actions=["status", "changed_paths", "commit_plan", "commit"],
            created_at="2026-07-13T10:00:00Z",
        )
    )
    return registry


def _request(*, confirmed: bool = True) -> ProjectCommitRequest:
    return ProjectCommitRequest.create(
        repo_id="demo",
        title="feat: retain project version",
        description="Keep the reviewed project state durable.",
        version_label="Version 1",
        change_notes=("Updated the readme", "Kept provider selection internal"),
        reviewed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=confirmed,
    )


def _service(tmp_path: Path, *, local_forge=None) -> ProjectCommitService:
    forge = local_forge or LocalProjectForge(
        root=tmp_path / "forge",
        source_roots=(tmp_path,),
    )
    return ProjectCommitService(
        registry=_registry(),
        local_forge=forge,
        workspace_base=tmp_path,
    )


def test_local_only_commit_stores_durable_version_and_exact_message(tmp_path: Path):
    repo = _make_repo(tmp_path)
    service = _service(tmp_path)

    report = service.commit(
        request=_request(),
        policy=ProjectForgePolicy(forge_mode="local"),
        owner_id="owner@example.com",
        idempotency_key="commit-request-1",
    )

    expected_body = (
        "Keep the reviewed project state durable.\n\n"
        "Change notes:\n"
        "- Updated the readme\n"
        "- Kept provider selection internal"
    )
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "feat: retain project version"
    assert _git(repo, "log", "-1", "--pretty=%b").strip() == expected_body
    assert "Version 1" not in _git(repo, "log", "-1", "--pretty=%B")
    assert report.commit_sha == _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    assert report.result.overall_status == "committed"
    assert report.result.provider_statuses == ()
    assert report.result.retry_scheduled is False
    assert report.transaction_evidence["transaction_id"] == report.result.transaction_id
    assert report.version_evidence["version_id"] == report.version_id
    payload = report.manifest_evidence["payload"]
    assert payload["version_label"] == "Version 1"
    assert payload["change_notes"] == ["Updated the readme", "Kept provider selection internal"]
    serialized = json.dumps(report.to_dict())
    assert str(tmp_path) not in serialized
    assert "command_results" not in serialized


def test_github_with_nextcloud_backup_only_records_sync_pending(tmp_path: Path):
    _make_repo(tmp_path)
    service = _service(tmp_path)
    policy = ProjectForgePolicy(forge_mode="github", backup_providers=("nextcloud",))

    report = service.commit_project(
        request=_request(),
        policy=policy,
        owner_id="owner@example.com",
        idempotency_key="commit-request-2",
    )

    assert report.result.overall_status == "sync_pending"
    assert report.result.retry_scheduled is True
    assert {item.provider: item.status for item in report.result.provider_statuses} == {
        "nextcloud": "sync_pending",
        "github": "sync_pending",
    }
    assert all(item.retryable for item in report.result.provider_statuses)


class _RecordingForge:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def store_commit(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("simulated local Forge failure")
        raise AssertionError("successful fake storage was not expected")


def test_git_failure_never_calls_local_forge(tmp_path: Path):
    repo = _make_repo(tmp_path)
    forge = _RecordingForge()
    service = _service(tmp_path, local_forge=forge)

    with pytest.raises(ProjectCommitServiceError) as captured:
        service.commit(
            request=_request(confirmed=False),
            policy=ProjectForgePolicy(),
            owner_id="owner@example.com",
            idempotency_key="commit-request-3",
        )

    assert captured.value.code == "git_commit_failed"
    assert forge.calls == []
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_local_forge_failure_after_commit_requires_reconciliation_without_rollback(tmp_path: Path):
    repo = _make_repo(tmp_path)
    forge = _RecordingForge(fail=True)
    service = _service(tmp_path, local_forge=forge)

    with pytest.raises(ProjectCommitServiceError) as captured:
        service.commit(
            request=_request(),
            policy=ProjectForgePolicy(forge_mode="github", backup_providers=("nextcloud",)),
            owner_id="owner@example.com",
            idempotency_key="commit-request-4",
        )

    assert captured.value.code == "reconcile_required"
    assert captured.value.commit_sha == _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    assert len(forge.calls) == 1
    assert forge.calls[0]["source_repo"] == repo
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "feat: retain project version"
    assert _git(repo, "status", "--short").strip() == ""
    assert captured.value.to_dict()["status"] == "reconcile_required"


def test_request_provider_field_remains_fail_closed():
    with pytest.raises(ProjectForgeContractError, match="unknown fields: provider"):
        ProjectCommitRequest.from_dict(
            {
                "repo_id": "demo",
                "title": "feat: update",
                "description": "Safe update",
                "reviewed_paths": ["README.md"],
                "checks_passed": True,
                "content_reviewed": True,
                "confirmed": True,
                "provider": "github",
            }
        )


def test_service_blocks_owner_mismatch_and_repo_root_escape_before_git(tmp_path: Path):
    repo = _make_repo(tmp_path)
    service = _service(tmp_path)

    with pytest.raises(ProjectCommitServiceError) as owner_error:
        service.commit(
            request=_request(),
            policy=ProjectForgePolicy(),
            owner_id="different@example.com",
            idempotency_key="commit-request-5",
        )
    assert owner_error.value.code == "owner_mismatch"
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"

    escaped = ProjectCommitService(
        registry=_registry(),
        local_forge=LocalProjectForge(root=tmp_path / "other-forge", source_roots=(tmp_path,)),
        workspace_base=tmp_path,
        repo_roots={"demo": tmp_path.parent},
    )
    with pytest.raises(ProjectCommitServiceError) as root_error:
        escaped.commit(
            request=_request(),
            policy=ProjectForgePolicy(),
            owner_id="owner@example.com",
            idempotency_key="commit-request-6",
        )
    assert root_error.value.code == "repo_outside_workspace"
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"
