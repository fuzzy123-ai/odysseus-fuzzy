import copy

import pytest

from src.plan_runtime import PlanRuntimeError, PlanRuntimeState


def _payload():
    return {
        "schema_version": 1,
        "plan_id": "odysseus-multiagent-roadmap",
        "title": "Odysseus Multiagent Runtime Roadmap",
        "format_decision": {"source_of_truth": "json"},
        "recommended_active_node": "planruntime-source-of-truth",
        "version_horizons": [{"id": "now"}, {"id": "v0-9"}],
        "graph_nodes": [
            {
                "id": "roadmap-reconciliation-import",
                "kind": "foundation",
                "priority_rank": 1,
                "title": "Reconcile markdown roadmaps into structured PlanRuntime seed",
                "horizon": "now",
                "target_version": "pre-0.9",
                "status": "done",
                "depends_on": [],
                "unlocks": ["planruntime-source-of-truth"],
                "gates": ["json_valid"],
                "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
                "deliverables": ["Canonical roadmap"],
                "completion_state": {"status": "live_installed"},
            },
            {
                "id": "planruntime-source-of-truth",
                "kind": "runtime",
                "priority_rank": 2,
                "title": "Build PlanRuntime as the source of truth",
                "horizon": "v0-9",
                "target_version": "0.9",
                "status": "planned",
                "depends_on": ["roadmap-reconciliation-import"],
                "unlocks": ["subagent-plan-binding"],
                "gates": ["plan_schema_validates"],
                "source_refs": ["src/plan_runtime.py"],
                "deliverables": ["PlanRuntime schema"],
            },
        ],
        "plan_graph_projection": {
            "status_mapping": {
                "done": "done",
                "planned": "pending",
                "active_candidate": "running",
            }
        },
    }


def test_runtime_loads_claimable_node_and_projects_plan_graph():
    runtime = PlanRuntimeState.from_dict(_payload())

    assert runtime.audit_summary()["source_of_truth"] == "json"
    assert runtime.next_claimable_node_id() == "planruntime-source-of-truth"

    graph = runtime.to_plan_graph()

    assert graph.plan_id == "odysseus-multiagent-roadmap"
    assert graph.audit_summary()["node_count"] == 2
    assert graph.audit_summary()["statuses"]["planruntime-source-of-truth"] == "pending"
    assert graph.audit_summary()["path_statuses"]["charlie"] == "running"


def test_claimability_requires_live_done_dependencies():
    payload = _payload()
    payload["graph_nodes"][0]["completion_state"] = {"status": "local_only"}

    runtime = PlanRuntimeState.from_dict(payload)

    assert runtime.next_claimable_node_id() == ""


def test_recommended_active_node_wins_when_claimable():
    payload = _payload()
    payload["recommended_active_node"] = "later-runtime-slice"
    payload["graph_nodes"].append(
        {
            "id": "later-runtime-slice",
            "kind": "runtime",
            "priority_rank": 3,
            "title": "Later runtime slice",
            "horizon": "v0-9",
            "target_version": "0.9",
            "status": "planned",
            "depends_on": ["roadmap-reconciliation-import"],
            "unlocks": [],
            "gates": ["runtime_gate"],
            "source_refs": ["src/plan_runtime.py"],
            "deliverables": ["Selector proof"],
        }
    )
    payload["next_actions"] = [
        {"rank": 1, "node_id": "later-runtime-slice"},
        {"rank": 2, "node_id": "planruntime-source-of-truth"},
    ]

    runtime = PlanRuntimeState.from_dict(payload)

    assert runtime.claimable_nodes()[0].node_id == "planruntime-source-of-truth"
    assert runtime.next_claimable_node_id() == "later-runtime-slice"


def test_next_actions_references_must_exist():
    payload = _payload()
    payload["next_actions"] = [{"rank": 1, "node_id": "missing-slice"}]

    with pytest.raises(PlanRuntimeError, match="next_actions reference unknown nodes"):
        PlanRuntimeState.from_dict(payload)


def test_unknown_dependency_is_rejected():
    payload = _payload()
    payload["graph_nodes"][1]["depends_on"] = ["missing-node"]

    with pytest.raises(PlanRuntimeError, match="unknown nodes"):
        PlanRuntimeState.from_dict(payload)


def test_unknown_horizon_is_rejected():
    payload = _payload()
    payload["graph_nodes"][1]["horizon"] = "later"

    with pytest.raises(PlanRuntimeError, match="unknown horizons"):
        PlanRuntimeState.from_dict(payload)


def test_current_roadmap_loads_and_projects_without_mutation():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    summary = runtime.audit_summary()
    graph = runtime.to_plan_graph()

    assert summary["source_of_truth"] == "json"
    assert summary["recommended_active_node"] in graph.audit_summary()["node_ids"]
    assert graph.audit_summary()["node_count"] == summary["node_count"]


def test_projection_revalidates_unsafe_source_refs():
    payload = _payload()
    payload["graph_nodes"][1]["source_refs"] = ["../escape.py"]

    with pytest.raises(PlanRuntimeError, match="traversal"):
        PlanRuntimeState.from_dict(payload)


def test_runtime_normalizes_trailing_slash_source_refs_for_subagent_specs():
    payload = _payload()
    payload["graph_nodes"][1]["source_refs"] = ["static/js/"]

    runtime = PlanRuntimeState.from_dict(payload)

    assert runtime.node_map()["planruntime-source-of-truth"].source_refs == ("static/js",)


def test_runtime_input_is_not_mutated():
    payload = _payload()
    before = copy.deepcopy(payload)

    PlanRuntimeState.from_dict(payload).to_plan_graph()

    assert payload == before
