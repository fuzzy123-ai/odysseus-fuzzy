from src.orchestration_activation_readiness_summary import build_activation_readiness_summary
from src.orchestration_operator_activation import OperatorActivationPolicy, build_orchestration_activation_plan
from src.orchestration_runtime_readiness import (
    RuntimeCapability,
    build_current_runtime_readiness_report,
    build_runtime_readiness_report,
)


def test_default_current_readiness_stays_conservative():
    report = build_current_runtime_readiness_report()
    plan = build_orchestration_activation_plan(
        readiness=report,
        policy=OperatorActivationPolicy.create(
            requested_mode="read_only",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
    )

    summary = build_activation_readiness_summary(report, plan)

    assert summary.status_label == "read_only"
    assert summary.live_dispatch_allowed is False
    assert summary.open_gap_count == 4
    assert summary.operator_required is True


def test_prepare_only_summary_keeps_prepare_actions_without_send():
    report = build_current_runtime_readiness_report()
    plan = build_orchestration_activation_plan(
        readiness=report,
        policy=OperatorActivationPolicy.create(
            requested_mode="prepare_dispatch",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
    )

    summary = build_activation_readiness_summary(report, plan)

    assert summary.status_label == "prepare_only"
    assert "prepare_mailbox_draft" in summary.allowed_actions
    assert "prepare_dispatch_plan" in summary.allowed_actions
    assert summary.live_dispatch_allowed is False


def test_live_limited_ready_requires_clean_report():
    clean_report = build_runtime_readiness_report(
        capabilities=(
            RuntimeCapability.create(
                capability_id="registry-model",
                category="dashboard",
                status="ready",
                live_hook=False,
                summary="ready metadata",
            ),
        ),
        gaps=(),
    )
    plan = build_orchestration_activation_plan(
        readiness=clean_report,
        policy=OperatorActivationPolicy.create(
            requested_mode="live_dispatch_limited",
            operator_approved=True,
            allow_live_dispatch=True,
        ),
    )

    summary = build_activation_readiness_summary(clean_report, plan)

    assert summary.status_label == "live_limited_ready"
    assert summary.live_dispatch_allowed is True
    assert summary.operator_required is False


def test_stable_dict_output():
    report = build_current_runtime_readiness_report()
    plan = build_orchestration_activation_plan(
        readiness=report,
        policy=OperatorActivationPolicy.create(
            requested_mode="prepare_dispatch",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
    )
    summary = build_activation_readiness_summary(report, plan)

    assert summary.to_dict() == {
        "mode": "prepare_dispatch",
        "status_label": "prepare_only",
        "live_dispatch_allowed": False,
        "open_gap_count": 4,
        "blocking_reasons": (
            "Git command execution still depends on explicit operator approval.",
            "Heartbeat scheduling is intentionally blocked from unattended execution.",
            "Automated test command execution is not yet live-safe for unattended orchestration.",
            "Thread send integration is modeled only and not safe for unattended live sends yet.",
        ),
        "allowed_actions": (
            "prepare_dispatch_plan",
            "prepare_mailbox_draft",
            "review_registry",
            "view_dashboard",
        ),
        "next_safe_action": "Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.",
        "operator_required": True,
    }
