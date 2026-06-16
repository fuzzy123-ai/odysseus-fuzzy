import json

import pytest

from src.agent_run_store import AgentRun
from src.orchestration_registry import OrchestrationRegistry, OrchestrationRegistryError
from src.plan_graph_store import AgentPath, PlanEdge, PlanGraph, PlanNode


def _node(node_id: str, *, status: str = "pending") -> PlanNode:
    return PlanNode.create(
        node_id=node_id,
        slice_id=node_id,
        title=f"{node_id} title",
        owner="bob",
        status=status,
        allowed_files=[f"src/{node_id}.py"],
        blocked_files=[],
        evidence_required=["green test"],
    )


def _plan() -> PlanGraph:
    return PlanGraph.create(
        plan_id="auto1-plan",
        title="AUTO1 Registry Plan",
        nodes=[_node("node-a", status="done"), _node("node-b")],
        edges=[PlanEdge.create(from_node="node-a", to_node="node-b", kind="handoff_to")],
        agent_paths=[AgentPath.create(agent_id="bob", node_ids=["node-a", "node-b"], status="running")],
    )


def _run() -> AgentRun:
    return AgentRun.create(
        agent_run_id="run-a",
        plan_id="auto1-plan",
        node_id="node-a",
        slice_id="node-a",
        agent_id="bob",
        role_id="backend",
        model="gpt-5.4",
        thinking="medium",
        status="done",
        started_at="2026-06-16T12:00:00Z",
        completed_at="2026-06-16T12:05:00Z",
        changed_files=["src/node-a.py"],
        tests=["pytest tests/test_orchestration_registry.py"],
        commit="abcdef1",
        warnings=[],
        errors=[],
        blocker="",
        next_action="handoff to charlie",
        evidence=["green test"],
    )


def test_registry_roundtrips_plan_and_run_through_json_file(tmp_path):
    registry = OrchestrationRegistry()
    registry.add_plan(_plan())
    registry.add_run(_run())

    path = tmp_path / "registry.json"
    registry.save_json(path)
    loaded = OrchestrationRegistry.load_json(path)

    assert loaded.audit_summary()["plan_count"] == 1
    assert loaded.audit_summary()["run_count"] == 1
    assert loaded.plans["auto1-plan"].audit_summary()["node_count"] == 2
    assert loaded.runs["run-a"].audit_summary()["commit"] == "abcdef1"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_registry_export_is_deterministically_sorted():
    registry = OrchestrationRegistry()
    registry.add_plan(_plan())
    registry.add_run(_run())

    payload = registry.to_dict()

    assert payload["plans"][0]["plan_id"] == "auto1-plan"
    assert payload["plans"][0]["nodes"][0]["node_id"] == "node-a"
    assert payload["runs"][0]["agent_run_id"] == "run-a"


def test_run_referencing_unknown_plan_is_rejected():
    registry = OrchestrationRegistry()

    with pytest.raises(OrchestrationRegistryError, match="unknown plan"):
        registry.add_run(_run())


def test_duplicate_plan_is_rejected():
    registry = OrchestrationRegistry()
    registry.add_plan(_plan())

    with pytest.raises(OrchestrationRegistryError, match="plan already exists"):
        registry.add_plan(_plan())


def test_duplicate_run_is_rejected():
    registry = OrchestrationRegistry()
    registry.add_plan(_plan())
    registry.add_run(_run())

    with pytest.raises(OrchestrationRegistryError, match="run already exists"):
        registry.add_run(_run())


def test_load_revalidates_plan_payloads():
    payload = OrchestrationRegistry().to_dict()
    payload["plans"].append(
        {
            "plan_id": "bad",
            "title": "Bad Plan",
            "nodes": [
                {
                    "node_id": "node-a",
                    "slice_id": "node-a",
                    "title": "Node A",
                    "owner": "bob",
                    "status": "pending",
                    "allowed_files": ["../escape.py"],
                    "blocked_files": [],
                    "evidence_required": [],
                }
            ],
            "edges": [],
            "agent_paths": [{"agent_id": "bob", "node_ids": ["node-a"], "status": "pending"}],
        }
    )

    with pytest.raises(Exception, match="repo-relative|traversal"):
        OrchestrationRegistry.from_dict(payload)


def test_schema_version_must_match():
    with pytest.raises(OrchestrationRegistryError, match="schema_version must be 1"):
        OrchestrationRegistry.from_dict({"schema_version": 2, "plans": [], "runs": []})
