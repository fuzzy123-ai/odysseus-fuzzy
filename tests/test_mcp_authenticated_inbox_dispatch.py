"""Authenticated Harbour Inbox dispatch integration tests."""

import asyncio
import threading
import time

import pytest

import src.mcp_authenticated_dispatch as authenticated_dispatch
from src.mcp_authenticated_dispatch import (
    McpAuthenticatedIdentity,
    McpDispatchFailure,
    dispatch_http_payload,
)
from tests.test_mcp_authenticated_dispatch import _Runtime, _rpc


def test_inbox_scope_lists_only_inbox_tools_and_rejects_planning_cross_scope():
    from src.harbour_inbox_service import build_harbour_inbox_tool_contracts
    identity = McpAuthenticatedIdentity(
        "token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox"
    )
    class Runtime(_Runtime):
        def tool_contracts(self): return build_harbour_inbox_tool_contracts()
    runtime = Runtime()
    listed = asyncio.run(dispatch_http_payload(
        _rpc("tools/list"), identity=identity, runtime=runtime, started=time.monotonic(),
    ))
    assert [tool["name"] for tool in listed["result"]["tools"]] == [
        "harbour_inbox_list", "harbour_inbox_pause", "harbour_inbox_reopen", "harbour_inbox_cancel",
    ]
    denied = asyncio.run(dispatch_http_payload(
        _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}),
        identity=identity, runtime=runtime, started=time.monotonic(),
    ))
    assert denied["error"]["message"] == "Invalid params" and runtime.calls == 0


def test_authority_finalized_action_skips_post_commit_fences():
    from src.harbour_inbox_service import HarbourInboxOutcome
    identity = McpAuthenticatedIdentity(
        "token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox"
    )
    calls = []
    class Runtime:
        def dispatch(self, name, arguments, current):
            calls.append((name, arguments, current.scope))
            return HarbourInboxOutcome({"schema": "odysseus.harbour_inbox_action.v1", "ok": True}, True)
    fences = []
    result = asyncio.run(dispatch_http_payload(
        _rpc("tools/call", {"name": "harbour_inbox_pause", "arguments": {
            "clarification_id": "clar-inbox-001", "expected_version": 1, "idempotency_key": "one",
        }}),
        identity=identity,
        runtime=Runtime(),
        started=time.monotonic(),
        fence=lambda method=None: fences.append(method),
    ))
    assert result.payload["result"]["isError"] is False and calls
    assert fences == ["tools/call", "tools/call", "tools/call"]


def test_unified_read_keeps_post_read_fences():
    from src.harbour_inbox_service import HarbourInboxOutcome
    identity = McpAuthenticatedIdentity("token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox")
    class Runtime:
        def dispatch(self, *_args):
            return HarbourInboxOutcome({"schema": "odysseus.harbour_inbox_unified_list.v1", "items": []}, False)
    fences = []
    result = asyncio.run(dispatch_http_payload(
        _rpc("tools/call", {"name": "harbour_inbox_list", "arguments": {"view": "unified"}}),
        identity=identity, runtime=Runtime(), started=time.monotonic(), fence=lambda method=None: fences.append(method),
    ))
    assert result["result"]["isError"] is False and fences == ["tools/call"] * 5


@pytest.mark.parametrize(("arguments", "finalized", "fence_count"), [
    ({}, True, 3),
    ({"view": "unified"}, False, 5),
])
def test_default_and_unified_inbox_http_reads_keep_their_distinct_final_fences(arguments, finalized, fence_count):
    from src.harbour_inbox_service import HarbourInboxOutcome
    identity = McpAuthenticatedIdentity("token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox")
    fences = []
    class Runtime:
        def dispatch(self, *_args):
            return HarbourInboxOutcome({"schema": "odysseus.harbour_inbox_list.v1", "items": []}, finalized)
    result = asyncio.run(dispatch_http_payload(
        _rpc("tools/call", {"name": "harbour_inbox_list", "arguments": arguments}),
        identity=identity, runtime=Runtime(), started=time.monotonic(),
        fence=lambda method=None: fences.append(method),
    ))
    public_result = result.payload if finalized else result
    assert public_result["result"]["isError"] is False and fences == ["tools/call"] * fence_count


def test_timeout_joins_and_returns_rpc_error_after_worker_completion(monkeypatch):
    from src.harbour_inbox_service import HarbourInboxOutcome
    identity = McpAuthenticatedIdentity(
        "token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox"
    )
    finished = threading.Event()
    class Runtime:
        def dispatch(self, *_args):
            time.sleep(.03)
            finished.set()
            return HarbourInboxOutcome({"schema": "odysseus.harbour_inbox_unified_list.v1", "items": []}, False)
    monkeypatch.setattr(authenticated_dispatch, "MCP_TOTAL_BUDGET_SECONDS", .005)
    async def call():
        try:
            return await dispatch_http_payload(
                _rpc("tools/call", {"name": "harbour_inbox_list", "arguments": {"view": "unified"}}),
                identity=identity, runtime=Runtime(), started=time.monotonic(),
            )
        except McpDispatchFailure as error:
            return authenticated_dispatch.dispatch_failure_response(error.code, message_id=1)

    result = asyncio.run(call())
    assert finished.is_set()
    assert result["error"] == {"code": -32013, "message": "Request timed out"}


@pytest.mark.parametrize("order", ["unified-first", "unified-last"])
def test_mixed_unified_batches_keep_the_final_http_authentication_fence(order):
    from src.harbour_inbox_service import HarbourInboxOutcome, build_harbour_inbox_tool_contracts

    identity = McpAuthenticatedIdentity(
        "token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox",
    )
    calls, fences = [], []
    class Runtime:
        def tool_contracts(self):
            return build_harbour_inbox_tool_contracts()
        def dispatch(self, name, _arguments, _identity):
            calls.append(name)
            if name != "harbour_inbox_list":
                return {"schema": "odysseus.mcp.tool_result.v1", "tool": name,
                        "origin": {"kind": "repository_projection", "source_of_truth": False},
                        "payload": {"schema": "odysseus.planning.roadmap_list.v1", "read_only": True,
                                    "writes_supported": False, "roadmaps": [], "summary": {}}}
            return HarbourInboxOutcome(
                {"schema": "odysseus.harbour_inbox_unified_list.v1", "items": []}, False,
            )
    read = _rpc("tools/call", {"name": "harbour_inbox_list", "arguments": {"view": "unified"}}, 1)
    ping = _rpc("ping", {}, 2)
    batch = [read, ping] if order == "unified-first" else [ping, read]
    def fence(method=None):
        fences.append(method)
        if len(fences) == 8:
            raise McpDispatchFailure("stale_authentication")
    with pytest.raises(McpDispatchFailure) as error:
        asyncio.run(dispatch_http_payload(
            batch, identity=identity, runtime=Runtime(), started=time.monotonic(), fence=fence,
        ))
    assert error.value.code == "stale_authentication"
    assert calls == ["harbour_inbox_list"] and fences[-1] in {"ping", "tools/call"}


@pytest.mark.parametrize("mode,cancel_count", [("timeout", 0), ("cancel", 1), ("cancel", 2)])
def test_unified_read_timeout_and_repeated_cancellation_join_the_worker(monkeypatch, mode, cancel_count):
    from src.harbour_inbox_service import HarbourInboxOutcome, build_harbour_inbox_tool_contracts

    identity = McpAuthenticatedIdentity(
        "token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox",
    )
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    class Runtime:
        def tool_contracts(self):
            return build_harbour_inbox_tool_contracts()
        def dispatch(self, *_args):
            started.set()
            if mode == "cancel":
                assert release.wait(2)
            else:
                time.sleep(.03)
            finished.set()
            return HarbourInboxOutcome(
                {"schema": "odysseus.harbour_inbox_unified_list.v1", "items": []}, False,
            )
    async def scenario():
        if mode == "timeout":
            monkeypatch.setattr(authenticated_dispatch, "MCP_TOTAL_BUDGET_SECONDS", .005)
        task = asyncio.create_task(dispatch_http_payload(
            _rpc("tools/call", {"name": "harbour_inbox_list", "arguments": {"view": "unified"}}),
            identity=identity, runtime=Runtime(), started=time.monotonic(),
        ))
        assert await asyncio.to_thread(started.wait, 1)
        if mode == "cancel":
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result["error"] == {
                "code": -32013,
                "message": "Request timed out",
            }
    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert finished.is_set()

