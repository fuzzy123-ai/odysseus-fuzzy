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

from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import ToolUsageSurface


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
    "tool_usage_invocations_total": {"type": "counter", "help": "Aggregated tool invocations."},
    "tool_usage_failures_total": {"type": "counter", "help": "Aggregated failed tool invocations."},
    "tool_usage_blocked_total": {"type": "counter", "help": "Aggregated blocked or rejected tool invocations."},
    "tool_usage_duration_milliseconds": {"type": "histogram", "help": "Aggregated tool invocation duration in milliseconds."},
}

TOOL_USAGE_ALLOWED_LABELS = frozenset({"family", "source", "surface", "status"})
TOOL_USAGE_METRIC_NAMES = frozenset(
    {
        "tool_usage_invocations_total",
        "tool_usage_failures_total",
        "tool_usage_blocked_total",
        "tool_usage_duration_milliseconds",
    }
)
TOOL_USAGE_AGGREGATE_SCHEMA = "odysseus.tool_usage_analytics.v1"
MAX_TOOL_USAGE_AGGREGATE_ROWS = 200
_TOOL_USAGE_STATUS_VALUES = frozenset(
    {"succeeded", "failed", "blocked", "cancelled", "rejected", "incomplete"}
)

ALLOWED_LABELS = {
    "family",
    "source",
    "surface",
    "component",
    "status",
    "severity",
    "queue",
    "runtime",
    "model_scope",
}

_TOOL_USAGE_LABEL_VALUES = {
    "family": frozenset(item.value for item in ToolFamily),
    "source": frozenset(item.value for item in ToolSource),
    "surface": frozenset(item.value for item in ToolUsageSurface),
    "status": _TOOL_USAGE_STATUS_VALUES,
}
_FORBIDDEN_AGGREGATE_KEYS = frozenset(
    {
        "event",
        "events",
        "raw_event",
        "raw_events",
        "event_id",
        "invocation_id",
        "owner_ref",
        "session_ref",
        "run_ref",
        "correlation_ref",
    }
)
_TOOL_USAGE_ROW_KEYS = frozenset(
    {
        "day",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
        "family",
        "source",
        "surface",
        "status",
        "event_count",
        "invocation_count",
        "calls",
    }
)

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
    if METRIC_DEFINITIONS[metric_name]["type"] == "histogram":
        raise ObservabilityMetricsError("histogram metrics require bounded aggregate buckets")
    safe_labels = _safe_labels(labels or {})
    if metric_name in TOOL_USAGE_METRIC_NAMES:
        _validate_tool_usage_labels(safe_labels)
    sample = RuntimeMetricSample(
        name=metric_name,
        value=_safe_metric_value(value),
        labels=safe_labels,
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


def build_tool_usage_metrics_from_aggregate(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    """Project one bounded analytics snapshot without reading source events."""

    if not isinstance(aggregate, Mapping):
        raise ObservabilityMetricsError("tool usage aggregate must be a mapping")
    if aggregate.get("schema_version") != TOOL_USAGE_AGGREGATE_SCHEMA:
        raise ObservabilityMetricsError("unsupported tool usage aggregate schema")
    _reject_raw_event_shape(aggregate)

    quality = aggregate.get("quality")
    if isinstance(quality, Mapping) and quality.get("result_truncated") is True:
        raise ObservabilityMetricsError("truncated aggregates cannot be projected")

    rows = aggregate.get("rows")
    if not isinstance(rows, (tuple, list)):
        raise ObservabilityMetricsError("tool usage aggregate rows must be a sequence")
    if len(rows) > MAX_TOOL_USAGE_AGGREGATE_ROWS:
        raise ObservabilityMetricsError("tool usage aggregate has too many rows")

    grouped: dict[tuple[tuple[str, str], ...], int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ObservabilityMetricsError("tool usage aggregate row must be a mapping")
        labels = _tool_usage_row_labels(row)
        count_value = row.get("calls", row.get("invocation_count"))
        count = _bounded_count(count_value, field="tool usage invocation count")
        key = tuple(sorted(labels.items()))
        grouped[key] = _bounded_count(
            grouped.get(key, 0) + count,
            field="coalesced tool usage invocation count",
        )

    samples: list[RuntimeMetricSample] = []
    for key, count in sorted(grouped.items()):
        labels = dict(key)
        samples.append(
            build_runtime_metric_sample(
                "tool_usage_invocations_total",
                count,
                labels=labels,
            )
        )
        status = labels["status"]
        if status == "failed":
            samples.append(
                build_runtime_metric_sample(
                    "tool_usage_failures_total",
                    count,
                    labels=labels,
                )
            )
        if status in {
            "blocked",
            "rejected",
        }:
            samples.append(
                build_runtime_metric_sample(
                    "tool_usage_blocked_total",
                    count,
                    labels=labels,
                )
            )

    bounds, counts = _tool_usage_histogram(aggregate)
    histogram_labels = _tool_usage_filter_labels(aggregate.get("filters"))
    payload = dict(build_runtime_metrics_snapshot(samples))
    payload["histograms"] = (
        {
            "name": "tool_usage_duration_milliseconds",
            "labels": histogram_labels,
            "bounds": bounds,
            "counts": counts,
        },
    )
    payload["tool_usage_projection"] = {
        "source_schema": TOOL_USAGE_AGGREGATE_SCHEMA,
        "aggregate_only": True,
        "row_count": len(rows),
        "application_labels": tuple(sorted(TOOL_USAGE_ALLOWED_LABELS)),
        "tool_identifier_exported": False,
        "pseudonymous_references_exported": False,
    }
    _reject_forbidden_payload(payload)
    return payload


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
    histograms = payload.get("histograms", ())
    if not isinstance(histograms, (tuple, list)):
        raise ObservabilityMetricsError("histograms must be a sequence")
    for histogram in histograms:
        if not isinstance(histogram, Mapping):
            raise ObservabilityMetricsError("histogram must be a mapping")
        name = _safe_metric_name(histogram.get("name"))
        definition = METRIC_DEFINITIONS.get(name)
        if not definition or definition.get("type") != "histogram":
            raise ObservabilityMetricsError("unsupported histogram metric")
        labels = _safe_labels(histogram.get("labels") or {})
        if name in TOOL_USAGE_METRIC_NAMES:
            _validate_tool_usage_labels(labels)
        bounds, counts = _normalize_histogram(
            histogram.get("bounds"),
            histogram.get("counts"),
        )
        if name not in emitted:
            lines.append(f"# HELP {name} {_escape_help(definition['help'])}")
            lines.append(f"# TYPE {name} histogram")
            emitted.add(name)
        cumulative = 0
        for bound, count in zip(bounds, counts):
            cumulative += count
            lines.append(
                f"{name}_bucket{_render_histogram_labels(labels, str(bound))} {cumulative}"
            )
        lines.append(
            f"{name}_bucket{_render_histogram_labels(labels, '+Inf')} {cumulative}"
        )
        lines.append(f"{name}_count{_render_labels(labels)} {cumulative}")
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


def _validate_tool_usage_labels(labels: Mapping[str, str]) -> None:
    unknown = set(labels) - TOOL_USAGE_ALLOWED_LABELS
    if unknown:
        raise ObservabilityMetricsError("unsupported tool usage metric label")
    for key, value in labels.items():
        if value not in _TOOL_USAGE_LABEL_VALUES[key]:
            raise ObservabilityMetricsError("unsupported tool usage metric label value")


def _tool_usage_row_labels(row: Mapping[str, Any]) -> dict[str, str]:
    if set(row) - _TOOL_USAGE_ROW_KEYS:
        raise ObservabilityMetricsError("unsupported tool usage aggregate row field")
    raw_labels = {
        "family": row.get("family", row.get("tool_family")),
        "source": row.get("source", row.get("tool_source")),
        "surface": row.get("surface"),
        "status": row.get("status"),
    }
    if any(value is None for value in raw_labels.values()):
        raise ObservabilityMetricsError("tool usage aggregate row is missing a dimension")
    labels = _safe_labels(raw_labels)
    _validate_tool_usage_labels(labels)
    return labels


def _tool_usage_filter_labels(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ObservabilityMetricsError("tool usage aggregate filters must be a mapping")
    if set(value) - (TOOL_USAGE_ALLOWED_LABELS | {"tool"}):
        raise ObservabilityMetricsError("unsupported tool usage aggregate filter")
    if value.get("tool") not in {None, ""}:
        raise ObservabilityMetricsError("tool-filtered aggregates cannot be projected")
    labels = _safe_labels(
        {
            key: item
            for key, item in value.items()
            if key in TOOL_USAGE_ALLOWED_LABELS and item is not None
        }
    )
    _validate_tool_usage_labels(labels)
    return labels


def _tool_usage_histogram(aggregate: Mapping[str, Any]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    histogram = aggregate.get("duration_histogram")
    if not isinstance(histogram, Mapping):
        raise ObservabilityMetricsError("tool usage duration histogram is required")
    bounds, counts = _normalize_histogram(
        histogram.get("bounds_ms"),
        histogram.get("counts"),
    )
    summary = aggregate.get("summary")
    if isinstance(summary, Mapping) and summary.get("duration_samples") is not None:
        expected = _bounded_count(
            summary.get("duration_samples"),
            field="tool usage duration sample count",
        )
        if expected != sum(counts):
            raise ObservabilityMetricsError("tool usage duration sample count mismatch")
    return bounds, counts


def _normalize_histogram(bounds_value: Any, counts_value: Any) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not isinstance(bounds_value, (tuple, list)) or not isinstance(counts_value, (tuple, list)):
        raise ObservabilityMetricsError("histogram bounds and counts must be sequences")
    if not bounds_value or len(bounds_value) != len(counts_value) or len(bounds_value) > 64:
        raise ObservabilityMetricsError("histogram bounds and counts must be bounded and aligned")
    bounds: list[int] = []
    counts: list[int] = []
    previous = -1
    for bound_value, count_value in zip(bounds_value, counts_value):
        if isinstance(bound_value, bool) or not isinstance(bound_value, int):
            raise ObservabilityMetricsError("histogram bound must be an integer")
        if bound_value <= previous or bound_value > 1_000_000_000:
            raise ObservabilityMetricsError("histogram bounds must be strictly increasing and bounded")
        bounds.append(bound_value)
        counts.append(_bounded_count(count_value, field="histogram bucket count"))
        previous = bound_value
    if sum(counts) > 1_000_000_000:
        raise ObservabilityMetricsError("histogram total is too large")
    return tuple(bounds), tuple(counts)


def _bounded_count(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
        raise ObservabilityMetricsError(f"{field} must be a bounded non-negative integer")
    return value


def _reject_raw_event_shape(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).strip().lower() in _FORBIDDEN_AGGREGATE_KEYS:
                raise ObservabilityMetricsError("raw event or pseudonymous reference fields are not accepted")
            _reject_raw_event_shape(nested)
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _reject_raw_event_shape(item)


def _render_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _render_histogram_labels(labels: Mapping[str, str], upper_bound: str) -> str:
    parts = [f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items())]
    parts.append(f'le="{_escape_label(upper_bound)}"')
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
