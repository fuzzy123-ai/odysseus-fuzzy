from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.plan_runtime import PlanRuntimeState
from src.planruntime_post_apply_dispatch import (
    DISPATCH_CONFIRMATION_TOKEN,
    build_post_apply_agent_dispatch_request,
)
from src.visual_agent_programming_lens import apply_visual_plan_mutation_patch, build_visual_plan_mutation_patch


def _applied_result(*, start_agent: bool = True):
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    patch = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "approve_for_me",
            "start_agent_after_apply": start_agent,
            "agent_start_confirmation": "START_AGENT_AFTER_MUTATION" if start_agent else "",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-apply-adapter",
                "node_id": "visual-agent-programming-dispatch-probe",
                "title": "Dispatch probe",
                "kind": "runtime",
                "horizon": "later",
                "target_version": "future",
                "status": "planned",
                "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
                "deliverables": ["Dispatch request proof"],
            },
        },
        last_updated_at="2026-06-21T14:00:00+00:00",
    ).to_dict()
    return apply_visual_plan_mutation_patch(
        runtime,
        {"patch_result": patch},
        last_updated_at="2026-06-21T14:01:00+00:00",
    ).to_dict()


def test_post_apply_dispatch_builds_confirmed_request_without_starting_agent():
    result = build_post_apply_agent_dispatch_request(
        {
            "apply_result": _applied_result(),
            "operator_id": "charlie",
            "agent_id": "bob",
            "node_id": "visual-agent-programming-dispatch-probe",
            "dispatch_confirmation": DISPATCH_CONFIRMATION_TOKEN,
        },
        created_at="2026-06-21T14:02:00Z",
    ).to_dict()

    assert result["state"] == "dispatch_request_ready"
    assert result["valid"] is True
    assert result["can_start_agent"] is False
    assert result["dispatched"] is False
    assert result["dispatch"]["state"] == "request_ready"
    assert result["dispatch"]["node_id"] == "visual-agent-programming-dispatch-probe"
    assert result["subagent_run_spec"]["node_id"] == "visual-agent-programming-dispatch-probe"
    assert any("Do not spawn" in stop for stop in result["subagent_run_spec"]["stop_conditions"])
    assert result["policy"]["execution_boundary"].startswith("request object only")


def test_post_apply_dispatch_blocks_missing_confirmation():
    result = build_post_apply_agent_dispatch_request(
        {
            "apply_result": _applied_result(),
            "operator_id": "charlie",
            "node_id": "visual-agent-programming-dispatch-probe",
        },
        created_at="2026-06-21T14:02:00Z",
    ).to_dict()

    assert result["state"] == "blocked"
    assert result["valid"] is False
    assert result["dispatched"] is False
    assert result["subagent_run_spec"] == {}
    assert any(stop["code"] == "missing_dispatch_confirmation" for stop in result["stops"])


def test_post_apply_dispatch_blocks_apply_without_ready_agent_request():
    result = build_post_apply_agent_dispatch_request(
        {
            "apply_result": _applied_result(start_agent=False),
            "operator_id": "charlie",
            "node_id": "visual-agent-programming-dispatch-probe",
            "dispatch_confirmation": DISPATCH_CONFIRMATION_TOKEN,
        },
        created_at="2026-06-21T14:02:00Z",
    ).to_dict()

    assert result["state"] == "blocked"
    assert any(stop["code"] == "no_ready_dispatch_request" for stop in result["stops"])


def test_post_apply_dispatch_route_returns_request_object(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).post(
        "/api/roadmap/visual-agent-programming/dispatch/request",
        json={
            "apply_result": _applied_result(),
            "operator_id": "charlie",
            "node_id": "visual-agent-programming-dispatch-probe",
            "dispatch_confirmation": DISPATCH_CONFIRMATION_TOKEN,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "dispatch_request_ready"
    assert payload["dispatched"] is False
    assert payload["policy"]["mode"] == "post_apply_dispatch_request"
