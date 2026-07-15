from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routes.agent_operation_routes import (
    setup_agent_operation_routes,
    setup_default_agent_operation_routes,
)
from routes.planning_definition_routes import setup_planning_definition_routes
from src.abc_execution_service import RUN_START_STORE_SCHEMA_ID
from src.planning_revision_store import PlanningRevisionStore
from src.temporal_agent_operation_adapter import (
    AgentOperationAdapterError,
    LazyTemporalSDKExecutionReader,
    PersistentRunCatalog,
    TemporalAgentOperationAdapter,
)
from src.temporal_runtime.commands import CommandContractError, CommandReceipt
from src.temporal_runtime.contracts import RUN_START_RECEIPT_SCHEMA_ID
from tests.test_planning_definition_projection import definition_fixture


ALICE = "owner:alice"
BOB = "owner:bob"
RUN_ALICE = "arun-" + "1" * 32
RUN_BOB = "arun-" + "2" * 32
HASH = "sha256:" + "a" * 64
NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def _workflow_id(agent_run_id):
    return f"odysseus-abc/0123456789abcdef/{agent_run_id}"


def _receipt(agent_run_id, owner):
    return {
        "schema_id": RUN_START_RECEIPT_SCHEMA_ID,
        "start_request_id": f"start-{owner.split(':')[1]}",
        "agent_run_id": agent_run_id,
        "workflow_id": _workflow_id(agent_run_id),
        "workflow_run_id": f"temporal-{owner.split(':')[1]}",
        "manifest_hash": HASH,
        "planning_ref": {
            "project_id": "project-1",
            "roadmap_id": "roadmap-1",
            "revision": 7,
            "content_hash": HASH,
        },
        "state": "started",
        "task_queue": "odysseus-temporal-light",
    }


def _record(agent_run_id, owner):
    receipt = _receipt(agent_run_id, owner)
    return {
        "start_request_id": receipt["start_request_id"],
        "agent_run_id": agent_run_id,
        "workflow_id": receipt["workflow_id"],
        "manifest_hash": HASH,
        "manifest": {
            "owner_scope_ref": owner,
            "project_id": "project-1",
            "deadline_at": "2026-07-16T12:00:00Z",
        },
        "state": "started",
        "receipt": receipt,
    }


def _write_catalog(path):
    path.write_text(
        json.dumps(
            {
                "schema_id": RUN_START_STORE_SCHEMA_ID,
                "records": {
                    "alice-record": _record(RUN_ALICE, ALICE),
                    "bob-record": _record(RUN_BOB, BOB),
                },
            }
        ),
        encoding="utf-8",
    )


class FakeTemporal:
    def __init__(self):
        self.states = {
            _workflow_id(RUN_ALICE): self._state("temporal-alice"),
            _workflow_id(RUN_BOB): self._state("temporal-bob"),
        }
        self.events = {
            workflow_id: [self._event(number) for number in range(1, 206)]
            for workflow_id in self.states
        }
        self.receipts = {}
        self.update_calls = 0

    @staticmethod
    def _state(workflow_run_id):
        return {
            "workflow_run_id": workflow_run_id,
            "run_state": "running",
            "run_version": 4,
            "slice_states": {"node-a": "activity_running", "node-b": "retry_wait"},
            "gate_states": {"gate-a": "pending"},
            "history_segment": 0,
            "started_at": "2026-07-15T10:00:00Z",
            "updated_at": "2026-07-15T11:59:55Z",
            "completed_at": None,
            "activities": [
                {
                    "activity_id": "activity-1",
                    "node_id": "node-a",
                    "type": "execute_slice",
                    "state": "running",
                    "attempt": 1,
                    "max_attempts": 3,
                    "retryable": True,
                    "next_retry_at": None,
                    "started_at": "2026-07-15T11:58:00Z",
                    "updated_at": "2026-07-15T11:59:55Z",
                    "completed_at": None,
                    "last_heartbeat_at": "2026-07-15T11:59:55Z",
                    "heartbeat_timeout_seconds": 90,
                    "error_code": None,
                }
            ],
        }

    @staticmethod
    def _event(number):
        return {
            "history_segment": 0,
            "event_id": number,
            "event_type": "workflow_task_completed",
            "occurred_at": f"2026-07-15T11:{number // 60:02d}:{number % 60:02d}Z",
            "node_id": None,
            "activity_id": None,
            "summary": "workflow task completed",
            "ref_ids": [],
        }

    async def snapshot(self, workflow_id, workflow_run_id):
        return deepcopy(self.states[workflow_id])

    async def history(
        self,
        workflow_id,
        workflow_run_id,
        *,
        history_segment,
        after_event_id,
        limit,
    ):
        return [
            deepcopy(event)
            for event in self.events[workflow_id]
            if event["event_id"] > after_event_id
        ][:limit]

    async def execute_command(self, workflow_id, workflow_run_id, request):
        self.update_calls += 1
        existing = self.receipts.get(request.idempotency_key)
        if existing is not None:
            if existing["binding_digest"] != request.binding_digest:
                raise CommandContractError("command_conflict", "idempotency key rebound")
            return deepcopy(existing)
        state = self.states[workflow_id]
        if request.expected_run_version != state["run_version"]:
            raise CommandContractError("stale_run_version", "workflow rejected stale command")
        state["run_version"] += 1
        if request.command == "pause":
            state["run_state"] = "paused"
        receipt = CommandReceipt.create(
            request,
            result_run_version=state["run_version"],
            result_code="applied",
            state=state["run_state"],
        ).to_payload()
        self.receipts[request.idempotency_key] = receipt
        return deepcopy(receipt)


class FakeStarter:
    async def start(self, owner_scope_ref, body):
        assert owner_scope_ref == ALICE
        return _receipt(RUN_ALICE, ALICE)


@pytest.fixture
def api(tmp_path):
    store_path = tmp_path / "run-starts.json"
    _write_catalog(store_path)
    temporal = FakeTemporal()
    adapter = TemporalAgentOperationAdapter(
        catalog=PersistentRunCatalog(store_path),
        temporal=temporal,
        starter=FakeStarter(),
        clock=lambda: NOW,
    )
    app = FastAPI()
    app.include_router(
        setup_agent_operation_routes(
            adapter,
            owner_resolver=lambda request: request.headers.get("x-owner"),
            abc_gate=lambda request: request.headers.get("x-entrypoint") == "/abc",
            csrf_gate=lambda request: request.headers.get("x-csrf") == "ok",
        )
    )
    return TestClient(app), adapter, temporal, store_path


def _headers(owner=ALICE, *, csrf=False, abc=False):
    result = {"x-owner": owner}
    if csrf:
        result["x-csrf"] = "ok"
    if abc:
        result["x-entrypoint"] = "/abc"
    return result


def _command_body():
    return {
        "command_id": "command-1",
        "command": "pause",
        "expected_run_version": 4,
        "idempotency_key": "idem-1",
        "payload": {},
    }


def test_all_six_agent_endpoints_are_registered_and_fail_closed(api):
    client, _, _, _ = api
    operations = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
    }
    expected = {
        ("POST", "/api/agent/runs"),
        ("GET", "/api/agent/runs"),
        ("GET", "/api/agent/runs/{agent_run_id}"),
        ("GET", "/api/agent/runs/{agent_run_id}/history"),
        ("GET", "/api/agent/runs/{agent_run_id}/stream"),
        ("POST", "/api/agent/runs/{agent_run_id}/commands"),
    }
    assert expected <= operations
    assert client.get("/api/agent/runs").status_code == 401


def test_owner_isolation_is_indistinguishable_from_missing(api):
    client, _, _, _ = api
    denied = client.get(f"/api/agent/runs/{RUN_BOB}", headers=_headers(ALICE))
    allowed = client.get(f"/api/agent/runs/{RUN_BOB}", headers=_headers(BOB))

    assert denied.status_code == 404
    assert allowed.status_code == 200
    listed = client.get("/api/agent/runs", headers=_headers(ALICE)).json()["runs"]
    assert [item["agent_run_id"] for item in listed] == [RUN_ALICE]


def test_current_projection_reconstructs_after_adapter_process_restart(api):
    client, _, temporal, store_path = api
    first = client.get(f"/api/agent/runs/{RUN_ALICE}", headers=_headers()).json()
    restarted = TemporalAgentOperationAdapter(
        catalog=PersistentRunCatalog(store_path),
        temporal=temporal,
        clock=lambda: NOW,
    )

    import asyncio

    second = asyncio.run(restarted.get_run(ALICE, RUN_ALICE))
    assert second == first
    assert second["run"]["plan_ref"]["content_hash"] == HASH


def test_history_limit_and_sse_last_event_id_have_no_gap_or_duplicate(api):
    client, _, _, _ = api
    too_large = client.get(
        f"/api/agent/runs/{RUN_ALICE}/history?limit=201", headers=_headers()
    )
    first = client.get(
        f"/api/agent/runs/{RUN_ALICE}/stream", headers=_headers()
    )
    first_ids = [
        line.removeprefix("id: ")
        for line in first.text.splitlines()
        if line.startswith("id: ")
    ]
    second = client.get(
        f"/api/agent/runs/{RUN_ALICE}/stream",
        headers={**_headers(), "Last-Event-ID": first_ids[-1]},
    )
    second_ids = [
        line.removeprefix("id: ")
        for line in second.text.splitlines()
        if line.startswith("id: ")
    ]

    assert too_large.status_code == 422
    assert first_ids == [f"h0:{number}" for number in range(1, 201)]
    assert second_ids == [f"h0:{number}" for number in range(201, 206)]
    assert len(set(first_ids + second_ids)) == 205
    assert "raw_history" not in (first.text + second.text).lower()
    assert r"C:\\Users" not in first.text + second.text


def test_start_requires_authenticated_abc_and_csrf(api):
    client, _, _, _ = api
    body = {
        "project_id": "project-1",
        "roadmap_id": "roadmap-1",
        "revision": 7,
        "content_hash": HASH,
        "start_request_id": "start-alice",
    }

    assert client.post("/api/agent/runs", headers=_headers(), json=body).status_code == 403
    assert (
        client.post(
            "/api/agent/runs", headers=_headers(csrf=True, abc=True), json=body
        ).status_code
        == 200
    )


def test_command_requires_csrf_and_returns_receipt_plus_current_run(api):
    client, _, temporal, _ = api
    path = f"/api/agent/runs/{RUN_ALICE}/commands"

    assert client.post(path, headers=_headers(), json=_command_body()).status_code == 403
    response = client.post(
        path, headers=_headers(csrf=True), json=_command_body()
    )

    assert response.status_code == 200
    assert response.json()["command_receipt"]["result_run_version"] == 5
    assert response.json()["run"]["version"] == 5
    assert temporal.update_calls == 1


def test_planning_routes_remain_definition_only():
    document = definition_fixture(include_draft=False)
    store = PlanningRevisionStore([(ALICE, document, "definition.json")])
    app = FastAPI()
    app.include_router(
        setup_planning_definition_routes(
            store,
            owner_resolver=lambda _request: ALICE,
            admin_gate=lambda _request: True,
        )
    )
    client = TestClient(app)
    project_id = document["project"]["project_id"]
    roadmap_id = document["roadmaps"][0]["roadmap_id"]
    response = client.get(
        f"/api/planning/projects/{project_id}/roadmaps/{roadmap_id}"
    )
    encoded = json.dumps(response.json(), sort_keys=True)

    assert response.status_code == 200
    for forbidden in (
        "agent_run_id",
        "workflow_id",
        "heartbeat_health",
        "allowed_commands",
        "history_segment",
    ):
        assert forbidden not in encoded


def test_default_router_registration_has_no_network_or_process_side_effect(
    tmp_path, monkeypatch
):
    calls = []

    async def forbidden_connect(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("registration must stay lazy")

    monkeypatch.setattr(
        "src.temporal_agent_operation_adapter.Client.connect", forbidden_connect
    )
    router = setup_default_agent_operation_routes(
        run_store_path=tmp_path / "run-starts.json"
    )

    assert calls == []
    assert len([route for route in router.routes if route.path == "/api/agent/runs"]) == 2


def test_default_router_registration_does_not_load_server_persistence_config(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ODYSSEUS_TEMPORAL_RUNTIME_DIR", str(Path.cwd()))

    router = setup_default_agent_operation_routes(
        run_store_path=tmp_path / "run-starts.json"
    )

    assert len([route for route in router.routes if route.path == "/api/agent/runs"]) == 2


def test_lazy_reader_rejects_any_non_pinned_runtime_target():
    with pytest.raises(AgentOperationAdapterError) as caught:
        LazyTemporalSDKExecutionReader(address="temporal.example.com:7233")
    assert caught.value.code == "invalid_temporal_address"


def test_agent_router_is_registered_once_adjacent_to_planning():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
        encoding="utf-8"
    )
    import_line = (
        "from routes.agent_operation_routes import setup_default_agent_operation_routes"
    )
    include_line = "app.include_router(setup_default_agent_operation_routes())"

    assert source.count(import_line) == 1
    assert source.count(include_line) == 1
    assert source.index("app.include_router(setup_default_planning_definition_routes())") < source.index(
        include_line
    )
