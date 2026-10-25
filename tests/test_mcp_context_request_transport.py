"""Outcome tests for the inactive bounded MCP context HTTP transport."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import sys
import threading
from types import SimpleNamespace

from fastapi import BackgroundTasks, Request
from fastapi.testclient import TestClient
import pytest

from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import (
    McpContextAuthorityError,
    issue_mcp_bearer_proof,
    release_mcp_bearer_proof,
)
from src.mcp_context_contract import MCP_CONTEXT_BODY_MAX_BYTES
from src.mcp_context_request_runtime import McpContextRequestRuntime
import src.mcp_context_request_transport as transport_module
import src.mcp_context_causal_observation as causal_observation
from src.mcp_context_request_transport import McpContextRequestTransport


def _support():
    # The persisted/browser-ACK graph is intentionally imported after pytest
    # collection so the two fixture modules do not form a collection cycle.
    import tests.test_mcp_context_request_runtime as support

    return support


def _scope(headers: tuple[tuple[bytes, bytes], ...] = ()) -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test/context-request-transport",
        "raw_path": b"/test/context-request-transport",
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


def _request_from_chunks(
    chunks: tuple[bytes, ...],
    *,
    headers: tuple[tuple[bytes, bytes], ...] = (),
    before_receive=None,
) -> Request:
    index = 0

    async def receive():
        nonlocal index
        if before_receive is not None:
            await before_receive(index)
        if index >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        body = chunks[index]
        index += 1
        return {
            "type": "http.request",
            "body": body,
            "more_body": index < len(chunks),
        }

    return Request(_scope(headers), receive)


def _assert_content_free(response, status: int) -> None:
    assert response.status_code == status
    assert response.body == b""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-type" not in response.headers


async def _handle_with_concurrent_close_at_native_return(
    transport: McpContextRequestTransport,
    incoming: Request,
):
    native_return = threading.Event()
    close_complete = threading.Event()
    observed = []
    edge_close_complete = []
    target_code = McpContextRequestRuntime.handle.__code__

    def close_worker() -> None:
        if not native_return.wait(2.0):
            return
        transport.close()
        close_complete.set()

    worker = threading.Thread(target=close_worker, daemon=True)
    worker_started = False
    old_profile = sys.getprofile()

    def profile(frame, event, arg):
        nonlocal worker_started
        if frame.f_code is target_code and event == "return" and type(arg) is bytes:
            observed.append(arg)
            native_return.set()
            if not worker_started:
                worker.start()
                worker_started = True
            edge_close_complete.append(close_complete.wait(2.0))
        return profile

    sys.setprofile(profile)
    try:
        response = await transport.handle(incoming)
    finally:
        sys.setprofile(old_profile)
        if worker_started:
            worker.join(timeout=2.0)
    assert worker_started
    assert not worker.is_alive()
    assert close_complete.is_set()
    assert edge_close_complete == [True]
    assert len(observed) == 1
    assert response.body is observed[0]
    return response


@pytest.fixture
async def transport_app(tmp_path, monkeypatch, request):
    support = _support()
    application = support.runtime_app.__wrapped__(
        tmp_path, monkeypatch, SimpleNamespace(param=True),
    )
    base_lifespan = application.router.lifespan_context
    transport = None

    def authenticate(incoming: Request) -> McpAuthenticatedIdentity:
        application.state.transport_auth_calls += 1
        if incoming.headers.get("x-auth-failure") == "raise":
            raise RuntimeError("private authentication failure")
        if incoming.headers.get("x-auth-failure") == "type":
            return object()
        return support._identity(
            application,
            owner_alias=incoming.headers.get("x-owner-alias", "fuzzy"),
        )

    def capture_prepare_proof(incoming: Request):
        application.state.transport_proof_calls += 1
        if incoming.headers.get("x-proof-failure") == "raise":
            raise RuntimeError("private proof failure")
        if incoming.headers.get("x-proof-failure") == "type":
            return object()
        return issue_mcp_bearer_proof(
            application.state.fixture["mcp_raw_tokens"][0],
        )

    @asynccontextmanager
    async def lifespan(app):
        nonlocal transport
        owner_thread = threading.get_ident()
        try:
            async with base_lifespan(app):
                assert threading.get_ident() == owner_thread
                local_transport = None
                try:
                    runtime = support._ensure_runtime(app)
                    app.state.transport_auth_calls = 0
                    app.state.transport_proof_calls = 0
                    app.state.transport_native_bytes_identical = False
                    app.state.transport_background_calls = 0
                    local_transport = McpContextRequestTransport(
                        runtime,
                        authenticate,
                        capture_prepare_proof,
                    )
                    transport = local_transport
                    app.state.request_transport = local_transport
                    yield
                finally:
                    assert threading.get_ident() == owner_thread
                    if local_transport is not None:
                        local_transport.close()
                    support._shutdown(app)
                    transport = None
                    assert threading.get_ident() == owner_thread
        finally:
            assert threading.get_ident() == owner_thread

    @application.post("/test/context-request-transport")
    async def context_request_transport(
        incoming: Request, background_tasks: BackgroundTasks,
    ):
        local_transport = transport
        assert local_transport is not None
        captured: list[bytes] = []
        target_code = McpContextRequestRuntime.handle.__code__
        old_profile = sys.getprofile()

        def profile(frame, event, arg):
            if frame.f_code is target_code and event == "return" and type(arg) is bytes:
                captured.append(arg)
            return profile

        profile_requested = incoming.headers.get("x-capture-native-return") == "1"
        if profile_requested:
            sys.setprofile(profile)
        try:
            response = await local_transport.handle(incoming)
        finally:
            if profile_requested:
                sys.setprofile(old_profile)
        if response.status_code == 200:
            payload = json.loads(response.body)
            if (
                type(payload) is dict
                and set(payload) == {"jsonrpc", "id", "result"}
                and type(payload["jsonrpc"]) is str
                and payload["jsonrpc"] == "2.0"
                and type(payload["id"]) in {str, int}
                and type(payload["result"]) is dict
                and set(payload["result"]) == {
                    "request_id",
                    "selected_count",
                    "requires_private_confirmation",
                }
                and type(payload["result"]["request_id"]) is str
                and type(payload["result"]["selected_count"]) is int
                and payload["result"]["requires_private_confirmation"] is True
            ):
                result = payload["result"]
                request_runtime = application.state.request_runtime
                record = request_runtime._records.get(result["request_id"])
                if record is not None:
                    assert record.request_id == result["request_id"]
                    assert result["selected_count"] == len(record.selection.items)
                    application.state.request_id = record.request_id
                    application.state.lease = record.lease
        application.state.transport_native_bytes_identical = (
            not profile_requested
            or len(captured) == 1
            and response.body is captured[0]
        )
        if incoming.headers.get("x-background-check") == "1":
            background_tasks.add_task(
                setattr,
                application.state,
                "transport_background_calls",
                application.state.transport_background_calls + 1,
            )
        return response

    application.router.lifespan_context = lifespan
    # The frozen 12-node inventory has one synchronous HTTP node whose
    # TestClient owns lifespan entry; every other node holds it in this fixture.
    if request.node.name == (
        "test_real_persisted_browser_ack_flow_returns_the_native_bytes_unchanged"
    ):
        yield application
        return
    async with application.router.lifespan_context(application):
        yield application


def test_real_persisted_browser_ack_flow_returns_the_native_bytes_unchanged(transport_app):
    support = _support()
    with TestClient(transport_app) as client:
        selection = support._selection_wire(transport_app)
        prepared = client.post(
            "/test/context-request-transport",
            content=support._wire(
                "harbour_context_prepare",
                {
                    "chat_session_id": "chat-owned",
                    "selection": selection,
                    "page_bytes": 16_384,
                },
                1,
            ),
            headers={
                "x-capture-native-return": "1",
                "x-background-check": "1",
            },
        )
        assert prepared.status_code == 200
        assert transport_app.state.transport_native_bytes_identical is True
        assert transport_app.state.transport_background_calls == 1
        prepared_result = prepared.json()["result"]
        request_id = prepared_result["request_id"]
        assert prepared_result["selected_count"] == len(selection)
        assert prepared_result["requires_private_confirmation"] is True
        support._browser_ack(client, request_id)

        manifest = client.post(
            "/test/context-request-transport",
            content=support._wire(
                "harbour_context_manifest", {"request_id": request_id}, "manifest",
            ),
            headers={"x-capture-native-return": "1"},
        )
        assert manifest.status_code == 200
        assert manifest.json()["result"]["complete"] is True
        assert transport_app.state.transport_native_bytes_identical is True

        cursor = None
        pages = 0
        while True:
            arguments = {"request_id": request_id}
            if cursor is not None:
                arguments["content_cursor"] = cursor
            response = client.post(
                "/test/context-request-transport",
                content=support._wire(
                    "harbour_context_page", arguments, 100 + pages,
                ),
                headers={"x-capture-native-return": "1"},
            )
            assert response.status_code == 200
            assert transport_app.state.transport_native_bytes_identical is True
            result = response.json()["result"]
            pages += 1
            if result["complete"]:
                assert result["content_cursor"] is None
                break
            cursor = result["content_cursor"]
            assert type(cursor) is str and cursor
            assert pages < 100

        assert pages >= len(selection)
        assert request_id not in transport_app.state.request_runtime._records
        assert request_id not in transport_app.state.service._entries
        assert transport_app.state.transport_auth_calls == pages + 2
        assert transport_app.state.transport_proof_calls == 1


@pytest.mark.asyncio
async def test_authentication_precedes_body_and_proof_capture_follows_closed_parse(
    transport_app, monkeypatch,
):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    receive_calls = 0
    proof_calls = 0

    async def count_receive(_index: int) -> None:
        nonlocal receive_calls
        receive_calls += 1

    def reject_auth(_request: Request):
        raise RuntimeError("must stay private")

    def capture(_request: Request):
        nonlocal proof_calls
        proof_calls += 1
        return issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        )

    rejected = McpContextRequestTransport(runtime, reject_auth, capture)
    response = await rejected.handle(
        _request_from_chunks((b"not read",), before_receive=count_receive),
    )
    _assert_content_free(response, 401)
    assert receive_calls == 0 and proof_calls == 0

    accepted = McpContextRequestTransport(runtime, lambda _request: identity, capture)
    malformed = await accepted.handle(_request_from_chunks((b"{",)))
    _assert_content_free(malformed, 400)
    assert proof_calls == 0

    missing = await accepted.handle(_request_from_chunks((support._wire(
        "harbour_context_manifest", {"request_id": "r." + "a" * 43}, 4,
    ),)))
    _assert_content_free(missing, 403)
    assert proof_calls == 0

    prepare = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        5,
    )
    parser_dependency_calls = []

    def fake_parser_dependency(*_args, **_kwargs):
        parser_dependency_calls.append(True)
        raise AssertionError("substituted parser dependency ran")

    parser_attacks = (
        (transport_module._contract_module, "_unique_pairs"),
        (transport_module._contract_module.json, "loads"),
        (transport_module._contract_module.json, "dumps"),
        (transport_module._contract_module, "McpContextToolCall"),
    )
    for target, name in parser_attacks:
        with monkeypatch.context() as attack:
            attack.setattr(target, name, fake_parser_dependency)
            response = await accepted.handle(_request_from_chunks((prepare,)))
        _assert_content_free(response, 503)
    assert parser_dependency_calls == []
    assert proof_calls == 0
    rejected.close()
    accepted.close()


@pytest.mark.asyncio
async def test_incremental_cap_and_cancelled_stream_leave_transport_reusable(transport_app):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    transport = McpContextRequestTransport(
        runtime,
        lambda _request: identity,
        lambda _request: issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        ),
    )

    oversized = await transport.handle(_request_from_chunks((
        b"x" * MCP_CONTEXT_BODY_MAX_BYTES,
        b"y",
        b"must-not-be-read",
    )))
    _assert_content_free(oversized, 413)

    receive_started = asyncio.Event()

    async def never_finishes(_index: int) -> None:
        receive_started.set()
        await asyncio.Future()

    cancelled = asyncio.create_task(transport.handle(
        _request_from_chunks((b"unused",), before_receive=never_finishes),
    ))
    await asyncio.wait_for(receive_started.wait(), timeout=1.0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    malformed = await transport.handle(_request_from_chunks((b"{",)))
    _assert_content_free(malformed, 400)
    transport.close()


@pytest.mark.asyncio
async def test_body_read_deadline_returns_bounded_content_free_408(
    transport_app, monkeypatch,
):
    support = _support()
    identity = support._identity(transport_app)
    proof_calls = 0
    receive_started = asyncio.Event()

    def capture(_request: Request):
        nonlocal proof_calls
        proof_calls += 1
        return object()

    async def stall(_index: int) -> None:
        receive_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        transport_module,
        "MCP_CONTEXT_TRANSPORT_BODY_TIMEOUT_SECONDS",
        0.01,
    )
    transport = McpContextRequestTransport(
        transport_app.state.request_runtime,
        lambda _request: identity,
        capture,
    )
    response = await asyncio.wait_for(
        transport.handle(_request_from_chunks((b"not-observed",), before_receive=stall)),
        timeout=1.0,
    )
    assert receive_started.is_set()
    assert proof_calls == 0
    _assert_content_free(response, 408)
    transport.close()


@pytest.mark.asyncio
async def test_close_between_checkpoint_and_reservation_stops_native_egress(
    transport_app,
):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    captured_proofs = []

    def capture_proof(_request: Request):
        proof = issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        )
        captured_proofs.append(proof)
        return proof

    closing = McpContextRequestTransport(
        runtime, lambda _request: identity, capture_proof,
    )
    prepare = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        20,
    )
    records_before = dict(runtime._records)
    lifecycle_factory_code = transport_module._TRUSTED_LIFECYCLE_FACTORY.__code__
    code_type = type(lifecycle_factory_code)
    checkpoint_codes = tuple(
        constant for constant in lifecycle_factory_code.co_consts
        if type(constant) is code_type and constant.co_name == "checkpoint"
    )
    reserve_codes = tuple(
        constant for constant in lifecycle_factory_code.co_consts
        if type(constant) is code_type and constant.co_name == "reserve"
    )
    assert len(checkpoint_codes) == 1
    assert len(reserve_codes) == 1
    checkpoint_code, reserve_code = checkpoint_codes[0], reserve_codes[0]
    native_code = McpContextRequestRuntime.handle.__code__
    close_requested = threading.Event()
    close_complete = threading.Event()
    fired = []
    native_entries = []

    def close_worker() -> None:
        if not close_requested.wait(2.0):
            return
        closing.close()
        close_complete.set()

    worker = threading.Thread(target=close_worker, daemon=True)
    worker_started = False
    close_completed_at_checkpoint = False
    old_profile = sys.getprofile()

    def profile(frame, event, _arg):
        nonlocal worker_started, close_completed_at_checkpoint
        if frame.f_code is native_code and event == "call":
            native_entries.append(None)
        if (
            frame.f_code is checkpoint_code and event == "return"
            and frame.f_back is not None and frame.f_back.f_code is reserve_code
        ):
            fired.append(None)
            worker.start()
            worker_started = True
            close_requested.set()
            close_completed_at_checkpoint = close_complete.wait(2.0)
        return profile

    sys.setprofile(profile)
    try:
        response = await closing.handle(_request_from_chunks((prepare,)))
    finally:
        sys.setprofile(old_profile)
        close_requested.set()
        if worker_started:
            worker.join(timeout=2.0)

    assert len(fired) == 1
    assert not worker.is_alive()
    assert close_complete.is_set()
    assert close_completed_at_checkpoint is True
    _assert_content_free(response, 503)
    assert native_entries == []
    assert len(captured_proofs) == 1
    with pytest.raises(McpContextAuthorityError):
        release_mcp_bearer_proof(captured_proofs[0])
    assert runtime._closed is False
    assert runtime._records == records_before


@pytest.mark.asyncio
async def test_prepare_native_return_close_defers_until_request_id_is_published(
    transport_app,
):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    prepare = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        21,
    )
    closing = McpContextRequestTransport(
        runtime,
        lambda _request: identity,
        lambda _request: issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        ),
    )
    response = await _handle_with_concurrent_close_at_native_return(
        closing, _request_from_chunks((prepare,)),
    )
    assert response.status_code == 200
    request_id = json.loads(response.body)["result"]["request_id"]
    assert request_id in runtime._records
    _assert_content_free(
        await closing.handle(_request_from_chunks((b"{}",))), 503,
    )

    cleanup = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    cancelled = await cleanup.handle(_request_from_chunks((support._wire(
        "harbour_context_cancel", {"request_id": request_id}, 22,
    ),)))
    assert cancelled.status_code == 200
    assert json.loads(cancelled.body)["result"] == {"cancelled": True}
    assert request_id not in runtime._records
    cleanup.close()


@pytest.mark.asyncio
async def test_existing_call_native_return_close_publishes_frozen_response(
    transport_app,
):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    setup = McpContextRequestTransport(
        runtime,
        lambda _request: identity,
        lambda _request: issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        ),
    )
    prepared = await setup.handle(_request_from_chunks((support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        31,
    ),)))
    assert prepared.status_code == 200
    request_id = json.loads(prepared.body)["result"]["request_id"]

    closing = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    cancelled = await _handle_with_concurrent_close_at_native_return(
        closing,
        _request_from_chunks((support._wire(
            "harbour_context_cancel", {"request_id": request_id}, 32,
        ),)),
    )
    assert cancelled.status_code == 200
    assert json.loads(cancelled.body)["result"] == {"cancelled": True}
    assert request_id not in runtime._records
    _assert_content_free(
        await closing.handle(_request_from_chunks((b"{}",))), 503,
    )
    setup.close()


@pytest.mark.asyncio
async def test_auth_proof_type_failures_and_failed_prepare_release_exact_pending_proof(
    transport_app,
):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    prepare = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        7,
    )

    bad_identity = McpContextRequestTransport(
        runtime, lambda _request: object(), lambda _request: object(),
    )
    _assert_content_free(
        await bad_identity.handle(_request_from_chunks((prepare,))), 401,
    )

    bad_proof = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    _assert_content_free(
        await bad_proof.handle(_request_from_chunks((prepare,))), 403,
    )

    stale_identity = McpAuthenticatedIdentity(
        identity.token_id,
        identity.owner_alias,
        identity.owner_subject_id,
        identity.cache_generation + 1,
        identity.security_action_revision,
        identity.scope,
    )
    stale = McpContextRequestTransport(
        runtime,
        lambda _request: stale_identity,
        lambda _request: issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        ),
    )
    _assert_content_free(
        await stale.handle(_request_from_chunks((prepare,))), 403,
    )

    captured = []
    holder = {}

    def close_after_capture(_request: Request):
        proof = issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        )
        captured.append(proof)
        holder["transport"].close()
        return proof

    closed = McpContextRequestTransport(runtime, lambda _request: identity, close_after_capture)
    holder["transport"] = closed
    _assert_content_free(
        await closed.handle(_request_from_chunks((prepare,))), 503,
    )
    assert len(captured) == 1
    with pytest.raises(McpContextAuthorityError):
        release_mcp_bearer_proof(captured[0])
    assert runtime._closed is False
    assert runtime._records == {}
    bad_identity.close()
    bad_proof.close()
    stale.close()


@pytest.mark.asyncio
async def test_close_is_transport_only_and_runtime_substitution_never_calls_fake(
    transport_app, monkeypatch,
):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)

    closed = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    closed.close()
    closed.close()
    _assert_content_free(
        await closed.handle(_request_from_chunks((b"{}",))), 503,
    )
    assert runtime._closed is False

    native_calls = []

    def fake_handle(*_args, **_kwargs):
        native_calls.append(True)
        return b"{}"

    class_substituted = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    with monkeypatch.context() as attack:
        attack.setattr(McpContextRequestRuntime, "handle", fake_handle)
        response = await class_substituted.handle(_request_from_chunks((b"{}",)))
    _assert_content_free(response, 503)
    assert native_calls == []

    instance_substituted = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    object.__setattr__(instance_substituted, "_runtime", object())
    response = await instance_substituted.handle(_request_from_chunks((b"{}",)))
    _assert_content_free(response, 503)
    assert native_calls == []
    object.__setattr__(instance_substituted, "_runtime", runtime)

    hostile_calls = []

    def hostile(*_args, **_kwargs):
        hostile_calls.append(True)
        raise AssertionError("hostile shared response hook ran")

    hostile_property = property(hostile, hostile)
    hostile_subclass_hook = classmethod(hostile)
    base_attacks = (
        ("__getattribute__", hostile),
        ("__setattr__", hostile),
        ("__call__", hostile),
        ("__init_subclass__", hostile_subclass_hook),
        ("render", hostile),
        ("init_headers", hostile),
        ("status_code", hostile_property),
        ("raw_headers", hostile_property),
        ("body", hostile_property),
        ("media_type", hostile_property),
        ("background", hostile_property),
        ("headers", hostile_property),
    )
    response_guarded = McpContextRequestTransport(
        runtime, lambda _request: identity, lambda _request: object(),
    )
    phase_calls = {"auth": 0, "body": 0, "proof": 0}

    def phase_authenticate(_request: Request):
        phase_calls["auth"] += 1
        return identity

    def phase_proof(_request: Request):
        phase_calls["proof"] += 1
        return object()

    async def phase_body(_index: int) -> None:
        phase_calls["body"] += 1

    phase_guarded = McpContextRequestTransport(
        runtime, phase_authenticate, phase_proof,
    )
    preauth_attacks = (
        (transport_module, "Response", object),
        (transport_module, "_TRUSTED_TYPE_NEW", object()),
        (transport_module, "_TRUSTED_TYPE_GETATTRIBUTE", object()),
        (transport_module, "_TRUSTED_OBJECT_NEW", object()),
        (transport_module, "_TRUSTED_RESPONSE_DICT_DESCRIPTOR", object()),
        (transport_module, "_TRUSTED_REQUEST_STREAM", hostile),
        (transport_module, "_TRUSTED_PARSE", hostile),
        (transport_module, "_TRUSTED_RELEASE_PROOF", hostile),
        (transport_module, "_PUBLICATION_FACTORY", hostile),
        (transport_module, "_TRUSTED_PUBLICATION_FACTORY", hostile),
        (transport_module, "_LIFECYCLE_FACTORY", hostile),
        (transport_module, "_TRUSTED_LIFECYCLE_FACTORY", hostile),
        (transport_module, "_PRIVATE_RELEASE_PROOF", hostile),
    )
    for target, name, replacement in preauth_attacks:
        with monkeypatch.context() as attack:
            attack.setattr(target, name, replacement)
            response = await phase_guarded.handle(_request_from_chunks(
                (b"unobserved",), before_receive=phase_body,
            ))
        _assert_content_free(response, 503)
        assert phase_calls == {"auth": 0, "body": 0, "proof": 0}
        assert hostile_calls == []

    for name, replacement in base_attacks:
        with monkeypatch.context() as attack:
            attack.setattr(
                transport_module.Response, name, replacement, raising=False,
            )
            response = await phase_guarded.handle(_request_from_chunks(
                (b"unobserved",), before_receive=phase_body,
            ))
            _assert_content_free(response, 503)
        assert phase_calls == {"auth": 0, "body": 0, "proof": 0}
        assert hostile_calls == []

    body_phase = McpContextRequestTransport(
        runtime, phase_authenticate, phase_proof,
    )
    with monkeypatch.context() as attack:
        async def mutate_during_body(_index: int) -> None:
            phase_calls["body"] += 1
            attack.setattr(
                transport_module.Response,
                "__init_subclass__",
                hostile_subclass_hook,
            )

        response = await body_phase.handle(_request_from_chunks(
            (b"{}",), before_receive=mutate_during_body,
        ))
    _assert_content_free(response, 503)
    assert phase_calls == {"auth": 1, "body": 1, "proof": 0}
    assert hostile_calls == []

    prepare_wire = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        40,
    )
    captured_proofs = []
    records_before = dict(runtime._records)
    with monkeypatch.context() as attack:
        def mutate_during_proof(_request: Request):
            phase_calls["proof"] += 1
            proof = issue_mcp_bearer_proof(
                transport_app.state.fixture["mcp_raw_tokens"][0],
            )
            captured_proofs.append(proof)
            attack.setattr(
                transport_module.Response,
                "__init_subclass__",
                hostile_subclass_hook,
            )
            return proof

        proof_phase = McpContextRequestTransport(
            runtime, phase_authenticate, mutate_during_proof,
        )
        response = await proof_phase.handle(
            _request_from_chunks((prepare_wire,)),
        )
    _assert_content_free(response, 503)
    assert phase_calls == {"auth": 2, "body": 1, "proof": 1}
    assert len(captured_proofs) == 1
    with pytest.raises(McpContextAuthorityError):
        release_mcp_bearer_proof(captured_proofs[0])
    assert runtime._records == records_before
    assert hostile_calls == []

    prior = await response_guarded.handle(_request_from_chunks((b"{",)))
    _assert_content_free(prior, 400)
    prior_view = type(prior)
    prior_shield = prior_view.__bases__[0]
    assert prior_view.__name__ == "_TransportResponseView"
    assert prior_shield.__name__ == "_TransportResponseShield"
    assert prior_view.__bases__ == (prior_shield, transport_module.Response)
    assert prior_view.__mro__ == (
        prior_view, prior_shield, *transport_module.Response.__mro__,
    )
    assert set(prior_shield.__dict__) == {
        "__module__", "__init_subclass__", "__dict__", "__weakref__", "__doc__",
    }
    assert set(prior_view.__dict__) == {
        "__module__", "__getattribute__", "__setattr__", "__call__",
        "__getitem__", "get", "items", "keys", "values", "__iter__", "__len__",
        "__contains__", "__init_subclass__", "__doc__",
    }
    for descriptor_name in ("__dict__", "__weakref__"):
        descriptor = prior_shield.__dict__[descriptor_name]
        assert type(descriptor) is type(
            transport_module._TRUSTED_RESPONSE_DICT_DESCRIPTOR,
        )
        assert descriptor.__name__ == descriptor_name
        assert descriptor.__objclass__ is prior_shield

    prior_attacks = (
        ("__getattribute__", hostile),
        ("__setattr__", hostile),
        ("__call__", hostile),
        ("__init_subclass__", hostile_subclass_hook),
        ("status_code", hostile_property),
        ("raw_headers", hostile_property),
        ("body", hostile_property),
        ("background", hostile_property),
    )
    with monkeypatch.context() as attack:
        for name, replacement in prior_attacks:
            attack.setattr(prior_view, name, replacement, raising=False)
            attack.setattr(prior_shield, name, replacement, raising=False)
        fresh = await response_guarded.handle(_request_from_chunks((b"{",)))
    _assert_content_free(fresh, 400)
    assert type(fresh) is not prior_view
    assert type(fresh).__bases__[0] is not prior_shield
    assert hostile_calls == []

    prepare = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        41,
    )
    native_return = []
    target_code = McpContextRequestRuntime.handle.__code__
    native_mutation = McpContextRequestTransport(
        runtime,
        lambda _request: identity,
        lambda _request: issue_mcp_bearer_proof(
            transport_app.state.fixture["mcp_raw_tokens"][0],
        ),
    )
    old_profile = sys.getprofile()
    with monkeypatch.context() as attack:
        def mutate_at_native_return(frame, event, arg):
            if frame.f_code is target_code and event == "return" and type(arg) is bytes:
                native_return.append(arg)
                for name, replacement in base_attacks:
                    attack.setattr(
                        transport_module.Response, name, replacement, raising=False,
                    )
                attack.setattr(
                    transport_module, "_PUBLICATION_FACTORY", hostile,
                )
                attack.setattr(
                    transport_module, "_PRIVATE_RELEASE_PROOF", hostile,
                )
            return mutate_at_native_return

        sys.setprofile(mutate_at_native_return)
        try:
            response = await native_mutation.handle(
                _request_from_chunks((prepare,)),
            )
        finally:
            sys.setprofile(old_profile)
        assert isinstance(response, transport_module._TRUSTED_RESPONSE_CLASS)
        assert response.status_code == 200
        assert response.body is native_return[0]
        assert response.headers["content-type"] == "application/json"
        assert response.headers["cache-control"] == "no-store"
        headers = response.raw_headers
        sent = []
        background_calls = []

        async def background():
            background_calls.append(True)

        response.background = background

        async def receive():
            raise AssertionError("response unexpectedly received ASGI input")

        async def send(message):
            sent.append(message)

        await response(_scope(), receive, send)
        assert sent[0]["headers"] is headers
        assert sent[1]["body"] is native_return[0]
        assert background_calls == [True]
    assert len(native_return) == 1
    assert hostile_calls == []
    request_id = json.loads(response.body)["result"]["request_id"]
    followup = await native_mutation.handle(_request_from_chunks((support._wire(
        "harbour_context_cancel", {"request_id": request_id}, 42,
    ),)))
    assert followup.status_code == 200

    class_substituted.close()
    instance_substituted.close()
    response_guarded.close()
    phase_guarded.close()
    body_phase.close()
    proof_phase.close()
    native_mutation.close()


@pytest.mark.asyncio
async def test_private_publication_owner_consumes_every_emission_once(transport_app):
    transport = transport_app.state.request_transport

    async def new_response():
        response = await transport.handle(_request_from_chunks((b"{",)))
        _assert_content_free(response, 400)
        return response

    async def receive():
        raise AssertionError("response unexpectedly received ASGI input")

    response = await new_response()
    raw_headers = response.raw_headers
    sent = []
    background_calls = []
    background = BackgroundTasks()
    background.add_task(background_calls.append, "once")
    response.background = background

    async def send(message):
        sent.append(message)

    await response(_scope(), receive, send)
    await response(_scope(), receive, send)
    assert len(sent) == 2
    assert sent[0]["headers"] is raw_headers
    assert sent[1]["body"] is response.body
    assert background_calls == ["once"]

    start_failed = await new_response()
    start_background = []
    tasks = BackgroundTasks()
    tasks.add_task(start_background.append, True)
    start_failed.background = tasks

    async def fail_start(_message):
        raise RuntimeError("start failed")

    with pytest.raises(RuntimeError, match="start failed"):
        await start_failed(_scope(), receive, fail_start)
    replay = []
    await start_failed(_scope(), receive, replay.append)
    assert replay == []
    assert start_background == []

    start_cancelled = await new_response()

    async def cancel_start(_message):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await start_cancelled(_scope(), receive, cancel_start)
    replay = []
    await start_cancelled(_scope(), receive, replay.append)
    assert replay == []

    body_failed = await new_response()
    body_messages = []
    body_background = []
    tasks = BackgroundTasks()
    tasks.add_task(body_background.append, True)
    body_failed.background = tasks

    async def fail_body(message):
        body_messages.append(message)
        if message["type"] == "http.response.body":
            raise RuntimeError("body failed")

    with pytest.raises(RuntimeError, match="body failed"):
        await body_failed(_scope(), receive, fail_body)
    assert [message["type"] for message in body_messages] == [
        "http.response.start", "http.response.body",
    ]
    replay = []
    await body_failed(_scope(), receive, replay.append)
    assert replay == []
    assert body_background == []

    body_cancelled = await new_response()
    cancelled_body_background = []
    tasks = BackgroundTasks()
    tasks.add_task(cancelled_body_background.append, True)
    body_cancelled.background = tasks

    async def cancel_body(message):
        if message["type"] == "http.response.body":
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await body_cancelled(_scope(), receive, cancel_body)
    replay = []
    await body_cancelled(_scope(), receive, replay.append)
    assert replay == []
    assert cancelled_body_background == []

    background_cancelled = await new_response()
    cancelled_background_calls = []

    async def cancel_background():
        cancelled_background_calls.append(True)
        raise asyncio.CancelledError

    background_cancelled.background = cancel_background
    with pytest.raises(asyncio.CancelledError):
        await background_cancelled(_scope(), receive, send)
    await background_cancelled(_scope(), receive, send)
    assert cancelled_background_calls == [True]


@pytest.mark.asyncio
async def test_callback_reentry_and_concurrency_stop_before_native_egress(transport_app):
    support = _support()
    runtime = transport_app.state.request_runtime
    identity = support._identity(transport_app)
    body_gate = asyncio.Event()
    body_started = asyncio.Event()
    nested = []
    transport_holder = {}
    wire = support._wire(
        "harbour_context_manifest", {"request_id": "r." + "z" * 43}, 11,
    )

    async def block_outer(_index: int) -> None:
        body_started.set()
        await body_gate.wait()

    def authenticate(incoming: Request):
        if incoming.headers.get("x-reenter") == "1":
            nested.append(asyncio.create_task(transport_holder["transport"].handle(
                _request_from_chunks((wire,)),
            )))
        return identity

    transport = McpContextRequestTransport(runtime, authenticate, lambda _request: object())
    transport_holder["transport"] = transport
    outer = asyncio.create_task(transport.handle(_request_from_chunks(
        (wire,),
        headers=((b"x-reenter", b"1"),),
        before_receive=block_outer,
    )))
    await asyncio.wait_for(body_started.wait(), timeout=1.0)
    assert len(nested) == 1
    nested_response = await asyncio.wait_for(nested[0], timeout=1.0)
    _assert_content_free(nested_response, 503)

    concurrent_response = await transport.handle(_request_from_chunks((wire,)))
    _assert_content_free(concurrent_response, 503)
    body_gate.set()
    outer_response = await asyncio.wait_for(outer, timeout=1.0)
    _assert_content_free(outer_response, 403)
    transport.close()


def test_transport_stores_no_bearer_value_and_exports_only_inactive_api(transport_app):
    transport = transport_app.state.request_transport
    raw = transport_app.state.fixture["mcp_raw_tokens"][0]
    assert raw not in repr(transport)
    assert set(type(transport).__slots__) == {
        "_runtime",
        "_runtime_pin",
        "_authenticate",
        "_authenticate_pin",
        "_capture_prepare_proof",
        "_capture_prepare_proof_pin",
        "_body_timeout",
        "_body_timeout_pin",
        "_lifecycle",
        "_lifecycle_pin",
    }
    assert not hasattr(transport, "issue_token")
    assert not hasattr(transport, "register_route")


@pytest.mark.asyncio
async def test_content_rejection_real_handler_reports_preconversion_runtime_403_without_response_change(
    transport_app, tmp_path, monkeypatch,
):
    support = _support()
    runtime_module = transport_module._runtime_module
    authority_rows = tuple(
        row for row in transport_module._CONTENT_REJECTION_CODE_MAP
        if row[4] == "authority.publication_open"
    )
    assert authority_rows == ((
        runtime_module._ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_CODE,
        2227, 2505, "authority", "authority.publication_open",
    ),)
    lease_rows = tuple(
        row for row in transport_module._CONTENT_REJECTION_CODE_MAP
        if type(row[4]) is str and row[4].startswith("authority.lease_recheck.")
    )
    assert lease_rows == tuple(
        (runtime_module._TRUSTED_RECHECK_LEASE.__code__, first, stop, "authority", branch)
        for first, stop, branch in (
            (1600, 1602, "authority.lease_recheck.snapshot"),
            (1602, 1607, "authority.lease_recheck.state_lookup"),
            (1607, 1608, "authority.lease_recheck.cancelled_marker"),
            (1608, 1609, "authority.lease_recheck.missing"),
            (1609, 1611, "authority.lease_recheck.cancelled_row"),
            (1611, 1617, "authority.lease_recheck.clock_read"),
            (1617, 1619, "authority.lease_recheck.clock_validity"),
            (1619, 1622, "authority.lease_recheck.expiry"),
            (1622, 1624, "authority.lease_recheck.binding"),
            (1624, 1630, "authority.lease_recheck.propagation"),
        )
    )
    binding_rows = tuple(
        row for row in transport_module._CONTENT_REJECTION_CODE_MAP
        if type(row[4]) is str and row[4].startswith("authority.binding_recheck.")
    )
    assert binding_rows == tuple(
        (runtime_module._TRUSTED_RECHECK_BINDING.__code__, first, stop, "authority", branch)
        for first, stop, branch in (
            (1490, 1492, "authority.binding_recheck.snapshot"),
            (1492, 1500, "authority.binding_recheck.identity"),
            (1500, 1508, "authority.binding_recheck.proof_ownership"),
            (1508, 1510, "authority.binding_recheck.live_precheck"),
            (1510, 1511, "authority.binding_recheck.proof_shape"),
            (1511, 1512, "authority.binding_recheck.authority_state"),
            (1512, 1513, "authority.binding_recheck.live_postcheck"),
            (1513, 1526, "authority.binding_recheck.final_ownership"),
        )
    )

    class NativeAuthorityHarness:
        recheck_lease = runtime_module._TRUSTED_RECHECK_LEASE
        recheck_binding = runtime_module._TRUSTED_RECHECK_BINDING

        def __init__(self, lease):
            self._lock = threading.RLock()
            self._leases = {id(lease): [None, None, None]}
            self._cancelled_leases = {}

        @staticmethod
        def _lease_snapshot(_lease):
            return (None, None, 0.0, 1000.0, 0.0, 1000.0, None)

        @staticmethod
        def _binding_snapshot(_binding):
            raise McpContextAuthorityError("closed native body")

        @staticmethod
        def _monotonic():
            return 1.0

        @staticmethod
        def _wall_time():
            return 1.0

    lease = SimpleNamespace(binding=object())
    native_authority = NativeAuthorityHarness(lease)

    def publication_caller():
        native_authority.recheck_lease(lease, lambda: None)

    publication_caller.__code__ = publication_caller.__code__.replace(
        co_firstlineno=2425,
    )
    native_error = None
    try:
        publication_caller()
    except McpContextAuthorityError as exc:
        native_error = exc
    assert native_error is not None
    native_lines = {}
    traceback = native_error.__traceback__
    while traceback is not None:
        native_lines[traceback.tb_frame.f_code] = traceback.tb_lineno
        traceback = traceback.tb_next
    assert native_lines[publication_caller.__code__] == 2426
    assert native_lines[runtime_module._TRUSTED_RECHECK_LEASE.__code__] == 1622
    assert native_lines[runtime_module._TRUSTED_RECHECK_BINDING.__code__] == 1491
    native_map = ((
        publication_caller.__code__, 2227, 2505,
        "authority", "authority.publication_open",
    ), *lease_rows, *binding_rows)
    assert causal_observation._native_exception_projection(
        native_error, native_map,
    )[:4] == (
        "exact", "authority", "authority.binding_recheck.snapshot",
        "authority.binding_recheck.snapshot.L1491",
    )

    class PinnedRuntimeBoundary(RuntimeError):
        pass

    def transparent_boundary():
        try:
            publication_caller()
        except McpContextAuthorityError:
            raise PinnedRuntimeBoundary() from None

    wrapped_native_error = None
    try:
        transparent_boundary()
    except PinnedRuntimeBoundary as exc:
        wrapped_native_error = exc
    assert wrapped_native_error is not None
    wrapper_traceback = wrapped_native_error.__traceback__
    while wrapper_traceback.tb_frame.f_code is not transparent_boundary.__code__:
        wrapper_traceback = wrapper_traceback.tb_next
    transparent_row = (
        "transparent_wrapper", PinnedRuntimeBoundary,
        transparent_boundary.__code__, wrapper_traceback.tb_lineno,
        wrapper_traceback.tb_lineno + 1,
    )
    assert causal_observation._native_exception_projection(
        wrapped_native_error, (*native_map, transparent_row),
    )[:4] == (
        "exact", "authority", "authority.binding_recheck.snapshot",
        "authority.binding_recheck.snapshot.L1491",
    )

    generator_anchor = runtime_module._ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED

    def mapped_runtime_caller():
        publication = generator_anchor(*(object() for _index in range(6)))
        next(publication)

    mapped_runtime_caller.__code__ = mapped_runtime_caller.__code__.replace(
        co_firstlineno=3213,
    )
    authority_error = None
    try:
        mapped_runtime_caller()
    except McpContextAuthorityError as exc:
        authority_error = exc
    assert authority_error is not None
    traceback = authority_error.__traceback__
    runtime_line = None
    authority_line = None
    while traceback is not None:
        if traceback.tb_frame.f_code is mapped_runtime_caller.__code__:
            runtime_line = traceback.tb_lineno
        if traceback.tb_frame.f_code is authority_rows[0][0]:
            authority_line = traceback.tb_lineno
        traceback = traceback.tb_next
    assert runtime_line == 3215
    assert type(authority_line) is int and 2227 <= authority_line < 2505
    runtime_row = (
        mapped_runtime_caller.__code__, 3215, 3234,
        "runtime", "runtime.publication_open",
    )
    assert causal_observation._native_exception_projection(
        authority_error, (runtime_row, authority_rows[0]),
    )[:4] == (
        "exact", "authority", "authority.publication_open",
        "authority.publication_open.L" + str(authority_line),
    )
    wrong_authority_row = (
        runtime_module._TRUSTED_OPEN_CONTENT_PUBLICATION.__code__,
        2227, 2505, "authority", "authority.publication_open",
    )
    assert wrong_authority_row[0] is not authority_rows[0][0]
    assert causal_observation._native_exception_projection(
        authority_error, (runtime_row, wrong_authority_row),
    )[:4] == (
        "exact", "runtime", "runtime.publication_open",
        "runtime.publication_open.L3215",
    )
    monkeypatch.setattr(causal_observation, "_content_state", None)
    target = tmp_path / causal_observation.CONTENT_REJECTION_BASENAME
    assert causal_observation.arm_content_rejection_observation(str(target))
    prepared = await transport_app.state.request_transport.handle(
        _request_from_chunks((support._wire(
            "harbour_context_prepare",
            {
                "chat_session_id": "chat-owned",
                "selection": support._selection_wire(transport_app),
                "page_bytes": 16_384,
            },
            90,
        ),)),
    )
    assert prepared.status_code == 200
    request_id = json.loads(prepared.body)["result"]["request_id"]
    assert causal_observation.begin_content_rejection_observation(1, "forge")
    wire = support._wire(
        "harbour_context_page", {"request_id": request_id}, 91,
    )
    response = await transport_app.state.request_transport.handle(
        _request_from_chunks((wire,)),
    )
    _assert_content_free(response, 403)
    assert causal_observation.end_content_rejection_observation(response.status_code)
    projection = causal_observation.read_content_rejection_projection(target)
    assert projection["route_class"] == "runtime_exception_conversion"
    assert projection["inner_map_status"] == "exact"
    assert projection["release_scope"] == "observer_disarmed"


@pytest.mark.asyncio
async def test_content_rejection_observer_failure_leaves_real_success_response_identical(
    transport_app, tmp_path, monkeypatch,
):
    support = _support()
    monkeypatch.setattr(causal_observation, "_content_state", None)
    target = tmp_path / causal_observation.CONTENT_REJECTION_BASENAME
    target.write_bytes(b"collision")
    assert not causal_observation.arm_content_rejection_observation(str(target))
    wire = support._wire(
        "harbour_context_prepare",
        {
            "chat_session_id": "chat-owned",
            "selection": support._selection_wire(transport_app),
            "page_bytes": 16_384,
        },
        92,
    )
    response = await transport_app.state.request_transport.handle(
        _request_from_chunks((wire,)),
    )
    assert response.status_code == 200
    assert set(json.loads(response.body)) == {"jsonrpc", "id", "result"}
    assert target.read_bytes() == b"collision"
