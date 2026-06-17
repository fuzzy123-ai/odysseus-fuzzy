from src.orchestration_activation_audit_trail import ActivationAuditError
from src.orchestration_activation_handoff_checklist import (
    HandoffChecklistItem,
    HandoffChecklistReport,
    HandoffChecklistStatus,
    build_handoff_checklist_report,
    default_handoff_checklist_report,
)


def test_default_report_is_conservative_pre_runtime_needs_review():
    report = default_handoff_checklist_report()

    assert report.mode == "pre-runtime"
    assert report.overall_status == "needs_review"
    assert any(item.status == HandoffChecklistStatus.UNKNOWN for item in report.items)


def test_fail_item_forces_blocked():
    report = build_handoff_checklist_report(
        commit_present=False,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )

    assert report.overall_status == "blocked"


def test_all_pass_items_allow_ready():
    report = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )

    assert report.overall_status == "ready"


def test_boolean_helper_maps_statuses_conservatively():
    report = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=None,
        no_foreign_staged_files=False,
    )
    items = {item.item_id: item for item in report.items}

    assert items["commit_present"].status == HandoffChecklistStatus.PASS
    assert items["worktree_clean"].status == HandoffChecklistStatus.UNKNOWN
    assert items["no_foreign_staged_files"].status == HandoffChecklistStatus.FAIL


def test_missing_item_coverage_is_rejected():
    try:
        HandoffChecklistReport.create(
            mode="pre-runtime",
            items=(
                HandoffChecklistItem.create(
                    item_id="handoff_present",
                    status="pass",
                    summary="handoff exists",
                ),
            ),
        )
    except ActivationAuditError as exc:
        assert "full handoff checklist" in str(exc)
    else:
        raise AssertionError("expected ActivationAuditError")


def test_to_dict_is_stable():
    report = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )

    assert report.to_dict() == {
        "mode": "pre-runtime",
        "overall_status": "ready",
        "items": (
            {
                "item_id": "commit_present",
                "status": "pass",
                "summary": "a focused commit is present for the slice",
                "next_action": "",
            },
            {
                "item_id": "handoff_present",
                "status": "pass",
                "summary": "handoff metadata is present for operator review",
                "next_action": "",
            },
            {
                "item_id": "no_foreign_staged_files",
                "status": "pass",
                "summary": "no foreign staged files are mixed into the slice",
                "next_action": "",
            },
            {
                "item_id": "no_hotfile_overlap",
                "status": "pass",
                "summary": "no hot-file overlap remains unresolved",
                "next_action": "",
            },
            {
                "item_id": "operator_approval_required",
                "status": "pass",
                "summary": "operator approval remains an explicit gating step",
                "next_action": "keep operator approval mandatory before any live activation",
            },
            {
                "item_id": "runtime_hooks_disabled",
                "status": "pass",
                "summary": "runtime hooks remain disabled in pre-runtime mode",
                "next_action": "",
            },
            {
                "item_id": "scope_verified",
                "status": "pass",
                "summary": "the implementation scope has been verified",
                "next_action": "",
            },
            {
                "item_id": "tests_reported",
                "status": "pass",
                "summary": "tests are reported alongside the handoff",
                "next_action": "",
            },
            {
                "item_id": "worktree_clean",
                "status": "pass",
                "summary": "the worktree is clean or explicitly accounted for",
                "next_action": "",
            },
        ),
    }
