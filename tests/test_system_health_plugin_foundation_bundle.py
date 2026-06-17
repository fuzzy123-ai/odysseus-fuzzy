from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_plugin_foundation_bundle import (
    FoundationBundleReadiness,
    FoundationComponent,
    FoundationComponentStatus,
    build_foundation_bundle_readiness,
)


def test_default_foundation_bundle_marks_foundation_ready_but_runtime_gates_closed():
    bundle = build_foundation_bundle_readiness()

    assert bundle.mode == "foundation"
    assert bundle.foundation_ready is True
    runtime_components = {item.component_id: item for item in bundle.components if item.component_id in {
        "host_agent_runtime",
        "telegram_delivery",
        "ui_integration",
        "privileged_host_access",
        "real_network_io",
    }}
    assert runtime_components["host_agent_runtime"].status == FoundationComponentStatus.DEFERRED
    assert runtime_components["privileged_host_access"].status == FoundationComponentStatus.BLOCKED


def test_foundation_ready_fails_if_foundation_component_is_not_ready():
    base = list(build_foundation_bundle_readiness().components)
    base = [
        FoundationComponent.create(
            component_id=item.component_id,
            status=("planned" if item.component_id == "rule_engine" else item.status),
            summary=item.summary,
            next_action=item.next_action,
        )
        for item in base
    ]

    bundle = FoundationBundleReadiness.create(mode="foundation", components=base)

    assert bundle.foundation_ready is False


def test_foundation_ready_fails_if_runtime_gate_is_falsely_ready():
    base = list(build_foundation_bundle_readiness().components)
    base = [
        FoundationComponent.create(
            component_id=item.component_id,
            status=("ready" if item.component_id == "real_network_io" else item.status),
            summary=item.summary,
            next_action=item.next_action,
        )
        for item in base
    ]

    bundle = FoundationBundleReadiness.create(mode="foundation", components=base)

    assert bundle.foundation_ready is False


def test_missing_required_component_is_rejected():
    base = [item for item in build_foundation_bundle_readiness().components if item.component_id != "dashboard_summary"]

    try:
        FoundationBundleReadiness.create(mode="foundation", components=base)
    except HealthAgentInterfaceError as exc:
        assert "missing required ids" in str(exc)
    else:
        raise AssertionError("expected HealthAgentInterfaceError")


def test_to_dict_is_stable():
    bundle = build_foundation_bundle_readiness()

    assert bundle.to_dict() == {
        "mode": "foundation",
        "foundation_ready": True,
        "components": (
            {
                "component_id": "advanced_collectors",
                "status": "ready",
                "summary": "advanced collector parsers for fixture payloads are available",
                "next_action": "",
            },
            {
                "component_id": "auto_alerting_decision",
                "status": "ready",
                "summary": "auto-alerting decision logic exists without push execution",
                "next_action": "",
            },
            {
                "component_id": "basic_collectors",
                "status": "ready",
                "summary": "basic collector reading models are available",
                "next_action": "",
            },
            {
                "component_id": "container_runtime_adapter",
                "status": "ready",
                "summary": "container runtime adapter model exists for fixture data",
                "next_action": "",
            },
            {
                "component_id": "dashboard_summary",
                "status": "ready",
                "summary": "dashboard summary model is available for sanitized status aggregation",
                "next_action": "",
            },
            {
                "component_id": "host_agent_runtime",
                "status": "deferred",
                "summary": "real host-agent runtime execution stays outside foundation mode",
                "next_action": "leave this capability outside foundation mode until runtime approval exists",
            },
            {
                "component_id": "interface",
                "status": "ready",
                "summary": "host-facing health snapshot interface model exists",
                "next_action": "",
            },
            {
                "component_id": "ops_readiness",
                "status": "ready",
                "summary": "ops readiness checklist model is available",
                "next_action": "",
            },
            {
                "component_id": "privileged_host_access",
                "status": "blocked",
                "summary": "privileged host access remains blocked in foundation mode",
                "next_action": "keep privileged host access disabled until an explicit operator-approved runtime phase exists",
            },
            {
                "component_id": "real_network_io",
                "status": "blocked",
                "summary": "real network or token-bearing delivery remains blocked in foundation mode",
                "next_action": "leave network delivery disabled until operator-owned runtime transport is approved",
            },
            {
                "component_id": "rule_engine",
                "status": "ready",
                "summary": "rule evaluation and alert synthesis models are available",
                "next_action": "",
            },
            {
                "component_id": "telegram_delivery",
                "status": "deferred",
                "summary": "telegram push or delivery runtime is intentionally not enabled",
                "next_action": "leave this capability outside foundation mode until runtime approval exists",
            },
            {
                "component_id": "telegram_pull",
                "status": "ready",
                "summary": "telegram pull command rendering model is available without bot delivery",
                "next_action": "",
            },
            {
                "component_id": "ui_integration",
                "status": "planned",
                "summary": "ui or route integration is not part of the foundation bundle",
                "next_action": "leave this capability outside foundation mode until runtime approval exists",
            },
        ),
    }
