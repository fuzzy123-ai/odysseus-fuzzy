"""Reachability ping orchestration for model endpoints."""

from __future__ import annotations

from typing import Any, Callable, Optional


def ping_endpoint(
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 1.5,
    *,
    normalize_base_func: Callable[[str], str],
    resolve_url_func: Callable[[str], str],
    safe_build_headers_func: Callable[[Optional[str], str], dict[str, Any]],
    ollama_native_probe_root_func: Callable[[str], Optional[str]],
    ollama_native_ping_urls_func: Callable[[Optional[str]], list[str]],
    probe_ollama_native_ping_func: Callable[..., tuple[dict[str, Any] | None, Optional[str]]],
    probe_base_ping_with_models_fallback_func: Callable[..., tuple[dict[str, Any] | None, Optional[str]]],
    http_get_func: Callable[..., Any],
    llm_verify_func: Callable[[], Any],
    ping_result_func: Callable[[Any], dict[str, Any]],
    should_try_models_url_func: Callable[[Any], bool],
    safe_build_models_url_func: Callable[[str], str],
) -> dict[str, Any]:
    """Reachability probe that does not require installed/listed models."""
    base = resolve_url_func(normalize_base_func(base_url))
    headers = safe_build_headers_func(api_key, base)

    ollama_root = ollama_native_probe_root_func(base)
    last_error: Optional[str] = None

    try:
        native_result, last_error = probe_ollama_native_ping_func(
            ollama_native_ping_urls_func(ollama_root),
            timeout=timeout,
            http_get_func=http_get_func,
            llm_verify_func=llm_verify_func,
            ping_result_func=ping_result_func,
        )
        if native_result:
            return native_result
    except Exception:
        pass

    result, base_error = probe_base_ping_with_models_fallback_func(
        base,
        headers,
        timeout=timeout,
        http_get_func=http_get_func,
        llm_verify_func=llm_verify_func,
        ping_result_func=ping_result_func,
        should_try_models_url_func=should_try_models_url_func,
        safe_build_models_url_func=safe_build_models_url_func,
    )
    if result:
        return result

    last_error = base_error or last_error
    return {"reachable": False, "status_code": None, "error": last_error}
