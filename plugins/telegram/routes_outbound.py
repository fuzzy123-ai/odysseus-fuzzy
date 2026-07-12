"""Outbound reply route registration for the Telegram plugin."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request


ReplyHandler = Callable[..., dict[str, Any]]
DocumentReplyTool = Callable[..., Awaitable[dict[str, Any]]]
LiveGateBuilder = Callable[[dict[str, Any]], dict[str, Any]]


def register_telegram_outbound_routes(
    router: APIRouter,
    *,
    require_admin: Callable[[Request], None],
    reply_with_gate: ReplyHandler,
    document_reply_tool: DocumentReplyTool,
    build_screenshot_live_gate_packet: LiveGateBuilder,
) -> None:
    """Register Telegram outbound/document routes on an existing router."""

    @router.post("/reply")
    async def reply(request: Request):
        require_admin(request)
        body = await request.json()
        chat_id = str(body.get("chat_id") or "")
        text = str(body.get("text") or "")
        result = reply_with_gate(
            chat_id,
            text,
            source_message_id=body.get("source_message_id"),
            classification=body.get("classification"),
            security_mode=body.get("security_mode") or "",
            secure_transport=bool(body.get("secure_transport")),
            can_start_secure_flow=bool(body.get("can_start_secure_flow")),
        )
        if result.get("exit_code") != 0:
            raise HTTPException(403, str(result.get("error") or "Telegram reply refused"))
        return json.loads(str(result["output"]))

    @router.post("/document-reply")
    async def document_reply(request: Request):
        require_admin(request)
        body = await request.json()
        try:
            result = await document_reply_tool("", **dict(body))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if result.get("exit_code") != 0:
            raise HTTPException(403, str(result.get("error") or "Telegram document reply refused"))
        return json.loads(str(result["output"]))

    @router.post("/document-reply/preview")
    async def document_reply_preview(request: Request):
        require_admin(request)
        body = await request.json()
        try:
            result = await document_reply_tool("", **{**dict(body), "preview_only": True})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if result.get("exit_code") != 0:
            raise HTTPException(400, str(result.get("error") or "Telegram document reply preview refused"))
        return json.loads(str(result["output"]))

    @router.post("/document-reply/live-gate")
    async def document_reply_live_gate(request: Request):
        require_admin(request)
        body = await request.json()
        try:
            result = await document_reply_tool("", **{**dict(body), "preview_only": True})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if result.get("exit_code") != 0:
            raise HTTPException(400, str(result.get("error") or "Telegram document reply live gate refused"))
        preview = json.loads(str(result["output"]))
        packet = preview.get("delivery_packet")
        if not packet:
            raise HTTPException(400, "Telegram screenshot live gate requires a photo artifact")
        return build_screenshot_live_gate_packet(packet)
