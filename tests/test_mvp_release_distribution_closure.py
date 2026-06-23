from src.mvp_release_distribution_closure import (
    ReleaseDistributionClosureGate,
    build_release_distribution_closure_report,
)


def test_default_release_distribution_progress_tracks_evidence_complete_with_release_deferred():
    report = build_release_distribution_closure_report()

    assert report.roadmap_id == "release_distribution_evidence"
    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Image Tools Worker Final Smoke" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["manual_provider_proof"].status == "go"
    assert gates["manual_test_vault"].status == "go"
    assert gates["live_phase_boundaries"].status == "go"
    assert gates["mvp_roadmap_aggregate"].status == "go"
    assert gates["deploy_tag_distribution"].status == "deferred"
    assert gates["deploy_tag_distribution"].slice_class == "needs_live_go"
    assert gates["new_ui_release_gate"].status == "deferred"
    assert gates["new_ui_release_gate"].slice_class == "needs_design"


def test_release_distribution_strict_mode_keeps_external_release_open():
    report = build_release_distribution_closure_report(
        deploy_tag_distribution_deferred=False,
        new_ui_release_gate_deferred=False,
    )

    assert report.percent_complete == 82
    assert "Deploy, tag and distribution execution" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision


def test_release_distribution_reaches_100_when_version_1_conditions_complete():
    report = build_release_distribution_closure_report(
        mvp_roadmap_aggregate_go=True,
        deploy_tag_distribution_go=True,
        new_ui_release_gate_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Image Tools Worker Final Smoke" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 8 | Release / Distribution Evidence | 100 | - |"


def test_release_distribution_live_gate_is_next_after_mvp_aggregate():
    report = build_release_distribution_closure_report(
        deploy_tag_distribution_deferred=False,
        new_ui_release_gate_deferred=False,
    )

    assert report.percent_complete == 82
    assert "Deploy, tag and distribution execution" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision


def test_release_distribution_gate_validation_rejects_unknown_values():
    try:
        ReleaseDistributionClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported release closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        ReleaseDistributionClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="publish_now",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported release closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")
