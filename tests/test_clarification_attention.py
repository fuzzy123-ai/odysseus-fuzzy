import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from routes.workspace_snapshot_routes import setup_workspace_snapshot_routes
from src.clarification_attention import (
    build_session_clarification_attention,
    build_workspace_clarification_status,
)
from src.auth_helpers import SessionPrincipal
from src.clarification_store import ClarificationStore


_OFFLINE_HARNESS_PATH = Path(__file__).with_name("test_app_offline_startup.py")
_OFFLINE_HARNESS_SPEC = importlib.util.spec_from_file_location(
    "clarification_offline_harness", _OFFLINE_HARNESS_PATH,
)
assert _OFFLINE_HARNESS_SPEC is not None and _OFFLINE_HARNESS_SPEC.loader is not None
_OFFLINE_HARNESS = importlib.util.module_from_spec(_OFFLINE_HARNESS_SPEC)
_OFFLINE_HARNESS_SPEC.loader.exec_module(_OFFLINE_HARNESS)
_TRIPWIRE = _OFFLINE_HARNESS._TRIPWIRE

_MIGRATION_HARNESS_PATH = Path(__file__).with_name("test_app_planning_migration.py")
_MIGRATION_HARNESS_SPEC = importlib.util.spec_from_file_location(
    "clarification_migration_harness", _MIGRATION_HARNESS_PATH,
)
assert _MIGRATION_HARNESS_SPEC is not None and _MIGRATION_HARNESS_SPEC.loader is not None
_MIGRATION_HARNESS = importlib.util.module_from_spec(_MIGRATION_HARNESS_SPEC)
_MIGRATION_HARNESS_SPEC.loader.exec_module(_MIGRATION_HARNESS)
_child_environment = _MIGRATION_HARNESS._child_environment


def _principal(owner="alice", sid="owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
    return SessionPrincipal(owner=owner, creator_subject_id=sid, authority="browser_session")


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _request():
    return {
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Build a local document review flow.",
        "questions": [
            {
                "key": "target_documents",
                "type": "short_text",
                "prompt": "Which documents should be reviewed first?",
                "required": True,
                "reason": "The document source changes scope and privacy boundaries.",
                "category": "scope",
            }
        ],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    }


_ACTUAL_APP_CLARIFICATION_CHILD = _TRIPWIRE + r'''
import json
import os
from pathlib import Path

phase = os.environ["ODYSSEUS_CLARIFICATION_PHASE"]
result_file = Path(os.environ["ODYSSEUS_CLARIFICATION_RESULT_FILE"])
checkpoint_file = Path(os.environ["ODYSSEUS_CLARIFICATION_CHECKPOINT_FILE"])
_allowed_stages = {"imports", "auth", "session", "clarification", "restart", "chat_nonstream", "chat_stream", "foreign", "denied", "complete", "failure"}
_keys = {
    "ok", "stage", "offline", "owner_detail", "owner_events", "owner_attention",
    "owner_workspace_pending", "sid_binding", "foreign_detail_denied",
    "foreign_events_denied", "foreign_workspace_empty", "anonymous_denied",
    "forged_denied", "chat_policy", "consumer_queries", "nonstream_context", "stream_context", "nonstream_gate_order", "stream_gate_order", "memory_clear", "research_clear", "stream_policy_blocked", "mutations_disabled", "network_clear_before_nonstream", "network_clear_after_nonstream", "network_clear_after_stream", "nonstream_reply_ok", "plainstream_reply_ok", "agentstream_reply_ok", "nonstream_context_disabled", "plainstream_context_disabled", "agentstream_context_disabled", "nonstream_background_disabled", "plainstream_background_disabled", "agentstream_background_disabled", "nonstream_network_attempted", "plainstream_network_attempted", "agentstream_network_attempted", "loopback_resolution_exercised", "networkorigin_context_probe", "networkorigin_endpoint_resolution", "networkorigin_model_listing", "networkorigin_tokenizer_asset", "networkorigin_context_provider", "networkorigin_webhook_event", "networkorigin_llm_transport", "networkorigin_other", "network_attempted", "process_attempted",
}

def _write(value):
    safe = {key: value.get(key, False) for key in _keys}
    safe["stage"] = value.get("stage") if value.get("stage") in _allowed_stages else "failure"
    if not all(type(safe[key]) is bool for key in _keys - {"stage"}):
        safe = {key: False for key in _keys if key != "stage"} | {"stage": "failure"}
    result_file.write_text(json.dumps(safe, sort_keys=True, separators=(",", ":")), encoding="utf-8")

stage = "imports"
try:
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    import inspect
    import socket
    import app as application
    from core.database import ClarificationRun, ModelEndpoint, SessionLocal
    import routes.chat_routes as chat_routes
    import routes.chat_helpers as chat_helpers
    import src.context_compactor as context_compactor
    import src.endpoint_resolver as endpoint_resolver
    from src.clarification_store import ClarificationStore

    password = "offline-clarification-owner-password-77!"
    request = {
        "schema": "odysseus.clarification_request.v2", "scope": "project",
        "intent_summary": "Offline clarification proof.",
        "questions": [{"key": "target_documents", "type": "short_text", "prompt": "Which documents?", "required": True, "reason": "Scope", "category": "scope"}],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    }
    checks = {key: False for key in _keys if key not in {"ok", "stage"}}
    checks["offline"] = application.STARTUP_OFFLINE is True
    loopback_resolution = [False]
    def _loopback_resolver(hostname):
        if hostname != "127.0.0.1":
            raise RuntimeError("offline_endpoint_resolution_blocked")
        loopback_resolution[0] = True
        return None
    endpoint_resolver._resolve_tailscale_host = _loopback_resolver
    origins = {name: False for name in ("context_probe", "endpoint_resolution", "model_listing", "tokenizer_asset", "context_provider", "webhook_event", "llm_transport", "other")}
    network_hits = [0]
    def _mark_network_origin():
        frame = inspect.currentframe().f_back
        pairs = set()
        for _ in range(64):
            if frame is None:
                break
            pairs.add((str(frame.f_globals.get("__name__", "")), frame.f_code.co_name))
            frame = frame.f_back
        categories = {
            "context_probe": {("src.model_context", "_default_async_context_transport"), ("src.model_context", "_probe_context_length_async"), ("src.model_context", "_run_probe"), ("src.model_context", "get_snapshot"), ("src.model_context", "_query_context_length"), ("src.model_context", "_get_context_length_cached"), ("src.model_context", "get_context_length"), ("src.model_context", "get_context_length_known"), ("src.model_context", "budget_context_for_model"), ("src.model_context", "_discover_context_length")},
            "endpoint_resolution": {("routes.chat_helpers", "resolve_session_auth"), ("routes.chat_helpers", "_session_url_matches_endpoint"), ("src.endpoint_resolver", "build_chat_url"), ("src.endpoint_resolver", "_prepare_endpoint_base"), ("src.endpoint_resolver", "resolve_url"), ("src.endpoint_resolver", "_resolve_tailscale_host")},
            "model_listing": {("src.llm_model_listing", "list_model_ids"), ("src.llm_core", "list_model_ids"), ("src.llm_core", "normalize_model_id")},
            "context_provider": {("src.context_orchestrator", "preload_provider_context")},
            "webhook_event": {("src.webhook_manager", "_resolve_hostname_ips"), ("src.webhook_manager", "_deliver"), ("src.webhook_manager", "_post_public_url"), ("src.event_bus", "_handle_event")},
            "llm_transport": {("src.llm_async_call", "llm_call_async_impl"), ("src.llm_core", "stream_llm"), ("src.llm_core", "llm_call_async")},
        }
        if any(module in {"tiktoken", "tokenizers", "transformers", "huggingface_hub"} for module, _ in pairs):
            origins["tokenizer_asset"] = True
        elif any(pairs & members for members in categories.values()):
            for name, members in categories.items():
                if pairs & members:
                    origins[name] = True
        else:
            origins["other"] = True
    def _observe_network_guard(original):
        def observed(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except RuntimeError as exc:
                if str(exc) == "offline_test_network_blocked":
                    network_hits[0] += 1
                    _mark_network_origin()
                raise
        return observed
    socket.socket.connect = _observe_network_guard(socket.socket.connect)
    socket.socket.connect_ex = _observe_network_guard(socket.socket.connect_ex)
    socket.create_connection = _observe_network_guard(socket.create_connection)
    socket.getaddrinfo = _observe_network_guard(socket.getaddrinfo)
    socket.socket.sendto = _observe_network_guard(socket.socket.sendto)
    if hasattr(socket.socket, "sendmsg"):
        socket.socket.sendmsg = _observe_network_guard(socket.socket.sendmsg)
    gate_calls, context_calls, stream_policy, query_counts, side_effects, order, background, policy_calls = [], [], {}, {"active": 0, "metadata": 0}, {"memory": 0, "research": 0}, [], [], []
    _real_gate = chat_routes._clarification_gate_for_session
    _real_context = chat_routes.build_chat_context
    _real_active = ClarificationStore.read_active_run_for_session
    _real_metadata = ClarificationStore.list_active_metadata
    _real_workspace = chat_routes._resolve_request_workspace
    _real_classify = chat_routes._classify_tool_intent
    _real_clear = chat_routes._clear_orphaned_session_endpoint
    _real_policy = chat_routes.build_effective_tool_policy
    from src import tool_implementations
    _real_clear_email = tool_implementations.clear_active_email
    _real_set_email = tool_implementations.set_active_email
    def _tracked_active(self, *args, **kwargs):
        query_counts["active"] += 1
        return _real_active(self, *args, **kwargs)
    def _tracked_metadata(self, *args, **kwargs):
        query_counts["metadata"] += 1
        return _real_metadata(self, *args, **kwargs)
    def _gate_wrapper(**kwargs):
        order.append("gate")
        gate_calls.append(kwargs.get("session_id"))
        return _real_gate(**kwargs)
    def _workspace_wrapper(*args, **kwargs):
        order.append("workspace")
        return _real_workspace(*args, **kwargs)
    def _classify_wrapper(*args, **kwargs):
        order.append("classify")
        return _real_classify(*args, **kwargs)
    def _clear_wrapper(*args, **kwargs):
        order.append("clear")
        return _real_clear(*args, **kwargs)
    def _clear_email_wrapper(*args, **kwargs):
        order.append("clear_email")
        return _real_clear_email(*args, **kwargs)
    def _set_email_wrapper(*args, **kwargs):
        order.append("set_email")
        return _real_set_email(*args, **kwargs)
    def _policy_wrapper(*args, **kwargs):
        policy = _real_policy(*args, **kwargs)
        policy_calls.append(policy)
        return policy
    async def _offline_llm(*_args, **_kwargs):
        return "offline ordinary reply"
    async def _static_budget(*_args, **_kwargs):
        return 4096
    async def _static_compactor_snapshot(*_args, **_kwargs):
        return SimpleNamespace(context_length=100000)
    def _static_model(*_args, **_kwargs):
        return "offline-model"
    def _bounded_background(*_args, **kwargs):
        background.append(bool(kwargs.get("allow_background_extraction")))
    async def _offline_agent(*_args, **kwargs):
        policy = kwargs.get("tool_policy")
        disabled = set(kwargs.get("disabled_tools") or ())
        stream_policy["mode"] = getattr(policy, "mode", "")
        stream_policy["disable_mcp"] = bool(getattr(policy, "disable_mcp", False))
        stream_policy["blocked"] = bool(getattr(policy, "block_all_tool_calls", False))
        stream_policy["mutation_blocked"] = {"bash", "write_file", "manage_memory", "trigger_research"}.issubset(disabled)
        stream_policy["readonly_allowed"] = not ({"ask_user", "read_file", "grep", "glob", "ls", "search_chats"} & disabled)
        yield 'data: {"delta":"offline stream reply"}\n\n'
        yield "data: [DONE]\n\n"
    async def _offline_chat_stream(*_args, **_kwargs):
        yield 'data: {"delta":"offline chat stream reply"}\n\n'
        yield "data: [DONE]\n\n"
    async def _context_after_gate(*args, **kwargs):
        order.append("context")
        context_calls.append((bool(gate_calls), kwargs.get("allow_tool_preprocessing")))
        if not gate_calls:
            raise RuntimeError("context_before_gate")
        return await _real_context(*args, **kwargs)
    async def _unexpected_research(*_args, **_kwargs):
        side_effects["research"] += 1
        raise RuntimeError("research_reached")
    async def _unexpected_memory(*_args, **_kwargs):
        side_effects["memory"] += 1
        raise RuntimeError("memory_reached")
    chat_routes._clarification_gate_for_session = _gate_wrapper
    chat_routes.llm_call_async = _offline_llm
    chat_routes.run_post_response_tasks = _bounded_background
    chat_routes.stream_agent_loop = _offline_agent
    chat_routes.stream_llm_with_fallback = _offline_chat_stream
    chat_routes.build_deterministic_capability_self_report = lambda _message: None
    chat_routes.build_chat_context = _context_after_gate
    chat_routes._resolve_request_workspace = _workspace_wrapper
    chat_routes._classify_tool_intent = _classify_wrapper
    chat_routes._clear_orphaned_session_endpoint = _clear_wrapper
    chat_routes.build_effective_tool_policy = _policy_wrapper
    application.research_handler.call_research_service = _unexpected_research
    application.chat_handler.handle_memory_command = _unexpected_memory
    tool_implementations.clear_active_email = _clear_email_wrapper
    tool_implementations.set_active_email = _set_email_wrapper
    chat_helpers.resolve_context_budget_tokens_async = _static_budget
    chat_helpers.normalize_model_id = _static_model
    context_compactor.resolve_request_context_snapshot = _static_compactor_snapshot
    ClarificationStore.read_active_run_for_session = _tracked_active
    ClarificationStore.list_active_metadata = _tracked_metadata
    with TestClient(application.app) as owner:
        if phase == "first":
            stage = "auth"
            checks["owner_detail"] = owner.post("/api/auth/setup", json={"username": "owner", "password": password}).status_code == 200
        stage = "auth"
        login = owner.post("/api/auth/login", json={"username": "owner", "password": password})
        if login.status_code != 200:
            raise RuntimeError("login")
        if phase == "first":
            stage = "session"
            db = SessionLocal()
            try:
                db.add(ModelEndpoint(id="offline-endpoint", name="offline", base_url="http://127.0.0.1:9/v1", is_enabled=True, owner="owner", cached_models='["offline-model"]'))
                db.commit()
            finally:
                db.close()
            created_session = owner.post("/api/session", data={"name": "offline clarification", "endpoint_url": "http://127.0.0.1:9/v1/chat/completions", "model": "offline-model", "skip_validation": "true"})
            if created_session.status_code != 200:
                raise RuntimeError("session")
            session_id = created_session.json().get("id")
            stage = "clarification"
            created = owner.post("/api/sessions/{}/clarification".format(session_id), json={"request": request})
            if created.status_code != 200:
                raise RuntimeError("clarification")
            clarification_id = created.json().get("run", {}).get("clarification_id")
            checkpoint_file.write_text(json.dumps({"session_id": session_id, "clarification_id": clarification_id}), encoding="utf-8")
        else:
            stage = "restart"
            saved = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            session_id, clarification_id = saved.get("session_id"), saved.get("clarification_id")
        detail = owner.get("/api/clarifications/{}".format(clarification_id))
        events = owner.get("/api/clarifications/{}/events".format(clarification_id))
        attention = owner.get("/api/sessions/{}/clarification".format(session_id))
        sessions = owner.get("/api/sessions")
        workspace = owner.get("/api/workspace/snapshot")
        section = next((x for x in workspace.json().get("snapshot", {}).get("sections", []) if x.get("id") == "clarification"), {}) if workspace.status_code == 200 else {}
        checks["owner_detail"] = detail.status_code == 200
        checks["owner_events"] = events.status_code == 200
        checks["owner_attention"] = attention.status_code == 200 and attention.json().get("active") is True and sessions.status_code == 200 and any(item.get("id") == session_id and item.get("status_reason") == "clarification_required" for item in sessions.json())
        checks["owner_workspace_pending"] = section.get("pending_count") == 1
        checks["consumer_queries"] = query_counts["active"] > 0 and query_counts["metadata"] > 0
        gate_calls[:] = []
        context_calls[:] = []
        order[:] = []
        stage = "chat_nonstream"
        checks["network_clear_before_nonstream"] = attempted["network"] is False
        nonstream_network_before = network_hits[0]
        background_before_ordinary = len(background)
        ordinary = owner.post("/api/chat", json={"message": "Please research this ordinary request", "session": session_id, "use_research": True})
        ordinary_background = background[background_before_ordinary:]
        checks["network_clear_after_nonstream"] = attempted["network"] is False
        checks["nonstream_network_attempted"] = network_hits[0] != nonstream_network_before
        ordinary_context = list(context_calls)
        ordinary_gate_count = len(gate_calls)
        ordinary_order = list(order)
        gate_calls[:] = []
        context_calls[:] = []
        order[:] = []
        background_before_stream_chat = len(background)
        plainstream_network_before = network_hits[0]
        stream_chat = owner.post("/api/chat_stream", data={"message": "Can you explain this ordinary topic?", "session": session_id, "mode": "chat", "use_research": "true", "active_email_uid": "offline-email"})
        stream_chat_background = background[background_before_stream_chat:]
        checks["plainstream_network_attempted"] = network_hits[0] != plainstream_network_before
        stream_chat_context = list(context_calls)
        stream_chat_order = list(order)
        gate_calls[:] = []
        context_calls[:] = []
        order[:] = []
        stage = "chat_stream"
        background_before_stream_agent = len(background)
        agentstream_network_before = network_hits[0]
        streamed = owner.post("/api/chat_stream", data={"message": "Please research this ordinary stream request", "session": session_id, "mode": "agent", "use_research": "true", "active_email_uid": "offline-email"})
        stream_agent_background = background[background_before_stream_agent:]
        checks["agentstream_network_attempted"] = network_hits[0] != agentstream_network_before
        checks["network_clear_after_stream"] = attempted["network"] is False
        stream_order = list(order)
        def _ordered(items):
            return bool(items) and items[0] == "gate" and "context" in items and all(items.index("gate") < items.index(marker) for marker in set(items) - {"gate"})
        checks["chat_policy"] = ordinary.status_code == 200 and ordinary.json().get("response") == "offline ordinary reply" and stream_chat.status_code == 200 and "offline chat stream reply" in stream_chat.text and streamed.status_code == 200 and "offline stream reply" in streamed.text and ordinary_gate_count >= 1 and len(gate_calls) >= 1 and ordinary_context and stream_chat_context and context_calls and all(after_gate and allow is False for after_gate, allow in ordinary_context + stream_chat_context + context_calls) and _ordered(ordinary_order) and _ordered(stream_chat_order) and _ordered(stream_order) and side_effects["memory"] == 0 and side_effects["research"] == 0 and ordinary_background and stream_chat_background and stream_agent_background and all(flag is False for flag in ordinary_background + stream_chat_background + stream_agent_background) and stream_policy.get("mode") == "clarification_open" and stream_policy.get("disable_mcp") is True and stream_policy.get("blocked") is False and stream_policy.get("mutation_blocked") is True and stream_policy.get("readonly_allowed") is True
        checks["nonstream_context"] = bool(ordinary_context) and all(allow is False for _, allow in ordinary_context)
        checks["stream_context"] = bool(stream_chat_context) and bool(context_calls) and all(allow is False for _, allow in stream_chat_context + context_calls)
        checks["nonstream_gate_order"] = _ordered(ordinary_order)
        checks["stream_gate_order"] = _ordered(stream_chat_order) and _ordered(stream_order)
        checks["memory_clear"] = side_effects["memory"] == 0
        checks["research_clear"] = side_effects["research"] == 0
        checks["stream_policy_blocked"] = stream_policy.get("mode") == "clarification_open" and stream_policy.get("disable_mcp") is True and stream_policy.get("blocked") is False
        checks["mutations_disabled"] = stream_policy.get("mutation_blocked") is True and stream_policy.get("readonly_allowed") is True and ordinary_background and stream_chat_background and stream_agent_background and all(flag is False for flag in ordinary_background + stream_chat_background + stream_agent_background)
        checks["nonstream_reply_ok"] = ordinary.status_code == 200 and ordinary.json().get("response") == "offline ordinary reply"
        checks["plainstream_reply_ok"] = stream_chat.status_code == 200 and "offline chat stream reply" in stream_chat.text
        checks["agentstream_reply_ok"] = streamed.status_code == 200 and "offline stream reply" in streamed.text
        checks["nonstream_context_disabled"] = bool(ordinary_context) and all(allow is False for _, allow in ordinary_context)
        checks["plainstream_context_disabled"] = bool(stream_chat_context) and all(allow is False for _, allow in stream_chat_context)
        checks["agentstream_context_disabled"] = bool(context_calls) and all(allow is False for _, allow in context_calls)
        checks["nonstream_background_disabled"] = bool(ordinary_background) and all(flag is False for flag in ordinary_background)
        checks["plainstream_background_disabled"] = bool(stream_chat_background) and all(flag is False for flag in stream_chat_background)
        checks["agentstream_background_disabled"] = bool(stream_agent_background) and all(flag is False for flag in stream_agent_background)
        token = owner.cookies.get("odysseus_session")
        identity = application.auth_manager.strict_session_identity(token)
        db = SessionLocal()
        try:
            row = db.query(ClarificationRun).filter(ClarificationRun.id == clarification_id).first()
            checks["sid_binding"] = row is not None and identity is not None and row.creator_subject_id == identity[1]
        finally:
            db.close()
        stage = "foreign"
        application.auth_manager.create_user("otheradmin", "offline-clarification-other-password-77!", is_admin=True)
        with TestClient(application.app) as other:
            other_login = other.post("/api/auth/login", json={"username": "otheradmin", "password": "offline-clarification-other-password-77!"})
            checks["foreign_detail_denied"] = other_login.status_code == 200 and other.get("/api/clarifications/{}".format(clarification_id)).status_code == 404
            checks["foreign_events_denied"] = other.get("/api/clarifications/{}/events".format(clarification_id)).status_code == 404
            foreign_workspace = other.get("/api/workspace/snapshot")
            foreign_section = next((x for x in foreign_workspace.json().get("snapshot", {}).get("sections", []) if x.get("id") == "clarification"), {}) if foreign_workspace.status_code == 200 else {}
            checks["foreign_workspace_empty"] = foreign_section.get("pending_count") == 0
        stage = "denied"
        with TestClient(application.app) as denied:
            before_denied = dict(query_counts)
            before_effects = (list(gate_calls), list(context_calls), list(order), dict(side_effects))
            anonymous = [
                denied.get("/api/sessions"),
                denied.get("/api/workspace/snapshot"),
                denied.post("/api/chat", json={"message": "ordinary", "session": session_id}),
                denied.post("/api/chat_stream", data={"message": "ordinary", "session": session_id}),
            ]
            checks["anonymous_denied"] = all(item.status_code in {401, 403} for item in anonymous)
            denied.cookies.set("odysseus_session", "forged")
            forged = [
                denied.get("/api/sessions"),
                denied.get("/api/workspace/snapshot"),
                denied.post("/api/chat", json={"message": "ordinary", "session": session_id}),
                denied.post("/api/chat_stream", data={"message": "ordinary", "session": session_id}),
            ]
            checks["forged_denied"] = all(item.status_code in {401, 403} for item in forged)
            checks["consumer_queries"] = checks["consumer_queries"] and query_counts == before_denied and (list(gate_calls), list(context_calls), list(order), dict(side_effects)) == before_effects
    checks["network_attempted"] = attempted["network"]
    checks["process_attempted"] = attempted["process"]
    checks["loopback_resolution_exercised"] = loopback_resolution[0]
    for name, value in origins.items():
        checks["networkorigin_" + name] = value
    diagnostic_keys = {"network_attempted", "process_attempted", "nonstream_network_attempted", "plainstream_network_attempted", "agentstream_network_attempted"} | {"networkorigin_" + name for name in origins}
    checks["ok"] = all(checks[key] is True for key in checks if key not in diagnostic_keys) and not attempted["network"] and not attempted["process"]
    checks["stage"] = "complete"
    _write(checks)
    raise SystemExit(0 if checks["ok"] else 2)
except Exception as exc:
    diagnostic = {
        "schema": "odysseus.clarification_offline_diagnostic.v1",
        "stage": stage if stage in _allowed_stages else "failure",
        "error_class": type(exc).__name__ if type(exc).__name__ in {
            "AssertionError", "AttributeError", "ImportError", "IndexError", "KeyError",
            "NameError", "OSError", "RuntimeError", "TimeoutError", "TypeError", "ValueError",
        } else "other",
        "child_line": exc.__traceback__.tb_lineno if exc.__traceback__ is not None else 0,
    }
    result_file.with_suffix(".diagnostic.json").write_text(
        json.dumps(diagnostic, sort_keys=True, separators=(",", ":")), encoding="utf-8",
    )
    _write({"ok": False, "stage": stage, "network_attempted": attempted["network"], "process_attempted": attempted["process"]})
    raise SystemExit(1)
'''


def test_clarification_attention_tracks_active_and_ready_states():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request())

    attention = build_session_clarification_attention(principal=_principal(), session_id="sess-1", store=store)
    workspace = build_workspace_clarification_status(principal=_principal(), store=store)

    assert attention["active"] is True
    assert attention["reason"] == "clarification_required"
    assert attention["unresolved_required_count"] == 1
    assert workspace["status"] == "pending"
    assert workspace["pending_count"] == 1
    assert workspace["active_run_count"] == 1

    answered = store.answer_question(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer="reports",
        expected_version=1,
        idempotency_key="idem-answer",
    )
    store.confirm_understanding(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        understanding_summary="Review reports first.",
        expected_version=answered.run["version"],
        idempotency_key="idem-ready",
    )

    ready = build_session_clarification_attention(principal=_principal(), session_id="sess-1", store=store)
    assert ready["active"] is False
    assert ready["status"] == "ready_for_plan"


def test_workspace_snapshot_default_clarification_provider_is_connected(monkeypatch, tmp_path):
    import routes.workspace_snapshot_routes as routes

    monkeypatch.setattr(
        routes,
        "build_workspace_clarification_status",
        lambda principal: {
            "schema": "odysseus.clarification_workspace_status.v1",
            "state": "live",
            "status": "pending",
            "pending_count": 2,
            "summary": "2 required answer(s) pending",
            "raw_content_visible": False,
        },
    )

    app = FastAPI()
    from core.auth import AuthManager
    manager = AuthManager(str(tmp_path / "attention-provider-auth.json"))
    assert manager.create_user("admin", "attention-fixture-password-123", is_admin=True)
    token = manager.create_session("admin", "attention-fixture-password-123")
    assert token
    app.state.auth_manager = manager

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = "admin"
        return await call_next(request)

    app.include_router(setup_workspace_snapshot_routes())
    client = TestClient(app)
    client.cookies.set("odysseus_session", token)
    response = client.get("/api/workspace/snapshot")

    assert response.status_code == 200
    sections = {section["id"]: section for section in response.json()["snapshot"]["sections"]}
    assert sections["clarification"]["state"] == "live"
    assert sections["clarification"]["pending_count"] == 2
    assert sections["clarification"]["frontend_hint"] == "render_attention"


def test_inbox_metadata_is_closed_to_principal_and_excludes_session_identity():
    store = ClarificationStore(session_factory=_session_factory())
    principal = _principal()
    store.create_run(principal=principal, session_id="metadata-session", request=_request())
    items = store.list_inbox_metadata(principal=principal)
    assert items[0]["owner"] == "self"
    assert items[0]["required_action"] == "review_clarification"
    assert "session_id" not in items[0]
    assert "owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in repr(items)


def test_actual_offline_app_clarification_survives_restart_with_sid_scope(tmp_path):
    """Two offline app processes retain only the owner SID's clarification."""
    repo_root = Path(__file__).resolve().parents[1]
    child_script = tmp_path / "clarification-offline-child.py"
    child_script.write_text(_ACTUAL_APP_CLARIFICATION_CHILD, encoding="utf-8", newline="\n")
    checkpoint_file = tmp_path / "clarification-checkpoint.json"
    expected = {
        "ok", "stage", "offline", "owner_detail", "owner_events", "owner_attention",
        "owner_workspace_pending", "sid_binding", "foreign_detail_denied",
        "foreign_events_denied", "foreign_workspace_empty", "anonymous_denied",
        "forged_denied", "chat_policy", "consumer_queries", "nonstream_context", "stream_context", "nonstream_gate_order", "stream_gate_order", "memory_clear", "research_clear", "stream_policy_blocked", "mutations_disabled", "network_clear_before_nonstream", "network_clear_after_nonstream", "network_clear_after_stream", "nonstream_reply_ok", "plainstream_reply_ok", "agentstream_reply_ok", "nonstream_context_disabled", "plainstream_context_disabled", "agentstream_context_disabled", "nonstream_background_disabled", "plainstream_background_disabled", "agentstream_background_disabled", "nonstream_network_attempted", "plainstream_network_attempted", "agentstream_network_attempted", "loopback_resolution_exercised", "networkorigin_context_probe", "networkorigin_endpoint_resolution", "networkorigin_model_listing", "networkorigin_tokenizer_asset", "networkorigin_context_provider", "networkorigin_webhook_event", "networkorigin_llm_transport", "networkorigin_other", "network_attempted", "process_attempted",
    }
    for phase in ("first", "second"):
        result_file = tmp_path / f"clarification-{phase}.json"
        env = _child_environment(tmp_path, repo_root)
        env.update({
            "ODYSSEUS_CLARIFICATION_PHASE": phase,
            "ODYSSEUS_CLARIFICATION_RESULT_FILE": str(result_file),
            "ODYSSEUS_CLARIFICATION_CHECKPOINT_FILE": str(checkpoint_file),
        })
        result = subprocess.run(
            [sys.executable, str(child_script)], cwd=repo_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False,
        )
        assert result_file.is_file()
        projection = json.loads(result_file.read_text(encoding="utf-8"))
        assert set(projection) == expected
        assert projection["stage"] == "complete"
        assert all(type(projection[key]) is bool for key in expected - {"stage"})
        assert result.returncode == 0 and projection["ok"] is True
        assert projection["network_attempted"] is False and projection["process_attempted"] is False
