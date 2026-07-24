from __future__ import annotations

import asyncio
import json

from src.agent_tools import TOOL_TAGS, ToolBlock, parse_tool_blocks
from src.builtin_tool_catalog import builtin_spec
from src.tool_execution import execute_tool_block
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_policy import known_tool_names
from src.tool_schemas import function_call_to_tool_block, get_function_tool_schemas
from src.tool_security import plan_mode_disabled_tools


def test_manage_todos_is_registered_in_schema_catalog_index_and_policy():
    schema_names = {
        schema["function"]["name"] for schema in get_function_tool_schemas()
    }

    assert "manage_todos" in schema_names
    assert "manage_todos" in TOOL_TAGS
    assert "manage_todos" in known_tool_names()
    assert "manage_todos" in BUILTIN_TOOL_DESCRIPTIONS
    assert builtin_spec("manage_todos") is not None
    assert "manage_todos" in plan_mode_disabled_tools()


def test_native_and_fenced_calls_preserve_manage_todos_json_arguments():
    payload = {
        "action": "add",
        "text": "Synthetic task",
        "idempotency_key": "native-call-1",
    }
    native = function_call_to_tool_block("manage_todos", json.dumps(payload))
    fenced = parse_tool_blocks(
        f"```manage_todos\n{json.dumps(payload)}\n```"
    )

    assert native.tool_type == "manage_todos"
    assert json.loads(native.content) == payload
    assert len(fenced) == 1
    assert fenced[0].tool_type == "manage_todos"


def test_dispatch_reaches_owner_gate_without_falling_through_unknown_tool():
    description, result = asyncio.run(
        execute_tool_block(ToolBlock("manage_todos", '{"action":"list"}'), owner=None)
    )

    assert description == "manage_todos"
    assert not description.startswith("unknown:")
    assert result["exit_code"] == 1
    assert "Unknown tool type:" not in str(result.get("error") or "")
