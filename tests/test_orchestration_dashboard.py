from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.orchestration_dashboard import build_orchestration_dashboard_snapshot
from src.plan_runtime import PlanRuntimeState


def test_current_roadmap_dashboard_snapshot_is_compact_and_actionable():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    snapshot = build_orchestration_dashboard_snapshot(
        runtime,
        last_updated_at="2026-06-21T10:30:00+00:00",
    )
    payload = snapshot.to_dict()
    expected_status = "healthy" if runtime.next_claimable_node_id() else "completed"

    assert payload["dashboard_id"] == "odysseus-multiagent-roadmap-dashboard"
    assert payload["plan_status"] == expected_status
    assert payload["active_node_id"] == runtime.recommended_active_node
    assert payload["next_claimable_node_id"] == runtime.next_claimable_node_id()
    assert payload["progress_percent"] > 0
    assert payload["counts"]["completed_nodes"] >= 12
    assert payload["agent_paths"][0]["agent_id"] == "charlie"
    assert payload["heartbeat_status"]["automation"] == "active"
    assert payload["quality_gates"]["claimed_done_without_verified_done_count"] == 0
    assert payload["controls"]["pause"]["state"] == "policy_gated"
    if runtime.next_claimable_node_id():
        assert any(action["node_id"] == runtime.next_claimable_node_id() for action in payload["next_actions"])
    else:
        assert all(action["status"] == "waiting" for action in payload["next_actions"])
    assert all("ref" in evidence for evidence in payload["evidence_refs"])


def test_roadmap_dashboard_route_returns_admin_snapshot(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).get("/api/roadmap/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dashboard_id"] == "odysseus-multiagent-roadmap-dashboard"
    assert payload["next_claimable_node_id"] == runtime.next_claimable_node_id()
    assert payload["controls"]["retry"]["reason"] == "dashboard v1 is read-only"
