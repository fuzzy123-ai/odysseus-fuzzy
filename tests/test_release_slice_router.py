from src.release_readiness_report import ReleaseReadinessReport
from src.release_slice_router import route_release_followups


def test_routes_provider_and_rebuild_partial_blockers_to_distinct_slices():
    report = ReleaseReadinessReport(
        status="blocked",
        external_release_go=False,
        release_gate_status="go",
        plugin_gate_ok=True,
        blocking_reasons=(
            "manual:partial:provider-proof",
            "manual:partial:export-import-rebuild",
        ),
        next_actions=("complete_partial_manual_evidence",),
    )

    slices = route_release_followups(report)

    assert [item.slice_id for item in slices] == [
        "REL-provider-proof-evidence",
        "REL-test-vault-rebuild-evidence",
        "REL-partial-manual-evidence-closeout",
    ]
    assert slices[0].owner == "Bob"
    assert slices[1].owner == "Alice"


def test_deduplicates_release_and_manual_pending_for_same_gate():
    report = ReleaseReadinessReport(
        status="blocked",
        external_release_go=False,
        release_gate_status="manual_pending",
        plugin_gate_ok=True,
        blocking_reasons=(
            "release:manual_pending:provider-proof",
            "manual:pending:provider-proof",
        ),
        next_actions=("complete_manual_release_evidence",),
    )

    slices = route_release_followups(report)

    assert [item.slice_id for item in slices] == [
        "REL-provider-proof-evidence",
        "REL-manual-evidence-closeout",
    ]


def test_routes_plugin_gate_failure_to_charlie():
    report = ReleaseReadinessReport(
        status="blocked",
        external_release_go=False,
        release_gate_status="go",
        plugin_gate_ok=False,
        blocking_reasons=("plugin:registry:plugins[0].download:download_not_https",),
        next_actions=("fix_plugin_release_gate",),
    )

    slices = route_release_followups(report)

    assert len(slices) == 1
    assert slices[0].slice_id == "REL-plugin-release-gate-fix"
    assert slices[0].owner == "Charlie"
    assert slices[0].parallel_safe is False


def test_routes_automated_gate_failure_to_bob():
    report = ReleaseReadinessReport(
        status="blocked",
        external_release_go=False,
        release_gate_status="blocked",
        plugin_gate_ok=True,
        blocking_reasons=("release:blocking:static-context-ui-safety-smoke",),
        next_actions=("fix_blocking_release_gates",),
    )

    slices = route_release_followups(report)

    assert slices[0].slice_id == "REL-automated-gate-fix"
    assert slices[0].owner == "Bob"


def test_green_report_routes_to_final_review():
    report = ReleaseReadinessReport(
        status="go",
        external_release_go=True,
        release_gate_status="go",
        plugin_gate_ok=True,
    )

    slices = route_release_followups(report)

    assert len(slices) == 1
    assert slices[0].slice_id == "REL-final-external-review"
    assert slices[0].owner == "Charlie"


def test_slice_to_dict_is_stable():
    report = ReleaseReadinessReport(
        status="blocked",
        external_release_go=False,
        release_gate_status="go",
        plugin_gate_ok=True,
        blocking_reasons=("manual:partial:provider-proof",),
    )

    item = route_release_followups(report)[0]

    assert item.to_dict() == {
        "slice_id": "REL-provider-proof-evidence",
        "owner": "Bob",
        "title": "Complete provider/fallback proof support",
        "scope": (
            "docs/plans/1.0-manual-release-evidence-log.md",
            "docs/plans/provider-fallback-answer-run-contract.md",
            "src/provider_fallback_answer_run.py",
            "tests/test_provider_fallback_answer_run.py",
        ),
        "exit_criteria": "Provider/fallback behavior is evidenced or a focused blocker is documented",
        "parallel_safe": False,
    }
