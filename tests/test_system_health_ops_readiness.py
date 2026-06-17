from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_ops_readiness import (
    OpsReadinessItem,
    OpsReadinessReport,
    OpsReadinessStatus,
    build_foundation_ops_readiness_report,
)


def test_foundation_report_is_conservative_warn_not_go():
    report = build_foundation_ops_readiness_report()

    assert report.mode == "foundation"
    assert report.overall_status == "warn"
    assert any(item.item_id == "no_auto_repair" and item.status == OpsReadinessStatus.WARN for item in report.items)


def test_fail_item_forces_no_go():
    report = OpsReadinessReport.create(
        mode="foundation",
        items=(
            OpsReadinessItem.create(
                item_id="host_agent_boundary",
                status="pass",
                summary="host boundary is respected",
            ),
            OpsReadinessItem.create(
                item_id="token_logging_blocked",
                status="fail",
                summary="secret-safe logging policy is missing",
                next_action="block rollout until token logging guardrails are defined",
            ),
        ),
    )

    assert report.overall_status == "no_go"


def test_all_pass_items_allow_go():
    report = OpsReadinessReport.create(
        mode="foundation",
        items=tuple(
            OpsReadinessItem.create(
                item_id=item_id,
                status="pass",
                summary=f"{item_id} is satisfied",
            )
            for item_id in (
                "host_agent_boundary",
                "no_core_host_commands",
                "no_socket_mount_required",
                "token_logging_blocked",
                "collector_unknown_safe",
                "alert_dedupe_defined",
                "no_auto_repair",
            )
        ),
    )

    assert report.overall_status == "go"


def test_warn_or_unknown_items_require_next_action():
    warn_item = OpsReadinessItem.create(
        item_id="no_auto_repair",
        status="warn",
        summary="auto repair remains disabled",
    )
    unknown_item = OpsReadinessItem.create(
        item_id="collector_unknown_safe",
        status="unknown",
        summary="unknown-safe handling needs confirmation",
    )

    assert warn_item.next_action
    assert unknown_item.next_action


def test_invalid_item_id_is_rejected():
    try:
        OpsReadinessItem.create(
            item_id="network_socket_access",
            status="pass",
            summary="invalid item",
        )
    except HealthAgentInterfaceError as exc:
        assert "item_id" in str(exc)
    else:
        raise AssertionError("expected HealthAgentInterfaceError")


def test_to_dict_is_stable():
    report = build_foundation_ops_readiness_report()

    assert report.to_dict() == {
        "mode": "foundation",
        "overall_status": "warn",
        "items": (
            {
                "item_id": "alert_dedupe_defined",
                "status": "pass",
                "summary": "alert dedupe and cooldown decisions are modeled explicitly",
                "next_action": "",
            },
            {
                "item_id": "collector_unknown_safe",
                "status": "pass",
                "summary": "collector parsers degrade to unknown or unsupported instead of guessing health",
                "next_action": "",
            },
            {
                "item_id": "host_agent_boundary",
                "status": "pass",
                "summary": "host access remains outside the core application boundary",
                "next_action": "",
            },
            {
                "item_id": "no_auto_repair",
                "status": "warn",
                "summary": "foundation mode intentionally omits auto-repair and operator execution hooks",
                "next_action": "keep operator-owned remediation outside the model layer until runtime review is approved",
            },
            {
                "item_id": "no_core_host_commands",
                "status": "pass",
                "summary": "core health models do not execute host commands directly",
                "next_action": "",
            },
            {
                "item_id": "no_socket_mount_required",
                "status": "pass",
                "summary": "container and host checks do not require socket mounts in foundation mode",
                "next_action": "",
            },
            {
                "item_id": "token_logging_blocked",
                "status": "pass",
                "summary": "alerting and telegram models avoid token-handling and secret logging paths",
                "next_action": "",
            },
        ),
    }
