"""Admin-gated read-only Ops Console routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src.ops_console_snapshot import build_ops_console_snapshot
from src.ops_timeline_adapters import create_default_security_incident_store


def setup_ops_console_routes(
    snapshot_builder: Callable[[], dict[str, Any]] | None = None,
    *,
    incident_store: Any | None = None,
) -> APIRouter:
    router = APIRouter(tags=["ops-console"])
    builder = snapshot_builder or build_ops_console_snapshot
    configured_store = incident_store if incident_store is not None else (None if snapshot_builder is not None else create_default_security_incident_store())

    @router.get("/api/ops-console/snapshot")
    def ops_console_snapshot(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            # Established injected builders retain their zero-argument shape;
            # the registered default obtains durable state only from app state.
            if snapshot_builder is not None:
                return builder()
            return builder(store=getattr(request.app.state, "security_incident_store", configured_store))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, "Failed to build ops console snapshot") from exc

    return router
