import asyncio

import pytest

from src.mcp_manager import McpManager
from src.runtime_tool_status import build_tool_catalog_projection
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_registry import (
    ToolSpec,
    generation,
    get_catalog_projection,
    get_function_schemas,
    get_tool,
    register_tool,
    unregister_tool,
)


_PLUGIN_NAMES = (
    "catalog_alpha",
    "catalog_alias_owner",
    "catalog_unknown_family",
    "catalog_reload",
)


@pytest.fixture(autouse=True)
def _clean_dynamic_catalog_tools():
    previous = {name: get_tool(name) for name in _PLUGIN_NAMES}
    for name in _PLUGIN_NAMES:
        unregister_tool(name)
    yield
    for name in _PLUGIN_NAMES:
        unregister_tool(name)
    for tool in previous.values():
        if tool is not None:
            register_tool(tool)


async def _execute(content, **_kwargs):
    return {"output": content, "exit_code": 0}


def _spec(name: str, **overrides) -> ToolSpec:
    values = {
        "name": name,
        "description": "Inspect a bounded reviewed capability.",
        "parameters": {"type": "object", "properties": {}},
        "execute": _execute,
    }
    values.update(overrides)
    return ToolSpec(**values)


def test_plugin_registration_emits_generation_bound_descriptor_v2_projection():
    before = generation()
    register_tool(
        _spec(
            "catalog_alpha",
            permission="user",
            family="knowledge_memory",
            lifecycle="contextual",
            availability="available",
            source_id="plugin:reviewed",
            aliases=("catalog_alias_owner",),
        )
    )

    projection = get_catalog_projection()
    row = next(item for item in projection["descriptors"] if item["tool_id"] == "catalog_alpha")

    assert projection["descriptor_schema"] == "odysseus.tool_descriptor.v2"
    assert projection["generation"] == before + 1
    assert row["source"] == "plugin"
    assert row["source_id"] == "plugin:reviewed"
    assert row["family"] == "knowledge_memory"
    assert row["permission"] == "owner"
    assert row["availability"] == "available"
    assert row["lifecycle"] == "contextual"
    assert row["aliases"] == ("catalog_alias_owner",)
    assert projection["raw_schema_visible"] is False
    assert projection["raw_content_visible"] is False
    assert projection["secret_values_visible"] is False


def test_unknown_plugin_family_is_unclassified_and_blocked_from_runtime_projection():
    tool = register_tool(
        _spec(
            "catalog_unknown_family",
            family="future_magic",
            description="Authorization: Bearer private-value-must-not-leak",
        )
    )

    projection = build_tool_catalog_projection(
        builtin_descriptions=BUILTIN_TOOL_DESCRIPTIONS,
        plugin_tools=[tool],
    )
    row = next(
        item for item in projection["descriptors"]
        if item["id"] == "catalog_unknown_family"
    )

    assert row["source"] == "plugin"
    assert row["source_id"] == "plugin:local"
    assert row["family"] == "unclassified_dynamic"
    assert row["lifecycle"] == "blocked"
    assert row["availability"] == "blocked"
    assert row["runtime_availability"] == "blocked_by_catalog"
    assert row["enabled"] is False
    assert row["settings_mutable"] is False
    assert "catalog_unknown_family" not in {item["id"] for item in projection["tools"]}
    assert "private-value-must-not-leak" not in repr(projection)


def test_plugin_names_and_aliases_cannot_collide_or_enter_mcp_namespace():
    before = generation()
    with pytest.raises(ValueError, match="built-in"):
        register_tool(_spec("read_file"))
    with pytest.raises(ValueError, match="reserved mcp__"):
        register_tool(_spec("mcp__review__lookup"))
    with pytest.raises(ValueError, match="non-path identifier"):
        register_tool(_spec("catalog_unknown_family", source_id="C:\\private\\plugin"))

    register_tool(_spec("catalog_alpha", aliases=("catalog_alias_owner",)))
    after_first = generation()
    with pytest.raises(ValueError, match="collision"):
        register_tool(_spec("catalog_alias_owner"))

    assert after_first == before + 1
    assert generation() == after_first
    assert get_tool("read_file") is None
    assert get_tool("mcp__review__lookup") is None


def test_plugin_reload_replaces_schema_and_unregister_invalidates_projection_once():
    register_tool(_spec("catalog_reload", description="First reviewed descriptor."))
    first_generation = generation()
    register_tool(_spec("catalog_reload", description="Second reviewed descriptor."))

    schemas = {
        item["function"]["name"]: item["function"]
        for item in get_function_schemas()
    }
    assert generation() == first_generation + 1
    assert schemas["catalog_reload"]["description"] == "Second reviewed descriptor."

    unregister_tool("catalog_reload")
    released_generation = generation()
    assert get_tool("catalog_reload") is None
    assert "catalog_reload" not in {
        item["tool_id"] for item in get_catalog_projection()["descriptors"]
    }
    unregister_tool("catalog_reload")
    assert generation() == released_generation


def test_mcp_inventory_normalizes_metadata_and_blocks_unknown_or_colliding_tools():
    manager = McpManager()
    manager._connections["review"] = {"name": "Review", "status": "connected"}
    manager._tools["review"] = [
        {
            "name": "lookup",
            "description": "Inspect a reviewed remote record.",
            "input_schema": {"type": "object", "properties": {}},
            "family": "knowledge_memory",
        },
        {
            "name": "future",
            "description": "Unknown family.",
            "input_schema": {},
            "family": "future_magic",
        },
        {"name": "duplicate", "description": "One.", "input_schema": {}},
        {"name": "duplicate", "description": "Two.", "input_schema": {}},
    ]

    rows = manager.get_all_tools()
    lookup = next(item for item in rows if item["name"] == "lookup")
    future = next(item for item in rows if item["name"] == "future")
    duplicates = [item for item in rows if item["name"] == "duplicate"]

    assert lookup["source"] == "mcp"
    assert lookup["source_id"] == "mcp:review"
    assert lookup["permission"] == "admin"
    assert lookup["family"] == "knowledge_memory"
    assert lookup["availability"] == "available"
    assert lookup["lifecycle"] == "contextual"
    assert lookup["policy_authority"] == "mcp_runtime_policy"
    assert lookup["catalog_blocked"] is False
    assert future["family"] == "unclassified_dynamic"
    assert future["availability"] == "blocked"
    assert future["lifecycle"] == "blocked"
    assert future["catalog_blocked"] is True
    assert all(item["catalog_blocked"] is True for item in duplicates)

    schemas = manager.get_all_openai_schemas()
    assert [item["function"]["name"] for item in schemas] == ["mcp__review__lookup"]


def test_mcp_projection_and_execution_fail_closed_without_exposing_raw_schema():
    manager = McpManager()
    manager._connections["review"] = {"name": "Review", "status": "connected"}
    manager._tools["review"] = [
        {
            "name": "lookup",
            "description": "Authorization: Bearer private-value-must-not-leak",
            "input_schema": {"type": "object", "properties": {"secret": {"type": "string"}}},
        },
        {
            "name": "future",
            "description": "Unknown family.",
            "input_schema": {},
            "family": "future_magic",
        },
    ]

    projection = build_tool_catalog_projection(
        builtin_descriptions=BUILTIN_TOOL_DESCRIPTIONS,
        mcp_tools=manager.get_all_tools({"review": {"lookup"}}),
    )
    rows = {item["id"]: item for item in projection["descriptors"]}

    assert rows["mcp__review__lookup"]["runtime_availability"] == "disabled_by_mcp_policy"
    assert rows["mcp__review__lookup"]["enabled"] is False
    assert rows["mcp__review__future"]["runtime_availability"] == "blocked_by_catalog"
    assert rows["mcp__review__future"]["family"] == "unclassified_dynamic"
    assert "private-value-must-not-leak" not in repr(projection)
    assert "input_schema" not in repr(projection)

    blocked = asyncio.run(manager.call_tool("mcp__review__future", {}))
    assert blocked == {
        "error": "MCP tool blocked by catalog normalization: mcp__review__future",
        "exit_code": 1,
    }


def test_mcp_disconnect_invalidates_generation_only_when_state_existed():
    manager = McpManager()
    manager._connections["review"] = {"name": "Review", "status": "connected"}
    manager._tools["review"] = [{"name": "lookup", "description": "Lookup."}]

    before = manager.generation()
    asyncio.run(manager.disconnect_server("review"))
    assert manager.generation() == before + 1
    assert manager.get_all_tools() == []

    asyncio.run(manager.disconnect_server("review"))
    assert manager.generation() == before + 1
