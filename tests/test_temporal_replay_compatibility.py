from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from src.temporal_runtime.worker import create_temporal_worker
from src.temporal_runtime.workflows import (
    ABCExecutionWorkflow,
    EXECUTE_SLICE_ACTIVITY,
    WorkflowCarryState,
    WorkflowStart,
)


def _test_server_path() -> str:
    raw = os.environ.get("ODYSSEUS_TEMPORAL_TEST_SERVER", "")
    if not raw:
        pytest.skip("ODYSSEUS_TEMPORAL_TEST_SERVER is required for offline SDK recovery tests")
    path = Path(raw)
    assert path.is_file(), "the configured Temporal SDK test server does not exist"
    return str(path)


def _deadline(seconds: int = 120) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace(
        "+00:00", "Z"
    )


def _manifest() -> dict[str, Any]:
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + "8" * 32,
        "manifest_hash": "sha256:" + "8" * 64,
        "deadline_at": _deadline(),
        "max_parallel_activities": 1,
        "normalized_dag": {
            "nodes": [
                {
                    "node_id": "first",
                    "kind": "repo_slice",
                    "depends_on": [],
                    "gate_ids": [],
                    "verification_rule_ids": ["verify-first"],
                },
                {
                    "node_id": "second",
                    "kind": "repo_slice",
                    "depends_on": ["first"],
                    "gate_ids": [],
                    "verification_rule_ids": ["verify-second"],
                },
            ],
            "edges": [{"from": "first", "to": "second"}],
            "gates": [],
        },
    }


@activity.defn(name=EXECUTE_SLICE_ACTIVITY)
async def _execute_slice(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": payload["node_id"],
        "status": "succeeded",
        "evidence_verified": True,
        "writeback_receipt": (
            f"recovery:{payload['history_segment']}:{payload['node_id']}"
        ),
    }


@pytest.mark.asyncio
async def test_continue_as_new_histories_replay_and_preserve_one_logical_run():
    manifest = _manifest()
    carry = WorkflowCarryState(
        agent_run_id=manifest["agent_run_id"],
        manifest_hash=manifest["manifest_hash"],
        run_state="running",
        run_version=2,
        slice_states={"first": "pending", "second": "pending"},
        gate_states={},
        deadline_at=manifest["deadline_at"],
        history_segment=0,
        event_cursor=2,
        receipt_store_cursor="",
        projected_event_count=1_999,
    )

    async with await WorkflowEnvironment.start_time_skipping(
        test_server_existing_path=_test_server_path()
    ) as environment:
        task_queue = "tlr08-replay-compatibility"
        async with create_temporal_worker(
            environment.client,
            task_queue=task_queue,
            activities=[_execute_slice],
            max_concurrent_activities=1,
        ):
            handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=manifest, carry=carry),
                id="tlr08-replay-logical-run",
                task_queue=task_queue,
            )
            initial_run_id = handle.result_run_id
            assert initial_run_id
            result = await handle.result()

            initial_history = await environment.client.get_workflow_handle(
                handle.id, run_id=initial_run_id
            ).fetch_history()
            assert (
                initial_history.events[-1].event_type
                == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW
            )
            continued_run_id = (
                initial_history.events[-1]
                .workflow_execution_continued_as_new_event_attributes.new_execution_run_id
            )
            continued_history = await environment.client.get_workflow_handle(
                handle.id, run_id=continued_run_id
            ).fetch_history()

        await Replayer(workflows=[ABCExecutionWorkflow]).replay_workflow(initial_history)
        await Replayer(workflows=[ABCExecutionWorkflow]).replay_workflow(continued_history)

    assert result["run_state"] == "completed"
    assert result["agent_run_id"] == manifest["agent_run_id"]
    assert result["manifest_hash"] == manifest["manifest_hash"]
    assert result["history_segment"] == 1
    assert result["event_cursor"] > carry.event_cursor
    assert result["slice_states"] == {"first": "succeeded", "second": "succeeded"}
    assert result["receipt_store_cursor"] == "recovery:1:second"
