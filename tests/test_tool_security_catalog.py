import asyncio
import json

from src.agent_tools import ToolBlock
from src.builtin_tool_catalog import BUILTIN_TOOL_SPECS, builtin_spec
from src.tool_catalog import (
    ToolEffectClass,
    ToolPermission,
    ToolRiskLevel,
)
from src import tool_execution
from src.tool_security import (
    NON_ADMIN_BLOCKED_TOOLS,
    RUNTIME_ADMIN_TOOLS,
    runtime_tool_security_profile,
    validate_catalog_runtime_security_projection,
)


def test_runtime_admin_boundary_is_central_and_includes_sensitive_log_access():
    assert tool_execution._ADMIN_TOOLS == set(RUNTIME_ADMIN_TOOLS)
    assert {
        "manage_plugins",
        "manage_settings",
        "manage_tokens",
        "manage_repos",
        "manage_embeddings",
        "manage_personal_docs",
        "download_model",
        "serve_model",
        "tail_serve_output",
    } <= RUNTIME_ADMIN_TOOLS
    assert "tail_serve_output" in NON_ADMIN_BLOCKED_TOOLS


def test_runtime_projection_is_never_weaker_than_builtin_catalog():
    assert validate_catalog_runtime_security_projection() == ()
    assert {spec.tool_id for spec in BUILTIN_TOOL_SPECS}

    tail_spec = builtin_spec("tail_serve_output")
    tail_profile = runtime_tool_security_profile("tail_serve_output")
    assert tail_spec.permission == ToolPermission.ADMIN
    assert tail_profile.permission == ToolPermission.ADMIN
    assert tail_profile.effect_class == ToolEffectClass.READ
    assert tail_profile.requires_confirmation is False


def test_management_actions_distinguish_reads_writes_and_destructive_operations():
    cases = {
        ("manage_plugins", "list"): (ToolEffectClass.READ, False),
        ("manage_plugins", "install"): (ToolEffectClass.CONTROL, True),
        ("manage_plugins", "uninstall"): (ToolEffectClass.DESTRUCTIVE, True),
        ("manage_settings", "get"): (ToolEffectClass.READ, False),
        ("manage_settings", "set"): (ToolEffectClass.CONTROL, True),
        ("manage_settings", "reset"): (ToolEffectClass.DESTRUCTIVE, True),
        ("manage_tokens", "list"): (ToolEffectClass.READ, False),
        ("manage_tokens", "create"): (ToolEffectClass.CONTROL, True),
        ("manage_tokens", "delete"): (ToolEffectClass.DESTRUCTIVE, True),
        ("manage_repos", "status"): (ToolEffectClass.READ, False),
        ("manage_repos", "commit"): (ToolEffectClass.LOCAL_WRITE, True),
        ("manage_repos", "push"): (ToolEffectClass.EXTERNAL_WRITE, True),
        ("manage_repos", "forget"): (ToolEffectClass.DESTRUCTIVE, True),
    }
    for (tool_id, action), expected in cases.items():
        profile = runtime_tool_security_profile(
            tool_id,
            json.dumps({"action": action}),
        )
        assert profile.permission == ToolPermission.ADMIN
        assert (profile.effect_class, profile.requires_confirmation) == expected


def test_unknown_dynamic_tools_start_conservative_until_policy_is_explicit():
    default = runtime_tool_security_profile("plugin_future_tool", "{}")
    assert default.permission == ToolPermission.ADMIN
    assert default.risk_level == ToolRiskLevel.ELEVATED
    assert default.effect_class == ToolEffectClass.CONTROL
    assert default.requires_confirmation is True
    assert default.source == "dynamic_conservative"

    explicit_user = runtime_tool_security_profile(
        "plugin_reviewed_read",
        "{}",
        dynamic_permission="user",
    )
    assert explicit_user.permission == ToolPermission.OWNER
    assert explicit_user.risk_level == ToolRiskLevel.ELEVATED
    assert explicit_user.requires_confirmation is True


def test_non_admin_tail_call_fails_before_implementation(monkeypatch):
    called = False

    async def _fail_tail(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"exit_code": 0}

    monkeypatch.setattr("src.tool_implementations.do_tail_serve_output", _fail_tail)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: False)
    desc, result = asyncio.run(
        tool_execution._execute_tool_block_impl(
            ToolBlock("tail_serve_output", '{"session_id":"serve-test"}'),
            session_id="chat-test",
            owner="public-user",
        )
    )

    assert desc == "tail_serve_output: BLOCKED"
    assert "requires an admin" in result["error"]
    assert called is False


def test_admin_tail_dispatch_threads_owner_and_caller_session(monkeypatch):
    captured = {}

    async def _capture_tail(content, owner=None, caller_session_id=None):
        captured.update(
            content=content,
            owner=owner,
            caller_session_id=caller_session_id,
        )
        return {"output": "bounded", "exit_code": 0}

    monkeypatch.setattr("src.tool_implementations.do_tail_serve_output", _capture_tail)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    desc, result = asyncio.run(
        tool_execution._execute_tool_block_impl(
            ToolBlock("tail_serve_output", '{"session_id":"serve-test"}'),
            session_id="chat-test",
            owner="admin-user",
        )
    )

    assert desc == "tail_serve_output"
    assert result["exit_code"] == 0
    assert captured == {
        "content": '{"session_id":"serve-test"}',
        "owner": "admin-user",
        "caller_session_id": "chat-test",
    }
