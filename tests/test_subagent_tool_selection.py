import json

import pytest

from src.agent_tools import TOOL_TAGS, ToolBlock
from src.chat_agent_tool_discovery_map import (
    DELEGATE_TOOLSET,
    DURABLE_SUBAGENT_TOOLSET,
    MULTI_AGENT_INTENT_KEYWORDS,
)
from src.tool_execution import execute_tool_block
from src.mcp_server_tool_policy import classify_mcp_tool
import src.tool_execution as tool_execution
from src.tool_index import ToolIndex
from src.tool_policy import build_effective_tool_policy
from src.tool_schemas import function_call_to_tool_block
from src.tool_security import is_public_blocked_tool
import src.subagent_runtime as subagent_runtime


@pytest.fixture(autouse=True)
def _reset_subagent_tool_runtime():
    subagent_runtime.reset_subagent_tool_runtime_for_tests()


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


def test_public_policy_blocks_durable_subagent_tools():
    assert is_public_blocked_tool("spawn_subagent") is True
    assert is_public_blocked_tool("manage_subagents") is True


async def test_manage_subagents_list_starts_with_empty_fake_runtime(monkeypatch):
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    desc, result = await execute_tool_block(ToolBlock("manage_subagents", json.dumps({"action": "list"})))

    assert desc == "manage_subagents"
    assert result["exit_code"] == 0
    assert result["runs"] == []


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


async def test_spawn_subagent_tool_uses_fake_backend_only(monkeypatch):
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

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


async def test_manage_subagents_pause_and_resume_use_fake_backend_only(monkeypatch):
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    run_id = "sub5-tool-pause-run"
    await execute_tool_block(
        ToolBlock(
            "spawn_subagent",
            json.dumps(
                {
                    "agent_run_id": run_id,
                    "plan_id": "subagent-runtime-v1",
                    "node_id": "sub5-pause",
                    "slice_id": "sub5-pause",
                    "agent_id": "bob",
                    "objective": "Pause and resume fake runtime.",
                    "allowed_files": ["src/subagent_runtime.py"],
                }
            ),
        )
    )

    pause_desc, pause_result = await execute_tool_block(
        ToolBlock("manage_subagents", json.dumps({"action": "pause", "agent_run_id": run_id}))
    )
    resume_desc, resume_result = await execute_tool_block(
        ToolBlock("manage_subagents", json.dumps({"action": "resume", "agent_run_id": run_id}))
    )

    assert pause_desc == "manage_subagents"
    assert pause_result["exit_code"] == 0
    assert pause_result["status"] == "paused"
    assert resume_desc == "manage_subagents"
    assert resume_result["exit_code"] == 0
    assert resume_result["status"] == "spawned"


async def test_subagent_tool_responses_do_not_leak_thread_or_job_refs(monkeypatch):
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    thread_ref = "private-thread-ref-sub8"
    job_ref = "private-job-ref-sub8"
    thread_run_id = "sub8-private-thread-run"
    job_run_id = "sub8-private-job-run"

    _, thread_result = await execute_tool_block(
        ToolBlock(
            "spawn_subagent",
            json.dumps(
                {
                    "agent_run_id": thread_run_id,
                    "plan_id": "subagent-runtime-v1",
                    "node_id": "sub8-thread",
                    "slice_id": "sub8-thread",
                    "agent_id": "alice",
                    "objective": "Spawn a fake thread-target run without leaking refs.",
                    "allowed_files": ["src/subagent_runtime.py"],
                    "target_kind": "thread",
                    "thread_id": thread_ref,
                }
            ),
        )
    )
    _, job_result = await execute_tool_block(
        ToolBlock(
            "spawn_subagent",
            json.dumps(
                {
                    "agent_run_id": job_run_id,
                    "plan_id": "subagent-runtime-v1",
                    "node_id": "sub8-job",
                    "slice_id": "sub8-job",
                    "agent_id": "bob",
                    "objective": "Spawn a fake job-target run without leaking refs.",
                    "allowed_files": ["src/subagent_runtime.py"],
                    "target_kind": "job",
                    "job_id": job_ref,
                }
            ),
        )
    )
    _, status_result = await execute_tool_block(
        ToolBlock("manage_subagents", json.dumps({"action": "status", "agent_run_id": thread_run_id}))
    )
    _, list_result = await execute_tool_block(ToolBlock("manage_subagents", json.dumps({"action": "list"})))

    rendered = repr((thread_result, job_result, status_result, list_result))
    assert thread_result["run"]["has_thread_ref"] is True
    assert job_result["run"]["has_job_ref"] is True
    assert thread_ref not in rendered
    assert job_ref not in rendered


async def test_manage_subagents_snapshot_is_plan_scoped_and_redacted(monkeypatch):
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    plan_id = "subagent-runtime-tool-snapshot"
    run_id = "sub9-snapshot-paused-run"
    other_run_id = "sub9-snapshot-other-plan-run"
    private_ref = "private-thread-ref-sub9"
    await execute_tool_block(
        ToolBlock(
            "spawn_subagent",
            json.dumps(
                {
                    "agent_run_id": run_id,
                    "plan_id": plan_id,
                    "node_id": "sub9-snapshot",
                    "slice_id": "sub9-snapshot",
                    "agent_id": "alice",
                    "objective": "Expose a fake status snapshot for one plan.",
                    "allowed_files": ["src/subagent_runtime.py"],
                    "target_kind": "thread",
                    "thread_id": private_ref,
                }
            ),
        )
    )
    await execute_tool_block(
        ToolBlock(
            "spawn_subagent",
            json.dumps(
                {
                    "agent_run_id": other_run_id,
                    "plan_id": "subagent-runtime-other-plan",
                    "node_id": "sub9-other",
                    "slice_id": "sub9-other",
                    "agent_id": "bob",
                    "objective": "Stay out of the requested snapshot.",
                    "allowed_files": ["src/subagent_runtime.py"],
                }
            ),
        )
    )
    await execute_tool_block(ToolBlock("manage_subagents", json.dumps({"action": "pause", "agent_run_id": run_id})))

    desc, result = await execute_tool_block(
        ToolBlock(
            "manage_subagents",
            json.dumps(
                {
                    "action": "snapshot",
                    "plan_id": plan_id,
                    "last_updated_at": "2026-06-20T12:20:00Z",
                }
            ),
        )
    )

    snapshot = result["snapshot"]
    rendered = repr(result)
    assert desc == "manage_subagents"
    assert result["exit_code"] == 0
    assert snapshot["plan_id"] == plan_id
    assert snapshot["run_count"] == 1
    assert snapshot["counts_by_state"] == {"paused": 1}
    assert snapshot["items"][0]["agent_run_id"] == run_id
    assert snapshot["items"][0]["allowed_actions"] == ("resume", "cancel")
    assert other_run_id not in rendered
    assert private_ref not in rendered
