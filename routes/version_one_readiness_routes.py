"""Admin-gated Version 1.0 readiness route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.version_one_readiness import load_version_one_readiness


def setup_version_one_readiness_routes() -> APIRouter:
    router = APIRouter(tags=["version-one"])

    @router.get("/api/version-one/readiness")
    def version_one_readiness(request: Request):
        require_admin(request)
        return load_version_one_readiness()

    return router
