from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import weakref

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import src.mcp_context_app as context_app_module
from core.database import ChatMessage, Session as DbSession
from fastapi.responses import Response
from src.context_source_app import ContextSourceServices
from src.mcp_authenticated_dispatch import MCP_CONTEXT_SCOPE, McpAuthenticatedIdentity
from src.mcp_context_app import McpContextApp, install_mcp_context_app
from src.mcp_context_authority import McpContextAuthorityError, issue_mcp_bearer_proof
from src.mcp_private_context_confirmation import McpPrivateContextConfirmationError
from src.private_context_confirmation_app import PrivateContextConfirmationApp
from tests.test_mcp_context_metadata import native_mcp_fixture


class _ConfigOwner:
    def __init__(self, enabled: bool = False, allowed: bool = False) -> None:
        self._lock = threading.RLock()
        self.enabled = enabled
        self.allowed = allowed
        self.version = 0
        self.app = None
        self.data_dir = object()
        self.config_path = object()
        self.read_descriptor = object()
        self.path_descriptor = object()
        self.binding = None

    def bind_app(self, app) -> None:
        self.app = app
        self.binding = (
            self.app, self.data_dir, self.config_path,
            self.read_descriptor, self.path_descriptor,
        )

    def context_owner_pins(self):
        if (
            type(self.binding) is not tuple
            or self.app is not self.binding[0]
            or self.data_dir is not self.binding[1]
            or self.config_path is not self.binding[2]
            or self.read_descriptor is not self.binding[3]
            or self.path_descriptor is not self.binding[4]
        ):
            raise ValueError("invalid MCP context configuration owner")
        return self.binding

    def context_snapshot(self) -> tuple[bool, bool, str]:
        with self._lock:
            return self.enabled is True, self.allowed is True, f"version-{self.version}"

    def set(self, enabled: bool, allowed: bool) -> None:
        with self._lock:
            self.enabled = enabled
            self.allowed = allowed
            self.version += 1


def _bare_app(auth, session_factory) -> tuple[FastAPI, McpContextApp]:
    app = FastAPI()
    app.state.auth_manager = auth
    app.state._token_cache_generation = 1
    app.state._token_cache_dirty = False
    composition = install_mcp_context_app(
        app,
        auth_manager=auth,
        session_factory=session_factory,
        context_sources=None,
    )
    return app, composition


def _active_app(native_mcp_fixture) -> tuple[FastAPI, McpContextApp, ContextSourceServices]:
    fixture = native_mcp_fixture
    app = FastAPI()
    app.state.auth_manager = fixture["auth"]
    app.state._token_cache_generation = fixture["mcp_state"]._token_cache_generation
    app.state._token_cache_dirty = False
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"], fixture["auth"], fixture["combined"],
    )
    sources = ContextSourceServices(
        fixture["combined"], confirmation, fixture["forge"],
        fixture["planning_producer"], fixture["knowledge_producer"],
        object(), object(), object(),
    )
    app.state.context_sources = sources
    app.state.private_context_confirmation = confirmation
    composition = install_mcp_context_app(
        app, auth_manager=fixture["auth"], session_factory=fixture["session_factory"],
        context_sources=sources,
    )
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.start()
    assert composition._graph is not None
    return app, composition, sources


def _stream_request(app: FastAPI, path: str, frames, *, content_length: bytes | None = None):
    pending = list(frames)

    async def receive():
        if pending:
            frame = pending.pop(0)
            if callable(frame):
                return await frame()
            return frame
        return {"type": "http.request", "body": b"", "more_body": False}

    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    return Request({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode("ascii"), "query_string": b"",
        "root_path": "", "headers": headers, "client": ("testclient", 50000),
        "server": ("testserver", 80), "app": app,
    }, receive)


def _authenticated_mcp_request(app: FastAPI, raw_token: str):
    pending = [{
        "type": "http.request",
        "body": b'{"jsonrpc":"2.0","id":"r3","method":"tools/list","params":{}}',
        "more_body": False,
    }]

    async def receive():
        return pending.pop(0) if pending else {
            "type": "http.request", "body": b"", "more_body": False,
        }

    return Request({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/plugins/mcp", "raw_path": b"/api/plugins/mcp",
        "query_string": b"", "root_path": "",
        "headers": [
            (b"authorization", ("Bearer " + raw_token).encode("ascii")),
            (b"content-type", b"application/json"),
        ],
        "client": ("testclient", 50000), "server": ("testserver", 80), "app": app,
    }, receive)


def _route_endpoint(app: FastAPI, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def _graph_lease(native_mcp_fixture, graph, request_id: str):
    fixture = native_mcp_fixture
    auth = fixture["auth"]
    identity = McpAuthenticatedIdentity(
        "mcp-metadata-token", "fuzzy", auth.subject_id_for_username("fuzzy"),
        fixture["mcp_state"]._token_cache_generation,
        auth.security_action_revision(), MCP_CONTEXT_SCOPE,
    )
    binding = graph.authority.issue_binding(
        identity, issue_mcp_bearer_proof(fixture["mcp_raw_tokens"][0]), fixture["chat_id"],
    )
    return graph.authority.issue_read_lease(binding, request_id)


def _attached_entry(native_mcp_fixture, graph, request_id: str):
    lease = _graph_lease(native_mcp_fixture, graph, request_id)
    prepared = graph.service.prepare_intent(
        lease, native_mcp_fixture["selection"], page_bytes=1024,
    )
    challenge = graph.service._attach_authenticated_challenge(
        prepared.request_id, native_mcp_fixture["cookie"],
    )
    return lease, graph.service._entries[prepared.request_id], challenge


def _replace_graph_factories(graph, factory) -> None:
    graph.bridge._session_factory = factory
    graph.service._session_factory = factory
    graph.service._session_factory_pin = factory
    graph.authority._session_factory = factory
    graph.authority._session_factory_pin = factory


def test_install_is_default_closed_routes_once_and_rejects_duplicate(native_mcp_fixture) -> None:
    fixture = native_mcp_fixture
    app, composition = _bare_app(fixture["auth"], fixture["session_factory"])
    assert type(composition) is McpContextApp
    assert app.state.mcp_context_plugin_enabled is False
    assert app.state.allow_context_reads is False
    assert re.fullmatch(r"epoch_[0-9a-f]{32}", app.state._process_epoch)
    paths = [route.path for route in app.routes]
    assert paths.count("/api/context/mcp/challenge") == 1
    assert paths.count("/api/context/mcp/revoke") == 1
    with pytest.raises(ValueError, match="already installed"):
        install_mcp_context_app(
            app,
            auth_manager=fixture["auth"],
            session_factory=fixture["session_factory"],
            context_sources=None,
        )


def test_incomplete_dependencies_never_publish_and_browser_failures_are_fixed(native_mcp_fixture) -> None:
    fixture = native_mcp_fixture
    app, composition = _bare_app(fixture["auth"], fixture["session_factory"])
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.start()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert app.state.allow_context_reads is False
    with TestClient(app) as client:
        response = client.post(
            "/api/context/mcp/challenge",
            content=b'{"request_id":"one","request_id":"two"}',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 403
    assert response.json() == {"error": "context_unavailable"}
    composition.stop()


def test_real_graph_borrows_sources_and_gate_aba_replaces_every_owned_node(native_mcp_fixture) -> None:
    fixture = native_mcp_fixture
    app = FastAPI()
    app.state.auth_manager = fixture["auth"]
    app.state._token_cache_generation = fixture["mcp_state"]._token_cache_generation
    app.state._token_cache_dirty = False
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"],
        fixture["auth"],
        fixture["combined"],
    )
    sources = ContextSourceServices(
        fixture["combined"],
        confirmation,
        fixture["forge"],
        fixture["planning_producer"],
        fixture["knowledge_producer"],
        object(),
        object(),
        object(),
    )
    app.state.context_sources = sources
    app.state.private_context_confirmation = confirmation
    composition = install_mcp_context_app(
        app,
        auth_manager=fixture["auth"],
        session_factory=fixture["session_factory"],
        context_sources=sources,
    )
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.start()
    first = composition._graph
    assert first is not None
    assert first.producer._forge is sources.forge
    assert first.producer._planning is sources.planning
    assert first.producer._knowledge is sources.knowledge
    assert app.state.mcp_context_plugin_enabled is True
    assert app.state.allow_context_reads is True
    first_epoch = app.state._process_epoch

    owner.set(False, True)
    composition.synchronize()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert app.state.allow_context_reads is False
    assert app.state._process_epoch != first_epoch
    assert first.runtime._closed is True
    assert first.coordinator._closed is True
    assert first.cursor._closed is True
    assert first.service._closed is True
    assert first.authority._closed is True

    disabled_epoch = app.state._process_epoch
    owner.set(True, True)
    composition.synchronize()
    second = composition._graph
    assert second is not None and second is not first
    assert second.authority is not first.authority
    assert app.state._process_epoch != disabled_epoch
    assert app.state.mcp_context_plugin_enabled is True
    assert app.state.allow_context_reads is True
    composition.stop()
    assert composition._graph is None
    assert second.runtime._closed is True
    assert second.service._closed is True


def test_configuration_owner_is_bound_by_exact_identity(native_mcp_fixture) -> None:
    fixture = native_mcp_fixture
    app, composition = _bare_app(fixture["auth"], fixture["session_factory"])
    owner = _ConfigOwner()
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.bind_plugin_context(app, owner)
    with pytest.raises(ValueError, match="already bound"):
        replacement = _ConfigOwner()
        replacement.bind_app(app)
        composition.bind_plugin_context(app, replacement)
    with pytest.raises(ValueError, match="configuration owner"):
        composition.bind_plugin_context(FastAPI(), owner)


def test_concurrent_same_configuration_owner_binding_is_idempotent(
    native_mcp_fixture, monkeypatch,
) -> None:
    fixture = native_mcp_fixture
    app, composition = _bare_app(fixture["auth"], fixture["session_factory"])
    owner = _ConfigOwner()
    owner.bind_app(app)
    original_pins = _ConfigOwner.context_owner_pins
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    descriptor_call_count = 0
    first_descriptor_threads = []
    successes = []
    failures = []

    def synchronized_pins(self):
        nonlocal descriptor_call_count
        with counter_lock:
            descriptor_call_count += 1
            call_number = descriptor_call_count
            if call_number <= 2:
                first_descriptor_threads.append(threading.get_ident())
        if call_number <= 2:
            barrier.wait(timeout=5)
        return original_pins(self)

    def bind():
        try:
            composition.bind_plugin_context(app, owner)
        except Exception as error:
            with counter_lock:
                failures.append(type(error).__name__)
        else:
            with counter_lock:
                successes.append(threading.get_ident())

    monkeypatch.setattr(_ConfigOwner, "context_owner_pins", synchronized_pins)
    threads = [threading.Thread(target=bind) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert len(successes) == 2 and len(set(successes)) == 2
    assert descriptor_call_count >= 2
    assert len(first_descriptor_threads) == 2
    assert first_descriptor_threads[0] != first_descriptor_threads[1]
    config = context_app_module._read_mcp_context_config(composition)
    assert config is not None
    assert config.owner is owner
    assert config.binding is owner.binding
    assert composition._config_owner is owner
    assert composition._config_binding is owner.binding


def test_config_descriptor_substitution_detaches_before_rejecting_reenable(
    native_mcp_fixture, monkeypatch,
) -> None:
    fixture = native_mcp_fixture
    app, composition = _bare_app(fixture["auth"], fixture["session_factory"])
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.start()
    assert composition._graph is None
    original_epoch = app.state._process_epoch
    original_read_descriptor = owner.read_descriptor
    owner.read_descriptor = object()
    assert composition._configuration_snapshot() == (False, False, "unavailable")
    composition.synchronize()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert app.state.allow_context_reads is False
    assert app.state._process_epoch != original_epoch
    owner.read_descriptor = original_read_descriptor
    original_epoch = app.state._process_epoch
    monkeypatch.setattr(_ConfigOwner, "context_snapshot", lambda _self: (True, True, "forged"))
    composition.synchronize()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert app.state.allow_context_reads is False
    assert app.state._process_epoch != original_epoch


def test_callback_global_substitution_is_denied_and_captured_close_survives_method_patch(
    native_mcp_fixture, monkeypatch,
) -> None:
    fixture = native_mcp_fixture
    app = FastAPI()
    app.state.auth_manager = fixture["auth"]
    app.state._token_cache_generation = fixture["mcp_state"]._token_cache_generation
    app.state._token_cache_dirty = False
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"], fixture["auth"], fixture["combined"],
    )
    sources = ContextSourceServices(
        fixture["combined"], confirmation, fixture["forge"],
        fixture["planning_producer"], fixture["knowledge_producer"],
        object(), object(), object(),
    )
    app.state.context_sources = sources
    app.state.private_context_confirmation = confirmation
    composition = install_mcp_context_app(
        app,
        auth_manager=fixture["auth"],
        session_factory=fixture["session_factory"],
        context_sources=sources,
    )
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.start()
    graph = composition._graph
    assert graph is not None
    monkeypatch.setattr(context_app_module, "authenticate_mcp_request", lambda _request: None)
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition.synchronize()
    monkeypatch.setattr(type(graph.runtime), "close", lambda _self: None)
    composition.stop()
    assert composition._graph is None
    assert graph.runtime._closed is True
    assert graph.service._closed is True
    assert graph.authority._closed is True


@pytest.mark.parametrize(
    "headers",
    (
        ((b"authorization", b"Bearer ody_firsttoken"),
         (b"authorization", b"Bearer ody_secondtoken")),
        ((b"authorization", b"Bearer ody_validshape"),
         (b"cookie", b"odysseus_session=browser")),
        ((b"authorization", b"Basic not-bearer"),),
    ),
    ids=("duplicate-authorization", "cookie-bearer-mix", "malformed-authorization"),
)
def test_credentials_are_rejected_before_body_or_native_graph(
    native_mcp_fixture, headers,
) -> None:
    fixture = native_mcp_fixture
    _app, composition = _bare_app(fixture["auth"], fixture["session_factory"])
    receives = []

    async def receive():
        receives.append(True)
        return {"type": "http.request", "body": b"must-not-be-read", "more_body": False}

    request = Request({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/plugins/mcp", "raw_path": b"/api/plugins/mcp",
        "query_string": b"", "root_path": "", "headers": list(headers),
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }, receive)
    response = asyncio.run(composition.handle_mcp_request(request))
    assert response.status_code == 401
    assert response.body == b""
    assert receives == []


def test_source_and_bridge_descriptor_drift_fail_closed_before_disclosure(
    native_mcp_fixture, monkeypatch,
) -> None:
    fixture = native_mcp_fixture
    app = FastAPI()
    app.state.auth_manager = fixture["auth"]
    app.state._token_cache_generation = fixture["mcp_state"]._token_cache_generation
    app.state._token_cache_dirty = False
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"], fixture["auth"], fixture["combined"],
    )
    sources = ContextSourceServices(
        fixture["combined"], confirmation, fixture["forge"],
        fixture["planning_producer"], fixture["knowledge_producer"],
        object(), object(), object(),
    )
    app.state.context_sources = sources
    app.state.private_context_confirmation = confirmation
    composition = install_mcp_context_app(
        app, auth_manager=fixture["auth"], session_factory=fixture["session_factory"],
        context_sources=sources,
    )
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    composition.start()
    graph = composition._graph
    assert graph is not None
    app.state.context_sources = object()
    with pytest.raises(ValueError, match="invalid MCP context graph"):
        composition._active_bridge()
    app.state.context_sources = sources
    monkeypatch.setattr(
        type(graph.bridge), "hold_authenticated_human_exchange", lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ValueError, match="invalid MCP context graph"):
        composition._active_bridge()
    composition.stop()
    assert graph.service._closed is True


def test_constructor_owner_capsules_are_write_once_and_preserve_native_instance_shape(
    native_mcp_fixture,
) -> None:
    _app, composition, _sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    assert type(composition.__dict__) is dict
    assert type(graph.bridge.__dict__) is dict
    assert weakref.ref(composition)() is composition
    assert weakref.ref(graph.bridge)() is graph.bridge
    assert "_mcp_context_owner_capsule" not in composition.__dict__
    assert "_bridge_owner_capsule" not in graph.bridge.__dict__
    with pytest.raises(AttributeError, match="write-once"):
        composition._mcp_context_owner_capsule = object()
    with pytest.raises(AttributeError, match="write-once"):
        graph.bridge._bridge_owner_capsule = object()
    composition.stop()


def test_coordinated_factory_replacement_cannot_replace_constructor_authority(
    native_mcp_fixture, monkeypatch,
) -> None:
    _app, composition, sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    fake_calls = []

    def fake_factory():
        fake_calls.append(True)
        raise AssertionError("replacement factory executed")

    for target, name in (
        (composition, "_session_factory"),
        (sources.confirmation._repository, "_session_factory"),
        (graph.authority, "_session_factory"),
        (graph.authority, "_session_factory_pin"),
        (graph.service, "_session_factory"),
        (graph.service, "_session_factory_pin"),
        (graph.bridge, "_session_factory"),
    ):
        monkeypatch.setattr(target, name, fake_factory)
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition._active_bridge()
    assert fake_calls == []
    composition.stop()


def test_coordinated_source_provider_replacement_is_denied_before_new_provider_use(
    native_mcp_fixture,
) -> None:
    _app, composition, sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    replacement = object()
    object.__setattr__(sources, "planning", replacement)
    graph.producer._planning = replacement
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition._active_bridge()
    composition.stop()


def test_prebuild_constructor_and_postbuild_auth_bridge_drift_never_reaches_replacements(
    native_mcp_fixture, monkeypatch,
) -> None:
    fixture = native_mcp_fixture
    app = FastAPI()
    app.state.auth_manager = fixture["auth"]
    app.state._token_cache_generation = fixture["mcp_state"]._token_cache_generation
    app.state._token_cache_dirty = False
    confirmation = PrivateContextConfirmationApp(
        fixture["session_factory"], fixture["auth"], fixture["combined"],
    )
    sources = ContextSourceServices(
        fixture["combined"], confirmation, fixture["forge"],
        fixture["planning_producer"], fixture["knowledge_producer"],
        object(), object(), object(),
    )
    app.state.context_sources = sources
    app.state.private_context_confirmation = confirmation
    composition = install_mcp_context_app(
        app, auth_manager=fixture["auth"], session_factory=fixture["session_factory"],
        context_sources=sources,
    )
    owner = _ConfigOwner(True, True)
    owner.bind_app(app)
    composition.bind_plugin_context(app, owner)
    constructor_calls = []

    def replacement_constructor(*_args, **_kwargs):
        constructor_calls.append(True)
        raise AssertionError("replacement constructor executed")

    monkeypatch.setattr(context_app_module, "McpContextAuthority", replacement_constructor)
    composition._owned_types["authority"] = replacement_constructor
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition.start()
    assert constructor_calls == []

    monkeypatch.undo()
    composition._owned_types["authority"] = context_app_module.McpContextAuthority
    composition.start()
    graph = composition._graph
    assert graph is not None
    replacement_auth = object()
    composition._auth = replacement_auth
    app.state.auth_manager = replacement_auth
    graph.service._auth = replacement_auth
    graph.bridge._auth = replacement_auth
    graph.authority._auth_manager = replacement_auth
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition._active_bridge()
    composition.stop()


@pytest.mark.parametrize("replaced", ("auth", "proof", "both"))
def test_gate_aba_rebuild_rejects_coordinated_auth_callback_pairs_then_recovers(
    native_mcp_fixture, monkeypatch, replaced,
) -> None:
    app, composition, _sources = _active_app(native_mcp_fixture)
    config = composition._config_owner
    config.set(False, True)
    composition.synchronize()
    assert composition._graph is None
    fake_calls = []

    def fake_auth(_request):
        fake_calls.append("auth")
        raise AssertionError("replacement authentication executed")

    def fake_proof(_raw):
        fake_calls.append("proof")
        raise AssertionError("replacement proof issuer executed")

    if replaced in {"auth", "both"}:
        monkeypatch.setattr(context_app_module, "authenticate_mcp_request", fake_auth)
        monkeypatch.setattr(composition, "_authenticate_dispatch", fake_auth)
    if replaced in {"proof", "both"}:
        monkeypatch.setattr(context_app_module, "issue_mcp_bearer_proof", fake_proof)
        monkeypatch.setattr(composition, "_issue_bearer_proof", fake_proof)
    config.set(True, True)
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition.synchronize()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert fake_calls == []
    monkeypatch.undo()
    composition.synchronize()
    assert composition._graph is not None
    assert app.state.mcp_context_plugin_enabled is True
    composition.stop()


@pytest.mark.parametrize(
    ("global_name", "field_name"),
    (
        ("McpAuthenticatedIdentity", "_identity_type"),
        ("ContextSourceServices", "_sources_type"),
        ("_McpContextGraph", "_graph_type"),
        ("MCP_CONTEXT_SCOPE", "_context_scope"),
    ),
    ids=("identity", "sources", "graph", "scope"),
)
def test_gate_rebuild_rejects_paired_identity_source_graph_and_scope_replacements(
    native_mcp_fixture, monkeypatch, global_name, field_name,
) -> None:
    app, composition, _sources = _active_app(native_mcp_fixture)
    config = composition._config_owner
    config.set(False, True)
    composition.synchronize()
    constructor_calls = []

    class Replacement:
        def __init__(self, *_args, **_kwargs):
            constructor_calls.append(True)

    replacement = "mcp:replacement" if global_name == "MCP_CONTEXT_SCOPE" else Replacement
    monkeypatch.setattr(context_app_module, global_name, replacement)
    monkeypatch.setattr(composition, field_name, replacement)
    config.set(True, True)
    with pytest.raises(ValueError, match="invalid MCP context composition"):
        composition.synchronize()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert constructor_calls == []
    monkeypatch.undo()
    composition.synchronize()
    assert composition._graph is not None
    composition.stop()


@pytest.mark.parametrize("replacement", ("raw", "fixed", "fixed-field", "disclose"))
def test_security_descriptor_replacements_use_trusted_rejection_and_never_execute_fake(
    native_mcp_fixture, monkeypatch, replacement,
) -> None:
    app, composition, _sources = _active_app(native_mcp_fixture)
    fake_calls = []

    def fake(*_args, **_kwargs):
        fake_calls.append(True)
        raise AssertionError("replacement descriptor executed")

    if replacement == "raw":
        descriptor = staticmethod(fake)
        monkeypatch.setattr(type(composition), "_raw_bearer", descriptor)
        monkeypatch.setattr(composition, "_raw_bearer_descriptor", descriptor)
        response = asyncio.run(composition.handle_mcp_request(
            _authenticated_mcp_request(app, native_mcp_fixture["mcp_raw_tokens"][0]),
        ))
        assert response.status_code == 401
    elif replacement in {"fixed", "fixed-field"}:
        descriptor = staticmethod(fake)
        if replacement == "fixed":
            monkeypatch.setattr(type(composition), "_fixed_transport_response", descriptor)
        monkeypatch.setattr(composition, "_fixed_transport_descriptor", descriptor)
        response = asyncio.run(composition.handle_mcp_request(
            _authenticated_mcp_request(app, native_mcp_fixture["mcp_raw_tokens"][0]),
        ))
        assert response.status_code == 401
        assert type(response) is Response
    else:
        capture = type(composition).__dict__["capture_browser_disclosure_entry"]
        monkeypatch.setattr(type(composition), "disclose_browser_challenge", fake)
        monkeypatch.setattr(composition, "_disclose_browser_descriptor", fake)
        with pytest.raises(Exception):
            capture(composition, app)
    assert fake_calls == []
    composition.stop()


def test_transport_handle_is_captured_after_admission_before_class_and_instance_replacement(
    native_mcp_fixture, monkeypatch,
) -> None:
    app, composition, _sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    original_assert = composition._assert_graph
    fake_calls = []
    replaced = False

    async def fake_handle(*_args, **_kwargs):
        fake_calls.append(True)
        raise AssertionError("replacement transport handle executed")

    def replace_after_admission(candidate):
        nonlocal replaced
        original_assert(candidate)
        if not replaced:
            replaced = True
            monkeypatch.setattr(type(candidate.transport), "handle", fake_handle)
            with pytest.raises(AttributeError):
                candidate.transport.handle = fake_handle

    monkeypatch.setattr(composition, "_assert_graph", replace_after_admission)
    response = asyncio.run(composition.handle_mcp_request(
        _authenticated_mcp_request(app, native_mcp_fixture["mcp_raw_tokens"][0]),
    ))
    assert isinstance(response, Response)
    assert replaced is True
    assert fake_calls == []
    composition.stop()


def test_late_config_capsule_rejects_coordinated_owner_descriptors_and_restores_fresh_graph(
    native_mcp_fixture, monkeypatch,
) -> None:
    app, composition, _sources = _active_app(native_mcp_fixture)
    config = composition._config_owner
    detached_graph = composition._graph
    assert detached_graph is not None
    config.set(False, True)
    composition.synchronize()
    assert composition._graph is None
    assert app.state.mcp_context_plugin_enabled is False
    assert app.state.allow_context_reads is False
    fake_calls = []
    stable_fake_tuple = (app, object(), object(), object(), object())

    def fake_snapshot(_self):
        fake_calls.append("snapshot")
        return True, True, "forged"

    def fake_binding(_self):
        fake_calls.append("binding")
        return stable_fake_tuple

    original_type = type(config)
    original_snapshot = original_type.__dict__["context_snapshot"]
    original_binding = original_type.__dict__["context_owner_pins"]
    genuine_tuple = config.binding
    config.set(True, True)

    with monkeypatch.context() as attack:
        attack.setattr(original_type, "context_snapshot", fake_snapshot)
        attack.setattr(original_type, "context_owner_pins", fake_binding)
        attack.setattr(composition, "_config_snapshot_descriptor", fake_snapshot)
        attack.setattr(composition, "_config_binding_descriptor", fake_binding)
        attack.setattr(composition, "_config_binding", stable_fake_tuple)
        assert composition._configuration_snapshot() == (False, False, "invalid")
        composition.synchronize()
        assert composition._graph is None
        assert app.state.mcp_context_plugin_enabled is False
        assert app.state.allow_context_reads is False
        assert fake_calls == []

    class FakeConfigOwner:
        def __init__(self):
            self.binding = stable_fake_tuple

        def context_snapshot(self):
            fake_calls.append("fresh-owner-snapshot")
            return True, True, "fresh-owner-enabled"

        def context_owner_pins(self):
            fake_calls.append("fresh-owner-binding")
            return self.binding

    fake_owner = FakeConfigOwner()
    with monkeypatch.context() as attack:
        attack.setattr(composition, "_config_owner", fake_owner)
        attack.setattr(composition, "_config_owner_type", type(fake_owner))
        attack.setattr(
            composition, "_config_snapshot_descriptor",
            type(fake_owner).__dict__["context_snapshot"],
        )
        attack.setattr(
            composition, "_config_binding_descriptor",
            type(fake_owner).__dict__["context_owner_pins"],
        )
        attack.setattr(composition, "_config_binding", stable_fake_tuple)
        assert composition._configuration_snapshot() == (False, False, "invalid")
        composition.synchronize()
        assert composition._graph is None
        assert app.state.mcp_context_plugin_enabled is False
        assert app.state.allow_context_reads is False
        assert fake_calls == []

    same_backing_owner = _ConfigOwner(True, True)
    same_backing_owner.app = config.app
    same_backing_owner.data_dir = config.data_dir
    same_backing_owner.config_path = config.config_path
    same_backing_owner.read_descriptor = config.read_descriptor
    same_backing_owner.path_descriptor = config.path_descriptor
    same_backing_owner.binding = genuine_tuple
    with monkeypatch.context() as attack:
        attack.setattr(composition, "_config_owner", same_backing_owner)
        attack.setattr(composition, "_config_owner_type", original_type)
        attack.setattr(composition, "_config_snapshot_descriptor", original_snapshot)
        attack.setattr(composition, "_config_binding_descriptor", original_binding)
        attack.setattr(composition, "_config_binding", genuine_tuple)
        assert composition._configuration_snapshot() == (False, False, "invalid")
        composition.synchronize()
        assert composition._graph is None
        assert app.state.mcp_context_plugin_enabled is False
        assert app.state.allow_context_reads is False
        assert fake_calls == []

    composition.synchronize()
    assert composition._graph is not None
    assert composition._graph is not detached_graph
    assert app.state.mcp_context_plugin_enabled is True
    assert app.state.allow_context_reads is True
    assert fake_calls == []
    composition.stop()


def test_active_challenge_rejects_other_valid_owner_and_distinct_valid_token_without_effects(
    native_mcp_fixture, monkeypatch,
) -> None:
    fixture = native_mcp_fixture
    app, composition, _sources = _active_app(fixture)
    graph = composition._graph
    assert graph is not None
    other_username = "other-mcp-owner"
    assert fixture["auth"].create_user(
        other_username, "Other-owner9!pass", is_admin=False,
    ) is True
    other_sid = fixture["auth"].subject_id_for_username(other_username)
    genuine_sid = fixture["auth"].subject_id_for_username("fuzzy")
    assert isinstance(other_sid, str) and other_sid
    assert isinstance(genuine_sid, str) and genuine_sid != other_sid
    other_chat_id = "chat-other-valid-owner"
    with fixture["session_factory"]() as db:
        db.add(DbSession(
            id=other_chat_id,
            name="Other owner",
            endpoint_url="http://other.invalid",
            model="test-model",
            owner=other_username,
        ))
        db.commit()
        before_messages = db.query(ChatMessage).count()

    _lease, entry, challenge = _attached_entry(
        fixture, graph, "active-wrong-owner-and-token",
    )
    service = graph.service
    counters = {"capture_cookie": 0, "capture_mcp": 0, "factory": 0}
    original_capture_cookie = service._capture_cookie
    original_capture_mcp = service._capture_mcp
    session_factory = fixture["session_factory"]
    factory_type = type(session_factory)
    original_factory_call = factory_type.__dict__["__call__"]

    def counted_capture_cookie(*args, **kwargs):
        counters["capture_cookie"] += 1
        return original_capture_cookie(*args, **kwargs)

    def counted_capture_mcp(*args, **kwargs):
        counters["capture_mcp"] += 1
        return original_capture_mcp(*args, **kwargs)

    def counted_factory_call(self, *args, **kwargs):
        if self is session_factory:
            counters["factory"] += 1
        return original_factory_call(self, *args, **kwargs)

    monkeypatch.setattr(service, "_capture_cookie", counted_capture_cookie)
    monkeypatch.setattr(service, "_capture_mcp", counted_capture_mcp)
    monkeypatch.setattr(factory_type, "__call__", counted_factory_call)
    wrong_challenge = "B" * 43 if challenge.token != "B" * 43 else "C" * 43
    assert len(challenge.token) == 43
    assert len(wrong_challenge) == 43 and wrong_challenge != challenge.token

    with pytest.raises(McpPrivateContextConfirmationError) as wrong_owner:
        composition.resolve_browser_challenge(
            challenge.token, other_sid, fixture["chat_id"],
        )
    assert wrong_owner.value.code == "invalid_binding"
    assert counters == {"capture_cookie": 0, "capture_mcp": 0, "factory": 0}
    assert entry.state == "challenged"
    assert entry.challenge == challenge.token
    with pytest.raises(McpPrivateContextConfirmationError) as wrong_owner_chat:
        composition.resolve_browser_challenge(
            challenge.token, other_sid, other_chat_id,
        )
    assert wrong_owner_chat.value.code == "invalid_binding"
    assert counters == {"capture_cookie": 0, "capture_mcp": 0, "factory": 0}
    assert entry.state == "challenged"
    assert entry.challenge == challenge.token
    with pytest.raises(McpPrivateContextConfirmationError) as wrong_token:
        composition.resolve_browser_challenge(
            wrong_challenge, genuine_sid, fixture["chat_id"],
        )
    assert wrong_token.value.code == "invalid_binding"
    assert counters == {"capture_cookie": 0, "capture_mcp": 0, "factory": 0}
    assert entry.state == "challenged"
    assert entry.challenge == challenge.token
    resolved_bridge, resolved_id = composition.resolve_browser_challenge(
        challenge.token, genuine_sid, fixture["chat_id"],
    )
    assert resolved_bridge is graph.bridge
    assert resolved_id == entry.request_id
    assert counters == {"capture_cookie": 0, "capture_mcp": 0, "factory": 0}
    assert entry.state == "challenged"
    assert entry.challenge == challenge.token
    monkeypatch.undo()
    with fixture["session_factory"]() as db:
        assert db.query(ChatMessage).count() == before_messages
    composition.stop()


def test_bridge_rechecks_constructor_factory_after_guard_callback_before_db_open(
    native_mcp_fixture, monkeypatch,
) -> None:
    _app, composition, _sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    bridge = graph.bridge
    service = graph.service
    fake_calls = []

    def fake_factory():
        fake_calls.append(True)
        raise AssertionError("replacement factory executed")

    with service._cookie_authority.hold(
        native_mcp_fixture["cookie"], native_mcp_fixture["chat_id"],
    ) as binding:
        original_require = service._cookie_authority.require_current

        def replace_during_guard(current):
            alias = original_require(current)
            _replace_graph_factories(graph, fake_factory)
            return alias

        monkeypatch.setattr(service._cookie_authority, "require_current", replace_during_guard)
        with pytest.raises(Exception, match="invalid_context"):
            bridge._assert_owned_chat(binding)
    assert fake_calls == []
    composition.stop()


def test_bridge_persist_captures_factory_then_denies_guard_interval_drift_without_commit(
    native_mcp_fixture, monkeypatch,
) -> None:
    _app, composition, _sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    lease, entry, challenge = _attached_entry(
        native_mcp_fixture, graph, "persist-factory-interval",
    )
    unrelated_lease = _graph_lease(
        native_mcp_fixture, graph, "persist-factory-unrelated",
    )
    command = "/mcp-context-disclose " + challenge.token
    session_factory = native_mcp_fixture["session_factory"]
    with session_factory() as db:
        before = db.query(ChatMessage).filter(
            ChatMessage.session_id == native_mcp_fixture["chat_id"],
        ).count()
    fake_calls = []

    def fake_factory():
        fake_calls.append(True)
        raise AssertionError("replacement factory executed")

    forbidden_calls: list[str] = []

    def forbidden_cancel(*_args, **_kwargs):
        forbidden_calls.append("cancel")
        raise RuntimeError("public cancellation path executed")

    def forbidden_transitive(*_args, **_kwargs):
        forbidden_calls.append("transitive")
        raise RuntimeError("public transitive path executed")

    class ForbiddenState:
        def __init__(self, label):
            self.label = label

        def _raise(self, *_args, **_kwargs):
            forbidden_calls.append(self.label)
            raise RuntimeError("replacement authority state executed")

        __enter__ = get = pop = clear = values = items = __len__ = __setitem__ = _raise

        def __exit__(self, *_args):
            return self._raise()

    authority = graph.authority
    native_dict = object.__getattribute__(authority, "__dict__")
    original_state = {
        name: native_dict[name]
        for name in ("_lock", "_leases", "_cancelled_leases")
    }
    native_dict["cancel_lease"] = forbidden_cancel
    native_dict["cancel"] = forbidden_cancel
    monkeypatch.setattr(type(authority), "cancel_lease", forbidden_cancel)
    monkeypatch.setattr(type(authority), "cancel", forbidden_cancel)
    original_current = graph.service._cookie_authority.current_binding
    with pytest.raises(McpPrivateContextConfirmationError) as denied:
        with graph.service._hold_human_exchange(
            entry.request_id, challenge.token, native_mcp_fixture["chat_id"],
            graph.bridge, lease, native_mcp_fixture["cookie"], command,
        ) as exchange:
            operation = entry.pending_ack_operation

            def replace_during_guard():
                binding = original_current()
                _replace_graph_factories(graph, fake_factory)
                native_dict["_lease_snapshot"] = forbidden_transitive
                native_dict["recheck_lease"] = forbidden_transitive
                native_dict["_lock"] = ForbiddenState("lock")
                native_dict["_leases"] = ForbiddenState("leases")
                native_dict["_cancelled_leases"] = ForbiddenState("cancelled")
                return binding

            monkeypatch.setattr(
                graph.service._cookie_authority, "current_binding", replace_during_guard,
            )
            graph.bridge._persist_ack(
                entry, lease, challenge.token, command, exchange, operation,
            )
    assert type(denied.value) is McpPrivateContextConfirmationError
    assert denied.value.code == "invalid_context"

    for name in ("cancel_lease", "cancel", "_lease_snapshot", "recheck_lease"):
        native_dict.pop(name, None)
    native_dict.update(original_state)
    monkeypatch.undo()
    _replace_graph_factories(graph, session_factory)

    with pytest.raises(McpContextAuthorityError) as cancelled:
        graph.authority.recheck_lease(lease)
    assert type(cancelled.value) is McpContextAuthorityError
    assert cancelled.value.code == "cancelled"
    assert graph.authority.recheck_lease(unrelated_lease) is unrelated_lease.binding
    with session_factory() as db:
        after = db.query(ChatMessage).filter(
            ChatMessage.session_id == native_mcp_fixture["chat_id"],
        ).count()
    assert after == before
    assert fake_calls == []
    assert forbidden_calls == []
    composition.stop()


def test_bridge_readback_captures_factory_then_denies_last_guard_drift_without_reopen(
    native_mcp_fixture, monkeypatch,
) -> None:
    _app, composition, _sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    lease, entry, challenge = _attached_entry(
        native_mcp_fixture, graph, "readback-factory-interval",
    )
    command = "/mcp-context-disclose " + challenge.token
    with graph.service._hold_human_exchange(
        entry.request_id, challenge.token, native_mcp_fixture["chat_id"],
        graph.bridge, lease, native_mcp_fixture["cookie"], command,
    ) as exchange:
        graph.service.disclose(
            entry.request_id, challenge.token,
            raw_user_command=command, human_exchange=exchange,
        )
    session_factory = native_mcp_fixture["session_factory"]
    with session_factory() as db:
        before = db.query(ChatMessage).filter(
            ChatMessage.session_id == native_mcp_fixture["chat_id"],
        ).count()
    fake_calls = []

    def fake_factory():
        fake_calls.append(True)
        raise AssertionError("replacement factory executed")

    original_require = graph.service._cookie_authority.require_current
    with graph.service._cookie_authority.hold(
        native_mcp_fixture["cookie"], native_mcp_fixture["chat_id"],
    ) as binding:
        def replace_at_last_guard(current):
            alias = original_require(current)
            _replace_graph_factories(graph, fake_factory)
            return alias

        monkeypatch.setattr(
            graph.service._cookie_authority, "require_current", replace_at_last_guard,
        )
        with pytest.raises(Exception, match="invalid_context"):
            graph.bridge._readback_ack_under_guard(entry, binding)
    with session_factory() as db:
        after = db.query(ChatMessage).filter(
            ChatMessage.session_id == native_mcp_fixture["chat_id"],
        ).count()
    assert after == before
    assert fake_calls == []
    composition.stop()


@pytest.mark.parametrize(
    ("operation_index", "operation_name"),
    (
        (0, "issue_authenticated_challenge"),
        (1, "revoke_authenticated"),
        (2, "resolve_authenticated_challenge"),
    ),
    ids=("issue", "revoke", "resolve"),
)
def test_captured_bridge_descriptor_ignores_post_validation_class_and_instance_replacement(
    native_mcp_fixture, monkeypatch, operation_index, operation_name,
) -> None:
    app, composition, _sources = _active_app(native_mcp_fixture)
    graph = composition._graph
    assert graph is not None
    replacement_calls = []
    original_capture = composition._active_bridge_operation

    def replacement(*_args, **_kwargs):
        replacement_calls.append(True)
        raise AssertionError("replacement descriptor executed")

    def capture_then_replace(index):
        bridge, descriptor = original_capture(index)
        assert index == operation_index
        monkeypatch.setattr(type(bridge), operation_name, replacement)
        bridge.__dict__[operation_name] = replacement
        return bridge, descriptor

    monkeypatch.setattr(composition, "_active_bridge_operation", capture_then_replace)
    request = _stream_request(app, "/api/context/mcp/challenge", [
        {"type": "http.request", "body": b'{}', "more_body": False},
    ])
    request._body = b'{}'
    with pytest.raises(Exception):
        if operation_index == 0:
            composition.issue_browser_challenge(request, "descriptor-race")
        elif operation_index == 1:
            composition.revoke_browser_consent(request, "descriptor-race")
        else:
            composition.resolve_browser_challenge(
                "a" * 43, native_mcp_fixture["auth"].subject_id_for_username("fuzzy"),
                native_mcp_fixture["chat_id"],
            )
    assert replacement_calls == []
    composition.stop()


@pytest.mark.parametrize("path", ("/api/context/mcp/challenge", "/api/context/mcp/revoke"))
def test_real_request_stream_accepts_exact_bound_and_rejects_chunked_overflow_before_effects(
    native_mcp_fixture, path,
) -> None:
    app, composition = _bare_app(
        native_mcp_fixture["auth"], native_mcp_fixture["session_factory"],
    )
    effects = []

    def unavailable(_index):
        effects.append(True)
        raise ValueError("native graph unavailable")

    composition._active_bridge_operation = unavailable
    endpoint = _route_endpoint(app, path)
    payload = json.dumps({"request_id": "bounded-stream"}, separators=(",", ":")).encode()
    exact = b" " * (4096 - len(payload)) + payload
    exact_request = _stream_request(app, path, [
        {"type": "http.request", "body": exact[:1024], "more_body": True},
        {"type": "http.request", "body": exact[1024:3072], "more_body": True},
        {"type": "http.request", "body": exact[3072:], "more_body": False},
    ])
    exact_response = asyncio.run(endpoint(exact_request))
    assert exact_response.status_code == 403
    assert effects == [True]
    assert exact_request._body == exact

    effects.clear()
    over_request = _stream_request(app, path, [
        {"type": "http.request", "body": b" " * 4090, "more_body": True},
        {"type": "http.request", "body": payload, "more_body": False},
    ], content_length=b"5")
    over_response = asyncio.run(endpoint(over_request))
    assert over_response.status_code == 403
    assert effects == []
    assert not hasattr(over_request, "_body")


@pytest.mark.parametrize("path", ("/api/context/mcp/challenge", "/api/context/mcp/revoke"))
def test_real_request_stream_disconnect_and_cancellation_leave_no_cache_or_effect(
    native_mcp_fixture, path,
) -> None:
    app, composition = _bare_app(
        native_mcp_fixture["auth"], native_mcp_fixture["session_factory"],
    )
    effects = []
    composition._active_bridge_operation = lambda _index: effects.append(True)
    endpoint = _route_endpoint(app, path)
    disconnected = _stream_request(app, path, [
        {"type": "http.request", "body": b'{"request_id":', "more_body": True},
        {"type": "http.disconnect"},
    ])
    response = asyncio.run(endpoint(disconnected))
    assert response.status_code == 403
    assert effects == []
    assert not hasattr(disconnected, "_body")

    async def cancel_probe():
        entered = asyncio.Event()

        async def stalled():
            entered.set()
            await asyncio.Event().wait()

        request = _stream_request(app, path, [stalled])
        task = asyncio.create_task(endpoint(request))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not hasattr(request, "_body")

    asyncio.run(cancel_probe())
    assert effects == []


@pytest.mark.parametrize("path", ("/api/context/mcp/challenge", "/api/context/mcp/revoke"))
def test_real_request_stream_uses_one_actual_ten_second_deadline_across_empty_chunks(
    native_mcp_fixture, path,
) -> None:
    app, composition = _bare_app(
        native_mcp_fixture["auth"], native_mcp_fixture["session_factory"],
    )
    effects = []
    composition._active_bridge_operation = lambda _index: effects.append(True)
    endpoint = _route_endpoint(app, path)

    async def immediate_empty():
        return {"type": "http.request", "body": b"", "more_body": True}

    request = _stream_request(app, path, [])
    object.__setattr__(request, "_receive", immediate_empty)
    started = time.monotonic()
    response = asyncio.run(endpoint(request))
    elapsed = time.monotonic() - started
    assert response.status_code == 403
    assert 9.5 <= elapsed < 15.0
    assert effects == []
    assert not hasattr(request, "_body")
