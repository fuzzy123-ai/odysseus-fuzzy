from src.mvp_gamedev_mount_closure import (
    GameDevMountClosureGate,
    build_gamedev_mount_closure_report,
)


def test_default_gamedev_mount_progress_keeps_write_smoke_open():
    report = build_gamedev_mount_closure_report()

    assert report.roadmap_id == "gamedev_mount_write_smoke"
    assert report.percent_complete == 88
    assert "Manual write smoke" in report.why_not_100
    assert "without explicit operator Go" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["mount_profile"].status == "go"
    assert gates["runtime_read_smoke"].status == "go"
    assert gates["path_owner_scope"].status == "go"
    assert gates["write_policy_guards"].status == "go"
    assert gates["command_gate"].status == "go"
    assert gates["operator_runbook"].status == "go"
    assert gates["reversible_write_plan"].status == "go"
    assert gates["manual_write_smoke"].slice_class == "needs_live_go"


def test_gamedev_mount_reaches_100_after_operator_write_smoke():
    report = build_gamedev_mount_closure_report(manual_write_smoke_go=True)

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Version 1.0" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 10 | GameDev Mount Write Smoke | 100 | - |"


def test_gamedev_mount_gate_validation_rejects_unknown_values():
    try:
        GameDevMountClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported gamedev mount closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        GameDevMountClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="publish_now",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported gamedev mount closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
