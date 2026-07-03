import json

from scripts.roadmap_safe_queue_audit import audit_plan_dir, render_markdown


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_audit_reports_safe_open_slices_and_gates(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "status": "repo_slices_done_live_gated",
            "slice_queue": [
                {"id": "SAFE-1", "class": "repo_only", "status": "open"},
                {"id": "SAFE-2", "class": "safe_offline", "status": "planned"},
                {"id": "DONE-1", "class": "repo_only", "status": "done"},
                {"id": "LIVE-1", "class": "needs_live_go", "status": "gated"},
                {"id": "UI-1", "class": "needs_design", "status": "open"},
                {"id": "READY-UI", "class": "repo_only", "status": "backend_ready_ui_gated"},
            ],
        },
    )
    mvp = plans / "mvp-roadmap-runner-state.json"
    _write_json(
        mvp,
        {
            "runner": {"active_slice": None},
            "version_1_0_gate": {"ui_live": False},
            "roadmaps": [{"percent": 100} for _ in range(10)],
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=mvp)

    assert report["files_scanned"] == 2
    assert report["safe_open_count"] == 2
    assert [item["id"] for item in report["safe_open_slices"]] == ["SAFE-1", "SAFE-2"]
    assert [item["id"] for item in report["live_gates"]] == ["LIVE-1"]
    assert [item["id"] for item in report["design_gates"]] == ["UI-1"]
    assert report["queue_exhausted"] is False


def test_audit_marks_queue_exhausted_when_only_gates_remain(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "slice_queue": [
                {"id": "DONE-1", "class": "repo_only", "status": "done"},
                {"id": "LIVE-1", "class": "needs_live_go", "status": "open"},
                {"id": "READY-UI", "class": "repo_only", "status": "backend_ready_ui_gated"},
            ],
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    rendered = render_markdown(report)

    assert report["safe_open_count"] == 0
    assert report["live_gate_count"] == 1
    assert report["queue_exhausted"] is True
    assert "Queue exhausted: yes" in rendered
    assert "LIVE-1" in rendered
