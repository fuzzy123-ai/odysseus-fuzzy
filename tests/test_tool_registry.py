import asyncio
import json

from src.agent_tools import ToolBlock
from src.tool_execution import execute_tool_block
from src.tool_parsing import parse_tool_blocks
from src.tool_registry import ToolSpec, get_tool, register_tool, unregister_tool
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block


def teardown_function(_fn):
    for name in ("plugin_echo", "plugin_dict"):
        unregister_tool(name)


def test_register_toolspec_exposes_function_schema_and_native_call():
    async def _run(content, owner=None, **_kwargs):
        return {"output": f"{owner}:{content}", "exit_code": 0}

    register_tool(ToolSpec(
        name="plugin_echo",
        description="Echo plugin input.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=_run,
    ))

    names = {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}
    assert "plugin_echo" in names

    block = function_call_to_tool_block("plugin_echo", json.dumps({"text": "hello"}))
    assert block == ToolBlock("plugin_echo", '{"text": "hello"}')


def test_register_dict_spec_and_fenced_parser():
    async def _run(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    register_tool({
        "name": "plugin_dict",
        "description": "Dict style plugin tool.",
        "schema": {
            "type": "function",
            "function": {
                "name": "plugin_dict",
                "description": "Dict style plugin tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _run,
    })

    blocks = parse_tool_blocks("```plugin_dict\n{\"ok\": true}\n```")
    assert blocks == [ToolBlock("plugin_dict", '{"ok": true}')]


def test_dispatcher_executes_registered_tool_with_owner():
    async def _run(content, owner=None, **_kwargs):
        args = json.loads(content)
        return {"output": f"{owner}:{args['value']}", "exit_code": 0}

    register_tool(ToolSpec(
        name="plugin_echo",
        description="Echo plugin input.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        execute=_run,
        permission="user",
    ))

    desc, result = asyncio.run(execute_tool_block(
        ToolBlock("plugin_echo", '{"value": "ok"}'),
        owner="alice",
    ))
    assert desc == "plugin_echo: plugin"
    assert result == {"output": "alice:ok", "exit_code": 0}


def test_unregister_removes_tool_and_schema():
    async def _run(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    register_tool(ToolSpec(
        name="plugin_echo",
        description="Echo plugin input.",
        parameters={"type": "object", "properties": {}},
        execute=_run,
    ))
    assert get_tool("plugin_echo") is not None
    unregister_tool("plugin_echo")
    assert get_tool("plugin_echo") is None
    assert "plugin_echo" not in {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}


def test_registered_tool_appears_in_agent_prompt():
    async def _run(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    register_tool(ToolSpec(
        name="plugin_echo",
        description="Echo plugin input.",
        parameters={"type": "object", "properties": {}},
        execute=_run,
        prompt="- ```plugin_echo``` - Echo plugin input.",
    ))

    from src.agent_loop import _assemble_prompt

    prompt = _assemble_prompt({"plugin_echo"})
    assert "plugin_echo" in prompt
    assert "Echo plugin input" in prompt


def test_tool_index_indexes_and_prunes_plugin_tools():
    class _Collection:
        def __init__(self):
            self.ids = ["plugin_stale"]
            self.deleted = []
            self.upserts = []

        def get(self, where=None):
            return {"ids": list(self.ids)}

        def delete(self, ids):
            self.deleted.extend(ids)
            self.ids = [i for i in self.ids if i not in ids]

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)
            self.ids.extend(i for i in kwargs["ids"] if i not in self.ids)

    class _Lane:
        name = "fake"

        def __init__(self):
            self.collection = _Collection()

        def encode(self, docs):
            return [[0.0] for _ in docs]

    async def _run(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    register_tool(ToolSpec(
        name="plugin_echo",
        description="Echo plugin input.",
        parameters={"type": "object", "properties": {}},
        execute=_run,
    ))

    from src.tool_index import ToolIndex

    ti = ToolIndex.__new__(ToolIndex)
    ti._lanes = [_Lane()]
    ti._plugin_generation = -1

    ti.index_plugin_tools()

    lane = ti._lanes[0]
    assert lane.collection.deleted == ["plugin_stale"]
    assert lane.collection.upserts[0]["ids"] == ["plugin_plugin_echo"]
    assert lane.collection.upserts[0]["metadatas"] == [
        {"tool_name": "plugin_echo", "tool_type": "plugin"}
    ]
