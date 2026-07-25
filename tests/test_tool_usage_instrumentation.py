import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import src.tool_execution as tool_execution
from src.tool_usage_events import ToolUsageEventBuilder
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    normalize_tool_usage_outcome,
)


FIXED_TIME = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)


class _Sink:
    def __init__(self, *, fail=False):
        self.events = []
        self.fail = fail

    def append_best_effort(self, events):
        if self.fail:
            raise RuntimeError("private sink failure")
        self.events.extend(events)
        return SimpleNamespace(failure_count=0)


class _Emitter:
    def __init__(self, *, fail=False):
        self.events = []
        self.fail = fail

    def emit(self, **event):
        if self.fail:
            raise RuntimeError("private lens failure")
        self.events.append(event)

    def record_rejection(self, _reason):
        return None


def _instrumentation(sink=None):
    return ToolUsageInstrumentation(
        builder=ToolUsageEventBuilder(app_version="0.25.0", hmac_key=None),
        sink=sink or _Sink(),
        clock=lambda: FIXED_TIME,
    )


def _block(name="read_file", content="private argument body"):
    return SimpleNamespace(tool_type=name, content=content)


def _patch_impl(monkeypatch, *, outcome=None, error=None):
    async def _impl(*_args, **_kwargs):
        if error is not None:
            raise error
        return outcome

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", _impl)


@pytest.mark.asyncio
async def test_success_emits_one_start_and_terminal_without_changing_return_tuple(monkeypatch):
    result = {"output": "private result body", "exit_code": 0}
    returned = ("read_file: private path", result)
    _patch_impl(monkeypatch, outcome=returned)
    ticks = iter((10.0, 10.125))
    monkeypatch.setattr(tool_execution.time, "perf_counter", lambda: next(ticks))
    sink = _Sink()

    actual = await tool_execution.execute_tool_block(
        _block(),
        tool_usage_instrumentation=_instrumentation(sink),
    )

    assert actual is returned
    assert actual[1] is result
    assert [event.event_kind.value for event in sink.events] == ["started", "terminal"]
    assert sink.events[0].invocation_id == sink.events[1].invocation_id
    assert sink.events[1].status.value == "succeeded"
    assert sink.events[1].duration_ms == 125
    encoded = " ".join(event.to_json() for event in sink.events)
    assert "private argument body" not in encoded
    assert "private result body" not in encoded
    assert "private path" not in encoded


@pytest.mark.asyncio
async def test_blocked_result_uses_bounded_reason_and_shared_lens_status(monkeypatch):
    returned = (
        "bash: BLOCKED by policy",
        {"error": "Tool blocked by policy", "exit_code": 1},
    )
    _patch_impl(monkeypatch, outcome=returned)
    sink = _Sink()
    emitter = _Emitter()

    actual = await tool_execution.execute_tool_block(
        _block("bash"),
        tool_usage_instrumentation=_instrumentation(sink),
        ai_lens_emitter=emitter,
    )

    terminal = sink.events[-1]
    assert actual is returned
    assert terminal.status.value == "blocked"
    assert terminal.blocked_reason_code.value == "policy"
    assert terminal.error_class is None
    assert [event["status"] for event in emitter.events] == ["started", "blocked"]


@pytest.mark.asyncio
async def test_exception_emits_failed_terminal_and_propagates_same_exception(monkeypatch):
    failure = RuntimeError("private main-path failure")
    _patch_impl(monkeypatch, error=failure)
    sink = _Sink()

    with pytest.raises(RuntimeError) as exc_info:
        await tool_execution.execute_tool_block(
            _block(),
            tool_usage_instrumentation=_instrumentation(sink),
        )

    assert exc_info.value is failure
    assert [event.event_kind.value for event in sink.events] == ["started", "terminal"]
    assert sink.events[-1].status.value == "failed"
    assert sink.events[-1].error_class.value == "execution"
    assert "private main-path failure" not in sink.events[-1].to_json()


@pytest.mark.asyncio
async def test_cancellation_is_terminal_cancelled_and_never_success(monkeypatch):
    _patch_impl(monkeypatch, error=asyncio.CancelledError())
    sink = _Sink()

    with pytest.raises(asyncio.CancelledError):
        await tool_execution.execute_tool_block(
            _block(),
            tool_usage_instrumentation=_instrumentation(sink),
        )

    assert [event.event_kind.value for event in sink.events] == ["started", "terminal"]
    assert sink.events[-1].status.value == "cancelled"
    assert sink.events[-1].error_class.value == "cancelled"


def test_invocation_accepts_at_most_one_terminal_event():
    sink = _Sink()
    instrumentation = _instrumentation(sink)
    invocation = instrumentation.begin("read_file", "private")
    outcome = normalize_tool_usage_outcome(result={"output": "ok", "exit_code": 0})

    instrumentation.finish(invocation, outcome=outcome, duration_ms=5)
    instrumentation.finish(invocation, outcome=outcome, duration_ms=9)

    assert [event.event_kind.value for event in sink.events] == ["started", "terminal"]
    assert sink.events[-1].duration_ms == 5


@pytest.mark.asyncio
async def test_usage_sink_failure_and_lens_failure_are_independent_and_fail_open(monkeypatch):
    result = {"output": "unchanged", "exit_code": 0}
    returned = ("read_file", result)
    _patch_impl(monkeypatch, outcome=returned)
    broken_sink = _Sink(fail=True)
    instrumentation = _instrumentation(broken_sink)
    working_lens = _Emitter()

    actual = await tool_execution.execute_tool_block(
        _block(),
        tool_usage_instrumentation=instrumentation,
        ai_lens_emitter=working_lens,
    )

    assert actual is returned
    assert actual[1] is result
    assert [event["status"] for event in working_lens.events] == ["started", "succeeded"]
    assert instrumentation.diagnostics()["failures"] == {"sink_failure": 2}

    working_sink = _Sink()
    actual_again = await tool_execution.execute_tool_block(
        _block(),
        tool_usage_instrumentation=_instrumentation(working_sink),
        ai_lens_emitter=_Emitter(fail=True),
    )
    assert actual_again is returned
    assert [event.event_kind.value for event in working_sink.events] == ["started", "terminal"]


@pytest.mark.asyncio
async def test_private_dynamic_runtime_name_maps_only_to_mcp_source_bucket(monkeypatch):
    private_name = "mcp__alice@example.test__session-private-note"
    _patch_impl(monkeypatch, outcome=("mcp", {"output": "ok", "exit_code": 0}))
    sink = _Sink()

    await tool_execution.execute_tool_block(
        _block(private_name),
        tool_usage_instrumentation=_instrumentation(sink),
    )

    assert {event.tool_analytics_id for event in sink.events} == {
        "dynamic.mcp.unclassified"
    }
    assert {event.tool_source.value for event in sink.events} == {"mcp"}
    assert private_name not in " ".join(event.to_json() for event in sink.events)


@pytest.mark.asyncio
async def test_instrumentation_is_default_off_when_not_injected(monkeypatch):
    returned = ("read_file", {"output": "ok", "exit_code": 0})
    _patch_impl(monkeypatch, outcome=returned)
    monkeypatch.setattr(
        tool_execution,
        "_normalized_tool_usage_outcome",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("default-off path imported usage")),
    )

    assert await tool_execution.execute_tool_block(_block()) is returned
