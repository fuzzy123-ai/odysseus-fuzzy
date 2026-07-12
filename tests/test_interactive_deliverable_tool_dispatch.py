import pytest

from src.agent_tools import TOOL_HANDLERS, ToolBlock
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, is_public_blocked_tool
from src.tool_execution import execute_tool_block


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["publish_artifact", "verify_pygame_headless"])
async def test_interactive_deliverable_tools_dispatch_through_central_executor(
    tool_name,
    tmp_path,
    monkeypatch,
):
    calls = []

    async def fake_handler(content, ctx):
        calls.append((content, ctx))
        return {"output": "dispatched", "exit_code": 0}

    monkeypatch.setitem(TOOL_HANDLERS, tool_name, fake_handler)
    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: True)

    description, result = await execute_tool_block(
        ToolBlock(tool_name, '{"path":"game.py"}'),
        owner="alice",
        workspace=str(tmp_path),
    )

    assert description.startswith(tool_name + ":")
    assert result == {"output": "dispatched", "exit_code": 0}
    assert len(calls) == 1
    assert calls[0][0] == '{"path":"game.py"}'
    assert calls[0][1]["owner"] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["publish_artifact", "verify_pygame_headless"])
async def test_interactive_deliverable_tools_are_admin_only_at_execution_boundary(
    tool_name,
    tmp_path,
    monkeypatch,
):
    called = False

    async def forbidden_handler(content, ctx):
        nonlocal called
        called = True
        return {"output": "should not run", "exit_code": 0}

    monkeypatch.setitem(TOOL_HANDLERS, tool_name, forbidden_handler)
    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: False)

    description, result = await execute_tool_block(
        ToolBlock(tool_name, '{"path":"game.py"}'),
        owner="public-user",
        workspace=str(tmp_path),
    )

    assert tool_name in NON_ADMIN_BLOCKED_TOOLS
    assert is_public_blocked_tool(tool_name) is True
    assert description == f"{tool_name}: BLOCKED"
    assert result["exit_code"] == 1
    assert "restricted to admin users" in result["error"]
    assert called is False
