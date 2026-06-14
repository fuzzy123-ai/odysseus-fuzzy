import json

import pytest

from src.agent_tools import ToolBlock
from src.tool_execution import execute_tool_block


@pytest.mark.asyncio
async def test_internal_vault_mcp_uses_trusted_request_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path / "{owner}"))
    fuzzy = tmp_path / "fuzzy"
    default = tmp_path / "default"
    fuzzy.mkdir()
    default.mkdir()
    (fuzzy / "Demo.md").write_text("# Fuzzy\n\ntrusted owner vault", encoding="utf-8")
    (default / "Demo.md").write_text("# Default\n\nwrong vault", encoding="utf-8")

    desc, result = await execute_tool_block(
        ToolBlock(
            "mcp__vault__obsidian_read_note",
            json.dumps({"path": "Demo.md", "owner": "mallory"}),
        ),
        owner="fuzzy",
    )

    assert desc == "mcp: mcp__vault__obsidian_read_note"
    assert result["exit_code"] == 0
    assert "trusted owner vault" in result["stdout"]
    assert "wrong vault" not in result["stdout"]


@pytest.mark.asyncio
async def test_internal_vault_mcp_reports_locked_vault(monkeypatch, tmp_path):
    from plugins.obsidian.backend.vault_security import lock_vault, set_password

    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path / "{owner}"))
    vault = tmp_path / "alice"
    vault.mkdir()
    (vault / "Secret.md").write_text("# Secret", encoding="utf-8")
    set_password(str(vault), "strong password")
    lock_vault(str(vault))

    _desc, result = await execute_tool_block(
        ToolBlock("mcp__vault__obsidian_read_note", json.dumps({"path": "Secret.md"})),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert "locked" in result["stderr"].lower()


async def test_invalid_legacy_obsidian_mcp_call_feeds_back_suggestion():
    desc, result = await execute_tool_block(
        ToolBlock(
            "invalid_tool_call",
            json.dumps({
                "tool": "obsidian_mcp__obsidian_file_create",
                "suggestions": ["mcp__vault__obsidian_write_note"],
                "arguments": {"path": "Test MCP Notiz.md", "content": "# Hi"},
            }),
        ),
        owner="fuzzy",
    )

    assert desc == "invalid_tool_call: obsidian_mcp__obsidian_file_create"
    assert result["exit_code"] == 1
    assert "Ungültiger Tool-Befehl" in result["error"]
    assert "mcp__vault__obsidian_write_note" in result["error"]
