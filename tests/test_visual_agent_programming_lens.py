from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.plan_runtime import PlanRuntimeState
from src.visual_agent_programming_lens import (
    build_visual_agent_programming_snapshot,
    build_visual_plan_proposal_queue,
    validate_visual_plan_edit,
)


def test_visual_agent_programming_snapshot_is_read_only_and_claimable():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    snapshot = build_visual_agent_programming_snapshot(
        runtime,
        last_updated_at="2026-06-21T11:15:00+00:00",
    )
    payload = snapshot.to_dict()

    assert payload["mode"] == "read_only"
    assert payload["active_node_id"] == "visual-agent-programming-proposal-review-queue"
    assert payload["next_claimable_node_id"] == "visual-agent-programming-proposal-review-queue"
    assert any(node["node_id"] == "visual-agent-programming-readonly-lens" and node["live_done"] for node in payload["nodes"])
    assert any(
        node["node_id"] == "visual-agent-programming-plan-edit-validator" and node["live_done"]
        for node in payload["nodes"]
    )
    assert any(
        node["node_id"] == "visual-agent-programming-proposal-review-queue" and node["claimable"]
        for node in payload["nodes"]
    )
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

    assert payload["progress"]["branch_nodes"] == 4
    assert any(layer["target_version"] == "0.10" for layer in payload["version_layers"])
    assert any(layer["target_version"] == "future" for layer in payload["version_layers"])
    assert [step["node_id"] for step in payload["next_steps"]] == [
        "visual-agent-programming-proposal-review-queue",
        "visual-agent-programming",
    ]
    assert payload["next_steps"][0]["state"] == "claimable"
    assert payload["next_steps"][1]["state"] == "research"


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


def test_visual_plan_edit_validator_accepts_safe_create_node_as_dry_run_only():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = validate_visual_plan_edit(
        runtime,
        {
            "action": "create_node",
            "node_id": "visual-agent-programming-dry-run-probe",
            "title": "Dry-run probe",
            "kind": "runtime",
            "horizon": "later",
            "target_version": "future",
            "status": "planned",
            "depends_on": ["visual-agent-programming-plan-edit-validator"],
            "gates": ["operator_go_required"],
            "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
            "deliverables": ["Dry-run validator proof"],
        },
    ).to_dict()

    assert result["state"] == "valid_dry_run"
    assert result["valid"] is True
    assert result["can_write"] is False
    assert result["can_start_agent"] is False
    assert result["accepted_events"] == []
    assert result["proposed_events"][0]["type"] == "plan_node_proposed"


def test_visual_plan_edit_validator_rejects_duplicate_node_collision():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = validate_visual_plan_edit(
        runtime,
        {
            "action": "create_node",
            "node_id": "visual-agent-programming-plan-edit-validator",
            "title": "Duplicate",
        },
    ).to_dict()

    assert result["state"] == "rejected"
    assert result["valid"] is False
    assert result["collisions"][0]["code"] == "node_exists"
    assert result["proposed_events"] == []


def test_visual_plan_edit_validator_rejects_dependency_cycles():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = validate_visual_plan_edit(
        runtime,
        {
            "action": "connect_dependency",
            "from_node": "visual-agent-programming",
            "to_node": "visual-agent-programming-plan-edit-validator",
            "kind": "depends_on",
        },
    ).to_dict()

    assert result["state"] == "rejected"
    assert result["collisions"][0]["code"] == "dependency_cycle"
    assert result["can_write"] is False
    assert result["can_start_agent"] is False


def test_visual_plan_edit_validator_route_returns_dry_run(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).post(
        "/api/roadmap/visual-agent-programming/validate-edit",
        json={
            "action": "connect_dependency",
            "from_node": "visual-agent-programming-readonly-lens",
            "to_node": "visual-agent-programming-plan-edit-validator",
            "kind": "depends_on",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "rejected"
    assert payload["can_write"] is False
    assert payload["accepted_events"] == []


def test_visual_plan_proposal_queue_is_read_only_review_surface():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    queue = build_visual_plan_proposal_queue(
        runtime,
        {
            "proposals": [
                {
                    "action": "create_node",
                    "node_id": "visual-agent-programming-review-probe",
                    "title": "Review probe",
                    "kind": "runtime",
                    "horizon": "later",
                    "target_version": "future",
                    "status": "planned",
                    "depends_on": ["visual-agent-programming-proposal-review-queue"],
                    "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
                    "deliverables": ["Review queue proof"],
                },
                {
                    "action": "create_node",
                    "node_id": "visual-agent-programming",
                    "title": "Duplicate",
                },
            ]
        },
    ).to_dict()

    assert queue["mode"] == "read_only"
    assert queue["counts"] == {"total_items": 2, "valid_items": 1, "blocked_items": 1, "accepted_items": 0}
    assert queue["items"][0]["state"] == "valid_dry_run"
    assert queue["items"][0]["accepted_events"] == []
    assert queue["items"][1]["collisions"][0]["code"] == "node_exists"
    assert all(control["state"] == "policy_gated" for control in queue["controls"].values())
    assert queue["context_policy"]["queue_state"] == "ephemeral_dry_run"


def test_visual_plan_proposal_queue_route_returns_review_snapshot(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).post(
        "/api/roadmap/visual-agent-programming/proposals/review",
        json={
            "proposals": [
                {
                    "action": "connect_dependency",
                    "from_node": "visual-agent-programming-proposal-review-queue",
                    "to_node": "visual-agent-programming",
                    "kind": "depends_on",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue_id"] == "odysseus-multiagent-roadmap-visual-proposal-review-queue"
    assert payload["counts"]["total_items"] == 1
    assert payload["controls"]["apply_to_roadmap"]["state"] == "policy_gated"
