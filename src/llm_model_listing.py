"""Model listing and model-id normalization helpers."""

from __future__ import annotations

import os
from typing import Callable, Optional


def list_model_ids(
    base_chat_url: str,
    *,
    timeout: int,
    headers: Optional[dict],
    owner: Optional[str],
    endpoint_id: Optional[str],
    configured_cached_model_ids_func: Callable[..., list[str]],
    detect_provider_func: Callable[[str], str],
    anthropic_models: list[str],
    ollama_api_root_func: Callable[[str], str],
    build_models_url_func: Callable[[str], str],
    httpx_get_func: Callable[..., object],
    http_get_func: Callable[..., object],
    logger,
) -> list[str]:
    """List available model IDs from an endpoint."""
    cached = configured_cached_model_ids_func(base_chat_url, owner=owner, endpoint_id=endpoint_id)
    if cached:
        return cached
    provider = detect_provider_func(base_chat_url)
    if provider == "anthropic":
        return list(anthropic_models)
    try:
        request_headers = {}
        if headers:
            request_headers.update(headers)
        if provider == "ollama":
            models_url = ollama_api_root_func(base_chat_url) + "/tags"
        else:
            models_url = build_models_url_func(base_chat_url)
        response = httpx_get_func(models_url, request_headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if not model_ids:
            model_ids = [
                m.get("name") or m.get("model")
                for m in (data.get("models") or [])
                if m.get("name") or m.get("model")
            ]
        return model_ids
    except Exception:
        try:
            if ":11434" in base_chat_url or "ollama" in base_chat_url.lower():
                root = base_chat_url.replace("/v1/chat/completions", "").replace("/chat/completions", "").rstrip("/")
                response = http_get_func(root + "/api/tags", timeout=timeout)
                response.raise_for_status()
                return [
                    m.get("name") or m.get("model")
                    for m in (response.json().get("models") or [])
                    if m.get("name") or m.get("model")
                ]
        except Exception as exc:
            logger.warning("Failed to fetch model list from configured endpoint", exc_info=exc)
        return []


def normalize_model_id(
    endpoint_url: str,
    requested: str,
    *,
    timeout: int,
    owner: Optional[str],
    endpoint_id: Optional[str],
    list_model_ids_func: Callable[..., list[str]],
) -> Optional[str]:
    """Normalize a model ID to match available models."""
    available = list_model_ids_func(endpoint_url, timeout=timeout, owner=owner, endpoint_id=endpoint_id)
    if not available:
        return None
    if requested in available:
        return requested
    requested_base = os.path.basename(requested.rstrip("/"))
    for model_id in available:
        if os.path.basename(model_id.rstrip("/")) == requested_base:
            return model_id
    return None
