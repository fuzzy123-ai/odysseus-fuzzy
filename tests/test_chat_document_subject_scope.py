import asyncio
import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from core.auth import AuthManager
from core.database import Base, Document, Session
from routes import chat_routes
from src import agent_tools, chat_handler, tool_execution
from src.chat_handler import ChatHandler
from src.agent_tools.document_tools import _sealed_document_result
from src.document_subject_scope import DocumentRequestAuthority


def _request(manager, token, *, api_token=False, authorization=None):
    app = FastAPI()
    if manager is not None:
        app.state.auth_manager = manager
    headers = []
    if token:
        headers.append((b"cookie", f"odysseus_session={token}".encode("ascii")))
    if authorization:
        headers.append((b"authorization", authorization.encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat_stream",
        "raw_path": b"/api/chat_stream",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 43100),
        "server": ("testserver", 80),
        "app": app,
    }
    request = Request(scope)
    request.state.api_token = api_token
    return request


def _browser(tmp_path):
    manager = AuthManager(str(tmp_path / "chat-auth.json"))
    assert manager.create_user("admin", "password-123", is_admin=True)
    assert manager.create_user("alice", "password-123")
    token = manager.create_session("alice", "password-123")
    assert token
    return manager, _request(manager, token)


def test_chat_capture_uses_real_cookie_and_only_api_compatibility_is_none(tmp_path):
    _, request = _browser(tmp_path)
    authority = chat_routes._capture_chat_document_authority(request)
    assert type(authority) is DocumentRequestAuthority

    api_request = _request(None, None, api_token=True, authorization="Bearer ody_test")
    assert chat_routes._capture_chat_document_authority(api_request) is None

    forged = _request(object(), None)
    with pytest.raises(Exception):
        chat_routes._capture_chat_document_authority(forged)


def test_active_document_snapshot_is_bounded_and_immutable():
    snapshot = chat_routes._ActiveDocumentSnapshot(
        id="doc-1",
        session_id="session-1",
        title="Title",
        language="markdown",
        current_content="Body",
        version_count=3,
        is_active=True,
        archived=False,
    )
    assert snapshot.current_content == "Body"
    assert not hasattr(snapshot, "_sa_instance_state")
    with pytest.raises(FrozenInstanceError):
        snapshot.version_count = 4


def test_chat_handler_forwards_the_same_authority_to_document_processing(monkeypatch):
    authority = object()
    seen = {}

    def fake_build_user_content(*args, **kwargs):
        seen["authority"] = kwargs.get("document_authority")
        return "payload"

    monkeypatch.setattr(chat_handler, "build_user_content", fake_build_user_content)
    handler = ChatHandler(None, None, None, None, None, object())
    session = Session(
        id="session-1",
        name="chat",
        endpoint_url="http://model",
        model="model",
        owner="alice",
    )

    result = asyncio.run(
        handler.preprocess_message(
            "hello",
            [],
            session,
            document_authority=authority,
        )
    )

    assert result[1] == "payload"
    assert seen["authority"] is authority


def test_document_dispatch_preserves_authority_identity(monkeypatch):
    authority = object()
    seen = {}

    async def handler(_content, ctx):
        seen.update(ctx)
        return {"ok": True}

    monkeypatch.setitem(agent_tools.TOOL_HANDLERS, "create_document", handler)
    result = asyncio.run(
        tool_execution._document_tool_dispatch(
            "create_document",
            "body",
            "session-1",
            "alice",
            document_authority=authority,
        )
    )

    assert result == {"ok": True}
    assert seen["document_authority"] is authority
    assert seen["session_id"] == "session-1" and seen["owner"] == "alice"


def test_document_instrumentation_is_bounded_before_result_finalization(monkeypatch):
    original = _sealed_document_result({
        "action": "create",
        "doc_id": "doc-private",
        "title": "Private title",
        "content": "Private body",
        "content_hash": "private-hash",
        "content_length": 12,
        "exit_code": 0,
    })
    observed = {}

    async def fake_execute(*args, **kwargs):
        return "create_document", original

    class FakeInstrumentation:
        def begin(self, *args, **kwargs):
            observed["begin"] = (args, kwargs)
            return object()

        def finish(self, invocation, *, outcome, duration_ms):
            observed["usage"] = outcome

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", fake_execute)
    monkeypatch.setattr(
        tool_execution,
        "_emit_ai_lens_tool_event",
        lambda _emitter, _block, **kwargs: observed.update(
            {"lens": kwargs.get("result"), "description": kwargs.get("status")}
        ),
    )

    description, result = asyncio.run(
        tool_execution.execute_tool_block(
            agent_tools.ToolBlock("create_document", "Private title\nmarkdown\nPrivate body"),
            tool_usage_instrumentation=FakeInstrumentation(),
        )
    )

    assert result is original
    assert description == "create_document"
    assert observed["begin"][0] == ("create_document", None)
    assert observed["lens"] == {
        "code": "document_tool",
        "name": "create_document",
        "metadata_only": True,
        "status": "succeeded",
        "exit_code": 0,
    }
    assert "Private title" not in repr(observed["lens"])
    assert "Private body" not in repr(observed["lens"])
    assert observed["usage"].result_size_bytes < len(repr(original))


def test_document_instrumentation_begin_and_both_lens_events_are_content_free():
    observed = {"begins": [], "events": []}

    class FakeInstrumentation:
        def begin(self, *args, **kwargs):
            observed["begins"].append((args, kwargs))
            return object()

    class FakeEmitter:
        def emit(self, **kwargs):
            observed["events"].append(kwargs)

    block = agent_tools.ToolBlock(
        "update_document",
        '{"doc_id":"private-doc","content":"private body"}',
    )
    invocation = tool_execution._begin_tool_usage(FakeInstrumentation(), block)
    assert invocation is not None
    assert observed["begins"] == [(('update_document', None), {})]

    tool_execution._emit_ai_lens_tool_event(
        FakeEmitter(), block, event_type="tool_call_started", status="started"
    )
    tool_execution._emit_ai_lens_tool_event(
        FakeEmitter(),
        block,
        event_type="tool_call_result",
        status="succeeded",
        result={"code": "document_tool"},
    )
    assert len(observed["events"]) == 2
    for event in observed["events"]:
        assert event["payload"]["argument_present"] is False
        assert event["payload"]["argument_bytes"] == 0
        assert event["payload"]["arguments_included"] is False


def test_private_command_returns_before_document_authority_capture():
    source = Path(chat_routes.__file__).read_text(encoding="utf-8")
    nonstream = source[source.index("async def chat_endpoint"):source.index("async def chat_stream")]
    stream = source[source.index("async def chat_stream"):source.index("return router")]
    for endpoint in (nonstream, stream):
        private_branch = endpoint.index("if private_token is not None:")
        private_return = endpoint.index("return await _private_disclosure_response", private_branch)
        capture = endpoint.index("_capture_chat_document_authority(request)")
        assert private_branch < private_return < capture


def test_authority_keyword_is_threaded_through_each_owned_boundary():
    expected = {
        "routes/chat_routes.py": {"build_chat_context", "stream_agent_loop"},
        "src/chat_handler.py": {"build_user_content"},
        "src/agent_loop.py": {"execute_tool_block"},
        "src/tool_execution.py": {"_execute_tool_block_impl", "_document_tool_dispatch"},
    }
    root = Path(chat_routes.__file__).resolve().parents[1]
    for relative_path, callees in expected.items():
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in callees:
                continue
            if any(
                keyword.arg == "document_authority"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "document_authority"
                for keyword in node.keywords
            ):
                found.add(node.func.id)
        assert found == callees


def test_chat_rebind_and_tool_output_use_atomic_revision_guards():
    root = Path(chat_routes.__file__).resolve().parents[1]
    route_source = (root / "routes/chat_routes.py").read_text(encoding="utf-8")
    loop_source = (root / "src/agent_loop.py").read_text(encoding="utf-8")
    execution_source = (root / "src/tool_execution.py").read_text(encoding="utf-8")

    rebind = route_source[route_source.index("_rebind_revision = ("):route_source.index("[doc-inject] found by ID")]
    for field in (
        "version_count",
        "current_content",
        "is_active",
        "archived",
        "session_id",
        "title",
        "language",
    ):
        assert f"DBDocument.{field}" in rebind
    assert "synchronize_session=False" in rebind
    assert "!= 1" in rebind and "rollback()" in rebind

    finalize = loop_source.index("finalize_document_tool_result(")
    public_doc_event = loop_source.index('"type": "doc_suggestions"')
    public_tool_event = loop_source.index("json.dumps(tool_output_data)")
    assert finalize < public_doc_event < public_tool_event
    assert "_instrumentation_result(" in execution_source
    assert "result=public_result" in execution_source


def test_document_finalization_failure_clears_stale_description_and_route_fails_closed():
    root = Path(chat_routes.__file__).resolve().parents[1]
    loop_source = (root / "src/agent_loop.py").read_text(encoding="utf-8")
    route_source = (root / "routes/chat_routes.py").read_text(encoding="utf-8")
    assert 'desc = f"{block.tool_type}: error"' in loop_source
    assert "_active_document_readback_failed = False" in route_source
    assert "active_doc = None" in route_source[route_source.index("except Exception:"):]
    assert (
        'raise HTTPException(status_code=409, detail="Document access is no longer available")'
        in route_source
    )


@pytest.mark.parametrize("failure_mode", ["fresh-none", "query-error", "revision-mismatch"])
def test_chat_document_readback_failures_use_sqlite_and_skip_provider(
    tmp_path, monkeypatch, failure_mode
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'chat-route-documents.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        db.add(
            Session(
                id="route-session",
                name="chat",
                endpoint_url="http://model",
                model="model",
                owner="alice",
            )
        )
        db.flush()
        db.add(
            Document(
                id="route-doc",
                session_id="route-session",
                title="Route document",
                language="markdown",
                current_content="body",
                version_count=1,
                is_active=True,
                archived=False,
                owner="alice",
                creator_subject_id=None,
            )
        )
        db.commit()

    state = {"first_calls": 0, "closed": False, "provider_called": False}

    class _QueryProxy:
        def __init__(self, query):
            self._query = query

        def filter(self, *args, **kwargs):
            return _QueryProxy(self._query.filter(*args, **kwargs))

        def order_by(self, *args, **kwargs):
            return _QueryProxy(self._query.order_by(*args, **kwargs))

        def populate_existing(self, *args, **kwargs):
            return _QueryProxy(self._query.populate_existing(*args, **kwargs))

        def first(self):
            state["first_calls"] += 1
            if failure_mode == "query-error" and state["first_calls"] == 1:
                raise RuntimeError("synthetic active document query failure")
            if failure_mode == "fresh-none" and state["first_calls"] == 2:
                return None
            if failure_mode == "revision-mismatch" and state["first_calls"] == 2:
                with factory() as rival:
                    row = rival.get(Document, "route-doc")
                    row.version_count = 2
                    rival.commit()
            return self._query.first()

        def update(self, *args, **kwargs):
            return self._query.update(*args, **kwargs)

    class _DatabaseProxy:
        def __init__(self):
            self._db = factory()

        def query(self, *args, **kwargs):
            return _QueryProxy(self._db.query(*args, **kwargs))

        def expire_all(self):
            return self._db.expire_all()

        def commit(self):
            return self._db.commit()

        def rollback(self):
            return self._db.rollback()

        def close(self):
            state["closed"] = True
            return self._db.close()

    session = SimpleNamespace(
        endpoint_url="http://model",
        model="model",
        headers={},
        name="",
        history=[],
    )

    class _SessionManager:
        def get_session(self, session_id):
            assert session_id == "route-session"
            return session

    class _DetachedRuns:
        def start(self, *_args, **_kwargs):
            raise AssertionError("provider stream should not start")

        async def subscribe(self, *_args, **_kwargs):
            if False:
                yield ""

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
            messages=[{"role": "user", "content": "hello"}],
            uprefs={},
            user="alice",
        )

    async def _provider(*_args, **_kwargs):
        state["provider_called"] = True
        raise AssertionError("provider must not run after document readback failure")
        if False:
            yield ""

    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_capture_chat_document_authority", lambda _request: None)
    monkeypatch.setattr(chat_routes, "_chat_effective_user", lambda _request: "alice")
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        chat_routes,
        "coerce_message_and_session",
        lambda _body, message, sid, *_args, **_kwargs: (message, sid),
    )
    monkeypatch.setattr(chat_routes, "build_chat_context", _context)
    monkeypatch.setattr(chat_routes, "build_deterministic_capability_self_report", lambda _message: None)
    monkeypatch.setattr(chat_routes, "SessionLocal", _DatabaseProxy)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda _sid: "chat")
    monkeypatch.setattr(chat_routes, "set_session_mode", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "_clarification_gate_for_session", lambda **_kwargs: {"open": False})
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_routes, "stream_agent_loop", _provider)
    monkeypatch.setattr(chat_routes, "agent_runs", _DetachedRuns())

    app = FastAPI()
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
            "message": "hello",
            "session": "route-session",
            "mode": "agent",
            "active_doc_id": "route-doc",
        },
    )

    assert response.status_code == 409
    assert state["closed"] is True
    assert state["provider_called"] is False
    engine.dispose()


def test_active_document_output_is_revalidated_after_each_provider_await():
    source = Path(chat_routes.__file__).resolve().parents[1].joinpath(
        "src/agent_loop.py"
    ).read_text(encoding="utf-8")
    stream = source[source.index("async for chunk in stream_llm_with_fallback("):]
    validate = stream.index("validate_document_revision(")
    first_forward = min(
        stream.index("yield chunk"),
        stream.index('"type": "doc_stream_open"'),
    )
    assert validate < first_forward
    assert "document_conflict" in stream[:first_forward]
