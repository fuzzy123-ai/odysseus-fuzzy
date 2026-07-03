from src.mcp_server_tool_policy import (
    ALWAYS_DENIED_TOOLS,
    DEBUG_READONLY_TOOLS,
    McpToolPolicyOptions,
    classify_mcp_tool,
    exposed_mcp_tool_names,
    filter_mcp_tools,
)


def test_mcp_policy_exposes_only_small_default_surface():
    names = exposed_mcp_tool_names([
        "list_sessions",
        "list_models",
        "odysseus_notify_user",
        "web_search",
        "bash",
        "write_file",
        "send_email",
        "odysseus_call",
        "unclassified_plugin_tool",
    ])

    assert names == (
        "list_models",
        "list_sessions",
        "odysseus_notify_user",
        "web_search",
    )


def test_mcp_policy_hides_high_risk_tools_even_with_expose_all():
    options = McpToolPolicyOptions(expose_all=True)

    for tool_name in ALWAYS_DENIED_TOOLS:
        decision = classify_mcp_tool(tool_name, options)
        assert decision.exposed is False, tool_name
        assert decision.category == "high_risk"
        assert decision.reason == "high_risk_tool_hidden"


def test_mcp_policy_hides_generic_api_by_default():
    assert classify_mcp_tool("odysseus_call").exposed is False
    assert classify_mcp_tool("odysseus_call").reason == "generic_api_hidden_by_default"
    assert classify_mcp_tool("odysseus_list_endpoints").exposed is False


def test_mcp_policy_requires_explicit_flags_for_sensitive_categories():
    assert classify_mcp_tool("create_document").exposed is False
    assert classify_mcp_tool("read_email").exposed is False
    assert classify_mcp_tool("read_file").exposed is False

    options = McpToolPolicyOptions(
        allow_owner_scoped_writes=True,
        allow_private_reads=True,
        allow_filesystem_reads=True,
    )

    assert classify_mcp_tool("create_document", options).exposed is True
    assert classify_mcp_tool("read_email", options).exposed is True
    assert classify_mcp_tool("read_file", options).exposed is True


def test_mcp_policy_keeps_unknown_tools_default_hidden():
    decision = classify_mcp_tool("new_plugin_tool")

    assert decision.exposed is False
    assert decision.category == "unclassified"
    assert decision.reason == "unclassified_tool_hidden_by_default"


def test_mcp_policy_filters_openai_function_schemas():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "odysseus_notify_user",
                "description": "Notify the user.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run shell.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    exposed = filter_mcp_tools(tools)

    assert [tool["function"]["name"] for tool in exposed] == ["odysseus_notify_user"]


def test_mcp_policy_allows_debug_readonly_tools_without_write_flags():
    for tool_name in DEBUG_READONLY_TOOLS:
        decision = classify_mcp_tool(tool_name)
        assert decision.exposed is True, tool_name
        assert decision.category == "debug_readonly"
        assert decision.reason == "debug_readonly_tool_allowed"

    names = exposed_mcp_tool_names(["debug_recent_failures", "podman_debug_status_readonly", "service_restart"])
    assert names == ("debug_recent_failures", "podman_debug_status_readonly")


def test_mcp_policy_exposes_only_github_issue_duplicate_lookup():
    duplicate_lookup = classify_mcp_tool("github_issue_find_duplicates")
    mixed_agent_tool = classify_mcp_tool("manage_github_issues")
    write_tool = classify_mcp_tool("github_issue_create_triaged")

    assert duplicate_lookup.exposed is True
    assert duplicate_lookup.category == "github_issue_readonly"
    assert mixed_agent_tool.exposed is False
    assert mixed_agent_tool.category == "high_risk"
    assert write_tool.exposed is False
    assert write_tool.category == "unclassified"
