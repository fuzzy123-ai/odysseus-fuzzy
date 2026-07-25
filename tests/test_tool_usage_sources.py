import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.mcp_manager import McpManager
from src.task_scheduler import TaskScheduler
from src.task_scheduler_delivery import deliver_via_mcp
import src.tool_execution as tool_execution
from src.tool_registry import ToolSpec, register_tool, unregister_tool
from src.tool_usage_context import TrustedToolUsageContext
from src.tool_usage_events import ToolUsageEventBuilder, pseudonymize_reference
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    execute_instrumented_bypass,
    normalize_bypass_tool_usage_outcome,
)


FIXED_TIME = datetime(2026, 7, 17, 22, 0, tzinfo=timezone.utc)
HMAC_KEY = b"source-adapter-key-" * 2


class _Sink:
    def __init__(self):
        self.events = []
        self.calls = 0

    def append_best_effort(self, events):
        self.calls += 1
        self.events.extend(events)
        return SimpleNamespace(failure_count=0)


def _context(**overrides):
    values = {
        "surface": "agent",
        "agent_mode": "agent",
        "model_scope": "local",
        "owner_identity": "owner-1",
        "session_identity": "session-1",
        "run_identity": "run-1",
        "correlation_identity": "correlation-1",
    }
    values.update(overrides)
    return TrustedToolUsageContext.create(**values)


def _instrumentation(sink, context=None):
    return ToolUsageInstrumentation(
        builder=ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY),
        sink=sink,
        context=context or _context(),
        clock=lambda: FIXED_TIME,
    )


@pytest.mark.parametrize(
    ("tool_name", "trusted_source", "expected_source", "expected_id"),
    [
        ("read_file", "builtin", "builtin", "read_file"),
        ("private-provider-runtime", "provider", "provider", "dynamic.provider.unclassified"),
        ("private-legacy-runtime", "legacy", "legacy", "legacy.unclassified"),
        ("mcp__private-server__private-op", None, "mcp", "dynamic.mcp.unclassified"),
    ],
)
def test_source_resolution_is_bounded_for_every_non_plugin_source(
    tool_name, trusted_source, expected_source, expected_id
):
    sink = _Sink()
    instrumentation = _instrumentation(sink)

    invocation = instrumentation.begin(
        tool_name,
        {"private": "argument"},
        trusted_source=trusted_source,
    )
    instrumentation.finish(
        invocation,
        outcome=normalize_bypass_tool_usage_outcome({"ok": True}, succeeded=True),
        duration_ms=3,
    )

    assert len(sink.events) == 2
    assert {event.tool_source.value for event in sink.events} == {expected_source}
    assert {event.tool_analytics_id for event in sink.events} == {expected_id}
    if expected_id.endswith("unclassified"):
        assert tool_name not in " ".join(event.to_json() for event in sink.events)


@pytest.mark.asyncio
async def test_plugin_crossing_central_wrapper_is_counted_once(monkeypatch):
    calls = []

    async def _handler(content, **_kwargs):
        calls.append(content)
        return {"output": "private plugin output", "exit_code": 0}

    unregister_tool("usage_source_plugin")
    register_tool(
        ToolSpec(
            name="usage_source_plugin",
            description="Source coverage plugin.",
            parameters={"type": "object", "properties": {}},
            execute=_handler,
            permission="public",
            source_id="private-plugin-source",
        )
    )
    try:
        sink = _Sink()
        result = await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="usage_source_plugin", content='{"private":true}'),
            tool_usage_instrumentation=_instrumentation(sink),
        )
    finally:
        unregister_tool("usage_source_plugin")

    assert result[1]["exit_code"] == 0
    assert calls == ['{"private":true}']
    assert len(sink.events) == 2
    assert {event.invocation_id for event in sink.events}.__len__() == 1
    assert {event.tool_source.value for event in sink.events} == {"plugin"}
    assert {event.tool_analytics_id for event in sink.events} == {
        "dynamic.plugin.unclassified"
    }
    encoded = " ".join(event.to_json() for event in sink.events)
    assert "usage_source_plugin" not in encoded
    assert "private-plugin-source" not in encoded


@pytest.mark.asyncio
async def test_mcp_transport_reconnect_remains_one_logical_wrapper_invocation(monkeypatch):
    manager = McpManager()
    manager._sessions["builtin_usage_test"] = object()
    attempts = []

    async def _do_call(_session, _tool_name, _arguments):
        attempts.append("transport-attempt")
        if len(attempts) == 1:
            raise RuntimeError("private transport failure")
        return {"stdout": "ok", "stderr": "", "exit_code": 0}

    async def _reconnect(_server_id):
        manager._sessions["builtin_usage_test"] = object()
        return True

    monkeypatch.setattr(manager, "_do_call", _do_call)
    monkeypatch.setattr(manager, "_reconnect_builtin", _reconnect)
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    sink = _Sink()

    result = await tool_execution.execute_tool_block(
        SimpleNamespace(
            tool_type="mcp__builtin_usage_test__private_runtime_name",
            content='{"private":"argument"}',
        ),
        owner="admin",
        tool_usage_instrumentation=_instrumentation(sink),
    )

    assert result[1]["exit_code"] == 0
    assert attempts == ["transport-attempt", "transport-attempt"]
    assert len(sink.events) == 2
    assert len({event.invocation_id for event in sink.events}) == 1
    assert {event.retry_ordinal for event in sink.events} == {0}
    assert {event.tool_source.value for event in sink.events} == {"mcp"}
    assert "private_runtime_name" not in " ".join(
        event.to_json() for event in sink.events
    )


@pytest.mark.asyncio
async def test_malformed_mcp_call_is_content_free_rejected_unknown(monkeypatch):
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: McpManager())
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    private_name = "mcp__private-owner-malformed"
    sink = _Sink()

    await tool_execution.execute_tool_block(
        SimpleNamespace(tool_type=private_name, content="private argument"),
        owner="admin",
        tool_usage_instrumentation=_instrumentation(sink),
    )

    terminal = sink.events[-1]
    assert len(sink.events) == 2
    assert terminal.status.value == "rejected"
    assert terminal.blocked_reason_code.value == "unknown_tool"
    assert terminal.tool_analytics_id == "dynamic.mcp.unclassified"
    assert private_name not in " ".join(event.to_json() for event in sink.events)
    assert "private argument" not in " ".join(event.to_json() for event in sink.events)


@pytest.mark.asyncio
async def test_retry_and_correlation_are_explicit_and_result_identity_is_preserved():
    sink = _Sink()
    instrumentation = _instrumentation(sink)
    result = {"output": "unchanged", "exit_code": 0}
    operation_calls = 0

    async def _operation():
        nonlocal operation_calls
        operation_calls += 1
        return result

    actual = await execute_instrumented_bypass(
        instrumentation,
        tool_name="private-provider-operation",
        argument="private argument",
        operation=_operation,
        trusted_source="provider",
        retry_ordinal=2,
    )

    assert actual is result
    assert operation_calls == 1
    assert len(sink.events) == 2
    assert {event.retry_ordinal for event in sink.events} == {2}
    assert {event.correlation_ref for event in sink.events} == {
        pseudonymize_reference("correlation", "correlation-1", key=HMAC_KEY)
    }
    assert _instrumentation(_Sink()).diagnostics()["retry_semantics"] == (
        "zero_based_logical_attempt"
    )


@pytest.mark.asyncio
async def test_scheduler_action_bypass_counts_execution_not_context_setup(monkeypatch):
    calls = []

    async def _action(**_kwargs):
        calls.append("executed")
        return "private action result", True

    monkeypatch.setitem(
        __import__("src.builtin_actions", fromlist=["BUILTIN_ACTIONS"]).BUILTIN_ACTIONS,
        "usage_source_scheduler_action",
        _action,
    )
    sink = _Sink()
    scheduler = TaskScheduler(
        session_manager=None,
        tool_usage_instrumentation=_instrumentation(sink),
    )
    task = SimpleNamespace(
        id="task-private-1",
        owner="owner-private-1",
        session_id="session-private-1",
        action="usage_source_scheduler_action",
        prompt="private scheduler argument",
        name="Private scheduler task",
    )

    # Context creation/binding is setup only and must not increment usage.
    assert scheduler._tool_usage_context_for_task(task, "run-private-1") is not None
    assert scheduler._bound_tool_usage_for_task(task, "run-private-1") is not None
    assert sink.events == []

    result = await scheduler._execute_action(task, run_id="run-private-1")

    assert result == ("private action result", True)
    assert calls == ["executed"]
    assert len(sink.events) == 2
    assert {event.surface.value for event in sink.events} == {"scheduler"}
    assert {event.agent_mode.value for event in sink.events} == {"background_system"}
    assert {event.tool_source.value for event in sink.events} == {"legacy"}
    assert sink.events[-1].status.value == "succeeded"
    encoded = " ".join(event.to_json() for event in sink.events)
    assert "usage_source_scheduler_action" not in encoded
    assert "private scheduler argument" not in encoded


@pytest.mark.asyncio
async def test_unknown_scheduler_action_is_rejected_without_raw_name():
    sink = _Sink()
    scheduler = TaskScheduler(
        session_manager=None,
        tool_usage_instrumentation=_instrumentation(sink),
    )
    private_name = "unknown_private_scheduler_action"
    task = SimpleNamespace(
        id="task-private-2",
        owner="owner-private-2",
        session_id=None,
        action=private_name,
        prompt="private",
        name="Unknown task",
    )

    result = await scheduler._execute_action(task, run_id="run-private-2")

    assert result[1] is False
    assert len(sink.events) == 2
    assert sink.events[-1].status.value == "rejected"
    assert sink.events[-1].blocked_reason_code.value == "unknown_tool"
    assert private_name not in " ".join(event.to_json() for event in sink.events)


@pytest.mark.asyncio
async def test_scheduler_mcp_delivery_instruments_only_an_actual_manager_call(monkeypatch):
    class _Manager:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"stdout": "delivered", "stderr": "", "exit_code": 0}

    manager = _Manager()
    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: manager)
    monkeypatch.setattr("routes.email_helpers._get_email_config", lambda: {})
    sink = _Sink()
    instrumentation = _instrumentation(
        sink,
        _context(
            surface="scheduler",
            agent_mode="background_system",
            model_scope="unknown",
        ),
    )
    task = SimpleNamespace(
        id="task-delivery-1",
        name="Delivery",
        owner="owner@example.test",
    )

    delivered = await deliver_via_mcp(
        "mcp__private-mail__send_message",
        task,
        "private body",
        tool_usage_instrumentation=instrumentation,
    )

    assert delivered["status"] == "success"
    assert len(manager.calls) == 1
    assert len(sink.events) == 2
    assert {event.tool_source.value for event in sink.events} == {"mcp"}

    empty_sink = _Sink()
    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: None)
    blocked = await deliver_via_mcp(
        "mcp__private-mail__send_message",
        task,
        "private body",
        tool_usage_instrumentation=_instrumentation(empty_sink),
    )
    assert blocked["status"] == "blocked"
    assert empty_sink.events == []


def test_scheduler_capture_remains_default_off():
    scheduler = TaskScheduler(session_manager=None)
    assert scheduler._tool_usage_instrumentation is None
    assert scheduler._tool_usage_context_for_task(
        SimpleNamespace(id="task", owner="owner", session_id=None),
        "run",
    ) is None

