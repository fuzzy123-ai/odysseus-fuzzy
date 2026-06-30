"""Asynchronous LLM request orchestration."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Optional

from src.llm_sync_call import _merge_system_messages


async def _collect_chatgpt_subscription_response(
    *,
    url: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    headers: Optional[dict],
    timeout: int,
    stream_llm_func: Callable[..., Any],
    http_exception_cls: type[Exception],
) -> str:
    parts: list[str] = []
    async for chunk in stream_llm_func(
        url,
        model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        headers=headers,
        timeout=timeout,
    ):
        event_is_error = False
        for line in str(chunk).splitlines():
            if line.startswith("event:"):
                event_is_error = line[6:].strip() == "error"
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw:
                continue
            if raw == "[DONE]":
                return "".join(parts)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event_is_error or data.get("error") or (data.get("status") and data.get("text")):
                status = int(data.get("status") or 502)
                text = data.get("text") or data.get("error") or "ChatGPT Subscription request failed"
                raise http_exception_cls(status, text)
            delta = data.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
    return "".join(parts)


async def llm_call_async_impl(
    url: str,
    model: str,
    messages: list[dict],
    *,
    temperature: float,
    max_tokens: int,
    headers: Optional[dict],
    timeout: int,
    max_retries: int,
    session_id: Optional[str],
    retry_delay: float,
    dead_host_cooldown: float,
    http_exception_cls: type[Exception],
    connect_error_classes: tuple[type[BaseException], ...],
    request_error_classes: tuple[type[BaseException], ...],
    logger,
    detect_provider_func: Callable[[str], str],
    sanitize_messages_func: Callable[[list[dict]], list[dict]],
    get_cache_key_func: Callable[..., str],
    get_cached_response_func: Callable[[str], Optional[str]],
    set_cached_response_func: Callable[[str, str], None],
    stream_llm_func: Callable[..., Any],
    normalize_anthropic_url_func: Callable[[str], str],
    build_anthropic_headers_func: Callable[[Optional[dict]], dict],
    build_anthropic_payload_func: Callable[..., dict],
    normalize_ollama_url_func: Callable[[str], str],
    build_ollama_payload_func: Callable[..., dict],
    get_context_length_func: Callable[[str, str], int],
    provider_headers_func: Callable[..., dict],
    omit_temperature_func: Callable[[str, str], bool],
    uses_max_completion_tokens_func: Callable[[str], bool],
    is_ollama_openai_compat_url_func: Callable[[str], bool],
    supports_thinking_func: Callable[[str], bool],
    mistral_reasoning_effort: str,
    apply_local_cache_affinity_func: Callable[[dict, str, Optional[str]], None],
    is_host_dead_func: Callable[[str], bool],
    host_key_func: Callable[[str], str],
    call_timeout_func: Callable[[int], Any],
    note_model_activity_func: Callable[[str, str], None],
    get_http_client_func: Callable[[], Any],
    httpx_post_async_func: Callable[..., Any],
    format_upstream_error_func: Callable[[int, str, str], str],
    clear_host_dead_func: Callable[[str], None],
    parse_anthropic_response_func: Callable[[dict], str],
    parse_ollama_response_func: Callable[[dict], str],
    normalize_mistral_content_func: Callable[[Any], tuple[str, str]],
    mark_host_dead_func: Callable[[str], bool],
) -> str:
    """Async LLM call with connection pooling, timeout, retry and logging."""
    provider = detect_provider_func(url)
    messages_copy = _merge_system_messages(sanitize_messages_func(messages))

    cache_key = get_cache_key_func(url, model, messages_copy, temperature, max_tokens)
    cached_response = get_cached_response_func(cache_key)
    if cached_response:
        logger.debug(f"Returning cached response for key: {cache_key}")
        return cached_response

    if provider == "chatgpt-subscription":
        response = await _collect_chatgpt_subscription_response(
            url=url,
            model=model,
            messages=messages_copy,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=headers,
            timeout=timeout,
            stream_llm_func=stream_llm_func,
            http_exception_cls=http_exception_cls,
        )
        set_cached_response_func(cache_key, response)
        return response

    if provider == "anthropic":
        target_url = normalize_anthropic_url_func(url)
        request_headers = build_anthropic_headers_func(headers)
        payload = build_anthropic_payload_func(model, messages_copy, temperature, max_tokens)
    elif provider == "ollama":
        target_url = normalize_ollama_url_func(url)
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        payload = build_ollama_payload_func(
            model,
            messages_copy,
            temperature,
            max_tokens,
            stream=False,
            num_ctx=get_context_length_func(url, model),
        )
    else:
        target_url = url
        request_headers = provider_headers_func(provider, headers)
        if provider == "copilot":
            from src.copilot import apply_request_headers

            apply_request_headers(request_headers, messages_copy)
        payload = {
            "model": model,
            "messages": messages_copy,
            "temperature": temperature,
        }
        if omit_temperature_func(provider, model):
            payload.pop("temperature", None)
        if max_tokens and max_tokens > 0:
            token_key = "max_completion_tokens" if uses_max_completion_tokens_func(model) else "max_tokens"
            payload[token_key] = max_tokens
        if is_ollama_openai_compat_url_func(url) and supports_thinking_func(model):
            payload["think"] = False
        if provider == "mistral" and supports_thinking_func(model):
            payload["reasoning_effort"] = mistral_reasoning_effort
        apply_local_cache_affinity_func(payload, url, session_id)

    if is_host_dead_func(target_url):
        raise http_exception_cls(503, f"Upstream {host_key_func(target_url)} marked unreachable (cooldown active)")

    call_timeout = call_timeout_func(timeout)
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        start = time.time()
        try:
            note_model_activity_func(target_url, model)
            client = get_http_client_func()
            response = await httpx_post_async_func(client, target_url, request_headers, json=payload, timeout=call_timeout)
            duration = time.time() - start
            if not response.is_success:
                friendly = format_upstream_error_func(response.status_code, response.text, target_url)
                logger.warning(
                    f"LLM async call to {target_url} failed in {duration:.2f}s "
                    f"(attempt {attempt}): HTTP {response.status_code} {friendly}"
                )
                if response.status_code in (429, 502, 503, 504) and attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    continue
                raise http_exception_cls(response.status_code, friendly)
            logger.info(f"LLM async call to {target_url} succeeded in {duration:.2f}s (attempt {attempt})")
            clear_host_dead_func(target_url)
            data = response.json()
            try:
                if provider == "anthropic":
                    text = parse_anthropic_response_func(data)
                elif provider == "ollama":
                    text = parse_ollama_response_func(data)
                else:
                    message = data["choices"][0]["message"]
                    content = message.get("content")
                    if isinstance(content, list):
                        text_part, thinking_part = normalize_mistral_content_func(content)
                        text = ((thinking_part + "\n\n") if thinking_part else "") + (text_part or "")
                        if not text:
                            text = message.get("reasoning_content") or ""
                    else:
                        text = content or message.get("reasoning_content") or ""
                set_cached_response_func(cache_key, text)
                return text
            except Exception:
                raise http_exception_cls(502, f"Unexpected schema from {target_url}: {str(data)[:400]}")
        except connect_error_classes as exc:
            cooled = mark_host_dead_func(target_url)
            duration = time.time() - start
            tail = f" - host cooled for {dead_host_cooldown:.0f}s" if cooled else " - transient, will retry"
            logger.warning(f"LLM async connect to {target_url} failed after {duration:.2f}s: {exc}{tail}")
            if cooled or attempt >= max_retries:
                raise http_exception_cls(503, f"Cannot reach {host_key_func(target_url)}: {exc}")
            await asyncio.sleep(retry_delay)
        except request_error_classes as exc:
            duration = time.time() - start
            logger.warning(f"LLM async call attempt {attempt} failed after {duration:.2f}s: {exc}")
            if attempt >= max_retries:
                raise http_exception_cls(502, f"POST {target_url} failed after {max_retries} attempts: {exc}")
            await asyncio.sleep(retry_delay)

    raise http_exception_cls(502, f"POST {url} failed after {max_retries} attempts")
