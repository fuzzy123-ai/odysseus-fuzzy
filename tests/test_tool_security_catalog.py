import json
from types import SimpleNamespace

import pytest

from src.builtin_tool_catalog import (
    RUNTIME_ADMIN_PERMISSION_IDS,
    build_builtin_descriptor_catalog,
)
from src.tool_catalog import (
    ToolEffectClass,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolDescriptorV2,
)
from src.tool_security import is_public_blocked_tool


def _descriptors():
    return {item.tool_id: item for item in build_builtin_descriptor_catalog().descriptors}


def test_builtin_descriptor_permissions_match_runtime_admin_policy_exactly():
    descriptors = _descriptors()
    descriptor_admin = {
        tool_id
        for tool_id, descriptor in descriptors.items()
        if descriptor.permission == ToolPermission.ADMIN
    }
    runtime_admin = {
        tool_id for tool_id in descriptors if is_public_blocked_tool(tool_id)
    }

    assert descriptor_admin == set(RUNTIME_ADMIN_PERMISSION_IDS)
    assert runtime_admin == descriptor_admin


def test_sensitive_admin_families_keep_conservative_effects_and_confirmation():
    descriptors = _descriptors()

    for tool_id in ("manage_plugins", "manage_settings", "manage_tokens"):
        descriptor = descriptors[tool_id]
        assert descriptor.permission == ToolPermission.ADMIN
        assert descriptor.effect_class == ToolEffectClass.CONTROL
        assert descriptor.risk_level == ToolRiskLevel.DANGEROUS
        assert descriptor.requires_confirmation is True

    repos = descriptors["manage_repos"]
    assert repos.permission == ToolPermission.ADMIN
    assert repos.effect_class == ToolEffectClass.LOCAL_WRITE
    assert repos.risk_level == ToolRiskLevel.DANGEROUS
    assert repos.requires_confirmation is True

    for tool_id in (
        "adopt_served_model",
        "cancel_download",
        "download_model",
        "serve_model",
        "serve_preset",
        "stop_served_model",
        "tail_serve_output",
    ):
        descriptor = descriptors[tool_id]
        assert descriptor.permission == ToolPermission.ADMIN
        assert descriptor.effect_class == ToolEffectClass.CONTROL


def test_unknown_dynamic_descriptor_starts_dangerous_admin_and_confirmed():
    descriptor = ToolDescriptorV2.conservative_dynamic(
        tool_id="future_provider_action",
        source=ToolSource.PROVIDER,
        source_id="provider-redacted",
    )

    assert descriptor.risk_level == ToolRiskLevel.DANGEROUS
    assert descriptor.permission == ToolPermission.ADMIN
    assert descriptor.effect_class == ToolEffectClass.CONTROL
    assert descriptor.requires_confirmation is True
    assert descriptor.default_enabled is False


@pytest.mark.asyncio
async def test_tail_serve_output_public_or_foreign_owner_fails_before_handler(monkeypatch):
    import src.tool_execution as tool_execution
    import src.tool_implementations as implementations

    called = False

    async def forbidden_handler(content, owner=None):
        nonlocal called
        called = True
        return {"output": "private log", "exit_code": 0}

    monkeypatch.setattr(implementations, "do_tail_serve_output", forbidden_handler)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: False)

    description, result = await tool_execution.execute_tool_block(
        SimpleNamespace(
            tool_type="tail_serve_output",
            content=json.dumps({"session_id": "serve-abc123"}),
        ),
        owner="foreign-owner",
    )

    assert description == "tail_serve_output: BLOCKED"
    assert result["exit_code"] == 1
    assert "admin" in result["error"].lower()
    assert called is False


@pytest.mark.asyncio
async def test_tail_serve_output_admin_reaches_bound_handler(monkeypatch):
    import src.tool_execution as tool_execution
    import src.tool_implementations as implementations

    seen = {}

    async def allowed_handler(content, owner=None):
        seen.update(content=content, owner=owner)
        return {"output": "bounded", "exit_code": 0}

    monkeypatch.setattr(implementations, "do_tail_serve_output", allowed_handler)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    description, result = await tool_execution.execute_tool_block(
        SimpleNamespace(
            tool_type="tail_serve_output",
            content=json.dumps({"session_id": "serve-abc123"}),
        ),
        owner="admin-owner",
    )

    assert description == "tail_serve_output"
    assert result == {"output": "bounded", "exit_code": 0}
    assert seen["owner"] == "admin-owner"


@pytest.mark.asyncio
async def test_dynamic_registry_tool_defaults_admin_at_runtime(monkeypatch):
    import src.tool_execution as tool_execution
    from src.tool_registry import register_tool, unregister_tool

    called = False

    async def forbidden_handler(**kwargs):
        nonlocal called
        called = True
        return {"output": "unexpected", "exit_code": 0}

    spec = register_tool(
        {
            "name": "tax5_dynamic_default_admin",
            "description": "Synthetic dynamic security fixture.",
            "parameters": {"type": "object", "properties": {}},
            "execute": forbidden_handler,
        }
    )
    try:
        assert spec.permission == "admin"
        monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: False)

        description, result = await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type=spec.name, content="{}"),
            owner="foreign-owner",
        )

        assert description == f"{spec.name}: BLOCKED"
        assert result["exit_code"] == 1
        assert "admin" in result["error"].lower()
        assert called is False
    finally:
        unregister_tool(spec.name)
