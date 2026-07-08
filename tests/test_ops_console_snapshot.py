import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.ops_console_routes import setup_ops_console_routes
from src.ops_console_snapshot import OPS_CONSOLE_SNAPSHOT_SCHEMA, build_ops_console_snapshot
from src.ops_timeline_adapters import build_ops_timeline_from_sources
from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_remediation_actions import prepare_remediation_plan
from src.security_response_policy import decide_incident_response


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _app(*, user="admin", admins=("admin",), snapshot_builder=None):
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_ops_console_routes(snapshot_builder=snapshot_builder))
    return app


def _security_sources():
    action = build_recommended_action(
        action_type="service_restart",
        summary="Prepare a service restart request.",
        risk="Brief service interruption requires operator review.",
        action_id="act-restart",
    )
    incident = build_security_incident(
        incident_id="inc-ops",
        level=3,
        severity="high",
        confidence=0.9,
        status="open",
        trigger="Service down event exceeded policy threshold.",
        affected_surfaces=("ops",),
        correlation_ids=("corr-ops-1",),
        evidence_refs=("runtime-event:evt-ops-1",),
        recommended_actions=(action,),
    )
    policy = decide_incident_response(incident)
    plan = prepare_remediation_plan(incident, requested_action_ids=("act-restart",))
    return incident, policy, plan


def test_ops_console_snapshot_defaults_to_readonly_redacted_no_live_packet():
    snapshot = build_ops_console_snapshot()
    encoded = json.dumps(snapshot, sort_keys=True)

    assert snapshot["schema"] == OPS_CONSOLE_SNAPSHOT_SCHEMA
    assert snapshot["status"] == "watch"
    assert snapshot["timeline"]["schema"] == "odysseus.ops_timeline.v1"
    assert snapshot["source_states"]["system_health"] == "no_data"
    assert snapshot["source_states"]["security_policy"] == "ready"
    assert snapshot["source_states"]["remediation"] == "ready"
    assert snapshot["raw_content_visible"] is False
    assert snapshot["raw_logs_visible"] is False
    assert snapshot["host_paths_visible"] is False
    assert snapshot["tokens_visible"] is False
    assert snapshot["live_queries_performed"] is False
    assert snapshot["host_commands_performed"] is False
    assert snapshot["writes_performed"] is False
    assert snapshot["remediation_performed"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "chat_id" not in encoded.lower()
    assert "C:\\\\" not in encoded


def test_ops_console_snapshot_preserves_security_gates_from_sources():
    incident, policy, plan = _security_sources()

    snapshot = build_ops_console_snapshot(
        security_incident=incident,
        response_policy=policy,
        remediation_plan=plan,
        timeline_id="ops-security",
    )

    assert snapshot["status"] == "contain"
    assert "service_restart-operator-go" in snapshot["operator_gates"]
    assert snapshot["counts"]["required_gates"] >= 1
    assert any(event["surface"] == "remediation" for event in snapshot["timeline"]["events"])
    assert snapshot["writes_performed"] is False
    assert snapshot["remediation_performed"] is False


def test_ops_console_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/ops-console/snapshot")

    assert response.status_code == 403


def test_ops_console_route_returns_snapshot_contract():
    response = TestClient(_app()).get("/api/ops-console/snapshot")
    payload = response.json()

    assert response.status_code == 200
    assert payload["schema"] == OPS_CONSOLE_SNAPSHOT_SCHEMA
    assert payload["timeline"]["schema"] == "odysseus.ops_timeline.v1"
    assert payload["security_policy_readiness"]["executes_live_actions"] is False
    assert payload["remediation_readiness"]["executes_live_actions"] is False
    assert payload["live_queries_performed"] is False
    assert payload["host_commands_performed"] is False


def test_ops_console_route_accepts_injected_redacted_snapshot_builder():
    timeline = build_ops_timeline_from_sources(
        diagnostic_packet={
            "status": "attention",
            "intent": "general_operations",
            "findings": (
                {
                    "code": "legacy-evidence",
                    "severity": "warning",
                    "evidence": r"C:\Users\nkatz\private.log",
                },
            ),
            "recommended_next_actions": (),
        },
        timeline_id="ops-injected",
    )

    def _builder():
        return {
            "schema": OPS_CONSOLE_SNAPSHOT_SCHEMA,
            "status": timeline["status"],
            "timeline": timeline,
            "source_states": {"diagnostics": "attention"},
            "counts": {"timeline_events": timeline["event_count"]},
            "operator_gates": timeline["required_gates"],
            "raw_content_visible": False,
            "host_paths_visible": False,
            "live_queries_performed": False,
            "host_commands_performed": False,
            "writes_performed": False,
            "remediation_performed": False,
        }

    response = TestClient(_app(snapshot_builder=_builder)).get("/api/ops-console/snapshot")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["timeline"]["timeline_id"] == "ops-injected"
    assert "private.log" not in encoded
    assert "evidence:sha256:" in encoded
