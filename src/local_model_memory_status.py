"""Frontend-safe local model and memory-maintenance status adapter."""

from __future__ import annotations

from typing import Any, Mapping
import re

from src.local_model_scheduler import local_model_gate_snapshot, read_local_model_foreground_marker


LOCAL_MODEL_MEMORY_STATUS_SCHEMA = "odysseus.local_model_memory_status.v1"
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_PRIVATE_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")


def build_local_model_memory_status(
    *,
    required_model: Any = "gemma3:4b",
    gate_snapshot: Mapping[str, Any] | None = None,
    foreground_marker: Mapping[str, Any] | None = None,
    maintenance_plan: Any = None,
    benchmark_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    gate = dict(gate_snapshot if isinstance(gate_snapshot, Mapping) else local_model_gate_snapshot())
    marker = (
        dict(foreground_marker)
        if isinstance(foreground_marker, Mapping)
        else read_local_model_foreground_marker()
    )
    maintenance = _maintenance_payload(maintenance_plan)
    benchmark = _benchmark_payload(benchmark_summary)
    active_foreground = _safe_int(gate.get("active_foreground")) + (1 if marker else 0)
    waiting_foreground = _safe_int(gate.get("waiting_foreground"))
    maintenance_status = str(maintenance.get("preflight_status") or "unknown")
    status = _overall_status(
        maintenance_status=maintenance_status,
        active_foreground=active_foreground,
        waiting_foreground=waiting_foreground,
    )
    payload = {
        "schema": LOCAL_MODEL_MEMORY_STATUS_SCHEMA,
        "state": "live",
        "status": status,
        "summary": _summary(status=status, active_foreground=active_foreground, waiting_foreground=waiting_foreground),
        "source_ref": "local_model:memory_maintenance",
        "required_model": _safe_label(required_model, fallback="gemma3:4b"),
        "warm_model_status": "foreground_active" if active_foreground else "unknown",
        "queue": {
            "active": _safe_int(gate.get("active")),
            "active_foreground": active_foreground,
            "waiting_foreground": waiting_foreground,
            "max_concurrency": max(1, _safe_int(gate.get("max_concurrency"))),
        },
        "foreground": {
            "active": bool(active_foreground),
            "model": _safe_label(marker.get("model") if marker else "", fallback=""),
            "reason": _safe_label(marker.get("reason") if marker else "", fallback=""),
        },
        "maintenance_guard": {
            "preflight_status": _safe_label(maintenance_status, fallback="unknown"),
            "priority_class": _safe_label(_nested(maintenance, "priority", "priority_class"), fallback=""),
            "required_model": _safe_label(maintenance.get("required_model"), fallback=""),
            "wait_timeout_seconds": _safe_int(maintenance.get("wait_timeout_seconds")),
            "command_timeout_seconds": _safe_int(maintenance.get("command_timeout_seconds")),
            "failure_count": len(tuple(maintenance.get("preflight_failures") or ())),
            "warning_count": len(tuple(maintenance.get("preflight_warnings") or ())),
            "executes": False,
        },
        "benchmark_summary": benchmark,
        "known_cpu_constraint": _known_cpu_constraint(benchmark),
        "pending_count": waiting_foreground,
        "blocked_count": 1 if maintenance_status == "blocked" else 0,
        "item_count": 1,
        "raw_content_visible": False,
        "private_content_visible": False,
        "path_values_visible": False,
        "token_value_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _maintenance_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        raw = value.to_dict()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _benchmark_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "model": _safe_label(value.get("model"), fallback=""),
        "latency_seconds": _safe_float(value.get("latency_seconds")),
        "tokens": _safe_int(value.get("tokens")),
        "tokens_per_second": _safe_float(value.get("tokens_per_second")),
        "result": _safe_label(value.get("result"), fallback="unknown"),
    }


def _overall_status(*, maintenance_status: str, active_foreground: int, waiting_foreground: int) -> str:
    if maintenance_status == "blocked":
        return "blocked"
    if active_foreground or waiting_foreground:
        return "pending"
    if maintenance_status in {"unknown", ""}:
        return "warn"
    return "ok"


def _summary(*, status: str, active_foreground: int, waiting_foreground: int) -> str:
    if status == "blocked":
        return "local model maintenance preflight is blocked"
    if active_foreground:
        return "local model foreground work active; maintenance must yield"
    if waiting_foreground:
        return "local model foreground work queued ahead of maintenance"
    if status == "warn":
        return "local model status available; maintenance preflight evidence missing"
    return "local model and memory maintenance guard available"


def _known_cpu_constraint(benchmark: Mapping[str, Any]) -> str:
    latency = _safe_float(benchmark.get("latency_seconds"))
    tokens = _safe_int(benchmark.get("tokens"))
    if latency >= 60.0 or (tokens and latency / max(1, tokens) >= 1.0):
        return "slow_local_model_latency_observed"
    return ""


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key)
    return current


def _safe_label(value: Any, *, fallback: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return fallback
    if _SECRET_RE.search(text) or _PRIVATE_PATH_RE.search(text):
        return fallback
    return text[:120]


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, round(float(value or 0.0), 6))
    except (TypeError, ValueError):
        return 0.0


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload)
    if _SECRET_RE.search(encoded):
        raise ValueError("local model memory status contains secret material")
    if _PRIVATE_PATH_RE.search(encoded):
        raise ValueError("local model memory status contains private path material")
