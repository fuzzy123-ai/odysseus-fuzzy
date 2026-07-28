import json
from types import SimpleNamespace

import mcp_servers.debug_server as debug_server
from mcp_servers.debug_server import (
    DEBUG_TOOL_NAMES,
    build_debug_tool_contracts,
    call_debug_tool_contract,
    configure_security_incident_store,
    configure_security_executor_kernel_for_tests,
    configure_default_security_incident_store,
    debug_tool_names,
)
from src.runtime_event_envelope import RUNTIME_EVENT_SCHEMA
from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_incident_store import SecurityIncidentStore
from src.security_executor_contracts import (
    SECURITY_EXECUTION_REQUEST_SCHEMA,
    SecurityExecutionRequest,
    build_rollback_descriptor,
)
from src.security_executor_kernel import SecurityExecutorKernel


def _store(tmp_path):
    store = SecurityIncidentStore(tmp_path / "incidents.sqlite", clock=lambda: 100.0)
    store.create_incident(incident_id="inc-mcp", incident_ref="evidence:sha256:" + "a" * 64, audit_ref="audit:sha256:" + "b" * 64)
    store.create_action(
        action_id="act-mcp", incident_id="inc-mcp", action_type="crowdsec_temp_block",
        scope_fingerprint="scope:sha256:" + "c" * 64, policy_revision="policy:sha256:" + "d" * 64,
        idempotency_key="idem-mcp", ttl_seconds=60, audit_ref="audit:sha256:" + "e" * 64,
    )
    return store


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
        if contract["name"] == "security_action_execute":
            assert annotations["read_only"] is False and annotations["high_risk"] is True
            assert annotations["default_disabled"] is True
            continue
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


def test_security_recommend_next_action_rejects_client_authority_object():
    result = call_debug_tool_contract("security_recommend_next_action", {"incident": _incident()})

    assert result["status"] == "blocked"
    assert result["reason"] == "client_authority_objects_rejected"
    assert result["writes_performed"] is False


def test_store_backed_mcp_list_read_trace_and_prepare_are_bounded_and_readonly(tmp_path):
    configure_security_incident_store(_store(tmp_path))
    try:
        listed = call_debug_tool_contract("security_incident_list", {"limit": 1})
        read = call_debug_tool_contract("security_incident_read", {"incident_id": "inc-mcp"})
        trace = call_debug_tool_contract("security_incident_trace", {"incident_id": "inc-mcp"})
        prepared = call_debug_tool_contract("security_action_prepare", {"action_id": "act-mcp", "expected_version": 1})
    finally:
        configure_security_incident_store(None)

    assert listed["status"] == read["status"] == trace["status"] == prepared["status"] == "success"
    assert listed["payload"]["incident_count"] == 1
    assert read["payload"]["incident"]["action_count"] == 1
    assert trace["payload"]["events"][0]["event_type"] == "incident_created"
    assert prepared["writes_performed"] is False
    encoded = json.dumps({"listed": listed, "read": read, "trace": trace}, sort_keys=True)
    assert "evidence:sha256:" not in encoded
    assert "scope:sha256:" not in encoded


def test_mcp_rejects_client_objects_in_schema_and_direct_calls(tmp_path):
    configure_security_incident_store(_store(tmp_path))
    try:
        result = call_debug_tool_contract("security_incident_read", {"incident_id": "inc-mcp", "incident": {"incident_id": "inc-mcp"}})
    finally:
        configure_security_incident_store(None)
    contracts = {item["name"]: item for item in build_debug_tool_contracts()}

    assert result["reason"] == "client_authority_objects_rejected"
    assert "incident" not in contracts["security_incident_read"]["inputSchema"]["properties"]
    assert "action" not in contracts["security_action_prepare"]["inputSchema"]["properties"]


def test_mcp_rejects_smuggled_server_objects_without_echoing_them(tmp_path):
    store = _store(tmp_path)
    configure_security_incident_store(store)
    try:
        result = call_debug_tool_contract("security_incident_list", {"store": store})
    finally:
        configure_security_incident_store(None)

    assert result["status"] == "blocked"
    assert result["reason"] == "client_arguments_rejected"
    assert "SecurityIncidentStore" not in json.dumps(result, sort_keys=True)


def _execute_request():
    request = {
        "schema": SECURITY_EXECUTION_REQUEST_SCHEMA, "action_id": "act-mcp", "action_version": 3,
        "action_type": "crowdsec_temp_block", "scope_fingerprint": "scope:sha256:" + "c" * 64,
        "policy_revision": "policy:sha256:" + "d" * 64, "policy_gate": "crowdsec-remediation-go",
        "timeout_seconds": 30, "idempotency_key": "idem-mcp", "rollback_descriptor": "rollback:sha256:" + "f" * 64,
        "expires_at": 160.0,
    }
    request["rollback_descriptor"] = build_rollback_descriptor(SecurityExecutionRequest.from_mapping(request))
    return request


def test_security_action_execute_is_the_only_effectful_route_and_is_disabled_by_default():
    contracts = {item["name"]: item for item in build_debug_tool_contracts()}
    result = call_debug_tool_contract("security_action_execute", _execute_request())

    assert "security_action_approve" not in contracts and "security_action_deny" not in contracts
    assert result["status"] == "blocked" and result["reason"] == "effectful_mcp_disabled"
    assert result["read_only"] is False and result["writes_performed"] is False


def test_effectful_mcp_execute_is_disabled_with_a_configured_store(tmp_path):
    configure_security_incident_store(_store(tmp_path))
    try:
        result = call_debug_tool_contract("security_action_execute", _execute_request())
    finally:
        configure_security_incident_store(None)

    assert result["reason"] == "effectful_mcp_disabled"
    assert result["writes_performed"] is False and result["allowed_to_execute"] is False


def test_injected_fake_kernel_is_the_only_mcp_execute_enablement(tmp_path):
    store = _store(tmp_path)
    prepared = store.transition(action_id="act-mcp", expected_version=1, target_state="prepared", audit_ref="audit:sha256:" + "f" * 64)
    store.approve(
        action_id="act-mcp", expected_version=prepared.version, approval_id="approval-mcp",
        approval_ref="approval:sha256:" + "1" * 64, scope_fingerprint="scope:sha256:" + "c" * 64,
        policy_revision="policy:sha256:" + "d" * 64, audit_ref="audit:sha256:" + "2" * 64,
    )
    calls = []
    def fake(request):
        calls.append(request.action_id)
    fake.security_executor_test_fake = True
    configure_security_executor_kernel_for_tests(SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": fake}, clock=lambda: 100.0))
    try:
        result = call_debug_tool_contract("security_action_execute", _execute_request())
    finally:
        configure_security_executor_kernel_for_tests(None)

    assert result["status"] == "success" and result["read_only"] is False
    assert result["verified"] is False and result["verification_state"] == "not_verified"
    assert calls == ["act-mcp"]


class _BrokenStore:
    def audit_events(self, *_args):
        raise RuntimeError("private backend detail")

    def get_incident(self, _incident_id):
        raise RuntimeError("private backend detail")


def test_mcp_long_history_selects_before_bounding_and_uses_exact_action_lookup():
    records = [SimpleNamespace(sequence=index, incident_id=f"inc-{index:03}", action_id=None, event_type="incident_created") for index in range(1, 102)]
    records.extend((
        SimpleNamespace(sequence=102, incident_id="inc-last", action_id=None, event_type="incident_created"),
        SimpleNamespace(sequence=103, incident_id="inc-last", action_id="act-last", action_version=1, event_type="action_proposed"),
    ))

    class _Store:
        def audit_events(self, action_id=None):
            return tuple(record for record in records if action_id is None or record.action_id == action_id)

        def get_incident(self, incident_id):
            if incident_id == "inc-last":
                return SimpleNamespace(incident_id=incident_id, version=1)
            return SimpleNamespace(incident_id=incident_id, version=1)

    configure_security_incident_store(_Store())
    try:
        listed = call_debug_tool_contract("security_incident_list", {"limit": 1})
        trace = call_debug_tool_contract("security_incident_trace", {"incident_id": "inc-last", "limit": 10})
        review = call_debug_tool_contract("security_action_prepare", {"action_id": "act-last", "expected_version": 1})
    finally:
        configure_security_incident_store(None)

    assert listed["payload"]["incidents"][0]["incident_id"] == "inc-last"
    assert trace["payload"]["event_count"] == 2
    assert review["status"] == "success"


def test_mcp_broken_store_fails_closed_without_exception_text():
    configure_security_incident_store(_BrokenStore())
    try:
        results = (
            call_debug_tool_contract("security_incident_list", {}),
            call_debug_tool_contract("security_incident_read", {"incident_id": "inc-broken"}),
            call_debug_tool_contract("security_incident_trace", {"incident_id": "inc-broken"}),
            call_debug_tool_contract("security_action_prepare", {"action_id": "act-broken", "expected_version": 1}),
        )
    finally:
        configure_security_incident_store(None)

    assert all(result["reason"] == "incident_store_unavailable" for result in results)
    assert "private backend detail" not in json.dumps(results, sort_keys=True)


def test_mcp_explicit_startup_provider_configures_server_owned_store(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(debug_server, "create_default_security_incident_store", lambda: store)
    configure_security_incident_store(None)

    configure_default_security_incident_store()
    result = call_debug_tool_contract("security_incident_read", {"incident_id": "inc-mcp"})
    configure_security_incident_store(None)

    assert result["status"] == "success"
