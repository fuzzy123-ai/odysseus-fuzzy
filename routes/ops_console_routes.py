"""Admin-gated read-only Ops Console routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src.ops_console_snapshot import build_ops_console_snapshot


def setup_ops_console_routes(
    snapshot_builder: Callable[[], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["ops-console"])
    builder = snapshot_builder or build_ops_console_snapshot

    @router.get("/api/ops-console/snapshot")
    def ops_console_snapshot(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            return builder()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, "Failed to build ops console snapshot") from exc

    return router
