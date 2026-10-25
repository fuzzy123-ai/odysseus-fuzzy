"""Hostile request-rejection tests for the MCP private-context browser bridge."""
from __future__ import annotations

from dataclasses import replace
import os
import threading
import weakref

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from routes.auth_routes import SESSION_COOKIE
from src.mcp_private_context_confirmation import (
    HumanExchange,
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
    Permit,
    VerifiedNativeCapture,
    VerifiedPlanningCapture,
    _CompletionRow,
    _HumanExchangeRow,
    _NativeCompletionReceipt,
    _NativeCompletionRow,
    _PermitRow,
    _PlanningCompletionReceipt,
)
from src.mcp_private_context_confirmation_app import (
    McpPrivateContextConfirmationApp,
    _BRIDGE_OWNER_SLOT,
)
from tests.test_app_mcp_private_context_confirmation import (
    _assert_success,
    _headers,
    _scrubbed_readback,
    consent_app,
)
from tests.test_mcp_private_context_confirmation import (
    _entry_snapshot,
    _entry_unchanged,
)


_SERVICE_METHODS = (
    "_reject_authority_reentry",
    "_registered_request_app_method",
    "_registered_app_method",
    "_assert_pins",
    "_terminate",
    "_scrub",
)
_COMBINED_CASES = tuple(
    pytest.param(
        stage, app_mode, method, service_mode,
        id=f"{stage}-app-{app_mode}-service-{method[1:]}-{service_mode}",
    )
    for stage in ("challenge", "exchange")
    for app_mode in ("class", "instance")
    for method in _SERVICE_METHODS
    for service_mode in ("class", "instance")
)
_SERVICE_ONLY_CASES = tuple(
    pytest.param(method, mode, id=f"service-{method[1:]}-{mode}")
    for method in _SERVICE_METHODS
    for mode in ("class", "instance")
)


def _install_h7_routes(application):
    @application.post("/test/h7/direct_challenge")
    async def direct_challenge(request: Request):
        body = await request.json()
        result = request.app.state.bridge.issue_authenticated_challenge(
            request, body.get("request_id"),
        )
        return {"token": result.token}

    @application.post("/test/h7/lease_readback")
    async def lease_readback(request: Request):
        request.app.state.fixture["authority"].recheck_lease(request.app.state.lease)
        return {
            "lease_retained": True,
            "native_completions_empty": not request.app.state.service._native_completions,
        }


def _prepare(client):
    fixture = client.app.state.fixture
    prepared = _assert_success(client.post(
        "/test/prepare",
        headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
    ))
    client.cookies.set(SESSION_COOKIE, fixture["cookie"])
    return prepared.json()["request_id"]


def _challenge(client):
    return _assert_success(client.post(
        "/api/context/mcp/challenge", headers=_headers(),
        json={"request_id": client.app.state.request_id},
    )).json()["token"]


def _exchange(client, challenge_token):
    return client.post(
        "/api/chat", headers=_headers(),
        json={
            "message": "/mcp-context-disclose " + challenge_token,
            "session": "chat-owned",
        },
    )


def _patch_service_method(patch, service, method, mode, calls):
    def shadow(*_args, **_kwargs):
        calls.append(method)
        raise AssertionError("substituted service method executed")

    if mode == "class":
        patch.setattr(McpPrivateContextConfirmationService, method, shadow)
    else:
        patch.setattr(service, method, shadow, raising=False)


def _assert_rejected_and_scrubbed(client, response):
    assert response.status_code == 403
    assert response.json() == {"error": "invalid_binding"}
    assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()
    assert _assert_success(client.post("/test/h7/lease_readback")).json() == {
        "lease_retained": True,
        "native_completions_empty": True,
    }


@pytest.mark.parametrize(
    ("stage", "app_mode", "service_method", "service_mode"),
    _COMBINED_CASES,
)
def test_combined_app_and_service_drift_scrubs_exact_request(
    consent_app, monkeypatch, stage, app_mode, service_method, service_mode,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        challenge_token = _challenge(client) if stage == "exchange" else None
        _assert_success(client.post(
            "/test/arm_substitution", json={"stage": stage, "mode": app_mode},
        ))
        calls = []
        with monkeypatch.context() as patch:
            _patch_service_method(
                patch, client.app.state.service, service_method, service_mode, calls,
            )
            denied = (client.post(
                "/api/context/mcp/challenge", headers=_headers(),
                json={"request_id": request_id},
            ) if stage == "challenge" else _exchange(client, challenge_token))
        assert calls == []
        _assert_rejected_and_scrubbed(client, denied)


@pytest.mark.parametrize(("service_method", "service_mode"), _SERVICE_ONLY_CASES)
def test_service_drift_alone_scrubs_exact_prepared_request(
    consent_app, monkeypatch, service_method, service_mode,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        _assert_success(client.post(
            "/test/arm_substitution", json={"stage": "exchange", "mode": "class"},
        ))
        calls = []
        with monkeypatch.context() as patch:
            _patch_service_method(
                patch, client.app.state.service, service_method, service_mode, calls,
            )
            denied = client.post(
                "/api/context/mcp/challenge", headers=_headers(),
                json={"request_id": request_id},
            )
        assert calls == []
        _assert_rejected_and_scrubbed(client, denied)


@pytest.mark.parametrize("stage", [
    pytest.param("challenge", id="challenge"),
    pytest.param("exchange", id="exchange"),
])
def test_clean_request_preflight_preserves_success(consent_app, stage):
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        challenge_token = _challenge(client)
        assert client.app.state.service._entries[request_id].state == "challenged"
        if stage == "exchange":
            response = _assert_success(_exchange(client, challenge_token))
            assert response.json()["request_id"] == request_id


def test_reentry_with_drift_sets_violation_before_cleanup(consent_app, monkeypatch):
    @consent_app.middleware("http")
    async def retain_current_request(request: Request, call_next):
        request.app.state.h7_current_request = request
        return await call_next(request)

    with TestClient(consent_app) as client:
        _prepare(client)
        challenge_token = _challenge(client)
        _assert_success(_exchange(client, challenge_token))
        _assert_success(client.post(
            "/test/arm_substitution", json={"stage": "challenge", "mode": "class"},
        ))
        state = client.app.state
        state.callback_permit_invalid = False
        calls = []

        def shadow(*_args, **_kwargs):
            calls.append("called")
            raise AssertionError("substituted method executed")

        def callback(_page):
            with monkeypatch.context() as patch:
                patch.setattr(
                    McpPrivateContextConfirmationApp,
                    "_cached_browser_submission", staticmethod(shadow),
                )
                patch.setattr(
                    McpPrivateContextConfirmationService, "_assert_pins", shadow,
                )
                try:
                    state.bridge.issue_authenticated_challenge(
                        state.h7_current_request, state.secondary_request_id,
                    )
                except McpPrivateContextConfirmationError as error:
                    assert error.code == "permit_invalid"
                    state.callback_permit_invalid = True
                    return None
                raise AssertionError("reentry unexpectedly succeeded")

        state.callback = callback
        denied = client.post("/test/consume")
        assert denied.status_code == 403
        assert denied.json() == {"error": "revoked"}
        assert calls == []
        expected = _scrubbed_readback()
        expected["callback_permit_invalid"] = True
        assert _assert_success(client.post("/test/substitution_readback")).json() == expected


@pytest.mark.parametrize(("request_id", "expected"), [
    pytest.param(7, "invalid_request", id="nonexact-int"),
    pytest.param(["r.missing"], "invalid_request", id="nonexact-list"),
    pytest.param("r.missing", "invalid_binding", id="absent-exact-str"),
])
def test_drift_rejection_validates_request_id_without_touching_primary(
    consent_app, monkeypatch, request_id, expected,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        primary_id = _prepare(client)
        service = client.app.state.service
        primary = service._entries[primary_id]
        before = _entry_snapshot(primary)

        def shadow(*_args, **_kwargs):
            raise AssertionError("substituted app method executed")

        with monkeypatch.context() as patch:
            patch.setattr(
                McpPrivateContextConfirmationApp,
                "_cached_browser_submission", staticmethod(shadow),
            )
            denied = client.post(
                "/test/h7/direct_challenge", json={"request_id": request_id},
            )
        assert denied.status_code == 403
        assert denied.json() == {"error": expected}
        assert service._entries.get(primary_id) is primary
        assert _entry_unchanged(primary, before)


@pytest.mark.parametrize("kind", [
    pytest.param("owner", id="forged-owner"),
    pytest.param("entries", id="replaced-entry-map"),
])
def test_untrusted_owner_or_storage_never_mutates_foreign_state(consent_app, kind):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        state = client.app.state
        service = state.service
        primary = service._entries[request_id]
        before = _entry_snapshot(primary)
        if kind == "owner":
            original = _BRIDGE_OWNER_SLOT.__get__(state.bridge, type(state.bridge))
            _BRIDGE_OWNER_SLOT.__set__(state.bridge, object())
            restore = lambda: _BRIDGE_OWNER_SLOT.__set__(state.bridge, original)
            foreign = None
        else:
            service_state = object.__getattribute__(service, "__dict__")
            original = service_state["_entries"]
            foreign = {}
            service_state["_entries"] = foreign
            restore = lambda: service_state.__setitem__("_entries", original)
        try:
            denied = client.post(
                "/test/h7/direct_challenge", json={"request_id": request_id},
            )
        finally:
            restore()
        assert denied.status_code == 403
        assert denied.json() == {"error": "invalid_context"}
        assert original.get(request_id) is primary if kind == "entries" else True
        assert _entry_unchanged(primary, before)
        if foreign is not None:
            assert foreign == {}


def test_malformed_foreign_row_blocks_cleanup_without_callbacks(consent_app, monkeypatch):
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        service = client.app.state.service
        primary = service._entries[request_id]
        before = _entry_snapshot(primary)
        foreign = object()
        service._permits[17] = foreign

        def shadow(*_args, **_kwargs):
            raise AssertionError("substituted app method executed")

        try:
            with monkeypatch.context() as patch:
                patch.setattr(
                    McpPrivateContextConfirmationApp,
                    "_cached_browser_submission", staticmethod(shadow),
                )
                denied = client.post(
                    "/api/context/mcp/challenge", headers=_headers(),
                    json={"request_id": request_id},
                )
            assert denied.status_code == 403
            assert denied.json() == {"error": "invalid_binding"}
            assert service._permits.get(17) is foreign
            assert service._entries.get(request_id) is primary
            assert _entry_unchanged(primary, before)
        finally:
            service._permits.pop(17, None)

class _WeakHolder:
    pass


def _patch_app_method(patch, calls):
    def shadow(*_args, **_kwargs):
        calls.append("app")
        raise AssertionError("substituted app method executed")

    patch.setattr(
        McpPrivateContextConfirmationApp,
        "_cached_browser_submission",
        staticmethod(shadow),
    )


def _associated_row(service, bridge, entry, kind):
    lease = entry.lease_ref()
    binding = entry.binding_ref()
    authority = service._authority
    thread = threading.current_thread()
    recipient = (
        binding.owner_subject_id,
        binding.chat_session_id,
        binding.token_id,
        binding.scope,
        binding.cache_generation,
        binding.security_action_revision,
        binding.process_epoch,
    )
    refs = [lease, binding, authority, thread]
    original_permit_ref = entry.permit_ref
    if kind == "permit":
        handle = Permit()
        handle_ref = weakref.ref(handle)
        entry.permit_ref = handle_ref
        row = _PermitRow(
            handle_ref,
            weakref.ref(entry),
            weakref.ref(service),
            weakref.ref(authority),
            weakref.ref(lease),
            weakref.ref(binding),
            recipient,
            weakref.ref(thread),
            threading.get_ident(),
            os.getpid(),
            entry.request_id,
            entry.generation,
            19,
        )
        refs.append(handle)
        return service._permits, id(handle), row, refs, original_permit_ref
    if kind == "exchange":
        exchange = HumanExchange()
        row = _HumanExchangeRow(
            weakref.ref(exchange),
            weakref.ref(bridge),
            weakref.ref(service),
            weakref.ref(authority),
            weakref.ref(lease),
            weakref.ref(binding),
            weakref.ref(thread),
            entry.request_id,
            "challenge-token",
            binding.chat_session_id,
            entry.generation,
            23,
            "/mcp-context-disclose challenge-token",
            b"recipient-nine",
            threading.get_ident(),
            os.getpid(),
        )
        refs.append(exchange)
        return service._exchanges, id(exchange), row, refs, original_permit_ref
    if kind == "completion":
        receipt = _PlanningCompletionReceipt()
        capture = VerifiedPlanningCapture(object(), receipt)
        row = _CompletionRow(
            weakref.ref(receipt),
            weakref.ref(capture),
            weakref.ref(entry),
            weakref.ref(service),
            weakref.ref(authority),
            weakref.ref(lease),
            weakref.ref(binding),
            weakref.ref(thread),
            entry.request_id,
            entry.generation,
            29,
            os.getpid(),
            recipient,
            "sha256:planning",
        )
        refs.extend((receipt, capture))
        return service._completions, id(receipt), row, refs, original_permit_ref
    if kind == "native_completion":
        receipt = _NativeCompletionReceipt()
        capture = VerifiedNativeCapture("forge", object(), receipt)
        row = _NativeCompletionRow(
            weakref.ref(receipt),
            weakref.ref(capture),
            weakref.ref(entry),
            weakref.ref(service),
            weakref.ref(authority),
            weakref.ref(lease),
            weakref.ref(binding),
            weakref.ref(thread),
            entry.request_id,
            entry.generation,
            31,
            os.getpid(),
            recipient,
            "forge",
            "sha256:native",
        )
        refs.extend((receipt, capture))
        return (
            service._native_completions,
            id(receipt),
            row,
            refs,
            original_permit_ref,
        )
    raise AssertionError("unknown row kind")


_EXACT_ROW_FAULTS = (
    pytest.param("permit", "request_id", id="permit-request-id"),
    pytest.param("exchange", "generation", id="exchange-generation"),
    pytest.param("completion", "service_identity", id="completion-service-identity"),
    pytest.param("native_completion", "entry_ref", id="native-completion-entry-ref"),
)
_ASSOCIATED_ROW_KINDS = tuple(
    pytest.param(kind, id=kind)
    for kind in ("permit", "exchange", "completion", "native_completion")
)
_SERVICE_ATTRIBUTE_SHAPES = (
    pytest.param("__getattribute__", id="getattribute"),
    pytest.param("__getattr__", id="getattr"),
)


@pytest.mark.parametrize(("kind", "fault"), _EXACT_ROW_FAULTS)
def test_exact_typed_malformed_row_blocks_all_mutation(
    consent_app, monkeypatch, kind, fault,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        service = client.app.state.service
        entry = service._entries[request_id]
        storage, key, row, retained, original_permit_ref = _associated_row(
            service, client.app.state.bridge, entry, kind,
        )
        foreign = _WeakHolder()
        retained.append(foreign)
        if fault == "request_id":
            malformed = replace(row, request_id="r.foreign")
        elif fault == "generation":
            malformed = replace(row, generation=True)
        elif fault == "service_identity":
            malformed = replace(row, service_ref=weakref.ref(foreign))
        else:
            malformed = replace(row, entry_ref=foreign)
        storage[key] = malformed
        before = _entry_snapshot(entry)
        maps_before = {
            "permits": dict(service._permits),
            "exchanges": dict(service._exchanges),
            "completions": dict(service._completions),
            "native_completions": dict(service._native_completions),
        }
        calls = []
        try:
            with monkeypatch.context() as patch:
                _patch_app_method(patch, calls)
                denied = client.post(
                    "/api/context/mcp/challenge",
                    headers=_headers(),
                    json={"request_id": request_id},
                )
            assert denied.status_code == 403
            assert denied.json() == {"error": "invalid_binding"}
            assert calls == []
            assert service._entries.get(request_id) is entry
            assert _entry_unchanged(entry, before)
            assert service._permits == maps_before["permits"]
            assert service._exchanges == maps_before["exchanges"]
            assert service._completions == maps_before["completions"]
            assert service._native_completions == maps_before["native_completions"]
            assert storage.get(key) is malformed
        finally:
            storage.pop(key, None)
            entry.permit_ref = original_permit_ref


def test_malformed_exact_foreign_entry_blocks_primary_cleanup(
    consent_app, monkeypatch,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        service = client.app.state.service
        primary = service._entries[request_id]
        before = _entry_snapshot(primary)
        foreign_key = "r.foreign"
        foreign = replace(primary, request_id="r.different")
        service._entries[foreign_key] = foreign
        calls = []
        try:
            with monkeypatch.context() as patch:
                _patch_app_method(patch, calls)
                denied = client.post(
                    "/api/context/mcp/challenge",
                    headers=_headers(),
                    json={"request_id": request_id},
                )
            assert denied.status_code == 403
            assert denied.json() == {"error": "invalid_binding"}
            assert calls == []
            assert service._entries.get(request_id) is primary
            assert service._entries.get(foreign_key) is foreign
            assert _entry_unchanged(primary, before)
        finally:
            service._entries.pop(foreign_key, None)


@pytest.mark.parametrize("kind", _ASSOCIATED_ROW_KINDS)
def test_valid_associated_row_is_removed_with_exact_primary(
    consent_app, monkeypatch, kind,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        _assert_success(client.post(
            "/test/arm_substitution",
            json={"stage": "challenge", "mode": "class"},
        ))
        service = client.app.state.service
        entry = service._entries[request_id]
        storage, key, row, retained, _original_permit_ref = _associated_row(
            service, client.app.state.bridge, entry, kind,
        )
        storage[key] = row
        denied = client.post(
            "/api/context/mcp/challenge",
            headers=_headers(),
            json={"request_id": request_id},
        )
        assert retained
        assert key not in storage
        _assert_rejected_and_scrubbed(client, denied)


@pytest.mark.parametrize("attribute_name", _SERVICE_ATTRIBUTE_SHAPES)
def test_service_attribute_shape_drift_is_fenced_without_callback(
    consent_app, monkeypatch, attribute_name,
):
    _install_h7_routes(consent_app)
    with TestClient(consent_app) as client:
        request_id = _prepare(client)
        _assert_success(client.post(
            "/test/arm_substitution",
            json={"stage": "exchange", "mode": "class"},
        ))
        calls = []

        def hostile(*_args, **_kwargs):
            calls.append(attribute_name)
            raise AssertionError("hostile service attribute hook executed")

        with monkeypatch.context() as patch:
            patch.setattr(
                McpPrivateContextConfirmationService,
                attribute_name,
                hostile,
                raising=False,
            )
            denied = client.post(
                "/api/context/mcp/challenge",
                headers=_headers(),
                json={"request_id": request_id},
            )
        assert calls == []
        _assert_rejected_and_scrubbed(client, denied)

