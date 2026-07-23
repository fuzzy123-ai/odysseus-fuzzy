from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from routes.chat_helpers import build_trusted_chat_tool_usage_context
from src.agent_loop import _bind_tool_usage_instrumentation
from src.tool_execution import execute_tool_block
from src.tool_usage_context import TrustedToolUsageContext, trusted_model_scope
from src.tool_usage_events import ToolUsageEventBuilder, pseudonymize_reference
from src.tool_usage_instrumentation import ToolUsageInstrumentation


HMAC_KEY = b"context-key-" * 3
FIXED_TIME = datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc)


class _Sink:
    def __init__(self):
        self.events = []

    def append_best_effort(self, events):
        self.events.extend(events)
        return SimpleNamespace(failure_count=0)


def _instrumentation(context, sink, *, key=HMAC_KEY):
    return ToolUsageInstrumentation(
        builder=ToolUsageEventBuilder(app_version="0.25.0", hmac_key=key),
        sink=sink,
        context=context,
        clock=lambda: FIXED_TIME,
    )


def test_trusted_context_is_bounded_and_audit_packet_excludes_raw_references():
    context = TrustedToolUsageContext.create(
        surface="chat",
        agent_mode="agent",
        model_scope="local",
        owner_identity="private-owner",
        session_identity="private-session",
        run_identity="private-run",
        correlation_identity="private-correlation",
    )

    encoded = repr(context.audit_dict()) + repr(context)
    assert context.persistence_allowed is True
    assert context.audit_dict()["surface"] == "chat"
    assert context.audit_dict()["agent_mode"] == "agent"
    assert context.audit_dict()["model_scope"] == "local"
    for private_value in (
        "private-owner",
        "private-session",
        "private-run",
        "private-correlation",
    ):
        assert private_value not in encoded
    assert context.audit_dict()["raw_content_visible"] is False


@pytest.mark.parametrize(
    ("endpoint", "scope"),
    [
        ("http://127.0.0.1:11434", "local"),
        ("http://localhost:8000/v1", "local"),
        ("https://models.example.test/v1", "remote"),
        ("", "unknown"),
    ],
)
def test_model_scope_uses_only_server_selected_endpoint(endpoint, scope):
    assert trusted_model_scope(endpoint).value == scope


def test_chat_context_builder_marks_mixed_fallback_scope_and_server_run_identity():
    context = build_trusted_chat_tool_usage_context(
        owner="alice",
        session_id="session-1",
        endpoint_urls=["http://localhost:11434", "https://api.example.test/v1"],
        agent_mode=True,
        incognito=False,
        run_identity="server-run-1",
    )

    assert context.surface.value == "chat"
    assert context.agent_mode.value == "agent"
    assert context.model_scope.value == "mixed"
    assert context.owner_identity == "alice"
    assert context.session_identity == "session-1"
    assert context.run_identity == "server-run-1"
    assert context.correlation_identity == "session-1"


@pytest.mark.asyncio
async def test_tool_arguments_cannot_spoof_identity_surface_mode_or_model_scope(monkeypatch):
    trusted = TrustedToolUsageContext.create(
        surface="chat",
        agent_mode="agent",
        model_scope="local",
        owner_identity="trusted-owner",
        session_identity="trusted-session",
        run_identity="trusted-run",
        correlation_identity="trusted-correlation",
    )
    sink = _Sink()
    instrumentation = _instrumentation(trusted, sink)
    spoofed_arguments = (
        '{"owner":"attacker","session":"attacker-session",'
        '"surface":"system","agent_mode":"background_system",'
        '"model_scope":"remote"}'
    )

    async def _impl(*_args, **_kwargs):
        return "read_file", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr("src.tool_execution._execute_tool_block_impl", _impl)
    await execute_tool_block(
        SimpleNamespace(tool_type="read_file", content=spoofed_arguments),
        tool_usage_instrumentation=instrumentation,
    )

    assert len(sink.events) == 2
    for event in sink.events:
        assert event.surface.value == "chat"
        assert event.agent_mode.value == "agent"
        assert event.model_scope.value == "local"
        assert event.owner_ref == pseudonymize_reference("owner", "trusted-owner", key=HMAC_KEY)
        assert event.session_ref == pseudonymize_reference("session", "trusted-session", key=HMAC_KEY)
        assert event.run_ref == pseudonymize_reference("run", "trusted-run", key=HMAC_KEY)
        assert event.correlation_ref == pseudonymize_reference(
            "correlation", "trusted-correlation", key=HMAC_KEY
        )
    encoded = " ".join(event.to_json() for event in sink.events)
    assert "attacker" not in encoded
    assert "attacker-session" not in encoded


def test_missing_hmac_key_produces_null_references_without_raw_fallback():
    context = TrustedToolUsageContext.create(
        surface="chat",
        agent_mode="agent",
        owner_identity="private-owner",
        session_identity="private-session",
    )
    sink = _Sink()
    instrumentation = _instrumentation(context, sink, key=None)

    invocation = instrumentation.begin("read_file", "private argument")

    assert invocation is not None
    event = sink.events[0]
    assert event.owner_ref is None
    assert event.session_ref is None
    assert event.reference_state.value == "unavailable"
    assert "private-owner" not in event.to_json()
    assert "private-session" not in event.to_json()


def test_agent_loop_binding_requires_trusted_context_and_is_fail_open():
    context = TrustedToolUsageContext.create(surface="chat", agent_mode="agent")

    class _Factory:
        def with_context(self, supplied):
            assert supplied is context
            return "bound"

    class _BrokenFactory:
        def with_context(self, _supplied):
            raise RuntimeError("private bind failure")

    assert _bind_tool_usage_instrumentation(context, _Factory()) == "bound"
    assert _bind_tool_usage_instrumentation(None, _Factory()) is None
    assert _bind_tool_usage_instrumentation(context, None) is None
    assert _bind_tool_usage_instrumentation(context, _BrokenFactory()) is None
