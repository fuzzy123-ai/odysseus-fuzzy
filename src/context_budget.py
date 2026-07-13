"""Adaptive, model-aware input-token budgets for the agent loop.

``agent_input_token_budget`` remains the legacy global prompt cap.  The default
value is an auto sentinel; model/provider overrides can select a more appropriate
cap without changing that global setting.  Output reserve is applied exactly once
against the real model context window and is never subtracted from an already
computed prompt cap by the context trimmer.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any, Mapping
from urllib.parse import urlparse


# Conservative ceiling for the auto-derived path. Long-context windows remain
# available when an operator raises the setting or configures an override.
DEFAULT_HARD_MAX = 32_000
DEFAULT_BUDGET = 6000
DEFAULT_HEADROOM = 0.85


@dataclass(frozen=True)
class InputBudgetDecision:
    """Resolved prompt budget plus non-sensitive routing diagnostics."""

    input_budget: int
    provider: str
    source: str
    normalized_model: str
    context_length: int
    output_reserve: int


def normalize_budget_model(model: Any) -> str:
    """Return the stable, exact key used for model override matching."""
    return str(model or "").strip().lower().replace("\\", "/")


def detect_budget_provider(endpoint_url: Any) -> str:
    """Classify an endpoint for provider budget overrides.

    Detection is deliberately URL-only: credentials and request payloads never
    enter budget selection or its logs.  Local OpenAI-compatible endpoints are
    distinct from Ollama; native Ollama, its OpenAI-compatible port, and Ollama
    Cloud all normalize to ``ollama``. Unknown remote endpoints stay ``unknown``
    so they receive the conservative global/auto policy unless explicitly keyed.
    """
    try:
        parsed = urlparse(str(endpoint_url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        path = (parsed.path or "").lower().rstrip("/")
        port = parsed.port
    except (TypeError, ValueError):
        return "unknown"

    if not host:
        return "unknown"

    def _domain(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    if _domain("deepseek.com"):
        return "deepseek"
    if _domain("ollama.com") or host == "ollama" or port == 11434:
        return "ollama"

    is_local = host in {
        "localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", "ollama",
    }
    if not is_local:
        try:
            address = ipaddress.ip_address(host)
            is_local = address.is_private or address.is_loopback
        except ValueError:
            is_local = host.endswith(".local")

    if is_local and (path == "/api" or path.startswith("/api/")):
        return "ollama"
    if is_local:
        return "local"

    domains = (
        ("openai.com", "openai"),
        ("anthropic.com", "anthropic"),
        ("openrouter.ai", "openrouter"),
        ("groq.com", "groq"),
        ("mistral.ai", "mistral"),
        ("nvidia.com", "nvidia"),
        ("moonshot.ai", "moonshot"),
        ("moonshot.cn", "moonshot"),
        ("googleapis.com", "google"),
        ("x.ai", "xai"),
    )
    for domain, provider in domains:
        if _domain(domain):
            return provider
    return "unknown"


def _positive_int(value: Any) -> int | None:
    """Accept positive JSON integers, rejecting bools and string coercions."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    return None


def _normalized_cap_map(value: Any, *, model_keys: bool) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for raw_key, raw_cap in value.items():
        key = (
            normalize_budget_model(raw_key)
            if model_keys
            else str(raw_key or "").strip().lower()
        )
        cap = _positive_int(raw_cap)
        if key and cap is not None:
            result[key] = cap
    return result


def _override_maps(overrides: Any) -> tuple[dict[str, int], dict[str, int]]:
    """Return sanitized ``(providers, models)`` maps from untrusted settings."""
    if not isinstance(overrides, Mapping):
        return {}, {}
    providers = _normalized_cap_map(overrides.get("providers"), model_keys=False)
    models = _normalized_cap_map(overrides.get("models"), model_keys=True)
    return providers, models


def resolve_input_token_budget(
    configured: int,
    context_length: int,
    explicit: bool,
    *,
    endpoint_url: str = "",
    model: str = "",
    overrides: Any = None,
    output_reserve: int = 0,
    default: int = DEFAULT_BUDGET,
    headroom: float = DEFAULT_HEADROOM,
    hard_max: int = DEFAULT_HARD_MAX,
) -> InputBudgetDecision:
    """Resolve a model-aware prompt cap and report which policy selected it.

    Precedence is exact normalized model, provider, legacy explicit global cap,
    then the auto-derived hard maximum. Invalid override shapes and non-positive
    values are ignored. The selected cap is finally clamped to the usable model
    window (``context_length - output_reserve``), with a floor of one token so a
    malformed/tiny window can never create a negative context budget.
    """
    configured_value = _positive_int(configured) or 0
    context_value = max(0, _positive_int(context_length) or 0)
    reserve_value = max(0, _positive_int(output_reserve) or 0)
    hard_max_value = _positive_int(hard_max) or DEFAULT_HARD_MAX
    default_value = _positive_int(default) or DEFAULT_BUDGET
    try:
        headroom_value = min(max(float(headroom), 0.01), 1.0)
    except (TypeError, ValueError):
        headroom_value = DEFAULT_HEADROOM

    provider = detect_budget_provider(endpoint_url)
    normalized_model = normalize_budget_model(model)
    provider_caps, model_caps = _override_maps(overrides)

    if normalized_model and normalized_model in model_caps:
        selected = model_caps[normalized_model]
        source = "model_override"
    elif provider in provider_caps:
        selected = provider_caps[provider]
        source = "provider_override"
    elif explicit and configured_value > 0:
        selected = configured_value
        source = "legacy_explicit"
    elif context_value > 0:
        selected = max(1, min(int(context_value * headroom_value), hard_max_value))
        source = "auto_hard_max"
    else:
        selected = configured_value or default_value
        source = "auto_unknown_window"

    if context_value > 0:
        usable_window = max(1, context_value - reserve_value)
        selected = min(selected, usable_window)

    return InputBudgetDecision(
        input_budget=max(1, int(selected)),
        provider=provider,
        source=source,
        normalized_model=normalized_model,
        context_length=context_value,
        output_reserve=reserve_value,
    )


def compute_input_token_budget(
    configured: int,
    context_length: int,
    explicit: bool,
    *,
    default: int = DEFAULT_BUDGET,
    headroom: float = DEFAULT_HEADROOM,
    hard_max: int = DEFAULT_HARD_MAX,
    endpoint_url: str = "",
    model: str = "",
    overrides: Any = None,
    output_reserve: int = 0,
) -> int:
    """Return the effective soft input-token budget.

    The original three positional arguments and keyword defaults remain
    compatible. New callers may pass endpoint/model/overrides/output_reserve for
    dynamic policy selection; callers needing diagnostics should use
    :func:`resolve_input_token_budget`.
    """
    return resolve_input_token_budget(
        configured,
        context_length,
        explicit,
        endpoint_url=endpoint_url,
        model=model,
        overrides=overrides,
        output_reserve=output_reserve,
        default=default,
        headroom=headroom,
        hard_max=hard_max,
    ).input_budget


def budget_is_explicit(configured: int, *, default: int = DEFAULT_BUDGET) -> bool:
    """Whether ``agent_input_token_budget`` is a deliberate legacy cap.

    The default value is the auto sentinel. Settings persistence materializes
    defaults, so this remains value-based rather than presence-based.
    """
    value = _positive_int(configured) or 0
    return value > 0 and value != default
