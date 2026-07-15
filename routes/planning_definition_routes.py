"""Fail-closed read routes for immutable Planning Definition v2 revisions."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.planning_definition_projection import origin_metadata
from src.planning_revision_store import PlanningRevisionStore, PlanningRevisionStoreError


OwnerResolver = Callable[[Request], str | None]
RequestGate = Callable[[Request], Any]


def setup_planning_definition_routes(
    store: PlanningRevisionStore,
    *,
    owner_resolver: OwnerResolver | None = None,
    admin_gate: RequestGate | None = None,
) -> APIRouter:
    if not isinstance(store, PlanningRevisionStore):
        raise ValueError("store must be a PlanningRevisionStore")
    resolve_owner = owner_resolver or _deny_owner
    require_admin = admin_gate or _deny_gate
    router = APIRouter(prefix="/api/planning", tags=["planning-definitions"])

    def scope(request: Request) -> str:
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
        return owner

    @router.get("/projects")
    def list_projects(
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(lambda: store.list_projects(scope(request), cursor=cursor, limit=limit))

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, request: Request):
        return _read(lambda: store.get_project(scope(request), project_id))

    @router.get("/projects/{project_id}/roadmaps")
    def list_roadmaps(
        project_id: str,
        request: Request,
        cursor: str = Query(default="", max_length=1_024),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return _read(
            lambda: store.list_roadmaps(
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
            lambda: store.get_roadmap(
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
            lambda: store.list_revisions(
                scope(request),
                project_id,
                roadmap_id,
                cursor=cursor,
                limit=limit,
            )
        )

    return router


def setup_default_planning_definition_routes(
    *,
    definitions_root: str | Path | None = None,
) -> APIRouter:
    """Compose read-only local definitions without a provider or runtime client."""

    from core.middleware import require_admin
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
    }:
        return 404
    if code == "invalid_cursor":
        return 400
    if code.startswith("invalid_") or code in {"owner_required"}:
        return 422
    if origin_state in {"unavailable", "error"}:
        return 503
    return 409


def _deny_owner(_request: Request) -> None:
    return None


def _deny_gate(_request: Request) -> bool:
    return False


__all__ = [
    "setup_default_planning_definition_routes",
    "setup_planning_definition_routes",
]
