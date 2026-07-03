"""Optional read-only Prometheus and Loki clients for diagnostics.

The module is deliberately configuration-gated. Without an explicit endpoint
and transport it returns readiness blockers instead of touching the network.
Responses are summarized and redacted; Loki log lines are never returned.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OBSERVABILITY_CLIENTS_SCHEMA = "odysseus.observability_clients.v1"

SAFE_LABELS = {"surface", "component", "event_type", "status", "severity", "job", "instance"}
SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9_:{}=~!\"',./\[\]() +*|\\-]+$")
HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)
FORBIDDEN_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "telegram_token",
    "chat_id",
    "private_document_text",
    "private_email_body",
    "image_base64",
    "unredacted_tool_output",
    "raw_prompt",
    "raw_output",
    "document_text",
    "email_body",
    "message_text",
)


class ObservabilityClientError(ValueError):
    """Raised when a query or response would violate the diagnostics boundary."""


Transport = Callable[[str, str, Mapping[str, Any], float], Mapping[str, Any]]


@dataclass(frozen=True)
class ObservabilityClientConfig:
    prometheus_url: str = ""
    loki_url: str = ""
    timeout_seconds: float = 2.0
    enabled: bool = False
    transport: Transport | None = None


def readiness(config: ObservabilityClientConfig | None = None) -> dict[str, Any]:
    config = config or ObservabilityClientConfig()
    return {
        "schema": OBSERVABILITY_CLIENTS_SCHEMA,
        "status": "ready" if config.enabled and (config.prometheus_url or config.loki_url) else "blocked",
        "reason": "configured" if config.enabled and (config.prometheus_url or config.loki_url) else "observability_endpoints_not_configured",
        "prometheus_configured": bool(config.enabled and config.prometheus_url),
        "loki_configured": bool(config.enabled and config.loki_url),
        "read_only": True,
        "redacted_output": True,
        "raw_content_visible": False,
        "writes_performed": False,
    }


def query_prometheus_readonly(
    query: Any,
    *,
    config: ObservabilityClientConfig | None = None,
    limit: Any = 20,
) -> dict[str, Any]:
    config = config or ObservabilityClientConfig()
    safe_query = _safe_query(query)
    capped_limit = _safe_limit(limit)
    if not config.enabled or not config.prometheus_url:
        return _blocked("prometheus_query_readonly", "prometheus_not_configured", safe_query, capped_limit)
    payload = _transport(config)("GET", _join_url(config.prometheus_url, "/api/v1/query"), {"query": safe_query}, config.timeout_seconds)
    result = _safe_prometheus_payload(payload, limit=capped_limit)
    return _success("prometheus_query_readonly", safe_query, result)


def query_loki_readonly(
    query: Any,
    *,
    config: ObservabilityClientConfig | None = None,
    limit: Any = 20,
) -> dict[str, Any]:
    config = config or ObservabilityClientConfig()
    safe_query = _safe_query(query)
    capped_limit = _safe_limit(limit)
    if not config.enabled or not config.loki_url:
        return _blocked("loki_query_readonly", "loki_not_configured", safe_query, capped_limit)
    payload = _transport(config)(
        "GET",
        _join_url(config.loki_url, "/loki/api/v1/query"),
        {"query": safe_query, "limit": capped_limit},
        config.timeout_seconds,
    )
    result = _safe_loki_payload(payload, limit=capped_limit)
    return _success("loki_query_readonly", safe_query, result)


def _blocked(tool: str, reason: str, query: str, limit: int) -> dict[str, Any]:
    return {
        "schema": OBSERVABILITY_CLIENTS_SCHEMA,
        "tool": tool,
        "status": "blocked",
        "reason": reason,
        "query_ref": _hashable_query_ref(query),
        "limit": limit,
        "records": (),
        "read_only": True,
        "redacted_output": True,
        "raw_content_visible": False,
        "writes_performed": False,
        "next_action": "configure_observability_endpoint_server_side",
    }


def _success(tool: str, query: str, result: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "schema": OBSERVABILITY_CLIENTS_SCHEMA,
        "tool": tool,
        "status": "success",
        "reason": "query_summarized",
        "query_ref": _hashable_query_ref(query),
        "read_only": True,
        "redacted_output": True,
        "raw_content_visible": False,
        "writes_performed": False,
        "result": dict(result),
    }
    _reject_forbidden_payload(payload)
    return payload


def _safe_prometheus_payload(payload: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ObservabilityClientError("prometheus response must be a mapping")
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    result = data.get("result") if isinstance(data.get("result"), list) else []
    items = []
    for item in result[:limit]:
        if not isinstance(item, Mapping):
            continue
        metric = item.get("metric") if isinstance(item.get("metric"), Mapping) else {}
        value = item.get("value") if isinstance(item.get("value"), list) else item.get("values")
        items.append(
            {
                "labels": _safe_labels(metric),
                "sample_count": _sample_count(value),
                "last_value": _last_numeric_value(value),
            }
        )
    return {
        "source": "prometheus",
        "api_status": _safe_token(payload.get("status") or "unknown"),
        "result_type": _safe_token(data.get("resultType") or "unknown"),
        "result_count": len(items),
        "results": tuple(items),
    }


def _safe_loki_payload(payload: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ObservabilityClientError("loki response must be a mapping")
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    result = data.get("result") if isinstance(data.get("result"), list) else []
    streams = []
    for item in result[:limit]:
        if not isinstance(item, Mapping):
            continue
        stream = item.get("stream") if isinstance(item.get("stream"), Mapping) else {}
        values = item.get("values") if isinstance(item.get("values"), list) else []
        timestamps = [str(value[0]) for value in values if isinstance(value, list) and value]
        streams.append(
            {
                "labels": _safe_labels(stream),
                "line_count": len(values),
                "first_ts": timestamps[0] if timestamps else "",
                "last_ts": timestamps[-1] if timestamps else "",
                "log_lines_included": False,
            }
        )
    return {
        "source": "loki",
        "api_status": _safe_token(payload.get("status") or "unknown"),
        "result_type": _safe_token(data.get("resultType") or "unknown"),
        "stream_count": len(streams),
        "streams": tuple(streams),
    }


def _safe_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in labels.items():
        clean_key = "".join(ch for ch in str(key or "").strip() if ch.isalnum() or ch in "_.:@/-")[:120]
        if clean_key not in SAFE_LABELS:
            continue
        clean_value = _safe_token(value)
        if clean_value:
            result[clean_key] = clean_value
    return result


def _sample_count(value: Any) -> int:
    if isinstance(value, list) and value and all(isinstance(item, list) for item in value):
        return min(len(value), 1_000_000)
    if isinstance(value, list):
        return 1
    return 0


def _last_numeric_value(value: Any) -> float | None:
    raw = None
    if isinstance(value, list) and len(value) >= 2 and not isinstance(value[0], list):
        raw = value[1]
    elif isinstance(value, list) and value and isinstance(value[-1], list) and len(value[-1]) >= 2:
        raw = value[-1][1]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _safe_query(value: Any) -> str:
    query = " ".join(str(value or "").split())
    if not query:
        raise ObservabilityClientError("query is required")
    if len(query) > 500:
        raise ObservabilityClientError("query is too long")
    _reject_forbidden_text(query)
    if not SAFE_QUERY_RE.fullmatch(query):
        raise ObservabilityClientError("query contains unsupported characters")
    return query


def _safe_token(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 120:
        text = text[:120]
    _reject_forbidden_text(text)
    return "".join(ch for ch in text if ch.isalnum() or ch in "_.:@/-")[:120]


def _safe_limit(value: Any) -> int:
    try:
        return max(1, min(int(value or 20), 100))
    except (TypeError, ValueError):
        return 20


def _transport(config: ObservabilityClientConfig) -> Transport:
    return config.transport or _urllib_transport


def _urllib_transport(method: str, url: str, params: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
    encoded = urlencode({key: str(value) for key, value in params.items()})
    target = f"{url}?{encoded}" if encoded else url
    request = Request(target, method=method.upper(), headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - config-gated read-only diagnostics client.
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ObservabilityClientError("endpoint must be http or https")
    _reject_forbidden_text(base)
    return base + path


def _hashable_query_ref(query: str) -> str:
    from src.runtime_event_envelope import stable_payload_hash

    return stable_payload_hash({"query": query})


def _reject_forbidden_payload(payload: Any) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    _reject_forbidden_text(encoded)


def _reject_forbidden_text(text: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in FORBIDDEN_MARKERS):
        raise ObservabilityClientError("value contains a forbidden marker")
    if HOST_PATH_RE.search(text):
        raise ObservabilityClientError("value contains a private host path")
