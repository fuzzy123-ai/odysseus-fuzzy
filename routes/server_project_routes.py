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
from src.server_project_registry import ServerProjectRegistry, ServerProjectRegistryError


DEFAULT_PROJECT_REGISTRY_PATH = Path(DATA_DIR) / "server_project_registry.json"


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    project_type: str = "generic"
    repo_name: str | None = None
    cloudflare_tunnel_requested: bool = False


class ProjectChatBindRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)


def setup_server_project_routes(
    *,
    registry_path: str | Path = DEFAULT_PROJECT_REGISTRY_PATH,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["server-projects"])
    registry_file = Path(registry_path)

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
