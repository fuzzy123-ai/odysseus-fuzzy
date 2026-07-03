import json

from mcp_servers.debug_server import (
    DEBUG_TOOL_NAMES,
    build_debug_tool_contracts,
    call_debug_tool_contract,
    debug_tool_names,
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
