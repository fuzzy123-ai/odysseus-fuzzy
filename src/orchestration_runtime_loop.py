"""Dry-run orchestration heartbeat runtime planner.

AUTO4 intentionally stops short of a real scheduler. The planner receives
already-collected thread snapshots, validates dispatch intent, and queues
mailbox messages without reading threads, running tests, or touching git.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from src.claim_lease_store import ClaimLeaseStore
from src.handoff_mailbox import DispatchMailbox, HandoffMailboxError, MailboxMessage, ParsedHandoff
from src.heartbeat_coordinator import HeartbeatDecision, HeartbeatDispatch, HeartbeatTick
from src.heartbeat_coordinator import HeartbeatCoordinatorState, HeartbeatMode
from src.plan_runtime import PlanRuntimeState
from src.quality_gates import QualityGateResult
from src.thread_lifecycle_bridge import ThreadDispatchDecision, ThreadDispatchRequest, ThreadLifecycleSnapshot


class OrchestrationRuntimeLoopError(ValueError):
    """Raised when a heartbeat runtime tick cannot be planned safely."""


@dataclass(frozen=True, slots=True)
class RuntimeDispatchIntent:
    request: ThreadDispatchRequest
    source_handoff: ParsedHandoff

    @classmethod
    def create(
        cls,
        *,
        request: ThreadDispatchRequest,
        source_handoff: ParsedHandoff,
    ) -> "RuntimeDispatchIntent":
        if not isinstance(request, ThreadDispatchRequest):
            raise OrchestrationRuntimeLoopError("request must be a ThreadDispatchRequest")
        if not isinstance(source_handoff, ParsedHandoff):
            raise OrchestrationRuntimeLoopError("source_handoff must be a ParsedHandoff")
        return cls(request=request, source_handoff=source_handoff)


@dataclass(frozen=True, slots=True)
class RuntimeTickPlan:
    tick: HeartbeatTick
    queued_messages: tuple[MailboxMessage, ...]
    stop_reason: str

    @property
    def can_continue(self) -> bool:
        return self.tick.decision not in {HeartbeatDecision.STOP}

    def audit_summary(self) -> dict[str, Any]:
        return {
            "tick_id": self.tick.tick_id,
            "decision": self.tick.decision.value,
            "queued_message_count": len(self.queued_messages),
            "can_continue": self.can_continue,
            "stop_reason": self.stop_reason,
            "error_count": len(self.tick.errors),
            "warning_count": len(self.tick.warnings),
        }


@dataclass(frozen=True, slots=True)
class RuntimeHeartbeatInput:
    runtime: PlanRuntimeState
    claim_store: ClaimLeaseStore
    coordinator_state: HeartbeatCoordinatorState
    snapshots: tuple[ThreadLifecycleSnapshot, ...]
    dispatch_intents: tuple[RuntimeDispatchIntent, ...]
    gate_results: tuple[QualityGateResult, ...]
    checked_at: str

    @classmethod
    def create(
        cls,
        *,
        runtime: PlanRuntimeState,
        claim_store: ClaimLeaseStore,
        coordinator_state: HeartbeatCoordinatorState,
        snapshots: Iterable[ThreadLifecycleSnapshot] = (),
        dispatch_intents: Iterable[RuntimeDispatchIntent] = (),
        gate_results: Iterable[QualityGateResult] = (),
        checked_at: str,
    ) -> "RuntimeHeartbeatInput":
        if not isinstance(runtime, PlanRuntimeState):
            raise OrchestrationRuntimeLoopError("runtime must be a PlanRuntimeState")
        if not isinstance(claim_store, ClaimLeaseStore):
            raise OrchestrationRuntimeLoopError("claim_store must be a ClaimLeaseStore")
        if not isinstance(coordinator_state, HeartbeatCoordinatorState):
            raise OrchestrationRuntimeLoopError("coordinator_state must be a HeartbeatCoordinatorState")
        normalized_snapshots = tuple(snapshots)
        normalized_intents = tuple(dispatch_intents)
        normalized_gate_results = tuple(gate_results)
        if any(not isinstance(snapshot, ThreadLifecycleSnapshot) for snapshot in normalized_snapshots):
            raise OrchestrationRuntimeLoopError("snapshots must contain ThreadLifecycleSnapshot items")
        if any(not isinstance(intent, RuntimeDispatchIntent) for intent in normalized_intents):
            raise OrchestrationRuntimeLoopError("dispatch_intents must contain RuntimeDispatchIntent items")
        if any(not isinstance(result, QualityGateResult) for result in normalized_gate_results):
            raise OrchestrationRuntimeLoopError("gate_results must contain QualityGateResult items")
        if not str(checked_at or "").strip():
            raise OrchestrationRuntimeLoopError("checked_at must not be empty")
        return cls(
            runtime=runtime,
            claim_store=claim_store,
            coordinator_state=coordinator_state,
            snapshots=normalized_snapshots,
            dispatch_intents=normalized_intents,
            gate_results=normalized_gate_results,
            checked_at=str(checked_at).strip(),
        )


@dataclass(slots=True)
class OrchestrationRuntimeLoop:
    mailbox: DispatchMailbox = field(default_factory=DispatchMailbox)

    def plan_runtime_tick(
        self,
        *,
        tick_id: str,
        runtime_input: RuntimeHeartbeatInput,
    ) -> RuntimeTickPlan:
        if not isinstance(runtime_input, RuntimeHeartbeatInput):
            raise OrchestrationRuntimeLoopError("runtime_input must be a RuntimeHeartbeatInput")

        claimable_node_id = runtime_input.runtime.next_claimable_node_id()
        evidence = [
            f"runtime_plan:{runtime_input.runtime.plan_id}",
            f"claimable_node:{claimable_node_id or 'none'}",
        ]
        warnings: list[str] = []
        errors: list[str] = []

        if not claimable_node_id:
            errors.append("no_claimable_roadmap_node")
        else:
            node = runtime_input.runtime.node_map()[claimable_node_id]
            files = node.source_refs or (runtime_input.runtime.roadmap_path,)
            blocker = runtime_input.claim_store.first_lock_blocker(files, at=runtime_input.checked_at)
            if blocker and blocker.node_id != claimable_node_id:
                errors.append(f"active_file_lock:{blocker.owner_agent_id}:{blocker.path}")
            elif blocker:
                evidence.append(f"active_claim:{blocker.owner_agent_id}:{blocker.node_id}")

        claim_summary = runtime_input.claim_store.audit_summary()
        evidence.append(f"active_claim_count:{claim_summary['active_claim_count']}")

        blocking_gate_ids = tuple(
            gate_id
            for result in runtime_input.gate_results
            for gate_id in result.blocking_gate_ids
            if not result.verified_done
        )
        if blocking_gate_ids:
            errors.append("quality_gates_blocked:" + ",".join(blocking_gate_ids))
        warning_gate_ids = tuple(
            gate_id
            for result in runtime_input.gate_results
            for gate_id in result.warning_gate_ids
        )
        if warning_gate_ids:
            warnings.append("quality_gate_warnings:" + ",".join(warning_gate_ids))

        if errors:
            return self._tick(
                tick_id=tick_id,
                state=runtime_input.coordinator_state,
                decision=HeartbeatDecision.STOP,
                evidence=evidence,
                warnings=warnings,
                errors=errors,
                stop_reason="blocked_runtime_heartbeat",
                queued_messages=[],
            )

        plan = self.plan_tick(
            tick_id=tick_id,
            state=runtime_input.coordinator_state,
            snapshots=runtime_input.snapshots,
            dispatch_intents=runtime_input.dispatch_intents,
        )
        if evidence or warnings:
            return self._tick(
                tick_id=tick_id,
                state=runtime_input.coordinator_state,
                decision=plan.tick.decision,
                evidence=(*evidence, *plan.tick.evidence),
                warnings=(*warnings, *plan.tick.warnings),
                errors=plan.tick.errors,
                stop_reason=plan.stop_reason,
                queued_messages=plan.queued_messages,
            )
        return plan

    def plan_tick(
        self,
        *,
        tick_id: str,
        state: HeartbeatCoordinatorState,
        snapshots: Iterable[ThreadLifecycleSnapshot],
        dispatch_intents: Iterable[RuntimeDispatchIntent],
    ) -> RuntimeTickPlan:
        if not isinstance(state, HeartbeatCoordinatorState):
            raise OrchestrationRuntimeLoopError("state must be a HeartbeatCoordinatorState")
        snapshot_by_thread = _snapshot_map(snapshots)
        intents = tuple(dispatch_intents)
        if any(not isinstance(intent, RuntimeDispatchIntent) for intent in intents):
            raise OrchestrationRuntimeLoopError("dispatch_intents must contain RuntimeDispatchIntent items")

        if state.mode == HeartbeatMode.MANUAL_STOP_PENDING:
            return self._tick(
                tick_id=tick_id,
                state=state,
                decision=HeartbeatDecision.STOP,
                evidence=["manual stop requested"],
                warnings=[],
                errors=[],
                stop_reason="manual_stop_pending",
                queued_messages=[],
            )

        queued: list[MailboxMessage] = []
        evidence: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []
        resolve_count = 0
        wait_count = 0

        for intent in intents:
            snapshot = snapshot_by_thread.get(intent.request.thread_ref.thread_id)
            decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=intent.request)
            if decision.action.value == "send" and decision.allowed:
                try:
                    message = MailboxMessage.create(
                        thread_ref=intent.request.thread_ref,
                        prompt_summary=intent.request.prompt_summary,
                        allowed_action=intent.request.allowed_action,
                        source_handoff=intent.source_handoff,
                    )
                    self.mailbox.queue(message)
                except HandoffMailboxError as exc:
                    errors.append(f"mailbox_error:{exc}")
                    continue
                queued.append(message)
                evidence.append(f"queued dispatch for {intent.request.expected_agent_run_id}")
            elif decision.action.value == "wait":
                wait_count += 1
                warnings.append(decision.reason)
            elif decision.action.value == "resolve":
                resolve_count += 1
                evidence.append(decision.reason)
            elif decision.action.value == "blocked":
                errors.append(decision.reason)
            else:
                warnings.append(decision.reason)

        if errors:
            return self._tick(
                tick_id=tick_id,
                state=state,
                decision=HeartbeatDecision.STOP,
                evidence=evidence,
                warnings=warnings,
                errors=errors,
                stop_reason="blocked_runtime_tick",
                queued_messages=queued,
            )
        if queued:
            return self._tick(
                tick_id=tick_id,
                state=state,
                decision=HeartbeatDecision.DISPATCH,
                evidence=evidence,
                warnings=warnings,
                errors=[],
                stop_reason="",
                queued_messages=queued,
            )
        if wait_count:
            return self._tick(
                tick_id=tick_id,
                state=state,
                decision=HeartbeatDecision.WAIT,
                evidence=["waiting for active threads"],
                warnings=warnings,
                errors=[],
                stop_reason="",
                queued_messages=[],
            )
        if resolve_count:
            return self._tick(
                tick_id=tick_id,
                state=state,
                decision=HeartbeatDecision.RESOLVE,
                evidence=evidence,
                warnings=warnings,
                errors=[],
                stop_reason="",
                queued_messages=[],
            )
        return self._tick(
            tick_id=tick_id,
            state=state,
            decision=HeartbeatDecision.READ,
            evidence=["no dispatch intents ready"],
            warnings=warnings,
            errors=[],
            stop_reason="",
            queued_messages=[],
        )

    def _tick(
        self,
        *,
        tick_id: str,
        state: HeartbeatCoordinatorState,
        decision: HeartbeatDecision,
        evidence: Iterable[str],
        warnings: Iterable[str],
        errors: Iterable[str],
        stop_reason: str,
        queued_messages: Iterable[MailboxMessage],
    ) -> RuntimeTickPlan:
        queued_tuple = tuple(queued_messages)
        dispatches = (
            tuple(
                HeartbeatDispatch.create(
                    target_thread_id=message.thread_ref.thread_id,
                    agent_run_id=message.thread_ref.agent_run_id,
                    action=message.allowed_action,
                    summary=message.prompt_summary,
                )
                for message in queued_tuple
            )
            if decision == HeartbeatDecision.DISPATCH
            else ()
        )
        tick = HeartbeatTick.create(
            tick_id=tick_id,
            heartbeat_id=state.heartbeat_id,
            decision=decision,
            dispatches=dispatches,
            evidence=evidence,
            warnings=warnings,
            errors=errors,
        )
        return RuntimeTickPlan(
            tick=tick,
            queued_messages=queued_tuple,
            stop_reason=stop_reason,
        )


def _snapshot_map(snapshots: Iterable[ThreadLifecycleSnapshot]) -> dict[str, ThreadLifecycleSnapshot]:
    mapped: dict[str, ThreadLifecycleSnapshot] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, ThreadLifecycleSnapshot):
            raise OrchestrationRuntimeLoopError("snapshots must contain ThreadLifecycleSnapshot items")
        thread_id = snapshot.thread_ref.thread_id
        if thread_id in mapped:
            raise OrchestrationRuntimeLoopError(f"duplicate snapshot for thread: {thread_id}")
        mapped[thread_id] = snapshot
    return mapped
