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
                {"id": "mcp-debug-server-exposure-go", "status": "open"},
                {"id": "security-incident-tabletop-go", "status": "open"},
                {"id": "ui-placement", "status": "open"},
                {"id": "ambiguous-followup", "status": "open"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")

    assert [item["id"] for item in report["live_gates"]] == [
        "telegram-reminder-live-go",
        "mcp-debug-server-exposure-go",
        "security-incident-tabletop-go",
    ]
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


def test_audit_classifies_non_ui_backend_gate_families(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "master.json",
        {
            "open_gates": [
                {"id": "AIL-TECH-GATE-live-provider-stream", "status": "open"},
                {"id": "AIL-TECH-GATE-local-internals-runtime", "status": "open"},
                {"id": "PLANNING-WRITE-GO", "status": "open"},
                {"id": "PMCP-7b-delete-undo-gate", "status": "open"},
                {"id": "ACT-3-answer-pack-preview", "class": "needs_live_go", "status": "open"},
                {"id": "memory_review_queue", "class": "needs_live_go", "status": "open"},
                {"id": "ui-placement", "status": "open"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    packets = {item["family"]: item for item in report["decision_packets"]}

    assert packets["ai_lens_runtime"]["priority"] == 35
    assert packets["ai_lens_runtime"]["unique_gate_count"] == 2
    assert packets["ai_lens_runtime"]["safe_default"] == "keep_ai_lens_backend_contracts_only"
    assert "Chosen runtime target" in packets["ai_lens_runtime"]["required_inputs"]
    assert "Persisting raw prompts, completions or private context" in packets["ai_lens_runtime"]["forbidden_until_go"]
    assert packets["planning_mcp"]["priority"] == 36
    assert packets["planning_mcp"]["unique_gate_count"] == 2
    assert packets["planning_mcp"]["go_phrase"] == "GO planning_mcp bounded mutation"
    assert "Preview or diff" in packets["planning_mcp"]["required_inputs"]
    assert "Silent overwrite of roadmap truth" in packets["planning_mcp"]["forbidden_until_go"]
    assert packets["agent_context_transparency"]["priority"] == 37
    assert packets["agent_context_transparency"]["unique_gate_count"] == 2
    assert packets["agent_context_transparency"]["safe_default"] == "keep_agent_context_contracts_backend_only"
    assert "Redacted context-pack or provenance payload" in packets["agent_context_transparency"]["evidence_required"]
    assert packets["ui_design"]["unique_gate_count"] == 1
    assert "other_gate" not in packets
    assert report["non_ui_decision_packet_count"] == 3
    assert [item["family"] for item in report["non_ui_decision_packets"]] == [
        "ai_lens_runtime",
        "planning_mcp",
        "agent_context_transparency",
    ]
    assert report["excluded_design_decision_packet_count"] == 1
    assert [item["family"] for item in report["excluded_design_decision_packets"]] == ["ui_design"]


def test_audit_classifies_harbor_v2_handoff_gates_as_ui_design(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "master.json",
        {
            "open_gates": [
                {"id": "ACT-2-v3-context-rail-prototype", "class": "needs_design", "status": "planned"},
                {"id": "HPIM-7-v2-roadmap-document-source", "class": "needs_design", "status": "planned"},
                {"id": "HPIM-GAP-visual-regression", "class": "needs_design", "status": "planned"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    packets = {item["family"]: item for item in report["decision_packets"]}

    assert packets["ui_design"]["unique_gate_count"] == 3
    assert "other_gate" not in packets
    assert report["non_ui_decision_packet_count"] == 0
    assert report["excluded_design_decision_packet_count"] == 1


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
                {"id": "ui-placement", "status": "open"},
            ]
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")
    rendered = render_markdown(report)

    packets = {item["family"]: item for item in report["decision_packets"]}
    assert packets["version_release"]["safe_default"] == "defer_release_live_actions"
    assert packets["version_release"]["go_phrase"] == "GO version_release bounded evidence"
    assert "Release target" in packets["version_release"]["required_inputs"]
    assert packets["calendar_reminders"]["decision_needed"].startswith("Choose one bounded live reminder path")
    assert "Server-side Telegram dispatch target or CalDAV target" in packets["calendar_reminders"]["required_inputs"]
    assert "Live Telegram send" in packets["calendar_reminders"]["forbidden_until_go"]
    assert packets["other_gate"]["safe_default"] == "defer_uncategorized_gate"
    assert "## Operator Decision Packets" in rendered
    assert "## Non-UI Operator Go Packets" in rendered
    assert "## Excluded UI/Design Packets" in rendered
    assert "## Operator Go Packet Details" in rendered
    assert "| 90 | other_gate | Review uncategorized gates" in rendered
    assert "| 60 | ui_design | do_not_edit_ui_from_backend_abc |" in rendered
    assert "- Required inputs:" in rendered
    assert "  - Release target" in rendered


def test_audit_ignores_abc_execution_queue_router_waits(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "open-work-completion-master-roadmap.json",
        {
            "abc_execution_queue": [
                {
                    "id": "OWM-1-next-decision-router",
                    "class": "needs_live_go",
                    "status": "waiting_for_operator_choice",
                }
            ],
            "completion_lanes": [
                {
                    "id": "OWM-2",
                    "status": "needs_live_go",
                    "gate_ids": ["telegram-reminder-live-go"],
                }
            ],
        },
    )
    _write_json(
        plans / "calendar.json",
        {"gate_queue": [{"id": "telegram-reminder-live-go", "class": "needs_live_go", "status": "open"}]},
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")

    assert [item["id"] for item in report["live_gates"]] == ["telegram-reminder-live-go"]
    assert report["unique_live_gate_count"] == 1
    assert report["other_open_items"] == []


def test_audit_treats_live_evidence_and_done_slice_statuses_as_closed(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "gate_queue": [
                {"id": "debian-live-sandbox-worker-go", "class": "needs_live_go", "status": "resolved_with_live_evidence"},
                {"id": "next-live-go", "class": "needs_live_go", "status": "open"},
            ],
            "abc_execution_queue": [
                {"id": "OWM-0-masterroadmap-artifact", "class": "repo_only", "status": "done_in_this_slice"}
            ],
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")

    assert [item["id"] for item in report["live_gates"]] == ["next-live-go"]
    assert report["other_open_items"] == []


def test_audit_does_not_treat_artifact_or_policy_gate_metadata_as_safe_slices(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "slice_queue": [
                {
                    "id": "ROADMAP-ARTIFACT",
                    "class": "safe_offline",
                    "status": "done_in_this_artifact",
                },
                {"id": "SAFE-1", "class": "repo_only", "status": "planned"},
            ],
            "security_and_policy": {
                "gates": [
                    {"id": "READONLY-POLICY-GO", "class": "repo_only"},
                    {"id": "APPLY-GO", "class": "needs_operator_go", "status": "planned"},
                    {"id": "UI-GO", "class": "needs_design", "status": "gated"},
                ]
            },
        },
    )

    report = audit_plan_dir(plans, mvp_state_path=plans / "missing-mvp.json")

    assert [item["id"] for item in report["safe_open_slices"]] == ["SAFE-1"]
    assert [item["id"] for item in report["live_gates"]] == ["APPLY-GO"]
    assert [item["id"] for item in report["design_gates"]] == ["UI-GO"]
    assert [item["id"] for item in report["other_open_items"]] == ["READONLY-POLICY-GO"]
