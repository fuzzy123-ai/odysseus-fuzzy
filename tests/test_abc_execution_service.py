from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.abc_execution_service import (
    ABCExecutionRequest,
    ABCExecutionService,
    ABCExecutionServiceError,
    PersistentRunStartStore,
)
from src.planning_agent_handoff import build_agent_plan_handoff
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import PlanningRevisionStore
from src.temporal_runtime.client import RecordingTemporalClient
from src.temporal_runtime.contracts import ExecutionPolicy
from tests.test_planning_definition_projection import definition_fixture


OWNER = "owner:alice"


def _planning(document: dict | None = None):
    value = document or definition_fixture(include_draft=False)
    store = PlanningRevisionStore([(OWNER, value, "definition.json")])
    roadmap = value["roadmaps"][0]
    read_model = store.get_roadmap(
        OWNER, value["project"]["project_id"], roadmap["roadmap_id"], revision=roadmap["revision"]
    )
    handoff = build_agent_plan_handoff(
        read_model,
        expected_revision=roadmap["revision"],
        expected_hash=roadmap["content_hash"],
    )
    return store, handoff


def _policy(**overrides):
    values = {
        "queue_scope": "named_roadmap",
        "supervision_mode": "unattended_long_run",
        "mutation_authority": "repo_only",
        "selected_route": {
            "entrypoint": "/abc",
            "skills": [{"id": "abc", "purpose": "orchestrator"}],
            "model": {"value": "surface_default", "reason": "unverified surface default"},
        },
        "deadline_at": "2026-07-16T09:00:00Z",
    }
    values.update(overrides)
    return ExecutionPolicy.create(**values)


def _request(handoff: dict, **overrides):
    values = {
        "owner_scope_ref": OWNER,
        "authenticated": True,
        "entrypoint": "/abc",
        "handoff": handoff,
        "start_request_id": "start-request-1001",
        "policy": _policy(),
    }
    values.update(overrides)
    return ABCExecutionRequest(**values)


def _service(tmp_path: Path, planning=None, client=None):
    store, handoff = _planning() if planning is None else planning
    temporal = client or RecordingTemporalClient()
    starts = PersistentRunStartStore(tmp_path / "run-starts.json")
    service = ABCExecutionService(planning_store=store, start_store=starts, temporal_client=temporal)
    return service, starts, temporal, handoff


@pytest.mark.asyncio
async def test_authenticated_abc_start_persists_before_one_fake_dispatch(tmp_path):
    service, starts, temporal, handoff = _service(tmp_path)

    receipt = await service.start_run(_request(handoff))
    stored = starts.get_record(OWNER, "start-request-1001")

    assert receipt.state == "started"
    assert receipt.planning_ref.to_value()["content_hash"] == handoff["content_hash"]
    assert stored["state"] == "started"
    assert stored["receipt"] == receipt.to_payload()
    assert len(temporal.workflows) == 1
    assert next(iter(temporal.workflows.values())).manifest_hash == receipt.manifest_hash


@pytest.mark.asyncio
async def test_reservation_is_durable_before_client_dispatch(tmp_path):
    planning, handoff = _planning()
    starts = PersistentRunStartStore(tmp_path / "starts-before-dispatch.json")

    class InspectingClient(RecordingTemporalClient):
        async def start_workflow(self, *, workflow_id, task_queue, manifest):
            record = starts.get_record(OWNER, "start-request-1001")
            assert record is not None
            assert record["state"] == "reserved"
            assert record["receipt"] is None
            assert record["workflow_id"] == workflow_id
            return await super().start_workflow(
                workflow_id=workflow_id,
                task_queue=task_queue,
                manifest=manifest,
            )

    client = InspectingClient()
    service = ABCExecutionService(
        planning_store=planning,
        start_store=starts,
        temporal_client=client,
    )

    receipt = await service.start_run(_request(handoff))

    assert receipt.state == "started"
    assert client.start_call_count == 1


@pytest.mark.asyncio
async def test_duplicate_and_service_restart_return_the_original_receipt(tmp_path):
    service, starts, temporal, handoff = _service(tmp_path)
    request = _request(handoff)

    first = await service.start_run(request)
    duplicate = await service.start_run(request)
    restarted = ABCExecutionService(
        planning_store=_planning()[0],
        start_store=PersistentRunStartStore(starts.path),
        temporal_client=temporal,
    )
    after_restart = await restarted.start_run(request)

    assert first == duplicate == after_restart
    assert temporal.start_call_count == 1
    assert len(temporal.workflows) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_create_one_workflow(tmp_path):
    service, _starts, temporal, handoff = _service(tmp_path)
    request = _request(handoff)

    receipts = await asyncio.gather(*(service.start_run(request) for _ in range(8)))

    assert len(set(receipts)) == 1
    assert temporal.start_call_count == 1
    assert len(temporal.workflows) == 1


@pytest.mark.asyncio
async def test_reused_start_request_with_changed_policy_is_a_conflict(tmp_path):
    service, _starts, temporal, handoff = _service(tmp_path)
    await service.start_run(_request(handoff))

    with pytest.raises(ABCExecutionServiceError) as raised:
        await service.start_run(
            _request(handoff, policy=_policy(max_parallel_activities=2))
        )

    assert raised.value.code == "idempotency_conflict"
    assert temporal.start_call_count == 1


@pytest.mark.asyncio
async def test_changed_planning_head_creates_no_run_or_reservation(tmp_path):
    old_document = definition_fixture(include_draft=False)
    _old_store, old_handoff = _planning(old_document)
    changed = definition_fixture(include_draft=True)
    newer = changed["roadmaps"][1]
    newer["revision_state"] = "approved"
    newer["content_hash"] = compute_roadmap_content_hash(newer)
    changed["project"]["latest_approved_revision"]["roadmap-a"] = {
        "revision": 2,
        "content_hash": newer["content_hash"],
    }
    new_store = PlanningRevisionStore([(OWNER, changed, "changed.json")])
    temporal = RecordingTemporalClient()
    starts = PersistentRunStartStore(tmp_path / "starts.json")
    service = ABCExecutionService(
        planning_store=new_store, start_store=starts, temporal_client=temporal
    )

    with pytest.raises(ABCExecutionServiceError) as raised:
        await service.start_run(_request(old_handoff))

    assert raised.value.code == "plan_revision_conflict"
    assert temporal.start_call_count == 0
    assert starts.get_record(OWNER, "start-request-1001") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"authenticated": False}, "authentication_required"),
        ({"entrypoint": "Planning"}, "entrypoint_required"),
    ],
)
async def test_only_authenticated_abc_can_reach_planning_or_dispatch(tmp_path, overrides, code):
    service, starts, temporal, handoff = _service(tmp_path)

    with pytest.raises(ABCExecutionServiceError) as raised:
        await service.start_run(_request(handoff, **overrides))

    assert raised.value.code == code
    assert temporal.start_call_count == 0
    assert starts.get_record(OWNER, "start-request-1001") is None


@pytest.mark.asyncio
async def test_tampered_handoff_fails_before_planning_dispatch(tmp_path):
    service, starts, temporal, handoff = _service(tmp_path)
    tampered = deepcopy(handoff)
    tampered["content_hash"] = "sha256:" + ("f" * 64)

    with pytest.raises(ABCExecutionServiceError) as raised:
        await service.start_run(_request(tampered))

    assert raised.value.code == "invalid_handoff"
    assert temporal.start_call_count == 0
    assert starts.get_record(OWNER, "start-request-1001") is None


@pytest.mark.asyncio
async def test_corrupt_existing_reservation_fails_closed_without_dispatch(tmp_path):
    service, starts, temporal, handoff = _service(tmp_path)
    first = await service.start_run(_request(handoff))
    document = json.loads(starts.path.read_text(encoding="utf-8"))
    record = next(iter(document["records"].values()))
    record["state"] = "unknown"
    starts.path.write_text(json.dumps(document), encoding="utf-8")
    temporal.start_call_count = 0

    with pytest.raises(ABCExecutionServiceError) as raised:
        await service.start_run(_request(handoff))

    assert first.state == "started"
    assert raised.value.code == "store_corrupt"
    assert temporal.start_call_count == 0


def test_planning_surface_has_no_temporal_or_execution_service_start_path():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8").lower()
        for path in (
            "src/planning_agent_handoff.py",
            "src/planning_mcp_service.py",
            "routes/planning_definition_routes.py",
        )
    )

    assert "abc_execution_service" not in source
    assert "start_workflow" not in source
    assert "/api/agent/runs" not in source
