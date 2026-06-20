import json

from src.agent_tools import TOOL_TAGS, ToolBlock
from src.chat_agent_tool_discovery_map import (
    DELEGATE_TOOLSET,
    DURABLE_SUBAGENT_TOOLSET,
    MULTI_AGENT_INTENT_KEYWORDS,
)
from src.tool_execution import execute_tool_block
from src.mcp_server_tool_policy import classify_mcp_tool
from src.tool_index import ToolIndex
from src.tool_policy import build_effective_tool_policy
from src.tool_schemas import function_call_to_tool_block


def _tools_for(query: str) -> set[str]:
    index = ToolIndex.__new__(ToolIndex)
    index.index_plugin_tools = lambda: None
    index.retrieve = lambda query, k=8: []
    return index.get_tools_for_query(query)


def test_durable_subagent_keywords_select_spawn_subagent_not_delegate():
    tools = _tools_for("Starte einen Worker fuer Alice und Bob parallel.")

    assert "spawn_subagent" in tools
    assert "delegate" not in tools


def test_delegate_stays_available_for_lightweight_readonly_analysis():
    tools = _tools_for("Delegate a focused read-only investigation of the code.")

    assert "delegate" in tools
    assert "spawn_subagent" not in tools


def test_multi_agent_backstop_uses_durable_runtime_surface():
    assert "worker starten" in MULTI_AGENT_INTENT_KEYWORDS
    assert "spawn_subagent" in DURABLE_SUBAGENT_TOOLSET
    assert "manage_subagents" in DURABLE_SUBAGENT_TOOLSET
    assert "delegate" not in DURABLE_SUBAGENT_TOOLSET
    assert DELEGATE_TOOLSET == frozenset({"delegate"})


def test_subagent_tools_are_not_exposed_through_mcp_policy():
    assert classify_mcp_tool("spawn_subagent").exposed is False
    assert classify_mcp_tool("manage_subagents").exposed is False


def test_orchestrator_mode_allows_fake_subagent_runtime_surface():
    policy = build_effective_tool_policy(
        last_user_message="Run this as an orchestrator.",
        orchestrator_mode=True,
    )

    assert not policy.blocks("spawn_subagent")
    assert not policy.blocks("manage_subagents")


def test_spawn_subagent_schema_is_executable_fake_surface():
    assert "spawn_subagent" in TOOL_TAGS

    block = function_call_to_tool_block(
        "spawn_subagent",
        json.dumps(
            {
                "plan_id": "subagent-runtime-v1",
                "node_id": "sub5",
                "slice_id": "sub5-tool-discovery",
                "agent_id": "bob",
                "objective": "Create fake runtime tool surface.",
                "allowed_files": ["src/subagent_runtime.py"],
            }
        ),
    )

    assert block.tool_type == "spawn_subagent"


async def test_spawn_subagent_tool_uses_fake_backend_only():
    desc, result = await execute_tool_block(
        ToolBlock(
            "spawn_subagent",
            json.dumps(
                {
                    "agent_run_id": "sub5-tool-test-run",
                    "plan_id": "subagent-runtime-v1",
                    "node_id": "sub5",
                    "slice_id": "sub5-tool-discovery",
                    "agent_id": "bob",
                    "objective": "Create fake runtime tool surface.",
                    "allowed_files": ["src/subagent_runtime.py"],
                    "tests": ["python -m pytest tests/test_subagent_tool_selection.py"],
                }
            ),
        )
    )

    assert desc == "spawn_subagent"
    assert result["exit_code"] == 0
    assert result["status"] == "spawned"
    assert "no live thread action" in result["summary"]
    assert result["run"]["target_kind"] == "job"
