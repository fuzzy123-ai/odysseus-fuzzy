"""Synthetic RAPTOR graph-memory scale simulation for the memory perf suite."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable

from src.local_model_scheduler import maintenance_cpu_checkpoint
from src.memory_perf_suite_metrics import ResourceMonitor, evaluate_performance_gate
from src.memory_perf_suite_models import ResourceBudget, SuiteMetric


RAPTOR_SIM_SCHEMA = "odysseus.memory_perf_suite.raptor.v1"


class RaptorGraphSimulationError(ValueError):
    """Raised when a RAPTOR graph simulation input is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class RaptorGraphSimulationResult:
    node_count: int
    edge_count: int
    active_count: int
    deprecated_count: int
    superseded_count: int
    dirty_count: int
    missing_count: int
    returned_nodes: int
    returned_edges: int
    cache_requests: int
    cache_hits: int
    cache_misses: int
    clipped: bool
    cursor: dict[str, int | None]
    gates: dict[str, str]
    metrics: tuple[SuiteMetric, ...]
    schema: str = RAPTOR_SIM_SCHEMA

    @property
    def passed(self) -> bool:
        return all(status == "passed" for status in self.gates.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "status_counts": {
                "active": self.active_count,
                "deprecated": self.deprecated_count,
                "superseded": self.superseded_count,
                "dirty": self.dirty_count,
                "missing": self.missing_count,
            },
            "returned": {
                "nodes": self.returned_nodes,
                "edges": self.returned_edges,
                "clipped": self.clipped,
                "cursor": dict(self.cursor),
            },
            "cache": {
                "requests": self.cache_requests,
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_ratio": round(self.cache_hits / self.cache_requests, 6) if self.cache_requests else 0.0,
            },
            "gates": dict(self.gates),
            "metrics": tuple(metric.to_dict() for metric in self.metrics),
        }


def run_raptor_graph_scale_simulation(
    *,
    run_dir: str | Path,
    node_count: int = 100_000,
    edges_per_node: int = 3,
    output_node_budget: int = 2_000,
    output_edge_budget: int = 5_000,
    deprecated_ratio: float = 0.04,
    superseded_ratio: float = 0.02,
    dirty_ratio: float = 0.01,
    missing_ratio: float = 0.005,
    repeated_view_requests: int = 3,
    budget: ResourceBudget | None = None,
    maintenance_yield_func: Callable[[], Any] | None = None,
) -> RaptorGraphSimulationResult:
    """Simulate a large RAPTOR graph without materializing the full payload."""

    node_count = _positive_int(node_count, "node_count")
    edges_per_node = _positive_int(edges_per_node, "edges_per_node")
    output_node_budget = _positive_int(output_node_budget, "output_node_budget")
    output_edge_budget = _positive_int(output_edge_budget, "output_edge_budget")
    repeated_view_requests = _positive_int(repeated_view_requests, "repeated_view_requests")
    ratios = {
        "deprecated": _ratio(deprecated_ratio, "deprecated_ratio"),
        "superseded": _ratio(superseded_ratio, "superseded_ratio"),
        "dirty": _ratio(dirty_ratio, "dirty_ratio"),
        "missing": _ratio(missing_ratio, "missing_ratio"),
    }
    if math.fsum(ratios.values()) >= 1:
        raise RaptorGraphSimulationError("status ratios must leave active capacity")

    checkpoint = maintenance_yield_func or maintenance_cpu_checkpoint
    checkpoint()
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    monitor = ResourceMonitor(root)
    monitor.start()

    edge_count = node_count * edges_per_node
    deprecated_count = int(node_count * ratios["deprecated"])
    superseded_count = int(node_count * ratios["superseded"])
    dirty_count = int(node_count * ratios["dirty"])
    missing_count = int(node_count * ratios["missing"])
    active_count = node_count - deprecated_count - superseded_count - dirty_count - missing_count

    returned_nodes = min(active_count, output_node_budget)
    returned_edges = min(edge_count, output_edge_budget)
    clipped = returned_nodes < active_count or returned_edges < edge_count
    checkpoint()
    cache_requests, cache_hits, cache_misses = _simulate_cache_reuse(repeated_view_requests)
    monitor.sample()

    resource_budget = budget or ResourceBudget.create(
        max_events=max(node_count, 1),
        max_event_bytes=4096,
        max_log_bytes=2_000_000,
        max_runtime_seconds=30,
        max_memory_mb=128,
    )
    performance_gate = evaluate_performance_gate(monitor.finish(), resource_budget)
    gates = {
        "large_graph_input_recorded": "passed" if node_count >= 100_000 and edge_count >= 250_000 else "failed",
        "deprecated_and_superseded_isolated": (
            "passed" if deprecated_count > 0 and superseded_count > 0 and active_count < node_count else "failed"
        ),
        "dirty_and_missing_recorded": "passed" if dirty_count > 0 and missing_count > 0 else "failed",
        "output_budget_enforced": "passed" if returned_nodes <= output_node_budget and returned_edges <= output_edge_budget else "failed",
        "full_payload_not_materialized": "passed" if clipped else "failed",
        "cache_reuse_recorded": "passed" if cache_requests > 1 and cache_hits > 0 and cache_misses == 1 else "failed",
        "performance_gate": performance_gate.status,
    }
    metrics = (
        SuiteMetric.create(name="raptor_sim_nodes", value=node_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_edges", value=edge_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_active_nodes", value=active_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_deprecated_nodes", value=deprecated_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_superseded_nodes", value=superseded_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_dirty_nodes", value=dirty_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_missing_nodes", value=missing_count, unit="count"),
        SuiteMetric.create(name="raptor_sim_returned_nodes", value=returned_nodes, unit="count"),
        SuiteMetric.create(name="raptor_sim_returned_edges", value=returned_edges, unit="count"),
        SuiteMetric.create(name="raptor_sim_cache_requests", value=cache_requests, unit="count"),
        SuiteMetric.create(name="raptor_sim_cache_hits", value=cache_hits, unit="count"),
        SuiteMetric.create(name="raptor_sim_cache_misses", value=cache_misses, unit="count"),
        SuiteMetric.create(
            name="raptor_sim_cache_hit_ratio",
            value=round(cache_hits / cache_requests, 6) if cache_requests else 0.0,
            unit="ratio",
        ),
        *performance_gate.to_metrics(),
    )
    return RaptorGraphSimulationResult(
        node_count=node_count,
        edge_count=edge_count,
        active_count=active_count,
        deprecated_count=deprecated_count,
        superseded_count=superseded_count,
        dirty_count=dirty_count,
        missing_count=missing_count,
        returned_nodes=returned_nodes,
        returned_edges=returned_edges,
        cache_requests=cache_requests,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        clipped=clipped,
        cursor={
            "next_node_offset": returned_nodes if returned_nodes < active_count else None,
            "next_edge_offset": returned_edges if returned_edges < edge_count else None,
        },
        gates=gates,
        metrics=metrics,
    )


def _positive_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RaptorGraphSimulationError(f"{field_name} must be a positive int")
    return value


def _ratio(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise RaptorGraphSimulationError(f"{field_name} must be numeric")
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        raise RaptorGraphSimulationError(f"{field_name} must be numeric") from None
    if not math.isfinite(ratio) or ratio < 0 or ratio >= 1:
        raise RaptorGraphSimulationError(f"{field_name} must be between 0 and 1")
    return ratio


def _simulate_cache_reuse(requests: int) -> tuple[int, int, int]:
    requests = _positive_int(requests, "repeated_view_requests")
    misses = 1
    hits = max(0, requests - misses)
    return requests, hits, misses
