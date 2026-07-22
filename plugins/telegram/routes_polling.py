"""Polling route registration for the Telegram plugin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request


def register_telegram_polling_routes(
    router: APIRouter,
    *,
    data_dir: str | Path,
    require_admin: Callable[[Request], None],
    run_polling_cycle: Callable[..., dict[str, Any]],
    fetch_updates: Any = None,
    session_creator: Any = None,
    session_archiver: Any = None,
    agent_turn_handler: Any = None,
    voice_stt_provider: Any = None,
    voice_bytes_provider: Any = None,
    image_bytes_provider: Any = None,
    attachment_bytes_provider: Any = None,
    image_worker_client: Any = None,
    reply_handler: Callable[..., dict[str, Any]] | None = None,
    document_reply_handler: Callable[..., dict[str, Any]] | None = None,
    memory_manager: Any = None,
    memory_vector: Any = None,
    memory_owner: str = "telegram",
    project_registry_path: str | Path | None = None,
) -> None:
    """Register Telegram polling route on an existing router."""

    @router.post("/poll")
    async def poll(request: Request):
        require_admin(request)
        result = await asyncio.to_thread(
            run_polling_cycle,
            data_dir=data_dir,
            fetch_updates=fetch_updates,
            session_creator=session_creator,
            session_archiver=session_archiver,
            agent_turn_handler=agent_turn_handler,
            voice_stt_provider=voice_stt_provider,
            voice_bytes_provider=voice_bytes_provider,
            image_bytes_provider=image_bytes_provider,
            attachment_bytes_provider=attachment_bytes_provider,
            image_worker_client=image_worker_client,
            reply_handler=reply_handler,
            document_reply_handler=document_reply_handler,
            memory_manager=memory_manager,
            memory_vector=memory_vector,
            memory_owner=memory_owner,
            project_registry_path=project_registry_path,
        )
        if not result["ok"]:
            raise HTTPException(403, result["status"])
        return result
