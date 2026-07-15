from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from src.agent_tools.project_commit_tools import CommitProjectAgentTool, CommitProjectToolHandler
from src.project_commit_service import ProjectCommitServiceError, ProjectCommitServiceReport
from src.project_forge_contract import ProjectCommitResult, ProviderStatus
from src.project_forge_outbox import ProjectForgeOutbox
from src.project_forge_policy import ProjectForgePolicy, resolve_commit_providers
from src.project_forge_sync import ForgeSyncOutcome, ProjectForgeSyncCoordinator


project_commit_tools = importlib.import_module("src.agent_tools.project_commit_tools")


TRANSACTION = "pct_" + "a" * 32
VERSION = "pv_" + "b" * 32
COMMIT = "c" * 40
OWNER = "owner@example.test"


def _arguments(**overrides):
    values = {
        "repo_id": "demo",
        "title": "feat: keep generated project",
        "description": "Retain the reviewed project state.",
        "version_label": "Version 1",
        "change_notes": ["Keep the readable output"],
        "reviewed_paths": ["README.md", "src/main.py"],
        "checks_passed": True,
        "content_reviewed": True,
        "confirmed": True,
        "idempotency_key": "commit-project-request-1",
    }
    values.update(overrides)
    return values


def _context(**overrides):
    values = {"is_authenticated": True, "authenticated_owner_id": OWNER}
    values.update(overrides)
    return values


class FakePolicySource:
    def __init__(self, policy=None, error=None):
        self.policy = policy or ProjectForgePolicy()
        self.error = error
        self.calls = []

    def load_policy(self, **scope):
        self.calls.append(scope)
        if self.error:
            raise self.error
        return self.policy


class FakeCommitService:
    def __init__(self, *, error=None, malformed_manifest=False):
        self.error = error
        self.malformed_manifest = malformed_manifest
        self.calls = []

    def commit_project(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        policy = kwargs["policy"]
        providers = resolve_commit_providers(policy)
        result = ProjectCommitResult.create(
            transaction_id=TRANSACTION,
            repo_id="demo",
            commit_sha=COMMIT,
            local_status="committed",
            provider_statuses=tuple(
                ProviderStatus.create(
                    provider=provider,
                    status="sync_pending",
                    retryable=True,
                )
                for provider in providers
            ),
        )
        manifest = {
            "schema": "odysseus.project_version_manifest.v1",
            "sha256": "invalid" if self.malformed_manifest else "sha256:" + "d" * 64,
            "payload": {"private_path": "C:/must-not-escape"},
        }
        return ProjectCommitServiceReport(
            project_commit_result=result,
            transaction_evidence={"transaction_id": TRANSACTION, "status": "stored"},
            version_evidence={
                "version_id": VERSION,
                "commit_sha": COMMIT,
                "created_at": "2026-07-13T12:00:00Z",
            },
            manifest_evidence=manifest,
        )


class FakeAdapter:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def sync(self, request):
        self.calls.append(request)
        return self.outcome


class RaisingCoordinator:
    def __init__(self):
        self.calls = []

    def run_due(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("Bearer not-a-real-value C:/private/provider")


def _handler(tmp_path: Path, *, service=None, source=None, coordinator=None, inline=False):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    handler = CommitProjectToolHandler(
        commit_service=service or FakeCommitService(),
        policy_source=source or FakePolicySource(),
        outbox=outbox,
        sync_coordinator=coordinator,
        sync_inline_enabled=inline,
    )
    return handler, outbox


def test_local_commit_uses_authenticated_owner_and_persisted_policy_only(tmp_path):
    service = FakeCommitService()
    source = FakePolicySource(ProjectForgePolicy(forge_mode="local"))
    handler, outbox = _handler(tmp_path, service=service, source=source)

    result = handler.handle(_arguments(), context=_context())

    assert result == {
        "status": "committed",
        "error_code": "",
        "transaction_id": TRANSACTION,
        "repo_id": "demo",
        "commit_sha": COMMIT,
        "local_status": "committed",
        "provider_statuses": {},
        "overall_status": "committed",
        "retry_scheduled": False,
    }
    assert source.calls == [{"owner_id": OWNER, "repo_id": "demo"}]
    call = service.calls[0]
    assert call["owner_id"] == OWNER
    assert call["idempotency_key"] == "commit-project-request-1"
    assert call["request"].title == "feat: keep generated project"
    assert call["request"].description == "Retain the reviewed project state."
    assert call["request"].version_label == "Version 1"
    assert call["request"].change_notes == ("Keep the readable output",)
    assert outbox.list_operations(owner_id=OWNER, repo_id="demo") == ()


def test_optional_description_metadata_uses_existing_request_defaults(tmp_path):
    arguments = _arguments()
    arguments.pop("version_label")
    arguments.pop("change_notes")
    service = FakeCommitService()
    handler, _ = _handler(tmp_path, service=service)

    result = handler(arguments, context=_context())

    assert result["status"] == "committed"
    assert service.calls[0]["request"].version_label == ""
    assert service.calls[0]["request"].change_notes == ()


def test_github_and_nextcloud_are_enqueued_without_inline_network_or_provider_args(tmp_path):
    policy = ProjectForgePolicy(forge_mode="github", backup_providers=("nextcloud",))
    source = FakePolicySource(policy)
    service = FakeCommitService()
    coordinator = RaisingCoordinator()
    handler, outbox = _handler(
        tmp_path,
        service=service,
        source=source,
        coordinator=coordinator,
        inline=False,
    )

    result = handler(_arguments(), context=_context())

    assert result["transaction_id"] == TRANSACTION
    assert result["local_status"] == "committed"
    assert result["provider_statuses"] == {"github": "pending", "nextcloud": "pending"}
    assert result["overall_status"] == "sync_pending"
    assert result["retry_scheduled"] is True
    assert coordinator.calls == []
    assert {item.provider for item in outbox.list_operations(owner_id=OWNER, repo_id="demo")} == {
        "github",
        "nextcloud",
    }


def test_inline_fake_sync_updates_statuses_without_rolling_back_local_commit(tmp_path):
    policy = ProjectForgePolicy(forge_mode="github", backup_providers=("nextcloud",))
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    github = FakeAdapter(ForgeSyncOutcome(status="synced"))
    nextcloud = FakeAdapter(
        ForgeSyncOutcome(status="retryable_failure", error_code="remote_busy")
    )
    coordinator = ProjectForgeSyncCoordinator(
        outbox=outbox,
        adapters={"github": github, "nextcloud": nextcloud},
    )
    service = FakeCommitService()
    handler = CommitProjectToolHandler(
        commit_service=service,
        policy_source=FakePolicySource(policy),
        outbox=outbox,
        sync_coordinator=coordinator,
        sync_inline_enabled=True,
    )

    result = handler(_arguments(), context=_context())

    assert result["status"] == "committed"
    assert result["local_status"] == "committed"
    assert result["provider_statuses"] == {
        "github": "synced",
        "nextcloud": "retry_scheduled",
    }
    assert result["overall_status"] == "partial"
    assert result["retry_scheduled"] is True
    assert len(github.calls) == 1 and len(nextcloud.calls) == 1
    assert len(service.calls) == 1


def test_provider_exception_after_commit_returns_pending_without_raw_details(tmp_path):
    policy = ProjectForgePolicy(forge_mode="github")
    coordinator = RaisingCoordinator()
    service = FakeCommitService()
    handler, _ = _handler(
        tmp_path,
        service=service,
        source=FakePolicySource(policy),
        coordinator=coordinator,
        inline=True,
    )

    result = handler(_arguments(), context=_context())

    assert result["status"] == "local_committed"
    assert result["error_code"] == "sync_dispatch_failed"
    assert result["local_status"] == "committed"
    assert result["provider_statuses"] == {"github": "pending"}
    assert result["retry_scheduled"] is True
    assert "Bearer" not in json.dumps(result)
    assert "C:/private" not in json.dumps(result)
    assert len(service.calls) == 1


def test_enqueue_failure_keeps_verified_local_commit_in_result(tmp_path):
    service = FakeCommitService(malformed_manifest=True)
    handler, _ = _handler(
        tmp_path,
        service=service,
        source=FakePolicySource(ProjectForgePolicy(forge_mode="github")),
    )

    result = handler(_arguments(), context=_context())

    assert result["status"] == "local_committed"
    assert result["error_code"] == "sync_enqueue_failed"
    assert result["local_status"] == "committed"
    assert result["provider_statuses"] == {"github": "sync_pending"}
    assert result["retry_scheduled"] is True
    assert len(service.calls) == 1


@pytest.mark.parametrize(
    "overrides",
    (
        {"confirmed": False},
        {"checks_passed": False},
        {"content_reviewed": False},
    ),
)
def test_confirmation_and_checks_fail_closed_before_policy_or_commit(tmp_path, overrides):
    service = FakeCommitService()
    source = FakePolicySource()
    handler, _ = _handler(tmp_path, service=service, source=source)

    result = handler(_arguments(**overrides), context=_context())

    assert result["status"] == "blocked"
    assert result["error_code"] == "confirmation_required"
    assert source.calls == []
    assert service.calls == []


@pytest.mark.parametrize("forbidden", ("provider", "forge_mode", "remote", "owner_id", "retry"))
def test_provider_remote_owner_and_retry_arguments_are_not_public(tmp_path, forbidden):
    service = FakeCommitService()
    source = FakePolicySource()
    handler, _ = _handler(tmp_path, service=service, source=source)
    arguments = _arguments()
    arguments[forbidden] = "github"

    result = handler(arguments, context=_context())

    assert result["error_code"] == "unsupported_arguments"
    assert source.calls == []
    assert service.calls == []


def test_owner_requires_authenticated_context_and_never_comes_from_arguments(tmp_path):
    service = FakeCommitService()
    handler, _ = _handler(tmp_path, service=service)

    unauthenticated = handler(_arguments(), context=_context(is_authenticated=False))
    missing_owner = handler(
        _arguments(),
        context={"is_authenticated": True, "owner_id": OWNER},
    )

    assert unauthenticated["error_code"] == "invalid_request"
    assert missing_owner["error_code"] == "invalid_request"
    assert service.calls == []


def test_policy_and_service_failures_are_redacted_and_do_not_expose_paths(tmp_path):
    policy_handler, _ = _handler(
        tmp_path,
        source=FakePolicySource(error=RuntimeError("token=value C:/private/policy")),
    )
    service_handler, _ = _handler(
        tmp_path,
        service=FakeCommitService(error=RuntimeError("Bearer value /private/repo")),
    )

    policy_result = policy_handler(_arguments(), context=_context())
    service_result = service_handler(_arguments(), context=_context())

    assert policy_result["error_code"] == "policy_unavailable"
    assert service_result["error_code"] == "local_commit_failed"
    dumped = json.dumps((policy_result, service_result))
    assert "token" not in dumped.casefold()
    assert "bearer" not in dumped.casefold()
    assert "private" not in dumped.casefold()


def test_known_service_error_uses_safe_code_only(tmp_path):
    service = FakeCommitService(
        error=ProjectCommitServiceError(
            "owner_mismatch",
            "private path and provider output must not escape",
        )
    )
    handler, _ = _handler(tmp_path, service=service)

    result = handler(_arguments(), context=_context())

    assert result["error_code"] == "owner_mismatch"
    assert "private" not in json.dumps(result)


def test_source_exposes_one_commit_handler_and_no_provider_specific_operations():
    source = Path("src/agent_tools/project_commit_tools.py").read_text(encoding="utf-8")

    forbidden = (
        "def push_project",
        "def upload_project",
        "def retry_project",
        "def commit_github",
        "def commit_nextcloud",
    )
    assert source.count("def handle_commit_project(") == 1
    for fragment in forbidden:
        assert fragment not in source


def test_public_schema_and_registry_expose_only_commit_project_for_provider_work():
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS

    schemas = {
        entry["function"]["name"]: entry["function"]
        for entry in FUNCTION_TOOL_SCHEMAS
        if entry.get("type") == "function"
    }
    schema = schemas["commit_project"]
    properties = schema["parameters"]["properties"]
    assert "commit_project" in TOOL_HANDLERS
    assert "commit_project" in TOOL_TAGS
    assert not ({"provider", "forge_mode", "remote", "push", "retry"} & set(properties))
    assert {"title", "description", "confirmed", "checks_passed", "content_reviewed"}.issubset(properties)
    for forbidden_tool in ("git_commit", "github_push", "nextcloud_upload", "retry_sync"):
        assert forbidden_tool not in schemas
    manage_actions = set(
        schemas["manage_repos"]["parameters"]["properties"]["action"]["enum"]
    )
    assert {"commit", "push", "forge_metadata"}.isdisjoint(manage_actions)


def test_agent_adapter_uses_authenticated_execution_owner(monkeypatch, tmp_path):
    service = FakeCommitService()
    handler, _ = _handler(
        tmp_path,
        service=service,
        source=FakePolicySource(ProjectForgePolicy(forge_mode="local")),
    )
    monkeypatch.setattr(project_commit_tools, "_build_default_handler", lambda **_kwargs: handler)

    result = asyncio.run(
        CommitProjectAgentTool().execute(json.dumps(_arguments()), {"owner": OWNER})
    )

    assert result["exit_code"] == 0
    assert result["local_status"] == "committed"
    assert service.calls[0]["owner_id"] == OWNER


def test_agent_adapter_rejects_missing_owner_and_invalid_json():
    missing_owner = asyncio.run(
        CommitProjectAgentTool().execute(json.dumps(_arguments()), {})
    )
    invalid_json = asyncio.run(CommitProjectAgentTool().execute("{bad", {"owner": OWNER}))

    assert missing_owner["exit_code"] == 1
    assert missing_owner["error_code"] == "authenticated_owner_required"
    assert invalid_json["exit_code"] == 1
    assert invalid_json["error_code"] == "arguments_must_be_json"


def test_agent_adapter_uses_canonical_owner_when_auth_is_disabled(monkeypatch, tmp_path):
    service = FakeCommitService()
    handler, _ = _handler(tmp_path, service=service)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ODYSSEUS_SINGLE_USER_OWNER", "local-owner")
    monkeypatch.setattr(project_commit_tools, "_build_default_handler", lambda **_kwargs: handler)

    result = asyncio.run(CommitProjectAgentTool().execute(json.dumps(_arguments()), {}))

    assert result["exit_code"] == 0
    assert service.calls[0]["owner_id"] == "local-owner"
