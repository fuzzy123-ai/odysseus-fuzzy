import json
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.server_project_routes import setup_server_project_routes
from src.agent_tools.project_commit_tools import build_default_commit_project_handler
from src.project_forge_local import LocalProjectForge
from src.project_forge_outbox import ProjectForgeOutbox
from src.project_forge_policy import ProjectForgePolicyStore
from src.repo_registry import RepoRegistry


def _client(path: Path, *, projects_root: Path | None = None, forge_registry_path: Path | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        setup_server_project_routes(
            registry_path=path,
            projects_root=projects_root or path.parent / "server-projects",
            forge_registry_path=forge_registry_path or path.parent / "forge-repos.json",
            owner_resolver=lambda _request: "test-owner",
            admin_gate=lambda _request: True,
            csrf_gate=lambda _request: True,
        )
    )
    return TestClient(app)


def test_project_routes_create_list_get_and_bind_chat(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    client = _client(registry_path)

    created = client.post(
        "/api/projects",
        json={"title": "Kundenportal MVP", "project_type": "app"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["success"] is True
    assert body["project"]["project_spec"]["project_slug"] == "kundenportal-mvp"
    assert body["project"]["project_spec"]["chat_scope"] == "project:kundenportal-mvp"

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert listed.json()["project_count"] == 1
    assert listed.json()["projects"][0]["project_slug"] == "kundenportal-mvp"

    fetched = client.get("/api/projects/kundenportal-mvp")
    assert fetched.status_code == 200
    assert fetched.json()["project"]["project_spec"]["repo_name"] == "kundenportal-mvp"

    bound = client.post("/api/projects/kundenportal-mvp/chat-bind", json={"session_id": "chat-1"})
    assert bound.status_code == 200
    context = bound.json()["context"]
    assert context["project_slug"] == "kundenportal-mvp"
    assert context["chat_scope"] == "project:kundenportal-mvp"
    assert "chat-1" not in json.dumps(bound.json()["audit"])

    stored = json.loads(registry_path.read_text(encoding="utf-8"))
    assert stored["projects"][0]["chat_session_ids"] == ["chat-1"]


def test_project_routes_preview_mobile_project_intake(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    client = _client(registry_path)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/intake/preview",
        json={
            "source_channel": "telegram",
            "text": "#project:kundenportal-mvp TODO: Login als MVP Slice aufnehmen.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    intake = body["intake"]
    assert intake["status"] == "review"
    assert intake["candidate_project"]["project_slug"] == "kundenportal-mvp"
    assert intake["tasks"][0]["title"] == "Login als MVP Slice aufnehmen."
    assert intake["raw_content_visible"] is False


def test_project_routes_preview_for_project_forces_target_project(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    client = _client(registry_path)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/kundenportal-mvp/intake/preview",
        json={"source_channel": "telegram", "text": "TODO: Release Smoke fuer unterwegs."},
    )

    assert response.status_code == 200
    intake = response.json()["intake"]
    assert intake["candidate_project"]["project_slug"] == "kundenportal-mvp"
    assert intake["candidate_project"]["confidence"] == 1.0
    assert "forced_project" in intake["candidate_project"]["reasons"]


def test_project_routes_apply_reviewed_project_intake(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200
    preview = client.post(
        "/api/projects/kundenportal-mvp/intake/preview",
        json={"source_channel": "telegram", "text": "TODO: Release Smoke fuer unterwegs."},
    ).json()["intake"]

    blocked = client.post(
        "/api/projects/kundenportal-mvp/intake/apply",
        json={"proposal": preview, "review_confirmed": False, "applied_by": "telegram"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["success"] is False
    assert "review_not_confirmed" in blocked.json()["intake_apply"]["blockers"]

    applied = client.post(
        "/api/projects/kundenportal-mvp/intake/apply",
        json={"proposal": preview, "review_confirmed": True, "applied_by": "telegram"},
    )
    assert applied.status_code == 200
    body = applied.json()
    assert body["success"] is True
    assert body["intake_apply"]["task_count"] == 1
    assert body["intake_apply"]["ledger_path"] == "project_intake_ledger.json"
    ledger = projects_root / "kundenportal-mvp" / ".odysseus" / "project_intake_ledger.json"
    assert ledger.is_file()
    assert str(projects_root) not in json.dumps(body)


def test_project_routes_merge_intake_into_project_state(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200
    preview = client.post(
        "/api/projects/kundenportal-mvp/intake/preview",
        json={
            "source_channel": "telegram",
            "text": (
                "TODO: Release Smoke unterwegs pruefbar machen.\n"
                "Risiko: DSGVO Review vor Livegang."
            ),
        },
    ).json()["intake"]
    apply_body = client.post(
        "/api/projects/kundenportal-mvp/intake/apply",
        json={"proposal": preview, "review_confirmed": True, "applied_by": "telegram"},
    ).json()

    merged = client.post("/api/projects/kundenportal-mvp/intake/merge", json={})
    assert merged.status_code == 200
    merge_payload = merged.json()
    assert merge_payload["success"] is True
    assert merge_payload["intake_merge"]["added_task_count"] == 1
    assert merge_payload["intake_merge"]["added_risk_count"] == 1
    assert merge_payload["intake_merge"]["state_path"] == "project_state.json"

    state = client.get("/api/projects/kundenportal-mvp/intake/state")
    assert state.status_code == 200
    state_payload = state.json()["intake_state"]
    assert state_payload["tasks"][0]["title"] == "Release Smoke unterwegs pruefbar machen."
    assert state_payload["risks"][0]["text"] == "DSGVO Review vor Livegang."
    assert state_payload["processed_event_ids"] == [apply_body["intake_apply"]["event_id"]]
    assert str(projects_root) not in json.dumps(merge_payload)


def test_project_routes_preview_rejects_secret_like_intake(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    client = _client(registry_path)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/intake/preview",
        json={"text": "#project:kundenportal-mvp password=abc123456789"},
    )

    assert response.status_code == 400
    assert "secret material" in response.json()["detail"]


def test_project_routes_provision_workspace_requires_go_then_creates(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    blocked = client.post("/api/projects/kundenportal-mvp/provision", json={})
    assert blocked.status_code == 200
    blocked_body = blocked.json()
    assert blocked_body["success"] is False
    assert blocked_body["provisioning"]["executed"] is False
    assert "operator decision is not go" in blocked_body["provisioning"]["blockers"]
    assert not (projects_root / "kundenportal-mvp").exists()

    created = client.post(
        "/api/projects/kundenportal-mvp/provision",
        json={"live_enabled": True, "operator_decision": "go"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["success"] is True
    assert body["provisioning"]["status"] == "provisioned"
    assert (projects_root / "kundenportal-mvp" / "repo").is_dir()
    assert (projects_root / "kundenportal-mvp" / ".odysseus" / "project.json").is_file()
    assert str(projects_root) not in json.dumps(body)


def test_project_routes_repo_provision_reports_gate_without_live_git(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/kundenportal-mvp/repo-provision",
        json={"remote_provider": "github", "remote_namespace": "fuzzy123-ai"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["repo_provisioning"]["executed"] is False
    assert "operator decision is not go" in body["repo_provisioning"]["blockers"]
    assert body["repo_provisioning"]["plan"]["remote_provider"] == "github"
    assert "fuzzy123-ai/kundenportal-mvp" in body["repo_provisioning"]["plan"]["provider_gate"]
    assert str(projects_root) not in json.dumps(body)


def test_project_routes_task_run_reports_gate_without_live_execution(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/kundenportal-mvp/task-run",
        json={
            "objective": "Add an app entrypoint",
            "file_writes": [{"path": "src/app.py", "content": "print('hi')\n"}],
            "checks": [{"argv": ["python", "-m", "pytest", "tests", "-q"]}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["task_run"]["executed"] is False
    assert "operator decision is not go" in body["task_run"]["blockers"]
    assert body["task_run"]["plan"]["repo_directory"] == "projects/kundenportal-mvp/repo"
    assert str(projects_root) not in json.dumps(body)


def test_project_routes_planner_task_run_adapts_to_task_plan_without_live_execution(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/kundenportal-mvp/planner-task-run",
        json={
            "objective": "Add an app entrypoint",
            "file_writes": [{"path": "src/app.py", "content": "print('hi')\n"}],
            "acceptance_criteria": ["entrypoint exists"],
            "check_profile": "auto",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["planner_task"]["executed"] is False
    planner = body["planner_task"]["planner"]
    assert planner["ready_for_task_runner"] is True
    assert planner["task_plan"]["checks"][0]["argv"] == ["python", "-m", "pytest", "tests", "-q"]
    assert "operator decision is not go" in planner["task_plan"]["blockers"]
    assert str(projects_root) not in json.dumps(body)


def test_project_routes_commit_run_is_retired_in_favor_of_single_commit_route(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/kundenportal-mvp/commit-run",
        json={
            "objective": "Add an app entrypoint",
            "changed_paths": ["src/app.py"],
            "checks_passed": False,
        },
    )

    assert response.status_code == 410
    assert "/api/project-versioning/{repo_id}/commit" in response.json()["detail"]
    assert str(projects_root) not in json.dumps(response.json())


def test_project_routes_push_run_is_retired_in_favor_of_policy_sync(tmp_path: Path):
    registry_path = tmp_path / "projects.json"
    projects_root = tmp_path / "server-projects"
    client = _client(registry_path, projects_root=projects_root)
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200

    response = client.post(
        "/api/projects/kundenportal-mvp/push-run",
        json={
            "branch": "project/kundenportal-mvp/work",
            "commit_ref": "abc1234",
            "commit_confirmed": False,
        },
    )

    assert response.status_code == 410
    assert "project policy" in response.json()["detail"]
    assert str(projects_root) not in json.dumps(response.json())


def test_project_routes_reject_duplicate_and_unknown_project(tmp_path: Path):
    client = _client(tmp_path / "projects.json")

    assert client.post("/api/projects", json={"title": "Demo"}).status_code == 200
    duplicate = client.post("/api/projects", json={"title": "Demo"})
    missing = client.get("/api/projects/missing")
    missing_bind = client.post("/api/projects/missing/chat-bind", json={"session_id": "chat-1"})

    assert duplicate.status_code == 400
    assert "project already exists" in duplicate.json()["detail"]
    assert missing.status_code == 404
    assert missing_bind.status_code == 404
    assert client.post("/api/projects/missing/provision", json={"live_enabled": True, "operator_decision": "go"}).status_code == 404
    assert client.post("/api/projects/missing/repo-provision", json={}).status_code == 404
    assert client.post(
        "/api/projects/missing/intake/apply",
        json={"proposal": {}, "review_confirmed": True},
    ).status_code == 404
    assert client.get("/api/projects/missing/intake/state").status_code == 404
    assert client.post("/api/projects/missing/intake/merge", json={}).status_code == 404
    assert client.post(
        "/api/projects/missing/task-run",
        json={"objective": "x", "file_writes": [], "checks": []},
    ).status_code == 404
    assert client.post(
        "/api/projects/missing/planner-task-run",
        json={"objective": "x", "file_writes": [], "checks": [], "acceptance_criteria": []},
    ).status_code == 404
    assert client.post(
        "/api/projects/missing/commit-run",
        json={"objective": "x", "changed_paths": [], "checks_passed": False},
    ).status_code == 410
    assert client.post(
        "/api/projects/missing/push-run",
        json={"branch": "project/demo/work", "commit_ref": "abc1234", "commit_confirmed": False},
    ).status_code == 410


def test_repo_provision_binds_external_project_root_to_canonical_commit_handler(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_PROJECT_PROVISIONER_LIVE_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_PROJECT_REPO_PROVISIONER_LIVE_ENABLED", "true")
    registry_path = tmp_path / "projects.json"
    forge_registry_path = tmp_path / "forge-repos.json"
    projects_root = tmp_path / "external-data" / "server-projects"
    client = _client(
        registry_path,
        projects_root=projects_root,
        forge_registry_path=forge_registry_path,
    )
    assert client.post("/api/projects", json={"title": "Kundenportal MVP", "project_type": "app"}).status_code == 200
    assert client.post(
        "/api/projects/kundenportal-mvp/provision",
        json={"live_enabled": True, "operator_decision": "go"},
    ).json()["success"] is True
    provisioned = client.post(
        "/api/projects/kundenportal-mvp/repo-provision",
        json={"live_enabled": True, "operator_decision": "go", "remote_provider": "none"},
    )
    assert provisioned.status_code == 200 and provisioned.json()["success"] is True

    forge_registry = RepoRegistry.load_json(forge_registry_path)
    bound = forge_registry.get("kundenportal-mvp")
    assert bound.owner == "test-owner"
    assert bound.path_ref == "kundenportal-mvp/repo"
    assert bound.linked_project_slug == "kundenportal-mvp"

    repo = projects_root / "kundenportal-mvp" / "repo"
    subprocess.run(["git", "config", "user.name", "Odysseus Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "odysseus@example.test"], cwd=repo, check=True)
    (repo / "README.md").write_text("durable project\n", encoding="utf-8")
    workspace = tmp_path / "odysseus-workspace"
    workspace.mkdir()
    handler = build_default_commit_project_handler(
        workspace=workspace,
        server_projects_root=projects_root,
        repo_registry_path=forge_registry_path,
        repo_id="kundenportal-mvp",
        local_forge=LocalProjectForge(
            root=tmp_path / "local-forge",
            source_roots=(workspace, projects_root),
        ),
        outbox=ProjectForgeOutbox(root=tmp_path / "outbox"),
        policy_source=ProjectForgePolicyStore(root=tmp_path / "policies"),
    )
    result = handler.handle(
        {
            "repo_id": "kundenportal-mvp",
            "title": "feat: retain server project",
            "description": "Store the reviewed server project in the local Forge.",
            "reviewed_paths": ["README.md"],
            "checks_passed": True,
            "content_reviewed": True,
            "confirmed": True,
            "idempotency_key": "server-project-route-e2e-1",
        },
        context={"is_authenticated": True, "authenticated_owner_id": "test-owner"},
    )
    assert result["status"] == "committed"
    assert result["local_status"] == "committed"
    assert result["provider_statuses"] == {}


def test_server_project_factory_defaults_fail_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = FastAPI()
    app.include_router(
        setup_server_project_routes(
            registry_path=tmp_path / "projects.json",
            projects_root=tmp_path / "server-projects",
            forge_registry_path=tmp_path / "forge-repos.json",
        )
    )
    assert TestClient(app).get("/api/projects").status_code == 401


def test_project_routes_reject_secret_like_input(tmp_path: Path):
    client = _client(tmp_path / "projects.json")

    response = client.post("/api/projects", json={"title": "Secret TOKEN=abc123 Project"})

    assert response.status_code == 400
    assert "secret material" in response.json()["detail"]


def test_server_project_routes_source_has_no_live_runtime():
    source = Path("routes/server_project_routes.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "podman", "docker", "systemctl", "cloudflared")
    for fragment in forbidden:
        assert fragment not in source
    assert "run_project_local_commit" not in source
    assert "run_project_push" not in source
    assert "ProjectCommitRunRequest" not in source
    assert "ProjectPushRunRequest" not in source
