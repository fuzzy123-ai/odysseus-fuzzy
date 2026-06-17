"""Read-only release evidence map for graph-memory and RAPTOR foundations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "progressive_graph_api_recorded",
    "query_budgets_recorded",
    "derived_cluster_runs_recorded",
    "summary_worker_recorded",
    "graph_maintenance_worker_recorded",
    "small_model_evaluation_gates_recorded",
    "fallback_routing_recorded",
    "provenance_required",
    "review_required",
    "truth_write_disabled",
    "unbounded_fullbuild_disabled",
    "accelerator_optional_post_release",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_release_review",
    "deferred",
)

_DECISION_VALUES = (
    "evidence_map_ready",
    "needs_release_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review graph-memory evidence records and provenance requirements manually",
    "confirm budget, worker, and fallback proofs offline",
    "keep truth-write, fullbuild, rebuild, migration, and accelerator runtime paths disabled",
    "record release-review notes without enabling plugin or runtime integrations",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported graph memory release evidence gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported graph memory release evidence gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported graph memory release evidence decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class GraphMemoryReleaseEvidenceGate:
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
    ) -> "GraphMemoryReleaseEvidenceGate":
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
class GraphMemoryReleaseEvidenceDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class GraphMemoryReleaseEvidenceMap:
    gates: tuple[GraphMemoryReleaseEvidenceGate, ...]
    decision: GraphMemoryReleaseEvidenceDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Graph Memory Release Evidence Map",
            "",
            f"- Decision: `{self.decision.decision}`",
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


def build_graph_memory_release_evidence_map(
    *,
    progressive_graph_api_recorded: bool = False,
    query_budgets_recorded: bool = False,
    derived_cluster_runs_recorded: bool = False,
    summary_worker_recorded: bool = False,
    graph_maintenance_worker_recorded: bool = False,
    small_model_evaluation_gates_recorded: bool = False,
    fallback_routing_recorded: bool = False,
    provenance_required: bool = True,
    review_required: bool = True,
    truth_write_disabled: bool = True,
    unbounded_fullbuild_disabled: bool = True,
    accelerator_optional_post_release: bool = True,
    truth_write_enabled: bool = False,
    unbounded_fullbuild_enabled: bool = False,
    graph_rebuild_enabled: bool = False,
    postgres_runtime_migration_enabled: bool = False,
    qdrant_enabled: bool = False,
    kuzu_enabled: bool = False,
    research_accelerator_enabled: bool = False,
    plugin_scope_touched: bool = False,
    unsafe_evidence_logging_enabled: bool = False,
) -> GraphMemoryReleaseEvidenceMap:
    unsafe_runtime_claimed = any(
        (
            truth_write_enabled,
            unbounded_fullbuild_enabled,
            graph_rebuild_enabled,
            postgres_runtime_migration_enabled,
            qdrant_enabled,
            kuzu_enabled,
            research_accelerator_enabled,
            plugin_scope_touched,
            unsafe_evidence_logging_enabled,
            not truth_write_disabled,
            not unbounded_fullbuild_disabled,
            not accelerator_optional_post_release,
            not provenance_required,
            not review_required,
        )
    )

    gate_map = {
        "progressive_graph_api_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="progressive_graph_api_recorded",
            status="go" if progressive_graph_api_recorded else "needs_release_review",
            summary=(
                "progressive graph API evidence is recorded for release review"
                if progressive_graph_api_recorded
                else "release review still needs the progressive graph API evidence record"
            ),
        ),
        "query_budgets_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="query_budgets_recorded",
            status="go" if query_budgets_recorded else "needs_release_review",
            summary=(
                "query budget evidence is recorded for release review"
                if query_budgets_recorded
                else "release review still needs the query budget evidence record"
            ),
        ),
        "derived_cluster_runs_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="derived_cluster_runs_recorded",
            status="go" if derived_cluster_runs_recorded else "needs_release_review",
            summary=(
                "derived cluster run evidence is recorded for release review"
                if derived_cluster_runs_recorded
                else "release review still needs the derived cluster run evidence record"
            ),
        ),
        "summary_worker_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="summary_worker_recorded",
            status="go" if summary_worker_recorded else "needs_release_review",
            summary=(
                "summary worker evidence is recorded for release review"
                if summary_worker_recorded
                else "release review still needs the summary worker evidence record"
            ),
        ),
        "graph_maintenance_worker_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="graph_maintenance_worker_recorded",
            status="go" if graph_maintenance_worker_recorded else "needs_release_review",
            summary=(
                "graph maintenance worker evidence is recorded for release review"
                if graph_maintenance_worker_recorded
                else "release review still needs the graph maintenance worker evidence record"
            ),
        ),
        "small_model_evaluation_gates_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="small_model_evaluation_gates_recorded",
            status="go" if small_model_evaluation_gates_recorded else "needs_release_review",
            summary=(
                "small-model evaluation gate evidence is recorded for release review"
                if small_model_evaluation_gates_recorded
                else "release review still needs the small-model evaluation gate evidence record"
            ),
        ),
        "fallback_routing_recorded": GraphMemoryReleaseEvidenceGate.create(
            gate_id="fallback_routing_recorded",
            status="go" if fallback_routing_recorded else "needs_release_review",
            summary=(
                "fallback routing evidence is recorded for release review"
                if fallback_routing_recorded
                else "release review still needs the fallback routing evidence record"
            ),
        ),
        "provenance_required": GraphMemoryReleaseEvidenceGate.create(
            gate_id="provenance_required",
            status="blocked" if unsafe_runtime_claimed else ("go" if provenance_required else "needs_release_review"),
            summary=(
                "unsafe runtime, rebuild, migration, accelerator, plugin, or logging behavior was enabled, which blocks the evidence map"
                if unsafe_runtime_claimed
                else (
                    "provenance remains required for graph-memory release review"
                    if provenance_required
                    else "release review still needs explicit provenance requirements"
                )
            ),
        ),
        "review_required": GraphMemoryReleaseEvidenceGate.create(
            gate_id="review_required",
            status="blocked" if unsafe_runtime_claimed else ("go" if review_required else "needs_release_review"),
            summary=(
                "unsafe runtime, rebuild, migration, accelerator, plugin, or logging behavior was enabled, which blocks the evidence map"
                if unsafe_runtime_claimed
                else (
                    "review remains required for graph-memory release review"
                    if review_required
                    else "release review still needs an explicit review requirement"
                )
            ),
        ),
        "truth_write_disabled": GraphMemoryReleaseEvidenceGate.create(
            gate_id="truth_write_disabled",
            status="blocked" if unsafe_runtime_claimed else ("go" if truth_write_disabled else "needs_release_review"),
            summary=(
                "truth-write, rebuild, migration, accelerator, plugin, or unsafe logging behavior was enabled, which blocks the evidence map"
                if unsafe_runtime_claimed
                else (
                    "truth-write remains disabled during graph-memory release review"
                    if truth_write_disabled
                    else "release review still needs truth-write disabled explicitly"
                )
            ),
        ),
        "unbounded_fullbuild_disabled": GraphMemoryReleaseEvidenceGate.create(
            gate_id="unbounded_fullbuild_disabled",
            status="blocked" if unsafe_runtime_claimed else ("go" if unbounded_fullbuild_disabled else "needs_release_review"),
            summary=(
                "fullbuild, rebuild, migration, accelerator, plugin, or unsafe logging behavior was enabled, which blocks the evidence map"
                if unsafe_runtime_claimed
                else (
                    "unbounded fullbuild remains disabled during graph-memory release review"
                    if unbounded_fullbuild_disabled
                    else "release review still needs unbounded fullbuild disabled explicitly"
                )
            ),
        ),
        "accelerator_optional_post_release": GraphMemoryReleaseEvidenceGate.create(
            gate_id="accelerator_optional_post_release",
            status="blocked" if unsafe_runtime_claimed else ("go" if accelerator_optional_post_release else "needs_release_review"),
            summary=(
                "accelerator or research runtime behavior was enabled, which blocks the evidence map"
                if unsafe_runtime_claimed
                else (
                    "accelerator tracks remain optional and post-release"
                    if accelerator_optional_post_release
                    else "release review still needs accelerator tracks kept optional post-release"
                )
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only release evidence conditions and remove truth-write, rebuild, migration, plugin, or accelerator runtime claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "evidence_map_ready"
        next_action = "perform manual release review of the evidence map without enabling any graph-memory runtime behavior"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred graph-memory release evidence inputs before review"
    else:
        decision_value = "needs_release_review"
        next_action = "complete the remaining graph-memory release evidence records before review"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return GraphMemoryReleaseEvidenceMap(
        gates=gates,
        decision=GraphMemoryReleaseEvidenceDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )
