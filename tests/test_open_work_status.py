import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from routes import diagnostics_routes
from src import open_work_status


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_open_work_status_compacts_masterroadmap_and_gate_packets(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    roadmap = plans / "open-work-completion-master-roadmap.json"
    mvp = plans / "mvp-roadmap-runner-state.json"
    _write_json(
        roadmap,
        {
            "kind": "odysseus.open_work_completion_master_roadmap",
            "status": "live_and_design_gated",
            "updated_at": "2026-07-04",
            "abc_mode": "Standard ABC",
            "goal": "finish open work",
            "goal_command": "/goal finish",
            "source_of_truth": [
                "docs/plans/mvp-master-roadmap.md",
                "C:/Users/private/not-allowed.md",
            ],
            "current_position": {
                "safe_open_json_slices": 0,
                "unique_live_gates": 1,
            },
            "completion_lanes": [
                {
                    "id": "OWM-2",
                    "name": "Calendar And Reminder Live Reliability",
                    "priority": 20,
                    "status": "needs_live_go",
                    "why_open": "Needs bounded live evidence.",
                    "source_gate_family": "calendar_reminders",
                    "safe_default": "keep_reminders_repo_ready_no_live_send",
                    "operator_go_phrase": "GO calendar_reminders bounded smoke",
                    "done_when": "Reminder is observed.",
                    "private_notes": "must not leak",
                }
            ],
            "recommended_execution_order": ["Calendar first"],
            "recommended_next_human_decision": "GO calendar_reminders bounded smoke",
        },
    )
    _write_json(
        mvp,
        {
            "runner": {"active_slice": None},
            "version_1_0_gate": {"ui_live": False},
            "roadmaps": [{"percent": 100} for _ in range(10)],
        },
    )
    _write_json(
        plans / "calendar.json",
        {"gate_queue": [{"id": "telegram-reminder-live-go", "class": "needs_live_go", "status": "open"}]},
    )

    payload = open_work_status.build_open_work_completion_status(
        roadmap_path=roadmap,
        plan_dir=plans,
        mvp_state_path=mvp,
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.open_work_completion_status.v1"
    assert payload["status"] == "live_and_design_gated"
    assert payload["roadmap"]["kind_ok"] is True
    assert payload["roadmap"]["source_of_truth"] == ["docs/plans/mvp-master-roadmap.md"]
    assert payload["queue"]["safe_open_slices"] == 0
    assert payload["queue"]["unique_live_gates"] == 1
    assert payload["queue"]["queue_exhausted"] is True
    assert payload["completion_lanes"][0]["id"] == "OWM-2"
    assert payload["decision_packets"][0]["family"] == "calendar_reminders"
    assert payload["raw_records_included"] is False
    assert "private_notes" not in encoded
    assert "C:/Users/private" not in encoded


def test_open_work_status_handles_missing_and_invalid_roadmap(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    missing = open_work_status.build_open_work_completion_status(
        roadmap_path=plans / "missing.json",
        plan_dir=plans,
        mvp_state_path=plans / "missing-mvp.json",
    )
    assert missing["status"] == "missing"
    assert missing["roadmap"]["exists"] is False

    invalid_path = plans / "invalid.json"
    invalid_path.write_text("{not-json", encoding="utf-8")
    invalid = open_work_status.build_open_work_completion_status(
        roadmap_path=invalid_path,
        plan_dir=plans,
        mvp_state_path=plans / "missing-mvp.json",
    )
    assert invalid["status"] == "invalid_json"
    assert invalid["roadmap"]["exists"] is True


def test_open_work_diagnostics_route_is_admin_gated_and_redacted(tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    plans.mkdir()
    roadmap = plans / "open-work-completion-master-roadmap.json"
    mvp = plans / "mvp-roadmap-runner-state.json"
    _write_json(
        roadmap,
        {
            "kind": "odysseus.open_work_completion_master_roadmap",
            "status": "live_and_design_gated",
            "completion_lanes": [],
            "recommended_next_human_decision": "GO calendar_reminders bounded smoke",
        },
    )
    _write_json(mvp, {"runner": {"active_slice": None}, "roadmaps": []})
    _write_json(
        plans / "calendar.json",
        {"gate_queue": [{"id": "telegram-reminder-live-go", "class": "needs_live_go", "status": "open"}]},
    )
    monkeypatch.setattr(open_work_status, "DEFAULT_PLAN_DIR", plans)
    monkeypatch.setattr(open_work_status, "DEFAULT_ROADMAP_PATH", roadmap)
    monkeypatch.setattr(open_work_status, "DEFAULT_MVP_STATE_PATH", mvp)

    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))

    def deny(_request: Request):
        raise HTTPException(403, "Admin only")

    monkeypatch.setattr(diagnostics_routes, "require_admin", deny)
    assert TestClient(app).get("/api/diagnostics/open-work").status_code == 403

    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    response = TestClient(app).get("/api/diagnostics/open-work")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "live_and_design_gated"
    assert payload["decision_packets"][0]["go_phrase"] == "GO calendar_reminders bounded smoke"
    assert payload["raw_records_included"] is False
