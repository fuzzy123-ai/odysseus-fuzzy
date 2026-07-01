"""Synchronous LLM request orchestration."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional


def _coerce_headers(headers: Optional[dict] | str) -> Optional[dict]:
    if isinstance(headers, str):
        try:
            parsed = json.loads(headers)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return headers if isinstance(headers, dict) else None


def _merge_system_messages(messages: list[dict]) -> list[dict]:
    system_parts = []
    non_system = []
    for message in messages:
        if message.get("role") == "system":
            system_parts.append(message.get("content") or "")
        else:
            non_system.append(message)
    if system_parts:
        return [{"role": "system", "content": "\n\n".join(system_parts)}] + non_system
    return non_system


def llm_call_impl(
    url: str,
    model: str,
    messages: list[dict],
    *,
    temperature: float,
    max_tokens: int,
    headers: Optional[dict] | str,
    timeout: int,
    http_exception_cls: type[Exception],
    logger,
    provider_headers_func: Callable[[str], dict],
    detect_provider_func: Callable[[str], str],
    sanitize_messages_func: Callable[[list[dict]], list[dict]],
    get_cache_key_func: Callable[..., str],
    get_cached_response_func: Callable[[str], Optional[str]],
    set_cached_response_func: Callable[[str, str], None],
    normalize_anthropic_url_func: Callable[[str], str],
    build_anthropic_headers_func: Callable[[Optional[dict] | str], dict],
    build_anthropic_payload_func: Callable[..., dict],
    normalize_ollama_url_func: Callable[[str], str],
    build_ollama_payload_func: Callable[..., dict],
    get_context_length_func: Callable[[str, str], int],
    omit_temperature_func: Callable[[str, str], bool],
    uses_max_completion_tokens_func: Callable[[str], bool],
    supports_thinking_func: Callable[[str], bool],
    mistral_reasoning_effort: str,
    note_model_activity_func: Callable[[str, str], None],
    httpx_post_func: Callable[..., Any],
    parse_anthropic_response_func: Callable[[dict], str],
    parse_ollama_response_func: Callable[[dict], str],
    normalize_mistral_content_func: Callable[[Any], tuple[str, str]],
    parse_openai_message_func: Callable[..., str],
) -> str:
    """Synchronous LLM call with optional prompt type enhancement."""
    provider = detect_provider_func(url)
    request_headers = provider_headers_func(provider)
    coerced_headers = _coerce_headers(headers)
    if coerced_headers:
        request_headers.update(coerced_headers)

    messages_copy = _merge_system_messages(sanitize_messages_func(messages))

    cache_key = get_cache_key_func(url, model, messages_copy, temperature, max_tokens)
    cached_response = get_cached_response_func(cache_key)
    if cached_response:
        logger.debug(f"Returning cached response for key: {cache_key}")
        return cached_response

    if provider == "anthropic":
        target_url = normalize_anthropic_url_func(url)
        request_headers = build_anthropic_headers_func(headers)
        payload = build_anthropic_payload_func(model, messages_copy, temperature, max_tokens)
    elif provider == "ollama":
        target_url = normalize_ollama_url_func(url)
        payload = build_ollama_payload_func(
            model,
            messages_copy,
            temperature,
            max_tokens,
            stream=False,
            num_ctx=get_context_length_func(url, model),
            think=False if supports_thinking_func(model) else None,
        )
    else:
        target_url = url
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
        if provider == "mistral" and supports_thinking_func(model):
            payload["reasoning_effort"] = mistral_reasoning_effort

    try:
        note_model_activity_func(target_url, model)
        response = httpx_post_func(target_url, request_headers, json=payload, timeout=timeout)
    except Exception as exc:
        raise http_exception_cls(502, f"POST {target_url} failed: {exc}")
    if not response.is_success:
        raise http_exception_cls(502, f"Upstream {target_url} -> {response.status_code}: {response.text}")

    data = response.json()
    try:
        if provider == "anthropic":
            text = parse_anthropic_response_func(data)
        elif provider == "ollama":
            text = parse_ollama_response_func(data)
        else:
            message = data["choices"][0]["message"]
            text = parse_openai_message_func(
                message,
                model=model,
                normalize_content_func=normalize_mistral_content_func,
            )
        set_cached_response_func(cache_key, text)
        return text
    except Exception:
        raise http_exception_cls(502, f"Unexpected schema from {target_url}: {str(data)[:400]}")
