"""Read-only readiness routes for non-UI roadmap gate packets."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from core.middleware import require_admin
from scripts.non_ui_gate_readiness import build_non_ui_gate_readiness, render_markdown


def setup_non_ui_gate_readiness_routes() -> APIRouter:
    router = APIRouter(tags=["non-ui-gates"])

    @router.get("/api/non-ui-gates/readiness")
    def non_ui_gate_readiness(request: Request):
        require_admin(request)
        return build_non_ui_gate_readiness()

    @router.get("/api/non-ui-gates/readiness.md", response_class=PlainTextResponse)
    def non_ui_gate_readiness_markdown(request: Request):
        require_admin(request)
        return render_markdown(build_non_ui_gate_readiness())

    return router
