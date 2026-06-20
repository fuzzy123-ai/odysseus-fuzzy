import json

import pytest

from src.memory_perf_suite_models import ResourceBudget
from src.memory_perf_suite_raptor import (
    RaptorGraphSimulationError,
    run_raptor_graph_scale_simulation,
)


def test_raptor_graph_scale_simulation_passes_large_bounded_budget(tmp_path):
    result = run_raptor_graph_scale_simulation(
        run_dir=tmp_path,
        node_count=100_000,
        edges_per_node=3,
        output_node_budget=1_000,
        output_edge_budget=2_500,
    )

    assert result.passed is True
    assert result.node_count == 100_000
    assert result.edge_count == 300_000
    assert result.returned_nodes == 1_000
    assert result.returned_edges == 2_500
    assert result.clipped is True
    assert result.cursor == {"next_node_offset": 1000, "next_edge_offset": 2500}
    assert result.gates == {
        "large_graph_input_recorded": "passed",
        "deprecated_and_superseded_isolated": "passed",
        "dirty_and_missing_recorded": "passed",
        "output_budget_enforced": "passed",
        "full_payload_not_materialized": "passed",
        "performance_gate": "passed",
    }


def test_raptor_graph_scale_simulation_serializes_counts_without_full_payload(tmp_path):
    result = run_raptor_graph_scale_simulation(run_dir=tmp_path, output_node_budget=10, output_edge_budget=20)
    payload = result.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status_counts"]["deprecated"] > 0
    assert payload["status_counts"]["superseded"] > 0
    assert payload["returned"] == {
        "nodes": 10,
        "edges": 20,
        "clipped": True,
        "cursor": {"next_node_offset": 10, "next_edge_offset": 20},
    }
    assert "raw_content" not in encoded
    assert "notes" not in payload
    assert "edges" not in payload
    assert "nodes" not in payload


def test_raptor_graph_scale_simulation_can_fail_resource_gate(tmp_path):
    (tmp_path / "existing.tmp").write_text("xx", encoding="utf-8")
    budget = ResourceBudget.create(
        max_events=100_000,
        max_event_bytes=4096,
        max_log_bytes=1,
        max_runtime_seconds=30,
        max_memory_mb=128,
    )

    result = run_raptor_graph_scale_simulation(run_dir=tmp_path, budget=budget)

    assert result.gates["performance_gate"] == "failed"
    assert result.passed is False


def test_raptor_graph_scale_simulation_rejects_unbounded_status_ratios(tmp_path):
    with pytest.raises(RaptorGraphSimulationError):
        run_raptor_graph_scale_simulation(
            run_dir=tmp_path,
            deprecated_ratio=0.4,
            superseded_ratio=0.3,
            dirty_ratio=0.2,
            missing_ratio=0.1,
        )
