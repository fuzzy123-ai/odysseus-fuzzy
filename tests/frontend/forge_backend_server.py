"""Ephemeral real FastAPI Forge backend for the browser acceptance lane."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles
import uvicorn

from core.auth import AuthManager
from core.middleware import require_admin
from routes.auth_routes import SESSION_COOKIE
from routes.project_versioning_routes import setup_project_versioning_routes
from routes.repo_routes import setup_repo_routes
from src.agent_tools.project_commit_tools import CommitProjectToolHandler
from src.project_commit_service import ProjectCommitService
from src.project_forge_local import LocalProjectForge
from src.project_forge_outbox import ProjectForgeOutbox
from src.project_forge_policy import ProjectForgePolicyStore
from src.project_version_store import ProjectVersionStore
from src.repo_registry import RepoRecord, RepoRegistry


OWNER = "alice@example.test"


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("fixture git command failed") from None
    return result.stdout.strip()


def _make_repo(base: Path, repo_id: str, title: str, *, dirty: bool) -> tuple[Path, str]:
    repo = base / repo_id
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "feature/ui")
    _git(repo, "config", "user.email", "forge-test@example.test")
    _git(repo, "config", "user.name", "Forge Browser Test")
    (repo / "README.md").write_text(f"{title}\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial project state")
    commit = _git(repo, "rev-parse", "HEAD")
    if dirty:
        (repo / "README.md").write_text(f"{title}\nlocal review change\n", encoding="utf-8")
    return repo, commit


def _seed(root: Path, scenario: str) -> tuple[Path, Path, Path, Path, Path, Path]:
    workspace = root / "workspace"
    repos_dir = workspace / "repos"
    repos_dir.mkdir(parents=True)
    registry_path = root / "repo_registry.json"
    repo_registry = RepoRegistry()
    repo_specs = () if scenario == "empty" else (("alpha", "Alpha Workspace", True), ("beta", "Beta Workspace", False))
    for repo_id, title, dirty in repo_specs:
        repo, commit = _make_repo(repos_dir, repo_id, title, dirty=dirty)
        record = RepoRecord.create(
            repo_id=repo_id,
            title=title,
            repo_kind="project",
            owner="alice@example.test",
            path_ref=f"repos/{repo_id}",
            workspace_root="workspace",
            project_root=f"workspace/repos/{repo_id}",
            default_branch="feature/ui",
            current_branch="feature/ui",
            created_at="2026-10-01T10:00:00Z",
        )
        repo_registry.add(record)
    repo_registry.save_json(registry_path)

    policy_root = root / "policies"
    version_root = root / "versions"
    outbox_root = root / "outbox"
    auth_path = root / "auth.json"
    auth = AuthManager(str(auth_path))
    auth.setup(OWNER, "test-password-123")
    auth.create_user("viewer@example.test", "test-password-123", is_admin=False)
    for repo_id, title, dirty in repo_specs:
        source = repos_dir / repo_id
        commit = _git(source, "rev-parse", "HEAD")
        store = ProjectVersionStore(root=version_root)
        forge = LocalProjectForge(root=version_root, source_roots=(workspace,))
        policy = ProjectForgePolicyStore(root=policy_root).load_policy(owner_id=OWNER, repo_id=repo_id)
        forge.store_commit(
            owner_id=OWNER,
            repo_id=repo_id,
            source_repo=source,
            commit_sha=commit,
            idempotency_key=f"browser-{repo_id}",
            policy_snapshot=policy.to_dict(),
            version_label=f"{title} verified",
            change_notes=["Browser acceptance fixture"],
        )
    return registry_path, workspace, policy_root, version_root, outbox_root, auth_path


class _SessionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth: AuthManager):
        super().__init__(app)
        self.auth = auth

    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE)
        if token and self.auth.validate_token(token):
            request.state.current_user = self.auth.get_username_for_token(token)
        elif request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        return await call_next(request)


class _TestSessionRoute:
    def __init__(self, app: FastAPI, auth: AuthManager):
        self.auth = auth
        app.add_api_route("/__test__/session/{role}", self.issue, methods=["GET"])

    async def issue(self, role: str):
        username = OWNER if role == "admin" else "viewer@example.test" if role == "viewer" else ""
        if not username:
            return JSONResponse({"error": "unknown test role"}, status_code=404)
        fence = self.auth.capture_login_identity(username)
        token = self.auth.create_session_trusted(username, identity_fence=fence) if fence else None
        if not token:
            return JSONResponse({"error": "session issuance failed"}, status_code=401)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/")
        return response


def build_app(root: Path, scenario: str) -> FastAPI:
    registry_path, workspace, policy_root, version_root, outbox_root, auth_path = _seed(root, scenario)
    auth = AuthManager(str(auth_path))
    registry = RepoRegistry.load_json(registry_path)
    policy_store = ProjectForgePolicyStore(root=policy_root)
    local_forge = LocalProjectForge(root=version_root, source_roots=(workspace,))
    outbox = ProjectForgeOutbox(root=outbox_root)
    service = ProjectCommitService(registry=registry, local_forge=local_forge, workspace_base=workspace)
    handler = CommitProjectToolHandler(commit_service=service, policy_source=policy_store, outbox=outbox)
    app = FastAPI()
    app.state.auth_manager = auth
    app.add_middleware(_SessionMiddleware, auth=auth)
    app.include_router(setup_repo_routes(registry_path=registry_path, workspace_base=workspace))
    app.include_router(setup_project_versioning_routes(
        policy_store=policy_store,
        local_forge=local_forge,
        outbox=outbox,
        commit_handler=handler,
        owner_resolver=lambda request: getattr(request.state, "current_user", None),
        admin_gate=require_admin,
        csrf_gate=lambda request: (_ for _ in ()).throw(HTTPException(status_code=403, detail="CSRF validation failed")),
    ))
    _TestSessionRoute(app, auth)
    app.mount("/frontpage-v2", StaticFiles(directory=str(ROOT / "static" / "frontpage-v2"), html=False), name="frontpage-v2")
    app.mount("/", StaticFiles(directory=str(ROOT / "static" / "frontpage-v3"), html=True), name="frontpage")
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--scenario", choices=("multi", "empty"), default="multi")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    os.environ["AUTH_ENABLED"] = "true"
    app = build_app(Path(args.root).resolve(), args.scenario)
    port = args.port
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen()
    port = listener.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    print(f"PORT={port}", flush=True)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
