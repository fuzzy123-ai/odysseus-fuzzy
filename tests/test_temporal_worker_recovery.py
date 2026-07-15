from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment

from src.temporal_runtime.commands import CommandRequest
from src.temporal_runtime.worker import create_temporal_worker
from src.temporal_runtime.workflows import (
    ABCExecutionWorkflow,
    EXECUTE_SLICE_ACTIVITY,
    WorkflowStart,
)


def _test_server_path() -> str:
    raw = os.environ.get("ODYSSEUS_TEMPORAL_TEST_SERVER", "")
    if not raw:
        pytest.skip("ODYSSEUS_TEMPORAL_TEST_SERVER is required for offline SDK recovery tests")
    path = Path(raw)
    assert path.is_file(), "the configured Temporal SDK test server does not exist"
    return str(path)


def _manifest() -> dict[str, Any]:
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + "9" * 32,
        "manifest_hash": "sha256:" + "9" * 64,
        "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
        "max_parallel_activities": 1,
        "normalized_dag": {
            "nodes": [
                {
                    "node_id": "after-restart",
                    "kind": "repo_slice",
                    "depends_on": [],
                    "gate_ids": ["restart-gate"],
                    "verification_rule_ids": ["verify-after-restart"],
                }
            ],
            "edges": [],
            "gates": [
                {
                    "gate_id": "restart-gate",
                    "kind": "runtime",
                    "blocks": ["after-restart"],
                }
            ],
        },
    }


def _gate_command(run_version: int) -> CommandRequest:
    return CommandRequest.create(
        command_id="command-worker-restart-gate",
        command="decide_gate",
        expected_run_version=run_version,
        idempotency_key="idempotency-worker-restart-gate",
        payload={"gate_id": "restart-gate", "decision": "approved"},
    )


@activity.defn(name=EXECUTE_SLICE_ACTIVITY)
async def _execute_slice(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": payload["node_id"],
        "status": "succeeded",
        "evidence_verified": True,
        "writeback_receipt": "worker-restart:verified",
    }


async def _wait_for_state(handle, expected: str) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for _ in range(100):
        try:
            last = await handle.query("get_run_state")
        except Exception:
            await asyncio.sleep(0.05)
            continue
        if last["run_state"] == expected:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow did not reach {expected!r}; last={last!r}")


async def _phase(name: str, awaitable, *, timeout: float = 10.0):
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout)
    except TimeoutError as exc:
        raise AssertionError(f"phase_timeout:{name}") from exc
    print(f"PHASE_OK:{name}", flush=True)
    return result


async def _wait_for_worker_started(worker, run_task) -> None:
    for _ in range(200):
        if run_task.done():
            await run_task
        if worker._started:
            return
        await asyncio.sleep(0.025)
    raise AssertionError("worker did not enter its started state")


async def _cleanup_worker(worker, run_task) -> str | None:
    if worker is None or run_task is None:
        return None
    if not run_task.done():
        try:
            await asyncio.wait_for(worker.shutdown(), timeout=3)
        except (TimeoutError, asyncio.CancelledError):
            run_task.cancel()
    if not run_task.done():
        run_task.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(run_task, return_exceptions=True), timeout=3)
    except TimeoutError:
        return "worker_run_task"
    return None


@pytest.mark.asyncio
async def test_worker_restart_resumes_same_run_and_applies_gate_command_once():
    environment = await WorkflowEnvironment.start_time_skipping(
        test_server_existing_path=_test_server_path()
    )
    first_worker = None
    first_run_task = None
    second_worker = None
    second_run_task = None
    primary_error: Exception | None = None
    cleanup_errors: list[str] = []
    try:
        task_queue = "tlr08-worker-recovery"
        first_worker = create_temporal_worker(
            environment.client,
            task_queue=task_queue,
            activities=[_execute_slice],
            max_concurrent_activities=1,
        )
        first_run_task = asyncio.create_task(first_worker.run())
        await _phase(
            "first_worker_started",
            _wait_for_worker_started(first_worker, first_run_task),
        )
        handle = await _phase(
            "workflow_started",
            environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=_manifest()),
                id="tlr08-worker-restart",
                task_queue=task_queue,
            ),
        )
        initial_run_id = handle.result_run_id
        waiting = await _phase(
            "waiting_gate_observed",
            _wait_for_state(handle, "waiting_gate"),
        )
        await _phase("first_worker_shutdown", first_worker.shutdown())
        await _phase("first_worker_run_completed", first_run_task)

        second_worker = create_temporal_worker(
            environment.client,
            task_queue=task_queue,
            activities=[_execute_slice],
            max_concurrent_activities=1,
        )
        second_run_task = asyncio.create_task(second_worker.run())
        await _phase(
            "second_worker_started",
            _wait_for_worker_started(second_worker, second_run_task),
        )
        resumed = await _phase(
            "second_worker_ready_query",
            handle.query("get_run_state"),
        )
        assert resumed["run_state"] == "waiting_gate"
        assert resumed["run_version"] == waiting["run_version"]
        request = _gate_command(waiting["run_version"])
        receipt = await _phase(
            "gate_update_applied",
            handle.execute_update(
                "decide_gate",
                request,
                id="restart-client",
            ),
        )
        result = await _phase("workflow_result_received", handle.result())
        history = await _phase("history_received", handle.fetch_history())
        await _phase("second_worker_shutdown", second_worker.shutdown())
        await _phase("second_worker_run_completed", second_run_task)
    except Exception as exc:
        primary_error = exc
    finally:
        for worker, run_task in (
            (second_worker, second_run_task),
            (first_worker, first_run_task),
        ):
            cleanup_error = await _cleanup_worker(worker, run_task)
            if cleanup_error:
                cleanup_errors.append(cleanup_error)
        try:
            await asyncio.wait_for(environment.shutdown(), timeout=5)
            print("PHASE_OK:environment_shutdown", flush=True)
        except TimeoutError:
            cleanup_errors.append("environment_shutdown")

    if primary_error is not None:
        raise primary_error
    if cleanup_errors:
        raise AssertionError("cleanup_timeout:" + ",".join(cleanup_errors))

    assert receipt["result_code"] == "applied"
    assert initial_run_id == handle.result_run_id
    assert result["run_state"] == "completed"
    assert result["command_receipt_count"] == 1
    assert result["slice_states"] == {"after-restart": "succeeded"}
    assert result["receipt_store_cursor"] == "worker-restart:verified"
    workflow_tasks = [
        event
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED
    ]
    assert len(workflow_tasks) >= 2
