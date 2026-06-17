from src.orchestration_operator_activation import (
    ActivationMode,
    OperatorActivationPolicy,
    build_orchestration_activation_plan,
)
from src.orchestration_runtime_readiness import (
    RuntimeCapability,
    build_current_runtime_readiness_report,
    build_runtime_readiness_report,
)


def test_default_current_readiness_does_not_allow_live_dispatch():
    plan = build_orchestration_activation_plan(
        readiness=build_current_runtime_readiness_report(),
        policy=OperatorActivationPolicy.create(
            requested_mode="live_dispatch_limited",
            operator_approved=True,
            allow_live_dispatch=True,
        ),
    )

    assert plan.mode == ActivationMode.LIVE_DISPATCH_LIMITED
    assert plan.ok is False
    assert any(item.action.value == "execute_live_dispatch" and item.decision.value == "block" for item in plan.blocked_actions)


def test_read_only_mode_allows_only_read_only_actions():
    plan = build_orchestration_activation_plan(
        readiness=build_current_runtime_readiness_report(),
        policy=OperatorActivationPolicy.create(
            requested_mode="read_only",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
    )

    allowed_actions = {item.action.value for item in plan.allowed_actions}
    blocked_actions = {item.action.value for item in plan.blocked_actions}

    assert allowed_actions == {"review_registry", "view_dashboard"}
    assert "prepare_mailbox_draft" in blocked_actions
    assert "execute_live_dispatch" in blocked_actions


def test_prepare_dispatch_allows_preparation_without_send():
    plan = build_orchestration_activation_plan(
        readiness=build_current_runtime_readiness_report(),
        policy=OperatorActivationPolicy.create(
            requested_mode="prepare_dispatch",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
    )

    prepare_actions = {item.action.value: item.decision.value for item in plan.allowed_actions}
    blocked_actions = {item.action.value for item in plan.blocked_actions}

    assert prepare_actions["prepare_mailbox_draft"] == "prepare_only"
    assert prepare_actions["prepare_dispatch_plan"] == "prepare_only"
    assert "execute_live_dispatch" in blocked_actions
    assert "confirm_dispatch" in blocked_actions


def test_live_dispatch_requires_clean_readiness_and_explicit_operator_approval():
    clean_readiness = build_runtime_readiness_report(
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
        readiness=clean_readiness,
        policy=OperatorActivationPolicy.create(
            requested_mode="live_dispatch_limited",
            operator_approved=True,
            allow_live_dispatch=True,
        ),
    )

    assert plan.ok is True
    assert any(item.action.value == "execute_live_dispatch" and item.decision.value == "allow" for item in plan.allowed_actions)


def test_stable_dict_output():
    plan = build_orchestration_activation_plan(
        readiness=build_current_runtime_readiness_report(),
        policy=OperatorActivationPolicy.create(
            requested_mode="prepare_dispatch",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
    )

    assert plan.to_dict() == {
        "mode": "prepare_dispatch",
        "open_gap_count": 4,
        "ok": False,
        "next_safe_action": "Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.",
        "allowed_actions": (
            {
                "action": "prepare_dispatch_plan",
                "decision": "prepare_only",
                "reason": "Dispatch plans can be assembled without executing live hooks.",
            },
            {
                "action": "prepare_mailbox_draft",
                "decision": "prepare_only",
                "reason": "Mailbox drafts can be prepared without sending.",
            },
            {
                "action": "review_registry",
                "decision": "allow",
                "reason": "Registry review stays read-only and does not trigger hooks.",
            },
            {
                "action": "view_dashboard",
                "decision": "allow",
                "reason": "Dashboard inspection remains read-only and safe.",
            },
        ),
        "blocked_actions": (
            {
                "action": "confirm_dispatch",
                "decision": "block",
                "reason": "Prepare-dispatch mode does not allow send confirmation.",
            },
            {
                "action": "execute_live_dispatch",
                "decision": "block",
                "reason": "Prepare-dispatch mode never executes live dispatch.",
            },
        ),
    }
