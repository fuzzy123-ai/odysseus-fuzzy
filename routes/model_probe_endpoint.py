"""Model-list endpoint probing orchestration."""

from __future__ import annotations

from typing import Any, Callable, Optional
from urllib.parse import urlparse


def probe_endpoint(
    base_url: str,
    api_key: Optional[str] = None,
    timeout: int = 5,
    *,
    normalize_base_func: Callable[[str], str],
    resolve_url_func: Callable[[str], str],
    safe_detect_provider_func: Callable[[str], str],
    chatgpt_fetch_available_models_func: Callable[..., list[str]],
    anthropic_models: list[str],
    safe_build_models_url_func: Callable[[str], str],
    safe_build_headers_func: Callable[[Optional[str], str], dict],
    http_get_func: Callable[..., Any],
    httpx_get_kimi_aware_func: Callable[..., Any],
    http_status_error_cls: type[BaseException],
    llm_verify_func: Callable[[], Any],
    logger,
    redact_url_func: Callable[[str], str],
    anthropic_model_ids_from_payload_func: Callable[[Any], list[str]],
    model_ids_from_listing_payload_func: Callable[[Any], list[str]],
    append_curated_probe_models_func: Callable[..., list[str]],
    host_match_func: Callable[[str, str], bool],
    match_provider_curated_func: Callable[[str, Optional[str]], Optional[str]],
    provider_curated: dict[str, list[str]],
    is_chat_model_func: Callable[[str], bool],
    ollama_tag_model_ids_from_payload_func: Callable[[Any], list[str]],
    curated_probe_fallback_models_func: Callable[..., tuple[Optional[str], list[str]]],
) -> list[str]:
    """Probe a base URL's model listing and return chat-capable model IDs."""
    base = resolve_url_func(normalize_base_func(base_url))
    provider = safe_detect_provider_func(base)

    if provider == "chatgpt-subscription":
        if api_key:
            return chatgpt_fetch_available_models_func(api_key, timeout=timeout)
        return []

    if provider == "anthropic":
        url = safe_build_models_url_func(base)
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        try:
            response = http_get_func(url, headers=headers, timeout=timeout, verify=llm_verify_func())
            response.raise_for_status()
            models = anthropic_model_ids_from_payload_func(response.json())
            if models:
                return models
        except http_status_error_cls as exc:
            if api_key:
                status = exc.response.status_code if exc.response is not None else "unknown"
                logger.warning(f"Anthropic /v1/models failed with API key: HTTP {status}")
                return []
            logger.warning(f"Anthropic /v1/models failed, using hardcoded list: {exc}")
        except Exception as exc:
            if api_key:
                logger.warning(f"Anthropic /v1/models failed with API key: {exc}")
                return []
            logger.warning(f"Anthropic /v1/models failed, using hardcoded list: {exc}")
        return list(anthropic_models)

    url = safe_build_models_url_func(base)
    headers = safe_build_headers_func(api_key, base)
    try:
        response = httpx_get_kimi_aware_func(url, headers, timeout=timeout, verify=llm_verify_func())
        response.raise_for_status()
        models = model_ids_from_listing_payload_func(response.json())
        if models:
            models = append_curated_probe_models_func(
                base,
                models,
                host_match_func=host_match_func,
                match_provider_curated_func=match_provider_curated_func,
                provider_curated=provider_curated,
            )
            return [model for model in models if is_chat_model_func(model)]
    except http_status_error_cls as exc:
        if api_key:
            status = exc.response.status_code if exc.response is not None else "unknown"
            logger.warning("Failed to probe %s with API key: HTTP %s", redact_url_func(url), status)
            return []
        logger.warning("Failed to probe %s: %s", redact_url_func(url), exc)
    except Exception as exc:
        if api_key:
            logger.warning("Failed to probe %s with API key: %s", redact_url_func(url), exc)
            return []
        logger.warning("Failed to probe %s: %s", redact_url_func(url), exc)

    try:
        parsed = urlparse(base)
        if parsed.port == 11434 or "ollama" in (parsed.hostname or "").lower():
            root = base[:-3].rstrip("/") if base.endswith("/v1") else base
            response = http_get_func(root + "/api/tags", timeout=timeout, verify=llm_verify_func())
            response.raise_for_status()
            models = ollama_tag_model_ids_from_payload_func(response.json())
            if models:
                return [model for model in models if is_chat_model_func(model)]
    except Exception as exc:
        logger.debug(f"Ollama /api/tags probe failed for {base}: {exc}")

    curated_key, fallback = curated_probe_fallback_models_func(
        base,
        match_provider_curated_func=match_provider_curated_func,
        provider_curated=provider_curated,
    )
    if fallback:
        logger.info(f"Using curated fallback for {curated_key}: {fallback}")
        return fallback
    return []
