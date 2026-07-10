"""Admin-gated, read-only HTTP surfaces for bounded AI Lens snapshots."""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.middleware import require_admin
from src.ai_lens_events import AiLensEventError, validate_ai_lens_event
from src.ai_lens_service import (
    AiLensService,
    AiLensServiceError,
    AiLensServiceMode,
    AiLensSessionNotFoundError,
)


AI_LENS_SESSIONS_SCHEMA = "odysseus.ai_lens.sessions.v1"
AI_LENS_STREAM_END_SCHEMA = "odysseus.ai_lens.stream_end.v1"
DEFAULT_STREAM_EVENT_LIMIT = 64
MAX_STREAM_EVENT_LIMIT = 128
DEFAULT_STREAM_BYTES = 512 * 1024
MIN_STREAM_BYTES = 4_096
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_HEARTBEAT_EVERY = 64

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_DEFAULT_RUNTIME_SERVICE = AiLensService()


class AiLensRouteConfigurationError(ValueError):
    """Raised when a route setup would expose an unsafe service mode."""


def setup_ai_lens_routes(
    service: AiLensService | None = None,
    *,
    allow_fixture: bool = False,
    max_stream_bytes: int = DEFAULT_STREAM_BYTES,
) -> APIRouter:
    """Build read-only routes; ``service`` is an explicit test/integration seam."""

    active_service = service or _DEFAULT_RUNTIME_SERVICE
    if not isinstance(active_service, AiLensService):
        raise AiLensRouteConfigurationError("service must be an AiLensService")
    if active_service.mode == AiLensServiceMode.FIXTURE and not allow_fixture:
        raise AiLensRouteConfigurationError("fixture mode is disabled by default")
    stream_byte_budget = _bounded_setup_int(
        max_stream_bytes,
        field_name="max_stream_bytes",
        minimum=MIN_STREAM_BYTES,
        maximum=MAX_STREAM_BYTES,
    )

    router = APIRouter(prefix="/api/ai-lens", tags=["ai-lens"])

    @router.get("/service")
    def get_ai_lens_service(request: Request) -> dict[str, Any]:
        require_admin(request)
        summary = active_service.service_summary()
        return {
            "schema": "odysseus.ai_lens.service.v1",
            **summary,
            "fixture_access_enabled": bool(allow_fixture),
            "write_endpoint_available": False,
            "stream_event_limit": MAX_STREAM_EVENT_LIMIT,
            "stream_byte_budget": stream_byte_budget,
        }

    @router.get("/sessions")
    def list_ai_lens_sessions(request: Request) -> dict[str, Any]:
        require_admin(request)
        sessions = list(active_service.list_session_summaries())
        return {
            "schema": AI_LENS_SESSIONS_SCHEMA,
            "mode": active_service.mode.value,
            "fixture_mode": active_service.fixture_mode,
            "session_count": len(sessions),
            "sessions": sessions,
            "raw_content_visible": False,
        }

    @router.get("/sessions/{session_id}/snapshot")
    def get_ai_lens_snapshot(
        session_id: str,
        request: Request,
        limit: str | None = Query(None),
    ) -> dict[str, Any]:
        require_admin(request)
        safe_session_id = _safe_session_id(session_id)
        bounded_limit = _bounded_query_int(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_STREAM_EVENT_LIMIT,
            allow_none=True,
        )
        try:
            return active_service.snapshot(safe_session_id, max_events=bounded_limit)
        except AiLensSessionNotFoundError as exc:
            raise HTTPException(404, "AI Lens session not found") from exc
        except (AiLensServiceError, AiLensEventError) as exc:
            raise HTTPException(400, "Invalid bounded AI Lens snapshot request") from exc

    @router.get("/sessions/{session_id}/stream")
    def stream_ai_lens_events(
        session_id: str,
        request: Request,
        event_limit: str = Query(str(DEFAULT_STREAM_EVENT_LIMIT)),
        heartbeat_every: str = Query("8"),
    ) -> StreamingResponse:
        require_admin(request)
        safe_session_id = _safe_session_id(session_id)
        requested_limit = _bounded_query_int(
            event_limit,
            field_name="event_limit",
            minimum=1,
            maximum=MAX_STREAM_EVENT_LIMIT,
        )
        bounded_heartbeat = _bounded_query_int(
            heartbeat_every,
            field_name="heartbeat_every",
            minimum=1,
            maximum=MAX_HEARTBEAT_EVERY,
        )
        bounded_limit = min(requested_limit, active_service.limits.max_snapshot_events)
        try:
            snapshot = active_service.snapshot(safe_session_id, max_events=bounded_limit)
            frames = _bounded_sse_frames(
                snapshot=snapshot,
                heartbeat_every=bounded_heartbeat,
                max_stream_bytes=stream_byte_budget,
            )
        except AiLensSessionNotFoundError as exc:
            raise HTTPException(404, "AI Lens session not found") from exc
        except (AiLensServiceError, AiLensEventError) as exc:
            raise HTTPException(400, "Invalid bounded AI Lens stream request") from exc
        return StreamingResponse(
            _iter_frames(frames),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


def _safe_session_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _SESSION_ID_RE.fullmatch(text):
        raise HTTPException(400, "Invalid AI Lens session identifier")
    return text


def _bounded_setup_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AiLensRouteConfigurationError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AiLensRouteConfigurationError(f"{field_name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise AiLensRouteConfigurationError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _bounded_query_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
    allow_none: bool = False,
) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise HTTPException(400, f"Invalid bounded AI Lens {field_name}")
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,6}", text):
        raise HTTPException(400, f"Invalid bounded AI Lens {field_name}")
    normalized = int(text)
    if normalized < minimum or normalized > maximum:
        raise HTTPException(400, f"Invalid bounded AI Lens {field_name}")
    return normalized


def _sse_frame(*, event_name: str, data: dict[str, Any], event_id: str = "") -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(
        "data: "
        + json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return "\n".join(lines) + "\n\n"


def _bounded_sse_frames(
    *,
    snapshot: dict[str, Any],
    heartbeat_every: int,
    max_stream_bytes: int,
) -> tuple[str, ...]:
    events = tuple(validate_ai_lens_event(item) for item in snapshot.get("events") or ())
    frames: list[str] = []
    used_bytes = 0
    emitted_count = 0
    byte_limited = False
    heartbeat_count = 0
    end_reserve = 512

    for event in events:
        frame = _sse_frame(
            event_name="ai_lens_event",
            event_id=event.event_id,
            data=event.to_dict(),
        )
        frame_bytes = len(frame.encode("utf-8"))
        if used_bytes + frame_bytes + end_reserve > max_stream_bytes:
            byte_limited = True
            break
        frames.append(frame)
        used_bytes += frame_bytes
        emitted_count += 1
        if emitted_count % heartbeat_every == 0:
            heartbeat_count += 1
            heartbeat = f": heartbeat {heartbeat_count}\n\n"
            heartbeat_bytes = len(heartbeat.encode("utf-8"))
            if used_bytes + heartbeat_bytes + end_reserve <= max_stream_bytes:
                frames.append(heartbeat)
                used_bytes += heartbeat_bytes

    if not events:
        heartbeat = ": heartbeat 1\n\n"
        if len(heartbeat.encode("utf-8")) + end_reserve <= max_stream_bytes:
            frames.append(heartbeat)
            used_bytes += len(heartbeat.encode("utf-8"))

    end_payload = {
        "schema": AI_LENS_STREAM_END_SCHEMA,
        "session_id": snapshot.get("session_id"),
        "available_event_count": len(events),
        "emitted_event_count": emitted_count,
        "byte_limited": byte_limited,
        "snapshot_incomplete": bool(snapshot.get("incomplete")),
        "snapshot_truncated": bool(snapshot.get("truncated")),
        "raw_content_visible": False,
    }
    end_frame = _sse_frame(event_name="stream_end", data=end_payload)
    if used_bytes + len(end_frame.encode("utf-8")) <= max_stream_bytes:
        frames.append(end_frame)
    return tuple(frames)


def _iter_frames(frames: tuple[str, ...]) -> Iterator[str]:
    yield from frames
