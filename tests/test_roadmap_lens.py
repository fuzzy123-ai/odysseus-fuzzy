from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plan_runtime import PlanRuntimeState
from src.progressive_graph_api import GraphQueryBudget
from src.roadmap_lens import build_roadmap_lens_page
from routes.roadmap_routes import setup_roadmap_routes


def test_current_roadmap_lens_page_highlights_active_and_claimable_nodes():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    lens = build_roadmap_lens_page(runtime)
    payload = lens.to_dict()

    assert payload["status"] == "complete"
    assert payload["active_node_id"] == runtime.recommended_active_node
    next_claimable = runtime.next_claimable_node_id()
    if next_claimable:
        assert next_claimable in payload["claimable_node_ids"]
    else:
        assert payload["claimable_node_ids"] == ()
    assert any(node["node_type"] == "version-horizon" and node["label"] == "v0-9" for node in payload["nodes"])
    assert any(node["node_type"] == "gate" for node in payload["nodes"])
    assert any(node["node_type"] == "commit" and node["label"] == "c92b143d" for node in payload["nodes"])
    active_title = runtime.node_map()[runtime.recommended_active_node].title
    active_nodes = [
        node
        for node in payload["nodes"]
        if node["label"] == active_title
    ]
    assert active_nodes and active_nodes[0]["score"] == 1.0


def test_roadmap_lens_budget_clips_without_edges_to_missing_nodes():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    budget = GraphQueryBudget.create(
        limit=4,
        max_nodes=4,
        max_edges=3,
        depth=1,
        max_hops=0,
        time_budget_ms=100,
        payload_budget_bytes=2048,
    )

    lens = build_roadmap_lens_page(runtime, budget=budget)
    payload = lens.to_dict()
    node_ids = {node["node_id"] for node in payload["nodes"]}

    assert payload["status"] == "clipped"
    assert payload["clipped"] is True
    assert len(payload["nodes"]) <= 4
    assert len(payload["edges"]) <= 3
    assert all(edge["source_id"] in node_ids and edge["target_id"] in node_ids for edge in payload["edges"])


def test_roadmap_graph_route_returns_readonly_snapshot(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).get("/api/roadmap/graph")

    assert response.status_code == 200
    payload = response.json()
    assert payload["graph_ref"] == "odysseus-roadmap-lens"
    assert payload["active_node_id"] == runtime.recommended_active_node
