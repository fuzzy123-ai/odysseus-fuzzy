import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.version_one_readiness_routes import setup_version_one_readiness_routes
from src.version_one_readiness import build_version_one_readiness


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _app(*, user="admin", admins=("admin",)) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_version_one_readiness_routes())
    return app


def _complete_mvp(ui_live=False):
    return {
        "version_1_0_gate": {"ui_live": ui_live},
        "roadmaps": [
            {"number": idx, "name": f"R{idx}", "percent": 100, "status": "go"}
            for idx in range(1, 11)
        ],
    }


def _legacy_backend_ready(open_contracts=()):
    evidence = {f"lc{idx}_backend_contract": [f"GET /api/lc{idx}"] for idx in range(1, 11)}
    evidence["version_1_0_ui_live"] = False
    return {"evidence": evidence, "open_backend_contracts": list(open_contracts)}


def _green_release_gates():
    return {
        "clarification_first": {"status": "ok", "accepted": True},
        "workspace_snapshot": {"status": "ok", "blocked_count": 0, "degraded_count": 0},
        "sandbox_python_acceptance": {"status": "passed"},
        "memory_local_model": {"status": "ok"},
    }


def test_version_one_blocks_when_ui_is_not_live():
    payload = build_version_one_readiness(
        mvp_state=_complete_mvp(ui_live=False),
        legacy_roadmap=_legacy_backend_ready(),
        **_green_release_gates(),
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.version_one_readiness.v1"
    assert payload["status"] == "ui_live_required"
    assert payload["version_1_0_ready"] is False
    assert payload["mvp"]["overall_percent"] == 100
    assert payload["legacy_chat"]["backend_ready"] is True
    assert payload["ui"]["live"] is False
    assert payload["readiness_gates"][1]["id"] == "harbor_one_live"
    assert payload["readiness_gates"][1]["ready"] is False
    assert payload["release"]["external_release_allowed"] is False
    assert payload["release"]["deploy_allowed"] is False
    assert payload["live_probe_performed"] is False
    assert payload["network_probe_performed"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "C:" not in encoded


def test_version_one_ready_only_when_mvp_backend_and_ui_are_ready():
    payload = build_version_one_readiness(
        mvp_state=_complete_mvp(ui_live=True),
        legacy_roadmap=_legacy_backend_ready(),
        **_green_release_gates(),
    )

    assert payload["status"] == "ready"
    assert payload["version_1_0_ready"] is True
    assert payload["release"]["tag_allowed"] is True
    assert payload["blocking_gate_ids"] == ()


def test_version_one_blocks_when_backend_contracts_are_open():
    payload = build_version_one_readiness(
        mvp_state=_complete_mvp(ui_live=True),
        legacy_roadmap=_legacy_backend_ready(open_contracts=("lc7",)),
        **_green_release_gates(),
    )

    assert payload["status"] == "backend_contracts_incomplete"
    assert payload["version_1_0_ready"] is False
    assert payload["legacy_chat"]["open_backend_contracts"] == ("lc7",)


def test_version_one_blocks_on_missing_clarification_first_acceptance():
    gates = _green_release_gates()
    gates.pop("clarification_first")
    payload = build_version_one_readiness(
        mvp_state=_complete_mvp(ui_live=True),
        legacy_roadmap=_legacy_backend_ready(),
        **gates,
    )

    assert payload["status"] == "clarification_first_acceptance_required"
    assert payload["version_1_0_ready"] is False
    assert "clarification_first_acceptance" in payload["blocking_gate_ids"]
    assert payload["readiness_gates"][0]["next_action"].startswith("Run the clarification-first")


def test_version_one_blocks_on_degraded_workspace_snapshot():
    gates = _green_release_gates()
    gates["workspace_snapshot"] = {"status": "attention", "degraded_count": 1}
    payload = build_version_one_readiness(
        mvp_state=_complete_mvp(ui_live=True),
        legacy_roadmap=_legacy_backend_ready(),
        **gates,
    )

    workspace_gate = {gate["id"]: gate for gate in payload["readiness_gates"]}["workspace_snapshot_green"]
    assert payload["status"] == "workspace_snapshot_green_required"
    assert workspace_gate["status"] == "partial"
    assert workspace_gate["ready"] is False
    assert payload["partial_gate_ids"] == ("workspace_snapshot_green",)


def test_version_one_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/version-one/readiness")

    assert response.status_code == 403


def test_version_one_route_returns_redacted_contract():
    response = TestClient(_app()).get("/api/version-one/readiness")
    payload = response.json()

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.version_one_readiness.v1"
    assert payload["ui"]["gate"] == "VERSION-1-UI-LIVE"
    assert payload["raw_content_visible"] is False
    assert payload["canonical_gate_evidence"][0]["schema"] == "gate_evidence_core.v1"
    assert payload["canonical_gate_evidence"][0]["id"] == "version_one_readiness"
    assert payload["canonical_safe_now"]["schema"] == "gate_evidence_core.safe_now.v1"
    assert payload["canonical_safe_now"]["can_proceed"] is False
