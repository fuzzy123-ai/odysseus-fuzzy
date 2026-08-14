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


def _planning_binding(
    *,
    item_id: str = "roadmap-1",
    revision: str = "plan-rev-1",
    allowed_paths: list[str] | None = None,
    status: str = "validated",
) -> dict[str, object]:
    return {
        "status": status,
        "planning_item_id": item_id,
        "canonical_plan_revision": revision,
        "acceptance_contract": "acceptance-contract-1",
        "allowed_paths": allowed_paths or ["src", "tests"],
        "gate_requirements": ["machine_auto", "agent_auto"],
    }


def test_resolves_exact_repo_id_to_bounded_owner_safe_scope():
    check = CodingCheckCommand.create(argv=["python", "-m", "pytest", "tests/test_api.py", "-q"])
    resolution = resolve_coding_project_scope(
        registry=_registry(),
        project="demo-api",
        slice_id="roadmap-1",
        allowed_paths=["src", "tests"],
        checks=[check],
        planning_binding=_planning_binding(),
    )

    assert resolution.resolved is True
    assert resolution.repo_id == "demo-api"
    assert resolution.allowed_paths == ("src", "tests")
    assert resolution.branch_policy["base_ref"] == "dev"
    assert resolution.branch_policy["remote_name"] == "fuzzy"
    assert resolution.sandbox_policy["network_allowed"] is False
    assert resolution.sandbox_policy["operator_go_required"] is True
    assert resolution.to_dict()["raw_content_visible"] is False
    assert resolution.to_dict()["planning"]["canonical_plan_revision"] == "plan-rev-1"
    assert resolution.scope_digest.startswith("sha256:")
    assert "project_query" not in resolution.to_dict()


def test_ambiguous_project_name_blocks_with_candidate_decision():
    resolution = resolve_coding_project_scope(
        registry=_registry(), project="Demo App", owner="fuzzy123-ai", planning_binding=_planning_binding()
    )

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
            allowed_paths=["X:/fixtures/odysseus/src"],
            planning_binding=_planning_binding(),
        )

    with pytest.raises(CodingProjectScopeError):
        resolve_coding_project_scope(
            registry=_registry(),
            project="demo-api",
            allowed_paths=[".git"],
            planning_binding=_planning_binding(),
        )


def test_branch_policy_blocks_when_repo_registry_disallows_branch_action():
    resolution = resolve_coding_project_scope(
        registry=_registry(), project="billing", planning_binding=_planning_binding()
    )

    assert resolution.resolved is False
    assert "repo registry does not allow branch/worktree actions" in resolution.blockers
    assert resolution.branch_policy["branch_action_allowed"] is False


def test_project_scope_route_without_planning_binding_fails_closed(tmp_path: Path, monkeypatch):
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
    assert payload["success"] is False
    assert payload["project_scope"]["status"] == "blocked"
    assert payload["project_scope"]["blockers"] == ["Planning authority blocked scope: planning_data_missing"]
    assert payload["project_scope"]["planning"]["authoritative"] is False
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


@pytest.mark.parametrize("status", ["missing", "stale", "ambiguous", "conflicting"])
def test_scope_blocks_missing_stale_or_ambiguous_planning(status: str):
    resolution = resolve_coding_project_scope(
        registry=_registry(),
        project="demo-api",
        planning_binding=_planning_binding(status=status),
    )

    assert resolution.status == "blocked"
    assert resolution.resolved is False
    assert resolution.planning_binding is None
    assert "Planning authority blocked scope" in resolution.blockers[0]


def test_scope_rejects_requested_path_or_revision_conflicting_with_planning():
    binding = _planning_binding(item_id="acpr-11", allowed_paths=["src", "tests"])
    path_conflict = resolve_coding_project_scope(
        registry=_registry(),
        project="demo-api",
        slice_id="acpr-11",
        allowed_paths=["src", "routes"],
        planning_binding=binding,
    )
    revision_conflict = resolve_coding_project_scope(
        registry=_registry(),
        project="demo-api",
        planning_binding={**binding, "planning_revision": "plan-rev-foreign"},
    )

    assert path_conflict.status == "blocked"
    assert "conflict" in path_conflict.blockers[0]
    assert revision_conflict.status == "blocked"
    assert "planning_data_ambiguous" in revision_conflict.blockers[0]
