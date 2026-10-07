from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
import pytest

from routes.auth_routes import SESSION_COOKIE
import src.mcp_context_planning_page as publication
from src.mcp_context_contract import encode_context_jsonrpc_response
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationService,
    Permit,
)
from src.planning_context_reader import PlanningContextPage
from tests.test_app_mcp_private_context_confirmation import (
    _assert_success,
    _headers,
    consent_app,
)


def _authorize(client: TestClient) -> str:
    fixture = client.app.state.fixture
    prepared = _assert_success(client.post(
        "/test/prepare",
        headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
    )).json()
    client.cookies.set(SESSION_COOKIE, fixture["cookie"])
    challenge = _assert_success(client.post(
        "/api/context/mcp/challenge",
        headers=_headers(),
        json={"request_id": prepared["request_id"]},
    )).json()
    _assert_success(client.post(
        "/api/chat",
        headers=_headers(),
        json={
            "message": "/mcp-context-disclose " + challenge["token"],
            "session": "chat-owned",
        },
    ))
    return prepared["request_id"]


def _install_publication_route(app) -> None:
    app.state.pending_permit = None
    app.state.last_permit = None
    app.state.publication_returned = False

    @app.exception_handler(publication.McpPlanningPagePublicationError)
    async def publication_error(_request, exc):
        return JSONResponse(status_code=403, content={"error": exc.code})

    @app.post("/test/planning-page-publication")
    async def planning_page_publication(request: Request):
        body = await request.json()
        mode = body.get("mode", "normal") if type(body) is dict else "invalid"
        service = request.app.state.service
        request.app.state.publication_returned = False
        if mode == "forged":
            permit = Permit()
        elif mode == "replay":
            permit = request.app.state.last_permit
        else:
            permit = request.app.state.pending_permit
            if permit is None:
                permit = service.issue_permit(request.app.state.request_id)
                request.app.state.pending_permit = permit
            request.app.state.last_permit = permit
        if mode == "revoked":
            service.revoke(request.app.state.request_id)
        target_index = next(
            index for index, item in enumerate(request.app.state.fixture["selection"].items)
            if item.source == "planning"
        )
        request_id: object = body.get("rpc_id", 17)
        offset: object = body.get("offset", 1)
        if mode == "request_bool":
            request_id = True
        elif mode == "request_subclass":
            request_id = _IntSubclass(17)
        elif mode == "target_bool":
            target_index = True
        elif mode == "target_subclass":
            target_index = _IntSubclass(target_index)
        elif mode == "offset_bool":
            offset = False
        elif mode == "offset_subclass":
            offset = _IntSubclass(1)
        encoded = publication.publish_planning_page(
            service,
            permit,
            request_id=request_id,
            target_index=target_index,
            offset=offset,
        )
        request.app.state.pending_permit = None
        request.app.state.publication_returned = True
        return Response(encoded, media_type="application/json")


class _IntSubclass(int):
    pass


class _EqualStringSubclass(str):
    def __eq__(self, _other):
        return True

    def __ne__(self, _other):
        return False


def _assert_fixed_failure(response) -> None:
    assert response.status_code == 403
    assert response.json() == {"error": publication.MCP_PLANNING_PAGE_ERROR}


def test_real_consent_publishes_exact_page_response_then_replay_is_denied(consent_app):
    _install_publication_route(consent_app)
    with TestClient(consent_app) as client:
        consent_request_id = _authorize(client)
        fixture = client.app.state.fixture
        service = client.app.state.service
        entry = service._entries[consent_request_id]
        target_index = next(
            index for index, item in enumerate(fixture["selection"].items)
            if item.source == "planning"
        )
        target = fixture["selection"].items[target_index]
        payload = client.app.state.oracle_payloads[target.selection_ref]
        expected_manifest = entry.cookie_snapshot.target_manifest_digests[target_index]
        page_limit = entry.page_bytes
        response = _assert_success(client.post(
            "/test/planning-page-publication",
            json={"mode": "normal", "rpc_id": "rpc-17", "offset": 1},
        ))
        encoded = response.content
        value = json.loads(encoded)
        assert encoded == encode_context_jsonrpc_response("rpc-17", value["result"])
        assert set(value) == {"jsonrpc", "id", "result"}
        assert value["jsonrpc"] == "2.0" and value["id"] == "rpc-17"
        result = value["result"]
        assert set(result) == {
            "schema", "target_index", "offset", "end", "total_bytes",
            "whole_sha256", "page_sha256", "manifest_digest", "byteform",
            "content_b64",
        }
        page_bytes = base64.b64decode(result["content_b64"], validate=True)
        assert result == {
            "schema": publication.MCP_PLANNING_PAGE_SCHEMA,
            "target_index": target_index,
            "offset": 1,
            "end": min(len(payload), 1 + page_limit),
            "total_bytes": len(payload),
            "whole_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "page_sha256": "sha256:" + hashlib.sha256(page_bytes).hexdigest(),
            "manifest_digest": expected_manifest,
            "byteform": "durable_revision_document",
            "content_b64": base64.b64encode(page_bytes).decode("ascii"),
        }
        assert page_bytes == payload[result["offset"]:result["end"]]
        assert not ({"complete", "next_cursor", "authority_granted"} & set(result))
        assert consent_request_id not in service._entries and not service._completions
        _assert_fixed_failure(client.post(
            "/test/planning-page-publication", json={"mode": "replay"},
        ))


def test_forged_permit_and_nonexact_scalars_never_publish(consent_app):
    _install_publication_route(consent_app)
    with TestClient(consent_app) as client:
        consent_request_id = _authorize(client)
        service = client.app.state.service
        _assert_fixed_failure(client.post(
            "/test/planning-page-publication", json={"mode": "forged"},
        ))
        for mode in (
            "request_bool", "request_subclass", "target_bool",
            "target_subclass", "offset_bool", "offset_subclass",
        ):
            _assert_fixed_failure(client.post(
                "/test/planning-page-publication", json={"mode": mode},
            ))
            assert client.app.state.publication_returned is False
            assert consent_request_id in service._entries
            assert not service._completions
        assert _assert_success(client.post(
            "/test/planning-page-publication", json={"mode": "normal"},
        )).json()["result"]["schema"] == publication.MCP_PLANNING_PAGE_SCHEMA


def test_revoked_request_returns_no_page_bytes(consent_app):
    _install_publication_route(consent_app)
    with TestClient(consent_app) as client:
        consent_request_id = _authorize(client)
        service = client.app.state.service
        _assert_fixed_failure(client.post(
            "/test/planning-page-publication", json={"mode": "revoked"},
        ))
        assert client.app.state.publication_returned is False
        assert consent_request_id not in service._entries
        assert not service._completions


def test_encoder_failures_and_oversize_never_publish_or_leave_completion(consent_app, monkeypatch):
    _install_publication_route(consent_app)
    with TestClient(consent_app) as client:
        mutation_reached = {"value": False}
        b64_tamper_armed = {"value": False}
        b64_callback_reached = {"value": False}
        b64_replacement_called = {"value": False}
        for mode in ("raise", "oversize", "mismatch", "mutate", "b64"):
            consent_request_id = _authorize(client)
            retained_entry = client.app.state.service._entries[consent_request_id]
            if mode == "raise":
                def broken_encoder(_request_id, _result):
                    raise RuntimeError("private encoder detail")
            elif mode == "oversize":
                def broken_encoder(_request_id, _result):
                    return b"x" * 65_537
            elif mode == "mismatch":
                def broken_encoder(_request_id, _result):
                    return b"{}"
            else:
                if mode == "mutate":
                    def broken_encoder(request_id, result):
                        mutation_reached["value"] = True
                        result["schema"] = "attacker.schema"
                        result["content_b64"] = "cHJpdmF0ZS1zdWJzdGl0dXRpb24="
                        result["page_sha256"] = "sha256:" + "0" * 64
                        result["injected"] = True
                        return encode_context_jsonrpc_response(request_id, result)
                else:
                    def broken_b64encode(_value):
                        b64_replacement_called["value"] = True
                        return b"cHJpdmF0ZS1zdWJzdGl0dXRpb24="
            with monkeypatch.context() as patch:
                if mode == "b64":
                    b64_tamper_armed["value"] = True
                    patch.setattr(publication, "base64", SimpleNamespace(b64encode=broken_b64encode))
                else:
                    patch.setattr(publication, "encode_context_jsonrpc_response", broken_encoder)
                response = client.post(
                    "/test/planning-page-publication", json={"mode": "normal"},
                )
            _assert_fixed_failure(response)
            assert "private encoder detail" not in response.text
            assert client.app.state.publication_returned is False
            assert consent_request_id not in client.app.state.service._entries
            assert not client.app.state.service._completions
            if mode == "b64":
                b64_callback_reached["value"] = (
                    retained_entry.state == "expired"
                    and retained_entry.page_bytes == 0
                    and retained_entry.selection is None
                    and retained_entry.lease_ref is None
                    and retained_entry.binding_ref is None
                )
            client.app.state.pending_permit = None
        assert mutation_reached["value"] is True
        assert b64_tamper_armed["value"] is True
        assert b64_callback_reached["value"] is True
        assert b64_replacement_called["value"] is False


def test_tampered_page_fields_are_rejected_by_detached_projection():
    cases = {
        "page_bytes": bytearray(b"abc"),
        "total_bytes": True,
        "offset": -1,
        "end": 4,
        "whole_sha256": "private-content",
        "page_sha256": "sha256:" + "0" * 64,
        "manifest_digest": "private-manifest",
        "byteform": _EqualStringSubclass("git_blob"),
    }
    for field, replacement in cases.items():
        page = PlanningContextPage(
            page_bytes=b"abc", total_bytes=3, offset=0, end=3,
            whole_sha256="sha256:" + hashlib.sha256(b"abc").hexdigest(),
            page_sha256="sha256:" + hashlib.sha256(b"abc").hexdigest(),
            manifest_digest="1" * 64,
        )
        object.__setattr__(page, field, replacement)
        with pytest.raises(publication.McpPlanningPagePublicationError) as caught:
            publication._result_for_page(page, 0)
        assert caught.value.code == publication.MCP_PLANNING_PAGE_ERROR


def test_method_substitution_and_completion_phase_denial_return_no_bytes(consent_app, monkeypatch):
    _install_publication_route(consent_app)
    with TestClient(consent_app) as client:
        consent_request_id = _authorize(client)
        service = client.app.state.service
        service._execute_planning_page = lambda *_args, **_kwargs: None
        _assert_fixed_failure(client.post(
            "/test/planning-page-publication", json={"mode": "normal"},
        ))
        del service._execute_planning_page
        with monkeypatch.context() as patch:
            patch.setattr(
                McpPrivateContextConfirmationService,
                "_execute_planning_page",
                lambda *_args, **_kwargs: None,
            )
            _assert_fixed_failure(client.post(
                "/test/planning-page-publication", json={"mode": "normal"},
            ))
        original_consume = McpPrivateContextConfirmationService._consume_planning_completion

        def replace_consume_after_encoding(request_id, result):
            encoded = encode_context_jsonrpc_response(request_id, result)
            patch.setattr(
                McpPrivateContextConfirmationService,
                "_consume_planning_completion",
                lambda *_args, **_kwargs: None,
            )
            return encoded

        with monkeypatch.context() as patch:
            patch.setattr(publication, "encode_context_jsonrpc_response", replace_consume_after_encoding)
            response = client.post(
                "/test/planning-page-publication", json={"mode": "normal"},
            )
        assert McpPrivateContextConfirmationService._consume_planning_completion is original_consume
        _assert_fixed_failure(response)
        assert client.app.state.publication_returned is False
        assert consent_request_id not in service._entries
        assert not service._completions
