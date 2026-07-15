from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import inspect
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment

from src.temporal_runtime.commands import (
    CommandRequest,
    ExternalConditionSignal,
    OperatorNoteSignal,
)
from src.temporal_runtime.config import load_temporal_light_config
from src.temporal_runtime.worker import create_temporal_worker
from src.temporal_runtime.workflows import (
    ABCExecutionWorkflow,
    EXECUTE_SLICE_ACTIVITY,
    WorkflowStart,
)


class ControlledActivity:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cancel_probe = asyncio.Event()

    @activity.defn(name=EXECUTE_SLICE_ACTIVITY)
    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.started.set()
        release_wait = asyncio.create_task(self.release.wait())
        cancel_wait = asyncio.create_task(activity.wait_for_cancelled())
        probe_wait = asyncio.create_task(self.cancel_probe.wait())
        try:
            done, _ = await asyncio.wait(
                (release_wait, cancel_wait, probe_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if probe_wait in done:
                activity.heartbeat({"phase": "cancellation_probe"})
                await cancel_wait
                self.cancelled.set()
                raise asyncio.CancelledError
            if cancel_wait in done:
                self.cancelled.set()
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            release_wait.cancel()
            cancel_wait.cancel()
            probe_wait.cancel()
        return {
            "node_id": payload["node_id"],
            "status": "succeeded",
            "evidence_verified": True,
            "writeback_receipt": f"fake:{payload['node_id']}",
        }


def _manifest(character: str) -> dict:
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + character * 32,
        "manifest_hash": "sha256:" + character * 64,
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=45))
        .isoformat()
        .replace("+00:00", "Z"),
        "max_parallel_activities": 1,
        "normalized_dag": {
            "nodes": [
                {
                    "node_id": "node-one",
                    "kind": "repo_slice",
                    "depends_on": [],
                    "gate_ids": [],
                    "verification_rule_ids": ["verify-node-one"],
                }
            ],
            "edges": [],
            "gates": [],
        },
    }


def _request(
    command: str,
    version: int,
    suffix: str,
    payload: dict[str, Any] | None = None,
) -> CommandRequest:
    return CommandRequest.create(
        command_id=f"command-{suffix}",
        command=command,
        expected_run_version=version,
        idempotency_key=f"idempotency-{suffix}",
        payload=payload or {},
    )


def _has_history_field(history, field: str) -> bool:
    return any(event.HasField(field) for event in history.events)


@pytest.mark.asyncio
async def test_real_updates_query_signals_duplicate_stale_and_structural_steer():
    config = load_temporal_light_config()
    controlled = ControlledActivity()
    async with await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        dev_server_existing_path=str(config.cli_path),
        dev_server_log_level="error",
    ) as environment:
        async with create_temporal_worker(
            environment.client,
            task_queue="tlr05-messages",
            activities=[controlled.execute],
            max_concurrent_activities=1,
        ):
            handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=_manifest("e")),
                id="tlr05-message-contract",
                task_queue="tlr05-messages",
            )
            await asyncio.wait_for(controlled.started.wait(), timeout=10)
            running = await handle.query("get_run_state")
            assert running["run_state"] == "running"

            pause = _request("pause", running["run_version"], "shared-pause")
            first, second = await asyncio.gather(
                handle.execute_update("pause", pause, id="client-a-pause"),
                handle.execute_update("pause", pause, id="client-b-pause"),
            )
            assert first == second
            paused = await handle.query("get_run_state")
            assert paused["run_state"] == "paused"
            assert paused["command_receipt_count"] == 1

            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(
                    "resume",
                    _request("resume", running["run_version"], "stale-resume"),
                    id="client-stale-resume",
                )
            after_stale = await handle.query("get_run_state")
            assert after_stale == paused

            structural = await handle.execute_update(
                "steer_run",
                _request(
                    "steer_run",
                    paused["run_version"],
                    "structural",
                    {"normalized_dag": {"nodes": []}},
                ),
                id="client-structural-steer",
            )
            assert structural["result_code"] == "requires_plan_revision"
            after_structural = await handle.query("get_run_state")
            assert after_structural["manifest_hash"] == running["manifest_hash"]
            assert after_structural["run_version"] == paused["run_version"]

            await handle.signal(
                "operator_note",
                OperatorNoteSignal.create(note_id="note-one", note_ref="note-ref-one"),
            )
            await handle.signal(
                "external_condition_changed",
                ExternalConditionSignal.create(condition_ref="condition-one"),
            )
            signalled = await handle.query("get_run_state")
            assert signalled["operator_notes"] == [
                {"note_id": "note-one", "note_ref": "note-ref-one"}
            ]
            assert signalled["external_condition_revisions"] == {"condition-one": 1}
            assert signalled["run_state"] == "paused"

            controlled.release.set()
            resumed = await handle.execute_update(
                "resume",
                _request("resume", signalled["run_version"], "valid-resume"),
                id="client-valid-resume",
            )
            result = await handle.result()
            history = await handle.fetch_history()

    assert resumed["result_code"] == "applied"
    assert result["run_state"] == "completed"
    assert result["command_receipt_count"] == 3
    assert _has_history_field(history, "workflow_execution_update_accepted_event_attributes")
    assert not _has_history_field(history, "workflow_execution_update_rejected_event_attributes")
    assert _has_history_field(history, "workflow_execution_signaled_event_attributes")


@pytest.mark.asyncio
async def test_real_cancel_update_cancels_active_activity_and_finishes_handlers():
    config = load_temporal_light_config()
    controlled = ControlledActivity()
    async with await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        dev_server_existing_path=str(config.cli_path),
        dev_server_log_level="error",
    ) as environment:
        async with create_temporal_worker(
            environment.client,
            task_queue="tlr05-cancel",
            activities=[controlled.execute],
            max_concurrent_activities=1,
        ):
            handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=_manifest("f")),
                id="tlr05-cancel-contract",
                task_queue="tlr05-cancel",
            )
            await asyncio.wait_for(controlled.started.wait(), timeout=10)
            running = await handle.query("get_run_state")
            try:
                receipt = await asyncio.wait_for(
                    handle.execute_update(
                        "cancel",
                        _request("cancel", running["run_version"], "cancel"),
                        id="client-cancel",
                    ),
                    timeout=10,
                )
            except BaseException:
                controlled.release.set()
                raise
            controlled.cancel_probe.set()
            try:
                result = await asyncio.wait_for(handle.result(), timeout=10)
            finally:
                controlled.release.set()
            history = await handle.fetch_history()

    assert receipt["state"] == "cancelling"
    assert result["run_state"] == "cancelled"
    assert result["command_receipt_count"] == 1
    assert controlled.cancelled.is_set()
    assert _has_history_field(history, "workflow_execution_update_completed_event_attributes")


def test_workflow_waits_for_handlers_before_continue_as_new_and_return():
    source = inspect.getsource(ABCExecutionWorkflow.run)
    assert "await self._wait_for_handlers()\n                workflow.continue_as_new" in source
    assert "await self._wait_for_handlers()\n        return state.projection()" in source
