import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.coding_agent_routes import setup_coding_agent_routes
from src.coding_agent_backend import CodingCheckCommand
from src.coding_project_scope import CodingProjectScopeError, resolve_coding_project_scope
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote


def _record(repo_id: str, title: str, project_root: str, *, branch_allowed: bool = True) -> RepoRecord:
    return RepoRecord.create(
        repo_id=repo_id,
        title=title,
        repo_kind="project",
        owner="fuzzy123-ai",
        path_ref=project_root,
        workspace_root="repos",
        project_root=project_root,
        default_branch="main",
        current_branch="dev",
        remotes=[
            RepoRemote.create(
                name="fuzzy",
                url="https://github.com/fuzzy123-ai/demo.git",
                purpose="fork",
                push_policy="push_allowed",
            )
        ],
        allowed_actions=[
            "status",
            "log",
            "diff_stat",
            "changed_paths",
            *([] if not branch_allowed else ["branch"]),
        ],
        created_at="2026-07-03T09:00:00Z",
    )


def _registry(path: Path | None = None) -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(_record("demo-api", "Demo App API", "repos/demo-api"))
    registry.add(_record("demo-web", "Demo App Web", "repos/demo-web"))
    registry.add(_record("billing", "Billing Backend", "repos/billing", branch_allowed=False))
    if path is not None:
        registry.save_json(path)
    return registry


def test_resolves_exact_repo_id_to_bounded_owner_safe_scope():
    check = CodingCheckCommand.create(argv=["python", "-m", "pytest", "tests/test_api.py", "-q"])
    resolution = resolve_coding_project_scope(
        registry=_registry(),
        project="demo-api",
        slice_id="roadmap-1",
        allowed_paths=["src", "tests"],
        checks=[check],
    )

    assert resolution.resolved is True
    assert resolution.repo_id == "demo-api"
    assert resolution.allowed_paths == ("src", "tests")
    assert resolution.branch_policy["base_ref"] == "dev"
    assert resolution.branch_policy["remote_name"] == "fuzzy"
    assert resolution.sandbox_policy["network_allowed"] is False
    assert resolution.sandbox_policy["operator_go_required"] is True
    assert resolution.to_dict()["raw_content_visible"] is False
    assert "project_query" not in resolution.to_dict()


def test_ambiguous_project_name_blocks_with_candidate_decision():
    resolution = resolve_coding_project_scope(registry=_registry(), project="Demo App", owner="fuzzy123-ai")

    assert resolution.resolved is False
    assert resolution.status == "blocked"
    assert resolution.blockers == ("project reference is ambiguous",)
    assert "Choose one repo_id" in resolution.next_human_decision
    assert [candidate.repo_id for candidate in resolution.candidates] == ["demo-api", "demo-web"]
    dumped = json.dumps(resolution.to_dict())
    assert "C:\\" not in dumped
    assert '"project_query":' not in dumped


def test_scope_rejects_absolute_or_blocked_paths():
    with pytest.raises(CodingProjectScopeError):
        resolve_coding_project_scope(
            registry=_registry(),
            project="demo-api",
            allowed_paths=["C:/Users/nkatz/odysseus/src"],
        )

    with pytest.raises(CodingProjectScopeError):
        resolve_coding_project_scope(
            registry=_registry(),
            project="demo-api",
            allowed_paths=[".git"],
        )


def test_branch_policy_blocks_when_repo_registry_disallows_branch_action():
    resolution = resolve_coding_project_scope(registry=_registry(), project="billing")

    assert resolution.resolved is False
    assert "repo registry does not allow branch/worktree actions" in resolution.blockers
    assert resolution.branch_policy["branch_action_allowed"] is False


def test_project_scope_route_returns_redacted_resolution(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    registry_path = tmp_path / "repos.json"
    _registry(registry_path)
    app = FastAPI()
    app.include_router(
        setup_coding_agent_routes(
            registry_path=registry_path,
            workspace_base=tmp_path,
            worktree_base=tmp_path / "worktrees",
            runner_state_dir=tmp_path / "runner-state",
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/coding-agent/project-scope",
        json={
            "project": "demo-api",
            "allowed_paths": ["src", "tests"],
            "checks": [{"argv": ["git", "status", "--short", "--branch"]}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["project_scope"]["target_ref"] == "repo:demo-api"
    assert payload["project_scope"]["allowed_paths"] == ["src", "tests"]
    assert str(tmp_path) not in json.dumps(payload)


def test_project_scope_route_rejects_unsafe_check(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    registry_path = tmp_path / "repos.json"
    _registry(registry_path)
    app = FastAPI()
    app.include_router(setup_coding_agent_routes(registry_path=registry_path, workspace_base=tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/coding-agent/project-scope",
        json={
            "project": "demo-api",
            "checks": [{"argv": ["powershell", "-Command", "Get-ChildItem"]}],
        },
    )

    assert response.status_code == 400
    assert "check command is not allowed" in response.json()["detail"]
