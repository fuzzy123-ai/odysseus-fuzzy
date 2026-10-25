import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

import core.database as core_db
import routes.chat_routes as chat_routes
import routes.session_routes as session_routes
import src.agent_loop as agent_loop
import src.agent_tools as agent_tools
import src.agent_tools.document_tools as document_tools
import src.database as src_db
import src.settings as settings
import src.tool_index as tool_index
from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion, Session
from src.tool_usage_context import TrustedToolUsageContext


@pytest.mark.parametrize("case", ["provider-revision", "provider-revocation", "tool-revision", "tool-revocation"])
def test_real_cookie_sqlite_document_conflict_stops_sse_before_private_output(
    tmp_path, monkeypatch, case
):
    mutation = case.rsplit("-", 1)[-1]
    tool_first = case.startswith("tool-")
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    assert token
    identity = manager.strict_session_identity(token)
    assert identity is not None
    owner, subject_id = identity

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'chat-sse-documents.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    state = {
        "guard_depth": 0,
        "db_depth": 0,
        "max_db_depth": 0,
        "close_count": 0,
        "provider_round": 0,
        "tool_calls": 0,
        "receipt_returned": False,
        "finalizer_calls": 0,
        "context_authority": None,
        "saved": [],
    }
    initial_revision = (
        "subject-doc",
        1,
        "private-old-body",
        True,
        False,
        "chat-session",
        "private-title",
        "markdown",
    )

    class _TrackedSession(OrmSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._tracked_closed = False
            state["db_depth"] += 1
            state["max_db_depth"] = max(state["max_db_depth"], state["db_depth"])

        def close(self):
            if not self._tracked_closed:
                self._tracked_closed = True
                state["db_depth"] -= 1
                state["close_count"] += 1
            return super().close()

    factory = sessionmaker(
        bind=engine,
        class_=_TrackedSession,
        autocommit=False,
        autoflush=False,
    )
    with factory() as db:
        db.add(
            Session(
                id="chat-session",
                name="chat",
                endpoint_url="http://model",
                model="model",
                owner=owner,
            )
        )
        db.add(
            Document(
                id="subject-doc",
                session_id="chat-session",
                title="private-title",
                language="markdown",
                current_content="private-old-body",
                version_count=1,
                is_active=True,
                archived=False,
                owner=owner,
                creator_subject_id=subject_id,
            )
        )
        db.add(
            DocumentVersion(
                id="subject-doc-v1",
                document_id="subject-doc",
                version_number=1,
                content="private-old-body",
                summary="initial",
                source="user",
            )
        )
        db.commit()

    monkeypatch.setattr(core_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "Document", Document, raising=False)
    monkeypatch.setattr(src_db, "DocumentVersion", DocumentVersion, raising=False)
    monkeypatch.setattr(chat_routes, "SessionLocal", factory)
    monkeypatch.setattr(session_routes, "SessionLocal", factory)

    original_chat_guard = chat_routes.guard_document_request_authority
    original_tool_guard = document_tools.guard_document_request_authority

    @contextmanager
    def tracked_chat_guard(authority):
        state["guard_depth"] += 1
        try:
            with original_chat_guard(authority) as lease:
                yield lease
        finally:
            state["guard_depth"] -= 1

    @contextmanager
    def tracked_tool_guard(authority):
        state["guard_depth"] += 1
        try:
            with original_tool_guard(authority) as lease:
                yield lease
        finally:
            state["guard_depth"] -= 1

    monkeypatch.setattr(chat_routes, "guard_document_request_authority", tracked_chat_guard)
    monkeypatch.setattr(document_tools, "guard_document_request_authority", tracked_tool_guard)

    session = SimpleNamespace(
        endpoint_url="http://model",
        model="model",
        headers={},
        name="",
        owner=owner,
        history=[],
    )

    class _SessionManager:
        def get_session(self, session_id):
            assert session_id == "chat-session"
            return session

    class _DetachedRuns:
        def __init__(self):
            self.streams = {}

        def start(self, session_id, stream):
            self.streams[session_id] = stream

        async def subscribe(self, session_id):
            async for chunk in self.streams.pop(session_id):
                yield chunk

    async def _context(*_args, **_kwargs):
        state["context_authority"] = _kwargs.get("document_authority")
        return SimpleNamespace(
            preprocessed=SimpleNamespace(attachment_meta=[]),
            preset=SimpleNamespace(character_name="", temperature=0, max_tokens=256),
            auto_opened_docs=[],
            rag_sources=[],
            web_sources=[],
            used_memories=[],
            was_compacted=False,
            context_length=4096,
            messages=[{"role": "user", "content": "update the document"}],
            uprefs={},
            user=owner,
        )

    async def _provider(_candidates, _messages, **_kwargs):
        state["provider_round"] += 1
        if state["provider_round"] == 1:
            if not tool_first:
                await asyncio.sleep(0)
                assert state["guard_depth"] == 0
                assert state["db_depth"] == 0
                with factory() as db:
                    row = db.get(Document, "subject-doc")
                    if mutation == "revision":
                        row.current_content = "private-raced-body"
                        row.version_count += 1
                        db.commit()
                if mutation == "revocation":
                    manager.revoke_token(token)
                yield 'data: {"delta":"private-provider-body"}\n\n'
                yield "data: [DONE]\n\n"
                return
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "tool_calls",
                        "calls": [
                            {
                                "name": "update_document",
                                "arguments": json.dumps({"content": "private-new-body"}),
                            }
                        ],
                    }
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"
            return
        raise AssertionError("document conflict must stop before a second provider round")

    original_update_handler = agent_tools.TOOL_HANDLERS["update_document"]

    async def _wrapped_update_document(content, ctx):
        state["tool_calls"] += 1
        assert ctx["document_authority"] is not None
        assert ctx["document_authority"] is state["context_authority"]
        assert ctx["owner"] == owner
        assert ctx["document_revision_token"] == initial_revision
        result = await original_update_handler(content, ctx)
        await asyncio.sleep(0)
        assert state["guard_depth"] == 0
        assert state["db_depth"] == 0
        assert type(result) is document_tools._DocumentToolResult
        assert type(result._document_revision_receipt) is document_tools._DocumentRevisionReceipt
        state["receipt_returned"] = True
        with factory() as db:
            row = db.get(Document, "subject-doc")
            if mutation == "revision":
                row.current_content = "private-raced-body"
                row.version_count += 1
                db.commit()
        if mutation == "revocation":
            manager.revoke_token(token)
        return result

    monkeypatch.setitem(agent_tools.TOOL_HANDLERS, "update_document", _wrapped_update_document)
    original_finalizer = agent_loop.finalize_document_tool_result

    def _tracked_finalizer(*args, **kwargs):
        state["finalizer_calls"] += 1
        return original_finalizer(*args, **kwargs)

    monkeypatch.setattr(agent_loop, "finalize_document_tool_result", _tracked_finalizer)

    monkeypatch.setattr(chat_routes, "_set_user_time_from_request", lambda _request: None)
    monkeypatch.setattr(chat_routes, "build_chat_context", _context)
    monkeypatch.setattr(chat_routes, "build_trusted_chat_tool_usage_context", lambda **_kwargs: TrustedToolUsageContext.create(surface="chat", agent_mode=True))
    monkeypatch.setattr(chat_routes, "_clarification_gate_for_session", lambda **_kwargs: {"open": False})
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *args, **_kwargs: state["saved"].append(args))
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings, "get_setting", lambda _name, default=None: default)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", _provider)
    monkeypatch.setattr(agent_loop, "_resolve_agent_context_snapshot", lambda *_args, **_kwargs: asyncio.sleep(0, result=SimpleNamespace(context_length=4096, known=False)))
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(tool_index, "get_tool_index", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "_load_mcp_disabled_map", lambda: {})
    monkeypatch.setattr(agent_loop, "_classify_agent_request", lambda *_args: {"domains": set(), "low_signal": False, "retrieval_query": "", "continuation": False})
    monkeypatch.setattr(agent_loop, "_detect_admin_intent", lambda _messages: False)
    monkeypatch.setattr(agent_loop, "_build_system_prompt", lambda messages, *_args, **_kwargs: (list(messages), []))
    monkeypatch.setattr(agent_loop, "_inject_context_provider_messages", lambda messages, **_kwargs: messages)
    monkeypatch.setattr(agent_loop, "get_function_tool_schemas", lambda: [{"type": "function", "function": {"name": "update_document"}}])
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(agent_loop, "get_setting", lambda name, default=None: 0 if name == "agent_input_token_budget" else default)
    detached_runs = _DetachedRuns()
    monkeypatch.setattr(chat_routes, "agent_runs", detached_runs)

    app = FastAPI()
    app.state.auth_manager = manager

    @app.middleware("http")
    async def _strict_cookie_identity(request, call_next):
        identity = manager.strict_session_identity(request.cookies.get("odysseus_session"))
        if identity is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        request.state.current_user = identity[0]
        request.state.api_token = False
        return await call_next(request)

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

    with TestClient(app) as client:
        response = client.post(
            "/api/chat_stream",
            data={
                "message": "update the document",
                "session": "chat-session",
                "mode": "agent",
                "active_doc_id": "subject-doc",
            },
            cookies={"odysseus_session": token},
        )
        unauthenticated = client.post(
            "/api/chat_stream",
            data={"message": "update the document", "session": "chat-session", "mode": "agent"},
        )

    assert response.status_code == 200
    assert unauthenticated.status_code == 401
    events = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload != "[DONE]":
            events.append(json.loads(payload))

    conflicts = [event for event in events if event.get("type") == "document_conflict"]
    assert len(conflicts) == 1
    assert events[-1].get("type") == "document_conflict"
    assert "data: [DONE]" not in response.text
    assert state["saved"] == []
    assert state["provider_round"] == 1
    assert state["tool_calls"] == (1 if tool_first else 0)
    assert state["receipt_returned"] is tool_first
    assert state["finalizer_calls"] == (1 if tool_first else 0)
    assert state["context_authority"] is not None
    assert state["guard_depth"] == 0
    assert state["db_depth"] == 0
    assert state["max_db_depth"] >= 1
    assert state["close_count"] > 0

    private_fields = (
        "subject-doc",
        "private-title",
        "private-old-body",
        "private-raced-body",
        "_document_revision_receipt",
        "document_revision_receipt",
    )
    if tool_first:
        tool_start_indexes = [
            index for index, event in enumerate(events) if event.get("type") == "tool_start"
        ]
        assert len(tool_start_indexes) == 1
        assert events[tool_start_indexes[0]].get("tool") == "update_document"
        post_boundary = events[tool_start_indexes[0] + 1 :]
        assert all(
            event.get("type") in {"tool_output", "agent_step", "document_conflict"}
            for event in post_boundary
        )
        for event in post_boundary:
            serialized = json.dumps(event)
            assert all(field not in serialized for field in private_fields)
            assert event.get("type") != "doc_update"
        tool_outputs = [event for event in post_boundary if event.get("type") == "tool_output"]
        assert len(tool_outputs) == 1
        tool_output = tool_outputs[0]
        for field in ("doc_id", "title", "content", "version", "action", "receipt"):
            assert field not in tool_output
        assert tool_output["output"] == "Document changed or access is no longer available"
        assert tool_output["exit_code"] == 1
        assert all(
            field not in tool_output["output"]
            for field in ("doc_id", "title", "content", "version", "action", "receipt")
        )
        assert any("private-new-body" in json.dumps(event) for event in events[: tool_start_indexes[0]])
    else:
        assert not any(event.get("type") == "tool_start" for event in events)
        serialized = json.dumps(events)
        assert all(field not in serialized for field in private_fields)
        assert "private-provider-body" not in serialized
    engine.dispose()
