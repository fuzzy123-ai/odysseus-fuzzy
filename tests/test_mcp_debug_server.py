import json

from mcp_servers.debug_server import (
    DEBUG_TOOL_NAMES,
    build_debug_tool_contracts,
    call_debug_tool_contract,
    debug_tool_names,
)
from src.runtime_event_envelope import RUNTIME_EVENT_SCHEMA
from src.security_incident_model import build_recommended_action, build_security_incident


def _incident():
    return build_security_incident(
        level=3,
        severity="high",
        confidence=0.86,
        status="candidate",
        trigger="service_down_security_relevant",
        affected_surfaces=("ops",),
        correlation_ids=("corr-ops-1",),
        evidence_refs=("event-1",),
        recommended_actions=(
            build_recommended_action(
                action_type="redacted_debug_bundle",
                summary="Prepare redacted debug bundle",
                risk="Read-only diagnostic",
                action_id="act-debug",
            ),
            build_recommended_action(
                action_type="service_restart",
                summary="Prepare service restart recommendation",
                risk="Requires explicit operator confirmation",
                action_id="act-restart",
            ),
        ),
        incident_id="inc-mcp",
    )


def test_debug_server_exposes_bounded_readonly_contracts():
    contracts = build_debug_tool_contracts()

    assert debug_tool_names() == DEBUG_TOOL_NAMES
    assert {contract["name"] for contract in contracts} == set(DEBUG_TOOL_NAMES)
    for contract in contracts:
        annotations = contract["annotations"]
        assert annotations["read_only"] is True
        assert annotations["redacted_output"] is True
        assert annotations["bounded"] is True
        assert annotations["no_raw_private_content"] is True
        assert contract["inputSchema"]["additionalProperties"] is False


def test_debug_server_returns_redacted_readiness_blocker_without_writes():
    result = call_debug_tool_contract(
        "debug_trace_by_correlation_id",
        {"correlation_id": "corr-safe", "limit": 500},
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "event_index_not_configured"
    assert result["read_only"] is True
    assert result["writes_performed"] is False
    assert result["raw_content_visible"] is False
    assert result["limit"] == 100
    assert result["records"] == ()


def test_debug_server_hashes_unsafe_arguments():
    result = call_debug_tool_contract(
        "debug_trace_by_task_id",
        {"task_id": "C:/Users/private/task.json", "limit": "bad"},
    )
    encoded = json.dumps(result, sort_keys=True)

    assert result["limit"] == 20
    assert "C:/Users/private" not in encoded
    assert result["query_ref"].startswith("sha256:")


def test_debug_server_unknown_tool_is_blocked():
    result = call_debug_tool_contract("service_restart", {})

    assert result["status"] == "blocked"
    assert result["reason"] == "unknown_debug_tool"
    assert result["writes_performed"] is False


def test_observability_query_tools_are_readonly_and_config_gated():
    prometheus = call_debug_tool_contract("prometheus_query_readonly", {"query": "up", "limit": 500})
    loki = call_debug_tool_contract("loki_query_readonly", {"query": '{surface="telegram"}'})
    grafana = call_debug_tool_contract("grafana_dashboard_summary", {"dashboard_uid": "ops-main"})

    assert prometheus["status"] == "blocked"
    assert prometheus["reason"] == "prometheus_not_configured"
    assert prometheus["limit"] == 100
    assert prometheus["read_only"] is True
    assert prometheus["writes_performed"] is False
    assert "up" not in json.dumps(prometheus, sort_keys=True)
    assert loki["status"] == "blocked"
    assert loki["reason"] == "loki_not_configured"
    assert loki["writes_performed"] is False
    assert grafana["status"] == "blocked"
    assert grafana["reason"] == "grafana_client_not_configured"


def test_security_policy_readiness_is_readonly():
    result = call_debug_tool_contract("security_policy_readiness", {})

    assert result["status"] == "success"
    assert result["read_only"] is True
    assert result["writes_performed"] is False
    assert result["allowed_to_execute"] is False
    assert result["payload"]["status"] == "ready"
    assert result["payload"]["raw_content_visible"] is False


def test_security_recent_anomalies_classifies_redacted_events():
    events = [
        {
            "schema": RUNTIME_EVENT_SCHEMA,
            "event_id": "evt-1",
            "surface": "ops",
            "component": "podman",
            "event_type": "service_down",
            "status": "failed",
            "severity": "error",
            "correlation_id": "corr-ops-1",
            "raw_content_visible": False,
        }
    ]

    result = call_debug_tool_contract("security_recent_anomalies", {"events": events})

    assert result["status"] == "success"
    assert result["payload"]["incident_count"] == 1
    assert result["payload"]["incidents"][0]["raw_content_visible"] is False
    assert result["writes_performed"] is False


def test_security_recommend_next_action_prepares_policy_and_notification_only():
    result = call_debug_tool_contract("security_recommend_next_action", {"incident": _incident()})

    assert result["status"] == "success"
    assert result["reason"] == "policy_and_notification_prepared"
    assert result["writes_performed"] is False
    assert result["allowed_to_execute"] is False
    assert result["payload"]["policy"]["allowed_to_execute"] is False
    assert result["payload"]["notification"]["delivery_performed"] is False
    assert "act-restart" in result["payload"]["notification"]["message"]


def test_security_action_execute_is_exposed_but_blocked_without_store_or_gate():
    result = call_debug_tool_contract("security_action_execute", {"action_id": "act-restart"})

    assert result["status"] == "blocked"
    assert result["reason"] == "action_store_not_configured"
    assert result["writes_performed"] is False
