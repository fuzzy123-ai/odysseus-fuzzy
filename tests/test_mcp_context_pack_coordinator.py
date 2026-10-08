"""Outcome tests for the inactive mixed MCP context pack coordinator."""
from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import json
import sys
import threading
from types import SimpleNamespace

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
import pytest

from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.mcp_context_cursor_authority import McpContextCursorAuthority
import src.mcp_private_context_confirmation as confirmation_module
from src.mcp_context_pack_coordinator import (
    MCP_CONTEXT_PACK_ERROR,
    McpContextPackCoordinator,
    McpContextPackCoordinatorError,
    _parse_inner,
)
import src.mcp_context_pack_coordinator as coordinator_module
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    SourceSelection,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.knowledge import project_harbour_knowledge_index_records
from tests.test_app_mcp_private_context_confirmation import consent_app
from tests.test_mcp_context_native_page import _authorize
from tests.test_mcp_private_context_confirmation import (
    _entry_fully_scrubbed,
    _entry_snapshot,
)


def _install_mixed_fixture_inputs(monkeypatch):
    """Extend the real persisted fixture to ten targets before native capture."""
    import tests.test_app_mcp_private_context_confirmation as consent_tests
    import tests.test_mcp_context_metadata as metadata_tests

    original_fixture = consent_tests.native_mcp_fixture
    original_create = HarbourKnowledgeStore.create_object
    original_authority = metadata_tests.McpContextAuthority
    authority_clock = {"value": None}

    def clock_value():
        clock = authority_clock["value"]
        assert type(clock) is dict and set(clock) == {"wall", "mono"}
        return clock

    def build_authority(*args, **kwargs):
        assert "monotonic" not in kwargs and "wall_time" not in kwargs
        return original_authority(
            *args, **kwargs,
            monotonic=lambda: clock_value()["mono"],
            wall_time=lambda: clock_value()["wall"].timestamp(),
        )

    def fixture_with_mixed_inputs(tmp_path, fixture_monkeypatch):
        fixture_body = None
        with fixture_monkeypatch.context() as input_patch:
            def create_object(store, principal, *args, **kwargs):
                result = original_create(store, principal, *args, **kwargs)
                if kwargs.get("knowledge_id") == "active-note":
                    for index in (1, 2):
                        extra = dict(kwargs)
                        extra.update({
                            "knowledge_id": f"active-note-{index}",
                            "payload_bytes": f"additional mixed evidence {index}".encode("utf-8"),
                            "idempotency_key": f"active-note-{index}-create",
                        })
                        original_create(store, principal, *args, **extra)
                return result

            input_patch.setattr(HarbourKnowledgeStore, "create_object", create_object)
            input_patch.setattr(metadata_tests, "McpContextAuthority", build_authority)
            fixture_body = original_fixture.__wrapped__(tmp_path, fixture_monkeypatch)
            fixture = next(fixture_body)
        try:
            with CookieConfirmationAuthority(fixture["auth"]).hold(
                fixture["cookie"], fixture["chat_id"],
            ) as binding:
                owner_sid, store, principal, *_rest = (
                    fixture["knowledge_composition"]._context_runtime(binding)
                )
                snapshot = store.capture_index_snapshot(principal, owner_id=owner_sid)
                knowledge: dict[tuple[str, str], SourceSelection] = {}
                for record in snapshot.records:
                    projected = project_harbour_knowledge_index_records(
                        OwnerScope.for_subject_id(owner_sid).value, record,
                    )
                    target = SourceSelection(
                        "knowledge", projected.source.source_id,
                        projected.source_version.source_version_id,
                    )
                    knowledge[(target.selection_ref, target.revision_ref)] = target
            retained = tuple(
                item for item in fixture["selection"].items if item.source != "knowledge"
            )
            forge = tuple(item for item in retained if item.source == "forge")
            planning = tuple(item for item in retained if item.source == "planning")
            targets = (*forge, *planning, *knowledge.values())
            selection = ContextSelection(tuple(sorted(
                targets,
                key=lambda item: (item.source, item.selection_ref, item.revision_ref),
            )))
            assert len(selection.items) > 8
            assert {item.source for item in selection.items} == {"forge", "planning", "knowledge"}
            assert tuple(dict.fromkeys(item.source for item in selection.items)) == (
                "forge", "knowledge", "planning",
            )
            fixture["selection"] = selection
            yield fixture
        finally:
            if fixture_body is not None:
                try:
                    next(fixture_body)
                except StopIteration:
                    pass

    monkeypatch.setattr(
        consent_tests,
        "native_mcp_fixture",
        SimpleNamespace(__wrapped__=fixture_with_mixed_inputs),
    )

    def bind_authority_clock(clock):
        assert authority_clock["value"] is None
        assert type(clock) is dict and set(clock) == {"wall", "mono"}
        authority_clock["value"] = clock

    return bind_authority_clock


def _install_routes(app, *, cursor_wall_time=None) -> None:
    @app.exception_handler(McpContextPackCoordinatorError)
    async def pack_error(_request, exc):
        return JSONResponse(status_code=403, content={"error": exc.code})

    def coordinator(request: Request) -> McpContextPackCoordinator:
        if not hasattr(request.app.state, "pack_cursor"):
            authority = request.app.state.fixture["authority"]
            request.app.state.pack_cursor = (
                McpContextCursorAuthority(authority)
                if cursor_wall_time is None
                else McpContextCursorAuthority(authority, wall_time=cursor_wall_time)
            )
            request.app.state.pack_coordinator = McpContextPackCoordinator(
                request.app.state.service,
                request.app.state.fixture["combined"],
                request.app.state.pack_cursor,
            )
        return request.app.state.pack_coordinator

    @app.post("/test/context-pack")
    async def context_pack(request: Request):
        body = await request.json()
        publish = coordinator(request)
        values = (
            request.app.state.lease,
            request.app.state.fixture["selection"],
            request.app.state.request_id,
            body.get("rpc_id", 17),
            body.get("page_bytes", 16_384),
            body.get("cursor"),
        )
        profile = getattr(request.app.state, "pack_profile", None)
        previous_profile = sys.getprofile()
        if profile is not None:
            sys.setprofile(profile)
        try:
            if body.get("kind") == "manifest":
                encoded = publish.publish_manifest(*values)
            elif body.get("kind") == "content":
                encoded = publish.publish_content(*values)
            else:
                raise McpContextPackCoordinatorError()
        finally:
            if profile is not None:
                sys.setprofile(previous_profile)
                request.app.state.pack_profile_restored = sys.getprofile() is previous_profile
        return Response(encoded, media_type="application/json")

    @app.post("/test/context-pack-cancel")
    async def context_pack_cancel(request: Request):
        coordinator(request).cancel(request.app.state.request_id)
        return {"cancelled": True}


def _post(client: TestClient, kind: str, cursor=None, *, page_bytes: int = 16_384):
    response = client.post(
        "/test/context-pack",
        json={"kind": kind, "cursor": cursor, "page_bytes": page_bytes, "rpc_id": 17},
    )
    if response.status_code != 200:
        payload = response.json()
        code = payload.get("error") if type(payload) is dict else None
        pytest.fail(f"unexpected pack response:{code if code == MCP_CONTEXT_PACK_ERROR else 'closed'}")
    payload = response.json()
    assert type(payload) is dict and set(payload) == {"jsonrpc", "id", "result"}
    assert payload["jsonrpc"] == "2.0" and payload["id"] == 17
    return payload["result"]


def _assert_closed_result(result, *, kind: str) -> None:
    common = {
        "schema", "pack_id", "complete", "read_only", "authority_granted",
        "send_performed",
    }
    expected = common | ({"items", "manifest_cursor"} if kind == "manifest"
                         else {"page", "content_cursor"})
    assert set(result) == expected
    assert result["schema"] == "odysseus.mcp.context_pack.v1"
    assert result["read_only"] is True
    assert result["authority_granted"] is False
    assert result["send_performed"] is False


@pytest.mark.parametrize("consent_app", [True], indirect=True)
def test_real_persisted_mixed_pack_paginates_every_target_and_finally_revokes(
    consent_app, monkeypatch,
):
    original_issue_read_lease = confirmation_module.McpContextAuthority.issue_read_lease

    def issue_read_lease(authority, binding, request_id, *, ttl_seconds=900):
        return original_issue_read_lease(
            authority, binding, request_id, ttl_seconds=ttl_seconds,
        )

    monkeypatch.setattr(
        confirmation_module.McpContextAuthority, "issue_read_lease", issue_read_lease,
    )
    bind_authority_clock = _install_mixed_fixture_inputs(monkeypatch)
    _install_routes(
        consent_app,
        cursor_wall_time=lambda: consent_app.state.clock["wall"].timestamp(),
    )
    with TestClient(consent_app) as client:
        bind_authority_clock(consent_app.state.clock)
        request_id = _authorize(client)
        fixture = consent_app.state.fixture
        service = consent_app.state.service
        selection = fixture["selection"]
        lease = consent_app.state.lease
        issued_mono = object.__getattribute__(
            lease, "_McpContextReadLease__issued_monotonic",
        )
        deadline_mono = object.__getattribute__(
            lease, "_McpContextReadLease__deadline_monotonic",
        )
        issued_wall = object.__getattribute__(lease, "_McpContextReadLease__issued_wall")
        deadline_wall = object.__getattribute__(lease, "_McpContextReadLease__deadline_wall")
        assert deadline_mono - issued_mono == 900.0
        assert deadline_wall - issued_wall == 900.0
        entry = service._entries[request_id]
        original_deadlines = (
            entry.deadline, entry.mono_deadline, entry.ack_deadline, entry.ack_mono_deadline,
        )
        snapshot = fixture["combined"].capture_mcp_snapshot(
            consent_app.state.lease, selection,
        )
        selection_keys = tuple(
            (item.source, item.selection_ref, item.revision_ref) for item in selection.items
        )
        snapshot_keys = tuple(
            (item.source, item.selection_ref, item.revision_ref) for item in snapshot.items
        )
        assert len(selection_keys) > 8
        assert snapshot_keys != selection_keys
        knowledge_index = next(
            index for index, item in enumerate(selection.items) if item.source == "knowledge"
        )
        cookie_target = entry.cookie_snapshot.target_manifest_digests[knowledge_index]
        mcp_target = entry.mcp_snapshot.target_manifest_digests[knowledge_index]
        assert (type(cookie_target) is str and len(cookie_target) == 71
                and cookie_target[:7] == "sha256:"
                and all(ch in "0123456789abcdef" for ch in cookie_target[7:]))
        assert (type(mcp_target) is str and len(mcp_target) == 64
                and all(ch in "0123456789abcdef" for ch in mcp_target))
        assert cookie_target != mcp_target
        cookie_projection = confirmation_module._service_snapshot_projection(
            entry.cookie_snapshot, require_metadata=False,
        )
        assert cookie_projection[1] is entry.cookie_snapshot.target_manifest_digests

        manifest_items = []
        manifest_cursor = None
        pack_id = None
        while True:
            result = _post(client, "manifest", manifest_cursor)
            _assert_closed_result(result, kind="manifest")
            pack_id = result["pack_id"] if pack_id is None else pack_id
            assert result["pack_id"] == pack_id
            manifest_items.extend(result["items"])
            assert (entry.deadline, entry.mono_deadline, entry.ack_deadline,
                    entry.ack_mono_deadline) == original_deadlines
            if result["complete"]:
                assert result["manifest_cursor"] is None
                break
            manifest_cursor = result["manifest_cursor"]
        assert len(manifest_items) == len(selection.items)
        assert tuple((item["source"], item["selection_ref"], item["revision_ref"])
                     for item in manifest_items) == selection_keys
        assert service._entries[request_id] is entry

        content_cursor = None
        visited = []
        pages = 0
        while True:
            result = _post(client, "content", content_cursor)
            _assert_closed_result(result, kind="content")
            assert result["pack_id"] == pack_id
            page = result["page"]
            assert set(page) == {
                "target_index", "offset", "end", "total", "whole_sha256",
                "page_sha256", "byteform", "content_b64",
            }
            raw = base64.b64decode(page["content_b64"], validate=True)
            assert page["page_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
            visited.append(selection.items[page["target_index"]].source)
            pages += 1
            if result["complete"]:
                assert result["content_cursor"] is None
                break
            assert (entry.deadline, entry.mono_deadline, entry.ack_deadline,
                    entry.ack_mono_deadline) == original_deadlines
            content_cursor = result["content_cursor"]
        assert pages >= len(selection.items)
        assert set(visited) == {"forge", "planning", "knowledge"}
        assert request_id not in service._entries
        consent_app.state.pack_coordinator.close()
        consent_app.state.pack_cursor.close()


@pytest.mark.parametrize("malformed_target", ["g" * 64, "sha256:" + "g" * 64])
def test_malformed_cookie_manifest_target_is_context_stale_without_bytes_and_scrubs(
    consent_app, malformed_target,
):
    @consent_app.post("/test/context-pack-cookie-target-fence")
    async def cookie_target_fence(request: Request):
        service = request.app.state.service
        entry = service._entries[request.app.state.request_id]
        service._fence_disclosed_publication(
            request.app.state.request_id,
            request.app.state.lease,
            request.app.state.fixture["selection"],
            entry.mcp_snapshot,
            page_bytes=entry.page_bytes,
        )
        return {"unexpected": True}

    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = consent_app.state.service
        entry = service._entries[request_id]
        before = _entry_snapshot(entry)
        targets = list(entry.cookie_snapshot.target_manifest_digests)
        knowledge_index = next(
            index for index, item in enumerate(entry.selection.items)
            if item.source == "knowledge"
        )
        targets[knowledge_index] = malformed_target
        object.__setattr__(entry.cookie_snapshot, "target_manifest_digests", tuple(targets))
        with pytest.raises(confirmation_module.McpPrivateContextConfirmationError) as error:
            confirmation_module._service_snapshot_projection(
                entry.cookie_snapshot, require_metadata=False,
            )
        assert error.value.code == "context_stale"

        response = client.post("/test/context-pack-cookie-target-fence")

        assert response.status_code == 403
        assert response.content == b'{"error":"context_stale"}'
        assert b'"result"' not in response.content and b'"content_b64"' not in response.content
        assert request_id not in service._entries
        assert _entry_fully_scrubbed(entry, before)


@pytest.mark.parametrize("consent_app", [True], indirect=True)
@pytest.mark.parametrize("drift", ["ack", "expiry"])
@pytest.mark.parametrize("phase", ["postpublisher", "postencoder"])
def test_post_publication_deadline_drift_returns_no_outer_bytes_and_scrubs(
    consent_app, monkeypatch, drift, phase,
):
    _install_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = consent_app.state.service
        calls = {"disclosed": 0}
        trigger = {"postpublisher": 2, "postencoder": 4}[phase]
        trusted_encoder = coordinator_module._TRUSTED_ENCODER
        assert coordinator_module._contract_module.encode_context_jsonrpc_response is trusted_encoder
        assert coordinator_module.encode_context_jsonrpc_response is trusted_encoder

        def hook():
            entry = service._entries.get(request_id)
            if entry is None or entry.state != "disclosed":
                return
            calls["disclosed"] += 1
            if calls["disclosed"] != trigger:
                return
            if phase == "postencoder":
                # Count four is the first clock in the second publication fence:
                # the real outer encoder and the complete first fence have returned.
                assert coordinator_module._contract_module.encode_context_jsonrpc_response is trusted_encoder
                assert coordinator_module.encode_context_jsonrpc_response is trusted_encoder
            consent_app.state.wall_clock_hook = None
            if drift == "ack":
                entry.ack_deadline = entry.issued
            else:
                consent_app.state.clock["wall"] = entry.deadline + timedelta(seconds=1)
                consent_app.state.clock["mono"] = entry.mono_deadline + 1.0

        consent_app.state.wall_clock_hook = hook
        response = client.post(
            "/test/context-pack",
            json={"kind": "content", "cursor": None, "page_bytes": 16_384, "rpc_id": 17},
        )
        assert response.status_code == 403
        assert response.content == b'{"error":"context_pack_unavailable"}'
        assert response.json() == {"error": MCP_CONTEXT_PACK_ERROR}
        assert calls["disclosed"] == trigger
        assert coordinator_module._contract_module.encode_context_jsonrpc_response is trusted_encoder
        assert coordinator_module.encode_context_jsonrpc_response is trusted_encoder
        assert request_id not in service._entries
        consent_app.state.pack_coordinator.close()
        consent_app.state.pack_cursor.close()


@pytest.mark.parametrize("consent_app", [True], indirect=True)
@pytest.mark.parametrize("action", ["close", "cancel"])
def test_postencoder_inflight_close_or_cancel_scrubs_without_closing_borrowed_dependencies(
    consent_app, action,
):
    _install_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = consent_app.state.service
        producer = consent_app.state.fixture["combined"]
        authority = consent_app.state.fixture["authority"]
        trusted_encoder = coordinator_module._TRUSTED_ENCODER
        calls = {"disclosed": 0}
        triggered = threading.Event()
        completed = threading.Event()
        statuses = []
        workers = []

        def hook():
            entry = service._entries.get(request_id)
            if entry is None or entry.state != "disclosed":
                return
            calls["disclosed"] += 1
            if calls["disclosed"] != 4:
                return
            # The second fence can only begin after the real outer encoder returns.
            assert coordinator_module._contract_module.encode_context_jsonrpc_response is trusted_encoder
            assert coordinator_module.encode_context_jsonrpc_response is trusted_encoder
            consent_app.state.wall_clock_hook = None
            consent_app.state.pack_coordinator.close()

        def cancel_request():
            try:
                consent_app.state.pack_coordinator.cancel(request_id)
                statuses.append("cancelled")
            except BaseException:
                statuses.append("failed")
            finally:
                completed.set()

        finish_code = coordinator_module._TRUSTED_COORDINATOR_FINISH.__code__

        def profile(frame, event, _arg):
            if event == "call" and frame.f_code is finish_code and not triggered.is_set():
                triggered.set()
                worker = threading.Thread(target=cancel_request)
                workers.append(worker)
                worker.start()
                worker.join(timeout=5.0)
                if worker.is_alive():
                    statuses.append("timeout")
            return profile

        if action == "close":
            consent_app.state.wall_clock_hook = hook
        else:
            consent_app.state.pack_profile = profile
        response = client.post(
            "/test/context-pack",
            json={"kind": "content", "cursor": None, "page_bytes": 16_384, "rpc_id": 17},
        )
        coordinator = consent_app.state.pack_coordinator
        cursor = consent_app.state.pack_cursor
        borrowed = (
            coordinator._service, coordinator._producer, coordinator._cursor,
            service._authority, producer._mcp_authority, cursor._authority,
        )
        assert response.status_code == 403
        assert response.content == b'{"error":"context_pack_unavailable"}'
        assert response.json() == {"error": MCP_CONTEXT_PACK_ERROR}
        if action == "close":
            assert calls["disclosed"] == 4
        else:
            assert triggered.is_set() and completed.is_set()
            assert len(workers) == 1 and not workers[0].is_alive()
            assert statuses == ["cancelled"]
            assert consent_app.state.pack_profile_restored is True
        assert request_id not in service._entries
        assert all(current is expected for current, expected in zip(
            borrowed, (service, producer, cursor, authority, authority, authority), strict=True,
        ))
        assert service._closed is False and cursor._closed is False
        assert coordinator_module._contract_module.encode_context_jsonrpc_response is trusted_encoder
        assert coordinator_module.encode_context_jsonrpc_response is trusted_encoder
        coordinator.close()
        cursor.close()


@pytest.mark.parametrize("guard", ["coordinator", "service"])
def test_hostile_getattribute_descriptor_is_denied_and_scrubbed_without_running(
    consent_app, monkeypatch, guard,
):
    _install_routes(consent_app)
    if guard == "service":
        @consent_app.post("/test/context-pack-service-fence")
        async def service_fence(request: Request):
            service = request.app.state.service
            request_id = request.app.state.request_id
            entry = service._entries[request_id]
            body = await request.json()
            target_request_id = request_id if body.get("genuine") is True else "foreign-" + request_id
            service._fence_disclosed_publication(
                target_request_id,
                request.app.state.lease,
                request.app.state.fixture["selection"],
                entry.mcp_snapshot,
                page_bytes=entry.page_bytes,
            )
            return {"unexpected": True}

    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = consent_app.state.service
        if guard == "coordinator":
            consent_app.state.pack_cursor = McpContextCursorAuthority(
                consent_app.state.fixture["authority"],
            )
            consent_app.state.pack_coordinator = McpContextPackCoordinator(
                service, consent_app.state.fixture["combined"], consent_app.state.pack_cursor,
            )
        calls = []

        class HostileDescriptor:
            def __get__(self, *_args, **_kwargs):
                calls.append("descriptor")
                raise AssertionError("hostile descriptor getter ran")

        class HostileState:
            def __eq__(self, _other):
                calls.append("state-eq")
                raise AssertionError("hostile state comparison ran")

            def __ne__(self, _other):
                calls.append("state-ne")
                raise AssertionError("hostile state comparison ran")

        if guard == "service":
            retained_entry = service._entries[request_id]
            retained_maps = (
                service._entries, service._permits, service._exchanges,
                service._completions, service._native_completions,
            )
            foreign = client.post(
                "/test/context-pack-service-fence", json={"genuine": False},
            )
            assert foreign.status_code == 403
            assert foreign.content == b'{"error":"context_stale"}'
            assert foreign.json() == {"error": "context_stale"}
            assert service._entries.get(request_id) is retained_entry
            object.__setattr__(retained_entry, "state", HostileState())
            try:
                unproved = client.post(
                    "/test/context-pack-service-fence", json={"genuine": True},
                )
            finally:
                object.__setattr__(retained_entry, "state", "disclosed")
            assert unproved.status_code == 403
            assert unproved.content == b'{"error":"context_stale"}'
            assert unproved.json() == {"error": "context_stale"}
            assert calls == []
            assert service._entries.get(request_id) is retained_entry
            assert all(current is retained for current, retained in zip(
                (service._entries, service._permits, service._exchanges,
                 service._completions, service._native_completions),
                retained_maps, strict=True,
            ))

        with monkeypatch.context() as attack:
            attack.setattr(ContextSelection, "__getattribute__", HostileDescriptor())
            if guard == "coordinator":
                response = client.post(
                    "/test/context-pack",
                    json={"kind": "content", "cursor": None,
                          "page_bytes": 16_384, "rpc_id": 17},
                )
            else:
                response = client.post(
                    "/test/context-pack-service-fence", json={"genuine": True},
                )
        expected = MCP_CONTEXT_PACK_ERROR if guard == "coordinator" else "context_stale"
        assert response.status_code == 403
        assert response.content == ('{"error":"' + expected + '"}').encode("ascii")
        assert response.json() == {"error": expected}
        assert calls == []
        assert request_id not in service._entries
        if guard == "coordinator":
            consent_app.state.pack_coordinator.close()
            consent_app.state.pack_cursor.close()


def test_service_initial_state_capture_rejects_hostile_field_descriptor_without_running(
    monkeypatch,
):
    calls = []

    class HostileDescriptor:
        def __get__(self, *_args, **_kwargs):
            calls.append("descriptor")
            raise AssertionError("hostile field descriptor getter ran")

    with monkeypatch.context() as attack:
        attack.setattr(ContextSelection, "items", HostileDescriptor())
        with pytest.raises(confirmation_module.McpPrivateContextConfirmationError) as error:
            confirmation_module._value_class_state(ContextSelection, ("items",))
    assert error.value.code == "context_stale"
    assert calls == []


@pytest.mark.parametrize(
    "mode", ["publisher", "encoder", "selection_scalar", "coordinator_guard"],
)
def test_substituted_publisher_encoder_scalar_or_guard_revokes_without_outer_bytes(
    consent_app, monkeypatch, mode,
):
    _install_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = consent_app.state.service
        consent_app.state.pack_cursor = McpContextCursorAuthority(
            consent_app.state.fixture["authority"],
        )
        consent_app.state.pack_coordinator = McpContextPackCoordinator(
            service, consent_app.state.fixture["combined"], consent_app.state.pack_cursor,
        )
        with monkeypatch.context() as attack:
            if mode == "publisher":
                def fake(*_args, **_kwargs):
                    pytest.fail("substituted publisher ran")
                attack.setattr(coordinator_module._native_module, "publish_forge_page", fake)
            elif mode == "encoder":
                def fake(*_args, **_kwargs):
                    pytest.fail("substituted encoder ran")
                attack.setattr(coordinator_module._contract_module,
                               "encode_context_jsonrpc_response", fake)
            elif mode == "selection_scalar":
                target = consent_app.state.fixture["selection"].items[0]
                object.__setattr__(target, "source", str.__new__(type("S", (str,), {}), target.source))
            else:
                def fake(*_args, **_kwargs):
                    pytest.fail("substituted coordinator guard ran")
                attack.setattr(McpContextPackCoordinator, "_begin", fake)
            response = client.post(
                "/test/context-pack",
                json={"kind": "content", "cursor": None, "page_bytes": 16_384, "rpc_id": 17},
            )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_PACK_ERROR}
        assert request_id not in service._entries
        if hasattr(consent_app.state, "pack_coordinator"):
            consent_app.state.pack_coordinator.close()
        if hasattr(consent_app.state, "pack_cursor"):
            consent_app.state.pack_cursor.close()


@pytest.mark.parametrize("case", [
    "duplicate", "trailing", "wrong_id", "wrong_schema", "bad_hash", "bad_base64",
])
def test_closed_inner_frame_rejects_malformed_or_misbinding_bytes(case):
    payload = b"page"
    result = {
        "schema": "odysseus.mcp.context_forge_page.v1",
        "target_index": 0,
        "offset": 0,
        "end": len(payload),
        "total_bytes": len(payload),
        "whole_sha256": "sha256:" + "a" * 64,
        "page_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "manifest_digest": "b" * 64,
        "byteform": "git_blob",
        "content_b64": base64.b64encode(payload).decode("ascii"),
    }
    document = {"jsonrpc": "2.0", "id": 17, "result": result}
    if case == "duplicate":
        raw = b'{"jsonrpc":"2.0","id":17,"id":17,"result":{}}'
    elif case == "trailing":
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"x"
    else:
        if case == "wrong_id":
            document["id"] = 18
        elif case == "wrong_schema":
            result["schema"] = "odysseus.mcp.context_knowledge_page.v1"
        elif case == "bad_hash":
            result["page_sha256"] = "sha256:" + "0" * 64
        else:
            result["content_b64"] = "@@"
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(McpContextPackCoordinatorError) as error:
        _parse_inner(
            raw, source="forge", rpc_request_id=17, target_index=0, offset=0,
            expected_item={"payload_sha256": "a" * 64}, page_bytes=16_384,
        )
    assert error.value.code == MCP_CONTEXT_PACK_ERROR


def test_cancel_and_local_close_do_not_close_or_replace_borrowed_dependencies(consent_app):
    _install_routes(consent_app)
    with TestClient(consent_app) as client:
        first = _authorize(client)
        result = _post(client, "manifest")
        assert result["complete"] in {True, False}
        coordinator = consent_app.state.pack_coordinator
        cursor = consent_app.state.pack_cursor
        service = consent_app.state.service
        producer = consent_app.state.fixture["combined"]
        authority = consent_app.state.fixture["authority"]
        response = client.post("/test/context-pack-cancel")
        assert response.status_code == 200 and first not in service._entries
        borrowed = (coordinator._service, coordinator._producer, coordinator._cursor,
                    service._authority, producer._mcp_authority, cursor._authority)
        coordinator.close()
        coordinator.close()
        assert borrowed == (service, producer, cursor, authority, authority, authority)
        assert service._closed is False and cursor._closed is False
        cursor.close()

