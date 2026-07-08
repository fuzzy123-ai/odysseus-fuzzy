from datetime import datetime, timezone

import pytest

from src.mcp_audit_events import MCP_AUDIT_EVENT_SCHEMA, McpAuditEventError, build_mcp_audit_event


def test_mcp_audit_event_redacts_sensitive_metadata_and_raw_args():
    event = build_mcp_audit_event({
        "method": "tools/call",
        "status": "ok",
        "tool": "list_sessions",
        "client_id": "codex-local",
        "reason": "tool call completed",
        "timestamp": "2026-07-06T12:00:00Z",
        "duration_ms": "42",
        "metadata": {
            "token": "secret-token-value",
            "arguments": {"query": "private"},
            "note": "safe summary",
        },
    })

    payload = event.to_dict()

    assert payload["schema"] == MCP_AUDIT_EVENT_SCHEMA
    assert payload["timestamp"] == "2026-07-06T12:00:00Z"
    assert payload["metadata"]["token"] == "[redacted]"
    assert payload["metadata"]["arguments"] == "[redacted-structured-value]"
    assert payload["metadata"]["note"] == "safe summary"
    assert payload["raw_arguments_visible"] is False
    assert payload["token_value_visible"] is False
    assert payload["secret_value_visible"] is False


def test_mcp_audit_event_marks_required_gate_for_hidden_tool():
    event = build_mcp_audit_event({
        "method": "tools/call",
        "status": "blocked",
        "tool_name": "read_email",
        "client_id": "codex-local",
        "reason": "not exposed",
        "timestamp": datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
    })

    payload = event.to_dict()

    assert payload["category"] == "private_read"
    assert payload["required_gate"] == "MCP-PRIVATE-READ-GO"
    assert payload["live_client_connection_allowed"] is False


def test_mcp_audit_event_accepts_unknown_status_as_error():
    event = build_mcp_audit_event({
        "method": "resources/read",
        "status": "sent to user",
        "timestamp": "2026-07-06T12:00:00Z",
    })

    assert event.to_dict()["status"] == "error"


def test_mcp_audit_event_rejects_non_object_payload():
    with pytest.raises(McpAuditEventError):
        build_mcp_audit_event(["not", "object"])
