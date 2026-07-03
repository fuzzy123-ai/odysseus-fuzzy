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


def test_audit_classifies_classless_gate_lists_by_id_and_path(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "master.json",
        {
            "open_gates": [
                {"id": "telegram-reminder-live-go", "status": "open"},
                {"id": "ui-placement", "status": "open"},
                {"id": "ambiguous-followup", "status": "open"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")

    assert [item["id"] for item in report["live_gates"]] == ["telegram-reminder-live-go"]
    assert [item["id"] for item in report["design_gates"]] == ["ui-placement"]
    assert [item["id"] for item in report["other_open_items"]] == ["ambiguous-followup"]


def test_audit_groups_duplicate_gate_decisions_across_files(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "master.json",
        {"open_gates": [{"id": "deploy-live-go", "status": "open"}]},
    )
    _write_json(
        plans / "detail.json",
        {"gate_queue": [{"id": "deploy-live-go", "class": "needs_live_go", "status": "open"}]},
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    rendered = render_markdown(report)

    assert report["live_gate_count"] == 2
    assert report["unique_live_gate_count"] == 1
    assert report["live_gate_groups"] == [
        {
            "id": "deploy-live-go",
            "entry_count": 2,
            "files": ["detail.json", "master.json"],
            "statuses": ["open"],
        }
    ]
    assert "Unique live gate ids: 1" in rendered
    assert "| live | deploy-live-go | 2 | detail.json, master.json |" in rendered


def test_audit_recommends_prioritized_decision_families(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "master.json",
        {
            "open_gates": [
                {"id": "telegram-reminder-live-go", "status": "open"},
                {"id": "caldav-write-live-go", "status": "open"},
                {"id": "deploy-live-go", "status": "open"},
                {"id": "ui-placement", "status": "open"},
                {"id": "crowdsec-remediation-go", "status": "open"},
            ]
        },
    )
    _write_json(
        plans / "detail.json",
        {
            "gate_queue": [
                {"id": "deploy-live-go", "class": "needs_live_go", "status": "open"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    rendered = render_markdown(report)

    assert [
        (item["family"], item["priority"], item["unique_gate_count"], item["entry_count"])
        for item in report["recommended_decisions"]
    ] == [
        ("version_release", 10, 1, 2),
        ("calendar_reminders", 20, 2, 2),
        ("security_ops", 50, 1, 1),
        ("ui_design", 60, 1, 1),
    ]
    assert "## Recommended Next Decisions" in rendered
    assert "| 10 | version_release | 1 | 2 | deploy-live-go |" in rendered


def test_audit_renders_operator_decision_packets(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "master.json",
        {
            "open_gates": [
                {"id": "deploy-live-go", "status": "open"},
                {"id": "telegram-reminder-live-go", "status": "open"},
                {"id": "unknown-gate", "class": "needs_live_go", "status": "open"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    rendered = render_markdown(report)

    packets = {item["family"]: item for item in report["decision_packets"]}
    assert packets["version_release"]["safe_default"] == "defer_release_live_actions"
    assert packets["version_release"]["go_phrase"] == "GO version_release bounded evidence"
    assert packets["calendar_reminders"]["decision_needed"].startswith("Choose one bounded live reminder path")
    assert packets["other_gate"]["safe_default"] == "defer_uncategorized_gate"
    assert "## Operator Decision Packets" in rendered
    assert "| 90 | other_gate | Review uncategorized gates" in rendered
