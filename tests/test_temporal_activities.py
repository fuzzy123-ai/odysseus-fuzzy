from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment

from src.headless_write_agent_state import (
    AdmissionLimits,
    AuthorityScope,
    HeadlessWriteAgentStateStore,
)
from src.temporal_runtime.activities import (
    ACTIVITY_CATALOG,
    HEARTBEAT_PAYLOAD_MAX_BYTES,
    ActivityLogicalName,
    FakeIsolatedActivityBackend,
    IsolatedActivityBackend,
    IsolatedBackendError,
    IsolatedExecutionResult,
    SliceInvocation,
    TemporalLightActivities,
)
from src.temporal_runtime.authority_adapter import (
    ActivityAuthorityAdapter,
    ActivitySpecRegistry,
    RegisteredActivitySpec,
)
from src.temporal_runtime.config import load_temporal_light_config
from src.temporal_runtime.worker import (
    ACTIVITY_CANCELLATION_GRACE,
    ACTIVITY_HEARTBEAT_THROTTLE,
    TEMPORAL_LIGHT_MAX_CACHED_WORKFLOWS,
    create_temporal_worker,
)
from src.temporal_runtime.workflows import ABCExecutionWorkflow, WorkflowStart


def _scope(run: str) -> AuthorityScope:
    return AuthorityScope.create(
        owner_id="owner-local",
        repo_id="repo-odysseus",
        task_id="task-tlr04",
        plan_id="plan-temporal-light",
        slice_id="slice-node-one",
        agent_run_id=run,
    )


def _build(tmp_path, backend: IsolatedActivityBackend):
    run = "arun-" + "c" * 32
    scope = _scope(run)
    spec = RegisteredActivitySpec.create(
        agent_run_id=run,
        node_id="node-one",
        manifest_hash="sha256:" + "c" * 64,
        scope=scope,
        backend_id=backend.backend_id,
        claimant_ref="temporal-local-worker",
        claimed_paths=("src/example.py",),
        hotfiles=("src/example.py",),
        admission_limits=AdmissionLimits.create(
            max_global_active=3,
            max_owner_active=3,
            max_project_active=3,
            max_agent_active=3,
        ),
    )
    store = HeadlessWriteAgentStateStore(tmp_path / "authority.sqlite3")
    authority = ActivityAuthorityAdapter(store, ActivitySpecRegistry((spec,)))
    payload = {
        "agent_run_id": run,
        "manifest_hash": spec.manifest_hash,
        "node_id": spec.node_id,
        "history_segment": 1,
    }
    return store, spec, payload, TemporalLightActivities(authority, (backend,))


class FailingBackend(IsolatedActivityBackend):
    backend_id = "fake-failing"

    async def execute(self, invocation, checkpoint):
        await checkpoint("executing", 1, None)
        raise IsolatedBackendError("transient_isolated_error", retryable=True)


class WaitingBackend(IsolatedActivityBackend):
    backend_id = "fake-waiting"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(self, invocation, checkpoint):
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class SecretArtifactBackend(IsolatedActivityBackend):
    backend_id = "fake-secret-artifact"

    async def execute(self, invocation, checkpoint):
        return IsolatedExecutionResult(artifact_ref="secret-token")


class RetryOnceBackend(IsolatedActivityBackend):
    backend_id = "fake-retry-once"

    def __init__(self) -> None:
        self.attempts: list[int] = []

    async def execute(self, invocation: SliceInvocation, checkpoint):
        self.attempts.append(invocation.attempt)
        await checkpoint("executing", invocation.attempt, None)
        if invocation.attempt == 1:
            raise IsolatedBackendError("transient_isolated_error", retryable=True)
        return IsolatedExecutionResult(artifact_ref="artifact:node-one")


def test_catalog_contains_exact_seven_typed_activity_contracts():
    assert tuple(item.logical_name for item in ACTIVITY_CATALOG) == tuple(ActivityLogicalName)
    assert len(ACTIVITY_CATALOG) == 7
    assert {item.idempotency_key for item in ACTIVITY_CATALOG}
    assert ACTIVITY_HEARTBEAT_THROTTLE == timedelta(seconds=30)
    assert ACTIVITY_CANCELLATION_GRACE == timedelta(seconds=300)
    assert TEMPORAL_LIGHT_MAX_CACHED_WORKFLOWS == 0


@pytest.mark.asyncio
async def test_fake_execution_heartbeats_safe_fields_releases_and_deduplicates(tmp_path):
    backend = FakeIsolatedActivityBackend()
    store, spec, payload, activities = _build(tmp_path, backend)
    heartbeats: list[dict] = []

    first = await activities.execute_for_test(
        payload,
        attempt=1,
        activity_id="activity-one",
        heartbeat_sink=lambda value: heartbeats.append(dict(value)),
    )
    second = await activities.execute_for_test(
        payload,
        attempt=1,
        activity_id="activity-duplicate",
        heartbeat_sink=lambda value: heartbeats.append(dict(value)),
    )

    assert first == second
    assert backend.execution_count == 1
    assert store.get_claim(spec.scope).state == "released"
    assert store.get_effect(f"{spec.agent_run_id}:node-one:execute_slice:1").status == "succeeded"
    assert len(heartbeats) >= 4
    assert set(heartbeats[0]) == {
        "activity_id",
        "node_id",
        "attempt",
        "phase",
        "progress_cursor",
        "last_durable_artifact_ref",
        "lease_revision",
        "observed_at",
    }
    assert max(len(json.dumps(item).encode("utf-8")) for item in heartbeats) < HEARTBEAT_PAYLOAD_MAX_BYTES
    assert "secret" not in json.dumps(heartbeats).lower()


@pytest.mark.asyncio
async def test_retryable_failure_persists_receipt_and_releases_claim(tmp_path):
    store, spec, payload, activities = _build(tmp_path, FailingBackend())

    with pytest.raises(ApplicationError) as raised:
        await activities.execute_for_test(
            payload,
            attempt=1,
            activity_id="activity-failure",
            heartbeat_sink=lambda value: None,
        )

    assert raised.value.type == "transient_isolated_error"
    assert raised.value.non_retryable is False
    assert store.get_claim(spec.scope).state == "released"
    effect = store.get_effect(f"{spec.agent_run_id}:node-one:execute_slice:1")
    assert effect.status == "failed"
    assert effect.failure_code == "transient_isolated_error"


@pytest.mark.asyncio
async def test_cancellation_persists_receipt_and_releases_claim(tmp_path):
    backend = WaitingBackend()
    store, spec, payload, activities = _build(tmp_path, backend)
    task = asyncio.create_task(
        activities.execute_for_test(
            payload,
            attempt=1,
            activity_id="activity-cancel",
            heartbeat_sink=lambda value: None,
        )
    )
    await backend.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.get_claim(spec.scope).state == "released"
    effect = store.get_effect(f"{spec.agent_run_id}:node-one:execute_slice:1")
    assert effect.status == "cancelled"
    assert effect.failure_code == "cancelled_by_operator"


@pytest.mark.asyncio
async def test_secret_like_artifact_never_enters_heartbeat_or_receipt(tmp_path):
    store, spec, payload, activities = _build(tmp_path, SecretArtifactBackend())
    heartbeats: list[dict] = []

    with pytest.raises(ApplicationError) as raised:
        await activities.execute_for_test(
            payload,
            attempt=1,
            activity_id="activity-redaction",
            heartbeat_sink=lambda value: heartbeats.append(dict(value)),
        )

    assert raised.value.type == "secret_detected"
    assert raised.value.non_retryable is True
    assert "secret-token" not in json.dumps(heartbeats)
    effect = store.get_effect(f"{spec.agent_run_id}:node-one:execute_slice:1")
    assert effect.result_ref is None
    assert effect.failure_code == "secret_detected"


def _manifest() -> dict:
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + "c" * 32,
        "manifest_hash": "sha256:" + "c" * 64,
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=40))
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


@pytest.mark.asyncio
async def test_local_temporal_retry_gets_new_fence_and_completes(tmp_path):
    config = load_temporal_light_config()
    backend = RetryOnceBackend()
    store, spec, _, activities = _build(tmp_path, backend)

    async with await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        dev_server_existing_path=str(config.cli_path),
        dev_server_log_level="error",
    ) as environment:
        async with create_temporal_worker(
            environment.client,
            task_queue="tlr04-activities",
            activities=[activities.execute_slice],
            max_concurrent_activities=1,
        ):
            handle = await environment.client.start_workflow(
                ABCExecutionWorkflow.run,
                WorkflowStart(manifest=_manifest()),
                id="tlr04-retry-fence",
                task_queue="tlr04-activities",
            )
            result = await handle.result()
            history = await handle.fetch_history()

    assert result["run_state"] == "completed"
    assert backend.attempts == [1, 2]
    assert store.get_claim(spec.scope).fence == 2
    assert store.get_claim(spec.scope).state == "released"
    first = store.get_effect(f"{spec.agent_run_id}:node-one:execute_slice:1")
    second = store.get_effect(f"{spec.agent_run_id}:node-one:execute_slice:2")
    assert (first.status, first.lease_fence) == ("failed", 1)
    assert (second.status, second.lease_fence) == ("succeeded", 2)
    scheduled = next(
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    )
    assert scheduled.heartbeat_timeout.seconds == 90
    assert scheduled.start_to_close_timeout.seconds == 5_400
    assert scheduled.schedule_to_close_timeout.seconds == 10_800
    assert scheduled.retry_policy.maximum_attempts == 3
    assert scheduled.retry_policy.initial_interval.seconds == 5
    assert scheduled.retry_policy.maximum_interval.seconds == 300
    assert scheduled.retry_policy.backoff_coefficient == 2.0
