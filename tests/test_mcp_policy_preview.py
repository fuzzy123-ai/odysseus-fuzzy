from datetime import datetime, timedelta, timezone

from src.mcp_client_profiles import McpClientProfile
from src.mcp_policy_preview import MCP_POLICY_PREVIEW_SCHEMA, build_mcp_policy_preview


FUTURE = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_policy_preview_summarizes_default_exposure_and_required_gates():
    preview = build_mcp_policy_preview([
        "list_sessions",
        "read_email",
        "read_file",
        "odysseus_call",
        "bash",
        "new_tool",
    ])
    payload = preview.to_dict()

    assert payload["schema"] == MCP_POLICY_PREVIEW_SCHEMA
    assert payload["exposed_count"] == 1
    assert payload["hidden_count"] == 5
    assert payload["required_gates"] == (
        "MCP-FILESYSTEM-READ-GO",
        "MCP-GENERIC-API-GO",
        "MCP-HIGH-RISK-NO-GO",
        "MCP-PRIVATE-READ-GO",
        "MCP-UNCLASSIFIED-TOOL-GO",
    )
    assert payload["live_client_connection_allowed"] is False
    assert payload["token_value_visible"] is False
    assert payload["secret_value_visible"] is False


def test_policy_preview_uses_active_client_profile_scopes():
    profile = McpClientProfile.create(
        client_id="codex-local",
        label="Codex local",
        owner="alice",
        scopes=["private_reads", "filesystem_reads"],
        enabled=True,
        reason="bounded local review",
        expires_at=FUTURE + timedelta(hours=1),
    )

    preview = build_mcp_policy_preview(
        ["read_email", "read_file", "odysseus_call"],
        client_profile=profile,
    )

    by_name = {item["tool_name"]: item for item in preview.to_dict()["tools"]}
    assert by_name["read_email"]["exposed"] is True
    assert by_name["read_file"]["exposed"] is True
    assert by_name["odysseus_call"]["exposed"] is False
    assert by_name["odysseus_call"]["required_gate"] == "MCP-GENERIC-API-GO"


def test_policy_preview_inactive_profile_does_not_grant_scopes():
    profile = McpClientProfile.create(
        client_id="codex-local",
        label="Codex local",
        scopes=["private_reads"],
        enabled=False,
    )

    preview = build_mcp_policy_preview(["read_email"], client_profile=profile)

    payload = preview.to_dict()
    assert payload["enabled_profile_active"] is False
    assert payload["tools"][0]["exposed"] is False
    assert payload["tools"][0]["required_gate"] == "MCP-PRIVATE-READ-GO"


def test_policy_preview_accepts_profile_payload_without_exposing_secrets():
    preview = build_mcp_policy_preview(
        ["create_document", "list_sessions"],
        client_profile={
            "id": "client",
            "name": "Client",
            "owner": "alice",
            "enabled": True,
            "owner_scoped_writes": True,
            "reason": "bounded write preview",
        },
    )

    payload = preview.to_dict()
    assert payload["client_profile"]["client_id"] == "client"
    assert payload["client_profile"]["token_value_visible"] is False
    assert payload["client_profile"]["secret_value_visible"] is False
    assert payload["tools"][0]["tool_name"] == "create_document"
    assert payload["tools"][0]["exposed"] is True
    assert payload["tools"][1]["tool_name"] == "list_sessions"
    assert payload["tools"][1]["exposed"] is True
