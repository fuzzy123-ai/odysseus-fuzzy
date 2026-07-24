import json

import pytest

from src.memory_runtime_metrics import (
    MemoryRuntimeMetricsRegistry,
    get_memory_runtime_metrics_registry,
)
from src.observability_metrics import render_process_runtime_metrics
from src.unified_source_index_diagnostics import (
    UnifiedSourceIndexDiagnostics,
    get_unified_source_index_diagnostics,
)


def _sample(snapshot, name: str, **labels: str) -> dict:
    return next(
        sample
        for sample in snapshot["samples"]
        if sample["name"] == name
        and all(sample["labels"].get(key) == value for key, value in labels.items())
    )


def test_positive_path_records_only_the_five_bounded_usi_metrics():
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    diagnostics = UnifiedSourceIndexDiagnostics(registry)

    operation = diagnostics.record_operation(
        "query",
        outcome="success",
        duration_seconds=0.125,
    )
    queue = diagnostics.set_queue_depth("index", 3)
    stale = diagnostics.set_stale_projections(2)
    records = diagnostics.set_record_count("source_version", 11)
    snapshot = diagnostics.snapshot().to_dict()

    assert operation.accepted and operation.accepted_metrics == 2
    assert queue.accepted and stale.accepted and records.accepted
    assert _sample(
        snapshot, "odysseus_usi_operations_total", operation="query"
    )["value"] == 1
    duration = _sample(
        snapshot,
        "odysseus_usi_operation_duration_seconds",
        operation="query",
    )
    assert duration["count"] == 1
    assert duration["sum"] == pytest.approx(0.125)
    assert _sample(snapshot, "odysseus_usi_queue_depth")["value"] == 3
    assert _sample(snapshot, "odysseus_usi_stale_projections")["value"] == 2
    assert _sample(snapshot, "odysseus_usi_records")["value"] == 11
    assert {sample["name"] for sample in snapshot["samples"]} == {
        "odysseus_usi_operations_total",
        "odysseus_usi_operation_duration_seconds",
        "odysseus_usi_queue_depth",
        "odysseus_usi_stale_projections",
        "odysseus_usi_records",
    }
    assert snapshot["raw_content_visible"] is False
    assert snapshot["owner_source_path_query_labels_allowed"] is False
    assert snapshot["productive_source_reads"] == 0
    assert snapshot["productive_source_writes"] == 0
    assert snapshot["network_calls"] == 0
    assert snapshot["model_calls"] == 0
    assert snapshot["live_activation_authorized"] is False


def test_shared_exporter_renders_usi_without_productive_io(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    diagnostics = UnifiedSourceIndexDiagnostics(registry)
    assert diagnostics.record_operation(
        "projection", outcome="blocked", duration_seconds=0.01
    ).accepted
    assert diagnostics.set_queue_depth("projection", 4).accepted
    assert diagnostics.set_stale_projections(1).accepted
    assert diagnostics.set_record_count("projection_manifest", 7).accepted

    def forbidden(*_args, **_kwargs):
        raise AssertionError("productive I/O is forbidden in the diagnostics path")

    monkeypatch.setattr("pathlib.Path.read_text", forbidden)
    monkeypatch.setattr("pathlib.Path.read_bytes", forbidden)
    monkeypatch.setattr("pathlib.Path.open", forbidden)
    rendered = render_process_runtime_metrics(memory_registry=registry)

    for metric_name in (
        "odysseus_usi_operations_total",
        "odysseus_usi_operation_duration_seconds",
        "odysseus_usi_queue_depth",
        "odysseus_usi_stale_projections",
        "odysseus_usi_records",
    ):
        assert metric_name in rendered
    forbidden_fragments = (
        "owner_id",
        "user_id",
        "source_id",
        "source_path",
        "query_text",
        "prompt",
        "response",
    )
    assert all(fragment not in rendered for fragment in forbidden_fragments)


@pytest.mark.parametrize(
    ("method", "args", "reason"),
    [
        ("record_operation", ("private-query",), "operation_rejected"),
        ("record_operation", ("query",), "outcome_rejected"),
        ("set_queue_depth", ("query", -1), "queue_depth_rejected"),
        ("set_queue_depth", ("query", True), "queue_depth_rejected"),
        ("set_stale_projections", (-1,), "stale_projection_count_rejected"),
        ("set_record_count", ("private-path", 1), "record_kind_rejected"),
        ("set_record_count", ("source", -1), "record_count_rejected"),
    ],
)
def test_private_or_invalid_values_are_rejected_before_registry(method, args, reason):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    diagnostics = UnifiedSourceIndexDiagnostics(registry)
    kwargs = {"outcome": "private-output"} if method == "record_operation" else {}

    result = getattr(diagnostics, method)(*args, **kwargs)

    assert result.accepted is False
    assert result.attempted_metrics == 0
    assert result.reason == reason
    payload = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert "private-query" not in payload
    assert "private-output" not in payload
    assert "private-path" not in payload
    assert registry.snapshot().dropped_samples_total == 0


class _ExplodingRegistry:
    def increment_counter(self, *_args, **_kwargs):
        raise RuntimeError("secret registry detail")

    def set_gauge(self, *_args, **_kwargs):
        raise RuntimeError("secret registry detail")

    def observe_histogram(self, *_args, **_kwargs):
        raise RuntimeError("secret registry detail")

    def snapshot(self):
        raise RuntimeError("secret registry detail")


def test_registry_failure_is_fail_soft_and_content_free():
    diagnostics = UnifiedSourceIndexDiagnostics(_ExplodingRegistry())

    operation = diagnostics.record_operation(
        "query", outcome="success", duration_seconds=0.1
    )
    gauge = diagnostics.set_record_count("source", 1)
    snapshot = diagnostics.snapshot().to_dict()

    assert operation.to_dict() == {
        "schema": "odysseus.unified_source_index.diagnostics.v1",
        "accepted": False,
        "attempted_metrics": 2,
        "accepted_metrics": 0,
        "reason": "registry_unavailable",
    }
    assert gauge.reason == "registry_unavailable"
    assert snapshot["samples"] == ()
    assert "secret registry detail" not in json.dumps(snapshot, sort_keys=True)


def test_timer_preserves_success_and_exception_semantics():
    ticks = iter((10.0, 10.25, 20.0, 20.5))
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    diagnostics = UnifiedSourceIndexDiagnostics(registry, clock=lambda: next(ticks))

    with diagnostics.time_operation("query"):
        result = "caller-result"
    assert result == "caller-result"

    with pytest.raises(LookupError, match="caller failure"):
        with diagnostics.time_operation("index", runtime="worker"):
            raise LookupError("caller failure")

    snapshot = diagnostics.snapshot().to_dict()
    assert _sample(
        snapshot,
        "odysseus_usi_operation_duration_seconds",
        operation="query",
        outcome="success",
    )["sum"] == pytest.approx(0.25)
    assert _sample(
        snapshot,
        "odysseus_usi_operation_duration_seconds",
        operation="index",
        outcome="error",
    )["sum"] == pytest.approx(0.5)


def test_singleton_binds_the_existing_process_registry():
    assert (
        get_unified_source_index_diagnostics()._registry
        is get_memory_runtime_metrics_registry()
    )
