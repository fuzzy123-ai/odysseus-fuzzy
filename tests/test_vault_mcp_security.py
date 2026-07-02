import json

import pytest

from src.agent_tools import ToolBlock
from src.tool_execution import execute_tool_block
from src.tool_security import is_public_blocked_tool


def test_public_vault_mcp_allows_readonly_only():
    assert is_public_blocked_tool("mcp__vault__obsidian_read_note") is False
    assert is_public_blocked_tool("mcp__vault__obsidian_search_notes") is False
    assert is_public_blocked_tool("mcp__vault__obsidian_raptor_status") is False

    assert is_public_blocked_tool("mcp__vault__obsidian_write_note") is True
    assert is_public_blocked_tool("mcp__vault__obsidian_delete_note") is True
    assert is_public_blocked_tool("mcp__vault__vault_batch") is True
    assert is_public_blocked_tool("mcp__vault__obsidian_undo") is True
    assert is_public_blocked_tool("mcp__vault__obsidian_raptor_rebuild") is True


@pytest.mark.asyncio
async def test_non_admin_vault_mcp_write_is_blocked_before_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path / "{owner}"))
    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: False)

    desc, result = await execute_tool_block(
        ToolBlock(
            "mcp__vault__obsidian_write_note",
            json.dumps({"path": "ShouldNotExist.md", "content": "# Nope"}),
        ),
        owner="alice",
    )

    assert desc == "mcp__vault__obsidian_write_note: BLOCKED"
    assert result["exit_code"] == 1
    assert not (tmp_path / "alice" / "ShouldNotExist.md").exists()
