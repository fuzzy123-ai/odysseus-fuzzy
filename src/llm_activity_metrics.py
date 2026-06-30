"""Redacted AI-activity and SSE metric helpers for LLM calls."""

import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def record_ai_activity_safe(
    *,
    owner: Optional[str] = None,
    surface: Optional[str] = None,
    correlation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    prompt_type: Optional[str] = None,
    provider: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    model: Optional[str] = None,
    messages: Optional[List[Dict]] = None,
    output_chars: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    duration_ms: Optional[int] = None,
    status: str = "unknown",
    error_class: Optional[str] = None,
    cache_hit: bool = False,
    side_effects: tuple[str, ...] = (),
) -> None:
    try:
        from src.ai_activity_ledger import record_ai_activity

        record_ai_activity(
            owner=owner,
            surface=surface,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
            doc_id=doc_id,
            prompt_type=prompt_type,
            provider=provider,
            endpoint_url=endpoint_url,
            model=model,
            messages=messages,
            output_chars=output_chars,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            status=status,
            error_class=error_class,
            cache_hit=cache_hit,
            side_effects=side_effects,
        )
    except Exception:
        logger.debug("[ai-activity-ledger] failed to append safe record", exc_info=True)


def sse_activity_delta_chars(chunk: str) -> int:
    total = 0
    try:
        for line in str(chunk).splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("delta"), str):
                total += len(data["delta"])
    except Exception:
        return total
    return total


def sse_activity_usage(chunk: str) -> tuple[int | None, int | None]:
    try:
        for line in str(chunk).splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("type") == "usage" and isinstance(data.get("data"), dict):
                usage = data["data"]
                return usage.get("input_tokens"), usage.get("output_tokens")
    except Exception:
        return None, None
    return None, None


def sse_activity_error_class(chunk: str) -> str | None:
    if not str(chunk).startswith("event: error"):
        return None
    try:
        for line in str(chunk).splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                status = data.get("status") if isinstance(data, dict) else None
                return f"sse_error_{status}" if status else "sse_error"
    except Exception:
        return "sse_error"
    return "sse_error"
