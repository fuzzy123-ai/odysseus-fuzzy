"""Read-only Roadmap Lens graph projection.

This module turns the structured PlanRuntime roadmap into a bounded
ProgressiveGraphPage for UI and operator overview. It does not mutate roadmap
state and does not inspect markdown files beyond the canonical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from src.plan_runtime import PlanRuntimeNode, PlanRuntimeState
from src.progressive_graph_api import (
    GraphAggregate,
    GraphEdgeSummary,
    GraphNodeSummary,
    GraphQueryBudget,
    GraphViewport,
    ProgressiveGraphPage,
)


_DEFAULT_BUDGET = GraphQueryBudget.create(
    limit=240,
    max_nodes=240,
    max_edges=480,
    depth=2,
    max_hops=0,
    time_budget_ms=200,
    payload_budget_bytes=65536,
)


@dataclass(frozen=True, slots=True)
class RoadmapLensPage:
    page: ProgressiveGraphPage
    active_node_id: str
    claimable_node_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_query_id": self.page.graph_query_id,
            "graph_ref": self.page.graph_ref,
            "viewport": {
                "viewport_ref": self.page.viewport.viewport_ref,
                "node_ref": self.page.viewport.node_ref,
            },
            "query_kind": self.page.query_kind.value,
            "status": self.page.status.value,
            "partial": self.page.partial,
            "clipped": self.page.clipped,
            "next_cursor": self.page.next_cursor,
            "reason": self.page.reason,
            "next_action": self.page.next_action,
            "evidence_ref": self.page.evidence_ref,
            "active_node_id": self.active_node_id,
            "claimable_node_ids": self.claimable_node_ids,
            "budget": {
                "limit": self.page.budget.limit,
                "max_nodes": self.page.budget.max_nodes,
                "max_edges": self.page.budget.max_edges,
                "depth": self.page.budget.depth,
                "max_hops": self.page.budget.max_hops,
                "time_budget_ms": self.page.budget.time_budget_ms,
                "payload_budget_bytes": self.page.budget.payload_budget_bytes,
            },
            "nodes": tuple(
                {
                    "node_id": node.node_id,
                    "label": node.label,
                    "node_type": node.node_type,
                    "score": node.score,
                }
                for node in self.page.nodes
            ),
            "edges": tuple(
                {
                    "edge_id": edge.edge_id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "edge_type": edge.edge_type,
                    "score": edge.score,
                }
                for edge in self.page.edges
            ),
            "aggregates": tuple(
                {
                    "aggregate_id": aggregate.aggregate_id,
                    "label": aggregate.label,
                    "count": aggregate.count,
                }
                for aggregate in self.page.aggregates
            ),
        }


def build_roadmap_lens_page(
    runtime: PlanRuntimeState,
    *,
    budget: GraphQueryBudget | None = None,
) -> RoadmapLensPage:
    if not isinstance(runtime, PlanRuntimeState):
        raise TypeError("runtime must be a PlanRuntimeState")
    graph_budget = budget or _DEFAULT_BUDGET
    if not isinstance(graph_budget, GraphQueryBudget):
        raise TypeError("budget must be a GraphQueryBudget")

    claimable_ids = tuple(node.node_id for node in runtime.claimable_nodes())
    active_node = runtime.recommended_active_node or runtime.next_claimable_node_id()
    nodes: dict[str, GraphNodeSummary] = {}
    edge_specs: list[tuple[str, str, str, float]] = []

    roadmap_id = _lens_id("roadmap", runtime.plan_id)
    _add_node(nodes, roadmap_id, runtime.title, "roadmap", score=1.0)

    for horizon in runtime.version_horizons:
        horizon_id = _lens_id("horizon", horizon)
        _add_node(nodes, horizon_id, horizon, "version_horizon", score=0.4)
        edge_specs.append((roadmap_id, horizon_id, "has_horizon", 0.5))

    for node in runtime.nodes:
        slice_id = _slice_id(node)
        _add_node(nodes, slice_id, node.title, _node_type(node), score=_score(node, active_node, claimable_ids))
        edge_specs.append((roadmap_id, slice_id, "contains_slice", 0.4))
        edge_specs.append((slice_id, _lens_id("horizon", node.horizon), "in_horizon", 0.5))

        for dep in node.depends_on:
            edge_specs.append((_slice_id_by_raw(dep), slice_id, "depends_on", 0.9))
        for gate in node.gates:
            gate_id = _lens_id("gate", gate)
            _add_node(nodes, gate_id, gate, "gate", score=0.3)
            edge_specs.append((slice_id, gate_id, "requires_gate", 0.7))
        for source_ref in node.source_refs:
            file_id = _lens_id("file", source_ref)
            _add_node(nodes, file_id, source_ref, "source_file", score=0.2)
            edge_specs.append((slice_id, file_id, "references_file", 0.45))
        if node.completion_status == "live_installed":
            commit = _completion_commit(node)
            if commit:
                commit_id = _lens_id("commit", commit)
                _add_node(nodes, commit_id, commit, "commit", score=0.5)
                edge_specs.append((slice_id, commit_id, "live_commit", 0.8))

    active_agent_id = _lens_id("agent", "charlie")
    _add_node(nodes, active_agent_id, "Charlie", "agent", score=0.9)
    if active_node:
        edge_specs.append((active_agent_id, _slice_id_by_raw(active_node), "active_run", 1.0))

    selected_nodes = tuple(nodes.values())[: graph_budget.max_nodes]
    selected_ids = {node.node_id for node in selected_nodes}
    selected_edges = tuple(
        GraphEdgeSummary.create(
            edge_id=_edge_id(source, target, edge_type),
            source_id=source,
            target_id=target,
            edge_type=edge_type,
            score=score,
        )
        for source, target, edge_type, score in edge_specs
        if source in selected_ids and target in selected_ids
    )[: graph_budget.max_edges]

    clipped = len(selected_nodes) < len(nodes) or len(selected_edges) < len(edge_specs)
    page = ProgressiveGraphPage.create(
        graph_query_id="roadmap-lens-overview",
        graph_ref="odysseus-roadmap-lens",
        viewport=GraphViewport.create(viewport_ref="roadmap-lens", node_ref=_slice_id_by_raw(active_node) if active_node else ""),
        query_kind="overview",
        budget=graph_budget,
        nodes=selected_nodes,
        edges=selected_edges,
        aggregates=_aggregates(runtime, nodes),
        node_count=len(selected_nodes),
        edge_count=len(selected_edges),
        status="clipped" if clipped else "complete",
        partial=clipped,
        clipped=clipped,
        next_cursor="roadmap-lens-next" if clipped else "",
        reason="roadmap graph clipped by budget" if clipped else "",
        next_action="increase graph budget or request a focused neighborhood" if clipped else "",
        evidence_ref=runtime.roadmap_path,
    )
    return RoadmapLensPage(
        page=page,
        active_node_id=active_node,
        claimable_node_ids=claimable_ids,
    )


def _add_node(nodes: dict[str, GraphNodeSummary], node_id: str, label: str, node_type: str, *, score: float) -> None:
    if node_id in nodes:
        return
    nodes[node_id] = GraphNodeSummary.create(node_id=node_id, label=label, node_type=node_type, score=score)


def _aggregates(runtime: PlanRuntimeState, nodes: dict[str, GraphNodeSummary]) -> tuple[GraphAggregate, ...]:
    type_counts: dict[str, int] = {}
    for node in nodes.values():
        type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1
    status_counts: dict[str, int] = {}
    for node in runtime.nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1
    aggregates = [
        GraphAggregate.create(aggregate_id=f"type-{node_type}", label=f"{node_type} nodes", count=count)
        for node_type, count in sorted(type_counts.items())
    ]
    aggregates.extend(
        GraphAggregate.create(aggregate_id=f"status-{status}", label=f"{status} slices", count=count)
        for status, count in sorted(status_counts.items())
    )
    return tuple(aggregates)


def _node_type(node: PlanRuntimeNode) -> str:
    if node.node_id in {"roadmap-lens-readonly", "orchestration-dashboard-control-center"}:
        return "lens_slice"
    if node.status == "done":
        return "done_slice"
    return "plan_slice"


def _score(node: PlanRuntimeNode, active_node: str, claimable_ids: tuple[str, ...]) -> float:
    if node.node_id == active_node:
        return 1.0
    if node.node_id in claimable_ids:
        return 0.82
    if node.is_live_done:
        return 0.65
    return 0.35


def _completion_commit(node: PlanRuntimeNode) -> str:
    return node.completion_commit


def _slice_id(node: PlanRuntimeNode) -> str:
    return _slice_id_by_raw(node.node_id)


def _slice_id_by_raw(node_id: str) -> str:
    return _lens_id("slice", node_id)


def _edge_id(source: str, target: str, edge_type: str) -> str:
    return _lens_id("edge", f"{source}-{edge_type}-{target}")


def _lens_id(prefix: str, value: str) -> str:
    raw = f"{prefix}-{value}"
    slug = "-".join(part for part in _slug(raw).split("-") if part)
    if len(slug) <= 78:
        return slug
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:67].rstrip('-')}-{digest}"


def _slug(value: str) -> str:
    normalized = []
    last_dash = False
    for char in str(value or "").lower():
        if char.isalnum():
            normalized.append(char)
            last_dash = False
        elif not last_dash:
            normalized.append("-")
            last_dash = True
    return "".join(normalized).strip("-") or "node"
