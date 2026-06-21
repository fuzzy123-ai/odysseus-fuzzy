from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.plan_runtime import PlanRuntimeState
from src.visual_agent_programming_lens import (
    apply_visual_plan_mutation_patch,
    build_visual_agent_programming_snapshot,
    build_visual_plan_mutation_patch,
    build_visual_plan_proposal_queue,
    validate_visual_plan_acceptance,
    validate_visual_plan_edit,
)


def test_visual_agent_programming_snapshot_is_read_only_and_at_policy_gate():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    snapshot = build_visual_agent_programming_snapshot(
        runtime,
        last_updated_at="2026-06-21T11:15:00+00:00",
    )
    payload = snapshot.to_dict()

    assert payload["mode"] == "read_only"
    assert payload["active_node_id"] == "visual-agent-programming-apply-adapter"
    assert payload["next_claimable_node_id"] == "visual-agent-programming-apply-adapter"
    assert any(node["node_id"] == "visual-agent-programming-readonly-lens" and node["live_done"] for node in payload["nodes"])
    assert any(
        node["node_id"] == "visual-agent-programming-plan-edit-validator" and node["live_done"]
        for node in payload["nodes"]
    )
    assert any(
        node["node_id"] == "visual-agent-programming-proposal-review-queue" and node["live_done"]
        for node in payload["nodes"]
    )
    assert any(
        node["node_id"] == "visual-agent-programming-operator-acceptance-contract" and node["live_done"]
        for node in payload["nodes"]
    )
    assert any(
        node["node_id"] == "visual-agent-programming-mutation-patch-contract"
        and node["live_done"]
        and node["visual_status"] == "completed"
        for node in payload["nodes"]
    )
    assert any(
        node["node_id"] == "visual-agent-programming-apply-adapter"
        and node["claimable"]
        and node["visual_status"] == "ready"
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

    assert payload["progress"]["branch_nodes"] == 7
    assert any(layer["target_version"] == "0.10" for layer in payload["version_layers"])
    assert any(layer["target_version"] == "future" for layer in payload["version_layers"])
    assert [step["node_id"] for step in payload["next_steps"]] == [
        "visual-agent-programming-apply-adapter",
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


def test_visual_plan_acceptance_contract_projects_auditable_accept_event():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = validate_visual_plan_acceptance(
        runtime,
        {
            "decision": "accept",
            "operator_id": "charlie",
            "operator_confirmation": "ACCEPT_PLAN_EVENT",
            "proposal": {
                "action": "create_node",
                "node_id": "visual-agent-programming-acceptance-probe",
                "title": "Acceptance probe",
                "kind": "runtime",
                "horizon": "later",
                "target_version": "future",
                "status": "planned",
                "depends_on": ["visual-agent-programming-operator-acceptance-contract"],
                "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
                "deliverables": ["Acceptance contract proof"],
            },
        },
    ).to_dict()

    assert result["state"] == "accepted_event_ready"
    assert result["valid"] is True
    assert result["can_write"] is False
    assert result["can_start_agent"] is False
    assert result["accepted_events"] == []
    assert result["event_projection"]["type"] == "visual_plan_proposal_accepted"
    assert result["event_projection"]["requires_future_write_adapter"] is True


def test_visual_plan_acceptance_contract_rejects_missing_confirmation():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = validate_visual_plan_acceptance(
        runtime,
        {
            "decision": "accept",
            "operator_id": "charlie",
            "operator_confirmation": "",
            "proposal": {"action": "create_node", "node_id": "visual-agent-programming-missing-confirmation"},
        },
    ).to_dict()

    assert result["state"] == "rejected"
    assert any(stop["code"] == "missing_operator_confirmation" for stop in result["stops"])
    assert result["event_projection"] == {}


def test_visual_plan_acceptance_contract_rejects_invalid_accept_proposal():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = validate_visual_plan_acceptance(
        runtime,
        {
            "decision": "accept",
            "operator_id": "charlie",
            "operator_confirmation": "ACCEPT_PLAN_EVENT",
            "proposal": {
                "action": "create_node",
                "node_id": "visual-agent-programming",
                "title": "Duplicate",
            },
        },
    ).to_dict()

    assert result["state"] == "rejected"
    assert any(stop["code"] == "proposal_not_valid" for stop in result["stops"])
    assert result["can_start_agent"] is False


def test_visual_plan_acceptance_contract_route_returns_policy_projection(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).post(
        "/api/roadmap/visual-agent-programming/proposals/acceptance/validate",
        json={
            "decision": "reject",
            "operator_id": "charlie",
            "operator_confirmation": "REJECT_PLAN_EVENT",
            "proposal": {
                "action": "create_node",
                "node_id": "visual-agent-programming-route-reject-probe",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "rejected_event_ready"
    assert payload["can_write"] is False
    assert payload["policy"]["agent_start_boundary"] == "acceptance never starts agents"


def test_visual_plan_mutation_patch_requires_default_confirmation():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-mutation-patch-contract",
                "node_id": "visual-agent-programming-patch-probe",
            },
        },
        last_updated_at="2026-06-21T12:30:00+00:00",
    ).to_dict()

    assert result["state"] == "rejected"
    assert any(stop["code"] == "missing_mutation_confirmation" for stop in result["stops"])
    assert result["can_write"] is False
    assert result["patch"] == {}


def test_visual_plan_mutation_patch_supports_approve_for_me_create_from_node():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "approve_for_me",
            "visual_status": "draft",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-mutation-patch-contract",
                "node_id": "visual-agent-programming-empty-path-probe",
                "title": "Empty path probe",
                "kind": "runtime",
                "horizon": "later",
                "target_version": "future",
                "status": "planned",
                "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
                "deliverables": ["Patch contract proof"],
            },
        },
        last_updated_at="2026-06-21T12:31:00+00:00",
    ).to_dict()

    assert result["state"] == "patch_ready"
    assert result["can_write"] is True
    assert result["audit"]["self_approved"] == "true"
    assert result["version"]["created_at"] == "2026-06-21T12:31:00+00:00"
    assert result["status_palette"]["completed"] == "green"
    operations = result["patch"]["operations"]
    assert operations[0]["op"] == "add_node"
    assert operations[0]["node"]["depends_on"] == ["visual-agent-programming-mutation-patch-contract"]
    assert operations[0]["node"]["visual_status"] == "draft"
    assert operations[1] == {
        "op": "add_edge",
        "from": "visual-agent-programming-mutation-patch-contract",
        "to": "visual-agent-programming-empty-path-probe",
        "kind": "depends_on",
    }


def test_visual_plan_mutation_patch_connects_existing_nodes_with_confirmation_and_agent_start_request():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    result = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "require_confirmation",
            "mutation_confirmation": "APPLY_VISUAL_PLAN_MUTATION",
            "visual_status": "ready",
            "start_agent_after_apply": True,
            "agent_start_confirmation": "START_AGENT_AFTER_MUTATION",
            "proposal": {
                "action": "connect_dependency",
                "from_node": "roadmap-lens-readonly",
                "to_node": "visual-agent-programming-operator-acceptance-contract",
                "kind": "depends_on",
            },
        },
        last_updated_at="2026-06-21T12:32:00+00:00",
    ).to_dict()

    assert result["state"] == "patch_ready"
    assert result["can_write"] is True
    assert result["can_start_agent"] is True
    assert result["agent_start_request"]["state"] == "authorized_after_apply"
    assert result["patch"]["operations"][0] == {
        "op": "add_edge",
        "from": "roadmap-lens-readonly",
        "to": "visual-agent-programming-operator-acceptance-contract",
        "kind": "depends_on",
        "visual_status": "ready",
    }


def test_visual_plan_mutation_patch_route_returns_patch(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).post(
        "/api/roadmap/visual-agent-programming/mutations/patch",
        json={
            "operator_id": "charlie",
            "permission_mode": "approve_for_me",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-mutation-patch-contract",
                "node_id": "visual-agent-programming-route-patch-probe",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "patch_ready"
    assert payload["policy"]["write_boundary"] == "patch is authorized but not applied by this endpoint"


def test_visual_plan_mutation_apply_adapter_applies_patch_to_payload_with_rollback():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    patch = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "approve_for_me",
            "visual_status": "ready",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-apply-adapter",
                "node_id": "visual-agent-programming-apply-probe",
                "title": "Apply probe",
                "kind": "runtime",
                "horizon": "later",
                "target_version": "future",
                "status": "planned",
                "source_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
                "deliverables": ["Apply adapter proof"],
            },
        },
        last_updated_at="2026-06-21T13:10:00+00:00",
    ).to_dict()

    result = apply_visual_plan_mutation_patch(
        runtime,
        {"patch_result": patch},
        last_updated_at="2026-06-21T13:11:00+00:00",
    ).to_dict()

    assert result["state"] == "applied_to_payload"
    assert result["valid"] is True
    assert result["file_written"] is False
    assert result["version"]["applied_version"].endswith("applied-2026-06-21t13-11-00-00-00")
    node_ids = {node["id"] for node in result["applied_payload"]["graph_nodes"]}
    assert "visual-agent-programming-apply-probe" in node_ids
    assert result["rollback"]["operations"][0]["op"] == "remove_edge"
    assert result["rollback"]["operations"][1] == {
        "op": "remove_node",
        "node_id": "visual-agent-programming-apply-probe",
    }


def test_visual_plan_mutation_apply_adapter_requires_confirmation_for_default_mode():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    patch = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "require_confirmation",
            "mutation_confirmation": "APPLY_VISUAL_PLAN_MUTATION",
            "proposal": {
                "action": "connect_dependency",
                "from_node": "roadmap-lens-readonly",
                "to_node": "visual-agent-programming-apply-adapter",
                "kind": "depends_on",
            },
        },
        last_updated_at="2026-06-21T13:12:00+00:00",
    ).to_dict()

    result = apply_visual_plan_mutation_patch(
        runtime,
        {"patch_result": patch},
        last_updated_at="2026-06-21T13:13:00+00:00",
    ).to_dict()

    assert result["state"] == "rejected"
    assert any(stop["code"] == "missing_apply_confirmation" for stop in result["stops"])
    assert result["applied_payload"] == {}


def test_visual_plan_mutation_apply_adapter_returns_agent_dispatch_request_after_apply():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    patch = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "approve_for_me",
            "start_agent_after_apply": True,
            "agent_start_confirmation": "START_AGENT_AFTER_MUTATION",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-apply-adapter",
                "node_id": "visual-agent-programming-agent-start-probe",
            },
        },
        last_updated_at="2026-06-21T13:14:00+00:00",
    ).to_dict()

    result = apply_visual_plan_mutation_patch(
        runtime,
        {"patch_result": patch},
        last_updated_at="2026-06-21T13:15:00+00:00",
    ).to_dict()

    assert result["state"] == "applied_to_payload"
    assert result["can_start_agent"] is True
    assert result["agent_start_request"]["state"] == "ready_for_dispatch"


def test_visual_plan_mutation_apply_route_returns_payload(monkeypatch):
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    patch = build_visual_plan_mutation_patch(
        runtime,
        {
            "operator_id": "charlie",
            "permission_mode": "approve_for_me",
            "proposal": {
                "action": "create_node",
                "from_node": "visual-agent-programming-apply-adapter",
                "node_id": "visual-agent-programming-route-apply-probe",
            },
        },
        last_updated_at="2026-06-21T13:16:00+00:00",
    ).to_dict()
    app = FastAPI()
    app.include_router(setup_roadmap_routes())

    response = TestClient(app).post(
        "/api/roadmap/visual-agent-programming/mutations/apply",
        json={"patch_result": patch},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "applied_to_payload"
    assert payload["policy"]["write_boundary"] == "applies to roadmap payload; filesystem persistence is a separate commit/deploy operation"
