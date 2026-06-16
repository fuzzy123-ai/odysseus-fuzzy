"""Persistent JSON registry for orchestration plans and agent runs.

This is an AUTO1 preparation slice: it provides deterministic JSON
save/load/export around the existing PlanGraph and AgentRun models without
running threads, tests, git commands, or schedulers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from src.agent_run_store import AgentRun
from src.plan_graph_store import AgentPath, PlanEdge, PlanGraph, PlanNode


class OrchestrationRegistryError(ValueError):
    """Raised when registry payloads or operations are invalid."""


@dataclass(slots=True)
class OrchestrationRegistry:
    plans: dict[str, PlanGraph] = field(default_factory=dict)
    runs: dict[str, AgentRun] = field(default_factory=dict)

    def add_plan(self, plan: PlanGraph) -> None:
        if not isinstance(plan, PlanGraph):
            raise OrchestrationRegistryError("plan must be a PlanGraph")
        if plan.plan_id in self.plans:
            raise OrchestrationRegistryError(f"plan already exists: {plan.plan_id}")
        self.plans[plan.plan_id] = plan

    def add_run(self, run: AgentRun) -> None:
        if not isinstance(run, AgentRun):
            raise OrchestrationRegistryError("run must be an AgentRun")
        if run.plan_id not in self.plans:
            raise OrchestrationRegistryError(f"run references unknown plan: {run.plan_id}")
        if run.agent_run_id in self.runs:
            raise OrchestrationRegistryError(f"run already exists: {run.agent_run_id}")
        self.runs[run.agent_run_id] = run

    def runs_for_plan(self, plan_id: str) -> tuple[AgentRun, ...]:
        return tuple(sorted((run for run in self.runs.values() if run.plan_id == plan_id), key=lambda run: run.agent_run_id))

    def audit_summary(self) -> dict[str, Any]:
        return {
            "plan_count": len(self.plans),
            "run_count": len(self.runs),
            "plans": [self.plans[plan_id].audit_summary() for plan_id in sorted(self.plans)],
            "runs": [self.runs[run_id].audit_summary() for run_id in sorted(self.runs)],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plans": [_plan_to_dict(self.plans[plan_id]) for plan_id in sorted(self.plans)],
            "runs": [_run_to_dict(self.runs[run_id]) for run_id in sorted(self.runs)],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OrchestrationRegistry":
        if not isinstance(payload, dict):
            raise OrchestrationRegistryError("payload must be a dict")
        if payload.get("schema_version") != 1:
            raise OrchestrationRegistryError("schema_version must be 1")
        registry = cls()
        for plan_payload in _list(payload.get("plans"), field_name="plans"):
            registry.add_plan(_plan_from_dict(plan_payload))
        for run_payload in _list(payload.get("runs"), field_name="runs"):
            registry.add_run(_run_from_dict(run_payload))
        return registry

    def save_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> "OrchestrationRegistry":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _plan_to_dict(plan: PlanGraph) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "title": plan.title,
        "nodes": [
            {
                "node_id": node.node_id,
                "slice_id": node.slice_id,
                "title": node.title,
                "owner": node.owner,
                "status": node.status.value,
                "allowed_files": list(node.allowed_files),
                "blocked_files": list(node.blocked_files),
                "evidence_required": list(node.evidence_required),
            }
            for node in plan.nodes
        ],
        "edges": [
            {
                "from_node": edge.from_node,
                "to_node": edge.to_node,
                "kind": edge.kind.value,
            }
            for edge in plan.edges
        ],
        "agent_paths": [
            {
                "agent_id": path.agent_id,
                "node_ids": list(path.node_ids),
                "status": path.status.value,
            }
            for path in plan.agent_paths
        ],
    }


def _plan_from_dict(payload: dict[str, Any]) -> PlanGraph:
    return PlanGraph.create(
        plan_id=_required(payload, "plan_id"),
        title=_required(payload, "title"),
        nodes=[
            PlanNode.create(
                node_id=_required(node, "node_id"),
                slice_id=_required(node, "slice_id"),
                title=_required(node, "title"),
                owner=_required(node, "owner"),
                status=_required(node, "status"),
                allowed_files=_list(node.get("allowed_files"), field_name="allowed_files"),
                blocked_files=_list(node.get("blocked_files", []), field_name="blocked_files"),
                evidence_required=_list(node.get("evidence_required", []), field_name="evidence_required"),
            )
            for node in _list(payload.get("nodes"), field_name="nodes")
        ],
        edges=[
            PlanEdge.create(
                from_node=_required(edge, "from_node"),
                to_node=_required(edge, "to_node"),
                kind=_required(edge, "kind"),
            )
            for edge in _list(payload.get("edges", []), field_name="edges")
        ],
        agent_paths=[
            AgentPath.create(
                agent_id=_required(path, "agent_id"),
                node_ids=_list(path.get("node_ids"), field_name="node_ids"),
                status=_required(path, "status"),
            )
            for path in _list(payload.get("agent_paths"), field_name="agent_paths")
        ],
    )


def _run_to_dict(run: AgentRun) -> dict[str, Any]:
    return {
        "agent_run_id": run.agent_run_id,
        "plan_id": run.plan_id,
        "node_id": run.node_id,
        "slice_id": run.slice_id,
        "agent_id": run.agent_id,
        "role_id": run.role_id,
        "model": run.model,
        "thinking": run.thinking,
        "status": run.status.value,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "changed_files": list(run.evidence.changed_files),
        "tests": list(run.evidence.tests),
        "commit": run.evidence.commit,
        "warnings": list(run.warnings),
        "errors": list(run.errors),
        "blocker": run.blocker,
        "next_action": run.next_action,
        "evidence": list(run.evidence.evidence),
    }


def _run_from_dict(payload: dict[str, Any]) -> AgentRun:
    return AgentRun.create(
        agent_run_id=_required(payload, "agent_run_id"),
        plan_id=_required(payload, "plan_id"),
        node_id=_required(payload, "node_id"),
        slice_id=_required(payload, "slice_id"),
        agent_id=_required(payload, "agent_id"),
        role_id=_required(payload, "role_id"),
        model=_required(payload, "model"),
        thinking=_required(payload, "thinking"),
        status=_required(payload, "status"),
        started_at=_required(payload, "started_at"),
        completed_at=payload.get("completed_at", ""),
        changed_files=_list(payload.get("changed_files", []), field_name="changed_files"),
        tests=_list(payload.get("tests", []), field_name="tests"),
        commit=payload.get("commit", ""),
        warnings=_list(payload.get("warnings", []), field_name="warnings"),
        errors=_list(payload.get("errors", []), field_name="errors"),
        blocker=payload.get("blocker", ""),
        next_action=payload.get("next_action", ""),
        evidence=_list(payload.get("evidence", []), field_name="evidence"),
    )


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise OrchestrationRegistryError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        raise OrchestrationRegistryError(f"{field_name} must be a list")
    if not isinstance(value, list):
        raise OrchestrationRegistryError(f"{field_name} must be a list")
    return value
