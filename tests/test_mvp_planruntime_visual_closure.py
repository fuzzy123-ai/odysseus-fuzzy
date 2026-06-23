from src.mvp_planruntime_visual_closure import (
    PlanRuntimeVisualClosureGate,
    build_planruntime_visual_closure_report,
)


def test_default_planruntime_visual_progress_tracks_backend_logic_done_ui_open():
    report = build_planruntime_visual_closure_report()

    assert report.roadmap_id == "planruntime_visual_planning_logic"
    assert report.percent_complete == 92
    assert "Browser proposal editor UI" in report.why_not_100
    assert "shared UI redesign" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["planruntime_source"].status == "go"
    assert gates["apply_adapter"].status == "go"
    assert gates["route_contracts"].status == "go"
    assert gates["browser_editor_ui"].slice_class == "needs_design"
    assert gates["post_apply_dispatch"].status == "go"


def test_planruntime_visual_reaches_100_when_all_gates_complete():
    report = build_planruntime_visual_closure_report(
        browser_editor_ui_go=True,
        post_apply_dispatch_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Release / Distribution Evidence" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 7 | PlanRuntime / Visual Planning Logic | 100 | - |"


def test_planruntime_visual_dispatch_gate_is_next_after_ui():
    report = build_planruntime_visual_closure_report(browser_editor_ui_go=True, post_apply_dispatch_go=False)

    assert report.percent_complete == 92
    assert "Post-apply agent dispatch" in report.why_not_100
    assert "post-apply dispatch" in report.recommended_next_human_decision


def test_planruntime_visual_gate_validation_rejects_unknown_values():
    try:
        PlanRuntimeVisualClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported PlanRuntime closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        PlanRuntimeVisualClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="dispatch_now",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported PlanRuntime closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
