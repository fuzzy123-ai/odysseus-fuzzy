from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import weakref
import xml.etree.ElementTree as ET

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
import pytest

from routes.auth_routes import SESSION_COOKIE
from src.forge_context_page import ForgeContextPage
from src.harbour_knowledge_active_runtime import KnowledgeContextPage
from src.mcp_context_authority import McpContextAuthorityError
from src.mcp_context_contract import encode_context_jsonrpc_response
import src.mcp_context_native_page as publication
from src.mcp_private_context_confirmation import McpPrivateContextConfirmationError, Permit
from src.private_context_confirmation_app import CookieConfirmationAuthority
from tests.test_app_mcp_private_context_confirmation import _assert_success, _headers, consent_app
from tests.test_mcp_context_metadata import _lease


_LARGE_FORGE_PAYLOAD = "".join(
    f"retained forge context café line {index:04d}\n" for index in range(900)
).encode("utf-8")
_LARGE_KNOWLEDGE_PAYLOAD = "".join(
    f"orbital mémoire privée evidence line {index:04d}\n" for index in range(900)
).encode("utf-8")
assert len(_LARGE_FORGE_PAYLOAD) > 16_384
assert len(_LARGE_KNOWLEDGE_PAYLOAD) > 16_384


def _isolate_inherited_new_mutation(request, tmp_path) -> bool:
    # CPython retains poisoned constructor dispatch after deleting a patched
    # inherited __new__. Keep the genuine hostile mutation in a fresh process.
    node = request.node.nodeid
    allowed = {
        "tests/test_mcp_context_native_page.py::test_native_helper_provider_and_body_reader_substitution_denies_before_fake_runs[forge-reader_new]",
        *(
            "tests/test_mcp_context_native_page.py::test_native_value_class_construction_access_and_slots_are_pinned_before_source_calls[" + case + "]"
            for case in ("range_new", "forge_page_new", "knowledge_page_new")
        ),
    }
    assert node in allowed
    marker = os.environ.get("ODYSSEUS_NATIVE_NEW_TEST_CHILD", "")
    if marker:
        assert marker == node
        return False
    child_env = os.environ.copy()
    child_env["ODYSSEUS_NATIVE_NEW_TEST_CHILD"] = node
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    junit = tmp_path / "child-junit.xml"
    # A nested pytest case directory would exceed Git's Windows ref-lock limit.
    child_temp = tmp_path.parent / ("n" + hashlib.sha256(node.encode("utf-8")).hexdigest()[:8])
    assert not child_temp.exists()
    command = [
        sys.executable, "-B", "-m", "pytest", "-q", "-x",
        "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
        "-W", "error::pytest.PytestUnhandledThreadExceptionWarning",
        "-W", "error::pytest.PytestUnraisableExceptionWarning",
        "--basetemp", str(child_temp),
        "--junitxml", str(junit), node,
    ]
    # Child processes inherit the verification controller's Windows job.
    # Never forward child output or failure details into the parent report.
    try:
        result = subprocess.run(
            command, cwd=Path(__file__).resolve().parents[1], env=child_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=900, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("isolated constructor test timed out", pytrace=False)
    assert result.returncode == 0, "isolated constructor test failed"
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    assert len(cases) == 1
    assert cases[0].get("classname") == "tests.test_mcp_context_native_page"
    assert cases[0].get("name") == node.split("::", 1)[1]
    assert all(cases[0].find(kind) is None for kind in ("failure", "error", "skipped"))
    return True


def _authorize(client: TestClient) -> str:
    fixture = client.app.state.fixture
    prepared = _assert_success(client.post(
        "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
    )).json()
    client.cookies.set(SESSION_COOKIE, fixture["cookie"])
    challenge = _assert_success(client.post(
        "/api/context/mcp/challenge", headers=_headers(), json={"request_id": prepared["request_id"]},
    )).json()
    _assert_success(client.post(
        "/api/chat", headers=_headers(),
        json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"},
    ))
    return prepared["request_id"]


def _install_route(app) -> None:
    app.state.last_permit = None
    app.state.issued_permits = []
    app.state.preissued_permit = None
    app.state.native_callback = None

    @app.exception_handler(publication.McpNativePagePublicationError)
    async def native_error(_request, exc):
        return JSONResponse(status_code=403, content={"error": exc.code})

    @app.post("/test/native-page")
    async def native_page(request: Request):
        body = await request.json()
        service = request.app.state.service
        source = body.get("source")
        if body.get("mode") == "preissued":
            permit = request.app.state.preissued_permit
        elif body.get("mode") == "old_permit":
            permit = request.app.state.issued_permits[body.get("permit_index", 0)]
        elif body.get("mode") == "replay":
            permit = request.app.state.last_permit
        elif body.get("mode") == "forged":
            permit = Permit()
        else:
            try:
                permit = service.issue_permit(request.app.state.request_id)
            except (McpPrivateContextConfirmationError, McpContextAuthorityError):
                raise publication.McpNativePagePublicationError() from None
            request.app.state.last_permit = permit
            request.app.state.issued_permits.append(permit)
        target_index = next(index for index, item in enumerate(request.app.state.fixture["selection"].items)
                            if item.source == body.get("target_source", source))
        target_index = body.get("target_index", target_index)
        entry = service._entries.get(request.app.state.request_id)
        if body.get("drift") == "ack":
            entry.ack_deadline = entry.issued
        elif body.get("drift") == "lease":
            request.app.state.drift_holder = Permit()
            entry.lease_ref = weakref.ref(request.app.state.drift_holder)
        elif body.get("drift") == "source":
            object.__setattr__(entry.selection.items[target_index], "source", "planning")
        elif body.get("drift") == "pid":
            row = service._permits[id(permit)]
            object.__setattr__(row, "pid", row.pid + 1)
        function = publication.publish_forge_page if source == "forge" else publication.publish_knowledge_page
        encoded = function(service, permit, request_id=body.get("rpc_id", 7),
                           target_index=target_index, offset=body.get("offset", 0))
        return Response(encoded, media_type="application/json")

    @app.post("/test/issue-native-permit")
    async def issue_native_permit(request: Request):
        request.app.state.preissued_permit = request.app.state.service.issue_permit(request.app.state.request_id)
        request.app.state.issued_permits.append(request.app.state.preissued_permit)
        return {"issued": True}

    @app.post("/test/native-execute")
    async def native_execute(request: Request):
        body = await request.json(); service = request.app.state.service; source = body["source"]
        permit = service.issue_permit(request.app.state.request_id)
        target_index = next(index for index, item in enumerate(request.app.state.fixture["selection"].items)
                            if item.source == source)
        if body.get("mode") == "thread":
            result = []
            def worker():
                try:
                    function = (publication.publish_forge_page if source == "forge"
                                else publication.publish_knowledge_page)
                    function(service, permit, request_id=1, target_index=target_index, offset=0)
                except publication.McpNativePagePublicationError:
                    result.append("denied")
            thread = threading.Thread(target=worker); thread.start(); thread.join()
            service.revoke(request.app.state.request_id)
            return {"result": result}
        capture = service._execute_native_page(
            permit, source=source, target_index=target_index, offset=0,
            validate_detached=request.app.state.native_callback,
        )
        if body.get("mode") == "capture_mismatch":
            object.__setattr__(capture, "source", "knowledge" if source == "forge" else "forge")
        service._consume_native_completion(capture)
        if body.get("mode") == "completion_replay":
            replay = None
            try: service._consume_native_completion(capture)
            except McpPrivateContextConfirmationError as error: replay = error.code
            service.revoke(request.app.state.request_id)
            return {"replay": replay}
        return {"ok": True}


def _install_large_native_fixture_inputs(monkeypatch) -> None:
    """Feed the existing real fixture large UTF-8 sources before persistence/capture."""
    import tests.test_app_mcp_private_context_confirmation as consent_tests
    from src.harbour_knowledge_store import HarbourKnowledgeStore

    original_fixture = consent_tests.native_mcp_fixture
    original_write_bytes = Path.write_bytes
    original_create_object = HarbourKnowledgeStore.create_object

    def fixture_with_large_inputs(tmp_path, fixture_monkeypatch):
        fixture_body = None
        with fixture_monkeypatch.context() as input_patch:
            def write_bytes(path, value):
                if (path.name == "README.md"
                        and value == "Public retained project — café\n".encode("utf-8")):
                    value = _LARGE_FORGE_PAYLOAD
                return original_write_bytes(path, value)

            def create_object(store, principal, *args, **kwargs):
                if (kwargs.get("knowledge_id") == "active-note"
                        and kwargs.get("payload_media_type") == "text/plain"):
                    kwargs = dict(kwargs)
                    kwargs["payload_bytes"] = _LARGE_KNOWLEDGE_PAYLOAD
                return original_create_object(store, principal, *args, **kwargs)

            input_patch.setattr(Path, "write_bytes", write_bytes)
            input_patch.setattr(HarbourKnowledgeStore, "create_object", create_object)
            fixture_body = original_fixture.__wrapped__(tmp_path, fixture_monkeypatch)
            fixture = next(fixture_body)
        try:
            yield fixture
        finally:
            if fixture_body is not None:
                try:
                    next(fixture_body)
                except StopIteration:
                    pass

    monkeypatch.setattr(
        consent_tests, "native_mcp_fixture",
        SimpleNamespace(__wrapped__=fixture_with_large_inputs),
    )


@pytest.mark.parametrize("source,schema,byteform", [
    ("forge", publication.MCP_FORGE_PAGE_SCHEMA, "git_blob"),
    ("knowledge", publication.MCP_KNOWLEDGE_PAGE_SCHEMA, "knowledge_payload"),
])
def test_real_persisted_consent_publishes_native_page_and_restores_same_entry(
    consent_app, source, schema, byteform,
):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = client.app.state.service
        entry = service._entries[request_id]
        target_index = next(index for index, item in enumerate(client.app.state.fixture["selection"].items)
                            if item.source == source)
        target = entry.selection.items[target_index]
        key = (target.source, target.selection_ref, target.revision_ref)
        cookie_item = next(item for item in entry.cookie_snapshot.items
                           if (item.source, item.selection_ref, item.revision_ref) == key)
        mcp_item = next(item for item in entry.mcp_snapshot.items
                        if (item.source, item.selection_ref, item.revision_ref) == key)
        assert (cookie_item.source, cookie_item.selection_ref, cookie_item.revision_ref,
                cookie_item.payload_sha256, cookie_item.classification, cookie_item.provenance_ref) == (
                    mcp_item.source, mcp_item.selection_ref, mcp_item.revision_ref,
                    mcp_item.payload_sha256, mcp_item.classification, mcp_item.provenance_ref,
                )
        if source == "knowledge":
            assert cookie_item.boundary_sha256 != mcp_item.boundary_sha256
            assert cookie_item.record_sha256 != mcp_item.record_sha256
        preserved = (entry.selection, entry.cookie_snapshot, entry.mcp_snapshot, entry.page_request_sha256,
                     entry.deadline, entry.mono_deadline, entry.receipt, entry.ack_deadline,
                     entry.ack_mono_deadline, entry.ack_attestation)
        response = _assert_success(client.post(
            "/test/native-page", json={"source": source, "rpc_id": "native-1", "offset": 0},
        ))
        value = json.loads(response.content)
        assert response.content == encode_context_jsonrpc_response("native-1", value["result"])
        result = value["result"]
        assert set(result) == {"schema", "target_index", "offset", "end", "total_bytes",
                               "whole_sha256", "page_sha256", "manifest_digest", "byteform", "content_b64"}
        payload = base64.b64decode(result["content_b64"], validate=True)
        assert result["schema"] == schema
        assert result["target_index"] == target_index and result["offset"] == 0
        assert result["end"] - result["offset"] == len(payload) <= 16_384
        assert result["page_sha256"] == "sha256:" + hashlib.sha256(payload).hexdigest()
        assert result["byteform"] == byteform
        assert (len(result["manifest_digest"]) == 64 if source == "forge"
                else result["manifest_digest"].startswith("sha256:") and len(result["manifest_digest"]) == 71)
        assert not ({"cursor", "complete", "authority", "grant", "cookie", "receipt"} & set(result))
        assert service._entries[request_id] is entry and entry.state == "disclosed"
        assert (entry.selection, entry.cookie_snapshot, entry.mcp_snapshot, entry.page_request_sha256,
                entry.deadline, entry.mono_deadline, entry.receipt, entry.ack_deadline,
                entry.ack_mono_deadline, entry.ack_attestation) == preserved
        assert not service._permits and not service._native_completions
        denied = client.post("/test/native-page", json={"source": source, "mode": "replay"})
        assert denied.status_code == 403
        assert denied.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}


def test_real_persisted_knowledge_publish_never_compares_opaque_completion_reference(
    consent_app, monkeypatch,
):
    from src.harbour_knowledge_staging_destination import StagingMigrationCompletionReference

    equality_calls = []

    def forbidden_equality(_reference, _other):
        equality_calls.append("called")
        raise AssertionError("opaque completion references must not be compared")

    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        service = client.app.state.service
        entry = service._entries[request_id]
        target_index = next(
            index for index, item in enumerate(client.app.state.fixture["selection"].items)
            if item.source == "knowledge"
        )
        preserved = (
            entry.request_id, entry.page_request_sha256, entry.deadline, entry.mono_deadline,
            entry.receipt, entry.ack_wall_time, entry.ack_mono_time, entry.ack_deadline,
            entry.ack_mono_deadline, entry.ack_descriptor, entry.ack_attestation,
            entry.ack_role_hmacs,
        )
        monkeypatch.setattr(StagingMigrationCompletionReference, "__eq__", forbidden_equality)

        response = _assert_success(client.post(
            "/test/native-page",
            json={"source": "knowledge", "rpc_id": "opaque-reference", "offset": 0},
        ))

        result = response.json()["result"]
        payload = "orbital mémoire privée".encode("utf-8")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        assert base64.b64decode(result["content_b64"], validate=True) == payload
        assert result["schema"] == publication.MCP_KNOWLEDGE_PAGE_SCHEMA
        assert result["target_index"] == target_index
        assert result["offset"] == 0 and result["end"] == result["total_bytes"] == len(payload)
        assert result["page_sha256"] == result["whole_sha256"] == digest
        assert equality_calls == []
        assert service._entries[request_id] is entry and entry.request_id == request_id
        assert (
            entry.request_id, entry.page_request_sha256, entry.deadline, entry.mono_deadline,
            entry.receipt, entry.ack_wall_time, entry.ack_mono_time, entry.ack_deadline,
            entry.ack_mono_deadline, entry.ack_descriptor, entry.ack_attestation,
            entry.ack_role_hmacs,
        ) == preserved
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source", ["forge", "knowledge"])
def test_fresh_permit_page_keeps_deadlines_and_old_completion_is_one_shot(consent_app, source):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; entry = service._entries[request_id]
        deadlines = (entry.deadline, entry.mono_deadline, entry.ack_deadline, entry.ack_mono_deadline)
        offset = 0; pages = []
        for rpc_id in range(1, 1025):
            result = _assert_success(client.post(
                "/test/native-page", json={"source": source, "rpc_id": rpc_id, "offset": offset},
            )).json()["result"]
            pages.append(result)
            assert result["offset"] == offset
            assert (entry.deadline, entry.mono_deadline, entry.ack_deadline, entry.ack_mono_deadline) == deadlines
            if result["end"] == result["total_bytes"]: break
            assert result["end"] > offset
            offset = result["end"]
        else: pytest.fail("native page traversal did not reach EOF")
        payload = b"".join(base64.b64decode(page["content_b64"], validate=True) for page in pages)
        assert len(payload) == pages[0]["total_bytes"]
        assert "sha256:" + hashlib.sha256(payload).hexdigest() == pages[0]["whole_sha256"]
        assert all(page["whole_sha256"] == pages[0]["whole_sha256"] for page in pages)
        assert all(page["manifest_digest"] == pages[0]["manifest_digest"] for page in pages)
        eof = client.post("/test/native-page", json={"source": source, "rpc_id": 2048, "offset": len(payload)})
        if source == "knowledge" or not payload:
            eof_result = _assert_success(eof).json()["result"]
            assert eof_result["offset"] == eof_result["end"] == eof_result["total_bytes"]
            assert base64.b64decode(eof_result["content_b64"], validate=True) == b""
            service.revoke(request_id)
        else:
            assert eof.status_code == 403 and eof.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert request_id not in service._entries


@pytest.mark.parametrize("source", ["forge", "knowledge"])
def test_real_large_native_source_reads_second_page_with_fresh_permit_and_no_renewal(
    consent_app, monkeypatch, source,
):
    _install_large_native_fixture_inputs(monkeypatch)
    _install_route(consent_app)
    expected_payload = _LARGE_FORGE_PAYLOAD if source == "forge" else _LARGE_KNOWLEDGE_PAYLOAD
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; entry = service._entries[request_id]
        preserved = (
            entry.selection, entry.cookie_snapshot, entry.mcp_snapshot, entry.page_bytes,
            entry.page_request_sha256, entry.deadline, entry.mono_deadline,
            entry.ack_deadline, entry.ack_mono_deadline, entry.receipt,
            entry.ack_descriptor, entry.ack_attestation, entry.ack_role_hmacs,
        )
        first = _assert_success(client.post(
            "/test/native-page", json={"source": source, "rpc_id": "large-1", "offset": 0},
        )).json()["result"]
        assert 0 < first["end"] < first["total_bytes"]
        assert first["total_bytes"] == len(expected_payload)
        assert service._entries[request_id] is entry and entry.state == "disclosed"
        assert (
            entry.selection, entry.cookie_snapshot, entry.mcp_snapshot, entry.page_bytes,
            entry.page_request_sha256, entry.deadline, entry.mono_deadline,
            entry.ack_deadline, entry.ack_mono_deadline, entry.receipt,
            entry.ack_descriptor, entry.ack_attestation, entry.ack_role_hmacs,
        ) == preserved
        second = _assert_success(client.post(
            "/test/native-page",
            json={"source": source, "rpc_id": "large-2", "offset": first["end"]},
        )).json()["result"]
        assert second["offset"] == first["end"] and second["end"] > second["offset"]
        first_bytes = base64.b64decode(first["content_b64"], validate=True)
        second_bytes = base64.b64decode(second["content_b64"], validate=True)
        assert first_bytes + second_bytes == expected_payload[:second["end"]]
        assert first["whole_sha256"] == second["whole_sha256"] == (
            "sha256:" + hashlib.sha256(expected_payload).hexdigest()
        )
        assert service._entries[request_id] is entry and entry.state == "disclosed"
        assert (
            entry.selection, entry.cookie_snapshot, entry.mcp_snapshot, entry.page_bytes,
            entry.page_request_sha256, entry.deadline, entry.mono_deadline,
            entry.ack_deadline, entry.ack_mono_deadline, entry.receipt,
            entry.ack_descriptor, entry.ack_attestation, entry.ack_role_hmacs,
        ) == preserved
        old_permit = client.post(
            "/test/native-page", json={"source": source, "mode": "old_permit", "permit_index": 0},
        )
        assert old_permit.status_code == 403
        assert old_permit.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert service._entries[request_id] is entry and entry.state == "disclosed"
        assert (
            entry.selection, entry.cookie_snapshot, entry.mcp_snapshot, entry.page_bytes,
            entry.page_request_sha256, entry.deadline, entry.mono_deadline,
            entry.ack_deadline, entry.ack_mono_deadline, entry.receipt,
            entry.ack_descriptor, entry.ack_attestation, entry.ack_role_hmacs,
        ) == preserved
        replay = _assert_success(client.post(
            "/test/native-execute", json={"source": source, "mode": "completion_replay"},
        ))
        assert replay.json() == {"replay": "permit_invalid"}
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("scenario", ["source_target_mismatch", "forged_permit"])
def test_source_target_mismatch_and_forged_permit_fail_without_egress(consent_app, scenario):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        if scenario == "source_target_mismatch":
            mismatch = client.post("/test/native-page", json={"source": "forge", "target_source": "knowledge"})
            assert mismatch.status_code == 403 and mismatch.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
            assert request_id not in service._entries
        else:
            forged = client.post("/test/native-page", json={"source": "knowledge", "mode": "forged"})
            assert forged.status_code == 403 and forged.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}


@pytest.mark.parametrize("page,source", [
    (ForgeContextPage(b"abc", 3, 0, 3, "sha256:" + hashlib.sha256(b"abc").hexdigest(),
                      "sha256:" + hashlib.sha256(b"abc").hexdigest(), "a" * 64), "forge"),
    (KnowledgeContextPage(b"abc", 3, 0, 3, "sha256:" + hashlib.sha256(b"abc").hexdigest(),
                          "sha256:" + hashlib.sha256(b"abc").hexdigest(), "sha256:" + "a" * 64), "knowledge"),
])
def test_native_page_abi_exact_scalars_hashes_and_byteforms(page, source):
    values = publication._page_values(page, source)
    result = publication._result_for_values(source, values, 0)
    assert base64.b64decode(result["content_b64"], validate=True) == b"abc"
    with pytest.raises(publication.McpNativePagePublicationError):
        publication._page_values(page, "knowledge" if source == "forge" else "forge")


@pytest.mark.parametrize("source,field,value", [
    ("forge", "total", True), ("forge", "offset", -1),
    ("forge", "end", 4), ("forge", "payload_bytes", bytearray(b"abc")),
    ("forge", "manifest_digest", "sha256:" + "a" * 64),
    ("knowledge", "total", (1 << 53)), ("knowledge", "page_sha256", "sha256:" + "0" * 64),
    ("knowledge", "manifest_digest", "a" * 64),
])
def test_native_page_normalizer_rejects_nonexact_types_ranges_and_digest_forms(source, field, value):
    digest = "sha256:" + hashlib.sha256(b"abc").hexdigest()
    page = (ForgeContextPage(b"abc", 3, 0, 3, digest, digest, "a" * 64)
            if source == "forge" else KnowledgeContextPage(b"abc", 3, 0, 3, digest, digest, "sha256:" + "a" * 64))
    object.__setattr__(page, field, value)
    with pytest.raises(publication.McpNativePagePublicationError):
        publication._page_values(page, source)


@pytest.mark.parametrize("request_id,target_index,offset", [
    (True, 0, 0), (1, True, 0), (1, 0, False), (1, 0, (1 << 53)),
])
def test_publication_rejects_nonexact_json_scalars(request_id, target_index, offset):
    with pytest.raises(publication.McpNativePagePublicationError):
        publication._validate_request(object(), object(), "forge", request_id, target_index, offset)


@pytest.mark.parametrize("source,field,value", [
    ("forge", "rpc_id", True), ("knowledge", "rpc_id", ""),
    ("forge", "target_index", True), ("knowledge", "target_index", (1 << 53)),
    ("forge", "offset", False), ("knowledge", "offset", (1 << 53)),
])
def test_real_publication_rejects_hostile_json_scalars_without_egress(consent_app, source, field, value):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        body = {"source": source, field: value}
        response = client.post("/test/native-page", json=body)
        assert response.status_code == 403 and response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source,drift", [
    ("forge", "source"), ("knowledge", "source"),
    ("forge", "lease"), ("knowledge", "lease"),
    ("forge", "ack"), ("knowledge", "ack"),
    ("forge", "pid"), ("knowledge", "pid"),
])
def test_source_lease_ack_and_pid_drift_never_egress_content(consent_app, source, drift):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        response = client.post("/test/native-page", json={"source": source, "drift": drift})
        assert response.status_code == 403 and response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert not service._native_completions
        if request_id in service._entries:
            service.revoke(request_id)
        assert request_id not in service._entries and not service._permits


@pytest.mark.parametrize("source,drift", [
    ("forge", "source"), ("knowledge", "source"),
    ("forge", "lease"), ("knowledge", "ack"),
])
def test_drift_during_detached_encoding_is_rechecked_before_bytes_escape(consent_app, source, drift):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; entry = service._entries[request_id]
        target = next(item for item in entry.selection.items if item.source == source)
        def callback(_page):
            if drift == "source": object.__setattr__(target, "source", "planning")
            elif drift == "lease":
                client.app.state.drift_holder = Permit(); entry.lease_ref = weakref.ref(client.app.state.drift_holder)
            else: entry.ack_deadline = entry.issued
            return None
        client.app.state.native_callback = callback
        response = client.post("/test/native-execute", json={"source": source})
        assert response.status_code == 403
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source,kind", [
    ("forge", "return"), ("forge", "generator"), ("knowledge", "coroutine"),
    ("knowledge", "async_generator"), ("knowledge", "error"),
    ("knowledge", "cancel"), ("forge", "reentry"),
])
def test_detached_callback_reentry_deferred_and_failure_paths_scrub(consent_app, source, kind):
    _install_route(consent_app)
    with TestClient(consent_app, raise_server_exceptions=False) as client:
        request_id = _authorize(client); service = client.app.state.service
        async def coroutine(): return None
        def generator(): yield None
        async def async_generator(): yield None
        if kind == "return": callback = lambda _page: True
        elif kind == "generator": callback = lambda _page: generator()
        elif kind == "coroutine": callback = lambda _page: coroutine()
        elif kind == "async_generator": callback = lambda _page: async_generator()
        elif kind == "error":
            def callback(_page): raise RuntimeError("private provider detail")
        elif kind == "cancel":
            def callback(_page): raise KeyboardInterrupt()
        else:
            def callback(_page):
                with pytest.raises(McpPrivateContextConfirmationError):
                    service.issue_permit(request_id)
                return None
        client.app.state.native_callback = callback
        response = client.post("/test/native-execute", json={"source": source})
        assert response.status_code == 403
        assert response.json() == {"error": "context_stale" if kind in {"error", "cancel"} else "invalid_request" if kind != "reentry" else "revoked"}
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source,case", [
    ("forge", "helper"), ("forge", "reader_init"), ("forge", "reader_new"),
    ("forge", "reader_getattribute"), ("forge", "reader_range_alias"),
    ("forge", "partial_range"), ("knowledge", "helper"), ("knowledge", "operation"),
    ("knowledge", "capture"), ("knowledge", "body"),
    ("knowledge", "staging"), ("knowledge", "store"),
])
def test_native_helper_provider_and_body_reader_substitution_denies_before_fake_runs(
    consent_app, monkeypatch, source, case, request, tmp_path,
):
    if case == "reader_new" and _isolate_inherited_new_mutation(request, tmp_path):
        return
    import src.forge_context_page as forge_page
    import src.forge_context_reader as forge_reader
    import src.harbour_knowledge_active_runtime as knowledge
    import src.harbour_knowledge_staging_destination as staging
    import src.harbour_knowledge_store as store
    _install_route(consent_app); calls = []
    def fake(*_args, **_kwargs):
        calls.append(case)
        raise AssertionError("substituted body reader ran")
    targets = {
        "helper": ((forge_page, "read_native_forge_context_page") if source == "forge"
                   else (knowledge, "read_native_knowledge_context_page")),
        "reader_init": (forge_reader.ImmutableForgeContextReader, "__init__"),
        "reader_new": (forge_reader.ImmutableForgeContextReader, "__new__"),
        "reader_getattribute": (forge_reader.ImmutableForgeContextReader, "__getattribute__"),
        "reader_range_alias": (forge_reader, "ForgeContentRange"),
        "partial_range": (forge_reader.ImmutableForgeContextReader, "read_exact_range"),
        "operation": (knowledge.ActiveKnowledgeComposition, "_context_operation"),
        "capture": (knowledge.ActiveKnowledgeComposition, "_capture_context_material"),
        "body": (knowledge.ActiveKnowledgeComposition, "_read_context_payload"),
        "staging": (staging.HarbourKnowledgeStagingDestinationService, "selected_runtime"),
        "store": (store.HarbourKnowledgeStore, "read_payload_at_generation"),
    }
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        _assert_success(client.post("/test/issue-native-permit"))
        with monkeypatch.context() as patch:
            patch.setattr(*targets[case], fake)
            response = client.post("/test/native-page", json={"source": source, "mode": "preissued"})
        assert response.status_code == 403
        assert response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert calls == []
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("case", ["operation", "capture", "body", "staging", "store"])
def test_knowledge_instance_method_shadow_routes_are_rejected(consent_app, monkeypatch, case):
    _install_route(consent_app); calls = []
    def fake(*_args, **_kwargs): calls.append(case)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        _assert_success(client.post("/test/issue-native-permit"))
        composition = service._producer._knowledge._composition
        selected = composition._staging.selected_runtime(service._entries[request_id].owner_sid)
        targets = {
            "operation": (composition, "_context_operation"),
            "capture": (composition, "_capture_context_material"),
            "body": (composition, "_read_context_payload"),
            "staging": (composition._staging, "selected_runtime"),
            "store": (selected[4], "read_payload_at_generation"),
        }
        with monkeypatch.context() as patch:
            patch.setattr(*targets[case], fake, raising=False)
            response = client.post("/test/native-page", json={"source": "knowledge", "mode": "preissued"})
        assert response.status_code == 403 and calls == []
        assert request_id not in service._entries and not service._native_completions


def test_exact_length_forged_partial_forge_range_is_blocked_before_substituted_reader_runs(
    consent_app, monkeypatch,
):
    from src.forge_context_reader import ImmutableForgeContextReader
    original = ImmutableForgeContextReader.read_exact_range; calls = []
    def forged(self, *args, **kwargs):
        calls.append("ran")
        result = original(self, *args, **kwargs)
        object.__setattr__(result, "content", b"x" * len(result.content))
        return result
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        _assert_success(client.post("/test/issue-native-permit"))
        with monkeypatch.context() as patch:
            patch.setattr(ImmutableForgeContextReader, "read_exact_range", forged)
            response = client.post("/test/native-page", json={"source": "forge", "mode": "preissued"})
        assert response.status_code == 403 and calls == []
        assert request_id not in service._entries and not service._native_completions


@pytest.mark.parametrize("case", [
    "range_init", "range_new", "range_getattribute", "range_post_init", "range_content_slot",
    "forge_page_init", "forge_page_new", "forge_page_getattribute", "forge_page_post_init",
    "forge_page_payload_slot", "knowledge_page_init", "knowledge_page_new",
    "knowledge_page_getattribute", "knowledge_page_post_init", "knowledge_page_payload_slot",
])
def test_native_value_class_construction_access_and_slots_are_pinned_before_source_calls(
    consent_app, monkeypatch, case, request, tmp_path,
):
    if case.endswith("_new") and _isolate_inherited_new_mutation(request, tmp_path):
        return
    import src.forge_context_page as forge_page
    import src.harbour_knowledge_active_runtime as knowledge
    import src.repo_git_adapter as repo_git

    source = "knowledge" if case.startswith("knowledge") else "forge"
    value_class = (repo_git.ForgeContentRange if case.startswith("range") else
                   knowledge.KnowledgeContextPage if case.startswith("knowledge") else
                   forge_page.ForgeContextPage)
    attribute = ("content" if case == "range_content_slot" else
                 "payload_bytes" if case.endswith("payload_slot") else
                 "__post_init__" if case.endswith("post_init") else
                 "__getattribute__" if case.endswith("getattribute") else
                 "__new__" if case.endswith("new") else "__init__")
    calls = []
    def fake(*_args, **_kwargs):
        calls.append(case)
        raise AssertionError("substituted value-class path ran")
    replacement = property(fake) if attribute in {"content", "payload_bytes"} else (
        staticmethod(fake) if attribute == "__new__" else fake
    )
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        _assert_success(client.post("/test/issue-native-permit"))
        with monkeypatch.context() as patch:
            patch.setattr(value_class, attribute, replacement)
            response = client.post("/test/native-page", json={"source": source, "mode": "preissued"})
        assert response.status_code == 403
        assert response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert calls == []
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source", ["forge", "knowledge"])
@pytest.mark.parametrize("case", ["page_getattribute", "page_payload_slot"])
def test_detached_page_class_dispatch_swap_is_denied_before_page_fields_are_read(
    consent_app, monkeypatch, source, case,
):
    import src.forge_context_page as forge_page
    import src.harbour_knowledge_active_runtime as knowledge

    page_class = forge_page.ForgeContextPage if source == "forge" else knowledge.KnowledgeContextPage
    calls = []
    def fake(*_args, **_kwargs):
        calls.append(case)
        raise AssertionError("detached page class substitution ran")
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        with monkeypatch.context() as patch:
            def callback(_page):
                replacement = fake if case == "page_getattribute" else property(fake)
                patch.setattr(
                    page_class,
                    "__getattribute__" if case == "page_getattribute" else "payload_bytes",
                    replacement,
                )
                return None
            client.app.state.native_callback = callback
            response = client.post("/test/native-execute", json={"source": source})
        assert response.status_code == 403 and response.json() == {"error": "context_stale"}
        assert calls == []
        assert request_id not in service._entries and not service._native_completions


@pytest.mark.parametrize("case", ["composition", "staging", "store"])
@pytest.mark.parametrize("detached", [False, True])
def test_knowledge_effective_getattribute_dispatch_is_pinned_before_instance_access(
    consent_app, monkeypatch, case, detached,
):
    import src.harbour_knowledge_active_runtime as knowledge
    import src.harbour_knowledge_staging_destination as staging
    import src.harbour_knowledge_store as store

    target_class = {
        "composition": knowledge.ActiveKnowledgeComposition,
        "staging": staging.HarbourKnowledgeStagingDestinationService,
        "store": store.HarbourKnowledgeStore,
    }[case]
    original_getattribute = target_class.__getattribute__
    calls = []
    def fake(instance, name):
        calls.append(case)
        return original_getattribute(instance, name)
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        if detached:
            with monkeypatch.context() as patch:
                def callback(_page):
                    patch.setattr(target_class, "__getattribute__", fake)
                    return None
                client.app.state.native_callback = callback
                response = client.post("/test/native-execute", json={"source": "knowledge"})
            assert response.status_code == 403 and response.json() == {"error": "context_stale"}
        else:
            _assert_success(client.post("/test/issue-native-permit"))
            with monkeypatch.context() as patch:
                patch.setattr(target_class, "__getattribute__", fake)
                response = client.post(
                    "/test/native-page", json={"source": "knowledge", "mode": "preissued"},
                )
            assert response.status_code == 403
            assert response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert calls == []
        assert request_id not in service._entries and not service._native_completions


@pytest.mark.parametrize("source,case", [
    ("forge", "forge_metadata"), ("forge", "local_forge"), ("forge", "retained_store"),
    ("knowledge", "knowledge_producer"), ("knowledge", "composition"),
    ("knowledge", "staging_identity"),
])
def test_selected_native_provider_identity_swaps_are_rejected(
    consent_app, monkeypatch, source, case,
):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; producer = service._producer
        _assert_success(client.post("/test/issue-native-permit"))
        targets = {
            "forge_metadata": (producer, "_forge"),
            "local_forge": (producer._forge, "_local_forge"),
            "retained_store": (producer._forge._local_forge, "store"),
            "knowledge_producer": (producer, "_knowledge"),
            "composition": (producer._knowledge, "_composition"),
            "staging_identity": (producer._knowledge._composition, "_staging"),
        }
        with monkeypatch.context() as patch:
            patch.setattr(*targets[case], object())
            response = client.post("/test/native-page", json={"source": source, "mode": "preissued"})
        assert response.status_code == 403 and response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert request_id not in service._entries and not service._native_completions


@pytest.mark.parametrize("source", ["forge", "knowledge"])
def test_selected_provider_identity_drift_during_detached_encoding_is_rejected(
    consent_app, monkeypatch, source,
):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; producer = service._producer
        with monkeypatch.context() as patch:
            def callback(_page):
                if source == "forge": patch.setattr(producer._forge._local_forge, "store", object())
                else: patch.setattr(producer._knowledge._composition, "_staging", object())
                return None
            client.app.state.native_callback = callback
            response = client.post("/test/native-execute", json={"source": source})
        assert response.status_code == 403
        assert request_id not in service._entries and not service._native_completions


@pytest.mark.parametrize("source,case", [
    ("forge", "page_class"), ("knowledge", "page_class"),
    ("forge", "reader_module_class"), ("forge", "range_module_class"),
    ("forge", "metadata_class"), ("forge", "local_forge_class"),
    ("knowledge", "store_class"), ("knowledge", "staging_class"),
    ("forge", "producer_pin"), ("knowledge", "producer_pin"),
    ("forge", "normalizer"), ("knowledge", "result_builder"),
    ("forge", "encoder"), ("knowledge", "base64"), ("forge", "base64_decode"),
    ("forge", "service_class_execute"), ("knowledge", "service_class_consume"),
    ("forge", "service_class_reserve"), ("knowledge", "service_class_reserve"),
    ("forge", "service_class_scrub"), ("knowledge", "service_class_scrub"),
    ("forge", "reject_helper"), ("knowledge", "reject_helper"),
])
def test_page_publication_class_function_and_service_class_substitution_is_fixed_failure(
    consent_app, monkeypatch, source, case,
):
    import src.forge_context_page as forge_page
    import src.forge_context_reader as forge_reader
    import src.harbour_knowledge_active_runtime as knowledge
    import src.harbour_knowledge_staging_destination as staging
    import src.harbour_knowledge_store as store
    import src.context_manifest_producer as combined
    import src.mcp_context_contract as contract
    import src.repo_git_adapter as repo_git
    from src.mcp_private_context_confirmation import McpPrivateContextConfirmationService
    _install_route(consent_app); calls = []
    def fake(*_args, **_kwargs): calls.append(case)
    targets = {
        "page_class": ((forge_page, "ForgeContextPage") if source == "forge"
                       else (knowledge, "KnowledgeContextPage")),
        "reader_module_class": (forge_reader, "ImmutableForgeContextReader"),
        "range_module_class": (repo_git, "ForgeContentRange"),
        "metadata_class": (forge_page, "ForgeContextMetadata"),
        "local_forge_class": (forge_page, "LocalProjectForge"),
        "store_class": (store, "HarbourKnowledgeStore"),
        "staging_class": (staging, "HarbourKnowledgeStagingDestinationService"),
        "producer_pin": (combined.CombinedContextManifestProducer, "_assert_mcp_provider_pins"),
        "normalizer": (publication, "_page_values"),
        "result_builder": (publication, "_result_for_values"),
        "encoder": (contract, "encode_context_jsonrpc_response"),
        "base64": (publication.base64, "b64encode"),
        "base64_decode": (publication.base64, "b64decode"),
        "service_class_execute": (McpPrivateContextConfirmationService, "_execute_native_page"),
        "service_class_consume": (McpPrivateContextConfirmationService, "_consume_native_completion"),
        "service_class_reserve": (McpPrivateContextConfirmationService, "_reserve_permit"),
        "service_class_scrub": (McpPrivateContextConfirmationService, "_scrub"),
        "reject_helper": (publication, "_reject_preissued_permit"),
    }
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        _assert_success(client.post("/test/issue-native-permit"))
        replacement = object() if case == "page_class" else fake
        with monkeypatch.context() as patch:
            patch.setattr(*targets[case], replacement)
            response = client.post("/test/native-page", json={"source": source, "mode": "preissued"})
        assert response.status_code == 403 and response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert calls == []
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source", ["forge", "knowledge"])
def test_cross_thread_permit_use_has_no_egress_and_explicit_final_revoke(consent_app, source):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        response = _assert_success(client.post("/test/native-execute", json={"source": source, "mode": "thread"}))
        assert response.json() == {"result": ["denied"]}
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source,method", [
    ("forge", "_execute_native_page"), ("knowledge", "_consume_native_completion"),
    ("forge", "_reserve_permit"), ("knowledge", "_reserve_permit"),
    ("forge", "_scrub"), ("knowledge", "_scrub"),
])
def test_service_instance_method_shadow_denies_without_page_bytes(consent_app, source, method):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; calls = []
        _assert_success(client.post("/test/issue-native-permit"))
        setattr(service, method, lambda *_args, **_kwargs: calls.append("ran"))
        response = client.post("/test/native-page", json={"source": source, "mode": "preissued"})
        assert response.status_code == 403 and response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert calls == []
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("case", [
    "class:_reject_authority_reentry", "instance:_reject_authority_reentry",
    "class:_recipient", "instance:_recipient",
    "class:_terminate", "instance:_terminate",
    "class:_reject_native_permit", "instance:_reject_native_permit",
    "class:__getattribute__", "field:_lock", "field:_entries",
    "field:_permits", "field:_exchanges", "field:_completions",
    "field:_native_completions",
])
def test_rejection_uses_original_state_without_hostile_service_dispatch(
    consent_app, monkeypatch, case,
):
    _install_route(consent_app)
    calls = []
    originals = {}

    def hostile(*_args, **_kwargs):
        calls.append(case)
        raise AssertionError("replacement dispatch ran")

    class HostileState:
        __enter__ = hostile
        __exit__ = hostile
        __getattribute__ = hostile

    @consent_app.post("/test/native-rejection-state")
    async def reject_state(request: Request):
        service = request.app.state.service
        request_id = request.app.state.request_id
        permit = service.issue_permit(request_id)
        originals.update({
            name: getattr(service, name) for name in (
                "_entries", "_permits", "_exchanges", "_completions", "_native_completions",
            )
        })
        target_index = next(
            index for index, item in enumerate(request.app.state.fixture["selection"].items)
            if item.source == "forge"
        )
        publish = publication.publish_forge_page
        scope, attribute = case.split(":", 1)
        original_field = getattr(service, attribute) if scope == "field" else None
        with monkeypatch.context() as patch:
            if scope == "class":
                patch.setattr(type(service), attribute, hostile, raising=False)
            elif scope == "instance":
                patch.setattr(service, attribute, hostile, raising=False)
            else:
                # MonkeyPatch checks isinstance(value, Notset), which itself
                # asks this hostile value for __class__ before installation.
                setattr(service, attribute, HostileState())
            # Capture all route inputs before installing hostile service access.
            # The patch unwinds before FastAPI handles the fixed exception.
            try:
                encoded = publish(service, permit, request_id=7, target_index=target_index, offset=0)
            finally:
                if scope == "field":
                    setattr(service, attribute, original_field)
        return Response(encoded, media_type="application/json")

    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        response = client.post("/test/native-rejection-state")
        assert response.status_code == 403
        assert response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert calls == []
        assert request_id not in originals["_entries"]
        assert all(not originals[name] for name in (
            "_permits", "_exchanges", "_completions", "_native_completions",
        ))


@pytest.mark.parametrize("case", [
    "empty", "copied_anchor", "callable_anchor", "missing_anchor", "dead_anchor",
    "wrong_service", "rejection_row_callable",
])
def test_unproved_rejection_anchor_cannot_consume_a_genuine_permit(consent_app, case):
    from src.mcp_private_context_confirmation import McpPrivateContextConfirmationService
    _install_route(consent_app)
    calls = []

    def hostile():
        calls.append("called")
        raise AssertionError("unproved reference called")

    @consent_app.post("/test/native-unproved-anchor")
    async def unproved_anchor(request: Request):
        service = request.app.state.service
        request_id = request.app.state.request_id
        genuine = service.issue_permit(request_id)
        slot = Permit.__dict__["_Permit__native_rejection_capsule_ref"]
        anchor = slot.__get__(genuine, Permit)
        assert type(anchor) is weakref.ReferenceType
        entry = service._entries[request_id]
        row = service._permits[id(genuine)]
        generation, nonce = entry.generation, entry.permit_nonce
        deadlines = entry.permit_deadline, entry.permit_mono_deadline
        target_index = next(
            index for index, item in enumerate(request.app.state.fixture["selection"].items)
            if item.source == "forge"
        )
        invalid = genuine
        other = None
        row_entry_ref = row.entry_ref
        if case in {"empty", "copied_anchor"}:
            invalid = Permit()
            if case == "copied_anchor":
                slot.__set__(invalid, anchor)
        elif case == "callable_anchor":
            slot.__set__(genuine, hostile)
        elif case == "missing_anchor":
            slot.__delete__(genuine)
        elif case == "dead_anchor":
            dead = Permit()
            dead_ref = weakref.ref(dead)
            del dead
            assert dead_ref() is None
            slot.__set__(genuine, dead_ref)
        elif case == "wrong_service":
            fixture = request.app.state.fixture
            other = McpPrivateContextConfirmationService(
                auth_manager=fixture["auth"], authority=fixture["authority"],
                producer=fixture["combined"], cookie_authority=service._cookie_authority,
                session_factory=fixture["session_factory"],
            )
        else:
            # Original-ledger corruption is outside normal publication authority.
            # The rejection capability must still decline unsafe dereferencing.
            object.__setattr__(row, "entry_ref", hostile)
        try:
            if case == "rejection_row_callable":
                assert publication._TRUSTED_REJECT_NATIVE_PERMIT(service, genuine) is False
            else:
                with pytest.raises(publication.McpNativePagePublicationError) as rejected:
                    publication.publish_forge_page(
                        other if other is not None else service, invalid,
                        request_id=7, target_index=target_index, offset=0,
                    )
                assert rejected.value.code == publication.MCP_NATIVE_PAGE_ERROR
        finally:
            slot.__set__(genuine, anchor)
            object.__setattr__(row, "entry_ref", row_entry_ref)
            if other is not None:
                other.close()
        assert calls == []
        assert service._entries[request_id] is entry
        assert service._permits[id(genuine)] is row
        assert entry.state == "permitted"
        assert (entry.generation, entry.permit_nonce) == (generation, nonce)
        assert (entry.permit_deadline, entry.permit_mono_deadline) == deadlines
        encoded = publication.publish_forge_page(
            service, genuine, request_id=7, target_index=target_index, offset=0,
        )
        return Response(encoded, media_type="application/json")

    with TestClient(consent_app) as client:
        request_id = _authorize(client)
        response = _assert_success(client.post("/test/native-unproved-anchor"))
        assert response.json()["result"]["schema"] == publication.MCP_FORGE_PAGE_SCHEMA
        assert calls == []
        service = client.app.state.service
        assert service._entries[request_id].state == "disclosed"
        assert not service._permits and not service._native_completions
        service.revoke(request_id)


def test_permit_anchor_does_not_retain_a_disposable_service(consent_app):
    from src.mcp_private_context_confirmation import McpPrivateContextConfirmationService
    _install_route(consent_app)

    @consent_app.post("/test/native-anchor-lifetime")
    async def anchor_lifetime(request: Request):
        fixture = request.app.state.fixture
        other = McpPrivateContextConfirmationService(
            auth_manager=fixture["auth"], authority=fixture["authority"],
            producer=fixture["combined"],
            cookie_authority=request.app.state.service._cookie_authority,
            session_factory=fixture["session_factory"],
        )
        service_ref = weakref.ref(other)
        capsule_ref = weakref.ref(other._native_rejection_capsule)
        surviving = Permit()
        slot = Permit.__dict__["_Permit__native_rejection_capsule_ref"]
        slot.__set__(surviving, capsule_ref)
        del other
        assert service_ref() is None
        assert capsule_ref() is None
        assert slot.__get__(surviving, Permit) is capsule_ref
        return {"collected": True}

    with TestClient(consent_app) as client:
        assert _assert_success(client.post("/test/native-anchor-lifetime")).json() == {"collected": True}


@pytest.mark.parametrize("source", ["forge", "knowledge"])
@pytest.mark.parametrize("scenario", ["capture_mismatch", "completion_replay"])
def test_native_completion_source_mismatch_scrubs_and_completion_replay_is_denied(consent_app, source, scenario):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        if scenario == "capture_mismatch":
            mismatch = client.post("/test/native-execute", json={"source": source, "mode": "capture_mismatch"})
            assert mismatch.status_code == 403 and mismatch.json() == {"error": "permit_invalid"}
        else:
            replay = _assert_success(client.post(
                "/test/native-execute", json={"source": source, "mode": "completion_replay"},
            ))
            assert replay.json() == {"replay": "permit_invalid"}
        assert request_id not in service._entries and not service._native_completions


@pytest.mark.parametrize("source,snapshot_name", [
    ("forge", "cookie_snapshot"), ("forge", "mcp_snapshot"),
    ("knowledge", "cookie_snapshot"), ("knowledge", "mcp_snapshot"),
])
def test_wrong_cookie_or_recipient_snapshot_item_denies_and_scrubs(consent_app, source, snapshot_name):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service; entry = service._entries[request_id]
        snapshot = getattr(entry, snapshot_name)
        items = list(snapshot.items)
        target = next(item for item in entry.selection.items if item.source == source)
        key = (target.source, target.selection_ref, target.revision_ref)
        target_index = next(
            index for index, item in enumerate(items)
            if (item.source, item.selection_ref, item.revision_ref) == key
        )
        wrong = next(item for item in items if item.source != source)
        assert items[target_index].source == source and wrong.source != source
        items[target_index] = wrong
        object.__setattr__(snapshot, "items", tuple(items))
        response = client.post("/test/native-page", json={"source": source})
        assert response.status_code == 403 and response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source", ["forge", "knowledge"])
@pytest.mark.parametrize("swap", [
    "cookie_from_other_recipient_mcp",
    "mcp_from_primary_cookie",
    "mcp_from_other_recipient_mcp",
])
def test_same_source_wrong_kind_and_other_recipient_snapshot_items_deny_without_content(
    consent_app, source, swap,
):
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        second_lease = _lease(fixture, "native-page-other-recipient", token_index=1)
        try:
            second_mcp = fixture["combined"].capture_mcp_snapshot(
                second_lease, fixture["selection"],
            )
            with CookieConfirmationAuthority(fixture["auth"]).hold(
                fixture["cookie"], second_lease.binding.chat_session_id,
            ) as second_binding:
                second_cookie = fixture["combined"].capture_snapshot(
                    second_binding, fixture["selection"],
                )
        finally:
            fixture["authority"].release_read_lease(second_lease)
        request_id = _authorize(client); service = client.app.state.service; entry = service._entries[request_id]
        target = next(item for item in entry.selection.items if item.source == source)
        key = (target.source, target.selection_ref, target.revision_ref)
        primary_cookie_item = next(
            item for item in entry.cookie_snapshot.items
            if (item.source, item.selection_ref, item.revision_ref) == key
        )
        primary_mcp_item = next(
            item for item in entry.mcp_snapshot.items
            if (item.source, item.selection_ref, item.revision_ref) == key
        )
        second_cookie_item = next(
            item for item in second_cookie.items
            if (item.source, item.selection_ref, item.revision_ref) == key
        )
        second_mcp_item = next(
            item for item in second_mcp.items
            if (item.source, item.selection_ref, item.revision_ref) == key
        )
        def source_facts(item):
            return (
                item.source, item.selection_ref, item.revision_ref,
                item.payload_sha256, item.classification, item.provenance_ref,
            )

        assert (
            source_facts(primary_cookie_item) == source_facts(primary_mcp_item)
            == source_facts(second_cookie_item) == source_facts(second_mcp_item)
        )
        # Knowledge's cookie boundary includes chat identity; its source record
        # remains stable. Forge's cookie capture is equal across these chats.
        assert second_cookie_item.record_sha256 == primary_cookie_item.record_sha256
        if source == "forge":
            assert second_cookie_item == primary_cookie_item
        else:
            assert source == "knowledge"
            assert second_cookie_item != primary_cookie_item
            assert second_cookie_item.boundary_sha256 != primary_cookie_item.boundary_sha256
        assert second_mcp_item.boundary_sha256 != primary_mcp_item.boundary_sha256
        assert second_mcp_item.record_sha256 != primary_mcp_item.record_sha256
        if source == "knowledge":
            assert primary_cookie_item.boundary_sha256 != primary_mcp_item.boundary_sha256
            assert primary_cookie_item.record_sha256 != primary_mcp_item.record_sha256
        snapshot_name, replacement = {
            "cookie_from_other_recipient_mcp": ("cookie_snapshot", second_mcp_item),
            "mcp_from_primary_cookie": ("mcp_snapshot", primary_cookie_item),
            "mcp_from_other_recipient_mcp": ("mcp_snapshot", second_mcp_item),
        }[swap]
        snapshot = getattr(entry, snapshot_name)
        items = list(snapshot.items)
        target_index = next(
            index for index, item in enumerate(items)
            if (item.source, item.selection_ref, item.revision_ref) == key
        )
        original = items[target_index]
        expected_original = (
            primary_cookie_item if snapshot_name == "cookie_snapshot" else primary_mcp_item
        )
        assert original == expected_original
        assert source_facts(replacement) == source_facts(original)
        assert replacement != original
        assert replacement.boundary_sha256 != original.boundary_sha256
        assert replacement.record_sha256 != original.record_sha256
        items[target_index] = replacement
        object.__setattr__(snapshot, "items", tuple(items))
        response = client.post("/test/native-page", json={"source": source})
        assert response.status_code == 403
        assert response.json() == {"error": publication.MCP_NATIVE_PAGE_ERROR}
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


@pytest.mark.parametrize("source", ["forge", "knowledge"])
@pytest.mark.parametrize("mutation", ["page_class", "payload_accessor"])
def test_trusted_consume_final_fence_mutation_is_fixed_failure_and_scrubs(
    consent_app, monkeypatch, source, mutation,
):
    import src.forge_context_page as forge_page
    import src.harbour_knowledge_active_runtime as knowledge

    page_module = forge_page if source == "forge" else knowledge
    page_name = "ForgeContextPage" if source == "forge" else "KnowledgeContextPage"
    page_class = getattr(page_module, page_name)
    _install_route(consent_app)
    with TestClient(consent_app) as client:
        request_id = _authorize(client); service = client.app.state.service
        original_clock = service._clock
        consume_clock_states = []
        with monkeypatch.context() as patch:
            def clock():
                value = original_clock()
                entry = service._entries.get(request_id)
                if entry is not None and entry.state == "native_completion_consuming":
                    consume_clock_states.append(entry.state)
                    if len(consume_clock_states) == 3:
                        if mutation == "page_class":
                            patch.setattr(page_module, page_name, object())
                        else:
                            patch.setattr(page_class, "payload_bytes", property(lambda _self: b"forged"))
                return value

            patch.setattr(service, "_clock", clock)
            response = client.post("/test/native-page", json={"source": source})
        assert consume_clock_states == ["native_completion_consuming"] * 3
        assert response.status_code == 403
        assert response.content == b'{"error":"native_page_unavailable"}'
        assert b"content_b64" not in response.content
        assert request_id not in service._entries
        assert not service._permits and not service._native_completions


def test_successful_native_consume_is_terminal_before_frozen_return():
    module = ast.parse(Path(publication.__file__).read_text(encoding="utf-8"))
    publish = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_publish"
    )
    consume_try = publish.body[-2]
    frozen_return = publish.body[-1]
    assert isinstance(consume_try, ast.Try)
    assert len(consume_try.body) == 1 and not consume_try.orelse and not consume_try.finalbody
    consume_statement = consume_try.body[0]
    assert isinstance(consume_statement, ast.Expr)
    assert isinstance(consume_statement.value, ast.Call)
    assert isinstance(consume_statement.value.func, ast.Name)
    assert consume_statement.value.func.id == "_TRUSTED_CONSUME"
    assert isinstance(frozen_return, ast.Return)
    assert isinstance(frozen_return.value, ast.Name)
    assert frozen_return.value.id == "frozen_encoded"
