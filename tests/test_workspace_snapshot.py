import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.workspace_snapshot_routes import setup_workspace_snapshot_routes
from src.workspace_snapshot import WORKSPACE_SNAPSHOT_SCHEMA, build_workspace_snapshot


class _AuthManager:
    is_configured = True

    def __init__(self, admins=("admin",)):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _app(*, user="admin", admins=("admin",), **providers) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_workspace_snapshot_routes(**providers))
    return app


def test_workspace_snapshot_normalizes_sections_and_fixture_state():
    snapshot = build_workspace_snapshot(
        operator_status={"schema": "odysseus.operator_dashboard.snapshot.v1", "status": "ok", "summary": "operator ready"},
        projects_status={"state": "fixture", "summary": "demo projects"},
        clarification_status={
            "schema": "odysseus.clarification_run_summary.v1",
            "state": "live",
            "status": "pending",
            "unresolved_required_count": 2,
        },
        release_status={"schema": "odysseus.version_one.readiness.v1", "state": "stale", "status": "partial"},
        generated_at="2026-07-12T10:00:00Z",
    )

    sections = {section["id"]: section for section in snapshot["sections"]}

    assert snapshot["schema"] == WORKSPACE_SNAPSHOT_SCHEMA
    assert snapshot["status"] == "attention"
    assert snapshot["counts"]["section_count"] == 10
    assert snapshot["counts"]["fixture_count"] == 1
    assert sections["projects"]["state"] == "fixture"
    assert sections["projects"]["available"] is True
    assert sections["projects"]["degraded"] is True
    assert sections["projects"]["frontend_hint"] == "render_fixture_fallback"
    assert sections["clarification"]["pending_count"] == 2
    assert sections["clarification"]["degraded"] is True
    assert sections["clarification"]["frontend_hint"] == "render_attention"
    assert sections["clarification"]["action"] == "review_clarification"
    assert sections["release"]["state"] == "stale"
    assert sections["release"]["degraded"] is True
    assert sections["release"]["degrade_reason"] == "release snapshot is stale"
    assert snapshot["controls"]["execute"]["state"] == "policy_gated"
    assert snapshot["live_probe_performed"] is False
    assert snapshot["write_action_available"] is False


def test_workspace_snapshot_redacts_secrets_and_private_paths():
    snapshot = build_workspace_snapshot(
        projects_status={
            "schema": "odysseus.projects.v1",
            "state": "live",
            "summary": "token SECRET should not pass",
        },
        coding_status={
            "schema": "odysseus.coding.v1",
            "state": "live",
            "summary": "C:/Users/private/project",
        },
    )
    encoded = json.dumps(snapshot, sort_keys=True)

    assert "SECRET" not in encoded
    assert "C:/Users" not in encoded
    sections = {section["id"]: section for section in snapshot["sections"]}
    assert sections["projects"]["summary"] == "projects unknown"
    assert sections["coding"]["summary"] == "coding unknown"


def test_workspace_snapshot_route_is_admin_gated_and_redacted():
    response = TestClient(
        _app(
            operator_provider=lambda: {"schema": "odysseus.operator.v1", "status": "ok", "summary": "operator ok"},
            projects_provider=lambda: {
                "schema": "odysseus.projects.v1",
                "state": "live",
                "status": "ok",
                "summary": "project ready",
                "raw_content": "PRIVATE BODY",
                "source_ref": "repo://safe-demo",
            },
            clarification_provider=lambda: {
                "schema": "odysseus.clarification_run_summary.v1",
                "state": "live",
                "status": "pending",
                "unresolved_required_count": 1,
                "prompt": "private prompt",
            },
            planning_provider=lambda: {
                "schema": "odysseus.planning.snapshot.v1",
                "state": "fixture",
                "summary": "synthetic planning fixture",
            },
            coding_provider=lambda: {
                "schema": "odysseus.coding.snapshot.v1",
                "state": "partial",
                "provider_failed": True,
                "reason": "runner adapter missing",
            },
            sandbox_provider=lambda: {
                "schema": "odysseus.agent.sandbox_job.v1",
                "status": "ready",
                "command": "python -m pytest --token SECRET",
            },
            local_model_provider=lambda: {
                "schema": "odysseus.local_model_status.v1",
                "state": "live",
                "status": "ok",
                "summary": "local gate ready",
            },
            release_provider=lambda: {
                "schema": "odysseus.version_one.readiness.v1",
                "state": "partial",
                "status": "blocked",
                "blocked_count": 1,
            },
        )
    ).get("/api/workspace/snapshot")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.workspace_snapshot.route.v1"
    assert payload["snapshot_schema"] == WORKSPACE_SNAPSHOT_SCHEMA
    assert payload["snapshot"]["schema"] == WORKSPACE_SNAPSHOT_SCHEMA
    assert payload["snapshot"]["status"] == "blocked"
    assert payload["snapshot"]["counts"]["section_count"] == 10
    assert payload["live_probe_performed"] is False
    assert payload["live_mutation_performed"] is False
    assert payload["write_action_enabled"] is False
    assert "PRIVATE BODY" not in encoded
    assert "private prompt" not in encoded
    assert "SECRET" not in encoded
    assert "--token" not in encoded


def test_workspace_snapshot_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/workspace/snapshot")

    assert response.status_code == 403


def test_workspace_snapshot_exposes_degradation_contract_for_frontend():
    snapshot = build_workspace_snapshot(
        projects_status={
            "schema": "odysseus.projects.v1",
            "state": "unavailable",
            "reason_unavailable": "project adapter disabled",
        },
        planning_status={
            "schema": "odysseus.planning.snapshot.v1",
            "state": "partial",
            "status": "ready",
            "degrade_reason": "proposal list omitted",
            "freshness": "partial",
        },
        knowledge_status={
            "schema": "odysseus.knowledge.snapshot.v1",
            "state": "live",
            "status": "ok",
            "freshness": "current",
        },
    )
    sections = {section["id"]: section for section in snapshot["sections"]}

    assert sections["projects"]["available"] is False
    assert sections["projects"]["degraded"] is True
    assert sections["projects"]["freshness"] == "missing"
    assert sections["projects"]["frontend_hint"] == "render_unavailable"
    assert sections["projects"]["degrade_reason"] == "project adapter disabled"

    assert sections["planning"]["available"] is True
    assert sections["planning"]["degraded"] is True
    assert sections["planning"]["freshness"] == "partial"
    assert sections["planning"]["frontend_hint"] == "render_attention"
    assert sections["planning"]["degrade_reason"] == "proposal list omitted"

    assert sections["knowledge"]["available"] is True
    assert sections["knowledge"]["degraded"] is False
    assert sections["knowledge"]["frontend_hint"] == "render_live"


def test_workspace_snapshot_route_marks_provider_failure_as_partial():
    response = TestClient(
        _app(
            operator_provider=lambda: {"schema": "odysseus.operator.v1", "status": "ok"},
            planning_provider=lambda: (_ for _ in ()).throw(RuntimeError("boom token SECRET")),
        )
    ).get("/api/workspace/snapshot")
    payload = response.json()
    sections = {section["id"]: section for section in payload["snapshot"]["sections"]}
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert sections["planning"]["state"] == "partial"
    assert sections["planning"]["available"] is True
    assert sections["planning"]["degraded"] is True
    assert sections["planning"]["freshness"] == "partial"
    assert sections["planning"]["frontend_hint"] == "render_attention"
    assert sections["planning"]["degrade_reason"] == "planning snapshot is partial"
    assert "SECRET" not in encoded
    assert "boom" not in encoded


def test_workspace_snapshot_exposes_coding_lifecycle_cards():
    snapshot = build_workspace_snapshot(
        coding_status={
            "schema": "odysseus.coding_runner_state.v1",
            "state": "live",
            "status": "review_ready",
            "task_id": "task-alpha",
            "repo_id": "demo",
            "phase": "review_ready",
            "progress_percent": 65,
            "worktree_ref": "worktree:task-alpha",
            "check_count": 3,
            "quality_gate": {"verified": True, "source_ref": "quality:task-alpha"},
            "done_gate": {"status": "pending"},
            "publish_gate": {"status": "pending"},
            "gates_waiting": ["operator_review", "publish_gate"],
        }
    )
    coding = {section["id"]: section for section in snapshot["sections"]}["coding"]
    cards = {card["id"]: card for card in coding["lifecycle_cards"]}

    assert len(coding["lifecycle_cards"]) == 10
    assert cards["clarification_gate"]["status"] == "unknown"
    assert cards["project_scope"]["status"] == "ok"
    assert cards["runner_phase"]["status"] == "ready"
    assert cards["runner_phase"]["progress_percent"] == 65
    assert cards["worktree_ref"]["status"] == "ok"
    assert cards["checks"]["summary"] == "3 check(s) reported for this coding task."
    assert cards["quality_gate"]["status"] == "ok"
    assert cards["publish_gate"]["requires_operator_go"] is True
    assert all(card["write_action_enabled"] is False for card in coding["lifecycle_cards"])
    assert all(card["raw_content_visible"] is False for card in coding["lifecycle_cards"])


def test_workspace_snapshot_coding_lifecycle_cards_redact_unsafe_values():
    snapshot = build_workspace_snapshot(
        coding_status={
            "schema": "odysseus.coding_runner_state.v1",
            "state": "live",
            "status": "blocked",
            "phase": "blocked",
            "task_id": "task-secret",
            "repo_id": "demo",
            "worktree_ref": "C:/Users/private/worktree",
            "quality_gate": {
                "status": "blocked",
                "summary": "token SECRET should vanish",
                "source_ref": "C:/Users/private/quality",
            },
            "done_gate": {"summary": "C:/Users/private/done"},
        }
    )
    coding = {section["id"]: section for section in snapshot["sections"]}["coding"]
    cards = {card["id"]: card for card in coding["lifecycle_cards"]}
    encoded = json.dumps(snapshot, sort_keys=True)

    assert cards["runner_phase"]["status"] == "blocked"
    assert cards["worktree_ref"]["status"] == "pending"
    assert cards["quality_gate"]["summary"] == ""
    assert cards["quality_gate"]["evidence_ref"] == ""
    assert cards["done_gate"]["summary"] == ""
    assert "SECRET" not in encoded
    assert "C:/Users" not in encoded


def test_workspace_snapshot_exposes_local_model_memory_details():
    snapshot = build_workspace_snapshot(
        local_model_status={
            "schema": "odysseus.local_model_memory_status.v1",
            "state": "live",
            "status": "pending",
            "summary": "local model foreground work active; maintenance must yield",
            "required_model": "gemma3:4b",
            "warm_model_status": "foreground_active",
            "known_cpu_constraint": "slow_local_model_latency_observed",
            "queue": {
                "active": 1,
                "active_foreground": 1,
                "waiting_foreground": 1,
                "max_concurrency": 1,
            },
            "foreground": {"active": True, "model": "gemma3:4b", "reason": "active"},
            "maintenance_guard": {
                "preflight_status": "unknown",
                "priority_class": "P3",
                "required_model": "gemma3:4b",
                "wait_timeout_seconds": 600,
                "command_timeout_seconds": 1800,
                "failure_count": 0,
                "warning_count": 1,
                "executes": False,
            },
            "benchmark_summary": {
                "model": "gemma3:4b",
                "latency_seconds": 81,
                "tokens": 27,
                "tokens_per_second": 0.333,
                "result": "slow",
            },
            "pending_count": 1,
        }
    )
    local_model = {section["id"]: section for section in snapshot["sections"]}["local_model"]
    details = local_model["status_details"]

    assert local_model["status"] == "pending"
    assert details["required_model"] == "gemma3:4b"
    assert details["queue"]["waiting_foreground"] == 1
    assert details["foreground"]["active"] is True
    assert details["maintenance_guard"]["executes"] is False
    assert details["benchmark_summary"]["latency_seconds"] == 81
    assert details["known_cpu_constraint"] == "slow_local_model_latency_observed"


def test_workspace_snapshot_exposes_knowledge_graph_details_without_raw_content():
    snapshot = build_workspace_snapshot(
        knowledge_status={
            "schema": "odysseus.knowledge.snapshot.v1",
            "state": "partial",
            "status": "pending",
            "redaction_state": "redacted",
            "memory_stats": {
                "personal_memory_entries": 12,
                "vector_index_count": 48,
                "vector_index_healthy": True,
                "rag_document_count": 7,
            },
            "graph": {
                "node_budget": 120,
                "node_count": 33,
                "edge_count": 51,
                "stale_count": 2,
                "partial": True,
            },
            "provenance": {
                "event_count": 9,
                "latest_event_type": "memory_retrieval",
                "source_ref": "memory:provenance",
            },
            "evidence_packets": [
                {"evidence_ref": "evidence:one", "status": "ok", "summary": "bounded packet"},
                {"evidence_ref": "C:/Users/private/evidence", "status": "blocked", "summary": "token SECRET"},
            ],
        }
    )
    knowledge = {section["id"]: section for section in snapshot["sections"]}["knowledge"]
    details = knowledge["status_details"]
    encoded = json.dumps(snapshot, sort_keys=True)

    assert knowledge["state"] == "partial"
    assert details["memory_stats"]["personal_memory_entries"] == 12
    assert details["graph"]["node_budget"] == 120
    assert details["graph"]["stale_count"] == 2
    assert details["provenance"]["latest_event_type"] == "memory_retrieval"
    assert details["evidence_packet_count"] == 2
    assert details["evidence_packets"][0]["evidence_ref"] == "evidence:one"
    assert details["evidence_packets"][1]["evidence_ref"] == ""
    assert details["evidence_packets"][1]["summary"] == ""
    assert "SECRET" not in encoded
    assert "C:/Users" not in encoded


def test_workspace_snapshot_exposes_planning_details_without_enabling_writes():
    snapshot = build_workspace_snapshot(
        planning_status={
            "schema": "odysseus.planning.snapshot.v1",
            "state": "live",
            "status": "pending",
            "roadmaps": {"count": 3, "ids": ["core-map", "ui-live", "C:/Users/private/map"]},
            "gates": {"count": 4, "open_count": 2},
            "proposals": {"count": 1, "status": "ready"},
            "context_pack": {"available": True, "source_ref": "planning:context-pack"},
            "apply_gate": {"status": "policy_gated"},
            "source_ref": "planning:dashboard",
        }
    )
    planning = {section["id"]: section for section in snapshot["sections"]}["planning"]
    details = planning["status_details"]
    encoded = json.dumps(snapshot, sort_keys=True)

    assert planning["status"] == "pending"
    assert details["roadmap_count"] == 3
    assert details["roadmap_ids"] == ("core-map", "ui-live")
    assert details["gate_count"] == 4
    assert details["open_gate_count"] == 2
    assert details["proposal_count"] == 1
    assert details["context_pack_available"] is True
    assert details["apply_gate_status"] == "policy_gated"
    assert details["writes_supported"] is False
    assert "C:/Users" not in encoded
