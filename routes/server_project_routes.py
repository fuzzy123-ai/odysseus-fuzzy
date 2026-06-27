"""API routes for universal server project registry and chat binding."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.constants import DATA_DIR
from src.server_project_chat_context import (
    ServerProjectChatContextError,
    bind_project_chat_session,
)
from src.server_project_provisioner import (
    ServerProjectProvisioningError,
    provision_project_workspace,
)
from src.server_project_registry import ServerProjectRegistry, ServerProjectRegistryError
from src.server_project_repo_provisioner import (
    ServerProjectRepoProvisioningError,
    provision_project_local_git_repo,
)
from src.server_project_task_runner import (
    ProjectTaskCheck,
    ProjectTaskFileWrite,
    ServerProjectTaskRunnerError,
    run_project_task,
)
from src.server_project_task_planner import (
    ServerProjectTaskPlannerError,
    run_planner_task,
)


DEFAULT_PROJECT_REGISTRY_PATH = Path(DATA_DIR) / "server_project_registry.json"
DEFAULT_PROJECTS_ROOT = Path(DATA_DIR) / "server_projects"


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    project_type: str = "generic"
    repo_name: str | None = None
    cloudflare_tunnel_requested: bool = False


class ProjectChatBindRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)


class ProjectProvisionRequest(BaseModel):
    live_enabled: bool = False
    operator_decision: str = "missing"


class ProjectRepoProvisionRequest(BaseModel):
    live_enabled: bool = False
    operator_decision: str = "missing"
    remote_provider: str = "none"
    remote_namespace: str = ""
    default_branch: str | None = None


class ProjectTaskFileWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=180)
    content: str = Field(max_length=256000)


class ProjectTaskCheckRequest(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=12)
    timeout_seconds: int = 300


class ProjectTaskRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=500)
    file_writes: list[ProjectTaskFileWriteRequest] = Field(default_factory=list)
    checks: list[ProjectTaskCheckRequest] = Field(default_factory=list)
    live_enabled: bool = False
    operator_decision: str = "missing"


class ProjectPlannerTaskRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=500)
    file_writes: list[ProjectTaskFileWriteRequest] = Field(default_factory=list)
    checks: list[ProjectTaskCheckRequest] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    check_profile: str = "auto"
    live_enabled: bool = False
    operator_decision: str = "missing"


def setup_server_project_routes(
    *,
    registry_path: str | Path = DEFAULT_PROJECT_REGISTRY_PATH,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["server-projects"])
    registry_file = Path(registry_path)
    configured_projects_root = Path(projects_root)

    @router.get("")
    def list_projects() -> dict[str, Any]:
        registry = _load_registry(registry_file)
        return registry.audit_summary()

    @router.post("")
    def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            record = registry.create_project(
                project_title=body.title,
                project_type=body.project_type,
                repo_name=body.repo_name,
                cloudflare_tunnel_requested=body.cloudflare_tunnel_requested,
                created_at=_now_iso(),
            )
        except ServerProjectRegistryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _save_registry(registry_file, registry)
        return {"success": True, "project": record.to_dict()}

    @router.get("/{project_slug}")
    def get_project(project_slug: str) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            record = registry.get(project_slug)
        except ServerProjectRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"project": record.to_dict()}

    @router.post("/{project_slug}/chat-bind")
    def bind_chat(project_slug: str, body: ProjectChatBindRequest) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            context = bind_project_chat_session(
                registry=registry,
                project_slug=project_slug,
                session_id=body.session_id,
                updated_at=_now_iso(),
            )
        except ServerProjectChatContextError as exc:
            status = 404 if "unknown project" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        _save_registry(registry_file, registry)
        return {"success": True, "context": context.metadata(), "audit": context.audit_summary()}

    @router.post("/{project_slug}/provision")
    def provision_workspace(project_slug: str, body: ProjectProvisionRequest) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            record = registry.get(project_slug)
            report = provision_project_workspace(
                record=record,
                projects_root=configured_projects_root,
                created_at=_now_iso(),
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
        except ServerProjectRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ServerProjectProvisioningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": report.executed, "provisioning": report.to_dict()}

    @router.post("/{project_slug}/repo-provision")
    def provision_repo(project_slug: str, body: ProjectRepoProvisionRequest) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            record = registry.get(project_slug)
            report = provision_project_local_git_repo(
                record=record,
                projects_root=configured_projects_root,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
                remote_provider=body.remote_provider,
                remote_namespace=body.remote_namespace,
                default_branch=body.default_branch,
            )
        except ServerProjectRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ServerProjectRepoProvisioningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": report.executed and report.status == "provisioned", "repo_provisioning": report.to_dict()}

    @router.post("/{project_slug}/task-run")
    def run_task(project_slug: str, body: ProjectTaskRunRequest) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            record = registry.get(project_slug)
            report = run_project_task(
                record=record,
                projects_root=configured_projects_root,
                objective=body.objective,
                file_writes=tuple(
                    ProjectTaskFileWrite.create(path=item.path, content=item.content)
                    for item in body.file_writes
                ),
                checks=tuple(
                    ProjectTaskCheck.create(argv=item.argv, timeout_seconds=item.timeout_seconds)
                    for item in body.checks
                ),
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
        except ServerProjectRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ServerProjectTaskRunnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": report.executed and report.status == "completed", "task_run": report.to_dict()}

    @router.post("/{project_slug}/planner-task-run")
    def run_planned_task(project_slug: str, body: ProjectPlannerTaskRunRequest) -> dict[str, Any]:
        registry = _load_registry(registry_file)
        try:
            record = registry.get(project_slug)
            report = run_planner_task(
                record=record,
                projects_root=configured_projects_root,
                objective=body.objective,
                file_writes=tuple({"path": item.path, "content": item.content} for item in body.file_writes),
                checks=tuple(
                    {"argv": item.argv, "timeout_seconds": item.timeout_seconds}
                    for item in body.checks
                ),
                acceptance_criteria=body.acceptance_criteria,
                check_profile=body.check_profile,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
        except ServerProjectRegistryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ServerProjectTaskPlannerError, ServerProjectTaskRunnerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": report.executed and report.status == "completed", "planner_task": report.to_dict()}

    return router


def _load_registry(path: Path) -> ServerProjectRegistry:
    if not path.exists():
        return ServerProjectRegistry()
    try:
        return ServerProjectRegistry.load_json(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="project registry is unreadable") from exc


def _save_registry(path: Path, registry: ServerProjectRegistry) -> None:
    try:
        registry.save_json(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="project registry could not be saved") from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
