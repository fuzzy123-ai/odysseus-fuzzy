"""Request-shape policy helpers for LLM calls."""

from __future__ import annotations

import re


_MAX_COMPLETION_TOKENS_MODELS = {"o1", "o3", "o4", "gpt-4.5", "gpt-5"}


def _uses_max_completion_tokens(model: str) -> bool:
    """Check if a model requires max_completion_tokens instead of max_tokens."""
    if not model:
        return False
    m = model.lower()
    return any(m.startswith(p) or f"/{p}" in m for p in _MAX_COMPLETION_TOKENS_MODELS)


_FIXED_TEMPERATURE_MODELS = ("o1", "o3", "o4", "gpt-5", "kimi-for-coding")


def _restricts_temperature(model: str) -> bool:
    """Check if a model rejects any non-default temperature."""
    if not model:
        return False
    m = model.lower()
    return any(m.startswith(p) or f"/{p}" in m for p in _FIXED_TEMPERATURE_MODELS)


def _moonshot_rejects_custom_temperature(provider: str, model: str) -> bool:
    """Check if the official Moonshot API fixes temperature for this model."""
    if provider != "moonshot" or not isinstance(model, str):
        return False
    model_id = model.lower().rsplit("/", 1)[-1]
    return bool(re.match(r"^kimi-k2\.(?:5|6)(?:$|[-_:])", model_id))


def _omit_temperature(provider: str, model: str) -> bool:
    """Check if a request should use the provider's default temperature."""
    return _restricts_temperature(model) or _moonshot_rejects_custom_temperature(
        provider, model
    )


def _normalize_anthropic_url(url: str) -> str:
    """Ensure Anthropic URL points to /v1/messages."""
    url = url.rstrip("/")
    if url.endswith("/v1/messages"):
        return url
    if url.endswith("/v1"):
        return url + "/messages"
    return url + "/v1/messages"
