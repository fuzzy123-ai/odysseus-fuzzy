"""ChatGPT Subscription / Codex Responses streaming helpers."""

from __future__ import annotations

import json
from typing import Any, Callable


async def stream_chatgpt_subscription_response(
    target_url: str,
    payload: dict,
    headers: dict,
    stream_timeout,
    *,
    get_http_client_func: Callable[[], Any],
    clear_host_dead_func: Callable[[str], None],
    format_error_func: Callable[[int, str], str],
    mark_host_dead_func: Callable[[str], bool],
    host_key_func: Callable[[str], str],
    dead_host_cooldown: float,
    logger,
    connect_error_classes: tuple[type[BaseException], ...],
    read_timeout_cls: type[BaseException],
    network_error_cls: type[BaseException],
):
    """Yield core SSE chunks for ChatGPT Subscription Responses streams."""
    event_name = ""
    input_tokens = 0
    output_tokens = 0
    try:
        client = get_http_client_func()
        async with client.stream("POST", target_url, json=payload, headers=headers, timeout=stream_timeout) as response:
            clear_host_dead_func(target_url)
            if response.status_code != 200:
                raw = (await response.aread()).decode(errors="replace")
                friendly = format_error_func(response.status_code, raw)
                yield f'event: error\ndata: {json.dumps({"status": response.status_code, "text": friendly, "raw": raw[:500]})}\n\n'
                return
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                evt = data.get("type") or event_name
                if evt == "response.output_text.delta":
                    delta = data.get("delta") or ""
                    if delta:
                        yield f'data: {json.dumps({"delta": delta})}\n\n'
                elif evt == "response.completed":
                    usage = (data.get("response") or {}).get("usage") or data.get("usage") or {}
                    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or input_tokens
                    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or output_tokens
                    if input_tokens or output_tokens:
                        yield f'data: {json.dumps({"type": "usage", "data": {"input_tokens": input_tokens, "output_tokens": output_tokens}})}\n\n'
                    yield "data: [DONE]\n\n"
                    return
                elif evt in ("response.failed", "error"):
                    err = data.get("error") or (data.get("response") or {}).get("error") or {}
                    text = err.get("message") if isinstance(err, dict) else str(err or "ChatGPT Subscription request failed")
                    yield f'event: error\ndata: {json.dumps({"status": 502, "text": text})}\n\n'
                    return
            yield "data: [DONE]\n\n"
    except connect_error_classes as exc:
        cooled = mark_host_dead_func(target_url)
        tail = f" - host cooled for {dead_host_cooldown:.0f}s" if cooled else " - transient, will retry"
        logger.warning(f"ChatGPT Subscription stream connect to {target_url} failed: {exc}{tail}")
        yield f'event: error\ndata: {json.dumps({"error": f"Cannot reach {host_key_func(target_url)}", "status": 503})}\n\n'
    except read_timeout_cls:
        yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
    except network_error_cls:
        yield f'event: error\ndata: {json.dumps({"error": "Network error", "status": 502})}\n\n'
    except Exception as exc:
        logger.error(f"ChatGPT Subscription stream error: {exc}")
        yield f'event: error\ndata: {json.dumps({"error": str(exc), "status": 502})}\n\n'
