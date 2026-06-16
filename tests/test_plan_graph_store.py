import pytest

from src.plan_graph_store import (
    AgentPath,
    AgentPathStatus,
    PlanEdge,
    PlanEdgeKind,
    PlanGraph,
    PlanGraphStoreError,
    PlanNode,
    PlanNodeStatus,
    validate_status_transition,
)


def _node(node_id: str, *, allowed_files: list[str], status: str = "pending") -> PlanNode:
    return PlanNode.create(
        node_id=node_id,
        slice_id=node_id,
        title=f"{node_id} title",
        owner="bob",
        status=status,
        allowed_files=allowed_files,
        blocked_files=[],
        evidence_required=["green test"],
    )


def test_valid_plan_normalizes_stably():
    graph = PlanGraph.create(
        plan_id=" OR1 Plan ",
        title="  Development orchestration graph  ",
        nodes=[
            _node("node-b", allowed_files=["tests/test_plan_graph_store.py"]),
            _node("node-a", allowed_files=["src/plan_graph_store.py"]),
        ],
        edges=[PlanEdge.create(from_node="node-a", to_node="node-b", kind="handoff_to")],
        agent_paths=[
            AgentPath.create(agent_id="bob", node_ids=["node-a"], status="running"),
            AgentPath.create(agent_id="alice", node_ids=["node-b"], status="pending"),
        ],
    )

    assert graph.plan_id == "or1-plan"
    assert graph.title == "Development orchestration graph"
    assert [node.node_id for node in graph.nodes] == ["node-a", "node-b"]
    assert graph.edges[0].kind == PlanEdgeKind.HANDOFF_TO
    assert graph.agent_paths[0].agent_id == "alice"


def test_unknown_edge_targets_are_rejected():
    with pytest.raises(PlanGraphStoreError):
        PlanGraph.create(
            plan_id="plan",
            title="Graph",
            nodes=[_node("node-a", allowed_files=["src/plan_graph_store.py"])],
            edges=[PlanEdge.create(from_node="node-a", to_node="node-b", kind="depends_on")],
            agent_paths=[AgentPath.create(agent_id="bob", node_ids=["node-a"], status="pending")],
        )


def test_depends_on_cycles_are_rejected():
    with pytest.raises(PlanGraphStoreError):
        PlanGraph.create(
            plan_id="plan",
            title="Graph",
            nodes=[
                _node("node-a", allowed_files=["src/plan_graph_store.py"]),
                _node("node-b", allowed_files=["tests/test_plan_graph_store.py"]),
            ],
            edges=[
                PlanEdge.create(from_node="node-a", to_node="node-b", kind="depends_on"),
                PlanEdge.create(from_node="node-b", to_node="node-a", kind="depends_on"),
            ],
            agent_paths=[
                AgentPath.create(agent_id="bob", node_ids=["node-a"], status="pending"),
                AgentPath.create(agent_id="alice", node_ids=["node-b"], status="pending"),
            ],
        )


def test_parallel_allowed_file_collision_requires_barrier():
    with pytest.raises(PlanGraphStoreError) as exc:
        PlanGraph.create(
            plan_id="plan",
            title="Graph",
            nodes=[
                _node("node-a", allowed_files=["src/shared.py"]),
                _node("node-b", allowed_files=["src/shared.py"]),
            ],
            edges=[],
            agent_paths=[
                AgentPath.create(agent_id="bob", node_ids=["node-a"], status="running"),
                AgentPath.create(agent_id="alice", node_ids=["node-b"], status="running"),
            ],
        )

    assert "share allowed files without barrier" in str(exc.value)


def test_status_transitions_are_validated():
    assert validate_status_transition("pending", "running") is True
    assert validate_status_transition(PlanNodeStatus.RUNNING, "done") is True
    assert validate_status_transition("blocked", "handoff") is True
    assert validate_status_transition("running", "failed") is True
    assert validate_status_transition("pending", "skipped") is True
    assert validate_status_transition("done", "running") is False
    assert validate_status_transition("skipped", "running") is False
    assert validate_status_transition("pending", "done") is False


def test_failed_and_skipped_statuses_match_contract():
    failed = _node("node-failed", allowed_files=["src/failed.py"], status="failed")
    skipped_path = AgentPath.create(agent_id="alice", node_ids=["node-failed"], status="skipped")

    assert failed.status == PlanNodeStatus.FAILED
    assert skipped_path.status == AgentPathStatus.SKIPPED


def test_audit_summary_keeps_ids_counts_and_statuses_without_long_dumps():
    graph = PlanGraph.create(
        plan_id="plan",
        title="Graph",
        nodes=[
            _node("node-a", allowed_files=["src/plan_graph_store.py"], status="running"),
            _node("node-b", allowed_files=["tests/test_plan_graph_store.py"], status="handoff"),
        ],
        edges=[PlanEdge.create(from_node="node-a", to_node="node-b", kind="handoff_to")],
        agent_paths=[
            AgentPath.create(agent_id="bob", node_ids=["node-a"], status="running"),
            AgentPath.create(agent_id="alice", node_ids=["node-b"], status="handoff"),
        ],
    )

    summary = graph.audit_summary()

    assert summary["plan_id"] == "plan"
    assert summary["node_count"] == 2
    assert summary["edge_count"] == 1
    assert summary["agent_path_count"] == 2
    assert summary["statuses"]["node-a"] == "running"
    assert summary["path_statuses"]["alice"] == "handoff"
    assert "green test" not in repr(summary)
