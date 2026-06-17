"""Read-only release gate model for progressive graph API evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_ID = "progressive_graph_api_release_gate"
_DECISIONS = (
    "progressive_graph_gate_ready",
    "needs_gate_review",
    "blocked",
    "deferred",
)
_STATUSES = (
    "go",
    "blocked",
    "needs_gate_review",
    "deferred",
)
_DEFAULT_ACTIONS = (
    "review progressive graph budget and clipping evidence manually",
    "confirm cursor, aggregate, or next-action continuation evidence offline",
    "keep full-dump, full-render, runtime activation, rebuild, migration, and accelerator paths disabled",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISIONS:
        raise ValueError("unsupported progressive graph api release gate decision")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _STATUSES:
        raise ValueError("unsupported progressive graph api release gate status")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class ProgressiveGraphApiReleaseGate:
    gate_id: str
    decision: str
    status: str
    summary: str
    node_count: int
    edge_count: int
    node_budget: int
    edge_budget: int
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "decision": self.decision,
            "status": self.status,
            "summary": self.summary,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "node_budget": self.node_budget,
            "edge_budget": self.edge_budget,
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Progressive Graph API Release Gate",
            "",
            f"- Gate: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Status: `{self.status}`",
            f"- Node count / budget: `{self.node_count}` / `{self.node_budget}`",
            f"- Edge count / budget: `{self.edge_count}` / `{self.edge_budget}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_progressive_graph_api_release_gate(
    *,
    graph_budget_required: bool = False,
    max_nodes_enforced: bool = False,
    max_edges_enforced: bool = False,
    clipped_status_explained: bool = False,
    partial_status_explained: bool = False,
    cursor_or_next_action_present: bool = False,
    aggregate_view_supported: bool = False,
    full_payload_dump_disabled: bool = True,
    api_runtime_activation_disabled: bool = True,
    node_budget: int = 0,
    edge_budget: int = 0,
    returned_nodes: int = 0,
    returned_edges: int = 0,
    full_payload_dump_enabled: bool = False,
    ui_full_render_enabled: bool = False,
    api_runtime_activation_enabled: bool = False,
    graph_rebuild_enabled: bool = False,
    postgres_runtime_migration_enabled: bool = False,
    qdrant_enabled: bool = False,
    kuzu_enabled: bool = False,
    research_accelerator_enabled: bool = False,
    plugin_scope_touched: bool = False,
    network_enabled: bool = False,
    unsafe_evidence_logging_enabled: bool = False,
) -> ProgressiveGraphApiReleaseGate:
    normalized_node_budget = int(node_budget)
    normalized_edge_budget = int(edge_budget)
    normalized_returned_nodes = int(returned_nodes)
    normalized_returned_edges = int(returned_edges)
    if min(normalized_node_budget, normalized_edge_budget, normalized_returned_nodes, normalized_returned_edges) < 0:
        raise ValueError("budgets and returned counts must be >= 0")

    unsafe_runtime_claimed = any(
        (
            full_payload_dump_enabled,
            ui_full_render_enabled,
            api_runtime_activation_enabled,
            graph_rebuild_enabled,
            postgres_runtime_migration_enabled,
            qdrant_enabled,
            kuzu_enabled,
            research_accelerator_enabled,
            plugin_scope_touched,
            network_enabled,
            unsafe_evidence_logging_enabled,
            not full_payload_dump_disabled,
            not api_runtime_activation_disabled,
        )
    )

    within_budget = (
        normalized_node_budget > 0
        and normalized_edge_budget > 0
        and normalized_returned_nodes <= normalized_node_budget
        and normalized_returned_edges <= normalized_edge_budget
    )
    continuation_present = cursor_or_next_action_present or aggregate_view_supported
    all_positive_gates = all(
        (
            graph_budget_required,
            max_nodes_enforced,
            max_edges_enforced,
            clipped_status_explained,
            partial_status_explained,
            continuation_present,
            full_payload_dump_disabled,
            api_runtime_activation_disabled,
            within_budget,
        )
    )

    if unsafe_runtime_claimed:
        decision = "blocked"
        status = "blocked"
        summary = "full-dump, full-render, runtime activation, rebuild, migration, accelerator, plugin, network, or unsafe logging behavior was enabled"
    elif all_positive_gates:
        decision = "progressive_graph_gate_ready"
        status = "go"
        summary = "progressive graph API release evidence is complete with enforced budgets and explained clipping/continuation behavior"
    elif any(
        value is None
        for value in (
            graph_budget_required,
            max_nodes_enforced,
            max_edges_enforced,
            clipped_status_explained,
            partial_status_explained,
            cursor_or_next_action_present,
            aggregate_view_supported,
        )
    ):
        decision = "deferred"
        status = "deferred"
        summary = "progressive graph API release evidence is deferred until incomplete gate signals are provided"
    else:
        decision = "needs_gate_review"
        status = "needs_gate_review"
        summary = "progressive graph API release evidence still needs budget, clipping, continuation, or compact output review"

    next_allowed_actions = (
        ()
        if decision == "blocked"
        else _normalize_tuple(_DEFAULT_ACTIONS, field_name="next_allowed_action")
    )

    return ProgressiveGraphApiReleaseGate(
        gate_id=_GATE_ID,
        decision=_normalize_decision(decision),
        status=_normalize_status(status),
        summary=summary,
        node_count=normalized_returned_nodes,
        edge_count=normalized_returned_edges,
        node_budget=normalized_node_budget,
        edge_budget=normalized_edge_budget,
        next_allowed_actions=next_allowed_actions,
    )
