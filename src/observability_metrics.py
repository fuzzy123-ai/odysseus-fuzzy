"""Content-free Odysseus runtime metrics for observability backends.

The exporter intentionally accepts only known metric names and low-cardinality
labels. It renders Prometheus text without raw logs, prompts, document text,
chat identifiers, tokens, host paths or provider output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
import re
import threading
import time
from typing import Any, Iterable, Mapping

from src.memory_runtime_metrics import (
    MEMORY_RUNTIME_HISTOGRAM_BUCKETS,
    MEMORY_RUNTIME_LABEL_ENUMS,
    MEMORY_RUNTIME_METRIC_DEFINITIONS,
    MemoryRuntimeMetricsRegistry,
    get_memory_runtime_metrics_registry,
)
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import ToolUsageStatus, ToolUsageSurface
from src.tool_usage_store import (
    DURATION_BUCKET_BOUNDS_MS,
    DURATION_BUCKET_COLUMNS,
    DURATION_OVERFLOW_COLUMN,
)


OBSERVABILITY_METRICS_SCHEMA = "odysseus.observability_metrics.v1"

METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
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
    "tool_usage_invocations_total": {"type": "counter", "help": "Aggregated canonical tool invocations."},
    "tool_usage_failures_total": {"type": "counter", "help": "Aggregated failed tool invocations."},
    "tool_usage_blocked_total": {"type": "counter", "help": "Aggregated blocked tool invocations."},
    "tool_usage_duration_seconds": {"type": "histogram", "help": "Aggregated tool invocation duration."},
    "gemma_maintenance_queue_wait_seconds": {"type": "histogram", "help": "Gemma maintenance admission queue wait duration."},
    "gemma_maintenance_runtime_seconds": {"type": "histogram", "help": "Gemma maintenance lease runtime duration."},
    "gemma_maintenance_context_probe_seconds": {"type": "histogram", "help": "Gemma maintenance context probe duration."},
    "gemma_maintenance_queue_depth": {"type": "gauge", "help": "Current Gemma maintenance queue depth."},
    "gemma_maintenance_admission_total": {"type": "counter", "help": "Gemma maintenance admission decisions."},
    "gemma_maintenance_context_cache_total": {"type": "counter", "help": "Gemma maintenance context cache outcomes."},
    "gemma_maintenance_yield_total": {"type": "counter", "help": "Gemma maintenance CPU yield outcomes."},
    "gemma_maintenance_cancellation_total": {"type": "counter", "help": "Gemma maintenance cancellation outcomes."},
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

TOOL_USAGE_ALLOWED_LABELS = frozenset({"family", "source", "surface", "status"})
TOOL_USAGE_METRIC_NAMES = frozenset(
    {
        "tool_usage_invocations_total",
        "tool_usage_failures_total",
        "tool_usage_blocked_total",
        "tool_usage_duration_seconds",
    }
)
TOOL_USAGE_DURATION_BOUNDS_SECONDS = tuple(
    bound / 1000.0 for bound in DURATION_BUCKET_BOUNDS_MS
)
HISTOGRAM_BOUNDS = {
    "tool_usage_duration_seconds": TOOL_USAGE_DURATION_BOUNDS_SECONDS,
    "gemma_maintenance_queue_wait_seconds": (
        0.001,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        30.0,
    ),
    "gemma_maintenance_runtime_seconds": (
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        30.0,
        60.0,
        300.0,
    ),
    "gemma_maintenance_context_probe_seconds": (
        0.001,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
    ),
}

_MEMORY_METRIC_HELP = {
    "odysseus_memory_operations_total": "Memory operations by bounded component, operation and outcome.",
    "odysseus_memory_operation_duration_seconds": "Memory operation and phase duration.",
    "odysseus_memory_event_loop_lag_seconds": "Event-loop lag observed during Memory work.",
    "odysseus_memory_worker_queue_depth": "Current bounded Memory worker queue depth.",
    "odysseus_raptor_cache_requests_total": "RaptorGraph cache outcomes.",
    "odysseus_raptor_cache_entries": "Current bounded RaptorGraph cache entries.",
    "odysseus_query_cache_entries": "Current bounded query-cache entries.",
    "odysseus_query_cache_bytes": "Current serialized query-cache size.",
    "odysseus_raptor_rebuild_duration_seconds": "RaptorGraph rebuild duration by phase.",
    "odysseus_raptor_rebuild_sources": "Sources in the last completed RaptorGraph rebuild.",
    "odysseus_raptor_rebuild_sources_per_second": "Source throughput of the last completed rebuild.",
    "odysseus_raptor_rebuild_rss_delta_bytes": "RSS delta of the last completed rebuild.",
    "odysseus_raptor_artifact_age_seconds": "Age of the last valid RaptorGraph artifact.",
    "odysseus_usi_operations_total": "Unified Source Index operations by bounded operation and outcome.",
    "odysseus_usi_operation_duration_seconds": "Unified Source Index operation and phase duration.",
    "odysseus_usi_queue_depth": "Current bounded Unified Source Index queue depth.",
    "odysseus_usi_stale_projections": "Current count of stale Unified Source Index projections.",
    "odysseus_usi_records": "Current Unified Source Index record count by bounded record kind.",
    "odysseus_metrics_render_duration_seconds": "Content-free metrics render duration.",
    "odysseus_metrics_samples_dropped_total": "Unsafe or over-cardinality metric samples dropped.",
}
MEMORY_RUNTIME_METRIC_NAMES = frozenset(MEMORY_RUNTIME_METRIC_DEFINITIONS)
for _metric_name, _definition in MEMORY_RUNTIME_METRIC_DEFINITIONS.items():
    METRIC_DEFINITIONS[_metric_name] = {
        "type": _definition.kind,
        "help": _MEMORY_METRIC_HELP[_metric_name],
        "allow_negative": _definition.allow_negative,
    }
    if _definition.kind == "histogram":
        HISTOGRAM_BOUNDS[_metric_name] = MEMORY_RUNTIME_HISTOGRAM_BUCKETS[
            _definition.bucket_family or ""
        ]
ALLOWED_LABELS.update({"operation", "phase", "outcome", "cache_result"})

GMI_RUNTIME_METRIC_NAMES = frozenset(
    {
        "gemma_maintenance_queue_wait_seconds",
        "gemma_maintenance_runtime_seconds",
        "gemma_maintenance_context_probe_seconds",
        "gemma_maintenance_queue_depth",
        "gemma_maintenance_admission_total",
        "gemma_maintenance_context_cache_total",
        "gemma_maintenance_yield_total",
        "gemma_maintenance_cancellation_total",
    }
)
_GMI_EVENT_CONTRACT: dict[str, tuple[str, str, frozenset[str], str]] = {
    "queue_wait": (
        "histogram",
        "gemma_maintenance_queue_wait_seconds",
        frozenset({"observed"}),
        "scheduler",
    ),
    "runtime": (
        "histogram",
        "gemma_maintenance_runtime_seconds",
        frozenset({"completed", "cancelled"}),
        "scheduler",
    ),
    "context_probe": (
        "histogram",
        "gemma_maintenance_context_probe_seconds",
        frozenset({"success", "failure", "cancelled"}),
        "context_service",
    ),
    "queue_depth": (
        "gauge",
        "gemma_maintenance_queue_depth",
        frozenset({"current"}),
        "scheduler",
    ),
    "admission": (
        "counter",
        "gemma_maintenance_admission_total",
        frozenset({"admitted", "bypassed", "rejected"}),
        "scheduler",
    ),
    "context_cache": (
        "counter",
        "gemma_maintenance_context_cache_total",
        frozenset({"hit", "stale", "miss", "negative"}),
        "context_service",
    ),
    "yield": (
        "counter",
        "gemma_maintenance_yield_total",
        frozenset({"yielded", "continued", "disabled"}),
        "cpu_checkpoint",
    ),
    "cancellation": (
        "counter",
        "gemma_maintenance_cancellation_total",
        frozenset({"queue_wait", "runtime", "context_wait", "context_probe"}),
        "runtime_control",
    ),
}
MAX_TOOL_USAGE_AGGREGATE_ROWS = 10_000
MAX_TOOL_USAGE_LABELSETS = 256
_MAX_AGGREGATE_COUNT = 1_000_000_000
_TOOL_ANALYTICS_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
_TOOL_USAGE_DAILY_FIELDS = frozenset(
    {
        "day",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
        "surface",
        "status",
        "invocation_count",
        "duration_count",
        "duration_total_ms",
        "distinct_owner_count",
        "distinct_session_count",
        "retry_count",
        "unknown_identity_count",
        *DURATION_BUCKET_COLUMNS,
        DURATION_OVERFLOW_COLUMN,
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


@dataclass(frozen=True)
class RuntimeHistogramSample:
    name: str
    count: int
    sum_value: float
    buckets: tuple[tuple[float, int], ...]
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "histogram",
            "name": self.name,
            "count": self.count,
            "sum": self.sum_value,
            "buckets": tuple(
                {"le": bound, "count": count} for bound, count in self.buckets
            ),
            "labels": dict(self.labels),
        }


class MaintenanceRuntimeMetricsRegistry:
    """Fixed-cardinality, process-local metrics for the Gemma maintenance lane."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._counters: dict[tuple[str, str], int] = {}
            self._gauges: dict[tuple[str, str], float] = {}
            self._histograms: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, event: str, *, status: str, value: Any = 1) -> None:
        contract = _GMI_EVENT_CONTRACT.get(str(event or ""))
        if contract is None:
            raise ObservabilityMetricsError("unsupported maintenance metric event")
        kind, metric_name, allowed_statuses, _component = contract
        normalized_status = str(status or "")
        if normalized_status not in allowed_statuses:
            raise ObservabilityMetricsError("unsupported maintenance metric status")
        number = _safe_metric_value(value)
        if number > 3_600:
            raise ObservabilityMetricsError("maintenance metric value exceeds bound")
        key = (event, normalized_status)
        with self._lock:
            if kind == "counter":
                if number != 1:
                    raise ObservabilityMetricsError(
                        "maintenance counter events must increment by one"
                    )
                self._counters[key] = self._counters.get(key, 0) + 1
                return
            if kind == "gauge":
                self._gauges[key] = number
                return
            bounds = HISTOGRAM_BOUNDS[metric_name]
            state = self._histograms.setdefault(
                key,
                {
                    "count": 0,
                    "sum": 0.0,
                    "buckets": [0 for _ in bounds],
                },
            )
            state["count"] += 1
            state["sum"] += number
            for index, bound in enumerate(bounds):
                if number <= bound:
                    state["buckets"][index] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {
                key: {
                    "count": state["count"],
                    "sum": state["sum"],
                    "buckets": tuple(state["buckets"]),
                }
                for key, state in self._histograms.items()
            }

        samples: list[RuntimeMetricSample | RuntimeHistogramSample] = []
        for (event, status), value in sorted(counters.items()):
            _kind, metric_name, _statuses, component = _GMI_EVENT_CONTRACT[event]
            samples.append(
                build_runtime_metric_sample(
                    metric_name,
                    value,
                    labels=_gmi_labels(event, status=status, component=component),
                )
            )
        for (event, status), value in sorted(gauges.items()):
            _kind, metric_name, _statuses, component = _GMI_EVENT_CONTRACT[event]
            samples.append(
                build_runtime_metric_sample(
                    metric_name,
                    value,
                    labels=_gmi_labels(event, status=status, component=component),
                )
            )
        for (event, status), state in sorted(histograms.items()):
            _kind, metric_name, _statuses, component = _GMI_EVENT_CONTRACT[event]
            bounds = HISTOGRAM_BOUNDS[metric_name]
            samples.append(
                build_runtime_histogram_sample(
                    metric_name,
                    count=state["count"],
                    sum_value=state["sum"],
                    buckets=dict(zip(bounds, state["buckets"])),
                    labels=_gmi_labels(event, status=status, component=component),
                )
            )
        snapshot = build_runtime_metrics_snapshot(samples)
        snapshot.update(
            {
                "source_kind": "process_local_gemma_maintenance",
                "metric_family": "gemma_maintenance",
                "fixed_metric_names": tuple(sorted(GMI_RUNTIME_METRIC_NAMES)),
                "model_scope": "gemma3_4b",
                "allowed_labels": (
                    "component",
                    "model_scope",
                    "queue",
                    "runtime",
                    "status",
                ),
                "raw_content_visible": False,
                "high_cardinality_labels_allowed": False,
                "live_scrape_configured": False,
            }
        )
        snapshot["definitions"] = {
            name: snapshot["definitions"][name]
            for name in sorted(GMI_RUNTIME_METRIC_NAMES)
        }
        _reject_forbidden_payload(snapshot)
        return snapshot


_MAINTENANCE_RUNTIME_METRICS = MaintenanceRuntimeMetricsRegistry()


def record_gmi_runtime_event(event: str, *, status: str, value: Any = 1) -> None:
    """Record one closed maintenance event without accepting arbitrary labels."""

    _MAINTENANCE_RUNTIME_METRICS.record(event, status=status, value=value)


def maintenance_runtime_metrics_snapshot() -> dict[str, Any]:
    return _MAINTENANCE_RUNTIME_METRICS.snapshot()


def reset_maintenance_runtime_metrics() -> None:
    _MAINTENANCE_RUNTIME_METRICS.reset()


def _gmi_labels(event: str, *, status: str, component: str) -> dict[str, str]:
    labels = {
        "model_scope": "gemma3_4b",
        "runtime": "maintenance",
        "component": component,
        "status": status,
    }
    if event in {"queue_wait", "queue_depth", "admission"}:
        labels["queue"] = "maintenance"
    return labels


def build_runtime_metric_sample(
    name: str,
    value: Any,
    *,
    labels: Mapping[str, Any] | None = None,
) -> RuntimeMetricSample:
    metric_name = _safe_metric_name(name)
    if metric_name not in METRIC_DEFINITIONS:
        raise ObservabilityMetricsError("unsupported metric name")
    if METRIC_DEFINITIONS[metric_name].get("type") == "histogram":
        raise ObservabilityMetricsError("histogram metric requires bounded buckets")
    sample = RuntimeMetricSample(
        name=metric_name,
        value=_safe_metric_value(
            value,
            allow_negative=bool(METRIC_DEFINITIONS[metric_name].get("allow_negative")),
        ),
        labels=_safe_labels(labels or {}, metric_name=metric_name),
    )
    _reject_forbidden_payload(sample.to_dict())
    return sample


def build_runtime_histogram_sample(
    name: str,
    *,
    count: Any,
    sum_value: Any,
    buckets: Mapping[Any, Any] | Iterable[Mapping[str, Any] | tuple[Any, Any]],
    labels: Mapping[str, Any] | None = None,
) -> RuntimeHistogramSample:
    metric_name = _safe_metric_name(name)
    definition = METRIC_DEFINITIONS.get(metric_name)
    if not definition or definition.get("type") != "histogram":
        raise ObservabilityMetricsError("unsupported histogram metric name")
    normalized_count = _safe_aggregate_count(count, "histogram count")
    normalized_sum = _safe_metric_value(sum_value)
    normalized_buckets = _safe_histogram_buckets(
        metric_name,
        buckets,
        count=normalized_count,
    )
    sample = RuntimeHistogramSample(
        name=metric_name,
        count=normalized_count,
        sum_value=normalized_sum,
        buckets=normalized_buckets,
        labels=_safe_labels(labels or {}, metric_name=metric_name),
    )
    _reject_forbidden_payload(sample.to_dict())
    return sample


def build_runtime_metrics_snapshot(
    samples: Iterable[
        RuntimeMetricSample | RuntimeHistogramSample | Mapping[str, Any]
    ],
) -> dict[str, Any]:
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


def build_tool_usage_metrics_snapshot(
    aggregate_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project content-free daily aggregates into bounded Prometheus series.

    The projector deliberately accepts the daily aggregate contract, not raw
    invocation events. Tool identifiers are validated only to establish the
    row shape and are then aggregated away before a metric sample is built.
    """

    bounded_rows: list[Mapping[str, Any]] = []
    for row in aggregate_rows:
        if len(bounded_rows) >= MAX_TOOL_USAGE_AGGREGATE_ROWS:
            raise ObservabilityMetricsError("too many tool usage aggregate rows")
        bounded_rows.append(row)
    rows = tuple(bounded_rows)

    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    primary_keys: set[tuple[str, str, str, str]] = set()
    for row in rows:
        normalized = _safe_tool_usage_aggregate_row(row)
        primary_key = (
            normalized["day"],
            normalized["tool_analytics_id"],
            normalized["surface"],
            normalized["status"],
        )
        if primary_key in primary_keys:
            raise ObservabilityMetricsError("duplicate tool usage aggregate row")
        primary_keys.add(primary_key)

        label_key = (
            normalized["tool_family"],
            normalized["tool_source"],
            normalized["surface"],
            normalized["status"],
        )
        if label_key not in groups and len(groups) >= MAX_TOOL_USAGE_LABELSETS:
            raise ObservabilityMetricsError("tool usage metric cardinality limit exceeded")
        group = groups.setdefault(
            label_key,
            {
                "invocations": 0,
                "duration_count": 0,
                "duration_total_ms": 0,
                "buckets": [0 for _ in DURATION_BUCKET_COLUMNS],
            },
        )
        group["invocations"] += normalized["invocation_count"]
        group["duration_count"] += normalized["duration_count"]
        group["duration_total_ms"] += normalized["duration_total_ms"]
        for index, column in enumerate(DURATION_BUCKET_COLUMNS):
            group["buckets"][index] += normalized[column]

    samples: list[RuntimeMetricSample | RuntimeHistogramSample] = []
    for label_key, group in sorted(groups.items()):
        family, source, surface, status = label_key
        labels = {
            "family": family,
            "source": source,
            "surface": surface,
            "status": status,
        }
        samples.append(
            build_runtime_metric_sample(
                "tool_usage_invocations_total",
                group["invocations"],
                labels=labels,
            )
        )
        if status == ToolUsageStatus.FAILED.value:
            samples.append(
                build_runtime_metric_sample(
                    "tool_usage_failures_total",
                    group["invocations"],
                    labels=labels,
                )
            )
        if status == ToolUsageStatus.BLOCKED.value:
            samples.append(
                build_runtime_metric_sample(
                    "tool_usage_blocked_total",
                    group["invocations"],
                    labels=labels,
                )
            )
        samples.append(
            build_runtime_histogram_sample(
                "tool_usage_duration_seconds",
                count=group["duration_count"],
                sum_value=group["duration_total_ms"] / 1000.0,
                buckets=dict(
                    zip(TOOL_USAGE_DURATION_BOUNDS_SECONDS, group["buckets"])
                ),
                labels=labels,
            )
        )

    snapshot = build_runtime_metrics_snapshot(samples)
    snapshot.update(
        {
            "source_schema": "odysseus.tool_usage_store.v2.daily_aggregate",
            "source_kind": "daily_aggregates",
            "aggregate_row_count": len(rows),
            "labelset_count": len(groups),
            "allowed_labels": tuple(sorted(TOOL_USAGE_ALLOWED_LABELS)),
            "tool_id_label_allowed": False,
            "pseudonymous_reference_labels_allowed": False,
        }
    )
    snapshot["definitions"] = {
        name: snapshot["definitions"][name]
        for name in sorted(TOOL_USAGE_METRIC_NAMES)
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
    task_counts = _dict(task_summary.get("counts") if isinstance(task_summary, Mapping) else None)
    samples.append(build_runtime_metric_sample("scheduler_due_tasks", _count(task_counts.get("due"))))
    samples.append(build_runtime_metric_sample("scheduler_delivery_failures_total", _count(task_counts.get("delivery_failed"))))

    inbox_counts = _dict(inbox_summary.get("counts") if isinstance(inbox_summary, Mapping) else None)
    samples.append(build_runtime_metric_sample("universal_inbox_processed_total", _count(inbox_counts.get("processed"))))
    samples.append(build_runtime_metric_sample("universal_inbox_blocked_total", _count(inbox_counts.get("blocked"))))

    snapshot = build_runtime_metrics_snapshot(samples)
    snapshot.update(
        {
            "source_kind": "bounded_redacted_diagnostics",
            "prometheus_scrape_eligible": False,
            "excluded_unscoped_metrics": (
                "local_model_latency_seconds",
                "raptorgraph_maintenance_runs_total",
                "raptorgraph_maintenance_failures_total",
            ),
        }
    )
    return snapshot


def build_process_runtime_metrics_snapshot(
    *,
    memory_registry: MemoryRuntimeMetricsRegistry | None = None,
) -> dict[str, Any]:
    """Merge typed process-local registries without any diagnostic-store I/O."""

    registry = memory_registry or get_memory_runtime_metrics_registry()
    memory_snapshot = registry.snapshot()
    samples: list[RuntimeMetricSample | RuntimeHistogramSample | Mapping[str, Any]] = []
    for sample in memory_snapshot.samples:
        labels = dict(sample.labels)
        if sample.kind == "histogram":
            samples.append(
                build_runtime_histogram_sample(
                    sample.name,
                    count=sample.count,
                    sum_value=sample.sum_value,
                    buckets={
                        bucket.upper_bound: bucket.cumulative_count
                        for bucket in sample.buckets
                    },
                    labels=labels,
                )
            )
        else:
            samples.append(
                build_runtime_metric_sample(sample.name, sample.value, labels=labels)
            )

    gmi_snapshot = maintenance_runtime_metrics_snapshot()
    samples.extend(gmi_snapshot.get("samples", ()))
    snapshot = build_runtime_metrics_snapshot(samples)
    snapshot.update(
        {
            "source_kind": "process_local_registries",
            "memory_registry_schema": memory_snapshot.schema,
            "memory_prometheus_series_count": memory_snapshot.prometheus_series_count,
            "legacy_diagnostic_projection_on_scrape": False,
            "filesystem_reads": 0,
            "ledger_reads": 0,
            "vault_reads": 0,
            "corpus_reads": 0,
            "query_calls": 0,
            "model_calls": 0,
            "network_calls": 0,
            "raw_content_visible": False,
            "high_cardinality_labels_allowed": False,
            "live_scrape_configured": False,
        }
    )
    _reject_forbidden_payload(snapshot)
    return snapshot


def render_process_runtime_metrics(
    *,
    memory_registry: MemoryRuntimeMetricsRegistry | None = None,
) -> str:
    """Render a scrape and record content-free exporter overhead for the next scrape."""

    registry = memory_registry or get_memory_runtime_metrics_registry()
    started = time.perf_counter()
    try:
        rendered = render_prometheus_text(
            build_process_runtime_metrics_snapshot(memory_registry=registry)
        )
    except Exception:
        registry.observe_histogram(
            "odysseus_metrics_render_duration_seconds",
            {"outcome": "error", "runtime": "app"},
            max(0.0, time.perf_counter() - started),
        )
        raise
    registry.observe_histogram(
        "odysseus_metrics_render_duration_seconds",
        {"outcome": "success", "runtime": "app"},
        max(0.0, time.perf_counter() - started),
    )
    return rendered


def render_prometheus_text(
    snapshot: Mapping[str, Any]
    | Iterable[RuntimeMetricSample | RuntimeHistogramSample | Mapping[str, Any]],
) -> str:
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
        if isinstance(sample, RuntimeHistogramSample):
            for bound, count in sample.buckets:
                # Prometheus requires ``le`` for histogram buckets. It is
                # generated only from the fixed contract and is never an
                # accepted caller-supplied or data-derived label.
                bucket_labels = {**sample.labels, "le": _render_histogram_bound(bound)}
                lines.append(
                    f"{sample.name}_bucket{_render_labels(bucket_labels)} {_render_value(float(count))}"
                )
            infinity_labels = {**sample.labels, "le": "+Inf"}
            lines.append(
                f"{sample.name}_bucket{_render_labels(infinity_labels)} {_render_value(float(sample.count))}"
            )
            labels = _render_labels(sample.labels)
            lines.append(f"{sample.name}_sum{labels} {_render_value(sample.sum_value)}")
            lines.append(f"{sample.name}_count{labels} {_render_value(float(sample.count))}")
            continue
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
        "scrape_source_kind": "process_local_registries",
        "legacy_diagnostic_projection_on_scrape": False,
        "scrape_scope": "observability:read",
        "additional_scrape_scopes_allowed": False,
        "local_latency_scope": "gemma3_4b_maintenance_only",
        "raptor_error_scope": "typed_raptor_operation_only",
        "prometheus_configured": False,
        "grafana_configured": False,
        "live_scrape_configured": False,
    }


def _normalize_sample(
    sample: RuntimeMetricSample | RuntimeHistogramSample | Mapping[str, Any],
) -> RuntimeMetricSample | RuntimeHistogramSample:
    if isinstance(sample, (RuntimeMetricSample, RuntimeHistogramSample)):
        if isinstance(sample, RuntimeHistogramSample):
            return build_runtime_histogram_sample(
                sample.name,
                count=sample.count,
                sum_value=sample.sum_value,
                buckets=sample.buckets,
                labels=sample.labels,
            )
        return build_runtime_metric_sample(sample.name, sample.value, labels=sample.labels)
    if not isinstance(sample, Mapping):
        raise ObservabilityMetricsError("sample must be a mapping")
    if sample.get("kind") == "histogram" or "buckets" in sample:
        return build_runtime_histogram_sample(
            sample.get("name") or "",
            count=sample.get("count"),
            sum_value=sample.get("sum"),
            buckets=sample.get("buckets") or (),
            labels=sample.get("labels") or {},
        )
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


def _safe_tool_usage_aggregate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ObservabilityMetricsError("tool usage aggregate row must be a mapping")
    keys = {str(key) for key in row}
    if keys != _TOOL_USAGE_DAILY_FIELDS:
        if keys - _TOOL_USAGE_DAILY_FIELDS:
            raise ObservabilityMetricsError("unsupported tool usage aggregate field")
        raise ObservabilityMetricsError("incomplete tool usage daily aggregate row")

    day = str(row["day"] or "")
    try:
        parsed_day = date.fromisoformat(day)
    except ValueError as exc:
        raise ObservabilityMetricsError("invalid aggregate day") from exc
    if parsed_day.isoformat() != day:
        raise ObservabilityMetricsError("invalid aggregate day")

    tool_analytics_id = str(row["tool_analytics_id"] or "")
    if not _TOOL_ANALYTICS_ID_RE.fullmatch(tool_analytics_id):
        raise ObservabilityMetricsError("invalid aggregate tool identity")

    normalized: dict[str, Any] = {
        "day": day,
        "tool_analytics_id": tool_analytics_id,
        "tool_family": _controlled_enum_value(
            ToolFamily, row["tool_family"], "tool family"
        ),
        "tool_source": _controlled_enum_value(
            ToolSource, row["tool_source"], "tool source"
        ),
        "surface": _controlled_enum_value(
            ToolUsageSurface, row["surface"], "tool usage surface"
        ),
        "status": _controlled_enum_value(
            ToolUsageStatus, row["status"], "tool usage status"
        ),
    }
    count_fields = (
        "invocation_count",
        "duration_count",
        "duration_total_ms",
        "distinct_owner_count",
        "distinct_session_count",
        "retry_count",
        "unknown_identity_count",
        *DURATION_BUCKET_COLUMNS,
        DURATION_OVERFLOW_COLUMN,
    )
    for field_name in count_fields:
        normalized[field_name] = _safe_aggregate_count(row[field_name], field_name)

    invocation_count = normalized["invocation_count"]
    duration_count = normalized["duration_count"]
    if duration_count > invocation_count:
        raise ObservabilityMetricsError("duration count exceeds invocation count")
    if duration_count == 0 and normalized["duration_total_ms"] != 0:
        raise ObservabilityMetricsError("duration sum requires duration samples")
    for field_name in (
        "distinct_owner_count",
        "distinct_session_count",
        "unknown_identity_count",
    ):
        if normalized[field_name] > invocation_count:
            raise ObservabilityMetricsError(
                f"{field_name.replace('_', ' ')} exceeds invocation count"
            )

    previous = 0
    for column in DURATION_BUCKET_COLUMNS:
        bucket_count = normalized[column]
        if bucket_count < previous or bucket_count > duration_count:
            raise ObservabilityMetricsError("invalid cumulative duration histogram")
        previous = bucket_count
    if previous + normalized[DURATION_OVERFLOW_COLUMN] != duration_count:
        raise ObservabilityMetricsError("duration histogram does not match count")
    return normalized


def _controlled_enum_value(enum_type, value: Any, field_name: str) -> str:
    text = str(value or "")
    try:
        return enum_type(text).value
    except (TypeError, ValueError) as exc:
        raise ObservabilityMetricsError(
            f"unsupported controlled {field_name} value"
        ) from exc


def _safe_aggregate_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObservabilityMetricsError(f"{field_name} must be an integer")
    if value < 0 or value > _MAX_AGGREGATE_COUNT:
        raise ObservabilityMetricsError(f"{field_name} is outside the bounded range")
    return value


def _safe_histogram_buckets(
    metric_name: str,
    buckets: Mapping[Any, Any]
    | Iterable[Mapping[str, Any] | tuple[Any, Any]],
    *,
    count: int,
) -> tuple[tuple[float, int], ...]:
    expected_bounds = HISTOGRAM_BOUNDS.get(metric_name)
    if expected_bounds is None:
        raise ObservabilityMetricsError("histogram has no fixed bucket contract")
    items = buckets.items() if isinstance(buckets, Mapping) else buckets
    normalized: list[tuple[float, int]] = []
    for item in items:
        if isinstance(item, Mapping):
            if set(item) != {"le", "count"}:
                raise ObservabilityMetricsError("invalid histogram bucket shape")
            bound_value, count_value = item["le"], item["count"]
        else:
            try:
                bound_value, count_value = item
            except (TypeError, ValueError) as exc:
                raise ObservabilityMetricsError("invalid histogram bucket shape") from exc
        try:
            bound = float(bound_value)
        except (TypeError, ValueError) as exc:
            raise ObservabilityMetricsError("histogram bound must be numeric") from exc
        if not math.isfinite(bound) or bound <= 0:
            raise ObservabilityMetricsError("histogram bound must be finite and positive")
        normalized.append(
            (bound, _safe_aggregate_count(count_value, "histogram bucket count"))
        )
    if len({bound for bound, _ in normalized}) != len(normalized):
        raise ObservabilityMetricsError("duplicate histogram bound")
    by_bound = dict(normalized)
    if set(by_bound) != set(expected_bounds):
        raise ObservabilityMetricsError("histogram buckets do not match the fixed contract")
    normalized = [(bound, by_bound[bound]) for bound in expected_bounds]
    previous = 0
    for _, bucket_count in normalized:
        if bucket_count < previous or bucket_count > count:
            raise ObservabilityMetricsError("invalid cumulative histogram counts")
        previous = bucket_count
    return tuple(normalized)


def _safe_metric_name(value: Any) -> str:
    text = str(value or "").strip()
    if not SAFE_METRIC_RE.fullmatch(text):
        raise ObservabilityMetricsError("invalid metric name")
    _reject_forbidden_text(text, field="metric_name")
    return text


def _safe_metric_value(value: Any, *, allow_negative: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ObservabilityMetricsError("metric value must be numeric") from None
    if not math.isfinite(number) or (number < 0 and not allow_negative):
        raise ObservabilityMetricsError("metric value must be finite and within its signedness contract")
    return number


def _safe_labels(
    labels: Mapping[str, Any],
    *,
    metric_name: str | None = None,
) -> dict[str, str]:
    if not isinstance(labels, Mapping):
        raise ObservabilityMetricsError("labels must be a mapping")
    if metric_name in MEMORY_RUNTIME_METRIC_NAMES:
        definition = MEMORY_RUNTIME_METRIC_DEFINITIONS[metric_name]
        if set(labels) != set(definition.labels):
            raise ObservabilityMetricsError("memory metric labels do not match the fixed contract")
        result: dict[str, str] = {}
        for label_key in definition.labels:
            label_value = labels.get(label_key)
            if not isinstance(label_value, str) or label_value not in MEMORY_RUNTIME_LABEL_ENUMS[label_key]:
                raise ObservabilityMetricsError("unsupported memory metric label value")
            result[label_key] = label_value
        return result
    allowed_labels = TOOL_USAGE_ALLOWED_LABELS if metric_name in TOOL_USAGE_METRIC_NAMES else ALLOWED_LABELS
    result: dict[str, str] = {}
    for key, value in labels.items():
        label_key = str(key or "").strip()
        if label_key not in allowed_labels:
            raise ObservabilityMetricsError("unsupported metric label")
        label_value = str(value or "").strip()
        _reject_forbidden_text(label_value, field=label_key)
        if not SAFE_LABEL_VALUE_RE.fullmatch(label_value):
            raise ObservabilityMetricsError("metric label value is too high-cardinality or unsafe")
        if metric_name in TOOL_USAGE_METRIC_NAMES:
            enum_type = {
                "family": ToolFamily,
                "source": ToolSource,
                "surface": ToolUsageSurface,
                "status": ToolUsageStatus,
            }[label_key]
            _controlled_enum_value(enum_type, label_value, f"{label_key} label")
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


def _render_histogram_bound(value: float) -> str:
    return _render_value(float(value))


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
