"""Content-free Odysseus runtime metrics for observability backends.

The exporter intentionally accepts only known metric names and low-cardinality
labels. It renders Prometheus text without raw logs, prompts, document text,
chat identifiers, tokens, host paths or provider output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable, Mapping


OBSERVABILITY_METRICS_SCHEMA = "odysseus.observability_metrics.v1"

METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "telegram_poll_success_total": {"type": "counter", "help": "Successful Telegram poll cycles."},
    "telegram_poll_failure_total": {"type": "counter", "help": "Failed Telegram poll cycles."},
    "telegram_last_update_age_seconds": {"type": "gauge", "help": "Age of the last processed Telegram update."},
    "scheduler_due_tasks": {"type": "gauge", "help": "Number of due scheduled tasks."},
    "scheduler_delivery_failures_total": {"type": "counter", "help": "Scheduled task delivery failures."},
    "universal_inbox_processed_total": {"type": "counter", "help": "Universal Inbox items processed."},
    "universal_inbox_blocked_total": {"type": "counter", "help": "Universal Inbox items blocked."},
    "memory_write_success_total": {"type": "counter", "help": "Successful long-term memory write events."},
    "memory_write_blocked_total": {"type": "counter", "help": "Blocked long-term memory write events."},
    "raptorgraph_maintenance_runs_total": {"type": "counter", "help": "RaptorGraph maintenance runs."},
    "raptorgraph_maintenance_failures_total": {"type": "counter", "help": "RaptorGraph maintenance failures."},
    "llm_call_failures_total": {"type": "counter", "help": "Failed upstream or local LLM calls."},
    "local_model_latency_seconds": {"type": "gauge", "help": "Observed local model call latency in seconds."},
}

ALLOWED_LABELS = {
    "surface",
    "component",
    "status",
    "severity",
    "queue",
    "runtime",
    "model_scope",
}

SAFE_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,80}$")
SAFE_METRIC_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
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
    "token",
    "secret",
)


class ObservabilityMetricsError(ValueError):
    """Raised when a metric sample would be unsafe or unsupported."""


@dataclass(frozen=True)
class RuntimeMetricSample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "labels": dict(self.labels)}


def build_runtime_metric_sample(
    name: str,
    value: Any,
    *,
    labels: Mapping[str, Any] | None = None,
) -> RuntimeMetricSample:
    metric_name = _safe_metric_name(name)
    if metric_name not in METRIC_DEFINITIONS:
        raise ObservabilityMetricsError("unsupported metric name")
    sample = RuntimeMetricSample(
        name=metric_name,
        value=_safe_metric_value(value),
        labels=_safe_labels(labels or {}),
    )
    _reject_forbidden_payload(sample.to_dict())
    return sample


def build_runtime_metrics_snapshot(samples: Iterable[RuntimeMetricSample | Mapping[str, Any]]) -> dict[str, Any]:
    normalized = tuple(_normalize_sample(sample) for sample in samples)
    definitions = {
        name: {
            "type": definition["type"],
            "help": definition["help"],
        }
        for name, definition in sorted(METRIC_DEFINITIONS.items())
    }
    snapshot = {
        "schema": OBSERVABILITY_METRICS_SCHEMA,
        "status": "ready",
        "sample_count": len(normalized),
        "samples": tuple(sample.to_dict() for sample in normalized),
        "definitions": definitions,
        "allowed_labels": tuple(sorted(ALLOWED_LABELS)),
        "raw_content_visible": False,
        "high_cardinality_labels_allowed": False,
    }
    _reject_forbidden_payload(snapshot)
    return snapshot


def build_runtime_metrics_from_diagnostics(
    *,
    ai_activity: Mapping[str, Any] | None = None,
    memory_provenance: Mapping[str, Any] | None = None,
    task_summary: Mapping[str, Any] | None = None,
    inbox_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build best-effort metrics from already-redacted diagnostic summaries."""

    samples: list[RuntimeMetricSample] = []
    ai_summary = _summary(ai_activity)
    ai_by_status = _dict(ai_summary.get("by_status"))
    failures = _count(ai_by_status.get("failed")) + _count(ai_by_status.get("error"))
    samples.append(build_runtime_metric_sample("llm_call_failures_total", failures))
    avg_duration_ms = ai_summary.get("avg_duration_ms")
    if avg_duration_ms is not None:
        samples.append(
            build_runtime_metric_sample(
                "local_model_latency_seconds",
                _count(avg_duration_ms) / 1000.0,
                labels={"model_scope": "all"},
            )
        )

    memory_summary = _summary(memory_provenance)
    memory_by_type = _dict(memory_summary.get("by_event_type"))
    memory_by_status = _dict(memory_summary.get("by_status"))
    memory_writes = _count(memory_by_type.get("memory_write_intent"))
    memory_blocked = (
        _count(memory_by_status.get("blocked"))
        + _count(memory_by_status.get("failed"))
        + _count(memory_by_status.get("error"))
    )
    samples.append(build_runtime_metric_sample("memory_write_success_total", max(0, memory_writes - memory_blocked)))
    samples.append(build_runtime_metric_sample("memory_write_blocked_total", memory_blocked))
    raptor_runs = _count(memory_by_type.get("raptorgraph_mutation")) + _count(memory_by_type.get("memory_maintenance"))
    samples.append(build_runtime_metric_sample("raptorgraph_maintenance_runs_total", raptor_runs))
    samples.append(build_runtime_metric_sample("raptorgraph_maintenance_failures_total", _count(memory_by_status.get("error"))))

    task_counts = _dict(task_summary.get("counts") if isinstance(task_summary, Mapping) else None)
    samples.append(build_runtime_metric_sample("scheduler_due_tasks", _count(task_counts.get("due"))))
    samples.append(build_runtime_metric_sample("scheduler_delivery_failures_total", _count(task_counts.get("delivery_failed"))))

    inbox_counts = _dict(inbox_summary.get("counts") if isinstance(inbox_summary, Mapping) else None)
    samples.append(build_runtime_metric_sample("universal_inbox_processed_total", _count(inbox_counts.get("processed"))))
    samples.append(build_runtime_metric_sample("universal_inbox_blocked_total", _count(inbox_counts.get("blocked"))))

    return build_runtime_metrics_snapshot(samples)


def render_prometheus_text(snapshot: Mapping[str, Any] | Iterable[RuntimeMetricSample | Mapping[str, Any]]) -> str:
    payload = snapshot if isinstance(snapshot, Mapping) and snapshot.get("schema") == OBSERVABILITY_METRICS_SCHEMA else build_runtime_metrics_snapshot(snapshot)  # type: ignore[arg-type]
    _reject_forbidden_payload(payload)
    definitions = _dict(payload.get("definitions"))
    samples = tuple(_normalize_sample(sample) for sample in payload.get("samples", ()))
    lines = [
        "# Odysseus runtime metrics. Content-free, low-cardinality, redacted by contract.",
    ]
    emitted: set[str] = set()
    for sample in samples:
        if sample.name not in emitted:
            definition = _dict(definitions.get(sample.name)) or METRIC_DEFINITIONS[sample.name]
            lines.append(f"# HELP {sample.name} {_escape_help(str(definition.get('help') or 'Odysseus runtime metric.'))}")
            lines.append(f"# TYPE {sample.name} {definition.get('type') or 'gauge'}")
            emitted.add(sample.name)
        labels = _render_labels(sample.labels)
        value = _render_value(sample.value)
        lines.append(f"{sample.name}{labels} {value}")
    return "\n".join(lines) + "\n"


def metrics_readiness() -> dict[str, Any]:
    return {
        "schema": OBSERVABILITY_METRICS_SCHEMA,
        "status": "ready",
        "metric_count": len(METRIC_DEFINITIONS),
        "metrics": tuple(sorted(METRIC_DEFINITIONS)),
        "allowed_labels": tuple(sorted(ALLOWED_LABELS)),
        "raw_content_visible": False,
        "high_cardinality_labels_allowed": False,
        "live_scrape_configured": False,
    }


def _normalize_sample(sample: RuntimeMetricSample | Mapping[str, Any]) -> RuntimeMetricSample:
    if isinstance(sample, RuntimeMetricSample):
        return build_runtime_metric_sample(sample.name, sample.value, labels=sample.labels)
    if not isinstance(sample, Mapping):
        raise ObservabilityMetricsError("sample must be a mapping")
    return build_runtime_metric_sample(sample.get("name") or "", sample.get("value"), labels=sample.get("labels") or {})


def _summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    summary = payload.get("summary")
    return dict(summary) if isinstance(summary, Mapping) else {}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _count(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 1_000_000_000))
    except (TypeError, ValueError):
        return 0


def _safe_metric_name(value: Any) -> str:
    text = str(value or "").strip()
    if not SAFE_METRIC_RE.fullmatch(text):
        raise ObservabilityMetricsError("invalid metric name")
    _reject_forbidden_text(text, field="metric_name")
    return text


def _safe_metric_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ObservabilityMetricsError("metric value must be numeric") from None
    if not math.isfinite(number) or number < 0:
        raise ObservabilityMetricsError("metric value must be finite and non-negative")
    return number


def _safe_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(labels, Mapping):
        raise ObservabilityMetricsError("labels must be a mapping")
    result: dict[str, str] = {}
    for key, value in labels.items():
        label_key = str(key or "").strip()
        if label_key not in ALLOWED_LABELS:
            raise ObservabilityMetricsError("unsupported metric label")
        label_value = str(value or "").strip()
        _reject_forbidden_text(label_value, field=label_key)
        if not SAFE_LABEL_VALUE_RE.fullmatch(label_value):
            raise ObservabilityMetricsError("metric label value is too high-cardinality or unsafe")
        result[label_key] = label_value
    return result


def _render_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _render_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ")


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_forbidden_text(str(key), field="field_name")
            _reject_forbidden_payload(nested)
        return
    if isinstance(value, (tuple, list, set)):
        for item in value:
            _reject_forbidden_payload(item)
        return
    if isinstance(value, str):
        _reject_forbidden_text(value)


def _reject_forbidden_text(value: str, *, field: str = "value") -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in FORBIDDEN_MARKERS):
        raise ObservabilityMetricsError(f"{field} contains a forbidden marker")
    if HOST_PATH_RE.search(value):
        raise ObservabilityMetricsError(f"{field} contains a private host path")
