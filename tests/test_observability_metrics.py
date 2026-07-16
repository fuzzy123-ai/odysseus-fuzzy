from __future__ import annotations

import pytest

from src.observability_metrics import (
    ObservabilityMetricsError,
    build_runtime_metric_sample,
    build_runtime_metrics_from_diagnostics,
    build_runtime_metrics_snapshot,
    metrics_readiness,
    render_prometheus_text,
)


def test_renders_prometheus_text_for_known_content_free_metrics():
    snapshot = build_runtime_metrics_snapshot(
        [
            build_runtime_metric_sample("scheduler_due_tasks", 3, labels={"queue": "default"}),
            build_runtime_metric_sample("llm_call_failures_total", 2, labels={"surface": "telegram"}),
        ]
    )

    text = render_prometheus_text(snapshot)

    assert "# TYPE scheduler_due_tasks gauge" in text
    assert 'scheduler_due_tasks{queue="default"} 3' in text
    assert "# TYPE llm_call_failures_total counter" in text
    assert 'llm_call_failures_total{surface="telegram"} 2' in text
    assert "chat_id" not in text.lower()
    assert "token" not in text.lower()


def test_rejects_unknown_metrics_high_cardinality_labels_and_private_values():
    with pytest.raises(ObservabilityMetricsError, match="unsupported metric name"):
        build_runtime_metric_sample("arbitrary_private_metric", 1)

    with pytest.raises(ObservabilityMetricsError, match="unsupported metric label"):
        build_runtime_metric_sample("scheduler_due_tasks", 1, labels={"owner": "niklas"})

    with pytest.raises(ObservabilityMetricsError):
        build_runtime_metric_sample("scheduler_due_tasks", 1, labels={"queue": r"C:\Users\nkatz\private"})


def test_builds_metrics_from_redacted_diagnostic_summaries():
    snapshot = build_runtime_metrics_from_diagnostics(
        ai_activity={
            "summary": {
                "by_status": {"success": 3, "failed": 2, "error": 1},
                "avg_duration_ms": 1250,
            }
        },
        memory_provenance={
            "summary": {
                "by_event_type": {"memory_write_intent": 5, "memory_maintenance": 2},
                "by_status": {"success": 4, "blocked": 1, "error": 1},
            }
        },
        task_summary={"counts": {"due": 4, "delivery_failed": 2}},
        inbox_summary={"counts": {"processed": 8, "blocked": 1}},
    )

    samples = {sample["name"]: sample for sample in snapshot["samples"]}

    assert samples["llm_call_failures_total"]["value"] == 3.0
    assert samples["local_model_latency_seconds"]["value"] == 1.25
    assert samples["memory_write_success_total"]["value"] == 3.0
    assert samples["memory_write_blocked_total"]["value"] == 2.0
    assert samples["raptorgraph_maintenance_runs_total"]["value"] == 2.0
    assert samples["scheduler_due_tasks"]["value"] == 4.0
    assert samples["universal_inbox_blocked_total"]["value"] == 1.0
    assert snapshot["raw_content_visible"] is False
    assert snapshot["high_cardinality_labels_allowed"] is False


def test_readiness_lists_only_safe_metric_contract():
    readiness = metrics_readiness()

    assert readiness["status"] == "ready"
    assert "telegram_poll_success_total" in readiness["metrics"]
    assert "tool_usage_invocations_total" in readiness["metrics"]
    assert "tool_usage_duration_milliseconds" in readiness["metrics"]
    assert "chat_id" not in str(readiness).lower()
    assert readiness["raw_content_visible"] is False
    assert readiness["high_cardinality_labels_allowed"] is False
