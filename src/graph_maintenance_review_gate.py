"""Read-only review gate model for graph maintenance evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_ID = "graph_maintenance_review_gate"
_DECISIONS = (
    "graph_review_gate_ready",
    "needs_review_gate_evidence",
    "blocked",
    "deferred",
)
_STATUSES = (
    "go",
    "blocked",
    "needs_review_gate_evidence",
    "deferred",
)
_DEFAULT_ACTIONS = (
    "review graph-maintenance evidence, provenance, and rollback notes manually",
    "confirm bounded batch and operator next-action evidence offline",
    "keep truth-write, rebuild, fullbuild, migration, and accelerator runtime paths disabled",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISIONS:
        raise ValueError("unsupported graph maintenance review gate decision")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _STATUSES:
        raise ValueError("unsupported graph maintenance review gate status")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class GraphMaintenanceReviewGate:
    gate_id: str
    decision: str
    status: str
    summary: str
    candidate_count: int
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "decision": self.decision,
            "status": self.status,
            "summary": self.summary,
            "candidate_count": self.candidate_count,
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Graph Maintenance Review Gate",
            "",
            f"- Gate: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Status: `{self.status}`",
            f"- Candidate count: `{self.candidate_count}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_graph_maintenance_review_gate(
    *,
    maintenance_job_recorded: bool = False,
    candidate_count_recorded: bool = False,
    provenance_recorded: bool = False,
    review_required: bool = False,
    truth_write_disabled: bool = True,
    bounded_batch_enforced: bool = False,
    rollback_plan_recorded: bool = False,
    operator_next_action_recorded: bool = False,
    candidate_count: int = 0,
    truth_write_enabled: bool = False,
    unbounded_maintenance_enabled: bool = False,
    graph_rebuild_enabled: bool = False,
    raptor_fullbuild_enabled: bool = False,
    postgres_runtime_migration_enabled: bool = False,
    qdrant_enabled: bool = False,
    kuzu_enabled: bool = False,
    research_accelerator_enabled: bool = False,
    plugin_scope_touched: bool = False,
    network_enabled: bool = False,
    unsafe_evidence_logging_enabled: bool = False,
) -> GraphMaintenanceReviewGate:
    normalized_candidate_count = int(candidate_count)
    if normalized_candidate_count < 0:
        raise ValueError("candidate_count must be >= 0")

    unsafe_runtime_claimed = any(
        (
            truth_write_enabled,
            unbounded_maintenance_enabled,
            graph_rebuild_enabled,
            raptor_fullbuild_enabled,
            postgres_runtime_migration_enabled,
            qdrant_enabled,
            kuzu_enabled,
            research_accelerator_enabled,
            plugin_scope_touched,
            network_enabled,
            unsafe_evidence_logging_enabled,
            not truth_write_disabled,
        )
    )

    all_positive_gates = all(
        (
            maintenance_job_recorded,
            candidate_count_recorded,
            provenance_recorded,
            review_required,
            truth_write_disabled,
            bounded_batch_enforced,
            rollback_plan_recorded,
            operator_next_action_recorded,
        )
    )

    if unsafe_runtime_claimed:
        decision = "blocked"
        status = "blocked"
        summary = "truth-write, unbounded maintenance, rebuild, fullbuild, migration, accelerator, plugin, network, or unsafe logging behavior was enabled"
    elif all_positive_gates:
        decision = "graph_review_gate_ready"
        status = "go"
        summary = "graph maintenance review evidence is complete with bounded batch, provenance, rollback, and operator next action recorded"
    elif any(
        value is None
        for value in (
            maintenance_job_recorded,
            candidate_count_recorded,
            provenance_recorded,
            review_required,
            bounded_batch_enforced,
            rollback_plan_recorded,
            operator_next_action_recorded,
        )
    ):
        decision = "deferred"
        status = "deferred"
        summary = "graph maintenance review evidence is deferred until incomplete gate signals are provided"
    else:
        decision = "needs_review_gate_evidence"
        status = "needs_review_gate_evidence"
        summary = "graph maintenance review evidence still needs maintenance, provenance, rollback, or bounded-batch proof"

    next_allowed_actions = (
        ()
        if decision == "blocked"
        else _normalize_tuple(_DEFAULT_ACTIONS, field_name="next_allowed_action")
    )

    return GraphMaintenanceReviewGate(
        gate_id=_GATE_ID,
        decision=_normalize_decision(decision),
        status=_normalize_status(status),
        summary=summary,
        candidate_count=normalized_candidate_count,
        next_allowed_actions=next_allowed_actions,
    )
