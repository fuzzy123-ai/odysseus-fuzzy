from types import SimpleNamespace

import pytest

from src.tool_execution import execute_tool_block


@pytest.mark.asyncio
async def test_native_pygame_launch_is_blocked_before_subprocess(monkeypatch):
    called = False

    async def fake_call(*args, **kwargs):
        nonlocal called
        called = True
        return {"output": "unexpected", "exit_code": 0}

    monkeypatch.setitem(execute_tool_block.__globals__, "_call_mcp_tool", fake_call)
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)
    _desc, result = await execute_tool_block(
        SimpleNamespace(tool_type="bash", content="python mario_game.py"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["interactive_runtime"]["kind"] == "interactive_native_gui_launch"
    assert called is False


@pytest.mark.asyncio
async def test_pipeline_masking_is_blocked_before_subprocess(monkeypatch):
    called = False

    async def fake_call(*args, **kwargs):
        nonlocal called
        called = True
        return {"output": "unexpected", "exit_code": 0}

    monkeypatch.setitem(execute_tool_block.__globals__, "_call_mcp_tool", fake_call)
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)
    _desc, result = await execute_tool_block(
        SimpleNamespace(tool_type="bash", content="python -m pip install pygame-ce | tail -n 2"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["interactive_runtime"]["kind"] == "pipeline_masking"
    assert called is False


@pytest.mark.asyncio
async def test_dummy_sdl_run_stays_headless_in_result_evidence(monkeypatch):
    async def fake_call(*args, **kwargs):
        return {"output": "frame captured", "exit_code": 0}

    monkeypatch.setitem(execute_tool_block.__globals__, "_call_mcp_tool", fake_call)
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)
    _desc, result = await execute_tool_block(
        SimpleNamespace(
            tool_type="bash",
            content="SDL_VIDEODRIVER=dummy python mario_game.py --capture frame.png",
        ),
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["interactive_runtime"]["kind"] == "headless_capture"
    assert result["interactive_runtime"]["headless"] is True


@pytest.mark.asyncio
async def test_noninteractive_install_keeps_existing_command_behavior(monkeypatch):
    async def fake_call(*args, **kwargs):
        return {"output": "installed", "exit_code": 0}

    monkeypatch.setitem(execute_tool_block.__globals__, "_call_mcp_tool", fake_call)
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)
    _desc, result = await execute_tool_block(
        SimpleNamespace(tool_type="bash", content="python -m pip install requests"),
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["interactive_runtime"]["kind"] == "risky_install"
