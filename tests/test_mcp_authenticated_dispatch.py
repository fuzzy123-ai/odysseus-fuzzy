import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import src.mcp_authenticated_dispatch as authenticated_dispatch
from src.mcp_authenticated_dispatch import (
    AppOwnedPlanningRuntime,
    authenticate_mcp_request,
    MCP_BATCH_MAX,
    MCP_BODY_MAX_BYTES,
    McpAuthenticatedIdentity,
    McpBearerProof,
    McpDispatchFailure,
    _validate_accepted_definition,
    _validate_projection,
    dispatch_http_payload,
    capture_mcp_bearer_proof,
    parse_json_body,
)
from src.planning_revision_store import PlanningRevisionStore


_IDENTITY = McpAuthenticatedIdentity("token-id", "admin", "subject-id", 4, 9)


def _rpc(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def _refresh_context_payload_bytes(payload):
    payload["payload_bytes"] = 0
    for _ in range(3):
        payload["payload_bytes"] = len(json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=True,
        ).encode("utf-8", "strict"))


class _Runtime:
    def __init__(self, result=None, dispatch=None):
        self.result = result or {
            "schema": "odysseus.planning.roadmap_list.v1",
            "read_only": True,
            "writes_supported": False,
            "roadmaps": [],
            "summary": {},
        }
        self._dispatch = dispatch
        self.calls = 0

    def dispatch(self, name, arguments, identity):
        self.calls += 1
        if self._dispatch:
            return self._dispatch(name, arguments, identity)
        return {
            "schema": "odysseus.mcp.tool_result.v1",
            "tool": name,
            "origin": {"kind": "repository_projection", "source_of_truth": False},
            "payload": self.result,
        }


def test_parse_rejects_duplicate_members_and_non_json_constants():
    with pytest.raises(McpDispatchFailure):
        parse_json_body(b'{"jsonrpc":"2.0","jsonrpc":"1.0"}')
    with pytest.raises(McpDispatchFailure):
        parse_json_body(b'{"value":NaN}')


def test_bearer_proof_is_exact_sealed_immutable_and_redacted():
    proof = capture_mcp_bearer_proof(SimpleNamespace(
        state=SimpleNamespace(api_token=True),
        headers={"authorization": "Bearer ody_private_fixture"},
    ))
    assert type(proof) is McpBearerProof and proof._is_issued() is True
    assert repr(proof) == "<McpBearerProof redacted>"
    with pytest.raises(AttributeError):
        proof._McpBearerProof__raw = b"replacement"
    with pytest.raises(ValueError):
        McpBearerProof(b"ody_private_fixture", _issuer=object())
    with pytest.raises(TypeError):
        class ForgedProof(McpBearerProof):
            pass


def test_parser_and_batch_enforce_transport_budgets():
    with pytest.raises(McpDispatchFailure) as body_error:
        parse_json_body(b" " * (MCP_BODY_MAX_BYTES + 1))
    assert body_error.value.code == "budget_exceeded"
    nested = b'{"value":' + b"[" * 17 + b"0" + b"]" * 17 + b"}"
    with pytest.raises(McpDispatchFailure) as depth_error:
        parse_json_body(nested)
    assert depth_error.value.code == "budget_exceeded"

    async def scenario():
        with pytest.raises(McpDispatchFailure) as batch_error:
            await dispatch_http_payload(
                [_rpc("ping", {}, index) for index in range(MCP_BATCH_MAX + 1)],
                identity=_IDENTITY,
                runtime=_Runtime(),
                started=time.monotonic(),
            )
        assert batch_error.value.code == "budget_exceeded"

    asyncio.run(scenario())


def test_projection_adapter_requires_known_readonly_contract():
    valid = {
        "schema": "odysseus.planning.graph_summary.v1",
        "read_only": True,
        "writes_performed": False,
        "roadmap_ref": {"project_id": "project-1", "roadmap_id": "roadmap-1", "derived": False},
        "depth": 1,
        "nodes": [],
        "edges": [],
        "summary": {"nodes": 0, "edges": 0, "clipped": False},
        "source_refs": [],
        "raw_content_included": False,
        "absolute_paths_visible": False,
    }
    _validate_projection("planning_graph_summary", valid)
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_graph_summary", {**valid, "writes_performed": True})
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_graph_summary", {**valid, "schema": "foreign.payload.v1"})
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_graph_summary", {**valid, "provider_response": "private"})
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_graph_summary", {**valid, "nodes": [{"id": "n", "kind": "roadmap", "label": "x", "raw": "body"}]})
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_graph_summary", {**valid, "source_refs": ["C:\\private\\file"]})


def test_planning_service_five_repository_projections_match_typed_contract(tmp_path):
    roadmap = {
        "schema_version": 1,
        "kind": "harbor.planning.roadmap",
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "revision": 1,
        "title": "Core roadmap",
        "goal": "Exercise bounded repository projections.",
        "status": "running",
        "source_refs": ["src/planning_mcp_service.py"],
        "slices": [{"id": "slice-one", "title": "One", "objective": "Read safely.", "status": "ready"}],
        "gates": [{"id": "gate-one", "decision_needed": "Keep the read bounded.", "blocks": ["slice-one"]}],
    }
    source = tmp_path / "docs" / "plans" / "core-roadmap.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(roadmap), encoding="utf-8")
    from mcp_servers.planning_server import call_planning_tool_contract

    calls = (
        ("planning_list_roadmaps", {"limit": 5}),
        ("planning_read_roadmap", {"source_id_or_path": "docs/plans/core-roadmap.json"}),
        ("planning_search_roadmaps", {"query": "core roadmap", "limit": 5}),
        ("planning_get_context_pack", {"roadmap_ref": "docs/plans/core-roadmap.json", "task": "Review the plan."}),
        ("planning_graph_summary", {"roadmap_ref": "docs/plans/core-roadmap.json", "depth": 2, "limit": 20}),
    )
    projections = {}
    for name, arguments in calls:
        projection = call_planning_tool_contract(
            name, arguments, repo_root=tmp_path, preview_chars=0,
        )
        _validate_projection(name, projection)
        projections[name] = projection
    assert projections["planning_list_roadmaps"]["roadmaps"][0]["preview"] == ""
    assert projections["planning_read_roadmap"]["source"]["preview"] == ""
    assert projections["planning_search_roadmaps"]["results"][0]["preview"] == ""
    runtime_projection = AppOwnedPlanningRuntime(
        repo_root=str(tmp_path),
        legacy_store=PlanningRevisionStore([]),
        durable_store=None,
        issuer=None,
    ).dispatch("planning_list_roadmaps", {"limit": 5}, _IDENTITY)
    assert runtime_projection["payload"]["roadmaps"][0]["preview"] == ""
    context_json_size = len(json.dumps(
        projections["planning_get_context_pack"], ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", "strict"))
    assert projections["planning_get_context_pack"]["payload_bytes"] == context_json_size

    multibyte_context = call_planning_tool_contract(
        "planning_get_context_pack",
        {"roadmap_ref": "docs/plans/core-roadmap.json", "task": "é" * 500},
        repo_root=tmp_path,
        preview_chars=0,
    )
    assert len(multibyte_context["task"]) == 500
    assert len(multibyte_context["task"].encode("utf-8")) > 500
    _validate_projection("planning_get_context_pack", multibyte_context)

    clipped_roadmap = deepcopy(roadmap)
    clipped_roadmap["slices"] = [
        {"id": "duplicate-slice", "title": f"Slice {index}", "objective": "Bounded validation issue."}
        for index in range(200)
    ]
    source.write_text(json.dumps(clipped_roadmap), encoding="utf-8")
    from src.planning_mcp_service import PlanningMcpService
    clipped_context = PlanningMcpService(tmp_path, preview_chars=0, context_budget_bytes=4_096).get_context_pack(
        "docs/plans/core-roadmap.json", task="A" * 500, max_items=24,
    )
    assert clipped_context["clipped"] is True
    assert clipped_context["validation"].get("details_truncated") is True
    assert "errors" in clipped_context["validation"] and "warnings" in clipped_context["validation"]
    _validate_projection("planning_get_context_pack", clipped_context)

    cases = []
    bad = deepcopy(projections["planning_list_roadmaps"])
    bad["roadmaps"][0]["size_bytes"] = True
    cases.append(("planning_list_roadmaps", bad))
    bad = deepcopy(projections["planning_list_roadmaps"])
    bad["roadmaps"][0]["source_id"] = "x" * 121
    cases.append(("planning_list_roadmaps", bad))
    bad = deepcopy(projections["planning_list_roadmaps"])
    bad["summary"]["returned"] = True
    cases.append(("planning_list_roadmaps", bad))
    bad = deepcopy(projections["planning_read_roadmap"])
    bad["roadmap"]["revision"] = True
    cases.append(("planning_read_roadmap", bad))
    bad = deepcopy(projections["planning_read_roadmap"])
    bad["slices"] = bad["slices"] * 25
    cases.append(("planning_read_roadmap", bad))
    bad = deepcopy(projections["planning_read_roadmap"])
    bad["validation"]["summary"]["errors"] = 1
    cases.append(("planning_read_roadmap", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    bad["roadmap_lens"]["nodes"] = ()
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    bad["memory_summary"]["candidates"] = True
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    bad["dependency_hints"] = {}
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    lens = bad["roadmap_lens"]
    if not lens["available"]:
        for key in ("reason", "slice_count", "gate_count", "dependency_count"):
            lens.pop(key)
        lens.update({"available": True, "status": "complete", "active_node_id": ""})
    lens["nodes"] = [{"node_id": "node-one", "label": "Node", "node_type": "slice", "score": math.nan}]
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    bad["memory"] = [{"preview": "private memory body"}]
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    bad["memory_included"] = True
    bad["memory_source"] = "repo_source_builder"
    bad["memory_summary"].update({"requested": True, "source": "repo_source_builder"})
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_get_context_pack"])
    bad["roadmap_lens"]["nodes"] = [{"node_id": "n" * 81, "label": "Node", "node_type": "slice", "score": 0.5}]
    cases.append(("planning_get_context_pack", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["summary"]["nodes"] += 1
    cases.append(("planning_graph_summary", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["nodes"] = tuple(bad["nodes"])
    cases.append(("planning_graph_summary", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["depth"] = 4
    cases.append(("planning_graph_summary", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["source_refs"] = ["docs/plans/core-roadmap.json"] * 25
    cases.append(("planning_graph_summary", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["source_refs"] = ["docs/plans/%2e%2e/private.json"]
    cases.append(("planning_graph_summary", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["source_refs"] = ["docs/plans/．．/private.json"]
    cases.append(("planning_graph_summary", bad))
    bad = deepcopy(projections["planning_graph_summary"])
    bad["source_refs"] = ("docs/plans/core-roadmap.json",)
    cases.append(("planning_graph_summary", bad))
    for name, projection in cases:
        if name == "planning_get_context_pack":
            _refresh_context_payload_bytes(projection)
        with pytest.raises(McpDispatchFailure):
            _validate_projection(name, projection)

    bad_bytes = deepcopy(projections["planning_get_context_pack"])
    bad_bytes["payload_bytes"] += 1
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_get_context_pack", bad_bytes)
    bad_bytes = deepcopy(projections["planning_get_context_pack"])
    bad_bytes["payload_bytes"] = "unknown"
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_get_context_pack", bad_bytes)

    fallback_validation = deepcopy(projections["planning_get_context_pack"])
    fallback_validation["validation"].pop("errors")
    fallback_validation["validation"].pop("warnings")
    fallback_validation["validation"]["details_truncated"] = True
    _refresh_context_payload_bytes(fallback_validation)
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_get_context_pack", fallback_validation)


def test_accepted_handoff_is_hash_pinned_and_never_authoritative():
    handoff = {
        "schema_id": "odysseus.agent.plan_handoff.v1",
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "revision": 7,
        "content_hash": "sha256:" + "a" * 64,
        "title": "Approved roadmap",
        "requested_entrypoint": "/abc",
        "composer_text": "/abc run roadmap:core-map@7 hash:sha256:" + "a" * 64,
        "launch_authorized": False,
        "read_only": True,
    }
    _validate_accepted_definition("planning_create_agent_handoff", {
        "project_id": "demo-project", "roadmap_id": "core-map",
    }, handoff)
    assert handoff["launch_authorized"] is False


def test_accepted_gate_definition_rejects_unrecognized_nested_authority_fields():
    payload = {
        "schema_id": "odysseus.planning.gate_definitions.v2",
        "project_id": "demo-project", "roadmap_id": "core-map", "revision": 7,
        "content_hash": "sha256:" + "b" * 64, "node_id": "", "gate_definitions": [{
            "gate_id": "gate-a", "kind": "repo", "title": "Review", "blocks": [],
            "decision_needed": "Approve the bounded read.", "safe_default": "Keep blocked.",
            "approval_scope_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "required_verification_rule_ids": [], "source_of_truth": True,
        }], "read_only": True, "writes_performed": False,
    }
    with pytest.raises(McpDispatchFailure):
        _validate_accepted_definition("planning_read_gate_definitions", {
            "project_id": "demo-project", "roadmap_id": "core-map",
        }, payload)


@pytest.mark.parametrize("field,value", [("blocks", [123]), ("required_verification_rule_ids", [False])])
def test_accepted_gate_definition_rejects_non_identifier_array_values(field, value):
    payload = {
        "schema_id": "odysseus.planning.gate_definitions.v2",
        "project_id": "demo-project", "roadmap_id": "core-map", "revision": 1,
        "content_hash": "sha256:" + "b" * 64, "node_id": "", "gate_definitions": [{
            "gate_id": "gate-a", "kind": "repo", "title": "Review", "blocks": [],
            "decision_needed": "Approve the bounded read.", "safe_default": "Keep blocked.",
            "approval_scope_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "required_verification_rule_ids": [],
        }], "read_only": True, "writes_performed": False,
    }
    payload["gate_definitions"][0][field] = value
    with pytest.raises(McpDispatchFailure):
        _validate_accepted_definition("planning_read_gate_definitions", {
            "project_id": "demo-project", "roadmap_id": "core-map",
        }, payload)


def test_accepted_definition_dispatch_keeps_durable_provenance(monkeypatch, tmp_path):
    content_hash_holder = {}
    class Issuer:
        def issue(self, **kwargs):
            assert kwargs["subject_id"] == _IDENTITY.owner_subject_id
            return object()

    class DurableStore:
        def _fixtures(self, project_id):
            assert project_id == "demo-project"
            definition = {
                "schema_version": 1,
                "kind": "harbor.planning.roadmap",
                "project_id": project_id,
                "roadmap_id": "core-map",
                "revision": 1,
                "created_at": "2026-07-15T06:00:00Z",
                "updated_at": "2026-07-15T06:00:00Z",
                "title": "Core Planning Roadmap",
                "goal": "Expose immutable Planning intent.",
                "status": "approved",
                "slice_queue": [{
                    "id": "definition-transport",
                    "title": "Definition transport",
                    "objective": "Expose immutable Planning intent.",
                    "status": "running",
                    "depends_on": [],
                    "gate_ids": ["definition-go"],
                }],
                "gate_queue": [{
                    "id": "definition-go",
                    "class": "repo",
                    "state": "blocked",
                    "decision": "synthetic-runtime-decision",
                    "decision_needed": "Confirm the definition-only transport.",
                    "safe_default": "Keep the transport read-only.",
                    "blocks": ["definition-transport"],
                }],
                "source_refs": ["mcp_servers/planning_server.py"],
            }
            record = ("fuzzy", definition, "durable:planning-revision:revision-1")
            indexed = PlanningRevisionStore([record])
            accepted = indexed.get_roadmap(
                "fuzzy", project_id, "core-map", revision=1,
            )["roadmap"]
            content_hash = accepted["content_hash"]
            content_hash_holder["value"] = content_hash
            binding = {
                "owner_id": "fuzzy",
                "project_id": project_id,
                "roadmap_id": "core-map",
                "accepted_revision_id": "planning-revision:revision-1",
                "accepted_revision": 1,
                "accepted_content_sha256": content_hash,
                "current_revision_id": "planning-revision:revision-1",
                "current_revision": 1,
                "current_content_sha256": content_hash,
                "approved_revision_id": "planning-revision:revision-1",
                "approved_revision": 1,
                "approved_content_sha256": content_hash,
            }
            return [record], [binding]

    durable = DurableStore()
    records, head_bindings = durable._fixtures("demo-project")
    durable.read_subject_bound_accepted_migration_records = lambda principal, *, project_id: (
        "fuzzy", object(), records, head_bindings,
    )

    output = {
        "schema_id": "odysseus.planning.gate_definitions.v2",
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "revision": 1,
        "content_hash": "",
        "node_id": "",
        "gate_definitions": [],
        "read_only": True,
        "writes_performed": False,
    }

    def adapter(name, arguments, **kwargs):
        assert isinstance(kwargs["definition_store"], PlanningRevisionStore)
        if name in {"planning_read_gate_definitions", "planning_create_agent_handoff"}:
            assert kwargs["definition_owner"] == "fuzzy"
        if name == "planning_read_gate_definitions":
            return {**output, "content_hash": content_hash_holder["value"]}
        return {
            "schema_id": "odysseus.agent.plan_handoff.v1",
            "project_id": "demo-project",
            "roadmap_id": "core-map",
            "revision": 1,
            "content_hash": content_hash_holder["value"],
            "title": "Approved roadmap",
            "requested_entrypoint": "/abc",
            "composer_text": "/abc run roadmap:core-map@1 hash:" + content_hash_holder["value"],
            "launch_authorized": False,
            "read_only": True,
        }

    monkeypatch.setattr("mcp_servers.planning_server.call_planning_tool_contract", adapter)
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(tmp_path),
        legacy_store=PlanningRevisionStore([]),
        durable_store=durable,
        issuer=Issuer(),
    )
    result = runtime.dispatch(
        "planning_read_gate_definitions",
        {"project_id": "demo-project", "roadmap_id": "core-map"},
        _IDENTITY,
    )
    assert result["origin"] == {
        "kind": "accepted_durable_definition",
        "source_of_truth": True,
        "derived_from_accepted_durable": False,
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "revision": 1,
        "content_hash": content_hash_holder["value"],
    }
    handoff = runtime.dispatch(
        "planning_create_agent_handoff",
        {"project_id": "demo-project", "roadmap_id": "core-map"},
        _IDENTITY,
    )
    assert handoff["origin"]["derived_from_accepted_durable"] is True
    assert handoff["origin"]["source_of_truth"] is False
    assert handoff["payload"]["launch_authorized"] is False


def test_missing_accepted_batch_fails_closed_without_legacy_fallback(monkeypatch, tmp_path):
    class Issuer:
        def issue(self, **kwargs):
            return object()

    class DurableStore:
        def read_subject_bound_accepted_migration_records(self, *_args, **_kwargs):
            from src.planning_revision_store import DurablePlanningStoreError
            raise DurablePlanningStoreError("migration_required")

    def forbidden_adapter(*_args, **_kwargs):
        raise AssertionError("legacy or generic adapter reached")

    monkeypatch.setattr("mcp_servers.planning_server.call_planning_tool_contract", forbidden_adapter)
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(tmp_path),
        legacy_store=PlanningRevisionStore([]),
        durable_store=DurableStore(),
        issuer=Issuer(),
    )
    with pytest.raises(McpDispatchFailure) as error:
        runtime.dispatch(
            "planning_read_gate_definitions",
            {"project_id": "project-1", "roadmap_id": "roadmap-1"},
            _IDENTITY,
        )
    assert error.value.code == "planning_unavailable"


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_worker_is_joined_before_native_cancellation_propagates(cancel_count):
    async def scenario():
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def dispatch(_name, _arguments, _identity):
            started.set()
            release.wait(timeout=5)
            finished.set()
            return {"schema": "odysseus.mcp.tool_result.v1"}

        runtime = _Runtime(dispatch=dispatch)
        task = asyncio.create_task(dispatch_http_payload(
            _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}),
            identity=_IDENTITY,
            runtime=runtime,
            started=time.monotonic(),
        ))
        assert await asyncio.to_thread(started.wait, 2)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(scenario())


def test_planning_dispatch_does_not_import_inbox_types_in_a_fresh_blocked_process():
    child = r'''
import asyncio
import builtins
import threading
import time

real_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "src.harbour_inbox_service" or name.startswith("src.harbour_inbox_service."):
        raise ImportError("inbox imports are blocked")
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

import src.mcp_authenticated_dispatch as dispatch
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity, dispatch_http_payload

identity = McpAuthenticatedIdentity("token-id", "admin", "subject-id", 4, 9)
request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
    "name": "planning_list_roadmaps", "arguments": {},
}}
payload = {"schema": "odysseus.mcp.tool_result.v1", "tool": "planning_list_roadmaps",
    "origin": {"kind": "repository_projection", "source_of_truth": False},
    "payload": {"schema": "odysseus.planning.roadmap_list.v1", "read_only": True,
        "writes_supported": False, "roadmaps": [], "summary": {}},
}

class Runtime:
    def __init__(self, mode):
        self.mode = mode
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
    def dispatch(self, *_args):
        self.started.set()
        if self.mode == "failure":
            raise RuntimeError("fixed planning failure")
        if self.mode == "timeout":
            time.sleep(.02)
        elif self.mode == "cancel":
            assert self.release.wait(2)
        self.finished.set()
        return payload

async def scenario():
    success = await dispatch_http_payload(request, identity=identity, runtime=Runtime("success"),
        started=time.monotonic())
    assert success["result"]["isError"] is False
    failure = await dispatch_http_payload(request, identity=identity, runtime=Runtime("failure"),
        started=time.monotonic())
    assert failure["error"] == {"code": -32011, "message": "Planning read failed"}
    original_budget = dispatch.MCP_TOTAL_BUDGET_SECONDS
    dispatch.MCP_TOTAL_BUDGET_SECONDS = .001
    try:
        timed = Runtime("timeout")
        timeout = await dispatch_http_payload(request, identity=identity, runtime=timed,
            started=time.monotonic())
        assert timeout["error"] == {"code": -32013, "message": "Request timed out"}
        assert timed.finished.is_set()
    finally:
        dispatch.MCP_TOTAL_BUDGET_SECONDS = original_budget
    cancelled = Runtime("cancel")
    task = asyncio.create_task(dispatch_http_payload(request, identity=identity, runtime=cancelled,
        started=time.monotonic()))
    assert await asyncio.to_thread(cancelled.started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    cancelled.release.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation was not reraised")
    assert cancelled.finished.is_set()

asyncio.run(scenario())
'''
    completed = subprocess.run(
        [sys.executable, "-B", "-c", child],
        cwd=str(__file__.replace("\\tests\\test_mcp_authenticated_dispatch.py", "")),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0


def test_unavailable_inbox_import_returns_a_fixed_error_envelope_in_a_fresh_process():
    child = r'''
import asyncio
import builtins
import time

real_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "src.harbour_inbox_service" or name.startswith("src.harbour_inbox_service."):
        raise ImportError("inbox imports are blocked")
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

from src.mcp_authenticated_dispatch import (
    McpAuthenticatedIdentity, McpDispatchFailure, dispatch_failure_response, dispatch_http_payload,
)

identity = McpAuthenticatedIdentity(
    "token-id", "admin", "subject-id", 4, 9, "mcp:harbour:inbox",
)
request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

class Runtime:
    def tool_contracts(self):
        raise AssertionError("unavailable Inbox must not reach the runtime")

async def scenario():
    try:
        result = await dispatch_http_payload(
            request, identity=identity, runtime=Runtime(), started=time.monotonic(),
        )
    except McpDispatchFailure as exc:
        result = dispatch_failure_response(exc.code, message_id=1)
    assert result == {"jsonrpc": "2.0", "id": 1,
        "error": {"code": -32011, "message": "Planning read failed"}}

asyncio.run(scenario())
'''
    completed = subprocess.run(
        [sys.executable, "-B", "-c", child],
        cwd=str(__file__.replace("\\tests\\test_mcp_authenticated_dispatch.py", "")),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0


def test_authentication_fence_stops_batch_before_second_call():
    async def scenario():
        runtime = _Runtime()
        fence_count = 0

        def fence():
            nonlocal fence_count
            fence_count += 1
            if fence_count >= 5:
                raise RuntimeError

        batch = [
            _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}, 1),
            _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}, 2),
        ]
        with pytest.raises(McpDispatchFailure) as error:
            await dispatch_http_payload(
                batch,
                identity=_IDENTITY,
                runtime=runtime,
                started=time.monotonic(),
                fence=fence,
            )
        assert error.value.code == "stale_authentication"
        assert runtime.calls == 1

    asyncio.run(scenario())


def test_authentication_requires_current_alias_persisted_owner_and_cache_pair(monkeypatch):
    class Manager:
        def __init__(self, alias="admin", subject="subject-id"):
            self.alias = alias
            self.subject = subject
        def security_action_revision(self):
            return 9
        def active_admin_subject_identity(self, *, subject_id):
            return (self.alias, self.subject) if subject_id == self.subject else None
        @contextmanager
        def guard_current_admin_subject(self, subject_id):
            identity = self.active_admin_subject_identity(subject_id=subject_id)
            if identity is None:
                raise RuntimeError
            yield identity[0]

    row = SimpleNamespace(
        id="token-id", is_active=True, scopes="mcp:planning:read",
        owner_subject_id="subject-id", owner="admin", token_hash="unused",
    )
    class Query:
        def filter(self, *_args):
            return self
        def first(self):
            return row
    class DB:
        def query(self, *_args):
            return Query()
    @contextmanager
    def session():
        yield DB()
    monkeypatch.setattr("src.mcp_authenticated_dispatch.get_db_session", session)
    manager = Manager()
    state = SimpleNamespace(
        api_token=True, api_token_scopes=("mcp:planning:read",), api_token_id="token-id",
        api_token_owner_subject_id="subject-id", api_token_owner="admin",
        api_token_cache_generation=4,
    )
    app_state = SimpleNamespace(
        _token_cache_generation=4, _token_cache_dirty=False, auth_manager=manager,
    )
    request = SimpleNamespace(
        state=state, headers={"authorization": "Bearer redacted"},
        app=SimpleNamespace(state=app_state),
    )
    proof = capture_mcp_bearer_proof(SimpleNamespace(
        state=SimpleNamespace(api_token=True),
        headers={"authorization": "Bearer ody_private_fixture"},
    ))
    monkeypatch.setattr(proof.__class__, "matches_hash", lambda self, value: value == "unused")
    assert authenticate_mcp_request(request, proof).owner_alias == "admin"
    manager.alias = "renamed-admin"
    with pytest.raises(Exception):
        authenticate_mcp_request(request, proof)
    manager.alias = "admin"
    state.api_token_owner = "renamed-admin"
    with pytest.raises(Exception):
        authenticate_mcp_request(request, proof)
    state.api_token_owner = "admin"
    row.owner = "renamed-admin"
    with pytest.raises(Exception):
        authenticate_mcp_request(request, proof)


def test_global_planning_gate_hides_and_blocks_tools():
    async def scenario():
        runtime = _Runtime()
        for method, params in (("tools/list", {}), ("tools/call", {"name": "planning_list_roadmaps", "arguments": {}})):
            result = await dispatch_http_payload(
                _rpc(method, params),
                identity=_IDENTITY,
                runtime=runtime,
                started=time.monotonic(),
                planning_reads_enabled=False,
            )
            assert result["error"]["message"] == "Planning read is unavailable"
        assert runtime.calls == 0

    asyncio.run(scenario())


def test_typed_inbox_read_is_classified_for_the_final_authority_fence():
    identity = McpAuthenticatedIdentity(
        "token-id", "admin", "owner_" + "a" * 32, 4, 9, "mcp:harbour:inbox",
    )
    message = _rpc("tools/call", {"name": "harbour_inbox_list", "arguments": {"view": "typed"}})
    assert authenticated_dispatch._is_unified_inbox_read(message, identity) is True


def test_http_read_roadmap_rejects_raw_preview_before_dispatch():
    async def scenario():
        runtime = _Runtime()
        result = await dispatch_http_payload(
            _rpc("tools/call", {"name": "planning_read_roadmap", "arguments": {
                "source_id_or_path": "repo-plan:example", "include_raw_preview_chars": 1,
            }}),
            identity=_IDENTITY, runtime=runtime, started=time.monotonic(),
        )
        assert result["error"]["message"] == "Invalid params"
        assert runtime.calls == 0
    asyncio.run(scenario())


def test_read_roadmap_adapter_rejects_raw_or_truncated_preview():
    record = {
        "source_id": "repo-plan:demo", "source_ref": "docs/demo.json", "source_hash": "sha256:" + "a" * 64,
        "project_id": "demo", "roadmap_id": "demo", "ids_derived": False, "title": "Demo", "goal": "Goal",
        "kind": "roadmap", "status": "approved", "size_bytes": 10, "valid_json": True,
        "preview": "", "repo_relative": True, "absolute_path_recorded": False,
    }
    payload = {
        "schema": "odysseus.planning.roadmap_read.v1", "read_only": True, "writes_supported": False,
        "source": record,
        "roadmap": {"schema_version": 1, "kind": "roadmap", "project_id": "demo", "roadmap_id": "demo",
                    "plan_id": "", "title": "Demo", "goal": "Goal", "status": "approved", "revision": 1,
                    "created_at": "", "updated_at": ""},
        "logical_ids": {"project_id": "demo", "roadmap_id": "demo", "derived": False},
        "source_refs": ["docs/demo.json"], "dependency_hints": [], "slices": [], "gates": [],
        "validation": {"schema": "odysseus.planning.roadmap_validation.v1", "read_only": True,
                       "writes_performed": False, "valid": True, "errors": [], "warnings": [],
                       "summary": {"errors": 0, "warnings": 0}},
        "raw_json_preview": "source body", "raw_json_truncated": False, "absolute_paths_visible": False,
    }
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_read_roadmap", payload)
    payload["source"]["preview"] = "raw inventory preview"
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_read_roadmap", payload)
    payload["source"]["preview"] = ""
    payload["raw_json_preview"] = ""
    payload["raw_json_truncated"] = True
    with pytest.raises(McpDispatchFailure):
        _validate_projection("planning_read_roadmap", payload)


def test_gate_flip_after_first_batch_call_stops_later_call():
    async def scenario():
        runtime = _Runtime()
        allowed = True
        call_fences = 0
        def fence(method=None):
            nonlocal allowed, call_fences
            if method in {"tools/list", "tools/call"} and not allowed:
                raise McpDispatchFailure("planning_unavailable")
            if method == "tools/call":
                call_fences += 1
                if call_fences == 4:
                    allowed = False
        batch = [
            _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}, 1),
            _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}, 2),
        ]
        with pytest.raises(McpDispatchFailure) as error:
            await dispatch_http_payload(
                batch, identity=_IDENTITY, runtime=runtime, started=time.monotonic(), fence=fence,
            )
        assert error.value.code == "planning_unavailable"
        assert runtime.calls == 1
    asyncio.run(scenario())


def test_batch_aggregate_limit_includes_complete_json_array_encoding(monkeypatch):
    batch = [_rpc("ping", {}, 1), _rpc("ping", {}, 2)]
    expected = [
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {}},
    ]
    encoded_size = len(json.dumps(
        expected, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", "strict"))
    member_size = sum(len(json.dumps(
        item, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", "strict")) for item in expected)
    assert encoded_size == member_size + 3  # array brackets and comma

    async def dispatch():
        return await dispatch_http_payload(
            batch, identity=_IDENTITY, runtime=_Runtime(), started=time.monotonic(),
        )

    monkeypatch.setattr(authenticated_dispatch, "MCP_AGGREGATE_MAX_BYTES", encoded_size)
    assert asyncio.run(dispatch()) == expected
    monkeypatch.setattr(authenticated_dispatch, "MCP_AGGREGATE_MAX_BYTES", encoded_size - 1)
    with pytest.raises(McpDispatchFailure) as error:
        asyncio.run(dispatch())
    assert error.value.code == "budget_exceeded"
