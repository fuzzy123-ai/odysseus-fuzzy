import json
import os
import sys

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)
for _path in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import backend.memory_automation as memory_automation
import backend.raptor_rebuild as raptor_rebuild
from src.memory_runtime_metrics import MemoryRuntimeMetricsRegistry


class _StepClock:
    def __init__(self, step: float) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def _samples(registry, name):
    return [sample for sample in registry.snapshot().samples if sample.name == name]


def test_rebuild_fake_clock_records_phases_resources_and_exact_outcomes(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    wall_clock = _StepClock(0.01)
    cpu_clock = _StepClock(0.002)
    rss_values = iter((1000, 1500, 1500, 1500, 1500, 1500, 1500, 1500))
    monkeypatch.setattr(
        raptor_rebuild, "get_memory_runtime_metrics_registry", lambda: registry
    )
    monkeypatch.setattr(raptor_rebuild, "_METRICS_CLOCK", wall_clock)
    monkeypatch.setattr(raptor_rebuild, "_CPU_CLOCK", cpu_clock)
    monkeypatch.setattr(raptor_rebuild, "_rss_bytes", lambda: next(rss_values))
    monkeypatch.setattr(raptor_rebuild, "_artifact_bytes", lambda _vault: 321)
    monkeypatch.setattr(
        raptor_rebuild,
        "all_flags",
        lambda: {
            "obsidian_raptor_enabled": True,
            "obsidian_raptor_rebuild_enabled": True,
        },
    )
    monkeypatch.setattr(
        raptor_rebuild.vault_service, "markdown_notes", lambda _vault: ["Synthetic.md"]
    )
    monkeypatch.setattr(
        raptor_rebuild.vault_service,
        "read_file",
        lambda _vault, _path: "# Synthetic\n\n[[Other]]",
    )
    monkeypatch.setattr(
        raptor_rebuild.vault_service,
        "secure_path",
        lambda _vault, relative: relative,
    )
    monkeypatch.setattr(raptor_rebuild, "_atomic_write_json", lambda *_args: None)
    monkeypatch.setattr(raptor_rebuild, "clear_raptor_cache", lambda *_args: None)

    result = raptor_rebuild.rebuild_raptor_artifacts("synthetic-vault")

    assert result["success"] is True
    assert result["performance"] == {
        "wall_seconds": pytest.approx(0.16),
        "cpu_seconds": pytest.approx(0.002),
        "rss_delta_bytes": 500,
        "artifact_bytes": 321,
        "source_count": 1,
        "sources_per_second": pytest.approx(1 / 0.16),
    }
    rebuild_samples = _samples(
        registry, "odysseus_raptor_rebuild_duration_seconds"
    )
    success_phases = {
        dict(sample.labels)["phase"]
        for sample in rebuild_samples
        if dict(sample.labels)["outcome"] == "success"
    }
    assert success_phases == {
        "discover",
        "read_hash",
        "build_graph",
        "cluster",
        "serialize",
        "write_artifact",
        "invalidate",
        "total",
    }
    assert _samples(registry, "odysseus_raptor_rebuild_sources")[0].value == 1
    assert _samples(
        registry, "odysseus_raptor_rebuild_rss_delta_bytes"
    )[0].value == 500

    monkeypatch.setattr(raptor_rebuild, "all_flags", lambda: {})
    blocked = raptor_rebuild.rebuild_raptor_artifacts("synthetic-vault")
    assert blocked["blocked"] is True

    monkeypatch.setattr(
        raptor_rebuild,
        "all_flags",
        lambda: {
            "obsidian_raptor_enabled": True,
            "obsidian_raptor_rebuild_enabled": True,
        },
    )
    monkeypatch.setattr(
        raptor_rebuild.vault_service,
        "markdown_notes",
        lambda _vault: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    with pytest.raises(RuntimeError):
        raptor_rebuild.rebuild_raptor_artifacts("synthetic-vault")
    monkeypatch.setattr(
        raptor_rebuild.vault_service,
        "markdown_notes",
        lambda _vault: (_ for _ in ()).throw(TimeoutError("synthetic timeout")),
    )
    with pytest.raises(TimeoutError):
        raptor_rebuild.rebuild_raptor_artifacts("synthetic-vault")

    outcomes = {
        dict(sample.labels)["outcome"]
        for sample in _samples(registry, "odysseus_memory_operations_total")
    }
    assert outcomes == {"success", "blocked", "error", "cancelled"}
    encoded = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert "Synthetic.md" not in encoded
    assert "synthetic-vault" not in encoded
    assert "synthetic failure" not in encoded


def test_automation_records_fixed_outcomes_and_zero_depth_without_owner(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    clock = _StepClock(0.01)
    monkeypatch.setattr(
        memory_automation, "get_memory_runtime_metrics_registry", lambda: registry
    )
    monkeypatch.setattr(memory_automation, "_METRICS_CLOCK", clock)
    responses = iter(
        (
            {"skipped": False, "failed": False, "actions_executed": []},
            {"skipped": True, "failed": False, "actions_executed": []},
            {"skipped": False, "failed": True, "actions_executed": []},
            TimeoutError("synthetic timeout"),
        )
    )

    def _run(**_kwargs):
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(memory_automation, "_run_memory_automation_impl", _run)
    assert memory_automation.run_memory_automation(owner="private-owner")["failed"] is False
    assert memory_automation.run_memory_automation(owner="private-owner")["skipped"] is True
    assert memory_automation.run_memory_automation(owner="private-owner")["failed"] is True
    with pytest.raises(TimeoutError):
        memory_automation.run_memory_automation(owner="private-owner")

    outcomes = {
        dict(sample.labels)["outcome"]
        for sample in _samples(registry, "odysseus_memory_operations_total")
    }
    assert outcomes == {"success", "blocked", "error", "cancelled"}
    queue = _samples(registry, "odysseus_memory_worker_queue_depth")
    assert len(queue) == 1
    assert queue[0].value == 0
    encoded = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert "private-owner" not in encoded
    assert "synthetic timeout" not in encoded
