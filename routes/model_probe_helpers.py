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
