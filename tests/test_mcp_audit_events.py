from datetime import datetime, timezone

import pytest

from src.mcp_audit_events import (
    MCP_AUDIT_EVENT_SCHEMA,
    PLANNING_SECTION_AUDIT_SCHEMA,
    McpAuditEventError,
    build_mcp_audit_event,
    build_planning_section_audit_descriptor,
)
from src.mcp_server_tool_policy import McpToolPolicyOptions


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


def test_planning_audit_records_argument_shape_hash_and_profile_ref_without_values():
    event = build_mcp_audit_event({
        "method": "tools/call",
        "status": "ok",
        "tool": "planning_read_roadmap",
        "client_id": "profile:codex-planning",
        "reason": "planning_read_explicitly_allowed",
        "arguments": {
            "source_id_or_path": "C:\\private\\roadmap.json",
            "include_nodes": True,
            "token": "synthetic-secret-value",
        },
        "options": McpToolPolicyOptions(allow_planning_reads=True),
    })

    payload = event.to_dict()
    encoded = str(payload)

    assert payload["category"] == "planning_readonly"
    assert payload["required_gate"] == ""
    assert payload["client_id"] == "profile:codex-planning"
    assert payload["argument_fields"] == ("include_nodes", "source_id_or_path", "token")
    assert payload["argument_count"] == 3
    assert payload["argument_hash"].startswith("sha256:")
    assert payload["raw_arguments_visible"] is False
    assert "synthetic-secret-value" not in encoded
    assert "C:\\private" not in encoded


def test_hidden_planning_audit_names_readonly_gate_and_redacts_reason():
    event = build_mcp_audit_event({
        "method": "tools/call",
        "status": "blocked",
        "tool": "planning_list_roadmaps",
        "client_id": "C:\\private\\client-profile.json",
        "reason": "token=synthetic-secret-value C:\\private\\roadmap.json",
        "arguments": {"query": "private value"},
    })

    payload = event.to_dict()
    encoded = str(payload)

    assert payload["required_gate"] == "PLANNING-MCP-READONLY-GO"
    assert payload["client_id"].startswith("client:")
    assert "synthetic-secret-value" not in encoded
    assert "C:\\private" not in encoded


def test_planning_section_audit_descriptor_records_only_value_free_field_shape():
    first = build_planning_section_audit_descriptor(
        client_id="profile:spark",
        arguments={
            "project_id": "alpha",
            "roadmap_id": "map-one",
            "section_id": "tasks",
            "task_id": "task-one",
            "include_memory": True,
        },
    )
    second = build_planning_section_audit_descriptor(
        client_id="profile:spark",
        arguments={
            "project_id": "beta",
            "roadmap_id": "map-two",
            "section_id": "gates",
            "task_id": "different-value",
            "include_memory": False,
        },
    )

    assert first["schema"] == PLANNING_SECTION_AUDIT_SCHEMA
    assert first["client_id"] == "profile:spark"
    assert first["tool_name"] == "planning_get_section_context_pack"
    assert first["argument_fields"] == (
        "include_memory", "project_id", "roadmap_id", "section_id", "task_id",
    )
    assert first["argument_count"] == 5
    assert first["argument_hash"] == second["argument_hash"]
    assert first["descriptor_id"] == second["descriptor_id"]
    assert first["persisted"] is False
    assert first["events_emitted"] is False
    assert first["raw_arguments_visible"] is False
    assert first["section_values_visible"] is False


def test_planning_section_audit_descriptor_hashes_private_client_and_ignores_unknown_fields():
    descriptor = build_planning_section_audit_descriptor(
        client_id="C:\\private\\spark-profile.json",
        arguments={
            "section_id": "data",
            "token": "synthetic-secret-value",
            "private_path": "C:\\private\\roadmap.json",
        },
    )
    encoded = str(descriptor)

    assert descriptor["client_id"].startswith("client:")
    assert descriptor["argument_fields"] == ("section_id",)
    assert descriptor["argument_count"] == 1
    assert descriptor["private_paths_visible"] is False
    assert descriptor["token_value_visible"] is False
    assert descriptor["secret_value_visible"] is False
    assert "synthetic-secret-value" not in encoded
    assert "C:\\private" not in encoded
