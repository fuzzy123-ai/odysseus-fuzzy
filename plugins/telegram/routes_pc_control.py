"""HMAC-authenticated outbound-agent routes for Telegram PC control."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from plugins.telegram.pc_control import PcControlError, TelegramPcControlService


_MAX_AGENT_BODY_BYTES = 8 * 1024
_BODY_READ_TIMEOUT_SECONDS = 2.0


class _AgentBodyTooLarge(Exception):
    pass


async def _read_bounded_agent_body(request: Request) -> bytes:
    """Read signed bytes with fixed size and time limits before authentication."""
    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > _MAX_AGENT_BODY_BYTES:
                raise _AgentBodyTooLarge
        except ValueError:
            pass

    async def collect() -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            if type(chunk) is not bytes:
                raise _AgentBodyTooLarge
            size += len(chunk)
            if size > _MAX_AGENT_BODY_BYTES:
                raise _AgentBodyTooLarge
            chunks.append(chunk)
        return b"".join(chunks)

    return await asyncio.wait_for(collect(), timeout=_BODY_READ_TIMEOUT_SECONDS)


def _body(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _unsigned_error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": code, "raw_content_visible": False},
    )


def _signed_response(
    service: TelegramPcControlService,
    *,
    request_nonce: str,
    status_code: int,
    value: dict[str, Any],
) -> Response:
    content = _body(value)
    signature = service.sign_agent_response(
        request_nonce=request_nonce,
        status_code=status_code,
        body=content,
    )
    return Response(
        content=content,
        status_code=status_code,
        media_type="application/json",
        headers={"X-Odysseus-Pc-Response-Signature": signature},
    )


async def _handle(
    request: Request,
    *,
    service: TelegramPcControlService,
    operation: str,
) -> Response:
    try:
        raw = await _read_bounded_agent_body(request)
    except _AgentBodyTooLarge:
        return _unsigned_error("agent_body_too_large", 413)
    except TimeoutError:
        return _unsigned_error("agent_body_timeout", 408)
    try:
        nonce = service.authenticate_agent_request(
            headers=request.headers,
            method=request.method,
            path=request.url.path,
            body=raw,
        )
    except PcControlError as error:
        return _unsigned_error(error.code, 403)
    try:
        payload = json.loads(raw.decode("utf-8"))
        if type(payload) is not dict:
            raise PcControlError("invalid_agent_payload")
        result = service.agent_poll(payload) if operation == "poll" else service.agent_result(payload)
        return _signed_response(
            service,
            request_nonce=nonce,
            status_code=200,
            value=result,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        error = PcControlError("invalid_agent_payload")
    except PcControlError as caught:
        error = caught
    except Exception:
        error = PcControlError("pc_control_internal_error")
    return _signed_response(
        service,
        request_nonce=nonce,
        status_code=400 if error.code != "pc_control_internal_error" else 500,
        value={"error": error.code, "raw_content_visible": False},
    )


def register_telegram_pc_control_routes(
    router: APIRouter, *, service: TelegramPcControlService
) -> None:
    """Register two non-admin routes protected by the PC-agent HMAC contract."""

    @router.post("/pc-agent/poll")
    async def pc_agent_poll(request: Request):
        return await _handle(request, service=service, operation="poll")

    @router.post("/pc-agent/result")
    async def pc_agent_result(request: Request):
        return await _handle(request, service=service, operation="result")
