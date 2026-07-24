import pytest

from src.mcp_manager import McpManager
from src.tool_registry import (
    ToolSpec,
    generation,
    get_descriptor,
    get_function_schemas,
    register_tool,
    unregister_tool,
)


_DYNAMIC_NAMES = ("tax8_dynamic_probe", "Tax8MixedCase")


def teardown_function(_fn):
    for name in _DYNAMIC_NAMES:
        unregister_tool(name)


async def _execute(content, **_kwargs):
    return {"output": content, "exit_code": 0}


def _spec(*, description="First revision.", properties=None, source_id="plugin-registry"):
    return ToolSpec(
        name="tax8_dynamic_probe",
        description=description,
        parameters={
            "type": "object",
            "properties": properties or {"first": {"type": "string"}},
        },
        execute=_execute,
        permission="user",
        source_id=source_id,
    )


def _mcp_tool(name, description="Read an external item.", *, readonly=True):
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": readonly},
    }


def test_plugin_registration_builds_fail_closed_descriptor_and_unregister_invalidates_it():
    before = generation()
    register_tool(_spec(source_id="Example Plugin"))
    descriptor = get_descriptor("tax8_dynamic_probe")

    assert generation() == before + 1
    assert descriptor.tool_id == "tax8_dynamic_probe"
    assert descriptor.source.value == "plugin"
    assert descriptor.source_id.startswith("example-plugin-")
    assert descriptor.family.value == "unclassified_dynamic"
    assert descriptor.lifecycle.value == "experimental"
    assert descriptor.availability.value == "unavailable"
    assert descriptor.default_enabled is False
    assert descriptor.permission.value == "admin"
    assert descriptor.requires_confirmation is True

    unregister_tool("tax8_dynamic_probe")

    assert generation() == before + 2
    assert get_descriptor("tax8_dynamic_probe") is None
    assert "tax8_dynamic_probe" not in {
        schema["function"]["name"] for schema in get_function_schemas()
    }


@pytest.mark.parametrize("reserved_name", ["bash", "BASH", "manage_rag"])
def test_plugin_registration_rejects_builtin_and_alias_collisions(reserved_name):
    with pytest.raises(ValueError, match="collides with a built-in tool or alias"):
        register_tool(
            ToolSpec(
                name=reserved_name,
                description="Collision probe.",
                parameters={"type": "object", "properties": {}},
                execute=_execute,
            )
        )


def test_plugin_reload_replaces_schema_prompt_and_descriptor_without_stale_projection():
    register_tool(_spec())
    first_generation = generation()
    first_descriptor = get_descriptor("tax8_dynamic_probe")

    register_tool(
        _spec(
            description="Second revision.",
            properties={"second": {"type": "integer"}},
        )
    )

    schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in get_function_schemas()
    }
    assert generation() == first_generation + 1
    assert get_descriptor("tax8_dynamic_probe") is not first_descriptor
    assert get_descriptor("tax8_dynamic_probe").description == "Second revision."
    assert schemas["tax8_dynamic_probe"]["description"] == "Second revision."
    assert set(schemas["tax8_dynamic_probe"]["parameters"]["properties"]) == {"second"}


def test_dynamic_runtime_identity_is_normalized_without_losing_runtime_name():
    register_tool(
        ToolSpec(
            name="Tax8MixedCase",
            description="Mixed case runtime identity.",
            parameters={"type": "object", "properties": {}},
            execute=_execute,
        )
    )

    descriptor = get_descriptor("Tax8MixedCase")
    assert descriptor.tool_id == "tax8mixedcase"
    assert descriptor.source_id == "plugin-registry"


def test_mcp_descriptor_projection_is_deterministic_redacted_and_generation_bound():
    manager = McpManager()
    manager._connections["Example MCP"] = {"name": "Example Server"}
    manager._tools["Example MCP"] = [
        _mcp_tool("lookup", "Authorization: Bearer private-mcp-token"),
        _mcp_tool("lookup", "Duplicate identity must be ignored."),
    ]
    manager._generation += 1

    first = manager.get_descriptor_projections()
    cached = manager.get_descriptor_projections()

    assert first == cached
    assert len(first) == 1
    assert first[0]["runtime_tool_id"] == "mcp__Example MCP__lookup"
    assert first[0]["tool_id"].startswith("mcp__example-mcp__lookup-")
    assert first[0]["source"] == "mcp"
    assert first[0]["source_id"].startswith("example-mcp-")
    assert first[0]["description"] == "[redacted]"
    assert first[0]["family"] == "unclassified_dynamic"
    assert first[0]["availability"] == "unavailable"
    assert first[0]["policy_status"] == "dynamic_review_required"
    assert "private-mcp-token" not in repr(first)

    manager._tools["Example MCP"] = [_mcp_tool("search")]
    manager._generation += 1
    reloaded = manager.get_descriptor_projections()

    assert [row["runtime_tool_id"] for row in reloaded] == ["mcp__Example MCP__search"]


def test_mcp_descriptor_projection_does_not_replace_execution_policy_authority():
    manager = McpManager()
    manager._connections["remote"] = {"name": "Remote"}
    manager._tools["remote"] = [
        _mcp_tool("read_item", readonly=True),
        _mcp_tool("write_item", readonly=False),
    ]
    manager._generation += 1

    schemas_before = manager.get_all_openai_schemas()
    blocked_before = manager.plan_mode_blocked_mcp()
    manager.get_descriptor_projections()

    assert manager.get_all_openai_schemas() == schemas_before
    assert manager.plan_mode_blocked_mcp() == blocked_before
    assert blocked_before == (
        {"remote": {"write_item"}},
        {"mcp__remote__write_item"},
    )
