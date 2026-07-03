"""Browser-safe Universal File IO planning routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth_helpers import require_authenticated_request
from src.universal_file_io import (
    UniversalFileIOError,
    build_export_plan,
    parse_export_intent,
    summarize_file_capabilities,
)


EXPORT_PLAN_RESPONSE_SCHEMA = "odysseus.universal_file_io.export_plan_response.v1"
CAPABILITIES_RESPONSE_SCHEMA = "odysseus.universal_file_io.capabilities_response.v1"


class UniversalFileIOPlanRequest(BaseModel):
    request_text: str = Field(..., min_length=1, max_length=2000)
    recent_source_ref: str = Field(..., min_length=1, max_length=300)
    source_name_or_extension: str = Field(..., min_length=1, max_length=300)
    dsgvo_mode: bool = False
    delivery_hint: str = "review"


def setup_universal_file_io_routes() -> APIRouter:
    router = APIRouter(prefix="/api/universal-file-io", tags=["universal-file-io"])

    @router.get("/capabilities")
    async def universal_file_io_capabilities(request: Request, extensions: str | None = None):
        require_authenticated_request(request)
        selected = [part.strip() for part in str(extensions or "").split(",") if part.strip()] or None
        summary = summarize_file_capabilities(selected)
        return {
            "schema": CAPABILITIES_RESPONSE_SCHEMA,
            "ok": True,
            "capabilities": summary,
            "raw_content_visible": False,
            "path_values_visible": False,
            "token_value_visible": False,
        }

    @router.post("/export-plan")
    async def universal_file_io_export_plan(request: Request, body: UniversalFileIOPlanRequest):
        require_authenticated_request(request)
        try:
            intent = parse_export_intent(
                body.request_text,
                recent_source_ref=body.recent_source_ref,
                dsgvo_mode=bool(body.dsgvo_mode),
                delivery_hint=body.delivery_hint,
            )
            plan = build_export_plan(
                intent,
                source_name_or_extension=body.source_name_or_extension,
                live_converter_enabled=False,
            )
        except UniversalFileIOError as exc:
            raise HTTPException(400, str(exc)) from exc

        payload = {
            "schema": EXPORT_PLAN_RESPONSE_SCHEMA,
            "ok": True,
            "intent": intent.to_dict(),
            "plan": plan.to_dict(),
            "execution_performed": False,
            "converter_execution_allowed": False,
            "delivery_allowed": False,
            "live_write_allowed": False,
            "source_name_visible": False,
            "raw_request_visible": False,
            "raw_content_visible": False,
            "path_values_visible": False,
            "chat_id_value_visible": False,
            "token_value_visible": False,
        }
        return _redaction_guard(payload)

    return router


def _redaction_guard(payload: dict[str, Any]) -> dict[str, Any]:
    """Last-line guard for the browser contract's public flags."""

    payload["execution_performed"] = False
    payload["converter_execution_allowed"] = False
    payload["delivery_allowed"] = False
    payload["live_write_allowed"] = False
    payload["source_name_visible"] = False
    payload["raw_request_visible"] = False
    payload["raw_content_visible"] = False
    payload["path_values_visible"] = False
    payload["chat_id_value_visible"] = False
    payload["token_value_visible"] = False
    plan = payload.get("plan")
    if isinstance(plan, dict):
        plan["live_execution_allowed"] = False
        plan["delivery_allowed"] = False
        plan["raw_content_visible"] = False
    intent = payload.get("intent")
    if isinstance(intent, dict):
        intent["raw_request_visible"] = False
    return payload
