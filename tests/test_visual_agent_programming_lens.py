from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.plan_runtime import PlanRuntimeState
from src.visual_agent_programming_lens import build_visual_agent_programming_snapshot


def test_visual_agent_programming_snapshot_is_read_only_and_claimable():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    snapshot = build_visual_agent_programming_snapshot(
        runtime,
        last_updated_at="2026-06-21T11:15:00+00:00",
    )
    payload = snapshot.to_dict()

    assert payload["mode"] == "read_only"
    assert payload["active_node_id"] == "visual-agent-programming-readonly-lens"
    assert payload["next_claimable_node_id"] == "visual-agent-programming-readonly-lens"
    assert any(node["node_id"] == "visual-agent-programming-readonly-lens" and node["claimable"] for node in payload["nodes"])
    assert any(node["node_id"] == "visual-agent-programming-plan-edit-validator" for node in payload["nodes"])
    assert all(control["state"] == "policy_gated" for control in payload["controls"].values())
    assert {item["action"] for item in payload["blocked_actions"]} >= {
        "mutate_graph",
        "start_agent",
        "accept_report_directly",
    }
    assert payload["context_policy"]["mutation_mode"] == "read_only"


def test_visual_agent_programming_snapshot_projects_future_version_layers():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    payload = build_visual_agent_programming_snapshot(
        runtime,
        last_updated_at="2026-06-21T11:15:00+00:00",
    ).to_dict()

    assert payload["progress"]["branch_nodes"] == 3
    assert any(layer["target_version"] == "0.10" for layer in payload["version_layers"])
    assert any(layer["target_version"] == "future" for layer in payload["version_layers"])
    assert [step["node_id"] for step in payload["next_steps"]][:2] == [
        "visual-agent-programming-readonly-lens",
        "visual-agent-programming-plan-edit-validator",
    ]
    assert payload["next_steps"][0]["state"] == "claimable"
    assert payload["next_steps"][1]["state"] == "waiting"


def test_visual_agent_programming_route_returns_admin_snapshot(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).get("/api/roadmap/visual-agent-programming")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot_id"] == "odysseus-multiagent-roadmap-visual-agent-programming"
    assert payload["controls"]["start_run"]["state"] == "policy_gated"
    assert payload["context_policy"]["agent_write_mode"] == "reports_only_until_reducer_accepts_events"
