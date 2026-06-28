"""API routes for registered repo control and UI-facing repo plans."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.constants import BASE_DIR
from src.repo_commit_runner import RepoCommitRunnerError, plan_repo_local_commit
from src.repo_git_adapter import RepoGitAdapter, RepoGitAdapterError
from src.repo_push_runner import RepoPushRunnerError, plan_repo_push
from src.repo_recent_memory import RepoRecentMemoryError, collect_repo_change_capsule
from src.repo_registry import REPO_REGISTRY_FILE, RepoRecord, RepoRegistry, RepoRegistryError, RepoRemote


class RepoRemoteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str | None = None
    url_redacted: str | None = None
    purpose: str = "other"
    push_policy: str = "read_only"


class RepoRegisterRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    owner: str = Field(min_length=1, max_length=120)
    repo_id: str | None = None
    repo_kind: str = "project"
    path_ref: str | None = None
    workspace_root: str
    project_root: str
    system_root: str = ""
    default_branch: str = "main"
    current_branch: str = ""
    remotes: list[RepoRemoteRequest] = Field(default_factory=list)
    privacy_class: str = "private"
    provider_scope: str | None = None
    allowed_actions: list[str] | None = None
    linked_project_slug: str = ""
    confirmed: bool = False


class RepoPolicyPatchRequest(BaseModel):
    privacy_class: str | None = None
    provider_scope: str | None = None
    allowed_actions: list[str] | None = None
    remotes: list[RepoRemoteRequest] | None = None
    confirmed: bool = False


class RepoCommitPlanRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=500)
    changed_paths: list[str] = Field(default_factory=list)
    checks_passed: bool = False
    content_reviewed: bool = False
    commit_message: str | None = None
    confirmed: bool = False


class RepoPushPlanRequest(BaseModel):
    remote_name: str = "fuzzy"
    branch_name: str = Field(min_length=1, max_length=160)
    commit_sha: str = Field(min_length=7, max_length=40)
    confirmed: bool = False
    operator_go: bool = False
    live_enabled: bool | None = None


def setup_repo_routes(
    *,
    registry_path: str | Path = REPO_REGISTRY_FILE,
    workspace_base: str | Path = BASE_DIR,
    changes_history_dir: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/repos", tags=["repos"])
    registry_file = Path(registry_path)
    configured_workspace_base = Path(workspace_base)

    @router.get("")
    def list_repos(request: Request) -> dict[str, Any]:
        require_admin(request)
        return _load_registry(registry_file).audit_summary()

    @router.get("/{repo_id}")
    def get_repo(request: Request, repo_id: str) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            record = registry.get(repo_id)
        except RepoRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"repo": record.to_dict()}

    @router.post("/register")
    def register_repo(request: Request, body: RepoRegisterRequest) -> dict[str, Any]:
        require_admin(request)
        if not body.confirmed:
            raise HTTPException(status_code=400, detail="register requires confirmed=true")
        registry = _load_registry(registry_file)
        try:
            record = RepoRecord.create(
                repo_id=body.repo_id,
                title=body.title,
                repo_kind=body.repo_kind,
                owner=body.owner,
                path_ref=body.path_ref,
                workspace_root=body.workspace_root,
                project_root=body.project_root,
                system_root=body.system_root,
                default_branch=body.default_branch,
                current_branch=body.current_branch,
                remotes=tuple(_remote_from_request(item) for item in body.remotes),
                privacy_class=body.privacy_class,
                provider_scope=body.provider_scope,
                allowed_actions=body.allowed_actions,
                linked_project_slug=body.linked_project_slug,
                created_at=_now_iso(),
            )
            registry.add(record)
            _save_registry(registry_file, registry)
        except RepoRegistryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": True, "repo": record.to_dict()}

    @router.patch("/{repo_id}/policy")
    def patch_repo_policy(request: Request, repo_id: str, body: RepoPolicyPatchRequest) -> dict[str, Any]:
        require_admin(request)
        if not body.confirmed:
            raise HTTPException(status_code=400, detail="policy update requires confirmed=true")
        registry = _load_registry(registry_file)
        try:
            record = registry.get(repo_id)
            updated = record.with_policy(
                privacy_class=body.privacy_class,
                provider_scope=body.provider_scope,
                allowed_actions=body.allowed_actions,
                remotes=None if body.remotes is None else tuple(_remote_from_request(item) for item in body.remotes),
                updated_at=_now_iso(),
            )
            registry.put(updated)
            _save_registry(registry_file, registry)
        except RepoRegistryError as exc:
            status = 404 if str(exc).startswith("unknown repo") else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": True, "repo": updated.to_dict()}

    @router.get("/{repo_id}/status")
    def get_repo_status(request: Request, repo_id: str) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        adapter = RepoGitAdapter(registry=registry, workspace_base=configured_workspace_base)
        try:
            snapshot = adapter.snapshot(repo_id)
        except RepoRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RepoGitAdapterError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"repo": registry.get(repo_id).to_dict(), "git": snapshot.to_dict()}

    @router.get("/{repo_id}/changes")
    def get_repo_changes(
        request: Request,
        repo_id: str,
        hours: int = 12,
        persist: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            report = collect_repo_change_capsule(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                hours=hours,
                history_dir=changes_history_dir,
                persist=persist,
                force=force,
            )
        except RepoRecentMemoryError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": True, "repo_changes": report.to_dict()}

    @router.post("/{repo_id}/commit-plan")
    def create_commit_plan(request: Request, repo_id: str, body: RepoCommitPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            report = plan_repo_local_commit(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                changed_paths=body.changed_paths,
                checks_passed=body.checks_passed,
                content_reviewed=body.content_reviewed,
                confirmed=body.confirmed,
                commit_message=body.commit_message,
            )
        except RepoCommitRunnerError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.status == "plan_ready", "commit_report": report.to_dict()}

    @router.post("/{repo_id}/push-plan")
    def create_push_plan(request: Request, repo_id: str, body: RepoPushPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            report = plan_repo_push(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                remote_name=body.remote_name,
                branch_name=body.branch_name,
                commit_sha=body.commit_sha,
                confirmed=body.confirmed,
                operator_go=body.operator_go,
                live_enabled=body.live_enabled,
            )
        except RepoPushRunnerError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.status == "plan_ready", "push_report": report.to_dict()}

    return router


def _load_registry(path: Path) -> RepoRegistry:
    try:
        return RepoRegistry.load_or_empty(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="repo registry is unreadable") from exc


def _save_registry(path: Path, registry: RepoRegistry) -> None:
    try:
        registry.save_json(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="repo registry could not be saved") from exc


def _remote_from_request(item: RepoRemoteRequest) -> RepoRemote:
    return RepoRemote.create(
        name=item.name,
        url=item.url,
        url_redacted=item.url_redacted,
        purpose=item.purpose,
        push_policy=item.push_policy,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
