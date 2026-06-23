from src.mvp_orca_lens_closure import (
    OrcaLensClosureGate,
    build_orca_lens_closure_report,
)


def test_default_orca_lens_progress_tracks_aliases_done_core_open():
    report = build_orca_lens_closure_report()

    assert report.roadmap_id == "orca_lens_naming_backend_migration"
    assert report.percent_complete == 80
    assert "Frontend Lens naming" in report.why_not_100
    assert "shared UI redesign" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["env_tool_provider_aliases"].status == "go"
    assert gates["route_aliases"].status == "go"
    assert gates["orca_core_modules"].status == "go"
    assert gates["legacy_deprecation"].status == "go"
    assert gates["frontend_lens_redesign"].slice_class == "needs_design"
    assert gates["data_path_migration"].slice_class == "needs_live_go"


def test_orca_lens_reaches_100_when_all_gates_complete():
    report = build_orca_lens_closure_report(
        orca_core_modules_go=True,
        frontend_lens_redesign_go=True,
        legacy_deprecation_go=True,
        data_path_migration_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "PlanRuntime" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 6 | ORCA / Lens Naming & Backend Migration | 100 | - |"


def test_orca_lens_design_gate_is_next_after_core_modules():
    report = build_orca_lens_closure_report(orca_core_modules_go=True)

    assert report.percent_complete == 80
    assert "Frontend Lens naming" in report.why_not_100
    assert "shared UI redesign" in report.recommended_next_human_decision


def test_orca_lens_gate_validation_rejects_unknown_values():
    try:
        OrcaLensClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported ORCA closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        OrcaLensClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="rename_live_vault",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported ORCA closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
