from src.orchestration_activation_bundle import (
    build_current_orchestration_activation_bundle,
    build_orchestration_activation_bundle,
)
from src.orchestration_operator_activation import OperatorActivationPolicy
from src.orchestration_runtime_readiness import RuntimeCapability, build_runtime_readiness_report


def test_current_bundle_is_not_live_ready_and_contains_prepare_language():
    bundle = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:00:00Z",
    )

    assert bundle.summary.live_dispatch_allowed is False
    assert bundle.summary.status_label == "prepare_only"
    assert '"status_label": "prepare_only"' in bundle.json_snapshot
    assert "Status: prepare_only" in bundle.markdown_snapshot
    assert "prepare_dispatch" in bundle.markdown_snapshot


def test_custom_clean_bundle_can_be_live_ready():
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
    bundle = build_orchestration_activation_bundle(
        readiness_report=clean_report,
        policy=OperatorActivationPolicy.create(
            requested_mode="live_dispatch_limited",
            operator_approved=True,
            allow_live_dispatch=True,
        ),
        label="Clean AUTO Bundle",
        generated_at="2026-06-17T12:05:00Z",
    )

    assert bundle.summary.live_dispatch_allowed is True
    assert bundle.summary.status_label == "live_limited_ready"
    assert '"status_label": "live_limited_ready"' in bundle.json_snapshot
    assert "Status: live_limited_ready" in bundle.markdown_snapshot


def test_stable_dict_output():
    bundle = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:00:00Z",
    )

    payload = bundle.to_dict()

    assert payload["label"] == "Morning AUTO Bundle"
    assert payload["generated_at"] == "2026-06-17T12:00:00Z"
    assert payload["summary"] == bundle.summary.to_dict()
    assert payload["activation_plan"] == bundle.activation_plan.to_dict()
    assert payload["readiness_report"] == bundle.readiness_report.to_dict()
    assert payload["json_snapshot"] == bundle.json_snapshot
    assert payload["markdown_snapshot"] == bundle.markdown_snapshot


def test_bundle_builder_has_no_live_side_effect_flags():
    bundle = build_current_orchestration_activation_bundle()

    assert bundle.activation_plan.ok is False
    assert all(item.live_hook is False for item in bundle.readiness_report.capabilities)
    assert any(action["action"] == "execute_live_dispatch" and action["decision"] == "block" for action in bundle.activation_plan.to_dict()["blocked_actions"])
