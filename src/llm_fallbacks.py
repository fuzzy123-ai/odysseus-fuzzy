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


def llm_call_with_fallback(
    candidates,
    messages,
    *,
    llm_call_func,
    http_exception_cls,
    logger,
    **kwargs,
) -> str:
    """Run a sync LLM call against an ordered fallback chain."""
    cands = _dedupe_candidates(candidates)
    if not cands:
        raise http_exception_cls(503, "No model endpoint configured")
    last_err = None
    for i, (url, model, headers) in enumerate(cands):
        try:
            return llm_call_func(url, model, messages, headers=headers, **kwargs)
        except Exception as e:
            last_err = e
            tag = "primary" if i == 0 else "candidate"
            logger.warning(f"[fallback] {tag} {model} failed ({type(e).__name__}); trying next")
            continue
    raise last_err if last_err else http_exception_cls(503, "All fallback candidates failed")


async def llm_call_async_with_fallback(
    candidates,
    messages,
    *,
    llm_call_async_func,
    http_exception_cls,
    logger,
    **kwargs,
) -> str:
    """Run an async LLM call against an ordered fallback chain."""
    cands = _dedupe_candidates(candidates)
    if not cands:
        raise http_exception_cls(503, "No model endpoint configured")
    last_err = None
    for i, (url, model, headers) in enumerate(cands):
        try:
            return await llm_call_async_func(url, model, messages, headers=headers, **kwargs)
        except Exception as e:
            last_err = e
            tag = "primary" if i == 0 else "candidate"
            logger.warning(f"[fallback] {tag} {model} failed ({type(e).__name__}); trying next")
            continue
    raise last_err if last_err else http_exception_cls(503, "All fallback candidates failed")
