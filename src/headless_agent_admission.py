"""Deterministic HWA3B admission over the single HWA3A authority store.

The coordinator admits at most one request per call.  Queue age is supplied as
``wait_rounds`` by the durable caller, so priority starvation is bounded without
creating another scheduler or persistence authority in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import math
from typing import Any, Iterable

from src.agent_pool_scaling import AgentPoolMember, WorkItem
from src.headless_write_agent_state import (
    AdmissionLimits,
    AuthorityScope,
    ClaimRecord,
    HeadlessWriteAgentStateError,
    HeadlessWriteAgentStateStore,
)


class AdmissionContractError(ValueError):
    """Invalid bounded queue, policy, agent or request input."""


class AdmissionDisposition(StrEnum):
    ADMITTED = "admitted"
    WAIT = "wait"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    work_id: str
    claim_id: str
    scope: AuthorityScope
    required_role: str
    required_files: tuple[str, ...]
    hotfiles: tuple[str, ...]
    estimated_tokens: int
    priority: int
    submitted_order: int
    wait_rounds: int
    lease_seconds: int

    @classmethod
    def create(
        cls,
        *,
        work_id: Any,
        scope: AuthorityScope,
        required_role: Any,
        required_files: Iterable[Any],
        hotfiles: Iterable[Any] = (),
        estimated_tokens: Any,
        priority: Any = 50,
        submitted_order: Any,
        wait_rounds: Any = 0,
        lease_seconds: Any = 90,
    ) -> "AdmissionRequest":
        if not isinstance(scope, AuthorityScope):
            raise AdmissionContractError("scope must be an AuthorityScope")
        work = WorkItem.create(
            work_id=work_id,
            required_role=required_role,
            required_files=required_files,
            estimated_tokens=estimated_tokens,
            priority=priority,
        )
        raw_hotfiles = tuple(hotfiles)
        normalized_hotfiles: tuple[str, ...] = ()
        if raw_hotfiles:
            hot_work = WorkItem.create(
                work_id=f"{work.work_id}-hotfiles",
                required_role=work.required_role,
                required_files=raw_hotfiles,
                estimated_tokens=1,
                priority=work.priority,
            )
            normalized_hotfiles = hot_work.required_files
        if not set(normalized_hotfiles).issubset(work.required_files):
            raise AdmissionContractError("hotfiles must be included in required_files")
        order = _bounded_int(submitted_order, "submitted_order", minimum=0, maximum=2**63 - 1)
        waits = _bounded_int(wait_rounds, "wait_rounds", minimum=0, maximum=10_000)
        lease = _bounded_int(lease_seconds, "lease_seconds", minimum=1, maximum=15 * 60)
        digest = hashlib.sha256(
            f"{scope.key}\0{work.work_id}".encode("utf-8")
        ).hexdigest()[:32]
        return cls(
            work_id=work.work_id,
            claim_id=f"hwa-admission-{digest}",
            scope=scope,
            required_role=work.required_role,
            required_files=work.required_files,
            hotfiles=normalized_hotfiles,
            estimated_tokens=work.estimated_tokens,
            priority=work.priority,
            submitted_order=order,
            wait_rounds=waits,
            lease_seconds=lease,
        )


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    limits: AdmissionLimits
    max_queue_size: int
    reserve_token_floor: int
    fairness_boost_per_round: int
    max_attempts_per_call: int

    @classmethod
    def create(
        cls,
        *,
        limits: AdmissionLimits,
        max_queue_size: Any = 100,
        reserve_token_floor: Any = 0,
        fairness_boost_per_round: Any = 5,
        max_attempts_per_call: Any = 3,
    ) -> "AdmissionPolicy":
        if not isinstance(limits, AdmissionLimits):
            raise AdmissionContractError("limits must be AdmissionLimits")
        queue_size = _bounded_int(max_queue_size, "max_queue_size", minimum=1, maximum=1_000)
        reserve = _bounded_int(
            reserve_token_floor, "reserve_token_floor", minimum=0, maximum=2**63 - 1
        )
        boost = _bounded_int(
            fairness_boost_per_round,
            "fairness_boost_per_round",
            minimum=1,
            maximum=25,
        )
        attempts = _bounded_int(
            max_attempts_per_call, "max_attempts_per_call", minimum=1, maximum=3
        )
        return cls(
            limits=limits,
            max_queue_size=queue_size,
            reserve_token_floor=reserve,
            fairness_boost_per_round=boost,
            max_attempts_per_call=attempts,
        )

    @property
    def maximum_starvation_rounds(self) -> int:
        return math.ceil(100 / self.fairness_boost_per_round)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    work_id: str
    disposition: AdmissionDisposition
    agent_id: str
    reason_code: str
    next_wait_rounds: int
    fence: int | None = None


@dataclass(frozen=True, slots=True)
class AdmissionBatch:
    decisions: tuple[AdmissionDecision, ...]
    admitted_claim: ClaimRecord | None
    metrics: dict[str, int]

    @property
    def admitted_count(self) -> int:
        return int(self.admitted_claim is not None)


class HeadlessAgentAdmissionCoordinator:
    """Select one fair request, then persist assignment and claim atomically."""

    def __init__(self, store: HeadlessWriteAgentStateStore) -> None:
        if not isinstance(store, HeadlessWriteAgentStateStore):
            raise AdmissionContractError("store must be HeadlessWriteAgentStateStore")
        self._store = store

    def admit_one(
        self,
        *,
        requests: Iterable[AdmissionRequest],
        agents: Iterable[AgentPoolMember],
        policy: AdmissionPolicy,
    ) -> AdmissionBatch:
        if not isinstance(policy, AdmissionPolicy):
            raise AdmissionContractError("policy must be AdmissionPolicy")
        request_list = tuple(requests)
        agent_list = tuple(agents)
        if any(not isinstance(item, AdmissionRequest) for item in request_list):
            raise AdmissionContractError("requests must contain AdmissionRequest items")
        if any(not isinstance(item, AgentPoolMember) for item in agent_list):
            raise AdmissionContractError("agents must contain AgentPoolMember items")
        if len({item.work_id for item in request_list}) != len(request_list):
            raise AdmissionContractError("work_id must be unique in one admission call")
        if len({item.scope.key for item in request_list}) != len(request_list):
            raise AdmissionContractError("authority scope must be unique in one admission call")
        if len({item.agent_id for item in agent_list}) != len(agent_list):
            raise AdmissionContractError("agent_id must be unique")

        ranked = tuple(
            sorted(
                request_list,
                key=lambda item: (
                    -_fairness_score(item, policy),
                    item.submitted_order,
                    item.scope.owner_id,
                    item.work_id,
                ),
            )
        )
        accepted = ranked[: policy.max_queue_size]
        overflow = ranked[policy.max_queue_size :]
        decisions: dict[str, AdmissionDecision] = {
            item.work_id: AdmissionDecision(
                work_id=item.work_id,
                disposition=AdmissionDisposition.WAIT,
                agent_id="",
                reason_code="queue_backpressure",
                next_wait_rounds=_next_wait(item.wait_rounds),
            )
            for item in overflow
        }
        attempts = 0
        collision_count = 0
        quota_wait_count = 0
        admitted_claim: ClaimRecord | None = None

        for request in accepted:
            if admitted_claim is not None:
                break
            agent, reason, terminal = _select_agent(
                request,
                agent_list,
                reserve_token_floor=policy.reserve_token_floor,
            )
            if agent is None:
                decisions[request.work_id] = AdmissionDecision(
                    work_id=request.work_id,
                    disposition=(AdmissionDisposition.BLOCKED if terminal else AdmissionDisposition.WAIT),
                    agent_id="",
                    reason_code=reason,
                    next_wait_rounds=(request.wait_rounds if terminal else _next_wait(request.wait_rounds)),
                )
                continue
            if attempts >= policy.max_attempts_per_call:
                decisions[request.work_id] = _wait_decision(request, "bounded_attempts_exhausted")
                continue
            attempts += 1
            try:
                claim = self._store.acquire_admitted_claim(
                    request.scope,
                    claim_id=request.claim_id,
                    claimant_ref=agent.agent_id,
                    lease_seconds=request.lease_seconds,
                    claimed_paths=request.required_files,
                    hotfiles=request.hotfiles,
                    limits=policy.limits,
                )
            except HeadlessWriteAgentStateError as exc:
                if exc.code in {"hotfile_collision", "path_prefix_collision", "claim_conflict"}:
                    collision_count += 1
                elif exc.code == "admission_backpressure":
                    quota_wait_count += 1
                elif exc.code not in {"authority_blocked"}:
                    raise
                decisions[request.work_id] = _wait_decision(request, exc.code)
                continue
            admitted_claim = claim
            decisions[request.work_id] = AdmissionDecision(
                work_id=request.work_id,
                disposition=AdmissionDisposition.ADMITTED,
                agent_id=agent.agent_id,
                reason_code="atomic_assignment_claimed",
                next_wait_rounds=0,
                fence=claim.fence,
            )

        for request in accepted:
            if request.work_id not in decisions:
                decisions[request.work_id] = _wait_decision(request, "fair_queue_wait")

        metrics = {
            **self._store.admission_metrics(),
            "queue_size": len(request_list),
            "accepted_queue_size": len(accepted),
            "overflow_count": len(overflow),
            "attempt_count": attempts,
            "collision_count": collision_count,
            "quota_wait_count": quota_wait_count,
            "admitted_count": int(admitted_claim is not None),
            "maximum_starvation_rounds": policy.maximum_starvation_rounds,
        }
        return AdmissionBatch(
            decisions=tuple(decisions[item.work_id] for item in ranked),
            admitted_claim=admitted_claim,
            metrics=metrics,
        )


def _select_agent(
    request: AdmissionRequest,
    agents: tuple[AgentPoolMember, ...],
    *,
    reserve_token_floor: int,
) -> tuple[AgentPoolMember | None, str, bool]:
    role_agents = tuple(agent for agent in agents if agent.role_id == request.required_role)
    if not role_agents:
        return None, "no_registered_role", True
    scoped = tuple(
        agent
        for agent in role_agents
        if all(
            any(path == root or path.startswith(root.rstrip("/") + "/") for root in agent.allowed_file_roots)
            for path in request.required_files
        )
    )
    if not scoped:
        return None, "agent_path_scope_blocked", True
    available = tuple(
        agent
        for agent in scoped
        if agent.available_slots > 0
        and agent.token_budget_remaining - request.estimated_tokens >= reserve_token_floor
    )
    if not available:
        return None, "agent_capacity_or_budget_wait", False
    chosen = sorted(
        available,
        key=lambda agent: (
            agent.active_runs * 1_000 // agent.max_parallel_runs,
            -agent.token_budget_remaining,
            agent.agent_id,
        ),
    )[0]
    return chosen, "eligible", False


def _fairness_score(request: AdmissionRequest, policy: AdmissionPolicy) -> int:
    return min(100, request.priority + request.wait_rounds * policy.fairness_boost_per_round)


def _wait_decision(request: AdmissionRequest, reason: str) -> AdmissionDecision:
    return AdmissionDecision(
        work_id=request.work_id,
        disposition=AdmissionDisposition.WAIT,
        agent_id="",
        reason_code=reason,
        next_wait_rounds=_next_wait(request.wait_rounds),
    )


def _next_wait(value: int) -> int:
    return min(10_000, value + 1)


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise AdmissionContractError(f"{field} must be {minimum} through {maximum}")
    return value
