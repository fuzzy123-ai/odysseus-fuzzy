from src.orchestration_activation_readiness_summary import build_activation_readiness_summary
from src.orchestration_activation_summary_renderers import (
    render_activation_readiness_summary_json,
    render_activation_readiness_summary_markdown,
)
from src.orchestration_operator_activation import OperatorActivationPolicy, build_orchestration_activation_plan
from src.orchestration_runtime_readiness import (
    RuntimeCapability,
    build_current_runtime_readiness_report,
    build_runtime_readiness_report,
)


def test_default_current_summary_renders_conservative_json_and_markdown():
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

    payload = render_activation_readiness_summary_json(summary)
    markdown = render_activation_readiness_summary_markdown(summary)

    assert '"status_label": "prepare_only"' in payload
    assert '"live_dispatch_allowed": false' in payload
    assert "Status: prepare_only" in markdown
    assert "Live Dispatch Allowed: no" in markdown


def test_clean_live_limited_ready_summary_renders_live_ready_state():
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

    payload = render_activation_readiness_summary_json(summary)
    markdown = render_activation_readiness_summary_markdown(summary)

    assert '"status_label": "live_limited_ready"' in payload
    assert '"live_dispatch_allowed": true' in payload
    assert "Status: live_limited_ready" in markdown
    assert "Live Dispatch Allowed: yes" in markdown


def test_json_snapshot_is_stable():
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

    assert render_activation_readiness_summary_json(summary) == """{
  "allowed_actions": [
    "prepare_dispatch_plan",
    "prepare_mailbox_draft",
    "review_registry",
    "view_dashboard"
  ],
  "blocking_reasons": [
    "Git command execution still depends on explicit operator approval.",
    "Heartbeat scheduling is intentionally blocked from unattended execution.",
    "Automated test command execution is not yet live-safe for unattended orchestration.",
    "Thread send integration is modeled only and not safe for unattended live sends yet."
  ],
  "live_dispatch_allowed": false,
  "mode": "prepare_dispatch",
  "next_safe_action": "Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.",
  "open_gap_count": 4,
  "operator_required": true,
  "status_label": "prepare_only"
}"""


def test_markdown_snapshot_is_stable():
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

    assert render_activation_readiness_summary_markdown(summary) == (
        "# Orchestration Activation Readiness\n"
        "\n"
        "Status: prepare_only\n"
        "Mode: prepare_dispatch\n"
        "Live Dispatch Allowed: no\n"
        "Open Gap Count: 4\n"
        "Operator Required: yes\n"
        "Next Safe Action: Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.\n"
        "\n"
        "Allowed Actions: prepare_dispatch_plan, prepare_mailbox_draft, review_registry, view_dashboard\n"
        "Blocking Reasons: Git command execution still depends on explicit operator approval.; Heartbeat scheduling is intentionally blocked from unattended execution.; Automated test command execution is not yet live-safe for unattended orchestration.; Thread send integration is modeled only and not safe for unattended live sends yet."
    )
