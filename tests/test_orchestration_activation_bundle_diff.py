from src.orchestration_activation_bundle import (
    build_current_orchestration_activation_bundle,
    build_orchestration_activation_bundle,
)
from src.orchestration_activation_bundle_diff import build_activation_bundle_diff
from src.orchestration_operator_activation import OperatorActivationPolicy
from src.orchestration_runtime_readiness import RuntimeCapability, build_runtime_readiness_report


def test_same_bundle_content_with_different_generated_at_is_unchanged():
    first = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:40:00Z",
    )
    second = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:45:00Z",
    )

    diff = build_activation_bundle_diff(first, second)

    assert diff.changed is False
    assert diff.digest_changed is False
    assert diff.status_changed is False
    assert diff.notes == ()


def test_status_change_and_blocker_resolution_are_detected():
    previous = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:40:00Z",
    )
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
    current = build_orchestration_activation_bundle(
        readiness_report=clean_report,
        policy=OperatorActivationPolicy.create(
            requested_mode="live_dispatch_limited",
            operator_approved=True,
            allow_live_dispatch=True,
        ),
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:50:00Z",
    )

    diff = build_activation_bundle_diff(previous, current)

    assert diff.changed is True
    assert diff.digest_changed is True
    assert diff.status_changed is True
    assert diff.previous_status == "prepare_only"
    assert diff.current_status == "live_limited_ready"
    assert diff.new_blockers == ()
    assert diff.resolved_blockers


def test_next_safe_action_change_is_detected():
    previous = build_current_orchestration_activation_bundle()
    current = build_current_orchestration_activation_bundle(
        policy=OperatorActivationPolicy.create(
            requested_mode="read_only",
            operator_approved=False,
            allow_live_dispatch=False,
        )
    )

    diff = build_activation_bundle_diff(previous, current)

    assert diff.changed is True
    assert diff.next_safe_action_changed is False
    assert diff.status_changed is True


def test_stable_dict_output():
    previous = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:40:00Z",
    )
    current = build_current_orchestration_activation_bundle(
        policy=OperatorActivationPolicy.create(
            requested_mode="read_only",
            operator_approved=False,
            allow_live_dispatch=False,
        ),
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:50:00Z",
    )

    diff = build_activation_bundle_diff(previous, current)

    assert diff.to_dict() == {
        "changed": True,
        "digest_changed": True,
        "status_changed": True,
        "previous_status": "prepare_only",
        "current_status": "read_only",
        "new_blockers": (),
        "resolved_blockers": (),
        "next_safe_action_changed": False,
        "notes": ("bundle_digest_changed", "status_changed"),
    }
