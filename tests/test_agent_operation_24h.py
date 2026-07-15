from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any, Callable

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment

from src.agent_operation_projection import (
    MAX_HISTORY_PAGE_SIZE,
    build_agent_operation_projection,
    project_history,
)
from src.temporal_runtime.commands import CommandRequest
from src.temporal_runtime.worker import create_temporal_worker
from src.temporal_runtime.workflows import (
    ABCExecutionWorkflow,
    EXECUTE_SLICE_ACTIVITY,
    WorkflowStart,
)


LOGICAL_DURATION = timedelta(hours=24)
SEGMENT_DURATION = timedelta(hours=6)
HEARTBEAT_INTERVAL = timedelta(seconds=30)
HEARTBEAT_OPPORTUNITIES = int(LOGICAL_DURATION / HEARTBEAT_INTERVAL)


def _test_server_path() -> str:
    raw = os.environ.get("ODYSSEUS_TEMPORAL_TEST_SERVER", "")
    if not raw:
        pytest.skip("ODYSSEUS_TEMPORAL_TEST_SERVER is required for offline SDK acceptance")
    path = Path(raw)
    assert path.is_file(), "the configured Temporal SDK test server does not exist"
    return str(path)


def _manifest(anchor: datetime) -> dict[str, Any]:
    deadline = anchor + LOGICAL_DURATION
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + "7" * 32,
        "manifest_hash": "sha256:" + "7" * 64,
        "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
        "max_parallel_activities": 1,
        "normalized_dag": {
            "nodes": [
                {
                    "node_id": "bootstrap",
                    "kind": "repo_slice",
                    "depends_on": [],
                    "gate_ids": [],
                    "verification_rule_ids": ["verify-bootstrap"],
                },
                {
                    "node_id": "after-gate",
                    "kind": "repo_slice",
                    "depends_on": ["bootstrap"],
                    "gate_ids": ["gate-hour-18"],
                    "verification_rule_ids": ["verify-after-gate"],
                },
                {
                    "node_id": "deadline-sentinel",
                    "kind": "repo_slice",
                    "depends_on": ["after-gate"],
                    "gate_ids": ["gate-never-approved"],
                    "verification_rule_ids": ["verify-deadline"],
                },
            ],
            "edges": [
                {"from": "bootstrap", "to": "after-gate"},
                {"from": "after-gate", "to": "deadline-sentinel"},
            ],
            "gates": [
                {"gate_id": "gate-hour-18"},
                {"gate_id": "gate-never-approved"},
            ],
        },
    }


class SyntheticLongRunActivity:
    def __init__(self) -> None:
        self.attempts: list[tuple[str, int]] = []
        self.effect_keys: set[str] = set()

    @activity.defn(name=EXECUTE_SLICE_ACTIVITY)
    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        node_id = str(payload["node_id"])
        attempt = activity.info().attempt
        self.attempts.append((node_id, attempt))
        activity.heartbeat(
            {
                "node_id": node_id,
                "attempt": attempt,
                "phase": "synthetic_acceptance",
            }
        )
        if node_id == "after-gate" and attempt == 1:
            raise ApplicationError(
                "synthetic retry",
                type="synthetic_retry",
                non_retryable=False,
            )
        effect_key = f"{payload['agent_run_id']}:{node_id}"
        assert effect_key not in self.effect_keys
        self.effect_keys.add(effect_key)
        return {
            "node_id": node_id,
            "status": "succeeded",
            "evidence_verified": True,
            "writeback_receipt": f"acceptance:{payload['history_segment']}:{node_id}",
        }


async def _wait_for_projection(
    handle,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    attempts: int = 200,
) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for _ in range(attempts):
        try:
            last = await handle.query("get_run_state")
        except Exception:
            await asyncio.sleep(0.025)
            continue
        if predicate(last):
            return last
        await asyncio.sleep(0.025)
    raise AssertionError(f"workflow projection did not reach the expected state; last={last!r}")


async def _phase(name: str, awaitable, *, timeout: float = 15.0):
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout)
    except TimeoutError as exc:
        raise AssertionError(f"phase_timeout:{name}") from exc
    print(f"PHASE_OK:{name}", flush=True)
    return result


async def _start_worker(environment, task_queue: str, implementation):
    worker = create_temporal_worker(
        environment.client,
        task_queue=task_queue,
        activities=[implementation],
        max_concurrent_activities=1,
    )
    run_task = asyncio.create_task(worker.run())
    for _ in range(200):
        if run_task.done():
            await run_task
        if worker._started:
            return worker, run_task
        await asyncio.sleep(0.025)
    raise AssertionError("Temporal worker did not start")


async def _stop_worker(worker, run_task) -> None:
    if worker is None or run_task is None:
        return
    if not run_task.done():
        await asyncio.wait_for(worker.shutdown(), timeout=5)
    await asyncio.wait_for(run_task, timeout=5)


def _gate_command(run_version: int) -> CommandRequest:
    return CommandRequest.create(
        command_id="command-hour-18-gate",
        command="decide_gate",
        expected_run_version=run_version,
        idempotency_key="idempotency-hour-18-gate",
        payload={"gate_id": "gate-hour-18", "decision": "approved"},
    )


def _agent_projection(
    workflow_projection: dict[str, Any],
    *,
    observed_at: datetime,
    heartbeat_at: datetime,
) -> dict[str, Any]:
    timestamp = lambda value: value.isoformat().replace("+00:00", "Z")
    return build_agent_operation_projection(
        plan_ref={
            "project_id": "odysseus",
            "roadmap_id": "temporal-light-agent-execution",
            "revision": 1,
            "content_hash": "sha256:" + "6" * 64,
        },
        run={
            **workflow_projection,
            "workflow_id": "tlr09-24h-acceptance",
            "workflow_run_id": "logical-run",
            "started_at": timestamp(observed_at - SEGMENT_DURATION),
            "updated_at": timestamp(observed_at),
        },
        activities=[
            {
                "activity_id": "activity-after-gate",
                "node_id": "after-gate",
                "type": EXECUTE_SLICE_ACTIVITY,
                "state": "running",
                "attempt": 1,
                "max_attempts": 3,
                "retryable": True,
                "started_at": timestamp(heartbeat_at),
                "updated_at": timestamp(heartbeat_at),
                "last_heartbeat_at": timestamp(heartbeat_at),
                "heartbeat_timeout_seconds": 90,
            }
        ],
        claims=[
            {
                "claim_id": "claim-after-gate",
                "node_id": "after-gate",
                "repo_id": "odysseus",
                "repo_relative_paths": ["src/temporal_runtime/workflows.py"],
                "hotfiles": ["src/temporal_runtime/workflows.py"],
                "state": "expired",
                "lease_revision": 1,
                "lease_expires_at": timestamp(heartbeat_at + timedelta(minutes=2)),
            }
        ],
        observed_at=timestamp(observed_at),
    )


async def _history_chain(client, workflow_id: str, first_run_id: str):
    histories = []
    run_id = first_run_id
    while run_id:
        history = await client.get_workflow_handle(
            workflow_id,
            run_id=run_id,
        ).fetch_history()
        histories.append(history)
        final_event = history.events[-1]
        if final_event.event_type != EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW:
            break
        run_id = (
            final_event.workflow_execution_continued_as_new_event_attributes
            .new_execution_run_id
        )
    return histories


def _bounded_projected_history(anchor: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for segment in range(4):
        for event_id in range(1, 76):
            occurred_at = anchor + timedelta(hours=segment * 6, seconds=event_id)
            events.append(
                {
                    "history_segment": segment,
                    "event_id": event_id,
                    "event_type": "heartbeat_opportunity_window",
                    "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
                    "node_id": "after-gate",
                    "activity_id": "activity-after-gate",
                    "summary": "bounded 30-second heartbeat window",
                    "ref_ids": [f"segment-{segment}"],
                }
            )
    return events


@pytest.mark.asyncio
async def test_24h_time_skipping_survives_reconnect_retry_worker_loss_and_deadline():
    environment = await _phase(
        "environment_started",
        WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=_test_server_path()
        ),
    )
    worker_one = run_task_one = worker_two = run_task_two = None
    activity_impl = SyntheticLongRunActivity()
    task_queue = "tlr09-24h-time-skipping"
    anchor = await _phase("logical_clock_read", environment.get_current_time())
    manifest = _manifest(anchor)
    histories = []
    hour_6_projection: dict[str, Any] | None = None
    hour_18_projection: dict[str, Any] | None = None
    try:
        worker_one, run_task_one = await _phase(
            "worker_one_started",
            _start_worker(
                environment,
                task_queue,
                activity_impl.execute,
            ),
        )
        handle = await _phase(
            "workflow_started",
            environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=manifest),
                id="tlr09-24h-acceptance",
                task_queue=task_queue,
            ),
        )
        first_run_id = handle.result_run_id
        initial = await _phase(
            "initial_gate_wait",
            _wait_for_projection(
                handle,
                lambda item: item["run_state"] == "waiting_gate"
                and item["slice_states"]["bootstrap"] == "succeeded",
            ),
        )

        await _phase("hour_6_skip", environment.sleep(SEGMENT_DURATION))
        hour_6 = await _phase(
            "hour_6_reconnect",
            _wait_for_projection(
                handle,
                lambda item: item["history_segment"] >= 1,
            ),
        )
        hour_6_projection = _agent_projection(
            hour_6,
            observed_at=anchor + SEGMENT_DURATION,
            heartbeat_at=anchor,
        )

        await _phase("hour_12_skip", environment.sleep(SEGMENT_DURATION))
        hour_12 = await _phase(
            "hour_12_segment",
            _wait_for_projection(
                handle,
                lambda item: item["history_segment"] >= 2,
            ),
        )
        await _phase("worker_one_stopped", _stop_worker(worker_one, run_task_one))
        worker_one = run_task_one = None

        await _phase(
            "hour_17_59_skip_without_worker",
            environment.sleep(SEGMENT_DURATION - timedelta(minutes=1)),
        )
        worker_two, run_task_two = await _phase(
            "worker_two_started",
            _start_worker(
                environment,
                task_queue,
                activity_impl.execute,
            ),
        )
        await _phase("hour_18_boundary_skip", environment.sleep(timedelta(minutes=1)))
        latest_handle = environment.client.get_workflow_handle(handle.id)
        hour_18 = await _phase(
            "hour_18_reconnect",
            _wait_for_projection(
                latest_handle,
                lambda item: item["history_segment"] >= 3
                and item["run_state"] == "waiting_gate",
            ),
        )
        hour_18_projection = _agent_projection(
            hour_18,
            observed_at=anchor + SEGMENT_DURATION * 3,
            heartbeat_at=anchor + SEGMENT_DURATION * 2,
        )

        request = _gate_command(hour_18["run_version"])
        first_receipt, duplicate_receipt = await _phase(
            "duplicate_gate_command",
            asyncio.gather(
                latest_handle.execute_update("decide_gate", request, id="hour-18-client-a"),
                latest_handle.execute_update("decide_gate", request, id="hour-18-client-b"),
            ),
        )
        assert duplicate_receipt == first_receipt

        await _phase("retry_backoff_skip", environment.sleep(timedelta(seconds=10)))
        after_retry = await _phase(
            "retry_completed",
            _wait_for_projection(
                latest_handle,
                lambda item: item["run_state"] == "waiting_gate"
                and item["slice_states"]["after-gate"] == "succeeded",
            ),
        )
        assert after_retry["command_receipt_count"] == 1

        await _phase(
            "deadline_skip",
            environment.sleep(SEGMENT_DURATION - timedelta(seconds=10)),
        )
        result = await _phase("deadline_result", latest_handle.result())
        histories = await _phase(
            "history_chain",
            _history_chain(
                environment.client,
                handle.id,
                first_run_id,
            ),
        )
    finally:
        for worker, run_task in (
            (worker_two, run_task_two),
            (worker_one, run_task_one),
        ):
            await _stop_worker(worker, run_task)
        await asyncio.wait_for(environment.shutdown(), timeout=10)

    assert initial["history_segment"] == 0
    assert hour_12["history_segment"] == 2
    assert result["run_state"] == "timed_out"
    assert result["history_segment"] == 3
    assert result["agent_run_id"] == manifest["agent_run_id"]
    assert result["slice_states"] == {
        "after-gate": "succeeded",
        "bootstrap": "succeeded",
        "deadline-sentinel": "pending",
    }
    assert result["gate_states"] == {
        "gate-hour-18": "approved",
        "gate-never-approved": "pending",
    }
    assert datetime.fromisoformat(manifest["deadline_at"].replace("Z", "+00:00")) - anchor == LOGICAL_DURATION
    assert HEARTBEAT_OPPORTUNITIES == 2_880
    assert activity_impl.attempts == [
        ("bootstrap", 1),
        ("after-gate", 1),
        ("after-gate", 2),
    ]
    assert activity_impl.effect_keys == {
        f"{manifest['agent_run_id']}:bootstrap",
        f"{manifest['agent_run_id']}:after-gate",
    }
    assert len(histories) == 4
    assert all(len(history.events) <= MAX_HISTORY_PAGE_SIZE for history in histories)
    assert sum(
        history.events[-1].event_type
        == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW
        for history in histories
    ) == 3

    assert hour_6_projection is not None
    assert hour_18_projection is not None
    assert hour_6_projection["run"]["history_segment"] == 1
    assert hour_18_projection["run"]["history_segment"] == 3
    assert hour_18_projection["activities"][0]["heartbeat_health"] == "stale"
    assert hour_18_projection["claims"][0]["state"] == "expired"

    events = _bounded_projected_history(anchor)
    first_page = project_history(events, limit=MAX_HISTORY_PAGE_SIZE)
    second_page = project_history(
        events,
        after=first_page["next_cursor"],
        limit=MAX_HISTORY_PAGE_SIZE,
    )
    projected_ids = [
        item["event_id"]
        for page in (first_page, second_page)
        for item in page["events"]
    ]
    assert len(first_page["events"]) == 200
    assert len(second_page["events"]) == 100
    assert first_page["has_more"] is True
    assert second_page["has_more"] is False
    assert len(projected_ids) == len(set(projected_ids)) == 300
