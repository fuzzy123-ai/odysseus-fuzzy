# src/llm_core.py
import httpx
import asyncio
import time
import json
import logging
import threading
import os
import contextvars
from fastapi import HTTPException
from typing import Optional, Dict, List, Tuple
from src.model_context import (
    ContextLengthSnapshot,
    DEFAULT_CONTEXT,
    get_context_length,
    get_context_snapshot_async,
)
from src.maintenance_model_policy import DEFAULT_MAINTENANCE_MODEL
from src.local_model_scheduler import local_model_async_slot
from src.llm_response_cache import LLMResponseCache

logger = logging.getLogger(__name__)
_ai_activity_cache_hit = contextvars.ContextVar("ai_activity_cache_hit", default=False)
_request_context_snapshots = contextvars.ContextVar(
    "request_context_snapshots",
    default=(),
)
_GEMMA_MAINTENANCE_CONTEXT_CAP = 8_192
_REQUEST_CONTEXT_BINDING_MAX = 32


def _request_context_key(endpoint_url: str, model: str) -> Tuple[str, str]:
    return (str(endpoint_url or "").strip().rstrip("/"), str(model or "").strip())


def get_request_context_snapshot(
    endpoint_url: str,
    model: str,
) -> Optional[ContextLengthSnapshot]:
    key = _request_context_key(endpoint_url, model)
    for bound_key, snapshot in reversed(_request_context_snapshots.get(())):
        if bound_key == key:
            return snapshot
    return None


def bind_request_context_snapshot(
    endpoint_url: str,
    model: str,
    snapshot: ContextLengthSnapshot,
) -> ContextLengthSnapshot:
    if not isinstance(snapshot, ContextLengthSnapshot):
        raise TypeError("snapshot must be a ContextLengthSnapshot")
    key = _request_context_key(endpoint_url, model)
    effective = _apply_request_context_profile(snapshot, model)
    bindings = tuple(
        item for item in _request_context_snapshots.get(()) if item[0] != key
    )
    bindings = (bindings + ((key, effective),))[-_REQUEST_CONTEXT_BINDING_MAX:]
    _request_context_snapshots.set(bindings)
    return effective


def clear_request_context_snapshots() -> None:
    _request_context_snapshots.set(())


def _apply_request_context_profile(
    snapshot: ContextLengthSnapshot,
    model: str,
) -> ContextLengthSnapshot:
    if str(model or "").strip() != DEFAULT_MAINTENANCE_MODEL:
        return snapshot
    discovered = snapshot.context_length if snapshot.known else _GEMMA_MAINTENANCE_CONTEXT_CAP
    effective = max(1, min(int(discovered or _GEMMA_MAINTENANCE_CONTEXT_CAP), _GEMMA_MAINTENANCE_CONTEXT_CAP))
    return ContextLengthSnapshot(
        context_length=effective,
        known=True,
        cache_status="gemma_profile_cap",
        endpoint_generation=snapshot.endpoint_generation,
    )


async def resolve_request_context_snapshot(
    endpoint_url: str,
    model: str,
    *,
    supplied_context_length: int | None = None,
    supplied_known: bool = True,
    probe_if_missing: bool = True,
) -> ContextLengthSnapshot:
    bound = get_request_context_snapshot(endpoint_url, model)
    if bound is not None:
        return bound
    if (
        not isinstance(supplied_context_length, bool)
        and isinstance(supplied_context_length, int)
        and supplied_context_length > 0
    ):
        snapshot = ContextLengthSnapshot(
            context_length=supplied_context_length,
            known=bool(supplied_known),
            cache_status="caller_supplied",
            endpoint_generation=0,
        )
    elif probe_if_missing:
        snapshot = await get_context_snapshot_async(endpoint_url, model)
    else:
        snapshot = ContextLengthSnapshot(
            context_length=DEFAULT_CONTEXT,
            known=False,
            cache_status="request_default",
            endpoint_generation=0,
        )
    return bind_request_context_snapshot(endpoint_url, model, snapshot)

class LLMConfig:
    """Configuration constants for LLM operations."""
    DEFAULT_TIMEOUT = 30
    DEFAULT_TEMPERATURE = 1.0
    DEFAULT_MAX_TOKENS = 0
    MAX_RETRIES = 3
    RETRY_DELAY = 0.5
    STREAM_TIMEOUT = 300
    # TCP+TLS connect budget for a SINGLE attempt. The old hard-coded 3.0s
    # assumed LAN/Tailscale peers ('SYN in <100ms'); it is too tight for public
    # cloud endpoints (offshore APIs take ~0.5-1.5s cold, with jitter), so a
    # brief blip on the first connect of an idle chat surfaced as a 503 on the
    # streaming path (which, unlike llm_call, does not retry the connect). A
    # genuinely dead upstream stays bounded by the dead-host cooldown. Override
    # with env LLM_CONNECT_TIMEOUT (seconds).
    CONNECT_TIMEOUT = float(os.getenv('LLM_CONNECT_TIMEOUT', '10') or '10')


def _call_timeout(read_timeout) -> httpx.Timeout:
    """Per-request timeout for non-streaming LLM calls (connect from config)."""
    return _call_timeout_impl(LLMConfig.CONNECT_TIMEOUT, read_timeout)


def _stream_timeout(read_timeout) -> httpx.Timeout:
    """Per-request timeout for streaming LLM calls (connect from config)."""
    return _stream_timeout_impl(LLMConfig.CONNECT_TIMEOUT, read_timeout)

_response_cache = LLMResponseCache()

# When a connect to a host fails, we mark it dead for DEAD_HOST_COOLDOWN seconds so
# subsequent calls fail instantly instead of waiting on the connect timeout. Keeps
# one unreachable upstream from jamming chat across the rest of the app.
#
# But a SINGLE transient blip (local model briefly busy, a momentary
# Tailscale hiccup) used to trip a full 60s lockout — the user saw a
# 503 and thought the model died when it was fine a second later. So:
#   - require FAIL_THRESHOLD consecutive failures before cooling
#   - shorter cooldown so recovery is quick
#   - any success resets the failure counter immediately
DEAD_HOST_COOLDOWN = 20.0
_HOST_FAIL_THRESHOLD = 2
_dead_hosts: Dict[str, float] = {}
_host_fails: Dict[str, int] = {}
# Guards the two maps above. The synchronous llm_call() runs inside FastAPI's
# threadpool (sync routes such as /sessions/auto-sort) while llm_call_async()
# runs on the event loop, so these maps are mutated from multiple OS threads.
# Without the lock the get()+1+set on _host_fails is a read-modify-write that
# loses failure counts under concurrent connect errors (issue #659).
_host_health_lock = threading.Lock()
from src.llm_stream_events import (
    _HarmonyStreamRouter,
    _HARMONY_MARKER_RE,
    _HARMONY_MARKERS,
    _HARMONY_MAX_MARKER_LEN,
    _harmony_suffix_hold_len,
    _stream_delta_event,
)
from src.llm_activity_metrics import (
    record_ai_activity_safe as _record_ai_activity_safe,
    sse_activity_delta_chars as _sse_activity_delta_chars,
    sse_activity_error_class as _sse_activity_error_class,
    sse_activity_usage as _sse_activity_usage,
)
from src.llm_async_call import llm_call_async_impl as _llm_call_async_impl_helper
from src.llm_cache_key import _get_cache_key
from src.llm_runtime_state import (
    call_timeout as _call_timeout_impl,
    clear_host_dead as _clear_host_dead_impl,
    get_shared_http_client,
    get_cached_response as _get_cached_response_impl,
    host_key as _host_key_impl,
    is_host_dead as _is_host_dead_impl,
    mark_host_dead as _mark_host_dead_impl,
    note_model_activity as _note_model_activity_impl,
    same_model_identity as _same_model_identity_impl,
    seconds_since_model_activity as _seconds_since_model_activity_impl,
    set_cached_response as _set_cached_response_impl,
    stream_timeout as _stream_timeout_impl,
)
from src.llm_stream_audit import stream_llm_with_activity as _stream_llm_with_activity_impl
from src.llm_stream_chatgpt_subscription import (
    stream_chatgpt_subscription_response as _stream_chatgpt_subscription_response_impl,
)


def _same_model_identity(left: str, right: str) -> bool:
    return _same_model_identity_impl(left, right)

def note_model_activity(url: str, model: str):
    """Record that a real upstream request used this endpoint/model."""
    _note_model_activity_impl(url, model)

def seconds_since_model_activity(url: str, model: str) -> Optional[float]:
    """Seconds since the endpoint/model was last used in this process."""
    return _seconds_since_model_activity_impl(url, model)

def _host_key(url: str) -> str:
    return _host_key_impl(url)

def _is_host_dead(url: str) -> bool:
    return _is_host_dead_impl(url, _dead_hosts, _host_health_lock)

def _mark_host_dead(url: str) -> bool:
    """Record a connect failure. Only actually cools the host after
    _HOST_FAIL_THRESHOLD consecutive failures. Returns True if the host
    is now cooled (so callers can log accurately), False if it's still
    within its allowed-failure grace."""
    return _mark_host_dead_impl(
        url,
        _host_fails,
        _dead_hosts,
        _host_health_lock,
        fail_threshold=_HOST_FAIL_THRESHOLD,
        cooldown_seconds=DEAD_HOST_COOLDOWN,
    )

def _clear_host_dead(url: str) -> None:
    _clear_host_dead_impl(url, _host_fails, _dead_hosts, _host_health_lock)


def _get_http_client() -> httpx.AsyncClient:
    """Return process-wide AsyncClient. Per-request timeout is passed at call time."""
    return get_shared_http_client()

def _get_cached_response(cache_key: str) -> Optional[str]:
    """Get cached response if it exists."""
    return _get_cached_response_impl(_response_cache, cache_key, _ai_activity_cache_hit.set)

def _set_cached_response(cache_key: str, response: str) -> None:
    """Store response in cache."""
    _set_cached_response_impl(_response_cache, cache_key, response)

# ── Anthropic native API adapter ──

ANTHROPIC_MODELS = [
    "claude-opus-4-20250514", "claude-opus-4",
    "claude-sonnet-4-20250514", "claude-sonnet-4", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5",
    "claude-haiku-4-20250514", "claude-haiku-4", "claude-haiku-3-5-20241022", "claude-haiku-3-5",
]


from src.llm_ollama import (
    _build_ollama_payload,
    _is_ollama_native_url,
    _is_ollama_openai_compat_url,
    _normalize_ollama_url,
    _ollama_api_root,
    _ollama_normalize_messages,
    _ollama_normalize_tool_messages,
    _parse_ollama_response,
)


from src.llm_provider_helpers import (
    _detect_provider as _detect_provider_impl,
    _host_match,
    _provider_headers,
    _provider_label as _provider_label_impl,
)

from src.llm_kimi_code import (
    KIMI_CODE_USER_AGENTS,
    KIMI_CODE_USER_AGENT,
    _is_kimi_code_access_denied,
    _is_kimi_code_url,
    _kimi_code_base_key,
    _kimi_code_ua_cache,
    _kimi_code_ua_candidates,
    _remember_kimi_code_user_agent,
    apply_kimi_code_headers,
    httpx_get_kimi_aware,
    httpx_post_kimi_aware,
    httpx_post_kimi_aware_async,
)


def _detect_provider(url: str) -> str:
    return _detect_provider_impl(url, is_ollama_native_url_func=_is_ollama_native_url)


def _provider_label(url: str) -> str:
    return _provider_label_impl(url, is_ollama_native_url_func=_is_ollama_native_url)


def _is_self_hosted_openai_compatible(url: str) -> bool:
    if _detect_provider(url) != "openai" or _host_match(url, "openai.com"):
        return False
    from src.model_context import is_local_endpoint
    return is_local_endpoint(url)


def _apply_local_cache_affinity(payload: Dict, url: str, session_id: Optional[str]) -> None:
    if not session_id:
        return
    if not _is_self_hosted_openai_compatible(url):
        return
    payload.setdefault("session_id", str(session_id))
    payload.setdefault("cache_prompt", True)


from src.llm_chatgpt_subscription import (
    build_chatgpt_responses_payload as _build_chatgpt_responses_payload_impl,
    chatgpt_subscription_instructions as _chatgpt_subscription_instructions,
    message_content_as_text as _message_content_as_text,
    normalize_chatgpt_subscription_url as _normalize_chatgpt_subscription_url,
)
from src.llm_error_formatting import (
    _format_chatgpt_subscription_error as _format_chatgpt_subscription_error_impl,
    _format_upstream_error as _format_upstream_error_impl,
)
from src.llm_fallbacks import (
    _dedupe_candidates,
    _fallback_notice_event,
    _no_model_endpoint_event,
    _summarize_stream_error,
    llm_call_async_with_fallback as _llm_call_async_with_fallback_impl,
    llm_call_with_fallback as _llm_call_with_fallback_impl,
    stream_llm_with_fallback as _stream_llm_with_fallback_impl,
)


def _build_chatgpt_responses_payload(
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
    *,
    stream: bool = False,
) -> Dict:
    return _build_chatgpt_responses_payload_impl(
        model,
        messages,
        temperature,
        max_tokens,
        stream=stream,
        restricts_temperature=_restricts_temperature,
    )

def _format_chatgpt_subscription_error(status_code: int, text: str) -> str:
    return _format_chatgpt_subscription_error_impl(
        status_code,
        text,
        upstream_formatter=_format_upstream_error,
    )


def _format_upstream_error(status: int, body: bytes | str, url: str) -> str:
    return _format_upstream_error_impl(status, body, url, provider_label_func=_provider_label)


from src.llm_message_formats import (
    _MISTRAL_REASONING_EFFORT,
    _anthropic_rejects_temperature,
    _apply_visible_reasoning_guard,
    _as_content_blocks,
    _build_anthropic_headers,
    _build_anthropic_payload,
    _convert_openai_content_to_anthropic,
    _normalize_mistral_content,
    _parse_openai_compatible_message,
    _parse_anthropic_response,
    _sanitize_llm_messages,
    _supports_thinking,
)
from src.llm_model_cache import (
    _configured_cached_model_ids,
)
from src.llm_model_listing import (
    list_model_ids as _list_model_ids_impl,
    normalize_model_id as _normalize_model_id_impl,
)
from src.llm_request_policy import (
    _moonshot_rejects_custom_temperature,
    _normalize_anthropic_url,
    _omit_temperature,
    _restricts_temperature,
    _uses_max_completion_tokens,
)
from src.llm_sync_call import llm_call_impl as _llm_call_impl_helper


def list_model_ids(
    base_chat_url: str,
    timeout: int = LLMConfig.DEFAULT_TIMEOUT,
    headers: Optional[Dict] = None,
    *,
    owner: Optional[str] = None,
    endpoint_id: Optional[str] = None,
) -> List[str]:
    """List available model IDs from an endpoint."""
    from src.endpoint_resolver import build_models_url

    return _list_model_ids_impl(
        base_chat_url,
        timeout=timeout,
        headers=headers,
        owner=owner,
        endpoint_id=endpoint_id,
        configured_cached_model_ids_func=_configured_cached_model_ids,
        detect_provider_func=_detect_provider,
        anthropic_models=list(ANTHROPIC_MODELS),
        ollama_api_root_func=_ollama_api_root,
        build_models_url_func=build_models_url,
        httpx_get_func=httpx_get_kimi_aware,
        http_get_func=httpx.get,
        logger=logger,
    )


def normalize_model_id(
    endpoint_url: str,
    requested: str,
    timeout: int = LLMConfig.DEFAULT_TIMEOUT,
    *,
    owner: Optional[str] = None,
    endpoint_id: Optional[str] = None,
) -> Optional[str]:
    """Normalize a model ID to match available models."""
    return _normalize_model_id_impl(
        endpoint_url,
        requested,
        timeout=timeout,
        owner=owner,
        endpoint_id=endpoint_id,
        list_model_ids_func=list_model_ids,
    )

def _llm_call_impl(url: str, model: str, messages: List[Dict], temperature: float = LLMConfig.DEFAULT_TEMPERATURE,
             max_tokens: int = LLMConfig.DEFAULT_MAX_TOKENS, headers: Optional[Dict] = None,
             timeout: int = LLMConfig.DEFAULT_TIMEOUT, prompt_type: Optional[str] = None,
             surface: Optional[str] = None) -> str:
    """Synchronous LLM call with optional prompt type enhancement."""
    return _llm_call_impl_helper(
        url,
        model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        headers=headers,
        timeout=timeout,
        http_exception_cls=HTTPException,
        logger=logger,
        provider_headers_func=_provider_headers,
        detect_provider_func=_detect_provider,
        sanitize_messages_func=_sanitize_llm_messages,
        visible_reasoning_guard_func=_apply_visible_reasoning_guard,
        get_cache_key_func=_get_cache_key,
        get_cached_response_func=_get_cached_response,
        set_cached_response_func=_set_cached_response,
        normalize_anthropic_url_func=_normalize_anthropic_url,
        build_anthropic_headers_func=_build_anthropic_headers,
        build_anthropic_payload_func=_build_anthropic_payload,
        normalize_ollama_url_func=_normalize_ollama_url,
        build_ollama_payload_func=_build_ollama_payload,
        get_context_length_func=get_context_length,
        omit_temperature_func=_omit_temperature,
        uses_max_completion_tokens_func=_uses_max_completion_tokens,
        supports_thinking_func=_supports_thinking,
        mistral_reasoning_effort=_MISTRAL_REASONING_EFFORT,
        note_model_activity_func=note_model_activity,
        httpx_post_func=httpx_post_kimi_aware,
        parse_anthropic_response_func=_parse_anthropic_response,
        parse_ollama_response_func=_parse_ollama_response,
        normalize_mistral_content_func=_normalize_mistral_content,
        parse_openai_message_func=_parse_openai_compatible_message,
        prompt_type=prompt_type,
        surface=surface,
    )

def llm_call(
    url: str,
    model: str,
    messages: List[Dict],
    temperature: float = LLMConfig.DEFAULT_TEMPERATURE,
    max_tokens: int = LLMConfig.DEFAULT_MAX_TOKENS,
    headers: Optional[Dict] = None,
    timeout: int = LLMConfig.DEFAULT_TIMEOUT,
    prompt_type: Optional[str] = None,
    *,
    owner: Optional[str] = None,
    surface: Optional[str] = None,
    correlation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    doc_id: Optional[str] = None,
) -> str:
    """Synchronous LLM call with redacted AI-activity audit metadata."""

    token = _ai_activity_cache_hit.set(False)
    start = time.time()
    status = "success"
    error_class = None
    response = ""
    try:
        response = _llm_call_impl(
            url,
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=headers,
            timeout=timeout,
            prompt_type=prompt_type,
            surface=surface,
        )
        return response
    except Exception as exc:
        status = "error"
        error_class = type(exc).__name__
        raise
    finally:
        duration_ms = int((time.time() - start) * 1000)
        cache_hit = bool(_ai_activity_cache_hit.get(False))
        _ai_activity_cache_hit.reset(token)
        _record_ai_activity_safe(
            owner=owner,
            surface=surface,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
            doc_id=doc_id,
            prompt_type=prompt_type or "llm_call",
            provider=_detect_provider(url),
            endpoint_url=url,
            model=model,
            messages=messages,
            output_chars=len(response or ""),
            duration_ms=duration_ms,
            status=status,
            error_class=error_class,
            cache_hit=cache_hit,
        )


def llm_call_with_fallback(candidates, messages, **kwargs) -> str:
    """Sync `llm_call` with an ordered fallback chain."""
    return _llm_call_with_fallback_impl(
        candidates,
        messages,
        llm_call_func=llm_call,
        http_exception_cls=HTTPException,
        logger=logger,
        **kwargs,
    )


async def llm_call_async_with_fallback(candidates, messages, **kwargs) -> str:
    """Async variant of `llm_call_with_fallback` with the same semantics."""
    return await _llm_call_async_with_fallback_impl(
        candidates,
        messages,
        llm_call_async_func=llm_call_async,
        http_exception_cls=HTTPException,
        logger=logger,
        **kwargs,
    )

async def _llm_call_async_impl(
    url: str,
    model: str,
    messages: List[Dict],
    temperature: float = LLMConfig.DEFAULT_TEMPERATURE,
    max_tokens: int = LLMConfig.DEFAULT_MAX_TOKENS,
    headers: Optional[Dict] = None,
    timeout: int = LLMConfig.STREAM_TIMEOUT,
    max_retries: int = LLMConfig.MAX_RETRIES,
    prompt_type: Optional[str] = None,
    session_id: Optional[str] = None,
    surface: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    """Async LLM call with connection pooling, timeout, retry and logging."""
    provider = _detect_provider(url)
    context_snapshot = None
    if provider == "ollama":
        context_snapshot = await resolve_request_context_snapshot(url, model)
    return await _llm_call_async_impl_helper(
        url,
        model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        headers=headers,
        timeout=timeout,
        max_retries=max_retries,
        session_id=session_id,
        prompt_type=prompt_type,
        surface=surface,
        retry_delay=LLMConfig.RETRY_DELAY,
        dead_host_cooldown=DEAD_HOST_COOLDOWN,
        http_exception_cls=HTTPException,
        connect_error_classes=(httpx.ConnectError, httpx.ConnectTimeout),
        request_error_classes=(httpx.RequestError, httpx.HTTPStatusError),
        logger=logger,
        detect_provider_func=_detect_provider,
        sanitize_messages_func=_sanitize_llm_messages,
        visible_reasoning_guard_func=_apply_visible_reasoning_guard,
        get_cache_key_func=_get_cache_key,
        get_cached_response_func=_get_cached_response,
        set_cached_response_func=_set_cached_response,
        stream_llm_func=stream_llm,
        normalize_anthropic_url_func=_normalize_anthropic_url,
        build_anthropic_headers_func=_build_anthropic_headers,
        build_anthropic_payload_func=_build_anthropic_payload,
        normalize_ollama_url_func=_normalize_ollama_url,
        build_ollama_payload_func=_build_ollama_payload,
        get_context_length_func=(
            (lambda _url, _model: context_snapshot.context_length)
            if context_snapshot is not None
            else (lambda _url, _model: DEFAULT_CONTEXT)
        ),
        provider_headers_func=_provider_headers,
        omit_temperature_func=_omit_temperature,
        uses_max_completion_tokens_func=_uses_max_completion_tokens,
        is_ollama_openai_compat_url_func=_is_ollama_openai_compat_url,
        supports_thinking_func=_supports_thinking,
        mistral_reasoning_effort=_MISTRAL_REASONING_EFFORT,
        apply_local_cache_affinity_func=_apply_local_cache_affinity,
        is_host_dead_func=_is_host_dead,
        host_key_func=_host_key,
        call_timeout_func=_call_timeout,
        note_model_activity_func=note_model_activity,
        get_http_client_func=_get_http_client,
        httpx_post_async_func=httpx_post_kimi_aware_async,
        direct_transport=transport,
        format_upstream_error_func=_format_upstream_error,
        clear_host_dead_func=_clear_host_dead,
        parse_anthropic_response_func=_parse_anthropic_response,
        parse_ollama_response_func=_parse_ollama_response,
        normalize_mistral_content_func=_normalize_mistral_content,
        parse_openai_message_func=_parse_openai_compatible_message,
        mark_host_dead_func=_mark_host_dead,
    )

async def llm_call_async(
    url: str,
    model: str,
    messages: List[Dict],
    temperature: float = LLMConfig.DEFAULT_TEMPERATURE,
    max_tokens: int = LLMConfig.DEFAULT_MAX_TOKENS,
    headers: Optional[Dict] = None,
    timeout: int = LLMConfig.STREAM_TIMEOUT,
    max_retries: int = LLMConfig.MAX_RETRIES,
    prompt_type: Optional[str] = None,
    session_id: Optional[str] = None,
    *,
    owner: Optional[str] = None,
    surface: Optional[str] = None,
    correlation_id: Optional[str] = None,
    task_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    """Asynchronous LLM call with redacted AI-activity audit metadata."""

    token = _ai_activity_cache_hit.set(False)
    start = time.time()
    status = "success"
    error_class = None
    response = ""
    try:
        response = await _llm_call_async_impl(
            url,
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
            prompt_type=prompt_type,
            session_id=session_id,
            surface=surface,
            transport=transport,
        )
        return response
    except Exception as exc:
        status = "error"
        error_class = type(exc).__name__
        raise
    finally:
        duration_ms = int((time.time() - start) * 1000)
        cache_hit = bool(_ai_activity_cache_hit.get(False))
        _ai_activity_cache_hit.reset(token)
        _record_ai_activity_safe(
            owner=owner,
            surface=surface,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
            doc_id=doc_id,
            prompt_type=prompt_type or "llm_call_async",
            provider=_detect_provider(url),
            endpoint_url=url,
            model=model,
            messages=messages,
            output_chars=len(response or ""),
            duration_ms=duration_ms,
            status=status,
            error_class=error_class,
            cache_hit=cache_hit,
        )


async def _stream_llm_impl(url: str, model: str, messages: List[Dict], temperature: float = LLMConfig.DEFAULT_TEMPERATURE,
                     max_tokens: int = LLMConfig.DEFAULT_MAX_TOKENS, headers: Optional[Dict] = None,
                     timeout: int = LLMConfig.STREAM_TIMEOUT, prompt_type: Optional[str] = None,
                     tools: Optional[List[Dict]] = None, session_id: Optional[str] = None):
    """Stream LLM responses with improved error handling.

    Yields SSE chunks:
      - data: {"delta": "text"}           — text content
      - data: {"type": "tool_calls", ...}  — accumulated native tool calls (before DONE)
      - event: error                       — errors
      - data: [DONE]                       — end of stream
    """
    provider = _detect_provider(url)
    context_snapshot = None
    if provider == "ollama":
        context_snapshot = await resolve_request_context_snapshot(url, model)
    messages_copy = _sanitize_llm_messages(messages)

    # Consolidate multiple system messages into one at the start.
    # Some models (e.g. Qwen3.5) reject system messages that aren't first.
    sys_parts = []
    non_sys = []
    for m in messages_copy:
        if m.get("role") == "system":
            sys_parts.append(m.get('content') or '')
        else:
            non_sys.append(m)
    if sys_parts:
        messages_copy = [{"role": "system", "content": "\n\n".join(sys_parts)}] + non_sys
    else:
        messages_copy = non_sys

    if provider == "anthropic":
        target_url = _normalize_anthropic_url(url)
        h = _build_anthropic_headers(headers)
        payload = _build_anthropic_payload(model, messages_copy, temperature, max_tokens, stream=True, tools=tools)
    elif provider == "ollama":
        target_url = _normalize_ollama_url(url)
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        payload = _build_ollama_payload(
            model, messages_copy, temperature, max_tokens,
            stream=True,
            tools=tools,
            num_ctx=context_snapshot.context_length if context_snapshot is not None else None,
            think=False if _supports_thinking(model) else None,
        )
    elif provider == "chatgpt-subscription":
        target_url = _normalize_chatgpt_subscription_url(url)
        h = _provider_headers(provider, headers)
        payload = _build_chatgpt_responses_payload(model, messages_copy, temperature, max_tokens, stream=True)
    else:
        target_url = url
        payload = {
            "model": model,
            "messages": messages_copy,
            "temperature": temperature,
            "stream": True,
        }
        if _omit_temperature(provider, model):
            payload.pop("temperature", None)
        if provider not in {"openrouter", "groq"}:
            payload["stream_options"] = {"include_usage": True}
        if max_tokens and max_tokens > 0:
            tok_key = "max_completion_tokens" if _uses_max_completion_tokens(model) else "max_tokens"
            payload[tok_key] = max_tokens
        if tools:
            payload["tools"] = tools
        if provider == "mistral" and _supports_thinking(model):
            payload["reasoning_effort"] = _MISTRAL_REASONING_EFFORT
        # For Ollama's OpenAI-compat /v1 endpoint with thinking models (qwen3,
        # gemma4, etc.), suppress thinking so tool calls aren't swallowed inside
        # <think> blocks. Ollama /v1 accepts "think": false as a top-level param.
        if _is_ollama_openai_compat_url(url) and _supports_thinking(model):
            payload["think"] = False
        _apply_local_cache_affinity(payload, url, session_id)
        h = _provider_headers(provider, headers)
        if provider == "copilot":
            from src.copilot import apply_request_headers
            apply_request_headers(h, messages_copy)

    # Connect budget from LLMConfig.CONNECT_TIMEOUT (env LLM_CONNECT_TIMEOUT).
    # The dead-host cooldown still bounds a genuinely unreachable upstream, so a
    # wider connect budget only affects first contact and stops a brief cold
    # connect blip (offshore/public endpoints) surfacing as a 503 on this stream
    # path, which -- unlike llm_call -- does not retry the connect.
    stream_timeout = _stream_timeout(timeout)

    if _is_host_dead(target_url):
        yield f'event: error\ndata: {json.dumps({"error": f"Upstream {_host_key(target_url)} unreachable (cooldown active)", "status": 503})}\n\n'
        return
    note_model_activity(target_url, model)

    # ChatGPT Subscription / Codex Responses streaming
    if provider == "chatgpt-subscription":
        async for chunk in _stream_chatgpt_subscription_response_impl(
            target_url,
            payload,
            h,
            stream_timeout,
            get_http_client_func=_get_http_client,
            clear_host_dead_func=_clear_host_dead,
            format_error_func=_format_chatgpt_subscription_error,
            mark_host_dead_func=_mark_host_dead,
            host_key_func=_host_key,
            dead_host_cooldown=DEAD_HOST_COOLDOWN,
            logger=logger,
            connect_error_classes=(httpx.ConnectError, httpx.ConnectTimeout),
            read_timeout_cls=httpx.ReadTimeout,
            network_error_cls=httpx.NetworkError,
        ):
            yield chunk
        return
    # ── Native Ollama streaming ──
    if provider == "ollama":
        _ollama_tool_calls: List[Dict] = []
        _harmony_router = _HarmonyStreamRouter()
        try:
            client = _get_http_client()
            # The single shared lease spans the complete native stream
            # lifetime and is released by EOF, error, cancellation, or
            # explicit async-generator close.
            async with local_model_async_slot(
                target_url,
                model,
                provider=provider,
                prompt_type=prompt_type,
            ):
                async with client.stream('POST', target_url, json=payload, headers=h, timeout=stream_timeout) as r:
                    _clear_host_dead(target_url)
                    if r.status_code != 200:
                        raw = (await r.aread()).decode(errors="replace")
                        friendly = _format_upstream_error(r.status_code, raw, target_url)
                        yield f'event: error\ndata: {json.dumps({"status": r.status_code, "text": friendly, "raw": raw[:500]})}\n\n'
                        return
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        try:
                            j = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        message = j.get("message") or {}
                        thinking = message.get("thinking") or ""
                        if thinking:
                            yield _stream_delta_event(thinking, thinking=True)
                        content = message.get("content") or ""
                        if content:
                            for part, is_thinking in _harmony_router.feed(content):
                                yield _stream_delta_event(part, thinking=is_thinking)
                        for tc in message.get("tool_calls") or []:
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                _ollama_tool_calls.append({
                                    "id": tc.get("id") or f"call_{len(_ollama_tool_calls)}",
                                    "name": fn.get("name") or "",
                                    "arguments": json.dumps(fn.get("arguments") or {}),
                                })
                        if j.get("done"):
                            for part, is_thinking in _harmony_router.flush():
                                yield _stream_delta_event(part, thinking=is_thinking)
                            if _ollama_tool_calls:
                                yield f'data: {json.dumps({"type": "tool_calls", "calls": _ollama_tool_calls})}\n\n'
                            if j.get("prompt_eval_count") is not None or j.get("eval_count") is not None:
                                yield f'data: {json.dumps({"type": "usage", "data": {"input_tokens": j.get("prompt_eval_count", 0), "output_tokens": j.get("eval_count", 0)}})}\n\n'
                            yield "data: [DONE]\n\n"
                            return
                    for part, is_thinking in _harmony_router.flush():
                        yield _stream_delta_event(part, thinking=is_thinking)
                    yield "data: [DONE]\n\n"
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            _cooled = _mark_host_dead(target_url)
            _tail = f" — host cooled for {DEAD_HOST_COOLDOWN:.0f}s" if _cooled else " — transient, will retry"
            logger.warning(f"Ollama stream connect to {target_url} failed: {e}{_tail}")
            yield f'event: error\ndata: {json.dumps({"error": f"Cannot reach {_host_key(target_url)}", "status": 503})}\n\n'
        except httpx.ReadTimeout:
            yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
        except httpx.NetworkError:
            yield f'event: error\ndata: {json.dumps({"error": "Network error", "status": 502})}\n\n'
        except Exception as e:
            logger.error(f"Ollama stream error: {e}")
            yield f'event: error\ndata: {json.dumps({"error": str(e), "status": 502})}\n\n'
        return

    # ── Anthropic streaming ──
    if provider == "anthropic":
        _anth_input_tokens = 0
        _anth_output_tokens = 0
        # Track tool_use blocks: {index: {id, name, arguments_json}}
        _anth_tool_blocks: Dict[int, Dict] = {}
        _anth_block_idx = -1
        _anth_block_type = ""
        try:
            client = _get_http_client()
            async with client.stream('POST', target_url, json=payload, headers=h, timeout=stream_timeout) as r:
                _clear_host_dead(target_url)
                if r.status_code != 200:
                    raw = (await r.aread()).decode(errors="replace")
                    friendly = _format_upstream_error(r.status_code, raw, target_url)
                    yield f'event: error\ndata: {json.dumps({"status": r.status_code, "text": friendly, "raw": raw[:500]})}\n\n'
                    return
                async for line in r.aiter_lines():
                    # SSE allows "data:value" with no space after the colon
                    # (the space is optional per the spec). Some gateways and
                    # local servers omit it; gating on "data: " dropped their
                    # entire stream.
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or not data.startswith("{"):
                        continue
                    try:
                        j = json.loads(data)
                        evt = j.get("type", "")
                        if evt == "content_block_start":
                            _anth_block_idx = j.get("index", _anth_block_idx + 1)
                            cb = j.get("content_block") or {}
                            _anth_block_type = cb.get("type", "text")
                            if _anth_block_type == "tool_use":
                                _anth_tool_blocks[_anth_block_idx] = {
                                    "id": cb.get("id") or f"call_{_anth_block_idx}",
                                    "name": cb.get("name") or "",
                                    "arguments": "",
                                }
                        elif evt == "content_block_delta":
                            delta = j.get("delta") or {}
                            delta_type = delta.get("type", "")
                            if delta_type == "text_delta":
                                text = delta.get("text") or ""
                                if text:
                                    yield f'data: {json.dumps({"delta": text})}\n\n'
                            elif delta_type == "input_json_delta":
                                # Accumulate tool arguments JSON
                                idx = j.get("index", _anth_block_idx)
                                if idx in _anth_tool_blocks:
                                    partial = delta.get("partial_json") or ""
                                    _anth_tool_blocks[idx]["arguments"] += partial
                                    # Stream tool arg deltas for doc tools
                                    if partial and _anth_tool_blocks[idx].get("name") in ("create_document", "update_document", "edit_document"):
                                        yield f'data: {json.dumps({"type": "tool_call_delta", "index": idx, "name": _anth_tool_blocks[idx]["name"], "arg_delta": partial})}\n\n'
                        elif evt == "message_start":
                            _u = j.get("message", {}).get("usage", {})
                            _anth_input_tokens = _u.get("input_tokens", 0)
                            # Surface prompt-cache effectiveness: cache_read > 0 means the
                            # stable system+tools prefix was served from cache this round.
                            _c_read = _u.get("cache_read_input_tokens", 0)
                            _c_write = _u.get("cache_creation_input_tokens", 0)
                            if _c_read or _c_write:
                                logger.info(
                                    "[anthropic-cache] read=%s write=%s fresh_input=%s",
                                    _c_read, _c_write, _anth_input_tokens,
                                )
                        elif evt == "message_delta":
                            _anth_output_tokens = j.get("usage", {}).get("output_tokens", 0)
                        elif evt == "message_stop":
                            # Emit accumulated tool calls in OpenAI-compatible format
                            if _anth_tool_blocks:
                                calls = []
                                for idx in sorted(_anth_tool_blocks):
                                    tb = _anth_tool_blocks[idx]
                                    calls.append({
                                        "id": tb["id"],
                                        "name": tb["name"],
                                        "arguments": tb["arguments"],
                                    })
                                yield f'data: {json.dumps({"type": "tool_calls", "calls": calls})}\n\n'
                            if _anth_input_tokens or _anth_output_tokens:
                                yield f'data: {json.dumps({"type": "usage", "data": {"input_tokens": _anth_input_tokens, "output_tokens": _anth_output_tokens}})}\n\n'
                            yield "data: [DONE]\n\n"
                            return
                        elif evt == "error":
                            err_msg = j.get("error", {}).get("message", "Unknown error")
                            yield f'event: error\ndata: {json.dumps({"error": err_msg, "status": 400})}\n\n'
                            return
                    except json.JSONDecodeError:
                        continue
                yield "data: [DONE]\n\n"
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            _cooled = _mark_host_dead(target_url)
            _tail = f" — host cooled for {DEAD_HOST_COOLDOWN:.0f}s" if _cooled else " — transient, will retry"
            logger.warning(f"Anthropic stream connect to {target_url} failed: {e}{_tail}")
            yield f'event: error\ndata: {json.dumps({"error": f"Cannot reach {_host_key(target_url)}", "status": 503})}\n\n'
        except httpx.ReadTimeout:
            yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
        except httpx.NetworkError:
            yield f'event: error\ndata: {json.dumps({"error": "Network error", "status": 502})}\n\n'
        except Exception as e:
            logger.error(f"Anthropic stream error: {e}")
            yield f'event: error\ndata: {json.dumps({"error": str(e), "status": 502})}\n\n'
        return

    # ── OpenAI-compatible streaming ──
    # Accumulate native tool_calls across streaming chunks
    _tc_acc: Dict[int, Dict] = {}  # index -> {id, name, arguments}
    _tc_last_idx = [-1]  # most-recently-touched slot, for providers that omit `index`
    # For thinking models: prepend <think> to first content delta so frontend
    # can detect thinking-in-progress (some models output </think> but no <think>)
    _thinking_model = _supports_thinking(model)
    _first_content_sent = False
    _in_think_tag = False        # True while consuming <think>…</think> content
    _think_open_stripped = False  # opening <think> tag already removed
    _harmony_router = _HarmonyStreamRouter()
    _harmony_active = False       # sticky: gpt-oss harmony <|channel|> stream detected
    _actual_model = ""
    _actual_model_announced = False

    def _emit_tool_calls():
        """Build the tool_calls event string if any were accumulated."""
        if not _tc_acc:
            return None
        calls = [_tc_acc[i] for i in sorted(_tc_acc)]
        return f'data: {json.dumps({"type": "tool_calls", "calls": calls})}\n\n'

    def _format_routed_content(parts: List[Tuple[str, bool]]) -> List[str]:
        nonlocal _first_content_sent
        events = []
        for part, is_thinking in parts:
            if is_thinking:
                events.append(_stream_delta_event(part, thinking=True))
                continue
            # Some thinking backends start normal content with a stray closing
            # tag. Repair only that shape; do not wrap every first token for
            # model families like MiniMax, which often stream ordinary answers.
            if _thinking_model and not _first_content_sent and part.lstrip().lower().startswith("</think"):
                part = "<think>" + part
            _first_content_sent = True
            events.append(_stream_delta_event(part))
        return events

    h = apply_kimi_code_headers(h, target_url)
    try:
        client = _get_http_client()
        # This includes local OpenAI-compatible /v1 servers.  Their provider
        # label is intentionally still ``openai``; the scheduler recognizes
        # only bounded local endpoints and leaves all cloud streams untouched.
        async with local_model_async_slot(
            target_url,
            model,
            provider=provider,
            prompt_type=prompt_type,
        ), client.stream('POST', target_url, json=payload, headers=h, timeout=stream_timeout) as r:
            _clear_host_dead(target_url)
            if r.status_code != 200:
                raw = (await r.aread()).decode(errors="replace")
                friendly = _format_upstream_error(r.status_code, raw, target_url)
                yield f'event: error\ndata: {json.dumps({"status": r.status_code, "text": friendly, "raw": raw[:500]})}\n\n'
                return

            async for line in r.aiter_lines():
                if not line:
                    continue

                # SSE allows "data:value" with no space after the colon; gating
                # on "data: " silently dropped content + usage from providers
                # that omit it.
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        for event in _format_routed_content(_harmony_router.flush()):
                            yield event
                        tc_event = _emit_tool_calls()
                        if tc_event:
                            yield tc_event
                        yield "data: [DONE]\n\n"
                        return

                    try:
                        if data.strip():
                            if data.startswith("{"):
                                j = json.loads(data)
                                chunk_model = j.get("model")
                                if isinstance(chunk_model, str) and chunk_model.strip():
                                    _actual_model = chunk_model.strip()
                                    if (
                                        not _actual_model_announced
                                        and not _same_model_identity(_actual_model, model)
                                    ):
                                        _actual_model_announced = True
                                        yield f'data: {json.dumps({"type": "model_actual", "requested_model": model, "model": _actual_model})}\n\n'
                                # Usage chunk (from stream_options)
                                _choices = j.get("choices") or []
                                _delta0 = _choices[0].get("delta") if (_choices and _choices[0] is not None) else None
                                # Capture usage whenever the chunk carries it and
                                # the delta has no actual output. Some gateways /
                                # local servers attach usage to the FINAL delta,
                                # which also carries role/finish_reason (so it is
                                # not exactly None/{}/{"content": None}); gating on
                                # those exact shapes discarded their token counts.
                                _delta_has_output = isinstance(_delta0, dict) and (
                                    _delta0.get("content")
                                    or _delta0.get("reasoning_content")
                                    or _delta0.get("reasoning")
                                    or _delta0.get("thinking")
                                    or _delta0.get("tool_calls")
                                )
                                if "usage" in j and not _delta_has_output:
                                    u = j["usage"] or {}
                                    _usage_data = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}
                                    # llama.cpp puts a `timings` block alongside `usage` with the
                                    # TRUE generation speed (predicted_per_second) — pure decode,
                                    # excluding prefill/network. Pass it through so the UI shows the
                                    # real gen t/s instead of recomputing tokens/wall-clock (which
                                    # includes prefill and reads ~20-40% low). Prefill speed too.
                                    _tm = j.get("timings")
                                    if isinstance(_tm, dict):
                                        if _tm.get("predicted_per_second"):
                                            _usage_data["gen_tps"] = round(_tm["predicted_per_second"], 2)
                                        if _tm.get("prompt_per_second"):
                                            _usage_data["prefill_tps"] = round(_tm["prompt_per_second"], 2)
                                    if _actual_model:
                                        _usage_data["model"] = _actual_model
                                        if not _same_model_identity(_actual_model, model):
                                            _usage_data["requested_model"] = model
                                    yield f'data: {json.dumps({"type": "usage", "data": _usage_data})}\n\n'
                                elif "choices" in j:
                                    _c0 = (j["choices"] or [None])[0]
                                    if _c0 is None:
                                        continue
                                    delta = _c0.get("delta") or {}
                                    if isinstance(delta, dict):
                                        # Text content
                                        # Reasoning tokens (VLLM --reasoning-parser, e.g. Qwen3/DeepSeek-R1, Nemotron). vLLM 0.20.2 / NIM emit the field as `reasoning`; older builds use `reasoning_content`. Some OpenAI-compatible Ollama builds use `thinking`.
                                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking") or ""
                                        content = delta.get("content") or ""
                                        if isinstance(content, list):
                                            text_part, thinking_part = _normalize_mistral_content(content)
                                            if thinking_part:
                                                reasoning = (reasoning + thinking_part) if reasoning else thinking_part
                                            content = text_part
                                        if reasoning:
                                            yield _stream_delta_event(reasoning, thinking=True)
                                        if content:
                                            stripped = content.lstrip()
                                            # gpt-oss harmony format (<|channel|>analysis/final): route via the harmony
                                            # stream router. Sticky once the first marker appears — distinct from the
                                            # <think> path below (handled in the else, preserving #2588 behaviour).
                                            if _harmony_active or "<|" in content:
                                                _harmony_active = True
                                                for event in _format_routed_content(_harmony_router.feed(content)):
                                                    yield event
                                            else:
                                                # Auto-detect <think>…</think> in content stream.
                                                # Covers Qwen3-derived models (Qwopus, QwQ forks) whose
                                                # names don't match _THINKING_MODEL_PATTERNS but still
                                                # emit literal <think> markup via llama.cpp --jinja.
                                                if not _first_content_sent and not _thinking_model and not _in_think_tag and stripped.lower().startswith("<think"):
                                                    _thinking_model = True
                                                    _in_think_tag = True
                                                if _in_think_tag:
                                                    close_idx = content.lower().find("</think>")
                                                    if close_idx != -1:
                                                        # Split: up-to-</think> → thinking, remainder → content
                                                        think_part = content[:close_idx]
                                                        if not _think_open_stripped:
                                                            # Strip the opening <think[...] > from the first chunk.
                                                            # Use a dedicated flag — _first_content_sent stays False
                                                            # throughout the think block, so it must not be reused.
                                                            tag_end = think_part.lower().find(">")
                                                            if tag_end != -1:
                                                                think_part = think_part[tag_end + 1:]
                                                            _think_open_stripped = True
                                                        regular_part = content[close_idx + len("</think>"):]
                                                        _in_think_tag = False
                                                        if think_part:
                                                            yield f'data: {json.dumps({"delta": think_part, "thinking": True})}\n\n'
                                                        if regular_part:
                                                            _first_content_sent = True
                                                            yield f'data: {json.dumps({"delta": regular_part})}\n\n'
                                                    else:
                                                        # Still inside <think>: route to thinking channel
                                                        if not _think_open_stripped:
                                                            # Strip the opening <think[...] > tag (first chunk only)
                                                            tag_end = stripped.lower().find(">")
                                                            if tag_end != -1:
                                                                content = stripped[tag_end + 1:]
                                                            _think_open_stripped = True
                                                        if content:
                                                            yield f'data: {json.dumps({"delta": content, "thinking": True})}\n\n'
                                                else:
                                                    # Some thinking backends start normal content with a
                                                    # stray closing tag. Repair only that shape; do not
                                                    # wrap every first token for model families like
                                                    # MiniMax, which often stream ordinary answers.
                                                    if _thinking_model and not _first_content_sent and stripped.lower().startswith("</think"):
                                                        content = "<think>" + content
                                                    _first_content_sent = True
                                                    yield f'data: {json.dumps({"delta": content})}\n\n'
                                        # Native tool calls — accumulate across chunks
                                        for tc in delta.get("tool_calls") or []:
                                            if tc is None:
                                                continue
                                            func = tc.get("function") or {}
                                            raw_idx = tc.get("index")
                                            if raw_idx is None:
                                                # Gemini's OpenAI-compat layer omits `index` on
                                                # parallel tool calls (every delta arrives as
                                                # index=None) and sends each call complete in one
                                                # delta. Without this, all parallel calls collide
                                                # into slot 0 — later calls overwrite the first's
                                                # name and CORRUPT its arguments by concatenation,
                                                # so only one malformed call survives and the
                                                # follow-up round 400s. A function name marks the
                                                # start of a new call → allocate a fresh slot;
                                                # an arg-only continuation attaches to the last.
                                                if func.get("name") or _tc_last_idx[0] < 0:
                                                    # Next free slot ABOVE any existing key (not
                                                    # len()), so a provider mixing integer indices
                                                    # with index=None can never collide.
                                                    idx = max(_tc_acc, default=-1) + 1
                                                else:
                                                    idx = _tc_last_idx[0]
                                            else:
                                                idx = raw_idx
                                            _tc_last_idx[0] = idx
                                            if idx not in _tc_acc:
                                                _tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                                            if tc.get("id"):
                                                _tc_acc[idx]["id"] = tc["id"]
                                            # Gemini 3 returns an opaque thought_signature in
                                            # extra_content on the function-call delta. It MUST be
                                            # echoed back on the assistant tool_call next round or the
                                            # follow-up request 400s ("Function call is missing a
                                            # thought_signature"). Preserve it verbatim; other
                                            # providers never send it, so this is a no-op for them.
                                            if tc.get("extra_content"):
                                                _tc_acc[idx]["extra_content"] = tc["extra_content"]
                                            if func.get("name"):
                                                _tc_acc[idx]["name"] = func["name"]
                                            if "arguments" in func:
                                                # Guard against a null arguments delta: `func` can be
                                                # {"arguments": None} (JSON null), and a raw `+= None`
                                                # raises TypeError that the broad except swallows,
                                                # silently dropping the rest of the chunk. Matches the
                                                # Anthropic accumulator (`partial = ... or ""`) above.
                                                _tc_acc[idx]["arguments"] += func["arguments"] or ""
                                                # Stream tool arg deltas for doc tools
                                                if func["arguments"] and _tc_acc[idx].get("name") in ("create_document", "update_document", "edit_document"):
                                                    yield f'data: {json.dumps({"type": "tool_call_delta", "index": idx, "name": _tc_acc[idx]["name"], "arg_delta": func["arguments"]})}\n\n'
                                elif "text" in j:
                                    if j["text"]:
                                        for event in _format_routed_content(_harmony_router.feed(j["text"])):
                                            yield event
                            else:
                                if data.strip():
                                    for event in _format_routed_content(_harmony_router.feed(data)):
                                        yield event
                    except Exception as e:
                        logger.error(f"Error parsing stream data: {e}")
                        continue

            # End of stream (no explicit [DONE] received)
            for event in _format_routed_content(_harmony_router.flush()):
                yield event
            tc_event = _emit_tool_calls()
            if tc_event:
                yield tc_event
            yield "data: [DONE]\n\n"

    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        _cooled = _mark_host_dead(target_url)
        _tail = f" — host cooled for {DEAD_HOST_COOLDOWN:.0f}s" if _cooled else " — transient, will retry"
        logger.warning(f"Stream connect to {target_url} failed: {e}{_tail}")
        yield f'event: error\ndata: {json.dumps({"error": f"Cannot reach {_host_key(target_url)}", "status": 503})}\n\n'
    except httpx.ReadTimeout:
        yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
    except httpx.NetworkError:
        yield f'event: error\ndata: {json.dumps({"error": "Network error", "status": 502})}\n\n'
    except Exception as e:
        logger.error(f"Stream error: {e}")
        yield f'event: error\ndata: {json.dumps({"error": str(e), "status": 502})}\n\n'


async def stream_llm(
    url: str,
    model: str,
    messages: List[Dict],
    temperature: float = LLMConfig.DEFAULT_TEMPERATURE,
    max_tokens: int = LLMConfig.DEFAULT_MAX_TOKENS,
    headers: Optional[Dict] = None,
    timeout: int = LLMConfig.STREAM_TIMEOUT,
    prompt_type: Optional[str] = None,
    tools: Optional[List[Dict]] = None,
    session_id: Optional[str] = None,
    *,
    owner: Optional[str] = None,
    surface: Optional[str] = None,
    correlation_id: Optional[str] = None,
    task_id: Optional[str] = None,
    doc_id: Optional[str] = None,
):
    """Stream LLM responses and record redacted activity metadata."""
    async for chunk in _stream_llm_with_activity_impl(
        url,
        model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        headers=headers,
        timeout=timeout,
        prompt_type=prompt_type,
        tools=tools,
        session_id=session_id,
        owner=owner,
        surface=surface,
        correlation_id=correlation_id,
        task_id=task_id,
        doc_id=doc_id,
        stream_impl_func=_stream_llm_impl,
        delta_chars_func=_sse_activity_delta_chars,
        usage_func=_sse_activity_usage,
        error_class_func=_sse_activity_error_class,
        record_activity_func=_record_ai_activity_safe,
        detect_provider_func=_detect_provider,
        time_func=time.time,
    ):
        yield chunk

async def stream_llm_with_fallback(candidates, messages, **kwargs):
    """Wrap stream_llm with an ordered fallback chain."""
    async for chunk in _stream_llm_with_fallback_impl(
        candidates,
        messages,
        stream_llm_func=stream_llm,
        logger=logger,
        **kwargs,
    ):
        yield chunk
