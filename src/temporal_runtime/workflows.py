"""Replay-safe Temporal workflow for one immutable ABC execution manifest.

This module is deliberately self-contained.  Workflow code may use Temporal's
deterministic clock, timers and task primitives, but it must never reach into an
Odysseus authority store or perform filesystem, network or process I/O.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from temporalio import workflow
from temporalio.common import RetryPolicy


WORKFLOW_NAME = "OdysseusABCExecutionWorkflow"
EXECUTE_SLICE_ACTIVITY = "odysseus.temporal_light.execute_slice"
ACTIVITY_NON_RETRYABLE_ERROR_TYPES = (
    "scope_violation",
    "owner_mismatch",
    "plan_revision_conflict",
    "claim_collision",
    "stale_fence",
    "live_go_missing",
    "secret_detected",
    "invalid_manifest",
    "cancelled_by_operator",
)
EXECUTION_MANIFEST_SCHEMA_ID = "odysseus.abc.execution_manifest.v1"
MAX_PROJECTED_EVENTS_PER_SEGMENT = 2_000
MAX_SEGMENT_SECONDS = 6 * 60 * 60
RUN_STATES = (
    "queued",
    "starting",
    "running",
    "waiting_gate",
    "waiting_signal",
    "paused",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "timed_out",
    "terminated",
)
TERMINAL_RUN_STATES = ("cancelled", "completed", "failed", "timed_out", "terminated")
SLICE_STATES = (
    "pending",
    "claiming",
    "activity_scheduled",
    "activity_running",
    "waiting_gate",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
)
ACTIVE_SLICE_STATES = ("claiming", "activity_scheduled", "activity_running")


class WorkflowContractError(ValueError):
    """A deterministic manifest, transition or Activity-result violation."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class WorkflowCarryState:
    """State transferred verbatim between Continue-As-New history segments."""

    agent_run_id: str
    manifest_hash: str
    run_state: str
    run_version: int
    slice_states: dict[str, str]
    gate_states: dict[str, str]
    deadline_at: str
    history_segment: int
    event_cursor: int
    receipt_store_cursor: str
    projected_event_count: int = 0


@dataclass(frozen=True)
class WorkflowStart:
    """Immutable manifest plus an optional Temporal-owned continuation state."""

    manifest: dict[str, Any]
    carry: WorkflowCarryState | None = None


@dataclass(frozen=True)
class DagNode:
    node_id: str
    depends_on: tuple[str, ...]
    gate_ids: tuple[str, ...]
    verification_rule_ids: tuple[str, ...]


class DeterministicDagState:
    """Pure state machine used by the sandboxed workflow and replay tests."""

    def __init__(self, start: WorkflowStart) -> None:
        manifest = _validate_manifest(start.manifest)
        self.manifest = manifest
        self.agent_run_id = _bounded_string(manifest.get("agent_run_id"), "agent_run_id")
        self.manifest_hash = _manifest_hash(manifest.get("manifest_hash"))
        self.deadline_at = _deadline(manifest.get("deadline_at"))
        self.max_parallel_activities = _parallelism(manifest.get("max_parallel_activities"))
        self.nodes, gate_ids = _validated_dag(manifest.get("normalized_dag"))

        carry = start.carry
        if carry is None:
            self.run_state = "queued"
            self.run_version = 0
            self.slice_states = {node_id: "pending" for node_id in self.nodes}
            self.gate_states = {gate_id: "pending" for gate_id in gate_ids}
            self.history_segment = 0
            self.event_cursor = 0
            self.receipt_store_cursor = ""
            self.projected_event_count = 0
        else:
            _validate_carry(carry, self, gate_ids)
            self.run_state = carry.run_state
            self.run_version = carry.run_version
            self.slice_states = dict(carry.slice_states)
            self.gate_states = dict(carry.gate_states)
            self.history_segment = carry.history_segment
            self.event_cursor = carry.event_cursor
            self.receipt_store_cursor = carry.receipt_store_cursor
            self.projected_event_count = carry.projected_event_count

    def transition(self, event: str) -> str:
        current = self.run_state
        target: str | None = None
        if current == "queued" and event == "workflow_started":
            target = "starting"
        elif current == "starting" and event == "manifest_accepted":
            target = "running"
        elif current == "starting" and event == "manifest_rejected":
            target = "failed"
        elif current == "running" and event == "unsatisfied_runtime_gate":
            target = "waiting_gate"
        elif current == "running" and event == "explicit_external_input_required":
            target = "waiting_signal"
        elif current == "running" and event == "pause_update_applied":
            target = "paused"
        elif current == "waiting_gate" and event == "gate_decision_approved":
            target = "running"
        elif current == "waiting_gate" and event == "gate_decision_rejected_when_required":
            target = "failed"
        elif current == "waiting_signal" and event == "steering_signal_applied":
            target = "running"
        elif current == "paused" and event == "resume_update_applied":
            target = "running"
        elif current in ("running", "waiting_gate", "waiting_signal", "paused") and event == "cancel_update_applied":
            target = "cancelling"
        elif current == "cancelling" and event == "activities_cancelled":
            target = "cancelled"
        elif current == "running" and event == "verified_completion" and self.is_complete():
            target = "completed"
        elif current in ("running", "waiting_gate", "waiting_signal", "paused") and event == "deadline_reached":
            target = "timed_out"
        elif current in ("starting", "running", "waiting_gate", "waiting_signal", "paused", "cancelling") and event == "unrecoverable_error":
            target = "failed"
        elif current not in TERMINAL_RUN_STATES and event == "admin_terminated":
            target = "terminated"
        if target is None:
            raise WorkflowContractError(
                "invalid_transition",
                f"event {event!r} is not allowed from {current!r} at run version {self.run_version}",
            )
        self.run_state = target
        self.run_version += 1
        self._record_event()
        return target

    def frontier(self) -> tuple[str, ...]:
        if self.run_state != "running":
            return ()
        capacity = self.max_parallel_activities - sum(
            state in ACTIVE_SLICE_STATES for state in self.slice_states.values()
        )
        if capacity <= 0:
            return ()
        ready: list[str] = []
        for node_id in sorted(self.nodes):
            if self.slice_states[node_id] != "pending":
                continue
            node = self.nodes[node_id]
            if any(self.slice_states[dependency] != "succeeded" for dependency in node.depends_on):
                continue
            if any(self.gate_states[gate_id] != "approved" for gate_id in node.gate_ids):
                continue
            ready.append(node_id)
        return tuple(ready[:capacity])

    def unsatisfied_ready_gates(self) -> tuple[str, ...]:
        waiting: set[str] = set()
        for node_id in sorted(self.nodes):
            if self.slice_states[node_id] != "pending":
                continue
            node = self.nodes[node_id]
            if any(self.slice_states[dependency] != "succeeded" for dependency in node.depends_on):
                continue
            waiting.update(
                gate_id for gate_id in node.gate_ids if self.gate_states[gate_id] != "approved"
            )
        return tuple(sorted(waiting))

    def schedule(self, node_id: str) -> None:
        self.transition_slice(node_id, "claim_granted")
        self.transition_slice(node_id, "activity_scheduled")

    def mark_running(self, node_id: str) -> None:
        self.transition_slice(node_id, "activity_started")

    def transition_slice(self, node_id: str, event: str) -> str:
        current = self.slice_states.get(node_id)
        target: str | None = None
        if current == "pending" and event == "claim_granted":
            target = "claiming"
        elif current == "pending" and event == "dependency_failed":
            target = "skipped"
        elif current == "claiming" and event == "activity_scheduled":
            target = "activity_scheduled"
        elif current == "claiming" and event == "claim_gate_required":
            target = "waiting_gate"
        elif current == "activity_scheduled" and event == "activity_started":
            target = "activity_running"
        elif current in ("activity_scheduled", "activity_running") and event == "activity_cancelled":
            target = "cancelled"
        elif current == "activity_running" and event == "verified_result":
            target = "succeeded"
        elif current == "activity_running" and event == "retryable_failure":
            target = "retry_wait"
        elif current == "activity_running" and event == "terminal_failure":
            target = "failed"
        elif current == "retry_wait" and event == "retry_due":
            target = "claiming"
        elif current == "waiting_gate" and event == "gate_approved":
            target = "pending"
        elif current == "waiting_gate" and event == "gate_rejected":
            target = "failed"
        if target is None:
            raise WorkflowContractError(
                "invalid_slice_transition",
                f"event {event!r} is not allowed for {node_id!r} from {current!r}",
            )
        self.slice_states[node_id] = target
        self._record_event()
        return target

    def apply_activity_result(self, node_id: str, result: Mapping[str, Any]) -> None:
        if self.slice_states.get(node_id) != "activity_running":
            raise WorkflowContractError("invalid_slice_transition", f"{node_id} is not running")
        if result.get("node_id") != node_id:
            raise WorkflowContractError("activity_result_mismatch", f"result does not belong to {node_id}")
        status = result.get("status")
        if status == "succeeded":
            receipt = result.get("writeback_receipt")
            if result.get("evidence_verified") is not True or not isinstance(receipt, str) or not receipt:
                raise WorkflowContractError(
                    "unverified_activity_result",
                    f"{node_id} has no verified evidence and writeback receipt",
                )
            self.receipt_store_cursor = receipt
            self.transition_slice(node_id, "verified_result")
            return
        if status == "failed":
            self.transition_slice(node_id, "terminal_failure")
            return
        raise WorkflowContractError("invalid_activity_result", f"unsupported status for {node_id}")

    def is_complete(self) -> bool:
        required_nodes_done = all(state in ("succeeded", "skipped") for state in self.slice_states.values())
        gates_done = all(state in ("approved", "waived") for state in self.gate_states.values())
        return bool(self.nodes) and required_nodes_done and gates_done and bool(self.receipt_store_cursor)

    def to_continue_start(self) -> WorkflowStart:
        if self.run_state in TERMINAL_RUN_STATES:
            raise WorkflowContractError("terminal_continue_as_new", "terminal workflow cannot continue")
        carry = WorkflowCarryState(
            agent_run_id=self.agent_run_id,
            manifest_hash=self.manifest_hash,
            run_state=self.run_state,
            run_version=self.run_version,
            slice_states=dict(self.slice_states),
            gate_states=dict(self.gate_states),
            deadline_at=self.deadline_at,
            history_segment=self.history_segment + 1,
            event_cursor=self.event_cursor,
            receipt_store_cursor=self.receipt_store_cursor,
            projected_event_count=0,
        )
        return WorkflowStart(manifest=dict(self.manifest), carry=carry)

    def projection(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "manifest_hash": self.manifest_hash,
            "run_state": self.run_state,
            "run_version": self.run_version,
            "slice_states": dict(sorted(self.slice_states.items())),
            "gate_states": dict(sorted(self.gate_states.items())),
            "deadline_at": self.deadline_at,
            "history_segment": self.history_segment,
            "event_cursor": self.event_cursor,
            "receipt_store_cursor": self.receipt_store_cursor,
        }

    def _record_event(self) -> None:
        self.event_cursor += 1
        self.projected_event_count += 1


@workflow.defn(name=WORKFLOW_NAME)
class ABCExecutionWorkflow:
    """Temporal-owned deterministic orchestration over Activity boundaries."""

    def __init__(self) -> None:
        self._state: DeterministicDagState | None = None
        self._segment_started_at = 0.0

    @workflow.run
    async def run(self, start: WorkflowStart) -> dict[str, Any]:
        state = DeterministicDagState(start)
        self._state = state
        self._segment_started_at = workflow.time()
        if start.carry is None:
            state.transition("workflow_started")
            state.transition("manifest_accepted")

        while state.run_state not in TERMINAL_RUN_STATES:
            if _deadline_reached(state.deadline_at):
                state.transition("deadline_reached")
                break
            if state.run_state != "running":
                await self._wait_until_running_or_deadline()
                continue

            frontier = state.frontier()
            if not frontier:
                if state.is_complete():
                    state.transition("verified_completion")
                    break
                if state.unsatisfied_ready_gates():
                    state.transition("unsatisfied_runtime_gate")
                    continue
                state.transition("unrecoverable_error")
                break

            handles = []
            for node_id in frontier:
                state.schedule(node_id)
                handles.append(
                    workflow.execute_activity(
                        EXECUTE_SLICE_ACTIVITY,
                        {
                            "agent_run_id": state.agent_run_id,
                            "manifest_hash": state.manifest_hash,
                            "node_id": node_id,
                            "history_segment": state.history_segment,
                        },
                        schedule_to_close_timeout=timedelta(seconds=10_800),
                        start_to_close_timeout=timedelta(seconds=5_400),
                        heartbeat_timeout=timedelta(seconds=90),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=5),
                            backoff_coefficient=2.0,
                            maximum_interval=timedelta(seconds=300),
                            maximum_attempts=3,
                            non_retryable_error_types=ACTIVITY_NON_RETRYABLE_ERROR_TYPES,
                        ),
                    )
                )
                state.mark_running(node_id)

            results = await asyncio.gather(*handles)
            for node_id, result in zip(frontier, results):
                if not isinstance(result, Mapping):
                    raise WorkflowContractError("invalid_activity_result", f"{node_id} result is not an object")
                state.apply_activity_result(node_id, result)
            if any(state.slice_states[node_id] == "failed" for node_id in frontier):
                state.transition("unrecoverable_error")
                break
            if self._should_continue_as_new(state):
                workflow.continue_as_new(state.to_continue_start())

        return state.projection()

    async def _wait_until_running_or_deadline(self) -> None:
        state = self._required_state()
        remaining = _remaining_seconds(state.deadline_at)
        if remaining <= 0:
            state.transition("deadline_reached")
            return
        try:
            await workflow.wait_condition(
                lambda: state.run_state == "running",
                timeout=remaining,
                timeout_summary="abc-run-deadline",
            )
        except asyncio.TimeoutError:
            state.transition("deadline_reached")

    def _should_continue_as_new(self, state: DeterministicDagState) -> bool:
        if state.run_state in TERMINAL_RUN_STATES or state.is_complete():
            return False
        return (
            state.projected_event_count >= MAX_PROJECTED_EVENTS_PER_SEGMENT
            or workflow.time() - self._segment_started_at >= MAX_SEGMENT_SECONDS
            or workflow.info().is_continue_as_new_suggested()
        )

    def _required_state(self) -> DeterministicDagState:
        if self._state is None:
            raise WorkflowContractError("workflow_not_started", "workflow state is unavailable")
        return self._state


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowContractError("invalid_manifest", "manifest must be an object")
    manifest = dict(value)
    if manifest.get("schema_id") != EXECUTION_MANIFEST_SCHEMA_ID:
        raise WorkflowContractError("invalid_manifest", "manifest schema is not supported")
    return manifest


def _validated_dag(value: Any) -> tuple[dict[str, DagNode], tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise WorkflowContractError("invalid_dag", "normalized_dag must be an object")
    raw_nodes = value.get("nodes")
    raw_gates = value.get("gates")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)) or not raw_nodes:
        raise WorkflowContractError("invalid_dag", "normalized_dag nodes must be a non-empty array")
    if not isinstance(raw_gates, Sequence) or isinstance(raw_gates, (str, bytes)):
        raise WorkflowContractError("invalid_dag", "normalized_dag gates must be an array")

    gate_ids: set[str] = set()
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, Mapping):
            raise WorkflowContractError("invalid_dag", "gate must be an object")
        gate_id = _bounded_string(raw_gate.get("gate_id"), "gate_id")
        if gate_id in gate_ids:
            raise WorkflowContractError("duplicate_gate", gate_id)
        gate_ids.add(gate_id)

    nodes: dict[str, DagNode] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise WorkflowContractError("invalid_dag", "node must be an object")
        node_id = _bounded_string(raw_node.get("node_id"), "node_id")
        if node_id in nodes:
            raise WorkflowContractError("duplicate_node", node_id)
        nodes[node_id] = DagNode(
            node_id=node_id,
            depends_on=_string_tuple(raw_node.get("depends_on", ()), "depends_on"),
            gate_ids=_string_tuple(raw_node.get("gate_ids", ()), "gate_ids"),
            verification_rule_ids=_string_tuple(
                raw_node.get("verification_rule_ids", ()), "verification_rule_ids"
            ),
        )
    for node in nodes.values():
        unknown_dependencies = sorted(set(node.depends_on) - set(nodes))
        if unknown_dependencies:
            raise WorkflowContractError("missing_dependency", unknown_dependencies[0])
        unknown_gates = sorted(set(node.gate_ids) - gate_ids)
        if unknown_gates:
            raise WorkflowContractError("missing_gate", unknown_gates[0])
    _reject_cycles(nodes)
    return dict(sorted(nodes.items())), tuple(sorted(gate_ids))


def _reject_cycles(nodes: Mapping[str, DagNode]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            raise WorkflowContractError("dependency_cycle", node_id)
        if node_id in visited:
            return
        active.add(node_id)
        for dependency in nodes[node_id].depends_on:
            visit(dependency)
        active.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(nodes):
        visit(node_id)


def _validate_carry(
    carry: WorkflowCarryState,
    state: DeterministicDagState,
    gate_ids: tuple[str, ...],
) -> None:
    if carry.agent_run_id != state.agent_run_id or carry.manifest_hash != state.manifest_hash:
        raise WorkflowContractError("continuation_identity_mismatch", "logical run identity changed")
    if carry.deadline_at != state.deadline_at:
        raise WorkflowContractError("continuation_deadline_mismatch", "deadline changed")
    if carry.run_state not in RUN_STATES or carry.run_state in TERMINAL_RUN_STATES:
        raise WorkflowContractError("invalid_continuation_state", carry.run_state)
    if set(carry.slice_states) != set(state.nodes):
        raise WorkflowContractError("invalid_continuation_slices", "slice identity changed")
    if any(value not in SLICE_STATES for value in carry.slice_states.values()):
        raise WorkflowContractError("invalid_continuation_slices", "slice state is invalid")
    if set(carry.gate_states) != set(gate_ids):
        raise WorkflowContractError("invalid_continuation_gates", "gate identity changed")
    if any(value not in ("pending", "approved", "rejected", "expired", "waived") for value in carry.gate_states.values()):
        raise WorkflowContractError("invalid_continuation_gates", "gate state is invalid")
    for field_name, field_value in (
        ("run_version", carry.run_version),
        ("history_segment", carry.history_segment),
        ("event_cursor", carry.event_cursor),
        ("projected_event_count", carry.projected_event_count),
    ):
        if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value < 0:
            raise WorkflowContractError("invalid_continuation_counter", field_name)
    if not isinstance(carry.receipt_store_cursor, str):
        raise WorkflowContractError("invalid_continuation_cursor", "receipt cursor must be a string")


def _deadline(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise WorkflowContractError("invalid_deadline", "deadline must be a bounded ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkflowContractError("invalid_deadline", "deadline is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WorkflowContractError("invalid_deadline", "deadline must contain a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _deadline_reached(deadline_at: str) -> bool:
    return workflow.now() >= datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))


def _remaining_seconds(deadline_at: str) -> float:
    deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    return max(0.0, (deadline - workflow.now()).total_seconds())


def _parallelism(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3:
        raise WorkflowContractError("invalid_parallelism", "max_parallel_activities must be 1 through 3")
    return value


def _manifest_hash(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise WorkflowContractError("invalid_manifest_hash", "manifest hash is invalid")
    return value


def _bounded_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise WorkflowContractError("invalid_identifier", field)
    return value


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise WorkflowContractError("invalid_dag", f"{field} must be an array")
    items = tuple(_bounded_string(item, field) for item in value)
    if len(items) != len(set(items)):
        raise WorkflowContractError("invalid_dag", f"{field} contains duplicates")
    return tuple(sorted(items))
