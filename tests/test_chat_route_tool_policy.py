"""Issue #3229 — allow_bash / allow_web_search must work for JSON API callers
and admin users must get bash enabled by default.

Bug: allow_bash and allow_web_search were only read from form_data, so JSON
API callers (Content-Type: application/json) always had bash disabled.

Fix: (1) Read from JSON body as fallback.
     (2) Only add bash/web_search to disabled_tools when explicitly set to a
         falsy value; when unset (None), defer to per-user privilege checks.
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


# ── Source-level guards ─────────────────────────────────────────


def test_allow_bash_reads_from_body_as_fallback():
    """chat_stream must read allow_bash from the JSON body, not just form_data."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the chat_stream function
    chat_stream_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            chat_stream_func = node
            break
    assert chat_stream_func is not None, "chat_stream function not found"

    # Look for an assignment to allow_bash that references 'body'
    found_body_fallback = False
    for node in ast.walk(chat_stream_func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "allow_bash":
                    # Check if 'body' appears in the value
                    src_segment = ast.get_source_segment(source, node)
                    if src_segment and "body" in src_segment:
                        found_body_fallback = True
    assert found_body_fallback, (
        "allow_bash assignment in chat_stream must fall back to JSON body"
    )


def test_allow_web_search_reads_from_body_as_fallback():
    """chat_stream must read allow_web_search from the JSON body, not just form_data."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    chat_stream_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            chat_stream_func = node
            break
    assert chat_stream_func is not None

    found_body_fallback = False
    for node in ast.walk(chat_stream_func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "allow_web_search":
                    src_segment = ast.get_source_segment(source, node)
                    if src_segment and "body" in src_segment:
                        found_body_fallback = True
    assert found_body_fallback, (
        "allow_web_search assignment in chat_stream must fall back to JSON body"
    )


def test_disabled_tools_does_not_bash_when_allow_bash_is_none():
    """When allow_bash is not set (None), bash must NOT be unconditionally
    added to disabled_tools.  The per-user privilege check handles it.
    """
    source = _CHAT_ROUTES.read_text(encoding="utf-8")

    # The fix changes:
    #   if str(allow_bash).lower() != "true":
    # to:
    #   if allow_bash is not None and str(allow_bash).lower() != "true":
    assert "allow_bash is not None" in source, (
        "disabled_tools check must guard against allow_bash being None"
    )
    assert "allow_web_search is not None" in source, (
        "disabled_tools check must guard against allow_web_search being None"
    )
    assert "_explicit_web_intent" in source and "not _explicit_web_intent" in source, (
        "explicit web-search requests must override an off web toggle for that turn"
    )


# ── Functional tests of the disabled-tools logic ───────────────


def _build_disabled_tools(
    allow_bash=None,
    allow_web_search=None,
    can_use_bash=True,
    can_use_browser=True,
    explicit_web_intent=False,
):
    """Replicate the disabled-tools logic from chat_stream for unit testing.

    Returns the set of tool names that would be disabled.
    """
    disabled_tools = set()

    # Issue #3229 fix: only disable when explicitly set to a falsy value.
    if allow_bash is not None and str(allow_bash).lower() != "true":
        disabled_tools.add("bash")
    if (
        allow_web_search is not None
        and str(allow_web_search).lower() != "true"
        and not explicit_web_intent
    ):
        disabled_tools.add("web_search")
        disabled_tools.add("web_fetch")

    # Enforce per-user privileges
    if not can_use_bash:
        disabled_tools.update({"bash", "python", "read_file", "write_file"})
    if not can_use_browser:
        disabled_tools.add("builtin_browser")

    return disabled_tools


def test_json_body_allow_bash_true_enables_bash():
    """API caller sending {"allow_bash": true} gets bash enabled."""
    disabled = _build_disabled_tools(allow_bash="true")
    assert "bash" not in disabled


def test_json_body_allow_bash_false_disables_bash():
    """API caller sending {"allow_bash": false} gets bash disabled."""
    disabled = _build_disabled_tools(allow_bash="false")
    assert "bash" in disabled


def test_json_body_allow_web_search_true_enables_web():
    """API caller sending {"allow_web_search": true} gets web tools enabled."""
    disabled = _build_disabled_tools(allow_web_search="true")
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_json_body_allow_web_search_false_disables_web():
    """API caller sending {"allow_web_search": false} gets web tools disabled."""
    disabled = _build_disabled_tools(allow_web_search="false")
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_explicit_web_intent_overrides_false_web_toggle_for_turn():
    """A stale/off web toggle must not remove web tools when the message
    explicitly asks to use web search."""
    disabled = _build_disabled_tools(
        allow_web_search="false",
        explicit_web_intent=True,
    )
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_admin_user_gets_bash_enabled_by_default():
    """When allow_bash is not set and user has can_use_bash privilege,
    bash must NOT be disabled.
    """
    disabled = _build_disabled_tools(allow_bash=None, can_use_bash=True)
    assert "bash" not in disabled


def test_admin_user_gets_web_search_enabled_by_default():
    """When allow_web_search is not set and user has normal privileges,
    web_search must NOT be disabled.
    """
    disabled = _build_disabled_tools(allow_web_search=None)
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_non_privileged_user_without_explicit_flag_still_disabled():
    """A user without can_use_bash privilege who doesn't send allow_bash
    should still have bash disabled via the privilege check.
    """
    disabled = _build_disabled_tools(allow_bash=None, can_use_bash=False)
    assert "bash" in disabled


def test_non_privileged_user_explicit_true_overridden_by_privilege():
    """Even if allow_bash=true is sent, a user without can_use_bash
    privilege still gets bash disabled by the privilege gate.
    """
    disabled = _build_disabled_tools(allow_bash="true", can_use_bash=False)
    assert "bash" in disabled


def test_form_data_none_body_true_works():
    """Simulates: form_data has no allow_bash, body has allow_bash=true.
    After the fallback (`form_data.get(...) or body.get(...)`), allow_bash
    should be "true".
    """
    # Simulate the fallback logic
    form_data_val = None  # not in form_data
    body_val = "true"     # from JSON body
    allow_bash = form_data_val or body_val
    assert str(allow_bash).lower() == "true"

    disabled = _build_disabled_tools(allow_bash=allow_bash)
    assert "bash" not in disabled


def test_explicit_false_disables_even_for_admin():
    """An admin who explicitly sends allow_bash=false should have bash disabled."""
    disabled = _build_disabled_tools(
        allow_bash="false", can_use_bash=True,
    )
    assert "bash" in disabled


@pytest.mark.parametrize("instrumentation_mode", ["disabled", "failing"])
def test_agent_stream_reaches_terminal_sse_when_runtime_instrumentation_is_optional(
    monkeypatch, instrumentation_mode
):
    """The route must fail open when optional capture cannot bind.

    This exercises the actual ``/api/chat_stream`` Agent branch and drains its
    detached SSE subscription.  Before TUA4R the call expression itself raised
    ``NameError`` because the instrumentation name had not been initialized.
    """
    import routes.chat_routes as chat_routes
    from src.agent_loop import _bind_tool_usage_instrumentation
    import src.settings as settings

    private_message = "synthetic-private-message-must-not-reach-analytics"
    session = SimpleNamespace(
        endpoint_url="http://localhost:11434",
        model="synthetic-agent-model",
        headers={},
        name="",
        history=[],
    )

    class _SessionManager:
        def get_session(self, session_id):
            assert session_id == "synthetic-agent-session"
            return session

    class _Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def first(self):
            return None

    class _Database:
        def query(self, *_args):
            return _Query()

        def close(self):
            return None

    class _FailingInstrumentation:
        def __init__(self):
            self.bind_calls = []
            self.write_calls = 0

        def with_context(self, context):
            self.bind_calls.append(context)
            raise RuntimeError("synthetic capture bind failure")

    class _DetachedRuns:
        def __init__(self):
            self.streams = {}

        def start(self, session_id, stream):
            self.streams[session_id] = stream

        async def subscribe(self, session_id):
            async for chunk in self.streams.pop(session_id):
                yield chunk

    async def _context(*_args, **_kwargs):
        return SimpleNamespace(
            preprocessed=SimpleNamespace(attachment_meta=[]),
            preset=SimpleNamespace(character_name="", temperature=0, max_tokens=0),
            auto_opened_docs=[],
            rag_sources=[],
            web_sources=[],
            used_memories=[],
            was_compacted=False,
            context_length=0,
            messages=[{"role": "user", "content": private_message}],
            uprefs={},
            user=None,
        )

    observed = {}

    async def _agent_stream(*_args, **kwargs):
        observed.update(kwargs)
        # This is the same bind boundary used by stream_agent_loop. The
        # optional factory failure must resolve to None, never abort reply flow.
        assert _bind_tool_usage_instrumentation(
            kwargs["tool_usage_context"], kwargs["tool_usage_instrumentation"]
        ) is None
        yield 'data: {"type":"tool_start","tool":"read_file","round":1,"command":"synthetic"}\n\n'
        yield 'data: {"type":"tool_output","tool":"read_file","exit_code":0,"output":"synthetic"}\n\n'
        yield 'data: {"delta":"synthetic agent reply"}\n\n'
        yield "data: [DONE]\n\n"

    instrumentation = None if instrumentation_mode == "disabled" else _FailingInstrumentation()
    detached_runs = _DetachedRuns()
    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_chat_effective_user", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda _body, message, sid, *_args, **_kwargs: (message, sid))
    monkeypatch.setattr(chat_routes, "build_chat_context", _context)
    monkeypatch.setattr(chat_routes, "build_deterministic_capability_self_report", lambda _message: None)
    monkeypatch.setattr(chat_routes, "SessionLocal", _Database)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda _sid: "chat")
    monkeypatch.setattr(chat_routes, "set_session_mode", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_clarification_gate_for_session", lambda **_kwargs: {"open": False})
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "stream_agent_loop", _agent_stream)
    monkeypatch.setattr(chat_routes, "agent_runs", detached_runs)
    monkeypatch.setattr(settings, "get_setting", lambda _name, default=None: default)

    app = FastAPI()
    app.state.tool_usage_instrumentation = instrumentation
    app.include_router(
        chat_routes.setup_chat_routes(
            _SessionManager(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
    )

    response = TestClient(app).post(
        "/api/chat_stream",
        data={
            "message": private_message,
            "session": "synthetic-agent-session",
            "mode": "agent",
        },
        headers={"Authorization": "Bearer synthetic-analytics-compat"},
    )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert '"type":"tool_start"' in response.text
    assert "synthetic agent reply" in response.text
    assert private_message not in response.text
    assert observed["tool_usage_context"].persistence_allowed is True
    assert observed["tool_usage_instrumentation"] is instrumentation
    if instrumentation is None:
        assert observed["tool_usage_instrumentation"] is None
    else:
        assert len(instrumentation.bind_calls) == 1
        assert instrumentation.write_calls == 0


@pytest.mark.parametrize("instrumentation_mode", ["disabled", "failing"])
def test_agent_stream_executes_real_loop_tool_round_without_optional_analytics(
    monkeypatch, instrumentation_mode
):
    """Route integration: the real loop must survive optional analytics failure."""
    import core.database as database
    import routes.chat_routes as chat_routes
    import src.agent_loop as agent_loop
    import src.settings as settings
    import src.teacher_escalation as teacher_escalation
    import src.tool_index as tool_index
    from src.tool_usage_context import TrustedToolUsageContext

    private_message = "synthetic-private-route-input-must-not-be-emitted"
    session = SimpleNamespace(
        endpoint_url="https://api.openai.com/v1",
        model="gpt-4o",
        headers={},
        name="",
        history=[],
    )

    class _SessionManager:
        def get_session(self, session_id):
            assert session_id == "synthetic-real-loop-session"
            return session

    class _Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def first(self):
            return None

    class _Database:
        def query(self, *_args):
            return _Query()

        def close(self):
            return None

    class _FailingInstrumentation:
        def __init__(self, sink):
            self.bind_calls = []
            self.sink = sink

        def with_context(self, context):
            self.bind_calls.append(context)
            raise RuntimeError("synthetic capture bind failure")

    class _Sink:
        def __init__(self):
            self.write_calls = 0

        def append_best_effort(self, _events):
            self.write_calls += 1

    class _DetachedRuns:
        def __init__(self):
            self.streams = {}

        def start(self, session_id, stream):
            self.streams[session_id] = stream

        async def subscribe(self, session_id):
            async for chunk in self.streams.pop(session_id):
                yield chunk

    async def _context(*_args, **_kwargs):
        return SimpleNamespace(
            preprocessed=SimpleNamespace(attachment_meta=[]),
            preset=SimpleNamespace(character_name="", temperature=0, max_tokens=0),
            auto_opened_docs=[],
            rag_sources=[],
            web_sources=[],
            used_memories=[],
            was_compacted=False,
            context_length=0,
            messages=[{"role": "user", "content": private_message}],
            uprefs={},
            user=None,
        )

    model_round = {"count": 0}
    executed = []

    async def _model_stream(_candidates, _messages, **_kwargs):
        model_round["count"] += 1
        if model_round["count"] == 1:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "tool_calls",
                        "calls": [
                            {
                                "name": "bash",
                                "arguments": json.dumps({"command": "synthetic-tool-command"}),
                            }
                        ],
                    }
                )
                + "\n\n"
            )
        else:
            yield 'data: {"delta":"synthetic real-loop reply"}\n\n'
        yield "data: [DONE]\n\n"

    async def _tool(block, **kwargs):
        executed.append((block, kwargs.get("tool_usage_instrumentation")))
        assert kwargs["tool_usage_instrumentation"] is None
        return "bash", {"output": "synthetic-tool-result", "exit_code": 0}

    async def _no_teacher(*_args, **_kwargs):
        if False:
            yield ""

    sink = _Sink()
    instrumentation = None if instrumentation_mode == "disabled" else _FailingInstrumentation(sink)
    detached_runs = _DetachedRuns()

    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_chat_effective_user", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda _body, message, sid, *_args, **_kwargs: (message, sid))
    monkeypatch.setattr(chat_routes, "build_chat_context", _context)
    monkeypatch.setattr(chat_routes, "build_deterministic_capability_self_report", lambda _message: None)
    monkeypatch.setattr(chat_routes, "build_trusted_chat_tool_usage_context", lambda **_kwargs: TrustedToolUsageContext.create(surface="chat", agent_mode="agent"))
    monkeypatch.setattr(chat_routes, "SessionLocal", _Database)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda _sid: "chat")
    monkeypatch.setattr(chat_routes, "set_session_mode", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_clarification_gate_for_session", lambda **_kwargs: {"open": False})
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "agent_runs", detached_runs)
    monkeypatch.setattr(settings, "get_setting", lambda _name, default=None: default)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(context_length=4096, known=False)

    monkeypatch.setattr(agent_loop, "_resolve_agent_context_snapshot", _snapshot)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(tool_index, "get_tool_index", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "_load_mcp_disabled_map", lambda: {})
    monkeypatch.setattr(agent_loop, "_classify_agent_request", lambda *_args: {"domains": set(), "low_signal": False, "retrieval_query": "", "continuation": False})
    monkeypatch.setattr(agent_loop, "_detect_admin_intent", lambda _messages: False)
    monkeypatch.setattr(agent_loop, "_build_system_prompt", lambda messages, *_args, **_kwargs: (list(messages), []))
    monkeypatch.setattr(agent_loop, "_inject_context_provider_messages", lambda messages, **_kwargs: messages)
    monkeypatch.setattr(agent_loop, "get_function_tool_schemas", lambda: [{"type": "function", "function": {"name": "bash"}}])
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", _model_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", _tool)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(agent_loop, "get_setting", lambda name, default=None: 0 if name == "agent_input_token_budget" else default)
    monkeypatch.setattr(database, "SessionLocal", _Database)
    monkeypatch.setattr(teacher_escalation, "run_teacher_inline", _no_teacher)

    app = FastAPI()
    app.state.tool_usage_instrumentation = instrumentation
    app.include_router(
        chat_routes.setup_chat_routes(
            _SessionManager(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
    )

    response = TestClient(app).post(
        "/api/chat_stream",
        data={
            "message": private_message,
            "session": "synthetic-real-loop-session",
            "mode": "agent",
        },
        headers={"Authorization": "Bearer synthetic-analytics-compat"},
    )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert "synthetic real-loop reply" in response.text
    assert '"type": "tool_start"' in response.text
    assert "synthetic-tool-result" in response.text
    assert private_message not in response.text
    assert model_round["count"] == 2
    assert len(executed) == 1
    assert executed[0][0].tool_type == "bash"
    if instrumentation is None:
        assert executed[0][1] is None
    else:
        assert len(instrumentation.bind_calls) == 1
    assert sink.write_calls == 0


# ── Frontend source-level guards ──────────────────────────────

_CHAT_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "chat.js"


def test_frontend_always_sends_explicit_allow_bash():
    """chat.js must always send allow_bash (both true and false), not only on toggle ON."""
    source = _CHAT_JS.read_text(encoding="utf-8")
    # Must not only append 'true' — must also handle the false case
    assert "allow_bash', el('bash-toggle').checked ? 'true' : 'false'" in source or \
           "allow_bash', 'false'" in source, (
        "Frontend must send explicit allow_bash=false when toggle is off"
    )


def test_frontend_sends_explicit_allow_web_search_false_in_agent_mode():
    """chat.js must send allow_web_search=false when web toggle is off in agent mode."""
    source = _CHAT_JS.read_text(encoding="utf-8")
    assert "allow_web_search', 'false'" in source, (
        "Frontend must send explicit allow_web_search=false in agent mode when toggle is off"
    )
