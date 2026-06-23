from src.mvp_image_tools_worker_closure import (
    ImageToolsWorkerClosureGate,
    build_image_tools_worker_closure_report,
)


def test_default_image_tools_worker_progress_keeps_manual_smoke_open():
    report = build_image_tools_worker_closure_report()

    assert report.roadmap_id == "image_tools_worker_final_smoke"
    assert report.percent_complete == 80
    assert "Manual Remove-BG smoke" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["worker_contract"].status == "go"
    assert gates["core_client"].status == "go"
    assert gates["route_integration"].status == "go"
    assert gates["isolated_worker_mvp"].status == "go"
    assert gates["fake_worker_smoke"].status == "go"
    assert gates["core_dependency_isolation"].status == "go"
    assert gates["ui_cookbook_contract"].status == "go"
    assert gates["telegram_image_readiness"].status == "go"
    assert gates["manual_remove_bg_smoke"].slice_class == "needs_live_go"
    assert gates["image_tools_ui_live"].slice_class == "needs_design"


def test_image_tools_worker_reaches_100_after_runtime_and_ui_gates():
    report = build_image_tools_worker_closure_report(
        telegram_image_readiness_go=True,
        manual_remove_bg_smoke_go=True,
        image_tools_ui_live_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "GameDev Mount Write Smoke" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 9 | Image Tools Worker Final Smoke | 100 | - |"


def test_image_tools_worker_live_gate_is_next_after_telegram_readiness():
    report = build_image_tools_worker_closure_report(telegram_image_readiness_go=True)

    assert report.percent_complete == 80
    assert "Manual Remove-BG smoke" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision


def test_image_tools_worker_gate_validation_rejects_unknown_values():
    try:
        ImageToolsWorkerClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported image tools worker closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        ImageToolsWorkerClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="publish_now",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported image tools worker closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
