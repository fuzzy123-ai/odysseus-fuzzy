from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from src.temporal_runtime.config import load_temporal_light_config
from src.temporal_runtime.worker import (
    WorkflowRegistrationError,
    assert_deterministic_workflow_source,
    assert_registered_workflow_is_deterministic,
    create_temporal_worker,
)
from src.temporal_runtime.workflows import (
    ABCExecutionWorkflow,
    EXECUTE_SLICE_ACTIVITY,
    WorkflowCarryState,
    WorkflowStart,
)


def _node(node_id: str, *depends_on: str, gate_ids: tuple[str, ...] = ()) -> dict:
    return {
        "node_id": node_id,
        "kind": "repo_slice",
        "depends_on": list(depends_on),
        "gate_ids": list(gate_ids),
        "verification_rule_ids": [f"verify-{node_id}"],
    }


def _future_deadline(seconds: float = 30) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return value.isoformat().replace("+00:00", "Z")


def _manifest(
    suffix: str,
    nodes: list[dict],
    *,
    gates: list[dict] | None = None,
    parallelism: int = 2,
    deadline_at: str | None = None,
) -> dict:
    character = suffix[-1].lower()
    if character not in "0123456789abcdef":
        character = "d"
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + character * 32,
        "manifest_hash": "sha256:" + character * 64,
        "deadline_at": deadline_at or _future_deadline(),
        "max_parallel_activities": parallelism,
        "normalized_dag": {"nodes": nodes, "edges": [], "gates": gates or []},
    }


@activity.defn(name=EXECUTE_SLICE_ACTIVITY)
async def _fake_execute_slice(payload: dict[str, Any]) -> dict[str, Any]:
    node_id = payload["node_id"]
    return {
        "node_id": node_id,
        "status": "succeeded",
        "evidence_verified": True,
        "writeback_receipt": f"fake:{payload['history_segment']}:{node_id}",
    }


@pytest.mark.parametrize(
    ("source", "category"),
    [
        ("from pathlib import Path\ndef run(): return Path.cwd()", "filesystem"),
        ("import socket\ndef run(): return socket.socket()", "network"),
        ("import subprocess\ndef run(): return subprocess.run([])", "process"),
        ("from datetime import datetime\ndef run(): return datetime.now()", "wall-clock"),
        ("import random\ndef run(): return random.random()", "random"),
        ("counter = 0\ndef run():\n global counter\n counter += 1", "global-state"),
    ],
)
def test_worker_registration_rejects_forbidden_workflow_capabilities(source, category):
    with pytest.raises(WorkflowRegistrationError):
        assert_deterministic_workflow_source(source)

    assert category in {"filesystem", "network", "process", "wall-clock", "random", "global-state"}


def test_product_workflow_passes_registration_policy_and_has_no_private_path_constant():
    assert_registered_workflow_is_deterministic()
    source = Path("src/temporal_runtime/workflows.py").read_text(encoding="utf-8")
    assert "LOCALAPPDATA" not in source
    assert "subprocess" not in source
    assert "socket" not in source


async def _replay(history) -> None:
    await Replayer(workflows=[ABCExecutionWorkflow]).replay_workflow(history)


@pytest.mark.asyncio
async def test_local_histories_replay_for_dag_timer_pause_and_continue_as_new():
    config = load_temporal_light_config()
    assert config.cli_path.is_file(), "pinned Temporal CLI must be installed for TLR-03"
    histories = []

    async with await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        dev_server_existing_path=str(config.cli_path),
        dev_server_log_level="error",
    ) as environment:
        task_queue = "tlr03-determinism"
        async with create_temporal_worker(
            environment.client,
            task_queue=task_queue,
            activities=[_fake_execute_slice],
            max_concurrent_activities=3,
        ):
            chain = _manifest(
                "1",
                [_node("prepare"), _node("verify", "prepare"), _node("finish", "verify")],
                parallelism=1,
            )
            chain_handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=chain),
                id="tlr03-chain-replay",
                task_queue=task_queue,
            )
            chain_result = await chain_handle.result()
            chain_history = await chain_handle.fetch_history()
            histories.append(chain_history)
            assert chain_result["run_state"] == "completed"
            assert list(chain_result["slice_states"].values()) == ["succeeded"] * 3

            fanout = _manifest(
                "2",
                [_node("a"), _node("b"), _node("c"), _node("join", "a", "b", "c")],
                parallelism=3,
            )
            fanout_handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=fanout),
                id="tlr03-fanout-replay",
                task_queue=task_queue,
            )
            fanout_result = await fanout_handle.result()
            histories.append(await fanout_handle.fetch_history())
            assert fanout_result["run_state"] == "completed"

            gate = {"gate_id": "local-go", "kind": "runtime", "blocks": ["blocked"]}
            timer_manifest = _manifest(
                "3",
                [_node("blocked", gate_ids=("local-go",))],
                gates=[gate],
                deadline_at=_future_deadline(1.0),
            )
            timer_handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=timer_manifest),
                id="tlr03-gate-deadline-replay",
                task_queue=task_queue,
            )
            timer_result = await timer_handle.result()
            timer_history = await timer_handle.fetch_history()
            histories.append(timer_history)
            timer_event_types = {event.event_type for event in timer_history.events}
            assert timer_result["run_state"] == "timed_out"
            assert EventType.EVENT_TYPE_TIMER_STARTED in timer_event_types
            assert EventType.EVENT_TYPE_TIMER_FIRED in timer_event_types

            paused_manifest = _manifest(
                "4",
                [_node("paused-node")],
                deadline_at=_future_deadline(1.0),
            )
            paused_carry = WorkflowCarryState(
                agent_run_id=paused_manifest["agent_run_id"],
                manifest_hash=paused_manifest["manifest_hash"],
                run_state="paused",
                run_version=3,
                slice_states={"paused-node": "pending"},
                gate_states={},
                deadline_at=paused_manifest["deadline_at"],
                history_segment=0,
                event_cursor=3,
                receipt_store_cursor="",
            )
            paused_handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=paused_manifest, carry=paused_carry),
                id="tlr03-paused-deadline-replay",
                task_queue=task_queue,
            )
            paused_result = await paused_handle.result()
            paused_history = await paused_handle.fetch_history()
            histories.append(paused_history)
            assert paused_result["run_state"] == "timed_out"
            assert EventType.EVENT_TYPE_TIMER_FIRED in {
                event.event_type for event in paused_history.events
            }

            continued_manifest = _manifest(
                "5",
                [_node("first"), _node("second", "first")],
                parallelism=1,
            )
            continued_carry = WorkflowCarryState(
                agent_run_id=continued_manifest["agent_run_id"],
                manifest_hash=continued_manifest["manifest_hash"],
                run_state="running",
                run_version=2,
                slice_states={"first": "pending", "second": "pending"},
                gate_states={},
                deadline_at=continued_manifest["deadline_at"],
                history_segment=0,
                event_cursor=2,
                receipt_store_cursor="",
                projected_event_count=1_999,
            )
            continue_handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=continued_manifest, carry=continued_carry),
                id="tlr03-continue-as-new-replay",
                task_queue=task_queue,
            )
            initial_run_id = continue_handle.result_run_id
            assert initial_run_id is not None
            continue_result = await continue_handle.result()
            initial_history = await environment.client.get_workflow_handle(
                "tlr03-continue-as-new-replay", run_id=initial_run_id
            ).fetch_history()
            histories.append(initial_history)
            assert continue_result["run_state"] == "completed"
            assert continue_result["history_segment"] == 1
            assert continue_result["agent_run_id"] == continued_manifest["agent_run_id"]
            assert continue_result["manifest_hash"] == continued_manifest["manifest_hash"]
            assert (
                initial_history.events[-1].event_type
                == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW
            )
            next_run_id = (
                initial_history.events[-1]
                .workflow_execution_continued_as_new_event_attributes.new_execution_run_id
            )
            continued_history = await environment.client.get_workflow_handle(
                "tlr03-continue-as-new-replay", run_id=next_run_id
            ).fetch_history()
            histories.append(continued_history)

        for history in histories:
            await _replay(history)

    assert environment.client.service_client.config.target_host.startswith("127.0.0.1:")
