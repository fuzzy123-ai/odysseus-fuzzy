import asyncio
from types import SimpleNamespace

import pytest

import src.tool_execution as tool_execution
import src.tool_usage_instrumentation as tool_usage_instrumentation
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageModelScope,
    ToolUsageSurface,
)
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    ToolUsageTrustedContext,
)


class RecordingSink:
    def __init__(self):
        self.write_calls = 0
        self.events = []

    def write_events(self, events):
        self.write_calls += 1
        self.events.extend(events)


def _incognito_instrumentation(sink):
    context = ToolUsageTrustedContext(
        surface=ToolUsageSurface.AGENT,
        model_scope=ToolUsageModelScope.LOCAL,
        agent_mode=ToolUsageAgentMode.AGENT,
        owner="runtime-owner",
        session_id="runtime-session",
        run_id="runtime-run",
        incognito=True,
    )
    return ToolUsageInstrumentation(
        trusted_context=context,
        sink=sink,
        hmac_key=b"synthetic-incognito-key-material",
        app_version="0.25.0",
        monotonic=lambda: 10.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    (
        ("read_file: ok", {"exit_code": 0}),
        ("read_file: failed", {"error": "private", "exit_code": 1}),
        ("read_file: BLOCKED", {"error": "disabled", "exit_code": 1}),
    ),
)
async def test_incognito_success_failure_and_blockade_never_reach_writer(
    monkeypatch,
    outcome,
):
    sink = RecordingSink()
    instrumentation = _incognito_instrumentation(sink)

    def forbid_pseudonymization(*_args, **_kwargs):
        raise AssertionError("incognito must short-circuit before HMAC work")

    async def execute(*_args, **_kwargs):
        return outcome

    monkeypatch.setattr(
        tool_usage_instrumentation,
        "pseudonymize_reference",
        forbid_pseudonymization,
    )
    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)

    actual = await tool_execution.execute_tool_block(
        SimpleNamespace(
            tool_type="read_file",
            content='{"owner":"spoof","incognito":false}',
        ),
        owner="wrapper-owner",
        session_id="wrapper-session",
        tool_usage_instrumentation=instrumentation,
    )

    assert actual is outcome
    assert sink.write_calls == 0
    assert sink.events == []
    assert instrumentation.diagnostics()["counts"] == {"suppressed_invocations": 1}


@pytest.mark.asyncio
async def test_incognito_exception_and_cancellation_remain_unpersisted(monkeypatch):
    for exception in (RuntimeError("private failure"), asyncio.CancelledError()):
        sink = RecordingSink()
        instrumentation = _incognito_instrumentation(sink)

        async def fail(*_args, _exception=exception, **_kwargs):
            raise _exception

        monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", fail)
        with pytest.raises(type(exception)):
            await tool_execution.execute_tool_block(
                SimpleNamespace(tool_type="read_file", content="private"),
                tool_usage_instrumentation=instrumentation,
            )

        assert sink.write_calls == 0
        assert sink.events == []
        assert instrumentation.diagnostics()["counts"] == {
            "suppressed_invocations": 1
        }
