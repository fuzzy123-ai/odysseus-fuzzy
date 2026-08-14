import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.coding_agent_routes import setup_coding_agent_routes
from routes.server_project_routes import setup_server_project_routes
from src.repo_registry import RepoRecord, RepoRegistry


def _repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "demo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _repo_registry(path: Path) -> None:
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
            allowed_actions=["status", "log", "diff_stat", "changed_paths", "branch"],
            created_at="2026-07-06T10:00:00Z",
        )
    )
    registry.save_json(path)


def _coding_client(tmp_path: Path) -> TestClient:
    _repo_root(tmp_path)
    registry_path = tmp_path / "repos.json"
    _repo_registry(registry_path)
    app = FastAPI()
    app.include_router(
        setup_coding_agent_routes(
            registry_path=registry_path,
            workspace_base=tmp_path,
            worktree_base=tmp_path / "worktrees",
            runner_state_dir=tmp_path / "runner-state",
        )
    )
    return TestClient(app)


def _project_client(path: Path, *, projects_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(
        setup_server_project_routes(
            registry_path=path,
            projects_root=projects_root,
            owner_resolver=lambda _request: "test-owner",
            admin_gate=lambda _request: True,
            csrf_gate=lambda _request: True,
        )
    )
    return TestClient(app)


def test_coding_task_plan_route_adds_lifecycle_and_identifiers_without_removing_legacy_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _coding_client(tmp_path)

    response = client.post(
        "/api/coding-agent/repos/demo/task-plan",
        json={
            "objective": "Add route compatibility",
            "allowed_paths": ["routes", "tests"],
            "checks": [{"argv": ["python", "-m", "pytest", "tests/test_coding_route_compatibility.py", "-q"]}],
            "task_id": "route-compat",
            "operator_decision": "go",
            "live_enabled": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["coding_task"]["task_id"] == "route-compat"
    assert payload["runner_state"]["phase"] == "blocked"
    assert payload["runner_state"]["gates_waiting"] == ["planning_authority"]
    assert payload["coding_lifecycle"]["schema"] == "odysseus.coding_lifecycle.v1"
    assert payload["coding_lifecycle"]["coding_task_id"] == "route-compat"
    assert payload["coding_lifecycle_identifiers"]["schema"] == "odysseus.coding_lifecycle.identifier_map.v1"
    assert payload["coding_lifecycle_identifiers"]["repo_id"] == "demo"
    assert payload["coding_lifecycle_identifiers"]["check_job_ids"] == ["route-compat-check-1"]
    dumped = json.dumps(payload, default=str)
    assert str(tmp_path) not in dumped


def test_coding_quality_and_sandbox_routes_add_canonical_alignment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _coding_client(tmp_path)

    quality = client.post(
        "/api/coding-agent/quality-gate",
        json={
            "changed_paths": ["routes/coding_agent_routes.py"],
            "allowed_paths": ["routes"],
            "check_results": [{"exit_code": 0, "stdout": "ok"}],
        },
    )
    sandbox = client.post(
        "/api/coding-agent/repos/demo/sandbox-checks",
        json={
            "objective": "Run route compatibility checks",
            "allowed_paths": ["routes", "tests"],
            "checks": [{"argv": ["python", "-m", "pytest", "tests/test_coding_route_compatibility.py", "-q"]}],
            "task_id": "route-compat",
            "operator_decision": "go",
            "live_enabled": True,
        },
    )

    assert quality.status_code == 200
    assert quality.json()["quality_gate"]["verified"] is True
    assert quality.json()["coding_quality_alignment"]["schema"] == "odysseus.coding_quality_alignment.v1"
    assert quality.json()["coding_quality_alignment"]["quality_gate"]["schema"] == "gate_evidence_core.v1"
    assert sandbox.status_code == 200
    sandbox_payload = sandbox.json()
    assert sandbox_payload["sandbox_dispatch"]["statuses"][0]["status"] == "dry_run"
    assert sandbox_payload["coding_quality_alignment"]["sandbox_gate"]["status"] == "blocked"
    assert "no changed paths were provided" in sandbox_payload["coding_quality_alignment"]["sandbox_gate"]["blockers"]
    assert sandbox_payload["coding_lifecycle"]["schema"] == "odysseus.coding_lifecycle.v1"
    assert sandbox_payload["coding_lifecycle_identifiers"]["check_job_ids"] == ["route-compat-check-1"]


def test_server_project_routes_add_identifier_map_to_project_and_task_responses(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _project_client(tmp_path / "projects.json", projects_root=tmp_path / "server-projects")

    created = client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"})
    task = client.post(
        "/api/projects/kundenportal-mvp/task-run",
        json={
            "objective": "Add entrypoint",
            "file_writes": [{"path": "src/app.py", "content": "print('hi')\n"}],
            "checks": [{"argv": ["python", "-m", "pytest", "tests", "-q"]}],
        },
    )

    assert created.status_code == 200
    assert created.json()["project"]["project_spec"]["project_slug"] == "kundenportal-mvp"
    created_ids = created.json()["coding_lifecycle_identifiers"]
    assert created_ids["schema"] == "odysseus.coding_lifecycle.identifier_map.v1"
    assert created_ids["server_project_id"] == "kundenportal-mvp"
    assert created_ids["repo_id"] == "kundenportal-mvp"
    assert task.status_code == 200
    task_payload = task.json()
    assert task_payload["task_run"]["executed"] is False
    assert task_payload["coding_lifecycle_identifiers"]["server_project_task_id"].startswith("server-project-task:")
    assert task_payload["coding_lifecycle_identifiers"]["check_job_ids"][0].endswith("-check-1")
    assert str(tmp_path / "server-projects") not in json.dumps(task_payload, default=str)
