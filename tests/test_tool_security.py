from __future__ import annotations

from src.tool_catalog import ToolEffectClass, ToolPermission
from src.tool_security import (
    PLAN_MODE_READONLY_TOOLS,
    is_public_blocked_tool,
    plan_mode_disabled_tools,
    runtime_tool_security_profile,
)


def test_public_policy_blocks_generic_diagnostic_execution_surfaces() -> None:
    for tool_name in ("bash", "python", "app_api", "api_call", "read_file"):
        assert is_public_blocked_tool(tool_name) is True


def test_unknown_runtime_tool_fails_closed_as_admin_control() -> None:
    profile = runtime_tool_security_profile("unknown_diagnostic_tool")

    assert profile.permission == ToolPermission.ADMIN
    assert profile.effect_class == ToolEffectClass.CONTROL
    assert profile.requires_confirmation is True
    assert profile.source == "dynamic_conservative"


def test_plan_mode_keeps_shell_and_mutators_disabled() -> None:
    disabled = plan_mode_disabled_tools()

    assert {"bash", "python", "manage_settings", "manage_tokens"} <= disabled
    assert disabled.isdisjoint(PLAN_MODE_READONLY_TOOLS)


def test_public_vault_mcp_projection_is_explicit_allowlist() -> None:
    assert is_public_blocked_tool("mcp__vault__obsidian_read_note") is False
    assert is_public_blocked_tool("mcp__vault__obsidian_write_note") is True
    assert is_public_blocked_tool("mcp__unknown__status") is True
