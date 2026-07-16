from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import src.tool_execution as tool_execution
from src.mcp_manager import McpManager, parse_qualified_mcp_tool_name
from src.task_scheduler import TaskScheduler
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_registry import ToolSpec, register_tool, unregister_tool
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageModelScope,
    ToolUsageStatus,
    ToolUsageSurface,
    pseudonymize_reference,
)
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    ToolUsageSourceIdentity,
    ToolUsageTrustedContext,
    bind_bypass_tool_usage_instrumentation,
    build_bypass_tool_usage_call_metadata,
    build_tool_usage_call_metadata_for_name,
    current_bypass_tool_usage_instrumentation,
)


FIXED_TIME = datetime(2026, 7, 16, 7, 30, tzinfo=timezone.utc)
HMAC_KEY = b"synthetic-source-key-material"


class SequenceClock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


def _context(*, retry_ordinal=0):
    return ToolUsageTrustedContext(
        surface=ToolUsageSurface.SCHEDULER,
        model_scope=ToolUsageModelScope.LOCAL,
        agent_mode=ToolUsageAgentMode.BACKGROUND,
        owner="runtime-owner",
        session_id="runtime-session",
        run_id="runtime-run",
        correlation_id="runtime-correlation",
        retry_ordinal=retry_ordinal,
        incognito=False,
    )


def _instrumentation(events, *, context=None):
    return ToolUsageInstrumentation(
        trusted_context=context or _context(),
        sink=events.append,
        hmac_key=HMAC_KEY,
        app_version="0.25.0",
        monotonic=SequenceClock((10.0, 10.1)),
        wall_clock=SequenceClock((FIXED_TIME, FIXED_TIME + timedelta(milliseconds=100))),
    )


def teardown_function(_function):
    unregister_tool("usage_plugin")


def test_catalog_resolution_covers_builtin_plugin_mcp_and_safe_unknown_identity():
    async def plugin_execute(_content, **_kwargs):
        return {"exit_code": 0}

    register_tool(
        ToolSpec(
            name="usage_plugin",
            description="Synthetic source adapter.",
            parameters={"type": "object", "properties": {}},
            execute=plugin_execute,
            permission="user",
        )
    )

    builtin = build_tool_usage_call_metadata_for_name("read_file")
    plugin = build_tool_usage_call_metadata_for_name("usage_plugin")
    mcp = build_tool_usage_call_metadata_for_name("mcp__review__inspect")
    malformed = build_tool_usage_call_metadata_for_name("mcp__malformed")

    assert builtin.tool_source == ToolSource.BUILTIN
    assert plugin.tool_source == ToolSource.PLUGIN
    assert plugin.tool_analytics_id == "usage-plugin"
    assert mcp.tool_source == ToolSource.MCP
    assert malformed.tool_source == ToolSource.DYNAMIC
    assert malformed.tool_analytics_id == "dynamic-unclassified"
    assert parse_qualified_mcp_tool_name("mcp__review__inspect") == (
        "review",
        "inspect",
    )
    assert parse_qualified_mcp_tool_name("mcp__malformed") is None


def test_explicit_bypass_adapter_supports_every_bounded_source_value():
    seen = set()
    for source in ToolSource:
        metadata = build_bypass_tool_usage_call_metadata(
            ToolUsageSourceIdentity(
                tool_analytics_id=f"{source.value}-adapter",
                tool_family=ToolFamily.PLUGINS_MCP,
                tool_source=source,
            ),
            argument_bytes=17,
        )
        seen.add(metadata.tool_source)
        assert metadata.argument_present is True
        assert metadata.argument_bytes == 17

    assert seen == set(ToolSource)


@pytest.mark.asyncio
async def test_central_wrapper_emits_one_pair_for_each_runtime_source(monkeypatch):
    async def plugin_execute(_content, **_kwargs):
        return {"exit_code": 0}

    register_tool(
        ToolSpec(
            name="usage_plugin",
            description="Synthetic source adapter.",
            parameters={"type": "object", "properties": {}},
            execute=plugin_execute,
            permission="user",
        )
    )

    async def execute(block, **_kwargs):
        return f"{block.tool_type}: ok", {"exit_code": 0}

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)
    cases = (
        ("read_file", ToolSource.BUILTIN),
        ("usage_plugin", ToolSource.PLUGIN),
        ("mcp__review__inspect", ToolSource.MCP),
    )
    for tool_name, expected_source in cases:
        events = []
        outcome = await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type=tool_name, content="{}"),
            tool_usage_instrumentation=_instrumentation(events),
        )

        assert outcome[1]["exit_code"] == 0
        assert len(events) == 2
        assert events[0].invocation_id == events[1].invocation_id
        assert {event.tool_source for event in events} == {expected_source}


@pytest.mark.asyncio
async def test_bound_scheduler_mcp_bypass_counts_once_and_unbound_call_counts_zero():
    manager = McpManager()
    manager._tools["review"] = [
        {"name": "inspect", "description": "Inspect.", "input_schema": {}}
    ]

    class Session:
        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(text="synthetic")],
                isError=False,
            )

    manager._sessions["review"] = Session()
    events = []
    instrumentation = _instrumentation(events)

    with bind_bypass_tool_usage_instrumentation(instrumentation):
        result = await manager.call_tool("mcp__review__inspect", {"value": "synthetic"})
    assert result["exit_code"] == 0
    assert len(events) == 2
    assert events[0].invocation_id == events[1].invocation_id
    assert {event.tool_source for event in events} == {ToolSource.MCP}
    assert {event.surface for event in events} == {ToolUsageSurface.SCHEDULER}

    await manager.call_tool("mcp__review__inspect", {"value": "synthetic"})
    assert len(events) == 2


@pytest.mark.asyncio
async def test_mcp_transport_reconnect_remains_one_logical_invocation(monkeypatch):
    manager = McpManager()
    manager._tools["review"] = [
        {"name": "inspect", "description": "Inspect.", "input_schema": {}}
    ]
    manager._sessions["review"] = object()
    attempts = []

    async def do_call(_session, _tool_name, _arguments):
        attempts.append("transport")
        if len(attempts) == 1:
            raise RuntimeError("synthetic transport failure")
        return {"stdout": "synthetic", "stderr": "", "exit_code": 0}

    async def reconnect(_server_id):
        manager._sessions["review"] = object()
        return True

    monkeypatch.setattr(manager, "_do_call", do_call)
    monkeypatch.setattr(manager, "is_builtin", lambda _server_id: True)
    monkeypatch.setattr(manager, "_reconnect_builtin", reconnect)
    events = []

    with bind_bypass_tool_usage_instrumentation(_instrumentation(events)):
        result = await manager.call_tool("mcp__review__inspect", {})

    assert result["exit_code"] == 0
    assert attempts == ["transport", "transport"]
    assert len(events) == 2
    assert events[0].invocation_id == events[1].invocation_id
    assert {event.retry_ordinal for event in events} == {0}


@pytest.mark.asyncio
async def test_malformed_direct_mcp_call_is_rejected_without_raw_telemetry_name():
    raw_name = "mcp__malformed-private-name"
    events = []
    manager = McpManager()

    with bind_bypass_tool_usage_instrumentation(_instrumentation(events)):
        result = await manager.call_tool(raw_name, {"value": "synthetic"})

    assert result == {"error": "Invalid MCP tool name", "exit_code": 1}
    assert len(events) == 2
    assert events[-1].status == ToolUsageStatus.REJECTED
    encoded = json.dumps([event.to_safe_dict() for event in events], sort_keys=True)
    assert raw_name not in encoded


@pytest.mark.asyncio
async def test_unknown_direct_mcp_call_is_rejected_without_raw_telemetry_name():
    raw_name = "mcp__review__unregistered-private-name"
    events = []
    manager = McpManager()

    with bind_bypass_tool_usage_instrumentation(_instrumentation(events)):
        result = await manager.call_tool(raw_name, {})

    assert result["exit_code"] == 1
    assert len(events) == 2
    assert events[-1].status == ToolUsageStatus.REJECTED
    assert events[-1].blocked_reason_code.value == "unknown_tool"
    encoded = json.dumps([event.to_safe_dict() for event in events], sort_keys=True)
    assert raw_name not in encoded


@pytest.mark.asyncio
async def test_scheduler_masks_direct_adapter_while_central_stream_advances():
    scheduler = TaskScheduler(session_manager=None)
    events = []
    instrumentation = _instrumentation(events)
    observed = []

    async def central_stream():
        observed.append(current_bypass_tool_usage_instrumentation())
        yield "data: [DONE]"

    with bind_bypass_tool_usage_instrumentation(instrumentation):
        yielded = [
            event
            async for event in scheduler._without_bypass_tool_usage_instrumentation(
                central_stream()
            )
        ]
        assert current_bypass_tool_usage_instrumentation() is instrumentation

    assert yielded == ["data: [DONE]"]
    assert observed == [None]
    assert events == []


@pytest.mark.asyncio
async def test_scheduler_binds_both_proven_direct_mcp_bypasses(monkeypatch):
    bound = []

    def factory(context):
        return ToolUsageInstrumentation(trusted_context=context)

    async def execute_checkin(*_args, **_kwargs):
        bound.append(("checkin", current_bypass_tool_usage_instrumentation()))
        return "synthetic checkin"

    async def deliver_via_mcp(*_args, **_kwargs):
        bound.append(("delivery", current_bypass_tool_usage_instrumentation()))
        return {"status": "success"}

    monkeypatch.setattr("src.task_scheduler_checkin.execute_checkin", execute_checkin)
    monkeypatch.setattr("src.task_scheduler_delivery.deliver_via_mcp", deliver_via_mcp)
    scheduler = TaskScheduler(
        session_manager=None,
        tool_usage_instrumentation_factory=factory,
    )
    task = SimpleNamespace(
        id="task-1",
        owner="runtime-owner",
        session_id="session-1",
        endpoint_url="http://127.0.0.1:11434/v1",
    )

    result = await scheduler._execute_checkin(
        task,
        crew=None,
        db=None,
        session_id="session-1",
        endpoint_url=task.endpoint_url,
        model="synthetic-model",
        run_id="run-1",
    )
    await scheduler._deliver_via_mcp(
        "mcp__review__inspect",
        task,
        "synthetic result",
        run_id="run-1",
    )

    assert result == "synthetic checkin"
    assert [label for label, _instrumentation in bound] == ["checkin", "delivery"]
    assert all(
        isinstance(instrumentation, ToolUsageInstrumentation)
        for _label, instrumentation in bound
    )


def test_retry_and_correlation_are_explicit_hmac_scoped_event_fields():
    events = []
    instrumentation = _instrumentation(events, context=_context(retry_ordinal=2))
    metadata = build_tool_usage_call_metadata_for_name("read_file")
    span = instrumentation.begin(metadata)
    from src.tool_usage_instrumentation import classify_tool_usage_outcome

    span.finish(
        classify_tool_usage_outcome("read_file: ok", {"exit_code": 0}),
        result={"exit_code": 0},
    )

    assert {event.retry_ordinal for event in events} == {2}
    assert {event.correlation_ref for event in events} == {
        pseudonymize_reference(
            "runtime-correlation",
            hmac_key=HMAC_KEY,
            kind="correlation",
        )
    }


def test_scheduler_factory_is_default_off_and_never_runs_for_preview():
    calls = []

    def factory(context):
        calls.append(context)
        return ToolUsageInstrumentation(trusted_context=context)

    task = SimpleNamespace(id="task-1", owner="runtime-owner")
    default_scheduler = TaskScheduler(session_manager=None)
    assert default_scheduler._tool_usage_instrumentation_for_execution(
        task,
        session_id="session-1",
        run_id="run-1",
        endpoint_url="http://127.0.0.1:11434/v1",
        execution_started=True,
    ) is None

    scheduler = TaskScheduler(
        session_manager=None,
        tool_usage_instrumentation_factory=factory,
    )
    assert scheduler._tool_usage_instrumentation_for_execution(
        task,
        session_id="session-1",
        run_id="run-1",
        endpoint_url="http://127.0.0.1:11434/v1",
        execution_started=False,
    ) is None
    assert calls == []

    instrumentation = scheduler._tool_usage_instrumentation_for_execution(
        task,
        session_id="session-1",
        run_id="run-1",
        endpoint_url="http://127.0.0.1:11434/v1",
        execution_started=True,
    )
    assert instrumentation is not None
    assert calls[0].surface == ToolUsageSurface.SCHEDULER
    assert calls[0].agent_mode == ToolUsageAgentMode.BACKGROUND
    assert calls[0].model_scope == ToolUsageModelScope.LOCAL
