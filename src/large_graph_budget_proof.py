"""Read-only proof model for large graph budget evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "large_graph_input_recorded",
    "node_count_at_least_100k",
    "edge_count_recorded",
    "output_budget_enforced",
    "returned_nodes_within_budget",
    "returned_edges_within_budget",
    "clipping_explained",
    "cursor_or_aggregate_available",
    "no_full_payload_dump",
    "accelerator_not_required",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_budget_review",
    "deferred",
)

_DECISION_VALUES = (
    "budget_proof_ready",
    "needs_budget_review",
    "blocked",
    "deferred",
)

_MIN_LARGE_NODE_COUNT = 100_000

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review large-graph count and clipping evidence manually",
    "confirm output budgets and cursor or aggregate evidence offline",
    "keep full payload dumps, rebuilds, migrations, and accelerator runtime paths disabled",
    "record budget-proof notes without enabling plugin or runtime integrations",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported large graph budget proof gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported large graph budget proof gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported large graph budget proof decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LargeGraphBudgetProofGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        status: Any,
        summary: Any,
    ) -> "LargeGraphBudgetProofGate":
        return cls(
            gate_id=_normalize_gate_id(gate_id),
            status=_normalize_gate_status(status),
            summary=_normalize_text(summary, field_name="summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class LargeGraphBudgetProofDecision:
    decision: str
    next_action: str
    node_count: int
    edge_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }


@dataclass(frozen=True, slots=True)
class LargeGraphBudgetProof:
    gates: tuple[LargeGraphBudgetProofGate, ...]
    decision: LargeGraphBudgetProofDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Large Graph Budget Proof",
            "",
            f"- Decision: `{self.decision.decision}`",
            f"- Node count: `{self.decision.node_count}`",
            f"- Edge count: `{self.decision.edge_count}`",
            f"- Next action: {self.decision.next_action}",
            "",
            "## Gates",
        ]
        for gate in self.gates:
            lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_large_graph_budget_proof(
    *,
    node_count: int = 0,
    edge_count: int = 0,
    output_budget_enforced: bool = False,
    returned_nodes_within_budget: bool = False,
    returned_edges_within_budget: bool = False,
    clipping_explained: bool = False,
    cursor_or_aggregate_available: bool = False,
    no_full_payload_dump: bool = True,
    accelerator_not_required: bool = True,
    full_payload_dump_enabled: bool = False,
    ui_full_render_enabled: bool = False,
    graph_rebuild_enabled: bool = False,
    postgres_runtime_migration_enabled: bool = False,
    qdrant_enabled: bool = False,
    kuzu_enabled: bool = False,
    research_accelerator_enabled: bool = False,
    plugin_scope_touched: bool = False,
    network_enabled: bool = False,
    unsafe_evidence_logging_enabled: bool = False,
) -> LargeGraphBudgetProof:
    normalized_node_count = int(node_count)
    normalized_edge_count = int(edge_count)
    if normalized_node_count < 0 or normalized_edge_count < 0:
        raise ValueError("node_count and edge_count must be >= 0")

    unsafe_runtime_claimed = any(
        (
            full_payload_dump_enabled,
            ui_full_render_enabled,
            graph_rebuild_enabled,
            postgres_runtime_migration_enabled,
            qdrant_enabled,
            kuzu_enabled,
            research_accelerator_enabled,
            plugin_scope_touched,
            network_enabled,
            unsafe_evidence_logging_enabled,
            not no_full_payload_dump,
            not accelerator_not_required,
        )
    )
    large_graph_input_recorded = normalized_node_count > 0
    node_count_large_enough = normalized_node_count >= _MIN_LARGE_NODE_COUNT
    edge_count_recorded = normalized_edge_count > 0

    gate_map = {
        "large_graph_input_recorded": LargeGraphBudgetProofGate.create(
            gate_id="large_graph_input_recorded",
            status="go" if large_graph_input_recorded else "needs_budget_review",
            summary=(
                "large-graph input metadata is recorded for budget review"
                if large_graph_input_recorded
                else "budget review still needs recorded large-graph input metadata"
            ),
        ),
        "node_count_at_least_100k": LargeGraphBudgetProofGate.create(
            gate_id="node_count_at_least_100k",
            status="go" if node_count_large_enough else "needs_budget_review",
            summary=(
                "node-count evidence shows a 100,000+ graph input"
                if node_count_large_enough
                else "budget review still needs 100,000+ node-count evidence"
            ),
        ),
        "edge_count_recorded": LargeGraphBudgetProofGate.create(
            gate_id="edge_count_recorded",
            status="go" if edge_count_recorded else "needs_budget_review",
            summary=(
                "edge-count evidence is recorded for budget review"
                if edge_count_recorded
                else "budget review still needs recorded edge-count evidence"
            ),
        ),
        "output_budget_enforced": LargeGraphBudgetProofGate.create(
            gate_id="output_budget_enforced",
            status="go" if output_budget_enforced else "needs_budget_review",
            summary=(
                "output budget enforcement is recorded for the large-graph proof"
                if output_budget_enforced
                else "budget review still needs output-budget enforcement evidence"
            ),
        ),
        "returned_nodes_within_budget": LargeGraphBudgetProofGate.create(
            gate_id="returned_nodes_within_budget",
            status="go" if returned_nodes_within_budget else "needs_budget_review",
            summary=(
                "returned node counts stay within the declared budget"
                if returned_nodes_within_budget
                else "budget review still needs proof that returned nodes stay within budget"
            ),
        ),
        "returned_edges_within_budget": LargeGraphBudgetProofGate.create(
            gate_id="returned_edges_within_budget",
            status="go" if returned_edges_within_budget else "needs_budget_review",
            summary=(
                "returned edge counts stay within the declared budget"
                if returned_edges_within_budget
                else "budget review still needs proof that returned edges stay within budget"
            ),
        ),
        "clipping_explained": LargeGraphBudgetProofGate.create(
            gate_id="clipping_explained",
            status="go" if clipping_explained else "needs_budget_review",
            summary=(
                "clipping behavior is explained for the large-graph proof"
                if clipping_explained
                else "budget review still needs clipping explanation evidence"
            ),
        ),
        "cursor_or_aggregate_available": LargeGraphBudgetProofGate.create(
            gate_id="cursor_or_aggregate_available",
            status="go" if cursor_or_aggregate_available else "needs_budget_review",
            summary=(
                "cursor or aggregate evidence is available for large-graph continuation"
                if cursor_or_aggregate_available
                else "budget review still needs cursor or aggregate evidence"
            ),
        ),
        "no_full_payload_dump": LargeGraphBudgetProofGate.create(
            gate_id="no_full_payload_dump",
            status="blocked" if unsafe_runtime_claimed else ("go" if no_full_payload_dump else "needs_budget_review"),
            summary=(
                "full dumps, renders, rebuilds, migrations, accelerators, plugin scope, network, or unsafe logging were enabled, which blocks the proof"
                if unsafe_runtime_claimed
                else (
                    "full payload dumps remain disabled for the large-graph proof"
                    if no_full_payload_dump
                    else "budget review still needs explicit proof that no full payload dump is allowed"
                )
            ),
        ),
        "accelerator_not_required": LargeGraphBudgetProofGate.create(
            gate_id="accelerator_not_required",
            status="blocked" if unsafe_runtime_claimed else ("go" if accelerator_not_required else "needs_budget_review"),
            summary=(
                "full dumps, renders, rebuilds, migrations, accelerators, plugin scope, network, or unsafe logging were enabled, which blocks the proof"
                if unsafe_runtime_claimed
                else (
                    "accelerator and research runtime are not required for this proof"
                    if accelerator_not_required
                    else "budget review still needs explicit proof that accelerators stay optional"
                )
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only budget-proof conditions and remove full-dump, rebuild, migration, accelerator, or plugin runtime claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "budget_proof_ready"
        next_action = "perform manual review of the compact large-graph budget proof without enabling any runtime integrations"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred large-graph budget-proof inputs before review"
    else:
        decision_value = "needs_budget_review"
        next_action = "complete the remaining large-graph budget-proof evidence before review"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LargeGraphBudgetProof(
        gates=gates,
        decision=LargeGraphBudgetProofDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
            node_count=normalized_node_count,
            edge_count=normalized_edge_count,
        ),
        next_allowed_actions=next_allowed_actions,
    )
