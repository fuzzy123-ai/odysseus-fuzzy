"""Outcome tests for the inactive MCP context request runtime."""
from __future__ import annotations

import json
import sys
import threading
from types import CodeType, FrameType, SimpleNamespace, TracebackType

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
import pytest

from routes.auth_routes import SESSION_COOKIE
from src.context_manifest_producer import (
    CombinedContextManifestProducer,
    ContextManifestProducerError,
)
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity, McpBearerProof
from src.mcp_context_authority import McpContextAuthorityError, issue_mcp_bearer_proof
from src.mcp_context_cursor_authority import McpContextCursorAuthority
from src.mcp_context_pack_coordinator import (
    McpContextPackCoordinator,
    McpContextPackCoordinatorError,
)
import src.mcp_context_contract as contract_module
import src.mcp_context_authority as authority_module
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
    PreparedRequest,
)
from src.mcp_context_request_runtime import (
    MCP_CONTEXT_REQUEST_ERROR,
    McpContextRequestRuntime,
    McpContextRequestRuntimeError,
)


_BROWSER_HEADERS = {
    "Origin": "http://testserver",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "",
}


def _wire(method: str, params: dict[str, object], rpc_id: str | int = 17) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity(app, *, owner_alias: str = "fuzzy") -> McpAuthenticatedIdentity:
    fixture = app.state.fixture
    return McpAuthenticatedIdentity(
        "mcp-metadata-token",
        owner_alias,
        fixture["auth"].subject_id_for_username("fuzzy"),
        fixture["mcp_state"]._token_cache_generation,
        fixture["auth"].security_action_revision(),
        "mcp:harbour:context:read",
    )


def _ensure_runtime(app) -> McpContextRequestRuntime:
    if not hasattr(app.state, "request_runtime"):
        fixture = app.state.fixture
        app.state.bind_request_authority_clock(app.state.clock)
        cursor = McpContextCursorAuthority(
            fixture["authority"],
            wall_time=lambda: app.state.clock["wall"].timestamp(),
        )
        coordinator = McpContextPackCoordinator(
            app.state.service, fixture["combined"], cursor,
        )
        app.state.request_cursor = cursor
        app.state.request_coordinator = coordinator
        app.state.request_runtime = McpContextRequestRuntime(
            fixture["authority"], app.state.service, fixture["combined"],
            cursor, coordinator,
        )
    return app.state.request_runtime


@pytest.fixture
def runtime_app(tmp_path, monkeypatch, request):
    # Import the established persisted-browser fixture lazily.  Importing it at
    # module load creates a circular fixture collection path.
    from tests.test_app_mcp_private_context_confirmation import consent_app
    from tests.test_context_manifest_producer import _compact_planning_fixture_root
    import tests.test_mcp_context_metadata as metadata_tests

    genuine_authority = metadata_tests.McpContextAuthority
    authority_clock = {"value": None}

    def clock_value():
        clock = authority_clock["value"]
        assert type(clock) is dict and set(clock) == {"wall", "mono"}
        return clock

    def build_authority(*args, **kwargs):
        assert "monotonic" not in kwargs and "wall_time" not in kwargs
        return genuine_authority(
            *args,
            **kwargs,
            monotonic=lambda: clock_value()["mono"],
            wall_time=lambda: clock_value()["wall"].timestamp(),
        )

    monkeypatch.setattr(metadata_tests, "McpContextAuthority", build_authority)

    planning_repo_root = _compact_planning_fixture_root(
        tmp_path / "planning-repository",
    )
    application = consent_app.__wrapped__(
        tmp_path,
        monkeypatch,
        SimpleNamespace(param=True),
        planning_repo_root,
    )

    def bind_authority_clock(clock):
        assert type(clock) is dict and set(clock) == {"wall", "mono"}
        if authority_clock["value"] is None:
            authority_clock["value"] = clock
        else:
            assert authority_clock["value"] is clock

    application.state.bind_request_authority_clock = bind_authority_clock

    @application.exception_handler(McpContextRequestRuntimeError)
    async def runtime_error(_request, exc):
        return JSONResponse(status_code=403, content={"error": exc.code})

    @application.post("/test/context-request-runtime")
    async def context_request_runtime(request: Request):
        raw = await request.body()
        runtime = _ensure_runtime(request.app)
        identity = _identity(
            request.app,
            owner_alias=request.headers.get("x-owner-alias", "fuzzy"),
        )
        proof = (
            issue_mcp_bearer_proof(request.app.state.fixture["mcp_raw_tokens"][0])
            if request.headers.get("x-bearer-proof") == "1"
            else None
        )
        captured: list[bytes] = []
        reentry_codes: list[str] = []
        target_code = McpContextPackCoordinator.publish_content.__code__
        prepare_code = McpPrivateContextConfirmationService.prepare_intent.__code__
        old_profile = sys.getprofile()
        capture = request.headers.get("x-capture-identity") == "1"
        force_profile_identity = (
            request.headers.get("x-force-profile-identity") == "1"
        )
        reentry = request.headers.get("x-reenter") == "1"
        close_on_prepare = request.headers.get("x-close-on-prepare-return") == "1"
        mutate_on_prepare = (
            request.headers.get("x-mutate-on-prepare-return") == "1"
        )
        mutate_advertised_id_on_prepare = (
            request.headers.get("x-mutate-prepared-id-on-return") == "1"
        )
        classifier_attack = (
            request.headers.get("x-profile-classifier-attack") == "1"
        )
        monitoring = getattr(sys, "monitoring", None)
        monitor_tool_id: int | None = None
        monitor_return_event: int | None = None

        def stop_local_identity_capture():
            nonlocal monitor_tool_id
            tool_id = monitor_tool_id
            monitor_tool_id = None
            if tool_id is None:
                return
            try:
                monitoring.set_local_events(tool_id, target_code, 0)
            finally:
                try:
                    monitoring.register_callback(tool_id, monitor_return_event, None)
                finally:
                    monitoring.free_tool_id(tool_id)

        def monitor_return(code, _instruction_offset, value):
            if code is target_code and type(value) is bytes:
                captured.append(value)

        def start_local_identity_capture() -> bool:
            nonlocal monitor_tool_id, monitor_return_event
            if monitoring is None:
                return False
            try:
                monitor_return_event = monitoring.events.PY_RETURN
                for tool_id in range(6):
                    if monitoring.get_tool(tool_id) is not None:
                        continue
                    try:
                        monitoring.use_tool_id(tool_id, "odysseus-test-page-identity")
                    except ValueError:
                        continue
                    monitor_tool_id = tool_id
                    monitoring.register_callback(
                        tool_id, monitor_return_event, monitor_return,
                    )
                    monitoring.set_local_events(
                        tool_id, target_code, monitor_return_event,
                    )
                    return True
            except Exception:
                if monitor_tool_id is not None:
                    stop_local_identity_capture()
            return False

        local_identity_capture = (
            capture
            and not force_profile_identity
            and start_local_identity_capture()
        )
        profile_identity_capture = capture and not local_identity_capture

        def profile(frame, event, arg):
            if (
                frame.f_code is prepare_code
                and event == "return"
                and classifier_attack
                and type(arg) is tuple
                and len(arg) == 2
                and type(arg[0]) is PreparedRequest
                and type(arg[1]) is tuple
            ):
                request.app.state.classifier_prepare_origin = arg[1]
            if (
                frame.f_code is prepare_code
                and event == "return"
                and close_on_prepare
                and type(arg) is tuple
                and len(arg) == 2
                and type(arg[0]) is PreparedRequest
                and type(arg[1]) is tuple
            ):
                allocated_id = object.__getattribute__(arg[0], "request_id")
                assert type(allocated_id) is str and len(allocated_id) == 45
                assert allocated_id.startswith("r.")
                assert all(character.isascii() and (
                    character.isalnum() or character in "-_"
                ) for character in allocated_id[2:])
                assert allocated_id in request.app.state.service._entries
                request.app.state.prepare_return_request_id = allocated_id
                request.app.state.prepare_return_close_count += 1
                runtime.close()
            elif (
                frame.f_code is prepare_code
                and event == "return"
                and mutate_on_prepare
                and type(arg) is tuple
                and len(arg) == 2
                and type(arg[0]) is PreparedRequest
                and type(arg[1]) is tuple
            ):
                allocated_id = object.__getattribute__(arg[0], "request_id")
                entry = arg[1][0]
                request.app.state.prepare_return_request_id = allocated_id
                request.app.state.prepare_return_origin = arg[1]
                request.app.state.prepare_return_mutation_count += 1
                digest = "0" * 64 if arg[1][12] != "0" * 64 else "1" * 64
                object.__setattr__(entry, "page_request_sha256", digest)
            elif (
                frame.f_code is prepare_code
                and event == "return"
                and mutate_advertised_id_on_prepare
                and type(arg) is tuple
                and len(arg) == 2
                and type(arg[0]) is PreparedRequest
                and type(arg[1]) is tuple
            ):
                allocated_id = object.__getattribute__(arg[0], "request_id")
                request.app.state.advertised_id_return_request_id = allocated_id
                request.app.state.advertised_id_return_origin = arg[1]
                request.app.state.advertised_id_return_mutation_count += 1
                object.__setattr__(
                    arg[0],
                    "request_id",
                    request.app.state.prepare_return_replacement_id,
                )
            elif frame.f_code is target_code and event == "call" and reentry:
                try:
                    runtime.handle(raw, identity=identity)
                except McpContextRequestRuntimeError as error:
                    reentry_codes.append(error.code)
            elif (
                frame.f_code is target_code
                and event == "return"
                and profile_identity_capture
                and type(arg) is bytes
            ):
                captured.append(arg)
            elif classifier_attack:
                attack = request.app.state.classifier_profile_attack
                attack_request_id = attack["request_id"]
                if (
                    not attack["fired"]
                    and (
                        attack["stage"] == "prepare"
                        or runtime._active_records.get(attack_request_id) is not None
                    )
                    and frame.f_code is attack["code"]
                    and event == attack["event"]
                ):
                    attack["fired"].append((frame.f_code, event))
                    attack["install"]()
            return profile

        profile_requested = (
            profile_identity_capture or reentry or close_on_prepare
            or mutate_on_prepare or mutate_advertised_id_on_prepare
            or classifier_attack
        )
        if close_on_prepare:
            request.app.state.prepare_return_close_count = 0
        if mutate_on_prepare:
            request.app.state.prepare_return_mutation_count = 0
        if mutate_advertised_id_on_prepare:
            request.app.state.advertised_id_return_mutation_count = 0
        try:
            if profile_requested:
                sys.setprofile(profile)
            frozen = runtime.handle(raw, identity=identity, bearer_proof=proof)
        finally:
            try:
                if profile_requested:
                    sys.setprofile(old_profile)
            finally:
                if local_identity_capture:
                    stop_local_identity_capture()
        request.app.state.runtime_byte_identity = not capture or (
            len(captured) == 1 and frozen is captured[0]
        )
        request.app.state.runtime_identity_capture_mode = (
            "monitoring" if local_identity_capture else "profile" if capture else "none"
        )
        request.app.state.runtime_reentry_codes = tuple(reentry_codes)
        parsed = json.loads(raw)
        if parsed["method"] == "harbour_context_prepare":
            result = json.loads(frozen)["result"]
            record = runtime._records[result["request_id"]]
            request.app.state.request_id = record.request_id
            request.app.state.lease = record.lease
        return Response(frozen, media_type="application/json")

    return application


def _call(
    client: TestClient,
    method: str,
    params: dict[str, object],
    *,
    rpc_id: str | int = 17,
    proof: bool = False,
    headers: dict[str, str] | None = None,
):
    merged = dict(headers or {})
    if proof:
        merged["x-bearer-proof"] = "1"
    return client.post(
        "/test/context-request-runtime",
        content=_wire(method, params, rpc_id),
        headers=merged,
    )


def _prepare(client: TestClient, *, rpc_id: int = 1) -> str:
    selection = _selection_wire(client.app)
    response = _call(
        client,
        "harbour_context_prepare",
        {"chat_session_id": "chat-owned", "selection": selection, "page_bytes": 16_384},
        rpc_id=rpc_id,
        proof=True,
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert set(result) == {
        "request_id", "selected_count", "requires_private_confirmation",
    }
    assert result["selected_count"] == len(selection)
    assert result["requires_private_confirmation"] is True
    return result["request_id"]


def _selection_wire(app) -> list[dict[str, object]]:
    return [
        {
            "source": item.source,
            "selection_ref": item.selection_ref,
            "revision_ref": item.revision_ref,
            **(
                {}
                if item.forge_locator is None
                else {"forge_locator": {
                    "repo_id": item.forge_locator.repo_id,
                    "version_id": item.forge_locator.version_id,
                    "path": item.forge_locator.path,
                }}
            ),
        }
        for item in app.state.fixture["selection"].items
    ]


def _browser_ack(client: TestClient, request_id: str) -> None:
    fixture = client.app.state.fixture
    client.cookies.set(SESSION_COOKIE, fixture["cookie"])
    challenge = client.post(
        "/api/context/mcp/challenge",
        headers=_BROWSER_HEADERS,
        json={"request_id": request_id},
    )
    assert challenge.status_code == 200
    token = challenge.json()["token"]
    response = client.post(
        "/api/chat",
        headers=_BROWSER_HEADERS,
        json={"message": "/mcp-context-disclose " + token, "session": "chat-owned"},
    )
    assert response.status_code == 200


def _shutdown(app) -> None:
    if hasattr(app.state, "request_runtime"):
        app.state.request_runtime.close()
        app.state.request_coordinator.close()
        app.state.request_cursor.close()


def test_real_persisted_prepare_browser_ack_manifest_and_full_eof_return_original_bytes(runtime_app):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        producer = runtime_app.state.fixture["combined"]
        assert type(producer) is CombinedContextManifestProducer
        with pytest.raises(AttributeError):
            object.__getattribute__(producer, "__dict__")
        record = runtime_app.state.request_runtime._records[request_id]
        issued_mono = object.__getattribute__(
            record.lease, "_McpContextReadLease__issued_monotonic",
        )
        deadline_mono = object.__getattribute__(
            record.lease, "_McpContextReadLease__deadline_monotonic",
        )
        issued_wall = object.__getattribute__(
            record.lease, "_McpContextReadLease__issued_wall",
        )
        deadline_wall = object.__getattribute__(
            record.lease, "_McpContextReadLease__deadline_wall",
        )
        assert deadline_mono - issued_mono == 300.0
        assert deadline_wall - issued_wall == 300.0
        _browser_ack(client, request_id)

        manifest = _call(
            client, "harbour_context_manifest", {"request_id": request_id}, rpc_id="manifest",
        )
        assert manifest.status_code == 200
        assert manifest.json()["result"]["complete"] is True
        assert request_id in runtime_app.state.request_runtime._records

        cursor = None
        pages = 0
        capture_modes: list[str] = []
        normal_capture_mode = (
            "monitoring" if getattr(sys, "monitoring", None) is not None else "profile"
        )
        while True:
            params = {"request_id": request_id}
            if cursor is not None:
                params["content_cursor"] = cursor
            capture_headers = {"x-capture-identity": "1"}
            if pages == 0:
                capture_headers["x-force-profile-identity"] = "1"
            response = _call(
                client,
                "harbour_context_page",
                params,
                rpc_id=100 + pages,
                headers=capture_headers,
            )
            assert response.status_code == 200
            assert runtime_app.state.runtime_byte_identity is True
            capture_modes.append(runtime_app.state.runtime_identity_capture_mode)
            assert capture_modes[-1] == (
                "profile" if pages == 0 else normal_capture_mode
            )
            result = response.json()["result"]
            pages += 1
            if result["complete"]:
                assert result["content_cursor"] is None
                break
            cursor = result["content_cursor"]
            assert type(cursor) is str and cursor
            assert pages < 100

        assert pages >= len(runtime_app.state.fixture["selection"].items)
        assert capture_modes[0] == "profile"
        assert capture_modes[1:] and set(capture_modes[1:]) == {normal_capture_mode}
        assert request_id not in runtime_app.state.request_runtime._records
        assert request_id not in runtime_app.state.service._entries
        _shutdown(runtime_app)


def test_preack_manifest_preserves_pristine_intent_until_browser_ack_and_cancel(
    runtime_app,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client, rpc_id=350)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        record = runtime._records[request_id]
        entry = service._entries[request_id]
        lease = record.lease
        binding = record.binding
        binding_issuer = object.__getattribute__(authority, "_binding_issuer")
        assert type(binding_issuer) is object
        assert binding_issuer is not authority
        assert object.__getattribute__(binding, "_McpContextBinding__issuer") is binding_issuer
        assert object.__getattribute__(binding, "_McpContextBinding__authority") is binding_issuer

        denied = _call(
            client,
            "harbour_context_manifest",
            {"request_id": request_id},
            rpc_id=351,
        )
        assert denied.status_code == 403
        assert denied.content == b'{"error":"context_request_unavailable"}'
        assert denied.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert runtime._records.get(request_id) is record
        assert service._entries.get(request_id) is entry
        assert record.lease is lease
        assert record.binding is binding
        assert authority.recheck_lease(lease) is binding

        repeated = _call(
            client,
            "harbour_context_manifest",
            {"request_id": request_id},
            rpc_id=354,
        )
        assert repeated.status_code == 403
        assert repeated.content == b'{"error":"context_request_unavailable"}'
        assert runtime._records.get(request_id) is record
        assert service._entries.get(request_id) is entry
        assert authority.recheck_lease(lease) is binding

        _browser_ack(client, request_id)
        manifest = _call(
            client,
            "harbour_context_manifest",
            {"request_id": request_id},
            rpc_id=352,
        )
        assert manifest.status_code == 200
        assert manifest.json()["result"]["complete"] is True
        cancelled = _call(
            client,
            "harbour_context_cancel",
            {"request_id": request_id},
            rpc_id=353,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["result"] == {"cancelled": True}
        assert request_id not in runtime._records
        assert request_id not in service._entries
        _shutdown(runtime_app)


def test_browser_revoke_then_runtime_cancel_releases_retained_owner_record_lease_and_binding(
    runtime_app,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client, rpc_id=360)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        record = runtime._records[request_id]
        lease_id = record.lease_id
        lease = record.lease
        binding = record.binding

        client.cookies.set(SESSION_COOKIE, runtime_app.state.fixture["cookie"])
        challenge = client.post(
            "/api/context/mcp/challenge",
            headers=_BROWSER_HEADERS,
            json={"request_id": request_id},
        )
        assert challenge.status_code == 200
        challenge_token = challenge.json()["token"]
        browser_revoke = client.post(
            "/api/context/mcp/revoke",
            headers=_BROWSER_HEADERS,
            json={"request_id": request_id},
        )
        assert browser_revoke.status_code == 200
        assert browser_revoke.json() == {
            "status": "revoked",
            "entry_absent": True,
            "lease_retained": True,
        }
        assert client.post(
            "/api/chat",
            headers=_BROWSER_HEADERS,
            json={
                "message": "/mcp-context-disclose " + challenge_token,
                "session": "chat-owned",
            },
        ).status_code == 403

        assert request_id not in service._entries
        assert runtime._records.get(request_id) is record
        assert record.lease_id == lease_id
        assert record.lease is lease
        assert record.binding is binding
        assert lease_id in runtime._lease_ids
        assert authority.recheck_lease(lease) is binding
        assert authority.recheck_binding(binding) is None

        cancelled = _call(
            client,
            "harbour_context_cancel",
            {"request_id": request_id},
            rpc_id=361,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["result"] == {"cancelled": True}
        assert request_id not in runtime._records
        assert request_id not in service._entries
        assert lease_id not in runtime._lease_ids
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(binding)
        _shutdown(runtime_app)


@pytest.mark.parametrize(
    ("case", "succeeds", "expected_calls"),
    (
        ("exact-invalid-request", True, ()),
        ("subclass-invalid-request", False, ("entries",)),
        ("exact-other-code", False, ("entries",)),
        ("trusted-revoke-substitution", False, ()),
        ("service-revoke-descriptor-substitution", False, ()),
    ),
)
def test_coordinator_cancel_tolerates_only_exact_invalid_request_from_genuine_pinned_revoke(
    runtime_app, monkeypatch, case, succeeds, expected_calls,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client, rpc_id=370)
        coordinator = runtime_app.state.request_coordinator
        service = runtime_app.state.service
        coordinator_module = sys.modules[McpContextPackCoordinator.__module__]
        calls: list[str] = []

        class DerivedConfirmationError(McpPrivateContextConfirmationError):
            pass

        class RaisingEntries(dict):
            def get(self, key, default=None):
                calls.append("entries")
                if case == "subclass-invalid-request":
                    raise DerivedConfirmationError("invalid_request")
                raise McpPrivateContextConfirmationError("closed")

        def fake_revoke(*_args, **_kwargs):
            calls.append("fake")
            raise McpPrivateContextConfirmationError("invalid_request")

        with monkeypatch.context() as attack:
            if case == "exact-invalid-request":
                McpPrivateContextConfirmationService.revoke(service, request_id)
            elif case in {"subclass-invalid-request", "exact-other-code"}:
                attack.setattr(service, "_entries", RaisingEntries(service._entries))
            elif case == "trusted-revoke-substitution":
                attack.setattr(coordinator_module, "_TRUSTED_REVOKE", fake_revoke)
            else:
                attack.setattr(
                    McpPrivateContextConfirmationService, "revoke", fake_revoke,
                )

            if succeeds:
                assert coordinator.cancel(request_id) is None
            else:
                with pytest.raises(McpContextPackCoordinatorError) as caught:
                    coordinator.cancel(request_id)
                assert type(caught.value) is McpContextPackCoordinatorError

        assert tuple(calls) == expected_calls
        if case == "exact-invalid-request":
            assert request_id not in service._entries
        else:
            assert request_id in service._entries
        _shutdown(runtime_app)


@pytest.mark.parametrize("case", (
    "state", "intent", "generation", "cookie", "cookie-snapshot",
    "challenge", "receipt", "ack-descriptor", "permit-nonce",
    "execution-nonce", "entry-page-bytes", "snapshot-item",
    "snapshot-target", "snapshot-metadata", "snapshot-replacement-pair",
    "selection-ref", "selection-revision", "forge-repo", "forge-version",
    "forge-path", "runtime-page-bytes", "page-digest", "issued", "deadline",
    "mono-deadline", "registry-permit", "registry-exchange",
    "registry-completion", "registry-native-completion",
), ids=(
    "state", "intent", "generation", "cookie", "cookie-snapshot",
    "challenge", "receipt", "ack-descriptor", "permit-nonce",
    "execution-nonce", "entry-page-bytes", "snapshot-item",
    "snapshot-target", "snapshot-metadata", "snapshot-replacement-pair",
    "selection-ref", "selection-revision", "forge-repo", "forge-version",
    "forge-path", "runtime-page-bytes", "page-digest", "issued", "deadline",
    "mono-deadline", "registry-permit", "registry-exchange",
    "registry-completion", "registry-native-completion",
))
def test_nonpristine_intent_fields_never_preserve_preack_manifest(
    runtime_app,
    case,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=400)
        request_id = _prepare(client, rpc_id=401)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        unrelated_record = runtime._records[unrelated_request_id]
        record = runtime._records[request_id]
        entry = service._entries[request_id]
        simple_entry_changes = {
            "state": ("state", "prepared"),
            "intent": ("intent", False),
            "generation": ("generation", 1),
            "cookie": ("cookie", "occupied"),
            "cookie-snapshot": ("cookie_snapshot", object()),
            "challenge": ("challenge", "occupied"),
            "receipt": ("receipt", object()),
            "ack-descriptor": ("ack_descriptor", object()),
            "permit-nonce": ("permit_nonce", 1),
            "execution-nonce": ("execution_nonce", 1),
            "entry-page-bytes": ("page_bytes", 4096),
        }
        if case in simple_entry_changes:
            field, value = simple_entry_changes[case]
            object.__setattr__(entry, field, value)
        elif case == "snapshot-item":
            item = entry.mcp_snapshot.items[0]
            digest = "0" * 64 if item.boundary_sha256 != "0" * 64 else "1" * 64
            object.__setattr__(item, "boundary_sha256", digest)
        elif case == "snapshot-target":
            targets = entry.mcp_snapshot.target_manifest_digests
            digest = "0" * 64 if targets[0] != "0" * 64 else "1" * 64
            object.__setattr__(
                entry.mcp_snapshot,
                "target_manifest_digests",
                (digest, *targets[1:]),
            )
        elif case == "snapshot-metadata":
            object.__setattr__(entry.metadata_binding, "owner_sid", "drift-owner")
        elif case == "snapshot-replacement-pair":
            metadata = entry.metadata_binding
            replacement_metadata = type(metadata)(*tuple(
                object.__getattribute__(metadata, name)
                for name in (
                    "schema", "audience", "owner_sid", "chat_session_id",
                    "recipient_token_id", "scope", "cache_generation",
                    "security_revision", "app_epoch",
                )
            ))
            snapshot = entry.mcp_snapshot
            replacement_snapshot = type(snapshot)(
                snapshot.items,
                snapshot.target_manifest_digests,
                replacement_metadata,
            )
            object.__setattr__(entry, "mcp_snapshot", replacement_snapshot)
            object.__setattr__(entry, "metadata_binding", replacement_metadata)
        elif case in {"selection-ref", "selection-revision"}:
            item = record.selection.items[0]
            field = "selection_ref" if case == "selection-ref" else "revision_ref"
            object.__setattr__(item, field, object.__getattribute__(item, field) + "-drift")
        elif case in {"forge-repo", "forge-version", "forge-path"}:
            forge_item = next(
                item for item in record.selection.items if item.forge_locator is not None
            )
            field = {
                "forge-repo": "repo_id",
                "forge-version": "version_id",
                "forge-path": "path",
            }[case]
            locator = forge_item.forge_locator
            object.__setattr__(
                locator,
                field,
                object.__getattribute__(locator, field) + "-drift",
            )
        elif case == "runtime-page-bytes":
            object.__setattr__(record, "page_bytes", 4096)
        elif case == "page-digest":
            digest = "0" * 64 if entry.page_request_sha256 != "0" * 64 else "1" * 64
            object.__setattr__(entry, "page_request_sha256", digest)
        elif case in {"issued", "deadline"}:
            value = object.__getattribute__(entry, case)
            object.__setattr__(entry, case, value.replace(year=value.year + 1))
        elif case == "mono-deadline":
            object.__setattr__(entry, "mono_deadline", entry.mono_deadline + 1.0)
        else:
            confirmation_module = sys.modules[
                McpPrivateContextConfirmationService.__module__
            ]
            registry_name, row_name, reference_field = {
                "registry-permit": ("_permits", "_PermitRow", "entry_ref"),
                "registry-exchange": (
                    "_exchanges", "_HumanExchangeRow", "request_id",
                ),
                "registry-completion": (
                    "_completions", "_CompletionRow", "entry_ref",
                ),
                "registry-native-completion": (
                    "_native_completions", "_NativeCompletionRow", "entry_ref",
                ),
            }[case]
            row = object.__new__(getattr(confirmation_module, row_name))
            reference = (
                request_id
                if reference_field == "request_id"
                else confirmation_module.weakref.ref(entry)
            )
            object.__setattr__(row, reference_field, reference)
            getattr(service, registry_name)[id(row)] = row

        denied = _call(
            client,
            "harbour_context_manifest",
            {"request_id": request_id},
            rpc_id=402,
        )
        assert denied.status_code == 403
        assert denied.content == b'{"error":"context_request_unavailable"}'
        assert denied.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert request_id not in runtime._records
        assert request_id not in service._entries
        assert unrelated_request_id in runtime._records
        assert unrelated_request_id in service._entries
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(record.lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(record.binding)
        assert authority.recheck_lease(unrelated_record.lease) is unrelated_record.binding
        _shutdown(runtime_app)


def test_pristine_classifier_rejects_mismatched_inputs_and_referencing_proof_row(
    runtime_app,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client, rpc_id=450)
        other_request_id = _prepare(client, rpc_id=451)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        classifier = confirmation_module._classify_pristine_intent
        sentinel = confirmation_module._PRISTINE_INTENT_SENTINEL
        record = runtime._records[request_id]
        other_record = runtime._records[other_request_id]
        binding = record.binding
        recipient = (
            binding.owner_subject_id,
            binding.chat_session_id,
            binding.token_id,
            binding.scope,
            binding.cache_generation,
            binding.security_action_revision,
            binding.process_epoch,
        )
        arguments = [
            runtime._request_rejection_capability,
            record.request_id,
            record.lease_id,
            record.lease,
            record.binding,
            record.identity[2],
            recipient,
            record.selection,
            record.page_bytes,
            record.origin,
        ]
        assert classifier(*arguments) is sentinel
        replacements = {
            2: "l." + "x" * 43,
            3: other_record.lease,
            4: other_record.binding,
            5: "wrong-owner",
            6: ("wrong-owner", *recipient[1:]),
            7: other_record.selection,
            8: 4096,
            9: other_record.origin,
        }
        for index, replacement in replacements.items():
            changed = list(arguments)
            changed[index] = replacement
            assert classifier(*changed) is not sentinel

        entry = service._entries[request_id]
        other_entry = service._entries[other_request_id]
        entry_replacements = {
            "lease_ref": other_entry.lease_ref,
            "binding_ref": other_entry.binding_ref,
            "owner_sid": "wrong-owner",
            "selection": other_entry.selection,
            "page_bytes": 4096,
        }
        for field, replacement in entry_replacements.items():
            original = object.__getattribute__(entry, field)
            object.__setattr__(entry, field, replacement)
            try:
                assert classifier(*arguments) is not sentinel
            finally:
                object.__setattr__(entry, field, original)

        _browser_ack(client, other_request_id)
        permit = service.issue_permit(other_request_id)
        row = service._permits[id(permit)]
        row_type = type(row)
        row_names = (
            "permit_ref", "entry_ref", "service_ref", "authority_ref", "lease_ref",
            "binding_ref", "recipient", "thread_ref", "thread_ident", "pid",
            "request_id", "generation", "nonce",
        )
        row_values = [object.__getattribute__(row, name) for name in row_names]
        row_values[1] = confirmation_module.weakref.ref(service._entries[request_id])
        service._permits[id(permit)] = row_type(*row_values)
        assert classifier(*arguments) is not sentinel

        denied = _call(
            client,
            "harbour_context_manifest",
            {"request_id": request_id},
            rpc_id=452,
        )
        assert denied.status_code == 403
        assert denied.content == b'{"error":"context_request_unavailable"}'
        assert denied.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert request_id not in runtime._records
        assert request_id not in service._entries
        assert other_request_id in runtime._records
        assert other_request_id in service._entries
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(record.lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(record.binding)
        assert authority.recheck_lease(other_record.lease) is other_record.binding
        assert permit is not None
        _shutdown(runtime_app)


def test_pristine_classifier_integrity_drift_is_preclaim_and_never_executes_fake(
    runtime_app,
    monkeypatch,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client, rpc_id=475)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        record = runtime._records[request_id]
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        acquirer = confirmation_module._acquire_request_rejection_capability
        classifier = confirmation_module._classify_pristine_intent
        calls = []

        def fake(*_args, **_kwargs):
            calls.append("fake")
            return 0

        def code_trap(*_args, **_kwargs):
            globals()["_runtime_pristine_classifier_trap_calls"].append("code")
            return 0

        def closure_replacement():
            captured = calls

            def replacement(*_args, **_kwargs):
                captured.append("closure")
                return 0

            return replacement

        class HostileDescriptor:
            def __get__(self, _instance, _owner):
                calls.append("descriptor")
                raise AssertionError("hostile descriptor executed")

            def __set__(self, _instance, _value):
                calls.append("descriptor-set")
                raise AssertionError("hostile descriptor executed")

        attacks = (
            ("factory-module", lambda attack: attack.setitem(
                confirmation_module.__dict__,
                "_acquire_request_rejection_capability",
                fake,
            )),
            ("factory-code", lambda attack: attack.setattr(
                acquirer, "__code__", code_trap.__code__,
            )),
            ("factory-defaults", lambda attack: attack.setattr(
                acquirer,
                "__defaults__",
                tuple(object() for _ in acquirer.__defaults__),
            )),
            ("factory-closure", lambda attack: attack.setitem(
                confirmation_module.__dict__,
                "_acquire_request_rejection_capability",
                closure_replacement(),
            )),
            ("module", lambda attack: attack.setitem(
                confirmation_module.__dict__, "_classify_pristine_intent", fake,
            )),
            ("sentinel", lambda attack: attack.setitem(
                confirmation_module.__dict__, "_PRISTINE_INTENT_SENTINEL", object(),
            )),
            ("code", lambda attack: attack.setattr(
                classifier, "__code__", code_trap.__code__,
            )),
            ("defaults", lambda attack: attack.setattr(
                classifier,
                "__defaults__",
                tuple(object() for _ in classifier.__defaults__),
            )),
            ("closure", lambda attack: attack.setitem(
                confirmation_module.__dict__,
                "_classify_pristine_intent",
                closure_replacement(),
            )),
            ("capsule", lambda attack: attack.setattr(
                service, "_native_rejection_capsule", object(),
            )),
            ("descriptor", lambda attack: attack.setattr(
                type(service._entries[request_id]), "state", HostileDescriptor(),
            )),
        )
        for _name, install in attacks:
            with monkeypatch.context() as attack:
                if _name in {"factory-code", "code"}:
                    target = acquirer if _name == "factory-code" else classifier
                    assert target.__code__.co_freevars == code_trap.__code__.co_freevars == ()
                    attack.setitem(
                        target.__globals__, "_runtime_pristine_classifier_trap_calls", calls,
                    )
                install(attack)
                denied = _call(
                    client,
                    "harbour_context_manifest",
                    {"request_id": request_id},
                    rpc_id=476,
                )
            assert denied.status_code == 403
            assert denied.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
            assert calls == []
            assert request_id in runtime._records
            assert request_id in service._entries
            assert record.lease_id in runtime._lease_ids
            assert runtime._pending == {}
            assert runtime._active_records == {}
            assert runtime._active_threads == {}
            assert authority.recheck_lease(record.lease) is record.binding
            assert authority.recheck_binding(record.binding) is None
        _shutdown(runtime_app)


@pytest.mark.parametrize("case", (
    "checker-code", "checker-defaults", "checker-descriptor",
    "projection-code", "projection-defaults", "projection-descriptor",
    "classifier-call-code", "classifier-call-defaults",
    "classifier-call-descriptor", "classifier-return-code",
    "classifier-return-defaults", "classifier-return-descriptor",
), ids=(
    "checker-code", "checker-defaults", "checker-descriptor",
    "projection-code", "projection-defaults", "projection-descriptor",
    "classifier-call-code", "classifier-call-defaults",
    "classifier-call-descriptor", "classifier-return-code",
    "classifier-return-defaults", "classifier-return-descriptor",
))
def test_pristine_classifier_profile_drift_is_after_claim_terminal_and_fake_free(
    runtime_app,
    monkeypatch,
    case,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=480)
        request_id = _prepare(client, rpc_id=481)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        record = runtime._records[request_id]
        unrelated_record = runtime._records[unrelated_request_id]
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        runtime_module = sys.modules[McpContextRequestRuntime.__module__]
        classifier = confirmation_module._classify_pristine_intent
        calls = []

        def code_trap(*_args, **_kwargs):
            globals()["_runtime_pristine_classifier_trap_calls"].append("code")
            return 0

        class HostileDescriptor:
            def __get__(self, _instance, _owner):
                calls.append("descriptor")
                return 0

            def __set__(self, _instance, _value):
                calls.append("descriptor-set")

        phase, attack_kind = case.rsplit("-", 1)
        phase_code, phase_event = {
            "checker": (runtime_module._class_matches.__code__, "return"),
            "projection": (runtime_module._binding_projection.__code__, "return"),
            "classifier-call": (classifier.__code__, "call"),
            "classifier-return": (classifier.__code__, "return"),
        }[phase]
        with monkeypatch.context() as attack:
            if attack_kind == "code":
                assert classifier.__code__.co_freevars == code_trap.__code__.co_freevars == ()
                attack.setitem(
                    classifier.__globals__, "_runtime_pristine_classifier_trap_calls", calls,
                )
                install = lambda: attack.setattr(
                    classifier, "__code__", code_trap.__code__,
                )
            elif attack_kind == "defaults":
                install = lambda: attack.setattr(
                    classifier,
                    "__defaults__",
                    tuple(object() for _ in classifier.__defaults__),
                )
            else:
                install = lambda: attack.setattr(
                    PreparedRequest, "request_id", HostileDescriptor(),
                )
            runtime_app.state.classifier_profile_attack = {
                "stage": "existing",
                "request_id": request_id,
                "code": phase_code,
                "event": phase_event,
                "install": install,
                "fired": [],
            }
            denied = _call(
                client,
                "harbour_context_manifest",
                {"request_id": request_id},
                rpc_id=482,
                headers={"x-profile-classifier-attack": "1"},
            )
            assert runtime_app.state.classifier_profile_attack["fired"]
        assert denied.status_code == 403
        assert denied.content == b'{"error":"context_request_unavailable"}'
        assert calls == []
        assert request_id not in runtime._records
        assert request_id not in service._entries
        assert record.lease_id not in runtime._lease_ids
        assert runtime._pending == {}
        assert runtime._active_records == {}
        assert runtime._active_threads == {}
        assert unrelated_request_id in runtime._records
        assert unrelated_request_id in service._entries
        assert unrelated_record.lease_id in runtime._lease_ids
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(record.lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(record.binding)
        assert authority.recheck_lease(unrelated_record.lease) is unrelated_record.binding
        _shutdown(runtime_app)


def test_mutation_on_genuine_prepare_return_releases_every_owned_partial_allocation(
    runtime_app,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=7)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime_app.state.fixture["authority"]
        unrelated_record = runtime._records[unrelated_request_id]
        response = _call(
            client,
            "harbour_context_prepare",
            {
                "chat_session_id": "chat-owned",
                "selection": _selection_wire(runtime_app),
                "page_bytes": 16_384,
            },
            rpc_id=8,
            proof=True,
            headers={"x-mutate-on-prepare-return": "1"},
        )
        assert response.status_code == 403
        assert response.content == b'{"error":"context_request_unavailable"}'
        assert runtime_app.state.prepare_return_mutation_count == 1
        allocated_id = runtime_app.state.prepare_return_request_id
        origin = runtime_app.state.prepare_return_origin
        target_lease = origin[4]
        target_binding = origin[5]
        target_lease_id = origin[7]

        assert runtime._closed is False
        assert set(runtime._records) == {unrelated_request_id}
        assert allocated_id not in service._entries
        assert set(service._entries) == {unrelated_request_id}
        assert target_lease_id not in runtime._lease_ids
        assert runtime._pending == {}
        assert runtime._active_records == {}
        assert runtime._active_threads == {}
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(target_lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(target_binding)
        assert authority._cancelled_leases == {}
        assert unrelated_record.lease_id in runtime._lease_ids
        assert authority.recheck_lease(unrelated_record.lease) is unrelated_record.binding
        _shutdown(runtime_app)


@pytest.mark.parametrize(
    "case",
    (
        "prepare-scalar", "prepare-digest", "prepare-state",
        "prepare-snapshot-item", "prepare-snapshot-target",
        "prepare-snapshot-metadata", "prepare-selection", "prepare-locator",
        "prepare-lease", "prepare-binding", "prepare-proof-permit",
        "prepare-proof-exchange", "prepare-proof-completion",
        "prepare-proof-native-completion",
        "existing-scalar", "existing-digest", "existing-state",
        "existing-snapshot-item", "existing-snapshot-target",
        "existing-snapshot-metadata", "existing-selection", "existing-locator",
        "existing-lease", "existing-binding", "existing-proof-permit",
        "existing-proof-exchange", "existing-proof-completion",
        "existing-proof-native-completion",
        "existing-record-request-id", "existing-record-lease",
        "existing-record-binding", "existing-record-origin",
        "prepare-binding-issuer", "prepare-binding-authority", "prepare-authority-issuer",
        "existing-binding-issuer", "existing-binding-authority", "existing-authority-issuer",
    ),
    ids=(
        "prepare-scalar", "prepare-digest", "prepare-state",
        "prepare-snapshot-item", "prepare-snapshot-target",
        "prepare-snapshot-metadata", "prepare-selection", "prepare-locator",
        "prepare-lease", "prepare-binding", "prepare-proof-permit",
        "prepare-proof-exchange", "prepare-proof-completion",
        "prepare-proof-native-completion",
        "existing-scalar", "existing-digest", "existing-state",
        "existing-snapshot-item", "existing-snapshot-target",
        "existing-snapshot-metadata", "existing-selection", "existing-locator",
        "existing-lease", "existing-binding", "existing-proof-permit",
        "existing-proof-exchange", "existing-proof-completion",
        "existing-proof-native-completion",
        "existing-record-request-id", "existing-record-lease",
        "existing-record-binding", "existing-record-origin",
        "prepare-binding-issuer", "prepare-binding-authority", "prepare-authority-issuer",
        "existing-binding-issuer", "existing-binding-authority", "existing-authority-issuer",
    ),
)
def test_classifier_return_live_state_drift_is_terminal_before_install_or_preserve(
    runtime_app,
    case,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=490)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        original_binding_issuer = object.__getattribute__(authority, "_binding_issuer")
        unrelated_record = runtime._records[unrelated_request_id]
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        classifier = confirmation_module._classify_pristine_intent
        stage, mutation = case.split("-", 1)
        target_record = None
        if stage == "existing":
            target_request_id = _prepare(client, rpc_id=491)
            target_record = runtime._records[target_request_id]
            target_lease = target_record.lease
            target_binding = target_record.binding
            target_lease_id = target_record.lease_id

        def install_live_drift():
            origin = (
                runtime_app.state.classifier_prepare_origin
                if stage == "prepare"
                else target_record.origin
            )
            entry = origin[0]
            snapshot = origin[1]
            metadata = origin[2]
            selection = origin[3]
            lease = origin[4]
            binding = origin[5]
            request_id = origin[6]
            if mutation == "scalar":
                object.__setattr__(entry, "page_bytes", 4096)
            elif mutation == "digest":
                replacement = "0" * 64 if origin[12] != "0" * 64 else "1" * 64
                object.__setattr__(entry, "page_request_sha256", replacement)
            elif mutation == "state":
                object.__setattr__(entry, "state", "challenge")
            elif mutation == "snapshot-item":
                item = snapshot.items[0]
                replacement = "public" if item.classification != "public" else "private"
                object.__setattr__(item, "classification", replacement)
            elif mutation == "snapshot-target":
                replacement = "0" * 64 if snapshot.target_manifest_digests[0] != "0" * 64 else "1" * 64
                object.__setattr__(
                    snapshot,
                    "target_manifest_digests",
                    (replacement, *snapshot.target_manifest_digests[1:]),
                )
            elif mutation == "snapshot-metadata":
                object.__setattr__(metadata, "scope", metadata.scope + ".drift")
            elif mutation == "selection":
                selected = selection.items[0]
                object.__setattr__(
                    selected, "selection_ref", selected.selection_ref + ".drift",
                )
            elif mutation == "locator":
                selected = next(
                    item for item in selection.items if item.forge_locator is not None
                )
                locator = selected.forge_locator
                object.__setattr__(locator, "path", locator.path + ".drift")
            elif mutation == "lease":
                object.__setattr__(
                    entry,
                    "lease_ref",
                    confirmation_module.weakref.ref(unrelated_record.lease),
                )
            elif mutation == "binding":
                object.__setattr__(
                    entry,
                    "binding_ref",
                    confirmation_module.weakref.ref(unrelated_record.binding),
                )
            elif mutation == "binding-issuer":
                object.__setattr__(binding, "_McpContextBinding__issuer", object())
            elif mutation == "binding-authority":
                object.__setattr__(binding, "_McpContextBinding__authority", object())
            elif mutation == "authority-issuer":
                object.__setattr__(authority, "_binding_issuer", object())
            elif mutation == "record-request-id":
                object.__setattr__(target_record, "request_id", unrelated_request_id)
            elif mutation == "record-lease":
                object.__setattr__(target_record, "lease", unrelated_record.lease)
            elif mutation == "record-binding":
                object.__setattr__(target_record, "binding", unrelated_record.binding)
            elif mutation == "record-origin":
                object.__setattr__(target_record, "origin", unrelated_record.origin)
            else:
                registry_name, row_name, reference_field = {
                    "proof-permit": ("_permits", "_PermitRow", "entry_ref"),
                    "proof-exchange": (
                        "_exchanges", "_HumanExchangeRow", "request_id",
                    ),
                    "proof-completion": (
                        "_completions", "_CompletionRow", "entry_ref",
                    ),
                    "proof-native-completion": (
                        "_native_completions", "_NativeCompletionRow", "entry_ref",
                    ),
                }[mutation]
                row = object.__new__(getattr(confirmation_module, row_name))
                reference = (
                    request_id
                    if reference_field == "request_id"
                    else confirmation_module.weakref.ref(entry)
                )
                object.__setattr__(row, reference_field, reference)
                getattr(service, registry_name)[id(row)] = row

        runtime_app.state.classifier_profile_attack = {
            "stage": stage,
            "request_id": (
                unrelated_request_id if stage == "prepare" else target_request_id
            ),
            "code": classifier.__code__,
            "event": "return",
            "install": install_live_drift,
            "fired": [],
        }
        if stage == "prepare":
            denied = _call(
                client,
                "harbour_context_prepare",
                {
                    "chat_session_id": "chat-owned",
                    "selection": _selection_wire(runtime_app),
                    "page_bytes": 16_384,
                },
                rpc_id=492,
                proof=True,
                headers={"x-profile-classifier-attack": "1"},
            )
            origin = runtime_app.state.classifier_prepare_origin
            target_request_id = origin[6]
            target_lease = origin[4]
            target_binding = origin[5]
            target_lease_id = origin[7]
        else:
            denied = _call(
                client,
                "harbour_context_manifest",
                {"request_id": target_request_id},
                rpc_id=493,
                headers={"x-profile-classifier-attack": "1"},
            )

        if mutation in {"binding-issuer", "binding-authority"}:
            attacked_slot = {
                "binding-issuer": "_McpContextBinding__issuer",
                "binding-authority": "_McpContextBinding__authority",
            }[mutation]
            assert object.__getattribute__(target_binding, attacked_slot) is not original_binding_issuer
            object.__setattr__(target_binding, attacked_slot, original_binding_issuer)
        if mutation == "authority-issuer":
            assert object.__getattribute__(authority, "_binding_issuer") is not original_binding_issuer
            object.__setattr__(authority, "_binding_issuer", original_binding_issuer)
        assert runtime_app.state.classifier_profile_attack["fired"]
        assert denied.status_code == 403
        assert denied.content == b'{"error":"context_request_unavailable"}'
        assert target_request_id not in runtime._records
        assert target_request_id not in service._entries
        assert target_lease_id not in runtime._lease_ids
        assert runtime._pending == {}
        assert runtime._active_records == {}
        assert runtime._active_threads == {}
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(target_lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(target_binding)
        assert runtime._records.get(unrelated_request_id) is unrelated_record
        assert unrelated_request_id in service._entries
        assert unrelated_record.lease_id in runtime._lease_ids
        assert authority.recheck_lease(unrelated_record.lease) is unrelated_record.binding
        _shutdown(runtime_app)


@pytest.mark.parametrize(
    "case",
    (
        "prepare-runtime-slot-descriptor",
        "prepare-runtime-getattribute",
        "prepare-record-slot-descriptor",
        "prepare-record-getattribute",
        "existing-preack-runtime-slot-descriptor",
        "existing-preack-runtime-getattribute",
        "existing-preack-record-slot-descriptor",
        "existing-preack-record-getattribute",
        "existing-postack-runtime-slot-descriptor",
        "existing-postack-runtime-getattribute",
        "existing-postack-record-slot-descriptor",
        "existing-postack-record-getattribute",
        "prepare-hostile-records-container",
        "prepare-hostile-runtime-generation",
        "prepare-hostile-pending-owner",
        "prepare-hostile-pending-lease",
        "existing-preack-hostile-record-request-id",
        "existing-preack-hostile-records-container",
        "existing-postack-hostile-record-lease-id",
        "existing-postack-hostile-record-identity-container",
        "existing-postack-hostile-active-records-container",
    ),
    ids=(
        "prepare-runtime-slot-descriptor",
        "prepare-runtime-getattribute",
        "prepare-record-slot-descriptor",
        "prepare-record-getattribute",
        "existing-preack-runtime-slot-descriptor",
        "existing-preack-runtime-getattribute",
        "existing-preack-record-slot-descriptor",
        "existing-preack-record-getattribute",
        "existing-postack-runtime-slot-descriptor",
        "existing-postack-runtime-getattribute",
        "existing-postack-record-slot-descriptor",
        "existing-postack-record-getattribute",
        "prepare-hostile-records-container",
        "prepare-hostile-runtime-generation",
        "prepare-hostile-pending-owner",
        "prepare-hostile-pending-lease",
        "existing-preack-hostile-record-request-id",
        "existing-preack-hostile-records-container",
        "existing-postack-hostile-record-lease-id",
        "existing-postack-hostile-record-identity-container",
        "existing-postack-hostile-active-records-container",
    ),
)
def test_classifier_return_native_runtime_ownership_drift_is_terminal_and_fake_free(
    runtime_app,
    monkeypatch,
    case,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=494)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime._authority
        unrelated_record = runtime._records[unrelated_request_id]
        runtime_module = sys.modules[McpContextRequestRuntime.__module__]
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        classifier = confirmation_module._classify_pristine_intent
        record_type = runtime_module._RequestRecord
        stage = "prepare" if case.startswith("prepare-") else "existing"
        postack = case.startswith("existing-postack-")
        if stage == "prepare":
            mutation = case[len("prepare-"):]
        elif postack:
            mutation = case[len("existing-postack-"):]
        else:
            mutation = case[len("existing-preack-"):]
        target_record = None
        target_request_id = unrelated_request_id
        target_lease = None
        target_binding = None
        target_lease_id = None
        if stage == "existing":
            target_request_id = _prepare(client, rpc_id=495)
            target_record = runtime._records[target_request_id]
            target_lease = target_record.lease
            target_binding = target_record.binding
            target_lease_id = target_record.lease_id
            if postack:
                _browser_ack(client, target_request_id)

        fake_calls: list[str] = []
        restored_runtime_slot: tuple[str, object] | None = None

        class HostileValue:
            def __eq__(self, _other):
                fake_calls.append("eq")
                raise AssertionError("hostile equality executed")

            def __ne__(self, _other):
                fake_calls.append("ne")
                raise AssertionError("hostile inequality executed")

            def __hash__(self):
                fake_calls.append("hash")
                raise AssertionError("hostile hash executed")

        class HostileContainer:
            def __eq__(self, _other):
                fake_calls.append("container-eq")
                raise AssertionError("hostile container equality executed")

            def __ne__(self, _other):
                fake_calls.append("container-ne")
                raise AssertionError("hostile container inequality executed")

            def __hash__(self):
                fake_calls.append("container-hash")
                raise AssertionError("hostile container hash executed")

            def get(self, *_args, **_kwargs):
                fake_calls.append("container-get")
                raise AssertionError("hostile container lookup executed")

        class HostileDescriptor:
            def __init__(self, fallback):
                self.fallback = fallback

            def __get__(self, _instance, _owner):
                fake_calls.append("descriptor-get")
                return self.fallback

            def __set__(self, _instance, _value):
                fake_calls.append("descriptor-set")
                raise AssertionError("hostile descriptor write executed")

        def hostile_runtime_getattribute(instance, name):
            fake_calls.append("runtime-getattribute")
            return object.__getattribute__(instance, name)

        def hostile_record_getattribute(instance, name):
            fake_calls.append("record-getattribute")
            return object.__getattribute__(instance, name)

        with monkeypatch.context() as attack:
            def install_native_drift():
                nonlocal restored_runtime_slot
                if mutation == "runtime-slot-descriptor":
                    attack.setattr(
                        McpContextRequestRuntime,
                        "_records",
                        HostileDescriptor(runtime._records),
                    )
                elif mutation == "runtime-getattribute":
                    attack.setattr(
                        McpContextRequestRuntime,
                        "__getattribute__",
                        hostile_runtime_getattribute,
                    )
                elif mutation == "record-slot-descriptor":
                    attack.setattr(
                        record_type,
                        "request_id",
                        HostileDescriptor(target_request_id),
                    )
                elif mutation == "record-getattribute":
                    attack.setattr(
                        record_type,
                        "__getattribute__",
                        hostile_record_getattribute,
                    )
                elif mutation == "hostile-records-container":
                    restored_runtime_slot = ("_records", runtime._records)
                    object.__setattr__(runtime, "_records", HostileContainer())
                elif mutation == "hostile-runtime-generation":
                    restored_runtime_slot = ("_generation", runtime._generation)
                    object.__setattr__(runtime, "_generation", HostileValue())
                elif mutation in ("hostile-pending-owner", "hostile-pending-lease"):
                    reservation = next(iter(runtime._pending))
                    owner_sid, lease_id = runtime._pending[reservation]
                    runtime._pending[reservation] = (
                        HostileValue() if mutation.endswith("owner") else owner_sid,
                        HostileValue() if mutation.endswith("lease") else lease_id,
                    )
                elif mutation == "hostile-record-request-id":
                    object.__setattr__(target_record, "request_id", HostileValue())
                elif mutation == "hostile-record-lease-id":
                    object.__setattr__(target_record, "lease_id", HostileValue())
                elif mutation == "hostile-record-identity-container":
                    object.__setattr__(
                        target_record, "identity", HostileContainer(),
                    )
                elif mutation == "hostile-active-records-container":
                    restored_runtime_slot = (
                        "_active_records", runtime._active_records,
                    )
                    object.__setattr__(
                        runtime, "_active_records", HostileContainer(),
                    )
                else:
                    raise AssertionError(case)

            runtime_app.state.classifier_profile_attack = {
                "stage": stage,
                "request_id": target_request_id,
                "code": classifier.__code__,
                "event": "return",
                "install": install_native_drift,
                "fired": [],
            }
            if stage == "prepare":
                denied = _call(
                    client,
                    "harbour_context_prepare",
                    {
                        "chat_session_id": "chat-owned",
                        "selection": _selection_wire(runtime_app),
                        "page_bytes": 16_384,
                    },
                    rpc_id=496,
                    proof=True,
                    headers={"x-profile-classifier-attack": "1"},
                )
                origin = runtime_app.state.classifier_prepare_origin
                target_request_id = origin[6]
                target_lease = origin[4]
                target_binding = origin[5]
                target_lease_id = origin[7]
            else:
                denied = _call(
                    client,
                    "harbour_context_manifest",
                    {"request_id": target_request_id},
                    rpc_id=497,
                    headers={"x-profile-classifier-attack": "1"},
                )
            assert runtime_app.state.classifier_profile_attack["fired"]

        if restored_runtime_slot is not None:
            object.__setattr__(
                runtime, restored_runtime_slot[0], restored_runtime_slot[1],
            )
        assert denied.status_code == 403
        assert denied.content == b'{"error":"context_request_unavailable"}'
        assert denied.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert fake_calls == []
        assert target_request_id not in runtime._records
        assert target_request_id not in service._entries
        assert target_lease_id not in runtime._lease_ids
        assert runtime._pending == {}
        assert runtime._active_records == {}
        assert runtime._active_threads == {}
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(target_lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(target_binding)
        assert runtime._records.get(unrelated_request_id) is unrelated_record
        assert unrelated_request_id in service._entries
        assert unrelated_record.lease_id in runtime._lease_ids
        assert authority.recheck_lease(unrelated_record.lease) is unrelated_record.binding
        _shutdown(runtime_app)


def test_each_content_page_uses_a_fresh_constant_proof_pair_and_preserves_partial(
        runtime_app, monkeypatch):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        _browser_ack(client, request_id)
        runtime = runtime_app.state.request_runtime
        record = runtime._records[request_id]
        capability = authority_module._acquire_native_lease_capability(
            runtime._authority, record.lease,
        )
        original = McpBearerProof.matches_hash
        page_calls = []
        exit_checks = []

        def counted(proof, persisted_hash):
            page_calls.append(id(proof))
            matched = original(proof, persisted_hash)
            if len(page_calls) == 2:
                assert runtime._records.get(request_id) is record
                assert capability.terminal_reason() is None
                exit_checks.append(request_id)
            return matched

        monkeypatch.setattr(McpBearerProof, "matches_hash", counted)
        page_calls.clear()
        first = _call(
            client, "harbour_context_page", {"request_id": request_id}, rpc_id=501,
        )
        assert first.status_code == 200
        assert len(page_calls) == 2
        assert exit_checks == [request_id]
        first_result = first.json()["result"]
        assert first_result["complete"] is False
        assert request_id in runtime._records
        assert runtime_app.state.fixture["authority"].recheck_lease(record.lease) is record.binding
        cursor = first_result["content_cursor"]
        pages = 1
        while True:
            page_calls.clear()
            exit_checks.clear()
            response = _call(
                client,
                "harbour_context_page",
                {"request_id": request_id, "content_cursor": cursor},
                rpc_id=501 + pages,
            )
            assert response.status_code == 200
            assert len(page_calls) == 2
            assert exit_checks == [request_id]
            assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
            result = response.json()["result"]
            pages += 1
            if result["complete"]:
                assert request_id not in runtime._records
                assert request_id not in runtime_app.state.service._entries
                break
            assert request_id in runtime._records
            cursor = result["content_cursor"]
            assert type(cursor) is str and cursor
            assert pages < 100
        _shutdown(runtime_app)


def test_content_final_full_proof_failure_returns_no_page_and_cleans_owned_record(
        runtime_app, monkeypatch):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        _browser_ack(client, request_id)
        unrelated_id = _prepare(client)
        original = McpBearerProof.matches_hash
        calls = []

        def fail_only_at_publication_exit(proof, persisted_hash):
            calls.append(id(proof))
            if len(calls) == 2:
                return False
            return original(proof, persisted_hash)

        monkeypatch.setattr(
            McpBearerProof, "matches_hash", fail_only_at_publication_exit,
        )
        response = _call(
            client, "harbour_context_page", {"request_id": request_id}, rpc_id=503,
        )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert len(calls) == 2
        assert request_id not in runtime_app.state.request_runtime._records
        assert request_id not in runtime_app.state.service._entries
        assert unrelated_id in runtime_app.state.request_runtime._records
        assert unrelated_id in runtime_app.state.service._entries
        assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
        _shutdown(runtime_app)


@pytest.mark.parametrize("target", [
    "_McpContentPublicationVerifier",
    "_open_mcp_content_publication",
    "_CONTENT_PUBLICATION_CONTEXT",
    "_CONTENT_PUBLICATION_OWNER_CONTEXT",
    "_CONTENT_PUBLICATIONS",
    "_content_publication_proof_mode",
    "_poison_active_registered_content_publication",
    "_register_content_publication",
    "_content_publication_control_snapshot",
    "_seed_content_publication_hash",
    "_finalize_content_publication",
    "_close_content_publication_control",
    "_content_publication_dispatch",
])
def test_content_publication_pin_substitution_is_rejected_before_record_claim(
        runtime_app, monkeypatch, target):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        _browser_ack(client, request_id)
        with monkeypatch.context() as patch:
            patch.setattr(authority_module, target, object())
            response = _call(
                client,
                "harbour_context_page",
                {"request_id": request_id},
                rpc_id=504,
            )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert request_id in runtime_app.state.request_runtime._records
        assert request_id in runtime_app.state.service._entries
        _shutdown(runtime_app)


def test_close_on_genuine_prepare_return_releases_every_owned_partial_allocation(runtime_app):
    with TestClient(runtime_app) as client:
        response = _call(
            client,
            "harbour_context_prepare",
            {
                "chat_session_id": "chat-owned",
                "selection": _selection_wire(runtime_app),
                "page_bytes": 16_384,
            },
            rpc_id=7,
            proof=True,
            headers={"x-close-on-prepare-return": "1"},
        )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert runtime_app.state.prepare_return_close_count == 1
        allocated_id = runtime_app.state.prepare_return_request_id

        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime_app.state.fixture["authority"]
        coordinator = runtime_app.state.request_coordinator
        cursor = runtime_app.state.request_cursor
        assert runtime._closed is True and runtime._records == {}
        assert allocated_id not in service._entries
        assert service._entries == {}
        assert authority._leases == {}
        assert authority._cancelled_leases == {}
        assert authority._bindings == {}
        assert service._closed is False
        assert coordinator._closed is False
        assert cursor._closed is False
        assert coordinator._service is service
        assert coordinator._authority is authority
        assert coordinator._cursor is cursor
        _shutdown(runtime_app)


@pytest.mark.parametrize(
    "replacement_kind",
    ("valid-nonexistent", "existing-unrelated"),
    ids=("valid-nonexistent", "existing-unrelated"),
)
def test_mutated_advertised_prepare_id_cleans_only_the_genuine_allocation(
    runtime_app, replacement_kind,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=7)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        authority = runtime_app.state.fixture["authority"]
        unrelated_record = runtime._records[unrelated_request_id]
        runtime_app.state.prepare_return_replacement_id = (
            "r." + "x" * 43
            if replacement_kind == "valid-nonexistent"
            else unrelated_request_id
        )
        response = _call(
            client,
            "harbour_context_prepare",
            {
                "chat_session_id": "chat-owned",
                "selection": _selection_wire(runtime_app),
                "page_bytes": 16_384,
            },
            rpc_id=8,
            proof=True,
            headers={"x-mutate-prepared-id-on-return": "1"},
        )
        assert response.status_code == 403
        assert response.content == b'{"error":"context_request_unavailable"}'
        assert runtime_app.state.advertised_id_return_mutation_count == 1
        allocated_id = runtime_app.state.advertised_id_return_request_id
        origin = runtime_app.state.advertised_id_return_origin
        target_lease = origin[4]
        target_binding = origin[5]
        target_lease_id = origin[7]

        assert runtime._closed is False
        assert set(runtime._records) == {unrelated_request_id}
        assert allocated_id not in service._entries
        assert set(service._entries) == {unrelated_request_id}
        assert runtime._pending == {}
        assert target_lease_id not in runtime._lease_ids
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_lease(target_lease)
        with pytest.raises(authority_module.McpContextAuthorityError):
            authority.recheck_binding(target_binding)
        assert authority.recheck_lease(unrelated_record.lease) is unrelated_record.binding
        assert authority._cancelled_leases == {}
        _shutdown(runtime_app)


def test_cancel_freezes_first_releases_before_return_and_encoder_failure_is_inert(
    runtime_app, monkeypatch,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        runtime = runtime_app.state.request_runtime
        identity = _identity(runtime_app)
        raw = _wire("harbour_context_cancel", {"request_id": request_id}, 31)
        calls: list[str] = []
        target_codes = {
            McpContextPackCoordinator.cancel.__code__: "cancel",
            runtime_app.state.fixture["authority"].release_read_lease.__func__.__code__: "lease",
            runtime_app.state.fixture["authority"].release_binding.__func__.__code__: "binding",
        }
        old_profile = sys.getprofile()

        def profile(frame, event, _arg):
            if event == "call" and frame.f_code in target_codes:
                calls.append(target_codes[frame.f_code])
            return profile

        original_dumps = contract_module.json.dumps

        def broken_encoder(*_args, **_kwargs):
            raise RuntimeError("test-only encoder failure")

        with monkeypatch.context() as fault:
            fault.setattr(contract_module.json, "dumps", broken_encoder)
            with pytest.raises(McpContextRequestRuntimeError) as error:
                runtime.handle(raw, identity=identity)
        assert error.value.code == MCP_CONTEXT_REQUEST_ERROR
        assert request_id in runtime._records
        assert request_id in runtime_app.state.service._entries
        assert calls == []
        assert contract_module.json.dumps is original_dumps

        sys.setprofile(profile)
        try:
            frozen = runtime.handle(raw, identity=identity)
        finally:
            sys.setprofile(old_profile)
        assert json.loads(frozen)["result"] == {"cancelled": True}
        assert calls[:3] == ["cancel", "lease", "binding"]
        assert request_id not in runtime._records
        assert request_id not in runtime_app.state.service._entries
        _shutdown(runtime_app)


@pytest.mark.parametrize("case", ["identity", "proof", "foreign"])
def test_wrong_identity_or_proof_is_rejected_without_touching_owned_record(runtime_app, case):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        headers = {"x-owner-alias": "other"} if case == "identity" else {}
        response = _call(
            client,
            "harbour_context_manifest",
            {"request_id": ("r." + "z" * 43) if case == "foreign" else request_id},
            proof=case == "proof",
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert request_id in runtime_app.state.request_runtime._records
        assert request_id in runtime_app.state.service._entries
        _shutdown(runtime_app)


def test_wrong_authenticated_content_cursor_is_terminal_without_egress(runtime_app):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        runtime = runtime_app.state.request_runtime
        record = runtime._records[request_id]
        capability = authority_module._acquire_native_lease_capability(
            runtime._authority,
            record.lease,
        )
        _browser_ack(client, request_id)
        first = _call(
            client, "harbour_context_page", {"request_id": request_id}, rpc_id=21,
        )
        assert first.status_code == 200
        cursor = first.json()["result"]["content_cursor"]
        assert type(cursor) is str and cursor.startswith("c1.")
        replacement = "A" if cursor[-2] != "A" else "B"
        response = _call(
            client,
            "harbour_context_page",
            {"request_id": request_id, "content_cursor": cursor[:-2] + replacement + cursor[-1:]},
            rpc_id=22,
        )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert capability.terminal_reason() is not None
        assert request_id not in runtime_app.state.request_runtime._records
        assert request_id not in runtime_app.state.service._entries
        assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
        _shutdown(runtime_app)


def test_expiry_is_terminal_and_releases_owned_authority(runtime_app):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        _browser_ack(client, request_id)
        assert client.post("/test/clock_near").status_code == 200
        assert client.post("/test/clock_advance").status_code == 200
        response = _call(
            client, "harbour_context_manifest", {"request_id": request_id},
        )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert request_id not in runtime_app.state.request_runtime._records
        assert request_id not in runtime_app.state.service._entries
        _shutdown(runtime_app)


def test_callback_reentry_and_concurrent_duplicate_stop_before_second_publication(runtime_app):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        _browser_ack(client, request_id)
        response = _call(
            client,
            "harbour_context_page",
            {"request_id": request_id},
            headers={"x-reenter": "1"},
        )
        assert response.status_code == 200
        assert runtime_app.state.runtime_reentry_codes == (MCP_CONTEXT_REQUEST_ERROR,)

        if response.json()["result"]["complete"]:
            request_id = _prepare(client, rpc_id=2)
            _browser_ack(client, request_id)
        runtime = runtime_app.state.request_runtime
        identity = _identity(runtime_app)
        raw = _wire("harbour_context_manifest", {"request_id": request_id}, 41)
        entered = threading.Event()
        release = threading.Event()
        result: list[object] = []
        code = McpContextPackCoordinator.publish_manifest.__code__

        main_thread = threading.current_thread()

        def run_paused_operation(phase_entered, phase_release, phase_completions, operation, verify):
            monitoring = getattr(sys, "monitoring", None)
            monitor_tool_id = None
            monitor_event = None
            previous_profile = None
            profile_installed = False
            helper_started = False
            helper_joined_in_callback = False
            callback_seen = False
            main_completion = []
            callback_failures = []
            handshake_failures = []
            cleanup_failures = []
            primary_failure = None

            def helper_attempt():
                try:
                    assert phase_entered.wait(10)
                    try:
                        value = operation()
                    except BaseException as error:
                        phase_completions.append((False, error))
                    else:
                        phase_completions.append((True, value))
                except BaseException as error:
                    handshake_failures.append(error)
                finally:
                    phase_release.set()

            helper = threading.Thread(target=helper_attempt)

            def pause_original():
                nonlocal callback_seen, helper_joined_in_callback
                if callback_seen:
                    return
                callback_seen = True
                try:
                    phase_entered.set()
                    assert phase_release.wait(10)
                    helper_joined_in_callback = True
                    helper.join(20)
                    assert not helper.is_alive()
                except BaseException as error:
                    callback_failures.append(error)
                    raise

            def monitoring_callback(code, instruction_offset):
                if code is code and threading.current_thread() is main_thread:
                    pause_original()

            def profile_callback(frame, event, arg):
                if (
                    event == "call"
                    and frame.f_code is code
                    and threading.current_thread() is main_thread
                ):
                    pause_original()
                return profile_callback

            try:
                if monitoring is None:
                    previous_profile = sys.getprofile()
                    sys.setprofile(profile_callback)
                    profile_installed = True
                else:
                    monitor_event = monitoring.events.PY_START
                    for candidate_tool_id in range(6):
                        if monitoring.get_tool(candidate_tool_id) is not None:
                            continue
                        try:
                            monitoring.use_tool_id(candidate_tool_id, 'odysseus-test-manifest-entry')
                        except ValueError:
                            continue
                        monitor_tool_id = candidate_tool_id
                        break
                    assert monitor_tool_id is not None
                    monitoring.register_callback(monitor_tool_id, monitor_event, monitoring_callback)
                    monitoring.set_local_events(monitor_tool_id, code, monitor_event)
                helper.start()
                helper_started = True
                assert threading.current_thread() is main_thread
                try:
                    value = runtime.handle(raw, identity=identity)
                except BaseException as error:
                    main_completion.append((False, error))
                else:
                    main_completion.append((True, value))
            except BaseException as error:
                primary_failure = error
            finally:
                if monitor_tool_id is not None:
                    try:
                        monitoring.set_local_events(monitor_tool_id, code, 0)
                    except BaseException as error:
                        cleanup_failures.append(error)
                    try:
                        monitoring.register_callback(monitor_tool_id, monitor_event, None)
                    except BaseException as error:
                        cleanup_failures.append(error)
                    try:
                        monitoring.free_tool_id(monitor_tool_id)
                    except BaseException as error:
                        cleanup_failures.append(error)
                elif monitoring is None and profile_installed:
                    try:
                        sys.setprofile(previous_profile)
                    except BaseException as error:
                        cleanup_failures.append(error)
                if helper_started and not helper_joined_in_callback:
                    try:
                        helper.join(20)
                        assert not helper.is_alive()
                    except BaseException as error:
                        cleanup_failures.append(error)
            if primary_failure is None and callback_failures:
                primary_failure = callback_failures[0]
            if primary_failure is None and handshake_failures:
                primary_failure = handshake_failures[0]
            if primary_failure is None:
                try:
                    verify(main_completion, phase_completions)
                except BaseException as error:
                    primary_failure = error
            if primary_failure is not None:
                raise primary_failure
            if cleanup_failures:
                raise cleanup_failures[0]

        def verify_duplicate(main_completion, helper_completions):
            assert len(main_completion) == 1
            assert main_completion[0][0] is True
            assert type(main_completion[0][1]) is bytes
            assert len(helper_completions) == 1
            assert helper_completions[0][0] is False
            assert type(helper_completions[0][1]) is McpContextRequestRuntimeError
            assert helper_completions[0][1].code == MCP_CONTEXT_REQUEST_ERROR
            assert request_id in runtime._records

        run_paused_operation(entered, release, result, lambda: runtime.handle(raw, identity=identity), verify_duplicate)
        close_entered = threading.Event()
        close_release = threading.Event()
        close_completions = []

        def verify_close(main_completion, helper_completions):
            assert len(main_completion) == 1
            assert main_completion[0][0] is False
            assert type(main_completion[0][1]) is McpContextRequestRuntimeError
            assert main_completion[0][1].code == MCP_CONTEXT_REQUEST_ERROR
            assert len(helper_completions) == 1
            assert helper_completions[0][0] is True
            assert helper_completions[0][1] is None

        run_paused_operation(close_entered, close_release, close_completions, runtime.close, verify_close)
        assert request_id not in runtime._records
        assert request_id not in runtime_app.state.service._entries
        assert runtime_app.state.service._closed is False
        assert runtime_app.state.request_cursor._closed is False
        runtime_app.state.request_coordinator.close()
        runtime_app.state.request_cursor.close()


def test_hostile_class_and_instance_substitution_are_rejected_before_fake_runs(
    runtime_app, monkeypatch,
):
    with TestClient(runtime_app) as client:
        request_id = _prepare(client)
        calls: list[str] = []

        def code_trap(*args, **kwargs):
            globals()["_runtime_integrity_trap_calls"].append("code")
            raise AssertionError("hostile code executed")

        def fake(*_args, **_kwargs):
            calls.append("fake")
            raise AssertionError("substitute ran")

        with monkeypatch.context() as attack:
            attack.setattr(McpContextPackCoordinator, "publish_manifest", fake)
            response = _call(
                client, "harbour_context_manifest", {"request_id": request_id},
            )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert calls == []
        assert request_id in runtime_app.state.request_runtime._records

        class HostileCapability:
            def __init__(self, *args, **kwargs):
                calls.append("capability")
                raise AssertionError("hostile capability executed")

        module_substitutions = (
            ("_acquire_native_lease_capability", fake),
            ("_NativeLeaseCapability", HostileCapability),
            ("_NATIVE_LEASE_CANCELLED", object()),
        )
        for name, replacement in module_substitutions:
            with monkeypatch.context() as attack:
                attack.setitem(authority_module.__dict__, name, replacement)
                response = _call(
                    client,
                    contract_module.MCP_CONTEXT_METHODS[1],
                    {"request_id": request_id},
                )
            assert response.status_code == 403
            assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
            assert calls == []
            assert request_id in runtime_app.state.request_runtime._records

        class HostileLeaseSlot:
            def __get__(self, instance, owner):
                calls.append("slot")
                raise AssertionError("hostile slot executed")

            def __set__(self, instance, value):
                calls.append("slot-set")
                raise AssertionError("hostile slot executed")

        acquirer = authority_module._acquire_native_lease_capability
        state_reader = authority_module._read_native_lease_state
        state_descriptor = state_reader.__defaults__[0]
        state_slot_class = state_descriptor.__objclass__
        capability_class = authority_module._NativeLeaseCapability
        capability_cancel = capability_class.__dict__["cancel"]
        native_state_class = authority_module._NativeLeaseState
        lease_class = authority_module.McpContextReadLease
        binding_class = authority_module.McpContextBinding
        wrapper_class = authority_module.McpContextBearerProof
        native_proof_class = McpBearerProof
        binding_snapshot_members = (
            "bearer_proof",
            "token_id",
            "owner_alias",
            "owner_subject_id",
            "scope",
            "cache_generation",
            "security_action_revision",
            "chat_session_id",
            "process_epoch",
            "audience",
            "_McpContextBinding__proof",
            "_McpContextBinding__token_id",
            "_McpContextBinding__owner_alias",
            "_McpContextBinding__owner_subject_id",
            "_McpContextBinding__scope",
            "_McpContextBinding__cache_generation",
            "_McpContextBinding__security_action_revision",
            "_McpContextBinding__chat_session_id",
            "_McpContextBinding__process_epoch",
            "_McpContextBinding__audience",
        )

        default_substitutions = (
            (acquirer, (fake,)),
            (state_reader, (HostileLeaseSlot(),)),
            (capability_cancel, (fake,)),
        )
        for target, replacement in default_substitutions:
            with monkeypatch.context() as attack:
                attack.setattr(target, "__defaults__", replacement)
                response = _call(
                    client,
                    contract_module.MCP_CONTEXT_METHODS[1],
                    {"request_id": request_id},
                )
            assert response.status_code == 403
            assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
            assert calls == []
            assert request_id in runtime_app.state.request_runtime._records

        code_targets = (
            acquirer,
            state_reader,
            authority_module._NativeLeaseCapability.terminal_reason,
            capability_cancel,
        )
        for target in code_targets:
            assert target.__code__.co_freevars == code_trap.__code__.co_freevars == ()
            with monkeypatch.context() as attack:
                attack.setitem(
                    target.__globals__,
                    "_runtime_integrity_trap_calls",
                    calls,
                )
                attack.setattr(target, "__code__", code_trap.__code__)
                response = _call(
                    client,
                    contract_module.MCP_CONTEXT_METHODS[1],
                    {"request_id": request_id},
                )
            assert response.status_code == 403
            assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
            assert calls == []
            assert request_id in runtime_app.state.request_runtime._records

        hostile_members = (
            (capability_class, "terminal_reason", fake),
            (capability_class, "cancel", fake),
            (
                capability_class,
                "state",
                HostileLeaseSlot(),
            ),
            (capability_class, "lease", HostileLeaseSlot()),
            (capability_class, "key", HostileLeaseSlot()),
            (capability_class, "record", HostileLeaseSlot()),
            (native_state_class, "lock", HostileLeaseSlot()),
            (native_state_class, "leases", HostileLeaseSlot()),
            (native_state_class, "cancelled_leases", HostileLeaseSlot()),
            (
                state_slot_class,
                "_NativeLeaseStateSlot__native_lease_state",
                HostileLeaseSlot(),
            ),
            (capability_class, "__init__", fake),
            (capability_class, "__new__", staticmethod(fake)),
            (capability_class, "__getattribute__", fake),
            (capability_class, "__getattr__", fake),
            (native_state_class, "__getattribute__", fake),
            (native_state_class, "__getattr__", fake),
            (state_slot_class, "__getattribute__", fake),
            (state_slot_class, "__getattr__", fake),
            (lease_class, "binding", HostileLeaseSlot()),
            (lease_class, "request_id", HostileLeaseSlot()),
            (lease_class, "expires_at", HostileLeaseSlot()),
            (lease_class, "_McpContextReadLease__binding", HostileLeaseSlot()),
            (lease_class, "__getattribute__", fake),
            (binding_class, "__getattribute__", fake),
            *tuple(
                (binding_class, name, HostileLeaseSlot())
                for name in binding_snapshot_members
            ),
            (wrapper_class, "_McpContextBearerProof__native", HostileLeaseSlot()),
            (wrapper_class, "_McpContextBearerProof__issuer", HostileLeaseSlot()),
            (native_proof_class, "_McpBearerProof__raw", HostileLeaseSlot()),
            (native_proof_class, "_McpBearerProof__issuer", HostileLeaseSlot()),
            (native_proof_class, "__getattribute__", fake),
            (native_proof_class, "_is_issued", fake),
        )
        for target, name, replacement in hostile_members:
            with monkeypatch.context() as attack:
                attack.setattr(target, name, replacement, raising=False)
                response = _call(
                    client,
                    contract_module.MCP_CONTEXT_METHODS[1],
                    {"request_id": request_id},
                )
            assert response.status_code == 403
            assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
            assert calls == []
            assert request_id in runtime_app.state.request_runtime._records

        runtime = runtime_app.state.request_runtime
        borrowed_authority = runtime._authority
        borrowed_service = runtime_app.state.service
        borrowed_cursor = runtime_app.state.request_cursor
        borrowed_coordinator = runtime_app.state.request_coordinator
        callback_capabilities = {}

        def run_callback_attack(mutate, rpc_id):
            callback_request_id = _prepare(client, rpc_id=rpc_id)
            _browser_ack(client, callback_request_id)
            manifest = _call(
                client,
                contract_module.MCP_CONTEXT_METHODS[1],
                {"request_id": callback_request_id},
                rpc_id=rpc_id + 1,
            )
            assert manifest.status_code == 200
            manifest_result = manifest.json()["result"]
            assert set(manifest_result) == {
                "schema",
                "pack_id",
                "items",
                "manifest_cursor",
                "complete",
                "read_only",
                "authority_granted",
                "send_performed",
            }
            assert manifest_result["complete"] is True
            assert manifest_result["manifest_cursor"] is None
            page = _call(
                client,
                contract_module.MCP_CONTEXT_METHODS[2],
                {"request_id": callback_request_id},
                rpc_id=rpc_id + 2,
            )
            assert page.status_code == 200
            page_result = page.json()["result"]
            assert page_result["complete"] is False
            content_cursor = page_result["content_cursor"]
            assert type(content_cursor) is str
            assert content_cursor
            assert content_cursor.startswith("c1.")
            assert callback_request_id in runtime._records
            assert callback_request_id in borrowed_service._entries
            raw = _wire(
                contract_module.MCP_CONTEXT_METHODS[2],
                {
                    "request_id": callback_request_id,
                    "content_cursor": content_cursor,
                },
                rpc_id + 3,
            )
            identity = _identity(runtime_app)
            callback_record = runtime._records[callback_request_id]
            captured_capability = acquirer(
                borrowed_authority,
                callback_record.lease,
                state_reader,
            )
            publish_code = McpContextPackCoordinator.publish_content.__code__
            previous_profile = sys.getprofile()
            callback_seen = False

            def profile(frame, event, arg):
                nonlocal callback_seen
                if not callback_seen and event == "call" and frame.f_code is publish_code:
                    callback_seen = True
                    mutate(captured_capability)
                return profile

            try:
                sys.setprofile(profile)
                with pytest.raises(McpContextRequestRuntimeError) as caught:
                    runtime.handle(raw, identity=identity)
            finally:
                sys.setprofile(previous_profile)
            assert callback_seen
            assert caught.value.code == MCP_CONTEXT_REQUEST_ERROR
            callback_capabilities[callback_request_id] = captured_capability
            return callback_request_id

        terminal_reader = authority_module._NativeLeaseCapability.terminal_reason
        callback_substitutions = (
            (terminal_reader, "__code__", code_trap.__code__),
            (capability_cancel, "__code__", code_trap.__code__),
            (acquirer, "__defaults__", (fake,)),
            (capability_cancel, "__defaults__", (fake,)),
            (capability_class, "terminal_reason", fake),
            (capability_class, "cancel", fake),
            (
                capability_class,
                "state",
                HostileLeaseSlot(),
            ),
            (capability_class, "lease", HostileLeaseSlot()),
            (capability_class, "key", HostileLeaseSlot()),
            (capability_class, "record", HostileLeaseSlot()),
            (native_state_class, "lock", HostileLeaseSlot()),
            (native_state_class, "leases", HostileLeaseSlot()),
            (native_state_class, "cancelled_leases", HostileLeaseSlot()),
            (native_state_class, "__getattribute__", fake),
            (
                state_slot_class,
                "_NativeLeaseStateSlot__native_lease_state",
                HostileLeaseSlot(),
            ),
            (capability_class, "__init__", fake),
            (capability_class, "__new__", staticmethod(fake)),
            (capability_class, "__getattribute__", fake),
            (lease_class, "binding", HostileLeaseSlot()),
            (lease_class, "__getattribute__", fake),
            (lease_class, "_McpContextReadLease__binding", HostileLeaseSlot()),
            (binding_class, "__getattribute__", fake),
            *tuple(
                (binding_class, name, HostileLeaseSlot())
                for name in binding_snapshot_members
            ),
            (wrapper_class, "_McpContextBearerProof__native", HostileLeaseSlot()),
            (wrapper_class, "_McpContextBearerProof__issuer", HostileLeaseSlot()),
            (native_proof_class, "_McpBearerProof__raw", HostileLeaseSlot()),
            (native_proof_class, "_McpBearerProof__issuer", HostileLeaseSlot()),
            (native_proof_class, "__getattribute__", fake),
            (native_proof_class, "_is_issued", fake),
            (authority_module.__dict__, "_NATIVE_LEASE_CANCELLED", object()),
        )
        for index, (target, name, replacement) in enumerate(callback_substitutions):
            with monkeypatch.context() as attack:
                if name == "__code__":
                    assert target.__code__.co_freevars == replacement.co_freevars == ()
                    attack.setitem(
                        target.__globals__,
                        "_runtime_integrity_trap_calls",
                        calls,
                    )

                def mutate(_captured_capability):
                    if target is authority_module.__dict__:
                        attack.setitem(target, name, replacement)
                    else:
                        attack.setattr(target, name, replacement, raising=False)

                callback_request_id = run_callback_attack(mutate, 900 + index * 10)
            assert calls == []
            captured_capability = callback_capabilities.pop(callback_request_id)
            assert (
                terminal_reader(captured_capability)
                is authority_module._NATIVE_LEASE_CANCELLED
            )
            assert callback_request_id not in runtime._records
            assert callback_request_id not in borrowed_service._entries
            assert request_id in runtime._records
            assert request_id in borrowed_service._entries
            assert runtime._authority is borrowed_authority
            assert runtime_app.state.service is borrowed_service
            assert runtime_app.state.request_cursor is borrowed_cursor
            assert runtime_app.state.request_coordinator is borrowed_coordinator
            assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
            assert authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT.get() is None

        preserved_reason = (McpContextRequestRuntimeError, MCP_CONTEXT_REQUEST_ERROR)

        def cancel_with_reason(captured_capability):
            capability_cancel(captured_capability, preserved_reason)

        preserved_request_id = run_callback_attack(cancel_with_reason, 9000)
        preserved_capability = callback_capabilities.pop(preserved_request_id)
        assert terminal_reader(preserved_capability) is preserved_reason
        assert calls == []
        assert preserved_request_id not in runtime._records
        assert preserved_request_id not in borrowed_service._entries
        assert request_id in runtime._records
        assert request_id in borrowed_service._entries
        assert runtime._authority is borrowed_authority
        assert runtime_app.state.service is borrowed_service
        assert runtime_app.state.request_cursor is borrowed_cursor
        assert runtime_app.state.request_coordinator is borrowed_coordinator
        assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
        assert authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT.get() is None

        fresh_request_id = _prepare(client)
        assert fresh_request_id in runtime._records
        assert fresh_request_id in borrowed_service._entries

        service = runtime_app.state.service
        with monkeypatch.context() as attack:
            attack.setattr(service, "prepare_intent", fake)
            response = _call(
                client,
                "harbour_context_prepare",
                {"chat_session_id": "chat-owned", "selection": []},
                proof=True,
            )
        assert response.status_code == 403
        assert calls == []

        runtime = runtime_app.state.request_runtime
        authority = runtime_app.state.fixture["authority"]
        service = runtime_app.state.service
        producer = runtime_app.state.fixture["combined"]
        cursor = runtime_app.state.request_cursor
        coordinator = runtime_app.state.request_coordinator

        class CoordinatedServiceReplacement:
            def __init__(self):
                self._authority = authority
                self._producer = producer

            def _reject_authority_reentry(self):
                calls.append("replacement-service-callback")
                raise AssertionError("replacement service ran")

        replacement_service = CoordinatedServiceReplacement()
        replacement_coordinator = SimpleNamespace(
            _authority=authority,
            _service=replacement_service,
            _producer=producer,
            _cursor=cursor,
        )
        object.__setattr__(runtime, "_service", replacement_service)
        object.__setattr__(runtime, "_coordinator", replacement_coordinator)
        try:
            response = _call(
                client,
                "harbour_context_prepare",
                {"chat_session_id": "chat-owned", "selection": []},
                proof=True,
            )
        finally:
            object.__setattr__(runtime, "_service", service)
            object.__setattr__(runtime, "_coordinator", coordinator)
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert calls == []

        producer_type = type(producer)
        authority_slot = producer_type.__dict__["_mcp_authority"]

        class HostileAuthoritySlot:
            def __get__(self, _instance, _owner):
                calls.append("producer-slot")
                raise AssertionError("substitute ran")

        with monkeypatch.context() as attack:
            attack.setattr(producer_type, "_mcp_authority", HostileAuthoritySlot())
            response = _call(
                client,
                "harbour_context_prepare",
                {"chat_session_id": "chat-owned", "selection": []},
                proof=True,
            )
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert calls == []

        class HostileAuthority:
            def __eq__(self, _other):
                calls.append("producer-authority")
                raise AssertionError("substitute ran")

        authority_slot.__set__(producer, HostileAuthority())
        try:
            response = _call(
                client,
                "harbour_context_prepare",
                {"chat_session_id": "chat-owned", "selection": []},
                proof=True,
            )
        finally:
            authority_slot.__set__(producer, authority)
        assert response.status_code == 403
        assert response.json() == {"error": MCP_CONTEXT_REQUEST_ERROR}
        assert calls == []
        _shutdown(runtime_app)


def test_publication_cleanup_helper_integrity_rejects_static_and_call_time_drift(
    runtime_app, monkeypatch,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=10_000)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        borrowed_authority = runtime._authority
        borrowed_service = service
        borrowed_cursor = runtime_app.state.request_cursor
        borrowed_coordinator = runtime_app.state.request_coordinator
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        anchor = confirmation_module._publication_cleanup_anchor
        terminator = confirmation_module._terminate_anchored_publication
        open_publication_code = (
            authority_module._open_mcp_content_publication.__wrapped__.__code__
        )
        acquirer = authority_module._acquire_native_lease_capability
        state_reader = authority_module._read_native_lease_state
        capability_cancel = authority_module._NativeLeaseCapability.__dict__["cancel"]
        terminal_reader = authority_module._NativeLeaseCapability.terminal_reason
        preserved_reason = (McpContextRequestRuntimeError, MCP_CONTEXT_REQUEST_ERROR)
        calls: list[str] = []

        def code_trap(*args, **kwargs):
            globals()["_runtime_cleanup_helper_trap_calls"].append("code")
            raise AssertionError("hostile cleanup helper code executed")

        def fake(*_args, **_kwargs):
            calls.append("fake")
            raise AssertionError("hostile cleanup helper executed")

        def closure_replacement(label):
            marker = label

            def replacement(*_args, **_kwargs):
                calls.append(marker)
                raise AssertionError("hostile closure helper executed")

            assert replacement.__closure__ is not None
            return replacement

        def disclosed_page(rpc_id):
            request_id = _prepare(client, rpc_id=rpc_id)
            _browser_ack(client, request_id)
            manifest = _call(
                client,
                contract_module.MCP_CONTEXT_METHODS[1],
                {"request_id": request_id},
                rpc_id=rpc_id + 1,
            )
            assert manifest.status_code == 200
            assert manifest.json()["result"]["complete"] is True
            first_page = _call(
                client,
                contract_module.MCP_CONTEXT_METHODS[2],
                {"request_id": request_id},
                rpc_id=rpc_id + 2,
            )
            assert first_page.status_code == 200
            first_result = first_page.json()["result"]
            assert first_result["complete"] is False
            cursor = first_result["content_cursor"]
            assert type(cursor) is str and cursor.startswith("c1.")
            return request_id, _wire(
                contract_module.MCP_CONTEXT_METHODS[2],
                {"request_id": request_id, "content_cursor": cursor},
                rpc_id + 3,
            )

        static_request_id, static_raw = disclosed_page(10_100)
        identity = _identity(runtime_app)
        helpers = (
            ("_publication_cleanup_anchor", anchor),
            ("_terminate_anchored_publication", terminator),
        )
        for name, _helper in helpers:
            with monkeypatch.context() as attack:
                attack.setitem(confirmation_module.__dict__, name, fake)
                with pytest.raises(McpContextRequestRuntimeError):
                    runtime.handle(static_raw, identity=identity)
            assert calls == []
            assert static_request_id in runtime._records
            assert static_request_id in service._entries

        for _name, helper in helpers:
            assert helper.__code__.co_freevars == code_trap.__code__.co_freevars == ()
            with monkeypatch.context() as attack:
                attack.setitem(
                    helper.__globals__, "_runtime_cleanup_helper_trap_calls", calls,
                )
                attack.setattr(helper, "__code__", code_trap.__code__)
                with pytest.raises(McpContextRequestRuntimeError):
                    runtime.handle(static_raw, identity=identity)
            assert calls == []
            assert static_request_id in runtime._records
            assert static_request_id in service._entries

        for _name, helper in helpers:
            defaults = helper.__defaults__
            assert type(defaults) is tuple and defaults
            with monkeypatch.context() as attack:
                attack.setattr(
                    helper, "__defaults__", tuple(object() for _ in defaults),
                )
                with pytest.raises(McpContextRequestRuntimeError):
                    runtime.handle(static_raw, identity=identity)
            assert calls == []
            assert static_request_id in runtime._records
            assert static_request_id in service._entries

        for name, helper in helpers:
            assert helper.__closure__ is None
            replacement = closure_replacement(name)
            with monkeypatch.context() as attack:
                attack.setitem(confirmation_module.__dict__, name, replacement)
                with pytest.raises(McpContextRequestRuntimeError):
                    runtime.handle(static_raw, identity=identity)
            assert calls == []
            assert static_request_id in runtime._records
            assert static_request_id in service._entries

        def verify_failed_target(request_id, capability):
            assert terminal_reader(capability) is preserved_reason
            assert request_id not in runtime._records
            assert request_id not in service._entries
            assert unrelated_request_id in runtime._records
            assert unrelated_request_id in service._entries
            assert static_request_id in runtime._records
            assert static_request_id in service._entries
            assert runtime._authority is borrowed_authority
            assert runtime_app.state.service is borrowed_service
            assert runtime_app.state.request_cursor is borrowed_cursor
            assert runtime_app.state.request_coordinator is borrowed_coordinator
            assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
            assert authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT.get() is None
            assert calls == []

        def mutate_helper(attack, kind, name, helper):
            if kind == "module":
                attack.setitem(confirmation_module.__dict__, name, fake)
            elif kind == "code":
                assert helper.__code__.co_freevars == code_trap.__code__.co_freevars == ()
                attack.setitem(
                    helper.__globals__, "_runtime_cleanup_helper_trap_calls", calls,
                )
                attack.setattr(helper, "__code__", code_trap.__code__)
            else:
                defaults = helper.__defaults__
                assert type(defaults) is tuple and defaults
                attack.setattr(
                    helper, "__defaults__", tuple(object() for _ in defaults),
                )

        for index, kind in enumerate(("module", "code", "defaults")):
            request_id, raw = disclosed_page(10_200 + index * 10)
            record = runtime._records[request_id]
            capability = acquirer(borrowed_authority, record.lease, state_reader)
            previous_profile = sys.getprofile()
            helper_seen = False
            with monkeypatch.context() as attack:
                def profile(frame, event, arg):
                    nonlocal helper_seen
                    if not helper_seen and event == "call" and frame.f_code is anchor.__code__:
                        helper_seen = True
                        mutate_helper(attack, kind, "_publication_cleanup_anchor", anchor)
                        capability_cancel(capability, preserved_reason)
                    return profile

                try:
                    sys.setprofile(profile)
                    with pytest.raises(McpContextRequestRuntimeError):
                        runtime.handle(raw, identity=identity)
                finally:
                    sys.setprofile(previous_profile)
            assert helper_seen
            verify_failed_target(request_id, capability)

        retained_permits = []
        original_issue_permit = (
            McpPrivateContextConfirmationService.__dict__["issue_permit"]
        )
        for index, kind in enumerate(("module", "code", "defaults")):
            request_id, raw = disclosed_page(10_300 + index * 10)
            record = runtime._records[request_id]
            capability = acquirer(borrowed_authority, record.lease, state_reader)
            previous_profile = sys.getprofile()
            open_seen = False
            helper_seen = False
            with monkeypatch.context() as attack:
                def profile(frame, event, arg):
                    nonlocal open_seen, helper_seen
                    if not open_seen and event == "call" and frame.f_code is open_publication_code:
                        open_seen = True
                        retained_permits.append(original_issue_permit(service, request_id))
                        capability_cancel(capability, preserved_reason)
                    elif not helper_seen and event == "call" and frame.f_code is terminator.__code__:
                        helper_seen = True
                        mutate_helper(
                            attack, kind, "_terminate_anchored_publication", terminator,
                        )
                    return profile

                try:
                    sys.setprofile(profile)
                    with pytest.raises(McpContextRequestRuntimeError):
                        runtime.handle(raw, identity=identity)
                finally:
                    sys.setprofile(previous_profile)
            assert open_seen and helper_seen
            verify_failed_target(request_id, capability)

        assert len(retained_permits) == 3
        _shutdown(runtime_app)


def test_genuine_cleanup_negative_results_fall_back_without_success_credit(
    runtime_app,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=11_000)
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        borrowed_authority = runtime._authority
        borrowed_service = service
        borrowed_cursor = runtime_app.state.request_cursor
        borrowed_coordinator = runtime_app.state.request_coordinator
        confirmation_module = sys.modules[
            McpPrivateContextConfirmationService.__module__
        ]
        anchor_code = confirmation_module._publication_cleanup_anchor.__code__
        terminator_code = confirmation_module._terminate_anchored_publication.__code__
        open_publication_code = (
            authority_module._open_mcp_content_publication.__wrapped__.__code__
        )
        original_issue_permit = (
            McpPrivateContextConfirmationService.__dict__["issue_permit"]
        )
        acquirer = authority_module._acquire_native_lease_capability
        state_reader = authority_module._read_native_lease_state
        capability_cancel = authority_module._NativeLeaseCapability.__dict__["cancel"]
        terminal_reader = authority_module._NativeLeaseCapability.terminal_reason
        preserved_reason = (McpContextRequestRuntimeError, MCP_CONTEXT_REQUEST_ERROR)
        retained_permits = []

        def disclosed_page(rpc_id):
            request_id = _prepare(client, rpc_id=rpc_id)
            _browser_ack(client, request_id)
            manifest = _call(
                client, contract_module.MCP_CONTEXT_METHODS[1],
                {"request_id": request_id}, rpc_id=rpc_id + 1,
            )
            assert manifest.status_code == 200
            first_page = _call(
                client, contract_module.MCP_CONTEXT_METHODS[2],
                {"request_id": request_id}, rpc_id=rpc_id + 2,
            )
            assert first_page.status_code == 200
            result = first_page.json()["result"]
            assert result["complete"] is False
            return request_id, _wire(
                contract_module.MCP_CONTEXT_METHODS[2],
                {"request_id": request_id, "content_cursor": result["content_cursor"]},
                rpc_id + 3,
            )

        def verify_target_removed(request_id, capability):
            assert terminal_reader(capability) is preserved_reason
            assert request_id not in runtime._records
            assert request_id not in service._entries
            assert unrelated_request_id in runtime._records
            assert unrelated_request_id in service._entries
            assert runtime._authority is borrowed_authority
            assert runtime_app.state.service is borrowed_service
            assert runtime_app.state.request_cursor is borrowed_cursor
            assert runtime_app.state.request_coordinator is borrowed_coordinator
            assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
            assert authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT.get() is None

        request_id, raw = disclosed_page(11_100)
        record = runtime._records[request_id]
        capability = acquirer(borrowed_authority, record.lease, state_reader)
        previous_profile = sys.getprofile()
        anchor_seen = False
        publish_seen = False
        anchor_returns = []
        publish_code = McpContextPackCoordinator.publish_content.__code__

        def wrong_anchor_profile(frame, event, arg):
            nonlocal anchor_seen, publish_seen
            if not anchor_seen and event == "call" and frame.f_code is anchor_code:
                anchor_seen = True
                retained_permits.append(original_issue_permit(service, request_id))
                capability_cancel(capability, preserved_reason)
            elif event == "return" and frame.f_code is anchor_code:
                anchor_returns.append(arg)
            elif event == "call" and frame.f_code is publish_code:
                publish_seen = True
            return wrong_anchor_profile

        try:
            sys.setprofile(wrong_anchor_profile)
            with pytest.raises(McpContextRequestRuntimeError):
                runtime.handle(raw, identity=_identity(runtime_app))
        finally:
            sys.setprofile(previous_profile)
        assert anchor_seen
        assert anchor_returns == [None]
        assert publish_seen is False
        verify_target_removed(request_id, capability)

        request_id, raw = disclosed_page(11_200)
        record = runtime._records[request_id]
        capability = acquirer(borrowed_authority, record.lease, state_reader)
        previous_profile = sys.getprofile()
        open_seen = False
        terminator_seen = False
        terminator_returns = []

        def false_terminator_profile(frame, event, arg):
            nonlocal open_seen, terminator_seen
            if not open_seen and event == "call" and frame.f_code is open_publication_code:
                open_seen = True
                retained_permits.append(original_issue_permit(service, request_id))
                capability_cancel(capability, preserved_reason)
            elif event == "call" and frame.f_code is terminator_code:
                terminator_seen = True
            elif event == "return" and frame.f_code is terminator_code:
                terminator_returns.append(arg)
            return false_terminator_profile

        try:
            sys.setprofile(false_terminator_profile)
            with pytest.raises(McpContextRequestRuntimeError):
                runtime.handle(raw, identity=_identity(runtime_app))
        finally:
            sys.setprofile(previous_profile)
        assert open_seen and terminator_seen
        assert terminator_returns == [False]
        verify_target_removed(request_id, capability)
        assert len(retained_permits) == 2
        _shutdown(runtime_app)


def test_owner_capacity_and_close_release_only_owned_records_and_keep_graph_borrowed(runtime_app):
    with TestClient(runtime_app) as client:
        request_ids = [_prepare(client, rpc_id=index + 1) for index in range(8)]
        response = _call(
            client,
            "harbour_context_prepare",
            {"chat_session_id": "chat-owned", "selection": _selection_wire(runtime_app)},
            proof=True,
            rpc_id=99,
        )
        assert response.status_code == 403
        runtime = runtime_app.state.request_runtime
        service = runtime_app.state.service
        producer = runtime_app.state.fixture["combined"]
        authority = runtime_app.state.fixture["authority"]
        cursor = runtime_app.state.request_cursor
        coordinator = runtime_app.state.request_coordinator
        assert set(request_ids) == set(runtime._records)
        assert set(request_ids) <= set(service._entries)

        runtime.close()
        runtime.close()
        assert runtime._records == {}
        assert all(request_id not in service._entries for request_id in request_ids)
        assert service._closed is False
        assert cursor._closed is False
        assert coordinator._closed is False
        assert coordinator._service is service
        assert coordinator._producer is producer
        assert coordinator._cursor is cursor
        assert coordinator._authority is authority
        authority_slot = type(producer).__dict__["_mcp_authority"]
        assert authority_slot.__get__(producer, type(producer)) is authority
        coordinator.close()
        cursor.close()


def test_original_native_capability_state_callback_is_rejected_without_fake_execution(
    runtime_app, monkeypatch,
):
    with TestClient(runtime_app) as client:
        unrelated_request_id = _prepare(client, rpc_id=12_000)
        runtime = runtime_app.state.request_runtime
        borrowed_authority = runtime._authority
        borrowed_service = runtime_app.state.service
        borrowed_cursor = runtime_app.state.request_cursor
        borrowed_coordinator = runtime_app.state.request_coordinator
        fake_calls: list[str] = []

        class HostileLeaseSlot:
            def __get__(self, instance, owner):
                fake_calls.append("slot")
                raise AssertionError("hostile slot executed")

            def __set__(self, instance, value):
                fake_calls.append("slot-set")
                raise AssertionError("hostile slot executed")

        acquirer = authority_module._acquire_native_lease_capability
        state_reader = authority_module._read_native_lease_state
        capability_class = authority_module._NativeLeaseCapability
        terminal_reader = capability_class.terminal_reason
        original_state_descriptor = capability_class.__dict__["state"]
        target_request_id = _prepare(client, rpc_id=12_100)
        _browser_ack(client, target_request_id)
        manifest = _call(
            client,
            contract_module.MCP_CONTEXT_METHODS[1],
            {"request_id": target_request_id},
            rpc_id=12_101,
        )
        assert manifest.status_code == 200
        manifest_result = manifest.json()["result"]
        assert set(manifest_result) == {
            "schema",
            "pack_id",
            "items",
            "manifest_cursor",
            "complete",
            "read_only",
            "authority_granted",
            "send_performed",
        }
        assert manifest_result["complete"] is True
        assert manifest_result["manifest_cursor"] is None
        first_page = _call(
            client,
            contract_module.MCP_CONTEXT_METHODS[2],
            {"request_id": target_request_id},
            rpc_id=12_102,
        )
        assert first_page.status_code == 200
        first_page_result = first_page.json()["result"]
        assert first_page_result["complete"] is False
        content_cursor = first_page_result["content_cursor"]
        assert type(content_cursor) is str
        assert content_cursor
        assert content_cursor.startswith("c1.")
        assert target_request_id in runtime._records
        assert target_request_id in borrowed_service._entries
        second_page_raw = _wire(
            contract_module.MCP_CONTEXT_METHODS[2],
            {
                "request_id": target_request_id,
                "content_cursor": content_cursor,
            },
            12_103,
        )
        identity = _identity(runtime_app)
        target_record = runtime._records[target_request_id]
        capability = acquirer(
            borrowed_authority,
            target_record.lease,
            state_reader,
        )
        publish_code = McpContextPackCoordinator.publish_content.__code__
        previous_profile = sys.getprofile()
        callback_count = 0

        with monkeypatch.context() as attack:
            def profile(frame, event, arg):
                nonlocal callback_count
                if event == "call" and frame.f_code is publish_code:
                    callback_count += 1
                    if callback_count == 1:
                        attack.setattr(
                            capability_class,
                            "state",
                            HostileLeaseSlot(),
                            raising=False,
                        )
                return profile

            try:
                sys.setprofile(profile)
                with pytest.raises(McpContextRequestRuntimeError) as caught:
                    runtime.handle(second_page_raw, identity=identity)
            finally:
                sys.setprofile(previous_profile)

        assert sys.getprofile() is previous_profile
        assert capability_class.__dict__["state"] is original_state_descriptor
        assert callback_count == 1
        assert type(caught.value) is McpContextRequestRuntimeError
        assert caught.value.code == MCP_CONTEXT_REQUEST_ERROR
        assert fake_calls == []
        assert terminal_reader(capability) is authority_module._NATIVE_LEASE_CANCELLED
        assert target_request_id not in runtime._records
        assert target_request_id not in borrowed_service._entries
        assert unrelated_request_id in runtime._records
        assert unrelated_request_id in borrowed_service._entries
        assert runtime._authority is borrowed_authority
        assert runtime_app.state.service is borrowed_service
        assert runtime_app.state.request_cursor is borrowed_cursor
        assert runtime_app.state.request_coordinator is borrowed_coordinator
        assert authority_module._CONTENT_PUBLICATION_CONTEXT.get() is None
        assert authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT.get() is None
        _shutdown(runtime_app)


def test_genuine_prepare_failure_reports_closed_native_exception_chain(runtime_app):
    schema = "h7_native_prepare_failure_projection_v1"

    def closed_fail(status):
        pytest.fail(json.dumps({"schema": schema, "status": status}), pytrace=False)

    if runtime_app.middleware_stack is not None:
        closed_fail("middleware_already_built")
    runtime_handler, confirmation_handler = (
        runtime_app.exception_handlers.get(McpContextRequestRuntimeError),
        runtime_app.exception_handlers.get(McpPrivateContextConfirmationError),
    )
    if not callable(runtime_handler) or not callable(confirmation_handler):
        closed_fail("typed_handler_unavailable")

    descriptor_specs = (
        (BaseException, "__traceback__"), (BaseException, "__context__"),
        (TracebackType, "tb_frame"), (TracebackType, "tb_lineno"), (TracebackType, "tb_next"),
        (FrameType, "f_code"), (CodeType, "co_filename"),
    )
    descriptors = tuple(owner.__dict__[name] for owner, name in descriptor_specs)
    descriptor_getters = tuple(type(descriptor).__get__ for descriptor in descriptors)

    def native_read(index, value):
        return descriptor_getters[index](
            descriptors[index], value, descriptor_specs[index][0],
        )

    test_code = test_genuine_prepare_failure_reports_closed_native_exception_chain.__code__
    test_bound = max(line for _a, _b, line in test_code.co_lines() if type(line) is int)
    file_specs = (
        (sys.modules[McpContextRequestRuntime.__module__], "runtime", 3455),
        (sys.modules[McpPrivateContextConfirmationService.__module__], "confirmation", 4078),
        (authority_module, "authority", 2504),
        (sys.modules[CombinedContextManifestProducer.__module__], "producer", 864),
        (sys.modules[McpContextPackCoordinator.__module__], "coordinator", 1381),
        (sys.modules[__name__], "runtime_test_fixture", test_bound),
    )
    files = {}
    for module, file_id, line_bound in file_specs:
        filename = module.__file__
        if type(filename) is not str or not filename or type(line_bound) is not int:
            closed_fail("file_pin_unavailable")
        if filename in files:
            closed_fail("file_pin_collision")
        files[filename] = (file_id, line_bound)
    known = (
        (McpContextRequestRuntimeError, "runtime_error"), (McpPrivateContextConfirmationError, "confirmation_error"),
        (McpContextAuthorityError, "authority_error"), (ContextManifestProducerError, "producer_error"),
        (contract_module.McpContextContractError, "contract_error"), (AssertionError, "assertion_error"),
        (AttributeError, "attribute_error"), (KeyError, "key_error"), (PermissionError, "permission_error"),
        (NameError, "name_error"), (UnboundLocalError, "unbound_local_error"),
        (RuntimeError, "builtin_runtime_error"), (TypeError, "type_error"), (ValueError, "value_error"),
    )
    finished = {"schema": schema, "status": "typed_handler_not_observed",
                "capture_count": 0, "multiple_captures": False, "capture_truncated": False}
    captures = 0

    def project(exception):
        contexts = []
        seen_contexts = set()
        total_frames = 0
        context_cycle = context_truncated = False
        frame_cycle = frame_truncated = False
        unknown_class = unknown_location = False
        current = exception
        traceback_node = frame = code = None
        try:
            while current is not None:
                if len(contexts) == 8:
                    context_truncated = True
                    break
                context_id = id(current)
                if context_id in seen_contexts:
                    context_cycle = True
                    break
                seen_contexts.add(context_id)
                current_type = type(current)
                enum = "unknown_exception"
                for known_type, known_enum in known:
                    if current_type is known_type:
                        enum = known_enum
                        break
                unknown_class = unknown_class or enum == "unknown_exception"
                traceback_node = native_read(0, current)
                frames = []
                seen_tracebacks = set()
                while traceback_node is not None:
                    if total_frames == 64:
                        frame_truncated = True
                        break
                    traceback_id = id(traceback_node)
                    if traceback_id in seen_tracebacks:
                        frame_cycle = True
                        break
                    seen_tracebacks.add(traceback_id)
                    frame = native_read(2, traceback_node)
                    line = native_read(3, traceback_node)
                    code = native_read(5, frame)
                    filename = native_read(6, code)
                    location = ["unknown_location", 0]
                    if type(filename) is str and type(line) is int and not isinstance(line, bool):
                        file_pin = files.get(filename)
                        if file_pin is not None and 1 <= line <= file_pin[1]:
                            location = [file_pin[0], line]
                        else:
                            unknown_location = True
                    else:
                        unknown_location = True
                    frames.append(location)
                    total_frames += 1
                    traceback_node = native_read(4, traceback_node)
                    frame = code = None
                contexts.append({"class": enum, "frames": frames})
                current = native_read(1, current)
                traceback_node = None
            return {
                "schema": schema, "status": "captured", "contexts": contexts,
                "context_count": len(contexts), "total_frames": total_frames,
                "context_cycle": context_cycle,
                "context_truncated": context_truncated, "frame_cycle": frame_cycle,
                "frame_truncated": frame_truncated, "multiple_contexts": len(contexts) > 1,
                "unknown_class": unknown_class, "unknown_location": unknown_location,
            }
        finally:
            exception = current = traceback_node = frame = code = None

    def reporting_handler(original_handler):
        async def handler(request, exception):
            nonlocal captures, finished
            if captures == 0:
                captures = 1
                try:
                    finished = project(exception)
                except BaseException:
                    finished = {"schema": schema, "status": "projection_unavailable"}
                finished.update(capture_count=1, multiple_captures=False, capture_truncated=False)
            else:
                captures = 2
                finished.update(capture_count=2, multiple_captures=True, capture_truncated=True)
            return await original_handler(request, exception)
        return handler

    runtime_app.add_exception_handler(
        McpContextRequestRuntimeError, reporting_handler(runtime_handler),
    )
    runtime_app.add_exception_handler(
        McpPrivateContextConfirmationError, reporting_handler(confirmation_handler),
    )
    response_status = None
    try:
        with TestClient(runtime_app) as client:
            try:
                response = _call(
                    client, "harbour_context_prepare",
                    {"chat_session_id": "chat-owned",
                     "selection": _selection_wire(runtime_app), "page_bytes": 16_384},
                    rpc_id=1, proof=True,
                )
                response_status = response.status_code
            finally:
                _shutdown(runtime_app)
    finally:
        runtime_app.exception_handlers[McpContextRequestRuntimeError] = runtime_handler
        runtime_app.exception_handlers[
            McpPrivateContextConfirmationError
        ] = confirmation_handler
    if response_status != 200:
        pytest.fail(
            json.dumps(finished, sort_keys=True, separators=(",", ":")),
            pytrace=False,
        )
