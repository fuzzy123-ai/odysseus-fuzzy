from __future__ import annotations

import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.recent_changes_routes as recent_changes_routes


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _client(monkeypatch, repo, history_dir, admin_calls):
    import src.recent_changes as recent_changes

    monkeypatch.setattr(recent_changes, "BASE_DIR", str(repo))
    monkeypatch.setattr(recent_changes, "RECENT_CHANGES_DIR", str(history_dir))
    monkeypatch.setattr(
        recent_changes_routes,
        "require_admin",
        lambda request: admin_calls.append(str(request.url.path)),
    )
    app = FastAPI()
    app.include_router(recent_changes_routes.setup_recent_changes_routes())
    return TestClient(app)


def test_recent_changes_route_is_admin_gated_and_returns_redacted_snapshot(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    env_file = repo / ".env"
    env_file.write_text("TOKEN=old\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    env_file.write_text("TOKEN=new\n", encoding="utf-8")
    useful = repo / "src" / "route_gate.py"
    useful.parent.mkdir()
    useful.write_text("print('ok')\n", encoding="utf-8")

    admin_calls = []
    client = _client(monkeypatch, repo, tmp_path / "history", admin_calls)

    response = client.get("/api/system/recent-changes?hours=12&persist=false")

    assert response.status_code == 200
    assert admin_calls == ["/api/system/recent-changes"]
    dumped = response.text
    assert "src/route_gate.py" in dumped
    assert ".env" not in dumped
    assert "TOKEN=" not in dumped
    assert str(repo) not in dumped


def test_recent_changes_route_rejects_without_admin(monkeypatch):
    monkeypatch.setattr(
        recent_changes_routes,
        "require_admin",
        lambda request: (_ for _ in ()).throw(HTTPException(status_code=403)),
    )
    app = FastAPI()
    app.include_router(recent_changes_routes.setup_recent_changes_routes())
    client = TestClient(app)

    response = client.get("/api/system/recent-changes/history")

    assert response.status_code == 403
