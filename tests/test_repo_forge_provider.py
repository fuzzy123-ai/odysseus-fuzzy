from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.repo_forge_provider import (
    RepoForgeMetadata,
    RepoForgeProviderError,
    build_repo_forge_plan,
    normalize_forge_metadata_payload,
    plan_repo_forge_metadata,
    run_repo_forge_metadata,
)
from src.repo_registry import RepoRecord, RepoRegistry


def _registry(*, privacy_class: str = "private", provider_scope: str = "external_allowed") -> RepoRegistry:
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
            privacy_class=privacy_class,
            provider_scope=provider_scope,
            allowed_actions=["status"],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


def test_forge_plan_blocks_without_auth_and_live_gates():
    report = plan_repo_forge_metadata(
        registry=_registry(),
        repo_id="demo",
        provider="github",
        namespace="fuzzy123-ai",
        repo_name="demo",
    )

    assert report.status == "hold"
    assert report.executed is False
    assert "auth_ready=true" in report.blockers[0]
    assert "confirmed=true" in report.blockers[1]
    assert report.plan.provider_gate.startswith("github/fuzzy123-ai/demo")
    assert "token" not in json.dumps(report.to_dict()).lower()
    assert "C:\\" not in json.dumps(report.to_dict())


def test_forge_metadata_runs_with_fake_provider_after_gates():
    calls = []

    def fake_provider(request):
        calls.append(request)
        return RepoForgeMetadata.create(
            provider=request.provider,
            namespace=request.namespace,
            repo_name=request.repo_name,
            default_branch="dev",
            permissions=("pull", "push"),
            issue_count=3,
            pull_request_count=2,
            private=True,
            html_url="https://github.com/fuzzy123-ai/demo",
            clone_url="https://token-secret@github.com/fuzzy123-ai/demo.git",
        )

    report = run_repo_forge_metadata(
        registry=_registry(),
        repo_id="demo",
        provider="github",
        namespace="fuzzy123-ai",
        repo_name="demo",
        auth_ready=True,
        confirmed=True,
        operator_go=True,
        live_enabled=True,
        metadata_provider=fake_provider,
    )

    dumped = json.dumps(report.to_dict())
    assert report.status == "fetched"
    assert report.executed is True
    assert report.metadata is not None
    assert report.metadata.default_branch == "dev"
    assert report.metadata.permissions == ("pull", "push")
    assert report.metadata.issue_count == 3
    assert report.metadata.pull_request_count == 2
    assert calls[0].repo_full_name == "fuzzy123-ai/demo"
    assert "token-secret" not in dumped
    assert "github.com/fuzzy123-ai/demo.git" in dumped


def test_forge_metadata_blocks_without_provider_client_after_gates():
    report = run_repo_forge_metadata(
        registry=_registry(),
        repo_id="demo",
        provider="gitea",
        namespace="fuzzy123-ai",
        repo_name="demo",
        api_base_url="https://gitea.example.test/api/v1",
        auth_ready=True,
        confirmed=True,
        operator_go=True,
        live_enabled=True,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "provider client is not configured" in report.blockers[0]
    assert report.plan.api_base_url_redacted == "https://gitea.example.test/api/v1"


def test_forge_plan_blocks_sensitive_repo_and_keeps_repo_creation_separate():
    plan = build_repo_forge_plan(
        record=_registry(privacy_class="sensitive", provider_scope="local_only").get("demo"),
        provider="forgejo",
        namespace="fuzzy123-ai",
        repo_name="demo",
        auth_ready=True,
        confirmed=True,
        operator_go=True,
        live_enabled=True,
        create_repo_requested=True,
    )

    assert plan.decision == "blocked"
    assert "sensitive repos" in plan.blockers[0]
    assert "separate confirmed live provider action" in plan.repo_creation_gate


def test_forge_payload_normalization_and_input_guards():
    metadata = normalize_forge_metadata_payload(
        {
            "name": "demo",
            "default_branch": "main",
            "permissions": {"admin": False, "push": True, "pull": True},
            "open_issues_count": 7,
            "open_pull_requests_count": 1,
            "private": False,
            "html_url": "https://github.com/fuzzy123-ai/demo",
            "clone_url": "https://oauth-token@example.invalid/fuzzy123-ai/demo.git",
        },
        provider="github",
        namespace="fuzzy123-ai",
        repo_name="demo",
    )

    assert metadata.permissions == ("push", "pull")
    assert metadata.issue_count == 7
    assert metadata.pull_request_count == 1
    assert metadata.clone_url_redacted == "https://example.invalid/fuzzy123-ai/demo.git"

    with pytest.raises(RepoForgeProviderError, match="unsupported provider"):
        plan_repo_forge_metadata(
            registry=_registry(),
            repo_id="demo",
            provider="gitlab",
            namespace="fuzzy123-ai",
            repo_name="demo",
        )

    with pytest.raises(RepoForgeProviderError, match="secret"):
        plan_repo_forge_metadata(
            registry=_registry(),
            repo_id="demo",
            provider="github",
            namespace="token=abc123",
            repo_name="demo",
        )


def test_source_has_no_live_provider_runtime():
    source = Path("src/repo_forge_provider.py").read_text(encoding="utf-8")

    forbidden = ("import requests", "import httpx", "paramiko", "cloudflared", "gh repo create")
    for fragment in forbidden:
        assert fragment not in source
