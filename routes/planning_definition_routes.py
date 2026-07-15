"""Fail-closed read routes for immutable Planning Definition v2 revisions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.planning_agent_handoff import (
    PlanningAgentHandoffError,
    build_agent_plan_handoff,
)
from src.planning_definition_projection import origin_metadata
from src.planning_revision_store import (
    PlanningRevisionRepository,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)


OwnerResolver = Callable[[Request], str | None]
RequestGate = Callable[[Request], Any]


def setup_planning_definition_routes(
    store: PlanningRevisionStore,
    *,
    write_service: PlanningRevisionRepository | None = None,
    owner_resolver: OwnerResolver | None = None,
    admin_gate: RequestGate | None = None,
    csrf_gate: RequestGate | None = None,
) -> APIRouter:
    if not isinstance(store, PlanningRevisionStore):
        raise ValueError("store must be a PlanningRevisionStore")
    resolve_owner = owner_resolver or _deny_owner
    require_admin = admin_gate or _deny_gate
    require_csrf = csrf_gate or _deny_gate
    router = APIRouter(prefix="/api/planning", tags=["planning-definitions"])

    def scope(request: Request, *, mutate: bool = False) -> str:
        try:
            owner = str(resolve_owner(request) or "").strip()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Not authenticated") from exc
        if not owner:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            decision = require_admin(request)
        except HTTPException:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise HTTPException(status_code=403, detail="Admin only") from exc
        if decision is not True and decision is not None:
            raise HTTPException(status_code=403, detail="Admin only")
        if mutate:
            try:
                csrf_decision = require_csrf(request)
            except HTTPException:
                raise
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                raise HTTPException(status_code=403, detail="CSRF validation failed") from exc
            if csrf_decision is not True and csrf_decision is not None:
                raise HTTPException(status_code=403, detail="CSRF validation failed")
        return owner

    def active_store() -> PlanningRevisionStore:
        return write_service.snapshot_store() if write_service is not None else store

    @router.get("/projects")
    def list_projects(
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(lambda: active_store().list_projects(scope(request), cursor=cursor, limit=limit))

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, request: Request):
        return _read(lambda: active_store().get_project(scope(request), project_id))

    @router.get("/projects/{project_id}/roadmaps")
    def list_roadmaps(
        project_id: str,
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(
            lambda: active_store().list_roadmaps(
                scope(request),
                project_id,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.get("/projects/{project_id}/roadmaps/{roadmap_id}")
    def get_roadmap(
        project_id: str,
        roadmap_id: str,
        request: Request,
        revision: str = Query(default="latest_approved", max_length=32),
    ):
        return _read(
            lambda: active_store().get_roadmap(
                scope(request),
                project_id,
                roadmap_id,
                revision=revision,
            )
        )

    @router.get("/projects/{project_id}/roadmaps/{roadmap_id}/revisions")
    def list_revisions(
        project_id: str,
        roadmap_id: str,
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(
            lambda: active_store().list_revisions(
                scope(request),
                project_id,
                roadmap_id,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/agent-handoff")
    async def create_agent_handoff(
        project_id: str,
        roadmap_id: str,
        request: Request,
    ):
        owner = scope(request)
        body = await _json_object(request, required={"revision", "content_hash"})

        def build() -> dict[str, Any]:
            try:
                read_model = active_store().get_roadmap(
                    owner,
                    project_id,
                    roadmap_id,
                    revision=body["revision"],
                )
                return build_agent_plan_handoff(
                    read_model,
                    expected_revision=body["revision"],
                    expected_hash=body["content_hash"],
                )
            except PlanningAgentHandoffError as exc:
                raise PlanningRevisionStoreError(
                    exc.code,
                    exc.detail,
                    origin_state="live",
                ) from exc

        return _read(build)

    @router.post("/projects/{project_id}/roadmaps/{roadmap_id}/drafts")
    async def create_draft(project_id: str, roadmap_id: str, request: Request):
        owner = scope(request, mutate=True)
        service = _write_service(write_service)
        body = await _json_object(
            request,
            required={"base_revision", "base_hash", "idempotency_key", "changes"},
        )
        return await _write_async(
            service.create_draft,
            owner,
            project_id,
            roadmap_id,
            base_revision=body["base_revision"],
            base_hash=body["base_hash"],
            idempotency_key=body["idempotency_key"],
            changes=body["changes"],
        )

    @router.post(
        "/projects/{project_id}/roadmaps/{roadmap_id}/drafts/{draft_id}/validate"
    )
    async def validate_draft(
        project_id: str,
        roadmap_id: str,
        draft_id: str,
        request: Request,
    ):
        owner = scope(request, mutate=True)
        service = _write_service(write_service)
        body = await _json_object(request, required={"expected_draft_version"})
        return await _write_async(
            service.validate_draft,
            owner,
            project_id,
            roadmap_id,
            draft_id,
            expected_draft_version=body["expected_draft_version"],
        )

    @router.post(
        "/projects/{project_id}/roadmaps/{roadmap_id}/drafts/{draft_id}/actions"
    )
    async def draft_action(
        project_id: str,
        roadmap_id: str,
        draft_id: str,
        request: Request,
    ):
        owner = scope(request, mutate=True)
        service = _write_service(write_service)
        body = await _json_object(
            request,
            required={"action", "expected_draft_version", "idempotency_key"},
        )
        return await _write_async(
            service.act_on_draft,
            owner,
            project_id,
            roadmap_id,
            draft_id,
            action=body["action"],
            expected_draft_version=body["expected_draft_version"],
            idempotency_key=body["idempotency_key"],
        )

    return router


def setup_default_planning_definition_routes(
    *,
    definitions_root: str | Path | None = None,
) -> APIRouter:
    """Compose read-only local definitions without a provider or runtime client."""

    from core.middleware import require_admin
    from routes.project_versioning_routes import _same_origin_csrf_gate
    from src.auth_helpers import effective_user
    from src.constants import DATA_DIR

    local_owner = str(os.getenv("ODYSSEUS_SINGLE_USER_OWNER") or "local-user").strip()
    root = Path(definitions_root or (Path(DATA_DIR) / "planning" / "definitions"))
    store = PlanningRevisionStore.from_directory(
        root,
        owner=local_owner,
        cursor_secret=secrets.token_bytes(32),
    )

    def resolve_owner(request: Request) -> str | None:
        owner = str(effective_user(request) or "").strip()
        if owner:
            return owner
        if os.getenv("AUTH_ENABLED", "true").strip().lower() == "false":
            return local_owner
        return None

    return setup_planning_definition_routes(
        store,
        owner_resolver=resolve_owner,
        admin_gate=require_admin,
        csrf_gate=_same_origin_csrf_gate,
    )


def _read(operation: Callable[[], dict[str, Any]]) -> dict[str, Any] | JSONResponse:
    try:
        return operation()
    except HTTPException:
        raise
    except PlanningRevisionStoreError as exc:
        status = _status_for(exc.code, exc.origin_state)
        return JSONResponse(
            status_code=status,
            content={
                "error": exc.code,
                "origin": origin_metadata(
                    exc.origin_state,
                    source="planning_revision_store",
                    reason=exc.code,
                    as_of="1970-01-01T00:00:00Z",
                ),
                "read_only": True,
                "raw_private_content_visible": False,
            },
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "error": "planning_read_unavailable",
                "origin": origin_metadata(
                    "error",
                    source="planning_revision_store",
                    reason="planning_read_unavailable",
                    as_of="1970-01-01T00:00:00Z",
                ),
                "read_only": True,
                "raw_private_content_visible": False,
            },
        )


def _status_for(code: str, origin_state: str) -> int:
    if code in {
        "project_not_found",
        "roadmap_not_found",
        "revision_not_found",
        "approved_revision_not_found",
        "draft_not_found",
        "undo_not_found",
    }:
        return 404
    if code == "invalid_cursor":
        return 400
    if code in {"planning_write_not_configured", "planning_write_gate_required"}:
        return 403
    if code in {
        "content_hash_mismatch",
        "dependency_cycle",
        "duplicate_id",
        "execution_state_forbidden",
        "invalid_completion_reference",
        "invalid_gate_target",
        "missing_field",
        "missing_reference",
        "runtime_field_forbidden",
        "unknown_field",
    }:
        return 422
    if code.startswith("invalid_") or code in {"owner_required"}:
        return 422
    if origin_state in {"unavailable", "error"}:
        return 503
    return 409


def _write_service(
    service: PlanningRevisionRepository | None,
) -> PlanningRevisionRepository:
    if service is None:
        raise HTTPException(status_code=403, detail="Planning writes are not configured")
    return service


async def _write_async(operation: Callable[..., dict[str, Any]], *args, **kwargs):
    return await asyncio.to_thread(_read, lambda: operation(*args, **kwargs))


async def _json_object(request: Request, *, required: set[str]) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON object") from exc
    if not isinstance(body, Mapping) or set(body) != required:
        raise HTTPException(status_code=422, detail="Request fields do not match the contract")
    return dict(body)


def _deny_owner(_request: Request) -> None:
    return None


def _deny_gate(_request: Request) -> bool:
    return False


__all__ = [
    "setup_default_planning_definition_routes",
    "setup_planning_definition_routes",
]
