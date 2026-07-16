import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import json
from types import SimpleNamespace

import pytest

import src.tool_execution as tool_execution
from src.tool_usage_events import ToolUsageStatus
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    build_tool_usage_call_metadata,
    classify_tool_usage_outcome,
)


FIXED_TIME = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)


class SequenceClock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def _instrumentation(events, *, sink=None, monotonic=(10.0, 10.125), **kwargs):
    wall_values = (FIXED_TIME, FIXED_TIME + timedelta(milliseconds=125))
    return ToolUsageInstrumentation(
        sink=events.append if sink is None else sink,
        hmac_key=b"synthetic-local-key-material",
        app_version="0.25.0",
        monotonic=SequenceClock(monotonic),
        wall_clock=SequenceClock(wall_values),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_success_emits_one_start_terminal_pair_and_preserves_result_identity(monkeypatch):
    events = []
    instrumentation = _instrumentation(events)
    block = SimpleNamespace(tool_type="read_file", content="private path and content")
    expected_result = {"stdout": "private result", "exit_code": 0}
    expected_outcome = ("read_file: ok", expected_result)

    async def execute(*_args, **_kwargs):
        return expected_outcome

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)
    outcome = await tool_execution.execute_tool_block(
        block,
        owner="private-owner",
        session_id="private-session",
        tool_usage_instrumentation=instrumentation,
    )

    assert outcome is expected_outcome
    assert outcome[1] is expected_result
    assert len(events) == 2
    assert events[0].invocation_id == events[1].invocation_id
    assert events[0].event_kind.value == "started"
    assert events[1].event_kind.value == "terminal"
    assert events[1].status == ToolUsageStatus.SUCCEEDED
    assert events[1].duration_ms == 125
    assert events[0].owner_ref.startswith("h1_owner_")
    assert events[0].session_ref.startswith("h1_session_")
    encoded = json.dumps([event.to_safe_dict() for event in events], sort_keys=True)
    for private_value in (
        "private path and content",
        "private result",
        "private-owner",
        "private-session",
    ):
        assert private_value not in encoded


@pytest.mark.asyncio
async def test_blocked_unknown_exception_and_cancellation_have_bounded_terminal_statuses(monkeypatch):
    cases = [
        (
            ("read_file: BLOCKED", {"error": "disabled by user", "exit_code": 1}),
            ToolUsageStatus.BLOCKED,
        ),
        (
            ("unknown: private_dynamic_tool", {"error": "unknown", "exit_code": 1}),
            ToolUsageStatus.REJECTED,
        ),
    ]
    for index, (returned, expected_status) in enumerate(cases):
        events = []
        instrumentation = _instrumentation(
            events,
            monotonic=(20.0 + index, 20.1 + index),
        )

        async def execute(*_args, _returned=returned, **_kwargs):
            return _returned

        monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)
        await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="read_file", content="private"),
            tool_usage_instrumentation=instrumentation,
        )
        assert [event.status for event in events if event.status] == [expected_status]

    error_events = []
    error_instrumentation = _instrumentation(error_events, monotonic=(30.0, 30.1))
    original = RuntimeError("private main path exception")

    async def fail(*_args, **_kwargs):
        raise original

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", fail)
    with pytest.raises(RuntimeError) as raised:
        await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="read_file", content="private"),
            tool_usage_instrumentation=error_instrumentation,
        )
    assert raised.value is original
    assert error_events[-1].status == ToolUsageStatus.FAILED

    cancelled_events = []
    cancelled_instrumentation = _instrumentation(
        cancelled_events,
        monotonic=(40.0, 40.1),
    )

    async def cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", cancel)
    with pytest.raises(asyncio.CancelledError):
        await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="read_file", content="private"),
            tool_usage_instrumentation=cancelled_instrumentation,
        )
    assert cancelled_events[-1].status == ToolUsageStatus.CANCELLED


@pytest.mark.asyncio
async def test_sink_failure_is_fully_isolated_from_existing_tool_semantics(monkeypatch):
    class FailingSink:
        def __call__(self, _event):
            raise RuntimeError("private telemetry failure")

    instrumentation = _instrumentation([], sink=FailingSink())
    expected = ("read_file: ok", {"stdout": "unchanged", "exit_code": 0})

    async def execute(*_args, **_kwargs):
        return expected

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)
    actual = await tool_execution.execute_tool_block(
        SimpleNamespace(tool_type="read_file", content="private"),
        tool_usage_instrumentation=instrumentation,
    )

    assert actual is expected
    assert instrumentation.diagnostics()["counts"] == {"sink_failures": 2}
    assert "private telemetry failure" not in json.dumps(instrumentation.diagnostics())


def test_span_closes_once_and_default_sink_discards_without_persistence():
    instrumentation = ToolUsageInstrumentation(
        app_version="0.25.0",
        monotonic=SequenceClock((1.0, 1.1)),
        wall_clock=SequenceClock((FIXED_TIME, FIXED_TIME)),
    )
    metadata = build_tool_usage_call_metadata(
        SimpleNamespace(tool_type="read_file", content="private")
    )
    span = instrumentation.begin(metadata)
    outcome = classify_tool_usage_outcome("read_file: ok", {"exit_code": 0})

    span.finish(outcome, result={"private": "result"})
    span.finish(outcome, result={"private": "result"})

    assert instrumentation.diagnostics()["counts"] == {
        "discarded_events": 2,
        "duplicate_terminal_attempts": 1,
    }


def test_metadata_normalizes_unknown_and_mcp_without_raw_dynamic_identity():
    raw_unknown = "private_provider_tool_123"
    raw_mcp = "mcp__private-server__private-tool"
    unknown = build_tool_usage_call_metadata(
        SimpleNamespace(tool_type=raw_unknown, content="private")
    )
    mcp = build_tool_usage_call_metadata(
        SimpleNamespace(tool_type=raw_mcp, content="private")
    )
    encoded = json.dumps([unknown.to_safe_dict(), mcp.to_safe_dict()], sort_keys=True)

    assert unknown.tool_analytics_id == "dynamic-unclassified"
    assert mcp.tool_analytics_id == "dynamic-mcp"
    assert raw_unknown not in encoded
    assert raw_mcp not in encoded


def test_no_instrumentation_object_means_capture_is_default_off():
    metadata = build_tool_usage_call_metadata(
        SimpleNamespace(tool_type="read_file", content="private")
    )
    parameter = inspect.signature(
        tool_execution.execute_tool_block
    ).parameters["tool_usage_instrumentation"]

    assert metadata.to_safe_dict()["raw_content_visible"] is False
    assert parameter.default is None
