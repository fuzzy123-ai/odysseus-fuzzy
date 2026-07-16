from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import routes.chat_routes as chat_routes
import src.tool_execution as tool_execution
from routes.chat_helpers import build_trusted_tool_usage_context
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageModelScope,
    ToolUsageSurface,
    pseudonymize_reference,
)
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    build_tool_usage_call_metadata,
    classify_tool_usage_outcome,
)


FIXED_TIME = datetime(2026, 7, 16, 7, 0, tzinfo=timezone.utc)
HMAC_KEY = b"synthetic-context-key-material"


class SequenceClock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


def _instrumentation(context, events, *, hmac_key=HMAC_KEY):
    return ToolUsageInstrumentation(
        trusted_context=context,
        sink=events.append,
        hmac_key=hmac_key,
        app_version="0.25.0",
        monotonic=SequenceClock((10.0, 10.1)),
        wall_clock=SequenceClock((FIXED_TIME, FIXED_TIME + timedelta(milliseconds=100))),
    )


def test_chat_helper_builds_normalized_context_from_server_runtime_values():
    context = build_trusted_tool_usage_context(
        SimpleNamespace(endpoint_url="http://127.0.0.1:11434/v1"),
        owner="runtime-owner",
        session_id="runtime-session",
        run_id="runtime-run",
        agent_mode=True,
        incognito=False,
    )

    assert context.surface == ToolUsageSurface.AGENT
    assert context.agent_mode == ToolUsageAgentMode.AGENT
    assert context.model_scope == ToolUsageModelScope.LOCAL
    assert context.owner == "runtime-owner"
    assert context.session_id == "runtime-session"
    assert context.run_id == "runtime-run"
    assert context.incognito is False


def test_capture_requires_server_app_gate_and_matching_trusted_factory():
    context = build_trusted_tool_usage_context(
        SimpleNamespace(endpoint_url="https://models.example.invalid/v1"),
        owner="runtime-owner",
        session_id="runtime-session",
        run_id="runtime-run",
        agent_mode=False,
        incognito=False,
    )
    factory_calls = []

    def factory(received):
        factory_calls.append(received)
        return ToolUsageInstrumentation(trusted_context=received)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(tool_usage_instrumentation_factory=factory)
        )
    )
    assert chat_routes._tool_usage_instrumentation_from_runtime(request, context) is None
    assert factory_calls == []

    request.app.state.tool_usage_capture_enabled = True
    instrumentation = chat_routes._tool_usage_instrumentation_from_runtime(
        request,
        context,
    )
    assert instrumentation is not None
    assert instrumentation.trusted_context is context
    assert factory_calls == [context]


@pytest.mark.asyncio
async def test_tool_arguments_cannot_spoof_trusted_telemetry_identity(monkeypatch):
    context = build_trusted_tool_usage_context(
        SimpleNamespace(endpoint_url="https://models.example.invalid/v1"),
        owner="runtime-owner",
        session_id="runtime-session",
        run_id="runtime-run",
        agent_mode=True,
        incognito=False,
    )
    events = []
    instrumentation = _instrumentation(context, events)

    async def execute(*_args, **_kwargs):
        return "read_file: ok", {"exit_code": 0}

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)
    await tool_execution.execute_tool_block(
        SimpleNamespace(
            tool_type="read_file",
            content=json.dumps(
                {
                    "owner": "spoof-owner",
                    "session_id": "spoof-session",
                    "run_id": "spoof-run",
                    "surface": "system",
                    "incognito": False,
                }
            ),
        ),
        owner="wrapper-owner",
        session_id="wrapper-session",
        tool_usage_instrumentation=instrumentation,
    )

    assert len(events) == 2
    for event in events:
        assert event.owner_ref == pseudonymize_reference(
            "runtime-owner", hmac_key=HMAC_KEY, kind="owner"
        )
        assert event.session_ref == pseudonymize_reference(
            "runtime-session", hmac_key=HMAC_KEY, kind="session"
        )
        assert event.run_ref == pseudonymize_reference(
            "runtime-run", hmac_key=HMAC_KEY, kind="run"
        )
        assert event.surface == ToolUsageSurface.AGENT
        assert event.model_scope == ToolUsageModelScope.REMOTE
    encoded = json.dumps([event.to_safe_dict() for event in events], sort_keys=True)
    for forbidden in (
        "runtime-owner",
        "runtime-session",
        "runtime-run",
        "spoof-owner",
        "spoof-session",
        "spoof-run",
        "wrapper-owner",
        "wrapper-session",
    ):
        assert forbidden not in encoded


def test_missing_hmac_key_produces_null_references_without_raw_fallback():
    context = build_trusted_tool_usage_context(
        SimpleNamespace(endpoint_url="https://models.example.invalid/v1"),
        owner="runtime-owner",
        session_id="runtime-session",
        run_id="runtime-run",
        agent_mode=True,
        incognito=False,
    )
    events = []
    instrumentation = _instrumentation(context, events, hmac_key=None)
    metadata = build_tool_usage_call_metadata(
        SimpleNamespace(tool_type="read_file", content="private")
    )

    span = instrumentation.begin(
        metadata,
        owner="wrapper-owner",
        session_id="wrapper-session",
    )
    span.finish(
        classify_tool_usage_outcome("read_file: ok", {"exit_code": 0}),
        result={"exit_code": 0},
    )

    assert len(events) == 2
    assert all(event.owner_ref is None for event in events)
    assert all(event.session_ref is None for event in events)
    assert all(event.run_ref is None for event in events)
