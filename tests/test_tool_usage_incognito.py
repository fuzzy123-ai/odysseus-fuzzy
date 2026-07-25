import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import src.tool_execution as tool_execution
from src.tool_usage_context import TrustedToolUsageContext
from src.tool_usage_events import ToolUsageEventBuilder
from src.tool_usage_instrumentation import ToolUsageInstrumentation


FIXED_TIME = datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc)


class _WriterSpy:
    def __init__(self):
        self.calls = 0
        self.events = []

    def append_best_effort(self, events):
        self.calls += 1
        self.events.extend(events)
        return SimpleNamespace(failure_count=0)


def _instrumentation(*, incognito=False, is_nobody=False):
    writer = _WriterSpy()
    context = TrustedToolUsageContext.create(
        surface="chat",
        agent_mode="agent",
        model_scope="remote",
        owner_identity="private-owner",
        session_identity="private-session",
        run_identity="private-run",
        incognito=incognito,
        is_nobody=is_nobody,
    )
    instrumentation = ToolUsageInstrumentation(
        builder=ToolUsageEventBuilder(app_version="0.25.0", hmac_key=b"i" * 32),
        sink=writer,
        context=context,
        clock=lambda: FIXED_TIME,
    )
    return instrumentation, writer


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        ({"incognito": True}, "incognito"),
        ({"is_nobody": True}, "nobody"),
    ],
)
def test_incognito_and_nobody_short_circuit_before_every_writer(flags, reason):
    instrumentation, writer = _instrumentation(**flags)

    assert instrumentation.begin("read_file", "private argument") is None
    assert instrumentation.begin("bash", "private failing argument") is None
    assert instrumentation.begin("unknown_private_tool", "private blocked argument") is None

    assert writer.calls == 0
    assert writer.events == []
    assert instrumentation.diagnostics()["suppressed"] == {reason: 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "raised"),
    [
        (("ask_user", {"output": "ok", "exit_code": 0}), None),
        (("ask_user: BLOCKED", {"error": "blocked", "exit_code": 1}), None),
        (None, RuntimeError("private main-path failure")),
    ],
)
async def test_incognito_writes_zero_records_for_success_block_or_exception(
    monkeypatch, outcome, raised
):
    instrumentation, writer = _instrumentation(incognito=True)
    impl_calls = 0

    async def _impl(*_args, **_kwargs):
        nonlocal impl_calls
        impl_calls += 1
        if raised is not None:
            raise raised
        return outcome

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", _impl)
    block = SimpleNamespace(tool_type="ask_user", content="private")
    if raised is None:
        assert await tool_execution.execute_tool_block(
            block,
            tool_usage_instrumentation=instrumentation,
        ) is outcome
    else:
        with pytest.raises(RuntimeError) as exc_info:
            await tool_execution.execute_tool_block(
                block,
                tool_usage_instrumentation=instrumentation,
            )
        assert exc_info.value is raised

    assert impl_calls == 1
    assert writer.calls == 0
    assert writer.events == []


@pytest.mark.asyncio
async def test_incognito_cancellation_writes_zero_records(monkeypatch):
    instrumentation, writer = _instrumentation(incognito=True)
    impl_calls = 0

    async def _impl(*_args, **_kwargs):
        nonlocal impl_calls
        impl_calls += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", _impl)
    with pytest.raises(asyncio.CancelledError):
        await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="ask_user", content="private"),
            tool_usage_instrumentation=instrumentation,
        )

    assert impl_calls == 1
    assert writer.calls == 0
    assert writer.events == []
