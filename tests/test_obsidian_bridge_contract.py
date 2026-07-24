import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI

import src.plugin_system as plugin_system
from src.agent_tools import ToolBlock
from src.mcp_manager import McpManager
from src.tool_execution import execute_tool_block
from src.tool_parsing import parse_tool_blocks
from src.tool_registry import ToolSpec, get_tool, register_tool, unregister_tool
from src.tool_schemas import function_call_to_tool_block
from src.tool_security import blocked_tools_for_owner, is_public_blocked_tool
from plugins.obsidian.backend.vault_rules import MAX_MARKDOWN_LINES, RULES_NOTE_PATH


CANONICAL_VAULT_MCP_TOOLS = {
    "obsidian_tree",
    "obsidian_read_note",
    "obsidian_search_notes",
    "obsidian_write_note",
    "obsidian_delete_note",
    "obsidian_undo",
    "obsidian_graph",
    "obsidian_spark_plan",
}

LEGACY_OR_FOREIGN_OBSIDIAN_TOOLS = {
    "vault_read",
    "vault_write",
    "vault_tree",
    "obsidian_file_create",
    "obsidian_mcp__obsidian_file_create",
}


@pytest.fixture
def obsidian_plugin_manager(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "plugins" / "obsidian"
    plugins_dir = tmp_path / "plugins"
    target = plugins_dir / "obsidian"
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
    )
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(plugin_system, "MANAGER", None)

    manager = plugin_system.PluginManager(app=FastAPI(), directory=str(plugins_dir))
    yield manager
    if "obsidian" in manager.records:
        manager.disable("obsidian")


def _vault_mcp_manager() -> McpManager:
    from plugins.obsidian.backend.tool_specs import VAULT_TOOL_SPECS

    mgr = McpManager()
    mgr._connections = {
        "vault": {"name": "Built-in: Obsidian Vault"},
        "memory": {"name": "Built-in: Memory"},
        "email": {"name": "Built-in: Email"},
    }
    mgr._tools = {
        "vault": [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in VAULT_TOOL_SPECS
        ],
        "memory": [{
            "name": "memory_search",
            "description": "Search server memory.",
            "input_schema": {"type": "object", "properties": {}},
        }],
        "email": [{
            "name": "send_email",
            "description": "Send mail.",
            "input_schema": {"type": "object", "properties": {}},
        }],
    }
    return mgr


def test_vault_tool_spec_contract_uses_canonical_obsidian_names():
    from plugins.obsidian.backend.tool_specs import DESTRUCTIVE_TOOL_NAMES, VAULT_TOOL_BY_NAME

    names = set(VAULT_TOOL_BY_NAME)

    assert CANONICAL_VAULT_MCP_TOOLS <= names
    assert names.isdisjoint(LEGACY_OR_FOREIGN_OBSIDIAN_TOOLS)
    assert {"obsidian_write_note", "vault_batch", "obsidian_delete_note", "obsidian_undo"} <= DESTRUCTIVE_TOOL_NAMES
    assert {"obsidian_read_note", "obsidian_search_notes", "obsidian_graph"}.isdisjoint(DESTRUCTIVE_TOOL_NAMES)


def test_vault_mcp_is_visible_to_chat_but_other_builtin_mcp_servers_stay_hidden():
    mgr = _vault_mcp_manager()

    schema_names = {schema["function"]["name"] for schema in mgr.get_all_openai_schemas()}
    prompt = mgr.get_tool_descriptions_for_prompt()

    assert "mcp__vault__obsidian_read_note" in schema_names
    assert "mcp__vault__obsidian_write_note" in schema_names
    assert "mcp__vault__obsidian_read_note" in prompt
    assert "mcp__memory__memory_search" not in schema_names
    assert "mcp__email__send_email" not in schema_names
    assert "mcp__memory__memory_search" not in prompt


def test_plan_mode_hides_and_blocks_mutating_vault_mcp_but_keeps_readonly_visible():
    mgr = _vault_mcp_manager()
    disabled_map, qualified = mgr.plan_mode_blocked_mcp()
    schema_names = {
        schema["function"]["name"]
        for schema in mgr.get_all_openai_schemas(disabled_map)
    }

    assert "mcp__vault__obsidian_read_note" in schema_names
    assert "mcp__vault__obsidian_search_notes" in schema_names
    assert "mcp__vault__obsidian_write_note" not in schema_names
    assert "mcp__vault__obsidian_delete_note" not in schema_names
    assert "mcp__vault__obsidian_write_note" in qualified
    assert "mcp__vault__obsidian_read_note" not in qualified


@pytest.mark.asyncio
async def test_chat_vault_mcp_bridge_writes_only_to_trusted_request_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path / "{owner}"))
    fuzzy = tmp_path / "fuzzy"
    mallory = tmp_path / "mallory"
    fuzzy.mkdir()
    mallory.mkdir()

    desc, result = await execute_tool_block(
        ToolBlock(
            "mcp__vault__obsidian_write_note",
            json.dumps({
                "path": "Contract/Test.md",
                "content": "# Owner scoped\n",
                "owner": "mallory",
            }),
        ),
        owner="fuzzy",
    )

    assert desc == "mcp: mcp__vault__obsidian_write_note"
    assert result["exit_code"] == 0
    assert (fuzzy / "Contract" / "Test.md").read_text(encoding="utf-8") == "# Owner scoped\n"
    assert not (mallory / "Contract" / "Test.md").exists()


def test_legacy_obsidian_mcp_names_feed_back_actionable_suggestions():
    native_block = function_call_to_tool_block(
        "obsidian_mcp__obsidian_file_create",
        json.dumps({"path": "Test MCP Notiz.md", "content": "# Test"}),
    )
    xml_blocks = parse_tool_blocks(
        "<obsidian_mcp__obsidian_file_create>"
        "<path>Test MCP Notiz.md</path>"
        "<content># Test</content>"
        "</obsidian_mcp__obsidian_file_create>"
    )

    assert native_block is not None
    assert native_block.tool_type == "invalid_tool_call"
    assert "mcp__vault__obsidian_write_note" in native_block.content
    assert [block.tool_type for block in xml_blocks] == ["invalid_tool_call"]
    assert "mcp__vault__obsidian_write_note" in xml_blocks[0].content


@pytest.mark.asyncio
async def test_invalid_tool_call_feedback_reaches_the_next_agent_round():
    desc, result = await execute_tool_block(
        ToolBlock(
            "invalid_tool_call",
            json.dumps({
                "tool": "obsidian_mcp__obsidian_file_create",
                "suggestions": ["mcp__vault__obsidian_write_note"],
            }),
        ),
        owner="fuzzy",
    )

    assert desc == "invalid_tool_call: obsidian_mcp__obsidian_file_create"
    assert result["exit_code"] == 1
    assert "Ung" in result["error"]
    assert "mcp__vault__obsidian_write_note" in result["error"]


def test_public_users_can_use_readonly_owner_scoped_vault_mcp_but_not_writes_or_foreign_servers(monkeypatch):
    monkeypatch.setattr("src.tool_security.owner_is_admin_or_single_user", lambda owner: False)

    assert is_public_blocked_tool("mcp__vault__obsidian_read_note") is False
    assert is_public_blocked_tool("mcp__vault__obsidian_write_note") is True
    assert is_public_blocked_tool("mcp__email__send_email") is True
    assert is_public_blocked_tool("mcp__filesystem__read_file") is True


def test_plugin_registry_permissions_keep_obsidian_user_tools_public_but_admin_tools_blocked(monkeypatch):
    monkeypatch.setattr("src.tool_security.owner_is_admin_or_single_user", lambda owner: False)
    register_tool(ToolSpec(
        name="contract_user_tool",
        description="User scoped.",
        parameters={"type": "object", "properties": {}},
        execute=lambda content, **kwargs: {"output": content, "exit_code": 0},
        permission="user",
    ))
    register_tool(ToolSpec(
        name="contract_admin_tool",
        description="Admin scoped.",
        parameters={"type": "object", "properties": {}},
        execute=lambda content, **kwargs: {"output": content, "exit_code": 0},
        permission="admin",
    ))
    try:
        blocked = blocked_tools_for_owner("alice")
        assert "contract_user_tool" not in blocked
        assert "contract_admin_tool" in blocked
    finally:
        unregister_tool("contract_user_tool")
        unregister_tool("contract_admin_tool")


def test_obsidian_plugin_load_contract_registers_user_tools_and_logs_summary(obsidian_plugin_manager, caplog):
    with caplog.at_level("INFO"):
        assert obsidian_plugin_manager.load_enabled(obsidian_plugin_manager.app) == 1

    record = obsidian_plugin_manager.records["obsidian"]
    tool_names = set(record.ctx._tools)
    messages = [record.getMessage() for record in caplog.records]

    assert record.status == "loaded", record.error
    assert len(tool_names) >= 30
    assert {
        "obsidian_read_note",
        "obsidian_write_note",
        "obsidian_search_notes",
        "obsidian_graph",
        "obsidian_delete_note",
    } <= tool_names
    assert all((get_tool(name) and get_tool(name).permission == "user") for name in tool_names)
    assert f"Plugin loaded: obsidian (registered {len(tool_names)} tool(s))" in messages
    assert not any(message.startswith("Registered plugin tool:") for message in messages)


def test_vault_rules_contract_creates_visible_rules_note_and_warns_on_large_markdown(tmp_path):
    from plugins.obsidian.backend import vault_service

    content = "\n".join(f"line {i}" for i in range(MAX_MARKDOWN_LINES + 1))

    result = vault_service.write_file(
        str(tmp_path),
        "Oversized.md",
        content,
        owner="alice",
        tool="contract",
    )

    assert (tmp_path / RULES_NOTE_PATH).exists()
    assert result["line_count"] == MAX_MARKDOWN_LINES + 1
    assert result["line_soft_cap"] == MAX_MARKDOWN_LINES
    assert "Split this note" in result["warning"]


def test_vault_rules_are_documented_for_external_ai_clients():
    from plugins.obsidian.backend.vault_rules import RULES_NOTE_CONTENT

    root = Path(__file__).resolve().parents[1]
    docs = [
        RULES_NOTE_CONTENT,
        (root / "plugins" / "obsidian" / "README.md").read_text(encoding="utf-8"),
        (root / "integrations" / "codex" / "skills" / "odysseus" / "SKILL.md").read_text(encoding="utf-8"),
        (root / "integrations" / "claude" / "skills" / "odysseus" / "SKILL.md").read_text(encoding="utf-8"),
    ]

    for text in docs:
        assert "600" in text
        assert "manageable AI context" in text
        assert "split" in text.lower()
