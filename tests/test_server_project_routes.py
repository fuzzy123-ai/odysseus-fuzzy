import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.server_project_routes import setup_server_project_routes


def _client(path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(setup_server_project_routes(registry_path=path))
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
