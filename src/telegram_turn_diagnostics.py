"""Closed provider-failure and Telegram turn diagnostic projections."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from src.chat_security_state import ProviderScope
from src.secure_provider_runtime import provider_scope_for_base_url


_PROVIDER_ERROR_CLASSES = frozenset(
    {
        "auth",
        "invalid_request",
        "provider_error",
        "provider_unavailable",
        "rate_limit",
        "request_too_large",
        "timeout",
    }
)


def _bounded_int(value: Any, *, maximum: int = 100_000) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, maximum))


def _provider_error_class(status: int) -> str:
    if status in {401, 403}:
        return "auth"
    if status == 400:
        return "invalid_request"
    if status == 413:
        return "request_too_large"
    if status == 429:
        return "rate_limit"
    if status in {408, 504}:
        return "timeout"
    if 500 <= status <= 599:
        return "provider_unavailable"
    return "provider_error"


def parse_terminal_provider_error_sse(chunk: Any) -> dict[str, Any] | None:
    """Project an SSE provider error to fixed fields without retaining its body."""

    raw = str(chunk or "")
    if not raw.startswith("event: error"):
        return None
    status = 0
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except (TypeError, ValueError, json.JSONDecodeError):
            break
        if isinstance(payload, Mapping):
            status = _bounded_int(payload.get("status"), maximum=599)
        break
    error_class = _provider_error_class(status)
    return {
        "schema": "odysseus.provider_failure.v1",
        "type": "provider_error",
        "status": status,
        "error_class": error_class,
        "retryable": error_class
        in {"provider_unavailable", "rate_limit", "timeout"},
    }


def provider_error_sse(failure: Mapping[str, Any]) -> str:
    """Serialize only the closed provider-failure projection as SSE."""

    status = _bounded_int(failure.get("status"), maximum=599)
    error_class = str(failure.get("error_class") or "provider_error")
    if error_class not in _PROVIDER_ERROR_CLASSES:
        error_class = "provider_error"
    projected = {
        "schema": "odysseus.provider_failure.v1",
        "type": "provider_error",
        "status": status,
        "error_class": error_class,
        "retryable": bool(failure.get("retryable")),
    }
    return f"event: error\ndata: {json.dumps(projected, sort_keys=True)}\n\n"


def telegram_provider_error_reply(failure: Mapping[str, Any]) -> str:
    """Render a stable German reply without provider text or identifiers."""

    status = _bounded_int(failure.get("status"), maximum=599)
    status_text = f" mit HTTP {status}" if status else ""
    return (
        f"Der Modellanbieter hat die Anfrage{status_text} abgelehnt. "
        "Es wurde keine Antwort erzeugt."
    )


def filter_telegram_fallback_candidates(
    candidates: Iterable[Any],
    *,
    local_only_required: bool,
) -> list[tuple[Any, Any, Any]]:
    """Keep well-formed candidates and enforce the Telegram local-only boundary."""

    filtered: list[tuple[Any, Any, Any]] = []
    for candidate in candidates or ():
        if not isinstance(candidate, (list, tuple)) or len(candidate) < 3:
            continue
        endpoint_url, model, headers = candidate[:3]
        if not str(endpoint_url or "").strip() or not str(model or "").strip():
            continue
        if (
            local_only_required
            and provider_scope_for_base_url(endpoint_url) is not ProviderScope.LOCAL_ONLY
        ):
            continue
        filtered.append((endpoint_url, model, headers))
    return filtered


def build_telegram_turn_diagnostic(
    *,
    context_evidence: Mapping[str, Any],
    agent_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the only supported content-free per-turn Telegram diagnostic."""

    retained_messages = _bounded_int(
        context_evidence.get("retained_history_message_count")
    )
    provider_failure = (
        agent_metrics.get("provider_failure")
        if isinstance(agent_metrics.get("provider_failure"), Mapping)
        else {}
    )
    error_class = str(provider_failure.get("error_class") or "provider_error")
    if error_class not in _PROVIDER_ERROR_CLASSES:
        error_class = "provider_error"
    provider_status = _bounded_int(provider_failure.get("status"), maximum=599)
    if not provider_status:
        error_class = "provider_error"
    return {
        "schema": "odysseus.telegram_turn_diagnostic.v1",
        "binding_reused": retained_messages > 0,
        "retained_history_message_count": retained_messages,
        "retained_history_character_count": _bounded_int(
            context_evidence.get("retained_history_character_count")
        ),
        "omitted_history_message_count": _bounded_int(
            context_evidence.get("omitted_history_message_count")
        ),
        "continuation": bool(agent_metrics.get("continuation")),
        "inherited_domain_count": _bounded_int(
            agent_metrics.get("inherited_domain_count"), maximum=64
        ),
        "selected_tool_count": _bounded_int(
            agent_metrics.get("selected_tool_count"), maximum=512
        ),
        "provider_status": provider_status,
        "provider_error_class": error_class,
        "fallback_attempted": bool(agent_metrics.get("fallback_attempted")),
        "fallback_succeeded": bool(agent_metrics.get("fallback_succeeded")),
    }


__all__ = [
    "build_telegram_turn_diagnostic",
    "filter_telegram_fallback_candidates",
    "parse_terminal_provider_error_sse",
    "provider_error_sse",
    "telegram_provider_error_reply",
]
