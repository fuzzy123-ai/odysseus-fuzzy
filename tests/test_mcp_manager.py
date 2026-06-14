import asyncio
from unittest.mock import patch

from src.mcp_manager import _format_mcp_connection_error, McpManager


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Odysseus" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_http_transport_routes_to_start_http_connect():
    mgr = McpManager()

    async def fake_start(server_id, name, url):
        return "ROUTED"

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as m:
        result = asyncio.run(mgr.connect_server("id1", "n", "http", url="https://x/mcp"))
    assert result == "ROUTED"
    m.assert_called_once()


def test_vault_builtin_is_visible_but_other_python_builtins_stay_hidden():
    mgr = McpManager()
    mgr._connections = {
        "vault": {"name": "Built-in: Obsidian Vault"},
        "memory": {"name": "Built-in: Memory"},
    }
    mgr._tools = {
        "vault": [{
            "name": "obsidian_read_note",
            "description": "Read a markdown note from the vault.",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }],
        "memory": [{
            "name": "memory_search",
            "description": "Search memory.",
            "input_schema": {"type": "object", "properties": {}},
        }],
    }

    schemas = mgr.get_all_openai_schemas()
    names = {schema["function"]["name"] for schema in schemas}
    prompt = mgr.get_tool_descriptions_for_prompt()

    assert "mcp__vault__obsidian_read_note" in names
    assert "mcp__memory__memory_search" not in names
    assert "mcp__vault__obsidian_read_note" in prompt
    assert "mcp__memory__memory_search" not in prompt


def test_obsidian_mcp_readonly_classification_for_plan_mode():
    from src.mcp_manager import mcp_tool_is_readonly

    assert mcp_tool_is_readonly({"name": "obsidian_read_note"}) is True
    assert mcp_tool_is_readonly({"name": "obsidian_search_notes"}) is True
    assert mcp_tool_is_readonly({"name": "obsidian_write_note"}) is False
    assert mcp_tool_is_readonly({"name": "vault_batch"}) is False


def test_plan_mode_blocks_only_mutating_vault_mcp_tools():
    mgr = McpManager()
    mgr._tools = {
        "vault": [
            {"name": "obsidian_read_note"},
            {"name": "obsidian_search_notes"},
            {"name": "obsidian_write_note"},
            {"name": "vault_batch"},
            {"name": "obsidian_delete_note"},
            {"name": "obsidian_undo"},
        ]
    }

    disabled_map, qualified = mgr.plan_mode_blocked_mcp()

    assert "obsidian_read_note" not in disabled_map.get("vault", set())
    assert "obsidian_search_notes" not in disabled_map.get("vault", set())
    assert {
        "obsidian_write_note",
        "vault_batch",
        "obsidian_delete_note",
        "obsidian_undo",
    } <= disabled_map["vault"]
    assert "mcp__vault__obsidian_write_note" in qualified
    assert "mcp__vault__obsidian_read_note" not in qualified
