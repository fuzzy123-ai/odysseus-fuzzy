import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)
for _path in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import backend.derived_index as derived_index
import backend.hybrid_retrieval as hybrid_retrieval
import backend.memory_status as memory_status_backend
import backend.query_layer as query_layer
from src.memory_runtime_metrics import MemoryRuntimeMetricsRegistry


class _StepClock:
    def __init__(self, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def _samples(registry, name):
    return [sample for sample in registry.snapshot().samples if sample.name == name]


def test_fake_clock_covers_query_phases_and_missing_locked_outcomes(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    clock = _StepClock()
    monkeypatch.setattr(derived_index, "get_memory_runtime_metrics_registry", lambda: registry)
    monkeypatch.setattr(derived_index, "_METRICS_CLOCK", clock)
    payload = {
        "sources": [{"path": "Synthetic.md", "links": []}],
        "chunks": [
            {
                "id": "synthetic-1",
                "source_path": "Synthetic.md",
                "title": "Synthetic",
                "text": "bounded graph retrieval",
                "tags": ["test"],
                "source_hash": "synthetic-hash",
            }
        ],
    }
    monkeypatch.setattr(derived_index, "_load_payload", lambda _vault: (payload, False))

    result = derived_index.retrieve_derived_chunks("synthetic-vault", "graph")
    assert result["summary"]["returned"] == 1

    duration_samples = _samples(
        registry, "odysseus_memory_operation_duration_seconds"
    )
    phases = {
        dict(sample.labels)["phase"]
        for sample in duration_samples
        if dict(sample.labels)["outcome"] == "success"
    }
    assert phases == {"total", "load_index", "retrieve", "rank", "build_response"}
    assert all(sample.count == 1 for sample in duration_samples)

    monkeypatch.setattr(derived_index, "_load_payload", lambda _vault: ({}, False))
    with pytest.raises(FileNotFoundError):
        derived_index.retrieve_derived_chunks("synthetic-vault", "graph")

    def _locked(_vault):
        raise PermissionError("locked")

    monkeypatch.setattr(derived_index, "_load_payload", _locked)
    with pytest.raises(PermissionError):
        derived_index.retrieve_derived_chunks("synthetic-vault", "graph")

    def _broken(_vault):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(derived_index, "_load_payload", _broken)
    with pytest.raises(RuntimeError):
        derived_index.retrieve_derived_chunks("synthetic-vault", "graph")

    duration_samples = _samples(
        registry, "odysseus_memory_operation_duration_seconds"
    )
    labelsets = {tuple(sample.labels) for sample in duration_samples}
    assert (
        ("component", "memory"),
        ("operation", "query"),
        ("phase", "total"),
        ("outcome", "blocked"),
        ("runtime", "app"),
    ) in labelsets
    assert (
        ("component", "memory"),
        ("operation", "query"),
        ("phase", "total"),
        ("outcome", "error"),
        ("runtime", "app"),
    ) in labelsets
    assert (
        ("component", "memory"),
        ("operation", "query"),
        ("phase", "load_index"),
        ("outcome", "blocked"),
        ("runtime", "app"),
    ) in labelsets
    encoded = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert "synthetic-vault" not in encoded
    assert "synthetic-hash" not in encoded
    assert "bounded graph retrieval" not in encoded


def test_raptor_status_records_cache_capability_age_and_blocked_missing(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    clock = _StepClock()
    monkeypatch.setattr(
        hybrid_retrieval, "get_memory_runtime_metrics_registry", lambda: registry
    )
    monkeypatch.setattr(hybrid_retrieval, "_METRICS_CLOCK", clock)
    built_at = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        hybrid_retrieval, "_METRICS_WALL_CLOCK", lambda: built_at.timestamp() + 3600
    )
    payloads = iter(
        (
            {
                "readiness": {"ready": True},
                "summary": {},
                "last_built": built_at.isoformat(),
                "cache": {"hit": True, "entry_count": 2},
            },
            {
                "readiness": {"ready": False, "state": "not_configured"},
                "summary": {},
                "last_built": "",
                "cache": {"hit": False, "entry_count": 1},
            },
        )
    )
    monkeypatch.setattr(
        hybrid_retrieval,
        "cached_raptor_payload",
        lambda *_args, **_kwargs: next(payloads),
    )

    ready = hybrid_retrieval.raptor_status("synthetic-vault")
    missing = hybrid_retrieval.raptor_status("synthetic-vault")

    assert ready["raptor_capability_level"] == "graph_cluster_summary"
    assert ready["summary"]["raptor_capability_level"] == "graph_cluster_summary"
    assert missing["raptor_capability_level"] == "graph_cluster_summary"
    assert _samples(registry, "odysseus_raptor_cache_requests_total") == []
    assert _samples(registry, "odysseus_raptor_cache_entries") == []
    assert _samples(registry, "odysseus_raptor_artifact_age_seconds")[0].value == 3600
    outcomes = {
        dict(sample.labels)["outcome"]
        for sample in _samples(registry, "odysseus_memory_operations_total")
    }
    assert outcomes == {"success", "blocked"}


def test_memory_status_exposes_capability_and_records_blocked_empty_fixture(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    clock = _StepClock()
    monkeypatch.setattr(
        memory_status_backend, "get_memory_runtime_metrics_registry", lambda: registry
    )
    monkeypatch.setattr(memory_status_backend, "_METRICS_CLOCK", clock)

    with tempfile.TemporaryDirectory() as vault_dir:
        result = memory_status_backend.memory_status(vault_dir)

    assert result["raptor_capability_level"] == "graph_cluster_summary"
    assert result["summary"]["raptor_capability_level"] == "graph_cluster_summary"
    operation = _samples(registry, "odysseus_memory_operations_total")
    assert any(
        dict(sample.labels)["operation"] == "memory_status"
        and dict(sample.labels)["outcome"] == "blocked"
        for sample in operation
    )


def test_async_query_cancellation_is_content_free_and_counted(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    clock = _StepClock()
    monkeypatch.setattr(query_layer, "get_memory_runtime_metrics_registry", lambda: registry)
    monkeypatch.setattr(query_layer, "_METRICS_CLOCK", clock)

    async def _cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(query_layer, "_answer_query_async_impl", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(query_layer.answer_query_async("synthetic-vault", "private-query"))

    operations = _samples(registry, "odysseus_memory_operations_total")
    assert any(dict(sample.labels)["outcome"] == "cancelled" for sample in operations)
    encoded = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert "private-query" not in encoded
    assert "synthetic-vault" not in encoded
