"""Probe support helpers for model endpoint routes."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional
from urllib.parse import urlparse


def safe_detect_provider(base_url: str, *, detect_provider_func: Callable[[str], str], logger: logging.Logger) -> str:
    """Best-effort provider detection that must not break endpoint probing."""
    try:
        return detect_provider_func(base_url)
    except Exception as exc:
        logger.debug("Provider detection failed for %s: %s", base_url, exc)
        return ""


def safe_build_models_url(
    base_url: str,
    *,
    build_models_url_func: Callable[[str], str],
    logger: logging.Logger,
) -> str:
    """Build a /models URL without letting optional provider imports break probes."""
    try:
        return build_models_url_func(base_url)
    except ValueError:
        raise
    except Exception as exc:
        logger.debug("Model URL detection failed for %s: %s", base_url, exc)
        return f"{(base_url or '').rstrip('/')}/models"


def safe_build_headers(
    api_key: Optional[str],
    base_url: str,
    *,
    build_headers_func: Callable[[Optional[str], str], dict],
    logger: logging.Logger,
) -> dict:
    """Build auth headers without letting optional provider imports break probes."""
    try:
        return build_headers_func(api_key, base_url)
    except Exception as exc:
        logger.debug("Header detection failed for %s: %s", base_url, exc)
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def is_discovery_only_provider(provider: str) -> bool:
    return provider == "chatgpt-subscription"


def model_endpoint_error_message(
    base_url: str,
    ping: dict[str, Any] | None = None,
    *,
    build_models_url_func: Callable[[str], str],
) -> str:
    """Return a provider-aware error message for failed endpoint probes."""
    ping = ping or {}
    error = ping.get("error")
    try:
        probed = build_models_url_func(base_url) or base_url
    except Exception:
        probed = base_url
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    is_ollama = parsed.port == 11434 or "ollama" in host or "ollama" in base_url.lower()
    is_lmstudio = (
        parsed.port == 1234
        or "lmstudio" in host
        or "lm-studio" in host
        or "lm_studio" in host
    )

    if is_lmstudio:
        parts = [
            "LM Studio is reachable, but no models were reported.",
            f"Probed {probed}.",
        ]
        if error:
            parts.append(f"Last probe error: {error}.")
        parts.append(
            "Open LM Studio, load at least one model, and confirm the "
            "Developer Server is running on port 1234."
        )
        parts.append(
            "Base URL should be http://localhost:1234/v1 (native) or "
            "http://host.docker.internal:1234/v1 (Docker)."
        )
        return " ".join(parts)

    if is_ollama:
        parts = ["No Ollama models found for that endpoint."]
        parts.append(f"Probed {probed}.")
        if error:
            parts.append(f"Last probe error: {error}.")
        parts.append("Check that Ollama is running and that the base URL is correct.")
        parts.append("For native/local installs, use http://localhost:11434/v1.")
        parts.append("For Docker, use http://host.docker.internal:11434/v1 when Ollama runs on the host.")
        parts.append("Run `ollama list` to confirm at least one model is installed.")
        return " ".join(parts)

    if error:
        return f"No models found for that provider/key. Probed {probed}. Last probe error: {error}."

    return f"No models found for that provider/key. Probed {probed}."


def probe_single_model(
    base: str,
    api_key: str,
    model_id: str,
    timeout: int = 10,
    with_tools: bool = False,
    *,
    safe_detect_provider_func: Callable[[str], str],
    safe_build_headers_func: Callable[[Optional[str], str], dict],
    build_chat_url_func: Callable[[str], str],
    llm_verify_func: Callable[[], Any],
    http_post_func: Callable[..., Any],
    monotonic_time_func: Callable[[], float],
    timeout_exception_cls: type[BaseException],
) -> dict[str, Any]:
    """Send a minimal completion request to verify a single model."""
    provider = safe_detect_provider_func(base)
    if is_discovery_only_provider(provider):
        return {"status": "ok", "latency_ms": 0, "skipped": True}

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say OK"},
    ]
    test_tools = [
        {
            "type": "function",
            "function": {
                "name": "test",
                "description": "Test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ] if with_tools else None

    if provider == "anthropic":
        from src.llm_core import (
            _build_anthropic_headers,
            _build_anthropic_payload,
            _normalize_anthropic_url,
        )

        target_url = _normalize_anthropic_url(base)
        auth_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        headers = _build_anthropic_headers(auth_headers)
        payload = _build_anthropic_payload(model_id, messages, 0.0, 5)
        if test_tools:
            payload["tools"] = [
                {
                    "name": "test",
                    "description": "Test tool",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
    elif provider == "ollama":
        from src.llm_core import _build_ollama_payload

        target_url = build_chat_url_func(base)
        headers = safe_build_headers_func(api_key, base)
        headers["Content-Type"] = "application/json"
        payload = _build_ollama_payload(
            model_id,
            messages,
            0.0,
            5,
            stream=False,
            tools=test_tools,
        )
    else:
        from src.llm_core import _restricts_temperature, _uses_max_completion_tokens

        target_url = build_chat_url_func(base)
        headers = safe_build_headers_func(api_key, base)
        headers["Content-Type"] = "application/json"
        max_key = "max_completion_tokens" if _uses_max_completion_tokens(model_id) else "max_tokens"
        payload = {"model": model_id, "messages": messages, max_key: 5}
        if not _restricts_temperature(model_id):
            payload["temperature"] = 0.0
        if test_tools:
            payload["tools"] = test_tools

    try:
        t0 = monotonic_time_func()
        response = http_post_func(
            target_url,
            headers=headers,
            json=payload,
            timeout=timeout,
            verify=llm_verify_func(),
        )
        latency = round((monotonic_time_func() - t0) * 1000)
        if response.is_success:
            return {"status": "ok", "latency_ms": latency}

        error_msg = f"HTTP {response.status_code}"
        try:
            body = response.json()
            if "error" in body:
                err = body["error"]
                if isinstance(err, dict):
                    error_msg = err.get("message", error_msg)[:120]
                elif isinstance(err, str):
                    error_msg = err[:120]
        except Exception:
            pass
        return {"status": "fail", "latency_ms": latency, "error": error_msg}
    except timeout_exception_cls:
        return {"status": "timeout", "latency_ms": timeout * 1000, "error": f"Timed out ({timeout}s)"}
    except Exception as exc:
        return {"status": "fail", "error": str(exc)[:80]}
