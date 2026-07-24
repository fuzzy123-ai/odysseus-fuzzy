from __future__ import annotations

import pytest

from src.tool_catalog import ToolEffectClass, ToolPermission
from src.tool_security import (
    NON_ADMIN_BLOCKED_TOOLS,
    PLAN_MODE_READONLY_TOOLS,
    RUNTIME_ADMIN_PERMISSION_IDS,
    is_public_blocked_tool,
    plan_mode_disabled_tools,
    runtime_tool_security_profile,
)


def test_public_policy_blocks_generic_diagnostic_execution_surfaces() -> None:
    for tool_name in ("bash", "python", "app_api", "api_call", "read_file"):
        assert is_public_blocked_tool(tool_name) is True


@pytest.mark.parametrize(
    "tool_name",
    (
        "commit_project",
        "bulk_email",
        "delete_email",
        "manage_assistant",
        "tail_serve_output",
    ),
)
def test_released_effectful_tools_remain_admin_only(tool_name: str) -> None:
    assert tool_name in NON_ADMIN_BLOCKED_TOOLS
    assert is_public_blocked_tool(tool_name) is True


def test_catalog_admin_permissions_are_never_weaker_in_public_policy() -> None:
    assert RUNTIME_ADMIN_PERMISSION_IDS
    assert all(is_public_blocked_tool(tool_name) for tool_name in RUNTIME_ADMIN_PERMISSION_IDS)


def test_unknown_runtime_tool_fails_closed_as_admin_control() -> None:
    profile = runtime_tool_security_profile("unknown_diagnostic_tool")

    assert profile.permission == ToolPermission.ADMIN
    assert profile.effect_class == ToolEffectClass.CONTROL
    assert profile.requires_confirmation is True
    assert profile.source == "dynamic_conservative"


def test_plan_mode_keeps_shell_and_mutators_disabled() -> None:
    disabled = plan_mode_disabled_tools()

    assert {
        "bash",
        "python",
        "manage_settings",
        "manage_tokens",
        "commit_project",
        "manage_assistant",
    } <= disabled
    assert disabled.isdisjoint(PLAN_MODE_READONLY_TOOLS)


def test_public_vault_mcp_projection_is_explicit_allowlist() -> None:
    assert is_public_blocked_tool("mcp__vault__obsidian_read_note") is False
    assert is_public_blocked_tool("mcp__vault__obsidian_write_note") is True
    assert is_public_blocked_tool("mcp__unknown__status") is True
