"""Fallback-chain helpers for LLM calls and streams."""

import json
from typing import Optional


def _dedupe_candidates(candidates):
    """Filter malformed entries and drop later repeated ``(url, model)`` routes."""
    seen = set()
    out = []
    for candidate in candidates or []:
        if not candidate or not candidate[0] or not candidate[1]:
            continue
        key = (candidate[0], candidate[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _summarize_stream_error(err_chunk: Optional[str]) -> str:
    """Pull a short human reason out of an ``event: error`` SSE chunk."""
    if not err_chunk:
        return "primary model failed"
    try:
        for line in err_chunk.split("\n"):
            if line.startswith("data: "):
                parsed = json.loads(line[6:])
                text = parsed.get("text") or parsed.get("error") or ""
                status = parsed.get("status")
                msg = (f"HTTP {status}: " if status else "") + str(text)
                return msg[:200].strip() or "primary model failed"
    except Exception:
        pass
    return "primary model failed"


def _no_model_endpoint_event() -> str:
    return 'event: error\ndata: {"error": "No model endpoint configured", "status": 503}\n\n'


def _fallback_notice_event(primary_model: str, answered_by: str, last_error: Optional[str]) -> str:
    return (
        "data: "
        + json.dumps(
            {
                "type": "fallback",
                "selected_model": primary_model,
                "answered_by": answered_by,
                "reason": _summarize_stream_error(last_error),
            }
        )
        + "\n\n"
    )
