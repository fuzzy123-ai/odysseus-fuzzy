"""Read-only Ops Console snapshot contract."""

from __future__ import annotations

from typing import Any, Mapping

from src.observability_alert_routing import build_observability_alert_routes
from src.observability_diagnostics_bridge import build_observability_diagnostic_packet
from src.ops_timeline_adapters import build_ops_timeline_from_sources
from src.security_remediation_actions import remediation_readiness
from src.security_response_policy import policy_readiness
from src.system_health_dashboard_summary import build_system_health_dashboard_summary


OPS_CONSOLE_SNAPSHOT_SCHEMA = "odysseus.ops_console.snapshot.v1"


def build_ops_console_snapshot(
    *,
    dashboard_summary: Any = None,
    diagnostic_packet: Mapping[str, Any] | None = None,
    alert_routes: Mapping[str, Any] | None = None,
    security_incident: Mapping[str, Any] | None = None,
    response_policy: Mapping[str, Any] | None = None,
    remediation_plan: Mapping[str, Any] | None = None,
    timeline_id: str = "ops-console-snapshot",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build one admin-facing, read-only ops snapshot.

    Inputs are optional and must already be redacted source-contract payloads.
    With no inputs, the snapshot returns conservative no-data/readiness packets
    and still performs no host, network, provider or remediation action.
    """

    dashboard = _dashboard_payload(dashboard_summary)
    diagnostics = diagnostic_packet if isinstance(diagnostic_packet, Mapping) else build_observability_diagnostic_packet(
        question="ops console snapshot",
        metrics_snapshot=None,
    )
    routes = alert_routes if isinstance(alert_routes, Mapping) else build_observability_alert_routes(())
    timeline = build_ops_timeline_from_sources(
        dashboard_summary=dashboard,
        diagnostic_packet=diagnostics,
        alert_routes=routes,
        security_incident=security_incident,
        response_policy=response_policy,
        remediation_plan=remediation_plan,
        timeline_id=timeline_id,
        generated_at=generated_at,
    )
    security_policy = policy_readiness()
    remediation = remediation_readiness()
    snapshot = {
        "schema": OPS_CONSOLE_SNAPSHOT_SCHEMA,
        "status": timeline["status"],
        "timeline": timeline,
        "source_states": {
            "system_health": _system_health_state(dashboard),
            "diagnostics": str(diagnostics.get("status") or "unknown"),
            "alert_routes": str(routes.get("status") or "unknown"),
            "security_policy": security_policy["status"],
            "remediation": remediation["status"],
        },
        "counts": {
            "timeline_events": timeline["event_count"],
            "required_gates": len(timeline["required_gates"]),
            "alert_routes": int(routes.get("route_count") or 0),
            "diagnostic_findings": len(tuple(diagnostics.get("findings") or ())),
        },
        "operator_gates": timeline["required_gates"],
        "security_policy_readiness": security_policy,
        "remediation_readiness": remediation,
        "raw_content_visible": False,
        "raw_logs_visible": False,
        "host_paths_visible": False,
        "tokens_visible": False,
        "chat_targets_visible": False,
        "live_queries_performed": False,
        "host_commands_performed": False,
        "writes_performed": False,
        "remediation_performed": False,
    }
    return snapshot


def _dashboard_payload(value: Any) -> Mapping[str, Any]:
    if value is None:
        return build_system_health_dashboard_summary().to_dict()
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        mapped = value.to_dict()
        return mapped if isinstance(mapped, Mapping) else build_system_health_dashboard_summary().to_dict()
    return build_system_health_dashboard_summary().to_dict()


def _system_health_state(dashboard: Mapping[str, Any]) -> str:
    return str(dashboard.get("overview_state") or "unknown")
