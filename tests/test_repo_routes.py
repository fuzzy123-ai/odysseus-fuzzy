from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.repo_routes import setup_repo_routes
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _make_repo(base: Path) -> tuple[Path, str, str]:
    repo = base / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "checkout", "-b", "main")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    branch = _git(repo, "branch", "--show-current")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "fuzzy", "https://github.com/fuzzy123-ai/demo.git")
    readme.write_text("two\n", encoding="utf-8")
    return repo, branch, head


def _write_registry(path: Path, *, push: bool = True) -> None:
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
            remotes=[
                RepoRemote.create(
                    name="fuzzy",
                    url="https://github.com/fuzzy123-ai/demo.git",
                    purpose="fork",
                    push_policy="push_allowed" if push else "read_only",
                )
            ],
            allowed_actions=[
                "status",
                "log",
                "diff_stat",
                "changed_paths",
                "remotes",
                "changes",
                "change_history",
                "commit_plan",
                "push_plan",
                *([] if not push else ["push"]),
            ],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    registry.save_json(path)


def _client(registry_path: Path, *, workspace_base: Path, changes_history_dir: Path | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        setup_repo_routes(
            registry_path=registry_path,
            workspace_base=workspace_base,
            changes_history_dir=changes_history_dir,
        )
    )
    return TestClient(app)


def test_repo_routes_require_admin_by_default(tmp_path: Path):
    client = _client(tmp_path / "repo-registry.json", workspace_base=tmp_path)

    response = client.get("/api/repos")

    assert response.status_code == 403


def test_repo_routes_register_list_get_and_patch_policy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    registry_path = tmp_path / "repo-registry.json"
    client = _client(registry_path, workspace_base=tmp_path)
    payload = {
        "title": "Demo Repo",
        "owner": "fuzzy123-ai",
        "path_ref": "repos/demo",
        "workspace_root": "repos",
        "project_root": "repos/demo",
        "remotes": [
            {
                "name": "fuzzy",
                "url": "https://x-access-token:secret-value@github.com/fuzzy123-ai/demo.git?token=abc",
                "purpose": "fork",
                "push_policy": "push_allowed",
            }
        ],
        "allowed_actions": ["status", "changes", "push"],
    }

    blocked = client.post("/api/repos/register", json=payload)
    payload["confirmed"] = True
    registered = client.post("/api/repos/register", json=payload)
    listed = client.get("/api/repos")
    fetched = client.get("/api/repos/demo-repo")
    patch_blocked = client.patch("/api/repos/demo-repo/policy", json={"privacy_class": "public"})
    patched = client.patch(
        "/api/repos/demo-repo/policy",
        json={"privacy_class": "public", "provider_scope": "default", "confirmed": True},
    )

    assert blocked.status_code == 400
    assert "confirmed=true" in blocked.json()["detail"]
    assert registered.status_code == 200
    assert registered.json()["repo"]["repo_id"] == "demo-repo"
    assert listed.json()["repo_count"] == 1
    assert fetched.json()["repo"]["remotes"][0]["url_redacted"] == "https://github.com/fuzzy123-ai/demo.git"
    assert patch_blocked.status_code == 400
    assert patched.json()["repo"]["privacy_class"] == "public"
    dumped = json.dumps(
        {
            "registered": registered.json(),
            "listed": listed.json(),
            "fetched": fetched.json(),
            "patched": patched.json(),
        },
        ensure_ascii=True,
    )
    assert "secret-value" not in dumped
    assert "x-access-token" not in dumped
    assert str(tmp_path) not in dumped


def test_repo_routes_status_changes_commit_plan_and_push_plan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _repo, branch, head = _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    changes_history_dir = tmp_path / "repo-change-history"
    _write_registry(registry_path)
    client = _client(registry_path, workspace_base=tmp_path, changes_history_dir=changes_history_dir)

    status = client.get("/api/repos/demo/status")
    changes = client.get("/api/repos/demo/changes?hours=24")
    commit_plan = client.post(
        "/api/repos/demo/commit-plan",
        json={
            "objective": "Update README",
            "changed_paths": ["README.md"],
            "checks_passed": True,
            "content_reviewed": True,
        },
    )
    push_plan = client.post(
        "/api/repos/demo/push-plan",
        json={"remote_name": "fuzzy", "branch_name": branch, "commit_sha": head},
    )

    assert status.status_code == 200
    assert status.json()["git"]["status"]["dirty"] is True
    assert any("README.md" in item for item in status.json()["git"]["status"]["entries"])
    assert changes.status_code == 200
    assert changes.json()["repo_changes"]["snapshot"]["repo_id"] == "demo"
    assert changes.json()["repo_changes"]["memory_records"][0]["source"] == "repo_recent_changes"
    assert commit_plan.status_code == 200
    assert commit_plan.json()["success"] is False
    assert "confirmed=true" in json.dumps(commit_plan.json()["commit_report"]["blockers"])
    assert push_plan.status_code == 200
    assert push_plan.json()["success"] is False
    assert "operator_go=true" in json.dumps(push_plan.json()["push_report"]["blockers"])
    dumped = json.dumps(
        {
            "status": status.json(),
            "changes": changes.json(),
            "commit": commit_plan.json(),
            "push": push_plan.json(),
        },
        ensure_ascii=True,
    )
    assert "repo_root" not in dumped
    assert "numstat" not in dumped
    assert str(tmp_path) not in dumped


def test_repo_routes_unknown_repo_and_secret_input_are_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client(tmp_path / "repo-registry.json", workspace_base=tmp_path)

    missing = client.get("/api/repos/missing")
    secret = client.post(
        "/api/repos/register",
        json={
            "title": "Secret TOKEN=abc123 Project",
            "owner": "fuzzy123-ai",
            "workspace_root": "repos",
            "project_root": "repos/demo",
            "confirmed": True,
        },
    )

    assert missing.status_code == 404
    assert secret.status_code == 400
    assert "secret material" in secret.json()["detail"]


def test_repo_routes_source_stays_plan_only_for_commit_and_push():
    source = Path("routes/repo_routes.py").read_text(encoding="utf-8")

    assert "run_repo_push" not in source
    assert "run_repo_local_commit" not in source
    assert "subprocess" not in source
