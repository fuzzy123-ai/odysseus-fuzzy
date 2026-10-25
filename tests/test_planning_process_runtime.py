from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
from datetime import datetime, timezone
import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import socket
import struct
from threading import Event, Thread
import time

import pytest

from core.auth import AuthManager
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_definition_contract import compute_roadmap_content_hash, validate_planning_definition
from src.planning_revision_store import DurablePlanningRevisionStore, DurablePlanningStoreError
from src.planning_roadmap_migration import build_h1r03_migration_manifest
import src.planning_process_runtime as process_runtime


PROJECT_ID = "harbour-one"
OWNER_ID = "fuzzy"
AUTH_CONTEXT_DIGEST = "sha256:" + "4" * 64
ISSUER_ID = "odysseus-planning-default"
KEY_ID = "planning-process-test-key"
ISSUER_SECRET = b"planning-process-runtime-test-secret-0001"


def _lock_holder(root: str, ready: object, release: object) -> None:
    """Hold the real repository interprocess lock in a spawned OS process."""

    from src.planning_revision_store import _interprocess_repository_lock

    with _interprocess_repository_lock(Path(root)):
        ready.set()
        release.wait()


def _spawn_lock_holder(root: Path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_lock_holder, args=(str(root), ready, release))
    process.start()
    assert ready.wait(10)
    return process, release


def _partial_response_peer(connection, startup_ready: object, request_seen: object) -> None:
    startup_ready.set()
    connection.recv_bytes(maxlength=process_runtime.REQUEST_MAX_BYTES)
    # Native Windows message pipes withhold the whole message; POSIX tests a partial frame.
    if os.name != "nt":
        connection._send(struct.pack("!i", 128) + b'{"partial":')
    request_seen.set()
    while True:
        time.sleep(60)


def _chunk_header_then_block_peer(
    connection, startup_ready: object, header_sent: object,
) -> None:
    startup_ready.set()
    request = json.loads(
        connection.recv_bytes(maxlength=process_runtime.REQUEST_MAX_BYTES).decode("ascii")
    )
    total_bytes = process_runtime._CHUNK_RAW_MAX_BYTES + 1
    response = {
        "protocol": process_runtime.PROTOCOL,
        "op": request["op"] + "_header",
        "request_id": request["request_id"],
        "generation": request["generation"],
        "claim_id": request["claim_id"],
        "total_bytes": total_bytes,
        "count": 2,
        "payload_sha256": "sha256:" + "0" * 64,
    }
    connection.send_bytes(process_runtime._encode_frame(
        response, maximum=process_runtime.REQUEST_MAX_BYTES,
    ))
    header_sent.set()
    while True:
        time.sleep(60)


def _protocol_response_peer(
    connection, mode: str, startup_ready: object, request_seen: object,
) -> None:
    startup_ready.set()
    raw = connection.recv_bytes(maxlength=process_runtime.REQUEST_MAX_BYTES)
    request = json.loads(raw.decode("utf-8"))
    request_seen.set()
    if mode in {"eof", "crash"}:
        if mode == "crash":
            os._exit(31)
        connection.close()
        return
    if mode == "empty":
        response = []
    elif mode in {"error", "error_request_id"}:
        response = {
            "protocol": process_runtime.PROTOCOL,
            "op": "read_subject_bound_accepted_migration_bundle_result",
            "status": "error",
            "request_id": request["request_id"], "generation": request["generation"],
            "claim_id": request["claim_id"], "code": "invalid_request",
        }
        if mode == "error_request_id":
            response["request_id"] = "0" * 32
    else:
        response = {
            "protocol": process_runtime.PROTOCOL,
            "op": "read_subject_bound_accepted_migration_bundle_result",
            "request_id": request["request_id"], "generation": request["generation"],
            "claim_id": request["claim_id"], "status": "ok", "bundle": {},
        }
        if mode == "request_id":
            response["request_id"] = "0" * 32
        elif mode == "generation":
            response["generation"] = "0" * 32
        elif mode == "claim_id":
            response["claim_id"] = "0" * 32
    encoded = json.dumps(response, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    connection.send_bytes(encoded)
    if mode not in {"eof", "crash"}:
        while True:
            time.sleep(60)
    connection.close()


def _large_migration_result_peer(connection, operation: str, payload_size: int, response_size) -> None:
    try:
        request = json.loads(connection.recv_bytes().decode("ascii"))
        raw = _canonical_json_frame_of_size(payload_size)
        result = process_runtime._tool_result_wire(operation, json.loads(raw.decode("ascii")))
        response = {
            "protocol": process_runtime.PROTOCOL, "op": operation + "_result",
            "request_id": request["request_id"], "generation": request["generation"],
            "claim_id": request["claim_id"], "status": "ok", "result": result,
        }
        encoded = process_runtime._encode_frame(
            response, maximum=process_runtime._response_total_maximum(operation),
        )
        if len(encoded) <= process_runtime.RESPONSE_MAX_BYTES:
            response_size.value = len(encoded)
        else:
            count = (len(encoded) + process_runtime._CHUNK_RAW_MAX_BYTES - 1) // process_runtime._CHUNK_RAW_MAX_BYTES
            digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
            sizes = []
            for sequence in range(count):
                chunk = encoded[sequence * process_runtime._CHUNK_RAW_MAX_BYTES:(sequence + 1) * process_runtime._CHUNK_RAW_MAX_BYTES]
                frame = {
                    "protocol": response["protocol"], "op": operation + "_chunk", "request_id": response["request_id"],
                    "generation": response["generation"], "claim_id": response["claim_id"], "sequence": sequence,
                    "count": count, "payload_sha256": digest, "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                }
                sizes.append(len(process_runtime._encode_frame(frame, maximum=process_runtime.RESPONSE_MAX_BYTES)))
            response_size.value = max(sizes)
        process_runtime._send_response(connection, response, operation=operation)
    finally:
        connection.close()


def _forged_migration_inventory_peer(connection, payload: object) -> None:
    try:
        request = json.loads(connection.recv_bytes(maxlength=process_runtime.REQUEST_MAX_BYTES).decode("utf-8"))
        response = {
            "protocol": process_runtime.PROTOCOL, "op": "planning_migration_inventory_result",
            "request_id": request["request_id"], "generation": request["generation"],
            "claim_id": request["claim_id"], "status": "ok",
            "result": process_runtime._tool_result_wire("planning_migration_inventory", payload),
        }
        process_runtime._send_response(connection, response, operation="planning_migration_inventory")
    finally:
        connection.close()


def _durable_response_peer(connection, mode: str, startup_ready: object, request_seen: object) -> None:
    startup_ready.set()
    request = json.loads(connection.recv_bytes(maxlength=process_runtime.REQUEST_MAX_BYTES).decode("utf-8"))
    request_seen.set()
    response = {
        "protocol": process_runtime.PROTOCOL,
        "op": request["op"] + "_result",
        "request_id": request["request_id"],
        "generation": request["generation"],
        "claim_id": request["claim_id"],
        "status": "ok",
        "head": {
            "owner_id": OWNER_ID,
            "project_id": PROJECT_ID,
            "roadmap_id": "planning-process-response-roadmap",
            "current_revision_id": "planning-revision:" + "a" * 64,
            "current_content_sha256": "sha256:" + "b" * 64,
            "approved_revision_id": None,
            "generation": 1,
        },
    }
    if mode == "extra_response_key":
        response["owner_id"] = OWNER_ID
    elif mode == "missing_head_generation":
        del response["head"]["generation"]
    elif mode == "stale_request_id":
        response["request_id"] = "0" * 32
    connection.send_bytes(json.dumps(response, sort_keys=True, separators=(",", ":")).encode("ascii"))
    while True:
        time.sleep(60)


def _swap_in_standalone_peer(client, fixture, target, args):
    old_process = client._process
    old_connection = client._connection
    assert old_process is not None and old_connection is not None
    old_process.terminate()
    old_process.join(5)
    _assert_reaped(old_process)
    old_connection.close()
    context = multiprocessing.get_context("spawn")
    parent_connection, peer_connection = context.Pipe(duplex=True)
    peer = context.Process(target=target, args=(peer_connection, *args), daemon=True)
    peer.start()
    peer_connection.close()
    token = client._token
    assert token is not None
    token.process = peer
    token.connection = parent_connection
    client._process = peer
    client._connection = parent_connection
    return peer


def _swap_in_socket_peer(client, target, args):
    old_process = client._process
    old_connection = client._connection
    assert old_process is not None and old_connection is not None
    old_process.terminate()
    old_process.join(5)
    _assert_reaped(old_process)
    old_connection.close()
    left_socket, right_socket = socket.socketpair()
    parent_connection = None
    peer_connection = None
    try:
        parent_connection = process_runtime.Connection(left_socket.detach())
        peer_connection = process_runtime.Connection(right_socket.detach())
    finally:
        left_socket.close()
        right_socket.close()
    context = multiprocessing.get_context("spawn")
    peer = context.Process(target=target, args=(peer_connection, *args), daemon=True)
    try:
        peer.start()
    except Exception:
        parent_connection.close()
        peer_connection.close()
        raise
    peer_connection.close()
    token = client._token
    assert token is not None
    token.process = peer
    token.connection = parent_connection
    client._process = peer
    client._connection = parent_connection
    return peer


def _assert_error(exc_info, code: str) -> None:
    assert exc_info.value.code == code
    assert str(exc_info.value) == code
    assert exc_info.value.__cause__ is None
    if exc_info.value.__context__ is not None and not exc_info.value.__suppress_context__:
        pytest.fail("process-boundary error exposed a chained context")


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        pytest.fail(f"{label} mismatch")


def _assert_private_values_absent(rendered: str, values) -> None:
    if any(value in rendered for value in values):
        pytest.fail("content-free representation exposed private fixture material")


def _observe_requests(monkeypatch: pytest.MonkeyPatch, client, *, operation="read_subject_bound_accepted_migration_bundle"):
    """Count bytes actually sent by this client's parent Connection."""
    connection = client._connection
    assert connection is not None
    sent = Event()
    requests = []
    connection_type = type(connection)
    original = connection_type.send_bytes

    def observed(candidate, raw, *args, **kwargs):
        was_request = False
        if candidate is connection:
            request = json.loads(raw.decode("utf-8"))
            if type(request) is dict and (operation is None or request.get("op") == operation):
                requests.append(request)
                was_request = True
        result = original(candidate, raw, *args, **kwargs)
        if was_request:
            sent.set()
        return result

    monkeypatch.setattr(connection_type, "send_bytes", observed)
    return sent, requests


def _track_runtime_helpers(monkeypatch: pytest.MonkeyPatch):
    helpers = []
    original_start = Thread.start

    def tracked_start(thread, *args, **kwargs):
        target = getattr(thread, "_target", None)
        if getattr(target, "__module__", None) == process_runtime.__name__:
            helpers.append(thread)
        return original_start(thread, *args, **kwargs)

    monkeypatch.setattr(Thread, "start", tracked_start)
    return helpers


def _assert_helpers_finished(helpers) -> None:
    if not helpers or any(helper.is_alive() for helper in helpers):
        pytest.fail("runtime I/O helper was not conclusively joined")


def _assert_exact_request(request, claim) -> None:
    assert set(request) == {
        "protocol", "op", "request_id", "generation", "claim_id", "deadline_ms", "claim",
    }
    assert request["protocol"] == process_runtime.PROTOCOL
    assert request["op"] == "read_subject_bound_accepted_migration_bundle"
    assert len(request["request_id"]) == 32
    _assert_equal(request["generation"], claim.generation, "request generation binding")
    _assert_equal(request["claim_id"], claim.claim_id, "request claim binding")
    assert 1 <= request["deadline_ms"] <= 10_000
    nested = request["claim"]
    assert set(nested) == {
        "owner_id", "subject_id", "project_id", "auth_context_digest", "generation", "claim_id",
    }
    expected_claim = process_runtime._claim_wire(claim)
    for field in expected_claim:
        _assert_equal(nested.get(field), expected_claim[field], f"nested request {field}")


def _assert_exact_durable_request(request, operation: str, claim, body_fields: set[str]) -> None:
    assert set(request) == {
        "protocol", "op", "request_id", "generation", "claim_id", "deadline_ms", "claim", "body",
    }
    assert request["protocol"] == process_runtime.PROTOCOL
    assert request["op"] == operation
    assert type(request["request_id"]) is str and len(request["request_id"]) == 32
    generation = claim["generation"] if type(claim) is dict else claim.generation
    claim_id = claim["claim_id"] if type(claim) is dict else claim.claim_id
    owner_id = claim["owner_id"] if type(claim) is dict else claim.owner_id
    subject_id = claim["subject_id"] if type(claim) is dict else claim.subject_id
    project_id = claim["project_id"] if type(claim) is dict else claim.project_id
    auth_context_digest = claim["auth_context_digest"] if type(claim) is dict else claim.auth_context_digest
    assert request["generation"] == generation
    assert request["claim_id"] == claim_id
    assert type(request["deadline_ms"]) is int and 1 <= request["deadline_ms"] <= 10_000
    assert set(request["claim"]) == {
        "owner_id", "subject_id", "project_id", "auth_context_digest", "generation", "claim_id",
    }
    assert request["claim"]["owner_id"] == owner_id
    assert request["claim"]["subject_id"] == subject_id
    assert request["claim"]["project_id"] == project_id
    assert request["claim"]["auth_context_digest"] == auth_context_digest
    assert request["claim"]["generation"] == generation
    assert request["claim"]["claim_id"] == claim_id
    assert type(request["body"]) is dict and set(request["body"]) == body_fields
    assert not ({"owner_id", "subject_id", "auth_context_digest", "principal", "token", "cookie"} & set(request["body"]))


def _assert_durable_bundle_matches_store(fixture, bundle, principal, roadmap_id: str, revision_id: str) -> None:
    assert type(bundle) is process_runtime.PlanningProcessRevisionBundle
    assert tuple(field.name for field in fields(bundle)) == ("revision", "payload")
    expected_revision = fixture["store"].read_revision(
        principal, owner_id=OWNER_ID, revision_id=revision_id,
    )
    expected_payload = fixture["store"].read_payload(
        principal, owner_id=OWNER_ID, revision_id=revision_id,
    )
    assert type(bundle.revision) is process_runtime.PlanningProcessDurableRevision
    assert tuple(field.name for field in fields(bundle.revision)) == (
        "revision_id", "owner_id", "project_id", "roadmap_id", "revision", "parent_revision_id",
        "payload_schema_id", "payload_sha256", "content_sha256", "revision_state", "created_at",
    )
    for field in fields(bundle.revision):
        _assert_equal(getattr(bundle.revision, field.name), getattr(expected_revision, field.name), f"durable revision {field.name}")
    assert bundle.revision.roadmap_id == roadmap_id
    assert bundle.payload == expected_payload


def _assert_reaped(process) -> None:
    assert process.is_alive() is False
    assert process.exitcode is not None


def _tool_payload(result, tool: str) -> dict:
    assert type(result) is process_runtime.PlanningProcessToolResult
    assert result.tool == tool
    assert result.payload_sha256 == "sha256:" + hashlib.sha256(result.payload).hexdigest()
    return json.loads(result.payload.decode("ascii"))


@pytest.fixture
def accepted_process_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repository_root = Path(__file__).resolve().parents[1]
    definitions_root = tmp_path / "definitions"
    definitions_root.mkdir()
    durable_root = tmp_path / "durable-planning"

    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user(OWNER_ID, "Password-123", is_admin=True)
    subject_id = auth.subject_id_for_username(OWNER_ID)
    assert isinstance(subject_id, str) and subject_id

    issuer = AuthenticatedPrincipalIssuer(
        issuer_id=ISSUER_ID,
        key_id=KEY_ID,
        secret=ISSUER_SECRET,
        clock=lambda: datetime.now(timezone.utc),
    )
    store = DurablePlanningRevisionStore(durable_root, trusted_issuers=(issuer,))
    principal = issuer.issue(
        subject_id=subject_id,
        owner_id=OWNER_ID,
        auth_context_digest=AUTH_CONTEXT_DIGEST,
        ttl_seconds=3600,
        nonce="planning-process-fixture-0001",
    )
    manifest = build_h1r03_migration_manifest(repository_root)
    store.prepare_migration(principal, manifest=manifest)
    store.accept_migration(
        principal,
        batch_id=manifest["batch_id"],
        current_manifest=lambda: deepcopy(manifest),
    )
    _append_full_history(store, issuer, principal)
    expected_bundle = store.read_subject_bound_accepted_migration_bundle(
        principal, project_id=PROJECT_ID
    )

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv(
        "ODYSSEUS_PLANNING_ISSUER_SECRET_B64",
        base64.b64encode(ISSUER_SECRET).decode("ascii"),
    )
    monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID", KEY_ID)

    client = process_runtime.PlanningProcessClient(
        repo_root=repository_root,
        definitions_root=definitions_root,
        durable_root=durable_root,
        legacy_owner=OWNER_ID,
    )
    yield {
        "repository_root": repository_root,
        "definitions_root": definitions_root,
        "durable_root": durable_root,
        "auth": auth,
        "issuer": issuer,
        "store": store,
        "principal": principal,
        "manifest": manifest,
        "expected_bundle": expected_bundle,
        "subject_id": subject_id,
        "client": client,
    }
    client.close()


def _new_claim(fixture, *, client=None, subject_id=None, owner_id=OWNER_ID):
    return process_runtime._create_subject_bound_accepted_migration_claim(
        client or fixture["client"],
        owner_id=owner_id,
        subject_id=subject_id or fixture["subject_id"],
        project_id=PROJECT_ID,
        auth_context_digest=AUTH_CONTEXT_DIGEST,
    )


def _start_and_claim(fixture, *, client=None, subject_id=None, owner_id=OWNER_ID):
    client = client or fixture["client"]
    handshake = client.start()
    claim = _new_claim(
        fixture, client=client, subject_id=subject_id, owner_id=owner_id,
    )
    return handshake, claim


def _new_operation_claim(
    fixture, *, client=None, subject_id=None, owner_id=OWNER_ID,
    auth_context_digest=AUTH_CONTEXT_DIGEST,
):
    return process_runtime._create_planning_process_operation_claim(
        client or fixture["client"],
        owner_id=owner_id,
        subject_id=subject_id or fixture["subject_id"],
        project_id=PROJECT_ID,
        auth_context_digest=auth_context_digest,
    )


def test_namespace_reads_keep_legacy_names_and_prefix_mcp_names(accepted_process_fixture):
    """The two list surfaces remain distinct while crossing the child boundary."""
    fixture = accepted_process_fixture
    from tests.test_planning_definition_projection import definition_fixture

    definition = definition_fixture(PROJECT_ID, "namespace-process-roadmap", include_draft=False)
    (fixture["definitions_root"] / "namespace.json").write_text(
        json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8",
    )
    _start_and_claim(fixture)
    client = fixture["client"]

    legacy_projects = _tool_payload(
        client.planning_list_projects(_new_operation_claim(fixture)), "planning_list_projects",
    )
    assert legacy_projects["read_only"] is True
    legacy_project = _tool_payload(
        client.planning_get_project(_new_operation_claim(fixture), project_id=PROJECT_ID),
        "planning_get_project",
    )
    assert legacy_project["project"]["project_id"] == PROJECT_ID
    legacy_roadmaps = _tool_payload(
        client.planning_list_roadmaps(_new_operation_claim(fixture), project_id=PROJECT_ID),
        "planning_list_roadmaps",
    )
    assert legacy_roadmaps["read_only"] is True
    legacy_roadmap = _tool_payload(
        client.planning_get_roadmap(
            _new_operation_claim(fixture), project_id=PROJECT_ID,
            roadmap_id="namespace-process-roadmap",
        ), "planning_get_roadmap",
    )
    assert legacy_roadmap["roadmap"]["roadmap_id"] == "namespace-process-roadmap"
    legacy_revisions = _tool_payload(
        client.planning_list_revisions(
            _new_operation_claim(fixture), project_id=PROJECT_ID,
            roadmap_id="namespace-process-roadmap",
        ), "planning_list_revisions",
    )
    assert legacy_revisions["read_only"] is True

    listed = _tool_payload(
        client.planning_mcp_list_roadmaps(_new_operation_claim(fixture)),
        "planning_mcp_list_roadmaps",
    )
    assert listed["read_only"] is True and listed["writes_supported"] is False
    source_id = listed["roadmaps"][0]["source_id"]
    read = _tool_payload(
        client.planning_mcp_read_roadmap(_new_operation_claim(fixture), source_id_or_path=source_id),
        "planning_mcp_read_roadmap",
    )
    assert read["read_only"] is True
    searched = _tool_payload(
        client.planning_mcp_search_roadmaps(_new_operation_claim(fixture), query=""),
        "planning_mcp_search_roadmaps",
    )
    assert searched["read_only"] is True
    context = _tool_payload(
        client.planning_mcp_get_context_pack(_new_operation_claim(fixture), roadmap_ref=source_id),
        "planning_mcp_get_context_pack",
    )
    assert context["read_only"] is True
    graph = _tool_payload(
        client.planning_mcp_graph_summary(_new_operation_claim(fixture), roadmap_ref=source_id),
        "planning_mcp_graph_summary",
    )
    assert graph["read_only"] is True and graph["writes_supported"] is False
    gates = _tool_payload(
        client.planning_mcp_read_gate_definitions(
            _new_operation_claim(fixture), project_id=PROJECT_ID,
            roadmap_id="namespace-process-roadmap",
        ), "planning_mcp_read_gate_definitions",
    )
    assert gates["read_only"] is True and gates["writes_performed"] is False
    handoff = _tool_payload(
        client.planning_mcp_create_agent_handoff(
            _new_operation_claim(fixture), project_id=PROJECT_ID,
            roadmap_id="namespace-process-roadmap",
        ), "planning_mcp_create_agent_handoff",
    )
    assert handoff["read_only"] is True


def test_migration_read_operations_are_child_bound_and_canonical(accepted_process_fixture):
    fixture = accepted_process_fixture
    _start_and_claim(fixture)
    client = fixture["client"]
    status = client.planning_migration_status(_new_operation_claim(fixture))
    assert type(status) is process_runtime.PlanningProcessMigrationSnapshot
    status = json.loads(status.payload.decode("ascii"))
    assert status["read_only"] is True and len(status["batches"]) == 1
    inventory = client.planning_migration_inventory(_new_operation_claim(fixture))
    assert type(inventory) is process_runtime.PlanningProcessMigrationSnapshot
    inventory = json.loads(inventory.payload.decode("ascii"))
    assert inventory["read_only"] is True
    roadmap_id = fixture["manifest"]["source_items"][0]["roadmap_id"]
    exported_json = client.planning_migration_export_json(_new_operation_claim(fixture), roadmap_id=roadmap_id)
    assert type(exported_json) is process_runtime.PlanningProcessMigrationExport
    assert exported_json.media_type == "application/json"
    exported_markdown = client.planning_migration_export_markdown(_new_operation_claim(fixture), roadmap_id=roadmap_id)
    assert type(exported_markdown) is process_runtime.PlanningProcessMigrationExport
    assert exported_markdown.media_type == "text/markdown"


def _forged_inventory_payload(fixture, *, forgery: str, principal_identity: dict[str, str]) -> dict[str, object]:
    manifest = fixture["manifest"]
    source_items = deepcopy(manifest["source_items"])
    normalized = deepcopy(manifest["normalized_payloads"])
    roadmap_id = source_items[0]["roadmap_id"]
    document = json.loads(normalized[roadmap_id])
    roadmap = document["roadmaps"][0]
    if forgery == "project_id":
        document["project"]["project_id"] = "foreign-project"
        roadmap["project_id"] = "foreign-project"
    elif forgery == "content_hash":
        roadmap["title"] = "Forged inventory content"
    else:
        raise AssertionError("unknown inventory forgery")
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap_id: {"revision": roadmap["revision"], "content_hash": roadmap["content_hash"]},
    }
    validate_planning_definition(document)
    normalized[roadmap_id] = json.dumps(
        document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    )
    source_items[0]["payload_sha256"] = "sha256:" + hashlib.sha256(
        normalized[roadmap_id].encode("utf-8"),
    ).hexdigest()
    identity = {
        key: deepcopy(value) for key, value in manifest.items()
        if key not in {"supporting_observation", "manifest_digest", "batch_id"}
    }
    identity["source_items"] = source_items
    identity["normalized_payloads"] = normalized
    digest = process_runtime._migration_digest(identity)
    return {
        "schema": "odysseus.planning.process.migration_inventory.v1", "read_only": True,
        "batch_id": "planning-migration-batch:" + digest[7:], "manifest_digest": digest,
        "structural_census": deepcopy(manifest["structural_census"]), "source_items": source_items,
        "supporting_relations": deepcopy(manifest["supporting_relations"]),
        "census_observation": deepcopy(manifest["supporting_observation"]),
        "manifest_identity": identity, "normalized_payloads": normalized,
        "principal_identity": principal_identity,
    }


@pytest.mark.parametrize("forgery", ["project_id", "content_hash"])
def test_child_inventory_rejects_project_and_content_hash_forgeries(
    accepted_process_fixture, forgery: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    handshake, claim = _start_and_claim(fixture)
    payload = _forged_inventory_payload(
        fixture, forgery=forgery,
        principal_identity={
            "issuer_id": handshake.issuer_id, "subject_id": claim.subject_id,
            "owner_id": claim.owner_id, "auth_context_digest": claim.auth_context_digest,
        },
    )
    peer = _swap_in_standalone_peer(
        client, fixture, _forged_migration_inventory_peer, (payload,),
    )
    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.planning_migration_inventory(_new_operation_claim(fixture), timeout_seconds=5)
        _assert_error(error, "invalid_response")
        peer.join(5)
        _assert_reaped(peer)
        assert client._process is None and client._connection is None
    finally:
        if peer.is_alive():
            peer.terminate()
            peer.join(5)
        client.close()


def test_migration_prepare_and_accept_bind_child_manifest(accepted_process_fixture, tmp_path: Path):
    fixture = accepted_process_fixture
    client = process_runtime.PlanningProcessClient(
        repo_root=fixture["repository_root"], definitions_root=fixture["definitions_root"],
        durable_root=tmp_path / "fresh-durable", legacy_owner=OWNER_ID,
    )
    try:
        client.start()
        manifest = fixture["manifest"]
        census_digest = process_runtime._canonical_digest(manifest["structural_census"])
        prepared = client.planning_prepare_migration(
                _new_operation_claim(fixture, client=client), expected_batch_id=manifest["batch_id"],
                expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
            )
        assert type(prepared) is process_runtime.PlanningProcessMigrationWriteResult
        assert prepared.batch.state == "prepared"
        accepted = client.planning_accept_migration(
                _new_operation_claim(fixture, client=client), batch_id=manifest["batch_id"],
                expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
                prior_receipt_id=prepared.receipt.receipt_id,
            )
        assert type(accepted) is process_runtime.PlanningProcessMigrationWriteResult
        assert accepted.batch.state == "accepted"
        replay = client.planning_accept_migration(
            _new_operation_claim(fixture, client=client), batch_id=manifest["batch_id"],
            expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
            prior_receipt_id=prepared.receipt.receipt_id,
        )
        assert replay.receipt.receipt_id == accepted.receipt.receipt_id
        with pytest.raises(process_runtime.PlanningProcessStoreError) as conflict:
            client.planning_accept_migration(
                _new_operation_claim(fixture, client=client), batch_id=manifest["batch_id"],
                expected_manifest_digest="sha256:" + "0" * 64, expected_structural_census_digest=census_digest,
                prior_receipt_id=prepared.receipt.receipt_id,
            )
        _assert_error(conflict, "stale_authority")
    finally:
        client.close()


def test_migration_abort_and_rollback_return_bound_terminal_receipts(accepted_process_fixture, tmp_path: Path):
    fixture = accepted_process_fixture
    manifest = fixture["manifest"]
    census_digest = process_runtime._canonical_digest(manifest["structural_census"])

    def fresh_client(name: str):
        return process_runtime.PlanningProcessClient(
            repo_root=fixture["repository_root"], definitions_root=fixture["definitions_root"],
            durable_root=tmp_path / name, legacy_owner=OWNER_ID,
        )

    aborted_client = fresh_client("aborted")
    try:
        aborted_client.start()
        prepared = aborted_client.planning_prepare_migration(
            _new_operation_claim(fixture, client=aborted_client), expected_batch_id=manifest["batch_id"],
            expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
        )
        aborted = aborted_client.planning_abort_migration(
            _new_operation_claim(fixture, client=aborted_client), batch_id=manifest["batch_id"],
            prior_receipt_id=prepared.receipt.receipt_id,
        )
        assert (aborted.receipt.phase, aborted.receipt.before_state, aborted.receipt.after_state) == ("abort", "prepared", "aborted")
        assert aborted.batch.state == "aborted" and aborted.batch.terminal_receipt_id == aborted.receipt.receipt_id
    finally:
        aborted_client.close()

    rolled_back_client = fresh_client("rolled-back")
    try:
        rolled_back_client.start()
        prepared = rolled_back_client.planning_prepare_migration(
            _new_operation_claim(fixture, client=rolled_back_client), expected_batch_id=manifest["batch_id"],
            expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
        )
        accepted = rolled_back_client.planning_accept_migration(
            _new_operation_claim(fixture, client=rolled_back_client), batch_id=manifest["batch_id"],
            expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
            prior_receipt_id=prepared.receipt.receipt_id,
        )
        rolled_back = rolled_back_client.planning_rollback_migration(
            _new_operation_claim(fixture, client=rolled_back_client), batch_id=manifest["batch_id"],
            prior_receipt_id=accepted.receipt.receipt_id,
        )
        assert (rolled_back.receipt.phase, rolled_back.receipt.before_state, rolled_back.receipt.after_state) == ("rollback", "accepted", "rolled_back")
        assert rolled_back.batch.state == "rolled_back" and rolled_back.batch.terminal_receipt_id == rolled_back.receipt.receipt_id
    finally:
        rolled_back_client.close()


def test_migration_dto_rejects_forged_subject_receipt_and_batch_bindings():
    head = process_runtime.PlanningProcessDurableHead(
        owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id="migration-dto-roadmap",
        current_revision_id="planning-revision:" + "a" * 64,
        current_content_sha256="sha256:" + "b" * 64,
        approved_revision_id="planning-revision:" + "a" * 64, generation=1,
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime.PlanningProcessMigrationSubject(
            subject_key=f"{OWNER_ID}:{PROJECT_ID}:migration-dto-roadmap", owner_id=OWNER_ID,
            project_id=PROJECT_ID, roadmap_id="migration-dto-roadmap", head=head, fence=0,
        )
    _assert_error(error, "invalid_response")

    subjects = tuple(
        process_runtime.PlanningProcessMigrationSubject(
            subject_key=f"{OWNER_ID}:{PROJECT_ID}:migration-dto-{index}", owner_id=OWNER_ID,
            project_id=PROJECT_ID, roadmap_id=f"migration-dto-{index}", head=None, fence=0,
        ) for index in range(6)
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime.PlanningProcessMigrationReceipt(
            receipt_type="planning_migration", receipt_id="planning-migration-receipt:" + "c" * 64,
            batch_id="planning-migration-batch:" + "d" * 64, phase="prepare",
            input_digest="sha256:" + "e" * 64, output_digest="sha256:" + "f" * 64,
            before_state="absent", after_state="prepared", before_subjects=subjects,
            after_subjects=subjects, new_revision_ids=(), producer="harbour_planning_store",
            occurred_at="2026-10-04T00:00:00Z",
        )
    _assert_error(error, "invalid_response")

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime.PlanningProcessMigrationBatchBinding(
            owner_id=OWNER_ID, project_id=PROJECT_ID, batch_id="planning-migration-batch:" + "d" * 64,
            manifest_digest="sha256:" + "e" * 64, structural_census_digest="sha256:" + "f" * 64,
            state="accepted", prepared_receipt_id="planning-migration-receipt:" + "c" * 64,
            accepted_receipt_id="", terminal_receipt_id="",
        )
    _assert_error(error, "invalid_response")


@pytest.mark.parametrize("field", ["path", "adapter", "roadmap_id", "blob", "sha256"])
def test_migration_source_policy_rejects_each_frozen_member_identity_forgery(
    accepted_process_fixture, field: str,
):
    items = deepcopy(accepted_process_fixture["manifest"]["source_items"])
    items[0][field] = "forged" if field != "sha256" else "0" * 64
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._migration_source_items(items, owner_id=OWNER_ID, project_id=PROJECT_ID)
    _assert_error(error, "invalid_response")


@pytest.mark.parametrize(("field", "value"), [
    ("path", "specs/roadmaps/forged.json"),
    ("target_roadmap_id", "forged-roadmap"),
    ("target_roadmap_id", process_runtime.FROZEN_MEMBERS[0][2]),
    ("sha256", "0" * 64),
])
def test_migration_supporting_relation_rejects_path_target_and_pin_forgeries(
    accepted_process_fixture, field: str, value: str,
):
    fixture = accepted_process_fixture
    batch = fixture["store"].get_migration_batch(
        fixture["principal"], owner_id=OWNER_ID, batch_id=fixture["manifest"]["batch_id"],
    )
    batch["supporting_relations"][0][field] = value
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._validate_migration_batch_payload(batch, owner_id=OWNER_ID, project_id=PROJECT_ID)
    _assert_error(error, "invalid_response")


def test_migration_read_principal_and_markdown_metadata_forgeries_fail_closed(accepted_process_fixture):
    fixture = accepted_process_fixture
    client = fixture["client"]
    handshake = client.start()
    claim = _new_operation_claim(fixture)
    valid_principal = {
        "issuer_id": handshake.issuer_id, "subject_id": claim.subject_id,
        "owner_id": claim.owner_id, "auth_context_digest": claim.auth_context_digest,
    }
    process_runtime._validate_snapshot_principal(valid_principal, claim=claim, issuer_id=handshake.issuer_id)
    forged_principal = {**valid_principal, "subject_id": "forged-subject"}
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._validate_snapshot_principal(forged_principal, claim=claim, issuer_id=handshake.issuer_id)
    _assert_error(error, "invalid_response")

    roadmap_id = "markdown-metadata-forgery"
    source = _durable_definition_payload(roadmap_id, 1, "Markdown metadata")
    document = json.loads(source.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    body = process_runtime.render_planning_definition_markdown(document)
    payload = {
        "schema": "odysseus.planning.process.migration_export.v1", "read_only": True,
        "roadmap_id": roadmap_id, "revision_id": "planning-revision:" + "a" * 64,
        "revision": roadmap["revision"], "content_sha256": roadmap["content_hash"],
        "payload_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
        "revision_state": roadmap["revision_state"], "format": "markdown",
        "media_type": "text/markdown", "body": body, "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "accepted_head": {
            "owner_id": OWNER_ID, "project_id": PROJECT_ID, "roadmap_id": roadmap_id,
            "current_revision_id": "planning-revision:" + "a" * 64,
            "current_content_sha256": roadmap["content_hash"],
            "approved_revision_id": "planning-revision:" + "a" * 64, "generation": 1,
        },
    }
    source_wire = {"payload_sha256": payload["payload_sha256"], "payload_b64": base64.b64encode(source).decode("ascii")}
    result = process_runtime._tool_result_from_wire(
        process_runtime._tool_result_wire("planning_migration_export_markdown", payload),
        expected_tool="planning_migration_export_markdown",
    )
    assert type(process_runtime._migration_read_projection(
        result, claim=claim, issuer_id=handshake.issuer_id, source_wire=source_wire,
    )) is process_runtime.PlanningProcessMigrationExport
    forged = {**payload, "revision_state": "draft"}
    forged_result = process_runtime._tool_result_from_wire(
        process_runtime._tool_result_wire("planning_migration_export_markdown", forged),
        expected_tool="planning_migration_export_markdown",
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._migration_read_projection(
            forged_result, claim=claim, issuer_id=handshake.issuer_id, source_wire=source_wire,
        )
    _assert_error(error, "invalid_response")
    forged_head = {**payload, "revision_id": "planning-revision:" + "b" * 64}
    forged_result = process_runtime._tool_result_from_wire(
        process_runtime._tool_result_wire("planning_migration_export_markdown", forged_head),
        expected_tool="planning_migration_export_markdown",
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._migration_read_projection(
            forged_result, claim=claim, issuer_id=handshake.issuer_id, source_wire=source_wire,
        )
    _assert_error(error, "invalid_response")

    for field, value in (
        ("owner_id", "foreign-owner"),
        ("project_id", "foreign-project"),
        ("roadmap_id", "foreign-roadmap"),
        ("current_revision_id", "planning-revision:" + "b" * 64),
        ("current_content_sha256", "sha256:" + "b" * 64),
        ("approved_revision_id", "planning-revision:" + "b" * 64),
        ("generation", 2),
    ):
        forged = deepcopy(payload)
        forged["accepted_head"][field] = value
        forged_result = process_runtime._tool_result_from_wire(
            process_runtime._tool_result_wire("planning_migration_export_markdown", forged),
            expected_tool="planning_migration_export_markdown",
        )
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._migration_read_projection(
                forged_result, claim=claim, issuer_id=handshake.issuer_id, source_wire=source_wire,
            )
        _assert_error(error, "invalid_response")


def test_migration_child_export_rejects_each_accepted_head_pointer_forgery(accepted_process_fixture):
    fixture = accepted_process_fixture
    roadmap_id = fixture["manifest"]["source_items"][0]["roadmap_id"]
    accepted = fixture["store"].find_accepted_migration_batch(
        fixture["principal"], owner_id=OWNER_ID, project_id=PROJECT_ID,
    )
    assert accepted is not None

    class BatchProjectionStore:
        def __init__(self, batch):
            self._batch = batch

        def find_accepted_migration_batch(self, *_args, **_kwargs):
            return deepcopy(self._batch)

        def read_revision(self, *args, **kwargs):
            return fixture["store"].read_revision(*args, **kwargs)

    valid = process_runtime._child_migration_operation(
        "planning_migration_export_json", {"roadmap_id": roadmap_id},
        principal=fixture["principal"], owner_id=OWNER_ID,
        durable_store=BatchProjectionStore(accepted), config={},
    )
    valid_result = process_runtime._tool_result_from_wire(
        valid["result"], expected_tool="planning_migration_export_json",
    )
    valid_payload = json.loads(valid_result.payload.decode("utf-8"))
    assert valid_payload["revision_id"] == valid_payload["accepted_head"]["current_revision_id"]

    index = next(index for index, item in enumerate(accepted["accepted_subjects"])
                 if item["roadmap_id"] == roadmap_id)
    for field, value in (
        ("owner_id", "foreign-owner"),
        ("project_id", "foreign-project"),
        ("roadmap_id", "foreign-roadmap"),
        ("current_revision_id", "planning-revision:" + "b" * 64),
        ("current_content_sha256", "sha256:" + "b" * 64),
        ("approved_revision_id", "planning-revision:" + "b" * 64),
        ("generation", 2),
    ):
        forged = deepcopy(accepted)
        forged["accepted_subjects"][index]["head"][field] = value
        with pytest.raises(DurablePlanningStoreError) as error:
            process_runtime._child_migration_operation(
                "planning_migration_export_json", {"roadmap_id": roadmap_id},
                principal=fixture["principal"], owner_id=OWNER_ID,
                durable_store=BatchProjectionStore(forged), config={},
            )
        assert error.value.code == "validation_failed"


def test_migration_result_parser_rejects_manifest_receipt_head_census_and_terminal_forgeries(
    accepted_process_fixture, tmp_path: Path,
):
    fixture = accepted_process_fixture
    manifest = fixture["manifest"]
    census_digest = process_runtime._canonical_digest(manifest["structural_census"])

    client = process_runtime.PlanningProcessClient(
        repo_root=fixture["repository_root"], definitions_root=fixture["definitions_root"],
        durable_root=tmp_path / "forgery-durable", legacy_owner=OWNER_ID,
    )
    try:
        client.start()
        prepare_claim = _new_operation_claim(fixture, client=client)
        prepare_body = {
            "expected_batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census_digest,
        }
        prepared = client.planning_prepare_migration(prepare_claim, **prepare_body)
        # A valid result proves the parser's positive baseline first.
        valid_payload = {
            "receipt": {
                "receipt_type": prepared.receipt.receipt_type, "receipt_id": prepared.receipt.receipt_id,
                "batch_id": prepared.receipt.batch_id, "phase": prepared.receipt.phase,
                "input_digest": prepared.receipt.input_digest, "output_digest": prepared.receipt.output_digest,
                "before_state": prepared.receipt.before_state, "after_state": prepared.receipt.after_state,
                "before_subjects": [process_runtime._migration_subject_wire(item) for item in prepared.receipt.before_subjects],
                "after_subjects": [process_runtime._migration_subject_wire(item) for item in prepared.receipt.after_subjects],
                "new_revision_ids": list(prepared.receipt.new_revision_ids), "producer": prepared.receipt.producer,
                "occurred_at": prepared.receipt.occurred_at,
            },
            "batch": None,
        }
        isolated_store = DurablePlanningRevisionStore(tmp_path / "forgery-durable", trusted_issuers=(fixture["issuer"],))
        raw_batch = isolated_store.get_migration_batch(
            fixture["principal"], owner_id=OWNER_ID, batch_id=manifest["batch_id"],
        )
        valid_payload["batch"] = raw_batch
        valid = process_runtime._tool_result_from_wire(
            process_runtime._tool_result_wire("planning_prepare_migration", valid_payload),
            expected_tool="planning_prepare_migration",
        )
        assert process_runtime._migration_write_result_from_tool(
            valid, operation="planning_prepare_migration", claim=prepare_claim, body=prepare_body,
        ).receipt == prepared.receipt

        mutations = []
        forged_manifest = deepcopy(valid_payload); forged_manifest["batch"]["manifest_identity"]["authority_pins"] = {}
        mutations.append(forged_manifest)
        forged_receipt = deepcopy(valid_payload); forged_receipt["receipt"]["input_digest"] = "sha256:" + "0" * 64
        mutations.append(forged_receipt)
        forged_census = deepcopy(valid_payload); forged_census["batch"]["structural_census"][0]["source_id"] = "repo-plan:" + "0" * 16
        mutations.append(forged_census)
        for forged in mutations:
            candidate = process_runtime._tool_result_from_wire(
                process_runtime._tool_result_wire("planning_prepare_migration", forged),
                expected_tool="planning_prepare_migration",
            )
            with pytest.raises(process_runtime.PlanningProcessError) as error:
                process_runtime._migration_write_result_from_tool(
                    candidate, operation="planning_prepare_migration", claim=prepare_claim, body=prepare_body,
                )
            _assert_error(error, "invalid_response")

        accepted_claim = _new_operation_claim(fixture, client=client)
        accept_body = {
            "batch_id": manifest["batch_id"], "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census_digest, "prior_receipt_id": prepared.receipt.receipt_id,
        }
        accepted = client.planning_accept_migration(accepted_claim, **accept_body)
        accepted_batch = isolated_store.get_migration_batch(
            fixture["principal"], owner_id=OWNER_ID, batch_id=manifest["batch_id"],
        )
        accepted_payload = {
            "receipt": {field: (
                [process_runtime._migration_subject_wire(item) for item in getattr(accepted.receipt, field)]
                if field in {"before_subjects", "after_subjects"} else list(getattr(accepted.receipt, field))
                if field == "new_revision_ids" else getattr(accepted.receipt, field)
            ) for field in (
                "receipt_type", "receipt_id", "batch_id", "phase", "input_digest", "output_digest",
                "before_state", "after_state", "before_subjects", "after_subjects", "new_revision_ids", "producer", "occurred_at",
            )},
            "batch": accepted_batch,
        }
        forged_head = deepcopy(accepted_payload)
        forged_head["batch"]["accepted_subjects"][0]["head"]["current_revision_id"] = "planning-revision:" + "0" * 64
        candidate = process_runtime._tool_result_from_wire(
            process_runtime._tool_result_wire("planning_accept_migration", forged_head),
            expected_tool="planning_accept_migration",
        )
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._migration_write_result_from_tool(
                candidate, operation="planning_accept_migration", claim=accepted_claim, body=accept_body,
            )
        _assert_error(error, "invalid_response")
    finally:
        client.close()


def test_migration_terminal_receipt_mutation_is_rejected(accepted_process_fixture, tmp_path: Path):
    fixture = accepted_process_fixture
    manifest = fixture["manifest"]
    census_digest = process_runtime._canonical_digest(manifest["structural_census"])
    client = process_runtime.PlanningProcessClient(
        repo_root=fixture["repository_root"], definitions_root=fixture["definitions_root"],
        durable_root=tmp_path / "terminal-forgery-durable", legacy_owner=OWNER_ID,
    )
    try:
        client.start()
        prepared = client.planning_prepare_migration(
            _new_operation_claim(fixture, client=client), expected_batch_id=manifest["batch_id"],
            expected_manifest_digest=manifest["manifest_digest"], expected_structural_census_digest=census_digest,
        )
        abort_claim = _new_operation_claim(fixture, client=client)
        abort_body = {"batch_id": manifest["batch_id"], "prior_receipt_id": prepared.receipt.receipt_id}
        aborted = client.planning_abort_migration(abort_claim, **abort_body)
        isolated_store = DurablePlanningRevisionStore(
            tmp_path / "terminal-forgery-durable", trusted_issuers=(fixture["issuer"],),
        )
        batch = isolated_store.get_migration_batch(
            fixture["principal"], owner_id=OWNER_ID, batch_id=manifest["batch_id"],
        )
        receipt = aborted.receipt
        payload = {
            "receipt": {
                "receipt_type": receipt.receipt_type, "receipt_id": receipt.receipt_id,
                "batch_id": receipt.batch_id, "phase": receipt.phase,
                "input_digest": receipt.input_digest, "output_digest": receipt.output_digest,
                "before_state": receipt.before_state, "after_state": receipt.after_state,
                "before_subjects": [process_runtime._migration_subject_wire(item) for item in receipt.before_subjects],
                "after_subjects": [process_runtime._migration_subject_wire(item) for item in receipt.after_subjects],
                "new_revision_ids": list(receipt.new_revision_ids), "producer": receipt.producer,
                "occurred_at": receipt.occurred_at,
            },
            "batch": batch,
        }
        payload["receipt"]["after_subjects"][0]["fence"] = 1
        candidate = process_runtime._tool_result_from_wire(
            process_runtime._tool_result_wire("planning_abort_migration", payload),
            expected_tool="planning_abort_migration",
        )
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._migration_write_result_from_tool(
                candidate, operation="planning_abort_migration", claim=abort_claim, body=abort_body,
            )
        _assert_error(error, "invalid_response")
    finally:
        client.close()


@pytest.mark.parametrize(("tool", "maximum"), [
    ("planning_migration_status", 12 * 1024 * 1024),
    ("planning_migration_export_markdown", 8 * 1024 * 1024 + process_runtime.MIGRATION_EXPORT_DTO_OVERHEAD_BYTES),
    ("planning_migration_export_json", 4 * 1024 * 1024 + process_runtime.MIGRATION_EXPORT_DTO_OVERHEAD_BYTES),
])
def test_migration_result_wire_accepts_exact_per_operation_size_and_rejects_plus_one(tool: str, maximum: int):
    raw = _canonical_json_frame_of_size(maximum)
    wire = process_runtime._tool_result_wire(tool, json.loads(raw.decode("ascii")))
    result = process_runtime._tool_result_from_wire(wire, expected_tool=tool)
    assert len(result.payload) == maximum
    over = _canonical_json_frame_of_size(maximum + 1)
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._tool_result_wire(tool, json.loads(over.decode("ascii")))
    _assert_error(error, "invalid_response")


def test_migration_exports_enforce_exact_body_limits_independent_of_dto_overhead():
    roadmap_id = "export-body-limit"
    source = _durable_definition_payload(
        roadmap_id, 1, "Export body limit", exact_size_bytes=process_runtime.MIGRATION_JSON_EXPORT_MAX_BYTES,
    )
    document = json.loads(source.decode("utf-8")); roadmap = document["roadmaps"][0]
    common = {
        "owner_id": OWNER_ID, "project_id": PROJECT_ID, "roadmap_id": roadmap_id,
        "revision_id": "planning-revision:" + "a" * 64, "revision": 1,
        "content_sha256": roadmap["content_hash"], "payload_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
    }
    json_export = process_runtime.PlanningProcessMigrationExport(
        **common, format="json", media_type="application/json", body=source,
    )
    assert len(json_export.body) == process_runtime.MIGRATION_JSON_EXPORT_MAX_BYTES
    markdown = b"# " + b"x" * (process_runtime.MIGRATION_MARKDOWN_EXPORT_MAX_BYTES - 2)
    markdown_export = process_runtime.PlanningProcessMigrationExport(
        **common, format="markdown", media_type="text/markdown", body=markdown,
    )
    assert len(markdown_export.body) == process_runtime.MIGRATION_MARKDOWN_EXPORT_MAX_BYTES
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime.PlanningProcessMigrationExport(
            **common, format="markdown", media_type="text/markdown", body=markdown + b"x",
        )
    _assert_error(error, "invalid_response")


def test_graph_summary_applies_depth_before_limit_projection():
    class LensService:
        def get_context_pack(self, roadmap_ref: str, *, max_items: int):
            assert roadmap_ref == "roadmap" and max_items == 10
            return {"roadmap_ref": roadmap_ref, "roadmap_lens": {
                "available": True, "active_node_id": "root", "clipped": False,
                "nodes": [{"node_id": "root"}, {"node_id": "near"}, {"node_id": "far"}],
                "edges": [
                    {"source_id": "root", "target_id": "near"},
                    {"source_id": "near", "target_id": "far"},
                ],
            }}

    depth_one = process_runtime._mcp_graph_summary(LensService(), "roadmap", depth=1, limit=10)
    assert [node["node_id"] for node in depth_one["nodes"]] == ["root", "near"]
    assert depth_one["edges"] == [{"source_id": "root", "target_id": "near"}]
    depth_zero = process_runtime._mcp_graph_summary(LensService(), "roadmap", depth=0, limit=10)
    assert [node["node_id"] for node in depth_zero["nodes"]] == ["root"] and depth_zero["edges"] == []


def test_graph_summary_returns_bounded_incomplete_projection_for_valid_unavailable_lens():
    class UnavailableLensService:
        def get_context_pack(self, roadmap_ref: str, *, max_items: int):
            assert roadmap_ref == "roadmap" and max_items == 10
            return {"roadmap_ref": roadmap_ref, "roadmap_lens": {
                "available": False, "nodes": [], "edges": [], "clipped": False, "incomplete": True,
            }}

    summary = process_runtime._mcp_graph_summary(UnavailableLensService(), "roadmap", depth=2, limit=10)
    assert summary == {
        "schema": "odysseus.planning.graph_summary.v1", "read_only": True, "writes_supported": False,
        "roadmap_ref": "roadmap", "depth": 2, "available": False, "incomplete": True,
        "node_count": 0, "edge_count": 0, "nodes": [], "edges": [], "clipped": False,
    }


def test_graph_summary_rejects_malformed_available_or_unavailable_lenses():
    class LensService:
        def __init__(self, lens): self.lens = lens
        def get_context_pack(self, roadmap_ref: str, *, max_items: int): return {"roadmap_ref": roadmap_ref, "roadmap_lens": self.lens}

    malformed = (
        {"available": True, "nodes": [], "edges": [], "clipped": False},
        {"available": False, "active_node_id": "forged", "nodes": [], "edges": [], "clipped": False, "incomplete": True},
        {"available": False, "active_node_id": None, "nodes": [], "edges": [], "clipped": False, "incomplete": True},
        {"available": False, "nodes": [{"node_id": "forged"}], "edges": [], "clipped": False, "incomplete": True},
        {"available": False, "nodes": [], "edges": [], "clipped": True, "incomplete": True},
        {"available": "false", "nodes": [], "edges": [], "clipped": False, "incomplete": True},
    )
    for lens in malformed:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._mcp_graph_summary(LensService(lens), "roadmap", depth=1, limit=10)
        _assert_error(error, "child_failed")


def _durable_definition_payload(
    roadmap_id: str, revision: int, title: str, *, state="approved", exact_size_bytes: int | None = None,
) -> bytes:
    from tests.test_planning_definition_projection import definition_fixture
    from src.planning_definition_contract import validate_planning_definition

    document = definition_fixture(PROJECT_ID, roadmap_id, include_draft=False)
    roadmap = document["roadmaps"][0]
    if exact_size_bytes is not None:
        roadmap["assumptions"] = []
    roadmap["revision"] = revision
    roadmap["revision_state"] = state
    roadmap["title"] = title
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = (
        {roadmap_id: {"revision": revision, "content_hash": roadmap["content_hash"]}}
        if state == "approved" else {}
    )
    document["project"]["draft_refs"] = []
    if exact_size_bytes is not None:
        base = json.dumps(
            document, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        delta = exact_size_bytes - len(base)
        count = (delta + 4003) // 4003
        total_chars = delta - 3 * count + 1
        if count < 1 or total_chars < count or total_chars > 4000 * count:
            raise AssertionError("target canonical payload size cannot be represented")
        assumptions = []
        remaining = total_chars
        for index in range(count):
            later = count - index - 1
            length = min(4000, remaining - later)
            prefix = f"assumption-{index:04d}-"
            if length < len(prefix):
                raise AssertionError("target payload left an invalid short assumption")
            assumptions.append(prefix + ("x" * (length - len(prefix))))
            remaining -= length
        roadmap["assumptions"] = assumptions
        if remaining != 0:
            raise AssertionError("target canonical payload filler was not exact")
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        document["project"]["latest_approved_revision"] = (
            {roadmap_id: {"revision": revision, "content_hash": roadmap["content_hash"]}}
            if state == "approved" else {}
        )
    validate_planning_definition(document)
    payload = json.dumps(
        document, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    if exact_size_bytes is not None and len(payload) != exact_size_bytes:
        raise AssertionError("canonical planning payload did not reach requested byte length")
    return payload


def _assert_durable_head_matches_store(actual, expected) -> None:
    assert type(actual) is process_runtime.PlanningProcessDurableHead
    assert tuple(field.name for field in fields(actual)) == (
        "owner_id", "project_id", "roadmap_id", "current_revision_id",
        "current_content_sha256", "approved_revision_id", "generation",
    )
    for field in fields(actual):
        _assert_equal(getattr(actual, field.name), getattr(expected, field.name), f"durable head {field.name}")


def _head_command_projection(head):
    return {
        "owner_id": head.owner_id,
        "project_id": head.project_id,
        "roadmap_id": head.roadmap_id,
        "current_revision_id": head.current_revision_id,
        "current_content_sha256": head.current_content_sha256,
        "approved_revision_id": head.approved_revision_id,
        "generation": head.generation,
    }


def _independent_canonical_digest(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _independent_revision_id(command_digest: str, revision: int) -> str:
    digest = hashlib.sha256((command_digest + ":" + str(revision)).encode("ascii")).hexdigest()
    return "planning-revision:" + digest


def _independent_receipt_id(command_digest: str) -> str:
    digest = hashlib.sha256((command_digest + ":receipt").encode("ascii")).hexdigest()
    return "planning-receipt:" + digest


def _assert_write_receipt_matches_command(
    result, *, command: dict[str, object], before_head,
    new_revision_id: str | None, restored_revision_id: str | None,
) -> None:
    receipt = result.receipt
    expected_input = _independent_canonical_digest(command)
    expected_subject = "planning-head:" + hashlib.sha256(
        json.dumps(
            (result.head.owner_id, result.head.project_id, result.head.roadmap_id),
            ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    expected_output = _independent_canonical_digest({
        "after_head": _head_command_projection(result.head),
        "new_revision_id": new_revision_id,
        "restored_revision_id": restored_revision_id,
    })
    assert receipt.input_digest == expected_input
    assert receipt.receipt_id == _independent_receipt_id(expected_input)
    assert receipt.subject_id == expected_subject
    assert receipt.output_digest == expected_output
    assert receipt.before_head == before_head
    assert receipt.new_revision_id == new_revision_id
    assert receipt.restored_revision_id == restored_revision_id
    assert receipt.after_head == result.head
    assert datetime.fromisoformat(receipt.occurred_at).tzinfo is not None


def _principal_identity(fixture) -> dict[str, str]:
    return {
        "issuer_id": ISSUER_ID,
        "subject_id": fixture["subject_id"],
        "owner_id": OWNER_ID,
        "auth_context_digest": AUTH_CONTEXT_DIGEST,
    }


def _assert_durable_write_matches_store(
    fixture, result, principal, roadmap_id: str, *, command: dict[str, object],
    before_head, new_revision_id: str | None, restored_revision_id: str | None,
) -> None:
    assert type(result) is process_runtime.PlanningProcessWriteResult
    assert type(result.receipt) is process_runtime.PlanningProcessReceipt
    expected_head = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    _assert_durable_head_matches_store(result.head, expected_head)
    assert tuple(field.name for field in fields(result.receipt)) == (
        "receipt_id", "command_kind", "subject_id", "input_digest", "output_digest",
        "before_head", "after_head", "new_revision_id", "restored_revision_id", "producer", "occurred_at",
    )
    assert result.receipt.after_head == result.head
    assert result.receipt.subject_id.startswith("planning-head:")
    assert result.receipt.producer == "harbour_planning_store"
    assert result.receipt.receipt_id.startswith("planning-receipt:")
    assert result.receipt.input_digest.startswith("sha256:")
    assert result.receipt.output_digest.startswith("sha256:")
    _assert_write_receipt_matches_command(
        result, command=command, before_head=before_head,
        new_revision_id=new_revision_id, restored_revision_id=restored_revision_id,
    )


def _append_full_history(store: DurablePlanningRevisionStore, issuer, principal: object) -> None:
    """Create real approved descendants under distinct issuer-minted principals."""

    owner, _batch, _records, heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id=PROJECT_ID
    )
    for head in heads:
        descendant_principal = issuer.issue(
            subject_id=principal.subject_id,
            owner_id=OWNER_ID,
            auth_context_digest=AUTH_CONTEXT_DIGEST,
            ttl_seconds=3600,
            nonce=f"planning-process-descendant-{head['roadmap_id']}",
        )
        payload = store.read_payload(
            descendant_principal, owner_id=owner, revision_id=head["current_revision_id"]
        )
        document = json.loads(payload.decode("utf-8"))
        roadmap = document["roadmaps"][0]
        roadmap["revision"] = head["current_revision"] + 1
        roadmap["title"] = f"Planning process test — {roadmap['roadmap_id']}"
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        document["project"]["latest_approved_revision"] = {
            roadmap["roadmap_id"]: {
                "revision": roadmap["revision"],
                "content_hash": roadmap["content_hash"],
            }
        }
        document["project"]["draft_refs"] = []
        next_payload = json.dumps(
            document, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        current = store.get_head(
            descendant_principal,
            owner_id=owner,
            project_id=PROJECT_ID,
            roadmap_id=roadmap["roadmap_id"],
        )
        appended = store.append_revision(
            descendant_principal,
            owner_id=owner,
            project_id=PROJECT_ID,
            roadmap_id=roadmap["roadmap_id"],
            payload_bytes=next_payload,
            expected_head=current,
            idempotency_key=f"planning-process-test-descendant-{roadmap['roadmap_id']}",
        )
        store.approve_revision(
            descendant_principal,
            owner_id=owner,
            project_id=PROJECT_ID,
            roadmap_id=roadmap["roadmap_id"],
            revision_id=appended.new_revision_id,
            expected_head=appended.after_head,
            idempotency_key=f"planning-process-test-approval-{roadmap['roadmap_id']}",
        )


def _assert_exact_bundle(expected, actual) -> None:
    assert type(actual) is process_runtime.PlanningProcessBundle
    _assert_equal(actual.owner_id, expected.owner_id, "bundle owner")
    _assert_equal(actual.project_id, expected.project_id, "bundle project")
    _assert_equal(actual.batch_bytes, expected.batch_bytes, "complete accepted batch")
    _assert_equal(actual.state_digest, expected.state_digest, "bundle state digest")
    assert len(actual.heads) == len(expected.heads) == 6
    assert len(actual.revisions) == len(expected.revisions) == 12

    expected_heads = {head.roadmap_id: head for head in expected.heads}
    actual_heads = {head.roadmap_id: head for head in actual.heads}
    _assert_equal(actual_heads.keys(), expected_heads.keys(), "head set")
    for roadmap_id, expected_head in expected_heads.items():
        actual_head = actual_heads[roadmap_id]
        for field in fields(expected_head):
            _assert_equal(
                getattr(actual_head, field.name), getattr(expected_head, field.name),
                f"head field {field.name}",
            )

    expected_revisions = {member.revision.revision_id: member for member in expected.revisions}
    actual_revisions = {member.revision_id: member for member in actual.revisions}
    _assert_equal(actual_revisions.keys(), expected_revisions.keys(), "revision set")
    for revision_id, expected_member in expected_revisions.items():
        actual_member = actual_revisions[revision_id]
        expected_revision = expected_member.revision
        for field in fields(expected_revision):
            if field.name == "principal_ref":
                _assert_equal(
                    actual_member.principal_digest, expected_revision.principal_ref.principal_digest,
                    "historical principal digest association",
                )
            else:
                _assert_equal(
                    getattr(actual_member, field.name), getattr(expected_revision, field.name),
                    f"revision field {field.name}",
                )
        _assert_equal(actual_member.payload, expected_member.payload, "historical payload")


def test_frozen_client_starts_explicitly_and_returns_complete_subject_bound_bundle(
    accepted_process_fixture,
):
    fixture = accepted_process_fixture
    client = fixture["client"]

    assert client._process is None
    assert client._connection is None
    assert client._generation is None
    assert client._claims == {}
    assert set(client._config) == {"repo_root", "definitions_root", "durable_root", "legacy_owner"}
    assert all(Path(client._config[key]).is_absolute() for key in ("repo_root", "definitions_root", "durable_root"))
    _assert_equal(client._config["legacy_owner"], OWNER_ID, "legacy owner binding")
    handshake, claim = _start_and_claim(fixture)
    process = client._process
    assert type(handshake) is process_runtime.PlanningProcessHandshake
    _assert_equal(handshake.issuer_id, ISSUER_ID, "handshake issuer")
    _assert_equal(handshake.key_id, KEY_ID, "handshake key identifier")
    assert handshake.pid > 0
    _assert_equal(handshake.generation, client._generation, "handshake generation")
    if len(handshake.generation) != 32 or handshake.generation != handshake.generation.lower():
        pytest.fail("handshake generation had a noncanonical shape")
    spawn_process_type = multiprocessing.get_context("spawn").Process
    assert type(process) is spawn_process_type and process.is_alive()
    assert client._connection is not None
    assert type(client._connection).__name__ == "PipeConnection"

    bundle = client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)
    history_digests = {
        member.revision.principal_ref.principal_digest
        for member in fixture["expected_bundle"].revisions
    }
    assert len(history_digests) >= 7
    for expected_member in fixture["expected_bundle"].revisions:
        actual_member = next(
            member for member in bundle.revisions
            if member.revision_id == expected_member.revision.revision_id
        )
        _assert_equal(
            actual_member.principal_digest, expected_member.revision.principal_ref.principal_digest,
            "historical principal digest association",
        )

    repr_values = (client, handshake, claim, bundle, *bundle.heads, *bundle.revisions)
    for value in repr_values:
        rendered = repr(value)
        assert rendered.endswith("(<content-free>)")
        for private_value in (ISSUER_SECRET.decode(), KEY_ID, OWNER_ID, fixture["subject_id"], AUTH_CONTEXT_DIGEST):
            _assert_private_values_absent(rendered, (private_value,))
        for member in bundle.revisions:
            _assert_private_values_absent(rendered, (member.payload.decode("utf-8"),))
    assert "accepted" == json.loads(bundle.batch_bytes)["state"]
    _assert_exact_bundle(
        fixture["expected_bundle"],
        client.read_subject_bound_accepted_migration_bundle(_new_claim(fixture), timeout_seconds=10),
    )


def test_claim_creator_is_private_single_use_and_owner_subject_project_bound(
    accepted_process_fixture,
):
    fixture = accepted_process_fixture
    client = fixture["client"]

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        _new_claim(fixture)
    _assert_error(error, "not_started")
    assert client._process is None and client._connection is None and client._claims == {}

    assert not hasattr(process_runtime, "PlanningProcessClaim")
    assert not hasattr(client, "register_claim")
    handshake, claim = _start_and_claim(fixture)
    _assert_equal(claim.owner_id, OWNER_ID, "claim owner")
    _assert_equal(claim.subject_id, fixture["subject_id"], "claim subject")
    _assert_equal(claim.project_id, PROJECT_ID, "claim project")
    _assert_equal(claim.auth_context_digest, AUTH_CONTEXT_DIGEST, "claim auth-context binding")
    if len(claim.claim_id) != 32 or claim.claim_id != claim.claim_id.lower():
        pytest.fail("claim identifier had a noncanonical shape")

    _assert_equal(claim.generation, handshake.generation, "claim generation")
    client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
    _assert_error(error, "single_use_claim")

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._create_subject_bound_accepted_migration_claim(
            client,
            owner_id=OWNER_ID,
            subject_id=fixture["subject_id"],
            project_id="other-project",
            auth_context_digest=AUTH_CONTEXT_DIGEST,
        )
    _assert_error(error, "invalid_claim")

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._create_subject_bound_accepted_migration_claim(
            client,
            owner_id=OWNER_ID,
            subject_id=fixture["subject_id"],
            project_id=PROJECT_ID,
            auth_context_digest="not-a-sha256-digest",
        )
    _assert_error(error, "invalid_claim")


def test_private_claim_creator_reaps_dead_child_and_restarts(accepted_process_fixture):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    process = client._process
    assert process is not None and process.is_alive()
    process.terminate()
    process.join(5)

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        _new_claim(fixture)
    _assert_error(error, "child_eof")
    assert client._generation is None
    assert client._process is None and client._connection is None
    _assert_reaped(process)

    _handshake, fresh_claim = _start_and_claim(fixture)
    bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


def test_foreign_and_stale_claims_are_rejected_without_resend(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    sent, requests = _observe_requests(monkeypatch, client)

    foreign = replace(claim, owner_id="other-owner")
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.read_subject_bound_accepted_migration_bundle(foreign, timeout_seconds=10)
    _assert_error(error, "unregistered_claim")
    other_client = process_runtime.PlanningProcessClient(
        repo_root=fixture["repository_root"], definitions_root=fixture["definitions_root"],
        durable_root=fixture["durable_root"], legacy_owner=OWNER_ID,
    )
    try:
        other_client.start()
        foreign_client_claim = _new_claim(fixture, client=other_client)
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(foreign_client_claim, timeout_seconds=10)
        _assert_error(error, "unregistered_claim")
    finally:
        other_client.close()
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.read_subject_bound_accepted_migration_bundle({"claim": claim.claim_id}, timeout_seconds=10)
    _assert_error(error, "invalid_claim")
    forged = process_runtime._PlanningProcessReadClaim(
        owner_id=OWNER_ID, subject_id=fixture["subject_id"], project_id=PROJECT_ID,
        auth_context_digest=AUTH_CONTEXT_DIGEST, generation=claim.generation,
        claim_id=claim.claim_id, _client_identity=object(),
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.read_subject_bound_accepted_migration_bundle(forged, timeout_seconds=10)
    _assert_error(error, "unregistered_claim")
    class ClaimSubclass(process_runtime._PlanningProcessReadClaim):
        pass

    subclass = ClaimSubclass(
        owner_id=OWNER_ID, subject_id=fixture["subject_id"], project_id=PROJECT_ID,
        auth_context_digest=AUTH_CONTEXT_DIGEST, generation=claim.generation,
        claim_id=claim.claim_id, _client_identity=client._claim_identity,
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.read_subject_bound_accepted_migration_bundle(subclass, timeout_seconds=10)
    _assert_error(error, "invalid_claim")
    original_owner = claim.owner_id
    object.__setattr__(claim, "owner_id", "other-owner")
    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
        _assert_error(error, "foreign_claim")
    finally:
        object.__setattr__(claim, "owner_id", original_owner)
    if requests or sent.is_set():
        pytest.fail("invalid claim caused request IPC")

    unused_prior_generation_claim = _new_claim(fixture)
    client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
    assert sent.wait(0)
    assert len(requests) == 1
    _assert_exact_request(requests[0], claim)
    client.close()
    assert client._process is None
    assert client._connection is None

    _handshake, replacement_claim = _start_and_claim(fixture)
    fresh_sent, fresh_requests = _observe_requests(monkeypatch, client)
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.read_subject_bound_accepted_migration_bundle(unused_prior_generation_claim, timeout_seconds=10)
    assert error.value.code in {"unregistered_claim", "stale_claim", "single_use_claim"}
    if fresh_requests or fresh_sent.is_set():
        pytest.fail("unused prior-generation claim caused request IPC")
    bundle = client.read_subject_bound_accepted_migration_bundle(replacement_claim, timeout_seconds=10)
    assert fresh_sent.wait(0) and len(fresh_requests) == 1
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


def test_codec_is_canonical_and_rejects_malformed_duplicate_oversize_depth_and_nodes():
    value = {"z": [True, None, 3], "a": "safe"}
    encoded = process_runtime._encode_frame(value, maximum=16 * 1024)
    assert encoded.startswith(b"{") and b"\\x00" not in encoded
    assert encoded == b'{"a":"safe","z":[true,null,3]}'
    assert process_runtime._decode_frame(encoded, maximum=16 * 1024) == value
    assert process_runtime._encode_frame({"b": 1, "a": 2}, maximum=1024) == process_runtime._encode_frame(
        {"a": 2, "b": 1}, maximum=1024
    )

    malformed = (
        b"", b"bad", b'\xff', encoded + b"trailing", b'{ "a":"safe","z":[true,null,3]}',
        b'{"a":1,"a":2}', b'{"a":NaN}', b'{"b":1,"a":2}',
    )
    for raw in malformed:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._decode_frame(raw, maximum=16 * 1024)
        _assert_error(error, "protocol_error")
    for malformed_id in (None, "", "A" * 32, "0" * 31, "0" * 32 + "x"):
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._wire_id(malformed_id)
        _assert_error(error, "protocol_error")

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._decode_frame(encoded, maximum=len(encoded) - 1)
    _assert_error(error, "protocol_error")
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._decode_frame(bytearray(encoded), maximum=16 * 1024)
    _assert_error(error, "protocol_error")

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._encode_frame({"x": "x" * 1024}, maximum=32)
    _assert_error(error, "protocol_error")

    nested: object = 0
    for _ in range(34):
        nested = [nested]
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._encode_frame(nested, maximum=16 * 1024)
    _assert_error(error, "protocol_error")
    nested_json = json.dumps(nested, separators=(",", ":")).encode("ascii")
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._decode_frame(nested_json, maximum=16 * 1024)
    _assert_error(error, "protocol_error")

    too_many_nodes = [0] * 200_000
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._encode_frame(too_many_nodes, maximum=2 * 1024 * 1024)
    _assert_error(error, "protocol_error")
    too_many_nodes_json = json.dumps(too_many_nodes, separators=(",", ":")).encode("ascii")
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._decode_frame(too_many_nodes_json, maximum=2 * 1024 * 1024)
    _assert_error(error, "protocol_error")


def _assert_invalid_bundle_wire(value) -> None:
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._bundle_from_wire(value)
    _assert_error(error, "invalid_response")


def test_bundle_wire_requires_exact_complete_snapshot_and_rejects_corruption(
    accepted_process_fixture,
):
    expected = accepted_process_fixture["expected_bundle"]
    valid_wire = process_runtime._bundle_wire(expected)
    dto = process_runtime._bundle_from_wire(deepcopy(valid_wire))
    _assert_exact_bundle(expected, dto)

    nested_mutations = ("roadmap_id", "revision", "state", "project_id", "content_hash", "project_roadmap_refs")
    for mutation in nested_mutations:
        value = deepcopy(valid_wire)
        revision_wire = value["revisions"][0]
        payload = json.loads(base64.b64decode(revision_wire["payload_b64"]).decode("utf-8"))
        roadmap = payload["roadmaps"][0]
        roadmap_id = roadmap["roadmap_id"]
        approved_ref = payload["project"]["latest_approved_revision"].get(roadmap_id)
        if mutation == "roadmap_id":
            changed_id = "roadmap-process-forged"
            roadmap["roadmap_id"] = changed_id
            payload["project"]["roadmap_refs"] = [
                changed_id if candidate == roadmap_id else candidate
                for candidate in payload["project"]["roadmap_refs"]
            ]
            if approved_ref is not None:
                payload["project"]["latest_approved_revision"].pop(roadmap_id)
                payload["project"]["latest_approved_revision"][changed_id] = approved_ref
        elif mutation == "revision":
            roadmap["revision"] += 1
            if approved_ref is not None:
                approved_ref["revision"] = roadmap["revision"]
        elif mutation == "state":
            roadmap["revision_state"] = "draft"
            payload["project"]["latest_approved_revision"].pop(roadmap_id, None)
        elif mutation == "project_id":
            payload["project"]["project_id"] = "other-process-project"
            roadmap["project_id"] = "other-process-project"
        elif mutation == "content_hash":
            roadmap["title"] += " forged"
        else:
            payload["project"]["roadmap_refs"] = []
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        if approved_ref is not None:
            approved_ref["content_hash"] = roadmap["content_hash"]
        payload_bytes = json.dumps(
            payload, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        revision_wire["payload_b64"] = base64.b64encode(payload_bytes).decode("ascii")
        revision_wire["payload_sha256"] = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
        _assert_invalid_bundle_wire(value)

    mutations = []
    value = deepcopy(valid_wire)
    del value["heads"][0]["generation"]
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"][0]["extra"] = True
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"][0]["principal_digest"] = "sha256:bad"
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["state_digest"] = "sha256:bad"
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["owner_id"] = "other-owner"
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["heads"].reverse()
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["heads"][0]["current_revision_id"] = "missing-revision"
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"].reverse()
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"].pop(1)
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"][0]["payload_b64"] = base64.b64encode(b"tampered").decode("ascii")
    mutations.append(value)
    value = deepcopy(valid_wire)
    batch = json.loads(base64.b64decode(value["batch_b64"]).decode("utf-8"))
    batch["state"] = "prepared"
    value["batch_b64"] = base64.b64encode(
        json.dumps(batch, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["heads"].append(deepcopy(value["heads"][0]))
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"].append(deepcopy(value["revisions"][0]))
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["revisions"][0]["payload_b64"] = "A" * (((process_runtime.PAYLOAD_MAX_BYTES + 2) // 3) * 4 + 8)
    mutations.append(value)
    value = deepcopy(valid_wire)
    value["batch_b64"] = "A" * (((process_runtime.AGGREGATE_PAYLOAD_MAX_BYTES + 2) // 3) * 4 + 8)
    mutations.append(value)

    for malformed in mutations:
        _assert_invalid_bundle_wire(malformed)


def _pad_canonical_revision_payload(payload_bytes: bytes, target_bytes: int, unique_marker: str):
    document = json.loads(payload_bytes.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    assumptions = roadmap["assumptions"]
    assert type(assumptions) is list
    padding_indices = []
    serial = 0

    def encode_current() -> bytes:
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        latest = document["project"]["latest_approved_revision"].get(roadmap["roadmap_id"])
        if latest is not None:
            latest["content_hash"] = roadmap["content_hash"]
        return json.dumps(
            document, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    encoded = encode_current()
    remaining = target_bytes - len(encoded)
    while True:
        if remaining == 0:
            encoded = encode_current()
            assert len(encoded) == target_bytes
            return encoded, roadmap["content_hash"]
        assert remaining > 0, "canonical revision payload exceeded its exact target"
        prefix = f"aggregate-padding-{unique_marker}-{serial:04d}:"
        separator_bytes = 3 if assumptions else 2
        final_text_size = remaining - separator_bytes
        if len(prefix) <= final_text_size <= 4_000:
            padding_indices.append(len(assumptions))
            assumptions.append(prefix + "x" * (final_text_size - len(prefix)))
            encoded = encode_current()
            assert len(encoded) == target_bytes
            return encoded, roadmap["content_hash"]
        if padding_indices and remaining < len(prefix) + separator_bytes:
            last_index = padding_indices[-1]
            assert len(assumptions[last_index]) + remaining <= 4_000
            assumptions[last_index] += "x" * remaining
            encoded = encode_current()
            assert len(encoded) == target_bytes
            return encoded, roadmap["content_hash"]
        chunk_size = 3_900
        chunk = prefix + "x" * (chunk_size - len(prefix))
        assert len(chunk) <= 4_000
        padding_indices.append(len(assumptions))
        assumptions.append(chunk)
        remaining -= len(json.dumps(chunk, ensure_ascii=False).encode("utf-8")) + (1 if len(assumptions) > 1 else 0)
        serial += 1


def _aggregate_bundle_with_exact_payloads(expected, target_count: int):
    base_revisions = []
    for member in expected.revisions:
        stored = member.revision
        base_revisions.append(process_runtime.PlanningProcessRevision(
            revision_id=stored.revision_id,
            owner_id=stored.owner_id,
            project_id=stored.project_id,
            roadmap_id=stored.roadmap_id,
            revision=stored.revision,
            parent_revision_id=stored.parent_revision_id,
            payload_schema_id=stored.payload_schema_id,
            payload_sha256=stored.payload_sha256,
            content_sha256=stored.content_sha256,
            immutable_locator=stored.immutable_locator,
            revision_state=stored.revision_state,
            created_at=stored.created_at,
            payload=member.payload,
            principal_digest=stored.principal_ref.principal_digest,
        ))
    padded_revisions = []
    for revision in base_revisions:
        payload, content_digest = _pad_canonical_revision_payload(
            revision.payload, process_runtime.PAYLOAD_MAX_BYTES + 64 * 1024,
            f"{revision.roadmap_id}-{revision.revision_id}",
        )
        padded_revisions.append(replace(
            revision,
            payload=payload,
            payload_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
            content_sha256=content_digest,
        ))
    revisions_by_id = {revision.revision_id: revision for revision in padded_revisions}
    heads = []
    for stored_head in expected.heads:
        head = process_runtime.PlanningProcessHead(**{
            field.name: getattr(stored_head, field.name)
            for field in fields(process_runtime.PlanningProcessHead)
        })
        accepted = revisions_by_id[head.accepted_revision_id]
        current = revisions_by_id[head.current_revision_id]
        approved = revisions_by_id[head.approved_revision_id]
        heads.append(replace(
            head,
            accepted_content_sha256=accepted.content_sha256,
            current_content_sha256=current.content_sha256,
            approved_content_sha256=approved.content_sha256,
        ))
    batch = json.loads(expected.batch_bytes.decode("utf-8"))
    accepted_hashes = {head.roadmap_id: head.accepted_content_sha256 for head in heads}
    for source_item in batch["source_items"]:
        source_item["content_hash"] = accepted_hashes[source_item["roadmap_id"]]
    batch_bytes = json.dumps(
        batch, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    bundle = process_runtime.PlanningProcessBundle(
        owner_id=expected.owner_id,
        project_id=expected.project_id,
        batch_bytes=batch_bytes,
        heads=tuple(heads),
        revisions=tuple(sorted(
            padded_revisions,
            key=lambda item: (item.project_id, item.roadmap_id, item.revision, item.revision_id),
        )),
        state_digest=expected.state_digest,
    )
    assert len(bundle.heads) == 6
    assert len(bundle.revisions) == target_count
    assert all(
        len(revision.payload) > process_runtime.PAYLOAD_MAX_BYTES
        for revision in bundle.revisions
    )
    return bundle


def _accepted_bundle_from_process_bundle(expected, bundle):
    stored_heads_by_roadmap = {
        head.roadmap_id: head for head in expected.heads
    }
    stored_revisions_by_id = {
        member.revision.revision_id: member for member in expected.revisions
    }
    heads = []
    for head in bundle.heads:
        prototype = stored_heads_by_roadmap[head.roadmap_id]
        heads.append(replace(prototype, **{
            field.name: getattr(head, field.name)
            for field in fields(type(prototype))
        }))
    revisions = []
    for revision in bundle.revisions:
        prototype = stored_revisions_by_id.get(revision.revision_id)
        if prototype is None:
            prototype = stored_revisions_by_id[revision.parent_revision_id]
        assert revision.principal_digest == prototype.revision.principal_ref.principal_digest
        stored_revision = replace(prototype.revision, **{
            field.name: getattr(revision, field.name)
            for field in fields(type(prototype.revision))
            if field.name != "principal_ref"
        })
        revisions.append(replace(
            prototype, revision=stored_revision, payload=revision.payload,
        ))
    return replace(
        expected,
        batch_bytes=bundle.batch_bytes,
        heads=tuple(heads),
        revisions=tuple(revisions),
        state_digest=bundle.state_digest,
    )


def test_bundle_validator_accepts_members_and_aggregate_over_old_limits(accepted_process_fixture):
    expected = accepted_process_fixture["expected_bundle"]
    valid_twelve = _aggregate_bundle_with_exact_payloads(expected, 12)
    assert sum(len(revision.payload) for revision in valid_twelve.revisions) > process_runtime.AGGREGATE_PAYLOAD_MAX_BYTES
    process_runtime._validate_bundle(valid_twelve)

    by_roadmap = {}
    for revision in valid_twelve.revisions:
        by_roadmap.setdefault(revision.roadmap_id, []).append(revision)
    target_revisions = max(by_roadmap.values(), key=len)
    predecessor = max(target_revisions, key=lambda revision: revision.revision)
    head_index = next(index for index, head in enumerate(valid_twelve.heads) if head.roadmap_id == predecessor.roadmap_id)
    head = valid_twelve.heads[head_index]
    document = json.loads(predecessor.payload.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = predecessor.revision + 1
    document["project"]["latest_approved_revision"][roadmap["roadmap_id"]]["revision"] = roadmap["revision"]
    overflow_id = f"overflow-revision-{roadmap['revision']:04d}"
    for index, assumption in enumerate(roadmap["assumptions"]):
        if assumption.startswith("aggregate-padding-"):
            marker = f"overflow-{overflow_id[-8:]}-{index:04d}:"
            assert len(marker) <= len(assumption)
            roadmap["assumptions"][index] = marker + "x" * (len(assumption) - len(marker))
    overflow_payload, overflow_content_digest = _pad_canonical_revision_payload(
        json.dumps(
            document, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"),
        process_runtime.PAYLOAD_MAX_BYTES + 64 * 1024,
        f"{predecessor.roadmap_id}-overflow-{roadmap['revision']}",
    )
    overflow_revision = replace(
        predecessor,
        revision_id=overflow_id,
        revision=roadmap["revision"],
        parent_revision_id=predecessor.revision_id,
        payload=overflow_payload,
        payload_sha256="sha256:" + hashlib.sha256(overflow_payload).hexdigest(),
        content_sha256=overflow_content_digest,
    )
    overflow_revisions = tuple(sorted(
        (*valid_twelve.revisions, overflow_revision),
        key=lambda item: (item.project_id, item.roadmap_id, item.revision, item.revision_id),
    ))
    overflow_head = replace(
        head,
        current_revision_id=overflow_id,
        current_revision=overflow_revision.revision,
        current_content_sha256=overflow_revision.content_sha256,
        approved_revision_id=overflow_id,
        approved_revision=overflow_revision.revision,
        approved_content_sha256=overflow_revision.content_sha256,
    )
    overflow_heads = list(valid_twelve.heads)
    overflow_heads[head_index] = overflow_head
    assert len(overflow_revisions) == 13
    assert sum(len(revision.payload) for revision in overflow_revisions) > process_runtime.AGGREGATE_PAYLOAD_MAX_BYTES
    expanded = replace(
        valid_twelve, heads=tuple(overflow_heads), revisions=overflow_revisions,
    )
    process_runtime._validate_bundle(expanded)
    accepted_expanded = _accepted_bundle_from_process_bundle(expected, expanded)
    assert type(accepted_expanded) is type(expected)
    wire = process_runtime._bundle_wire(accepted_expanded)
    assert process_runtime._bundle_from_wire(deepcopy(wire)) == expanded
    operation = "read_subject_bound_accepted_migration_bundle"
    request_id = "a" * 32; generation = "b" * 32; claim_id = "c" * 32
    response = {
        "protocol": process_runtime.PROTOCOL, "op": operation + "_result",
        "request_id": request_id, "generation": generation, "claim_id": claim_id,
        "status": "ok", "bundle": wire,
    }

    class Frames:
        def __init__(self): self.values = []
        def send_bytes(self, value): self.values.append(value)
        def recv_bytes(self, maxlength):
            value = self.values.pop(0)
            assert len(value) <= maxlength
            return value

    frames = Frames()
    process_runtime._send_response(frames, response, operation=operation)
    assert len(frames.values) > 2
    received = process_runtime._receive_response(
        frames, operation=operation, request_id=request_id,
        generation=generation, claim_id=claim_id,
    )
    assert process_runtime._bundle_from_wire(received["bundle"]) == expanded


def test_deadline_invalidates_generation_and_reaps_real_child_then_fresh_claim_restarts(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    process = client._process
    assert process is not None
    holder, release = _spawn_lock_holder(fixture["durable_root"])
    sent, requests = _observe_requests(monkeypatch, client)
    helpers = _track_runtime_helpers(monkeypatch)
    started = time.monotonic()
    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=0.25)
        _assert_error(error, "deadline_exceeded")
        assert sent.wait(0) and len(requests) == 1
        _assert_exact_request(requests[0], claim)
        assert time.monotonic() - started < 4
        assert client._generation is None
        assert client._process is None
        assert client._connection is None
        _assert_reaped(process)
        _assert_helpers_finished(helpers)

        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
        _assert_error(error, "single_use_claim")
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(10)

    _handshake, new_claim = _start_and_claim(fixture)
    bundle = client.read_subject_bound_accepted_migration_bundle(new_claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


@pytest.mark.parametrize("death_stage", ["before_admission", "after_valid_response"])
def test_real_child_death_at_request_fences_reaps_and_restarts(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch, death_stage: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    process = client._process
    assert process is not None and process.is_alive()
    sent, requests = _observe_requests(monkeypatch, client)
    helpers = _track_runtime_helpers(monkeypatch)
    killed = Event()

    def kill_and_join() -> None:
        if process.is_alive():
            process.terminate()
            process.join(5)
        _assert_reaped(process)
        killed.set()

    if death_stage == "before_admission":
        original_encode = process_runtime._encode_frame

        def kill_before_admission(value, *, maximum):
            if type(value) is dict and value.get("op") == "read_subject_bound_accepted_migration_bundle":
                kill_and_join()
            return original_encode(value, maximum=maximum)

        monkeypatch.setattr(process_runtime, "_encode_frame", kill_before_admission)
    else:
        original_bundle = process_runtime._bundle_from_wire

        def kill_after_response(value):
            bundle = original_bundle(value)
            kill_and_join()
            return bundle

        monkeypatch.setattr(process_runtime, "_bundle_from_wire", kill_after_response)

    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=5)
        _assert_error(error, "child_eof")
        assert killed.is_set()
        assert client._generation is None and client._process is None and client._connection is None
        _assert_reaped(process)
        if death_stage == "before_admission":
            assert not sent.is_set() and requests == []
            assert helpers == []
        else:
            assert sent.wait(0) and len(requests) == 1
            _assert_exact_request(requests[0], claim)
            _assert_helpers_finished(helpers)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)

    _handshake, fresh_claim = _start_and_claim(fixture)
    bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


def test_concurrent_cancel_and_close_share_reap_and_restart(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    process = client._process
    assert process is not None
    holder, release = _spawn_lock_holder(fixture["durable_root"])
    sent, requests = _observe_requests(monkeypatch, client)
    helpers = _track_runtime_helpers(monkeypatch)
    result = {}
    spawn_process_type = multiprocessing.get_context("spawn").Process
    original_join = spawn_process_type.join
    join_entered = Event()
    join_release = Event()

    def held_join(candidate, *args, **kwargs):
        if candidate is process and not join_entered.is_set():
            join_entered.set()
            if not join_release.wait(8):
                raise AssertionError("timed out holding owned child reap")
        return original_join(candidate, *args, **kwargs)

    monkeypatch.setattr(spawn_process_type, "join", held_join)
    try:
        def request() -> None:
            try:
                client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=10)
            except process_runtime.PlanningProcessError as error:
                result["error"] = error

        worker = Thread(target=request)
        worker.start()
        assert sent.wait(5)
        assert len(requests) == 1
        _assert_exact_request(requests[0], claim)
        entered = {"cancel": Event(), "close": Event()}

        def cancel() -> None:
            entered["cancel"].set()
            result["cancel"] = client.cancel_inflight()

        def close() -> None:
            entered["close"].set()
            client.close()

        cancel_worker = Thread(target=cancel)
        close_worker = Thread(target=close)
        cancel_worker.start()
        close_worker.start()
        assert entered["cancel"].wait(2) and entered["close"].wait(2)
        assert join_entered.wait(4)
        token = client._reaping_token
        assert token is not None and client._reaping is True
        assert client._token is None
        assert client._process is None and client._connection is None
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.start()
        _assert_error(error, "unavailable")
        assert client._reaping_token is token
        assert client._process is None
        join_release.set()
        cancel_worker.join(8)
        close_worker.join(8)
        worker.join(4)
        assert not cancel_worker.is_alive() and not close_worker.is_alive()
        assert not worker.is_alive()
        assert result["error"].code == "cancelled"
        assert result["cancel"] is True
        assert client._process is None
        assert client._connection is None
        _assert_reaped(process)
        assert client._reaping is False and client._reaping_token is None
        _assert_helpers_finished(helpers)
    finally:
        join_release.set()
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(10)

    _handshake, fresh_claim = _start_and_claim(fixture)
    bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


def test_queued_old_generation_request_performs_no_io_after_restart(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake = client.start()
    old_process = client._process
    assert old_process is not None
    sent, requests = _observe_requests(monkeypatch, client)
    helpers = _track_runtime_helpers(monkeypatch)
    result = {}
    acquired = False
    try:
        acquired = client._request_lock.acquire(timeout=1)
        assert acquired is True
        acquired = True
        queued_claim = _new_claim(fixture)
        queued_started = Event()

        def queued_request() -> None:
            queued_started.set()
            try:
                client.read_subject_bound_accepted_migration_bundle(queued_claim, timeout_seconds=10)
            except process_runtime.PlanningProcessError as error:
                result["queued_error"] = error

        queued_worker = Thread(target=queued_request)
        queued_worker.start()
        assert queued_started.wait(2)
        deadline = time.monotonic() + 2
        while queued_claim.claim_id not in client._spent_claims and time.monotonic() < deadline:
            Event().wait(0.001)
        assert queued_claim.claim_id in client._spent_claims
        assert client.cancel_inflight() is True
        _assert_reaped(old_process)
        assert client._process is None and client._connection is None

        _handshake, fresh_claim = _start_and_claim(fixture)
    finally:
        if acquired:
            client._request_lock.release()
            acquired = False
    queued_worker.join(4)
    assert not queued_worker.is_alive()
    assert result["queued_error"].code == "cancelled"
    assert str(result["queued_error"]) == "cancelled"
    assert not sent.is_set() and requests == []
    fresh_sent, fresh_requests = _observe_requests_after_swap(monkeypatch, client)
    bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
    assert fresh_sent.wait(0) and len(fresh_requests) == 1
    _assert_exact_request(fresh_requests[0], fresh_claim)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)
    _assert_helpers_finished(helpers)


def _observe_requests_after_swap(
    monkeypatch: pytest.MonkeyPatch, client, *, operation="read_subject_bound_accepted_migration_bundle",
):
    connection = client._connection
    assert connection is not None
    sent = Event()
    requests = []
    connection_type = type(connection)
    original = connection_type.send_bytes

    def observed(candidate, raw, *args, **kwargs):
        was_request = False
        if candidate is connection:
            request = json.loads(raw.decode("utf-8"))
            if type(request) is dict and (operation is None or request.get("op") == operation):
                requests.append(request)
                was_request = True
        result = original(candidate, raw, *args, **kwargs)
        if was_request:
            sent.set()
        return result

    monkeypatch.setattr(connection_type, "send_bytes", observed)
    return sent, requests


def test_partial_response_deadline_joins_helper_before_fresh_start(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    context = multiprocessing.get_context("spawn")
    startup_ready = context.Event()
    request_seen = context.Event()
    peer = _swap_in_standalone_peer(
        client, fixture, _partial_response_peer, (startup_ready, request_seen),
    )
    sent, requests = _observe_requests_after_swap(monkeypatch, client)
    helpers = _track_runtime_helpers(monkeypatch)
    try:
        assert startup_ready.wait(10)
        started = time.monotonic()
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=0.25)
        _assert_error(error, "deadline_exceeded")
        assert sent.wait(0) and request_seen.wait(2)
        assert len(requests) == 1
        _assert_exact_request(requests[0], claim)
        assert time.monotonic() - started < 4
        _assert_reaped(peer)
        _assert_helpers_finished(helpers)
        assert client._process is None and client._connection is None and client._generation is None
    finally:
        if peer.is_alive():
            peer.terminate()
            peer.join(5)
        client.close()

    _handshake, fresh_claim = _start_and_claim(fixture)
    bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


def test_cancel_closes_and_releases_tracked_chunk_spool_before_generation_restart(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    roadmap_id = "planning-process-cancel-tracked-spool-roadmap"
    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=_durable_definition_payload(roadmap_id, 1, "Tracked spool baseline"),
        idempotency_key="planning-process-cancel-tracked-spool-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    token = client._token
    assert token is not None
    context = multiprocessing.get_context("spawn")
    startup_ready = context.Event()
    header_sent = context.Event()
    peer = _swap_in_standalone_peer(
        client, fixture, _chunk_header_then_block_peer, (startup_ready, header_sent),
    )
    helpers = _track_runtime_helpers(monkeypatch)
    result = {}

    def receive_large_response() -> None:
        try:
            client.planning_get_revision_bundle(
                _new_operation_claim(fixture), project_id=PROJECT_ID,
                roadmap_id=roadmap_id, revision_id=created.head.current_revision_id,
                timeout_seconds=10,
            )
        except process_runtime.PlanningProcessError as error:
            result["error"] = error

    worker = Thread(target=receive_large_response)
    worker.start()
    try:
        assert startup_ready.wait(10) and header_sent.wait(10)
        deadline = time.monotonic() + 3
        while not token.active_resources() and time.monotonic() < deadline:
            Event().wait(0.001)
        resources = token.active_resources()
        assert len(resources) == 1
        spool = resources[0]
        assert spool.closed is False
        assert client.cancel_inflight() is True
        worker.join(5)
        assert not worker.is_alive()
        assert result["error"].code == "cancelled"
        assert str(result["error"]) == "cancelled"
        _assert_reaped(peer)
        _assert_helpers_finished(helpers)
        assert spool.closed is True
        assert token.active_resources() == ()
        assert client._process is None and client._connection is None
    finally:
        if peer.is_alive():
            peer.terminate()
            peer.join(5)
        if worker.is_alive():
            worker.join(5)

    client.start()
    observed = client.planning_get_head(
        _new_operation_claim(fixture), project_id=PROJECT_ID,
        roadmap_id=roadmap_id, timeout_seconds=10,
    )
    _assert_durable_head_matches_store(observed, created.head)


def test_public_deadline_expiring_during_payload_preparation_never_reaches_ipc(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    claim = _new_operation_claim(fixture)
    sent, requests = _observe_requests(
        monkeypatch, client, operation="planning_create_project",
    )
    expired = Event()
    real_monotonic = time.monotonic
    original_validate = process_runtime._validate_write_payload

    def controlled_monotonic():
        return real_monotonic() + (20 if expired.is_set() else 0)

    def validation_that_crosses_deadline(*args, **kwargs):
        value = original_validate(*args, **kwargs)
        expired.set()
        return value

    monkeypatch.setattr(process_runtime.time, "monotonic", controlled_monotonic)
    monkeypatch.setattr(
        process_runtime, "_validate_write_payload", validation_that_crosses_deadline,
    )
    roadmap_id = "planning-process-preparation-deadline-roadmap"
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.planning_create_project(
            claim, project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=_durable_definition_payload(
                roadmap_id, 1, "deadline expires while validating the document",
            ),
            idempotency_key="planning-process-preparation-deadline-create-0001",
            expected_generation=0, timeout_seconds=10,
        )
    _assert_error(error, "deadline_exceeded")
    assert expired.is_set()
    assert not sent.is_set() and requests == []
    assert claim.claim_id in client._spent_claims
    assert client._process is not None and client._process.is_alive()


def test_mutation_deadline_expiring_during_result_parse_never_publishes_late_success(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    principal = fixture["principal"]
    client.start()
    roadmap_id = "planning-process-result-parse-deadline-roadmap"
    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=_durable_definition_payload(roadmap_id, 1, "parse deadline baseline"),
        idempotency_key="planning-process-result-parse-deadline-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    before = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    expired = Event()
    parsed = Event()
    real_monotonic = time.monotonic
    original_parse = process_runtime._receipt_from_wire

    def controlled_monotonic():
        return real_monotonic() + (20 if expired.is_set() else 0)

    def parse_that_crosses_deadline(value):
        receipt = original_parse(value)
        parsed.set()
        expired.set()
        return receipt

    monkeypatch.setattr(process_runtime.time, "monotonic", controlled_monotonic)
    monkeypatch.setattr(process_runtime, "_receipt_from_wire", parse_that_crosses_deadline)
    sent, requests = _observe_requests(
        monkeypatch, client, operation="planning_append_revision",
    )
    old_process = client._process
    with pytest.raises(process_runtime.PlanningProcessOutcomeUnknown) as error:
        client.planning_append_revision(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=_durable_definition_payload(
                roadmap_id, 2, "committed before result publication",
            ),
            expected_head=created.head,
            idempotency_key="planning-process-result-parse-deadline-append-0001",
            timeout_seconds=10,
        )
    assert error.value.code == "outcome_unknown"
    assert parsed.is_set() and expired.is_set()
    assert sent.is_set() and len(requests) == 1
    _assert_reaped(old_process)
    assert client._process is None and client._connection is None
    committed = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    assert committed.generation == before.generation + 1
    assert committed.current_content_sha256 != before.current_content_sha256


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("request_id", "invalid_response"), ("generation", "invalid_response"),
        ("claim_id", "invalid_response"), ("error", "invalid_request"),
        ("error_request_id", "invalid_response"), ("empty", "invalid_response"),
        ("eof", "child_eof"), ("crash", "child_eof"),
    ],
)
def test_inflight_protocol_failures_reap_without_resending(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch, mode: str, expected_code: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    context = multiprocessing.get_context("spawn")
    startup_ready = context.Event()
    request_seen = context.Event()
    peer = _swap_in_standalone_peer(
        client, fixture, _protocol_response_peer, (mode, startup_ready, request_seen),
    )
    sent, requests = _observe_requests_after_swap(monkeypatch, client)
    helpers = _track_runtime_helpers(monkeypatch)
    try:
        assert startup_ready.wait(10)
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=2)
        _assert_error(error, expected_code)
        assert sent.wait(0) and request_seen.wait(2)
        assert len(requests) == 1
        _assert_exact_request(requests[0], claim)
        if mode == "error":
            assert peer.is_alive()
            assert client._process is peer and client._connection is not None
            client.close()
        _assert_reaped(peer)
        _assert_helpers_finished(helpers)
        assert client._process is None and client._connection is None
    finally:
        if peer.is_alive():
            peer.terminate()
            peer.join(5)


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [("request_id", "child_eof"), ("generation", "invalid_response"), ("claim_id", "invalid_response")],
)
def test_actual_child_closes_on_untrusted_request_correlation(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch, field: str, expected_code: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    process = client._process
    connection = client._connection
    assert process is not None and connection is not None
    connection_type = type(connection)
    original_send = connection_type.send_bytes
    sent = Event()
    requests = []

    def corrupted_send(candidate, raw, *args, **kwargs):
        if candidate is connection:
            request = json.loads(raw.decode("utf-8"))
            if type(request) is dict and request.get("op") == "read_subject_bound_accepted_migration_bundle":
                original_request = deepcopy(request)
                request[field] = "@invalid" if field == "request_id" else "0" * 32
                requests.append((original_request, request[field]))
                raw = process_runtime._encode_frame(request, maximum=process_runtime.REQUEST_MAX_BYTES)
        result = original_send(candidate, raw, *args, **kwargs)
        if candidate is connection and requests:
            sent.set()
        return result

    monkeypatch.setattr(connection_type, "send_bytes", corrupted_send)
    helpers = _track_runtime_helpers(monkeypatch)
    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=5)
        _assert_error(error, expected_code)
        assert sent.wait(0) and len(requests) == 1
        _assert_exact_request(requests[0][0], claim)
        assert requests[0][1] == ("@invalid" if field == "request_id" else "0" * 32)
        assert client._process is None and client._connection is None and client._generation is None
        _assert_reaped(process)
        _assert_helpers_finished(helpers)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)

    _handshake, fresh_claim = _start_and_claim(fixture)
    bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
    _assert_exact_bundle(fixture["expected_bundle"], bundle)


def test_oversized_finite_deadlines_and_bad_identifiers_fail_before_ipc(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, claim = _start_and_claim(fixture)
    sent, requests = _observe_requests(monkeypatch, client)
    for timeout in (1e308, float("inf"), float("nan"), True, 0, 10.001):
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.read_subject_bound_accepted_migration_bundle(claim, timeout_seconds=timeout)
        _assert_error(error, "invalid_request")
    for field in ("owner_id", "subject_id", "project_id"):
        kwargs = {
            "owner_id": OWNER_ID, "subject_id": fixture["subject_id"],
            "project_id": PROJECT_ID, "auth_context_digest": AUTH_CONTEXT_DIGEST,
        }
        kwargs[field] = "@invalid"
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._create_subject_bound_accepted_migration_claim(client, **kwargs)
        _assert_error(error, "invalid_claim")
    if requests or sent.is_set():
        pytest.fail("invalid local input caused request IPC")


def test_handshake_bindings_reject_missing_malformed_and_mismatched_fields(accepted_process_fixture):
    fixture = accepted_process_fixture
    client = fixture["client"]
    generation = "c" * 32
    value = process_runtime._child_handshake(client._config, fixture["issuer"], generation)
    value["pid"] = 12345
    valid = process_runtime._handshake_from_wire(value, client._config, expected_pid=12345)
    assert type(valid) is process_runtime.PlanningProcessHandshake
    _assert_equal(valid.generation, generation, "validated handshake generation")
    mutations = []
    for field, bad in (
        ("protocol", "wrong"), ("op", "wrong"), ("issuer_id", "@invalid"),
        ("key_id", "@invalid"), ("repo_root_sha256", "0" * 64),
        ("backend_digest", "0" * 64), ("pid", 12346), ("generation", "bad"),
    ):
        changed = deepcopy(value)
        changed[field] = bad
        mutations.append(changed)
    changed = deepcopy(value)
    del changed["key_id"]
    mutations.append(changed)
    changed = deepcopy(value)
    changed["types"] = {"forged": "implementation"}
    mutations.append(changed)
    for malformed in mutations:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._handshake_from_wire(malformed, client._config, expected_pid=12345)
        _assert_error(error, "handshake_failed")


@pytest.mark.parametrize("operation", ["cancel", "close"])
def test_startup_cancel_keeps_reap_gate_closed_until_owned_spawn_is_joined(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch, operation: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    spawn_process_type = multiprocessing.get_context("spawn").Process
    original_start = spawn_process_type.start
    original_join = spawn_process_type.join
    spawn_return_gate = Event()
    spawned = Event()
    cancel_entered = Event()
    contender_entered = Event()
    old_joined = Event()
    first_process = []
    spawn_calls = []
    later_spawn_state = []
    contender_result = {}
    first_result = {}
    helpers = _track_runtime_helpers(monkeypatch)

    def delayed_start(process, *args, **kwargs):
        target = getattr(process, "_target", None)
        if target is process_runtime._child_process_main:
            spawn_calls.append(process)
            if len(spawn_calls) == 1:
                first_process.append(process)
                result = original_start(process, *args, **kwargs)
                spawned.set()
                if not spawn_return_gate.wait(8):
                    raise AssertionError("timed out holding spawned child start return")
                return result
            old = first_process[0]
            later_spawn_state.append((old_joined.is_set(), old.is_alive(), old.exitcode))
            return original_start(process, *args, **kwargs)
        return original_start(process, *args, **kwargs)

    def observed_join(process, *args, **kwargs):
        result = original_join(process, *args, **kwargs)
        if first_process and process is first_process[0] and not process.is_alive() and process.exitcode is not None:
            old_joined.set()
        return result

    monkeypatch.setattr(spawn_process_type, "start", delayed_start)
    monkeypatch.setattr(spawn_process_type, "join", observed_join)

    def initial_start():
        try:
            first_result["handshake"] = client.start()
        except process_runtime.PlanningProcessError as error:
            first_result["error"] = error

    def cancel():
        cancel_entered.set()
        if operation == "cancel":
            first_result["cancelled"] = client.cancel_inflight()
        else:
            client.close()
            first_result["cancelled"] = True

    def competing_start():
        contender_entered.set()
        try:
            contender_result["handshake"] = client.start()
        except process_runtime.PlanningProcessError as error:
            contender_result["error"] = error

    initial_worker = Thread(target=initial_start)
    initial_worker.start()
    try:
        assert spawned.wait(5)
        acquired_state_lock = client._state_lock.acquire(timeout=0.05)
        assert acquired_state_lock is True
        try:
            assert client._state_lock._is_owned()
        finally:
            if acquired_state_lock:
                client._state_lock.release()
        cancel_worker = Thread(target=cancel)
        cancel_worker.start()
        assert cancel_entered.wait(2)
        contender = Thread(target=competing_start)
        contender.start()
        assert contender_entered.wait(2)
        assert len(spawn_calls) == 1

        spawn_return_gate.set()
        initial_worker.join(10)
        cancel_worker.join(10)
        contender.join(10)
        assert not initial_worker.is_alive() and not cancel_worker.is_alive() and not contender.is_alive()
        assert old_joined.is_set()
        _assert_helpers_finished(helpers)
        assert len(spawn_calls) in {1, 2}
        if len(spawn_calls) == 2:
            assert later_spawn_state[0][0] is True
            assert later_spawn_state[0][1] is False and later_spawn_state[0][2] is not None
        if client._process is None:
            _handshake, fresh_claim = _start_and_claim(fixture)
        else:
            assert client._connection is not None and client._generation is not None
            fresh_claim = _new_claim(fixture)
        bundle = client.read_subject_bound_accepted_migration_bundle(fresh_claim, timeout_seconds=10)
        _assert_exact_bundle(fixture["expected_bundle"], bundle)
        _assert_reaped(first_process[0])
    finally:
        spawn_return_gate.set()
        if initial_worker.is_alive():
            initial_worker.join(10)
        if client._process is not None:
            client.close()


@pytest.mark.parametrize("broken_setting", [
    "auth_disabled", "missing_secret", "missing_key", "malformed_secret", "short_secret", "malformed_key",
])
def test_missing_or_malformed_issuer_configuration_fails_closed(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch, broken_setting: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    if broken_setting == "auth_disabled":
        monkeypatch.setenv("AUTH_ENABLED", "false")
    elif broken_setting == "missing_secret":
        monkeypatch.delenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", raising=False)
    elif broken_setting == "missing_key":
        monkeypatch.delenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID", raising=False)
    elif broken_setting == "malformed_secret":
        monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", "not-base64!")
    elif broken_setting == "short_secret":
        monkeypatch.setenv(
            "ODYSSEUS_PLANNING_ISSUER_SECRET_B64", base64.b64encode(b"short").decode("ascii"),
        )
    else:
        monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID", "@invalid")

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.start()
    _assert_error(error, "unavailable")
    for private_value in (ISSUER_SECRET.decode(), KEY_ID, OWNER_ID, fixture["subject_id"], AUTH_CONTEXT_DIGEST):
        _assert_private_values_absent(str(error.value), (private_value,))
        _assert_private_values_absent(repr(error.value), (private_value,))
    assert client._process is None and client._connection is None


def test_durable_family_runs_all_six_operations_against_store_and_survives_restart(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _handshake, _read_claim = _start_and_claim(fixture)
    sent, requests = _observe_requests(monkeypatch, client, operation=None)
    principal = fixture["principal"]
    roadmap_id = "planning-process-family-roadmap"
    first_payload = _durable_definition_payload(roadmap_id, 1, "Durable family first revision")
    create_key = "planning-process-family-create-0001"
    principal_identity = _principal_identity(fixture)
    create_command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(first_payload).hexdigest(),
        "principal_identity": principal_identity,
    }
    create_digest = _independent_canonical_digest(create_command)
    first_revision_id = _independent_revision_id(create_digest, 1)

    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=first_payload, idempotency_key=create_key,
        expected_generation=0, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, created, principal, roadmap_id, command=create_command, before_head=None,
        new_revision_id=first_revision_id, restored_revision_id=None,
    )
    assert created.receipt.command_kind == "create_project"
    assert created.head.generation == 1
    assert created.head.current_revision_id == first_revision_id
    _assert_durable_bundle_matches_store(
        fixture,
        client.planning_get_revision_bundle(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            revision_id=first_revision_id, timeout_seconds=10,
        ),
        principal, roadmap_id, first_revision_id,
    )

    replayed_create = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=first_payload, idempotency_key=create_key,
        expected_generation=0, timeout_seconds=10,
    )
    assert replayed_create.receipt == created.receipt
    assert replayed_create.head == created.head

    second_payload = _durable_definition_payload(roadmap_id, 2, "Durable family second revision")
    append_key = "planning-process-family-append-0001"
    append_command = {
        "kind": "append_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_head": _head_command_projection(created.head),
        "payload_sha256": "sha256:" + hashlib.sha256(second_payload).hexdigest(),
        "principal_identity": principal_identity,
    }
    append_digest = _independent_canonical_digest(append_command)
    second_revision_id = _independent_revision_id(append_digest, 2)
    appended = client.planning_append_revision(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=second_payload, expected_head=created.head,
        idempotency_key=append_key, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, appended, principal, roadmap_id, command=append_command,
        before_head=created.head, new_revision_id=second_revision_id, restored_revision_id=None,
    )
    assert appended.receipt.command_kind == "append_revision"
    assert appended.head.generation == created.head.generation + 1
    assert appended.head.current_revision_id == second_revision_id
    _assert_durable_bundle_matches_store(
        fixture,
        client.planning_get_revision_bundle(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            revision_id=second_revision_id, timeout_seconds=10,
        ),
        principal, roadmap_id, second_revision_id,
    )

    approve_key = "planning-process-family-approve-0001"
    approve_command = {
        "kind": "approve_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_head": _head_command_projection(appended.head),
        "revision_id": second_revision_id, "principal_identity": principal_identity,
    }
    approved = client.planning_approve_revision(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        revision_id=second_revision_id, expected_head=appended.head,
        idempotency_key=approve_key, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, approved, principal, roadmap_id, command=approve_command,
        before_head=appended.head, new_revision_id=None, restored_revision_id=None,
    )
    assert approved.receipt.command_kind == "approve_revision"

    restored_payload = _durable_definition_payload(roadmap_id, 3, "Durable family first revision")
    revert_command = {
        "kind": "revert_head", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_head": _head_command_projection(approved.head),
        "ancestor_revision_id": first_revision_id,
        "payload_sha256": "sha256:" + hashlib.sha256(restored_payload).hexdigest(),
        "principal_identity": principal_identity,
    }
    revert_digest = _independent_canonical_digest(revert_command)
    reverted_revision_id = _independent_revision_id(revert_digest, 3)
    reverted = client.planning_revert_head(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        ancestor_revision_id=first_revision_id, expected_head=approved.head,
        idempotency_key="planning-process-family-revert-0001", timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, reverted, principal, roadmap_id, command=revert_command,
        before_head=approved.head, new_revision_id=reverted_revision_id,
        restored_revision_id=first_revision_id,
    )
    assert reverted.receipt.command_kind == "revert_head"
    assert reverted.head.generation == approved.head.generation + 1
    assert reverted.head.current_revision_id == reverted_revision_id
    assert fixture["store"].read_payload(
        principal, owner_id=OWNER_ID, revision_id=reverted_revision_id,
    ) == restored_payload
    _assert_durable_bundle_matches_store(
        fixture,
        client.planning_get_revision_bundle(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            revision_id=reverted.head.current_revision_id, timeout_seconds=10,
        ),
        principal, roadmap_id, reverted.head.current_revision_id,
    )

    queried_head = client.planning_get_head(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        timeout_seconds=10,
    )
    oracle_head = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    _assert_durable_head_matches_store(queried_head, oracle_head)
    expected_bodies = {
        "planning_create_project": {"project_id", "roadmap_id", "payload_b64", "idempotency_key", "expected_generation"},
        "planning_get_head": {"project_id", "roadmap_id"},
        "planning_get_revision_bundle": {"project_id", "roadmap_id", "revision_id"},
        "planning_append_revision": {"project_id", "roadmap_id", "payload_b64", "expected_head", "idempotency_key"},
        "planning_approve_revision": {"project_id", "roadmap_id", "revision_id", "expected_head", "idempotency_key"},
        "planning_revert_head": {"project_id", "roadmap_id", "ancestor_revision_id", "expected_head", "idempotency_key"},
    }
    assert len(requests) == 9
    assert len([item for item in requests if item["op"] == "planning_create_project"]) == 2
    for item in requests:
        _assert_exact_durable_request(item, item["op"], item["claim"], expected_bodies[item["op"]])
    assert sent.is_set()

    old_process = client._process
    client.close()
    _assert_reaped(old_process)
    _handshake = client.start()
    restarted_head = client.planning_get_head(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        timeout_seconds=10,
    )
    _assert_durable_head_matches_store(restarted_head, oracle_head)
    _assert_durable_bundle_matches_store(
        fixture,
        client.planning_get_revision_bundle(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            revision_id=second_revision_id, timeout_seconds=10,
        ),
        principal, roadmap_id, second_revision_id,
    )


def test_durable_idempotency_conflicts_for_body_subject_and_auth_digest_leave_head_unchanged(
    accepted_process_fixture,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _start_and_claim(fixture)
    principal = fixture["principal"]
    roadmap_id = "planning-process-idempotency-roadmap"
    key = "planning-process-idempotency-create-0001"
    original_payload = _durable_definition_payload(roadmap_id, 1, "Original idempotent body")
    create_command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(original_payload).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    create_revision_id = _independent_revision_id(
        _independent_canonical_digest(create_command), 1,
    )
    initial = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=original_payload, idempotency_key=key, expected_generation=0,
        timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, initial, principal, roadmap_id, command=create_command,
        before_head=None, new_revision_id=create_revision_id, restored_revision_id=None,
    )
    baseline = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )

    assert fixture["auth"].create_user("durable-conflict-user", "Password-456", is_admin=False)
    other_subject = fixture["auth"].subject_id_for_username("durable-conflict-user")
    assert isinstance(other_subject, str) and other_subject
    conflicts = (
        (_durable_definition_payload(roadmap_id, 1, "Changed idempotent body"), fixture["subject_id"], AUTH_CONTEXT_DIGEST),
        (original_payload, other_subject, AUTH_CONTEXT_DIGEST),
        (original_payload, fixture["subject_id"], "sha256:" + "5" * 64),
    )
    for index, (payload, subject_id, auth_digest) in enumerate(conflicts, start=1):
        with pytest.raises(process_runtime.PlanningProcessStoreError) as error:
            client.planning_create_project(
                _new_operation_claim(
                    fixture, subject_id=subject_id, auth_context_digest=auth_digest,
                ),
                project_id=PROJECT_ID, roadmap_id=roadmap_id, payload_bytes=payload,
                idempotency_key=key, expected_generation=0, timeout_seconds=10,
            )
        _assert_error(error, "idempotency_conflict")
        observed = client.planning_get_head(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            timeout_seconds=10,
        )
        _assert_durable_head_matches_store(observed, baseline)
        oracle = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        assert oracle == baseline

    replay = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=original_payload, idempotency_key=key, expected_generation=0,
        timeout_seconds=10,
    )
    assert replay.receipt == initial.receipt
    assert replay.head == initial.head
    assert fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    ) == baseline


def test_durable_append_deadline_is_unknown_reaped_and_requires_manual_same_key_retry(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _start_and_claim(fixture)
    principal = fixture["principal"]
    roadmap_id = "planning-process-deadline-roadmap"
    first_payload = _durable_definition_payload(roadmap_id, 1, "Deadline baseline")
    create_command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(first_payload).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    create_revision_id = _independent_revision_id(
        _independent_canonical_digest(create_command), 1,
    )
    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=first_payload, idempotency_key="planning-process-deadline-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, created, principal, roadmap_id, command=create_command,
        before_head=None, new_revision_id=create_revision_id, restored_revision_id=None,
    )
    before = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    assert before.generation == created.head.generation == 1
    parked_process = client._process
    lock_process = None
    try:
        lock_process, _release_lock = _spawn_lock_holder(fixture["durable_root"])
        append_key = "planning-process-deadline-append-0001"
        append_payload = _durable_definition_payload(roadmap_id, 2, "Deadline append retry")
        claim = _new_operation_claim(fixture)
        _sent, requests = _observe_requests(monkeypatch, client, operation="planning_append_revision")
        with pytest.raises(process_runtime.PlanningProcessOutcomeUnknown) as error:
            client.planning_append_revision(
                claim, project_id=PROJECT_ID, roadmap_id=roadmap_id,
                payload_bytes=append_payload, expected_head=created.head,
                idempotency_key=append_key, timeout_seconds=0.25,
            )
        assert error.value.code == "outcome_unknown"
        assert len(requests) == 1
        _assert_exact_durable_request(
            requests[0], "planning_append_revision", claim,
            {"project_id", "roadmap_id", "payload_b64", "expected_head", "idempotency_key"},
        )
        _assert_reaped(parked_process)
        assert client._process is None and client._connection is None

        lock_process.terminate()
        lock_process.join(10)
        _assert_reaped(lock_process)
        lock_process = None

        # Outcome uncertainty never starts a replacement child or replays the write.
        unchanged = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        assert unchanged == before
        client.start()
        fresh = client.planning_get_head(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            timeout_seconds=10,
        )
        _assert_durable_head_matches_store(fresh, before)

        retried = client.planning_append_revision(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=append_payload, expected_head=created.head,
            idempotency_key=append_key, timeout_seconds=10,
        )
        append_command = {
            "kind": "append_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id, "expected_head": _head_command_projection(created.head),
            "payload_sha256": "sha256:" + hashlib.sha256(append_payload).hexdigest(),
            "principal_identity": _principal_identity(fixture),
        }
        expected_revision_id = _independent_revision_id(
            _independent_canonical_digest(append_command), 2,
        )
        assert retried.head.generation == before.generation + 1
        _assert_durable_write_matches_store(
            fixture, retried, principal, roadmap_id, command=append_command,
            before_head=created.head, new_revision_id=expected_revision_id,
            restored_revision_id=None,
        )
        assert retried.head.current_revision_id == expected_revision_id
        replayed = client.planning_append_revision(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=append_payload, expected_head=created.head,
            idempotency_key=append_key, timeout_seconds=10,
        )
        assert replayed.receipt == retried.receipt
        assert replayed.head == retried.head
        final = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        assert final.generation == before.generation + 1
        assert final.current_revision_id == retried.head.current_revision_id
    finally:
        if lock_process is not None:
            if lock_process.is_alive():
                lock_process.terminate()
            lock_process.join(10)
            _assert_reaped(lock_process)


def test_durable_compare_and_swap_rejects_each_stale_head_field_without_mutation(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _start_and_claim(fixture)
    principal = fixture["principal"]
    roadmap_id = "planning-process-cas-roadmap"
    first_payload = _durable_definition_payload(roadmap_id, 1, "CAS baseline")
    create_command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(first_payload).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    first_revision_id = _independent_revision_id(
        _independent_canonical_digest(create_command), 1,
    )
    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=first_payload, idempotency_key="planning-process-cas-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, created, principal, roadmap_id, command=create_command,
        before_head=None, new_revision_id=first_revision_id, restored_revision_id=None,
    )
    current = created.head
    candidate = _durable_definition_payload(roadmap_id, 2, "CAS must not commit")
    _sent, requests = _observe_requests(monkeypatch, client, operation="planning_append_revision")
    local_fields = {
        "owner_id": OWNER_ID + "-foreign",
        "project_id": "another-project",
        "roadmap_id": roadmap_id + "-foreign",
    }
    for bad_field, stale_value in local_fields.items():
        malformed = replace(current, **{bad_field: stale_value})
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.planning_append_revision(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                payload_bytes=candidate, expected_head=malformed,
                idempotency_key=f"planning-process-cas-local-{bad_field}", timeout_seconds=10,
            )
        assert error.value.code == "invalid_request"
        assert requests == []

    store_fields = {
        "current_revision_id": "planning-revision:" + "0" * 64,
        "current_content_sha256": "sha256:" + "0" * 64,
        "approved_revision_id": "planning-revision:" + "1" * 64,
        "generation": current.generation + 1,
    }
    for index, (bad_field, stale_value) in enumerate(store_fields.items(), start=1):
        stale = replace(current, **{bad_field: stale_value})
        claim = _new_operation_claim(fixture)
        with pytest.raises(process_runtime.PlanningProcessStoreError) as error:
            client.planning_append_revision(
                claim, project_id=PROJECT_ID, roadmap_id=roadmap_id,
                payload_bytes=candidate, expected_head=stale,
                idempotency_key=f"planning-process-cas-store-{bad_field}", timeout_seconds=10,
            )
        assert error.value.code == "stale_authority"
        assert len(requests) == index
        _assert_exact_durable_request(
            requests[-1], "planning_append_revision", claim,
            {"project_id", "roadmap_id", "payload_b64", "expected_head", "idempotency_key"},
        )
        oracle = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        _assert_durable_head_matches_store(current, oracle)
    assert len(requests) == len(store_fields)
    final_oracle = fixture["store"].get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    _assert_durable_head_matches_store(current, final_oracle)
    _assert_durable_head_matches_store(
        client.planning_get_head(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            timeout_seconds=10,
        ),
        current,
    )


def test_durable_local_hostile_scalars_and_body_shapes_fail_before_request(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _start_and_claim(fixture)
    _sent, requests = _observe_requests(monkeypatch, client, operation="planning_create_project")
    roadmap_id = "planning-process-hostile-roadmap"
    payload = _durable_definition_payload(roadmap_id, 1, "Hostile boundary baseline")
    hostile_calls = (
        {"expected_generation": True},
        {"payload_bytes": bytearray(payload)},
        {"idempotency_key": "planning-process:hostile-key"},
        {"roadmap_id": "planning-process:hostile-roadmap"},
        {"project_id": "another-project"},
    )
    for index, override in enumerate(hostile_calls):
        kwargs = {
            "project_id": PROJECT_ID, "roadmap_id": roadmap_id,
            "payload_bytes": payload, "idempotency_key": f"planning-process-hostile-key-{index:04d}",
            "expected_generation": 0, "timeout_seconds": 10,
        }
        kwargs.update(override)
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.planning_create_project(_new_operation_claim(fixture), **kwargs)
        assert error.value.code == "invalid_request"
    with pytest.raises(TypeError):
        client.planning_create_project(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=payload, idempotency_key="planning-process-hostile-key-extra",
            expected_generation=0, timeout_seconds=10, owner_id=OWNER_ID,
        )
    assert requests == []


def test_durable_operation_claim_is_client_generation_bound_and_single_use_before_io(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    principal = fixture["principal"]
    roadmap_id = "planning-process-claim-roadmap"
    payload = _durable_definition_payload(roadmap_id, 1, "Bound operation claim")
    create_command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    expected_revision_id = _independent_revision_id(_independent_canonical_digest(create_command), 1)
    create_claim = _new_operation_claim(fixture)
    created = client.planning_create_project(
        create_claim, project_id=PROJECT_ID, roadmap_id=roadmap_id, payload_bytes=payload,
        idempotency_key="planning-process-claim-create-0001", expected_generation=0,
        timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, created, principal, roadmap_id, command=create_command, before_head=None,
        new_revision_id=expected_revision_id, restored_revision_id=None,
    )

    _sent, same_generation_requests = _observe_requests(
        monkeypatch, client, operation=None,
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.planning_get_head(
            create_claim, project_id=PROJECT_ID, roadmap_id=roadmap_id, timeout_seconds=10,
        )
    assert error.value.code == "single_use_claim"
    assert same_generation_requests == []

    stale_claim = _new_operation_claim(fixture)
    client.close()
    client.start()
    _new_sent, restarted_requests = _observe_requests_after_swap(
        monkeypatch, client, operation=None,
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.planning_get_head(
            stale_claim, project_id=PROJECT_ID, roadmap_id=roadmap_id, timeout_seconds=10,
        )
    assert error.value.code == "stale_claim"
    assert restarted_requests == []
    fresh_head = client.planning_get_head(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        timeout_seconds=10,
    )
    _assert_durable_head_matches_store(
        fresh_head,
        fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        ),
    )
    assert len(restarted_requests) == 1

    foreign = process_runtime.PlanningProcessClient(
        repo_root=fixture["repository_root"],
        definitions_root=fixture["definitions_root"],
        durable_root=fixture["durable_root"],
        legacy_owner=OWNER_ID,
    )
    foreign.start()
    foreign_claim = _new_operation_claim(fixture)
    _foreign_sent, foreign_requests = _observe_requests(monkeypatch, foreign, operation=None)
    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            foreign.planning_get_head(
                foreign_claim, project_id=PROJECT_ID, roadmap_id=roadmap_id,
                timeout_seconds=10,
            )
        assert error.value.code == "unregistered_claim"
        assert foreign_requests == []
    finally:
        foreign.close()


def test_durable_dto_records_are_immutable_slotted_and_content_free(accepted_process_fixture):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    principal = fixture["principal"]
    roadmap_id = "planning-process-dto-roadmap"
    payload = _durable_definition_payload(roadmap_id, 1, "Content free DTO")
    command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    revision_id = _independent_revision_id(_independent_canonical_digest(command), 1)
    result = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=payload, idempotency_key="planning-process-dto-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    bundle = client.planning_get_revision_bundle(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        revision_id=revision_id, timeout_seconds=10,
    )
    values = (result, result.receipt, result.head, bundle, bundle.revision)
    for value in values:
        assert type(value).__dataclass_params__.frozen is True
        assert type(value).__slots__ == tuple(field.name for field in fields(value))
        rendered = repr(value)
        assert rendered.endswith("(<content-free>)")
        _assert_private_values_absent(
            rendered,
            (ISSUER_SECRET.decode(), KEY_ID, OWNER_ID, fixture["subject_id"], AUTH_CONTEXT_DIGEST,
             payload.decode("utf-8")),
        )
    assert not hasattr(result, "__dict__")
    assert not hasattr(result.receipt, "__dict__")
    assert not hasattr(result.head, "__dict__")
    assert not hasattr(bundle, "__dict__")
    assert not hasattr(bundle.revision, "__dict__")
    assert bundle.payload == payload
    assert result.head.current_revision_id == revision_id
    _assert_durable_write_matches_store(
        fixture, result, principal, roadmap_id, command=command, before_head=None,
        new_revision_id=revision_id, restored_revision_id=None,
    )


@pytest.mark.parametrize("mode", ["extra_response_key", "missing_head_generation", "stale_request_id"])
def test_durable_response_schema_and_correlation_fail_closed_and_reap_peer(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch, mode: str,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    _start_and_claim(fixture)
    claim = _new_operation_claim(fixture)
    context = multiprocessing.get_context("spawn")
    startup_ready = context.Event()
    request_seen = context.Event()
    peer = _swap_in_standalone_peer(
        client, fixture, _durable_response_peer, (mode, startup_ready, request_seen),
    )
    sent, requests = _observe_requests_after_swap(
        monkeypatch, client, operation="planning_get_head",
    )
    try:
        assert startup_ready.wait(10)
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client.planning_get_head(
                claim, project_id=PROJECT_ID, roadmap_id="planning-process-response-roadmap",
                timeout_seconds=0.5,
            )
        assert error.value.code in {"protocol_error", "invalid_response"}
        assert sent.wait(2)
        assert request_seen.wait(2)
        assert len(requests) == 1
        _assert_exact_durable_request(
            requests[0], "planning_get_head", claim, {"project_id", "roadmap_id"},
        )
        _assert_reaped(peer)
        assert client._process is None and client._connection is None
    finally:
        if peer.is_alive():
            peer.terminate()
            peer.join(5)
        _assert_reaped(peer)


def test_canonical_planning_payloads_over_old_cap_roundtrip_restart_and_replay(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    principal = fixture["principal"]
    roadmap_id = "planning-process-maximum-payload-roadmap"
    old_limit = 4 * 1024 * 1024
    payload = _durable_definition_payload(
        roadmap_id, 1, "Former maximum canonical planning payload", exact_size_bytes=old_limit,
    )
    assert len(payload) == old_limit
    encoded = base64.b64encode(payload).decode("ascii")
    assert len(encoded) == process_runtime.WRITE_PAYLOAD_B64_MAX_CHARS == 5_592_408
    assert base64.b64decode(encoded, validate=True) == payload
    sent, requests = _observe_requests(
        monkeypatch, client, operation="planning_create_project",
    )
    command = {
        "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_generation": 0,
        "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    expected_revision_id = _independent_revision_id(_independent_canonical_digest(command), 1)
    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=payload, idempotency_key="planning-process-maximum-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, created, principal, roadmap_id, command=command, before_head=None,
        new_revision_id=expected_revision_id, restored_revision_id=None,
    )
    create_request = requests[0]
    _assert_exact_durable_request(
        create_request, "planning_create_project", create_request["claim"],
        {"project_id", "roadmap_id", "payload_b64", "idempotency_key", "expected_generation"},
    )
    assert len(create_request["body"]["payload_b64"]) == process_runtime.WRITE_PAYLOAD_B64_MAX_CHARS
    assert base64.b64decode(create_request["body"]["payload_b64"], validate=True) == payload

    bundle = client.planning_get_revision_bundle(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        revision_id=expected_revision_id, timeout_seconds=10,
    )
    _assert_durable_bundle_matches_store(
        fixture, bundle, principal, roadmap_id, expected_revision_id,
    )
    assert bundle.payload == payload
    over_old_limit = _durable_definition_payload(
        roadmap_id, 2, "Document transported beyond the old request ceiling",
        exact_size_bytes=5 * 1024 * 1024,
    )
    append_key = "planning-process-over-old-cap-append-0001"
    append_command = {
        "kind": "append_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
        "roadmap_id": roadmap_id, "expected_head": _head_command_projection(created.head),
        "payload_sha256": "sha256:" + hashlib.sha256(over_old_limit).hexdigest(),
        "principal_identity": _principal_identity(fixture),
    }
    expected_append_id = _independent_revision_id(
        _independent_canonical_digest(append_command), 2,
    )
    appended = client.planning_append_revision(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=over_old_limit, expected_head=created.head,
        idempotency_key=append_key, timeout_seconds=10,
    )
    _assert_durable_write_matches_store(
        fixture, appended, principal, roadmap_id, command=append_command,
        before_head=created.head, new_revision_id=expected_append_id,
        restored_revision_id=None,
    )
    assert len(over_old_limit) > old_limit
    client.close()
    client.start()
    replayed = client.planning_append_revision(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=over_old_limit, expected_head=created.head,
        idempotency_key=append_key, timeout_seconds=10,
    )
    assert replayed == appended
    readback = client.planning_get_revision_bundle(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        revision_id=expected_append_id, timeout_seconds=10,
    )
    assert readback.payload == over_old_limit
    assert len(requests) == 1
    assert sent.is_set()


def _planning_document_over_protocol_node_budget(
    roadmap_id: str, revision: int, marker: str,
):
    document = json.loads(_durable_definition_payload(
        roadmap_id, revision, "Document node budget is not a document cap",
    ).decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap["assumptions"] = [
        f"document-node-{marker}-{index:06d}" for index in range(200_001)
    ]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"][roadmap_id]["content_hash"] = roadmap["content_hash"]
    payload = json.dumps(
        document, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return document, payload


def test_valid_planning_document_over_protocol_node_budget_is_document_valid():
    roadmap_id = "planning-process-over-protocol-node-budget"
    document, payload = _planning_document_over_protocol_node_budget(
        roadmap_id, 1, "local",
    )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._walk_json(document)
    _assert_error(error, "protocol_error")
    assert process_runtime._validate_write_payload(
        payload, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    ) == payload


def test_process_create_append_and_readback_accept_documents_over_protocol_node_budget(
    accepted_process_fixture,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    roadmap_id = "planning-process-node-budget-roundtrip-roadmap"
    _created_document, created_payload = _planning_document_over_protocol_node_budget(
        roadmap_id, 1, "create",
    )
    created = client.planning_create_project(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=created_payload,
        idempotency_key="planning-process-node-budget-create-0001",
        expected_generation=0, timeout_seconds=10,
    )
    _appended_document, appended_payload = _planning_document_over_protocol_node_budget(
        roadmap_id, 2, "append",
    )
    appended = client.planning_append_revision(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=appended_payload, expected_head=created.head,
        idempotency_key="planning-process-node-budget-append-0001",
        timeout_seconds=10,
    )
    created_readback = client.planning_get_revision_bundle(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        revision_id=created.head.current_revision_id, timeout_seconds=10,
    )
    appended_readback = client.planning_get_revision_bundle(
        _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
        revision_id=appended.head.current_revision_id, timeout_seconds=10,
    )
    assert created_readback.payload == created_payload
    assert appended_readback.payload == appended_payload
    assert appended.head.generation == created.head.generation + 1


def test_child_revision_bundle_and_append_use_atomic_store_operations(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    store = fixture["store"]
    principal = fixture["principal"]
    issuer = fixture["issuer"]
    roadmap_id = "planning-process-atomic-store-operations"
    created_payload = _durable_definition_payload(
        roadmap_id, 1, "Atomic child store source",
    )
    created = store.create_project(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=created_payload,
        idempotency_key="planning-process-atomic-store-create-0001",
        expected_generation=0,
    )
    store_type = type(store)
    original_bundle = store_type.read_revision_bundle
    original_append = store_type.append_revision
    bundle_calls = []
    append_calls = []

    def counted_bundle(actual_store, actual_principal, **kwargs):
        bundle_calls.append(kwargs["revision_id"])
        return original_bundle(actual_store, actual_principal, **kwargs)

    def counted_append(actual_store, actual_principal, **kwargs):
        append_calls.append(kwargs["idempotency_key"])
        return original_append(actual_store, actual_principal, **kwargs)

    def forbidden_preflight(*_args, **_kwargs):
        pytest.fail("child used a superseded multi-read or append preflight")

    monkeypatch.setattr(store_type, "read_revision_bundle", counted_bundle)
    monkeypatch.setattr(store_type, "append_revision", counted_append)
    monkeypatch.setattr(store_type, "read_revision", forbidden_preflight)
    monkeypatch.setattr(store_type, "read_payload", forbidden_preflight)
    monkeypatch.setattr(store_type, "replay_append_revision", forbidden_preflight)
    monkeypatch.setattr(store_type, "get_head", forbidden_preflight)

    read_result = process_runtime._child_durable_operation(
        "planning_get_revision_bundle",
        {
            "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id,
            "revision_id": created.new_revision_id,
        },
        principal=principal, owner_id=OWNER_ID, claim_project_id=PROJECT_ID,
        durable_store=store, issuer=issuer,
    )
    assert bundle_calls == [created.new_revision_id]
    assert base64.b64decode(read_result["payload_b64"], validate=True) == created_payload

    appended_payload = _durable_definition_payload(
        roadmap_id, 2, "Atomic child store append",
    )
    append_result = process_runtime._child_durable_operation(
        "planning_append_revision",
        {
            "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id,
            "payload_b64": base64.b64encode(appended_payload).decode("ascii"),
            "expected_head": _head_command_projection(created.after_head),
            "idempotency_key": "planning-process-atomic-store-append-0001",
        },
        principal=principal, owner_id=OWNER_ID, claim_project_id=PROJECT_ID,
        durable_store=store, issuer=issuer,
    )
    assert append_calls == ["planning-process-atomic-store-append-0001"]
    assert append_result["head"]["generation"] == created.after_head.generation + 1
    assert append_result["receipt"]["new_revision_id"] == append_result["head"]["current_revision_id"]


@pytest.mark.parametrize(
    "tamper",
    ["document_content", "dto_content_hash"],
    ids=["document-content", "dto-content-hash"],
)
def test_revision_bundle_dto_rejects_document_and_metadata_hash_tamper(
    accepted_process_fixture, tamper: str,
):
    fixture = accepted_process_fixture
    store = fixture["store"]
    principal = fixture["principal"]
    roadmap_id = f"planning-process-bundle-hash-{tamper}"
    payload = _durable_definition_payload(roadmap_id, 1, "DTO hash binding")
    created = store.create_project(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=payload,
        idempotency_key=f"planning-process-bundle-hash-{tamper}-0001",
        expected_generation=0,
    )
    member = store.read_revision_bundle(
        principal, owner_id=OWNER_ID, revision_id=created.new_revision_id,
    )
    revision = process_runtime._durable_revision_from_wire(
        process_runtime._durable_revision_wire(member.revision)
    )
    candidate_payload = member.payload
    if tamper == "document_content":
        document = json.loads(candidate_payload.decode("utf-8"))
        document["roadmaps"][0]["title"] = "Tampered without a content hash update"
        candidate_payload = json.dumps(
            document, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        revision = replace(
            revision,
            payload_sha256="sha256:" + hashlib.sha256(candidate_payload).hexdigest(),
        )
    else:
        revision = replace(revision, content_sha256="sha256:" + "0" * 64)
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime.PlanningProcessRevisionBundle(revision, candidate_payload)
    _assert_error(error, "invalid_response")


def _canonical_json_frame_of_size(size: int) -> bytes:
    prefix = b'{"pad":"'
    suffix = b'"}'
    if size < len(prefix) + len(suffix):
        raise ValueError("requested frame size is too small")
    raw = prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix
    assert len(raw) == size
    value = json.loads(raw.decode("ascii"))
    assert json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("ascii") == raw
    return raw


def test_request_and_response_frame_codecs_accept_exact_canonical_limits_and_reject_valid_json_overage():
    maxima = (
        process_runtime.REQUEST_MAX_BYTES,
        process_runtime.WRITE_REQUEST_MAX_BYTES,
        process_runtime.RESPONSE_MAX_BYTES,
    )
    assert maxima == (16 * 1024, 6 * 1024 * 1024, 16 * 1024 * 1024)
    for maximum in maxima:
        raw = _canonical_json_frame_of_size(maximum)
        value = json.loads(raw.decode("ascii"))
        assert process_runtime._encode_frame(value, maximum=maximum) == raw
        assert process_runtime._decode_frame(raw, maximum=maximum) == value
        over = _canonical_json_frame_of_size(maximum + 1)
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._encode_frame(json.loads(over.decode("ascii")), maximum=maximum)
        assert error.value.code == "protocol_error"
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._decode_frame(over, maximum=maximum)
        assert error.value.code == "protocol_error"


@pytest.mark.parametrize(("operation", "maximum"), [
    ("planning_migration_export_json", 4 * 1024 * 1024),
    ("planning_migration_export_markdown", 8 * 1024 * 1024),
    ("planning_migration_status", 12 * 1024 * 1024),
])
def test_real_child_pipe_accepts_exact_migration_response_envelopes_before_dto_rejection(
    accepted_process_fixture, operation: str, maximum: int,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    client.start()
    response_size = multiprocessing.get_context("spawn").Value("q", 0)
    peer = _swap_in_standalone_peer(
        client, fixture, _large_migration_result_peer, (operation, maximum, response_size),
    )
    claim = _new_operation_claim(fixture)
    body = {} if operation == "planning_migration_status" else {"roadmap_id": "wire-bound-roadmap"}
    try:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            client._planning_read(claim, operation=operation, body=body, timeout_seconds=10)
        _assert_error(error, "invalid_response")
        peer.join(5)
        _assert_reaped(peer)
        assert 0 < response_size.value <= process_runtime.RESPONSE_MAX_BYTES
    finally:
        if peer.is_alive():
            peer.terminate()
            peer.join(5)


def test_document_response_transfer_enforces_header_order_total_digest_and_canonical_base64(
    monkeypatch: pytest.MonkeyPatch,
):
    class Frames:
        def __init__(self, values): self.values = list(values)
        def recv_bytes(self, maxlength):
            if not self.values: raise EOFError
            value = self.values.pop(0)
            assert len(value) <= maxlength
            return value

    operation = "planning_migration_status"
    request_id = "a" * 32; generation = "b" * 32; claim_id = "c" * 32
    monkeypatch.setattr(process_runtime, "_CHUNK_RAW_MAX_BYTES", 5)
    raw = process_runtime._encode_frame({"complete": "abcdefghijk"}, maximum=1024)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    count = (len(raw) + 4) // 5

    def header(**changes):
        value = {
            "protocol": process_runtime.PROTOCOL, "op": operation + "_header",
            "request_id": request_id, "generation": generation, "claim_id": claim_id,
            "total_bytes": len(raw), "count": count, "payload_sha256": digest,
        }
        value.update(changes)
        return process_runtime._encode_frame(
            value, maximum=process_runtime.RESPONSE_MAX_BYTES,
        )

    def frame(sequence, chunk, **changes):
        value = {
            "protocol": process_runtime.PROTOCOL, "op": operation + "_chunk",
            "request_id": request_id, "generation": generation, "claim_id": claim_id,
            "sequence": sequence, "count": count, "total_bytes": len(raw),
            "payload_sha256": digest,
            "chunk_b64": base64.b64encode(chunk).decode("ascii"),
        }
        value.update(changes)
        return process_runtime._encode_frame({
            **value,
        }, maximum=process_runtime.RESPONSE_MAX_BYTES)

    chunks = [frame(index, raw[index * 5:(index + 1) * 5]) for index in range(count)]
    good = [header(), *chunks]
    assert process_runtime._receive_response(
        Frames(good), operation=operation, request_id=request_id, generation=generation, claim_id=claim_id,
    ) == {"complete": "abcdefghijk"}
    malformed = (
        ([header(), chunks[0], chunks[0]], "invalid_response"),
        ([header(), chunks[1], *chunks[1:]], "invalid_response"),
        ([header(), *chunks[:-1]], "child_eof"),
        ([header(total_bytes=len(raw) + 1), *chunks], "invalid_response"),
        ([header(request_id="0" * 32)], "invalid_response"),
        ([header(payload_sha256="sha256:" + "0" * 64), *chunks], "invalid_response"),
        ([header(), frame(0, raw[:5], chunk_b64=base64.b64encode(raw[:5]).decode("ascii") + "=")], "invalid_response"),
    )
    for values, code in malformed:
        with pytest.raises(process_runtime.PlanningProcessError) as error:
            process_runtime._receive_response(
                Frames(values), operation=operation, request_id=request_id, generation=generation, claim_id=claim_id,
            )
        _assert_error(error, code)


def test_bounded_response_total_is_rejected_before_spool_or_body_but_exact_limit_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
):
    operation = "planning_migration_status"
    request_id = "a" * 32; generation = "b" * 32; claim_id = "c" * 32
    raw_limit = process_runtime._response_total_maximum(operation)
    spool_calls = []

    def observed_spool(**kwargs):
        spool_calls.append(kwargs["max_size"])
        raise OSError("test-only admission sentinel")

    monkeypatch.setattr(process_runtime.tempfile, "SpooledTemporaryFile", observed_spool)

    def header(total_bytes: int, digest: str) -> bytes:
        return process_runtime._encode_frame({
            "protocol": process_runtime.PROTOCOL, "op": operation + "_header",
            "request_id": request_id, "generation": generation, "claim_id": claim_id,
            "total_bytes": total_bytes,
            "count": (
                total_bytes + process_runtime._CHUNK_RAW_MAX_BYTES - 1
            ) // process_runtime._CHUNK_RAW_MAX_BYTES,
            "payload_sha256": digest,
        }, maximum=process_runtime.RESPONSE_MAX_BYTES)

    class HeaderOnly:
        def __init__(self, value): self.value = value; self.body_reads = 0
        def recv_bytes(self, maxlength):
            if self.value is not None:
                value, self.value = self.value, None
                return value
            self.body_reads += 1
            raise AssertionError("bounded rejection read a body frame")

    oversized = HeaderOnly(header(raw_limit + 1, "sha256:" + "0" * 64))
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._receive_response(
            oversized, operation=operation, request_id=request_id,
            generation=generation, claim_id=claim_id,
        )
    _assert_error(error, "invalid_response")
    assert oversized.body_reads == 0 and spool_calls == []

    exact = HeaderOnly(header(raw_limit, "sha256:" + "0" * 64))
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._receive_response(
            exact, operation=operation, request_id=request_id,
            generation=generation, claim_id=claim_id,
        )
    _assert_error(error, "unavailable")
    assert exact.body_reads == 0
    assert spool_calls == [process_runtime._CHUNK_RAW_MAX_BYTES]


@pytest.mark.parametrize(
    "field",
    [
        "protocol", "op", "request_id", "generation", "claim_id", "sequence",
        "count", "total_bytes", "payload_sha256", "chunk_b64",
    ],
)
def test_each_chunk_frame_binding_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch, field: str,
):
    operation = "planning_migration_status"
    request_id = "a" * 32; generation = "b" * 32; claim_id = "c" * 32
    raw = process_runtime._encode_frame({"complete": True}, maximum=1024)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(process_runtime, "_CHUNK_RAW_MAX_BYTES", len(raw))
    header = process_runtime._encode_frame({
        "protocol": process_runtime.PROTOCOL, "op": operation + "_header",
        "request_id": request_id, "generation": generation, "claim_id": claim_id,
        "total_bytes": len(raw), "count": 1, "payload_sha256": digest,
    }, maximum=process_runtime.RESPONSE_MAX_BYTES)
    frame = {
        "protocol": process_runtime.PROTOCOL, "op": operation + "_chunk",
        "request_id": request_id, "generation": generation, "claim_id": claim_id,
        "sequence": 0, "count": 1, "total_bytes": len(raw),
        "payload_sha256": digest, "chunk_b64": base64.b64encode(raw).decode("ascii"),
    }
    frame[field] = {
        "protocol": "wrong-protocol", "op": "wrong-op", "request_id": "0" * 32,
        "generation": "0" * 32, "claim_id": "0" * 32, "sequence": 1,
        "count": True, "total_bytes": len(raw) + 1,
        "payload_sha256": "sha256:" + "0" * 64,
        "chunk_b64": frame["chunk_b64"] + "=",
    }[field]
    encoded = process_runtime._encode_frame(
        frame, maximum=process_runtime.RESPONSE_MAX_BYTES,
    )

    class Frames:
        def __init__(self): self.values = [header, encoded]
        def recv_bytes(self, maxlength): return self.values.pop(0)

    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._receive_response(
            Frames(), operation=operation, request_id=request_id,
            generation=generation, claim_id=claim_id,
        )
    _assert_error(error, "invalid_response")


def test_document_response_over_old_total_limit_roundtrips_in_bounded_frames():
    operation = "planning_get_revision_bundle"
    request_id = "a" * 32; generation = "b" * 32; claim_id = "c" * 32
    response = {
        "protocol": process_runtime.PROTOCOL, "op": operation + "_result",
        "request_id": request_id, "generation": generation, "claim_id": claim_id,
        "status": "ok", "payload": "x" * (16 * 1024 * 1024),
    }

    class Frames:
        def __init__(self): self.values = []
        def send_bytes(self, value):
            assert len(value) <= process_runtime.RESPONSE_MAX_BYTES
            self.values.append(value)
        def recv_bytes(self, maxlength):
            if not self.values: raise EOFError
            value = self.values.pop(0)
            assert len(value) <= maxlength
            return value

    frames = Frames()
    process_runtime._send_response(frames, response, operation=operation)
    first = json.loads(frames.values[0].decode("ascii"))
    assert first["op"] == operation + "_header"
    assert first["total_bytes"] > process_runtime.RESPONSE_MAX_BYTES
    assert first["count"] == (
        first["total_bytes"] + process_runtime._CHUNK_RAW_MAX_BYTES - 1
    ) // process_runtime._CHUNK_RAW_MAX_BYTES
    assert process_runtime._receive_response(
        frames, operation=operation, request_id=request_id,
        generation=generation, claim_id=claim_id,
    ) == response


@pytest.mark.parametrize(
    "operation", sorted(process_runtime._UNBOUNDED_DOCUMENT_REQUEST_OPERATIONS),
)
def test_large_request_header_is_admitted_before_body_and_failed_nonce_is_not_reusable(
    operation: str,
):
    generation = "b" * 32
    claim_id = "c" * 32
    total_bytes = 10**12
    assert total_bytes > process_runtime._response_total_maximum(operation)
    count = (total_bytes + process_runtime._CHUNK_RAW_MAX_BYTES - 1) // process_runtime._CHUNK_RAW_MAX_BYTES
    claim = {
        "owner_id": OWNER_ID, "subject_id": "large-request-subject",
        "project_id": PROJECT_ID, "auth_context_digest": AUTH_CONTEXT_DIGEST,
        "generation": generation, "claim_id": claim_id,
    }
    header = {
        "protocol": process_runtime.PROTOCOL, "op": operation + "_header",
        "request_id": "a" * 32, "generation": generation, "claim_id": claim_id,
        "deadline_ms": 10_000, "claim": claim, "total_bytes": total_bytes,
        "count": count, "payload_sha256": "sha256:" + "0" * 64,
    }
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id=ISSUER_ID, key_id=KEY_ID, secret=ISSUER_SECRET,
    )

    class NoBody:
        def __init__(self): self.reads = 0
        def recv_bytes(self, maxlength):
            self.reads += 1
            raise EOFError

    rejected = dict(header)
    rejected["protocol"] = "wrong-protocol"
    unread = NoBody()
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._child_receive_large_request(
            unread, rejected, issuer=issuer, generation=generation, seen_nonces=set(),
        )
    _assert_error(error, "invalid_request")
    assert unread.reads == 0

    seen = set()
    partial = NoBody()
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._child_receive_large_request(
            partial, header, issuer=issuer, generation=generation, seen_nonces=seen,
        )
    _assert_error(error, "child_eof")
    assert partial.reads == 1 and seen == {claim_id}
    replay = NoBody()
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._child_receive_large_request(
            replay, header, issuer=issuer, generation=generation, seen_nonces=seen,
        )
    _assert_error(error, "single_use_claim")
    assert replay.reads == 0


@pytest.mark.parametrize("failure", ["allocation", "write", "short_write", "read", "close"])
def test_chunk_spool_failures_are_content_free_and_closed(
    monkeypatch: pytest.MonkeyPatch, failure: str,
):
    raw = process_runtime._encode_frame({"complete": True}, maximum=1024)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    operation = "planning_get_revision_bundle"
    request_id = "a" * 32; generation = "b" * 32; claim_id = "c" * 32
    header = process_runtime._encode_frame({
        "protocol": process_runtime.PROTOCOL, "op": operation + "_header",
        "request_id": request_id, "generation": generation, "claim_id": claim_id,
        "total_bytes": len(raw), "count": 1, "payload_sha256": digest,
    }, maximum=process_runtime.RESPONSE_MAX_BYTES)
    chunk = process_runtime._encode_frame({
        "protocol": process_runtime.PROTOCOL, "op": operation + "_chunk",
        "request_id": request_id, "generation": generation, "claim_id": claim_id,
        "sequence": 0, "count": 1, "total_bytes": len(raw),
        "payload_sha256": digest, "chunk_b64": base64.b64encode(raw).decode("ascii"),
    }, maximum=process_runtime.RESPONSE_MAX_BYTES)

    class Frames:
        def __init__(self): self.values = [header, chunk]
        def recv_bytes(self, maxlength): return self.values.pop(0)

    close_calls = []
    if failure == "allocation":
        monkeypatch.setattr(
            process_runtime.tempfile, "SpooledTemporaryFile",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("test-only allocation")),
        )
    else:
        class BrokenSpool:
            def write(self, value):
                if failure == "write": raise OSError("test-only disk full")
                return len(value) - 1 if failure == "short_write" else len(value)
            def flush(self): return None
            def seek(self, _offset): return 0
            def read(self):
                if failure == "read": raise OSError("test-only spool read")
                return raw
            def close(self):
                close_calls.append(1)
                if failure == "close": raise OSError("test-only close")
        monkeypatch.setattr(
            process_runtime.tempfile, "SpooledTemporaryFile", lambda **_kwargs: BrokenSpool(),
        )
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._receive_response(
            Frames(), operation=operation, request_id=request_id,
            generation=generation, claim_id=claim_id,
        )
    _assert_error(error, "unavailable")
    assert close_calls == ([] if failure == "allocation" else [1])


def test_generation_spool_cleanup_failure_keeps_restart_gate_permanently_closed(
    accepted_process_fixture,
):
    client = accepted_process_fixture["client"]
    client.start()
    token = client._token
    process = client._process
    assert token is not None and process is not None

    class UncloseableSpool:
        def __init__(self): self.close_calls = 0
        def close(self):
            self.close_calls += 1
            raise OSError("test-only spool cleanup failure")

    spool = UncloseableSpool()
    assert token.track_resource(spool) is True
    assert client.cancel_inflight() is False
    _assert_reaped(process)
    assert spool.close_calls == 1
    assert token.active_resources() == (spool,)
    assert client._permanently_unavailable is True
    assert client._reaping is True and client._reaping_token is token
    assert client._process is None and client._connection is None
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        client.start()
    _assert_error(error, "permanently_unavailable")


def test_child_revert_receipt_verifier_rejects_self_consistent_forged_digest_before_response(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    store = fixture["store"]
    issuer = fixture["issuer"]
    principal = fixture["principal"]
    roadmap_id = "planning-process-forged-revert-roadmap"
    payload = _durable_definition_payload(roadmap_id, 1, "Forged revert receipt baseline")
    created = store.create_project(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        payload_bytes=payload, idempotency_key="planning-process-forged-create-0001",
        expected_generation=0,
    )
    forged_receipts = []
    store_type = type(store)
    original_revert = store_type.revert_head
    forged_digest = "sha256:" + "f" * 64
    forged_receipt_id = "planning-receipt:" + hashlib.sha256(
        (forged_digest + ":receipt").encode("ascii")
    ).hexdigest()

    def forged_revert(actual_store, actual_principal, **kwargs):
        receipt = original_revert(actual_store, actual_principal, **kwargs)
        forged = replace(receipt, input_digest=forged_digest, receipt_id=forged_receipt_id)
        forged_receipts.append(forged)
        return forged

    monkeypatch.setattr(store_type, "revert_head", forged_revert)
    with pytest.raises(process_runtime.PlanningProcessError) as error:
        process_runtime._child_durable_operation(
            "planning_revert_head",
            {
                "project_id": PROJECT_ID,
                "roadmap_id": roadmap_id,
                "ancestor_revision_id": created.new_revision_id,
                "expected_head": _head_command_projection(created.after_head),
                "idempotency_key": "planning-process-forged-revert-0001",
            },
            principal=principal, owner_id=OWNER_ID, claim_project_id=PROJECT_ID,
            durable_store=store, issuer=issuer,
        )
    assert error.value.code == "invalid_response"
    assert len(forged_receipts) == 1
    forged = forged_receipts[0]
    assert forged.input_digest == forged_digest
    assert forged.receipt_id == forged_receipt_id
    actual_head = store.get_head(
        principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
    )
    assert actual_head.generation == created.after_head.generation + 1
    assert actual_head.current_revision_id == forged.new_revision_id
    expected_reverted_payload = _durable_definition_payload(
        roadmap_id, 2, "Forged revert receipt baseline",
    )
    assert store.read_payload(
        principal, owner_id=OWNER_ID, revision_id=forged.new_revision_id,
    ) == expected_reverted_payload


def test_real_mutation_cancel_and_close_are_unknown_and_manual_retry_advances_once(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    principal = fixture["principal"]
    client.start()
    for mode in ("cancel", "close"):
        if client._process is None:
            client.start()
        roadmap_id = f"planning-process-{mode}-roadmap"
        first_payload = _durable_definition_payload(roadmap_id, 1, f"{mode} baseline")
        create_command = {
            "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id, "expected_generation": 0,
            "payload_sha256": "sha256:" + hashlib.sha256(first_payload).hexdigest(),
            "principal_identity": _principal_identity(fixture),
        }
        first_revision_id = _independent_revision_id(
            _independent_canonical_digest(create_command), 1,
        )
        created = client.planning_create_project(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=first_payload, idempotency_key=f"planning-process-{mode}-create-0001",
            expected_generation=0, timeout_seconds=10,
        )
        _assert_durable_write_matches_store(
            fixture, created, principal, roadmap_id, command=create_command,
            before_head=None, new_revision_id=first_revision_id, restored_revision_id=None,
        )
        before = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        append_key = f"planning-process-{mode}-append-0001"
        append_payload = _durable_definition_payload(roadmap_id, 2, f"{mode} retry")
        append_command = {
            "kind": "append_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id, "expected_head": _head_command_projection(created.head),
            "payload_sha256": "sha256:" + hashlib.sha256(append_payload).hexdigest(),
            "principal_identity": _principal_identity(fixture),
        }
        expected_revision_id = _independent_revision_id(
            _independent_canonical_digest(append_command), 2,
        )
        lock_process = None
        worker = None
        result = {}
        try:
            lock_process, _release_lock = _spawn_lock_holder(fixture["durable_root"])
            request_sent, requests = _observe_requests(
                monkeypatch, client, operation="planning_append_revision",
            )
            claim = _new_operation_claim(fixture)
            old_process = client._process

            def append_while_locked():
                try:
                    result["value"] = client.planning_append_revision(
                        claim, project_id=PROJECT_ID, roadmap_id=roadmap_id,
                        payload_bytes=append_payload, expected_head=created.head,
                        idempotency_key=append_key, timeout_seconds=10,
                    )
                except process_runtime.PlanningProcessOutcomeUnknown as error:
                    result["error"] = error

            worker = Thread(target=append_while_locked)
            worker.start()
            assert request_sent.wait(10)
            assert len(requests) == 1
            if mode == "cancel":
                assert client.cancel_inflight() is True
            else:
                client.close()
            _assert_reaped(old_process)
            worker.join(10)
            assert not worker.is_alive()
            assert isinstance(result.get("error"), process_runtime.PlanningProcessOutcomeUnknown)
            assert len(requests) == 1
            assert client._process is None and client._connection is None

            lock_process.terminate()
            lock_process.join(10)
            _assert_reaped(lock_process)
            lock_process = None
            assert fixture["store"].get_head(
                principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
            ) == before

            client.start()
            explicit_read = client.planning_get_head(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                timeout_seconds=10,
            )
            _assert_durable_head_matches_store(explicit_read, before)
            retried = client.planning_append_revision(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                payload_bytes=append_payload, expected_head=created.head,
                idempotency_key=append_key, timeout_seconds=10,
            )
            _assert_durable_write_matches_store(
                fixture, retried, principal, roadmap_id, command=append_command,
                before_head=created.head, new_revision_id=expected_revision_id,
                restored_revision_id=None,
            )
            replayed = client.planning_append_revision(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                payload_bytes=append_payload, expected_head=created.head,
                idempotency_key=append_key, timeout_seconds=10,
            )
            assert replayed.receipt == retried.receipt
            assert replayed.head == retried.head
            final = fixture["store"].get_head(
                principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
            )
            assert final.generation == before.generation + 1
            assert final.current_revision_id == expected_revision_id
        finally:
            if lock_process is not None:
                if lock_process.is_alive():
                    lock_process.terminate()
                lock_process.join(10)
                _assert_reaped(lock_process)
            if worker is not None and worker.is_alive():
                worker.join(10)


def test_real_handoff_send_failure_and_late_invalid_success_are_unknown_then_same_key_retry_replays(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    fixture = accepted_process_fixture
    client = fixture["client"]
    principal = fixture["principal"]
    client.start()
    connection_type = type(client._connection)
    original_send = connection_type.send_bytes
    original_receive = connection_type.recv_bytes
    fault = {"mode": None, "recording": False, "requests": []}

    def injected_send(connection, raw, *args, **kwargs):
        request = json.loads(raw.decode("utf-8")) if connection is client._connection else None
        if request is not None and request.get("op") == "planning_append_revision" and fault["recording"]:
            fault["requests"].append(request)
        result = original_send(connection, raw, *args, **kwargs)
        if (request is not None and request.get("op") == "planning_append_revision"
                and fault["mode"] == "send_failure_after_handoff"):
            response = original_receive(connection, maxlength=process_runtime.RESPONSE_MAX_BYTES)
            acknowledged = json.loads(response.decode("utf-8"))
            assert acknowledged["op"] == "planning_append_revision_result"
            assert acknowledged["status"] == "ok"
            fault["send_failure_after_ack"] = True
            raise OSError("injected transport failure after child success response")
        return result

    def injected_receive(connection, *args, **kwargs):
        raw = original_receive(connection, *args, **kwargs)
        if connection is client._connection and fault["mode"] == "late_invalid_response":
            response = json.loads(raw.decode("utf-8"))
            if response.get("op") == "planning_append_revision_result":
                response["request_id"] = "0" * 32
                fault["late_response_mutated"] = True
                return json.dumps(
                    response, ensure_ascii=True, allow_nan=False,
                    separators=(",", ":"), sort_keys=True,
                ).encode("ascii")
        return raw

    monkeypatch.setattr(connection_type, "send_bytes", injected_send)
    monkeypatch.setattr(connection_type, "recv_bytes", injected_receive)
    for mode in ("send_failure_after_handoff", "late_invalid_response"):
        if client._process is None:
            client.start()
        roadmap_id = f"planning-process-{mode}-roadmap"
        first_payload = _durable_definition_payload(roadmap_id, 1, f"{mode} baseline")
        create_command = {
            "kind": "create_project", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id, "expected_generation": 0,
            "payload_sha256": "sha256:" + hashlib.sha256(first_payload).hexdigest(),
            "principal_identity": _principal_identity(fixture),
        }
        first_revision_id = _independent_revision_id(
            _independent_canonical_digest(create_command), 1,
        )
        created = client.planning_create_project(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=first_payload, idempotency_key=f"planning-process-{mode}-create-0001",
            expected_generation=0, timeout_seconds=10,
        )
        _assert_durable_write_matches_store(
            fixture, created, principal, roadmap_id, command=create_command,
            before_head=None, new_revision_id=first_revision_id, restored_revision_id=None,
        )
        before = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        append_payload = _durable_definition_payload(roadmap_id, 2, f"{mode} committed append")
        append_key = f"planning-process-{mode}-append-0001"
        append_command = {
            "kind": "append_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id, "expected_head": _head_command_projection(created.head),
            "payload_sha256": "sha256:" + hashlib.sha256(append_payload).hexdigest(),
            "principal_identity": _principal_identity(fixture),
        }
        expected_revision_id = _independent_revision_id(
            _independent_canonical_digest(append_command), 2,
        )
        fault["mode"] = mode
        fault["recording"] = True
        fault["send_failure_after_ack"] = False
        fault["late_response_mutated"] = False
        fault["requests"] = []
        old_process = client._process
        with pytest.raises(process_runtime.PlanningProcessOutcomeUnknown) as error:
            client.planning_append_revision(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                payload_bytes=append_payload, expected_head=created.head,
                idempotency_key=append_key, timeout_seconds=10,
            )
        assert error.value.code == "outcome_unknown"
        assert len(fault["requests"]) == 1
        _assert_reaped(old_process)
        assert client._process is None and client._connection is None
        if mode == "send_failure_after_handoff":
            assert fault["send_failure_after_ack"] is True
        else:
            assert fault["late_response_mutated"] is True
        committed = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        assert committed.generation == before.generation + 1
        assert committed.current_revision_id == expected_revision_id

        # Readback is explicit after uncertainty. The only retry is the following call.
        fault["mode"] = None
        fault["recording"] = False
        client.start()
        fresh = client.planning_get_head(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            timeout_seconds=10,
        )
        _assert_durable_head_matches_store(fresh, committed)
        fault["recording"] = True
        replay = client.planning_append_revision(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=append_payload, expected_head=created.head,
            idempotency_key=append_key, timeout_seconds=10,
        )
        _assert_durable_write_matches_store(
            fixture, replay, principal, roadmap_id, command=append_command,
            before_head=created.head, new_revision_id=expected_revision_id,
            restored_revision_id=None,
        )
        assert len(fault["requests"]) == 2
        assert replay.head.generation == before.generation + 1
        assert fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        ) == committed
    fault["mode"] = None
    fault["recording"] = False


def test_approve_and_revert_committed_unknown_recover_only_with_the_same_key(
    accepted_process_fixture, monkeypatch: pytest.MonkeyPatch,
):
    """A lost successful child response must not turn a same-key retry stale."""
    fixture = accepted_process_fixture
    client = fixture["client"]
    principal = fixture["principal"]
    client.start()
    connection_type = type(client._connection)
    original_send = connection_type.send_bytes
    original_receive = connection_type.recv_bytes
    fault = {"operation": None, "requests": []}

    def lose_successful_response(connection, raw, *args, **kwargs):
        request = json.loads(raw.decode("utf-8")) if connection is client._connection else None
        if request is not None and request.get("op") == fault["operation"]:
            fault["requests"].append(request)
            result = original_send(connection, raw, *args, **kwargs)
            response = original_receive(connection, maxlength=process_runtime.RESPONSE_MAX_BYTES)
            acknowledged = json.loads(response.decode("utf-8"))
            assert acknowledged["op"] == f"{fault['operation']}_result"
            assert acknowledged["status"] == "ok"
            raise OSError("injected transport failure after child success response")
        return original_send(connection, raw, *args, **kwargs)

    monkeypatch.setattr(connection_type, "send_bytes", lose_successful_response)

    for operation in ("planning_approve_revision", "planning_revert_head"):
        if client._process is None:
            client.start()
        roadmap_id = f"planning-process-{operation}-same-key-roadmap"
        first_payload = _durable_definition_payload(roadmap_id, 1, "same-key baseline")
        created = client.planning_create_project(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=first_payload,
            idempotency_key=f"{operation}-same-key-create-0001",
            expected_generation=0, timeout_seconds=10,
        )
        append_payload = _durable_definition_payload(roadmap_id, 2, "same-key candidate")
        append_command = {
            "kind": "append_revision", "owner_id": OWNER_ID, "project_id": PROJECT_ID,
            "roadmap_id": roadmap_id, "expected_head": _head_command_projection(created.head),
            "payload_sha256": "sha256:" + hashlib.sha256(append_payload).hexdigest(),
            "principal_identity": _principal_identity(fixture),
        }
        candidate_revision_id = _independent_revision_id(
            _independent_canonical_digest(append_command), 2,
        )
        appended = client.planning_append_revision(
            _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
            payload_bytes=append_payload, expected_head=created.head,
            idempotency_key=f"{operation}-same-key-append-0001", timeout_seconds=10,
        )
        if operation == "planning_approve_revision":
            expected_head = appended.head
            target_id = candidate_revision_id
            invoke = lambda key: client.planning_approve_revision(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                revision_id=target_id, expected_head=expected_head,
                idempotency_key=key, timeout_seconds=10,
            )
        else:
            approved = client.planning_approve_revision(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                revision_id=candidate_revision_id, expected_head=appended.head,
                idempotency_key=f"{operation}-same-key-setup-approve-0001", timeout_seconds=10,
            )
            expected_head = approved.head
            target_id = created.head.current_revision_id
            invoke = lambda key: client.planning_revert_head(
                _new_operation_claim(fixture), project_id=PROJECT_ID, roadmap_id=roadmap_id,
                ancestor_revision_id=target_id, expected_head=expected_head,
                idempotency_key=key, timeout_seconds=10,
            )

        before = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        key = f"{operation}-same-key-recover-0001"
        fault["operation"] = operation
        fault["requests"] = []
        parked_process = client._process
        with pytest.raises(process_runtime.PlanningProcessOutcomeUnknown) as error:
            invoke(key)
        assert error.value.code == "outcome_unknown"
        assert len(fault["requests"]) == 1
        _assert_reaped(parked_process)
        assert client._process is None and client._connection is None
        committed = fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        )
        assert committed.generation == before.generation + 1

        # The caller explicitly restarts and retries.  No automatic replay occurs.
        fault["operation"] = None
        client.start()
        replay = invoke(key)
        replay_again = invoke(key)
        assert replay.receipt == replay_again.receipt
        assert replay.head == replay_again.head
        _assert_durable_head_matches_store(replay.head, committed)
        assert fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        ) == committed

        # A fresh key keeps the old expected head and must still fail the locked CAS.
        with pytest.raises(process_runtime.PlanningProcessStoreError) as stale:
            invoke(f"{operation}-same-key-fresh-stale-0001")
        assert stale.value.code == "stale_authority"
        assert fixture["store"].get_head(
            principal, owner_id=OWNER_ID, project_id=PROJECT_ID, roadmap_id=roadmap_id,
        ) == committed
    fault["operation"] = None
