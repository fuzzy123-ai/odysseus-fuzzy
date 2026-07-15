"""Fail-closed owner-scoped HTTP routes for Agent run operations."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.agent_operation_commands import (
    AgentOperationCommandError,
    AgentOperationCommandService,
)
from src.agent_operation_projection import AgentOperationProjectionError
from src.temporal_agent_operation_adapter import (
    AgentOperationAdapterError,
    TemporalAgentOperationAdapter,
    LazyTemporalSDKExecutionReader,
    PersistentRunCatalog,
    TemporalExecutionReader,
)


OwnerResolver = Callable[[Request], str | None]
RequestGate = Callable[[Request], Any]
MAX_JSON_BODY_BYTES = 65_536


def setup_agent_operation_routes(
    adapter: TemporalAgentOperationAdapter,
    *,
    command_service: AgentOperationCommandService | None = None,
    owner_resolver: OwnerResolver | None = None,
    abc_gate: RequestGate | None = None,
    csrf_gate: RequestGate | None = None,
) -> APIRouter:
    if not isinstance(adapter, TemporalAgentOperationAdapter):
        raise ValueError("adapter must be a TemporalAgentOperationAdapter")
    commands = command_service or AgentOperationCommandService(adapter)
    resolve_owner = owner_resolver or _deny_owner
    require_abc = abc_gate or _deny_gate
    require_csrf = csrf_gate or _deny_gate
    router = APIRouter(prefix="/api/agent/runs", tags=["agent-operations"])

    def scope(request: Request, *, mutate: bool = False, abc_only: bool = False) -> str:
        try:
            owner = str(resolve_owner(request) or "").strip()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Not authenticated") from exc
        if not owner:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if mutate:
            _apply_gate(require_csrf, request, "CSRF validation failed")
        if abc_only:
            _apply_gate(require_abc, request, "Authenticated /abc handler required")
        return owner

    @router.post("")
    async def start_run(request: Request):
        owner = scope(request, mutate=True, abc_only=True)
        body = await _json_object(
            request,
            required={
                "project_id",
                "roadmap_id",
                "revision",
                "content_hash",
                "start_request_id",
            },
        )
        return await _read(lambda: adapter.start_run(owner, body))

    @router.get("")
    async def list_runs(
        request: Request,
        project_id: str = Query(default="", max_length=128),
        state: str = Query(default="", max_length=64),
        cursor: str = Query(default="", max_length=256),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        owner = scope(request)
        return await _read(
            lambda: adapter.list_runs(
                owner,
                project_id=project_id,
                state=state,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.get("/{agent_run_id}")
    async def get_run(agent_run_id: str, request: Request):
        return await _read(lambda: adapter.get_run(scope(request), agent_run_id))

    @router.get("/{agent_run_id}/history")
    async def get_history(
        agent_run_id: str,
        request: Request,
        after: str = Query(default="", max_length=64),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return await _read(
            lambda: adapter.get_history(
                scope(request), agent_run_id, after=after, limit=limit
            )
        )

    @router.get("/{agent_run_id}/stream")
    async def stream_history(
        agent_run_id: str,
        request: Request,
        after: str = Query(default="", max_length=64),
    ):
        owner = scope(request)
        header_cursor = request.headers.get("last-event-id", "").strip()
        if after and header_cursor and after != header_cursor:
            raise HTTPException(status_code=400, detail="Conflicting history cursors")
        resume_cursor = header_cursor or after
        # Resolve owner and run before the response starts so failures are not
        # hidden inside an already-committed 200 streaming response.
        await _read(lambda: adapter.get_run(owner, agent_run_id))

        async def events():
            try:
                async for event in adapter.stream_history(
                    owner, agent_run_id, after=resume_cursor, page_limit=200
                ):
                    event_id = str(event["event_id"])
                    payload = json.dumps(
                        event,
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    yield f"id: {event_id}\nevent: agent_operation\ndata: {payload}\n\n"
            except (AgentOperationAdapterError, AgentOperationProjectionError):
                # The client reconnects from the last complete id.  Error
                # details stay server-side and no partial product event leaks.
                return

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/{agent_run_id}/commands")
    async def execute_command(agent_run_id: str, request: Request):
        owner = scope(request, mutate=True)
        body = await _json_object(
            request,
            required={
                "command_id",
                "command",
                "expected_run_version",
                "idempotency_key",
                "payload",
            },
        )
        return await _read(lambda: commands.execute(owner, agent_run_id, body))

    return router


def setup_default_agent_operation_routes(
    *,
    run_store_path: str | Path | None = None,
    temporal: TemporalExecutionReader | None = None,
) -> APIRouter:
    """Compose the app router without connecting to or starting Temporal."""

    from routes.project_versioning_routes import _same_origin_csrf_gate
    from src.auth_helpers import effective_user
    from src.constants import DATA_DIR
    from src.temporal_runtime.config import load_temporal_light_config

    local_owner = str(os.getenv("ODYSSEUS_SINGLE_USER_OWNER") or "local-user").strip()
    store_path = Path(
        run_store_path
        or (Path(DATA_DIR) / "temporal_light" / "run-starts.json")
    )
    if temporal is None:
        config = load_temporal_light_config()
        temporal = LazyTemporalSDKExecutionReader(
            address=config.address,
            namespace=config.namespace,
        )
    adapter = TemporalAgentOperationAdapter(
        catalog=PersistentRunCatalog(store_path),
        temporal=temporal,
    )

    def resolve_owner(request: Request) -> str | None:
        owner = str(effective_user(request) or "").strip()
        if owner:
            return owner
        if os.getenv("AUTH_ENABLED", "true").strip().lower() == "false":
            return local_owner
        return None

    def authenticated_abc_handler(request: Request) -> bool:
        # No client header can grant this flag.  A future in-process /abc
        # handler may stamp it only after authenticating and pinning the plan.
        return getattr(request.state, "abc_handler_authorized", False) is True

    return setup_agent_operation_routes(
        adapter,
        owner_resolver=resolve_owner,
        abc_gate=authenticated_abc_handler,
        csrf_gate=_same_origin_csrf_gate,
    )


async def _read(call: Callable[[], Any]) -> Any:
    try:
        value = call()
        return await value if inspect.isawaitable(value) else value
    except HTTPException:
        raise
    except (AgentOperationAdapterError, AgentOperationProjectionError, AgentOperationCommandError) as exc:
        status = _status_for(exc.code)
        raise HTTPException(status_code=status, detail=exc.code) from exc
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="agent_operation_unavailable") from exc


async def _json_object(request: Request, *, required: set[str]) -> dict[str, Any]:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_JSON_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Request body too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict) or set(body) != required:
        raise HTTPException(status_code=422, detail="Request fields are not exact")
    return body


def _apply_gate(gate: RequestGate, request: Request, detail: str) -> None:
    try:
        decision = gate(request)
    except HTTPException:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise HTTPException(status_code=403, detail=detail) from exc
    if inspect.isawaitable(decision):
        raise HTTPException(status_code=500, detail="Synchronous request gate required")
    if decision is not True and decision is not None:
        raise HTTPException(status_code=403, detail=detail)


def _status_for(code: str) -> int:
    if code == "run_not_found":
        return 404
    if code in {
        "stale_run_version",
        "command_not_allowed",
        "command_conflict",
        "history_segment_unavailable",
    }:
        return 409
    if code.endswith("_unavailable") or code in {"run_store_corrupt", "run_start_failed"}:
        return 503
    return 400


def _deny_owner(_request: Request) -> None:
    return None


def _deny_gate(_request: Request) -> bool:
    return False


__all__ = [
    "setup_agent_operation_routes",
    "setup_default_agent_operation_routes",
]
