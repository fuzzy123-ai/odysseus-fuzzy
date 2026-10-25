"""Comprehensive tests for HarbourMcpDispatcher, tool contracts, and End-to-End MCP dispatch."""

import asyncio
import json
import time
from pathlib import Path
import pytest

from src.harbour.mcp import (
    HARBOUR_MCP_FIRST_TOOLS,
    HarbourMcpDispatchError,
    HarbourMcpDispatcher,
    build_harbour_mcp_tool_contracts,
)
from src.mcp_authenticated_dispatch import (
    AppOwnedPlanningRuntime,
    McpAuthenticatedIdentity,
    dispatch_http_payload,
)


def _rpc(method: str, params: dict | None = None, message_id: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_build_harbour_mcp_tool_contracts():
    """Tool contracts builder must cover all 12 tools and support scope filtering."""
    contracts = build_harbour_mcp_tool_contracts()
    assert len(contracts) == 12
    names = {c["name"] for c in contracts}
    assert names == set(HARBOUR_MCP_FIRST_TOOLS.keys())
    for contract in contracts:
        assert "name" in contract
        assert "description" in contract
        assert "inputSchema" in contract

    planning_contracts = build_harbour_mcp_tool_contracts("mcp:planning:read")
    assert len(planning_contracts) == 3
    assert {c["name"] for c in planning_contracts} == {
        "harbour_planning_get_projection",
        "harbour_planning_lookup_revision",
        "harbour_planning_list_heads",
    }

    inbox_contracts = build_harbour_mcp_tool_contracts("mcp:harbour:inbox")
    assert len(inbox_contracts) == 3
    assert {c["name"] for c in inbox_contracts} == {
        "harbour_inbox_list_items",
        "harbour_inbox_capture_document",
        "harbour_inbox_stamp_document",
    }

    gateway_contracts = build_harbour_mcp_tool_contracts("mcp:harbour:gateway")
    assert len(gateway_contracts) == 3
    assert {c["name"] for c in gateway_contracts} == {
        "harbour_gateway_dispatch_task",
        "harbour_gateway_get_runner_state",
        "harbour_gateway_evaluate_quality_gate",
    }

    memory_contracts = build_harbour_mcp_tool_contracts("mcp:harbour:context:read")
    assert len(memory_contracts) == 3
    assert {c["name"] for c in memory_contracts} == {
        "harbour_memory_query_usi",
        "harbour_memory_get_context_manifest",
        "harbour_memory_runtime_status",
    }


def test_dispatcher_planning_domain():
    """Dispatcher must serve all 3 planning tools using structural census or store."""
    dispatcher = HarbourMcpDispatcher(repo_root=Path(__file__).resolve().parents[1])

    # 1. get_projection
    proj = dispatcher.dispatch(
        "harbour_planning_get_projection",
        {},
        identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:planning:read"),
    )
    assert proj["schema"] == "odysseus.harbour.planning_projection.v1"
    assert proj["total_sources"] > 0
    assert len(proj["heads"]) > 0

    # 2. lookup_revision
    head_source_id = proj["heads"][0]["source_id"]
    found = dispatcher.dispatch(
        "harbour_planning_lookup_revision",
        {"revision_id": head_source_id},
        identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:planning:read"),
    )
    assert found["schema"] == "odysseus.harbour.planning_revision_lookup.v1"
    assert found["found"] is True
    assert found["record"]["source_id"] == head_source_id

    not_found = dispatcher.dispatch(
        "harbour_planning_lookup_revision",
        {"revision_id": "non_existent_revision_xyz_123"},
        identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:planning:read"),
    )
    assert not_found["found"] is False
    assert not_found["record"] is None

    # Missing revision_id
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_planning_lookup_revision",
            {},
            identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:planning:read"),
        )
    assert exc.value.code == "invalid_params"

    # 3. list_heads
    heads = dispatcher.dispatch(
        "harbour_planning_list_heads",
        {},
        identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:planning:read"),
    )
    assert heads["schema"] == "odysseus.harbour.planning_heads.v1"
    assert heads["total_heads"] > 0
    assert len(heads["heads"]) == heads["total_heads"]


def test_dispatcher_memory_domain():
    """Dispatcher must serve all 3 memory tools with USI query, manifest, and runtime status."""
    dispatcher = HarbourMcpDispatcher(repo_root=Path(__file__).resolve().parents[1])
    identity = McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:harbour:context:read")

    # 1. query_usi
    result = dispatcher.dispatch(
        "harbour_memory_query_usi",
        {"query": "roadmap", "limit": 5},
        identity=identity,
    )
    assert result["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert result["query"] == "roadmap"
    assert result["limit"] == 5
    assert len(result["matches"]) <= 5

    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch("harbour_memory_query_usi", {"query": ""}, identity=identity)
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch("harbour_memory_query_usi", {"query": "test", "limit": 999}, identity=identity)
    assert exc.value.code == "invalid_params"

    # 2. get_context_manifest
    manifest = dispatcher.dispatch(
        "harbour_memory_get_context_manifest",
        {"session_id": "sess-test-42"},
        identity=identity,
    )
    assert manifest["schema"] == "odysseus.harbour.context_manifest.v1"
    assert manifest["session_id"] == "sess-test-42"
    assert manifest["bounded"] is True

    # 3. runtime_status
    status = dispatcher.dispatch(
        "harbour_memory_runtime_status",
        {},
        identity=identity,
    )
    assert status["schema"] == "odysseus.unified_source_index.runtime_status.v1"
    assert "knowledge" in status
    assert "index_job_pump" in status


def test_dispatcher_gateway_domain():
    """Dispatcher must serve gateway tools enforcing execution policy."""
    dispatcher = HarbourMcpDispatcher(repo_root=Path(__file__).resolve().parents[1])
    identity = McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:harbour:gateway")

    # 1. dispatch_task - valid
    valid_task = {
        "task_id": "test-task-1",
        "isolation_mode": "git_worktree_sandbox_pod",
        "lease_required": True,
        "authorizes_mutation": False,
    }
    dispatch_res = dispatcher.dispatch(
        "harbour_gateway_dispatch_task",
        {"task_plan": valid_task},
        identity=identity,
    )
    assert dispatch_res["schema"] == "odysseus.harbour.gateway_dispatch_result.v1"
    assert dispatch_res["status"] == "dispatched"
    assert dispatch_res["task_id"] == "test-task-1"
    assert dispatch_res["authorizes_mutation"] is False

    # 1b. dispatch_task - policy violation (authorizes_mutation)
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_gateway_dispatch_task",
            {"task_plan": {"authorizes_mutation": True}},
            identity=identity,
        )
    assert exc.value.code == "policy_violation"

    # 1c. dispatch_task - policy violation (direct_main_process_execution_allowed)
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_gateway_dispatch_task",
            {"task_plan": {"direct_main_process_execution_allowed": True}},
            identity=identity,
        )
    assert exc.value.code == "policy_violation"

    # 1d. dispatch_task - policy violation (invalid isolation_mode)
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_gateway_dispatch_task",
            {"task_plan": {"isolation_mode": "in_process_unisolated"}},
            identity=identity,
        )
    assert exc.value.code == "policy_violation"

    # 2. get_runner_state - not found
    runner_state = dispatcher.dispatch(
        "harbour_gateway_get_runner_state",
        {"task_id": "nonexistent_task_404"},
        identity=identity,
    )
    assert runner_state["schema"] == "odysseus.harbour.runner_state.v1"
    assert runner_state["status"] == "not_found"
    assert runner_state["found"] is False

    # 3. evaluate_quality_gate
    qg = dispatcher.dispatch(
        "harbour_gateway_evaluate_quality_gate",
        {"task_id": "task-qg-test"},
        identity=identity,
    )
    assert qg["schema"] == "odysseus.harbour.quality_gate_evaluation.v1"
    assert qg["task_id"] == "task-qg-test"
    assert "quality_gate" in qg


def test_dispatcher_inbox_domain():
    """Dispatcher must serve inbox tools enforcing 256 KB budget and subject binding."""
    dispatcher = HarbourMcpDispatcher(repo_root=Path(__file__).resolve().parents[1])
    identity = McpAuthenticatedIdentity("token-1", "admin", "owner_subj_999", 1, 1, "mcp:harbour:inbox")

    # 1. list_items
    items = dispatcher.dispatch(
        "harbour_inbox_list_items",
        {"filter_status": "pending"},
        identity=identity,
    )
    assert items["schema"] == "odysseus.harbour.inbox_items_list.v1"
    assert items["filter_status"] == "pending"
    assert items["owner_subject_id"] == "owner_subj_999"

    # 2. capture_document
    doc = dispatcher.dispatch(
        "harbour_inbox_capture_document",
        {"filename": "test_note.txt", "content_bytes": "hello world from inbox test"},
        identity=identity,
    )
    assert doc["schema"] == "odysseus.harbour.inbox_captured_document.v1"
    assert doc["filename"] == "test_note.txt"
    assert doc["owner_subject_id"] == "owner_subj_999"
    assert doc["size_bytes"] == len("hello world from inbox test")
    assert doc["document_id"].startswith("doc_")

    # 2b. capture_document traversal rejection
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_inbox_capture_document",
            {"filename": "../etc/passwd", "content_bytes": "payload"},
            identity=identity,
        )
    assert exc.value.code == "invalid_params"

    # 2c. capture_document budget exceeded (> 256 KB)
    oversized = "x" * (256 * 1024 + 1)
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_inbox_capture_document",
            {"filename": "oversized.txt", "content_bytes": oversized},
            identity=identity,
        )
    assert exc.value.code == "budget_exceeded"

    # 3. stamp_document
    stamped = dispatcher.dispatch(
        "harbour_inbox_stamp_document",
        {"document_id": doc["document_id"]},
        identity=identity,
    )
    assert stamped["schema"] == "odysseus.harbour.inbox_stamped_document.v1"
    assert stamped["document_id"] == doc["document_id"]
    assert stamped["stamped"] is True
    assert stamped["owner_subject_id"] == "owner_subj_999"
    assert stamped["stamp_hash"].startswith("sha256:")


def test_dispatcher_scope_enforcement():
    """Dispatcher must reject cross-scope calls with unauthorized."""
    dispatcher = HarbourMcpDispatcher(repo_root=Path(__file__).resolve().parents[1])

    # inbox identity calling planning tool
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_planning_get_projection",
            {},
            identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:harbour:inbox"),
        )
    assert exc.value.code == "unauthorized"

    # planning identity calling inbox tool
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_inbox_list_items",
            {},
            identity=McpAuthenticatedIdentity("token-1", "admin", "subj-1", 1, 1, "mcp:planning:read"),
        )
    assert exc.value.code == "unauthorized"


def test_end_to_end_mcp_dispatch_http_payload():
    """End-to-End JSON-RPC execution via dispatch_http_payload for Harbour tools."""
    repo_root = str(Path(__file__).resolve().parents[1])
    identity_planning = McpAuthenticatedIdentity("token-1", "admin", "subj-admin", 1, 1, "mcp:planning:read")

    class MockLegacyStore:
        pass

    runtime = AppOwnedPlanningRuntime(
        repo_root=repo_root,
        legacy_store=MockLegacyStore(),  # type: ignore
        durable_store=None,
        issuer=None,
    )

    # 1. harbour_planning_get_projection over JSON-RPC
    req1 = _rpc("tools/call", {"name": "harbour_planning_get_projection", "arguments": {}})
    res1 = asyncio.run(
        dispatch_http_payload(
            req1,
            identity=identity_planning,
            runtime=runtime,
            started=time.monotonic(),
        )
    )
    assert res1["jsonrpc"] == "2.0"
    assert res1["id"] == 1
    content_raw = res1["result"]["content"][0]["text"]
    parsed_content = json.loads(content_raw)
    assert parsed_content["schema"] == "odysseus.harbour.planning_projection.v1"
    assert parsed_content["total_sources"] > 0

    # 2. harbour_gateway_dispatch_task over JSON-RPC
    req2 = _rpc(
        "tools/call",
        {
            "name": "harbour_gateway_dispatch_task",
            "arguments": {
                "task_plan": {
                    "task_id": "rpc-task-1",
                    "isolation_mode": "git_worktree_sandbox_pod",
                    "lease_required": True,
                    "authorizes_mutation": False,
                }
            },
        },
        message_id=2,
    )
    res2 = asyncio.run(
        dispatch_http_payload(
            req2,
            identity=identity_planning,
            runtime=runtime,
            started=time.monotonic(),
        )
    )
    assert res2["id"] == 2
    parsed_res2 = json.loads(res2["result"]["content"][0]["text"])
    assert parsed_res2["schema"] == "odysseus.harbour.gateway_dispatch_result.v1"
    assert parsed_res2["status"] == "dispatched"

    # 3. Policy violation maps to JSON-RPC error
    req3 = _rpc(
        "tools/call",
        {
            "name": "harbour_gateway_dispatch_task",
            "arguments": {
                "task_plan": {
                    "authorizes_mutation": True,
                }
            },
        },
        message_id=3,
    )
    res3 = asyncio.run(
        dispatch_http_payload(
            req3,
            identity=identity_planning,
            runtime=runtime,
            started=time.monotonic(),
        )
    )
    assert res3["id"] == 3
    assert "error" in res3
    assert res3["error"]["message"] == "Planning read failed"

    # 4. Scope denial maps to JSON-RPC error
    req4 = _rpc(
        "tools/call",
        {
            "name": "harbour_inbox_list_items",
            "arguments": {},
        },
        message_id=4,
    )
    res4 = asyncio.run(
        dispatch_http_payload(
            req4,
            identity=identity_planning,
            runtime=runtime,
            started=time.monotonic(),
        )
    )
    assert res4["id"] == 4
    assert res4["error"]["code"] == -32001  # Unauthorized
