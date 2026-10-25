"""Harbour MCP-First Primary Interface routes — /mcp."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from src.mcp_authenticated_dispatch import (
    MCP_CONTEXT_SCOPE,
    MCP_HARBOUR_INBOX_SCOPE,
    McpDispatchFailure,
    _AuthorityFinalizedPayload,
    authenticate_mcp_request,
    dispatch_failure_response,
    dispatch_http_payload,
    parse_json_body,
    read_bounded_body,
)

logger = logging.getLogger(__name__)


def setup_harbour_mcp_routes() -> APIRouter:
    router = APIRouter(tags=["harbour_mcp"])

    @router.get("/mcp")
    @router.get("/mcp/")
    async def harbour_mcp_get(request: Request) -> Response:
        return JSONResponse({
            "status": "ready",
            "primary_interface": "mcp",
            "server": "odysseus-harbour",
        })

    @router.post("/mcp")
    @router.post("/mcp/")
    async def harbour_mcp_endpoint(request: Request) -> Response:
        started = time.monotonic()
        if tuple(getattr(request.state, "api_token_scopes", ()) or ()) == (MCP_CONTEXT_SCOPE,):
            context_composition = getattr(request.app.state, "mcp_context_app", None)
            context_handler = getattr(context_composition, "handle_mcp_request", None) if context_composition else None
            if callable(context_handler):
                return await context_handler(request)
            return Response(
                content=b"",
                status_code=503,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

        try:
            identity = authenticate_mcp_request(request)
            bearer_proof = getattr(request.state, "_mcp_bearer_proof", None)
            if identity.scope == MCP_HARBOUR_INBOX_SCOPE and bearer_proof is None:
                raise ValueError
        except Exception:
            return JSONResponse(
                dispatch_failure_response("unauthorized"), status_code=401,
            )

        try:
            raw = await read_bounded_body(request, started)
            payload = parse_json_body(raw)

            if identity.scope == MCP_HARBOUR_INBOX_SCOPE:
                from src.harbour_inbox_service import HarbourInboxService
                runtime = HarbourInboxService(
                    auth_manager=request.app.state.auth_manager,
                    bearer_proof=bearer_proof,
                    planning_runtime=getattr(request.app.state, "planning_mcp_runtime", None),
                    knowledge_composition=getattr(request.app.state, "knowledge_composition", None),
                    upload_handler=getattr(request.app.state, "upload_handler", None),
                )
            else:
                runtime = getattr(request.app.state, "planning_mcp_runtime", None)
                if runtime is None:
                    from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
                    from src.planning_revision_store import PlanningRevisionStore
                    repo_root = Path(__file__).resolve().parents[1]
                    definitions_dir = repo_root / "data" / "planning" / "definitions"
                    runtime = AppOwnedPlanningRuntime(
                        repo_root=str(repo_root),
                        legacy_store=PlanningRevisionStore.from_directory(definitions_dir) if definitions_dir.is_dir() else PlanningRevisionStore([]),
                        durable_store=None,
                        issuer=None,
                    )
                    request.app.state.planning_mcp_runtime = runtime

            result = await dispatch_http_payload(
                payload,
                identity=identity,
                runtime=runtime,
                started=started,
                fence=None,
                planning_reads_enabled=True,
            )
            if type(result) is _AuthorityFinalizedPayload:
                result = result.payload
            return JSONResponse(result, status_code=200)
        except McpDispatchFailure as exc:
            status = (
                401 if exc.code in {"unauthorized", "stale_authentication"}
                else 403 if exc.code in {"planning_unavailable", "disabled"}
                else 413 if exc.code == "budget_exceeded"
                else 400 if exc.code == "parse_error"
                else 200
            )
            return JSONResponse(dispatch_failure_response(exc.code), status_code=status)
        except Exception as exc:
            logger.error("Unhandled error in Harbour MCP endpoint", exc_info=exc)
            return JSONResponse(dispatch_failure_response("planning_failed"), status_code=200)

    return router
